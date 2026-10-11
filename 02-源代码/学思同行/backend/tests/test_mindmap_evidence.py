"""Grounded mindmap excerpts: full-tree nodes, bounded originals and ownership."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import dumps
from backend.app.knowledge_map import build_mindmap_evidence, heading_id
from backend.app.main import create_app
from backend.tests.test_api import FakeProvider, register
from backend.tests.test_graph_structure import body_document, chunk, document, store


def evidence(db, node_id, *, course="private", user="alice"):
    with db.connection() as conn:
        conn.execute("PRAGMA query_only=ON")
        return build_mindmap_evidence(conn, db.one("SELECT * FROM courses WHERE id=?", (course,)), user, node_id)


def text_chunk(db, doc_id, section, page, text, *, owner="alice"):
    chunk(db, doc_id, section, page, owner=owner)
    last = db.one("SELECT id FROM chunks ORDER BY rowid DESC LIMIT 1")["id"]
    db.execute("UPDATE chunks SET text=? WHERE id=?", (text, last))
    return last


def test_evidence_for_an_unsampled_leaf_is_original_bounded_and_read_only(store):
    body_document(store, "a")
    for number in range(1, 192):
        chunk(store, "a", f"第{number}章 真实标题{number}", number)
    section = "第191章 真实标题191 / 191.1 地址变换"
    ids = []
    for page in range(192, 198):
        text = "191.1 地址变换\n" + f"第{page}页中，页表记录页号和页框之间的对应关系。" * 25
        ids.append(text_chunk(store, "a", section, page, text))
    result = evidence(store, heading_id("a", section))
    assert result["heading"] == section
    assert len(result["evidence"]) == 3
    assert [item["page"] for item in result["evidence"]] == [192, 193, 194]
    assert [item["chunk_id"] for item in result["evidence"]] == ids[:3]
    assert all(12 < len(item["excerpt"]) <= 321 for item in result["evidence"])
    assert all(item["page_kind"] == "physical" for item in result["evidence"])
    assert all(not item["excerpt"].startswith("191.1 地址变换") for item in result["evidence"])
    assert "不是 AI 要点总结" in result["notice"]
    for item in result["evidence"]:
        original = store.one("SELECT text FROM chunks WHERE id=?", (item["chunk_id"],))["text"]
        assert item["excerpt"].rstrip("…") in original
    assert store.one("SELECT COUNT(*) AS n FROM chunks")["n"] == 197


def test_evidence_uses_inferred_descendants_without_inventing_parent_text(store):
    body_document(store, "a")
    parent = "第四章存储器管理 / 4.4 基本分页"
    text_chunk(store, "a", parent, 475, "4.4 基本分页")
    child = "第四章存储器管理 / 4.4.2 地址变换"
    cid = text_chunk(store, "a", child, 481, "4.4.2 地址变换\n地址变换机构利用页表查找页框，并保留页内偏移量。")
    result = evidence(store, heading_id("a", parent))
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["chunk_id"] == cid
    assert result["evidence"][0]["section"] == child
    assert "基本分页" not in result["evidence"][0]["excerpt"]


def test_restored_slash_title_keeps_its_original_excerpt_and_anchor(store):
    body_document(store, "a")
    path = "第八章网络操作系统 / 8.4 客户 / 服务器模式"
    cid = text_chunk(store, "a", path, 1060, "8.4 客户 / 服务器模式\n客户向服务器发送请求，服务器执行处理后返回结果。")
    node_id = heading_id("a", "第八章网络操作系统 / 8.4 客户")
    result = evidence(store, node_id)
    assert result["heading"] == path
    assert result["evidence"][0]["chunk_id"] == cid
    assert result["evidence"][0]["excerpt"] == "客户向服务器发送请求，服务器执行处理后返回结果。"


def test_directory_title_only_and_unrelated_ancestors_do_not_become_evidence(store):
    document(store, "a")
    path = "第一章 进程 / 1.1 线程"
    text_chunk(store, "a", path, 3, "1.1 线程 .......... 35")
    text_chunk(store, "a", path, 35, "1.1 线程")
    text_chunk(store, "a", "第一章 进程", 35, "这个段落讨论的是同一页上的另一个主题，不能自动移入线程小节。")
    result = evidence(store, heading_id("a", path))
    assert result["evidence"] == []
    assert "不自动补造要点" in result["notice"]


def test_manual_outline_and_document_roots_have_explicitly_empty_evidence(store):
    store.execute("INSERT INTO concepts VALUES ('os-1','builtin','线程','第1章','original')")
    manual = evidence(store, "os-1", course="builtin")
    assert manual["evidence"] == [] and "没有真实教材章节" in manual["notice"]
    document(store, "a")
    root = evidence(store, "document:a")
    assert root["evidence"] == [] and "请选择章节或小节" in root["notice"]
    assert evidence(store, "missing") is None


def test_evidence_filters_document_and_chunk_ownership_and_ready_status(store):
    for doc_id, owner, status in (("public", None, "ready"), ("mine", "alice", "ready"),
                                  ("foreign", "bob", "ready"), ("queued", "alice", "queued")):
        document(store, doc_id, course="builtin", owner=owner, status=status)
        text_chunk(store, doc_id, "第一章 线程", 10, "线程是进程内的执行单元，共享进程所分配的资源。", owner=owner)
    text_chunk(store, "mine", "第一章 线程", 10, "BOB_PRIVATE_SENTINEL 这是属于另一个学生的正文，不能读取。", owner="bob")
    own = evidence(store, heading_id("mine", "第一章 线程"), course="builtin")
    assert len(own["evidence"]) == 1
    assert "BOB_PRIVATE_SENTINEL" not in dumps(own)
    assert evidence(store, heading_id("public", "第一章 线程"), course="builtin")["evidence"]
    assert evidence(store, heading_id("foreign", "第一章 线程"), course="builtin") is None
    assert evidence(store, heading_id("queued", "第一章 线程"), course="builtin") is None


def test_evidence_endpoint_authorizes_course_and_makes_no_provider_calls(tmp_path):
    provider = FakeProvider()
    app = create_app(Settings(root=tmp_path, embedding_enabled=False, api_key="", model="fake-test-model"), provider)
    with TestClient(app) as client:
        assert client.get("/api/courses/unknown/mindmap/nodes/missing/evidence").status_code == 401
        user = register(client)
        course_id = client.post("/api/courses", json={"title": "专业课"}).json()["id"]
        db = app.state.db
        body_document(db, "a", course=course_id, owner=user["id"])
        path = "第一章 进程 / 1.1 线程"
        text_chunk(db, "a", path, 8, "1.1 线程\n线程是进程内的执行单元，共享进程所分配的资源。", owner=user["id"])
        route = f"/api/courses/{course_id}/mindmap/nodes/{heading_id('a', path)}/evidence"
        assert client.get(route).status_code == 200
        assert len(client.get(route).json()["evidence"]) == 1
        assert client.get(f"/api/courses/{course_id}/mindmap/nodes/missing/evidence").status_code == 404
        assert provider.histories == [] and provider.stream_histories == []
        with TestClient(app) as bob:
            register(bob, "bob")
            assert bob.get(route).status_code == 404
