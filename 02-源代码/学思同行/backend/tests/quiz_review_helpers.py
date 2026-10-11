"""Mechanical review ledgers for FakeProvider tests, not semantic validators."""
from __future__ import annotations

import json


class BlindReviewTestMixin:
    """Mechanical stub for unrelated routing/persistence tests only.

    It deliberately uses fixture answers. Dedicated blind-wire and pipeline
    tests override this method; these old fixtures provide no semantic proof.
    """

    async def review_quiz_blind(self, questions, **kwargs):
        return {"content": json.dumps(fake_quiz_review(questions), ensure_ascii=False),
                "model": "fake-test-blind-review-model"}


def fake_quiz_review(questions, approved=True, issues=None):
    checks = [{"question_id": question["id"], "conditions_complete": True, "source_supported": True,
               "options": [{"key": key, "verdict": "correct" if key == question["answer"] else "incorrect",
                            "reason": "本测试替身指定的判断，仅用于协议和持久化边界。"} for key in "ABCD"]}
              for question in questions]
    if not approved and checks:
        checks[0]["conditions_complete"] = False
    return {"approved": approved, "issues": [] if issues is None else issues, "question_checks": checks}


def fake_application_basis(source_ids, index=0):
    """Mechanical schema/provenance fixture, never a novelty or answer oracle."""
    return {"original_source_ids": list(source_ids),
            "original_case": "机械夹具原情形；不是对教材原例的语义分析。",
            "new_condition": f"机械夹具新条件{index}，只验证传输和持久化协议。",
            "required_operation": "机械夹具指定的累计与判断，不证明实际应用难度。"}


def fake_quiz_application_review(questions, approved=True, issues=None):
    """Four-field full-review stub for nonsemantic pipeline tests only.

    Flags deliberately follow the presence of fixture metadata; they do not
    inspect a source, solve a question, or certify an actual difficulty level.
    The old three-field helper remains unchanged for blind/protocol tests.
    """
    value = fake_quiz_review(questions, approved, issues)
    value["application_checks"] = [
        {"question_id": question["id"],
         "has_new_condition": question.get("application_basis") is not None,
         "requires_inference": question.get("application_basis") is not None,
         "explanation_uses_new_condition": question.get("application_basis") is not None,
         "requires_multiple_steps": question.get("application_basis") is not None,
         "reason": "机械夹具指定的应用账目，仅验证协议和入库边界，不证明真实难度。"}
        for question in questions]
    return value


def complete_legacy_quiz_decision(decision, questions):
    """Expand intentional old fake decisions; leave malformed fixtures malformed."""
    if isinstance(decision, dict) and set(decision) == {"approved", "issues"} and type(decision["approved"]) is bool:
        return fake_quiz_review(questions, decision["approved"], decision["issues"])
    return decision


def complete_legacy_quiz_application_decision(decision, questions):
    """Expand only intentionally old decision stubs into the new full ledger."""
    if isinstance(decision, dict) and set(decision) == {"approved", "issues"} and type(decision["approved"]) is bool:
        return fake_quiz_application_review(questions, decision["approved"], decision["issues"])
    return decision
