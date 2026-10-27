"""MultiProxy Courier (2026-10-01): the small program that goes on your other devices.

Built to design/multiproxy-courier-mockup.html. It has no web page. Its one job is to let your gaming
Device's MultiProxy see and manage the AI programs (Ollama, LM Studio, llama.cpp) running on THIS device, and to
pass your NPCs' chat requests through to them. After the one-time setup you never need to come back here.

The door (one port, home network only, the pairing code on every single request):

    GET  /courier/v1/report                       everything this device reports (graphics cards, memory, disk, programs...)
    GET  /courier/v1/memory                       the graphics-memory figures the memory monitor already reads
    GET  /courier/v1/ws                           the live connection: this device pushes its report; nothing else goes over it
    POST /courier/v1/programs/{id}/start|restart|stop  start / restart / stop an AI program
    POST /courier/v1/programs/{id}/models/load|unload|delete|download     {"model": "..."}   (add "wait": true to hold until done)
    POST /courier/v1/programs/{id}/downloads/cancel                       {"model": "..."}
    POST /courier/v1/settings                     {"keep_awake": bool, "start_with_windows": bool, "models_folder": "..."}
    POST /courier/v1/models/find                  {"folder": "..."}  model files (.gguf) already on this device: LM Studio's folder, the Courier's own, one given
    GET  /courier/v1/models/scan   POST .../scan/start|stop    a full scan of this device's drives, in the background, only when asked
    POST /courier/v1/models/add                   {"paths": [...]}  use model files where they sit (never copied)
    GET  /courier/v1/drives                       this device's drives with free space, and where models are saved now
    GET  /courier/v1/voice/status|fit|log         the voice on this device (Higgs, Qwen3-TTS or Chatterbox): its state, what installing it needs (and what is already here), its log
                                                  (`fit?model=<key>` asks about one voice model; none: the chosen one)
    POST /courier/v1/voice/license                {"licenseVersion": "...", "from": "<main device>", "model": "<key>"}  the player accepted that voice's license for this device
    POST /courier/v1/voice/install                {"source": "kept"|"internet", "model": "<key>"}  (refused until that voice's license is accepted)
    POST /courier/v1/voice/select                 {"model": "<key>"}  choose which installed voice this device uses
    POST /courier/v1/voice/cancel|start|stop|restart|uninstall   (uninstall takes {"model": "<key>"} to remove just that voice and {"alsoClips": true})
    POST /courier/v1/voice/speak                  one line in, the audio streamed back (the relay: the engine itself listens to this device only)
    GET  /courier/v1/voice/clips[/audio?voice=&which=]   the player's own voice clips kept on this device (list; one clip's sound)
    POST /courier/v1/voice/clips/add|remove       {"voice", "kind": "yours"|"skyrimnet", "dataB64"} / {"voice"}
    POST /courier/v1/voice/clips/words            {"voice", "words"}  (change the words a clip says; typed words win over ones the voice worked out)
    GET  /courier/v1/decisions/status|fit|log     the local decision model on this device: its state, what installing it needs (and what is already here), its log
    POST /courier/v1/decisions/install            {"source": "kept"|"internet"}   POST .../settings {"model", "runOn", "limitSeconds"}
    POST /courier/v1/decisions/cancel|start|stop|restart|uninstall
    POST /courier/v1/programs/llamacpp/uninstall       (the program, never a model) and POST /courier/v1/claude/uninstall
    GET  /courier/v1/programs/llamacpp/engine          {kind, build, update: {available, build, sizeBytes}, options: [faster versions this card is offered]}
    POST /courier/v1/programs/llamacpp/engine          {"action": "update"} (newest numbered build, only when asked) or {"action": "use", "kind": "rocm"|"sycl"|"vulkan"}
    POST /courier/v1/claude/signin/start|code|cancel   sign Claude in from the main device: this device runs `claude setup-token` itself, hands back the sign-in link, takes the one-time code
    GET  /courier/v1/copilot/status                    GitHub Copilot's program on this device (copilot_courier.py)   POST .../copilot/install|update|cancel|signin|cancelsignin|signout|test|uninstall
    POST /courier/v1/decisions/answer             {state, questions, limit} in, {answers, usage} out (the relay: the model itself listens to this device only)
    GET  /courier/v1/gpu                          which graphics card each local AI is kept on: {cards, ais}   POST {"ai": "local:<model>"|"decisions"|"voice", "cardId": id|null}
    POST /courier/v1/models-folder                {"path": "..."}  choose where downloaded models are saved (checked: made and written to)
    POST /courier/v1/pairing/new                  a new pairing code (every other connection is dropped)
    POST /v1/chat/completions   GET /v1/models    chat pass-through (streaming and pictures included), routed to the right program by model

and the older routes the full MultiProxy already speaks (`/share/info`, `/share/memory`, `/share/v1/chat/completions`), so a
Main device that paired with a "model server" keeps working with a Courier.

Reuses MultiProxy's own modules (other_pcs.py for the pairing code, the home-network rule and discovery;
local_models.py for the built-in llama.cpp engine and its downloads; gpu_monitor.py for graphics memory) so a fix lands in both.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import socket
import subprocess


def _recover_unfinished_update(base=None, build=None, now=None) -> str:
    """A power cut (or a crash) while an update was being installed: put the previous program files back BEFORE anything else is imported, because a half-replaced set of
    modules may not even import. Standard library only, on purpose (docs/security-review-update-courier-design.md M8); courier_updater.restore_previous does the same job for the
    helper, and a test holds the two to the same result. Does nothing unless a journal says an install was left half done AND no helper is still working on it.
    Returns "", "helper_working", "complete" (the new files are all in place: carry on, the new Courier confirms itself) or "restored"."""
    import json as _json
    import re as _re
    import shutil as _shutil
    import time as _time
    from pathlib import Path as _Path
    base = _Path(base) if base else _Path(__file__).resolve().parent
    update = base / "update"
    try:
        journal = _json.loads((update / "journal.json").read_text(encoding="utf-8"))
        state = journal.get("state")
        if state not in ("replacing", "started", "rolling_back"):
            return ""
        now = _time.time() if now is None else now
        try:
            if now - (update / "beat").stat().st_mtime < 20:
                return "helper_working"
        except OSError:
            pass
        if build is None:
            build = int(_re.search(rb"^COURIER_BUILD = (\d+)", _Path(__file__).read_bytes(), _re.M).group(1))
        if state in ("replacing", "started") and build == journal.get("to_build"):
            return "complete"
        previous = update / "previous"

        def flat(name):          # a name the journal may hold (the same rule as courier_updater.flat_name_ok); anything else in a journal is ignored
            return isinstance(name, str) and _re.match(r"^[A-Za-z][A-Za-z0-9_.-]{0,60}\.(py|txt|png|ico)$", name) is not None and ".." not in name

        def put_back(src, dst):
            tmp = dst.with_name(dst.name + ".new")
            _shutil.copyfile(src, tmp)
            os.replace(tmp, dst)

        def put_back_settings(src, dst):          # the same rule as courier_updater.settings_to_put_back: a pairing code made meanwhile stays made
            text = src.read_text(encoding="utf-8")
            try:
                data, cur = _json.loads(text), _json.loads(dst.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(cur, dict) and isinstance(cur.get("code"), str) and cur["code"] and cur["code"] != data.get("code"):
                    for k in ("code", "code_made_at", "paired", "paired_name"):
                        if k in cur:
                            data[k] = cur[k]
                    text = _json.dumps(data, indent=1)
            except (OSError, ValueError):
                pass
            tmp = dst.with_name(dst.name + ".new")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, dst)
        for name in sorted([n for n in (journal.get("backed_up") or []) if flat(n)], key=lambda n: n != "courier.py"):
            try:
                put_back(previous / name, base / name)
            except OSError:
                pass
        for name in journal.get("created") or []:
            if not flat(name):
                continue
            try:
                (base / name).unlink(missing_ok=True)
            except OSError:
                pass
        settings = journal.get("settings_file")
        if isinstance(settings, str) and _Path(settings).is_absolute() and _Path(settings).name == "courier.json" and _Path(settings).is_file() and (previous / "courier.json").is_file():
            try:
                put_back_settings(previous / "courier.json", _Path(settings))
            except OSError:
                pass
        journal.update(state="rolled_back", result="rolled_back", reason="power_cut", finished_at=now)
        journal.pop("logged", None)
        tmp = update / "journal.json.tmp"
        tmp.write_text(_json.dumps(journal, indent=1), encoding="utf-8")
        os.replace(tmp, update / "journal.json")
        return "restored"
    except Exception:          # never stop the Courier from starting because of this
        return ""


if __name__ == "__main__" and (len(__import__("sys").argv) < 2 or __import__("sys").argv[1] == "run"):
    _recover_unfinished_update()

import hidden_process  # every program started from here goes through it: no console window flashing up
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
from aiohttp import web

import claude_courier
import copilot_courier
import courier_update
import courier_models
import decision_courier
import decision_engine
import file_transfer
import safe_paths
import gpu_choice
import gpu_choice_wiring
import local_engine_sync
import local_models
import voice_courier
import voice_downloads
from voice_engine import VoiceEngineError
import courier_system as system
import other_pcs
from courier_adapters import (Adapter, AdapterError, CourierLocalModels, LlamaCppAdapter, LmStudioAdapter, OllamaAdapter)
from gpu_monitor import GpuMonitor
from local_models import LocalModelError

# The build number: an integer bumped BY HAND whenever the Courier's abilities or protocol change (a new voice, a new route, a changed answer). The main device
# compares it with the oldest it supports (other_pcs.MIN_COURIER_BUILD) and tells the player which device to update. A Courier built before this number existed
# sends none, which counts as older. (Keep it a plain `COURIER_BUILD = <integer>` line: the updater, the signer and the build scripts read it from the file.)
COURIER_BUILD = 11
# The one number a person sees: 1.<build> (the 1 is the major number and changes only with a revamp). Computed, never written twice.
COURIER_MAJOR = 1
COURIER_VERSION = f"{COURIER_MAJOR}.{COURIER_BUILD}"


def read_built() -> str:
    """The date and commit the package was built from, as scripts/build_courier_package.py stamped them (courier-build.txt next to this file); empty when run from the source."""
    try:
        return (Path(__file__).resolve().parent / "courier-build.txt").read_text(encoding="utf-8").strip()[:80]
    except OSError:
        return ""
COURIER_PORT_DEFAULT = 8110                 # the Courier's door; never the dashboard's 8000, never the old model server's 8100
OPENING_LINE = '[MultiProxy Courier] "I\'ve been looking for you..."'
CHAT_BLOCKED_PARAMS = {"thinking", "reasoning", "reasoning_effort"}      # llama.cpp only, as the old model-server door did
IDLE_PUSH_SECONDS = 5.0                     # status over the live connection while nothing is happening ...
ACTIVE_PUSH_SECONDS = 1.5                   # ... and while a model is generating, loading or downloading
REPORT_CACHE_SECONDS = 0.8
REFUSAL_QUIET_SECONDS = 600.0               # a wrong-code log line again only after this much quiet from that address; the status keeps saying it for as long
NO_CODE_LOG_SECONDS = 3600.0                # something with no pairing code at all: one log line per address per hour
NEW_CODE_HINT_SECONDS = 3600.0              # "You made a new pairing code" is said for this long after Make new pairing code
CONNECTED_WINDOW_SECONDS = 45.0             # a plain-HTTP main device (the older kind) counts as connected for this long after a request


def _err(status: int, message: str, **kw) -> web.Response:
    return web.json_response({"error": {"message": message, "type": "api_error", "code": None}}, status=status, **kw)


# --------------------------------------------------------------------------------------------
# courier.json
# --------------------------------------------------------------------------------------------
class CourierSettings:
    """courier.json. Doubles as the "config" the shared llama.cpp / graphics-memory modules read (their own keys live
    in the same file), so there is exactly one settings file on this device."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        changed = False
        for key, make in (("id", lambda: uuid.uuid4().hex), ("code", other_pcs.new_pairing_code)):
            if not self.data.get(key):
                self.data[key] = make()
                changed = True
        for key, default in (("port", COURIER_PORT_DEFAULT), ("keep_awake", True), ("start_with_windows", True), ("ready_shown", False)):
            if key not in self.data:
                self.data[key] = default
                changed = True
        if self.data.get("model_server") is not True:
            self.data["model_server"] = True            # (what the shared graphics-memory code reads: no Skyrim on this device)
            changed = True
        if changed:
            self.save()

    def save(self) -> None:
        safe_paths.write_private_json(self.path, self.data)           # all-or-nothing, owner-only where the system has permissions (security review S16)

    # the shapes local_models.py / gpu_monitor.py expect
    def load_cfg(self) -> dict:
        return self.data

    def save_cfg(self, cfg: dict) -> None:
        if cfg is not self.data:
            self.data.clear()
            self.data.update(cfg)
        self.save()

    def set(self, **changes) -> None:
        self.data.update(changes)
        self.save()

    def paired_name(self) -> Optional[str]:
        """None when no main device has paired with the current code; otherwise its name ("your main device" when it never said)."""
        if not self.data.get("paired"):
            return None
        return str(self.data.get("paired_name") or "") or "your main device"

    def note_pairing(self, name: str) -> None:
        name = (name or "").strip()[:60]
        if not self.data.get("paired") or (name and name != self.data.get("paired_name")):
            self.set(paired=True, paired_name=name or str(self.data.get("paired_name") or ""))

    def new_code_recent(self) -> bool:
        """Was Make new pairing code used on this device less than an hour ago?"""
        try:
            return 0 <= time.time() - float(self.data.get("code_made_at") or 0) < NEW_CODE_HINT_SECONDS
        except (TypeError, ValueError):
            return False

    def clear_pairing(self) -> None:
        self.set(paired=False, paired_name="")

    def note_start(self, version: str = "") -> None:
        """Remember this start's version. A different one than last time means the Courier was updated: `updated_from` holds the old one
        until the window that opens after this start has shown it (a start with the same version clears it)."""
        version = version or COURIER_VERSION
        old = str(self.data.get("last_version") or "")
        self.set(last_version=version, updated_from=old if old and old != version else "")

    @property
    def id(self) -> str: return self.data["id"]
    @property
    def code(self) -> str: return self.data["code"]
    @property
    def port(self) -> int:
        try:
            return int(self.data.get("port") or COURIER_PORT_DEFAULT)
        except (TypeError, ValueError):
            return COURIER_PORT_DEFAULT


class _Client:
    def __init__(self, ws: web.WebSocketResponse, ip: str, name: str):
        self.ws, self.ip, self.name = ws, ip, name
        self.event = asyncio.Event()
        self.connected_at = time.time()


# --------------------------------------------------------------------------------------------
# The Courier
# --------------------------------------------------------------------------------------------
class Courier:
    def __init__(self, settings: CourierSettings, log: Optional[logging.Logger] = None, adapters: Optional[list] = None,
                 bind_ips: Optional[list] = None, discovery_port: int = other_pcs.DISCOVERY_PORT,
                 keep_awake: Optional[system.KeepAwake] = None, startup: Optional[system.StartWithWindows] = None,
                 gpu_reader: Callable[[], list] = system.read_gpus, cards_fn: Callable[[], list] = gpu_choice.cached_cards):
        self.settings = settings
        self._cards_fn = cards_fn
        self.log = log or logging.getLogger("courier")
        self.local = CourierLocalModels(settings.load_cfg, settings.save_cfg, self.log,
                                        pids_file=str(settings.path.parent / "local_models_pids.json"),
                                        gpu_choice_fn=gpu_choice_wiring.local_choice_fn(settings.load_cfg), cards_fn=cards_fn)
        self.monitor = GpuMonitor(self.local, settings.load_cfg, settings.save_cfg, self.log)
        # This device's model settings are SENT by the main device and applied here before any model starts (local_engine_sync.py); it never edits them itself.
        self.engine_receiver = local_engine_sync.DeviceReceiver(self.local.engine_settings, settings.load_cfg, settings.save_cfg, local_models.LLAMA_CPP_BUILD, self.log)
        self.local.start_gate = self.engine_receiver.gate
        self._tasks: set = set()
        self.scanner = courier_models.Scanner()
        self.voice = voice_courier.CourierVoice(lambda: voice_downloads.default_voice_folder(settings.path.parent, self._chosen_models_folder()),
                                                lambda: voice_downloads.downloads_plan(settings.path.parent, self._chosen_models_folder())["folder"], self.log,
                                                changed=self.signal_change, gpu_choice_fn=gpu_choice_wiring.choice_fn(settings.load_cfg, gpu_choice_wiring.VOICE),
                                                cards_fn=cards_fn, redact=lambda s: mask_log_line(s, settings.code))          # what the engine prints is masked like the log (security review SF8)
        self.decisions = decision_courier.CourierDecisions(
            lambda: (self.local.folder or settings.path.parent / "models") / "decisions", settings, self.log, self.local,
            ensure_folder=lambda: self.local.set_folder(str(settings.path.parent / "models")), changed=self.signal_change,
            gpu_choice_fn=gpu_choice_wiring.choice_fn(settings.load_cfg, gpu_choice_wiring.DECISIONS), cards_fn=cards_fn, redact=lambda s: mask_log_line(s, settings.code))
        self.claude = claude_courier.ClaudeHost(settings.path.parent / "claude", self.log, changed=self.signal_change)      # Anthropic's Claude program on this device (claude_courier.py)
        self.copilot = copilot_courier.CopilotHost(settings.path.parent / "copilot", self.log, changed=self.signal_change)      # GitHub's Copilot program on this device (copilot_courier.py)
        self.update = courier_update.UpdateManager(self, Path(__file__).resolve().parent, COURIER_BUILD, self.log)      # a signed Courier update from the main device (courier_update.py)
        self._speaking = 0                                  # voice lines being made right now (an update waits for them unless the person says Update anyway)
        self._stop_event: Optional[asyncio.Event] = None    # set by _serve; the update sets it to let the helper swap the files
        self.transfers = file_transfer.TransferReceiver(self._transfer_dest, self.log, self.signal_change, self._transfer_file_done,
                                                        resolve_voice=lambda spec: self.voice.resolve_voice_dest(spec),
                                                        on_voice_done=lambda inc: self.voice.on_voice_file_done(inc),
                                                        resolve_decision=lambda spec: self.decisions.resolve_decision_dest(spec),
                                                        on_decision_done=lambda inc: self.decisions.on_decision_file_done(inc),
                                                        resolve_update=lambda spec: self.update.resolve_dest(spec))
        self.adapters: list[Adapter] = adapters if adapters is not None else [
            OllamaAdapter(self.log), LmStudioAdapter(self.log), LlamaCppAdapter(self.log, self.local)]
        for a in self.adapters:
            a.changed = self.signal_change
        self.bind_ips = bind_ips
        self.discovery_port = discovery_port
        self.keep_awake = keep_awake or system.KeepAwake()
        self.startup = startup
        self._gpu_reader = gpu_reader
        self._runner: Optional[web.AppRunner] = None
        self._transport = None
        self._ticker: Optional[asyncio.Task] = None
        self.addresses: list[str] = []
        self.error = ""
        self.started_at = time.time()
        self.clients: set[_Client] = set()
        self.last_seen = 0.0
        self.last_seen_ip = ""
        self.last_client_name = ""
        self._bad: dict[str, deque] = {}
        self._clock = time.monotonic
        self._refused: dict[str, dict] = {}              # address -> {"last", "unlogged"}: Devices that tried a wrong pairing code (never the code itself)
        self._no_code: dict[str, float] = {}             # address -> when "asked without a pairing code" was last logged
        self._welcomed: set[str] = set()                 # addresses whose first right-code connection is already in the log
        self._report: Optional[tuple] = None            # (monotonic time, report)
        self._changes = 0                               # counts changes, so a report built across one is never trusted
        self._report_lock = asyncio.Lock()
        self._signature = ""
        self._connected_logged = ""
        self.on_state_change: Callable[[], None] = lambda: None     # the tray uses this to refresh its icon

    # ---- which graphics card each local AI uses (gpu_choice_wiring.py) -------------------------------------------
    def gpu_block(self) -> dict:
        try:
            cards = self._cards_fn()
        except Exception:
            cards = []
        return gpu_choice_wiring.build_block(self.local, self.decisions.engine, self.voice.engine, gpu_choice_wiring.stored_choices(self.settings.load_cfg), cards)

    async def _h_gpu_get(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.gpu_block))

    async def _h_gpu_set(self, request: web.Request) -> web.Response:
        body = await self._body(request)

        def apply() -> dict:
            cards = self._cards_fn()
            block = gpu_choice_wiring.build_block(self.local, self.decisions.engine, self.voice.engine, gpu_choice_wiring.stored_choices(self.settings.load_cfg), cards)
            cfg = self.settings.load_cfg()
            gpu_choice_wiring.set_choice(cfg, block, cards, str(body.get("ai") or ""), body.get("cardId"))
            self.settings.save_cfg(cfg)
            return self.gpu_block()
        try:
            block = await asyncio.to_thread(apply)
        except gpu_choice_wiring.ChoiceError as e:
            raise AdapterError(e.status, e.message)
        self.signal_change()
        return web.json_response(block)

    # ---- settings the player can flip ---------------------------------------------------------------
    def apply_settings(self) -> None:
        """Make this device match the saved settings (at start, and after a change)."""
        other_pcs.set_extra_networks(self.settings.data.get("private_networks"))          # the VPN ranges the player listed: what this device accepts and listens on
        self.keep_awake.set(bool(self.settings.data.get("keep_awake")))
        if self.startup is not None and self.startup.supported:
            self.startup.set(bool(self.settings.data.get("start_with_windows")))

    def new_code(self) -> str:
        code = other_pcs.new_pairing_code()
        self.settings.set(code=code, paired=False, paired_name="", code_made_at=time.time())       # a new code un-pairs: the old main device no longer holds it
        self._bad.clear()
        self._refused.clear()                               # a new situation: the next wrong try is written again
        self._welcomed.clear()
        for c in list(self.clients):                      # whoever was connected holds the old code
            asyncio.get_running_loop().create_task(c.ws.close(code=4001, message=b"new pairing code"))
        self.log.info("Created a new pairing code. Update the code on your main device to reconnect.")
        self.signal_change()
        return code

    # ---- the report -------------------------------------------------------------------------------------
    def models_dir(self) -> Optional[Path]:
        return self.local.folder

    def _chosen_models_folder(self):
        """The models folder once the player picked one (or models were installed into it); None before, so voice stays in the Courier's own folder."""
        return self.local.folder if self.models_folder_chosen() else None

    def models_folder_chosen(self) -> bool:
        """Has the player picked where models are saved here (or already downloaded some into the default)? Until then the main device asks."""
        return bool(self.settings.data.get("models_folder_chosen")) or any(not m.custom for m in self.local.installed())

    async def build_report(self, fresh: bool = False) -> dict:
        snaps = await asyncio.gather(*(a.snapshot(fresh) for a in self.adapters), return_exceptions=True)
        programs = []
        for a, s in zip(self.adapters, snaps):
            if isinstance(s, Exception):
                self.log.error(f"{a.name}: reading its state failed: {s}")
                s = {"id": a.id, "name": a.name, "installed": False, "running": False, "version": None, "state": "error",
                     "error": "Couldn't check this program's status.", "capabilities": dict(a.capabilities), "notes": [], "models": [], "downloads": []}
            if s["id"] == "llamacpp":                       # models copied from the main device show beside real downloads
                s = {**s, "capabilities": {**s["capabilities"], "receive": True}, "downloads": list(s["downloads"]) + self.transfers.downloads_json()}
            if s["installed"] or s["id"] == "llamacpp":
                programs.append(s)
        gpus = await asyncio.to_thread(self._gpu_reader)
        voice = await asyncio.to_thread(self.voice.status_json, False, self.transfers.voice_json())
        decisions = await asyncio.to_thread(self.decisions.status_json, False, self.transfers.decision_json())
        gpu_block = await asyncio.to_thread(self.gpu_block)
        return {
            "courier": {"id": self.settings.id, "name": other_pcs.short_hostname(), "version": COURIER_VERSION, "build": COURIER_BUILD, "built": read_built(),
                        "platform": sys.platform, "uptime_seconds": system.read_uptime_seconds(),
                        "running_seconds": int(time.time() - self.started_at), "pairing_trouble": self.pairing_trouble(),
                        "paired_name": self.settings.paired_name(), "connected_to": (self.connected()[1] or None) if self.connected()[0] else None},
            "gpus": gpus,
            "pc_memory": system.read_pc_memory(),
            "disk": system.read_disk(self.models_dir()),
            "power": system.read_power(),
            "programs": programs,
            "voice": voice,
            "decisions": decisions,
            "claude": self.claude.status_json(),
            "copilot": self.copilot.status_json(),
            "gpu_choice": gpu_block,
            "engine_settings": self.engine_receiver.status(),          # the hash of the model settings this device holds, and whether it has been told since it connected
            "settings": {"keep_awake": bool(self.settings.data.get("keep_awake")), "keep_awake_active": self.keep_awake.on,
                         "start_with_windows": bool(self.settings.data.get("start_with_windows")),
                         "private_networks": other_pcs.extra_networks(),
                         "start_with_windows_supported": bool(self.startup and self.startup.supported),
                         "models_folder": str(self.local.folder) if self.local.folder else None,
                         "models_folder_chosen": self.models_folder_chosen(),
                         "firewall": self.settings.data.get("firewall", "unknown")},
            "capabilities": self.update.report()["capabilities"], "last_update": self.update.last_update(),
            "generated_at": time.time(),
        }

    async def report(self, fresh: bool = False, max_age: Optional[float] = None) -> dict:
        """This device's report. Cached for a moment (many things ask); `max_age` accepts a recent one. `fresh` rebuilds AND makes every
        program be asked again (otherwise each adapter answers from its own slower memory, so an idle Courier stays quiet)."""
        ttl = 0.0 if fresh else (REPORT_CACHE_SECONDS if max_age is None else max_age)
        async with self._report_lock:
            now = time.monotonic()
            if self._report and now - self._report[0] < ttl:
                return self._report[1]
            gen = self._changes
            rep = await self.build_report(fresh)
            # A change that landed while this was being built makes it already out of date: keep it, but expired, so the next ask rebuilds
            self._report = (time.monotonic() if gen == self._changes else 0.0, rep)
            return rep

    @staticmethod
    def signature(rep: dict) -> str:
        sig = []
        for p in rep["programs"]:
            sig.append([p["id"], p["state"], p["running"], p["version"], p.get("error"),
                        [[m["id"], m["state"], m["loaded"], m.get("on_card_percent")] for m in p["models"]],
                        [[d["model"], d["state"], (d["percent"] or 0) // 5] for d in p["downloads"]]])
        v = rep.get("voice") or {}
        sig.append([v.get("state"), v.get("detail"), v.get("licenseAccepted"), (v.get("install") or {}).get("state"), (v.get("install") or {}).get("step"),
                    ((v.get("install") or {}).get("percent") or 0) // 5, (v.get("receiving") or {}).get("state"), ((v.get("receiving") or {}).get("percent") or 0) // 5,
                    v.get("linesWaiting")])
        d = rep.get("decisions") or {}
        sig.append([d.get("state"), d.get("detail"), d.get("installed"), d.get("runOn"), (d.get("install") or {}).get("state"), (d.get("install") or {}).get("step"),
                    ((d.get("install") or {}).get("percent") or 0) // 5, (d.get("receiving") or {}).get("state"), ((d.get("receiving") or {}).get("percent") or 0) // 5,
                    d.get("settings")])
        c = rep.get("claude") or {}
        sig.append([c.get("state"), c.get("version"), c.get("updateAvailable"), c.get("warning"), (c.get("install") or {}).get("state"), (c.get("install") or {}).get("step"),
                    ((c.get("install") or {}).get("percent") or 0) // 5, [m.get("id") for m in c.get("models") or []], c.get("busy"), (c.get("signin") or {}).get("state")])
        cp = rep.get("copilot") or {}
        sig.append([cp.get("state"), cp.get("version"), cp.get("updateAvailable"), cp.get("warning"), (cp.get("install") or {}).get("state"), (cp.get("install") or {}).get("step"),
                    ((cp.get("install") or {}).get("percent") or 0) // 5, [m.get("id") for m in cp.get("models") or []], cp.get("busy"), (cp.get("signin") or {}).get("state"), (cp.get("signin") or {}).get("code")])
        g = rep.get("gpu_choice") or {}
        sig.append([[c.get("id") for c in g.get("cards") or []], [[a.get("key"), a.get("choice"), a.get("note"), a.get("running"), a.get("restartNeeded")] for a in g.get("ais") or []]])
        sig.append(rep["settings"])
        sig.append(rep.get("engine_settings"))
        return hashlib.sha1(json.dumps(sig, sort_keys=True, default=str).encode()).hexdigest()

    def active(self) -> bool:
        rep = self._report[1] if self._report else None
        if not rep:
            return False
        return any(p["state"] in ("busy", "loading") or any(d["state"] == "running" for d in p["downloads"]) for p in rep["programs"])

    def signal_change(self) -> None:
        self._changes += 1
        if self._report:                                   # what we know is out of date now: the next ask rebuilds it
            self._report = (0.0, self._report[1])
        for c in list(self.clients):
            c.event.set()
        try:
            self.on_state_change()
        except Exception:
            pass

    async def _tick(self) -> None:
        """Once a second while anyone's connected: if anything changed, push at once (this is what makes the main device
        mark a stopped program, or a download's progress, instantly rather than at the next 5 s beat)."""
        while True:
            await asyncio.sleep(1.0)
            if not self.clients:
                continue
            try:
                rep = await self.report()
                sig = self.signature(rep)
                if sig != self._signature:
                    self._signature = sig
                    self.signal_change()
            except Exception:
                self.log.exception("Checking this device's state failed")

    # ---- connection status (the tray's dot) ---------------------------------------------------------------
    def connected(self) -> tuple[bool, str]:
        if self.clients:
            c = next(iter(self.clients))
            return True, c.name or c.ip
        if self.last_seen and time.time() - self.last_seen < CONNECTED_WINDOW_SECONDS:
            return True, self.last_client_name or self.last_seen_ip
        return False, ""

    # ---- who tried what (the log, and the status the tray and the window show) ------------------------------------
    def _note_wrong_code(self, ip: str) -> None:
        """A device tried a pairing code that doesn't match. One log line the first time, then (counting the tries in between) only after 10 minutes of quiet from that address.
        The code that was tried is never written anywhere."""
        now = self._clock()
        e = self._refused.get(ip)
        if e is None:
            self.log.info(f"A device at {ip} tried to connect with a pairing code that doesn't match.")
            self._refused[ip] = {"last": now, "unlogged": 0}
            self.signal_change()
            return
        quiet = now - e["last"] > REFUSAL_QUIET_SECONDS
        e["unlogged"] += 1
        e["last"] = now
        if quiet:
            n = e["unlogged"]
            self.log.info(f"A device at {ip} tried to connect with a pairing code that doesn't match." + (f" (tried {n} times)" if n > 1 else ""))
            e["unlogged"] = 0
            self.signal_change()

    def _note_no_code(self, ip: str) -> None:
        now = self._clock()
        if ip not in self._no_code or now - self._no_code[ip] >= NO_CODE_LOG_SECONDS:
            self._no_code[ip] = now
            self.log.info(f"Something at {ip} asked for Courier without a pairing code.")

    def _note_right_code(self, ip: str) -> None:
        if ip not in self._welcomed:
            self._welcomed.add(ip)
            self.log.info(f"A device at {ip} connected.")
        if self._refused.pop(ip, None) is not None:
            self.signal_change()

    def pairing_trouble(self) -> Optional[dict]:
        """The device that most recently tried a wrong code, if that was within the last 10 minutes and no main device is connected now: {"address", "new_code": bool}. None otherwise."""
        if self.connected()[0]:
            return None
        now = self._clock()
        recent = [(e["last"], ip) for ip, e in self._refused.items() if now - e["last"] <= REFUSAL_QUIET_SECONDS]
        if not recent:
            return None
        return {"address": max(recent)[1], "new_code": self.settings.new_code_recent()}

    def _note_connected(self) -> None:
        ok, name = self.connected()
        if ok and name != self._connected_logged:
            self._connected_logged = name
            self.log.info(f"Connected to {name}")
        elif not ok and self._connected_logged:
            self.log.info(f"{self._connected_logged} disconnected. Waiting for reconnect...")
            self._connected_logged = ""
        try:
            self.on_state_change()
        except Exception:
            pass

    # ---- the door's guard -----------------------------------------------------------------------------------
    def _check(self, request: web.Request) -> Optional[web.Response]:
        """Every request, and the live connection's upgrade, comes through here: home network only, AND the pairing code."""
        ip = request.remote or ""
        if other_pcs.ip_class(ip) not in ("private", "loopback"):
            return _err(403, "Connection refused: Courier only connects to devices on your private network or Tailscale.")
        window = self._bad.setdefault(ip, deque())
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        auth = request.headers.get("Authorization", "")
        given = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get(other_pcs.CODE_HEADER, "")
        matches = other_pcs.codes_match(given, self.settings.code)
        if not matches and given.strip():
            self._note_wrong_code(ip)                    # said in the log and the status, even when the minute's limit is already reached
        if len(window) >= other_pcs.MAX_BAD_CODES:
            return _err(429, "Too many wrong codes. Wait a minute and try again.", headers={"Retry-After": "60"})
        if not matches:
            window.append(now)
            if not given.strip():
                self._note_no_code(ip)
            return _err(403, "Wrong pairing code. Check the code shown on your Courier device.")
        if other_pcs.ip_class(ip) != "loopback":
            self._note_right_code(ip)
        if other_pcs.ip_class(ip) != "loopback":            # this device's own status command / ready window isn't "the main device"
            self.last_seen, self.last_seen_ip = time.time(), ip
        name = re.sub(r"[\x00-\x1f\x7f]", "", request.headers.get("X-Courier-Client", "")).strip()          # (no control character reaches a log or the window)
        if name:
            self.last_client_name = name[:60]
        if other_pcs.ip_class(ip) != "loopback":            # a main device with the right code: this device is paired (saved, so it survives restarts and updates)
            try:
                self.settings.note_pairing(name)
            except OSError:
                self.log.warning("Couldn't save which main device this one is paired with")
        self._note_connected()
        return None

    def guarded(self, fn):
        async def handler(request: web.Request):
            denied = self._check(request)
            if denied:
                return denied
            try:
                return await fn(request)
            except AdapterError as e:
                return _err(e.status, e.message)
            except LocalModelError as e:
                return _err(e.status, e.message)
            except file_transfer.TransferError as e:
                return self._transfer_reply(e)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                return _err(502, "The program didn't answer. Try starting it again on the Other devices tab.")
        return handler

    # ---- handlers --------------------------------------------------------------------------------------------
    def _adapter(self, request: web.Request) -> Adapter:
        a = next((x for x in self.adapters if x.id == request.match_info["program"]), None)
        if not a:
            raise AdapterError(404, "This device doesn't have that program installed.")
        return a

    # ---- files sent from the main device (file_transfer.py) --------------------------------------------------------
    def _transfer_dest(self, spec: dict) -> Path:
        folder = self.local.folder
        if not folder:
            raise file_transfer.TransferError(409, "{pc} hasn't picked a folder for models yet.")
        return Path(folder) / "models" / str(spec["model"]) / str(spec["file"])

    def _transfer_file_done(self, inc) -> None:
        """A copied model is a normal downloaded one. A catalog model is recognised by where its files sit; a model the player
        added on the main device by its own path gets added here as soon as all its files (model + vision file) are in."""
        custom = inc.spec.get("custom")
        if not custom:
            return
        folder = inc.dest.parent
        if not isinstance(custom, dict) or not file_transfer.valid_gguf_name(custom.get("model_file")) or (custom.get("mmproj_file") and not file_transfer.valid_gguf_name(custom.get("mmproj_file"))):
            return                                          # (`begin` already refuses such names; a name with a folder in it is never joined onto a path)
        main, mm = folder / str(custom.get("model_file", "")), (folder / str(custom["mmproj_file"]) if custom.get("mmproj_file") else None)
        if main.is_file() and (mm is None or mm.is_file()):
            try:
                self.local.add_custom(str(main), str(mm) if mm else "")
            except LocalModelError as e:
                self.log.warning(f"Couldn't add the copied model {main.name}: {e.message}")

    @staticmethod
    def _transfer_reply(e: "file_transfer.TransferError") -> web.Response:
        body = {"error": {"message": e.message, "type": "api_error", "code": None}}
        if e.offset is not None:
            body["offset"] = e.offset
        return web.json_response(body, status=e.status)

    async def _h_transfer_begin(self, request: web.Request) -> web.Response:
        try:
            body = await self._body(request)
            if body.get("kind") == file_transfer.UPDATE_KIND:          # an update's bytes begin only through /courier/v1/update/begin (its own guard, lock and limits)
                return courier_update.error_response("update_failed", 403)
            return web.json_response(self.transfers.begin(body))
        except file_transfer.TransferError as e:
            return self._transfer_reply(e)

    async def _h_transfer_chunk(self, request: web.Request) -> web.Response:
        if self.update.owns(request.match_info["tid"]):
            denied = self.update.gate(request, json_body=False)
            if denied:
                return denied
        try:
            offset = int(request.query.get("offset", "-1"))
        except ValueError:
            offset = -1
        if offset < 0:
            return _err(400, "The chunk's position is missing.")
        try:
            done = await self.transfers.chunk(request.match_info["tid"], offset, request.content.iter_chunked(1024 * 1024))
            return web.json_response({"offset": done})
        except file_transfer.TransferError as e:
            return self._transfer_reply(e)

    async def _h_transfer_finish(self, request: web.Request) -> web.Response:
        if self.update.owns(request.match_info["tid"]):
            denied = self.update.gate(request, json_body=False)
            if denied:
                return denied
        try:
            return web.json_response(await self.transfers.finish(request.match_info["tid"]))
        except file_transfer.TransferError as e:
            return self._transfer_reply(e)

    async def _h_transfer_cancel(self, request: web.Request) -> web.Response:
        if self.update.owns(request.match_info["tid"]):
            denied = self.update.gate(request, json_body=False)
            if denied:
                return denied
        self.transfers.cancel(request.match_info["tid"])
        return web.json_response({"ok": True})

    # ---- a signed Courier update (courier_update.py) -------------------------------------------------------------
    async def _h_update_begin(self, request: web.Request) -> web.Response:
        return self.update.gate(request) or await self.update.begin(request)

    async def _h_update_stage(self, request: web.Request) -> web.Response:
        return self.update.gate(request) or await self.update.stage(request)

    async def _h_update_apply(self, request: web.Request) -> web.Response:
        return self.update.gate(request) or await self.update.apply(request)

    async def _h_update_cancel(self, request: web.Request) -> web.Response:
        return self.update.gate(request) or await self.update.cancel(request)

    def exit_for_update(self) -> None:
        """The helper is running: tell the main device we are updating (not lost), then let _serve return so this process ends and the files can be swapped."""
        for c in list(self.clients):
            asyncio.get_running_loop().create_task(c.ws.close(code=4002, message=b"updating"))
        if self._stop_event is not None:
            self._stop_event.set()

    async def _body(self, request: web.Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            raise AdapterError(400, "Received an invalid request.")
        if not isinstance(body, dict):
            raise AdapterError(400, "Received an invalid request.")
        return body

    async def _h_report(self, request: web.Request) -> web.Response:
        return web.json_response(await self.report(fresh=request.query.get("fresh") == "1"))

    async def _h_log(self, request: web.Request) -> web.Response:
        """This device's recent log for the main device's Log button: read-only, the last lines only, with anything secret masked."""
        try:
            n = int(request.query.get("lines", LOG_DEFAULT_LINES))
        except ValueError:
            n = LOG_DEFAULT_LINES
        lines = read_log_tail(self.settings.path.parent / "courier.log", max(1, min(n, LOG_MAX_LINES)), code=self.settings.code)
        return web.json_response({"lines": lines, "name": other_pcs.short_hostname()})

    async def _h_memory(self, request: web.Request) -> web.Response:
        data = await self.monitor.payload(include_skyrim=False)
        return web.json_response(data if data and data.get("available") else {"available": False})

    async def _h_action(self, request: web.Request) -> web.Response:
        a = self._adapter(request)
        action = request.match_info["action"]
        body = await self._body(request) if request.can_read_body else {}
        model, wait = str(body.get("model") or ""), bool(body.get("wait"))
        if action in ("start", "restart", "stop"):
            await getattr(a, action)()
            a.refresh()                                    # whatever the Courier itself just did shows at once, not at the next slow check
            self.signal_change()
            return web.json_response({"ok": True})
        if not model:
            raise AdapterError(400, "You didn't choose a model.")
        if action == "load":
            await a.load(model, wait=wait)
        elif action == "unload":
            await a.unload(model)
        elif action == "delete":
            await a.delete(model)
        elif action == "download":
            d = await a.download(model)
            if wait and d.task:
                await asyncio.gather(d.task, return_exceptions=True)
            a.refresh()
            self.signal_change()
            return web.json_response({"ok": True, "download": d.to_json()})
        elif action == "cancel":
            await a.cancel_download(model)
        else:
            raise AdapterError(404, "Didn't recognize that command.")
        a.refresh()
        self.signal_change()
        return web.json_response({"ok": True})

    async def _h_uninstall_program(self, request: web.Request) -> web.Response:
        """Remove a program MultiProxy installed on this device. Only the llama.cpp program has this route (Higgs, the decision model and Claude have their own); the program is
        named by the route, never by a path in the request. The Courier's own folder is protected: this can never remove the Courier."""
        a = self._adapter(request)
        if a.id != "llamacpp":
            raise AdapterError(400, f"{a.name} wasn't installed by MultiProxy, so it can't be removed from here.")
        freed = await a.uninstall(protected=(self.settings.path.parent,))
        a.refresh()
        self.signal_change()
        return web.json_response({"ok": True, "freedBytes": freed})

    async def _h_engine_get(self, request: web.Request) -> web.Response:
        a = self._adapter(request)
        if a.id != "llamacpp":
            raise AdapterError(400, f"{a.name} has no engine to update from here.")
        return web.json_response(await a.engine_info())

    async def _h_engine_post(self, request: web.Request) -> web.Response:
        """The main device's player pressed Update, or picked another version. Nothing here ever changes the engine on its own."""
        a = self._adapter(request)
        if a.id != "llamacpp":
            raise AdapterError(400, f"{a.name} has no engine to update from here.")
        body = await self._body(request)
        action = str(body.get("action") or "")
        if action == "update":
            await a.engine_update()
        elif action == "use":
            await a.engine_use(str(body.get("kind") or ""))
        else:
            raise AdapterError(400, "Didn't recognize that command.")
        a.refresh()
        self.signal_change()
        return web.json_response({"ok": True})

    async def _h_settings(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        if "keep_awake" in body:
            on = bool(body["keep_awake"])
            self.settings.set(keep_awake=on)
            if not self.keep_awake.set(on):
                raise AdapterError(502, "Couldn't change the sleep settings on this device.")
        if "start_with_windows" in body:
            on = bool(body["start_with_windows"])
            if not (self.startup and self.startup.supported):
                raise AdapterError(400, "The system installer manages auto-start on this device.")
            if not self.startup.set(on):
                raise AdapterError(502, "Couldn't update the Start with Windows setting.")
            self.settings.set(start_with_windows=on)
        if "private_networks" in body:
            try:
                nets = other_pcs.clean_private_networks(body["private_networks"])
            except other_pcs.OtherPcsError as e:
                raise AdapterError(e.status, e.message)
            self.settings.set(private_networks=nets)
            other_pcs.set_extra_networks(nets)
            await self.refresh_listeners()
        if body.get("models_folder"):
            self._use_models_folder(str(body["models_folder"]))
        self.signal_change()
        return web.json_response((await self.report(fresh=True))["settings"])

    # ---- models already on this device, and where new ones are saved ------------------------------------------------
    def _added_paths(self) -> set[str]:
        return {os.path.normcase(str(m.path)) for m in self.local.installed()}

    def _mark_added(self, items: list[dict]) -> list[dict]:
        have = self._added_paths()
        return [{**i, "added": os.path.normcase(i["path"]) in have} for i in items]

    def _use_models_folder(self, text: str) -> Path:
        try:
            folder = courier_models.check_folder(text)
        except courier_models.FolderProblem as e:
            raise AdapterError(400, f"Couldn't use that folder on {other_pcs.short_hostname()}: {e}.")
        self.local.set_folder(str(folder))
        self.settings.set(models_folder_chosen=True)
        return folder

    async def _h_models_find(self, request: web.Request) -> web.Response:
        body = await self._body(request) if request.can_read_body else {}
        lms = next((a for a in self.adapters if a.id == "lmstudio"), None)
        known = [("lmstudio", lms.models_dir() if lms else None),
                 ("courier", (self.local.folder / "models") if self.local.folder else None)]
        extra = str(body.get("folder") or "").strip().strip('"')
        if extra:
            p = Path(os.path.expandvars(extra)).expanduser()
            if not p.is_absolute() or not p.is_dir():
                raise AdapterError(400, f"Couldn't find that folder on {other_pcs.short_hostname()}.")
            if not safe_paths.is_local_fixed(p):
                raise AdapterError(400, f"Couldn't use that folder on {other_pcs.short_hostname()}: {safe_paths.NOT_LOCAL_FRAGMENT}.")
            known.append(("folder", p))
        searched = [{"source": src, "path": str(f), "exists": bool(f and Path(f).is_dir())} for src, f in known if f]
        items = await asyncio.to_thread(courier_models.find, [(src, f) for src, f in known if f])
        return web.json_response({"found": self._mark_added(items), "searched": searched, "scan": self._scan_json()})

    def _scan_json(self) -> dict:
        snap = self.scanner.snapshot()
        return {**snap, "found": self._mark_added(snap["found"])}

    async def _h_scan_status(self, request: web.Request) -> web.Response:
        return web.json_response(self._scan_json())

    async def _h_scan_action(self, request: web.Request) -> web.Response:
        if request.match_info["action"] == "start":
            self.scanner.start()
            self.log.info("Scanning this device's drives for model files (asked from the main device).")
        else:
            await asyncio.to_thread(self.scanner.stop)
        return web.json_response(self._scan_json())

    async def _h_models_add(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        paths = body.get("paths")
        if not isinstance(paths, list) or not paths:
            raise AdapterError(400, "You didn't choose any model files.")
        added, problems = [], []
        have = self._added_paths()
        for raw in paths[:200]:
            path = str(raw)
            if not Path(path).is_absolute():
                problems.append({"path": path, "message": "Type a full folder path on that device."})
                continue
            if os.path.normcase(path) in have:
                problems.append({"path": path, "message": "That model is already added."})
                continue
            item = await asyncio.to_thread(courier_models.describe, path)
            if not item:
                problems.append({"path": path, "message": "That file won't work. Pick a supported model file."})
                continue
            try:
                m = self.local.add_custom(path, item["mmproj"] or "")
            except LocalModelError as e:
                problems.append({"path": path, "message": e.message})
                continue
            added.append({"id": m.id, "name": m.name})
            self.log.info(f"Added {m.name} to llama.cpp where it sits (not copied).")
        if added:
            for a in self.adapters:
                a.refresh()
            self.signal_change()
        return web.json_response({"added": added, "problems": problems})

    async def _h_drives(self, request: web.Request) -> web.Response:
        default = self.settings.path.parent / "models"
        return web.json_response({"drives": await asyncio.to_thread(courier_models.drives), "name": other_pcs.short_hostname(),
                                  "current": str(self.local.folder) if self.local.folder else None,
                                  "default": str(default), "chosen": self.models_folder_chosen()})

    async def _h_models_folder(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        folder = self._use_models_folder(str(body.get("path") or ""))
        self.signal_change()
        return web.json_response({"folder": str(folder), "disk": system.read_disk(folder)})

    # ---- Higgs voice on this device (voice_courier.py) ---------------------------------------------------------------------------------
    @staticmethod
    def _voice_err(e: VoiceEngineError) -> web.Response:
        return _err(e.status, e.message)

    async def _h_voice_status(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.voice.status_json, True, self.transfers.voice_json()))

    async def _h_voice_fit(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(await asyncio.to_thread(self.voice.files_needed, request.query.get("model") or None))
        except VoiceEngineError as e:
            return self._voice_err(e)

    async def _h_voice_license(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        try:
            return web.json_response(self.voice.accept_license(str(body.get("from") or ""), str(body.get("licenseVersion") or ""), str(body.get("model") or "") or None))
        except VoiceEngineError as e:
            return self._voice_err(e)

    async def _h_voice_install(self, request: web.Request) -> web.Response:
        body = await self._body(request) if request.can_read_body else {}
        model = str(body.get("model") or "") or None
        try:
            if model:
                await self.voice.engine.select_model(model)          # installing a voice makes it the chosen one on that PC
            return web.json_response(self.voice.start_install(str(body.get("source") or "kept"), model))
        except VoiceEngineError as e:
            return self._voice_err(e)

    async def _h_voice_action(self, request: web.Request) -> web.Response:
        action, eng = request.match_info["action"], self.voice.engine
        try:
            if action == "cancel":
                eng.cancel_install()
            elif action == "start":
                await eng.start()
            elif action == "stop":
                await eng.stop()
            elif action == "restart":
                await eng.restart()
            elif action == "select":
                body = await self._body(request) if request.can_read_body else {}
                await eng.select_model(str(body.get("model") or ""))
            elif action == "uninstall":
                body = await self._body(request) if request.can_read_body else {}
                await eng.uninstall(keep_clips=not bool(body.get("alsoClips")), model_key=str(body.get("model") or "") or None)
        except VoiceEngineError as e:
            return self._voice_err(e)
        self.voice.invalidate()
        return web.json_response(await asyncio.to_thread(self.voice.status_json, True, self.transfers.voice_json()))

    async def _h_voice_log(self, request: web.Request) -> web.Response:
        return web.json_response({"log": self.voice.engine.log_tail(200)})

    async def _h_voice_output(self, request: web.Request) -> web.Response:
        """Read-only, on the same authenticated channel as the rest: what the voice engine printed after line `since`."""
        def num(name: str, default: int) -> int:
            try:
                return int(request.query.get(name, default))
            except ValueError:
                return default
        return web.json_response(self.voice.engine.output_since(num("since", 0), num("limit", 200)))

    async def _h_voice_speak(self, request: web.Request) -> web.StreamResponse:
        self._speaking += 1
        try:
            return await self.voice.speak(request)
        finally:
            self._speaking -= 1

    async def _h_voice_clips(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.voice.clips_listing))

    async def _h_voice_clips_audio(self, request: web.Request) -> web.Response:
        data = await asyncio.to_thread(self.voice.clips_audio, request.query.get("voice", ""), request.query.get("which", ""))
        if data is None:
            return _err(404, "That voice doesn't have a sample clip.")
        return web.json_response({"wavB64": base64.b64encode(data).decode("ascii")})

    async def _h_voice_clips_add(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        try:
            return web.json_response(await asyncio.to_thread(self.voice.clips_add, str(body.get("voice") or ""), str(body.get("kind") or "yours"), str(body.get("dataB64") or ""), str(body["words"]) if body.get("words") is not None else None))
        except VoiceEngineError as e:
            return self._voice_err(e)

    async def _h_voice_clips_words(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        return web.json_response(await asyncio.to_thread(self.voice.clips_words, str(body.get("voice") or ""), str(body.get("words") or "")))

    async def _h_voice_clips_remove(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        return web.json_response(await asyncio.to_thread(self.voice.clips_remove, str(body.get("voice") or "")))

    # ---- Claude on this device (claude_courier.py) ------------------------------------------------------------------------------------------------
    @staticmethod
    def _claude_err(e: claude_courier.ClaudeError) -> web.Response:
        return _err(e.status, e.message, headers={"X-Courier-Kind": e.kind})

    async def _h_claude_status(self, request: web.Request) -> web.Response:
        if request.query.get("fresh") == "1":
            self.claude.refresh_latest_soon(force=True)
        return web.json_response(self.claude.status_json())

    async def _h_claude_signin(self, request: web.Request) -> web.Response:
        """start | code | cancel for signing Claude in from the main device (this device runs `claude setup-token` itself). Behind the pairing code like every route here."""
        step, host = request.match_info["step"], self.claude
        body = await self._body(request) if request.can_read_body else {}
        try:
            if step == "start":
                return web.json_response(await host.start_signin())
            if step == "code":
                return web.json_response(await host.submit_code(str(body.get("code") or "")))
            host.cancel_signin()
        except claude_courier.ClaudeError as e:
            return self._claude_err(e)
        return web.json_response(host.status_json())

    async def _h_claude_action(self, request: web.Request) -> web.Response:
        action, host = request.match_info["action"], self.claude
        body = await self._body(request) if request.can_read_body else {}
        try:
            if action == "install":
                host.start_install()
            elif action == "update":
                host.start_install(updating=True)
            elif action == "cancel":
                host.cancel_install()
            elif action == "signin":
                return web.json_response(await host.sign_in(str(body.get("code") or "")))
            elif action == "signout":
                return web.json_response(host.sign_out())
            elif action == "uninstall":
                result = await host.uninstall()
                self.signal_change()
                return web.json_response(result)
            elif action == "test":
                return web.json_response(await host.test())
        except claude_courier.ClaudeError as e:
            return self._claude_err(e)
        return web.json_response(host.status_json())

    # ---- Copilot on this device (copilot_courier.py) ------------------------------------------------------------------------------------------------
    @staticmethod
    def _copilot_err(e: copilot_courier.CopilotError) -> web.Response:
        return _err(e.status, e.message, headers={"X-Courier-Kind": e.kind})

    async def _h_copilot_status(self, request: web.Request) -> web.Response:
        if request.query.get("fresh") == "1":
            self.copilot.refresh_latest_soon(force=True)
        return web.json_response(self.copilot.status_json())

    async def _h_copilot_action(self, request: web.Request) -> web.Response:
        action, host = request.match_info["action"], self.copilot
        try:
            if action == "install":
                host.start_install()
            elif action == "update":
                host.start_install(updating=True)
            elif action == "cancel":
                host.cancel_install()
            elif action == "signin":
                host.start_signin()
            elif action == "cancelsignin":
                host.cancel_signin()
            elif action == "signout":
                return web.json_response(await host.sign_out())
            elif action == "uninstall":
                result = await host.uninstall()
                self.signal_change()
                return web.json_response(result)
            elif action == "test":
                return web.json_response(await host.test())
        except copilot_courier.CopilotError as e:
            return self._copilot_err(e)
        return web.json_response(host.status_json())

    # ---- the local decision model on this device (decision_courier.py) ------------------------------------------------------------------------------
    @staticmethod
    def _decision_err(e) -> web.Response:
        return _err(getattr(e, "status", 500), getattr(e, "message", str(e)))

    async def _decision_status(self) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.decisions.status_json, True, self.transfers.decision_json()))

    async def _h_decisions_status(self, request: web.Request) -> web.Response:
        return await self._decision_status()

    async def _h_decisions_fit(self, request: web.Request) -> web.Response:
        return web.json_response(await self.decisions.files_needed())

    async def _h_decisions_log(self, request: web.Request) -> web.Response:
        return web.json_response({"log": self.decisions.engine.log_tail(200)})

    async def _h_decisions_install(self, request: web.Request) -> web.Response:
        body = await self._body(request) if request.can_read_body else {}
        try:
            return web.json_response(self.decisions.start_install(str(body.get("source") or "kept"), runner_only=bool(body.get("runnerOnly"))))
        except decision_engine.DecisionError as e:
            return self._decision_err(e)

    async def _h_decisions_settings(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        try:
            self.decisions.set_choices(body.get("model"), body.get("runOn"), body.get("limitSeconds"))
        except decision_engine.DecisionError as e:
            return self._decision_err(e)
        return await self._decision_status()

    async def _h_decisions_action(self, request: web.Request) -> web.Response:
        action, eng = request.match_info["action"], self.decisions.engine
        try:
            if action == "cancel":
                eng.cancel_install()
            elif action == "start":
                await eng.start()
            elif action == "stop":
                await eng.stop()
            elif action == "restart":
                await eng.restart()
            elif action == "uninstall":
                await eng.uninstall()
        except decision_engine.DecisionError as e:
            return self._decision_err(e)
        self.decisions.invalidate()
        return await self._decision_status()

    async def _h_decisions_answer(self, request: web.Request) -> web.Response:
        return await self.decisions.answer(request)

    async def _h_engine_settings_get(self, request: web.Request) -> web.Response:
        return web.json_response(self.engine_receiver.status())

    async def _h_engine_settings_post(self, request: web.Request) -> web.Response:
        """The main device sends this device its model settings (local_engine_sync.payload_for). Applied at once; `restart` (a list of model ids, or "all") restarts those
        running models with them in the background. A settings set made for another engine build is refused (409) with the reason, and no model starts until a matching one arrives."""
        body = await self._body(request)
        try:
            out = self.engine_receiver.apply(body)
        except local_engine_sync.SyncError as e:
            self.signal_change()
            return _err(e.status, e.message)
        restart = body.get("restart")
        ids = ([m for m in self.local.servers if self.local.servers[m].state in ("starting", "running")] if restart == "all"
               else [str(m) for m in restart if str(m) in self.local.servers and self.local.servers[str(m)].state in ("starting", "running")] if isinstance(restart, list) else [])
        if ids:
            async def again():
                for mid in ids:
                    await self.local.restart(mid)
                self.signal_change()
            t = asyncio.create_task(again())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        self.signal_change()
        return web.json_response({**out, "restarting": ids})

    async def _h_new_code(self, request: web.Request) -> web.Response:
        return web.json_response({"code": self.new_code()})

    async def _h_unpair(self, request: web.Request) -> web.Response:
        self.new_code()                  # un-pairs and makes a fresh code; the pairing code on this call was already checked by the door's guard
        return web.json_response({"status": "unpaired"})

    async def _h_ws(self, request: web.Request) -> web.StreamResponse:
        denied = self._check(request)               # checked BEFORE the upgrade: a wrong code never gets a socket
        if denied:
            return denied
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        client = _Client(ws, request.remote or "", request.headers.get("X-Courier-Client", "").strip()[:60])
        self.clients.add(client)
        self.engine_receiver.connected()                   # a (new) connection: wait to be told this device's model settings again before starting a model
        self._note_connected()
        try:
            await ws.send_json({"type": "hello", "courier": {"id": self.settings.id, "name": other_pcs.short_hostname(), "version": COURIER_VERSION}})
            await ws.send_json({"type": "report", "reason": "connect", "report": await self.report(fresh=True)})
            self._signature = self.signature(self._report[1])

            async def reader():
                async for _ in ws:                  # nothing is accepted over this connection; reading keeps ping/close working
                    pass

            async def writer():
                while not ws.closed:
                    try:
                        await asyncio.wait_for(client.event.wait(), ACTIVE_PUSH_SECONDS if self.active() else IDLE_PUSH_SECONDS)
                        reason = "change"
                    except asyncio.TimeoutError:
                        reason = "interval"
                    client.event.clear()            # (cleared BEFORE building, so a change that lands meanwhile wakes the next round)
                    if ws.closed:
                        break
                    rep = await self.report(max_age=0.15 if reason == "change" else None)
                    await ws.send_json({"type": "report", "reason": reason, "report": rep})

            tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except (ConnectionResetError, aiohttp.ClientError):
            pass
        finally:
            self.clients.discard(client)
            self._note_connected()
        return ws

    # ---- chat pass-through -----------------------------------------------------------------------------------
    async def resolve(self, wanted: str) -> tuple[Adapter, str]:
        """Which program answers for `wanted`. `ollama/gemma4:12b` names the program outright; otherwise the first program that
        has a model of that name (a loaded one wins). `local/x` is llama.cpp's own spelling."""
        name = (wanted or "").strip()
        rep = await self.report()
        by_id = {p["id"]: p for p in rep["programs"]}
        if name.lower().startswith("local/"):
            a = next((x for x in self.adapters if x.id == "llamacpp"), None)
            return a, name[6:]
        first, _, rest = name.partition("/")
        explicit = next((x for x in self.adapters if x.id == first.lower()), None)
        if explicit and rest:
            if explicit.id in ("ollama", "lmstudio") and (by_id.get(explicit.id) or {}).get("state") == "stopped":
                raise AdapterError(503, NOT_SERVING.format(program=explicit.name))
            return explicit, rest
        def find(by_id: dict) -> list:
            hits = []
            for a in self.adapters:
                for m in (by_id.get(a.id) or {}).get("models", []):
                    if m["id"].lower() in (name.lower(), name.lower() + ":latest"):
                        hits.append((0 if m["loaded"] else 1, a))
            return hits
        hits = find(by_id)
        if not hits:                  # the cached picture may be a second old (a model was just downloaded): look again, fresh
            by_id = {p["id"]: p for p in (await self.report(fresh=True))["programs"]}
            hits = find(by_id)
        if not hits:
            ours = [p for p in by_id.values() if p["id"] in ("ollama", "lmstudio") and p.get("installed")]
            if ours and all(p.get("state") == "stopped" for p in ours):
                # every program that could have it is off, so of course no model is listed: say that, and never start one for a request
                raise AdapterError(503, NOT_SERVING.format(program=ours[0]["name"]))
            raise AdapterError(404, f"This device doesn't have a model named '{name}'.")
        return sorted(hits, key=lambda h: h[0])[0][1], name

    async def _h_models(self, request: web.Request) -> web.Response:
        rep = await self.report()
        data = [{"id": m["id"], "object": "model", "owned_by": p["id"]} for p in rep["programs"] for m in p["models"]]
        data += [{"id": m["id"], "object": "model", "owned_by": "claude"} for m in (rep.get("claude") or {}).get("models") or []]
        data += [{"id": m["id"], "object": "model", "owned_by": "copilot"} for m in (rep.get("copilot") or {}).get("models") or []]
        return web.json_response({"object": "list", "data": data})

    async def _h_chat(self, request: web.Request) -> web.StreamResponse:
        try:
            body = await request.json()
        except ValueError:
            raise AdapterError(400, "Received something unexpected instead of a request.")
        if not isinstance(body, dict):
            raise AdapterError(400, "Received something unexpected instead of a request.")
        if self.copilot.handles(str(body.get("model") or "")):
            try:
                return await self.copilot.chat(request, body)
            except copilot_courier.CopilotError as e:
                return self._copilot_err(e)
        if self.claude.handles(str(body.get("model") or "")):
            try:
                return await self.claude.chat(request, body)
            except claude_courier.ClaudeError as e:
                return self._claude_err(e)
        adapter, model = await self.resolve(str(body.get("model") or ""))
        base, body = await adapter.chat_target(model, body)
        if adapter.id == "llamacpp":
            for k in CHAT_BLOCKED_PARAMS:
                body.pop(k, None)
        adapter.inflight += 1
        self.signal_change()
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=600))
        try:
            async with session.post(base + "/chat/completions", json=body) as up:
                ctype = up.headers.get("Content-Type", "application/json")
                if not body.get("stream") or up.status != 200:
                    data = await up.read()
                    return web.Response(status=up.status, body=data, content_type=ctype.split(";")[0], charset="utf-8")
                resp = web.StreamResponse(status=200, headers={"Content-Type": ctype, "Cache-Control": "no-cache"})
                await resp.prepare(request)
                async for chunk in up.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return _err(502, f"{adapter.name} failed to respond: {e}")
        finally:
            await session.close()
            adapter.inflight -= 1
            self.signal_change()

    # ---- the older "model server" routes -----------------------------------------------------------------------
    async def _h_share_info(self, request: web.Request) -> web.Response:
        rep = await self.report()
        models = []
        for p in rep["programs"]:
            for m in p["models"]:
                on_demand = p["id"] in ("ollama", "lmstudio")           # these load a model when asked
                models.append({"id": m["id"], "name": m["name"], "vision": m["vision"], "program": p["id"],
                               "state": "running" if (m["loaded"] or (on_demand and p["running"])) else "stopped"})
        g = (rep["gpus"] or [None])[0]
        gpu = {"name": g["name"], "vendor": g["vendor"], "memoryGb": round(g["memory_total_mb"] / 1024, 1) if g["memory_total_mb"] else None} if g else None
        return web.json_response({"product": "multiproxy", "kind": "courier", "name": other_pcs.short_hostname(),
                                  "instance": self.settings.id, "version": COURIER_VERSION, "gpu": gpu, "models": models})

    # ---- discovery -----------------------------------------------------------------------------------------------
    def is_paired_host(self, ip: str) -> bool:
        """True when `ip` is the main device this Courier is paired with and talking to (its live connection, or a request it made a moment ago). Nothing else
        counts: a host that merely asks "who is here?" is not paired."""
        if not ip:
            return False
        if any(c.ip == ip for c in self.clients):
            return True
        return bool(self.last_seen_ip == ip and self.last_seen and time.time() - self.last_seen < CONNECTED_WINDOW_SECONDS)

    def _discovery_answer(self, host: str = "") -> dict:
        """The "I'm here" reply. To anyone who is not paired: this device's name, port and permanent id, nothing more (security review S4). Versions, programs, model
        names and the graphics card are for the paired main device only."""
        short = {"magic": other_pcs.DISCOVERY_ANSWER, "name": other_pcs.short_hostname(), "instance": self.settings.id, "port": self.settings.port}
        if not self.is_paired_host(host):
            return short
        rep = self._report[1] if self._report else None
        progs = [{"id": p["id"], "name": p["name"], "version": p["version"], "running": p["running"]}
                 for p in (rep["programs"] if rep else []) if p["installed"]]
        g = (rep["gpus"] or [None])[0] if rep else None
        loaded = [m["name"] for p in (rep["programs"] if rep else []) for m in p["models"] if m["loaded"]]
        return {"magic": other_pcs.DISCOVERY_ANSWER, "name": other_pcs.short_hostname(), "instance": self.settings.id,
                "port": self.settings.port, "courier": COURIER_VERSION, "programs": progs, "models": loaded,
                "gpu": ({"name": g["name"], "vendor": g["vendor"],
                         "memoryGb": round(g["memory_total_mb"] / 1024, 1) if g["memory_total_mb"] else None} if g else None)}

    # ---- on / off -------------------------------------------------------------------------------------------------
    def app(self) -> web.Application:
        app = web.Application(client_max_size=128 * 1024 * 1024)
        g = self.guarded
        app.router.add_get("/courier/v1/report", g(self._h_report))
        app.router.add_get("/courier/v1/memory", g(self._h_memory))
        app.router.add_get("/courier/v1/log", g(self._h_log))
        app.router.add_post("/courier/v1/transfers", g(self._h_transfer_begin))
        app.router.add_put("/courier/v1/transfers/{tid}", g(self._h_transfer_chunk))
        app.router.add_post("/courier/v1/transfers/{tid}/finish", g(self._h_transfer_finish))
        app.router.add_delete("/courier/v1/transfers/{tid}", g(self._h_transfer_cancel))
        app.router.add_post("/courier/v1/update/begin", g(self._h_update_begin))
        app.router.add_post("/courier/v1/update/stage", g(self._h_update_stage))
        app.router.add_post("/courier/v1/update/apply", g(self._h_update_apply))
        app.router.add_post("/courier/v1/update/cancel", g(self._h_update_cancel))
        app.router.add_get("/courier/v1/ws", self._h_ws)                      # guards itself (before the upgrade)
        app.router.add_post("/courier/v1/programs/{program}/{action:start|restart|stop}", g(self._h_action))
        app.router.add_post("/courier/v1/programs/{program}/uninstall", g(self._h_uninstall_program))
        app.router.add_get("/courier/v1/programs/{program}/engine", g(self._h_engine_get))
        app.router.add_post("/courier/v1/programs/{program}/engine", g(self._h_engine_post))
        app.router.add_post("/courier/v1/programs/{program}/models/{action:load|unload|delete|download}", g(self._h_action))
        app.router.add_post("/courier/v1/programs/{program}/downloads/{action:cancel}", g(self._h_action))
        app.router.add_post("/courier/v1/settings", g(self._h_settings))
        app.router.add_post("/courier/v1/models/find", g(self._h_models_find))
        app.router.add_get("/courier/v1/models/scan", g(self._h_scan_status))
        app.router.add_post("/courier/v1/models/scan/{action:start|stop}", g(self._h_scan_action))
        app.router.add_post("/courier/v1/models/add", g(self._h_models_add))
        app.router.add_get("/courier/v1/drives", g(self._h_drives))
        app.router.add_post("/courier/v1/models-folder", g(self._h_models_folder))
        app.router.add_get("/courier/v1/voice/status", g(self._h_voice_status))
        app.router.add_get("/courier/v1/voice/fit", g(self._h_voice_fit))
        app.router.add_get("/courier/v1/voice/log", g(self._h_voice_log))
        app.router.add_get("/courier/v1/voice/output", g(self._h_voice_output))
        app.router.add_post("/courier/v1/voice/license", g(self._h_voice_license))
        app.router.add_post("/courier/v1/voice/install", g(self._h_voice_install))
        app.router.add_post("/courier/v1/voice/{action:cancel|start|stop|restart|uninstall|select}", g(self._h_voice_action))
        app.router.add_post("/courier/v1/voice/speak", g(self._h_voice_speak))
        app.router.add_get("/courier/v1/voice/clips", g(self._h_voice_clips))
        app.router.add_get("/courier/v1/voice/clips/audio", g(self._h_voice_clips_audio))
        app.router.add_post("/courier/v1/voice/clips/add", g(self._h_voice_clips_add))
        app.router.add_post("/courier/v1/voice/clips/words", g(self._h_voice_clips_words))
        app.router.add_post("/courier/v1/voice/clips/remove", g(self._h_voice_clips_remove))
        app.router.add_get("/courier/v1/claude/status", g(self._h_claude_status))
        app.router.add_post("/courier/v1/claude/signin/{step:start|code|cancel}", g(self._h_claude_signin))
        app.router.add_post("/courier/v1/claude/{action:install|update|cancel|signin|signout|test|uninstall}", g(self._h_claude_action))
        app.router.add_get("/courier/v1/copilot/status", g(self._h_copilot_status))
        app.router.add_post("/courier/v1/copilot/{action:install|update|cancel|signin|cancelsignin|signout|test|uninstall}", g(self._h_copilot_action))
        app.router.add_get("/courier/v1/decisions/status", g(self._h_decisions_status))
        app.router.add_get("/courier/v1/decisions/fit", g(self._h_decisions_fit))
        app.router.add_get("/courier/v1/decisions/log", g(self._h_decisions_log))
        app.router.add_post("/courier/v1/decisions/install", g(self._h_decisions_install))
        app.router.add_post("/courier/v1/decisions/settings", g(self._h_decisions_settings))
        app.router.add_post("/courier/v1/decisions/{action:cancel|start|stop|restart|uninstall}", g(self._h_decisions_action))
        app.router.add_post("/courier/v1/decisions/answer", g(self._h_decisions_answer))
        app.router.add_get("/courier/v1/engine-settings", g(self._h_engine_settings_get))
        app.router.add_post("/courier/v1/engine-settings", g(self._h_engine_settings_post))
        app.router.add_get("/courier/v1/gpu", g(self._h_gpu_get))
        app.router.add_post("/courier/v1/gpu", g(self._h_gpu_set))
        app.router.add_post("/courier/v1/pairing/new", g(self._h_new_code))
        app.router.add_post("/courier/v1/pairing/unpair", g(self._h_unpair))
        app.router.add_post("/v1/chat/completions", g(self._h_chat))
        app.router.add_get("/v1/models", g(self._h_models))
        # what an already-paired main device (paired with a "model server") speaks
        app.router.add_get("/share/info", g(self._h_share_info))
        app.router.add_get("/share/memory", g(self._h_memory))
        app.router.add_post("/share/v1/chat/completions", g(self._h_chat))
        return app

    async def start(self) -> None:
        self.error = ""
        self.apply_settings()
        if not self.local.folder:
            self.local.set_folder(str(self.settings.path.parent / "models"))
        self.local.cleanup_leftovers()
        ips = self.bind_ips
        if ips is None:
            ips = await asyncio.to_thread(other_pcs.local_private_ips)
            ips = ["127.0.0.1"] + ips                  # this device itself too: the status command and the ready window ask it
        if not ips:
            self.error = "Couldn't find a local network address. Is this device connected to your Wi-Fi or router?"
            raise RuntimeError(self.error)
        runner = web.AppRunner(self.app(), access_log=None)
        await runner.setup()
        bound, problems = [], []
        self._sites = {}
        for ip in ips:
            try:
                site = web.TCPSite(runner, ip, self.settings.port)
                await site.start()
                self._sites[ip] = site
                bound.append(ip)
            except OSError as e:
                problems.append(f"{ip}:{self.settings.port} ({e.strerror or e})")
        if not bound:
            await runner.cleanup()
            self.error = (f"Courier couldn't start ({'; '.join(problems)}). "
                          "Is another copy of Courier already running on this device?")
            raise RuntimeError(self.error)
        self._runner = runner
        loopback_only = bool(self.bind_ips) and all(ip in ("127.0.0.1", "::1", "localhost") for ip in self.bind_ips)
        self.addresses = [f"{ip}:{self.settings.port}" for ip in bound]
        await self.report(fresh=True)
        try:
            loop = asyncio.get_running_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1" if loopback_only else "", self.discovery_port))        # (told to listen on this device only: nothing is opened to the network, so no firewall question)
            self._transport, _ = await loop.create_datagram_endpoint(lambda: other_pcs._DiscoveryProtocol(self._discovery_answer), sock=sock)
        except OSError as e:
            self.log.warning(f"Your main device can't find this device automatically ({e.strerror or e}). You can add it manually using its network address.")
        self._ticker = asyncio.create_task(self._tick())
        self.log.info(OPENING_LINE)
        self.log.info(self.status_line())

    async def refresh_listeners(self) -> None:
        """The VPN list changed: start listening on an address that now qualifies, stop listening on one that no longer does. (Skipped when the addresses were fixed by the caller.)"""
        if self.bind_ips is not None or not getattr(self, "_sites", None) or self._runner is None:
            return
        want = ["127.0.0.1"] + await asyncio.to_thread(other_pcs.local_private_ips)
        for ip in list(self._sites):
            if ip not in want:
                try:
                    await self._sites.pop(ip).stop()
                except Exception:
                    pass
        for ip in want:
            if ip not in self._sites:
                try:
                    site = web.TCPSite(self._runner, ip, self.settings.port)
                    await site.start()
                    self._sites[ip] = site
                except OSError:
                    pass
        self.addresses = [f"{ip}:{self.settings.port}" for ip in self._sites]

    def status_line(self) -> str:
        rep = self._report[1] if self._report else None
        progs = []
        for p in (rep["programs"] if rep else []):
            if p["id"] == "llamacpp":
                progs.append("Model engine (llama.cpp) (" + ("already installed" if (p.get("engine") or {}).get("kind") == "existing" else "from MultiProxy") + ")")
            elif p["installed"]:
                progs.append(f"{p['name']}{' ' + p['version'] if p['version'] else ''} ({'running' if p['running'] else 'not running'})")
        who = "waiting for your main device"
        return (f"Ready for your main device (pairing code {self.settings.code}). "
                f"Found: {', '.join(progs) or 'none'}. Status: {who}.")

    async def stop(self) -> None:
        if self._ticker:
            self._ticker.cancel()
            await asyncio.gather(self._ticker, return_exceptions=True)
            self._ticker = None
        for c in list(self.clients):
            await c.ws.close()
        if self._transport:
            self._transport.close()
            self._transport = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        for a in self.adapters:
            await a.shutdown()
        await self.claude.shutdown()                # a Claude sign-in still waiting is stopped, and the program with it
        await self.monitor.stop()
        await asyncio.to_thread(self.scanner.stop)
        await self.voice.close()
        await self.decisions.close()
        self.keep_awake.set(False)
        self.log.info("Stopped.")


# --------------------------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------------------------
def default_config_dir() -> Path:
    return Path(__file__).resolve().parent


NOT_SERVING = "{program} isn't answering right now."
LOG_DEFAULT_LINES = 200
LOG_MAX_LINES = 500
LOG_MAX_LINE_CHARS = 400
LOG_MAX_BYTES = 256 * 1024                      # only this much of the end of the file is ever read

_PAIRING_LIKE = re.compile(r"\b[A-Z0-9]{3}-[A-Z0-9]{3}\b")
_SECRETS = [
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 •••"),
    (re.compile(r"(?i)\b(api[_ -]?key|token|password|secret|authorization|code)(\"?\s*[:=]\s*)\"?[^\s\",;}]+\"?"), r"\1\2•••"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "•••"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "•••"),         # a GitHub token (Copilot, copilot_courier.py)
]
_CHAT_TEXT = re.compile(r'"(messages|content|prompt)"\s*:')


def mask_log_line(line: str, code: str = "") -> str:
    """One log line made safe to show on another device: the pairing code (as set, and anything shaped like one), keys and tokens,
    and anything that looks like chat text are replaced; a long line is cut."""
    if _CHAT_TEXT.search(line):
        return line[:line.find("{")].rstrip() + " (chat text hidden)" if "{" in line else "(chat text hidden)"
    if code:
        line = re.sub(re.escape(code), "•••-•••", line, flags=re.I)
    line = _PAIRING_LIKE.sub("•••-•••", line)
    for pat, rep in _SECRETS:
        line = pat.sub(rep, line)
    return line if len(line) <= LOG_MAX_LINE_CHARS else line[:LOG_MAX_LINE_CHARS] + "…"


def read_log_tail(path: Path, n: int, code: str = "") -> list:
    """The last `n` lines of the Courier's own log file, masked. A missing file is simply no lines."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LOG_MAX_BYTES))
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    if size > LOG_MAX_BYTES and lines:
        lines = lines[1:]                           # the first one is cut in half
    return [mask_log_line(l, code) for l in lines[-n:]]


class _KeepCodesOutOfTheFile(logging.Formatter):
    """The window and the console show the pairing code to the person sitting at this device; the log FILE (kept, rotated, pasted into bug reports) never does
    (security review 2026-10-06). A formatter, so the console's own copy of the line is left alone."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        line = _PAIRING_LIKE.sub("•••-•••", line)
        for pat, rep in _SECRETS:
            line = pat.sub(rep, line)
        return line


def _stdout_is_a_terminal() -> bool:
    try:
        return bool(sys.stdout) and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def setup_logging(config_dir: Path, console: bool = True) -> logging.Logger:
    log = logging.getLogger("courier")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(config_dir / "courier.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(_KeepCodesOutOfTheFile("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stdout)
        # A person at a terminal sees the pairing code; a service's output (the journal, a redirected file) is a log too, and never holds it.
        ch.setFormatter(logging.Formatter("%(message)s") if _stdout_is_a_terminal() else _KeepCodesOutOfTheFile("%(message)s"))
        log.addHandler(ch)
    return log


LAUNCHER_NAME = "MultiProxy Courier.exe"                     # the small start program the installer puts next to courier.py (installer/launcher/): its own name, publisher and icon


def startup_command(config_dir: Optional[Path] = None) -> str:
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"           # no console window at login
    extra = ""
    if config_dir is not None and Path(config_dir).resolve() != default_config_dir().resolve():
        extra = f' --config-dir "{Path(config_dir).resolve()}"'     # a settings folder the player chose (the installer) must be followed at sign-in too
    here = Path(__file__).resolve().parent
    launcher = here / LAUNCHER_NAME
    if sys.platform == "win32" and launcher.is_file() and (here / "python" / "pythonw.exe").is_file():
        return f'"{launcher}" run{extra}'                       # Windows' Startup list names the item from the program file: this one says "MultiProxy Courier", not "Python"
    return f'"{exe}" "{Path(__file__).resolve()}" run{extra}'


def local_call(settings: CourierSettings, method: str, path: str, timeout: float = 5, body: Optional[dict] = None) -> Optional[dict]:
    """Ask the Courier running on THIS device (the status command and the ready window do)."""
    import urllib.error
    import urllib.request
    headers = {"Authorization": f"Bearer {settings.code}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{settings.port}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def pairing_report_lines(state: dict) -> list:
    """What `status` says about the pairing (the same truth the ready window shows): who is connected, who it is waiting for, or the code to type.
    The code is only shown while no main device is paired, or one is being refused for an old code (when a person needs it)."""
    import courier_ui
    if state["kind"] == "connected":
        lines = [f"Connected to {state['name']}"]
    elif state["kind"] == "paired":
        lines = [f"Paired with {state['name']}, waiting for it to connect"]
    else:
        lines = [f"Pairing code: {state['code']}"]
    return lines + courier_ui.trouble_lines(state["trouble"])


def status_text(settings: CourierSettings) -> str:
    import courier_ui
    rep = local_call(settings, "GET", "/courier/v1/report")
    state = courier_ui.pairing_state(rep, settings.code, settings.paired_name())
    if not rep:
        return "\n".join(["MultiProxy Courier is not running on this device.",
                          f"Paired with {state['name']}." if state["kind"] != "not_paired" else f"Pairing code: {settings.code}"])
    build = rep["courier"].get("build")
    lines = [f"MultiProxy Courier {rep['courier']['version']}{', build ' + str(build) if build is not None else ''} is running."] + pairing_report_lines(state)
    for p in rep["programs"]:
        if p["id"] == "llamacpp" and not p["installed"]:
            lines.append("  Model engine (llama.cpp): from MultiProxy (downloads the first time you load a model)")
        else:
            lines.append(f"  {p['name']}{' ' + p['version'] if p['version'] else ''}: {p['state']}")
    return "\n".join(lines)


VPN_NETWORKS_USAGE = "Usage: multiproxy-courier vpn-networks list | add 10.8.0.0/16 | remove 10.8.0.0/16"
NOT_RUNNING_NOT_SAVED = "Couldn't save because MultiProxy Courier isn't running. Start it and try again."
NOT_SAVED = "Couldn't save. Make sure MultiProxy Courier is running and try again."


def apply_vpn_networks(settings: CourierSettings, nets: list, allow_file: bool = False) -> str:
    """Give the running Courier this list (it saves it and starts or stops listening at once). "" when done, else the plain line to show.
    A Courier that isn't running can only be told by writing its settings file, which the command may do (`allow_file`) and the ready window never does."""
    if local_call(settings, "GET", "/courier/v1/report") is None:
        if not allow_file:
            return NOT_RUNNING_NOT_SAVED
        settings.set(private_networks=nets)             # nothing else holds this file: the Courier reads it when it starts
        return ""
    return "" if local_call(settings, "POST", "/courier/v1/settings", body={"private_networks": nets}) is not None else NOT_SAVED


def vpn_networks_command(settings: CourierSettings, action: str = "list", value: str = "") -> tuple:
    """`vpn-networks list | add <network> | remove <network>`: the same list the ready window and the main device's card edit, with the same checks
    and the same plain refusals. Returns (what to print, exit code)."""
    current = [str(n) for n in (settings.data.get("private_networks") or [])]
    new = current
    try:
        if action == "add" and value.strip():
            new = other_pcs.clean_private_networks(current + [value])
        elif action == "remove" and value.strip():
            gone = str(other_pcs.parse_private_network(value))
            if gone not in current:
                return "That network isn't in the list.", 1
            new = [n for n in current if n != gone]
        elif action != "list":
            return VPN_NETWORKS_USAGE, 2
    except other_pcs.OtherPcsError as e:
        return e.message, 1
    if new != current:
        problem = apply_vpn_networks(settings, new, allow_file=True)
        if problem:
            return problem, 1
    return "\n".join(new) if new else "No other VPN or Tailscale networks are listed.", 0


def code_text(settings: CourierSettings, platform: str = sys.platform) -> str:
    """What `code` prints: the pairing code while no main device is paired; otherwise who this device is paired with and how to make a new code (a used code is no use to anyone)."""
    name = settings.paired_name()
    if name is None:
        return settings.code
    how = "choose Make new pairing code in the tray menu" if platform == "win32" else "run multiproxy-courier new-code"
    return f"This device is already paired with {name}. To make a new pairing code, {how}."


PAIR_FILE_NAME = "courier-pair.json"             # what the main PC's Add a PC page offers next to the normal installer (add_a_pc.py)
PAIR_FILE_MAX_AGE = 3600.0                       # an older file is deleted unused (its token lasted 15 minutes)
PAIR_FILE_MAX_BYTES = 4096
PAIR_ANSWER_WAIT = 135.0                         # seconds the Courier waits for the main PC's answer (the main PC waits 120 s for the click)


def pair_file_folders() -> list:
    """Where the file may be: the person's Downloads folder (where a browser puts it), and anything listed in MULTIPROXY_PAIR_FILE_DIRS (tests and unusual setups). The
    Courier's installer is not changed, so it cannot be told which folder it was run from; Downloads is the one place that covers the normal case."""
    extra = [Path(x) for x in os.environ.get("MULTIPROXY_PAIR_FILE_DIRS", "").split(os.pathsep) if x.strip()]
    return extra or [Path.home() / "Downloads"]


def on_my_network(main_ip: str, own_ips: list) -> bool:
    """Whether the main PC's address in a pairing file is on the same local network as one of this PC's own addresses: the same /24 for a home address (the size a home router
    gives out; a bigger home network falls back to the pairing code), the same 100.64.0.0/10 for a Tailscale address (both must be Tailscale). Anything else is not trusted."""
    try:
        main = ipaddress.ip_address(main_ip)
        if not isinstance(main, ipaddress.IPv4Address):
            return False
        cgnat = ipaddress.ip_network("100.64.0.0/10")
        for own in own_ips or []:
            mine = ipaddress.ip_address(str(own))
            if not isinstance(mine, ipaddress.IPv4Address):
                continue
            if main in cgnat or mine in cgnat:
                if main in cgnat and mine in cgnat:
                    return True
                continue
            if ipaddress.ip_network(f"{mine}/24", strict=False) == ipaddress.ip_network(f"{main}/24", strict=False):
                return True
    except ValueError:
        return False
    return False


def claim_pair_file(settings: "CourierSettings", log: logging.Logger, folders: Optional[list] = None, now: Optional[float] = None, post=None, own_ips: Optional[list] = None) -> str:
    """If a `courier-pair.json` is waiting, use it ONCE and delete it, whatever happens: tell the main PC (a private home-network address only) this Courier's port, pairing
    code and name, with the one-time token, so the main PC adds it without anyone typing the code. Returns "" (no file), "paired", "ignored" or "failed".
    The file is read as data: a small JSON object, version 1, `main` = http://<private address>:<port>, `token`. Nothing in it names a path or anything to run."""
    now = time.time() if now is None else now
    found = None
    for folder in (folders if folders is not None else pair_file_folders()):
        f = Path(folder) / PAIR_FILE_NAME
        try:
            if f.is_file():
                found = f
                break
        except OSError:
            continue
    if found is None:
        return ""
    try:
        raw = found.read_bytes()[:PAIR_FILE_MAX_BYTES + 1]
        age = now - found.stat().st_mtime
    except OSError:
        return ""
    try:
        found.unlink()                                          # used once: gone before anything else happens
    except OSError:
        log.warning("Found a pairing file but couldn't delete it, so it was not used.")
        return "failed"
    try:
        if len(raw) > PAIR_FILE_MAX_BYTES or age > PAIR_FILE_MAX_AGE:
            raise ValueError("too big or too old")
        data = json.loads(raw.decode("utf-8"))
        token, main = str(data["token"]), str(data["main"])
        if data.get("v") != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
            raise ValueError("not a pairing file")
        m = re.fullmatch(r"http://(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", main)
        if not m or other_pcs.ip_class(m.group(1)) != "private" or not 1 <= int(m.group(2)) <= 65535:
            raise ValueError("not a home-network address")
        if not on_my_network(m.group(1), other_pcs.local_private_ips() if own_ips is None else own_ips):
            log.info("Found a pairing file for a PC that is not on this PC's own network. It was deleted and not used.")
            return "ignored"
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        log.info("Found a pairing file that couldn't be used (it was old, damaged or not for a home network). It was deleted.")
        return "ignored"
    if settings.paired_name():
        log.info("Found a pairing file, but this device is already paired. It was deleted.")
        return "ignored"
    body = json.dumps({"token": token, "port": int(settings.data.get("port") or COURIER_PORT_DEFAULT), "code": settings.data["code"], "name": other_pcs.short_hostname()}).encode()
    send = post or _post_pairing
    for attempt in range(3):
        try:
            got = send(f"{main}/add-a-pc/register", body)
            if got == "declined":                                   # the person on the main PC said No (or did not answer): ending here, not asking again
                log.info("The main PC didn't add this PC. Use the pairing code instead.")
                return "declined"
            if got:
                log.info("Paired with the main PC using its Add a PC address.")
                return "paired"
        except Exception as e:
            log.info(f"Couldn't reach the main PC to pair ({e.__class__.__name__}).")
        time.sleep(3 if attempt < 2 else 0)
    log.info("The main PC didn't take the pairing. Use the pairing code instead.")
    return "failed"


def _post_pairing(url: str, body: bytes) -> bool:
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "X-MultiProxy": "1"})
    try:
        # the main PC waits up to 2 minutes for one click before it answers (add_a_pc.JOIN_WAIT_SECONDS): this waits a little longer than that
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=PAIR_ANSWER_WAIT) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return "declined"
        raise


async def _serve(courier: Courier, stop: asyncio.Event) -> None:
    courier._pair_task = asyncio.get_running_loop().create_task(_claim_pair_file_later(courier))
    courier._stop_event = stop
    await courier.start()
    await courier.update.after_start()
    await stop.wait()
    await courier.stop()


async def _claim_pair_file_later(courier: Courier) -> None:
    """After the door is open (the main PC connects straight back), look for a pairing file once. Never lets a problem here stop the Courier."""
    try:
        await asyncio.sleep(2)
        await asyncio.to_thread(claim_pair_file, courier.settings, courier.log)
    except Exception as e:
        courier.log.info(f"Looking for a pairing file failed ({e.__class__.__name__}).")


def ready_window_wanted(shown_before: bool, show_flag: bool, no_flag: bool, platform: str = sys.platform) -> bool:
    """Does this start open the "This device is Ready" window? Only on Windows, never when `--no-ready-window` says no, and then: always when the installer
    (or the setup script) asked for it with `--show-ready-window` (it must not depend on what an earlier Courier on this device left in the settings: a device
    that is set up again, or updated, is told the window is open), otherwise only the first time ever. Starting with Windows passes neither flag."""
    if platform != "win32" or no_flag:
        return False
    return bool(show_flag or not shown_before)


def log_ready_window_problem(config_dir: Path, message: str) -> None:
    """The ready window is its own process, so a failure to show it is written straight to the Courier's log (it can't be told to the screen)."""
    try:
        with open(Path(config_dir) / "courier.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} The ready window couldn't be shown: {message}\n")
    except OSError:
        pass


def launch_ready_window(config_dir: Path, log: Optional[logging.Logger] = None, updated_from: str = "") -> bool:
    """Start the ready window in its own process (so it can never hold the Courier up). False, with the reason in the log, when it couldn't be started.
    `updated_from` is the version this Courier was updated from (the window says so once; the window never writes the settings, the Courier does)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "ready-window", "--config-dir", str(config_dir)]
    if updated_from:
        cmd += ["--updated-from", updated_from]
    try:
        hidden_process.popen(cmd,
                             creationflags=0x08000000 if os.name == "nt" else 0, stdin=subprocess.DEVNULL)
        if log:
            log.info("Showing the ready window.")
        return True
    except OSError as e:
        if log:
            log.warning(f"The ready window couldn't be shown: {e}")
        else:
            log_ready_window_problem(config_dir, str(e))
        return False


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="courier", description="MultiProxy Courier")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "status", "code", "paired", "new-code", "ready-window", "confirm-new-code", "confirm-unpair", "vpn-networks"])
    ap.add_argument("words", nargs="*", help="vpn-networks: list, add <network> or remove <network>")
    ap.add_argument("--config-dir", default=str(default_config_dir()))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--discovery-port", type=int, default=other_pcs.DISCOVERY_PORT)
    ap.add_argument("--bind", action="append", default=None, help="listen on this address (default: this device's home-network addresses)")
    ap.add_argument("--no-tray", action="store_true")
    ap.add_argument("--no-ready-window", action="store_true")
    ap.add_argument("--show-code", action="store_true", help="ready-window: show the pairing code even when this device is already paired (the tray's Show pairing code)")
    ap.add_argument("--updated-from", default="", help="ready-window: the version the Courier was updated from")
    ap.add_argument("--show-ready-window", action="store_true", help="open the ready window now even if it was shown before (the installer and the setup script ask for this)")
    ap.add_argument("--firewall", choices=["added", "declined", "unknown"], default=None, help="what setup did about the Windows firewall")
    ap.add_argument("--start-with-windows", choices=["on", "off"], default=None)
    args = ap.parse_args(argv)

    config_dir = Path(args.config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = CourierSettings(config_dir / "courier.json")
    if args.port:
        settings.set(port=args.port)
    if args.firewall:
        settings.set(firewall=args.firewall)
    if args.start_with_windows:
        settings.set(start_with_windows=args.start_with_windows == "on")

    if args.command == "code":
        print(code_text(settings))
        return 0
    if args.command == "status":
        print(status_text(settings))
        return 0
    if args.command == "vpn-networks":
        text, rc = vpn_networks_command(settings, (args.words[0] if args.words else "list"), " ".join(args.words[1:]))
        print(text)
        return rc
    if args.command == "paired":                    # the setup scripts' last lines ask: prints the main device's name, or nothing when not paired
        print(settings.paired_name() or "")
        return 0
    if args.command == "new-code":
        res = local_call(settings, "POST", "/courier/v1/pairing/new")
        if res:
            print(res["code"])
        else:
            settings.set(code=other_pcs.new_pairing_code(), paired=False, paired_name="", code_made_at=time.time())
            print(settings.code)
        return 0
    if args.command == "confirm-new-code":
        import courier_ui
        return courier_ui.show_confirm_new_code(settings)
    if args.command == "confirm-unpair":
        import courier_ui
        return courier_ui.show_confirm_unpair(settings)
    if args.command == "ready-window":
        import courier_ui
        try:
            return courier_ui.show_ready_window(settings, updated_from=args.updated_from, force_code=args.show_code)
        except Exception as e:                      # no desktop, no Tk: say so in the log, never a traceback nobody sees
            log_ready_window_problem(config_dir, f"{e.__class__.__name__}: {e}")
            return 1

    settings.note_start()
    log = setup_logging(config_dir, console=args.no_tray or sys.platform != "win32" or sys.stdout is not None and sys.stdout.isatty())
    bind = args.bind or [a for a in os.environ.get("MULTIPROXY_COURIER_BIND", "").replace(";", ",").split(",") if a.strip()] or None      # (the environment variable is for tests and diagnostics: it makes a Courier started by Start Courier.bat listen on this device only)
    log.info(f"MultiProxy Courier {COURIER_VERSION}" + (f" - {read_built()}" if read_built() else ""))
    courier = Courier(settings, log, bind_ips=bind, discovery_port=args.discovery_port,
                      startup=system.StartWithWindows(startup_command(config_dir)) if sys.platform == "win32" else system.StartWithWindows("", registry=None))
    tray = None
    if not args.no_tray and sys.platform == "win32":
        try:
            import courier_ui
            tray = courier_ui.Tray(courier, settings, config_dir)
        except ImportError as e:
            log.warning(f"Couldn't show the tray icon ({e}). Courier is still running in the background; type 'courier.py status' to check on it.")

    if ready_window_wanted(bool(settings.data.get("ready_shown")), args.show_ready_window, args.no_ready_window):
        # The first run ever, or whenever the installer / setup script asks: show the "This device is ready" window (its own process)
        if launch_ready_window(config_dir, log, str(settings.data.get("updated_from") or "")):
            settings.set(ready_shown=True)
    if settings.data.get("updated_from"):
        settings.set(updated_from="")                # said once, in the window that opens after this start (or not at all when none opens)

    if tray is None:
        loop = asyncio.new_event_loop()
        stop = asyncio.Event()
        try:
            import signal
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop.set)
                except (NotImplementedError, RuntimeError):
                    signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        except Exception:
            pass
        try:
            loop.run_until_complete(_serve(courier, stop))
        except RuntimeError as e:
            log.error(str(e))
            return 1
        except KeyboardInterrupt:
            loop.run_until_complete(courier.stop())
        return 0
    return tray.run_with(courier, _serve)


if __name__ == "__main__":
    sys.exit(main())
