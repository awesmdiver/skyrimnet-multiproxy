"""Who is using the graphics card, read through Windows' own counter library in English (Windows only; Linux uses `nvidia-smi`).

Why this exists (the review in docs/roadmap/ideas-from-gputaskman.md):
  * `typeperf` takes counter names in the Windows display language, so on a Russian, Chinese, German... Windows it found nothing. `PdhAddEnglishCounterW` takes the English
    names on any language.
  * `Dedicated Usage` / `Shared Usage` count every allocation a program can SEE, including surfaces shared from other programs (the desktop read more than the whole card).
    `Local Usage` / `Non Local Usage` count what is CHARGED to each program and add up to the card. Those come first; the older pair is only the fallback.
  * One open query, read as often as needed: no program started per reading.

A counter that cannot be added or read is ABSENT (None), never zero.

The counter-library plumbing below (the ctypes structures, `_load_pdh`, the open / add / collect / read-an-array sequence, the instance-name patterns and the engine-type
grouping) is ported from `src/gputaskman/pdh.py` of gputaskman, with the licence notice kept:

    MIT License

    Copyright (c) 2026 Scott Hanselman

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Everything else here (the backend seam, the absent-not-zero rule, the card matching, the program names, the shared reader) is our own.
"""
from __future__ import annotations

import ctypes
import logging
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("multiproxy.gpu_counters")

MIB = 1024 * 1024
PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2

# What each reading is called, and the English counter paths to try in order (first one the Windows build has wins).
COUNTERS = {
    "dedicated": (r"\GPU Process Memory(*)\Local Usage", r"\GPU Process Memory(*)\Dedicated Usage"),
    "shared": (r"\GPU Process Memory(*)\Non Local Usage", r"\GPU Process Memory(*)\Shared Usage"),
    "committed": (r"\GPU Process Memory(*)\Total Committed",),
    "engine": (r"\GPU Engine(*)\Utilization Percentage",),
    "a_dedicated": (r"\GPU Adapter Memory(*)\Dedicated Usage",),
    "a_shared": (r"\GPU Adapter Memory(*)\Shared Usage",),
}

_LUID = r"luid_0x([0-9a-fA-F]+)_0x([0-9a-fA-F]+)"
_PROC_RE = re.compile(rf"pid_(\d+)_{_LUID}_phys_(\d+)")
_ENGINE_RE = re.compile(rf"pid_(\d+)_{_LUID}_phys_(\d+)_eng_(\d+)_engtype_(.*)$")
_ADAPTER_RE = re.compile(rf"^{_LUID}_phys_(\d+)$")


def luid_key(hi: str, lo: str) -> int:
    return (int(hi, 16) << 32) | int(lo, 16)


def engine_kind(t: str) -> str:
    """`3D`, `Compute_0`, `Copy`, `VideoDecode`, `VideoEncode` -> one of 3d / compute / copy / decode / encode / other."""
    t = (t or "").lower()
    for start, kind in (("3d", "3d"), ("compute", "compute"), ("cuda", "compute"), ("copy", "copy"), ("videodecode", "decode"), ("videoencode", "encode")):
        if t.startswith(start):
            return kind
    return "other"


# --- the library (ported plumbing) -------------------------------------------------------------------------------------------------------------------------------------------

class _Value(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong), ("str", ctypes.c_wchar_p)]


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("u", _Value)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


_pdh = None


def _load_pdh():
    """pdh.dll is loaded on first use so this module imports cleanly on Linux."""
    global _pdh
    if _pdh is None:
        from ctypes import wintypes
        lib = ctypes.WinDLL("pdh")
        lib.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        lib.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        lib.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        lib.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        lib.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        for fn in ("PdhOpenQueryW", "PdhAddEnglishCounterW", "PdhCollectQueryData", "PdhGetFormattedCounterArrayW", "PdhCloseQuery"):
            getattr(lib, fn).restype = wintypes.DWORD
        _pdh = lib
    return _pdh


class PdhBackend:
    """The real counter query: one open query for the life of the program. `add` always asks by the ENGLISH name (`PdhAddEnglishCounterW`)."""

    def __init__(self):
        from ctypes import wintypes
        self._w = wintypes
        lib = _load_pdh()
        self._query = wintypes.HANDLE()
        status = lib.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
        if status != 0:
            raise OSError(f"PdhOpenQuery failed: 0x{status:08X}")

    def add(self, path: str):
        h = self._w.HANDLE()
        return h if _pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(h)) == 0 else None

    def collect(self) -> bool:
        return _pdh.PdhCollectQueryData(self._query) == 0

    def read(self, handle) -> list:
        """`[(instance name, value)]` for every instance with valid data; [] when the counter cannot be read now."""
        w = self._w
        size, count = w.DWORD(0), w.DWORD(0)
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        if _pdh.PdhGetFormattedCounterArrayW(handle, fmt, ctypes.byref(size), ctypes.byref(count), None) != PDH_MORE_DATA:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if _pdh.PdhGetFormattedCounterArrayW(handle, fmt, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        return [(items[i].szName, items[i].FmtValue.u.doubleValue) for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1)]    # valid data / new data

    def close(self) -> None:
        if self._query:
            _pdh.PdhCloseQuery(self._query)
            self._query = self._w.HANDLE()


# --- what one reading holds --------------------------------------------------------------------------------------------------------------------------------------------------

@dataclass
class ProcGpu:
    """One program on one card. A figure the counter library did not give is None (absent), never 0."""
    dedicated: Optional[float] = None                 # bytes charged to this program in the card's own memory ("Local Usage")
    shared: Optional[float] = None                    # bytes charged to it in main memory used by the card ("Non Local Usage")
    committed: Optional[float] = None
    engines: dict = field(default_factory=dict)       # engine kind -> utilisation %

    @property
    def gpu_pct(self) -> Optional[float]:
        """The busiest engine wins (as in Task Manager)."""
        return max(self.engines.values()) if self.engines else None


@dataclass
class Snapshot:
    procs: dict = field(default_factory=dict)         # luid -> pid -> ProcGpu
    adapters: dict = field(default_factory=dict)      # luid -> {"dedicated": bytes or None, "shared": bytes or None}
    which: dict = field(default_factory=dict)         # reading -> the English path that was used


class GpuCounters:
    """One open query. `sample()` reads every counter once. Safe to call from several threads."""

    def __init__(self, backend=None):
        self._lock = threading.Lock()
        self._backend = backend if backend is not None else PdhBackend()
        self._handles: dict = {}
        self._paths: dict = {}
        for key, paths in COUNTERS.items():
            for path in paths:
                h = self._backend.add(path)
                if h is not None:
                    self._handles[key], self._paths[key] = h, path
                    break
        if self._handles:
            self._backend.collect()                    # rate counters (engine use) need a first sample to compare with
        log.info("Graphics counters opened: %s", ", ".join(f"{k}={p.rsplit(chr(92), 1)[-1]}" for k, p in self._paths.items()) or "none available")

    def available(self) -> bool:
        return bool(self._handles)

    def sample(self) -> Optional[Snapshot]:
        """None when the library could not read at all."""
        with self._lock:
            if not self._handles or not self._backend.collect():
                return None
            snap = Snapshot(which=dict(self._paths))

            def proc(luid: int, pid: int) -> ProcGpu:
                return snap.procs.setdefault(luid, {}).setdefault(pid, ProcGpu())

            for attr in ("dedicated", "shared", "committed"):
                h = self._handles.get(attr)
                if h is None:
                    continue
                for name, val in self._backend.read(h):
                    m = _PROC_RE.match(name)
                    if m:
                        p = proc(luid_key(m[2], m[3]), int(m[1]))
                        setattr(p, attr, (getattr(p, attr) or 0.0) + val)
            h = self._handles.get("engine")
            if h is not None:
                for name, val in self._backend.read(h):
                    m = _ENGINE_RE.match(name)
                    if m:
                        p = proc(luid_key(m[2], m[3]), int(m[1]))
                        k = engine_kind(m[6])
                        p.engines[k] = p.engines.get(k, 0.0) + val
            for attr, key in (("dedicated", "a_dedicated"), ("shared", "a_shared")):
                h = self._handles.get(key)
                if h is None:
                    continue
                for name, val in self._backend.read(h):
                    m = _ADAPTER_RE.match(name)
                    if m:
                        a = snap.adapters.setdefault(luid_key(m[1], m[2]), {"dedicated": None, "shared": None})
                        a[attr] = (a[attr] or 0.0) + val
            return snap

    def close(self) -> None:
        with self._lock:
            self._backend.close()
            self._handles = {}


# --- the card the page uses --------------------------------------------------------------------------------------------------------------------------------------------------

def match_adapter(snap: Snapshot, card_used_mb: Optional[float] = None, card_luid: Optional[int] = None) -> Optional[int]:
    """The adapter (luid) that is the card the page uses. With one adapter: that one. With several: the one whose own 'GPU Adapter Memory' dedicated usage is closest to what the card
    itself (nvidia-smi / NVML, the card the picker chose) says is in use; when the card did not say, or the adapter totals are missing, the one holding the most dedicated memory
    across its programs (the built-in graphics of a laptop holds almost nothing). `card_luid` (the adapter id from `gpu_adapters`, the same id the counters use) settles it at once
    when the counters list that adapter."""
    luids = sorted(set(snap.procs) | set(snap.adapters))
    if not luids:
        return None
    if card_luid is not None and card_luid in luids:
        return card_luid
    if len(luids) == 1:
        return luids[0]
    if card_used_mb is not None:
        known = [(l, snap.adapters[l]["dedicated"]) for l in luids if l in snap.adapters and snap.adapters[l].get("dedicated") is not None]
        if known:
            return min(known, key=lambda t: abs(t[1] / MIB - card_used_mb))[0]
    return max(luids, key=lambda l: sum((p.dedicated or 0.0) for p in snap.procs.get(l, {}).values()))


def programs_of(snap: Snapshot, luid: Optional[int]) -> dict:
    """`{pid: {"dedicated_mb": int or None, "shared_mb": int or None, "gpu_pct": float or None}}` for one adapter."""
    if luid is None:
        return {}
    out = {}
    for pid, p in snap.procs.get(luid, {}).items():
        out[pid] = {"dedicated_mb": None if p.dedicated is None else int(p.dedicated / MIB), "shared_mb": None if p.shared is None else int(p.shared / MIB), "gpu_pct": p.gpu_pct}
    return out


# --- the shared query and the readers ----------------------------------------------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_counters: Optional[GpuCounters] = None
_failed = False
_factory: Callable[[], GpuCounters] = lambda: GpuCounters()          # tests replace this


def _windows() -> bool:
    return sys.platform == "win32"                  # tests replace this


def _get() -> Optional[GpuCounters]:
    global _counters, _failed
    if not _windows():
        return None
    with _lock:
        if _counters is None and not _failed:
            try:
                _counters = _factory()
                if not _counters.available():
                    _counters, _failed = None, True
                    log.warning("Graphics counters are not available on this Windows")
            except Exception as e:
                _failed = True
                log.warning("Graphics counters could not be opened: %s", e)
        return _counters


def reset() -> None:
    """Close the shared query (the next read opens a new one). At exit, and between tests."""
    global _counters, _failed
    with _lock:
        if _counters is not None:
            try:
                _counters.close()
            except Exception:
                pass
        _counters, _failed = None, False


def read_snapshot() -> Optional[Snapshot]:
    c = _get()
    if c is None:
        return None
    try:
        return c.sample()
    except Exception as e:
        log.warning("Graphics counters could not be read: %s", e)
        return None


def read_programs(card_used_mb: Optional[float] = None, card_luid: Optional[int] = None) -> Optional[dict]:
    """Every program on the card the page uses: `{pid: {"dedicated_mb", "shared_mb", "gpu_pct"}}`; None when the counters cannot be read (no Windows, no counters)."""
    snap = read_snapshot()
    if snap is None:
        return None
    return programs_of(snap, match_adapter(snap, card_used_mb, card_luid))


def adapter_used_mb(card_luid: Optional[int]) -> Optional[int]:
    """The memory in use on one adapter (the card's own memory, from 'GPU Adapter Memory'); None when the counters do not say. Lets a card with no maker's tool (AMD, Intel) show how much is used."""
    if card_luid is None:
        return None
    snap = read_snapshot()
    a = snap.adapters.get(card_luid) if snap is not None else None
    return None if not a or a.get("dedicated") is None else int(a["dedicated"] / MIB)


def read_pid(pid: int) -> Optional[dict]:
    """One program's figures on the adapter where it holds the most; None when it is not listed or the counters cannot be read."""
    snap = read_snapshot()
    if snap is None:
        return None
    best = None
    for luid, procs in snap.procs.items():
        p = procs.get(int(pid))
        if p is not None and (best is None or (p.dedicated or 0.0) > (best.dedicated or 0.0)):
            best = p
    if best is None:
        return None
    return {"dedicated_mb": None if best.dedicated is None else int(best.dedicated / MIB), "shared_mb": None if best.shared is None else int(best.shared / MIB), "gpu_pct": best.gpu_pct}


def process_names() -> dict:
    """`{pid: image name}` of every running program, from the Windows process snapshot (the call `tasklist` makes), in this program: nothing is started. {} when it cannot be read."""
    if not _windows():
        return {}
    try:
        import native_probe
        return {pid: name for name, pid in (native_probe.process_list() or [])}
    except Exception:
        return {}
