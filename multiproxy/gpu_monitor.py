"""Graphics memory monitor (2026-10-01), built to design/dashboard-local-models-mockup.html Revisions 5 and 6.

Shows who is using this device's graphics memory (Skyrim, the local model engines, everything else, what's free),
and learns Skyrim's busiest point so the Local models fit icons can use the real number instead of a guess.

No dependency on multiproxy.py. Everything that talks to the operating system runs off the request path, in a
thread, and is cached for a couple of seconds so a dashboard polling every few seconds can't pile up.

Where the numbers come from:
  * Windows: the `\\GPU Process Memory(*)\\Dedicated Usage` performance counters (via `typeperf`) give each
    process's graphics memory; instances are named `pid_<pid>_luid_..._phys_0`. Total in use: `nvidia-smi` when
    there is one, else the sum of the per-process readings.
  * Linux, NVIDIA: `nvidia-smi` for the total and for the processes it can see (the model engines). Skyrim isn't
    on a model server. No per-process figures -> total + models only.
  * Anything else (AMD/Intel on Linux, no reading at all): there is no monitor; the card hides itself.
Some processes report nonsense (one claimed 200 GB): any per-process reading bigger than the card is ignored and
falls into "Everything else"; nothing is ever drawn bigger than the card.
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import io
import re
import subprocess
import hidden_process  # every program started from here goes through it: no console window flashing up
import skyrim_peak  # the one stored figure for Skyrim's busiest moment: the highest reading of the last 14 days
import native_probe  # Windows: graphics and process numbers read in-process, without starting a program
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

SKYRIM_PROCESSES = ("skyrimse.exe", "skyrimvr.exe")
SAMPLE_CACHE_SECONDS = 2.5
PEAK_SAMPLE_SECONDS = 30.0
MIB = 1024 * 1024
_PID_COLUMN = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_\d+")


@dataclass
class MemorySample:
    total_mb: int
    used_mb: int
    skyrim_running: bool = False
    skyrim_mb: Optional[int] = None          # None = not running, or running but unreadable
    models: dict = field(default_factory=dict)       # model id -> MB
    other_mb: int = 0
    free_mb: int = 0
    source: str = ""


# ---------------------------------------------------------------------------------------------
# Parsing (pure; tested with real captured output)
# ---------------------------------------------------------------------------------------------
def parse_typeperf_gpu_process_memory(text: str) -> dict:
    """`typeperf "\\GPU Process Memory(*)\\Dedicated Usage" -sc 1` -> {pid: MB} for the main graphics card.
    The CSV has one column per (process, adapter). Several adapters can exist (a built-in one beside the real
    card): the one with the most memory in use is "the card"."""
    rows = list(csv.reader(io.StringIO(text or "")))
    rows = [r for r in rows if r]
    header = next((r for r in rows if r and r[0].startswith("(PDH")), None)
    if header is None:
        return {}
    values = next((r for r in rows[rows.index(header) + 1:] if r and re.match(r"\d\d/\d\d/\d{4}", r[0])), None)
    if values is None:
        return {}
    per_luid: dict = {}
    for col, raw in zip(header[1:], values[1:]):
        m = _PID_COLUMN.search(col)
        if not m:
            continue
        try:
            nbytes = float(raw)
        except ValueError:
            continue
        pid, luid = int(m.group(1)), m.group(2)
        per_luid.setdefault(luid, {})
        per_luid[luid][pid] = per_luid[luid].get(pid, 0) + nbytes
    if not per_luid:
        return {}
    main = max(per_luid, key=lambda l: sum(per_luid[l].values()))
    return {pid: int(nbytes / MIB) for pid, nbytes in per_luid[main].items()}


def parse_tasklist_pids(text: str, names=SKYRIM_PROCESSES) -> list:
    """`tasklist /FO CSV /NH` -> the PIDs of the named programs."""
    pids = []
    for row in csv.reader(io.StringIO(text or "")):
        if len(row) >= 2 and row[0].strip().lower() in names:
            try:
                pids.append(int(row[1]))
            except ValueError:
                pass
    return pids


def parse_nvidia_memory(text: str) -> Optional[tuple]:
    """`nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits` -> (used_mb, total_mb) for
    the card with the most memory, or None."""
    best = None
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            try:
                used, total = int(float(parts[0])), int(float(parts[1]))
            except ValueError:
                continue
            if best is None or total > best[1]:
                best = (used, total)
    return best


def parse_nvidia_cards(text: str) -> list:
    """The same query -> [(used_mb, total_mb), ...] for EVERY card, in the tool's own order (the graphics memory guard needs one card's free memory, not the biggest's).
    A line that can't be read keeps its place as None, so the others still line up with the cards."""
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            out.append((int(float(parts[0])), int(float(parts[1]))) if len(parts) == 2 else None)
        except ValueError:
            out.append(None)
    return out


def parse_nvidia_compute_apps(text: str) -> dict:
    """`nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits` -> {pid: MB}."""
    out = {}
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit():
            try:
                out[int(parts[0])] = int(float(parts[1]))
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------------------------
# Putting the readings together (pure)
# ---------------------------------------------------------------------------------------------
def build_sample(total_mb: int, used_mb: Optional[int], per_pid: Optional[dict], skyrim_pids: list,
                 engine_pids: dict, source: str = "") -> MemorySample:
    """Split the card's memory into Skyrim / each model / everything else / free.

    `per_pid` is {pid: MB} or None when per-process figures aren't available. A reading bigger than the card
    is nonsense: it is ignored (so it lands in "Everything else") and can't make any segment bigger than the
    card. `engine_pids` is {model id: pid} for the engines MultiProxy started."""
    valid = {pid: mb for pid, mb in (per_pid or {}).items() if 0 <= mb <= total_mb}
    skyrim_running = bool(skyrim_pids)
    skyrim_mb = None
    if skyrim_running and per_pid is not None:
        readings = [valid[p] for p in skyrim_pids if p in valid]
        skyrim_mb = sum(readings) if readings else None
    models = {mid: valid[pid] for mid, pid in engine_pids.items() if pid in valid}
    attributed = (skyrim_mb or 0) + sum(models.values())
    if used_mb is None:
        used_mb = sum(valid.values())
    used_mb = max(0, min(used_mb, total_mb))
    if attributed > used_mb:                  # attributed readings can't exceed what's really in use
        used_mb = min(total_mb, attributed)
    return MemorySample(total_mb=total_mb, used_mb=used_mb, skyrim_running=skyrim_running, skyrim_mb=skyrim_mb,
                        models=models, other_mb=max(0, used_mb - attributed), free_mb=max(0, total_mb - used_mb), source=source)


def when_text(iso_date: Optional[str], today: Optional[datetime.date] = None) -> str:
    """'today', 'yesterday' or 'on 28 Sep' for the date Skyrim's busiest point was seen."""
    if not iso_date:
        return ""
    today = today or datetime.date.today()
    try:
        d = datetime.date.fromisoformat(iso_date)
    except ValueError:
        return ""
    if d == today:
        return "today"
    if d == today - datetime.timedelta(days=1):
        return "yesterday"
    return f"on {d.day} {d.strftime('%b')}"


# ---------------------------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------------------------
def _run(cmd: list, timeout: float = 15) -> Optional[str]:
    # On Windows the numbers come from inside this process (no program started, so nothing can flash up); the real program is the fallback.
    ans = native_probe.answer(cmd)
    if ans is not None:
        return ans
    try:
        r = hidden_process.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


class GpuMonitor:
    """Reads this device's graphics memory and remembers Skyrim's busiest point.

    `local_svc` (part 1's LocalModels) supplies the card and the running engines; `load_cfg`/`save_cfg` read and
    write config.json (`gpu_monitor`: `{"skyrimPeakMb", "skyrimPeakDate"}`)."""

    def __init__(self, local_svc, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], log,
                 reader: Optional[Callable[[], Optional[MemorySample]]] = None):
        self.local = local_svc
        self._load_cfg, self._save_cfg, self.log = load_cfg, save_cfg, log
        self._reader = reader or self._read_sync
        self._cache: Optional[tuple] = None
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self.last_skyrim_mb: Optional[int] = None       # the game's graphics memory the last time a reading could tell (the memory guard records it)

    # ---- the peak ------------------------------------------------------------------------------------
    def _cfg(self) -> dict:
        return dict(self._load_cfg().get("gpu_monitor") or {})

    @property
    def skyrim_peak_mb(self) -> Optional[int]:
        return skyrim_peak.figure(self._load_cfg())[0]

    @property
    def skyrim_peak_date(self) -> Optional[str]:
        return skyrim_peak.figure(self._load_cfg())[1]

    def note_skyrim(self, mb: int, today: Optional[datetime.date] = None) -> bool:
        """Record `mb` as a reading for today (the figure is the highest of the last 14 days, skyrim_peak.py). True when it raised today's figure."""
        cfg = self._load_cfg()
        gpu = getattr(self.local, "_gpu", None)
        if not skyrim_peak.record(cfg, mb, today, getattr(gpu, "memory_mb", None)):
            return False
        self._save_cfg(cfg)
        return True

    def reset_peak(self) -> None:
        cfg = self._load_cfg()
        skyrim_peak.clear(cfg)
        self._save_cfg(cfg)

    # ---- reading ----------------------------------------------------------------------------------------
    @property
    def model_server(self) -> bool:
        return bool(self._load_cfg().get("model_server"))

    def _engine_pids(self) -> dict:
        return {mid: s.proc.pid for mid, s in self.local.servers.items()
                if s.proc is not None and s.proc.returncode is None}

    def _read_sync(self) -> Optional[MemorySample]:
        """One reading, or None when this device can't be monitored. Blocking (runs in a thread)."""
        gpu = getattr(self.local, "_gpu", None)
        engines = self._engine_pids()
        with_skyrim = not self.model_server
        if sys.platform == "win32":
            return self._read_windows(gpu, engines, with_skyrim)
        if sys.platform.startswith("linux"):
            return self._read_linux(gpu, engines)
        return None

    def card_memory(self) -> list:
        """Every NVIDIA card's (used_mb, total_mb), in the driver's order; [] when this device can't say. One light query: on Windows it is answered inside this process by the
        driver's own library (native_probe), so no program starts and no window can flash. Blocking (the memory guard runs it in a thread)."""
        out = _run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
        return parse_nvidia_cards(out) if out else []

    def skyrim_alive(self) -> Optional[bool]:
        """Is Skyrim's program running? None when this device can't say (not Windows, or the list could not be read). Answered in-process on Windows."""
        if sys.platform != "win32":
            return None
        out = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=10)
        return None if out is None else bool(parse_tasklist_pids(out))

    def _nvidia(self) -> Optional[tuple]:
        out = _run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
        return parse_nvidia_memory(out) if out else None

    def _read_windows(self, gpu, engines: dict, with_skyrim: bool) -> Optional[MemorySample]:
        nv = self._nvidia()
        total = nv[1] if nv else (gpu.memory_mb if gpu and gpu.memory_mb else None)
        if not total:
            return None
        skyrim_pids = []
        if with_skyrim:
            tl = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=10)
            skyrim_pids = parse_tasklist_pids(tl or "")
        wanted = set(skyrim_pids) | set(engines.values())
        per_pid = None
        if wanted or not nv:
            out = _run(["typeperf", r"\GPU Process Memory(*)\Dedicated Usage", "-sc", "1"], timeout=20)
            per_pid = parse_typeperf_gpu_process_memory(out) if out else None
        if per_pid is None and not nv:
            return None
        return build_sample(total, nv[0] if nv else None, per_pid, skyrim_pids, engines, source="windows-counters")

    def _read_linux(self, gpu, engines: dict) -> Optional[MemorySample]:
        nv = self._nvidia()
        if not nv:
            return None
        apps = _run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"])
        per_pid = parse_nvidia_compute_apps(apps) if apps is not None else None
        return build_sample(nv[1], nv[0], per_pid, [], engines, source="nvidia-smi")

    async def sample(self, force: bool = False) -> Optional[MemorySample]:
        """The latest reading (cached a couple of seconds), taken off the request path."""
        async with self._lock:
            now = time.monotonic()
            if not force and self._cache and now - self._cache[0] < SAMPLE_CACHE_SECONDS:
                return self._cache[1]
            try:
                s = await asyncio.to_thread(self._reader)
            except Exception:
                self.log.exception("Reading graphics memory failed")
                s = None
            self._cache = (time.monotonic(), s)
            if s is not None and s.skyrim_mb:
                self.last_skyrim_mb = s.skyrim_mb
            return s

    # ---- what the dashboard and the sharing door send ----------------------------------------------------
    def model_names(self) -> dict:
        return {m.id: m.name for m in self.local.installed()}

    async def payload(self, include_skyrim: bool = True) -> dict:
        """The Graphics memory card's data. `available: False` means "hide the card"."""
        gpu = getattr(self.local, "_gpu", None)
        s = await self.sample()
        if s is None:
            return {"available": False}
        names = self.model_names()
        include_skyrim = include_skyrim and not self.model_server
        segs = []
        if include_skyrim and s.skyrim_mb:
            segs.append({"key": "skyrim", "label": "Skyrim", "mb": s.skyrim_mb})
        for mid, mb in s.models.items():
            segs.append({"key": "model:" + mid, "label": names.get(mid, mid), "mb": mb})
        peak = self.skyrim_peak_mb if include_skyrim else None
        return {
            "available": True, "card": gpu.name if gpu else "Graphics card", "totalMb": s.total_mb,
            "usedMb": s.used_mb, "segments": segs, "otherMb": s.other_mb + ((s.skyrim_mb or 0) if not include_skyrim else 0),
            "freeMb": s.free_mb, "skyrimShown": include_skyrim,
            "skyrimRunning": bool(include_skyrim and s.skyrim_running),
            "peakMb": peak, "peakWhen": when_text(self.skyrim_peak_date) if peak else "",
        }

    async def measure_skyrim(self, readings: int = 10, seconds: float = 10.0, sleep=asyncio.sleep) -> dict:
        """The running game's graphics memory right now: the highest of `readings` readings over about `seconds`. {"running": bool, "mb": int | None}; mb is None when the game is not
        running or this PC cannot give a figure (a reading bigger than the card is ignored by build_sample). One measure is one moment of play: it cannot know a bigger area or more mods."""
        best, running = None, False
        for i in range(max(1, readings)):
            s = await self.sample(force=True)
            if s and s.skyrim_running:
                running = True
                if s.skyrim_mb:
                    best = max(best or 0, s.skyrim_mb)
            elif i == 0:
                break                                        # the game is not running: nothing to wait for
            if i + 1 < readings:
                await sleep(seconds / max(1, readings - 1))
        return {"running": running, "mb": best}

    # ---- background: learn Skyrim's busiest point ----------------------------------------------------------
    async def sample_for_peak(self) -> None:
        if self.model_server:
            return
        s = await self.sample(force=True)
        if s and s.skyrim_running and s.skyrim_mb:
            if self.note_skyrim(s.skyrim_mb):
                self.log.info(f"Skyrim's busiest graphics memory so far: {s.skyrim_mb / 1024:.1f} GB")

    def start(self) -> None:
        if self._task and not self._task.done():
            return

        async def loop():
            while True:
                try:
                    await self.sample_for_peak()
                except Exception:
                    self.log.exception("Learning Skyrim's graphics memory failed")
                await asyncio.sleep(PEAK_SAMPLE_SECONDS)
        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
