"""The main device's side of a Courier (2026-10-01): talk to one Courier, keep one live connection to it.

The main device opens ONE persistent WebSocket to each Courier (never the other way round: this MultiProxy
keeps listening on 127.0.0.1 only, so it never has to open itself to the network). The Courier pushes its
report over it; if the connection drops, the Courier is marked offline AT ONCE (that's what makes the
backup-model switch instant) and the client reconnects by itself.

The next item ("Other devices tab: run every device through its Courier") adds the screens; this is the clean API
those screens call. Every request carries the pairing code as a bearer token, including the connection.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any, Awaitable, Callable, Optional

import aiohttp

import other_pcs


def _refuse_redirect(session, ctx, params) -> None:
    raise aiohttp.ClientError("A redirect was refused.")


def _session(timeout: aiohttp.ClientTimeout) -> aiohttp.ClientSession:
    """Every request to a Courier goes through here. A Courier never answers with a redirect, so none is ever followed (the pairing code, a chat or a sign-in code in
    the request would be sent on to wherever it points): `allow_redirects=False` on each request, and a trace that refuses a redirect for the one call that has no such option (the
    live connection). A 3xx answer is the same as no connection."""
    tc = aiohttp.TraceConfig()
    tc.on_request_redirect.append(_refuse_redirect)
    return aiohttp.ClientSession(timeout=timeout, trace_configs=[tc])


def _no_redirect(r) -> None:
    if 300 <= r.status < 400:
        raise aiohttp.ClientError("A redirect was refused.")


class UpdateRefused(Exception):
    """A Courier's update route said no. `code` is one of update_verify.WIRE_CODES (the Courier never sends anything else), `status` the HTTP status."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


RECONNECT_START = 1.0
RECONNECT_MAX = 15.0


class CourierClient:
    def __init__(self, address: str, code: str, client_name: str = "",
                 on_report: Optional[Callable[[dict], Any]] = None, on_offline: Optional[Callable[[str], Any]] = None,
                 on_online: Optional[Callable[[], Any]] = None, allow_loopback: bool = False):
        addr = other_pcs.parse_address(address, 8110)
        other_pcs.check_host_allowed(addr.host, allow_loopback=allow_loopback)      # home network only, same rule as everywhere
        self.base = addr.base
        self.code = code
        self.name = client_name or other_pcs.short_hostname()
        self.on_report, self.on_offline, self.on_online = on_report, on_offline, on_online
        self.online: Optional[bool] = None
        self.report: Optional[dict] = None
        self.error = ""
        self._task: Optional[asyncio.Task] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._stopping = False
        self._got_report = False

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.code}", "X-Courier-Client": self.name}

    async def _call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 15) -> Any:
        try:
            async with _session(aiohttp.ClientTimeout(total=timeout)) as s:
                async with s.request(method, self.base + path, json=body, headers=self._headers(), allow_redirects=False) as r:
                    _no_redirect(r)
                    try:
                        data = await r.json(content_type=None)
                    except ValueError:                  # not JSON at all (a plain-text refusal, or something that isn't a Courier): never crash the caller on it
                        data = None
                        if r.status < 400:
                            raise other_pcs.OtherPcsError(502, f"The Courier answered with an error ({r.status}).")
                    if r.status >= 400:
                        msg = ((data or {}).get("error") or {}).get("message") if isinstance(data, dict) else None
                        if not msg and r.status in (401, 403):
                            msg = "Wrong pairing code."
                        raise other_pcs.OtherPcsError(r.status, msg or f"The Courier answered with an error ({r.status}).")
                    return data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise other_pcs.OtherPcsError(502, other_pcs.COULDNT_CONNECT) from e

    # ---- one-off calls ---------------------------------------------------------------------------------------
    async def fetch_report(self) -> dict:
        self.report = await self._call("GET", "/courier/v1/report")
        return self.report

    async def action(self, program: str, action: str, model: str = "", wait: bool = False) -> dict:
        path = f"/courier/v1/programs/{program}/" + ("downloads/cancel" if action == "cancel" else (action if action in ("start", "restart", "stop", "uninstall") else f"models/{action}"))
        body = {"model": model, "wait": wait} if model or wait else {}
        return await self._call("POST", path, body, timeout=1800 if wait else 30)

    async def engine_get(self) -> dict:
        """The llama.cpp engine on that device: its build, whether a newer numbered build exists, and the faster versions its card is offered."""
        return await self._call("GET", "/courier/v1/programs/llamacpp/engine", timeout=30)

    async def engine_post(self, body: dict) -> dict:
        """{"action": "update"} or {"action": "use", "kind": ...}: only ever sent when the player pressed the button."""
        return await self._call("POST", "/courier/v1/programs/llamacpp/engine", body, timeout=60)

    async def log(self, lines: int = 200) -> list:
        """The Courier device's recent log lines (already masked over there)."""
        return (await self._call("GET", f"/courier/v1/log?lines={int(lines)}")).get("lines", [])

    # ---- sending a file to this Courier (file_transfer.py) ------------------------------------------------------
    # ---- Higgs voice on that device (voice_courier.py) ----------------------------------------------------------------------------------
    async def voice_get(self, what: str) -> dict:
        return await self._call("GET", f"/courier/v1/voice/{what}", timeout=30)

    async def voice_post(self, what: str, body: Optional[dict] = None, timeout: float = 30) -> dict:
        return await self._call("POST", f"/courier/v1/voice/{what}", body or {}, timeout=timeout)

    async def voice_speak(self, body: dict, dest, timeout: float = 60) -> dict:
        """Relay one line: post it to that device and stream the audio it sends straight to the file `dest`, a piece at a time (never whole in memory).
        Returns the engine's timing headers. A refusal comes back as the plain voice errors (busy, ran out of room, not running...); a device that
        can't be reached raises aiohttp / OS errors, which the caller turns into "that device isn't answering"."""
        from voice_engine import EngineBusy, EngineTokenLimit, VoiceEngineError
        async with _session(aiohttp.ClientTimeout(total=timeout, sock_connect=5)) as s:
            async with s.post(self.base + "/courier/v1/voice/speak", json=body, headers=self._headers(), allow_redirects=False) as r:
                _no_redirect(r)
                if r.status != 200:
                    try:
                        err = ((await r.json(content_type=None)) or {}).get("error") or {}
                    except Exception:
                        err = {}
                    kind, msg = err.get("type"), err.get("message") or f"The Courier answered with an error ({r.status})."
                    if kind == "busy":
                        raise EngineBusy(msg)
                    if kind == "token_limit":
                        raise EngineTokenLimit()
                    raise VoiceEngineError(r.status, msg)
                with open(dest, "wb") as f:
                    async for chunk in r.content.iter_chunked(64 * 1024):
                        f.write(chunk)
                return {k: r.headers[k] for k in ("X-AudioCPP-Wall-Ms", "X-AudioCPP-Audio-Duration-Ms", "X-AudioCPP-RTF") if k in r.headers}

    # ---- Claude on that device (claude_courier.py) -------------------------------------------------------------------------------------------
    async def claude_post(self, what: str, body: Optional[dict] = None, timeout: float = 30) -> dict:
        return await self._call("POST", f"/courier/v1/claude/{what}", body or {}, timeout=timeout)

    # ---- Copilot on that device (copilot_courier.py) ----------------------------------------------------------------------------------------
    async def copilot_post(self, what: str, body: Optional[dict] = None, timeout: float = 30) -> dict:
        return await self._call("POST", f"/courier/v1/copilot/{what}", body or {}, timeout=timeout)

    # ---- the local decision model on that device (decision_courier.py) ----------------------------------------------------------------------
    async def decisions_get(self, what: str) -> dict:
        return await self._call("GET", f"/courier/v1/decisions/{what}", timeout=30)

    async def decisions_post(self, what: str, body: Optional[dict] = None, timeout: float = 30) -> dict:
        return await self._call("POST", f"/courier/v1/decisions/{what}", body or {}, timeout=timeout)

    async def decisions_answer(self, state, questions: dict, limit: float) -> dict:
        """Relay one decision and wait at most `limit` seconds for the answer. A refusal comes back as the same plain errors the local model raises (too slow, not
        running, can't answer honestly); a device that can't be reached raises aiohttp / OS errors or a timeout, which the caller turns into "that device isn't answering"."""
        from decision_engine import DecisionError, DecisionNotReady, DecisionTooSlow, Unanswerable
        async with _session(aiohttp.ClientTimeout(total=max(0.05, limit), sock_connect=min(2.0, max(0.05, limit)))) as s:
            async with s.post(self.base + "/courier/v1/decisions/answer", json={"state": state, "questions": questions, "limit": limit}, headers=self._headers(),
                              allow_redirects=False) as r:
                _no_redirect(r)
                body = await r.json(content_type=None)
                if r.status != 200:
                    err = ((body or {}).get("error") or {}) if isinstance(body, dict) else {}
                    kind, msg = err.get("type"), err.get("message") or f"The Courier answered with an error ({r.status})."
                    if kind == "too_slow":
                        raise DecisionTooSlow(float(err.get("limit") or limit))
                    if kind == "not_running":
                        raise DecisionNotReady(msg)
                    if kind == "unanswerable":
                        raise Unanswerable(msg)
                    raise DecisionError(r.status, msg)
                return body

    async def update_call(self, path: str, body: dict, timeout: float = 60) -> dict:
        """One request of a Courier update (begin, stage, apply, cancel): always POST with a JSON body. A refusal is an UpdateRefused carrying the device's reason code;
        a connection that can't be made is left as it is."""
        async with _session(aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.post(self.base + path, json=body, headers=self._headers(), allow_redirects=False) as r:
                _no_redirect(r)
                data = await r.json(content_type=None)
                if r.status >= 400:
                    code = (((data or {}).get("error") or {}).get("code") if isinstance(data, dict) else None) or "update_failed"
                    raise UpdateRefused(str(code), r.status)
                return data

    async def transfer_begin(self, spec: dict) -> dict:
        return await self._transfer_call("POST", "/courier/v1/transfers", json_body=spec)

    async def transfer_chunk(self, tid: str, offset: int, data: bytes) -> int:
        return int((await self._transfer_call("PUT", f"/courier/v1/transfers/{tid}?offset={offset}", data=data, timeout=120))["offset"])

    async def transfer_finish(self, tid: str) -> dict:
        return await self._transfer_call("POST", f"/courier/v1/transfers/{tid}/finish", json_body={}, timeout=3600)

    async def transfer_cancel(self, tid: str) -> None:
        await self._transfer_call("DELETE", f"/courier/v1/transfers/{tid}")

    async def _transfer_call(self, method: str, path: str, json_body=None, data=None, timeout: float = 30) -> dict:
        """One request of a transfer. A real answer with an error becomes a TransferError (a wrong offset carries the right one);
        a connection that can't be made is left as it is, so the sender pauses and tries again."""
        import file_transfer
        async with _session(aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.request(method, self.base + path, json=json_body, data=data, headers=self._headers(), allow_redirects=False) as r:
                _no_redirect(r)
                body = await r.json(content_type=None)
                if r.status >= 400:
                    msg = ((body or {}).get("error") or {}).get("message") if isinstance(body, dict) else None
                    raise file_transfer.TransferError(r.status, msg or f"The Courier answered with an error ({r.status}).",
                                                      offset=body.get("offset") if isinstance(body, dict) else None)
                return body

    # ---- which graphics card each local AI on that device uses (gpu_choice_wiring.py) -----------------------------------------------------
    async def gpu_get(self) -> dict:
        return await self._call("GET", "/courier/v1/gpu", timeout=30)

    async def gpu_post(self, body: dict) -> dict:
        """{"ai": key, "cardId": id-or-None}: the device's whole `{cards, ais}` block comes back."""
        return await self._call("POST", "/courier/v1/gpu", body, timeout=30)

    async def settings(self, **changes) -> dict:
        return await self._call("POST", "/courier/v1/settings", changes)

    # ---- the model settings of that device (local_engine_sync.py) ---------------------------------------------------------------------------
    async def engine_settings_post(self, payload: dict) -> dict:
        """Send that device its model settings (the record and its hash). A refusal (another engine build, an older Courier) is an OtherPcsError with status 409."""
        return await self._call("POST", "/courier/v1/engine-settings", payload, timeout=30)

    async def engine_settings_get(self) -> dict:
        return await self._call("GET", "/courier/v1/engine-settings")

    # ---- models already on that device, and where new ones are saved ----------------------------------------------
    async def find_models(self, folder: str = "") -> dict:
        return await self._call("POST", "/courier/v1/models/find", {"folder": folder} if folder else {}, timeout=60)

    async def scan(self, action: str) -> dict:
        """The full scan of that device's drives: action is start, stop or status."""
        if action == "status":
            return await self._call("GET", "/courier/v1/models/scan")
        return await self._call("POST", f"/courier/v1/models/scan/{action}", {}, timeout=30)

    async def add_models(self, paths: list) -> dict:
        return await self._call("POST", "/courier/v1/models/add", {"paths": list(paths)}, timeout=60)

    async def drives(self) -> dict:
        return await self._call("GET", "/courier/v1/drives")

    async def set_models_folder(self, path: str) -> dict:
        return await self._call("POST", "/courier/v1/models-folder", {"path": path}, timeout=30)

    async def new_pairing_code(self) -> str:
        return (await self._call("POST", "/courier/v1/pairing/new"))["code"]

    async def unpair(self) -> None:
        await self._call("POST", "/courier/v1/pairing/unpair", timeout=5)

    # ---- the live connection ---------------------------------------------------------------------------------
    def _mark(self, online: bool, error: str = "") -> None:
        was, self.online, self.error = self.online, online, error
        if online and was is not True and self.on_online:
            self.on_online()
        if not online and was is not False and self.on_offline:
            self.on_offline(error)

    async def _listen_once(self) -> None:
        async with _session(aiohttp.ClientTimeout(total=None, sock_connect=5)) as s:
            async with s.ws_connect(self.base + "/courier/v1/ws", headers=self._headers(), heartbeat=20) as ws:
                self._ws = ws
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = msg.json()
                        if data.get("type") == "report":
                            self.report = data["report"]
                            self._got_report = True
                            self._mark(True)
                            if self.on_report:
                                res = self.on_report(data["report"])
                                if asyncio.iscoroutine(res):
                                    await res
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                code = ws.close_code
        self._ws = None
        if code == 4001:
            raise other_pcs.OtherPcsError(403, "That device generated a new pairing code. Enter it to reconnect.")
        raise ConnectionError("The connection to that device was lost.")

    async def _loop(self) -> None:
        delay = RECONNECT_START
        while not self._stopping:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as e:
                self._mark(False, "Wrong pairing code." if e.status == 403 else f"That device refused the connection ({e.status}).")
                if e.status == 403:
                    delay = RECONNECT_MAX            # a wrong code won't fix itself; don't hammer (the Courier rate-limits wrong codes)
            except other_pcs.OtherPcsError as e:
                self._mark(False, e.message)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ConnectionError):
                self._mark(False, "Lost connection to the Courier device.")
            else:
                continue
            if self._stopping:
                break
            if self._got_report:                     # it had been working: come straight back
                delay, self._got_report = RECONNECT_START, False
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
