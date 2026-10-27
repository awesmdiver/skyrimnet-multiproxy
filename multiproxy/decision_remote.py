"""The local decision model on another device, seen from the main device (2026-10-02, Local decisions step 3): the relay, "where it runs", and installing on a Courier device.
The same shape as `voice_remote.py`:

  * `RemoteDecisionEngine` looks like the local engine manager to `decision_wiring.DecisionHandles`, but a decision goes to the Courier device through the existing paired
    connection (pairing code, home network only). The model on that device listens to that device only: **no new port is opened there**. The waiting limit is counted from when
    MultiProxy received the decision, the relay's own time included. A device that is asleep, unreachable or has the model stopped fails at once, in plain words naming
    it, so the next answerer takes the decision straight away.
  * `RoutedDecisionEngine` is the "where it runs" setting: This device or one named Courier device.
  * `DecisionRemote` installs and manages the decision model on a Courier device from here: the files **from this device** by default (downloaded here once, kept, then copied
    over the shared file transfer: resumable, checksum-verified, real progress; only the pinned files) or "download it on that device", disk checks on both ends, and
    start / stop / restart / uninstall / log. There is no license step (everything it uses is Apache-2.0).
"""
import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

import decision_courier
import download_meter
import decision_engine as de
import file_transfer
from local_models import LocalModelError
from voice_downloads import DownloadStore, InternetSource
from voice_engine import gb


# ---------------------------------------------------------------------------------------------------------------------------------
# The relay
# ---------------------------------------------------------------------------------------------------------------------------------
class RemoteDecisionEngine:
    """The decision model that is on another device. Same face as `decision_engine.DecisionEngine` for what the handles use: `answer`, `installed`, `state`, `status`."""

    def __init__(self, remotes, pc_fn: Callable[[], str], settings_fn: Callable[[], dict], log=lambda m: None):
        self.remotes, self._pc_fn, self._settings_fn, self.log = remotes, pc_fn, settings_fn, log
        self.limit_hits = 0
        self.answered = 0
        self.last_relay_ms: Optional[float] = None       # what the relay added on top of the model's own time, for the last decision
        self.last_ms: Optional[float] = None
        self.waiting = 0

    @property
    def pc(self) -> str:
        return self._pc_fn()

    def _remote(self):
        try:
            return self.remotes._courier(self.pc)
        except Exception:
            return None

    def block(self) -> dict:
        r = self._remote()
        return dict((r.report or {}).get("decisions") or {}) if r else {}

    def reachable(self) -> bool:
        r = self._remote()
        return bool(r and r.client is not None and r.online is not False)

    def has_support(self) -> bool:
        r = self._remote()
        return bool(r and "decisions" in (r.report or {}))

    @property
    def state(self) -> str:
        return self.block().get("state", "unreachable") if self.reachable() else "unreachable"

    def installed(self, key: Optional[str] = None) -> bool:
        b = self.block()
        return bool(b.get("installed")) and (key is None or key in (b.get("installedModels") or [key]))

    def model_label(self) -> str:
        key = self.block().get("model") or de.DEFAULT_MODEL
        return de.MODELS[key].label if key in de.MODELS else key

    def not_ready_message(self) -> str:
        """Why a decision can't go there, in plain words that name the device."""
        pc, r, b = self.pc, self._remote(), self.block()
        if r is None:
            return f"The local decision model is set to run on {pc}, but {pc} isn't in your list of devices anymore. Choose where it runs again."
        if not self.reachable():
            return f"The local decision model runs on {pc}, which isn't answering. It may be asleep or turned off."
        if not self.has_support():
            return f"{pc} has an older MultiProxy Courier that can't run the local decision model."
        if not b.get("installed"):
            return f"The local decision model isn't installed on {pc}."
        return f"The local decision model isn't running on {pc}."

    def limit_seconds(self) -> float:
        """The waiting limit: the player's setting, else the usual for where it runs on that device (it knows: graphics card or system memory)."""
        try:
            setting = float(self._settings_fn().get("limit_seconds") or 0)
        except (TypeError, ValueError):
            setting = 0.0
        run_on = self.block().get("runOn")
        return de.limit_for(run_on or "gpu", setting)

    def status(self) -> dict:
        b, pc = self.block(), self.pc
        if not self.reachable():
            msg = self.not_ready_message()
            return {"state": "unreachable", "label": "Not ready", "detail": msg, "ready": False, "installed": bool(b.get("installed")), "pc": pc,
                    "model": b.get("model"), "install": None, "downloads": {"files": [], "totalBytes": 0, "folder": ""}, "installedModels": b.get("installedModels") or [],
                    "runOn": None, "limitHits": self.limit_hits, "limitSeconds": self.limit_seconds()}
        s = {**b, "pc": pc, "relayMs": self.last_relay_ms, "limitHits": self.limit_hits, "limitSeconds": self.limit_seconds()}
        s["waiting"] = self.waiting
        if s.get("state") == "ready" and self.waiting:
            s["label"] = f"Busy ({self.waiting} decision{'s' if self.waiting != 1 else ''} waiting)"
        if self.last_ms:
            s["lastAnswerMs"] = round(self.last_ms)
        return s

    async def answer(self, state, questions: dict, received: Optional[float] = None) -> dict:
        """{"answers", "usage"} from the model on that device, or DecisionNotReady (at once: unreachable, not installed, stopped) / DecisionTooSlow / Unanswerable."""
        if not self.reachable() or not self.block().get("installed") or self.block().get("state") != "ready":
            raise de.DecisionNotReady(self.not_ready_message())
        limit = self.limit_seconds()
        now = time.time()
        remaining = limit - (now - (received if received is not None else now))
        if remaining <= 0.02:
            self.limit_hits += 1
            raise de.DecisionTooSlow(limit)
        r = self._remote()
        self.waiting += 1
        t0 = time.perf_counter()
        try:
            res = await r.client.decisions_answer(state, questions, remaining)
        except de.DecisionTooSlow:
            self.limit_hits += 1
            raise de.DecisionTooSlow(limit)
        except (asyncio.TimeoutError, TimeoutError):
            self.limit_hits += 1                     # the wait ran out while that device was still working
            raise de.DecisionTooSlow(limit)
        except (aiohttp.ClientError, OSError) as e:
            self.log(f"The decision relay to {self.pc} failed: {e.__class__.__name__}")
            raise de.DecisionNotReady(f"The local decision model runs on {self.pc}, but it stopped answering. It might be asleep or turned off.")
        finally:
            self.waiting -= 1
        wall = (time.perf_counter() - t0) * 1000
        self.last_ms = wall
        engine_ms = res.pop("engineMs", None)
        self.last_relay_ms = max(0.0, wall - float(engine_ms)) if engine_ms is not None else None
        self.answered += 1
        return res


class RoutedDecisionEngine:
    """"Where it runs": This device (`location` is "this") or the Courier device with that nickname."""

    def __init__(self, local: de.DecisionEngine, remote: RemoteDecisionEngine, location_fn: Callable[[], str]):
        self.local, self.remote, self._location_fn = local, remote, location_fn

    @property
    def location(self) -> str:
        return self._location_fn() or "this"

    def active(self):
        return self.local if self.location == "this" else self.remote


# ---------------------------------------------------------------------------------------------------------------------------------
# Installing and managing the decision model on a Courier device
# ---------------------------------------------------------------------------------------------------------------------------------
@dataclass
class RemoteInstall:
    pc: str
    source: str                      # "gaming-pc" | "that-pc"
    runner_only: bool = False        # only the runner software (the model is already on that device)
    state: str = "running"           # running | done | error | cancelled
    step: str = "engine"             # engine | model | warmup: the card's three steps
    stage: str = "preparing"         # preparing (downloading here) | copying | installing | done
    message: str = ""
    done: int = 0
    total: int = 0
    speed: float = 0.0
    error: str = ""
    cancel: bool = False
    trail: list = field(default_factory=list)       # every (stage, step, message) it went through, in order (the screen shows the current one; tests read them all)
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    meter: Any = field(default_factory=download_meter.Meter, repr=False)      # fed with the running total each time the screen asks: the time left comes from it

    def note(self, stage: str, step: str, message: str) -> None:
        self.stage, self.step, self.message = stage, step, message
        self.trail.append((stage, step, message))

    def to_json(self) -> dict:
        self.meter.observe(self.done)
        return {"pc": self.pc, "source": self.source, "runnerOnly": self.runner_only, "state": self.state, "stage": self.stage, "step": self.step, "message": self.message, "done": self.done,
                "total": self.total, "percent": int(self.done * 100 / self.total) if self.total else 0, "speed_bps": int(self.speed), "error": self.error,
                "left": self.meter.text(self.done, self.total) if self.state == "running" and self.stage != "installing" and self.step != "warmup" else ""}


class _Cancelled(Exception):
    pass


STEP_OF_ROLE = {"engine": "engine", "model": "model"}


class DecisionRemote:
    def __init__(self, remotes, engine: de.DecisionEngine, store: DownloadStore, this_pc_fn: Callable[[], str], log,
                 source_factory: Optional[Callable[[object], object]] = None, settings_fn: Callable[[], dict] = lambda: {}, local=None):
        self.remotes, self.engine, self.store, self._this_pc_fn, self.log = remotes, engine, store, this_pc_fn, log
        self.local = local                                            # this device's Local models (its installed runner can be copied to another device)
        self._source_factory = source_factory or de.source_for        # where files come from when this device doesn't have them yet (the internet; a test folder)
        self._settings_fn = settings_fn
        self.jobs: dict[str, RemoteInstall] = {}

    # ---- what is known about that device ------------------------------------------------------------------------------------------------
    async def _refresh(self, r) -> None:
        try:
            self.remotes._apply_report(r, await r.client.fetch_report())
        except Exception:
            pass

    @staticmethod
    def _name_the_pc(text: str, pc: str) -> str:
        return text.replace("this device", pc).replace("that drive", f"{pc}'s drive")

    async def describe(self, pc: str) -> dict:
        r = self.remotes._courier(pc)
        out = {"pc": r.id, "online": r.online is not False, "job": self.jobs[r.id].to_json() if r.id in self.jobs else None,
               "decisions": (r.report or {}).get("decisions"), "keptBytes": self.store.listing()["totalBytes"]}
        if r.online is not False:
            try:
                await self._push_settings(r)
                info = await r.client.decisions_get("fit")
                info["fit"]["reasons"] = [self._name_the_pc(x, r.label) for x in info["fit"].get("reasons", [])]
                info["fit"]["warnings"] = [self._name_the_pc(x, r.label) for x in info["fit"].get("warnings", [])]
                out["fit"] = info
            except Exception as e:
                out["fit"] = None
                out["fitError"] = f"{r.label} didn't answer ({getattr(e, 'message', e.__class__.__name__)})."
        return out

    async def _push_settings(self, r) -> None:
        """What the player chose on this device (the model, where it runs, the waiting limit) goes to that device, which makes its own choice from it."""
        s = self._settings_fn()
        await r.client.decisions_post("settings", {"model": s.get("model"), "runOn": s.get("run_on"), "limitSeconds": s.get("limit_seconds") or 0})

    async def push_settings(self, pc: str) -> None:
        r = self.remotes._courier(pc)
        try:
            await self._push_settings(r)
        except Exception as e:
            raise de.DecisionError(getattr(e, "status", 502), getattr(e, "message", f"{r.label} didn't answer."))
        await self._refresh(r)

    # ---- install ----------------------------------------------------------------------------------------------------------------------
    def start_install(self, pc: str, source: str = "gaming-pc", runner_only: bool = False) -> RemoteInstall:
        r = self.remotes._courier(pc)
        if source not in ("gaming-pc", "that-pc"):
            raise de.DecisionError(400, "Unknown way to get the files.")
        live = self.jobs.get(r.id)
        if live and live.state == "running":
            return live
        job = RemoteInstall(r.id, source, runner_only=runner_only)
        self.jobs[r.id] = job
        job.task = asyncio.ensure_future(self._run(r, job))
        return job

    def cancel(self, pc: str) -> bool:
        job = self.jobs.get(self.remotes._courier(pc).id)
        if job and job.state == "running":
            job.cancel = True
            return True
        return False

    def _gaming_free(self) -> Optional[int]:
        p = self.store.folder
        while not p.exists() and p.parent != p:
            p = p.parent
        try:
            return shutil.disk_usage(p).free
        except OSError:
            return None

    async def _run(self, r, job: RemoteInstall) -> None:
        try:
            await self._push_settings(r)
            info = await r.client.decisions_get("fit")
            fit = info["fit"]
            if fit["verdict"] == "refuse":
                raise de.DecisionError(400, self._name_the_pc(" ".join(fit["reasons"]), r.label))
            if job.runner_only:
                info = {**info, "files": [f for f in info["files"] if f.get("role") == "engine"]}         # the runner alone: the model's files are never copied again
            if job.source == "gaming-pc":
                if await self._try_engine_copy(r, job, info):
                    info = {**info, "files": [f for f in info["files"] if f.get("role") != "engine"]}       # the runner is there now: only the model's files are left to send
                await self._get_files_over(r, job, info)
            else:
                job.note("installing", "engine" if job.runner_only else "model", f"{r.label} is downloading the " + ("engine software" if job.runner_only else "decision model's files") + " itself…")
            await self._install_there(r, job, "kept" if job.source == "gaming-pc" else "internet")
            job.state, job.stage, job.message = "done", "done", ""
            self.log(f"The local decision model is installed on {r.label}")
        except asyncio.CancelledError:
            job.state = "cancelled"
            raise
        except (_Cancelled, file_transfer.SendCancelled):
            job.state, job.message = "cancelled", ""
            try:
                await r.client.decisions_post("cancel")
            except Exception:
                pass
        except file_transfer.TransferError as e:
            job.state, job.error = "error", e.message.replace("{pc}", r.id)
        except (de.DecisionError, LocalModelError) as e:
            job.state, job.error = "error", self._name_the_pc(de.plain_install_error(e.message), r.label)
        except Exception as e:
            job.state, job.error = "error", f"Setting up the local decision model on {r.label} stopped ({getattr(e, 'message', e.__class__.__name__)})."
            self.log(f"The decision model install on {r.label} failed: {e.__class__.__name__}: {e}")
        if job.state == "error":
            self.log(f"The decision model install on {r.label} failed: {job.error}")
        await self._refresh(r)

    def _known_specs(self) -> dict:
        specs = {f.name: f for m in self.engine.models.values() for f in m.files()}
        specs.update(decision_courier.every_engine_spec())
        return specs

    async def _try_engine_copy(self, r, job: RemoteInstall, info: dict) -> bool:
        """Send this device's own installed runner to that device instead of downloading one: only when that device is a new Courier on Windows like this one, would download the
        same kind of engine, and this device's engine (a build the decision model can use) is that kind. True when it arrived; False for any reason it did not (a cancel
        excepted): the usual way then runs. The zip of the engine folder is made here, sent over the same transfer as the model files, and removed here."""
        local = self.local
        wanted = [f for f in info.get("files", []) if f.get("role") == "engine" and not f.get("have")]
        if not wanted or local is None or not info.get("engineCopy") or info.get("engineSystem") != "win" or local._system_key() != "win":
            return False
        kind = info.get("engineKind")
        eng = (local._cfg().get("engine") or {})
        if not kind or eng.get("kind") != kind or not local.engine_exe_current():
            return False
        zip_path = None
        try:
            job.note("preparing", "engine", "Packing the engine software on this device…")
            zip_path = self.store.folder / ".engine-copy-packing.zip"
            build, kind = await asyncio.to_thread(local.pack_engine, zip_path)
            name = decision_courier.engine_copy_name(build, kind)
            final = self.store.folder / name
            zip_path.replace(final)
            zip_path = final
            if job.cancel:
                raise _Cancelled()
            size = zip_path.stat().st_size
            sha = await asyncio.to_thread(file_transfer.sha256_file, zip_path)
            job.note("copying", "engine", f"Copying the engine software from this device to {r.label}…")
            job.done, job.total = 0, size
            tspec = {"kind": "decision", "model": "decision-model", "file": name, "sha256": sha, "size": size, "role": decision_courier.ENGINE_COPY_ROLE}

            def progress(done, total, speed):
                job.done, job.speed = done, speed

            def paused(msg):
                job.message = f"Waiting for {r.label} to answer. It will finish on its own."
            await file_transfer.send_file(r.client, tspec, zip_path, progress, lambda: job.cancel, on_pause=paused)
            self.log(f"The engine software ({build}, {kind}) was copied from this device to {r.label} instead of downloading it there")
            return True
        except (_Cancelled, file_transfer.SendCancelled, asyncio.CancelledError):
            raise
        except Exception as e:
            self.log(f"The engine software could not be copied to {r.label} ({getattr(e, 'message', e.__class__.__name__)}). Using the usual download instead")
            return False
        finally:
            for p in (self.store.folder / ".engine-copy-packing.zip", zip_path):
                if p is not None:
                    Path(p).unlink(missing_ok=True)
            job.done, job.total, job.speed = 0, 0, 0.0

    async def _get_files_over(self, r, job: RemoteInstall, info: dict) -> None:
        """The files that device needs: fetched here first when they aren't kept yet (once), then copied to that device."""
        specs = self._known_specs()
        wanted = []
        for f in info["files"]:
            spec = specs.get(f["name"])
            if spec is None or spec.size != f["size"] or spec.sha256 != f["sha256"]:
                raise de.DecisionError(409, f"{r.label} has a different version of MultiProxy Courier. Update it (use the newest MultiProxy Courier download) and try again.")
            if not f["have"]:
                wanted.append((spec, f["role"]))
        if not wanted:
            return
        need_there = sum(s.size for s, _ in wanted)
        free_there = info.get("freeDiskBytes")
        if free_there is not None and free_there < need_there + file_transfer.SPACE_MARGIN_BYTES:
            raise de.DecisionError(507, f"{r.label} doesn't have room for this. It needs {gb(need_there)} more, but only has {gb(free_there)} free.")
        need_here = self.store.missing_bytes([s for s, _ in wanted])
        free_here = self._gaming_free()
        if need_here and free_here is not None and free_here < need_here + file_transfer.SPACE_MARGIN_BYTES:
            raise de.DecisionError(507, f"This device doesn't have room for the decision model's files. They need {gb(need_here)}, but only {gb(free_here)} is free. "
                                        f"You can let {r.label} download them itself instead.")
        role_of = {s.name: role for s, role in wanted}
        if need_here:
            job.total, job.done = need_here, 0
            job.note("preparing", "model", "Downloading the decision model's files on this device (once)…")

            def on_bytes(n):
                if job.cancel:
                    raise _Cancelled()
                job.done = max(0, job.done + n)
            for spec, _ in wanted:
                if not self.store.has(spec):
                    source = self._source_factory(spec)
                    if isinstance(source, InternetSource):
                        source.log = self.log
                        source.what = "the engine software" if role_of.get(spec.name) == "engine" else "the decision model's files"
                    await self.store.get(spec, source, on_bytes)
        job.done, job.total = 0, need_there
        base = 0
        for spec, role in wanted:
            word = {"engine": "engine software", "model": "decision model"}.get(role, "decision model")
            job.note("copying", STEP_OF_ROLE.get(role, "model"), f"Copying the {word} from this device to {r.label}…")
            tspec = {"kind": "decision", "model": "decision-model", "file": spec.name, "sha256": spec.sha256, "size": spec.size, "role": role}

            def progress(done, total, speed, base=base):
                job.done, job.speed = base + done, speed

            def paused(msg):
                job.message = f"Waiting for {r.label} to answer. It will finish on its own."
            await file_transfer.send_file(r.client, tspec, self.store.path_of(spec.name), progress, lambda: job.cancel, on_pause=paused)
            base += spec.size
        job.done = job.total

    async def _install_there(self, r, job: RemoteInstall, source: str) -> None:
        job.note("installing", "engine", f"Setting up the local decision model on {r.label}…")
        job.done, job.total, job.speed = 0, 0, 0.0
        await r.client.decisions_post("install", {"source": source, **({"runnerOnly": True} if job.runner_only else {})})
        while True:
            if job.cancel:
                raise _Cancelled()
            st = await r.client.decisions_get("status")
            inst = st.get("install") or {}
            if inst.get("step"):
                job.step = inst["step"]
            job.done, job.total = int(inst.get("done") or 0), int(inst.get("total") or 0)
            if inst.get("state") == "done" or (inst.get("state") is None and st.get("ready")):
                break
            if inst.get("state") in ("error", "cancelled"):
                raise de.DecisionError(502, inst.get("error") or f"The local decision model didn't install on {r.label}.")
            await asyncio.sleep(1.0)

    # ---- the rest of the engine's actions, on that device -------------------------------------------------------------------------------------
    async def act(self, pc: str, action: str) -> dict:
        if action not in ("start", "stop", "restart", "uninstall", "cancel"):
            raise de.DecisionError(400, "Unknown action.")
        r = self.remotes._courier(pc)
        try:
            if action in ("start", "restart"):
                await self._push_settings(r)
            out = await r.client.decisions_post(action, {}, timeout=300 if action in ("start", "restart") else 60)
        except Exception as e:
            raise de.DecisionError(getattr(e, "status", 502), getattr(e, "message", f"{r.label} didn't answer."))
        await self._refresh(r)
        return out

    async def engine_log(self, pc: str) -> str:
        r = self.remotes._courier(pc)
        return (await r.client.decisions_get("log")).get("log", "")
