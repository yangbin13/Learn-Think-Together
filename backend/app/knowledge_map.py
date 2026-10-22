"""A textbook outline is navigation evidence, not a verified semantic graph.

The complete outline is derived from ready chunks. Only the legacy local graph
is sampled; collapsing or searching a tree must never lose an omitted heading.
The caption cache is read without scanning source PDFs or changing this model.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, deque

from .db import loads
from .figures import FIGURE_NOTICE, figure_index_status, figure_public

GRAPH_HEADING_LIMIT = 180
_PDF_SOURCES = {"uploaded_pdf", "open_textbook", "pdf"}
_CHINESE_DIGITS = {"零": 0, "〇": 0, **dict(zip("一二三四五六七八九", range(1, 10)))}


def _chinese_number(value):
    result, digit = 0, 0
    for char in value:
        if char in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS[char]
        elif char in "十百千":
            result += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
        else:
            return None
    return result + digit


def natural_key(value):
    """Sort both 第十二章 and os-12 naturally without mixed-type comparisons."""
    text = str(value or "")
    chapter = re.match(r"^第([零〇一二三四五六七八九十百千]+)([章节])", text)
    if chapter:
        number = _chinese_number(chapter.group(1))
        if number is not None:
            text = f"第{number}{chapter.group(2)}" + text[chapter.end():]
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r"(\d+)", text) if part)


def cleaned_label(value):
    original = value.strip()
    label = re.sub(r"^(?:第[零〇一二三四五六七八九十百千0-9]+[章节]\s*|\d+(?:\.\d+)*[.、]?\s*)", "", original)
    return (label or original)[:80]


def heading_id(document_id, path):
    # Preserve IDs already used by the ingestion cache and existing clients.
    return hashlib.sha256((document_id + "/" + path).encode()).hexdigest()[:18]


_NUMBERED_SECTION = re.compile(r"^(\d+(?:\.\d+)+)(?:\s|\D|$)")
_CHAPTER_NUMBER = re.compile(r"^第([零〇一二三四五六七八九十百千0-9]+)章")
_PROSE_REFERENCE = re.compile(r"节中(?:再|将|对)|为止的|如图\s*\d|见图\s*\d|主要介绍|较详细|是一个|本章")
_FRONT_MATTER = re.compile(r"^(?:封面|书名|书名页|扉页|版权页|版权信息|(?:数字|电子版?)?版权声明|出版说明|出版者的话|作译者简介|内容简介|译者序|作者序|序言|前言|致谢|cover|titlepage|copyright(?:page|notice|statement)?|preface|foreword|acknowledgements|acknowledgments)$", re.I)
_BACK_MATTER = re.compile(r"^(?:封底|阅读结束|阅读完毕|看完了|读者服务|读者反馈|联系我们|推荐图书|图书推荐|backcover|endofbook|readerfeedback|readerservices)$", re.I)


def _body_extracted(document):
    """Only repair body-extracted PDF paths, never a genuine PDF outline."""
    quality = loads(document.get("quality"), {}) or {}
    return document["source_type"] in _PDF_SOURCES and (
        quality.get("outline_entries") == 0 or "正文标题提取" in quality.get("chapter_source", ""))


def _structure_parts(document, value):
    """Keep literal slashes inside a body heading and its existing anchor ID.

    Ingestion stores a chapter followed by a numbered body heading. A slash in
    `客户 / 服务器` is text, not a third outline level. The numbered heading's
    old prefix remains its identity, while the full title is restored for display.
    Explicit outline and non-PDF paths retain their original hierarchy.
    """
    original = [part.strip() for part in (value or "").split(" / ") if part.strip()]
    if not _body_extracted(document):
        return [(part, " / ".join(original[:index + 1])) for index, part in enumerate(original)]
    parts = []
    for index, part in enumerate(original):
        if not parts or _NUMBERED_SECTION.match(part) or _CHAPTER_NUMBER.match(part):
            parts.append([part, " / ".join(original[:index + 1])])
        else:
            parts[-1][0] += " / " + part
    return [tuple(part) for part in parts]


def _valid_structure_parts(document, value):
    parts = _structure_parts(document, value)
    if not parts or not _body_extracted(document):
        return parts
    chapter = _CHAPTER_NUMBER.match(parts[0][0])
    chapter_number = None
    if chapter:
        value = chapter.group(1)
        chapter_number = int(value) if value.isdigit() else _chinese_number(value)
    for index, (label, _) in enumerate(parts):
        number = _NUMBERED_SECTION.match(label)
        if number and (chapter_number is not None and int(number.group(1).split(".")[0]) != chapter_number
                       or _PROSE_REFERENCE.search(label)):
            # Keep the actual chapter's coverage; only the spurious child is omitted.
            return parts[:index]
    return parts


def _source(document, first_page, last_page):
    unit = "物理页" if document["source_type"] in _PDF_SOURCES else "逻辑段"
    return f"自动提取：{document['title']}标题，{unit}{first_page}–{last_page}（待人工核验）"


def _toc_pages(rows):
    """Detect whole-page TOC leaders, including headings split into own chunks.

    Three distinct dotted/ellipsis leaders ending in page numbers are required.
    A prose mention of a directory or a single dotted line is insufficient.
    This is a conservative text heuristic, never a certified PDF body boundary.
    """
    by_page = {}
    for row in rows:
        key = (row["document_id"], row["page"])
        by_page.setdefault(key, set()).update(line.strip() for line in row.get("text", "").splitlines() if line.strip())
    leader = re.compile(r"(?:\.{3,}|…{2,}|(?:\. ){3,})\s*\d+\s*$")
    return {key for key, lines in by_page.items() if sum(bool(leader.search(line)) for line in lines) >= 3}


def _body_paths_on_toc_pages(rows, toc_pages):
    """Keep a genuine body introduction when it shares the final TOC page.

    An isolated title is retained only if prose for that path (or a descendant)
    is also observed on this page. That avoids promoting TOC-only title chunks.
    """
    body_paths = set()
    leader = re.compile(r"(?:\.{3,}|…{2,}|(?:\. ){3,})\s*\d+\s*$", re.M)
    for row in rows:
        key = (row["document_id"], row["page"])
        text = row.get("text", "")
        if key not in toc_pages or leader.search(text):
            continue
        chinese_prose = len(re.findall(r"[\u3400-\u9fff]", text)) >= 35 and re.search(r"[。，；！？]", text)
        english_prose = len(re.findall(r"[A-Za-z]{3,}", text)) >= 12 and re.search(r"[.!?](?:\s|$)", text)
        if not chinese_prose and not english_prose:
            continue
        parts = [part.strip() for part in (row["section"] or "").split(" / ") if part.strip()]
        for depth in range(1, len(parts) + 1):
            body_paths.add((*key, " / ".join(parts[:depth])))
    return body_paths


def _front_matter_roots(documents, rows):
    """Exclude explicit prefatory outline roots only before a real first chapter.

    Body-extracted PDFs retain their historical identities. A chapter titled
    '前言' later in the book, or an unnumbered course outline, is not inferred to
    be a cover. The source PDF and all original chunks remain untouched.
    """
    outlined = {doc["id"] for doc in documents if doc["source_type"] in _PDF_SOURCES
                and (loads(doc.get("quality"), {}) or {}).get("outline_entries", 0) > 0}
    first_chapter = {}
    roots = {}
    for row in rows:
        if row["document_id"] not in outlined:
            continue
        root = (row["section"] or "").split(" / ", 1)[0].strip()
        roots[(row["document_id"], root)] = min(roots.get((row["document_id"], root), row["page"]), row["page"])
        if _CHAPTER_NUMBER.match(root) or re.match(r"^chapter\s+(?:\d+|[ivxlcdm]+)\b", root, re.I):
            first_chapter[row["document_id"]] = min(first_chapter.get(row["document_id"], row["page"]), row["page"])
    return {(doc_id, root) for (doc_id, root), page in roots.items()
            if doc_id in first_chapter and page < first_chapter[doc_id]
            and _FRONT_MATTER.fullmatch(re.sub(r"\s+", "", root))}


def _back_matter_roots(documents, rows):
    """Hide explicit publisher/reader closing pages after the final chapter.

    Appendices, indexes, solutions, conclusions and unnamed course outlines are
    preserved. Position alone never establishes that an outline is non-content.
    """
    outlined = {doc["id"] for doc in documents if doc["source_type"] in _PDF_SOURCES
                and (loads(doc.get("quality"), {}) or {}).get("outline_entries", 0) > 0}
    roots, last_chapter = {}, {}
    for row in rows:
        if row["document_id"] not in outlined:
            continue
        root = (row["section"] or "").split(" / ", 1)[0].strip()
        key = (row["document_id"], root)
        roots[key] = min(roots.get(key, row["page"]), row["page"])
    for (doc_id, root), page in roots.items():
        if _CHAPTER_NUMBER.match(root) or re.match(r"^chapter\s+(?:\d+|[ivxlcdm]+)\b", root, re.I):
            last_chapter[doc_id] = max(last_chapter.get(doc_id, page), page)
    return {(doc_id, root) for (doc_id, root), page in roots.items()
            if doc_id in last_chapter and page > last_chapter[doc_id]
            and _BACK_MATTER.fullmatch(re.sub(r"\s+", "", root))}


def _pdf_outline_navigation(document, rows):
    """Use a cached genuine outline without scanning PDFs on a read request.

    Earlier chunks re-parsed page furniture after the outline had been applied.
    Match only a known path, a known numbered heading in the same real chapter,
    or its running-header page suffix. Existing real anchor IDs take precedence;
    aliases keep figure/excerpt/annotation reads attached after duplicates merge.
    Original chunks, vectors and annotation rows are never rewritten here.
    """
    quality = loads(document.get("quality"), {}) or {}
    cache = quality.get("pdf_outline")
    if (document["source_type"] not in _PDF_SOURCES or _body_extracted(document)
            or not isinstance(cache, list) or not cache):
        return rows, {}, 0
    compact = lambda value: re.sub(r"\s+", "", value)
    entries = {}
    for entry in cache:
        if not isinstance(entry, dict):
            continue
        page, path = entry.get("page"), entry.get("path")
        if (type(page) is not int or page < 1 or page > document.get("page_count", 0)
                or not isinstance(path, str) or not path.strip()):
            continue
        path = " / ".join(part.strip() for part in path.split(" / ") if part.strip())
        if path:
            entries.setdefault(path, {"page": page, "end": document["page_count"], "parts": path.split(" / ")})
    if not entries:
        return rows, {}, 0
    # Page intervals include the next destination page because two headings can
    # legitimately share it; they do not certify where body prose starts/ends.
    pending = []
    for path, entry in sorted(entries.items(), key=lambda item: item[1]["page"]):
        while pending and not path.startswith(pending[-1] + " / "):
            entries[pending.pop()]["end"] = entry["page"]
        pending.append(path)
    exact, roots, leaf = {}, {}, {}
    for path, entry in entries.items():
        exact.setdefault(compact(path), []).append(path)
        root = entry["parts"][0]
        roots.setdefault(compact(root), set()).add(root)
        leaf.setdefault((root, compact(entry["parts"][-1])), []).append(path)

    def in_range(path, page):
        return entries[path]["page"] <= page <= entries[path]["end"]

    def match(value, page):
        parts = [part.strip() for part in (value or "").split(" / ") if part.strip()]
        if not parts:
            return None
        paths = [path for path in exact.get(compact(" / ".join(parts)), []) if in_range(path, page)]
        if len(paths) == 1:
            return paths[0]
        candidates = roots.get(compact(parts[0]), set())
        bare = _CHAPTER_NUMBER.fullmatch(compact(parts[0]))
        if not candidates and bare:
            number = natural_key(bare.group(0))
            candidates = {entry["parts"][0] for entry in entries.values()
                          if (chapter := _CHAPTER_NUMBER.match(compact(entry["parts"][0])))
                          and natural_key(chapter.group(0)) == number}
        candidates = [root for root in candidates if root in entries and in_range(root, page)]
        if len(candidates) != 1:
            return None
        root = candidates[0]
        if len(parts) == 1 and bare:
            return root
        if not _NUMBERED_SECTION.match(parts[-1]):
            return None
        paths = [path for path in leaf.get((root, compact(parts[-1])), []) if in_range(path, page)]
        if not paths:
            # Remove a number only when the exact shorter heading is witnessed
            # in this chapter's PDF outline. Numeric course titles stay literal.
            stripped = re.sub(r"\s+\d+\s*$", "", parts[-1])
            paths = [path for path in leaf.get((root, compact(stripped)), []) if in_range(path, page)]
        return paths[0] if len(paths) == 1 else None

    observed, preferred = {}, {}
    for row in rows:
        parts = [part.strip() for part in (row["section"] or "").split(" / ") if part.strip()]
        for depth in range(1, len(parts) + 1):
            old_path = " / ".join(parts[:depth])
            path = match(old_path, row["page"])
            if path is None:
                continue
            observed.setdefault(path, set()).add(old_path)
            priority = (0 if old_path == path else 1 if compact(old_path) == compact(path)
                        else 2 if compact(parts[depth - 1]) == compact(entries[path]["parts"][-1]) else 3)
            rank = (priority, -depth, row["page"], old_path)
            if path not in preferred or rank < preferred[path][0]:
                preferred[path] = (rank, old_path)
    identities = {path: preferred[path][1] if path in preferred else path for path in entries}
    aliases = {}
    for path in entries:
        target = heading_id(document["id"], identities[path])
        for old_path in {*observed.get(path, set()), path}:
            original = heading_id(document["id"], old_path)
            if original != target:
                aliases[original] = target

    def structured(path):
        parts = entries[path]["parts"]
        return [(part, identities.get(" / ".join(parts[:depth]), " / ".join(parts[:depth])))
                for depth, part in enumerate(parts, 1)]

    result = []
    observed_roots = {entries[path]["parts"][0] for path in observed}
    corrected = 0
    for row in rows:
        path = match(row["section"], row["page"])
        if path is not None:
            observed_roots.add(entries[path]["parts"][0])
            result.append({**row, "_outline_parts": structured(path)})
            corrected += row["section"] != path
        else:
            result.append(row)
    # Restore an actual bookmarked parent/heading even when the text extractor
    # reordered it or no independent chunk was produced. Keep the same roots
    # that have indexed text; a blank promotional bookmark adds no new chapter.
    for path, entry in entries.items():
        if entry["parts"][0] in observed_roots:
            result.append({"document_id": document["id"], "page": entry["page"], "section": path,
                           "text": "", "_outline_parts": structured(path)})
    return result, aliases, corrected


def derive_textbook_tree(documents, rows):
    """Retain explicit paths and observed numbered parents, separated by PDF.

    Some PDF text extractors store `chapter / 1.2.3 heading` without a middle
    path. We attach it to 1.2 only if that parent is actually observed under the
    same chapter. Missing headings are never invented.
    """
    documents = sorted((dict(doc) for doc in documents),
                       key=lambda doc: (doc.get("created_at", ""), doc["id"]))
    rows = [dict(row) for row in rows]
    toc_pages = _toc_pages(rows)
    body_paths = _body_paths_on_toc_pages(rows, toc_pages)
    by_document = {doc["id"]: doc for doc in documents}
    prepared, aliases, corrected_paths = [], {}, 0
    for document in documents:
        normalized, document_aliases, corrected = _pdf_outline_navigation(
            document, [row for row in rows if row["document_id"] == document["id"]])
        prepared.extend(normalized)
        aliases.update(document_aliases)
        corrected_paths += corrected
    rows = prepared
    front_matter = _front_matter_roots(documents, rows)
    back_matter = _back_matter_roots(documents, rows)
    records = {}
    filtered_paths, collapsed_paths = set(), set()
    for row in rows:
        doc_id = row["document_id"]
        if doc_id not in by_document:
            continue
        raw_parts = [part.strip() for part in (row["section"] or "").split(" / ") if part.strip()]
        if not raw_parts or raw_parts[0] in {"正文", "目录"}:
            continue
        if (doc_id, raw_parts[0]) in front_matter or (doc_id, raw_parts[0]) in back_matter:
            continue
        if (doc_id, row["page"]) in toc_pages and (doc_id, row["page"], " / ".join(raw_parts)) not in body_paths:
            continue
        document = by_document[doc_id]
        structured = _structure_parts(document, row["section"])
        accepted = row.get("_outline_parts", _valid_structure_parts(document, row["section"]))
        filtered_paths.update((doc_id, path) for _, path in structured[len(accepted):])
        identities = {path for _, path in structured}
        collapsed_paths.update((doc_id, " / ".join(raw_parts[:depth])) for depth in range(1, len(raw_parts) + 1)
                               if " / ".join(raw_parts[:depth]) not in identities)
        parts = [part for part, _ in accepted]
        for depth in range(1, len(parts) + 1):
            path = " / ".join(parts[:depth])
            node_id = heading_id(doc_id, accepted[depth - 1][1])
            if node_id not in records:
                records[node_id] = {
                    "id": node_id, "parent_id": heading_id(doc_id, accepted[depth - 2][1]) if depth > 1 else "document:" + doc_id,
                    "children": [], "kind": "chapter" if depth == 1 else "section",
                    "label": parts[depth - 1], "heading": path, "depth": depth, "order": 0,
                    "document_id": doc_id, "page_start": row["page"], "page_end": row["page"],
                    "page_kind": "physical" if by_document[doc_id]["source_type"] in _PDF_SOURCES else "logical",
                    "source": "", "concept_id": None, "verified": False,
                    "location_confidence": "extracted_heading",
                    "hierarchy_source": "extracted_path", "_encounter": len(records), "_parts": parts[:depth],
                }
            elif len(parts[depth - 1]) > len(records[node_id]["label"]):
                # Some continuation chunks contain only the shortened slash prefix.
                records[node_id].update(label=parts[depth - 1], heading=path, _parts=parts[:depth])
            records[node_id]["page_start"] = min(records[node_id]["page_start"], row["page"])
            records[node_id]["page_end"] = max(records[node_id]["page_end"], row["page"])

    # Only infer the nesting of numbered sections when the real parent exists.
    numeric = {}
    for node in records.values():
        parts = node["_parts"]
        match = _NUMBERED_SECTION.match(node["label"])
        if len(parts) == 2 and match:
            numeric.setdefault((node["document_id"], parts[0], match.group(1)), []).append(node["id"])
    for node in records.values():
        parts = node["_parts"]
        match = re.match(r"^(\d+(?:\.\d+){2,})(?:\s|\D|$)", node["label"])
        if len(parts) != 2 or not match:
            continue
        parent_number = match.group(1).rsplit(".", 1)[0]
        candidates = numeric.get((node["document_id"], parts[0], parent_number), [])
        if len(candidates) == 1:
            node["parent_id"] = candidates[0]
            node["hierarchy_source"] = "observed_heading_numbers"

    document_nodes = {}
    for document in documents:
        quality = loads(document.get("quality"), {}) or {}
        document_nodes["document:" + document["id"]] = {
            "id": "document:" + document["id"], "parent_id": None, "children": [], "kind": "document",
            "label": document["title"], "heading": document["title"], "depth": 0, "order": len(document_nodes),
            "document_id": document["id"], "page_start": 1 if document.get("page_count", 0) else None,
            "page_end": document.get("page_count") or None,
            "page_kind": "physical" if document["source_type"] in _PDF_SOURCES else "logical",
            "source": quality.get("chapter_source", "教材标题提取（待人工核验）"), "concept_id": None,
            "verified": False, "hierarchy_source": "document", "_encounter": -1,
            "location_confidence": "document_metadata",
        }
    all_nodes = {**document_nodes, **records}
    for node in records.values():
        all_nodes[node["parent_id"]]["children"].append(node["id"])
    for parent in all_nodes.values():
        parent["children"].sort(key=lambda node_id: (
            all_nodes[node_id]["page_start"], natural_key(all_nodes[node_id]["label"]), all_nodes[node_id]["_encounter"]))
        for order, node_id in enumerate(parent["children"]):
            all_nodes[node_id]["order"] = order

    flattened = []

    def visit(node_id, depth):
        node = all_nodes[node_id]
        node["depth"] = depth
        flattened.append(node)
        for child in node["children"]:
            visit(child, depth + 1)
            # A parent can be an observed numbered heading omitted from the
            # explicit chunk path; include its descendants' real page range.
            if node["kind"] != "document":
                node["page_start"] = min(node["page_start"], all_nodes[child]["page_start"])
                node["page_end"] = max(node["page_end"], all_nodes[child]["page_end"])
        if node["kind"] != "document":
            node["source"] = _source(by_document[node["document_id"]], node["page_start"], node["page_end"])

    for root in document_nodes:
        visit(root, 0)
    for node in flattened:
        node.pop("_encounter")
        node.pop("_parts", None)
    warnings = []
    if records:
        warnings.append("章节层级及页码来自自动标题提取；页码表示标题及关联文字所在页，尚非人工核准的正文起页。")
    if toc_pages:
        warnings.append(f"结构导航已过滤 {len(toc_pages)} 个目录样式页中的目录标题，优先定位正文标题；目录与正文混排仍需核验原教材。")
    if filtered_paths or collapsed_paths:
        warnings.append(f"展示结构已排除 {len(filtered_paths)} 个正文引用或错章编号标题，合并 {len(collapsed_paths)} 个标题内斜杠误拆层级；原教材及分块保留。")
    if front_matter:
        warnings.append(f"知识导航已隐藏 {len(front_matter)} 个首章之前的封面、书名或前言等前置材料；原 PDF 页和文字分块仍可访问。")
    if back_matter:
        warnings.append(f"知识导航已隐藏 {len(back_matter)} 个末章之后的出版服务或阅读结束等后置材料；附录、索引和正文结论保留，原 PDF 仍可访问。")
    if corrected_paths:
        warnings.append(f"已按缓存的真实 PDF 目录校准 {corrected_paths} 个章节路径，合并页眉页号及装饰章号；原分块、向量和学习记录保留。")
    result = {"root_ids": list(document_nodes), "nodes": flattened, "total_headings": len(records), "warnings": warnings}
    if aliases:
        result["node_aliases"] = aliases
    return result


def select_graph_headings(tree, limit=GRAPH_HEADING_LIMIT):
    """Breadth-first, chapter-balanced sampling with a strict local graph cap."""
    lookup = {node["id"]: node for node in tree["nodes"]}
    roots_by_document = [deque(lookup[root]["children"]) for root in tree["root_ids"]]
    chapters = []
    while any(roots_by_document):
        for roots in roots_by_document:
            if roots:
                chapters.append(roots.popleft())
    selected = chapters[:limit]
    branches = [deque(lookup[node_id]["children"]) for node_id in selected]
    while len(selected) < limit and any(branches):
        for branch in branches:
            if branch and len(selected) < limit:
                child = branch.popleft()
                selected.append(child)
                branch.extend(lookup[child]["children"])
    selected_set = set(selected)
    return [node for node in tree["nodes"] if node["id"] in selected_set]


def extracted_graph(tree):
    lookup = {node["id"]: node for node in tree["nodes"]}
    selected = select_graph_headings(tree)
    selected_ids = {node["id"] for node in selected}
    nodes, edges = [], []
    for heading in selected:
        heading["concept_id"] = heading["id"]
        chapter = heading
        while lookup[chapter["parent_id"]]["kind"] != "document":
            chapter = lookup[chapter["parent_id"]]
        nodes.append({
            "id": heading["id"], "label": cleaned_label(heading["label"]), "chapter": chapter["label"],
            "source": heading["source"], "kind": "heading", "parent_id": heading["parent_id"],
            "document_id": heading["document_id"], "page_start": heading["page_start"],
            "page_end": heading["page_end"], "page_kind": heading["page_kind"], "verified": False,
            "location_confidence": heading["location_confidence"],
        })
        if heading["parent_id"] in selected_ids:
            edges.append({"source": heading["parent_id"], "target": heading["id"], "relation": "章节包含",
                          "kind": "structural", "source_label": "教材标题层级，待人工核验"})
    for root_id in tree["root_ids"]:
        chapters = lookup[root_id]["children"]
        for left, right in zip(chapters, chapters[1:]):
            if left in selected_ids and right in selected_ids:
                edges.append({"source": left, "target": right, "relation": "教材相邻章节", "kind": "sequence",
                              "source_label": "仅表示教材编排顺序，不表示语义先修"})
    return nodes, edges


def append_figure_nodes(conn, documents, tree, user_id):
    """Attach separate caption anchors after legacy heading sampling.

    Parent matching uses the observed section path, never the figure number.
    Unknown/filtered sections keep a document-level anchor rather than inventing
    a chapter. Existing heading IDs, source labels and ranges stay intact.
    """
    tree["total_figures"] = 0
    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_figures'").fetchone()
    if not table:
        return
    lookup = {node["id"]: node for node in tree["nodes"]}
    aliases = tree.get("node_aliases", {})
    for document in documents:
        rows = conn.execute("SELECT * FROM document_figures WHERE document_id=? AND physical_page BETWEEN 1 AND ? "
                            "AND (source_kind='auto' OR (source_kind='manual' AND user_id=?)) "
                            "ORDER BY physical_page,figure_number,id", (document["id"], document["page_count"], user_id)).fetchall()
        for row in rows:
            item = figure_public(row)
            parts = _valid_structure_parts(document, row["section_path"])
            parent_id = "document:" + document["id"]
            matched = False
            for _, path in reversed(parts):
                candidate = heading_id(document["id"], path)
                candidate = aliases.get(candidate, candidate)
                if candidate in lookup:
                    parent_id, matched = candidate, True
                    break
            parent = lookup.get(parent_id)
            if parent is None:
                continue
            node = {**item, "id": item["figure_id"], "kind": "figure", "parent_id": parent_id,
                    "children": [], "heading": parent["heading"] + " / " + item["label"],
                    "depth": parent["depth"] + 1, "order": len(parent["children"]),
                    "concept_id": None, "mastery": None, "location_confidence": "caption_text_layer",
                    "hierarchy_source": "observed_section_path" if matched else "document_only"}
            parent["children"].append(node["id"])
            tree["nodes"].append(node)
            lookup[node["id"]] = node
            tree["total_figures"] += 1
    for parent in tree["nodes"]:
        if not any(lookup[child]["kind"] == "figure" for child in parent["children"]):
            continue
        parent["children"].sort(key=lambda child: (
            lookup[child]["page_start"] or 0, lookup[child]["kind"] == "figure", natural_key(lookup[child]["label"])))
        for order, child in enumerate(parent["children"]):
            lookup[child]["order"] = order


def build_knowledge_map(conn, course, user_id):
    """Caller authorizes the course; material access is still filtered per user."""
    documents = [dict(row) for row in conn.execute(
        "SELECT id,title,source_type,page_count,created_at,quality FROM documents "
        "WHERE course_id=? AND status='ready' AND (user_id IS NULL OR user_id=?) ORDER BY created_at,id",
        (course["id"], user_id))]
    rows = conn.execute(
        "SELECT c.document_id,c.page,c.section,c.text FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE c.course_id=? AND d.status='ready' AND (d.user_id IS NULL OR d.user_id=?) "
        "AND (c.user_id IS NULL OR c.user_id=?) ORDER BY d.created_at,d.id,c.page,c.rowid",
        (course["id"], user_id, user_id)).fetchall()
    tree = derive_textbook_tree(documents, rows)
    warnings = tree.pop("warnings")
    if course["is_builtin"]:
        nodes = [dict(row) for row in conn.execute("SELECT id,label,chapter,source FROM concepts WHERE course_id=?", (course["id"],))]
        nodes.sort(key=lambda node: (natural_key(node["chapter"]), natural_key(node["id"])))
        outline_id = "outline:" + course["id"]
        source = "项目人工编写的原创导学结构，不代表上传 PDF 的章节对应或已证明的先修关系"
        outline = {"id": outline_id, "parent_id": None, "children": [node["id"] for node in nodes],
                   "kind": "document", "label": "原创导学知识结构", "heading": "原创导学知识结构", "depth": 0,
                   "order": 0, "document_id": None, "page_start": None, "page_end": None, "page_kind": None,
                   "source": source, "concept_id": None, "verified": False, "hierarchy_source": "manual_outline",
                   "location_confidence": "no_pdf_mapping"}
        concept_tree = []
        for order, node in enumerate(nodes):
            node.update(kind="concept", parent_id=outline_id, document_id=None, page_start=None, page_end=None,
                        page_kind=None, verified=False, location_confidence="no_pdf_mapping")
            concept_tree.append({**node, "label": f"{node['chapter']} · {node['label']}",
                                 "heading": f"{node['chapter']} / {node['label']}", "children": [], "depth": 1,
                                 "order": order, "concept_id": node["id"], "hierarchy_source": "manual_outline"})
        tree["root_ids"].insert(0, outline_id)
        tree["nodes"] = [outline, *concept_tree, *tree["nodes"]]
        for order, root_id in enumerate(tree["root_ids"]):
            next(node for node in tree["nodes"] if node["id"] == root_id)["order"] = order
        edges = [dict(row) for row in conn.execute(
            "SELECT source,target,relation FROM edges WHERE course_id=? ORDER BY source,target,relation", (course["id"],))]
        for edge in edges:
            edge.update(kind="sequence" if edge["relation"] in {"相邻章节", "教材相邻章节"} else "association",
                        source_label="项目人工编写的导学关联，未声明为形式化先修关系")
    else:
        nodes, edges = extracted_graph(tree)
    append_figure_nodes(conn, documents, tree, user_id)
    figure_indexes = [{"document_id": document["id"], **figure_index_status(conn, document["id"])}
                      for document in documents if document["source_type"] in _PDF_SOURCES]
    mastery = {row["concept"]: row for row in conn.execute(
        "SELECT concept,correct,attempts FROM mastery WHERE user_id=? AND course_id=?", (user_id, course["id"]))}
    heading_labels = Counter(cleaned_label(node["label"]) for node in tree["nodes"]
                             if node["kind"] in {"chapter", "section"})
    legacy_labels = {}
    aliases = tree.get("node_aliases", {})
    if aliases:
        headings = {node["id"] for node in tree["nodes"] if node["kind"] in {"chapter", "section"}}
        by_document = {document["id"]: document for document in documents}
        source_targets = {}
        for row in rows:
            document = by_document.get(row["document_id"])
            if not document:
                continue
            for label, path in _valid_structure_parts(document, row["section"]):
                original = heading_id(document["id"], path)
                target = aliases.get(original, original)
                if target in headings:
                    source_targets.setdefault(cleaned_label(label), set()).add(target)
        # This is independent of the replaceable concepts cache. Historical
        # label-keyed grades survive its refresh only when the original chunk
        # path identifies exactly one current heading in this whole course.
        for label, targets in source_targets.items():
            if len(targets) == 1:
                legacy_labels.setdefault(next(iter(targets)), set()).add(label)

    def mastery_for(label, *, heading=False, node_id=None):
        # Older quiz records are keyed by a label, not a textbook heading ID.
        # A repeated title cannot establish which chapter the student practiced.
        names = {label} if not heading or heading_labels[label] <= 1 else set()
        if heading:
            names.update(name for name in legacy_labels.get(node_id, set())
                         if name != label or heading_labels[label] <= 1)
        measured = [mastery[name] for name in names if name in mastery and mastery[name]["attempts"]]
        attempts = sum(record["attempts"] for record in measured)
        return round(sum(record["correct"] for record in measured) / attempts, 3) if attempts else None

    for node in nodes:
        node["mastery"] = mastery_for(node["label"], heading=node["kind"] == "heading", node_id=node["id"])
    seed_labels = {node["id"]: node["label"] for node in nodes if node["kind"] == "concept"}
    for node in tree["nodes"]:
        if node["kind"] in {"document", "figure"}:
            # Document roots are navigation, not a measured or aggregated topic.
            node["mastery"] = None
        else:
            label = seed_labels.get(node["id"]) if node["kind"] == "concept" else cleaned_label(node["label"])
            node["mastery"] = mastery_for(label, heading=node["kind"] in {"chapter", "section"}, node_id=node["id"])
    if any(count > 1 for count in heading_labels.values()):
        warnings.append("同名教材标题不共用练习正确率；旧练习仅记录概念名称，无法唯一对应的标题保持待测。")
    # Annotations belong to a student, not to the textbook or concept cache.
    # A readonly audit can still open a pre-migration database without changing it.
    annotation_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mindmap_annotations'").fetchone()
    if annotation_table:
        annotations = {row["node_id"]: row for row in conn.execute(
            "SELECT node_id,display_label,study_note,updated_at FROM mindmap_annotations WHERE user_id=? AND course_id=?",
            (user_id, course["id"]))}
        aliases = tree.get("node_aliases", {})
        inherited = {}
        for original, target in aliases.items():
            annotation = annotations.get(original)
            if annotation and (target not in inherited or annotation["updated_at"] > inherited[target]["updated_at"]):
                inherited[target] = annotation
        for node in tree["nodes"]:
            annotation = annotations.get(node["id"]) or inherited.get(node["id"])
            if annotation and not (node["kind"] == "document" and node["document_id"] is None):
                node.update(display_label=annotation["display_label"], study_note=annotation["study_note"],
                            annotation_updated_at=annotation["updated_at"])
                if annotation["node_id"] != node["id"]:
                    node["annotation_source_node_id"] = annotation["node_id"]
    displayed = sum(node["kind"] == "heading" for node in nodes)
    return {"nodes": nodes, "edges": edges, "tree": tree, "metadata": {
        "mode": "manual_course_outline" if course["is_builtin"] else "textbook_structure",
        "total_headings": tree["total_headings"], "displayed_headings": displayed,
        "graph_limit": GRAPH_HEADING_LIMIT, "sampled": not course["is_builtin"] and displayed < tree["total_headings"],
        "headings_count_excludes_document_roots": True,
        "total_figures": tree["total_figures"], "auto_figures": sum(node.get("origin") == "auto" for node in tree["nodes"]),
        "manual_figures": sum(node.get("origin") == "manual" for node in tree["nodes"]),
        "figure_index": figure_indexes, "figure_notice": FIGURE_NOTICE,
        "warnings": warnings,
    }}


def _short_original_excerpt(text, label, limit=320):
    """Return original prose, excluding title-only/TOC lines; never summarize."""
    lines = text.strip().splitlines()
    while lines and re.sub(r"\s+", "", lines[0]) == re.sub(r"\s+", "", label):
        lines.pop(0)
    if not lines:
        return ""
    # A leader belongs to a directory, regardless of whether the full page met
    # the conservative three-leader TOC threshold.
    if re.search(r"(?:\.{3,}|…{2,}|(?:\. ){3,})\s*\d+\s*$", "\n".join(lines), re.M):
        return ""
    original = "\n".join(lines).strip()
    if len(re.findall(r"[\u3400-\u9fff]", original)) < 12 and len(re.findall(r"[A-Za-z]{3,}", original)) < 8:
        return ""
    return original[:limit] + ("…" if len(original) > limit else "")


def build_mindmap_evidence(conn, course, user_id, node_id):
    """Permission-filtered original excerpts for one full-tree heading.

    The caller must authorize the course. This read model performs no writes,
    retrieval-provider requests or AI summary generation. A title or its page
    range is a navigation anchor, not certification of knowledge correctness.
    """
    data = build_knowledge_map(conn, course, user_id)
    lookup = {node["id"]: node for node in data["tree"]["nodes"]}
    aliases = data["tree"].get("node_aliases", {})
    selected = lookup.get(aliases.get(node_id, node_id))
    if selected is None:
        return None
    result = {"node_id": node_id, "heading": selected["heading"], "evidence": [],
              "notice": "以下是该标题范围内的教材短原文，不是 AI 要点总结；请结合原 PDF 核对完整条件、公式与图表。"}
    if selected["document_id"] is None:
        result["notice"] = "该节点属于项目原创导学结构，没有真实教材章节或页码映射；请选择具体教材标题查看依据。"
        return result
    if selected["kind"] == "document":
        result["notice"] = "教材根节点表示整本书；请选择章节或小节查看原文依据。"
        return result
    document = conn.execute(
        "SELECT id,title,source_type,quality FROM documents WHERE id=? AND course_id=? AND status='ready' "
        "AND (user_id IS NULL OR user_id=?)", (selected["document_id"], course["id"], user_id)).fetchone()
    if not document:
        return None
    document = dict(document)
    if selected["kind"] == "figure":
        row = conn.execute("SELECT * FROM document_figures WHERE id=? AND document_id=? AND physical_page=? "
                           "AND (source_kind='auto' OR (source_kind='manual' AND user_id=?))",
                           (node_id, document["id"], selected["caption_page"], user_id)).fetchone()
        if not row:
            return None
        result["figure"] = figure_public(row)
        result["notice"] = ("这是本人手动填写的图示标注；请查看书中真实原页核验图题和内容。" if result["figure"]["origin"] == "manual"
                            else "图题自动定位待核验；请查看书中真实原页。图题仅用于导航，尚未自动识别或总结图内知识。")
        return result
    descendants = set()
    pending = [selected["id"]]
    while pending:
        current = pending.pop()
        if current in descendants:
            continue
        descendants.add(current)
        pending.extend(lookup[current]["children"])
    rows = [dict(row) for row in conn.execute(
        "SELECT c.id AS chunk_id,c.document_id,c.page,c.section,c.text FROM chunks c "
        "JOIN documents d ON d.id=c.document_id WHERE c.document_id=? AND c.course_id=? "
        "AND d.course_id=? AND d.status='ready' AND (d.user_id IS NULL OR d.user_id=?) "
        "AND (c.user_id IS NULL OR c.user_id=?) AND c.page BETWEEN ? AND ? ORDER BY c.page,c.rowid",
        (document["id"], course["id"], course["id"], user_id, user_id,
         selected["page_start"], selected["page_end"]))]
    toc_pages = _toc_pages(rows)
    body_paths = _body_paths_on_toc_pages(rows, toc_pages)
    seen = []
    for row in rows:
        if (row["document_id"], row["page"]) in toc_pages and (
                row["document_id"], row["page"], row["section"]) not in body_paths:
            continue
        parts = _valid_structure_parts(document, row["section"])
        if not parts:
            continue
        # Match actual heading identities, including children whose numbered
        # parent was inferred from a separate observed body heading.
        source_id = heading_id(document["id"], parts[-1][1])
        source_id = aliases.get(source_id, source_id)
        if source_id not in descendants:
            continue
        excerpt = _short_original_excerpt(row["text"], parts[-1][0])
        if not excerpt:
            continue
        normalized = re.sub(r"\s+", "", excerpt).rstrip("…")
        if any(normalized == prior or normalized[:80] in prior for prior in seen):
            continue
        seen.append(normalized)
        result["evidence"].append({key: row[key] for key in ("document_id", "page", "section", "chunk_id")}
                                   | {"title": document["title"], "excerpt": excerpt,
                                      "page_kind": selected["page_kind"]})
        if len(result["evidence"]) == 3:
            break
    if not result["evidence"]:
        result["notice"] = "该标题尚无可明确对应的正文短片段；当前仅提供结构定位，请打开原教材核对，不自动补造要点。"
    return result
