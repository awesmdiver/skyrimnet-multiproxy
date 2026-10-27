"""Claude on another device, the main device's side (2026-10-05): the small set of dashboard routes the program row calls.

Everything real lives in `claude_courier.py` (on the Courier device); this file only passes the player's choices along through the paired connection
(`other_pcs.RemoteServers.courier_claude`). The sign-in code is the one secret that passes through: it is sent on at once and is never kept, logged or
returned here, and a failed request's body is never written to the log (`multiproxy._BODY_HIDDEN_PATHS` lists `SIGN_IN_PATH`).
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SIGN_IN_PATH = "/config/claude/pc/signin"
SIGN_IN_CODE_PATH = "/config/claude/pc/signin-code"        # the one-time code from the sign-in page: sent on at once, never kept or logged
ACTIONS = ("install", "update", "cancel", "signin", "signout", "test", "uninstall", "signin-start", "signin-code", "signin-cancel")


def setup(app: FastAPI, remotes) -> None:
    def pc_error(e: Exception):
        status = getattr(e, "status", 500)
        return JSONResponse({"detail": getattr(e, "message", str(e))}, status_code=status if isinstance(status, int) and 400 <= status < 600 else 500)

    @app.post("/config/claude/pc/{action}")
    async def claude_pc_action(action: str, request: Request):
        """install | update | cancel | signin | signout | test | uninstall for Claude on that device: {"id": "<PC>"} (signin also {"code": "<the code>"}). signin-start asks that device to run
        the sign-in itself and answers with the sign-in link in its status; signin-code {"code"} passes the one-time code from the sign-in page; signin-cancel stops it."""
        if action not in ACTIONS:
            return JSONResponse({"detail": "That isn't something Claude can do on another device."}, status_code=404)
        data = await request.json()
        body = {"code": str(data.get("code") or "")} if action in ("signin", "signin-code") else {}
        try:
            return await remotes.courier_claude(str(data.get("id", "")), action, body)
        except Exception as e:
            return pc_error(e)
