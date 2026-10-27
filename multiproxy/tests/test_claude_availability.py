"""Tests for making Claude optional (2026-09-13): a user with Codex set up but no working Claude
subscription (their org had disabled Claude Code access) couldn't use this proxy AT ALL, because
CLAUDE_PATH used to `raise RuntimeError` at import time, and a failed startup auth-capture crashed
`lifespan` before the app ever came up. Claude auth is now only required for a request that
actually routes to Claude; every other provider must keep working regardless of Claude's state.
"""
import importlib.util
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _import_proxy_fresh(file_path, which_side_effect):
    """Load multiproxy.py as a brand-new module object under a throwaway name -- NOT into
    sys.modules['proxy'] -- so a test can exercise real import-time behavior (does the module
    still import cleanly with no CLI on PATH?) without disturbing the shared `proxy` module every
    other test in this suite relies on."""
    spec = importlib.util.spec_from_file_location("proxy_fresh_import_test", file_path)
    module = importlib.util.module_from_spec(spec)
    import poll_cache                       # (the shared lookup remembers answers and also reads Windows' saved PATH: start it clean, and with no saved PATH)
    poll_cache.forget_which()
    try:
        with patch("shutil.which", side_effect=which_side_effect), patch.object(poll_cache, "_saved_path_reader", lambda: ""):
            spec.loader.exec_module(module)
    finally:
        poll_cache.forget_which()
    return module


def test_import_without_claude_cli_does_not_crash(proxy_module, tmp_path):
    """The actual regression: this used to `raise RuntimeError("claude CLI not found on PATH")` at
    import time, so a machine with no Claude CLI (Codex-only, say) couldn't even start the proxy."""
    fresh = _import_proxy_fresh(proxy_module.__file__, lambda name: None)
    # This disposable module still computed its own CONFIG_FILE pointing at the real config.json
    # (read-only at import, but redirect it anyway before touching it any further, on principle).
    fresh.CONFIG_FILE = str(tmp_path / "config.json")

    assert fresh.CLAUDE_PATH is None

    from fastapi.testclient import TestClient
    client = TestClient(fresh.app, raise_server_exceptions=True)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "SkyrimNet MultiProxy" in resp.text
    assert "Program not installed" in resp.text

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["claude_cli_found"] is False
    assert data["claude_path"] is None
    assert data["status"] == "healthy"  # the PROXY is up, independent of Claude


@pytest.mark.asyncio
async def test_lifespan_survives_failing_auth_capture(proxy_module, monkeypatch):
    """The other half of the same regression: even with a Claude CLI present, a failed capture
    (e.g. "Your organization has disabled Claude subscription access") used to raise straight out
    of `lifespan` before `try: yield`, crashing startup entirely and leaking `auth.session`."""
    class _FakeRunner:
        async def cleanup(self):
            pass

    async def _fake_start_interceptor():
        return _FakeRunner()

    async def _raise_unavailable(*args, **kwargs):
        raise RuntimeError("Your organization has disabled Claude subscription access")

    monkeypatch.setattr(proxy_module, "start_interceptor", _fake_start_interceptor)
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", _raise_unavailable)

    async with proxy_module.lifespan(proxy_module.app):
        pass  # must not raise

    assert proxy_module.auth.session is None  # closed and cleared by the `finally` block


def _reset_claude_auth_state(monkeypatch, proxy_module):
    """A clean 'never attempted' Claude auth state -- auth.headers/body_template/last_error persist
    on the module-level `auth` singleton across tests otherwise, since proxy_module's own fixture
    doesn't touch them (nothing else in this suite exercises the real Claude branch)."""
    monkeypatch.setattr(proxy_module.auth, "headers", None)
    monkeypatch.setattr(proxy_module.auth, "body_template", None)
    monkeypatch.setattr(proxy_module.auth, "last_error", None)
    monkeypatch.setattr(proxy_module.auth, "_last_attempt", 0.0)


def test_claude_non_streaming_request_returns_clean_error_when_unavailable(test_client, proxy_module, monkeypatch):
    _reset_claude_auth_state(monkeypatch, proxy_module)

    async def _fail_capture():
        proxy_module.auth.last_error = "Your organization has disabled Claude subscription access for Claude Code"
        raise RuntimeError(proxy_module.auth.last_error)

    monkeypatch.setattr(proxy_module, "capture_auth", _fail_capture)

    resp = test_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 503
    message = resp.json()["error"]["message"]
    assert "Claude isn't available right now" in message
    assert "organization has disabled" in message
    assert "different provider" in message.lower()


def test_claude_streaming_request_returns_clean_error_when_unavailable(test_client, proxy_module, monkeypatch):
    """Streaming gets the same real HTTP 503 as non-streaming (2026-09-27, SkyrimNet Beta 25): a
    "200 OK" stream carrying an error inside it looks like a success to SkyrimNet's smart routing."""
    _reset_claude_auth_state(monkeypatch, proxy_module)

    async def _fail_capture():
        proxy_module.auth.last_error = "Claude CLI is not installed (or not on PATH)"
        raise RuntimeError(proxy_module.auth.last_error)

    monkeypatch.setattr(proxy_module, "capture_auth", _fail_capture)

    resp = test_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert resp.status_code == 503
    assert "Claude isn't available right now" in resp.json()["error"]["message"]
    assert "data:" not in resp.text


def test_second_claude_request_within_cooldown_does_not_recapture(test_client, proxy_module, monkeypatch):
    """Rate-limit guard: a SkyrimNet session firing many requests at a broken Claude must not spawn
    a fresh `claude --print` per request -- reuse the same recent failure instead."""
    _reset_claude_auth_state(monkeypatch, proxy_module)

    call_count = {"n": 0}

    async def _fail_capture():
        call_count["n"] += 1
        proxy_module.auth.last_error = "Your organization has disabled Claude subscription access"
        raise RuntimeError(proxy_module.auth.last_error)

    monkeypatch.setattr(proxy_module, "capture_auth", _fail_capture)

    for _ in range(3):
        resp = test_client.post("/v1/chat/completions", json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 503

    assert call_count["n"] == 1, "capture_auth should only run once per cooldown window, not once per request"


def test_non_claude_request_succeeds_while_claude_unavailable(test_client, proxy_module, monkeypatch):
    """The actual point of this whole fix: every other provider must work normally regardless of
    Claude's state -- Claude auth is never touched for a request that doesn't route to Claude."""
    _reset_claude_auth_state(monkeypatch, proxy_module)
    monkeypatch.setattr(proxy_module.auth, "last_error", "Claude CLI is not installed (or not on PATH)")
    monkeypatch.setattr(proxy_module.auth, "_last_attempt", time.time())

    async def _should_not_be_called():
        raise AssertionError("capture_auth must never run for a non-Claude request")

    monkeypatch.setattr(proxy_module, "capture_auth", _should_not_be_called)
    monkeypatch.setattr(proxy_module, "openai_api_key", "sk-test-key")

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={
        "choices": [{"message": {"content": "Hello from OpenAI"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=mock_resp)
    session.close = AsyncMock()

    with patch("aiohttp.ClientSession", return_value=session):
        resp = test_client.post("/v1/chat/completions", json={
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
        })

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Hello from OpenAI"


def _make_mock_stream_response(status=200, body=b"", content_iter=None):
    """A mock aiohttp response usable as an async context manager, for call_api_streaming_with_retry
    (which reads via .read() on a non-200 and .content.iter_any() on success)."""
    resp = MagicMock()
    resp.status = status
    resp.read = AsyncMock(return_value=body)
    content = MagicMock()

    async def _iter():
        for chunk in (content_iter or []):
            yield chunk
    content.iter_any.return_value = _iter()
    resp.content = content
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class TestStreamingSurvivesAuthAndUnexpectedFailures:
    """Real production incident (2026-09-16), confirmed live: call_api_streaming_with_retry's own
    internal auth.ensure_ready() calls had nothing catching them -- a login that expired (or failed
    to refresh) AFTER chat_completions()'s own pre-check had already passed raised RuntimeError
    straight out of the generator with ZERO SSE events sent, not even [DONE]. FastAPI had already
    committed to the StreamingResponse by then, so the player got a truncated body and a dead NPC --
    the same failure class the Antigravity CLI streaming bug produced, on Claude this time."""

    def test_auth_refresh_failure_after_stream_starts_gets_a_clean_actionable_error(self, test_client, proxy_module, monkeypatch):
        """The exact reproduction: auth is genuinely ready when the request starts (real headers
        set, matching chat_completions()'s own pre-check having already succeeded), the live call
        comes back 401 (a token that just expired), and the internal single-flight refresh
        (auth.ensure_ready(force=True)) itself fails -- exactly what "confirmed live" means here."""
        monkeypatch.setattr(proxy_module.auth, "headers", {"Authorization": "Bearer expired-token"})
        monkeypatch.setattr(proxy_module.auth, "body_template", {})
        monkeypatch.setattr(proxy_module.auth, "session", None)

        ensure_ready_calls = {"n": 0}

        async def _ensure_ready(*, force=False, timeout=60.0):
            ensure_ready_calls["n"] += 1
            if ensure_ready_calls["n"] == 1:
                return  # the top-of-loop check: auth was genuinely fine a moment ago
            proxy_module.auth.last_error = "Your organization has disabled Claude subscription access"
            raise RuntimeError(proxy_module.auth.last_error)  # the forced refresh, which fails for real
        monkeypatch.setattr(proxy_module.auth, "ensure_ready", _ensure_ready)

        unauthorized = _make_mock_stream_response(status=401, body=b'{"error":{"message":"invalid x-api-key"}}')
        session = MagicMock()
        session.post = MagicMock(return_value=unauthorized)
        session.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=session):
            resp = test_client.post("/v1/chat/completions", json={
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })

        # Nothing has been sent yet, so (2026-09-27) this is a real HTTP 503, not a "200" stream.
        assert resp.status_code == 503
        message = resp.json()["error"]["message"]
        assert ensure_ready_calls["n"] == 2, "both the top-of-loop check and the forced refresh must have run"
        # Actionable, not a bare internal-error message -- a login the player can go fix.
        assert "Claude isn't available right now" in message
        assert "organization has disabled" in message
        assert "different provider" in message.lower()
        assert "[Claude Error]" not in resp.text

    def test_unexpected_non_auth_exception_gets_a_clean_generic_error(self, test_client, proxy_module, monkeypatch):
        """A failure that ISN'T specifically an auth problem (a body-building bug, a network hiccup
        with no clean status code) must still degrade to a readable chunk + clean [DONE] -- the
        broader `except Exception` safety net, same shape the Antigravity CLI fix already
        established, for anything the auth-specific handling above doesn't cover."""
        monkeypatch.setattr(proxy_module.auth, "headers", {"Authorization": "Bearer real-token"})
        monkeypatch.setattr(proxy_module.auth, "body_template", {})
        monkeypatch.setattr(proxy_module.auth, "session", None)

        async def _ensure_ready(*, force=False, timeout=60.0):
            return  # auth itself is completely fine
        monkeypatch.setattr(proxy_module.auth, "ensure_ready", _ensure_ready)

        def _raise(*args, **kwargs):
            raise ValueError("a genuinely unexpected bug, not an auth problem")
        monkeypatch.setattr(proxy_module, "_build_api_body", _raise)

        resp = test_client.post("/v1/chat/completions", json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })

        assert resp.status_code == 500
        assert "Something went wrong in MultiProxy" in resp.json()["error"]["message"]
        assert "[Claude Error]" not in resp.text

    def test_normal_streamed_reply_is_unaffected(self, test_client, proxy_module, monkeypatch):
        """The happy path must stream exactly as before -- no new wrapping changes real behavior
        when nothing goes wrong."""
        monkeypatch.setattr(proxy_module.auth, "headers", {"Authorization": "Bearer real-token"})
        monkeypatch.setattr(proxy_module.auth, "body_template", {
            "system": [{"type": "text", "text": "billing block"}], "messages": [],
        })
        monkeypatch.setattr(proxy_module.auth, "session", None)

        async def _ensure_ready(*, force=False, timeout=60.0):
            return
        monkeypatch.setattr(proxy_module.auth, "ensure_ready", _ensure_ready)

        sse_events = [
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n\n',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}\n\n',
            b'data: {"type":"message_delta","usage":{"output_tokens":3},"delta":{"stop_reason":"end_turn"}}\n\n',
        ]
        good = _make_mock_stream_response(status=200, content_iter=sse_events)
        session = MagicMock()
        session.post = MagicMock(return_value=good)
        session.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=session):
            resp = test_client.post("/v1/chat/completions", json={
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })

        assert resp.status_code == 200
        body = resp.text
        assert '"content": "Hello"' in body
        assert "[Claude Error]" not in body
        assert body.count("data: [DONE]") == 1


def test_redact_credential_hides_the_full_token(proxy_module):
    redacted = proxy_module._redact_credential("Bearer sk-ant-oat01-verysecrettoken1234")
    assert "verysecrettoken" not in redacted
    assert redacted.startswith("Bearer")
    assert redacted.endswith("1234")


def test_redact_credential_handles_missing_value(proxy_module):
    assert proxy_module._redact_credential(None) == "(none)"
    assert proxy_module._redact_credential("") == "(none)"


class _FakeInterceptorRequest:
    """Just enough of an aiohttp.web.Request to drive interceptor_handler in a test."""

    def __init__(self, body: bytes, headers: dict, path: str = "/v1/messages"):
        self._body = body
        self.headers = headers
        self.path = path
        self.path_qs = path

    async def read(self):
        return self._body


@pytest.mark.asyncio
async def test_interceptor_never_logs_the_full_captured_token(proxy_module, monkeypatch, caplog):
    """The actual incident this session started from: the full 'Bearer sk-ant-...' token was
    logged to console (and pasted into Discord by a user asking for help). Drives the real
    interceptor_handler code path, not just the _redact_credential helper in isolation."""
    monkeypatch.setattr(proxy_module.auth, "headers", None)
    monkeypatch.setattr(proxy_module.auth, "body_template", None)

    secret_token = "sk-ant-oat01-THIS-IS-A-FAKE-SECRET-TOKEN-DO-NOT-LEAK-1234"
    fake_request = _FakeInterceptorRequest(
        body=json.dumps({"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"Authorization": f"Bearer {secret_token}", "Host": "127.0.0.1"},
    )

    forward_resp = MagicMock()
    forward_resp.status = 200
    forward_resp.read = AsyncMock(return_value=b"{}")
    forward_resp.headers = {"Content-Type": "application/json"}
    forward_resp.__aenter__ = AsyncMock(return_value=forward_resp)
    forward_resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post = MagicMock(return_value=forward_resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with caplog.at_level(logging.INFO, logger="proxy"), \
         patch("aiohttp.ClientSession", return_value=session):
        await proxy_module.interceptor_handler(fake_request)

    assert secret_token not in caplog.text
    assert "Auth captured" in caplog.text
