"""The main PC's side of a Courier update (courier_update_sender.py, the routes in multiproxy.py, courier_client.update_call).

Two kinds of stand-in: scripted fake devices (to prove the order, the codes, the busy and failure rows quickly) and one REAL Courier on 127.0.0.1 with a scratch install folder and
throwaway keys (to prove the whole send, stage and apply over real HTTP, and the wait for the new build). The 30 second wait is made short here. Nothing is ever installed.
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path

import httpx
import pytest

import courier as cr
import courier_client as cc
import courier_keys
import courier_update_sender as cus
import file_transfer as ft
import other_pcs as op
import update_verify as uv
from tests.fixtures import update_pkg as pk
from tests.test_courier import env  # noqa: F401
from tests.test_courier_update import dev  # noqa: F401
from tests.test_other_pcs_courier import allow_hosts, make_remotes, paired, until  # noqa: F401

pytestmark = pytest.mark.asyncio
log = logging.getLogger("test-update-sender")
NEW = cr.COURIER_BUILD + 1


def report(build, update=True, last=None):
    rep = {"courier": {"id": "x", "name": "x", "version": "1.0.0", "build": build}, "gpus": [], "programs": [], "settings": {}}
    if update:
        rep["capabilities"] = {"update": True}
    if last is not None:
        rep["last_update"] = last
    return rep


class FakeClient:
    """A device that behaves as scripted. `script` maps a step to an UpdateRefused code or None; the new build appears after apply unless `stay_old`."""

    def __init__(self, name, log_):
        self.name, self.calls, self.log_ = name, [], log_
        self.script, self.stay_old, self.silent, self.new_build = {}, False, False, NEW
        self.report = None
        self.applied = False
        self.size_seen = 0

    async def update_call(self, path, body, timeout=60):
        step = path.rsplit("/", 1)[-1]
        self.calls.append(step)
        self.log_.append((self.name, step))
        code = self.script.get(step)
        if code:
            raise cc.UpdateRefused(code, 409)
        if step == "begin":
            return {"id": "courier-update__courier-update.zip", "offset": 0, "state": "receiving"}
        if step == "apply":
            self.applied = True
        return {"state": step}

    async def transfer_chunk(self, tid, offset, data):
        self.size_seen = offset + len(data)
        await asyncio.sleep(0)
        return offset + len(data)

    async def transfer_finish(self, tid):
        self.calls.append("finish")
        return {"state": "done"}

    async def fetch_report(self):
        if self.silent and self.applied:
            raise OSError("no answer")
        if self.applied and not self.stay_old:
            self.report = report(self.new_build)
        return self.report


class FakeRemote:
    def __init__(self, rid, build=cr.COURIER_BUILD, update=True, online=True, name=None, log_=None):
        self.id, self.name, self.kind, self.online = rid, name or rid, "courier", online
        self.client = FakeClient(rid, log_ if log_ is not None else [])
        self.report = report(build, update)
        self.client.report = self.report

    @property
    def label(self):
        return self.name


class FakeRemotes:
    def __init__(self, *remotes):
        self.remotes = {r.id: r for r in remotes}
        self.applied = []

    def all(self):
        return list(self.remotes.values())

    def _courier(self, rid):
        if rid not in self.remotes:
            raise op.OtherPcsError(404, "That device isn't in your list.")
        return self.remotes[rid]

    def _apply_report(self, r, rep):
        r.report = rep
        self.applied.append((r.id, rep))


@pytest.fixture
def package(tmp_path, monkeypatch):
    """A signed package in a scratch folder, checked against a throwaway key."""
    monkeypatch.setattr(courier_keys, "CURRENT", pk.PUBLIC_B64)
    monkeypatch.setattr(courier_keys, "NEXT", "")
    path = tmp_path / "carried" / "MultiProxy-Courier-Update.zip"
    path.parent.mkdir()
    path.write_bytes(pk.package_bytes(NEW))
    cus._cache.clear()
    return path


def sender(remotes, package, **kw):
    kw.setdefault("wait", 0.3)
    kw.setdefault("grace", 0.3)
    kw.setdefault("poll", 0.02)
    return cus.UpdateSender(remotes, log, package_fn=lambda *a: cus.load_package(package, force=True), **kw)


async def finish(s, rid):
    job = s.jobs[rid]
    await asyncio.wait_for(job.task, 10)
    return job


# ---- the carried package ----------------------------------------------------------------------------------------------------------------------------
async def test_a_missing_or_badly_signed_update_file_means_no_update_is_offered(package, tmp_path, monkeypatch):
    good = cus.load_package(package)
    assert good.ok and good.build == NEW and good.size == package.stat().st_size and good.sha256 == hashlib.sha256(package.read_bytes()).hexdigest()
    assert cus.load_package(tmp_path / "nowhere.zip").ok is False
    package.write_bytes(pk.package_bytes(NEW, seed=pk.OTHER_SEED))                      # signed by a key the program does not trust
    bad = cus.load_package(package)
    assert bad.ok is False and bad.reason == "bad_signature" and bad.to_json() == {"ok": False, "build": None}
    package.write_bytes(pk.package_bytes(NEW)[:-9])
    assert cus.load_package(package).ok is False
    package.write_bytes(b"not a zip")
    assert cus.load_package(package).reason == "damaged"


async def test_the_package_is_checked_again_when_the_file_changes(package):
    assert cus.load_package(package).ok
    package.write_bytes(pk.package_bytes(NEW + 1))
    assert cus.load_package(package).build == NEW + 1


async def test_start_refuses_without_a_good_file_and_says_the_words(package):
    r = FakeRemote("MSI", build=4)
    s = sender(FakeRemotes(r), package)
    package.write_bytes(b"junk")
    with pytest.raises(op.OtherPcsError) as e:
        s.start("MSI")
    assert e.value.message == "No update file here." and r.client.calls == []


# ---- who can be updated -----------------------------------------------------------------------------------------------------------------------------
async def test_only_an_online_capable_device_that_is_behind_can_be_updated(package):
    rs = [FakeRemote("behind", build=4), FakeRemote("current", build=NEW), FakeRemote("old", build=4, update=False), FakeRemote("off", build=4, online=False),
          FakeRemote("none", build=4, online=None)]
    s = sender(FakeRemotes(*rs), package)
    assert [r.id for r in s.eligible()] == ["behind"]
    st = s.status()
    assert st["package"] == {"ok": True, "build": NEW} and st["eligible"] == ["behind"]
    assert st["devices"]["old"]["canUpdate"] is False and st["devices"]["behind"]["canUpdate"] is True
    for rid, phrase in (("old", "too old to update from here"), ("off", "isn't answering"), ("current", "already on 1.")):
        with pytest.raises(op.OtherPcsError) as e:
            s.start(rid)
        assert phrase in e.value.message, rid
    assert s.remote.remotes["old"].client.calls == []


async def test_a_courier_with_no_build_number_at_all_is_too_old_and_never_sent_anything(package):
    r = FakeRemote("ancient", build=0, update=False)
    r.report["courier"].pop("build")
    s = sender(FakeRemotes(r), package)
    with pytest.raises(op.OtherPcsError) as e:
        s.start("ancient")
    assert "too old to update from here. Update it on that device once." in e.value.message


# ---- one send ---------------------------------------------------------------------------------------------------------------------------------------
async def test_a_good_send_goes_begin_bytes_finish_stage_apply_and_is_silent_when_the_new_build_answers(package, caplog):
    caplog.set_level(logging.INFO, logger="test-update-sender")
    r = FakeRemote("MSI", build=4)
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    job = await finish(s, "MSI")
    assert r.client.calls == ["begin", "finish", "stage", "apply"] and r.client.size_seen == package.stat().st_size
    assert job.state == "done" and s.job_json("MSI") is None, "success is silent: nothing for the row to draw"
    assert s.remote.applied and s.remote.applied[-1][1]["courier"]["build"] == NEW
    lines = [m for m in caplog.messages if m.startswith("Courier update on")]
    assert lines == [f"Courier update on MSI: 1.4 to 1.{NEW}: ok"], "one line per attempt, with the device, the builds and the result"


async def test_the_row_can_show_the_bytes_going_across_and_then_the_wait(package):
    r = FakeRemote("MSI", build=4)
    seen = []
    orig = r.client.transfer_chunk

    async def spy(tid, off, data):
        seen.append(s.job_json("MSI"))
        return await orig(tid, off, data)
    r.client.transfer_chunk = spy
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    await finish(s, "MSI")
    assert seen and seen[0]["state"] == "sending" and seen[0]["to"] == NEW and seen[0]["from"] == 4


async def test_a_busy_device_says_so_keeps_nothing_staged_and_update_anyway_goes_through(package):
    r = FakeRemote("MSI", build=4)
    r.client.script["apply"] = "busy"
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    job = await finish(s, "MSI")
    assert job.state == "busy" and s.job_json("MSI")["state"] == "busy"
    assert r.client.calls[-2:] == ["apply", "cancel"], "what was staged is dropped again"
    r.client.script.clear()
    s.start("MSI", anyway=True)
    job = await finish(s, "MSI")
    assert job.state == "done"


async def test_the_anyway_flag_reaches_the_device_only_when_it_is_a_real_yes(package):
    r = FakeRemote("MSI", build=4)
    bodies = []
    orig = r.client.update_call

    async def spy(path, body, timeout=60):
        bodies.append((path.rsplit("/", 1)[-1], dict(body)))
        return await orig(path, body, timeout)
    r.client.update_call = spy
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    await finish(s, "MSI")
    assert [b for p, b in bodies if p == "apply"] == [{"build": NEW, "anyway": False}]


@pytest.mark.parametrize("step,device_code,expect", [("stage", "bad_signature", "bad_signature"), ("stage", "bad_package", "bad_package"), ("stage", "no_space", "no_space"),
                                                     ("stage", "runtime_changed", "runtime_changed"), ("begin", "update_failed", "update_failed"), ("apply", "update_failed", "update_failed"),
                                                     ("begin", "not_newer", "not_newer"), ("stage", "something odd", "update_failed")])
async def test_a_refusal_becomes_a_failed_row_with_the_real_reason(package, step, device_code, expect, caplog):
    caplog.set_level(logging.INFO, logger="test-update-sender")
    r = FakeRemote("MSI", build=4)
    r.client.script[step] = device_code
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    job = await finish(s, "MSI")
    assert job.state == "failed" and job.code == expect and s.job_json("MSI")["code"] == expect
    assert any(m == f"Courier update on MSI: 1.4 to 1.{NEW}: {expect}" for m in caplog.messages)


async def test_a_new_courier_that_never_answers_as_the_new_build_is_a_failure_with_its_reason(package):
    r = FakeRemote("MSI", build=4)
    r.client.stay_old = True
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    assert (await finish(s, "MSI")).code == "update_failed", "it answers, still the old build, and says nothing happened"

    r2 = FakeRemote("Kitchen", build=4)
    r2.client.silent = True
    s2 = sender(FakeRemotes(r2), package)
    t0 = time.monotonic()
    s2.start("Kitchen")
    job = await finish(s2, "Kitchen")
    assert job.code == "unreachable" and time.monotonic() - t0 >= 0.55, "a silent device gets the wait and then the grace before it is called unreachable"


async def test_a_rollback_the_device_reports_is_shown_as_rolled_back(package):
    r = FakeRemote("MSI", build=4)
    r.client.stay_old = True
    orig = r.client.fetch_report

    async def back():
        rep = await orig()
        if r.client.applied:
            rep = dict(rep, last_update={"code": "rolled_back", "from": 4, "to": NEW, "time": 1})
        return rep
    r.client.fetch_report = back
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    job = await finish(s, "MSI")
    assert job.state == "failed" and job.code == "rolled_back"


async def test_an_old_last_update_is_not_mistaken_for_this_try(package):
    r = FakeRemote("MSI", build=4)
    old = {"code": "rolled_back", "from": 4, "to": 5, "time": 1}
    r.report = report(4, last=old)
    r.client.report = r.report
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    assert (await finish(s, "MSI")).state == "done"


async def test_cancel_stops_the_bytes_and_the_row_goes_quiet(package):
    r = FakeRemote("MSI", build=4)
    s = sender(FakeRemotes(r), package)
    orig = r.client.update_call

    async def begin_then_cancel(path, body, timeout=60):
        res = await orig(path, body, timeout)
        if path.endswith("/begin"):
            s.cancel("MSI")                                  # the person pressed Cancel while the bytes were about to go
        return res
    r.client.update_call = begin_then_cancel
    s.start("MSI")
    job = await finish(s, "MSI")
    assert job.state == "cancelled" and s.job_json("MSI") is None and "stage" not in r.client.calls


async def test_a_dropped_connection_while_sending_waits_and_carries_on_then_gives_up(package, monkeypatch):
    r = FakeRemote("MSI", build=4)
    fails = {"n": 2}
    orig = r.client.transfer_chunk

    async def flaky(tid, off, data):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise OSError("wifi dropped")
        return await orig(tid, off, data)
    r.client.transfer_chunk = flaky
    monkeypatch.setattr(ft.asyncio, "sleep", asyncio.sleep)
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    assert (await finish(s, "MSI")).state == "done"

    r2 = FakeRemote("Kitchen", build=4)

    async def dead(tid, off, data):
        raise OSError("gone")
    r2.client.transfer_chunk = dead
    monkeypatch.setattr(cus, "SEND_RETRY_SECONDS", 0.1)
    s2 = sender(FakeRemotes(r2), package)
    s2.start("Kitchen")
    job = await finish(s2, "Kitchen")
    assert job.code == "unreachable"


async def test_a_second_start_while_one_runs_is_the_same_job(package):
    r = FakeRemote("MSI", build=4)
    s = sender(FakeRemotes(r), package)
    a = s.start("MSI")
    b = s.start("MSI")
    assert a is b
    await finish(s, "MSI")
    assert r.client.calls.count("begin") == 1


# ---- Update all -------------------------------------------------------------------------------------------------------------------------------------
async def test_update_all_goes_one_after_the_other_and_a_failure_does_not_stop_the_rest(package):
    order = []
    rs = [FakeRemote(n, build=4, log_=order) for n in ("A", "B", "C")]
    rs[1].client.script["stage"] = "no_space"
    skip = FakeRemote("D", build=4, update=False, log_=order)
    off = FakeRemote("E", build=4, online=False, log_=order)
    s = sender(FakeRemotes(*rs, skip, off), package)
    assert s.start_all() == 3 and s.status()["allRunning"] is True
    await asyncio.wait_for(s._all_task, 20)
    steps = [(n, st) for n, st in order]
    names_in_order = [n for n, st in steps if st == "begin"]
    assert names_in_order == ["A", "B", "C"]
    for first, second in (("A", "B"), ("B", "C")):
        last_of_first = max(i for i, (n, _) in enumerate(steps) if n == first)
        first_of_second = min(i for i, (n, _) in enumerate(steps) if n == second)
        assert last_of_first < first_of_second, "never together"
    assert s.jobs["A"].state == "done" and s.jobs["B"].code == "no_space" and s.jobs["C"].state == "done"
    assert "D" not in s.jobs and "E" not in s.jobs and s.status()["allRunning"] is False


async def test_update_all_skips_a_device_that_was_updated_by_hand_while_it_waited(package):
    rs = [FakeRemote(n, build=4) for n in ("A", "B")]
    s = sender(FakeRemotes(*rs), package)
    assert s.start_all() == 2
    await asyncio.sleep(0)
    rs[1].report = report(NEW)                       # updated by hand before its turn
    await asyncio.wait_for(s._all_task, 20)
    assert rs[1].client.calls == []


async def test_a_second_update_all_while_one_runs_does_nothing(package):
    rs = [FakeRemote(n, build=4) for n in ("A", "B")]
    s = sender(FakeRemotes(*rs), package)
    assert s.start_all() == 2 and s.start_all() == 0
    await asyncio.wait_for(s._all_task, 20)


# ---- a real Courier over real HTTP -----------------------------------------------------------------------------------------------------------------
async def real_world(env, dev, allow_hosts, tmp_path, package_bytes=None):  # noqa: F811
    remotes, r, _ = await paired(env)
    path = tmp_path / "carried-real.zip"
    path.write_bytes(package_bytes or pk.package_bytes(NEW))
    cus._cache.clear()
    s = cus.UpdateSender(remotes, log, package_fn=lambda *a: cus.load_package(path, force=True), wait=0.4, grace=0.4, poll=0.05)
    return remotes, r, s


async def test_a_real_courier_takes_the_whole_send_and_answers_as_the_new_build(env, dev, allow_hosts, tmp_path, monkeypatch):  # noqa: F811
    remotes, r, s = await real_world(env, dev, allow_hosts, tmp_path)
    cu_spawn = dev.mgr.spawn

    def spawn(argv, systemd):
        cu_spawn(argv, systemd)
        monkeypatch.setattr(cr, "COURIER_BUILD", NEW)
        env.courier._report = None                               # (a new process builds its report afresh)
    dev.mgr.spawn = spawn
    r.report["courier"]["build"] = NEW - 1                      # (what the device reported before)
    s.start(r.id)
    job = await finish(s, r.id)
    assert job.state == "done", job
    (argv, systemd), = dev.spawned
    assert argv[1] == "-I" and argv[2].endswith("courier_updater.py")
    assert (dev.install / "update" / "staging" / str(NEW) / "courier.py").is_file(), "the device checked and unpacked it"
    assert op.courier_build_of(r.report) == NEW, "the row now reads the new build"
    await remotes.stop_polling()


async def test_a_real_courier_that_is_busy_asks_and_then_goes_ahead(env, dev, allow_hosts, tmp_path, monkeypatch):  # noqa: F811
    remotes, r, s = await real_world(env, dev, allow_hosts, tmp_path)
    r.report["courier"]["build"] = NEW - 1
    env.courier.adapters[0].inflight = 1
    s.start(r.id)
    job = await finish(s, r.id)
    assert job.state == "busy" and dev.spawned == []
    cu_spawn = dev.mgr.spawn
    dev.mgr.spawn = lambda argv, systemd: (cu_spawn(argv, systemd), monkeypatch.setattr(cr, "COURIER_BUILD", NEW), setattr(env.courier, "_report", None))
    s.start(r.id, anyway=True)
    assert (await finish(s, r.id)).state == "done" and len(dev.spawned) == 1
    await remotes.stop_polling()


async def test_a_real_courier_that_cannot_take_the_runtime_says_so(env, dev, allow_hosts, tmp_path):  # noqa: F811
    other = pk.files_for(NEW, **{"requirements-courier.txt": b"aiohttp>=3.9.0\nnumpy\n"})
    remotes, r, s = await real_world(env, dev, allow_hosts, tmp_path, pk.package_bytes(NEW, files=other))
    r.report["courier"]["build"] = NEW - 1
    s.start(r.id)
    job = await finish(s, r.id)
    assert job.state == "failed" and job.code == "runtime_changed" and dev.spawned == []
    await remotes.stop_polling()


async def test_a_real_courier_with_no_room_says_so(env, dev, allow_hosts, tmp_path, monkeypatch):  # noqa: F811
    remotes, r, s = await real_world(env, dev, allow_hosts, tmp_path)
    r.report["courier"]["build"] = NEW - 1
    from tests import test_courier_update as tcu
    monkeypatch.setattr(tcu.cu.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    s.start(r.id)
    job = await finish(s, r.id)
    assert job.code == "no_space"
    await remotes.stop_polling()


async def test_a_package_signed_with_a_key_the_main_pc_does_not_carry_never_leaves_it(env, dev, allow_hosts, tmp_path):  # noqa: F811
    remotes, r, s = await real_world(env, dev, allow_hosts, tmp_path, pk.package_bytes(NEW, seed=pk.OTHER_SEED))
    r.report["courier"]["build"] = NEW - 1
    with pytest.raises(op.OtherPcsError) as e:
        s.start(r.id)
    assert e.value.message == "No update file here." and not (dev.install / "update").exists()
    await remotes.stop_polling()


async def test_the_client_turns_a_devices_no_into_its_reason_code(env, dev, allow_hosts):  # noqa: F811
    remotes, r, _ = await paired(env)
    with pytest.raises(cc.UpdateRefused) as e:
        await r.client.update_call("/courier/v1/update/apply", {"build": 99})
    assert e.value.code == "update_failed" and e.value.status == 409
    with pytest.raises(cc.UpdateRefused) as e:
        await r.client.update_call("/courier/v1/update/begin", {"build": 1, "size": 10, "sha256": "a" * 64})
    assert e.value.code == "not_newer"
    await remotes.stop_polling()


# ---- the routes the page calls ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


async def test_the_state_carries_the_package_the_builds_and_each_devices_job(client, proxy_module, package, monkeypatch):
    r = FakeRemote("MSI", build=4)
    r.to_json = lambda: {"id": "MSI", "kind": "courier", "name": "MSI", "online": True, "report": r.report, "models": [], "courierBuild": 4, "canUpdate": True, "courierOlder": False}
    r.base = "http://x"
    fake = FakeRemotes(r)
    s = sender(fake, package)
    monkeypatch.setattr(proxy_module, "update_sender", s)
    monkeypatch.setattr(proxy_module, "remote_svc", fake)
    monkeypatch.setattr(fake, "poll_all", lambda: asyncio.sleep(0), raising=False)
    monkeypatch.setattr(proxy_module, "_backup_state", lambda: {"enabled": False, "model": "", "active": False})
    d = (await client.get("/config/other-pcs")).json()
    assert d["courierUpdate"]["package"] == {"ok": True, "build": NEW} and d["courierUpdate"]["eligible"] == ["MSI"] and d["servers"][0]["update"] is None
    st = (await client.get("/config/other-pcs/courier/update")).json()
    assert st["devices"]["MSI"] == {"build": 4, "canUpdate": True, "online": True, "job": None} and st["allRunning"] is False


async def test_the_routes_start_cancel_and_update_all(client, proxy_module, package, monkeypatch):
    rs = [FakeRemote("A", build=4), FakeRemote("B", build=4)]
    fake = FakeRemotes(*rs)
    s = sender(fake, package)
    monkeypatch.setattr(proxy_module, "update_sender", s)
    r = await client.post("/config/other-pcs/courier/update", json={"id": "A", "anyway": True})
    assert r.status_code == 200 and r.json()["status"] == "started" and r.json()["to"] == NEW
    await asyncio.wait_for(s.jobs["A"].task, 10)
    assert rs[0].client.calls[0] == "begin"
    r = await client.post("/config/other-pcs/courier/update/all", json={})
    assert r.json() == {"status": "started", "count": 1}
    await asyncio.wait_for(s._all_task, 10)
    assert (await client.post("/config/other-pcs/courier/update/cancel", json={"id": "A"})).json() == {"status": "ok"}
    bad = await client.post("/config/other-pcs/courier/update", json={"id": "nobody"})
    assert bad.status_code == 404
    package.write_bytes(b"junk")
    rs[0].report = report(4)
    again = await client.post("/config/other-pcs/courier/update", json={"id": "A"})
    assert again.status_code == 409 and "No update file here." in again.text


async def test_nothing_secret_is_in_the_state_or_the_log(client, proxy_module, package, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="test-update-sender")
    r = FakeRemote("MSI", build=4)
    fake = FakeRemotes(r)
    s = sender(fake, package)
    s.start("MSI")
    await finish(s, "MSI")
    text = str(s.status()) + "\n".join(caplog.messages)
    assert str(package.parent) not in text and "carried" not in text and "code" not in " ".join(m for m in caplog.messages if "code" in m.lower() and "build" not in m)


async def test_how_an_earlier_try_ended_is_not_taken_for_this_tries_result(package):
    """A busy refusal is recorded as the device's last_update; the main PC's cached report may not have it yet. The wait compares with a fresh report taken just before apply."""
    r = FakeRemote("MSI", build=4)
    earlier = {"code": "busy", "from": 4, "to": NEW, "time": 1}
    orig = r.client.fetch_report

    async def fetch():
        rep = await orig()
        return dict(rep, last_update=earlier) if rep else rep
    r.client.fetch_report = fetch                         # the device knows about the earlier try; r.report (the cache) does not
    s = sender(FakeRemotes(r), package)
    s.start("MSI")
    assert (await finish(s, "MSI")).state == "done"
