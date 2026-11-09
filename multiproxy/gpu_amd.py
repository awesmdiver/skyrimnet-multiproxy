"""AMD card readings (Windows): utilisation, clocks, power and temperature, through AMD's own driver library (ADL, `atiadlxx.dll` / `atiadlxy.dll`, shipped with the AMD driver).

NEW AND UNVERIFIED: this was written without an AMD card to try it on. Every reading the driver does not give is simply absent (None), a missing library means no AMD readings
(never an error), and no call here ever raises into the sampler. Read calls only: nothing here sets a fan, a clock or any other card setting.

How an ADL adapter is tied to a card from the DXGI list (`gpu_adapters`): by the card's PCI bus, device and function. DXGI's list gives them through the graphics kernel
(`gpu_adapters.Adapter.pci`); ADL's adapter table gives the same three numbers. Only an unambiguous match counts: if two cards share the numbers, or none matches, that card gives no
AMD readings. ADL may list several logical handles for one card; the lowest index is used.

The ADL plumbing below (the `_AdapterInfo`, `_Sensor` and `_Metrics` structures, the memory callback through the C runtime's `malloc`, the create / adapter-count / adapter-info /
`ADL2_New_QueryPMLogData_Get` call sequence, the matching of an adapter by bus, device and function, and the sensor numbers 1 core clock, 2 memory clock, 8 temperature,
19 utilisation, 23 chip power, 73 board power) is ported from `src/gputaskman/adl.py` of gputaskman, with the licence notice kept:

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

The rest (the seams for tests, the rows for the sampler, the one log line per card, the driver version read) is our own. `ADL2_New_QueryPMLogData_Get` is deprecated by AMD; a
failure just leaves the readings out.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from typing import Callable, Optional

log = logging.getLogger("multiproxy.gpu_amd")

SENSOR_CLOCK, SENSOR_MEM_CLOCK, SENSOR_TEMP, SENSOR_UTIL, SENSOR_ASIC_POWER, SENSOR_BOARD_POWER = 1, 2, 8, 19, 23, 73
LIBRARY_NAMES = ("atiadlxx.dll", "atiadlxy.dll")


class _AdapterInfo(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("index", ctypes.c_int), ("udid", ctypes.c_char * 256),
                ("bus", ctypes.c_int), ("device", ctypes.c_int), ("function", ctypes.c_int),
                ("vendor", ctypes.c_int), ("name", ctypes.c_char * 256),
                ("display", ctypes.c_char * 256), ("present", ctypes.c_int), ("exists", ctypes.c_int),
                ("driver", ctypes.c_char * 256), ("driver_ext", ctypes.c_char * 256),
                ("pnp", ctypes.c_char * 256), ("os_index", ctypes.c_int)]


class _Sensor(ctypes.Structure):
    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _Metrics(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("sensors", _Sensor * 256)]


class AdlError(Exception):
    pass


class AdlLibrary:
    """One open ADL context. `dll` is the loaded library (or, in tests, a stand-in with the same `ADL2_*` calls); `malloc` is the memory callback ADL asks for."""

    def __init__(self, dll, malloc):
        self._d = dll
        self._ctx = ctypes.c_void_p()
        if dll.ADL2_Main_Control_Create(malloc, 1, ctypes.byref(self._ctx)) != 0:
            raise AdlError("the AMD library would not start")

    @staticmethod
    def open() -> "AdlLibrary":
        """Load the real library (the AMD driver puts it in System32). Raises when there is none."""
        last: Optional[Exception] = None
        for name in LIBRARY_NAMES:
            try:
                dll = ctypes.CDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name))        # the driver's own location, never the working folder
            except OSError as e:
                last = e
                continue
            crt = ctypes.CDLL("msvcrt")
            crt.malloc.argtypes = [ctypes.c_size_t]
            crt.malloc.restype = ctypes.c_void_p
            malloc = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)(crt.malloc)
            dll.ADL2_Main_Control_Create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
            dll.ADL2_Main_Control_Destroy.argtypes = [ctypes.c_void_p]
            dll.ADL2_Adapter_NumberOfAdapters_Get.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            dll.ADL2_Adapter_AdapterInfo_Get.argtypes = [ctypes.c_void_p, ctypes.POINTER(_AdapterInfo), ctypes.c_int]
            dll.ADL2_New_QueryPMLogData_Get.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(_Metrics)]
            lib = AdlLibrary(dll, malloc)
            lib._malloc = malloc                                  # keep the callback alive as long as the library
            return lib
        raise AdlError(f"no AMD library: {last}")

    def adapters(self) -> list:
        """`[{"index", "bus", "device", "function", "name"}]` for every adapter ADL lists."""
        count = ctypes.c_int()
        if self._d.ADL2_Adapter_NumberOfAdapters_Get(self._ctx, ctypes.byref(count)) != 0 or not 0 < count.value <= 256:
            return []
        entries = (_AdapterInfo * count.value)()
        for e in entries:
            e.size = ctypes.sizeof(_AdapterInfo)
        if self._d.ADL2_Adapter_AdapterInfo_Get(self._ctx, entries, ctypes.sizeof(entries)) != 0:
            return []
        return [{"index": e.index, "bus": e.bus, "device": e.device, "function": e.function, "name": e.name.decode("utf-8", "replace")} for e in entries]

    def sensors(self, index: int) -> dict:
        """`{sensor number: value}` for every sensor the driver says it supports with a non-negative value; {} when the call fails (a failed call is not an error here)."""
        m = _Metrics()
        m.size = ctypes.sizeof(m)
        try:
            status = self._d.ADL2_New_QueryPMLogData_Get(self._ctx, index, ctypes.byref(m))
        except (OSError, AttributeError):
            return {}
        if status != 0:
            return {}
        return {i: s.value for i, s in enumerate(m.sensors) if s.supported and s.value >= 0}

    def version(self) -> Optional[str]:
        """The AMD driver's version text, when the library has the call and answers; None otherwise."""
        fn = getattr(self._d, "ADL2_Graphics_Versions_Get", None)
        if fn is None:
            return None
        buf = ctypes.create_string_buffer(4096)                  # ADL's versions record is three 256-byte texts; the driver version is the first
        try:
            if fn(self._ctx, buf) != 0:
                return None
        except (OSError, ValueError, ctypes.ArgumentError):
            return None
        text = buf.raw[:256].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        return text or None


def readings_from_sensors(s: dict) -> dict:
    """The sampler's field names from ADL sensor numbers. A sensor the driver did not give is None. Power is the board's figure when there is one, else the chip's."""
    power = s.get(SENSOR_BOARD_POWER, s.get(SENSOR_ASIC_POWER))
    return {"gpu_util_pct": s.get(SENSOR_UTIL), "temp_c": s.get(SENSOR_TEMP), "power_w": power, "clock_mhz": s.get(SENSOR_CLOCK), "mem_clock_mhz": s.get(SENSOR_MEM_CLOCK)}


def match_index(infos: list, pci: Optional[tuple]) -> Optional[int]:
    """The ADL adapter index for a card with this PCI (bus, device, function): the lowest index among the entries that carry those numbers; None for no PCI address or no match."""
    if not pci:
        return None
    hits = [i["index"] for i in infos if (i["bus"], i["device"], i["function"]) == tuple(pci)]
    return min(hits) if hits else None


# --- the shared library and the rows for the sampler -------------------------------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_lib: Optional[AdlLibrary] = None
_failed = False
_noted: set = set()
_opener: Callable[[], AdlLibrary] = AdlLibrary.open               # tests replace this


def reset() -> None:
    global _lib, _failed
    with _lock:
        _lib, _failed = None, False
        _noted.clear()


def _library() -> Optional[AdlLibrary]:
    global _lib, _failed
    with _lock:
        if _lib is None and not _failed:
            try:
                _lib = _opener()
            except Exception as e:
                _failed = True
                log.info("No AMD driver library on this PC (%s): no AMD readings", e)
        return _lib


def _cards() -> list:
    """The AMD cards that are real cards (not a processor's built-in graphics) from the card list, with a PCI address."""
    if sys.platform != "win32" and not _force_windows:
        return []
    try:
        import gpu_adapters
        import gpu_choice
        out = []
        for a in gpu_adapters.list_adapters():
            if a.vendor != "amd" or a.pci is None:
                continue
            if gpu_choice.is_integrated(gpu_choice.Card(a.stable_id, a.name, a.memory_mb, "amd")):
                continue
            out.append(a)
        return out
    except Exception:
        return []


_force_windows = False                                           # tests


def _yes(v) -> str:
    return "yes" if v is not None else "no"


def read_rows() -> list:
    """One row per AMD card, in the sampler's field names (`name`, `vram_used_mb`, `vram_total_mb`, `gpu_util_pct`, `temp_c`, `power_w`, `clock_mhz`, plus `mem_clock_mhz`); a figure the
    driver does not give is None. [] when there is no AMD card, no library, or anything goes wrong. Never raises."""
    try:
        cards = _cards()
        if not cards:
            return []
        lib = _library()
        if lib is None:
            return []
        infos = lib.adapters()
        rows = []
        for a in cards:
            if sum(1 for b in cards if b.pci == a.pci) != 1:
                continue                                             # two cards with the same PCI numbers: not certain which is which
            idx = match_index(infos, a.pci)
            if idx is None:
                continue
            r = readings_from_sensors(lib.sensors(idx))
            used = None
            if a.luid is not None:
                try:
                    import gpu_counters
                    used = gpu_counters.adapter_used_mb(a.luid)
                except Exception:
                    used = None
            rows.append(dict(name=a.name, vram_used_mb=used, vram_total_mb=a.memory_mb, power_limit_w=None, fan_pct=None, t_limit_margin_c=None, **r))
            if a.stable_id not in _noted:
                _noted.add(a.stable_id)
                log.info("AMD card in use (new, not fully tested): %s; driver library %s; readings: utilisation %s, clocks %s, power %s, temperature %s", a.name,
                         lib.version() or "version unknown", _yes(r["gpu_util_pct"]), _yes(r["clock_mhz"] if r["clock_mhz"] is not None else r["mem_clock_mhz"]), _yes(r["power_w"]),
                         _yes(r["temp_c"]))
        return rows
    except Exception as e:
        log.warning("AMD readings failed: %s", e)
        return []
