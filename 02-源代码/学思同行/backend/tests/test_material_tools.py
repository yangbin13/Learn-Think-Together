import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.db import dumps, now, uid
from backend.app.materials import MaterialAccessError, MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS, accessible_document, read_material_pages, render_pdf_page
from backend.app.provider import ProviderError
from backend.tests.test_api import setup, register, events, wait_document


def pdf_bytes(pages=2, width=612, height=792):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for index in range(pages):
        page = writer.add_blank_page(width=width, height=height)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        text = DecodedStreamObject()
        text.set_data(f"BT /F1 12 Tf 20 20 Td (Material page {index + 1}: independent source text) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(text)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def upload_pdf(client, course_id="data-structures", pages=2):
    response = client.post(f"/api/courses/{course_id}/documents", files={"file": ("visual.pdf", pdf_bytes(pages))}, data={"rights_confirmed": "true"})
    assert response.status_code == 202
    doc = wait_document(client, course_id, response.json()["id"])
    assert doc["status"] == "ready" and doc["page_count"] == pages
    return doc


def test_page_image_api_rechecks_owner_type_readiness_and_physical_bounds(setup):
    client, app, _ = setup
    register(client)
    doc = upload_pdf(client)
    route = f"/api/documents/{doc['id']}/pages/2/image"
    response = client.get(route)
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-pdf-physical-page"] == "2"
    with Image.open(io.BytesIO(response.content)) as image:
        assert max(image.size) <= MAX_IMAGE_EDGE and image.width * image.height <= MAX_IMAGE_PIXELS
    bob = TestClient(app)
    register(bob, "bob")
    assert bob.get(route).status_code == 404
    assert TestClient(app).get(route).status_code == 401
    assert client.get(f"/api/documents/{doc['id']}/pages/0/image").status_code == 422
    assert client.get(f"/api/documents/{doc['id']}/pages/3/image").status_code == 422
    assert client.get("/api/documents/seed-does-not-exist/pages/1/image").status_code == 404
    app.state.db.execute("UPDATE documents SET status='processing' WHERE id=?", (doc["id"],))
    assert client.get(route).status_code == 409
    seed_id = app.state.db.one("SELECT id FROM documents WHERE user_id IS NULL")["id"]
    assert client.get(f"/api/documents/{seed_id}/pages/1/image").status_code == 415


def test_material_deep_read_preserves_separate_pages_and_rejects_foreign_courses(setup):
    client, app, _ = setup
    user = register(client)
    doc = upload_pdf(client, pages=4)
    read = read_material_pages(app.state.db, "data-structures", user["id"], doc["id"], 2, 4)
    assert {item["page"] for item in read["items"]} == {2, 3, 4}
    assert all(item["document_id"] == doc["id"] and "Material page" in item["text"] for item in read["items"])
    for start, end in [(0, 1), (4, 5), (3, 2), (1, 4), (True, 1), (1.0, 2)]:
        with pytest.raises(MaterialAccessError):
            read_material_pages(app.state.db, "data-structures", user["id"], doc["id"], start, end)
    with pytest.raises(MaterialAccessError) as denied:
        read_material_pages(app.state.db, "foreign-course", user["id"], doc["id"], 1)
    assert denied.value.status_code == 404
    with pytest.raises(MaterialAccessError):
        accessible_document(app.state.db, "data-structures", "foreign-user", doc["id"])


def test_huge_pdf_geometry_is_scaled_before_allocating_bitmap(tmp_path):
    path = tmp_path / "huge.pdf"
    path.write_bytes(pdf_bytes(1, width=100_000, height=80_000))
    rendered = render_pdf_page({"path": str(path), "page_count": 1}, 1)
    assert max(rendered["width"], rendered["height"]) <= MAX_IMAGE_EDGE
    assert rendered["width"] * rendered["height"] <= MAX_IMAGE_PIXELS
    assert len(rendered["png"]) < 8 * 1024 * 1024


def install_vision_protocol(provider, doc_id, calls=1, answer_prefix="", pages=None):
    provider.vision_calls = []
    requested_pages = pages or [1] * calls

    async def planning(messages, **kwargs):
        if messages[0].get("content", "").startswith("你是独立事实审查器"):
            return {"content": dumps({"approved": True, "issues": []}), "model": "fake-test-review-model"}
        if any(message["role"] == "tool" for message in messages):
            return {"tool_calls": [], "model": "fake-test-model"}
        return {"tool_calls": [{"id": f"vision-{index}", "name": "interpret_pdf_page", "arguments": dumps({"document_id": doc_id, "physical_page": page, "question": "核对图中标注，不猜未读清的字符"})} for index, page in enumerate(requested_pages)], "model": "fake-test-model"}

    async def vision(png, question, title, physical_page):
        assert png.startswith(b"\x89PNG") and 1 <= physical_page <= 3
        provider.vision_calls.append((title, physical_page))
        return {"content": "测试协议模型识别：原页标注清晰，其余字符需原页核验。", "model": "fake-vision-model", "usage": {"total_tokens": 20}}

    async def teaching(messages, **kwargs):
        provider.stream_histories.append(messages)
        encoded = messages[0]["content"].split("可引用证据（位置说明已由服务器标注）：\n", 1)[1].split("\n实际工具结果：", 1)[0]
        image_id = next(citation["id"] for citation in json.loads(encoded) if citation.get("evidence_kind") == "image") if '"evidence_kind":"image"' in encoded else 999
        yield {"text": f"{answer_prefix}模型页图识别结果可能误读，请对照原PDF页图核验[{image_id}]。", "model": "fake-test-model"}

    provider.complete, provider.interpret_image, provider.stream = planning, vision, teaching


def test_vision_tool_citations_are_real_physical_images_and_two_call_budget(setup):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client, pages=3)
    install_vision_protocol(provider, doc["id"], calls=3, pages=[1, 2, 3])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "请核对原文件物理页1、物理页2和物理页3的图示"}))
    assert result[-1][0] == "done"
    assert len(provider.vision_calls) == 2
    failed = [payload for event, payload in result if event == "trace" and payload["node"] == "interpret_pdf_page" and payload["status"] == "failed"]
    assert len(failed) == 1 and "2次上限" in failed[0]["detail"]
    citation = next(payload for event, payload in result if event == "citations")["items"][0]
    assert citation["document_id"] == doc["id"] and citation["page"] == 1
    assert citation["page_kind"] == "physical" and citation["source_type"] == "uploaded_pdf"
    assert citation["evidence_kind"] == "image" and citation["model"] == "fake-vision-model"
    assert citation["vision_model"] == "fake-vision-model"
    assert "chunk_id" not in citation and "不是确定性证明" in citation["uncertainty"]
    stored = client.get(f"/api/sessions/{result[-1][1]['session_id']}/messages").json()[-1]
    assert stored["citations"][0] == citation


def test_vision_tool_refuses_guessed_pages_and_foreign_documents(setup):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    install_vision_protocol(provider, doc["id"])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释教材图形"}))
    assert result[-1][0] == "error" and provider.vision_calls == []
    assert any("不可猜造页码" in payload.get("detail", "") for event, payload in result if event == "trace")
    bob = TestClient(app)
    register(bob, "bob")
    result = events(bob.post("/api/chat", json={"course_id": "data-structures", "message": "请看物理页1"}))
    assert result[-1][0] == "error" and provider.vision_calls == []
    assert any("不存在可读取的教材" in payload.get("detail", "") for event, payload in result if event == "trace")


@pytest.mark.parametrize("location", ["第1物理页", "1物理页", "第 1 物理页"])
def test_vision_tool_accepts_explicit_postfixed_physical_page_requests(setup, location):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    install_vision_protocol(provider, doc["id"])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": f"请核对原 PDF {location}的图示"}))
    assert result[-1][0] == "done" and len(provider.vision_calls) == 1, {"final": result[-1], "vision_calls": provider.vision_calls}
    citation = next(payload for event, payload in result if event == "citations")["items"][0]
    assert citation["document_id"] == doc["id"] and citation["page"] == 1 and citation["evidence_kind"] == "image"


@pytest.mark.parametrize("locator", [
    "文档ID {document_id}，原PDF物理页1",
    "文档 ID 为 {document_id}，位于原 PDF 物理页 1",
])
def test_vision_answer_covers_teaching_terms_without_repeating_source_metadata(setup, locator):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    install_vision_protocol(provider, doc["id"], answer_prefix="TCP 的 SYN 同步请求。")
    question = "请解释图中 TCP 三次握手中 SYN 的作用。" + locator.format(document_id=doc["id"])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": question}))
    assert result[-1][0] == "done" and len(provider.vision_calls) == 1
    assert not any(event == "answer_reset" for event, _ in result)
    citation = next(payload for event, payload in result if event == "citations")["items"][0]
    assert citation["document_id"] == doc["id"] and citation["page"] == 1 and citation["evidence_kind"] == "image"
    messages = client.get(f"/api/sessions/{result[-1][1]['session_id']}/messages").json()
    assert next(message["content"] for message in messages if message["role"] == "user") == question


def test_vision_answer_still_must_cover_pdf_and_id_when_the_student_asks_about_them(setup):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    install_vision_protocol(provider, doc["id"], answer_prefix="TCP 的 SYN 同步请求。")
    question = ("请解释图中 TCP 与 SYN，并说明 PDF 文件格式和 ID 字段。"
                + f"文档ID {doc['id']}，原PDF物理页1")
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": question}))
    assert result[-1][0] == "error" and len(provider.vision_calls) == 1
    assert "命名术语：ID" in result[-1][1]["message"]


def test_assistant_history_postfixed_page_does_not_authorize_an_unlocated_vision_call(setup):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    session = client.post("/api/courses/data-structures/sessions", json={}).json()
    app.state.db.execute("INSERT INTO messages (id,session_id,role,content,created_at) VALUES (?,?,'assistant','第1物理页',?)",
                         (uid(), session["id"], now()))
    install_vision_protocol(provider, doc["id"])
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": session["id"], "message": "请解释图内关系"}))
    assert result[-1][0] == "error" and provider.vision_calls == []
    assert any("不可猜造页码" in payload.get("detail", "") for event, payload in result if event == "trace")


def changing_vision_protocol(provider):
    protocol = {"target": None, "direction": "", "old_direction": "北端→南端", "fail": False, "missing_label": "来源",
                "planning": [], "vision": [], "teaching": [], "reviews": []}

    async def planning(messages, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            review = json.loads(messages[1]["content"])
            protocol["reviews"].append(review)
            images = [item for item in review["evidence"] if item.get("evidence_kind") == "image"]
            supported = (images[-1]["excerpt"] in review["draft"] if images else
                         ("缺少本轮图像" + protocol["missing_label"]) in review["draft"])
            return {"content": dumps({"approved": supported, "issues": [] if supported else
                    [{"kind": "source_support", "detail": "草稿观察与本轮实际图像结果不一致"}]}), "model": "fake-review-model"}
        protocol["planning"].append(json.loads(dumps(messages)))
        if any(message["role"] == "tool" for message in messages):
            return {"tool_calls": [], "model": "fake-test-model"}
        question = "核对目标图的实际箭头，不补造未知字段"
        if any(protocol["old_direction"] in (message.get("content") or "")
               for message in messages if message["role"] == "assistant"):
            question += "，先前已知条件为" + protocol["old_direction"]
        document_id, page = protocol["target"]
        return {"tool_calls": [{"id": "current-vision", "name": "interpret_pdf_page",
                "arguments": dumps({"document_id": document_id, "physical_page": page, "question": question})}], "model": "fake-test-model"}

    async def vision(png, question, title, physical_page):
        assert png.startswith(b"\x89PNG") and physical_page == protocol["target"][1]
        protocol["vision"].append({"question": question, "page": physical_page})
        if protocol["fail"]:
            raise ProviderError("测试页图识别暂不可用")
        return {"content": protocol["direction"], "model": "fake-current-vision-model"}

    async def teaching(messages, **kwargs):
        protocol["teaching"].append(json.loads(dumps(messages)))
        encoded = messages[0]["content"].split("可引用证据（位置说明已由服务器标注）：\n", 1)[1].split("\n实际工具结果：", 1)[0]
        images = [item for item in json.loads(encoded) if item.get("evidence_kind") == "image"]
        if not images:
            yield {"text": f"缺少本轮图像{protocol['missing_label']}，页图未成功读取，无法确认具体箭头；请查看原页核验。"}
            return
        direction = images[-1]["excerpt"]
        # A context-sensitive fake reproduces the old-value contamination if history is replayed.
        if any(protocol["old_direction"] in message["content"] for message in messages if message["role"] == "assistant"):
            direction = protocol["old_direction"]
        yield {"text": f"本轮页图模型识别的方向为{direction}，可能误读，请对照原PDF原页图核验 [{images[-1]['id']}]。", "model": "fake-test-model"}

    provider.complete, provider.interpret_image, provider.stream = planning, vision, teaching
    return protocol


@pytest.mark.parametrize("other_source", ["other_document", "other_page", "mixed_message"])
def test_same_page_reread_uses_new_image_without_replaying_stale_assistant_fields(setup, other_source):
    client, app, provider = setup
    register(client)
    ordinary = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "解释栈"}))
    assert ordinary[-1][0] == "done"
    session_id = ordinary[-1][1]["session_id"]
    doc = upload_pdf(client)
    other = upload_pdf(client) if other_source == "other_document" else doc
    other_page = 1 if other_source == "other_document" else 2
    protocol = changing_vision_protocol(provider)

    def ask(target, direction, reread=False):
        protocol["target"], protocol["direction"] = target, direction
        question = f"请{'重新' if reread else ''}实际读取文档ID {target[0]} 的原PDF物理页{target[1]}，核对目标图的箭头。"
        result = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": session_id, "message": question}))
        assert result[-1][0] == "done", result[-1]
        return result, question

    other_result, other_question = ask((other["id"], other_page), "左端→右端")
    old_result, old_question = ask((doc["id"], 1), protocol["old_direction"])
    if other_source == "mixed_message":
        old_row = app.state.db.one("SELECT * FROM messages WHERE id=?", (old_result[-1][1]["message_id"],))
        image_sources = json.loads(old_row["citations"])
        other_image = next(item for event, payload in other_result if event == "citations" for item in payload["items"] if item.get("evidence_kind") == "image")
        other_image = {**other_image, "id": max(item["id"] for item in image_sources) + 1}
        app.state.db.execute("UPDATE messages SET content=?,citations=? WHERE id=?",
                             (old_row["content"] + f"另一图观察为左端→右端 [{other_image['id']}]。", dumps(image_sources + [other_image]), old_row["id"]))
    prior_rows = app.state.db.all("SELECT * FROM messages WHERE session_id=? ORDER BY rowid", (session_id,))
    new_direction = "南端→北端"
    result, new_question = ask((doc["id"], 1), new_direction, True)
    assert new_direction in result[-1][1]["final_text"] and protocol["old_direction"] not in result[-1][1]["final_text"]
    assert not any(event == "answer_reset" for event, _ in result)
    image = next(item for event, payload in result if event == "citations" for item in payload["items"] if item.get("evidence_kind") == "image")
    assert image["document_id"] == doc["id"] and image["page"] == 1 and image["excerpt"] == new_direction
    assert protocol["old_direction"] not in protocol["vision"][-1]["question"]
    teaching = protocol["teaching"][-1]
    assistants = [message["content"] for message in teaching if message["role"] == "assistant"]
    assert not any(protocol["old_direction"] in content for content in assistants)
    assert any("左端→右端" in content for content in assistants)
    assert any("栈具有后进先出的特征" in content for content in assistants)
    assert any("本轮已成功重新读取" in content for content in assistants)
    if other_source == "mixed_message":
        mixed_notice = next(content for content in assistants if "本轮已成功重新读取" in content)
        assert "物理页 1" in mixed_notice and "物理页 2" in mixed_notice and "本轮尚未成功重读" in mixed_notice
        assert "左端→右端" not in mixed_notice
    assert new_direction in teaching[0]["content"]
    current_review = protocol["reviews"][-1]
    assert not any(protocol["old_direction"] in message["content"] for message in current_review["conversation"] if message["role"] == "assistant")
    assert any(item.get("evidence_kind") == "image" and item["excerpt"] == new_direction for item in current_review["evidence"])
    for question in ["解释栈", other_question, old_question, new_question]:
        assert any(message["role"] == "user" and message["content"] == question for message in teaching)
    last_planning = protocol["planning"][-1]
    assert not any(protocol["old_direction"] in (message.get("content") or "") for message in last_planning if message["role"] == "assistant")
    assert any("栈具有后进先出的特征" in message["content"] for message in last_planning if message["role"] == "assistant" and message.get("content"))
    assert app.state.db.all("SELECT * FROM messages WHERE session_id=? ORDER BY rowid LIMIT ?", (session_id, len(prior_rows))) == prior_rows
    assert len(protocol["vision"]) == 3


@pytest.mark.parametrize("failure_stage", ["vision", "render"])
@pytest.mark.parametrize("missing_label", ["来源", "依据"])
def test_failed_same_page_reread_does_not_replay_old_fields_or_claim_refresh_success(setup, monkeypatch, failure_stage, missing_label):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    protocol = changing_vision_protocol(provider)
    protocol["missing_label"] = missing_label
    protocol["target"], protocol["direction"] = (doc["id"], 1), protocol["old_direction"]
    question = f"请实际读取文档ID {doc['id']} 的原PDF物理页1，核对目标图的箭头。"
    first = events(client.post("/api/chat", json={"course_id": "data-structures", "message": question}))
    assert first[-1][0] == "done"
    session_id = first[-1][1]["session_id"]
    old_row = app.state.db.one("SELECT * FROM messages WHERE id=?", (first[-1][1]["message_id"],))
    protocol["fail"] = True
    if failure_stage == "render":
        def broken_render(*args):
            raise MaterialAccessError("测试原页渲染失败")
        monkeypatch.setattr("backend.app.agent.render_pdf_page", broken_render)
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": session_id, "message": question.replace("请实际", "请重新实际")}))
    assert result[-1][0] == "done" and result[-1][1]["grounded"] is False, result[-1]
    assert "缺少本轮图像" + missing_label in result[-1][1]["final_text"]
    assert next(payload for event, payload in result if event == "citations")["items"] == []
    assert any(event == "trace" and payload["node"] == "interpret_pdf_page" and payload["status"] == "failed" for event, payload in result)
    assistants = [message["content"] for message in protocol["teaching"][-1] if message["role"] == "assistant"]
    assert not any(protocol["old_direction"] in content or "本轮已成功重新读取" in content for content in assistants)
    assert any("本轮尚未成功重读" in content for content in assistants)
    assert len(protocol["vision"]) == (1 if failure_stage == "render" else 2)
    assert not any(item.get("evidence_kind") == "image" for item in protocol["reviews"][-1]["evidence"])
    assert app.state.db.one("SELECT * FROM messages WHERE id=?", (old_row["id"],)) == old_row


def test_neutral_old_image_location_cannot_authorize_or_cite_a_page_without_user_or_retrieval_location(setup):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    session = client.post("/api/courses/data-structures/sessions", json={}).json()
    citation = {"id": 9, "document_id": doc["id"], "page": 1, "source_type": "uploaded_pdf", "evidence_kind": "image",
                "title": doc["title"], "excerpt": "北端→南端", "vision_model": "fake-past-model"}
    old_id = uid()
    app.state.db.execute("INSERT INTO messages (id,session_id,role,content,citations,created_at) VALUES (?,?,'assistant',?,?,?)",
                         (old_id, session["id"], "先前页图为北端→南端 [9]。", dumps([citation]), now()))
    old_row = app.state.db.one("SELECT * FROM messages WHERE id=?", (old_id,))
    protocol = changing_vision_protocol(provider)
    protocol["target"] = (doc["id"], 1)

    async def no_matches(*args, **kwargs):
        return {"items": [], "method": "BM25", "notice": "测试本轮没有定位命中"}

    app.state.retriever.search = no_matches
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "session_id": session["id"], "message": "请核对目标图的箭头。"}))
    assert protocol["vision"] == []
    assert any(event == "trace" and payload["node"] == "interpret_pdf_page" and "不可猜造页码" in payload["detail"] for event, payload in result)
    assert result[-1][0] == "done" and result[-1][1]["grounded"] is False
    assert next(payload for event, payload in result if event == "citations")["items"] == []
    initial_planning = protocol["planning"][0]
    assert any("物理页 1" in message["content"] and "历史定位" in message["content"] for message in initial_planning if message["role"] == "assistant")
    assert not any(item.get("evidence_kind") == "image" for item in protocol["reviews"][-1]["evidence"])
    assert app.state.db.one("SELECT * FROM messages WHERE id=?", (old_id,)) == old_row


def test_usage_sums_independent_planner_vision_and_teaching_requests_once(setup):
    client, _, provider = setup
    register(client)
    doc = upload_pdf(client)
    install_vision_protocol(provider, doc["id"], calls=2)
    original_planning, original_teaching = provider.complete, provider.stream

    async def planning_with_usage(messages, **kwargs):
        result = await original_planning(messages, **kwargs)
        total = 11 if messages[0].get("content", "").startswith("你是独立事实审查器") else (7 if any(message["role"] == "tool" for message in messages) else 13)
        return {**result, "usage": {"total_tokens": total}}

    async def teaching_with_usage(messages, **kwargs):
        async for event in original_teaching(messages, **kwargs):
            yield event
        yield {"usage": {"total_tokens": 30}}

    provider.complete, provider.stream = planning_with_usage, teaching_with_usage
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "请核对物理页1的图示"}))
    assert result[-1][0] == "done" and len(provider.vision_calls) == 1
    assert result[-1][1]["usage"]["total_tokens"] == 13 + 7 + 20 + 30 + 11


def test_heading_only_chunk_cannot_be_ranked_as_fact_evidence(setup):
    client, app, _ = setup
    user = register(client)
    doc_id = app.state.db.one("SELECT id FROM documents WHERE user_id IS NULL")["id"]
    app.state.db.execute("INSERT INTO chunks VALUES ('title-only',?,'data-structures',NULL,1,'稀有结构章节标题','稀有结构章节标题',NULL)", (doc_id,))
    result = asyncio.run(app.state.retriever.search("data-structures", user["id"], "稀有结构章节标题"))
    assert all(item["chunk_id"] != "title-only" for item in result["items"])
