"""Benchmarks on another PC, step 3: the results come back, and the Courier deletes them only after they are saved.

A real Courier on 127.0.0.1 (scratch folders, fake engine and card, as in test_benchmarks_courier.py) and this PC's collector on a scratch benchmark folder of its own.
"""
import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

import benchmarks_contract as bc
import benchmarks_courier as bcr
import benchmarks_remote_client as brc
import benchmarks_remote_collect as col
import benchmarks_remote_plan as rp
import benchmarks_results as bres
import benchmarks_store as bs
from benchmarks_fakes import write_model
from tests.test_benchmarks_courier import OTHER, OWNER, make_env, plan_body, unit, body_for, end_of

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    await e.courier.start()
    e.mine = tmp_path / "mine"                                   # this PC's own benchmark folder
    e.mine.mkdir()
    e.book = col.RemoteJobBook(e.mine)
    e.client = brc.BenchmarkClient(f"127.0.0.1:{e.courier.settings.port}", e.courier.settings.code, OWNER, allow_loopback=True, pc="LAPTOP")
    yield e
    await e.courier.stop()
    for eng in e.launcher.engines:
        eng.stop()


async def start_job(w, **kw):
    body = brc.build_plan([w.model_id], OWNER, levels=[1], requests_per_level=4, sustained=False, cooldown=False, while_testing="stop", **kw)
    started = await w.client.start(body)
    w.book.add(started["job_id"], started["job_token"], "dev", "LAPTOP", [w.model_id])
    return started["job_id"]


async def finish(w, job_id):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if w.courier.benchmarks.jobs[job_id]["state"] in rp.ENDED_STATES:
            return w.courier.benchmarks.jobs[job_id]
        await asyncio.sleep(0.05)
    raise AssertionError("job did not end")


def courier_run_hashes(w, run_id):
    folder = w.courier.benchmarks.runs_dir / run_id
    return {n: col._sha256(folder / n) for n in rp.RESULT_FILES if (folder / n).is_file()}


async def raw(w, method, path, body=None, headers=None):
    async with aiohttp.ClientSession() as s:
        async with s.request(method, w.base + path, json=body, headers=headers or w.headers()) as r:
            data = await r.read()
            try:
                return r.status, json.loads(data)
            except ValueError:
                return r.status, data


# --- the happy path ---------------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_a_finished_run_is_collected_checked_committed_and_only_then_deleted_on_the_courier(world):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    original = courier_run_hashes(w, run_id)
    pic = await col.collect_job(w.client, w.book, job_id, w.mine)
    assert pic["runs"][run_id]["state"] == col.SAVED and pic["done"] is True
    assert col._hashes_of(w.mine / run_id) == original                                   # the same bytes, file for file
    assert not (w.courier.benchmarks.runs_dir / run_id).exists()                         # the device's copy went only after it was saved here
    assert w.courier.benchmarks.jobs[job_id]["state"] == "collected" and not (w.mine / col.TEMP_FOLDER / run_id).exists()
    rows = bs.list_runs(w.mine, time.time())
    assert rows[0]["run_id"] == run_id and rows[0]["device"] == {"kind": "courier", "id": w.courier.settings.id, "name": rows[0]["device"]["name"]} and rows[0]["device"]["name"]
    run = bs.load_run(w.mine, run_id, time.time())
    assert run["manifest"]["status"] == "completed" and run["manifest"]["skyrim_state"] == "not_applicable"
    row = bres.result_row(run["manifest"], run["summary"])
    assert row["device"]["kind"] == "courier" and row["status"] == "completed" and row["generation_tok_s"]
    assert bres.detail(run["manifest"], run["summary"], run["requests"], run["samples"])["header"]["device"]["kind"] == "courier"
    again = await col.collect_job(w.client, w.book, job_id, w.mine)                      # nothing more to do, nothing downloaded twice
    assert again["done"] is True and list(again["runs"]) == [run_id]


async def test_a_local_run_still_reads_as_this_pc():
    assert bc.device_of({"device": "this"}) == {"kind": "this", "id": None, "name": None}
    assert bc.device_of({}) == {"kind": "this", "id": None, "name": None}
    assert bc.device_of({"device": {"kind": "courier", "id": "x" * 500, "name": "N" * 500}})["name"] == "N" * 80


async def test_deleting_a_collected_run_removes_only_this_pcs_copy(world):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    await col.collect_job(w.client, w.book, job_id, w.mine)
    before = dict(w.courier.benchmarks.jobs[job_id])
    bs.delete_run(w.mine, run_id)
    assert not (w.mine / run_id).exists()
    await col.collect_job(w.client, w.book, job_id, w.mine)                              # a later pass does not bring it back: the device has been told, and it is the person's choice
    assert not (w.mine / run_id).exists() and w.courier.benchmarks.jobs[job_id]["state"] == before["state"] == "collected"


# --- a crash between the commit and the acknowledge --------------------------------------------------------------------------------------------------------------------------

async def test_a_crash_between_commit_and_acknowledge_is_safe(world, monkeypatch):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    downloads = []
    real_download, real_ack = w.client.download, w.client.acknowledge

    async def counting(*a, **k):
        downloads.append(a[1])
        return await real_download(*a, **k)

    async def lost_ack(*a, **k):
        raise brc.NoContact()                                                            # the program died / the network dropped right after the commit
    monkeypatch.setattr(w.client, "download", counting)
    monkeypatch.setattr(w.client, "acknowledge", lost_ack)
    pic = await col.collect_job(w.client, w.book, job_id, w.mine)
    assert pic["runs"][run_id]["state"] == col.SAVED and pic["runs"][run_id]["acknowledged"] is False and pic["done"] is False
    assert (w.mine / run_id / "manifest.json").is_file() and (w.courier.benchmarks.runs_dir / run_id).exists()      # saved here, still kept there
    n = len(downloads)
    monkeypatch.setattr(w.client, "acknowledge", real_ack)
    fresh_book = col.RemoteJobBook(w.mine)                                               # (this PC restarted: the book is read from disk)
    pic = await col.collect_job(w.client, fresh_book, job_id, w.mine)
    assert pic["runs"][run_id]["acknowledged"] is True and pic["done"] is True
    assert len(downloads) == n                                                           # found already stored: nothing downloaded again
    assert not (w.courier.benchmarks.runs_dir / run_id).exists()


# --- a bad file, hostile names, the acknowledge's own rules ------------------------------------------------------------------------------------------------------------------

async def test_a_corrupted_file_is_not_committed_not_acknowledged_and_the_device_keeps_it(world, monkeypatch):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    real = w.client.download

    async def flipped(run, name, dest, size, **k):
        await real(run, name, dest, size, **k)
        if name == "requests.jsonl":
            data = bytearray(Path(dest).read_bytes())
            data[0] ^= 0xFF                                                              # same size, different bytes
            Path(dest).write_bytes(bytes(data))
    monkeypatch.setattr(w.client, "download", flipped)
    pic = await col.collect_job(w.client, w.book, job_id, w.mine)
    r = pic["runs"][run_id]
    assert r["state"] == col.FAILED and "LAPTOP" in r["reason"] and "still on LAPTOP" in r["reason"]
    assert not (w.mine / run_id).exists() and not (w.mine / col.TEMP_FOLDER / run_id).exists()
    assert (w.courier.benchmarks.runs_dir / run_id).exists() and w.courier.benchmarks.jobs[job_id]["state"] == "finished"
    assert pic["done"] is True                                                           # settled: not retried by itself, and the device keeps the run


async def test_results_that_do_not_look_right_are_refused_even_when_the_bytes_match(world):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    folder = w.courier.benchmarks.runs_dir / run_id
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for change, code in (({"schema_version": 99}, "newer_version"), ({"run_id": "someone-elses-run"}, "bad_result"), ({"status": "banana"}, "refused"), ({"schema_version": "1"}, "bad_result")):
        (folder / "manifest.json").write_text(json.dumps(dict(m, **change)), encoding="utf-8")
        with pytest.raises(col.CollectFailed) as e:
            await col.collect_run(w.client, run_id, w.mine)
        assert e.value.code == code, (change, e.value.code)
        assert not (w.mine / run_id).exists()
    (folder / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(col.CollectFailed):
        await col.collect_run(w.client, run_id, w.mine)
    (folder / "manifest.json").write_text("[" * 100000, encoding="utf-8")               # nested far too deep
    with pytest.raises(col.CollectFailed):
        await col.collect_run(w.client, run_id, w.mine)
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (folder / "requests.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(col.CollectFailed):
        await col.collect_run(w.client, run_id, w.mine)
    assert (folder / "manifest.json").exists() and not (w.mine / run_id).exists() and not (w.mine / col.TEMP_FOLDER / run_id).exists()


async def test_a_hostile_listing_is_refused_on_this_side(world, monkeypatch):
    w = world
    good = {"name": "manifest.json", "size": 10, "sha256": "a" * 64}
    cases = [
        [good, {"name": "../manifest.json", "size": 1, "sha256": "a" * 64}],
        [good, {"name": "C:\\Windows\\evil.json", "size": 1, "sha256": "a" * 64}],
        [good, {"name": "/etc/passwd", "size": 1, "sha256": "a" * 64}],
        [good, {"name": "evil.exe", "size": 1, "sha256": "a" * 64}],
        [good, {"name": "CON", "size": 1, "sha256": "a" * 64}],
        [good, dict(good)],                                                              # a name twice
        [dict(good, size=-1)], [dict(good, size=rp.MAX_RESULT_FILE_BYTES + 1)], [dict(good, size=True)], [dict(good, sha256="nothex")], [dict(good, sha256=None)],
        [{"name": "summary.json", "size": 1, "sha256": "a" * 64}],                       # no manifest
        [dict(good, size=rp.MAX_RESULT_FILE_BYTES)] * 1 + [{"name": n, "size": rp.MAX_RESULT_FILE_BYTES, "sha256": "a" * 64} for n in ("requests.jsonl", "samples.jsonl", "summary.json", "progress.json")],
        "not a list",
    ]
    for files in cases:
        async def listing(run_id, files=files):
            return {"run_id": run_id, "files": files}
        monkeypatch.setattr(w.client, "run_listing", listing)
        with pytest.raises(col.CollectFailed) as e:
            await col.collect_run(w.client, "run-2026-ok", w.mine)
        assert e.value.code == "bad_listing", files
    async def other_run(run_id):
        return {"run_id": "another-run", "files": [good]}
    monkeypatch.setattr(w.client, "run_listing", other_run)
    with pytest.raises(col.CollectFailed):
        await col.collect_run(w.client, "run-2026-ok", w.mine)
    for run_id in ("..", "../x", "a/b", "C:x", "", "run id", ".", "x" * 200):
        with pytest.raises(col.CollectFailed):
            await col.collect_run(w.client, run_id, w.mine)
    assert not any(p for p in w.mine.iterdir() if p.name != col.TEMP_FOLDER) and os.listdir(w.mine / col.TEMP_FOLDER) == [] if (w.mine / col.TEMP_FOLDER).exists() else True


async def test_a_run_here_with_other_content_is_never_overwritten(world):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    (w.mine / run_id).mkdir()
    (w.mine / run_id / "manifest.json").write_text('{"mine": true}', encoding="utf-8")
    with pytest.raises(col.CollectFailed) as e:
        await col.collect_run(w.client, run_id, w.mine)
    assert e.value.code == "different_here" and (w.mine / run_id / "manifest.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert (w.courier.benchmarks.runs_dir / run_id).exists()


async def test_the_acknowledge_needs_the_owner_the_key_and_every_hash(world):
    w = world
    job_id = await start_job(w)
    job = await finish(w, job_id)
    run_id = job["runs"][0]["run_id"]
    token = job["job_token"]
    here = f"/courier/v1/benchmarks/runs/{run_id}"
    good = courier_run_hashes(w, run_id)
    mine = w.headers(OWNER, token)
    assert (await raw(w, "DELETE", here, {}, mine))[0] == 400                            # no hashes
    assert (await raw(w, "DELETE", here, {"hashes": {}}, mine))[0] == 400
    short = dict(good)
    short.pop("samples.jsonl")
    status, data = await raw(w, "DELETE", here, {"hashes": short}, mine)
    assert status == 409 and data["error"]["code"] == "hash_mismatch"                    # not every file
    status, data = await raw(w, "DELETE", here, {"hashes": dict(good, **{"manifest.json": "0" * 64})}, mine)
    assert status == 409
    assert (await raw(w, "DELETE", here, {"hashes": good}, w.headers(OTHER, token)))[0] == 403                    # another PC
    assert (await raw(w, "DELETE", here, {"hashes": good}, w.headers(OWNER, "wrong-key-wrong-key")))[0] == 403    # the right PC, the wrong key
    assert (await raw(w, "DELETE", here, {"hashes": good}, w.headers(OWNER)))[0] == 403                           # no key
    assert (await raw(w, "DELETE", "/courier/v1/benchmarks/runs/run-never-had", {"hashes": good}, mine))[0] == 404
    assert (w.courier.benchmarks.runs_dir / run_id).exists()                             # nothing above deleted anything
    status, data = await raw(w, "DELETE", here, {"hashes": good}, mine)
    assert status == 200 and data["already"] is False and data["job_state"] == "collected"
    status, data = await raw(w, "DELETE", here, {"hashes": good}, mine)
    assert status == 200 and data["already"] is True                                     # a second delete is a success
    assert not (w.courier.benchmarks.runs_dir / run_id).exists()


async def test_the_file_route_serves_ranges_to_the_owner_only_and_only_the_five_names(world):
    w = world
    job_id = await start_job(w)
    job = await finish(w, job_id)
    run_id = job["runs"][0]["run_id"]
    data = (w.courier.benchmarks.runs_dir / run_id / "manifest.json").read_bytes()
    mine = w.headers(OWNER, job["job_token"])
    base = f"/courier/v1/benchmarks/runs/{run_id}/files/"
    async with aiohttp.ClientSession() as s:
        async with s.get(w.base + base + "manifest.json", headers={**mine, "Range": "bytes=10-"}) as r:
            assert r.status == 206 and await r.read() == data[10:]
        async with s.get(w.base + base + "manifest.json", headers=mine) as r:
            assert r.status == 200 and await r.read() == data
    for bad in ("evil.exe", "manifest.json%20", "CON", "requests.jsonl.bak"):
        assert (await raw(w, "GET", base + bad, None, mine))[0] == 400, bad
    assert (await raw(w, "GET", base + "manifest.json", None, w.headers(OTHER, job["job_token"])))[0] == 403
    assert (await raw(w, "GET", base + "manifest.json", None, w.headers(OWNER, "nope-nope-nope-nope")))[0] == 403
    assert (await raw(w, "GET", f"/courier/v1/benchmarks/runs/{run_id}", None, w.headers(OWNER, "nope-nope-nope-nope")))[0] == 403
    status, listing = await raw(w, "GET", f"/courier/v1/benchmarks/runs/{run_id}", None, mine)
    assert status == 200 and {f["name"] for f in listing["files"]} <= set(rp.RESULT_FILES) and "manifest.json" in {f["name"] for f in listing["files"]}
    assert (await raw(w, "GET", base + "manifest.json", None, {"Authorization": f"Bearer {w.courier.settings.code}"}))[0] == 400    # not this program (no PC id)
    assert (await raw(w, "GET", base + "manifest.json", None, {**mine, "Origin": "https://evil.example"}))[0] == 403


async def test_the_file_route_never_follows_a_link_out_of_the_run_folder(world):
    w = world
    job_id = await start_job(w)
    job = await finish(w, job_id)
    run_id = job["runs"][0]["run_id"]
    folder = w.courier.benchmarks.runs_dir / run_id
    secret = w.courier.settings.path.parent / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (folder / "samples.jsonl").unlink()
    if os.name == "nt":
        import subprocess
        tgt = w.courier.settings.path.parent / "outside"
        tgt.mkdir()
        (tgt / "samples.jsonl").write_text("x", encoding="utf-8")
        out = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "linked"), str(tgt)], capture_output=True)
        assert out.returncode == 0
        try:
            names = {f["name"] for f in w.courier.benchmarks._listing(run_id)}
            assert "samples.jsonl" not in names
        finally:
            os.rmdir(folder / "linked")


# --- waiting, resuming, interrupted batches ----------------------------------------------------------------------------------------------------------------------------------

async def test_a_device_that_is_away_leaves_the_run_waiting_and_a_half_download_is_continued(world, monkeypatch):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)
    run_id = w.courier.benchmarks.jobs[job_id]["runs"][0]["run_id"]
    real = w.client.download
    seen = {"calls": 0, "ranges": []}

    async def dropping(run, name, dest, size, **k):
        seen["calls"] += 1
        if name == "requests.jsonl" and seen["calls"] <= 3:
            data = (w.courier.benchmarks.runs_dir / run / name).read_bytes()
            Path(dest).write_bytes(data[: len(data) // 2])                               # half arrived, then the device went away
            raise brc.NoContact()
        have = Path(dest).stat().st_size if Path(dest).exists() else 0
        seen["ranges"].append((name, have))
        return await real(run, name, dest, size, **k)
    monkeypatch.setattr(w.client, "download", dropping)
    pic = await col.collect_job(w.client, w.book, job_id, w.mine)
    assert pic["runs"][run_id]["state"] == col.WAITING and pic["done"] is False and not (w.mine / run_id).exists()
    assert (w.courier.benchmarks.runs_dir / run_id).exists()
    for _ in range(3):
        pic = await col.collect_job(w.client, w.book, job_id, w.mine)
        if pic["runs"][run_id]["state"] == col.SAVED:
            break
    assert pic["runs"][run_id]["state"] == col.SAVED and pic["done"] is True
    assert ("requests.jsonl", (w.mine / run_id / "requests.jsonl").stat().st_size // 2) in [(n, h) for n, h in seen["ranges"]] or any(n == "requests.jsonl" and h > 0 for n, h in seen["ranges"])
    assert col._hashes_of(w.mine / run_id)["requests.jsonl"]


async def test_the_page_states_while_the_device_is_unreachable(world, monkeypatch):
    w = world
    job_id = await start_job(w)
    await finish(w, job_id)

    async def nobody():
        raise brc.NoContact()
    monkeypatch.setattr(w.client, "status", nobody)
    pic = await col.collect_job(w.client, w.book, job_id, w.mine)
    assert pic["contact"] is False and pic["done"] is False and pic["models"][0]["state"] in ("ready", col.WAITING)


async def test_an_interrupted_batch_keeps_what_finished_and_records_the_rest_as_not_run(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch, engine_kwargs=lambda n: {"token_s": 0.02, "tokens": 20})
    gg = [e.courier.local.add_custom(write_model(tmp_path, f"Other{i}.gguf")).id for i in (1, 2)]
    await e.courier.start()
    mine = tmp_path / "mine"
    mine.mkdir()
    book = col.RemoteJobBook(mine)
    client = brc.BenchmarkClient(f"127.0.0.1:{e.courier.settings.port}", e.courier.settings.code, OWNER, allow_loopback=True, pc="LAPTOP")
    try:
        models = [e.model_id] + gg
        started = await client.start(brc.build_plan(models, OWNER, levels=[1], requests_per_level=4, sustained=False, cooldown=False, while_testing="stop"))
        book.add(started["job_id"], started["job_token"], "dev", "LAPTOP", models)
        saved_while_running = False
        end = time.monotonic() + 60
        picture = None
        tripped = False
        while time.monotonic() < end:
            picture = await col.collect_job(client, book, started["job_id"], mine)
            if any(r["state"] == col.SAVED for r in picture["runs"].values()) and picture["job_state"] in rp.ACTIVE_STATES:
                saved_while_running = True                                               # collected as soon as the first model finished, not at the end
                if not tripped:
                    e.power.update(has_battery=True, battery_percent=5, plugged_in=False)       # the battery runs low in the middle of the next model
                    tripped = True
            if picture["done"]:
                break
            await asyncio.sleep(0.1)
        assert saved_while_running and picture["done"] and picture["job_state"] in ("interrupted", "collected") and picture["reason"] == "low_battery"
        states = [m["state"] for m in picture["models"]]
        assert states[0] == col.SAVED and states[-1] == col.NOT_RUN                        # the first was stored, the last never ran: nothing invented
        stored = bs.list_runs(mine, time.time())
        assert {r["status"] for r in stored} <= {"completed", "interrupted"} and len(stored) == len([s for s in states if s == col.SAVED])
        assert any(r["status"] == "completed" for r in stored)
        j = e.courier.benchmarks.jobs[started["job_id"]]
        assert j["state"] == "collected", (j["state"], j["runs"], j.get("acked"))
    finally:
        await e.courier.stop()
        for eng in e.launcher.engines:
            eng.stop()


async def test_a_restarted_courier_keeps_the_runs_of_an_interrupted_job_collectable(tmp_path):
    u = unit(tmp_path)
    plan = rp.validate_plan(json.loads(body_for()), {"unit-model": {"file": "unit.gguf"}})
    job = {"job_id": "job-left-behind-0002", "request_id": plan["request_id"], "owner_id": OWNER, "job_token": "tok-" + "x" * 30, "plan": plan, "state": "running", "reason": "", "message": "",
           "cancel_requested": False, "interrupt_reason": "", "created_at": "2026-10-28T10:00:00+00:00", "state_changed_at": "2026-10-28T10:00:00+00:00", "ended_at": None,
           "ended_epoch_s": None, "model_paths": [u.model], "runs": [], "services_started": [], "services_stopped_for_test": [], "services_restored": [], "batch_id": "b"}
    done = bs.create_run(u.jobs.runs_dir, "run-2026-done", {"status": "completed", "job": {"job_id": job["job_id"]}, "model_file": "unit.gguf", "steps_total": 3, "steps_done": 3})
    bs.finish_run(done, "completed", "", time.time(), 3, 3)
    bs.create_run(u.jobs.runs_dir, "run-2026-mid", {"status": "running", "job": {"job_id": job["job_id"]}, "model_file": "unit.gguf", "steps_total": 3, "steps_done": 1})
    u.jobs.jobs[job["job_id"]] = job
    u.jobs._save(job)
    fresh = bcr.CourierBenchmarks(tmp_path, u.svc, lambda: {"unit-model": u.model}, device_fn=lambda: {}, free_mb_fn=lambda p: 1e6)
    await fresh.reconcile()
    j = fresh.jobs[job["job_id"]]
    assert j["state"] == "interrupted" and {r["run_id"] for r in j["runs"]} == {"run-2026-done", "run-2026-mid"}
    listing = fresh.run_listing(OWNER, job["job_token"], "run-2026-mid")
    assert {f["name"] for f in listing["files"]} >= {"manifest.json", "progress.json"}
    states = {r["run_id"]: r["state"] for r in fresh.view(j)["runs"]}
    assert states == {"run-2026-done": "completed", "run-2026-mid": "interrupted"}
    for rid in ("run-2026-done", "run-2026-mid"):
        fresh.acknowledge(OWNER, job["job_token"], rid, {f["name"]: f["sha256"] for f in fresh.run_listing(OWNER, job["job_token"], rid)["files"]})
    assert fresh.jobs[job["job_id"]]["state"] == "collected"


# --- bounded retention, capabilities ----------------------------------------------------------------------------------------------------------------------------------

async def test_results_nobody_collects_are_removed_after_fourteen_days_with_a_log_line(tmp_path):
    t = [time.time()]
    u = unit(tmp_path)
    u.jobs.wall = lambda: t[0]
    lines = []
    u.jobs.log = lines.append
    status, job = await u.jobs.start(OWNER, body_for())
    await end_of(u, job["job_id"])
    await asyncio.sleep(0.1)
    run_id = u.jobs.jobs[job["job_id"]]["runs"][0]["run_id"]
    assert bcr.RETENTION_S == 14 * 86400
    t[0] += 13 * 86400
    u.jobs.prune()
    assert job["job_id"] in u.jobs.jobs and (u.jobs.runs_dir / run_id).exists()
    t[0] += 2 * 86400
    lines.clear()
    u.jobs.prune()
    assert job["job_id"] not in u.jobs.jobs and not (u.jobs.runs_dir / run_id).exists()
    assert any("never collected" in l and job["job_id"] in l for l in lines) and not any(u.jobs.jobs.get(job["job_id"], {}).get("job_token", "\0") in l for l in lines)


async def test_a_collected_jobs_record_goes_after_a_day_and_the_courier_says_it_can_collect(world):
    w = world
    _, rep = await w.call("GET", "/courier/v1/report", expect=200)
    block = rep["courier"]["benchmarks"]
    plan = rp.validate_plan(plan_body(w.model_id), {w.model_id: {"file": "Den Model.gguf"}})
    assert rp.missing_capabilities(block, rp.required_capabilities(plan)) == []           # collect and acknowledge are now offered: the full list is satisfied
    brc.check_report(rep, plan, collect=True)
    job_id = await start_job(w)
    await finish(w, job_id)
    await col.collect_job(w.client, w.book, job_id, w.mine)
    j = w.courier.benchmarks.jobs[job_id]
    assert j["state"] == "collected"
    w.courier.benchmarks.wall = lambda: time.time() + 2 * 86400
    w.courier.benchmarks.prune()
    assert job_id not in w.courier.benchmarks.jobs
