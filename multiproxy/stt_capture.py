"""Speech-to-text, step one (2026-10-06): see exactly what SkyrimNet sends when it wants a voice clip turned into text.

`POST /v1/audio/transcriptions` (OpenAI-style, also under `/voice`) transcribes nothing yet. It records the SHAPE of the request and answers 501 so nothing downstream is
misled. Never logged: the sound, a key's value, any text value outside a short harmless list. The sound is never written to disk.

One short line is always logged (so a player can tell SkyrimNet reaches MultiProxy); with the Settings switch "Log what SkyrimNet sends" (or `MULTIPROXY_STT_FIELDS=1`) a second line says what the request was made of.
"""
import json
import os
import re
import struct
from email import message_from_bytes
from email.policy import HTTP
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import field_log

FIELDS_SWITCH = "MULTIPROXY_STT_FIELDS"
MAX_BYTES = 25 * 1024 * 1024
NOT_AVAILABLE = "Speech to text is off. Turn it on in MultiProxy on the Voice tab."
TOO_BIG = "That recording is too big."
ARRIVED = "A speech-to-text request arrived (POST {path}); speech to text is off, so it was answered with 501."
_KEY_HEADERS = ("authorization", "x-api-key", "api-key", "x-goog-api-key")
_USUAL = {"host", "user-agent", "accept", "accept-encoding", "connection", "content-length", "content-type"}
_HARMLESS = ("model", "language", "response_format", "temperature", "timestamp_granularities", "timestamp_granularities[]")


def _clean(text, limit: int = 80) -> str:
    """A name taken from a request, safe for one log line: no control character, cut short."""
    return re.sub(r"[\x00-\x1f\x7f]", "?", str(text))[:limit]


def _kind(v: str) -> str:
    s = v.strip()
    if s.lower() in ("true", "false"):
        return "bool"
    try:
        float(s)
        return "number"
    except ValueError:
        return "text"


def wav_facts(data: bytes) -> Optional[str]:
    """Sample rate, channels and length from a WAV's header (nothing else is read), or None when it is not a WAV."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, fmt, size = 12, None, None
    while pos + 8 <= len(data):
        cid, n = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt " and pos + 8 + 16 <= len(data):
            tag, ch, rate, byte_rate, _align, bits = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
            fmt = (tag, ch, rate, byte_rate, bits)
        elif cid == b"data":
            size = len(data) - (pos + 8) if n in (0, 0xFFFFFFFF) or pos + 8 + n > len(data) else n
            break
        pos += 8 + n + (n & 1)
    if not fmt:
        return "wav (header unreadable)"
    tag, ch, rate, byte_rate, bits = fmt
    out = f"wav, {rate} Hz, {ch} channel{'s' if ch != 1 else ''}, {bits}-bit{' float' if tag == 3 else ''}"
    if size is not None and byte_rate:
        out += f", {size / byte_rate:.2f} s"
    return out


def container_of(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "mp4/m4a"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm/matroska"
    return "unknown"


def _multipart_parts(content_type: str, body: bytes) -> list:
    """[(name, filename, part content type, bytes, is_file)] for a multipart body. A part is a file when it has a filename or an audio/video/binary type."""
    msg = message_from_bytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body, policy=HTTP)
    out = []
    for p in msg.iter_parts():
        ptype, fn = p.get_content_type(), p.get_filename()
        is_file = fn is not None or ptype.startswith(("audio/", "video/", "application/octet-stream"))
        out.append((_clean(p.get_param("name", header="content-disposition") or "?", 40), fn, ptype, p.get_payload(decode=True) or b"", is_file))
    return out


def has_audio_file(content_type: str, body: bytes) -> bool:
    """True for a multipart request with a `file` part of an audio type (what SkyrimNet's External Whisper sends)."""
    if "multipart" not in (content_type or "").lower():
        return False
    try:
        return any(name == "file" and is_file and ptype.startswith("audio/") for name, _fn, ptype, _data, is_file in _multipart_parts(content_type, body))
    except Exception:
        return False


def is_text_type(content_type: str) -> bool:
    """A body whose content type says plain text (none given, JSON, text/*, a form, XML) may be logged as text; anything else may hold bytes."""
    low = (content_type or "").split(";")[0].strip().lower()
    return not low or low.startswith("text/") or "json" in low or "xml" in low or low == "application/x-www-form-urlencoded"


def body_summary(content_type: str, body: bytes) -> Optional[str]:
    """What a failed request's body becomes in a log when it is not plain text, or None when it is plain text (the caller logs it as before). Only the type and the
    size; for multipart, each part's name, type and size, with the text of a non-file part shown only for the short harmless list. Never a file part's content."""
    ctype = _clean((content_type or "").split(";")[0].strip() or "none")
    if is_text_type(content_type):
        return None
    if "multipart" in ctype.lower():
        try:
            parts = []
            for name, _fn, ptype, data, is_file in _multipart_parts(content_type, body):
                if not is_file and name in _HARMLESS:
                    parts.append(f"{name}: {_clean(data.decode('utf-8', 'replace').strip(), 40)}")
                else:
                    parts.append(f"{name}: {_clean(ptype)}, {len(data):,} bytes")
            return f"{ctype}, {len(body):,} bytes (not shown); parts: " + ("; ".join(parts) or "none")
        except Exception:
            pass
    return f"{ctype}, {len(body):,} bytes (not shown)"


def describe(method: str, path: str, headers: dict, body: bytes) -> str:
    """One line: what a speech-to-text request was made of. Names, kinds and sizes; values only for a short harmless list."""
    content_type = headers.get("content-type", "")
    ctype = _clean(re.sub(r";\s*boundary=[^;]*", "", content_type, flags=re.I).strip() or "none")
    key = "yes" if any(k.lower() in _KEY_HEADERS for k in headers) else "no"
    names = sorted(_clean(k.lower(), 40) for k in ({k.lower() for k in headers} - _USUAL - set(_KEY_HEADERS)))[:20]
    shape, audio = "no body fields", ""
    low = content_type.lower()
    if "multipart" in low:
        try:
            parts = []
            for name, fn, ptype, data, is_file in _multipart_parts(content_type, body):
                if is_file:
                    ext = os.path.splitext(fn or "")[1].lower()[:8] or "none"
                    parts.append(f"{name}(file, extension {ext}, {ptype}, {len(data)} bytes)")
                    if not audio:
                        audio = f"{container_of(data)}" + (f"; {wav_facts(data)}" if container_of(data) == "wav" else "")
                else:
                    text = data.decode("utf-8", "replace")
                    if name in _HARMLESS:
                        parts.append(f"{name}({_kind(text)} = {text.strip()[:40]})")
                    elif name == "prompt":
                        parts.append(f"prompt (text, {len(text)} characters)")
                    else:
                        parts.append(f"{name}({_kind(text)}, {len(data)} bytes)")
            shape = "multipart parts: " + (", ".join(parts) or "none")
        except Exception:
            shape = "multipart (unreadable)"
    elif "json" in low or body[:1] in (b"{", b"["):
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            kinds = {str: "text", bool: "bool", int: "number", float: "number", list: "list", dict: "object", type(None): "null"}
            shape = "json fields: " + ", ".join(f"{k}={kinds.get(type(v), type(v).__name__)}" for k, v in parsed.items())
    return (f"Speech-to-text request {method} {path}: content type {ctype}; key sent {key}; other headers: {', '.join(names) or 'none'}; {shape}"
            + (f"; audio: {audio}" if audio else "; audio: none found"))


def build_router(log, xai=None) -> APIRouter:
    """`xai`: the xAI translator (stt_xai.XaiStt). While the player's setting is on it answers; with it off (or none) the request is only recorded and answered 501."""
    r = APIRouter()

    async def answer(request: Request, only_audio: bool):
        """The shared answer. `only_audio` (the bare address): the body is read to see whether it is an audio upload, and anything else is a plain 404."""
        path = request.url.path
        if xai is not None and xai.enabled():
            return await xai.transcribe(request)
        try:
            if int(request.headers.get("content-length", "0") or 0) > MAX_BYTES:
                return JSONResponse({"error": {"message": TOO_BIG, "type": "request_too_large"}}, status_code=413)
        except ValueError:
            pass
        chunks, total, keep = [], 0, field_log.wanted(FIELDS_SWITCH) or only_audio          # (the body is kept only when it will be read)
        async for chunk in request.stream():                 # (a body with no length is still stopped at the cap)
            total += len(chunk)
            if total > MAX_BYTES:
                return JSONResponse({"error": {"message": TOO_BIG, "type": "request_too_large"}}, status_code=413)
            if keep:
                chunks.append(chunk)
        body = b"".join(chunks)
        if only_audio and not has_audio_file(request.headers.get("content-type", ""), body):
            raise HTTPException(status_code=404, detail="Not Found")
        log(ARRIVED.format(path=path))
        if field_log.wanted(FIELDS_SWITCH):
            log(describe(request.method, path, dict(request.headers), body))
        return JSONResponse({"error": {"message": NOT_AVAILABLE, "type": "not_implemented"}}, status_code=501)

    @r.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request):
        return await answer(request, False)

    @r.post("/")        # SkyrimNet's External Whisper uses the address exactly as typed, and a player leaves the path off
    async def transcriptions_bare(request: Request):
        return await answer(request, True)

    return r
