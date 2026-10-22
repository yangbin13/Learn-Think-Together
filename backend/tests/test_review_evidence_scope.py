import asyncio
import json

from backend.app.config import Settings
from backend.app.provider import Provider
from backend.app.fact_review import REVIEW_PROMPT
from backend.tests.test_api import setup, register, events


def test_fact_review_contract_treats_exact_tool_results_as_independent_evidence():
    assert '它就能独立支持该次精确计算、步骤和边界输入' in REVIEW_PROMPT
    assert '不要要求教材另行证明这些工具输出' in REVIEW_PROMPT
    assert '工具结果不能替代未列出的通用理论或外推' in REVIEW_PROMPT


def test_fact_reviewer_sees_available_evidence_and_precise_selected_ids(setup):
    client, app, provider = setup
    register(client)
    payloads = []
    original = provider.complete

    async def capture(messages, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            payloads.append(json.loads(messages[1]["content"]))
        return await original(messages, **kwargs)

    provider.complete = capture
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "为什么栈后进先出？", "mode": "explain"}))
    assert result[-1][0] == "done"
    cards = next(payload["items"] for name, payload in reversed(result) if name == "citations")
    assert payloads and payloads[-1]["referenced_evidence_ids"] == [card["id"] for card in cards]
    available = payloads[-1]["evidence"]
    assert len(cards) == 1 and len(available) > 1
    assert all(card in available for card in cards)
    assert any(card["id"] not in payloads[-1]["referenced_evidence_ids"] for card in available)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM chunks WHERE course_id='data-structures'")["n"] > 1


def test_provider_does_not_implicitly_retry_a_potentially_billed_request(tmp_path):
    provider = Provider(Settings(root=tmp_path, api_key="test-only", model="test-model"))
    try:
        assert provider.client.max_retries == 0
    finally:
        asyncio.run(provider.close())
