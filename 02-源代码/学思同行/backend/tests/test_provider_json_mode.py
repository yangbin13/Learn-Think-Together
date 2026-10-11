import asyncio
from types import SimpleNamespace

import pytest

from backend.app.provider import ProviderError
from backend.tests.test_provider_boundaries import adapter


@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("tools", [None, [{"type": "function"}]])
def test_mimo_complete_sends_explicit_thinking_policy_including_planner_calls(thinking, tools):
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{}', tool_calls=[]))], usage=None, model="mimo-v2.6-flash")

    provider = adapter(create)
    provider.settings.model = "mimo-v2.6-flash"
    provider.settings.api_base = "https://token-plan-cn.xiaomimimo.com/v1"
    asyncio.run(provider.complete([{"role": "user", "content": "test"}], thinking=thinking, tools=tools))
    assert requests[0]["extra_body"] == {"thinking": {"type": "enabled" if thinking else "disabled"}}


@pytest.mark.parametrize("host", ["api.xiaomimimo.com", "token-plan-cn.xiaomimimo.com", "api.xiaomimimo.com.example.invalid"])
def test_mimo_stream_disables_thinking_only_for_the_official_endpoint(host):
    requests = []

    async def upstream():
        yield SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", delta=SimpleNamespace(content='{}'))], usage=None, model="mimo-v2.6-flash")

    async def create(**kwargs):
        requests.append(kwargs)
        return upstream()

    provider = adapter(create)
    provider.settings.api_base = f"https://{host}/v1"
    async def collect():
        return [part async for part in provider.stream([{"role": "user", "content": "test"}], json_mode=True)]
    asyncio.run(collect())
    if host.endswith("example.invalid"):
        assert "extra_body" not in requests[0]
    else:
        assert requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_json_mode_is_explicit_and_has_no_implicit_paid_fallback():
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"value":1}', tool_calls=[]))], usage=None, model="test-model")

    provider = adapter(create)
    asyncio.run(provider.complete([{"role": "user", "content": "JSON"}], json_mode=True))
    asyncio.run(provider.complete([{"role": "user", "content": "plain"}], thinking=True, thinking_budget=1024))
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]
    assert requests[0]["extra_body"]["enable_thinking"] is False
    assert requests[1]["extra_body"]["enable_thinking"] is True
    assert requests[1]["extra_body"]["thinking_budget"] == 1024
    provider.settings.json_mode_enabled = False
    asyncio.run(provider.complete([{"role": "user", "content": "JSON"}], json_mode=True))
    assert "response_format" not in requests[2]


def test_unsupported_json_mode_fails_after_one_provider_request():
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        raise RuntimeError("unsupported response format")

    with pytest.raises(ProviderError):
        asyncio.run(adapter(create).complete([{"role": "user", "content": "JSON"}], json_mode=True))
    assert len(requests) == 1


def test_stream_json_mode_is_applied_only_when_enabled():
    requests = []

    async def upstream():
        yield SimpleNamespace(choices=[], usage=None, model="test-model")

    async def create(**kwargs):
        requests.append(kwargs)
        return upstream()

    provider = adapter(create)

    async def collect(json_mode):
        return [part async for part in provider.stream([{"role": "user", "content": "JSON"}], json_mode=json_mode)]

    asyncio.run(collect(json_mode=True))
    assert requests[0]["response_format"] == {"type": "json_object"}
    provider.settings.json_mode_enabled = False
    asyncio.run(collect(json_mode=True))
    assert "response_format" not in requests[1]


def test_json_mode_never_conflates_tool_calls_with_teaching_body():
    async def create(**kwargs):
        raise AssertionError("tool planning and JSON body must be separate calls")

    with pytest.raises(ProviderError, match="不能同时请求工具规划"):
        asyncio.run(adapter(create).complete([{"role": "user", "content": "JSON"}], tools=[{"type": "function"}], json_mode=True))
