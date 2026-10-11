import asyncio
import json

import pytest

from backend.app.db import dumps
from backend.app.fact_review import (HASH_PREMISE_REVIEW_PROMPT, HASH_PREMISE_TEACHING_PROMPT,
                                     EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT, EVIDENCE_ADMISSIBILITY_TEACHING_PROMPT,
                                     IMAGE_RECOGNITION_REVIEW_PROMPT, REVIEW_PROMPT,
                                     SOURCE_CORRECTION_REVIEW_PROMPT, SOURCE_CORRECTION_TEACHING_PROMPT,
                                     SOURCE_IMPLEMENTATION_REVIEW_PROMPT, SOURCE_IMPLEMENTATION_TEACHING_PROMPT,
                                     parse_review, should_review, validate_computation_assumptions,
                                     validate_hash_bucket_average_claims, validate_image_claim_uncertainty,
                                     validate_hash_slot_constant_time_claims,
                                     is_source_correction_challenge,
                                     is_explicit_hash_premise_inference,
                                     validate_hash_premise_inference_scope,
                                     requests_hash_bucket_average_details,
                                     validate_explicit_key_value_roles, validate_load_factor_source_claims,
                                     validate_socratic_question_count)
from backend.app.provider import ProviderError
from backend.app.socratic_reply import SOCRATIC_REPLY_PREFIX
from backend.tests.test_api import setup, register, events


@pytest.mark.parametrize("content", [
    '{}', '{"approved":1,"issues":[]}', '{"approved":false,"issues":[]}',
    '{"approved":true,"issues":[{"kind":"logic","detail":"矛盾"}]}',
    '{"approved":true,"issues":[],"analysis":"hidden"}',
    '{"approved":false,"issues":[{"kind":"unknown","detail":"错误"}]}',
    '{"approved":false,"issues":[{"kind":"logic","detail":"<think>hidden"}]}',
])
def test_invalid_review_cannot_approve_a_draft(content):
    with pytest.raises(ProviderError):
        parse_review(content)


def test_uniform_successful_hash_bucket_average_rejects_n_over_two():
    question = "同一冲突桶中4个已存键被成功查询的概率相同，平均比较次数是多少？"
    with pytest.raises(ProviderError, match=r"\(N\+1\)/2"):
        validate_hash_bucket_average_claims(question, "平均比较次数为N/2，此处结果为2.5。")


@pytest.mark.parametrize("answer", [
    "平均比较次数为(N+1)/2；当N=4时是2.5。",
    "平均值并非N/2，而是(N+1)/2；本题结果是2.5。",
    "不同键的查询概率不相同，不能套用等概率成功查询公式。",
])
def test_hash_average_guard_accepts_correct_or_out_of_scope_statements(answer):
    question = "同一冲突桶中4个已存键被成功查询的概率相同，平均比较次数是多少？"
    validate_hash_bucket_average_claims(question, answer)


def test_hash_average_guard_does_not_block_unsuccessful_search_mean():
    validate_hash_bucket_average_claims(
        "同一冲突桶中查找一个不存在的键，平均比较次数是多少？",
        "在均匀插入位置的假设下可能约为N/2。")


def test_uncited_hash_inference_is_narrow_and_still_reviewed():
    question = "散列函数对任意键都恒返回8，扩容十倍能保证把冲突键分散吗？"
    assert is_explicit_hash_premise_inference(
        question, "按题设推导，键仍映射到同一桶；扩容不能保证把它们分散。", [])
    assert not is_explicit_hash_premise_inference(
        question, "按题设推导，键仍在同一桶。教材说明扩容无效。", [])
    assert not is_explicit_hash_premise_inference(
        question, "按题设推导，键仍在同一桶。", [{"id": 3}])
    assert not is_explicit_hash_premise_inference(
        "扩容后查找通常是常数时间。", "按题设推导，键仍在同一桶。", [])


def test_fixed_hash_inference_must_be_uncited_and_avoid_invented_bucket_arithmetic():
    question = "散列函数对任意键都恒返回8，表扩容十倍能保证把冲突键分散吗？"
    clean = {"blocks": [{"text": "按题设推导，相同散列输出仍落入同一桶；扩容不能保证把这些键分散。", "source_ids": []}]}
    validate_hash_premise_inference_scope(question, "按题设推导，相同散列输出仍落入同一桶；扩容不能保证把这些键分散。", [], clean)
    cited = {"blocks": [{"text": "按题设推导，相同散列输出仍落入同一桶；扩容不能保证把这些键分散。", "source_ids": [1]}]}
    with pytest.raises(ProviderError, match="空source_ids"):
        validate_hash_premise_inference_scope(question, "按题设推导，相同散列输出仍落入同一桶；扩容不能保证把这些键分散。", [{"id": 1}], cited)
    invented_arithmetic = "按题设推导，8%10=8，但扩容不能保证把这些键分散。"
    valid_but_cited_block = {"blocks": [{"text": invented_arithmetic, "source_ids": []}]}
    with pytest.raises(ProviderError, match="未给出具体容量"):
        validate_hash_premise_inference_scope(question, invented_arithmetic, [], valid_but_cited_block)
    unsupported_rule = "按题设推导，相同散列输出仍落入同一桶；桶号由散列值 % 新容量决定，因此扩容不能保证分散。"
    unsupported_rule_block = {"blocks": [{"text": unsupported_rule, "source_ids": []}]}
    with pytest.raises(ProviderError, match="未给出具体容量"):
        validate_hash_premise_inference_scope(question, unsupported_rule, [], unsupported_rule_block)


def test_fixed_hash_scope_guard_checks_inline_markdown_code_for_unsupported_rule():
    question = "散列函数对任意键都恒返回8，扩容能保证分散吗？"
    answer = "按题设推导，桶号通常由 `哈希值 % 新容量` 决定，但所有键仍然相同。"
    block = {"blocks": [{"text": answer, "source_ids": []}]}
    with pytest.raises(ProviderError, match="未给出具体容量"):
        validate_hash_premise_inference_scope(question, answer, [], block)


def test_hash_average_guidance_is_injected_only_for_explicit_average_questions():
    assert not requests_hash_bucket_average_details(
        "改成槽内数组比较键，所以dab查得pat、bad查得evil。那查询永远O(1)吗？")
    assert requests_hash_bucket_average_details(
        "同一个散列冲突桶有4对不同键；成功查询概率相同，平均要比较几个键？")


def test_hash_slot_constant_time_needs_a_directly_supporting_excerpt():
    claim = "定位槽位是O(1)，冲突桶内仍需线性扫描。"
    book_excerpt = [{"id": 1, "excerpt": "若散列表的格子含有数组，因为要在线性查找，所以最坏为O(N)；设计应尽量减少冲突以便查找O(1)。"}]
    with pytest.raises(ProviderError, match="哈希计算或槽位访问本身"):
        validate_hash_slot_constant_time_claims(claim, book_excerpt)
    directly_supported = [{"id": 2, "excerpt": "定位槽位的操作是O(1)，哈希函数计算也是O(1)。"}]
    validate_hash_slot_constant_time_claims(claim, directly_supported)


def test_load_factor_claim_requires_a_cited_excerpt_that_defines_it():
    with pytest.raises(ProviderError, match="负载因子"):
        validate_load_factor_source_claims(
            "平均查找还取决于负载因子。[1]",
            [{"id": 1, "source_type": "uploaded_pdf", "excerpt": "减少冲突有助于保持O(1)查找。"}])
    validate_load_factor_source_claims(
        "负载因子是数据量与格子数的比值。[2]",
        [{"id": 2, "source_type": "uploaded_pdf", "excerpt": "数据量与格子数的比值称为负载因子。"}])


def test_image_observation_must_include_uncertainty_and_original_page_check():
    image = [{"id": 1, "evidence_kind": "image", "document_id": "book", "page": 79}]
    with pytest.raises(ProviderError, match="视觉模型识别"):
        validate_image_claim_uncertainty("图中显示第8槽包含两个键值对。[1]", image)
    with pytest.raises(ProviderError, match="视觉模型识别"):
        validate_image_claim_uncertainty(
            '你的理解有误。根据教材第79页的图示与说明，键 "bad" 和 "dab" 计算出的哈希值都是8。[1]', image)
    with pytest.raises(ProviderError, match="对照原PDF页图"):
        validate_image_claim_uncertainty(
            "按视觉识别结果，第8槽似乎包含两个键值对，但可能误读。[1]", image)
    validate_image_claim_uncertainty(
        "根据教材第79页的图示与说明，键 bad 和 dab 计算出的哈希值都是8。"
        "页图是视觉模型识别结果，可能误读，请对照原PDF页图核验。[1]", image)
    validate_image_claim_uncertainty(
        "按视觉识别结果，第8槽似乎包含两个键值对，但可能误读，请对照原PDF页图。[1]", image)
    validate_image_claim_uncertainty(
        "模型识别结果可能误读，仍需结合原页核验。[1]", image)
    validate_image_claim_uncertainty(
        "图中显示第8槽包含两个键值对。[1]",
        [{"id": 1, "source_type": "uploaded_pdf", "page": 79}])


def test_hash_prompt_distinguishes_explicit_premise_inference_from_textbook_fact():
    for prompt in (HASH_PREMISE_TEACHING_PROMPT, HASH_PREMISE_REVIEW_PROMPT, REVIEW_PROMPT):
        assert "按题设" in prompt
        assert "散列函数" in prompt
        assert "不需要教材" in prompt or "无需教材" in prompt or "不要说成教材原文" in prompt or "不得仅因教材未讨论" in prompt
    assert "容量取模可能改变桶号" in HASH_PREMISE_TEACHING_PROMPT
    assert "把这项推论单独成段并使用空source_ids" in HASH_PREMISE_TEACHING_PROMPT
    assert "必须是独立JSON block" in HASH_PREMISE_TEACHING_PROMPT
    assert "只问这个固定哈希假设的后果，不要补充教材背景" in HASH_PREMISE_TEACHING_PROMPT
    assert "空source_ids block就是完整回答" in HASH_PREMISE_REVIEW_PROMPT
    assert "单独一个按题设推导的空source_ids block就是完整回答" in HASH_PREMISE_REVIEW_PROMPT
    assert "若引用摘录未包含负载因子/load factor，不要自行引入它" in HASH_PREMISE_TEACHING_PROMPT
    assert "常见映射语义" in HASH_PREMISE_REVIEW_PROMPT
    assert "任意键总返回同一个常数" in HASH_PREMISE_REVIEW_PROMPT
    assert "一般映射语义（非本书明文规定）" in HASH_PREMISE_TEACHING_PROMPT
    assert "这不等于扩容只有浪费空间" in HASH_PREMISE_TEACHING_PROMPT
    assert "替换旧值而不是新增重复键" in HASH_PREMISE_REVIEW_PROMPT
    assert "不要把散列值8乘以容量增长倍数当作新桶号" in HASH_PREMISE_TEACHING_PROMPT
    assert "没有新容量和映射规则时，精确桶号未知" in HASH_PREMISE_REVIEW_PROMPT
    assert "不要写‘散列值 % 新容量’的符号式" in HASH_PREMISE_TEACHING_PROMPT
    assert "不要用‘散列值 % 新容量’的符号式填补缺失规则" in HASH_PREMISE_REVIEW_PROMPT
    assert "若本轮可读文字明确写该教材的冲突桶存放在数组中" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    assert "不要无来源地声称计算哈希值或定位槽位本身为O(1)" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "不支持未给前提的‘一般情况/平均复杂度’定论" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    assert "历史助教回答不能自证" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "若来源不支持，明确撤回或限定该断言" in SOURCE_IMPLEMENTATION_TEACHING_PROMPT
    assert "不把历史助教文本当证据" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT
    assert "应认可明确撤回/限定" in SOURCE_IMPLEMENTATION_REVIEW_PROMPT


def test_source_correction_challenge_does_not_replay_stale_tutor_history():
    assert is_source_correction_challenge("你上条说定位槽位是O(1)，本书引用真的支持吗？如果没有请纠正。")
    assert is_source_correction_challenge("你上条说定位槽位是O(1)，第81页是否支持？请明确纠正。")
    assert is_source_correction_challenge("上一轮说法不对吗？这段原文能证明它吗？")
    assert is_source_correction_challenge("上一轮回答的复杂度有教材依据吗？请重新核对来源。")
    assert is_source_correction_challenge("上一轮你说同时有三个保存记录，原文是否支持这个计数？")
    assert is_source_correction_challenge("刚才助教回答有三个保存记录，教材有依据吗？")
    assert not is_source_correction_challenge("请依据教材解释冲突桶的最坏查找开销。")
    assert not is_source_correction_challenge("我上条说数组查找是O(1)，这个说法对吗？")


def test_source_challenge_rebuilds_context_without_prior_claim_or_citations(setup):
    client, app, provider = setup
    register(client)
    first = events(client.post("/api/chat", json={
        "course_id": "data-structures", "message": "请解释栈", "mode": "explain"
    }))
    session_id = first[-1][1]["session_id"]
    assistant = app.state.db.one(
        "SELECT id FROM messages WHERE session_id=? AND role='assistant' ORDER BY rowid DESC LIMIT 1",
        (session_id,),
    )
    marker = "STALE-ANSWER-9F2A: 栈是先进先出"
    app.state.db.execute("UPDATE messages SET content=? WHERE id=?", (marker, assistant["id"]))
    previous_completion_calls = len(provider.histories)

    challenged = events(client.post("/api/chat", json={
        "course_id": "data-structures", "session_id": session_id,
        "message": "你上条说栈是先进先出，本书引用真的支持吗？请重新核对并纠正。",
        "mode": "explain",
    }))
    assert challenged[-1][0] == "done"
    generation = provider.stream_histories[-1]
    assert SOURCE_CORRECTION_TEACHING_PROMPT in generation[0]["content"]
    assert "静水工具moment为null" not in generation[0]["content"]
    current_question = "你上条说栈是先进先出，本书引用真的支持吗？请重新核对并纠正。"
    assert sum(message == {"role": "user", "content": current_question}
               for message in generation) == 1
    review = next(messages for messages in provider.histories
                  if messages[0].get("content", "").startswith("你是独立事实审查器"))
    assert review[0]["content"].startswith(SOURCE_CORRECTION_REVIEW_PROMPT)
    assert "力矩必须有题给" not in review[0]["content"]
    all_prompt_content = "\n".join(message.get("content", "") for message in generation + review)
    assert marker not in all_prompt_content
    assert all(messages[0].get("content", "").startswith("你是独立事实审查器")
               for messages in provider.histories[previous_completion_calls:])
    assert any(payload.get("node") == "planner" and payload.get("label") == "仅用本轮新检索核对来源"
               for kind, payload in challenged if kind == "trace")
    assert not any(payload.get("node") == "search_materials" for kind, payload in challenged if kind == "trace")
    assert "隔离待核历史回答" == next(payload["label"] for kind, payload in challenged
                                     if kind == "trace" and payload.get("node") == "history_context")


def test_image_review_treats_qualified_local_observations_as_evidence():
    assert "不表示image对其可见局部毫无支持" in IMAGE_RECOGNITION_REVIEW_PROMPT
    assert "不表示image对其可见局部毫无支持" in REVIEW_PROMPT
    assert "应接受该image对局部观察的支持" in IMAGE_RECOGNITION_REVIEW_PROMPT
    assert "角色定义引用文字，具体配对引用image" in EVIDENCE_ADMISSIBILITY_TEACHING_PROMPT
    assert "局部观察由image支持" in EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT
    assert "image excerpt若没有明确列出某个字段/配对，不得把题干或历史中的该字段改写成视觉观察" in EVIDENCE_ADMISSIBILITY_TEACHING_PROMPT
    assert "应接受这种诚实回退" in EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT


def test_explicit_pair_roles_override_conflicting_image_labels():
    citations = [
        {"id": 1, "evidence_kind": "uploaded_pdf", "page": 79,
         "excerpt": "每个子数组含两个元素，第一个是被检索的词，后一个是其相应的同义词。"},
        {"id": 2, "evidence_kind": "image", "page": 79,
         "excerpt": '图示包含 ["bad", "evil"] 和 ["dab", "pat"]。'}]
    with pytest.raises(ProviderError, match="不能把键和值都称为键"):
        validate_explicit_key_value_roles("图里一共有四个不同的键。[1][2]", citations)
    with pytest.raises(ProviderError, match="次项受教材明确角色定义为值"):
        validate_explicit_key_value_roles("evil是一个键，pat也作为键存储。[1][2]", citations)
    validate_explicit_key_value_roles("键`bad`对应值`evil`，键`dab`对应值`pat`。[1][2]", citations)


def install_review_protocol(provider, decisions):
    closed, reviews, teaching = [], [], []

    async def complete(messages, tools=None, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            reviews.append(json.loads(messages[1]["content"]))
            decision = decisions[min(len(reviews) - 1, len(decisions) - 1)]
            if isinstance(decision, Exception):
                raise decision
            return {"content": dumps(decision), "usage": {"total_tokens": 7 + len(reviews)}, "model": "fake-test-review-model"}
        if not any(message["role"] == "tool" for message in messages):
            return {"tool_calls": [{"id": "atomic-math", "name": "calculate", "arguments": '{"expression":"2+3"}'}], "usage": {"total_tokens": 10}}
        return {"tool_calls": [], "usage": {"total_tokens": 7}}

    async def stream(messages, **kwargs):
        teaching.append(messages)
        try:
            yield {"text": "2+3=6[1]。" if len(teaching) == 1 else "2+3=5[1]。", "model": "fake-test-teaching-model"}
            yield {"usage": {"total_tokens": 30}}
        finally:
            closed.append(True)

    provider.complete, provider.stream = complete, stream
    return closed, reviews, teaching


def install_socratic_drafts(provider, texts):
    """Adapt only teaching drafts, retaining the separate fact-review fake.

    The stream branch preserves the previous text protocol during integration;
    the complete branch returns the new two-field protocol without approving it.
    """
    previous_complete = provider.complete
    generated = []

    def next_draft(messages):
        text = texts[min(len(generated), len(texts) - 1)]
        generated.append(messages)
        return text if isinstance(text, dict) else {"feedback": "", "question": text}

    async def complete(messages, **kwargs):
        if messages[0]["content"].startswith(SOCRATIC_REPLY_PREFIX):
            return {"content": dumps(next_draft(messages)),
                    "usage": {"total_tokens": 30}, "model": "fake-test-model"}
        return await previous_complete(messages, **kwargs)

    async def stream(messages, **kwargs):
        value = next_draft(messages)
        yield {"text": "\n\n".join(part for part in (value["feedback"], value["question"]) if part), "model": "fake-test-model"}

    provider.complete, provider.stream = complete, stream
    return generated


def test_review_rejects_numeric_mismatch_repairs_once_counts_usage_and_closes_drafts(setup):
    client, app, provider = setup
    register(client)
    closed, reviews, teaching = install_review_protocol(provider, [
        {"approved": False, "issues": [{"kind": "numerical", "detail": "正文6与本次工具result=5不一致"}]},
        {"approved": True, "issues": []},
    ])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "核验2+3并给出计算结果", "mode": "solve"}))
    assert result[-1][0] == "done" and result[-1][1]["final_text"] == "2+3=5[1]。"
    assert result[-1][1]["usage"]["total_tokens"] == 10 + 7 + 30 + 8 + 30 + 9
    assert result[-1][1]["model"] == "fake-test-teaching-model"
    assert len(reviews) == len(teaching) == len(closed) == 2
    assert reviews[0]["question"] == "核验2+3并给出计算结果"
    assert reviews[0]["computations"][0]["arguments"] == {"expression": "2+3"}
    assert reviews[0]["computations"][0]["result"]["result"] == 5
    assert reviews[0]["previous_review_issues"] == []
    assert reviews[1]["previous_review_issues"] == [{"kind": "numerical", "detail": "正文6与本次工具result=5不一致"}]
    assert sum(event == "answer_reset" for event, _ in result) == 1
    assert "result=5" in teaching[1][0]["content"]
    assert any(event == "trace" and payload["node"] == "fact_review" and payload["status"] == "failed" for event, payload in result)
    saved = app.state.db.one("SELECT content FROM messages WHERE role='assistant'")
    assert saved["content"] == "2+3=5[1]。"


@pytest.mark.parametrize("kind", ["condition", "logic", "source_support", "coverage", "unsupported_inference"])
def test_unrepaired_review_issue_is_an_error_never_a_saved_success(setup, kind):
    client, app, provider = setup
    register(client)
    closed, reviews, _ = install_review_protocol(provider, [{"approved": False, "issues": [{"kind": kind, "detail": "存在具体未覆盖条件或不支持的结论"}]}])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "计算2+3并解释适用条件", "mode": "solve"}))
    assert result[-1][0] == "error" and "局部事实复核" in result[-1][1]["message"]
    assert len(reviews) == len(closed) == 2
    assert not any(event == "done" for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


@pytest.mark.parametrize("failure", [ProviderError("模型事实复核服务暂不可用"), {}, {"approved": 1, "issues": []}])
def test_review_service_failure_is_not_implicit_approval_or_an_extra_paid_draft(setup, failure):
    client, app, provider = setup
    register(client)
    closed, reviews, teaching = install_review_protocol(provider, [failure])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "计算2+3", "mode": "solve"}))
    assert result[-1][0] == "error" and len(closed) == len(reviews) == len(teaching) == 1
    assert not any(event in {"answer_reset", "done"} for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


def test_cancellation_during_fact_review_keeps_stream_closed(setup):
    client, app, provider = setup
    user = register(client)
    closed, _, _ = install_review_protocol(provider, [{"approved": True, "issues": []}])
    original_complete = provider.complete
    cancelled = []

    async def scenario():
        reviewing = asyncio.Event()

        async def complete(messages, **kwargs):
            if messages[0]["content"].startswith("你是独立事实审查器"):
                reviewing.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)
            return await original_complete(messages, **kwargs)

        async def emit(*args):
            pass

        provider.complete = complete
        course = app.state.db.one("SELECT * FROM courses WHERE id='data-structures'")
        task = asyncio.create_task(app.state.agent.run(course, user["id"], [{"role": "user", "content": "计算2+3"}], "solve", emit))
        await asyncio.wait_for(reviewing.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled == [True] and closed == [True]

    asyncio.run(scenario())


@pytest.mark.parametrize("question_text", ["你会如何描述压栈与出栈的先后关系？", "我们先从基础开始：**你会如何描述压栈与出栈的先后关系？**", "关键问题：__你会如何描述压栈与出栈的先后关系？__"])
def test_socratic_question_only_has_not_applicable_grounding_then_sourced_definition(setup, question_text):
    client, app, provider = setup
    register(client)
    original_stream = provider.stream
    original_complete = provider.complete
    install_socratic_drafts(provider, [question_text])
    first = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "请先只问一个关键问题，不直接给出定义", "mode": "socratic"}))
    assert first[-1][0] == "done" and first[-1][1]["grounded"] is None
    assert first[-1][1]["final_text"].count("？") == 1
    assert next(payload for event, payload in first if event == "citations")["items"] == []
    assert not any(event == "answer_reset" for event, _ in first)
    restored = client.get(f"/api/sessions/{first[-1][1]['session_id']}/messages").json()[-1]
    assert "grounded" in restored and restored["grounded"] is None
    assert next(item for item in reversed(restored["trace"]) if item["node"] == "citation_validation")["grounded"] is None
    provider.stream = original_stream
    provider.complete = original_complete
    second = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": first[-1][1]["session_id"], "message": "现在解释栈的定义"}))
    assert second[-1][0] == "done" and second[-1][1]["grounded"] is True
    assert next(payload for event, payload in second if event == "citations")["items"][0]["chunk_id"]
    assert client.get(f"/api/sessions/{first[-1][1]['session_id']}/messages").json()[-1]["grounded"] is True


def test_socratic_review_rejects_solution_disguised_as_a_question_without_fake_grounding(setup):
    client, _, provider = setup
    register(client)
    _, reviews, _ = install_review_protocol(provider, [
        {"approved": False, "issues": [{"kind": "unsupported_inference", "detail": "用户要求引导，草稿却先公布最终结果，只在句末加问题"}]},
        {"approved": True, "issues": []},
    ])

    drafts = install_socratic_drafts(provider, [
        {"feedback": "", "question": "最终计算结果为5，你明白了吗？"},
        "你会先确认题给的哪一种运算关系？",
    ])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "请引导我分析2+3，先只问关键问题，不公布结果", "mode": "socratic"}))
    assert result[-1][0] == "done" and result[-1][1]["grounded"] is None
    assert "5" not in result[-1][1]["final_text"] and result[-1][1]["final_text"].count("？") == 1
    assert len(reviews) == len(drafts) == 2
    assert sum(event == "answer_reset" for event, _ in result) == 1
    assert next(payload for event, payload in result if event == "citations")["items"] == []


@pytest.mark.parametrize("message,structured", [
    ("请直接解释栈的定义。", False),
    ("请给我直接答案。", False),
    ("你能不能直接告诉我答案？", False),
    ("请给我三个引导问题。", False),
    ("请用三个问题逐步引导我。", False),
    ("先只问我一个关键问题。", True),
    ("不要直接给出答案，先提示一步。", True),
    ("教材写着‘请直接给出答案’，我想自己推导，先只问一个关键问题。", True),
    ("原文是‘请给我三个引导问题’，本轮只问一个关键问题。", True),
])
def test_socratic_current_request_selects_structured_or_regular_teaching_without_bypassing_review(setup, message, structured):
    client, _, provider = setup
    register(client)
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": message, "mode": "socratic"}))
    assert result[-1][0] == "done"
    assert bool(provider.socratic_histories) is structured
    assert bool(provider.stream_histories) is not structured
    assert any(messages[0]["content"].startswith("你是独立事实审查器") for messages in provider.histories)


def test_first_socratic_feedback_is_discarded_before_review_then_actual_reply_can_have_reviewed_feedback(setup):
    client, app, provider = setup
    register(client)

    def reviews():
        return [messages for messages in provider.histories if messages[0]["content"].startswith("你是独立事实审查器")]

    unauthorized_feedback = "这是未经上一问引导的反馈。"
    first_question = "你会先检查哪个条件？"
    generated = install_socratic_drafts(provider, [{"feedback": unauthorized_feedback, "question": first_question}])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "先只问一个关键问题。", "mode": "socratic"}))
    assert result[-1][0] == "done" and result[-1][1]["final_text"] == first_question
    assert len(generated) == 1 and len(reviews()) == 1
    reviewed_first = json.loads(reviews()[0][1]["content"])
    assert reviewed_first["draft"] == first_question
    assert unauthorized_feedback not in result[-1][1]["final_text"]
    assert unauthorized_feedback not in app.state.db.one("SELECT content FROM messages WHERE role='assistant'")["content"]
    session_id = next(payload["session_id"] for kind, payload in result if kind == "session")

    feedback, question = "你明确了要检查的条件。", "下一步你会核对哪个操作？"
    responding = install_socratic_drafts(provider, [{"feedback": feedback, "question": question}])
    second = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": session_id,
                     "message": "我先检查操作的顺序。", "mode": "socratic"}))
    assert second[-1][0] == "done" and second[-1][1]["final_text"] == feedback + "\n\n" + question
    assert len(responding) == 1 and len(reviews()) == 2
    saved = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [item["role"] for item in saved] == ["user", "assistant", "user", "assistant"]
    assert saved[-1]["content"] == second[-1][1]["final_text"]
    reviewed_reply = json.loads(reviews()[-1][1]["content"])
    assert reviewed_reply["draft"] == saved[-1]["content"]
    assert reviewed_reply["single_step_reply"] == {"feedback": feedback, "question": question}


def test_socratic_multi_question_draft_is_not_shown_before_bounded_repair(setup):
    client, _, provider = setup
    register(client)
    _, reviews, _ = install_review_protocol(provider, [{"approved": True, "issues": []}])
    bad = "当N=50谁更快？当N=150谁更快？临界点是多少？"
    good = "我们先比较一个给定规模：你会先核对哪两个步数？"

    drafts = install_socratic_drafts(provider, [bad, good])
    result = events(client.post('/api/chat', json={'course_id': 'data-structures',
        'message': '比较固定100步与N步，先只提示一个问题，不公布解答。', 'mode': 'socratic'}))
    assert result[-1][0] == 'done' and result[-1][1]['final_text'] == good
    assert ''.join(data['text'] for kind, data in result if kind == 'token') == good
    assert len(drafts) == 2 and len(reviews) == 1
    assert sum(kind == 'answer_reset' for kind, _ in result) == 1


def test_twice_overloaded_socratic_draft_saves_and_shows_no_assistant(setup):
    client, app, provider = setup
    register(client)
    install_review_protocol(provider, [{"approved": True, "issues": []}])
    drafts = install_socratic_drafts(provider, ['你先比较两个步数？然后再求临界点？'])
    result = events(client.post('/api/chat', json={'course_id': 'data-structures',
        'message': '先用一个问题引导我。', 'mode': 'socratic'}))
    assert result[-1][0] == 'error'
    assert not any(kind == 'token' for kind, _ in result)
    assert not app.state.db.one("SELECT id FROM messages WHERE role='assistant'")
    assert len(drafts) == 2


def test_explicit_multiple_guiding_questions_take_precedence_over_mode_default():
    validate_socratic_question_count('请给我三个引导问题。', '你先怎么选？为什么？下一步呢？', 'socratic')
    validate_socratic_question_count('直接比较两个情况。', '条件一是什么？条件二是什么？', 'explain')
    with pytest.raises(ProviderError):
        validate_socratic_question_count('先只提示一步。', '你先怎么选？然后比较什么？', 'socratic')


def test_one_question_mark_does_not_allow_the_observed_extra_reflection_task():
    with pytest.raises(ProviderError, match='追加'):
        validate_socratic_question_count(
            '比较固定100步与N步，提示我别直接给结论。',
            '请计算：当N取什么值时，N步算法会更快？并说明这个结果如何修正你的看法。', 'socratic')
    # A legitimate intermediate equation remains one task. No requirement to
    # first discover the existence of an equality is invented by this gate.
    validate_socratic_question_count('先提示一步。', '两个给定步数相等时，你会写出什么等式？', 'socratic')
    validate_socratic_question_count('请给我三个引导问题。', '比较什么并说明理由？下一步是什么？最后怎样检查？', 'socratic')


def test_legacy_history_without_structured_grounding_uses_real_citation_presence(setup):
    client, app, _ = setup
    register(client)
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    session_id = result[-1][1]["session_id"]
    saved = app.state.db.one("SELECT id FROM messages WHERE session_id=? AND role='assistant'", (session_id,))
    app.state.db.execute("UPDATE messages SET trace='[]' WHERE id=?", (saved["id"],))
    restored = client.get(f"/api/sessions/{session_id}/messages").json()
    assert "grounded" not in restored[0] and restored[-1]["grounded"] is True
    app.state.db.execute("UPDATE messages SET citations='[]' WHERE id=?", (saved["id"],))
    assert client.get(f"/api/sessions/{session_id}/messages").json()[-1]["grounded"] is False


def test_review_triggers_for_condition_and_multiple_questions_without_golden_answers():
    assert should_review("分别比较这两种机制并说明条件", "简短回答", [], "explain")
    assert should_review("解释机制", "这些条件是充要条件。", [], "explain")
    assert not should_review("你好", "你好", [], "explain")


@pytest.mark.parametrize("answer", [
    "若有100个域和10^5个对象，则10^8个表项，每项1 byte约100 MB。",
    "例如存在10**5条，合计10^8字节。",
])
def test_textbook_numbers_of_scale_need_an_actual_calculation_tool(answer):
    with pytest.raises(ProviderError, match="确定性工具核验"):
        validate_computation_assumptions(answer, [])
    # An unrelated course tool call is not an arithmetic check.
    with pytest.raises(ProviderError):
        validate_computation_assumptions(answer, [{"tool": "subnet_info", "arguments": {}, "result": {}}])
    validate_computation_assumptions(answer, [{"tool": "calculate", "arguments": {"expression": "100*10**5"}, "result": {"result": 10_000_000}}])


def test_hydrostatic_moment_claim_requires_a_supplied_and_computed_reference():
    result = {"tool": "hydrostatic_vertical_plate", "arguments": {"width": 2, "height": 3},
              "result": {"moment_about_supplied_reference": None}}
    with pytest.raises(ProviderError, match="参照点"):
        validate_computation_assumptions("合力是88290N，但放在1.5m处会低估底部铰点的力矩。", [result])
    with pytest.raises(ProviderError, match="参照点"):
        validate_computation_assumptions("若绕底部支座，误差会导致力矩更大。", [result])
    validate_computation_assumptions("合力作用深度是2m，没有给底部铰点，不能据此判断支座力矩。", [result])


@pytest.mark.parametrize('answer', [
    '压力中心由等效荷载的力矩守恒确定，而不是板面积的形心。',
    '将合力放在板形心不能保持实际压力分布的力矩，因此需采用压力分布的形心。',
])
def test_hydrostatic_qualitative_equivalence_does_not_invent_a_torque_reference(answer):
    validate_computation_assumptions(answer, [{'tool': 'hydrostatic_vertical_plate',
        'result': {'moment_about_supplied_reference': None}}])


def test_hydrostatic_unspecified_support_is_rejected_even_without_a_numeric_torque():
    with pytest.raises(ProviderError, match='参照点'):
        validate_computation_assumptions('底部有一个铰接支座，它提供反力。',
            [{'tool': 'hydrostatic_vertical_plate', 'result': {'moment_about_supplied_reference': None}}])


@pytest.mark.parametrize('answer', [
    '若置于1.5 m，则对底部或任意点的力矩小于实际分布力的总矩，导致分析错误。',
    '将合力放在面积形心时，对底部的力矩大于实际分布力的总矩。',
    '改用面积形心后，对任意参考点的扭矩都会更小。',
    '合力的力矩相对底部小于实际分布力的总矩。',
    '没有给参照点，但对底部的力矩小于实际分布力的总矩。',
])
def test_hydrostatic_bottom_or_arbitrary_reference_size_comparison_needs_verification(answer):
    with pytest.raises(ProviderError, match='参照点'):
        validate_computation_assumptions(answer, [{'tool': 'hydrostatic_vertical_plate',
            'result': {'moment_about_supplied_reference': None}}])


@pytest.mark.parametrize('answer', [
    '未给参照点，不能判断对底部的力矩大于还是小于实际总矩。',
    '压力中心保证等效合力和实际压力分布对任意点产生相同的力矩。',
    '需要选定参照点，再用两种作用点的力臂比较力矩大小。',
])
def test_hydrostatic_equivalence_and_explicit_comparison_limit_remain_allowed(answer):
    validate_computation_assumptions(answer, [{'tool': 'hydrostatic_vertical_plate',
        'result': {'moment_about_supplied_reference': None}}])


def test_hydrostatic_computed_bottom_reference_is_not_rejected_as_unspecified():
    # This guard checks reference availability; numerical accuracy is reviewed
    # separately against the real, supplied tool result.
    validate_computation_assumptions('面积形心处的合力对底部的力矩大于压力中心处的合力。',
        [{'tool': 'hydrostatic_vertical_plate', 'arguments': {'hinge_depth': 3},
          'result': {'moment_about_supplied_reference': {'magnitude_Nm': 88290}}}])
