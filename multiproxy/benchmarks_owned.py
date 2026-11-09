"""Benchmarks: owning the engine process and everything it starts, and stopping it for sure.

The benchmark's engine is started inside a container only it lives in: on Windows a Job Object that kills its members when it closes, on Linux its own process group. The process is
known by its identity (its id PLUS the time it started PLUS its program name), so an id that another program has been given since is never touched. Stopping is: ask the engine to
leave, wait 5 seconds, then end the whole owned tree by force, then wait up to 30 seconds for every owned process to be gone. Nothing is ever ended by program name or by port, and if
the identity does not match, nothing is ended at all (a reused id is reported, not acted on).

All the operating-system calls are in `SystemOps`, so the rules above are tested with a fake one.
"""
import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

GRACEFUL_S = 5.0
GONE_S = 30.0


@dataclass(frozen=True)
class Identity:
    pid: int
    started: Optional[float]               # when the process started (Windows: creation time in 100 ns ticks; Linux: clock ticks since boot); None when it cannot be read
    exe: str                               # the program's file name, lower case

    def same_as(self, other: Optional["Identity"]) -> bool:
        return other is not None and other.pid == self.pid and other.exe == self.exe and (self.started is None or other.started is None or other.started == self.started)


class SystemOps:
    """The real operating-system calls. Replaced by a fake in the tests."""

    def identity(self, pid: int) -> Optional[Identity]:
        try:
            return _win_identity(pid) if sys.platform == "win32" else _linux_identity(pid)
        except Exception:
            return None

    def make_container(self, pid: int):
        """Put the process (and so everything it starts) in a container only this benchmark owns. Returns a handle, or None when it cannot."""
        try:
            return _win_job_for(pid) if sys.platform == "win32" else {"pgid": pid}
        except Exception:
            return None

    def ask_exit(self, pid: int, container) -> None:
        """The polite request. Linux: SIGTERM to the owned group. Windows has no polite signal for a program without a console, so this ends only the main process and the
        container (below) ends whatever it left behind."""
        if sys.platform == "win32":
            _win_terminate_process(pid)
        else:
            os.killpg(container["pgid"], signal.SIGTERM)

    def kill_container(self, container) -> None:
        if sys.platform == "win32":
            _win_terminate_job(container)
        else:
            os.killpg(container["pgid"], signal.SIGKILL)

    def container_alive(self, container) -> bool:
        if container is None:
            return False
        if sys.platform == "win32":
            return _win_job_active(container) > 0
        try:
            os.killpg(container["pgid"], 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def close_container(self, container) -> None:
        if sys.platform == "win32" and container is not None:
            _win_close_job(container)


class OwnedProcess:
    """One engine process and its container. `stop()` is the only way it is ended."""

    def __init__(self, pid: int, ops: Optional[SystemOps] = None):
        self.ops = ops or SystemOps()
        self.pid = pid
        self.identity = self.ops.identity(pid)
        self.container = self.ops.make_container(pid)

    async def stop(self, process_gone: Callable[[], bool] = lambda: False, graceful_s: float = GRACEFUL_S, gone_s: float = GONE_S,
                   sleep: Callable = asyncio.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
        """Returns `{"graceful": bool, "forced": bool, "owned_gone": bool, "identity_mismatch": bool, "error": str}`. `process_gone()` says whether the main process has ended."""
        out = {"graceful": False, "forced": False, "owned_gone": False, "identity_mismatch": False, "error": ""}
        now_id = self.ops.identity(self.pid)
        if now_id is not None and self.identity is not None and not self.identity.same_as(now_id):
            out["identity_mismatch"] = True                          # the id belongs to another program now: nothing is ended
            out["error"] = "the process id is used by another program now, so nothing was ended"
            try:
                self.ops.close_container(self.container)             # (releases our own container; it holds only what this benchmark started)
            except Exception:
                pass
            out["owned_gone"] = not self.ops.container_alive(self.container)
            return out
        if now_id is not None:
            try:
                self.ops.ask_exit(self.pid, self.container)
                out["graceful"] = True
            except PermissionError as e:
                out["error"] = f"could not ask the engine to leave: {e}"
            except Exception as e:
                out["error"] = f"could not ask the engine to leave: {type(e).__name__}"
            end = clock() + graceful_s
            while clock() < end:
                main_done = process_gone() or self.ops.identity(self.pid) is None
                if main_done and not self.ops.container_alive(self.container):
                    break
                await sleep(0.2)
        if self.ops.container_alive(self.container):
            out["forced"] = True
            try:
                self.ops.kill_container(self.container)
            except PermissionError as e:
                out["error"] = f"could not end the engine: {e}"
            except Exception as e:
                out["error"] = f"could not end the engine: {type(e).__name__}"
        end = clock() + gone_s
        while self.ops.container_alive(self.container) and clock() < end:
            await sleep(0.2)
        out["owned_gone"] = not self.ops.container_alive(self.container)
        try:
            self.ops.close_container(self.container)
        except Exception:
            pass
        return out


# --- Windows ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def _k32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    return k


def _win_identity(pid: int) -> Optional[Identity]:
    import ctypes
    from ctypes import wintypes
    k = _k32()
    h = k.OpenProcess(0x1000, False, int(pid))                        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if k.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:      # 259 = still active; anything else: it has ended (a handle held by someone else keeps its shell around)
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        started = None
        if k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            started = float((created.dwHighDateTime << 32) | created.dwLowDateTime)
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        exe = os.path.basename(buf.value).lower() if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
        return Identity(int(pid), started, exe)
    finally:
        k.CloseHandle(h)


def _win_job_for(pid: int):
    import ctypes
    from ctypes import wintypes
    k = _k32()

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXT()
    info.Basic.LimitFlags = 0x2000                                    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k.CloseHandle(job)
        return None
    hp = k.OpenProcess(0x0100 | 0x0001, False, int(pid))             # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if not hp:
        k.CloseHandle(job)
        return None
    try:
        if not k.AssignProcessToJobObject(job, hp):
            k.CloseHandle(job)
            return None
    finally:
        k.CloseHandle(hp)
    return job


def _win_terminate_process(pid: int) -> None:
    k = _k32()
    h = k.OpenProcess(0x0001, False, int(pid))                        # PROCESS_TERMINATE
    if not h:
        raise PermissionError("cannot open the engine process")
    try:
        k.TerminateProcess(h, 1)
    finally:
        k.CloseHandle(h)


def _win_terminate_job(job) -> None:
    if not _k32().TerminateJobObject(job, 1):
        raise PermissionError("could not end the container")


def _win_job_active(job) -> int:
    import ctypes
    from ctypes import wintypes

    class ACCOUNTING(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64), ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD), ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
    a = ACCOUNTING()
    if not _k32().QueryInformationJobObject(job, 1, ctypes.byref(a), ctypes.sizeof(a), None):
        return 0
    return int(a.ActiveProcesses)


def _win_close_job(job) -> None:
    _k32().CloseHandle(job)


# --- Linux ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def _linux_identity(pid: int) -> Optional[Identity]:
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    left, _, right = raw.rpartition(")")
    comm = left.partition("(")[2].lower()
    fields = right.split()
    started = float(fields[19]) if len(fields) > 19 else None         # field 22 of the stat line: start time in clock ticks since boot
    return Identity(int(pid), started, comm)
