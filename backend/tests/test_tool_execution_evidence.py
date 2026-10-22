"""Successful execution, not a tool name or model approval, proves tool use."""
from __future__ import annotations

import json

import pytest

from backend.app.db import dumps
from backend.app.fact_review import validate_tool_verification_claims
from backend.app.provider import ProviderError
from backend.tests.test_api import setup, register, events


@pytest.mark.parametrize("draft", [
    "使用分页地址转换工具核验结果如下：物理地址为8072。",
    "经工具验证，结果是8072。",
    "本轮已通过 `translate_paging_address` 工具完成验证。",
    "工具已完成核验，结果一致。",
    "计算器验证了这个结果。",
    "工具输出显示物理地址为8072。",
    "This result was verified using the tool.",
    "工具核验失败，但这个结果已经通过工具验证。",
])
def test_completed_verification_cannot_be_asserted_without_successful_execution(draft):
    with pytest.raises(ProviderError, match="成功执行结果"):
        validate_tool_verification_claims(draft, [], {"translate_paging_address"})


@pytest.mark.parametrize("draft", [
    "工具调用失败，尚未完成工具核验。",
    "本次没有工具核验结果，不能提供已验证的数值。",
    "未能使用分页地址转换工具核验，缺少成功结果。",
    "请使用 translate_paging_address 工具验证。",
    "需要使用分页地址转换工具核验后才能给出结果。",
    "计算器可以验证算术结果，但本轮没有执行。",
    "学生要求‘使用分页地址转换工具核验’，本轮尚未完成。",
    "你要求“使用 translate_paging_address 工具验证”，目前缺少成功结果。",
    "“请使用 translate_paging_address 工具验证”是题目的要求。",
    "不应声称经工具验证。",
    "无需使用计算器验证这个概念定义。",
    "> 经工具验证，结果如下。\n上述是学生引用的旧说法，本轮尚未核验。",
    "```text\n经工具验证，结果如下。\n```\n该示例字符串不构成已执行说明。",
])
def test_honest_failures_instructions_and_quoted_requests_are_not_success_claims(draft):
    validate_tool_verification_claims(draft, [], {"translate_paging_address"})


def test_different_tool_success_cannot_certify_requested_atomic_paging_tool():
    unrelated = [{"tool": "calculate", "arguments": {"expression": "2+3"}, "result": {"result": 5}}]
    with pytest.raises(ProviderError, match="translate_paging_address"):
        validate_tool_verification_claims("通过分页地址转换工具核验结果如下。", unrelated, {"translate_paging_address"})
    with pytest.raises(ProviderError, match="translate_paging_address"):
        validate_tool_verification_claims("经工具验证，物理地址如下。", unrelated, {"translate_paging_address"}, {"translate_paging_address"})
    validate_tool_verification_claims("通过 translate_paging_address 工具核验了结果。", [{"tool": "translate_paging_address", "result": {"present": True}}])


def install_protocol(provider, tables, drafts):
    planner_calls, review_calls, streams = [], [], []

    async def complete(messages, tools=None, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            review_calls.append(json.loads(messages[1]["content"]))
            return {"content": dumps({"approved": True, "issues": []}), "usage": {}, "model": "test-review"}
        planner_calls.append(messages)
        already = sum(message["role"] == "tool" for message in messages)
        if already >= len(tables):
            return {"tool_calls": [], "usage": {}, "model": "test-planner"}
        return {"tool_calls": [{"id": f"paging-{already}", "name": "translate_paging_address",
                                "arguments": dumps({"logical_address": 5000, "page_size_bytes": 1024, "page_table": tables[already]})}],
                "usage": {}, "model": "test-planner"}

    async def stream(messages, **kwargs):
        streams.append(messages)
        draft = drafts[min(len(streams) - 1, len(drafts) - 1)]
        if callable(draft):
            draft = draft(messages)
        yield {"text": draft, "model": "test-teaching"}

    provider.complete, provider.stream = complete, stream
    return planner_calls, review_calls, streams


QUESTION = "请使用 translate_paging_address 核验分页题：字节地址5000，页大小1024字节，页号4映射到页框号7，编号从0开始。"
FALSE_CLAIM = "使用分页地址转换工具核验结果如下：页号4，页内偏移904，物理地址8072。"
HONEST_FAILURE = "translate_paging_address 本轮调用失败，缺少成功计算依据，尚未完成工具核验；因此不报告已验证的数值。"


def test_failed_tool_twice_has_one_retry_and_approval_cannot_save_false_claim(setup):
    client, app, provider = setup
    register(client)
    planner, reviews, streams = install_protocol(provider, [{}, {}, {}, {}], [FALSE_CLAIM])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": QUESTION, "mode": "solve"}))
    assert result[-1][0] == "error" and "成功执行结果" in result[-1][1]["message"]
    assert len(planner) == len(streams) == 2  # One parameter retry and one answer repair.
    assert reviews == []  # The deterministic guard runs before the approving reviewer.
    assert sum(event == "answer_reset" for event, _ in result) == 1
    traces = [payload for event, payload in result if event == "trace"]
    paging = [trace for trace in traces if trace["node"] == "translate_paging_address"]
    assert len(paging) == 2 and all(trace["status"] == "failed" for trace in paging)
    assert all(trace["argument_shape"]["page_table_type"] == "object" and trace["argument_shape"]["page_table_entries"] == 0 for trace in paging)
    assert not any(event in {"done", "citations"} for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


def test_failed_tool_false_claim_can_repair_to_honest_unverified_answer(setup):
    client, app, provider = setup
    register(client)
    planner, reviews, streams = install_protocol(provider, [{}, {}], [FALSE_CLAIM, HONEST_FAILURE])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": QUESTION, "mode": "solve"}))
    assert result[-1][0] == "done"
    assert result[-1][1]["final_text"] == HONEST_FAILURE
    assert result[-1][1]["grounded"] is False
    assert len(planner) == len(streams) == 2 and len(reviews) == 1
    assert reviews[0]["computations"] == [] and len(reviews[0]["tool_failures"]) == 2
    assert next(payload for event, payload in result if event == "citations")["items"] == []
    assert "本轮计算工具失败" in streams[0][0]["content"]
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"] == HONEST_FAILURE


def test_valid_input_retry_produces_real_computation_and_only_then_verified_citation(setup):
    client, app, provider = setup
    register(client)

    def answer(messages):
        prompt = messages[0]["content"]
        citations = json.loads(prompt.split("可引用证据（位置说明已由服务器标注）：\n", 1)[1].split("\n实际工具结果：\n", 1)[0])
        computed = next(citation for citation in citations if citation["source_type"] == "verified_computation")
        return f"本轮通过 translate_paging_address 工具核验：物理地址为8072 [{computed['id']}]。"

    _, reviews, _ = install_protocol(provider, [{}, [{"page": 4, "frame": 7}]], [answer])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": QUESTION, "mode": "solve"}))
    assert result[-1][0] == "done"
    traces = [payload for event, payload in result if event == "trace" and payload["node"] == "translate_paging_address"]
    assert [trace["status"] for trace in traces] == ["failed", "completed"]
    actual = json.loads(traces[1]["detail"])
    assert (actual["page"], actual["offset"], actual["frame"], actual["physical_address"]) == (4, 904, 7, 8072)
    citations = next(payload for event, payload in result if event == "citations")["items"]
    assert len(citations) == 1 and citations[0]["source_type"] == "verified_computation"
    assert json.loads(citations[0]["excerpt"]) == actual
    assert len(reviews[0]["computations"]) == 1
    assert reviews[0]["computations"][0]["arguments"] == {"logical_address": 5000, "page_size_bytes": 1024, "page_table": [{"page": 4, "frame": 7}]}


def test_invalid_tool_diagnostic_never_saves_unknown_parameter_names_or_values(setup):
    client, app, provider = setup
    register(client)
    invalid = [{"page": 4, "frame": 7, "private-sentinel-field": "private-sentinel-value"}]
    install_protocol(provider, [invalid, invalid], [HONEST_FAILURE])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": QUESTION, "mode": "solve"}))
    assert result[-1][0] == "done"
    trace = app.state.db.one("SELECT trace FROM messages WHERE role='assistant'")["trace"]
    assert "private-sentinel" not in trace
    paging = [item for item in json.loads(trace) if item["node"] == "translate_paging_address"]
    assert all(item["argument_shape"]["page_table_unknown_fields"] is True for item in paging)
    assert all(item["argument_shape"]["page_table_record_fields"] == ["frame", "page"] for item in paging)
