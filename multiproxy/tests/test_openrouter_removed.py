"""OpenRouter was removed from MultiProxy on 2026-09-30 (SkyrimNet's own OpenRouter handling --
smart routing, community routing, provider mixer -- does a better job when SkyrimNet talks to
OpenRouter directly; going through MultiProxy hid all of it). These tests pin the new behaviour:
no OpenRouter anywhere a player can see, `provider/model` names get a clear HTTP error, and every
other provider is unaffected.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

MESSAGE = ("MultiProxy doesn't connect to OpenRouter anymore. Point SkyrimNet at OpenRouter directly "
           "for this model, or pick one of MultiProxy's providers.")


class TestSlashNamesGetAClearError:
    @pytest.mark.parametrize("model", ["google/gemma-4-31b-it", "anthropic/claude-haiku-4.5", "openrouter/qwen/qwen3-vl", "some/model"])
    def test_non_streaming_is_a_real_http_error_with_the_plain_message(self, test_client, model):
        r = test_client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404
        assert r.json()["error"]["message"] == MESSAGE

    def test_streaming_is_a_real_http_error_not_a_200_stream(self, test_client):
        r = test_client.post("/v1/chat/completions", json={
            "model": "google/gemma-4-31b-it", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404
        assert r.json()["error"]["message"] == MESSAGE

    def test_a_round_robin_list_with_one_slash_name_still_errors_when_it_is_picked(self, test_client):
        r = test_client.post("/v1/chat/completions", json={
            "model": "google/gemma-4-31b-it", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404

    def test_the_error_is_recorded_nowhere_as_an_openrouter_call(self, proxy_module, test_client):
        test_client.post("/v1/chat/completions", json={"model": "some/model", "messages": [{"role": "user", "content": "hi"}]})
        assert "openrouter" not in json.dumps(proxy_module.speed_tracker.snapshot()).lower()

    def test_no_key_attribute_or_calls_remain(self, proxy_module):
        for name in ("openrouter_api_key", "call_openrouter_direct", "call_openrouter_streaming",
                     "_openrouter_headers", "OPENROUTER_REFERER", "call_jev_via_openrouter", "is_openrouter_model"):
            assert not hasattr(proxy_module, name), name


class TestEveryOtherProviderIsUnaffected:
    @pytest.mark.parametrize("model,attr,fn", [
        ("glmzai/glm-4.6", "glm_api_key", "call_glm_direct"),
        ("nano/gpt-4o-mini", "nano_api_key", "call_nano_direct"),
        ("openai/gpt-4o-mini", "openai_api_key", "call_openai_direct"),
        ("deepseek/deepseek-flash", "deepseek_api_key", "call_deepseek_direct"),
        ("kilo/anthropic/claude-sonnet-5", "kilo_api_key", "call_kilo_direct"),
        ("gemini/gemini-3.8-flash", "gemini_api_key", "call_gemini_direct"),
        ("ollama/llama3", "ollama_api_key", "call_ollama_direct"),
        ("local/whatever", None, "call_local_direct"),
    ])
    def test_prefixed_providers_still_route_to_their_own_call(self, proxy_module, test_client, model, attr, fn):
        mock = AsyncMock(return_value=("ok", {"prompt_tokens": 1, "completion_tokens": 1}))
        with patch.object(proxy_module, fn, mock):
            r = test_client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "ok"
        mock.assert_awaited_once()


class TestNothingOpenRouterOnTheDashboardOrApis:
    def test_no_provider_entry(self, proxy_module):
        providers = asyncio.run(proxy_module._dashboard_providers())
        assert "openrouter" not in [p["id"] for p in providers]
        assert "openrouter" not in json.dumps(providers).lower()

    def test_dashboard_page_has_no_openrouter_pill_card_or_key_route(self, test_client):
        html = test_client.get("/").text
        assert "openrouter-key" not in html
        assert "/config/openrouter" not in html
        assert "openrouter.ai" not in html.lower()

    def test_the_key_route_is_gone(self, test_client):
        assert test_client.post("/config/openrouter-key", json={"key": "x"}).status_code in (404, 405)

    def test_models_and_health_dont_mention_it(self, test_client):
        assert "openrouter" not in test_client.get("/v1/models").text.lower()
        assert "openrouter" not in test_client.get("/health").text.lower()

    def test_a_key_left_in_config_json_is_ignored_and_left_alone(self, proxy_module, monkeypatch, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"openrouter_api_key": "sk-or-old", "glm_api_key": "g"}))
        monkeypatch.setattr(proxy_module, "CONFIG_FILE", str(cfg))
        assert proxy_module._load_config()["openrouter_api_key"] == "sk-or-old"
        assert not hasattr(proxy_module, "openrouter_api_key")
        proxy_module._save_config({**proxy_module._load_config(), "x": 1})
        assert json.loads(cfg.read_text())["openrouter_api_key"] == "sk-or-old", "saving other settings must not erase it"

    def test_new_tagline(self, test_client):
        html = test_client.get("/").text
        assert "Connect any AI to SkyrimNet—cloud or local, paid or free." in html
        assert "Connect your AI of choice to SkyrimNet" not in html

    def test_example_config_and_ini_no_longer_list_it(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        assert "openrouter" not in (root / "config.example.json").read_text().lower()
        assert "openrouter" not in (root / "multiproxy.ini.example").read_text().lower()

    def test_an_old_proxy_ini_with_an_openrouter_line_is_harmless(self, proxy_module):
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read_string("[Concurrency]\nOpenRouter = 4\nGLM = 2\n")
        limits, _ = proxy_module._read_concurrency_ini(cfg)
        assert limits["glm"] == 2 and "openrouter" not in limits

    def test_typesafe_card_no_longer_mentions_it(self, proxy_module):
        card = next(p for p in asyncio.run(proxy_module._dashboard_providers()) if p["id"] == "typesafe")
        assert "OpenRouter" not in card["help"]
        # "Simple is the word" dropped the old fall-back sentence; the card now just says what TypeSafe is for.
        assert "answers quick decisions" in card["help"] and "It does not write dialogue" in card["help"]

    def test_codex_custom_provider_form_offers_no_openrouter_preset_or_example(self, test_client):
        html = test_client.get("/").text
        assert "My OpenRouter" not in html and "https://openrouter.ai/api/v1" not in html


class TestJevDecisionOrder:
    def test_typesafe_first(self, proxy_module):
        import inspect
        src = inspect.getsource(proxy_module._handle_jev_request)
        assert src.index("if typesafe_api_key:") < src.index("stand_in = await call_jev_stand_in")
        assert "openrouter" not in src.split("\"\"\"")[2].lower(), "the code (not just the docstring) must not mention it"

    def test_stand_in_then_a_clear_error(self, test_client):
        r = test_client.post("/v1/chat/completions", json={
            "model": "typesafe/jev-1.13", "questions": {"q": {"type": "noul", "instructions": "?"}}, "state": {}})
        assert r.status_code == 500
        assert r.json()["error"]["message"] == (
            "No TypeSafe key found, and no other AI is ready. Add a key on the dashboard.")
