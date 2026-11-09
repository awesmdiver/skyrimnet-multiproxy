"""Benchmarks: the readings sampler (contract section 5, and the heat and spill parts of section 8).

A test run starts one `ReadingsSampler`. Once a second it reads every graphics card with ONE `nvidia-smi --query-gpu=... --format=csv` call (all fields at once) and the computer's
processor and memory use, hands each reading to a sink callback (`on_sample(dict)`: this module writes no files) and keeps the highest and lowest of each reading. Nothing here
guesses: a field the card cannot give is `None`, with the reason recorded once per run (`reasons()`), never a zero or a made-up figure.

Words used on purpose: the highest reading is a **sampled peak** (the highest of once-a-second samples, so a short spike between two samples can be missed); the names say
`highest_sampled` / `lowest_sampled`. `vram_used_mb` is the whole card's use, other programs included.

Where the readings come from (no second reader is run):
- Graphics cards, NVIDIA: `nvidia-smi` (one call a second, for all cards and all fields). On Windows this goes through `hidden_process`, so no console window flashes up. The in-process
  NVML shortcut of `native_probe` only answers a smaller field list, so this wider query is run as the real program. The engine's own memory per process uses
  `gpu_monitor.parse_nvidia_compute_apps` / `parse_typeperf_gpu_process_memory`.
- Processor use and memory use: the standard library only. Windows: `GetSystemTimes` and, for memory, `courier_system.read_pc_memory` (`GlobalMemoryStatusEx`). Linux: `/proc/stat`
  and `/proc/meminfo`.
- Cards that are not NVIDIA: only what the machine itself reports; this reader has no AMD or Intel path, so those card fields are `not measured` with the reason "needs an NVIDIA
  card". (Not verified on any non-NVIDIA hardware: none is available here.)

Runs in its own daemon thread, never on the proxy's event loop, with a time limit on every call. After 5 failed calls in a row it stops asking. It never kills a process it did not
start (the only process it starts is a one-second `nvidia-smi` that `subprocess.run` itself ends on a time-out).
"""
import csv
import io
import re
import sys
import threading
import time
from typing import Callable, Optional

import benchmarks_cardusage
import benchmarks_guard
import gpu_amd
import gpu_counters
import gpu_nvml
import gpu_monitor
import hidden_process

INTERVAL_S = 1.0
CALL_TIMEOUT_S = 4.0
MAX_FAILURES_IN_A_ROW = 5
SHARED_RISE_MB = 256                                      # shared GPU memory of the engine's process must rise by this much to count as spill evidence
RAM_RISE_NOTE_MB = 512                                    # a system RAM rise this big is mentioned as an observation (never as evidence)

GPU_FIELDS = ("vram_used_mb", "vram_total_mb", "gpu_util_pct", "temp_c", "power_w", "power_limit_w", "clock_mhz", "fan_pct", "t_limit_margin_c")
MACHINE_FIELDS = ("cpu_util_pct", "ram_used_mb", "ram_total_mb")
MEMORY_EXTRA_FIELDS = ("ram_available_mb", "commit_used_mb", "commit_limit_mb", "psi_full_avg10")      # for the memory guard: written with every machine row, not part of the availability list
_QUERY_FIELDS = ("index", "name", "memory.used", "memory.total", "utilization.gpu", "temperature.gpu", "power.draw", "power.limit", "clocks.sm", "fan.speed")
SMI_QUERY = ["nvidia-smi", "--query-gpu=" + ",".join(_QUERY_FIELDS), "--format=csv,noheader,nounits"]
# `temperature.gpu.tlimit` is the live margin: degrees left before the card's own thermal limit (shrinks as it heats). Older drivers do not have the field and refuse the whole
# query, so the first pass tries it and falls back to `SMI_QUERY` (and the `-q` text) when nvidia-smi says no.
SMI_QUERY_MARGIN = ["nvidia-smi", "--query-gpu=" + ",".join(_QUERY_FIELDS + ("temperature.gpu.tlimit",)), "--format=csv,noheader,nounits"]
Q_MARGIN_EVERY = 5                                        # without the column, the margin is read from the `-q` text every this many passes
SMI_THERMAL = ["nvidia-smi", "-q", "-d", "TEMPERATURE"]

REASON_NOT_REPORTED = "not reported by this card"
REASON_NEEDS_NVIDIA = "needs an NVIDIA card"
REASON_UNREADABLE = "could not be read"
REASON_GAVE_UP = f"the card reader failed {MAX_FAILURES_IN_A_ROW} times in a row"
PHASE_START = "start"

Runner = Callable[[list, float], tuple]                   # (command, time limit in seconds) -> (status, text); status: ok | missing | timeout | error


def run_command(cmd: list, timeout: float) -> tuple:
    """The default runner: an `nvidia-smi` query is answered through the driver library when it can be (no program started); anything else is the real program, hidden window, a time
    limit. `subprocess.run` ends the program itself when the limit passes."""
    import subprocess
    ans = gpu_nvml.answer(cmd)
    if ans is not None:
        return "ok", ans
    try:
        r = hidden_process.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "missing", ""
    except subprocess.TimeoutExpired:
        return "timeout", ""
    except Exception:
        return "error", ""
    return ("ok", r.stdout) if r.returncode == 0 else ("error", "")


# --- parsing (pure) ---------------------------------------------------------------------------------------------------------------------------------------------------------

def _num(text: str) -> Optional[float]:
    """A figure as nvidia-smi prints it, or None for `[N/A]`, `N/A`, `[Not Supported]`, empty or anything not a number."""
    t = (text or "").strip()
    if not t or t.startswith("[") or t.upper() == "N/A":
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None


_FIELD_FOR_COLUMN = (("vram_used_mb", 2), ("vram_total_mb", 3), ("gpu_util_pct", 4), ("temp_c", 5), ("power_w", 6), ("power_limit_w", 7), ("clock_mhz", 8), ("fan_pct", 9))


def parse_gpu_rows(text: str) -> list:
    """`SMI_QUERY` output -> one dict per card: `gpu_index`, `name` and the eight readings (a number or None). A line that has the wrong number of columns is skipped."""
    out = []
    for cols in csv.reader(io.StringIO(text or ""), skipinitialspace=True):
        if len(cols) not in (len(_QUERY_FIELDS), len(_QUERY_FIELDS) + 1):
            continue
        idx = _num(cols[0])
        if idx is None:
            continue
        row = {"gpu_index": int(idx), "name": cols[1].strip()}
        for field, col in _FIELD_FOR_COLUMN:
            row[field] = _num(cols[col])
        row["t_limit_margin_c"] = _num(cols[10]) if len(cols) == len(_QUERY_FIELDS) + 1 else None
        out.append(row)
    return out


def parse_thermal_limits(text: str) -> dict:
    """`nvidia-smi -q -d TEMPERATURE` -> {gpu_index: {...}}. Cards are the `GPU <bus id>` blocks in order.

    Absolute thresholds (older cards): `slowdown_c`, `shutdown_c`, and `thermal_limit_c` = the lower of them (None when neither). Margin readings (newer cards, for example an RTX 5090,
    which print degrees of headroom instead): `current_margin_c` (`GPU Current T.Limit Temp`, shrinks as the card heats), and the card's own thresholds written as margins:
    `max_operating_margin_c`, `slowdown_margin_c`, `shutdown_margin_c`. A margin is never turned into an absolute temperature."""
    out: dict = {}
    idx = -1
    blank = {"slowdown_c": None, "shutdown_c": None, "current_margin_c": None, "max_operating_margin_c": None, "slowdown_margin_c": None, "shutdown_margin_c": None}
    absolute = {"GPU Slowdown Temp": "slowdown_c", "GPU Shutdown Temp": "shutdown_c"}
    margins = {"GPU Current T.Limit Temp": "current_margin_c", "GPU Max Operating T.Limit Temp Specification": "max_operating_margin_c",
               "GPU Slowdown T.Limit Temp Specification": "slowdown_margin_c", "GPU Shutdown T.Limit Temp Specification": "shutdown_margin_c"}
    for line in (text or "").splitlines():
        if re.match(r"^GPU\s+\S+", line):
            idx += 1
            out[idx] = dict(blank)
            continue
        m = re.match(r"^\s*(.+?)\s*:\s*(.+?)\s*$", line)
        if m and idx >= 0 and (m.group(1) in absolute or m.group(1) in margins):
            parts = m.group(2).split()
            out[idx][absolute.get(m.group(1)) or margins[m.group(1)]] = _num(parts[0]) if parts else None
    for info in out.values():
        known = [v for v in (info["slowdown_c"], info["shutdown_c"]) if v is not None]
        info["thermal_limit_c"] = min(known) if known else None
    return out


def parse_cpu_times_windows(idle: int, kernel: int, user: int) -> tuple:
    """GetSystemTimes: kernel time includes idle time. -> (idle, total)."""
    return idle, kernel + user


def parse_proc_stat(text: str) -> Optional[tuple]:
    """The first `cpu` line of `/proc/stat` -> (idle, total) in clock ticks (idle + iowait count as idle)."""
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            parts = [int(p) for p in line.split()[1:] if p.isdigit()]
            if len(parts) >= 4:
                idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
                return idle, sum(parts[:8])
    return None


def cpu_util_between(before: Optional[tuple], after: Optional[tuple]) -> Optional[float]:
    """Percent busy between two (idle, total) snapshots; None when there is no earlier snapshot or no time passed."""
    if not before or not after:
        return None
    total = after[1] - before[1]
    if total <= 0:
        return None
    return max(0.0, min(100.0, (1 - (after[0] - before[0]) / total) * 100.0))


def read_cpu_times() -> Optional[tuple]:
    """(idle, total) now, system-wide, standard library only."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
            val = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            return parse_cpu_times_windows(val(idle), val(kern), val(user))
        with open("/proc/stat", "r", encoding="utf-8") as f:
            return parse_proc_stat(f.read())
    except Exception:
        return None


def read_ram_mb() -> tuple:
    """(used, total) system RAM in MB, or (None, None)."""
    try:
        import courier_system
        mem = courier_system.read_pc_memory()
        return (int(mem["used_mb"]), int(mem["total_mb"])) if mem else (None, None)
    except Exception:
        return None, None


def read_ram_used_mb() -> Optional[int]:
    return read_ram_mb()[0]


# --- spill evidence (pure) --------------------------------------------------------------------------------------------------------------------------------------------------

def spill_evidence(layers_on_card: Optional[int] = None, layers_requested: Optional[int] = None, shared_mb_readings: Optional[list] = None,
                   ram_rose_mb: Optional[float] = None) -> dict:
    """`{"spill": "detected" | "not_detected" | "unknown", "evidence": text, "observation": text or None}`.

    Detected only with evidence: (a) the engine reports fewer layers on the card than were requested, or (b) the shared GPU memory counter of the engine's process (`shared_mb_readings`,
    oldest first) rose by `SHARED_RISE_MB` or more. A rise of system RAM alone is never evidence: it comes back as an observation with `unknown`. Not detected needs a source that
    could have shown it: all layers on the card, or a shared-memory counter that did not rise."""
    notes = []
    if layers_on_card is not None and layers_requested is not None and layers_requested > 0 and layers_on_card < layers_requested:
        notes.append(f"The engine reports {layers_on_card} of {layers_requested} layers on the card.")
    shared = [v for v in (shared_mb_readings or []) if v is not None]
    rise = (shared[-1] - shared[0]) if len(shared) >= 2 else None
    if rise is not None and rise >= SHARED_RISE_MB:
        notes.append(f"Shared GPU memory used by the engine rose by {int(round(rise))} MB.")
    if notes:
        return {"spill": "detected", "evidence": " ".join(notes), "observation": None}
    layers_full = layers_on_card is not None and layers_requested is not None and layers_requested > 0 and layers_on_card >= layers_requested
    shared_flat = rise is not None
    if layers_full or shared_flat:
        what = [t for t, ok in (("all requested layers are on the card", layers_full), ("shared GPU memory did not rise", shared_flat)) if ok]
        return {"spill": "not_detected", "evidence": ("; ".join(what)[:1].upper() + "; ".join(what)[1:]) + ".", "observation": None}
    obs = None
    if ram_rose_mb is not None and ram_rose_mb >= RAM_RISE_NOTE_MB:
        obs = f"System RAM rose by {int(round(ram_rose_mb))} MB during the run."
    return {"spill": "unknown", "evidence": "", "observation": obs}


# --- the sampler ------------------------------------------------------------------------------------------------------------------------------------------------------------

class ReadingsSampler:
    """One sample per second per GPU plus one machine line, to `on_sample`. Create it, `start()`, `mark(phase)` as the run moves on, `stop()`.

    `clock` and `runner` and `machine_reader` can be replaced (tests do); `ceiling_c` is the optional heat ceiling from a setting (None = off); `tested_gpus` limits the heat check to
    those card indexes (None = every card)."""

    def __init__(self, on_sample: Optional[Callable[[dict], None]] = None, clock: Callable[[], float] = time.monotonic, runner: Runner = run_command,
                 machine_reader: Optional[Callable[[], dict]] = None, interval_s: float = INTERVAL_S, call_timeout_s: float = CALL_TIMEOUT_S,
                 ceiling_c: Optional[float] = None, tested_gpus: Optional[set] = None, counters: Optional[Callable[[int], Optional[dict]]] = None, amd_reader: Optional[Callable[[], list]] = None):
        self._sink, self._clock, self._runner = on_sample, clock, runner
        self._amd_reader = amd_reader or gpu_amd.read_rows           # AMD cards (new, unverified): rows in the same field names; [] when there is no AMD card or no library
        self._counters = counters or gpu_counters.read_pid          # pid -> {"dedicated_mb", "shared_mb", "gpu_pct"} or None
        self._machine_reader = machine_reader
        self._interval, self._timeout = interval_s, call_timeout_s
        self._ceiling_c, self._tested = ceiling_c, tested_gpus
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started = False
        self._t0 = 0.0
        self._next_due = 0.0
        self._phase = PHASE_START
        self._failures = 0
        self._gave_up = ""                                 # the reason once the card reader stops being asked
        self._latest: dict = {}                            # gpu_index -> last row; "machine" -> last machine row
        self._limits: dict = {}                            # gpu_index -> parse_thermal_limits entry
        self._query = SMI_QUERY_MARGIN                     # the card query in use (falls back to SMI_QUERY when the driver has no margin column)
        self._margin_column = False                        # True once the margin query has answered
        self._high: dict = {}                              # (who, field) -> highest; who = gpu_index or "machine"
        self._low: dict = {}
        self._phase_high: dict = {}                        # (phase, who, field) -> highest
        self._names: dict = {}
        self._reasons: dict = {}
        self._cpu_prev: Optional[tuple] = None
        self._shared: list = []
        self._ram_first: Optional[float] = None
        self._ram_last: Optional[float] = None
        self._min_available: Optional[float] = None
        self.samples_taken = 0

    # -- lifecycle --
    def start(self) -> bool:
        """Start reading in a background thread. A second call while running is ignored (returns False)."""
        with self._lock:
            if self._started:
                return False
            self._started = True
            self._stop.clear()
            gpu_nvml.reset()                               # a new run tries the driver library again (it is switched off for the rest of a run that failed)
            self._t0 = self._clock()
            self._next_due = self._t0
            self._cpu_prev = read_cpu_times() if self._machine_reader is None else None
            self._thread = threading.Thread(target=self._loop, name="benchmarks-sampler", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop and wait for the thread to end (at most the call time limit plus a moment). Safe to call twice or without start."""
        with self._lock:
            t, self._thread = self._thread, None
            self._started = False
        self._stop.set()
        if t is not None and t is not threading.current_thread():
            t.join(self._timeout + 2.0)

    def alive(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    def mark(self, phase: str) -> None:
        """From now on samples are tagged with `phase` (for example `idle_before_load`, `after_load`, `level_4`)."""
        with self._lock:
            self._phase = phase

    def _loop(self) -> None:
        self._read_limits()
        while not self._stop.is_set():
            self.run_due(self._clock())
            self._stop.wait(max(0.01, min(self._interval, self._next_due - self._clock())))

    def run_due(self, now: float) -> bool:
        """Take one sample if one is due at `now`. A late call does not catch up with a burst: the next one is due an interval after `now`."""
        with self._lock:
            if now < self._next_due:
                return False
            self._next_due += self._interval
            if self._next_due <= now:
                self._next_due = now + self._interval
        self.tick(now)
        return True

    # -- one reading --
    def _read_limits(self) -> None:
        status, text = self._runner(SMI_THERMAL, self._timeout)
        if status == "ok":
            with self._lock:
                self._limits = parse_thermal_limits(text)

    def _machine_now(self) -> dict:
        if self._machine_reader is not None:
            return self._machine_reader()
        cpu_now = read_cpu_times()
        cpu = cpu_util_between(self._cpu_prev, cpu_now)
        self._cpu_prev = cpu_now or self._cpu_prev
        used, total = read_ram_mb()
        d = benchmarks_guard.read_memory_detail()
        return {"cpu_util_pct": cpu, "ram_used_mb": used, "ram_total_mb": total, "ram_available_mb": d.get("available_mb"), "commit_used_mb": d.get("commit_used_mb"),
                "commit_limit_mb": d.get("commit_limit_mb"), "psi_full_avg10": d.get("psi_full_avg10")}

    def tick(self, now: float) -> None:
        """Read everything once and hand the rows to the sink."""
        t_s = round(now - self._t0, 3)
        rows = []
        if not self._gave_up:
            status, text = self._runner(self._query, self._timeout)
            if status == "error" and self._query is SMI_QUERY_MARGIN and not self._margin_column:      # an older driver refuses the margin field: ask the plain query, not a failure
                self._query = SMI_QUERY
                status, text = self._runner(self._query, self._timeout)
            cards = parse_gpu_rows(text) if status == "ok" else []
            amd = self._amd_rows(cards)
            if status == "ok" and cards and self._query is SMI_QUERY_MARGIN:
                self._margin_column = True
            elif status == "ok" and cards and self.samples_taken % Q_MARGIN_EVERY == Q_MARGIN_EVERY - 1:
                self._read_limits()                        # no margin column: read the margin from the `-q` text now and then
            with self._lock:
                if status == "missing" and not amd:
                    self._gave_up = REASON_NEEDS_NVIDIA
                elif amd and (status != "ok" or not cards):
                    self._failures = 0                         # an AMD-only PC: no NVIDIA tool is not a failure
                elif status != "ok" or not cards:
                    self._failures += 1
                    if self._failures >= MAX_FAILURES_IN_A_ROW:
                        self._gave_up = REASON_GAVE_UP
                else:
                    self._failures = 0
            rows += cards + amd
        raw = dict(self._machine_now() or {})
        machine = {f: raw.get(f) for f in MACHINE_FIELDS}
        extra = {f: raw.get(f) for f in MEMORY_EXTRA_FIELDS}
        with self._lock:
            phase = self._phase
            for r in rows:
                if r["t_limit_margin_c"] is None and not self._margin_column:
                    r["t_limit_margin_c"] = (self._limits.get(r["gpu_index"]) or {}).get("current_margin_c")
                self._names[r["gpu_index"]] = r["name"]
                for f in GPU_FIELDS:
                    if r[f] is None:
                        self._reasons.setdefault(f"gpu{r['gpu_index']}.{f}", REASON_NOT_REPORTED)
                self._latest[r["gpu_index"]] = dict(r, t_s=t_s, phase=phase)
                self._keep(r["gpu_index"], {f: r[f] for f in GPU_FIELDS}, phase)
            for f in MACHINE_FIELDS:
                if machine[f] is None:
                    self._reasons.setdefault(f"machine.{f}", REASON_UNREADABLE)
            self._latest["machine"] = dict(machine, **extra, t_s=t_s, phase=phase)
            self._keep("machine", machine, phase)
            if extra["ram_available_mb"] is not None:
                self._min_available = extra["ram_available_mb"] if self._min_available is None else min(self._min_available, extra["ram_available_mb"])
            if machine["ram_used_mb"] is not None:
                self._ram_first = machine["ram_used_mb"] if self._ram_first is None else self._ram_first
                self._ram_last = machine["ram_used_mb"]
            self.samples_taken += 1
        out = [dict(r, kind="gpu", t_s=t_s, phase=phase) for r in rows] + [dict(machine, **extra, kind="machine", t_s=t_s, phase=phase)]
        if self._sink:
            for row in out:
                try:
                    self._sink(row)
                except Exception:
                    pass                                   # a failing sink must not stop the readings

    def _amd_rows(self, nvidia_cards: list) -> list:
        """The AMD cards' rows, numbered after the NVIDIA cards. Whatever the reader does, it never raises into the sampler; a card the driver gives nothing for still has its row (the
        figures are None and so not drawn)."""
        try:
            got = self._amd_reader() or []
        except Exception:
            return []
        nxt = max([c["gpu_index"] for c in nvidia_cards], default=-1) + 1
        out = []
        for i, r in enumerate(got):
            row = {"gpu_index": nxt + i, "name": str(r.get("name") or "AMD graphics card")}
            for f in GPU_FIELDS:
                row[f] = r.get(f)
            if "mem_clock_mhz" in r:
                row["mem_clock_mhz"] = r["mem_clock_mhz"]
            out.append(row)
        return out

    def _keep(self, who, values: dict, phase: str) -> None:
        for f, v in values.items():
            if v is None:
                continue
            k = (who, f)
            self._high[k] = v if k not in self._high else max(self._high[k], v)
            self._low[k] = v if k not in self._low else min(self._low[k], v)
            pk = (phase, who, f)
            self._phase_high[pk] = v if pk not in self._phase_high else max(self._phase_high[pk], v)

    # -- what the runner and the live panel ask --
    def snapshot(self) -> dict:
        """The latest readings: `{"gpus": [row, ...], "machine": row or None, "not_measured": reason or ""}`. Rows carry `thermal_limit_c`."""
        with self._lock:
            gpus = [dict(self._latest[i], thermal_limit_c=self._limit_of(i)) for i in sorted(k for k in self._latest if k != "machine")]
            return {"gpus": gpus, "machine": dict(self._latest["machine"]) if "machine" in self._latest else None, "not_measured": self._gave_up}

    def highest_lowest(self) -> dict:
        """Since the sampler started: `{who: {field: {"highest_sampled": x, "lowest_sampled": y}}}`, who = a card index or `"machine"`. A field never read is absent."""
        with self._lock:
            out: dict = {}
            for (who, f), hi in self._high.items():
                out.setdefault(who, {})[f] = {"highest_sampled": hi, "lowest_sampled": self._low[(who, f)]}
            return out

    def highest_in_phase(self, phase: Optional[str] = None) -> dict:
        """The highest sampled value of each reading during one phase (default: the current one): `{who: {field: highest_sampled}}`."""
        with self._lock:
            p = phase if phase is not None else self._phase
            out: dict = {}
            for (ph, who, f), hi in self._phase_high.items():
                if ph == p:
                    out.setdefault(who, {})[f] = hi
            return out

    def lowest_available_ram_mb(self) -> Optional[float]:
        with self._lock:
            return self._min_available

    def reasons(self) -> dict:
        """Why a reading is missing, once per run: `{"gpu0.power_w": "not reported by this card", "gpus": "needs an NVIDIA card", ...}`. For the manifest's availability list."""
        with self._lock:
            out = dict(self._reasons)
            if self._gave_up:
                out["gpus"] = self._gave_up
            return out

    def gpu_names(self) -> dict:
        with self._lock:
            return dict(self._names)

    def _limit_of(self, idx: int) -> Optional[float]:
        return (self._limits.get(idx) or {}).get("thermal_limit_c")

    def thermal_limit_c(self) -> dict:
        """`{gpu_index: the card's own lowest heat threshold in C, or None}` for the cards seen."""
        with self._lock:
            return {i: self._limit_of(i) for i in self._names}

    def thermal_basis(self) -> dict:
        """What each card gives for a heat limit, for the result file: `{gpu_index: "absolute" | "margin" | "none"}`. `none` means the automatic stop is off for that card (the
        setting ceiling, when one is given, still applies) and the file says so."""
        with self._lock:
            out = {}
            for i in self._names:
                info = self._limits.get(i) or {}
                out[i] = "absolute" if info.get("thermal_limit_c") is not None else ("margin" if info.get("max_operating_margin_c") is not None else "none")
            return out

    def thermal_stop(self) -> dict:
        """Has any tested card reached its own heat limit? `{"stop": bool, ...}`. Per card, in this order: the card's absolute slowdown / shutdown temperature; else its margin (the
        live degrees of headroom has fallen to the card's own max-operating margin: "the card says it has reached its maximum operating temperature"); else the optional setting
        ceiling. A card that gives none of them never stops the run (nothing is invented); `limit_known` says whether any source exists."""
        with self._lock:
            for i in sorted(k for k in self._latest if k != "machine"):
                if self._tested is not None and i not in self._tested:
                    continue
                row, info = self._latest[i], self._limits.get(i) or {}
                temp, limit = row.get("temp_c"), info.get("thermal_limit_c")
                if limit is not None:
                    if temp is not None and temp >= limit:
                        return {"stop": True, "gpu_index": i, "temp_c": temp, "limit_c": limit, "limit_source": "card"}
                    continue
                live, at = row.get("t_limit_margin_c"), info.get("max_operating_margin_c")
                if at is not None:
                    if live is not None and live <= at:
                        return {"stop": True, "gpu_index": i, "temp_c": temp, "margin_c": live, "limit_margin_c": at, "limit_source": "card margin"}
                    continue
                if self._ceiling_c is not None and temp is not None and temp >= self._ceiling_c:
                    return {"stop": True, "gpu_index": i, "temp_c": temp, "limit_c": self._ceiling_c, "limit_source": "setting"}
            known = any(self._limit_of(i) is not None or (self._limits.get(i) or {}).get("max_operating_margin_c") is not None for i in self._names) or self._ceiling_c is not None
            return {"stop": False, "limit_known": known}

    # -- spill --
    def read_engine_shared_gpu_mb(self, pid: int) -> Optional[int]:
        """Windows only: the shared GPU memory ("Non Local Usage") charged to one process (the engine), read on demand through the open counter query and remembered for `spill()`. None when it cannot be read."""
        if not gpu_counters._windows():
            return None
        got = self._counters(int(pid))
        mb = got.get("shared_mb") if isinstance(got, dict) else None          # Non Local Usage: what is charged to the engine, not every surface it can see
        with self._lock:
            self._shared.append(mb)
        return mb

    def peek_engine_shared_gpu_mb(self, pid: int) -> Optional[int]:
        """The same figure as `read_engine_shared_gpu_mb` (the "Non Local Usage" the card has put in main memory for this program), read for the live panel only: it is NOT remembered for the
        spill evidence. None when it cannot be read."""
        if not gpu_counters._windows():
            return None
        got = self._counters(int(pid))
        return got.get("shared_mb") if isinstance(got, dict) else None

    def read_card_users(self, skip_pid: Optional[int] = None) -> dict:
        """Who is using the card right now (every program, grouped by name), read on demand: the open counter query on Windows (`nvidia-smi` on Linux, through the
        hidden-window runner). NEVER called from the one-second tick: the run asks for it at the start and then every `LIVE_EVERY_S` seconds. `skip_pid` (the engine this test
        started) is left out. `{"available": False}` when the card could not be read; a program that cannot be read is not in the list."""
        return benchmarks_cardusage.read_usage(self._runner, self._timeout, skip_pids=(skip_pid,) if skip_pid else ())

    def spill(self, layers_on_card: Optional[int] = None, layers_requested: Optional[int] = None) -> dict:
        """`spill_evidence` with this run's remembered shared-memory readings and system RAM rise."""
        with self._lock:
            rose = (self._ram_last - self._ram_first) if self._ram_first is not None and self._ram_last is not None else None
            return spill_evidence(layers_on_card, layers_requested, list(self._shared), rose)
