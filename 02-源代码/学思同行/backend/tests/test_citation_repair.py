import asyncio
import json

import pytest

from backend.app.agent import validate_citations
from backend.app.citations import canonicalize_citations, rewrite_history_references
from backend.app.provider import ProviderError
from backend.tests.test_api import setup, register, events


def test_fullwidth_citations_are_canonical_and_code_arrays_are_untouched():
    text = "正文【１】与［2］，`array[99]`。\n```python\narr[12]\n```"
    canonical = canonicalize_citations(text)
    assert canonical == "正文[1]与[2]，`array[99]`。\n```python\narr[12]\n```"
    sources = [{"id": 1}, {"id": 2}]
    assert validate_citations(text, sources) == (sources, True)
    with pytest.raises(ProviderError):
        validate_citations("不存在的来源【９９】", sources)


def test_history_only_rebinds_surviving_sources_and_never_injects_internal_placeholders():
    original = "已有定义【１】（物理页178），删除来源[2]。旧错误【历史引用10，须使用本轮来源重新绑定】。`arr[3]`"
    assert rewrite_history_references(original, {1: 8}) == "已有定义[8]，删除来源。旧错误。`arr[3]`"
    assert "[" not in rewrite_history_references("定义[1]物理页178")


def test_single_element_table_arrays_are_data_but_source_columns_and_sentence_refs_rebind():
    original = "| 步骤 | 访问 | 页框 | 来源 |\n|---|---|---|---|\n| 1 | 1 | [1] | [1] |\n\n驻留：[1]，定义【１】。frames=[1]，arr[99]。"
    actual = rewrite_history_references(original, {1: 2})
    assert "| 1 | 1 | [1] | [2] |" in actual
    assert "驻留：[1]，定义[2]。frames=[1]，arr[99]。" in actual
    used, grounded = validate_citations(actual, [{"id": 2}])
    assert used == [{"id": 2}] and grounded
    assert validate_citations("| 页框 |\n|---|\n| [99] |\n序列：[77]，arr[66]", []) == ([], False)


def test_missing_citations_receive_one_controlled_retry_and_canonical_final_text(setup):
    client, app, provider = setup
    register(client)
    calls = []

    async def no_tools(messages, **kwargs):
        return {"content": "DONE", "tool_calls": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}

    async def drafts(messages, **kwargs):
        calls.append(messages)
        yield {"text": "教材指出栈是后进先出结构。" if len(calls) == 1 else "栈是后进先出的线性结构【１】。"}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}

    provider.complete, provider.stream = no_tools, drafts
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert result[-1][0] == "done" and len(calls) == 2
    assert sum(event == "answer_reset" for event, _ in result) == 1
    assert result[-1][1]["final_text"] == "栈是后进先出的线性结构[1]。"
    assert result[-1][1]["usage"]["total_tokens"] == 28  # Two teaching requests; sufficient evidence skips planner.
    assert any(event == "trace" and payload["node"] == "teaching_route" for event, payload in result)
    saved = app.state.db.one("SELECT content FROM messages WHERE role='assistant'")
    assert saved["content"] == result[-1][1]["final_text"]
    assert any(event == "trace" and payload["node"] == "initial_retrieval" for event, payload in result)


def test_internal_placeholder_second_draft_is_refused_and_never_persisted(setup):
    client, app, provider = setup
    register(client)
    calls = []

    async def drafts(messages, **kwargs):
        calls.append(1)
        yield {"text": "教材内容【历史引用1，须使用本轮来源重新绑定】"}

    provider.stream = drafts
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert result[-1][0] == "error" and len(calls) == 2
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


def test_older_message_sources_are_reauthorized_and_remapped_after_uncited_latest_message(setup):
    client, app, provider = setup
    user = register(client)
    chunks = app.state.db.all("SELECT c.*,d.title,d.source_type FROM chunks c JOIN documents d ON d.id=c.document_id ORDER BY c.rowid")
    stack = next(chunk for chunk in chunks if chunk["section"] == "栈")
    queue = next(chunk for chunk in chunks if chunk["section"] == "队列")

    def prior(chunk, old_id):
        return {"id": old_id, "document_id": chunk["document_id"], "chunk_id": chunk["id"], "page": chunk["page"], "excerpt": chunk["text"][:700], "source_type": chunk["source_type"]}

    history = [
        {"role": "user", "content": "比较栈和队列"},
        {"role": "assistant", "content": "栈定义【10】（逻辑段1），已失效出处[99]。", "citations": [prior(stack, 10)]},
        {"role": "assistant", "content": "队列定义[20]。", "citations": [prior(queue, 20)]},
        {"role": "assistant", "content": "好的，我们继续。", "citations": []},
        {"role": "user", "content": "继续解释之前的栈定义"},
    ]
    planner_histories, teaching_histories = [], []

    async def no_initial_matches(*args, **kwargs):
        return {"items": [], "method": "BM25", "notice": "本轮关键词无新命中"}

    async def no_tools(messages, **kwargs):
        planner_histories.append(messages)
        if messages[0].get("content", "").startswith("你是独立事实审查器"):
            return {"content": '{"approved":true,"issues":[]}'}
        return {"content": "DONE", "tool_calls": []}

    async def teaching(messages, **kwargs):
        teaching_histories.append(messages)
        encoded = messages[0]["content"].split("可引用证据（位置说明已由服务器标注）：\n", 1)[1].split("\n实际工具结果：", 1)[0]
        citations = json.loads(encoded)
        current = next(citation["id"] for citation in citations if citation["chunk_id"] == stack["id"])
        assert current == 2  # Newest queue evidence becomes 1; old stack citation 10 must become 2.
        yield {"text": f"栈具有后进先出的特征[{current}]。"}

    async def emit(*args):
        pass

    app.state.retriever.search = no_initial_matches
    provider.complete, provider.stream = no_tools, teaching
    course = app.state.db.one("SELECT * FROM courses WHERE id='data-structures'")
    result = asyncio.run(app.state.agent.run(course, user["id"], history, "explain", emit))
    assert result["grounded"] and result["citations"][0]["chunk_id"] == stack["id"]
    assert result["citations"][0]["id"] == 2
    planner_assistants = [message["content"] for message in planner_histories[0] if message["role"] == "assistant"]
    assert planner_assistants[0] == "栈定义，已失效出处。"
    teaching_assistants = [message["content"] for message in teaching_histories[0] if message["role"] == "assistant"]
    assert teaching_assistants[0] == "栈定义[2]，已失效出处。"
    assert teaching_assistants[1] == "队列定义[1]。"
    assert all("历史引用" not in content and "[99]" not in content for content in teaching_assistants)


def test_greeting_skips_initial_retrieval_and_can_finish_without_citations(setup):
    client, app, provider = setup
    register(client)

    async def must_not_search(*args, **kwargs):
        raise AssertionError("Simple greeting must not trigger paid or lexical retrieval")

    async def no_tools(*args, **kwargs):
        return {"content": "DONE", "tool_calls": []}

    async def greeting(*args, **kwargs):
        yield {"text": "你好，可以告诉我你想学习的内容。"}

    app.state.retriever.search = must_not_search
    provider.complete, provider.stream = no_tools, greeting
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "你好！"}))
    assert result[-1][0] == "done" and result[-1][1]["grounded"] is False
    assert not any(event == "trace" and payload["node"] == "initial_retrieval" for event, payload in result)
