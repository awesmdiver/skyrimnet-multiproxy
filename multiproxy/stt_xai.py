"""Speech-to-text through xAI (4.0, 2026-10-24): SkyrimNet's voice clips turned into text by `grok-voice-transcribe-2.0`. Off by default.

SkyrimNet's External Whisper posts an OpenAI-style `multipart/form-data` request (`file`, `model`, `language`, `prompt`; no key). When the player has turned the setting on, the
clip is passed on to `POST https://api.x.ai/v1/stt` and SkyrimNet is answered `{"text": ...}`. The player's voice leaves the PC, which is why the setting is off until they say so.

What is never kept: the sound is held in memory only for the length of the request, and neither it nor the text that comes back is written anywhere (no log line, no history).
The one log line says how many seconds of sound and how long xAI took, or the reason code of a failure. The key is only ever put in the `Authorization` header of the call to xAI.
No automatic retry: the player is waiting, and a second try would double the wait.

Vendor page (docs.x.ai, read 2026-10-24): the `file` part must be the LAST part; `language` is a code like `en`; `format=true` writes numbers as numbers ("twenty one" -> 21)
and needs `language` (it only has an effect for some languages, harmless for the rest).
"""
import asyncio
import json
import re
import struct
import time
from collections import deque
from email import message_from_bytes
from email.policy import HTTP
from typing import Callable, Optional

import aiohttp
from fastapi import Request
from fastapi.responses import JSONResponse

import https_only

XAI_URL = "https://api.x.ai/v1/stt"
CONFIG_KEY = "stt_xai"                         # in config.json: true when the player turned it on
MAX_BYTES = 12 * 1024 * 1024                   # a push-to-talk line is well under 1 MB; 60 s of 44.1 kHz mono 16-bit is 5.3 MB
MAX_AT_ONCE = 2                                # clips being received or sent on at the same moment; a third is refused
UPLOAD_S = 20                                  # the whole upload from SkyrimNet must arrive within this
MAX_BYTE_RATE = 384000                         # 96 kHz mono 32-bit; a WAV header claiming more is not believed
TRAILING_SLACK = 64 * 1024                     # bytes allowed after the sound chunk (a LIST tag and the like)
MAX_SECONDS = 60
TIMEOUT_S = 15
PER_MINUTE = 30
MAX_REPLY = 2 * 1024 * 1024

TURNED_OFF = "Speech to text is off. Turn it on in MultiProxy on the Voice tab."
TOO_BIG = "That recording is too big."
TOO_LONG = f"That recording is longer than {MAX_SECONDS} seconds."
NO_FILE = "No recording came with that request."
NO_KEY = "There is no xAI key saved. Add it on the Providers tab."
KEY_REFUSED = "xAI didn't accept the key. Check it on the Providers tab."
TIMED_OUT = "xAI didn't answer in time. Try again."
BUSY = "xAI is busy right now. Try again in a moment."
UNREACHABLE = "MultiProxy couldn't reach xAI. Check your internet connection."
NOT_UNDERSTOOD = "xAI sent back something MultiProxy couldn't read."
NOT_ACCEPTED = "xAI couldn't read that recording."
TOO_MANY = "Too many recordings in a minute. Wait a moment and try again."
TOO_MANY_AT_ONCE = "Too many recordings at once. Wait a moment and try again."
SLOW_UPLOAD = "That recording took too long to arrive. Try again."
NOT_A_SOUND = "That recording isn't a valid sound file."

_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def error(message: str, status: int, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def parse_form(content_type: str, body: bytes) -> dict:
    """name -> (filename or None, content type, bytes) for each part of a multipart body; {} when it can't be read."""
    if "multipart" not in content_type.lower():
        return {}
    try:
        msg = message_from_bytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body, policy=HTTP)
        out = {}
        for p in msg.iter_parts():
            name = p.get_param("name", header="content-disposition")
            if name and name not in out:
                out[str(name)] = (p.get_filename(), p.get_content_type(), p.get_payload(decode=True) or b"")
        return out
    except Exception:
        return {}


def wav_seconds(data: bytes) -> Optional[float]:
    """The length of a WAV from its header, or None when it isn't a WAV (or the header can't say).
    Raises ValueError when the header and the real size disagree (a forged length) or the byte rate is not believable."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, byte_rate, size = 12, 0, None
    while pos + 8 <= len(data):
        cid, n = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt " and pos + 8 + 16 <= len(data):
            byte_rate = struct.unpack("<I", data[pos + 16:pos + 20])[0]
            if byte_rate > MAX_BYTE_RATE:
                raise ValueError("byte rate")
        elif cid == b"data":
            real = len(data) - (pos + 8)
            if n in (0, 0xFFFFFFFF) or n > real:
                size = real                                    # (a streamed WAV: the real size is the length)
            elif real - n > TRAILING_SLACK:
                raise ValueError("declared length is far shorter than the file")
            else:
                size = n
            break
        pos += 8 + n + (n & 1)
    return size / byte_rate if size is not None and byte_rate else None


class XaiStt:
    def __init__(self, load_cfg: Callable[[], dict], key: Callable[[], Optional[str]], log: Callable[[str], None], url: str = XAI_URL,
                 session_factory: Optional[Callable] = None, clock: Callable[[], float] = time.time):
        self.load_cfg, self.key, self.log, self.url, self.clock = load_cfg, key, log, url, clock
        self._session = session_factory or https_only.session
        self._recent = deque()
        self._active = 0
        self.last = None                     # {"at": epoch seconds, "took": seconds, "ok": bool}

    def enabled(self) -> bool:
        return self.load_cfg().get(CONFIG_KEY) is True

    def status(self) -> dict:
        last = {"ago": max(0.0, self.clock() - self.last["at"]), "took": self.last["took"], "ok": self.last["ok"]} if self.last else None
        return {"on": self.enabled(), "hasKey": bool(self.key()), "last": last}

    def _allowed_now(self) -> bool:
        now = self.clock()
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()
        if len(self._recent) >= PER_MINUTE:
            return False
        self._recent.append(now)
        return True

    def _fail(self, code: str, started: float, message: str, status: int, kind: str) -> JSONResponse:
        self.last = {"at": self.clock(), "took": round(time.monotonic() - started, 1), "ok": False}
        self.log(f"Speech to text via xAI failed: {code}.")
        return error(message, status, kind)

    async def _receive(self, request: Request, started: float):
        """The whole body, or the JSONResponse to answer with when it is too big or arrives too slowly."""
        chunks, total = [], 0

        async def read():
            nonlocal total
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BYTES:
                    return False
                chunks.append(chunk)
            return True
        try:
            ok = await asyncio.wait_for(read(), UPLOAD_S)
        except asyncio.TimeoutError:
            return self._fail("slow_upload", started, SLOW_UPLOAD, 408, "timeout")
        if not ok:
            return self._fail("too_big", started, TOO_BIG, 413, "request_too_large")
        return chunks

    async def transcribe(self, request: Request) -> JSONResponse:
        started = time.monotonic()
        if self._active >= MAX_AT_ONCE:
            self.log("Speech to text via xAI failed: too_many_at_once.")
            return error(TOO_MANY_AT_ONCE, 429, "rate_limit_exceeded")
        self._active += 1
        try:
            return await self._transcribe(request, started)
        finally:
            self._active -= 1

    async def _transcribe(self, request: Request, started: float) -> JSONResponse:
        if not self._allowed_now():
            self.log("Speech to text via xAI failed: rate_limit.")
            return error(TOO_MANY, 429, "rate_limit_exceeded")
        key = (self.key() or "").strip()
        if not key:
            return self._fail("no_key", started, NO_KEY, 401, "invalid_api_key")
        try:
            if int(request.headers.get("content-length", "0") or 0) > MAX_BYTES:
                return self._fail("too_big", started, TOO_BIG, 413, "request_too_large")
        except ValueError:
            pass
        chunks = await self._receive(request, started)
        if isinstance(chunks, JSONResponse):
            return chunks
        ctype_in = request.headers.get("content-type", "")
        parts = await asyncio.to_thread(lambda: parse_form(ctype_in, b"".join(chunks)))      # (off the main loop: a big clip must not stall other requests)
        chunks.clear()
        sent = parts.get("file")
        if not sent or not sent[2]:
            return self._fail("no_file", started, NO_FILE, 400, "invalid_request_error")
        filename, ctype, sound = sent
        try:
            length = await asyncio.to_thread(wav_seconds, sound)
        except ValueError:
            return self._fail("bad_wav", started, NOT_A_SOUND, 400, "invalid_request_error")
        if length is not None and length > MAX_SECONDS:
            return self._fail("too_long", started, TOO_LONG, 413, "request_too_large")
        lang = parts.get("language", (None, "", b""))[2].decode("utf-8", "replace").strip()
        form = aiohttp.FormData()
        if _LANGUAGE.match(lang):
            form.add_field("language", lang)
            form.add_field("format", "true")                      # (the vendor page: it needs `language`)
        form.add_field("file", sound, filename="audio.wav" if length is not None else (re.sub(r"[^A-Za-z0-9._-]", "_", filename or "") or "audio"),
                       content_type=ctype if ctype.startswith("audio/") else "application/octet-stream")          # (last: the vendor says so)
        try:
            async with self._session(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as s:
                async with s.post(self.url, data=form, headers={"Authorization": f"Bearer {key}"}, allow_redirects=False) as resp:
                    status = resp.status
                    raw = bytearray()
                    async for piece in resp.content.iter_chunked(65536):
                        raw += piece
                        if len(raw) > MAX_REPLY:
                            return self._fail("reply_too_big", started, NOT_UNDERSTOOD, 502, "bad_gateway")
        except asyncio.TimeoutError:
            return self._fail("timeout", started, TIMED_OUT, 504, "timeout")
        except aiohttp.ClientError as e:
            return self._fail(type(e).__name__, started, UNREACHABLE, 502, "bad_gateway")
        finally:
            del sound, form
        if status in (401, 403):
            return self._fail(f"key_refused_{status}", started, KEY_REFUSED, 401, "invalid_api_key")
        if status == 429 or status >= 500:
            return self._fail(f"busy_{status}", started, BUSY, 503, "service_unavailable")
        if status != 200:
            return self._fail(f"refused_{status}", started, NOT_ACCEPTED, 502, "bad_gateway")
        try:
            reply = json.loads(bytes(raw))
            text = reply["text"]
            if not isinstance(text, str):
                raise ValueError
        except (ValueError, KeyError, TypeError, RecursionError):
            return self._fail("bad_reply", started, NOT_UNDERSTOOD, 502, "bad_gateway")
        took = time.monotonic() - started
        heard = reply.get("duration") if isinstance(reply.get("duration"), (int, float)) and not isinstance(reply.get("duration"), bool) else length
        self.last = {"at": self.clock(), "took": round(took, 1), "ok": True}
        self.log(f"Speech to text via xAI: {f'{heard:.1f} s of sound' if heard is not None else 'a recording'}, answered in {took:.1f} s.")
        return JSONResponse({"text": text})
