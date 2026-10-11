"""Outline navigation, graph sampling and material ownership regressions."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db import Database, dumps, now
from backend.app.ingestion import rebuild_extracted_graph
from backend.app.knowledge_map import build_knowledge_map, heading_id, natural_key
from backend.app.main import create_app
from backend.tests.test_api import FakeProvider, register


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "map.sqlite3")
    for user in ("alice", "bob"):
        db.execute("INSERT INTO users VALUES (?,?,?,'unused',?)", (user, user, user, now()))
    db.execute("INSERT INTO courses VALUES ('private','alice','教材测试','','C','#557766','book','助教',0,?)", (now(),))
    db.execute("INSERT INTO courses VALUES ('builtin',NULL,'原创导学','','B','#557766','book','助教',1,?)", (now(),))
    return db


def document(db, doc_id, *, course="private", owner="alice", source="uploaded_pdf", status="ready"):
    db.execute(
        "INSERT INTO documents (id,course_id,user_id,title,filename,path,status,page_count,source_type,license,created_at,quality) "
        "VALUES (?,?,?,?,?,?,?,400,?,'test',?,?)",
        (doc_id, course, owner, "教材" + doc_id, doc_id + ".pdf", "not-opened.pdf", status, source,
         "2026-10-03T00:00:00Z", dumps({"chapter_source": "PDF outline"})))


def chunk(db, doc_id, section, page, *, owner="alice"):
    course = db.one("SELECT course_id FROM documents WHERE id=?", (doc_id,))["course_id"]
    db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,NULL)", (
        f"{doc_id}-{page}-{db.one('SELECT COUNT(*) AS n FROM chunks')['n']}", doc_id, course, owner,
        page, section, "真实教材内容，不参与知识关系推断。"))


def graph(db, course="private", user="alice"):
    with db.connection() as conn:
        return build_knowledge_map(conn, db.one("SELECT * FROM courses WHERE id=?", (course,)), user)


def assert_forest(tree):
    nodes = {node["id"]: node for node in tree["nodes"]}
    assert len(nodes) == len(tree["nodes"])
    assert set(tree["root_ids"]) == {node["id"] for node in nodes.values() if node["parent_id"] is None}
    visited = set()

    def visit(node_id, ancestors=()):
        assert node_id not in ancestors
        assert node_id not in visited
        visited.add(node_id)
        parent = nodes[node_id]
        for order, child_id in enumerate(parent["children"]):
            child = nodes[child_id]
            assert child["parent_id"] == node_id
            assert child["depth"] == parent["depth"] + 1
            assert child["order"] == order
            visit(child_id, (*ancestors, node_id))

    for root in tree["root_ids"]:
        visit(root)
    assert visited == set(nodes)


def test_natural_chapter_order_covers_chinese_nine_tens_and_arabic_numbers():
    chapters = ["第二十章", "第十一章", "第九章", "第二章", "第十章", "第一章"]
    assert sorted(chapters, key=natural_key) == ["第一章", "第二章", "第九章", "第十章", "第十一章", "第二十章"]
    assert natural_key("第九章") == natural_key("第9章")
    assert natural_key("第一百零二章") == natural_key("第102章")


def test_outline_front_matter_is_not_a_knowledge_chapter_but_source_chunks_survive(store):
    document(store, "a")
    store.execute("UPDATE documents SET quality=? WHERE id='a'", (dumps({"chapter_source": "PDF outline", "outline_entries": 12}),))
    for page, section in ((1, "封面"), (2, "书名"), (7, "前言"), (8, "前言 / 写给读者"),
                          (19, "第1章 真实知识"), (20, "第1章 真实知识 / 1.1 内容"), (72, "第2章 更多知识")):
        chunk(store, "a", section, page)
    before = store.all("SELECT * FROM chunks WHERE document_id='a'")
    result = graph(store)
    assert [node["label"] for node in result["tree"]["nodes"] if node["kind"] == "chapter"] == ["第1章 真实知识", "第2章 更多知识"]
    assert result["metadata"]["total_headings"] == 3
    assert any("3 个首章之前" in warning for warning in result["metadata"]["warnings"])
    assert store.all("SELECT * FROM chunks WHERE document_id='a'") == before
    assert heading_id("a", "第1章 真实知识 / 1.1 内容") in {node["id"] for node in result["tree"]["nodes"]}


def test_front_matter_filter_requires_real_pdf_outline_and_prior_position(store):
    for doc_id, source, outline_entries, preface_page in (("body", "uploaded_pdf", 0, 1),
                                                        ("text", "uploaded_txt", 5, 1),
                                                        ("later", "uploaded_pdf", 5, 15)):
        document(store, doc_id, source=source)
        store.execute("UPDATE documents SET quality=? WHERE id=?", (
            dumps({"chapter_source": "PDF outline" if outline_entries else "正文标题提取（待核验）", "outline_entries": outline_entries}), doc_id))
        chunk(store, doc_id, "前言", preface_page)
        chunk(store, doc_id, "第1章 真实知识", 10)
    nodes = {node["id"] for node in graph(store)["tree"]["nodes"]}
    assert all(heading_id(doc_id, "前言") in nodes for doc_id in ("body", "text", "later"))


def test_full_pdf_paths_distinguish_same_names_across_sections_and_documents(store):
    for doc in ("a", "b"):
        document(store, doc)
        chunk(store, doc, "第一章 进程 / 1.1 创建 / 定义", 12)
        chunk(store, doc, "第一章 进程 / 1.1 创建 / 定义", 15)
        chunk(store, doc, "第一章 进程 / 1.2 终止 / 定义", 20)
    result = graph(store)
    tree = result["tree"]
    assert_forest(tree)
    definitions = [node for node in tree["nodes"] if node["label"] == "定义"]
    assert len(definitions) == 4 and len({node["id"] for node in definitions}) == 4
    first = next(node for node in definitions if node["id"] == heading_id("a", "第一章 进程 / 1.1 创建 / 定义"))
    assert (first["page_start"], first["page_end"], first["page_kind"]) == (12, 15, "physical")
    assert first["parent_id"] == heading_id("a", "第一章 进程 / 1.1 创建")
    assert first["heading"] == "第一章 进程 / 1.1 创建 / 定义"
    assert first["verified"] is False
    assert all(node["kind"] == "heading" and "待人工核验" in node["source"] for node in result["nodes"])
    definition_ids = {node["id"] for node in definitions}
    assert all(edge["source"] not in {heading_id(doc, "第一章 进程") for doc in ("a", "b")}
               for edge in result["edges"] if edge["target"] in definition_ids)


def test_numbered_parent_must_exist_and_deep_section_keeps_actual_source_path(store):
    document(store, "a")
    chunk(store, "a", "第一章 进程 / 1.2 执行", 10)
    chunk(store, "a", "第一章 进程 / 1.2.1 上下文", 12)
    chunk(store, "a", "第一章 进程 / 1.3.1 没有父标题", 16)
    result = graph(store)
    assert_forest(result["tree"])
    lookup = {node["id"]: node for node in result["tree"]["nodes"]}
    deep = lookup[heading_id("a", "第一章 进程 / 1.2.1 上下文")]
    assert deep["parent_id"] == heading_id("a", "第一章 进程 / 1.2 执行")
    assert deep["depth"] == 3 and deep["hierarchy_source"] == "observed_heading_numbers"
    assert deep["heading"] == "第一章 进程 / 1.2.1 上下文"
    assert lookup[deep["parent_id"]]["page_end"] == 12
    orphan = lookup[heading_id("a", "第一章 进程 / 1.3.1 没有父标题")]
    assert orphan["parent_id"] == heading_id("a", "第一章 进程")
    assert not any(node["label"].startswith("1.3 ") for node in lookup.values())
    edge = next(edge for edge in result["edges"] if edge["target"] == deep["id"])
    assert edge["source"] == deep["parent_id"] and edge["kind"] == "structural"


def test_navigation_is_complete_when_graph_is_capped_and_ancestors_survive_search(store):
    document(store, "a")
    for chapter in range(1, 11):
        for section in range(1, 21):
            chunk(store, "a", f"第{chapter}章 主题{chapter} / {chapter}.{section} 小节{chapter}-{section}",
                  (chapter - 1) * 20 + section)
    result = graph(store)
    assert_forest(result["tree"])
    assert len(result["nodes"]) == 180
    assert len(result["tree"]["nodes"]) == 211
    heading_metadata = {"mode", "total_headings", "displayed_headings", "graph_limit", "sampled", "headings_count_excludes_document_roots"}
    assert {key: result["metadata"][key] for key in heading_metadata} == {
        "mode": "textbook_structure", "total_headings": 210, "displayed_headings": 180,
        "graph_limit": 180, "sampled": True, "headings_count_excludes_document_roots": True,
    }
    assert result["metadata"]["total_figures"] == result["tree"]["total_figures"] == 0
    assert result["metadata"]["figure_index"][0]["status"] == "not_indexed"
    assert any("人工核准" in warning for warning in result["metadata"]["warnings"])
    for chapter in range(1, 11):
        assert any(node["label"] == f"主题{chapter}" for node in result["nodes"])
        assert any(node["label"].startswith(f"小节{chapter}-") for node in result["nodes"])
    lookup = {node["id"]: node for node in result["tree"]["nodes"]}
    omitted = next(node for node in lookup.values() if node["kind"] == "section" and node["concept_id"] is None)
    assert omitted["page_start"] > 0 and omitted["document_id"] == "a"
    assert lookup[omitted["parent_id"]]["parent_id"] == "document:a"
    with store.connection() as conn:
        rebuild_extracted_graph(conn, "private")
    assert {row["id"] for row in store.all("SELECT id FROM concepts WHERE course_id='private'")} == {node["id"] for node in result["nodes"]}


def test_graph_limit_remains_strict_when_chapter_roots_alone_exceed_180(store):
    document(store, "a")
    for chapter in range(1, 192):
        chunk(store, "a", f"第{chapter}章 主题{chapter}", chapter)
    result = graph(store)
    assert len(result["nodes"]) == 180
    assert result["tree"]["total_headings"] == 191
    assert len(result["tree"]["nodes"]) == 192 and result["metadata"]["sampled"]
    assert_forest(result["tree"])


def test_source_pages_prefer_body_and_skip_whole_toc_page_even_for_isolated_headings(store):
    document(store, "a")
    # This title has its own chunk; detecting leaders per chunk would miss it.
    chunk(store, "a", "第一章 进程", 3)
    chunk(store, "a", "第二章 调度", 3)
    store.execute("UPDATE chunks SET text=? WHERE document_id='a' AND section='第二章 调度'", (
        "2.1 调度目标 ................. 18\n2.2 调度算法 ............ 20\n2.3 自查 ........... 25",))
    chunk(store, "a", "第一章 进程", 27)
    chunk(store, "a", "第一章 进程 / 1.1 线程", 29)
    chunk(store, "a", "第二章 调度", 50)
    result = graph(store)
    lookup = {node["label"]: node for node in result["nodes"]}
    assert lookup["进程"]["page_start"] == 27
    assert lookup["调度"]["page_start"] == 50
    assert all(node["location_confidence"] == "extracted_heading" for node in result["nodes"])
    assert any("1 个目录样式页" in warning for warning in result["metadata"]["warnings"])
    assert store.one("SELECT COUNT(*) AS n FROM chunks WHERE page=3")["n"] == 2


def test_directory_prose_and_single_dotted_line_are_not_toc_pages(store):
    document(store, "a")
    chunk(store, "a", "第一章 文件目录结构", 35)
    store.execute("UPDATE chunks SET text=? WHERE document_id='a'", (
        "目录结构用于组织文件。\n图1 文件目录 ........... 35\n说明目录并不等于本书的目录页。",))
    result = graph(store)
    assert result["nodes"][0]["page_start"] == 35
    assert not any("目录样式页" in warning for warning in result["metadata"]["warnings"])


def test_body_introduction_on_final_toc_page_retains_its_title_anchor(store):
    document(store, "a")
    chunk(store, "a", "第十章 后续章节", 27)
    store.execute("UPDATE chunks SET text=? WHERE document_id='a'", (
        "10.1 接口 .......... 110\n10.2 内核 .......... 120\n10.3 复习 .......... 130",))
    chunk(store, "a", "第一章 操作系统引论", 27)
    chunk(store, "a", "第一章 操作系统引论", 27)
    last = store.one("SELECT id FROM chunks ORDER BY rowid DESC LIMIT 1")["id"]
    store.execute("UPDATE chunks SET text=? WHERE id=?", (
        "第一章 操作系统引论\n计算机系统由硬件和软件两部分组成。操作系统负责管理这些资源，"
        "并为应用程序提供统一的服务接口。本章讨论操作系统的目标、作用与发展过程。", last))
    result = graph(store)
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["label"] == "操作系统引论"
    assert result["nodes"][0]["page_start"] == 27
    assert result["tree"]["total_headings"] == 1


def test_chapter_order_and_adjacency_use_book_order_not_semantic_prerequisites(store):
    document(store, "a")
    for title in ("第10章 文件", "第2章 调度", "第1章 进程"):
        chunk(store, "a", title, 1)
    result = graph(store)
    assert [node["label"] for node in result["tree"]["nodes"][1:]] == ["第1章 进程", "第2章 调度", "第10章 文件"]
    assert len(result["edges"]) == 2
    assert all(edge["kind"] == "sequence" and "不表示语义先修" in edge["source_label"] for edge in result["edges"])


def test_builtin_outline_has_natural_order_without_fabricating_pdf_chapter_mapping(store):
    for number in (1, 10, 2, 12):
        store.execute("INSERT INTO concepts VALUES (?,'builtin',?,?,?)", (
            f"os-{number}", f"概念{number}", f"第{number}章", "课程手册知识结构"))
    store.execute("INSERT INTO edges VALUES ('builtin','os-1','os-2','学习关联')")
    document(store, "reference", course="builtin", owner=None)
    chunk(store, "reference", "第十二章 真实书中不同的章节", 200, owner=None)
    result = graph(store, "builtin")
    assert [node["id"] for node in result["nodes"]] == ["os-1", "os-2", "os-10", "os-12"]
    assert all(node["kind"] == "concept" and node["document_id"] is None and node["page_start"] is None for node in result["nodes"])
    assert result["tree"]["root_ids"] == ["outline:builtin", "document:reference"]
    assert "不代表上传 PDF" in result["tree"]["nodes"][0]["source"]
    assert next(node for node in result["tree"]["nodes"] if node["document_id"] == "reference" and node["kind"] == "chapter")["concept_id"] is None
    assert result["edges"][0]["kind"] == "association"
    assert_forest(result["tree"])


def test_mastery_zero_is_measured_and_owned_by_current_student(store):
    document(store, "a")
    chunk(store, "a", "第一章 进程", 4)
    chunk(store, "a", "第二章 调度", 8)
    store.execute("INSERT INTO mastery VALUES ('alice','private','进程',2,0)")
    store.execute("INSERT INTO mastery VALUES ('bob','private','调度',2,2)")
    lookup = {node["label"]: node for node in graph(store)["nodes"]}
    assert lookup["进程"]["mastery"] == 0
    assert lookup["调度"]["mastery"] is None


def test_unsampled_tree_mastery_preserves_zero_positive_and_unmeasured_seed_labels(store):
    document(store, "a")
    for chapter in range(1, 192):
        chunk(store, "a", f"第{chapter}章 主题{chapter}", chapter)
    for label, attempts, correct in (("主题181", 2, 0), ("主题182", 4, 3),
                                     ("主题183", 0, 0), ("教材a", 2, 2)):
        store.execute("INSERT INTO mastery VALUES ('alice','private',?,?,?)", (label, attempts, correct))
    store.execute("INSERT INTO mastery VALUES ('bob','private','主题184',1,1)")
    result = graph(store)
    graph_ids = {node["id"] for node in result["nodes"]}
    tree = {node["label"]: node for node in result["tree"]["nodes"]}
    assert tree["第181章 主题181"]["id"] not in graph_ids
    assert tree["第182章 主题182"]["id"] not in graph_ids
    assert tree["第181章 主题181"]["mastery"] == 0
    assert tree["第182章 主题182"]["mastery"] == .75
    assert tree["第183章 主题183"]["mastery"] is None
    assert tree["第184章 主题184"]["mastery"] is None
    assert tree["教材a"]["mastery"] is None

    # Built-in tree labels include a chapter prefix but mastery uses seed labels.
    for index, (label, attempts, correct) in enumerate((("栈", 2, 0), ("队列", 3, 2)), 1):
        store.execute("INSERT INTO concepts VALUES (?,'builtin',?,?,?)", (
            f"builtin-{index}", label, f"第{index}章", "课程手册知识结构"))
        store.execute("INSERT INTO mastery VALUES ('alice','builtin',?,?,?)", (label, attempts, correct))
    builtin = graph(store, "builtin")
    builtin_tree = {node["id"]: node for node in builtin["tree"]["nodes"]}
    assert builtin_tree["builtin-1"]["label"] == "第1章 · 栈"
    assert builtin_tree["builtin-1"]["mastery"] == 0
    assert builtin_tree["builtin-2"]["mastery"] == .667
    assert builtin_tree["outline:builtin"]["mastery"] is None


def test_builtin_materials_include_public_and_own_ready_documents_only(store):
    for doc_id, owner, status in (("public", None, "ready"), ("mine", "alice", "ready"),
                                  ("foreign", "bob", "ready"), ("queued", "alice", "queued"),
                                  ("failed", "alice", "failed")):
        document(store, doc_id, course="builtin", owner=owner, status=status)
        chunk(store, doc_id, "第一章 独立教材标题" + doc_id, 10, owner=owner)
    # Corrupted/mismatched ownership must not expose another student's chunks.
    chunk(store, "mine", "第二章 不应暴露的异主块", 15, owner="bob")
    alice = graph(store, "builtin")
    bob = graph(store, "builtin", "bob")
    assert {node["document_id"] for node in alice["tree"]["nodes"]} == {None, "public", "mine"}
    assert {node["document_id"] for node in bob["tree"]["nodes"]} == {None, "public", "foreign"}
    assert not any("异主块" in node["heading"] for node in alice["tree"]["nodes"])


def test_empty_or_unheaded_material_stays_navigable_without_invented_concepts(store):
    assert graph(store)["tree"] == {"root_ids": [], "nodes": [], "total_headings": 0, "total_figures": 0}
    document(store, "a", source="uploaded_txt")
    chunk(store, "a", "正文", 1)
    chunk(store, "a", "目录 / 第一章 从目录不补章节", 2)
    result = graph(store)
    assert result["nodes"] == [] and result["edges"] == []
    assert result["tree"]["root_ids"] == ["document:a"]
    assert len(result["tree"]["nodes"]) == 1
    assert result["tree"]["nodes"][0]["page_kind"] == "logical"


def test_read_model_is_read_only_and_deletion_removes_only_its_own_headings(store):
    for doc in ("a", "b"):
        document(store, doc)
        chunk(store, doc, "第一章 进程 / 1.1 概念", 3)
    with store.connection() as conn:
        conn.execute("PRAGMA query_only=ON")
        first = build_knowledge_map(conn, store.one("SELECT * FROM courses WHERE id='private'"), "alice")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM concepts")
    surviving = {node["id"] for node in first["tree"]["nodes"] if node["document_id"] == "b"}
    store.execute("DELETE FROM documents WHERE id='a'")
    after = graph(store)
    assert {node["id"] for node in after["tree"]["nodes"]} == surviving
    assert_forest(after["tree"])


def test_graph_endpoint_keeps_auth_ownership_and_does_not_rebuild_on_get(tmp_path):
    app = create_app(Settings(root=tmp_path, embedding_enabled=False, api_key="", model="fake-test-model"), FakeProvider())
    with TestClient(app) as client:
        assert client.get("/api/courses/unknown/graph").status_code == 401
        user = register(client)
        course_id = client.post("/api/courses", json={"title": "专业课"}).json()["id"]
        db = app.state.db
        document(db, "a", course=course_id, owner=user["id"])
        chunk(db, "a", "第一章 进程 / 1.1 线程", 8, owner=user["id"])
        db.execute("INSERT INTO concepts VALUES ('stale',?,'旧缓存','旧章节','old')", (course_id,))
        response = client.get(f"/api/courses/{course_id}/graph")
        assert response.status_code == 200
        result = response.json()
        assert {"id", "label", "chapter", "source", "mastery"} <= set(result["nodes"][0])
        assert {node["label"] for node in result["nodes"]} == {"进程", "线程"}
        assert db.one("SELECT id FROM concepts WHERE course_id=?", (course_id,))["id"] == "stale"
        assert_forest(result["tree"])
        with TestClient(app) as bob:
            register(bob, "bob")
            assert bob.get(f"/api/courses/{course_id}/graph").status_code == 404


def body_document(db, doc_id, **kwargs):
    document(db, doc_id, **kwargs)
    db.execute("UPDATE documents SET quality=? WHERE id=?", (
        dumps({"chapter_source": "正文标题提取（待核验）", "outline_entries": 0}), doc_id))


def test_body_prose_references_do_not_corrupt_real_numbered_parent(store):
    body_document(store, "a")
    for label, page in (("1.5 节中再对微机 OS 和网络 OS 作简单说明", 41),
                        ("3.3 为止的 DOS 版本", 67), ("1.5 OS 结构设计", 99),
                        ("1.5.1 传统的操作系统结构", 100), ("1.5.4 微内核 OS 结构", 118)):
        chunk(store, "a", "第一章操作系统引论 / " + label, page)
    before = store.one("SELECT COUNT(*) AS n FROM chunks")["n"]
    result = graph(store)
    assert_forest(result["tree"])
    nodes = {node["label"]: node for node in result["tree"]["nodes"]}
    assert not any("节中再" in label or "DOS 版本" in label for label in nodes)
    actual_parent = nodes["1.5 OS 结构设计"]
    assert actual_parent["id"] == heading_id("a", "第一章操作系统引论 / 1.5 OS 结构设计")
    assert len(actual_parent["children"]) == 2
    assert nodes["1.5.1 传统的操作系统结构"]["parent_id"] == actual_parent["id"]
    assert actual_parent["page_start"] == 99
    assert any("排除 2 个正文引用或错章编号标题" in message for message in result["metadata"]["warnings"])
    assert store.one("SELECT COUNT(*) AS n FROM chunks")["n"] == before


def test_literal_slashes_restore_full_body_titles_without_changing_numbered_anchors(store):
    body_document(store, "a")
    for label, page in (("8.4 客户 / 服务器模式", 1060),
                        ("8.4.1 两层结构客户 / 服务器模式的局限性", 1061),
                        ("8.4.3 两层客户 / 服务器与三层客户 / 服务器的比较", 1067)):
        chunk(store, "a", "第八章网络操作系统 / " + label, page)
    result = graph(store)
    assert_forest(result["tree"])
    nodes = {node["label"]: node for node in result["tree"]["nodes"]}
    assert len(nodes) == 5  # Document, chapter, one real section, two subsections.
    parent = nodes["8.4 客户 / 服务器模式"]
    child = nodes["8.4.1 两层结构客户 / 服务器模式的局限性"]
    assert parent["id"] == heading_id("a", "第八章网络操作系统 / 8.4 客户")
    assert child["id"] == heading_id("a", "第八章网络操作系统 / 8.4.1 两层结构客户")
    assert child["parent_id"] == parent["id"] and child["depth"] == 3
    assert child["heading"] == "第八章网络操作系统 / 8.4.1 两层结构客户 / 服务器模式的局限性"
    assert not any(label in {"服务器模式", "服务器的比较"} for label in nodes)


def test_shortened_continuation_paths_do_not_replace_the_full_slash_title(store):
    body_document(store, "a")
    chunk(store, "a", "第一章操作系统 / 1.5 客户", 100)
    chunk(store, "a", "第一章操作系统 / 1.5 客户 / 服务器模式", 101)
    chunk(store, "a", "第一章操作系统 / 1.5 客户", 102)
    result = graph(store)
    nodes = result["tree"]["nodes"]
    title = next(node for node in nodes if node["id"] == heading_id("a", "第一章操作系统 / 1.5 客户"))
    assert title["label"] == "1.5 客户 / 服务器模式"
    assert (title["page_start"], title["page_end"]) == (100, 102)
    assert len(nodes) == 3


def test_repeated_real_numbers_and_labels_remain_separate_and_do_not_share_mastery(store):
    body_document(store, "a")
    chunk(store, "a", "第三章处理机调度 / 3.2.1 调度队列模型", 298)
    chunk(store, "a", "第三章处理机调度 / 3.2.1 先来先服务调度", 309)
    chunk(store, "a", "第一章操作系统 / 1.1 文件管理", 10)
    chunk(store, "a", "第六章文件管理", 728)
    store.execute("INSERT INTO mastery VALUES ('alice','private','文件管理',2,2)")
    result = graph(store)
    assert len([node for node in result["tree"]["nodes"] if node["label"].startswith("3.2.1")]) == 2
    duplicate_labels = [node for node in result["nodes"] if node["label"] == "文件管理"]
    assert len(duplicate_labels) == 2 and all(node["mastery"] is None for node in duplicate_labels)
    assert_forest(result["tree"])
