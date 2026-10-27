"""A Claude answer compressed with Brotli is read instead of failing (2026-10-26). The headers captured from the Claude CLI carry its own `Accept-Encoding` (`gzip, deflate, br, zstd`);
forwarded upstream they let Anthropic answer with Brotli, which the bundled Python cannot decode ("Can not decode content-encoding: brotli") and the caller got a 500. Now the Claude
lane asks only for `gzip, deflate`, and an answer that still cannot be decoded is asked for once more without compression. Stand-in sessions only: the real API is never called."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from tests.test_upstream_retry import _Ctx, _FakeSession, _dropped, _ok_resp

BROTLI_MSG = "Can not decode content-encoding: brotli (br). Please install `Brotli`"
CAPTURED = {"Authorization": "Bearer sk-FAKE", "anthropic-version": "2023-06-01", "Content-Type": "application/json", "accept-encoding": "gzip, deflate, br, zstd", "User-Agent": "claude-cli"}


def _undecodable():
    info = MagicMock()
    info.real_url = "https://api.anthropic.com/v1/messages?beta=true"
    return aiohttp.ClientResponseError(info, (), status=400, message=BROTLI_MSG)


def _encoding_of(headers: dict) -> list:
    return [v for k, v in headers.items() if k.lower() == "accept-encoding"]


def test_the_claude_lane_never_asks_for_brotli_or_zstd(proxy_module):
    h = proxy_module._with_readable_encodings(CAPTURED)
    assert _encoding_of(h) == ["gzip, deflate"], "one Accept-Encoding, only what aiohttp reads on its own"
    assert h["Authorization"] == "Bearer sk-FAKE" and h["User-Agent"] == "claude-cli", "every other captured header is kept"
    assert "accept-encoding" not in h, "the captured spelling is replaced, not added to"
    assert _encoding_of(proxy_module._with_readable_encodings({"Authorization": "x"})) == ["gzip, deflate"], "none captured: still set"
    assert CAPTURED["accept-encoding"] == "gzip, deflate, br, zstd", "the captured headers themselves are not changed"


@pytest.mark.asyncio
async def test_a_direct_claude_call_sends_only_readable_encodings(proxy_module, monkeypatch):
    sent = {}
    resp = MagicMock()
    resp.status = 200
    resp.read = AsyncMock(return_value=json.dumps({"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1, "output_tokens": 1}}).encode())

    class Session:
        def post(self, url, **kw):
            sent.update(kw["headers"])
            return _Ctx(resp=resp)

    monkeypatch.setattr(proxy_module.auth, "body_template", {"system": [{"type": "text", "text": "billing"}], "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}], "model": "m", "max_tokens": 1}, raising=False)
    monkeypatch.setattr(proxy_module.auth, "headers", dict(CAPTURED), raising=False)
    monkeypatch.setattr(proxy_module.auth, "session", Session(), raising=False)
    err = None
    try:
        await proxy_module.call_api_direct("sys", [{"role": "user", "content": "hi"}], "claude-sonnet-5-5", 20)
    except Exception as e:                                     # (what comes back is not the point: what was asked for is)
        err = repr(e)
    assert _encoding_of(sent) == ["gzip, deflate"], (sent, err)


@pytest.mark.asyncio
async def test_an_answer_that_cannot_be_decoded_is_asked_for_once_more_without_compression(proxy_module):
    good = _ok_resp("pong")
    session = _FakeSession([_Ctx(exc=_undecodable()), _Ctx(resp=good)])
    async with proxy_module._post_with_retry(session, "https://x/y", provider="Claude", data=b"{}", headers=proxy_module._with_readable_encodings(CAPTURED)) as resp:
        assert resp is good, "the caller gets the answer, not a 500"
    assert len(session.calls) == 2
    assert _encoding_of(session.calls[0][1]["headers"]) == ["gzip, deflate"] and _encoding_of(session.calls[1][1]["headers"]) == ["identity"]
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer sk-FAKE" and session.calls[1][1]["data"] == b"{}", "the same request, only the encoding changed"


@pytest.mark.asyncio
async def test_the_extra_open_does_not_use_up_the_connection_drop_attempts_and_happens_once(proxy_module):
    good = _ok_resp()
    session = _FakeSession([_Ctx(exc=_dropped()), _Ctx(exc=_undecodable()), _Ctx(exc=_dropped()), _Ctx(resp=good)])
    async with proxy_module._post_with_retry(session, "https://x", provider="Claude", headers={}) as resp:
        assert resp is good
    assert len(session.calls) == 4
    again = _FakeSession([_Ctx(exc=_undecodable()), _Ctx(exc=_undecodable()), _Ctx(resp=good)])
    with pytest.raises(aiohttp.ClientResponseError):
        async with proxy_module._post_with_retry(again, "https://x", provider="Claude", headers={}):
            pass
    assert len(again.calls) == 2, "asked without compression once; a second undecodable answer is the caller's error, never a loop"


@pytest.mark.asyncio
async def test_other_400s_are_not_retried_and_the_log_line_is_plain(proxy_module, caplog):
    info = MagicMock()
    info.real_url = "https://x"
    other = aiohttp.ClientResponseError(info, (), status=400, message="Bad Request")
    session = _FakeSession([_Ctx(exc=other), _Ctx(resp=_ok_resp())])
    with pytest.raises(aiohttp.ClientResponseError):
        async with proxy_module._post_with_retry(session, "https://x", provider="Claude"):
            pass
    assert len(session.calls) == 1
    caplog.set_level("WARNING")
    ok = _FakeSession([_Ctx(exc=_undecodable()), _Ctx(resp=_ok_resp())])
    async with proxy_module._post_with_retry(ok, "https://x", provider="Claude", request_id="ab12", headers={}):
        pass
    assert any("[ab12] Claude answered in a compression MultiProxy can't read, asking again without compression" in r.getMessage() for r in caplog.records)
