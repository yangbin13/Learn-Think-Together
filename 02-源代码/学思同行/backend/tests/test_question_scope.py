import pytest

from backend.app.question_scope import question_scope, missing_explicit_terms, requests_textbook_source
from backend.tests.test_api import setup, register, events


@pytest.mark.parametrize(("question", "expected"), [
    ("FAT与隐式指针表有什么区别？请解释各自对增长和随机访问的影响。", ["FAT", "隐式指针表有什么区别", "对增长", "随机访问的影响"]),
    ("ACL与Capabilities分别按列还是按行？条目记录什么？", ["ACL", "Capabilities", "按列还是按行"]),
    ("CPU在I/O后先进入就绪队列，再等待调度。", ["CPU", "I/O"]),
    ("只用中文解释抽象含义，不涉及具体缩写。", []),
])
def test_question_scope_exposes_named_terms_without_benchmark_answers(question, expected):
    checklist = question_scope(question)
    for item in expected:
        assert any(item in value for value in checklist["named_terms"] + checklist["required_subquestions"])


def test_every_explicit_named_term_must_be_acknowledged_before_review():
    question = "FAT的显式集中指针与盘块内隐式指针有什么区别？"
    assert missing_explicit_terms(question, "FAT在一个表中维护显式链接。") == []
    assert missing_explicit_terms(question, "显式表中的链接与盘块内指针位置不同。") == ["FAT"]
    assert missing_explicit_terms(question, "教材没有提供FAT表如何组织的依据，暂时不能据本书断言。") == []


def test_same_term_inside_inline_code_does_not_satisfy_student_facing_coverage():
    assert missing_explicit_terms("FAT如何组织？", "`FAT` 内部结构在本轮来源中没有说明。") == ["FAT"]


@pytest.mark.parametrize("locator", [
    "文档ID {document_id}，原PDF物理页185",
    "文档 ID 为 {document_id}，位于原 PDF 物理页 185",
    "文档 ID：`{document_id}`，原 PDF 第185物理页",
    "原 PDF 185物理页，文档 ID = {document_id}",
    "document ID {document_id}; in the original PDF physical page 185",
    "文档ID {document_id} 的原PDF物理页185",
    "文档ID {document_id} 中的原PDF物理页185",
    "document ID {document_id}'s original PDF physical page 185",
    "the original PDF physical page 185 of document ID {document_id}",
    "原 PDF 物理页185",
])
def test_explicit_source_locator_is_not_a_teaching_requirement(locator):
    document_id = "e" * 32
    question = "请解释TCP和SYN在三次握手中的作用。" + locator.format(document_id=document_id) + "。"
    scope = question_scope(question)
    assert scope["named_terms"] == ["TCP", "SYN"]
    assert all(document_id not in part and "PDF" not in part and "ID" not in part
               for part in scope["required_subquestions"])
    assert missing_explicit_terms(question, "TCP以SYN报文段发起连接同步。") == []


@pytest.mark.parametrize("locator", [
    "文档ID {document_id}，原PDF物理页185",
    "文档 ID 为 {document_id}，位于原 PDF 物理页 185",
    "文档ID {document_id} 的原PDF物理页185",
    "文档ID {document_id} 中的原PDF物理页185",
])
def test_terms_requested_elsewhere_in_the_same_message_still_require_coverage(locator):
    question = ("请解释TCP和SYN。" + locator.format(document_id="e" * 32)
                + "。另外解释PDF文件格式和ID字段的含义。")
    assert question_scope(question)["named_terms"] == ["TCP", "SYN", "PDF", "ID"]
    assert missing_explicit_terms(question, "TCP以SYN发起连接同步。") == ["PDF", "ID"]
    assert missing_explicit_terms(question, "TCP、SYN、PDF文件格式和ID字段分别需要不同的依据。") == []


@pytest.mark.parametrize(("question", "expected"), [
    ("PDF文件格式如何保存矢量图？", ["PDF"]),
    ("PDF中ID数组如何标识文档？", ["PDF", "ID"]),
    ("ID标识符为什么要全局唯一？", ["ID"]),
    ("文档ID " + "e" * 32 + " 的编码规则是什么？", ["ID"]),
    ("原PDF中的物理页和逻辑页有何区别？", ["PDF"]),
    ("请解释TCP、SYN、FAT、ACL和I/O。", ["TCP", "SYN", "FAT", "ACL", "I/O"]),
])
def test_content_terms_are_not_globally_exempted(question, expected):
    assert question_scope(question)["named_terms"] == expected
    assert missing_explicit_terms(question, "本轮回答尚未解释这些术语。") == expected


def test_document_id_discussion_between_locators_is_retained():
    question = "文档ID " + "e" * 32 + " 如何编码？参考原PDF物理页185。"
    assert question_scope(question)["named_terms"] == ["ID"]
    assert missing_explicit_terms(question, "请结合原页核验。") == ["ID"]


def test_rereading_a_document_owned_page_does_not_require_explaining_its_id():
    question = "请重新实际读取文档ID " + "e" * 32 + " 的原PDF物理页185，核对SYN字段。"
    assert question_scope(question)["named_terms"] == ["SYN"]
    assert missing_explicit_terms(question, "SYN字段尚需原页核验。") == []


@pytest.mark.parametrize(("bridge", "expected"), [
    (" " * 79 + "的", ["SYN"]),
    (" " * 80 + "的", ["ID", "SYN"]),
])
def test_source_ownership_bridge_keeps_the_eighty_character_adjacency_limit(bridge, expected):
    question = "文档ID " + "e" * 32 + bridge + "原PDF物理页185，核对SYN字段。"
    assert question_scope(question)["named_terms"] == expected


@pytest.mark.parametrize(("question", "expected"), [
    ("文档ID " + "e" * 32 + " 的编码规则是什么？参考原PDF物理页185，解释SYN。", ["ID", "SYN"]),
    ("文档ID " + "e" * 32 + " 中的ID字段是什么意思？参考原PDF物理页185。", ["ID"]),
    ("文档ID " + "e" * 32 + "的PDF文件格式如何表示SYN？", ["ID", "PDF", "SYN"]),
    ("文档ID abc 的原PDF物理页185中SYN字段如何表示？", ["ID", "SYN"]),
])
def test_ownership_words_do_not_hide_knowledge_questions_or_replace_required_anchors(question, expected):
    assert question_scope(question)["named_terms"] == expected
    assert missing_explicit_terms(question, "这些术语尚未解释。") == expected


@pytest.mark.parametrize("tail", [
    "第36物理页让我以为 O(1) 就是一步而且任何时候都比 O(N) 快。先比较固定100步与N步，提示我别直接给结论。",
    "N=50时，固定100步比50步多，这能说明什么？",
    "请用安全计算工具核对自拟三角形参数：边长3和4、夹角60度，明确角度单位。",
    "第36物理页，其中N表示数组长度。请比较固定100步与N步。",
])
def test_real_student_multi_turn_source_prefix_does_not_require_teaching_document_id(tail):
    question = "我正在学《数据结构与算法图解》，文档ID " + "e" * 32 + "。" + tail
    scope = question_scope(question)
    assert "ID" not in scope["named_terms"]
    assert all("e" * 32 not in part for part in scope["required_subquestions"])
    assert missing_explicit_terms(question, "先比较固定步数和当前数据量，明确输入条件后再计算。") == []
    assert any("固定" in part or "角度" in part for part in scope["required_subquestions"])


@pytest.mark.parametrize("locator", [
    "文档ID {document_id}。", "文档 ID 为 `{document_id}`；", "document ID: {document_id};",
    "资料来源：文档ID {document_id}，", "(文档 ID = {document_id})，",
])
def test_delimited_native_document_metadata_can_omit_repeated_page(locator):
    question = locator.format(document_id="abc123ef" * 4) + "请解释TCP和SYN字段。"
    assert question_scope(question)["named_terms"] == ["TCP", "SYN"]
    assert missing_explicit_terms(question, "TCP用SYN同步序号。") == []


@pytest.mark.parametrize("locator", [
    "文档ID {document_id}。第36物理页", "文档ID {document_id}，物理页36",
    "第36物理页，文档ID {document_id}", "物理第36页，文档ID {document_id}",
])
def test_bare_physical_page_is_source_metadata_only_when_bound_to_a_native_document(locator):
    question = locator.format(document_id="e" * 32) + "，核对TCP和SYN。"
    assert question_scope(question)["named_terms"] == ["TCP", "SYN"]
    assert missing_explicit_terms(question, "TCP和SYN仍需原页核对。") == []


@pytest.mark.parametrize("question", [
    "文档ID " + "e" * 32 + " 的编码规则是什么？",
    "文档ID " + "e" * 32 + "。它的编码规则是什么？",
    "文档ID " + "e" * 32 + "。这个值如何生成？",
    "文档ID " + "e" * 32 + "。它由哪些字段组成？",
    "文档ID " + "e" * 32 + "。请解释其含义。",
    "请解释：文档ID " + "e" * 32 + "。",
    "第36物理页，文档ID " + "e" * 32 + "的生成机制是什么？",
    "原PDF物理页36，文档ID " + "e" * 32 + "如何编码？",
    "document ID " + "e" * 32 + "; how is it generated?",
    "文档ID " + "e" * 32 + "，原PDF物理页36。它的编码规则是什么？",
    "文档ID " + "e" * 32 + "，第36物理页。它由哪些字段组成？",
    "请解释文档ID " + "e" * 32 + "，原PDF物理页36。",
    "请解释文档ID student-chosen-identifier，原PDF物理页36。",
])
def test_actual_native_identifier_discussions_and_anaphora_still_require_id(question):
    assert "ID" in question_scope(question)["named_terms"]
    assert "ID" in missing_explicit_terms(question, "本轮尚未回答这个问题。")


@pytest.mark.parametrize("question", [
    "HTTP request ID 为什么用于请求追踪？",
    "HTTP request ID " + "e" * 32 + "。它如何在重试时保持不变？",
    "我正在学《网络》，文档ID " + "e" * 32 + "。HTTP request ID 的生成规则是什么？",
    "我正在学《网络》，文档ID " + "e" * 32 + "。PDF文件中的ID数组是做什么用的？",
])
def test_true_id_and_http_or_pdf_terms_elsewhere_are_never_globally_exempt(question):
    assert "ID" in question_scope(question)["named_terms"]
    assert "ID" in missing_explicit_terms(question, "这里只说明HTTP或PDF文件的一部分。")


def test_opaque_non_native_identifier_is_not_assumed_to_be_a_standalone_source_field():
    question = "文档ID student-chosen-identifier。解释它的作用。"
    assert question_scope(question)["named_terms"] == ["ID"]
    assert missing_explicit_terms(question, "尚未解释标识符。") == ["ID"]


@pytest.mark.parametrize("tail", [
    "第36物理页让我以为 O(1) 就是一步。先比较固定100步与N步。",
    "N=50时，固定100步比50步多，这能说明什么？",
])
def test_native_source_metadata_survives_real_chat_pipeline_without_false_term_rejection(setup, tail):
    client, app, provider = setup
    register(client)
    question = "我正在学《数据结构与算法图解》，文档ID " + "e" * 32 + "。" + tail
    result = events(client.post("/api/chat", json={
        "course_id": "data-structures", "message": question, "mode": "socratic",
    }))
    assert result[-1][0] == "done"
    assert not any(event in {"error", "answer_reset"} for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 1
    assert app.state.db.one("SELECT content FROM messages WHERE role='user'")["content"] == question
    assert any(message.get("content") == question
               for messages in provider.socratic_histories for message in messages)


def test_actual_document_id_question_still_fails_closed_in_chat_when_teaching_omits_id(setup):
    client, app, provider = setup
    register(client)
    question = "文档ID " + "e" * 32 + "。它的编码规则是什么？"
    result = events(client.post("/api/chat", json={
        "course_id": "data-structures", "message": question, "mode": "socratic",
    }))
    assert result[-1][0] == "error" and "命名术语：ID" in result[-1][1]["message"]
    assert sum(event == "answer_reset" for event, _ in result) == 1
    assert not any(event == "done" for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0
    assert len(provider.socratic_histories) == 2


_HASH_PAGE_QUESTION = (
    "第79物理页 bad 和 dab 都在第8槽，哈希相同代表键相同，所以新 pat 覆盖原 evil 吧？按图提示我哪里错。\n"
    "请依据当前课程《数据结构与算法图解》教材（文档 e8fc31725eff48be9c093b1279a0c24e）回答，页码指PDF物理页。"
)


@pytest.mark.parametrize("declaration", [
    "页码指PDF物理页。", "页码指 PDF 物理页。", "页码均指PDF物理页。",
    "页码都指原 PDF 物理页。", "页码指的是PDF物理页。", "（页码指PDF物理页）",
])
def test_page_number_convention_is_source_metadata_not_a_pdf_lesson(declaration):
    question = "请解释HTTP与TCP的作用。" + declaration
    assert question_scope(question)["named_terms"] == ["HTTP", "TCP"]
    assert missing_explicit_terms(question, "HTTP承载请求语义，TCP提供传输。") == []
    assert all("PDF" not in part for part in question_scope(question)["required_subquestions"])


def test_actual_hash_question_page_convention_does_not_require_pdf_in_hint():
    assert question_scope(_HASH_PAGE_QUESTION)["named_terms"] == []
    assert missing_explicit_terms(_HASH_PAGE_QUESTION, "同一槽里的两个键是否完全相同？") == []


@pytest.mark.parametrize("question", [
    "页码指PDF物理页是什么意思？",
    "请解释页码指PDF物理页。", "请解释：页码指PDF物理页。",
    "‘页码指PDF物理页’这句话有什么含义？",
    "页码指PDF物理页，它与印刷页有何区别？",
    "页码指PDF物理页。它如何定位？",
    "页码指PDF物理页。请解释其格式。",
    "页码指PDF物理页，说明其格式如何表示页面对象。",
    "页码指PDF物理页，有什么意义？",
    "PDF文件格式如何保存页面对象？页码指PDF物理页。",
    "页码指PDF物理页。请解释PDF文件格式和HTTP、TCP协议。",
])
def test_pdf_knowledge_questions_and_anaphora_remain_required(question):
    assert "PDF" in question_scope(question)["named_terms"]
    assert "PDF" in missing_explicit_terms(question, "本轮尚未解释该术语。")
    if "HTTP" in question:
        assert question_scope(question)["named_terms"] == ["PDF", "HTTP", "TCP"]


@pytest.mark.parametrize("reject_review", [False, True])
def test_actual_page_convention_reaches_independent_review_in_temp_chat(setup, reject_review):
    from backend.app.db import dumps
    from backend.app.grounded_reply import GROUNDED_REPLY_PROMPT

    client, app, provider = setup
    register(client)
    original_complete = provider.complete
    reviewed, teaching_inputs = [], []

    async def complete(messages, **kwargs):
        prompt = messages[0].get("content", "")
        if GROUNDED_REPLY_PROMPT in prompt:
            teaching_inputs.append(messages)
            answer = ("不正确：不同键通过链表结构共存。你会先比较哪两个键？" if reject_review
                      else "当前证据不足，无法判断这张图中键的关系。你能先核对同一槽里的两个键是否完全相同吗？")
            return {"content": dumps({"blocks": [{"text": answer, "source_ids": [1]}]}), "model": "fake-test-model"}
        if prompt.startswith("你是独立事实审查器"):
            reviewed.append(messages)
            return {"content": dumps({"approved": not reject_review,
                    "issues": [{"kind": "source_support", "detail": "实际证据没有支持链表结构，不能添加该机制。"}] if reject_review else []})}
        return await original_complete(messages, **kwargs)

    provider.complete = complete
    previous_stream = provider.stream

    async def stream(messages, **kwargs):
        if GROUNDED_REPLY_PROMPT in messages[0].get("content", ""):
            result = await complete(messages, **kwargs)
            yield {"text": result["content"], "model": result["model"]}
        else:
            async for part in previous_stream(messages, **kwargs):
                yield part

    provider.stream = stream
    result = events(client.post("/api/chat", json={
        "course_id": "data-structures", "message": _HASH_PAGE_QUESTION, "mode": "socratic",
    }))
    assert len(reviewed) == (2 if reject_review else 1)
    assert not any("命名术语：PDF" in item.get("message", "") for event, item in result if event == "error")
    assert app.state.db.one("SELECT content FROM messages WHERE role='user'")["content"] == _HASH_PAGE_QUESTION
    assert any(message.get("content") == _HASH_PAGE_QUESTION
               for messages in teaching_inputs for message in messages)
    assert result[-1][0] == ("error" if reject_review else "done")
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == (0 if reject_review else 1)
    if reject_review:
        assert not any(event in {"token", "done"} for event, _ in result)


@pytest.mark.parametrize(("question", "expected"), [
    ("给出教材依据解释这个定理", True), ("请引用教材原文并回答", True),
    ("依据本书说明列组织", True), ("只解释图的外部几何", False),
])
def test_explicit_textbook_source_requests(question, expected):
    assert requests_textbook_source(question) is expected
