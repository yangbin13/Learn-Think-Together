"""Real caption anchors, unchanged headings/vectors, and material permissions."""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.config import Settings
from backend.app.db import dumps, now
from backend.app.figures import (FIGURE_INDEX_VERSION, add_manual_figure, extract_figure_captions, figure_id,
                                 queue_figure_index, queue_missing_figure_indexes, store_figure_index)
from backend.app.ingestion import IngestionWorker, extract_pages
from backend.app.knowledge_map import build_mindmap_evidence, heading_id
from backend.tests.test_api import setup, register, wait_document
from backend.tests.test_graph_structure import store, document, chunk, graph, assert_forest


def caption_pdf():
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    text = DecodedStreamObject()
    text.set_data(b"BT /F1 12 Tf 20 700 Td (Source transport chapter prose.) Tj 0 -20 Td "
                  b"(Figure 3-52 TCP congestion window) Tj 0 -20 Td "
                  b"(Figure 3-53 Congestion avoidance sawtooth) Tj 0 -20 Td "
                  b"(Figure 3-52 shows Reno and Tahoe in the preceding diagram.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(text)
    parent = writer.add_outline_item("Chapter 3 Transport", 0)
    writer.add_outline_item("3.7 TCP", 0, parent=parent)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def save_figures(db, doc_id, raw, page=10, section="第一章 进程 / 1.1 线程"):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    with db.connection() as conn:
        return store_figure_index(conn, doc, extract_figure_captions(raw, page, section), doc["page_count"])


def test_actual_ocr_caption_lines_exclude_body_references_and_preserve_uncorrected_words():
    raw = "\n".join([
        "图 l-23 因特网协议栈和 OS I 参考模型",
        "图 2 - 8 一 个 HTTP 请求报文的通用格式",
        "图 3-39 TCP 兰 次握手 ：报文段交换",
        "图 3-40 关闭 一 条 TCP 连接",
        "图 5-4 对于节点 u 的最低开销 路径 和转发表",
        "图 3 - 52 图示了 Reno 版 TCP 与 Tahoe 版 TCP 的拥塞控制窗口 的 演 化 情 况 。 在该图 中，",
        "TCP Tahoe 下拥塞窗口被设置为 1 个 图 3 会 52 TCP 拥塞窟口的演化 ( Ta h oe 和 R e no)",
        "图 1-19 和 图 1 -20 中的例子说明端到端吞吐量。",
        "如图1-20所示，图内条件仍需核验。", "1-20 端到端", "装饰 logo",
        "图1-21给出一个示例。", "图1-22，后文继续讨论该例。",
    ])
    result = extract_figure_captions(raw, 52)
    assert [item["figure_number"] for item in result] == ["1-23", "2-8", "3-39", "3-40", "5-4"]
    assert result[0]["raw_figure_number"] == "l-23"
    assert "兰 次握手" in result[2]["caption"]
    assert all(item["physical_page"] == 52 for item in result)


def test_captions_handle_adjacent_cjk_and_exact_numbers_but_omit_toc_and_ambiguity():
    assert extract_figure_captions("图1-2协议栈", 1)[0]["figure_number"] == "1-2"
    raw = "Figure 3.52 First diagram\nFigure 3.53 Second diagram\nFigure 3.52 First diagram"
    assert len(extract_figure_captions(raw, 1)) == 2
    assert extract_figure_captions("Figure 3.52 First diagram\nFigure 3.52 Conflicting diagram", 1) == []
    assert extract_figure_captions("图1-2 协议栈 .......... 19", 1) == []
    assert extract_figure_captions("插图目录\n图1-2 协议栈", 1) == []
    assert extract_figure_captions("Figure 3.520 Other diagram", 1)[0]["figure_number"] == "3.520"
    assert extract_figure_captions("", 1) == []  # An image-only page does not invent captions.


@pytest.mark.parametrize("line", [
    "图 3-52 中列出的 TC P 拥 塞控 制 算法仍能取得所希望的 l OGb ps 速率 ?在本章 的课后习题",
    "图 2-6 中进行了图示 。 当用户请求一个 Web 页面时,浏览器发送请求。",
    "图 3-16 中,时间从图的顶部朝底部移动",
    "图 4-18 中存在的 3 个 IP 子网 。",
    "图 5-11 中 总结了在一台路由器转发表中增加前缀的步骤",
    "图1-1 举例说明我们的讨论 。",
    "图1-19b 此时显示了一个具有N条链路的网络",
    "图1-24 也说明了一个重要概念:封装",
    "图2-18 讲过,在DNS等级结构中服务器授权请求",
    "图3-6 列出了流行的因特网应用",
    "图4-13 描述了有两个优先权类的操作",
    "图5-17 呈现了控制器平台的简化视图",
    "图8-31 概述了鉴别框架",
    "图9-17 提供了边缘路由器分类功能的逻辑视图",
    "图3-53 ) , 同时维护一种长期的发送速率",
    "图7-3 (该图选自 [ Holland 2001 ] )显示几种物理层",
    "图8-32 EAP是一个端到端协议。EAP报文使用另一个链路层机制",
    "图1-1 2 显示了一个简单的网络",
    "图3~45", "图6-l9 由一台路由器互联的两个子网",
])
def test_full_textbook_prose_references_and_damaged_number_boundaries_do_not_become_nodes(line):
    assert extract_figure_captions(line, 200) == []


def test_adjacent_real_captions_stay_separate_and_internal_references_do_not_invent_more_figures():
    result = extract_figure_captions("图 3-34 巾 千确认丢失而重传 图 3-35 报文段 100 没有重传", 179)
    assert [item["figure_number"] for item in result] == ["3-34", "3-35"]
    result = extract_figure_captions("图 7-7 IEEE 802.11 LAN 体系结构 图 7-8 IEEE 802.11 自组织网络", 366)
    assert [item["figure_number"] for item in result] == ["7-7", "7-8"]
    result = extract_figure_captions("图 6-22 图 6-15 中最上面交换机的交换机表的一部分", 331)
    assert [item["figure_number"] for item in result] == ["6-22"]
    assert "图 6-15" in result[0]["caption"]
    result = extract_figure_captions("图4-20 3 台路由器互联6个子网", 237)
    assert result[0]["figure_number"] == "4-20"  # A complete number can have a numeric title.


def test_descriptive_multi_sentence_captions_and_ocr_words_keep_original_source_titles():
    text = "\n".join([
        "图 1-14 对千 FDM, 每条电路连续地得到部分带宽 。 对于 TOM, 钮条电路在",
        "图 5-8 具有 3 个自治系统的网络 。 AS3 包括 一个具有前缀 x 的子网",
        "图 8-5 -个块密码的例子",
    ])
    result = extract_figure_captions(text, 38)
    assert [item["figure_number"] for item in result] == ["1-14", "5-8", "8-5"]
    assert "对千 FDM" in result[0]["caption"] and "AS3 包括" in result[1]["caption"]
    assert result[2]["caption"] == "-个块密码的例子"


def test_new_ingestion_captions_are_collected_before_cjk_continuation_merges(monkeypatch, tmp_path):
    from backend.app import ingestion
    from backend.tests.test_ingestion_quality import FakeReader, FakePage

    class CaptionReader(FakeReader):
        outline = []

        def __init__(self, path):
            self.pages = [FakePage("第一章网络\n1.1 端到端\n原文讨论传输\n图 1-20 端到端吞 吐扭\n图 1-19 和 图1-20 中的例子说明。")]

    monkeypatch.setattr(ingestion, "PdfReader", CaptionReader)
    pages = extract_pages(tmp_path / "book.pdf")
    assert len(pages.figures) == 1 and pages.figures[0]["figure_number"] == "1-20"
    assert pages.figures[0]["section_path"].startswith("第一章网络")


def test_figure_tree_preserves_headings_sample_annotations_and_mastery(store, monkeypatch):
    from backend.app import figures
    document(store, "a")
    for number in range(1, 191):
        chunk(store, "a", f"第{number}章 主题{number}", number)
    path = "第190章 主题190 / 190.1 真实小节"
    chunk(store, "a", path, 190)
    old = graph(store)
    original_ids = {node["id"] for node in old["tree"]["nodes"]}
    selected = heading_id("a", path)
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'旧名称','旧笔记',?)", (selected, now()))
    save_figures(store, "a", "图9-1 相同页第一幅\n图9-2 相同页第二幅", page=190, section=path)
    fig_id = figure_id("a", 190, "9-1")
    store.execute("INSERT INTO mastery VALUES ('alice','private','图 9-1 相同页第一幅',4,4)")
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'我的图题','原图笔记',?)", (fig_id, now()))
    monkeypatch.setattr(figures, "PdfReader", lambda *args: pytest.fail("GET graph must not scan a PDF"))
    with store.connection() as conn:
        conn.execute("PRAGMA query_only=ON")
        from backend.app.knowledge_map import build_knowledge_map
        result = build_knowledge_map(conn, store.one("SELECT * FROM courses WHERE id='private'"), "alice")
    assert result["metadata"]["total_headings"] == old["metadata"]["total_headings"] == 191
    assert len(result["nodes"]) == len(old["nodes"]) == 180
    assert original_ids <= {node["id"] for node in result["tree"]["nodes"]}
    assert result["tree"]["total_figures"] == result["metadata"]["total_figures"] == 2
    assert_forest(result["tree"])
    nodes = {node["id"]: node for node in result["tree"]["nodes"]}
    assert nodes[selected]["study_note"] == "旧笔记"
    assert nodes[fig_id]["parent_id"] == selected and nodes[fig_id]["mastery"] is None
    assert nodes[fig_id]["study_note"] == "原图笔记" and nodes[fig_id]["verified"] is False
    with store.connection() as conn:
        result = build_mindmap_evidence(conn, store.one("SELECT * FROM courses WHERE id='private'"), "alice", fig_id)
    assert result["evidence"] == [] and result["figure"]["caption_page"] == 190
    assert "chunk_id" not in result["figure"] and result["figure"]["image_mode"] == "full_page"


def test_figure_cache_filters_private_uploads_readiness_and_uses_document_fallback(store):
    for name, owner, status in (("public", None, "ready"), ("mine", "alice", "ready"),
                                ("foreign", "bob", "ready"), ("failed", "alice", "failed")):
        document(store, name, course="builtin", owner=owner, status=status)
        save_figures(store, name, "图1-1 图题原文", section="不在现有结构中的小节")
    result = graph(store, "builtin", "alice")
    nodes = [node for node in result["tree"]["nodes"] if node["kind"] == "figure"]
    assert {node["document_id"] for node in nodes} == {"public", "mine"}
    assert all(node["parent_id"] == "document:" + node["document_id"] for node in nodes)
    assert all(node["hierarchy_source"] == "document_only" for node in nodes)


def test_ready_backfill_is_idempotent_and_leaves_chunks_vectors_and_notes_intact(store, tmp_path):
    document(store, "a")
    path = tmp_path / "book.pdf"
    path.write_bytes(caption_pdf())
    store.execute("UPDATE documents SET path=?,page_count=1,embedding_status='hybrid' WHERE id='a'", (str(path),))
    section = "Chapter 3 Transport / 3.7 TCP"
    chunk(store, "a", section, 1)
    store.execute("UPDATE chunks SET embedding='[1,0,0]' WHERE document_id='a'")
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'','旧笔记',?)", (heading_id("a", section), now()))
    before = store.all("SELECT * FROM chunks WHERE document_id='a'")
    queue_missing_figure_indexes(store)
    provider = object()  # Caption work cannot require an embedding or visual provider.
    worker = IngestionWorker(store, provider, Settings(root=tmp_path, embedding_enabled=False))
    asyncio.run(worker.process_figures(store.one("SELECT * FROM documents WHERE id='a'")))
    assert store.all("SELECT * FROM chunks WHERE document_id='a'") == before
    assert store.one("SELECT status,embedding_status FROM documents WHERE id='a'") == {"status": "ready", "embedding_status": "hybrid"}
    assert store.one("SELECT study_note FROM mindmap_annotations")["study_note"] == "旧笔记"
    assert store.one("SELECT figure_count,status FROM figure_index_jobs") == {"figure_count": 2, "status": "ready"}
    with store.connection() as conn:
        status = queue_figure_index(conn, store.one("SELECT * FROM documents WHERE id='a'"))
    assert status["status"] == "ready"
    assert store.one("SELECT attempts FROM figure_index_jobs")["attempts"] == 1
    ids = {row["id"] for row in store.all("SELECT id FROM document_figures")}
    queue_missing_figure_indexes(store)
    assert {row["id"] for row in store.all("SELECT id FROM document_figures")} == ids


def test_failed_backfill_is_safe_retryable_and_interrupted_or_old_work_recovers(store, tmp_path):
    document(store, "a")
    queue_missing_figure_indexes(store)
    worker = IngestionWorker(store, object(), Settings(root=tmp_path, embedding_enabled=False))
    asyncio.run(worker.process_figures(store.one("SELECT * FROM documents WHERE id='a'")))
    failed = store.one("SELECT * FROM figure_index_jobs")
    assert failed["status"] == "failed" and "not-opened" not in failed["error"]
    assert store.one("SELECT status FROM documents WHERE id='a'")["status"] == "ready"
    with store.connection() as conn:
        assert queue_figure_index(conn, store.one("SELECT * FROM documents WHERE id='a'"))["status"] == "queued"
        assert queue_figure_index(conn, store.one("SELECT * FROM documents WHERE id='a'"))["status"] == "queued"
    store.execute("UPDATE figure_index_jobs SET status='processing'")
    queue_missing_figure_indexes(store)
    assert store.one("SELECT status FROM figure_index_jobs")["status"] == "queued"
    store.execute("UPDATE figure_index_jobs SET status='ready',index_version='older-version'")
    queue_missing_figure_indexes(store)
    assert store.one("SELECT status,index_version FROM figure_index_jobs") == {"status": "queued", "index_version": FIGURE_INDEX_VERSION}


def test_deletion_during_backfill_cannot_restore_caption_nodes(store, tmp_path, monkeypatch):
    from backend.app import ingestion
    document(store, "a")
    queue_missing_figure_indexes(store)

    def removed_while_scanning(*args):
        store.execute("DELETE FROM documents WHERE id='a'")
        return extract_figure_captions("图1-1 原图题", 1)

    monkeypatch.setattr(ingestion, "scan_pdf_figures", removed_while_scanning)
    asyncio.run(IngestionWorker(store, object(), Settings(root=tmp_path, embedding_enabled=False)).process_figures(
        store.one("SELECT * FROM documents WHERE id='a'")))
    assert not store.all("SELECT * FROM document_figures") and not store.all("SELECT * FROM figure_index_jobs")


def test_real_pdf_upload_figure_routes_evidence_annotations_and_page_permissions(setup):
    client, app, _ = setup
    register(client)
    course = client.post("/api/courses", json={"title": "Real figure PDF"}).json()
    response = client.post(f"/api/courses/{course['id']}/documents", files={"file": ("figures.pdf", caption_pdf())},
                           data={"rights_confirmed": "true"})
    assert response.status_code == 202
    doc = wait_document(client, course["id"], response.json()["id"])
    route = f"/api/documents/{doc['id']}/figures"
    result = client.get(route).json()
    assert result["index"]["status"] == "ready" and len(result["items"]) == 2
    figure = result["items"][0]
    assert figure["caption_page"] == 1 and figure["page_image_url"].endswith("/pages/1/image")
    assert client.get(figure["page_image_url"]).status_code == 200
    graph_data = client.get(f"/api/courses/{course['id']}/graph").json()
    nodes = [node for node in graph_data["tree"]["nodes"] if node["kind"] == "figure"]
    assert len(nodes) == 2 and all(node["mastery"] is None for node in nodes)
    node_route = f"/api/courses/{course['id']}/mindmap/nodes/{figure['figure_id']}"
    evidence = client.get(node_route + "/evidence").json()
    assert evidence["evidence"] == [] and evidence["figure"]["figure_id"] == figure["figure_id"]
    assert client.put(node_route + "/annotation", json={"display_label": "我的图题", "study_note": "检查窗口变化"}).status_code == 200
    assert client.post(route + "/reindex").json()["index"]["status"] == "ready"
    bob = TestClient(app)
    register(bob, "bob")
    assert bob.get(route).status_code == bob.get(figure["page_image_url"]).status_code == 404
    assert bob.post(route + "/reindex").status_code == bob.get(node_route + "/evidence").status_code == 404
    app.state.db.execute("UPDATE documents SET status='failed' WHERE id=?", (doc["id"],))
    assert client.get(route).status_code == client.get(figure["page_image_url"]).status_code == 409
    assert client.get(node_route + "/evidence").status_code == 404
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert client.get(route).status_code == client.get(figure["page_image_url"]).status_code == 404
    assert not app.state.db.all("SELECT * FROM document_figures WHERE document_id=?", (doc["id"],))


def test_manual_shared_pdf_figures_are_personal_and_only_own_manual_nodes_can_be_deleted(setup):
    client, app, _ = setup
    alice = register(client)
    response = client.post("/api/courses/data-structures/documents", files={"file": ("shared.pdf", caption_pdf())},
                           data={"rights_confirmed": "true"})
    doc = wait_document(client, "data-structures", response.json()["id"])
    # This fixture models a genuinely shared built-in PDF, not another user's upload.
    app.state.db.execute("UPDATE documents SET user_id=NULL WHERE id=?", (doc["id"],))
    app.state.db.execute("UPDATE chunks SET user_id=NULL WHERE document_id=?", (doc["id"],))
    route = f"/api/documents/{doc['id']}/figures"
    auto_id = client.get(route).json()["items"][0]["figure_id"]
    added = client.post(route, json={"physical_page": 1, "figure_number": "3-52", "caption": "用户标注的窗口演化"})
    assert added.status_code == 201
    manual = added.json()
    assert manual["origin"] == "manual" and manual["verified"] is False
    assert manual["figure_id"].startswith("figure:manual:")
    assert manual["caption_page"] == 1 and manual["page_image_url"].endswith("/pages/1/image")
    bob = TestClient(app)
    register(bob, "bob")
    assert len(client.get(route).json()["items"]) == 3 and len(bob.get(route).json()["items"]) == 2
    node_route = f"/api/courses/data-structures/mindmap/nodes/{manual['figure_id']}"
    assert bob.get(node_route + "/evidence").status_code == 404
    assert bob.delete(route + "/" + manual["figure_id"]).status_code == 404
    assert client.delete(route + "/" + auto_id).status_code == 403
    assert client.post(route + "/reindex").status_code == 403
    for body in ({"physical_page": True, "caption": "图题"}, {"physical_page": 2, "caption": "图题"},
                 {"physical_page": 1, "caption": "   "}, {"physical_page": 1, "caption": "x" * 201},
                 {"physical_page": 1, "caption": "图题", "figure_number": "3-52\n3-53"}):
        assert client.post(route, json=body).status_code == 422
    bob_manual = bob.post(route, json={"physical_page": 1, "caption": "无图号个人标注"}).json()
    assert bob_manual["figure_number"] == "" and bob_manual["origin"] == "manual"
    assert all(item["figure_id"] != bob_manual["figure_id"] for item in client.get(route).json()["items"])
    assert client.put(node_route + "/annotation", json={"display_label": "我的窗口图", "study_note": "原页检查"}).status_code == 200
    assert client.delete(route + "/" + manual["figure_id"]).status_code == 200
    assert client.get(node_route + "/evidence").status_code == 404
    assert not app.state.db.one("SELECT 1 FROM mindmap_annotations WHERE user_id=? AND node_id=?", (alice["id"], manual["figure_id"]))
    assert bob.get(f"/api/courses/data-structures/mindmap/nodes/{bob_manual['figure_id']}/evidence").status_code == 200


def test_manual_nodes_survive_auto_rebuild_with_stable_ids_and_annotations(store):
    document(store, "a")
    path = "第一章 进程 / 1.1 线程"
    chunk(store, "a", path, 10)
    doc = store.one("SELECT * FROM documents WHERE id='a'")
    with store.connection() as conn:
        manual = add_manual_figure(conn, doc, "alice", 10, "1-9", "本人补充的图题")
    store.execute("INSERT INTO mindmap_annotations VALUES ('alice','private',?,'自定义图题','我的笔记',?)", (manual["figure_id"], now()))
    save_figures(store, "a", "图1-1 自动图题", section=path)
    save_figures(store, "a", "图1-2 新自动图题", section=path)
    result = graph(store)
    ids = {node["id"] for node in result["tree"]["nodes"] if node["kind"] == "figure"}
    assert ids == {manual["figure_id"], figure_id("a", 10, "1-2")}
    assert result["metadata"]["manual_figures"] == result["metadata"]["auto_figures"] == 1
    node = next(node for node in result["tree"]["nodes"] if node["id"] == manual["figure_id"])
    assert node["study_note"] == "我的笔记" and node["parent_id"] == heading_id("a", path)


@pytest.mark.parametrize("value", ["0", "513", "1.5", "true", ""])
def test_invalid_upload_environment_limit_fails_without_reading_secrets(monkeypatch, value):
    from backend.app import config
    monkeypatch.setattr(config, "read_dotenv", lambda path: None)
    monkeypatch.setenv("MAX_UPLOAD_MB", value)
    with pytest.raises(ValueError, match="MAX_UPLOAD_MB"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["1", "200", "512"])
def test_public_upload_limit_uses_valid_environment_setting(monkeypatch, value):
    from backend.app import config
    monkeypatch.setattr(config, "read_dotenv", lambda path: None)
    monkeypatch.setenv("MAX_UPLOAD_MB", value)
    assert Settings.from_env().max_upload_bytes == int(value) * 1024 * 1024
    assert Settings().max_upload_bytes == 200 * 1024 * 1024


def test_upload_limit_public_response_and_rejected_files_are_closed_and_cleaned(setup, monkeypatch):
    from starlette.datastructures import UploadFile
    client, app, _ = setup
    register(client)
    app.state.settings.max_upload_bytes = 8
    assert client.get("/api/config/public").json()["max_upload_bytes"] == 8
    closed = []
    original_close = UploadFile.close

    async def tracked_close(file):
        await original_close(file)
        closed.append((file.filename, file.file.closed))

    monkeypatch.setattr(UploadFile, "close", tracked_close)
    route = "/api/courses/data-structures/documents"
    before = app.state.db.one("SELECT COUNT(*) AS n FROM documents")["n"]
    response = client.post(route, files={"file": ("too-big.txt", b"123456789")}, data={"rights_confirmed": "true"})
    assert response.status_code == 413 and "8 字节" in response.json()["detail"]
    assert client.post(route, files={"file": ("unsupported.exe", b"123")}, data={"rights_confirmed": "true"}).status_code == 415
    assert client.post(route, files={"file": ("no-rights.txt", b"123")}).status_code == 422
    assert app.state.db.one("SELECT COUNT(*) AS n FROM documents")["n"] == before
    assert not list((app.state.settings.data_dir / "uploads").glob("*"))
    assert {name for name, is_closed in closed if is_closed} >= {"too-big.txt", "unsupported.exe", "no-rights.txt"}
