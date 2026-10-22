"""Real PDF outline patterns: page furniture, old anchors and source ownership."""
from __future__ import annotations

import pytest

from backend.app.db import dumps, now
from backend.app.figures import add_manual_figure
from backend.app.ingestion import chunk_pages, extract_pages
from backend.app.knowledge_map import build_knowledge_map, build_mindmap_evidence, heading_id
from backend.tests.test_graph_structure import assert_forest, chunk, document, graph, store


CHAPTER = "第1章 数据结构为何重要"
ARRAY = CHAPTER + " / 1.1 基础数据结构:数组"
READ = ARRAY + " / 1.1.1 读取"
INSERT = ARRAY + " / 1.1.3 插入"
SECOND = "第2章 算法为何重要"
ORDERED = SECOND + " / 2.1 有序数组"
# A small structural sample from the supplied textbook's genuine bookmarks.
# The full local 174-page PDF is inspected separately; no textbook prose ships.
CACHE = [
    {"page": 3, "path": "数字版权声明"},
    {"page": 6, "path": "版权声明"},
    {"page": 14, "path": CHAPTER},
    {"page": 14, "path": ARRAY},
    {"page": 16, "path": READ},
    {"page": 20, "path": INSERT},
    {"page": 26, "path": SECOND},
    {"page": 26, "path": ORDERED},
    {"page": 174, "path": "看完了"},
]


def cached_document(db, doc_id="a", *, source="uploaded_pdf", entries=None):
    document(db, doc_id, source=source)
    cache = CACHE if entries is None else entries
    db.execute("UPDATE documents SET quality=? WHERE id=?", (
        dumps({"chapter_source": "PDF outline", "outline_entries": len(cache), "pdf_outline": cache}), doc_id))


def legacy_chunks(db, doc_id="a"):
    for page, section in ((3, "数字版权声明"), (6, "版权声明"),
                          (14, ARRAY + " 1"), (14, ARRAY), (14, "第1章"),
                          (16, ARRAY + " 3"), (16, CHAPTER + " / 1.1.1 读取"),
                          (17, READ), (20, CHAPTER + " / 1.1.3 插入"),
                          (26, ORDERED + " 13"), (26, ORDERED), (26, "第2章"), (174, "看完了")):
        chunk(db, doc_id, section, page)


def test_cached_real_outline_merges_chapter_furniture_and_running_headers_read_only(store, monkeypatch):
    cached_document(store)
    legacy_chunks(store)
    store.execute("UPDATE chunks SET embedding='[0.12,0.3]' WHERE document_id='a'")
    before = store.all("SELECT * FROM chunks")
    # A graph GET must read the small metadata cache, never reopen the PDF.
    monkeypatch.setattr("backend.app.ingestion.PdfReader", lambda *args: pytest.fail("read model scanned a PDF"))
    with store.connection() as conn:
        conn.execute("PRAGMA query_only=ON")
        result = build_knowledge_map(conn, store.one("SELECT * FROM courses WHERE id='private'"), "alice")
    assert_forest(result["tree"])
    nodes = {node["id"]: node for node in result["tree"]["nodes"]}
    assert [node["label"] for node in nodes.values() if node["kind"] == "chapter"] == [CHAPTER, SECOND]
    assert result["metadata"]["total_headings"] == 6
    assert nodes[heading_id("a", ARRAY)]["page_end"] == 20
    assert nodes[heading_id("a", READ)]["parent_id"] == heading_id("a", ARRAY)
    assert nodes[heading_id("a", READ)]["depth"] == 3
    assert result["tree"]["node_aliases"][heading_id("a", "第1章")] == heading_id("a", CHAPTER)
    assert result["tree"]["node_aliases"][heading_id("a", ARRAY + " 3")] == heading_id("a", ARRAY)
    assert store.all("SELECT * FROM chunks") == before


def test_existing_flat_real_leaf_id_is_retained_when_no_nested_old_anchor_exists(store):
    cached_document(store)
    legacy_chunks(store)
    old_path = CHAPTER + " / 1.1.3 插入"
    result = graph(store)
    leaf = next(node for node in result["tree"]["nodes"] if node["label"] == "1.1.3 插入")
    assert leaf["id"] == heading_id("a", old_path)
    assert leaf["parent_id"] == heading_id("a", ARRAY)
    assert leaf["heading"] == INSERT
    assert result["tree"]["node_aliases"][heading_id("a", INSERT)] == leaf["id"]


def test_manual_figures_and_old_alias_annotations_keep_their_original_rows_and_owner(store):
    cached_document(store)
    legacy_chunks(store)
    old_id = heading_id("a", ARRAY + " 3")
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'我的数组','数组复习笔记',?)", (old_id, now()))
    store.execute("INSERT INTO mindmap_annotations VALUES ('bob','private',?,'别人的名称','BOB_PRIVATE_NOTE',?)", (old_id, now()))
    chunk(store, "a", ARRAY + " 2", 15)
    with store.connection() as conn:
        figure = add_manual_figure(conn, store.one("SELECT * FROM documents WHERE id='a'"),
                                   "alice", 15, "", "数组读取图")
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'我的图','我的图示笔记',?)", (figure["figure_id"], now()))
    before = store.all("SELECT * FROM mindmap_annotations ORDER BY user_id,node_id")
    result = graph(store)
    nodes = {node["id"]: node for node in result["tree"]["nodes"]}
    assert nodes[figure["figure_id"]]["parent_id"] == heading_id("a", ARRAY)
    assert nodes[figure["figure_id"]]["study_note"] == "我的图示笔记"
    assert nodes[heading_id("a", ARRAY)]["study_note"] == "数组复习笔记"
    assert nodes[heading_id("a", ARRAY)]["annotation_source_node_id"] == old_id
    assert "BOB_PRIVATE_NOTE" not in dumps(result)
    assert store.all("SELECT * FROM mindmap_annotations ORDER BY user_id,node_id") == before


def test_current_real_anchor_annotation_takes_precedence_over_an_old_alias_without_deleting_either(store):
    cached_document(store)
    legacy_chunks(store)
    for path, note in ((ARRAY, "当前节点笔记"), (ARRAY + " 3", "旧标题笔记")):
        store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'',?,?)", (heading_id("a", path), note, now()))
    result = graph(store)
    node = next(node for node in result["tree"]["nodes"] if node["id"] == heading_id("a", ARRAY))
    assert node["study_note"] == "当前节点笔记"
    assert store.one("SELECT COUNT(*) AS n FROM mindmap_annotations")["n"] == 2


def test_old_formatted_mastery_remains_visible_after_concept_cache_refresh_without_regrading(store):
    from backend.app.ingestion import rebuild_extracted_graph

    cached_document(store)
    legacy_chunks(store)
    store.execute("INSERT INTO mastery VALUES ('alice','private','基础数据结构:数组 3',2,0)")
    store.execute("INSERT INTO mastery VALUES ('alice','private','基础数据结构:数组',2,1)")
    store.execute("INSERT INTO mastery VALUES ('bob','private','基础数据结构:数组 3',1,1)")
    before = store.all("SELECT * FROM mastery ORDER BY user_id,concept")
    with store.connection() as conn:
        rebuild_extracted_graph(conn, "private")
    assert {row["label"] for row in store.all("SELECT label FROM concepts")} == {
        "数据结构为何重要", "基础数据结构:数组", "读取", "插入", "算法为何重要", "有序数组"}
    result = graph(store)
    node = next(node for node in result["tree"]["nodes"] if node["id"] == heading_id("a", ARRAY))
    # Two disjoint label-keyed practice aggregates map to the same real heading.
    assert node["mastery"] == .25
    assert next(node for node in result["nodes"] if node["id"] == heading_id("a", ARRAY))["mastery"] == .25
    assert store.all("SELECT * FROM mastery ORDER BY user_id,concept") == before


def test_ambiguous_old_format_labels_across_documents_never_share_mastery(store):
    for doc_id in ("a", "b"):
        cached_document(store, doc_id)
        legacy_chunks(store, doc_id)
    store.execute("INSERT INTO mastery VALUES ('alice','private','基础数据结构:数组 3',2,2)")
    result = graph(store)
    array_nodes = [node for node in result["tree"]["nodes"] if node["label"] == "1.1 基础数据结构:数组"]
    assert len(array_nodes) == 2 and all(node["mastery"] is None for node in array_nodes)


def test_plain_same_name_mastery_stays_ambiguous_even_when_one_bookmark_lacked_a_chunk(store):
    first_summary, second_summary = CHAPTER + " / 1.3 总结", SECOND + " / 2.5 总结"
    cached_document(store, entries=[*CACHE, {"page": 25, "path": first_summary}, {"page": 33, "path": second_summary}])
    legacy_chunks(store)
    chunk(store, "a", first_summary, 25)
    chunk(store, "a", first_summary + " 12", 25)
    store.execute("INSERT INTO mastery VALUES ('alice','private','总结',2,2)")
    result = graph(store)
    summaries = [node for node in result["tree"]["nodes"] if node["label"] in {"1.3 总结", "2.5 总结"}]
    assert len(summaries) == 2 and all(node["mastery"] is None for node in summaries)
    # The old, distinct running-header label does identify a single source path.
    store.execute("INSERT INTO mastery VALUES ('alice','private','总结 12',2,1)")
    result = graph(store)
    nodes = {node["id"]: node for node in result["tree"]["nodes"]}
    assert nodes[heading_id("a", first_summary)]["mastery"] == .5
    assert nodes[heading_id("a", second_summary)]["mastery"] is None


def test_merged_node_evidence_resolves_original_chunk_ids_and_old_requests(store):
    cached_document(store)
    legacy_chunks(store)
    old_path = CHAPTER + " / 1.1.3 插入"
    original = "插入元素需要先移动后续元素，再把新元素写入相应位置。"
    store.execute("UPDATE chunks SET text=? WHERE section=?", ("1.1.3 插入\n" + original, old_path))
    with store.connection() as conn:
        conn.execute("PRAGMA query_only=ON")
        course = store.one("SELECT * FROM courses WHERE id='private'")
        restored = build_mindmap_evidence(conn, course, "alice", heading_id("a", INSERT))
        chapter = build_mindmap_evidence(conn, course, "alice", heading_id("a", "第1章"))
    assert restored["heading"] == INSERT
    assert any(item["excerpt"] == original for item in restored["evidence"])
    assert chapter["heading"] == CHAPTER
    assert any(original in item["excerpt"] for item in chapter["evidence"])


@pytest.mark.parametrize("source,outline_entries", [("uploaded_txt", 9), ("uploaded_docx", 9), ("uploaded_pdf", 0)])
def test_cache_does_not_reinterpret_non_pdf_or_body_extracted_paths(store, source, outline_entries):
    cached_document(store, source=source)
    store.execute("UPDATE documents SET quality=? WHERE id='a'", (dumps({
        "chapter_source": "正文标题提取（待核验）" if not outline_entries else "PDF outline",
        "outline_entries": outline_entries, "pdf_outline": CACHE}),))
    chunk(store, "a", ARRAY, 14)
    chunk(store, "a", "第1章", 14)
    nodes = {node["id"] for node in graph(store)["tree"]["nodes"]}
    assert heading_id("a", "第1章") in nodes
    assert heading_id("a", CHAPTER) in nodes


def test_unmatched_numbered_titles_are_retained_and_chapters_do_not_merge_across_books(store):
    for doc_id in ("a", "b"):
        cached_document(store, doc_id)
        legacy_chunks(store, doc_id)
        chunk(store, doc_id, CHAPTER + " / 1.1 不同知识标题", 16)
    result = graph(store)
    assert_forest(result["tree"])
    ids = {node["id"] for node in result["tree"]["nodes"]}
    assert all(heading_id(doc_id, CHAPTER + " / 1.1 不同知识标题") in ids for doc_id in ("a", "b"))
    assert all(heading_id(doc_id, CHAPTER) in ids for doc_id in ("a", "b"))


def test_explicit_unnumbered_outline_paths_and_numeric_titles_keep_their_literal_hierarchy(store):
    paths = ["课程", "课程 / Definition", "课程 / 1.1 IPv4", "课程 / 1.1 IPv4 2"]
    cached_document(store, entries=[{"page": i + 1, "path": path} for i, path in enumerate(paths)])
    for i, path in enumerate(paths, 1):
        chunk(store, "a", path, i)
    result = graph(store)
    assert_forest(result["tree"])
    assert {heading_id("a", path) for path in paths} <= {node["id"] for node in result["tree"]["nodes"]}
    assert any(node["label"] == "1.1 IPv4 2" for node in result["tree"]["nodes"])


def test_publisher_closing_pages_require_genuine_outline_and_position_but_appendices_survive(store):
    cached_document(store)
    for page, path in ((5, "看完了"), (14, CHAPTER), (26, SECOND),
                       (174, "阅读结束"), (175, "附录"), (176, "索引"), (177, "结论")):
        chunk(store, "a", path, page)
    nodes = {node["label"] for node in graph(store)["tree"]["nodes"]}
    assert "看完了" in nodes and "阅读结束" not in nodes
    assert {"附录", "索引", "结论"} <= nodes


@pytest.mark.parametrize("cache", [None, "broken", [{"page": True, "path": CHAPTER}],
                                 [{"page": 500, "path": CHAPTER}], [{"page": 14, "path": None}]])
def test_malformed_outline_cache_keeps_legacy_navigation_available(store, cache):
    document(store, "a")
    store.execute("UPDATE documents SET quality=? WHERE id='a'", (dumps({
        "chapter_source": "PDF outline", "outline_entries": 2, "pdf_outline": cache}),))
    chunk(store, "a", ARRAY, 14)
    assert heading_id("a", ARRAY) in {node["id"] for node in graph(store)["tree"]["nodes"]}


def test_new_pdf_ingestion_retains_real_nested_outline_and_ignores_page_furniture(monkeypatch, tmp_path):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        is_encrypted = False
        pages = [Page("1.1 数组 1\n1.1 数组\n数组按连续位置保存元素。\n第1章"),
                 Page("1.1 数组 2\n1.1.1 读取\n读取元素可用已知索引。"),
                 Page("2.1 有序数组 3\n2.1 有序数组\n有序数组中的元素按顺序排列。\n第2章")]
        outline = [{"/Title": "第1章 数据结构", "/Page": 0},
                   [{"/Title": "1.1 数组", "/Page": 0}, [{"/Title": "1.1.1 读取", "/Page": 1}]],
                   {"/Title": "第2章 算法", "/Page": 2}, [{"/Title": "2.1 有序数组", "/Page": 2}]]

        def get_destination_page_number(self, item):
            return item["/Page"]

    monkeypatch.setattr("backend.app.ingestion.PdfReader", lambda *args: Reader())
    pages = extract_pages(tmp_path / "real-outline-pattern.pdf")
    chunks = chunk_pages(pages)
    paths = {entry["path"] for entry in pages.quality["pdf_outline"]}
    assert {chunk["section"] for chunk in chunks} <= paths
    assert any(chunk["section"] == "第1章 数据结构 / 1.1 数组 / 1.1.1 读取" for chunk in chunks)
    assert "第1章" not in {chunk["section"] for chunk in chunks}
    assert pages.quality["outline_entries"] == 5


def test_coarse_pdf_bookmarks_do_not_erase_an_actual_unlisted_body_section(store):
    from backend.app.ingestion import ExtractedPages

    pages = ExtractedPages([(1, "1.1 数组\n数组按连续位置保存元素。")],
        sections={1: "第1章 数据结构"}, quality={"page_kind": "physical", "chapter_source": "PDF outline",
            "outline_entries": 1, "pdf_outline": [{"page": 1, "path": "第1章 数据结构"}]})
    chunks = chunk_pages(pages)
    assert chunks[0]["section"] == "第1章 数据结构 / 1.1 数组"
    cached_document(store, entries=pages.quality["pdf_outline"])
    chunk(store, "a", chunks[0]["section"], 3)
    nodes = {node["id"]: node for node in graph(store)["tree"]["nodes"]}
    assert nodes[heading_id("a", "第1章 数据结构")]["page_start"] == 1
    assert nodes[heading_id("a", "第1章 数据结构 / 1.1 数组")]["label"] == "1.1 数组"
