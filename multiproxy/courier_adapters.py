"""MultiProxy Courier: one adapter per AI program on this device (2026-10-01).

Each adapter says what's installed and running, lists that program's models, loads / unloads /
deletes / downloads them, starts it, and hands back the address chat requests should be forwarded to.
A device that really runs more than one program reports each one separately (director's decision).

  Ollama     - its own HTTP API. Loaded models report how much of each sits on the card.
  LM Studio  - its command-line tool (`lms`), which isn't on the PATH unless the player clicked
               "Install CLI" in LM Studio, so the usual folders are searched too.
  llama.cpp  - an existing `llama-server`, or MultiProxy's built-in engine; we run it ourselves
               through local_models.py (the same code the full MultiProxy uses).

Every state is one of: ready, busy, loading, error, stopped.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess

import hidden_process  # every program started from here goes through it: no console window flashing up
import native_probe
import poll_cache  # the program lookup everything shares: also searches Windows' saved PATH, so a program installed after this one started is found
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

import courier_models
import local_models
import program_uninstall
from local_models import LocalModelError

STOP_NOTE = ("{program} is running as something Courier isn't allowed to stop (a Windows service, or set to restart itself). "
             "Close it on that device.")
STATES = ("ready", "busy", "loading", "error", "stopped")
KEEP_FOREVER = -1                   # Ollama's keep_alive: stay loaded until told to unload
PROBE_SECONDS = 0.6                 # "is it running?" on this same device answers in milliseconds; a program that isn't running must not
                                    # slow every report down (Windows takes ~2 s to refuse a connection to a closed port)

# How often each program is really asked, when nothing is going on. Before this, every report (the Courier builds one a second while
# a main device is connected) re-listed every model: LM Studio's own log filled up non-stop and `lms` was started twice a second.
IDLE_PROBE_SECONDS = 10.0           # "is it answering?" (never lists models)
LOADED_SECONDS = 30.0               # which models are loaded: a model loaded by hand in the program still shows up within this
DOWNLOADED_SECONDS = 300.0          # which models are downloaded
BUSY_SECONDS = 2.0                  # while a load or a download is under way the faster rhythm is fine; it drops back when it ends
ERROR_RETRY_SECONDS = 15.0          # a check that failed isn't repeated every second either


MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
BAD_MODEL_NAME = "MultiProxy can't download that model name. Use the exact name the program lists, like gemma3:12b."


def check_model_name(model: str) -> str:
    """A model name asked for from another device goes to a program's command line or web call, so it must look like a model name: letters, digits and . _ : / @ + -, starting
    with a letter or digit (never a leading '-', which a command-line tool would read as an option), no spaces, no backslash, no '..', a sane length."""
    name = (model or "").strip()
    if not MODEL_NAME_RE.fullmatch(name) or ".." in name or "//" in name:
        raise AdapterError(400, BAD_MODEL_NAME)
    return name


class AdapterError(Exception):
    """A plain, player-readable reason something didn't work, with an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Download:
    model: str
    state: str = "running"          # running | done | error | cancelled
    done: int = 0
    total: int = 0
    progress_known: bool = True     # False: the program doesn't report progress (an honest "Downloading..." with no bar)
    unit_bytes: bool = True         # done/total are bytes (False: LM Studio's tool only gives a percentage, kept as per-mille)
    error: str = ""
    task: Optional[asyncio.Task] = None

    def to_json(self) -> dict:
        pct = int(self.done * 100 / self.total) if self.progress_known and self.total else (100 if self.state == "done" else None)
        return {"model": self.model, "state": self.state, "done": self.done, "total": self.total,
                "percent": pct, "progress_known": self.progress_known, "bytes": self.unit_bytes, "error": self.error}


def _tail_ints(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", text or "")]


class _Check:
    """One remembered answer: when it was got, and (for a failure) what went wrong. The lock makes everyone asking at once share one check."""
    __slots__ = ("value", "at", "error", "lock")

    def __init__(self):
        self.value: Any = None
        self.at: Optional[float] = None
        self.error: Optional[tuple[Exception, float]] = None
        self.lock = asyncio.Lock()


class Adapter:
    id = ""
    name = ""
    # what the desktop may offer for this program (the report says so; a button is never a guess)
    capabilities: dict = {}

    def __init__(self, log):
        self.log = log
        self.inflight = 0                       # chat requests being answered right now
        self.stop_note = ""                     # why a Stop didn't take (the program runs as something this Courier can't stop), until it does
        self._error = ""                        # the last thing that went wrong, until something works again (see `error`)
        self._error_at = 0.0
        self.loading: set[str] = set()
        self.downloads: dict[str, Download] = {}
        self._tasks: set[asyncio.Task] = set()
        self.changed: Callable[[], None] = lambda: None     # the Courier sets this: "something changed, tell the main device now"
        self._checks: dict[str, _Check] = {}
        self._gen = 0                                       # bumped by refresh(): a check that started before it never counts as fresh

    # ---- to be filled in -----------------------------------------------------------------------
    async def probe(self) -> dict:
        """{"installed": bool, "running": bool, "version": str|None}"""
        raise NotImplementedError

    async def models(self) -> list[dict]:
        """[{"id","name","size_bytes","loaded","state","vision","on_card_percent"}]"""
        raise NotImplementedError

    async def load(self, model: str) -> None: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def unload(self, model: str) -> None: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def delete(self, model: str) -> None: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def download(self, model: str) -> Download: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def start(self) -> None: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def restart(self) -> None: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def stop(self) -> None: raise AdapterError(400, f"{self.name} doesn't support that action remotely.")
    async def chat_target(self, model: str, body: dict) -> tuple[str, dict]:
        """(base URL of its OpenAI-style API, the request body to send) for a chat request for `model`."""
        raise NotImplementedError

    # ---- shared ------------------------------------------------------------------------------------
    def working(self) -> bool:
        """A load or a download is under way (the faster rhythm is allowed)."""
        return bool(self.loading) or any(d.state == "running" for d in self.downloads.values())

    def refresh(self) -> None:
        """Forget everything remembered: the next report asks the program again. Called when this Courier itself changed something
        (a load, an unload, a finished download, a start or stop) and when the main device explicitly asks for a refresh."""
        self._gen += 1
        for key, c in self._checks.items():
            if key != "version":                             # a program's version doesn't change while the Courier runs
                c.at, c.error = None, None

    async def _check(self, key: str, idle_seconds: float, fn: Callable[[], Any], busy_fast: bool = True,
                     error_seconds: float = ERROR_RETRY_SECONDS) -> Any:
        """`fn()`'s answer, remembered for `idle_seconds` (BUSY_SECONDS while something is loading or downloading, if `busy_fast`).
        Many callers at once share one check; a failure is remembered for `error_seconds` and raised again instead of retried."""
        c = self._checks.get(key)
        if c is None:
            c = self._checks[key] = _Check()
        async with c.lock:
            ttl = min(idle_seconds, BUSY_SECONDS) if (busy_fast and self.working()) else idle_seconds
            now = time.monotonic()
            if c.at is not None and now - c.at < ttl:
                return c.value
            if c.error is not None and now - c.error[1] < error_seconds:
                raise c.error[0]
            gen = self._gen
            try:
                value = await fn()
            except Exception as e:
                c.error = (e, time.monotonic()) if gen == self._gen else None
                raise
            c.value, c.error = value, None
            c.at = time.monotonic() if gen == self._gen else None
            return value

    def _bg(self, coro, label: str) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                self.log.error(f"{self.name}: {label} failed: {t.exception()}")
            self.refresh()                                   # a load or download just ended: what it left behind is looked at again
            self.changed()
        task.add_done_callback(done)
        return task

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def cancel_download(self, model: str) -> None:
        d = self.downloads.get(model)
        if d and d.task and d.state == "running":
            d.task.cancel()
            await asyncio.gather(d.task, return_exceptions=True)
            d.state = "cancelled"
            self.changed()

    def downloads_json(self) -> list[dict]:
        return [d.to_json() for d in self.downloads.values()]

    def state_of(self, probe: dict) -> str:
        if not probe.get("running"):
            return "error" if self.error and probe.get("installed") else "stopped"
        if self.error:
            return "error"
        if self.loading:
            return "loading"
        return "busy" if self.inflight > 0 else "ready"

    async def snapshot(self, fresh: bool = False) -> dict:
        if fresh:
            self.refresh()
        probe = await self.probe()
        models = []
        if probe.get("running") or self.id == "llamacpp":
            try:
                models = await self.models()
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, AdapterError):
                models = []
        return {"id": self.id, "name": self.name, "installed": bool(probe.get("installed")), "running": bool(probe.get("running")),
                "version": probe.get("version"), "state": self.state_of(probe), "error": self.error, "error_ago_seconds": self.error_ago_seconds(), "stop_note": self.stop_note,
                "capabilities": dict(self.capabilities), "notes": probe.get("notes", []), "models": models,
                "downloads": self.downloads_json(), **{k: v for k, v in probe.items() if k in ("builtin", "engine")}}

    @property
    def error(self) -> str:
        return self._error

    @error.setter
    def error(self, value: str) -> None:
        """Setting a message notes when it happened (the main device's row says "3 min ago"); clearing it forgets."""
        self._error = value or ""
        self._error_at = time.time() if value else 0.0

    def error_ago_seconds(self) -> Optional[int]:
        return max(0, int(time.time() - self._error_at)) if self._error and self._error_at else None

    def find_model(self, wanted: str, models: list[dict]) -> Optional[dict]:
        w = (wanted or "").lower()
        for m in models:
            if m["id"].lower() == w:
                return m
        return None


# --------------------------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------------------------
OLLAMA_PORT = 11434


def find_ollama_exe() -> Optional[str]:
    found = poll_cache.cached_which("ollama")
    if found:
        return found
    cands = []
    if sys.platform == "win32":
        cands.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    else:
        cands += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return next((str(c) for c in cands if c.is_file()), None)


class OllamaAdapter(Adapter):
    id, name = "ollama", "Ollama"
    capabilities = {"load": True, "unload": True, "delete": True, "download": True, "download_progress": True,
                    "start": True, "restart": True, "stop": True, "spill_over": True}

    def __init__(self, log, base: str = f"http://127.0.0.1:{OLLAMA_PORT}", exe: Optional[str] = "auto",
                 spawn: Callable[..., Any] = hidden_process.popen, kill: Optional[Callable[[], None]] = None):
        super().__init__(log)
        self.base = base.rstrip("/")
        self._auto_exe = exe == "auto"
        self._exe = find_ollama_exe() if self._auto_exe else exe
        self._spawn = spawn
        self._kill = kill or self._kill_ollama
        self._pinned: set[str] = set()
        self._vision: dict[str, bool] = {}

    # ---- plumbing -----------------------------------------------------------------------------------
    def _session(self, total: Optional[float] = 8) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=total, sock_connect=2))

    async def _json(self, method: str, path: str, body: Optional[dict] = None, total: Optional[float] = 8) -> Any:
        async with self._session(total) as s:
            async with s.request(method, self.base + path, json=body) as r:
                text = await r.text()
                if r.status >= 400:
                    msg = ""
                    try:
                        msg = (json.loads(text) or {}).get("error", "")
                    except ValueError:
                        pass
                    raise AdapterError(r.status if r.status != 500 else 502, msg or f"Ollama answered with an error ({r.status}).")
                try:
                    return json.loads(text) if text.strip() else {}
                except ValueError:
                    return {}

    # ---- what's there --------------------------------------------------------------------------------
    async def _probe_live(self) -> dict:
        try:
            data = await self._json("GET", "/api/version", total=PROBE_SECONDS)
            return {"installed": True, "running": True, "version": str(data.get("version") or "") or None}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, AdapterError):
            if self._auto_exe and not (self._exe and Path(self._exe).is_file()):
                self._exe = find_ollama_exe()                 # uninstalled while the Courier runs (or installed since): look again, never keep the old answer
            return {"installed": bool(self._exe), "running": False, "version": None}

    async def probe(self) -> dict:
        return await self._check("probe", IDLE_PROBE_SECONDS, self._probe_live, busy_fast=False)

    async def _show_vision(self, name: str) -> bool:
        if name not in self._vision:
            try:
                d = await self._json("POST", "/api/show", {"model": name}, total=4)
                caps = d.get("capabilities") or []
                fams = (d.get("details") or {}).get("families") or []
                self._vision[name] = ("vision" in caps) or any(f in ("clip", "mllama") for f in fams)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, AdapterError):
                return False
        return self._vision[name]

    async def _tags(self) -> list:
        return (await self._json("GET", "/api/tags")).get("models") or []

    async def _ps(self) -> dict:
        try:
            return {m.get("name") or m.get("model"): m for m in ((await self._json("GET", "/api/ps")).get("models") or [])}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, AdapterError):
            return {}

    async def models(self) -> list[dict]:
        tags = await self._check("downloaded", DOWNLOADED_SECONDS, self._tags, busy_fast=False)
        running = await self._check("loaded", LOADED_SECONDS, self._ps)
        names = [t.get("name") or t.get("model") for t in tags]
        vis = await asyncio.gather(*(self._show_vision(n) for n in names))
        out = []
        for t, name, vision in zip(tags, names, vis):
            r = running.get(name)
            state = "loading" if name in self.loading else ("loaded" if r else "available")
            on_card = None
            if r and r.get("size"):
                on_card = max(0, min(100, round(100 * (r.get("size_vram") or 0) / r["size"])))   # Ollama says this directly
            out.append({"id": name, "name": name, "size_bytes": int(t.get("size") or 0), "loaded": bool(r), "state": state,
                        "vision": bool(vision), "on_card_percent": on_card})
        return out

    # ---- managing ------------------------------------------------------------------------------------
    async def _need_model(self, model: str) -> dict:
        for _ in (0, 1):
            models = await self.models()
            found = self.find_model(model, models) or self.find_model(model + ":latest", models)
            if found:
                return found
            self.refresh()                                   # not in what was remembered: look again before saying it isn't there
        raise AdapterError(404, f"Ollama doesn't have '{model}' installed yet. Download it first.")

    async def load(self, model: str, wait: bool = False) -> None:
        m = await self._need_model(model)

        async def go():
            self.loading.add(m["id"])
            self.changed()
            try:
                # An empty generate loads the model; keep_alive -1 keeps it loaded (Ollama's default drops it after 5 idle minutes)
                await self._json("POST", "/api/generate", {"model": m["id"], "keep_alive": KEEP_FOREVER, "stream": False}, total=900)
                self._pinned.add(m["id"])
                self.error = ""
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, AdapterError) as e:
                self.error = getattr(e, "message", "") or f"Ollama couldn't load {m['id']}. That device might not have enough graphics memory."
                raise
            finally:
                self.loading.discard(m["id"])
        task = self._bg(go(), f"loading {m['id']}")
        if wait:
            await asyncio.gather(task, return_exceptions=False)

    async def unload(self, model: str) -> None:
        m = await self._need_model(model)
        await self._json("POST", "/api/generate", {"model": m["id"], "keep_alive": 0, "stream": False}, total=30)
        self._pinned.discard(m["id"])
        self.refresh()
        self.changed()

    async def delete(self, model: str) -> None:
        m = await self._need_model(model)
        await self._json("DELETE", "/api/delete", {"model": m["id"]}, total=60)
        self._pinned.discard(m["id"])
        self.refresh()
        self.changed()

    async def download(self, model: str) -> Download:
        model = (model or "").strip()
        if not model:
            raise AdapterError(400, "Please choose a model (for example: gemma3:12b).")
        model = check_model_name(model)
        live = self.downloads.get(model)
        if live and live.state == "running":
            return live
        d = Download(model)
        self.downloads[model] = d
        totals: dict[str, list[int]] = {}

        async def pull():
            try:
                async with self._session(total=None) as s:
                    async with s.post(self.base + "/api/pull", json={"model": model, "stream": True}) as r:
                        if r.status >= 400:
                            raise AdapterError(r.status, f"Ollama failed to download '{model}' ({r.status}).")
                        async for raw in r.content:
                            line = raw.decode("utf-8", "ignore").strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except ValueError:
                                continue
                            if ev.get("error"):
                                raise AdapterError(502, str(ev["error"]))
                            if ev.get("digest") and ev.get("total"):
                                totals[ev["digest"]] = [int(ev["total"]), int(ev.get("completed") or 0)]
                                d.total = sum(t[0] for t in totals.values())
                                d.done = sum(t[1] for t in totals.values())
                                self.changed()
                d.state, d.done = "done", d.total
            except asyncio.CancelledError:
                d.state = "cancelled"
                raise
            except AdapterError as e:
                d.state, d.error = "error", e.message
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                d.state, d.error = "error", f"Download failed: {e}"
            self.changed()
        d.task = self._bg(pull(), f"downloading {model}")
        self.changed()
        return d

    def _kill_ollama(self) -> None:
        if sys.platform == "win32":
            for image in ("ollama app.exe", "ollama.exe"):
                hidden_process.run(["taskkill", "/F", "/IM", image], capture_output=True, creationflags=0x08000000)
        else:
            hidden_process.run(["pkill", "-x", "ollama"], capture_output=True)

    async def start(self) -> None:
        if (await self._probe_live())["running"]:
            return
        if not self._exe:
            raise AdapterError(404, "Ollama isn't installed on the Courier device.")
        kw = {"creationflags": 0x08000000 | 0x00000008} if os.name == "nt" else {"start_new_session": True}
        self._spawn([self._exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        for _ in range(40):
            await asyncio.sleep(0.5)
            if (await self._probe_live())["running"]:
                self.error = ""
                self.refresh()
                self.changed()
                return
        self.error = "Ollama failed to launch. Try starting it again from the Other devices tab."
        raise AdapterError(502, self.error)

    async def stop(self) -> None:
        """Unload every loaded model (so the graphics memory is really freed), then close Ollama the way it was started: its
        tray app and its server. If something brings it straight back (a system service this Courier isn't allowed to stop),
        say so plainly instead of pretending."""
        if not (await self._probe_live())["running"]:
            return
        try:
            self.refresh()
            for m in await self.models():
                if m.get("loaded"):
                    await self._json("POST", "/api/generate", {"model": m["id"], "keep_alive": 0, "stream": False}, total=30)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, AdapterError):
            pass                                             # killing the server below frees them anyway
        self._pinned.clear()
        self.loading.clear()
        await asyncio.to_thread(self._kill)
        for _ in range(16):
            await asyncio.sleep(0.5)
            if not (await self._probe_live())["running"]:
                self.stop_note = ""
                self.refresh()
                self.changed()
                return
        self.stop_note = STOP_NOTE.format(program="Ollama")
        self.refresh()
        self.changed()
        raise AdapterError(409, self.stop_note)

    async def restart(self) -> None:
        await asyncio.to_thread(self._kill)
        await asyncio.sleep(1.0)
        self._pinned.clear()
        self.loading.clear()
        await self.start()           # (a no-op if Ollama's own tray app already brought it back)

    # ---- chat ----------------------------------------------------------------------------------------
    async def chat_target(self, model: str, body: dict) -> tuple[str, dict]:
        m = await self._need_model(model)
        if m["id"] not in self._pinned or not m["loaded"]:
            # Keep it loaded: without this Ollama drops the model after 5 idle minutes and the next NPC line
            # after a quiet dungeon waits 10-20 seconds for it to come back.
            self.loading.add(m["id"])
            self.changed()
            try:
                await self._json("POST", "/api/generate", {"model": m["id"], "keep_alive": KEEP_FOREVER, "stream": False}, total=900)
                self._pinned.add(m["id"])
            finally:
                self.loading.discard(m["id"])
                self.refresh()
                self.changed()
        return self.base + "/v1", {**body, "model": m["id"]}


# --------------------------------------------------------------------------------------------
# LM Studio
# --------------------------------------------------------------------------------------------
LMSTUDIO_PORT = 1234
LMS_NOT_FOUND = ("LM Studio isn't fully set up. In LM Studio on that device, open Settings, click \"Install CLI\", and try again.")


def lms_search_dirs() -> list[Path]:
    home = Path.home()
    return [home / ".cache" / "lm-studio" / "bin", home / ".lmstudio" / "bin"]


def find_lms() -> Optional[str]:
    """LM Studio's `lms` isn't on the PATH unless "Install CLI" was clicked, so look in its own folders too."""
    found = poll_cache.cached_which("lms")
    if found:
        return found
    name = "lms.exe" if sys.platform == "win32" else "lms"
    return next((str(d / name) for d in lms_search_dirs() if (d / name).is_file()), None)


def lm_models_dir() -> Optional[Path]:
    home = Path.home()
    settings = home / ".lmstudio" / "settings.json"
    try:
        custom = json.loads(settings.read_text(encoding="utf-8")).get("downloadsFolder")
        if custom and Path(custom).is_dir():
            return Path(custom)
    except (OSError, ValueError):
        pass
    return next((p for p in (home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models") if p.is_dir()), None)


def parse_lms_json(text: str) -> list[dict]:
    """`lms ls --json` / `lms ps --json` print a JSON list; tolerate a banner line before it."""
    t = (text or "").strip()
    start = min((i for i in (t.find("["), t.find("{")) if i >= 0), default=-1)
    if start < 0:
        return []
    try:
        data = json.loads(t[start:])
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("models") or data.get("data") or []
    return [d for d in data if isinstance(d, dict)]


class LmStudioAdapter(Adapter):
    id, name = "lmstudio", "LM Studio"
    capabilities = {"load": True, "unload": True, "delete": False, "download": True, "download_progress": True, "stop": True,
                    "start": True, "restart": True, "spill_over": False}

    def __init__(self, log, base: str = f"http://127.0.0.1:{LMSTUDIO_PORT}", cli: Optional[str] = "auto",
                 models_dir: Optional[Path] = None, runner: Optional[Callable[..., Any]] = None):
        super().__init__(log)
        self.base = base.rstrip("/")
        self._auto_cli = cli == "auto"
        self._cli = find_lms() if self._auto_cli else cli
        self._models_dir = models_dir
        self._runner = runner or self._run_cli
        self._opened_app = False                 # LM Studio's app was opened by this Courier's own Start (so its Stop may close it)

    @property
    def cli(self) -> Optional[str]:
        if self._auto_cli and not (self._cli and Path(self._cli).is_file()):
            self._cli = find_lms()                            # gone (uninstalled) or not found yet: look again
        return self._cli

    async def _run_cli(self, args: list[str], timeout: float = 60) -> tuple[int, str, str]:
        if not self.cli:
            raise AdapterError(404, LMS_NOT_FOUND)
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        proc = await hidden_process.create_subprocess_exec(self.cli, *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, **kw)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise AdapterError(504, "LM Studio took too long to respond.")
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def _lms(self, args: list[str], timeout: float = 60) -> str:
        rc, out, err = await self._runner(args, timeout)
        if rc != 0:
            raise AdapterError(502, (err.strip() or out.strip() or "LM Studio's command-line tool reported an error.").splitlines()[-1])
        return out

    # ---- what's there --------------------------------------------------------------------------------
    async def _server_up(self) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PROBE_SECONDS)) as s:
                async with s.get(self.base + "/v1/models") as r:
                    return r.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return False

    def _app_installed(self) -> bool:
        if sys.platform == "win32":
            return (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio").is_dir()
        return (Path.home() / ".lmstudio").is_dir() or (Path.home() / ".cache" / "lm-studio").is_dir() or bool(shutil.which("lm-studio"))

    async def _version(self) -> Optional[str]:
        out = await self._lms(["version"], 10)
        m = re.search(r"\d+\.\d+(?:\.\d+)*", out)
        return m.group(0) if m else None

    async def _probe_live(self) -> dict:
        up = await self._server_up()
        installed = bool(self.cli) or up or self._app_installed()
        version, notes = None, []
        if self.cli:
            try:
                # the version doesn't change while the Courier runs: asked once (a failure is retried only every few minutes)
                version = await self._check("version", 3600.0, self._version, busy_fast=False, error_seconds=300.0)
            except AdapterError:
                pass
        elif installed:
            notes.append(LMS_NOT_FOUND)         # said on the main device, in plain words, with what to click
        return {"installed": installed, "running": up, "version": version, "notes": notes}

    async def probe(self) -> dict:
        """The cheap "is it answering?" check, no model list in it: at most every IDLE_PROBE_SECONDS while idle."""
        return await self._check("probe", IDLE_PROBE_SECONDS, self._probe_live, busy_fast=False)

    async def _rest_models(self) -> Optional[list[dict]]:
        """LM Studio's own web API: every model with its loaded state, in ONE request. None when it didn't answer."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
                async with s.get(self.base + "/api/v0/models") as r:
                    if r.status == 200:
                        return [m for m in ((await r.json(content_type=None)).get("data") or []) if isinstance(m, dict)]
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            pass
        return None

    async def _downloaded(self) -> list[dict]:
        """What's downloaded (with sizes, which only `lms ls` gives). Changes rarely: asked at most every DOWNLOADED_SECONDS."""
        rows: list[dict] = []
        if self.cli:
            try:
                rows = parse_lms_json(await self._lms(["ls", "--json"], 30))
            except AdapterError:
                rows = []
        if not rows:                                      # no command-line tool: whatever the running server will tell us
            for m in (await self._rest_models()) or []:
                rows.append({"modelKey": m.get("id"), "displayName": m.get("id"), "type": m.get("type"),
                             "vision": m.get("type") == "vlm", "_loaded": m.get("state") == "loaded"})
        if not rows:
            raise AdapterError(502, "LM Studio didn't list its models.")      # not remembered as "no models" (retried shortly)
        return rows

    async def _loaded(self) -> dict[str, dict]:
        """Which models are loaded, key -> {"identifier"}. Its web API answers this in one request; `lms ps` is the way when it doesn't."""
        rest = await self._rest_models()
        if rest is not None:
            return {str(m.get("id")): {"identifier": str(m.get("id"))} for m in rest if m.get("state") == "loaded"}
        loaded: dict[str, dict] = {}
        if self.cli:
            try:
                for p in parse_lms_json(await self._lms(["ps", "--json"], 15)):
                    loaded[str(p.get("modelKey") or p.get("identifier") or p.get("path"))] = p
            except AdapterError:
                pass
        return loaded

    async def models(self) -> list[dict]:
        try:
            rows = await self._check("downloaded", DOWNLOADED_SECONDS, self._downloaded, busy_fast=False)
        except AdapterError:
            rows = []
        loaded = await self._check("loaded", LOADED_SECONDS, self._loaded)
        out = []
        for r in rows:
            if str(r.get("type") or "llm").lower() in ("embedding", "embeddings"):
                continue
            key = str(r.get("modelKey") or r.get("id") or r.get("path") or "")
            if not key or courier_models.is_helper_model(key, r.get("path"), r.get("arch") or r.get("architecture")):
                continue                                       # a voice or helper file LM Studio indexed from a shared folder
            is_loaded = key in loaded
            state = "loading" if key in self.loading else ("loaded" if is_loaded else "available")
            out.append({"id": key, "name": str(r.get("displayName") or key), "size_bytes": int(r.get("sizeBytes") or r.get("size") or 0),
                        "loaded": is_loaded, "state": state, "vision": bool(r.get("vision") or str(r.get("type")).lower() == "vlm"),
                        "on_card_percent": None,                       # LM Studio doesn't say; never guessed
                        "_identifier": str((loaded.get(key) or {}).get("identifier") or key), "_path": r.get("path")})
        return out

    # ---- managing ------------------------------------------------------------------------------------
    async def _need_model(self, model: str) -> dict:
        if not self.cli:
            raise AdapterError(404, LMS_NOT_FOUND)           # everything below goes through its command-line tool
        m = self.find_model(model, await self.models())
        if not m:
            self.refresh()                                   # not in what was remembered: look again before saying it isn't there
            m = self.find_model(model, await self.models())
        if not m:
            raise AdapterError(404, f"LM Studio doesn't have '{model}'. Download it first.")
        return m

    async def load(self, model: str, wait: bool = False) -> None:
        m = await self._need_model(model)

        async def go():
            self.loading.add(m["id"])
            self.changed()
            try:
                await self._lms(["load", m["id"], "--yes"], 900)
                self.error = ""
            except AdapterError as e:
                self.error = e.message
                raise
            finally:
                self.loading.discard(m["id"])
        task = self._bg(go(), f"loading {m['id']}")
        if wait:
            await asyncio.gather(task)

    async def unload(self, model: str) -> None:
        m = await self._need_model(model)
        ident = m["_identifier"]
        try:                                                 # the name it was loaded under (only `lms ps` says): asked now, for this one action
            for p in parse_lms_json(await self._lms(["ps", "--json"], 15)):
                if str(p.get("modelKey") or p.get("identifier") or p.get("path")) == m["id"]:
                    ident = str(p.get("identifier") or ident)
        except AdapterError:
            pass
        await self._lms(["unload", ident], 60)
        self.refresh()
        self.changed()

    def models_dir(self) -> Optional[Path]:
        return self._models_dir or lm_models_dir()

    async def delete(self, model: str) -> None:
        """LM Studio's command-line tool has no delete, and a model's `path` in `lms ls --json` is its key, not a file on disk (checked
        against the real LM Studio: the key `google/gemma-4-12b-qat` lives in `lmstudio-community/gemma-4-12B-it-QAT-GGUF`), so there is
        no safe way to find the right file. Better to say so than to guess at someone's model files."""
        raise AdapterError(400, "LM Studio doesn't let other programs delete models. Delete it in LM Studio on that device.")

    async def download(self, model: str) -> Download:
        model = (model or "").strip()
        if not model:
            raise AdapterError(400, "Please choose a model (for example: qwen/qwen3-4b).")
        model = check_model_name(model)
        if not self.cli:
            raise AdapterError(404, LMS_NOT_FOUND)
        live = self.downloads.get(model)
        if live and live.state == "running":
            return live
        d = Download(model, progress_known=False, unit_bytes=False)      # becomes known only if the tool actually prints a percentage
        self.downloads[model] = d

        async def get():
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
            proc = await hidden_process.create_subprocess_exec(self.cli, "get", model, "--yes", stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kw)
            tail = ""
            try:
                buf = b""
                while True:
                    chunk = await proc.stdout.read(256)
                    if not chunk:
                        break
                    buf += chunk
                    parts = re.split(rb"[\r\n]", buf)
                    buf = parts.pop()
                    for p in parts:
                        line = p.decode("utf-8", "replace").strip()
                        if not line:
                            continue
                        tail = line
                        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", line)
                        if m:
                            d.progress_known, d.total, d.done = True, 1000, int(float(m.group(1)) * 10)
                            self.changed()
                rc = await proc.wait()
            except asyncio.CancelledError:
                proc.kill()
                d.state = "cancelled"
                raise
            if rc == 0:
                d.state = "done"
                d.done, d.total, d.progress_known = d.total or 1, d.total or 1, d.progress_known
            else:
                d.state, d.error = "error", tail or "LM Studio failed to download that model."
        d.task = self._bg(get(), f"downloading {model}")
        self.changed()
        return d

    def _app_open(self) -> Optional[bool]:
        """Is LM Studio's own app running? None when this can't tell (only Windows is checked)."""
        if sys.platform != "win32":
            return None
        procs = native_probe.process_list()
        return None if procs is None else any(n.lower() == "lm studio.exe" for n, _ in procs)

    async def start(self) -> None:
        if await self._server_up():
            return
        was_open = await asyncio.to_thread(self._app_open)
        await self._lms(["server", "start"], 60)
        for _ in range(20):
            if await self._server_up():
                self.error = ""
                if was_open is False and await asyncio.to_thread(self._app_open):
                    self._opened_app = True              # `lms server start` opened the app: this Courier is the one that did
                self.refresh()
                self.changed()
                return
            await asyncio.sleep(0.5)
        self.error = "LM Studio's local server failed to start."
        raise AdapterError(502, self.error)

    async def stop(self) -> None:
        """Unload every model, stop the server, and close the app only if this Courier's own Start is what opened it (a player
        who had LM Studio open themselves keeps it open). Unloading first is what really frees the graphics memory."""
        if not self.cli:
            raise AdapterError(404, LMS_NOT_FOUND)
        for args in (["unload", "--all"], ["server", "stop"]):
            try:
                await self._lms(args, 60)
            except AdapterError:
                pass
        for _ in range(10):
            if not await self._server_up():
                break
            await asyncio.sleep(0.5)
        else:
            self.stop_note = STOP_NOTE.format(program="LM Studio")
            self.refresh()
            self.changed()
            raise AdapterError(409, self.stop_note)
        self.stop_note = ""
        if self._opened_app:
            await asyncio.to_thread(self._close_app)
            self._opened_app = False
        self.refresh()
        self.changed()

    def _close_app(self) -> None:
        """Close LM Studio's app politely, then firmly if it hangs on."""
        if sys.platform != "win32":
            return
        hidden_process.run(["taskkill", "/IM", "LM Studio.exe"], capture_output=True)
        for _ in range(16):
            time.sleep(0.5)
            if not self._app_open():
                return
        hidden_process.run(["taskkill", "/F", "/IM", "LM Studio.exe"], capture_output=True)

    async def restart(self) -> None:
        try:
            await self._lms(["server", "stop"], 30)
        except AdapterError:
            pass
        self.refresh()
        await asyncio.sleep(0.5)
        await self.start()

    # ---- chat ----------------------------------------------------------------------------------------
    async def chat_target(self, model: str, body: dict) -> tuple[str, dict]:
        m = (self.find_model(model, await self.models()) if self.cli else None) or {"id": model}
        return self.base + "/v1", {**body, "model": m["id"]}


# --------------------------------------------------------------------------------------------
# llama.cpp (an existing llama-server, or MultiProxy's built-in engine)
# --------------------------------------------------------------------------------------------
_OFFLOAD = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", re.I)


def parse_offload(log_text: str) -> Optional[int]:
    """How much of a model sits on the card, from llama-server's own startup lines ("offloaded 33/33 layers to GPU").
    None when it never said: llama-server has no route for this, so we never guess."""
    last = None
    for m in _OFFLOAD.finditer(log_text or ""):
        last = m
    if not last or int(last.group(2)) == 0:
        return None
    return max(0, min(100, round(100 * int(last.group(1)) / int(last.group(2)))))


class CourierLocalModels(local_models.LocalModels):
    """The full MultiProxy's engine code, with one change: an existing llama-server (set in courier.json, or found on
    the PATH) is used when there is one; MultiProxy's built-in engine is what's left when nothing is installed.
    No Skyrim runs on a Courier device, so its "memory to keep free" default is 1 GB, not the game's busiest point."""

    HAS_GAME = False

    def engine_exe(self) -> Optional[Path]:
        own = self._load_cfg().get("llama_server_path")
        if own and Path(own).is_file():
            return Path(own)
        found = poll_cache.cached_which("llama-server")
        if found:
            return Path(found)
        return super().engine_exe()

    def external_engine(self) -> Optional[Path]:
        own = self._load_cfg().get("llama_server_path")
        if own and Path(own).is_file():
            return Path(own)
        found = poll_cache.cached_which("llama-server")
        return Path(found) if found else None


class LlamaCppAdapter(Adapter):
    id, name = "llamacpp", "llama.cpp"
    capabilities = {"load": True, "unload": True, "delete": True, "download": True, "download_progress": True,
                    "start": False, "restart": False, "spill_over": True}

    def __init__(self, log, local_svc: CourierLocalModels):
        super().__init__(log)
        self.local = local_svc
        self._versions: dict[str, Optional[str]] = {}       # engine file -> its version (a process start to ask: once per file)
        self._size = program_uninstall.SizeCache()

    def _version(self, exe: Path) -> Optional[str]:
        try:
            r = hidden_process.run([str(exe), "--version"], capture_output=True, text=True, timeout=10,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            m = re.search(r"version:\s*(\S+)", (r.stdout or "") + (r.stderr or ""))
            return m.group(1) if m else None
        except Exception:
            return None

    async def probe(self) -> dict:
        exe = self.local.engine_exe()
        external = self.local.external_engine()
        running = any(s.state in ("starting", "running") for s in self.local.servers.values())
        engine = {"kind": "existing" if external else ("builtin" if exe else "none"), "path": str(exe) if exe else None}
        folder = self.local.folder
        mine = bool(folder and (folder / "engine").is_dir())          # the program MultiProxy downloaded (what an uninstall removes), not one the player already had
        engine["removable"], engine["diskBytes"] = mine, (self._size.get(folder / "engine") if mine else 0)
        notes = []
        if not exe:
            notes.append("MultiProxy's model engine (llama.cpp) downloads the first time you load a model.")
        version = None
        if exe:
            build = (self.local._cfg().get("engine") or {}).get("build")
            if not (not external and build) and str(exe) not in self._versions:
                self._versions[str(exe)] = await asyncio.to_thread(self._version, exe)
            version = build if (not external and build) else self._versions.get(str(exe))
        # "installed" means we can run a model now; the built-in one is offered even before it's downloaded
        return {"installed": bool(exe), "running": running, "version": version, "builtin": not external,
                "engine": engine, "notes": notes}

    def state_of(self, probe: dict) -> str:
        servers = list(self.local.servers.values())
        if any(s.state == "starting" for s in servers):
            return "loading"
        if self.inflight > 0 and any(s.state == "running" for s in servers):
            return "busy"
        if any(s.state == "running" for s in servers):
            return "ready"
        if any(s.state == "failed" for s in servers):
            return "error"
        return "stopped"

    async def models(self) -> list[dict]:
        out = []
        for m in self.local.installed():
            st, msg = self.local.state_of(m.id)
            on_card = None
            srv = self.local.servers.get(m.id)
            if st == "running" and srv:
                on_card = parse_offload(self.local._log_tail(srv, 60000))
                if on_card is None and srv.started:
                    on_card = srv.started.get("onCardPercent")          # the layers are known from the settings (or the engine's own dry run), not from its log
            state = {"running": "loaded", "starting": "loading", "failed": "error"}.get(st, "available")
            ej = self.local.jobs.get("engine")
            wait = bool(srv and srv.engine_wait and ej)
            out.append({"id": m.id, "name": m.name, "size_bytes": m.size, "loaded": st == "running", "state": state,
                        **({"engine_download": {"done": ej.done, "total": ej.total, "state": ej.state}} if wait else {}),
                        "vision": m.vision, "on_card_percent": on_card, "custom": m.custom,      # custom = a file the player pointed at: removing it only unlists it
                        **({"engine_note": self.local.engine_notes[m.id]} if self.local.engine_notes.get(m.id) else {}),     # the faster engine wouldn't start: the works-on-anything one took over
                        **({"started": srv.started} if st == "running" and srv and srv.started else {}),      # what its last start really did (command, engine build, layers)
                        **({"error": msg} if msg else {})})
        return out

    async def available(self) -> list[dict]:
        """MultiProxy's own catalog: what can be downloaded here."""
        have = {m.id for m in self.local.installed()}
        gpu = await self.local.gpu()
        out = []
        for spec in local_models.CATALOG:
            total = local_models.model_total_bytes(spec["files"])
            out.append({"id": spec["id"], "name": spec["name"], "blurb": spec["blurb"], "vision": spec["vision"], "size_bytes": total,
                        "installed": spec["id"] in have,
                        "fit": local_models.fit_check(total, gpu, reserve_skyrim=False)})
        return out

    async def snapshot(self, fresh: bool = False) -> dict:
        snap = await super().snapshot(fresh)
        snap["available"] = await self.available()
        snap["models_folder"] = str(self.local.folder) if self.local.folder else None
        return snap

    def _need_known(self, model: str):
        m = self.local.find(model)
        if not m:
            raise AdapterError(404, f"'{model}' hasn't been downloaded on that device yet.")
        return m

    async def load(self, model: str, wait: bool = False) -> None:
        m = self._need_known(model)
        s = await self.local.start(m.id)                     # fetches the engine first when it isn't here yet
        if wait:
            await s.ready.wait()
        else:
            self._bg(self._after_ready(s), f"loading {m.id}")
        if s.state == "failed":
            raise AdapterError(502, s.message or "The model failed to start.")

    async def _after_ready(self, s) -> None:
        await s.ready.wait()

    async def unload(self, model: str) -> None:
        await self.local.stop(self._need_known(model).id)
        self.changed()

    async def cancel_download(self, model: str) -> None:
        """Cancel also covers a model that is waiting for the engine to download (Cancel on its row)."""
        s = self.local.servers.get(model)
        if s and s.engine_wait:
            await self.local.stop(model)
            self.changed()
            return
        await super().cancel_download(model)

    async def delete(self, model: str) -> None:
        m = self._need_known(model)
        await self.local.remove(m.id, delete_files=True)       # this is the Delete button of a device's list: it deletes
        self.changed()

    def _engine_busy(self) -> bool:
        return self.inflight > 0 or self.loading or self.working() or any(j.state == "running" for j in self.local.jobs.values()) \
            or any(s.state == "starting" for s in self.local.servers.values())

    async def engine_info(self) -> dict:
        """The engine on this device: what is installed, whether a newer numbered build exists, and the faster versions this device's card can take (Advanced)."""
        eng = self.local._cfg().get("engine") or {}
        info = await self.local.engine_update_info()
        options = [{"kind": o["kind"], "label": o["label"], "blurb": o["blurb"], "sizeBytes": o["sizeBytes"]}
                   for o in local_models.engine_options(await self.local.gpu()) if o["advanced"] and o["kind"] != eng.get("kind")]
        return {"installed": bool(self.local.engine_exe()), "kind": eng.get("kind"), "build": eng.get("build"),
                "update": {"available": bool(info.get("available")), "build": info.get("build"), "sizeBytes": info.get("sizeBytes")}, "options": options}

    async def engine_update(self) -> None:
        """Update to the newest numbered build, only when the main device's player pressed Update. Busy answers a plain line and does nothing."""
        if self._engine_busy():
            raise AdapterError(409, "llama.cpp is busy on that device right now. Try again in a moment.")
        try:
            await self.local.update_engine()
        except local_models.LocalModelError as e:
            raise AdapterError(e.status, e.message)
        self.changed()

    async def engine_use(self, kind: str) -> None:
        """Switch to another version (a faster one, or back to the works-on-anything one). Only the versions this device's card is offered."""
        if self._engine_busy():
            raise AdapterError(409, "llama.cpp is busy on that device right now. Try again in a moment.")
        offered = {o["kind"] for o in local_models.engine_options(await self.local.gpu())}
        if kind not in offered:
            raise AdapterError(400, "That version isn't offered for this device's graphics card.")
        try:
            self.local.download_engine(kind)
        except local_models.LocalModelError as e:
            raise AdapterError(e.status, e.message)
        self.changed()

    async def uninstall(self, protected=()) -> int:
        """Remove the llama.cpp program MultiProxy downloaded (never a model). Busy (a request is being answered, a model is loading, something is downloading)
        answers a plain line and does nothing. Returns the bytes freed."""
        if self.inflight > 0 or self.loading or self.working() or any(j.state == "running" for j in self.local.jobs.values()) \
                or any(s.state == "starting" for s in self.local.servers.values()):
            raise AdapterError(409, "llama.cpp is busy on that device right now. Try again in a moment.")
        try:
            freed = await self.local.uninstall_engine(protected)
        except program_uninstall.UninstallError as e:
            self.changed()
            raise AdapterError(e.status, e.message)
        self.changed()
        return freed

    async def download(self, model: str) -> Download:
        spec = local_models.CATALOG_BY_ID.get((model or "").strip())
        if not spec:
            raise AdapterError(404, f"'{model}' isn't in MultiProxy's model list.")
        live = self.downloads.get(spec["id"])
        if live and live.state == "running":
            return live
        d = Download(spec["id"])
        self.downloads[spec["id"]] = d
        if self.local.find(spec["id"]):                      # already here: nothing to fetch
            d.state = "done"
            return d

        async def run():
            try:
                if not self.local.engine_exe():
                    gpu = await self.local.gpu()
                    kind = local_models.engine_options(gpu)[0]["kind"]
                    jobs = [self.local.download_engine(kind), None]
                else:
                    jobs = [None, None]
                try:
                    jobs[1] = self.local.download_model(spec["id"])
                except LocalModelError as e:
                    raise AdapterError(e.status, e.message)
                engine_job, model_job = jobs
                while True:
                    live_jobs = [j for j in (engine_job, model_job) if j]
                    d.total = sum(j.total for j in live_jobs)
                    d.done = sum(j.done for j in live_jobs)
                    self.changed()
                    bad = next((j for j in live_jobs if j.state in ("error", "cancelled")), None)
                    if bad:
                        raise AdapterError(502, bad.error or "The download was cancelled.")
                    if all(j.state == "done" for j in live_jobs):
                        break
                    await asyncio.sleep(0.5)
                d.state, d.done = "done", d.total
            except asyncio.CancelledError:
                d.state = "cancelled"
                for key in ("engine", f"model:{spec['id']}"):
                    await self.local.cancel(key)
                raise
            except (AdapterError, LocalModelError) as e:
                d.state, d.error = "error", e.message
            self.changed()
        d.task = self._bg(run(), f"downloading {spec['id']}")
        self.changed()
        return d

    async def chat_target(self, model: str, body: dict) -> tuple[str, dict]:
        m = self._need_known(model)
        try:
            if self.local.state_of(m.id)[0] == "stopped":          # asked for a model that isn't running: start it (fetching the engine if need be), then wait for it
                await self.local.start(m.id)
            base = await self.local.ensure_ready(m.model_name)
        except LocalModelError as e:
            raise AdapterError(e.status, e.message)
        return base, {**body, "model": m.id}

    async def shutdown(self) -> None:
        await super().shutdown()
        await self.local.stop_all()
