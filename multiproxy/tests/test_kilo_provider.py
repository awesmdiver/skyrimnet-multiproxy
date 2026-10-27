"""Tests for the Kilo Gateway provider (call_kilo_direct/call_kilo_streaming).

Adapted from rhinos0608/skyrimnet-codex-proxy's own tests/test_kilo_provider.py -- same aiohttp
mocking shape (a fake response usable as an async context manager, a fake session whose .post()
returns it), rewritten against our own module layout (a single multiproxy.py, not a proxy_internal
package) and our own model-id shape (kilo/provider/model, not kilo:provider/model).

Note: call_kilo_direct/streaming receive an ALREADY-STRIPPED model (chat_completions() strips the
kilo/ prefix before calling either function -- see the _ROUTES table) -- these tests pass a bare
gateway model id directly, matching how the dispatcher actually calls them.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_mock_response(status=200, json_data=None):
    """Build a mock aiohttp response usable as an async context manager."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.text = AsyncMock(return_value="")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_mock_session(resp):
    """Build a mock aiohttp session whose .post() returns the given response."""
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


async def _async_bytes_iter(chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_kilo_direct_no_key_raises(proxy_module):
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_kilo_direct(None, [{"role": "user", "content": "hi"}], "kilo-auto/balanced", 100)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_kilo_direct_sends_bearer_auth(proxy_module):
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "kilo_api_key", "kilo-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_kilo_direct(None, [{"role": "user", "content": "hi"}], "kilo-auto/balanced", 100)

    hdrs = session.post.call_args.kwargs["headers"]
    assert hdrs["Authorization"] == "Bearer kilo-test-key"


@pytest.mark.asyncio
async def test_kilo_direct_posts_to_gateway_endpoint(proxy_module):
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "kilo_api_key", "kilo-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_kilo_direct(None, [{"role": "user", "content": "hi"}], "kilo-auto/balanced", 100)

    url = session.post.call_args.args[0]
    assert url == "https://api.kilo.ai/api/gateway/chat/completions"


@pytest.mark.asyncio
async def test_kilo_direct_passes_raw_gateway_model_unchanged(proxy_module):
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "kilo_api_key", "kilo-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_kilo_direct(
            None, [{"role": "user", "content": "hi"}], "anthropic/claude-sonnet-5", 100,
        )

    payload = session.post.call_args.kwargs["json"]
    assert payload["model"] == "anthropic/claude-sonnet-5"


@pytest.mark.asyncio
async def test_kilo_direct_strips_unsupported_params_but_keeps_reasoning_effort(proxy_module):
    """thinking/reasoning/top_k are stripped; reasoning_effort survives -- confirmed against
    skyrimnet-codex-proxy's own test_kilo_provider.py, which explicitly asserts reasoning_effort is
    NOT stripped for Kilo (unlike Ollama -- see test_ollama_provider.py). Caught a real bug in this
    project's own first draft, which stripped all four identically for both providers."""
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "kilo_api_key", "kilo-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_kilo_direct(
            None, [{"role": "user", "content": "hi"}], "kilo-auto/balanced", 100,
            temperature=0.7,
            thinking={"budget_tokens": 16000},
            reasoning={"effort": "high"},
            reasoning_effort="medium",
            top_k=50,
        )

    payload = session.post.call_args.kwargs["json"]
    assert payload["temperature"] == 0.7
    assert payload["reasoning_effort"] == "medium"
    assert "thinking" not in payload
    assert "reasoning" not in payload
    assert "top_k" not in payload


@pytest.mark.asyncio
async def test_kilo_streaming_strips_unsupported_params(proxy_module):
    sse_bytes = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
    mock_content = MagicMock()
    mock_content.iter_any.return_value = _async_bytes_iter([sse_bytes])
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.content = mock_content
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=mock_resp)
    session.close = AsyncMock()

    with patch.object(proxy_module, "kilo_api_key", "kilo-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks = [
            chunk
            async for chunk in proxy_module.call_kilo_streaming(
                None, [{"role": "user", "content": "hi"}], "kilo-auto/balanced", 100,
                thinking={"budget_tokens": 16000}, reasoning_effort="medium", top_k=50,
            )
        ]

    combined = "".join(chunks)
    assert "hello" in combined
    payload = session.post.call_args.kwargs["json"]
    assert payload["reasoning_effort"] == "medium"
    assert "thinking" not in payload
    assert "top_k" not in payload
