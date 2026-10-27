"""Sending a signed Courier update from the main device to a Courier device (docs/security-review-update-courier-design.md; the device's side is courier_update.py).

MultiProxy carries ONE signed Courier package (courier-update/MultiProxy-Courier-Update.zip next to the program, put there by the installer build). A file in a folder the person
can write is not trusted by where it is: this module checks the signature itself (update_verify, with the keys compiled into the program) before it says an update is available
and again right before it sends, and the device checks it a third time. A missing or unsigned file means no buttons.

One send is: begin -> the bytes (the shared resumable transfer) -> stage (the device verifies and unpacks) -> apply (the device checks it is not busy and hands over to its helper),
then a wait for the Courier to come back as the new build. The result is a job per device (`UpdateJob`) the page draws; it holds codes, never sentences. Failures are real reasons
only: the codes the device sends (update_verify.WIRE_CODES) plus `unreachable` (it stopped answering) and `no_package`.
"""
import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import file_transfer
import other_pcs
import update_verify as uv

PACKAGE_PATH = Path(__file__).resolve().parent / "courier-update" / "MultiProxy-Courier-Update.zip"
RETURN_WAIT_SECONDS = 30.0         # a Courier that was updated is normally back as the new build well within this
RETURN_GRACE_SECONDS = 120.0       # a device that has not answered yet may be rolling back (its helper waits up to 90 s): wait for it before saying "stopped answering"
RETURN_POLL_SECONDS = 2.0
SEND_RETRY_SECONDS = 120.0
MAIN_CODES = ("unreachable", "no_package")


@dataclass
class Package:
    ok: bool
    build: Optional[int] = None
    path: Optional[Path] = None
    size: int = 0
    sha256: str = ""
    reason: str = ""          # a code from update_verify.CODES, or "missing"

    def to_json(self) -> dict:
        return {"ok": self.ok, "build": self.build}


_cache: dict = {}


def load_package(path: Optional[Path] = None, keys: Optional[list] = None, force: bool = False) -> Package:
    """The carried package, fully checked (signature by a compiled-in key, manifest, every file). Remembered while the file is unchanged; `force` checks it again."""
    path = Path(path or PACKAGE_PATH)
    try:
        st = path.stat()
    except OSError:
        return Package(False, reason="missing")
    key = (str(path), st.st_size, st.st_mtime_ns, None if keys is None else tuple(keys))
    if not force and key in _cache:
        return _cache[key]
    try:
        blob = path.read_bytes()
        manifest, _ = uv.verify_package(blob, keys=keys)
        pkg = Package(True, manifest["build"], path, len(blob), hashlib.sha256(blob).hexdigest())
    except (OSError, uv.UpdateRejected) as e:
        pkg = Package(False, reason=getattr(e, "code", "damaged"))
    _cache.clear()
    _cache[key] = pkg
    return pkg


def device_can_update(report: Optional[dict]) -> bool:
    """Does this Courier's report say it can take an update from here (a Courier from before the updater has no such field)?"""
    return bool(((report or {}).get("capabilities") or {}).get("update") is True)


def version_of(build) -> str:
    """The number a person sees for a build: 1.<build>."""
    return other_pcs.courier_version_of(build)


def device_build(report: Optional[dict]) -> int:
    b = other_pcs.courier_build_of(report)
    return b if isinstance(b, int) else 0


@dataclass
class UpdateJob:
    pc: str
    label: str
    from_build: int
    to_build: int
    state: str = "sending"      # sending | installing | busy | failed | done | cancelled
    code: str = ""              # the reason, when state is failed
    done: int = 0
    total: int = 0
    speed: float = 0.0
    cancel: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    ended: float = 0.0

    def to_json(self) -> dict:
        return {"state": self.state, "code": self.code, "from": self.from_build, "to": self.to_build,
                "percent": int(self.done * 100 / self.total) if self.total else 0, "speed_bps": int(self.speed)}

    @property
    def active(self) -> bool:
        return self.state in ("sending", "installing")


class _Transport:
    """What file_transfer.send_file talks to: the begin goes to the update's own route (with its own guard), the rest is the shared transfer."""

    def __init__(self, client, build: int):
        self.client, self.build = client, build

    async def transfer_begin(self, spec: dict) -> dict:
        import courier_client
        try:
            return await self.client.update_call("/courier/v1/update/begin", {"build": self.build, "size": spec["size"], "sha256": spec["sha256"]})
        except courier_client.UpdateRefused as e:
            raise file_transfer.TransferError(e.status, e.code)

    async def transfer_chunk(self, tid, offset, data):
        return await self.client.transfer_chunk(tid, offset, data)

    async def transfer_finish(self, tid):
        return await self.client.transfer_finish(tid)

    async def transfer_cancel(self, tid):
        try:
            await self.client.update_call("/courier/v1/update/cancel", {})
        except Exception:
            pass


def code_of_transfer_error(e: "file_transfer.TransferError") -> str:
    if e.message in uv.WIRE_CODES:
        return e.message
    return {507: "no_space", 422: "bad_package", 413: "bad_package", 502: "unreachable"}.get(e.status, "update_failed")


class UpdateSender:
    def __init__(self, remote_svc, log: Optional[logging.Logger] = None, package_fn: Callable[..., Package] = load_package,
                 wait: float = RETURN_WAIT_SECONDS, grace: float = RETURN_GRACE_SECONDS, poll: float = RETURN_POLL_SECONDS):
        self.remote, self.log, self.package_fn = remote_svc, log or logging.getLogger("multiproxy"), package_fn
        self.wait, self.grace, self.poll = wait, grace, poll
        self.jobs: dict[str, UpdateJob] = {}
        self._all_task: Optional[asyncio.Task] = None

    # ---- what the page needs ----------------------------------------------------------------------------------------
    def package(self) -> Package:
        return self.package_fn()

    def job_json(self, pc: str) -> Optional[dict]:
        j = self.jobs.get(pc)
        if j and j.state in ("done", "cancelled"):
            return None                                    # success is silent
        return j.to_json() if j else None

    def can_update(self, r, pkg: Optional[Package] = None) -> bool:
        pkg = pkg or self.package()
        return bool(pkg.ok and r.kind == "courier" and r.online is True and device_can_update(r.report) and device_build(r.report) < pkg.build
                    and not (self.jobs.get(r.id) and self.jobs[r.id].active))

    def eligible(self) -> list:
        pkg = self.package()
        return [r for r in self.remote.all() if self.can_update(r, pkg)]

    def status(self) -> dict:
        pkg = self.package()
        devices = {}
        for r in self.remote.all():
            if r.kind == "courier":
                devices[r.id] = {"build": device_build(r.report) if r.report else None, "canUpdate": device_can_update(r.report), "online": r.online,
                                 "job": self.job_json(r.id)}
        return {"package": pkg.to_json(), "devices": devices, "eligible": [r.id for r in self.eligible()], "allRunning": bool(self._all_task and not self._all_task.done())}

    def any_active(self) -> bool:
        return any(j.active for j in self.jobs.values()) or bool(self._all_task and not self._all_task.done())

    # ---- start / cancel ---------------------------------------------------------------------------------------------
    def start(self, rid: str, anyway: bool = False) -> UpdateJob:
        r = self.remote._courier(rid)
        pkg = self.package()
        if not pkg.ok:
            raise other_pcs.OtherPcsError(409, "No update file here.")
        live = self.jobs.get(r.id)
        if live and live.active:
            return live
        if r.online is not True:
            raise other_pcs.OtherPcsError(409, f"{r.label} isn't answering, so it can't be updated now.")
        if not device_can_update(r.report):
            raise other_pcs.OtherPcsError(409, f"{version_of(device_build(r.report)) if device_build(r.report) else 'This Courier'} is too old to update from here. Update it on that device once.")
        if device_build(r.report) >= pkg.build:
            raise other_pcs.OtherPcsError(409, f"{r.label} is already on {version_of(device_build(r.report))}.")
        job = UpdateJob(r.id, r.label, device_build(r.report), pkg.build, total=pkg.size)
        self.jobs[r.id] = job
        job.task = asyncio.create_task(self._run(r, job, anyway))
        return job

    def start_all(self) -> int:
        """Every device that can be updated, one after the other (never together). A failure on one is on its row; the loop goes on to the next."""
        if self._all_task and not self._all_task.done():
            return 0
        todo = [r.id for r in self.eligible()]
        self._all_task = asyncio.create_task(self._run_all(todo))
        return len(todo)

    async def _run_all(self, ids: list) -> None:
        for rid in ids:
            try:
                r = self.remote._courier(rid)
                if not self.can_update(r):
                    continue                             # (updated by hand while it waited its turn, or gone offline)
                job = self.start(rid)
            except other_pcs.OtherPcsError:
                continue
            if job.task:
                await asyncio.gather(job.task, return_exceptions=True)

    def cancel(self, rid: str) -> None:
        job = self.jobs.get(self.remote._courier(rid).id)
        if job and job.state == "sending":
            job.cancel = True

    # ---- one update -------------------------------------------------------------------------------------------------
    def _fail(self, job: UpdateJob, code: str) -> None:
        job.state, job.code, job.ended = "failed", code, time.time()
        self.log.warning(f"Courier update on {job.label}: {version_of(job.from_build)} to {version_of(job.to_build)}: {code}")

    async def _run(self, r, job: UpdateJob, anyway: bool) -> None:
        import courier_client
        try:
            pkg = await asyncio.to_thread(self.package_fn, None, None, True) if self.package_fn is load_package else self.package()
            if not pkg.ok or pkg.build != job.to_build:
                return self._fail(job, "no_package")
            job.total = pkg.size
            spec = {"kind": file_transfer.UPDATE_KIND, "model": file_transfer.UPDATE_KIND, "file": file_transfer.UPDATE_FILE, "sha256": pkg.sha256}

            def progress(done, total, speed):
                job.done, job.speed = done, speed

            await file_transfer.send_file(_Transport(r.client, pkg.build), spec, pkg.path, progress, lambda: job.cancel, retry_for=SEND_RETRY_SECONDS)
            job.state, job.done = "installing", job.total
            await r.client.update_call("/courier/v1/update/stage", {"build": pkg.build}, timeout=120)
            before = (await r.client.fetch_report()).get("last_update")        # asked afresh: how an EARLIER try ended (a busy refusal, say) is not this try's result
            try:
                await r.client.update_call("/courier/v1/update/apply", {"build": pkg.build, "anyway": bool(anyway)}, timeout=60)
            except courier_client.UpdateRefused as e:
                if e.code == "busy":
                    job.state, job.code, job.ended = "busy", "busy", time.time()
                    self.log.info(f"Courier update on {job.label}: {version_of(job.from_build)} to {version_of(job.to_build)}: {job.label} is busy")
                    await self._drop_staged(r)
                    return
                raise
            code = await self._wait_for_return(r, job, before)
            if code == "ok":
                job.state, job.ended = "done", time.time()
                self.log.info(f"Courier update on {job.label}: {version_of(job.from_build)} to {version_of(job.to_build)}: ok")
            else:
                self._fail(job, code)
        except file_transfer.SendCancelled:
            job.state, job.ended = "cancelled", time.time()
            self.log.info(f"Courier update on {job.label}: {version_of(job.from_build)} to {version_of(job.to_build)}: cancelled")
        except file_transfer.TransferError as e:
            self._fail(job, code_of_transfer_error(e))
        except courier_client.UpdateRefused as e:
            self._fail(job, e.code if e.code in uv.WIRE_CODES else "update_failed")
        except Exception:                                    # never leave a job saying "sending" forever
            self._fail(job, "unreachable")

    async def _drop_staged(self, r) -> None:
        try:
            await r.client.update_call("/courier/v1/update/cancel", {}, timeout=15)
        except Exception:
            pass

    async def _wait_for_return(self, r, job: UpdateJob, before) -> str:
        """Wait for the Courier to answer as the new build. Returns "ok" or a code. About `wait` seconds is normal; a device that has not answered by then may be rolling
        back (its helper waits up to 90 s for the new Courier), so a silent device gets `grace` more before it is called unreachable."""
        started = time.monotonic()
        while True:
            await asyncio.sleep(self.poll)
            elapsed = time.monotonic() - started
            try:
                rep = await asyncio.wait_for(r.client.fetch_report(), 8)
            except Exception:
                rep = None
            if rep:
                build = device_build(rep)
                if build >= job.to_build:
                    self.remote._apply_report(r, rep)
                    return "ok"
                last = rep.get("last_update")
                if last and last != before:                  # the device says how this try ended
                    self.remote._apply_report(r, rep)
                    code = str(last.get("code") or "")
                    return code if code in uv.WIRE_CODES else "update_failed"
                if elapsed > self.wait:
                    self.remote._apply_report(r, rep)
                    return "update_failed"                   # it answers, still the old build, and says nothing happened
            elif elapsed > self.wait + self.grace:
                return "unreachable"
