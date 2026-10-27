"""The local decision model, on this device: the glue between the engine manager (`decision_engine.py`) and MultiProxy (2026-10-02).

Everything real lives in `decision_engine.py` (install, run, and passing the question to llama.cpp's own decision API). This file decides where the
files live, keeps the settings, exposes the manager's actions as plain routes (no dashboard screen yet: the next item builds the card on the Local models tab),
and says WHO ANSWERS a decision (`order`), which `multiproxy.py`'s decisions handling follows.

Settings (config.json, key `decisions`): `model` (a key of `decision_engine.MODELS`; "kev-4b" default), `run_on` ("automatic" default, "gpu", "cpu"), `limit_seconds` (0: the default for where it
runs), `answer_first` ("local" default, "typesafe" or "nanogpt": which one answers first when the player has more than one), `autostart` (start it whenever MultiProxy starts) and
`location` (where it runs: "this" default, or the nickname of a Courier device; step 3, see decision_remote.py).
"""
import asyncio
import re
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import decision_engine as de
import decision_providers as dp
import poll_cache
import decision_remote as dr
from decision_engine import Unanswerable
from voice_downloads import DownloadStore

PROVIDER_NAME = "MultiProxy Decisions"   # what the player types into SkyrimNet's custom provider (the card shows and copies it)
API_KEY_WORD = "proxy"                   # SkyrimNet needs something in the API Key box; MultiProxy ignores what it is
LOCAL_NAME = "local/decisions"          # SkyrimNet's model name for "this one, only": any other name leaves it to the order below
BASE_ANSWER_FIRST = ("local", "typesafe", "nanogpt")
# Who answers a decision when the player has not chosen a first one (2026-10-25): the local decision model when it is installed and ready, then TypeSafe, then Perplexity (a
# cheap cloud decision model), then Nano-GPT, then any other registered provider (Ollama's decision models), and last another AI standing in. A step with no key, or no model,
# is left out; the player's own choice in "Answer decisions with" moves one step to the front.
DECISION_ORDER = ("local", "typesafe", "perplexity", "nano")


def service_ids() -> list:
    """Every service that can answer a decision, in the default order: the local model, TypeSafe, Perplexity, Nano-GPT, then any other registered decision provider
    (Ollama's decision models, and later ones)."""
    withask = [p.id for p in dp.REGISTRY.values() if p.ask is not None]
    return [x for x in DECISION_ORDER if x in ("local", "typesafe", "nano") or x in withask] + [i for i in withask if i not in DECISION_ORDER]


def clean_order(saved, answer_first: str = "local") -> list:
    """The saved order of answerers (`decision_order`), tidied: only services that exist, each once, any service that is not in it (a provider added later) at the end. Nothing
    saved yet: the order the code always built (the default, with the player's old "Answer decisions with" pick moved to the front). The old setting is not read any more once
    an order is saved."""
    known = service_ids()
    if isinstance(saved, (list, tuple)) and saved:
        out = [x for x in dict.fromkeys(str(i) for i in saved) if x in known]
        return out + [x for x in known if x not in out]
    lead = {"local": "local", "typesafe": "typesafe", "nanogpt": "nano"}.get(answer_first) or dp.step_of(answer_first)
    seq = list(known)
    if lead in seq:
        seq.remove(lead)
        seq.insert(0, lead)
    return seq

CLOUD_ONLY_WORDS = {"needKey": "Add a key first.", "switched": "Cloud only is on. Decisions now go to {who}.", "refused": "Cloud only is on. No local decision model will start."}


def answer_first_choices() -> tuple:
    """"Answer decisions with" values: the three that were always there, plus one per registered decision provider (decision_providers.py)."""
    return BASE_ANSWER_FIRST + tuple(c for c in dp.choices() if c not in BASE_ANSWER_FIRST)


def skyrimnet_decisions_name(table) -> Optional[str]:
    """The model name SkyrimNet's Decisions job is set to when that is NOT MultiProxy's decision model, else None (also None when SkyrimNet's settings could not be read, or say
    nothing about Decisions: never a guess). MultiProxy's is the model name `local/decisions`, or a provider whose id says "multiproxy-decisions" (the same test the Overview and
    Routing use). A name is only reported when its provider is known and is not MultiProxy's; only the model name and the provider id are read, never anything else from SkyrimNet's files."""
    row = (table or {}).get("decisions") if isinstance(table, dict) else None
    if not isinstance(row, dict):
        return None
    models = [str(m) for m in (row.get("models") or []) if str(m).strip()]
    provs = list(row.get("providers") or []) + [None] * len(models)
    if not models:
        return None
    pairs = list(zip(models, provs))
    for m, pid in pairs:
        if m.strip().lower() == LOCAL_NAME or "multiproxy-decisions" in re.sub(r"[\s_]+", "-", str(pid or "").lower()):
            return None
    if all(pid is None for _, pid in pairs):
        return None                                   # the provider is not said: it may well be MultiProxy's, so say nothing
    return models[0]


RESTART_EVERY = 300.0               # seconds: the soonest MultiProxy starts a stopped local model again by itself (a crash is never retried in a loop)
DEFAULT_SETTINGS = {"model": de.DEFAULT_MODEL, "run_on": "automatic", "limit_seconds": 0, "paid_limit_seconds": 6.0, "ask_when_unsure": True, "sure_percent": 60, "answer_first": "local", "decision_order": None, "autostart": True, "location": "this", "decision_models": {}, "cloud_only": False, "cloud_prev_answer": "", "cloud_switched_to": ""}


def endpoint_address(port: int) -> str:
    """The address SkyrimNet's custom provider takes in its API Endpoint box (API Style: System One). The Quick decisions card and Settings > SkyrimNet connection both show this one."""
    return f"http://127.0.0.1:{port}/v1/systemone"


class DecisionHandles:
    def __init__(self, engine: de.DecisionEngine, settings_fn: Callable[[], dict], logger, remote_engine: Optional[dr.RemoteDecisionEngine] = None,
                 remote: Optional[dr.DecisionRemote] = None):
        self.engine, self._settings_fn, self.log = engine, settings_fn, logger
        self.remote_engine, self.remote = remote_engine, remote
        self._told = ""
        self.last_reason = ""              # why the last try_local gave nothing: "too_slow" | "not_running" | "other" | ""
        self.last_answer: Optional[dict] = None
        self.stopped_by_player = False     # Stop was pressed this session: it stays stopped until Start (or a new MultiProxy session)
        self.last_auto_start = 0.0         # when MultiProxy last started it by itself (at launch, or after a crash / a stop it did not ask for): at most one try per RESTART_EVERY
        self.typesafe_line = ""            # the same for TypeSafe, when it was too slow (it has no other line: its own errors go to SkyrimNet unchanged)
        self.nano_line = ""                # the plain line for the card when Nano-GPT said no to the last decision it was asked ("" when it answered or was not asked)
        self.provider_lines: dict = {}     # the same for the other paid decision providers (Perplexity), by provider id

    def settings(self) -> dict:
        return self._settings_fn()

    def _time_cap(self) -> Optional[float]:
        """The seconds in "...or the answer is taking longer than N seconds, try the next model down", or None while that box is unticked (no hand-over by time)."""
        s = self.settings()
        if not s.get("ask_when_unsure", True):
            return None
        try:
            return float(s.get("paid_limit_seconds") or 6.0)
        except (TypeError, ValueError):
            return 6.0

    def cloud_only(self) -> bool:
        """"Cloud only: never start a local decision model" (2026-10-26): no device starts one, and the local step is left out of the order."""
        return bool(self.settings().get("cloud_only"))

    def cloud_choice(self, has_typesafe_key: bool, has_nano_key: bool = False) -> Optional[str]:
        """The first "Answer decisions with" value that is a cloud key able to answer now (TypeSafe, Perplexity, Nano-GPT, then any other key), None when there is no key."""
        seq = [x for x in DECISION_ORDER if x != "local"] + [p.id for p in dp.REGISTRY.values() if p.id not in DECISION_ORDER]
        for step in seq:
            if step == "typesafe" and has_typesafe_key:
                return "typesafe"
            if step == "nano" and has_nano_key:
                return "nanogpt"
            p = dp.REGISTRY.get(step)
            if p is not None and p.ask is not None and not p.local and p.ready():
                return p.choice
        return None

    def location(self) -> str:
        loc = str(self.settings().get("location") or "this")
        return loc if loc != "this" and self.remote_engine is not None else "this"

    def active(self):
        """The engine decisions go to: the one on this device, or the relay to the Courier device chosen in `location`."""
        return self.engine if self.location() == "this" else self.remote_engine

    def order(self, model_name: Optional[str], has_typesafe_key: bool, has_nano_key: bool = False) -> Optional[list]:
        """Who answers a decision, in order: "local", "typesafe", "nano" (Jev through Nano-GPT), a registered decision provider's own step ("ollama", see
        decision_providers.py), "standin" (another AI standing in). None means "as before": the local model isn't installed and no provider reports a decision model, so
        nothing about the old handling changes. SkyrimNet's `typesafe/...` model names don't matter; `local/decisions` means the local model and nothing else. The
        player's choice goes first and the local model comes next, then the other paid one."""
        cloud = self.cloud_only()
        if str(model_name or "").strip().lower().startswith("local/") and not cloud:
            return ["local"]
        local_ok = False if cloud else self.active().installed()
        extra = [p.id for p in dp.REGISTRY.values() if p.ask is not None and p.ready() and not (cloud and p.local)]
        if not local_ok and not extra:
            return None
        seq = self.settings()["decision_order"]          # the player's own order (the list on the Quick decisions card), first to last
        have = {"local": local_ok, "typesafe": has_typesafe_key, "nano": has_nano_key}
        have.update({i: True for i in extra})
        return [x for x in seq if have.get(x)] + ["standin"]

    def nano_active(self, has_typesafe_key: bool, has_nano_key: bool) -> bool:
        """Is Nano-GPT the one answering decisions first right now (so the card can say it charges)? Not the local model, and not TypeSafe ahead of it."""
        if not has_nano_key:
            return False
        order = self.order(None, has_typesafe_key, has_nano_key)
        if order is None:
            return not has_typesafe_key
        return order[0] == "nano"

    def note_answer(self, by: str, first: str, miss: str = "") -> None:
        """Remember who answered the last decision (for the card): `by` the answerer's name ("local" for the local model), `first` which of local / typesafe was
        the first choice, `miss` why the one before it didn't answer ("too_slow" | "not_running" | "other" | "typesafe" | ""). The card says so only when the answer
        did not come from the first choice."""
        self.last_answer = {"by": by, "first": first, "miss": miss, "at": time.time()}

    def _start_in_background(self, request_id: str) -> None:
        """A decision found the local model stopped. When it is installed, set to run on this device, "start with MultiProxy" is on, and the player did not stop it, start it now
        in the background (this decision goes to the next answerer; nothing waits). Covers a crash too, at most once per RESTART_EVERY seconds, never in a loop."""
        e = self.engine
        s = self.settings()
        if (s.get("cloud_only") or self.stopped_by_player or not s.get("autostart") or s.get("location", "this") != "this" or not e.installed() or e.state not in ("stopped", "died", "error")
                or time.time() - self.last_auto_start < RESTART_EVERY):
            return
        self.last_auto_start = time.time()
        self.log.info(f"[{request_id}] The local decision model was stopped, so MultiProxy is starting it again (this decision goes to the next answerer)")

        async def go() -> None:
            try:
                await e.start()
            except de.DecisionError as ex:
                self.log.warning(f"The local decision model didn't start again: {ex.message}")
        try:
            asyncio.get_running_loop().create_task(go())
        except RuntimeError:
            pass

    async def try_local(self, state, questions: dict, request_id: str, via: str = "", received: Optional[float] = None) -> Optional[dict]:
        """The local model's answer ({"answers", "usage"}), or None when it can't give one right now (stopped, too slow, a question it can't answer honestly): the
        next answerer takes over. Says why in the log, in plain words. On another device the waiting limit is counted from `received` (when MultiProxy got the decision)."""
        e = self.active()
        remote = e is not self.engine
        where = f" on {e.pc}" if remote else ""
        self.last_reason = ""
        if self.cloud_only():
            return None
        try:
            if remote:
                return await e.answer(state, questions, received=received)
            cap = self._time_cap()
            if cap is not None and hasattr(e, "limit_seconds") and cap < e.limit_seconds():            # the sentence's seconds are shorter than the model's own waiting limit: the shorter one counts
                return await e.answer(state, questions, limit=cap)
            return await e.answer(state, questions)
        except de.DecisionTooSlow as ex:
            self.last_reason = "too_slow"
            self.log.info(f"[{request_id}] The local decision model{where} didn't answer within {ex.limit:g} s, passing the decision on ({e.limit_hits} so far)")
        except de.DecisionNotReady as ex:
            self.last_reason = "not_running"
            if not remote:
                self._start_in_background(request_id)
            msg = ex.message if (e.state != "stopped" or remote) else "The local decision model is stopped"
            if msg != self._told:                     # a stopped model would otherwise say so on every decision
                self._told = msg
                self.log.info(f"[{request_id}] {msg}, so the next answerer takes the decision")
        except Unanswerable as ex:
            self.last_reason = "other"
            self.log.info(f"[{request_id}] The local decision model{where} can't answer this one honestly ({ex.reason}), passing it on")
        except de.DecisionError as ex:
            self.last_reason = "other"
            self.log.warning(f"[{request_id}] The local decision model{where} failed ({ex.message}), passing the decision on")
        return None


def setup(app: FastAPI, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], settings_dir: Callable[[], str], redact: Callable[[str], str], logger,
          local_svc, store: DownloadStore, engine: Optional[de.DecisionEngine] = None, main_port: Callable[[], int] = lambda: 8000,
          has_typesafe_key: Callable[[], bool] = lambda: False, has_nano_key: Callable[[], bool] = lambda: False, remotes=None, this_pc: Callable[[], str] = lambda: "this device", source_factory=None,
          gpu_choice_fn: Callable[[], Optional[dict]] = lambda: None, cards_fn: Optional[Callable[[], list]] = None,
          skyrimnet_table: Callable[[], dict] = lambda: {}) -> DecisionHandles:
    """`local_svc` is MultiProxy's `LocalModels` (it owns the llama.cpp engine this one uses). `store` is the shared kept-downloads store. `remotes` is the list of other devices
    (the Courier ones can run the decision model: `location`); `source_factory(spec)` says where a file this device doesn't have yet comes from (tests hand in a folder)."""

    def settings() -> dict:
        s = dict(DEFAULT_SETTINGS)
        raw = load_cfg().get("decisions") or {}
        if isinstance(raw, dict):
            s.update({k: raw[k] for k in DEFAULT_SETTINGS if k in raw})
        if s["model"] not in de.MODELS:
            s["model"] = de.DEFAULT_MODEL
        if s["run_on"] not in de.RUN_ON_CHOICES:
            s["run_on"] = "automatic"
        if s["answer_first"] not in answer_first_choices():
            s["answer_first"] = "local"
        s["decision_order"] = clean_order(s["decision_order"], s["answer_first"])
        picks = s["decision_models"] if isinstance(s["decision_models"], dict) else {}
        s["decision_models"] = {str(k): str(v) for k, v in picks.items() if isinstance(v, str)}
        s["location"] = str(s["location"] or "this")
        s["ask_when_unsure"] = bool(s["ask_when_unsure"])
        s["cloud_only"] = bool(s["cloud_only"])
        try:
            s["sure_percent"] = int(min(max(float(s["sure_percent"]), 10), 95))
        except (TypeError, ValueError):
            s["sure_percent"] = 60
        try:
            s["paid_limit_seconds"] = min(max(float(s["paid_limit_seconds"]), 2.0), 9.0)
        except (TypeError, ValueError):
            s["paid_limit_seconds"] = 6.0
        return s

    def folder() -> Path:
        base = local_svc.folder if local_svc is not None and local_svc.folder else Path(settings_dir())
        return Path(base) / "decisions"

    async def ensure_engine(on_progress) -> Path:
        """The llama.cpp engine "Local models" downloads: used as it is when it is there and new enough for llama.cpp's own decision API; fetched (one time, the same
        kind as the one it has) when it isn't there or is older. The new one then serves Local models' chat models too."""
        exe = local_svc.engine_exe_current()
        if exe:
            return exe
        if not local_svc.folder:
            default = Path(settings_dir()) / "local-models"
            local_svc.set_folder(str(default))
            logger.info(f"Local models had no folder yet, so the llama.cpp engine goes into {default}")
        job = local_svc.download_engine(engine_kind() or await local_svc.default_engine_kind())
        while job.state == "running":
            on_progress(job.done, job.total)
            await asyncio.sleep(0.5)
        exe = local_svc.engine_exe_current()
        if job.state != "done" or not exe:
            raise de.DecisionError(502, (job.error or "The engine software didn't download.").replace("Press Download to", "Press Install to"))
        return exe

    def engine_kind() -> str:
        return str((local_svc._cfg().get("engine") or {}).get("kind") or "") if local_svc is not None else ""

    engine = engine or de.DecisionEngine(folder, store, settings, lambda: local_svc.engine_exe_current(), ensure_engine, engine_kind, redact=redact,
                                          gpu_choice_fn=gpu_choice_fn, **({"cards_fn": cards_fn} if cards_fn else {}))
    engine.cleanup_leftover()
    remote_engine = dr.RemoteDecisionEngine(remotes, lambda: settings()["location"] if settings()["location"] != "this" else "", settings, log=lambda m: logger.warning(m)) if remotes is not None else None
    remote = dr.DecisionRemote(remotes, engine, store, this_pc, logger.info, source_factory, settings, local=local_svc) if remotes is not None else None
    handles = DecisionHandles(engine, settings, logger, remote_engine, remote)

    def err(e: Exception):
        status = getattr(e, "status", 500)
        return JSONResponse({"detail": getattr(e, "message", str(e))}, status_code=status if isinstance(status, int) and 400 <= status < 600 else 500)

    def courier_pcs() -> list:
        return [r for r in (remotes.all() if remotes is not None else []) if r.kind == "courier"]

    def need_remote() -> dr.DecisionRemote:
        if remote is None:
            raise de.DecisionError(404, "Other devices aren't available here.")
        return remote

    def where() -> str:
        return handles.location()

    async def body_of(request: Request) -> dict:
        try:
            d = await request.json()
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    def pick_pc(name) -> str:
        """The device an action is for: "this", or a Courier device by nickname. Nothing named: wherever the decision model runs now."""
        name = str(name or "").strip() or where()
        if name.lower() == "this":
            return "this"
        match = next((r for r in courier_pcs() if r.id.lower() == name.lower()), None)
        if match is None:
            raise de.DecisionError(404, "That device doesn't have the MultiProxy Courier installed.")
        return match.id

    def read_card() -> dict:
        """Ask the graphics driver and the system for this device's card and memory (starts a program: only ever called on a worker thread)."""
        gpu, ram = None, None
        try:
            g = engine.hardware().gpu
            gpu = g.to_json() if g else None
        except Exception:
            pass
        try:
            import courier_system
            m = courier_system.read_pc_memory()
            ram = round(m["total_mb"] / 1024, 1) if m and m.get("total_mb") else None
        except Exception:
            pass
        return {"name": this_pc(), "gpu": gpu, "ramGb": ram}

    # This device's card and memory for the "Run it on" list: read at most every 30 seconds, never on the event loop, and never waited for by a
    # poll after the first one (a stale card is shown while a worker thread reads it again).
    card = poll_cache.SlowValue(read_card, 30.0)

    def this_pc_card() -> dict:
        return card.get()

    def skyrimnet_name(pcs: list) -> Optional[str]:
        """The name SkyrimNet's Decisions job is set to, only when MultiProxy's decisions are set up (a decision model installed on any device, or a key that can answer) and that name is not
        the one MultiProxy answers to. None otherwise: the player may not use MultiProxy for decisions at all."""
        try:
            set_up = handles.engine.installed() or any((p.get("decisions") or {}).get("installed") for p in pcs) or handles.cloud_choice(bool(has_typesafe_key()), bool(has_nano_key())) is not None
            return skyrimnet_decisions_name(skyrimnet_table()) if set_up else None
        except Exception:
            return None

    def full_status() -> dict:
        s = settings()
        loc = where()
        models = [{"key": m.key, "label": m.label, "blurb": m.blurb, "graphicsBytes": m.graphics_bytes, "diskBytes": engine.store.missing_bytes(list(m.files()))} for m in engine.models.values()]        # what is still to download: a kept model needs no more room
        shown = engine.status() if loc == "this" else remote_engine.status()
        pcs = []
        for r in courier_pcs():
            rep = r.report or {}
            mem = (rep.get("pc_memory") or {}).get("total_mb")
            pcs.append({"id": r.id, "name": r.label, "online": r.online is not False, "gpu": r.gpu, "ramGb": round(mem / 1024, 1) if mem else None, "decisions": rep.get("decisions"),
                        "hasDecisions": "decisions" in rep, "autostart": pc_autostart(r.id), "job": remote.jobs[r.id].to_json() if remote and r.id in remote.jobs else None})
        return {**shown, "settings": s, "localName": LOCAL_NAME, "address": endpoint_address(main_port()), "models": models,
                "typesafeKey": bool(has_typesafe_key()), "nanoKey": bool(has_nano_key()), "lastAnswer": handles.last_answer, "providers": provider_cards(),
                "answerOrder": handles.order(None, bool(has_typesafe_key()), bool(has_nano_key())),
                "providerLines": [handles.provider_lines[p.id] for p in dp.REGISTRY.values() if p.ask is not None and p.ready() and handles.provider_lines.get(p.id)],
                "answerLines": {**{p.id: handles.provider_lines[p.id] for p in dp.REGISTRY.values() if p.ask is not None and p.ready() and handles.provider_lines.get(p.id)},
                                **({"nano": handles.nano_line} if has_nano_key() and handles.nano_line else {}), **({"typesafe": handles.typesafe_line} if has_typesafe_key() and handles.typesafe_line else {})},
                "nanoLine": handles.nano_line if has_nano_key() else "", "typesafeLine": handles.typesafe_line if has_typesafe_key() else "", "nanoActive": handles.nano_active(bool(has_typesafe_key()), bool(has_nano_key())),
                "providerName": PROVIDER_NAME, "apiKeyWord": API_KEY_WORD, "downloads": store.listing(), "location": loc, "pcs": pcs, "thisPc": this_pc_card(),
                "thisEngine": engine.status() if loc != "this" else shown, "cloudKey": handles.cloud_choice(bool(has_typesafe_key()), bool(has_nano_key())) is not None,
                "cloudOnlyWords": CLOUD_ONLY_WORDS, "skyrimnetName": skyrimnet_name(pcs)}

    def provider_cards() -> list:
        """One entry per registered decision provider for the card's "Answer decisions with" list: its label, whether it can answer now, and its models with the one in use."""
        saved = settings()["decision_models"]
        return [{"id": p.id, "choice": p.choice, "label": p.label, "ready": bool(p.ready()), "keyOk": bool(p.key_ok()), "models": list(p.models()), "model": dp.chosen_model(p, saved), "offReason": p.off_reason, "labels": dict(p.model_labels or {}), "local": bool(p.local)}
                for p in dp.REGISTRY.values()]

    async def move_to(target: str) -> tuple:
        """Decisions now run on `target` ("this" or a Courier device): save it, then STOP the copy on the device they came from (it would otherwise keep holding graphics memory for
        nothing), and start the one on this device when they come back. Returns (a short warning when the old copy could not be stopped, else "", the device they came from).
        Only the engine MultiProxy itself started is stopped (this device: by the process it recorded; another device: its own Courier stops it)."""
        old = where()
        cfg = load_cfg()
        cur = dict(cfg.get("decisions") or {})
        cur["location"] = target
        cfg["decisions"] = cur
        save_cfg(cfg)
        if old == target:
            return "", old
        warn = ""
        try:
            if old == "this":
                await engine.stop()
            else:
                stopped_pcs.add(old)
                await need_remote().act(old, "stop")
        except Exception as e:
            logger.warning(f"Couldn't stop the decision model on {old} after moving decisions to {target}: {getattr(e, 'message', e)}")
            warn = f"Couldn't stop the model on {'this PC' if old == 'this' else old}. Please stop it manually."
        if target == "this":
            handles.stopped_by_player = False
            stopped_pcs.discard(old)
            if engine.installed() and engine.state in ("stopped", "died", "error"):
                try:
                    asyncio.get_running_loop().create_task(engine.start())
                except RuntimeError:
                    pass
        return warn, old

    def when_installed(job, pc: str) -> None:
        """When an install on another device finishes well, the decision model now runs there (decisions go to it; SkyrimNet's address doesn't change)."""
        def done(_t) -> None:
            if job.state != "done":
                return
            asyncio.ensure_future(move_to(pc))
        if job is not None and getattr(job, "task", None) is not None:
            job.task.add_done_callback(done)

    @app.get("/config/decisions/status")
    async def decisions_status():
        # The graphics-card read behind "Run it on" starts a program (190-880 ms, every 30 s): done on a worker thread, so the page's
        # 2 s poll never freezes the server nor waits for it.
        await card.ensure()
        await dp.refresh(wait=False)
        return full_status()

    @app.get("/config/decisions/fit")
    async def decisions_fit(pc: str = ""):
        """Can that device run it, and where it would run: {verdict, reasons, warnings, needDiskBytes, runOn, runOnReason, graphics}. Never a refusal over memory.
        `pc`: another device (its own check, asked of it); nothing or "this": this device."""
        try:
            target = pick_pc(pc) if pc else "this"
            if target == "this":
                return await asyncio.to_thread(engine.fit_check)
            info = await need_remote().describe(target)
            if info.get("fit") is None:
                raise de.DecisionError(502, info.get("fitError") or f"{target} didn't answer.")
            return info["fit"]["fit"]
        except Exception as e:
            return err(e)

    async def recommended_model(target: str) -> str:
        """The model "Set it up" installs on a device (the Decisions tab's one click): Kev-4B, or Laya when that device's graphics card is known and under 4 GB.
        The model is one saved choice for wherever it runs, so a model the device in use already has working is kept (switching it would turn that copy into "not installed")."""
        if target == "this":
            fit = await asyncio.to_thread(engine.fit_check)
        else:
            info = await need_remote().describe(target)
            fit = (info.get("fit") or {}).get("fit") or {}
        total_mb = (fit.get("graphics") or {}).get("totalMb")
        pick = "laya" if total_mb and total_mb < 4 * 1024 else de.DEFAULT_MODEL
        saved = settings()["model"]
        if saved != pick:
            in_use = engine.installed(saved) if where() == "this" else saved in (remote_engine.block().get("installedModels") or [])
            if in_use:
                return saved
        return pick

    @app.post("/config/decisions/install")
    async def decisions_install(request: Request):
        """Install on this device, or on another: {"pc": "<PC>", "source": "gaming-pc" (default) | "that-pc", "recommended": true (the one-click Set it up: the model is picked here)}.
        When an install finishes well (the model is running), the decision model moves to that device; a failed or cancelled install changes nothing."""
        data = await body_of(request)
        try:
            if handles.cloud_only():
                raise de.DecisionError(409, CLOUD_ONLY_WORDS["refused"])
            target = pick_pc(data.get("pc"))
            runner_only = bool(data.get("runnerOnly"))          # "Get the runner": only the runner software, the model's files stay as they are
            if data.get("recommended") and not runner_only:
                pick = await recommended_model(target)
                if pick != settings()["model"]:
                    cfg = load_cfg()
                    cur = dict(cfg.get("decisions") or {})
                    cur["model"] = pick
                    cfg["decisions"] = cur
                    save_cfg(cfg)
            if data.get("model") in engine.models and not runner_only and not data.get("recommended") and data["model"] != settings()["model"]:      # the Models tab's Add for a kept model
                cfg = load_cfg()
                cur = dict(cfg.get("decisions") or {})
                cur["model"] = data["model"]
                cfg["decisions"] = cur
                save_cfg(cfg)
            if target == "this":
                job = engine.start_runner_install() if runner_only else engine.start_install()
            else:
                job = need_remote().start_install(target, str(data.get("source") or "gaming-pc"), runner_only=runner_only)
            if not runner_only:
                when_installed(job, target)
        except de.DecisionError as e:
            return err(e)
        except Exception as e:
            return err(e)
        return full_status()

    @app.post("/config/decisions/pause")
    async def decisions_pause(request: Request):
        """Pause an install: it stops now and keeps what it fetched. On this device it is remembered across a restart; on another device it is a stop that keeps the
        partial files (the screen shows Paused, and Resume is the ordinary install call, which carries on from there)."""
        data = await body_of(request)
        try:
            target = pick_pc(data.get("pc"))
            return {"paused": engine.pause_install() if target == "this" else need_remote().cancel(target)}
        except Exception as e:
            return err(e)

    @app.post("/config/decisions/cancel")
    async def decisions_cancel(request: Request):
        data = await body_of(request)
        try:
            target = pick_pc(data.get("pc"))
            return {"cancelled": engine.cancel_install() if target == "this" else need_remote().cancel(target)}
        except Exception as e:
            return err(e)

    async def act(action: str, request: Request):
        data = await body_of(request)
        try:
            if action in ("start", "restart") and handles.cloud_only():
                raise de.DecisionError(409, CLOUD_ONLY_WORDS["refused"])
            target = pick_pc(data.get("pc"))
            if target == "this":
                if action == "start":
                    handles.stopped_by_player = False
                    await handles.engine.start()
                elif action == "stop":
                    handles.stopped_by_player = True            # a Stop stays stopped until Start (or until MultiProxy starts again)
                    await handles.engine.stop(by_button=True)
                elif action == "restart":
                    handles.stopped_by_player = False
                    await handles.engine.restart()
            else:
                if action == "stop":
                    stopped_pcs.add(target)
                elif action in ("start", "restart"):
                    stopped_pcs.discard(target)
                await need_remote().act(target, action)
        except Exception as e:
            return err(e)
        return full_status()

    @app.post("/config/decisions/start")
    async def decisions_start(request: Request):
        return await act("start", request)

    @app.post("/config/decisions/stop")
    async def decisions_stop(request: Request):
        return await act("stop", request)

    @app.post("/config/decisions/restart")
    async def decisions_restart(request: Request):
        return await act("restart", request)

    @app.post("/config/decisions/uninstall")
    async def decisions_uninstall(request: Request):
        data = await body_of(request)
        try:
            target = pick_pc(data.get("pc"))
            gone = [f.name for k in engine.models if engine.installed(k) for f in engine.spec(k).files()] if target == "this" else []
            freed = await engine.uninstall() if target == "this" else 0
            if gone and data.get("deleteFiles") is True:        # "Remove, delete files": this model's kept downloads go too (nothing else's)
                freed += await asyncio.to_thread(engine.store.clear, gone)
            if target != "this":
                await need_remote().act(target, "uninstall")
                if where() == target:                          # it is gone from there: decisions go back to being answered here / by the others
                    cfg = load_cfg()
                    cur = dict(cfg.get("decisions") or {})
                    cur["location"] = "this"
                    cfg["decisions"] = cur
                    save_cfg(cfg)
        except Exception as e:
            return err(e)
        return {"freedBytes": freed, **full_status()}

    @app.get("/config/decisions/log")
    async def decisions_log(pc: str = ""):
        try:
            target = pick_pc(pc)
            return {"log": engine.log_tail(200) if target == "this" else await need_remote().engine_log(target)}
        except Exception as e:
            return err(e)

    @app.post("/config/decisions/location")
    async def decisions_location(request: Request):
        """Move the decision model to another device (or back): {"pc": "this"} or {"pc": "<nickname>"}. SkyrimNet's address doesn't change."""
        data = await body_of(request)
        try:
            if handles.cloud_only():
                raise de.DecisionError(409, CLOUD_ONLY_WORDS["refused"])
            target = pick_pc(data.get("pc") or "this")
        except Exception as e:
            return err(e)
        warn, old = await move_to(target)
        return {**full_status(), "moveWarning": warn, "moveWarningPc": old}

    @app.post("/config/decisions/settings")
    async def decisions_settings(request: Request):
        """{"model": a key of decision_engine.MODELS, "runOn": "automatic"|"gpu"|"cpu", "limitSeconds": 0|number, "answerFirst": "local"|"typesafe"|"nanogpt", "autostart": bool}. A new model or
        place takes effect the next time it starts (`restartNeeded`). They are the player's choices for wherever it runs: another device is told at once, and makes its
        own decision from them."""
        data = await request.json()
        cur = settings()
        new = dict(cur)
        for key, cfg_key, ok in (("model", "model", lambda v: v in de.MODELS), ("runOn", "run_on", lambda v: v in de.RUN_ON_CHOICES),
                                 ("answerFirst", "answer_first", lambda v: v in answer_first_choices())):
            if key in data:
                if not ok(data[key]):
                    return JSONResponse({"detail": f"That isn't a choice for {key}."}, status_code=400)
                new[cfg_key] = data[key]
        if "limitSeconds" in data:
            try:
                v = float(data["limitSeconds"] or 0)
            except (TypeError, ValueError):
                return JSONResponse({"detail": "Enter a number of seconds."}, status_code=400)
            if v < 0 or (0 < v < de.ASSUMED_LIMIT_FLOOR):
                return JSONResponse({"detail": f"Enter {de.ASSUMED_LIMIT_FLOOR:g} seconds or more, or 0 for the default."}, status_code=400)
            new["limit_seconds"] = v
        if "paidLimitSeconds" in data:
            try:
                v = float(data["paidLimitSeconds"])
            except (TypeError, ValueError):
                return JSONResponse({"detail": "Enter a number of seconds from 2 to 9."}, status_code=400)
            if not 2.0 <= v <= 9.0:
                return JSONResponse({"detail": "Enter a number of seconds from 2 to 9."}, status_code=400)
            new["paid_limit_seconds"] = v
        if "decisionOrder" in data:         # the list on the Quick decisions card, dragged into the player's own order
            wanted = data["decisionOrder"]
            if not isinstance(wanted, list) or not wanted or any(str(x) not in service_ids() for x in wanted):
                return JSONResponse({"detail": "That isn't a list of the services that answer decisions."}, status_code=400)
            new["decision_order"] = clean_order(wanted, cur["answer_first"])
        if "askWhenUnsure" in data:
            new["ask_when_unsure"] = bool(data["askWhenUnsure"])
        if "surePercent" in data:
            try:
                v = float(data["surePercent"])
            except (TypeError, ValueError):
                return JSONResponse({"detail": "Please type a number from 10 to 95."}, status_code=400)
            if not 10 <= v <= 95:
                return JSONResponse({"detail": "Please type a number from 10 to 95."}, status_code=400)
            new["sure_percent"] = int(round(v))
        if "decisionModel" in data:
            pick = data["decisionModel"] if isinstance(data["decisionModel"], dict) else {}
            prov = dp.REGISTRY.get(str(pick.get("provider") or ""))
            if prov is None or str(pick.get("model") or "") not in prov.models():
                return JSONResponse({"detail": "That device doesn't have this decision model."}, status_code=400)
            new["decision_models"] = {**cur.get("decision_models", {}), prov.id: str(pick["model"])}
        if "autostart" in data:
            new["autostart"] = bool(data["autostart"])
        turned_on, switched = False, ""
        if "cloudOnly" in data:
            want = bool(data["cloudOnly"])
            if want and not cur["cloud_only"]:
                pick = handles.cloud_choice(bool(has_typesafe_key()), bool(has_nano_key()))
                if pick is None:
                    return JSONResponse({"detail": CLOUD_ONLY_WORDS["needKey"]}, status_code=400)
                turned_on = True
                is_key = lambda c: (c == "typesafe" and has_typesafe_key()) or (c == "nanogpt" and has_nano_key()) or (dp.by_choice(c) is not None and not dp.by_choice(c).local and dp.by_choice(c).ready())
                if not is_key(new["answer_first"]):                 # the choice was the local model (or a key that cannot answer): the first key that can takes over, and the card says so once
                    new["cloud_prev_answer"], new["cloud_switched_to"] = new["answer_first"], pick        # remembered, so unticking puts the old choice back
                    new["answer_first"] = pick
                    who = {"typesafe": "My TypeSafe key", "nanogpt": "My Nano-GPT key"}.get(pick) or dp.by_choice(pick).label
                    switched = CLOUD_ONLY_WORDS["switched"].format(who=("your " + who[3:]) if who.startswith("My ") else who)
            elif not want and cur["cloud_only"]:
                if cur["cloud_prev_answer"] and cur["answer_first"] == cur["cloud_switched_to"] and "answerFirst" not in data:
                    new["answer_first"] = cur["cloud_prev_answer"]          # unticked: the choice the player had before comes back (unless they have chosen something since)
                new["cloud_prev_answer"] = new["cloud_switched_to"] = ""
            new["cloud_only"] = want
        ap = data.get("autostartPc")
        if isinstance(ap, dict) and str(ap.get("pc") or "").strip() not in ("", "this"):       # the same choice for the model on another device: kept here, per device, not on the Courier
            try:
                apc = pick_pc(ap.get("pc"))
            except de.DecisionError as e:
                return err(e)
            cfg0 = load_cfg()
            raw = dict(cfg0.get("decision_autostart_pcs") if isinstance(cfg0.get("decision_autostart_pcs"), dict) else {})
            if ap.get("on"):
                raw.pop(apc, None)                     # on is the default: nothing is written
            else:
                raw[apc] = False
            if raw:
                cfg0["decision_autostart_pcs"] = raw
            else:
                cfg0.pop("decision_autostart_pcs", None)
            save_cfg(cfg0)
        cfg = load_cfg()
        stored = dict(cfg.get("decisions") or {}) if isinstance(cfg.get("decisions"), dict) else {}
        stored.update({k: v for k, v in new.items() if v != cur.get(k)})        # only what the player changed is written: a setting never touched keeps reading as its default
        cfg["decisions"] = stored
        save_cfg(cfg)
        loc = where()
        if loc != "this":
            try:
                await need_remote().push_settings(loc)
            except Exception as e:
                logger.info(f"The decision model's settings couldn't be sent to {loc} just now ({getattr(e, 'message', e.__class__.__name__)}); they go with the next start")
        if turned_on:                                  # whatever runs a decision engine anywhere is shut down with the Shut down button's own calls (only engines MultiProxy started: each by the process it recorded, or by its own Courier)
            try:
                await handles.engine.stop()
            except Exception as e:
                logger.warning(f"Cloud only: couldn't stop the decision model on this PC: {getattr(e, 'message', e)}")
            for r in courier_pcs():
                d = (r.report or {}).get("decisions") or {}
                if r.online is not False and d.get("state") in ("ready", "starting", "getting_ready"):
                    stopped_pcs.add(r.id)
                    try:
                        await need_remote().act(r.id, "stop")
                    except Exception as e:
                        logger.warning(f"Cloud only: couldn't stop the decision model on {r.label}: {getattr(e, 'message', e)}")
            logger.info("Cloud only is on: no local decision model will be started")
        running = engine.state in ("ready", "starting", "getting_ready") if loc == "this" else remote_engine.state in ("ready", "starting", "getting_ready")
        restart = running and (new["model"] != cur["model"] or new["run_on"] != cur["run_on"])
        return {"settings": settings(), "restartNeeded": restart, "cloudOnlySwitched": switched}

    stopped_pcs: set = set()          # devices whose decision model the player stopped since MultiProxy started

    def pc_autostart(pc: str) -> bool:
        raw = load_cfg().get("decision_autostart_pcs")
        v = raw.get(pc) if isinstance(raw, dict) else None
        return True if v is None else bool(v)          # on unless the player switched it off for that device

    async def autostart_pc(r) -> None:
        """A device came online: start the decision model there when the player asked for that, it is installed, it is the one in use and it is not already running or starting.
        Once per coming-online; the player's own Stop is respected."""
        d = (r.report or {}).get("decisions") or {}
        if (handles.cloud_only() or not pc_autostart(r.id) or str(where()).lower() != r.id.lower() or r.id in stopped_pcs or not d.get("installed")
                or d.get("state") in ("ready", "starting", "getting_ready", "installing")):
            return
        logger.info(f"Starting the decision model on {r.label} automatically")
        try:
            await need_remote().act(r.id, "start")
        except Exception as e:
            logger.warning(f"The decision model didn't start on {r.label} by itself: {getattr(e, 'message', e.__class__.__name__)}")

    def on_online(r) -> None:
        if r.kind != "courier":
            return
        try:
            asyncio.get_running_loop().create_task(autostart_pc(r))
        except RuntimeError:
            pass
    if remotes is not None:
        remotes.online_hooks.append(on_online)

    async def autostart() -> None:
        if settings()["autostart"] and not settings()["cloud_only"] and where() == "this" and handles.engine.installed():
            handles.last_auto_start = time.time()
            try:
                await handles.engine.start()
            except de.DecisionError as e:
                logger.warning(f"The local decision model didn't start with MultiProxy: {e.message}")
    handles.autostart = autostart
    return handles
