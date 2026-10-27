"""A recording stand-in for SkyrimNet's Higgs voice endpoint (2026-10-02). It answers just enough for SkyrimNet to carry on, and saves every
request it receives, so the exact words SkyrimNet uses can be written down (and turned into test fixtures) instead of guessed.

Run it on a spare port with Skyrim closed or open (it never touches MultiProxy or a real Higgs):

    python scripts/voice_capture_standin.py --port 7861

then, in SkyrimNet's settings: Text-to-Speech, engine Higgs, **Endpoint** `http://127.0.0.1:7861`, press **Test Voice**, then talk to one NPC.
Put your own Endpoint back afterwards. Press Ctrl+C here when done. Everything lands in `voice-capture/<date and time>/`:
`NNN-METHOD-name.json` (one per request) and `index.jsonl` (the one-line version of all of them). Uploaded voice samples are NOT kept (only
their names, sizes and a checksum), so nothing from the game's audio ends up in a file.

It speaks the same flow SkyrimNet's own log shows it using: upload a sample, check it still exists, call `generate_audio`, wait for the answer,
fetch the audio. The "audio" it returns is a short generated tone.
"""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import re
import secrets
import struct
import sys
import time
import wave
from email import message_from_bytes
from email.policy import HTTP
from pathlib import Path

from aiohttp import web


def tone_wav(seconds: float = 0.4, rate: int = 24000) -> bytes:
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 330 * i / rate))) for i in range(n)))
    return buf.getvalue()


def skyrimnet_version() -> str:
    """The SkyrimNet version from its own log (the line 'SkyrimNet 0-26-0-0 Plugin Loaded' and its Git line), if the log can be found."""
    import os
    roots = [Path(os.path.expanduser("~")) / "Documents" / "My Games" / "Skyrim Special Edition" / "SKSE",
             Path(os.path.expanduser("~")) / "OneDrive" / "Documents" / "My Games" / "Skyrim Special Edition" / "SKSE"]
    for root in roots:
        log = root / "SkyrimNet.log"
        try:
            head = log.read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            continue
        m = re.search(r"SkyrimNet ([0-9-]+) Plugin Loaded", head)
        g = re.search(r"Git: (.+)", head)
        if m:
            return m.group(1).replace("-", ".") + (f" ({g.group(1).strip()})" if g else "")
    return "unknown (pass --skyrimnet-version)"


class Recorder:
    def __init__(self, out: Path, version: str):
        self.out, self.n = out, 0
        out.mkdir(parents=True, exist_ok=True)
        (out / "SKYRIMNET-VERSION.txt").write_text(version + "\n", encoding="utf-8")
        self.files: dict = {}
        self.events: dict = {}

    def save(self, entry: dict) -> None:
        self.n += 1
        name = re.sub(r"[^A-Za-z0-9]+", "-", entry["path"])[:50].strip("-") or "root"
        (self.out / f"{self.n:03d}-{entry['method']}-{name}.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
        with open(self.out / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: entry[k] for k in ("n", "time", "method", "path")}) + "\n")
        print(f"#{self.n} {entry['method']} {entry['path']}", flush=True)


def describe_body(content_type: str, body: bytes) -> dict:
    if not body:
        return {"kind": "empty"}
    if "multipart/" in content_type:
        msg = message_from_bytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body, policy=HTTP)
        parts = []
        for p in msg.iter_parts() if msg.is_multipart() else []:
            data = p.get_payload(decode=True) or b""
            parts.append({"name": p.get_param("name", header="content-disposition"), "filename": p.get_filename(), "content_type": p.get_content_type(),
                          "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return {"kind": "multipart", "parts": parts}
    try:
        return {"kind": "json", "json": json.loads(body)}
    except ValueError:
        return {"kind": "text", "text": body[:4000].decode("utf-8", "replace")}


def make_app(rec: Recorder) -> web.Application:
    app = web.Application(client_max_size=64 * 1024 * 1024)
    clip = tone_wav()

    async def handle(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        rec.save({"n": rec.n + 1, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "method": request.method, "path": str(request.rel_url),
                  "headers": dict(request.headers), "body": describe_body(request.headers.get("Content-Type", ""), body)})
        tail = request.path.split("/gradio_api/", 1)[1] if "/gradio_api/" in request.path else None
        prefix = request.path.split("/gradio_api/", 1)[0] if tail is not None else ""
        host = request.headers.get("Host", f"127.0.0.1:{request.url.port}")
        if tail == "upload" and request.method == "POST":
            names = [p["filename"] for p in describe_body(request.headers.get("Content-Type", ""), body).get("parts", []) if p.get("filename")]
            for nm in names:
                rec.files[f"standin/{nm}"] = clip
            return web.json_response([f"standin/{nm}" for nm in names])
        if tail is not None and tail.startswith("file="):
            ref = tail[len("file="):]
            if ref in rec.files or ref.startswith("standin/"):
                return web.Response(body=clip if request.method == "GET" else None, content_type="audio/wav", headers={"Content-Length": str(len(clip))})
            return web.json_response({"detail": "not found"}, status=404)
        if tail == "call/generate_audio" and request.method == "POST":
            eid = secrets.token_hex(16)
            rec.events[eid] = prefix
            return web.json_response({"event_id": eid})
        if tail and tail.startswith("call/generate_audio/") and request.method == "GET":
            item = {"path": "standin/result.wav", "url": f"http://{host}{prefix}/gradio_api/file=standin/result.wav", "size": len(clip),
                    "orig_name": "result.wav", "mime_type": None, "is_stream": False, "meta": {"_type": "gradio.FileData"}}
            rec.files["standin/result.wav"] = clip
            return web.Response(text=f"event: complete\ndata: {json.dumps([item])}\n\n", content_type="text/event-stream")
        return web.json_response({"detail": "stand-in: not something it knows (recorded)"}, status=404)

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=7861)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", default="voice-capture")
    ap.add_argument("--skyrimnet-version", default="")
    args = ap.parse_args()
    out = Path(args.out) / time.strftime("%Y-%m-%d_%H-%M-%S")
    rec = Recorder(out, args.skyrimnet_version or skyrimnet_version())
    print(f"Voice capture stand-in on http://{args.host}:{args.port}  ->  {out}\nSkyrimNet version: {(out / 'SKYRIMNET-VERSION.txt').read_text().strip()}\n"
          "Point SkyrimNet's Higgs Endpoint at it, press Test Voice, talk to one NPC, then Ctrl+C here.", flush=True)
    web.run_app(make_app(rec), host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
