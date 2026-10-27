"""Set me up (2026-10-26): the picks against stand-in hardware, the plan, the install steps against stand-in installs and the real download code, the Routing wiring, the screens."""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import decision_engine as de
import local_models as lm
import routing
import set_me_up as sm
import set_me_up_ui
from tests.test_local_models import MMPROJ_BYTES, MODEL_BYTES, _tiny_catalog, serve

NODE = shutil.which("node")
GB = 1024


def card(gb, vendor="nvidia", name="Test card", count=1, chosen=False):
    return sm.Card(name, int(gb * GB) if gb is not None else None, vendor, count=count, chosen=chosen)


# ---- the picks: hardware in, models out ----------------------------------------------------------------------------------------------------------------------------------
PICKS = [
    # (graphics memory GB, game on this PC, chat model, decision model)
    (24, True, "gemma-4-12b", "kev-4b"),
    (24, False, "gemma-4-26b-a4b", "kev-4b"),
    (32, True, "gemma-4-12b", "kev-4b"),            # the game's 12 GB estimate keeps the 26B off a 32 GB card that also runs Skyrim
    (32, False, "gemma-4-26b-a4b", "kev-4b"),
    (16, True, "gemma-4-12b", "kev-4b"),            # only fits with "little" room: the smallest that fits is the start
    (16, False, "gemma-4-12b", "kev-4b"),
    (12, True, None, "kev-4b"),
    (12, False, "gemma-4-12b", "kev-4b"),
    (8, True, None, "kev-4b"),
    (8, False, "gemma-4-e2b", "kev-4b"),            # E4B only "leaves little" here, so the safe start is E2B
    (6, False, None, "kev-4b"),
    (24, True, "gemma-4-12b", "kev-4b"),
    (4, True, None, "kev-4b"),
    (2, False, None, "laya"),
    (None, True, None, "laya"),           # no card, or one that cannot be read
    (80, False, "gemma-4-26b-a4b", "kev-4b"),       # a huge card still never starts on the 31B
]


@pytest.mark.parametrize("gb,game,chat,decision", PICKS)
def test_picks_follow_the_fit_check(gb, game, chat, decision):
    c = card(gb) if gb is not None else sm.Card()
    assert sm.pick_chat(c, game) == chat
    assert sm.pick_decision(c) == decision
    assert sm.pick_chat(c, game) != "gemma-4-31b"


def test_the_31b_is_listed_but_the_catalog_gained_nothing():
    assert "gemma-4-31b" in sm.CHAT_CANDIDATES
    assert [m["id"] for m in lm.CATALOG] == ["gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-31b"]


def test_the_game_leaves_less_room_by_the_named_amounts():
    c = card(24)
    assert sm.budget_mb(c, True) == 24 * GB - sm.SYSTEM_HEADROOM_MB - sm.SKYRIM_HEADROOM_MB
    assert sm.budget_mb(c, False) == 24 * GB - sm.SYSTEM_HEADROOM_MB
    assert sm.budget_mb(sm.Card(), True) is None


def test_the_estimate_is_the_programs_own_and_grows_with_the_context(monkeypatch):
    c = card(24)
    small = sm.estimate_for("gemma-4-12b", c, True).total_mb
    assert 8000 < small < 12000
    real = sm.local_engine_memory.estimate_from_facts
    seen = {}

    def spy(size, mm, facts, settings, override=None, card_mb=None):
        seen.update(settings)
        return real(size, mm, facts, dict(settings, ctx=settings["ctx"] * 2), override, card_mb)
    monkeypatch.setattr(sm.local_engine_memory, "estimate_from_facts", spy)
    assert sm.estimate_for("gemma-4-12b", c, True).total_mb > small
    assert seen["ctx"] == 32768 and seen["parallel"] == 2          # the Local models tab's suggestion for a 24 GB card with the game


def test_a_model_without_facts_is_never_picked(monkeypatch):
    monkeypatch.setattr(sm, "CACHE_FACTS", {"gemma-4-12b": sm.CACHE_FACTS["gemma-4-12b"]})
    assert sm.pick_chat(card(80), False) == "gemma-4-12b"
    monkeypatch.setattr(sm, "CACHE_FACTS", {})
    assert sm.pick_chat(card(80), False) is None


def test_every_catalog_chat_model_is_listed_with_a_verdict():
    for gb, game, verdicts in [(32, True, ["little", "little", "room", "room", "room"]), (32, False, ["room"] * 5), (24, True, ["no", "no", "room", "room", "room"]), (8, False, ["no", "no", "no", "little", "room"])]:
        opts = sm.chat_options(card(gb), game)
        assert [o["id"] for o in opts] == [m["id"] for m in reversed(lm.CATALOG)]
        assert [o["verdict"] for o in opts] == verdicts
        assert all(o["bytes"] == lm.model_total_bytes(lm.CATALOG_BY_ID[o["id"]]["files"]) for o in opts)


def test_the_preselect_is_the_largest_with_room_and_the_31b_is_never_it():
    assert sm.default_chat(sm.chat_options(card(32), True)) == "gemma-4-12b"
    assert sm.default_chat(sm.chat_options(card(32), False)) == "gemma-4-26b-a4b"
    assert sm.default_chat(sm.chat_options(card(80), False)) == "gemma-4-26b-a4b"
    p = sm.make_plan(facts(gb=32), True)
    assert [o["id"] for o in p["models"] if o["selected"]] == ["gemma-4-12b"] and p["chatWarning"] == ""


def test_a_will_not_fit_row_cannot_be_chosen():
    opts = {o["id"]: o for o in sm.chat_options(card(24), True)}
    assert opts["gemma-4-26b-a4b"]["verdict"] == "no" and not opts["gemma-4-26b-a4b"]["selectable"]
    assert opts["gemma-4-26b-a4b"]["text"] == sm.WORDS["verdict_no_game"]
    assert sm.pick_chat(card(24), True, "gemma-4-26b-a4b") == "gemma-4-12b"          # refused: the safe one stays


def test_a_little_room_row_can_be_chosen_and_shows_the_amber_line():
    p = sm.make_plan(facts(gb=32), True, chat="gemma-4-26b-a4b")
    assert p["chat"] == "gemma-4-26b-a4b" and p["chatWarning"] == sm.WORDS["warn_game"]
    row = next(o for o in p["models"] if o["id"] == "gemma-4-26b-a4b")
    assert row["selected"] and row["selectable"] and row["text"] == sm.WORDS["verdict_little_game"]
    assert sm.make_plan(facts(gb=32), False, chat="gemma-4-26b-a4b")["chatWarning"] == ""          # alone it has room


def test_the_size_and_the_install_follow_the_pick():
    small = sm.make_plan(facts(gb=32), True)
    big = sm.make_plan(facts(gb=32), True, chat="gemma-4-26b-a4b")
    chat = lambda p: next(i for i in p["items"] if i["key"] == "chat")
    assert chat(big)["bytes"] == lm.model_total_bytes(lm.CATALOG_BY_ID["gemma-4-26b-a4b"]["files"]) > chat(small)["bytes"]
    assert big["needBytes"] > small["needBytes"]


def test_the_kept_header_facts_are_whole():
    for mid, kv in sm.CACHE_FACTS.items():
        n = kv["gemma4.block_count"]
        heads = kv["gemma4.attention.head_count_kv"]
        assert (len(heads) if isinstance(heads, list) else n) == n == len(kv["gemma4.attention.sliding_window_pattern"])


def test_an_installed_decision_model_is_kept():
    assert sm.pick_decision(card(24), "lev") == "lev"


# ---- the plan --------------------------------------------------------------------------------------------------------------------------------------------------------------
def facts(gb=24, folder=Path("D:/Models"), free=500e9, **kw):
    base = dict(card=card(gb), folder=folder, free_disk=int(free), engine_kind="cuda-13.4", engine_bytes=400_000_000)
    base.update(kw)
    return sm.Facts(**base)


def test_the_plan_lists_the_pieces_and_what_they_need():
    p = sm.make_plan(facts(), True)
    assert [i["key"] for i in p["items"]] == ["chat", "decision", "engine"]
    assert p["chat"] == "gemma-4-12b" and p["decision"] == "kev-4b" and p["canInstall"] and p["installCount"] == 3
    assert p["needBytes"] > sum(i["bytes"] for i in p["items"])                     # the engine's unpacked copy and the install margin are counted
    assert p["warning"] == "" and p["notes"] == []


def test_not_enough_disk_warns_and_there_is_no_install():
    p = sm.make_plan(facts(free=5e9), True)
    assert not p["canInstall"] and "needed" in p["warning"] and "free" in p["warning"]
    assert p["warning"].startswith(sm.gb_text(p["needBytes"]))


def test_no_folder_yet_asks_for_one_and_never_picks_one():
    p = sm.make_plan(facts(folder=None), True)
    assert p["folder"] is None and not p["canInstall"] and p["warning"] == sm.WORDS["folder_needed"]


def test_no_chat_model_fits_installs_only_the_decision_model_and_says_so():
    p = sm.make_plan(facts(gb=8), True)
    assert [i["key"] for i in p["items"]] == ["decision", "engine"] and p["chat"] is None
    assert p["noRoom"] and sm.WORDS["no_chat"] not in p["notes"] and p["canInstall"]          # (the cloud line moved into the no-room panel, 2026-10-26)


def test_a_pc_with_no_card_gets_laya_and_the_no_card_line():
    p = sm.make_plan(facts(gb=None), False)
    assert p["decision"] == "laya" and sm.WORDS["no_card"] in p["notes"] and sm.WORDS["no_chat"] in p["notes"]


def test_what_is_already_installed_is_not_downloaded_again():
    p = sm.make_plan(facts(chat_here={"gemma-4-12b"}, decision_key="kev-4b", engine_here=True), True)
    assert all(i["here"] and i["bytes"] == 0 for i in p["items"]) and p["installCount"] == 0
    assert p["canInstall"] and p["allHere"] and p["useWord"] == "Use these models", "everything is there: the button uses the models instead of installing"


def test_two_cards_say_which_one_is_used():
    chosen = sm.make_plan(facts(card=card(24, name="RTX 3090", count=2, chosen=True)), True)
    biggest = sm.make_plan(facts(card=card(24, name="RTX 3090", count=2)), True)
    one = sm.make_plan(facts(card=card(24, name="RTX 3090", count=1)), True)
    assert chosen["cardNote"] == sm.WORDS["card_chosen"].format(name="RTX 3090")
    assert biggest["cardNote"] == sm.WORDS["card_biggest"].format(name="RTX 3090", n=2) and one["cardNote"] == ""


def test_voice_is_listed_unticked_and_only_installs_when_ticked_and_licensed():
    base = dict(voice_available=True, voice_bytes=5_300_000_000)
    off = sm.make_plan(facts(voice_licence_ok=True, **base), True)
    voice = next(i for i in off["items"] if i["key"] == "voice")
    assert not voice["ticked"] and voice["optional"] and off["installCount"] == 3
    on = sm.make_plan(facts(voice_licence_ok=True, **base), True, voice=True)
    assert next(i for i in on["items"] if i["key"] == "voice")["ticked"] and on["installCount"] == 4 and on["needBytes"] > off["needBytes"]
    unlicensed = sm.make_plan(facts(voice_licence_ok=False, **base), True, voice=True)
    v = next(i for i in unlicensed["items"] if i["key"] == "voice")
    assert not v["ticked"] and v["disabled"] and v["detail"] == sm.WORDS["voice_licence"]
    assert "voice" not in [i["key"] for i in sm.make_plan(facts(), True)["items"]]          # not offered where it cannot be installed


# ---- the install, against stand-in installs ---------------------------------------------------------------------------------------------------------------------------
class FakeJob:
    def __init__(self, total=100, fail=None, hold=False):
        self.state, self.done, self.total, self.error = "running", 0, total, ""
        self._fail, self.hold = fail, hold

    def finish(self):
        if self._fail:
            self.state, self.error = "error", self._fail
        else:
            self.state, self.done = "done", self.total


class FakeLocal:
    """Starts the stand-in installs; `plan` says how each attempt for a key ends (None: fine, a string: that error)."""
    folder = Path("D:/Models")

    def __init__(self, outcomes=None):
        self.outcomes = {k: list(v) for k, v in (outcomes or {}).items()}
        self.calls, self.cancelled, self.jobs = [], [], {}

    def _start(self, key, total):
        self.calls.append(key)
        outcome = (self.outcomes.get(key) or [None]).pop(0) if self.outcomes.get(key) else None
        job = FakeJob(total, fail=outcome if isinstance(outcome, str) else None, hold=outcome == "hold")
        self.jobs[key] = job
        if not job.hold:
            asyncio.get_event_loop().call_later(0.02, job.finish)
        return job

    def download_engine(self, kind):
        return self._start("engine", 400)

    def download_model(self, mid):
        return self._start(f"model:{mid}", 7000)

    async def cancel(self, key):
        self.cancelled.append(key)


class FakeDecisions:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.cancelled = False
        self.starts = 0

    def start_install(self):
        self.starts += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        job = FakeJob(3000, fail=outcome)
        asyncio.get_event_loop().call_later(0.02, job.finish)
        return job

    def cancel_install(self):
        self.cancelled = True


class Store:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def load(self):
        return json.loads(json.dumps(self.cfg))

    def save(self, cfg):
        self.cfg = json.loads(json.dumps(cfg))


def make(local=None, decisions=None, f=None, store=None, **kw):
    store = store or Store()
    f = f or facts()

    async def facts_fn():
        return f
    log = SimpleNamespace(warning=lambda *a, **k: None, exception=lambda *a, **k: None, info=lambda *a, **k: None)
    svc = sm.SetMeUp(local=local or FakeLocal(), decisions=decisions or FakeDecisions(), facts_fn=facts_fn, load_cfg=store.load, save_cfg=store.save, log=log,
                     retry_waits=(0.01, 0.01), poll=0.01, **kw)
    return svc, store


async def finished(svc, timeout=10):
    await asyncio.wait_for(svc.job.task, timeout)
    return svc.job


@pytest.mark.asyncio
async def test_everything_installs_in_order_wires_routing_and_is_marked_used():
    local, decisions = FakeLocal(), FakeDecisions()
    svc, store = make(local, decisions)
    assert not svc.used()
    await svc.start(True)
    job = await finished(svc)
    assert job.state == "done" and local.calls == ["engine", "model:gemma-4-12b"] and decisions.starts == 1
    assert [s.key for s in job.steps] == ["engine", "chat", "decision"] and all(s.state == "done" for s in job.steps)
    assert job.message == "Installed. The talking jobs use Gemma 4 12B and quick decisions use Kev-4B."
    assert store.cfg["decisions"]["model"] == "kev-4b"
    routes = routing.clean_settings(store.cfg["routing"])["routes"]
    assert {k: v["ais"] for k, v in routes.items()} == {j: ["local/gemma-4-12b"] for j in sm.ROUTED_JOBS}
    assert svc.used()


@pytest.mark.asyncio
async def test_a_route_the_player_made_is_left_alone():
    store = Store({"routing": {"routes": {"memory": {"ais": ["claude/opus"], "how": "order"}}}})
    svc, store = make(store=store)
    await svc.start(True)
    await finished(svc)
    routes = routing.clean_settings(store.cfg["routing"])["routes"]
    assert routes["memory"]["ais"] == ["claude/opus"] and routes["dialogue"]["ais"] == ["local/gemma-4-12b"]


@pytest.mark.asyncio
async def test_only_the_decision_model_when_no_chat_model_fits():
    local, decisions = FakeLocal(), FakeDecisions()
    svc, store = make(local, decisions, f=facts(gb=8))
    await svc.start(True)
    job = await finished(svc)
    assert job.state == "done" and local.calls == ["engine"] and job.message == "Installed. Quick decisions use Kev-4B."
    assert "routing" not in store.cfg


@pytest.mark.asyncio
async def test_laya_is_set_as_the_decision_model_before_its_install():
    svc, store = make(f=facts(gb=2))
    await svc.start(False)
    await finished(svc)
    assert store.cfg["decisions"]["model"] == "laya"


@pytest.mark.asyncio
async def test_a_download_that_fails_tries_again_by_itself_and_says_so():
    local = FakeLocal({"model:gemma-4-12b": ["The download was interrupted.", None]})
    svc, _ = make(local)
    seen = []
    await svc.start(True)
    while svc.job.state == "running":
        seen.append(svc.job.warning)
        await asyncio.sleep(0.005)
    job = svc.job
    assert job.state == "done" and local.calls.count("model:gemma-4-12b") == 2
    assert sm.WORDS["retry"].format(name="Gemma 4 12B") in seen and job.warning == ""


@pytest.mark.asyncio
async def test_a_download_that_never_finishes_ends_with_one_plain_error():
    local = FakeLocal({"model:gemma-4-12b": ["The download was interrupted."] * 3})
    svc, store = make(local)
    await svc.start(True)
    job = await finished(svc)
    assert job.state == "error" and "Could not install Gemma 4 12B: The download was interrupted." == job.error
    assert local.calls.count("model:gemma-4-12b") == 3 and not svc.used() and "routing" not in store.cfg


@pytest.mark.asyncio
async def test_an_error_asking_again_cannot_fix_is_not_retried():
    err = lm.LocalModelError(400, "This needs about 9.0 GB free, but the drive you chose only has 1.0 GB left.")
    local = FakeLocal()
    local.download_model = lambda mid: (_ for _ in ()).throw(err)
    svc, _ = make(local)
    await svc.start(True)
    job = await finished(svc)
    assert job.state == "error" and "only has 1.0 GB left" in job.error


@pytest.mark.asyncio
async def test_cancel_stops_the_running_install_and_the_card_stays():
    local = FakeLocal({"engine": ["hold"]})
    svc, store = make(local)
    await svc.start(True)
    await asyncio.sleep(0.05)
    await svc.cancel()
    assert svc.job.state == "cancelled" and local.cancelled == ["engine"] and local.calls == ["engine"]
    assert not svc.used()


@pytest.mark.asyncio
async def test_nothing_starts_when_the_disk_is_short_or_no_folder_is_chosen():
    for f in (facts(free=1e9), facts(folder=None)):
        svc, _ = make(f=f)
        with pytest.raises(lm.LocalModelError):
            await svc.start(True)
        assert svc.job is None


@pytest.mark.asyncio
async def test_voice_installs_last_only_when_ticked():
    class FakeVoice:
        starts = 0

        def start_install(self):
            FakeVoice.starts += 1
            j = FakeJob(5000)
            asyncio.get_event_loop().call_later(0.02, j.finish)
            return j
    f = facts(voice_available=True, voice_licence_ok=True, voice_bytes=5_300_000_000)
    svc, _ = make(f=f, voice=FakeVoice())
    await svc.start(True, voice=False)
    await finished(svc)
    assert FakeVoice.starts == 0
    svc, _ = make(f=f, voice=FakeVoice())
    await svc.start(True, voice=True)
    job = await finished(svc)
    assert FakeVoice.starts == 1 and [s.key for s in job.steps][-1] == "voice"


# ---- the real download code (a tiny model served from this PC) ----------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_real_model_download_is_what_runs(tmp_path, monkeypatch):
    catalog = _tiny_catalog()
    monkeypatch.setattr(lm, "CATALOG", catalog)
    monkeypatch.setattr(lm, "CATALOG_BY_ID", {m["id"]: m for m in catalog})
    monkeypatch.setattr(sm, "CHAT_CANDIDATES", ("tiny-model",))
    monkeypatch.setattr(sm, "CACHE_FACTS", dict(sm.CACHE_FACTS, **{"tiny-model": sm.CACHE_FACTS["gemma-4-12b"]}))
    monkeypatch.setattr(sm, "estimate_for", lambda mid, c, g: SimpleNamespace(total_mb=100))
    store = Store()
    log = SimpleNamespace(warning=lambda *a, **k: None, exception=lambda *a, **k: None, info=lambda *a, **k: None, error=lambda *a, **k: None)
    local = lm.LocalModels(store.load, store.save, log, str(tmp_path / "pids.json"), gpu_detector=lambda: lm.GpuInfo("Test GPU", 24576, "nvidia", cuda=(13, 4)))
    local.set_folder(str(tmp_path / "models"))
    eng = local.folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-cuda-13.4"
    eng.mkdir(parents=True)
    (eng / ("llama-server.exe" if os.name == "nt" else "llama-server")).write_bytes(b"x")
    local._update(engine={"kind": "cuda-13.4", "build": lm.LLAMA_CPP_BUILD})
    f = facts(folder=local.folder, engine_here=True)

    async def facts_fn():
        return f
    svc = sm.SetMeUp(local=local, decisions=FakeDecisions(), facts_fn=facts_fn, load_cfg=store.load, save_cfg=store.save, log=log, retry_waits=(0.01,), poll=0.01)
    async with serve({"acme/tiny/tiny.gguf": MODEL_BYTES, "acme/tiny/mmproj-tiny.gguf": MMPROJ_BYTES}) as (base, requests):
        monkeypatch.setattr(lm, "HF_URL", base + "/{repo}/{file}")
        await svc.start(True)
        job = await finished(svc)
    chat = next(s for s in job.steps if s.key == "chat")
    assert job.state == "done" and chat.done == chat.total == len(MODEL_BYTES) + len(MMPROJ_BYTES)
    assert [m.id for m in local.installed()] == ["tiny-model"]
    assert routing.clean_settings(store.cfg["routing"])["routes"]["dialogue"]["ais"] == ["local/tiny-model"]


# ---- looking at this PC ---------------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_read_facts_uses_the_players_card_and_never_a_folder_it_was_not_given(tmp_path):
    gpu = lm.GpuInfo("Chosen card", 24576, "nvidia", cuda=(13, 4))

    class L:
        folder = None

        async def gpu_for(self, mid):
            return gpu

        def engine_exe_current(self):
            return None

        def installed(self):
            return []
    d = SimpleNamespace(installed=lambda key: False, model_key=lambda: "kev-4b")
    import gpu_choice
    cards = [gpu_choice.Card("GPU-1", "Chosen card", 24576, "nvidia"), gpu_choice.Card("GPU-2", "Other card", 8192, "nvidia")]
    f = await sm.read_facts(L(), d, cards_fn=lambda: cards, choice_fn=lambda mid: {"id": "GPU-1"})
    assert f.card.name == "Chosen card" and f.card.count == 2 and f.card.chosen and f.folder is None and f.free_disk is None
    assert f.engine_kind == "cuda-13.4" and not f.engine_here


def test_free_room_is_read_from_the_nearest_folder_that_exists(tmp_path):
    assert sm.nearest_existing(tmp_path / "a" / "b") == tmp_path


# ---- the screens ----------------------------------------------------------------------------------------------------------------------------------------------------------
def run_js(body: str) -> list:
    prelude = (
        "const esc = s => String(s == null ? '' : s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        "const el = { innerHTML: '' }; const document = { getElementById: id => (id === 'gsCard' ? el : (el.innerHTML.includes('id=\"smuRoot\"') ? el : null)) };\n"
        "function gsRender() { return 'gs'; } function showMainTab() {} function setInterval() { return 1; } function clearInterval() {}\n")
    src = prelude + set_me_up_ui.JS + "\n" + body
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        res = subprocess.run([NODE, f.name], capture_output=True, timeout=30)
    finally:
        os.unlink(f.name)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    return json.loads(res.stdout.decode("utf-8"))


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_the_screens_parse_and_draw_each_state():
    plan = sm.make_plan(facts(voice_available=True, voice_licence_ok=True, voice_bytes=5_300_000_000), True)
    short = sm.make_plan(facts(free=5e9), True)
    little = sm.make_plan(facts(gb=32), True, chat="gemma-4-26b-a4b")
    job = {"state": "running", "warning": "Gemma 4 12B did not finish downloading. It will try again by itself.", "error": "", "message": "",
           "steps": [{"key": "chat", "label": "Gemma 4 12B", "total": 7600000000, "done": 4900000000, "state": "running", "percent": 64},
                     {"key": "decision", "label": "Kev-4B", "total": 3000000000, "done": 3000000000, "state": "done", "percent": 100},
                     {"key": "engine", "label": "llama.cpp engine", "total": 400000000, "done": 0, "state": "waiting", "percent": 0}]}
    done = dict(job, state="done", warning="", message="Installed. The talking jobs use Gemma 4 12B and quick decisions use Kev-4B.")
    look = {"question": sm.WORDS["question"], "questionHint": sm.WORDS["question_hint"], "card": {"name": "RTX", "memoryGb": 24}, "freeGb": 310, "drive": "D:", "cardNote": "", "job": None}
    out = run_js(f"""
      smu.open = true; smu.look = {json.dumps(look)};
      const shots = [];
      smuDraw(); shots.push(el.innerHTML);
      smu.game = true; smu.plan = {json.dumps(plan)}; smuDraw(); shots.push(el.innerHTML);
      smu.plan = {json.dumps(short)}; smuDraw(); shots.push(el.innerHTML);
      smu.plan = {json.dumps(little)}; smuDraw(); shots.push(el.innerHTML);
      smu.job = {json.dumps(job)}; smuDraw(); shots.push(el.innerHTML);
      smu.job = {json.dumps(done)}; smuDraw(); shots.push(el.innerHTML);
      gs = {{setMeUp: {{show: true}}}}; shots.push(smuBlock(), ovSetMeUp({{setMeUp: {{show: true, running: false}}}}), ovSetMeUp({{setMeUp: {{show: false}}}}));
      console.log(JSON.stringify(shots));
    """)
    ask, pick, nodisk, amber, running, finished_, block, card_, hidden = out
    assert "Do you play Skyrim on this PC?" in ask and "No, another PC" in ask and "Here is what fits this PC" in ask
    assert pick.count('type="radio"') == 5 and 'value="gemma-4-12b" checked' in pick and sm.WORDS["verdict_room"] in pick and sm.WORDS["warn_game"] not in pick
    assert sm.WORDS["verdict_no_game"] in pick and pick.count(" disabled") >= 2 and "Gemma 4 31B" in pick
    assert "Install 3 things" in pick and '<input type="checkbox"' in pick and "Voice: Higgs" in pick
    assert "to download" in pick and "disabled" not in pick.split("Install 3 things")[0].rsplit("<button", 1)[-1]
    assert "needed" in nodisk and "smu-warn" in nodisk and "Install 3 things</button>" in nodisk and 'class="primary" disabled' in nodisk
    assert sm.WORDS["warn_game"] in amber and sm.WORDS["verdict_little_game"] in amber and 'value="gemma-4-26b-a4b" checked' in amber
    assert "4.9 GB of 7.6 GB" in running and "width:64%" in running and "Waiting" in running and "Done" in running and "did not finish downloading" in running and "Cancel" in running
    assert "keeps going" not in running and "close this page" not in running.lower() and "Cancel" in running
    assert "Installed. The talking jobs use Gemma 4 12B" in finished_ and "Tell SkyrimNet" in finished_
    assert "Set me up" in block and "pick the models yourself" in block and "Set me up" in card_ and hidden == ""


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_the_welcome_step_and_overview_use_the_block_and_the_card():
    import get_started_ui
    import overview_ui
    assert "smuBlock" in get_started_ui.JS and "ovSetMeUp" in overview_ui.JS


def test_the_routes_and_the_service_are_wired(proxy_module):
    from tests.route_paths import route_paths
    paths = route_paths(proxy_module.app)
    assert {"/config/set-me-up", "/config/set-me-up/plan", "/config/set-me-up/install", "/config/set-me-up/cancel", "/config/set-me-up/job", "/config/set-me-up/dismiss"} <= paths
    assert isinstance(proxy_module.set_me_up_svc, sm.SetMeUp)


# ---- the card and the block only show on a brand-new install (2026-10-26) ------------------------------------------------------------------------
class _Fresh:
    """A fresh install as the program sees it; each test turns one thing on."""
    def __init__(self, pm, mp):
        self.cfg = {}
        self.models, self.providers, self.pcs, self.routes, self.decision = [], [], [], {}, False
        mp.setattr(pm.set_me_up_svc, "_load_cfg", lambda: self.cfg)
        mp.setattr(pm.set_me_up_svc, "_save_cfg", lambda c: self.cfg.update(c))
        mp.setattr(pm.local_svc, "installed", lambda: self.models)

        async def provs(*a, **k):
            return self.providers
        mp.setattr(pm.poll_cache, "shared", provs)
        mp.setattr(pm.remote_svc, "all", lambda: self.pcs)
        mp.setattr(pm, "_routing_settings", lambda: {"routes": self.routes})
        mp.setattr(pm, "_overview_service", lambda h: {"installed": self.decision})

    async def show(self, pm):
        return await pm._set_me_up_show()


@pytest.mark.parametrize("turn_on", [
    lambda f: f.models.append("m"),
    lambda f: f.providers.append({"id": "openai", "state": "ok"}),
    lambda f: f.providers.append({"id": "claude", "state": "ok"}),            # a signed-in subscription
    lambda f: f.pcs.append("pc"),
    lambda f: f.routes.update({"dialogue": "x"}),
    lambda f: setattr(f, "decision", True),
    lambda f: f.cfg.update({"set_me_up": {"used": True}}),
])
def test_each_sign_of_a_working_setup_hides_the_card_for_good(proxy_module, monkeypatch, turn_on):
    import asyncio
    f = _Fresh(proxy_module, monkeypatch)
    assert asyncio.run(f.show(proxy_module)) is True, "a brand-new install shows it"
    f.providers.append({"id": "ollama", "state": "ok"})                      # a local engine alone is not a provider the player set up
    f.providers.append({"id": "openai", "state": "unset"})
    assert asyncio.run(f.show(proxy_module)) is True
    turn_on(f)
    assert asyncio.run(f.show(proxy_module)) is False
    f.models.clear(); f.providers.clear(); f.pcs.clear(); f.routes.clear(); f.decision = False       # deleting everything later
    assert asyncio.run(f.show(proxy_module)) is False and (f.cfg.get("set_up_before") or f.cfg.get("set_me_up"))


def test_get_started_and_the_local_models_tab(proxy_module, monkeypatch):
    import asyncio, local_models_ui, set_me_up_ui
    f = _Fresh(proxy_module, monkeypatch)
    monkeypatch.setattr(proxy_module, "_get_started_state", lambda: {})
    from tests.test_set_me_up import sm as _sm  # noqa: F401
    assert asyncio.run(proxy_module.get_started_get())["setMeUp"] == {"show": True}
    f.models.append("m")
    assert asyncio.run(proxy_module.get_started_get())["setMeUp"] == {"show": False}
    assert "smuOpen()" in local_models_ui.LOCAL_MODELS_JS and ">Set me up<" in local_models_ui.LOCAL_MODELS_JS
    assert "gs.setMeUp" in set_me_up_ui.JS and "keepsGoing" not in set_me_up_ui.JS


# ---- Gemma 4 E2B and E4B (2026-10-26) --------------------------------------------------------------------------------------------------------------------
def test_the_two_small_gemmas_are_whole_and_have_their_header_facts():
    for mid, blocks, shared in (("gemma-4-e2b", 35, 20), ("gemma-4-e4b", 42, 18)):
        spec = lm.CATALOG_BY_ID[mid]
        assert {f["role"] for f in spec["files"]} == {"model", "mmproj"} and spec["vision"] and spec["repo"].startswith("lmstudio-community/gemma-4-E")
        assert all(len(f["sha256"]) == 64 and f["size"] > 100_000_000 for f in spec["files"])
        kv = sm.CACHE_FACTS[mid]
        assert kv["gemma4.block_count"] == blocks and kv["gemma4.attention.shared_kv_layers"] == shared and mid in sm.CHAT_CANDIDATES
        assert sm.facts_of(mid, 1) is not None


def test_layers_that_reuse_the_cache_keep_none():
    import local_engine_memory as mem
    kv = dict(sm.CACHE_FACTS["gemma-4-e4b"])
    with_shared = mem.cache_bytes(sm.facts_of("gemma-4-e4b", 1), 8192, 1)
    kv["gemma4.attention.shared_kv_layers"] = 0
    import gguf_header
    without = mem.cache_bytes(gguf_header.ModelFacts("gemma4", 42, 131072, 1, kv), 8192, 1)
    assert 0 < with_shared < without


@pytest.mark.parametrize("gb,game,chat", [(24, False, "gemma-4-26b-a4b"), (16, False, "gemma-4-12b"), (12, False, "gemma-4-12b"), (8, False, "gemma-4-e2b"), (6, False, None), (4, False, None),
                                          (24, True, "gemma-4-12b"), (16, True, "gemma-4-12b"), (12, True, None), (8, True, None), (6, True, None), (4, True, None)])
def test_the_hardware_table_with_the_small_gemmas(gb, game, chat):
    assert sm.pick_chat(card(gb), game) == chat


def test_a_small_gemma_is_never_started_on_when_it_only_leaves_little():
    opts = sm.chat_options(card(8), False)
    assert next(o for o in opts if o["id"] == "gemma-4-e4b")["verdict"] == "little"
    assert sm.default_chat(opts) == "gemma-4-e2b"
    assert sm.default_chat([o for o in opts if o["id"] != "gemma-4-e2b"]) is None
    assert sm.make_plan(facts(gb=8), False)["chat"] == "gemma-4-e2b"


def test_the_recommended_settings_apply_to_the_new_models():
    import tested_settings as ts
    assert ts.family_of("gemma-4-e2b") == "gemma4" == ts.family_of("gemma-4-e4b")


# ---- one table, with a Status column and one warning line on top (2026-10-26) ----------------------------------------------------------------------------
def test_the_status_words_and_the_only_missing_total():
    p = sm.make_plan(facts(chat_here={"gemma-4-12b"}, chat_running={"gemma-4-12b"}, decision_key="kev-4b", engine_here=True), True)
    rows = {o["id"]: o["status"] for o in p["models"]}
    assert rows["gemma-4-12b"] == "Running" and rows["gemma-4-e4b"] == "–"
    p2 = sm.make_plan(facts(chat_here={"gemma-4-12b"}, decision_key="kev-4b", engine_here=True), True)
    assert {o["id"]: o["status"] for o in p2["models"]}["gemma-4-12b"] == "Installed"
    items = {i["key"]: i["status"] for i in p2["items"]}
    assert items["decision"] == "Installed" and items["engine"] == "Installed"
    assert p["columns"] == ["Model", "Size", "Fits this PC", "Status"]
    # an installed pick counts nothing; a missing one counts only itself
    assert p2["needBytes"] == 0 and p2["installCount"] == 0
    only_model = sm.make_plan(facts(decision_key="kev-4b", engine_here=True), True)
    assert only_model["installCount"] == 1 and only_model["needBytes"] > 0 and not only_model["allHere"]


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_the_table_has_the_columns_no_sentences_and_one_warning_line_on_top():
    little = sm.make_plan(facts(gb=32, chat_here={"gemma-4-26b-a4b"}, chat_running={"gemma-4-26b-a4b"}, voice_available=True, voice_licence_ok=True, voice_bytes=5_300_000_000), True, chat="gemma-4-26b-a4b")
    clean = sm.make_plan(facts(chat_here={"gemma-4-12b"}, decision_key="kev-4b", engine_here=True), True)
    out = run_js(f"""
      smu.open = true; smu.look = {{question: 'Q', card: {{}}, job: null}}; smu.game = true;
      const shots = [];
      smu.plan = {json.dumps(little)}; smuDraw(); shots.push(el.innerHTML);
      smu.plan = {json.dumps(clean)}; smuDraw(); shots.push(el.innerHTML);
      smu.plan = {json.dumps(sm.make_plan(facts(gb=24), True))}; smuDraw(); shots.push(el.innerHTML);
      console.log(JSON.stringify(shots));
    """)
    warned, plain, grey = out
    warn = sm.WORDS["warn_game"]
    assert warned.count(warn) == 1 and warned.index(warn) < warned.index("<table"), "the one warning line is above the table"
    assert warn not in warned.split("</table>")[1]
    assert warned.count("<th>") + warned.count("<th class") == 5 and all(w in warned for w in (">Model<", ">Size<", ">Fits this PC<", ">Status<"))
    assert 'class="dt"' not in warned and sm.WORDS["chat_detail_game"] not in warned and sm.WORDS["installed"] not in warned, "no sentence under a name"
    assert "Running" in warned and "Fits, but leaves little for the game" in warned and sm.WORDS["voice_detail"] in warned
    row = grey.split('value="gemma-4-26b-a4b"')[1].split("</tr>")[0]
    assert "disabled" in row and sm.WORDS["verdict_no_game"] in row, "a row that cannot be picked is greyed and disabled"
    assert warn not in plain and "Use these models" in plain and "Install 0" not in plain and "Installed" in plain


@pytest.mark.asyncio
async def test_using_installed_models_downloads_nothing_and_still_wires_routing():
    local = FakeLocal()
    svc, store = make(local=local, f=facts(chat_here={"gemma-4-12b"}, decision_key="kev-4b", engine_here=True))
    await svc.start(True)
    job = await finished(svc)
    assert job.state == "done" and job.steps == [] and not any(c.startswith(("model:", "engine")) for c in local.calls)
    assert "routing" in store.cfg or store.cfg.get("set_me_up", {}).get("used")


def test_a_kept_decision_model_and_a_kept_chat_model_need_no_room_to_download():
    import decision_engine as de
    f0 = facts()
    spec = de.MODELS[sm.pick_decision(f0.card, None)]
    names = {x.name for x in spec.files()}
    base = sm.make_plan(f0, True)
    kept = sm.make_plan(facts(kept_files=names), True)
    d0, d1 = (next(i for i in p["items"] if i["key"] == "decision") for p in (base, kept))
    assert d0["bytes"] == spec.total_bytes() and d1["bytes"] == 0
    assert kept["needBytes"] == base["needBytes"] - spec.total_bytes()
    chat = next(i for i in base["items"] if i["key"] == "chat")
    c1 = next(i for i in sm.make_plan(facts(chat_kept={chat["id"]}), True)["items"] if i["key"] == "chat")
    assert chat["bytes"] > 0 and c1["bytes"] == 0
