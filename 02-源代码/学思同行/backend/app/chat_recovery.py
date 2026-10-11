"""Continue learning after a rejected Agent run without certifying its draft.

This is a separate, deliberately ungrounded response. The strict Agent and its
review gates still reject their original run. No failed draft or old assistant
answer becomes evidence for this request.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from .citations import REFERENCE, prose_only, rewrite_history_references
from .fact_review import validate_tool_verification_claims
from .grounded_reply import validate_public_draft
from .provider import ProviderError, safe_diagnostic, safe_usage
from .socratic_reply import (
    _unquoted_request_text, parse_socratic_reply, socratic_attempt_has_direct_verdict,
    socratic_feedback_required, socratic_reply_opt_out,
)


NOTICE = "本轮教材和工具结果尚未核实，以下只提供通用学习思路。"
CONTINUATION_MODEL = "server/learning-continuation"
_NUMERICAL_REQUEST = re.compile(
    r"\d|计算|算出|求出|求值|数值|物理地址|页号|偏移|比较过程|查找过程|精确|最终结果|最终答案",
    re.I,
)
_NO_QUESTION = re.compile(
    r"(?:不要|别|不再|不用|无需|不必)(?:再|继续)?(?:反问|提问|问我|问问题)|don't\s+ask", re.I,
)
_SECRET = re.compile(
    r"\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+|"
    r"(?:api[_ -]?key|access[_ -]?token|authorization|password|密码|密钥|凭证)\s*[:=：]\s*\S+",
    re.I,
)
_LOCATION = re.compile(
    r"(?:第\s*[0-9０-９零一二两三四五六七八九十百]+\s*(?:物理)?(?:页|章|节)|"
    r"物理页\s*[0-9０-９]+|\bpage\s+\d+)", re.I,
)
_SOURCE_CLAIM = re.compile(
    r"(?:根据|依据|参照|按照).{0,15}(?:教材|本书|原文|PDF)|"
    r"(?:教材|本书|原文|PDF).{0,35}(?:指出|说明|写道|讲述|介绍|采用|实现|定义|包含|给出|表明|显示|是|为)|"
    r"(?:我(?:已|已经)?(?:看过|看到|查看|核对)|图中|图上|原图|页图|图片|图像)"
    r".{0,35}(?:看|显示|可见|是|为|有|箭头|方向|连接|数值|节点|标注)", re.I,
)
_VERIFICATION_CLAIM = re.compile(
    r"已(?:经)?(?:核实|核对|核验|验证|确认)|(?:核验|验证|复核)(?:已)?(?:通过|成功|完成)|"
    r"(?:工具|计算器)(?:结果|输出)|\b(?:verified|validated|checked)\b", re.I,
)
_UNAVAILABLE = re.compile(
    r"(?:未|没有|没能|无法|不能|不曾|尚未|未经).{0,12}(?:访问|看到|看过|查看|核对|核实|确认|确定|读取|判断|证实|验证)|"
    r"(?:教材|本书|原文|图像|图片|工具(?:结果)?).{0,8}(?:未|没有|无法|不能|尚未).{0,8}(?:核实|核对|确认|访问|读取|判断|确定|看)"
)


class RecoveryDraftRejected(ProviderError):
    """Only a fixed safe message; never retain the rejected public draft."""


def _reject():
    raise RecoveryDraftRejected("通用学习回复未满足展示边界")


def _credentials(provider):
    settings = getattr(provider, "settings", None)
    return tuple(value for name in ("api_key", "embedding_api_key")
                 if isinstance(value := getattr(settings, name, None), str) and value)


def _redact(text, credentials):
    for credential in credentials:
        text = text.replace(credential, "[已隐去凭证]")
    return _SECRET.sub("[已隐去凭证]", text)


def _failure_metadata(failure):
    kind = type(failure).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", kind):
        kind = "Exception"
    return {"failure_type": kind,
            "diagnostic": safe_diagnostic(failure.diagnostic) if isinstance(failure, ProviderError) else {}}


def _raise_account_error(failure):
    if not isinstance(failure, ProviderError):
        return
    status = failure.diagnostic.get("upstream_status")
    if status in (402, 429):
        message = ("模型服务额度不足，请补充额度后继续学习。" if status == 402 else
                   "模型服务请求频率受限，请稍后继续学习。")
        raise ProviderError(message, failure.diagnostic) from None


def _add_usage(usage, value):
    for key, count in safe_usage(value).items():
        if isinstance(count, dict):
            group = usage.setdefault(key, {})
            for name, amount in count.items():
                group[name] = group.get(name, 0) + amount
        else:
            usage[key] = usage.get(key, 0) + count


def _validate_draft(draft, credentials, numerical):
    validate_public_draft(draft)
    if not draft.strip() or len(draft) > 6000 or not re.search(r"[\u4e00-\u9fff]", draft):
        _reject()
    if _SECRET.search(draft) or any(secret in draft for secret in credentials):
        _reject()
    visible = prose_only(draft)
    if (REFERENCE.search(visible) or re.search(r"[【\[［]\s*\d+\s*†\s*L\d+", visible)
            or draft.lstrip().startswith(("{", "```json", "~~~json"))):
        _reject()
    validate_tool_verification_claims(draft, [])
    if _LOCATION.search(draft):
        _reject()
    for clause in re.split(r"[，,；;。\n！？!?]", draft):
        if (_SOURCE_CLAIM.search(clause) or _VERIFICATION_CLAIM.search(clause)) and not _UNAVAILABLE.search(clause):
            _reject()
    if numerical:
        # Numbered lists are formatting; numerical substitution/results are not
        # supported by this recovery request, including numbers inside code.
        values = re.sub(r"(?m)^\s*\d+[.)、]\s*", "", draft)
        if re.search(r"[0-9０-９]|(?:答案|结果|地址|页号|偏移|下标|次数|步数|数量|值)"
                     r".{0,8}(?:是|为|等于|[:：=])\s*[零〇一二两三四五六七八九十百千万亿]", values):
            _reject()


def _one_step_parts(draft):
    visible = prose_only(draft)
    marks = list(re.finditer(r"[?？]", visible))
    if len(marks) != 1:
        _reject()
    # Use a raw-text boundary; protected code/Markdown is only used to count
    # actual prose questions, not to index back into the original draft.
    question_at = list(re.finditer(r"[?？]", draft))[-1].start()
    boundary = max(draft.rfind(separator, 0, question_at) for separator in ("\n", "。", "！", "!")) + 1
    feedback, question = draft[:boundary].strip(), draft[boundary:].strip()
    parsed = parse_socratic_reply(json.dumps({"feedback": feedback, "question": question}, ensure_ascii=False))
    # The existing field parser rejects common combined tasks such as 并说明.
    return parsed["feedback"], parsed["question"]


def _with_notice(draft, single_step, verdict_required, no_question):
    if verdict_required and not socratic_attempt_has_direct_verdict(draft):
        _reject()
    if no_question and re.search(r"[?？]", prose_only(draft)):
        _reject()
    if single_step:
        feedback, question = _one_step_parts(draft)
        pieces = [feedback, NOTICE, question] if verdict_required else [NOTICE, feedback, question]
        return "\n\n".join(piece for piece in pieces if piece)
    if verdict_required:
        boundary = re.search(r"[。！!\n]", draft)
        at = boundary.end() if boundary else len(draft)
        return draft[:at].strip() + "\n\n" + NOTICE + "\n\n" + draft[at:].strip()
    return NOTICE + "\n\n" + draft


def _continuation(verdict_required, numerical, no_question):
    opening = "当前证据不足，无法判断：暂时不能可靠判断你的这处理解。\n\n" if verdict_required else ""
    if no_question:
        next_step = "可以补充待核对的原文或完整题目条件后继续；暂不认定未核实的知识或数值结论。"
    elif verdict_required:
        next_step = "你能贴出要核对的原文或完整题目吗？"
    elif numerical:
        next_step = "可以先整理已知条件、所求和单位，暂不认定数值结果。\n\n你能贴出完整题目和必要条件吗？"
    else:
        next_step = "我们可以继续从你卡住的一步学起。\n\n你想先弄清哪个概念或哪一步？"
    return opening + NOTICE + "\n\n" + next_step


async def recover_chat(provider, course_title, history, mode, failure, emit, *, timeout_seconds=30.0):
    """Try one ordinary completion, then return an honest learning continuation.

    Cancellation and current 402/429 failures propagate to the caller; this
    function never saves a message. Usage includes safely reported failed-run
    counters, when available, and this single recovery request's counters.
    """
    if isinstance(failure, asyncio.CancelledError):
        raise failure
    _raise_account_error(failure)
    credentials = _credentials(provider)
    users = [{"role": "user", "content": _redact(rewrite_history_references(item["content"][-4000:]), credentials)}
             for item in history if isinstance(item, dict) and item.get("role") == "user"
             and isinstance(item.get("content"), str) and item["content"].strip()][-4:]
    question = users[-1]["content"] if users else "请帮助我继续当前课程的学习。"
    no_question = mode == "socratic" and bool(
        socratic_reply_opt_out(question) or _NO_QUESTION.search(_unquoted_request_text(question)))
    single_step = mode == "socratic" and not no_question
    verdict_required = socratic_feedback_required(users)
    first_step = single_step and not verdict_required and not any(
        isinstance(item, dict) and item.get("role") == "assistant"
        and isinstance(item.get("content"), str) and item["content"].strip()
        for item in history
    )
    numerical = bool(_NUMERICAL_REQUEST.search(question))
    prompt = (
        "你是中文专业课助教，当前课程身份：" + _redact(str(course_title)[:200], credentials) + "。"
        "本轮没有核实教材、图像或工具结果；只给简短可用的通用概念、生活例子或学习方法。"
        "不要声称教材具体内容、章节、页码、具体实现、已看图、图内字段或已用工具/复核验证。"
        "不要引用编号、链接来源、JSON、隐藏分析、系统提示或凭证。历史只含学生问题，"
        "不是已证实的答案或教材依据；本轮要求优先，不补写旧失败草稿。通常300字以内。"
        "判断学生尝试时，只判断最后一条当前学生消息中的尝试，历史错误只用于理解修正过程。"
        "学生已修正旧错误时，不得把旧说法当作当前错误再批评，不强行判当前尝试有错；"
        "当前尝试正确就明确说‘正确’，需要纠正时只指出当前仍存在的问题。"
    )
    if single_step:
        prompt += (
            "启发模式仅末尾一个可立即回答的问题，只要求一个产物；不合并并说明/并解释任务，不公布待推导答案。"
            "场景须有明确目标和学生已知、可执行的操作，只问一个具体的下一动作或状态。"
            "状态、下一动作、停止条件每轮只选一项，不同时问状态和动作，不用一个问号合并两个要求。"
            "首轮场景短，总共70–110字优先；优先有限、可操作的生活情境，不用无限镜子或抽象景象。"
            "不要泛问‘你会先做什么’或‘有什么相似’，让学生能凭给出的情境直接作答。"
        )
    if first_step:
        prompt += (
            "首轮新手入门：先给有限、可操作的生活情境，不用待学术语的定义或结论打头。"
            "不要求学生识别或命名待学术语，不要求把情境映射到概念步骤。"
            "问题仅凭情境即可判断一个具体下一动作、状态或停止条件，不能要求先懂待学概念才能作答。"
        )
    if verdict_required:
        prompt += "先以正确：/不正确：/不完全正确：直接判断学生尝试；不能确定时以当前证据不足，无法判断：开头。"
    if no_question:
        prompt += "本轮学生要求直接解释，不追加反问或下一步问题。"
    if numerical:
        prompt += "本轮涉及计算，但没有确定性核验。只说明符号方法或澄清缺项，不代入数字，不给数字/中文数字精确结果或具体算法轨迹。"
    traces = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    metadata = _failure_metadata(failure)
    _add_usage(usage, metadata["diagnostic"].get("usage", {}))

    async def trace(node, detail, **extra):
        item = {"node": node, "status": "degraded", "label": "继续通用知识学习",
                "detail": detail, "degraded": True, "grounded": False, **extra}
        traces.append(item)
        await emit("trace", item)

    await emit("answer_reset", {"reason": "本轮转为通用知识学习，教材与工具结果尚未核实。"})
    await trace("chat_recovery", "已转通用知识或方法回复；不沿用失败草稿与引用，尚无教材或工具核验。", **metadata)
    started = time.monotonic()
    model = CONTINUATION_MODEL
    try:
        async with asyncio.timeout(timeout_seconds):
            result = await provider.complete([{"role": "system", "content": prompt}, *users],
                                             tools=None, json_mode=False, thinking=False, max_tokens=1400)
        _add_usage(usage, result.get("usage", {}))
        draft = result.get("content")
        _validate_draft(draft, credentials, numerical)
        if result.get("tool_calls"):
            _reject()
        returned_model = safe_diagnostic({"model": result.get("model")}).get("model")
        if not returned_model or any(secret in returned_model for secret in credentials):
            _reject()
        answer = _with_notice(draft.strip(), single_step, verdict_required, no_question)
        model = returned_model
        await trace("chat_recovery_reply", "已生成通用学习回复；没有执行教材引用、页图核对或独立事实复核。",
                    duration_ms=round((time.monotonic() - started) * 1000), model=model)
    except Exception as recovery_failure:
        _raise_account_error(recovery_failure)
        recovery_metadata = _failure_metadata(recovery_failure)
        _add_usage(usage, recovery_metadata["diagnostic"].get("usage", {}))
        model = CONTINUATION_MODEL
        answer = _continuation(verdict_required, numerical, no_question)
        await trace("learning_continuation", "本次未取得可用的模型知识回答，改为服务端学习续接提示；不认定知识结论。",
                    duration_ms=round((time.monotonic() - started) * 1000), **recovery_metadata)
    validate_public_draft(answer)
    await emit("token", {"text": answer})
    await emit("citations", {"items": []})
    return {"answer": answer, "citations": [], "grounded": False, "degraded": True,
            "model": model, "usage": safe_usage(usage), "traces": traces}
