"""Protocol and explicit-request boundaries; no model or live database calls."""
import json

import pytest

from backend.app.citations import prose_only
from backend.app.provider import ProviderError
from backend.app.socratic_reply import (
    MAX_FEEDBACK_CHARACTERS, MAX_FIELD_CHARACTERS, MAX_REPLY_CHARACTERS, SOCRATIC_REPLY_PREFIX,
    SOCRATIC_REPLY_PROMPT, SocraticReplyProtocolError, assemble_socratic_reply,
    parse_socratic_reply, socratic_feedback_allowed, socratic_feedback_required,
    socratic_attempt_has_direct_verdict, socratic_attempt_has_next_step, socratic_reply_opt_out,
)


def reply(feedback="", question="你会先检查哪一个条件？"):
    return json.dumps({"feedback": feedback, "question": question}, ensure_ascii=False)


@pytest.mark.parametrize("feedback,question", [
    ("", "你会先检查哪一个条件？"),
    ("先看题目给出的条件。", "你会写出哪个等式？"),
    ("这个操作的定义见[1]。", "按这个定义，你会先检查哪个操作[1]？"),
    ("", "**你会先检查哪一个条件？**"),
    ("", "关键问题：__你会先检查哪一个条件？__"),
    ("", "你会先检查哪一个条件？[2]"),
    ("", "你会先检查哪一个条件？**[2]"),
    ("", "**你会先检查哪一个条件？[2]**"),
    ("", "你会先检查哪一个条件？【２】"),
    ("", "Which initial condition would you check?"),
    ("", "运行`push(x)`之前，栈顶位于哪个位置？"),
    ('`find("?")`中有一个字符字面量。', '`find("?")`首先检查哪个字符？'),
    ("", "代码中的`并说明`出现在什么位置？"),
])
def test_short_reply_preserves_citations_formatting_and_one_question(feedback, question):
    assert parse_socratic_reply(reply(feedback, question)) == {"feedback": feedback, "question": question}
    expected = (feedback + "\n\n" if feedback else "") + question
    assert assemble_socratic_reply(reply(feedback, question)) == expected


def test_allowed_whitespace_does_not_create_empty_feedback_paragraph():
    assert assemble_socratic_reply(reply(" \n", " 问题？ ")) == "问题？"


@pytest.mark.parametrize("question,expected", [
    ("你会先检查哪个条件", "你会先检查哪个条件？"),
    ("你会先检查什么[2]", "你会先检查什么？[2]"),
    ("**你会先检查哪一个条件**【２】", "**你会先检查哪一个条件**？【２】"),
])
def test_clear_interrogative_with_missing_mark_is_repaired(question, expected):
    assert parse_socratic_reply(reply(question=question))["question"] == expected


def test_declarative_or_over_limit_question_is_not_repaired():
    with pytest.raises(SocraticReplyProtocolError, match="问号"):
        parse_socratic_reply(reply(question="先检查数组是否有序"))
    too_long = "你" * (MAX_FIELD_CHARACTERS - 1) + "吗"
    with pytest.raises(SocraticReplyProtocolError, match="问号"):
        parse_socratic_reply(reply(question=too_long))


def test_field_limit_is_measured_before_trimming_and_includes_punctuation():
    assert parse_socratic_reply(reply("字" * MAX_FIELD_CHARACTERS,
                                      "字" * (MAX_FIELD_CHARACTERS - 1) + "？"))
    assert parse_socratic_reply(reply("字" * MAX_FEEDBACK_CHARACTERS,
                                      "你会先检查哪一个条件？"))
    for content in (reply("字" * (MAX_FEEDBACK_CHARACTERS + 1)),
                    reply(question="字" * MAX_FIELD_CHARACTERS + "？"),
                    reply(feedback=" " * MAX_FEEDBACK_CHARACTERS + "字")):
        with pytest.raises(SocraticReplyProtocolError):
            parse_socratic_reply(content)


@pytest.mark.parametrize("content", [
    None, 1, {}, [], "", " ", "not JSON", "{}", "[]", "null", "true",
    '{"feedback":""}', '{"question":"问题？"}',
    '{"feedback":"","question":"问题？","analysis":"hidden"}',
    '{"feedback":"","question":"问题？","approved":true}',
    '{"feedback":null,"question":"问题？"}',
    '{"feedback":false,"question":"问题？"}',
    '{"feedback":[],"question":"问题？"}',
    '{"feedback":"","question":1}',
    '{"feedback":"","question":null}',
    '{"feedback":"","question":NaN}',
    '{"feedback":"","question":Infinity}',
    '{"feedback":"","feedback":"覆盖","question":"问题？"}',
    '{"feedback":"","question":"问题？","question":"覆盖？"}',
    '{"feedback":"","qu\\u0065stion":"问题？","question":"覆盖？"}',
    '```json\n{"feedback":"","question":"问题？"}\n```',
    '```\n{"feedback":"","question":"问题？"}\n```',
    '~~~json\n{"feedback":"","question":"问题？"}\n~~~',
    '下面是JSON：{"feedback":"","question":"问题？"}',
    '{"feedback":"","question":"问题？"} trailing prose',
    '{"feedback":"","question":"问题？"} {"feedback":"","question":"第二个？"}',
    '{"feedback":"","question":"问题？",}',
    '{"feedback":"","question":"问题？"',
])
def test_bad_protocol_never_becomes_plaintext_or_approval(content):
    with pytest.raises(SocraticReplyProtocolError):
        assemble_socratic_reply(content)


@pytest.mark.parametrize("feedback,question", [
    ("你是否明白？", "你会先检查哪一个条件？"),
    ("OK?", "你会先检查哪一个条件？"),
    ("", ""), ("", "  "), ("", "你会先检查哪一个条件。"),
    ("", "你先比较哪个值？之后选什么？"),
    ("", "先检查哪里?然后怎么做？"),
    ("", "你先比较哪个值？再计算临界点。"),
    ("", "你会先比较哪个值，并说明理由？"),
    ("", "你会先比较哪个值，并请解释依据？"),
    ("", "你会先比较哪个值，并且请说明为什么？"),
    ("", "你会先比较哪个值，同时解释你的选择？"),
    ("", "你会先比较哪个值然后解释它？"),
    ("<think>private</think>", "你会先检查哪一个条件？"),
    ("< analysis >private", "你会先检查哪一个条件？"),
    ("analysis: private", "你会先检查哪一个条件？"),
    ("思维链：private", "你会先检查哪一个条件？"),
    ("", "<reasoning>private</reasoning>你会先检查哪一个条件？"),
    ("", "</ANALYSIS>你会先检查哪一个条件？"),
    ("", "```问题？```"), ("", "~~~问题？~~~"),
    ("", "`问题？`"), ("", '请检查`find("?")`。'),
    ('`find("?")`表示查询吗？', "你会先检查哪一个条件？"),
    ("bad\x00text", "问题？"), ("", "bad\x1f问题？"),
    ("\ud800", "问题？"),
])
def test_invalid_field_semistructure_is_rejected(feedback, question):
    with pytest.raises(SocraticReplyProtocolError):
        parse_socratic_reply(reply(feedback, question))


@pytest.mark.parametrize("question", [
    "这张已核对的明信片处理完后，你手边剩下的是什么状态，下一步该做什么？",
    "区间现在有多少个元素，接下来你应该怎么更新边界？",
    "当前最小子任务是什么，然后应该如何调用它？",
    "你会先检查哪个字段，并解释你的选择？",
])
def test_one_question_mark_cannot_hide_two_explicit_learning_tasks(question):
    with pytest.raises(SocraticReplyProtocolError):
        parse_socratic_reply(reply(question=question))
    assert not socratic_attempt_has_next_step("正确：这一步符合给定规则。\n\n" + question)


@pytest.mark.parametrize("question", [
    "这张核对完后，下一步该做什么？",
    "剩余的明信片现在是什么状态？",
    "区间只剩一个元素时，你会检查哪个值？",
    "例句‘状态是什么，下一步做什么’包含哪个连接词？",
    '代码`print("状态是什么，下一步做什么")`首先调用哪个函数？',
])
def test_scenario_or_quoted_combined_asks_do_not_add_a_current_task(question):
    assert parse_socratic_reply(reply(question=question))["question"] == question
    assert socratic_attempt_has_next_step("正确：这一步符合给定规则。\n\n" + question)


def test_oversized_or_deep_json_is_a_bounded_provider_error():
    for content in (" " * MAX_REPLY_CHARACTERS + reply(), "[" * 1100 + "]" * 1100):
        with pytest.raises(ProviderError):
            parse_socratic_reply(content)


def test_error_does_not_echo_model_content_or_hidden_reasoning():
    with pytest.raises(ProviderError) as caught:
        parse_socratic_reply(reply("<think>private-secret</think>"))
    assert "private-secret" not in str(caught.value)


def test_structurally_valid_answer_leak_is_not_approved_by_this_parser():
    # Meaning remains the independent fact/pedagogy review's responsibility.
    text = assemble_socratic_reply(reply("最终答案是5。", "你会先检查哪一个条件？"))
    assert text == "最终答案是5。\n\n你会先检查哪一个条件？"
    assert "approved" not in parse_socratic_reply(reply())


def test_prompt_identifies_contract_and_requires_original_evidence_review():
    assert SOCRATIC_REPLY_PROMPT.startswith(SOCRATIC_REPLY_PREFIX)
    assert "feedback" in SOCRATIC_REPLY_PROMPT and "question" in SOCRATIC_REPLY_PROMPT
    assert "来源、事实和题目范围审查" in SOCRATIC_REPLY_PROMPT
    assert "普通首轮feedback必须为空" in SOCRATIC_REPLY_PROMPT
    assert "明确询问其中哪里错" in SOCRATIC_REPLY_PROMPT
    assert "feedback必须先以" in SOCRATIC_REPLY_PROMPT


def test_unauthorized_feedback_is_discarded_and_question_remains_separately_validated():
    assert parse_socratic_reply(reply(), allow_feedback=False)["feedback"] == ""
    assert assemble_socratic_reply(reply(" \n", "问题？"), allow_feedback=False) == "问题？"
    for feedback in ("这是首次未经引导的反馈。", '`find("?")`', "最终答案是5。"):
        assert parse_socratic_reply(reply(feedback, "你会先检查哪个条件？"), allow_feedback=False) == {
            "feedback": "", "question": "你会先检查哪个条件？"}
        assert assemble_socratic_reply(reply(feedback, "你会先检查哪个条件？"), allow_feedback=False) == "你会先检查哪个条件？"
    assert parse_socratic_reply(reply("oversized unauthorized draft " * 20),
                                allow_feedback=False)["feedback"] == ""
    # Structural safety still applies before an unauthorized field is discarded.
    with pytest.raises(SocraticReplyProtocolError):
        parse_socratic_reply(reply("<think>secret</think>", "你会先检查哪个条件？"), allow_feedback=False)
    assert parse_socratic_reply(reply("这是回应上一问的草稿。"), allow_feedback=True)["feedback"]


def test_question_length_failure_identifies_the_visible_field():
    with pytest.raises(SocraticReplyProtocolError, match="question"):
        parse_socratic_reply(reply(question="x" * (MAX_FIELD_CHARACTERS + 1) + "?"))


@pytest.mark.parametrize("permission", [None, 0, 1, "false", [], {}])
def test_invalid_feedback_permission_is_not_an_implicit_allow(permission):
    with pytest.raises(SocraticReplyProtocolError, match="布尔"):
        parse_socratic_reply(reply(), allow_feedback=permission)


@pytest.mark.parametrize("question,required", [
    ("我理解factorial(4)先返回4、3、2、1再相加，这样理解正确吗？", True),
    ("我认为队列是后进先出，请指出我的理解哪里错。", True),
    ("我认为队列是后进先出，先提示我一步。", False),
    ("如果学生问‘我理解正确吗’，请只提示一步。", False),
    ("我的答案是B。", False),
])
def test_only_explicit_substantive_attempt_check_requires_feedback(question, required):
    assert socratic_feedback_required([{"role": "user", "content": question}]) is required


@pytest.mark.parametrize("feedback,expected", [
    ("不正确：这一处理违反题目给定顺序[1]。", True),
    ("不完全正确：还需核对基准情形[1]。", True),
    ("正确：这个步骤符合当前代码[1]。", True),
    ("你会先检查哪条规则？", False),
    ("你的答案不正确，因为…", False),
])
def test_explicit_attempt_verdict_must_be_direct(feedback, expected):
    from backend.app.socratic_reply import socratic_feedback_has_direct_verdict
    assert socratic_feedback_has_direct_verdict(feedback) is expected


def test_grounded_attempt_verdict_requires_first_sentence_judgment():
    assert socratic_attempt_has_direct_verdict("不正确：教材代码使用乘法。")
    assert socratic_attempt_has_direct_verdict("**不正确：**教材代码使用乘法。")
    assert socratic_attempt_has_direct_verdict("### __不完全正确：__还需区分调用与返回。")
    assert socratic_attempt_has_direct_verdict("当前证据不足，无法判断：图中字符未识别清楚。")
    assert socratic_attempt_has_direct_verdict("当前证据不足，无法判断这张图中的键关系。")
    assert socratic_attempt_has_direct_verdict("现有来源不足，无法判断你的推演是否符合教材。")
    assert not socratic_attempt_has_direct_verdict("教材代码使用乘法，因此你的理解不正确。")
    assert not socratic_attempt_has_direct_verdict("视觉模型识别可能误读。不正确：教材代码使用乘法。")


def attempt_blocks(task, feedback="不完全正确：应先区分当前项与后续结果。"):
    return [{"text": feedback, "source_ids": [1]}, {"text": task, "source_ids": []}]


def attempt_answer(blocks):
    # Synthetic rendering avoids a provider/database fixture and leaves the
    # helper responsible only for syntax candidates, not independent approval.
    return "\n\n".join(block["text"] + (" [1]" if block["source_ids"] else "") for block in blocks)


@pytest.mark.parametrize("task", [
    "请写出当前项与下一项结果组合的表达式。",
    "请根据相邻文字给出的运算关系，写出这一层待恢复的表达式。",
    "请根据这一层的参数和后续结果写出表达式。",
    "请先写出 `step(k - 1)` 返回后这一层的符号关系。",
    "请你选择第一个需要检查的字段。",
    "请依据给定规则补全一个空格。",
    "下一步：请标出需要读取的那个位置。",
    "**请写出一个中间表达式。**",
])
def test_grounded_final_single_imperative_is_a_next_step_candidate_without_question_repair(task):
    blocks = attempt_blocks(task)
    answer = attempt_answer(blocks)
    saved_answer, saved_blocks = answer, json.dumps(blocks, ensure_ascii=False)
    assert "？" not in prose_only(answer)
    assert socratic_attempt_has_next_step(answer, blocks=blocks)
    assert answer == saved_answer and json.dumps(blocks, ensure_ascii=False) == saved_blocks
    assert not socratic_attempt_has_next_step(answer)  # No prose fallback.


@pytest.mark.parametrize("answer,expected", [
    ("不正确：先区分输入和状态。你会先查看哪个字段？", True),
    ("不正确：请先比较什么？再解释原因？", False),
    ('不正确：代码里的`find("?")`只是字面量。你会先查看哪个字段？', True),
    ("不正确：代码如下。\n```python\nprint('?')\n```\n你会先检查哪个字段？", True),
    ("不正确：原文的问题是‘下一层是什么？’。你会先检查哪个字段？", True),
    ("不正确：原文的问题是‘下一层是什么？’。", False),
    ('不正确：原文的问题是"下一层是什么?"。', False),
    ("> 你会先检查哪个条件？", False),
    ("不正确：原文的问题如下。\n> 你会先检查哪个条件？", False),
    ('不正确：`find("?")`只是代码中的字符。', False),
    ("不正确：这里只给出了反馈。", False),
])
def test_visible_question_count_excludes_code_and_quoted_old_questions(answer, expected):
    assert socratic_attempt_has_next_step(answer) is expected


@pytest.mark.parametrize("task", [
    "当前项应和后续结果组合。",  # A statement is not a next step.
    "请写出表达式，并说明理由。",
    "请写出表达式，并解释为什么这样组合。",
    "请写出表达式，并先列出输入。",
    "请写出表达式，同时列出输入。",
    "请根据规则写出表达式，然后判断结果。",
    "请写出表达式，再列出另一个情形。",
    "请写出表达式。请解释理由。",
    "请写出表达式；请列出参数。",
    "先看当前输入，接着请写出表达式。",
    "请写出表达式，另外选择下一步。",
    "‘请写出表达式。’",  # A quote does not assign a task.
    "`请写出表达式。`",  # Neither does a code string.
    "请写出。",  # No task object.
    "请写出    。",  # Whitespace is not a task object either.
    "请写出表达式。答案已经全部给出了。",
])
def test_grounded_imperative_candidate_excludes_explicit_extra_tasks_and_non_tasks(task):
    blocks = attempt_blocks(task)
    assert not socratic_attempt_has_next_step(attempt_answer(blocks), blocks=blocks)


def test_imperative_must_be_only_task_and_final_grounded_block():
    blocks = attempt_blocks("请写出一个表达式。")
    blocks.insert(1, {"text": "请列出本轮输入。", "source_ids": []})
    assert not socratic_attempt_has_next_step(attempt_answer(blocks), blocks=blocks)
    blocks = attempt_blocks("请写出一个表达式。", feedback="不正确：请列出输入。")
    assert not socratic_attempt_has_next_step(attempt_answer(blocks), blocks=blocks)
    blocks = attempt_blocks("请写出一个表达式。")
    blocks.append({"text": "这只是后续说明。", "source_ids": []})
    assert not socratic_attempt_has_next_step(attempt_answer(blocks), blocks=blocks)
    blocks = attempt_blocks("请写出一个表达式。")
    assert not socratic_attempt_has_next_step("不正确：这里没有下一步。", blocks=blocks)
    assert not socratic_attempt_has_next_step(attempt_answer(blocks) + "\n后续说明。", blocks=blocks)


def test_quote_or_code_in_feedback_does_not_assign_an_extra_imperative():
    for feedback in ("不正确：原文举例说‘请列出输入。’，但这里只是引用。",
                     "不正确：`请列出输入。`只是被引用的代码字符串。",
                     "不正确：局部图像可能误读，请对照原PDF页图核验。"):
        blocks = attempt_blocks("请写出一个表达式。", feedback=feedback)
        assert socratic_attempt_has_next_step(attempt_answer(blocks), blocks=blocks)


@pytest.mark.parametrize("blocks", [
    [], {}, (), "blocks", [None], [{"text": "请写出表达式。"}],
    [{"text": "请写出表达式。", "source_ids": [], "judgment": True}],
    [{"text": None, "source_ids": []}], [{"text": 1, "source_ids": []}],
    [{"text": " ", "source_ids": []}], [{"text": "x" * 8193, "source_ids": []}],
    [{"text": "请写出表达式。", "source_ids": "1"}],
    [{"text": "请写出表达式。", "source_ids": [True]}],
    [{"text": "请写出表达式。", "source_ids": [0]}],
    [{"text": "请写出表达式。", "source_ids": [-1]}],
    [{"text": "请写出表达式。", "source_ids": [1, 1]}],
    [{"text": "请写出表达式。", "source_ids": [[]]}],
    [{"text": "请写出表达式。", "source_ids": list(range(1, 34))}],
    [{"text": "请写出表达式。", "source_ids": []}] * 33,
    [{"text": "x" * 8192, "source_ids": []}] * 5,
], ids=lambda value: type(value).__name__)
def test_invalid_grounded_blocks_cannot_authorize_question_or_imperative(blocks):
    for answer in ("不正确：请写出表达式。", "不正确：你会先检查什么？"):
        assert not socratic_attempt_has_next_step(answer, blocks=blocks)


@pytest.mark.parametrize("answer", [None, {}, [], 1, "", "  ", "x" * 65537],
                         ids=lambda value: type(value).__name__)
def test_next_step_candidate_handles_bad_answer_types_and_size_without_throwing(answer):
    assert not socratic_attempt_has_next_step(answer)


def test_candidate_detection_does_not_approve_verdict_or_semantic_single_task():
    # The caller's existing direct-verdict gate remains necessary: a syntactic
    # question candidate is not a correctness judgment or teaching approval.
    answer = "你会先检查哪个字段？"
    assert socratic_attempt_has_next_step(answer)
    assert not socratic_attempt_has_direct_verdict(answer)
    blocks = attempt_blocks("请写出这两个条件对应的两个表达式。")
    assert socratic_attempt_has_next_step(attempt_answer(blocks), blocks=blocks)
    # More than one product can hide in a single imperative. Independent review
    # must still reject that in a single-step scope; this helper cannot prove it.


def history(answer):
    return [{"role": "assistant", "content": answer}, {"role": "user", "content": "这是我的尝试。"}]


@pytest.mark.parametrize("answer", [
    "你会先检查哪个条件？", "**你会先检查哪个条件？**", "关键问题：__你会先检查哪个条件？__",
    "你会先检查哪个条件？[1]", "**你会先检查哪个条件？[1]**", "你会先检查哪个条件？【１】",
    "这是目前题目给出的条件[1]。\n\n你会先检查哪个条件？",
    '`find("?")`首先检查哪个字符？', "你会先检查‘栈顶’的哪个位置？",
    "What's the first operation you would check?", "Which variable's value would you check?",
    "你先看哪里？然后看哪个条件？",  # An earlier explicit multi-question reply may end in a real question.
])
def test_saved_adjacent_assistant_ends_in_a_real_question_before_current_user(answer):
    assert socratic_feedback_allowed(history(answer))


@pytest.mark.parametrize("question", [
    ("第79物理页 bad 和 dab 都在第8槽，哈希相同代表键相同，所以新 pat 覆盖原 evil 吧？"
     "按图提示我哪里错。"),
    "队列是后进先出，麻烦指出哪里错。",
])
def test_first_turn_explicit_attempt_and_correction_request_allows_short_feedback(question):
    first_turn = [{"role": "user", "content": question}]
    assert socratic_feedback_allowed(first_turn)
    assert parse_socratic_reply(reply("哈希槽相同不等于键相同；教材图示展示了不同键可以落入同一槽[1]。",
                                      "新键应与槽内哪个字段比较？[1]"), allow_feedback=True)


@pytest.mark.parametrize("question", [
    "我认为队列是后进先出，请只提示下一步。",
    "我的答案是B，请继续引导我。",
    "我哪里错？",  # No substantive attempt to review.
    "麻烦指出哪里错。",  # A correction request alone contains no attempt to inspect.
    "‘队列是后进先出’，麻烦指出哪里错。",  # The only assertion is quoted material.
    "如果学生认为队列是后进先出，按图提示我哪里错？",
    "教材里的示例写着‘按图提示我哪里错’，请解释这句话。",
    "普通首轮问题：队列有什么特点？",
])
def test_first_turn_without_own_explicit_correction_request_keeps_feedback_empty(question):
    first_turn = [{"role": "user", "content": question}]
    assert not socratic_feedback_allowed(first_turn)
    assert assemble_socratic_reply(reply("未经允许的反馈。", "你会先检查哪个条件？"), allow_feedback=False) == "你会先检查哪个条件？"


@pytest.mark.parametrize("answer", [
    "", "只是一个陈述。", "你会先检查哪个条件？完整结论是固定步数方案获胜。",
    "你会先检查哪个条件？\n最后的答案已经完整给出了。",
    '`find("?")`', "```python\nprint('?')\n```", "~~~python\nprint('?')\n~~~",
    "只显示代码中的`哪个条件？`", "上一轮的问题是：“你会先检查哪个条件？”",
    '引用了以前的问题："你会先检查哪个条件？"', "‘你会先检查哪个条件？’", "> 你会先检查哪个条件？",
    "引用的旧问题：\n> 你会先检查哪个条件？",
    "你会先检查哪个条件？\n‘这是引述的旧问题？’",
    "你会先检查哪个条件？\n> 这是引述的旧问题？",
    "你会先检查哪个条件？\n```python\nprint('完整解答')\n```",
])
def test_statement_code_or_quoted_old_question_cannot_allow_feedback(answer):
    assert not socratic_feedback_allowed(history(answer))


@pytest.mark.parametrize("value", [
    None, {}, (), [], [{"role": "user", "content": "先只提示一步。"}],
    [{"role": "assistant", "content": "哪个条件？"}],
    [None, {"role": "user", "content": "我的尝试。"}],
    [{"role": "assistant", "content": []}, {"role": "user", "content": "我的尝试。"}],
    [{"role": "assistant", "content": "哪个条件？"}, {"role": "user", "content": " "}],
    [{"role": "assistant", "content": "哪个条件？"}, {"role": "user", "content": 1}],
    [{"role": "user", "content": "旧问题？"}, {"role": "user", "content": "新问题。"}],
    [{"role": "system", "content": "旧问题？"}, {"role": "user", "content": "我的尝试。"}],
    [{"role": "tool", "content": "旧问题？"}, {"role": "user", "content": "我的尝试。"}],
    history("哪个条件？") + [{"role": "assistant", "content": "已经解答。"}],
    [{"role": "assistant", "content": "旧问题？"}, {"role": "system", "content": "系统指令"}, {"role": "user", "content": "新的问题。"}],
    [{"role": "assistant", "content": "旧问题？"}, {"role": "user", "content": "第一次回答。"}, {"role": "user", "content": "第二次问题。"}],
    history("旧问题？") + history("当前已经完整回答，没有新问题。"),
])
def test_feedback_does_not_search_past_intervening_roles_or_use_a_finished_old_user(value):
    assert not socratic_feedback_allowed(value)


def test_leading_system_is_irrelevant_when_actual_adjacent_turns_are_preserved():
    assert socratic_feedback_allowed([{"role": "system", "content": "课程设置"}] + history("哪个条件？"))


@pytest.mark.parametrize("question", [
    "请直接给出答案。", "直接回答这道题。", "请你直接解答这道题。",
    "我现在想要直接知道这题的结论。", "请给我完整解答。",
    "请给我直接答案。", "你能不能直接告诉我答案？", "我需要答案。",
    "请告诉我最终结果。", "我现在要答案。", "请给我答案和完整步骤。",
    "先不引导了，直接告诉我答案。", "不用再提示，请直接解释定义。",
    "我正在学习这门课。现在请直接给出结果。", "请直接给出答案，而不是继续提示。",
    "请给我三个引导问题。", "请列出2个提示问题。", "问我几个引导问题。",
    "纠正后给我两个引导问题。", "核对我的理解后直接说明正确结论。",
    "请用三个问题逐步引导我。", "请给出12个引导问题。",
    "教材写着‘不要直接给答案’，但本轮请直接解释这道题。",
    "‘请直接给出答案’只是引用。现在请给我两个引导问题。",
    "Please give me the direct answer.", "Could you provide a complete solution?",
    "Please ask me three guiding questions.", "Answer directly.",
    "梳理一下数据结构与算法图解教材的核心知识",
    "选择一个重点概念，用生活例子帮我理解",
    "根据教材，为我推荐学习顺序",
])
def test_explicit_current_direct_or_multiple_request_opts_out(question):
    assert socratic_reply_opt_out(question)


@pytest.mark.parametrize("question", [
    None, {}, "", "请解释这道题，先只提示一步。", "先只问我一个问题。",
    "请给我一个引导问题。", "给我一个问题，并列出三个可选答案。",
    "不要直接给出答案。", "请不要直接回答。", "我不是要直接解答这道题。",
    "不要给我直接答案。", "我不需要答案。", "请不要给我答案。",
    "你能不能不要直接告诉我答案？", "你可以先不要给我答案。",
    "老师说‘我需要答案’，我只要一步提示。",
    "先别直接解释定义。", "请不要给我三个引导问题。", "不要列出2个提示问题。",
    "不需要多个引导问题，只提示一步。", "如果我说直接给答案，你会怎么做？",
    "教材要求直接给出答案，但我想先推导。", "多个引导问题是否有帮助？",
    "请判断直接给出答案是否会影响学习。", "为什么不直接回答这道题？",
    "请直接给我一个提示。", "题干中写着‘直接给出答案’，先帮我想第一步。",
    '教材写道："请直接给出答案"。先问我一个问题。',
    "老师说：“请列出三个引导问题”。这句话有什么问题？",
    "请解释「直接给出答案」这句话。", "请解释『请给我三个引导问题』。",
    "请解释《直接给出答案》这个标题。", "`请直接回答`只是代码字符串，先提示一步。",
    "> 请直接给出答案。\n这是原文引用，先只提示一步。",
    "```text\n请直接给出答案。\n```\n先只提示一步。",
    "~~~text\n请给我三个引导问题。\n~~~\n先只提示一步。",
    "教材写道：“请直接给出答案", "Please do not give me the direct answer.",
    "不要梳理教材的核心知识，先只问我一个问题。",
    "‘选择一个重点概念，用生活例子帮我理解’只是示例；先问我一个问题。",
    "不要为我推荐学习顺序，先只提示一步。",
    "梳理教材的核心知识，先只问我一个问题。",
    "请概括教材的核心知识，只给我一个问题，不要给答案。",
    "请概括教材的核心知识，仅提供一个引导问题。",
    "选择一个重点概念，用生活例子帮我理解，但先只提示第一步。",
    "根据教材，为我推荐学习顺序；先问我一个问题来确定起点。",
    "What does 'give me the direct answer' mean?", "Do not ask me three guiding questions.",
])
def test_quoted_negated_conditional_or_merely_reported_request_keeps_single_step(question):
    assert not socratic_reply_opt_out(question)


@pytest.mark.parametrize("question", [
    "不要继续提问。", "别再问我。", "无需再反问。", "不用继续问我了。",
    "请不要再提问。", "本轮不用反问。", "接下来别问我。", "不要反问吧。",
    "奶茶最前面、栈最上面最近盘子，请确认并再讲清楚一次，不要继续提问。",
    "我认为队列先进先出，正确吗？请确认，不用再提问。",
    "老师说‘不要继续提问’，但现在请不要继续提问。",
])
def test_current_whole_clause_no_question_command_opts_out(question):
    assert socratic_reply_opt_out(question)


@pytest.mark.parametrize("question", [
    "“不要继续提问”只是一个例子，先问我一步。",
    "教材写道：不要继续提问。", "老师要求，不要继续提问。",
    "我上次说，不要继续提问；现在先问一个问题。",
    "我没有要求，不要继续提问；先问我一步。",
    "如果我说，不要继续提问，你会如何理解？",
    "如果我说，这次不要继续提问，你会如何理解？",
    "假如，不要继续提问，你会直接讲解吗？",
    "我不是要求不要继续提问，先继续提示。", "不是不要继续提问。",
    "上一轮不要继续提问是旧要求，当前先问一步。",
    "请解释不要继续提问这句话。", "不要继续提问是否合适？", "不要继续提问吗？",
    "`不要继续提问`只是代码字符串，先问一个问题。",
    "> 不要继续提问。\n请先问我一步。", "```text\n不要继续提问。\n```\n先问我一步。",
    "老师说：‘不要继续提问", "不用再提问的措辞需要修改。",
])
def test_no_question_examples_reports_and_negations_do_not_grant_optout(question):
    assert not socratic_reply_opt_out(question)
