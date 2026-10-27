"""MultiProxy Courier: the tray icon and the "This device is ready" window (2026-10-01).

Built to design/multiproxy-courier-mockup.html. The Courier has no web page. On Windows the whole interface is a
tray icon (a green dot when your main device is connected, grey when it's just waiting) with a small menu, plus
one window shown once after setup. Everything the player reads is in the two pure functions at the top
(`menu_model` and `ready_window_text`) so the words can be reviewed, and tested, without a screen.

The tray needs `pystray` and `Pillow` (setup installs them into the Courier's own private environment); the
window needs Python's built-in tkinter. Linux has no tray: a service, a status command and the installer's printout.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

import app_icon
import hidden_process  # every program started from here goes through it: no console window flashing up
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

START_STEPS = (
    {"text": "On your main device, open MultiProxy > Other devices.", "note": None},
    {"text": "On your main PC, select this computer ({pc}) and enter the code below.",
     "note": "(If Windows Firewall asks, allow access on private networks.)"},
)
WRONG_CODE_STATUS = "A device at {address} tried to connect with an old pairing code. Update the code on your main PC."
WRONG_CODE_MENU = "Wrong pairing code from {address}"                    # the tray menu's header: the same words, shorter
NEW_CODE_HINT = "You made a new pairing code on this device. Enter it on your main device."
BUILTIN_LINE = "Model engine (llama.cpp): Ready\n(downloads when you first load a model)"   # second line under the first (director, 2026-10-02)
BUILTIN_LINE_DOWNLOADED = "Model engine (llama.cpp): Ready"
# The VPN field in the ready window: the same label, hint and example as the main device's Settings page and its device card; the last line says when to use it.
VPN_FIELD = {"label": "Other VPN or Tailscale networks",
             "placeholder": "10.8.0.0/16",
             "hint": "Only needed if your VPN uses addresses outside normal home ranges. Separate them with commas.",
             "button": "Save",
             "line": "Connecting over a VPN? Add the network above before you pair."}


# --------------------------------------------------------------------------------------------
# The words
# --------------------------------------------------------------------------------------------
def version_line() -> str:
    """"MultiProxy Courier 1.7": the same number the window and the log show, so two packages can be told apart."""
    from courier import COURIER_VERSION
    return f"MultiProxy Courier {COURIER_VERSION}"


def menu_model(connected: bool, client_name: str, code: str, keep_awake: bool, start_with_windows: bool,
               start_supported: bool = True, paired_name: Optional[str] = None, trouble: Optional[dict] = None) -> list[dict]:
    """The tray menu, top to bottom. `key` says what a click does; `None` text is a separator. `paired_name` is None for a
    Courier no main device has paired with; a paired one names it, keeps the code out of the menu (it stays under "Show pairing code…").
    `trouble` ({"address": ...}) is a device that has just tried a wrong pairing code: the header says so instead of only waiting."""
    if connected:
        status = f"Connected to {client_name or paired_name or 'your main device'}"
    elif trouble:
        status = WRONG_CODE_MENU.format(address=trouble["address"])
    elif paired_name:
        status = f"Waiting for {paired_name} to connect"
    else:
        status = "Waiting for your main device"
    from courier import COURIER_BUILD, COURIER_VERSION
    # the name once, then the facts (version and build first, so a bug report can quote them); the long form stays in the window, the log and `status`
    items = [{"key": "title", "text": "MultiProxy Courier", "enabled": False},
             {"key": "version", "text": f"{COURIER_VERSION} · {status}", "enabled": False}]
    if paired_name is None:
        items.append({"key": "code", "text": f"Pairing code {code}", "enabled": False})
    items += [
        {"key": "-", "text": None},
        {"key": "keep_awake", "text": "Keep this device awake", "checked": bool(keep_awake)},
    ]
    if start_supported:
        items.append({"key": "start_with_windows", "text": "Start with Windows", "checked": bool(start_with_windows)})
    items += [
        {"key": "-", "text": None},
        {"key": "show_code", "text": "Show pairing code…"},
    ]
    if paired_name is not None:
        items.append({"key": "unpair", "text": f"Unpair from {paired_name}"})
    items += [
        {"key": "new_code", "text": "Make new pairing code"},
        {"key": "open_log", "text": "Open log folder"},
        {"key": "-", "text": None},
        {"key": "quit", "text": "Quit Courier"},
    ]
    return items


def _program_line(p: dict) -> Optional[str]:
    ver = f" {p['version']}" if p.get("version") else ""
    if p["id"] == "llamacpp":
        if (p.get("engine") or {}).get("kind") == "existing":
            return f"llama.cpp{ver}: " + ("Running" if p.get("running") else "Ready")
        if p.get("installed"):
            return BUILTIN_LINE_DOWNLOADED
        return BUILTIN_LINE
    if not p.get("installed"):
        return None
    return f"{p['name']}{ver}: " + ("Running" if p.get("running") else "Installed (not running)")


def _this_pc() -> str:
    try:
        import other_pcs
        return other_pcs.short_hostname()
    except Exception:
        import socket
        return socket.gethostname().split(".")[0]


def trouble_lines(trouble: Optional[dict]) -> list:
    """What the window says under its title while a device is being refused for its pairing code (nothing otherwise)."""
    if not trouble:
        return []
    return [WRONG_CODE_STATUS.format(address=trouble["address"])] + ([NEW_CODE_HINT] if trouble.get("new_code") else [])


def pairing_state(report: Optional[dict], code: str, paired_name: Optional[str]) -> dict:
    """The one place that decides what a Courier says about its pairing, from its own report and its saved settings. Used by the ready window
    and by the Linux `status`, so they always tell the same story. `kind`: "not_paired" (the code is shown), "paired" (waiting for the main device
    to connect), "connected". A device being refused for an old code shows the code again, whatever else is true."""
    courier = (report or {}).get("courier") or {}
    trouble = courier.get("pairing_trouble")
    connected_to = courier.get("connected_to")
    if trouble or paired_name is None:
        kind, name = "not_paired", ""
    elif connected_to:
        kind, name = "connected", connected_to
    else:
        kind, name = "paired", paired_name
    return {"kind": kind, "name": name, "code": code, "trouble": trouble}


def build_line(report: Optional[dict]) -> str:
    """The small line at the foot of the window: which version this Courier is (1.<build>; so a player can compare two devices)."""
    c = (report or {}).get("courier") or {}
    if c.get("build") is None:
        return ""
    return f"Version 1.{c['build']}" + (f" · {c['built']}" if c.get("built") else "")


def ready_window_text(code: str, report: Optional[dict], paired_name: Optional[str] = None,
                      updated: Optional[tuple] = None) -> dict:
    """Everything the "This device is Ready" window says (the director's own words, 2026-10-02; the paired and updated
    wording is Gemini's, 2026-10-03). Four states: not paired (steps and code), paired (no steps, no code), connected right now
    (the same, but it says who) and any of them right after an update (`updated` = (old, new) adds a line)."""
    state = pairing_state(report, code, paired_name)
    trouble = state["trouble"]
    if state["kind"] == "not_paired":
        paired_name = None              # the code is what needs fixing: it is shown, even when the window was opened by a paired device
    programs = [ln for ln in (_program_line(p) for p in (report or {}).get("programs", [])) if ln]
    if not programs:
        programs = [BUILTIN_LINE]
    text = {
        "title": "This device is ready.",
        "updated": f"Courier just updated from {updated[0]} to {updated[1]}." if updated else None,
        "build": build_line(report),
        "paired": None,
        "trouble": trouble_lines(trouble),
        "found_title": "Found AI programs:",
        "programs": programs,
        "close": "Got it",
        "vpn": dict(VPN_FIELD),
    }
    if paired_name is not None:
        heading = f"Connected to {state['name']}." if state["kind"] == "connected" else f"You're all set! This computer is already paired with {paired_name}."
        text.update(steps_title="", steps=[], code="", paired=heading,
                    footer="You can safely close this window. Courier will stay active in your system tray by the clock.")
    else:
        text.update(steps_title="How to connect:",
                    steps=[dict(s, text=s["text"].replace("{pc}", ((report or {}).get("courier") or {}).get("name") or _this_pc())) for s in START_STEPS],
                    code=code,
                    footer="After you have paired this computer, you can safely close this window. Courier will stay active in your system tray by the clock.")
    return text


# --------------------------------------------------------------------------------------------
# The window (its own process: it can never hold the Courier up)
# --------------------------------------------------------------------------------------------
def read_pairing(path) -> Optional[tuple]:
    """(code, paired name or None) as the Courier saved them, or None when the file can't be read just now (it is being written, say).
    Read-only: the window must never make a new code of its own."""
    import json
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        code = str(data["code"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    name = (str(data.get("paired_name") or "") or "your main device") if data.get("paired") else None
    return code, name


class WindowWatch:
    """Decides what the open ready window should draw. `view` gets what the Courier and its settings say now and returns the text to draw, or None
    when nothing changed or the Courier couldn't be asked (the window keeps what it shows and tries again). "Show pairing code" keeps showing the code
    only until something about the pairing changes; after that the window tells the truth like any other."""

    def __init__(self, force_code: bool = False, updated: Optional[tuple] = None):
        self.force_code, self.updated = force_code, updated
        self.shown: Optional[dict] = None
        self._base = None

    def view(self, code: str, paired_name: Optional[str], report: Optional[dict]) -> Optional[dict]:
        if report is None and self.shown is not None:
            return None
        state = pairing_state(report, code, paired_name)
        key = (code, paired_name, state["kind"], state["name"], (state["trouble"] or {}).get("address"))
        if self._base is None:
            self._base = key
        elif key != self._base:
            self.force_code = False
        text = ready_window_text(code, report, None if self.force_code else paired_name, self.updated)
        if text == self.shown:
            return None
        self.shown = text
        return text


def save_vpn_text(settings, text: str) -> tuple:
    """What the ready window's Save does: check the typed list like everywhere else, hand it to the running Courier. Returns (the list as written, the
    plain line to show or "")."""
    import other_pcs
    from courier import apply_vpn_networks
    try:
        nets = other_pcs.clean_private_networks(text)
    except other_pcs.OtherPcsError as e:
        return text, e.message
    return ", ".join(nets), apply_vpn_networks(settings, nets)


def _draw_ready(tk, frame, text: dict, close, vpn: Optional[dict] = None) -> None:
    bg, card, fg, soft, accent = "#17171b", "#222228", "#ececf0", "#a0a0ab", "#d9b44a"
    tk.Label(frame, text=text["title"], bg=bg, fg=fg, font=("Segoe UI", 16, "bold")).pack(anchor="w")
    if text["updated"]:
        tk.Label(frame, text=text["updated"], bg=bg, fg=soft, font=("Segoe UI", 10), wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))
    for line in text["trouble"]:
        tk.Label(frame, text=line, bg=bg, fg=accent, font=("Segoe UI", 10, "bold"), wraplength=420, justify="left").pack(anchor="w", pady=(8, 0))
    if text["paired"]:
        tk.Label(frame, text=text["paired"], bg=bg, fg=fg, font=("Segoe UI", 11), wraplength=420, justify="left").pack(anchor="w", pady=(12, 14))
    else:
        tk.Label(frame, text=text["steps_title"], bg=bg, fg=fg, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 4))
    for n, step in enumerate(text["steps"], 1):
        row = tk.Frame(frame, bg=bg)
        row.pack(anchor="w", fill="x", pady=1)
        tk.Label(row, text=f"{n}.", bg=bg, fg=fg, font=("Segoe UI", 10), anchor="ne", justify="right").pack(side="left", anchor="n")
        col = tk.Frame(row, bg=bg)
        col.pack(side="left", anchor="w", padx=(6, 0))
        tk.Label(col, text=step["text"], bg=bg, fg=fg, font=("Segoe UI", 10), wraplength=400, justify="left").pack(anchor="w")
        if step["note"]:
            tk.Label(col, text=step["note"], bg=bg, fg=fg, font=("Segoe UI", 10, "italic"), wraplength=400, justify="left").pack(anchor="w")
    if text["code"]:
        box = tk.Frame(frame, bg=card, padx=18, pady=12)
        box.pack(fill="x", pady=(14, 14))
        tk.Label(box, text=text["code"], bg=card, fg=accent, font=("Consolas", 28, "bold")).pack(anchor="w")
    tk.Label(frame, text=text["found_title"], bg=bg, fg=fg, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
    for line in text["programs"]:
        tk.Label(frame, text="\u2022 " + line.replace("\n", "\n   "), bg=bg, fg=fg, font=("Segoe UI", 10), wraplength=420, justify="left").pack(anchor="w")
    if vpn is not None:
        f = text["vpn"]
        tk.Label(frame, text=f["label"], bg=bg, fg=fg, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(14, 4))
        row = tk.Frame(frame, bg=bg)
        row.pack(anchor="w", fill="x")
        entry = tk.Entry(row, textvariable=vpn["var"], bg=card, fg=fg, insertbackground=fg, relief="flat", font=("Segoe UI", 10), width=34)
        entry.pack(side="left", ipady=3)
        vpn["entry"] = entry
        vpn["ph"] = ph = {"on": False}                         # the example shows in grey while the box is empty; it is never saved as if typed

        def show_example(_e=None) -> None:
            if not vpn["var"].get():
                vpn["var"].set(f["placeholder"])
                entry.config(fg=soft)
                ph["on"] = True

        def hide_example(_e=None) -> None:
            if ph["on"]:
                vpn["var"].set("")
                entry.config(fg=fg)
                ph["on"] = False
        entry.bind("<FocusIn>", hide_example)
        entry.bind("<FocusOut>", show_example)
        show_example()
        tk.Button(row, text=f["button"], command=vpn["save"], bg=card, fg=fg, relief="flat", padx=14, pady=2).pack(side="left", padx=(8, 0))
        tk.Label(frame, text=f["hint"], bg=bg, fg=soft, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(4, 0))
        tk.Label(frame, textvariable=vpn["msg"], bg=bg, fg=accent, font=("Segoe UI", 10), wraplength=420, justify="left").pack(anchor="w")
        tk.Label(frame, text=f["line"], bg=bg, fg=fg, font=("Segoe UI", 10), wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))
    tk.Label(frame, text=text["footer"], bg=bg, fg=soft, font=("Segoe UI", 9), wraplength=380, justify="left").pack(anchor="w", pady=(14, 0))
    if text.get("build"):
        tk.Label(frame, text=text["build"], bg=bg, fg=soft, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 8))
    tk.Button(frame, text=text["close"], command=close, bg=card, fg=fg, relief="flat", padx=14, pady=4).pack(anchor="e")


WINDOW_WATCH_SECONDS = 2.0            # how often the open window asks the running Courier how things are


def show_ready_window(settings, updated_from: str = "", force_code: bool = False) -> int:
    from courier import local_call
    try:
        import tkinter as tk
    except ImportError:
        return 0                      # no desktop toolkit (a Linux server, say): the commands say everything the window would
    from types import SimpleNamespace
    from courier import COURIER_VERSION
    watch = WindowWatch(force_code, (updated_from, COURIER_VERSION) if updated_from else None)
    text = watch.view(settings.code, settings.paired_name(), local_call(settings, "GET", "/courier/v1/report"))
    bg = "#17171b"
    root = tk.Tk()
    root.title("MultiProxy Courier")
    app_icon.apply_to_window(root, "courier")
    root.configure(bg=bg)
    root.resizable(False, False)
    holder = {"frame": None}
    latest: dict = {}
    stopping = threading.Event()
    vpn = {"var": tk.StringVar(value=", ".join(str(n) for n in (settings.data.get("private_networks") or []))), "msg": tk.StringVar(value="")}

    def save_vpn() -> None:
        vpn["msg"].set("")
        typed = "" if vpn.get("ph", {}).get("on") else vpn["var"].get()

        def work() -> None:           # off the screen's thread: a slow answer never freezes the window
            latest["vpn"] = save_vpn_text(settings, typed)
        threading.Thread(target=work, daemon=True).start()
    vpn["save"] = save_vpn

    def draw(t: dict) -> None:
        if holder["frame"] is not None:
            holder["frame"].destroy()
        if vpn.get("ph", {}).get("on"):
            vpn["var"].set("")                         # the grey example is redrawn with the new picture, never kept as if typed
        holder["frame"] = tk.Frame(root, bg=bg, padx=28, pady=22)
        holder["frame"].pack()
        _draw_ready(tk, holder["frame"], t, root.destroy, vpn)

    draw(text)

    def ask() -> None:                # off the screen's thread: a slow answer never freezes the window
        while not stopping.wait(WINDOW_WATCH_SECONDS):
            saved = read_pairing(settings.path)
            if saved is None:
                continue
            code, name = saved
            latest["now"] = (code, name, local_call(SimpleNamespace(port=settings.port, code=code), "GET", "/courier/v1/report"))

    def tick() -> None:
        saved = latest.pop("vpn", None)
        if saved:
            if "ph" in vpn:
                vpn["ph"]["on"] = False
                vpn["entry"].config(fg="#ececf0")
            vpn["var"].set(saved[0])
            vpn["msg"].set(saved[1])
        now = latest.pop("now", None)
        if now:
            t = watch.view(*now)
            if t:
                draw(t)
        root.after(500, tick)

    threading.Thread(target=ask, daemon=True).start()
    root.after(500, tick)
    root.mainloop()
    stopping.set()
    return 0


def show_confirm_new_code(settings) -> int:
    """A small window asking the shared "make a new pairing code?" question. Exit code 0 = go ahead, 1 = cancelled."""
    from other_pcs import NEW_CODE_CONFIRM as q
    return _confirm_window(q)


def show_confirm_unpair(settings) -> int:
    """The same window for "Unpair from <name>?". Exit code 0 = go ahead, 1 = cancelled."""
    from other_pcs import UNPAIR_CONFIRM as q
    return _confirm_window(q, title=q["title"].format(name=settings.paired_name() or "your main device"))


def _confirm_window(q: dict, title: str = "") -> int:
    try:
        import tkinter as tk
    except ImportError:
        return 0
    bg, card, fg, accent = "#17171b", "#222228", "#ececf0", "#d9b44a"
    result = {"rc": 1}
    root = tk.Tk()
    root.title("MultiProxy Courier")
    app_icon.apply_to_window(root, "courier")
    root.configure(bg=bg)
    root.resizable(False, False)
    frame = tk.Frame(root, bg=bg, padx=26, pady=20)
    frame.pack()
    if title:
        tk.Label(frame, text=title, bg=bg, fg=fg, font=("Segoe UI", 11, "bold"), wraplength=340, justify="left").pack(anchor="w", pady=(0, 6))
    tk.Label(frame, text=q["question"], bg=bg, fg=fg, font=("Segoe UI", 10), wraplength=340, justify="left").pack(anchor="w", pady=(0, 16))
    row = tk.Frame(frame, bg=bg)
    row.pack(anchor="e")

    def go():
        result["rc"] = 0
        root.destroy()
    tk.Button(row, text=q["cancel"], command=root.destroy, bg=card, fg=fg, relief="flat", padx=14, pady=4).pack(side="left", padx=(0, 8))
    tk.Button(row, text=q["confirm"], command=go, bg=accent, fg="#17171b", relief="flat", padx=14, pady=4).pack(side="left")
    root.mainloop()
    return result["rc"]


# --------------------------------------------------------------------------------------------
# The tray
# --------------------------------------------------------------------------------------------
def make_icon(connected: bool):
    from PIL import Image, ImageDraw
    img = app_icon.base_image("courier")                       # the Courier's icon, with the status dot over its corner; the drawn envelope if the file is missing
    if img is None:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((4, 10, 60, 54), radius=9, fill=(40, 40, 48, 255), outline=(217, 180, 74, 255), width=3)   # an envelope: a courier's parcel
        d.line((8, 16, 32, 36, 56, 16), fill=(217, 180, 74, 255), width=3)
    d = ImageDraw.Draw(img)
    d.ellipse((38, 38, 62, 62), fill=(52, 199, 89, 255) if connected else (140, 140, 148, 255), outline=(20, 20, 24, 255), width=3)
    return img


class Tray:
    def __init__(self, courier, settings, config_dir: Path):
        import pystray                       # ImportError here is caught by the caller: no tray, the Courier still runs
        from PIL import Image                # noqa: F401
        self.pystray = pystray
        self.courier, self.settings, self.config_dir = courier, settings, Path(config_dir)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stop: Optional[asyncio.Event] = None
        self.icon = None
        self._last = None

    # ---- the menu ------------------------------------------------------------------------------------------
    def model(self) -> list[dict]:
        ok, name = self.courier.connected()
        d = self.settings.data
        return menu_model(ok, name, self.settings.code, bool(d.get("keep_awake")), bool(d.get("start_with_windows")),
                          bool(self.courier.startup and self.courier.startup.supported), self.settings.paired_name(), self.courier.pairing_trouble())

    def _menu(self):
        pystray = self.pystray
        entries = []
        for it in self.model():
            if it["text"] is None:
                entries.append(pystray.Menu.SEPARATOR)
            else:
                entries.append(pystray.MenuItem(it["text"], self._action(it["key"]), checked=(lambda _i, k=it["key"]: self._checked(k))
                                                if "checked" in it else None, enabled=it.get("enabled", True)))
        return pystray.Menu(*entries)

    def _checked(self, key: str) -> bool:
        return bool(self.settings.data.get(key))

    def _action(self, key: str):
        def run(icon=None, item=None):
            getattr(self, "do_" + key, lambda: None)()
        return run

    # ---- what the clicks do ------------------------------------------------------------------------------------
    def _poke(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.courier.signal_change)

    def do_keep_awake(self) -> None:
        on = not self.settings.data.get("keep_awake")
        self.settings.set(keep_awake=on)
        self.courier.keep_awake.set(on)
        self._poke()

    def do_start_with_windows(self) -> None:
        on = not self.settings.data.get("start_with_windows")
        if self.courier.startup and self.courier.startup.set(on):
            self.settings.set(start_with_windows=on)
        self._poke()

    def do_show_code(self) -> None:
        hidden_process.popen([sys.executable, str(Path(__file__).with_name("courier.py")), "ready-window", "--show-code", "--config-dir", str(self.config_dir)],
                         creationflags=0x08000000, stdin=subprocess.DEVNULL)

    def do_new_code(self) -> None:
        # Ask first: a new code disconnects whoever is connected (the question's words are shared with the Other devices tab)
        ask = hidden_process.run([sys.executable, str(Path(__file__).with_name("courier.py")), "confirm-new-code", "--config-dir", str(self.config_dir)],
                             creationflags=0x08000000, stdin=subprocess.DEVNULL)
        if ask.returncode != 0:
            return
        if self.loop:
            fut = asyncio.run_coroutine_threadsafe(self._new_code(), self.loop)
            fut.result(10)
        self.do_show_code()

    def do_unpair(self) -> None:
        # Ask first, then land in the same state as a new code: not paired, fresh code (shown, so it can be paired again)
        ask = hidden_process.run([sys.executable, str(Path(__file__).with_name("courier.py")), "confirm-unpair", "--config-dir", str(self.config_dir)],
                             creationflags=0x08000000, stdin=subprocess.DEVNULL)
        if ask.returncode != 0:
            return
        if self.loop:
            fut = asyncio.run_coroutine_threadsafe(self._new_code(), self.loop)
            fut.result(10)
        self.do_show_code()

    async def _new_code(self) -> None:
        self.courier.new_code()

    def do_open_log(self) -> None:
        os.startfile(str(self.config_dir))          # noqa: the log folder opens in Explorer

    def do_quit(self) -> None:
        if self.loop and self.stop:
            self.loop.call_soon_threadsafe(self.stop.set)
        if self.icon:
            self.icon.stop()

    # ---- running --------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.icon:
            return
        ok, name = self.courier.connected()
        trouble = self.courier.pairing_trouble()
        key = (ok, name, self.settings.code, self.settings.paired_name(), trouble and trouble["address"], bool(self.settings.data.get("keep_awake")), bool(self.settings.data.get("start_with_windows")))
        if key != self._last:
            self._last = key
            self.icon.icon = make_icon(ok)
            self.icon.title = "MultiProxy Courier: " + (f"connected to {name}" if ok else WRONG_CODE_MENU.format(address=trouble["address"]).lower() if trouble else "waiting for your main device")
            self.icon.menu = self._menu()
            self.icon.update_menu()

    def run_with(self, courier, serve: Callable[..., Any]) -> int:
        """Server on a background thread, the tray on this (main) one. Returns when "Quit Courier" is chosen."""
        result = {"rc": 0}
        ready = threading.Event()

        def server():
            self.loop = asyncio.new_event_loop()
            self.stop = asyncio.Event()
            ready.set()
            try:
                self.loop.run_until_complete(serve(courier, self.stop))
            except RuntimeError as e:
                courier.log.error(str(e))
                result["rc"] = 1
            finally:
                if self.icon:
                    self.icon.stop()

        t = threading.Thread(target=server, name="courier-server", daemon=True)
        t.start()
        ready.wait(5)

        def poll():
            import time
            while t.is_alive():
                self.refresh()
                time.sleep(1.5)

        def setup(icon):
            icon.visible = True
            threading.Thread(target=poll, daemon=True).start()

        self.icon = self.pystray.Icon("MultiProxy Courier", make_icon(False), "MultiProxy Courier", self._menu())
        courier.on_state_change = lambda: None
        self.icon.run(setup)
        if self.loop and self.stop:
            self.loop.call_soon_threadsafe(self.stop.set)
        t.join(30)
        return result["rc"]
