"""Project explicit file positions for the observation call, not the student turn.

Pixels reveal printed labels, not a PDF file index. This function only removes
an unambiguous single file-position anchor from the vision question. It does
not rewrite observations or decide which document/page the server should read.
"""
from __future__ import annotations

import re

from .citations import protected_spans


MAX_VISUAL_QUESTION_CHARACTERS = 2_000
VISUAL_FILE_INDEX_POLICY = (
    "本次只观察实际提供的页图像素；你不能从页脚、书页印刷号或题干数字推断它在PDF文件中的索引。"
    "若页图有印刷页码，可以逐字记录在对应区域的visible_text中，并说明它是图内印刷标记。"
    "印刷号不证明文件物理页，也不与用户指定的文件位置构成矛盾；"
    "不得据此声称图像不是指定的物理页、文件页错位或服务器选错页。"
    "‘本次原页’指当前提供的像素。问题涉及多页时，只记录当前像素中的目标及尚不能确认的目标，"
    "不要合并其它页内容或根据印刷号判断当前文件索引。"
    "实际文档ID和物理页由服务器在返回结果时附加，不由视觉模型填写、修正或证明。"
)

_NUMBER = r"\d+(?![\d.A-Za-z])"
_PDF_PREFIX = r"(?:原\s*)?PDF\s*(?:(?:中)?的\s*|中\s*)?"
_EXPLICIT_FILE_POSITION = re.compile(
    r"(?:"
    + _PDF_PREFIX + r"第\s*" + _NUMBER + r"\s*(?:物理)?页|"
    r"(?:" + _PDF_PREFIX + r")?(?:第\s*" + _NUMBER + r"\s*物理页|"
    r"物理页\s*(?:第\s*)?" + _NUMBER + r"(?:\s*页)?|"
    r"物理第\s*" + _NUMBER + r"\s*页)|"
    r"(?<![A-Za-z])(?:(?:original\s+)?PDF\s+)?physical\s+page\s+" + _NUMBER +
    r")", re.I,
)
_PAGE_SEPARATOR = r"(?:[-–—~～至到、，,和与及或/]|\band\b|\bto\b|\bthrough\b)"
_EXPLICIT_RANGE = re.compile(
    r"(?:"
    + _PDF_PREFIX + r"(?:第\s*)?\d+\s*" + _PAGE_SEPARATOR + r"\s*(?:第\s*)?\d+\s*(?:物理)?页|"
    r"第\s*\d+\s*" + _PAGE_SEPARATOR + r"\s*(?:第\s*)?\d+\s*物理页|"
    r"物理页\s*(?:第\s*)?\d+\s*" + _PAGE_SEPARATOR + r"\s*(?:第\s*)?\d+|"
    r"(?<![A-Za-z])(?:PDF\s+)?physical\s+pages?\s+\d+\s*" + _PAGE_SEPARATOR + r"\s*\d+"
    r")", re.I,
)
_MULTIPAGE_TAIL = re.compile(r"^\s*" + _PAGE_SEPARATOR + r"\s*(?:第\s*)?\d+", re.I)
_OTHER_PAGE = re.compile(r"第\s*(\d+)\s*页|\bpage\s+(\d+)", re.I)
_PRINTED_CONTEXT = re.compile(r"印刷|页脚|书页|printed|footer", re.I)
_COMPARISON = re.compile(r"比较|对比|区别|两页|跨页|\bcompare\b|\bcomparison\b|\bversus\b|\bvs\b", re.I)


class VisualObservationQuestionError(ValueError):
    """Invalid projection input; diagnostics never echo the question."""


def _literal_spans(text):
    spans = list(protected_spans(text))
    spans.extend((match.start(), match.end()) for match in re.finditer(r"(?m)^\s*>[^\n]*", text))
    for opening, closing in (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"), ("《", "》"), ('"', '"')):
        spans.extend((match.start(), match.end()) for match in re.finditer(
            re.escape(opening) + r"[^" + re.escape(closing) + r"]*(?:" + re.escape(closing) + r"|$)", text))
    # An apostrophe within an English word does not start an example quotation.
    spans.extend((match.start(), match.end()) for match in re.finditer(
        r"(?<![A-Za-z0-9])'(?:[^']|'(?=[A-Za-z0-9]))*(?:'(?![A-Za-z0-9])|$)", text))
    return spans


def _overlaps(match, spans):
    return any(match.start() < end and match.end() > start for start, end in spans)


def _printed_reference(match, text):
    before, after = text[max(0, match.start() - 16):match.start()], text[match.end():match.end() + 16]
    return bool(_PRINTED_CONTEXT.search(before)
                or re.match(r"\s*[（(]?\s*(?:印刷|页脚|书页|printed|footer)", after, re.I))


def prepare_visual_observation_question(question: str) -> str:
    """Replace one unambiguous explicit PDF physical position with 本次原页.

    Ordinary 第N页, figure/printed labels, array values, code and quotations are
    preserved. Repeated references to the same file page can be projected. A
    range, different file pages, another unresolved page target, or a comparison
    with multiple position anchors is preserved verbatim: replacing those with
    one label could collapse the requested comparison. Callers must retain the
    original student question for planning, teaching and independent review.
    """
    if (type(question) is not str or not question.strip()
            or len(question) > MAX_VISUAL_QUESTION_CHARACTERS
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", question)):
        raise VisualObservationQuestionError("视觉观察问题的类型、内容或长度无效")
    literals = _literal_spans(question)
    matches = [match for match in _EXPLICIT_FILE_POSITION.finditer(question)
               if not _overlaps(match, literals)]
    if not matches:
        return question
    if any(not _overlaps(match, literals) for match in _EXPLICIT_RANGE.finditer(question)):
        return question
    numbers = [re.search(r"\d+", match.group(0)).group(0) for match in matches]
    if any(len(number) > 10 for number in numbers):
        return question
    pages = {int(number) for number in numbers}
    if len(pages) != 1 or 0 in pages:
        return question
    if len(matches) > 1 and _COMPARISON.search(question):
        return question
    if any(_MULTIPAGE_TAIL.match(question[match.end():]) for match in matches):
        return question
    position_spans = [(match.start(), match.end()) for match in matches]
    for other in _OTHER_PAGE.finditer(question):
        if _overlaps(other, literals + position_spans) or _printed_reference(other, question):
            continue
        number = next(group for group in other.groups() if group is not None)
        if len(number) > 10 or int(number) not in pages:
            return question  # Another page target has not been disambiguated.
    result, position = [], 0
    for match in matches:
        result.extend((question[position:match.start()], "本次原页"))
        position = match.end()
    result.append(question[position:])
    return "".join(result)
