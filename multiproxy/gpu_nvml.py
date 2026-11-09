"""The NVIDIA card, read through NVIDIA's own driver library (NVML: `nvml.dll` on Windows, `libnvidia-ml.so.1` on Linux) instead of starting `nvidia-smi` for every reading.

It is the same data: `answer(cmd)` takes the `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` command that WOULD have been run and returns exactly the text it would have
printed (same columns, same units, `[N/A]` for a figure the card does not give), so every parser and every page stays as it was. When it cannot answer (no library, a field it does
not know, any fault) it returns None and the caller runs the real `nvidia-smi` as before. If the library fails while a run is going, it is switched off for the rest of that run
(one log line) and the real program takes over. No program is started on this path.

What the NVML calls are, and the rules for a card that goes away mid-run, follow `src/gputaskman/nvml.py` of gputaskman (MIT, Scott Hanselman; that file uses the `pynvml` package,
this one uses ctypes directly): the table of clock-throttle reason bits (`THROTTLE`), the error codes that mean "start the library again / the card is gone" (`RESET_ERRORS`), the
rule that a fault inside the library (an access violation, which ctypes raises as OSError) ends all reading of it for the rest of the process and that `nvmlShutdown` is never
called after one (a hang was reported there), and the reading of the device's name, memory, utilisation, temperature, power, clocks, fan speed and throttle reasons. The licence
notice is kept:

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
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import types
from typing import Callable, Optional

log = logging.getLogger("multiproxy.gpu_nvml")

MIB = 1024 * 1024
THROTTLE = {0x2: "app-clocks", 0x4: "sw-power-cap", 0x8: "hw-slowdown", 0x20: "sw-thermal", 0x40: "hw-thermal", 0x80: "power-brake"}
RESET_ERRORS = (1, 9, 15)                      # uninitialized, driver not loaded, GPU lost
NVML_CLOCK_SM = 1
NVML_TEMPERATURE_GPU = 0
NA = "[N/A]"

FIELDS = {"index", "name", "uuid", "memory.used", "memory.total", "memory.free", "utilization.gpu", "temperature.gpu", "temperature.gpu.tlimit", "power.draw", "power.limit",
          "clocks.sm", "fan.speed", "driver_version"}


class Lost(Exception):
    """The library cannot be trusted any more (the card went away, a fault inside it): stop using it."""


class NvmlLib:
    """The few NVML calls we need, as Python values. A figure the card does not give is None. Anything that means the library is broken raises `Lost`."""

    def __init__(self, dll):
        self._d = dll
        self.driver_version()                    # fails early if the driver is not really there

    @staticmethod
    def open() -> "NvmlLib":
        names = ("nvml.dll", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll") if sys.platform == "win32" else ("libnvidia-ml.so.1", "libnvidia-ml.so")
        last: Optional[Exception] = None
        for n in names:
            try:
                dll = ctypes.WinDLL(n) if sys.platform == "win32" else ctypes.CDLL(n)
                if dll.nvmlInit_v2() == 0:
                    return NvmlLib(dll)
            except (OSError, AttributeError) as e:
                last = e
        raise Lost(f"the NVIDIA driver library could not be opened: {last}")

    def _call(self, fn: str, *args) -> Optional[int]:
        try:
            code = getattr(self._d, fn)(*args)
        except AttributeError:
            return 3                              # this library does not have that call: "not supported"
        except OSError as e:                      # a fault inside the library: never read it again
            raise Lost(f"{fn} faulted: {e}")
        if code in RESET_ERRORS:
            raise Lost(f"{fn} returned {code}")
        return code

    def _uint(self, fn: str, *lead) -> Optional[int]:
        v = ctypes.c_uint()
        return v.value if self._call(fn, *lead, ctypes.byref(v)) == 0 else None

    def _text(self, fn: str, *lead, size: int = 96) -> Optional[str]:
        buf = ctypes.create_string_buffer(size)
        return buf.value.decode("utf-8", "replace") if self._call(fn, *lead, buf, size) == 0 else None

    def count(self) -> int:
        n = self._uint("nvmlDeviceGetCount_v2")
        if n is None:
            raise Lost("the card count could not be read")
        return n

    def handle(self, i: int):
        h = ctypes.c_void_p()
        if self._call("nvmlDeviceGetHandleByIndex_v2", i, ctypes.byref(h)) != 0:
            raise Lost(f"card {i} has no handle")
        return h

    def driver_version(self) -> Optional[str]:
        v = self._text("nvmlSystemGetDriverVersion", size=80)
        if v is None:
            raise Lost("the driver version could not be read")
        return v

    def name(self, h): return self._text("nvmlDeviceGetName", h)
    def uuid(self, h): return self._text("nvmlDeviceGetUUID", h)

    def memory(self, h):
        class Mem(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]
        m = Mem()
        return (m.total, m.free, m.used) if self._call("nvmlDeviceGetMemoryInfo", h, ctypes.byref(m)) == 0 else None

    def utilization(self, h):
        class Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]
        u = Util()
        return u.gpu if self._call("nvmlDeviceGetUtilizationRates", h, ctypes.byref(u)) == 0 else None

    def temperature(self, h): return self._uint("nvmlDeviceGetTemperature", h, NVML_TEMPERATURE_GPU)
    def power_mw(self, h): return self._uint("nvmlDeviceGetPowerUsage", h)
    def power_limit_mw(self, h): return self._uint("nvmlDeviceGetPowerManagementLimit", h)
    def clock_sm(self, h): return self._uint("nvmlDeviceGetClockInfo", h, NVML_CLOCK_SM)
    def fan(self, h): return self._uint("nvmlDeviceGetFanSpeed", h)

    def margin_c(self, h) -> Optional[int]:
        """Degrees left before the card's own thermal limit (`temperature.gpu.tlimit`). Newer drivers only; None when this driver does not have it."""
        class Margin(ctypes.Structure):
            _fields_ = [("version", ctypes.c_uint), ("marginTemperature", ctypes.c_int)]
        m = Margin()
        m.version = ctypes.sizeof(Margin) | (1 << 24)
        return m.marginTemperature if self._call("nvmlDeviceGetMarginTemperature", h, ctypes.byref(m)) == 0 else None

    def throttle_reasons(self, h) -> Optional[int]:
        v = ctypes.c_ulonglong()
        for fn in ("nvmlDeviceGetCurrentClocksEventReasons", "nvmlDeviceGetCurrentClocksThrottleReasons"):
            if self._call(fn, h, ctypes.byref(v)) == 0:
                return v.value
        return None


def throttle_names(bits: Optional[int]) -> list:
    """The clock-throttle reasons set in `bits`, by name."""
    return [n for bit, n in THROTTLE.items() if bits and bits & bit]


# --- the text nvidia-smi would print (pure over the library object, so tests use a fake one) ---------------------------------------------------------------------------------

def _fmt(value, spec: str = "") -> str:
    return NA if value is None else (format(value, spec) if spec else str(value))


def format_query(lib, fields: list) -> Optional[str]:
    """One line per card, the fields in order, as `--format=csv,noheader,nounits` prints them. None when a field is not one we can give (the real program answers then)."""
    if not fields or any(f not in FIELDS for f in fields):
        return None
    lines = []
    version = lib.driver_version() if "driver_version" in fields else None
    for i in range(lib.count()):
        h = lib.handle(i)
        mem = lib.memory(h) if any(f.startswith("memory.") for f in fields) else None
        row = []
        for f in fields:
            if f == "index": v = str(i)
            elif f == "name": v = _fmt(lib.name(h))
            elif f == "uuid": v = _fmt(lib.uuid(h))
            elif f == "driver_version": v = _fmt(version)
            elif f == "memory.total": v = _fmt(None if mem is None else mem[0] // MIB)
            elif f == "memory.free": v = _fmt(None if mem is None else mem[1] // MIB)
            elif f == "memory.used": v = _fmt(None if mem is None else mem[2] // MIB)
            elif f == "utilization.gpu": v = _fmt(lib.utilization(h))
            elif f == "temperature.gpu": v = _fmt(lib.temperature(h))
            elif f == "temperature.gpu.tlimit":
                m = lib.margin_c(h)
                if m is None:
                    return None                    # this driver has no live margin: nvidia-smi decides what to print (an older one refuses the field, and the sampler copes)
                v = str(m)
            elif f == "power.draw":
                p = lib.power_mw(h)
                v = _fmt(None if p is None else p / 1000.0, ".2f")
            elif f == "power.limit":
                p = lib.power_limit_mw(h)
                v = _fmt(None if p is None else p / 1000.0, ".2f")
            elif f == "clocks.sm": v = _fmt(lib.clock_sm(h))
            else: v = _fmt(lib.fan(h))             # fan.speed
            row.append(v)
        lines.append(", ".join(row))
    return "\n".join(lines) + ("\n" if lines else "")


def parse_command(cmd: list) -> Optional[list]:
    """The fields of an `nvidia-smi --query-gpu=a,b,c --format=csv,noheader,nounits` command; None for any other command (including a `-i` card choice)."""
    if not cmd or str(cmd[0]).lower() != "nvidia-smi" or len(cmd) != 3 or "--format=csv,noheader,nounits" not in cmd:
        return None
    q = next((a for a in cmd[1:] if a.startswith("--query-gpu=")), None)
    return q.split("=", 1)[1].split(",") if q else None


# --- the shared library, switched off for good when it fails --------------------------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_lib = None
_off = False
_opener: Callable[[], object] = NvmlLib.open                 # tests replace this


def reset() -> None:
    """Allow the library to be tried again (a new run). The library itself is not shut down: a shutdown after a fault can hang."""
    global _lib, _off
    with _lock:
        _lib, _off = None, False


def available() -> bool:
    return not _off


def answer(cmd: list) -> Optional[str]:
    """The text `cmd` would print, read through NVML; None when this cannot answer it (the caller runs the real `nvidia-smi`)."""
    global _lib, _off
    fields = parse_command(cmd)
    if fields is None or _off or not any(f in FIELDS for f in fields):
        return None
    with _lock:
        if _off:
            return None
        try:
            if _lib is None:
                _lib = _opener()
                log.info("NVIDIA card is read through the driver library (no nvidia-smi started)")
            return format_query(_lib, fields)
        except Exception as e:                     # Lost, or anything unexpected: the real program takes over for the rest of the run
            _off = True
            log.warning("NVIDIA driver library failed (%s); nvidia-smi is used from now on", e)
            return None


def run(cmd: list, **kw):
    """`hidden_process.run` for callers that take a finished process: the NVML text for an `nvidia-smi` query it can answer, else the real program."""
    ans = answer(cmd)
    if ans is not None:
        return types.SimpleNamespace(returncode=0, stdout=ans, stderr="")
    import hidden_process
    return hidden_process.run(cmd, **kw)
