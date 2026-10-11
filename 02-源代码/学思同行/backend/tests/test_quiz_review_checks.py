"""Protocol invariants do not certify the semantic truth of model verdicts."""
import copy
import json

import pytest

from backend.app.quiz_review import (MAX_OPTION_REASON, MAX_REVIEW_CHARACTERS,
                                     QuizReviewProtocolError, parse_quiz_review)
from backend.tests.quiz_review_helpers import fake_quiz_review


QUESTIONS = [{"id": "question-1", "answer": "B"}, {"id": "question-2", "answer": "D"}]


def parse(value, questions=QUESTIONS):
    return parse_quiz_review(json.dumps(value, ensure_ascii=False), questions)


def test_complete_review_accepts_independent_correct_keys_and_order():
    value = fake_quiz_review(QUESTIONS)
    value["question_checks"].reverse()
    value["question_checks"][0]["options"].reverse()
    assert parse(value) == value
    assert parse_quiz_review("```json\n" + json.dumps(value) + "\n```", QUESTIONS) == value


@pytest.mark.parametrize("case", [
    "legacy-total-only", "missing-checks", "missing-question", "extra-question", "unknown-question",
    "duplicate-question", "null-checks", "checks-object", "integer-approved", "string-approved",
    "approved-with-issues", "rejected-without-issues", "unknown-field", "extra-analysis",
    "unknown-issue-kind", "non-string-kind", "empty-issue-reason", "long-issue-reason", "hidden-issue-reason",
])
def test_incomplete_or_inconsistent_top_level_ledgers_fail_as_protocol_errors(case):
    value = fake_quiz_review(QUESTIONS)
    if case in {"legacy-total-only", "missing-checks"}:
        value.pop("question_checks")
    elif case == "missing-question":
        value["question_checks"].pop()
    elif case == "extra-question":
        value["question_checks"].append(copy.deepcopy(value["question_checks"][0]))
    elif case == "unknown-question":
        value["question_checks"][0]["question_id"] = "another-run-question"
    elif case == "duplicate-question":
        value["question_checks"][1]["question_id"] = QUESTIONS[0]["id"]
    elif case == "null-checks":
        value["question_checks"] = None
    elif case == "checks-object":
        value["question_checks"] = {}
    elif case == "integer-approved":
        value["approved"] = 1
    elif case == "string-approved":
        value["approved"] = "true"
    elif case in {"approved-with-issues", "unknown-issue-kind", "non-string-kind", "empty-issue-reason", "long-issue-reason", "hidden-issue-reason"}:
        value["issues"] = [{"kind": "condition", "detail": "题干未说明适用条件"}]
        if case != "approved-with-issues":
            value["approved"] = False
            issue = value["issues"][0]
            if case == "unknown-issue-kind":
                issue["kind"] = "style"
            elif case == "non-string-kind":
                issue["kind"] = []
            elif case == "empty-issue-reason":
                issue["detail"] = " "
            elif case == "long-issue-reason":
                issue["detail"] = "x" * 361
            elif case == "hidden-issue-reason":
                issue["detail"] = "<think>hidden"
    elif case == "rejected-without-issues":
        value["approved"] = False
    elif case in {"unknown-field", "extra-analysis"}:
        value["analysis"] = "hidden"
    with pytest.raises(QuizReviewProtocolError):
        parse(value)


@pytest.mark.parametrize("case", [
    "missing-option", "extra-option", "duplicate-key", "unknown-key", "lowercase-key", "list-key",
    "missing-reason", "empty-reason", "long-reason", "hidden-reason", "integer-reason",
    "unknown-verdict", "list-verdict", "missing-flag", "integer-flag", "string-flag",
    "question-id-list", "extra-question-field", "extra-option-field",
])
def test_question_and_option_protocol_never_guesses_missing_or_invalid_values(case):
    value = fake_quiz_review(QUESTIONS)
    item = value["question_checks"][0]
    option = item["options"][0]
    if case == "missing-option":
        item["options"].pop()
    elif case == "extra-option":
        item["options"].append(copy.deepcopy(option))
    elif case == "duplicate-key":
        item["options"][1]["key"] = option["key"]
    elif case in {"unknown-key", "lowercase-key", "list-key"}:
        option["key"] = {"unknown-key": "E", "lowercase-key": "a", "list-key": []}[case]
    elif case == "missing-reason":
        option.pop("reason")
    elif case in {"empty-reason", "long-reason", "hidden-reason", "integer-reason"}:
        option["reason"] = {"empty-reason": "\n ", "long-reason": "x" * (MAX_OPTION_REASON + 1),
                            "hidden-reason": "<analysis>hidden", "integer-reason": 4}[case]
    elif case in {"unknown-verdict", "list-verdict"}:
        option["verdict"] = "probably" if case == "unknown-verdict" else []
    elif case == "missing-flag":
        item.pop("source_supported")
    elif case in {"integer-flag", "string-flag"}:
        item["source_supported"] = 1 if case == "integer-flag" else "true"
    elif case == "question-id-list":
        item["question_id"] = []
    elif case == "extra-question-field":
        item["analysis"] = "hidden"
    elif case == "extra-option-field":
        option["analysis"] = "hidden"
    with pytest.raises(QuizReviewProtocolError):
        parse(value)


@pytest.mark.parametrize("problem", ["conditions", "sources", "uncertain", "multiple-correct", "none-correct", "answer-mismatch"])
def test_aggregate_true_cannot_override_negative_or_ambiguous_option_judgments(problem):
    value = fake_quiz_review(QUESTIONS)
    item = value["question_checks"][0]
    if problem == "conditions":
        item["conditions_complete"] = False
    elif problem == "sources":
        item["source_supported"] = False
    elif problem == "uncertain":
        item["options"][0]["verdict"] = "uncertain"
    elif problem == "multiple-correct":
        item["options"][0]["verdict"] = "correct"
    elif problem == "none-correct":
        item["options"][1]["verdict"] = "incorrect"
    else:
        item["options"][1]["verdict"] = "incorrect"
        item["options"][0]["verdict"] = "correct"
    with pytest.raises(QuizReviewProtocolError):
        parse(value)


def test_complete_explicit_semantic_rejection_is_preserved_for_bounded_regeneration():
    value = fake_quiz_review(QUESTIONS, False, [{"kind": "logic", "detail": "第1题A与B在题干条件下可同时成立。"}])
    value["question_checks"][0]["options"][0].update(verdict="correct", reason="该情景符合题干，与B重叠。")
    assert parse(value)["approved"] is False


@pytest.mark.parametrize("content", [
    "", "null", "[]", "{", "before {} after", '"text"',
    '{"approved":false,"approved":true,"issues":[],"question_checks":[]}',
    '{"approved":true,"issues":[],"question_checks":[],"other":NaN}',
    '```json\n{"approved":true,"issues":[],"question_checks":[]}',
    '{"approved":' + '9' * 5000 + ',"issues":[],"question_checks":[]}',
    "x" * (MAX_REVIEW_CHARACTERS + 1), None,
])
def test_raw_protocol_is_bounded_and_duplicate_json_fields_are_not_silently_overwritten(content):
    with pytest.raises(QuizReviewProtocolError):
        parse_quiz_review(content, QUESTIONS)


@pytest.mark.parametrize("questions", [[], [{"id": "same", "answer": "A"}] * 2,
                                      [{"id": "q", "answer": "E"}], [{"id": [], "answer": "A"}],
                                      [{"id": "q" + str(i), "answer": "A"} for i in range(6)]])
def test_invalid_expected_question_identity_or_count_is_not_reviewable(questions):
    with pytest.raises(QuizReviewProtocolError):
        parse(fake_quiz_review(QUESTIONS), questions)
