"""Exercise the public recovery boundary without weakening strict Agent gates."""
import pytest

from backend.app.chat_recovery import NOTICE, CONTINUATION_MODEL
from backend.app.provider import ProviderError
from backend.tests.test_api import setup, register, events


@pytest.mark.parametrize("mode,question,answer", [
    ("explain", "请解释栈。", "栈从同一端放入和取出，像叠餐盘，最后放上去的先取走。"),
    ("socratic", "用生活情境引出栈，只问一个入门问题，不给答案。", "餐盘依次叠放，每次从顶部取走。\n\n你先取到最早还是最后放上的餐盘？"),
    ("solve", "请说明分页地址转换的方法。", "先由虚拟地址和页大小求页号与偏移，再通过页表找到帧号，合成物理地址。"),
])
def test_internal_failure_returns_persisted_honest_reply_in_each_mode(setup, mode, question, answer):
    client, app, provider = setup
    register(client)
    app.state.settings.chat_recovery_enabled = True

    async def rejected(*args, **kwargs):
        await args[4]("trace", {"node": "fact_review", "status": "failed", "label": "局部事实复核拒绝", "detail": "未取得可交付依据"})
        raise ProviderError("private internal failure")

    async def ordinary(messages, **kwargs):
        assert kwargs["tools"] is None and kwargs["json_mode"] is False
        assert "private internal failure" not in str(messages)
        return {"content": answer, "model": "fake-recovery-model", "usage": {"total_tokens": 12}, "tool_calls": []}

    app.state.agent.run = rejected
    provider.complete = ordinary
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "mode": mode, "message": question}))
    assert result[-1][0] == "done" and not any(name == "error" for name, _ in result)
    done = result[-1][1]
    assert done["degraded"] is True and done["grounded"] is False
    assert NOTICE in done["final_text"] and answer in done["final_text"]
    saved = client.get(f"/api/sessions/{done['session_id']}/messages").json()[-1]
    assert saved["content"] == done["final_text"] and saved["model"] == "fake-recovery-model"
    assert saved["citations"] == []
    assert saved["trace"][0]["node"] == "fact_review"
    assert any(trace["status"] == "degraded" for trace in saved["trace"])


@pytest.mark.parametrize("status", [402, 429])
def test_quota_or_rate_limit_has_clear_error_without_saving_a_fake_answer(setup, status):
    client, app, provider = setup
    register(client)
    app.state.settings.chat_recovery_enabled = True

    async def rejected(*args, **kwargs):
        raise ProviderError("private upstream details", {"upstream_status": status})

    app.state.agent.run = rejected
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈。"}))
    assert result[-1][0] == "error" and "private" not in result[-1][1]["message"]
    assert ("额度" if status == 402 else "频率") in result[-1][1]["message"]
    assert provider.histories == []
    session_id = next(value["session_id"] for name, value in result if name == "session")
    assert [item["role"] for item in client.get(f"/api/sessions/{session_id}/messages").json()] == ["user"]


def test_network_unavailable_continues_without_mislabeling_a_model_answer(setup):
    client, app, provider = setup
    register(client)
    app.state.settings.chat_recovery_enabled = True

    async def rejected(*args, **kwargs):
        raise RuntimeError("private internal issue")

    async def offline(*args, **kwargs):
        raise ProviderError("private connection details")

    app.state.agent.run, provider.complete = rejected, offline
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈。"}))
    assert result[-1][0] == "done" and result[-1][1]["model"] == CONTINUATION_MODEL
    assert "private" not in str(result)
    assert result[-1][1]["grounded"] is False and "哪个概念或哪一步" in result[-1][1]["final_text"]
