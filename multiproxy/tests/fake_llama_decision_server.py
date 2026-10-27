"""A stand-in for llama-server, for the local decision model's tests: /health and llama.cpp's own `POST /v1/systemone` (TypeSafe's System One format), with knobs in
environment variables (the fake is a child process, so set them before it starts):

  FAKE_LOAD_DELAY   seconds before /health says 200 (503 while "loading")
  FAKE_DELAY        seconds every /v1/systemone takes (concurrent ones overlap, as the engine's slots do)
  FAKE_PICK         which answer gets the probability: first | last | second (default first)
  FAKE_ERROR        answer /v1/systemone with this HTTP status
  FAKE_REFUSE       a question id (or several, comma separated) the engine can't take: answered with 400, as llama.cpp does for an invalid question
  FAKE_BAD_SHAPE    answer 200 with something that isn't System One's shape
  FAKE_DIE_AT_ONCE  exit straight away with code 3
  FAKE_LOG          a file: one JSON line per request (the question ids, the state's length, the types)
"""
import argparse
import asyncio
import json
import os
import sys
import time

from aiohttp import web


def answer_for(qid: str, q: dict, pick: str) -> dict:
    t = q.get("type")
    if t == "noul":
        return {"type": "noul", "noul": {"first": 0.9, "last": 0.1, "second": 0.4}[pick]}
    if t == "choice":
        opts = list((q.get("criteria") or {"yes": None, "no": None}).keys())
        win = {"first": 0, "last": len(opts) - 1, "second": min(1, len(opts) - 1)}[pick]
        probs = {o: (0.7 if i == win else 0.3 / max(1, len(opts) - 1)) for i, o in enumerate(opts)}
        return {"type": "choice", "choice": opts[win], "probabilities": probs, "confidence": 0.5}
    levels = q.get("criteria") or ["low", "high"]
    win = {"first": 0, "last": len(levels) - 1, "second": min(1, len(levels) - 1)}[pick]
    probs = {str(i): (0.7 if i == win else 0.3 / max(1, len(levels) - 1)) for i in range(len(levels))}
    return {"type": "score", "score": float(win), "legend": {str(i): l for i, l in enumerate(levels)}, "probabilities": probs, "confidence": 0.5}


def make_app(slots: int = 1000) -> web.Application:
    state = {"t0": time.time(), "n": 0}
    gate = asyncio.Semaphore(slots)                  # llama-server answers `-np` requests at once and queues the rest
    load = float(os.environ.get("FAKE_LOAD_DELAY", "0"))

    async def health(request):
        if time.time() - state["t0"] < load:
            return web.json_response({"error": {"message": "Loading model"}}, status=503)
        return web.json_response({"status": "ok"})

    async def systemone(request):
        body = await request.json()
        state["n"] += 1
        if os.environ.get("FAKE_ERROR"):
            return web.json_response({"error": {"message": "fake"}}, status=int(os.environ["FAKE_ERROR"]))
        questions = body.get("questions") if isinstance(body, dict) else None
        if not isinstance(questions, dict) or not questions:
            return web.json_response({"error": {"message": "questions must be a non-empty object"}}, status=400)
        refused = {x.strip() for x in os.environ.get("FAKE_REFUSE", "").split(",") if x.strip()}
        bad = [k for k in questions if k in refused]
        if bad:
            return web.json_response({"error": {"message": f"question '{bad[0]}' is not valid"}}, status=400)
        st = body.get("state")
        text = st if isinstance(st, str) else json.dumps(st)
        if os.environ.get("FAKE_LOG"):
            with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"ids": list(questions), "chars": len(text), "types": [q.get("type") for q in questions.values()], "state": text[:60]}) + "\n")
        async with gate:
            await asyncio.sleep(float(os.environ.get("FAKE_DELAY", "0")))
        if os.environ.get("FAKE_BAD_SHAPE"):
            return web.json_response({"nothing": "useful"})
        pick = os.environ.get("FAKE_PICK", "first")
        return web.json_response({"model": "fake", "answers": {k: answer_for(k, q, pick) for k, q in questions.items()},
                                  "usage": {"input_tokens": max(1, len(text) // 4) * len(questions), "output_tokens": 0}})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/v1/systemone", systemone)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--slots", type=int, default=1000)
    a, _ = ap.parse_known_args()
    if os.environ.get("FAKE_DIE_AT_ONCE"):
        sys.exit(3)
    web.run_app(make_app(a.slots), host="127.0.0.1", port=a.port, print=None)
