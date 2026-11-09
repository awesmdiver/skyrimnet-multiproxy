"""Benchmarks: the Chat test runner on this PC (contract sections 1 to 9, first round).

`BenchmarkService` runs a plan: for each model, one at a time, one run folder (see `benchmarks_store`) with the steps load, warmup, one level per ticked concurrency, sustained, unload and
cooldown. It starts the engine through a launcher (`benchmarks_engine.LlamaLauncher` for real, a fake one in tests), sends its requests STRAIGHT to that engine (never through the proxy
SkyrimNet uses, so nothing here is game traffic and nothing reaches the Speeds history), keeps one `ReadingsSampler` going for the whole run, and writes every request and every reading as it
happens. It never scores or ranks.

Everything outside this module that the run needs to ask (is Skyrim running, was SkyrimNet just here, which models MultiProxy loaded for the game, how big is the card) comes in through
`Hooks`, so the whole run can be tested without a game or a card. Times come from `clock` (monotonic) and `wall` (epoch seconds); `sleep` is replaceable.
"""
import asyncio
import json
import os
import platform
import random
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp

import benchmarks_background as bg
import benchmarks_cardusage as bcu
import benchmarks_checks as bchk
import benchmarks_contract as bc
import benchmarks_guard as bguard
import benchmarks_sampler as bsm
import benchmarks_store as bs
import benchmarks_testset as tset

DEFAULT_LEVELS = (1, 4, 8)
DEFAULT_REQUESTS_PER_LEVEL = 25
MAX_REQUESTS_PER_LEVEL = 100
WARMUP_REQUESTS = 2
DEFAULT_REQUEST_TIMEOUT_S = 300.0
DEFAULT_SUSTAINED_S = 300.0
DEFAULT_COOLDOWN_C = 55.0
DEFAULT_COOLDOWN_MAX_S = 300.0
MAX_TOKENS = 128
TEMPERATURE = 0.0
HEARTBEAT_EVERY_S = 30.0
MIN_FREE_DISK_MB = 200
RECENT_GAME_REQUEST_S = 120.0                             # contract section 8: no SkyrimNet request in the last 2 minutes
ANSWER_KEEP_CHARS = 4000
ATTEMPTS_PER_REQUEST = 3                                  # a level gives up after this many tries per wanted request (and 10 more): the engine is not answering
ALLOWED_OVERRIDES = ("layers", "flashAttn", "cacheType", "ctx", "batch", "threads", "gpu")           # `gpu`: a card id from the Settings graphics-card choice (one card)
PROMPT_CLASSES = ("short", "medium", "long")

# Said to the person (the screen shows them as they are; the design side runs them through a Gemini pass).
REFUSE_RECENT = "SkyrimNet asked MultiProxy for something in the last 2 minutes. Wait until the game is quiet, then try again."
REFUSE_GAME = "Skyrim is running but you chose \"Game closed\". Close Skyrim or pick another option."
REFUSE_BUSY = "A benchmark is already running. Wait for it to finish, or cancel it."
REFUSE_NO_ENGINE = "Benchmarks need the local engine (llama.cpp). Install it from the Local device tab."
REFUSE_MODELS_OFF = "Local models are turned off on this device, and Benchmarks need them."
ENGINE_INSTALLED, ENGINE_NOT_INSTALLED, ENGINE_MODELS_OFF = "installed", "not_installed", "local_models_off"
REFUSE_MISSING = "A model file is missing: {name}."
REFUSE_FIT = "{name} is too big to load on your graphics card."
REFUSE_DISK = "There isn't enough free space to save the results."
REFUSE_TAKEOVER = "A game model is using the graphics card. Stop it to run the benchmark?"
BATCH_STOP_MEMORY = "The PC's memory did not free up after the last model. The rest of the tests were cancelled."
BATCH_STOP_CLEANUP = "The last model's engine did not stop fully. The rest of the tests were cancelled."
PLAN_NO_MODELS = "Pick at least one model."
PLAN_NOT_GGUF = "A benchmark can only use a .gguf model file that exists on this device."
PLAN_BAD = "That benchmark plan isn't valid: {why}."
REFUSE_BACKGROUND_FIT = bg.REFUSE_BACKGROUND_FIT
REFUSE_BACKGROUND_START = bg.REFUSE_BACKGROUND_START


class PlanError(ValueError):
    pass


class EngineStartError(Exception):
    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.message, self.log_tail = message, log_tail


class EngineDied(Exception):
    def __init__(self, tail: str):
        super().__init__("the engine stopped")
        self.tail = tail


class LevelFailed(Exception):
    pass


# --- the plan ---------------------------------------------------------------------------------------------------------------------------------------------------------------

@dataclass
class Plan:
    models: list
    kind: str = "chat"
    levels: tuple = DEFAULT_LEVELS
    requests_per_level: int = DEFAULT_REQUESTS_PER_LEVEL
    overrides: dict = field(default_factory=dict)
    skyrim: str = "history"                               # history | closed | typed
    skyrim_gb: Optional[float] = None
    sustained: bool = True
    sustained_s: float = DEFAULT_SUSTAINED_S
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    cooldown: bool = True
    cooldown_c: float = DEFAULT_COOLDOWN_C
    cooldown_max_s: float = DEFAULT_COOLDOWN_MAX_S
    ceiling_c: Optional[float] = None
    warmup: int = WARMUP_REQUESTS
    prompts: tuple = PROMPT_CLASSES                       # which prompt lengths the levels use
    temperature: float = TEMPERATURE
    heat_stop: bool = True                                # stop the run when a tested card reaches its own heat limit
    memory_guard: bool = True                             # stop a model's test when the PC runs short of memory (the emergency floor is on whatever this says)
    background: str = bg.BACKGROUND_STOP                  # "While testing": stop | keep | start_normal (contract 12 and 12b)
    keep_started: bool = False                            # start_normal: leave the services this run started running when it ends
    pair_of: Optional[str] = None                         # the run id this run repeats (same test, other background)

    @property
    def profile(self) -> str:
        """`standard` only when nothing differs from the defaults; any change is `custom` and never presented as equal to a standard run."""
        std = (tuple(self.levels) == DEFAULT_LEVELS and self.requests_per_level == DEFAULT_REQUESTS_PER_LEVEL and not self.overrides and self.sustained
               and self.sustained_s == DEFAULT_SUSTAINED_S and self.request_timeout_s == DEFAULT_REQUEST_TIMEOUT_S and self.warmup == WARMUP_REQUESTS
               and tuple(self.prompts) == PROMPT_CLASSES and self.temperature == TEMPERATURE)
        return "standard" if std else "custom"

    def requested(self) -> dict:
        return {"levels": list(self.levels), "requests_per_level": self.requests_per_level, "overrides": dict(self.overrides), "sustained": self.sustained,
                "sustained_s": self.sustained_s, "request_timeout_s": self.request_timeout_s, "warmup_requests": self.warmup, "max_tokens": MAX_TOKENS,
                "temperature": self.temperature, "prompts": list(self.prompts), "heat_stop": self.heat_stop, "memory_guard": self.memory_guard, "skyrim": self.skyrim, "skyrim_gb": self.skyrim_gb, "cooldown": self.cooldown, "cooldown_c": self.cooldown_c, "cooldown_max_s": self.cooldown_max_s, "ceiling_c": self.ceiling_c}


def _num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise PlanError(PLAN_BAD.format(why=f"{what} must be a number from {lo} to {hi}"))
    return v


def parse_plan(body, file_ok: Callable[[str], bool] = lambda p: os.path.isfile(p)) -> Plan:
    """A plan from the request body. Raises `PlanError` with a plain line. A model is a path to an existing `.gguf` file, nothing else."""
    if not isinstance(body, dict):
        raise PlanError(PLAN_BAD.format(why="the plan must be a JSON object"))
    if body.get("kind", "chat") != "chat":
        raise PlanError(PLAN_BAD.format(why="only Chat tests exist so far"))
    models = body.get("models")
    if not isinstance(models, list) or not models:
        raise PlanError(PLAN_NO_MODELS)
    paths = []
    for m in models:
        if not isinstance(m, str) or not m.strip() or "\x00" in m or not m.lower().endswith(".gguf") or not file_ok(m):
            raise PlanError(PLAN_NOT_GGUF)
        paths.append(os.path.abspath(m))
    levels = body.get("levels", list(DEFAULT_LEVELS))
    if not isinstance(levels, list) or not levels or not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 32 for n in levels):
        raise PlanError(PLAN_BAD.format(why="levels must be whole numbers from 1 to 32"))
    over = body.get("settings", {}) or {}
    if not isinstance(over, dict) or set(over) - set(ALLOWED_OVERRIDES):
        raise PlanError(PLAN_BAD.format(why="unknown setting"))
    import local_engine_flags as lef
    if "cacheType" in over and over["cacheType"] not in lef.SIMPLE_CACHE_TYPES:
        raise PlanError(PLAN_BAD.format(why="unknown cache type"))
    if "flashAttn" in over and over["flashAttn"] not in ("auto", "on", "off"):
        raise PlanError(PLAN_BAD.format(why="unknown flash attention value"))
    if "layers" in over and not (over["layers"] in lef.LAYER_WORDS or (isinstance(over["layers"], int) and not isinstance(over["layers"], bool) and 0 <= over["layers"] <= 999)):
        raise PlanError(PLAN_BAD.format(why="unknown layers value"))
    if "ctx" in over:
        _num(over["ctx"], 512, 4 * 1024 * 1024, "ctx")
    if "batch" in over:
        _num(over["batch"], 32, 65536, "the batch size")
    if "threads" in over:
        _num(over["threads"], 1, 256, "the processor threads")
    if "gpu" in over and (not isinstance(over["gpu"], str) or not over["gpu"] or len(over["gpu"]) > 300):
        raise PlanError(PLAN_BAD.format(why="unknown graphics card"))
    prompts = body.get("prompts", list(PROMPT_CLASSES))
    if not isinstance(prompts, list) or not prompts or not set(prompts) <= set(PROMPT_CLASSES):
        raise PlanError(PLAN_BAD.format(why="prompts must be short, medium or long"))
    temperature = float(_num(body.get("temperature", TEMPERATURE), 0, 2, "the temperature"))
    skyrim, gb = "history", None
    choice = str(body.get("skyrim", "history"))
    if choice in ("history", "closed"):
        skyrim = choice
    elif choice.startswith(("typed:", "available:")):
        try:
            gb = float(choice.split(":", 1)[1])
        except ValueError:
            raise PlanError(PLAN_BAD.format(why="the typed figure isn't a number"))
        _num(gb, 0, 1024, "the typed figure")
        skyrim = choice.split(":", 1)[0]                  # `typed`: Skyrim's own peak; `available`: the VRAM left for models (the screen's "I'll type a number")
    else:
        raise PlanError(PLAN_BAD.format(why="skyrim must be history, closed or typed:<gb>"))
    choice = bg.normalize_choice(body.get("background", bg.BACKGROUND_STOP))
    if not choice:
        raise PlanError(PLAN_BAD.format(why="background must be stop, keep or start_normal"))
    pair_of = body.get("pair_of")
    if pair_of is not None and (not isinstance(pair_of, str) or not bc.valid_run_id(pair_of)):
        raise PlanError(PLAN_BAD.format(why="pair_of must be a run id"))
    ceiling = body.get("ceiling_c")
    return Plan(models=paths, levels=tuple(sorted(set(levels))), requests_per_level=int(_num(body.get("requests_per_level", DEFAULT_REQUESTS_PER_LEVEL), 1, MAX_REQUESTS_PER_LEVEL, "requests per level")),
                overrides=dict(over), skyrim=skyrim, skyrim_gb=gb, sustained=bool(body.get("sustained", True)),
                sustained_s=float(_num(body.get("sustained_s", DEFAULT_SUSTAINED_S), 0.1, 1800, "the sustained time")),
                request_timeout_s=float(_num(body.get("request_timeout_s", DEFAULT_REQUEST_TIMEOUT_S), 0.1, 3600, "the request time-out")),
                prompts=tuple(c for c in PROMPT_CLASSES if c in prompts), temperature=temperature, heat_stop=body.get("heat_stop", True) is not False, memory_guard=body.get("memory_guard", True) is not False, background=choice, keep_started=body.get("keep_started", False) is True, pair_of=pair_of,
                warmup=int(_num(body.get("warmup", WARMUP_REQUESTS), 0, 5, "the warm-up count")), cooldown=bool(body.get("cooldown", True)), cooldown_c=float(_num(body.get("cooldown_c", DEFAULT_COOLDOWN_C), 30, 90, "the cool-down temperature")),
                cooldown_max_s=float(_num(body.get("cooldown_max_s", DEFAULT_COOLDOWN_MAX_S), 0, 900, "the cool-down wait")),
                ceiling_c=None if ceiling is None else float(_num(ceiling, 50, 120, "the heat ceiling")))


# --- what the service asks of the outside -----------------------------------------------------------------------------------------------------------------------------------

async def _no_unload(ids: list) -> None:
    return None


async def _no_start(service_id: str) -> Optional[str]:
    return None


async def _no_model_done(path: str, result: dict) -> None:
    return None


@dataclass
class Hooks:
    game_running: Callable[[], bool] = lambda: False
    last_game_request_at: Callable[[], Optional[float]] = lambda: None          # epoch seconds of the latest SkyrimNet request, or None
    loaded_by_multiproxy: Callable[[], list] = lambda: []                       # [{"id", "name"}] models MultiProxy loaded that hold the card now
    unload: Callable[[list], Awaitable[None]] = _no_unload                      # unload exactly those ids, the way the Local device tab's Stop does
    fit_check: Callable[[str], Optional[str]] = lambda path: None               # "too_big" when the model cannot load at all, else None
    fit_next_to: Callable[[str, float], Optional[str]] = lambda path, background_mb: None   # "too_big" when the model cannot load next to a background holding that much on one card
    background_loaded: Callable[[], list] = lambda: []                          # everything loaded or running on the test device now (benchmarks_background entries), MultiProxy's or not
    background_assigned: Callable[[], list] = lambda: []                        # services MultiProxy has set to run on the test device that are stopped ("when running" sizes)
    start_service: Callable[[str], Awaitable] = _no_start                       # start one of those with the existing start function and its guard: None when started, else a plain reason
    engine_state: Callable[[], str] = lambda: ENGINE_INSTALLED                  # `engine_state_of` answer; checked before anything is created
    memory_now: Optional[Callable[[], dict]] = None                             # the computer's memory right now (`available_mb`, `total_mb`, `used_mb`, `commit_*`, `vram_*`): the gate between models and the before / after record. None: no gate
    engine_working_set_mb: Optional[Callable[[int], Optional[float]]] = None   # the system RAM one engine process holds (the spill rule)
    owned_alive: Optional[Callable[[], bool]] = None                           # is anything the last engine started still running?
    runs_game: Callable[[], bool] = lambda: True                               # whether the test device runs Skyrim (False: no Skyrim term, `skyrim_state` is not_applicable)
    skyrim_peak_mb: Callable[[], Optional[float]] = lambda: None
    usual_skyrim_mb: Callable[[], Optional[float]] = lambda: None
    hardware: Callable[[], dict] = lambda: {}
    free_disk_mb: Callable[[Path], float] = lambda p: shutil.disk_usage(p).free / (1024 * 1024)
    version: str = ""
    sampler_factory: Optional[Callable[..., "bsm.ReadingsSampler"]] = None     # (on_sample, plan) -> sampler; default builds the real one
    model_done: Callable[[str, dict], Awaitable] = lambda path, result: _no_model_done(path, result)       # after one model's run is closed and its engine stopped (a Courier deletes a model it was sent)
    estimate_before: Optional[Callable[[str, object], Optional[dict]]] = None   # (model path, plan) -> {"total_mb", "model_mb", "cache_mb", "slots"}: our header-based estimate of the card memory, saved BEFORE the run
    model_facts: Optional[Callable[[str, object], Optional[dict]]] = None      # (model path, plan) -> {"facts": {...}, "cache": {...}}: what the model file says and what its cache would cost (benchmarks_facts), for the live panel
    run_facts: Callable[[str, object], dict] = lambda phase, ctx: {}            # extra manifest keys: ('start', ctx) once the model is loaded, ('end', ctx) as the run is closed (a Courier adds its device, power and job facts)


def _default_sampler(on_sample, plan):
    return bsm.ReadingsSampler(on_sample=on_sample, ceiling_c=plan.ceiling_c)


def engine_state_of(engine_installed: bool, cfg: dict) -> str:
    """What the engine is like on this device: `local_models_off` ("Skip local installation" is on), else `not_installed`, else `installed`. One answer for the tab and the start check."""
    import models_off
    if models_off.is_skipped(cfg):
        return ENGINE_MODELS_OFF
    return ENGINE_INSTALLED if engine_installed else ENGINE_NOT_INSTALLED


def _short(text: str, n: int = 400) -> str:
    return " ".join((text or "").split())[-n:]


# --- the service ------------------------------------------------------------------------------------------------------------------------------------------------------------

def pick_prompt(prompts: list, uses: dict, seed: str) -> dict:
    """The next prompt for a measured request: one of those used the fewest times so far in this model's run (measured plus still in flight, counted across all the levels), taking them in
    an order that is shuffled for each count by the test set's hash (which every result records, so the order can be rebuilt). With nothing dropped, that is complete cycles through all the
    prompts, each cycle in its own order, so every prompt is used the same number of times (never more than one apart)."""
    low = min(uses.get(p["id"], 0) for p in prompts)
    order = list(range(len(prompts)))
    random.Random(f"{seed}:{low}").shuffle(order)
    return next(prompts[k] for k in order if uses.get(prompts[k]["id"], 0) == low)


class BenchmarkService:
    def __init__(self, base_dir, launcher, hooks: Optional[Hooks] = None, clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, heartbeat_s: float = HEARTBEAT_EVERY_S, thermal_poll_s: float = 1.0,
                 guard: Optional["bguard.GuardConfig"] = None, guard_poll_s: float = 1.0, log: Callable[[str], None] = lambda m: None):
        self.base = Path(base_dir)
        self.launcher, self.hooks = launcher, hooks or Hooks()
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.heartbeat_s, self.thermal_poll_s = heartbeat_s, thermal_poll_s
        self.guard_cfg, self.guard_poll_s, self.log = guard or bguard.GuardConfig(), guard_poll_s, log
        self._claim: Optional[dict] = None                     # what ended the current model's test, set by the first to claim it (memory, time, heat, a person's Stop ...)
        self._task: Optional[asyncio.Task] = None
        self._work: Optional[asyncio.Task] = None
        self._cancel = False
        self._shutdown = False
        self._interrupt_reason = ""
        self._thermal: Optional[dict] = None
        self._status: dict = {"running": False}
        self._sampler = None
        self.batches: list = []
        self._bg: dict = {}                                    # the running batch's background: loaded, assigned, started, unloaded, choice

    def claim(self, kind: str, info: Optional[dict] = None) -> bool:
        """The first thing that ends a model's test wins and is the one stored; the ones that arrive after it change nothing."""
        if self._claim is not None:
            return False
        self._claim = {"kind": kind, "info": dict(info or {})}
        return True

    # -- asked by the routes --
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def status(self) -> dict:
        out = dict(self._status)
        e = out.get("engine_busy")
        if isinstance(e, dict) and e.get("since") is not None:
            out["engine_busy"] = {"state": e.get("state"), "slots": e.get("slots"), "seconds": max(0, int(self.clock() - e["since"]))}          # seconds in this state: a real sign of life
        out["server_now_epoch_s"] = self.wall()
        s = self._sampler
        if s is not None and out.get("running"):
            try:
                out["readings"] = dict(s.snapshot(), peaks={str(k): v for k, v in s.highest_lowest().items()})
            except Exception:
                pass
        if not out.get("running"):
            try:
                out["background_now"] = {"loaded": bg.normalize(self.hooks.background_loaded()), "assigned": bg.normalize(self.hooks.background_assigned())}
            except Exception:
                out["background_now"] = {"loaded": [], "assigned": [], "unreadable": True}
        return out

    async def start(self, body, takeover: bool = False) -> dict:
        """Check everything, then start the batch in the background. Returns `{"ok": True, "batch_id"}` or `{"ok": False, "message", ...}` (`needs_takeover` with the list when a
        model MultiProxy loaded for the game holds the card and the person has not agreed yet)."""
        if self.running():
            return {"ok": False, "status": 409, "message": REFUSE_BUSY}
        state = self.hooks.engine_state()
        if state == ENGINE_MODELS_OFF:
            return {"ok": False, "status": 409, "message": REFUSE_MODELS_OFF}
        if state == ENGINE_NOT_INSTALLED:
            return {"ok": False, "status": 409, "message": REFUSE_NO_ENGINE}
        try:
            plan = parse_plan(body)
        except PlanError as e:
            return {"ok": False, "status": 400, "message": str(e)}
        last = self.hooks.last_game_request_at()
        if last is not None and self.wall() - last < RECENT_GAME_REQUEST_S:
            return {"ok": False, "status": 409, "message": REFUSE_RECENT}
        if plan.skyrim == "closed" and self.hooks.game_running():
            return {"ok": False, "status": 409, "message": REFUSE_GAME}
        for m in plan.models:
            if not os.path.isfile(m):
                return {"ok": False, "status": 409, "message": REFUSE_MISSING.format(name=os.path.basename(m))}
            if self.hooks.fit_check(m) == "too_big":
                return {"ok": False, "status": 409, "message": REFUSE_FIT.format(name=os.path.basename(m))}
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            if self.hooks.free_disk_mb(self.base) < MIN_FREE_DISK_MB:
                return {"ok": False, "status": 409, "message": REFUSE_DISK}
        except OSError:
            return {"ok": False, "status": 409, "message": REFUSE_DISK}
        try:
            loaded = bg.normalize(self.hooks.background_loaded())
            assigned = [a for a in bg.normalize(self.hooks.background_assigned()) if a["state"] == bg.STATE_ASSIGNED_STOPPED]
        except Exception:
            loaded, assigned = [], []
        to_start = assigned if plan.background == bg.BACKGROUND_START_NORMAL else []
        if plan.background != bg.BACKGROUND_STOP:
            # "Leave them running" / "start everything it normally runs": the tested model must fit next to all of it before anything is touched
            others = loaded + to_start
            need = bg.biggest_on_one_card_mb(loaded) + bg.biggest_on_one_card_mb(to_start, use_size_for_unknown=True)
            for m in plan.models:
                if need > 0 and self.hooks.fit_next_to(m, need) == "too_big":
                    return {"ok": False, "status": 409, "message": REFUSE_BACKGROUND_FIT.format(name=os.path.basename(m), others=bg.names(others))}
        held = list(self.hooks.loaded_by_multiproxy() or []) if plan.background == bg.BACKGROUND_STOP else []
        if held and not takeover:
            return {"ok": False, "status": 409, "needs_takeover": held, "message": REFUSE_TAKEOVER}
        unloaded: list = []
        if held:
            unloaded = [m["id"] for m in held]
            await self.hooks.unload(unloaded)                         # only the ones MultiProxy loaded, named by id
        started: list = []
        for a in to_start:
            why = await self.hooks.start_service(a["id"])
            if why:
                if started:
                    await self.hooks.unload(started)                  # stop only what this attempt started
                return {"ok": False, "status": 409, "message": REFUSE_BACKGROUND_START.format(name=a["name"]) + f" {why}"}
            started.append(a["id"])
        batch_id = uuid.uuid4().hex[:12]
        self._cancel, self._thermal, self._interrupt_reason, self._claim = False, None, "", None
        self._bg = {"choice": plan.background, "assigned": assigned, "started": started, "unloaded": unloaded, "keep_started": plan.keep_started, "loaded": loaded}
        self._status = {"running": True, "batch_id": batch_id, "profile": plan.profile, "levels": list(plan.levels), "models_total": len(plan.models), "model_index": 0, "step": "starting", "runs": [], "batch_started_epoch_s": self.wall(),
                        "background_choice": plan.background, "background": [i for i in loaded if i["id"] not in unloaded], "background_started": list(started)}
        self._task = asyncio.create_task(self._batch(plan, batch_id))
        return {"ok": True, "batch_id": batch_id}

    async def cancel(self) -> bool:
        """Stop cleanly: in-flight requests are dropped, the engine is unloaded, everything measured is kept, status `cancelled`. False when nothing runs."""
        if not self.running():
            return False
        self.claim("cancel")
        self._cancel = True
        if self._work is not None and not self._work.done():
            self._work.cancel()
        return True

    async def interrupt(self, reason: str) -> bool:
        """End the running test as `interrupted` with `reason` (the owner went quiet, the battery is low, the PC slept). Everything measured is kept. False when nothing runs."""
        if not self.running():
            return False
        self.claim("interrupt", {"reason": reason})
        self._interrupt_reason = reason
        self._cancel = True
        if self._work is not None and not self._work.done():
            self._work.cancel()
        return True

    async def shutdown(self) -> None:
        """The program is closing: the running test ends `interrupted` with reason `program_stopped` (everything measured is kept) and its engine is stopped."""
        if not self.running():
            return
        self.claim("shutdown")
        self._shutdown = True
        self._cancel = True
        if self._work is not None and not self._work.done():
            self._work.cancel()
        await self.wait()

    async def wait(self) -> None:
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    # -- the batch: one run per model, one after another --
    async def _batch(self, plan: Plan, batch_id: str) -> None:
        try:
            for i, path in enumerate(plan.models):
                if self._cancel:
                    break
                if i > 0:
                    gate = await self._memory_gate(path)
                    if gate:
                        self._status.update(batch_stopped=gate, message=BATCH_STOP_MEMORY)
                        break
                    if self._cancel:
                        break
                self._claim = None
                for k in ("layers", "memory_model", "card_users", "model_facts", "engine", "paged_mb", "engine_busy"):
                    self._status.pop(k, None)
                self._status.update(model_index=i + 1, model_name=os.path.basename(path), step="starting", level=None, model_started_epoch_s=self.wall())
                last = i == len(plan.models) - 1
                self._work = asyncio.create_task(self._run_model(plan, batch_id, i, path, last))
                try:
                    result = await self._work
                except asyncio.CancelledError:
                    result = None
                if result:
                    self._status["runs"].append(result)
                    if result.get("status") in bc.RUN_STATES_STOPPED:
                        self._status["stops"] = self._status.get("stops", 0) + 1
                        self._status["last_stop"] = {"index": i + 1, "status": result["status"], "model": os.path.basename(path)}
                try:
                    await asyncio.shield(self.hooks.model_done(path, result or {}))
                except Exception:
                    pass
                if self._cancel or (result or {}).get("status") in ("interrupted", "cancelled", "invalidated") and (result or {}).get("stop_batch"):
                    break
                if (result or {}).get("cleanup_failed"):
                    self._status.update(batch_stopped="cleanup", message=BATCH_STOP_CLEANUP)       # something this engine started is still running: no next model on top of it
                    break
        finally:
            ids = [i for i in self._bg.get("started", []) if not self._bg.get("keep_started")]
            if ids:
                try:
                    await asyncio.shield(self.hooks.unload(ids))      # only the services this run started
                    self._status["background_stopped_after"] = ids
                except Exception:
                    self._status["background_stop_failed"] = ids
            self._status.update(running=False, step="finished")
            self._sampler = None
            self.batches.append(batch_id)

    async def _memory_gate(self, next_path: str) -> str:
        """Between models: wait until the owned processes are gone and the computer's memory has come back (available RAM above the warning level plus 1 GiB, commit charge acceptable, room on
        the card), all of it for `gate_hold_s` in a row. Returns "" to go on, or "memory_not_recovered" after `gate_timeout_s`. While it waits the status says so (not an error)."""
        if self.hooks.memory_now is None:
            return ""
        cfg = self.guard_cfg
        try:
            need = os.path.getsize(next_path) / (1024 * 1024)
        except OSError:
            need = None
        t0, ok_since = self.clock(), None
        while True:
            if self._cancel:
                self._status.pop("waiting_memory", None)
                return ""
            try:
                reading = dict(self.hooks.memory_now() or {})
            except Exception:
                reading = {}
            try:
                alive = bool(self.hooks.owned_alive()) if self.hooks.owned_alive is not None else False
            except Exception:
                alive = True                                   # cannot tell: not "gone"
            now = self.clock()
            if not alive and bguard.recovered_for_next_model(reading, cfg, need):
                ok_since = now if ok_since is None else ok_since
                if now - ok_since >= cfg.gate_hold_s:
                    self._status.pop("waiting_memory", None)
                    return ""
            else:
                ok_since = None
                self._status["waiting_memory"] = True
            if now - t0 >= cfg.gate_timeout_s:
                self._status.pop("waiting_memory", None)
                self.log(f"Benchmarks: the PC's memory did not free up within {int(cfg.gate_timeout_s)} s after the last model, so the batch was stopped")
                return "memory_not_recovered"
            await self.sleep(self.guard_poll_s)

    async def _run_model(self, plan: Plan, batch_id: str, index: int, path: str, last: bool) -> dict:
        ctx = _RunCtx(self, plan, batch_id, index, path, last)
        return await ctx.execute()


# --- one model's run --------------------------------------------------------------------------------------------------------------------------------------------------------

class _RunCtx:
    def __init__(self, svc: BenchmarkService, plan: Plan, batch_id: str, index: int, path: str, last: bool):
        self.svc, self.plan, self.batch_id, self.index, self.path, self.last = svc, plan, batch_id, index, path, last
        self.t0 = svc.clock()
        self.start_wall = svc.wall()
        self.run_id = bc.new_run_id(self.start_wall)
        self.folder: Optional[Path] = None
        self.records: list = []
        self.writers: dict = {}
        self.sampler = None
        self.handle = None
        self.step_states: dict = {}
        self.extra_steps: list = []
        self.levels = sorted(plan.levels)
        self.total = bc.steps_total(self.levels, plan.sustained, plan.cooldown and not last)
        self.current = "start"
        self.level_summaries: dict = {}
        self.sustained: dict = {"status": "not_run"}
        self.manifest_extra: dict = {}
        self.load_time_s: Optional[float] = None
        self.resolved: dict = {}
        self.started_wall = self.start_wall
        self.hashing = None
        self.peak_inflight = 0
        self.testset = tset.load()
        self.prompts = tset.prompts_for(self.testset, plan.prompts)
        self.uses: dict = {}                                                   # prompt id -> measured requests started (and not dropped) in this model's run: the cycle goes on across the levels
        self.sustained_prompt = tset.sustained_prompt(self.testset)
        self.per_slot_tokens = tset.tokens_per_slot(self.prompts + ([self.sustained_prompt] if plan.sustained else []))      # what one slot needs for the biggest prompt and its reply
        self.bg_loaded: list = []
        self.guard = bguard.MemoryGuard(svc.guard_cfg, enabled=plan.memory_guard)
        self.abort_flight = asyncio.Event()                                    # set when a memory hold has lasted past the grace: requests still running are given up (not counted)
        self.outstanding = 0                                                   # requests sent and not finished
        self.last_progress = svc.clock()                                       # when any request last got a word or ended
        self.stop_outcomes: list = []                                          # how each stop of this model's engine went
        self.cooldown_s = 0.0                                                  # the time limit does not count the cool-down after the model
        self._cooldown_at: Optional[float] = None
        self.mem_before: Optional[dict] = None
        self.mem_after: Optional[dict] = None

    # -- helpers --
    def now(self) -> float:
        return self.svc.clock() - self.t0

    def beat(self, finished: bool = False) -> None:
        if self.folder is not None:
            bs.write_progress(self.folder, self.current, bc.steps_done(self.step_states), self.total, self.svc.wall(), finished)

    def set_step(self, name: str, state: str = "running") -> None:
        self.current = name
        if name in bc.step_plan(self.levels, self.plan.sustained, self.plan.cooldown and not self.last):
            self.step_states[name] = state
        self.svc._status.update(step=name, steps_done=bc.steps_done(self.step_states), steps_total=self.total)
        self.beat()

    def done_step(self, name: str) -> None:
        self.step_states[name] = "done"
        self.svc._status.update(steps_done=bc.steps_done(self.step_states))
        self.beat()

    def write(self, kind: str, row: dict) -> None:
        self.writers[kind].append(row)

    async def wait_samples(self, k: int = 1) -> None:
        if self.sampler is None:
            return
        target = self.sampler.samples_taken + k
        for _ in range(100):
            if self.sampler.samples_taken >= target or self.sampler._gave_up:
                return
            await self.svc.sleep(0.05)

    # -- the whole run --
    async def execute(self) -> dict:
        svc, plan = self.svc, self.plan
        status, reason, stop_batch = bc.RUN_COMPLETED, "", False
        heartbeat = None
        try:
            try:
                self.svc._status.update(step="hashing")
                model_id = await asyncio.to_thread(bs.model_id_for, self.path, svc.base, self._hash_progress, lambda: svc._cancel)
            except bs.HashCancelled:
                return {"run_id": None, "status": bc.RUN_CANCELLED, "stop_batch": True}
            self.hashing = None
            self.bg_loaded = self._read_background()
            self.folder = bs.create_run(svc.base, self.run_id, self._manifest(model_id))
            self.writers = {"requests": bs.JsonlWriter(self.folder / bs.REQUESTS), "samples": bs.JsonlWriter(self.folder / bs.SAMPLES)}
            svc._status["run_id"] = self.run_id
            self.sampler = (svc.hooks.sampler_factory or _default_sampler)(lambda row: self.writers["samples"].append(row), plan)
            svc._sampler = self.sampler
            self.sampler.start()
            heartbeat = asyncio.create_task(self._heartbeat())
            watcher = asyncio.create_task(self._watch_heat() if plan.heat_stop else asyncio.sleep(0))
            mem_watch = asyncio.create_task(self._watch_memory())
            time_watch = asyncio.create_task(self._watch_time())
            card_watch = asyncio.create_task(self._watch_card_users())
            try:
                await self._steps(model_id)
            finally:
                for t in (watcher, mem_watch, time_watch, card_watch):
                    t.cancel()
        except asyncio.CancelledError:
            claim = svc._claim or {}
            if claim.get("kind") == "memory":                  # the PC was protected: this model is skipped, the batch goes on
                status, reason = bc.RUN_STOPPED_MEMORY, (claim.get("info") or {}).get("reason") or bguard.R_LOW
            elif claim.get("kind") == "time":
                status, reason = bc.RUN_STOPPED_TIME, bguard.R_TOO_LONG
            else:
                if svc._shutdown:
                    status, reason = bc.RUN_INTERRUPTED, "program_stopped"
                elif svc._interrupt_reason:
                    status, reason = bc.RUN_INTERRUPTED, svc._interrupt_reason
                elif svc._cancel:
                    status, reason = bc.RUN_CANCELLED, ""
                else:
                    status, reason = bc.RUN_INTERRUPTED, "thermal_stop" if svc._thermal else "stopped"
                stop_batch = True
        except EngineStartError as e:
            status, reason = bc.RUN_FAILED, _short(e.message + " " + e.log_tail)
        except EngineDied as e:
            status, reason = bc.RUN_FAILED, "The engine stopped. Its last words: " + _short(e.tail)
        except LevelFailed as e:
            status, reason = bc.RUN_FAILED, str(e)
        except Exception as e:                              # a bug or a full disk must end this run, not the program
            status, reason = bc.RUN_FAILED, f"{type(e).__name__}: {_short(str(e), 200)}"
        finally:
            if heartbeat:
                heartbeat.cancel()
        if self.folder is None:
            return {"run_id": None, "status": status, "reason": reason, "stop_batch": True}
        try:
            await self._finish(status, reason)
        except Exception:
            pass
        return {"run_id": self.run_id, "status": status, "reason": reason, "model": os.path.basename(self.path), "stop_batch": stop_batch,
                "cleanup_failed": any(o.get("owned_gone") is False for o in self.stop_outcomes)}

    def _hash_progress(self, done: int, total: int) -> None:
        self.svc._status["hashing"] = {"percent": int(100 * done / total) if total else 100}

    async def _heartbeat(self) -> None:
        while True:
            await self.svc.sleep(self.svc.heartbeat_s)
            self.beat()

    async def _watch_heat(self) -> None:
        """Each second: has a tested card reached its own limit? Then the run ends `interrupted` with reason `thermal_stop`, the reading saved."""
        work = asyncio.current_task()
        while True:
            await self.svc.sleep(self.svc.thermal_poll_s)
            stop = self.sampler.thermal_stop() if self.sampler else {"stop": False}
            if stop.get("stop") and self.svc.claim("thermal", stop):
                self.svc._thermal = dict(stop, at_s=round(self.now(), 3), step=self.current)
                self.manifest_extra["thermal_stop_reading"] = self.svc._thermal
                if self.svc._work is not None:
                    self.svc._work.cancel()
                return

    def _reading(self, m: dict) -> dict:
        """The guard's reading from the sampler's latest machine row, the card and the engine."""
        total = m.get("ram_total_mb")
        avail = m.get("ram_available_mb")
        if avail is None and total is not None and m.get("ram_used_mb") is not None:
            avail = total - m["ram_used_mb"]
        gpus = self.sampler.snapshot()["gpus"] if self.sampler else []
        full = [g for g in gpus if g.get("vram_used_mb") is not None and g.get("vram_total_mb")]
        top = max(full, key=lambda g: g["vram_used_mb"] / g["vram_total_mb"]) if full else {}
        ws = None
        if self.handle is not None and self.svc.hooks.engine_working_set_mb is not None:
            try:
                ws = self.svc.hooks.engine_working_set_mb(self.handle.pid)
            except Exception:
                ws = None
        return {"available_mb": avail, "total_mb": total, "commit_used_mb": m.get("commit_used_mb"), "commit_limit_mb": m.get("commit_limit_mb"), "psi_full_avg10": m.get("psi_full_avg10"),
                "vram_used_mb": top.get("vram_used_mb"), "vram_total_mb": top.get("vram_total_mb"), "engine_ws_mb": ws,
                "progress_age_s": round(self.svc.clock() - self.last_progress, 3) if self.outstanding > 0 else 0.0}

    def _stop_record(self, kind: str, info: dict) -> dict:
        """Everything worth knowing about why this model was stopped, kept in the run's files."""
        hl = self._hl()
        gpu_hi = [v.get("vram_used_mb", {}).get("highest_sampled") for k, v in hl.items() if k != "machine"]
        return {"kind": kind, "reason": info.get("reason"), "threshold": info.get("threshold"), "duration_s": info.get("duration_s"), "reading": info.get("reading"),
                "samples_before": info.get("samples_before") or [], "events": info.get("events") or [], "limit_s": info.get("limit_s"), "elapsed_s": info.get("elapsed_s"),
                "step": self.current, "concurrency": self.handle.slots if self.handle else None, "at_s": round(self.now(), 3),
                "engine": {k: self.resolved.get(k) for k in ("slots", "ctx", "total_slots", "layers_on_card", "layers_total", "flash_attn_active", "cache_type_k", "cache_type_v")},
                "estimate_mb": None, "observed": {"ram_used_highest_mb": hl.get("machine", {}).get("ram_used_mb", {}).get("highest_sampled"),
                                                  "ram_available_lowest_mb": self.sampler.lowest_available_ram_mb() if self.sampler else None,
                                                  "vram_highest_mb": max([v for v in gpu_hi if v is not None], default=None)},
                "requests_in_flight_cancelled": self.outstanding}

    async def _watch_memory(self) -> None:
        """Each second: feed the guard the computer's memory. A hold stops new requests; a stop ends this model's test (first to claim it wins)."""
        svc, cfg = self.svc, self.svc.guard_cfg
        last_t, last_seen = None, svc.clock()
        while True:
            await svc.sleep(svc.guard_poll_s)
            if self.sampler is None:
                continue
            m = self.sampler.snapshot().get("machine") or {}
            now = svc.clock()
            if m.get("t_s") != last_t:
                last_t, last_seen = m.get("t_s"), now
            reading = self._reading(m) if m and now - last_seen <= cfg.stale_after_s else None          # a stale reading is no reading
            state = self.guard.feed(now, reading)
            svc._status["memory_hold"] = state == bguard.STATE_HOLD
            if state == bguard.STATE_STOP:
                info = self.guard.stopped or {}
                if svc.claim("memory", info):
                    self.manifest_extra["safety_stop"] = self._stop_record("memory", info)
                    self.log_stop("memory", info)
                    if svc._work is not None:
                        svc._work.cancel()
                return
            if state == bguard.STATE_HOLD and self.guard._hold_at is not None and now - self.guard._hold_at >= cfg.inflight_grace_s:
                self.abort_flight.set()                       # the requests still running are given up; new ones wait
            elif state == bguard.STATE_OK:
                self.abort_flight.clear()

    def log_stop(self, kind: str, info: dict) -> None:
        try:
            self.svc.log(f"Benchmarks: stopped {os.path.basename(self.path)} to protect the PC ({info.get('reason')}, threshold {info.get('threshold')}, held {info.get('duration_s')} s, "
                         f"step {self.current})")
        except Exception:
            pass

    async def _watch_time(self) -> None:
        """The model's whole test (load and retries included, the cool-down after it excluded) has a time limit."""
        svc, limit = self.svc, self.svc.guard_cfg.model_time_limit_s
        while True:
            await svc.sleep(svc.guard_poll_s)
            running_cooldown = (svc.clock() - self._cooldown_at) if self._cooldown_at is not None else 0.0
            elapsed = self.now() - self.cooldown_s - running_cooldown
            if elapsed >= limit:
                info = {"reason": bguard.R_TOO_LONG, "limit_s": limit, "elapsed_s": round(elapsed, 1), "threshold": limit, "duration_s": round(elapsed, 1)}
                if svc.claim("time", info):
                    self.manifest_extra["safety_stop"] = self._stop_record("time", info)
                    self.log_stop("time", info)
                    if svc._work is not None:
                        svc._work.cancel()
                return

    async def _admit(self) -> None:
        """A request starts only while the guard is not holding. Cancelled or stopped tests do not wait here."""
        while not self.guard.admitting() and not self.svc._cancel and self.svc._claim is None:
            await self.svc.sleep(min(0.2, self.svc.guard_poll_s))

    def _mem_now(self) -> Optional[dict]:
        f = self.svc.hooks.memory_now
        if f is None:
            return None
        try:
            return dict(f() or {})
        except Exception:
            return None

    async def _after_unload(self) -> None:
        """Once per model, after its engine is stopped: let the computer settle, then look at its memory again and say if it did not come back."""
        if self.mem_after is not None or self.svc.hooks.memory_now is None:
            return
        await self.svc.sleep(self.svc.guard_cfg.settle_s)
        self.mem_after = self._mem_now()
        gone = bguard.memory_not_released(self.mem_before, self.mem_after, self.svc.guard_cfg)
        if gone:
            self.manifest_extra["memory_not_released"] = gone
            self.svc.log(f"Benchmarks: {os.path.basename(self.path)}: memory did not return after unload: before {gone['before_mb']} MB, after {gone['after_mb']} MB")

    def _read_background(self) -> list:
        """What is loaded on the test device now (read again at each run start), minus what a "Stop others" just unloaded; marks the services this batch started."""
        b = self.svc._bg
        try:
            now = bg.normalize(self.svc.hooks.background_loaded())
        except Exception:
            now = list(b.get("loaded", []))
        gone = set(b.get("unloaded", []))
        started = set(b.get("started", []))
        out = []
        for i in now:
            if i["id"] in gone:
                continue
            out.append(dict(i, started_by_this_run=True) if i["id"] in started else i)
        return out

    def _setup_dict(self, extra: Optional[dict] = None) -> dict:
        d = {"kind": "chat", **tset.version_info(self.testset), "profile": self.plan.profile, "requested": self.plan.requested()}
        d.update(extra or {})
        return d

    def _background_manifest(self) -> dict:
        b = self.svc._bg
        return {"background": self.bg_loaded, "background_choice": self.plan.background, "background_assigned": b.get("assigned", []), "background_started": list(b.get("started", [])),
                "background_stopped_for_test": list(b.get("unloaded", [])), "background_keep_started": bool(b.get("keep_started")), "background_key": bg.background_key(self.bg_loaded),
                "background_names": [i["name"] for i in self.bg_loaded], "pair_of": self.plan.pair_of}

    def _manifest(self, model_id_text: str) -> dict:
        p = self.plan
        return {"kind": "chat", "device": "this", "profile": p.profile, **tset.version_info(self.testset), "model_id": model_id_text, "model_file": os.path.basename(self.path), "model_size_bytes": os.path.getsize(self.path),
                "run_id": self.run_id, "batch_id": self.batch_id, "model_index": self.index + 1, "models_total": len(p.models), "requested_settings": p.requested(),
                "multiproxy_version": self.svc.hooks.version, "started_epoch_s": self.start_wall, "steps_total": self.total, "steps_done": 0, "status": bc.RUN_RUNNING, "reason": "",
                "setup_id": bc.setup_id(self._setup_dict({"background": bg.setup_part(self.bg_loaded)})), "setup_core_id": bc.setup_id(self._setup_dict()), "setup_id_final": False,
                **self._background_manifest(), **self._estimate_manifest()}

    def _estimate_manifest(self) -> dict:
        """Our estimate of the card memory for this run (from the model file's header), saved before the run so the Results can set it beside what the engine really took. Numbers only."""
        f = self.svc.hooks.estimate_before
        if f is None:
            return {}
        try:
            est = f(self.path, self.plan)
        except Exception:
            est = None
        return {"memory_estimate_mb": est} if isinstance(est, dict) and est.get("total_mb") else {}

    async def _read_other_programs(self) -> list:
        """Who else is using the card, at this moment: `[{"name", "mb"}]` for the biggest few (never the engine this run started). [] when it cannot be read."""
        reader = getattr(self.sampler, "read_card_users", None)
        if reader is None:
            return []
        try:
            use = await asyncio.to_thread(reader, self.handle.pid if self.handle is not None else None)
        except Exception:
            return []
        return bcu.top(use.get("programs") or []) if isinstance(use, dict) and use.get("available") else []

    async def _note_programs_at_start(self) -> None:
        """Once, before the model loads: the biggest other programs on the card, kept in this run's own folder and shown on this PC only (see benchmarks_cardusage)."""
        top = await self._read_other_programs()
        self.svc._status["card_users"] = top
        if top:
            bs.write_json(self.folder, bcu.OTHER_FILE, {"at_start": top})
        f = self.svc.hooks.model_facts
        if f is not None:
            try:
                got = await asyncio.to_thread(f, self.path, self.plan)
            except Exception:
                got = None
            if isinstance(got, dict) and got:
                self.svc._status["model_facts"] = got

    async def _note_engine(self) -> None:
        """What the live panel says about the running engine: its name, build and kind of card code (from its own start log), how many requests it handles at once and how many are busy now
        (its own `/slots` route), and the card memory it has put in main memory ("paged", only when above 0). A part with no real source is left out."""
        if self.handle is None:
            return
        eng = {k: v for k, v in (("name", "llama.cpp"), ("build", (self.resolved or {}).get("engine_build")), ("backend", (self.resolved or {}).get("backend"))) if v}
        ask = getattr(self.svc.launcher, "slot_state", None)
        if ask is not None:
            try:
                st = await ask(self.handle)
            except Exception:
                st = None
            if isinstance(st, dict) and isinstance(st.get("slots"), int) and isinstance(st.get("busy"), int):
                eng["slots"], eng["busy"] = st["slots"], st["busy"]
        if eng:
            self.svc._status["engine"] = eng
        peek = getattr(self.sampler, "peek_engine_shared_gpu_mb", None)
        paged = None
        if peek is not None and getattr(self.handle, "pid", None):
            try:
                paged = await asyncio.to_thread(peek, self.handle.pid)
            except Exception:
                paged = None
        if isinstance(paged, (int, float)) and paged > 0:
            self.svc._status["paged_mb"] = int(paged)
        else:
            self.svc._status.pop("paged_mb", None)

    async def _watch_card_users(self) -> None:
        """During the run, every `LIVE_EVERY_S` seconds (one counter call each; never the sampler's one-second tick): the live panel's "Everything else on the card"."""
        while True:
            await self.svc.sleep(bcu.LIVE_EVERY_S)
            self.svc._status["card_users"] = await self._read_other_programs()
            await self._note_engine()

    # -- the steps --
    async def _steps(self, model_id: str) -> None:
        svc, plan = self.svc, self.plan
        first_slots = self.levels[0]
        # load
        self.mem_before = self._mem_now()
        self.set_step("load")
        self.sampler.mark("idle_before_load")
        await self.wait_samples(1)
        await self._note_programs_at_start()
        self.manifest_extra["start_temp_c"] = {str(g["gpu_index"]): g.get("temp_c") for g in self.sampler.snapshot()["gpus"]}
        self.sampler.mark("loading")                         # the memory rises while the model loads: that is not "idle"
        await self._start_engine(first_slots)
        self.sampler.mark("after_load")
        await self.wait_samples(1)
        if self.handle.pid:
            await asyncio.to_thread(self.sampler.read_engine_shared_gpu_mb, self.handle.pid)
        self._write_manifest_after_load(model_id)
        self.done_step("load")
        # warmup
        self.set_step("warmup")
        self.sampler.mark("warmup")
        await self._warmup(first_slots)
        self.done_step("warmup")
        # levels
        for n in self.levels:
            name = f"level:{n}"
            if self.handle.slots != n:
                self.svc._status.update(step=name)          # the step the number names, shown while the engine restarts for it
                await self._restart_engine(n)
                self.sampler.mark("warmup")
                await self._warmup(n)
            self.set_step(name)
            self.sampler.mark(f"level_{n}")
            await self._level(n)
            self.done_step(name)
            self._write_summary()
        # sustained
        if plan.sustained:
            self.set_step("sustained")
            if self.handle.slots != 4:
                await self._restart_engine(4)
                self.sampler.mark("warmup")
                await self._warmup(4)
            self.sampler.mark("sustained")
            await self._sustained()
            self.done_step("sustained")
            self._write_summary()
        # unload (one more look at the engine's shared graphics memory first, for the spill evidence)
        self.set_step("unload")
        if self.handle is not None and self.handle.pid:
            await asyncio.to_thread(self.sampler.read_engine_shared_gpu_mb, self.handle.pid)
        await self._stop_engine()
        self.sampler.mark("unloaded")
        await self.wait_samples(1)
        await self._after_unload()
        self.done_step("unload")
        # cooldown (only when another model follows)
        if plan.cooldown and not self.last:
            self.set_step("cooldown")
            self.sampler.mark("cooldown")
            self._cooldown_at = self.svc.clock()
            try:
                self.manifest_extra["cooldown"] = await self._cooldown()
            finally:
                self.cooldown_s += self.svc.clock() - self._cooldown_at
                self._cooldown_at = None
            self.done_step("cooldown")

    def _engine_busy(self, state: Optional[str], slots: int = 0) -> None:
        """What the engine is doing inside the current step (`loading` or `restarting`), for the live card; None when it is doing nothing special. The step itself (`_status["step"]`)
        is never changed by it: the number and the words on the card must name the same step (a restart before the sustained run used to leave the card saying "Loading the model"
        for the whole 5 minutes)."""
        if state is None:
            self.svc._status.pop("engine_busy", None)
        else:
            self.svc._status["engine_busy"] = {"state": state, "slots": slots, "since": self.svc.clock()}

    async def _start_engine(self, slots: int, state: str = "loading") -> None:
        self._engine_busy(state, slots)
        try:
            await self._start_engine_inner(slots)
        finally:
            self._engine_busy(None)

    async def _start_engine_inner(self, slots: int) -> None:
        t = self.svc.clock()
        task =asyncio.ensure_future(self.svc.launcher.start(self.path, slots, dict(self.plan.overrides, per_slot_tokens=self.per_slot_tokens), self.folder / "engine.log", self.run_id))
        try:
            self.handle = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Stop or a safety stop arrived while the engine was starting. It must never be left running: give the start a moment to finish so the engine it made is ours to stop
            # (the real launcher also stops its own engine when it is cancelled).
            try:
                self.handle = await asyncio.wait_for(asyncio.shield(task), 5.0)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise
        self.load_time_s = round(self.svc.clock() - t, 3) if self.load_time_s is None else self.load_time_s
        self.resolved = dict(self.handle.resolved or {}, slots=slots)
        if self.resolved.get("layers_on_card") is not None and self.resolved.get("layers_total") is not None:
            self.svc._status["layers"] = {"on_card": self.resolved["layers_on_card"], "total": self.resolved["layers_total"]}          # the live panel says when part of the model runs on the processor
        buffers = self.resolved.get("buffers_mb") or {}
        if all(buffers.get(k) is not None for k in ("model", "cache", "compute")):
            self.svc._status["memory_model"] = {"slots": slots, **{k: buffers[k] for k in ("model", "cache", "compute")}}          # measured: what the engine itself printed when it loaded
        else:
            self.svc._status.pop("memory_model", None)
            self.svc.log("Benchmarks: " + bcu.NO_BUFFERS_LINE)
        await self._note_engine()

    async def _stop_engine(self) -> None:
        h, self.handle = self.handle, None
        if h is not None:
            out = await asyncio.shield(h.stop())
            if isinstance(out, dict):
                self.stop_outcomes.append(out)

    async def _restart_engine(self, slots: int) -> None:
        """The slots must change: stop only this run's own engine and start it again. Recorded as an `engine_restart` step; never counted in any speed."""
        before = self.handle.slots
        self.current = "engine_restart"          # (what the records say; the card keeps naming the plan's step and shows the restart inside it: `_engine_busy`)
        self.beat()
        t = self.svc.clock()
        self.sampler.mark("engine_restart")
        self._engine_busy("restarting", slots)          # (the stop takes a moment too)
        await self._stop_engine()
        await self._start_engine(slots, "restarting")
        self.extra_steps.append({"step": "engine_restart", "slots_before": before, "slots_after": slots, "load_time_s": round(self.svc.clock() - t, 3), "at_s": round(self.now(), 3)})

    def _write_manifest_after_load(self, model_id: str) -> None:
        m = bs.read_manifest(self.folder)
        hw = dict(self.svc.hooks.hardware() or {})
        hw.setdefault("os", platform.platform())
        hw.setdefault("cpu", platform.processor())
        hw["gpus"] = [{"gpu_index": i, "name": n, "vram_total_mb": next((g.get("vram_total_mb") for g in self.sampler.snapshot()["gpus"] if g["gpu_index"] == i), None)}
                      for i, n in sorted(self.sampler.gpu_names().items())]
        core = {"kind": "chat", **tset.version_info(self.testset), "profile": self.plan.profile, "requested": self.plan.requested(), "resolved": self.resolved,
                "engine": self.resolved.get("engine_build"), "model_id": model_id}
        setup = dict(core, background=bg.setup_part(self.bg_loaded))
        m.update(resolved_settings=self.resolved, engine={"name": "llama.cpp", "build": self.resolved.get("engine_build")}, load_time_s=self.load_time_s, hardware=hw,
                 setup_id=bc.setup_id(setup), setup_core_id=bc.setup_id(core), setup_id_final=True, thermal_basis={str(k): v for k, v in self.sampler.thermal_basis().items()})
        m["gpu_choice"] = self.resolved.get("gpu_choice")
        m.update(self.svc.hooks.run_facts("start", self) or {})
        for g in hw["gpus"]:
            chosen = (self.resolved.get("gpu_choice") or {}).get("card")
            g["used_by_engine"] = None if not chosen else (g["name"] == chosen)
        m["hardware"] = hw
        bs.write_json(self.folder, bs.MANIFEST, m)

    async def _warmup(self, level: int) -> None:
        for i in range(self.plan.warmup):
            rec = await self._request(level, self.prompts[i % len(self.prompts)], warmup=True, n=i + 1)
            self.write("requests", rec.to_row())
            self.records.append(rec)
            await self._check_engine(rec)

    async def _check_engine(self, rec: "bc.RequestRecord") -> None:
        if rec.status != bc.STATUS_OK and self.handle is not None and not await self.handle.alive():
            raise EngineDied(self.handle.log_tail())

    async def _request(self, level: int, prompt: dict, warmup: bool, n: int) -> "bc.RequestRecord":
        svc = self.svc
        await self._admit()
        rec = bc.RequestRecord(request_id=f"{'w' if warmup else 'r'}{level}-{n}-{uuid.uuid4().hex[:4]}", level=level, prompt_id=prompt["id"], prompt_length_class=prompt["length"],
                               warmup=warmup, t_submit_s=self.now())
        url = f"http://127.0.0.1:{self.handle.port}/v1/chat/completions"
        payload = {"model": self.handle.alias, "messages": prompt["messages"], "max_tokens": prompt["max_tokens"], "temperature": self.plan.temperature, "stream": True,
                   "stream_options": {"include_usage": True}, "cache_prompt": False}
        got = {"timings": None, "usage": None, "finish": "", "words": 0, "text": []}

        async def go():
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=None, sock_connect=10)) as r:
                    if r.status != 200:
                        rec.status, rec.error = bc.STATUS_ERROR, f"HTTP {r.status}: {_short((await r.text()), 160)}"
                        return
                    async for raw in r.content:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        if isinstance(obj.get("timings"), dict):
                            got["timings"] = obj["timings"]
                        if isinstance(obj.get("usage"), dict):
                            got["usage"] = obj["usage"]
                        for ch in obj.get("choices") or []:
                            d = ch.get("delta") or {}
                            if d.get("content") or d.get("reasoning_content"):
                                self.last_progress = self.svc.clock()
                            if (d.get("content") or d.get("reasoning_content")) and rec.t_first_token_s is None:
                                rec.t_first_token_s = self.now()
                            if d.get("content") or d.get("reasoning_content"):
                                got["words"] += 1
                            if d.get("content"):
                                got["text"].append(d["content"])
                            if ch.get("finish_reason"):
                                got["finish"] = ch["finish_reason"]

        self.outstanding += 1
        task = asyncio.ensure_future(go())
        abort = asyncio.ensure_future(self.abort_flight.wait())
        try:
            done, _ = await asyncio.wait({task, abort}, timeout=self.plan.request_timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                task.result()
            elif abort in done:                                  # a memory hold lasted too long: this request is given up, not counted, and not tried again while memory is short
                rec.status, rec.error = bc.STATUS_ERROR, "given up while the PC's memory was low"
            else:
                rec.status, rec.error = bc.STATUS_TIMEOUT, "no answer in time"
        except (aiohttp.ClientError, OSError, ValueError) as e:
            rec.status, rec.error = bc.STATUS_ERROR, _short(f"{type(e).__name__}: {e}", 160)
        finally:
            self.outstanding -= 1
            self.last_progress = self.svc.clock()
            for t in (task, abort):
                if not t.done():
                    t.cancel()
            await asyncio.gather(task, abort, return_exceptions=True)
        rec.t_done_s = self.now()
        t, u = got["timings"] or {}, got["usage"] or {}
        rec.finish_reason = got["finish"]
        rec.answer = "".join(got["text"])[:ANSWER_KEEP_CHARS]                     # what the model wrote (the checks read it, a failed check shows its line)
        rec.prompt_n, rec.prompt_ms = t.get("prompt_n"), t.get("prompt_ms")
        rec.predicted_n, rec.predicted_ms = t.get("predicted_n"), t.get("predicted_ms")
        rec.prompt_tokens = u.get("prompt_tokens", t.get("prompt_n"))
        rec.completion_tokens = u.get("completion_tokens", t.get("predicted_n"))
        if rec.status == bc.STATUS_OK and not got["words"]:
            rec.status, rec.error = bc.STATUS_ERROR, "the model sent no words"
        return rec

    async def _level(self, n: int) -> None:
        """`n` requests in flight at once until N measured ones have completed (errors and timeouts do not count toward N; requests still in flight when the N-th completes are dropped,
        not recorded)."""
        want = self.plan.requests_per_level
        st = {"ok": 0, "attempts": 0, "inflight": 0, "peak": 0, "errors": 0}
        cap = ATTEMPTS_PER_REQUEST * want + 10
        done = asyncio.Event()
        failure: list = []

        async def worker():
            while not done.is_set():
                if st["attempts"] >= cap:
                    if not failure:
                        failure.append("too many requests failed")
                    done.set()
                    return
                st["attempts"] += 1
                k = st["attempts"]
                st["inflight"] += 1
                st["peak"] = max(st["peak"], st["inflight"])
                try:
                    prompt = pick_prompt(self.prompts, self.uses, self.testset["sha256"])
                    self.uses[prompt["id"]] = self.uses.get(prompt["id"], 0) + 1
                    try:
                        rec = await self._request(n, prompt, warmup=False, n=k)
                    except asyncio.CancelledError:
                        self.uses[prompt["id"]] -= 1                # dropped with the level: it does not count
                        raise
                finally:
                    st["inflight"] -= 1
                if done.is_set():
                    self.uses[prompt["id"]] -= 1             # finished after the N-th: not one of the measured requests, so it does not count
                    return
                self.write("requests", rec.to_row())
                self.records.append(rec)
                if rec.status == bc.STATUS_OK:
                    st["ok"] += 1
                    if st["ok"] >= want:
                        done.set()
                else:
                    try:
                        await self._check_engine(rec)
                    except EngineDied as e:
                        failure.append(e)
                        done.set()
                        return

        tasks = [asyncio.create_task(worker()) for _ in range(n)]
        try:
            await done.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.peak_inflight = st["peak"]
        summary = bc.level_summary(self.records, n, slots=self.handle.slots if self.handle else n)
        summary["actual_overlap"] = st["peak"]
        summary["engine"] = {k: self.resolved.get(k) for k in ("slots", "ctx", "ctx_requested", "total_slots", "layers_on_card", "layers_total", "flash_attn_active", "cache_type_k", "cache_type_v", "buffers_mb")}     # the engine this level ran on (a restart replaces it)
        summary["vram_highest_in_level_mb"] = {str(k): v.get("vram_used_mb") for k, v in self.sampler.highest_in_phase(f"level_{n}").items() if k != "machine"}
        self.level_summaries[str(n)] = summary
        if failure:
            if isinstance(failure[0], EngineDied):
                raise failure[0]
            raise LevelFailed(f"Level {n}: {failure[0]}.")

    async def _sustained(self) -> None:
        samples, failed = [], ""
        end = self.svc.clock() + self.plan.sustained_s
        t_start = self.svc.clock()
        stop = asyncio.Event()

        async def worker(i):
            nonlocal failed
            k = 0
            while self.svc.clock() < end and not stop.is_set():
                k += 1
                rec = await self._request(4, self.sustained_prompt, warmup=False, n=1000 * (i + 1) + k)
                self.write("requests", dict(rec.to_row(), sustained=True))
                if rec.status != bc.STATUS_OK:
                    failed = failed or f"a request {rec.status}: {rec.error}"
                    stop.set()
                    break
                samples.append((self.svc.clock() - t_start, rec.generation_tok_s().value if rec.generation_tok_s().ok else None))

        tasks = [asyncio.create_task(worker(i)) for i in range(4)]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            # a heat stop, Stop benchmark or the program closing: the sustained run is `interrupted` with the reason, what it measured so far is kept
            why = "thermal_stop" if self.svc._thermal else ("program_stopped" if self.svc._shutdown else "stopped")
            self.sustained = bc.sustained_verdict(samples, duration_s=self.plan.sustained_s, interrupted_reason=why)
            raise
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.sustained = bc.sustained_verdict(samples, duration_s=self.plan.sustained_s, failed_reason=failed)
        if failed and self.handle is not None and not await self.handle.alive():
            raise EngineDied(self.handle.log_tail())

    async def _cooldown(self) -> dict:
        svc, plan = self.svc, self.plan
        t = svc.clock()
        deadline = t + plan.cooldown_max_s
        timed_out, temps = False, []
        while True:
            temps = [g.get("temp_c") for g in self.sampler.snapshot()["gpus"] if g.get("temp_c") is not None]
            if not temps or max(temps) < plan.cooldown_c:
                break
            if svc.clock() >= deadline:
                timed_out = True
                break
            await svc.sleep(1.0)
        return {"wanted_below_c": plan.cooldown_c, "waited_s": round(svc.clock() - t, 3), "final_temp_c": max(temps) if temps else None, "timed_out": timed_out, "measured": bool(temps)}

    def _protection_facts(self) -> dict:
        """What protected the PC in this run and what the computer's memory did, for the run's files."""
        cfg, hl = self.svc.guard_cfg, self._hl()
        total = (hl.get("machine", {}).get("ram_total_mb") or {}).get("highest_sampled")
        out = {"protections": {"memory_guard": self.plan.memory_guard, "emergency_floor": True, "heat_stop": self.plan.heat_stop, "model_time_limit_s": cfg.model_time_limit_s,
                               "request_timeout_s": self.plan.request_timeout_s},
               "memory_guard": {"constants": cfg.as_dict(), "levels_mb": {k: round(v) for k, v in cfg.levels(total).items()} if total else None, "events": list(self.guard.events),
                                "recoveries": self.guard.recoveries},
               "machine_memory": {"before_load": self.mem_before, "after_unload": self.mem_after, "in_use_highest_mb": hl.get("machine", {}).get("ram_used_mb", {}).get("highest_sampled"),
                                  "available_lowest_mb": self.sampler.lowest_available_ram_mb() if self.sampler else None}}
        stop = self.manifest_extra.get("safety_stop")
        if stop is not None:
            stop["cleanup"] = self.stop_outcomes[-1] if self.stop_outcomes else {}
            stop["requests"] = {"finished": sum(1 for r in self.records if r.status == bc.STATUS_OK and not r.warmup),
                                "timed_out": sum(1 for r in self.records if r.status == bc.STATUS_TIMEOUT), "errors": sum(1 for r in self.records if r.status == bc.STATUS_ERROR)}
            out["safety_stop"] = stop
        return out

    # -- the end --
    def _hl(self) -> dict:
        return self.sampler.highest_lowest() if self.sampler else {}

    def _memory_phases(self) -> dict:
        out = {}
        idle = self.sampler.highest_in_phase("idle_before_load")
        after = self.sampler.highest_in_phase("after_load")
        for gpu, vals in self._hl().items():
            if gpu == "machine":
                continue
            highest = vals.get("vram_used_mb", {}).get("highest_sampled")
            out[str(gpu)] = {"vram_idle_before_load_mb": idle.get(gpu, {}).get("vram_used_mb"), "vram_after_load_mb": after.get(gpu, {}).get("vram_used_mb"),
                             "vram_highest_overall_mb": highest, "vram_total_mb": vals.get("vram_total_mb", {}).get("highest_sampled"), "kind": "measured",
                             "scope": "whole card (other programs included); highest sampled value"}
            out[str(gpu)]["model_vram_delta_mb"] = bc.model_vram_delta_mb(highest, out[str(gpu)]["vram_idle_before_load_mb"])
        return out

    def _skyrim(self) -> tuple:
        p, hooks = self.plan, self.svc.hooks
        if not hooks.runs_game():
            return "not_applicable", 0.0                 # a device that does not run the game: the competing load is its services (contract 12b)
        if p.skyrim == "closed":
            return "closed", 0.0
        if p.skyrim == "typed":
            return "typed", (p.skyrim_gb or 0) * 1024
        if p.skyrim == "available":
            return "typed", None                         # worked out per card in `_write_summary`: the card's memory minus what the person says is left for models
        peak = hooks.skyrim_peak_mb()
        return ("running" if hooks.game_running() else "history"), peak

    def _write_summary(self, final: bool = False) -> None:
        mem = self._memory_phases()
        state, peak = self._skyrim()
        gpus = [{"gpu_index": int(i), "vram_total_mb": m["vram_total_mb"], "vram_idle_before_load_mb": m["vram_idle_before_load_mb"], "vram_highest_overall_mb": m["vram_highest_overall_mb"]}
                for i, m in mem.items()]
        if self.plan.skyrim == "available":
            head = []
            for g in gpus:
                total = g.get("vram_total_mb")
                one = bc.headroom_per_gpu([g], None if total is None else max(0.0, total - (self.plan.skyrim_gb or 0) * 1024), state, None, background=self.bg_loaded)[0]
                head.append(one)
        else:
            head = bc.headroom_per_gpu(gpus, peak, state, self.svc.hooks.usual_skyrim_mb(), background=self.bg_loaded)
        summary = {"schema_version": bc.SCHEMA_VERSION, "kind": "chat", "levels": self.level_summaries, "sustained": self.sustained, "checks": bchk.summarize([r.to_row() for r in self.records], {p["id"]: p for p in self.testset["prompts"]}), "memory": mem, "headroom": head,
                   "engine_steps": self.extra_steps, "load_time_s": self.load_time_s, "resolved_settings": self.resolved, "highest_lowest": {str(k): v for k, v in self._hl().items()},
                   **tset.version_info(self.testset)}
        bs.write_json(self.folder, bs.SUMMARY, summary)

    def _availability(self) -> dict:
        real = [r for r in self.records if not r.warmup and r.status == bc.STATUS_OK]
        def seen(get):
            return bc.measured(True).to_dict() if any(get(r).ok for r in real) else bc.NOT_REPORTED_AVAILABILITY.to_dict()
        reasons = self.sampler.reasons() if self.sampler else {}
        out = {"ttft_ms": seen(bc.RequestRecord.ttft_ms), "prompt_tok_s": seen(bc.RequestRecord.prompt_tok_s), "generation_tok_s": seen(bc.RequestRecord.generation_tok_s)}
        hl = self._hl()
        for f in bsm.GPU_FIELDS:
            have = any(f in v for k, v in hl.items() if k != "machine")
            why = next((r for k, r in reasons.items() if k.endswith("." + f)), reasons.get("gpus", bsm.REASON_NEEDS_NVIDIA))
            out[f] = bc.measured(True).to_dict() if have else bc.not_measured(why).to_dict()
        for f in bsm.MACHINE_FIELDS:
            out[f] = bc.measured(True).to_dict() if f in hl.get("machine", {}) else bc.not_measured(reasons.get(f"machine.{f}", bsm.REASON_UNREADABLE)).to_dict()
        basis = self.sampler.thermal_basis() if self.sampler else {}
        out["thermal_stop"] = bc.measured(basis).to_dict() if basis and all(v != "none" for v in basis.values()) else bc.not_measured("the card reports no heat limit, so the automatic stop is off").to_dict()
        return out

    async def _finish(self, status: str, reason: str) -> None:
        svc = self.svc
        if self.handle is not None:
            await self._stop_engine()
        try:
            await self._after_unload()
        except Exception:
            pass
        spill = None
        if self.sampler is not None:
            if self.resolved:
                spill = self.sampler.spill(self.resolved.get("layers_on_card"), self.resolved.get("layers_total"))
            await asyncio.to_thread(self.sampler.stop)
        for w in self.writers.values():
            w.close()
        if self.folder is None:
            return
        if self.level_summaries or self.sampler is not None:
            try:
                self._write_summary(final=True)
            except Exception:
                pass
        state, _ = self._skyrim()
        extra = dict(self.manifest_extra, skyrim_state=state, spill=spill, measurement_availability=self._availability() if self.sampler else {}, reasons=self.sampler.reasons() if self.sampler else {},
                     engine_steps=self.extra_steps, load_time_s=self.load_time_s)
        extra.update(svc.hooks.run_facts("end", self) or {})
        extra.update(self._protection_facts())
        if self.svc._thermal and status == bc.RUN_INTERRUPTED:
            reason = "thermal_stop"
        if status == bc.RUN_INTERRUPTED and not reason:
            reason = "stopped"
        bs.finish_run(self.folder, status, reason, svc.wall(), bc.steps_done(self.step_states), self.total, extra)


# --- startup recovery -------------------------------------------------------------------------------------------------------------------------------------------------------

def mark_unfinished_interrupted(base, wall: Callable[[], float] = time.time) -> int:
    """At program start nothing of a previous run can still be running: every run folder still marked `running` becomes `interrupted` (reason `program_stopped`), its data kept."""
    n = 0
    try:
        names = [x for x in os.listdir(base) if bc.valid_run_id(x) and (Path(base) / x).is_dir()]
    except OSError:
        return 0
    for name in names:
        folder = Path(base) / name
        try:
            m = bs.read_manifest(folder)
        except ValueError:
            continue
        if m.get("status") == bc.RUN_RUNNING:
            prog = bs._read_json(folder / bs.PROGRESS) or {}
            bs.finish_run(folder, bc.RUN_INTERRUPTED, "program_stopped", wall(), int(prog.get("steps_done", m.get("steps_done", 0)) or 0), int(m.get("steps_total", 0) or 0))
            n += 1
    return n
