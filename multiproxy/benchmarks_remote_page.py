"""The Benchmarks page, for a test on another PC (design/mockup-benchmarks-remote.html, revision 5): what the page asks of this PC's side. It adds no protocol: it calls the pieces of steps 1 to 4
(`benchmarks_remote_client`, `_send`, `_collect`, `_plan`) and shapes their answers for the screens.

  GET  /config/benchmarks/remote/devices                      the "Test on" list: the paired Couriers, each ready / offline / needs an update, with its card and what it runs
  GET  /config/benchmarks/remote/plan?device=<id>             for each model in the list: is it already on that device (the same bytes), what will happen, does it fit that card; the free space,
                                                              the last send speed, the services the device runs (for "While testing") and whether it has a battery
  POST /config/benchmarks/remote/start                        {device, keys, setup, whileTesting, keep, takeOver}  ->  {"ok": true} or {"ok": false, "code", ...} (a refusal the page words)
  GET  /config/benchmarks/remote/state                        the live panel: phase, sending progress, readings, lost contact, and what became of each result
  POST /config/benchmarks/remote/cancel                       stop the test (the device stops its engine and deletes what it was sent)

The page never sends a path: models are named by their key in the list, and the file is found here. Refusals are codes with numbers; the words are the page's (mockup section 6).
"""
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import benchmarks_contract as bc
import benchmarks_remote_client as brc
import benchmarks_remote_collect as col
import benchmarks_remote_plan as rp
import benchmarks_remote_send as snd
import benchmarks_store as bs
import other_pcs
import safe_paths

POLL_S = 2.0
MAX_BODY_BYTES = 64 * 1024                 # the page's own POST bodies are small; anything bigger is refused before it is read in full
MAX_KEYS = 64
LOST_AFTER_S = 15.0                      # no answer for this long: the page says contact is lost (the device stops after 60 s)
SLEEP_REASON = "slept"
INSTANCE_FILE = "remote-instance.json"
WHILE_TESTING = ("start_normal", "keep", "stop")

# Codes the page words (mockup sections 1 and 6).
OFFLINE, NEEDS_UPDATE, READY = "offline", "needs_update", "ready"


def instance_id(base) -> str:
    """This PC's permanent id for jobs on other PCs, made once and kept in the benchmark folder."""
    import json
    path = Path(base) / INSTANCE_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("id")
        if isinstance(value, str) and rp.OWNER_ID.match(value):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    value = uuid.uuid4().hex
    safe_paths.write_private_json(path, {"id": value})
    return value


def _first_card(report: Optional[dict]) -> dict:
    g = ((report or {}).get("gpus") or [None])[0]
    return g if isinstance(g, dict) else {}


def _services(report: Optional[dict]) -> list:
    """What the device runs for the person, from its own report: `[{id, name, running, mb}]` (the voice engine and the quick-decision model, when installed)."""
    out = []
    for sid, key, default in (("service:voice", "voice", "Voice"), ("service:decision", "decisions", "Quick-decision model")):
        s = (report or {}).get(key)
        if not isinstance(s, dict) or s.get("installed") is False or s.get("state") in (None, "not_installed"):
            continue
        out.append({"id": sid, "name": str(s.get("modelName") or default)[:60] if sid == "service:voice" else default,
                    "running": s.get("state") in ("ready", "getting_ready", "too_slow", "starting"), "mb": s.get("gpuMemoryMb")})
    return out


def device_row(r, now: float) -> dict:
    """One Courier as the "Test on" list shows it."""
    rep = r.report or {}
    card = _first_card(rep)
    block = (rep.get("courier") or {}).get("benchmarks")
    missing = rp.missing_capabilities(block, rp.required_capabilities({"while_testing": "stop"}, models_missing=True))
    if r.online is not True:
        state = OFFLINE
    elif other_pcs.courier_is_older(rep) or missing:
        state = NEEDS_UPDATE
    else:
        state = READY
    build = other_pcs.courier_build_of(rep)
    return {"id": r.id, "name": r.label, "state": state, "card": card.get("name"), "vramMb": card.get("memory_total_mb"), "vendor": card.get("vendor"),
            "runs": [s["id"] for s in _services(rep)], "courier": other_pcs.courier_version_of(build) if build is not None else None,
            "hasBattery": bool((rep.get("power") or {}).get("has_battery")), "agoS": int(now - r.checked_at) if r.checked_at and state == OFFLINE else None}


def refusal(code: str, device: str, message: str = "", **extra) -> dict:
    return {"ok": False, "code": code, "device": device, "detail": message, **extra}


class RemoteRun:
    """One test on another PC, from the press of Start to the last result saved here. The page reads `state()`."""

    def __init__(self, page: "RemotePage", remote, client: "brc.BenchmarkClient", items: list, begun: dict, options: dict):
        self.page, self.remote, self.client, self.items = page, remote, client, items
        self.begun, self.options = begun, options
        self.job_id = begun["started"]["job_id"]
        self.to_send = begun["to_send"]
        self.started_wall = page.wall()
        self.sending: Optional[dict] = None
        self.sent: Optional[dict] = None
        self.stop = False
        self.error: Optional[dict] = None
        self.picture: dict = {}
        self.status: Optional[dict] = None
        self.last_contact = page.clock()
        self.finished = False
        self.task: Optional[asyncio.Task] = None
        self.estimate = begun.get("estimate")

    # -- what the page reads --
    def state(self) -> dict:
        now = self.page.clock()
        st = self.status or {}
        step = st.get("step") or {}
        lost_for = now - self.last_contact
        saved = [r for r in (self.picture.get("runs") or {}).values() if r.get("state") == col.SAVED]
        phase = self._phase(st)
        send = None
        if self.sending and phase == "sending":
            done, total, speed = self.sending["bytes_done"], self.sending["bytes_total"], self.sending["speed_bps"]
            send = {"model": self.sending.get("model"), "bytesDone": done, "bytesTotal": total, "speedBps": speed, "secondsLeft": int((total - done) / speed) if speed else None}
        names = {i.key: i.file for i in self.items}
        by_id = {i.content_id: i.file for i in self.items}
        return {"running": not self.finished, "device": {"id": self.remote.id, "name": self.remote.label}, "jobId": self.job_id, "phase": phase, "sending": send, "stopRequested": self.stop,
                "modelIndex": step.get("model_index"), "modelsTotal": step.get("models_total") or len(self.items), "modelName": (step.get("model_name") or ""),
                "sendIndex": ([i.key for i in self.to_send].index(self.sending["model"]) + 1) if self.sending and self.sending.get("model") in [i.key for i in self.to_send] else None,
                "sendTotal": len(self.to_send), "sendName": names.get((self.sending or {}).get("model"), ""),
                "stepsDone": step.get("steps_done"), "stepsTotal": step.get("steps_total"), "step": step.get("name"), "engineBusy": step.get("engine_busy"),
                "readings": st.get("readings"), "services": _services(self.remote.report), "power": st.get("power"),
                "batchStartedEpochS": self.started_wall, "modelStartedEpochS": step.get("model_started_epoch_s"), "serverNowEpochS": self.page.wall(),
                "lost": {"seconds": int(lost_for), "stepsDone": step.get("steps_done"), "stepsTotal": step.get("steps_total"), "saved": len(saved)} if lost_for >= LOST_AFTER_S and not self.finished else None,
                "jobState": self.picture.get("job_state") or st.get("state"), "reason": self.picture.get("reason") or st.get("reason") or "", "message": st.get("message") or "",
                "runs": self.picture.get("runs") or {}, "models": [dict(m, name=by_id.get(m.get("id"), "")) for m in (self.picture.get("models") or [])], "error": self.error,
                "interruptedModel": next((x.get("model_file") for x in (st.get("runs") or []) if x.get("state") == "interrupted"), ""), "estimate": self.estimate}

    def _phase(self, st: dict) -> str:
        if self.error or (st.get("state") in rp.ENDED_STATES + (rp.J_COLLECTED,)):
            return "bringing_results_back"
        if st.get("phase") in ("preparing", "sending", "testing"):
            return st["phase"]
        return "sending" if self.sending and not self.sent else "preparing"

    # -- the work --
    async def run(self) -> None:
        try:
            if self.to_send:
                try:
                    self.sent = await snd.send_job_models(self.client, self.items, self.to_send, self.begun["started"], self.page.speeds, self.remote.id, self._progress, lambda: self.stop,
                                                           heartbeat_s=self.page.heartbeat_s)
                except snd.SendRefused as e:
                    self.error = {"code": e.code, "detail": e.message}
            while True:
                try:
                    self.picture = await col.collect_job(self.client, self.page.book, self.job_id, self.page.base)
                except brc.RemoteError as e:
                    self.error = self.error or {"code": e.code, "detail": e.message}
                    break
                if self.picture.get("contact"):
                    self.last_contact = self.page.clock()
                    self.status = self.picture.get("status") or self.status
                if self.picture.get("done"):
                    break
                await asyncio.sleep(self.page.poll_s)
        except Exception as e:                                          # a bug or a full disk ends this run with a line, never a silent task
            self.error = self.error or {"code": "error", "detail": f"{type(e).__name__}"}
        finally:
            self.finished = True

    def _progress(self, p: dict) -> None:
        self.sending = p

    async def cancel(self) -> None:
        self.stop = True
        try:
            await self.client.cancel()
        except (brc.NoContact, brc.RemoteError):
            pass


class RemotePage:
    def __init__(self, base, remotes_fn: Callable[[], list], rows_fn: Callable[[], Any], wall: Callable[[], float] = time.time, clock: Callable[[], float] = time.monotonic,
                 client_factory: Optional[Callable[[Any], "brc.BenchmarkClient"]] = None, poll_s: float = POLL_S, heartbeat_s: float = snd.HEARTBEAT_S, here_free: Optional[Callable[[], int]] = None):
        self.base = Path(base)
        self.remotes_fn, self.rows_fn, self.wall, self.clock = remotes_fn, rows_fn, wall, clock
        self.client_factory = client_factory or self._default_client
        self.poll_s, self.heartbeat_s, self.here_free = poll_s, heartbeat_s, here_free
        self.speeds = snd.SpeedBook(self.base)
        self.book = col.RemoteJobBook(self.base)
        self.run: Optional[RemoteRun] = None
        self._plans: dict = {}
        self.clean_temp()

    def clean_temp(self, older_than_s: float = 7 * 86400) -> int:
        """Half-collected folders (`benchmarks/.collecting/<run id>`) that nobody came back to are removed after a week; a fresh one is a download that can still be continued."""
        import shutil
        n, folder = 0, self.base / col.TEMP_FOLDER
        try:
            for p in folder.iterdir():
                if p.is_dir() and not p.is_symlink() and self.wall() - p.stat().st_mtime > older_than_s:
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
        except OSError:
            pass
        return n

    def _default_client(self, remote) -> "brc.BenchmarkClient":
        return brc.BenchmarkClient(remote.base, remote.code, instance_id(self.base), pc=remote.label)

    def _remote(self, device_id: str):
        r = next((x for x in self.remotes_fn() if x.id == device_id and x.kind == "courier"), None)
        return r

    # ---- the "Test on" list ----
    def devices(self) -> list:
        now = self.wall()
        return [device_row(r, now) for r in self.remotes_fn() if r.kind == "courier"]

    # ---- one device: the model column, the room, the services ----
    async def plan(self, device_id: str, levels: str = "", prompts: str = "") -> dict:
        r = self._remote(device_id)
        if r is None:
            return refusal("unknown_device", "")
        row = device_row(r, self.wall())
        if row["state"] != READY:
            return refusal(row["state"], row["name"], **{"row": row})
        try:
            client = self.client_factory(r)
            inv = await client.models()
        except (brc.NoContact, other_pcs.OtherPcsError):
            return refusal(OFFLINE, row["name"], row=row)
        except brc.RemoteError as e:
            return refusal("error", row["name"], e.message)
        import benchmarks_tab
        import benchmarks_testset as tset
        import local_engine_settings
        import local_models
        card = row["vramMb"]
        gpu = local_models.GpuInfo(row["card"] or "", int(card), row["vendor"] or "nvidia") if card else None
        have = {m.get("content_id") for m in inv.get("models", []) if isinstance(m, dict)}
        out: dict = {}
        rows = await self._rows()
        lv = [int(x) for x in str(levels or "").split(",") if x.strip().isdigit() and 1 <= int(x) <= 64][:8] or [1, 4, 8]
        classes = [c for c in str(prompts or "").split(",") if c in tset.CLASSES] or list(tset.CLASSES)
        # the same estimate as the dots: the memory at the highest number of requests at once, against that device's whole card (a test device runs no game). Nothing is refused for size.
        benchmarks_tab.apply_fit(rows, gpu, "closed", None, None, levels=lv, prompts=classes, device_settings=dict(local_engine_settings.DEFAULT_SETTINGS))
        for m in rows:
            size = m.get("sizeBytes") or 0
            entry: dict = {"fit": m.get("fit"), "size": size, "needMb": m.get("fitNeedMb"), "freeMb": m.get("fitFreeMb"), "slots": m.get("fitSlots"), "confidence": m.get("fitConfidence")}
            path = m.get("path") if m.get("state") in ("downloaded", "local") else None
            if path and Path(path).is_file():
                cid = await asyncio.to_thread(bs.model_id_for, path, self.base)
                entry.update(on="already_there" if cid in have else "not_there", what="as_is" if cid in have else "sent", cid=cid)
            else:
                entry.update(on="not_there", what="download_then_send")
            out[m["key"]] = entry
        speed = self.speeds.last(device_id)
        rep = r.report or {}
        return {"ok": True, "device": row, "models": out, "freeBytes": inv.get("free_bytes"), "speedBps": int(speed) if speed else None, "services": _services(rep),
                "hasBattery": row["hasBattery"], "batteryPercent": (rep.get("power") or {}).get("battery_percent")}

    async def _rows(self) -> list:
        rows = self.rows_fn()
        if asyncio.iscoroutine(rows):
            rows = await rows
        return list(rows or [])

    # ---- Start ----
    async def start(self, body: dict) -> dict:
        if self.run is not None and not self.run.finished:
            return refusal("busy_here", "", "A benchmark is already running.")
        r = self._remote(str(body.get("device") or ""))
        if r is None:
            return refusal("unknown_device", "")
        row = device_row(r, self.wall())
        if row["state"] != READY:
            return refusal(row["state"], row["name"], row=row)
        raw_keys = body.get("keys")
        if not isinstance(raw_keys, list) or len(raw_keys) > MAX_KEYS or not all(isinstance(k, str) and len(k) <= 200 for k in raw_keys):
            return refusal("no_models", row["name"])
        keys = list(raw_keys)
        rows = {m["key"]: m for m in await self._rows()}
        models = {}
        for k in keys:
            m = rows.get(k)
            path = (m or {}).get("path") if (m or {}).get("state") in ("downloaded", "local") else None
            if not path or not Path(path).is_file():
                return refusal("not_downloaded", row["name"], "Download the ticked models first.")
            models[k] = path
        if not models:
            return refusal("no_models", row["name"])
        choice = body.get("whileTesting")
        if choice not in WHILE_TESTING:
            choice = "start_normal"
        setup = body.get("setup") if isinstance(body.get("setup"), dict) else {}
        settings = setup.get("settings") if isinstance(setup.get("settings"), dict) else {}
        options = {"levels": setup.get("levels") or [1, 4, 8], "requests_per_level": setup.get("requests_per_level") or 25, "prompts": setup.get("prompts") or list(bc.PROMPT_CLASSES),
                   "temperature": setup.get("temperature") or 0.0, "cooldown": bool(setup.get("cooldown", True)), "cooldown_c": setup.get("cooldown_c") or 55, "heat_stop": bool(setup.get("heat_stop", True)),
                   "while_testing": choice, "keep_models": bool(body.get("keep")), "take_over": bool(body.get("takeOver"))}
        if setup.get("sustained_s"):
            options["sustained_s"] = setup["sustained_s"]
        over = {k: v for k, v in settings.items() if k != "gpu"}       # (a card id from this PC means nothing on another one)
        if over:
            options["settings"] = over
        try:
            client = self.client_factory(r)
            items = await snd.hash_items(models, self.base)
            begun = await snd.begin_job(client, self.book, self.base, items, r.id, r.label, here_free=self.here_free, **options)
        except snd.SendRefused as e:
            return refusal(e.code, row["name"], e.message, **{k: v for k, v in e.extra.items() if k in ("need_bytes", "have_bytes")})
        except brc.OldCourier:
            return refusal(NEEDS_UPDATE, row["name"])
        except (brc.NoContact, other_pcs.OtherPcsError):
            return refusal(OFFLINE, row["name"])
        except brc.RemoteError as e:
            return refusal(self._code_of(e), row["name"], e.message, **{k: v for k, v in e.extra.items() if k in ("busy_reason", "need_bytes", "have_bytes")})
        need = sum(i.size for i in begun["to_send"])
        begun["estimate"] = {"sendBytes": need, "sendSeconds": (self.speeds.estimate(r.id, need) or {}).get("seconds")}
        self.run = RemoteRun(self, r, client, items, begun, options)
        self.run.task = asyncio.create_task(self.run.run())
        return {"ok": True, "jobId": self.run.job_id, "device": row["name"]}

    @staticmethod
    def _code_of(e: "brc.RemoteError") -> str:
        if e.code == "busy":
            return {"chat": "busy_chat", "transfer": "busy_transfer"}.get(e.extra.get("busy_reason"), "busy_benchmark")
        return e.code

    def state(self) -> dict:
        return {"active": self.run is not None, "run": self.run.state() if self.run else None}

    async def add(self, run_id: str) -> dict:
        """"Add to MultiProxy" for a result made on another PC: is the model on that PC now (kept there by "Keep them")? `{"onDevice": bool, "device": name}`. Sending it again is not offered from here."""
        if not bc.valid_run_id(run_id):
            return {"onDevice": False, "device": ""}
        try:
            m = await asyncio.to_thread(bs.read_manifest, bs.run_dir(self.base, run_id))
        except ValueError:
            return {"onDevice": False, "device": ""}
        dev = bc.device_of(m)
        r = next((x for x in self.remotes_fn() if x.kind == "courier" and dev.get("id") and dev.get("id") in (getattr(x, "instance", ""), x.id)), None)       # (the manifest holds the device's permanent id)
        if r is None or r.online is not True:
            return {"onDevice": False, "device": dev.get("name") or ""}
        try:
            inv = await self.client_factory(r).models()
        except (brc.NoContact, brc.RemoteError, other_pcs.OtherPcsError):
            return {"onDevice": False, "device": r.label}
        return {"onDevice": any(x.get("content_id") == m.get("model_id") for x in inv.get("models", []) if isinstance(x, dict)), "device": r.label}

    async def cancel(self) -> dict:
        if self.run is None or self.run.finished:
            return {"ok": True, "cancelled": False}
        await self.run.cancel()
        return {"ok": True, "cancelled": True}

    def dismiss(self) -> None:
        """The panel has shown the end of a finished run; forget it."""
        if self.run is not None and self.run.finished:
            self.run = None


def setup(app, base, remotes_fn, rows_fn, **kw) -> RemotePage:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    page = RemotePage(base, remotes_fn, rows_fn, **kw)

    async def body_of(request: Request) -> dict:
        """The JSON object of a small POST. Read with a cap: a body bigger than MAX_BODY_BYTES (declared or not) is refused (413) before it is held in memory."""
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
                return {"__too_big__": True}
        except ValueError:
            return {}
        size, parts = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return {"__too_big__": True}
            parts.append(chunk)
        try:
            data = json.loads(b"".join(parts).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError, RecursionError):
            return {}
        return data if isinstance(data, dict) else {}

    def too_big():
        return JSONResponse({"detail": "That request is too big."}, status_code=413)

    @app.get("/config/benchmarks/remote/devices")
    async def remote_devices(request: Request):
        return {"devices": page.devices()}

    @app.get("/config/benchmarks/remote/plan")
    async def remote_plan(request: Request, device: str = "", levels: str = "", prompts: str = ""):
        return await page.plan(device, levels, prompts)

    @app.post("/config/benchmarks/remote/start")
    async def remote_start(request: Request):
        body = await body_of(request)
        return too_big() if body.get("__too_big__") else await page.start(body)

    @app.get("/config/benchmarks/remote/state")
    async def remote_state(request: Request):
        return page.state()

    @app.post("/config/benchmarks/remote/cancel")
    async def remote_cancel(request: Request):
        return await page.cancel()

    @app.post("/config/benchmarks/remote/add")
    async def remote_add(request: Request):
        body = await body_of(request)
        return too_big() if body.get("__too_big__") else await page.add(str(body.get("run_id") or "")[:200])

    @app.post("/config/benchmarks/remote/dismiss")
    async def remote_dismiss(request: Request):
        page.dismiss()
        return {"ok": True}

    return page
