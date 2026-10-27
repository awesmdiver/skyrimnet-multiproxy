"""Sending AI models from the main device to a Courier device (2026-10-01): "download once". Part 2 of the transfer in file_transfer.py.

MultiProxy offers exactly its own installed local models (and each one's vision file), never a path from a request: a model is named by id,
the files come from `LocalModels.installed()`. A catalog model's files have known sizes and SHA-256 (from the catalog); a model the player
added by its own path gets its checksum computed once (a thread, one chunk at a time) and remembered while the file is unchanged.
"""
import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import file_transfer
import local_models
import other_pcs

_sha_cache: dict = {}      # (path, size, mtime_ns) -> sha256


def _checksum(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _sha_cache:
        _sha_cache[key] = file_transfer.sha256_file(path)
    return _sha_cache[key]


def offered_files(m: "local_models.Installed") -> list[dict]:
    """The files of one installed model, as the Courier is told about them: [{role, file, path, size, sha256 | None (compute)}]."""
    spec = local_models.CATALOG_BY_ID.get(m.id)
    files = []
    if spec and not m.custom:
        by_role = {f["role"]: f for f in spec["files"]}
        for role, p in (("model", m.path), ("mmproj", m.mmproj)):
            if p and role in by_role:
                f = by_role[role]
                files.append({"role": role, "file": f["file"], "path": Path(p), "size": f["size"], "sha256": f["sha256"]})
    else:
        for role, p in (("model", m.path), ("mmproj", m.mmproj)):
            if p:
                files.append({"role": role, "file": Path(p).name, "path": Path(p), "size": Path(p).stat().st_size, "sha256": None})
    return files


@dataclass
class SendJob:
    pc: str
    model: str
    name: str
    total: int
    done: int = 0
    speed: float = 0.0
    state: str = "sending"          # sending | paused | error | done | cancelled
    message: str = ""
    cancel: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_json(self) -> dict:
        return {"model": self.model, "name": self.name, "state": self.state, "message": self.message, "done": self.done, "total": self.total,
                "percent": int(self.done * 100 / self.total) if self.total else 0, "speed_bps": int(self.speed)}


class ModelSender:
    """What's on this device that a Courier device could be sent, and the sends in flight."""

    def __init__(self, local_svc, remote_svc, log):
        self.local, self.remote, self.log = local_svc, remote_svc, log
        self.jobs: dict[tuple[str, str], SendJob] = {}

    # ---- what can be sent ------------------------------------------------------------------------------------
    def gaming_models(self) -> list[dict]:
        out = []
        for m in self.local.installed():
            try:
                total = m.size
            except OSError:
                continue
            out.append({"id": m.id, "name": m.name, "size_bytes": total, "vision": bool(m.mmproj), "custom": m.custom})
        return out

    def _installed(self, model_id: str) -> "local_models.Installed":
        m = next((x for x in self.local.installed() if x.id == model_id), None)
        if not m:
            raise other_pcs.OtherPcsError(404, "This device doesn't have that model, so it can't be sent.")
        return m

    def jobs_for(self, pc: str) -> list[dict]:
        now = time.time()
        for k in [k for k, j in self.jobs.items() if j.state in ("done", "cancelled") and now - getattr(j, "_ended", now) > 60]:
            self.jobs.pop(k, None)
        return [j.to_json() for (p, _), j in self.jobs.items() if p == pc]

    # ---- start / cancel ---------------------------------------------------------------------------------------
    def start(self, rid: str, model_id: str) -> SendJob:
        r = self.remote._courier(rid)
        m = self._installed(model_id)
        live = self.jobs.get((r.id, m.id))
        if live and live.state in ("sending", "paused"):
            return live
        files = offered_files(m)
        if not files:
            raise other_pcs.OtherPcsError(404, "That model's files can't be found on this device.")
        job = SendJob(r.id, m.id, m.name, sum(f["size"] for f in files))
        self.jobs[(r.id, m.id)] = job
        job.task = asyncio.create_task(self._run(r, m, files, job))
        return job

    def cancel(self, rid: str, model_id: str) -> None:
        job = self.jobs.get((self.remote._courier(rid).id, model_id))
        if job and job.state in ("sending", "paused"):
            job.cancel = True

    async def _run(self, r, m, files: list[dict], job: SendJob) -> None:
        base = 0
        try:
            names = [f["file"] for f in files]
            for f in files:
                sha = f["sha256"] or await asyncio.to_thread(_checksum, f["path"])
                spec = {"kind": "model", "model": m.id, "file": f["file"], "role": f["role"], "sha256": sha, "size": f["size"]}
                if m.custom:
                    spec["custom"] = {"name": m.name, "model_file": next(x["file"] for x in files if x["role"] == "model"),
                                      "mmproj_file": next((x["file"] for x in files if x["role"] == "mmproj"), None)}

                def progress(done, total, speed, base=base):
                    job.done, job.speed, job.state, job.message = base + done, speed, "sending", ""

                def paused(msg):
                    job.state, job.message = "paused", f"Waiting for {r.label} to answer. It will finish on its own."

                await file_transfer.send_file(r.client, spec, f["path"], progress, lambda: job.cancel, on_pause=paused)
                base += f["size"]
            job.done, job.state, job.message = job.total, "done", ""
            self.log.info(f"Sent {m.name} to {r.label} ({names})")
        except file_transfer.SendCancelled:
            job.state, job.message = "cancelled", ""
        except file_transfer.TransferError as e:
            job.state, job.message = "error", e.message.replace("{pc}", r.id)
            self.log.warning(f"Sending {m.name} to {r.label} failed: {job.message}")
        except Exception as e:                                               # never leave a job saying "sending" forever
            job.state, job.message = "error", f"The copy to {r.label} stopped ({e})."
            self.log.warning(f"Sending {m.name} to {r.label} failed: {e}")
        job._ended = time.time()
        try:
            self.remote._apply_report(r, await r.client.fetch_report())      # show the model as soon as it's there
        except Exception:
            pass
