"""Tests for how a streaming failure is reported -- reworked 2026-09-27 for SkyrimNet Beta 25.

History: on 2026-09-16 every streaming provider learned to catch a mid-stream surprise and turn it into
a readable error CHUNK plus a clean [DONE], because an uncaught exception after SSE headers are sent
truncates the body (curl "Transferred a partial file"). That fixed truncation, but it put error words
inside a "200 OK" reply: SkyrimNet's Beta 25 smart routing can't tell that failed (it looks like a
success), and an NPC could even speak the error text.

Now, every provider streaming generator simply RAISES on failure, and the route decides what that
means: before the first event a real HTTP error (right status, normal {"error": {...}} body); after it
the stream just ends cleanly with `data: [DONE]` and no error text. This file covers both layers --
the provider generators (they raise, after yielding whatever real text already went out), and the
end-to-end behavior through /v1/chat/completions.

Mocking shape mirrors test_gemini_provider.py/test_kilo_provider.py: a fake response usable as an async
context manager, a fake session whose .post() returns it, content.iter_any() as an async generator
(_async_bytes_iter_then_raise appends a forced failure after some real content).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi import HTTPException

PARTIAL = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'


async def _async_bytes_iter_then_raise(chunks, exc):
    for chunk in chunks:
        yield chunk
    raise exc


def _make_mock_streaming_response(chunks, exc):
    mock_content = MagicMock()
    mock_content.iter_any.return_value = _async_bytes_iter_then_raise(chunks, exc)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.content = mock_content
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


async def _drain(gen):
    """(chunks yielded, the exception the generator ended with or None)."""
    chunks, error = [], None
    try:
        async for chunk in gen:
            chunks.append(chunk)
    except Exception as e:  # noqa: BLE001 -- the point is to see what comes out
        error = e
    return chunks, error


def _assert_raised(error, status, *, contains):
    assert isinstance(error, HTTPException), f"expected an HTTPException, got {error!r}"
    assert error.status_code == status
    assert contains in error.detail


# ---- Provider generators raise (they never write error wording into the reply) ----

@pytest.mark.asyncio
async def test_gemini_unexpected_error_raises_after_partial_text(proxy_module):
    """Shared OpenAI-compatible helper (Gemini/OpenAI/Kilo Gateway/Ollama/DeepSeek): a surprise
    mid-stream raises a clean 500 -- the text that already went out is left exactly as it was."""
    resp = _make_mock_streaming_response([PARTIAL], ValueError("a real surprise, not a connection failure"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, "gemini_api_key", "gemini-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks, error = await _drain(proxy_module.call_gemini_streaming(
            None, [{"role": "user", "content": "hi"}], "gemini-3.8-flash", 100,
        ))
    assert "partial" in "".join(chunks)
    assert "error" not in "".join(chunks).lower()
    _assert_raised(error, 500, contains="Something went wrong in MultiProxy")


@pytest.mark.asyncio
async def test_kilo_unexpected_error_raises_before_any_text(proxy_module):
    resp = _make_mock_streaming_response([], RuntimeError("a real surprise"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, "kilo_api_key", "kilo-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks, error = await _drain(proxy_module.call_kilo_streaming(
            None, [{"role": "user", "content": "hi"}], "kilo-auto/balanced", 100,
        ))
    assert chunks == []
    _assert_raised(error, 500, contains="Something went wrong in MultiProxy")


@pytest.mark.asyncio
async def test_openai_compatible_connection_error_raises_a_502(proxy_module):
    resp = _make_mock_streaming_response([], aiohttp.ClientConnectionError("connection reset"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, "gemini_api_key", "gemini-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks, error = await _drain(proxy_module.call_gemini_streaming(
            None, [{"role": "user", "content": "hi"}], "gemini-3.8-flash", 100,
        ))
    assert chunks == []
    _assert_raised(error, 502, contains="Couldn't reach Gemini")


@pytest.mark.asyncio
async def test_gemini_normal_reply_is_unaffected(proxy_module):
    """The happy path streams exactly as before -- nothing added, nothing changed."""
    sse_bytes = b'data: {"choices":[{"delta":{"content":"1, 2, 3"},"index":0}]}\n\ndata: [DONE]\n\n'
    mock_content = MagicMock()

    async def _iter(_chunks=[sse_bytes]):
        for c in _chunks:
            yield c

    mock_content.iter_any.return_value = _iter()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.content = mock_content
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = _make_mock_session(mock_resp)
    with patch.object(proxy_module, "gemini_api_key", "gemini-test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks, error = await _drain(proxy_module.call_gemini_streaming(
            None, [{"role": "user", "content": "Count to 3."}], "gemini-3.8-flash", 100,
        ))
    assert error is None
    assert chunks == ['data: {"choices":[{"delta":{"content":"1, 2, 3"},"index":0}]}\n\n', "data: [DONE]\n\n"]


@pytest.mark.asyncio
async def test_missing_key_raises_instead_of_streaming_an_error(proxy_module):
    for fn, model in (
        (proxy_module.call_glm_streaming, "glm-4.6"),
        (proxy_module.call_nano_streaming, "some-model"),
        (proxy_module.call_deepseek_streaming, "deepseek-flash"),
    ):
        chunks, error = await _drain(fn(None, [{"role": "user", "content": "hi"}], model, 100))
        assert chunks == []
        _assert_raised(error, 500, contains="Add one on the Providers tab.")


# ---- GLM / Nano-GPT (dedicated handlers) ----

@pytest.mark.asyncio
@pytest.mark.parametrize("fn_name,key_attr,model,label", [
    ("call_glm_streaming", "glm_api_key", "glm-4.6", "GLM"),
    ("call_nano_streaming", "nano_api_key", "some-model", "Nano-GPT"),
])
async def test_dedicated_handlers_raise_502_on_a_connection_error(proxy_module, fn_name, key_attr, model, label):
    resp = _make_mock_streaming_response([], aiohttp.ClientConnectionError("connection reset"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, key_attr, "test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks, error = await _drain(getattr(proxy_module, fn_name)(
            None, [{"role": "user", "content": "hi"}], model, 100,
        ))
    assert chunks == []
    _assert_raised(error, 502, contains=f"Couldn't reach {label}")


@pytest.mark.asyncio
@pytest.mark.parametrize("fn_name,key_attr,model", [
    ("call_glm_streaming", "glm_api_key", "glm-4.6"),
    ("call_nano_streaming", "nano_api_key", "some-model"),
])
async def test_dedicated_handlers_raise_500_on_a_surprise_after_partial_text(proxy_module, fn_name, key_attr, model):
    resp = _make_mock_streaming_response([PARTIAL], ValueError("a real surprise"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, key_attr, "test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        chunks, error = await _drain(getattr(proxy_module, fn_name)(
            None, [{"role": "user", "content": "hi"}], model, 100,
        ))
    assert "partial" in "".join(chunks)
    assert "error" not in "".join(chunks).lower()
    _assert_raised(error, 500, contains="Something went wrong in MultiProxy")
    session.post.assert_called_once()  # must not spin any retry loop after a genuine exception


# ---- End to end through /v1/chat/completions ----

def _post(test_client, model):
    return test_client.post("/v1/chat/completions", json={
        "model": model, "messages": [{"role": "user", "content": "hi"}], "stream": True,
    })


def test_failure_before_any_text_is_a_real_http_error(proxy_module, test_client):
    resp = _make_mock_streaming_response([], aiohttp.ClientConnectionError("connection reset"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, "glm_api_key", "k"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        r = _post(test_client, proxy_module.GLM_PREFIX + "glm-4.6")
    assert r.status_code == 502
    body = r.json()
    assert "Couldn't reach GLM" in body["error"]["message"]
    assert "data:" not in r.text


def test_failure_after_some_text_ends_the_stream_cleanly_with_no_error_text(proxy_module, test_client):
    resp = _make_mock_streaming_response([PARTIAL], aiohttp.ClientConnectionError("connection reset"))
    session = _make_mock_session(resp)
    with patch.object(proxy_module, "glm_api_key", "k"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        r = _post(test_client, proxy_module.GLM_PREFIX + "glm-4.6")
    assert r.status_code == 200
    assert "partial" in r.text
    assert "error" not in r.text.lower() and "failed" not in r.text.lower() and "reach" not in r.text.lower()
    assert r.text.count("data: [DONE]") == 1
    assert r.text.rstrip().endswith("data: [DONE]")


def test_stream_that_ends_without_sending_anything_is_a_502(proxy_module, test_client, monkeypatch):
    async def empty(*a, **k):
        return
        yield  # pragma: no cover -- makes this an async generator

    monkeypatch.setattr(proxy_module, "call_glm_streaming", empty)
    r = _post(test_client, proxy_module.GLM_PREFIX + "glm-4.6")
    assert r.status_code == 502
    assert "sent back an empty reply" in r.json()["error"]["message"]
