"""Conservative, local routing for already grounded introductory teaching.

This decision only removes the planning call. The caller must still perform
evidence binding, teaching and all existing factual/protocol reviews. Sources
must already have been authenticated by the caller; this helper grants no access.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .material_overview import requests_material_overview


_HARD_REQUIREMENT = re.compile(
    r"计算|算出|求出|换算|数值|具体数字|精确|比较次数|步数|复杂度|大\s*O|"
    r"\b(?:calculate|compute|verify|trace|complexity|implement|debug)\b|"
    r"代码|程序|编程|运行|核验|验证|检查|证明|推导|公式|轨迹|"
    r"原图|页图|图中|图示|图像|图片|看图|按图|根据图|箭头|矩阵|拓扑|物理页|图\s*\d|"
    r"学习画像|薄弱|错题|成绩|掌握|学习计划|学习顺序|个性化|我的进度|"
    r"实现|边界|保证|是否|能否|重复值|首个|最左|不变量|返回语句|"
    r"比较|区别|异同|对比|分别|所有|每个|全部|全覆盖|逐一|逐个|"
    r"\b(?:compare|difference|all|each|profile|plan)\b|"
    r"```|~~~|\d\s*(?:[+*/%=<>]|次|步|个元素)|[+*/%=<>]\s*\d",
    re.I,
)
_META_SECTION = re.compile(
    r"^(?:前言|序言|目录|版权(?:页|声明)?|参考文献|致谢|封面|"
    r"preface|contents|table\s*of\s*contents|copyright|references|acknowledg(?:e)?ments)$", re.I
)
_NARRATIVE = re.compile(
    r"是(?:一种|一个|指)|指(?:的是|一种)|称为|称之为|叫(?:做|作)|表示|记录|存储|"
    r"后进先出|先进先出|依次|需要|可以|首先|通过|遵循|"
    r"\b(?:is\s+(?:a|an)|means|stores|records|uses|follows)\b", re.I
)
_CHAPTER = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百]+)\s*章|\bchapter\s+(\d+)\b", re.I)
_OVERVIEW_BODY = re.compile(
    r"(?:本书|本教材|全书|这本书)[^。！？!?；;\n]{0,50}"
    r"(?:内容|包括|涵盖|分为|章节安排|内容安排|组成)|"
    r"\b(?:this|the)\s+(?:book|textbook)\b[^.!?\n]{0,80}"
    r"(?:covers|includes|divided|organized|chapters|contents)", re.I
)

_POLITE = r"(?:请(?:你|您)?\s*|能(?:请)?你\s*|麻烦(?:你)?\s*)?"
_SOURCE = r"(?:(?:根据|依据|结合|按照|按)\s*(?:这本书|本书|教材|课本|资料)(?:正文)?\s*[,，]?\s*)?"
_TERM = r"(?P<term>[A-Za-z][A-Za-z0-9 _-]{0,39}|[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9 _-]{0,19})"
_END = r"[。？！!?\s]*"
_BASIC_REQUESTS = [
    re.compile(_POLITE + _SOURCE + r"(?:解释|讲解|介绍|说明)(?:一下|下)?\s*" + _TERM
               + r"(?:的(?:基本)?(?:概念|定义|含义))?" + _END, re.I),
    re.compile(_POLITE + _SOURCE + _TERM + r"(?:是|指)什么(?:意思)?" + _END, re.I),
    re.compile(_POLITE + _SOURCE + r"什么是\s*" + _TERM + _END, re.I),
    re.compile(_POLITE + _SOURCE + r"用(?:一个|个)?(?:简单的?)?(?:生活(?:中的?)?(?:例子|情境)|比喻|类比)"
               + r"(?:来)?(?:帮我)?(?:解释|讲解|说明|理解)\s*" + _TERM
               + r"(?:的(?:基本)?(?:概念|定义|含义))?" + _END, re.I),
    re.compile(_POLITE + _SOURCE + r"(?:解释|讲解|说明)\s*" + _TERM
               + r"[,，]?\s*用(?:一个|个)?(?:简单的?)?(?:生活(?:中的?)?(?:例子|情境)|比喻|类比)"
               + r"(?:帮我理解|说明|解释)?" + _END, re.I),
    re.compile(r"(?:please\s+)?(?:explain|define)\s+(?:the\s+)?" + _TERM
               + r"(?:\s+(?:with|using)\s+(?:a\s+)?(?:simple\s+)?(?:everyday|real-life)\s+example)?"
               + _END, re.I),
]
_PICK = (
    _POLITE + r"(?:(?:从|在)\s*(?:这本书|本书|教材|课本|资料)(?:正文)?(?:中|里)?\s*)?"
    + r"(?:选|选择|挑|挑选)(?:取|出)?(?:一个|个)(?:具体|重点|基础|简单)?(?:的)?概念"
)
_GENERIC_REQUEST = re.compile(
    _PICK + r"[,，;；]?\s*(?:说明出处\s*[,，]\s*并)?(?:"
    r"(?:用(?:一个|个)?(?:简单的?)?(?:生活(?:中的?)?(?:例子|情境)|比喻|类比)(?:来)?)"
    r"(?:帮我)?(?:理解|解释|讲解|说明)(?:它|这个概念)?|"
    r"(?:解释|讲解|介绍|说明)(?:一下|下)?(?:它|这个概念)?(?:的(?:基本)?(?:概念|定义|含义))?|"
    r"(?:只|仅)(?:问我|给我)(?:一个|个)(?:可回答的)?(?:入门|基础|简单)(?:问题|提示)"
    r"(?:[,，;；]\s*(?:暂时)?(?:不要|别)(?:直接)?(?:讲解|解释|给出)?(?:答案|结论))?"
    r")" + _END, re.I
)
_LIFE_FIRST_STEP = re.compile(
    r"用一个生活情境引出教材中的概念[,，]\s*再只问我一个问题[;；]\s*"
    r"暂时不要解释答案" + _END
)
_NAMED_LIFE_FIRST_STEP = re.compile(
    _POLITE + r"(?:我)?(?:还)?(?:不理解|不懂)\s*" + _TERM + r"[,，;；]\s*"
    r"(?:请)?(?:先)?用(?:一个|个)?生活(?:情境|例子)(?:来)?(?:只|仅)?问我一个"
    r"(?:入门|基础)(?:的)?问题(?:[,，;；]\s*(?:暂时)?(?:不要|别)(?:先|直接)?"
    r"(?:给(?:我)?|给出|解释|讲解)(?:答案|结论))?" + _END
)


def _bodies(citations: Sequence[Mapping]) -> list[tuple[str, str]]:
    bodies = []
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        if (citation.get("source_type") == "verified_computation"
                or citation.get("evidence_kind") == "image"
                or str(citation.get("document_id", "")).startswith("tool:")):
            continue
        text = citation.get("excerpt", citation.get("text", ""))
        section = citation.get("section", "")
        if not isinstance(text, str) or not isinstance(section, str):
            continue
        text = text.strip()
        leaf = re.split(r"\s*/\s*", section)[-1].strip().lstrip("# ")
        normalized = re.sub(r"\s+", "", text.lstrip("# "))
        if len(normalized) < 12 or normalized == re.sub(r"\s+", "", leaf):
            continue
        # A multi-line list of headings is structural evidence only.
        if not _NARRATIVE.search(text) and not _CHAPTER.search(text):
            continue
        bodies.append((text, leaf))
    return bodies


def _has_overview(bodies: list[tuple[str, str]]) -> bool:
    for text, section in bodies:
        chapters = {match.group(1) or match.group(2) for match in _CHAPTER.finditer(text)}
        if len(chapters) < 2:
            continue
        if _OVERVIEW_BODY.search(text) or re.fullmatch(r"目录|contents|table\s*of\s*contents", section, re.I):
            return True
    return False


def _concept_bodies(bodies: list[tuple[str, str]]) -> list[str]:
    return [text for text, section in bodies
            if not _META_SECTION.fullmatch(section) and _NARRATIVE.search(text)]


def _named_term(question: str) -> str | None:
    for pattern in _BASIC_REQUESTS:
        match = pattern.fullmatch(question)
        if match:
            return _clean_term(match.group("term"))
    return None


def _clean_term(term: str) -> str | None:
    term = re.sub(r"的(?:基本)?(?:概念|定义|含义)$", "", term.strip()).strip()
    # Do not treat multiple objects or task instructions as a term.
    if not term or re.search(r"和|与|及|、|或者|以及|并|请|要求|同时|但|然后|不要|别|从教材|\b(?:and|or|then)\b", term, re.I):
        return None
    return term


def _supports_term(text: str, term: str) -> bool:
    escaped = re.escape(term)
    if re.search(r"[A-Za-z]", term):
        escaped = r"(?<![A-Za-z0-9])" + escaped + r"(?![A-Za-z0-9])"
    # Match a definition/operation attached to the term in the actual body.
    # Mentioning a term in metadata or in a comparison with another object
    # does not demonstrate that this excerpt explains that term.
    direct_definition = bool(re.search(
        escaped + r"\s*[）)‘’“”\"']?\s*(?:是|指|表示|采用|遵循|通过|用来|用于|的(?:特点|规则|特性|含义|定义))"
        + r"[^。！？!?；;\n]{6,120}|"
        + r"(?:称为|叫做|叫作|就是|is\s+(?:a|an)|means)\s*[‘“\"']?" + escaped
        + r"|" + escaped + r"\s+(?:is\s+(?:a|an)|means|stores|records|uses|follows)\b", text, re.I))
    if direct_definition:
        return True
    # Chinese textbooks sometimes describe the mechanism before naming it:
    # "...又执行了这个过程，我们称之为递归(recursion)". Require the
    # preceding clause to explain an operation; a bare naming phrase is not
    # enough to teach the requested concept.
    for naming in re.finditer(r"(?:我们)?称之为\s*[‘“\"']?" + escaped, text, re.I):
        preceding = re.split(r"[。！？!?；;\n]", text[:naming.start()])[-1]
        if (len(re.sub(r"\s+", "", preceding)) >= 10
                and re.search(r"过程|操作|结构|方式|函数|方法|数据|元素", preceding)
                and re.search(r"执行|调用|存储|记录|存入|放入|取出|传入|接收|读取|遍历|分解", preceding)):
            return True
    return False


def can_skip_planner(
    question: str, mode: str, citations: Sequence[Mapping], *,
    overview_requested: bool = False, qualitative_analogy: bool = False,
    single_step_scope: bool = False,
) -> bool:
    """Whether current body evidence suffices for a narrow introductory request.

    Accept citation ``excerpt`` or raw retrieved ``text``. Flags are server
    hints, not authorization to ignore the rest of the question. False keeps
    the existing planner path, including any needed retrieval or verification.
    """
    if (type(question) is not str or type(mode) is not str or mode not in {"explain", "socratic", "solve"}
            or not isinstance(citations, Sequence) or isinstance(citations, (str, bytes))
            or any(type(flag) is not bool for flag in (overview_requested, qualitative_analogy, single_step_scope))
            or (single_step_scope and mode != "socratic")):
        return False
    question = question.strip()
    if not question or _HARD_REQUIREMENT.search(question):
        return False
    bodies = _bodies(citations)
    if not bodies:
        return False
    if overview_requested:
        return requests_material_overview(question) and _has_overview(bodies)
    concept_bodies = _concept_bodies(bodies)
    if not concept_bodies:
        return False
    if _LIFE_FIRST_STEP.fullmatch(question):
        return mode == "socratic" and single_step_scope
    named_step = _NAMED_LIFE_FIRST_STEP.fullmatch(question)
    if named_step:
        term = _clean_term(named_step.group("term"))
        return bool(mode == "socratic" and single_step_scope and term
                    and any(_supports_term(text, term) for text in concept_bodies))
    if _GENERIC_REQUEST.fullmatch(question):
        asks_single_step = bool(re.search(r"(?:只|仅)(?:问我|给我)", question))
        return not asks_single_step or (mode == "socratic" and single_step_scope)
    # A named single-step exercise can require conditions absent from a general
    # definition. Restrict named shortcuts to direct basic/life explanations.
    if single_step_scope:
        return False
    term = _named_term(question)
    return bool(term and any(_supports_term(text, term) for text in concept_bodies))
