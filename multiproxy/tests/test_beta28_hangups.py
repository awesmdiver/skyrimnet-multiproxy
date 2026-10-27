"""SkyrimNet Beta 28 hangs up on lines it no longer needs (2026-10-20): a hung-up request must stop the AI behind it at once.

Beta 28 plays one line at a time and cancels lines that were replaced or purged, so SkyrimNet closes the connection mid-reply far more often than before. For each kind of
request (a streamed chat, a whole-reply chat, a route with a backup, a decision, a voice line) a real server on a real socket is hung up on by a real client, and a stand-in
AI on its own socket proves the request to it was stopped within about a second, nothing was started for a backup, and nothing stays held. Stand-ins only: no real AI,
key or engine.
"""
import asyncio
import json
import logging
import time

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

import routing
import voice_service as vs
from tests.fixtures.courier_fakes import free_port
from tests.test_client_disconnect import _live_server, _until

SECOND = 1.5          # "about a second": the server looks every half second, and the stand-in notices a closed socket within a few hundredths


class Upstream:
    """A stand-in AI on 127.0.0.1. `stall-*` models say nothing until the caller leaves, `dribble-*` models stream a word every 30 ms for ever, `good-*` answer at once.
    `closed` lists the models whose connection was seen to close; `calls` every model asked."""

    def __init__(self):
        self.port = free_port()
        self.calls: list = []
        self.closed: list = []
        self.runner = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_post("/v1/systemone", self.systemone)
        app.router.add_post("/api/v1/systemone", self.systemone)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def _wait_closed(self, request, label):
        for _ in range(400):                         # at most 12 s: a test that is broken must not hang
            if request.transport is None or request.transport.is_closing():
                self.closed.append(label)
                return True
            await asyncio.sleep(0.03)
        return False

    async def chat(self, request):
        body = await request.json()
        model = body["model"]
        self.calls.append(model)
        if model.startswith("stall"):
            await self._wait_closed(request, model)
            return web.Response(status=499)
        if model.startswith("dribble") and body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            try:
                for i in range(400):
                    await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'w%d ' % i}}]})}\n\n".encode())
                    await asyncio.sleep(0.03)
                    if request.transport is None or request.transport.is_closing():
                        break
            except (ConnectionResetError, ConnectionError):
                pass
            self.closed.append(model)
            return resp
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'answer from ' + model}}]})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "answer from " + model}, "finish_reason": "stop"}], "usage": {}})

    async def systemone(self, request):
        body = await request.json()
        self.calls.append("decision")
        if body.get("model", "").startswith("stall") or request.headers.get("X-Stall"):
            await self._wait_closed(request, "decision")
            return web.Response(status=499)
        return web.json_response({"answers": {}, "usage": {}})


@pytest_asyncio.fixture
async def ai(proxy_module, monkeypatch):
    up = await Upstream().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", up.base)
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    proxy_module._routing_engine.__init__(proxy_module._ai_class, key=lambda a: proxy_module._resolve_model_name(a))
    proxy_module._job_table_cache["ts"] = 0.0
    yield up
    await up.stop()


def _nothing_held(pm) -> bool:
    return all(v["active"] == 0 and v["queued"] == 0 for v in pm._request_line_snapshot().values()) and pm._in_flight.count() == 0


def _chat(model, stream):
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": "hello"}]}


async def _hang_up_after(r, seconds):
    await asyncio.sleep(seconds)
    r.close()
    return time.monotonic()


@pytest.mark.asyncio
async def test_a_streamed_reply_hung_up_mid_way_stops_the_ai_and_holds_nothing(proxy_module, ai, caplog):
    with caplog.at_level(logging.INFO, logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            r = await http.post(f"{base}/v1/chat/completions", json=_chat("lmstudio/dribble-1", True))
            await r.content.readany()
            await r.content.readany()
            t0 = time.monotonic()
            r.close()
            assert await _until(lambda: "dribble-1" in ai.closed, SECOND + 1), "the AI was never told to stop"
            assert time.monotonic() - t0 < SECOND + 0.5
            assert await _until(lambda: _nothing_held(proxy_module))
    assert ai.calls == ["dribble-1"], "no second request was started for a line that was dropped"
    assert "SkyrimNet hung up before the reply was finished" in caplog.text and "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_a_whole_reply_hung_up_before_the_ai_answers_stops_it_and_says_so_in_the_log(proxy_module, ai, caplog):
    with caplog.at_level(logging.INFO, logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            task = asyncio.ensure_future(http.post(f"{base}/v1/chat/completions", json=_chat("lmstudio/stall-1", False)))
            assert await _until(lambda: ai.calls == ["stall-1"])
            await asyncio.sleep(0.2)
            t0 = time.monotonic()
            task.cancel()                                  # the client closes its connection
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await _until(lambda: "stall-1" in ai.closed, SECOND + 1), "the AI was never told to stop"
            assert time.monotonic() - t0 < SECOND + 0.5
            assert await _until(lambda: _nothing_held(proxy_module))
    assert "SkyrimNet hung up before the reply was ready" in caplog.text
    assert ai.calls == ["stall-1"]


@pytest.mark.asyncio
async def test_a_route_does_not_start_its_backup_for_a_line_that_was_dropped(proxy_module, ai):
    """MultiProxy never asks two AIs at once: a route asks the first, and only when it fails or is too slow does the next get the line. So the thing to prove is that a hang-up
    during the first ends the whole route: the first is stopped and the second is never asked."""
    cfg = proxy_module._load_config()
    cfg["routing"] = {"routes": {"memory": {"ais": ["lmstudio/stall-1", "lmstudio/good-1"], "slow_limit_s": 30}}}
    proxy_module._save_config(cfg)
    async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
        task = asyncio.ensure_future(http.post(f"{base}/v1/chat/completions", json=_chat("job/memory", True)))
        assert await _until(lambda: ai.calls == ["stall-1"])
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await _until(lambda: "stall-1" in ai.closed, SECOND + 1)
        assert await _until(lambda: _nothing_held(proxy_module))
        await asyncio.sleep(1.0)                                         # long enough for a backup to have been started, had it been going to be
    assert ai.calls == ["stall-1"], "the backup was asked for a line nobody was waiting for"


# ---- decisions ----

DECISION = {"model": "typesafe/jev-latest", "state": {"player": "x"}, "questions": {"intent": {"type": "choice", "options": ["a", "b"]}}}


@pytest_asyncio.fixture
async def paid_decisions(proxy_module, ai, monkeypatch):
    """TypeSafe's address is the stand-in (which says nothing until the caller leaves) and Nano-GPT's is a second one that answers: a decision that was dropped must never reach it."""
    nano = await Upstream().start()
    monkeypatch.setattr(proxy_module, "TYPESAFE_BASE_URL", f"http://127.0.0.1:{ai.port}/v1/systemone")
    monkeypatch.setattr(proxy_module, "NANO_DECISIONS_URL", f"http://127.0.0.1:{nano.port}/api/v1/systemone")
    monkeypatch.setattr(proxy_module, "typesafe_api_key", "FAKE-KEY-1")
    monkeypatch.setattr(proxy_module, "nano_api_key", "FAKE-KEY-2")
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)
    monkeypatch.setattr(proxy_module.auth, "session", None, raising=False)

    class Pair:
        pass
    p = Pair()
    p.typesafe, p.nano = ai, nano
    yield p
    await nano.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/systemone", "/v1/chat/completions"])
async def test_a_decision_hung_up_on_stops_the_ai_and_is_never_passed_to_the_next_answerer(proxy_module, paid_decisions, caplog, path):
    ts, nano = paid_decisions.typesafe, paid_decisions.nano
    body = {**DECISION, "model": "stall-jev"}
    with caplog.at_level(logging.INFO, logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            task = asyncio.ensure_future(http.post(f"{base}{path}", json=body))
            assert await _until(lambda: ts.calls == ["decision"])
            await asyncio.sleep(0.2)
            t0 = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await _until(lambda: "decision" in ts.closed, SECOND + 1), "TypeSafe's request was never stopped"
            assert time.monotonic() - t0 < SECOND + 0.5
            assert await _until(lambda: proxy_module._in_flight.count() == 0)
            await asyncio.sleep(1.0)
    assert nano.calls == [], "the next answerer was asked for a decision nobody was waiting for"
    assert "SkyrimNet hung up before the " in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_an_ollama_decision_is_stopped_when_the_caller_leaves(proxy_module, ai, monkeypatch):
    """The Ollama decision call runs its request apart so a model that is only slow can finish loading: a hang-up must still stop it (a slow one is not a hang-up)."""
    monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", ai.base)
    ai.calls.clear()
    task = asyncio.ensure_future(proxy_module.call_ollama_decision("stall-model", {}, {"q": {}}, "t1", "/v1/systemone", 30.0))
    assert await _until(lambda: ai.calls == ["decision"])
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _until(lambda: "decision" in ai.closed, SECOND), "the request to Ollama was left running"


# ---- voice ----

class _Engine:
    """The voice engine as the service sees it: ready, one line at a time, a line takes as long as the test says and notes when it was stopped."""

    def __init__(self):
        self.waiting_fn = None
        self.started: list = []
        self.stopped: list = []
        self.finished: list = []
        self.hold = 30.0

    def status(self):
        return {"ready": True}

    async def synthesize(self, text, ref_wav=None, **kw):
        self.started.append(text)
        try:
            await asyncio.sleep(self.hold if "slow" in text else 0.05)
        except asyncio.CancelledError:
            self.stopped.append(text)
            raise
        self.finished.append(text)
        from tests.test_voice_service import wav
        return type("R", (), {"wav": wav(24000, seconds=0.2), "path": None, "wall_ms": 50, "audio_ms": 200, "rtf": 0.1})()


@pytest_asyncio.fixture
async def voice(tmp_path, proxy_module):
    from fastapi import FastAPI
    logs: list = []
    engine = _Engine()
    service = vs.VoiceService(engine, lambda: tmp_path / "work", log=logs.append)
    app = FastAPI()
    vs.mount(app, service)
    # a real server on a real socket, because only that can hang up
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None, lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    v = type("V", (), {})()
    v.engine, v.service, v.logs, v.base = engine, service, logs, f"http://127.0.0.1:{port}"
    yield v
    server.should_exit = True
    server.force_exit = True
    await asyncio.wait_for(task, 10)


@pytest.mark.asyncio
async def test_a_voice_line_hung_up_on_is_stopped_and_the_next_line_gets_the_engine_at_once(voice):
    """OpenAI-Compatible: `POST /v1/audio/speech` waits for the finished sound. Hung up on, the line is stopped, the engine's turn goes to the next line, and the log says it was given up."""
    async with aiohttp.ClientSession() as http:
        slow = asyncio.ensure_future(http.post(f"{voice.base}/v1/audio/speech", json={"model": "higgs", "input": "a slow line", "voice": "v1"}))
        assert await _until(lambda: voice.engine.started == ["a slow line"])
        nxt = asyncio.ensure_future(http.post(f"{voice.base}/v1/audio/speech", json={"model": "higgs", "input": "the next line", "voice": "v1"}))
        await asyncio.sleep(0.3)
        assert voice.engine.started == ["a slow line"], "the next line is waiting its turn"
        t0 = time.monotonic()
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        assert await _until(lambda: voice.engine.stopped == ["a slow line"], SECOND + 1)
        r = await asyncio.wait_for(nxt, 5)
        assert r.status == 200 and (await r.read())[:4] == b"RIFF"
        assert time.monotonic() - t0 < SECOND + 1.0, "the next line had to wait for a line nobody wanted"
    assert any("hung up" in m and "given up on" in m for m in voice.logs)
    assert not any("failed" in m for m in voice.logs), "a hang-up is not a fault of the voice"
    assert voice.service.queue.waiting == 0


@pytest.mark.asyncio
async def test_a_gradio_voice_line_hung_up_on_is_stopped_too(voice):
    """The Higgs flow: the line is started with one request and its sound is waited for with a second; hanging up on the second stops the line."""
    async with aiohttp.ClientSession() as http:
        r = await http.post(f"{voice.base}/gradio_api/call/generate_audio", json={"data": ["a slow line", None, 0.8]})
        ev = (await r.json())["event_id"]
        waiting = asyncio.ensure_future(http.get(f"{voice.base}/gradio_api/call/generate_audio/{ev}"))
        assert await _until(lambda: voice.engine.started == ["a slow line"])
        await asyncio.sleep(0.2)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert await _until(lambda: voice.engine.stopped == ["a slow line"], SECOND + 1)
        assert await _until(lambda: voice.service.queue.waiting == 0 and voice.service.jobs[ev].done.is_set())
    assert voice.service.jobs[ev].result_path is None, "no half-made sound is kept"
    assert any("given up on" in m for m in voice.logs)


@pytest.mark.asyncio
async def test_a_line_that_finishes_is_still_logged_and_counted(voice):
    """Guard for the tests above: giving up must not have switched the normal record off."""
    seen: list = []
    voice.service.on_line = lambda seconds, ok: seen.append(ok)
    async with aiohttp.ClientSession() as http:
        r = await http.post(f"{voice.base}/v1/audio/speech", json={"model": "higgs", "input": "quick", "voice": "v1"})
        assert r.status == 200
        await r.read()
    assert seen == [True] and any("answered" in m for m in voice.logs)
