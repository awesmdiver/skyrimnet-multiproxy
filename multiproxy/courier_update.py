"""The Courier's side of a signed update (docs/security-review-update-courier-design.md M1 to M10, M12).

Four steps, each its own request from the paired main device, each behind the same guard (the pairing code, plus the exact-Host / no-Origin / no-browser-marks
checks in `gate`):

  POST /courier/v1/update/begin   {size, sha256, build}     -> starts the byte transfer (file_transfer kind "courier-update": one fixed file, at most 64 MB)
  PUT  /courier/v1/transfers/<id>?offset=N ... POST .../finish                                 (the shared resumable transfer; checks the SHA-256 for damage only)
  POST /courier/v1/update/stage   {build}                   -> verifies the package (update_verify: a signature a key we carry made, the SIGNED build above ours, ...)
                                                              and unpacks it, member by member, into <install>/update/staging/<build>/
  POST /courier/v1/update/apply   {build, anyway}           -> refuses when this device is busy (unless `anyway`), then starts the helper (courier_updater.py, from the
                                                              staged copy), tells the main device it is installing, and exits so the helper can swap the files

The Courier accepts BYTES over the paired channel and verifies them; no request names a path or a URL. Nothing here runs a setup script, pip or an installer.
What crosses the wire is a code from update_verify.WIRE_CODES; the device's own log has one line per attempt (no keys, no paths).
"""
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

import courier_keys
import courier_updater as upd
import file_transfer
import other_pcs
import update_verify as uv

MAX_ATTEMPTS_PER_HOUR = 3
ATTEMPT_WINDOW_SECONDS = 3600.0
STATE_EXPIRES_SECONDS = 1800.0            # an update that was begun and never finished stops blocking the next one after this long
STAGED_KEEP_SECONDS = 3600.0
SPACE_MARGIN_BYTES = 64 * 1024 ** 2

# What the API says for each wire code (the main device shows its own short line for each; these are for the device's own side and for a person reading the answer).
MESSAGES = {
    "bad_signature": "The update file isn't trusted.",
    "not_newer": "This Courier is already on that build or newer.",
    "bad_package": "The update file is damaged or isn't a Courier update.",
    "busy": "This device is busy.",
    "no_space": "There isn't enough disk space for the update.",
    "runtime_changed": "This update needs a manual install.",
    "update_failed": "The update didn't go through.",
    "rolled_back": "The update didn't start, so the old Courier was put back.",
}
STATUS = {"bad_signature": 422, "not_newer": 409, "bad_package": 422, "busy": 409, "no_space": 507, "runtime_changed": 409, "update_failed": 409, "rolled_back": 409}


def error_response(code: str, status: Optional[int] = None) -> web.Response:
    return web.json_response({"error": {"message": MESSAGES.get(code, MESSAGES["update_failed"]), "type": "update_error", "code": code}}, status=status or STATUS.get(code, 409))


def split_host(host: str) -> tuple:
    """A Host header as (name, port): "192.168.1.5:8110", "[::1]:8110", "pc-name:8110" (the port is "" when there is none)."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        rest = host[end + 1:] if end > 0 else ""
        return (host[1:end] if end > 0 else host), (rest[1:] if rest.startswith(":") else "")
    name, _, port = host.partition(":")
    return name, port


def spawn_helper(argv: list, systemd: bool) -> None:
    """Start the helper so that it outlives this Courier (and its service): detached on Windows, a transient systemd unit (or a new session) on Linux. Raises OSError when it cannot."""
    kw = {"cwd": str(Path(argv[2]).resolve().parents[3]), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(argv, creationflags=flags | 0x01000000, **kw)          # | CREATE_BREAKAWAY_FROM_JOB
        except PermissionError:
            subprocess.Popen(argv, creationflags=flags, **kw)                          # a job that forbids breakaway: the helper may then end with this Courier
        return
    if systemd:
        # Stopping the service ends every process in its group, so the helper must not be in it. If this cannot be arranged, the update does not start.
        run = shutil.which("systemd-run")
        if not run:
            raise OSError("systemd-run is not available")
        subprocess.run([run, "--user", "--collect", "--quiet", *argv], check=True, capture_output=True, timeout=30, cwd=kw["cwd"])
        return
    subprocess.Popen(argv, start_new_session=True, **kw)


class UpdateManager:
    def __init__(self, courier, install_dir: Path, build: int, log: Optional[logging.Logger] = None, now: Callable[[], float] = time.time,
                 spawn: Optional[Callable[[list, bool], None]] = None, run_args: Optional[list] = None):
        self.courier = courier
        self.install = Path(install_dir)
        self.update_dir = self.install / "update"
        self.build = build
        self.log = log or logging.getLogger("courier")
        self.now = now
        self.spawn = spawn or spawn_helper
        self.run_args = list(sys.argv[1:] if run_args is None else run_args)
        self.state = "idle"                        # idle | receiving | staged | applying
        self.state_since = 0.0
        self.announced: Optional[dict] = None      # {"build", "sha256"} of the update being received
        self._attempts: deque = deque()
        self._stage_busy = False

    # ---- small things -----------------------------------------------------------------------------------------------
    @property
    def tid(self) -> str:
        return f"{file_transfer.UPDATE_KIND}__{file_transfer.UPDATE_FILE}"

    def owns(self, tid: str) -> bool:
        return tid == self.tid

    def resolve_dest(self, spec: dict) -> Path:
        d = self.update_dir / "incoming"
        d.mkdir(parents=True, exist_ok=True)
        return d / file_transfer.UPDATE_FILE

    def _set(self, state: str) -> None:
        self.state, self.state_since = state, self.now()

    def _expire(self) -> None:
        if self.state != "idle" and self.now() - self.state_since > STATE_EXPIRES_SECONDS:
            self._set("idle")

    @staticmethod
    def who(request: web.Request) -> str:
        name = re.sub(r"[\x00-\x1f\x7f]", "", request.headers.get("X-Courier-Client", "") or "").strip()[:60].replace('"', "'")          # (no control character reaches the log)
        return f"{request.remote or '?'}" + (f' (client "{name}")' if name else "")

    def note(self, who: str, to_build, result: str, code: str = "", record: bool = True) -> None:
        """One log line for the attempt, and (for a refusal) the record the report's `last_update` is made from. No keys, no paths."""
        shown = result if not code else f"{result} ({code})"
        self.log.info(f"update: from 1.{self.build} to {'1.%s' % to_build if to_build is not None else '?'}, asked by {who}, result: {shown}")
        if record:
            self._write_last(code or "update_failed", to_build)

    def _write_last(self, code: str, to_build) -> None:
        try:
            self.update_dir.mkdir(parents=True, exist_ok=True)
            upd.write_json(self.update_dir / upd.LAST_NAME, {"code": code, "from": self.build, "to": to_build, "time": int(self.now())})
        except OSError:
            pass

    def last_update(self) -> Optional[dict]:
        j = upd.read_json(self.update_dir / upd.LAST_NAME)
        if not j or not isinstance(j.get("time"), (int, float)):
            return None
        return {"code": str(j.get("code") or ""), "from": j.get("from"), "to": j.get("to"), "time": int(j["time"])}

    def report(self) -> dict:
        """The two fields the Courier's report carries: that this Courier can be updated from the dashboard, and how the last attempt ended."""
        return {"capabilities": {"update": True}, "last_update": self.last_update()}

    # ---- the guard ------------------------------------------------------------------------------------------------
    def _own_hosts(self) -> set:
        hosts = {"127.0.0.1", "localhost", "::1"}
        for a in getattr(self.courier, "addresses", []) or []:
            hosts.add(a.rsplit(":", 1)[0].strip("[]").lower())
        for n in (other_pcs.short_hostname(), socket.gethostname()):
            n = (n or "").lower()
            if n:
                hosts |= {n, n + ".local", n + ".lan"}
        return hosts

    def gate(self, request: web.Request, json_body: bool = True) -> Optional[web.Response]:
        """On top of the pairing code (checked before this): the Host must be one of this device's own addresses or names on its own port (a name an attacker's page can
        rebind does not qualify), a browser's marks (Origin, Sec-Fetch-*) are refused, and a request with a body says it is JSON. A refusal is one log line."""
        h = request.headers
        reason = ""
        name, port = split_host(h.get("Host") or "")
        if name not in self._own_hosts() or port != str(self.courier.settings.port):
            reason = "bad_host"
        elif "Origin" in h:
            reason = "origin"
        elif any(k.lower().startswith("sec-fetch-") for k in h.keys()):
            reason = "browser"
        elif json_body and request.method == "POST" and not (h.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"):
            reason = "not_json"
        if not reason:
            return None
        self.log.info(f"update: refused a request from {self.who(request)} ({reason})")
        return error_response("update_failed", 403)

    # ---- busy -------------------------------------------------------------------------------------------------------
    def busy_reason(self) -> str:
        """Why this device should not restart right now, from its own state (never from what the sender says); empty when it can."""
        c = self.courier
        if any(i.state in ("receiving", "verifying") and i.kind != file_transfer.UPDATE_KIND for i in c.transfers.items.values()):
            return "a file is being copied here"
        if any(getattr(a, "inflight", 0) > 0 for a in c.adapters):
            return "a chat request is running"
        if getattr(c, "_speaking", 0) > 0:
            return "a voice line is being made"
        if c.active():
            return "a model is loading or downloading"
        for eng in (getattr(c.voice, "engine", None), getattr(c.decisions, "engine", None)):
            job = getattr(eng, "job", None)
            if job is not None and getattr(job, "state", "") == "running":
                return "an install is running"
        return ""

    # ---- begin ------------------------------------------------------------------------------------------------------
    def _rate_ok(self) -> bool:
        now = self.now()
        while self._attempts and now - self._attempts[0] > ATTEMPT_WINDOW_SECONDS:
            self._attempts.popleft()
        return len(self._attempts) < MAX_ATTEMPTS_PER_HOUR

    async def begin(self, request: web.Request) -> web.Response:
        who = self.who(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self.note(who, None, "refused", "bad_package")
            return error_response("bad_package", 400)
        build, sha, size = body.get("build"), str(body.get("sha256") or "").lower(), body.get("size")
        if isinstance(build, bool) or not isinstance(build, int) or isinstance(size, bool) or not isinstance(size, int) or size <= 0 or len(sha) != 64:
            self.note(who, None, "refused", "bad_package")
            return error_response("bad_package", 400)
        if size > file_transfer.MAX_UPDATE_BYTES:
            self.note(who, build, "refused", "bad_package")
            return error_response("bad_package", 413)
        if build <= self.build:                                  # the real check is on the SIGNED build at `stage`; this just spares a pointless copy
            self.note(who, build, "refused", "not_newer")
            return error_response("not_newer")
        self._expire()
        resume = self.state == "receiving" and self.announced == {"build": build, "sha256": sha}
        if self.state == "applying" or (self.state == "receiving" and not resume):          # one update at a time
            self.note(who, build, "refused", "update_failed")
            return error_response("update_failed")
        if not resume:
            if not self._rate_ok():
                self.note(who, build, "refused", "too_many_tries")
                return error_response("update_failed", 429)
            self._attempts.append(self.now())
            self._clear_old_files()
        try:
            self.update_dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.update_dir).free < size * 4 + SPACE_MARGIN_BYTES:
                self.note(who, build, "refused", "no_space")
                return error_response("no_space")
            res = self.courier.transfers.begin({"kind": file_transfer.UPDATE_KIND, "size": size, "sha256": sha})
        except file_transfer.TransferError as e:
            code = "no_space" if e.status == 507 else "bad_package" if e.status in (400, 413) else "update_failed"
            self.note(who, build, "refused", code)
            return error_response(code, e.status if e.status in (400, 413, 507) else None)
        except OSError:
            self.note(who, build, "refused", "update_failed")
            return error_response("update_failed")
        self.announced = {"build": build, "sha256": sha}
        self._set("receiving")
        return web.json_response(res)

    def _clear_old_files(self) -> None:
        """A fresh attempt starts from nothing: an earlier package (or a half of one) is never reused, and an earlier staged update is dropped."""
        self.courier.transfers.items.pop(self.tid, None)
        for name in (file_transfer.UPDATE_FILE, file_transfer.UPDATE_FILE + ".partial", file_transfer.UPDATE_FILE + ".partial.json"):
            try:
                (self.update_dir / "incoming" / name).unlink(missing_ok=True)
            except OSError:
                pass
        j = upd.read_journal(self.update_dir)
        if j and j.get("state") == "staged":
            shutil.rmtree(self.update_dir / "staging", ignore_errors=True)
            (self.update_dir / upd.JOURNAL_NAME).unlink(missing_ok=True)

    # ---- stage ------------------------------------------------------------------------------------------------------
    def local_runtime_id(self) -> str:
        return uv.runtime_id((self.install / uv.REQUIREMENTS_FILE).read_bytes())

    def _stage_blocking(self, package: Path, announced_build: int) -> int:
        """Verify and unpack. Raises uv.UpdateRejected(code) (internal or wire code). Runs in a thread."""
        keys = courier_keys.public_keys()
        manifest, members, raw_manifest, raw_sig = uv.verify_package_raw(package, keys=keys, running_build=self.build)
        if manifest["build"] != announced_build:
            raise uv.UpdateRejected("wrong_build", "the signed build is not the announced one")
        try:
            mine = self.local_runtime_id()
        except OSError:
            raise uv.UpdateRejected("update_failed", "cannot read the local requirements")
        if manifest["runtime_id"] != mine:
            raise uv.UpdateRejected("runtime_changed", "the package needs a different runtime")
        needed = sum(len(d) for d in members.values())
        self.update_dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.update_dir).free < needed * 3 + SPACE_MARGIN_BYTES:
            raise uv.UpdateRejected("no_space", "not enough room to stage")
        staging = self.update_dir / "staging"
        shutil.rmtree(staging, ignore_errors=True)
        here = staging / str(manifest["build"])
        here.mkdir(parents=True)
        for name, data in members.items():             # by the manifest's own (validated, flat) names, one at a time; nothing is extracted by the zip's own paths
            (here / name).write_bytes(data)
        (here / uv.MANIFEST_NAME).write_bytes(raw_manifest)
        (here / uv.SIGNATURE_NAME).write_bytes(raw_sig)
        systemd = sys.platform != "win32" and bool(os.environ.get("INVOCATION_ID"))
        upd.write_journal(self.update_dir, {
            "state": "staged", "from_build": self.build, "to_build": manifest["build"], "version": manifest["version"],
            "keys": [base64.b64encode(k).decode("ascii") for k in keys], "settings_file": str(self.courier.settings.path), "run_args": self.run_args,
            "manager": "systemd" if systemd else "direct", "staged_at": self.now()})
        return manifest["build"]

    async def stage(self, request: web.Request) -> web.Response:
        who = self.who(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        build = body.get("build") if isinstance(body, dict) else None
        if isinstance(build, bool) or not isinstance(build, int):
            self.note(who, None, "refused", "bad_package")
            return error_response("bad_package", 400)
        inc = self.courier.transfers.items.get(self.tid)
        if self._stage_busy or self.state != "receiving" or not inc or inc.state != "done" or not self.announced or self.announced["build"] != build:
            self.note(who, build, "refused", "update_failed")
            return error_response("update_failed")
        self._stage_busy = True
        try:
            to_build = await asyncio.to_thread(self._stage_blocking, inc.dest, build)
        except uv.UpdateRejected as e:
            code = uv.to_wire(e.code)
            self.log.info(f"update: the package from {who} did not pass its check ({e.code})")
            shutil.rmtree(self.update_dir / "staging", ignore_errors=True)
            self._set("idle")
            self.note(who, build, "refused", code)
            return error_response(code)
        except OSError:
            self._set("idle")
            self.note(who, build, "refused", "update_failed")
            return error_response("update_failed")
        finally:
            self._stage_busy = False
        try:
            inc.dest.unlink(missing_ok=True)
        except OSError:
            pass
        self.courier.transfers.items.pop(self.tid, None)
        self._set("staged")
        return web.json_response({"state": "staged", "build": to_build})

    # ---- apply ------------------------------------------------------------------------------------------------------
    async def apply(self, request: web.Request) -> web.Response:
        who = self.who(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        build = body.get("build") if isinstance(body, dict) else None
        anyway = isinstance(body, dict) and body.get("anyway") is True
        j = upd.read_journal(self.update_dir)
        helper = self.update_dir / "staging" / str(build) / "courier_updater.py"
        if isinstance(build, bool) or not isinstance(build, int) or self.state != "staged" or not j or j.get("state") != "staged" or j.get("to_build") != build or not helper.is_file():
            self.note(who, build if isinstance(build, int) else None, "refused", "update_failed")
            return error_response("update_failed")
        why = self.busy_reason()
        if why and not anyway:
            self.note(who, build, "refused", "busy")
            return error_response("busy")
        argv = [sys.executable, "-I", str(helper), str(os.getpid())]
        try:
            self.spawn(argv, j.get("manager") == "systemd")
        except Exception:          # nothing was stopped yet: the Courier simply carries on as it was
            self.note(who, build, "refused", "update_failed")
            return error_response("update_failed")
        self._set("applying")
        self.note(who, build, "started: the helper is installing it" + (" (Update anyway was chosen)" if why and anyway else ""), record=False)
        loop = asyncio.get_running_loop()
        loop.call_later(1.0, self.courier.exit_for_update)
        return web.json_response({"state": "installing", "build": build})

    async def cancel(self, request: web.Request) -> web.Response:
        """Drop an update that was begun or staged and not applied."""
        if self.state in ("receiving", "staged"):
            self._clear_old_files()
            shutil.rmtree(self.update_dir / "staging", ignore_errors=True)
            self._set("idle")
        return web.json_response({"ok": True})

    def _drop_settings_copy(self) -> None:
        """The copy of courier.json (it holds the pairing code) the helper kept for a rollback is not needed once the update is settled, and must not outlive "Make a new pairing code"."""
        try:
            (self.update_dir / "previous" / upd.SETTINGS_FILE_NAME).unlink(missing_ok=True)
        except OSError:
            pass

    # ---- after a start ----------------------------------------------------------------------------------------------
    def _answers_own_report(self) -> bool:
        addrs = list(getattr(self.courier, "addresses", []) or [])
        if not addrs:
            return False
        req = urllib.request.Request(f"http://{addrs[0]}/courier/v1/report", headers={"Authorization": f"Bearer {self.courier.settings.code}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            return int(((data or {}).get("courier") or {}).get("build")) == self.build
        except (OSError, ValueError, TypeError):
            return False

    async def after_start(self) -> None:
        """Called once the server is listening. A start that follows an update: the new Courier answers its own report and only THEN writes "confirmed" (the helper rolls back
        if it never does). A start that follows a rollback: say so once. Then clean up what is no longer needed."""
        j = upd.read_journal(self.update_dir)
        if not j:
            return
        state = j.get("state")
        if state in ("started", "replacing") and j.get("to_build") == self.build:
            if await asyncio.to_thread(self._answers_own_report):
                j = upd.read_journal(self.update_dir) or j
                if j.get("state") in ("started", "replacing"):          # (not already rolled back by a helper that ran out of patience)
                    j.update(state="confirmed", result="ok", logged=True, finished_at=self.now())
                    upd.write_journal(self.update_dir, j)
                    self._drop_settings_copy()
                    self.log.info(f"update: from 1.{j.get('from_build')} to 1.{self.build}, result: ok")
                    self._write_last("ok", self.build)
            return
        if state in ("rolled_back", "failed") and not j.get("logged"):
            code = "rolled_back" if state == "rolled_back" else uv.to_wire(str(j.get("reason") or ""))
            self.log.info(f"update: from 1.{j.get('from_build')} to 1.{j.get('to_build')}, result: {code} ({j.get('reason') or 'no reason given'})")
            self._write_last(code, j.get("to_build"))
            j["logged"] = True
            upd.write_journal(self.update_dir, j)
        if state in ("confirmed", "rolled_back"):
            self._drop_settings_copy()
        if state in ("confirmed", "rolled_back", "failed") or (state == "staged" and self.now() - float(j.get("staged_at") or 0) > STAGED_KEEP_SECONDS):
            shutil.rmtree(self.update_dir / "staging", ignore_errors=True)
            shutil.rmtree(self.update_dir / "incoming", ignore_errors=True)
