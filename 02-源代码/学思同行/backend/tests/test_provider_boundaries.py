import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.app.provider import Provider, ProviderError, extract_json, completion_budget_with_thinking


@pytest.mark.parametrize("visible,thinking,budget,expected", [
    (1100, False, 2048, 1100), (1100, True, 2048, 3148),
    (4000, True, 1024, 5024), (4200, True, 1024, 5224),
])
def test_reasoning_and_visible_response_have_separate_space_in_total_output_cap(visible, thinking, budget, expected):
    assert completion_budget_with_thinking(visible, thinking, budget) == expected


@pytest.mark.parametrize("visible,thinking,budget", [
    (True, True, 1024), (0, False, 1024), (1100, "yes", 1024),
    (1100, True, True), (1100, True, 127), (4000, True, 32768),
])
def test_invalid_or_overflowing_combined_output_budget_fails_before_provider_call(visible, thinking, budget):
    with pytest.raises(ProviderError):
        completion_budget_with_thinking(visible, thinking, budget)


def test_provider_caches_windows_request_headers_without_wmi(monkeypatch):
    import backend.app.provider as provider_module

    class FakeClient:
        def __init__(self, **kwargs):
            self.arguments = kwargs
            self._platform = None
            self._version = "test-version"

    settings = SimpleNamespace(api_key="test-only", api_base="https://example.invalid/v1", provider_timeout=3)
    with monkeypatch.context() as patch:
        patch.setattr(provider_module.sys, "platform", "win32")
        patch.setattr(provider_module, "AsyncOpenAI", FakeClient)
        provider = Provider(settings)

    assert provider.client._platform == "Windows"
    assert provider.client.arguments["timeout"] == 3
    headers = provider.client.platform_headers()
    assert headers["X-Stainless-OS"] == "Windows"
    assert headers["X-Stainless-Package-Version"] == "test-version"
    assert headers["X-Stainless-Arch"] in {"x32", "x64"}


def test_embedding_uses_its_own_endpoint_and_key_without_changing_chat_client(monkeypatch):
    import backend.app.provider as provider_module
    from backend.app.config import Settings

    clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.arguments = kwargs
            self._version = "test-version"
            self.closed = False
            self.embeddings = SimpleNamespace(create=self.create_embedding)
            clients.append(self)

        async def create_embedding(self, **kwargs):
            self.embedding_request = kwargs
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1, 0.2])])

        async def close(self):
            self.closed = True

    monkeypatch.setattr(provider_module, "AsyncOpenAI", FakeClient)
    settings = Settings(api_key="chat-secret", api_base="https://chat.example/v1",
                        model="mimo-v2.6-flash", embedding_api_key="vector-secret",
                        embedding_api_base="https://vectors.example/v1/", embedding_model="BAAI/bge-m3")
    provider = Provider(settings)
    assert len(clients) == 2
    assert provider.client.arguments["api_key"] == "chat-secret"
    assert provider.client.arguments["base_url"] == "https://chat.example/v1"
    assert provider.embedding_client.arguments["api_key"] == "vector-secret"
    assert provider.embedding_client.arguments["base_url"] == "https://vectors.example/v1"
    assert asyncio.run(provider.embed(["教材片段"])) == [[0.1, 0.2]]
    assert provider.embedding_client.embedding_request == {"model": "BAAI/bge-m3", "input": ["教材片段"]}
    asyncio.run(provider.close())
    assert all(client.closed for client in clients)


def adapter(create):
    """Exercise the real adapter against explicit OpenAI-protocol responses."""
    provider = object.__new__(Provider)
    provider.settings = SimpleNamespace(api_key="test-only", model="test-model", api_base="https://api.siliconflow.cn/v1")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return provider


def json_tool_response(payload, *, model="test-model", finish_reason="tool_calls", content=None):
    class Usage:
        def model_dump(self):
            return {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}

    call = SimpleNamespace(function=SimpleNamespace(
        name="return_json_payload",
        arguments=json.dumps({"json_payload": json.dumps(payload, ensure_ascii=False)}, ensure_ascii=False),
    ))
    message = SimpleNamespace(content=content, tool_calls=[call])
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(choices=[choice], usage=Usage(), model=model)


def json_text_response(payload, *, model="test-model", finish_reason="stop"):
    class Usage:
        def model_dump(self):
            return {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13}

    message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), tool_calls=[])
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(choices=[choice], usage=Usage(), model=model)


class UpstreamBadRequest(Exception):
    status_code = 400


@pytest.mark.parametrize("finish_reason", ["tool_calls", "stop"])
def test_complete_uses_strict_function_call_fallback_when_json_object_is_rejected(finish_reason):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            assert kwargs["response_format"] == {"type": "json_object"}
            raise UpstreamBadRequest("private provider message must not escape")
        return json_tool_response({"ok": True}, finish_reason=finish_reason)

    result = asyncio.run(adapter(create).complete(
        [{"role": "user", "content": 'Return {"ok":true}'}], json_mode=True))
    assert len(seen) == 2
    assert "response_format" not in seen[1]
    assert seen[1]["tool_choice"] == {
        "type": "function", "function": {"name": "return_json_payload"}}
    assert result["content"] == '{"ok": true}'
    assert result["tool_calls"] == []
    assert result["model"] == "test-model"
    assert result["usage"]["total_tokens"] == 7
    assert result["json_transport"] == "function_call_fallback"


@pytest.mark.parametrize("transport", ["function_call_fallback", "plain_json_fallback"])
@pytest.mark.parametrize("finish_reason,reason", [("length", "output_truncated"), ("content_filter", "content_filtered")])
def test_paid_json_fallback_failures_keep_safe_usage_and_never_expose_payload(transport, finish_reason, reason):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if kwargs.get("response_format") or (transport == "plain_json_fallback" and kwargs.get("tools")):
            raise UpstreamBadRequest("private upstream body")
        if transport == "function_call_fallback":
            return json_tool_response({"private-content": "do not expose"}, finish_reason=finish_reason)
        return json_text_response({"private-content": "do not expose"}, finish_reason=finish_reason)

    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter(create).complete([{"role": "user", "content": "JSON"}], json_mode=True))
    diagnostic = caught.value.diagnostic
    assert diagnostic["reason"] == reason and diagnostic["json_transport"] == transport
    assert diagnostic["finish_reason"] == finish_reason and diagnostic["model"] == "test-model"
    assert diagnostic["usage"]["total_tokens"] == (7 if transport == "function_call_fallback" else 13)
    assert len(seen) == (2 if transport == "function_call_fallback" else 3)
    assert "private" not in repr(diagnostic) and "do not expose" not in str(caught.value)


@pytest.mark.parametrize("finish_reason", ["tool_calls", "stop"])
def test_stream_uses_non_streaming_strict_function_call_fallback(finish_reason):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            assert kwargs["stream"] is True
            raise UpstreamBadRequest("provider details are private")
        assert "stream" not in kwargs and "stream_options" not in kwargs
        return json_tool_response({"blocks": [{"text": "答案", "source_ids": [1]}]},
                                  model="stepfun", finish_reason=finish_reason)

    parts = asyncio.run(_collect_provider_stream(adapter(create)))
    assert len(seen) == 2
    assert "response_format" not in seen[1]
    assert parts[0] == {
        "text": '{"blocks": [{"text": "答案", "source_ids": [1]}]}',
        "model": "stepfun",
    }
    assert parts[1]["usage"]["total_tokens"] == 7


async def _collect_provider_stream(provider):
    return [part async for part in provider.stream(
        [{"role": "user", "content": "Return JSON"}], json_mode=True)]


def test_complete_uses_one_strict_plain_json_fallback_after_two_transport_rejections():
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if len(seen) <= 2:
            raise UpstreamBadRequest("private provider message")
        return json_text_response({"blocks": [{"text": "只依据来源回答", "source_ids": [4]}]})

    result = asyncio.run(adapter(create).complete(
        [{"role": "user", "content": 'Return {"blocks":[]}'}], json_mode=True))
    assert len(seen) == 3
    assert "response_format" not in seen[2]
    assert "tools" not in seen[2] and "tool_choice" not in seen[2]
    assert "JSON对象" in seen[2]["messages"][-1]["content"]
    assert result["content"] == '{"blocks": [{"text": "只依据来源回答", "source_ids": [4]}]}'
    assert result["json_transport"] == "plain_json_fallback"
    assert result["usage"]["total_tokens"] == 13


def test_stream_uses_one_non_streaming_plain_json_fallback_after_two_transport_rejections():
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if len(seen) <= 2:
            raise UpstreamBadRequest("private provider message")
        assert "stream" not in kwargs and "stream_options" not in kwargs
        return json_text_response({"blocks": [{"text": "答案", "source_ids": [1]}]}, model="qwen")

    parts = asyncio.run(_collect_provider_stream(adapter(create)))
    assert len(seen) == 3
    assert seen[0]["stream"] is True
    assert "tools" not in seen[2] and "response_format" not in seen[2]
    assert parts[0] == {"text": '{"blocks": [{"text": "答案", "source_ids": [1]}]}', "model": "qwen"}
    assert parts[1]["usage"]["total_tokens"] == 13


@pytest.mark.parametrize("valid_final", [True, False])
def test_invalid_function_envelope_recovers_only_through_strict_plain_json(valid_final):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise UpstreamBadRequest("private provider body")
        if len(seen) == 2:
            call = SimpleNamespace(function=SimpleNamespace(
                name="return_json_payload", arguments='{"wrong_field":true}'))
            response = json_tool_response({"unused": True})
            response.choices[0].message.tool_calls = [call]
            return response
        assert "tools" not in kwargs and "response_format" not in kwargs
        return json_text_response({"blocks": [{"text": "有依据的回答", "source_ids": [1]}]} if valid_final else "invalid object")

    if not valid_final:
        with pytest.raises(ProviderError, match="JSON纯文本回退未返回完整且唯一字段的JSON对象") as caught:
            asyncio.run(adapter(create).complete(
                [{"role": "user", "content": "Return JSON"}], json_mode=True))
        assert len(seen) == 3
        assert caught.value.diagnostic["usage"] == {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20}
        return
    result = asyncio.run(adapter(create).complete([{ "role": "user", "content": "Return JSON"}], json_mode=True))
    assert len(seen) == 3
    assert result["json_transport"] == "plain_json_fallback"
    assert json.loads(result["content"])["blocks"][0]["source_ids"] == [1]
    assert result["usage"] == {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20}


def test_plain_json_fallback_rejects_markdown():
    async def create(**kwargs):
        if "response_format" in kwargs or "tools" in kwargs:
            raise UpstreamBadRequest("private provider message")
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='```json\n{"ok":true}\n```', tool_calls=[]))],
            usage=None, model="test-model")

    with pytest.raises(ProviderError, match="JSON纯文本回退未返回完整且唯一字段的JSON对象"):
        asyncio.run(adapter(create).complete(
            [{"role": "user", "content": "Return JSON"}], json_mode=True))


@pytest.mark.parametrize("payload", [
    '{"ok":true,"ok":false}',
    '{"ok":NaN}',
    '[1,2,3]',
    "not JSON",
])
def test_json_function_fallback_rejects_non_object_duplicate_or_invalid_payload(payload):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise UpstreamBadRequest("private provider body")
        if len(seen) == 3:
            return json_text_response(payload)
        call = SimpleNamespace(function=SimpleNamespace(
            name="return_json_payload",
            arguments=json.dumps({"json_payload": payload}),
        ))
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="tool_calls", message=SimpleNamespace(content=None, tool_calls=[call]))],
            usage=None, model="test-model")

    with pytest.raises(ProviderError, match="JSON纯文本回退未返回完整且唯一字段的JSON对象"):
        asyncio.run(adapter(create).complete(
            [{"role": "user", "content": "Return JSON"}], json_mode=True))
    assert len(seen) == 3


def test_json_tool_fallback_is_not_used_for_non_400_or_when_disabled():
    class UpstreamServerError(Exception):
        status_code = 500

    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        raise UpstreamServerError("private provider body")

    provider = adapter(create)
    with pytest.raises(ProviderError, match="HTTP 500"):
        asyncio.run(provider.complete([{"role": "user", "content": "Return JSON"}], json_mode=True))
    provider.settings.json_mode_enabled = False
    with pytest.raises(ProviderError, match="HTTP 500"):
        asyncio.run(provider.complete([{"role": "user", "content": "Return JSON"}], json_mode=True))
    assert len(seen) == 2
    assert all("response_format" not in kwargs for kwargs in seen[1:])
    assert "private provider body" not in str(ProviderError("safe", {"model": "test-model"}))


def test_json_fallback_failure_reports_safe_transport_stage_only():
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        raise UpstreamBadRequest("private body includes request details")

    with pytest.raises(ProviderError, match="JSON纯文本回退请求失败（HTTP 400）") as caught:
        asyncio.run(adapter(create).complete(
            [{"role": "user", "content": "Return JSON"}], json_mode=True))
    assert len(seen) == 3
    assert caught.value.diagnostic["json_transport"] == "plain_json_fallback"
    assert caught.value.diagnostic["upstream_status"] == 400
    assert "private body" not in str(caught.value)


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_complete_rejects_incomplete_planning_or_quiz_output(reason):
    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content='{"questions":[]}', tool_calls=[]))])

    with pytest.raises(ProviderError, match="未完整生成|未完成"):
        asyncio.run(adapter(create).complete([{"role": "user", "content": "出三道题"}]))


@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_stream_rejects_incomplete_response_after_partial_tokens(reason):
    async def upstream():
        yield SimpleNamespace(choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content="已输出但尚未完成"))], usage=None, model="test-model")
        yield SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason, delta=SimpleNamespace(content=None))], usage=None, model="test-model")

    async def create(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["extra_body"]["enable_thinking"] is False
        return upstream()

    async def collect():
        parts = []
        with pytest.raises(ProviderError, match="阻止保存"):
            async for part in adapter(create).stream([{"role": "user", "content": "解释"}]):
                parts.append(part)
        assert parts == [{"text": "已输出但尚未完成", "model": "test-model"}]

    asyncio.run(collect())


@pytest.mark.parametrize("text", ['{"questions":', '模型结果：{"questions": [}', '```json\n{"questions": [}\n```', '没有 JSON 对象'])
def test_invalid_json_has_safe_consistent_provider_error(text):
    with pytest.raises(ProviderError, match="模型输出格式无效"):
        extract_json(text)


def test_json_wrapper_remains_supported():
    assert extract_json('```json\n{"questions": []}\n```') == {"questions": []}
    assert extract_json('结果如下：{"questions": []}。') == {"questions": []}


def test_vision_adapter_sends_actual_png_base64_to_configured_vision_model():
    import base64
    seen = []
    png = b"\x89PNG\r\n\x1a\nprotocol-test-bytes"

    async def create(**kwargs):
        seen.append(kwargs)
        value = {"regions": [{"position": "页中部", "visible_text": ["局部标注"], "visual_facts": ["两框间有一条箭头"]}],
                 "limitations": ["边缘文字未读清"]}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(value, ensure_ascii=False), tool_calls=[]))], usage=None, model="test-vision-model")

    provider = adapter(create)
    provider.settings.vision_enabled = True
    provider.settings.vision_model = "test-vision-model"
    result = asyncio.run(provider.interpret_image(png, "核对图示", "测试教材", 2))
    assert result["model"] == "test-vision-model" and provider.settings.model == "test-model"
    assert seen[0]["model"] == "test-vision-model"
    contents = seen[0]["messages"][1]["content"]
    encoded = next(item["image_url"]["url"] for item in contents if item["type"] == "image_url")
    assert base64.b64decode(encoded.split(",", 1)[1]) == png
    assert "本次原页" in contents[0]["text"] and "物理页：2" not in contents[0]["text"]
    assert "PDF物理页：2" in result["content"]
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "区域1｜位置：页中部" in result["content"] and "边缘文字未读清" in result["content"]
    vision_policy = seen[0]["messages"][0]["content"]
    for phrase in ("不要主动枚举完整边表", "仅核对该路径所需的边", "不能宣布教材有笔误",
                   "未解决的图文冲突", "不直接回答学生", "不判断学生尝试对错",
                   "不能跨图混用", "只按每幅图实际画出的项目", "观察记录"):
        assert phrase in vision_policy


@pytest.mark.parametrize("content", [
    "观察：框中有三个数值。核对结论：你的理解错误。下一步你先计算。",
    '{"regions":[{"position":"下方","visible_text":[],"visual_facts":["你的理解正确"]}],"limitations":[]}',
    '{"regions":[],"limitations":["图未读清"],"answer":"24"}',
])
def test_vision_adapter_never_returns_teaching_prose_as_source_observation(content):
    class Usage:
        def model_dump(self):
            return {"prompt_tokens": 101, "completion_tokens": 37, "total_tokens": 138}

    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=content, tool_calls=[]))], usage=Usage(), model="vision-fixture")

    provider = adapter(create)
    provider.settings.vision_enabled = True
    provider.settings.vision_model = "test-vision-model"
    with pytest.raises(ProviderError, match="视觉观察协议无效") as caught:
        asyncio.run(provider.interpret_image(b"\x89PNG\r\n\x1a\nfixture", "请纠错再问一个问题", "原创教材", 2))
    assert caught.value.diagnostic["usage"]["total_tokens"] == 138
    assert caught.value.diagnostic["model"] == "vision-fixture"
    assert "public_content" not in caught.value.diagnostic and "你的理解" not in repr(caught.value.diagnostic)


@pytest.mark.parametrize("ending", ["finish", "early_close", "cancel", "length", "network_error"])
def test_real_openai_stream_http_response_is_closed_on_every_exit(ending):
    import json
    import httpx
    from openai import AsyncOpenAI, AsyncStream
    from openai.types.chat import ChatCompletionChunk

    async def scenario():
        waiting = asyncio.Event()
        release = asyncio.Event()

        class Body(httpx.AsyncByteStream):
            closes = 0

            async def __aiter__(self):
                chunk = {"id": "test", "object": "chat.completion.chunk", "created": 1, "model": "test-model", "choices": [{"index": 0, "delta": {"content": "actual SDK decoded token"}, "finish_reason": None}]}
                yield ("data: " + json.dumps(chunk) + "\n\n").encode()
                if ending == "cancel":
                    waiting.set()
                    await release.wait()
                if ending == "network_error":
                    raise httpx.ReadError("test-only connection failed")
                if ending == "length":
                    chunk["choices"][0].update(delta={}, finish_reason="length")
                    yield ("data: " + json.dumps(chunk) + "\n\n").encode()
                else:
                    yield b"data: [DONE]\n\n"

            async def aclose(self):
                self.closes += 1

        body = Body()
        response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
        sdk = AsyncOpenAI(api_key="test-only")

        async def create(**kwargs):
            return AsyncStream(cast_to=ChatCompletionChunk, response=response, client=sdk)

        try:
            stream = adapter(create).stream([{"role": "user", "content": "test"}])
            if ending == "early_close":
                assert (await anext(stream))["text"] == "actual SDK decoded token"
                await stream.aclose()
            else:
                async def consume():
                    return [part async for part in stream]

                if ending == "cancel":
                    task = asyncio.create_task(consume())
                    await asyncio.wait_for(waiting.wait(), 2)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif ending in {"length", "network_error"}:
                    with pytest.raises(ProviderError):
                        await consume()
                else:
                    assert (await consume())[0]["text"] == "actual SDK decoded token"
            assert response.is_closed and body.closes == 1
        finally:
            await sdk.close()

    asyncio.run(scenario())


def test_stream_cumulative_usage_is_counted_once_and_token_content_is_preserved():
    class Usage:
        def __init__(self, completion):
            self.completion = completion

        def model_dump(self):
            return {"prompt_tokens": 17, "completion_tokens": self.completion, "total_tokens": 17 + self.completion}

    async def upstream():
        for text, completion in [("静", 0), ("力", 1), ("学", 32), (None, 32), (None, 32)]:
            yield SimpleNamespace(choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content=text))], usage=Usage(completion), model="test-model")
        yield SimpleNamespace(choices=[], usage=None, model="test-model")

    async def create(**kwargs):
        return upstream()

    async def collect():
        return [part async for part in adapter(create).stream([{"role": "user", "content": "test"}])]

    parts = asyncio.run(collect())
    assert "".join(part.get("text", "") for part in parts) == "静力学"
    usages = [part["usage"] for part in parts if part.get("usage")]
    assert usages == [{"prompt_tokens": 17, "completion_tokens": 32, "total_tokens": 49}]


class TokenUsage:
    def __init__(self, completion):
        self.completion = completion

    def model_dump(self):
        return {"prompt_tokens": 13, "completion_tokens": self.completion, "total_tokens": 13 + self.completion,
                "completion_tokens_details": {"reasoning_tokens": 7, "private_content": "private-reasoning"},
                "untrusted_extra": "private-api-key"}


@pytest.mark.parametrize("reason,code", [("length", "output_truncated"), ("content_filter", "content_filtered")])
def test_complete_failure_preserves_safe_usage_without_retaining_answer_or_reasoning(reason, code):
    async def create(**kwargs):
        message = SimpleNamespace(content="private-textbook-answer", reasoning_content="private-reasoning", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason, message=message)],
                               model="actual-model", usage=TokenUsage(1100))

    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter(create).complete([{"role": "user", "content": "private-request"}], max_tokens=1100))
    diagnostic = caught.value.diagnostic
    assert diagnostic == {"model": "actual-model", "finish_reason": reason,
                          "usage": {"prompt_tokens": 13, "completion_tokens": 1100, "total_tokens": 1113,
                                    "completion_tokens_details": {"reasoning_tokens": 7}},
                          "content_characters": len("private-textbook-answer"), "reason": code}
    assert caught.value.metadata is diagnostic
    assert all(private not in repr(diagnostic) + str(caught.value) for private in (
        "private-textbook-answer", "private-reasoning", "private-api-key", "private-request"))


@pytest.mark.parametrize("ending", ["length", "content_filter", "network_error"])
def test_stream_failure_preserves_latest_usage_including_termination_chunk_and_closes(ending):
    class Stream:
        closed = False

        async def __aiter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content="private-answer"))],
                                  usage=TokenUsage(20), model="actual-stream-model")
            if ending == "network_error":
                raise RuntimeError("private-upstream-body")
            yield SimpleNamespace(choices=[SimpleNamespace(finish_reason=ending, delta=SimpleNamespace(content=None))],
                                  usage=TokenUsage(3200), model="actual-stream-model")

        async def close(self):
            self.closed = True

    upstream = Stream()

    async def create(**kwargs):
        return upstream

    async def collect():
        delivered = []
        with pytest.raises(ProviderError) as caught:
            async for part in adapter(create).stream([{"role": "user", "content": "private-request"}]):
                delivered.append(part)
        assert upstream.closed
        assert delivered == [{"text": "private-answer", "model": "actual-stream-model"}]
        return caught.value

    error = asyncio.run(collect())
    completion = 20 if ending == "network_error" else 3200
    assert error.diagnostic["usage"] == {"prompt_tokens": 13, "completion_tokens": completion,
                                         "total_tokens": 13 + completion,
                                         "completion_tokens_details": {"reasoning_tokens": 7}}
    assert error.diagnostic["model"] == "actual-stream-model"
    assert error.diagnostic["content_characters"] == len("private-answer")
    assert error.diagnostic["finish_reason"] == (None if ending == "network_error" else ending)
    assert all(private not in repr(error.diagnostic) + str(error) for private in (
        "private-answer", "private-reasoning", "private-api-key", "private-upstream-body", "private-request"))


def test_diagnostic_allowlist_rejects_unknown_fields_and_invalid_counters():
    error = ProviderError("safe", {"model": "model with private text", "finish_reason": {"private": "secret"},
                                  "usage": {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "private-key",
                                            "prompt_cache_hit_tokens": 5, "prompt_tokens_details": {"cached_tokens": 3, "raw": "secret"}},
                                  "content_characters": float("inf"), "reason": {"raw": "secret"},
                                  "api_key": "private-api-key", "body": "private-upstream-body"})
    assert error.diagnostic == {"finish_reason": "unknown", "usage": {"prompt_cache_hit_tokens": 5,
                                                                         "prompt_tokens_details": {"cached_tokens": 3}}}


def test_upstream_http_error_diagnostic_does_not_include_raw_body():
    class UpstreamError(RuntimeError):
        status_code = 429
        body = "private-response-body"

    async def create(**kwargs):
        raise UpstreamError("private-api-key")

    with pytest.raises(ProviderError, match="HTTP 429") as caught:
        asyncio.run(adapter(create).complete([{"role": "user", "content": "private-request"}]))
    assert caught.value.diagnostic == {"model": "test-model", "usage": {}, "content_characters": 0,
                                       "reason": "upstream_http_error", "upstream_status": 429}
    assert "private" not in str(caught.value) + repr(caught.value.diagnostic)


@pytest.mark.parametrize("model,tools,temperature,top_p", [
    ("stepfun-ai/Step-3.5-Flash", None, 0.6, 0.95),
    ("stepfun-ai/Step-3.5-Flash", [{"type": "function"}], 1.0, 0.95),
    ("stepfun-ai/Step-3.5-Flash-Base", None, 0.25, None),
    ("deepseek-ai/DeepSeek-V4-Flash", [{"type": "function"}], 0.25, None),
])
def test_complete_uses_exact_step_model_sampling_and_budget(model, tools, temperature, top_p):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK", tool_calls=[]))],
                               usage=None, model=model)

    provider = adapter(create)
    provider.settings.model = model
    assert asyncio.run(provider.complete([{"role": "user", "content": "test"}], tools=tools, max_tokens=1800))["content"] == "OK"
    assert seen[0]["temperature"] == temperature
    assert seen[0].get("top_p") == top_p
    assert seen[0]["max_tokens"] == (8192 if model == "stepfun-ai/Step-3.5-Flash" else 1800)


@pytest.mark.parametrize("model,temperature,top_p", [
    ("stepfun-ai/Step-3.5-Flash", 0.6, 0.95),
    ("test-model", 0.25, None),
])
def test_stream_uses_model_sampling_and_budget(model, temperature, top_p):
    seen = []

    async def upstream():
        yield SimpleNamespace(choices=[], usage=None, model=model)

    async def create(**kwargs):
        seen.append(kwargs)
        return upstream()

    provider = adapter(create)
    provider.settings.model = model

    async def collect():
        return [part async for part in provider.stream([{"role": "user", "content": "test"}])]

    assert asyncio.run(collect()) == []
    assert seen[0]["temperature"] == temperature and seen[0].get("top_p") == top_p
    assert seen[0]["max_tokens"] == (8192 if model == "stepfun-ai/Step-3.5-Flash" else 3200)


def test_explicit_vision_model_override_keeps_its_own_sampling_when_main_model_is_step():
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK", tool_calls=[]))],
                               usage=None, model="Qwen/Qwen3-VL-32B-Instruct")

    provider = adapter(create)
    provider.settings.model = "stepfun-ai/Step-3.5-Flash"
    asyncio.run(provider.complete([{"role": "user", "content": "test"}], model="Qwen/Qwen3-VL-32B-Instruct", max_tokens=1800))
    assert seen[0]["temperature"] == 0.25 and "top_p" not in seen[0]
    assert seen[0]["max_tokens"] == 1800


@pytest.mark.parametrize("mode", ["complete", "stream"])
@pytest.mark.parametrize("model,minimum,requested,expected", [
    ("stepfun-ai/Step-3.5-Flash", 0, 1100, 8192),
    ("stepfun-ai/Step-3.5-Flash", 0, 16384, 16384),
    ("stepfun-ai/Step-3.5-Flash", 2048, 1100, 2048),
    ("stepfun-ai/Step-3.5-Flash", 32768, 4200, 32768),
    ("test-model", 0, 4200, 4200),
    ("test-model", 8192, 4200, 8192),
    ("test-model", 2048, 4200, 4200),
])
def test_output_budget_actual_requests_match_across_stream_and_complete(mode, model, minimum, requested, expected):
    seen = []

    async def upstream():
        yield SimpleNamespace(choices=[], usage=None, model=model)

    async def create(**kwargs):
        seen.append(kwargs)
        if kwargs.get("stream"):
            return upstream()
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK", tool_calls=[]))],
                               usage=None, model=model)

    provider = adapter(create)
    provider.settings.model = model
    provider.settings.min_output_tokens = minimum

    async def request():
        if mode == "stream":
            return [part async for part in provider.stream([{"role": "user", "content": "test"}], max_tokens=requested)]
        return await provider.complete([{"role": "user", "content": "test"}], max_tokens=requested)

    asyncio.run(request())
    assert seen[0]["max_tokens"] == expected


def test_explicit_global_budget_override_applies_to_actual_vision_model_without_step_default():
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"regions":[],"limitations":["标注未读清"]}', tool_calls=[]))],
                               usage=None, model="Qwen/Qwen3-VL-32B-Instruct")

    provider = adapter(create)
    provider.settings.model = "stepfun-ai/Step-3.5-Flash"
    provider.settings.min_output_tokens = 4096
    provider.settings.vision_enabled = True
    provider.settings.vision_model = "Qwen/Qwen3-VL-32B-Instruct"
    asyncio.run(provider.interpret_image(b"\x89PNG\r\n\x1a\nprotocol-test-bytes", "核对图示", "测试教材", 2))
    assert seen[0]["model"] == "Qwen/Qwen3-VL-32B-Instruct"
    assert seen[0]["max_tokens"] == 4096 and seen[0]["temperature"] == 0.25


@pytest.mark.parametrize("mode", ["complete", "stream"])
@pytest.mark.parametrize("requested", [0, -1, 32769, True, float("inf"), float("nan"), "8192"])
def test_invalid_output_budgets_are_rejected_before_upstream_call(mode, requested):
    async def create(**kwargs):
        pytest.fail("invalid budget must not reach upstream")

    provider = adapter(create)

    async def request():
        if mode == "stream":
            return [part async for part in provider.stream([{"role": "user", "content": "test"}], max_tokens=requested)]
        return await provider.complete([{"role": "user", "content": "test"}], max_tokens=requested)

    with pytest.raises(ProviderError, match="预算须为"):
        asyncio.run(request())


def test_invalid_global_minimum_does_not_reach_upstream():
    async def create(**kwargs):
        pytest.fail("invalid configured minimum must not reach upstream")

    provider = adapter(create)
    provider.settings.min_output_tokens = float("inf")
    with pytest.raises(ProviderError, match="预算下限"):
        asyncio.run(provider.complete([{"role": "user", "content": "test"}]))


def test_local_memory_failure_is_not_reported_as_network_or_credential_failure():
    async def create(**kwargs):
        raise MemoryError("private upstream body with token must never escape")

    with pytest.raises(ProviderError, match="本机可分配内存不足") as caught:
        asyncio.run(adapter(create).complete([{"role": "user", "content": "test"}]))
    assert caught.value.diagnostic["reason"] == "local_memory_error"
    assert "token" not in str(caught.value)
    assert "凭证" not in str(caught.value)
