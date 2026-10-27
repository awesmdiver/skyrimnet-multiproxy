"""Tests for Codex multi-provider support (2026-09-16): saving/editing/removing a saved provider
through the /config/codex-providers REST routes, the codex/MODEL vs codex/<provider-id>/MODEL
routing split, and the real subprocess argv/env each path produces.

The default ChatGPT/Codex-subscription provider is never stored in codex_providers -- it's
implicit, always present, and can't be created/edited/removed through these routes. Every test
here that touches a real subprocess mocks asyncio.create_subprocess_exec the same way
test_codex_subprocess.py already does.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_proc(out_text: str = "hello"):
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))
    return proc


def _patch_out_file(monkeypatch, proxy_module, text: str):
    """_run_codex_exec reads its reply from the -o output file on disk, not from stdout -- write
    it ourselves right before the mocked subprocess "finishes", mirroring how a real `codex exec`
    call would have populated it."""
    real_exec = asyncio.create_subprocess_exec

    async def _fake_exec(*args, **kwargs):
        proc = _make_mock_proc()
        # "-o" is always immediately followed by the output file path in _run_codex_exec's argv.
        out_idx = args.index("-o") + 1
        out_file = args[out_idx]

        async def _communicate(_input=None):
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(text)
            return (b"", b"")

        proc.communicate = _communicate
        return proc

    return _fake_exec


# --- REST CRUD -------------------------------------------------------------------------------

def test_add_custom_provider_round_trips(test_client, proxy_module):
    r = test_client.post("/config/codex-providers", json={
        "type": "custom", "name": "My OpenRouter",
        "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-real-key",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["id"].startswith("sn-")
    assert "." not in data["id"]
    assert data["name"] == "My OpenRouter"
    assert data["hasKey"] is True
    assert "api_key" not in data  # never echoed back

    listed = test_client.get("/config/codex-providers").json()["providers"]
    assert len(listed) == 1
    assert listed[0]["id"] == data["id"]
    assert "api_key" not in listed[0]
    assert proxy_module.codex_providers[0]["api_key"] == "sk-or-real-key"  # actually saved server-side


def test_add_custom_provider_requires_name_and_base_url(test_client):
    assert test_client.post("/config/codex-providers", json={"type": "custom", "base_url": "https://x/v1"}).status_code == 400
    assert test_client.post("/config/codex-providers", json={"type": "custom", "name": "X"}).status_code == 400


def test_add_builtin_preset_forces_fixed_base_url_and_no_key(test_client, proxy_module):
    r = test_client.post("/config/codex-providers", json={
        "type": "builtin", "preset": "ollama", "api_key": "should-be-ignored",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Local Ollama"
    assert data["base_url"] == proxy_module.CODEX_OLLAMA_BASE_URL
    assert data["hasKey"] is False
    assert proxy_module.codex_providers[0].get("api_key") is None


def test_add_builtin_openai_preset_saves_a_real_key(test_client, proxy_module):
    r = test_client.post("/config/codex-providers", json={
        "type": "builtin", "preset": "openai", "api_key": "sk-proj-real",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["base_url"] == proxy_module.CODEX_OPENAI_BASE_URL
    assert data["hasKey"] is True
    assert proxy_module.codex_providers[0]["api_key"] == "sk-proj-real"


def test_add_builtin_rejects_unknown_preset(test_client):
    assert test_client.post("/config/codex-providers", json={"type": "builtin", "preset": "bedrock"}).status_code == 400


def test_edit_provider_updates_fields_and_can_clear_key(test_client, proxy_module):
    added = test_client.post("/config/codex-providers", json={
        "type": "custom", "name": "Throwaway", "base_url": "https://a.invalid/v1", "api_key": "sk-a",
    }).json()
    pid = added["id"]

    r = test_client.post(f"/config/codex-providers/{pid}", json={"name": "Renamed", "base_url": "https://b.invalid/v1"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert proxy_module.codex_providers[0]["base_url"] == "https://b.invalid/v1"
    assert proxy_module.codex_providers[0]["api_key"] == "sk-a"  # untouched -- omitted from the edit body

    r2 = test_client.post(f"/config/codex-providers/{pid}", json={"api_key": ""})
    assert r2.status_code == 200
    assert r2.json()["hasKey"] is False
    assert "api_key" not in proxy_module.codex_providers[0]


def test_edit_unknown_provider_404s(test_client):
    assert test_client.post("/config/codex-providers/does-not-exist", json={"name": "x"}).status_code == 404


def test_remove_provider(test_client, proxy_module):
    added = test_client.post("/config/codex-providers", json={
        "type": "custom", "name": "Throwaway", "base_url": "https://a.invalid/v1",
    }).json()
    pid = added["id"]
    assert len(proxy_module.codex_providers) == 1

    r = test_client.post(f"/config/codex-providers/{pid}/remove")
    assert r.status_code == 200
    assert proxy_module.codex_providers == []
    assert test_client.post(f"/config/codex-providers/{pid}/remove").status_code == 404


def test_default_provider_cannot_be_edited_or_removed(test_client):
    assert test_client.post("/config/codex-providers/default", json={"name": "x"}).status_code == 400
    assert test_client.post("/config/codex-providers/default/remove").status_code == 400


# --- Model routing resolution ------------------------------------------------------------------

def test_plain_codex_model_resolves_to_default_unchanged(proxy_module):
    """The one behavior that must never regress: a plain codex/MODEL request (no provider
    segment) keeps meaning the default ChatGPT subscription, even with saved providers present."""
    proxy_module.codex_providers.append({
        "id": "sn-my-openrouter", "type": "custom", "name": "My OpenRouter",
        "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-x",
    })
    provider, model = proxy_module._codex_resolve_provider("gpt-5.5")
    assert provider is None
    assert model == "gpt-5.5"


def test_model_containing_slash_with_no_matching_provider_id_falls_back_to_default(proxy_module):
    """A model id that happens to contain '/' for the DEFAULT provider must still work if its
    first segment doesn't match any saved provider's own id -- same specific-match-first,
    generic-catch-all-last precedence is_openrouter_model already uses."""
    proxy_module.codex_providers.append({
        "id": "sn-my-openrouter", "type": "custom", "name": "My OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
    })
    provider, model = proxy_module._codex_resolve_provider("some-vendor/some-model")
    assert provider is None
    assert model == "some-vendor/some-model"


def test_provider_prefixed_model_resolves_to_saved_provider_and_remaining_model(proxy_module):
    proxy_module.codex_providers.append({
        "id": "sn-my-openrouter", "type": "custom", "name": "My OpenRouter",
        "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-x",
    })
    provider, model = proxy_module._codex_resolve_provider("sn-my-openrouter/anthropic/claude-sonnet-4.5")
    assert provider["id"] == "sn-my-openrouter"
    assert model == "anthropic/claude-sonnet-4.5"


# --- Subprocess argv/env for each path -----------------------------------------------------

@pytest.mark.asyncio
async def test_default_provider_invocation_unchanged(monkeypatch, proxy_module):
    """provider=None must produce byte-for-byte the same argv/env as before this feature existed
    -- no extra -c flags, env=None (inherits the parent process env exactly like today)."""
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        proc = _make_mock_proc()
        out_idx = args.index("-o") + 1
        with open(args[out_idx], "w", encoding="utf-8") as f:
            f.write("pong")
        return proc

    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", _fake_exec):
        text, usage = await proxy_module.call_codex_direct(
            system_prompt=None, messages=[{"role": "user", "content": "hi"}], model="gpt-5.5", max_tokens=100,
        )

    assert text == "pong"
    assert "-c" not in captured["args"]
    assert captured["env"] is None


@pytest.mark.asyncio
async def test_added_provider_injects_c_flags_and_scoped_env_key(monkeypatch, proxy_module):
    """The real, live-verified mechanism (2026-09-16): a saved provider is defined entirely via
    repeated -c overrides plus a single env var scoped to the subprocess's own env -- never the
    proxy's global environment."""
    proxy_module.codex_providers.append({
        "id": "sn-my-openrouter", "type": "custom", "name": "My OpenRouter",
        "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-real-key",
    })
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        proc = _make_mock_proc()
        out_idx = args.index("-o") + 1
        with open(args[out_idx], "w", encoding="utf-8") as f:
            f.write("I AM ROUTED VIA OPENROUTER")
        return proc

    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=False)), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", _fake_exec):
        # _codex_logged_in forced False here on purpose -- an added provider must NOT require the
        # ChatGPT login precondition at all, unlike the default path.
        text, usage = await proxy_module.call_codex_direct(
            system_prompt=None, messages=[{"role": "user", "content": "hi"}],
            model="sn-my-openrouter/anthropic/claude-sonnet-4.5", max_tokens=100,
        )

    assert text == "I AM ROUTED VIA OPENROUTER"
    args = captured["args"]
    assert "-m" in args and args[args.index("-m") + 1] == "anthropic/claude-sonnet-4.5"
    assert "model_providers.sn-my-openrouter.base_url=https://openrouter.ai/api/v1" in args
    assert "model_providers.sn-my-openrouter.wire_api=responses" in args
    assert "model_provider=sn-my-openrouter" in args
    env_key_arg = next(a for a in args if a.startswith("model_providers.sn-my-openrouter.env_key="))
    env_var_name = env_key_arg.split("=", 1)[1]
    assert captured["env"][env_var_name] == "sk-or-real-key"
    # A real, scoped subprocess env -- never the proxy's own global os.environ.
    import os
    assert env_var_name not in os.environ


@pytest.mark.asyncio
async def test_added_provider_without_key_omits_env_key(proxy_module):
    """Ollama/LM Studio presets have no key at all -- confirmed live 2026-09-16 that Codex's own
    built-in local providers don't set env_key either, so a keyless saved provider shouldn't
    either."""
    proxy_module.codex_providers.append({
        "id": "sn-local-ollama", "type": "builtin", "preset": "ollama",
        "name": "Local Ollama", "base_url": proxy_module.CODEX_OLLAMA_BASE_URL,
    })
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        proc = _make_mock_proc()
        out_idx = args.index("-o") + 1
        with open(args[out_idx], "w", encoding="utf-8") as f:
            f.write("ok")
        return proc

    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", _fake_exec):
        await proxy_module.call_codex_direct(
            system_prompt=None, messages=[{"role": "user", "content": "hi"}],
            model="sn-local-ollama/qwen3:8b", max_tokens=100,
        )

    assert not any("env_key=" in a for a in captured["args"])
    assert captured["env"] is None
