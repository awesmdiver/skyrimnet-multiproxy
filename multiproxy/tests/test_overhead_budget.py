"""A guard for the future (2026-10-02): MultiProxy's own added time for a simple request must stay a few thousandths of a second.

A real MultiProxy is started on spare ports with a scratch settings folder (and a scratch home folder, so it can't find any real login), a fake AI that answers at once is
added as another device's AI server, and requests go through it: the time from the client sending to the fake AI seeing the request (before), and from the fake's last byte to the
client's last byte (after). Measured on the dev device: about 1.6 ms before and 0.8 ms after (median). The budget is ten times that so the test isn't flaky on a slow or busy device:
it exists to catch a mistake that makes a request wait for something slow (a database write, a file read, a program start on the request path), which costs tens or hundreds
of milliseconds, not to measure.

`MULTIPROXY_LOOP_LAG_MS` is set for the run, so anything that blocks the server for longer than the budget is also listed (loop_lag.py) when the test fails.
"""
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
MEDIAN_BUDGET_MS = 25.0          # before + after, one request at a time (measured: about 2.4)
BURST_AFTER_BUDGET_MS = 250.0    # the median "AI's last byte to the reply's last byte" with 30 requests at once (measured: under 60; a request waiting on a disk write was over 1000)
MODELS_BUDGET_MS = 150.0         # GET /v1/models (SkyrimNet's model check), median with a warm list (measured: about 2; it was about 1100)
MODELS_STALE_BUDGET_MS = 250.0   # the first call after the lists have gone stale: still answered from the last lists, the refresh runs behind it
START_WAIT_SECONDS = 120


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_scenarios(main_port: int, fake_port: int) -> dict:
    arrive: dict = {}
    last: dict = {}

    async def models(request):
        return web.json_response({"object": "list", "data": [{"id": "m", "object": "model"}]})

    async def chat(request):
        t_arrive = time.perf_counter()
        body = await request.json()
        rid = str(body["messages"][-1]["content"]).rsplit("#", 1)[-1]          # the request carries its own number in the prompt (headers are not passed on)
        arrive[rid] = t_arrive
        resp = {"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
        last[rid] = time.perf_counter()
        return web.json_response(resp)

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", fake_port).start()
    out: dict = {}
    try:
        base = f"http://127.0.0.1:{main_port}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), headers={"X-MultiProxy": "1"}) as s:
            async with s.post(base + "/config/other-pcs/servers/add-own", json={"address": f"127.0.0.1:{fake_port}", "nickname": "fastpc"}) as r:
                assert r.status == 200, await r.text()

            async def one(i: int) -> tuple:
                rid = f"b{i}-{time.perf_counter_ns()}"
                t0 = time.perf_counter()
                async with s.post(base + "/v1/chat/completions", json={"model": "fastpc/m", "messages": [{"role": "user", "content": f"Say hello. #{rid}"}], "max_tokens": 8}) as r:
                    await r.read()
                    t1 = time.perf_counter()
                    assert r.status == 200
                return arrive[rid] - t0, t1 - last[rid]

            for i in range(5):                                   # warm-up (not counted)
                await one(-i - 1)
            single = [await one(i) for i in range(60)]
            out["before_ms"] = [a * 1000 for a, _ in single]
            out["after_ms"] = [b * 1000 for _, b in single]
            burst = await asyncio.gather(*[one(1000 + i) for i in range(30)])
            out["burst_after_ms"] = [b * 1000 for _, b in burst]
            out["burst_before_ms"] = [a * 1000 for a, _ in burst]

            async def models_call() -> float:
                t0 = time.perf_counter()
                async with s.get(base + "/v1/models") as r:
                    await r.read()
                    assert r.status == 200
                return (time.perf_counter() - t0) * 1000

            out["models_first_ms"] = await models_call()           # (what the first call costs is not budgeted: it may wait for a source that has never answered)
            await asyncio.sleep(3)
            out["models_warm_ms"] = [await models_call() for _ in range(20)]
            await asyncio.sleep(6)                                 # (longer than the lists are kept fresh)
            out["models_stale_ms"] = await models_call()
    finally:
        await runner.cleanup()
    return out


def test_multiproxys_own_added_time_stays_small(tmp_path):
    main_port, icept_port, fake_port = free_port(), free_port(), free_port()
    settings, home = tmp_path / "settings", tmp_path / "home"
    settings.mkdir()
    home.mkdir()
    env = {**os.environ, "MULTIPROXY_SETTINGS_DIR": str(settings), "MULTIPROXY_PORT": str(main_port), "MULTIPROXY_INTERCEPTOR_PORT": str(icept_port), "MULTIPROXY_NO_INI_MIGRATION": "1",
           "MULTIPROXY_LOOP_LAG_MS": str(int(MEDIAN_BUDGET_MS)), "PYTHONUNBUFFERED": "1", "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
           "LOCALAPPDATA": str(home / "Local"), "XDG_CONFIG_HOME": str(home / "config")}
    logf = open(tmp_path / "multiproxy.out", "wb")
    proc = subprocess.Popen([sys.executable, "multiproxy.py"], cwd=str(ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + START_WAIT_SECONDS
        up = False
        while time.time() < deadline and proc.poll() is None:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{main_port}/health", timeout=2)
                up = True
                break
            except Exception:
                time.sleep(0.5)
        if not up:
            pytest.skip("a scratch MultiProxy didn't start in time on this device")
        res = asyncio.run(run_scenarios(main_port, fake_port))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        logf.close()
    stalls = [ln for ln in (tmp_path / "multiproxy.out").read_text(encoding="utf-8", errors="replace").splitlines() if "The server was busy for" in ln]
    total = [a + b for a, b in zip(res["before_ms"], res["after_ms"])]
    med = statistics.median(total)
    burst_med = statistics.median(res["burst_after_ms"])
    models_med = statistics.median(res["models_warm_ms"])
    detail = f"/v1/models: warm median {models_med:.1f} ms (budget {MODELS_BUDGET_MS}), first call after the lists went stale {res['models_stale_ms']:.1f} ms (budget {MODELS_STALE_BUDGET_MS}); median own time {med:.1f} ms (budget {MEDIAN_BUDGET_MS}); burst of 30: median wait after the AI answered {burst_med:.1f} ms (budget {BURST_AFTER_BUDGET_MS}); stalls seen: {stalls[:5]}"
    assert med < MEDIAN_BUDGET_MS, detail
    assert burst_med < BURST_AFTER_BUDGET_MS, detail
    assert models_med < MODELS_BUDGET_MS, detail
    assert res["models_stale_ms"] < MODELS_STALE_BUDGET_MS, detail
