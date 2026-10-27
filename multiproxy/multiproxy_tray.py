"""MultiProxy in the tray (2026-10-02): no terminal window, a status dot, start/stop and logs. Built to design/installer-and-tray-mockup.html, section 2.

The tray app OWNS the server: it starts `multiproxy.py` as a hidden child (no console window), stops it, restarts it, and quits it with the tray.
A second launch just opens the dashboard. The Courier's tray code (pystray + Pillow) is the model; every program started here goes through
`hidden_process`. Run it from a dev checkout with:   python multiproxy_tray.py       (or pythonw.exe for no console at all)

Layout of this file, top to bottom: the words (pure functions, tested without a screen), the pieces that talk to the server and the files, the
controller that ties them together (no screen either), the pystray icon, the two small log windows, and `main`.

Contract with the Skyrim mod: see `local_channel.py` (and TECHNICAL.md). One thing worth knowing here: "Start and stop with Skyrim" ON means the
WHOLE tray app quits when the game closes, but only if the mod started the tray app (`--from-skyrim`). If the player started it themselves before the
game, closing the game changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

import app_icon
import build_info
import hidden_process
import local_channel
import process_watch
import settings_files

PROGRAM_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = Path(settings_files.settings_dir(PROGRAM_DIR))     # settings and logs: beside the program unless the installer chose another folder
RESTART_EXIT_CODE = 75                      # the server's "start me again" (a changed port): the tray app starts it again at once
START_TIMEOUT_SECONDS = 90                  # models are warmed and every AI is checked at start-up; give it time before calling it stuck
POLL_SECONDS = 1.5
LOCK_PORT_DEFAULT = 18777                   # one tray app per device: whoever holds this local port is the one
LOG_LINES_SHOWN = 300
RELEASES_URL = "https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest"


# ---------------------------------------------------------------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------------------------------------------------------------
def icon_color(state: str, ready: int) -> str:
    """green = server answering and at least one AI ready; yellow = starting, or running with none ready; red = stopped or failed."""
    if state == "running":
        return "green" if ready > 0 else "yellow"
    if state == "starting":
        return "yellow"
    return "red"


def hover_text(state: str, ready: int = 0, total: int = 0, reason: str = "") -> str:
    if state == "running":
        return f"MultiProxy is running · {ready} of {total} models ready"
    if state == "starting":
        return "MultiProxy: starting…"
    if state == "failed":
        return f"MultiProxy couldn't start: {reason.rstrip('.')}. Click 'View recent activity' to see why."      # whole: it is also the first line of the menu (the icon's hover is cut at 127 characters where it is set)
    if state == "crashed":
        return "MultiProxy stopped. Click 'View recent activity' to see why, then try again."
    return "MultiProxy: stopped"


STATE_WORDS = {"running": "Running", "starting": "Starting", "stopped": "Stopped", "failed": "Stopped", "crashed": "Stopped"}


def menu_model(*, version: str, state: str, start_with_windows: bool, start_with_windows_supported: bool, start_stop_with_skyrim: bool,
               skyrim_tick_enabled: bool, update_available: bool, problem: str = "", build: str = "") -> list:
    """The tray menu, top to bottom. `key` says what a click does; `text` None is a separator. `problem` (a failed or crashed
    server) becomes the first line, so it is read in the menu and not only in the icon's hover text."""
    items = [{"key": "problem", "text": problem, "enabled": False}] if problem else []
    items += [
        {"key": "title", "text": "SkyrimNet MultiProxy", "enabled": False},        # the name once; the quiet line under it has the facts (version and build first, for a bug report)
        {"key": "version", "text": " · ".join([version] + ([f"built {build}"] if build else []) + [STATE_WORDS.get(state, 'Stopped')]), "enabled": False},
        {"key": "-", "text": None},
        {"key": "open_dashboard", "text": "Open dashboard", "default": True},
        {"key": "toggle_server", "text": "Stop MultiProxy" if state in ("running", "starting") else "Start MultiProxy"},
        {"key": "view_logs", "text": "View recent activity"},
        {"key": "live_log", "text": "Watch activity live"},
        {"key": "-", "text": None},
    ]
    if start_with_windows_supported:
        items.append({"key": "start_with_windows", "text": "Start with Windows", "checked": bool(start_with_windows)})
    items.append({"key": "start_stop_skyrim", "enabled": bool(skyrim_tick_enabled), "checked": bool(start_stop_with_skyrim),
                  "text": "Start and stop with Skyrim" if skyrim_tick_enabled else "Start and stop with Skyrim (install the mod from Settings)"})
    if update_available:
        items += [{"key": "-", "text": None}, {"key": "update", "text": "● New version available…"}]
    items += [{"key": "-", "text": None}, {"key": "quit", "text": "Quit MultiProxy"}]
    return items


def explain_start_failure(log_lines: list, exit_code: Optional[int], port: int) -> str:
    """One plain sentence for why the server didn't start, from the end of its own log."""
    text = "\n".join(log_lines[-40:])
    if re.search(r"Port \d+ is already in use|port \d+ is already in use", text) or "address already in use" in text.lower():
        if "another MultiProxy" in text:
            return "MultiProxy is already running. Check the icons by your clock."
        return "another program is using MultiProxy's address. Check Help to change it."
    m = re.search(r"No module named '([^']+)'", text)
    if m:
        return "a needed file is missing. Please reinstall MultiProxy."
    return "something went wrong while starting. Click 'View recent activity' to see why."


def should_quit_for_skyrim(*, auto_close: bool, owned_by_skyrim: bool, closed_seq: int, handled_seq: Optional[int]) -> bool:
    """Skyrim just closed (a "closed" the tray app hasn't acted on yet): quit the whole tray app only if "Start and stop with Skyrim" is on AND
    the mod is what started the tray app. The first status the tray app ever sees is only a baseline, never an event."""
    if handled_seq is None or closed_seq <= handled_seq:
        return False
    return bool(auto_close and owned_by_skyrim)


# ---------------------------------------------------------------------------------------------------------------------------------
# One tray app per device
# ---------------------------------------------------------------------------------------------------------------------------------
class SingleInstance:
    """Whoever holds a local port is "the" tray app. A second launch connects to it, says "open", and exits."""

    def __init__(self, port: Optional[int] = None):
        self.port = port or int(os.environ.get("MULTIPROXY_TRAY_LOCK_PORT", LOCK_PORT_DEFAULT))
        self._sock: Optional[socket.socket] = None

    def acquire(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", self.port))
            s.listen(4)
        except OSError:
            s.close()
            return False
        self._sock = s
        return True

    def serve(self, on_open: Callable[[], None], on_quit: Optional[Callable[[], None]] = None) -> threading.Thread:
        def loop():
            while self._sock:
                try:
                    conn, _ = self._sock.accept()
                except OSError:
                    return
                try:
                    conn.settimeout(2)
                    word = conn.recv(16).strip()
                    if word == b"open":
                        on_open()
                    elif word == b"quit" and on_quit:
                        on_quit()
                except OSError:
                    pass
                finally:
                    conn.close()
        t = threading.Thread(target=loop, name="tray-instance", daemon=True)
        t.start()
        return t

    def tell_existing(self, word: bytes) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as c:
                c.sendall(word + b"\n")
            return True
        except OSError:
            return False

    def tell_existing_to_open(self) -> bool:
        return self.tell_existing(b"open")

    def release(self) -> None:
        s, self._sock = self._sock, None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)       # Linux keeps a listening port open while another thread waits in accept(); this wakes it so close() really frees the port
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------------------------------------------
# The server, as a hidden child
# ---------------------------------------------------------------------------------------------------------------------------------
IS_WINDOWS = os.name == "nt"
LINUX_TERMINALS = [("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("xterm", ["-e"])]


def linux_terminal_command(cmd: list, which: Optional[Callable[[str], Optional[str]]] = None) -> Optional[list]:
    """`cmd` wrapped to run inside the first terminal program this desktop has (None when it has none)."""
    for exe, run_flag in LINUX_TERMINALS:
        path = (which or shutil.which)(exe)
        if path:
            return [path, *run_flag, *cmd]
    return None


def configured_port(program_dir: Path) -> int:
    """The port the server will use: MULTIPROXY_PORT, else the Settings page's choice (config.json), else 8000 (the same order the server uses)."""
    env = os.environ.get("MULTIPROXY_PORT", "").strip()
    if env.isdigit():
        return int(env)
    p = settings_files.read_json(str(Path(settings_files.settings_dir(program_dir)) / "config.json")).get("main_port")
    return int(p) if isinstance(p, int) and 1024 <= p <= 65535 else 8000


def server_python() -> str:
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"      # a server needs real standard streams; hidden_process gives it no window anyway
    return exe


class ServerHost:
    """Starts, stops and watches `multiproxy.py` as a hidden child. `state`: stopped | starting | running | failed | crashed."""

    def __init__(self, program_dir: Path, *, popen: Callable = hidden_process.popen, log_path: Optional[Path] = None, env_extra: Optional[dict] = None):
        self.dir = Path(program_dir)
        self._popen = popen
        self.log_path = Path(log_path) if log_path else Path(settings_files.settings_dir(self.dir)) / "multiproxy.log"
        self.env_extra = env_extra or {}
        self.proc = None
        self.state = "stopped"
        self.reason = ""
        self.port = configured_port(self.dir)
        self.started_at = 0.0
        self._stopping = False

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.port = configured_port(self.dir)
        env = {**os.environ, "MULTIPROXY_TRAY": "1", **self.env_extra}
        self._stopping = False
        self.reason = ""
        self.proc = self._popen([server_python(), str(self.dir / "multiproxy.py")], cwd=str(self.dir), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started_at = time.time()
        self.state = "starting"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def check_exit(self) -> None:
        """Notice a child that has gone: a restart request (exit code 75) starts it again; anything else is a failure, with the plain reason."""
        if self.proc is None or self.proc.poll() is None or self._stopping:
            return
        code = self.proc.returncode
        was_running = self.state == "running"
        self.proc = None
        if code == RESTART_EXIT_CODE:
            self.start()
            return
        if was_running:
            self.state, self.reason = "crashed", f"exit code {code}"
        else:
            self.state = "failed"
            self.reason = explain_start_failure(self.read_log(), code, self.port)

    def mark_running(self) -> None:
        if self.alive():
            self.state = "running"

    def mark_stuck_if_slow(self) -> None:
        if self.state == "starting" and self.alive() and time.time() - self.started_at > START_TIMEOUT_SECONDS:
            self.state, self.reason = "failed", "it took too long to start. Try starting it again."
            self.kill()

    def read_log(self, n: int = 60) -> list:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        except OSError:
            return []

    def kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                if os.name == "nt":
                    hidden_process.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(10)
            except Exception:
                pass

    def stop(self, graceful: Optional[Callable[[], None]] = None, wait: float = 10.0) -> None:
        """Stop the server: ask it to shut down cleanly first (it stops its local AI engines), then end it if it hasn't gone."""
        self._stopping = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            if graceful:
                try:
                    graceful()
                except Exception:
                    pass
                deadline = time.time() + wait
                while time.time() < deadline and proc.poll() is None:
                    time.sleep(0.1)
            if proc.poll() is None:
                self.kill()
        self.proc = None
        self.state, self.reason = "stopped", ""


# ---------------------------------------------------------------------------------------------------------------------------------
# The controller: everything the tray does, with no screen in it
# ---------------------------------------------------------------------------------------------------------------------------------
def read_version(program_dir: Path) -> str:
    try:
        m = re.search(r'^PROXY_VERSION\s*=\s*"([^"]+)"', (program_dir / "multiproxy.py").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


class Controller:
    def __init__(self, program_dir: Path = PROGRAM_DIR, *, from_skyrim: bool = False, host: Optional[ServerHost] = None, registry: Any = None,
                 open_url: Callable[[str], Any] = webbrowser.open, spawn_window: Optional[Callable[[list, int], None]] = None,
                 http: Optional[Callable] = None):
        self.dir = Path(program_dir)
        self.from_skyrim = from_skyrim
        self.host = host or ServerHost(self.dir)
        self.registry = registry
        self.open_url = open_url
        self._spawn_window = spawn_window or self._default_spawn
        self._http = http or self._urlopen
        self.version = read_version(self.dir)
        self.build = build_info.read_stamp(self.dir)
        self.quit_requested = threading.Event()
        self.snapshot: dict = {}
        self.ready, self.total = 0, 0
        self.handled_closed: Optional[int] = None
        self._look = process_watch.look                  # (a test hands in a stand-in)
        self._watch: Optional[dict] = None               # the Skyrim process being watched: {"pid", "seq", "started"}
        self._watch_noted: set = set()                   # what was already said once in the log (never every few seconds)
        self._boot: Optional[str] = None
        self.lock = threading.Lock()
        self.settings_dir = Path(settings_files.settings_dir(self.dir))
        self.ini_path = str(self.settings_dir / "multiproxy.ini")
        self.config_path = str(self.settings_dir / "config.json")
        self.log = lambda m: None

    # ---- pointer file for the Skyrim mod ----
    def write_pointer(self) -> dict:
        info = local_channel.write_info(port=self.host.port, command=settings_files.tray_start_command(str(self.dir)),
                                        program_folder=str(self.dir), settings_folder=str(self.settings_dir))
        return info

    # ---- talking to the server ----
    def _token(self) -> str:
        return str(local_channel.read_info().get("token") or "")

    def _urlopen(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 3) -> Optional[dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.host.port}{path}", method=method,
                                     data=json.dumps(body).encode("utf-8") if body is not None else None,
                                     headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json", "X-MultiProxy": "1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def poll(self) -> None:
        """One look at the server: has it exited, is it answering, how many AIs are ready, what has the Skyrim mod said."""
        with self.lock:
            h = self.host
            h.check_exit()
            if h.state in ("starting", "running") and h.alive():
                st = self._http("GET", "/local/tray-status")
                if st is not None:
                    h.mark_running()
                    self.snapshot = st
                    self.ready, self.total = int(st.get("readyCount") or 0), int(st.get("totalAIs") or 0)
                    if local_channel.read_info().get("port") != h.port:
                        self.write_pointer()
                    self._skyrim_events(st)
                else:
                    h.mark_stuck_if_slow()
            if h.state != "running":
                self.snapshot = {}

    def _skyrim_events(self, st: dict) -> None:
        self._closed_message(st)
        self._watch_skyrim(st)

    def _watch_skyrim(self, st: dict) -> None:
        """The backup for the mod's "closed": Skyrim killed hard never runs the mod's exit code. While Start and stop with Skyrim is on and the mod started this tray app, the
        process number from the mod's "started" is looked at on every poll; when that process is gone (or its number now belongs to something that started later), it is
        treated exactly like the mod's "closed". Whichever notices first quits (once); a looked-at-but-unreadable process does nothing but a log line, once."""
        sk = st.get("skyrim") or {}
        if not (st.get("autoClose") and self.from_skyrim) or not sk.get("running"):
            self._watch = None                           # off, or the mod already said closed: nothing to watch
            return
        pid, seq = sk.get("pid"), sk.get("startedSeq")
        if not isinstance(pid, int) or pid <= 0:
            if "nopid" not in self._watch_noted:
                self._watch_noted.add("nopid")
                self.log("The Skyrim mod didn't send Skyrim's game program number, so MultiProxy can't notice Skyrim ending by itself (it still quits on the mod's shutdown signal)")
            return
        state, started = self._look(pid)
        w = self._watch
        if w is None or w["pid"] != pid or w["seq"] != seq:          # a new "started" (a second Skyrim run) replaces what is watched; remember when this process started
            w = self._watch = {"pid": pid, "seq": seq, "started": started}
        if state == "unknown":
            if ("unknown", pid) not in self._watch_noted:
                self._watch_noted.add(("unknown", pid))
                self.log(f"Couldn't look at Skyrim's game program ({pid}), so MultiProxy won't notice by itself when it ends")
            return
        if process_watch.has_ended(state, started, w["started"]):
            self._watch = None
            self.log("Skyrim closed, so MultiProxy is quitting ('Start and stop with Skyrim' is on, and the mod started this). MultiProxy noticed the game process was gone before the mod sent a closing message.")
            self.quit_requested.set()

    def _closed_message(self, st: dict) -> None:
        if st.get("boot") != self._boot:               # a different server (it was restarted, or an old one was just taken over): count again from here
            self._boot = st.get("boot")
            self.handled_closed = None
        seq = int((st.get("skyrim") or {}).get("closedSeq") or 0)
        if self.handled_closed is None:
            self.handled_closed = seq                      # the first look is only a baseline
            return
        quit_now = should_quit_for_skyrim(auto_close=bool(st.get("autoClose")), owned_by_skyrim=self.from_skyrim, closed_seq=seq, handled_seq=self.handled_closed)
        self.handled_closed = max(self.handled_closed, seq)
        if quit_now:
            self.log("Skyrim closed: quitting (Start and stop with Skyrim is on, and the Skyrim mod started this)")
            self.quit_requested.set()

    # ---- what the tray shows ----
    @property
    def state(self) -> str:
        return self.host.state

    def hover(self) -> str:
        return hover_text(self.host.state, self.ready, self.total, self.host.reason)

    def color(self) -> str:
        return icon_color(self.host.state, self.ready)

    def _run_entry(self):
        return settings_files.run_entry(str(self.dir), self.registry)

    def refresh_run_entry(self) -> bool:
        """An install made before the start program existed has its Start-with-Windows entry on pythonw.exe (Windows lists that as "Python"): rewrite it, once, to the current
        command. Only this program's own entry, only while it is on, and only if it names this program's folder."""
        try:
            e = self._run_entry()
            return bool(e.supported and e.refresh(str(self.dir)))
        except Exception:
            return False

    def start_with_windows_on(self) -> bool:
        e = self._run_entry()
        return e.is_on() if e.supported else bool(settings_files.read_json(self.config_path).get("start_with_windows"))

    def start_stop_with_skyrim_on(self) -> bool:
        if "autoClose" in self.snapshot:
            return bool(self.snapshot["autoClose"])
        return settings_files.ini_get_bool(self.ini_path, "General", "AutoCloseWithSkyrim", False)

    def mod_detected(self) -> bool:
        return bool(self.snapshot.get("modDetected"))

    def update_available(self) -> bool:
        return (self.snapshot.get("update") or {}).get("state") == "available"

    def menu(self) -> list:
        return menu_model(version=self.snapshot.get("version") or self.version, state=self.host.state, start_with_windows=self.start_with_windows_on(),
                          start_with_windows_supported=self._run_entry().supported, start_stop_with_skyrim=self.start_stop_with_skyrim_on(),
                          skyrim_tick_enabled=self.mod_detected(), update_available=self.update_available(),
                          problem=self.hover() if self.host.state in ("failed", "crashed") else "", build=self.build)

    # ---- what the clicks do ----
    def dashboard_url(self) -> str:
        return f"http://127.0.0.1:{self.host.port}/"

    def do_open_dashboard(self) -> None:
        self.open_url(self.dashboard_url())

    def do_toggle_server(self) -> None:
        if self.host.state in ("running", "starting"):
            self.host.stop(graceful=self._ask_shutdown)
            self.ready = self.total = 0
        else:
            self.host.start()

    def _ask_shutdown(self) -> None:
        self._http("POST", "/local/shutdown", {})

    def do_start_with_windows(self) -> None:
        e = self._run_entry()
        if not e.supported:
            return
        on = not e.is_on()
        if e.set(on):
            cfg = settings_files.read_json(self.config_path)
            cfg["start_with_windows"] = on
            settings_files.write_json(self.config_path, cfg)

    def do_start_stop_skyrim(self) -> None:
        if not self.mod_detected():
            return
        on = not self.start_stop_with_skyrim_on()
        if self.host.state == "running" and self._http("POST", "/config/settings", {"startStopWithSkyrim": on}) is not None:
            return
        settings_files.ini_set(self.ini_path, "General", "AutoCloseWithSkyrim", "true" if on else "false")

    def do_update(self) -> None:
        self.open_url((self.snapshot.get("update") or {}).get("url") or RELEASES_URL)

    def do_view_logs(self) -> None:
        self._spawn_window([server_python(), str(self.dir / "multiproxy_tray.py"), "view-logs", "--log", str(self.host.log_path)], 0)

    def do_live_log(self) -> None:
        self._spawn_window([server_python(), str(self.dir / "multiproxy_tray.py"), "live-log", "--log", str(self.host.log_path)], 0x00000010)   # CREATE_NEW_CONSOLE

    def do_quit(self) -> None:
        self.quit_requested.set()

    def _default_spawn(self, cmd: list, flags: int) -> None:
        if IS_WINDOWS:
            hidden_process.popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL, cwd=str(self.dir))
            return
        if flags:                       # "its own console window" (the live log): on Linux that is a terminal program, whichever this desktop has
            cmd = linux_terminal_command(cmd) or []
            if not cmd:
                self.log("The live log window needs a terminal program (none was found). Use the `multiproxy logs` command instead.")
                return
        hidden_process.popen(cmd, stdin=subprocess.DEVNULL, cwd=str(self.dir))      # (creationflags only exists on Windows)

    def shutdown(self) -> None:
        self.host.stop(graceful=self._ask_shutdown)


# ---------------------------------------------------------------------------------------------------------------------------------
# The icon (pystray + Pillow, like the Courier's)
# ---------------------------------------------------------------------------------------------------------------------------------
DOT = {"green": (52, 199, 89, 255), "yellow": (255, 204, 0, 255), "red": (235, 77, 75, 255)}


def make_icon(color: str):
    from PIL import Image, ImageDraw
    img = app_icon.base_image("multiproxy")                    # the app icon, with the status dot over its corner; the drawn "M" if the file is missing
    if img is None:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((4, 6, 60, 58), radius=10, fill=(40, 40, 48, 255), outline=(217, 180, 74, 255), width=3)
        d.line((14, 44, 14, 20, 26, 36, 38, 20, 38, 44), fill=(217, 180, 74, 255), width=5, joint="curve")        # an "M"
    d = ImageDraw.Draw(img)
    d.ellipse((36, 36, 62, 62), fill=DOT.get(color, DOT["red"]), outline=(20, 20, 24, 255), width=3)
    return img


class Tray:
    def __init__(self, ctl: Controller):
        import pystray                       # ImportError here is caught by the caller: no tray, the server still runs
        from PIL import Image                # noqa: F401
        self.pystray = pystray
        self.ctl = ctl
        self.icon = None
        self._last = None

    def _menu(self):
        ps = self.pystray
        entries = []
        for it in self.ctl.menu():
            if it["text"] is None:
                entries.append(ps.Menu.SEPARATOR)
                continue
            entries.append(ps.MenuItem(it["text"], self._action(it["key"]), checked=self._checked(it) if "checked" in it else None,
                                       enabled=it.get("enabled", True), default=bool(it.get("default"))))
        return ps.Menu(*entries)

    def _action(self, key: str):
        def run(icon, item):                  # pystray accepts exactly 0, 1 or 2 parameters here
            self._click(key)
        return run

    @staticmethod
    def _checked(it: dict):
        state = bool(it["checked"])

        def checked(item):
            return state
        return checked

    def _click(self, key: str) -> None:
        threading.Thread(target=lambda: getattr(self.ctl, "do_" + key, lambda: None)(), daemon=True).start()

    def refresh(self) -> None:
        if not self.icon:
            return
        key = (self.ctl.color(), self.ctl.hover(), json.dumps(self.ctl.menu(), sort_keys=True))
        if key != self._last:
            self._last = key
            self.icon.icon = make_icon(self.ctl.color())
            self.icon.title = self.ctl.hover()[:127]          # Windows cuts a tooltip at 127 characters; the menu's first line has the whole text
            self.icon.menu = self._menu()
            self.icon.update_menu()

    def run(self) -> None:
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self._poll_loop, daemon=True).start()
        self.icon = self.pystray.Icon("SkyrimNet MultiProxy", make_icon("yellow"), "MultiProxy: starting…", self._menu())
        threading.Thread(target=self._watch_quit, daemon=True).start()
        self.icon.run(setup)

    def _poll_loop(self) -> None:
        while not self.ctl.quit_requested.is_set():
            try:
                self.ctl.poll()
                self.refresh()
            except Exception as e:                    # never let one bad look end the loop, but never hide it either
                self.ctl.log(f"Tray refresh problem: {e.__class__.__name__}: {e}")
            time.sleep(POLL_SECONDS)

    def _watch_quit(self) -> None:
        self.ctl.quit_requested.wait()
        if self.icon:
            self.icon.stop()


# ---------------------------------------------------------------------------------------------------------------------------------
# The two log windows (separate small programs, so closing one never touches the server)
# ---------------------------------------------------------------------------------------------------------------------------------
def masked_tail(path: Path, n: int = LOG_LINES_SHOWN) -> list:
    from courier import read_log_tail            # the Courier's own log masking: keys, tokens and anything shaped like chat text never show
    return read_log_tail(Path(path), n)


def show_log_window(log_path: Path) -> int:
    try:
        import tkinter as tk
    except ImportError:
        print("\n".join(masked_tail(log_path)))
        return 0
    bg, card, fg, soft, accent = "#17171b", "#222228", "#ececf0", "#a0a0ab", "#d9b44a"
    root = tk.Tk()
    root.title("MultiProxy log")
    app_icon.apply_to_window(root, "multiproxy")
    root.configure(bg=bg)
    root.geometry("760x460")
    frame = tk.Frame(root, bg=bg, padx=14, pady=12)
    frame.pack(fill="both", expand=True)
    box = tk.Text(frame, bg=card, fg=fg, insertbackground=fg, relief="flat", font=("Consolas", 9), wrap="word")
    box.pack(fill="both", expand=True)

    def refresh():
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", "\n".join(masked_tail(log_path)) or "Nothing has been logged yet.")
        box.configure(state="disabled")
        box.see("end")

    def copy():
        root.clipboard_clear()
        root.clipboard_append("\n".join(masked_tail(log_path)))

    def open_folder():
        try:
            if os.name == "nt":
                os.startfile(str(Path(log_path).parent))          # noqa: opens the log folder in Explorer
            else:
                hidden_process.popen(["xdg-open", str(Path(log_path).parent)])
        except OSError:
            pass
    row = tk.Frame(frame, bg=bg)
    row.pack(fill="x", pady=(10, 0))
    for text, cmd in (("Open folder", open_folder), ("Copy for support", copy), ("Refresh", refresh)):
        tk.Button(row, text=text, command=cmd, bg=card, fg=fg, relief="flat", padx=12, pady=4).pack(side="left", padx=(0, 8))
    tk.Button(row, text="Close", command=root.destroy, bg=card, fg=fg, relief="flat", padx=12, pady=4).pack(side="right")
    tk.Label(frame, text="Copy this text if you need to ask for help.", bg=bg, fg=soft, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))
    refresh()
    root.mainloop()
    return 0


def follow_log(log_path: Path, out=None, poll: float = 0.5, stop: Optional[Callable[[], bool]] = None) -> int:
    """A console window's job: show the end of the log, then every new line as it arrives (masked). Closing the window just ends this."""
    out = out or sys.stdout
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("SkyrimNet MultiProxy: live activity")
    except Exception:
        pass
    print("SkyrimNet MultiProxy: live activity (close this anytime; MultiProxy will keep running)", file=out, flush=True)
    for line in masked_tail(log_path, 30):
        print(line, file=out, flush=True)
    from courier import mask_log_line
    pos = None
    while not (stop and stop()):
        try:
            size = Path(log_path).stat().st_size
        except OSError:
            time.sleep(poll)
            continue
        if pos is None:
            pos = size
        if size < pos:
            pos = 0                                       # the log was rotated: start again from the top of the new file
        if size > pos:
            with open(log_path, "rb") as f:
                f.seek(pos)
                chunk = f.read(size - pos)
            pos = size
            for line in chunk.decode("utf-8", "replace").splitlines():
                print(mask_log_line(line), file=out, flush=True)
        time.sleep(poll)
    return 0


# ---------------------------------------------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------------------------------------------
def _tray_log(program_dir: Path):
    """The tray app's own small rotating log (`multiproxy-tray.log`): a program with no window can't show an error any other way."""
    import logging
    import logging.handlers
    log = logging.getLogger("multiproxy-tray")
    if not log.handlers:
        log.setLevel(logging.INFO)
        h = logging.handlers.RotatingFileHandler(Path(settings_files.settings_dir(program_dir)) / "multiproxy-tray.log", maxBytes=200_000, backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
    return log


def run_tray(args) -> int:
    log = _tray_log(PROGRAM_DIR)
    lock = SingleInstance()
    ctl = Controller(PROGRAM_DIR, from_skyrim=args.from_skyrim)
    ctl.log = log.info
    if not lock.acquire():
        lock.tell_existing_to_open()
        log.info("A tray app is already running: asked it to open the dashboard")
        return 0
    log.info(f"Tray app started (from Skyrim: {args.from_skyrim})")
    if ctl.refresh_run_entry():
        log.info("The Start-with-Windows entry now points at SkyrimNet MultiProxy's own startup launcher (it showed as Python in Windows' Startup list)")
    lock.serve(ctl.do_open_dashboard, ctl.do_quit)       # ("quit" is how the installer closes a running tray app before an upgrade or an uninstall)
    ctl.write_pointer()
    ctl.host.start()
    try:
        tray = Tray(ctl)
    except Exception as e:
        log.info(f"No tray icon here ({e.__class__.__name__}: {e}); the server runs without it")
        tray = None                                      # no tray here (a headless Linux, or a desktop without one): the server still runs
    try:
        if tray is not None:
            tray.run()
        else:
            while not ctl.quit_requested.is_set():
                ctl.poll()
                time.sleep(POLL_SECONDS)
    except Exception as e:
        log.exception(f"The tray app stopped because of an error: {e}")
        return 1
    finally:
        log.info("Quitting: stopping the server")
        ctl.shutdown()
        lock.release()
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="SkyrimNet MultiProxy")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "view-logs", "live-log", "quit"])
    ap.add_argument("--from-skyrim", action="store_true", help="the Skyrim mod started this (so closing Skyrim may quit it)")
    ap.add_argument("--log", default=str(SETTINGS_DIR / "multiproxy.log"))
    args = ap.parse_args(argv)
    if args.command == "quit":                      # used by the installer: ask a running tray app to stop its server and quit, and wait for it to go
        lock = SingleInstance()
        if not lock.tell_existing(b"quit"):
            return 0
        for _ in range(60):
            time.sleep(0.5)
            probe = SingleInstance()
            if probe.acquire():
                probe.release()
                return 0
        return 1
    if args.command == "view-logs":
        return show_log_window(Path(args.log))
    if args.command == "live-log":
        try:
            return follow_log(Path(args.log))
        except KeyboardInterrupt:
            return 0
    return run_tray(args)


if __name__ == "__main__":
    sys.exit(main())
