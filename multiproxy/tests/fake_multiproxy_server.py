"""A stand-in for multiproxy.py, for the tray app's tests (it is copied into a temp program folder AS multiproxy.py).

It answers the three routes the tray app uses (`/local/tray-status`, `/local/shutdown`, `/config/settings`) with the same token rule, and has
knobs in environment variables:  FAKE_FAIL_PORT=1 (log "Port N is already in use ..." and exit 1), FAKE_FAIL_MODULE=1 (log a missing module and
exit 1), FAKE_EXIT_AFTER=<seconds>:<code> (exit with that code a bit later: 75 = "start me again"), FAKE_NEVER_ANSWER=1 (listen but never
answer, i.e. a server stuck while starting), FAKE_READY=<n>, FAKE_TOTAL=<n>.
"""
PROXY_VERSION = "9.9.9"

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from aiohttp import web

HERE = Path(__file__).resolve().parent
LOG = HERE / "multiproxy.log"
STARTS = HERE / "starts.txt"


def log(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def token() -> str:
    try:
        return json.load(open(os.environ["MULTIPROXY_INSTALL_INFO"], encoding="utf-8")).get("token", "")
    except (OSError, ValueError, KeyError):
        return ""


async def main():
    port = int(os.environ["MULTIPROXY_PORT"])
    with open(STARTS, "a", encoding="utf-8") as f:
        f.write(f"{os.getpid()} tray={os.environ.get('MULTIPROXY_TRAY')}\n")
    log(f"fake server starting on {port}")
    if os.environ.get("FAKE_FAIL_PORT"):
        log(f"MultiProxy can't start: port {port} is already in use.")
        sys.exit(1)
    if os.environ.get("FAKE_FAIL_MODULE"):
        log("ModuleNotFoundError: No module named 'aiohttp'")
        sys.exit(1)
    state = {"autoClose": False, "closedSeq": int(os.environ.get("FAKE_CLOSED_SEQ", "0"))}

    def authed(request):
        return request.headers.get("Authorization") == f"Bearer {token()}"

    async def status(request):
        if os.environ.get("FAKE_NEVER_ANSWER"):
            await asyncio.sleep(3600)
        if not authed(request):
            return web.json_response({"detail": "Wrong token."}, status=401)
        return web.json_response({"boot": str(os.getpid()), "version": "9.9.9", "port": port, "autoClose": state["autoClose"], "modDetected": os.environ.get("FAKE_MOD", "1") == "1",
                                  "update": {"state": os.environ.get("FAKE_UPDATE", "current"), "url": "https://example.test/release"},
                                  "readyCount": int(os.environ.get("FAKE_READY", "3")), "totalAIs": int(os.environ.get("FAKE_TOTAL", "14")),
                                  "skyrim": {"running": False, "folder": "", "modVersion": "", "startedSeq": 0, "closedSeq": state["closedSeq"]}})

    async def shutdown(request):
        if not authed(request):
            return web.json_response({"detail": "Wrong token."}, status=401)
        log("shutdown asked for")
        asyncio.get_event_loop().call_later(0.2, lambda: os._exit(0))
        return web.json_response({"ok": True})

    async def settings(request):
        body = await request.json()
        if "startStopWithSkyrim" in body:
            state["autoClose"] = bool(body["startStopWithSkyrim"])
        return web.json_response({"ok": True})

    async def bump(request):                                  # test helper: pretend the Skyrim mod said "closed"
        state["closedSeq"] += 1
        return web.json_response({"closedSeq": state["closedSeq"]})

    app = web.Application()
    app.router.add_get("/local/tray-status", status)
    app.router.add_post("/local/shutdown", shutdown)
    app.router.add_post("/config/settings", settings)
    app.router.add_post("/test/closed", bump)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    if os.environ.get("FAKE_EXIT_AFTER"):
        secs, code = os.environ["FAKE_EXIT_AFTER"].split(":")
        await asyncio.sleep(float(secs))
        os._exit(int(code))
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
