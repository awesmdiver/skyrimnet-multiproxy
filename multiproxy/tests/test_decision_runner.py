"""The decision model is installed but its engine software is not (2026-10-23): the tab says it plainly and offers one button that gets the runner alone, on this device and on
another device, and the model's files are neither touched nor downloaded again."""
import hashlib
from pathlib import Path

import pytest

import decision_engine as de
from tests.decision_helpers import installed_engine, make_engine
from tests.test_decision_courier import PC, install, installed_rig, rig, until  # noqa: F401

pytestmark = pytest.mark.asyncio


def model_fingerprint(engine) -> list:
    d = engine.model_dir(engine.spec().key)
    return [(p.name, p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(d.iterdir()) if p.is_file()]


async def a_machine_that_lost_its_runner(tmp_path):
    engine, _settings, _src = await installed_engine(tmp_path)
    await engine.stop()
    exe = tmp_path / "llama-server.exe"
    seen = {"exe": None, "fetched": 0}
    engine._exe_fn = lambda: seen["exe"]

    async def fetch(progress):
        seen["fetched"] += 1
        progress(5, 10)
        seen["exe"] = exe
        return exe
    engine._ensure_engine = fetch
    return engine, seen


async def test_a_model_that_is_there_without_its_runner_says_so_and_start_says_the_same(tmp_path):
    engine, seen = await a_machine_that_lost_its_runner(tmp_path)
    s = engine.status()
    assert s["installed"] is True and s["runnerMissing"] is True and s["state"] == "stopped" and s["label"] == "Needs the engine"
    with pytest.raises(de.DecisionError) as e:
        await engine.start()
    assert e.value.status == 409 and e.value.message == "The engine software is missing. Press Get the engine."
    assert "Install the decision model again" not in e.value.message


async def test_get_the_runner_fetches_only_the_runner_leaves_the_model_alone_and_start_then_works(tmp_path):
    engine, seen = await a_machine_that_lost_its_runner(tmp_path)
    before = model_fingerprint(engine)
    job = engine.start_runner_install()
    assert job.runner_only is True and job.to_json()["runnerOnly"] is True and engine.status()["state"] == "installing"
    await job.task
    assert job.state == "done", job.error
    assert seen["fetched"] == 1 and model_fingerprint(engine) == before, "the model's files were not touched"
    assert engine.store.listing()["totalBytes"] >= 0
    s = engine.status()
    assert s["state"] == "stopped" and s["runnerMissing"] is False and s["label"] == "Stopped"
    await engine.start()
    assert engine.status()["ready"] is True
    await engine.close()


async def test_a_failed_runner_download_says_so_and_the_model_stays_installed(tmp_path):
    engine, seen = await a_machine_that_lost_its_runner(tmp_path)

    async def broken(progress):
        raise de.DecisionError(502, "The engine software didn't download.")
    engine._ensure_engine = broken
    job = engine.start_runner_install()
    await job.task
    assert job.state == "error" and "engine software" in job.error
    s = engine.status()
    assert s["installed"] is True and s["runnerMissing"] is True and s["state"] == "stopped"


async def test_asking_for_the_runner_makes_no_sense_without_the_model_or_when_it_is_there(tmp_path):
    engine, _s, _src = make_engine(tmp_path)
    with pytest.raises(de.DecisionError) as e:
        engine.start_runner_install()
    assert "isn't installed yet" in e.value.message
    engine2, _s2, _src2 = await installed_engine(tmp_path / "again")
    with pytest.raises(de.DecisionError) as e2:
        engine2.start_runner_install()
    assert "already here" in e2.value.message
    await engine2.close()


async def test_a_model_that_is_missing_still_goes_the_old_install_way(tmp_path):
    engine, _s, _src = make_engine(tmp_path)
    s = engine.status()
    assert s["installed"] is False and s["runnerMissing"] is False and s["state"] == "not_installed"
    job = engine.start_install()
    await job.task
    assert job.state == "done" and job.runner_only is False
    await engine.close()


# ---- the screen ----
async def test_the_screen_has_the_words_the_button_and_the_other_devices_line():
    import decision_ui
    src = open(decision_ui.__file__, encoding="utf-8").read()
    for s in ("The engine software is missing.", "Needs the engine.", "Get the engine</button>", "needs the engine", "runnerOnly: true", "dcGetRunner"):
        assert s in src, s
    assert "Install the decision model again" not in src
    run = src.split("function dcRowBits(dev) {", 1)[1].split("function dcRow(dev)", 1)[0]
    assert run.index("dcGetRunner") < run.index("start\\'"), "Get the engine takes Start's place"      # (the Shut down and Restart buttons come earlier in the row, so look for the Start button itself)
    assert "Uninstall" in src.split("function dcMenuHtml", 1)[1].split("}", 1)[0] or "dcAskUninstall" in src, "the menu keeps Uninstall; it is just not the way out"


# ---- on another device ----
async def test_the_other_device_gets_only_its_runner_either_way(rig):  # noqa: F811
    await installed_rig(rig)
    state = {"exe": None, "n": 0}
    real_exe = Path(rig.tmp) / "c" / "llama-server.exe"
    rig.c_engine._exe_fn = lambda: state["exe"]

    async def fetch(progress):
        state["n"] += 1
        state["exe"] = real_exe
        return real_exe
    rig.c_engine._ensure_engine = fetch
    await rig.c_engine.stop()
    before = model_fingerprint(rig.c_engine)
    assert rig.c_engine.status()["runnerMissing"] is True
    for source in ("that-pc", "gaming-pc"):
        state["exe"] = None
        r = await rig.client.post("/config/decisions/install", json={"pc": PC, "source": source, "runnerOnly": True})
        assert r.status_code == 200, r.text
        job = rig.handles.remote.jobs[PC]
        assert job.runner_only is True
        assert await until(lambda: job.state != "running", 30)
        assert job.state == "done", job.error
        assert rig.c_engine.job.runner_only is True and model_fingerprint(rig.c_engine) == before
        assert rig.c_engine.status()["runnerMissing"] is False
    assert state["n"] == 2
    assert await until(lambda: ((rig.remote.report or {}).get("decisions") or {}).get("runnerMissing") is False, 10)
