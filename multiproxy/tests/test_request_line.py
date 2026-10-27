"""Tests for the request line ([Concurrency] in multiproxy.ini) -- ported from Raydonn's fork
(2026-09-27), with real active/queued counters and a slot that always comes back, including when
SkyrimNet hangs up mid-stream.
"""
import asyncio
import gc
import json

import httpx
import pytest
from fastapi import HTTPException


def _set_limit(monkeypatch, proxy_module, provider, limit, timeout=60.0):
    limits = dict(proxy_module.REQUEST_LIMITS)
    limits[provider] = limit
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", limits)
    monkeypatch.setattr(proxy_module, "QUEUE_TIMEOUT_SECONDS", timeout)


def _snap(proxy_module):
    return proxy_module._request_line_snapshot()


# --- the counter class ---

@pytest.mark.asyncio
async def test_line_counts_active_and_queued(proxy_module):
    line = proxy_module._RequestLine(2)
    await line.acquire(1)
    await line.acquire(1)
    assert (line.active, line.queued) == (2, 0)
    waiter = asyncio.create_task(line.acquire(5))
    await asyncio.sleep(0.01)
    assert (line.active, line.queued) == (2, 1)
    line.release()                       # hands its slot straight to the waiter
    await waiter
    assert (line.active, line.queued) == (2, 0)
    line.release()
    line.release()
    assert (line.active, line.queued) == (0, 0)


@pytest.mark.asyncio
async def test_line_serves_waiters_in_order(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)
    order = []

    async def go(name):
        await line.acquire(5)
        order.append(name)

    tasks = [asyncio.create_task(go(n)) for n in ("a", "b", "c")]
    await asyncio.sleep(0.01)
    assert line.queued == 3
    for _ in range(3):
        line.release()
        await asyncio.sleep(0.01)
    assert order == ["a", "b", "c"]
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_line_timeout_removes_the_waiter(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)
    with pytest.raises(proxy_module._RequestLineTimeout):
        await line.acquire(0.05)
    assert (line.active, line.queued) == (1, 0)


@pytest.mark.asyncio
async def test_cancelled_waiter_leaves_the_line_cleanly(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)
    waiter = asyncio.create_task(line.acquire(5))
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert (line.active, line.queued) == (1, 0)
    line.release()
    assert line.active == 0


def test_slot_release_is_idempotent(proxy_module):
    line = proxy_module._RequestLine(3)
    line.active = 1
    slot = proxy_module._Slot(line)
    slot.release()
    slot.release()
    assert line.active == 0


# --- acquiring, limits and the 429 ---

@pytest.mark.asyncio
async def test_no_limit_means_no_slot(proxy_module):
    assert await proxy_module._acquire_request_slot("openai", "rid") is None
    assert await proxy_module._acquire_request_slot(None, "rid") is None
    assert _snap(proxy_module) == {}


@pytest.mark.asyncio
async def test_over_limit_request_gets_a_friendly_429_with_retry_after(proxy_module, monkeypatch):
    """429 (not 503) with Retry-After, and a SHORT wait: SkyrimNet Beta 25's smart routing treats 429
    as a rate limit and routes around it; a request that just sits there only looks slow to it."""
    _set_limit(monkeypatch, proxy_module, "glm", 1, timeout=0.05)
    held = await proxy_module._acquire_request_slot("glm", "a")
    with pytest.raises(HTTPException) as exc:
        await proxy_module._acquire_request_slot("glm", "b")
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": str(proxy_module.RETRY_AFTER_SECONDS)}
    assert "GLM is busy right now" in exc.value.detail and "its only spot" in exc.value.detail
    assert "stepped aside" in exc.value.detail
    assert _snap(proxy_module) == {"glm": {"active": 1, "queued": 0, "limit": 1}}
    held.release()
    assert _snap(proxy_module)["glm"]["active"] == 0


def test_default_limits_are_glm_six_and_the_rest_zero(proxy_module):
    import configparser
    limits, timeout = proxy_module._read_concurrency_ini(configparser.ConfigParser())
    assert limits["glm"] == 6
    free_allowances = {s.id: s.limit_default for s in proxy_module.key_sources.SOURCES}          # Mistral and Groq start at 2 (their free plans take few requests at once)
    assert free_allowances == {"cloudflare": 0, "mistral": 2, "groq": 2}
    assert all(limits[k] == v for k, v in free_allowances.items())
    assert all(v == 0 for k, v in limits.items() if k != "glm" and k not in free_allowances)
    assert timeout == 10.0   # short on purpose -- see the 429 test above


def test_ini_values_are_read_and_bad_ones_fall_back(proxy_module):
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read_string("[Concurrency]\nGLM = 2\nOpenAI = 4\nNanoGPT = lots\nDeepSeek = -3\nQueueTimeoutSeconds = 15\n")
    limits, timeout = proxy_module._read_concurrency_ini(cfg)
    assert (limits["glm"], limits["openai"], limits["nano"], limits["deepseek"]) == (2, 4, 0, 0)
    assert timeout == 15.0


# --- end to end through the real route ---

def _client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t")


def _body(proxy_module, stream=False):
    return {"model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": stream, "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_second_request_waits_then_runs_and_counters_show_it(proxy_module, monkeypatch):
    _set_limit(monkeypatch, proxy_module, "glm", 1)
    gate = asyncio.Event()
    started = []

    async def direct(system_prompt, messages, model, max_tokens, **extra):
        started.append(1)
        await gate.wait()
        return "ok", {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    async with _client(proxy_module) as client:
        a = asyncio.create_task(client.post("/v1/chat/completions", json=_body(proxy_module)))
        await asyncio.sleep(0.05)
        b = asyncio.create_task(client.post("/v1/chat/completions", json=_body(proxy_module)))
        await asyncio.sleep(0.05)
        assert len(started) == 1
        assert _snap(proxy_module) == {"glm": {"active": 1, "queued": 1, "limit": 1}}
        live = (await client.get("/stats/request-line")).json()
        assert live == {"glm": {"active": 1, "queued": 1, "limit": 1}}
        gate.set()
        ra, rb = await asyncio.gather(a, b)
    assert ra.status_code == rb.status_code == 200
    assert len(started) == 2
    assert _snap(proxy_module) == {"glm": {"active": 0, "queued": 0, "limit": 1}}


@pytest.mark.asyncio
async def test_queue_timeout_returns_429_and_the_holder_is_unaffected(proxy_module, monkeypatch):
    _set_limit(monkeypatch, proxy_module, "glm", 1, timeout=0.1)
    gate = asyncio.Event()

    async def direct(system_prompt, messages, model, max_tokens, **extra):
        await gate.wait()
        return "ok", {}

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    async with _client(proxy_module) as client:
        a = asyncio.create_task(client.post("/v1/chat/completions", json=_body(proxy_module)))
        await asyncio.sleep(0.05)
        b = await client.post("/v1/chat/completions", json=_body(proxy_module))
        assert b.status_code == 429
        assert b.headers["retry-after"] == str(proxy_module.RETRY_AFTER_SECONDS)
        assert "GLM is busy right now" in b.json()["error"]["message"]
        gate.set()
        assert (await a).status_code == 200
    assert _snap(proxy_module)["glm"]["active"] == 0


@pytest.mark.asyncio
async def test_a_failing_request_still_gives_its_slot_back(proxy_module, monkeypatch):
    _set_limit(monkeypatch, proxy_module, "glm", 1)

    async def direct(system_prompt, messages, model, max_tokens, **extra):
        raise HTTPException(status_code=502, detail="upstream broke")

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    async with _client(proxy_module) as client:
        for _ in range(3):   # would hang on the 2nd if the first leaked its slot
            r = await asyncio.wait_for(client.post("/v1/chat/completions", json=_body(proxy_module)), 5)
            assert r.status_code == 502
    assert _snap(proxy_module)["glm"]["active"] == 0


@pytest.mark.asyncio
async def test_streaming_request_holds_the_slot_until_the_stream_ends(proxy_module, monkeypatch):
    _set_limit(monkeypatch, proxy_module, "glm", 1)
    seen_active = []

    async def streaming(system_prompt, messages, model, max_tokens, **extra):
        seen_active.append(_snap(proxy_module)["glm"]["active"])
        yield 'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(proxy_module, "call_glm_streaming", streaming)
    async with _client(proxy_module) as client:
        r = await client.post("/v1/chat/completions", json=_body(proxy_module, stream=True))
    assert r.status_code == 200 and "hi" in r.text
    assert seen_active == [1]
    assert _snap(proxy_module)["glm"]["active"] == 0


# --- slot release when a stream is cut short ---

async def _endless():
    i = 0
    while True:
        i += 1
        yield f'data: {{"choices":[{{"index":0,"delta":{{"content":"w{i} "}},"finish_reason":null}}]}}\n\n'
        await asyncio.sleep(0)


async def _held_slot(proxy_module, monkeypatch):
    _set_limit(monkeypatch, proxy_module, "glm", 1)
    slot = await proxy_module._acquire_request_slot("glm", "rid")
    assert _snap(proxy_module)["glm"]["active"] == 1
    return slot


@pytest.mark.asyncio
async def test_client_hanging_up_mid_stream_releases_the_slot(proxy_module, monkeypatch):
    slot = await _held_slot(proxy_module, monkeypatch)
    gen = proxy_module._finish_stream(_endless(), "rid", "m", slot)
    await gen.__anext__()
    await gen.__anext__()
    await gen.aclose()                   # what Starlette does when the client disconnects
    assert _snap(proxy_module)["glm"]["active"] == 0


@pytest.mark.asyncio
async def test_cancelled_stream_releases_the_slot(proxy_module, monkeypatch):
    slot = await _held_slot(proxy_module, monkeypatch)

    async def consume():
        async for _ in proxy_module._finish_stream(_endless(), "rid", "m", slot):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _snap(proxy_module)["glm"]["active"] == 0


@pytest.mark.asyncio
async def test_upstream_error_mid_stream_ends_cleanly_and_releases_the_slot(proxy_module, monkeypatch):
    slot = await _held_slot(proxy_module, monkeypatch)

    async def boom():
        yield 'data: {"choices":[{"index":0,"delta":{"content":"Half"},"finish_reason":null}]}\n\n'
        raise RuntimeError("upstream exploded")

    events = [e async for e in proxy_module._finish_stream(boom(), "rid", "m", slot)]
    assert events[-1] == "data: [DONE]\n\n"          # ended cleanly...
    assert "exploded" not in "".join(events)         # ...with no error wording in the reply
    assert _snap(proxy_module)["glm"]["active"] == 0


@pytest.mark.asyncio
async def test_a_stream_that_never_started_still_gives_the_slot_back(proxy_module, monkeypatch):
    slot = await _held_slot(proxy_module, monkeypatch)
    gen = proxy_module._finish_stream(_endless(), "rid", "m", slot)
    del gen                              # dropped before a single chunk was read
    gc.collect()
    assert _snap(proxy_module)["glm"]["active"] == 0
