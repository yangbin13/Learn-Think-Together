"""Bounded original textbook context for topic-based quiz generation.

Neighboring chunks complete local page context, not semantic source support.
Question generation, strict concept binding and independent review still decide
whether that evidence is adequate for a particular question.
"""
from __future__ import annotations

import re

from .db import loads
from .knowledge_map import (
    _BACK_MATTER,
    _FRONT_MATTER,
    _PDF_SOURCES,
    _body_paths_on_toc_pages,
    _toc_pages,
    derive_textbook_tree,
    heading_id,
)


MAX_QUIZ_EVIDENCE_CHUNKS = 16
MAX_QUIZ_EVIDENCE_CHARACTERS = 16_000
_NUMBERED_CHAPTER = re.compile(
    r"^(?:第[零〇一二三四五六七八九十百千0-9]+章|chapter\s+(?:\d+|[ivxlcdm]+)\b)", re.I,
)


def _chapter_id(tree, index, chunk):
    aliases = tree.get("node_aliases", {})
    parts = [part.strip() for part in chunk["section"].split(" / ") if part.strip()]
    node = None
    for depth in range(len(parts), 0, -1):
        original = heading_id(chunk["document_id"], " / ".join(parts[:depth]))
        candidate = index.get(aliases.get(original, original))
        if candidate and candidate["kind"] in {"chapter", "section"}:
            node = candidate
            break
    chapters, seen = [], set()
    while node and node["kind"] in {"chapter", "section"}:
        if node["id"] in seen:
            return None
        seen.add(node["id"])
        if node["kind"] == "chapter":
            chapters.append(node["id"])
        node = index.get(node["parent_id"])
    return chapters[0] if len(chapters) == 1 else None


def prepare_quiz_evidence(db, course_id, user_id, all_chunks, seeds):
    """Keep ranked authoritative seeds and at most two local neighbors each.

    All returned objects are unchanged rows from ``all_chunks``; a retriever's
    text, page, section or title never overrides them. Budgets count complete
    text characters, not tokens: over-budget blocks are skipped, never sliced.
    Unstructured documents retain seeds but do not authorize neighbor inference.
    """
    documents = db.all(
        "SELECT id,title,source_type,page_count,created_at,quality FROM documents "
        "WHERE course_id=? AND user_id=? AND status='ready' ORDER BY created_at,id",
        (course_id, user_id),
    )
    document_by_id = {document["id"]: document for document in documents}
    chunks_by_id, duplicate_ids = {}, set()
    original_order = {}
    for position, chunk in enumerate(all_chunks):
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("id")
        if (not isinstance(chunk_id, str) or not chunk_id
                or chunk.get("document_id") not in document_by_id
                or ("course_id" in chunk and chunk["course_id"] != course_id)
                or ("user_id" in chunk and chunk["user_id"] != user_id)
                or type(chunk.get("page")) is not int or chunk["page"] < 1
                or not isinstance(chunk.get("section"), str)
                or not isinstance(chunk.get("text"), str) or not chunk["text"].strip()):
            continue
        if chunk_id in chunks_by_id:
            duplicate_ids.add(chunk_id)
        else:
            chunks_by_id[chunk_id] = chunk
            original_order[chunk_id] = position
    for chunk_id in duplicate_ids:
        chunks_by_id.pop(chunk_id)
    rows = list(chunks_by_id.values())
    if not rows:
        return []

    tree = derive_textbook_tree(documents, rows)
    index = {node["id"]: node for node in tree["nodes"]}
    aliases = tree.get("node_aliases", {})
    heading_ids = {node["id"] for node in tree["nodes"] if node["kind"] in {"chapter", "section"}}
    structured = {node["document_id"] for node in tree["nodes"] if node["kind"] in {"chapter", "section"}}
    outlined_chapters = {}
    for document in documents:
        quality = loads(document.get("quality"), {}) or {}
        outline = quality.get("pdf_outline", [])
        if isinstance(outline, list) and outline:
            structured.add(document["id"])
        for entry in outline if isinstance(outline, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            root = entry["path"].split(" / ", 1)[0].strip()
            if _NUMBERED_CHAPTER.match(root):
                original = heading_id(document["id"], root)
                outlined_chapters.setdefault(document["id"], set()).add(aliases.get(original, original))
    toc_pages = _toc_pages(rows)
    body_paths = _body_paths_on_toc_pages(rows, toc_pages)
    eligible, chapters = {}, {}
    for chunk in rows:
        document_id = chunk["document_id"]
        parts = [part.strip() for part in chunk["section"].split(" / ") if part.strip()]
        root = parts[0] if parts else ""
        compact_root = re.sub(r"\s+", "", root)
        # These are explicit publication/navigation labels, not a last-page
        # heuristic. Appendices, indexes and ordinary final chapters remain.
        if root == "目录" or _FRONT_MATTER.fullmatch(compact_root) or _BACK_MATTER.fullmatch(compact_root):
            continue
        if ((document_id, chunk["page"]) in toc_pages
                and (document_id, chunk["page"], " / ".join(parts)) not in body_paths):
            continue
        original = heading_id(document_id, chunk["section"])
        if document_id in structured and aliases.get(original, original) not in heading_ids:
            continue
        chapter = _chapter_id(tree, index, chunk)
        if document_id in outlined_chapters and chapter not in outlined_chapters[document_id]:
            continue
        eligible[chunk["id"]] = chunk
        chapters[chunk["id"]] = chapter

    accepted, accepted_ids, characters = [], set(), 0

    def keep(chunk):
        nonlocal characters
        if chunk["id"] in accepted_ids:
            return False
        size = len(chunk["text"])
        if (len(accepted) >= MAX_QUIZ_EVIDENCE_CHUNKS
                or characters + size > MAX_QUIZ_EVIDENCE_CHARACTERS):
            return False
        accepted.append(chunk)
        accepted_ids.add(chunk["id"])
        characters += size
        return True

    kept_seeds = []
    for seed in seeds:
        source_id = seed if isinstance(seed, str) else seed.get("id", seed.get("chunk_id")) if isinstance(seed, dict) else None
        if not isinstance(source_id, str):
            continue
        chunk = eligible.get(source_id)
        if chunk and keep(chunk):
            kept_seeds.append(chunk)
    # Spend any remaining budget on immediate neighbors, preserving all seeds
    # that fit first. Never recursively expand a neighbor's neighborhood.
    groups = {}
    for chunk in eligible.values():
        document = document_by_id[chunk["document_id"]]
        chapter = chapters[chunk["id"]]
        if document["source_type"] in _PDF_SOURCES and chapter is not None:
            groups.setdefault((chunk["document_id"], chapter), []).append(chunk)
    positions = {}
    for key, group in groups.items():
        group.sort(key=lambda chunk: (chunk["page"], original_order[chunk["id"]]))
        for position, chunk in enumerate(group):
            positions[chunk["id"]] = (key, position)
    for seed in kept_seeds:
        location = positions.get(seed["id"])
        if location is None:
            continue
        key, position = location
        group = groups[key]
        for neighbor_position in (position - 1, position + 1):
            if 0 <= neighbor_position < len(group):
                neighbor = group[neighbor_position]
                if abs(neighbor["page"] - seed["page"]) <= 1:
                    keep(neighbor)
    return accepted
