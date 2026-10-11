"""Prompt transmission and review control flow, not real critic capability."""
import json

import httpx
import pytest

from backend.app.db import dumps
from backend.app.fact_review import (
    REVIEW_PROMPT, SOURCE_IMPLEMENTATION_REVIEW_PROMPT, SOURCE_IMPLEMENTATION_TEACHING_PROMPT,
    build_teaching_review_contract,
)
from backend.app.socratic_reply import SOCRATIC_REPLY_PREFIX
from backend.tests.test_api import events, register, setup  # noqa: F401


ARRAY_SOURCE = "栈专题示例结构：该实现用嵌套数组保存条目，内层数组保存键和值。本例没有描述节点或next指针。"
LIST_SOURCE = "栈专题示例结构：该实现用链表保存条目，节点包含键和值，每个节点的next指针连接后继节点。"
STATE_SOURCE = "栈专题示例模型：状态栈只保存等待恢复的状态。仅进入非终止分支时保存状态，终止分支直接返回而不保存状态。"
BAD_A_OR_B = "本课程材料明确采用数组（或链表）保存条目。"
GOOD_A = "本课程材料采用嵌套数组保存条目。"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real outbound HTTP is forbidden in implementation attribution tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)


def test_prompt_contract_distinguishes_source_only_A_and_general_alternative_B():
    assert "只给出实现A" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "A或B" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "另有支持B的当前来源" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "本轮来源未证实B" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert SOURCE_IMPLEMENTATION_REVIEW_PROMPT in REVIEW_PROMPT
    assert "即使B一般可行" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    assert "对应来源确实支持B则可以接受" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    assert "不能按某个结构词一律拒绝" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    assert "抽象状态模型" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "不能仅凭一般调用栈常识改写来源模型" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    for prompt in (SOURCE_IMPLEMENTATION_TEACHING_PROMPT, SOURCE_IMPLEMENTATION_REVIEW_PROMPT):
        # No textbook title, answer keys, page anchor, or this incident's keys.
        assert all(value not in prompt for value in ("bad", "dab", "evil", "pat", "79", "数据结构与算法图解"))


@pytest.mark.parametrize("mode,single", [("explain", False), ("solve", False),
                                        ("socratic", False), ("socratic", True)])
def test_server_contract_keeps_implementation_attribution_in_every_teaching_scope(mode, single):
    contract = build_teaching_review_contract(mode, single_step_scope=single, feedback_allowed=False)
    policy = contract["implementation_attribution_policy"]
    assert policy["provenance"] == "server"
    assert "A或B" in policy["source_only_A"] and "通用扩展" in policy["alternative_B"]
    assert "来源归因" in "".join(contract["required_checks"])
    assert "approved" not in policy


def install_protocol(provider, drafts, decisions, *, single):
    previous_complete = provider.complete
    teaching, reviews, review_systems, closed = [], [], [], []

    def next_draft(messages):
        text = drafts[min(len(teaching), len(drafts) - 1)]
        teaching.append(messages)
        if single:
            return {"feedback": "", "question": text.rstrip("。") + "[1]，你会先检查哪一项？"}
        return {"blocks": [{"text": text, "source_ids": [1]}]}

    async def complete(messages, **kwargs):
        prompt = messages[0]["content"]
        if prompt.startswith(SOCRATIC_REPLY_PREFIX):
            return {"content": dumps(next_draft(messages)), "usage": {"total_tokens": 5}, "model": "fake-teacher"}
        if prompt.startswith("你是独立事实审查器"):
            reviews.append(json.loads(messages[1]["content"]))
            review_systems.append(prompt)
            return {"content": dumps(decisions[min(len(reviews) - 1, len(decisions) - 1)]),
                    "usage": {"total_tokens": 7}, "model": "fake-critic"}
        return await previous_complete(messages, **kwargs)

    async def stream(messages, **kwargs):
        try:
            text = dumps(next_draft(messages))
            yield {"text": text[:18]}
            yield {"text": text[18:]}
            yield {"usage": {"total_tokens": 5}, "model": "fake-teacher"}
        finally:
            closed.append(True)

    provider.complete, provider.stream = complete, stream
    return teaching, reviews, review_systems, closed


REJECTED = {"approved": False, "issues": [{"kind": "source_support", "detail": "来源只描述嵌套数组，未支持将同一教材实现扩展成数组或链表。"}]}
APPROVED = {"approved": True, "issues": []}


@pytest.mark.parametrize("mode,single", [("explain", False), ("solve", False),
                                        ("socratic", False), ("socratic", True)])
def test_source_specific_rule_reaches_teacher_and_critic_and_rejected_draft_can_be_repaired(setup, mode, single):
    client, app, provider = setup
    register(client)
    app.state.db.execute("UPDATE chunks SET text=? WHERE course_id=?", (ARRAY_SOURCE, "data-structures"))
    teaching, reviews, systems, closed = install_protocol(provider, [BAD_A_OR_B, GOOD_A], [REJECTED, APPROVED], single=single)
    question = "请按本课程材料提示我一个关键问题。" if single else "请直接解释本课程材料的示例结构。"
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": question}))
    assert result[-1][0] == "done" and len(teaching) == len(reviews) == 2
    assert len(closed) == (0 if single else 2)
    assert all("教材的具体实现与通用替代实现分开" in messages[0]["content"]
               and "只支持A的来源不能归因为A或B" in messages[0]["content"] for messages in teaching)
    assert all(SOURCE_IMPLEMENTATION_REVIEW_PROMPT in prompt for prompt in systems)
    assert all(review["teaching_contract"]["implementation_attribution_policy"]["provenance"] == "server" for review in reviews)
    assert ARRAY_SOURCE in reviews[0]["evidence"][0]["excerpt"]
    assert "或链表" in reviews[0]["draft"] and "或链表" not in reviews[1]["draft"]
    assert reviews[1]["previous_review_issues"] == REJECTED["issues"]
    tokens = "".join(item["text"] for event, item in result if event == "token")
    assert "或链表" not in tokens and "嵌套数组" in tokens
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"] == tokens


@pytest.mark.parametrize("draft", [
    BAD_A_OR_B, "本课程图示明确用链表保存条目。",
    "本课程先展示数组，实际通过链表方式共存。",
    "通用扩展：链表也能保存条目，所以本课程数组引用足以证明这里是链表。",
])
def test_unrepaired_implementation_attribution_issue_emits_and_saves_nothing(setup, draft):
    client, app, provider = setup
    register(client)
    app.state.db.execute("UPDATE chunks SET text=? WHERE course_id=?", (ARRAY_SOURCE, "data-structures"))
    _, reviews, _, closed = install_protocol(provider, [draft], [REJECTED], single=False)
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": "explain", "message": "请解释本课程示例结构。"}))
    assert result[-1][0] == "error" and len(reviews) == len(closed) == 2
    assert not any(event in {"token", "done"} for event, _ in result)
    assert not app.state.db.one("SELECT id FROM messages WHERE role='assistant'")


@pytest.mark.parametrize("mode,single", [("explain", False), ("socratic", True)])
def test_actual_linked_list_evidence_can_still_reach_fake_approval_without_word_blacklist(setup, mode, single):
    client, app, provider = setup
    register(client)
    app.state.db.execute("UPDATE chunks SET text=? WHERE course_id=?", (LIST_SOURCE, "data-structures"))
    _, reviews, _, _ = install_protocol(provider, ["本课程材料用链表保存条目，节点含next指针。"], [APPROVED], single=single)
    question = "请按本课程材料提示我一个关键问题。" if single else "请解释本课程示例结构。"
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": question}))
    assert result[-1][0] == "done" and len(reviews) == 1
    assert LIST_SOURCE in reviews[0]["evidence"][0]["excerpt"]
    assert "链表" in result[-1][1]["final_text"]
    assert app.state.db.one("SELECT id FROM messages WHERE role='assistant'")
    # Fake approval proves the server does not ban the word; it does not prove
    # a real critic will correctly identify the implementation in new evidence.


@pytest.mark.parametrize("reject", [False, True])
def test_abstract_state_model_and_active_call_frame_are_sent_to_review_as_distinct_scopes(setup, reject):
    client, app, provider = setup
    register(client)
    app.state.db.execute("UPDATE chunks SET text=? WHERE course_id=?", (STATE_SOURCE, "data-structures"))
    text = ("教材状态栈包括每次调用的活动帧，终止分支也会保存一项状态。" if reject else
            "该资料的状态栈只保存等待恢复的状态，终止分支直接返回且不保存状态。")
    decision = ({"approved": False, "issues": [{"kind": "source_support", "detail": "待恢复状态与活动调用帧是不同模型，不能把每次调用归成来源的保存步骤。"}]}
                if reject else APPROVED)
    teaching, reviews, systems, _ = install_protocol(provider, [text], [decision], single=False)
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": "solve", "message": "请解释本课程示例模型与状态记录口径。"}))
    assert result[-1][0] == ("error" if reject else "done")
    assert len(reviews) == (2 if reject else 1)
    assert all(STATE_SOURCE in review["evidence"][0]["excerpt"] for review in reviews)
    assert all("抽象状态模型" in messages[0]["content"] for messages in teaching)
    assert all("边界分支是否入栈" in system for system in systems)
    assert bool(app.state.db.one("SELECT id FROM messages WHERE role='assistant'")) is (not reject)
    if reject:
        assert not any(event in {"token", "done"} for event, _ in result)
