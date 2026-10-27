"""LM Studio as a provider like Ollama (2026-10-01): `lmstudio/<id>` model ids, LM Studio's local OpenAI-compatible server, a live
model list that says what's loaded and what sees pictures, a pill that says why it isn't ready, Quick Test, Speeds' Where.

A real aiohttp server stands in for LM Studio (tests/fixtures/courier_fakes.py, with LM Studio's real routes: `/api/v0/models`,
`/v1/models`, `/v1/chat/completions`); the real LM Studio on the dev device is never touched by the suite (conftest isolates it).
"""
import asyncio
import json
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

from conftest import REAL_FETCH_LMSTUDIO_MODELS
from tests.fixtures.courier_fakes import FakeLmStudio, free_port

LIB = [
    {"type": "llm", "modelKey": "google/gemma-4-e4b", "displayName": "Gemma 4 E4B", "path": "google/gemma-4-e4b", "sizeBytes": 5_335_366_779},
    {"type": "llm", "modelKey": "google/gemma-4-12b-qat", "displayName": "Gemma 4 12B QAT", "path": "google/gemma-4-12b-qat", "sizeBytes": 7_151_067_268, "vision": True},
    {"type": "embedding", "modelKey": "text-embedding-nomic-embed-text-v1.5", "displayName": "Nomic", "path": "x", "sizeBytes": 84_106_624},
]


@pytest_asyncio.fixture
async def lms(proxy_module, monkeypatch):
    f = await FakeLmStudio(models=[dict(m) for m in LIB]).start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", f.base + "/v1")
    proxy_module._lmstudio_cache.clear()
    yield f
    await f.stop()


@pytest.fixture
def live_fetch(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "_fetch_lmstudio_models", REAL_FETCH_LMSTUDIO_MODELS)


@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


def chat(model, text="hello", stream=False, image=False, **extra):
    content = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}] if image else text
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": content}], **extra}


# ---- the prefix ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestPrefix:
    async def test_the_prefix_and_the_id_after_it(self, proxy_module):
        assert proxy_module.LMSTUDIO_PREFIX == "lmstudio/"
        assert proxy_module.is_lmstudio_model("lmstudio/qwen/qwen3-4b") and proxy_module.is_lmstudio_model("LMStudio/x")
        assert not proxy_module.is_lmstudio_model("ollama/x") and not proxy_module.is_lmstudio_model("local/x")
        assert proxy_module.strip_lmstudio_prefix("lmstudio/google/gemma-4-e4b") == "google/gemma-4-e4b"
        assert proxy_module._resolve_model_name("lmstudio/google/gemma-4-e4b") == "google/gemma-4-e4b"

    async def test_a_slash_id_is_claimed_not_an_unknown_provider(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("lmstudio/qwen/qwen3-4b") is False
        assert proxy_module.is_unclaimed_slash_model("qwen/qwen3-4b") is True

    async def test_lmstudio_cannot_be_a_nickname_for_another_pc(self, proxy_module):
        assert proxy_module._nickname_problem("lmstudio") and proxy_module._nickname_problem("LMStudio")
        assert proxy_module._nickname_problem("den-pc") is None

    async def test_it_shares_its_default_address_with_the_codex_preset_and_leaves_that_preset_alone(self, proxy_module):
        assert proxy_module.LMSTUDIO_DEFAULT_BASE_URL == proxy_module.CODEX_LMSTUDIO_BASE_URL == "http://localhost:1234/v1"
        assert proxy_module._CODEX_PRESET_DEFAULT_NAMES["lmstudio"] == "LM Studio"

    async def test_the_default_model_can_be_one_of_its_models(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "_default_claude_model", lambda: "lmstudio/google/gemma-4-e4b")
        assert proxy_module._default_provider_id() == "lmstudio"

    async def test_it_has_its_own_request_line(self, proxy_module):
        assert proxy_module._REQUEST_LINE_LABELS["lmstudio"] == "LM Studio"
        assert ("LMStudio", "lmstudio", "LM Studio", 0) in proxy_module._REQUEST_LINE_PROVIDERS


@pytest.mark.asyncio
class TestAddress:
    @pytest.mark.parametrize("typed,want", [
        ("localhost:1234", "http://localhost:1234/v1"), ("http://localhost:1234", "http://localhost:1234/v1"),
        ("http://192.168.1.5:1234/v1", "http://192.168.1.5:1234/v1"), ("  192.168.1.5  ", "http://192.168.1.5/v1"),
        ("https://lm.example:8443/", "https://lm.example:8443/v1")])
    async def test_what_a_player_types_becomes_a_base_url(self, proxy_module, typed, want):
        assert proxy_module.normalize_lmstudio_address(typed) == want

    @pytest.mark.parametrize("typed", ["", "   ", "ftp://x", "http://:1234", "localhost:abc"])
    async def test_nonsense_is_refused_in_plain_words(self, proxy_module, typed):
        with pytest.raises(ValueError) as e:
            proxy_module.normalize_lmstudio_address(typed)
        assert str(e.value)


# ---- the model list -------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestModelList:
    async def test_it_lists_chat_models_and_says_which_are_loaded_and_which_see_pictures(self, proxy_module, lms, live_fetch):
        lms.loaded["google/gemma-4-e4b"] = "google/gemma-4-e4b"
        models = await proxy_module._fetch_lmstudio_models(proxy_module.lmstudio_base_url, force=True)
        assert {m["id"]: (m["loaded"], m["vision"]) for m in models} == {"google/gemma-4-e4b": (True, False), "google/gemma-4-12b-qat": (False, True)}, \
            "the embedding model isn't a chat model: left out"

    async def test_a_server_that_isnt_running_is_none_not_a_crash_and_is_quick(self, proxy_module, live_fetch):
        import time
        t0 = time.monotonic()
        assert await proxy_module._fetch_lmstudio_models(f"http://127.0.0.1:{free_port()}/v1", force=True) is None
        assert time.monotonic() - t0 < 3

    async def test_an_older_lm_studio_without_the_richer_route_still_lists_its_models(self, proxy_module, live_fetch):
        app = web.Application()

        async def v1(request): return web.json_response({"data": [{"id": "qwen/qwen3-4b"}, {"id": "text-embedding-nomic-embed-text-v1.5"}]})
        app.router.add_get("/v1/models", v1)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        port = free_port()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            models = await proxy_module._fetch_lmstudio_models(f"http://127.0.0.1:{port}/v1", force=True)
            assert [(m["id"], m["loaded"], m["vision"]) for m in models] == [("qwen/qwen3-4b", False, False)]
        finally:
            await runner.cleanup()

    async def test_it_is_cached_briefly(self, proxy_module, lms, live_fetch):
        a = await proxy_module._fetch_lmstudio_models(proxy_module.lmstudio_base_url, force=True)
        lms.library.append({"type": "llm", "modelKey": "new/model", "displayName": "n", "path": "n", "sizeBytes": 1})
        assert await proxy_module._fetch_lmstudio_models(proxy_module.lmstudio_base_url) == a
        assert len(await proxy_module._fetch_lmstudio_models(proxy_module.lmstudio_base_url, force=True)) == len(a) + 1

    async def test_rows_carry_the_prefixed_id_the_name_and_a_note(self, proxy_module):
        assert proxy_module._lmstudio_row({"id": "google/gemma-4-12b-qat", "loaded": True, "vision": True}) == \
            ["lmstudio/google/gemma-4-12b-qat", "google/gemma-4-12b-qat", "Loaded · Chat · Vision"]
        assert proxy_module._lmstudio_row({"id": "a", "loaded": False, "vision": False})[2] == ""


# ---- the pill ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestStatus:
    async def test_ready_says_how_many_models_and_how_many_are_loaded(self, proxy_module, lms, live_fetch):
        s = await proxy_module._lmstudio_status(force=True)
        assert (s["state"], s["status"], s["testable"]) == ("ok", "2 models", True) and len(s["models"]) == 2
        lms.loaded["google/gemma-4-e4b"] = "google/gemma-4-e4b"
        assert (await proxy_module._lmstudio_status(force=True))["status"] == "2 models, 1 loaded"

    async def test_one_model_is_singular(self, proxy_module, lms, live_fetch):
        lms.library[:] = [LIB[0]]
        assert (await proxy_module._lmstudio_status(force=True))["status"] == "1 model"

    async def test_running_with_nothing_downloaded_says_so(self, proxy_module, lms, live_fetch):
        lms.library[:] = [LIB[2]]
        s = await proxy_module._lmstudio_status(force=True)
        assert (s["state"], s["status"], s["testable"]) == ("warn", "No models downloaded", False)
        assert s["empty"] == "LM Studio is running, but has no models. Download one in LM Studio, then press Check for models."

    async def test_not_installed_is_grey_and_says_nothing_more(self, proxy_module):
        s = await proxy_module._lmstudio_status(force=True)
        assert (s["state"], s["status"], s["testable"], s["canStart"], s["empty"]) == ("off", "Not installed", False, False, "")

    async def test_installed_but_off_says_so_and_offers_the_start_button_when_its_tool_is_here(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "_lmstudio_adapter", lambda base: SimpleNamespace(cli="lms", _app_installed=lambda: True))
        s = await proxy_module._lmstudio_status(force=True)
        assert (s["state"], s["status"], s["canStart"]) == ("off", "LM Studio isn't running", True)
        assert s["empty"] == "LM Studio's server is off. Start it here or in LM Studio, then press Check for models."

    async def test_installed_but_off_without_its_tool_says_what_to_do_and_has_no_button(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "_lmstudio_adapter", lambda base: SimpleNamespace(cli=None, _app_installed=lambda: True))
        s = await proxy_module._lmstudio_status(force=True)
        assert (s["status"], s["canStart"]) == ("LM Studio isn't running", False)
        assert s["empty"] == "LM Studio's server is off. Turn it on in LM Studio, then press Check for models."

    async def test_a_changed_address_that_nobody_answers_says_what_to_check(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", "http://192.168.1.77:1234/v1")
        s = await proxy_module._lmstudio_status(force=True)
        assert (s["state"], s["status"], s["address"]) == ("off", "Can't reach it", "http://192.168.1.77:1234")
        assert s["empty"] == "Nothing answered. Make sure LM Studio's server is on, then press Check for models."

    async def test_the_providers_card_entry_sits_after_ollama_and_is_keyless(self, proxy_module, lms, live_fetch):
        providers = await proxy_module._dashboard_providers()
        ids = [p["id"] for p in providers]
        assert ids.index("lmstudio") == ids.index("ollama") + 1
        p = providers[ids.index("lmstudio")]
        assert (p["name"], p["keyless"], p["hasKey"], p["live"], p["state"], p["status"]) == ("LM Studio", True, False, True, "ok", "2 models")
        assert p["refreshEndpoint"] == "/config/lmstudio-models" and p["help"] == "Connects to LM Studio on this device. No subscription needed."
        assert [m[0] for m in p["models"]] == ["lmstudio/google/gemma-4-12b-qat", "lmstudio/google/gemma-4-e4b"]

    async def test_quick_test_can_pick_it_only_when_it_can_answer(self, proxy_module, lms, live_fetch):
        p = next(x for x in await proxy_module._dashboard_providers() if x["id"] == "lmstudio")
        assert p["testable"] is True
        lms.library.clear()
        proxy_module._lmstudio_cache.clear()
        p = next(x for x in await proxy_module._dashboard_providers() if x["id"] == "lmstudio")
        assert p["testable"] is False

    async def test_a_long_list_is_grouped_by_family_like_the_others(self, proxy_module, lms, live_fetch):
        for i in range(8):
            lms.library.append({"type": "llm", "modelKey": f"qwen/qwen3-{i}b", "displayName": "q", "path": "q", "sizeBytes": 1})
        p = next(x for x in await proxy_module._dashboard_providers() if x["id"] == "lmstudio")
        assert len(p["models"]) == 10          # (the page groups any provider's list longer than 5 by family: model_groups_ui.py)


# ---- the routes the panel calls ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestRoutes:
    async def test_refresh_returns_the_same_shape_the_card_builds(self, client, lms, live_fetch):
        d = (await client.get("/config/lmstudio-models")).json()
        assert d["state"] == "ok" and d["status"] == "2 models" and d["canStart"] is False and d["defaultAddress"] == "http://localhost:1234"

    async def test_saving_and_resetting_the_address(self, client, proxy_module, lms, live_fetch):
        r = await client.post("/config/lmstudio-address", json={"address": lms.base.replace("http://", "")})
        assert r.status_code == 200 and r.json()["state"] == "ok" and r.json()["address"] == lms.base
        assert proxy_module.lmstudio_base_url == lms.base + "/v1" and proxy_module._load_config()["lmstudio_base_url"] == lms.base + "/v1"
        back = await client.post("/config/lmstudio-address", json={"address": ""})
        assert back.status_code == 200 and proxy_module.lmstudio_base_url == proxy_module.LMSTUDIO_DEFAULT_BASE_URL
        assert "lmstudio_base_url" not in proxy_module._load_config()

    async def test_a_bad_address_is_a_plain_400_and_changes_nothing(self, client, proxy_module):
        before = proxy_module.lmstudio_base_url
        r = await client.post("/config/lmstudio-address", json={"address": "ftp://nope"})
        assert r.status_code == 400 and r.json()["error"]["message"] == "That address doesn't look right. Try localhost:1234."
        assert proxy_module.lmstudio_base_url == before

    async def test_start_runs_lm_studios_own_server_start_then_reports_the_new_state(self, client, proxy_module, lms, live_fetch, monkeypatch):
        started = []

        class A:
            cli = "lms"
            def _app_installed(self): return True
            async def start(self): started.append(1)
        monkeypatch.setattr(proxy_module, "_lmstudio_adapter", lambda base: A())
        r = await client.post("/config/lmstudio-start")
        assert r.status_code == 200 and started == [1] and r.json()["state"] == "ok"

    async def test_start_without_its_tool_says_what_to_click(self, client):
        r = await client.post("/config/lmstudio-start")
        assert r.status_code == 404 and "Install CLI" in r.json()["error"]["message"]

    async def test_a_failed_start_is_its_own_plain_reason(self, client, proxy_module, monkeypatch):
        import courier_adapters as ca

        class A:
            cli = "lms"
            def _app_installed(self): return True
            async def start(self): raise ca.AdapterError(502, "LM Studio's local server failed to start.")
        monkeypatch.setattr(proxy_module, "_lmstudio_adapter", lambda base: A())
        r = await client.post("/config/lmstudio-start")
        assert r.status_code == 502 and r.json()["error"]["message"] == "LM Studio's local server failed to start."

    async def test_its_models_are_in_the_v1_models_list(self, client, lms, live_fetch):
        ids = {m["id"] for m in (await client.get("/v1/models")).json()["data"]}
        assert {"lmstudio/google/gemma-4-e4b", "lmstudio/google/gemma-4-12b-qat"} <= ids


# ---- chatting through it ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestChat:
    async def test_a_request_reaches_lm_studio_with_its_own_model_id(self, client, lms):
        r = await client.post("/v1/chat/completions", json=chat("lmstudio/google/gemma-4-e4b", "hi there"))
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "lmstudio:hi there"
        assert lms.chat_calls[-1]["model"] == "google/gemma-4-e4b", "the lmstudio/ prefix is stripped before the call"

    async def test_streaming_comes_through(self, client, lms):
        async with client.stream("POST", "/v1/chat/completions", json=chat("lmstudio/google/gemma-4-e4b", "stream me", stream=True)) as r:
            assert r.status_code == 200
            text = ""
            async for line in r.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    text += json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
        assert text == "lmstudio:stream me"

    async def test_pictures_pass_through_untouched(self, client, lms):
        r = await client.post("/v1/chat/completions", json=chat("lmstudio/google/gemma-4-12b-qat", "what is this", image=True))
        assert r.json()["choices"][0]["message"]["content"] == "lmstudio:[picture] what is this"
        sent = lms.chat_calls[-1]["messages"][-1]["content"]
        assert sent[1]["image_url"]["url"] == "data:image/png;base64,AAAA"

    async def test_reasoning_knobs_lm_studio_doesnt_take_are_dropped(self, client, lms):
        await client.post("/v1/chat/completions", json=chat("lmstudio/google/gemma-4-e4b", thinking={"budget": 1}, reasoning="high"))
        assert "thinking" not in lms.chat_calls[-1] and "reasoning" not in lms.chat_calls[-1]

    async def test_a_server_that_is_off_is_a_plain_502_so_skyrimnet_can_fall_back(self, client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", f"http://127.0.0.1:{free_port()}/v1")
        r = await client.post("/v1/chat/completions", json=chat("lmstudio/google/gemma-4-e4b"))
        assert r.status_code == 502 and "Couldn't reach LM Studio" in (r.json().get("detail") or r.json()["error"]["message"])

    async def test_a_chat_is_recorded_as_lm_studio_and_speeds_calls_it_this_pc(self, client, proxy_module, lms):
        await client.post("/v1/chat/completions", json=chat("lmstudio/google/gemma-4-e4b"))
        assert proxy_module._speed_where("google/gemma-4-e4b", "LM Studio") == "This device"
        assert proxy_module._speed_where("google/gemma-4-e4b", None) == "Cloud", "(old rows with no provider stay Cloud, as before)"
        rows = (await client.get("/stats/speeds?range=today&pc=this")).json()["models"]
        assert [m["model"] for m in rows] == ["google/gemma-4-e4b"] and rows[0]["where"] == "This device"
        assert (await client.get("/stats/speeds?range=today&pc=cloud")).json()["models"] == []

    async def test_the_health_card_names_it_lm_studio(self, client, proxy_module, lms, live_fetch):
        await client.post("/v1/chat/completions", json=chat("lmstudio/google/gemma-4-e4b"))
        cards = (await client.get("/stats/speeds/health")).json()["cards"]
        assert [c["provider"] for c in cards] == ["LM Studio"] and cards[0]["where"] == "This device"
        ready = (await client.get("/stats/speeds/health?show=ready")).json()["cards"]
        assert "LM Studio" in [c["provider"] for c in ready]

    async def test_the_speeds_filter_shows_when_lm_studio_has_models_even_with_nothing_else_local(self, client, lms, live_fetch):
        assert (await client.get("/stats/speeds/pcs")).json()["show"] is True


# ---- the page ----------------------------------------------------------------------------------------------------------
class TestPage:
    def test_the_page_carries_it_and_the_hook_in_the_panel(self, test_client):
        html = test_client.get("/").text
        for needle in ("function lmsPanelExtras", "lmsSaveAddress", "lmsStart", "typeof lmsPanelExtras === 'function' ? lmsPanelExtras(p)"):
            assert needle in html, needle
        assert "Start LM Studio\\'s server" in html and "Use the default</button>" in html and "LM Studio\\'s address" in html and "function lmsSteps" in html

    def test_the_quick_test_hint_names_lm_studio(self, test_client):
        # The Quick Test panel lives in quicktest_ui now (its model is qtModelId, not MODEL).
        import quicktest_ui
        assert "qtModelId.startsWith('lmstudio/')" in quicktest_ui.JS and "If you use Ollama, LM Studio, Codex, or Antigravity" in quicktest_ui.JS
        html = test_client.get("/").text
        assert "qtModelId.startsWith('lmstudio/')" in html


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
class TestPanelScript:
    def run(self, entry: dict) -> str:
        import lmstudio_ui
        src = ("function esc(s){return String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}\n"
               + lmstudio_ui.LMSTUDIO_JS + f"\nconsole.log(lmsPanelExtras({json.dumps(entry)}));")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
        try:
            return subprocess.run([NODE, f.name], capture_output=True, timeout=30).stdout.decode("utf-8")
        finally:
            import os
            os.unlink(f.name)

    def test_nothing_for_other_providers(self):
        assert self.run({"id": "ollama"}).strip() == ""

    def test_the_address_box_and_the_default_note(self):
        out = self.run({"id": "lmstudio", "address": "", "defaultAddress": "http://localhost:1234", "canStart": False})
        assert 'id="lmsAddr"' in out and "Save" in out and "Use the default" not in out and "Start LM Studio" not in out
        assert "Change this only if" not in out and "Its server is at" not in out, "the note under the address box is gone"

    def test_a_changed_address_offers_the_default_back_and_start_shows_only_when_it_can(self):
        out = self.run({"id": "lmstudio", "address": "http://192.168.1.5:1234", "defaultAddress": "http://localhost:1234", "canStart": True})
        assert 'value="http://192.168.1.5:1234"' in out and "Use the default" in out and "Start LM Studio's server" in out
