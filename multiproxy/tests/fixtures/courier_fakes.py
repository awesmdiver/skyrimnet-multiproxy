"""Stand-ins for the AI programs a Courier drives, for tests: a fake Ollama (its real HTTP shapes), a fake LM Studio
(its OpenAI-style server plus the command-line tool as a Python function). They run as real HTTP servers on spare ports.
"""
import asyncio
import json
import socket

from aiohttp import web


from tests.fixtures.portpool import free_port  # noqa: E402,F401  (one shared allocator: see portpool.py)


class _Server:
    def __init__(self):
        self.port = free_port()
        self.runner = None
        self.http_calls: list[str] = []           # "METHOD /path" for every request this fake answered (to count how often it is asked)

    def _counting(self):
        @web.middleware
        async def count(request, handler):
            self.http_calls.append(f"{request.method} {request.path}")
            return await handler(request)
        return count

    async def start(self):
        app = self.app()
        app.middlewares.append(self._counting())
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


async def _chat(request: web.Request, tag: str, calls: list) -> web.StreamResponse:
    body = await request.json()
    calls.append(body)
    last = body["messages"][-1]["content"]
    text = last if isinstance(last, str) else " ".join(p.get("text", "") for p in last if p.get("type") == "text")
    has_image = isinstance(last, list) and any(p.get("type") == "image_url" for p in last)
    reply = f"{tag}:{'[picture] ' if has_image else ''}{text}"
    if body.get("stream"):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for piece in (reply[:len(reply) // 2], reply[len(reply) // 2:]):
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': piece}}]})}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp
    return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}]})


class FakeOllama(_Server):
    """Ollama's real routes: /api/version, /api/tags, /api/ps, /api/show, /api/generate, /api/pull, /api/delete, /v1/chat/completions."""

    def __init__(self, models=None, version="0.12.3"):
        super().__init__()
        self.version = version
        self.models = {name: {"size": size} for name, size in (models or {"gemma4:12b": 8_000_000_000}).items()}
        self.loaded: dict[str, dict] = {}                 # name -> {"size_vram": ...}
        self.vram_share: dict[str, float] = {}            # name -> fraction on the card when loaded
        self.generate_calls: list[dict] = []
        self.chat_calls: list[dict] = []
        self.deleted: list[str] = []
        self.pull_delay = 0.0
        self.pull_error = ""
        self.pulls: list[str] = []
        self.vision_models = {"llava:7b"}
        self.load_delay = 0.0

    def app(self):
        app = web.Application()
        app.router.add_get("/api/version", self.h_version)
        app.router.add_get("/v1/models", self.h_v1_models)
        app.router.add_get("/api/tags", self.h_tags)
        app.router.add_get("/api/ps", self.h_ps)
        app.router.add_post("/api/show", self.h_show)
        app.router.add_post("/api/generate", self.h_generate)
        app.router.add_post("/api/pull", self.h_pull)
        app.router.add_delete("/api/delete", self.h_delete)
        app.router.add_post("/v1/chat/completions", self.h_chat)
        return app

    async def h_version(self, r): return web.json_response({"version": self.version})

    async def h_v1_models(self, r):
        return web.json_response({"object": "list", "data": [{"id": n, "object": "model", "owned_by": "library"} for n in self.models]})

    async def h_tags(self, r):
        return web.json_response({"models": [{"name": n, "model": n, "size": m["size"], "details": {"families": ["clip"] if n in self.vision_models else []}}
                                             for n, m in self.models.items()]})

    async def h_ps(self, r):
        return web.json_response({"models": [{"name": n, "model": n, "size": self.models[n]["size"],
                                              "size_vram": int(self.models[n]["size"] * self.vram_share.get(n, 1.0))} for n in self.loaded]})

    async def h_show(self, r):
        body = await r.json()
        if body.get("model") not in self.models:
            return web.json_response({"error": "model not found"}, status=404)
        return web.json_response({"capabilities": ["completion"] + (["vision"] if body["model"] in self.vision_models else [])})

    async def h_generate(self, r):
        body = await r.json()
        self.generate_calls.append(body)
        name = body.get("model")
        if name not in self.models:
            return web.json_response({"error": f"model '{name}' not found"}, status=404)
        if self.load_delay:
            await asyncio.sleep(self.load_delay)
        if body.get("keep_alive") == 0:
            self.loaded.pop(name, None)
        else:
            self.loaded[name] = {}
        return web.json_response({"model": name, "done": True})

    async def h_pull(self, r):
        body = await r.json()
        name = body["model"]
        self.pulls.append(name)
        resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await resp.prepare(r)
        if self.pull_error:
            await resp.write((json.dumps({"error": self.pull_error}) + "\n").encode())
            await resp.write_eof()
            return resp
        await resp.write(b'{"status":"pulling manifest"}\n')
        total = 1000
        for done in (0, 250, 500, 750, 1000):
            await resp.write((json.dumps({"status": "pulling abc", "digest": "sha256:abc", "total": total, "completed": done}) + "\n").encode())
            if self.pull_delay:
                await asyncio.sleep(self.pull_delay)
        self.models[name] = {"size": total}
        await resp.write(b'{"status":"success"}\n')
        await resp.write_eof()
        return resp

    async def h_delete(self, r):
        body = await r.json()
        if body["model"] not in self.models:
            return web.json_response({"error": f"model '{body['model']}' not found"}, status=404)
        self.models.pop(body["model"])
        self.loaded.pop(body["model"], None)
        self.deleted.append(body["model"])
        return web.json_response({})

    async def h_chat(self, r): return await _chat(r, "ollama", self.chat_calls)


class FakeLmStudio(_Server):
    """LM Studio's OpenAI-style server on its port, plus `lms` (its command-line tool) as an async function."""

    def __init__(self, models=None, server_up=True):
        super().__init__()
        self.library = models or [
            {"type": "llm", "modelKey": "mistral-small-3.2-24b", "displayName": "Mistral Small 3.2 24B", "path": "lmstudio-community/mistral/mistral-q4.gguf", "sizeBytes": 14_000_000_000},
            {"type": "embedding", "modelKey": "nomic-embed", "displayName": "Nomic", "path": "nomic/nomic.gguf", "sizeBytes": 100_000_000},
        ]
        self.loaded: dict[str, str] = {}          # modelKey -> identifier
        self.cli_calls: list[list] = []
        self.chat_calls: list[dict] = []
        self.server_up = server_up
        self.get_output = ["Resolving...", "Downloading 10%", "Downloading 55%", "Downloading 100%"]
        self.get_has_progress = True

    def app(self):
        app = web.Application()
        app.router.add_get("/v1/models", self.h_models)
        app.router.add_get("/api/v0/models", self.h_v0_models)
        app.router.add_post("/v1/chat/completions", self.h_chat)
        return app

    async def h_v0_models(self, r):
        return web.json_response({"data": [{"id": m["modelKey"], "type": "vlm" if m.get("vision") else m["type"], "state": "loaded" if m["modelKey"] in self.loaded else "not-loaded", **({"arch": m["arch"]} if m.get("arch") else {})} for m in self.library]})

    async def h_models(self, r):
        return web.json_response({"data": [{"id": k} for k in self.loaded]})

    async def h_chat(self, r): return await _chat(r, "lmstudio", self.chat_calls)

    async def cli(self, args, timeout=60):
        """The `lms` tool: (return code, stdout, stderr)."""
        self.cli_calls.append(list(args))
        cmd = args[0]
        if cmd == "version":
            return 0, "lms - LM Studio CLI\nCLI commit: abc  v0.4.1\n", ""
        if cmd == "ls":
            return 0, "Found models:\n" + json.dumps(self.library), ""
        if cmd == "ps":
            return 0, json.dumps([{"identifier": ident, "modelKey": key} for key, ident in self.loaded.items()]), ""
        if cmd == "load":
            if not any(m["modelKey"] == args[1] for m in self.library):
                return 1, "", f"Model '{args[1]}' not found"
            self.loaded[args[1]] = args[1]
            return 0, "Loaded", ""
        if cmd == "unload":
            self.loaded = {} if args[1] == "--all" else {k: v for k, v in self.loaded.items() if v != args[1]}
            return 0, "Unloaded", ""
        if cmd == "server" and args[1] == "start":
            if not self.runner:
                await self.start()
            return 0, "Started", ""
        if cmd == "server" and args[1] == "stop":
            await self.stop()
            return 0, "Stopped", ""
        return 1, "", "unknown command"
