"""Benchmarks: the result folder on disk (contract section 9, schema version 1) and the model hash cache (section 1).

One folder per run under the benchmark data folder, next to the settings and never inside another program's folder: `benchmarks/<run id>/` holding `manifest.json`, `requests.jsonl`,
`samples.jsonl`, `summary.json` and `progress.json`. Whole-file JSON is written all-or-nothing (a temp file, then a rename, via `safe_paths.write_private_json`); the two `.jsonl` files
are appended line by line and flushed, by one writer each. The rules themselves (ids, states, statistics) are in `benchmarks_contract.py`. No network and no processes; the time is
passed in.
"""
import csv
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

import benchmarks_contract as bc
import safe_paths
from settings_files import settings_dir

BENCHMARKS_DIR_ENV = "MULTIPROXY_BENCHMARKS_DIR"           # tests (and a player who wants them elsewhere) set this
BENCHMARKS_FOLDER_NAME = "benchmarks"
HASH_CACHE_NAME = "model-hashes.json"
HASH_CHUNK_BYTES = 8 * 1024 * 1024
STALE_HEARTBEAT_S = 120.0                                  # the runner writes a heartbeat at least every 30 s; this long without one means the program is gone
MANIFEST, REQUESTS, SAMPLES, SUMMARY, PROGRESS = "manifest.json", "requests.jsonl", "samples.jsonl", "summary.json", "progress.json"

NEWER_VERSION_LINE = "This run was saved by a newer version of MultiProxy, so it can't be shown here."   # shown instead of a run this version cannot read


class NewerSchemaError(ValueError):
    """A run folder whose `schema_version` is newer than this program knows. Its data is never guessed at."""

    def __init__(self, found: int):
        super().__init__(NEWER_VERSION_LINE)
        self.found = found


class HashCancelled(Exception):
    """The person cancelled while a model file was being hashed."""


# --- where ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def benchmark_dir(program_dir) -> Path:
    """The benchmark data folder: `MULTIPROXY_BENCHMARKS_DIR` if set, else `benchmarks` inside the settings folder (the way the other data folders are resolved)."""
    env = os.environ.get(BENCHMARKS_DIR_ENV, "").strip()
    return Path(env) if env else Path(settings_dir(program_dir)) / BENCHMARKS_FOLDER_NAME


def run_dir(base, run_id: str) -> Path:
    """The folder of one run. Only a well-formed run id is accepted, so a name can never point outside `base`."""
    if not bc.valid_run_id(run_id):
        raise ValueError("not a run id")
    return Path(base) / run_id


# --- the model id and its cache ---------------------------------------------------------------------------------------------------------------------------------------------

def hash_file(path, progress: Optional[Callable[[int, int], None]] = None, cancelled: Optional[Callable[[], bool]] = None) -> str:
    """sha256 of a file read in chunks. `progress(bytes_done, bytes_total)` after each chunk; `cancelled()` is asked before each chunk and raises `HashCancelled`."""
    total = os.path.getsize(path)
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while True:
            if cancelled and cancelled():
                raise HashCancelled()
            chunk = f.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return h.hexdigest()


def model_id_for(path, base, progress=None, cancelled=None) -> str:
    """The model id of a file (`sha256:<hex>:<size>`). Hashed once and cached in `base/model-hashes.json` by (path, size, modified time); a changed file is hashed again."""
    st = os.stat(path)
    key = os.path.normcase(os.path.abspath(path))
    cache_path = Path(base) / HASH_CACHE_NAME
    cache = _read_json(cache_path) or {}
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("size") == st.st_size and hit.get("mtime_ns") == st.st_mtime_ns and hit.get("sha256"):
        return bc.model_id(hit["sha256"], st.st_size)
    digest = hash_file(path, progress, cancelled)
    if os.stat(path).st_mtime_ns == st.st_mtime_ns:                # only cache a file that did not change while it was read
        cache[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
        safe_paths.write_private_json(cache_path, cache)
    return bc.model_id(digest, st.st_size)


# --- whole-file JSON and line files -----------------------------------------------------------------------------------------------------------------------------------------

def _read_json(path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(folder, name: str, data: dict) -> None:
    """All-or-nothing: a temp file in the same folder, then a rename. Only the run's own writer calls this for a given file."""
    safe_paths.write_private_json(Path(folder) / name, data)


_open_writers: set = set()


class JsonlWriter:
    """Appends one JSON line at a time and flushes each. One writer per file in this program: a second one on the same file raises."""

    def __init__(self, path):
        self.path = os.path.normcase(os.path.abspath(path))
        if self.path in _open_writers:
            raise RuntimeError("this file already has a writer")
        _open_writers.add(self.path)
        try:
            self._f = open(path, "a", encoding="utf-8", newline="\n")
        except BaseException:
            _open_writers.discard(self.path)
            raise

    def append(self, row: dict) -> None:
        self._f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()
            _open_writers.discard(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_jsonl(path) -> list:
    """Every complete line. A last line cut short by a crash is skipped; the rows before it are kept."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


# --- one run ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def create_run(base, run_id: str, manifest: dict) -> Path:
    """Make the folder and write the first manifest (status `running`, `schema_version` 1) and progress file. Refuses a folder that is not on a hard drive inside this device,
    and a run id that already has a folder."""
    safe_paths.require_local_fixed(base)
    folder = run_dir(base, run_id)
    folder.mkdir(parents=True, exist_ok=False)
    data = dict(manifest, schema_version=bc.SCHEMA_VERSION, run_id=run_id)
    data.setdefault("status", bc.RUN_RUNNING)
    write_json(folder, MANIFEST, data)
    return folder


def write_progress(folder, step: str, steps_done: int, steps_total: int, heartbeat_epoch_s: float, finished: bool = False) -> None:
    """The current step and the heartbeat (wall-clock seconds, so it still means something after a restart)."""
    write_json(folder, PROGRESS, {"schema_version": bc.SCHEMA_VERSION, "step": step, "steps_done": steps_done, "steps_total": steps_total,
                                  "heartbeat_epoch_s": heartbeat_epoch_s, "finished": finished})


def finish_run(folder, status: str, reason: str, end_epoch_s: float, steps_done: int, steps_total: int, extra: Optional[dict] = None) -> None:
    """Write the end of a run into the manifest and mark the progress file finished. A failed, interrupted or invalidated run needs a reason."""
    bc.move_run(bc.RUN_RUNNING, status, reason)
    manifest = read_manifest(folder)
    manifest.update(extra or {}, status=status, reason=reason, ended_epoch_s=end_epoch_s, steps_done=steps_done, steps_total=steps_total)
    write_json(folder, MANIFEST, manifest)
    write_progress(folder, "finished", steps_done, steps_total, end_epoch_s, finished=True)


def read_manifest(folder) -> dict:
    data = _read_json(Path(folder) / MANIFEST)
    if data is None:
        raise ValueError("no readable manifest.json")
    version = data.get("schema_version")
    if not isinstance(version, int):
        raise ValueError("manifest.json has no schema_version")
    if version > bc.SCHEMA_VERSION:
        raise NewerSchemaError(version)
    return data


def load_run(base, run_id: str, now_epoch_s: float, stale_after_s: float = STALE_HEARTBEAT_S) -> dict:
    """Everything of one run: manifest (with the effective status), summary, progress, requests and samples. Raises `NewerSchemaError` for a newer file."""
    folder = run_dir(base, run_id)
    manifest = read_manifest(folder)
    progress = _read_json(folder / PROGRESS)
    status, reason = effective_status(manifest, progress, now_epoch_s, stale_after_s)
    manifest = dict(manifest, status=status, reason=reason)           # a copy: nothing is written back
    return {"manifest": manifest, "summary": _read_json(folder / SUMMARY), "progress": progress,
            "requests": read_jsonl(folder / REQUESTS), "samples": read_jsonl(folder / SAMPLES)}


def effective_status(manifest: dict, progress: Optional[dict], now_epoch_s: float, stale_after_s: float = STALE_HEARTBEAT_S) -> tuple:
    """(status, reason) to show. A run still marked `running` whose progress file is not finished and whose heartbeat is old is shown as `interrupted`; the files are not changed."""
    status, reason = manifest.get("status", ""), manifest.get("reason", "")
    if status not in (bc.RUN_RUNNING, "queued"):
        return status, reason
    if progress and progress.get("finished"):
        return status, reason
    beat = (progress or {}).get("heartbeat_epoch_s", manifest.get("started_epoch_s"))
    if beat is None or now_epoch_s - beat > stale_after_s:
        return bc.RUN_INTERRUPTED, "program_stopped"
    return status, reason


def list_runs(base, now_epoch_s: float, stale_after_s: float = STALE_HEARTBEAT_S) -> list:
    """One entry per run folder, newest first: ids, profile, kind, status, reason, steps and start time. A folder that cannot be read says so (`unreadable`); a newer schema says
    so (`newer_version`, with the plain line) instead of being guessed at."""
    out = []
    try:
        names = sorted(n for n in os.listdir(base) if bc.valid_run_id(n) and (Path(base) / n).is_dir())
    except OSError:
        return []
    for name in names:
        try:
            m = load_manifest_only(base, name, now_epoch_s, stale_after_s)
        except NewerSchemaError as e:
            out.append({"run_id": name, "status": "newer_version", "schema_version": e.found, "message": NEWER_VERSION_LINE})
            continue
        except ValueError:
            out.append({"run_id": name, "status": "unreadable"})
            continue
        out.append({**{k: m.get(k) for k in ("run_id", "model_id", "setup_id", "profile", "kind", "status", "reason", "steps_done", "steps_total", "started_epoch_s", "ended_epoch_s", "skyrim_state")},
                    "device": bc.device_of(m)})
    out.sort(key=lambda e: e.get("started_epoch_s") or 0, reverse=True)
    return out


def load_manifest_only(base, run_id: str, now_epoch_s: float, stale_after_s: float = STALE_HEARTBEAT_S) -> dict:
    folder = run_dir(base, run_id)
    m = read_manifest(folder)
    prog = _read_json(folder / PROGRESS)
    status, reason = effective_status(m, prog, now_epoch_s, stale_after_s)
    out = dict(m, status=status, reason=reason)
    if prog and status != bc.RUN_COMPLETED and "steps_done" in prog:
        out["steps_done"], out["steps_total"] = prog["steps_done"], prog.get("steps_total", m.get("steps_total"))
    return out


def pairs_of(base, run_id: str, now_epoch_s: float) -> list:
    """The runs that are the same test as `run_id` apart from the background (same model, same setup without its background, another background): newest first. Unreadable runs are left out."""
    import benchmarks_background as bgm
    out = []
    try:
        names = sorted(n for n in os.listdir(base) if bc.valid_run_id(n) and (Path(base) / n).is_dir())
    except OSError:
        return []
    for name in names:
        try:
            out.append(load_manifest_only(base, name, now_epoch_s))
        except ValueError:
            continue
    return bgm.sharing_setup(out, run_id)


def delete_run(base, run_id: str) -> None:
    """Delete one run folder. Only a real folder directly inside the benchmark folder that holds a manifest is deleted; any other path, a link, or a name that climbs out is refused."""
    folder = run_dir(base, run_id)
    base_real = os.path.realpath(base)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("not a run folder")
    if os.path.dirname(os.path.realpath(folder)) != base_real:
        raise ValueError("that folder is not inside the benchmark folder")
    if not (folder / MANIFEST).is_file():
        raise ValueError("not a run folder")
    shutil.rmtree(folder)


# --- exports ----------------------------------------------------------------------------------------------------------------------------------------------------------------

CSV_ID_COLUMNS = ("run_id", "model_id", "setup_id", "profile", "kind")
CSV_REQUEST_COLUMNS = tuple(f for f in bc.RequestRecord(request_id="", level=0).to_row().keys())


def _csv_cell(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v                                          # a spreadsheet must not run a model's text as a formula
    return "" if v is None else v


def requests_csv(base, run_id: str) -> str:
    """The per-request CSV: `requests.jsonl` flattened, one row per request, with the manifest's ids repeated on every row. Unknown extra keys of a later version are not dropped."""
    folder = run_dir(base, run_id)
    manifest = read_manifest(folder)
    rows = read_jsonl(folder / REQUESTS)
    extra = sorted({k for r in rows for k in r} - set(CSV_REQUEST_COLUMNS))
    cols = CSV_ID_COLUMNS + CSV_REQUEST_COLUMNS + tuple(extra)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    ids = [manifest.get(c, "") for c in CSV_ID_COLUMNS]
    for r in rows:
        w.writerow([_csv_cell(v) for v in ids] + [_csv_cell(r.get(c)) for c in cols[len(ids):]])
    return buf.getvalue()


def full_export(base, run_id: str, now_epoch_s: float) -> dict:
    """The whole folder as one JSON-able dict (what `load_run` returns, plus the schema version). The caller asks the person where to save it."""
    return dict(load_run(base, run_id, now_epoch_s), schema_version=bc.SCHEMA_VERSION)
