"""Compact, transparent checklist of the user's explicitly named requirements."""
from __future__ import annotations

import re


_SOURCE_PAGE = re.compile(
    r"(?:位于\s*)?原\s*PDF\s*(?:物理(?:第)?(?:页)?\s*\d+(?:\s*页)?|(?:第\s*)?\d+\s*物理页)"
    r"|(?:in\s+)?(?:the\s+)?original\s+PDF\s+physical\s+page\s+\d+", re.I)
_SOURCE_DOCUMENT = re.compile(
    r"(?:文档|document)\s*ID\s*(?:为|是|is\b|[:：=])?\s*`?(?P<identifier>[A-Za-z0-9][A-Za-z0-9_-]{7,127})`?(?![A-Za-z0-9_-])", re.I)
_PROJECT_DOCUMENT_ID = re.compile(r"(?:[0-9a-f]{32}|seed-[0-9a-f]{24})", re.I)
_BARE_PHYSICAL_PAGE = re.compile(r"(?:第\s*)?\d+\s*物理页|物理(?:页\s*(?:第\s*)?|第\s*)\d+(?:\s*页)?")
_SOURCE_BRIDGE = re.compile(
    r"[\s,，;；。:：()（）\[\]【】]*(?:(?:的|中的|of\b|in\b|['’]s\b)[\s,，;；。:：()（）\[\]【】]*)?", re.I)
_ID_DISCUSSION_BEFORE = re.compile(r"(?:解释|分析|讨论|比较|讲解|研究|验证|explain|analy[sz]e|compare|discuss|describe)\s*[,:：，]*$", re.I)
_ID_DISCUSSION_AFTER = re.compile(
    r"(?:它|其(?!中)|该(?:值|字段|标识符|编号|ID)|这(?:串|个)(?:值|字段|标识符|编号|ID)?|"
    r"\b(?:it|its|this|that)\b).{0,24}"
    r"(?:编码|生成|唯一|格式|长度|含义|字段|冲突|校验|encode|generat|unique|format|length|meaning|field|collision|checksum)"
    r"|^(?:如何|怎样|怎么|为什么|how|why)\s*(?:编码|校验|保持唯一|保证唯一|encode|validate)"
    r"|^(?:能否|能|请)?\s*解释(?:一下|下)?\s*(?:其)?含义", re.I)
_ID_DIRECT_DISCUSSION = re.compile(
    r"^\s*(?:的\s*)?(?:编码|生成|唯一|格式|长度|含义|字段|冲突|校验|如何|怎样|怎么|为什么|是什么|how\b|why\b|what\b)", re.I)
_SOURCE_PAGE_CONVENTION = re.compile(
    r"页码\s*(?:均|都)?\s*指(?:的是|的?是)?\s*(?:原\s*)?PDF\s*物理页(?:码)?"
    r"(?=\s*(?:[，,。;；\n)）]|$))", re.I)


def _page_convention_locator(question, declaration):
    """A delimited page-number declaration is metadata, not a PDF lesson.

    Keep explicit discussion and a following anaphoric question. Other PDF
    occurrences elsewhere are untouched and still require coverage.
    """
    before = question[:declaration.start()].rstrip()
    if before and before[-1] not in ",，;；。:：\n([（【":
        return False
    if _ID_DISCUSSION_BEFORE.search(before):
        return False
    following = question[declaration.end():].lstrip(" \t\r\n,，;；。:：)]）】")
    first_clause = re.split(r"[。;；\n]", following, maxsplit=1)[0][:140]
    refers_back = re.match(
        r"(?:(?:请)?\s*(?:解释|说明|比较|讨论|分析|讲解)\s*)?"
        r"(?:它|其|这|物理页|印刷页|逻辑页|如何|怎样|怎么|为什么|为何|什么|有何|有什么|是否|能否)", first_clause)
    asks_about_it = re.search(r"[?？]|区别|含义|意义|术语|差异|格式|编码|对象|结构|计数|起算|定位|不同", first_clause)
    return not (refers_back and asks_about_it)


def _standalone_document_locator(question, document):
    """Recognize a project ID field, never globally exempt the term ID.

    Chat follow-ups often keep their source declaration while omitting a page.
    Require the app's actual identifier shape and a delimited metadata field;
    retain genuine identifier questions, including anaphora across sentences.
    """
    if not _PROJECT_DOCUMENT_ID.fullmatch(document.group("identifier")):
        return False
    before, after = question[:document.start()].rstrip(), question[document.end():]
    starts_field = (not before or before[-1] in ",，;；。:：\n([（【"
                    or re.search(r"(?:教材|资料|来源|source|material)\s*$", before, re.I))
    ends_field = not after.strip() or after.lstrip()[0] in ",，;；。\n)]）】"
    if not starts_field or not ends_field or _ID_DISCUSSION_BEFORE.search(before):
        return False
    following = after.lstrip(" \t\r\n,，;；。:：)]）】")
    first_clause = re.split(r"[?？!！。;；\n]", following, maxsplit=1)[0][:140]
    return not _ID_DISCUSSION_AFTER.search(first_clause)


def _scope_content(question: str) -> str:
    """Omit source locators from coverage, retaining the original message for tools."""
    pages = list(_SOURCE_PAGE.finditer(question))
    spans = [page.span() for page in pages]
    spans.extend(declaration.span() for declaration in _SOURCE_PAGE_CONVENTION.finditer(question)
                 if _page_convention_locator(question, declaration))
    for document in _SOURCE_DOCUMENT.finditer(question):
        project_id = bool(_PROJECT_DOCUMENT_ID.fullmatch(document.group("identifier")))
        candidates = pages + (list(_BARE_PHYSICAL_PAGE.finditer(question)) if project_id else [])
        for page in candidates:
            left, right = sorted((document, page), key=lambda match: match.start())
            bridge = question[left.end():right.start()]
            if len(bridge) <= 80 and _SOURCE_BRIDGE.fullmatch(bridge):
                following = question[right.end():].lstrip(" \t\r\n,，;；。:：)]）】")
                first_clause = re.split(r"[?？!！。;；\n]", following, maxsplit=1)[0][:140]
                if (_ID_DISCUSSION_BEFORE.search(question[:document.start()].rstrip())
                        or _ID_DISCUSSION_AFTER.search(first_clause)):
                    break
                if page.end() <= document.start():
                    if _ID_DIRECT_DISCUSSION.match(question[document.end():]):
                        break
                spans.append(document.span())
                spans.append(page.span())
                break
        else:
            if _standalone_document_locator(question, document):
                spans.append(document.span())
    if not spans:
        return question
    pieces, position = [], 0
    for start, end in sorted(set(spans)):
        if start < position:
            position = max(position, end)
            continue
        pieces.extend((question[position:start], " "))
        position = end
    pieces.append(question[position:])
    return "".join(pieces)


def question_scope(question: str) -> dict[str, list[str]]:
    """Expose question parts and named acronyms to the reviewer; this is not gold data."""
    content = _scope_content(question or "")
    raw_parts = re.split(r"[?？!！。；;\n]+", content)
    connectors = re.compile(r"\s*(?:并分别|以及|同时|另外|此外|还要|并且|而且|分别|其次|再说明|再解释|再比较|再判断|并|还需|还要|且|与|和)\s*")
    parts: list[str] = []
    for part in raw_parts:
        part = re.sub(r"^\s*\d+[.、)）]\s*", "", part).strip(" ，,、")
        for fragment in connectors.split(part):
            clean = fragment.strip(" ，,、:：")
            if len(clean) >= 3 and clean not in parts:
                parts.append(clean[:220])

    acronyms = []
    for match in re.finditer(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*(?![A-Za-z0-9])", content):
        term = match.group(0)
        if len(re.sub(r"[^A-Z0-9]", "", term)) >= 2 and term.casefold() not in {value.casefold() for value in acronyms}:
            acronyms.append(term)

    return {"required_subquestions": parts[:12], "named_terms": acronyms[:20]}


def missing_explicit_terms(question: str, answer: str) -> list[str]:
    terms = question_scope(question)["named_terms"]
    prose = re.sub(r"`[^`]*`|```[\s\S]*?```", " ", answer or "")
    folded = prose.casefold()
    return [term for term in terms if not re.search(rf"(?<![A-Za-z0-9]){re.escape(term.casefold())}(?![A-Za-z0-9])", folded)]


def requests_textbook_source(question: str) -> bool:
    return bool(re.search(r"教材依据|教材原文|教材出处|教材引用|引用教材|请给.{0,8}教材|(?:依据|根據|根据)本书|本书.{0,12}(?:出处|依据)|cite.{0,10}(?:textbook|book)|textbook.{0,10}(?:source|citation)", question or "", re.I))
