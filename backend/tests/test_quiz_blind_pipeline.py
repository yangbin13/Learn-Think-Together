"""Blind-wire privacy and bounded persistence control; fixtures are not semantic proof."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.app.provider import Provider, ProviderError, extract_json
from backend.app.quiz_review import QUIZ_BLIND_REVIEW_PROMPT
from backend.tests.quiz_review_helpers import fake_quiz_review, fake_quiz_application_review
from backend.tests.test_quiz_generation_routing import routed_app
from backend.tests.test_provider_boundaries import adapter


def test_real_provider_adapter_blinds_candidate_and_uses_review_model():
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content='{"approved":true}', tool_calls=[]))],
            usage=None, model="observed-blind-model")

    provider = adapter(create)
    provider.settings.review_model = "configured-review-model"
    provider.settings.review_thinking = True
    provider.settings.review_thinking_budget = 512
    questions = [{"id": "q", "type": "single_choice", "stem": "原创题干", "options": [],
                  "concept": "索引", "source_ids": ["s"], "answer": "secret-candidate-answer",
                  "explanation": "secret-candidate-explanation", "internal_hint": "private-hint",
                  "application_basis": {"new_condition": "private-application-basis"}}]
    result = asyncio.run(provider.review_quiz_blind(questions, evidence=[{"id": "s", "text": "原创原理"}],
        source_scopes={"s": ["索引"]}, scope_notes={}, difficulty_contract={"requested_difficulty": "进阶"}))
    assert result["model"] == "observed-blind-model"
    call = seen[0]
    assert call["model"] == "configured-review-model"
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["content"] == QUIZ_BLIND_REVIEW_PROMPT
    payload = json.loads(call["messages"][1]["content"])
    assert set(payload["questions"][0]) == {"id", "type", "stem", "options", "concept", "source_ids"}
    assert "secret-candidate" not in call["messages"][1]["content"]
    assert "private-hint" not in call["messages"][1]["content"]
    assert "application_basis" not in call["messages"][1]["content"]
    assert "private-application-basis" not in call["messages"][1]["content"]
    assert questions[0]["answer"] == "secret-candidate-answer"


@pytest.mark.parametrize("text", [
    '{"questions":[{"answer":"A","answer":"B"}]}',
    '{"questions":[{"options":[{"key":"A","key":"B"}]}]}',
    '{"questions":[],"n":NaN}', '{"questions":[],"n":Infinity}',
    '前言{"questions":[]}', '```json\n{"questions":[]}',
])
def test_assessment_json_cannot_collapse_duplicates_constants_or_extra_prose(text):
    with pytest.raises(ProviderError, match="JSON须为完整对象"):
        extract_json(text, strict=True)


@pytest.mark.parametrize("text", ['{"questions":[]}', '```json\n{"questions":[]}\n```'])
def test_assessment_json_accepts_complete_objects_and_complete_fences(text):
    assert extract_json(text, strict=True) == {"questions": []}


@pytest.mark.parametrize("behavior,success,generations,full_reviews", [
    ("match", True, 1, 1), ("wrong-first", True, 2, 1),
    ("wrong-always", False, 2, 0), ("malformed", False, 1, 0),
    ("outage", False, 1, 0), ("explanation-rejected", False, 2, 2),
])
def test_blind_solution_precedes_explanation_and_never_persists_failure(
        routed_app, behavior, success, generations, full_reviews):
    client, app, provider, course = routed_app
    blind_calls = []

    async def blind(questions, **kwargs):
        blind_calls.append((questions, kwargs))
        if behavior == "outage":
            raise ProviderError("独立盲解服务临时不可用")
        if behavior == "malformed":
            return {"content": '{"approved":true,"issues":[],"question_checks":[]}'}
        value = fake_quiz_review(questions)
        if behavior == "wrong-always" or (behavior == "wrong-first" and len(blind_calls) == 1):
            for check in value["question_checks"]:
                for option in check["options"]:
                    option["verdict"] = "correct" if option["key"] == "B" else "incorrect"
        return {"content": json.dumps(value), "model": "fixture-blind"}

    provider.review_quiz_blind = blind
    if behavior == "explanation-rejected":
        original = provider.complete

        async def complete(messages, **kwargs):
            result = await original(messages, **kwargs)
            if provider.calls[-1]["stage"] == "review":
                payload = json.loads(messages[1]["content"])
                result["content"] = json.dumps(fake_quiz_application_review(payload["questions"], False,
                    [{"kind": "numerical", "detail": "第1题解析中的计算与题干输入不一致。"}]))
            return result

        provider.complete = complete
    response = client.post(f"/api/courses/{course['id']}/quizzes",
        json={"topic": "", "difficulty": "基础", "count": 3})
    assert (response.status_code == 200) is success, response.text
    assert sum(call["stage"] == "generate" for call in provider.calls) == generations
    assert sum(call["stage"] == "review" for call in provider.calls) == full_reviews
    assert app.state.db.one("SELECT COUNT(*) AS n FROM quizzes WHERE course_id=?", (course["id"],))["n"] == int(success)
    if behavior == "wrong-first":
        assert "独立盲解" in provider.calls[1]["messages"][-1]["content"]
    if behavior == "match":
        assert len(blind_calls) == 1
        assert provider.calls[-1]["payload"]["questions"][0]["explanation"]
