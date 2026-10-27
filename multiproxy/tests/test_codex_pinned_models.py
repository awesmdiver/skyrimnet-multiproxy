"""Tests for Codex "pin a model" (2026-09-16): saving/removing a pinned model per provider through
the /config/codex-pin-model and /config/codex-unpin-model routes, and that pinned entries actually
show up merged into that provider's own models list (both the default ChatGPT provider and a saved
added provider), with the right full model id shape for each.
"""


def test_pin_default_model_appears_in_codex_status(test_client, proxy_module):
    r = test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": "gpt-6-astra"})
    assert r.status_code == 200
    assert proxy_module.codex_pinned_models["default"] == [{"id": "gpt-6-astra"}]

    import asyncio
    status = asyncio.run(proxy_module._codex_status())
    pinned_rows = [m for m in status["models"] if len(m) > 3 and m[3]]
    assert pinned_rows == [["codex/gpt-6-astra", "gpt-6-astra", "", "gpt-6-astra"]]


def test_pin_with_display_name(test_client, proxy_module):
    test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": "gpt-6-astra", "name": "GPT-6 Astra"})
    assert proxy_module.codex_pinned_models["default"] == [{"id": "gpt-6-astra", "name": "GPT-6 Astra"}]


def test_pinning_same_id_twice_does_not_duplicate_but_updates_name(test_client, proxy_module):
    test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": "gpt-6-astra"})
    test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": "gpt-6-astra", "name": "Astra"})
    assert proxy_module.codex_pinned_models["default"] == [{"id": "gpt-6-astra", "name": "Astra"}]


def test_pin_requires_model_id(test_client):
    assert test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": ""}).status_code == 400


def test_pin_rejects_unknown_provider(test_client):
    assert test_client.post("/config/codex-pin-model", json={"provider": "sn-does-not-exist", "model_id": "x"}).status_code == 400


def test_pin_on_added_provider_uses_provider_prefixed_model_id(test_client, proxy_module):
    added = test_client.post("/config/codex-providers", json={
        "type": "custom", "name": "My OpenRouter", "base_url": "https://openrouter.ai/api/v1",
    }).json()
    pid = added["id"]

    r = test_client.post("/config/codex-pin-model", json={"provider": pid, "model_id": "openai/gpt-4o-mini"})
    assert r.status_code == 200

    panel = test_client.get("/config/codex-provider-panel").json()
    entry = next(e for e in panel if e["id"] == pid)
    assert entry["models"] == [[f"codex/{pid}/openai/gpt-4o-mini", "openai/gpt-4o-mini", "", "openai/gpt-4o-mini"]]


def test_unpin_removes_model(test_client, proxy_module):
    test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": "gpt-6-astra"})
    r = test_client.post("/config/codex-unpin-model", json={"provider": "default", "model_id": "gpt-6-astra"})
    assert r.status_code == 200
    assert "default" not in proxy_module.codex_pinned_models  # tidy: empty provider key removed, not left as []


def test_unpin_unknown_pin_404s(test_client):
    assert test_client.post("/config/codex-unpin-model", json={"provider": "default", "model_id": "never-pinned"}).status_code == 404


def test_removing_a_provider_does_not_crash_its_leftover_pins(test_client, proxy_module):
    """Not cleaning up a removed provider's own pinned-models entry is fine -- _codex_pinned_rows is
    only ever looked up by a currently-saved provider's own id, so a stale key is simply never read
    again, not a dangling reference that could crash anything."""
    added = test_client.post("/config/codex-providers", json={
        "type": "custom", "name": "Throwaway", "base_url": "https://a.invalid/v1",
    }).json()
    pid = added["id"]
    test_client.post("/config/codex-pin-model", json={"provider": pid, "model_id": "x"})
    r = test_client.post(f"/config/codex-providers/{pid}/remove")
    assert r.status_code == 200
    # No crash re-rendering the panel afterward.
    assert test_client.get("/config/codex-provider-panel").status_code == 200
