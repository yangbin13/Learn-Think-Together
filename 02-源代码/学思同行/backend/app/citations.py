"""Make physical pages, logical segments, and computed evidence unambiguous."""

import re
import unicodedata


CODE_SPANS = re.compile(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`")
REFERENCE = re.compile(r"\[\s*([0-9０-９]+)\s*\]|【\s*([0-9０-９]+)\s*】|［\s*([0-9０-９]+)\s*］")


def protected_spans(text):
    """Code and identifiable numerical containers are data, never citation markers."""
    spans = [(match.start(), match.end()) for match in CODE_SPANS.finditer(text)]
    lines = list(re.finditer(r"[^\n]*(?:\n|$)", text))
    headers = []
    for index, line in enumerate(lines):
        value = line.group(0).strip()
        if not value.startswith("|"):
            headers = []
            continue
        cells = list(re.finditer(r"(?<=\|)[^|]*(?=\|)", line.group(0)))
        next_value = lines[index + 1].group(0).strip() if index + 1 < len(lines) else ""
        if re.fullmatch(r"\|[\s:|\-]+\|", next_value) and "-" in next_value:
            headers = [cell.group(0).strip() for cell in cells]
            continue
        for column, cell in enumerate(cells):
            # A standalone [1] in a data column is a one-element numerical container.
            # Explicit Source/Citation columns still support real references.
            source_column = column < len(headers) and re.search(r"来源|依据|引用|出处|source|citation|reference", headers[column], re.I)
            if REFERENCE.fullmatch(cell.group(0).strip()) and not source_column:
                spans.append((line.start() + cell.start(), line.start() + cell.end()))
    for match in REFERENCE.finditer(text):
        before, after = text[max(0, match.start() - 50):match.start()], text[match.end():match.end() + 1]
        identifier = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", before)
        indexed_identifier = identifier and not re.fullmatch(r"[A-Z]{2,}", identifier.group(0))
        if indexed_identifier or re.search(r"=\s*$|\[\s*$|(?:数组|序列|列表|集合|索引|下标|页框|驻留|frames|array|list|set|sequence)\s*[:：]?\s*$", before, re.I) or after == "]":
            spans.append((match.start(), match.end()))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def map_prose(text, operation):
    pieces, position = [], 0
    for start, end in protected_spans(text):
        pieces.extend((operation(text[position:start]), text[start:end]))
        position = end
    pieces.append(operation(text[position:]))
    return "".join(pieces)


def canonicalize_citations(text):
    return map_prose(text, lambda prose: REFERENCE.sub(lambda match: f"[{int(unicodedata.normalize('NFKC', next(group for group in match.groups() if group is not None)))}]", prose))


def prose_only(text):
    pieces, position = [], 0
    for start, end in protected_spans(text):
        pieces.extend((text[position:start], " " * (end - start)))
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def rewrite_history_references(text, bindings=None):
    """Strip old locations; only restore markers whose exact source survived this turn."""
    bindings = bindings or {}

    def rewrite(prose):
        prose = re.sub(r"【历史引用[^】]*】", "", prose)
        prose = re.sub(r"[（(]?\s*(?:原(?:PDF|文件)?\s*)?(?:物理页|逻辑段)\s*[0-9０-９]+(?:\s*[-–—至到]\s*[0-9０-９]+)?\s*[）)]?", "", prose)
        return re.sub(r"\[(\d+)\]", lambda match: f"[{bindings[int(match.group(1))]}]" if int(match.group(1)) in bindings else "", prose)

    return map_prose(canonicalize_citations(text), rewrite)


def with_location(citation):
    result = dict(citation)
    source_type = str(result.get("source_type", "")).lower()
    if source_type == "verified_computation" or str(result.get("document_id", "")).startswith("tool:"):
        kind, label = "calculation", "确定性计算结果"
    elif source_type in {"pdf", "open_textbook", "uploaded_pdf"}:
        kind, label = "physical", f"物理页{result['page']}"
    else:
        kind, label = "logical", f"逻辑段{result['page']}"
    result.update(page_kind=kind, location_label=label)
    return result
