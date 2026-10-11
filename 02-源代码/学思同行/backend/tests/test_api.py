import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.agent import validate_citations
from backend.app.config import Settings
from backend.app.db import dumps, loads
from backend.app.main import create_app
from backend.app.provider import ProviderError
from backend.app.quiz_review import QUIZ_REVIEW_PREFIX
from backend.app.socratic_reply import SOCRATIC_REPLY_PREFIX
from backend.app.tools import calculate, cache_metrics, shortest_path, simulate_page_replacement, subnet_info
from backend.tests.quiz_review_helpers import fake_quiz_application_review, BlindReviewTestMixin


class FakeProvider(BlindReviewTestMixin):
    configured = True

    def __init__(self):
        self.histories = []
        self.stream_histories = []
        self.socratic_histories = []
        self.fail = False
        self.invalid_citation = False
        self.refine = False
        self.thinking = False
        self.tool_runs = 0

    async def complete(self, messages, tools=None, **kwargs):
        if self.fail:
            raise ProviderError("模型服务凭证失效或无访问权限（HTTP 401）")
        self.histories.append(messages.copy())
        if messages[0].get("content", "").startswith(QUIZ_REVIEW_PREFIX):
            questions = json.loads(messages[1]["content"])["questions"]
            return {"content": dumps(fake_quiz_application_review(questions)), "tool_calls": [], "usage": {"total_tokens": 5}, "model": "fake-test-review-model"}
        if messages[0].get("content", "").startswith("你是独立事实审查器"):
            return {"content": dumps({"approved": True, "issues": []}), "tool_calls": [], "usage": {"total_tokens": 5}, "model": "fake-test-review-model"}
        if messages[0].get("content", "").startswith(SOCRATIC_REPLY_PREFIX):
            self.socratic_histories.append(messages.copy())
            return {"content": dumps({"feedback": "", "question": "最后放入的盘子在取出时处于什么位置？"}),
                    "tool_calls": [], "usage": {"total_tokens": 30}, "model": "fake-test-model"}
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if tools and (not tool_messages or (self.refine and len(tool_messages) < 2)):
            self.tool_runs += 1
            return {"content": "", "tool_calls": [{"id": "call_" + str(self.tool_runs), "name": "search_materials", "arguments": dumps({"query": "栈 后进先出", "limit": 3})}], "usage": {"total_tokens": 20}, "model": "fake-test-model"}
        return {"content": "", "tool_calls": [], "usage": {"total_tokens": 10}, "model": "fake-test-model"}

    async def stream(self, messages, **kwargs):
        self.stream_histories.append(messages.copy())
        if self.fail:
            raise ProviderError("模型服务凭证失效或无访问权限（HTTP 401）")
        if self.thinking:
            for part in ("<th", "ink>", "private secret reasoning"):
                yield {"text": part}
        else:
            for part in ("栈具有后进先出的特征", "[99]" if self.invalid_citation else "[1]", "，可类比叠放的盘子。"):
                yield {"text": part, "model": "fake-test-model"}
        yield {"usage": {"total_tokens": 30}, "model": "fake-test-model"}

    async def embed(self, texts):
        raise ProviderError("test embeddings intentionally unavailable")

    async def models(self):
        return [{"id": "fake-test-model"}]

    async def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# 数据结构课程\n## 栈\n栈是一种后进先出的线性结构，压栈push与出栈pop。\n## 队列\n队列先进先出。", encoding="utf-8")
    catalog = [{"id": "data-structures", "title": "数据结构", "description": "数据结构课程", "code": "DS", "assistant_name": "结构助教", "color": "#557766", "icon": "network",
                "documents": [{"path": "book.md", "title": "测试手册", "license": "CC0", "source_type": "original_handbook"}],
                "concepts": [{"id": "stack", "label": "栈", "chapter": "栈"}, {"id": "queue", "label": "队列", "chapter": "队列"}],
                "edges": [{"source": "stack", "target": "queue", "relation": "相邻章节"}],
                "questions": [{"id": "q" + str(i), "stem": "栈的特点是什么？" + str(i), "options": [{"key": "A", "text": "后进先出"}, {"key": "B", "text": "先进先出"}, {"key": "C", "text": "随机访问"}, {"key": "D", "text": "无序访问"}], "answer": "A", "explanation": "栈后进先出。", "concept": "栈", "difficulty": "基础" if i < 4 else "进阶"} for i in range(6)]}]
    seed = tmp_path / "catalog.json"
    seed.write_text(dumps(catalog), encoding="utf-8")
    settings = Settings(root=tmp_path, data_dir=tmp_path / "data", seed_file=seed, model="fake-test-model", embedding_enabled=False, chat_recovery_enabled=False)
    provider = FakeProvider()
    app = create_app(settings, provider)
    with TestClient(app) as client:
        yield client, app, provider


def register(client, name="alice"):
    response = client.post("/api/auth/register", json={"username": name, "password": "password123", "display_name": name})
    assert response.status_code == 200, response.text
    return response.json()


def events(response):
    result = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0].startswith("event:"):
            result.append((lines[0].split(":", 1)[1].strip(), json.loads(lines[1].split(":", 1)[1].strip())))
    return result


def wait_document(client, course_id, doc_id):
    for _ in range(120):
        doc = next(d for d in client.get(f"/api/courses/{course_id}/documents").json() if d["id"] == doc_id)
        if doc["status"] in {"ready", "failed"}:
            return doc
        time.sleep(.035)
    pytest.fail("ingestion worker did not finish")


def test_auth_password_session_and_logout(setup):
    client, app, _ = setup
    assert client.get("/api/courses").status_code == 401
    user = register(client)
    stored = app.state.db.one("SELECT * FROM users WHERE id=?", (user["id"],))
    assert stored["password_hash"].startswith("scrypt$") and "password123" not in stored["password_hash"]
    assert client.get("/api/auth/me").json()["id"] == user["id"]
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    assert client.post("/api/auth/register", json={"username": "ALICE", "password": "password123", "display_name": "x"}).status_code == 409


def test_login_rate_limit_normalizes_whitespace_and_case_like_account_lookup(setup):
    client, _, _ = setup
    register(client)
    client.post("/api/auth/logout")
    for index in range(12):
        response = client.post("/api/auth/login", json={"username": ("Alice" if index % 2 else "alice") + " " * index, "password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/auth/login", json={"username": " ALICE ", "password": "wrong"}).status_code == 429
    assert client.post("/api/auth/login", json={"username": "other-user", "password": "wrong"}).status_code == 401


def test_course_and_builtin_upload_isolation(setup):
    client, app, _ = setup
    register(client)
    course = client.post("/api/courses", json={"title": "线性代数"}).json()
    doc = client.post("/api/courses/data-structures/documents", files={"file": ("private.txt", "alice private text 栈")}, data={"rights_confirmed": "true"}).json()
    bob = TestClient(app)
    register(bob, "bob")
    assert bob.get(f"/api/courses/{course['id']}").status_code == 404
    assert bob.get(f"/api/courses/{course['id']}/graph").status_code == 404
    assert bob.get(f"/api/documents/{doc['id']}/download").status_code == 404
    assert doc["id"] not in [d["id"] for d in bob.get("/api/courses/data-structures/documents").json()]
    assert bob.delete(f"/api/courses/{course['id']}").status_code == 404
    assert client.delete("/api/courses/data-structures").status_code == 403


def test_retrieval_cache_rechecks_ownership_and_document_deletion(setup):
    client, app, _ = setup
    alice = register(client)
    uploaded = client.post("/api/courses/data-structures/documents", files={"file": ("private.txt", b"PRIVATEUNICORNSENTINEL private learning content")}, data={"rights_confirmed": "true"}).json()
    assert wait_document(client, "data-structures", uploaded["id"])["status"] == "ready"
    first = asyncio.run(app.state.retriever.search("data-structures", alice["id"], "PRIVATEUNICORNSENTINEL"))
    assert {item["document_id"] for item in first["items"]} == {uploaded["id"]}
    assert ("data-structures", alice["id"]) in app.state.retriever.lexical_cache
    bob_client = TestClient(app)
    bob = register(bob_client, "bob")
    second = asyncio.run(app.state.retriever.search("data-structures", bob["id"], "PRIVATEUNICORNSENTINEL"))
    assert second["items"] == []
    assert client.delete(f"/api/documents/{uploaded['id']}").status_code == 200
    after_delete = asyncio.run(app.state.retriever.search("data-structures", alice["id"], "PRIVATEUNICORNSENTINEL"))
    assert after_delete["items"] == []


def test_upload_ready_sections_and_graph(setup):
    client, app, _ = setup
    register(client)
    course_id = client.post("/api/courses", json={"title": "算法课"}).json()["id"]
    text = "# 算法\n## 栈\n栈后进先出。\n### 自查\n如何出栈？\n## 队列\n队列先进先出。\n## 链表\n链表结点指针。"
    response = client.post(f"/api/courses/{course_id}/documents", files={"file": ("course.md", text.encode())}, data={"rights_confirmed": "true"})
    assert response.status_code == 202
    doc = wait_document(client, course_id, response.json()["id"])
    assert doc["status"] == "ready" and doc["page_count"] > 0
    chunks = client.get(f"/api/documents/{doc['id']}/chunks").json()["items"]
    assert any(c["section"] == "队列" and "先进先出" in c["text"] for c in chunks)
    labels = {n["label"] for n in client.get(f"/api/courses/{course_id}/graph").json()["nodes"]}
    assert {"栈", "队列", "链表"} <= labels
    assert client.get(f"/api/documents/{doc['id']}/download").content == text.encode()
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert not app.state.db.one("SELECT * FROM chunks WHERE document_id=?", (doc["id"],))
    assert client.get(f"/api/courses/{course_id}/graph").json()["nodes"] == []


def test_upload_invalid_rights_size_extension_and_failed_parse(setup):
    client, app, _ = setup
    register(client)
    base = "/api/courses/data-structures/documents"
    assert client.post(base, files={"file": ("x.md", b"valid")}).status_code == 422
    assert client.post(base, files={"file": ("x.exe", b"x")}, data={"rights_confirmed": "true"}).status_code == 415
    app.state.settings.max_upload_bytes = 8
    assert client.post(base, files={"file": ("x.md", b"123456789")}, data={"rights_confirmed": "true"}).status_code == 413
    app.state.settings.max_upload_bytes = 30 * 1024 * 1024
    response = client.post(base, files={"file": ("broken.pdf", b"not a pdf")}, data={"rights_confirmed": "true"})
    doc = wait_document(client, "data-structures", response.json()["id"])
    assert doc["status"] == "failed" and doc["error"]
    assert client.get(f"/api/documents/{doc['id']}/chunks").json()["total"] == 0


def test_chat_multiturn_real_tool_loop_and_persistence(setup):
    client, app, provider = setup
    register(client)
    provider.refine = True
    response = client.post("/api/chat", json={"course_id": "data-structures", "message": "请查阅教材解释栈", "mode": "explain"})
    event_list = events(response)
    names = [event for event, _ in event_list]
    assert names[0] == "session" and names[-1] == "done"
    done = event_list[-1][1]
    assert done["grounded"] is True
    assert sum(payload.get("node") == "search_materials" for event, payload in event_list if event == "trace") == 2
    session_id = done["session_id"]
    saved = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[-1]["citations"][0]["title"] == "测试手册"
    assert saved[-1]["citations"][0]["page_kind"] == "logical"
    assert saved[-1]["citations"][0]["location_label"] == "逻辑段1"
    assert '"location_label":"逻辑段1"' in provider.stream_histories[-1][0]["content"]
    assert "思维链" not in "".join(m["content"] for m in saved)
    response2 = client.post("/api/chat", json={"course_id": "data-structures", "session_id": session_id, "message": "再给一个例子", "mode": "socratic"})
    assert events(response2)[-1][0] == "done"
    teaching_messages = (provider.socratic_histories or provider.stream_histories)[-1]
    assert any(m["role"] == "assistant" and "后进先出" in m["content"] for m in teaching_messages)
    assert not any("历史引用" in (m.get("content") or "") for m in teaching_messages if m["role"] == "assistant")
    assert any(m["role"] == "assistant" and "[1]" in m["content"] for m in teaching_messages)
    assert any(event == "trace" and payload.get("node") == "history_evidence" for event, payload in events(response2))
    assert len(client.get(f"/api/sessions/{session_id}/messages").json()) == 4


def test_legacy_history_citation_restores_exact_excerpt_without_retrieval(setup):
    client, app, provider = setup
    register(client)
    first = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    session_id = first[-1][1]["session_id"]
    saved = app.state.db.one("SELECT * FROM messages WHERE session_id=? AND role='assistant'", (session_id,))
    legacy = loads(saved["citations"])
    chunk_id = legacy[0].pop("chunk_id")
    # Previous releases retained only an excerpt prefix, and several chunks may share one logical page.
    legacy[0]["excerpt"] = legacy[0]["excerpt"][:12]
    app.state.db.execute("UPDATE messages SET citations=? WHERE id=?", (dumps(legacy), saved["id"]))

    async def finish_planning(messages, **kwargs):
        return {"tool_calls": [], "model": "fake-test-model"}

    provider.complete = finish_planning
    followup = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": session_id, "message": "继续刚才的解释"}))
    assert followup[-1][0] == "done" and followup[-1][1]["grounded"] is True
    restored = next(payload for event, payload in followup if event == "citations")["items"]
    assert restored[0]["chunk_id"] == chunk_id
    assert "恢复 1 条" in next(payload for event, payload in followup if event == "trace" and payload["node"] == "history_evidence")["detail"]
    assert not any(event == "trace" and payload["node"] == "search_materials" for event, payload in followup)


def test_deleted_textbook_history_cannot_reuse_old_source_number(setup):
    client, app, provider = setup
    register(client)
    course_id = client.post("/api/courses", json={"title": "私人教材课"}).json()["id"]
    uploaded = client.post(f"/api/courses/{course_id}/documents", files={"file": ("私有教材.txt", "栈是一种后进先出的结构。".encode())}, data={"rights_confirmed": "true"}).json()
    assert wait_document(client, course_id, uploaded["id"])["status"] == "ready"
    first = events(client.post("/api/chat", json={"course_id": course_id, "message": "解释栈"}))
    assert first[-1][0] == "done"
    session_id = first[-1][1]["session_id"]
    assert client.delete(f"/api/documents/{uploaded['id']}").status_code == 200

    async def finish_planning(messages, **kwargs):
        return {"tool_calls": [], "model": "fake-test-model"}

    provider.complete = finish_planning
    followup = events(client.post("/api/chat", json={"course_id": course_id, "session_id": session_id, "message": "继续引用刚才的教材"}))
    assert followup[-1][0] == "error" and "引用" in followup[-1][1]["message"]
    assert "恢复 0 条" in next(payload for event, payload in followup if event == "trace" and payload["node"] == "history_evidence")["detail"]
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id=? AND role='assistant'", (session_id,))["n"] == 1


def test_incomplete_stream_never_saves_a_successful_assistant_message(setup):
    client, app, provider = setup
    register(client)

    async def truncated(messages, **kwargs):
        yield {"text": "已经生成的一部分正文，仍然缺少最终结论与完整来源说明。"}
        raise ProviderError("回答达到模型输出上限，尚未完整生成，已阻止保存")

    provider.stream = truncated
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert result[-1][0] == "error" and "阻止保存" in result[-1][1]["message"]
    assert not any(event == "done" for event, _ in result)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


@pytest.mark.parametrize("disconnect_mode", ["receive", "send_error"])
@pytest.mark.parametrize("mode", ["explain", "solve", "socratic"])
@pytest.mark.parametrize("recovery_enabled", [False, True])
def test_asgi_client_disconnect_closes_generation_and_releases_session_lock(setup, disconnect_mode, mode, recovery_enabled):
    from backend.app.auth import COOKIE_NAME

    client, app, provider = setup
    register(client)
    app.state.settings.chat_recovery_enabled = recovery_enabled
    closed = []
    recovery_calls = []
    upstream_started = None
    original_complete = provider.complete

    async def watched_complete(messages, **kwargs):
        if messages[0]["content"].startswith("你是中文专业课助教，当前课程身份"):
            recovery_calls.append(True)
        return await original_complete(messages, **kwargs)

    provider.complete = watched_complete

    async def pending_stream(messages, **kwargs):
        try:
            upstream_started.set()
            yield {"text": "本次回答尚未生成完整，先发送一段足够长的正文以模拟用户点击停止。"}
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    provider.stream = pending_stream
    sent = []
    payload = dumps({"course_id": "data-structures", "mode": mode,
                     "message": "直接解释栈" if mode == "socratic" else "解释栈"}).encode()
    cookie = f"{COOKIE_NAME}={client.cookies.get(COOKIE_NAME)}".encode()

    async def disconnected_request():
        nonlocal upstream_started
        upstream_started = asyncio.Event()
        disconnect = asyncio.Event()
        body_delivered = False

        async def receive():
            nonlocal body_delivered
            if not body_delivered:
                body_delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                sent.append(message.get("body", b""))
                body = message.get("body", b"")
                if b"event: trace" in body and b'"teaching"' in body and b'"running"' in body:
                    # The running trace may be queued before stream iteration.
                    # Wait for actual upstream generation, then disconnect.
                    await asyncio.wait_for(upstream_started.wait(), 2)
                    assert not closed
                    if disconnect_mode == "send_error":
                        raise OSError("test-only client socket closed")
                    disconnect.set()

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4" if disconnect_mode == "send_error" else "2.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/chat", "raw_path": b"/api/chat", "query_string": b"", "root_path": "", "client": ("127.0.0.1", 19001), "server": ("testserver", 80),
                 "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), (b"cookie", cookie)]}
        if disconnect_mode == "send_error":
            from starlette.requests import ClientDisconnect
            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(app(scope, receive, send), 5)
        else:
            await asyncio.wait_for(app(scope, receive, send), 5)
        # Check before asyncio.run's shutdown, which would otherwise mask orphaned producer tasks.
        assert closed == [True]
        assert not any(lock.locked() for lock in app.state.session_locks.values())

    asyncio.run(disconnected_request())
    assert closed == [True]
    assert recovery_calls == []
    assert upstream_started.is_set()
    assert b"event: token" not in b"".join(sent)
    assert b"event: done" not in b"".join(sent)
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0
    assert app.state.session_locks and not any(lock.locked() for lock in app.state.session_locks.values())


@pytest.mark.parametrize("chunks", [
    ("<think>非教学内容",),
    ("<thi", "nk>非教学内容"),
    ("analysis", ": 不应作为答案输出"),
])
def test_teaching_rejection_explicitly_closes_suspended_provider_generator(setup, chunks):
    client, _, provider = setup
    register(client)
    closed = []

    async def forbidden_stream(messages, **kwargs):
        try:
            for chunk in chunks:
                yield {"text": chunk}
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    provider.stream = forbidden_stream
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert result[-1][0] == "error" and closed == [True]


def test_chat_failure_and_invalid_citation_never_persist_ai(setup):
    client, app, provider = setup
    register(client)
    provider.fail = True
    event_list = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert event_list[-1][0] == "error" and "401" in event_list[-1][1]["message"]
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0
    provider.fail, provider.invalid_citation = False, True
    event_list = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert event_list[-1][0] == "error" and "引用" in event_list[-1][1]["message"]
    assert app.state.db.one("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'")["n"] == 0


def test_thinking_fragments_not_forwarded(setup):
    client, app, provider = setup
    register(client)
    provider.thinking = True
    event_list = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert event_list[-1][0] == "error"
    assert not any(event == "token" for event, _ in event_list)


def test_session_and_quiz_cross_user_access_denied(setup):
    client, app, _ = setup
    register(client)
    session_id = client.post("/api/courses/data-structures/sessions", json={}).json()["id"]
    quiz_id = client.post("/api/courses/data-structures/quizzes", json={"count": 3}).json()["id"]
    bob = TestClient(app)
    register(bob, "bob")
    assert bob.get(f"/api/sessions/{session_id}/messages").status_code == 404
    assert bob.delete(f"/api/sessions/{session_id}").status_code == 404
    assert bob.post("/api/chat", json={"course_id": "data-structures", "session_id": session_id, "message": "x"}).status_code == 404
    assert bob.get(f"/api/quizzes/{quiz_id}").status_code == 404
    assert bob.post(f"/api/quizzes/{quiz_id}/submit", json={"answers": {}}).status_code == 404


def test_single_question_quiz_is_supported_without_revealing_answer(setup):
    client, _, _ = setup
    register(client)
    response = client.post("/api/courses/data-structures/quizzes", json={"count": 1, "difficulty": "基础"})
    assert response.status_code == 200
    quiz = response.json()
    assert len(quiz["questions"]) == 1
    assert all("answer" not in q and "explanation" not in q for q in quiz["questions"])
    pending = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert pending["submitted"] is False and "result" not in pending


def test_quiz_answers_hidden_idempotent_and_mastery_real(setup):
    client, app, _ = setup
    user = register(client)
    quiz = client.post("/api/courses/data-structures/quizzes", json={"count": 5, "difficulty": "基础"}).json()
    assert len(quiz["questions"]) == 5 and all("answer" not in q and "explanation" not in q for q in quiz["questions"])
    pending = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert "result" not in pending
    answers = {q["id"]: "B" for q in quiz["questions"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers}), range(2)))
    assert all(r.status_code == 200 for r in responses)
    result = responses[0].json()
    assert result == responses[1].json() and result["score"] == 0 and result["total"] == 5
    assert app.state.db.one("SELECT SUM(attempts) AS n FROM mastery WHERE user_id=?", (user["id"],))["n"] == 5
    assert len(client.get("/api/courses/data-structures/practice").json()["mistakes"]) == 5
    assert client.get(f"/api/quizzes/{quiz['id']}").json()["result"] == result
    assert client.get("/api/courses/data-structures/overview").json()["accuracy"] == 0


def test_builtin_quiz_topic_supplement_is_disclosed_and_answers_remain_hidden(setup):
    client, _, _ = setup
    register(client)
    quiz = client.post("/api/courses/data-structures/quizzes", json={"count": 3, "difficulty": "基础", "topic": "队列"}).json()
    assert "匹配 0/3 题" in quiz["source"] and "其他概念补充题" in quiz["source"]
    assert "自建课程" in quiz["source"]
    assert all("answer" not in question and "explanation" not in question for question in quiz["questions"])
    all_match = client.post("/api/courses/data-structures/quizzes", json={"count": 3, "topic": "栈"}).json()
    assert "其他概念补充题" not in all_match["source"]


def test_invalid_submission_not_consumed(setup):
    client, _, _ = setup
    register(client)
    quiz = client.post("/api/courses/data-structures/quizzes", json={}).json()
    assert client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": {"foreign-id": "A"}}).status_code == 422
    assert client.get(f"/api/quizzes/{quiz['id']}").json()["submitted"] is False


def test_plans_isolation_minutes_and_persisted_completion(setup):
    client, app, _ = setup
    register(client)
    assert client.get("/api/courses/data-structures/overview").json()["mastery"] is None
    plan = client.post("/api/courses/data-structures/plans", json={"days": 7, "daily_minutes": 30}).json()
    assert len(plan["items"]) == 7
    assert all(sum(item["minutes"] for item in plan["items"] if item["day"] == day) == 30 for day in range(1, 8))
    item_id = plan["items"][0]["id"]
    assert client.patch(f"/api/plans/{plan['id']}/items/{item_id}", json={"completed": True}).json()["items"][0]["completed"]
    assert client.get("/api/courses/data-structures/plans").json()[0]["items"][0]["completed"]
    bob = TestClient(app)
    register(bob, "bob")
    assert bob.patch(f"/api/plans/{plan['id']}/items/{item_id}", json={"completed": True}).status_code == 404
    assert bob.get("/api/courses/data-structures/plans").json() == []


@pytest.mark.parametrize("days,expected", [(7, [6]), (14, [6, 9, 12]), (30, list(range(6, 31, 3)))])
def test_plan_basis_reports_only_actual_review_days(setup, days, expected):
    client, _, _ = setup
    register(client)
    plan = client.post("/api/courses/data-structures/plans", json={"days": days, "daily_minutes": 30}).json()
    actual = [item["day"] for item in plan["items"] if item["title"].startswith("间隔复习")]
    assert actual == expected
    assert "第 " + "、".join(str(day) for day in actual) + " 天" in plan["basis"]
    assert "第 3/6" not in plan["basis"]


def test_export_all_and_secrets_not_exposed(setup):
    client, app, _ = setup
    register(client)
    app.state.settings.api_key = "TOP_SECRET_TEST_SENTINEL"
    app.state.settings.api_base = "https://user:secret@example.com"
    config = client.get("/api/config/public").text
    assert "TOP_SECRET" not in config and "example.com" not in config
    export = client.get("/api/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    assert "TOP_SECRET" not in export.text and "数据结构" in export.text


def test_csrf_blocked(setup):
    client, _, _ = setup
    register(client)
    assert client.post("/api/courses", json={"title": "test"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_job_recovery_across_restart(tmp_path):
    from backend.app.db import Database, now, uid
    from backend.app.ingestion import IngestionWorker
    settings = Settings(root=tmp_path, model="fake", embedding_enabled=False)
    db = Database(settings.database)
    stamp = now()
    db.execute("INSERT INTO users VALUES ('u','u','u','unused',?)", (stamp,))
    db.execute("INSERT INTO courses VALUES ('c','u','c','','c','#557766','layers','a',0,?)", (stamp,))
    path = tmp_path / "x.md"
    path.write_text("## 栈\n栈后进先出", encoding="utf-8")
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at) VALUES ('d','c','u','x','x.md',?,'processing','uploaded_md','own',?)", (str(path), stamp))
    db.execute("INSERT INTO ingestion_jobs VALUES ('j','d','processing',1,NULL,?,?)", (stamp, stamp))

    async def recover():
        worker = IngestionWorker(db, FakeProvider(), settings)
        await worker.start()
        for _ in range(100):
            if db.one("SELECT status FROM documents WHERE id='d'")["status"] == "ready":
                break
            await asyncio.sleep(.02)
        await worker.stop()
    asyncio.run(recover())
    assert db.one("SELECT status FROM documents WHERE id='d'")["status"] == "ready"
    assert db.one("SELECT attempts FROM ingestion_jobs WHERE id='j'")["attempts"] == 2


def test_deterministic_tools_and_safety():
    assert calculate("(2+3)*4")["result"] == 20
    with pytest.raises(ValueError):
        calculate("__import__('os').system('whoami')")
    with pytest.raises(ValueError):
        calculate("9**999999999")
    lru = simulate_page_replacement([1, 2, 3, 1, 4], 3, "LRU")
    fifo = simulate_page_replacement([1, 2, 3, 1, 4], 3, "FIFO")
    assert lru["steps"][-1]["evicted"] == 2 and fifo["steps"][-1]["evicted"] == 1
    with pytest.raises(ValueError):
        simulate_page_replacement([1, 2, 3, 4], "3", "FIFO")
    assert subnet_info("192.168.1.10/24")["hosts"] == 254
    assert shortest_path([{"source": "A", "target": "B", "weight": 2}, {"source": "B", "target": "C", "weight": 3}], "A")["distances"]["C"] == 5
    assert cache_metrics(.9, 10, 100)["average_serial_ns"] == pytest.approx(20)


def test_citation_validity_and_generated_questions():
    from backend.app.practice import validate_generated_questions
    from backend.app.citations import with_location
    assert with_location({"source_type": "uploaded_pdf", "page": 2})["location_label"] == "物理页2"
    assert with_location({"source_type": "open_textbook", "page": 2})["page_kind"] == "physical"
    assert with_location({"source_type": "uploaded_docx", "page": 2})["location_label"] == "逻辑段2"
    assert with_location({"source_type": "verified_computation", "page": 0})["page_kind"] == "calculation"
    assert validate_citations("教材指出[1]", [{"id": 1}]) == ([{"id": 1}], True)
    with pytest.raises(ProviderError):
        validate_citations("错误来源[2]", [{"id": 1}])
    with pytest.raises(ProviderError):
        validate_citations("FIFO定义[1]物理页548", [{"id": 1, "page": 552, "page_kind": "physical", "source_type": "uploaded_pdf"}])
    with pytest.raises(ProviderError):
        validate_citations("教材[1]物理页192–193", [{"id": 1, "page": 192, "page_kind": "physical", "source_type": "uploaded_pdf"}])
    with pytest.raises(ProviderError):
        validate_generated_questions({"questions": []}, 3, {"source"})

