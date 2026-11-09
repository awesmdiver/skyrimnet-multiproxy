"""Benchmarks: who is using the graphics card right now (design/mockup-benchmarks-card-usage.html, screens A to D).

Everything here is a pure function of text a program printed, plus one reader that asks for that text through the sampler's runner (hidden window, a time limit, never a made-up
number). A figure that cannot be read is simply absent: no zero, no row.

Where the numbers come from:
- Windows: ONE `typeperf` call for the counters `\\GPU Process Memory(*)\\Dedicated Usage` and `\\GPU Process Memory(*)\\Shared Usage` (every program at once), and one `tasklist /FO CSV /NH`
  call for the program names. The card's own total and use come from `nvidia-smi --query-gpu=memory.used,memory.total`.
- Linux (NVIDIA): `nvidia-smi --query-compute-apps=pid,process_name,used_memory` (only programs that compute on the card; a desktop or a game is not in that list, and so is not drawn).

The size drawn for a program is its DEDICATED memory (what it holds on the card). Its shared memory (graphics memory kept in the computer's main memory) is kept beside it as `shared_mb`.

Program names are for the person's own screen and own saved results only: `OTHER_FILE` is written next to a run's files but is NOT one of the files a Courier hands back (`RESULT_FILES`
in benchmarks_remote_plan), and `strip_names` is the one function an export or a send goes through.
"""
import csv
import io
import re
import sys
from typing import Optional

import gpu_counters
import gpu_monitor

MIB = 1024 * 1024
OTHER_FILE = "other-programs.json"                        # a run's top programs at the start: local only, never part of an export or of what a Courier sends back
TOP_N = 3
NO_BUFFERS_LINE = "engine did not print its buffer sizes"          # the log line when the engine's own memory lines are missing (nothing is drawn then)
LIVE_EVERY_S = 5.0                                         # during a run the other programs are re-read this often (one counter call each), never on the sampler's one-second tick
NAME_KEYS = ("other_programs", "others_at_start")          # every key under which program names travel; `strip_names` removes them

_SKYRIM = {"skyrimse.exe", "skyrimvr.exe", "skyrim.exe"}
_DESKTOP = {"dwm.exe"}
_COUNTER_COL = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_\d+")

SMI_MEMORY_CMD = ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]
SMI_APPS_CMD = ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]


# --- names ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def plain_name(exe: str) -> str:
    """`SkyrimSE.exe` / `SkyrimVR.exe` -> Skyrim; `dwm.exe` -> Windows desktop; any other program -> its name without `.exe`, first letter capitalised."""
    base = (exe or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    low = base.lower()
    if low in _SKYRIM:
        return "Skyrim"
    if low in _DESKTOP:
        return "Windows desktop"
    if low.endswith(".exe"):
        base = base[:-4]
    return base[:1].upper() + base[1:] if base else ""


# --- parsing (pure) ---------------------------------------------------------------------------------------------------------------------------------------------------------

def parse_typeperf_both(text: str) -> dict:
    """The two-counter `typeperf` CSV -> `{pid: {"dedicated_mb": int or None, "shared_mb": int or None}}` for the main card (the adapter holding the most dedicated memory).
    Several adapters can exist (a built-in one beside the real card). A column that is not a number is skipped, not turned into a zero."""
    rows = [r for r in csv.reader(io.StringIO(text or "")) if r]
    header = next((r for r in rows if r[0].startswith("(PDH")), None)
    if header is None:
        return {}
    values = next((r for r in rows[rows.index(header) + 1:] if re.match(r"\d\d/\d\d/\d{4}", r[0])), None)
    if values is None:
        return {}
    per: dict = {}                                         # luid -> pid -> {"dedicated": bytes, "shared": bytes}
    for col, raw in zip(header[1:], values[1:]):
        m = _COUNTER_COL.search(col)
        kind = "dedicated" if col.endswith("Dedicated Usage") else ("shared" if col.endswith("Shared Usage") else None)
        if not m or kind is None:
            continue
        try:
            nbytes = float(raw)
        except ValueError:
            continue
        slot = per.setdefault(m.group(2), {}).setdefault(int(m.group(1)), {})
        slot[kind] = slot.get(kind, 0) + nbytes
    if not per:
        return {}
    main = max(per, key=lambda l: sum(v.get("dedicated", 0) for v in per[l].values()))
    return {pid: {"dedicated_mb": int(v["dedicated"] / MIB) if "dedicated" in v else None, "shared_mb": int(v["shared"] / MIB) if "shared" in v else None}
            for pid, v in per[main].items()}


def parse_tasklist_names(text: str) -> dict:
    """`tasklist /FO CSV /NH` -> `{pid: image name}`."""
    out = {}
    for row in csv.reader(io.StringIO(text or "")):
        if len(row) >= 2:
            try:
                out[int(row[1])] = row[0].strip()
            except ValueError:
                pass
    return out


def parse_smi_apps(text: str) -> dict:
    """`nvidia-smi --query-compute-apps=pid,process_name,used_memory` -> `{pid: {"name": image name, "dedicated_mb": int}}`. A line that is not three columns, or has no number, is skipped."""
    out = {}
    for row in csv.reader(io.StringIO(text or ""), skipinitialspace=True):
        if len(row) == 3 and row[0].strip().isdigit():
            try:
                out[int(row[0])] = {"name": row[1].strip(), "dedicated_mb": int(float(row[2]))}
            except ValueError:
                pass
    return out


# --- grouping (pure) --------------------------------------------------------------------------------------------------------------------------------------------------------

def group_programs(per_pid: dict, names: dict, skip_pids=(), card_total_mb: Optional[int] = None) -> list:
    """Several processes of one program add up. `per_pid`: `{pid: {"dedicated_mb", "shared_mb"}}`; `names`: `{pid: image name}`. Returns `[{"name", "mb", "shared_mb"}]`, biggest first.

    Left out: the pids in `skip_pids` (the engine the test started: it is "the model"), a process with no name (never invented), a process with no dedicated figure, a process holding
    nothing, and a reading bigger than the whole card (some drivers report nonsense; it is not drawn)."""
    skip = set(skip_pids or ())
    groups: dict = {}
    for pid, v in (per_pid or {}).items():
        if pid in skip:
            continue
        exe = names.get(pid)
        mb = v.get("dedicated_mb")
        if not exe or mb is None or mb <= 0 or (card_total_mb and mb > card_total_mb):
            continue
        name = plain_name(exe)
        if not name:
            continue
        g = groups.setdefault(name, {"name": name, "mb": 0, "shared_mb": 0})
        g["mb"] += mb
        g["shared_mb"] += v.get("shared_mb") or 0
    return sorted(groups.values(), key=lambda g: (-g["mb"], g["name"]))


def parse_card_memory(text: str) -> Optional[tuple]:
    """(used_mb, total_mb) of the biggest card, or None."""
    return gpu_monitor.parse_nvidia_memory(text)


def build_usage(total_mb: Optional[int], used_mb: Optional[int], programs: list, source: str) -> dict:
    """The block the page draws: `{"available", "totalMb", "usedMb", "freeMb", "programs", "source"}`. No card total -> not available (nothing is drawn). `freeMb` is the total minus
    everything in use now, and is None when the card did not say what is in use."""
    if not total_mb:
        return {"available": False}
    free = max(0, total_mb - used_mb) if used_mb is not None else None
    return {"available": True, "totalMb": int(total_mb), "usedMb": used_mb, "freeMb": free, "programs": list(programs or []), "source": source}


def top(programs: list, n: int = TOP_N) -> list:
    return [{"name": p["name"], "mb": p["mb"]} for p in (programs or [])[:n]]


# --- the reader -------------------------------------------------------------------------------------------------------------------------------------------------------------

def _main_adapter():
    """The card with the most memory among those `gpu_adapters` lists (a laptop's built-in graphics loses to the real card); None when none has a readable total."""
    try:
        import gpu_adapters
        cards = [a for a in gpu_adapters.list_adapters() if a.memory_mb]
    except Exception:
        return None
    return max(cards, key=lambda a: a.memory_mb) if cards else None


def read_usage(runner, timeout: float = 20.0, skip_pids=(), platform: Optional[str] = None, counters=None, names=None) -> dict:
    """One reading of the card and of who is using it. `runner(cmd, timeout) -> (status, text)` is the sampler's (hidden window, a time limit); tests give a fake. Blocking: call it from
    a thread. `counters(card_used_mb) -> {pid: {"dedicated_mb", "shared_mb"}} or None` and `names() -> {pid: image name}` default to `gpu_counters`; tests give fakes. Anything unreadable is
    left out (see `build_usage`)."""
    plat = platform or sys.platform
    status, text = runner(SMI_MEMORY_CMD, timeout)
    mem = parse_card_memory(text) if status == "ok" else None
    used, total = (mem[0], mem[1]) if mem else (None, None)
    programs: list = []
    source = "nvidia-smi"
    luid = None
    if plat == "win32" and total is None:                      # no NVIDIA card or tool: the biggest card Windows lists (AMD, Intel...), its use from the counters
        main = _main_adapter()
        if main is not None:
            total, luid, source = main.memory_mb, main.luid, "windows-counters"
            used = gpu_counters.adapter_used_mb(luid)
    if plat == "win32":
        per = (counters or gpu_counters.read_programs)(used, luid) if counters is None else counters(used)
        per = per or {}
        if per:
            programs = group_programs(per, (names or gpu_counters.process_names)(), skip_pids, total)
            source = "windows-counters"
    else:
        s1, t1 = runner(SMI_APPS_CMD, timeout)
        apps = parse_smi_apps(t1) if s1 == "ok" else {}
        programs = group_programs({p: {"dedicated_mb": a["dedicated_mb"], "shared_mb": None} for p, a in apps.items()}, {p: a["name"] for p, a in apps.items()}, skip_pids, total)
    return build_usage(total, used, programs, source)


# --- what an export or a send may carry ------------------------------------------------------------------------------------------------------------------------------------

def strip_names(obj):
    """A copy of `obj` (dicts and lists, any depth) with every key in `NAME_KEYS` removed. Every path that writes a result for someone else goes through this."""
    if isinstance(obj, dict):
        return {k: strip_names(v) for k, v in obj.items() if k not in NAME_KEYS}
    if isinstance(obj, list):
        return [strip_names(v) for v in obj]
    return obj
