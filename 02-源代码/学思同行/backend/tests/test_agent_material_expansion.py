"""The tutor expands a located implementation excerpt before answering boundary questions."""
import json
import pytest

from backend.app.db import dumps
from backend.app.fact_review import REVIEW_PROMPT
from backend.app.agent import _explicit_visual_document, _explicit_visual_page_numbers
from backend.tests.test_api import events, register, setup


def test_binary_duplicate_question_reads_adjacent_implementation_code_and_reviews_it(setup, monkeypatch):
    client, app, provider = setup
    register(client)
    initial = {
        "chunk_id": "partial-binary-code",
        "document_id": "test-textbook",
        "page": 31,
        "section": "第2章 / 2.3 二分查找",
        "text": "以下是二分查找的 Ruby 实现。def binary_search(array, value)\nlower_bound = 0\nupper_bound = array.length - 1",
        "title": "测试教材",
        "source_type": "uploaded_pdf",
    }
    implementation = {
        **initial,
        "chunk_id": "complete-binary-implementation",
        "page": 32,
        "section": "第2章 / 2.4 二分查找与线性查找",
        "text": "while lower_bound <= upper_bound do\nif value < value_at_midpoint\nupper_bound = midpoint - 1\nelsif value > value_at_midpoint\nlower_bound = midpoint + 1\nelsif value == value_at_midpoint\nreturn midpoint\nend\nend",
    }
    read_calls = []

    async def search(course_id, user_id, query, limit=6):
        return {"items": [initial], "method": "BM25", "notice": "test fixture"}

    monkeypatch.setattr(app.state.agent.retriever, "search", search)
    monkeypatch.setattr("backend.app.agent.accessible_document", lambda *args: {"page_count": 100})

    def read_pages(db, course_id, user_id, document_id, start_page, end_page=None):
        read_calls.append((document_id, start_page, end_page))
        return {"items": [implementation], "start_page": start_page, "end_page": end_page,
                "notice": "test fixture"}

    monkeypatch.setattr("backend.app.agent.read_material_pages", read_pages)
    reviews = []

    async def complete(messages, tools=None, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            reviews.append(json.loads(messages[1]["content"]))
            return {"content": dumps({"approved": True, "issues": []}), "model": "fake-review-model"}
        return {"content": "DONE", "model": "fake-planner-model"}

    async def stream(messages, **kwargs):
        prompt = messages[0]["content"]
        cards = json.loads(prompt.split("可引用证据（位置说明已由服务器标注）：\n", 1)[1]
                           .split("\n实际工具结果：\n", 1)[0])
        code = next(item for item in cards if "return midpoint" in item["excerpt"])
        yield {"text": dumps({"blocks": [{
            "text": "该教材这段实现命中相等值时直接返回midpoint，没有继续向左检查；因此不能据此保证返回重复值中的第一个。这是对这段实现的推论，不代表所有二分查找变体。",
            "source_ids": [code["id"]],
        }]}), "model": "fake-teaching-model"}

    provider.complete, provider.stream = complete, stream
    question = "普通二分查找遇到重复值时会保证返回第一个吗？请按教材中的Ruby实现解释。"
    result = events(client.post("/api/chat", json={
        "course_id": "data-structures", "message": question, "mode": "explain"}))

    assert result[-1][0] == "done", [(name, item.get("node"), item.get("status"), item.get("detail")) for name, item in result]
    assert read_calls == [("test-textbook", 30, 32)]
    assert any(item["node"] == "read_material_pages" and "30–32" in item["detail"]
               for name, item in result if name == "trace")
    assert len(reviews) == 1
    cited = reviews[0]["evidence"]
    assert any(item["page"] == 32 and "return midpoint" in item["excerpt"] for item in cited)
    assert "真实代码或伪码本身是来源" in REVIEW_PROMPT
    assert result[-1][1]["grounded"] is True


@pytest.mark.parametrize("page,vision_enabled,text_failure,image_failure", [
    (79, True, False, False), (1, True, False, False), (100, True, False, False),
    (79, False, False, False), (79, True, True, False), (79, True, False, True),
])
def test_explicit_physical_page_figure_request_is_read_before_planner(
        setup, monkeypatch, page, vision_enabled, text_failure, image_failure):
    client, app, provider = setup
    register(client)
    document = {"id": "fixture-pdf-id", "title": "测试教材.pdf", "page_count": 100, "source_type": "uploaded_pdf"}
    original_all = app.state.db.all

    def all_rows(query, params=()):
        if query.startswith("SELECT id,title,page_count FROM documents"):
            return [document]
        return original_all(query, params)

    monkeypatch.setattr(app.state.db, "all", all_rows)
    monkeypatch.setattr(app.state.agent.settings, "vision_enabled", vision_enabled)
    async def search(*args, **kwargs):
        return {"items": [], "method": "BM25", "notice": "test fixture"}

    monkeypatch.setattr(app.state.agent.retriever, "search", search)
    monkeypatch.setattr("backend.app.agent.accessible_document", lambda *args: {
        **document, "path": "fixture.pdf"})
    nearby_text = {
        "chunk_id": "neighbor-page-text", "document_id": document["id"],
        "page": max(1, page - 1), "section": "第9章 / 阶乘实现",
        "text": "递归实现将当前参数与下一次调用的返回值相乘。",
        "title": document["title"], "source_type": document["source_type"],
    }
    read_calls, review_payloads = [], []

    def read_pages(db, course_id, user_id, document_id, start_page, end_page=None):
        read_calls.append((document_id, start_page, end_page))
        if text_failure:
            raise ValueError("测试文字层不可用")
        return {"items": [nearby_text], "start_page": start_page,
                "end_page": end_page, "notice": "test fixture"}

    monkeypatch.setattr("backend.app.agent.read_material_pages", read_pages)
    monkeypatch.setattr("backend.app.agent.checked_page_range", lambda *args: (page, page))
    monkeypatch.setattr("backend.app.agent.render_pdf_page", lambda *args: {
        "png": b"\x89PNG\r\n\x1a\nfixture", "width": 100, "height": 120})
    image_calls = []

    async def interpret_image(png, question, title, physical_page):
        image_calls.append((png, question, title, physical_page))
        if image_failure:
            raise ValueError("测试图像不可用")
        return {"content": f"当前PDF物理页为{physical_page}。图中第8槽有 bad→evil 与 dab→pat 两组键值对。",
                "model": "fake-vision-model", "usage": {"total_tokens": 4}}

    async def complete(messages, tools=None, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            review_payloads.append(json.loads(messages[1]["content"]))
            return {"content": dumps({"approved": True, "issues": []}),
                    "model": "fake-review-model"}
        return {"content": "DONE", "model": "fake-planner-model"}

    async def stream(messages, **kwargs):
        prompt = messages[0]["content"]
        cards = json.loads(prompt.split("可引用证据（位置说明已由服务器标注）：\n", 1)[1]
                           .split("\n实际工具结果：\n", 1)[0])
        image = next((item for item in cards if item.get("evidence_kind") == "image"), None)
        if image is None:
            text = next(item for item in cards if item.get("evidence_kind") != "image")
            yield {"text": dumps({"blocks": [{
                "text": "当前图像依据不足，无法确认图中具体内容。文字说明递归实现将参数与返回值相乘。",
                "source_ids": [text["id"]],
            }]}), "model": "fake-teaching-model"}
            return
        yield {"text": dumps({"blocks": [{
            "text": "按视觉识别结果，第8槽似乎包含 bad→evil 和 dab→pat 两组键值对，模型可能误读，请对照原PDF页图；同槽冲突不表示两个键相同。",
            "source_ids": [image["id"]],
        }]}), "model": "fake-teaching-model"}

    provider.interpret_image = interpret_image
    provider.complete, provider.stream = complete, stream
    result = events(client.post("/api/chat", json={
        "course_id": "data-structures",
        "message": f"我正在学测试教材。请根据第{page}物理页图中散列表，说明 bad 和 dab 是否处于同一个槽。",
        "mode": "explain"}))

    assert result[-1][0] == "done", result[-1]
    assert len(image_calls) == int(vision_enabled)
    if image_calls:
        assert image_calls[0][3] == page
    assert read_calls == [(document["id"], max(1, page - 1), min(100, page + 1))]
    assert len(review_payloads) == 1
    assert any(item["page"] == max(1, page - 1) and "相乘" in item["excerpt"]
               for item in review_payloads[0]["evidence"]) is not text_failure, {
                   "evidence": review_payloads[0]["evidence"],
                   "traces": [(value.get("node"), value.get("detail"))
                              for name, value in result if name == "trace"],
               }
    image_trace = [item for name, item in result if name == "trace" and item["node"] == "interpret_pdf_page"]
    assert len(image_trace) == 1
    assert image_trace[0]["status"] == ("completed" if vision_enabled and not image_failure else "failed")
    assistant = app.state.db.one("SELECT content,citations FROM messages WHERE role='assistant'")
    citations = json.loads(assistant["citations"])
    assert any(item.get("page") == page and item.get("evidence_kind") == "image" for item in citations) is (vision_enabled and not image_failure)
    assert result[-1][1]["grounded"] is True


def test_visual_page_detection_and_document_resolution_never_guess():
    assert _explicit_visual_page_numbers("按图说明第79物理页的散列表") == [79]
    assert _explicit_visual_page_numbers("请解释二分查找的比较次数") == []
    assert _explicit_visual_page_numbers("根据图中内容核对物理页 12 和第13物理页") == [12, 13]
    assert _explicit_visual_page_numbers("根据图中内容核对第3页") == []

    books = [{"id": "book-a", "title": "A.pdf"}, {"id": "book-b", "title": "B.pdf"}]
    assert _explicit_visual_document(books, "请查看 book-b 的物理页79图") == books[1]
    assert _explicit_visual_document(books, "请看这张教材的物理页79图") is None
    assert _explicit_visual_document([books[0]], "请看这本教材的物理页79图") == books[0]
