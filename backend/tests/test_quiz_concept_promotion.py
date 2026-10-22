"""Original fixtures for narrow concept promotion; no provider or real account."""
from copy import deepcopy

import pytest

from backend.app.db import Database, dumps, now
from backend.app.knowledge_map import derive_textbook_tree, extracted_graph
from backend.app.practice import ConceptScopeError, bind_question_concepts, source_concept_scopes
from backend.app.quiz_concept_promotion import promote_question_concepts


@pytest.fixture
def textbook(tmp_path):
    db = Database(tmp_path / "promotion.sqlite3")
    db.execute("INSERT INTO users VALUES ('student','student','Student','unused',?)", (now(),))
    db.execute("INSERT INTO courses VALUES ('course','student','课程','','C','#557766','layers','助教',0,?)", (now(),))
    return db


def add_book(db, document, paths, rows):
    quality = {"pdf_outline": [{"page": page, "path": path} for page, path in paths]}
    db.execute(
        "INSERT INTO documents (id,course_id,user_id,title,filename,path,status,source_type,license,created_at,page_count,quality) "
        "VALUES (?,'course','student',?,'unused.pdf','/unused','ready','uploaded_pdf','test',?,40,?)",
        (document, document, now(), dumps(quality)),
    )
    for chunk_id, page, section in rows:
        db.execute("INSERT INTO chunks VALUES (?,?,'course','student',?,?,?,NULL)",
                   (chunk_id, document, page, section, "原创来源说明一次明确的处理操作及其前提。"))
    documents = db.all("SELECT * FROM documents WHERE course_id='course'")
    chunks = db.all("SELECT * FROM chunks WHERE course_id='course'")
    nodes, _ = extracted_graph(derive_textbook_tree(documents, chunks))
    db.execute("DELETE FROM concepts WHERE course_id='course'")
    for node in nodes:
        db.execute("INSERT INTO concepts VALUES (?,'course',?,?,?)",
                   (node["id"], node["label"], node["chapter"], node["source"]))


def add_original_outline(db):
    add_book(db, "book", [
        (1, "第1章 任务处理"),
        (2, "第1章 任务处理 / 1.1 顺序步骤"),
        (3, "第1章 任务处理 / 1.1 顺序步骤 / 1.1.1 入口条件"),
        (6, "第1章 任务处理 / 1.1 顺序步骤 / 1.1.2 结束条件"),
        (10, "第1章 任务处理 / 1.2 分支步骤"),
        (20, "第2章 数据整理"),
        (21, "第2章 数据整理 / 2.1 排列顺序"),
    ], [
        ("entry", 4, "第1章 任务处理 / 1.1 顺序步骤 / 1.1.1 入口条件"),
        ("finish", 7, "第1章 任务处理 / 1.1 顺序步骤 / 1.1.2 结束条件"),
        ("boundary", 6, "第1章 任务处理 / 1.1 顺序步骤 / 1.1.2 结束条件"),
        ("branch", 11, "第1章 任务处理 / 1.2 分支步骤"),
        ("sort", 22, "第2章 数据整理 / 2.1 排列顺序"),
    ])


def question(label, sources):
    return {"id": "question", "concept": label, "source_ids": sources,
            "stem": "原创测试题", "answer": "A", "explanation": "不应改动此解析。"}


def run_promotion(db, q, overrides=None):
    all_chunks = db.all("SELECT * FROM chunks WHERE course_id='course'")
    selected = [chunk for chunk in all_chunks if chunk["id"] in q["source_ids"]]
    scopes, _ = source_concept_scopes(db, "course", "student", all_chunks, selected)
    if overrides:
        scopes.update(overrides)
    notices = promote_question_concepts(db, "course", "student", all_chunks, selected, [q], scopes)
    return notices, scopes


def test_nearest_real_common_section_is_selected_without_changing_question(textbook):
    add_original_outline(textbook)
    q = question("入口条件", ["entry", "finish"])
    original = deepcopy(q)
    notices, scopes = run_promotion(textbook, q)
    assert q["concept"] == "顺序步骤"
    assert q["concept_original"] == "入口条件" and q["concept_scope_adjusted"] is True
    assert "不表示已单独测量" in notices[q["id"]]
    assert {key: q[key] for key in original if key != "concept"} == {key: value for key, value in original.items() if key != "concept"}
    bind_question_concepts([q], scopes)
    assert q["concept_measured"] is True


def test_more_distant_chapter_is_used_only_when_it_is_nearest_shared_ancestor(textbook):
    add_original_outline(textbook)
    q = question("入口条件", ["entry", "branch"])
    notices, scopes = run_promotion(textbook, q)
    assert q["concept"] == "任务处理" and notices
    bind_question_concepts([q], scopes)


def test_physical_boundary_cannot_gain_leaf_measurement(textbook):
    add_original_outline(textbook)
    q = question("结束条件", ["finish", "boundary"])
    notices, scopes = run_promotion(textbook, q)
    assert "结束条件" not in scopes["boundary"]
    assert q["concept"] == "顺序步骤" and notices
    bind_question_concepts([q], scopes)


@pytest.mark.parametrize("label,sources", [
    ("入口条件", ["entry", "sort"]),
    ("未知叶子", ["entry", "finish"]),
    ("分支步骤", ["entry", "finish"]),
    ("排列顺序", ["entry", "branch"]),
])
def test_cross_chapter_unknown_and_unselected_labels_remain_strictly_rejected(textbook, label, sources):
    add_original_outline(textbook)
    q = question(label, sources)
    original = deepcopy(q)
    notices, scopes = run_promotion(textbook, q)
    assert not notices and q == original
    with pytest.raises(ConceptScopeError):
        bind_question_concepts([q], scopes)


def test_already_common_label_remains_unchanged(textbook):
    add_original_outline(textbook)
    q = question("顺序步骤", ["entry", "finish"])
    original = deepcopy(q)
    notices, scopes = run_promotion(textbook, q)
    assert not notices and q == original
    bind_question_concepts([q], scopes)


def test_empty_safe_scope_is_never_promoted_from_other_source(textbook):
    add_original_outline(textbook)
    q = question("入口条件", ["entry", "finish"])
    original = deepcopy(q)
    notices, scopes = run_promotion(textbook, q, {"finish": []})
    assert not notices and q == original
    with pytest.raises(ConceptScopeError):
        bind_question_concepts([q], scopes)


def test_label_present_only_in_tree_cannot_be_inferred_as_safe(textbook):
    add_original_outline(textbook)
    q = question("结束条件", ["boundary", "entry"])
    original = deepcopy(q)
    notices, scopes = run_promotion(textbook, q)
    assert "结束条件" not in set().union(*(set(labels) for labels in scopes.values()))
    assert not notices and q == original
    with pytest.raises(ConceptScopeError):
        bind_question_concepts([q], scopes)


@pytest.mark.parametrize("repeated", ["leaf", "parent"])
def test_duplicate_tree_labels_across_documents_do_not_promote(textbook, repeated):
    add_original_outline(textbook)
    path = "第3章 其他知识 / 3.1 入口条件" if repeated == "leaf" else "第3章 顺序步骤 / 3.1 其他内容"
    root = path.split(" / ")[0]
    add_book(textbook, "second", [(1, root), (2, path)], [("other", 3, path)])
    q = question("入口条件", ["entry", "finish"])
    original = deepcopy(q)
    notices, _ = run_promotion(textbook, q)
    # Neither an ambiguous original label nor an ambiguous nearest real
    # common parent authorizes a silent promotion to a broader chapter.
    assert not notices and q == original


def test_catalog_id_must_bind_original_label_to_actual_node(textbook):
    add_original_outline(textbook)
    textbook.execute("DELETE FROM concepts WHERE course_id='course' AND label='入口条件'")
    q = question("入口条件", ["entry", "finish"])
    original = deepcopy(q)
    notices, _ = run_promotion(textbook, q, {"entry": ["入口条件", "顺序步骤", "任务处理"]})
    assert not notices and q == original


def test_invalid_safe_label_and_missing_selected_evidence_do_not_authorize_promotion(textbook):
    add_original_outline(textbook)
    q = question("入口条件", ["entry", "finish"])
    original = deepcopy(q)
    notices, _ = run_promotion(textbook, q, {"finish": ["排列顺序", "任务处理"]})
    assert not notices and q == original
    chunks = textbook.all("SELECT * FROM chunks WHERE course_id='course'")
    selected = [chunk for chunk in chunks if chunk["id"] == "entry"]
    scopes = {"entry": ["入口条件", "顺序步骤", "任务处理"], "finish": ["结束条件", "顺序步骤", "任务处理"]}
    assert not promote_question_concepts(textbook, "course", "student", chunks, selected, [q], scopes)
    assert q == original


def test_duplicate_question_identity_does_not_partially_mutate_batch(textbook):
    add_original_outline(textbook)
    chunks = textbook.all("SELECT * FROM chunks WHERE course_id='course'")
    selected = [chunk for chunk in chunks if chunk["id"] in {"entry", "finish"}]
    scopes, _ = source_concept_scopes(textbook, "course", "student", chunks, selected)
    qs = [question("入口条件", ["entry", "finish"]), question("结束条件", ["entry", "finish"])]
    original = deepcopy(qs)
    assert not promote_question_concepts(textbook, "course", "student", chunks, selected, qs, scopes)
    assert qs == original
