"""Hanging async work is bounded without converting cancellation into recovery."""
import asyncio

from backend.app.chat_recovery import CONTINUATION_MODEL, NOTICE
from backend.tests.test_api import events, register, setup


def enable_short_deadline(app, seconds=0.05):
    app.state.settings.chat_recovery_enabled = True
    app.state.settings.provider_timeout = seconds


def ordinary_reply():
    return {"content": "栈从同一端放入和取出，像叠餐盘，最后放上去的先取走。", "model": "fake-recovery-model", "usage": {"total_tokens": 12}, "tool_calls": []}


def test_whole_agent_deadline_cancels_hanging_work_and_recovers(setup):
    client, app, provider = setup
    register(client)
    enable_short_deadline(app)
    cancelled = []
    recovered = []

    async def hanging(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def ordinary(*args, **kwargs):
        recovered.append(True)
        return ordinary_reply()

    app.state.agent.run, provider.complete = hanging, ordinary
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈。"}))
    done = result[-1][1]
    assert result[-1][0] == "done" and not any(event == "error" for event, _ in result)
    assert cancelled == [True] and recovered == [True]
    assert done["degraded"] is True and done["grounded"] is False
    assert done["model"] == "fake-recovery-model" and NOTICE in done["final_text"]
    assert not app.state.session_locks[done["session_id"]].locked()
    saved = client.get(f"/api/sessions/{done['session_id']}/messages").json()
    assert saved[-1]["content"] == done["final_text"] and saved[-1]["citations"] == []


def test_recovery_deadline_cancels_hanging_model_and_continues_honestly(setup):
    client, app, provider = setup
    register(client)
    enable_short_deadline(app)
    cancelled = []

    async def rejected(*args, **kwargs):
        raise RuntimeError("private internal failure")

    async def hanging(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    app.state.agent.run, provider.complete = rejected, hanging
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈。"}))
    done = result[-1][1]
    assert result[-1][0] == "done" and not any(event == "error" for event, _ in result)
    assert cancelled == [True] and done["model"] == CONTINUATION_MODEL
    assert done["grounded"] is False and NOTICE in done["final_text"]
    assert "private" not in done["final_text"] and "Timeout" not in done["final_text"]
    assert not app.state.session_locks[done["session_id"]].locked()
    assert any(trace["node"] == "learning_continuation" for trace in client.get(
        f"/api/sessions/{done['session_id']}/messages").json()[-1]["trace"])


def test_actual_agent_stream_deadline_closes_upstream_generator(setup):
    client, app, provider = setup
    register(client)
    # Reach the real LangGraph teaching iterator before timing out its hanging
    # read; the other cases isolate the shorter deadline without graph setup.
    enable_short_deadline(app, seconds=2.0)
    entered = []
    closed = []
    original_complete = provider.complete

    async def hanging_stream(*args, **kwargs):
        try:
            entered.append(True)
            yield {"text": "本次教材正文尚未生成完整。"}
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    async def complete(messages, **kwargs):
        if messages[0]["content"].startswith("你是中文专业课助教，当前课程身份"):
            return ordinary_reply()
        return await original_complete(messages, **kwargs)

    provider.stream, provider.complete = hanging_stream, complete
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈。"}))
    assert result[-1][0] == "done" and result[-1][1]["degraded"] is True
    assert entered == [True] and closed == [True], result
    assert not app.state.session_locks[result[-1][1]["session_id"]].locked()
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 1
