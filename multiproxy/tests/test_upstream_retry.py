"""Tests for the dropped-connection retry (_post_with_retry) -- ported from Raydonn's fork
(2026-09-27). A fake session that drops the first attempt stands in for a stale keep-alive
connection; the retry must only ever apply while OPENING the request.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi import HTTPException


class _Ctx:
    """What `session.post(...)` returns: an async context manager that either fails to open with
    `exc`, or opens and hands back `resp`."""

    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc
        self.exited = False

    async def __aenter__(self):
        if self.exc is not None:
            raise self.exc
        return self.resp

    async def __aexit__(self, *a):
        self.exited = True
        return False


class _FakeSession:
    def __init__(self, ctxs):
        self._ctxs = list(ctxs)
        self.calls = []
        self.close = AsyncMock()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._ctxs.pop(0)


def _ok_resp(text="pong"):
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
    resp.text = AsyncMock(return_value="")
    return resp


def _dropped():
    return aiohttp.ServerDisconnectedError("Server disconnected")


@pytest.mark.asyncio
async def test_retries_a_dropped_first_attempt(proxy_module):
    good = _ok_resp()
    session = _FakeSession([_Ctx(exc=_dropped()), _Ctx(resp=good)])
    async with proxy_module._post_with_retry(session, "http://x/y", provider="Test", json={"a": 1}) as resp:
        assert resp is good
    assert len(session.calls) == 2
    assert session.calls[1] == ("http://x/y", {"json": {"a": 1}})


@pytest.mark.asyncio
async def test_passes_data_and_headers_through(proxy_module):
    session = _FakeSession([_Ctx(resp=_ok_resp())])
    async with proxy_module._post_with_retry(session, "http://x", provider="Test", data=b"raw", headers={"h": "1"}):
        pass
    assert session.calls[0][1] == {"data": b"raw", "headers": {"h": "1"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    aiohttp.ServerDisconnectedError("x"),
    aiohttp.ClientOSError("x"),
    aiohttp.ClientConnectionError("x"),
])
async def test_all_three_error_kinds_are_retried(proxy_module, exc):
    session = _FakeSession([_Ctx(exc=exc), _Ctx(resp=_ok_resp())])
    async with proxy_module._post_with_retry(session, "http://x", provider="Test"):
        pass
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_gives_up_after_three_attempts_and_raises_the_original_error(proxy_module):
    session = _FakeSession([_Ctx(exc=_dropped()) for _ in range(5)])
    with pytest.raises(aiohttp.ServerDisconnectedError):
        async with proxy_module._post_with_retry(session, "http://x", provider="Test"):
            pass
    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_a_timeout_is_not_retried(proxy_module):
    session = _FakeSession([_Ctx(exc=aiohttp.ServerTimeoutError("slow")), _Ctx(resp=_ok_resp())])
    with pytest.raises(aiohttp.ServerTimeoutError):
        async with proxy_module._post_with_retry(session, "http://x", provider="Test"):
            pass
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_never_retries_once_the_response_has_started(proxy_module):
    session = _FakeSession([_Ctx(resp=_ok_resp()), _Ctx(resp=_ok_resp())])
    with pytest.raises(aiohttp.ClientConnectionError):
        async with proxy_module._post_with_retry(session, "http://x", provider="Test"):
            raise aiohttp.ClientConnectionError("dropped mid-body")
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_a_bad_status_is_not_a_dropped_connection(proxy_module):
    bad = _ok_resp()
    bad.status = 500
    session = _FakeSession([_Ctx(resp=bad), _Ctx(resp=_ok_resp())])
    async with proxy_module._post_with_retry(session, "http://x", provider="Test") as resp:
        assert resp.status == 500
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_retry_logs_a_warning(proxy_module, caplog):
    session = _FakeSession([_Ctx(exc=_dropped()), _Ctx(resp=_ok_resp())])
    with caplog.at_level("WARNING", logger="proxy"):
        async with proxy_module._post_with_retry(session, "http://x", provider="GLM", request_id="abc"):
            pass
    assert any("[abc] GLM connection dropped" in r.message and "attempt 2/3" in r.message for r in caplog.records)


# --- through real provider functions ---

@pytest.mark.asyncio
async def test_glm_direct_recovers_from_a_dropped_connection(proxy_module):
    session = _FakeSession([_Ctx(exc=_dropped()), _Ctx(resp=_ok_resp("hello"))])
    with patch.object(proxy_module, "glm_api_key", "k"), \
         patch.object(proxy_module, "auth", MagicMock(session=session)):
        text, _ = await proxy_module.call_glm_direct(None, [{"role": "user", "content": "hi"}], "glm-4.6", 50)
    assert text == "hello"
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_openai_compatible_direct_recovers_from_a_dropped_connection(proxy_module):
    session = _FakeSession([_Ctx(exc=_dropped()), _Ctx(resp=_ok_resp("hello"))])
    with patch.object(proxy_module, "deepseek_api_key", "k"), \
         patch.object(proxy_module, "auth", MagicMock(session=session)):
        text, _ = await proxy_module.call_deepseek_direct(None, [{"role": "user", "content": "hi"}], "deepseek-flash", 50)
    assert text == "hello"
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_exhausted_retries_still_reach_the_providers_own_connection_message(proxy_module):
    session = _FakeSession([_Ctx(exc=aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))) for _ in range(3)])
    with patch.object(proxy_module, "auth", MagicMock(session=session)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "llama3", 50)
    assert exc.value.status_code == 502
    assert "Couldn't reach Ollama" in exc.value.detail
    assert len(session.calls) == 3
