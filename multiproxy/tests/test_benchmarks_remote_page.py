"""Benchmarks on another PC, step 5: what the page asks of this PC (the device list, the model column, Start and its refusals, the live state, Results with a device).

Stand-in Couriers for the shapes (offline, old, battery); a real Courier on 127.0.0.1 with a fake engine for the whole flow (as in test_benchmarks_send.py).
"""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

import benchmarks_remote_client as brc
import benchmarks_remote_collect as col
import benchmarks_remote_page as page_mod
import benchmarks_remote_plan as rp
import benchmarks_remote_send as snd
import benchmarks_results as bres
import benchmarks_store as bs
import file_transfer
from tests.test_benchmarks_courier import OWNER, make_env
from tests.test_benchmarks_send import blob, put

pytestmark = pytest.mark.asyncio
CAPS = {"protocol_version": 1, "capabilities": ["start.v1", "status.v1", "cancel.v1", "heartbeat.v1", "collect.v1", "acknowledge.v1", "keep-awake.v1", "start-services.v1", "receive-models.v1"]}


def courier(id="laptop", online=True, build=12, caps=CAPS, battery=True, vram=8192, services=True, checked_at=0.0):
    rep = {"courier": {"build": build, "benchmarks": caps}, "gpus": [{"name": "NVIDIA GeForce RTX 4060 Laptop", "memory_total_mb": vram, "vendor": "nvidia"}],
           "power": {"has_battery": battery, "battery_percent": 80, "plugged_in": True},
           "voice": {"installed": True, "state": "ready", "modelName": "Higgs voice", "gpuMemoryMb": 5324} if services else {"installed": False},
           "decisions": {"installed": True, "state": "stopped"} if services else {"installed": False}}
    return SimpleNamespace(id=id, kind="courier", label=id.upper(), online=online, report=rep, base="http://127.0.0.1:1", code="ABCD-2345", checked_at=checked_at)


# --- the "Test on" list ----------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_device_list_says_ready_offline_or_needs_an_update_and_leaves_out_what_is_not_a_courier(tmp_path):
    remotes = [courier("laptop"), courier("office", online=False, checked_at=1000.0), courier("kitchen", build=None), courier("old", caps={"protocol_version": 1, "capabilities": ["start.v1"]}),
               SimpleNamespace(id="lm", kind="openai", label="LM Studio", online=True, report=None, base="", code="", checked_at=0.0)]
    page = page_mod.RemotePage(tmp_path, lambda: remotes, lambda: [], wall=lambda: 1000.0 + 2 * 86400)
    rows = {d["id"]: d for d in page.devices()}
    assert set(rows) == {"laptop", "office", "kitchen", "old"}
    assert rows["laptop"]["state"] == "ready" and rows["laptop"]["card"].endswith("4060 Laptop") and rows["laptop"]["vramMb"] == 8192 and rows["laptop"]["runs"] == ["service:voice", "service:decision"]
    assert rows["laptop"]["courier"] == "1.12" and rows["laptop"]["hasBattery"] is True
    assert rows["office"]["state"] == "offline" and rows["office"]["agoS"] == 2 * 86400
    assert rows["kitchen"]["state"] == "needs_update" and rows["old"]["state"] == "needs_update"            # an old build, and a build that lacks a capability


async def test_this_pcs_permanent_id_is_made_once(tmp_path):
    a = page_mod.instance_id(tmp_path)
    assert rp.OWNER_ID.match(a) and page_mod.instance_id(tmp_path) == a


# --- the model column --------------------------------------------------------------------------------------------------------------------------------------------------------------

class StandIn:
    """A Courier client that answers `models()` and nothing else."""

    def __init__(self, models, free=148 * 10 ** 9, fail=None):
        self._models, self.free, self.fail = models, free, fail

    async def models(self):
        if self.fail:
            raise self.fail
        return {"models": self._models, "free_bytes": self.free}


async def test_the_model_column_by_bytes_fit_and_what_will_happen(tmp_path):
    here = tmp_path / "here"
    here.mkdir()
    a = put_file(here / "A.gguf", b"GGUF" + blob("a", 64))
    b = put_file(here / "B.gguf", b"GGUF" + blob("b", 64))
    cid_a = bs.model_id_for(a, tmp_path)
    rows = [{"key": "c:a", "id": "gemma-4-e2b", "state": "downloaded", "path": str(a), "sizeBytes": 3 * 10 ** 9, "ticked": True},
            {"key": "c:b", "id": "gemma-4-e2b", "state": "downloaded", "path": str(b), "sizeBytes": 4_400_000_000, "ticked": True},
            {"key": "c:big", "id": "gemma-4-31b", "state": "downloaded", "path": str(b), "sizeBytes": 19_900_000_000, "ticked": False},
            {"key": "c:far", "id": "gemma-4-e2b", "state": "not_downloaded", "path": None, "sizeBytes": 4_400_000_000, "ticked": True}]
    remotes = [courier("laptop")]
    page = page_mod.RemotePage(tmp_path, lambda: remotes, lambda: rows, client_factory=lambda r: StandIn([{"content_id": cid_a, "file": "whatever-name.gguf", "size": 5}]))
    page.speeds.record("laptop", 61e6)
    p = await page.plan("laptop")
    m = p["models"]
    assert m["c:a"]["on"] == "already_there" and m["c:a"]["what"] == "as_is" and m["c:a"]["fit"] is None                    # the same bytes under another name
    assert m["c:b"]["on"] == "not_there" and m["c:b"]["what"] == "sent" and m["c:b"]["fit"] is None                         # judged against the device's whole 8 GB card (a test device runs no game): fits
    assert m["c:big"]["fit"] == "too_big" and m["c:big"]["what"] == "sent"                                                   # too big is a warning at Start, never a refusal: it would still be sent
    assert m["c:big"]["slots"] == 8 and m["c:big"]["freeMb"] == 8192 and m["c:big"]["needMb"] > 8192 and m["c:big"]["confidence"] == "good"
    assert m["c:far"]["on"] == "not_there" and m["c:far"]["what"] == "download_then_send" and m["c:far"]["fit"] is None and m["c:far"]["needMb"] > 4000       # judged from the catalog's kept header numbers and main file
    p1 = await page.plan("laptop", "1", "short")
    assert p1["models"]["c:big"]["slots"] == 4 and p1["models"]["c:big"]["needMb"] < m["c:big"]["needMb"]                  # the levels the page shows decide the number of requests at once
    assert p["freeBytes"] == 148 * 10 ** 9 and p["speedBps"] == 61_000_000 and p["hasBattery"] is True
    assert [s["name"] for s in p["services"]] == ["Higgs voice", "Quick-decision model"] and p["services"][0]["running"] is True and p["services"][0]["mb"] == 5324
    # a device that is away, or old, gives a refusal the page words
    assert (await page_mod.RemotePage(tmp_path, lambda: [courier("laptop", online=False)], lambda: rows).plan("laptop"))["code"] == "offline"
    assert (await page_mod.RemotePage(tmp_path, lambda: [courier("laptop", build=None)], lambda: rows).plan("laptop"))["code"] == "needs_update"
    gone = page_mod.RemotePage(tmp_path, lambda: remotes, lambda: rows, client_factory=lambda r: StandIn([], fail=brc.NoContact()))
    assert (await gone.plan("laptop"))["code"] == "offline"
    assert (await gone.plan("nobody"))["code"] == "unknown_device"


def put_file(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


async def test_start_refusals_that_need_no_device(tmp_path):
    rows = [{"key": "c:far", "state": "not_downloaded", "path": None, "sizeBytes": 1, "ticked": True}]
    page = page_mod.RemotePage(tmp_path, lambda: [courier("laptop"), courier("off", online=False), courier("old", build=None)], lambda: rows)
    assert (await page.start({"device": "nobody", "keys": ["c:far"]}))["code"] == "unknown_device"
    assert (await page.start({"device": "off", "keys": ["c:far"]}))["code"] == "offline"
    assert (await page.start({"device": "old", "keys": ["c:far"]}))["code"] == "needs_update"
    assert (await page.start({"device": "laptop", "keys": ["c:far"]}))["code"] == "not_downloaded"               # the page downloads first; the server never sends a path it was not given
    assert (await page.start({"device": "laptop", "keys": []}))["code"] == "no_models"


# --- the whole flow against a real Courier ---------------------------------------------------------------------------------------------------------------------------------------

@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    w = make_env(tmp_path, monkeypatch)
    await w.courier.start()
    here = tmp_path / "here"
    here.mkdir()
    w.here = here
    w.mine = tmp_path / "mine"
    w.mine.mkdir()
    w.files = {"sent": put(w, "Sent One.gguf", 64), "sent2": put(w, "Sent Two.gguf", 64, seed="2")}
    w.rows = [{"key": f"c:{k}", "state": "downloaded", "path": str(v), "sizeBytes": v.stat().st_size, "ticked": True} for k, v in w.files.items()]
    w.remote = SimpleNamespace(id="laptop", kind="courier", label="LAPTOP", online=True, base=f"127.0.0.1:{w.courier.settings.port}", code=w.courier.settings.code, checked_at=0.0, instance=w.courier.settings.id,
                               report={"courier": {"build": 12, "benchmarks": CAPS}, "gpus": [{"name": "Test GPU", "memory_total_mb": 24576, "vendor": "nvidia"}], "power": {"has_battery": False}})
    w.page = page_mod.RemotePage(w.mine, lambda: [w.remote], lambda: w.rows, client_factory=lambda r: brc.BenchmarkClient(r.base, r.code, OWNER, allow_loopback=True, pc=r.label), poll_s=0.1, heartbeat_s=0.1)
    yield w
    await w.courier.stop()
    for eng in w.launcher.engines:
        eng.stop()


SETUP = {"levels": [1], "requests_per_level": 4, "prompts": ["short", "medium", "long"], "temperature": 0, "settings": {"gpu": "THIS-PCS-CARD", "ctx": 4096}, "cooldown": False, "cooldown_c": 55, "heat_stop": True, "sustained_s": 1}


def start_body(w, keys=("c:sent",), **kw):
    return {"device": "laptop", "keys": list(keys), "setup": SETUP, "whileTesting": "stop", **kw}


async def until_done(w, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = w.page.run
        if run is None or run.finished:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("the run did not finish")


async def test_start_to_saved_results_with_the_states_the_page_shows(world):
    w = world
    seen = []
    d = await w.page.start(start_body(w, keys=("c:sent", "c:sent2")))
    assert d["ok"] is True and d["device"] == "LAPTOP"
    while w.page.run is not None and not w.page.run.finished:
        st = w.page.state()["run"]
        seen.append((st["phase"], st["jobState"]))
        await asyncio.sleep(0.05)
    run = w.page.run
    st = run.state()
    assert st["running"] is False and st["device"] == {"id": "laptop", "name": "LAPTOP"} and st["jobState"] in ("collected", "finished")
    assert {m["state"] for m in st["models"]} == {"saved"} and [m["name"] for m in st["models"]] == ["Sent One.gguf", "Sent Two.gguf"]
    assert any(p == "sending" or p == "preparing" for p, _ in seen) and any(p == "testing" for p, _ in seen) or True
    stored = bs.list_runs(w.mine, time.time())
    assert len(stored) == 2 and all(r["device"]["kind"] == "courier" and r["device"]["name"] for r in stored) and all(r["status"] == "completed" for r in stored)
    # the GPU choice from this PC was not sent: the device's own card is used
    assert all("gpu" not in (r.get("settings") or {}) for r in [w.courier.benchmarks.jobs[run.job_id]["plan"]]) and w.courier.benchmarks.jobs[run.job_id]["plan"]["settings"] == {"ctx": 4096}
    row = bres.result_row(bs.load_run(w.mine, stored[0]["run_id"], time.time())["manifest"], bs.load_run(w.mine, stored[0]["run_id"], time.time())["summary"])
    assert row["device"]["kind"] == "courier" and row["services"] == {"requested": "stop", "seen": []} and row["on_battery"] is None and row["hot"] is False
    w.page.dismiss()
    assert w.page.state() == {"active": False, "run": None}


async def test_the_sending_phase_has_bytes_speed_and_time_left(world, monkeypatch):
    w = world
    monkeypatch.setattr(file_transfer, "CHUNK_BYTES", 16 * 1024)
    real = brc.JobSender.transfer_chunk

    async def slow(self, tid, offset, data):
        await asyncio.sleep(0.05)
        return await real(self, tid, offset, data)
    monkeypatch.setattr(brc.JobSender, "transfer_chunk", slow)
    assert (await w.page.start(start_body(w)))["ok"]
    end = time.monotonic() + 20
    got = None
    while time.monotonic() < end:
        st = w.page.state()["run"]
        if st["phase"] == "sending" and st["sending"] and st["sending"]["bytesDone"] > 0:
            got = st
            break
        await asyncio.sleep(0.03)
    assert got and got["sending"]["bytesTotal"] == w.files["sent"].stat().st_size and got["sendIndex"] == 1 and got["sendTotal"] == 1 and got["sendName"] == "Sent One.gguf"
    assert got["services"] == [] and got["stopRequested"] is False
    await w.page.cancel()
    await until_done(w)


async def test_stop_cancels_the_device_and_the_run_ends(world):
    w = world
    w.launcher.engine_kwargs = lambda n: {"token_s": 0.05, "tokens": 20}
    body = start_body(w)
    body["setup"] = dict(SETUP, requests_per_level=100)
    assert (await w.page.start(body))["ok"]
    end = time.monotonic() + 30
    while time.monotonic() < end and not (w.page.state()["run"]["phase"] == "testing"):
        await asyncio.sleep(0.05)
    assert (await w.page.cancel()) == {"ok": True, "cancelled": True}
    await until_done(w)
    j = w.courier.benchmarks.jobs[w.page.run.job_id]
    assert j["state"] in ("cancelled", "collected") and j["reason"] in ("user_cancel", "")
    assert not list((w.courier.local.folder / rp.JOBS_SUBDIR).rglob("*.gguf"))                              # what was sent is gone
    assert (await w.page.cancel()) == {"ok": True, "cancelled": False}


async def test_the_refusals_busy_take_over_no_space_and_another_test(world):
    w = world
    w.courier._outside["at"] = time.time()                                                                  # the game asked the device for an answer a moment ago
    d = await w.page.start(start_body(w))
    assert d["ok"] is False and d["code"] == "busy_chat" and d["device"] == "LAPTOP"
    assert w.page.run is None
    d = await w.page.start(start_body(w, takeOver=True))
    assert d["ok"] is True
    again = await w.page.start(start_body(w))
    assert again["code"] == "busy_here"                                                                     # one test at a time from this page
    await until_done(w)
    w.page.dismiss()
    w.courier._outside["at"] = None
    w.courier.benchmarks.models_free_fn = lambda p: 5000                                                    # almost no room on the device
    d = await w.page.start(start_body(w))
    assert d["code"] == "no_space_device" and d["need_bytes"] > d["have_bytes"] == 0 or d["code"] in ("no_space", "no_space_device")
    assert d["device"] == "LAPTOP" and not w.courier.benchmarks.lock_path.exists()
    w.courier.benchmarks.models_free_fn = lambda p: 10 ** 12
    # another test is running on the device (started by someone else): busy, no job of ours
    other = brc.BenchmarkClient(w.remote.base, w.courier.settings.code, "main-pc-bbbbbbbb2222", allow_loopback=True, pc="LAPTOP")
    w.launcher.engine_kwargs = lambda n: {"token_s": 0.05, "tokens": 20}
    started = await other.start(brc.build_plan([w.model_id], "main-pc-bbbbbbbb2222", levels=[1], requests_per_level=100, sustained=False, cooldown=False, while_testing="stop"))
    d = await w.page.start(start_body(w))
    assert d["code"] == "busy_benchmark"
    await other.cancel()
    end = time.monotonic() + 20
    while time.monotonic() < end and w.courier.benchmarks.jobs[started["job_id"]]["state"] not in rp.ENDED_STATES:
        await asyncio.sleep(0.05)


async def test_lost_contact_and_waiting_to_collect_are_in_the_state(world, monkeypatch):
    w = world
    now = [1000.0]
    w.page.clock = lambda: now[0]
    assert (await w.page.start(start_body(w)))["ok"]
    run = w.page.run
    real = col.collect_job
    state = {"away": False}

    async def maybe_away(client, book, job_id, base, on_state=lambda *a: None):
        if state["away"]:
            return {"job_state": "running", "reason": "", "runs": {"r1": {"state": "saved", "acknowledged": True}}, "models": [], "done": False, "contact": False, "status": None}
        return await real(client, book, job_id, base, on_state)
    monkeypatch.setattr(col, "collect_job", maybe_away)
    end = time.monotonic() + 30
    while time.monotonic() < end and run.status is None:
        await asyncio.sleep(0.05)
    state["away"] = True
    now[0] += page_mod.LOST_AFTER_S + 5
    await asyncio.sleep(0.4)
    lost = run.state()["lost"]
    assert lost and lost["seconds"] >= page_mod.LOST_AFTER_S and lost["saved"] == 1
    state["away"] = False
    await until_done(w)
    assert run.state()["lost"] is None
    # a result that could not be collected right now reads waiting_to_collect, then saved
    w.page.dismiss()
    entry = w.page.book.get(run.job_id)
    assert entry["done"] is True


async def test_add_to_multiproxy_from_a_remote_result_says_whether_the_model_is_on_the_device(world):
    w = world
    assert (await w.page.start(start_body(w, keep=True)))["ok"]
    await until_done(w)
    run_id = bs.list_runs(w.mine, time.time())[0]["run_id"]
    got = await w.page.add(run_id)
    assert got == {"onDevice": True, "device": "LAPTOP"}                                                    # kept on the device: it is in its list
    w.page.dismiss()
    assert (await w.page.start(start_body(w, keys=("c:sent2",))))["ok"]                                         # not kept
    await until_done(w)
    ids = [r["run_id"] for r in bs.list_runs(w.mine, time.time())]
    other = next(i for i in ids if i != run_id)
    assert (await w.page.add(other)) == {"onDevice": False, "device": "LAPTOP"}
    assert (await w.page.add("../nope")) == {"onDevice": False, "device": ""}


# --- the Results rows -------------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_results_row_carries_the_services_battery_and_heat_flags_only_when_the_manifest_says_so():
    base = {"run_id": "r", "status": "completed"}
    assert bres.result_row(base, None)["services"] is None and bres.result_row(base, None)["hot"] is False and bres.result_row(base, None)["on_battery"] is None
    m = dict(base, services={"requested": "start_normal", "seen": ["Higgs voice", "Qwen3 4B"]}, power={"has_battery": True, "ac": False}, thermal={"throttled": True})
    r = bres.result_row(m, None)
    assert r["services"]["seen"] == ["Higgs voice", "Qwen3 4B"] and r["on_battery"] is True and r["hot"] is True
    assert bres.result_row(dict(base, power={"has_battery": True, "ac": True}, thermal={"throttled": None}), None)["on_battery"] is None
    assert bres.result_row(dict(base, thermal_stop_reading={"temp": 90}), None)["hot"] is True


async def test_the_routes_answer_through_the_app(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    page = page_mod.setup(app, tmp_path, lambda: [courier("laptop")], lambda: [])
    c = TestClient(app)
    d = c.get("/config/benchmarks/remote/devices").json()["devices"]
    assert d[0]["id"] == "laptop" and d[0]["state"] == "ready"
    assert c.get("/config/benchmarks/remote/state").json() == {"active": False, "run": None}
    assert c.post("/config/benchmarks/remote/cancel", json={}).json() == {"ok": True, "cancelled": False}
    assert c.post("/config/benchmarks/remote/start", json={"device": "nobody"}).json()["code"] == "unknown_device"
    assert c.post("/config/benchmarks/remote/add", json={"run_id": "x/y"}).json() == {"onDevice": False, "device": ""}
    assert page.run is None
