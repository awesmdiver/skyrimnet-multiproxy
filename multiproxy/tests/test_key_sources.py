"""Cloudflare Workers AI, Mistral and Groq as ready-made "paste a key" choices (2026-10-06), and the sources that must not be offered any more.

Stand-ins only: a mocked HTTP session, no real keys, nothing sent anywhere.
"""
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import key_sources as ks

ROOT = Path(__file__).resolve().parent.parent
ACCOUNT = "0123456789abcdef0123456789abcdef"
CF_KEY = f"{ACCOUNT}:cf-token-0123456789"


def _ok_response(text="pong"):
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {}})
    resp.text = AsyncMock(return_value="")
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _session(resp):
    s = MagicMock()
    s.post = MagicMock(return_value=resp)
    s.close = AsyncMock()
    return s


async def _chat(proxy_module, source_id, key, model, **params):
    session = _session(_ok_response())
    with patch.dict(proxy_module._ks_keys, {source_id: key}), patch.object(proxy_module, "auth", MagicMock(session=None)), patch("aiohttp.ClientSession", return_value=session):
        direct, _ = proxy_module._ks_call_pair(ks.BY_ID[source_id])
        await direct(None, [{"role": "user", "content": "hi"}], model, 100, **params)
    return session.post.call_args


# ---- the picker -----------------------------------------------------------------------------------------------------------------------------------
class TestThePicker:
    @pytest.mark.asyncio
    async def test_the_three_choices_come_in_order_right_after_the_other_keys(self, proxy_module):
        entries = await proxy_module._dashboard_providers()
        ids = [e["id"] for e in entries]
        assert ids[ids.index("continuum") + 1:ids.index("continuum") + 4] == ["cloudflare", "mistral", "groq"]
        by = {e["id"]: e for e in entries}
        for sid in ("cloudflare", "mistral", "groq"):
            e = by[sid]
            assert (e["group"], e["tag"]) == ("key", "free allowance") and e["pill"] == ks.BY_ID[sid].pill
            assert e["keyEndpoint"] == f"/config/{sid}-key" and e["state"] == "off" and e["status"] == "No key yet" and e["hasKey"] is False and e["testable"] is False
            assert e["models"] and all(len(m) == 3 for m in e["models"]) and e["help"] and "(More in Help)" in e["help"]
        assert "refreshEndpoint" not in by["cloudflare"] and by["mistral"]["refreshEndpoint"] == "/config/mistral-models" and by["groq"]["live"] is True

    def test_the_page_and_get_started_know_them(self, test_client):
        html = test_client.get("/").text
        assert "'typesafe', 'perplexity', 'cloudflare', 'mistral', 'groq']" in html                 # they get the same "Start here" steps as every key provider
        assert "ids: ['glm', 'nano', 'openai', 'deepseek', 'gemini', 'xai', 'continuum', 'kilo', 'typesafe', 'perplexity', 'cloudflare', 'mistral', 'groq']" in html
        assert "Cloudflare, Mistral and Groq, which have a small free allowance" in html

    def test_every_one_has_a_help_page(self):
        import help_content
        pages = {m[1] for m in help_content.PAGES.values() if m[1]}
        assert {"cloudflare", "mistral", "groq"} <= pages

    def test_the_notes_say_plainly_what_they_cost_you(self):
        by = ks.BY_ID
        assert "train its AI" in by["mistral"].help_html and "Privacy settings" in by["mistral"].help_html
        assert "very small" in by["groq"].help_html and "few hundred replies a day" in by["cloudflare"].help_html
        for s in ks.SOURCES:
            assert s.help_html.endswith("(More in Help)")


# ---- a preset fills the address (and nothing else can) -----------------------------------------------------------------------------------
class TestTheAddress:
    @pytest.mark.asyncio
    async def test_each_source_calls_its_own_address_with_its_own_key(self, proxy_module):
        call = await _chat(proxy_module, "mistral", "mk-123456789", "mistral-small-2603")
        assert call.args[0] == "https://api.mistral.ai/v1/chat/completions" and call.kwargs["headers"]["Authorization"] == "Bearer mk-123456789"
        assert call.kwargs["json"]["model"] == "mistral-small-2603" and call.kwargs["json"]["max_tokens"] == 100
        call = await _chat(proxy_module, "groq", "gk-123456789", "openai/gpt-oss-20b")
        assert call.args[0] == "https://api.groq.com/openai/v1/chat/completions" and call.kwargs["headers"]["Authorization"] == "Bearer gk-123456789"
        assert "max_completion_tokens" in call.kwargs["json"] and "max_tokens" not in call.kwargs["json"], "Groq wants the newer name"

    @pytest.mark.asyncio
    async def test_cloudflare_takes_the_account_and_the_token_from_one_box(self, proxy_module):
        call = await _chat(proxy_module, "cloudflare", CF_KEY, "@cf/qwen/qwen3-30b-a3b-fp8")
        assert call.args[0] == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1/chat/completions"
        assert call.kwargs["headers"]["Authorization"] == "Bearer cf-token-0123456789" and ACCOUNT not in str(call.kwargs["headers"])
        assert call.kwargs["json"]["model"] == "@cf/qwen/qwen3-30b-a3b-fp8"

    def test_a_malformed_box_can_only_ever_reach_cloudflare(self):
        base, token = ks.split_key(ks.BY_ID["cloudflare"], "just-a-token")
        assert base.startswith("https://api.cloudflare.com/client/v4/accounts/") and token == "just-a-token"
        assert ks.split_key(ks.BY_ID["mistral"], "abc") == ("https://api.mistral.ai/v1", "abc")

    @pytest.mark.asyncio
    async def test_only_the_settings_each_service_takes_are_sent(self, proxy_module):
        call = await _chat(proxy_module, "mistral", "mk-123456789", "mistral-small-2603", temperature=0.7, top_k=40, seed=7, frequency_penalty=0.1, logprobs=True)
        sent = call.kwargs["json"]
        assert sent["temperature"] == 0.7 and sent["frequency_penalty"] == 0.1 and not ({"top_k", "seed", "logprobs"} & set(sent))
        call = await _chat(proxy_module, "groq", "gk-123456789", "openai/gpt-oss-20b", n=2, logprobs=True, temperature=0.5)
        sent = call.kwargs["json"]
        assert sent["temperature"] == 0.5 and not ({"n", "logprobs"} & set(sent))

    @pytest.mark.asyncio
    async def test_no_key_is_a_plain_error_that_names_the_source(self, proxy_module):
        from fastapi import HTTPException
        direct, _ = proxy_module._ks_call_pair(ks.BY_ID["groq"])
        with pytest.raises(HTTPException) as e:
            await direct(None, [{"role": "user", "content": "hi"}], "openai/gpt-oss-20b", 50)
        assert e.value.status_code == 500 and "Groq" in e.value.detail

    def test_skyrimnet_names_route_to_them_and_unknown_slash_names_still_do_not(self, proxy_module):
        assert proxy_module._resolve_model_name("groq/openai/gpt-oss-120b") == "openai/gpt-oss-120b"
        assert proxy_module._resolve_model_name("cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast") == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        assert proxy_module._resolve_model_name("mistral/mistral-small-2603") == "mistral-small-2603"
        assert not proxy_module.is_unclaimed_slash_model("groq/openai/gpt-oss-20b") and proxy_module.is_unclaimed_slash_model("google/gemma-4-31b-it")
        assert proxy_module._provider_label_for("mistral/x") == "Mistral" and proxy_module._ai_class("groq/x")[0] == "free"

    @pytest.mark.asyncio
    async def test_a_chat_request_reaches_the_right_one_and_is_counted_on_its_own_line(self, proxy_module, test_client):
        fake = AsyncMock(return_value=("hello", {"prompt_tokens": 3, "completion_tokens": 1, "finish_reason": "stop"}))
        with patch.dict(proxy_module._ks_keys, {"groq": "gk-123456789"}), patch.object(proxy_module, "_call_openai_compatible_direct", fake):
            r = test_client.post("/v1/chat/completions", json={"model": "groq/openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 20})
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hello"
        args = fake.call_args.args
        assert args[0] == "Groq" and args[1] == "https://api.groq.com/openai/v1" and args[7] == "openai/gpt-oss-20b"


# ---- the key and the model list -----------------------------------------------------------------------------------------------------------------
class TestTheKeyAndTheList:
    def test_save_and_clear_a_key(self, proxy_module, test_client):
        r = test_client.post("/config/mistral-key", json={"key": "  mk-123456789  "})
        assert r.json() == {"status": "saved"} and proxy_module._ks_keys["mistral"] == "mk-123456789"
        assert proxy_module._load_config()["mistral_api_key"] == "mk-123456789"
        assert test_client.get("/health").json()["mistral_configured"] is True
        assert test_client.post("/config/mistral-key", json={"key": ""}).json() == {"status": "cleared"}
        assert proxy_module._ks_keys["mistral"] is None and "mistral_api_key" not in proxy_module._load_config()

    def test_cloudflares_box_must_hold_an_account_id_a_colon_and_a_token(self, proxy_module, test_client):
        for bad in ("only-a-token", "0123:token", f"{ACCOUNT} token-0123456789"):
            r = test_client.post("/config/cloudflare-key", json={"key": bad})
            assert r.status_code == 400 and r.json()["detail"] == ks.ACCOUNT_KEY_WRONG
        assert proxy_module._ks_keys["cloudflare"] is None
        assert test_client.post("/config/cloudflare-key", json={"key": CF_KEY}).json() == {"status": "saved"}
        e = next(x for x in __import__("asyncio").run(proxy_module._dashboard_providers()) if x["id"] == "cloudflare")
        assert e["state"] == "ok" and e["status"] == "Key saved" and e["testable"] is True and re.match(e["keyShape"], CF_KEY)

    @pytest.mark.asyncio
    async def test_the_live_list_keeps_only_chat_models_and_says_how_many(self, proxy_module):
        listing = [{"id": "mistral-small-2603"}, {"id": "mistral-embed"}, {"id": "mistral-ocr-latest"}, {"id": "voxtral-mini"}, {"id": "ministral-3-8b-2512"}]
        with patch.dict(proxy_module._ks_keys, {"mistral": "mk-123456789"}), patch.object(proxy_module, "_get_key_model_list", AsyncMock(return_value=listing)):
            proxy_module._ks_models_cache.clear()
            st = await proxy_module._ks_status(ks.BY_ID["mistral"], force=True)
        assert st["state"] == "ok" and st["status"] == "Key saved: 2 models available" and [m[0] for m in st["models"]] == ["mistral/ministral-3-8b-2512", "mistral/mistral-small-2603"]

    @pytest.mark.asyncio
    async def test_a_refused_key_and_an_unreachable_service_are_told_apart(self, proxy_module):
        src = ks.BY_ID["groq"]
        with patch.dict(proxy_module._ks_keys, {"groq": "gk-123456789"}):
            proxy_module._ks_models_cache.clear()
            with patch.object(proxy_module, "_get_key_model_list", AsyncMock(return_value=None)):
                proxy_module._model_list_problems[("groq", "gk-123456789")] = "refused"
                st = await proxy_module._ks_status(src, force=True)
                assert st["state"] == "warn" and "Groq didn't accept this key" in st["status"] and "console.groq.com" in st["status"] and st["models"][0][0] == "groq/openai/gpt-oss-120b"
                proxy_module._model_list_problems[("groq", "gk-123456789")] = "unreachable"
                st = await proxy_module._ks_status(src, force=True)
                assert st["state"] == "warn" and "couldn't reach the live model list" in st["status"]

    @pytest.mark.asyncio
    async def test_cloudflare_offers_its_own_list_and_never_asks_a_server_for_one(self, proxy_module):
        with patch.dict(proxy_module._ks_keys, {"cloudflare": CF_KEY}), patch.object(proxy_module, "_get_key_model_list", AsyncMock(side_effect=AssertionError("no list route"))):
            st = await proxy_module._ks_status(ks.BY_ID["cloudflare"], force=True)
        assert st["state"] == "ok" and st["live"] is False and len(st["models"]) == 7 and st["any"] == "cloudflare/@cf/MAKER/MODEL"

    @pytest.mark.asyncio
    async def test_the_models_skyrimnet_can_read_include_a_source_once_it_has_a_key(self, proxy_module, test_client):
        before = {m["id"] for m in test_client.get("/v1/models").json()["data"]}
        assert not any(i.startswith("cloudflare/") for i in before)
        with patch.dict(proxy_module._ks_keys, {"cloudflare": CF_KEY}):
            proxy_module._models_forget()
            test_client.get("/v1/models")
            import asyncio
            await asyncio.sleep(0.05)
            data = test_client.get("/v1/models").json()["data"]
        assert any(m["id"].startswith("cloudflare/@cf/") and m["owned_by"] == "cloudflare" for m in data)


# ---- the sources that must not be offered ------------------------------------------------------------------------------------------------------------
DEAD = (r"github models", r"cerebras", r"chutes", r"qwen (code|oauth)", r"qwen[^.\n]{0,30}(free sign|sign-in|sign in)")


class TestTheDeadOnes:
    def _shipped_text(self, test_client) -> dict:
        files = {"the dashboard page": test_client.get("/").text, "README.md": (ROOT / "README.md").read_text(encoding="utf-8")}
        for p in sorted((ROOT / "docs" / "help").glob("*.md")):
            files[f"docs/help/{p.name}"] = p.read_text(encoding="utf-8")
        for name in ("get_started_ui.py", "key_sources.py", "help_content.py", "settings_ui.py"):
            files[name] = (ROOT / name).read_text(encoding="utf-8")
        return files

    def test_the_retired_free_sources_appear_nowhere_in_what_ships(self, test_client):
        for where, text in self._shipped_text(test_client).items():
            for pattern in DEAD:
                assert not re.search(pattern, text, re.I), f"{where} still mentions {pattern}"

    def test_nvidia_nim_is_not_offered_because_its_terms_allow_testing_only(self):
        assert all(s.id not in ("nim", "nvidia") and not s.prefix.startswith(("nim/", "nvidia/")) for s in ks.SOURCES)
        assert "internal testing and evaluation purposes, not in production" in (ROOT / "key_sources.py").read_text(encoding="utf-8")     # the reason is written down where the next person looks
