"""Quick decisions answered by a provider's own decision model (2026-10-05): Ollama's `clef` / `clef-flash` and Nano-GPT's decision models, through one registry
(decision_providers.py). The providers are stand-in servers on this device (never the real Ollama or Nano-GPT); what is checked is that a provider is offered only while its own
list says a model is for Decisions, the request in and the answer out, the passing on, the Quick Test line, the Speeds row and the saved model choice."""
import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

import decision_providers as dp
import model_kinds
from tests.test_jev_provider import REAL_JEV_REQUEST

OLLAMA_ANSWER = {"model": "clef:latest", "answers": {"intent": {"type": "choice", "choice": "greeting", "confidence": 0.97,
                                                                  "probabilities": {"complaint": 0.0, "greeting": 0.97, "other": 0.01, "question": 0.01, "request": 0.01}}},
                 "usage": {"input_tokens": 143, "output_tokens": 0}}


class Stand:
    """A stand-in for Ollama (/v1/systemone, /api/version) and for Nano-GPT's lists and System One route, on 127.0.0.1 with a spare port."""

    def __init__(self):
        self.body, self.status, self.delay, self.calls, self.sent = OLLAMA_ANSWER, 200, 0.0, 0, []
        self.version, self.nano_list = "0.35.1", {"data": []}
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def systemone(request):
            self.calls += 1
            self.sent.append(await request.json())
            await asyncio.sleep(self.delay)
            return web.json_response(self.body, status=self.status)

        async def version(request):
            return web.json_response({"version": self.version})

        async def models(request):
            return web.json_response(self.nano_list)

        async def start():
            app = web.Application()
            app.router.add_post("/v1/systemone", systemone)
            app.router.add_post("/nano/systemone", systemone)
            app.router.add_get("/api/version", version)
            app.router.add_get("/nano/models", models)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            ready.set()
        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True).start()
        ready.wait(5)

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def stand(proxy_module, monkeypatch, real_read_nano_decision_models):
    s = Stand()
    dp.CACHE.clear()
    monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", f"http://127.0.0.1:{s.port}/v1")
    monkeypatch.setattr(proxy_module, "NANO_BASE_URL", f"http://127.0.0.1:{s.port}/nano")
    monkeypatch.setattr(proxy_module, "NANO_DECISIONS_URL", f"http://127.0.0.1:{s.port}/nano/systemone")
    monkeypatch.setattr(proxy_module, "auth", MagicMock(session=None, is_ready=False))
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)
    monkeypatch.setattr(proxy_module, "_record_speed", AsyncMock())

    monkeypatch.setattr(proxy_module, "_read_nano_decision_models", real_read_nano_decision_models)
    yield s
    s.close()
    dp.CACHE.clear()
    proxy_module.decision_handles.last_answer = None
    proxy_module.decision_handles.nano_line = proxy_module.decision_handles.typesafe_line = ""
    proxy_module._save_config({"decisions": {}})


def ollama_has(proxy_module, monkeypatch, models: dict):
    """Ollama lists these models, each with its kinds ({name: kinds or None})."""
    async def tags(base, key, force=False):
        return {"models": [{"name": n, "digest": "d"} for n in models]}

    async def kinds(base, raw):
        return {m["name"]: models[m["name"]] for m in raw}
    monkeypatch.setattr(proxy_module, "_fetch_ollama_tags", tags)
    monkeypatch.setattr(proxy_module, "_ollama_kinds", kinds)


def read_all():
    asyncio.run(dp.refresh(force=True))


def deepseek(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    return patch.object(proxy_module, "call_deepseek_direct", AsyncMock(return_value=(json.dumps({"intent": "greeting"}), {"prompt_tokens": 3, "completion_tokens": 1})))


# ---- the registry's own rules -----------------------------------------------------------------------------------------------------------------------
def test_only_models_the_provider_says_are_for_decisions_are_listed():
    got = dp.decision_models([("a", ["Chat"]), ("clef:latest", ["Decisions"]), ("b", None), ("e", ["Embeddings"]), ("c", ["Chat", "Decisions"])])
    assert got == ["clef:latest", "c"]                      # an unknown kind is never guessed to be a decision model


def test_clef_flash_stays_off_until_ollama_is_newer_than_the_version_it_fails_on():
    assert not dp.ollama_model_works("clef-flash:latest", "0.35.1") and not dp.ollama_model_works("clef-flash:latest", "") and not dp.ollama_model_works("clef-flash", "0.30.0")
    assert dp.ollama_model_works("clef-flash:latest", "0.35.2") and dp.ollama_model_works("clef-flash:latest", "0.40.0-rc0")
    assert dp.ollama_model_works("clef:latest", "0.35.1") and dp.ollama_model_works("llmtech/decider:4b", "0.35.1")


def test_the_model_in_use_is_the_players_pick_when_it_is_still_listed_else_the_first():
    p = dp.DecisionProvider(id="x", name="X", choice="x", label="X", read=AsyncMock(return_value=["a", "b"]))
    dp.CACHE["x"] = (1.0, ["a", "b"])
    try:
        assert dp.chosen_model(p, {"x": "b"}) == "b" and dp.chosen_model(p, {"x": "gone"}) == "a" and dp.chosen_model(p, {}) == "a"
    finally:
        dp.CACHE.pop("x", None)


# ---- offered only with a Decisions-kind model ---------------------------------------------------------------------------------------------------------
class TestOffered:
    def test_not_offered_when_ollama_has_only_chat_models(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"qwen3:8b": ["Chat"]})
        read_all()
        p = next(x for x in test_client.get("/config/decisions/status").json()["providers"] if x["id"] == "ollama")
        assert p["ready"] is False and p["models"] == []
        assert proxy_module.decision_handles.order(None, False) is None            # nothing about the old handling changes

    def test_offered_with_its_decision_models_the_first_the_default(self, test_client, proxy_module, stand, monkeypatch):
        stand.version = "0.36.0"
        ollama_has(proxy_module, monkeypatch, {"clef-flash:latest": ["Decisions", "Vision"], "clef:latest": ["Decisions"], "qwen3:8b": ["Chat"]})
        read_all()
        p = next(x for x in test_client.get("/config/decisions/status").json()["providers"] if x["id"] == "ollama")
        assert p["ready"] is True and p["models"] == ["clef-flash:latest", "clef:latest"] and p["model"] == "clef-flash:latest" and p["label"] == "My Ollama decision model"

    def test_clef_flash_is_left_off_on_the_ollama_where_it_fails(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef-flash:latest": ["Decisions"], "clef:latest": ["Decisions"]})
        read_all()
        p = next(x for x in test_client.get("/config/decisions/status").json()["providers"] if x["id"] == "ollama")
        assert p["models"] == ["clef:latest"]

    def test_the_card_list_gets_the_new_choice(self, test_client, proxy_module, stand, monkeypatch):
        r = test_client.post("/config/decisions/settings", json={"answerFirst": "ollama"})
        assert r.status_code == 200 and r.json()["settings"]["answer_first"] == "ollama"
        assert test_client.post("/config/decisions/settings", json={"answerFirst": "nothing"}).status_code == 400


# ---- the order -----------------------------------------------------------------------------------------------------------------------------------------
class TestOrder:
    def test_it_joins_the_order_even_without_the_local_model_and_can_be_first(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        h = proxy_module.decision_handles
        assert h.order(None, False) == ["ollama", "standin"] and h.order(None, True, True) == ["typesafe", "nano", "ollama", "standin"]
        test_client.post("/config/decisions/settings", json={"answerFirst": "ollama"})
        assert h.order(None, True, True) == ["ollama", "typesafe", "nano", "standin"]
        assert h.order("local/decisions", True) == ["local"]


# ---- the request in, the answer out ---------------------------------------------------------------------------------------------------------------------
class TestAnswering:
    def test_the_request_goes_to_ollamas_systemone_with_the_model_and_the_answer_comes_back(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and r.json()["answers"] == OLLAMA_ANSWER["answers"] and r.json()["usage"] == {"input_tokens": 143, "output_tokens": 0}
        sent = stand.sent[0]
        assert sent["model"] == "clef:latest" and sent["state"] == REAL_JEV_REQUEST["state"] and sent["questions"] == REAL_JEV_REQUEST["questions"]
        assert "keep_alive" not in sent                       # MultiProxy never holds the model in graphics memory for good
        proxy_module._record_speed.assert_awaited()
        args, kw = proxy_module._record_speed.await_args
        assert args[0] == "clef:latest" and kw["provider"] == "Ollama" and kw["extra_roles"] == ["Decisions"] and args[2] is True
        assert proxy_module.decision_handles.last_answer["by"] == "Ollama"

    def test_a_bad_answer_counts_as_no_answer_and_the_next_answerer_is_used(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        stand.body = {"model": "clef:latest", "oops": 1}
        with deepseek(proxy_module, monkeypatch) as ds:
            r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and ds.await_count == 1 and r.json()["answers"]["intent"]["choice"] == "greeting"
        assert stand.calls == 1

    def test_a_question_ollama_refuses_is_passed_on(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        stand.status, stand.body = 400, {"error": "criteria must contain 2-26 candidates"}
        with deepseek(proxy_module, monkeypatch) as ds:
            r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and ds.await_count == 1

    def test_a_slow_model_is_passed_on_and_left_loading(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        stand.delay = 1.2
        monkeypatch.setattr(proxy_module.decision_wiring.de, "limit_for", lambda mode, setting=0.0: 0.2)
        with deepseek(proxy_module, monkeypatch) as ds:
            r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and ds.await_count == 1 and stand.calls == 1
        assert proxy_module._record_speed.await_args_list[0].kwargs["fail_reason"] == "timeout"

    def test_it_can_be_first_and_a_stronger_ai_steps_in_when_it_is_unsure(self, test_client, proxy_module, stand, monkeypatch):
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        test_client.post("/config/decisions/settings", json={"answerFirst": "ollama"})
        unsure = json.loads(json.dumps(OLLAMA_ANSWER))
        unsure["answers"]["intent"]["confidence"] = 0.3
        stand.body = unsure
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        nano_answer = {"model": "x", "answers": OLLAMA_ANSWER["answers"], "usage": {}}
        with patch.object(proxy_module, "_ask_nano_decision", AsyncMock(return_value=(200, json.dumps(nano_answer).encode(), "application/json"))) as nano:
            r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and nano.await_count == 1
        assert proxy_module.decision_handles.last_answer["line"] == "The Ollama model wasn't sure, so Nano-GPT stepped in."


# ---- Nano-GPT: the choice among its decision models ----------------------------------------------------------------------------------------------------
NANO_LIST = {"data": [
    {"id": "gpt-4o", "architecture": {"output_modalities": ["text"]}},
    {"id": "liquid/d1", "architecture": {"output_modalities": ["decisions"]}},
    {"id": "typesafe/jev-latest", "architecture": {"output_modalities": ["decisions"]}},
    {"id": "cloudflare/clef", "architecture": {"output_modalities": ["decisions"]}},
]}


class TestNano:
    def test_its_decision_models_are_listed_with_the_one_always_used_first(self, test_client, proxy_module, stand, monkeypatch):
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        stand.nano_list = NANO_LIST
        read_all()
        p = next(x for x in test_client.get("/config/decisions/status").json()["providers"] if x["id"] == "nano")
        assert p["models"] == ["typesafe/jev-latest", "liquid/d1", "cloudflare/clef"] and p["model"] == "typesafe/jev-latest"

    def test_the_chosen_model_is_the_one_sent_and_recorded(self, test_client, proxy_module, stand, monkeypatch):
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        stand.nano_list = NANO_LIST
        read_all()
        r = test_client.post("/config/decisions/settings", json={"decisionModel": {"provider": "nano", "model": "liquid/d1"}})
        assert r.status_code == 200 and r.json()["settings"]["decision_models"] == {"nano": "liquid/d1"}
        stand.body = {"model": "liquid/d1-2026", "answers": OLLAMA_ANSWER["answers"], "usage": {"input_tokens": 5, "output_tokens": 0}, "id": "gen-1", "provider": "Liquid"}
        r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and "provider" not in r.json()
        assert stand.sent[0]["model"] == "liquid/d1"
        assert proxy_module._record_speed.await_args.args[0] == "liquid/d1"

    def test_a_model_the_provider_does_not_report_is_refused(self, test_client, proxy_module, stand, monkeypatch):
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        stand.nano_list = NANO_LIST
        read_all()
        assert test_client.post("/config/decisions/settings", json={"decisionModel": {"provider": "nano", "model": "gpt-4o"}}).status_code == 400
        assert test_client.post("/config/decisions/settings", json={"decisionModel": {"provider": "nobody", "model": "x"}}).status_code == 400

    def test_without_a_list_it_still_uses_the_model_it_always_used(self, test_client, proxy_module, stand, monkeypatch):
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        stand.body = {"model": "jev", "answers": OLLAMA_ANSWER["answers"], "usage": {}}
        assert test_client.post("/v1/systemone", json=REAL_JEV_REQUEST).status_code == 200
        assert stand.sent[0]["model"] == proxy_module.NANO_DECISIONS_MODEL


# ---- the Quick Test -----------------------------------------------------------------------------------------------------------------------------------
class TestQuickTest:
    def test_it_is_a_way_of_answering_only_while_ollama_has_a_decision_model(self, test_client, proxy_module, stand, monkeypatch):
        opts = test_client.get("/quicktest/decision/options").json()
        c = next(x for x in opts["choices"] if x["id"] == "ollama")
        assert c["label"] == "My Ollama decision model" and c["reason"]
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        c = next(x for x in test_client.get("/quicktest/decision/options").json()["choices"] if x["id"] == "ollama")
        assert c["reason"] == ""

    def test_it_says_plainly_who_answered_and_how_fast(self, test_client, proxy_module, stand, monkeypatch):
        import decision_test
        ollama_has(proxy_module, monkeypatch, {"clef:latest": ["Decisions"]})
        read_all()
        scenario = decision_test.listing()[0]["id"]
        stand.body = {"model": "clef:latest", "answers": {q["name"]: {"type": "noul", "noul": 0.9} for q in decision_test.listing()[0]["questions"]}, "usage": {}}
        built = decision_test.request(scenario)
        stand.body["answers"] = {n: ({"type": "noul", "noul": 0.9} if q.get("type") == "noul" else {"type": q.get("type")}) for n, q in built[1].items()}
        r = test_client.post("/quicktest/decision", json={"scenario": scenario, "who": "ollama"})
        d = r.json()
        assert d["ok"] is True and d["line"].startswith("Answered by clef:latest in ") and d["line"].endswith(" s")
        assert stand.sent[0]["model"] == "clef:latest"

    def test_when_it_is_off_the_test_says_so(self, test_client, proxy_module, stand):
        import decision_test
        d = test_client.post("/quicktest/decision", json={"scenario": decision_test.listing()[0]["id"], "who": "ollama"}).json()
        assert d["ok"] is False and "that choice is off" in d["error"]


# ---- the card's own script ----------------------------------------------------------------------------------------------------------------------------
def test_the_card_lists_a_provider_and_a_model_list_from_the_status_not_from_its_own_code():
    import decision_ui
    js = decision_ui.DECISION_JS if hasattr(decision_ui, "DECISION_JS") else "".join(v for v in vars(decision_ui).values() if isinstance(v, str))
    assert "dcSvc" in js and "dcOrdTable" in js and "p.label" in js and "My Ollama" not in js
    assert "decisionModel: { provider:" in js


def test_the_card_draws_the_provider_entry_and_a_plain_model_list_in_node():
    import os, shutil, subprocess
    import decision_ui
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed")
    harness = r"""
const vm = require('vm');
const ghost = new Proxy({}, { has: () => true, get: (t, k) => (k in t ? t[k] : (k === Symbol.unscopables ? undefined : (k in globalThis ? globalThis[k] : () => ''))), set: (t, k, v) => { t[k] = v; return true; } });
vm.createContext(ghost);
const noop = () => new Proxy(function () {}, { get: () => noop(), apply: () => noop() });
for (const n of ['document', 'window', 'localStorage', 'navigator', 'history', 'location']) ghost[n] = noop();
vm.runInContext(process.env.DC_JS, ghost);
const s = { typesafeKey: false, nanoKey: true, location: 'this', models: [], thisPc: { name: 'X' }, settings: {}, providers: [
  { id: 'ollama', choice: 'ollama', label: 'My Ollama decision model', ready: true, keyOk: true, models: ['clef:latest', 'llmtech/decider:4b'], model: 'clef:latest' },
  { id: 'nano', choice: 'nanogpt', label: 'My Nano-GPT key', ready: true, keyOk: true, models: ['typesafe/jev-latest', 'liquid/d1'], model: 'liquid/d1' }] };
const off = { ...s, nanoKey: false, providers: [{ id: 'ollama', choice: 'ollama', label: 'My Ollama decision model', ready: false, keyOk: true, models: [], model: '' }] };
ghost.esc = (x) => String(x); ghost.jsq = (x) => String(x); ghost.s = s; ghost.off = off; vm.runInContext("dcSt = s", ghost);
console.log(JSON.stringify({ on: [vm.runInContext("dcSvc('ollama', s, {})", ghost), vm.runInContext("dcSvc('nano', s, {})", ghost)], off: vm.runInContext("dcSvc('ollama', off, {})", ghost) }));
"""
    r = subprocess.run([node, "-e", harness], capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, DC_JS=decision_ui.DECISION_JS), timeout=60)
    assert r.returncode == 0, r.stderr[:1200]
    out = json.loads(r.stdout)
    ollama, nano = out["on"]
    assert ollama["name"] == "Ollama" and (ollama["dot"], ollama["word"]) == ("on", "Ready")
    assert '<select id="dcSelModelOfollama"' in ollama["model"] and '<option value="llmtech/decider:4b">llmtech/decider:4b</option>' in ollama["model"] and '<option value="clef:latest" selected>clef:latest</option>' in ollama["model"]
    assert nano["name"] == "Nano-GPT" and '<option value="liquid/d1" selected>liquid/d1</option>' in nano["model"]
    assert (out["off"]["dot"], out["off"]["word"]) == ("idle", "Stopped"), "a provider that is not running stays in the list, grey"
