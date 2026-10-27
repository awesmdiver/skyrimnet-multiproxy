"""Tests for SkyrimNet hanging up mid-request (2026-09-27, SkyrimNet Beta 25).

Beta 25's "first-token guard" starts a second request when a provider is slow to start replying and
DROPS whichever one loses the race -- so a client disconnect is now routine, not rare. When it happens:
the request-line slot must come back, the upstream connection / CLI child process must be closed, the
Speeds tab must not record it as a provider failure, and nothing may log a scary traceback.

The route-level tests run against a REAL uvicorn server on a real socket and use a real aiohttp client
that really hangs up -- the in-process TestClient can't disconnect mid-request, and this is exactly the
kind of behavior a mock would only confirm the test author's own assumption about.
"""
import asyncio
import contextlib
import socket
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import uvicorn


@contextlib.asynccontextmanager
async def _live_server(proxy_module):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(
        proxy_module.app, host="127.0.0.1", port=port, log_level="warning", log_config=None, lifespan="off",
    ))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        server.force_exit = True   # never wait on a request a broken test left in flight
        await asyncio.wait_for(task, 10)


async def _until(predicate, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


def _glm_body(proxy_module, stream):
    return {"model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": stream, "messages": [{"role": "user", "content": "hi"}]}


def _line(proxy_module):
    return proxy_module._request_line_snapshot().get("glm", {"active": 0, "queued": 0})


@pytest.fixture()
def one_glm_spot(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "glm": 1})
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    return proxy_module


def _no_speed_recorded(proxy_module):
    return proxy_module.speed_tracker.snapshot() == {}


def _gave_up_recorded(proxy_module):
    """A request SkyrimNet hung up on while the AI had not answered is saved as ONE failed request with the reason `gave up` (it used to be left out entirely, so a model that
    was asked and never answered did not show on Speeds at all). No success is saved for it."""
    import speed_store
    speed_store.flush()
    with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
        rows = conn.execute("SELECT success, fail_reason, provider FROM speed_history").fetchall()
    return rows == [(0, "gave up", "GLM")]


def _no_scary_logging(caplog):
    assert "Traceback" not in caplog.text
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL") or r.exc_info]


# --- hang up mid-stream ---

@pytest.mark.asyncio
async def test_hanging_up_mid_stream_frees_the_slot_closes_upstream_and_records_no_failure(one_glm_spot, monkeypatch, caplog):
    proxy_module = one_glm_spot
    state = {"closed": False}

    async def endless(system_prompt, messages, model, max_tokens, **extra):
        try:
            i = 0
            while True:
                i += 1
                yield 'data: {"choices":[{"index":0,"delta":{"content":"w%d "},"finish_reason":null}]}\n\n' % i
                await asyncio.sleep(0.02)
        finally:
            state["closed"] = True   # what closes the real upstream HTTP response

    monkeypatch.setattr(proxy_module, "call_glm_streaming", endless)
    with caplog.at_level("DEBUG", logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            r = await http.post(f"{base}/v1/chat/completions", json=_glm_body(proxy_module, True))
            await r.content.readany()
            await r.content.readany()
            assert _line(proxy_module)["active"] == 1
            r.close()   # SkyrimNet gives up on this reply
            assert await _until(lambda: _line(proxy_module)["active"] == 0), "the slot was never given back"
            assert await _until(lambda: state["closed"]), "the upstream stream was never closed"
    assert _no_speed_recorded(proxy_module), "a hang-up must not show up as a provider failure (or success)"
    _no_scary_logging(caplog)


@pytest.mark.asyncio
async def test_a_reply_that_finishes_normally_is_still_recorded(one_glm_spot, monkeypatch):
    """Guard for the test above: skipping the record on a hang-up must not have switched recording off."""
    proxy_module = one_glm_spot

    async def short(system_prompt, messages, model, max_tokens, **extra):
        yield 'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(proxy_module, "call_glm_streaming", short)
    async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
        r = await http.post(f"{base}/v1/chat/completions", json=_glm_body(proxy_module, True))
        await r.read()
    assert await _until(lambda: "GLM::glm-4.6" in proxy_module.speed_tracker.snapshot())
    assert proxy_module.speed_tracker.snapshot()["GLM::glm-4.6"]["success_pct"] == 100


# --- hang up before the first byte ---

@pytest.mark.asyncio
async def test_hanging_up_before_the_first_token_frees_the_slot_and_closes_upstream(one_glm_spot, monkeypatch, caplog):
    proxy_module = one_glm_spot
    state = {"started": False, "closed": False}

    async def slow_to_start(system_prompt, messages, model, max_tokens, **extra):
        state["started"] = True
        try:
            await asyncio.sleep(3600)
            yield "data: [DONE]\n\n"
        finally:
            state["closed"] = True

    monkeypatch.setattr(proxy_module, "call_glm_streaming", slow_to_start)
    with caplog.at_level("DEBUG", logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(http.post(f"{base}/v1/chat/completions", json=_glm_body(proxy_module, True)), 0.4)
            assert state["started"]
            assert await _until(lambda: state["closed"]), "the slow upstream request was never cancelled"
            assert await _until(lambda: _line(proxy_module)["active"] == 0), "the slot was never given back"
    assert _gave_up_recorded(proxy_module), "a hang-up before the first word is a failed request that gave up"
    _no_scary_logging(caplog)


@pytest.mark.asyncio
async def test_hanging_up_during_a_non_streaming_call_cancels_it(one_glm_spot, monkeypatch, caplog):
    proxy_module = one_glm_spot
    state = {"started": False, "cancelled": False}

    async def slow_direct(system_prompt, messages, model, max_tokens, **extra):
        state["started"] = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr(proxy_module, "call_glm_direct", slow_direct)
    with caplog.at_level("DEBUG", logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(http.post(f"{base}/v1/chat/completions", json=_glm_body(proxy_module, False)), 0.4)
            assert await _until(lambda: state["cancelled"]), "the abandoned request kept running"
            assert await _until(lambda: _line(proxy_module)["active"] == 0)
    assert _gave_up_recorded(proxy_module), "a hang-up during a plain call is a failed request that gave up"
    _no_scary_logging(caplog)


# --- hang up while still waiting in line ---

@pytest.mark.asyncio
async def test_a_request_that_hangs_up_while_queued_leaves_the_line_and_never_runs(one_glm_spot, monkeypatch, caplog):
    proxy_module = one_glm_spot
    monkeypatch.setattr(proxy_module, "QUEUE_TIMEOUT_SECONDS", 30.0)
    calls = []

    async def endless(system_prompt, messages, model, max_tokens, **extra):
        calls.append(1)
        while True:
            yield 'data: {"choices":[{"index":0,"delta":{"content":"w "},"finish_reason":null}]}\n\n'
            await asyncio.sleep(0.02)

    monkeypatch.setattr(proxy_module, "call_glm_streaming", endless)
    with caplog.at_level("DEBUG", logger="proxy"):
        async with _live_server(proxy_module) as base, aiohttp.ClientSession() as http:
            first = await http.post(f"{base}/v1/chat/completions", json=_glm_body(proxy_module, True))
            await first.content.readany()
            with pytest.raises(asyncio.TimeoutError):   # second request queues behind the first, then gives up
                await asyncio.wait_for(http.post(f"{base}/v1/chat/completions", json=_glm_body(proxy_module, True)), 0.4)
            assert await _until(lambda: _line(proxy_module)["queued"] == 0), "the abandoned request is still in line"
            assert len(calls) == 1, "an abandoned request must never reach the provider"
            first.close()
            assert await _until(lambda: _line(proxy_module)["active"] == 0)
            await asyncio.sleep(0.2)
            assert len(calls) == 1
    _no_scary_logging(caplog)


# --- CLI providers kill their child process ---

def _mock_proc():
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_codex_child_process_is_killed_when_the_request_is_abandoned(proxy_module):
    proc = _mock_proc()

    async def hang(*a, **k):
        await asyncio.sleep(3600)

    proc.communicate = hang
    killed = AsyncMock()
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/codex"), \
         patch.object(proxy_module, "_kill_process_tree", killed), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        task = asyncio.create_task(proxy_module._run_codex_exec("hello", "gpt-5.5", "rid"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    killed.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_antigravity_child_process_is_killed_when_the_stream_is_closed_early(proxy_module):
    proc = _mock_proc()
    proc.stdin = MagicMock(write=MagicMock(), drain=AsyncMock(), close=MagicMock())
    proc.stderr = MagicMock(read=AsyncMock(side_effect=[b""]))
    line = b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Hi"}}\n'
    proc.stdout = MagicMock(readline=AsyncMock(side_effect=[line, line, line]))
    killed = AsyncMock()
    with patch.object(proxy_module, "_agy_path", lambda: "/fake/agy"), \
         patch.object(proxy_module, "_kill_process_tree", killed), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        gen = proxy_module.call_geminicli_streaming(
            None, [{"role": "user", "content": "hi"}], "gemini-3.8-flash-low", 100,
        )
        first = await gen.__anext__()          # the first text has been sent...
        assert "Hi" in first
        await gen.aclose()                     # ...then SkyrimNet hangs up
    killed.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_antigravity_child_process_is_not_killed_after_a_normal_finish(proxy_module):
    """Guard for the test above: a stream that ends normally must keep the old, gentle cleanup."""
    proc = _mock_proc()
    proc.returncode = 0
    proc.stdin = MagicMock(write=MagicMock(), drain=AsyncMock(), close=MagicMock())
    proc.stderr = MagicMock(read=AsyncMock(side_effect=[b""]))
    line = b'{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"Hi"}}\n'
    proc.stdout = MagicMock(readline=AsyncMock(side_effect=[line, b""]))
    killed = AsyncMock()
    with patch.object(proxy_module, "_agy_path", lambda: "/fake/agy"), \
         patch.object(proxy_module, "_kill_process_tree", killed), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        events = [e async for e in proxy_module.call_geminicli_streaming(
            None, [{"role": "user", "content": "hi"}], "gemini-3.8-flash-low", 100,
        )]
    assert events[-1] == "data: [DONE]\n\n"
    killed.assert_not_awaited()
