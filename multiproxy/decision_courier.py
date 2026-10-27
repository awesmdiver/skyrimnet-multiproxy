"""The local decision model on a Courier device (2026-10-02, Local decisions step 3): the Courier manages the decision model on its own device with the same engine
manager the main device uses (`decision_engine.DecisionEngine`), the way `voice_courier.py` does for Higgs voice.

What the Courier device does here, all behind the pairing code and the home-network rule like everything else the Courier answers:

  * runs the engine manager (fit check, install, start, stop, restart, uninstall, status, log). The model listens to THIS device only (a loopback port from 18900 to 18949);
    **no new port is opened**: the main device reaches it through the Courier's one door.
  * decides where it runs (Automatic / Graphics card / System memory) on THIS device, from its own free graphics memory: the main device only passes the player's choice along.
  * takes the files **from the main device** by default ("download once, share at home"): the main device pushes them through the shared file transfer (`file_transfer.py`,
    kind `decision`); this module only decides where each file goes and refuses anything that isn't one of the pinned files (name, size and checksum all have to match).
    The player may instead choose "download it on that device". The decision model's file is copied or downloaded as it is.
  * answers a relayed decision: `answer` takes the state and questions, the time left (the main device counts the wait from when it received them) and answers, or says
    plainly why it can't (too slow, not running, can't answer honestly), so the main device hands the decision to the next answerer at once.

Nothing here knows about MultiProxy's web app; `courier.py` only wires the routes.
"""
import asyncio
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

from aiohttp import web

import decision_engine as de
import file_transfer
import local_models
from local_models import LocalModelError
from voice_courier import NotCopiedYet
from voice_downloads import DownloadStore, FileSpec, link_or_copy

STABLE_STATES = ("ready", "stopped", "not_installed", "died", "error")
STATUS_CACHE_SECONDS = 3.0           # the engine's status asks the graphics card for its memory use: not on every report
NOT_COPIED = "{name} hasn't been copied to this device yet. Start the install from your main device."


def _error_body(kind: str, message: str, **extra) -> dict:
    return {"error": {"type": kind, "message": message, **extra}}


def engine_specs(kind: str) -> list:
    """The llama.cpp engine archives for one engine kind on this device's operating system, as pinned files."""
    return [FileSpec(n, local_models.engine_url(local_models.LLAMA_CPP_BUILD, n), s, h) for n, s, h in local_models.engine_files_table().get(kind, [])]


# The runner copied from the main device (2026-10-23): one zip of the main device's own installed runner folder. It is the one file this device would accept that is not pinned by
# checksum: the name has to be exactly this shape, the build at least the minimum, the kind one this device's own table lists, Windows only, and no bigger than 6 GB.
# SWITCHED OFF (security review of the 4.0 release, 2026-10-24, Must fix M1): the zip holds programs, and the only thing that vouches for it is the pairing code, which crosses the
# home network in the clear. Every other file this device takes from the main device is pinned here by name, size and SHA-256, so a stranger who learned the code can't make it
# run a program of their own choosing. Turn this on only when the copy can be checked against something this device knows (a signature, or the pinned archive itself).
ENGINE_COPY_ENABLED = False
ENGINE_COPY_NAME = re.compile(r"^engine-copy-b(\d+)-([a-z0-9][a-z0-9.\-]{0,30})\.zip$")
ENGINE_COPY_MAX_BYTES = 6 * 1024 ** 3
ENGINE_COPY_ROLE = "engine-copy"


def engine_copy_name(build: str, kind: str) -> str:
    return f"engine-copy-{build}-{kind}.zip"


def parse_engine_copy(name: str):
    """(build, kind) for a name of the engine-copy shape that this device accepts (Windows, build at least the minimum, a kind from its own table), else None."""
    m = ENGINE_COPY_NAME.match(str(name or ""))
    if not ENGINE_COPY_ENABLED or not m or os.name != "nt" or int(m.group(1)) < local_models.NATIVE_DECISION_MIN_BUILD or m.group(2) not in local_models.engine_files_table():
        return None
    return f"b{m.group(1)}", m.group(2)


def every_engine_spec() -> dict:
    """Every engine archive this version knows, for every operating system: a Windows main device sends a Linux Courier its Linux build."""
    out = {}
    for table in (local_models.ENGINE_FILES, local_models.ENGINE_FILES_LINUX, local_models.ENGINE_FILES_LINUX_ARM64):
        for rows in table.values():
            for n, s, h in rows:
                out[n] = FileSpec(n, local_models.engine_url(local_models.LLAMA_CPP_BUILD, n), s, h)
    return out


class CourierDecisions:
    def __init__(self, folder_fn: Callable[[], Path], settings, log, local: Optional[local_models.LocalModels] = None, ensure_folder: Callable[[], None] = lambda: None,
                 changed: Callable[[], None] = lambda: None, engine: Optional[de.DecisionEngine] = None, store: Optional[DownloadStore] = None,
                 gpu_choice_fn: Callable[[], Optional[dict]] = lambda: None, cards_fn: Optional[Callable[[], list]] = None, redact: Callable[[str], str] = lambda s: s):
        """`settings` has `.data` and `.save()` (the Courier's settings file): the player's choices live under its `decisions` key."""
        self.settings, self.log, self.changed, self.local, self._ensure_folder = settings, log, changed, local, ensure_folder
        self._folder_fn = folder_fn
        self._models = engine.models if engine else de.MODELS          # (the engine manager asks for the choices while it is being made)
        self.store = store or (engine.store if engine else DownloadStore(lambda: Path(folder_fn()) / "downloads"))
        self.mode = "kept"                                # where the files for the install in progress come from: "kept" (what the main device sent) | "internet"
        self.engine = engine or de.DecisionEngine(folder_fn, self.store, self.choices, lambda: self.local.engine_exe_current() if self.local else None, self._ensure_engine,
                                                   self._engine_kind, source_fn=self._source,
                                                   gpu_choice_fn=gpu_choice_fn, redact=redact, **({"cards_fn": cards_fn} if cards_fn else {}))
        self._cache: Optional[tuple] = None

    # ---- the player's choices (model, where it runs, the waiting limit), kept in the Courier's settings --------------------------------------------------------
    def choices(self) -> dict:
        raw = self.settings.data.get("decisions")
        raw = raw if isinstance(raw, dict) else {}
        model = raw.get("model") if raw.get("model") in self._models else de.DEFAULT_MODEL
        run_on = raw.get("run_on") if raw.get("run_on") in de.RUN_ON_CHOICES else "automatic"
        try:
            limit = max(0.0, float(raw.get("limit_seconds") or 0))
        except (TypeError, ValueError):
            limit = 0.0
        return {"model": model, "run_on": run_on, "limit_seconds": limit}

    def set_choices(self, model: Optional[str] = None, run_on: Optional[str] = None, limit_seconds=None) -> dict:
        cur = self.choices()
        if model is not None:
            if model not in self._models:
                raise de.DecisionError(400, "This device doesn't recognize that decision model.")
            cur["model"] = model
        if run_on is not None:
            if run_on not in de.RUN_ON_CHOICES:
                raise de.DecisionError(400, "That isn't a valid choice for where it runs.")
            cur["run_on"] = run_on
        if limit_seconds is not None:
            try:
                v = float(limit_seconds or 0)
            except (TypeError, ValueError):
                raise de.DecisionError(400, "Enter a number of seconds.")
            if v < 0 or (0 < v < de.ASSUMED_LIMIT_FLOOR):
                raise de.DecisionError(400, f"Enter {de.ASSUMED_LIMIT_FLOOR:g} seconds or more, or 0 for the default.")
            cur["limit_seconds"] = v
        self.settings.data["decisions"] = cur
        self.settings.save()
        self.invalidate()
        return cur

    # ---- the llama.cpp engine: this device's own (an existing one, or the one Local models downloads) ----------------------------------------------------------
    def _engine_kind(self) -> str:
        return str((self.local._cfg().get("engine") or {}).get("kind") or "") if self.local is not None else ""

    def _source(self, spec: FileSpec):
        return NotCopiedYet() if self.mode == "kept" else de.source_for(spec)

    def _engine_copy_waiting(self, kind: str):
        """(path, build) of a runner the main device sent for this kind of engine, if one is here."""
        try:
            for p in self.store.path_of("x").parent.glob("engine-copy-*.zip"):
                got = parse_engine_copy(p.name)
                if got and got[1] == kind:
                    return p, got[0]
        except OSError:
            pass
        return None

    async def engine_kind_needed(self) -> Optional[str]:
        """The engine build this device would download, or None when it already has one that is new enough for llama.cpp's own decision API."""
        if self.local is None or self.local.engine_exe_current():
            return None
        return self._engine_kind() or await self.local.default_engine_kind()

    async def _ensure_engine(self, on_progress) -> Path:
        local = self.local
        exe = local.engine_exe_current()
        if exe:
            return exe
        if not local.folder:
            self._ensure_folder()
        kind = self._engine_kind() or await local.default_engine_kind()
        copy = self._engine_copy_waiting(kind)
        if copy is not None:                              # the main device sent its own runner: unpack it here and the download is not needed
            path, build = copy
            try:
                await asyncio.to_thread(local.install_engine_copy, path, build, kind)
                self.log.info(f"The engine software ({build}, {kind}) was copied from the main device instead of downloading it here")
            except LocalModelError as e:                  # not usable: the usual way (download, or the pinned files) is what runs next
                self.log.warning(f"The engine software copied from the main device could not be used ({e.message}). Using the usual download instead")
            path.unlink(missing_ok=True)
            exe = local.engine_exe_current()
            if exe:
                return exe
        if self.mode == "kept":                           # the archives the main device sent go where the engine download looks first: it then finds them and skips the download
            dl = local.folder / "engine" / "_downloads"
            for spec in engine_specs(kind):
                if not self.store.has(spec):
                    raise de.DecisionError(409, NOT_COPIED.format(name=spec.name))
                await asyncio.to_thread(link_or_copy, self.store.path_of(spec.name), dl / spec.name)
        job = local.download_engine(kind)
        while job.state == "running":
            on_progress(job.done, job.total)
            await asyncio.sleep(0.5)
        exe = local.engine_exe_current()
        if job.state != "done" or not exe:
            raise de.DecisionError(502, job.error or "The engine software didn't download.")
        return exe

    # ---- which files, and where they go --------------------------------------------------------------------------------------------
    def _specs(self) -> dict:
        specs = {f.name: f for m in self.engine.models.values() for f in m.files()}
        specs.update(every_engine_spec())
        return specs

    def resolve_decision_dest(self, spec: dict) -> Path:
        """Where a decision file sent from the main device goes: only the pinned files, only with the size and checksum this version expects."""
        name = str(spec.get("file", ""))
        if spec.get("role") == ENGINE_COPY_ROLE or ENGINE_COPY_NAME.match(name):
            try:
                size = int(spec.get("size"))
            except (TypeError, ValueError):
                size = -1
            if spec.get("role") != ENGINE_COPY_ROLE or parse_engine_copy(name) is None or not 0 < size <= ENGINE_COPY_MAX_BYTES:
                raise file_transfer.TransferError(400, "This device can't take that copy of the engine software.")
            return self.store.path_of(name)
        known = self._specs().get(name)
        if not known:
            raise file_transfer.TransferError(400, "MultiProxy doesn't send files with that name.")
        try:
            size = int(spec.get("size"))
        except (TypeError, ValueError):
            size = -1
        if size != known.size or str(spec.get("sha256", "")).lower() != known.sha256:
            raise file_transfer.TransferError(400, "This version of the decision model expects a different file.")
        return self.store.path_of(known.name)

    def on_decision_file_done(self, inc) -> None:
        if parse_engine_copy(inc.file):
            self.log.info(f"The engine software ({inc.file}) was copied here from the main device")
            self.invalidate()
            return
        known = self._specs().get(inc.file)
        if known:
            self.store.adopt(known)
        self.invalidate()

    async def files_needed(self) -> dict:
        """What installing needs here and what is already here, for the chosen model: the fit check, the files (the model, and the llama.cpp engine when
        this device has none) and the room. The main device sets the model choice first (`set_choices`), so this answers for that one."""
        fit = await asyncio.to_thread(self.engine.fit_check)
        hw = await asyncio.to_thread(self.engine.hardware)
        spec = self.engine.spec()
        files = [{"name": f.name, "size": f.size, "sha256": f.sha256, "have": self.store.has(f), "role": role}
                 for f, role in ((spec.model, "model"),)]
        kind = await self.engine_kind_needed()
        if kind:
            files += [{"name": f.name, "size": f.size, "sha256": f.sha256, "have": self.store.has(f), "role": "engine"} for f in engine_specs(kind)]
        return {"fit": fit, "files": files, "freeDiskBytes": hw.free_disk_bytes, "installed": self.engine.installed(), "model": spec.key, "engineKind": kind,
                **self.engine_copy_facts()}

    @staticmethod
    def engine_copy_facts() -> dict:
        """What the main device needs to know before it sends its own runner here: can this device take one (Windows only) and which kind of system it is."""
        return {"engineCopy": ENGINE_COPY_ENABLED and os.name == "nt", "engineSystem": local_models.LocalModels._system_key()}

    # ---- install and run (the engine manager's own actions) -----------------------------------------------------------------------
    def start_install(self, source: str = "kept", runner_only: bool = False) -> dict:
        if source not in ("kept", "internet"):
            raise de.DecisionError(400, "Unknown way to get the files.")
        self.mode = source
        self.engine.start_runner_install() if runner_only else self.engine.start_install()
        self.invalidate()
        return self.status_json(fresh=True)

    # ---- what the report says ------------------------------------------------------------------------------------------------------
    def invalidate(self) -> None:
        self._cache = None
        self.changed()

    def status_json(self, fresh: bool = False, receiving: Optional[dict] = None) -> dict:
        now = time.monotonic()
        busy = self.engine.job is not None and self.engine.job.state == "running"
        if not fresh and not busy and self._cache and now - self._cache[0] < STATUS_CACHE_SECONDS:
            base = self._cache[1]
        else:
            base = self.engine.status()
            if base.get("state") in STABLE_STATES:               # a state that is still changing (starting, installing...) is never remembered
                self._cache = (now, base)
        return {**base, "settings": self.choices(), "receiving": receiving or {}, **self.engine_copy_facts()}

    # ---- a decision, relayed ---------------------------------------------------------------------------------------------------------
    async def answer(self, request: web.Request) -> web.Response:
        """POST /courier/v1/decisions/answer: {state, questions, limit} in; {answers, usage, engineMs} out, or a plain error with a `type` the main device understands
        (too_slow, not_running, unanswerable, error). `limit` is the time left of the waiting limit, counted from when MultiProxy received the decision."""
        try:
            body = await request.json()
        except ValueError:
            return web.json_response(_error_body("error", "That wasn't a decision request."), status=400)
        questions = (body or {}).get("questions")
        if not isinstance(questions, dict) or not questions:
            return web.json_response(_error_body("error", "There were no questions."), status=400)
        try:
            limit = float(body["limit"]) if body.get("limit") is not None else None
        except (TypeError, ValueError):
            limit = None
        t0 = time.perf_counter()
        try:
            res = await self.engine.answer(body.get("state"), questions, limit=limit)
        except de.DecisionTooSlow as e:
            return web.json_response(_error_body("too_slow", e.message, limit=e.limit), status=503)
        except de.DecisionNotReady as e:
            return web.json_response(_error_body("not_running", e.message), status=503)
        except de.Unanswerable as e:
            return web.json_response(_error_body("unanswerable", e.reason), status=422)
        except de.DecisionError as e:
            return web.json_response(_error_body("error", e.message), status=e.status)
        return web.json_response({**res, "engineMs": round((time.perf_counter() - t0) * 1000, 1)})

    async def close(self) -> None:
        await self.engine.close()
