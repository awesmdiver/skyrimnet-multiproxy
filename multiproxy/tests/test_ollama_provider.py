"""Tests for the Ollama provider (local daemon vs Ollama Cloud, call_ollama_direct/streaming).

Adapted from rhinos0608/skyrimnet-codex-proxy's own tests/test_ollama_reasoning_strip.py (which
tests a standalone pure `_ollama_payload_fixup` helper their project has) -- our own equivalent
logic lives inside the shared `_oai_compat_payload` helper (used by OpenAI/Kilo/Ollama alike), so
these tests exercise it through the real call functions instead of a matching standalone helper we
don't have. Also covers `_ollama_target` (local-vs-Cloud selection) and the two real,
already-found Ollama Cloud bugs ported from that reference project: the 32768 max_tokens cap and
synthesizing a missing `[DONE]`.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_mock_response(status=200, json_data=None):
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data or {})
    mock_resp.text = AsyncMock(return_value="")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


async def _async_bytes_iter(chunks):
    for chunk in chunks:
        yield chunk


class TestOllamaTarget:
    def test_defaults_to_local_daemon_with_no_key(self, proxy_module):
        base_url, api_key, require_key = proxy_module._ollama_target("llama3.1:8b")
        assert base_url == proxy_module.OLLAMA_LOCAL_BASE_URL
        assert api_key is None
        assert require_key is False

    def test_uses_cloud_when_key_configured(self, proxy_module):
        with patch.object(proxy_module, "ollama_api_key", "ollama-cloud-key"):
            base_url, api_key, require_key = proxy_module._ollama_target("llama3.1:8b")
        assert base_url == proxy_module.OLLAMA_CLOUD_BASE_URL
        assert api_key == "ollama-cloud-key"
        assert require_key is True


@pytest.mark.asyncio
async def test_ollama_direct_local_no_auth_header(proxy_module):
    """Local Ollama needs no key at all -- confirm no Authorization header is sent."""
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 100)

    url = session.post.call_args.args[0]
    hdrs = session.post.call_args.kwargs["headers"]
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in hdrs


@pytest.mark.asyncio
async def test_ollama_direct_cloud_sends_bearer_auth(proxy_module):
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "ollama_api_key", "ollama-cloud-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 100)

    url = session.post.call_args.args[0]
    hdrs = session.post.call_args.kwargs["headers"]
    assert url == "https://ollama.com/v1/chat/completions"
    assert hdrs["Authorization"] == "Bearer ollama-cloud-key"


@pytest.mark.asyncio
async def test_ollama_strips_thinking_and_top_k_and_takes_reasoning_effort_as_a_level(proxy_module):
    """Ollama strips `thinking` and `top_k` (skyrimnet-codex-proxy's own test_ollama_reasoning_strip.py). `reasoning_effort` low/medium/high is no longer stripped for a LOCAL
    Ollama (2026-10-20, SkyrimNet's six reasoning levels): Ollama's OpenAI route reads it (a real Ollama 0.40 turned thinking off with `none`, see thinking_off.py), so the
    player's level reaches the model. Ollama Cloud (a key) still has it stripped: see test_reasoning_levels.py."""
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_ollama_direct(
            None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 100,
            temperature=0.5, reasoning_effort="high", thinking={"x": 1}, top_k=40,
        )

    payload = session.post.call_args.kwargs["json"]
    assert payload["temperature"] == 0.5
    assert payload["reasoning_effort"] == "high"
    assert "thinking" not in payload
    assert "top_k" not in payload


@pytest.mark.asyncio
async def test_ollama_cloud_caps_max_tokens(proxy_module):
    """A real, already-found Ollama Cloud bug (skyrimnet-codex-proxy): sending an uncapped
    max_tokens can get silently clamped or rejected model-by-model. 32768 is the lowest common
    per-model cap across Ollama Cloud's hosted models."""
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "ollama_api_key", "ollama-cloud-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 999_999)

    payload = session.post.call_args.kwargs["json"]
    assert payload["max_tokens"] == proxy_module.OLLAMA_CLOUD_MAX_TOKENS_CAP


@pytest.mark.asyncio
async def test_ollama_local_does_not_cap_max_tokens(proxy_module):
    """The 32768 cap is an Ollama CLOUD-specific quirk -- a local daemon has no such external
    constraint and should receive the caller's real max_tokens unmodified."""
    resp = _make_mock_response(200, {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 999_999)

    payload = session.post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 999_999


@pytest.mark.asyncio
async def test_ollama_cloud_synthesizes_missing_done(proxy_module):
    """A real, already-found Ollama Cloud bug: the stream can drop mid-generation without ever
    sending `data: [DONE]`, which would otherwise hang SkyrimNet waiting for a terminator that never
    arrives. Only applies when synthesize_done=bool(api_key) is True -- i.e. Cloud, not local."""
    sse_bytes = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'  # no [DONE] at all
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

    with patch.object(proxy_module, "ollama_api_key", "ollama-cloud-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks = [
            chunk
            async for chunk in proxy_module.call_ollama_streaming(
                None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 100,
            )
        ]

    combined = "".join(chunks)
    assert "hello" in combined
    assert "[DONE]" in combined


@pytest.mark.asyncio
async def test_ollama_local_does_not_synthesize_done(proxy_module):
    """Local Ollama is well-behaved and always sends a real [DONE] -- confirm the synthesis logic
    doesn't fire (and so doesn't mask a genuinely missing terminator) when there's no key at all."""
    sse_bytes = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'  # no [DONE], deliberately
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

    with patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks = [
            chunk
            async for chunk in proxy_module.call_ollama_streaming(
                None, [{"role": "user", "content": "hi"}], "llama3.1:8b", 100,
            )
        ]

    combined = "".join(chunks)
    assert "hello" in combined
    assert "[DONE]" not in combined
