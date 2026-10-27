"""The Courier's side of a signed update (courier_update.py + the routes in courier.py): the guard, the bytes, the verification, the busy check and the hand-over.

A real Courier answers real HTTP on 127.0.0.1 (a spare port, a scratch settings folder and a scratch install folder; the real Courier's folders are never touched). Packages
are made here and signed with a throwaway key. Nothing is ever actually installed: the helper is replaced by a recorder (the helper's own behaviour is tested in
test_courier_updater.py, including a real helper process against a stand-in Courier).
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

import courier as cr
import courier_keys
import courier_update as cu
import courier_updater as upd
import file_transfer as ft
import update_verify as uv
from tests.fixtures import update_pkg as pk
from tests.test_courier import env  # noqa: F401

pytestmark = pytest.mark.asyncio
BUILD = cr.COURIER_BUILD          # the running build
NEW = BUILD + 1


class Dev:
    def __init__(self, env, install, spawned):
        self.env, self.install, self.spawned = env, install, spawned

    @property
    def mgr(self):
        return self.env.courier.update

    async def req(self, method, path, body=None, headers=None, data=None, auth=True):
        h = dict(self.env.headers if auth else {})
        h.update(headers or {})
        async with aiohttp.ClientSession() as s:
            async with s.request(method, self.env.base + path, json=body, data=data, headers=h) as r:
                text = await r.text()
                try:
                    return r.status, json.loads(text)
                except ValueError:
                    return r.status, {"text": text}

    async def begin(self, pkg: bytes, build=NEW, **kw):
        return await self.req("POST", "/courier/v1/update/begin", {"build": build, "size": len(pkg), "sha256": hashlib.sha256(pkg).hexdigest()}, **kw)

    async def put(self, pkg: bytes, offset=0, headers=None):
        return await self.req("PUT", f"/courier/v1/transfers/{self.mgr.tid}?offset={offset}", data=pkg[offset:], headers=headers)

    async def finish(self, headers=None):
        return await self.req("POST", f"/courier/v1/transfers/{self.mgr.tid}/finish", headers=headers)

    async def send(self, pkg: bytes, build=NEW):
        st, res = await self.begin(pkg, build)
        assert st == 200, res
        st, res = await self.put(pkg, res["offset"])
        assert st == 200, res
        return await self.finish()

    async def stage(self, build=NEW):
        return await self.req("POST", "/courier/v1/update/stage", {"build": build})

    async def apply(self, build=NEW, **extra):
        return await self.req("POST", "/courier/v1/update/apply", {"build": build, **extra})

    async def staged(self, pkg=None, build=NEW):
        pkg = pkg or pk.package_bytes(build)
        st, res = await self.send(pkg, build)
        assert st == 200, res
        st, res = await self.stage(build)
        assert st == 200, res
        return res


@pytest_asyncio.fixture
async def dev(env, tmp_path, monkeypatch):  # noqa: F811
    install = pk.make_install(tmp_path / "install", build=BUILD)
    spawned = []
    mgr = cu.UpdateManager(env.courier, install, BUILD, env.courier.log, spawn=lambda argv, systemd: spawned.append((argv, systemd)), run_args=["run", "--no-tray"])
    env.courier.update = mgr
    env.courier.transfers.resolve_update = mgr.resolve_dest
    env.courier._stop_event = asyncio.Event()
    monkeypatch.setattr(courier_keys, "CURRENT", pk.PUBLIC_B64)
    monkeypatch.setattr(courier_keys, "NEXT", "")
    return Dev(env, install, spawned)


def swap_manifest(blob: bytes, change) -> bytes:
    """The same package with its manifest changed AFTER it was signed."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as zin, zipfile.ZipFile(out, "w") as zout:
        for i in zin.infolist():
            data = zin.read(i)
            zout.writestr(i.filename, change(data) if i.filename == "manifest.json" else data)
    return out.getvalue()


def codes(res):
    return (res.get("error") or {}).get("code")


# ---- the report -------------------------------------------------------------------------------------------------------------------------------------
async def test_the_report_says_this_courier_can_be_updated_and_how_the_last_try_ended(dev):
    st, rep = await dev.req("GET", "/courier/v1/report?fresh=1")
    assert rep["capabilities"]["update"] is True and rep["last_update"] is None and rep["courier"]["build"] == BUILD
    await dev.begin(pk.package_bytes(NEW), build=BUILD)                  # a refusal is an attempt too
    st, rep = await dev.req("GET", "/courier/v1/report?fresh=1")
    assert rep["last_update"]["code"] == "not_newer" and rep["last_update"]["from"] == BUILD and isinstance(rep["last_update"]["time"], int)


async def test_this_build_is_the_first_one_that_can_update(dev):
    assert BUILD >= 6


# ---- the guard --------------------------------------------------------------------------------------------------------------------------------------
async def test_the_update_routes_need_the_pairing_code(dev):
    pkg = pk.package_bytes(NEW)
    body = {"build": NEW, "size": len(pkg), "sha256": hashlib.sha256(pkg).hexdigest()}
    for path in ("begin", "stage", "apply", "cancel"):
        st, _ = await dev.req("POST", f"/courier/v1/update/{path}", body, auth=False)
        assert st == 403
        st, _ = await dev.req("POST", f"/courier/v1/update/{path}", body, headers={"Authorization": "Bearer WRONG-CODE"}, auth=False)
        assert st == 403
    assert dev.mgr.state == "idle" and not (dev.install / "update").exists()


@pytest.mark.parametrize("header,value,why", [
    ("Host", "evil.example:{port}", "bad_host"), ("Host", "127.0.0.1:1", "bad_host"), ("Host", "127.0.0.1", "bad_host"),
    ("Origin", "http://evil.example", "origin"), ("Sec-Fetch-Site", "cross-site", "browser"), ("Sec-Fetch-Mode", "cors", "browser"),
    ("Content-Type", "text/plain", "not_json")])
async def test_a_request_that_does_not_look_like_the_main_device_is_refused_and_nothing_starts(dev, caplog, header, value, why):
    caplog.set_level(logging.INFO, logger="test-courier")
    pkg = pk.package_bytes(NEW)
    body = {"build": NEW, "size": len(pkg), "sha256": hashlib.sha256(pkg).hexdigest()}
    async with aiohttp.ClientSession() as s:
        h = dict(dev.env.headers)
        h[header] = value.format(port=dev.env.courier.settings.port)
        data = json.dumps(body) if header == "Content-Type" else None
        async with s.post(dev.env.base + "/courier/v1/update/begin", json=None if data else body, data=data, headers=h) as r:
            assert r.status == 403 and codes(await r.json()) == "update_failed"
    assert dev.mgr.state == "idle"
    lines = [m for m in caplog.messages if "refused a request" in m]
    assert len(lines) == 1 and why in lines[0], lines


async def test_only_post_can_start_anything(dev):
    for path in ("begin", "stage", "apply", "cancel"):
        for method in ("GET", "PUT", "DELETE"):
            st, _ = await dev.req(method, f"/courier/v1/update/{path}")
            assert st == 405, (method, path)


async def test_the_update_bytes_cannot_be_started_by_the_general_transfer_route_and_a_chunk_from_a_web_page_is_refused(dev):
    st, res = await dev.req("POST", "/courier/v1/transfers", {"kind": "courier-update", "size": 10, "sha256": "d" * 64, "model": "x", "file": "x"})
    assert st == 403 and codes(res) == "update_failed" and not (dev.install / "update").exists()
    pkg = pk.package_bytes(NEW)
    assert (await dev.begin(pkg))[0] == 200
    st, _ = await dev.put(pkg, headers={"Origin": "http://evil.example"})
    assert st == 403
    st, _ = await dev.finish(headers={"Sec-Fetch-Site": "cross-site"})
    assert st == 403


async def test_no_request_can_name_a_path_or_a_web_address(dev):
    pkg = pk.package_bytes(NEW)
    body = {"build": NEW, "size": len(pkg), "sha256": hashlib.sha256(pkg).hexdigest(), "path": str(dev.install.parent / "evil.zip"), "file": "../../evil.zip", "url": "http://evil.example/x.zip"}
    st, res = await dev.req("POST", "/courier/v1/update/begin", body)
    assert st == 200
    await dev.put(pkg)
    await dev.finish()
    assert (dev.install / "update" / "incoming" / "courier-update.zip").is_file()
    assert not (dev.install.parent / "evil.zip").exists()
    assert {p.name for p in (dev.install / "update").iterdir()} == {"incoming"}


# ---- the happy path ---------------------------------------------------------------------------------------------------------------------------------
async def test_a_signed_newer_package_is_received_checked_staged_and_handed_to_the_helper(dev, caplog):
    caplog.set_level(logging.INFO, logger="test-courier")
    settings = dev.env.courier.settings.path
    before = settings.read_bytes()
    tree = {p for p in dev.install.parent.rglob("*")}
    installed_before = pk.snapshot(dev.install)
    res = await dev.staged()
    assert res == {"state": "staged", "build": NEW}
    here = dev.install / "update" / "staging" / str(NEW)
    assert {p.name for p in here.iterdir()} == set(pk.files_for(NEW)) | {"manifest.json", "manifest.sig"}
    assert (here / "courier.py").read_bytes() == pk.courier_py(NEW)
    j = upd.read_journal(dev.install / "update")
    assert j["state"] == "staged" and j["to_build"] == NEW and j["from_build"] == BUILD and j["keys"] == [pk.PUBLIC_B64] and j["run_args"] == ["run", "--no-tray"]
    assert j["settings_file"] == str(settings)
    assert not (dev.install / "update" / "incoming" / "courier-update.zip").exists(), "the zip is not kept once it is staged"
    assert dev.spawned == [] and not dev.env.courier._stop_event.is_set(), "nothing is installed by staging"
    assert pk.snapshot(dev.install) == installed_before, "the installed program files are untouched"

    st, res = await dev.apply()
    assert st == 200 and res == {"state": "installing", "build": NEW}
    (argv, systemd), = dev.spawned
    assert argv == [sys.executable, "-I", str(here / "courier_updater.py"), str(os.getpid())] and systemd is False, "an argument list, run from the staged copy"
    assert dev.mgr.state == "applying"
    await asyncio.sleep(1.4)
    assert dev.env.courier._stop_event.is_set(), "the Courier lets go so the files can be swapped"

    assert settings.read_bytes() == before, "the pairing code and the settings are untouched"
    new = {p for p in dev.install.parent.rglob("*")} - tree
    assert all(dev.install / "update" in p.parents or p == dev.install / "update" for p in new), "everything new is inside the Courier's own update folder"
    text = "\n".join(caplog.messages)
    assert dev.env.courier.settings.code not in text and str(dev.install.parent) not in text, "no pairing code and no path in the log"
    attempt_lines = [m for m in caplog.messages if m.startswith("update:")]
    assert len(attempt_lines) == 1 and f"from 1.{BUILD} to 1.{NEW}" in attempt_lines[0] and "started" in attempt_lines[0], attempt_lines


async def test_a_dropped_connection_carries_on_where_it_stopped(dev):
    pkg = pk.package_bytes(NEW, files=pk.files_for(NEW, **{"other_pcs.py": os.urandom(40000)}))
    st, res = await dev.begin(pkg)
    half = len(pkg) // 2
    st, _ = await dev.req("PUT", f"/courier/v1/transfers/{dev.mgr.tid}?offset=0", data=pkg[:half])
    st, res = await dev.begin(pkg)
    assert st == 200 and res["offset"] == half and res["state"] == "receiving", "the same file is picked up where it was"
    await dev.put(pkg, half)
    assert (await dev.finish())[0] == 200
    assert (await dev.stage())[0] == 200


# ---- what must be refused ---------------------------------------------------------------------------------------------------------------------------
async def test_a_build_that_is_not_newer_is_refused_at_the_start_and_again_on_the_signed_number(dev):
    for b in (BUILD, BUILD - 1):
        st, res = await dev.begin(pk.package_bytes(b), build=b)
        assert st == 409 and codes(res) == "not_newer"
    pkg = pk.package_bytes(BUILD)                      # the sender claims a newer build than the package is signed for
    assert (await dev.send(pkg, build=NEW))[0] == 200
    st, res = await dev.stage(NEW)
    assert st == 409 and codes(res) == "not_newer"
    assert not (dev.install / "update" / "staging").exists() and dev.mgr.state == "idle"


async def test_the_announced_build_must_be_the_signed_one(dev):
    pkg = pk.package_bytes(NEW + 1)
    assert (await dev.send(pkg, build=NEW))[0] == 200
    st, res = await dev.stage(NEW)
    assert st == 422 and codes(res) == "bad_package"


@pytest.mark.parametrize("why,make,code", [
    ("signed by another key", lambda: pk.package_bytes(NEW, seed=pk.OTHER_SEED), "bad_signature"),
    ("a changed manifest", lambda: swap_manifest(pk.package_bytes(NEW), lambda m: m.replace(b'"build": %d' % NEW, b'"build": %d' % (NEW + 1))), "bad_signature"),
    ("a changed file", lambda: pk.package_bytes(NEW, members=pk.files_for(NEW, **{"other_pcs.py": b"import os\n"})), "bad_package"),
    ("an extra file", lambda: pk.package_bytes(NEW, members=pk.files_for(NEW, **{"evil.py": b"x"})), "bad_package"),
    ("a climbing name", lambda: pk.package_bytes(NEW, members=pk.files_for(NEW, **{"../evil.py": b"x"})), "bad_package"),
    ("a different runtime", lambda: pk.package_bytes(NEW, files=pk.files_for(NEW, **{"requirements-courier.txt": b"aiohttp>=3.9.0\nnumpy\n"})), "runtime_changed"),
    ("not a zip", lambda: b"this is not a zip file at all", "bad_package"),
])
async def test_a_package_that_is_wrong_is_refused_with_its_code_and_nothing_is_left_staged(dev, why, make, code):
    pkg = make()
    st, res = await dev.send(pkg)
    assert st == 200, res
    st, res = await dev.stage()
    assert codes(res) == code, (why, res)
    assert not (dev.install / "update" / "staging").exists() and dev.mgr.state == "idle", why
    assert (await dev.apply())[0] == 409, "and there is nothing to apply"
    assert dev.spawned == []


async def test_a_link_in_the_package_is_refused(dev):
    files = pk.files_for(NEW)
    m = pk.manifest_bytes(files, NEW)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", m)
        z.writestr("manifest.sig", __import__("base64").b64encode(uv.ed25519_sign(pk.SEED, m)))
        for n, d in files.items():
            z.writestr(n, d)
        link = zipfile.ZipInfo("evil.py")
        link.external_attr = 0o120777 << 16
        z.writestr(link, "courier.py")
    await dev.send(buf.getvalue())
    st, res = await dev.stage()
    assert codes(res) == "bad_package"


async def test_a_copy_that_arrives_damaged_is_thrown_away_by_the_shared_transfer(dev):
    pkg = pk.package_bytes(NEW)
    st, res = await dev.begin(pkg)
    st, _ = await dev.put(pkg[:-1] + bytes([pkg[-1] ^ 1]))
    st, res = await dev.finish()
    assert st == 422
    assert (await dev.stage())[0] == 409


async def test_a_package_over_the_size_cap_is_refused_before_any_byte_moves(dev):
    st, res = await dev.req("POST", "/courier/v1/update/begin", {"build": NEW, "size": ft.MAX_UPDATE_BYTES + 1, "sha256": "a" * 64})
    assert st == 413 and codes(res) == "bad_package"
    st, res = await dev.req("POST", "/courier/v1/update/begin", {"build": NEW, "size": "10", "sha256": "a" * 64})
    assert st == 400
    st, res = await dev.req("POST", "/courier/v1/update/begin", {"build": True, "size": 10, "sha256": "a" * 64})
    assert st == 400 and not (dev.install / "update" / "incoming").exists() and not (dev.install / "update" / "staging").exists()


async def test_no_room_is_said_before_anything_is_copied(dev, monkeypatch):
    monkeypatch.setattr(cu.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    st, res = await dev.begin(pk.package_bytes(NEW))
    assert st == 507 and codes(res) == "no_space"
    assert pk.snapshot(dev.install)["courier.py"] == pk.courier_py(BUILD), "the working Courier is not touched"


async def test_a_second_update_while_one_runs_is_refused_but_the_same_one_may_resume(dev):
    a, b = pk.package_bytes(NEW), pk.package_bytes(NEW + 1)
    assert (await dev.begin(a))[0] == 200
    st, res = await dev.begin(b, build=NEW + 1)
    assert st == 409 and codes(res) == "update_failed", "one update at a time"
    assert (await dev.begin(a))[0] == 200, "the same package resumes"
    await dev.put(a)
    await dev.finish()
    assert (await dev.stage())[0] == 200
    assert (await dev.apply())[0] == 200
    st, res = await dev.begin(b, build=NEW + 1)
    assert st == 409 and codes(res) == "update_failed", "and nothing new starts while the helper is installing"


async def test_two_stage_requests_at_once_stage_it_once(dev):
    pkg = pk.package_bytes(NEW)
    await dev.send(pkg)
    results = await asyncio.gather(dev.stage(), dev.stage())
    assert sorted(st for st, _ in results) == [200, 409]


async def test_only_three_fresh_attempts_an_hour(dev):
    for i in range(3):
        st, res = await dev.begin(pk.package_bytes(NEW + i, files=pk.files_for(NEW + i)), build=NEW + i)
        assert st == 200, res
        assert (await dev.req("POST", "/courier/v1/update/cancel", {}))[0] == 200
    st, res = await dev.begin(pk.package_bytes(NEW + 9), build=NEW + 9)
    assert st == 429 and codes(res) == "update_failed"
    dev.mgr.now = lambda: time.time() + 3700
    assert (await dev.begin(pk.package_bytes(NEW + 9), build=NEW + 9))[0] == 200


async def test_apply_needs_a_staged_package_for_that_exact_build(dev):
    assert (await dev.apply())[0] == 409
    await dev.staged()
    st, res = await dev.apply(build=NEW + 1)
    assert st == 409 and codes(res) == "update_failed"
    st, res = await dev.req("POST", "/courier/v1/update/apply", {"build": str(NEW)})
    assert st == 409 and dev.spawned == []


# ---- busy -------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make_busy", [
    lambda d: setattr(d.env.courier.adapters[0], "inflight", 1),
    lambda d: setattr(d.env.courier, "_speaking", 1),
    lambda d: d.env.courier.transfers.items.__setitem__("model__x.gguf", ft.Incoming("model__x.gguf", "model", "model", "x.gguf", Path("x"), 10, "a" * 64)),
], ids=["a chat request", "a voice line", "a model file being copied"])
async def test_a_busy_device_says_so_and_update_anyway_overrides_only_that(dev, make_busy):
    await dev.staged()
    make_busy(dev)
    st, res = await dev.apply()
    assert st == 409 and codes(res) == "busy" and dev.spawned == [] and not dev.env.courier._stop_event.is_set()
    st, res = await dev.apply(anyway="true")
    assert st == 409 and codes(res) == "busy", "only a real yes counts"
    st, res = await dev.apply(anyway=True)
    assert st == 200 and len(dev.spawned) == 1


async def test_update_anyway_does_not_skip_any_check(dev):
    pkg = pk.package_bytes(NEW, seed=pk.OTHER_SEED)
    await dev.send(pkg)
    dev.env.courier._speaking = 1
    st, res = await dev.stage()
    assert codes(res) == "bad_signature"
    st, res = await dev.apply(anyway=True)
    assert st == 409 and dev.spawned == []


async def test_when_the_helper_cannot_be_started_nothing_is_stopped(dev):
    await dev.staged()

    def boom(argv, systemd):
        raise OSError("no")
    dev.mgr.spawn = boom
    st, res = await dev.apply()
    assert st == 409 and codes(res) == "update_failed"
    await asyncio.sleep(1.3)
    assert not dev.env.courier._stop_event.is_set(), "this Courier carries on as it was"


async def test_cancel_drops_a_staged_update(dev):
    await dev.staged()
    assert (await dev.req("POST", "/courier/v1/update/cancel", {}))[0] == 200
    assert not (dev.install / "update" / "staging").exists() and (await dev.apply())[0] == 409


# ---- after a start ----------------------------------------------------------------------------------------------------------------------------------
async def test_the_new_courier_confirms_only_after_it_answers_its_own_report(dev, caplog):
    caplog.set_level(logging.INFO, logger="test-courier")
    update = dev.install / "update"
    update.mkdir(parents=True)
    upd.write_journal(update, {"state": "started", "from_build": BUILD - 1, "to_build": BUILD, "keys": []})
    await dev.mgr.after_start()
    j = upd.read_journal(update)
    assert j["state"] == "confirmed" and j["result"] == "ok"
    assert dev.mgr.last_update()["code"] == "ok"
    assert any(m.startswith("update: from 1.") and "result: ok" in m for m in caplog.messages)


async def test_a_start_that_is_not_the_new_build_confirms_nothing(dev):
    update = dev.install / "update"
    update.mkdir(parents=True)
    upd.write_journal(update, {"state": "started", "from_build": BUILD, "to_build": NEW, "keys": []})
    await dev.mgr.after_start()
    assert upd.read_journal(update)["state"] == "started"


async def test_a_start_that_follows_a_rollback_says_so_once(dev, caplog):
    caplog.set_level(logging.INFO, logger="test-courier")
    update = dev.install / "update"
    update.mkdir(parents=True)
    (update / "staging" / "7").mkdir(parents=True)
    upd.write_journal(update, {"state": "rolled_back", "result": "rolled_back", "reason": "no_confirm", "from_build": BUILD, "to_build": NEW})
    await dev.mgr.after_start()
    await dev.mgr.after_start()
    lines = [m for m in caplog.messages if "result: rolled_back" in m]
    assert len(lines) == 1 and "no_confirm" in lines[0]
    assert dev.mgr.last_update()["code"] == "rolled_back"
    st, rep = await dev.req("GET", "/courier/v1/report?fresh=1")
    assert rep["last_update"]["code"] == "rolled_back"
    assert not (update / "staging").exists(), "what is no longer needed is cleaned up"


async def test_a_failed_update_reports_its_wire_code(dev):
    update = dev.install / "update"
    update.mkdir(parents=True)
    upd.write_journal(update, {"state": "failed", "result": "update_failed", "reason": "damaged", "from_build": BUILD, "to_build": NEW})
    await dev.mgr.after_start()
    assert dev.mgr.last_update()["code"] == "bad_package"
    assert set(uv.WIRE_CODES) == {"bad_signature", "not_newer", "bad_package", "busy", "no_space", "runtime_changed", "update_failed", "rolled_back"}
    assert uv.to_wire("damaged") == "bad_package" and uv.to_wire("something odd") == "update_failed"


async def test_the_courier_py_has_the_recovery_before_its_first_sibling_import():
    text = (Path(__file__).resolve().parent.parent / "courier.py").read_text(encoding="utf-8")
    assert text.index("def _recover_unfinished_update") < text.index("import hidden_process") < text.index("import courier_update")
    head = text[:text.index("import hidden_process")]
    import re
    assert not re.findall(r"^import (claude_courier|courier_\w+|file_transfer|other_pcs|safe_paths)\b", head, flags=re.M)


# ---- security round two (2026-10-24): SF4 (the old pairing code does not stay behind), NH2 --------------------------------------------------------------
async def test_the_copy_of_the_settings_file_is_dropped_once_the_update_is_confirmed(dev):
    update = dev.install / "update"
    (update / "previous").mkdir(parents=True)
    (update / "previous" / "courier.json").write_text('{"code": "OLD-111"}', encoding="utf-8")
    (update / "previous" / "courier.py").write_text("# the old program file stays", encoding="utf-8")
    upd.write_journal(update, {"state": "started", "from_build": BUILD - 1, "to_build": BUILD, "keys": []})
    await dev.mgr.after_start()
    assert upd.read_journal(update)["state"] == "confirmed"
    assert not (update / "previous" / "courier.json").exists() and (update / "previous" / "courier.py").exists()


async def test_the_copy_of_the_settings_file_is_dropped_after_a_rollback_too(dev):
    update = dev.install / "update"
    (update / "previous").mkdir(parents=True)
    (update / "previous" / "courier.json").write_text('{"code": "OLD-111"}', encoding="utf-8")
    upd.write_journal(update, {"state": "rolled_back", "result": "rolled_back", "reason": "no_confirm", "from_build": BUILD, "to_build": NEW})
    await dev.mgr.after_start()
    assert not (update / "previous" / "courier.json").exists()


def test_a_client_name_with_control_characters_is_cleaned_before_it_is_logged():
    class Req:
        remote = "192.168.1.9"
        headers = {"X-Courier-Client": "GAMING\x1b[31m-PC\r\x00 x"}

        def get(self, k, d=None):
            return self.headers.get(k, d)
    shown = cu.UpdateManager.who(Req())
    assert "\x1b" not in shown and "\x00" not in shown and "\r" not in shown and "GAMING" in shown
