"""Personal mindmap corrections preserve source identity and student isolation."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Database, SCHEMA, now
from backend.app.knowledge_map import build_knowledge_map, heading_id
from backend.app.main import create_app
from backend.tests.test_api import FakeProvider, register
from backend.tests.test_graph_structure import body_document, chunk, store


@pytest.fixture
def annotation_api(tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(root=tmp_path, embedding_enabled=False, api_key="", model="fake-test-model"), provider)
    with TestClient(app) as client:
        user = register(client)
        course = client.post("/api/courses", json={"title": "操作系统教材"}).json()["id"]
        db = app.state.db
        body_document(db, "book", course=course, owner=user["id"])
        db.execute("UPDATE documents SET page_count=1349 WHERE id='book'")
        heading = "第七章操作系统接又"
        chunk(db, "book", heading, 892, owner=user["id"])
        db.execute("UPDATE chunks SET text=? WHERE document_id='book'", (
            heading + "\n操作系统提供统一的接口，学生核对完整原文后再保存自己的总结。",))
        node = heading_id("book", heading)
        db.execute("INSERT INTO concepts VALUES (?,?,?,'第七章','旧概念缓存')", (node, course, "操作系统接又"))
        db.execute("INSERT INTO mastery VALUES (?,?,?,4,3)", (user["id"], course, "操作系统接又"))
        yield {"client": client, "app": app, "db": db, "provider": provider, "user": user,
               "course": course, "node": node,
               "route": f"/api/courses/{course}/mindmap/nodes/{node}/annotation"}


def tree_node(client, course, node_id):
    return next(item for item in client.get(f"/api/courses/{course}/graph").json()["tree"]["nodes"]
                if item["id"] == node_id)


def test_annotation_put_supports_trusted_dev_origins_and_rejects_other_preflights(annotation_api):
    a = annotation_api
    client, route = a["client"], a["route"]
    value = {"display_label": "第七章 操作系统接口", "study_note": "核对原页后的个人笔记。"}
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        preflight = client.options(route, headers={
            "Origin": origin, "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        })
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        assert preflight.headers["access-control-allow-credentials"] == "true"
        assert "PUT" in {method.strip() for method in preflight.headers["access-control-allow-methods"].split(",")}
        saved = client.put(route, headers={"Origin": origin}, json=value)
        assert saved.status_code == 200
        assert saved.headers["access-control-allow-origin"] == origin
        assert saved.json()["study_note"] == value["study_note"]
    before = client.get(route).json()
    denied = client.options(route, headers={
        "Origin": "http://untrusted.invalid", "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type",
    })
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
    assert client.get(route).json() == before
    assert a["provider"].histories == [] and a["provider"].stream_histories == []


def test_pre_annotation_database_migrates_without_replacing_source_or_history(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    old_schema, replacements = re.subn(r"CREATE TABLE IF NOT EXISTS mindmap_annotations \(.*?\);\n", "", SCHEMA, flags=re.S)
    assert replacements == 1
    with sqlite3.connect(path) as conn:
        conn.executescript(old_schema)
        conn.execute("INSERT INTO users VALUES ('alice','alice','原学生','unused',?)", (now(),))
        conn.execute("INSERT INTO courses VALUES ('old','alice','原课程','','C','#557766','book','助教',0,?)", (now(),))
        conn.execute("INSERT INTO tutor_sessions VALUES ('history','old','alice','原会话',?,?)", (now(), now()))
        conn.execute("INSERT INTO messages VALUES ('message','history','user','原始提问','[]','[]',?,NULL)", (now(),))
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='mindmap_annotations'").fetchone() is None
    db = Database(path)
    assert db.one("SELECT display_name FROM users WHERE id='alice'")["display_name"] == "原学生"
    assert db.one("SELECT content FROM messages WHERE id='message'")["content"] == "原始提问"
    assert db.one("SELECT COUNT(*) AS n FROM mindmap_annotations")["n"] == 0
    with db.connection() as conn:
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(mindmap_annotations)")}
        assert {column for column, row in columns.items() if row["pk"]} == {"user_id", "course_id", "node_id"}
        foreign = list(conn.execute("PRAGMA foreign_key_list(mindmap_annotations)"))
        assert {(row["table"], row["from"], row["on_delete"]) for row in foreign} == {
            ("users", "user_id", "CASCADE"), ("courses", "course_id", "CASCADE")}
    # Startup is idempotent; the new table and prior records survive reopening.
    reopened = Database(path)
    assert reopened.one("SELECT COUNT(*) AS n FROM messages")["n"] == 1


def test_save_update_and_clear_leave_canonical_nodes_and_original_evidence_unchanged(annotation_api):
    a = annotation_api
    client, db, route = a["client"], a["db"], a["route"]
    untouched = tree_node(client, a["course"], a["node"])
    source_rows = db.all("SELECT id,document_id,page,section,text FROM chunks WHERE document_id='book'")
    concepts = db.all("SELECT * FROM concepts WHERE course_id=?", (a["course"],))
    mastery = db.all("SELECT * FROM mastery WHERE course_id=?", (a["course"],))
    evidence_route = route.removesuffix("annotation") + "evidence"
    original_evidence = client.get(evidence_route).json()
    empty = {"node_id": a["node"], "display_label": "", "study_note": "", "updated_at": None}
    assert client.get(route).json() == empty
    saved = client.put(route, json={"display_label": "  第七章 操作系统接口  ", "study_note": "  先回忆系统调用与用户接口的区别。\n再核对教材。  "})
    assert saved.status_code == 200
    value = saved.json()
    assert value["display_label"] == "第七章 操作系统接口"
    assert value["study_note"] == "先回忆系统调用与用户接口的区别。\n再核对教材。"
    assert datetime.fromisoformat(value["updated_at"]).tzinfo is not None
    assert client.get(route).json() == value
    shown = tree_node(client, a["course"], a["node"])
    assert {key: shown[key] for key in untouched} == untouched
    assert shown["display_label"] == value["display_label"]
    assert shown["study_note"] == value["study_note"]
    assert shown["annotation_updated_at"] == value["updated_at"]
    assert shown["mastery"] == .75
    assert client.get(evidence_route).json() == original_evidence
    changed = client.put(route, json={"display_label": "", "study_note": "只保存自己的回忆笔记。"}).json()
    assert changed["display_label"] == "" and changed["study_note"] == "只保存自己的回忆笔记。"
    assert db.one("SELECT COUNT(*) AS n FROM mindmap_annotations")["n"] == 1
    cleared = client.put(route, json={"display_label": "   ", "study_note": " \n  "})
    assert cleared.status_code == 200 and cleared.json() == empty
    assert client.get(route).json() == empty
    assert tree_node(client, a["course"], a["node"]) == untouched
    assert db.one("SELECT COUNT(*) AS n FROM mindmap_annotations")["n"] == 0
    assert db.all("SELECT id,document_id,page,section,text FROM chunks WHERE document_id='book'") == source_rows
    assert db.all("SELECT * FROM concepts WHERE course_id=?", (a["course"],)) == concepts
    assert db.all("SELECT * FROM mastery WHERE course_id=?", (a["course"],)) == mastery
    assert a["provider"].histories == [] and a["provider"].stream_histories == []


def test_annotations_accept_full_tree_nodes_outside_the_legacy_graph_sample(annotation_api):
    a = annotation_api
    for number in range(1, 192):
        chunk(a["db"], "book", f"第{number}章 专题{number}", number, owner=a["user"]["id"])
    node = heading_id("book", "第191章 专题191")
    graph = a["client"].get(f"/api/courses/{a['course']}/graph").json()
    assert node not in {item["id"] for item in graph["nodes"]}
    original = next(item for item in graph["tree"]["nodes"] if item["id"] == node)
    route = a["route"].replace(a["node"], node)
    response = a["client"].put(route, json={"display_label": "末章专题", "study_note": "这部分也可以保存自己的总结。"})
    assert response.status_code == 200
    assert a["client"].get(route).json()["study_note"] == "这部分也可以保存自己的总结。"
    annotated = tree_node(a["client"], a["course"], node)
    assert {key: annotated[key] for key in original} == original
    assert annotated["display_label"] == "末章专题"


def test_two_students_and_two_courses_do_not_share_annotations(annotation_api):
    a = annotation_api
    client, app, db = a["client"], a["app"], a["db"]
    for cid in ("public", "second"):
        db.execute("INSERT INTO courses VALUES (?,NULL,'公开导学','','B','#557766','book','助教',1,?)", (cid, now()))
        db.execute("INSERT INTO concepts VALUES ('same-node',?,'系统调用','第1章','original')", (cid,))
    public = "/api/courses/public/mindmap/nodes/same-node/annotation"
    second = "/api/courses/second/mindmap/nodes/same-node/annotation"
    assert client.put(public, json={"display_label": "学生甲的标题", "study_note": "甲的第一门课笔记"}).status_code == 200
    assert client.put(second, json={"display_label": "甲的另一门课", "study_note": "甲的第二门课笔记"}).status_code == 200
    with TestClient(app) as bob:
        register(bob, "bob")
        assert bob.get(public).json()["study_note"] == ""
        assert "display_label" not in tree_node(bob, "public", "same-node")
        assert bob.put(public, json={"display_label": "乙的标题", "study_note": "乙自己的笔记"}).status_code == 200
        assert bob.get(public).json()["study_note"] == "乙自己的笔记"
        assert tree_node(bob, "public", "same-node")["display_label"] == "乙的标题"
        assert bob.get(a["route"]).status_code == 404
        assert bob.put(a["route"], json={"display_label": "越权", "study_note": ""}).status_code == 404
    assert client.get(public).json()["study_note"] == "甲的第一门课笔记"
    assert client.get(second).json()["study_note"] == "甲的第二门课笔记"
    assert tree_node(client, "public", "same-node")["display_label"] == "学生甲的标题"
    assert db.one("SELECT COUNT(*) AS n FROM mindmap_annotations")["n"] == 3


def test_unknown_foreign_unready_and_virtual_nodes_are_not_annotation_targets(annotation_api):
    a = annotation_api
    client, db = a["client"], a["db"]
    other = client.post("/api/courses", json={"title": "其他专业课"}).json()["id"]
    payload = {"display_label": "校正", "study_note": "笔记"}
    wrong_course = a["route"].replace(a["course"], other)
    virtual = f"/api/courses/{a['course']}/mindmap/nodes/course:{a['course']}/annotation"
    unknown = a["route"].replace(a["node"], "missing")
    for path in (wrong_course, virtual, unknown):
        assert client.get(path).status_code == 404
        assert client.put(path, json=payload).status_code == 404
    db.execute("INSERT INTO courses VALUES ('manual',NULL,'导学','','B','#557766','book','助教',1,?)", (now(),))
    container = "/api/courses/manual/mindmap/nodes/outline:manual/annotation"
    assert client.get(container).status_code == 404
    assert client.put(container, json=payload).status_code == 404
    # A real textbook root is editable; only synthesized container roots are rejected.
    actual_root = a["route"].replace(a["node"], "document:book")
    assert client.put(actual_root, json=payload).status_code == 200
    db.execute("UPDATE documents SET status='processing' WHERE id='book'")
    assert client.get(a["route"]).status_code == 404
    assert client.put(a["route"], json=payload).status_code == 404
    assert db.one("SELECT COUNT(*) AS n FROM mindmap_annotations")["n"] == 1


def test_annotation_field_boundaries_controls_and_authentication(annotation_api):
    a = annotation_api
    client, route = a["client"], a["route"]
    boundary = {"display_label": "题" * 160, "study_note": "记" * 2000}
    assert client.put(route, json=boundary).status_code == 200
    invalid = [
        {"display_label": "题" * 161, "study_note": ""},
        {"display_label": "", "study_note": "记" * 2001},
        {"display_label": None, "study_note": ""},
        {"display_label": "", "study_note": 3},
        {"display_label": "缺少笔记字段"},
        {"study_note": "缺少标题字段"},
        {"display_label": "", "study_note": "", "user_id": "someone-else"},
        {"display_label": "", "study_note": "", "heading": "不允许覆盖原标题"},
    ]
    invalid.extend({"display_label": "标题" + control + "文字", "study_note": ""}
                   for control in ("\n", "\r", "\t", "\x00", "\x7f", "\u200b", "\u202e", "\u2028", "\u2029"))
    invalid.extend({"display_label": "", "study_note": "笔记" + control + "内容"}
                   for control in ("\x00", "\x01", "\x0c", "\x1b", "\x7f"))
    for payload in invalid:
        assert client.put(route, json=payload).status_code == 422, repr(payload)
    # HTTPX's normal JSON encoder cannot encode an isolated surrogate. Send the
    # escaped ASCII request a malicious/custom client could actually submit.
    surrogate = json.dumps({"display_label": "", "study_note": chr(0xD800)}, ensure_ascii=True).encode("ascii")
    assert client.put(route, content=surrogate, headers={"Content-Type": "application/json"}).status_code == 422
    assert client.get(route).json()["display_label"] == boundary["display_label"]
    assert client.get(route).json()["study_note"] == boundary["study_note"]
    normal_note = "第一行\n\t第二行\r\n第三行"
    normal = client.put(route, json={"display_label": "", "study_note": normal_note})
    assert normal.status_code == 200 and normal.json()["study_note"] == normal_note
    assert client.get(route).json()["study_note"] == normal_note
    client.post("/api/auth/logout")
    assert client.get(route).status_code == 401
    assert client.put(route, json={"display_label": "", "study_note": ""}).status_code == 401


@pytest.mark.parametrize("delete", ["course", "user"])
def test_user_or_course_deletion_cascades_only_owned_annotations(store, delete):
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private','node','标题','笔记',?)", (now(),))
    store.execute("INSERT INTO mindmap_annotations VALUES ('bob','builtin','node','另一个人','保留笔记',?)", (now(),))
    if delete == "course":
        store.execute("DELETE FROM courses WHERE id='private'")
    else:
        store.execute("DELETE FROM users WHERE id='alice'")
    rows = store.all("SELECT user_id,course_id,study_note FROM mindmap_annotations")
    assert rows == [{"user_id": "bob", "course_id": "builtin", "study_note": "保留笔记"}]


def test_read_only_tree_can_open_pre_migration_database_without_annotation_table(store):
    body_document(store, "a")
    chunk(store, "a", "第一章教材", 1)
    store.execute("DROP TABLE mindmap_annotations")
    with store.connection() as conn:
        conn.execute("PRAGMA query_only=ON")
        data = build_knowledge_map(conn, store.one("SELECT * FROM courses WHERE id='private'"), "alice")
        assert data["tree"]["total_headings"] == 1
        assert all("display_label" not in node for node in data["tree"]["nodes"])
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='mindmap_annotations'").fetchone() is None
