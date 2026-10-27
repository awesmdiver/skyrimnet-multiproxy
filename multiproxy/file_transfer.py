"""Sending a big file from the main device to a Courier device (2026-10-01): the shared "download once" transfer.

Direction, and why. The Courier is the one that listens (the main device never opens a port to the home network). So the main device
*pushes*: it asks the Courier to start a transfer, then sends the file in chunks over plain HTTP requests it makes to the Courier's
door, with the pairing code on every one, exactly like every other call it makes to a Courier. The Courier writes each chunk to
`<name>.partial` and says how far it has got. Nothing here needs a second connection, a second port or anything new to be allowed in
a firewall.

  begin   POST /courier/v1/transfers              {kind, model, file, size, sha256, ...}  -> {id, offset, state}
  chunk   PUT  /courier/v1/transfers/<id>?offset=N   (raw bytes, at most CHUNK_BYTES)      -> {offset}   (409 + the real offset if N is wrong)
  finish  POST /courier/v1/transfers/<id>/finish                                          -> checks the SHA-256, renames into place
  cancel  DELETE /courier/v1/transfers/<id>                                               -> deletes the partial file

* **Resumable.** The partial file is the memory: `begin` for the same file answers with the size already there, and the sender carries on
  from that byte. A dropped connection, a laptop that fell asleep, or a restart of either program costs nothing already copied.
* **Verified.** The receiver hashes the whole file (streamed, in a thread) at `finish` and compares with the SHA-256 the sender
  promised; a mismatch deletes the partial and says so plainly.
* **Never whole in memory.** The sender reads one chunk at a time; the receiver streams each request body to disk.
* **Room first.** `begin` checks the free space on the receiving drive before any byte moves.
* **Half a file is never offered.** Until `finish` succeeds the file only exists as `<name>.partial` (+ a small `.partial.json`), which nothing
  scans for models; the final name appears by an atomic rename.
* **Only what is offered.** The receiver decides where a file goes from validated fields (`kind`, a model id, a plain file name), never from a path in
  the request; the sender (model_transfer.py) only ever sends files MultiProxy itself lists as its own.
Progress (bytes done, total, speed) is part of the Courier's report, so it reaches the dashboard over the live connection.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

CHUNK_BYTES = 8 * 1024 * 1024
SPACE_MARGIN_BYTES = 256 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._() \[\]-]{0,200}\.gguf$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
# Limits (security review S6): one file, everything arriving at once, and how many copies run at once. A model copy is at most a few tens of GB.
MAX_FILE_BYTES = 120 * 1024 ** 3
MAX_TOTAL_BYTES = 240 * 1024 ** 3
MAX_PARALLEL = 4
TOO_BIG = "That file is too big for a model copy, so MultiProxy refused it."
TOO_MANY = "{pc} is already receiving several files at once. Wait for one to finish, then try again."
TOO_MUCH = "{pc} is already receiving a lot of data. Wait for the copies that are running to finish, then try again."
BAD_FILE = "MultiProxy doesn't send files with that name."
UPDATE_KIND = "courier-update"            # a signed Courier update package (courier_update.py): one fixed file, at most MAX_UPDATE_BYTES
UPDATE_FILE = "courier-update.zip"
MAX_UPDATE_BYTES = 64 * 1024 ** 2


def valid_gguf_name(name) -> bool:
    """A plain model file name: letters, digits and . _ ( ) [ ] space - only, ending in .gguf, no folder in it."""
    return isinstance(name, str) and bool(_FILE.match(name))


class TransferError(Exception):
    """A plain, player-readable reason a transfer can't go on, with an HTTP status (and, for a wrong offset, the right one)."""

    def __init__(self, status: int, message: str, offset: Optional[int] = None):
        super().__init__(message)
        self.status, self.message, self.offset = status, message, offset


def gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def sha256_file(path: Path, on_bytes: Optional[Callable[[int], None]] = None) -> str:
    """SHA-256 of a file, one chunk at a time (blocking: call it in a thread)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    return h.hexdigest()


@dataclass
class Incoming:
    id: str
    kind: str
    group: str                      # the model this file belongs to
    file: str
    dest: Path
    size: int
    sha256: str
    role: str = "model"             # model | mmproj
    spec: dict = field(default_factory=dict)
    state: str = "receiving"        # receiving | verifying | done | error | cancelled
    done: int = 0
    error: str = ""
    speed: float = 0.0              # bytes per second, smoothed
    _t: float = 0.0
    _b: int = 0
    finished_at: float = 0.0
    lock: Optional[asyncio.Lock] = field(default=None, repr=False)

    @property
    def partial(self) -> Path:
        return self.dest.with_name(self.dest.name + ".partial")

    @property
    def sidecar(self) -> Path:
        return self.dest.with_name(self.dest.name + ".partial.json")


class TransferReceiver:
    """The Courier's side: accepts transfers from the main device. `resolve_dest(spec) -> Path` decides (and validates) where a file goes."""

    def __init__(self, resolve_dest: Callable[[dict], Path], log, changed: Callable[[], None] = lambda: None,
                 on_file_done: Callable[[Incoming], None] = lambda i: None, resolve_voice: Optional[Callable[[dict], Path]] = None,
                 on_voice_done: Callable[[Incoming], None] = lambda i: None, resolve_decision: Optional[Callable[[dict], Path]] = None,
                 on_decision_done: Callable[[Incoming], None] = lambda i: None, resolve_update: Optional[Callable[[dict], Path]] = None,
                 on_update_done: Callable[[Incoming], None] = lambda i: None):
        self.resolve_dest, self.log, self.changed, self.on_file_done = resolve_dest, log, changed, on_file_done
        self.resolve_voice, self.on_voice_done = resolve_voice, on_voice_done      # the voice files (engine builds, the voice model): see voice_courier.py
        self.resolve_decision, self.on_decision_done = resolve_decision, on_decision_done      # the local decision model's files (model, add-on, engine): see decision_courier.py
        self.resolve_update, self.on_update_done = resolve_update, on_update_done      # a signed Courier update package (courier_update.py): bytes only, one fixed file, checked there
        self.items: dict[str, Incoming] = {}
        self._last_signal = 0.0

    # ---- begin ------------------------------------------------------------------------------------------------
    def begin(self, spec: dict) -> dict:
        kind, group, file = str(spec.get("kind", "")), str(spec.get("model", "")), str(spec.get("file", ""))
        if kind == "voice":
            group = "higgs-voice"
            if not self.resolve_voice:
                raise TransferError(400, "MultiProxy doesn't send files with that name.")
        elif kind == "decision":
            group = "decision-model"
            if not self.resolve_decision:
                raise TransferError(400, "MultiProxy doesn't send files with that name.")
        elif kind == UPDATE_KIND:
            group, file = UPDATE_KIND, UPDATE_FILE          # one fixed name: nothing in the request chooses where an update package goes
            if not self.resolve_update:
                raise TransferError(400, "MultiProxy doesn't send files with that name.")
        elif kind != "model" or not _ID.match(group) or not _FILE.match(file):
            raise TransferError(400, BAD_FILE)
        custom = spec.get("custom")
        if custom is not None:                          # a model the player added by its own path: the names it asks to be added under are plain file names too
            if not isinstance(custom, dict) or not valid_gguf_name(custom.get("model_file")) or (custom.get("mmproj_file") and not valid_gguf_name(custom.get("mmproj_file"))):
                raise TransferError(400, BAD_FILE)
        try:
            size = int(spec.get("size"))
        except (TypeError, ValueError):
            raise TransferError(400, "The file's size is missing.")
        sha = str(spec.get("sha256", "")).lower()
        if size <= 0 or not _SHA.match(sha):
            raise TransferError(400, "The file's size or checksum is missing.")
        if size > (MAX_UPDATE_BYTES if kind == UPDATE_KIND else MAX_FILE_BYTES):
            raise TransferError(413, TOO_BIG)
        dest = Path(self.resolve_voice(spec) if kind == "voice" else self.resolve_decision(spec) if kind == "decision" else self.resolve_update(spec) if kind == UPDATE_KIND else self.resolve_dest(spec))      # raises TransferError (no folder yet, a bad name, no license...)
        tid = f"{group}__{file}"
        cur = self.items.get(tid)
        if cur and cur.state in ("receiving", "verifying"):
            return {"id": tid, "offset": cur.done, "state": cur.state}
        running = [i for i in self.items.values() if i.state in ("receiving", "verifying") and i.id != tid]
        if not (dest.exists() and dest.stat().st_size == size):                 # a file that is already here costs nothing
            if len(running) >= MAX_PARALLEL:
                raise TransferError(429, TOO_MANY)
            if sum(i.size for i in running) + size > MAX_TOTAL_BYTES:
                raise TransferError(429, TOO_MUCH)
        if dest.exists() and dest.stat().st_size == size:
            self.items[tid] = Incoming(tid, kind, group, file, dest, size, sha, str(spec.get("role") or "model"), spec, state="done", done=size, finished_at=time.time())
            self.changed()
            return {"id": tid, "offset": size, "state": "done"}
        inc = Incoming(tid, kind, group, file, dest, size, sha, str(spec.get("role") or "model"), spec)
        inc.lock = asyncio.Lock()
        have = self._resume_point(inc)
        need = size - have + SPACE_MARGIN_BYTES
        dest.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(dest.parent).free
        if free < need:
            raise TransferError(507, f"{{pc}} doesn't have room for this. It needs {gb(size - have + SPACE_MARGIN_BYTES)} more, but only has {gb(free)} free.")
        inc.done = have
        inc._t, inc._b = time.monotonic(), have
        inc.sidecar.write_text(json.dumps({"size": size, "sha256": sha}), encoding="utf-8")
        if not inc.partial.exists():
            inc.partial.touch()
        self.items[tid] = inc
        self.changed()
        return {"id": tid, "offset": have, "state": "receiving"}

    def _resume_point(self, inc: Incoming) -> int:
        """How much of an earlier, interrupted copy of exactly this file is already here (0 for anything else)."""
        try:
            meta = json.loads(inc.sidecar.read_text(encoding="utf-8"))
            have = inc.partial.stat().st_size
        except (OSError, ValueError):
            have, meta = 0, None
        if meta and meta.get("size") == inc.size and meta.get("sha256") == inc.sha256 and 0 <= have <= inc.size:
            return have
        for p in (inc.partial, inc.sidecar):
            p.unlink(missing_ok=True)
        return 0

    # ---- chunks -------------------------------------------------------------------------------------------------
    def _get(self, tid: str) -> Incoming:
        inc = self.items.get(tid)
        if not inc:
            raise TransferError(404, "That copy isn't running here. You might need to start it again.")
        return inc

    async def chunk(self, tid: str, offset: int, body: AsyncIterator[bytes]) -> int:
        inc = self._get(tid)
        if inc.state != "receiving":
            raise TransferError(409, "That copy isn't taking more data.", offset=inc.done)
        async with inc.lock:
            if offset != inc.done:
                raise TransferError(409, "The copy is in a different place than expected.", offset=inc.done)
            try:
                with open(inc.partial, "r+b") as f:
                    f.seek(offset)
                    async for piece in body:
                        if inc.done + len(piece) > inc.size:
                            raise TransferError(400, "More data arrived than the file's size.")
                        await asyncio.to_thread(f.write, piece)
                        inc.done += len(piece)
                        self._tick(inc)
            except OSError as e:
                inc.state, inc.error = "error", f"Couldn't write the file ({e.strerror or e})."
                self.changed()
                raise TransferError(500, inc.error)
        return inc.done

    def _tick(self, inc: Incoming) -> None:
        now = time.monotonic()
        if now - inc._t >= 1.0:
            rate = (inc.done - inc._b) / (now - inc._t)
            inc.speed = rate if not inc.speed else inc.speed * 0.6 + rate * 0.4
            inc._t, inc._b = now, inc.done
        if now - self._last_signal >= 0.5:
            self._last_signal = now
            self.changed()

    # ---- finish / cancel ----------------------------------------------------------------------------------------
    async def finish(self, tid: str) -> dict:
        inc = self._get(tid)
        if inc.state == "done":
            return {"state": "done"}
        if inc.done != inc.size:
            raise TransferError(409, "The file isn't all here yet.", offset=inc.done)
        inc.state = "verifying"
        self.changed()
        digest = await asyncio.to_thread(sha256_file, inc.partial)
        if digest != inc.sha256:
            for p in (inc.partial, inc.sidecar):
                p.unlink(missing_ok=True)
            inc.state, inc.error = "error", f"{inc.file} didn't pass its safety check, so the copy was thrown away. Try again."
            self.changed()
            raise TransferError(422, inc.error)
        os.replace(inc.partial, inc.dest)
        inc.sidecar.unlink(missing_ok=True)
        inc.state, inc.finished_at = "done", time.time()
        self.log.info(f"Received {inc.file} ({gb(inc.size)}) from the main device")
        try:
            (self.on_voice_done if inc.kind == "voice" else self.on_decision_done if inc.kind == "decision" else self.on_update_done if inc.kind == UPDATE_KIND
             else self.on_file_done)(inc)
        finally:
            self.changed()
        return {"state": "done"}

    def cancel(self, tid: str) -> None:
        inc = self.items.get(tid)
        if not inc:
            return
        if inc.state in ("receiving", "verifying"):
            inc.state = "cancelled"
            for p in (inc.partial, inc.sidecar):
                p.unlink(missing_ok=True)
            self.changed()

    # ---- what the report says ------------------------------------------------------------------------------------
    def voice_json(self) -> dict:
        """The voice files being copied right now: {state, done, total, percent, speed_bps, error} or {} when none."""
        files = [i for i in self.items.values() if i.kind == "voice" and i.state in ("receiving", "verifying", "error")]
        if not files:
            return {}
        total, done = sum(f.size for f in files), sum(f.done for f in files)
        err = next((f.error for f in files if f.state == "error"), "")
        return {"state": "error" if err else "running", "done": done, "total": total, "percent": int(done * 100 / total) if total else 0,
                "speed_bps": int(sum(f.speed for f in files)), "error": err}

    def decision_json(self) -> dict:
        """The decision model's files being copied right now, shaped like `voice_json`."""
        files = [i for i in self.items.values() if i.kind == "decision" and i.state in ("receiving", "verifying", "error")]
        if not files:
            return {}
        total, done = sum(f.size for f in files), sum(f.done for f in files)
        err = next((f.error for f in files if f.state == "error"), "")
        return {"state": "error" if err else "running", "done": done, "total": total, "percent": int(done * 100 / total) if total else 0,
                "speed_bps": int(sum(f.speed for f in files)), "error": err}

    def downloads_json(self) -> list:
        """One row per model being copied (its files added together), shaped like a download so the dashboard draws the same bar."""
        now = time.time()
        for tid in [t for t, i in self.items.items() if i.state in ("done", "cancelled") and now - (i.finished_at or now) > 30]:
            self.items.pop(tid, None)
        groups: dict[str, list[Incoming]] = {}
        for i in self.items.values():
            if i.state in ("receiving", "verifying", "error") and i.kind not in ("voice", "decision", UPDATE_KIND):      # (the voice and decision files report in their own blocks instead)
                groups.setdefault(i.group, []).append(i)
        out = []
        for g, files in groups.items():
            total = sum(f.size for f in files)
            done = sum(f.done for f in files)
            err = next((f.error for f in files if f.state == "error"), "")
            out.append({"model": g, "state": "error" if err else "running", "done": done, "total": total, "percent": int(done * 100 / total) if total else 0,
                        "progress_known": True, "bytes": True, "error": err, "source": "gaming-pc", "speed_bps": int(sum(f.speed for f in files))})
        return out


# --------------------------------------------------------------------------------------------------------------------
# The main device's side
# --------------------------------------------------------------------------------------------------------------------
class SendCancelled(Exception):
    pass


async def send_file(client, spec: dict, path: Path, on_progress: Callable[[int, int, float], None],
                    cancelled: Callable[[], bool], on_pause: Callable[[str], None] = lambda m: None,
                    retry_for: float = 900.0, pause: float = 2.0) -> None:
    """Push `path` to the Courier behind `client` (a courier_client.CourierClient), resuming wherever the Courier already has it.
    A connection that drops (or a Courier that is asleep) pauses and tries again by itself for `retry_for` seconds."""
    size = path.stat().st_size
    spec = {**spec, "size": size}
    started = time.monotonic()
    last_ok = time.monotonic()
    speed, t0, b0 = 0.0, time.monotonic(), 0
    fh = None
    try:
        while True:
            if cancelled():
                raise SendCancelled()
            try:
                info = await client.transfer_begin(spec)
                tid, offset = info["id"], int(info["offset"])
                if info.get("state") == "done":
                    on_progress(size, size, 0.0)
                    return
                if fh is None:
                    fh = open(path, "rb")
                while offset < size:
                    if cancelled():
                        await _quiet(client.transfer_cancel(tid))
                        raise SendCancelled()
                    fh.seek(offset)
                    data = await asyncio.to_thread(fh.read, min(CHUNK_BYTES, size - offset))
                    try:
                        offset = await client.transfer_chunk(tid, offset, data)
                    except TransferError as e:
                        if e.status == 409 and e.offset is not None:
                            offset = e.offset                       # the Courier says where it really is: carry on from there
                            continue
                        raise
                    last_ok = time.monotonic()
                    now = time.monotonic()
                    if now - t0 >= 1.0:
                        rate = (offset - b0) / (now - t0)
                        speed = rate if not speed else speed * 0.6 + rate * 0.4
                        t0, b0 = now, offset
                    on_progress(offset, size, speed)
                await client.transfer_finish(tid)
                on_progress(size, size, speed)
                return
            except TransferError:
                raise
            except SendCancelled:
                raise
            except Exception as e:                                  # a dropped connection, a sleeping laptop: wait and carry on
                if time.monotonic() - last_ok > retry_for:
                    raise TransferError(502, "The copy stopped because the other device didn't answer. Start it again when it's back.")
                on_pause(str(getattr(e, "message", e)))
                await asyncio.sleep(pause)
    finally:
        if fh:
            fh.close()


async def _quiet(coro) -> None:
    try:
        await coro
    except Exception:
        pass
