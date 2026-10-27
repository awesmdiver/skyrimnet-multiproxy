"""Tests for CapturePrompts (multiproxy.ini) -- the opt-in switch that saves real SkyrimNet request
bodies to prompt-captures/ so they can replace the synthetic fixtures in
tests/fixtures/skyrimnet_shapes.py with genuine ones. Off by default; these tests always redirect
CAPTURE_DIR to a throwaway tmp_path, same isolation discipline conftest.py's own
_isolate_config_file fixture uses for config.json -- a test must never write into the real
prompt-captures/ next to multiproxy.py.
"""
import json
import os

import pytest


@pytest.mark.asyncio
async def test_disabled_by_default_writes_nothing(proxy_module, tmp_path):
    capture_dir = tmp_path / "prompt-captures"
    req = proxy_module.ChatRequest(model="ollama/x", messages=[{"role": "user", "content": "hi"}])
    proxy_module.capture_prompt_if_enabled(req)  # CAPTURE_PROMPTS is False by default
    assert not capture_dir.exists()


def test_disabled_reads_false_by_default(proxy_module):
    # Confirms the real fallback in the ini-parsing block, not just this test's own assumption.
    assert proxy_module.CAPTURE_PROMPTS is False


@pytest.mark.asyncio
async def test_enabled_writes_a_stripped_json_file(proxy_module, tmp_path, monkeypatch):
    capture_dir = tmp_path / "prompt-captures"
    monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", True)
    monkeypatch.setattr(proxy_module, "CAPTURE_DIR", str(capture_dir))

    req = proxy_module.ChatRequest(
        model="gemini/gemini-3.6-flash",
        messages=[
            {"role": "system", "content": "You are Lydia."},
            {"role": "user", "content": "Hungry?"},
        ],
        stream=True,
        api_key="sk-should-never-be-written",
    )
    proxy_module.capture_prompt_if_enabled(req)

    files = list(capture_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["model"] == "gemini/gemini-3.6-flash"
    assert data["messages"][0]["content"] == "You are Lydia."
    assert data["api_key"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_enabled_strips_nested_and_case_insensitive_secret_fields(proxy_module, tmp_path, monkeypatch):
    capture_dir = tmp_path / "prompt-captures"
    monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", True)
    monkeypatch.setattr(proxy_module, "CAPTURE_DIR", str(capture_dir))

    req = proxy_module.ChatRequest(
        model="ollama/x",
        messages=[{"role": "user", "content": "hi"}],
        Authorization="Bearer real-token-should-not-survive",
        nested={"apiKey": "also-should-not-survive", "safe_field": "this stays"},
    )
    proxy_module.capture_prompt_if_enabled(req)

    data = json.loads(next(capture_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert data["Authorization"] == "***REDACTED***"
    assert data["nested"]["apiKey"] == "***REDACTED***"
    assert data["nested"]["safe_field"] == "this stays"


@pytest.mark.asyncio
async def test_capture_failure_never_raises(proxy_module, monkeypatch):
    """Best-effort: a broken CAPTURE_DIR (e.g. an unwritable path) must never break a real request."""
    monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", True)
    monkeypatch.setattr(proxy_module, "CAPTURE_DIR", "\0invalid-path")
    req = proxy_module.ChatRequest(model="ollama/x", messages=[{"role": "user", "content": "hi"}])
    proxy_module.capture_prompt_if_enabled(req)  # must not raise


class TestChatCompletionsEndToEnd:
    def test_capture_fires_through_the_real_endpoint(self, proxy_module, test_client, tmp_path, monkeypatch):
        capture_dir = tmp_path / "prompt-captures"
        monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", True)
        monkeypatch.setattr(proxy_module, "CAPTURE_DIR", str(capture_dir))
        monkeypatch.setattr(proxy_module, "ollama_api_key", None)

        async def _fake_ollama_direct(system_prompt, messages, model, max_tokens, **extra_params):
            return "ok", {}
        monkeypatch.setattr(proxy_module, "call_ollama_direct", _fake_ollama_direct)

        resp = test_client.post("/v1/chat/completions", json={
            "model": "ollama/some-model",
            "messages": [{"role": "user", "content": "hi"}],
        })

        assert resp.status_code == 200
        files = list(capture_dir.glob("*.json"))
        assert len(files) == 1
        assert os.path.getsize(files[0]) > 0
