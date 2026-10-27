"""The graphics memory guard (2026-10-26): stops the built-in chat model before Skyrim runs out of graphics memory.

Everything runs against stand-in readings and a stand-in clock: no real game, no real card, no real model (the few tests that need a running model use the fake llama-server
the local-models tests already use).
"""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import gpu_guard as gg
import gpu_monitor as gm
from test_local_models_routing import _chat, client, local, running  # noqa: F401  (fixtures: a real LocalModels with a fake engine)

log = logging.getLogger("test-guard")
GB = 1024


# ---- stand-ins --------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds


class FakeLocal:
    """What the guard uses of LocalModels: servers, stop, installed, guard_stops, the card choice."""

    def __init__(self, ids=("chat",), choices=None):
        self.servers = {i: SimpleNamespace(state="running", proc=SimpleNamespace(returncode=None)) for i in ids}
        self.guard_stops, self.stopped, self.choices = {}, [], choices or {}

    async def stop(self, mid):
        self.servers[mid].state = "stopped"
        self.stopped.append(mid)

    def installed(self):
        return [SimpleNamespace(id=i, name=f"Model {i}") for i in self.servers]

    def _choice_for(self, mid):
        return self.choices.get(mid)


class FakeMonitor:
    def __init__(self, cards=((30 * GB, 32 * GB),), skyrim_mb=20 * GB):
        self.cards, self.reads, self.alive, self.last_skyrim_mb = [tuple(c) for c in cards], 0, True, skyrim_mb

    def set_free(self, free_mb, card=0):
        total = self.cards[card][1]
        self.cards[card] = (total - free_mb, total)

    def card_memory(self):
        self.reads += 1
        return list(self.cards)

    def skyrim_alive(self):
        return self.alive

    async def sample(self, force=False):
        return SimpleNamespace(skyrim_mb=self.last_skyrim_mb)


def make(tmp_path, local=None, monitor=None, running=True, on=True, cards=None):
    clock, flags = Clock(), {"game": running, "on": on}
    local = local or FakeLocal()
    monitor = monitor or FakeMonitor()
    g = gg.GraphicsGuard(local, monitor, game_running=lambda: flags["game"], enabled=lambda: flags["on"], events_path=str(tmp_path / "events.jsonl"), log=log,
                         cards_fn=lambda: cards or [], clock=clock, sleep=clock.sleep)
    return g, local, monitor, flags, clock


async def steps(g, n):
    for _ in range(n):
        await g.step()


NEARLY_FULL = 1000      # MB free on a 32 GB card (the reserve there is about 1.6 GB)


# ---- the rule --------------------------------------------------------------------------------------------------
def test_the_reserve_is_five_percent_of_the_card_but_never_under_one_and_a_half_gb():
    assert gg.threshold_mb(32 * GB) == pytest.approx(1638.4)     # 32 GB card: about 1.6 GB
    assert gg.threshold_mb(24 * GB) == 1536                      # 24 GB card: 5 percent would be 1.2 GB, so the floor
    assert gg.threshold_mb(16 * GB) == 1536                      # 16 GB card: 5 percent would be 0.8 GB, so the floor


@pytest.mark.asyncio
async def test_plenty_of_free_memory_never_trips(tmp_path):
    g, local, mon, *_ = make(tmp_path)
    mon.set_free(3 * GB)
    await steps(g, 60)
    assert local.stopped == [] and mon.reads == 60


@pytest.mark.asyncio
async def test_low_for_five_seconds_in_a_row_trips_once(tmp_path):
    g, local, mon, *_ = make(tmp_path)
    mon.set_free(NEARLY_FULL)
    await steps(g, 3)                   # reads at 0, 2 and 4 seconds: only four seconds of low so far
    assert local.stopped == []
    await steps(g, 1)                   # the read at 6 seconds: low for six
    assert local.stopped == ["chat"]
    mon.set_free(NEARLY_FULL)
    await steps(g, 20)                  # the model is stopped now: nothing left to stop, nothing is read
    assert local.stopped == ["chat"]


@pytest.mark.asyncio
async def test_one_short_dip_does_not_trip(tmp_path):
    g, local, mon, *_ = make(tmp_path)
    for _ in range(10):                 # two seconds low (a cell loading), then fine again, over and over
        mon.set_free(NEARLY_FULL)
        await steps(g, 1)
        mon.set_free(3 * GB)
        await steps(g, 1)
    assert local.stopped == []


@pytest.mark.asyncio
async def test_a_failed_reading_does_not_add_up_to_five_seconds(tmp_path):
    g, local, mon, *_ = make(tmp_path)
    mon.set_free(NEARLY_FULL)
    await steps(g, 2)
    mon.card_memory = lambda: []
    await steps(g, 1)
    mon.card_memory = lambda: [(32 * GB - NEARLY_FULL, 32 * GB)]
    await steps(g, 2)
    assert local.stopped == [], "the count started again after the gap"


# ---- what it stops ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_stops_only_the_models_the_built_in_engine_started_on_the_squeezed_card(tmp_path):
    local = FakeLocal(("chat", "second"))
    local.servers["idle"] = SimpleNamespace(state="stopped", proc=None)      # not running: not touched
    local.servers["second"].state = "running"
    mon = FakeMonitor(cards=[(1 * GB, 8 * GB), (30 * GB, 32 * GB)])
    cards = [SimpleNamespace(id="GPU-a", vendor="nvidia"), SimpleNamespace(id="GPU-b", vendor="nvidia")]
    local.choices = {"chat": {"id": "GPU-a"}}                               # "chat" is kept on the small card; "second" is Automatic (the biggest card)
    g, *_ = make(tmp_path, local, mon, cards=cards)
    mon.cards[1] = (32 * GB - NEARLY_FULL, 32 * GB)                         # the big card is the squeezed one
    await steps(g, 4)
    assert local.stopped == ["second"], "the model on the other card (plenty free) keeps running"


@pytest.mark.asyncio
async def test_a_chosen_card_is_followed(tmp_path):
    local = FakeLocal(("chat",), choices={"chat": {"id": "GPU-b"}})
    mon = FakeMonitor(cards=[(1 * GB, 16 * GB), (32 * GB - NEARLY_FULL, 32 * GB)])
    cards = [SimpleNamespace(id="GPU-a", vendor="nvidia"), SimpleNamespace(id="GPU-b", vendor="nvidia"), SimpleNamespace(id="PCI-x", vendor="intel")]
    g, *_ = make(tmp_path, local, mon, cards=cards)
    await steps(g, 4)
    assert local.stopped == ["chat"]


# ---- when it reads --------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_reads_nothing_while_the_game_is_closed(tmp_path):
    g, local, mon, flags, _ = make(tmp_path, running=False)
    mon.set_free(NEARLY_FULL)
    with pytest.raises(asyncio.TimeoutError):       # it just waits for the mod's "started"
        await asyncio.wait_for(g.step(), 0.05)
    assert mon.reads == 0 and local.stopped == []


@pytest.mark.asyncio
async def test_started_wakes_it(tmp_path):
    g, local, mon, flags, _ = make(tmp_path, running=False)
    waiting = asyncio.ensure_future(g.step())
    await asyncio.sleep(0.01)
    assert not waiting.done()
    flags["game"] = True
    g.notify_started()
    await asyncio.wait_for(waiting, 1)
    await g.step()
    assert mon.reads == 1


@pytest.mark.asyncio
async def test_the_switch_off_means_no_reading_at_all(tmp_path):
    g, local, mon, flags, _ = make(tmp_path, on=False)
    mon.set_free(NEARLY_FULL)
    await steps(g, 30)
    assert mon.reads == 0 and local.stopped == []
    flags["on"] = True
    await steps(g, 4)
    assert local.stopped == ["chat"], "turning it on again protects from then on"


@pytest.mark.asyncio
async def test_no_model_running_means_no_reading(tmp_path):
    local = FakeLocal(())
    g, _, mon, *_ = make(tmp_path, local)
    await steps(g, 10)
    assert mon.reads == 0


@pytest.mark.asyncio
async def test_a_crash_with_no_closed_notice_is_noticed_from_the_missing_program(tmp_path):
    g, local, mon, flags, _ = make(tmp_path)
    await steps(g, gg.PROCESS_CHECK_EVERY - 1)
    reads = mon.reads
    mon.alive = False                                # the game crashed; the mod never said "closed"
    await steps(g, 1)
    assert g.game_up() is False
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(g.step(), 0.05)
    assert mon.reads == reads, "reading stopped with the game"


@pytest.mark.asyncio
async def test_a_program_check_that_cannot_tell_does_not_stop_the_guard(tmp_path):
    g, local, mon, *_ = make(tmp_path)
    mon.alive = None
    await steps(g, gg.PROCESS_CHECK_EVERY * 2)
    assert g.game_up() is True


# ---- what is recorded and said -------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_every_event_is_recorded_and_none_is_pruned(tmp_path):
    g, local, mon, *_ = make(tmp_path)
    for n in range(3):
        local.servers["chat"].state = "running"
        mon.set_free(NEARLY_FULL + n)
        await steps(g, 4)
    lines = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    e = lines[0]
    assert e["model"] == "Model chat" and e["freeMb"] == NEARLY_FULL and e["totalMb"] == 32 * GB and e["skyrimMb"] == 20 * GB and e["time"]
    assert e["reserveMb"] == 1638


@pytest.mark.asyncio
async def test_the_note_is_there_after_a_stop_and_gone_when_the_game_closes(tmp_path):
    g, local, mon, flags, _ = make(tmp_path)
    mon.set_free(NEARLY_FULL)
    await steps(g, 4)
    assert set(local.guard_stops) == {"chat"}
    flags["game"] = False
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(g.step(), 0.05)
    assert local.guard_stops == {}, "once the game is closed it is just a stopped model"
    assert local.servers["chat"].state == "stopped", "and it was not started again by the guard"


def test_the_words_are_in_one_place_and_the_notice_names_the_model():
    assert gg.notice("Gemma 4 26B A4B") == "Stopped Gemma 4 26B A4B: Skyrim's graphics memory was nearly full."
    assert set(gg.WORDS) == {"notice", "switchTitle", "switchHint"}


# ---- with a real LocalModels (a fake engine process behind it) -----------------------------------------------------------
@pytest.mark.asyncio
async def test_stop_note_start_with_the_real_model_and_the_request_answer_is_the_stop_buttons(running, client, tmp_path):
    local = running
    mon = FakeMonitor()
    mon.set_free(NEARLY_FULL)
    g, *_ = make(tmp_path, local, mon)
    await steps(g, 4)
    assert local.state_of("tiny-chat")[0] == "stopped"
    assert local.guard_note("tiny-chat") == "Stopped Tiny Chat: Skyrim's graphics memory was nearly full."
    # what Skyrim's requests meet is exactly what a model stopped with the Stop button gives, which Routing already hands to the next AI in the job's route
    r = await client.post("/v1/chat/completions", json=_chat())
    assert r.status_code == 503 and "stopped" in json.dumps(r.json())
    import routing
    assert routing.failure_kind(r.status_code, json.dumps(r.json())) == "failed"
    # more readings, even good ones: the guard never starts it again by itself
    mon.set_free(8 * GB)
    await steps(g, 10)
    assert local.state_of("tiny-chat")[0] == "stopped"
    # the snapshot (the Local models tab) carries the note, and the Start button path clears it
    snap = await local.snapshot()
    assert next(m for m in snap["installed"] if m["id"] == "tiny-chat")["guardNote"].startswith("Stopped Tiny Chat: Skyrim's graphics memory")
    s = await local.start("tiny-chat")
    await s.ready.wait()
    assert s.state == "running" and local.guard_note("tiny-chat") == ""
    snap = await local.snapshot()
    assert next(m for m in snap["installed"] if m["id"] == "tiny-chat")["guardNote"] == ""


def test_the_overview_says_it_once_with_a_start_button():
    from test_overview import base
    inp = base(local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "stopped",
                              "guardNote": "Stopped gemma-4-12b: Skyrim's graphics memory was nearly full."}])
    a = [x for x in inp["attention"] if "Skyrim's graphics memory was nearly full" in x["line"]]
    assert len(a) == 1 and a[0]["level"] == "warn" and a[0]["action"]["kind"] == "start" and a[0]["action"]["url"] == "/config/local-models/start"
    assert a[0]["action"]["body"] == {"id": "gemma"}
    quiet = base()["attention"]
    assert not any("Skyrim's graphics memory was nearly full" in x["line"] for x in quiet)


# ---- the Settings switch ------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_switch_is_on_by_default_is_saved_and_drives_the_guard(proxy_module, client):
    r = await client.get("/config/settings")
    s = r.json()
    assert s["graphicsGuard"] is True
    assert s["graphicsGuardWords"] == {"title": gg.WORDS["switchTitle"], "hint": gg.WORDS["switchHint"]}
    assert proxy_module.graphics_guard._enabled() is True
    r = await client.post("/config/settings", json={"graphicsGuard": False})
    assert r.json()["graphicsGuard"] is False
    assert proxy_module._load_config()["graphics_guard"] is False and proxy_module.graphics_guard._enabled() is False
    r = await client.post("/config/settings", json={"graphicsGuard": True})
    assert r.json()["graphicsGuard"] is True and proxy_module.graphics_guard._enabled() is True


def test_the_screens_show_the_note_and_the_switch():
    import local_models_ui
    import overview_ui
    import settings_ui
    assert "m.guardNote" in local_models_ui.__dict__.get("HTML", "") or "guardNote" in Path(local_models_ui.__file__).read_text(encoding="utf-8")
    assert "x.action" in Path(overview_ui.__file__).read_text(encoding="utf-8")
    assert "graphicsGuard" in Path(settings_ui.__file__).read_text(encoding="utf-8")


# ---- the reading itself ---------------------------------------------------------------------------------------------------------
def test_every_card_is_read_in_the_drivers_order_and_a_bad_line_keeps_its_place():
    assert gm.parse_nvidia_cards("2000, 32607\n500, 16384\n") == [(2000, 32607), (500, 16384)]
    assert gm.parse_nvidia_cards("[N/A], [N/A]\n500, 16384\n") == [None, (500, 16384)]
    assert gm.parse_nvidia_cards("") == []


def test_the_monitor_reads_the_cards_with_the_light_query(monkeypatch):
    asked = []
    monkeypatch.setattr(gm, "_run", lambda cmd, timeout=15: asked.append(cmd) or "9000, 32607\n")
    mon = gm.GpuMonitor(SimpleNamespace(), lambda: {}, lambda d: None, log)
    assert mon.card_memory() == [(9000, 32607)]
    assert asked == [["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]]
