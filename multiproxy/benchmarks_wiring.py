"""Benchmarks: the routes (`/config/benchmarks/...`) and the production wiring of the runner.

The routes sit behind the same guard as every other dashboard route (`local_guard`: exact Host, own Origin, the `X-MultiProxy` header on a change, JSON bodies only, no CORS). A GET that
carries a body is refused. A model in a plan is a path to an existing `.gguf` file and nothing else; the routes only ever read and write inside the benchmark folder.

  POST   /config/benchmarks/start                 plan -> {"ok": true, "batch_id"} | {"ok": false, "message", "needs_takeover"?}   (`takeover: true` agrees to unload the models MultiProxy loaded;
                                                  `background`: "stop" (default) | "keep" | "start_normal", see benchmarks_background.py; `pair_of`: the run id this one repeats)
  GET    /config/benchmarks/status                current step, model k of n, live readings, the run id, the background (`background_now` when idle)
  POST   /config/benchmarks/cancel
  GET    /config/benchmarks/runs                  every saved run, newest first
  GET    /config/benchmarks/runs/<id>             manifest + summary (+ progress)
  GET    /config/benchmarks/runs/<id>/pairs       the runs that are the same test apart from the background
  GET    /config/benchmarks/runs/<id>/export?format=csv|json
  DELETE /config/benchmarks/runs/<id>
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import benchmarks_background as bgm
import benchmarks_cardusage as bcu
import benchmarks_contract as bc
import benchmarks_engine
import benchmarks_runner as br
import benchmarks_store as bs

BAD_BODY = "MultiProxy couldn't read the data sent to it."
NO_BODY_ALLOWED = "That request doesn't take a body."
NO_SUCH_RUN = "There is no benchmark run with that ID."
RUN_IN_USE = "That run is still going. Cancel it first."
BAD_FORMAT = "The export format must be CSV or JSON."


class Handles:
    def __init__(self, service: br.BenchmarkService, base: Path, pid_dir: Path, log: Callable[[str], None]):
        self.service, self.base, self.pid_dir, self.log = service, base, pid_dir, log
        self.remote = None                           # the page logic for a test on another PC, when the Other devices list was given

    def recover_on_start(self) -> dict:
        """At program start: stop the stale benchmark engine a previous run kept the process id of (only that one), and mark runs that were going as interrupted."""
        killed = benchmarks_engine.stop_leftover_engine(self.pid_dir, self.log)
        return {"engine_stopped": killed, "runs_marked": br.mark_unfinished_interrupted(self.base)}

    async def shutdown(self) -> None:
        """The program is closing: the run ends `interrupted` (`program_stopped`) and its engine is stopped."""
        await self.service.shutdown()


from benchmarks_hooks import DECISION_ID, SERVICE_READY_WAIT_S, VOICE_ID, _driver_cache, _driver_version, _engine_entry, production_hooks  # noqa: F401,E402  (re-exported: the Courier uses them without this module)


def setup(app, local_svc, load_cfg, skyrim_state, outside_client: dict, settings_dir: str, version: str, log: Callable[[str], None] = lambda m: None,
          service: Optional[br.BenchmarkService] = None, wall: Callable[[], float] = time.time, tab_options: Optional[dict] = None, voice_engine=None, decision_engine=None,
          remotes=None, remote_options: Optional[dict] = None) -> Handles:
    from fastapi import Request
    from fastapi.responses import JSONResponse, Response

    base = bs.benchmark_dir(settings_dir)
    pid_dir = base
    if service is None:
        import gpu_choice
        launcher = benchmarks_engine.LlamaLauncher(local_svc, pid_dir, cards_fn=gpu_choice.cached_cards)
        hooks = production_hooks(local_svc, load_cfg, skyrim_state, outside_client, version, voice_engine, decision_engine, gpu_choice.cached_cards)
        hooks.owned_alive = launcher.owned_alive
        service = br.BenchmarkService(base, launcher, hooks, log=log)
    handles = Handles(service, base, pid_dir, log)
    import benchmarks_tab
    benchmarks_tab.setup(app, base, local_svc, load_cfg, service, wall, has_game=lambda: bool(getattr(local_svc, "HAS_GAME", True)), game_running=lambda: bool(getattr(skyrim_state, "running", False)), **(tab_options or {}))
    if remotes is not None:                       # a test on another PC: the "Test on" list is the Other devices list (/config/benchmarks/remote/*)
        import benchmarks_remote_page
        handles.remote = benchmarks_remote_page.setup(app, base, lambda: remotes.all(), app.state.benchmarks_rows, **(remote_options or {}))

    def err(status: int, message: str, **extra):
        return JSONResponse({"detail": message, **extra}, status_code=status)

    def has_body(request: Request) -> bool:
        h = request.headers
        try:
            return int(h.get("content-length") or 0) > 0 or "transfer-encoding" in h
        except ValueError:
            return True

    async def json_body(request: Request):
        try:
            data = await request.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @app.post("/config/benchmarks/start")
    async def benchmarks_start(request: Request):
        body = await json_body(request)
        if body is None:
            return err(400, BAD_BODY)
        takeover = body.pop("takeover", False) is True
        result = await service.start(body, takeover=takeover)
        if result.get("ok"):
            return result
        extra = {k: v for k, v in result.items() if k in ("needs_takeover",)}
        return JSONResponse({"ok": False, "detail": result.get("message", ""), **extra}, status_code=int(result.get("status", 409)))

    @app.get("/config/benchmarks/status")
    async def benchmarks_status(request: Request):
        if has_body(request):
            return err(400, NO_BODY_ALLOWED)
        return service.status()

    @app.post("/config/benchmarks/cancel")
    async def benchmarks_cancel(request: Request):
        return {"ok": True, "cancelled": await service.cancel()}

    @app.get("/config/benchmarks/runs")
    async def benchmarks_runs(request: Request):
        if has_body(request):
            return err(400, NO_BODY_ALLOWED)
        return {"runs": await asyncio.to_thread(bs.list_runs, base, wall())}

    def valid(run_id: str) -> bool:
        return bc.valid_run_id(run_id) and (base / run_id).is_dir()

    @app.get("/config/benchmarks/runs/{run_id}")
    async def benchmarks_run(run_id: str, request: Request):
        if has_body(request):
            return err(400, NO_BODY_ALLOWED)
        if not valid(run_id):
            return err(404, NO_SUCH_RUN)
        try:
            run = await asyncio.to_thread(bs.load_run, base, run_id, wall())
        except bs.NewerSchemaError as e:
            return JSONResponse({"detail": bs.NEWER_VERSION_LINE, "status": "newer_version", "schema_version": e.found}, status_code=200)
        except ValueError:
            return err(404, NO_SUCH_RUN)
        return {"manifest": run["manifest"], "summary": run["summary"], "progress": run["progress"], "request_count": len(run["requests"])}

    @app.get("/config/benchmarks/runs/{run_id}/pairs")
    async def benchmarks_pairs(run_id: str, request: Request):
        if has_body(request):
            return err(400, NO_BODY_ALLOWED)
        if not valid(run_id):
            return err(404, NO_SUCH_RUN)
        return {"pairs": await asyncio.to_thread(bs.pairs_of, base, run_id, wall())}

    @app.get("/config/benchmarks/runs/{run_id}/export")
    async def benchmarks_export(run_id: str, request: Request, format: str = "json"):
        if has_body(request):
            return err(400, NO_BODY_ALLOWED)
        if format not in ("csv", "json"):
            return err(400, BAD_FORMAT)
        if not valid(run_id):
            return err(404, NO_SUCH_RUN)
        try:
            if format == "csv":
                return Response(await asyncio.to_thread(bs.requests_csv, base, run_id), media_type="text/csv; charset=utf-8")
            return Response(json.dumps(bcu.strip_names(await asyncio.to_thread(bs.full_export, base, run_id, wall()))), media_type="application/json")
        except (bs.NewerSchemaError, ValueError):
            return err(404, NO_SUCH_RUN)

    @app.delete("/config/benchmarks/runs/{run_id}")
    async def benchmarks_delete(run_id: str, request: Request):
        if not valid(run_id):
            return err(404, NO_SUCH_RUN)
        if service.running() and service.status().get("run_id") == run_id:
            return err(409, RUN_IN_USE)
        try:
            await asyncio.to_thread(bs.delete_run, base, run_id)
        except ValueError:
            return err(404, NO_SUCH_RUN)
        return {"ok": True}

    return handles
