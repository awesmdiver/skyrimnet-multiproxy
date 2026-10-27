"""A recording stand-in for SkyrimNet Beta 26 rc2's three provider styles (Chat Completions, Decisions, System One) (2026-10-02). It answers just enough for
SkyrimNet to carry on and saves every request, so what rc2 REALLY sends (path, headers, body shape, model name, whether the API Key is sent) is written
down instead of guessed. Keys are never saved: an Authorization header is reduced to its kind and length, and any long text in a body is cut short.

Run it on a spare port (it never touches MultiProxy or a real AI):

    python scripts/decisions_capture_standin.py --port 8051

Then, in SkyrimNet (AI > Providers), add a custom provider called  Capture Chat  with Endpoint  http://127.0.0.1:8051/chat/completions  and API Style
"Chat Completions"; another called  Capture Decisions  with Endpoint  http://127.0.0.1:8051/decisions  and API Style "Decisions"; and a third called
Capture System One  with Endpoint  http://127.0.0.1:8051/systemone  and API Style "System One" (type any text in the API Key box of one of them, so we learn
whether it is sent: any text, not a real key). Under AI > Models pick each one on the Decisions card in turn (Capture Chat and the others), press its test
button if there is one and talk to an NPC for a moment each time. Put your own settings back afterwards and press Ctrl+C here.

Everything lands in `decisions-capture/<date and time>/`: `NNN-METHOD-name.json` (one per request) and `index.jsonl`. The Endpoints above are typed
with DIFFERENT paths on purpose: the recording then shows whether SkyrimNet uses an Endpoint exactly as typed or changes its path.
"""
import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path

from aiohttp import web

LONG_TEXT = 220
SECRET_KEYS = re.compile(r"(authorization|api[-_]?key|token|secret|cookie)", re.I)


def skyrimnet_version() -> str:
    import os
    roots = [Path(os.path.expanduser("~")) / "Documents" / "My Games" / "Skyrim Special Edition" / "SKSE",
             Path(os.path.expanduser("~")) / "OneDrive" / "Documents" / "My Games" / "Skyrim Special Edition" / "SKSE"]
    for env in ("OneDrive", "OneDriveConsumer"):          # a redirected Documents folder (OneDrive on another drive) is where SkyrimNet's log often is
        if os.environ.get(env):
            roots.append(Path(os.environ[env]) / "Documents" / "My Games" / "Skyrim Special Edition" / "SKSE")
    for root in roots:
        try:
            head = (root / "SkyrimNet.log").read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            continue
        m = re.search(r"SkyrimNet ([0-9-]+) Plugin Loaded", head)
        build = re.search(r"Build Date: (.+)", head)
        git = re.search(r"Git: (.+)", head)
        if m:
            return m.group(1).replace("-", ".") + (f" (build {build.group(1).strip()}, git {git.group(1).strip()})" if build and git else "")
    return "unknown (pass --skyrimnet-version)"


def mask_header(name: str, value: str) -> str:
    if SECRET_KEYS.search(name):
        kind = value.split(" ", 1)[0] if " " in value else "(no scheme)"
        return f"<{kind}, {len(value)} characters, not saved>"
    return value


def shorten(v):
    """The shape of a body with long text cut: dialogue and prompts are never kept whole."""
    if isinstance(v, str):
        return v if len(v) <= LONG_TEXT else v[:LONG_TEXT] + f"... (+{len(v) - LONG_TEXT} characters)"
    if isinstance(v, dict):
        return {k: ("<not saved>" if SECRET_KEYS.search(k) else shorten(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [shorten(x) for x in v[:12]] + ([f"... (+{len(v) - 12} more)"] if len(v) > 12 else [])
    return v


def describe_body(raw: bytes):
    if not raw:
        return {"kind": "empty"}
    try:
        return {"kind": "json", "json": shorten(json.loads(raw))}
    except ValueError:
        return {"kind": "text", "text": raw[:400].decode("utf-8", "replace")}


def decision_answers(body: dict) -> dict:
    """What a Jev-shaped reply looks like: one answer per question (the shape SkyrimNet's own log shows it reading)."""
    answers = {}
    for qid, q in (body.get("questions") or {}).items():
        crit = (q or {}).get("criteria") if isinstance(q, dict) else None
        if isinstance(crit, dict) and crit:
            first = next(iter(crit))
            answers[qid] = {"type": "choice", "choice": first, "confidence": 1.0, "probabilities": {k: (1.0 if k == first else 0.0) for k in crit}}
        else:
            answers[qid] = {"type": "noul", "noul": 0.5}
    return answers


class Recorder:
    def __init__(self, out: Path, version: str):
        self.out, self.n = out, 0
        out.mkdir(parents=True, exist_ok=True)
        (out / "SKYRIMNET-VERSION.txt").write_text(version + "\n", encoding="utf-8")

    def save(self, entry: dict) -> None:
        self.n += 1
        name = re.sub(r"[^A-Za-z0-9]+", "-", entry["path"])[:50].strip("-") or "root"
        (self.out / f"{self.n:03d}-{entry['method']}-{name}.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
        with open(self.out / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: entry[k] for k in ("n", "time", "method", "path")}) + "\n")
        print(f"#{self.n} {entry['method']} {entry['path']}", flush=True)


def make_app(rec: Recorder) -> web.Application:
    app = web.Application(client_max_size=64 * 1024 * 1024)

    async def handle(request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        rec.save({"n": rec.n + 1, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "method": request.method, "path": str(request.rel_url),
                  "headers": {k: mask_header(k, v) for k, v in request.headers.items()}, "body": describe_body(raw)})
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if request.method == "GET" and request.path.rstrip("/").endswith("/models"):
            return web.json_response({"object": "list", "data": [{"id": "capture-model", "object": "model"}, {"id": "typesafe/jev-1.13", "object": "model"}]})
        if isinstance(body, dict) and body.get("questions") is not None:
            return web.json_response({"model": body.get("model", "capture-model"), "answers": decision_answers(body), "usage": {"input_tokens": 1, "output_tokens": 1}})
        if isinstance(body, dict) and body.get("messages") is not None:
            if body.get("stream"):
                resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
                await resp.prepare(request)
                chunk = {"id": "cap", "object": "chat.completion.chunk", "model": body.get("model", "capture-model"), "choices": [{"index": 0, "delta": {"content": "Understood."}, "finish_reason": None}]}
                end = {"id": "cap", "object": "chat.completion.chunk", "model": body.get("model", "capture-model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
                await resp.write(f"data: {json.dumps(end)}\n\n".encode())
                await resp.write(b"data: [DONE]\n\n")
                return resp
            return web.json_response({"id": "cap", "object": "chat.completion", "model": body.get("model", "capture-model"),
                                      "choices": [{"index": 0, "message": {"role": "assistant", "content": "Understood."}, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        return web.json_response({"detail": "capture stand-in: not something it knows (recorded)"}, status=404)

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8051)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", default="decisions-capture")
    ap.add_argument("--skyrimnet-version", default="")
    args = ap.parse_args()
    out = Path(args.out) / time.strftime("%Y-%m-%d_%H-%M-%S")
    rec = Recorder(out, args.skyrimnet_version or skyrimnet_version())
    print(f"Decisions capture stand-in on http://{args.host}:{args.port}  ->  {out}\nSkyrimNet version: {(out / 'SKYRIMNET-VERSION.txt').read_text().strip()}\n"
          "Add the three custom providers (see the top of this file), pick each on the Decisions card, talk to an NPC, then press Ctrl+C here.", flush=True)
    web.run_app(make_app(rec), host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
