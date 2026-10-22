"""Planner routing boundaries; no provider, database, or service calls."""
import pytest

from backend.app.teaching_route import can_skip_planner


def body(text="栈是一种后进先出的数据结构，最后放入的数据最先取出。", **overrides):
    return {"document_id": "book", "section": "第2章 / 栈", "excerpt": text,
            "source_type": "uploaded_pdf", **overrides}


@pytest.mark.parametrize("mode", ["explain", "socratic", "solve"])
@pytest.mark.parametrize("question", ["栈是什么？", "请解释栈的基本概念。", "什么是栈？",
                                     "请用生活例子解释栈。"])
def test_basic_named_definition_matches_actual_body(mode, question):
    assert can_skip_planner(question, mode, [body()])


@pytest.mark.parametrize("question,flags", [
    ("从教材选一个重点概念，用生活例子帮我理解。", {"qualitative_analogy": True}),
    ("选择一个重点概念，用生活例子帮我理解", {"qualitative_analogy": True}),
    ("请从教材选择一个重点概念，说明出处，并用一个生活例子解释", {"qualitative_analogy": True}),
    ("选择一个具体概念，解释它的基本定义。", {}),
    ("从教材选一个具体概念，只问我一个可回答的入门问题；暂时不要讲解结论。",
     {"single_step_scope": True}),
    ("用一个生活情境引出教材中的概念，再只问我一个问题；暂时不要解释答案",
     {"single_step_scope": True, "qualitative_analogy": True}),
])
def test_choose_one_concept_can_use_existing_body(question, flags):
    assert can_skip_planner(question, "socratic", [body()], **flags)


def test_overview_needs_real_multi_chapter_structure():
    question = "请概括本书的主要内容，归纳为四个主要学习模块。"
    sources = [body("本书分为四章。第1章介绍数组，第2章介绍链表，第3章介绍排序，第4章介绍图。", section="前言")]
    assert can_skip_planner(question, "explain", sources, overview_requested=True)
    assert not can_skip_planner(question, "explain", [body()], overview_requested=True)
    assert not can_skip_planner(question, "explain", sources)


def test_multichapter_contents_are_valid_structure_but_cannot_supply_a_concept():
    sources = [body("第1章 数组\n第2章 链表\n第3章 排序", section="目录")]
    assert can_skip_planner("请总结全书。", "solve", sources, overview_requested=True)
    assert not can_skip_planner("选择一个具体概念，用生活例子帮我理解。", "explain", sources)


@pytest.mark.parametrize("source", [
    body("", title="栈是一种后进先出的结构"),
    body("栈", section="栈"),
    body("第1章 栈\n第2章 队列", section="目录"),
    body("栈是一种后进先出的数据结构。", evidence_kind="image"),
    body("栈是一种后进先出的数据结构。", source_type="verified_computation"),
    body("栈是一种后进先出的数据结构。", document_id="tool:call"),
    body("感谢所有参与教材出版的人们，本书是大家共同努力的结果。", section="前言"),
    {"title": "栈", "section": "栈"},
])
def test_nonbody_sources_cannot_enable_shortcut(source):
    assert not can_skip_planner("栈是什么？", "explain", [source])
    assert not can_skip_planner("选择一个具体概念，用生活例子帮我理解。", "explain", [source])


@pytest.mark.parametrize("question", [
    "从教材选一个概念，用生活例子解释，并计算复杂度。",
    "从教材选一个概念，用生活例子解释，给出具体数字。",
    "从教材选一个概念，只问我一个入门问题，再验证我的代码。",
    "从教材选一个概念，用生活例子解释，说明实现边界。",
    "从教材选一个概念，用生活例子解释，核对原图中的箭头。",
    "从教材选一个概念，用生活例子解释，再读取我的学习画像。",
    "请概括本书主要内容，并逐一比较所有算法。",
    "请用生活例子解释栈和队列的区别。",
    "请说明二分查找是否保证返回重复值中的首个。",
    "解释栈并证明它的性质。",
    "栈的复杂度是什么？",
    "请解释栈，给出程序。",
    "explain stack and queue",
    "explain stack using an everyday example, verify my code",
])
def test_limiting_requirements_are_not_overridden_by_template_or_flags(question):
    assert not can_skip_planner(question, "socratic", [body()], overview_requested=True,
                                qualitative_analogy=True, single_step_scope=True)


@pytest.mark.parametrize("source", [
    body("队列是一种先进先出的数据结构，最先存入的数据最先取出。", section="栈", title="栈"),
    body("与栈不同，队列是一种先进先出的数据结构，最先存入的数据最先取出。"),
    body("本章介绍栈与队列，需要先学习数组。"),
])
def test_named_concept_requires_its_own_body_definition(source):
    assert not can_skip_planner("栈是什么？", "explain", [source])


def test_basic_definition_in_raw_retrieval_text_is_supported_without_mutation():
    source = body()
    source["text"] = source.pop("excerpt")
    before = dict(source)
    assert can_skip_planner("请解释栈。", "solve", [source])
    assert source == before


def test_named_algorithm_life_example_is_allowed_when_body_explains_it():
    sources = [body("二分查找通过比较中间元素与目标，每次缩小候选范围，直到找到目标或范围为空。", section="二分查找")]
    assert can_skip_planner("请用一个生活例子解释二分查找。", "explain", sources,
                            qualitative_analogy=True)
    assert not can_skip_planner("请用一个生活例子解释散列表。", "explain", sources,
                                qualitative_analogy=True)


def test_quoted_or_extended_templates_are_not_current_narrow_requests():
    sources = [body()]
    for question in ("他说‘从教材选一个概念，用生活例子帮我理解’，请评价。",
                     "选择一个具体概念，用生活例子帮我理解，但满足老师给定的条件。",
                     "选择一个具体概念，用生活例子帮我理解；接着完成练习题。"):
        assert not can_skip_planner(question, "explain", sources, qualitative_analogy=True)


def test_single_step_needs_socratic_scope_and_does_not_skip_named_exercises():
    question = "从教材选一个具体概念，只问我一个入门问题。"
    assert not can_skip_planner(question, "socratic", [body()])
    assert not can_skip_planner(question, "explain", [body()], single_step_scope=True)
    assert not can_skip_planner("只问我一个栈的入门问题。", "socratic", [body()], single_step_scope=True)
    assert not can_skip_planner("栈是什么？", "socratic", [body()], single_step_scope=True)


def test_life_first_step_template_requires_scope_and_does_not_accept_added_requirements():
    question = "用一个生活情境引出教材中的概念，再只问我一个问题；暂时不要解释答案"
    assert not can_skip_planner(question, "socratic", [body()])
    assert not can_skip_planner(question, "explain", [body()], single_step_scope=True)
    assert not can_skip_planner(question + "；还要满足老师规定的限制", "socratic", [body()],
                                single_step_scope=True, qualitative_analogy=True)


@pytest.mark.parametrize("question,source", [
    ("我不理解递归，先用生活情境只问我一个入门问题，不要先给答案",
     body("在某个过程当中，又执行了这个过程，我们称之为递归(recursion)。", section="递归")),
    ("我不懂栈，先用一个生活例子问我一个基础问题，暂时不要直接给我答案。", body()),
    ("我还不理解递归，先用一个生活情境问我一个入门的问题，不要先解释答案。",
     body("递归通过调用函数自身，把问题分解成同类的较小问题，直到满足终止条件。", section="递归")),
])
def test_named_life_first_step_requires_the_concepts_actual_definition(question, source):
    assert can_skip_planner(question, "socratic", [source], single_step_scope=True)
    assert not can_skip_planner(question, "socratic", [source])
    assert not can_skip_planner(question, "solve", [source], single_step_scope=True)


@pytest.mark.parametrize("source", [
    body("队列是一种先进先出的数据结构，最先放入的数据最先取出。", section="递归"),
    body("前面的章节提到这个重要术语，我们称之为递归(recursion)。", section="递归"),
    body("递归可以在后面的章节找到，本章需要先学习其他术语。", section="递归"),
    body("递归", section="递归"),
    body("在某个过程当中，又执行了这个过程，我们称之为递归(recursion)。", evidence_kind="image"),
])
def test_named_life_first_step_does_not_use_incidental_mentions_or_missing_definitions(source):
    assert not can_skip_planner("我不理解递归，先用生活情境只问我一个入门问题，不要先给答案",
                                "socratic", [source], single_step_scope=True)


@pytest.mark.parametrize("requirement", [
    "并计算3次调用的结果", "并求出最终答案", "并按原图核对箭头", "并比较栈和队列",
    "并检查我给出的递归代码", "并说明具体实现边界", "并纠正我的调用轨迹",
    "并满足老师给定的条件", "并读取我的学习画像",
])
def test_named_life_first_step_does_not_swallow_additional_tasks(requirement):
    question = "我不理解栈，先用生活情境只问我一个入门问题，不要先给答案，" + requirement
    assert not can_skip_planner(question, "socratic", [body()], single_step_scope=True,
                                qualitative_analogy=True)


@pytest.mark.parametrize("question,mode,sources,flags", [
    (None, "explain", [body()], {}), ("", "explain", [body()], {}),
    ("栈是什么？", "invalid", [body()], {}), ("栈是什么？", [], [body()], {}),
    ("栈是什么？", "explain", [], {}),
    ("栈是什么？", "explain", "body", {}), ("栈是什么？", "explain", None, {}),
    ("栈是什么？", "explain", [None, {}], {}),
    ("栈是什么？", "explain", [body()], {"qualitative_analogy": "yes"}),
])
def test_invalid_or_empty_inputs_keep_planner(question, mode, sources, flags):
    assert not can_skip_planner(question, mode, sources, **flags)
