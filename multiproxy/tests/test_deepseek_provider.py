"""Tests for the DeepSeek provider (call_deepseek_direct/call_deepseek_streaming). DeepSeek is
OpenAI-compatible with no param whitelist/blocklist (confirmed live 2026-09-16 -- reasoning_effort
and thinking={"type": "disabled"} both accepted unfiltered), the same shape OpenAI's own provider
already uses -- so this mirrors test_gemini_provider.py/test_kilo_provider.py's mocking shape but
without any param-stripping tests, since there's nothing to strip here.

Real shapes throughout, captured live 2026-09-16 against a real DeepSeek API key: the real model
catalog (deepseek-flash, deepseek-v4-pro -- NOT deepseek-chat/deepseek-reasoner, this project's own
prior, now-stale knowledge), and the real `reasoning_content` field both models emit alongside
`content` by default (reasoning tokens count against max_tokens/completion_tokens -- confirmed live
that a tiny max_tokens can consume the whole budget on reasoning, leaving `content: ""`, never a
missing key, so the shared helper's existing `.get("content") or ""` fallback already covers it
without any DeepSeek-specific change). See TECHNICAL.md's own "DeepSeek reasoning_content" note for
the full narrative, including the streaming quirk (delta.content stays null through the whole
reasoning phase, then carries the real answer afterward) not exercised directly here since the
shared streaming helper is a raw SSE passthrough with no per-field parsing to test.
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


@pytest.mark.asyncio
async def test_direct_no_key_raises(proxy_module):
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 100)
    assert exc.value.status_code == 500
    assert "DeepSeek" in exc.value.detail


@pytest.mark.asyncio
async def test_direct_sends_bearer_auth(proxy_module):
    resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 100)

    hdrs = session.post.call_args.kwargs["headers"]
    assert hdrs["Authorization"] == "Bearer sk-test-key"


@pytest.mark.asyncio
async def test_direct_posts_to_the_real_endpoint(proxy_module):
    resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 100)

    url = session.post.call_args.args[0]
    assert url == "https://api.deepseek.com/chat/completions"


@pytest.mark.asyncio
async def test_direct_uses_plain_max_tokens(proxy_module):
    """Unlike OpenAI (max_completion_tokens), DeepSeek accepts plain max_tokens -- confirmed live."""
    resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 150)

    payload = session.post.call_args.kwargs["json"]
    assert payload["max_tokens"] == 150
    assert "max_completion_tokens" not in payload


@pytest.mark.asyncio
async def test_direct_parses_a_real_recorded_response_with_reasoning_content(proxy_module):
    """Real shape captured live 2026-09-16: DeepSeek emits `reasoning_content` alongside `content`
    by default, and `completion_tokens_details.reasoning_tokens` in usage. The shared helper only
    reads `message["content"]` and passes the whole `usage` dict through -- reasoning_content is
    simply ignored (never surfaced to SkyrimNet), which is the correct behavior for a text reply."""
    real_output = {
        "id": "3528aba8-c13a-4835-8f58-4b3b64e54740",
        "object": "chat.completion",
        "model": "deepseek-flash",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "pong", "reasoning_content": "We need answer only single word: pong. Must comply."},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 43, "completion_tokens": 16, "total_tokens": 59,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 13},
        },
    }
    resp = _make_mock_response(200, real_output)
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        text, usage = await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 50)

    assert text == "pong"
    assert usage["prompt_tokens"] == 43
    assert usage["completion_tokens"] == 16
    assert usage["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_direct_survives_reasoning_consuming_the_whole_budget(proxy_module):
    """Real shape captured live 2026-09-16: a tiny max_tokens can be entirely consumed by
    deepseek-v4-pro's own reasoning, leaving `content: ""` (an empty string, NOT a missing key --
    unlike the real Gemini bug this shared helper was originally fixed for). Must degrade to an
    empty string, never crash."""
    real_output = {
        "id": "320c92fc-c3f9-4825-ad1a-fce30db0f3c6",
        "object": "chat.completion",
        "model": "deepseek-v4-pro",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "", "reasoning_content": "We need answer"},
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 89, "completion_tokens": 3, "total_tokens": 92},
    }
    resp = _make_mock_response(200, real_output)
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        text, usage = await proxy_module.call_deepseek_direct(
            None, [{"role": "user", "content": "Explain quantum computing in detail."}], "deepseek-v4-pro", 3,
        )

    assert text == ""
    assert usage["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_direct_passes_reasoning_effort_through_unfiltered(proxy_module):
    """No param blocklist/whitelist here -- confirmed live reasoning_effort is honored by the real
    API, so it must reach the payload untouched, the same "pass extra params through unfiltered"
    behavior OpenAI's own provider already has."""
    resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
    session = _make_mock_session(resp)

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.call_deepseek_direct(
            None, [{"role": "user", "content": "hi"}], "deepseek-v4-pro", 50,
            reasoning_effort="low", thinking={"type": "disabled"},
        )

    payload = session.post.call_args.kwargs["json"]
    assert payload["reasoning_effort"] == "low"
    assert payload["thinking"] == {"type": "disabled"}


class TestReasoningDefaultsOff:
    """2026-09-22: reasoning tokens count against max_tokens/completion_tokens on both DeepSeek
    models (TECHNICAL.md) -- confirmed live against the director's own multiproxy.log, a small
    max_tokens (several of SkyrimNet's own variants use ones as low as 100) got entirely consumed
    by hidden thinking, leaving content: "" and a clean-but-unhelpful 500 "Empty response". Fixed
    the same way GLM already defaults thinking off (_build_glm_payload) -- DeepSeek never rejects
    `thinking: {"type": "disabled"}` (TECHNICAL.md, live-verified), so unlike GLM 5.3+ this needs
    no retry/reasoning_effort fallback."""

    @pytest.mark.asyncio
    async def test_direct_defaults_thinking_disabled(self, proxy_module):
        resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
        session = _make_mock_session(resp)

        with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 100)

        payload = session.post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_streaming_defaults_thinking_disabled(self, proxy_module):
        sse_bytes = b'data: {"choices":[{"delta":{"content":"pong"},"index":0}]}\n\ndata: [DONE]\n\n'
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

        with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            async for _chunk in proxy_module.call_deepseek_streaming(
                None, [{"role": "user", "content": "hi"}], "deepseek-flash", 100,
            ):
                pass

        payload = session.post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_direct_does_not_override_an_explicit_thinking_config(self, proxy_module):
        """An explicit caller-supplied `thinking` is honored as-is, never overridden -- same
        contract _build_glm_payload already has for GLM."""
        resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
        session = _make_mock_session(resp)

        with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            await proxy_module.call_deepseek_direct(
                None, [{"role": "user", "content": "hi"}], "deepseek-v4-pro", 500,
                thinking={"type": "enabled"},
            )

        payload = session.post.call_args.kwargs["json"]
        assert payload["thinking"] == {"type": "enabled"}

    @pytest.mark.asyncio
    async def test_direct_does_not_inject_thinking_when_caller_set_reasoning_effort_alone(self, proxy_module):
        """Same OR-based check GLM's own _wants_own_thinking_config already uses -- an explicit
        reasoning_effort alone (no `thinking` field at all) must also suppress the default,
        rather than both fields ending up in the payload together."""
        resp = _make_mock_response(200, {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}]})
        session = _make_mock_session(resp)

        with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            await proxy_module.call_deepseek_direct(
                None, [{"role": "user", "content": "hi"}], "deepseek-v4-pro", 500,
                reasoning_effort="low",
            )

        payload = session.post.call_args.kwargs["json"]
        assert payload["reasoning_effort"] == "low"
        assert "thinking" not in payload


@pytest.mark.asyncio
async def test_streaming_translates_sse_chunks(proxy_module):
    """Real shape confirmed live 2026-09-16: DeepSeek's own SSE stream is already standard OpenAI
    chat.completion.chunk events -- the shared streaming helper needs zero special handling to pass
    them through, same as Gemini/Kilo/OpenAI."""
    sse_bytes = b'data: {"choices":[{"delta":{"content":"pong"},"index":0}]}\n\ndata: [DONE]\n\n'
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

    with patch.object(proxy_module, "deepseek_api_key", "sk-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks = [
            chunk
            async for chunk in proxy_module.call_deepseek_streaming(
                None, [{"role": "user", "content": "Say pong."}], "deepseek-flash", 100,
            )
        ]

    combined = "".join(chunks)
    assert "pong" in combined
    assert "data: [DONE]" in combined
