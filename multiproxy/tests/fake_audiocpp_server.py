"""A stand-in for the real voice engine (audiocpp_server), for tests: the same /health and /v1/audio/speech routes, the same one-at-a-time behaviour
and the same `busy_timeout_ms` refusal, with knobs in environment variables:

  FAKE_LOAD_DELAY   seconds before the port starts listening (the real one doesn't listen until the model is loaded)
  FAKE_DELAY        seconds each line takes
  FAKE_DIE_AT_ONCE  exit straight away with code 3 (an antivirus-killed engine)
  FAKE_WARMUP_FAIL  answer the first line with a 500
  FAKE_NEEDS_TEXT   refuse a line that carries a voice clip but no `reference_text`, with the real engine's own words (HTTP 500)
  FAKE_LOG          a file: every request's (text, ref bytes, extra fields) is appended as a JSON line
  FAKE_ASR_TEXT     what the stand-in transcription model (a `qwen3_asr` entry in the config) says every clip says (default "Hello there, this is the clip.")
  FAKE_ASR_FAIL     answer a transcription with a 500
  FAKE_ASR_EMPTY    answer a transcription with an empty text
  FAKE_ASR_DELAY    seconds a transcription takes
  FAKE_ASR_LOG      a file: every transcription request (model, clip bytes, extra fields) is appended as a JSON line, with this program's own port

and words inside the line's text that make it misbehave the way the real engine did in a real evening of play:
  RUNAWAY   a 40 s answer (a repetition loop)
  TL1       the engine's "reached max_tokens before EOC" error (HTTP 500) unless a seed was sent (a retry with a fresh seed works)
  TL2       the same error unless the text has no control tags (only the tag-free retry works)
  TL3       the same error every time
"""
import argparse
import asyncio
import base64
import io
import json
import math
import os
import struct
import sys
import time
import wave

from aiohttp import web


TASKS = ("vad", "asr", "diar", "sep", "gen", "tts", "clon", "vc", "s2s", "align", "vdes", "spk", "svc", "midi")   # the real engine's own list, kept apart from the product's on purpose


def tone(seconds=1.0, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 300 * i / rate))) for i in range(int(rate * seconds))))
    return buf.getvalue()


async def main_async(cfg_path):
    if os.environ.get("FAKE_DIE_AT_ONCE"):
        sys.exit(3)
    if os.environ.get("FAKE_DIE_PRINTING"):             # the engine's own error, then exit 1 (what the real one does for a setup it refuses)
        print(os.environ["FAKE_DIE_PRINTING"], flush=True)
        sys.exit(1)
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    if os.environ.get("FAKE_PRINT"):                      # a line printed at once (and flushed), for the Engine output window's tests
        print(os.environ["FAKE_PRINT"], flush=True)
    for m in cfg.get("models", []):
        if m.get("task") not in TASKS:                   # the real engine's own refusal of a task name it does not know
            print(f"audiocpp_server failed: unsupported task: {m.get('task')} (expected {', '.join(TASKS[:-1])}, or {TASKS[-1]})", flush=True)
            sys.exit(1)
        if m.get("family") == "chatterbox" and m.get("task") not in ("clon", "vc"):   # its loader check: Chatterbox only takes the clone (or conversion) task
            print("audiocpp_server failed: Chatterbox supports VoiceCloning and VoiceConversion", flush=True)
            sys.exit(1)
    await asyncio.sleep(float(os.environ.get("FAKE_LOAD_DELAY", "0")))
    lock = asyncio.Lock()
    state = {"first": True}

    async def health(_):
        return web.json_response({"status": "ok", "backend": cfg.get("backend"), "models": 1})

    async def speech(request):
        body = await request.json()
        print(f"speech request: {len(body.get('input') or '')} characters", flush=True)       # (what the real engine prints for a line: the Engine output window shows it)
        waited = float(body.get("busy_timeout_ms", 300000)) / 1000.0
        if os.environ.get("FAKE_WARMUP_FAIL") and state["first"]:
            state["first"] = False
            return web.json_response({"error": {"message": "warm-up failed"}}, status=500)
        state["first"] = False
        if os.environ.get("FAKE_NEEDS_TEXT") and body.get("voice_ref") and not (body.get("reference_text") or "").strip():
            return web.json_response({"error": {"message": "Qwen3 voice clone ICL mode requires reference text", "type": "server_error"}}, status=500)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=waited)
        except asyncio.TimeoutError:
            return web.json_response({"error": {"type": "server_busy", "message": "timed out waiting for the inference lock"}}, status=503)
        try:
            ref = body.get("voice_ref")
            if os.environ.get("FAKE_LOG"):
                with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
                    f.write(json.dumps({"input": body.get("input"), "ref_bytes": len(base64.b64decode(ref["data"])) if ref else None,
                                        "temperature": body.get("temperature"), "top_p": body.get("top_p"), "seed": body.get("seed"),
                                        "busy_timeout_ms": body.get("busy_timeout_ms"), "reference_text": body.get("reference_text")}) + "\n")
            text = body.get("input") or ""
            limit = ("TL3" in text) or ("TL1" in text and "seed" not in body) or ("TL2" in text and "<|" in text)
            if limit:
                return web.json_response({"error": {"type": "server_error", "message": "Higgs TTS generation reached max_tokens (2048) before EOC for this text chunk; "
                                                    "raise it with --max-tokens on the CLI or the \"max_tokens\" request option on the server"}}, status=500)
            await asyncio.sleep(float(os.environ.get("FAKE_DELAY", "0.05")))
            wav = tone(40.0 if "RUNAWAY" in text else 1.0)
            return web.Response(body=wav, content_type="audio/wav", headers={"X-AudioCPP-Wall-Ms": str(float(os.environ.get("FAKE_DELAY", "0.05")) * 1000),
                                                                              "X-AudioCPP-Audio-Duration-Ms": "1000", "X-AudioCPP-RTF": os.environ.get("FAKE_RTF", "0.1")})
        finally:
            lock.release()

    async def transcriptions(request):
        form = await request.post()
        clip = form.get("file")
        data = clip.file.read() if hasattr(clip, "file") else b""
        if os.environ.get("FAKE_ASR_LOG"):
            with open(os.environ["FAKE_ASR_LOG"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"model": form.get("model"), "clip_bytes": len(data), "rate": wave.open(io.BytesIO(data)).getframerate() if data else None,
                                    "fields": sorted(k for k in form.keys() if k not in ("file", "model")), "port": cfg["port"], "backend": cfg.get("backend")}) + "\n")
        await asyncio.sleep(float(os.environ.get("FAKE_ASR_DELAY", "0")))
        if os.environ.get("FAKE_ASR_FAIL"):
            return web.json_response({"error": {"message": "asr broke"}}, status=500)
        if os.environ.get("FAKE_ASR_EMPTY"):
            return web.json_response({"text": ""})
        return web.json_response({"text": os.environ.get("FAKE_ASR_TEXT", "Hello there, this is the clip.")})

    app = web.Application()
    app.router.add_get("/health", health)
    if any(m.get("family") == "qwen3_asr" for m in cfg.get("models", [])):
        app.router.add_post("/v1/audio/transcriptions", transcriptions)
    app.router.add_post("/v1/audio/speech", speech)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", int(cfg["port"])).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    a = ap.parse_args()
    asyncio.run(main_async(a.config))
