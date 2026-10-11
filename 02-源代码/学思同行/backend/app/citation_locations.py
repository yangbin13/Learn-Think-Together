"""Validate explicit source locations within their actual citation scope.

Location checks neither bind new sources nor approve factual/semantic support.
Grounded blocks retain their own source IDs; legacy prose uses only nearby
markers in the same paragraph and keeps the existing 48-character guard.
"""
from __future__ import annotations

import re

from .citations import canonicalize_citations, prose_only
from .grounded_reply import MAX_BLOCKS, MAX_BLOCK_CHARACTERS, MAX_BLOCK_SOURCES, MAX_TEXT_CHARACTERS
from .provider import ProviderError


_LOCATIONS = re.compile(r"(物理页|逻辑段)\s*(\d+)(?:\s*[-–—至到]\s*(\d+))?")
_MARKERS = re.compile(r"\[(\d+)\]")
_PDF_SOURCES = {"pdf", "open_textbook", "uploaded_pdf"}
_MISMATCH = "回答所述出处位置与实际引用不一致，已阻止保存，请重试"
_INVALID = "出处位置校验的数据格式无效，已阻止保存，请重试"


def _invalid():
    raise ProviderError(_INVALID)


def _mismatch():
    # Never include page excerpts, provider output, titles, or private IDs.
    raise ProviderError(_MISMATCH)


def _integer(value):
    if type(value) is int:
        return value
    if type(value) is str and len(value) <= 10 and re.fullmatch(r"\d+", value):
        return int(value)
    _invalid()


def _sources_by_id(citations):
    if type(citations) is not list:
        _invalid()
    result = {}
    for source in citations:
        if type(source) is not dict or "id" not in source:
            _invalid()
        source_id = _integer(source["id"])
        if source_id <= 0 or source_id in result:
            _invalid()
        result[source_id] = source
    return result


def _source_location(source):
    """Follow the established physical/logical source classifier; skip tools."""
    source_type = source.get("source_type")
    document_id = source.get("document_id")
    if source_type == "verified_computation" or (type(document_id) is str and document_id.startswith("tool:")):
        return None
    if source_type is not None and type(source_type) is not str:
        _mismatch()
    kind = source.get("page_kind", "physical" if source_type in _PDF_SOURCES else "logical")
    if type(kind) is not str or kind not in {"physical", "logical"} or "page" not in source:
        _mismatch()
    try:
        page = _integer(source["page"])
    except ProviderError:
        _mismatch()
    if page <= 0:
        _mismatch()
    return kind, page


def _claim(location):
    try:
        start = _integer(location.group(2))
        end = _integer(location.group(3)) if location.group(3) else start
    except ProviderError:
        _mismatch()
    if not 1 <= start <= end:
        _mismatch()
    return ("physical" if location.group(1) == "物理页" else "logical"), start, end


def _visible_text(text):
    canonical = canonicalize_citations(text)
    visible = prose_only(canonical)
    # prose_only masks code with spaces. Preserve its original line boundaries
    # so a fenced code block cannot accidentally merge separate prose paragraphs.
    return "".join(original if original in "\r\n" else masked
                   for original, masked in zip(canonical, visible))


def _check_blocks(blocks, sources):
    if type(blocks) is not list or not 1 <= len(blocks) <= MAX_BLOCKS:
        _invalid()
    total = 0
    for block in blocks:
        if type(block) is not dict or set(block) != {"text", "source_ids"}:
            _invalid()
        text, source_ids = block["text"], block["source_ids"]
        if type(text) is not str or not text.strip() or len(text) > MAX_BLOCK_CHARACTERS:
            _invalid()
        if (type(source_ids) is not list or len(source_ids) > MAX_BLOCK_SOURCES
                or any(type(source_id) is not int or source_id <= 0 for source_id in source_ids)
                or len(set(source_ids)) != len(source_ids)
                or any(source_id not in sources for source_id in source_ids)):
            _invalid()
        total += len(text)
        if total > MAX_TEXT_CHARACTERS:
            _invalid()
        locations = list(_LOCATIONS.finditer(_visible_text(text)))
        if not locations:
            continue
        actual = {_source_location(sources[source_id]) for source_id in source_ids}
        actual.discard(None)
        for location in locations:
            kind, start, end = _claim(location)
            pages = {page for source_kind, page in actual if source_kind == kind}
            # Bound range work by the actual source set, not the numeric span.
            if end - start + 1 > len(pages) or any(page not in pages for page in range(start, end + 1)):
                _mismatch()


def _check_legacy(answer, sources):
    for paragraph in re.split(r"\r?\n[ \t]*\r?\n", _visible_text(answer)):
        markers = list(_MARKERS.finditer(paragraph))
        if not markers:
            continue
        for location in _LOCATIONS.finditer(paragraph):
            distance, marker = min(
                ((min(abs(item.end() - location.start()), abs(location.end() - item.start())), item)
                 for item in markers),
                key=lambda pair: pair[0],
            )
            if distance > 48:
                continue
            source_id = _integer(marker.group(1))
            if source_id not in sources:
                _mismatch()
            actual = _source_location(sources[source_id])
            if actual is None:
                _mismatch()  # Tool page zero cannot validate a source location.
            kind, start, end = _claim(location)
            if actual != (kind, start) or end != start:
                _mismatch()


def validate_citation_locations(answer, citations, *, grounded_blocks=None):
    """Return None when locations match scope, otherwise raise a safe ProviderError.

    Grounded callers pass the blocks that produced ``answer``. Every explicit
    physical/logical page, including all pages in a range, needs a matching
    non-tool source selected by that same block. No source from another block
    or the unselected evidence pool can repair the binding. This does not alter
    the answer, assign sources, or establish semantic support for its claims.
    """
    if type(answer) is not str or len(answer) > 65_536:
        _invalid()
    sources = _sources_by_id(citations)
    if grounded_blocks is not None:
        _check_blocks(grounded_blocks, sources)
    else:
        _check_legacy(answer, sources)
