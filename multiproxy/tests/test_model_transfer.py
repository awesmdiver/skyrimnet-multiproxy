"""Download once (2026-10-01): send AI models from the main device to a Courier device.

A real Courier and the main device's real sender talk over real HTTP on spare ports, with generated files in a scratch folder (never
the director's real models). The Courier receives into its own models folder; the sender only offers MultiProxy's own installed models.
"""
import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import aiohttp
import pytest

import courier_client as cc
import file_transfer as ft
import local_models as lm
import model_transfer as mt
import other_pcs as op
from tests.test_courier import env  # noqa: F401
from tests.test_other_pcs_courier import allow_hosts, make_remotes, paired, until  # noqa: F401

pytestmark = pytest.mark.asyncio
CHUNK_FOR_TESTS = 64 * 1024


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Gaming:
    """The main device's local models: a stand-in `LocalModels` that lists generated files."""

    def __init__(self, tmp: Path):
        self.root = tmp / "gaming-models"
        self.root.mkdir()
        self.models = []

    def add(self, mid, data: bytes, mm: bytes = b"", custom=False, name=None):
        d = self.root / mid
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{mid}-Q4.gguf"
        p.write_bytes(data)
        mp = None
        if mm:
            mp = d / f"mmproj-{mid}.gguf"
            mp.write_bytes(mm)
        m = lm.Installed(mid, name or mid, bool(mp), p, mp, len(data) + len(mm), custom=custom)
        self.models.append(m)
        return m

    def installed(self):
        return list(self.models)


@pytest.fixture
def gaming(tmp_path):
    return Gaming(tmp_path)


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    monkeypatch.setattr(ft, "CHUNK_BYTES", CHUNK_FOR_TESTS)
    monkeypatch.setattr(ft, "SPACE_MARGIN_BYTES", 1024)


def catalog_model(monkeypatch, mid, data: bytes, mm: bytes = b""):
    """A tiny model that the catalog knows (so the checksum is known without computing it)."""
    files = [{"file": f"{mid}-Q4.gguf", "size": len(data), "sha256": sha(data), "role": "model"}]
    if mm:
        files.append({"file": f"mmproj-{mid}.gguf", "size": len(mm), "sha256": sha(mm), "role": "mmproj"})
    spec = {"id": mid, "name": mid, "blurb": "", "vision": bool(mm), "repo": "x/y", "files": files}
    monkeypatch.setitem(lm.CATALOG_BY_ID, mid, spec)
    monkeypatch.setattr(lm, "CATALOG", lm.CATALOG + [spec])
    return spec


async def setup_pair(env, gaming):
    remotes, r, _ = await paired(env)
    sender = mt.ModelSender(gaming, remotes, logging_stub())
    return remotes, r, sender


def logging_stub():
    import logging
    return logging.getLogger("test-model-transfer")


async def finished(sender, rid, mid, secs=30):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        j = sender.jobs.get((rid, mid))
        if j and j.state not in ("sending", "paused"):
            return j
        await asyncio.sleep(0.05)
    raise AssertionError("the copy didn't finish")


def courier_models_dir(env):
    return Path(env.courier.local.folder) / "models"


# ---- Part 1: the transfer ---------------------------------------------------------------------------------------------------
async def test_a_custom_model_is_copied_whole_verified_and_becomes_a_normal_model_there(env, allow_hosts, gaming):
    data, mm = os.urandom(300_000), os.urandom(70_000)
    m = gaming.add("my-model", data, mm, custom=True, name="My Model")
    remotes, r, sender = await setup_pair(env, gaming)
    sender.start("s2", "my-model")
    job = await finished(sender, "s2", "my-model")
    assert job.state == "done" and job.done == job.total == len(data) + len(mm)
    d = courier_models_dir(env) / "my-model"
    assert (d / "my-model-Q4.gguf").read_bytes() == data and (d / "mmproj-my-model.gguf").read_bytes() == mm
    assert not list(d.glob("*.partial*")), "no leftovers"
    installed = [i for i in env.courier.local.installed() if i.custom and i.name == "my-model-Q4"]
    assert installed and installed[0].mmproj is not None, "added as a downloaded model, with its vision file"


async def test_a_catalog_model_lands_exactly_where_a_download_would_and_counts_as_installed(env, allow_hosts, gaming, monkeypatch):
    data, mm = os.urandom(200_000), os.urandom(50_000)
    catalog_model(monkeypatch, "tiny-cat", data, mm)
    gaming.add("tiny-cat", data, mm, name="Tiny Cat")
    remotes, r, sender = await setup_pair(env, gaming)
    sender.start("s2", "tiny-cat")
    assert (await finished(sender, "s2", "tiny-cat")).state == "done"
    assert (courier_models_dir(env) / "tiny-cat" / "tiny-cat-Q4.gguf").read_bytes() == data
    assert any(i.id == "tiny-cat" and not i.custom for i in env.courier.local.installed())


async def test_resume_after_a_cut_carries_on_from_the_byte_it_reached_not_from_the_start(env, allow_hosts, gaming, monkeypatch):
    data = os.urandom(500_000)
    gaming.add("resume-me", data, custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    real = r.client.transfer_chunk
    state = {"n": 0}

    async def flaky(tid, offset, payload):
        state["n"] += 1
        if state["n"] == 4:
            raise aiohttp.ClientConnectionError("cable pulled")
        return await real(tid, offset, payload)
    r.client.transfer_chunk = flaky
    offsets = []
    real_begin = r.client.transfer_begin

    async def begin(spec):
        info = await real_begin(spec)
        offsets.append(info["offset"])
        return info
    r.client.transfer_begin = begin
    sender.start("s2", "resume-me")
    job = await finished(sender, "s2", "resume-me")
    assert job.state == "done"
    assert offsets[0] == 0 and offsets[1] == 3 * CHUNK_FOR_TESTS, "the second try started where the first one stopped"
    assert (courier_models_dir(env) / "resume-me" / "resume-me-Q4.gguf").read_bytes() == data


async def test_a_copy_resumes_after_the_courier_itself_restarted(env, allow_hosts, gaming):
    data = os.urandom(300_000)
    gaming.add("restarted", data, custom=True)
    remotes, r, _ = await paired(env)
    spec = {"kind": "model", "model": "restarted", "file": "restarted-Q4.gguf", "sha256": sha(data), "size": len(data), "role": "model"}
    info = await r.client.transfer_begin(spec)
    await r.client.transfer_chunk(info["id"], 0, data[:CHUNK_FOR_TESTS * 2])
    env.courier.transfers.items.clear()                                  # the Courier forgot everything in memory
    info2 = await r.client.transfer_begin(spec)
    assert info2["offset"] == CHUNK_FOR_TESTS * 2, "the partial file on disk is the memory"
    await r.client.transfer_chunk(info2["id"], info2["offset"], data[info2["offset"]:])
    await r.client.transfer_finish(info2["id"])
    assert (courier_models_dir(env) / "restarted" / "restarted-Q4.gguf").read_bytes() == data


async def test_a_wrong_checksum_deletes_the_partial_and_says_so_plainly(env, allow_hosts, gaming):
    data = os.urandom(100_000)
    remotes, r, _ = await paired(env)
    spec = {"kind": "model", "model": "bad-sum", "file": "bad-sum.gguf", "sha256": sha(b"something else"), "size": len(data), "role": "model"}
    info = await r.client.transfer_begin(spec)
    await r.client.transfer_chunk(info["id"], 0, data)
    with pytest.raises(ft.TransferError) as e:
        await r.client.transfer_finish(info["id"])
    assert e.value.message == "bad-sum.gguf didn't pass its safety check, so the copy was thrown away. Try again."
    d = courier_models_dir(env) / "bad-sum"
    assert not (d / "bad-sum.gguf").exists() and not list(d.glob("*.partial*")), "neither the file nor the partial is left"


async def test_no_room_is_refused_before_a_byte_moves_with_the_numbers(env, allow_hosts, gaming, monkeypatch):
    remotes, r, _ = await paired(env)
    monkeypatch.setattr(ft.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10_000_000_000, 9_000_000_000, 500_000_000))
    spec = {"kind": "model", "model": "huge", "file": "huge.gguf", "sha256": "a" * 64, "size": 7_400_000_000, "role": "model"}
    with pytest.raises(ft.TransferError) as e:
        await r.client.transfer_begin(spec)
    assert e.value.status == 507 and e.value.message == "{pc} doesn't have room for this. It needs 7.4 GB more, but only has 0.5 GB free."
    assert not list((courier_models_dir(env) / "huge").glob("*")), "nothing was created"


async def test_the_senders_error_names_the_pc_by_its_nickname(env, allow_hosts, gaming, monkeypatch):
    gaming.add("big-one", os.urandom(100_000), custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    monkeypatch.setattr(ft.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10_000_000_000, 9_999_000_000, 1_000))
    sender.start("s2", "big-one")
    job = await finished(sender, "s2", "big-one")
    assert job.state == "error" and job.message.startswith("s2 doesn't have room for this. It needs")


async def test_cancel_stops_the_copy_and_deletes_the_partial_file(env, allow_hosts, gaming):
    gaming.add("cancel-me", os.urandom(600_000), custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    real = r.client.transfer_chunk

    async def slow(tid, offset, payload):
        await asyncio.sleep(0.05)
        return await real(tid, offset, payload)
    r.client.transfer_chunk = slow
    sender.start("s2", "cancel-me")
    assert await until(lambda: sender.jobs[("s2", "cancel-me")].done > 0, 5)
    sender.cancel("s2", "cancel-me")
    job = await finished(sender, "s2", "cancel-me")
    assert job.state == "cancelled"
    d = courier_models_dir(env) / "cancel-me"
    assert not (d / "cancel-me-Q4.gguf").exists() and not list(d.glob("*.partial*"))


async def test_two_copies_at_once_both_arrive_intact(env, allow_hosts, gaming):
    a, b = os.urandom(400_000), os.urandom(350_000)
    gaming.add("model-a", a, custom=True)
    gaming.add("model-b", b, custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    sender.start("s2", "model-a")
    sender.start("s2", "model-b")
    ja, jb = await finished(sender, "s2", "model-a"), await finished(sender, "s2", "model-b")
    assert ja.state == jb.state == "done"
    assert (courier_models_dir(env) / "model-a" / "model-a-Q4.gguf").read_bytes() == a
    assert (courier_models_dir(env) / "model-b" / "model-b-Q4.gguf").read_bytes() == b


async def test_the_same_model_started_twice_is_one_copy(env, allow_hosts, gaming):
    gaming.add("twice", os.urandom(300_000), custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    assert sender.start("s2", "twice") is sender.start("s2", "twice")
    await finished(sender, "s2", "twice")


async def test_a_half_copied_file_is_never_offered_as_a_model(env, allow_hosts, gaming):
    data = os.urandom(300_000)
    remotes, r, _ = await paired(env)
    spec = {"kind": "model", "model": "half", "file": "half.gguf", "sha256": sha(data), "size": len(data), "role": "model"}
    info = await r.client.transfer_begin(spec)
    await r.client.transfer_chunk(info["id"], 0, data[:100_000])
    d = courier_models_dir(env) / "half"
    assert sorted(p.name for p in d.iterdir()) == ["half.gguf.partial", "half.gguf.partial.json"], "only a partial name exists until it is verified"
    assert not any(i.id == "half" or i.name == "half" for i in env.courier.local.installed())


async def test_a_wrong_offset_is_told_the_right_one(env, allow_hosts):
    remotes, r, _ = await paired(env)
    data = os.urandom(200_000)
    spec = {"kind": "model", "model": "offset", "file": "offset.gguf", "sha256": sha(data), "size": len(data), "role": "model"}
    info = await r.client.transfer_begin(spec)
    await r.client.transfer_chunk(info["id"], 0, data[:50_000])
    with pytest.raises(ft.TransferError) as e:
        await r.client.transfer_chunk(info["id"], 10_000, data[10_000:20_000])
    assert e.value.status == 409 and e.value.offset == 50_000


async def test_more_bytes_than_the_declared_size_is_refused(env, allow_hosts):
    remotes, r, _ = await paired(env)
    spec = {"kind": "model", "model": "toobig", "file": "toobig.gguf", "sha256": "b" * 64, "size": 1000, "role": "model"}
    info = await r.client.transfer_begin(spec)
    with pytest.raises(ft.TransferError) as e:
        await r.client.transfer_chunk(info["id"], 0, b"x" * 2000)
    assert e.value.status == 400


@pytest.mark.parametrize("model,file", [("../evil", "a.gguf"), ("ok", "../../evil.gguf"), ("ok", "sub/dir.gguf"), ("ok", "notamodel.exe"), ("a/b", "x.gguf"), ("", "x.gguf"), ("ok", "..\\x.gguf")])
async def test_a_path_in_the_request_is_refused_and_nothing_is_written(env, allow_hosts, model, file):
    remotes, r, _ = await paired(env)
    spec = {"kind": "model", "model": model, "file": file, "sha256": "c" * 64, "size": 100, "role": "model"}
    with pytest.raises(ft.TransferError) as e:
        await r.client.transfer_begin(spec)
    assert e.value.status == 400
    assert not (courier_models_dir(env).parent / "evil.gguf").exists()


async def test_other_kinds_of_file_are_refused(env, allow_hosts):
    remotes, r, _ = await paired(env)
    with pytest.raises(ft.TransferError) as e:
        await r.client.transfer_begin({"kind": "anything-else", "model": "x", "file": "x.gguf", "sha256": "d" * 64, "size": 10})
    assert e.value.status == 400


async def test_the_transfer_needs_the_pairing_code(env, allow_hosts):
    async with aiohttp.ClientSession() as s:
        async with s.post(env.base + "/courier/v1/transfers", json={}, headers={"Authorization": "Bearer ZZZ-999"}) as resp:
            assert resp.status == 403
        async with s.put(env.base + "/courier/v1/transfers/x?offset=0", data=b"x") as resp:
            assert resp.status in (401, 403)


# ---- the sender only offers its own models -------------------------------------------------------------------------------------
async def test_only_models_this_pc_has_can_be_sent_never_a_path(env, allow_hosts, gaming):
    gaming.add("real-one", os.urandom(1000), custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    for bad in ("../../etc/passwd", "C:\\Windows\\win.ini", "not-installed", ""):
        with pytest.raises(op.OtherPcsError) as e:
            sender.start("s2", bad)
        assert e.value.status == 404 and "doesn't have that model" in e.value.message
    assert not sender.jobs


async def test_the_offered_files_are_the_models_own_files_with_their_vision_file(gaming, monkeypatch):
    data, mm = os.urandom(5000), os.urandom(900)
    m = gaming.add("seeing", data, mm, custom=True)
    files = mt.offered_files(m)
    assert [(f["role"], f["file"], f["size"]) for f in files] == [("model", "seeing-Q4.gguf", 5000), ("mmproj", "mmproj-seeing.gguf", 900)]
    spec = catalog_model(monkeypatch, "cat-see", data, mm)
    c = gaming.add("cat-see", data, mm)
    cf = mt.offered_files(c)
    assert [f["sha256"] for f in cf] == [sha(data), sha(mm)], "a catalog model's checksums come from the catalog, not a slow re-read"


async def test_the_gaming_pcs_list_has_sizes_and_vision(gaming):
    gaming.add("a", os.urandom(1000))
    gaming.add("b", os.urandom(500), os.urandom(100), name="B Model")
    sender = mt.ModelSender(gaming, None, logging_stub())
    assert sender.gaming_models() == [
        {"id": "a", "name": "a", "size_bytes": 1000, "vision": False, "custom": False},
        {"id": "b", "name": "B Model", "size_bytes": 600, "vision": True, "custom": False}]


# ---- progress reaches the dashboard through the report ---------------------------------------------------------------------------
async def test_progress_is_in_the_couriers_report_with_bytes_and_speed_then_clears(env, allow_hosts, gaming):
    data = os.urandom(700_000)
    gaming.add("progress", data, custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    real = r.client.transfer_chunk

    async def slow(tid, offset, payload):
        await asyncio.sleep(0.25)
        return await real(tid, offset, payload)
    r.client.transfer_chunk = slow
    sender.start("s2", "progress")
    seen = None
    for _ in range(200):
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        llama = next(p for p in rep["programs"] if p["id"] == "llamacpp")
        rows = [d for d in llama["downloads"] if d.get("source") == "gaming-pc"]
        if rows and rows[0]["state"] == "running" and rows[0]["done"] > 0:
            seen = rows[0]
            break
        await asyncio.sleep(0.03)
    assert seen and seen["model"] == "progress" and seen["total"] == len(data) and seen["progress_known"] and seen["bytes"] is True and "speed_bps" in seen
    assert llama["capabilities"]["receive"] is True
    await finished(sender, "s2", "progress")
    rep = await env.call("GET", "/courier/v1/report?fresh=1")
    assert not [d for d in next(p for p in rep["programs"] if p["id"] == "llamacpp")["downloads"] if d["state"] == "running"]


async def test_a_pause_says_it_will_carry_on_by_itself_and_then_it_does(env, allow_hosts, gaming, monkeypatch):
    monkeypatch.setattr(ft, "send_file", ft.send_file)
    gaming.add("paused", os.urandom(300_000), custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    real = r.client.transfer_chunk
    gate = {"fail": 3}

    async def flaky(tid, offset, payload):
        if gate["fail"] > 0 and offset >= CHUNK_FOR_TESTS:
            gate["fail"] -= 1
            raise aiohttp.ClientConnectionError("laptop asleep")
        return await real(tid, offset, payload)
    r.client.transfer_chunk = flaky
    sender.start("s2", "paused")
    saw = False
    for _ in range(100):
        j = sender.jobs[("s2", "paused")]
        if j.state == "paused":
            saw = True
            assert j.message == "Waiting for s2 to answer. It will finish on its own."
        if j.state == "done":
            break
        await asyncio.sleep(0.05)
    assert saw, "it said it was waiting"
    assert (await finished(sender, "s2", "paused")).state == "done"


async def test_a_copy_that_never_gets_through_gives_up_with_a_plain_reason(env, allow_hosts, gaming):
    gaming.add("gives-up", os.urandom(100_000), custom=True)
    remotes, r, sender = await setup_pair(env, gaming)

    async def dead(*a, **k):
        raise aiohttp.ClientConnectionError("gone")
    r.client.transfer_begin = dead
    spec = {"kind": "model", "model": "gives-up", "file": "gives-up-Q4.gguf", "sha256": "e" * 64, "role": "model"}
    with pytest.raises(ft.TransferError) as e:
        await ft.send_file(r.client, spec, gaming.models[0].path, lambda *a: None, lambda: False, retry_for=0.3, pause=0.05)
    assert e.value.message == "The copy stopped because the other device didn't answer. Start it again when it's back."


async def test_nothing_big_is_held_in_memory_the_sender_reads_one_chunk_at_a_time(env, allow_hosts, gaming):
    big = os.urandom(CHUNK_FOR_TESTS * 12 + 123)
    gaming.add("chunky", big, custom=True)
    remotes, r, sender = await setup_pair(env, gaming)
    sizes = []
    real = r.client.transfer_chunk

    async def watch(tid, offset, payload):
        sizes.append(len(payload))
        return await real(tid, offset, payload)
    r.client.transfer_chunk = watch
    sender.start("s2", "chunky")
    await finished(sender, "s2", "chunky")
    assert max(sizes) <= CHUNK_FOR_TESTS and sum(sizes) == len(big) and len(sizes) == 13


async def test_a_model_the_courier_already_has_is_not_sent_again(env, allow_hosts, gaming, monkeypatch):
    data = os.urandom(200_000)
    catalog_model(monkeypatch, "have-it", data)
    gaming.add("have-it", data)
    remotes, r, sender = await setup_pair(env, gaming)
    d = courier_models_dir(env) / "have-it"
    d.mkdir(parents=True)
    (d / "have-it-Q4.gguf").write_bytes(data)
    sizes = []
    real = r.client.transfer_chunk

    async def watch(tid, offset, payload):
        sizes.append(len(payload))
        return await real(tid, offset, payload)
    r.client.transfer_chunk = watch
    sender.start("s2", "have-it")
    assert (await finished(sender, "s2", "have-it")).state == "done" and sizes == []
