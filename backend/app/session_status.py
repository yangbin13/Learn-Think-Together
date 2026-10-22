"""Recognize only a standalone request to acknowledge the current chat."""
from __future__ import annotations

import re


_ACK = re.compile(
    r"(?:请)?(?:只|仅)?(?:确认|告诉我)(?:一下)?(?:你|助教|我们)?"
    r"(?:能否|是否(?:可以|能)|还(?:可以|能)|能够|能|可以)"
    r"(?:继续|恢复)(?:本门|这门|当前)?(?:课程)?(?:对话|提问|学习|教学)?"
)
_CONTEXT = re.compile(
    r"(?:你好|您好)|"
    r"(?:刚才我|刚刚我|我刚才|我刚刚|我)(?:停止|取消|中止)(?:了)?(?:生成|回答|回复)|"
    r"(?:暂时|先)?(?:不要|不|不用)(?:讲解|讲|讨论|解释)(?:知识点|知识|内容|题目)"
)


def is_session_ack_request(text):
    """No substring matching or removal of quoted/unrecognized knowledge tasks.

    This recognizes a small operational vocabulary, not general intent or model
    health. Every clause must belong to it and a confirmation must be present.
    """
    if type(text) is not str or len(text) > 240 or re.search(r"[`<>\[\]{}\"'“”‘’「」『』]", text):
        return False
    clauses = [part.strip() for part in re.split(r"[。！？!?，,；;\n]", text) if part.strip()]
    return bool(clauses and any(_ACK.fullmatch(part) for part in clauses)
                and all(_ACK.fullmatch(part) or _CONTEXT.fullmatch(part) for part in clauses))
