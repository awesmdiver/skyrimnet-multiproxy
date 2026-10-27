"""Is that process still there? A plain look at one process number (2026-10-26), for the tray app's "Start and stop with Skyrim" backup: when Skyrim ends hard (killed, End
Task) its mod never runs its exit code, so MultiProxy is never told "closed". The mod's "started" message carries Skyrim's process number; the tray app looks at it every
few seconds. No admin rights, no hooks, nothing is started or stopped.

`look(pid)` answers (state, started):
  state    "alive" (it exists), "gone" (it does not), or "unknown" (it could not be looked at, for example a rights error: the caller does nothing).
  started  a token for WHEN that process started (Windows: its creation time; Linux: its start tick), or None when that could not be read. A process number is reused by
           Windows after a process ends, so the number alone is not enough: the same number with another start token is a different program, i.e. Skyrim is gone.
"""
import os
import sys
from typing import Optional

STILL_ACTIVE = 259
ERROR_INVALID_PARAMETER = 87          # OpenProcess says this for a process number that does not exist
ERROR_ACCESS_DENIED = 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _look_windows(pid: int) -> tuple:
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        err = ctypes.get_last_error()
        return ("gone", None) if err == ERROR_INVALID_PARAMETER else ("unknown", None)
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return "unknown", None
        if code.value != STILL_ACTIVE:                      # a handle can outlive its process: an exit code means it ended
            return "gone", None
        created, _exit, _kernel, _user = (wintypes.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(_exit), ctypes.byref(_kernel), ctypes.byref(_user)):
            return "alive", None
        return "alive", (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        k.CloseHandle(h)


def _look_posix(pid: int) -> tuple:
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read().decode("latin-1")
    except FileNotFoundError:
        return "gone", None
    except OSError:
        return "unknown", None
    after = raw[raw.rfind(")") + 2:].split()                 # after the "(name)" field: state is first, start time is the 20th
    if after and after[0] in ("Z", "X"):
        return "gone", None                                  # a zombie has ended
    try:
        return "alive", int(after[19])
    except (IndexError, ValueError):
        return "alive", None


def look(pid: int) -> tuple:
    if not isinstance(pid, int) or pid <= 0:
        return "unknown", None
    try:
        if sys.platform == "win32":
            return _look_windows(pid)
        if os.path.isdir("/proc"):
            return _look_posix(pid)
        os.kill(pid, 0)
        return "alive", None
    except ProcessLookupError:
        return "gone", None
    except Exception:
        return "unknown", None


def has_ended(state: str, started: Optional[int], first_started: Optional[int]) -> bool:
    """Has the watched program ended? Gone, or the same number now belonging to a process that started at another time (the number was reused). "unknown" never counts."""
    if state == "gone":
        return True
    return state == "alive" and started is not None and first_started is not None and started != first_started
