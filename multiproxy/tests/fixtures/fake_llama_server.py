"""A stand-in for llama.cpp's llama-server, for tests: same /health and /v1/chat/completions
shape, no model. Run as `python fake_llama_server.py --port N [--mode MODE]`.

Modes: ok (default) | load-delay:SECONDS (health says 503 until then) | oom-exit (prints an
out-of-memory line and exits) | die-after-ready (becomes ready, then exits 1 a moment later).
"""
import argparse
import asyncio
import json
import sys
import time

from aiohttp import web


def _text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


def _has_image(content):
    return isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in content)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mode", default="ok")
    args = ap.parse_args()

    if args.mode == "oom-exit":
        print("ggml_cuda_init: found 1 CUDA devices", flush=True)
        print("cudaMalloc failed: out of memory", flush=True)
        sys.exit(1)

    ready_at = time.monotonic()
    if args.mode.startswith("load-delay:"):
        ready_at += float(args.mode.split(":", 1)[1])

    async def health(request):
        if time.monotonic() < ready_at:
            return web.json_response({"error": {"message": "Loading model"}}, status=503)
        return web.json_response({"status": "ok"})

    async def chat(request):
        body = await request.json()
        last = body["messages"][-1]["content"]
        reply = "I see a picture. " if _has_image(last) else ""
        reply += "echo:" + _text_of(last)
        if _text_of(last) == "params?":
            reply = ",".join(sorted(k for k in body if k not in ("model", "messages")))
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for piece in (reply[: len(reply) // 2], reply[len(reply) // 2:]):
                chunk = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            end = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            await resp.write(f"data: {json.dumps(end)}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            return resp
        return web.json_response({
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            "seen_params": sorted(k for k in body if k not in ("model", "messages")),
        })

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", args.port).start()
    print(f"fake llama-server listening on {args.port}", flush=True)
    if args.mode == "die-after-ready":
        await asyncio.sleep(1.0)
        print("fatal: something broke", flush=True)
        sys.exit(1)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
