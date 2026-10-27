"""Copilot on another device, the main device's side (2026-10-06): the small set of dashboard routes the program row calls.

Everything real lives in `copilot_courier.py` (on the Courier device); this file only passes the player's choices along through the paired connection
(`other_pcs.RemoteServers.courier_copilot`). Nothing secret passes through: the sign-in itself never leaves the other device, and the one-time sign-in code the
player types at GitHub comes back in the device's report.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ACTIONS = ("install", "update", "cancel", "signin", "cancelsignin", "signout", "test", "uninstall")


def setup(app: FastAPI, remotes) -> None:
    def pc_error(e: Exception):
        status = getattr(e, "status", 500)
        return JSONResponse({"detail": getattr(e, "message", str(e))}, status_code=status if isinstance(status, int) and 400 <= status < 600 else 500)

    @app.post("/config/copilot/pc/{action}")
    async def copilot_pc_action(action: str, request: Request):
        """install | update | cancel | signin | cancelsignin | signout | test | uninstall for Copilot on that device: {"id": "<PC>"}."""
        if action not in ACTIONS:
            return JSONResponse({"detail": "That isn't something Copilot can do on another device."}, status_code=404)
        data = await request.json()
        try:
            return await remotes.courier_copilot(str(data.get("id", "")), action)
        except Exception as e:
            return pc_error(e)
