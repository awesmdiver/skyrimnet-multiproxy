"""Benchmarks protect the PC (step 1): the memory guard, as pure rules.

A test can ask more of the computer than it has (2026-10-28: a 31B model at 8 at once used almost all of a 63 GB PC). This module decides, from one reading a second, whether a
model's test must slow down or stop. It does no I/O of its own except the plain readers at the bottom, and it never touches a process: the runner acts on what it says.

How it decides (all numbers are in `GuardConfig`, the one place to change them; the machine's size sets the memory levels):
- Time is the caller's monotonic clock in seconds, never a count of samples. A reading that is missing, stale or not a number never counts as healthy: it neither starts a recovery nor
  clears a pressure that is already running.
- Available RAM below the WARNING level for 5 s: `hold` (stop admitting new requests, let the ones in flight finish for 15 s). Memory above the warning level plus 1 GiB for 10 s lets it
  go on: ONE such recovery per model, and a second pressure event stops the model. A hold that lasts 60 s past the grace also stops it.
- Available RAM below the STOP level for 5 s: stop the model. Below the EMERGENCY level for 2 s: stop at once (this one is never switched off).
- Windows commit charge above 95% of the commit limit for 10 s while RAM is already below the warning level: stop. (Linux: memory pressure `full avg10` above 10% with RAM below the warning
  level for 10 s, the same idea: it corroborates low memory and never stops anything alone.)
- Spill: the card at or above 98% of its budget AND the engine's own system-RAM use growing by 512 MiB in 10 s AND no request progress for 30 s. Shared graphics memory alone never stops
  a run.
- Not used to stop, ever: working set alone, page-file occupancy, CPU or GPU use, temperature, raw page-fault counts.
With the tick off only the emergency level (and the time limit and everything else outside this module) still applies.
"""
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

MIB = 1024 * 1024


@dataclass
class GuardConfig:
    # memory levels: the larger of a fixed floor and a share of the machine's RAM
    warn_floor_mb: int = 2048
    warn_pct: float = 12.0
    stop_floor_mb: int = 1536
    stop_pct: float = 8.0
    emergency_floor_mb: int = 512
    emergency_pct: float = 2.0
    # how long a level must hold (seconds of the monotonic clock)
    warn_hold_s: float = 5.0
    stop_hold_s: float = 5.0
    emergency_hold_s: float = 2.0
    commit_pct: float = 95.0
    commit_hold_s: float = 10.0
    psi_full_avg10_pct: float = 10.0
    # hold and recovery
    inflight_grace_s: float = 15.0
    recover_extra_mb: int = 1024
    recover_hold_s: float = 10.0
    hold_give_up_s: float = 60.0
    # spill
    spill_budget_pct: float = 98.0
    spill_ws_rise_mb: int = 512
    spill_rise_window_s: float = 10.0
    spill_no_progress_s: float = 30.0
    # a model's whole test
    model_time_limit_s: float = 1800.0
    # readings
    stale_after_s: float = 3.0
    samples_kept_s: float = 30.0
    # between models
    settle_s: float = 3.0
    gate_hold_s: float = 10.0
    gate_timeout_s: float = 90.0
    release_slack_floor_mb: int = 1024
    release_slack_pct: float = 5.0

    def levels(self, total_mb: float) -> dict:
        t = float(total_mb or 0)
        return {"warn": max(float(self.warn_floor_mb), t * self.warn_pct / 100.0), "stop": max(float(self.stop_floor_mb), t * self.stop_pct / 100.0),
                "emergency": max(float(self.emergency_floor_mb), t * self.emergency_pct / 100.0)}

    def as_dict(self) -> dict:
        return dict(self.__dict__)


DEFAULT = GuardConfig()

STATE_OK, STATE_HOLD, STATE_STOP = "ok", "hold", "stop"
# why a model was stopped (the run keeps the code)
R_EMERGENCY, R_LOW, R_REPEATED, R_HELD, R_COMMIT, R_SPILL, R_TOO_LONG = ("memory_emergency", "memory_low", "memory_repeated", "memory_held", "commit_pressure", "memory_spill", "too_long")
MEMORY_REASONS = (R_EMERGENCY, R_LOW, R_REPEATED, R_HELD, R_COMMIT, R_SPILL)


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if v == v and abs(v) != float("inf") else None


class MemoryGuard:
    """One model's guard. `feed(now, reading)` once a second; the answer is `ok`, `hold` or `stop` (`self.stopped` then says why). A reading is a dict:
    `available_mb`, `total_mb`, `commit_used_mb`, `commit_limit_mb`, `psi_full_avg10`, `vram_used_mb`, `vram_total_mb`, `engine_ws_mb`, `progress_age_s` (None for what is not known)."""

    def __init__(self, cfg: GuardConfig = DEFAULT, enabled: bool = True):
        self.cfg, self.enabled = cfg, enabled
        self.state = STATE_OK
        self.stopped: Optional[dict] = None
        self.recoveries = 0
        self.events: list = []                                # what happened, in order (warnings, recoveries, the stop)
        self._since: dict = {}                                # condition -> the time it began to be true without a break
        self._hold_at: Optional[float] = None
        self._recover_since: Optional[float] = None
        self._ws: deque = deque()
        self._recent: deque = deque()                         # (time, reading) of the last `samples_kept_s`
        self._last_good = None
        self.lowest_available_mb: Optional[float] = None

    # -- the answer the runner asks --
    def admitting(self) -> bool:
        return self.state == STATE_OK

    def recent(self) -> list:
        return [dict(r, t=t) for t, r in self._recent]

    def feed(self, now: float, reading: Optional[dict]) -> str:
        if self.stopped:
            return STATE_STOP
        r = dict(reading or {})
        avail, total = _num(r.get("available_mb")), _num(r.get("total_mb"))
        usable = avail is not None and total is not None and total > 0
        self._recent.append((now, dict(r)))
        while self._recent and now - self._recent[0][0] > self.cfg.samples_kept_s:
            self._recent.popleft()
        if not usable:
            self._recover_since = None                        # a missing reading is never "healthy"; a pressure already running is left running
            return self.state
        self._last_good = now
        self.lowest_available_mb = avail if self.lowest_available_mb is None else min(self.lowest_available_mb, avail)
        lv = self.cfg.levels(total)
        c = self.cfg

        def held(name: str, cond: bool) -> float:
            """Seconds this condition has been true without a break (0 when it is not true)."""
            if not cond:
                self._since.pop(name, None)
                return 0.0
            return now - self._since.setdefault(name, now)

        emergency = held("emergency", avail < lv["emergency"])
        if emergency >= c.emergency_hold_s:                  # the floor: never switched off
            return self._stop(now, R_EMERGENCY, r, lv["emergency"], emergency)
        if not self.enabled:
            return self.state
        low_stop = held("stop", avail < lv["stop"])
        if low_stop >= c.stop_hold_s:
            return self._stop(now, R_LOW, r, lv["stop"], low_stop)
        below_warn = avail < lv["warn"]
        commit_used, commit_limit = _num(r.get("commit_used_mb")), _num(r.get("commit_limit_mb"))
        commit = held("commit", below_warn and commit_used is not None and commit_limit is not None and commit_limit > 0 and commit_used > commit_limit * c.commit_pct / 100.0)
        if commit >= c.commit_hold_s:
            return self._stop(now, R_COMMIT, r, lv["warn"], commit)
        psi = _num(r.get("psi_full_avg10"))
        pressure = held("psi", below_warn and psi is not None and psi > c.psi_full_avg10_pct)
        if pressure >= c.commit_hold_s:
            return self._stop(now, R_COMMIT, r, lv["warn"], pressure)
        spill = self._spill(now, r, held)
        if spill is not None:
            return self._stop(now, R_SPILL, r, c.spill_budget_pct, spill)
        warn = held("warn", below_warn)
        if self.state == STATE_OK and warn >= c.warn_hold_s:
            if self.recoveries >= 1:
                return self._stop(now, R_REPEATED, r, lv["warn"], warn)
            self.state, self._hold_at, self._recover_since = STATE_HOLD, now, None
            self.events.append({"event": "memory_warning", "at": now, "available_mb": round(avail), "level_mb": round(lv["warn"]), "held_s": round(warn, 1)})
            return self.state
        if self.state == STATE_HOLD:
            if avail >= lv["warn"] + c.recover_extra_mb:
                self._recover_since = self._recover_since if self._recover_since is not None else now
                if now - self._recover_since >= c.recover_hold_s:
                    self.state, self._hold_at, self._recover_since = STATE_OK, None, None
                    self.recoveries += 1
                    self._since.pop("warn", None)
                    self.events.append({"event": "memory_recovered", "at": now, "available_mb": round(avail)})
            else:
                self._recover_since = None
            if self.state == STATE_HOLD and self._hold_at is not None and now - self._hold_at >= c.inflight_grace_s + c.hold_give_up_s:
                return self._stop(now, R_HELD, r, lv["warn"], now - self._hold_at)
        return self.state

    def _spill(self, now: float, r: dict, held) -> Optional[float]:
        c = self.cfg
        used, total = _num(r.get("vram_used_mb")), _num(r.get("vram_total_mb"))
        ws = _num(r.get("engine_ws_mb"))
        if ws is not None:
            self._ws.append((now, ws))
            while self._ws and now - self._ws[0][0] > c.spill_rise_window_s:
                self._ws.popleft()
        full = used is not None and total is not None and total > 0 and used >= total * c.spill_budget_pct / 100.0
        rise = (ws - min(w for _, w in self._ws)) if ws is not None and self._ws else 0.0
        age = _num(r.get("progress_age_s"))
        stalled = age is not None and age >= c.spill_no_progress_s
        return rise if held("spill", full and rise >= c.spill_ws_rise_mb and stalled) > 0 else None

    def _stop(self, now: float, reason: str, r: dict, threshold: float, duration: float) -> str:
        self.state = STATE_STOP
        self.stopped = {"reason": reason, "threshold": round(threshold, 1), "duration_s": round(duration, 1), "at": now, "reading": {k: v for k, v in r.items()},
                        "samples_before": self.recent(), "events": list(self.events)}
        return self.state


def recovered_for_next_model(reading: Optional[dict], cfg: GuardConfig = DEFAULT, need_vram_mb: Optional[float] = None) -> bool:
    """Is the computer ready for the next model right now? Available RAM above the warning level plus 1 GiB, commit charge acceptable, and (when known) room on the card.
    A missing reading is never ready. The caller needs this to hold for `gate_hold_s` in a row."""
    r = reading or {}
    avail, total = _num(r.get("available_mb")), _num(r.get("total_mb"))
    if avail is None or total is None or total <= 0:
        return False
    if avail < cfg.levels(total)["warn"] + cfg.recover_extra_mb:
        return False
    used, limit = _num(r.get("commit_used_mb")), _num(r.get("commit_limit_mb"))
    if used is not None and limit and used > limit * cfg.commit_pct / 100.0:
        return False
    if need_vram_mb is not None:
        vu, vt = _num(r.get("vram_used_mb")), _num(r.get("vram_total_mb"))
        if vu is not None and vt is not None and vt - vu < need_vram_mb:
            return False
    return True


def memory_not_released(before: Optional[dict], after: Optional[dict], cfg: GuardConfig = DEFAULT) -> Optional[dict]:
    """After a model is unloaded, is the computer's memory in use more than the larger of 1 GB and 5% of the total above what it was before the load? Then the details, else None."""
    b, a = _num((before or {}).get("used_mb")), _num((after or {}).get("used_mb"))
    total = _num((after or {}).get("total_mb")) or _num((before or {}).get("total_mb"))
    if b is None or a is None or not total:
        return None
    slack = max(float(cfg.release_slack_floor_mb), total * cfg.release_slack_pct / 100.0)
    return {"before_mb": round(b), "after_mb": round(a), "extra_mb": round(a - b), "allowed_mb": round(slack)} if a - b > slack else None


# --- the plain readers (no decisions) ---------------------------------------------------------------------------------------------------------------------------------------

def read_memory_detail() -> dict:
    """Now: `total_mb`, `available_mb`, `used_mb`, `commit_used_mb`, `commit_limit_mb` (Windows), `psi_full_avg10` (Linux). What cannot be read is None."""
    out = {"total_mb": None, "available_mb": None, "used_mb": None, "commit_used_mb": None, "commit_limit_mb": None, "psi_full_avg10": None}
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                out.update(total_mb=st.ullTotalPhys // MIB, available_mb=st.ullAvailPhys // MIB, used_mb=(st.ullTotalPhys - st.ullAvailPhys) // MIB)

            class PERFORMANCE_INFORMATION(ctypes.Structure):          # GetPerformanceInfo: the system's commit charge and limit, in pages
                _fields_ = [("cb", ctypes.c_ulong), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                            ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                            ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t), ("HandleCount", ctypes.c_ulong),
                            ("ProcessCount", ctypes.c_ulong), ("ThreadCount", ctypes.c_ulong)]
            pi = PERFORMANCE_INFORMATION()
            pi.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
            if ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
                out.update(commit_used_mb=pi.CommitTotal * pi.PageSize // MIB, commit_limit_mb=pi.CommitLimit * pi.PageSize // MIB)
        else:
            info = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    parts = v.split()
                    if parts and parts[0].isdigit():
                        info[k] = int(parts[0])
            if "MemTotal" in info and "MemAvailable" in info:
                out.update(total_mb=info["MemTotal"] // 1024, available_mb=info["MemAvailable"] // 1024, used_mb=(info["MemTotal"] - info["MemAvailable"]) // 1024)
            try:
                with open("/proc/pressure/memory", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("full"):
                            for part in line.split():
                                if part.startswith("avg10="):
                                    out["psi_full_avg10"] = float(part[6:])
            except OSError:
                pass
    except Exception:
        pass
    return out


def read_process_working_set_mb(pid: int) -> Optional[float]:
    """The system RAM one process holds now (Windows: its working set; Linux: VmRSS). None when it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class COUNTERS(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            h = k32.OpenProcess(0x1000 | 0x0010, False, int(pid))          # QUERY_LIMITED_INFORMATION | VM_READ
            if not h:
                return None
            try:
                c = COUNTERS()
                c.cb = ctypes.sizeof(COUNTERS)
                return c.WorkingSetSize / MIB if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb) else None
            finally:
                k32.CloseHandle(h)
        with open(f"/proc/{int(pid)}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        return None
    return None
