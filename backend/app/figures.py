"""Cached, conservative PDF caption anchors; no OCR or visual interpretation."""
from __future__ import annotations

import bisect
import hashlib
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from .db import now, uid


FIGURE_INDEX_VERSION = "2026-10-04-caption-anchors-v3"
FIGURE_NOTICE = "仅定位文字层中完整的图号和图题，尚未理解图内知识；OCR 缺字、乱序或无图题的图片可能未收录，请核验原 PDF。"
_PDF_SOURCES = {"uploaded_pdf", "open_textbook", "pdf"}
_NUMBER = r"(?:[A-Za-z]\s*[.\-]\s*)?\d+(?:\s*[.\-]\s*\d+)*[A-Za-z]?"
_CAPTION = re.compile(r"^\s*(?:图|figure|fig\.?)\s*(?P<number>" + _NUMBER + r")(?![A-Za-z0-9_.\-])\s*[:：、]?\s*(?P<title>\S.*?)\s*$", re.I)
_CAPTION_MARKER = re.compile(r"(?<!\S)(?:图|figure|fig\.?)\s*" + _NUMBER + r"(?![A-Za-z0-9_.\-])", re.I)
_LEADER = re.compile(r"(?:\.{3,}|…{2,}|(?:\. ){3,})\s*\d+\s*$")
_REFERENCE_TITLE = re.compile(r"^(?:[，。；！？,;!?)）]|所示|的|中(?:[，,。.:：；;]|的|所|给|显|描|例|进|列|存|总|时)|和\s*图|与\s*图|及\s*图|图示|显示|示出|说明|描绘|给出|表示|描述了|呈现了|概述了|提供了|列出|举例说明|讲过|此时|也(?:显示|说明|表示)|尽管|可以|可见|是|为|如图|见图|and\b|shows?\b|illustrates?\b|depicts?\b|describes?\b|lists?\b|is\s|in\s+(?:figure|fig)|as\s+shown\b)", re.I)


def figure_id(document_id, physical_page, number):
    value = f"{document_id}/{physical_page}/{number.casefold()}"
    return "figure:" + hashlib.sha256(value.encode()).hexdigest()[:20]


def normalized_figure_number(raw_number):
    """Confusable first chapter digit only; keep the source number alongside it."""
    number = re.sub(r"\s+", "", raw_number)
    if re.fullmatch(r"[lIOo]-\d+(?:[.\-]\d+)*", number):
        number = {"l": "1", "I": "1", "O": "0", "o": "0"}[number[0]] + number[1:]
    return number


def _caption_title_has_boundary(title, number):
    compact = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", title)
    if not 2 <= len(title) <= 200 or _REFERENCE_TITLE.search(compact):
        return False
    # A damaged separator or detached final digit cannot establish the number.
    if re.match(r"^干\s*\d", title) or (re.match(r"^[-~～−–—]", title)
            and (not re.search(r"[.\-]", number) or re.match(r"^[-~～−–—]\s*[A-Za-z0-9]", title))):
        return False
    if len(re.split(r"[.\-]", number)[-1]) == 1 and re.match(r"^\d\s+", title):
        return False
    if re.match(r"^\([^)]{0,100}\)\s*(?:显示|图示|描述|说明|呈现)", compact):
        return False
    # OCR can concatenate another prose sentence. Descriptive multi-sentence
    # captions remain valid; only clear exposition/question continuations are
    # rejected. Never shorten the line to fabricate a more plausible title.
    if re.search(r"[?？!！]|\.\s*但|。\s*(?:我们|你|本[章节书]|在本[章节书]|假设|如|当|然后|因此|该|这些|它|[^。]{0,12}(?:报文|分组)(?:使用|必须|需要))", compact):
        return False
    if re.search(r"(?:图\s*\d[^\n]{0,12}(?:中的|所示)|(?:中|所)的例子说明)", compact[:80]):
        return False
    return True


def _separate_adjacent_captions(line):
    """Split only when every explicitly numbered segment has its own title.

    A caption mentioning an earlier figure (e.g. '图6-22 图6-15中...') stays a
    single caption: the referenced marker has no independent title boundary.
    """
    markers = list(_CAPTION_MARKER.finditer(line))
    if len(markers) < 2 or markers[0].start() != 0:
        return [line]
    segments = [line[marker.start():markers[index + 1].start() if index + 1 < len(markers) else len(line)].strip()
                for index, marker in enumerate(markers)]
    for segment in segments:
        match = _CAPTION.fullmatch(segment)
        if not match or re.search(r"(?:如|见|参见|see|shown in)\s*$", match["title"], re.I):
            return [line]
        if not _caption_title_has_boundary(match["title"].strip(), re.sub(r"\s+", "", match["number"])):
            return [line]
    return segments


def extract_figure_captions(raw_text, physical_page, section_path=""):
    """Keep explicit caption lines, never general body references or image objects.

    Caption text retains OCR wording. Missing prefixes and reordered text are
    deliberately omitted; a line-number pattern alone cannot certify a figure.
    """
    text = unicodedata.normalize("NFKC", raw_text).replace("－", "-").replace("–", "-")
    lines = [re.sub(r"[\t \u00a0]+", " ", line).strip() for line in text.splitlines()]
    if any(re.search(r"^(?:插图目录|图目录|list of figures)\s*$", line, re.I) for line in lines):
        return []
    if sum(bool(_LEADER.search(line)) for line in lines) >= 3:
        return []
    found = {}
    ambiguous = set()
    for line in [segment for original in lines for segment in _separate_adjacent_captions(original)]:
        if _LEADER.search(line):
            continue
        match = _CAPTION.fullmatch(line)
        if not match:
            continue
        title = match["title"].strip()
        raw_number = re.sub(r"\s+", "", match["number"])
        if not _caption_title_has_boundary(title, raw_number):
            continue
        number = normalized_figure_number(raw_number)
        key = number.casefold()
        value = {"physical_page": physical_page, "figure_number": number, "caption": title,
                 "caption_text": line, "section_path": section_path, "raw_figure_number": raw_number}
        if key in found and found[key]["caption"] != title:
            ambiguous.add(key)
        else:
            found[key] = value
    return [value for key, value in found.items() if key not in ambiguous]


def figure_index_status(conn, document_id):
    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='figure_index_jobs'").fetchone()
    row = conn.execute("SELECT status,index_version,current_page,total_pages,figure_count,error,updated_at "
                       "FROM figure_index_jobs WHERE document_id=?", (document_id,)).fetchone() if table else None
    return {**(dict(row) if row else {"status": "not_indexed", "index_version": None, "current_page": 0,
                                    "total_pages": 0, "figure_count": 0, "error": None, "updated_at": None}),
            "notice": FIGURE_NOTICE}


def queue_figure_index(conn, doc):
    """Idempotent retry: a current ready or active cache is left alone."""
    if doc["status"] != "ready" or doc["source_type"] not in _PDF_SOURCES:
        return figure_index_status(conn, doc["id"])
    row = conn.execute("SELECT status,index_version FROM figure_index_jobs WHERE document_id=?", (doc["id"],)).fetchone()
    if row and row["index_version"] == FIGURE_INDEX_VERSION and row["status"] in {"ready", "queued", "processing"}:
        return figure_index_status(conn, doc["id"])
    conn.execute("INSERT INTO figure_index_jobs (document_id,status,index_version,total_pages,updated_at) "
                 "VALUES (?,'queued',?,?,?) ON CONFLICT(document_id) DO UPDATE SET status='queued',"
                 "index_version=excluded.index_version,current_page=0,total_pages=excluded.total_pages,error=NULL,updated_at=excluded.updated_at",
                 (doc["id"], FIGURE_INDEX_VERSION, doc["page_count"], now()))
    return figure_index_status(conn, doc["id"])


def queue_missing_figure_indexes(db):
    """Recover interrupted caption work without changing document/embedding state."""
    with db.connection() as conn:
        conn.execute("UPDATE figure_index_jobs SET status='queued',updated_at=? WHERE status='processing'", (now(),))
        docs = conn.execute("SELECT id,status,source_type,page_count FROM documents WHERE status='ready' "
                            "AND source_type IN ('uploaded_pdf','open_textbook','pdf')").fetchall()
        for doc in docs:
            existing = conn.execute("SELECT status,index_version FROM figure_index_jobs WHERE document_id=?", (doc["id"],)).fetchone()
            if not existing or existing["index_version"] != FIGURE_INDEX_VERSION:
                queue_figure_index(conn, doc)


def store_figure_index(conn, doc, figures, page_count):
    """Replace only caption anchors atomically, preserving all teaching records."""
    current = conn.execute("SELECT id FROM documents WHERE id=? AND course_id=?", (doc["id"], doc["course_id"])).fetchone()
    if not current:
        return False
    conn.execute("DELETE FROM document_figures WHERE document_id=? AND source_kind='auto'", (doc["id"],))
    accepted = [item for item in figures if 1 <= item["physical_page"] <= page_count]
    conn.executemany("INSERT INTO document_figures (id,document_id,physical_page,figure_number,caption,caption_text,section_path,index_version,source_kind,user_id,raw_figure_number) "
                     "VALUES (?,?,?,?,?,?,?,?,'auto',NULL,?)", [
        (figure_id(doc["id"], item["physical_page"], item["figure_number"]), doc["id"], item["physical_page"],
         item["figure_number"], item["caption"], item["caption_text"], item["section_path"], FIGURE_INDEX_VERSION, item.get("raw_figure_number", item["figure_number"]))
        for item in accepted])
    conn.execute("INSERT INTO figure_index_jobs (document_id,status,index_version,current_page,total_pages,figure_count,updated_at) "
                 "VALUES (?,'ready',?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET status='ready',index_version=excluded.index_version,"
                 "current_page=excluded.current_page,total_pages=excluded.total_pages,figure_count=excluded.figure_count,error=NULL,updated_at=excluded.updated_at",
                 (doc["id"], FIGURE_INDEX_VERSION, page_count, page_count, len(accepted), now()))
    return True


def add_manual_figure(conn, doc, user_id, physical_page, number, caption):
    """A student's explicit source-page annotation, never an automatic caption."""
    # The page's actual stored section is the only hierarchy candidate. If two
    # sections occur here, do not guess which one the student meant.
    sections = {row["section"] for row in conn.execute(
        "SELECT section FROM chunks WHERE document_id=? AND page=? AND (user_id IS NULL OR user_id=?)",
        (doc["id"], physical_page, user_id))}
    section = next(iter(sections)) if len(sections) == 1 else ""
    if not section:
        # A page can contain two genuine sections. Keep their actually observed
        # common parent (usually the chapter) when it exists, otherwise root.
        paths = [value.split(" / ") for value in sections]
        common = []
        for values in zip(*paths):
            if len(set(values)) != 1:
                break
            common.append(values[0])
        section = " / ".join(common)
    number, caption = number.strip(), caption.strip()
    caption_text = f"图 {number} {caption}" if number else caption
    row = {"id": "figure:manual:" + uid(), "document_id": doc["id"], "physical_page": physical_page,
           "figure_number": number, "raw_figure_number": number, "caption": caption,
           "caption_text": caption_text, "section_path": section, "index_version": "manual-v1",
           "source_kind": "manual", "user_id": user_id}
    conn.execute("INSERT INTO document_figures (id,document_id,physical_page,figure_number,caption,caption_text,section_path,index_version,source_kind,user_id,raw_figure_number) "
                 "VALUES (:id,:document_id,:physical_page,:figure_number,:caption,:caption_text,:section_path,:index_version,:source_kind,:user_id,:raw_figure_number)", row)
    return figure_public(row)


def scan_pdf_figures(path, expected_pages, section_by_page=None, progress=None):
    """One background text scan for older ready PDFs; no providers or rendering."""
    from .ingestion import usable_outline

    reader = PdfReader(Path(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF 已加密，无法定位图题")
    count = len(reader.pages)
    if not 1 <= count <= 2000 or count != expected_pages:
        raise ValueError("PDF 实际页数与教材索引不一致，请核验原文件")
    outline = usable_outline(reader)
    outline_pages = [page for page, _ in outline]
    result = []
    for index, page in enumerate(reader.pages):
        physical = index + 1
        section = (section_by_page or {}).get(physical, "")
        position = bisect.bisect_right(outline_pages, physical) - 1
        if position >= 0:
            section = outline[position][1]
        result.extend(extract_figure_captions(page.extract_text() or "", physical, section))
        if progress and (index % 10 == 0 or physical == count):
            progress(physical, count)
    return result


def figure_public(row):
    item = dict(row)
    label = f"图 {item['figure_number']} {item['caption']}" if item["figure_number"] else "图示：" + item["caption"]
    page = item["physical_page"]
    manual = item.get("source_kind", "auto") == "manual"
    raw_number = item.get("raw_figure_number") or item["figure_number"]
    return {"figure_id": item["id"], "document_id": item["document_id"], "figure_number": item["figure_number"],
            "raw_figure_number": raw_number, "number_normalized": raw_number != item["figure_number"], "origin": "manual" if manual else "auto",
            "caption": item["caption"], "caption_text": item["caption_text"], "caption_page": page,
            "page_start": page, "page_end": page, "page_kind": "physical", "label": label,
            "verified": False, "image_mode": "full_page",
            "page_image_url": f"/api/documents/{item['document_id']}/pages/{page}/image",
            "source": "个人手动图示标注；图题由本人填写，原页与内容待核验" if manual else "图题自动定位待核验；文字层图题不代表图内知识已识别",
            "notice": "这是你填写的图示页码和图题，请结合原 PDF 核验；不代表自动识别结果或教材结论。" if manual else FIGURE_NOTICE}
