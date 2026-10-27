"""The local channel between the Skyrim mod (an SKSE plugin) and MultiProxy (2026-10-02). This device only.

The mod never kills processes and never starts a server itself. It reads a small pointer file the tray app writes, and talks to the running MultiProxy:

  POINTER FILE   %APPDATA%\\SkyrimNet MultiProxy\\install-info.json   (Linux: ~/.config/skyrimnet-multiproxy/install-info.json)
                 {"version": 1, "port": 8000, "token": "<random>", "tray": {"command": ["<pythonw.exe>", "<...>\\multiproxy_tray.py"],
                  "workingDirectory": "<program folder>", "fromSkyrimArg": "--from-skyrim"}, "programFolder": "...", "settingsFolder": "...",
                  "updated": "<ISO time>"}
  Every request carries   Authorization: Bearer <token>   and must come from this device (anything else is refused).

  GET  /local/ping                  -> 200 {"ok": true, "version": "<MultiProxy version>", "port": 8000}   (is MultiProxy up? 401 wrong token, 404 channel off)
  POST /local/skyrim/started        {"folder": "<Skyrim's folder>", "modVersion": "<the mod's version>", "pid": <Skyrim's process number>}    -> 200 {"ok": true}
                                    (`pid`, also accepted as `processId`, is what the tray app watches so it notices Skyrim ending hard, without the mod's "closed"; optional)
  POST /local/skyrim/closed         {}                                                                      -> 200 {"ok": true}
  (the tray app also uses  GET /local/tray-status  -> what the dot, the menu and the quit-with-Skyrim rule need, and
   POST /local/shutdown  -> a clean stop: the server stops its local AI engines, then exits)

What the mod does at game start: read the pointer file (not there: stay quiet, one line in the mod's own log); ping; if nothing answers, start
`tray.command` + `fromSkyrimArg` OUTSIDE Mod Organizer 2's job (CREATE_BREAKAWAY_FROM_JOB; if the job forbids it, ask the shell or a scheduled task
to start it) and ping until it answers (allow up to 60 s: the server loads models and checks its AIs); then POST "started". At game exit it POSTs
"closed" and does nothing else: whether MultiProxy quits is the tray app's decision ("Start and stop with Skyrim" on, and the tray app was started
by the mod rather than by the player).

The pointer file only exists once the tray app has run on this device, and the token is only ever in that file: a plain run of `multiproxy.py` in a
terminal (a developer, the current release) has no pointer file, so this whole channel answers 404 and nothing changes for it.
"""
import hmac
import inspect
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

POINTER_VERSION = 1
FROM_SKYRIM_ARG = "--from-skyrim"


def info_path() -> Path:
    """Where the pointer file lives. `MULTIPROXY_INSTALL_INFO` overrides it (tests, and anyone who needs it elsewhere)."""
    env = os.environ.get("MULTIPROXY_INSTALL_INFO")
    if env:
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "SkyrimNet MultiProxy" / "install-info.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "skyrimnet-multiproxy" / "install-info.json"


def read_info(path: Optional[Path] = None) -> dict:
    try:
        with open(path or info_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_info(*, port: int, command: list, program_folder: str, settings_folder: str, path: Optional[Path] = None) -> dict:
    """(Re)write the pointer file. The token is made once and kept, so the mod's copy of it never goes stale."""
    p = path or info_path()
    old = read_info(p)
    info = {"version": POINTER_VERSION, "port": int(port), "token": old.get("token") or secrets.token_urlsafe(24),
            "tray": {"command": command, "workingDirectory": program_folder, "fromSkyrimArg": FROM_SKYRIM_ARG},
            "programFolder": program_folder, "settingsFolder": settings_folder, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    # The token in this file lets whoever holds it stop the server and say "Skyrim closed": the person's own account only (a no-op on Windows, whose
    # profile folder is already private).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    os.replace(tmp, p)
    if os.name != "nt":
        for target, mode in ((p, 0o600),) + (((p.parent, 0o700),) if p.parent.name == "skyrimnet-multiproxy" else ()):
            try:
                os.chmod(target, mode)
            except OSError:
                pass
    return info


class SkyrimState:
    """What the mod has told us. `closed_seq` goes up on every "closed", so the tray app can tell a new one from one it already acted on."""

    def __init__(self):
        self.running = False
        self.folder = ""
        self.mod_version = ""
        self.started_seq = 0
        self.closed_seq = 0
        self.pid: Optional[int] = None                  # Skyrim's process number from the last "started" (None: the mod did not send one)

    def to_json(self) -> dict:
        return {"running": self.running, "folder": self.folder, "modVersion": self.mod_version, "startedSeq": self.started_seq, "closedSeq": self.closed_seq, "pid": self.pid}


def build_router(state: SkyrimState, version_fn: Callable[[], str], port_fn: Callable[[], int], on_started: Callable[[str, str], None],
                 status_fn: Callable[[], dict], on_shutdown: Callable[[], None] = lambda: None,
                 log: Callable[[str], None] = lambda m: None) -> APIRouter:
    r = APIRouter()

    def refuse(request: Request) -> Optional[JSONResponse]:
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse({"detail": "This device only."}, status_code=403)
        token = read_info().get("token")
        if not token:
            return JSONResponse({"detail": "Not enabled."}, status_code=404)
        given = request.headers.get("authorization", "")
        given = given[7:] if given.lower().startswith("bearer ") else request.headers.get("x-multiproxy-token", "")
        if not hmac.compare_digest(str(given), str(token)):
            return JSONResponse({"detail": "Wrong token."}, status_code=401)
        return None

    @r.get("/local/ping")
    async def ping(request: Request):
        return refuse(request) or {"ok": True, "version": version_fn(), "port": port_fn()}

    @r.post("/local/skyrim/started")
    async def started(request: Request):
        bad = refuse(request)
        if bad:
            return bad
        try:
            body = await request.json()
        except Exception:
            body = {}
        folder = str((body or {}).get("folder") or "")[:600]
        mod_version = str((body or {}).get("modVersion") or "")[:40]
        raw_pid = (body or {}).get("pid", (body or {}).get("processId"))
        state.pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) and 0 < raw_pid < 2 ** 31 else None
        state.running, state.folder, state.mod_version = True, folder, mod_version
        state.started_seq += 1
        log(f"Skyrim started{' (' + folder + ')' if folder else ''}")
        on_started(folder, mod_version)
        return {"ok": True}

    @r.post("/local/skyrim/closed")
    async def closed(request: Request):
        bad = refuse(request)
        if bad:
            return bad
        state.running = False
        state.closed_seq += 1
        log("Skyrim closed")
        return {"ok": True}

    @r.post("/local/shutdown")
    async def shutdown(request: Request):
        bad = refuse(request)
        if bad:
            return bad
        log("Shutdown asked for by the tray app")
        on_shutdown()
        return {"ok": True}

    @r.get("/local/tray-status")
    async def tray_status(request: Request):
        bad = refuse(request)
        if bad:
            return bad
        res = status_fn()
        if inspect.isawaitable(res):
            res = await res
        return {**res, "skyrim": state.to_json()}

    return r
