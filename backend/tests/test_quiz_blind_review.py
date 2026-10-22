"""Offline blind ledger contracts; fixtures do not prove model correctness."""
from copy import deepcopy
import json

import pytest

from backend.app.quiz_review import (
    QUIZ_BLIND_REVIEW_PROMPT,
    QuizReviewProtocolError,
    build_quiz_difficulty_contract,
    parse_quiz_review,
)


def question(question_id="q1", answer="A"):
    return {"id": question_id, "stem": "原创数量题，按题干明确的操作数计算。",
            "options": [{"key": key, "text": f"原创选项{key}"} for key in "ABCD"],
            "answer": answer, "explanation": "候选解析仅留在服务器，不提供给盲解阶段。"}


def ledger(questions, selected=None, approved=True):
    selected = selected or {q["id"]: q["answer"] for q in questions}
    return {
        "approved": approved,
        "issues": [] if approved else [{"kind": "condition", "detail": "题干缺少确定状态所需的条件。"}],
        "question_checks": [
            {"question_id": q["id"], "conditions_complete": approved, "source_supported": True,
             "options": [{"key": key, "verdict": "correct" if key == selected[q["id"]] else "incorrect",
                          "reason": "根据完整题干独立计算并排除其他数量。"} for key in "ABCD"]}
            for q in questions
        ],
    }


def parse(value, questions, **kwargs):
    return parse_quiz_review(json.dumps(value, ensure_ascii=False), questions, **kwargs)


def test_blind_independent_answer_disagreement_is_semantic_rejection():
    questions = [question(answer="B")]
    value = ledger(questions, {"q1": "C"})
    original = deepcopy(value)
    result = parse(value, questions, blind=True)
    assert result["approved"] is False
    assert result["issues"] == [{"kind": "logic", "detail": "第1题独立盲解的唯一选项为C，与候选答案B不一致，需重新生成题目与答案。"}]
    assert result["question_checks"] == original["question_checks"]
    assert value == original and questions[0]["answer"] == "B"


def test_full_review_answer_disagreement_remains_protocol_error():
    questions = [question(answer="B")]
    with pytest.raises(QuizReviewProtocolError, match="总判通过"):
        parse(ledger(questions, {"q1": "C"}), questions)


def test_blind_correct_matching_answer_preserves_complete_ledger():
    questions = [question()]
    value = ledger(questions)
    assert parse(value, questions, blind=True) == value
    assert parse(value, questions) == value


def test_multiple_blind_disagreements_have_stable_input_question_numbers():
    questions = [question("first", "A"), question("second", "B")]
    value = ledger(questions, {"first": "D", "second": "C"})
    value["question_checks"].reverse()
    result = parse(value, questions, blind=True)
    assert result["approved"] is False and len(result["issues"]) == 2
    assert result["issues"][0]["detail"].startswith("第2题")
    assert result["issues"][1]["detail"].startswith("第1题")


@pytest.mark.parametrize("change", ["multiple_correct", "none_correct", "uncertain", "source_false", "conditions_false"])
def test_blind_approval_cannot_hide_nonunique_or_unsupported_ledger(change):
    questions = [question()]
    value = ledger(questions)
    item = value["question_checks"][0]
    if change == "multiple_correct":
        item["options"][1]["verdict"] = "correct"
    elif change == "none_correct":
        item["options"][0]["verdict"] = "incorrect"
    elif change == "uncertain":
        item["options"][1]["verdict"] = "uncertain"
    elif change == "source_false":
        item["source_supported"] = False
    else:
        item["conditions_complete"] = False
    with pytest.raises(QuizReviewProtocolError, match="总判通过"):
        parse(value, questions, blind=True)


def test_blind_explicit_wellformed_rejection_is_not_reclassified():
    questions = [question(answer="B")]
    value = ledger(questions, {"q1": "C"}, approved=False)
    assert parse(value, questions, blind=True) == value


@pytest.mark.parametrize("change", ["foreign_id", "missing_question", "duplicate_question", "missing_option", "duplicate_option", "extra_field", "string_bool"])
def test_blind_protocol_still_enforces_identity_and_complete_ledger(change):
    questions = [question()]
    value = ledger(questions)
    item = value["question_checks"][0]
    if change == "foreign_id":
        item["question_id"] = "outside"
    elif change == "missing_question":
        value["question_checks"] = []
    elif change == "duplicate_question":
        questions.append(question("q2"))
        value["question_checks"].append(deepcopy(item))
    elif change == "missing_option":
        item["options"].pop()
    elif change == "duplicate_option":
        item["options"][1]["key"] = "A"
    elif change == "extra_field":
        item["explanation_valid"] = True
    else:
        item["conditions_complete"] = "true"
    with pytest.raises(QuizReviewProtocolError):
        parse(value, questions, blind=True)


@pytest.mark.parametrize("raw", [
    '{"approved":true,"approved":true,"issues":[],"question_checks":[]}',
    '{"approved":true,"issues":[],"question_checks":[{"question_id":"q1","conditions_complete":true,"source_supported":true,"options":[{"key":"A","key":"A","verdict":"correct","reason":"依据"}]}]}',
])
def test_blind_duplicate_json_fields_are_rejected_before_use(raw):
    with pytest.raises(QuizReviewProtocolError, match="JSON字段重复"):
        parse_quiz_review(raw, [question()], blind=True)


def test_blind_mode_flag_cannot_be_truthy_nonboolean():
    questions = [question()]
    with pytest.raises(QuizReviewProtocolError, match="盲解模式"):
        parse(ledger(questions), questions, blind="true")


def test_blind_prompt_separates_new_input_application_from_later_explanation_review():
    contract = build_quiz_difficulty_contract("进阶", 3)
    assert contract["minimum_application_questions"] == 2
    assert "本阶段不提供候选answer或explanation" in QUIZ_BLIND_REVIEW_PROMPT
    assert "不要求每个片段单独支持所有结论" in QUIZ_BLIND_REVIEW_PROMPT
    assert "来源无需逐字出现新问题、数字或每步计算" in QUIZ_BLIND_REVIEW_PROMPT
    assert "难度仅按题干实际要求评估" in QUIZ_BLIND_REVIEW_PROMPT
    assert "本阶段不检查未提供的解析是否实际使用新输入" in QUIZ_BLIND_REVIEW_PROMPT
    assert "difficulty_contract.minimum_application_questions" in QUIZ_BLIND_REVIEW_PROMPT
    assert "challenge_definition" in QUIZ_BLIND_REVIEW_PROMPT


def test_blind_fenced_json_keeps_existing_transport_compatibility():
    questions = [question()]
    value = ledger(questions)
    raw = "```json\n" + json.dumps(value, ensure_ascii=False) + "\n```"
    assert parse_quiz_review(raw, questions, blind=True) == value
