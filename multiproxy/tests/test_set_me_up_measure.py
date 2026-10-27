"""Set me up measures the running game's graphics memory (2026-10-26): stand-in readers only, no real game, no real counters."""
import asyncio
import datetime
import json
import types

import pytest

import gpu_monitor as gm
import set_me_up as sm
from tests.test_set_me_up import NODE, Store, card, facts, make, run_js

GB = 1024


def test_the_buffer_is_the_larger_of_2_gb_and_15_percent():
    assert sm.buffer_mb(6 * GB) == 2 * GB            # 15% would be 0.9 GB
    assert sm.buffer_mb(10 * GB) == 2 * GB           # 15% = 1.5 GB
    assert sm.buffer_mb(20 * GB) == 3 * GB           # 15% = 3 GB
    assert sm.game_room_mb(14 * GB) == 14 * GB + int(2.1 * GB) and sm.game_room_mb(None) == sm.SKYRIM_HEADROOM_MB


@pytest.mark.parametrize("card_gb,game_gb,pick,verdicts", [
    (32, None, "gemma-4-12b", {"gemma-4-31b": "little", "gemma-4-26b-a4b": "little", "gemma-4-12b": "room"}),
    (32, 6, "gemma-4-26b-a4b", {"gemma-4-26b-a4b": "room", "gemma-4-31b": "little"}),
    (32, 14, "gemma-4-12b", {"gemma-4-26b-a4b": "no", "gemma-4-12b": "room"}),
    (24, 14, "gemma-4-e2b", {"gemma-4-12b": "no", "gemma-4-e4b": "little", "gemma-4-e2b": "room"}),
    (16, 6, "gemma-4-e2b", {"gemma-4-12b": "little", "gemma-4-e2b": "room"}),
    (16, 10, None, {"gemma-4-e2b": "no"}),
])
def test_measured_game_memory_decides_the_verdicts_and_the_pick(card_gb, game_gb, pick, verdicts):
    mb = game_gb * GB if game_gb else None
    opts = {o["id"]: o["verdict"] for o in sm.chat_options(card(card_gb), True, mb)}
    for k, v in verdicts.items():
        assert opts[k] == v, (card_gb, game_gb, k, opts)
    assert sm.pick_chat(card(card_gb), True, None, mb) == pick
    assert sm.pick_chat(card(card_gb), True, None, mb) != "gemma-4-31b"


def test_a_measurement_is_used_by_the_plan_and_shown_as_data():
    p = sm.make_plan(facts(gb=32, skyrim_mb=14336, skyrim_when="today"), True)
    g = p["game"]
    assert g["measured"] and g["label"] == "Skyrim is using" and g["mb"] == 14336 and g["bufferLabel"] == "Buffer" and g["bufferMb"] == 2150 and g["when"] == "measured today"
    assert p["chat"] == "gemma-4-12b"
    est = sm.make_plan(facts(gb=32), True)["game"]
    assert not est["measured"] and est["label"] == "Estimate" and est["mb"] == sm.SKYRIM_HEADROOM_MB and est["steps"] == [
        "Start Skyrim and load a save in a busy place, such as Whiterun.", "Come back here.", "Press Measure."] and est["button"] == "Measure"
    assert sm.make_plan(facts(gb=32), False)["game"] is None, "nothing about the game when it is not on this PC"


# ---- the measuring itself -----------------------------------------------------------------------------------------------------------------
def sample(skyrim_mb, running=True, total=32607):
    return gm.MemorySample(total_mb=total, used_mb=total // 2, skyrim_running=running, skyrim_mb=skyrim_mb)


def monitor(readings):
    it = iter(readings)
    local = types.SimpleNamespace(servers={}, _gpu=None, installed=lambda: [])
    mon = gm.GpuMonitor(local, lambda: {}, lambda c: None, types.SimpleNamespace(exception=lambda *a, **k: None, info=lambda *a, **k: None), reader=lambda: next(it))
    return mon


async def no_sleep(s):
    return None


def test_the_highest_of_the_readings_is_used():
    mon = monitor([sample(9000), sample(14000), sample(11000)])
    res = asyncio.run(mon.measure_skyrim(3, 10, sleep=no_sleep))
    assert res == {"running": True, "mb": 14000}


def test_a_game_that_is_not_running_gives_no_figure_and_does_not_wait():
    mon = monitor([sample(None, running=False)])
    assert asyncio.run(mon.measure_skyrim(10, 10, sleep=no_sleep)) == {"running": False, "mb": None}


def test_a_reading_that_fails_the_sanity_rule_falls_back_to_the_estimate():
    # build_sample ignores a per-process reading bigger than the card: the game runs but no figure comes out
    s = gm.build_sample(8192, 4096, {111: 200000}, [111], {})
    assert s.skyrim_running and s.skyrim_mb is None
    mon = monitor([s, s])
    assert asyncio.run(mon.measure_skyrim(2, 1, sleep=no_sleep)) == {"running": True, "mb": None}


def service(measure_result, f=None, store=None):
    async def fn(n, secs):
        return measure_result
    return make(f=f or facts(gb=32), store=store, measure_fn=fn)


def test_a_measure_is_remembered_with_its_date_and_used_by_the_next_plan():
    svc, store = service({"running": True, "mb": 14336})
    out = asyncio.run(svc.measure())
    assert out == {"running": True, "measured": True}
    g = store.cfg["gpu_monitor"]            # the one shared figure (skyrim_peak.py), not a figure of Set me up's own
    assert g["skyrimPeakMb"] == 14336 and g["skyrimPeakDate"] == datetime.date.today().isoformat() and "set_me_up_measure" not in store.cfg
    p = asyncio.run(svc.plan(True))
    assert p["game"]["measured"] and p["game"]["when"] == "measured today" and p["game"]["mb"] == 14336
    again, _ = make(f=facts(gb=32), store=store)               # reopening Set me up shows it, dated
    assert asyncio.run(again.plan(True))["game"]["when"] == "measured today"


def test_not_running_or_unreadable_changes_nothing():
    svc, store = service({"running": False, "mb": None})
    assert asyncio.run(svc.measure()) == {"running": False, "measured": False}
    assert "gpu_monitor" not in store.cfg
    assert not asyncio.run(svc.plan(True))["game"]["measured"]


def test_going_on_without_measuring_installs_exactly_as_before():
    """No measurement: the estimate stays, the verdicts and the pick are today's, and the install path is untouched (no extra warning, same button)."""
    svc, store = make(f=facts(gb=32))
    p = asyncio.run(svc.plan(True))
    before = sm.make_plan(facts(gb=32), True)
    assert p["chat"] == before["chat"] == "gemma-4-12b" and p["canInstall"] and p["warning"] == "" and p["chatWarning"] == ""
    assert [o["verdict"] for o in p["models"]] == [o["verdict"] for o in before["models"]]
    async def go():
        job = await svc.start(True)
        assert job.plan["canInstall"] and job.plan["chat"] == "gemma-4-12b"
        await asyncio.wait_for(job.task, 10)
        return job
    job = asyncio.run(go())
    assert job.state == "done" and "gpu_monitor" not in store.cfg


def test_the_remembered_figure_does_not_override_a_new_game_answer():
    svc, store = service({"running": True, "mb": 14336})
    asyncio.run(svc.measure())
    assert asyncio.run(svc.plan(False))["game"] is None


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_the_screen_shows_the_steps_the_button_and_the_data():
    est = sm.make_plan(facts(gb=32), True)
    done = sm.make_plan(facts(gb=32, skyrim_mb=14336, skyrim_when="today"), True)
    look = {"question": "Q", "card": {}, "job": None}
    out = run_js(f"""
      smu.open = true; smu.look = {json.dumps(look)}; smu.game = true;
      const shots = [];
      smu.plan = {json.dumps(est)}; smuDraw(); shots.push(el.innerHTML);
      smu.plan = {json.dumps(done)}; smuDraw(); shots.push(el.innerHTML);
      console.log(JSON.stringify(shots));
    """)
    steps, measured = out
    assert "Estimate" in steps and "12.0 GB" in steps and "Start Skyrim and load a save" in steps and "Come back here." in steps and "Press Measure." in steps and ">Measure<" in steps
    assert "Skyrim is using" in measured and "14.0 GB" in measured and "Buffer" in measured and "2.1 GB" in measured and "measured today" in measured
    assert "Start Skyrim and load a save" not in measured and ">Measure<" in measured
    assert "Install" in measured and "disabled" not in measured.split("smuInstall")[0].rsplit("<button", 1)[-1]
