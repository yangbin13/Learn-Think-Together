"""Physical-page boundaries and evidence contracts; no live model evaluation."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.app.db import dumps
from backend.app.fact_review import (EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT,
                                    EVIDENCE_ADMISSIBILITY_TEACHING_PROMPT, REVIEW_PROMPT)
from backend.app.provider import ProviderError, validate_vision_physical_page
from backend.tests.test_api import events, register, setup  # noqa: F401
from backend.tests.test_material_tools import install_vision_protocol, upload_pdf
from backend.tests.test_provider_boundaries import adapter


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real outbound HTTP is forbidden in evidence admissibility tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)


@pytest.mark.parametrize("content", [
    "根据教材原页（物理页8，非问题所述21）核对如下。",
    "当前图像对应原PDF物理页8。", "本次页图来自第8物理页。",
    "该图像的物理页为8。", "原PDF物理页：8。", "### 物理页8\n观察如下。",
    "Current image corresponds to physical page 8.", "Physical page: 8.",
    "Printed page 8. Current image corresponds to physical page 8.",
])
def test_explicit_current_image_physical_page_conflict_is_rejected(content):
    with pytest.raises(ProviderError, match="物理页.*冲突"):
        validate_vision_physical_page(content, 21)


@pytest.mark.parametrize("content", [
    "本次页图对应PDF物理页21，页脚印刷号为8。",
    "当前图像物理页21（印刷页8）。", "图中印刷页号8。",
    "图中印刷页号8，不能将其称为物理页8。",
    "假设当前图像物理页8，则应另行核对；本次图像物理页21。",
    "如果物理页8和物理页21不同，请区分。",
    "与物理页8比較，当前图像对应物理页21。",
    "物理页8和物理页21不同，不据此猜当前位置。",
    "当前图像并非物理页8。", "当前图像未标注物理页8。",
    "当前图像物理页8吗？", "physical page 8 is a hypothetical comparison.",
    "示例：原PDF物理页：8。", "代码`物理页8`不是本次位置。",
    "物理页：8（假设）不代表本次图像的物理页。",
    "```text\n当前图像物理页8\n```\n本次图像物理页21。",
])
def test_printed_numbers_comparisons_questions_and_hypotheses_do_not_change_metadata(content):
    validate_vision_physical_page(content, 21)


@pytest.mark.parametrize("page", [None, True, False, 0, -1, 1.0, "1"])
def test_vision_page_must_be_a_server_positive_integer(page):
    with pytest.raises(ProviderError, match="正整数"):
        validate_vision_physical_page("标注清晰。", page)


def vision_adapter(contents, seen):
    async def create(**kwargs):
        index = len(seen)
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
               message=SimpleNamespace(content=contents[min(index, len(contents) - 1)], tool_calls=[]))],
               usage=None, model="test-vision-model")

    provider = adapter(create)
    provider.settings.vision_enabled = True
    provider.settings.vision_model = "test-vision-model"
    return provider


def test_actual_adapter_declares_trusted_physical_page_and_does_not_cache_bad_recognition():
    seen = []
    provider = vision_adapter(["根据教材原页（物理页4）核对。", dumps({
        "regions": [{"position": "页脚", "visible_text": ["印刷号4"], "visual_facts": []}],
        "limitations": [],
    })], seen)

    async def scenario():
        png = b"\x89PNG\r\n\x1a\nprotocol-only"
        with pytest.raises(ProviderError) as caught:
            await provider.interpret_image(png, "核对字段；图内号可能不同。", "测试教材", 13)
        assert caught.value.diagnostic["reason"] == "vision_page_conflict"
        assert caught.value.diagnostic["model"] == "test-vision-model"
        assert "教材原页" not in repr(caught.value.diagnostic)
        result = await provider.interpret_image(png, "重新核对字段。", "测试教材", 13)
        assert "印刷号4" in result["content"] and "物理页4" not in result["content"]
        assert len(seen) == 2
        for request in seen:
            assert "实际文档ID和物理页由服务器在返回结果时附加" in request["messages"][0]["content"]
            assert "物理页为13" not in request["messages"][0]["content"]
            assert "必须标为印刷号" in request["messages"][0]["content"]
            assert "图内没有重复角色标签" in request["messages"][0]["content"]
            assert "逐字核对像素" in request["messages"][0]["content"]
            assert "不能只概括数量或结构" in request["messages"][0]["content"]
            assert "物理页：13" not in request["messages"][1]["content"][0]["text"]
            assert "服务器实际渲染的PDF物理页：13" in result["content"]

    asyncio.run(scenario())


def test_vision_projection_preserves_student_question_and_pixel_bytes_while_server_owns_location():
    seen = []
    provider = vision_adapter([dumps({"regions": [{"position": "页脚", "visible_text": ["印刷号86"], "visual_facts": []}], "limitations": []})], seen)
    question = "第99物理页，factorial(4)与factorial(2)的图示，请核对原PDF第99物理页。"
    png = b"\x89PNG\r\n\x1a\nprotocol-pixels"
    result = asyncio.run(provider.interpret_image(png, question, "测试教材", 99))
    sent = seen[0]["messages"][1]["content"][0]["text"]
    assert "99" not in sent and "本次原页" in sent
    assert "factorial(4)" in sent and "factorial(2)" in sent
    assert question == "第99物理页，factorial(4)与factorial(2)的图示，请核对原PDF第99物理页。"
    assert "PDF物理页：99" in result["content"] and "印刷号86" in result["content"]


def test_admissibility_policy_allows_joint_support_but_retains_true_source_errors():
    assert EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT in REVIEW_PROMPT
    for term in ("题目明确给定", "待验证", "联合支持", "不要求每张图", "角色颠倒", "真实文字/图像冲突", "等价表述"):
        assert term in EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT
    assert "不属于新增计算" in EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT
    assert "正文角色定义" in EVIDENCE_ADMISSIBILITY_TEACHING_PROMPT
    assert all(word not in EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT for word in ("bad", "dab", "evil", "pat", "79", "66"))


@pytest.mark.parametrize("conflict", [False, True])
def test_temp_chat_keeps_server_page_ignores_model_metadata_and_refuses_current_page_conflict(setup, conflict):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    install_vision_protocol(provider, doc["id"])

    async def vision(png, question, title, physical_page):
        assert physical_page == 1
        return {"content": "当前图像对应物理页9。" if conflict else "本次图像物理页1，图内印刷页号9。",
                "model": "fake-vision-model", "usage": {"total_tokens": 20}, "page": 9, "physical_page": 9}

    provider.interpret_image = vision
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": "请核对第1物理页的图示。"}))
    if conflict:
        assert result[-1][0] == "error"
        assert any(event == "trace" and item["node"] == "interpret_pdf_page" and item["status"] == "failed"
                   and "位置冲突" in item["detail"] for event, item in result)
        assert not any(event in {"token", "citations", "done"} for event, _ in result)
        assert not app.state.db.one("SELECT id FROM messages WHERE role='assistant'")
    else:
        assert result[-1][0] == "done"
        image = next(item for event, payload in result if event == "citations" for item in payload["items"] if item.get("evidence_kind") == "image")
        assert image["page"] == 1 and image["location_label"] == "物理页1" and image["document_id"] == doc["id"]
        assert "印刷页号9" in image["excerpt"]


@pytest.mark.parametrize("true_source_error", [False, True])
def test_temp_chat_givens_and_same_object_text_plus_image_reach_independent_review(setup, true_source_error):
    client, app, provider = setup
    register(client)
    doc = upload_pdf(client)
    definition = "每个内部数组含两个元素，第一项是被检索的词，第二项是对应同义词。槽内数组可包含多个内部数组。"
    app.state.db.execute("UPDATE chunks SET text=? WHERE document_id=?", (definition, doc["id"]))
    install_vision_protocol(provider, doc["id"])
    previous_complete = provider.complete
    reviews = []

    async def vision(png, question, title, physical_page):
        return {"content": '图中无角色标签，但可见同一槽内的两组配对：["amber","warm"]和["cold","chill"]；是数组，没有链指针。',
                "model": "fake-vision-model"}

    async def stream(messages, **kwargs):
        prompt = messages[0]["content"]
        assert "同一对象的正文角色定义与图像字段可联合支持，各自绑定实际来源" in prompt
        assert "题干或历史字段不能改写成已观察字段" in prompt
        assert "未读清或相互矛盾的字段写未知" in prompt
        assert "图只画状态时不补未展示的计算过程" in prompt
        encoded = prompt.split("可引用证据（位置说明已由服务器标注）：\n", 1)[1].split("\n实际工具结果：", 1)[0]
        sources = json.loads(encoded)
        text_id = next(item["id"] for item in sources if item["document_id"] == doc["id"] and item.get("chunk_id"))
        image_id = next(item["id"] for item in sources if item.get("evidence_kind") == "image")
        caveat = "页图内容为视觉模型识别，可能误读，请对照原PDF原页图核验。"
        body = (caveat + "图中明确使用链表来保存条目。" if true_source_error else
                caveat + '按题给共同槽位条件，两词并非同一个词。正文定义第一项为检索词、第二项为同义词，'
                '结合对应图像可见的`["amber","warm"]`和`["cold","chill"]`两组配对，槽内数组保存两项键值对，新增一对不会覆盖另一对。')
        yield {"text": dumps({"blocks": [{"text": body, "source_ids": [text_id, image_id]}]})}

    async def complete(messages, **kwargs):
        if messages[0]["content"].startswith("你是独立事实审查器"):
            assert EVIDENCE_ADMISSIBILITY_REVIEW_PROMPT in messages[0]["content"]
            reviews.append(json.loads(messages[1]["content"]))
            return {"content": dumps({"approved": not true_source_error,
                    "issues": [{"kind": "source_support", "detail": "对应文字与图像只支持数组，不能归因为链表。"}] if true_source_error else []})}
        return await previous_complete(messages, **kwargs)

    provider.interpret_image, provider.stream, provider.complete = vision, stream, complete
    question = "题给两词都在第5槽，这就说明两词相同且新同义词覆盖旧同义词吧？请按第1物理页图示解释哪里错。"
    result = events(client.post("/api/chat", json={"course_id": "data-structures", "message": question, "mode": "explain"}))
    assert len(reviews) == (2 if true_source_error else 1)
    assert all(review["question"] == question for review in reviews)
    assert all(review["computations"] == [] for review in reviews)
    assert all(review["teaching_contract"]["evidence_admissibility_policy"]["provenance"] == "server" for review in reviews)
    assert all(any(item.get("evidence_kind") == "image" for item in review["evidence"])
               and any(definition == item["excerpt"] for item in review["evidence"]) for review in reviews)
    assert result[-1][0] == ("error" if true_source_error else "done")
    assert bool(app.state.db.one("SELECT id FROM messages WHERE role='assistant'")) is (not true_source_error)
    if true_source_error:
        assert not any(event in {"token", "done"} for event, _ in result)
    # Fake decisions verify preserved evidence and review flow, not critic quality.
