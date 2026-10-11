"""Render model-selected evidence IDs; this protocol never approves facts."""
from __future__ import annotations

import json
import re

from .citations import REFERENCE, protected_spans, prose_only
from .provider import ProviderError


MAX_REPLY_CHARACTERS = 65_536
MAX_BLOCKS = 32
MAX_BLOCK_CHARACTERS = 8_192
MAX_TEXT_CHARACTERS = 32_768
MAX_BLOCK_SOURCES = 32
GROUNDED_REPLY_PROMPT = (
    '本轮采用证据段落协议，只输出完整JSON对象：{"blocks":[{"text":"段落Markdown","source_ids":[1]}]}。'
    "顶层只能有blocks，每个段落只能有text和source_ids；不输出JSON围栏或外部文字。"
    "最多32段，每段非空Markdown最多8192字，总正文最多32768字；保留必要表格、公式和代码。"
    "段落text只使用可显示的Unicode文本；不要包含C0控制字符或孤立UTF-16代理项，也不要把它们写成\\u转义。普通换行按JSON规范转义。"
    "每段由你亲自选择真正支持该段事实的本轮真实证据整数ID，不得猜ID；同段ID不重复，最多32个。"
    "text中不要自行写自然语言引用[n]、书名页码代替引用；服务器只把你选的source_ids渲染成[n]。"
    "本轮没有来源行号协议，禁止自行生成【1†L1-L4】等行号引用；只用source_ids。"
    "代码和表格的数据数组可以保留，例如[1]不代表来源。"
    "题目已给条件、诚实说明资料不足或寒暄可以使用空source_ids；空数组不能免除事实核验。"
    "选了来源不等于来源支持事实；仍要逐段核对原文，不支持就承认缺资料，不能绑无关来源。"
    "不得包含隐藏思维链、分析标签或额外reasoning字段。此前要求事实紧邻[n]在本协议中通过该段source_ids履行。"
)
GROUNDED_REVIEW_REMINDER = (
    "证据段落协议只记录生成模型亲自选择的来源，服务器没有认定事实受支持。"
    "复核输入的draft是模型原始正文，不含服务器依source_ids渲染的引用标记；绑定关系只在grounded_blocks.source_ids中。"
    "evidence包含本轮完整可用来源，referenced_evidence_ids列出正文实际选择的来源ID；未选择来源不能替代该段绑定。"
    "若完整evidence中另有来源可支持草稿事实，但该段没有选择它，应指出需把该真实来源绑定到对应段落，而非把它视为已引用。"
    "不要把服务器展示的[n]标记归因为模型手写，也不要用该标记判定学生要求‘不要自己写引用编号’未被遵守；按原始text与source_ids核对。"
    "逐段检查grounded_blocks中text的事实是否确由该段source_ids对应evidence支持；"
    "chosen不等于supported，绑定无关来源或无来源新增事实仍按source_support/unsupported_inference拒绝。"
    "空source_ids只允许题给条件、诚实缺资料、寒暄等无需新增来源的文字，不能据此批准通用事实。"
    "对通过服务器parse_grounded_reply的正文，引用标记只能在正文之外的source_ids元数据中；若正文没有引用标记，不能把JSON里的source_ids或服务器渲染标记判作模型手写编号。"
)
GROUND_REVIEW_CITATION_ISSUE = re.compile(
    r"(?:手写|手工|自行写|自己写|自写).{0,16}(?:引用|编号|标记)|"
    r"(?:引用|编号|标记).{0,16}(?:手写|手工|自行写|自己写|自写)"
)


class GroundedReplyProtocolError(ProviderError):
    """A rejected draft, never a fallback to a less strict parser."""


class HiddenReasoningOutput(GroundedReplyProtocolError):
    """A non-retryable stream violation that must close the upstream response."""


def _invalid(detail):
    raise GroundedReplyProtocolError("证据段落回复协议无效，已阻止输出与保存：" + detail)


def _normalize_inline_sources(text, selected):
    """Move unprotected citation markers into source_ids.

    Providers sometimes place citations inside JSON text despite the contract.
    The shared citation parser protects code, arrays, indexes, and data cells;
    other numeric markers are moved to source_ids and still pass the normal
    source allowlist plus the independent evidence review.
    """
    blocked = protected_spans(text)
    matches = []
    for match in REFERENCE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in blocked):
            continue
        if not re.fullmatch(r"(?:\[\s*[0-9０-９]+\s*\]|【\s*[0-9０-９]+\s*】|［\s*[0-9０-９]+\s*］)", match.group(0)):
            continue
        raw = next(group for group in match.groups() if group is not None)
        source_id = int(re.sub(r"[０-９]", lambda char: chr(ord(char.group(0)) - 0xFEE0), raw))
        matches.append((match.start(), match.end(), source_id))
    if not matches:
        return text, selected
    removals = []
    for start, end, source_id in matches:
        if start > 0 and text[start - 1] in " \t":
            start -= 1
        removals.append((start, end, source_id))
    additions = []
    for start, end, source_id in removals:
        if source_id not in selected and source_id not in additions:
            additions.append(source_id)
    for start, end, _ in sorted(removals, reverse=True):
        text = text[:start] + text[end:]
    return text, [*selected, *additions]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("JSON字段重复")
        result[key] = value
    return result


def _invalid_constant(value):
    _invalid("JSON包含非标准常量")


def validate_public_draft(text):
    """Also usable while buffering to close an upstream hidden-reasoning stream."""
    if type(text) is not str or len(text) > MAX_REPLY_CHARACTERS:
        _invalid("响应内容类型或长度无效")
    if (re.search(r"<\s*/?\s*(?:think|analysis|reasoning)\b", text, re.I)
            or re.search(r"(?:^|\n)\s*(?:analysis|reasoning|思考过程|思维链|推理过程)\s*[:：]", text, re.I)
            ):
        raise HiddenReasoningOutput("响应包含隐藏分析或非展示内容")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", text):
        _invalid("响应包含非展示字符")


def _json_candidate(text):
    return bool(text.startswith(("{", "[", '"', "\ufeff"))
                or re.search(r'"blocks"\s*:', text)
                or re.match(r"^(?:```|~~~)\s*(?:json\b|\{)", text, re.I)
                or re.fullmatch(r"(?:true|false|null|NaN|[-+]?Infinity|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", text))


def _discard_wrapped_provider_reasoning(text):
    """Discard only a leading, explicitly tagged reasoning block before JSON.

    Some OpenAI-compatible providers leak a complete <think>/<analysis> block
    into streamed content despite thinking being disabled. This block is never
    returned to the user or reviewer. Unclosed tags and tags inside JSON text
    remain rejected by validate_public_draft.
    """
    text = text.strip()
    while match := re.match(r"<\s*(think|analysis|reasoning)\b[^>]*>", text, re.I):
        tag = match.group(1)
        closing = re.search(rf"</\s*{re.escape(tag)}\s*>", text[match.end():], re.I)
        if not closing:
            break
        end = match.end() + closing.end()
        remainder = text[end:].lstrip()
        if not remainder.startswith("{"):
            break
        text = remainder
    return text


def parse_grounded_reply(content, citations):
    """Return validated blocks, or None only for clearly non-JSON legacy prose.

    A legacy provider must still pass the caller's existing citation and review
    gates. Neither this parser nor the renderer chooses a source on its behalf.
    """
    if type(content) is not str or len(content) > MAX_REPLY_CHARACTERS:
        _invalid("响应内容类型或长度无效")
    text = _discard_wrapped_provider_reasoning(content)
    validate_public_draft(text)
    if not text:
        _invalid("响应为空")
    if not _json_candidate(text):
        return None
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        _invalid("响应不是完整JSON对象，不能包含围栏或其他文字")
    if type(value) is not dict or set(value) != {"blocks"}:
        _invalid("顶层必须且只能包含blocks")
    blocks = value["blocks"]
    if type(blocks) is not list or not 1 <= len(blocks) <= MAX_BLOCKS:
        _invalid("blocks必须为1至32段的数组")
    allowed = {item["id"] for item in citations if type(item.get("id")) is int and item["id"] > 0}
    result, total = [], 0
    for block in blocks:
        if type(block) is not dict or set(block) != {"text", "source_ids"}:
            _invalid("段落必须且只能包含text和source_ids")
        body, selected = block["text"], block["source_ids"]
        if type(body) is not str or not body.strip() or len(body) > MAX_BLOCK_CHARACTERS:
            _invalid("text必须为非空且最多8192字的字符串")
        validate_public_draft(body)
        if type(selected) is not list or len(selected) > MAX_BLOCK_SOURCES:
            _invalid("source_ids必须为最多32个整数ID的数组")
        if any(type(source_id) is not int for source_id in selected):
            _invalid("source_ids包含未知或非整数证据ID")
        if len(set(selected)) != len(selected):
            _invalid("同段source_ids不能重复")
        # Some providers still put their chosen citations at the end of the
        # paragraph despite the JSON contract. Normalize that unambiguous
        # trailing form into source_ids, then run the same evidence-ID and
        # independent-review gates as for structured output.
        body, selected = _normalize_inline_sources(body, selected)
        if re.search(r"[【\[［]\s*\d+\s*†\s*L\d+", prose_only(body), re.I):
            _invalid("text不能编造来源行号引用，请使用source_ids")
        if REFERENCE.search(prose_only(body)):
            _invalid("text不能自写自然语言引用编号，请使用source_ids")
        if any(type(source_id) is not int or source_id <= 0 or source_id not in allowed for source_id in selected):
            _invalid("source_ids包含未知或非整数证据ID")
        if len(selected) > MAX_BLOCK_SOURCES:
            _invalid("source_ids必须为最多32个整数ID的数组")
        total += len(body)
        if total > MAX_TEXT_CHARACTERS:
            _invalid("总正文超过32768字")
        result.append({"text": body.strip(), "source_ids": list(selected)})
    return {"blocks": result}


def render_grounded_reply(reply):
    """Append only IDs explicitly selected by the model, outside code/tables."""
    rendered = []
    for block in reply["blocks"]:
        body = block["text"]
        if block["source_ids"]:
            markers = "".join(f"[{source_id}]" for source_id in block["source_ids"])
            separator = "\n\n" if "\n" in body or body.startswith(("```", "~~~", "|")) else " "
            body += separator + markers
        rendered.append(body)
    return "\n\n".join(rendered)


def reconcile_grounded_review_metadata(review, reply):
    """Discard only reviewer claims that normalized citation metadata is body text."""
    if type(review) is not dict or type(reply) is not dict:
        return review
    body = "\n\n".join(block.get("text", "") for block in reply.get("blocks", [])
                       if type(block) is dict and type(block.get("text")) is str)
    if REFERENCE.search(prose_only(body)):
        return review
    issues = [issue for issue in review.get("issues", [])
              if not (type(issue) is dict and issue.get("kind") == "condition"
                      and type(issue.get("detail")) is str
                      and GROUND_REVIEW_CITATION_ISSUE.search(issue["detail"]))]
    if len(issues) == len(review.get("issues", [])):
        return review
    return {"approved": not issues, "issues": issues}
