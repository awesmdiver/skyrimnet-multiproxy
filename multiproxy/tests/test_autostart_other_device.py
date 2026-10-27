"""The voice and the decision model on another device can start by themselves when MultiProxy starts and that device is online (2026-10-23). Real Couriers on spare ports with a fake
engine behind them; "the device comes online" is the first report after it was seen offline."""
import asyncio

import pytest

from tests.test_voice_courier import rig as voice_rig, install as voice_install, PC as VPC, until  # noqa: F401
from tests.test_decision_courier import rig as dec_rig, installed_rig as dec_installed, move_to, PC as DPC  # noqa: F401

pytestmark = pytest.mark.asyncio


async def fresh_report(rg):
    for part in (getattr(rg.courier, "voice", None), getattr(rg.courier, "decisions", None)):
        if part is not None and hasattr(part, "invalidate"):
            part.invalidate()                                  # (the Courier remembers its own state for a moment; a device that was just switched on has none)
    return await rg.remote.client.fetch_report()


async def comes_online(rg):
    """The device is seen offline, then reports again: what happens when it is switched on after MultiProxy started."""
    rg.remotes.mark(rg.remote, False, "gone")
    assert rg.remote.announced is False
    rg.remotes._apply_report(rg.remote, await fresh_report(rg))
    await asyncio.sleep(0.05)


def count_starts(engine):
    real = engine.start
    calls = []

    async def counted(*a, **k):
        calls.append(1)
        return await real(*a, **k)
    engine.start = counted
    return calls


# ---- the hook itself ----
async def test_the_online_hook_runs_once_per_coming_online(voice_rig):
    seen = []
    voice_rig.remotes.online_hooks.append(lambda r: seen.append(r.id))
    r = voice_rig.remote
    voice_rig.remotes._apply_report(r, await r.client.fetch_report())
    voice_rig.remotes._apply_report(r, await r.client.fetch_report())
    n = len(seen)
    assert n <= 1, "report after report is not a coming-online"
    await comes_online(voice_rig)
    await comes_online(voice_rig)
    assert len(seen) == n + 2 and set(seen) == {VPC}


# ---- the voice ----
async def test_the_voice_starts_once_when_its_device_comes_online_and_not_on_every_report(voice_rig):
    j, _ = await voice_install(voice_rig)
    assert j["state"] == "done"
    await voice_rig.client.post("/config/voice/location", json={"pc": VPC})
    assert (await voice_rig.client.post("/config/voice/autostart", json={"on": True, "pc": VPC})).json() == {"autostart": True, "pc": VPC}
    assert any(p["id"] == VPC and p["autostart"] for p in (await voice_rig.client.get("/config/voice/overview")).json()["pcs"])
    await voice_rig.c_engine.stop()
    calls = count_starts(voice_rig.c_engine)
    await comes_online(voice_rig)
    assert await until(lambda: voice_rig.c_engine.state == "ready", 30) and len(calls) == 1
    await voice_rig.c_engine.stop()
    voice_rig.remotes._apply_report(voice_rig.remote, await fresh_report(voice_rig))      # a plain report: the device did not come online
    await asyncio.sleep(0.3)
    assert len(calls) == 1 and voice_rig.c_engine.state != "ready"


async def test_a_deliberate_stop_is_respected_and_a_start_by_hand_lifts_it(voice_rig):
    await voice_install(voice_rig)
    await voice_rig.client.post("/config/voice/location", json={"pc": VPC})
    await voice_rig.client.post("/config/voice/autostart", json={"on": True, "pc": VPC})
    assert (await voice_rig.client.post("/config/voice/pc/stop", json={"id": VPC})).status_code == 200
    calls = count_starts(voice_rig.c_engine)
    await comes_online(voice_rig)
    await asyncio.sleep(0.4)
    assert calls == [] and voice_rig.c_engine.state != "ready"
    assert (await voice_rig.client.post("/config/voice/pc/start", json={"id": VPC})).status_code == 200
    assert await until(lambda: voice_rig.c_engine.state == "ready", 30)
    await voice_rig.c_engine.stop()
    await comes_online(voice_rig)
    assert await until(lambda: voice_rig.c_engine.state == "ready", 30)


async def test_nothing_starts_that_is_not_installed_not_licensed_not_in_use_or_not_asked_for(voice_rig):
    calls = count_starts(voice_rig.c_engine)
    await voice_rig.client.post("/config/voice/location", json={"pc": VPC})
    await voice_rig.client.post("/config/voice/autostart", json={"on": True, "pc": VPC})
    await comes_online(voice_rig)
    await asyncio.sleep(0.3)
    assert calls == [], "not installed on that device"
    await voice_install(voice_rig)
    await voice_rig.c_engine.stop()
    calls.clear()                                           # (the install started it once; that is not what is being counted)
    rep = voice_rig.remote.report
    voice_rig.remote.report = {**rep, "voice": {**rep["voice"], "licenseAccepted": False}}
    voice_rig.remote.announced = False
    for hook in voice_rig.remotes.online_hooks:
        hook(voice_rig.remote)
    await asyncio.sleep(0.3)
    assert calls == [], "license not accepted"
    await voice_rig.client.post("/config/voice/location", json={"pc": "this"})              # the voice runs here now: that device's setting is not used
    await comes_online(voice_rig)
    await asyncio.sleep(0.3)
    assert calls == []
    await voice_rig.client.post("/config/voice/location", json={"pc": VPC})
    await voice_rig.client.post("/config/voice/autostart", json={"on": False, "pc": VPC})
    await comes_online(voice_rig)
    await asyncio.sleep(0.3)
    assert calls == [], "the player switched it off"


async def test_this_device_is_unchanged_and_each_device_has_its_own_choice(voice_rig):
    r = await voice_rig.client.post("/config/voice/autostart", json={"on": True})
    assert r.json() == {"autostart": True} and voice_rig.handles is not None
    cfg = voice_rig.handles and __import__("json").loads(__import__("json").dumps(voice_rig.remotes._load_cfg()))
    assert "voice_autostart_pcs" not in cfg and cfg.get("voice_autostart") is True
    await voice_rig.client.post("/config/voice/autostart", json={"on": False, "pc": VPC})
    cfg = voice_rig.remotes._load_cfg()
    assert cfg["voice_autostart_pcs"] == {VPC: False} and cfg["voice_autostart"] is True
    await voice_rig.client.post("/config/voice/autostart", json={"on": True, "pc": VPC})
    assert "voice_autostart_pcs" not in voice_rig.remotes._load_cfg(), "on is the default: nothing is written"


# ---- on by default (2026-10-24) ----
async def test_the_voice_on_another_device_starts_by_itself_with_no_setting_at_all(voice_rig):
    await voice_install(voice_rig)
    await voice_rig.client.post("/config/voice/location", json={"pc": VPC})          # moved; the player never touched the autostart choice
    assert "voice_autostart_pcs" not in voice_rig.remotes._load_cfg()
    assert any(p["id"] == VPC and p["autostart"] for p in (await voice_rig.client.get("/config/voice/overview")).json()["pcs"])
    await voice_rig.c_engine.stop()
    calls = count_starts(voice_rig.c_engine)
    await comes_online(voice_rig)
    assert await until(lambda: voice_rig.c_engine.state == "ready", 30) and len(calls) == 1


async def test_a_courier_that_restarted_between_two_looks_counts_as_coming_online(voice_rig):
    await voice_install(voice_rig)
    await voice_rig.client.post("/config/voice/location", json={"pc": VPC})
    r = voice_rig.remote
    rep = await fresh_report(voice_rig)
    voice_rig.remotes._apply_report(r, {**rep, "courier": {**rep["courier"], "running_seconds": 4000}, "generated_at": rep.get("generated_at", 0) + 1})
    await voice_rig.c_engine.stop()
    calls = count_starts(voice_rig.c_engine)
    rep2 = await fresh_report(voice_rig)
    voice_rig.remotes._apply_report(r, {**rep2, "courier": {**rep2["courier"], "running_seconds": 3}, "generated_at": rep.get("generated_at", 0) + 2})      # the clock went back: it restarted
    assert await until(lambda: voice_rig.c_engine.state == "ready", 30) and len(calls) == 1


async def test_the_decision_model_on_another_device_starts_by_itself_with_no_setting_at_all(dec_rig):
    await dec_installed(dec_rig)
    await move_to(dec_rig, DPC)
    assert "decision_autostart_pcs" not in dec_rig.remotes._load_cfg()
    assert any(p["id"] == DPC and p["autostart"] for p in (await dec_rig.client.get("/config/decisions/status")).json()["pcs"])
    await dec_rig.c_engine.stop()
    calls = count_starts(dec_rig.c_engine)
    await comes_online(dec_rig)
    assert await until(lambda: dec_rig.c_engine.state == "ready", 30) and len(calls) == 1
    await dec_rig.client.post("/config/decisions/settings", json={"autostartPc": {"pc": DPC, "on": False}})
    assert not any(p["id"] == DPC and p["autostart"] for p in (await dec_rig.client.get("/config/decisions/status")).json()["pcs"])


# ---- the decision model ----
async def test_the_decision_model_starts_when_its_device_comes_online_and_respects_a_stop(dec_rig):
    await dec_installed(dec_rig)
    await move_to(dec_rig, DPC)
    r = await dec_rig.client.post("/config/decisions/settings", json={"autostartPc": {"pc": DPC, "on": True}})
    assert r.status_code == 200, r.text
    assert any(p["id"] == DPC and p["autostart"] for p in (await dec_rig.client.get("/config/decisions/status")).json()["pcs"])
    await dec_rig.c_engine.stop()
    calls = count_starts(dec_rig.c_engine)
    await comes_online(dec_rig)
    assert await until(lambda: dec_rig.c_engine.state == "ready", 30) and len(calls) == 1
    assert (await dec_rig.client.post("/config/decisions/stop", json={"pc": DPC})).status_code == 200
    await comes_online(dec_rig)
    await asyncio.sleep(0.4)
    assert len(calls) == 1 and dec_rig.c_engine.state != "ready", "a deliberate Stop is respected"


async def test_the_decision_model_is_left_alone_when_not_installed_not_in_use_or_not_asked_for(dec_rig):
    calls = count_starts(dec_rig.c_engine)
    await dec_rig.client.post("/config/decisions/settings", json={"autostartPc": {"pc": DPC, "on": True}})
    await comes_online(dec_rig)
    await asyncio.sleep(0.3)
    assert calls == [], "not installed there"
    unknown = await dec_rig.client.post("/config/decisions/settings", json={"autostartPc": {"pc": "nobody", "on": True}})
    assert unknown.status_code == 404


# ---- the screen ----
async def test_the_menus_offer_the_same_words_for_another_device_and_the_waiting_line_only_when_offline():
    import decision_ui
    import voice_ui
    v = open(voice_ui.__file__, encoding="utf-8").read()
    d = open(decision_ui.__file__, encoding="utf-8").read()
    assert "Start when MultiProxy starts" in v and "Start when MultiProxy starts" in d
    assert "if (k === 'this') h += '<button onclick=\"vcAuto(" not in v, "the voice item is no longer for this device only"
    assert "else if (k !== 'this' && i.online === false)" in v and "Waiting for ' + esc(vcShowPc(k)) + ' to come online" in v
    assert "i.online === false" in d and "to come online" in d
    for src in (v, d):
        assert "Starts by itself when MultiProxy starts and when this device comes online. Change it in the ⋯ menu." in src
        assert "This won\\'t start by itself. <a onclick=" in src and ">Start when MultiProxy starts</a>" in src


# ---- moving the voice off this device stops the copy here (2026-10-26) ----
async def test_moving_the_voice_to_another_device_stops_the_engine_here(voice_rig):
    j, _ = await voice_install(voice_rig)
    assert j["state"] == "done"
    await voice_rig.client.post("/config/voice/location", json={"pc": "this"})          # (the install above put it on the other device)
    stops = []
    real = voice_rig.g_engine.stop

    async def counted(*a, **k):
        stops.append(1)
        return await real(*a, **k)
    voice_rig.g_engine.stop = counted
    d = (await voice_rig.client.post("/config/voice/location", json={"pc": VPC})).json()
    assert d["location"] == VPC and d["moveWarning"] == "" and stops, "the copy here was stopped"

    async def broken(*a, **k):
        raise RuntimeError("would not stop")
    await voice_rig.client.post("/config/voice/location", json={"pc": "this"})
    voice_rig.g_engine.stop = broken
    d = (await voice_rig.client.post("/config/voice/location", json={"pc": VPC})).json()
    assert d["moveWarning"] == "Couldn't stop the model on this PC. Please stop it manually."
