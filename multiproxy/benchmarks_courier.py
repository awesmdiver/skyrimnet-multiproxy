"""Benchmarks on another PC, the Courier's side (protocol: docs/roadmap/benchmarks-remote-protocol.md, rules: benchmarks_remote_plan.py).

The main PC asks this Courier, over the home network, to run a benchmark with models that are ALREADY on this PC. This module is the job: it validates the plan, takes the device's one lock
in the same step as the busy check, runs the existing runner (`benchmarks_runner.BenchmarkService`, on this Courier's own engine and models folder), keeps the job's state on disk, and
ends the run cleanly when the owner goes quiet (60 s lease), the battery runs low or the PC sleeps. It never runs anything the plan names: a plan carries model IDs and a few allow-listed
settings, nothing else (step 1's validator refuses the rest). Sending models and collecting results are later steps; the finished run stays on this PC.

The routes live in courier.py and only call `CourierBenchmarks`; they add the request checks (security review SF1) before anything gets here.
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import benchmarks_contract as bc
import benchmarks_remote_plan as rp
import benchmarks_runner as br
import benchmarks_store as bs
import file_transfer
import safe_paths

LEASE_S = 60.0
LOW_BATTERY_PCT = 10
TICK_S = 1.0
SLEEP_GAP_S = 20.0                       # the check loop wanted to run every TICK_S; a gap this long means the whole PC was suspended
RETENTION_S = 14 * 86400                 # results nobody collected are removed after this (with a log line)
COLLECTED_KEEP_S = 86400                 # a collected job's record stays this long so a repeated acknowledge is still a success
MAX_UNCOLLECTED = 5
MAX_RESULT_BYTES = 512 * 1024 ** 2
REQUEST_MEMORY_S = 24 * 3600
MIN_FREE_MB = br.MIN_FREE_DISK_MB
RECENT_CHAT_S = br.RECENT_GAME_REQUEST_S
OWNER_HEADER = "X-MultiProxy-Instance"
TOKEN_HEADER = "X-Benchmark-Job-Token"

# Said to the main PC (it may show them; the design side words the page).
REFUSE_BUSY_BENCHMARK = "{pc} is already running a benchmark. Wait for it to finish, or cancel it."
REFUSE_BUSY_CHAT = "{pc} is answering the game right now. Wait until it has been quiet for a couple of minutes, or choose Take over."
REFUSE_BUSY_TRANSFER = "{pc} is receiving a file right now. Wait for it to finish, then try again."
REFUSE_NO_SPACE = "{pc} doesn't have enough free space to save the results."
REFUSE_MODEL_MISSING = "{name} isn't on {pc} yet."
REFUSE_NO_JOB = "{pc} has no benchmark with that ID."
REFUSE_EXPIRED = "{pc} no longer has the results of that benchmark."
REFUSE_OWNER = "That benchmark belongs to another PC."
REFUSE_TOKEN = "That benchmark's key doesn't match."
REFUSE_REUSED = "That request ID was already used for a different benchmark plan."
REFUSE_ENDED = "That benchmark has already ended."
REFUSE_NO_RUN = "{pc} has no results with that ID."
REFUSE_NOT_READY = "Those results aren't finished yet."
REFUSE_GONE = "Those results were already collected."
REFUSE_HASHES = "The checksums don't match the results on {pc}, so nothing was deleted."
REFUSE_NEED_HASHES = "To delete the results, send the checksum of every file you received."
REFUSE_BAD_NAME = "{pc} has no result file with that name."
REFUSE_TOO_BIG = "Those results are too big to send."
REFUSE_NO_SPACE_MODELS = "{pc} needs {need} free for the models and some spare, but has {have}."
REFUSE_NO_FOLDER = "{pc} hasn't picked a folder for models yet."
REFUSE_NOT_IN_PLAN = "This benchmark does not need that model."
REFUSE_NOT_RECEIVING = "{pc} isn't taking models for that benchmark any more."
DISK_FULL_LINE = "{pc} ran out of disk space while receiving a model, so the test was stopped."
UNSAFE_LINE = "{pc} stopped the test: a folder it was receiving a model into was changed by something else."
SPARE_MIN_BYTES = 2 * 1024 ** 3
SPARE_FRACTION = 0.10
KEPT_FOLDER = "sent-from-main-pc"            # under <models>/models: where a model the person asked to keep goes


class BenchError(Exception):
    """A refusal with an HTTP status, a short code, the plain line, and the field it is about."""

    def __init__(self, status: int, code: str, message: str, field: Optional[str] = None, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.field, self.extra = status, code, message, field, extra


def capabilities_block() -> dict:
    """What `/courier/v1/report` says under `courier.benchmarks`: the protocol version and what this Courier can do (the main PC refuses a Courier that lacks one it needs)."""
    return {"protocol_version": rp.PROTOCOL_VERSION,
            "capabilities": [rp.CAP_START, rp.CAP_STATUS, rp.CAP_CANCEL, rp.CAP_HEARTBEAT, rp.CAP_COLLECT, rp.CAP_ACK, rp.CAP_RECEIVE_MODELS, rp.CAP_KEEP_AWAKE, rp.CAP_BATTERY_STOP, rp.CAP_START_SERVICES]}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class _Log:
    """The transfer receiver logs with `.info`; the job's log is a plain function."""

    def __init__(self, fn: Callable[[str], None]):
        self.fn = fn

    def info(self, msg, *a, **k):
        self.fn(str(msg))

    warning = error = debug = info


class CourierBenchmarks:
    def __init__(self, root, service: br.BenchmarkService, models_fn: Callable[[], dict], *, device_fn: Callable[[], dict], keep_awake=None, power_fn: Callable[[], dict] = lambda: {"has_battery": False},
                 recent_chat_fn: Callable[[], Optional[float]] = lambda: None, transfer_busy_fn: Callable[[], bool] = lambda: False, restore_fn: Optional[Callable[[str], Awaitable]] = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time, lease_s: float = LEASE_S, tick_s: float = TICK_S, sleep_gap_s: float = SLEEP_GAP_S,
                 free_mb_fn: Optional[Callable[[Path], float]] = None, models_root=None, models_free_fn: Optional[Callable[[Path], int]] = None,
                 register_fn: Optional[Callable[[Path], Any]] = None, pc_name: Callable[[], str] = lambda: "This PC", log: Callable[[str], None] = lambda m: None):
        self.root = Path(root)
        self.runs_dir = self.root / "benchmarks"                     # the runner's run folders (`benchmarks_store`)
        self.jobs_dir = self.root / "benchmark-jobs"                # one `<job id>.json` per job, the lock, the request memory, the engine's pid file
        self.service, self.models_fn = service, models_fn
        self.device_fn, self.keep_awake, self.power_fn = device_fn, keep_awake, power_fn
        self.recent_chat_fn, self.transfer_busy_fn, self.restore_fn = recent_chat_fn, transfer_busy_fn, restore_fn
        self.clock, self.wall, self.lease_s, self.tick_s, self.sleep_gap_s = clock, wall, lease_s, tick_s, sleep_gap_s
        self.free_mb_fn = free_mb_fn or (lambda p: shutil.disk_usage(p).free / (1024 * 1024))
        self.pc_name, self.log = pc_name, log
        self.models_root = models_root                              # the Courier's models folder: a job's own folder lives under it (`benchmark-jobs/<job id>`)
        self.models_free_fn = models_free_fn or (lambda p: shutil.disk_usage(p).free)
        self.register_fn = register_fn                              # registers a kept model like one added by hand (`local.add_custom`)
        self.receiver = file_transfer.TransferReceiver(self._bench_dest, _Log(log), on_file_done=self._file_received)      # the existing transfer, for models sent to a job
        self.jobs: dict = {}                                        # job id -> job (the dict that is saved)
        self.requests: dict = {}                                    # request id -> {"job_id", "owner_id", "plan_hash", "at"}
        self._rt: dict = {}                                         # job id -> {"lease": monotonic deadline, "task": Task, "watch": Task}
        self._takeover = False
        self._awake_was_on = False
        self._power_start: dict = {}
        hooks = service.hooks
        original = hooks.last_game_request_at
        hooks.last_game_request_at = lambda: None if self._takeover else original()
        hooks.run_facts = self.run_facts
        hooks.model_done = self._model_done
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---- files ---------------------------------------------------------------------------------------------------------------------------------------------------------------
    @property
    def lock_path(self) -> Path:
        return self.jobs_dir / "lock.json"

    def _job_path(self, job_id: str) -> Path:
        return rp.contain(self.jobs_dir, f"{job_id}.json")

    def _save(self, job: dict) -> None:
        safe_paths.write_private_json(self._job_path(job["job_id"]), job)

    def _save_requests(self) -> None:
        safe_paths.write_private_json(self.jobs_dir / "requests.json", self.requests)

    def _load(self) -> None:
        for p in sorted(self.jobs_dir.glob("*.json")):
            if p.name in ("lock.json", "requests.json"):
                continue
            try:
                job = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(job, dict) and rp.valid_job_id(job.get("job_id")) and job.get("state") in rp.JOB_STATES:
                self.jobs[job["job_id"]] = job
        try:
            data = json.loads((self.jobs_dir / "requests.json").read_text(encoding="utf-8"))
            self.requests = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.requests = {}

    # ---- states --------------------------------------------------------------------------------------------------------------------------------------------------------------
    def _move(self, job: dict, new: str, reason: str = "", message: str = "") -> None:
        job["state"] = rp.move_job(job["state"], new, rp.ACTOR_COURIER, reason)
        job["state_changed_at"] = _iso(self.wall())
        if new in rp.REASON_STATES:
            job["reason"], job["message"] = reason, message
        if new in rp.ENDED_STATES:
            job["ended_at"], job["ended_epoch_s"] = _iso(self.wall()), self.wall()
        self._save(job)

    def active_job(self) -> Optional[dict]:
        return next((j for j in self.jobs.values() if j["state"] in rp.ACTIVE_STATES), None)

    # ---- Start ---------------------------------------------------------------------------------------------------------------------------------------------------------------
    def _busy_reason(self, plan: dict) -> Optional[tuple]:
        """(busy_reason, plain line) when this device cannot take a job now, else None. Taken in the same synchronous step as the lock below, so two controllers cannot both pass."""
        pc = self.pc_name()
        if self.active_job() is not None or self.service.running():
            return "benchmark", REFUSE_BUSY_BENCHMARK.format(pc=pc)
        if self.lock_path.exists():                                 # (a lock whose job is not active is stale: reconcile removes it, and so does this)
            try:
                holder = json.loads(self.lock_path.read_text(encoding="utf-8")).get("job_id")
            except (OSError, ValueError):
                holder = None
            if holder in self.jobs and self.jobs[holder]["state"] in rp.ACTIVE_STATES:
                return "benchmark", REFUSE_BUSY_BENCHMARK.format(pc=pc)
            self.lock_path.unlink(missing_ok=True)
        if self.transfer_busy_fn():
            return "transfer", REFUSE_BUSY_TRANSFER.format(pc=pc)
        if not plan["take_over"]:
            last = self.recent_chat_fn()
            if last is not None and self.wall() - last < RECENT_CHAT_S:
                return "chat", REFUSE_BUSY_CHAT.format(pc=pc)
            if plan["while_testing"] == "stop":
                try:
                    held = self.service.hooks.loaded_by_multiproxy()
                except Exception:
                    held = []
                if held:
                    return "chat", REFUSE_BUSY_CHAT.format(pc=pc)
        return None

    def _take_lock(self, job_id: str) -> bool:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(str(self.lock_path), flags, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"job_id": job_id, "created_at": _iso(self.wall())}, f)
        return True

    def _release_lock(self, job_id: str) -> None:
        try:
            if json.loads(self.lock_path.read_text(encoding="utf-8")).get("job_id") == job_id:
                self.lock_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            self.lock_path.unlink(missing_ok=True)

    def _forget_old_requests(self) -> None:
        now = self.wall()
        stale = [k for k, v in self.requests.items() if now - float(v.get("at", 0)) > REQUEST_MEMORY_S]
        for k in stale:
            del self.requests[k]

    async def start(self, owner: str, raw: bytes) -> tuple:
        """(HTTP status, answer) for a start request. Raises BenchError. Nothing runs, and nothing is reserved, until the plan has passed the validator."""
        pc = self.pc_name()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):                                   # (RecursionError: a plan nested far too deeply)
            raise BenchError(400, "bad_plan", rp.PLAN_BAD.format(pc=pc, why="the plan must be JSON"))
        installed = self.models_fn()
        catalogue = {mid: {"file": Path(path).name} for mid, path in installed.items()}
        try:
            plan = rp.validate_plan(body, catalogue)
        except rp.PlanRefused as e:
            raise BenchError(400, "bad_protocol" if e.code == "bad_protocol" else "bad_plan", rp.PLAN_BAD.format(pc=pc, why=e.why), field=e.field or None)
        if plan["owner_id"] != owner:
            raise BenchError(403, "owner_mismatch", REFUSE_OWNER, field="owner_id")
        self._forget_old_requests()
        known = self.requests.get(plan["request_id"])
        if known:
            if known.get("owner_id") != owner or known.get("plan_hash") != plan["plan_hash"]:
                raise BenchError(409, "request_id_reused", REFUSE_REUSED, field="request_id")
            job = self.jobs.get(known["job_id"])
            if job is None:
                raise BenchError(410, "result_expired", REFUSE_EXPIRED.format(pc=pc))
            return 200, {"job_id": job["job_id"], "job_token": job["job_token"], "state": job["state"], "replayed": True}
        inventory = await asyncio.to_thread(self._inventory) if any(m["source"] == "content" for m in plan["models"]) else {}      # (the same bytes, by hash, never the same name)
        paths, origins, to_send_idx = [], [], []
        for i, m in enumerate(plan["models"]):
            if m["source"] == "catalogue":
                path = installed.get(m["id"])
                if path is None or not Path(path).is_file():
                    raise BenchError(409, "model_missing", REFUSE_MODEL_MISSING.format(name=m["id"], pc=pc), field="models")
                paths.append(str(path))
                origins.append("catalogue")
            elif m["id"] in inventory:
                paths.append(str(inventory[m["id"]]))
                origins.append("already_there")
            else:
                paths.append("")
                origins.append("sent")
                to_send_idx.append(i)
        if to_send_idx:
            if not self.models_root:
                raise BenchError(409, "no_models_folder", REFUSE_NO_FOLDER.format(pc=pc))
            need = sum(plan["models"][i]["size"] for i in to_send_idx)
            spare = max(int(need * SPARE_FRACTION), SPARE_MIN_BYTES)
            try:
                Path(self.models_root).mkdir(parents=True, exist_ok=True)
                have = int(self.models_free_fn(Path(self.models_root)))
            except OSError:
                have = 0
            if have < need + spare:
                raise BenchError(409, "no_space", REFUSE_NO_SPACE_MODELS.format(pc=pc, need=file_transfer.gb(need + spare), have=file_transfer.gb(have)), need_bytes=need + spare, have_bytes=have)
        try:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            if self.free_mb_fn(self.runs_dir) < MIN_FREE_MB:
                raise OSError("full")
        except OSError:
            raise BenchError(409, "no_space", REFUSE_NO_SPACE.format(pc=pc))
        # ---- one step, no await: the busy check and the reservation (a second controller arriving now sees the lock) ----
        busy = self._busy_reason(plan)
        if busy:
            raise BenchError(409, "busy", busy[1], busy_reason=busy[0])
        job_id = "job-" + secrets.token_urlsafe(18)
        if not self._take_lock(job_id):
            raise BenchError(409, "busy", REFUSE_BUSY_BENCHMARK.format(pc=pc), busy_reason="benchmark")
        now = self.wall()
        to_send = []
        try:
            for i in to_send_idx:
                m = plan["models"][i]
                fname = f"{m['sha256'][:16]}.gguf"                      # the file's name is made from its hash; no text from the request is ever a path
                path = rp.job_file(self.models_root, job_id, fname)
                paths[i] = str(path)
                to_send.append({"i": i, "id": m["id"], "sha256": m["sha256"], "size": m["size"], "file": fname, "path": str(path), "state": "waiting", "tid": None})
        except ValueError:
            self._release_lock(job_id)
            raise BenchError(409, "no_models_folder", REFUSE_NO_FOLDER.format(pc=pc))
        job = {"job_id": job_id, "request_id": plan["request_id"], "owner_id": owner, "job_token": secrets.token_urlsafe(32), "plan": plan, "state": rp.J_QUEUED, "reason": "", "message": "",
               "cancel_requested": False, "interrupt_reason": "", "created_at": _iso(now), "state_changed_at": _iso(now), "ended_at": None, "ended_epoch_s": None,
               "model_paths": paths, "runs": [], "services_started": [], "services_stopped_for_test": [], "services_restored": [], "batch_id": None,
               "origins": origins, "to_send": to_send, "after": {}, "fail": None}
        self.jobs[job_id] = job
        self.requests[plan["request_id"]] = {"job_id": job_id, "owner_id": owner, "plan_hash": plan["plan_hash"], "at": now}
        try:
            self._save(job)
            self._save_requests()
        except OSError:
            self.jobs.pop(job_id, None)
            self.requests.pop(plan["request_id"], None)
            self._release_lock(job_id)
            raise BenchError(409, "no_space", REFUSE_NO_SPACE.format(pc=pc))
        rt = {"lease": self.clock() + self.lease_s}
        self._rt[job_id] = rt
        rt["task"] = asyncio.create_task(self._drive(job))
        self.log(f"Benchmark {job_id} accepted for {len(paths)} model(s)")
        return 201, {"job_id": job_id, "job_token": job["job_token"], "state": job["state"], "replayed": False}

    # ---- the job -------------------------------------------------------------------------------------------------------------------------------------------------------------
    def _runner_body(self, plan: dict, paths: list) -> dict:
        body = {k: plan[k] for k in ("levels", "requests_per_level", "prompts", "temperature", "sustained", "sustained_s", "request_timeout_s", "warmup", "cooldown", "cooldown_c",
                                     "cooldown_max_s", "ceiling_c", "heat_stop", "keep_started")}
        body.update(models=list(paths), settings=dict(plan["settings"]), background=plan["while_testing"], kind=plan["kind"])
        return body

    async def _drive(self, job: dict) -> None:
        """Run the job: prepare, run, then clean up (put back what the test stopped, release the keep-awake and the lock) and ONLY THEN move to the end state, so a main PC that sees the job
        ended can start the next one at once."""
        plan, jid = job["plan"], job["job_id"]
        rt = self._rt[jid]
        watch = None
        failure: Optional[tuple] = None
        forced: Optional[tuple] = None
        try:
            if self.keep_awake is not None:
                self._awake_was_on = bool(self.keep_awake.on)
                self.keep_awake.set(True)
            self._power_start = self._power()
            self._takeover = plan["take_over"]
            rt["watch"] = watch = asyncio.create_task(self._watch(job))
            self._move(job, rp.J_PREPARING)
            if job["to_send"]:
                self._move(job, rp.J_RECEIVING)
                await self._await_models(job)
                if job["interrupt_reason"] or job["cancel_requested"] or job.get("fail"):
                    if job.get("fail"):
                        failure = tuple(job["fail"])
                    return
                self._move(job, rp.J_PREPARING)
            result = await self.service.start(self._runner_body(plan, job["model_paths"]), takeover=plan["take_over"])
            if not result.get("ok"):
                failure = ("refused", str(result.get("message") or ""))
            else:
                job["batch_id"] = result.get("batch_id")
                bg = getattr(self.service, "_bg", {}) or {}
                job["services_started"] = list(bg.get("started", []))
                job["services_stopped_for_test"] = list(bg.get("unloaded", []))
                self._save(job)
                if job["interrupt_reason"]:
                    await self.service.interrupt(job["interrupt_reason"])
                elif job["cancel_requested"]:
                    await self.service.cancel()
                else:
                    self._move(job, rp.J_RUNNING)
                await self.service.wait()
        except asyncio.CancelledError:
            forced = ("interrupted", "courier_stopped")
            raise
        except Exception as e:                              # a bug must end this job, not the Courier
            self.log(f"Benchmark {jid} failed: {type(e).__name__}")
            failure = ("internal_error", type(e).__name__)
        finally:
            self._takeover = False
            if watch is not None:
                watch.cancel()
            try:
                await asyncio.shield(asyncio.to_thread(self._cleanup_job_files, job))        # whatever was sent for this job goes (a model that was already there is never in it)
            except Exception:
                pass
            try:
                await asyncio.shield(self._restore_services(job))
            except Exception:
                pass
            if self.keep_awake is not None and not self._awake_was_on:
                self.keep_awake.set(False)
            self._release_lock(jid)
            self._rt.pop(jid, None)
            if job["state"] in rp.ACTIVE_STATES:
                if failure:
                    self._move(job, rp.J_FAILED, failure[0], failure[1])
                else:
                    self._finish(job, forced=forced)
                if job["state"] in rp.ACTIVE_STATES:                    # (never leave a job active with no one driving it)
                    self._move(job, rp.J_INTERRUPTED, "internal_error", "")
                self._maybe_collected(job)
                self._save(job)
            self.prune()

    def _finish(self, job: dict, forced: Optional[tuple] = None) -> None:
        if job["state"] not in rp.ACTIVE_STATES:
            return
        st = self.service.status()
        runs = [{"run_id": r.get("run_id"), "model_file": r.get("model"), "state": r.get("status"), "reason": r.get("reason", ""), "collectable": bool(r.get("run_id"))} for r in st.get("runs", [])]
        job["runs"] = runs
        if job["interrupt_reason"]:
            return self._move(job, rp.J_INTERRUPTED, job["interrupt_reason"], self._reason_line(job["interrupt_reason"]))
        if forced:
            return self._move(job, rp.J_INTERRUPTED, forced[1], self._reason_line(forced[1]))
        if job["cancel_requested"]:
            return self._move(job, rp.J_CANCELLED, "user_cancel", "")
        states = [r["state"] for r in runs]
        if any(s == bc.RUN_FAILED for s in states):
            r = next(r for r in runs if r["state"] == bc.RUN_FAILED)
            return self._move(job, rp.J_FAILED, "run_failed", r.get("reason") or "")
        if any(s in (bc.RUN_INTERRUPTED, bc.RUN_INVALIDATED) for s in states):
            r = next(r for r in runs if r["state"] in (bc.RUN_INTERRUPTED, bc.RUN_INVALIDATED))
            return self._move(job, rp.J_INTERRUPTED, r.get("reason") or "interrupted", "")
        if any(s == bc.RUN_CANCELLED for s in states):
            return self._move(job, rp.J_CANCELLED, "user_cancel", "")
        if st.get("batch_stopped"):                                  # the guard ended the batch: memory did not free up, or an engine could not be fully stopped
            reason = "memory_not_recovered" if st.get("batch_stopped") == "memory_not_recovered" else "cleanup_failed"
            return self._move(job, rp.J_INTERRUPTED, reason, self._reason_line(reason))
        if not runs or len(runs) < len(job["model_paths"]):
            return self._move(job, rp.J_FAILED, "no_run", "")
        self._move(job, rp.J_FINISHED)

    @staticmethod
    def _reason_line(reason: str) -> str:
        return {"lost_contact": "The main PC stopped answering, so the test was stopped.", "low_battery": "The test stopped because the battery was low.",
                "slept": "This PC went to sleep during the test.", "courier_stopped": "The Courier was closed during the test.",
                "courier_restarted": "The Courier was restarted during the test.", "memory_not_recovered": "The PC's memory did not free up after a model. The rest of the tests were cancelled.",
                "cleanup_failed": "A model's engine did not stop fully. The rest of the tests were cancelled."}.get(reason, "")

    async def _watch(self, job: dict) -> None:
        """Every tick while the job is active: the lease, the battery, a sleep, a cancel asked while preparing. The first problem found ends the run (everything measured is kept)."""
        rt, last = self._rt[job["job_id"]], self.clock()
        cancelled_sent = False
        while job["state"] in rp.ACTIVE_STATES:
            await asyncio.sleep(self.tick_s)
            now = self.clock()
            gap, last = now - last, now
            reason = ""
            if gap > self.sleep_gap_s:
                reason = "slept"
            elif now > rt["lease"]:
                reason = "lost_contact"
            else:
                p = self._power()
                pct = p.get("battery_percent")
                if p.get("has_battery") and p.get("plugged_in") is False and isinstance(pct, (int, float)) and pct < LOW_BATTERY_PCT:
                    reason = "low_battery"
            if reason and not job["interrupt_reason"]:
                job["interrupt_reason"] = reason
                self._save(job)
                self.log(f"Benchmark {job['job_id']} interrupted: {reason}")
                await self.service.interrupt(reason)
            elif job["cancel_requested"] and not cancelled_sent and self.service.running():
                cancelled_sent = True
                await self.service.cancel()

    def _power(self) -> dict:
        try:
            return dict(self.power_fn() or {})
        except Exception:
            return {"has_battery": False}

    async def _restore_services(self, job: dict) -> None:
        """Put back what this test stopped for it (the models MultiProxy had loaded), unless something has started them since: a newer change by the person stays."""
        ids = [i for i in job.get("services_stopped_for_test", []) if i not in job.get("services_restored", [])]
        if not ids or self.restore_fn is None:
            return
        try:
            loaded = {e["id"] for e in self.service.hooks.background_loaded()}
        except Exception:
            loaded = set()
        for sid in ids:
            if sid in loaded:
                continue
            try:
                why = await self.restore_fn(sid)
            except Exception:
                why = "failed"
            if not why:
                job["services_restored"].append(sid)
        self._save(job)

    @staticmethod
    def _delivery_of(job: dict, path: str) -> str:
        key = os.path.normcase(os.path.abspath(str(path)))
        for i, p in enumerate(job.get("model_paths", [])):
            if p and os.path.normcase(os.path.abspath(p)) == key:
                return (job.get("origins") or [])[i] if i < len(job.get("origins") or []) else "catalogue"
        return "catalogue"

    # ---- the facts a run records ---------------------------------------------------------------------------------------------------------------------------------------------
    def run_facts(self, phase: str, ctx) -> dict:
        job = self.active_job()
        if job is None:
            return {}
        power = self._power()
        if phase == "start":
            dev = dict(self.device_fn())
            return {"device": dev, "job": {"job_id": job["job_id"], "request_id": job["request_id"], "owner_id": job["owner_id"], "plan_hash": job["plan"]["plan_hash"]},
                    "builds": {"courier_build": dev.get("courier_build"), "protocol_version": rp.PROTOCOL_VERSION, "schema_version": bc.SCHEMA_VERSION},
                    "power": {"ac": self._power_start.get("plugged_in") if self._power_start.get("has_battery") else None, "has_battery": bool(self._power_start.get("has_battery")),
                              "battery_pct_start": self._power_start.get("battery_percent"), "power_mode": None, "power_mode_reason": "not read on this PC"},
                    "services": {"requested": job["plan"]["while_testing"], "seen": [i.get("name") for i in getattr(ctx, "bg_loaded", [])]},
                    "model_delivery": {"how": self._delivery_of(job, getattr(ctx, "path", "")), "keep_requested": bool(job["plan"]["keep_models"])}}
        temps = {}
        try:
            for who, fields in ctx.sampler.highest_lowest().items():
                if who != "machine" and "temp_c" in fields:
                    temps[str(who)] = fields["temp_c"]["highest_sampled"]
        except Exception:
            pass
        return {"power_end": {"ac": power.get("plugged_in") if power.get("has_battery") else None, "battery_pct_end": power.get("battery_percent")},
                "thermal": {"max_temp_c": temps or None, "throttled": None, "throttled_reason": "the card's throttle flags are not read yet"},
                "services_started_by_run": list(job.get("services_started", [])), "services_stopped_for_test": list(job.get("services_stopped_for_test", [])),
                "interruptions": ([{"kind": job["interrupt_reason"], "detail": self._reason_line(job["interrupt_reason"])}] if job.get("interrupt_reason") else [])}

    # ---- asked by the routes -------------------------------------------------------------------------------------------------------------------------------------------------
    def _owned(self, owner: str, token: str, job_id: str = "") -> dict:
        """The caller's job, or BenchError. Only a caller that proves both the owner and the job's key gets the job, and only that caller renews its lease."""
        pc = self.pc_name()
        job = self.jobs.get(job_id) if job_id else next(iter(sorted((j for j in self.jobs.values() if j["owner_id"] == owner), key=lambda j: j["created_at"], reverse=True)), None)
        if job is None:
            if job_id and any(v.get("job_id") == job_id for v in self.requests.values()):
                raise BenchError(410, "result_expired", REFUSE_EXPIRED.format(pc=pc))
            raise BenchError(404, "unknown_job", REFUSE_NO_JOB.format(pc=pc))
        if job["owner_id"] != owner:
            raise BenchError(403, "owner_mismatch", REFUSE_OWNER)
        if not hmac.compare_digest(str(token or "").encode("utf-8"), job["job_token"].encode("utf-8")):
            raise BenchError(403, "bad_job_token", REFUSE_TOKEN)
        return job

    def _renew(self, job: dict) -> None:
        rt = self._rt.get(job["job_id"])
        if rt is not None:
            rt["lease"] = self.clock() + self.lease_s

    def view(self, job: dict) -> dict:
        rt = self._rt.get(job["job_id"])
        st = self.service.status() if job["state"] in rp.ACTIVE_STATES else {}
        runs = self._job_runs(job)
        done = {r["model_file"] for r in runs}
        idx = int(st.get("model_index") or 0)
        models = []
        sent = {e["i"]: e for e in job.get("to_send", [])}
        for i, m in enumerate(job["plan"]["models"]):
            fname = Path(job["model_paths"][i]).name
            state = "done" if fname in done else ("testing" if job["state"] == rp.J_RUNNING and idx == i + 1 else "ready")
            row = {"id": m["id"], "state": state, "run_id": next((r["run_id"] for r in runs if r["model_file"] == fname), None), "delivery": (job.get("origins") or ["catalogue"] * (i + 1))[i]}
            e = sent.get(i)
            if e is not None:
                live = self.receiver.items.get(e.get("tid") or "")
                row.update(bytes_total=e["size"], bytes_received=e["size"] if e["state"] == "received" else (live.done if live else e.get("bytes", 0)))
                if e["state"] != "received" and state == "ready":
                    row["state"] = "receiving" if e["state"] == "receiving" else "waiting"
            if m["id"] in job.get("after", {}):
                row["after"] = job["after"][m["id"]]
            models.append(row)
        sending = None
        if job.get("to_send"):
            live = [self.receiver.items.get(e.get("tid") or "") for e in job["to_send"]]
            speed = sum(i.speed for i in live if i and i.state == "receiving")
            sending = {"bytes_total": sum(e["size"] for e in job["to_send"]),
                       "bytes_done": sum(e["size"] if e["state"] == "received" else (i.done if i else e.get("bytes", 0)) for e, i in zip(job["to_send"], live)), "speed_bps": int(speed)}
        phase = {rp.J_QUEUED: "preparing", rp.J_PREPARING: "preparing", rp.J_RECEIVING: "sending", rp.J_RUNNING: "testing"}.get(job["state"], "bringing_results_back")
        out = {"job_id": job["job_id"], "phase": phase, "sending": sending, "request_id": job["request_id"], "owner_id": job["owner_id"], "state": job["state"], "reason": job["reason"], "message": job["message"],
               "cancel_requested": job["cancel_requested"], "created_at": job["created_at"], "state_changed_at": job["state_changed_at"], "ended_at": job["ended_at"],
               "plan_hash": job["plan"]["plan_hash"], "lease_expires_in_s": max(0, round(rt["lease"] - self.clock())) if rt else None, "models": models, "runs": runs,
               "step": {"model_index": st.get("model_index"), "models_total": st.get("models_total"), "name": st.get("step"), "level": st.get("level"), "steps_done": st.get("steps_done"), "steps_total": st.get("steps_total"),
                        "engine_busy": st.get("engine_busy"), "model_name": st.get("model_name"), "model_started_epoch_s": st.get("model_started_epoch_s"), "batch_started_epoch_s": st.get("batch_started_epoch_s")} if st else None}
        if st.get("readings"):
            out["readings"] = st["readings"]
        if job["state"] in rp.ACTIVE_STATES:
            out["power"] = self._power()
        return out

    # ---- models sent for a job: inventory, receiving, publishing, cleaning up ----------------------------------------------------------------------------------------
    def _inventory(self) -> dict:
        """{content id: path} for every model this PC has, by the bytes (`sha256:<hex>:<size>`; hashed once, remembered by path, size and date). Slow the first time: call it in a thread."""
        out = {}
        for mid, path in self.models_fn().items():
            try:
                out[bs.model_id_for(path, self.runs_dir)] = Path(path)
            except OSError:
                continue
        return out

    def models_report(self) -> dict:
        """What the main PC asks before it sends anything: the models here by their bytes, and the free space on the models drive. No path is given out."""
        inv = self._inventory()
        free = None
        if self.models_root:
            try:
                Path(self.models_root).mkdir(parents=True, exist_ok=True)
                free = int(self.models_free_fn(Path(self.models_root)))
            except OSError:
                free = None
        return {"models": [{"content_id": cid, "file": p.name, "size": p.stat().st_size if p.exists() else None} for cid, p in inv.items()], "free_bytes": free}

    def _send_job(self, owner: str, token: str, job_id: str) -> dict:
        job = self._owned(owner, token, job_id)
        if job["state"] not in (rp.J_QUEUED, rp.J_PREPARING, rp.J_RECEIVING) or not job["to_send"]:
            raise BenchError(409, "illegal_state", REFUSE_NOT_RECEIVING.format(pc=self.pc_name()))
        return job

    def _entry(self, job: dict, sha: Any, size: Any = None, tid: Optional[str] = None) -> dict:
        for e in job["to_send"]:
            if (tid is not None and e.get("tid") == tid) or (tid is None and e["sha256"] == sha and (size is None or e["size"] == size)):
                return e
        raise BenchError(404, "unknown_model", REFUSE_NOT_IN_PLAN)

    def _bench_dest(self, spec: dict) -> Path:
        """Where the receiver may write: the job's own folder, the file named from its hash. The job must exist, be waiting for models, and the file must be one of the plan's."""
        job = self.jobs.get(str(spec.get("job_id")))
        if job is None or job["state"] not in (rp.J_QUEUED, rp.J_PREPARING, rp.J_RECEIVING):
            raise file_transfer.TransferError(409, REFUSE_NOT_RECEIVING)
        e = next((x for x in job["to_send"] if x["sha256"] == spec.get("sha256") and x["file"] == spec.get("file") and x["size"] == spec.get("size")), None)
        if e is None:
            raise file_transfer.TransferError(404, REFUSE_NOT_IN_PLAN)
        try:
            folder = rp.job_file(self.models_root, job["job_id"], e["file"]).parent
            folder.mkdir(parents=True, exist_ok=True)                  # the job's folder is made here, then the path is checked again with the folder in place
            return rp.job_file(self.models_root, job["job_id"], e["file"])
        except (ValueError, OSError):
            raise file_transfer.TransferError(400, file_transfer.BAD_FILE)

    def _recheck(self, job: dict, e: dict) -> None:
        """Security review SF2: the path was checked when the transfer began; check it again right before each write and the final rename, so a folder swapped for a junction in between is
        refused (and the job ends: something other than this program is changing that folder)."""
        try:
            rp.job_file(self.models_root, job["job_id"], e["file"])
        except ValueError:
            job["fail"] = ["unsafe_path", UNSAFE_LINE.format(pc=self.pc_name())]
            self._save(job)
            raise BenchError(409, "unsafe_path", UNSAFE_LINE.format(pc=self.pc_name()))

    def _as_error(self, e: "file_transfer.TransferError") -> BenchError:
        return BenchError(e.status, "transfer_failed", e.message.replace("{pc}", self.pc_name()), **({"offset": e.offset} if e.offset is not None else {}))

    def transfer_begin(self, owner: str, token: str, job_id: str, body: dict) -> dict:
        job = self._send_job(owner, token, job_id)
        self._renew(job)
        sha, size = body.get("sha256"), body.get("size")
        if isinstance(size, bool) or not isinstance(size, int):
            raise BenchError(400, "bad_size", REFUSE_NOT_IN_PLAN)
        e = self._entry(job, sha, size)
        spec = {"kind": "model", "model": "bm-" + e["sha256"][:24], "file": e["file"], "size": e["size"], "sha256": e["sha256"], "job_id": job_id}
        try:
            info = self.receiver.begin(spec)
        except file_transfer.TransferError as te:
            raise self._as_error(te)
        e["tid"] = info["id"]
        if info.get("state") == "done":
            e["state"] = "received"
        else:
            e["state"] = "receiving"
        self._save(job)
        return info

    async def transfer_chunk(self, owner: str, token: str, job_id: str, tid: str, offset: int, body) -> dict:
        job = self._send_job(owner, token, job_id)
        e = self._entry(job, None, tid=tid)
        self._recheck(job, e)
        try:
            done = await self.receiver.chunk(tid, offset, body)
        except file_transfer.TransferError as te:
            if te.status == 500:                                          # the disk filled (or failed) while writing: the job ends, what was sent is thrown away
                job["fail"] = ["disk_full", DISK_FULL_LINE.format(pc=self.pc_name())]
                self._save(job)
            raise self._as_error(te)
        e["bytes"] = done
        return {"offset": done}

    async def transfer_finish(self, owner: str, token: str, job_id: str, tid: str) -> dict:
        job = self._send_job(owner, token, job_id)
        e = self._entry(job, None, tid=tid)
        self._recheck(job, e)
        try:
            return await self.receiver.finish(tid)
        except file_transfer.TransferError as te:
            raise self._as_error(te)

    def transfer_cancel(self, owner: str, token: str, job_id: str, tid: str) -> dict:
        job = self._send_job(owner, token, job_id)
        e = self._entry(job, None, tid=tid)
        self.receiver.cancel(tid)
        e["state"], e["bytes"] = "waiting", 0
        self._save(job)
        return {"ok": True}

    def _file_received(self, inc) -> None:
        """The receiver says a model file passed its hash check and is in place (only then is it a file at all: until now it was a `.partial`)."""
        job = self.jobs.get(str(inc.spec.get("job_id")))
        if job is None:
            return
        e = next((x for x in job["to_send"] if x.get("tid") == inc.id), None)
        if e is not None:
            e["state"], e["bytes"] = "received", e["size"]
            self._save(job)

    async def _await_models(self, job: dict) -> None:
        """Wait in `receiving` until every file the plan needs sent has arrived and passed its hash, or the job is told to stop (cancel, a quiet owner, a full disk)."""
        while not all(e["state"] == "received" for e in job["to_send"]):
            if job["interrupt_reason"] or job["cancel_requested"] or job.get("fail"):
                return
            await asyncio.sleep(0.05)

    def _cleanup_job_files(self, job: dict) -> int:
        """Delete everything this job received: stop its transfers (their `.partial` files go), then remove the job's own folder. Nothing outside `benchmark-jobs/<job id>` is touched, and a model that was
        already on this PC is never in there."""
        for e in job.get("to_send", []):
            if e.get("tid"):
                self.receiver.cancel(e["tid"])
        if not self.models_root or not rp.valid_job_id(job["job_id"]):
            return 0
        try:
            folder = rp.contain(self.models_root, f"{rp.JOBS_SUBDIR}/{job['job_id']}")
        except ValueError:
            return 0
        if not folder.is_dir() or rp._is_link(str(folder)):
            return 0
        n = sum(1 for p in folder.iterdir())
        shutil.rmtree(folder, ignore_errors=True)
        return n

    def _remove_orphans(self) -> int:
        """Job folders no live job owns (a dead job's leftovers, a job record that was pruned)."""
        if not self.models_root:
            return 0
        try:
            root = rp.contain(self.models_root, rp.JOBS_SUBDIR)
        except ValueError:
            return 0
        n = 0
        if root.is_dir() and not rp._is_link(str(root)):
            for p in root.iterdir():
                job = self.jobs.get(p.name)
                if p.is_dir() and not rp._is_link(str(p)) and not (job and job["state"] in rp.ACTIVE_STATES):
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
        return n

    async def _model_done(self, path: str, result: dict) -> None:
        """One model's test is over and its engine is stopped: delete the model that was sent for it (job-owned files only), or, if the plan says keep and the test completed, move it into this PC's
        normal models folder and register it like a model added by hand."""
        job = self.active_job()
        if job is None:
            return
        key = os.path.normcase(os.path.abspath(str(path)))
        e = next((x for x in job.get("to_send", []) if os.path.normcase(os.path.abspath(x["path"])) == key), None)
        if e is None:
            return                                                     # a model that was already there is never touched
        keep = job["plan"]["keep_models"] and (result or {}).get("status") == bc.RUN_COMPLETED
        outcome = "deleted"
        try:
            if keep:
                outcome = await asyncio.to_thread(self._publish_keep, job, e)
            else:
                await asyncio.to_thread(self._delete_sent, e)
        except Exception as ex:
            self.log(f"Couldn't finish with a model sent for a benchmark: {type(ex).__name__}")
            outcome = "delete_failed"
        job["after"][e["id"]] = outcome
        self._save(job)

    def _delete_sent(self, e: dict) -> None:
        p = Path(e["path"])
        if p.is_file() and not rp._is_link(str(p)):
            p.unlink()

    def _publish_keep(self, job: dict, e: dict) -> str:
        """Move a sent model out of the job's folder into `<models>/models/sent-from-main-pc/` (same drive, one rename) and register it. A copy with the same bytes already there is reused."""
        src = Path(e["path"])
        plan_model = job["plan"]["models"][e["i"]]
        folder = rp.contain(self.models_root, f"models/{KEPT_FOLDER}")
        folder.mkdir(parents=True, exist_ok=True)
        dest = rp.contain(self.models_root, f"models/{KEPT_FOLDER}/{plan_model['file']}")
        if dest.exists() and (dest.stat().st_size != e["size"] or file_transfer.sha256_file(dest) != e["sha256"]):
            dest = rp.contain(self.models_root, f"models/{KEPT_FOLDER}/{e['sha256'][:16]}-{plan_model['file']}"[:200])
        if dest.exists():
            self._delete_sent(e)
        else:
            os.replace(src, dest)
        if self.register_fn is not None:
            try:
                self.register_fn(dest)
            except Exception as ex:
                self.log(f"Kept a model but couldn't list it: {type(ex).__name__}")
        return "kept"

    # ---- results: list, serve, acknowledge ---------------------------------------------------------------------------------------------------------------------------------
    def _job_runs(self, job: dict) -> list:
        """The runs of a job: finished ones (the runner's answer while the job runs, the saved list afterwards), each with `collectable` (its files are final and still here) and `acknowledged`."""
        if job["state"] in rp.ACTIVE_STATES:
            base = [{"run_id": r.get("run_id"), "model_file": r.get("model"), "state": r.get("status"), "reason": r.get("reason", "")} for r in self.service.status().get("runs", [])]
        else:
            base = [dict(r) for r in job["runs"]]
        acked = set(job.get("acked", []))
        for r in base:
            rid = r.get("run_id")
            r["acknowledged"] = rid in acked
            r["collectable"] = bool(rid) and rid not in acked and self._run_final(rid)
        return base

    def _run_final(self, run_id: str) -> bool:
        if not bc.valid_run_id(run_id):
            return False
        m = bs._read_json(self.runs_dir / run_id / bs.MANIFEST)
        prog = bs._read_json(self.runs_dir / run_id / bs.PROGRESS)
        return bool(m) and m.get("status") in bc.RUN_STATES_FINAL and bool(prog and prog.get("finished"))      # (`finish_run` writes the progress file last: until then a file may still change)

    def _scan_runs(self, job_id: str) -> list:
        """The finished run folders that say they belong to `job_id` (after a restart the job file may not know them)."""
        out = []
        try:
            names = sorted(n for n in os.listdir(self.runs_dir) if bc.valid_run_id(n))
        except OSError:
            return out
        for n in names:
            m = bs._read_json(self.runs_dir / n / bs.MANIFEST)
            if m and (m.get("job") or {}).get("job_id") == job_id and self._run_final(n):
                out.append({"run_id": n, "model_file": m.get("model_file"), "state": m.get("status"), "reason": m.get("reason", "")})
        return out

    def _run_job(self, owner: str, token: str, run_id: str) -> dict:
        """The caller's job that holds `run_id`, or BenchError. Another PC's run is a 403, not a 404, so it never learns more than that."""
        pc = self.pc_name()
        if not bc.valid_run_id(run_id):
            raise BenchError(404, "unknown_run", REFUSE_NO_RUN.format(pc=pc))
        holder = next((j for j in self.jobs.values() if run_id in {r.get("run_id") for r in self._job_runs(j)} or run_id in j.get("acked", [])), None)
        if holder is None:
            raise BenchError(404, "unknown_run", REFUSE_NO_RUN.format(pc=pc))
        return self._owned(owner, token, holder["job_id"])

    def _file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _listing(self, run_id: str) -> list:
        """[{name, size, sha256}] for the files of a finished run. Only the five result names, only plain files (never a link), each under its size cap."""
        try:
            folder = rp.contain(self.runs_dir, run_id)
        except ValueError:
            raise BenchError(404, "unknown_run", REFUSE_NO_RUN.format(pc=self.pc_name()))
        out, total = [], 0
        for name in rp.RESULT_FILES:
            try:
                path = rp.result_file(self.runs_dir, run_id, name)
            except ValueError:
                continue
            if not path.is_file() or rp._is_link(str(path)):
                continue
            size = path.stat().st_size
            total += size
            if size > rp.MAX_RESULT_FILE_BYTES or total > rp.MAX_RESULT_TOTAL_BYTES:
                raise BenchError(413, "too_big", REFUSE_TOO_BIG)
            out.append({"name": name, "size": size, "sha256": self._file_hash(path)})
        if not folder.is_dir() or not any(f["name"] == bs.MANIFEST for f in out):
            raise BenchError(410, "result_expired", REFUSE_EXPIRED.format(pc=self.pc_name()))
        return out

    def run_listing(self, owner: str, token: str, run_id: str) -> dict:
        job = self._run_job(owner, token, run_id)
        self._renew(job)
        if run_id in job.get("acked", []):
            raise BenchError(410, "already_collected", REFUSE_GONE)
        if not self._run_final(run_id):
            raise BenchError(409, "not_collectable", REFUSE_NOT_READY)
        files = self._listing(run_id)
        return {"run_id": run_id, "job_id": job["job_id"], "files": files, "total_bytes": sum(f["size"] for f in files)}

    def run_file(self, owner: str, token: str, run_id: str, name: str) -> Path:
        """The path of one result file to serve (the route streams it, with ranges)."""
        job = self._run_job(owner, token, run_id)
        self._renew(job)
        if not rp.valid_result_filename(name):
            raise BenchError(400, "bad_name", REFUSE_BAD_NAME.format(pc=self.pc_name()))
        if run_id in job.get("acked", []):
            raise BenchError(410, "already_collected", REFUSE_GONE)
        if not self._run_final(run_id):
            raise BenchError(409, "not_collectable", REFUSE_NOT_READY)
        try:
            path = rp.result_file(self.runs_dir, run_id, name)
        except ValueError:
            raise BenchError(400, "bad_name", REFUSE_BAD_NAME.format(pc=self.pc_name()))
        if not path.is_file() or rp._is_link(str(path)) or path.stat().st_size > rp.MAX_RESULT_FILE_BYTES:
            raise BenchError(404, "unknown_run", REFUSE_NO_RUN.format(pc=self.pc_name()))
        return path

    def acknowledge(self, owner: str, token: str, run_id: str, hashes: Any) -> dict:
        """The main PC has the run safely: delete this PC's copy. Only for the owner who sends the checksum of EVERY file it received, and only when they still match what is here. A
        second call on a run already acknowledged is a success."""
        job = self._run_job(owner, token, run_id)
        self._renew(job)
        if run_id in job.get("acked", []):
            return {"ok": True, "already": True, "job_state": job["state"]}
        if not self._run_final(run_id):
            raise BenchError(409, "not_collectable", REFUSE_NOT_READY)
        if not isinstance(hashes, dict) or not hashes:
            raise BenchError(400, "hashes_required", REFUSE_NEED_HASHES)
        here = {f["name"]: f["sha256"] for f in self._listing(run_id)}
        if {str(k): str(v) for k, v in hashes.items()} != here:
            raise BenchError(409, "hash_mismatch", REFUSE_HASHES.format(pc=self.pc_name()))
        try:
            bs.delete_run(self.runs_dir, run_id)
        except ValueError:
            raise BenchError(410, "result_expired", REFUSE_EXPIRED.format(pc=self.pc_name()))
        job.setdefault("acked", []).append(run_id)
        self._maybe_collected(job)
        self._save(job)
        return {"ok": True, "already": False, "job_state": job["state"]}

    def _maybe_collected(self, job: dict) -> None:
        """An ended job whose every run has been acknowledged is `collected` (the owner's move, the only one the owner has)."""
        runs = job.get("runs") or []
        if job["state"] in rp.ENDED_STATES and runs and all(r.get("run_id") in job.get("acked", []) for r in runs if r.get("run_id")):
            job["state"] = rp.move_job(job["state"], rp.J_COLLECTED, rp.ACTOR_OWNER)
            job["state_changed_at"] = _iso(self.wall())

    def status(self, owner: str, token: str, job_id: str = "") -> dict:
        job = self._owned(owner, token, job_id)
        self._renew(job)
        return self.view(job)

    def heartbeat(self, owner: str, token: str, job_id: str) -> dict:
        job = self._owned(owner, token, job_id)
        if job["state"] not in rp.ACTIVE_STATES:
            raise BenchError(409, "illegal_state", REFUSE_ENDED)
        self._renew(job)
        return {"lease_expires_in_s": round(self.lease_s)}

    async def cancel(self, owner: str, token: str, job_id: str) -> dict:
        job = self._owned(owner, token, job_id)
        if job["state"] not in rp.ACTIVE_STATES:
            raise BenchError(409, "illegal_state", REFUSE_ENDED)
        self._renew(job)
        if not job["cancel_requested"]:
            job["cancel_requested"] = True
            self._save(job)
        if self.service.running():
            await self.service.cancel()
        return self.view(job)

    # ---- start-up and housekeeping ---------------------------------------------------------------------------------------------------------------------------------------
    async def reconcile(self, stop_services: Optional[Callable[[list], Awaitable]] = None) -> dict:
        """Before the door accepts a job: whatever a previous Courier left unfinished is reported as interrupted and cleaned up. An engine this Courier started is stopped (it is named in its
        own pid file, and only a llama-server is ever stopped); runs still marked running are marked interrupted; partial model files of a job are removed; a lock whose job is not active
        goes. Nothing is ever resumed."""
        import benchmarks_engine
        out = {"engine_stopped": False, "jobs_interrupted": 0, "runs_marked": 0, "partial_removed": 0}
        out["engine_stopped"] = benchmarks_engine.stop_leftover_engine(self.jobs_dir, self.log)
        for job in list(self.jobs.values()):
            if job["state"] in rp.ACTIVE_STATES:
                if stop_services and job.get("services_started") and not job["plan"].get("keep_started"):
                    try:
                        await stop_services(list(job["services_started"]))
                    except Exception:
                        pass
                job["_rescan"] = True
                self._move(job, rp.J_INTERRUPTED, "courier_restarted", self._reason_line("courier_restarted"))
                out["jobs_interrupted"] += 1
            out["partial_removed"] += self._remove_partials(job["job_id"])
            self._cleanup_job_files(job)
        out["orphans_removed"] = self._remove_orphans()
        out["runs_marked"] = br.mark_unfinished_interrupted(self.runs_dir, self.wall)
        for job in self.jobs.values():                              # the runs an interrupted job had made (finished ones and the one that was running) stay collectable
            if job.pop("_rescan", False):
                job["runs"] = self._scan_runs(job["job_id"])
                self._save(job)
        try:
            holder = json.loads(self.lock_path.read_text(encoding="utf-8")).get("job_id")
        except (OSError, ValueError):
            holder = None
        if self.lock_path.exists() and not (holder in self.jobs and self.jobs[holder]["state"] in rp.ACTIVE_STATES):
            self.lock_path.unlink(missing_ok=True)
        self.prune()
        return out

    def _remove_partials(self, job_id: str) -> int:
        """Half-received model files of a job (`*.partial`, `*.partial.json`) under the models folder's job folder. Contained: nothing outside `benchmark-jobs/<job id>` is touched."""
        models_root = self.models_root
        if not models_root or not rp.valid_job_id(job_id):
            return 0
        try:
            folder = rp.contain(models_root, f"{rp.JOBS_SUBDIR}/{job_id}")
        except ValueError:
            return 0
        n = 0
        if folder.is_dir():
            for p in folder.iterdir():
                if p.is_file() and (p.name.endswith(".partial") or p.name.endswith(".partial.json")) and not rp._is_link(str(p)):
                    try:
                        p.unlink()
                        n += 1
                    except OSError:
                        pass
        return n

    def prune(self) -> None:
        """Bounded retention: an ended job's results stay until acknowledged, but not forever (7 days, 5 jobs, 512 MiB); the oldest go first. A running job is never touched."""
        ended = sorted((j for j in self.jobs.values() if j["state"] in rp.ENDED_STATES), key=lambda j: j.get("ended_epoch_s") or 0)

        def size(j: dict) -> int:
            total = 0
            for r in j.get("runs", []):
                if r.get("run_id") and bc.valid_run_id(r["run_id"]):
                    for p in (self.runs_dir / r["run_id"]).glob("*"):
                        try:
                            total += p.stat().st_size
                        except OSError:
                            pass
            return total
        now, drop = self.wall(), []
        for j in ended:
            if now - (j.get("ended_epoch_s") or now) > RETENTION_S:
                drop.append(j)
        keep = [j for j in ended if j not in drop]
        while len(keep) > MAX_UNCOLLECTED:
            drop.append(keep.pop(0))
        while keep and sum(size(j) for j in keep) > MAX_RESULT_BYTES:
            drop.append(keep.pop(0))
        for j in drop:
            self.log(f"Removed the results of benchmark {j['job_id']}: the main PC never collected them in time")
            self._remove_job(j)
        for j in [j for j in self.jobs.values() if j["state"] == rp.J_COLLECTED and now - (j.get("ended_epoch_s") or now) > COLLECTED_KEEP_S]:
            self._remove_job(j)

    def _remove_job(self, job: dict) -> None:
        for r in job.get("runs", []):
            if r.get("run_id") and bc.valid_run_id(r["run_id"]):
                try:
                    shutil.rmtree(rp.contain(self.runs_dir, r["run_id"]), ignore_errors=True)
                except ValueError:
                    pass
        try:
            self._job_path(job["job_id"]).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        self.jobs.pop(job["job_id"], None)

    async def shutdown(self) -> None:
        """The Courier is closing: a running job ends interrupted (everything measured is kept) and its engine is stopped."""
        job = self.active_job()
        if job is None:
            return
        job["interrupt_reason"] = job["interrupt_reason"] or "courier_stopped"
        await self.service.shutdown()
        rt = self._rt.get(job["job_id"])
        task = rt and rt.get("task")
        if task:
            await asyncio.gather(task, return_exceptions=True)
