"""Original offline protocol fixtures, not evidence of model content quality.

The temporary import name gives the isolated candidate its eventual app package
context without copying it into or changing any stable backend/app file.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from backend.app.provider import ProviderError
from backend.app.quiz_review import QuizReviewProtocolError, build_quiz_difficulty_contract


_CANDIDATE_PATH = Path(__file__).with_name("quiz_application.py")
if _CANDIDATE_PATH.exists():
    _SPEC = importlib.util.spec_from_file_location("backend.app._quiz_application_candidate", _CANDIDATE_PATH)
    candidate = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(candidate)
else:
    # This same test file can move into backend/tests after the implementation
    # moves into backend/app; no temporary import then remains necessary.
    from backend.app import quiz_application as candidate


def context():
    return [
        {"id": "chain-original", "text": "本资料采用结点串联记录链路，原例从入口经单个中继结点到出口。"},
        {"id": "device-background", "text": "设备外壳上有2种标记；这个背景数量不是链路状态。"},
        {"id": "independent-rule", "text": "逐一访问后累加每个结点的规定操作成本。"},
    ]


def basis():
    return {"original_source_ids": ["chain-original"], "original_case": "入口经单个中继到出口。",
            "new_condition": "新链路再增加一个中继，计数包含入口和出口。",
            "required_operation": "按明确的结点与操作计数口径重新累计。"}


def question(question_id="q1", *, with_basis=True):
    value = {"id": question_id, "type": "single_choice", "stem": "按本资料原理，对新增中继的链路累计结点操作。" + question_id,
             "options": [{"key": key, "text": "原创结论" + key} for key in "ABCD"],
             "answer": "A", "explanation": "解析按新增中继及题设口径逐一累计结点。",
             "concept": "结点累计", "difficulty": "进阶", "source_ids": ["independent-rule"]}
    if with_basis:
        value["application_basis"] = basis()
    return value


def review(questions, *, approved=True):
    return {"approved": approved,
            "issues": [] if approved else [{"kind": "condition", "detail": "题干没有给出必需的完整初始状态。"}],
            "question_checks": [{"question_id": q["id"], "conditions_complete": True, "source_supported": True,
                "options": [{"key": key, "verdict": "correct" if key == q["answer"] else "incorrect",
                             "reason": "依据给定口径独立核对该结论。"} for key in "ABCD"]} for q in questions],
            "application_checks": [{"question_id": q["id"], "has_new_condition": True, "requires_inference": True,
                "explanation_uses_new_condition": True, "requires_multiple_steps": False,
                "reason": "新增中继改变原情形，题干要求累计，解析使用此变化。"} for q in questions]}


def parse(value, questions, *, difficulty="进阶", generation_context=None, contract=None):
    return candidate.parse_quiz_application_review(json.dumps(value, ensure_ascii=False), questions,
        difficulty_contract=contract or build_quiz_difficulty_contract(difficulty, len(questions)),
        generation_context=context() if generation_context is None else generation_context)


def test_basis_is_retained_without_copying_other_raw_fields_or_mutating_inputs():
    questions = [question()]
    raw = [{**deepcopy(questions[0]), "answer": "D", "explanation": "未经校验的另一个字段不应复制。"}]
    raw[0]["application_basis"]["new_condition"] += "  "
    before = deepcopy((raw, questions, context()))
    result = candidate.retain_application_bases(raw, questions, context())
    assert result[0]["answer"] == "A"
    assert result[0]["explanation"] == questions[0]["explanation"]
    assert result[0]["application_basis"] == basis()
    assert result[0]["source_ids"] == ["independent-rule"]
    assert (raw, questions, context()) == before
    result[0]["application_basis"]["original_source_ids"].append("device-background")
    assert raw[0]["application_basis"]["original_source_ids"] == ["chain-original"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_null_basis_is_valid_metadata_but_not_an_application_claim(missing):
    q = question(with_basis=False)
    raw = deepcopy(q)
    if not missing:
        raw["application_basis"] = None
    q["application_basis"] = {"unsafe": "must not survive"}
    result = candidate.retain_application_bases([raw], [q], context())
    assert result[0]["application_basis"] is None


def test_original_example_may_be_in_full_context_outside_correctness_citations():
    q = question()
    assert "chain-original" not in q["source_ids"]
    assert candidate.retain_application_bases([deepcopy(q)], [q], context())[0]["application_basis"] == basis()


@pytest.mark.parametrize("change", ["unknown_id", "duplicate_id", "empty_ids", "too_many_ids", "string_ids", "numeric_id",
                                   "missing_field", "extra_answer", "string_basis", "blank_text", "long_text", "nonstring_text", "think_text"])
def test_bad_generation_basis_stops_with_bounded_provider_error(change):
    q = question()
    value = q["application_basis"]
    if change == "unknown_id":
        value["original_source_ids"] = ["unseen-material"]
    elif change == "duplicate_id":
        value["original_source_ids"] *= 2
    elif change == "empty_ids":
        value["original_source_ids"] = []
    elif change == "too_many_ids":
        value["original_source_ids"] *= 5
    elif change == "string_ids":
        value["original_source_ids"] = "chain-original"
    elif change == "numeric_id":
        value["original_source_ids"] = [7]
    elif change == "missing_field":
        value.pop("new_condition")
    elif change == "extra_answer":
        value["answer"] = "A"
    elif change == "string_basis":
        q["application_basis"] = "application"
    elif change == "blank_text":
        value["original_case"] = "  "
    elif change == "long_text":
        value["required_operation"] = "长" * 121
    elif change == "nonstring_text":
        value["new_condition"] = True
    else:
        value["new_condition"] = "<think>private chain"
    with pytest.raises(candidate.QuizApplicationProtocolError) as error:
        candidate.retain_application_bases([q], [deepcopy(q)], context())
    assert isinstance(error.value, ProviderError)
    assert "unseen-material" not in str(error.value) and "private chain" not in str(error.value)


@pytest.mark.parametrize("generation_context", [[], "not-list", [{"id": "x", "text": ""}],
    [{"id": "x", "text": "正文"}, {"id": "x", "text": "另一个正文"}],
    [{"id": str(i), "text": "正文"} for i in range(17)]])
def test_bad_generation_context_is_not_silently_accepted(generation_context):
    q = question()
    with pytest.raises(candidate.QuizApplicationProtocolError):
        candidate.retain_application_bases([q], [q], generation_context)


@pytest.mark.parametrize("change", ["count", "order", "duplicate_identity"])
def test_retention_requires_unchanged_validated_order_and_identity(change):
    questions = [question("q1"), question("q2")]
    raw = deepcopy(questions)
    if change == "count":
        raw.pop()
    elif change == "order":
        raw.reverse()
    else:
        questions[1]["id"] = "q1"
    with pytest.raises(candidate.QuizApplicationProtocolError):
        candidate.retain_application_bases(raw, questions, context())


def test_advanced_three_questions_require_two_independent_applications():
    questions = [question("q1"), question("q2"), question("q3")]
    value = review(questions)
    value["application_checks"][2]["requires_inference"] = False
    original = deepcopy((value, questions))
    result = parse(value, questions)
    assert result["approved"] is True
    assert result["application_summary"]["application_count"] == 2
    assert result["application_summary"]["application_question_ids"] == ["q1", "q2"]
    assert result["application_summary"]["minimum_application_questions"] == 2
    assert (value, questions) == original


@pytest.mark.parametrize("false_flag", ["has_new_condition", "requires_inference", "explanation_uses_new_condition"])
def test_self_declaration_cannot_override_any_independent_required_check(false_flag):
    questions = [question()]
    value = review(questions)
    value["application_checks"][0][false_flag] = False
    result = parse(value, questions)
    assert result["approved"] is False
    assert result["issues"][0]["kind"] == "coverage"
    assert result["application_summary"]["application_count"] == 0
    assert questions[0]["answer"] == "A"


def test_wellformed_approval_with_insufficient_coverage_becomes_semantic_rejection():
    questions = [question("q1"), question("q2"), question("q3")]
    value = review(questions)
    for item in value["application_checks"][1:]:
        item["has_new_condition"] = False
        item["reason"] = "任务沿用来源原情形，变化自述与题干不符。"
    result = parse(value, questions)
    assert result["approved"] is False
    assert result["issues"][0]["kind"] == "coverage"
    assert "1/3" in result["issues"][0]["detail"] and "至少要求2题" in result["issues"][0]["detail"]
    assert result["question_checks"] == value["question_checks"]
    assert result["application_checks"] == value["application_checks"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_null_basis_cannot_count_even_if_reviewer_claims_all_checks_true(missing):
    q = question(with_basis=False)
    if not missing:
        q["application_basis"] = None
    result = parse(review([q]), [q])
    assert result["approved"] is False
    assert result["application_summary"]["application_count"] == 0
    assert result["application_summary"]["missing_basis_question_ids"] == ["q1"]
    assert "未计入" in result["issues"][0]["detail"]


def test_basis_meaning_is_not_certified_by_string_comparison_or_numeric_presence():
    q = question()
    q["application_basis"]["original_case"] = "一个设定中出现2种标记。"
    q["application_basis"]["new_condition"] = "另一不同操作使用2次访问。"
    retained = candidate.retain_application_bases([deepcopy(q)], [q], context())
    assert retained[0]["application_basis"]["new_condition"] == q["application_basis"]["new_condition"]
    # The pure validator does not invent a semantic rejection from a repeated
    # number. Conversely, an independent negative flag wins over this claim.
    value = review(retained)
    value["application_checks"][0]["has_new_condition"] = False
    assert parse(value, retained)["approved"] is False


def test_identical_case_descriptions_remain_untrusted_until_review():
    q = question()
    q["application_basis"]["new_condition"] = q["application_basis"]["original_case"]
    retained = candidate.retain_application_bases([deepcopy(q)], [q], context())
    value = review(retained)
    value["application_checks"][0]["has_new_condition"] = False
    assert parse(value, retained)["approved"] is False


def test_challenge_requires_every_question_and_distinct_steps():
    questions = [question("q1"), question("q2")]
    value = review(questions)
    value["application_checks"][0]["requires_multiple_steps"] = True
    result = parse(value, questions, difficulty="挑战")
    assert result["approved"] is False
    assert result["application_summary"]["application_count"] == 2
    assert result["application_summary"]["challenge_failure_question_ids"] == ["q2"]
    assert "挑战" in result["issues"][0]["detail"]
    value["application_checks"][1]["requires_multiple_steps"] = True
    result = parse(value, questions, difficulty="挑战")
    assert result["approved"] is True
    assert result["application_summary"]["multi_step_question_ids"] == ["q1", "q2"]


def test_basic_question_without_basis_is_legal_and_does_not_become_application():
    questions = [question(with_basis=False)]
    result = parse(review(questions), questions, difficulty="基础")
    assert result["approved"] is True
    assert result["application_summary"]["application_count"] == 0
    assert result["application_summary"]["minimum_application_questions"] == 0


def test_wellformed_explicit_rejection_is_preserved_with_independent_summary():
    questions = [question()]
    value = review(questions, approved=False)
    result = parse(value, questions)
    assert result["approved"] is False
    assert result["issues"] == value["issues"]
    assert result["application_summary"]["application_count"] == 1


def test_reordered_application_checks_produce_stable_question_order():
    questions = [question("q1"), question("q2"), question("q3")]
    value = review(questions)
    value["application_checks"].reverse()
    assert parse(value, questions)["application_summary"]["application_question_ids"] == ["q1", "q2", "q3"]


@pytest.mark.parametrize("change", ["foreign_id", "duplicate_id", "missing_question", "extra_question", "string_bool", "integer_bool",
                                   "missing_flag", "extra_field", "empty_reason", "long_reason", "think_reason"])
def test_application_ledger_protocol_failures_stop_instead_of_semantic_retry(change):
    questions = [question("q1"), question("q2")]
    value = review(questions)
    item = value["application_checks"][0]
    if change == "foreign_id":
        item["question_id"] = "foreign"
    elif change == "duplicate_id":
        value["application_checks"][1]["question_id"] = "q1"
    elif change == "missing_question":
        value["application_checks"].pop()
    elif change == "extra_question":
        value["application_checks"].append(deepcopy(item))
    elif change == "string_bool":
        item["has_new_condition"] = "true"
    elif change == "integer_bool":
        item["requires_inference"] = 1
    elif change == "missing_flag":
        item.pop("requires_multiple_steps")
    elif change == "extra_field":
        item["answer"] = "A"
    elif change == "empty_reason":
        item["reason"] = " "
    elif change == "long_reason":
        item["reason"] = "长" * 121
    else:
        item["reason"] = "<analysis>hidden"
    with pytest.raises(candidate.QuizApplicationProtocolError):
        parse(value, questions)


@pytest.mark.parametrize("raw", [
    '{"approved":true,"approved":true,"issues":[],"question_checks":[],"application_checks":[]}',
    '{"approved":true,"issues":[],"question_checks":[],"application_checks":[{"question_id":"q1","question_id":"q2"}]}',
    '{"approved":true,"issues":[],"question_checks":[],"application_checks":NaN}',
    'prefix {"approved":true}',
    '```json\n{"approved":true}',
])
def test_strict_raw_json_rejects_duplicates_constants_and_partial_outputs(raw):
    with pytest.raises(candidate.QuizApplicationProtocolError):
        candidate.parse_quiz_application_review(raw, [question()],
            difficulty_contract=build_quiz_difficulty_contract("进阶", 1), generation_context=context())


def test_missing_or_extra_top_level_field_is_protocol_failure():
    questions = [question()]
    value = review(questions)
    value.pop("application_checks")
    with pytest.raises(candidate.QuizApplicationProtocolError):
        parse(value, questions)
    value = review(questions)
    value["answer"] = "A"
    with pytest.raises(candidate.QuizApplicationProtocolError):
        parse(value, questions)


def test_existing_correctness_ledger_cannot_be_bypassed_by_good_application_checks():
    questions = [question()]
    value = review(questions)
    value["question_checks"][0]["options"][1]["verdict"] = "correct"
    with pytest.raises(QuizReviewProtocolError):
        parse(value, questions)
    value = review(questions)
    value["question_checks"][0]["source_supported"] = False
    with pytest.raises(QuizReviewProtocolError):
        parse(value, questions)


@pytest.mark.parametrize("change", ["minimum", "provenance", "requested_count", "definition", "bool_minimum", "bool_count"])
def test_server_contract_cannot_be_downgraded_by_caller_supplied_fields(change):
    questions = [question()]
    contract = build_quiz_difficulty_contract("进阶", 1)
    if change == "minimum":
        contract["minimum_application_questions"] = 0
    elif change == "provenance":
        contract["provenance"] = "model"
    elif change == "requested_count":
        contract["requested_count"] = 2
    elif change == "bool_minimum":
        contract["minimum_application_questions"] = True
    elif change == "bool_count":
        contract["requested_count"] = True
    else:
        contract["application_definition"] = "标签足够。"
    with pytest.raises(candidate.QuizApplicationProtocolError):
        parse(review(questions), questions, contract=contract)


def test_full_review_revalidates_basis_against_exact_generation_context():
    questions = [question()]
    questions[0]["application_basis"]["original_source_ids"] = ["outside"]
    with pytest.raises(candidate.QuizApplicationProtocolError):
        parse(review(questions), questions)


def test_complete_fenced_json_remains_compatible():
    questions = [question()]
    raw = "```json\n" + json.dumps(review(questions), ensure_ascii=False) + "\n```"
    result = candidate.parse_quiz_application_review(raw, questions,
        difficulty_contract=build_quiz_difficulty_contract("进阶", 1), generation_context=context())
    assert result["approved"] is True


def test_prompt_replaces_schema_and_keeps_correctness_source_boundary():
    prompt = candidate.build_application_review_prompt()
    assert prompt.count("精确字段approved,issues,question_checks,application_checks。") == 1
    assert "精确字段approved,issues,question_checks。格式：" not in prompt
    assert "答案解析一致且无实质问题" in prompt
    assert "每题正确性与答案解析支持仍只依据该题source_ids" in prompt
    assert "未引用片段仅用于核对原例差异" in prompt
    assert "application_basis是待核对自述" in prompt
    assert "不因某数字出现或未出现就判断新旧" in prompt
    assert "基础允许没有应用题" in prompt
    assert "不能改题、答案、解析" in prompt
    assert "不写答案、结果或解题金标" in candidate.QUIZ_APPLICATION_BASIS_PROMPT


@pytest.mark.parametrize("base_prompt", [None, "new unknown schema", candidate._OLD_SCHEMA_START * 2 + candidate._OLD_SCHEMA_END,
    candidate._OLD_SCHEMA_END + candidate._OLD_SCHEMA_START])
def test_changed_or_ambiguous_base_prompt_schema_fails_closed(base_prompt):
    with pytest.raises(candidate.QuizApplicationProtocolError):
        candidate.build_application_review_prompt(base_prompt)
