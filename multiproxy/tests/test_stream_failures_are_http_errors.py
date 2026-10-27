"""Every provider's streaming path, end to end: a failure before any reply text is a REAL HTTP error
(2026-09-27, SkyrimNet Beta 25).

SkyrimNet's smart routing retries on the next provider and skips failing ones -- but only when it can
tell a request failed. A stream that answers "200 OK" with the error inside it counts as a success
(and the error words could be spoken by an NPC). So each streaming provider is checked here through the
real /v1/chat/completions route: the upstream's status (429 stays 429, 5xx stays 5xx), the normal
{"error": {...}} body, and no SSE data at all.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# (id, model given to SkyrimNet, module attribute holding the API key)
API_PROVIDERS = [
    ("glm", "glmzai/glm-4.6", "glm_api_key"),
    ("nano", "nano/gpt-4o-mini", "nano_api_key"),
    ("openai", "openai/gpt-4o-mini", "openai_api_key"),
    ("deepseek", "deepseek/deepseek-flash", "deepseek_api_key"),
    ("kilo", "kilo/anthropic/claude-sonnet-5", "kilo_api_key"),
    ("gemini", "gemini/gemini-3.8-flash", "gemini_api_key"),
    ("ollama-cloud", "ollama/llama3", "ollama_api_key"),
]


def _mock_response(status, text):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.read = AsyncMock(return_value=text.encode())
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


def _post(test_client, model):
    return test_client.post("/v1/chat/completions", json={
        "model": model, "messages": [{"role": "user", "content": "hi"}], "stream": True,
    })


def _assert_real_error(r, status, message):
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"error": {"message": message, "type": "api_error", "code": None}}
    assert "data:" not in r.text


@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.parametrize("provider,model,key_attr", API_PROVIDERS, ids=[p[0] for p in API_PROVIDERS])
def test_upstream_error_status_is_kept(proxy_module, test_client, provider, model, key_attr, status):
    body = json.dumps({"error": {"message": f"upstream said {status}"}})
    session = _mock_session(_mock_response(status, body))
    with patch.object(proxy_module, key_attr, "test-key"), \
         patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        r = _post(test_client, model)
    assert r.status_code == status
    assert f"upstream said {status}" in r.json()["error"]["message"]
    assert "data:" not in r.text
    assert "error]" not in r.text.lower()


@pytest.mark.parametrize("provider,model,key_attr", API_PROVIDERS, ids=[p[0] for p in API_PROVIDERS])
def test_a_missing_key_is_a_real_error_not_a_stream(proxy_module, test_client, provider, model, key_attr):
    if provider == "ollama-cloud":
        pytest.skip("Ollama without a key means the local daemon, which needs no key")
    r = _post(test_client, model)   # conftest leaves every key unset
    assert r.status_code == 500
    assert "Add one on the Providers tab." in r.json()["error"]["message"]
    assert "data:" not in r.text


def test_claude_upstream_429_is_a_real_429(proxy_module, test_client, monkeypatch):
    monkeypatch.setattr(proxy_module.auth, "headers", {"Authorization": "Bearer t"})
    monkeypatch.setattr(proxy_module.auth, "body_template", {"system": [{"type": "text", "text": "billing block"}], "messages": []})
    monkeypatch.setattr(proxy_module.auth, "session", None)

    async def _ready(*, force=False, timeout=60.0):
        return

    monkeypatch.setattr(proxy_module.auth, "ensure_ready", _ready)
    body = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
    session = _mock_session(_mock_response(429, body))
    with patch("aiohttp.ClientSession", return_value=session):
        r = _post(test_client, "claude-sonnet-5")
    _assert_real_error(r, 429, "slow down")


def test_claude_unavailable_is_a_real_503(proxy_module, test_client, monkeypatch):
    async def _not_ready(*, force=False, timeout=60.0):
        raise RuntimeError("Claude CLI is not installed (or not on PATH)")

    monkeypatch.setattr(proxy_module.auth, "ensure_ready", _not_ready)
    r = _post(test_client, "claude-sonnet-5")
    assert r.status_code == 503
    assert "Claude isn't available right now" in r.json()["error"]["message"]
    assert "data:" not in r.text


def test_codex_not_installed_is_a_real_500(proxy_module, test_client):
    r = _post(test_client, "codex/gpt-5.5")   # conftest: no Codex CLI on PATH
    assert r.status_code == 500
    assert "Codex isn't installed. Check Help to see how to install it." in r.json()["error"]["message"]
    assert "data:" not in r.text


def test_codex_not_logged_in_is_a_real_401(proxy_module, test_client):
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=False)):
        r = _post(test_client, "codex/gpt-5.5")
    assert r.status_code == 401
    assert "not signed in to Codex" in r.json()["error"]["message"]
    assert "data:" not in r.text


def test_codex_failure_is_a_real_error(proxy_module, test_client):
    from fastapi import HTTPException
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
         patch.object(proxy_module, "_run_codex_exec", AsyncMock(side_effect=HTTPException(status_code=504, detail="Codex didn't respond in time."))):
        r = _post(test_client, "codex/gpt-5.5")
    _assert_real_error(r, 504, "Codex didn't respond in time.")


def test_antigravity_not_installed_is_a_real_500(proxy_module, test_client):
    r = _post(test_client, "geminicli/gemini-3.8-flash-low")   # conftest: no agy on PATH
    assert r.status_code == 500
    assert "Google Antigravity is not installed" in r.json()["error"]["message"]
    assert "data:" not in r.text


def test_antigravity_failure_before_any_text_is_a_real_error(proxy_module, test_client):
    from fastapi import HTTPException

    async def broken(*a, **k):
        if False:
            yield  # pragma: no cover
        raise HTTPException(status_code=502, detail="Antigravity failed: not signed in")

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/agy"), \
         patch.object(proxy_module, "_stream_agy_events", broken):
        r = _post(test_client, "geminicli/gemini-3.8-flash-low")
    _assert_real_error(r, 502, "Antigravity failed: not signed in")


def test_antigravity_failure_after_some_text_ends_cleanly(proxy_module, test_client):
    from fastapi import HTTPException

    async def half(*a, **k):
        yield {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Well, I"}}
        raise HTTPException(status_code=504, detail="Antigravity didn't respond in time.")

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/agy"), \
         patch.object(proxy_module, "_stream_agy_events", half):
        r = _post(test_client, "geminicli/gemini-3.8-flash-low")
    assert r.status_code == 200
    assert "Well, I" in r.text
    assert "respond in time" not in r.text
    assert r.text.rstrip().endswith("data: [DONE]")


def test_no_provider_ever_puts_error_wording_into_reply_text():
    """Belt and braces: none of the old in-reply error labels are left anywhere in the source."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "multiproxy.py").read_text(encoding="utf-8")
    for label in ("[Claude Error]", "[API Error", "[Auth refresh failed]", "[Codex Error",
                  "[Antigravity CLI Error", " error] {message}", " connection failed: {e}]"):
        assert not re.search(r'"content": f?"' + re.escape(label), src), label
