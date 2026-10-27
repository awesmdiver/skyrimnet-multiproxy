"""The Decisions tab's one click, server side (2026-10-23): "Set it up" installs on a device with the recommended model that fits, and the decisions move there only when
the install has finished and the model is running. A failed or cancelled install changes nothing. Uses the same rig as test_decision_courier.py: a real Courier on a spare
port with a fake llama-server behind it, and the main device's real decision routes."""
import asyncio

import pytest

import decision_engine as de
import file_transfer
from tests.decision_helpers import hardware
from tests.test_decision_courier import PC, rig, until      # noqa: F401  (rig is a fixture)

pytestmark = pytest.mark.asyncio


async def one_click(rig, pc=PC, **extra):
    r = await rig.client.post("/config/decisions/install", json={"pc": pc, "source": "gaming-pc", "recommended": True, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_decisions_stay_where_they_are_while_the_install_runs_and_move_when_it_is_ready(rig, monkeypatch):
    orig = file_transfer.send_file

    async def slow(*a, **k):
        await asyncio.sleep(1.0)
        return await orig(*a, **k)
    monkeypatch.setattr(file_transfer, "send_file", slow)
    st = await one_click(rig)
    job = rig.handles.remote.jobs[PC]
    assert job.state == "running" and st["pcs"][0]["job"]["state"] == "running"
    assert rig.handles.settings()["location"] == "this" and st["location"] == "this", "decisions keep going to this device until it is ready"
    assert await until(lambda: job.state != "running", 30) and job.state == "done"
    assert await until(lambda: rig.handles.settings()["location"] == PC, 5), "only now do the decisions move"
    assert await until(lambda: rig.c_engine.state == "ready", 10), "and it is running there"


async def test_a_cancelled_install_changes_nothing(rig, monkeypatch):
    orig = file_transfer.send_file

    async def slow(*a, **k):
        await asyncio.sleep(2)
        return await orig(*a, **k)
    monkeypatch.setattr(file_transfer, "send_file", slow)
    await one_click(rig)
    await asyncio.sleep(0.3)
    assert (await rig.client.post("/config/decisions/cancel", json={"pc": PC})).json() == {"cancelled": True}
    job = rig.handles.remote.jobs[PC]
    assert await until(lambda: job.state != "running", 10) and job.state == "cancelled"
    await asyncio.sleep(0.3)
    assert rig.handles.settings()["location"] == "this", "the decisions did not move"
    assert not rig.c_engine.installed()


async def test_a_failed_install_changes_nothing_and_says_why(rig, monkeypatch):
    async def broken(*a, **k):
        raise file_transfer.TransferError("The copy to {pc} stopped.")
    monkeypatch.setattr(file_transfer, "send_file", broken)
    await one_click(rig)
    job = rig.handles.remote.jobs[PC]
    assert await until(lambda: job.state != "running", 20) and job.state == "error" and job.error
    await asyncio.sleep(0.3)
    assert rig.handles.settings()["location"] == "this"
    d = (await rig.client.get("/config/decisions/status")).json()
    assert d["pcs"][0]["job"]["state"] == "error" and d["pcs"][0]["job"]["error"] == job.error and d["location"] == "this"


async def test_a_device_that_does_not_fit_is_refused_before_anything_moves(rig):
    rig.c_hw["hw"] = hardware(free_disk_gb=0.2)
    r = await rig.client.post("/config/decisions/install", json={"pc": PC, "source": "gaming-pc", "recommended": True})
    job = rig.handles.remote.jobs.get(PC)
    if r.status_code == 200 and job is not None:
        assert await until(lambda: job.state != "running", 20) and job.state == "error"
    else:
        assert r.status_code >= 400
    assert rig.handles.settings()["location"] == "this"


async def test_the_one_click_picks_kev_4b_when_the_card_is_big_enough(rig):
    rig.cfg["decisions"] = {"model": "lev"}                       # (the saved choice the Model box showed before: not installed anywhere, so the recommended one wins)
    await one_click(rig)
    assert rig.handles.settings()["model"] == "kev-4b"
    job = rig.handles.remote.jobs[PC]
    assert await until(lambda: job.state != "running", 30) and job.state == "done"


async def test_the_one_click_picks_laya_for_a_card_under_4_gb(rig):
    rig.c_hw["hw"] = hardware(free_vram_gb=1.5, total_gb=3.0)
    await one_click(rig)
    assert rig.handles.settings()["model"] == "laya"
    job = rig.handles.remote.jobs[PC]
    assert await until(lambda: job.state != "running", 30)


async def test_setting_up_this_device_moves_the_decisions_back_here_when_it_is_ready(rig):
    # decisions run on the other device first
    job_json = await rig.client.post("/config/decisions/install", json={"pc": PC, "source": "gaming-pc"})
    job = rig.handles.remote.jobs[PC]
    assert await until(lambda: job.state != "running", 30) and job.state == "done"
    assert await until(lambda: rig.handles.settings()["location"] == PC, 5)
    st = await one_click(rig, pc="this")
    assert rig.handles.settings()["location"] == PC, "still the other device while this one installs"
    ej = rig.g_engine.job
    assert await until(lambda: ej.state != "running", 30) and ej.state == "done"
    assert await until(lambda: rig.handles.settings()["location"] == "this", 5)
