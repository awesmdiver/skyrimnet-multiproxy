"""The local decision model on another device through the Courier (Local decisions, step 3): a real Courier on a spare port with a FAKE llama-server behind its decision
manager, and a real gaming-device side (MultiProxy's decision routes, the pairing, the shared file transfer) in front of it. Install with progress, the pinned files only,
where it runs decided on that device, the relay, the waiting limit counted from receipt, the unreachable device, moving between devices, the disk checks on both ends."""
import asyncio
import dataclasses
import json
import logging
import os
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import courier as cr
import courier_client
import courier_system as cs
import decision_courier
import decision_engine as de
import decision_wiring
import file_transfer
import other_pcs as op
from tests.decision_helpers import fake_launcher, hardware, make_files
from tests.fixtures.courier_fakes import free_port
from tests.test_courier import GPUS, FakeAwake, FakeRegistry
from tests.test_decision_engine import CHOICE, NOUL, SCORE, STATE
from voice_downloads import DownloadStore, FolderSource

log = logging.getLogger("test-decision-courier")
pytestmark = pytest.mark.asyncio
PC = "laptop"
HOST = os.environ.get("DECISION_RIG_HOST", "127.0.0.1")        # (the measuring script reaches the Courier at this device's home-network address instead)
QUESTIONS = {"combat": NOUL, "mood": CHOICE, "urgency": SCORE}


class Rig:
    pass


@pytest_asyncio.fixture
async def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback=False: None)       # the other device is this device in these tests
    rg = Rig()
    rg.tmp = tmp_path
    src = tmp_path / "source"
    models = make_files(src)
    monkeypatch.setattr(de, "source_for", lambda spec: FolderSource(src))        # "download it on that device" reads the same fake files
    # ---- the Courier device: a real Courier, with a fake llama-server behind its decision manager ----
    (tmp_path / "courier").mkdir()
    settings = cr.CourierSettings(tmp_path / "courier" / "courier.json")
    settings.set(port=free_port())
    courier = cr.Courier(settings, log, bind_ips=[HOST], discovery_port=free_port(), keep_awake=cs.KeepAwake(FakeAwake()),
                         startup=cs.StartWithWindows("cmd", registry=FakeRegistry()), gpu_reader=lambda: GPUS, adapters=[])
    rg.c_hw = {"hw": hardware(free_vram_gb=20.0)}
    exe = tmp_path / "c" / "llama-server.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"x")
    holder: dict = {}

    async def ensure(_p):
        return exe
    c_engine = de.DecisionEngine(lambda: tmp_path / "c" / "decisions", DownloadStore(lambda: tmp_path / "c" / "decisions" / "downloads"),
                                 lambda: {"model": "kev-4b", "run_on": "automatic", "limit_seconds": 0, **(settings.data.get("decisions") or {})},
                                 lambda: exe, ensure, lambda: "cuda-13.4", models=models, hardware_fn=lambda f: rg.c_hw["hw"], launcher=fake_launcher,
                                 port_range=(29200, 29249), slots=4, source_fn=lambda spec: holder["cd"]._source(spec))
    holder["cd"] = courier.decisions = decision_courier.CourierDecisions(lambda: tmp_path / "c" / "decisions", settings, log, None, changed=courier.signal_change, engine=c_engine)
    await courier.start()
    # ---- the main device: MultiProxy's decision routes, with a fake engine of its own and the same pinned files ----
    g_store = DownloadStore(lambda: tmp_path / "g" / "downloads")
    g_exe = tmp_path / "g" / "llama-server.exe"
    g_exe.parent.mkdir()
    g_exe.write_bytes(b"x")

    async def g_ensure(_p):
        return g_exe
    cfg: dict = {}
    g_engine = de.DecisionEngine(lambda: tmp_path / "g" / "decisions", g_store, lambda: decision_wiring_settings(cfg), lambda: g_exe,
                                 g_ensure, lambda: "cuda-13.4", models=models, hardware_fn=lambda f: hardware(), launcher=fake_launcher, port_range=(29300, 29349), slots=4,
                                 source_fn=lambda spec: FolderSource(src))
    remotes = op.RemoteServers(lambda: cfg.setdefault("_remotes", {}), lambda d: cfg.__setitem__("_remotes", d), log, lambda n: None, lambda: set(),
                               client_factory=courier_client.CourierClient)
    app = FastAPI()
    rg.handles = decision_wiring.setup(app, lambda: json.loads(json.dumps(cfg)), lambda d: (cfg.clear(), cfg.update(d)), lambda: str(tmp_path / "g"), lambda s: s, log, None,
                                       g_store, engine=g_engine, remotes=remotes, this_pc=lambda: "gaming-pc", source_factory=lambda spec: FolderSource(src),
                                       has_typesafe_key=lambda: rg.typesafe)
    rg.typesafe = False
    rg.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", timeout=60)
    rg.courier, rg.c_engine, rg.g_engine, rg.remotes, rg.src, rg.cfg, rg.models, rg.settings = courier, c_engine, g_engine, remotes, src, cfg, models, settings
    r = await remotes.add_multiproxy(f"{HOST}:{settings.port}", settings.code, PC)
    for _ in range(100):
        if r.report:
            break
        await asyncio.sleep(0.05)
    rg.remote = r
    yield rg
    await rg.client.aclose()
    await remotes.stop_polling()
    await g_engine.close()
    await courier.stop()


def decision_wiring_settings(cfg: dict) -> dict:
    raw = cfg.get("decisions") or {}
    return {**decision_wiring.DEFAULT_SETTINGS, **raw}


async def until(cond, secs=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def install(rig, source="gaming-pc"):
    r = await rig.client.post("/config/decisions/install", json={"pc": PC, "source": source})
    assert r.status_code == 200, r.text
    job = rig.handles.remote.jobs[PC]
    seen = []
    for _ in range(600):
        seen.append(job.to_json())
        if job.state != "running":
            return job.to_json(), seen
        await asyncio.sleep(0.05)
    raise AssertionError("the install never finished")


async def installed_rig(rig):
    job, _ = await install(rig)
    assert job["state"] == "done", job
    assert await until(lambda: rig.c_engine.state == "ready")
    assert await until(lambda: ((rig.remote.report or {}).get("decisions") or {}).get("state") == "ready", 10), "the main device has been told it is ready"


async def move_to(rig, pc):
    r = await rig.client.post("/config/decisions/location", json={"pc": pc})
    assert r.status_code == 200, r.text
    return r.json()


# ---- the Courier reports the decision model, and takes only the pinned files ---------------------------------------------------------------------------------
class TestCourierSide:
    async def test_the_courier_report_carries_a_decisions_block(self, rig):
        rep = await rig.courier.report(fresh=True)
        d = rep["decisions"]
        assert d["state"] == "not_installed" and d["installed"] is False and d["settings"] == {"model": "kev-4b", "run_on": "automatic", "limit_seconds": 0.0}
        assert d["receiving"] == {}

    async def test_the_fit_says_what_it_needs_and_what_is_already_there(self, rig):
        info = await rig.remote.client.decisions_get("fit")
        assert info["fit"]["verdict"] in ("ok", "warn") and info["installed"] is False and info["engineKind"] is None
        assert [f["role"] for f in info["files"]] == ["model"] and not any(f["have"] for f in info["files"])
        assert info["freeDiskBytes"] > 0

    async def test_only_the_pinned_files_are_taken_with_the_right_size_and_checksum(self, rig):
        spec = rig.models["kev-4b"].model
        c = rig.remote.client
        for bad in ({"file": "evil.gguf", "size": 10, "sha256": "a" * 64}, {"file": spec.name, "size": spec.size + 1, "sha256": spec.sha256},
                    {"file": spec.name, "size": spec.size, "sha256": "b" * 64}, {"file": "../escape.gguf", "size": 10, "sha256": "a" * 64}):
            with pytest.raises(file_transfer.TransferError) as e:
                await c.transfer_begin({"kind": "decision", "model": "decision-model", **bad})
            assert e.value.status == 400
        info = await c.transfer_begin({"kind": "decision", "model": "decision-model", "file": spec.name, "size": spec.size, "sha256": spec.sha256})
        assert info["state"] != "done"
        await c.transfer_cancel(info["id"])

    async def test_the_progress_comes_through_the_couriers_live_report(self, rig):
        spec = rig.models["kev-4b"].model
        info = await rig.remote.client.transfer_begin({"kind": "decision", "model": "decision-model", "file": spec.name, "size": spec.size, "sha256": spec.sha256})
        rep = await rig.courier.report(fresh=True)
        assert rep["decisions"]["receiving"]["state"] == "running"
        assert rep["programs"] == [] or all(p["downloads"] == [] for p in rep["programs"]), "not shown as a model download too"
        await rig.remote.client.transfer_cancel(info["id"])

    async def test_settings_are_checked_and_kept_on_that_pc(self, rig):
        c = rig.remote.client
        st = await c.decisions_post("settings", {"model": "laya", "runOn": "cpu", "limitSeconds": 2.5})
        assert st["settings"] == {"model": "laya", "run_on": "cpu", "limit_seconds": 2.5}
        assert json.loads(rig.settings.path.read_text(encoding="utf-8"))["decisions"]["run_on"] == "cpu"
        for bad in ({"runOn": "tpu"}, {"model": "9b"}, {"limitSeconds": 0.05}, {"limitSeconds": "x"}):
            with pytest.raises(Exception) as e:
                await c.decisions_post("settings", bad)
            assert getattr(e.value, "status", 400) == 400

    async def test_the_signature_changes_when_the_decision_model_does_but_not_on_every_decision(self, rig):
        a = cr.Courier.signature(await rig.courier.report(fresh=True))
        rig.c_engine.answered += 5
        rig.c_engine.last_ms = 40
        assert cr.Courier.signature(await rig.courier.report(fresh=True)) == a
        await installed_rig(rig)
        assert cr.Courier.signature(await rig.courier.report(fresh=True)) != a


# ---- install ------------------------------------------------------------------------------------------------------------------------------------------------
class TestInstall:
    async def test_install_gets_the_files_over_from_the_gaming_pc_with_real_progress_and_starts_it(self, rig):
        job, seen = await install(rig)
        assert job["state"] == "done", job
        trail = rig.handles.remote.jobs[PC].trail
        assert trail[0][0] == "preparing" and "copying" in [t[0] for t in trail] and trail[-1][0] == "installing", "fetched here once, copied, then set up there"
        msgs = " | ".join(t[2] for t in trail)
        assert "Downloading the decision model's files on this device (once)" in msgs and "Copying the decision model from this device to laptop" in msgs
        assert "extra model files" not in msgs and "add-on" not in msgs
        assert any(j["stage"] == "copying" and 0 < j["percent"] < 100 or j["percent"] == 100 for j in seen)
        assert await until(lambda: rig.c_engine.state == "ready")
        assert rig.c_engine.installed()
        for f in rig.models["kev-4b"].files():
            assert rig.c_engine.store.has(f), f.name
            assert rig.g_engine.store.has(f), "kept on the main device too (download once)"
        rep = await rig.courier.report(fresh=True)
        assert rep["decisions"]["ready"] is True and rep["decisions"]["installed"] is True
        assert rig.handles.settings()["location"] == PC, "it now runs there"

    async def test_a_second_install_copies_nothing_that_is_already_there(self, rig):
        await install(rig)
        await rig.c_engine.stop()
        job, _ = await install(rig)
        assert job["state"] == "done" and "copying" not in [t[0] for t in rig.handles.remote.jobs[PC].trail]

    async def test_the_progress_numbers_are_real_bytes_that_only_go_up(self, rig, monkeypatch):
        orig = file_transfer.send_file

        async def slow(*a, **k):                      # (each file takes a moment, so the 50 ms look sees the copying stage even on a fast machine)
            await asyncio.sleep(0.15)
            return await orig(*a, **k)
        monkeypatch.setattr(file_transfer, "send_file", slow)
        job, seen = await install(rig)
        copying = [j for j in seen if j["stage"] == "copying"]
        total = sum(f.size for f in rig.models["kev-4b"].files())
        assert copying and all(j["total"] == total for j in copying)
        done = [j["done"] for j in copying]
        assert done == sorted(done) and done[-1] <= total

    async def test_download_it_on_that_pc_is_the_other_choice(self, rig):
        job, seen = await install(rig, "that-pc")
        assert job["state"] == "done" and not any(j["stage"] == "copying" for j in seen)
        assert rig.c_engine.installed()

    async def test_a_pc_told_to_use_what_was_copied_when_nothing_was_says_so(self, rig):
        with pytest.raises(Exception) as e:
            await rig.remote.client.decisions_post("install", {"source": "kept"})
            for _ in range(100):
                st = await rig.remote.client.decisions_get("status")
                if (st.get("install") or {}).get("state") == "error":
                    raise RuntimeError(st["install"]["error"])
                await asyncio.sleep(0.05)
        assert "hasn't been copied to this device yet" in str(e.value) or "couldn't be downloaded" in str(e.value)

    async def test_a_pc_with_no_room_is_refused_before_anything_moves(self, rig):
        rig.c_hw["hw"] = hardware(free_disk_gb=0.1)
        job, seen = await install(rig)
        assert job["state"] == "error" and "room on laptop's drive" in job["error"] and not any(j["stage"] == "copying" for j in seen)
        assert not any(rig.c_engine.store.has(f) for f in rig.models["kev-4b"].files())

    async def test_this_pc_short_of_room_says_so_and_offers_the_other_way(self, rig, monkeypatch):
        monkeypatch.setattr("decision_remote.DecisionRemote._gaming_free", lambda self: 1024)
        job, _ = await install(rig)
        assert job["state"] == "error" and "doesn't have room for the decision model's files" in job["error"] and "download them itself instead" in job["error"]

    async def test_a_courier_with_different_pinned_files_asks_for_an_update(self, rig):
        spec = rig.g_engine.models["kev-4b"]
        rig.g_engine.models = {**rig.g_engine.models, "kev-4b": dataclasses.replace(spec, model=dataclasses.replace(spec.model, sha256="9" * 64))}
        job, _ = await install(rig)
        assert job["state"] == "error" and "different version" in job["error"]

    async def test_cancel_stops_it(self, rig, monkeypatch):
        orig = file_transfer.send_file

        async def slow(*a, **k):
            await asyncio.sleep(2)
            return await orig(*a, **k)
        monkeypatch.setattr(file_transfer, "send_file", slow)
        await rig.client.post("/config/decisions/install", json={"pc": PC})
        await asyncio.sleep(0.3)
        assert (await rig.client.post("/config/decisions/cancel", json={"pc": PC})).json() == {"cancelled": True}
        job = rig.handles.remote.jobs[PC]
        assert await until(lambda: job.state != "running", 10) and job.state == "cancelled"

    async def test_a_pc_with_an_older_courier_is_said_plainly(self, rig):
        rig.remote.report = {k: v for k, v in rig.remote.report.items() if k != "decisions"}
        d = (await rig.client.get("/config/decisions/status")).json()
        assert [p["hasDecisions"] for p in d["pcs"]] == [False]
        rig.handles.remote_engine._pc_fn = lambda: PC
        assert "older MultiProxy Courier" in rig.handles.remote_engine.not_ready_message()

    async def test_the_courier_picks_up_the_engine_archives_the_gaming_pc_sent(self, tmp_path):
        """A Courier with no llama.cpp engine: the archives that came over are put where the engine download looks first, so it finds them and fetches nothing."""
        import local_models as lm

        class Job:
            state, done, total, error = "done", 0, 0, ""

        class FakeLocal:
            folder = tmp_path / "models"
            asked = None

            def engine_exe(self):
                return self.exe if getattr(self, "exe", None) and self.exe.exists() else None

            engine_exe_current = engine_exe

            def _cfg(self):
                return {}

            async def default_engine_kind(self):
                return "cpu"

            def download_engine(self, kind):
                dl = self.folder / "engine" / "_downloads"
                self.asked = sorted(p.name for p in dl.iterdir())
                self.exe = self.folder / "llama-server.exe"
                self.exe.write_bytes(b"x")
                return Job()

        fl = FakeLocal()
        fl.folder.mkdir()
        s = cr.CourierSettings(tmp_path / "c2.json")
        cd = decision_courier.CourierDecisions(lambda: tmp_path / "d", s, log, fl)
        specs = decision_courier.engine_specs("cpu")
        assert specs, "this operating system has a no-graphics-card engine"
        cd.mode = "kept"
        with pytest.raises(de.DecisionError) as e:
            await cd._ensure_engine(lambda a, b: None)
        assert "hasn't been copied to this device yet" in e.value.message
        for sp in specs:
            cd.store.path_of(sp.name).parent.mkdir(parents=True, exist_ok=True)
            with open(cd.store.path_of(sp.name), "wb") as f:
                f.truncate(sp.size)                                        # (a file of the right size, not the real archive)
            cd.store.adopt(sp)
        exe = await cd._ensure_engine(lambda a, b: None)
        assert exe.name == "llama-server.exe" and fl.asked == sorted(sp.name for sp in specs)

    async def test_the_engine_archives_are_in_the_pinned_list_for_every_operating_system(self, rig):
        names = set(decision_courier.every_engine_spec())
        assert any("win-cpu" in n for n in names) and any("ubuntu-x64" in n for n in names) and any("arm64" in n for n in names)
        # a Linux Courier may be sent its own build by a Windows main device: the receiving side accepts exactly the pinned ones
        name = next(n for n in names if "ubuntu-x64" in n and "cuda" not in n and "vulkan" not in n and "rocm" not in n)
        sp = decision_courier.every_engine_spec()[name]
        assert rig.courier.decisions.resolve_decision_dest({"file": name, "size": sp.size, "sha256": sp.sha256}).name == name


# ---- where the decision model runs is decided on that device --------------------------------------------------------------------------------------------------
class TestRunsWhereThatPcDecides:
    async def test_a_full_card_on_that_pc_means_system_memory_there_whatever_this_pc_has(self, rig):
        rig.c_hw["hw"] = hardware(free_vram_gb=1.0)                         # that device's card is nearly full; this device has 20 GB free
        await installed_rig(rig)
        d = rig.remote.report["decisions"]
        assert d["runOn"] == "cpu" and d["runOnReason"] == "Running on system memory (the graphics card is too full)."
        st = (await rig.client.get("/config/decisions/status")).json()
        assert st["location"] == PC and st["runOn"] == "cpu" and st["pc"] == PC

    async def test_a_roomy_card_runs_on_the_card_and_the_fit_says_so_before_installing(self, rig):
        r = (await rig.client.get(f"/config/decisions/fit?pc={PC}")).json()
        assert r["runOn"] == "gpu" and "free" in r["runOnReason"]
        await installed_rig(rig)
        assert rig.remote.report["decisions"]["runOn"] == "gpu"

    async def test_the_choice_made_here_is_passed_along_and_system_memory_is_honoured_there(self, rig):
        await rig.client.post("/config/decisions/settings", json={"runOn": "cpu", "model": "kev-4b"})
        await installed_rig(rig)
        assert rig.remote.report["decisions"]["runOn"] == "cpu" and rig.remote.report["decisions"]["runOnReason"] == "You chose system memory."
        assert rig.remote.report["decisions"]["limitSeconds"] == de.CPU_LIMIT_SECONDS

    async def test_the_fit_names_that_pc_not_this_one(self, rig):
        rig.c_hw["hw"] = hardware(free_disk_gb=0.1)
        r = await rig.client.get(f"/config/decisions/fit?pc={PC}")
        assert r.status_code == 200 and r.json()["verdict"] == "refuse" and "laptop's drive" in " ".join(r.json()["reasons"]) and "that drive" not in " ".join(r.json()["reasons"])


# ---- the relay ------------------------------------------------------------------------------------------------------------------------------------------
class TestRelay:
    async def test_a_decision_goes_to_the_other_pc_and_the_answers_come_back_the_same_shape(self, rig):
        await installed_rig(rig)
        res = await rig.handles.try_local(STATE, QUESTIONS, "r1", received=time.time())
        assert res is not None and set(res["answers"]) == {"combat", "mood", "urgency"} and res["usage"]["output_tokens"] == 0
        local = await rig.c_engine.answer(STATE, QUESTIONS)
        assert res["answers"] == local["answers"], "the same answers the engine on that device gives itself"
        assert rig.c_engine.answered >= 2 and rig.g_engine.answered == 0, "this device's own model took no part"

    async def test_the_relay_reports_what_it_adds(self, rig):
        await installed_rig(rig)
        await rig.handles.try_local(STATE, QUESTIONS, "r1", received=time.time())
        ms = rig.handles.remote_engine.last_relay_ms
        assert ms is not None and 0 <= ms < 500
        st = (await rig.client.get("/config/decisions/status")).json()
        assert st["relayMs"] == ms and st["lastAnswerMs"] > 0

    async def test_the_move_changes_where_decisions_go_and_back(self, rig):
        await installed_rig(rig)
        assert (await move_to(rig, "this"))["location"] == "this"
        assert rig.handles.active() is rig.g_engine
        d = await move_to(rig, PC)
        assert d["location"] == PC and rig.handles.active() is rig.handles.remote_engine and d["pcs"][0]["decisions"]["installed"] is True

    async def test_an_unknown_pc_cannot_be_chosen(self, rig):
        r = await rig.client.post("/config/decisions/location", json={"pc": "nowhere"})
        assert r.status_code == 404

    async def test_it_answers_in_the_order_the_player_set_with_the_other_pc_in_it(self, rig):
        await installed_rig(rig)
        assert rig.handles.order(None, False) == ["local", "standin"] and rig.handles.order(None, True) == ["local", "typesafe", "standin"]
        assert rig.handles.order("local/decisions", True) == ["local"]
        assert (await rig.client.get("/config/decisions/status")).json()["answerOrder"] == ["local", "standin"]

    async def test_the_model_not_installed_there_leaves_the_order_as_before(self, rig):
        await move_to(rig, PC)
        assert rig.handles.order(None, True) is None

    async def test_a_question_the_engine_refuses_is_passed_on_across_the_relay(self, rig, monkeypatch):
        monkeypatch.setenv("FAKE_REFUSE", "big")                        # the fake engine behind the Courier answers question "big" with HTTP 400, as llama.cpp does for an invalid one
        await installed_rig(rig)
        many = {"type": "choice", "instructions": "who", "criteria": {"a": None, "b": None}}
        res = await rig.handles.try_local(STATE, {"big": many}, "r2", received=time.time())
        assert res is None and rig.handles.last_reason == "other"
        with pytest.raises(de.Unanswerable):
            await rig.handles.remote_engine.answer(STATE, {"big": many}, received=time.time())

    async def test_start_stop_and_the_log_go_to_that_pc(self, rig):
        await installed_rig(rig)
        st = (await rig.client.post("/config/decisions/stop", json={"pc": PC})).json()
        assert await until(lambda: rig.c_engine.state == "stopped", 10)
        assert (await rig.client.post("/config/decisions/start", json={"pc": PC})).status_code == 200
        assert await until(lambda: rig.c_engine.state == "ready", 20)
        log_text = (await rig.client.get(f"/config/decisions/log?pc={PC}")).json()["log"]
        assert "Ready after" in log_text

    async def test_uninstalling_there_brings_it_back_here(self, rig):
        await installed_rig(rig)
        r = await rig.client.post("/config/decisions/uninstall", json={"pc": PC})
        assert r.status_code == 200 and not rig.c_engine.installed() and rig.handles.settings()["location"] == "this"


# ---- the waiting limit, counted from receipt -------------------------------------------------------------------------------------------------------
class TestLimit:
    async def test_the_wait_is_counted_from_when_multiproxy_received_the_decision(self, rig, monkeypatch):
        monkeypatch.setenv("FAKE_DELAY", "0.4")
        await installed_rig(rig)
        assert await rig.handles.try_local(STATE, {"q": NOUL}, "fresh", received=time.time()) is not None, "0.4 s is inside 0.8 s"
        before = rig.c_engine.answered
        t = time.time()
        res = await rig.handles.try_local(STATE, {"q": NOUL}, "late", received=time.time() - 0.6)      # 0.6 s already spent: 0.2 s left
        assert res is None and rig.handles.last_reason == "too_slow" and time.time() - t < 0.6
        assert rig.handles.remote_engine.limit_hits >= 1
        await asyncio.sleep(0.5)
        assert rig.c_engine.answered == before, "what was sent for the late one was abandoned, never counted as an answer"

    async def test_a_decision_whose_time_is_gone_never_reaches_that_pc(self, rig):
        await installed_rig(rig)
        sent = rig.c_engine.answered
        assert await rig.handles.try_local(STATE, {"q": NOUL}, "stale", received=time.time() - 30) is None
        assert rig.handles.last_reason == "too_slow" and rig.c_engine.answered == sent

    async def test_a_slow_answer_is_given_up_at_the_limit_and_the_next_answerer_takes_it(self, rig, monkeypatch):
        monkeypatch.setenv("FAKE_DELAY", "3")
        await installed_rig(rig)
        t = time.time()
        assert await rig.handles.try_local(STATE, {"q": NOUL}, "slow", received=time.time()) is None
        assert rig.handles.last_reason == "too_slow" and time.time() - t < 1.6

    async def test_the_players_waiting_limit_is_the_one_used_across_the_relay(self, rig, monkeypatch):
        monkeypatch.setenv("FAKE_DELAY", "1.2")
        await installed_rig(rig)
        assert await rig.handles.try_local(STATE, {"q": NOUL}, "a", received=time.time()) is None
        r = await rig.client.post("/config/decisions/settings", json={"limitSeconds": 3})
        assert r.status_code == 200
        assert await until(lambda: rig.remote.report["decisions"]["limitSeconds"] == 3, 10)
        assert await rig.handles.try_local(STATE, {"q": NOUL}, "b", received=time.time()) is not None


# ---- the unreachable device ------------------------------------------------------------------------------------------------------------------------------------
class TestUnreachable:
    async def test_a_stopped_courier_gives_an_immediate_clean_error_naming_the_pc_and_the_next_answerer_takes_over(self, rig, caplog):
        await installed_rig(rig)
        await rig.courier.stop()
        t = time.time()
        with caplog.at_level(logging.INFO):
            res = await rig.handles.try_local(STATE, QUESTIONS, "gone", received=time.time())
        assert res is None and time.time() - t < 1.0 and rig.handles.last_reason == "not_running"
        assert any(PC in r.getMessage() and "so the next answerer takes the decision" in r.getMessage() for r in caplog.records)
        await until(lambda: rig.remote.online is False, 10)
        t = time.time()
        assert await rig.handles.try_local(STATE, QUESTIONS, "gone2", received=time.time()) is None and time.time() - t < 0.2
        st = (await rig.client.get("/config/decisions/status")).json()
        assert st["state"] == "unreachable" and st["pc"] == PC and PC in st["detail"] and "isn't answering" in st["detail"] and st["ready"] is False

    async def test_a_pc_that_is_there_but_has_no_model_says_so(self, rig):
        await move_to(rig, PC)
        with pytest.raises(de.DecisionNotReady) as e:
            await rig.handles.remote_engine.answer(STATE, QUESTIONS, received=time.time())
        assert f"The local decision model isn't installed on {PC}." == e.value.message

    async def test_a_stopped_model_on_that_pc_says_to_start_it(self, rig):
        await installed_rig(rig)
        await rig.client.post("/config/decisions/stop", json={"pc": PC})
        await move_to(rig, PC)
        await until(lambda: rig.remote.report["decisions"]["state"] != "ready", 10)
        with pytest.raises(de.DecisionNotReady) as e:
            await rig.handles.remote_engine.answer(STATE, QUESTIONS, received=time.time())
        assert e.value.message == f"The local decision model isn't running on {PC}."

    async def test_a_pc_removed_from_the_list_while_chosen_is_said_plainly(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        rig.remotes.remove(PC)
        with pytest.raises(de.DecisionNotReady) as e:
            await rig.handles.remote_engine.answer(STATE, QUESTIONS, received=time.time())
        assert "isn't in your list of devices anymore" in e.value.message
        assert rig.handles.order(None, False) is None or isinstance(rig.handles.order(None, False), list)

    async def test_a_model_that_died_on_that_pc_fails_at_once(self, rig):
        await installed_rig(rig)
        rig.c_engine.proc.kill()
        rig.c_engine.proc.wait(10)                    # (killed means gone: on Windows the kill is asked, and for a moment poll() still says running, which made this flaky under load)
        t = time.time()
        assert await rig.handles.try_local(STATE, QUESTIONS, "died", received=time.time()) is None
        assert time.time() - t < 1.0 and rig.handles.last_reason == "not_running"


# ---- the card and the Other devices row ------------------------------------------------------------------------------------------------------------------
class TestCardData:
    async def test_the_status_lists_the_pcs_and_where_it_runs(self, rig):
        d = (await rig.client.get("/config/decisions/status")).json()
        assert d["location"] == "this" and d["thisPc"]["name"] == "gaming-pc"
        assert [p["id"] for p in d["pcs"]] == [PC] and d["pcs"][0]["online"] is True and d["pcs"][0]["hasDecisions"] is True
        assert d["pcs"][0]["decisions"]["installed"] is False and d["pcs"][0]["job"] is None
        await installed_rig(rig)
        d = (await rig.client.get("/config/decisions/status")).json()
        assert d["location"] == PC and d["pcs"][0]["decisions"]["installed"] is True and d["pcs"][0]["job"]["state"] == "done"
        assert d["thisEngine"]["installed"] is False, "this device's own model isn't installed"

    async def test_a_running_install_shows_through_the_status_with_the_steps(self, rig, monkeypatch):
        orig = file_transfer.send_file

        async def slow(*a, **k):
            await asyncio.sleep(0.6)
            return await orig(*a, **k)
        monkeypatch.setattr(file_transfer, "send_file", slow)
        await rig.client.post("/config/decisions/install", json={"pc": PC})
        await asyncio.sleep(0.3)
        d = (await rig.client.get("/config/decisions/status")).json()
        job = d["pcs"][0]["job"]
        assert job["state"] == "running" and job["stage"] in ("preparing", "copying") and job["step"] in ("engine", "model") and job["message"]
        await until(lambda: rig.handles.remote.jobs[PC].state != "running", 20)

    async def test_the_card_and_the_row_know_about_other_pcs(self):
        import decision_ui
        js = decision_ui.DECISION_JS
        for text in ("Set it up", "Installed, not in use", "Use it", "function dcPcRow(r)", "Needs a newer MultiProxy Courier", "Offline", "Open Decisions"):
            assert text in js or text.replace("\\'", "'") in js, text
        import other_pcs_ui
        assert "dcPcRow" in other_pcs_ui.OTHER_PCS_JS if hasattr(other_pcs_ui, "OTHER_PCS_JS") else "dcPcRow" in Path(other_pcs_ui.__file__).read_text(encoding="utf-8")


# ---- moving decisions away stops the copy they came from (2026-10-26) ----------------------------------------------------------------------------------------
class TestMovingStopsTheOldCopy:
    async def _both_installed(self, rig):
        r = await rig.client.post("/config/decisions/install", json={"pc": "this"})
        assert r.status_code == 200, r.text
        assert await until(lambda: rig.g_engine.state == "ready", 20) and rig.handles.settings()["location"] == "this"
        await installed_rig(rig)                        # installing on the other PC moves decisions there when it is ready
        assert await until(lambda: rig.handles.settings()["location"] == PC, 10)

    async def test_moving_to_the_other_pc_stops_the_engine_here_and_moving_back_starts_it_and_stops_the_other(self, rig):
        await self._both_installed(rig)
        assert await until(lambda: rig.g_engine.state == "stopped", 10), "the copy on this device was stopped by itself"
        d = await move_to(rig, "this")
        assert d["moveWarning"] == "" and d["location"] == "this"
        assert await until(lambda: rig.g_engine.state == "ready", 20), "it starts here again"
        assert await until(lambda: rig.c_engine.state == "stopped", 10), "and the other PC's copy stops"
        d = await move_to(rig, PC)
        assert d["moveWarning"] == "" and await until(lambda: rig.g_engine.state == "stopped", 10)

    async def test_a_stop_that_fails_says_so_on_that_row_in_one_line(self, rig, monkeypatch):
        await self._both_installed(rig)
        await move_to(rig, "this")
        assert await until(lambda: rig.g_engine.state == "ready", 20)

        async def broken():
            raise de.DecisionError(500, "would not stop")
        monkeypatch.setattr(rig.g_engine, "stop", broken)
        d = await move_to(rig, PC)
        assert d["moveWarning"] == "Couldn't stop the model on this PC. Please stop it manually." and d["moveWarningPc"] == "this" and d["location"] == PC

    async def test_the_page_puts_that_line_on_the_row_the_decisions_came_from(self):
        import decision_ui
        assert "dcFail[d.moveWarningPc] = d.moveWarning" in decision_ui.DECISION_JS
