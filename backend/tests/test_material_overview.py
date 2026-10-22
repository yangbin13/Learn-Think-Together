"""Whole-book evidence selection keeps ownership, scope and actual text intact."""
import pytest

from backend.app.db import Database, now
from backend.app.material_overview import material_overview_sources, requests_material_overview


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "overview.sqlite3")
    for user in ("alice", "bob"):
        db.execute("INSERT INTO users VALUES (?,?,?,'unused',?)", (user, user, user, now()))
    for course, owner, builtin in (("mine", "alice", 0), ("other", "bob", 0), ("builtin", None, 1)):
        db.execute("INSERT INTO courses VALUES (?,?,?,'','C','#557766','book','助教',?,?)",
                   (course, owner, course, builtin, now()))
    return db


def add_document(db, doc_id, course="mine", owner="alice", status="ready"):
    db.execute("INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at) "
               "VALUES (?,?,?,?,?,'not-opened.pdf',?,'uploaded_pdf','test',?)",
               (doc_id, course, owner, "教材" + doc_id, doc_id + ".pdf", status, now()))


def add_chunk(db, chunk_id, doc_id, text, section="前言", page=2, owner="alice", course=None):
    if course is None:
        course = db.one("SELECT course_id FROM documents WHERE id=?", (doc_id,))["course_id"]
    db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,NULL)",
               (chunk_id, doc_id, course, owner, page, section, text))


@pytest.mark.parametrize("question", [
    "梳理一下数据结构与算法图解教材的核心知识",
    "请概括本书的知识框架", "介绍教材主要内容", "教材有哪些学习模块？",
    "总结一下全书", "请依据教材目录，概括数据结构与算法图解的三个主要学习模块，并标注对应章节",
])
def test_whole_material_requests(question):
    assert requests_material_overview(question)


@pytest.mark.parametrize("question", [
    "概括教材第3章的核心知识", "只总结某章的主要内容", "根据教材，概括栈和队列的核心知识",
    "梳理教材中二分查找的核心知识", "概括教材里关于散列表的核心知识",
    "请概括教材的核心知识，只给我一个问题，不要给答案。",
    "从教材选一个具体概念，只问我一个可回答的入门问题；暂时不要讲解结论",
    "梳理教材的核心知识，用问题一步步引导我理解。",
    "不要总结教材核心知识，只讲二分查找", "他说‘概括教材核心知识’，我想问一道练习题。",
    "选择一个重点概念，用生活例子帮我理解", "根据教材，为我推荐学习顺序", None,
])
def test_topic_or_single_hint_does_not_pin_whole_book(question):
    assert not requests_material_overview(question)


def test_prioritizes_actual_multi_chapter_narrative_and_preserves_fields(store):
    add_document(store, "book")
    add_chunk(store, "toc", "book", "第1章 基础\n第2章 效率", "目录", 12)
    text = "本书内容如下。第1章介绍基础，第2章讨论效率，第3章介绍排序，第4章介绍图。"
    add_chunk(store, "overview", "book", text, "前言", 9)
    add_chunk(store, "thanks", "book", "感谢所有支持本书出版的朋友。", "前言", 10)
    add_chunk(store, "copyright", "book", "不得抄袭本书内容。", "版权声明", 6)
    add_chunk(store, "heading", "book", "目录", "目录", 11)
    add_chunk(store, "topic", "book", "第1章数组的查找算法需要逐项比较。", "第1章 基础", 15)
    sources = material_overview_sources(store, "mine", "alice")
    assert [item["chunk_id"] for item in sources] == ["overview", "toc"]
    assert sources[0] == {"chunk_id": "overview", "document_id": "book", "page": 9,
                          "section": "前言", "text": text, "title": "教材book", "source_type": "uploaded_pdf"}


def test_authorization_checks_chunk_document_course_and_readiness(store):
    text = "本书内容如下：第1章基础，第2章算法。"
    for name, course, owner, status in (("ok", "mine", "alice", "ready"),
                                       ("foreign_doc", "mine", "bob", "ready"),
                                       ("processing", "mine", "alice", "processing"),
                                       ("foreign_course", "other", "bob", "ready"),
                                       ("shared", "builtin", None, "ready")):
        add_document(store, name, course, owner, status)
        add_chunk(store, name, name, text, owner=owner)
    add_chunk(store, "foreign_chunk", "ok", text, owner="bob")
    add_chunk(store, "shared_chunk_foreign_doc", "foreign_doc", text, owner=None)
    add_chunk(store, "mismatched_course", "foreign_course", text, owner="alice", course="mine")
    assert [item["chunk_id"] for item in material_overview_sources(store, "mine", "alice")] == ["ok"]
    assert material_overview_sources(store, "mine", "bob") == []
    assert material_overview_sources(store, "other", "alice") == []
    assert [item["chunk_id"] for item in material_overview_sources(store, "builtin", "bob")] == ["shared"]
    store.execute("UPDATE documents SET status='failed' WHERE id='ok'")
    assert material_overview_sources(store, "mine", "alice") == []


def test_english_and_body_overviews_and_six_source_limit(store):
    add_document(store, "book")
    add_chunk(store, "english", "book", "This book is organized into chapters. Chapter 1 covers input. Chapter 2 covers output.", "Preface")
    add_chunk(store, "body", "book", "全书分为两个部分，第1章介绍基础，第2章介绍应用。", "阅读指南", 3)
    for index in range(8):
        add_chunk(store, f"toc-{index}", "book", f"Chapter {index + 3} Topic {index}\nMore actual contents.", "Contents", index + 4)
    sources = material_overview_sources(store, "mine", "alice")
    assert len(sources) == 6
    assert {item["chunk_id"] for item in sources[:2]} == {"english", "body"}
    assert all(item["document_id"] == "book" and item["text"] for item in sources)


def test_no_overview_does_not_invent_one(store):
    add_document(store, "book")
    add_chunk(store, "topic", "book", "链表的每个结点记录下一个结点的地址。", "链表")
    assert material_overview_sources(store, "mine", "alice") == []
