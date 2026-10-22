import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import config, practice
from backend.app.db import Database, MAX_QUIZ_DRAFT_VERSION, dumps, loads, now


class OfflineProvider:
    configured = False

    async def complete(self, *args, **kwargs):
        raise AssertionError("Quiz draft tests must not request a model")

    async def embed(self, *args, **kwargs):
        raise AssertionError("Quiz draft tests must not request embeddings")


def bank_question(index):
    return {"id": f"bank-{index}", "type": "single_choice", "stem": f"离线练习问题 {index}",
            "options": [{"key": key, "text": f"选项 {key}"} for key in "ABCD"],
            "answer": "A", "explanation": "只在提交后展示的离线解析。", "concept": f"概念 {index}",
            "difficulty": "基础"}


@pytest.fixture
def draft_app(tmp_path, monkeypatch):
    settings = config.Settings(root=tmp_path, data_dir=tmp_path / "data", api_key="", embedding_enabled=False)
    # Importing main constructs its module app. Keep that construction inside this
    # temporary workspace without reading server dotenv or starting its lifespan.
    with monkeypatch.context() as import_guard:
        import_guard.setattr(config.Settings, "from_env", classmethod(lambda cls: settings))
        from backend.app.main import create_app
    app = create_app(settings, OfflineProvider())
    db = app.state.db
    db.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("draft-course", None, "离线草稿课程", "", "DRAFT", "#52796f", "layers", "离线助教", 1, now()))
    for index in range(3):
        db.execute("INSERT INTO question_bank VALUES (?,?,?)", (f"bank-{index}", "draft-course", dumps(bank_question(index))))
    client = TestClient(app)
    user = client.post("/api/auth/register", json={"username": "draft_alice", "password": "offline-test-only", "display_name": "草稿测试"}).json()
    response = client.post("/api/courses/draft-course/quizzes", json={"count": 3})
    assert response.status_code == 200, response.text
    try:
        yield {"client": client, "app": app, "db": db, "user": user, "quiz": response.json()}
    finally:
        client.close()


def save(client, quiz_id, answers, version):
    return client.put(f"/api/quizzes/{quiz_id}/draft", json={"answers": answers, "expected_version": version})


def test_generation_and_reload_restore_drafts_without_revealing_answers(draft_app):
    client, quiz = draft_app["client"], draft_app["quiz"]
    assert quiz["draft_answers"] == {} and quiz["draft_version"] == 0
    first = quiz["questions"][0]["id"]
    response = save(client, quiz["id"], {first: "B"}, 0)
    assert response.status_code == 200
    assert response.json() == {"draft_answers": {first: "B"}, "draft_version": 1}
    # A new browser client with the same authenticated session reads persisted data.
    with closing(TestClient(draft_app["app"])) as other_page:
        other_page.cookies.update(client.cookies)
        restored = other_page.get(f"/api/quizzes/{quiz['id']}").json()
    assert restored["submitted"] is False
    assert restored["draft_answers"] == {first: "B"} and restored["draft_version"] == 1
    assert "result" not in restored
    assert all(not ({"answer", "explanation", "source_ids"} & question.keys()) for question in restored["questions"])


def test_quiz_provenance_survives_resume_and_grading_without_parsing_display_text(draft_app):
    client, db, quiz = draft_app["client"], draft_app["db"], draft_app["quiz"]
    assert quiz["source_kind"] == "curated_bank"
    # Translated disclosure text can change without changing the source identity.
    db.execute("UPDATE quizzes SET source=? WHERE id=?", ("题库（本组包含相邻难度补充题）", quiz["id"]))
    resumed = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert resumed["source_kind"] == "curated_bank"
    assert "相邻难度" in resumed["source"]
    assert "result" not in resumed
    client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": {}})
    graded = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert graded["source_kind"] == "curated_bank"
    assert graded["submitted"] and "result" in graded


def test_private_course_provenance_is_derived_from_course_access_not_bank_word(draft_app):
    client, db, quiz = draft_app["client"], draft_app["db"], draft_app["quiz"]
    db.execute("UPDATE courses SET is_builtin=0,user_id=? WHERE id=?", (draft_app["user"]["id"], "draft-course"))
    db.execute("UPDATE quizzes SET source=? WHERE id=?", ("教材专题 bank account 数据结构", quiz["id"]))
    resumed = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert resumed["source_kind"] == "textbook_generated"
    assert resumed["submitted"] is False
    assert all("answer" not in question for question in resumed["questions"])


def test_optimistic_versions_idempotency_and_empty_draft(draft_app):
    client, quiz = draft_app["client"], draft_app["quiz"]
    first, second = [question["id"] for question in quiz["questions"][:2]]
    assert save(client, quiz["id"], {}, 0).json()["draft_version"] == 0
    assert save(client, quiz["id"], {first: "A"}, 0).json()["draft_version"] == 1
    assert save(client, quiz["id"], {first: "A"}, 0).json()["draft_version"] == 1
    assert save(client, quiz["id"], {first: "A", second: "B"}, 1).json()["draft_version"] == 2
    assert save(client, quiz["id"], {second: "B", first: "A"}, 0).json()["draft_version"] == 2
    assert save(client, quiz["id"], {first: "C"}, 1).status_code == 409
    restored = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert restored["draft_answers"] == {first: "A", second: "B"} and restored["draft_version"] == 2
    assert save(client, quiz["id"], {}, 1).status_code == 409
    assert save(client, quiz["id"], {}, 2).json() == {"draft_answers": {}, "draft_version": 3}
    assert save(client, quiz["id"], {}, 0).json() == {"draft_answers": {}, "draft_version": 3}


@pytest.mark.parametrize("body", [
    {"answers": {}, "expected_version": -1},
    {"answers": {}, "expected_version": True},
    {"answers": {}, "expected_version": 0.0},
    {"answers": {}, "expected_version": "0"},
    {"answers": {}, "expected_version": None},
    {"answers": {}, "expected_version": MAX_QUIZ_DRAFT_VERSION + 1},
    {"answers": {}},
    {"expected_version": 0},
    {"answers": [], "expected_version": 0},
    {"answers": None, "expected_version": 0},
    {"answers": {"first": "E"}, "expected_version": 0},
    {"answers": {"first": "a"}, "expected_version": 0},
    {"answers": {"first": "A "}, "expected_version": 0},
    {"answers": {"first": 1}, "expected_version": 0},
    {"answers": {"first": True}, "expected_version": 0},
    {"answers": {"foreign-question": "A"}, "expected_version": 0},
    {"answers": {}, "expected_version": 0, "result": {"score": 100}},
])
def test_bad_draft_input_is_rejected_without_writing(draft_app, body):
    client, quiz, db = draft_app["client"], draft_app["quiz"], draft_app["db"]
    body = dict(body)
    if isinstance(body.get("answers"), dict):
        body["answers"] = {quiz["questions"][0]["id"] if key == "first" else key: value for key, value in body["answers"].items()}
    assert client.put(f"/api/quizzes/{quiz['id']}/draft", json=body).status_code == 422
    row = db.one("SELECT draft_answers,draft_version,submitted,result FROM quizzes WHERE id=?", (quiz["id"],))
    assert row == {"draft_answers": "{}", "draft_version": 0, "submitted": 0, "result": None}


def test_anonymous_foreign_owner_and_origin_do_not_gain_access(draft_app):
    app, client, quiz = draft_app["app"], draft_app["client"], draft_app["quiz"]
    with closing(TestClient(app)) as anonymous:
        assert save(anonymous, quiz["id"], {}, 0).status_code == 401
    with closing(TestClient(app)) as other:
        assert other.post("/api/auth/register", json={"username": "draft_bob", "password": "offline-test-only", "display_name": "另一位"}).status_code == 200
        response = save(other, quiz["id"], {}, 0)
        assert response.status_code == 404 and quiz["questions"][0]["stem"] not in response.text
        assert other.get(f"/api/quizzes/{quiz['id']}").status_code == 404
    assert client.put(f"/api/quizzes/{quiz['id']}/draft", json={"answers": {}, "expected_version": 0},
                      headers={"Origin": "https://untrusted.invalid"}).status_code == 403


def test_quiz_owner_still_needs_access_to_its_course(draft_app):
    db, client, quiz = draft_app["db"], draft_app["client"], draft_app["quiz"]
    db.execute("INSERT INTO users VALUES (?,?,?,?,?)", ("foreign-owner", "foreign-owner", "其他学生", "unused-test-hash", now()))
    db.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("foreign-course", "foreign-owner", "私人课程", "", "CUSTOM", "#52796f", "layers", "助教", 0, now()))
    db.execute("UPDATE quizzes SET course_id=? WHERE id=?", ("foreign-course", quiz["id"]))
    assert save(client, quiz["id"], {}, 0).status_code == 404
    assert client.get(f"/api/quizzes/{quiz['id']}").status_code == 404
    assert client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": {}}).status_code == 404


def test_draft_does_not_change_result_mistakes_mastery_or_learning_activity(draft_app):
    client, quiz, db = draft_app["client"], draft_app["quiz"], draft_app["db"]
    dashboard = client.get("/api/dashboard").json()
    overview = client.get("/api/courses/draft-course/overview").json()
    first = quiz["questions"][0]["id"]
    assert save(client, quiz["id"], {first: "B"}, 0).status_code == 200
    assert client.get("/api/dashboard").json() == dashboard
    assert client.get("/api/courses/draft-course/overview").json() == overview
    assert client.get("/api/courses/draft-course/practice").json()["mistakes"] == []
    assert db.all("SELECT * FROM mastery") == [] and db.all("SELECT * FROM activity") == []
    assert db.one("SELECT submitted,result FROM quizzes WHERE id=?", (quiz["id"],)) == {"submitted": 0, "result": None}


def test_submitted_result_is_authoritative_and_late_saves_are_rejected(draft_app):
    client, quiz, db = draft_app["client"], draft_app["quiz"], draft_app["db"]
    first = quiz["questions"][0]["id"]
    assert save(client, quiz["id"], {first: "A"}, 0).status_code == 200
    answers = {question["id"]: "B" for question in quiz["questions"]}
    result = client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": answers}).json()
    assert result["score"] == 0 and result["total"] == 3
    restored = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert restored["submitted"] is True and restored["result"] == result
    assert "draft_answers" not in restored and "draft_version" not in restored
    assert save(client, quiz["id"], {first: "A"}, 0).status_code == 409
    assert save(client, quiz["id"], {}, 1).status_code == 409
    assert client.post(f"/api/quizzes/{quiz['id']}/submit", json={"answers": {first: "A"}}).json() == result
    assert db.one("SELECT draft_answers,draft_version FROM quizzes WHERE id=?", (quiz["id"],)) == {"draft_answers": dumps({first: "A"}), "draft_version": 1}
    assert db.one("SELECT SUM(attempts) AS n FROM mastery")["n"] == 3
    assert len(db.all("SELECT * FROM mistakes")) == 3 and len(db.all("SELECT * FROM activity")) == 1


def test_different_concurrent_drafts_do_not_overwrite_another_page(draft_app):
    db, quiz, user_id = draft_app["db"], draft_app["quiz"], draft_app["user"]["id"]
    first = quiz["questions"][0]["id"]
    ready = Barrier(2)

    def persist(answer):
        ready.wait(timeout=5)
        try:
            return 200, practice.save_quiz_draft(db, quiz["id"], user_id, {first: answer}, 0)
        except HTTPException as exc:
            return exc.status_code, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(persist, ["A", "B"]))
    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(result for status, result in results if status == 200)
    assert winner["draft_version"] == 1
    restored = practice.load_quiz(db, quiz["id"], user_id)
    assert restored["draft_answers"] == winner["draft_answers"] and restored["draft_version"] == 1


def test_save_waits_for_grading_transaction_then_rejects_stale_write(draft_app, monkeypatch):
    db, quiz, user_id = draft_app["db"], draft_app["quiz"], draft_app["user"]["id"]
    first = quiz["questions"][0]["id"]
    entered, release = Event(), Event()

    def grading_checkpoint(*args):
        entered.set()
        assert release.wait(timeout=5)
        return []

    monkeypatch.setattr(practice, "question_citations", grading_checkpoint)
    with ThreadPoolExecutor(max_workers=2) as executor:
        graded = executor.submit(practice.submit_quiz, db, quiz["id"], user_id, {question["id"]: "B" for question in quiz["questions"]})
        try:
            assert entered.wait(timeout=5)
            draft = executor.submit(practice.save_quiz_draft, db, quiz["id"], user_id, {first: "A"}, 0)
            with pytest.raises(TimeoutError):
                draft.result(timeout=.1)
        finally:
            release.set()
        assert graded.result(timeout=5)["score"] == 0
        with pytest.raises(HTTPException) as refused:
            draft.result(timeout=5)
        assert refused.value.status_code == 409
    row = db.one("SELECT draft_answers,draft_version,result FROM quizzes WHERE id=?", (quiz["id"],))
    assert row["draft_answers"] == "{}" and row["draft_version"] == 0 and loads(row["result"])["score"] == 0


def test_version_limit_never_wraps_or_changes_existing_draft(draft_app):
    client, quiz, db = draft_app["client"], draft_app["quiz"], draft_app["db"]
    first = quiz["questions"][0]["id"]
    answers = {first: "A"}
    db.execute("UPDATE quizzes SET draft_answers=?,draft_version=? WHERE id=?", (dumps(answers), MAX_QUIZ_DRAFT_VERSION, quiz["id"]))
    assert save(client, quiz["id"], answers, 0).json() == {"draft_answers": answers, "draft_version": MAX_QUIZ_DRAFT_VERSION}
    assert save(client, quiz["id"], {first: "B"}, MAX_QUIZ_DRAFT_VERSION).status_code == 409
    assert client.get(f"/api/quizzes/{quiz['id']}").json()["draft_answers"] == answers


def test_legacy_schema_migration_preserves_grades_and_adds_recoverable_drafts(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    questions = [bank_question(index) for index in range(3)]
    original_result = dumps({"score": 67, "total": 3, "correct": 2, "results": [{"question_id": "bank-0", "user_answer": "B"}]})
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE quizzes (id TEXT PRIMARY KEY,course_id TEXT NOT NULL,user_id TEXT NOT NULL,title TEXT NOT NULL,"
                     "questions TEXT NOT NULL,created_at TEXT NOT NULL,source TEXT NOT NULL,submitted INTEGER NOT NULL DEFAULT 0,result TEXT)")
        conn.executemany("INSERT INTO quizzes VALUES (?,?,?,?,?,?,?,?,?)", [
            ("legacy-open", "legacy-course", "legacy-user", "未提交练习", dumps(questions), now(), "离线题库", 0, None),
            ("legacy-graded", "legacy-course", "legacy-user", "已提交练习", dumps(questions), now(), "离线题库", 1, original_result),
        ])
    db = Database(path)
    db.execute("INSERT INTO users VALUES (?,?,?,?,?)", ("legacy-user", "legacy-user", "旧用户", "unused-test-hash", now()))
    db.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("legacy-course", "legacy-user", "旧课程", "", "CUSTOM", "#52796f", "layers", "助教", 0, now()))
    assert db.one("SELECT submitted,result FROM quizzes WHERE id='legacy-graded'") == {"submitted": 1, "result": original_result}
    assert practice.load_quiz(db, "legacy-open", "legacy-user")["draft_answers"] == {}
    assert practice.save_quiz_draft(db, "legacy-open", "legacy-user", {"bank-0": "C"}, 0)["draft_version"] == 1
    reopened = Database(path)
    assert practice.load_quiz(reopened, "legacy-open", "legacy-user")["draft_answers"] == {"bank-0": "C"}
    assert practice.load_quiz(reopened, "legacy-open", "legacy-user")["draft_version"] == 1
    assert practice.load_quiz(reopened, "legacy-graded", "legacy-user")["result"] == loads(original_result)
    assert reopened.one("SELECT result FROM quizzes WHERE id='legacy-graded'")["result"] == original_result
