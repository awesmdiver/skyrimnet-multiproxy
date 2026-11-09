"""Benchmarks on another PC, this PC's side of the models (design decision: this PC holds the models and sends them; the device downloads nothing).

For a batch: make sure every model is on THIS PC (a missing one is downloaded here, once, by the Local device tab's own download, which the caller passes in), name each model by its bytes
(`sha256:<hex>:<size>`, hashed once and remembered), ask the device which of those it already has (the same bytes, never the same name), check there is room on both PCs for what has to be sent
plus a spare, start the job with a plan that names the models by their bytes, then send the missing ones with the existing transfer into the job's own folder on the device. A task of its
own keeps the device's 60-second lease renewed while the files move. The device checks each file's hash before it exists as a model, and deletes what it was sent when the test is over.
"""
import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

import benchmarks_remote_client as brc
import benchmarks_remote_collect as col
import benchmarks_remote_plan as rp
import benchmarks_store as bs
import file_transfer
import safe_paths

SPARE_MIN_BYTES = 2 * 1024 ** 3
SPARE_FRACTION = 0.10
SPEEDS_NAME = "transfer-speeds.json"
HEARTBEAT_S = 5.0
ALREADY_THERE, TO_SEND = "already_there", "sent"

# Said to the person (the page words them). Numbers are in GB.
REFUSE_NO_ROOM_DEVICE = "{pc} doesn't have room: the models need {need} free (with some spare) and it has {have}."
REFUSE_NO_ROOM_HERE = "This PC does not have enough space: it needs {need} free and only has {have}."
REFUSE_SEND_STOPPED = "Couldn't send the models to {pc}: {why}"
REFUSE_CANCELLED = "Sending to {pc} was cancelled."


class SendRefused(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


@dataclass
class Item:
    key: str                         # the page's name for the model (a catalogue id, or a path the person added)
    path: Path
    file: str                        # a plain .gguf name for the plan (letters, digits and . _ ( ) [ ] space -)
    content_id: str = ""
    sha256: str = ""
    size: int = 0
    delivery: str = TO_SEND          # `already_there` (the device has these bytes) or `sent`


def safe_model_file(name: str) -> str:
    """A name the device's validator accepts: odd characters become `_`, `.gguf` kept, cut to 150 characters."""
    stem = name[:-5] if name.lower().endswith(".gguf") else name
    stem = re.sub(r"[^A-Za-z0-9._() \[\]-]", "_", stem).strip(" .")[:150] or "model"
    if not re.match(r"^[A-Za-z0-9]", stem):
        stem = "m" + stem
    return stem + ".gguf"


def gb(n: float) -> str:
    return file_transfer.gb(n)


# --- how fast the last send was, per device (a small file in this PC's data folder) --------------------------------------------------------------------------------------------------

class SpeedBook:
    def __init__(self, base):
        self.path = Path(base) / SPEEDS_NAME
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def record(self, device_id: str, bytes_per_s: float, wall: Callable[[], float] = time.time) -> None:
        if not device_id or not bytes_per_s or bytes_per_s <= 0:
            return
        self.data[device_id] = {"bytes_per_s": float(bytes_per_s), "at": wall()}
        safe_paths.write_private_json(self.path, self.data)

    def last(self, device_id: str) -> Optional[float]:
        v = (self.data.get(device_id) or {}).get("bytes_per_s")
        return float(v) if isinstance(v, (int, float)) and v > 0 else None

    def estimate(self, device_id: str, nbytes: int) -> Optional[dict]:
        """`{"seconds", "bytes_per_s"}` for sending `nbytes` at the last speed measured to that device, or None when no speed is known (the page then shows only the size)."""
        bps = self.last(device_id)
        return {"seconds": int(nbytes / bps), "bytes_per_s": int(bps)} if bps and nbytes > 0 else None


# --- naming, comparing, room ------------------------------------------------------------------------------------------------------------------------------------------------------------

async def hash_items(models: dict, base) -> list:
    """`models`: {key: path} of files that are on THIS PC. Each gets its bytes' name; hashing a big file takes a while, so it is done once and remembered (`benchmarks/model-hashes.json`)."""
    out = []
    for key, path in models.items():
        path = Path(path)
        cid = await asyncio.to_thread(bs.model_id_for, path, base)
        _, sha, size = cid.split(":")
        out.append(Item(key=key, path=path, file=safe_model_file(path.name), content_id=cid, sha256=sha, size=int(size)))
    return out


def classify(items: list, device_models: dict) -> list:
    """Mark each item `already_there` when the device reports the same bytes. The name never decides."""
    have = {m.get("content_id") for m in (device_models or {}).get("models", []) if isinstance(m, dict)}
    for it in items:
        it.delivery = ALREADY_THERE if it.content_id in have else TO_SEND
    return items


def room_problem(items: list, device_free: Optional[int], here_free: Optional[int], pc: str) -> Optional[SendRefused]:
    """None when there is room, else the refusal with the numbers. What is sent plus a spare (the larger of 10% and 2 GB) must fit on the device's models drive; this PC needs the same spare for its
    own files (results, a model still to download). A free-space figure that could not be read counts as no room."""
    need = sum(i.size for i in items if i.delivery == TO_SEND)
    spare = max(int(need * SPARE_FRACTION), SPARE_MIN_BYTES)
    if need and (device_free is None or device_free < need + spare):
        return SendRefused("no_space_device", REFUSE_NO_ROOM_DEVICE.format(pc=pc, need=gb(need + spare), have=gb(device_free or 0)), need_bytes=need + spare, have_bytes=device_free or 0)
    if need and (here_free is None or here_free < spare):
        return SendRefused("no_space_here", REFUSE_NO_ROOM_HERE.format(need=gb(spare), have=gb(here_free or 0)), need_bytes=spare, have_bytes=here_free or 0)
    return None


async def ensure_local(keys: list, resolve_local: Callable[[str], Awaitable[Path]]) -> dict:
    """Make sure each model is on this PC. A model that is not here is downloaded by `resolve_local` (the Local device tab's own download, passed in), once per key in the batch."""
    out: dict = {}
    for key in dict.fromkeys(keys):
        out[key] = Path(await resolve_local(key))
    return out


# --- the batch ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------

async def _heartbeats(client: "brc.BenchmarkClient", every: float) -> None:
    """Keep the device's lease renewed while files move: a task of its own, never waiting on a transfer."""
    while True:
        await asyncio.sleep(every)
        try:
            await client.heartbeat()
        except (brc.NoContact, brc.RemoteError):
            pass


async def begin_job(client: "brc.BenchmarkClient", book: "col.RemoteJobBook", base, items: list, device_id: str = "", device_name: str = "",
                    here_free: Optional[Callable[[], int]] = None, **plan_options) -> dict:
    """Everything before the first byte moves: ask the device what it has, check there is room, start the job. Returns `{"started", "to_send": [items]}`; raises SendRefused (room) or
    RemoteError / OldCourier / NoContact (the device refused or is away). Nothing has been sent when this raises."""
    pc = client.pc
    inventory = await client.models()
    classify(items, inventory)
    here = (here_free or (lambda: shutil.disk_usage(Path(base)).free))()
    problem = room_problem(items, inventory.get("free_bytes"), here, pc)
    if problem:
        raise problem
    plan = brc.build_plan([{"id": i.content_id, "file": i.file, "sha256": i.sha256, "size": i.size} for i in items], client.owner_id, **plan_options)
    to_send = [i for i in items if i.delivery == TO_SEND]
    started = await client.start(plan, models_missing=bool(to_send))
    book.add(started["job_id"], started["job_token"], device_id, device_name, [i.content_id for i in items])
    return {"started": started, "to_send": to_send}


async def send_job_models(client: "brc.BenchmarkClient", items: list, to_send: list, started: dict, speeds: Optional[SpeedBook] = None, device_id: str = "",
                          on_progress: Callable[[dict], None] = lambda p: None, cancelled: Callable[[], bool] = lambda: False, heartbeat_s: float = HEARTBEAT_S, retry_for: float = 900.0) -> dict:
    """Send the models the device lacks into the started job, with a task of its own keeping the lease alive. Returns `{"job_id", "sent", "already_there", "bytes_sent", "seconds"}`; raises SendRefused."""
    pc = client.pc
    sender = client.sender()
    sent_bytes, t0 = 0, time.monotonic()
    done_before = 0
    beat = asyncio.create_task(_heartbeats(client, heartbeat_s))
    try:
        for it in to_send:
            if cancelled():
                raise file_transfer.SendCancelled()

            def progress(done, total, speed, it=it, before=done_before):
                on_progress({"model": it.key, "bytes_done": before + done, "bytes_total": sum(i.size for i in to_send), "speed_bps": int(speed)})
            await file_transfer.send_file(sender, {"sha256": it.sha256}, it.path, progress, cancelled, retry_for=retry_for, pause=0.2)
            done_before += it.size
            sent_bytes += it.size
    except file_transfer.SendCancelled:
        await _stop_job(client)
        raise SendRefused("cancelled", REFUSE_CANCELLED.format(pc=pc))
    except file_transfer.TransferError as e:
        await _stop_job(client)
        raise SendRefused("send_failed", REFUSE_SEND_STOPPED.format(pc=pc, why=e.message), status=e.status)
    finally:
        beat.cancel()
    seconds = time.monotonic() - t0
    if speeds is not None and sent_bytes and seconds > 0:
        speeds.record(device_id, sent_bytes / seconds)
    return {"job_id": started["job_id"], "sent": [i.key for i in to_send], "already_there": [i.key for i in items if i.delivery == ALREADY_THERE], "bytes_sent": sent_bytes, "seconds": seconds}


async def start_and_send(client: "brc.BenchmarkClient", book: "col.RemoteJobBook", base, items: list, device_id: str = "", device_name: str = "", speeds: Optional[SpeedBook] = None,
                         on_progress: Callable[[dict], None] = lambda p: None, cancelled: Callable[[], bool] = lambda: False, heartbeat_s: float = HEARTBEAT_S,
                         here_free: Optional[Callable[[], int]] = None, retry_for: float = 900.0, **plan_options) -> dict:
    """Start the job on the device and send the models it lacks. Returns `{"job_id", "sent": [keys], "already_there": [keys], "bytes_sent", "seconds"}`; raises SendRefused (nothing was started, or the
    job was cancelled on the device and what it had received is gone). The caller carries on with `col.watch_and_collect`."""
    begun = await begin_job(client, book, base, items, device_id, device_name, here_free=here_free, **plan_options)
    return await send_job_models(client, items, begun["to_send"], begun["started"], speeds, device_id, on_progress, cancelled, heartbeat_s, retry_for)


async def _stop_job(client: "brc.BenchmarkClient") -> None:
    try:
        await client.cancel()
    except (brc.NoContact, brc.RemoteError):
        pass
