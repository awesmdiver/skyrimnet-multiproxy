"""Stand-ins for the Benchmarks runner's tests: a fake llama.cpp chat engine (a real local HTTP server that streams tokens and returns llama.cpp-shaped timings), a launcher that starts
it, and a fake graphics card that answers `nvidia-smi`. Nothing here touches the real engine, a real card, or the director's settings."""
import asyncio
import json
import threading
import time

from aiohttp import web

import benchmarks_contract as bc
import benchmarks_runner as br
import benchmarks_sampler as bsm

THERMAL_5090 = """
GPU 00000000:01:00.0
    Temperature
        GPU Current Temp                               : 34.88 C
        GPU Current T.Limit Temp                       : 55 C
        GPU Shutdown T.Limit Temp Specification        : -5 C
        GPU Slowdown T.Limit Temp Specification        : -2 C
        GPU Max Operating T.Limit Temp Specification   : 0 C
"""


class FakeEngine:
    """Serves /health, /props and a streaming /v1/chat/completions on 127.0.0.1 with settable delays. `peak` is the most requests in flight at once."""

    def __init__(self, slots: int = 1, first_s: float = 0.01, token_s: float = 0.002, tokens: int = 8, fail_every: int = 0, hang: bool = False, die_after: int = 0, answer=None, finish: str = "stop"):
        self.slots, self.first_s, self.token_s, self.tokens = slots, first_s, token_s, tokens
        self.fail_every, self.hang, self.die_after = fail_every, hang, die_after
        self.answer, self.finish = answer, finish                  # answer: text, or a function of the request's messages; every request is also kept in `seen`
        self.seen = []
        self.count = 0
        self.inflight = 0
        self.peak = 0
        self.running = False
        self.port = 0
        self._loop = None
        self._runner = None
        self._thread = None
        self._ready = threading.Event()
        self.timings = True
        self.stream_usage = True

    async def _chat(self, request):
        body = await request.json()
        self.count += 1
        n = self.count
        if self.die_after and n > self.die_after:
            asyncio.get_running_loop().call_soon(self._kill_soon)
            return web.Response(status=500, text="engine fell over")
        if self.fail_every and n % self.fail_every == 0:
            return web.Response(status=500, text="boom")
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self.hang:
                await asyncio.sleep(3600)
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await asyncio.sleep(self.first_s)
            self.seen.append(body)
            words = None
            if self.answer is not None:
                text = self.answer(body["messages"]) if callable(self.answer) else self.answer
                words = [w + " " for w in text.split(" ")]
            for i in range(len(words) if words else self.tokens):
                chunk = {"choices": [{"delta": {"content": words[i] if words else f"w{i} "}, "finish_reason": None}]}
                await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
                await asyncio.sleep(self.token_s)
            last = {"choices": [{"delta": {}, "finish_reason": self.finish}]}
            if self.timings:
                last["timings"] = {"prompt_n": 200, "prompt_ms": 100.0, "predicted_n": self.tokens, "predicted_ms": self.tokens * self.token_s * 1000 + 10.0}
            await resp.write(f"data: {json.dumps(last)}\n\n".encode())
            if self.stream_usage:
                await resp.write(f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 200, 'completion_tokens': self.tokens}})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            return resp
        finally:
            self.inflight -= 1

    def _kill_soon(self):
        self.running = False
        asyncio.ensure_future(self._runner.cleanup())

    async def _serve(self):
        app = web.Application()
        app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
        app.router.add_get("/props", lambda r: web.json_response({"total_slots": self.slots, "default_generation_settings": {"n_ctx": 4096 * self.slots}}))
        app.router.add_post("/v1/chat/completions", self._chat)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.running = True
        self._ready.set()

    def start(self) -> "FakeEngine":
        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
            self._loop.run_forever()
        self._thread = threading.Thread(target=run, daemon=True, name="fake-engine")
        self._thread.start()
        assert self._ready.wait(10)
        return self

    def stop(self) -> None:
        if self._loop is None:
            return
        self.running = False
        fut = asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)
        try:
            fut.result(5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop = None

    def die(self) -> None:
        self.stop()


class FakeCard:
    """A runner for the sampler: answers the nvidia-smi query with a card whose memory and temperature the test can change."""

    def __init__(self, vram_mb=1000, temp_c=40.0, margin_c=55.0, total_mb=32000, thermal=THERMAL_5090):
        self.vram_mb, self.temp_c, self.margin_c, self.total_mb, self.thermal = vram_mb, temp_c, margin_c, total_mb, thermal

    def __call__(self, cmd, timeout):
        if cmd == bsm.SMI_THERMAL:
            return "ok", self.thermal
        if cmd[0] == "nvidia-smi":
            return "ok", f"0, Fake GPU, {self.vram_mb}, {self.total_mb}, 5, {self.temp_c}, 100.0, 575.0, 1500, 30, {self.margin_c}\n"
        return "error", ""


class FakeHandle:
    def __init__(self, launcher, engine, slots, index):
        self.launcher, self.engine, self.slots, self.index = launcher, engine, slots, index
        self.port, self.alias, self.pid = engine.port, "benchmark", None
        self.resolved = {"layers_on_card": 33, "layers_total": 33, "engine_build": "b-fake", "ctx": 4096 * slots, "slots": slots, "flash_attn": "auto"}

    async def alive(self):
        return self.engine.running

    def log_tail(self, n=600):
        return "fake engine log: out of memory"

    async def stop(self):
        self.launcher.stops += 1
        if self.engine.running:
            self.engine.stop()
        self.launcher.card.vram_mb = self.launcher.idle_mb


class FakeLauncher:
    """Starts a FakeEngine per start(); `engine_kwargs` (a function of the start number) sets its behaviour; the card's memory rises by `model_mb` while it runs."""

    def __init__(self, card: FakeCard, model_mb=8000, engine_kwargs=None, fail_start: str = ""):
        self.card, self.model_mb, self.idle_mb = card, model_mb, card.vram_mb
        self.engine_kwargs = engine_kwargs or (lambda n: {})
        self.fail_start = fail_start
        self.starts, self.stops = 0, 0
        self.slots_seen: list = []
        self.overrides_seen: list = []
        self.engines: list = []

    async def start(self, model_path, slots, overrides, log_path, run_id):
        self.starts += 1
        self.overrides_seen.append(dict(overrides))
        if self.fail_start:
            raise br.EngineStartError(self.fail_start, "fake log tail")
        kw = dict(self.engine_kwargs(self.starts))
        eng = FakeEngine(slots=slots, **kw).start()
        self.engines.append(eng)
        self.slots_seen.append(slots)
        self.card.vram_mb = self.idle_mb + self.model_mb
        await asyncio.sleep(0.02)
        return FakeHandle(self, eng, slots, self.starts)


def make_service(base, launcher, card, **kw):
    """A service wired to the fake engine and card; fast sampler, fast heartbeat and heat check."""
    hooks = kw.pop("hooks", None) or br.Hooks()
    hooks.sampler_factory = lambda on_sample, plan: bsm.ReadingsSampler(on_sample=on_sample, runner=card, interval_s=0.03, machine_reader=lambda: {"cpu_util_pct": 5.0, "ram_used_mb": 9000},
                                                                         ceiling_c=plan.ceiling_c)
    hooks.version = "test"
    kw.setdefault("heartbeat_s", 0.05)
    kw.setdefault("thermal_poll_s", 0.03)
    return br.BenchmarkService(base, launcher, hooks, **kw)


def write_model(folder, name="tiny.gguf", size=2048):
    p = folder / name
    p.write_bytes(b"GGUF" + b"\0" * size)
    return str(p)


def plan_body(models, **kw):
    body = {"models": models, "levels": [1], "requests_per_level": 4, "sustained": False, "cooldown": False}
    body.update(kw)
    return body


async def wait_for(cond, timeout=20.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def read_rows(folder, name):
    import benchmarks_store as bs
    return bs.read_jsonl(folder / name)
