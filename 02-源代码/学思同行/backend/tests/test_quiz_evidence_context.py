"""Original textbook fixtures for bounded local evidence, without model calls."""
from copy import deepcopy

import pytest

from backend.app.db import Database, dumps, now
from backend.app.quiz_evidence import (
    MAX_QUIZ_EVIDENCE_CHARACTERS,
    MAX_QUIZ_EVIDENCE_CHUNKS,
    prepare_quiz_evidence,
)


@pytest.fixture
def textbook(tmp_path):
    db = Database(tmp_path / "context.sqlite3")
    db.execute("INSERT INTO users VALUES ('student','student','Student','unused',?)", (now(),))
    db.execute("INSERT INTO courses VALUES ('course','student','课程','','C','#557766','layers','助教',0,?)", (now(),))
    return db


def add_book(db, document, outline=(), source_type="uploaded_pdf", user_id="student", status="ready"):
    quality = {"outline_entries": len(outline), "pdf_outline": [{"page": page, "path": path} for page, path in outline]} if outline else {}
    db.execute(
        "INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at,page_count,quality) "
        "VALUES (?,'course',?,?,'unused.pdf','/unused',?,?,'test',?,60,?)",
        (document, user_id, document, status, source_type, now(), dumps(quality)),
    )


def chunk(chunk_id, page, text, section="第1章 执行流程 / 1.1 有限步骤", document="book", **extra):
    return {"id": chunk_id, "document_id": document, "course_id": "course", "user_id": "student",
            "page": page, "section": section, "text": text, "title": document,
            "source_type": "uploaded_pdf", **extra}


def prepared(db, rows, seeds):
    return prepare_quiz_evidence(db, "course", "student", rows, seeds)


def ids(rows):
    return [row["id"] for row in rows]


def test_cross_page_function_adds_only_immediate_original_context(textbook):
    add_book(textbook, "book", [(1, "第1章 执行流程"), (2, "第1章 执行流程 / 1.1 有限步骤")])
    rows = [
        chunk("distant", 2, "远处说明，不应扩张到这里。"),
        chunk("function-start", 8, "def stop_at(n):\n    if n <= 0:\n        return\n    stop_at(n - 1)"),
        chunk("function-end", 9, "stop_at(7)\n当参数为0时停止再次调用。"),
        chunk("local-after", 10, "随后讨论该有限调用的行为。"),
        chunk("nonrecursive", 11, "邻居的邻居不应自动进入本次证据。"),
    ]
    originals = deepcopy(rows)
    result = prepared(textbook, rows, [{"id": "function-end", "text": "伪造检索副本", "page": 40}])
    assert ids(result) == ["function-end", "function-start", "local-after"]
    assert result[0] is rows[2] and result[1] is rows[1]
    assert rows == originals and "def stop_at" in result[1]["text"]


def test_same_page_previous_and_next_chunk_are_retained(textbook):
    add_book(textbook, "book", [(1, "第1章 执行流程"), (2, "第1章 执行流程 / 1.1 有限步骤")])
    rows = [chunk("a", 4, "前段"), chunk("b", 4, "命中段"), chunk("c", 4, "后段"), chunk("d", 4, "更后段")]
    assert ids(prepared(textbook, rows, ["b"])) == ["b", "a", "c"]


def test_frontmatter_toc_and_publisher_end_are_not_quiz_evidence(textbook):
    add_book(textbook, "book", [(1, "版权页"), (2, "目录"), (4, "第1章 执行流程"),
                                (5, "第1章 执行流程 / 1.1 有限步骤"), (50, "阅读结束")])
    rows = [chunk("copyright", 1, "出版信息", "版权页"),
            chunk("toc", 2, "第一节......4\n第二节......6\n第三节......8", "目录"),
            chunk("body", 6, "可学习的正文内容。"),
            chunk("end", 50, "读者服务说明", "阅读结束")]
    assert ids(prepared(textbook, rows, rows)) == ["body"]


def test_toc_heading_that_also_exists_in_body_is_still_excluded_on_toc_page(textbook):
    add_book(textbook, "book", [(4, "第1章 执行流程"), (5, "第1章 执行流程 / 1.1 有限步骤")])
    rows = [chunk("toc-title", 2, "第1章 执行流程", "第1章 执行流程"),
            chunk("toc-lines", 2, "一......4\n二......5\n三......7", "目录"),
            chunk("body", 6, "这是实际的完整正文内容。")]
    assert ids(prepared(textbook, rows, ["toc-title", "body"])) == ["body"]


def test_mixed_toc_page_genuine_body_is_preserved(textbook):
    add_book(textbook, "book", [(2, "第1章 执行流程"), (2, "第1章 执行流程 / 1.1 有限步骤")])
    text = "本节介绍有限执行过程的初始状态、前置条件与结束条件，并逐项说明为什么每次操作都必须满足明确的输入约束。"
    rows = [chunk("toc", 2, "一......4\n二......6\n三......8", "目录"), chunk("body", 2, text)]
    assert ids(prepared(textbook, rows, ["toc", "body"])) == ["body"]


def test_no_body_returns_empty_without_promoting_cached_outline(textbook):
    add_book(textbook, "book", [(1, "版权页"), (5, "第1章 执行流程"), (50, "阅读结束")])
    rows = [chunk("copyright", 1, "出版说明", "版权页"), chunk("end", 50, "推广说明", "阅读结束")]
    assert prepared(textbook, rows, rows) == []


def test_unstructured_material_keeps_authoritative_seeds_without_neighbor_guessing(textbook):
    add_book(textbook, "book")
    rows = [chunk("a", 1, "原始前文。", "正文"), chunk("b", 2, "完整命中原文。", "正文"), chunk("c", 3, "原始后文。", "正文")]
    result = prepared(textbook, rows, [{"chunk_id": "b", "text": "检索副本"}])
    assert result == [rows[1]] and result[0] is rows[1]


def test_non_pdf_logical_segments_are_not_physical_neighbor_context(textbook):
    add_book(textbook, "book", source_type="uploaded_txt")
    rows = [chunk("a", 1, "逻辑段一"), chunk("b", 2, "逻辑段二"), chunk("c", 3, "逻辑段三")]
    assert ids(prepared(textbook, rows, ["b"])) == ["b"]


def test_neighbors_never_cross_chapter_or_document(textbook):
    add_book(textbook, "book", [(1, "第1章 执行流程"), (10, "第2章 数据保存")])
    add_book(textbook, "second", [(1, "第1章 执行流程")])
    rows = [chunk("same-book-before", 8, "前文", "第1章 执行流程"),
            chunk("chapter-end", 9, "章末正文", "第1章 执行流程"),
            chunk("next-chapter", 10, "另一章正文", "第2章 数据保存"),
            chunk("other-book", 9, "另一本教材正文", "第1章 执行流程", "second")]
    assert ids(prepared(textbook, rows, ["chapter-end"])) == ["chapter-end", "same-book-before"]


def test_page_gap_larger_than_one_is_not_filled(textbook):
    add_book(textbook, "book", [(1, "第1章 执行流程")])
    rows = [chunk("old", 3, "远处前文", "第1章 执行流程"), chunk("hit", 8, "命中", "第1章 执行流程"),
            chunk("far", 11, "远处后文", "第1章 执行流程")]
    assert ids(prepared(textbook, rows, ["hit"])) == ["hit"]


def test_owner_and_ready_document_state_are_authoritative(textbook):
    textbook.execute("INSERT INTO users VALUES ('other','other','Other','unused',?)", (now(),))
    add_book(textbook, "book", [(1, "第1章 执行流程")])
    add_book(textbook, "foreign", [(1, "第1章 执行流程")], user_id="other")
    add_book(textbook, "pending", [(1, "第1章 执行流程")], status="processing")
    rows = [chunk("good", 2, "本人正文", "第1章 执行流程"),
            chunk("foreign", 2, "他人正文", "第1章 执行流程", "foreign"),
            chunk("pending", 2, "未完成正文", "第1章 执行流程", "pending"),
            chunk("wrong-course", 2, "错误课程字段", "第1章 执行流程", course_id="outside")]
    assert ids(prepared(textbook, rows, rows)) == ["good"]


def test_seed_deduplication_and_unknown_ids_do_not_fabricate_sources(textbook):
    add_book(textbook, "book")
    rows = [chunk("original", 1, "完整正文", "正文")]
    result = prepared(textbook, rows, ["unknown", {"id": ["original"]}, {"id": "original"}, {"id": "original", "text": "伪造"}])
    assert result == rows and result[0] is rows[0]


def test_all_ranked_seeds_get_budget_before_neighbors(textbook):
    add_book(textbook, "book", [(1, "第1章 执行流程")])
    rows = [chunk(str(i), i + 1, "原文" * 100, "第1章 执行流程") for i in range(25)]
    seeds = [rows[i] for i in range(1, 25, 2)]
    result = prepared(textbook, rows, seeds)
    assert ids(result)[:len(seeds)] == ids(seeds)
    assert len(result) == MAX_QUIZ_EVIDENCE_CHUNKS
    assert set(ids(result)) <= set(ids(rows))


def test_character_budget_keeps_complete_blocks_and_later_small_seed(textbook):
    add_book(textbook, "book")
    rows = [chunk("too-long", 1, "长" * (MAX_QUIZ_EVIDENCE_CHARACTERS + 1), "正文"),
            chunk("large", 2, "甲" * 10_000, "正文"), chunk("does-not-fit", 3, "乙" * 6_001, "正文"),
            chunk("fits", 4, "丙" * 6_000, "正文")]
    original = deepcopy(rows)
    result = prepared(textbook, rows, rows)
    assert ids(result) == ["large", "fits"]
    assert sum(len(row["text"]) for row in result) == MAX_QUIZ_EVIDENCE_CHARACTERS
    assert rows == original and result[1]["text"] == rows[3]["text"]


def test_overlarge_neighbor_is_skipped_without_truncating_or_using_its_neighbor(textbook):
    add_book(textbook, "book", [(1, "第1章 执行流程")])
    rows = [chunk("hit", 2, "命中正文", "第1章 执行流程"),
            chunk("long-neighbor", 3, "长" * MAX_QUIZ_EVIDENCE_CHARACTERS, "第1章 执行流程"),
            chunk("beyond", 3, "不应递归或跨过紧邻块补入", "第1章 执行流程")]
    assert ids(prepared(textbook, rows, ["hit"])) == ["hit"]


def test_duplicate_authoritative_identity_is_not_guessed(textbook):
    add_book(textbook, "book")
    rows = [chunk("same", 1, "第一份", "正文"), chunk("same", 2, "另一份", "正文")]
    assert prepared(textbook, rows, ["same"]) == []
