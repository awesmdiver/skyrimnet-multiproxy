"""Benchmarks: testing next to other models (contract sections 12 and 12b): the background list, the three "While testing" answers, the headroom with a background, pairs.
Fake engine, fake card, fake model state: nothing real is started, stopped or read."""
import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import benchmarks_background as bgm
import benchmarks_contract as bc
import benchmarks_runner as br
import benchmarks_store as bs
import benchmarks_wiring as bw
import local_guard
from benchmarks_fakes import FakeCard, FakeLauncher, make_service, plan_body, write_model

GB = 1024
OWN = "http://127.0.0.1:8000"
JSON_H = {"X-MultiProxy": "1", "Content-Type": "application/json"}


def item(id, kind="chat", name=None, mb=None, state="loaded", mine=True, size=None):
    return {"id": id, "kind": kind, "name": name or id, "state": state, "vram_mb": None if mb is None else {"0": mb}, "size_mb": size, "started_by_multiproxy": mine}


class FakeDevice:
    """What the test device runs: `loaded` and `assigned` lists, plus the calls the runner made."""

    def __init__(self, loaded=(), assigned=(), fail_start=()):
        self.loaded, self.assigned = [dict(i) for i in loaded], [dict(i) for i in assigned]
        self.unloaded, self.started, self.fail_start = [], [], set(fail_start)
        self.events = []

    def hooks(self, runs_game=True, fit=None):
        h = br.Hooks()
        h.background_loaded = lambda: [dict(i) for i in self.loaded]
        h.background_assigned = lambda: [dict(i) for i in self.assigned]
        h.loaded_by_multiproxy = lambda: [{"id": i["id"], "name": i["name"]} for i in self.loaded if i["started_by_multiproxy"]]
        h.runs_game = lambda: runs_game
        if fit:
            h.fit_next_to = fit

        async def unload(ids):
            self.events.append(("unload", list(ids)))
            self.unloaded += list(ids)
            self.loaded = [i for i in self.loaded if i["id"] not in ids]

        async def start_service(sid):
            if sid in self.fail_start:
                return "No room."
            self.events.append(("start", sid))
            self.started.append(sid)
            a = next(i for i in self.assigned if i["id"] == sid)
            self.assigned.remove(a)
            self.loaded.append(dict(a, state="loaded", vram_mb={"0": a.get("size_mb") or 0}, started_by_multiproxy=True))
            return None

        h.unload, h.start_service = unload, start_service
        return h


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "benchmarks"
    card = FakeCard()
    launcher = FakeLauncher(card)
    yield {"base": base, "model": write_model(tmp_path), "card": card, "launcher": launcher, "tmp": tmp_path}
    for e in launcher.engines:
        e.stop()


def run(coro):
    return asyncio.run(coro)


async def go(env, body, device, takeover=False, **kw):
    svc = make_service(env["base"], env["launcher"], env["card"], hooks=device.hooks(**kw))
    res = await svc.start(body, takeover=takeover)
    if res["ok"]:
        await svc.wait()
    return svc, res


def manifests(base):
    out = [bs.read_manifest(p) for p in sorted(base.iterdir()) if p.is_dir()]
    return sorted(out, key=lambda m: m["started_epoch_s"])


# --- the estimate: card - skyrim - background - model, at the 4 GB edge ---
@pytest.mark.parametrize("bg_gb,dot,mb", [(0, "green", 4096), (1, "amber", 3072), (3, "amber", 1024)])
def test_headroom_at_the_four_gb_edge_with_a_background(bg_gb, dot, mb):
    bg = [item("higgs", "voice", mb=bg_gb * GB)] if bg_gb else []
    g = {"gpu_index": 0, "vram_total_mb": 16384, "vram_idle_before_load_mb": 5000, "vram_highest_overall_mb": 5000 + 12288}
    est = bc.headroom_per_gpu([g], 0, "closed", background=bg)[0]
    assert est["headroom_mb"] == mb and est["dot"] == dot and est["background_vram_mb"] == bg_gb * GB and est["kind"] == "estimate"
    assert est["background_source"] == ("list" if bg_gb else "none")


def test_background_that_takes_the_edge_exactly_and_one_mb_more():
    g = {"gpu_index": 0, "vram_total_mb": 19456, "vram_idle_before_load_mb": 5000, "vram_highest_overall_mb": 5000 + 12288}
    assert bc.headroom_per_gpu([g], 0, "closed", background=[item("h", mb=3072)])[0]["dot"] == "green"
    assert bc.headroom_per_gpu([g], 0, "closed", background=[item("h", mb=3073)])[0]["dot"] == "amber"


def test_where_the_background_figure_comes_from():
    two = [item("a", mb=1000), item("b", mb=500), item("c", mb=None)]
    assert bc.background_vram_mb(two, 0) == (1500, "list")
    assert bc.background_vram_mb([], 0) == (0, "none")
    assert bc.background_vram_mb([item("a", mb=None)], 0, vram_idle_before_load_mb=4000, card_own_idle_mb=1000) == (3000, "idle_reading")
    assert bc.background_vram_mb([item("a", mb=None)], 0, vram_idle_before_load_mb=500, card_own_idle_mb=1000) == (0, "idle_reading")      # never negative
    assert bc.background_vram_mb([item("a", mb=None)], 0, vram_idle_before_load_mb=4000) == (None, "unknown")                              # nothing guessed
    assert bc.background_vram_mb([item("a", mb=900)], 1) == (None, "unknown")                                                              # a figure for card 0 says nothing of card 1
    g = {"gpu_index": 0, "vram_total_mb": 32000, "vram_idle_before_load_mb": 4000, "vram_highest_overall_mb": 12000}
    e = bc.headroom_per_gpu([g], 0, "closed", background=[item("a", mb=None)])[0]
    assert e["dot"] == "grey" and e["headroom_mb"] is None and e["background_source"] == "unknown"
    e = bc.headroom_per_gpu([g], 0, "closed", background=two)[0]
    assert e["background_unmeasured"] == 1 and e["headroom_mb"] == 32000 - 1500 - 8000


def test_no_skyrim_term_on_a_device_without_the_game():
    g = {"gpu_index": 0, "vram_total_mb": 24000, "vram_idle_before_load_mb": 2000, "vram_highest_overall_mb": 12000}
    e = bc.headroom_per_gpu([g], 99999, "not_applicable", background=[item("q", mb=2000)])[0]
    assert e["skyrim_state"] == "not_applicable" and e["skyrim_peak_mb"] is None and e["headroom_mb"] == 24000 - 2000 - 10000 and e["dot"] == "green"


# --- the list ---
def test_normalize_keeps_what_is_known_and_never_guesses():
    out = bgm.normalize([{"id": "a", "kind": "weird", "name": "A", "vram_mb": {"0": 1200, "1": "x"}}, {"id": ""}, "junk",
                         {"id": "s", "kind": "voice", "name": "Voice", "state": "assigned_stopped", "size_mb": 6000}])
    assert [i["id"] for i in out] == ["a", "s"]
    assert out[0]["kind"] == "chat" and out[0]["vram_mb"] == {"0": 1200} and out[0]["state"] == "loaded"
    assert out[1]["size_note"] == "when running" and out[1]["vram_mb"] is None and out[1]["size_mb"] == 6000


def test_setup_part_ignores_the_memory_figures_and_the_order():
    a = [item("b", "voice", mb=100), item("a", mb=200)]
    b = [item("a", mb=250), item("b", "voice", mb=90)]
    assert bgm.setup_part(a) == bgm.setup_part(b) and bgm.background_key(a) == bgm.background_key(b)
    assert bgm.background_key(a) != bgm.background_key([])


# --- the service ---
def test_background_is_listed_in_the_manifest_and_the_status(env):
    dev = FakeDevice(loaded=[item("higgs", "voice", "Higgs voice", mb=5000, mine=False), item("qwen", name="Qwen3 4B", mb=None, size=2600), item("gemma", name="Gemma", mb=7000, mine=False)])
    svc, res = run(go(env, plan_body([env["model"]], background="keep"), dev))
    assert res["ok"]
    m = manifests(env["base"])[0]
    assert m["status"] == "completed" and m["background_choice"] == "keep"
    assert [b["id"] for b in m["background"]] == ["higgs", "qwen", "gemma"] and m["background_names"] == ["Higgs voice", "Qwen3 4B", "Gemma"]
    assert m["background"][1]["vram_mb"] is None                                  # no figure: said so, not guessed
    assert dev.unloaded == []                                                    # keep: nothing is touched
    summary = json.loads((env["base"] / m["run_id"] / bs.SUMMARY).read_text(encoding="utf-8"))
    assert summary["headroom"][0]["background_vram_mb"] == 12000 and summary["headroom"][0]["background_unmeasured"] == 1


def test_no_background_is_an_empty_list(env):
    dev = FakeDevice()
    run(go(env, plan_body([env["model"]]), dev))
    m = manifests(env["base"])[0]
    assert m["background"] == [] and m["background_choice"] == "stop" and m["pair_of"] is None
    summary = json.loads((env["base"] / m["run_id"] / bs.SUMMARY).read_text(encoding="utf-8"))
    assert summary["headroom"][0]["background_vram_mb"] == 0 and summary["headroom"][0]["background_source"] == "none"


def test_stop_unloads_only_what_multiproxy_started_and_records_what_is_left(env):
    dev = FakeDevice(loaded=[item("gemma", name="Gemma", mb=7000, mine=True), item("higgs", "voice", "Higgs voice", mb=5000, mine=False)])
    svc, first = run(go(env, plan_body([env["model"]]), dev))
    assert not first["ok"] and first["needs_takeover"] == [{"id": "gemma", "name": "Gemma"}] and dev.unloaded == []        # asks first, as before
    svc, second = run(go(env, plan_body([env["model"]]), dev, takeover=True))
    assert second["ok"] and dev.unloaded == ["gemma"]
    m = manifests(env["base"])[0]
    assert [b["id"] for b in m["background"]] == ["higgs"] and m["background_stopped_for_test"] == ["gemma"]


def test_keep_never_asks_and_never_unloads_even_what_multiproxy_started(env):
    dev = FakeDevice(loaded=[item("gemma", mb=7000, mine=True)])
    svc, res = run(go(env, plan_body([env["model"]], background="keep"), dev))
    assert res["ok"] and dev.unloaded == [] and [b["id"] for b in manifests(env["base"])[0]["background"]] == ["gemma"]


def test_refuses_when_it_cannot_fit_next_to_the_background_and_touches_nothing(env):
    seen = []
    dev = FakeDevice(loaded=[item("higgs", "voice", "Higgs voice", mb=5000, mine=True)], assigned=[item("q", "decision", "Decisions", state="assigned_stopped", size=3000, mine=False)])
    svc, res = run(go(env, plan_body([env["model"]], background="start_normal"), dev, fit=lambda path, mb: seen.append(mb) or "too_big"))
    assert not res["ok"] and res["status"] == 409 and "tiny.gguf" in res["message"] and "Higgs voice" in res["message"] and "Decisions" in res["message"]
    assert seen == [8000]                                                        # the loaded 5000 plus the stopped one's 3000 "when running"
    assert dev.events == [] and env["launcher"].starts == 0 and (not env["base"].exists() or not list(env["base"].iterdir()))


def test_fits_next_to_the_background_goes_ahead(env):
    dev = FakeDevice(loaded=[item("higgs", "voice", mb=5000)])
    svc, res = run(go(env, plan_body([env["model"]], background="keep"), dev, fit=lambda path, mb: None))
    assert res["ok"] and manifests(env["base"])[0]["status"] == "completed"


def test_stop_choice_does_not_run_the_fit_check(env):
    dev = FakeDevice(loaded=[item("higgs", "voice", mb=5000, mine=False)])
    svc, res = run(go(env, plan_body([env["model"]]), dev, fit=lambda path, mb: "too_big"))
    assert res["ok"]


def test_setup_id_differs_with_the_background_but_the_core_does_not(env):
    alone = FakeDevice()
    with_higgs = FakeDevice(loaded=[item("higgs", "voice", "Higgs voice", mb=5000, mine=False)])
    run(go(env, plan_body([env["model"]]), alone))
    time.sleep(1.1)                                                              # run ids carry the start second
    run(go(env, plan_body([env["model"]], background="keep", pair_of=manifests(env["base"])[0]["run_id"]), with_higgs))
    a, b = manifests(env["base"])
    assert a["setup_id"] != b["setup_id"] and a["setup_core_id"] == b["setup_core_id"] and a["model_id"] == b["model_id"]
    assert b["pair_of"] == a["run_id"] and a["pair_of"] is None
    pairs = bs.pairs_of(env["base"], a["run_id"], time.time())
    assert [p["run_id"] for p in pairs] == [b["run_id"]] and pairs[0]["background_names"] == ["Higgs voice"] and pairs[0]["pair_of"] == a["run_id"]
    assert [p["run_id"] for p in bs.pairs_of(env["base"], b["run_id"], time.time())] == [a["run_id"]]


def test_same_background_twice_is_not_a_pair(env):
    dev = FakeDevice(loaded=[item("higgs", "voice", mb=5000, mine=False)])
    run(go(env, plan_body([env["model"]], background="keep"), dev))
    time.sleep(1.1)
    run(go(env, plan_body([env["model"]], background="keep"), dev))
    a, b = manifests(env["base"])
    assert a["setup_id"] == b["setup_id"] and bs.pairs_of(env["base"], a["run_id"], time.time()) == []


# --- 12b: the device that does not run the game ---
def test_no_skyrim_on_a_device_without_the_game(env):
    dev = FakeDevice(loaded=[item("q", "decision", "Decisions", mb=2000)])
    run(go(env, plan_body([env["model"]], background="keep"), dev, runs_game=False))
    m = manifests(env["base"])[0]
    assert m["skyrim_state"] == "not_applicable"
    summary = json.loads((env["base"] / m["run_id"] / bs.SUMMARY).read_text(encoding="utf-8"))
    h = summary["headroom"][0]
    assert h["skyrim_state"] == "not_applicable" and h["skyrim_peak_mb"] is None and h["headroom_mb"] == 32000 - 2000 - 8000


def test_assigned_but_stopped_services_are_listed_with_their_size_when_running(env):
    dev = FakeDevice(loaded=[item("higgs", "voice", "Higgs voice", mb=5000)], assigned=[item("qwen", name="Qwen3 4B", state="assigned_stopped", size=2600, mine=False)])
    svc = make_service(env["base"], env["launcher"], env["card"], hooks=dev.hooks())
    now = svc.status()["background_now"]
    assert [i["id"] for i in now["loaded"]] == ["higgs"]
    assert now["assigned"][0]["id"] == "qwen" and now["assigned"][0]["size_mb"] == 2600 and now["assigned"][0]["size_note"] == "when running"
    async def main():
        assert (await svc.start(plan_body([env["model"]], background="keep")))["ok"]
        await svc.wait()
    run(main())
    m = manifests(env["base"])[0]
    assert [a["id"] for a in m["background_assigned"]] == ["qwen"] and [b["id"] for b in m["background"]] == ["higgs"]


def test_start_normal_starts_what_is_stopped_then_stops_only_what_it_started(env):
    dev = FakeDevice(loaded=[item("higgs", "voice", "Higgs voice", mb=5000, mine=False)],
                     assigned=[item("dec", "decision", "Decisions", state="assigned_stopped", size=2000, mine=False), item("stt", "stt", "Speech to text", state="assigned_stopped", size=800, mine=False)])
    svc, res = run(go(env, plan_body([env["model"]], background="start_normal"), dev))
    assert res["ok"] and dev.started == ["dec", "stt"]
    m = manifests(env["base"])[0]
    assert m["status"] == "completed" and [b["id"] for b in m["background"]] == ["higgs", "dec", "stt"] and m["background_started"] == ["dec", "stt"]
    assert m["background"][1]["started_by_this_run"] is True and "started_by_this_run" not in m["background"][0]
    assert dev.unloaded == ["dec", "stt"]                                         # Higgs was running before and is never touched
    assert [i["id"] for i in dev.loaded] == ["higgs"] and svc.status()["background_stopped_after"] == ["dec", "stt"]
    assert dev.events.index(("start", "dec")) < dev.events.index(("unload", ["dec", "stt"]))


def test_start_normal_with_keep_started_leaves_them_running(env):
    dev = FakeDevice(assigned=[item("dec", "decision", "Decisions", state="assigned_stopped", size=2000, mine=False)])
    svc, res = run(go(env, plan_body([env["model"]], background="start_normal", keep_started=True), dev))
    assert res["ok"] and dev.started == ["dec"] and dev.unloaded == [] and [i["id"] for i in dev.loaded] == ["dec"]


def test_start_normal_stops_what_it_started_even_when_cancelled(env):
    dev = FakeDevice(assigned=[item("dec", "decision", "Decisions", state="assigned_stopped", size=2000, mine=False)])

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"], hooks=dev.hooks())
        assert (await svc.start(plan_body([env["model"]], levels=[1, 4], requests_per_level=50, background="start_normal"), takeover=True))["ok"]
        await asyncio.sleep(0.3)
        await svc.cancel()
        await svc.wait()
    run(main())
    assert dev.unloaded == ["dec"] and dev.loaded == []


def test_a_service_that_will_not_start_stops_the_test_and_the_ones_already_started(env):
    dev = FakeDevice(assigned=[item("a", "decision", "A", state="assigned_stopped", size=100, mine=False), item("b", "voice", "B", state="assigned_stopped", size=100, mine=False)], fail_start=["b"])
    svc, res = run(go(env, plan_body([env["model"]], background="start_normal"), dev))
    assert not res["ok"] and "B" in res["message"] and "No room." in res["message"]
    assert dev.started == ["a"] and dev.unloaded == ["a"] and env["launcher"].starts == 0


def test_a_service_multiproxy_did_not_start_is_never_touched_by_start_normal(env):
    dev = FakeDevice(loaded=[item("theirs", "voice", "Theirs", mb=3000, mine=False)], assigned=[item("dec", "decision", "Decisions", state="assigned_stopped", size=100, mine=False)])
    run(go(env, plan_body([env["model"]], background="start_normal"), dev))
    assert "theirs" not in dev.unloaded and [i["id"] for i in dev.loaded] == ["theirs"]


# --- the routes ---
@pytest.fixture
def api(tmp_path, monkeypatch):
    base = tmp_path / "benchmarks"
    monkeypatch.setenv(bs.BENCHMARKS_DIR_ENV, str(base))
    local_guard.ENABLED = True
    card = FakeCard()
    launcher = FakeLauncher(card)
    dev = FakeDevice(loaded=[item("higgs", "voice", "Higgs voice", mb=5000, mine=False)], assigned=[item("dec", "decision", "Decisions", state="assigned_stopped", size=2000, mine=False)])
    svc = make_service(base, launcher, card, hooks=dev.hooks())
    app = FastAPI()
    app.add_middleware(local_guard.LocalGuard, log=lambda m: None)
    bw.setup(app, None, None, None, {}, str(tmp_path), "test", service=svc)
    with TestClient(app, base_url=OWN) as client:
        yield {"c": client, "svc": svc, "base": base, "model": write_model(tmp_path), "dev": dev}
    for e in launcher.engines:
        e.stop()


def post(api, **kw):
    return api["c"].post("/config/benchmarks/start", headers=JSON_H, content=json.dumps(plan_body([api["model"]], **kw)))


def wait_idle(api):
    for _ in range(300):
        s = api["c"].get("/config/benchmarks/status").json()
        if not s["running"]:
            return s
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


@pytest.mark.parametrize("choice", ["stop", "keep", "start_normal"])
def test_the_three_route_values_are_accepted(api, choice):
    r = post(api, background=choice)
    assert r.status_code == 200 and r.json()["ok"]
    s = wait_idle(api)
    assert s["background_choice"] == choice


def test_route_rejects_an_unknown_background_or_pair(api):
    assert post(api, background="everything").status_code == 400
    assert post(api, background=True).status_code == 400
    assert post(api, pair_of="../x").status_code == 400
    assert api["dev"].events == []


def test_status_reports_the_background_idle_and_running(api):
    idle = api["c"].get("/config/benchmarks/status").json()
    assert idle["background_now"]["loaded"][0]["id"] == "higgs" and idle["background_now"]["assigned"][0]["size_note"] == "when running"
    post(api, background="keep")
    done = wait_idle(api)
    assert done["background"][0]["id"] == "higgs"


def test_pairs_route(api):
    post(api, background="keep")
    wait_idle(api)
    time.sleep(1.1)
    first = api["c"].get("/config/benchmarks/runs").json()["runs"][0]["run_id"]
    api["dev"].loaded = []
    post(api, background="keep", pair_of=first)
    wait_idle(api)
    r = api["c"].get(f"/config/benchmarks/runs/{first}/pairs")
    assert r.status_code == 200 and len(r.json()["pairs"]) == 1 and r.json()["pairs"][0]["background_names"] == []
    assert api["c"].get("/config/benchmarks/runs/nope/pairs").status_code == 404


# --- the production hooks, against stand-in services ---
class _Srv:
    def __init__(self, state):
        self.state = state


class _Installed:
    def __init__(self, id, name, size):
        self.id, self.name, self.size = id, name, size


class _Local:
    HAS_GAME = False
    _gpu = None

    def __init__(self):
        self.servers = {"gemma": _Srv("running")}
        self.stopped = []

    def installed(self):
        return [_Installed("gemma", "Gemma", 5 * 1024 * 1024 * 1024), _Installed("qwen", "Qwen", 2 * 1024 * 1024 * 1024)]

    def find(self, mid):
        return next((m for m in self.installed() if m.id == mid), None)

    async def stop(self, mid):
        self.stopped.append(mid)

    def skyrim_peak_mb(self):
        return None


class _Engine:
    def __init__(self, state, mb=None, card=None):
        self.state, self.mb, self.card, self.stops, self.starts = state, mb, card, 0, 0

    def installed(self):
        return True

    def status(self):
        return {"state": self.state, "gpuMemoryMb": self.mb, "gpuCard": self.card, "modelName": "Higgs voice"}

    async def start(self):
        self.starts += 1
        self.state = "ready"

    async def stop(self):
        self.stops += 1
        self.state = "stopped"


class _Card:
    vendor = "nvidia"

    def __init__(self, name):
        self.name = name


def test_production_hooks_list_chat_voice_and_decision_and_stop_each_by_its_own_function():
    local, voice, dec = _Local(), _Engine("ready", 5200, "RTX A"), _Engine("stopped")
    h = bw.production_hooks(local, lambda: {}, None, {}, "t", voice, dec, cards_fn=lambda: [_Card("RTX A"), _Card("RTX B")])
    loaded = {i["id"]: i for i in bgm.normalize(h.background_loaded())}
    assert set(loaded) == {"gemma", "service:voice"} and loaded["service:voice"]["vram_mb"] == {"0": 5200} and loaded["gemma"]["vram_mb"] is None and loaded["gemma"]["size_mb"] == 5120
    assigned = bgm.normalize(h.background_assigned())
    assert [i["id"] for i in assigned] == ["service:decision"]                    # a stopped chat model is not "assigned": left alone
    assert h.runs_game() is False

    async def main():
        assert await h.start_service("service:decision") is None
        await h.unload(["service:decision", "service:voice", "gemma"])
        assert await h.start_service("service:nope") == "It isn't set up on this PC."
    run(main())
    assert dec.starts == 1 and dec.stops == 1 and voice.stops == 1 and local.stopped == ["gemma"]


def test_a_card_that_cannot_be_told_gives_no_figure():
    voice = _Engine("ready", 5200, "Unknown card")
    h = bw.production_hooks(_Local(), lambda: {}, None, {}, "t", voice, None, cards_fn=lambda: [_Card("RTX A"), _Card("RTX B")])
    v = next(i for i in bgm.normalize(h.background_loaded()) if i["id"] == "service:voice")
    assert v["vram_mb"] is None
    h1 = bw.production_hooks(_Local(), lambda: {}, None, {}, "t", voice, None, cards_fn=lambda: [_Card("RTX A")])
    assert next(i for i in bgm.normalize(h1.background_loaded()) if i["id"] == "service:voice")["vram_mb"] == {"0": 5200}
