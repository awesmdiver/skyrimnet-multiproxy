"""Guards against the real bug that shipped in 2d1de03: a dashboard test posted through the real
/config/*-key routes with no isolation, and _save_config() wrote straight to the director's live
config.json (the one Skyrim is pointed at) -- erasing real OpenRouter/GLM/Nano-GPT/OpenAI
keys with no way to recover them. Fixed by the autouse `_isolate_config_file` fixture in
conftest.py; this test exists to prove that fixture is actually working, not just present.
"""
import os

from conftest import _hash_file


def _real_config_path():
    """The real config.json path, computed independently of proxy.CONFIG_FILE -- during a test,
    proxy.CONFIG_FILE has been monkeypatched away by the autouse isolation fixture, so reading it
    here would just tell us the fixture worked, not prove it against the real file's own location."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def test_config_file_is_isolated_during_tests(proxy_module):
    """proxy.CONFIG_FILE must never equal the real config.json path while a test is running --
    that's the whole isolation contract the autouse fixture exists to provide."""
    assert proxy_module.CONFIG_FILE != _real_config_path()


def test_posting_keys_never_touches_the_real_config_file(test_client, proxy_module):
    """The actual regression check: hit every /config/*-key route (throwaway key, then empty key --
    the exact sequence that wiped the real file before) and prove the real config.json on disk is
    byte-for-byte unchanged afterward."""
    real_path = _real_config_path()
    before_hash = _hash_file(real_path)

    for endpoint in (
        "/config/openrouter-key", "/config/glm-key", "/config/nano-key",
        "/config/openai-key", "/config/deepseek-key", "/config/kilo-key", "/config/ollama-key",
    ):
        test_client.post(endpoint, json={"key": "throwaway-value"})
        test_client.post(endpoint, json={"key": ""})

    after_hash = _hash_file(real_path)
    assert after_hash == before_hash, (
        "a /config/*-key POST touched the REAL config.json -- this is the exact bug that erased "
        "real saved API keys in 2d1de03. Check that proxy.CONFIG_FILE is actually being "
        "monkeypatched for this test."
    )


def test_codex_provider_routes_never_touch_the_real_config_file(test_client, proxy_module):
    """Same regression guard as above, extended to the new Codex multi-provider CRUD routes
    (2026-09-16) -- add, edit, and remove a throwaway custom provider and prove the real
    config.json on disk is byte-for-byte unchanged afterward."""
    real_path = _real_config_path()
    before_hash = _hash_file(real_path)

    add_resp = test_client.post("/config/codex-providers", json={
        "type": "custom", "name": "Throwaway", "base_url": "https://example.invalid/v1", "api_key": "sk-throwaway",
    })
    provider_id = add_resp.json()["id"]
    test_client.post(f"/config/codex-providers/{provider_id}", json={"name": "Throwaway Renamed"})
    test_client.post(f"/config/codex-providers/{provider_id}/remove")

    after_hash = _hash_file(real_path)
    assert after_hash == before_hash, (
        "a /config/codex-providers* POST touched the REAL config.json -- check that "
        "proxy.CONFIG_FILE is actually being monkeypatched for this test."
    )


def test_codex_pin_routes_never_touch_the_real_config_file(test_client, proxy_module):
    """Same regression guard, extended to the new Codex pin/unpin routes (2026-09-16)."""
    real_path = _real_config_path()
    before_hash = _hash_file(real_path)

    test_client.post("/config/codex-pin-model", json={"provider": "default", "model_id": "gpt-6-astra"})
    test_client.post("/config/codex-unpin-model", json={"provider": "default", "model_id": "gpt-6-astra"})

    after_hash = _hash_file(real_path)
    assert after_hash == before_hash, (
        "a /config/codex-*-model POST touched the REAL config.json -- check that "
        "proxy.CONFIG_FILE is actually being monkeypatched for this test."
    )
