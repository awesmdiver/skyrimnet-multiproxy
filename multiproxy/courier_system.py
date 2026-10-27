"""MultiProxy Courier: what this device reports about itself, and the few things it does to it (2026-10-01).

Built to design/multiproxy-courier-mockup.html. Everything here is small and honest: a reading we can't
take comes back as None ("not available"), never a made-up number. Works on Windows and Linux; nothing
here imports the rest of MultiProxy except the graphics-card detector.

  * graphics cards (a list: a device with two cards reports both), system memory, free disk, battery, uptime
  * keep this device awake (Windows: "keep the system awake" request; Linux: a sleep inhibitor)
  * start with Windows (a per-user Run entry: no admin needed)
  * the Windows firewall rule's wording (the rule itself is added by the setup script, with Windows' own OK prompt)
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess

import hidden_process  # every program started from here goes through it: no console window flashing up
import native_probe
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

STARTUP_NAME = "MultiProxy Courier"
FIREWALL_RULE_NAME = "MultiProxy Courier"


def _run(cmd: list, timeout: float = 10) -> Optional[str]:
    ans = native_probe.answer(cmd)             # Windows: read in-process, no program started (see native_probe.py)
    if ans is not None:
        return ans
    try:
        flags = 0x08000000 if os.name == "nt" else 0          # no console window flashing up
        r = hidden_process.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


# --------------------------------------------------------------------------------------------
# Graphics cards
# --------------------------------------------------------------------------------------------
def _num(text: str) -> Optional[float]:
    """"[N/A]", "N/A", "Not Supported" and friends mean the card doesn't say: None, never 0."""
    t = (text or "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    return float(t)


def parse_nvidia_cards(csv_text: str) -> list[dict]:
    """`nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits`"""
    cards = []
    for line in (csv_text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5 or not parts[0]:
            continue
        used, total, busy, temp = (_num(p) for p in parts[1:5])
        cards.append({
            "name": parts[0], "vendor": "nvidia",
            "memory_used_mb": int(used) if used is not None else None,
            "memory_total_mb": int(total) if total is not None else None,
            "busy_percent": int(busy) if busy is not None else None,
            "temperature_c": int(temp) if temp is not None else None,
            "stats_available": busy is not None or temp is not None,
        })
    return cards


def read_gpus(run: Callable[..., Optional[str]] = _run, detect: Optional[Callable[[], Any]] = None) -> list[dict]:
    """Every graphics card, with what each one will tell us. NVIDIA cards come from nvidia-smi (busy % and
    temperature included); other cards come from the card detector with name and memory only, and
    `stats_available: False` so the desktop says "not available" instead of showing a fake number."""
    out = run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
               "--format=csv,noheader,nounits"])
    cards = parse_nvidia_cards(out) if out else []
    if cards:
        return cards
    if detect is None:
        from local_models import detect_gpu as detect
    try:
        g = detect()
    except Exception:
        g = None
    if not g:
        return []
    return [{"name": g.name, "vendor": g.vendor, "memory_used_mb": None, "memory_total_mb": g.memory_mb,
             "busy_percent": None, "temperature_c": None, "stats_available": False}]


# --------------------------------------------------------------------------------------------
# system memory, disk, battery, uptime
# --------------------------------------------------------------------------------------------
def parse_meminfo(text: str) -> Optional[dict]:
    vals = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(\w+):\s+(\d+) kB", text or "", flags=re.M)}
    if "MemTotal" not in vals or "MemAvailable" not in vals:
        return None
    total = vals["MemTotal"] // 1024
    return {"total_mb": total, "used_mb": total - vals["MemAvailable"] // 1024}


def read_pc_memory() -> Optional[dict]:
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        total = st.ullTotalPhys // (1024 * 1024)
        return {"total_mb": total, "used_mb": total - st.ullAvailPhys // (1024 * 1024)}
    try:
        return parse_meminfo(Path("/proc/meminfo").read_text())
    except OSError:
        return None


def read_disk(path: Optional[Path]) -> Optional[dict]:
    """Free space on the drive the models live on."""
    try:
        p = Path(path) if path else Path.home()
        while not p.exists() and p != p.parent:
            p = p.parent
        u = shutil.disk_usage(p)
    except OSError:
        return None
    return {"path": str(path or p), "free_gb": round(u.free / 1024 ** 3, 1), "total_gb": round(u.total / 1024 ** 3, 1)}


def read_power() -> dict:
    """Battery and plug. A desktop has no battery: `has_battery: False`, nothing else invented."""
    none = {"has_battery": False, "battery_percent": None, "plugged_in": None}
    if sys.platform == "win32":
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                        ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        st = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return none
        return interpret_power_status(st.ACLineStatus, st.BatteryFlag, st.BatteryLifePercent)
    try:
        base = Path("/sys/class/power_supply")
        bats = [p for p in base.iterdir() if (p / "type").read_text().strip() == "Battery"]
        if not bats:
            return none
        pct = int((bats[0] / "capacity").read_text().strip())
        status = (bats[0] / "status").read_text().strip().lower()
        return {"has_battery": True, "battery_percent": pct, "plugged_in": status in ("charging", "full", "not charging")}
    except (OSError, ValueError):
        return none


def interpret_power_status(ac_line: int, battery_flag: int, percent: int) -> dict:
    """Windows' GetSystemPowerStatus: ACLineStatus 1 = plugged in, 0 = on battery, 255 = unknown;
    BatteryFlag 128 = no battery at all; BatteryLifePercent 255 = unknown."""
    if battery_flag == 128 or battery_flag == 255 and percent == 255:
        return {"has_battery": False, "battery_percent": None, "plugged_in": None}
    return {"has_battery": True, "battery_percent": int(percent) if percent <= 100 else None,
            "plugged_in": {1: True, 0: False}.get(ac_line)}


def read_uptime_seconds() -> Optional[int]:
    if sys.platform == "win32":
        f = ctypes.windll.kernel32.GetTickCount64
        f.restype = ctypes.c_ulonglong
        return int(f() // 1000)
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


# --------------------------------------------------------------------------------------------
# Keep this device awake
# --------------------------------------------------------------------------------------------
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """While on, this device doesn't go to sleep. Windows: a thread holds a "system required" request for as
    long as it lives (the request belongs to the thread that made it). Linux: `systemd-inhibit` holds a
    sleep lock for as long as its child process runs. Turning it off, or the Courier quitting, lets go."""

    def __init__(self, backend: Optional["_AwakeBackend"] = None):
        self._backend = backend or (_WindowsAwake() if sys.platform == "win32" else _LinuxAwake())
        self._on = False

    @property
    def on(self) -> bool:
        return self._on

    def set(self, enabled: bool) -> bool:
        """True when the request now matches what was asked."""
        if enabled and not self._on:
            self._on = bool(self._backend.start())
        elif not enabled and self._on:
            self._backend.stop()
            self._on = False
        return self._on == bool(enabled)


class _AwakeBackend:
    def start(self) -> bool: ...
    def stop(self) -> None: ...


class _WindowsAwake(_AwakeBackend):
    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ok = threading.Event()

    def start(self) -> bool:
        self._stop.clear()
        self._ok.clear()
        ready = threading.Event()

        def hold():
            k = ctypes.windll.kernel32
            k.SetThreadExecutionState.restype = ctypes.c_ulong
            if k.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                self._ok.set()
            ready.set()
            self._stop.wait()
            k.SetThreadExecutionState(ES_CONTINUOUS)               # let go

        self._thread = threading.Thread(target=hold, name="courier-keep-awake", daemon=True)
        self._thread.start()
        ready.wait(5)
        return self._ok.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)


class _LinuxAwake(_AwakeBackend):
    def __init__(self):
        self._proc: Optional["subprocess.Popen"] = None

    def start(self) -> bool:
        exe = shutil.which("systemd-inhibit")
        if not exe:
            return False
        try:
            self._proc = hidden_process.popen([exe, "--what=sleep:idle", "--who=MultiProxy Courier",
                                           "--why=This device is running AI models for your main device", "--mode=block",
                                           "sleep", "infinity"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        time.sleep(0.2)
        return self._proc.poll() is None

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


# --------------------------------------------------------------------------------------------
# Start with Windows
# --------------------------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class StartWithWindows:
    """A per-user "Run" entry (no admin needed). `registry` is a winreg-like module, so tests never touch the
    real one. On Linux the installer makes a systemd service instead and this reports it isn't its job."""

    def __init__(self, command: str, registry: Any = None, name: str = STARTUP_NAME):
        self.command, self.name = command, name
        self._reg = registry
        if self._reg is None and sys.platform == "win32":
            import winreg
            self._reg = winreg

    @property
    def supported(self) -> bool:
        return self._reg is not None

    def is_on(self) -> bool:
        if not self.supported:
            return False
        try:
            with self._reg.OpenKey(self._reg.HKEY_CURRENT_USER, RUN_KEY, 0, self._reg.KEY_READ) as k:
                value, _ = self._reg.QueryValueEx(k, self.name)
                return bool(value)
        except OSError:
            return False

    def refresh(self, belongs_to: str = "") -> bool:
        """An entry this program made earlier (an older install pointed it at pythonw.exe, which Windows lists as "Python") is rewritten to the current command. Only the entry
        under this program's own value name, only while it is switched on, and only when its value names `belongs_to` (this program's folder) so another install's entry is
        never taken over. True when it rewrote it."""
        if not self.supported or not self.is_on():
            return False
        try:
            with self._reg.OpenKey(self._reg.HKEY_CURRENT_USER, RUN_KEY, 0, self._reg.KEY_READ) as k:
                value, _ = self._reg.QueryValueEx(k, self.name)
        except OSError:
            return False
        if str(value) == self.command or (belongs_to and belongs_to.lower() not in str(value).lower()):
            return False
        return self.set(True)

    def set(self, enabled: bool) -> bool:
        if not self.supported:
            return False
        try:
            with self._reg.OpenKey(self._reg.HKEY_CURRENT_USER, RUN_KEY, 0, self._reg.KEY_SET_VALUE) as k:
                if enabled:
                    self._reg.SetValueEx(k, self.name, 0, self._reg.REG_SZ, self.command)
                else:
                    try:
                        self._reg.DeleteValue(k, self.name)
                    except FileNotFoundError:
                        pass
            return True
        except OSError:
            return False


# --------------------------------------------------------------------------------------------
# The Windows firewall rule (added by the setup script, narrowly)
# --------------------------------------------------------------------------------------------
def firewall_commands(port: int, discovery_port: int, program: str = "") -> list[list[str]]:
    """The two rules the setup script adds (it runs them elevated, after Windows' own OK prompt): the
    Courier's port, and the "I'm here" finder. Private networks ONLY, never Public, from this network only (`remoteip=LocalSubnet`),
    named for the Courier, and for the Courier's own program when it is given (`program=`)."""
    base = ["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE_NAME}", "dir=in", "action=allow",
            "profile=private", "remoteip=LocalSubnet"]
    extra = [f"program={program}"] if program else []
    return [base + ["protocol=TCP", f"localport={int(port)}"] + extra, base + ["protocol=UDP", f"localport={int(discovery_port)}"] + extra]
