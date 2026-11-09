"""Benchmarks on another PC, step 4: this PC sends the models, and the Courier receives them and cleans up.

A real Courier on 127.0.0.1 (scratch folders, fake engine and card, as in test_benchmarks_courier.py), this PC's sender on a scratch benchmark folder of its own, and model files that are a few
kilobytes or megabytes of known bytes.
"""
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
import pytest_asyncio

import benchmarks_courier as bcr
import benchmarks_remote_client as brc
import benchmarks_remote_collect as col
import benchmarks_remote_plan as rp
import benchmarks_remote_send as snd
import benchmarks_store as bs
import file_transfer
from tests.test_benchmarks_courier import OTHER, OWNER, make_env, unit, body_for

pytestmark = pytest.mark.asyncio
OPTS = dict(levels=[1], requests_per_level=4, sustained=False, cooldown=False, while_testing="stop")


def make_world(tmp_path, monkeypatch, lease_s=60.0):
    w = make_env(tmp_path, monkeypatch, lease_s=lease_s)
    w.here = tmp_path / "here"
    w.here.mkdir()
    w.mine = tmp_path / "mine"
    w.mine.mkdir()
    w.book = col.RemoteJobBook(w.mine)
    w.speeds = snd.SpeedBook(w.mine)
    w.client = brc.BenchmarkClient(f"127.0.0.1:{w.courier.settings.port}", w.courier.settings.code, OWNER, allow_loopback=True, pc="LAPTOP")
    w.launched = []
    real = w.launcher.start

    async def recording(model_path, *a, **k):
        w.launched.append(str(model_path))
        return await real(model_path, *a, **k)
    w.launcher.start = recording
    return w


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    await w.courier.start()
    w.jobs_root = w.courier.local.folder / rp.JOBS_SUBDIR
    yield w
    await w.courier.stop()
    for eng in w.launcher.engines:
        eng.stop()


def blob(name: str, kb: int, seed: str = "x") -> bytes:
    return (hashlib.sha256(f"{seed}{name}".encode()).digest() * (kb * 32))[: kb * 1024]


def put(w, name: str, kb: int = 64, seed: str = "x", data: bytes = None) -> Path:
    p = w.here / name
    p.write_bytes(data if data is not None else b"GGUF" + blob(name, kb, seed))
    return p


async def items_for(w, **models):
    return await snd.hash_items({k: v for k, v in models.items()}, w.mine)


async def batch(w, items, **extra):
    return await snd.start_and_send(w.client, w.book, w.mine, items, "dev-1", "LAPTOP", w.speeds, heartbeat_s=0.1, **{**OPTS, **extra})


async def job_ended(w, job_id, timeout=40.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j = w.courier.benchmarks.jobs[job_id]
        if j["state"] in rp.ENDED_STATES + (rp.J_COLLECTED,):
            return j
        await asyncio.sleep(0.05)
    raise AssertionError(f"job stayed {j['state']}")


def job_files(w):
    return [p for p in w.jobs_root.rglob("*") if p.is_file()] if w.jobs_root.exists() else []


# --- send, run, delete ------------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_a_model_is_sent_then_tested_then_deleted_and_the_device_downloads_nothing(world):
    w = world
    before = {m.id: m.path for m in w.courier.local.installed()}
    src = put(w, "Sent Model.gguf")
    items = await items_for(w, sent=src)
    res = await batch(w, items)
    assert res["sent"] == ["sent"] and res["already_there"] == [] and res["bytes_sent"] == src.stat().st_size
    j = await job_ended(w, res["job_id"])
    assert j["state"] == "finished" and j["after"] == {items[0].content_id: "deleted"}
    assert len(w.launched) == 1 and Path(w.launched[0]).parent.name == res["job_id"] and Path(w.launched[0]).name == f"{items[0].sha256[:16]}.gguf"     # named from its hash, in the job's own folder
    assert not job_files(w) and not (w.jobs_root / res["job_id"]).exists()                                   # gone right after its test
    assert {m.id: m.path for m in w.courier.local.installed()} == before                                     # never became a model of the device
    run = w.courier.benchmarks.runs_dir / j["runs"][0]["run_id"]
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "completed" and m["model_delivery"] == {"how": "sent", "keep_requested": False} and m["model_id"] == items[0].content_id
    assert w.speeds.last("dev-1") and w.speeds.estimate("dev-1", 10 * 1024 ** 2)["seconds"] >= 0


async def test_a_model_already_there_by_its_bytes_is_not_sent_and_never_deleted(world):
    w = world
    original = w.courier.local.installed()[0].path
    copy = put(w, "Another Name.gguf", data=original.read_bytes())                                            # same bytes, another name
    items = await items_for(w, copy=copy)
    res = await batch(w, items)
    assert res["sent"] == [] and res["already_there"] == ["copy"] and res["bytes_sent"] == 0
    j = await job_ended(w, res["job_id"])
    assert j["state"] == "finished" and w.launched == [str(original)]                                         # tested in place
    assert original.is_file() and not w.courier.benchmarks.receiver.items and not job_files(w)
    m = json.loads((w.courier.benchmarks.runs_dir / j["runs"][0]["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert m["model_delivery"]["how"] == "already_there"
    assert j["after"] == {}                                                                                  # nothing was done to it afterwards


async def test_the_same_name_with_different_bytes_is_sent_and_the_original_is_left_alone(world):
    w = world
    original = w.courier.local.installed()[0].path
    kept_bytes = original.read_bytes()
    other = put(w, original.name, 64, seed="different")                                                       # "Den Model.gguf" again, other bytes
    items = await items_for(w, other=other)
    res = await batch(w, items)
    assert res["sent"] == ["other"]
    await job_ended(w, res["job_id"])
    assert original.read_bytes() == kept_bytes and Path(w.launched[0]) != original


async def test_the_devices_inventory_names_models_by_their_bytes_and_gives_no_path(world):
    w = world
    inv = await w.client.models()
    assert inv["free_bytes"] > 0 and all(m["content_id"].startswith("sha256:") for m in inv["models"])
    assert str(w.courier.local.folder) not in json.dumps(inv)


# --- cut transfers, bad bytes, no room ----------------------------------------------------------------------------------------------------------------------------------------------

async def test_a_transfer_cut_at_sixty_percent_leaves_no_model_and_a_quiet_owner_loses_the_job_and_its_files(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch, lease_s=1.2)
    await w.courier.start()
    try:
        w.jobs_root = w.courier.local.folder / rp.JOBS_SUBDIR
        src = put(w, "Big.gguf", 400)
        data = src.read_bytes()
        it = (await items_for(w, big=src))[0]
        before = [m.id for m in w.courier.local.installed()]
        plan = brc.build_plan([{"id": it.content_id, "file": it.file, "sha256": it.sha256, "size": it.size}], OWNER, **OPTS)
        started = await w.client.start(plan, models_missing=True)
        sender = w.client.sender()
        info = await sender.transfer_begin({"sha256": it.sha256, "size": it.size})
        cut = int(len(data) * 0.6)
        await sender.transfer_chunk(info["id"], 0, data[:cut])                                                  # ... and the connection dies
        assert [p.name for p in job_files(w)] == [f"{it.sha256[:16]}.gguf.partial", f"{it.sha256[:16]}.gguf.partial.json"]
        assert [m.id for m in w.courier.local.installed()] == before and not any(p.suffix == ".gguf" for p in job_files(w))      # a half file is not a model
        j = await job_ended(w, started["job_id"])
        assert j["state"] == "interrupted" and j["reason"] == "lost_contact"
        assert not job_files(w) and not (w.jobs_root / started["job_id"]).exists() and w.launched == []
    finally:
        await w.courier.stop()


async def test_a_restarted_courier_removes_what_a_dead_job_left(tmp_path):
    u = unit(tmp_path, models_root=tmp_path / "models")
    dead = tmp_path / "models" / rp.JOBS_SUBDIR / "job-dead-job-0001"
    dead.mkdir(parents=True)
    (dead / "abc.gguf.partial").write_bytes(b"half")
    (dead / "abc.gguf.partial.json").write_text("{}", encoding="utf-8")
    (dead / "def.gguf").write_bytes(b"whole but nobody's now")
    keep = tmp_path / "models" / "keeper.gguf"
    keep.write_bytes(b"someone's model")
    fresh = bcr.CourierBenchmarks(tmp_path, u.svc, lambda: {}, device_fn=lambda: {}, models_root=tmp_path / "models", free_mb_fn=lambda p: 1e6)
    out = await fresh.reconcile()
    assert out["orphans_removed"] == 1 and not dead.exists() and keep.exists()


async def test_a_file_whose_hash_does_not_match_is_refused_and_never_becomes_a_model(world):
    w = world
    src = put(w, "Real.gguf", 64)
    liar = put(w, "Liar.gguf", 64, seed="liar")
    assert src.stat().st_size == liar.stat().st_size
    it = (await items_for(w, real=src))[0]
    plan = brc.build_plan([{"id": it.content_id, "file": it.file, "sha256": it.sha256, "size": it.size}], OWNER, **OPTS)
    await w.client.start(plan, models_missing=True)
    sender = w.client.sender()
    info = await sender.transfer_begin({"sha256": it.sha256, "size": it.size})
    await sender.transfer_chunk(info["id"], 0, liar.read_bytes())                                              # the wrong bytes, the right size
    with pytest.raises(file_transfer.TransferError) as e:
        await sender.transfer_finish(info["id"])
    assert e.value.status == 422
    assert not job_files(w) and w.courier.benchmarks.active_job()["to_send"][0]["state"] != "received"


async def test_not_enough_room_refuses_early_with_the_numbers_on_both_sides(world):
    w = world
    src = put(w, "Room.gguf", 64)
    items = await items_for(w, room=src)
    need = items[0].size
    spare = snd.SPARE_MIN_BYTES
    p = snd.room_problem(items, need + spare - 1, 10 ** 12, "LAPTOP")
    assert p.code == "no_space_device" and "LAPTOP" in p.message and "GB" in p.message and p.extra["need_bytes"] == need + spare
    assert snd.room_problem(items, need + spare, 10 ** 12, "LAPTOP") is None
    p = snd.room_problem(items, 10 ** 12, spare - 1, "LAPTOP")
    assert p.code == "no_space_here" and p.extra["have_bytes"] == spare - 1
    assert snd.room_problem(items, None, 10 ** 12, "LAPTOP").code == "no_space_device"                          # a figure that could not be read counts as no room
    big = [snd.Item("m", src, "m.gguf", "c", "a" * 64, 50 * 1024 ** 3)]
    assert snd.room_problem(big, int(54e9), 10 ** 12, "L").extra["need_bytes"] == 55 * 1024 ** 3                  # the spare is 10% when that is more than 2 GB
    assert snd.room_problem([snd.Item("m", src, "m.gguf", "c", "a" * 64, 5, snd.ALREADY_THERE)], 0, 0, "L") is None   # nothing to send needs no room
    # start_and_send refuses before the job exists
    w.courier.benchmarks.models_free_fn = lambda p: 1000
    with pytest.raises(snd.SendRefused) as e:
        await batch(w, items)
    assert e.value.code == "no_space_device" and not w.courier.benchmarks.jobs
    # and the device refuses a start that slipped past (its own check, with its own numbers)
    plan = brc.build_plan([{"id": items[0].content_id, "file": items[0].file, "sha256": items[0].sha256, "size": items[0].size}], OWNER, **OPTS)
    with pytest.raises(brc.RemoteError) as e2:
        await w.client.start(plan, models_missing=True)
    assert e2.value.code == "no_space" and "free" in e2.value.message and e2.value.extra.get("have_bytes") == 1000 and not w.courier.benchmarks.jobs
    assert not w.courier.benchmarks.lock_path.exists()


async def test_a_disk_that_fills_during_a_write_ends_the_job_and_throws_away_what_arrived(world, monkeypatch):
    w = world
    src = put(w, "Fills.gguf", 400)
    items = await items_for(w, f=src)
    real = file_transfer.TransferReceiver.chunk
    calls = {"n": 0}

    async def failing(self, tid, offset, body):
        calls["n"] += 1
        if calls["n"] >= 2:
            inc = self._get(tid)
            inc.state, inc.error = "error", "Couldn't write the file (No space left on device)."
            raise file_transfer.TransferError(500, inc.error)
        return await real(self, tid, offset, body)
    monkeypatch.setattr(file_transfer.TransferReceiver, "chunk", failing)
    monkeypatch.setattr(file_transfer, "CHUNK_BYTES", 100 * 1024)
    with pytest.raises(snd.SendRefused) as e:
        await batch(w, items)
    assert e.value.code == "send_failed"
    j = await job_ended(w, next(iter(w.courier.benchmarks.jobs)))
    assert j["state"] == "failed" and j["reason"] == "disk_full" and "disk space" in j["message"]
    assert not job_files(w) and w.launched == []


# --- keep ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_keep_moves_the_model_into_the_normal_folder_and_lists_it(world):
    w = world
    src = put(w, "Keep Me.gguf", 64)
    items = await items_for(w, k=src)
    before = {m.id for m in w.courier.local.installed()}
    res = await batch(w, items, keep_models=True)
    j = await job_ended(w, res["job_id"])
    assert j["state"] == "finished" and j["after"] == {items[0].content_id: "kept"}
    kept = w.courier.local.folder / "models" / bcr.KEPT_FOLDER / "Keep Me.gguf"
    assert kept.is_file() and kept.read_bytes() == src.read_bytes() and not job_files(w)
    new = [m for m in w.courier.local.installed() if m.id not in before]
    assert len(new) == 1 and new[0].path == kept                                                             # listed like a model added by hand
    import courier_adapters as ca
    from tests.test_benchmarks_courier import logging_stub
    w.courier.adapters = [ca.LlamaCppAdapter(logging_stub, w.courier.local)]                                   # (the Other devices list comes from this adapter)
    _, rep = await w.call("GET", "/courier/v1/report?fresh=1", expect=200)
    assert any(m["id"] == new[0].id for p in rep["programs"] for m in p["models"])
    m = json.loads((w.courier.benchmarks.runs_dir / j["runs"][0]["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert m["model_delivery"] == {"how": "sent", "keep_requested": True}
    again = await batch(w, await items_for(w, k=src), keep_models=True)                                       # the same bytes next time: already there, nothing sent, nothing doubled
    assert again["sent"] == [] and again["already_there"] == ["k"]
    await job_ended(w, again["job_id"])
    assert len([m for m in w.courier.local.installed() if m.id not in before]) == 1


async def test_keep_does_not_keep_a_test_that_did_not_complete(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    w.launcher.fail_start = "the engine would not start"
    await w.courier.start()
    try:
        src = put(w, "Fails.gguf")
        items = await items_for(w, f=src)
        res = await batch(w, items, keep_models=True)
        j = await job_ended(w, res["job_id"])
        assert j["state"] == "failed"
        assert not (w.courier.local.folder / "models" / bcr.KEPT_FOLDER).exists() and not (w.courier.local.folder / rp.JOBS_SUBDIR / res["job_id"]).exists()
    finally:
        await w.courier.stop()


# --- the lease, cancel, owner rules ---------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_lease_keeps_renewing_during_a_long_transfer_and_is_lost_without_the_owners_contact(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch, lease_s=1.5)
    await w.courier.start()
    try:
        src = put(w, "Slow.gguf", 600)
        monkeypatch.setattr(file_transfer, "CHUNK_BYTES", 64 * 1024)
        real = brc.JobSender.transfer_chunk

        async def slow(self, tid, offset, data):
            await asyncio.sleep(0.3)
            return await real(self, tid, offset, data)
        monkeypatch.setattr(brc.JobSender, "transfer_chunk", slow)
        t0 = time.monotonic()
        res = await snd.start_and_send(w.client, w.book, w.mine, await items_for(w, s=src), "dev-1", "LAPTOP", w.speeds, heartbeat_s=0.2, **OPTS)
        assert time.monotonic() - t0 > 2.5                                                                       # longer than the lease: only the heartbeat task kept the job alive
        assert (await job_ended(w, res["job_id"]))["state"] == "finished"
        # the same without any contact from the owner: the job is lost and so are its files
        src2 = put(w, "Slow2.gguf", 600, seed="2")
        with pytest.raises(snd.SendRefused) as e:
            await snd.start_and_send(w.client, w.book, w.mine, await items_for(w, s2=src2), "dev-1", "LAPTOP", w.speeds, heartbeat_s=1000, **OPTS)
        assert e.value.code == "send_failed"
        jid = [k for k, j in w.courier.benchmarks.jobs.items() if k != res["job_id"]][0]
        j = await job_ended(w, jid)
        assert j["state"] == "interrupted" and j["reason"] == "lost_contact"
        w.jobs_root = w.courier.local.folder / rp.JOBS_SUBDIR
        assert not job_files(w)
    finally:
        await w.courier.stop()


async def test_cancel_in_the_middle_of_a_send_cleans_up(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    await w.courier.start()
    try:
        w.jobs_root = w.courier.local.folder / rp.JOBS_SUBDIR
        src = put(w, "Cancel.gguf", 600)
        monkeypatch.setattr(file_transfer, "CHUNK_BYTES", 64 * 1024)
        real = brc.JobSender.transfer_chunk

        async def slow(self, tid, offset, data):
            await asyncio.sleep(0.15)
            return await real(self, tid, offset, data)
        monkeypatch.setattr(brc.JobSender, "transfer_chunk", slow)
        task = asyncio.create_task(snd.start_and_send(w.client, w.book, w.mine, await items_for(w, c=src), "dev-1", "LAPTOP", w.speeds, heartbeat_s=0.2, **OPTS))
        end = time.monotonic() + 20
        while time.monotonic() < end and not any(p.suffix == ".partial" for p in job_files(w)):
            await asyncio.sleep(0.05)
        assert any(p.suffix == ".partial" for p in job_files(w))
        st = await w.client.cancel()
        assert st["cancel_requested"] is True
        with pytest.raises(snd.SendRefused):
            await task
        j = await job_ended(w, next(iter(w.courier.benchmarks.jobs)))
        assert j["state"] == "cancelled" and not job_files(w) and w.launched == []
    finally:
        await w.courier.stop()


async def test_the_send_routes_are_owner_only_and_only_for_models_the_plan_needs(world):
    w = world
    src = put(w, "Owner.gguf", 64)
    it = (await items_for(w, o=src))[0]
    plan = brc.build_plan([{"id": it.content_id, "file": it.file, "sha256": it.sha256, "size": it.size}], OWNER, **OPTS)
    started = await w.client.start(plan, models_missing=True)
    base = f"/courier/v1/benchmarks/jobs/{started['job_id']}/models"
    good = w.headers(OWNER, started["job_token"])
    body = {"sha256": it.sha256, "size": it.size}
    for headers, code in ((w.headers(OTHER, started["job_token"]), 403), (w.headers(OWNER, "wrong-key-wrong-key"), 403), (w.headers(OWNER), 403)):
        assert (await w.call("POST", base, body, headers))[0] == code
    assert (await w.call("POST", base, {"sha256": "f" * 64, "size": it.size}, good))[0] == 404                      # not a file this plan needs
    assert (await w.call("POST", base, {"sha256": it.sha256, "size": it.size + 1}, good))[0] == 404
    status, info = await w.call("POST", base, body, good)
    assert status == 200 and info["state"] == "receiving"
    assert (await w.call("PUT", f"{base}/bm-nope__nope.gguf?offset=0", None, good))[0] == 404                         # a transfer that is not this job's
    assert (await w.call("PUT", f"{base}/{info['id']}?offset=0", None, w.headers(OTHER, started["job_token"])))[0] == 403
    assert (await w.call("POST", base, body, {**good, "Origin": "https://evil.example"}))[0] == 403
    # the general transfer door does not take a job's models, and a plan's file name is never a path
    assert not job_files(w) or all(p.name.startswith(it.sha256[:16]) for p in job_files(w))
    await w.client.cancel()
    await job_ended(w, started["job_id"])
    assert (await w.call("POST", base, body, good))[0] == 409                                                    # an ended job takes no more models
    assert not job_files(w)


# --- this PC's side: speeds, downloading once, the report -------------------------------------------------------------------------------------------------------------------------

async def test_the_last_speed_per_device_gives_a_time_estimate_or_nothing(tmp_path):
    book = snd.SpeedBook(tmp_path)
    assert book.last("dev-1") is None and book.estimate("dev-1", 10 ** 9) is None                              # no speed known: no estimate, only the size
    book.record("dev-1", 50 * 1024 ** 2, wall=lambda: 1000.0)
    book.record("dev-1", 0)
    book.record("", 99)
    again = snd.SpeedBook(tmp_path)                                                                          # kept in a file
    assert again.last("dev-1") == 50 * 1024 ** 2 and again.estimate("dev-1", 5 * 1024 ** 3) == {"seconds": 102, "bytes_per_s": 50 * 1024 ** 2}
    assert again.estimate("dev-2", 5) is None and again.estimate("dev-1", 0) is None


async def test_a_missing_model_is_downloaded_here_once_per_batch(tmp_path):
    calls = []

    async def resolve(key):
        calls.append(key)
        return tmp_path / f"{key}.gguf"
    out = await snd.ensure_local(["a", "b", "a", "a"], resolve)
    assert calls == ["a", "b"] and set(out) == {"a", "b"}


async def test_the_courier_offers_receiving_and_an_old_one_is_refused_when_models_must_be_sent(world):
    w = world
    _, rep = await w.call("GET", "/courier/v1/report", expect=200)
    block = rep["courier"]["benchmarks"]
    assert rp.CAP_RECEIVE_MODELS in block["capabilities"]
    plan = rp.validate_plan(json.loads(body_for()), {"unit-model": {"file": "unit.gguf"}})
    brc.check_report(rep, plan, "LAPTOP", models_missing=True)
    old = {"courier": {"benchmarks": {"protocol_version": 1, "capabilities": [c for c in block["capabilities"] if c != rp.CAP_RECEIVE_MODELS]}}}
    brc.check_report(old, plan, "LAPTOP", models_missing=False)
    with pytest.raises(brc.OldCourier) as e:
        brc.check_report(old, plan, "LAPTOP", models_missing=True)
    assert e.value.missing == [rp.CAP_RECEIVE_MODELS]


async def test_safe_model_file_names():
    assert snd.safe_model_file("Qwen3-4B-Q4_K_M.gguf") == "Qwen3-4B-Q4_K_M.gguf"
    for odd in ("../evil.gguf", "a;b&c.gguf", "C:\\x\\y.gguf", "名前.gguf", "...gguf", "", "x" * 400 + ".gguf"):
        assert rp.GGUF_NAME.match(snd.safe_model_file(odd)), odd


@pytest.mark.skipif(os.name != "nt", reason="a directory junction is a Windows thing")
async def test_a_job_folder_swapped_for_a_junction_after_the_check_is_refused_and_nothing_lands_outside(world):
    import subprocess
    w = world
    src = put(w, "Swap.gguf", 64)
    it = (await items_for(w, s=src))[0]
    data = src.read_bytes()
    plan = brc.build_plan([{"id": it.content_id, "file": it.file, "sha256": it.sha256, "size": it.size}], OWNER, **OPTS)
    started = await w.client.start(plan, models_missing=True)
    sender = w.client.sender()
    info = await sender.transfer_begin({"sha256": it.sha256, "size": it.size})
    folder = w.jobs_root / started["job_id"]
    outside = w.courier.settings.path.parent / "outside"
    outside.mkdir()
    import shutil
    shutil.rmtree(folder)                                                                                  # something else replaces the job's folder with a junction to somewhere else
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder), str(outside)], capture_output=True)
    assert out.returncode == 0
    try:
        with pytest.raises(file_transfer.TransferError) as e:
            await sender.transfer_chunk(info["id"], 0, data[:1000])
        assert e.value.status == 409
        with pytest.raises(file_transfer.TransferError):
            await sender.transfer_finish(info["id"])
        assert os.listdir(outside) == []                                                                   # nothing was written through the junction
        j = await job_ended(w, started["job_id"])
        assert j["state"] == "failed" and j["reason"] == "unsafe_path"
    finally:
        os.rmdir(folder)
