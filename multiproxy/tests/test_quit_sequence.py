"""Quitting MultiProxy (4.2.1): the ports are let go first, every engine is stopped at the same time, each step is timed, and the log has exactly one end line.

Stand-ins only: no real engine, no real MultiProxy, never port 8000 or the real folders.
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import quit_sequence as qs

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def test_every_engine_is_stopped_at_the_same_time_not_one_after_another():
    async def slow():
        await asyncio.sleep(0.5)
    t = time.monotonic()
    steps = run(qs.run([], [(f"engine {i}", slow) for i in range(4)], log=lambda m: None))
    took = time.monotonic() - t
    assert took < 1.2, took                    # one after another would be 2 s
    assert [n for n, _ in steps] == ["engine 0", "engine 1", "engine 2", "engine 3"] or len(steps) == 4


def test_the_ports_are_let_go_before_any_engine_starts_stopping():
    order = []

    async def port():
        order.append("port")

    async def eng():
        order.append("engine")
        await asyncio.sleep(0.05)
    run(qs.run([("dashboard port", port)], [("a", eng), ("b", eng)], log=lambda m: None))
    assert order == ["port", "engine", "engine"]


def test_the_port_is_free_while_a_slow_engine_is_still_stopping():
    """The point of the change: the dashboard port is released at once, not after the engines."""
    import socket
    ln = socket.socket()
    ln.bind(("127.0.0.1", 0))
    ln.listen(1)
    port = ln.getsockname()[1]
    seen = {}

    async def release():
        ln.close()

    async def engine():
        await asyncio.sleep(1.5)
        seen["engine_done"] = time.monotonic()

    async def go():
        t = time.monotonic()
        task = asyncio.ensure_future(qs.run([("dashboard port", release)], [("slow engine", engine)], log=lambda m: None))
        await asyncio.sleep(0.2)
        probe = socket.socket()
        probe.settimeout(0.5)
        try:
            refused = probe.connect_ex(("127.0.0.1", port)) != 0
        finally:
            probe.close()
        engine_still_stopping = "engine_done" not in seen          # (the engine takes 1.5 s; the refused connection was seen well before it ended)
        await task
        return refused, engine_still_stopping, "engine_done" in seen
    refused, engine_still_stopping, engine_done_after = run(go())
    assert refused and engine_still_stopping and engine_done_after


def test_an_engine_that_ignores_the_stop_is_given_up_on_after_its_limit_and_the_others_do_not_wait_for_it(  ):
    logged = []

    async def stuck():
        await asyncio.sleep(30)

    async def quick():
        await asyncio.sleep(0.05)
    t = time.monotonic()
    steps = dict(run(qs.run([], [("stuck engine", stuck), ("quick engine", quick)], log=logged.append, engine_limit=0.4)))
    assert time.monotonic() - t < 2
    assert steps["quick engine"] < 0.3 and 0.35 <= steps["stuck engine"] < 1.5
    assert any("stuck engine had not finished after" in m and "moving on" in m for m in logged)


def test_an_engine_that_fails_does_not_stop_the_quit():
    async def boom():
        raise RuntimeError("x")
    logged = []
    steps = run(qs.run([("p", boom)], [("e", boom)], log=logged.append))
    assert len(steps) == 2 and len(logged) == 2


def test_a_request_that_arrives_while_closing_gets_a_short_closing_answer():
    sent = []

    async def app(scope, receive, send):
        sent.append("app")
    state = {"closing": False}
    g = qs.ClosingGuard(app, lambda: state["closing"])

    async def send(m):
        sent.append(m)
    run(g({"type": "http"}, None, send))
    assert sent == ["app"]
    sent.clear()
    state["closing"] = True
    run(g({"type": "http"}, None, send))
    assert sent[0]["status"] == 503 and (b"connection", b"close") in sent[0]["headers"] and json.loads(sent[1]["body"]) == {"detail": "MultiProxy is closing."}
    sent.clear()
    run(g({"type": "lifespan"}, None, send))      # (not a request: passed through)
    assert sent == ["app"]


def test_the_end_line_has_the_seconds_the_port_time_and_the_slow_steps(proxy_module):
    f = proxy_module._shutdown_end_line
    assert f(0.4, [("local models", 0.1)], 0, 0.1) == "Closing finished in 0.4s, port let go after 0.1s (nothing slow)"
    line = f(14.26, [("dashboard port", 0.02), ("local models", 12.04), ("decision engine", 1.5)], 2, 0.02)
    assert line == "Closing finished in 14.3s, port let go after 0.0s (slow: local models 12.0s, decision engine 1.5s); 2 requests still open when it was told to stop"
    assert f(1, [], 1).endswith("1 request still open when it was told to stop")


def test_the_quit_logs_exactly_one_end_line(proxy_module):
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
    assert src.count("_shutdown_end_line(") == 2          # its definition and the single call in _tray_shutdown
    assert "quit_sequence.run(release, engines" in src and "_finish_process()" in src


def test_the_wait_at_start_is_gone_and_a_busy_port_is_todays_flow(proxy_module):
    for gone in ("_wait_for_last_multiproxy", "PORT_WAIT_WORDS", "PORT_WAIT_LIMIT_SECONDS"):
        assert not hasattr(proxy_module, gone), gone
    from unittest.mock import patch
    with patch.object(proxy_module, "_port_busy", return_value=True), patch.object(proxy_module, "_resolve_port_conflict") as resolve:
        proxy_module._preflight_main_port()
    resolve.assert_called_once_with(proxy_module.MAIN_PORT)


def test_uvicorn_is_given_a_graceful_shutdown_limit_and_the_server_object_is_kept():
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
    assert "timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS" in src and "_UVICORN_SERVER = uvicorn.Server(" in src
    assert 1 <= __import__("multiproxy").GRACEFUL_SHUTDOWN_SECONDS <= 10


# ---- a real uvicorn: an open keep-alive connection does not hold the port beyond the graceful limit -----------------------------------
_SERVER = r"""
import asyncio, sys, uvicorn
from fastapi import FastAPI
app = FastAPI()
@app.get("/ping")
async def ping(): return {"ok": True}
cfg = uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning", timeout_graceful_shutdown=1)
srv = uvicorn.Server(cfg)
async def main():
    task = asyncio.ensure_future(srv.serve())
    while not srv.started: await asyncio.sleep(0.05)
    print("up", flush=True)
    await asyncio.sleep(1.0)
    for s in srv.servers: s.close()
    srv.should_exit = True
    await task
asyncio.run(main())
print("done", flush=True)
"""


def test_an_open_connection_does_not_hold_the_server_beyond_the_graceful_limit(tmp_path):
    import http.client
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    f = tmp_path / "srv.py"
    f.write_text(_SERVER, encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(f), str(port)], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "up"
        held = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        held.request("GET", "/ping")
        held.getresponse().read()                      # keep-alive: left open on purpose
        t = time.monotonic()
        p.wait(timeout=15)
        assert time.monotonic() - t < 6
    finally:
        if p.poll() is None:
            p.kill()


# ---- the tray app's side: a quit is done only when the server and its engines are really gone ---------------------------------------
def test_wait_until_gone_returns_when_the_last_program_has_gone_and_gives_up_after_its_limit():
    import multiproxy_tray as mt
    t = time.monotonic()
    ticks = {"n": 0}

    def look(pid):
        ticks["n"] += 1
        return ("alive", None) if ticks["n"] < 6 else ("gone", None)
    assert mt.wait_until_gone([11, 22], 5, look, pause=0.05) is True and time.monotonic() - t < 3
    assert mt.wait_until_gone([11], 0.3, lambda pid: ("alive", None), pause=0.05) is False       # one that never goes: it gives up, it does not hang
    assert mt.wait_until_gone([11], 1, lambda pid: ("unknown", None), pause=0.05) is True          # a look that cannot tell is not "still there"


def test_the_tray_does_not_report_the_end_done_while_a_child_is_still_alive(tmp_path):
    import multiproxy_tray as mt
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    host = mt.ServerHost(tmp_path)
    host.proc = parent
    start = time.monotonic()
    flips = {"n": 0}

    def look(pid):
        if pid == parent.pid and parent.poll() is None:
            return ("alive", None)
        flips["n"] += 1
        return ("alive", None) if time.monotonic() - start < 1.5 else ("gone", None)          # a child engine that takes 1.5 s to really go
    try:
        host.kill(tree_wait=10, look=look)
        took = time.monotonic() - start
    finally:
        if parent.poll() is None:
            parent.kill()
    assert parent.poll() is not None and 1.4 <= took < 8 and host.proc is None


@pytest.mark.skipif(sys.platform != "win32", reason="the tree end is taskkill (Windows)")
def test_a_real_child_of_the_server_is_gone_when_kill_returns(tmp_path):
    import multiproxy_tray as mt
    import process_watch
    code = "import subprocess,sys,time; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(c.pid, flush=True); time.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    host = mt.ServerHost(tmp_path)
    host.proc = parent
    try:
        host.kill(tree_wait=15)
        assert parent.poll() is not None and process_watch.look(child)[0] != "alive"
    finally:
        for p in (parent,):
            if p.poll() is None:
                p.kill()
        if process_watch.look(child)[0] == "alive":
            import hidden_process
            hidden_process.run(["taskkill", "/F", "/PID", str(child)], capture_output=True)
