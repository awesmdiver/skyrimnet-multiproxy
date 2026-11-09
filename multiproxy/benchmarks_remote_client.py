"""Benchmarks on another PC, this PC's side (minimal, no page yet): build a plan, start it on a paired Courier, watch it, cancel it.

    python benchmarks_remote_client.py --address 192.168.1.20 --code ABCD-2345 --model qwen3-4b [--level 1 --level 4] [--while-testing stop]

Polling the status IS the heartbeat: the Courier stops the test if it hears nothing from this PC for 60 seconds, so `watch` polls every few seconds and tells the caller when contact is lost.
It talks only to a Courier this PC is paired with (home network only, the same rule as every Courier call), and never follows a redirect. Protocol: docs/roadmap/benchmarks-remote-protocol.md.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

import aiohttp

import benchmarks_remote_plan as rp
import courier_client
import other_pcs

OWNER_HEADER = "X-MultiProxy-Instance"
TOKEN_HEADER = "X-Benchmark-Job-Token"
POLL_S = 5.0                                             # well inside the Courier's 60 s lease
LOST_AFTER_S = 20.0                                      # no answer for this long: tell the caller contact is lost (the Courier is set to stop at 60 s)

# Said to the person (the page, a later step, words these).
OLD_COURIER = "{pc} needs an update before it can run benchmarks."
LOST_CONTACT = "Lost contact with {pc}. It is set to stop the test after 60 seconds without hearing from this PC."


class RemoteError(Exception):
    def __init__(self, status: int, code: str, message: str, field: Optional[str] = None, extra: Optional[dict] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field, self.extra = status, code, message, field, extra or {}


class OldCourier(RemoteError):
    def __init__(self, missing: list, message: str):
        super().__init__(426, "missing_capability", message, extra={"missing": list(missing)})
        self.missing = list(missing)


class NoContact(RemoteError):
    def __init__(self, message: str = "The Courier didn't answer."):
        super().__init__(502, "no_contact", message)


def instance_id(load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None]) -> str:
    """This PC's permanent id for benchmark jobs, made once and kept in its settings (the Courier records it, security review SF4)."""
    cfg = load_cfg()
    value = cfg.get("instance_id")
    if not (isinstance(value, str) and rp.OWNER_ID.match(value)):
        value = uuid.uuid4().hex
        cfg["instance_id"] = value
        save_cfg(cfg)
    return value


def build_plan(models: list, owner_id: str, request_id: Optional[str] = None, **options) -> dict:
    """A plan body. `models` are model IDs from the Courier's own list. `options` are any of the plan's other fields (levels, requests_per_level, while_testing, settings, ...); the Courier's
    validator refuses what is not allowed, so nothing is checked twice here."""
    return {"protocol_version": rp.PROTOCOL_VERSION, "request_id": request_id or uuid.uuid4().hex, "owner_id": owner_id, "kind": rp.KIND_CHAT,
            "models": [{"id": m} if isinstance(m, str) else dict(m) for m in models], **options}


def check_report(report: Any, plan: dict, pc: str = "That PC", collect: bool = True, models_missing: bool = False) -> None:
    """Raise OldCourier when the Courier's report lacks a capability this plan needs (the build number alone is not a contract). `models_missing`: the plan needs models sent, so the Courier must be
    able to receive them."""
    block = ((report or {}).get("courier") or {}).get("benchmarks") if isinstance(report, dict) else None
    missing = rp.missing_capabilities(block, rp.required_capabilities(plan, models_missing=models_missing, collect=collect))
    if missing:
        raise OldCourier(missing, OLD_COURIER.format(pc=pc))


class BenchmarkClient:
    def __init__(self, address: str, code: str, owner_id: str, name: str = "", allow_loopback: bool = False, pc: str = "That PC", clock: Callable[[], float] = time.monotonic):
        addr = other_pcs.parse_address(address, 8110)
        other_pcs.check_host_allowed(addr.host, allow_loopback=allow_loopback)
        self.base, self.code, self.owner_id = addr.base, code, owner_id
        self.name = name or other_pcs.short_hostname()
        self.pc, self.clock = pc, clock
        self.job_id: Optional[str] = None
        self.job_token: Optional[str] = None
        self.last_contact: Optional[float] = None

    def _headers(self, with_job: bool = True) -> dict:
        h = {"Authorization": f"Bearer {self.code}", "X-Courier-Client": self.name, OWNER_HEADER: self.owner_id}
        if with_job and self.job_token:
            h[TOKEN_HEADER] = self.job_token
        return h

    async def _call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 15, with_job: bool = True) -> Any:
        try:
            async with courier_client._session(aiohttp.ClientTimeout(total=timeout)) as s:
                async with s.request(method, self.base + path, json=body, headers=self._headers(with_job), allow_redirects=False) as r:
                    courier_client._no_redirect(r)
                    try:
                        data = await r.json(content_type=None)
                    except ValueError:
                        data = None
                    if r.status >= 400:
                        err = (data or {}).get("error") if isinstance(data, dict) else None
                        err = err if isinstance(err, dict) else {}
                        raise RemoteError(r.status, str(err.get("code") or "error"), str(err.get("message") or f"The Courier answered with an error ({r.status})."), err.get("field"),
                                          {k: v for k, v in err.items() if k not in ("message", "type", "code", "field")})
                    self.last_contact = self.clock()
                    return data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise NoContact() from e

    async def report(self) -> dict:
        return await self._call("GET", "/courier/v1/report", with_job=False)

    async def start(self, plan: dict, retries: int = 3, models_missing: bool = False) -> dict:
        """Start the plan. Refuses at once (OldCourier) when the Courier lacks a needed capability. A lost answer is safe to retry: the same request id gives back the same job."""
        check_report(await self.report(), plan, self.pc, models_missing=models_missing)
        last: Optional[Exception] = None
        for attempt in range(max(1, retries)):
            try:
                answer = await self._call("POST", "/courier/v1/benchmarks", plan, timeout=30, with_job=False)
            except NoContact as e:
                last = e
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            self.job_id, self.job_token = answer["job_id"], answer["job_token"]
            return answer
        raise last or NoContact()

    def _job_path(self, path: str) -> str:
        return f"{path}?job={self.job_id}" if self.job_id else path

    async def status(self) -> dict:
        return await self._call("GET", self._job_path("/courier/v1/benchmarks/status"))

    async def heartbeat(self) -> dict:
        return await self._call("POST", "/courier/v1/benchmarks/heartbeat", {"job_id": self.job_id})

    async def cancel(self) -> dict:
        return await self._call("POST", "/courier/v1/benchmarks/cancel", {"job_id": self.job_id})

    # ---- models: what the device has, and sending the ones it lacks (a job's own routes: owner and key on every call) --------------------------------------------------------------------
    async def models(self) -> dict:
        """The models the device has, by their bytes (`content_id`), and the free space on its models drive."""
        return await self._call("GET", "/courier/v1/benchmarks/models", timeout=3600, with_job=False)

    def sender(self) -> "JobSender":
        """An object `file_transfer.send_file` can drive: the existing begin / chunk / finish transfer, aimed at this job's own routes."""
        return JobSender(self)

    # ---- results (the files stay on the Courier until this PC has them safely and says so) ----------------------------------------------------------------------------------
    async def run_listing(self, run_id: str) -> dict:
        return await self._call("GET", f"/courier/v1/benchmarks/runs/{run_id}")

    async def acknowledge(self, run_id: str, hashes: dict) -> dict:
        """Tell the Courier it may delete its copy: only ever sent after the run is committed here, with the checksum of every file received."""
        return await self._call("DELETE", f"/courier/v1/benchmarks/runs/{run_id}", {"hashes": dict(hashes)}, timeout=30)

    async def download(self, run_id: str, name: str, dest, size: int, chunk_timeout: float = 30.0) -> None:
        """Fetch one result file into `dest` (a temporary file). A partial `dest` from an earlier try is continued with a Range request. Raises NoContact when the Courier goes away; the partial
        file is kept so the next try resumes. Size and checksum are checked by the caller."""
        from pathlib import Path
        dest = Path(dest)
        have = dest.stat().st_size if dest.exists() else 0
        if have > size:
            dest.unlink()
            have = 0
        if have == size:
            dest.touch(exist_ok=True)                                                # (an empty file is a file too)
            return
        headers = self._headers()
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            async with courier_client._session(aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=chunk_timeout)) as sess:
                async with sess.get(self.base + f"/courier/v1/benchmarks/runs/{run_id}/files/{name}", headers=headers, allow_redirects=False) as r:
                    courier_client._no_redirect(r)
                    if r.status >= 400:
                        try:
                            err = ((await r.json(content_type=None)) or {}).get("error") or {}
                        except Exception:
                            err = {}
                        raise RemoteError(r.status, str(err.get("code") or "error"), str(err.get("message") or f"The Courier answered with an error ({r.status})."), err.get("field"))
                    mode = "ab" if (have and r.status == 206) else "wb"          # (a Courier that ignored the range sent the whole file again)
                    with open(dest, mode) as f:
                        async for part in r.content.iter_chunked(1024 * 1024):
                            f.write(part)
            self.last_contact = self.clock()
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:               # (this PC's own disk trouble is an OSError and is not a lost connection)
            raise NoContact() from e

    async def watch(self, on_status: Callable[[dict], Any] = lambda s: None, on_lost: Callable[[float], Any] = lambda secs: None, interval: float = POLL_S,
                    lost_after: float = LOST_AFTER_S, sleep: Callable[[float], Awaitable] = asyncio.sleep) -> dict:
        """Poll until the job has ended; return the last status. Each poll renews the Courier's lease. When nothing has been heard for `lost_after` seconds, `on_lost(seconds)` is called on
        every poll that still gets no answer, and the first answer after that is delivered as usual."""
        while True:
            try:
                st = await self.status()
            except NoContact:
                silent = self.clock() - (self.last_contact if self.last_contact is not None else self.clock())
                if self.last_contact is None:
                    self.last_contact = self.clock()
                if silent >= lost_after:
                    on_lost(silent)
                await sleep(interval)
                continue
            r = on_status(st)
            if asyncio.iscoroutine(r):
                await r
            if st.get("state") in rp.ENDED_STATES:
                return st
            await sleep(interval)


class JobSender:
    """The three calls of the existing transfer (`begin`, `chunk`, `finish`) plus `cancel`, on a job's routes. Errors become `file_transfer.TransferError` (a wrong offset carries the right one); a
    connection that cannot be made is left as it is so the sender pauses and tries again."""

    def __init__(self, client: "BenchmarkClient"):
        self.c = client

    async def _call(self, method: str, path: str, json_body=None, data=None, timeout: float = 30) -> dict:
        import file_transfer
        base = f"/courier/v1/benchmarks/jobs/{self.c.job_id}/models"
        async with courier_client._session(aiohttp.ClientTimeout(total=timeout)) as sess:
            async with sess.request(method, self.c.base + base + path, json=json_body, data=data, headers=self.c._headers(), allow_redirects=False) as r:
                courier_client._no_redirect(r)
                body = await r.json(content_type=None)
                if r.status >= 400:
                    err = (body or {}).get("error") if isinstance(body, dict) else None
                    err = err if isinstance(err, dict) else {}
                    raise file_transfer.TransferError(r.status, str(err.get("message") or f"The Courier answered with an error ({r.status})."), offset=err.get("offset"))
                self.c.last_contact = self.c.clock()
                return body

    async def transfer_begin(self, spec: dict) -> dict:
        return await self._call("POST", "", json_body={"sha256": spec["sha256"], "size": spec["size"]})

    async def transfer_chunk(self, tid: str, offset: int, data: bytes) -> int:
        return int((await self._call("PUT", f"/{tid}?offset={offset}", data=data, timeout=120))["offset"])

    async def transfer_finish(self, tid: str) -> dict:
        return await self._call("POST", f"/{tid}/finish", json_body={}, timeout=3600)

    async def transfer_cancel(self, tid: str) -> None:
        await self._call("DELETE", f"/{tid}")


async def _main(args) -> int:
    owner = args.owner or uuid.uuid4().hex
    client = BenchmarkClient(args.address, args.code, owner, allow_loopback=args.loopback)
    options = {"levels": args.level or [1, 4, 8], "while_testing": args.while_testing}
    plan = build_plan(args.model, owner, **options)
    try:
        started = await client.start(plan)
        print(f"job {started['job_id']} {started['state']}")
        final = await client.watch(lambda st: print(st["state"], (st.get("step") or {}).get("name") or ""), lambda s: print(LOST_CONTACT.format(pc="the Courier")))
        print(json.dumps({k: final.get(k) for k in ("state", "reason", "runs")}, indent=1))
        return 0 if final.get("state") == rp.J_FINISHED else 1
    except RemoteError as e:
        print(f"{e.code}: {e.message}")
        return 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Start a benchmark on a paired Courier and watch it (developer entry point).")
    ap.add_argument("--address", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--model", action="append", required=True, help="a model id from the Courier's own list")
    ap.add_argument("--level", action="append", type=int)
    ap.add_argument("--while-testing", default="start_normal", choices=list(rp.WHILE_TESTING))
    ap.add_argument("--owner")
    ap.add_argument("--loopback", action="store_true")
    sys.exit(asyncio.run(_main(ap.parse_args())))
