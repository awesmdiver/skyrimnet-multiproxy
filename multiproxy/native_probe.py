"""Reads this device's graphics and process numbers without starting a program (2026-10-01).

Real problem: a Courier device answered "how is the graphics card doing?" every few seconds by running `nvidia-smi`, `typeperf` and
`tasklist` each time. From a program with no console window, every one of those opened a console window for a split second (on a
laptop with Windows Terminal as the default: 23 windows in 70 seconds). `hidden_process` now hides any program that still has to
be started; this module makes most of them unnecessary: on Windows it answers the same three questions from inside the process,
with the graphics driver's own library (NVML, the thing nvidia-smi is built on), the Windows performance-counter library (PDH,
what typeperf reads) and the process snapshot (what tasklist lists).

`answer(cmd)` takes the command that *would* have been run and returns exactly the text it would have printed, or None when it
can't (not Windows, a command or field it doesn't know, a library that isn't there, any error): the caller then runs the real
program, hidden, as before. So every parser in gpu_monitor / courier_system is unchanged, and the real tools stay the fallback.
"""
import ctypes
import csv
import io
import socket
import sys
import threading
import time
from typing import Optional

_lock = threading.Lock()
_nvml = None
_nvml_failed = False
_pdh = None
_pdh_failed = False

_NVIDIA_FIELDS = {"name", "memory.used", "memory.total", "utilization.gpu", "temperature.gpu"}


def answer(cmd: list) -> Optional[str]:
    """The text `cmd` would print, read in-process; None when this can't answer it (run the real program then)."""
    if sys.platform != "win32" or not cmd:
        return None
    try:
        exe = str(cmd[0]).lower()
        if exe == "nvidia-smi":
            return _nvidia_smi(cmd[1:])
        if exe == "typeperf":
            return _typeperf(cmd[1:])
        if exe == "tasklist":
            return _tasklist(cmd[1:])
    except Exception:
        return None
    return None


# ---- nvidia-smi --query-gpu=... --format=csv,noheader,nounits  (NVML) ----------------------------------------------------
class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


def _load_nvml():
    global _nvml, _nvml_failed
    if _nvml is not None or _nvml_failed:
        return _nvml
    for path in ("nvml.dll", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"):
        try:
            lib = ctypes.WinDLL(path)
            if lib.nvmlInit_v2() == 0:
                _nvml = lib
                return lib
        except OSError:
            continue
    _nvml_failed = True
    return None


def _nvidia_smi(args: list) -> Optional[str]:
    fields = None
    for a in args:
        if a.startswith("--query-gpu="):
            fields = a.split("=", 1)[1].split(",")
    if fields is None or "--format=csv,noheader,nounits" not in args or not set(fields) <= _NVIDIA_FIELDS:
        return None
    with _lock:
        lib = _load_nvml()
        if lib is None:
            return None
        count = ctypes.c_uint(0)
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return None
        lines = []
        for i in range(count.value):
            h = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h)) != 0:
                return None
            row = []
            for f in fields:
                row.append(_nvml_field(lib, h, f))
            lines.append(", ".join(row))
        return "\n".join(lines) + ("\n" if lines else "")


def _nvml_field(lib, h, field: str) -> str:
    """One value the way nvidia-smi prints it ("[N/A]" when the card doesn't say)."""
    if field == "name":
        buf = ctypes.create_string_buffer(96)
        return buf.value.decode("utf-8", "replace") if lib.nvmlDeviceGetName(h, buf, 96) == 0 else "[N/A]"
    if field in ("memory.used", "memory.total"):
        m = _Memory()
        if lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m)) != 0:
            return "[N/A]"
        return str((m.used if field == "memory.used" else m.total) // (1024 * 1024))
    if field == "utilization.gpu":
        u = _Utilization()
        return str(u.gpu) if lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u)) == 0 else "[N/A]"
    t = ctypes.c_uint()
    return str(t.value) if lib.nvmlDeviceGetTemperature(h, 0, ctypes.byref(t)) == 0 else "[N/A]"


# ---- typeperf "\GPU Process Memory(*)\Dedicated Usage" -sc 1  (PDH) ---------------------------------------------------------
class _CounterItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("CStatus", ctypes.c_ulong), ("largeValue", ctypes.c_longlong)]


_PDH_MORE_DATA = 0x800007D2
_PDH_FMT_LARGE = 0x400
_GPU_COUNTER = r"\GPU Process Memory(*)\Dedicated Usage"


def _typeperf(args: list) -> Optional[str]:
    if len(args) < 3 or args[0] != _GPU_COUNTER or args[1:3] != ["-sc", "1"]:
        return None
    items = gpu_process_counters()
    if items is None:
        return None
    return format_typeperf(items, socket.gethostname(), time.localtime())


def gpu_process_counters() -> Optional[list]:
    """[(instance name like pid_123_luid_0x.._0x.._phys_0, bytes)] from the performance counters, or None."""
    global _pdh, _pdh_failed
    with _lock:
        if _pdh_failed:
            return None
        if _pdh is None:
            lib = ctypes.WinDLL("pdh")
            query, counter = ctypes.c_void_p(), ctypes.c_void_p()
            if lib.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0 or \
                    lib.PdhAddEnglishCounterW(query, _GPU_COUNTER, 0, ctypes.byref(counter)) != 0:
                _pdh_failed = True
                return None
            _pdh = (lib, query, counter)
        lib, query, counter = _pdh
        if lib.PdhCollectQueryData(query) != 0:
            return None
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        if lib.PdhGetFormattedCounterArrayW(counter, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None) & 0xFFFFFFFF != _PDH_MORE_DATA:
            return [] if count.value == 0 else None
        buf = ctypes.create_string_buffer(size.value)
        if lib.PdhGetFormattedCounterArrayW(counter, _PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return None
        arr = ctypes.cast(buf, ctypes.POINTER(_CounterItem))
        return [(arr[i].szName, int(arr[i].largeValue)) for i in range(count.value) if arr[i].CStatus in (0, 0x00000001)]


def format_typeperf(items: list, host: str, now: time.struct_time) -> str:
    """The CSV `typeperf ... -sc 1` prints: a header of counter paths and one row of values."""
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["(PDH-CSV 4.0)"] + [f"\\\\{host}\\GPU Process Memory({name})\\Dedicated Usage" for name, _ in items])
    w.writerow([time.strftime("%m/%d/%Y %H:%M:%S", now) + ".000"] + [f"{float(v):.6f}" for _, v in items])
    return out.getvalue()


# ---- tasklist /FO CSV /NH  (process snapshot) -------------------------------------------------------------------------------
class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


def _tasklist(args: list) -> Optional[str]:
    if [a.lower() for a in args] != ["/fo", "csv", "/nh"]:
        return None
    procs = process_list()
    if procs is None:
        return None
    return format_tasklist(procs)


def process_list() -> Optional[list]:
    """[(exe name, pid)] for every running program, or None."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)                       # TH32CS_SNAPPROCESS
    if snap in (None, ctypes.c_void_p(-1).value):
        return None
    try:
        e = _ProcessEntry()
        e.dwSize = ctypes.sizeof(_ProcessEntry)
        out = []
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append((e.szExeFile, int(e.th32ProcessID)))
            ok = k.Process32NextW(snap, ctypes.byref(e))
        return out
    finally:
        k.CloseHandle(snap)


def format_tasklist(procs: list) -> str:
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    for name, pid in procs:
        w.writerow([name, str(pid), "Console", "0", "0 K"])
    return out.getvalue()
