"""Benchmarks on another PC, this PC's side of the results (protocol section 8): bring a finished run home safely, then, and only then, let the Courier delete its copy.

For each finished run: list its files, download each into a TEMPORARY folder, check every size and checksum against the listing, check the result files themselves (names, schema version,
the run id inside the manifest), commit the folder into this PC's own `benchmarks/` store exactly like a local run (same files, same schema; the manifest, written by the device, names it),
and only then send the acknowledge with the checksum of every file. A crash between the commit and the acknowledge is safe: the next pass finds the run already stored (the same bytes),
and acknowledges. A run that does not pass is not committed and not acknowledged: the device keeps it.

Everything that comes from the device is data: file names are checked with `benchmarks_remote_plan` (only the five result names), the destination is built with `contain` from a run id this
PC validated, and nothing in a manifest is ever used as a path. The page's words are the design side's; this module gives states and a plain reason.
"""
import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import benchmarks_contract as bc
import benchmarks_remote_client as brc
import benchmarks_remote_plan as rp
import benchmarks_store as bs
import safe_paths

# The states the page shows for a remote run (data only; the words are the design side's).
SAVED, WAITING, COLLECTING, FAILED, NOT_RUN = "saved", "waiting_to_collect", "collecting", "failed_to_collect", "not_run"
TEMP_FOLDER = ".collecting"                       # inside the benchmark folder; not a valid run id, so the store never lists it
BOOK_NAME = "remote-jobs.json"
MAX_TEXT = 5000                                   # a string in a collected manifest is cut here when it is only a label; longer ones are refused

# Said to the person (the page words them).
FAIL_LISTING = "{pc} sent a list of results this PC doesn't trust, so nothing was saved. The results are still on {pc}."
FAIL_HASH = "A file from {pc} didn't arrive intact, so nothing was saved. The results are still on {pc}."
FAIL_RESULT = "The results from {pc} didn't look right, so nothing was saved. They are still on {pc}."
FAIL_NEWER = "The results from {pc} are from a newer version of MultiProxy, so nothing was saved. They are still on {pc}."
FAIL_HERE = "This PC already has different results with the same ID, so nothing was saved. The results are still on {pc}."
FAIL_DISK = "This PC couldn't save the results ({why}). They are still on {pc}."
FAIL_GONE = "{pc} no longer has those results."
FAIL_REFUSED = "{pc} wouldn't hand over the results: {why}"


class CollectFailed(Exception):
    """A run that was not saved. `code` is short, `reason` the plain line. `acknowledge_anyway` is never set: a failed run stays on the device."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_listing(listing: Any, run_id: str, pc: str = "That PC") -> list:
    """[(name, size, sha256)] from the device's list of a run, or CollectFailed. Only the five result names; no name twice; sizes within the caps; a manifest must be there."""
    bad = CollectFailed("bad_listing", FAIL_LISTING.format(pc=pc))
    if not isinstance(listing, dict) or listing.get("run_id") != run_id or not isinstance(listing.get("files"), list):
        raise bad
    files, seen, total = [], set(), 0
    for f in listing["files"]:
        if not isinstance(f, dict):
            raise bad
        name, size, sha = f.get("name"), f.get("size"), f.get("sha256")
        if not rp.valid_result_filename(name) or name in seen or isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= rp.MAX_RESULT_FILE_BYTES \
                or not isinstance(sha, str) or not rp.SHA256.match(sha):
            raise bad
        seen.add(name)
        total += size
        files.append((name, size, sha))
    if total > rp.MAX_RESULT_TOTAL_BYTES or bs.MANIFEST not in seen:
        raise bad
    return files


def check_result_files(folder: Path, run_id: str, pc: str = "That PC") -> dict:
    """The collected files themselves: the manifest parses, says it is schema 1 (or older), names THIS run, has a final status and short labels; the other JSON files parse; the line files are
    one object a line. Returns the manifest. Raises CollectFailed. (RecursionError: a deeply nested file is refused, not followed.)"""
    bad = CollectFailed("bad_result", FAIL_RESULT.format(pc=pc))
    try:
        manifest = json.loads((folder / bs.MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise bad
    if not isinstance(manifest, dict):
        raise bad
    version = manifest.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise bad
    if version > bc.SCHEMA_VERSION:
        raise CollectFailed("newer_version", FAIL_NEWER.format(pc=pc))
    if manifest.get("run_id") != run_id or manifest.get("status") not in bc.RUN_STATES_FINAL:
        raise bad
    dev = manifest.get("device")
    if isinstance(dev, dict) and any(isinstance(v, str) and len(v) > MAX_TEXT for v in dev.values()):
        raise bad
    for name in (bs.SUMMARY, bs.PROGRESS):
        p = folder / name
        if p.exists():
            try:
                if not isinstance(json.loads(p.read_text(encoding="utf-8")), dict):
                    raise bad
            except (OSError, ValueError, RecursionError):
                raise bad
    for name in (bs.REQUESTS, bs.SAMPLES):
        p = folder / name
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip() and not isinstance(json.loads(line), dict):
                            raise bad
            except (OSError, ValueError, RecursionError):
                raise bad
    return manifest


def _hashes_of(folder: Path) -> dict:
    return {n: _sha256(folder / n) for n in rp.RESULT_FILES if (folder / n).is_file()}


async def collect_run(client: "brc.BenchmarkClient", run_id: str, base) -> dict:
    """Bring one finished run home. Returns `{"state": saved, "acknowledged": bool, "already_here": bool}`. Raises CollectFailed (not saved, not acknowledged) or NoContact (the device is
    away: try again later; a partly downloaded file is kept and continued)."""
    pc = client.pc
    if not bc.valid_run_id(run_id) or not rp.safe_component(run_id):
        raise CollectFailed("bad_listing", FAIL_LISTING.format(pc=pc))
    try:
        listing = await client.run_listing(run_id)
    except brc.RemoteError as e:
        if isinstance(e, brc.NoContact):
            raise
        if e.code in ("result_expired", "already_collected", "unknown_run"):
            raise CollectFailed("gone", FAIL_GONE.format(pc=pc))
        raise CollectFailed("refused", FAIL_REFUSED.format(pc=pc, why=e.message))
    files = validate_listing(listing, run_id, pc)
    want = {n: sha for n, _, sha in files}
    base = Path(base)
    try:
        safe_paths.require_local_fixed(base)
        dest = rp.contain(base, run_id)
    except ValueError as e:
        raise CollectFailed("disk", FAIL_DISK.format(pc=pc, why=str(e)[:120]))
    if dest.exists():                                               # saved by an earlier pass (a crash before the acknowledge): the same bytes? then only the acknowledge is missing
        if await asyncio.to_thread(_hashes_of, dest) != want:
            raise CollectFailed("different_here", FAIL_HERE.format(pc=pc))
        return await _acknowledge(client, run_id, want, already_here=True)
    tmp = rp.contain(base, f"{TEMP_FOLDER}/{run_id}")               # one folder per run: a half download is continued by the next try
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for name, size, sha in files:
            part = rp.contain(base, f"{TEMP_FOLDER}/{run_id}/{name}")
            await client.download(run_id, name, part, size)
            if part.stat().st_size != size or await asyncio.to_thread(_sha256, part) != sha:
                part.unlink(missing_ok=True)
                raise CollectFailed("hash_mismatch", FAIL_HASH.format(pc=pc))
        for stray in tmp.iterdir():                                 # (nothing but the listed files is ever committed)
            if stray.name not in want:
                raise CollectFailed("hash_mismatch", FAIL_HASH.format(pc=pc))
        try:
            await asyncio.to_thread(check_result_files, tmp, run_id, pc)
        except CollectFailed:
            raise
        try:
            os.replace(tmp, dest)                                    # the commit: the folder appears whole or not at all
        except OSError as e:
            raise CollectFailed("disk", FAIL_DISK.format(pc=pc, why=e.strerror or "disk error"))
    except CollectFailed as e:
        if e.code != "disk":
            shutil.rmtree(tmp, ignore_errors=True)                   # a run that did not pass leaves nothing behind (the device still has it)
        raise
    except OSError as e:
        raise CollectFailed("disk", FAIL_DISK.format(pc=pc, why=e.strerror or "disk error"))
    return await _acknowledge(client, run_id, want, already_here=False)


async def _acknowledge(client: "brc.BenchmarkClient", run_id: str, hashes: dict, already_here: bool) -> dict:
    try:
        await client.acknowledge(run_id, hashes)
    except brc.NoContact:
        return {"state": SAVED, "acknowledged": False, "already_here": already_here}            # saved here; the acknowledge is sent on the next pass
    except brc.RemoteError as e:
        if e.code in ("already_collected", "unknown_run", "result_expired"):
            return {"state": SAVED, "acknowledged": True, "already_here": already_here}
        return {"state": SAVED, "acknowledged": False, "already_here": already_here, "note": e.message}
    return {"state": SAVED, "acknowledged": True, "already_here": already_here}


# --- the book: which jobs this PC still has results to collect from ---------------------------------------------------------------------------------------------------------------

class RemoteJobBook:
    """`remote-jobs.json` in the benchmark folder: for each job started on another PC, its key and what has been collected, so a restart of this PC (or a device that was away) can carry on.
    Private to this PC (it holds the job keys)."""

    def __init__(self, base):
        self.path = Path(base) / BOOK_NAME
        self.data: dict = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        safe_paths.write_private_json(self.path, self.data)

    def add(self, job_id: str, job_token: str, device_id: str = "", device_name: str = "", models: Optional[list] = None) -> dict:
        entry = self.data.setdefault(job_id, {"job_id": job_id, "job_token": job_token, "device_id": device_id, "device_name": device_name, "models": list(models or []), "runs": {}, "done": False})
        self.save()
        return entry

    def get(self, job_id: str) -> Optional[dict]:
        return self.data.get(job_id)

    def pending(self) -> list:
        return [e for e in self.data.values() if not e.get("done")]


def _set(entry: dict, run_id: str, state: str, reason: str = "") -> None:
    entry["runs"][run_id] = {"state": state, "reason": reason}


async def collect_job(client: "brc.BenchmarkClient", book: RemoteJobBook, job_id: str, base, on_state: Callable[[str, str, str], Any] = lambda run_id, state, reason: None) -> dict:
    """One pass over a job: ask its state (this is also the heartbeat), collect every finished run not yet saved, and return the picture the page needs:
    `{"job_state", "reason", "runs": {run_id: {state, reason}}, "models": [{id, state, run_id?}], "done": bool}`. A device that cannot be reached leaves its runs `waiting_to_collect`."""
    entry = book.get(job_id)
    if entry is None:
        raise KeyError(job_id)
    client.job_id, client.job_token = job_id, entry["job_token"]

    def note(run_id, state, reason=""):
        _set(entry, run_id, state, reason)
        on_state(run_id, state, reason)

    try:
        st = await client.status()
    except brc.NoContact:
        for r in entry["runs"].values():
            if r["state"] in (WAITING, COLLECTING):                                      # (a run saved here but not yet acknowledged stays `saved`: only the acknowledge is waiting)
                r["state"] = WAITING
        book.save()
        return {"job_state": entry.get("job_state"), "reason": "", "runs": dict(entry["runs"]), "models": _models(entry, None), "done": False, "contact": False, "status": None}
    except brc.RemoteError as e:
        if e.code in ("unknown_job", "result_expired"):
            for rid, r in entry["runs"].items():
                if r["state"] != SAVED:
                    note(rid, FAILED, FAIL_GONE.format(pc=client.pc))
            entry["done"] = True
            book.save()
            return {"job_state": "gone", "reason": e.code, "runs": dict(entry["runs"]), "models": _models(entry, None), "done": True, "contact": True, "status": None}
        raise
    entry["job_state"] = st.get("state")
    for run in st.get("runs", []):
        rid = run.get("run_id")
        if not rid:
            continue
        have = entry["runs"].get(rid, {})
        if have.get("state") == FAILED:
            continue                                                                     # (not tried again by itself: the same bytes would fail the same way; the device keeps the run)
        if have.get("state") == SAVED and have.get("acknowledged", False):
            continue
        if run.get("acknowledged") and have.get("state") == SAVED:
            have["acknowledged"] = True
            continue
        if not (run.get("collectable") or have.get("state") == SAVED or run.get("acknowledged")):
            continue
        if run.get("acknowledged") and not (Path(base) / rid).exists():
            continue                                                                     # (acknowledged long ago, and this PC's copy was deleted by the person: nothing to do)
        note(rid, COLLECTING)
        try:
            res = await collect_run(client, rid, base)
        except brc.NoContact:
            note(rid, WAITING)
            continue
        except CollectFailed as e:
            note(rid, FAILED, e.reason)
            continue
        _set(entry, rid, SAVED)
        entry["runs"][rid]["acknowledged"] = bool(res.get("acknowledged"))
        on_state(rid, SAVED, "")
    ended = st.get("state") in rp.ENDED_STATES or st.get("state") == rp.J_COLLECTED
    settled = all(r["state"] in (SAVED, FAILED) and (r["state"] == FAILED or r.get("acknowledged", False)) for r in entry["runs"].values())
    entry["done"] = bool(ended and settled and all((m.get("run_id") or "") in entry["runs"] or m.get("state") != "done" for m in st.get("models", [])))
    book.save()
    return {"job_state": st.get("state"), "reason": st.get("reason", ""), "runs": dict(entry["runs"]), "models": _models(entry, st), "done": entry["done"], "contact": True, "status": st}


def _models(entry: dict, st: Optional[dict]) -> list:
    """Each model of the plan with what became of it: its run's state here, `not_run` once the job has ended without one (never invented), else what the device says."""
    out = []
    for m in (st or {}).get("models", []) or [{"id": i} for i in entry.get("models", [])]:
        rid = m.get("run_id")
        if rid and rid in entry["runs"]:
            state = entry["runs"][rid]["state"]
        elif st is not None and st.get("state") in rp.ENDED_STATES + (rp.J_COLLECTED,) and not rid:
            state = NOT_RUN
        else:
            state = m.get("state") or "ready"
        out.append({"id": m.get("id"), "state": state, **({"run_id": rid} if rid else {})})
    return out


async def watch_and_collect(client: "brc.BenchmarkClient", book: RemoteJobBook, job_id: str, base, interval: float = brc.POLL_S, sleep: Callable[[float], Awaitable] = asyncio.sleep,
                            on_state: Callable[[str, str, str], Any] = lambda run_id, state, reason: None, stop: Callable[[], bool] = lambda: False) -> dict:
    """Poll the job (each poll is the heartbeat) and collect each model's run as soon as it finishes, until the job has ended and every run is settled. A device that goes quiet leaves
    the runs waiting; the loop keeps asking and collects when it is back. `stop()` ends the loop early (the person closed the page); it can be resumed with another call."""
    picture: dict = {}
    while True:
        picture = await collect_job(client, book, job_id, base, on_state)
        if picture["done"] or stop():
            return picture
        await sleep(interval)
