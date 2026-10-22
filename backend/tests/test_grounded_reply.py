"""Bounded protocol plus temporary ASGI persistence and review boundaries."""
import json

import httpx
import pytest

from backend.app.grounded_reply import (
    MAX_BLOCK_CHARACTERS, MAX_REPLY_CHARACTERS, GroundedReplyProtocolError,
    parse_grounded_reply, reconcile_grounded_review_metadata, render_grounded_reply,
)
from backend.tests.test_api import events, register, setup  # noqa: F401


SOURCES = [{"id": 1}, {"id": 2}]


def draft(text="栈遵循后进先出。", source_ids=None):
    return json.dumps({"blocks": [{"text": text, "source_ids": [1] if source_ids is None else source_ids}]}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def deny_async(*args, **kwargs):
        pytest.fail("real outbound HTTP is forbidden in grounded reply tests")

    def deny_sync(*args, **kwargs):
        pytest.fail("real outbound HTTP is forbidden in grounded reply tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_sync)


def test_only_model_chosen_ids_are_rendered_without_approval():
    parsed = parse_grounded_reply(draft(source_ids=[2]), SOURCES)
    assert parsed == {"blocks": [{"text": "栈遵循后进先出。", "source_ids": [2]}]}
    assert render_grounded_reply(parsed) == "栈遵循后进先出。 [2]"
    assert "approved" not in parsed
    assert render_grounded_reply(parse_grounded_reply(draft("题目已给定初始值。", []), SOURCES)) == "题目已给定初始值。"


def test_inline_citations_are_normalized_and_still_reviewable():
    parsed = parse_grounded_reply(draft("栈遵循后进先出[1]。", []), SOURCES)
    assert parsed == {"blocks": [{"text": "栈遵循后进先出。", "source_ids": [1]}]}
    assert render_grounded_reply(parsed) == "栈遵循后进先出。 [1]"
    parsed_with_duplicate = parse_grounded_reply(draft("栈遵循后进先出[1]。", [1]), SOURCES)
    assert parsed_with_duplicate == parsed
    with pytest.raises(GroundedReplyProtocolError, match="未知或非整数"):
        parse_grounded_reply(draft("栈遵循后进先出[99]。", []), SOURCES)
    embedded = parse_grounded_reply(draft("事实[1]仍未结束。", []), SOURCES)
    assert embedded == {"blocks": [{"text": "事实仍未结束。", "source_ids": [1]}]}


@pytest.mark.parametrize("marker", ["【1†L1-L4】", "【1†L1-L4}", "[1†L1-L4]", "【99†L1】", "［1†L1-L4］", "【1†l1-l4】"])
def test_invented_line_reference_cannot_be_published_as_a_server_citation(marker):
    with pytest.raises(GroundedReplyProtocolError, match="行号引用"):
        parse_grounded_reply(draft("教材事实" + marker, [1]), SOURCES)
    code = "引用格式示例：`" + marker + "`"
    assert parse_grounded_reply(draft(code, []), SOURCES)["blocks"][0]["text"] == code


@pytest.mark.parametrize("marker", ["【1】", "［1］", "【１】", "［２］"])
def test_trailing_citation_variants_normalize_to_allowed_source_ids(marker):
    expected = 1 if "1" in marker or "１" in marker else 2
    parsed = parse_grounded_reply(draft(f"事实{marker}。", []), SOURCES)
    assert parsed == {"blocks": [{"text": "事实。", "source_ids": [expected]}]}
    with pytest.raises(GroundedReplyProtocolError, match="未知或非整数"):
        parse_grounded_reply(draft("事实【3】。", []), SOURCES)


def test_sentence_and_table_end_citations_move_to_source_ids_without_capturing_data():
    parsed = parse_grounded_reply(draft("第一条事实 [1]。第二条事实 [2]。", []), SOURCES)
    assert parsed == {"blocks": [{"text": "第一条事实。第二条事实。", "source_ids": [1, 2]}]}
    repeated = parse_grounded_reply(draft("同一事实 [1][2]。", []), SOURCES)
    assert repeated == {"blocks": [{"text": "同一事实。", "source_ids": [1, 2]}]}
    table = "| 结论 | 引用 |\n| --- | --- |\n| A [1] | [2] |"
    parsed_table = parse_grounded_reply(draft(table, []), SOURCES)
    assert parsed_table["blocks"][0]["source_ids"] == [1, 2]
    assert "[1]" not in parsed_table["blocks"][0]["text"]
    assert "[2]" not in parsed_table["blocks"][0]["text"]
    data = parse_grounded_reply(draft("输入数组[1,2]的索引[0]命中。", []), SOURCES)
    assert data == {"blocks": [{"text": "输入数组[1,2]的索引[0]命中。", "source_ids": []}]}


def test_review_metadata_number_is_not_mistaken_for_a_handwritten_body_citation():
    parsed = parse_grounded_reply(draft("栈遵循后进先出[1]。", []), SOURCES)
    false_positive = {"approved": False, "issues": [
        {"kind": "condition", "detail": "正文包含手写引用编号如[1]，与用户要求冲突。"}
    ]}
    assert reconcile_grounded_review_metadata(false_positive, parsed) == {"approved": True, "issues": []}

    mixed = {"approved": False, "issues": [
        {"kind": "condition", "detail": "正文包含手写引用编号如[1]，与用户要求冲突。"},
        {"kind": "source_support", "detail": "证据不支持正文的事实。"},
    ]}
    assert reconcile_grounded_review_metadata(mixed, parsed) == {
        "approved": False, "issues": [{"kind": "source_support", "detail": "证据不支持正文的事实。"}]
    }


def test_review_reconciliation_does_not_hide_a_reference_left_in_body():
    review = {"approved": False, "issues": [
        {"kind": "condition", "detail": "正文包含手写引用编号如[1]，与用户要求冲突。"}
    ]}
    reply = {"blocks": [{"text": "正文[1]仍有引用", "source_ids": [1]}]}
    assert reconcile_grounded_review_metadata(review, reply) is review


@pytest.mark.parametrize("raw", [
    None, 1, [], "", " ", "{}", "[]", "null", "true", "42", "NaN",
    '{"blocks":[]}', '{"blocks":false}', '{"blocks":{}}',
    '{"blocks":[{"text":"事实","source_ids":[1]}],"approved":true}',
    '{"blocks":[],"blocks":[{"text":"事实","source_ids":[1]}]}',
    '{"blocks":[],"blo\\u0063ks":[]}',
    '{"blocks":[{"text":"事实","text":"覆盖","source_ids":[1]}]}',
    '{"blocks":[{"text":"事实","source_ids":[1],"reasoning":"secret"}]}',
    '{"blocks":[{"text":"事实","source_ids":[NaN]}]}',
    '{"blocks":[{"text":"事实","source_ids":[Infinity]}]}',
    '{"blocks":[{"text":"事实","source_ids":[1]}],}',
    '{"blocks":[{"text":"事实","source_ids":[1]}]} trailing',
    '下面是JSON：{"blocks":[{"text":"事实","source_ids":[1]}]}',
    '```json\n{"blocks":[{"text":"事实","source_ids":[1]}]}\n```',
    '```\n{"blocks":[{"text":"事实","source_ids":[1]}]}\n```',
    '{"blocks":', "[" * 1100 + "]" * 1100,
    " " * MAX_REPLY_CHARACTERS + draft(),
], ids=lambda value: "case")
def test_json_candidates_never_fall_back_or_bypass_protocol(raw):
    with pytest.raises(GroundedReplyProtocolError):
        parse_grounded_reply(raw, SOURCES)


def test_leading_provider_reasoning_is_discarded_before_review_and_never_rendered():
    raw = '<think>private reasoning</think>{"blocks":[{"text":"栈遵循后进先出。","source_ids":[1]}]}'
    parsed = parse_grounded_reply(raw, SOURCES)
    assert parsed == {"blocks": [{"text": "栈遵循后进先出。", "source_ids": [1]}]}
    assert "private reasoning" not in render_grounded_reply(parsed)
    for invalid in (
        '<think>private reasoning</think>普通文字',
        '<think>unclosed{"blocks":[{"text":"栈","source_ids":[1]}]}',
        '{"blocks":[{"text":"<think>private reasoning</think>栈","source_ids":[1]}]}',
    ):
        with pytest.raises(GroundedReplyProtocolError):
            parse_grounded_reply(invalid, SOURCES)


@pytest.mark.parametrize("text,ids", [
    (None, [1]), (False, [1]), (1, [1]), (" ", [1]),
    ("字" * (MAX_BLOCK_CHARACTERS + 1), [1]),
    ("事实", False), ("事实", "1"), ("事实", [True]), ("事实", [1.0]),
    ("事实", ["1"]), ("事实", [0]), ("事实", [-1]), ("事实", [99]),
        ("事实", [1, 1]), ("事实", [1] * 33),
    ("<think>private-secret</think>事实", [1]), ("</analysis>private", [1]),
    ("reasoning: private-secret", [1]), ("思维链：private-secret", [1]),
    ("bad\x00text", [1]), ("\ud800", [1]),
], ids=lambda value: "case")
def test_bad_text_or_sources_fail_without_echoing_hidden_content(text, ids):
    with pytest.raises(GroundedReplyProtocolError) as error:
        parse_grounded_reply(draft(text, ids), SOURCES)
    assert "private-secret" not in str(error.value)


def test_block_count_and_total_text_have_separate_bounds():
    block = {"text": "x", "source_ids": [1]}
    assert len(parse_grounded_reply(json.dumps({"blocks": [block] * 32}), SOURCES)["blocks"]) == 32
    for blocks in ([block] * 33, [{"text": "x" * 8192, "source_ids": []}] * 5):
        with pytest.raises(GroundedReplyProtocolError):
            parse_grounded_reply(json.dumps({"blocks": blocks}), SOURCES)


def test_markdown_arrays_tables_and_code_preserve_data_and_expose_selected_citations():
    text = "数组：[1]\n\n| 数据 | 含义 |\n| --- | --- |\n| [2] | 容器 |\n\n```python\na = [1]\na[2]\n```"
    rendered = render_grounded_reply(parse_grounded_reply(draft(text, [2]), SOURCES))
    assert rendered == text + "\n\n[2]"
    from backend.app.agent import validate_citations
    assert validate_citations(rendered, SOURCES) == ([{"id": 2}], True)
    normalized = parse_grounded_reply(draft("| 事实 | 来源 |\n| --- | --- |\n| 事实 | [1] |", []), SOURCES)
    assert normalized["blocks"][0]["source_ids"] == [1]
    assert "[1]" not in normalized["blocks"][0]["text"]


@pytest.mark.parametrize("legacy", ["栈后进先出[1]。", "你好！", "没有找到教材依据。", "```python\na = [1]\n```"])
def test_clearly_legacy_text_requires_callers_existing_gates(legacy):
    assert parse_grounded_reply(legacy, SOURCES) is None


def install_stream(provider, drafts):
    calls, closed = [], []

    async def stream(messages, **kwargs):
        index = len(calls)
        calls.append(messages)
        try:
            text = drafts[min(index, len(drafts) - 1)]
            # Split keys and content as an actual async provider stream does.
            for start in range(0, len(text), 9):
                yield {"text": text[start:start + 9], "model": "fake-test-model"}
            yield {"usage": {"total_tokens": 7}}
        finally:
            closed.append(index)

    provider.stream = stream
    return calls, closed


@pytest.mark.parametrize("mode", ["explain", "solve", "socratic"])
@pytest.mark.parametrize("bad", [draft(source_ids=[99]), draft("无依据的事实。", []), '{"blocks":[]}', "未引用的教材事实。"])
def test_temp_asgi_rejected_drafts_emit_zero_tokens_and_save_nothing(setup, mode, bad):
    client, app, provider = setup
    register(client)
    calls, closed = install_stream(provider, [bad])
    question = "直接解释栈" if mode == "socratic" else "解释栈"
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": question}))
    assert result[-1][0] == "error"
    assert not any(event in {"token", "citations", "done"} for event, _ in result)
    assert len(calls) == 2 and closed == [0, 1]
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


@pytest.mark.parametrize("mode", ["explain", "solve", "socratic"])
def test_temp_asgi_protocol_retry_then_review_and_emit_only_final_rendered_body(setup, mode):
    client, app, provider = setup
    register(client)
    calls, closed = install_stream(provider, [draft(source_ids=[99]), draft()])
    question = "直接解释栈" if mode == "socratic" else "解释栈"
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": question}))
    assert result[-1][0] == "done"
    assert len(calls) == 2 and closed == [0, 1]
    assert "引用编号只放source_ids数组" in calls[1][0]["content"]
    assert "正文和表格单元格中都不得手写[n]" in calls[1][0]["content"]
    assert "不要包含C0控制字符或孤立UTF-16代理项" in calls[1][0]["content"]
    token_text = "".join(item["text"] for event, item in result if event == "token")
    assert token_text == "栈遵循后进先出。 [1]"
    assert "source_ids" not in token_text and "[99]" not in token_text
    review_messages = next(messages for messages in reversed(provider.histories)
                           if messages[0]["content"].startswith("你是独立事实审查器"))
    review = json.loads(review_messages[1]["content"])
    assert review["grounded_blocks"] == [{"text": "栈遵循后进先出。", "source_ids": [1]}]
    assert review["draft"] == "栈遵循后进先出。"
    assert "不要把服务器展示的[n]标记归因为模型手写" in review_messages[0]["content"]
    review_index = next(i for i, (event, item) in enumerate(result)
                        if event == "trace" and item["node"] == "fact_review" and item["status"] == "completed")
    assert all(i > review_index for i, (event, _) in enumerate(result) if event == "token")
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"] == token_text


def test_temp_asgi_normalizes_provider_inline_citation_before_review_and_save(setup):
    client, app, provider = setup
    register(client)
    calls, closed = install_stream(provider, [draft("栈遵循后进先出[1]。", [])])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": "explain", "message": "解释栈"}))
    assert result[-1][0] == "done"
    assert len(calls) == 1 and closed == [0]
    token_text = "".join(item["text"] for event, item in result if event == "token")
    assert token_text == "栈遵循后进先出。 [1]"
    review_messages = next(messages for messages in reversed(provider.histories)
                           if messages[0]["content"].startswith("你是独立事实审查器"))
    review = json.loads(review_messages[1]["content"])
    assert review["grounded_blocks"] == [{"text": "栈遵循后进先出。", "source_ids": [1]}]
    assert review["draft"] == "栈遵循后进先出。"
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"] == token_text


def test_temp_asgi_does_not_let_review_misread_server_source_metadata_as_student_visible_body(setup):
    client, app, provider = setup
    register(client)
    calls, closed = install_stream(provider, [draft("栈遵循后进先出[1]。", [])])
    original_complete = provider.complete
    reviews = []

    async def complete(messages, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            reviews.append(json.loads(messages[1]["content"]))
            return {"content": json.dumps({"approved": False, "issues": [
                {"kind": "condition", "detail": "正文包含手写引用编号如[1]，违反用户‘不要在正文里手写引用编号’要求。"}
            ]})}
        return await original_complete(messages, **kwargs)

    provider.complete = complete
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": "explain", "message": "解释栈"}))
    assert result[-1][0] == "done"
    assert len(calls) == 1 and closed == [0] and len(reviews) == 1
    assert reviews[0]["draft"] == "栈遵循后进先出。"
    assert reviews[0]["grounded_blocks"] == [{"text": "栈遵循后进先出。", "source_ids": [1]}]
    token_text = "".join(item["text"] for event, item in result if event == "token")
    assert token_text == "栈遵循后进先出。 [1]"
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"] == token_text


@pytest.mark.parametrize("mode", ["explain", "solve"])
def test_grounded_teaching_prompt_does_not_conflict_with_source_id_protocol(setup, mode):
    client, _, provider = setup
    register(client)
    calls, _ = install_stream(provider, [draft()])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": "解释栈"}))
    assert result[-1][0] == "done"
    system_prompt = calls[0][0]["content"]
    assert "本轮采用证据段落JSON" in system_prompt
    assert "在source_ids中选择本轮确实支持该段的证据ID" in system_prompt
    assert "不要在text中写[n]" in system_prompt
    assert "引用必须写成[1]、[2]等" not in system_prompt
    assert "本轮交付检查：教材和图中事实逐段绑定到支持它的source_ids" in system_prompt
    assert "用户明确指定的教材例子、输入或使用情境必须按指定内容展开至少一个实际步骤" in system_prompt


@pytest.mark.parametrize("mode", ["explain", "solve", "socratic"])
def test_temp_asgi_valid_selected_source_is_still_rejectable_by_independent_review(setup, mode):
    client, app, provider = setup
    register(client)
    calls, closed = install_stream(provider, [draft("栈遵循先进先出。")])
    original_complete = provider.complete
    reviews = []

    async def complete(messages, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            reviews.append(messages)
            return {"content": json.dumps({"approved": False, "issues": [{"kind": "source_support", "detail": "教材实际说后进先出，所选来源不支持先进先出。"}]}), "usage": {"total_tokens": 5}}
        return await original_complete(messages, **kwargs)

    provider.complete = complete
    question = "直接解释栈" if mode == "socratic" else "解释栈"
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": question}))
    assert result[-1][0] == "error" and len(reviews) == 2
    assert len(calls) == 2 and closed == [0, 1]
    assert not any(event in {"token", "done"} for event, _ in result)
    assert all("chosen不等于supported" in messages[0]["content"] for messages in reviews)
    assert json.loads(reviews[0][1]["content"])["draft"] == "栈遵循先进先出。"
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


def test_temp_asgi_legacy_failure_also_buffers_until_existing_review_passes(setup):
    client, app, provider = setup
    register(client)
    calls, _ = install_stream(provider, ["教材宣称先进先出，但缺少教材依据。"])
    original_complete = provider.complete

    async def complete(messages, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            return {"content": json.dumps({"approved": False, "issues": [{"kind": "source_support", "detail": "教材不支持。"}]})}
        return await original_complete(messages, **kwargs)

    provider.complete = complete
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": "solve", "message": "解释栈"}))
    assert result[-1][0] == "error" and len(calls) == 2
    assert not any(event == "token" for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


@pytest.mark.parametrize("question,text", [("你好", "你好！"),
                                         ("解释栈", "没有找到充分教材依据，请提供更多资料。")])
def test_temp_asgi_empty_sources_honest_missing_or_greeting_still_require_review(setup, question, text):
    client, app, provider = setup
    register(client)
    calls, _ = install_stream(provider, [draft(text, [])])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": question}))
    assert result[-1][0] == "done" and len(calls) == 1
    assert [item["text"] for event, item in result if event == "token"] == [text]
    assert next(item for event, item in result if event == "citations")["items"] == []
    reviews = [messages for messages in provider.histories if messages[0]["content"].startswith("你是独立事实审查器")]
    assert len(reviews) == 1
    assert json.loads(reviews[0][1]["content"])["grounded_blocks"] == [{"text": text, "source_ids": []}]
    assert app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"] == text
