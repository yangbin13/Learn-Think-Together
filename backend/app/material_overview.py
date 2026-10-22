"""Locate real whole-material structure without replacing retrieval or review."""
from __future__ import annotations

import re

from .retrieval import is_heading_only_chunk


_BOOK = r"(?:教材|本书|全书|整本书|这本书|整部书|整个教材)"
_CONTENT = r"(?:核心知识|知识框架|主要内容|学习模块|主要知识|知识体系|整体结构)"
_SUMMARY = re.compile(r"梳理|概括|总结|整理|归纳|介绍|哪些|包含什么|有什么|有哪些")
_SCOPED = re.compile(
    r"第\s*[0-9０-９一二三四五六七八九十百]+\s*[章节]|"
    r"某[章节]|(?:仅|只|单独|限于|限定)[^。！？!?；;\n]{0,12}(?:章节|主题|概念|部分)|"
    r"(?:关于|有关|针对|围绕)[^。！？!?；;\n]{0,24}(?:主题|概念|算法|结构|方法|知识)"
)
_SINGLE_STEP = re.compile(
    r"(?:只|仅)(?:问我|问|给我|给|提供|提示|引导)[^。！？!?；;\n]{0,12}(?:一个|一步|第一步)|"
    r"(?:先|暂时)[^。！？!?；;\n]{0,6}(?:提示|引导|问我)|"
    r"(?:一步步|逐步|一步一步)[^。！？!?；;\n]{0,12}(?:引导|提问|问)|"
    r"(?:不要|别|暂不)[^。！？!?；;\n]{0,8}(?:讲解|解释|答案|结论)"
)
_CHAPTER = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百]+)\s*章|\bchapter\s+(\d+)\b", re.I)
_OVERVIEW_TEXT = re.compile(
    r"(?:本书|本教材)内容\s*(?:[:：]|如下|包括|分为|安排|本书|本教材|主要|涵盖)|"
    r"(?:本书|本教材|全书|这本书)[^。！？!?；;\n]{0,50}"
    r"(?:主旨|具体内容|主要内容|包括|涵盖|分为|由[^。！？!?；;\n]{0,12}章组成|章节安排|内容安排|结构如下)|"
    r"\b(?:this|the)\s+(?:book|textbook)\b[^.!?\n]{0,80}"
    r"(?:covers|includes|divided|organized|chapters|contents)", re.I
)


def requests_material_overview(question):
    """Recognize an explicit whole-book summary, not a topic or single hint."""
    if type(question) is not str:
        return False
    # Quoted examples and fenced instructions are not the current request.
    text = re.sub(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|"
                  r"“[^”]*”|‘[^’]*’|\"[^\"]*\"|`[^`\n]*`", " ", question)
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    if not _SUMMARY.search(text) or _SCOPED.search(text) or _SINGLE_STEP.search(text):
        return False
    if re.search(r"(?:不要|不必|无需|别)[^。！？!?；;\n]{0,12}(?:梳理|概括|总结|整理|归纳)", text):
        return False
    # Binding the content directly to the book avoids treating "according to
    # the textbook, summarize stack/queue knowledge" as a whole-book request.
    if re.search(_BOOK + r"\s*(?:的|中(?:的)?|里(?:的)?|有哪些|包含哪些|有什么)?\s*" + _CONTENT, text):
        return True
    if re.search(r"(?:梳理|概括|总结|整理|归纳|介绍)[^。！？!?；;\n]{0,24}(?:全书|整本书|整个教材)", text):
        return True
    return bool(re.search(r"教材目录|本书目录|全书目录", text)
                and re.search(r"(?:[一二三四五六七八九十0-9]+个)?主要学习模块|学习模块|整体结构", text))


def _candidate(row):
    section = re.sub(r"\s+", "", row["section"]).lower()
    leaves = section.split("/")
    contents = any(leaf in {"目录", "contents", "tableofcontents"} for leaf in leaves)
    preface = any(leaf in {"前言", "序言", "preface"} for leaf in leaves)
    chapters = {match.group(1) or match.group(2) for match in _CHAPTER.finditer(row["text"])}
    overview = bool(_OVERVIEW_TEXT.search(row["text"]))
    if is_heading_only_chunk(row) or not row["text"].strip():
        return None
    if not (contents or overview or (preface and chapters)):
        return None
    # An actual multi-chapter narrative is more useful than a partial contents
    # page. Text is never rewritten, and these labels are ranking only.
    rank = (int(overview and len(chapters) >= 2), len(chapters), int(overview), int(contents))
    return row, chapters, rank


def material_overview_sources(db, course_id, user_id):
    """Return at most six authorized, ready, real overview chunks for pinning."""
    rows = db.all(
        "SELECT c.id AS chunk_id,c.document_id,c.page,c.section,c.text,d.title,d.source_type "
        "FROM chunks c JOIN documents d ON d.id=c.document_id AND d.course_id=c.course_id "
        "JOIN courses co ON co.id=c.course_id "
        "WHERE c.course_id=? AND (c.user_id IS NULL OR c.user_id=?) "
        "AND (d.user_id IS NULL OR d.user_id=?) AND (co.is_builtin=1 OR co.user_id=?) "
        "AND d.status='ready' ORDER BY d.created_at,d.id,c.page,c.rowid",
        (course_id, user_id, user_id, user_id),
    )
    candidates = [candidate for row in rows if (candidate := _candidate(row))]
    selected, covered = [], set()
    while candidates and len(selected) < 6:
        # Break equal rank by fresh chapter coverage, then stable database order.
        index = max(range(len(candidates)), key=lambda i: (
            candidates[i][2][0],
            len({(candidates[i][0]["document_id"], chapter) for chapter in candidates[i][1]} - covered),
            candidates[i][2], -i,
        ))
        row, chapters, _ = candidates.pop(index)
        selected.append(row)
        covered.update((row["document_id"], chapter) for chapter in chapters)
    return selected
