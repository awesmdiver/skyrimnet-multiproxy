"""The Benchmarks tab's own routes (what the page needs besides the runner's): the model list, the saved setup, the results, one run's detail, saving files, "Add to MultiProxy".

  GET  /config/benchmarks/tab                     the model list (with download progress), the Skyrim figure, the graphics cards, the saved setup, the defaults of the Advanced list
  POST /config/benchmarks/setup                   save the ticks and the setup the page holds (a small file next to the results)
  POST /config/benchmarks/add-file                the native file picker (`.gguf` only), parented to the app window; the file is used where it sits and never deleted
  POST /config/benchmarks/remove-file             take a local file out of the list (the file itself is never touched)
  POST /config/benchmarks/hide                    take a community model out of the list
  POST /config/benchmarks/download                download the model files of the ticked models that are not on this PC, one after another (only the model file, never a second copy),
                                                  and, when `then` carries a start body, test every ticked model back to back when the downloads are done
  POST /config/benchmarks/download/cancel         stop the downloads (and the start that was waiting for them); `id` also cancels that one download
  GET  /config/benchmarks/downloads               where the download queue is
  GET  /config/benchmarks/card-usage              who is using the graphics card right now (this PC); `fresh=1` reads it again, else the last reading is returned
  GET  /config/benchmarks/results                 one row per run, from the result files
  GET  /config/benchmarks/runs/<id>/detail        everything one run shows
  GET  /config/benchmarks/runs/<id>/plan          a start body that repeats that run ("Test again")
  POST /config/benchmarks/runs/<id>/save          the native Save window, then the requests (csv) or the full results (json) are written where the person chose
  POST /config/benchmarks/save-results            the same for the results table
  POST /config/benchmarks/add-to-multiproxy       a tested local file goes onto the Models tab (the existing add); it never changes a route

The model list comes from `benchmarks/community-models.json` (a short curated list that can change without a new MultiProxy: each entry names a model MultiProxy's Local device tab already
knows, by its `id`, so the existing download is what downloads it) plus the local files the person added.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import benchmarks_cardusage as bcu
import benchmarks_contract as bc
import benchmarks_estimate as be
import benchmarks_results as br_
import benchmarks_runner as brun
import benchmarks_sampler as bsm
import benchmarks_store as bs
import benchmarks_testset as tset
import safe_paths

COMMUNITY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmarks", "community-models.json")
SETUP_FILE = "setup.json"
MAX_SETUP_BYTES = 20000
GGUF_TYPES = [("Model files", "*.gguf")]
ADVANCED_DEFAULTS = {"ctx": None, "layers": None, "flashAttn": None, "batch": None, "threads": None, "temperature": 0.0, "cacheType": None}

DL_NOTHING = "Pick a model to download."
DL_BUSY = "Models are already being downloaded."
DL_FAILED_NONE = "None of the ticked models could be downloaded, so the benchmark didn't start."
DL_CANCELLED = "Cancelled."
DL_PAUSED = "Paused."
NO_SUCH = "There is no benchmark run with that ID."
NOT_GGUF = "A benchmark can only use a .gguf model file that exists on this device."
NO_FILE_FOR_RUN = "The model file for that test isn't on this device anymore."
ALREADY_THERE = "That model is already on the Models tab."
NOTHING_TO_ADD = "Only local files you add can be put on the Models tab from here."
NO_WINDOW = "MultiProxy couldn't open the save window."
BAD_FORMAT = "The export format must be CSV or JSON."


def load_community(path: str = COMMUNITY_PATH, catalog_ids: Optional[set] = None) -> list:
    """The curated list: entries with a known `id` (a model the Local device tab can download) and a title. Anything malformed is skipped, a missing file gives an empty list."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for e in data.get("models", []) if isinstance(data, dict) else []:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not isinstance(e.get("title"), str):
            continue
        if catalog_ids is not None and e["id"] not in catalog_ids:
            continue
        out.append({k: e.get(k) for k in ("id", "title", "quantisation", "size_bytes", "link", "publisher", "source")})
    return out


def read_setup(base) -> dict:
    data = bs._read_json(Path(base) / SETUP_FILE) or {}
    files = [p for p in data.get("files", []) if isinstance(p, str)]
    return {"files": files, "ticked": [t for t in data.get("ticked", []) if isinstance(t, str)], "hidden": [h for h in data.get("hidden", []) if isinstance(h, str)],
            "setup": data.get("setup") if isinstance(data.get("setup"), dict) else {}}


def write_setup(base, data: dict) -> None:
    Path(base).mkdir(parents=True, exist_ok=True)
    safe_paths.write_private_json(Path(base) / SETUP_FILE, data)


def _gguf_ok(path: str) -> bool:
    return isinstance(path, str) and "\x00" not in path and path.lower().endswith(".gguf") and os.path.isfile(path)


def model_rows(community: list, saved: dict, snap: Optional[dict], paths: Optional[dict] = None, ready: Optional[dict] = None, others: Optional[list] = None,
               failed: Optional[dict] = None) -> list:
    """The list the page draws: `{key, title, sizeBytes, source, state, path?, download?, ticked, failure?}`. `state`: `downloaded` (the model file is on this PC: the Local device tab's
    own copy, whether or not its companion vision file is there), `not_downloaded`, `downloading` (with the same progress object the Local device tab draws), `local` (a file the person
    added, or a model the Local device tab holds that this list does not know), `missing` (a local file that is gone). Every row but a missing one can be ticked: a ticked model that is
    not downloaded is downloaded first. `ready`: catalog id -> the model file on disk. `others`: `[{path, name}]` the Local device tab holds that are not in the curated list."""
    cat = {c["id"]: c for c in (snap or {}).get("catalog", [])}
    inst = {m["id"]: m for m in (snap or {}).get("installed", [])}
    ready = ready if ready is not None else {}
    failed = failed or {}
    rows, seen = [], set()
    for e in community:
        if e["id"] in saved["hidden"]:
            continue
        c = cat.get(e["id"]) or {}
        dl = c.get("download")
        busy = bool(dl) and (dl.get("state") == "running" or bool(dl.get("paused")))
        state = "downloaded" if e["id"] in ready else ("downloading" if busy else "not_downloaded")
        path = ready.get(e["id"])
        if path:
            seen.add(os.path.abspath(path))
        rows.append({"key": "c:" + e["id"], "id": e["id"], "title": e["title"], "quantisation": e.get("quantisation"), "sizeBytes": c.get("sizeBytes") or e.get("size_bytes"), "source": "community",
                     "state": state, "download": dl if state != "downloaded" else None, "link": e.get("link"), "publisher": e.get("publisher"), "installedName": (inst.get(e["id"]) or {}).get("name"),
                     "path": path, "failure": failed.get(e["id"], "")})
    for p in saved["files"]:
        ok = _gguf_ok(p)
        seen.add(os.path.abspath(p))
        rows.append({"key": "f:" + p, "path": p, "title": os.path.basename(p), "sizeBytes": os.path.getsize(p) if ok else None, "source": "local", "state": "local" if ok else "missing"})
    for o in others or []:
        p = os.path.abspath(o["path"])
        if p in seen or not _gguf_ok(p):
            continue
        seen.add(p)
        rows.append({"key": "f:" + p, "path": p, "title": o.get("name") or os.path.basename(p), "sizeBytes": os.path.getsize(p), "source": "local", "state": "local", "fromLocalTab": True})
    ticked = set(saved["ticked"])
    for r in rows:
        r["ticked"] = r["key"] in ticked and r["state"] != "missing"
    return rows


def add_facts(rows: list, local_svc, prompts) -> None:
    """Put `facts` and `cache` (benchmarks_facts) on every row whose model file is on this PC: the facts line and the cache line under its name. A row with nothing to say gets neither."""
    import benchmarks_facts as bf
    import local_engine_settings
    try:
        dev = dict(local_engine_settings.DEFAULT_SETTINGS, **local_svc.engine_settings.effective(local_svc.device_id, "")[0])
        ctx = bf.test_ctx(prompts)
    except Exception:
        return
    for r in rows:
        p = r.get("path")
        if not p or r.get("state") not in ("downloaded", "local"):
            continue
        try:
            got = bf.for_model(p, dev, ctx)
        except Exception:
            got = {}
        r.update(got)


def slots_for(levels, sustained: bool = True) -> int:
    """The most requests at once the test will send: the highest ticked level (the 5-minute run uses 4 slots)."""
    vals = [int(x) for x in (levels or []) if isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 1]
    if sustained:
        vals.append(4)
    return max(vals or [1])


def per_slot_tokens(classes, sustained: bool = True) -> int:
    """What each slot reserves: the room for the biggest prompt of the ticked lengths and its reply (the same figure the runner starts its engine with)."""
    data = tset.load()
    prompts = tset.prompts_for(data, [c for c in classes if c in tset.CLASSES] or list(tset.CLASSES))
    return tset.tokens_per_slot(prompts + ([tset.sustained_prompt(data)] if sustained else []))


def free_memory_mb(card_mb: Optional[float], mode: str, typed_gb, peak_mb, background_mb: float = 0.0) -> Optional[float]:
    """The graphics memory the test can use: the whole card when the game is closed, the card minus Skyrim's recorded peak (`history`), or the number the person typed (`typed`),
    minus what models kept running next to the test hold. None when the card's size is not known."""
    if not card_mb:
        return None
    if mode == "typed":
        try:
            free = min(float(card_mb), float(typed_gb) * 1024)
        except (TypeError, ValueError):
            free = float(card_mb)
    elif mode == "history" and peak_mb:
        free = float(card_mb) - float(peak_mb)
    else:
        free = float(card_mb)
    return max(0.0, free - float(background_mb or 0))


def apply_fit(rows: list, gpu, mode: str, typed_gb, peak_mb, *, levels=(1, 4, 8), prompts=("short", "medium", "long"), device_settings: Optional[dict] = None,
              overrides: Optional[dict] = None, measured: Optional[list] = None, build: Optional[str] = None, background_mb: float = 0.0, batch=None, sustained: bool = True,
              free_now_mb: Optional[float] = None, skyrim_running: bool = False) -> None:
    """Puts the verdict on every row that has a size, from `benchmarks_estimate`: the memory the model needs at the highest ticked number of requests at once (not the file's size)
    against the memory free for the test. `fit` is "too_big" (over the free memory) or "tight" (fits only inside the margin, or the estimate is rough), else None; the rest
    (`fitNeedMb`, `fitFreeMb`, `fitSlots`, `fitConfidence`, `fitSource`) is what the words say. `mode`: `history` (alongside Skyrim, from the recorded peak), `typed` (alongside Skyrim,
    with the memory the person says is free), `closed` (the whole card). No card figure never guesses: no verdict. Nothing is blocked by it. The model list's dot and the question at
    Start both call this, so they cannot disagree."""
    import gguf_header
    import gguf_tensors
    import local_models
    import set_me_up
    alongside = mode in ("history", "typed") and not (mode == "history" and not peak_mb)
    slots = slots_for(levels, sustained)
    per_slot = per_slot_tokens(prompts, sustained)
    card_mb = getattr(gpu, "memory_mb", None) if gpu else None
    free = free_memory_mb(card_mb, mode if alongside else "closed", typed_gb, peak_mb, background_mb)
    if free_now_mb is not None:                       # what the card really has free right now (the card's total minus everything in use): the measured number wins over a guess
        now = max(0.0, float(free_now_mb))
        free = now if (skyrim_running or not alongside or free is None) else min(free, now)     # a planned "next to Skyrim" (Skyrim not running yet) or a typed number can only make it stricter
    for r in rows:
        r["fit"], r["fitWhy"], r["fitAlongside"] = None, "", alongside
        r["fitNeedMb"] = r["fitFreeMb"] = r["fitConfidence"] = r["fitSource"] = None
        r["fitSlots"] = slots
        r["fitParts"] = None
        size = r.get("sizeBytes")
        if not size or free is None:
            continue
        path = r.get("path") if r.get("state") in ("downloaded", "local") else None
        facts = None
        tensors = None
        if path and os.path.isfile(path):
            size = os.path.getsize(path)
            facts = gguf_header.read_model_facts(path)
            tensors = gguf_tensors.read_tensor_sizes(path)
        if not path and r.get("id") in local_models.CATALOG_BY_ID:
            main = [f["size"] for f in local_models.CATALOG_BY_ID[r["id"]]["files"] if f.get("role") == "model"]
            size = main[0] if main else size                                      # the test loads the model file only, not the vision file next to it
        if facts is None and r.get("id"):
            facts = set_me_up.facts_of(r["id"], int(size))                      # a catalog model's header numbers are kept, so a model not downloaded yet (or a file that cannot be read) is judged too
        settings, over = be.run_settings(device_settings or {}, overrides or {}, slots, per_slot)
        m = be.measured_for(measured, os.path.basename(path), size, slots, over["ctx"], settings, build, batch) if path and measured else None
        need = be.estimate_gpu_need(int(size), facts, device_settings or {}, slots, per_slot, overrides, None, m, tensors)
        v = be.verdict(need, free)
        r.update(fitNeedMb=need.need_mb, fitFreeMb=int(free), fitConfidence=need.confidence, fitSource=need.source)
        parts = need.parts or {}
        if (parts.get("weightsMb") or 0) > 0 and (parts.get("cacheMb") or 0) > 0:
            r["fitParts"] = {"modelMb": parts["weightsMb"], "cacheMb": parts["cacheMb"]}          # the model and the cache of the estimate (the Start sentence names them)
        if v in (be.TIGHT, be.TOO_BIG):
            r["fit"] = v


def fit_json(r: dict) -> dict:
    return {"fit": r["fit"], "fitWhy": r["fitWhy"], "fitAlongside": r["fitAlongside"], "fitNeedMb": r["fitNeedMb"], "fitFreeMb": r["fitFreeMb"], "fitSlots": r["fitSlots"], "fitParts": r.get("fitParts"),
            "fitConfidence": r["fitConfidence"], "fitSource": r["fitSource"]}


def pending_download(rows: list) -> dict:
    """The ticked rows whose model file is not on this PC yet: `{ids, count, bytes}` (`bytes` is the full size of their model files; a part already fetched is not taken off)."""
    need = [r for r in rows if r.get("ticked") and r["source"] == "community" and r["state"] in ("not_downloaded", "downloading")]
    return {"ids": [r["id"] for r in need], "count": len(need), "bytes": sum(r.get("sizeBytes") or 0 for r in need)}


def model_path_of(row: dict, local_svc) -> Optional[str]:
    """The file a ticked row stands for."""
    if row["source"] == "local":
        return row["path"] if _gguf_ok(row["path"]) else None
    m = next((m for m in local_svc.installed() if m.id == row["id"]), None)
    return str(m.path) if m else None


def find_model_file(manifest: dict, local_svc, saved: dict) -> Optional[str]:
    """Where the tested file is now: among the downloaded models, or the local files in the list, by file name and size."""
    name, size = manifest.get("model_file"), manifest.get("model_size_bytes")
    cands = [str(m.path) for m in local_svc.installed()] + saved["files"]
    for p in cands:
        if os.path.basename(p) == name and os.path.isfile(p) and (size is None or os.path.getsize(p) == size):
            return p
    return None


def plan_of(manifest: dict, path: str) -> dict:
    """A start body that repeats a run: same model file, levels, requests, prompts, settings and Skyrim choice."""
    r = manifest.get("requested_settings") or {}
    skyrim = r.get("skyrim") or "history"
    if skyrim == "typed":
        skyrim = f"typed:{r.get('skyrim_gb', 0)}"
    elif skyrim == "available":
        skyrim = f"available:{r.get('skyrim_gb', 0)}"
    body = {"models": [path], "levels": r.get("levels"), "requests_per_level": r.get("requests_per_level"), "settings": r.get("overrides") or {}, "prompts": r.get("prompts"),
            "temperature": r.get("temperature"), "sustained": r.get("sustained"), "sustained_s": r.get("sustained_s"), "request_timeout_s": r.get("request_timeout_s"),
            "cooldown": r.get("cooldown"), "cooldown_c": r.get("cooldown_c"), "cooldown_max_s": r.get("cooldown_max_s"), "heat_stop": r.get("heat_stop"), "skyrim": skyrim,
            "warmup": r.get("warmup_requests"), "ceiling_c": r.get("ceiling_c")}
    return {k: v for k, v in body.items() if v is not None}


def results(base, now: float) -> list:
    """One row per run, newest first, from the result files. A run saved by a newer version is listed as such, not guessed at."""
    out = []
    try:
        names = sorted(n for n in os.listdir(base) if bc.valid_run_id(n) and (Path(base) / n).is_dir())
    except OSError:
        return []
    for n in names:
        try:
            m = bs.load_manifest_only(base, n, now)
        except bs.NewerSchemaError as e:
            out.append({"run_id": n, "status": "newer_version", "schema_version": e.found})
            continue
        except ValueError:
            continue
        out.append(br_.result_row(m, bs._read_json(Path(base) / n / bs.SUMMARY)))
    out.sort(key=lambda r: r.get("started_epoch_s") or 0, reverse=True)
    return out


def _read_card_usage() -> dict:
    """The real reading (this PC's card and the programs on it). Its own function so a test can replace it and never touch the real card."""
    return bcu.read_usage(bsm.run_command)


def local_programs(base, run_id: str) -> list:
    """The biggest other programs on the card when this run started, from the run's own local file. Program names stay on this PC: only the two routes that draw this PC's own screen call
    this; every export, save and send goes through `results()` / `bs.full_export`, which never read that file."""
    data = bs._read_json(Path(base) / run_id / bcu.OTHER_FILE) or {}
    return [{"name": str(p.get("name")), "mb": p.get("mb")} for p in (data.get("at_start") or []) if isinstance(p, dict) and p.get("name") and isinstance(p.get("mb"), (int, float))][:bcu.TOP_N]


def with_local_programs(base, rows: list) -> list:
    """The results rows for this PC's own screen, each with `others_at_start` (names and sizes). NOT for an export."""
    return [dict(r, others_at_start=local_programs(base, r["run_id"])) if r.get("run_id") and bc.valid_run_id(r["run_id"]) and r.get("status") != "newer_version" else r for r in rows]


def run_detail(base, run_id: str, now: float) -> dict:
    run = bs.load_run(base, run_id, now)
    return dict(br_.detail(run["manifest"], run["summary"], run["requests"], run["samples"]), others_at_start=local_programs(base, run_id))          # this PC's own screen only


def export_text(base, run_id: str, fmt: str, now: float) -> tuple:
    """(text, suggested file name, extension) for one run."""
    if fmt == "csv":
        return bs.requests_csv(base, run_id), f"benchmark-{run_id[:15]}-requests.csv", ".csv"
    return json.dumps(bcu.strip_names(bs.full_export(base, run_id, now)), indent=2), f"benchmark-{run_id[:15]}-results.json", ".json"


class DownloadQueue:
    """Downloads the model files of several models one after another with the Local device tab's own download (only the model file), then, if a start body came with it, tests every
    model that is on the PC back to back. A download that fails leaves that model out and says why; Cancel stops everything and starts nothing; a Pause ends the queue (Resume downloads
    that one model; the test is started again with Start benchmark)."""

    def __init__(self, local_svc, service, log: Callable[[str], None] = lambda m: None):
        self.local, self.service, self.log = local_svc, service, log
        self.task: Optional[asyncio.Task] = None
        self.reset()

    def reset(self) -> None:
        self.n = getattr(self, "n", 0)
        self.ids: list = []
        self.current: Optional[str] = None
        self.failed: dict = {}
        self.done: list = []
        self.cancelled = False
        self.paused = False
        self.message = ""
        self.start_result: Optional[dict] = None
        self.body: Optional[dict] = None

    def active(self) -> bool:
        return bool(self.task and not self.task.done())

    def state(self) -> dict:
        return {"id": self.n, "active": self.active(), "ids": list(self.ids), "current": self.current, "failed": dict(self.failed), "done": list(self.done), "cancelled": self.cancelled,
                "paused": self.paused, "message": self.message, "will_start": bool(self.body), "start_result": self.start_result, "body": self.body if self.start_result and not self.start_result.get("ok") else None}

    def begin(self, ids: list, then: Optional[dict]) -> None:
        self.n += 1
        self.reset()
        self.ids, self.body = list(ids), then
        self.task = asyncio.create_task(self._run())

    async def cancel(self, one: Optional[str] = None) -> bool:
        was = self.active()
        if was:
            self.cancelled = True
        current = self.current
        if self.task and not self.task.done():
            self.task.cancel()                                  # first, so it cannot begin the next download
            await asyncio.gather(self.task, return_exceptions=True)
        for mid in {x for x in (one, current) if x}:
            await self.local.cancel(f"model:{mid}")
        if was:
            self.current = None
            self.message = DL_CANCELLED
        return was

    async def _run(self) -> None:
        import local_models
        try:
            for mid in self.ids:
                self.current = mid
                try:
                    job = self.local.download_model_file(mid)
                except local_models.LocalModelError as e:
                    self.failed[mid] = e.message
                    continue
                if job.task is not None:
                    await asyncio.gather(job.task, return_exceptions=True)
                if self.cancelled:
                    return
                if job.state == "done" and self.local.model_file(mid):
                    self.done.append(mid)
                elif job.state == "paused" or self.local_paused(mid):
                    self.paused, self.message = True, DL_PAUSED
                    return
                else:
                    self.failed[mid] = job.error or "The download didn't finish."
            self.current = None
            if self.body is None:
                return
            paths = [p for p in self.body.get("models", []) if isinstance(p, str)] + [str(self.local.model_file(m)) for m in self.done]
            if not paths:
                self.message = DL_FAILED_NONE
                self.start_result = {"ok": False, "message": DL_FAILED_NONE}
                return
            body = dict(self.body, models=paths)
            self.body = body
            self.start_result = await self.service.start(dict(body))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log(f"Benchmarks download queue stopped: {e}")
            self.message = str(e)

    def local_paused(self, mid: str) -> bool:
        try:
            return f"model:{mid}" in self.local._paused_marks()
        except Exception:
            return False


def setup(app, base: Path, local_svc, load_cfg: Callable[[], dict], service, wall: Callable[[], float] = time.time, cards_fn: Optional[Callable[[], list]] = None,
          has_game: Callable[[], bool] = lambda: True, game_running: Callable[[], bool] = lambda: False, save_dialog=None, pick_dialog=None, community_path: str = COMMUNITY_PATH) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    import local_models
    import skyrim_peak
    import gpu_choice

    save_dialog = save_dialog or local_models.save_file_dialog
    pick_dialog = pick_dialog or local_models.pick_file_dialog
    cards_fn = cards_fn or gpu_choice.cached_cards
    queue = DownloadQueue(local_svc, service)

    def err(status: int, message: str):
        return JSONResponse({"detail": message}, status_code=status)

    def has_body(request: Request) -> bool:
        h = request.headers
        try:
            return int(h.get("content-length") or 0) > 0 or "transfer-encoding" in h
        except ValueError:
            return True

    async def body_of(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def card_of():
        try:
            return await local_svc.gpu()
        except Exception:
            return None

    def int_list(text: str, default: list) -> list:
        vals = [int(x) for x in str(text or "").split(",") if x.strip().isdigit() and 1 <= int(x) <= 64][:8]
        return vals or default

    def overrides_of(text: str) -> dict:
        """The plan's changed engine settings, as the page sends them (a small JSON object, only the keys a plan may change)."""
        try:
            data = json.loads(text) if text and len(text) <= 2000 else {}
        except ValueError:
            data = {}
        return {k: v for k, v in data.items() if k in brun.ALLOWED_OVERRIDES and isinstance(v, (str, int))} if isinstance(data, dict) else {}

    def background_mb(choice: str) -> float:
        """What models kept running next to the test hold on the card (nothing when they are stopped for the test)."""
        import benchmarks_background as bgm
        if choice not in (bgm.BACKGROUND_KEEP, bgm.BACKGROUND_START_NORMAL):
            return 0.0
        try:
            hooks = service.hooks
            loaded = bgm.normalize(hooks.background_loaded())
            to_start = [a for a in bgm.normalize(hooks.background_assigned()) if a["state"] == bgm.STATE_ASSIGNED_STOPPED] if choice == bgm.BACKGROUND_START_NORMAL else []
            return float(bgm.biggest_on_one_card_mb(loaded) + bgm.biggest_on_one_card_mb(to_start, use_size_for_unknown=True))
        except Exception:
            return 0.0

    usage_cache: dict = {"usage": None}

    async def read_usage(fresh: bool) -> dict:
        """Who is using the card now. Read when the tab opens, on Refresh and at Start (`fresh`); every other caller (the fit dots re-drawn after a click) uses the last reading, so the
        counters are not asked on every poll. During a run the runner asks, every 5 seconds."""
        if fresh or usage_cache["usage"] is None:
            usage_cache["usage"] = await asyncio.to_thread(_read_card_usage)
        return usage_cache["usage"]

    @app.get("/config/benchmarks/card-usage")
    async def card_usage(request: Request, fresh: str = ""):
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        return await read_usage(fresh == "1")

    async def fit_inputs(levels, prompts, overrides, background) -> dict:
        """Everything `apply_fit` needs besides the rows: the device's own engine settings, earlier runs of the same configuration, the engine build, what runs next to the test."""
        try:
            import local_engine_settings
            dev = dict(local_engine_settings.DEFAULT_SETTINGS, **local_svc.engine_settings.effective(local_svc.device_id, "")[0])
        except Exception:
            dev = {}
        build = None
        try:
            exe = local_svc.engine_exe()
            build = local_svc.engine_build_label(exe) if exe else None
        except Exception:
            build = None
        measured = await asyncio.to_thread(be.load_measured, base)
        use = usage_cache["usage"] or {}
        return {"levels": levels, "prompts": prompts, "device_settings": dev, "overrides": overrides, "measured": measured, "build": build, "background_mb": background_mb(background),
                "batch": overrides.get("batch"), "free_now_mb": use.get("freeMb") if use.get("available") else None, "skyrim_running": bool(game_running())}

    @app.get("/config/benchmarks/fit")
    async def fit_state(request: Request, mode: str = "closed", gb: str = "", levels: str = "", prompts: str = "", bg: str = "stop", ov: str = ""):
        """The fit dots again for the choices the person just made: the Skyrim choice, the levels, the prompt lengths, what runs next to the test (the page asks when one changes)."""
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        snap = await local_svc.snapshot()
        community = load_community(community_path, {c["id"] for c in snap.get("catalog", [])})
        saved = await asyncio.to_thread(read_setup, base)
        peak, _ = skyrim_peak.figure(load_cfg())
        ready = {e["id"]: str(p) for e in community for p in [local_svc.model_file(e["id"])] if p}
        rows = model_rows(community, saved, snap, None, ready, [{"path": str(m.path), "name": m.name} for m in local_svc.installed() if m.id not in {e["id"] for e in community}], queue.failed)
        kw = await fit_inputs(int_list(levels, [1, 4, 8]), [c for c in prompts.split(",") if c in tset.CLASSES] or list(tset.CLASSES), overrides_of(ov), bg)
        apply_fit(rows, await card_of(), mode if mode in ("history", "typed", "closed") else "closed", gb, peak if has_game() else None, **kw)
        return {"fit": {r["key"]: fit_json(r) for r in rows}}

    @app.post("/config/benchmarks/precheck")
    async def precheck(request: Request):
        """Before a test starts: for the plan about to be sent, which models need more graphics memory than is free (the same numbers as the dots). Asks nothing, blocks nothing."""
        body = await body_of(request)
        try:
            plan = brun.parse_plan(body)
        except brun.PlanError as e:
            return err(400, str(e))
        ids = {str(m.path): m.id for m in local_svc.installed()}                  # a model this PC downloaded is known by its catalog id too (its header numbers are kept)
        rows = [{"key": p, "path": p, "state": "local", "sizeBytes": os.path.getsize(p), "title": os.path.basename(p), "id": ids.get(p)} for p in plan.models if os.path.isfile(p)]
        peak, _ = skyrim_peak.figure(load_cfg())
        mode = {"closed": "closed", "available": "typed"}.get(plan.skyrim, "history")
        usage = await read_usage(True)                                           # at Start the card is read again
        kw = await fit_inputs(list(plan.levels), list(plan.prompts), dict(plan.overrides), plan.background)
        apply_fit(rows, await card_of(), mode, plan.skyrim_gb, peak if has_game() else None, sustained=plan.sustained, **kw)
        return {"rows": [dict(fit_json(r), path=r["path"], name=str(r["title"]).replace(".gguf", "")) for r in rows], "slots": slots_for(plan.levels, plan.sustained),
                "usage": usage if usage.get("available") else None}

    async def rows_now() -> list:
        """The model list as the page shows it (key, path, size, ticked, state): the page that tests on another PC finds its files from the keys it sends, never from a path."""
        snap = await local_svc.snapshot()
        community = load_community(community_path, {c["id"] for c in snap.get("catalog", [])})
        saved = await asyncio.to_thread(read_setup, base)
        ready = {e["id"]: str(p) for e in community for p in [local_svc.model_file(e["id"])] if p}
        others = [{"path": str(m.path), "name": m.name} for m in local_svc.installed() if m.id not in {e["id"] for e in community}]
        return model_rows(community, saved, snap, None, ready, others, queue.failed)
    app.state.benchmarks_rows = rows_now

    @app.get("/config/benchmarks/tab")
    async def tab_state(request: Request):
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        snap = await local_svc.snapshot()
        community = load_community(community_path, {c["id"] for c in snap.get("catalog", [])})
        saved = await asyncio.to_thread(read_setup, base)
        peak, day = skyrim_peak.figure(load_cfg())
        cards = await asyncio.to_thread(cards_fn)
        real = gpu_choice.choosable(cards)
        in_list = {e["id"] for e in community}
        ready = {e["id"]: str(p) for e in community for p in [local_svc.model_file(e["id"])] if p}
        others = [{"path": str(m.path), "name": m.name} for m in local_svc.installed() if m.id not in in_list]
        rows = model_rows(community, saved, snap, None, ready, others, queue.failed)
        s = saved.get("setup") or {}
        kw = await fit_inputs(int_list(",".join(str(x) for x in (s.get("levels") or [])), [1, 4, 8]), [c for c in (s.get("prompts") or []) if c in tset.CLASSES] or list(tset.CLASSES), {}, "stop")
        apply_fit(rows, await card_of(), s.get("skyrim") if s.get("skyrim") in ("history", "typed", "closed") else "history", s.get("typed"), peak if has_game() else None, **kw)
        need = pending_download(rows)
        await asyncio.to_thread(add_facts, rows, local_svc, [c for c in (s.get("prompts") or []) if c in tset.CLASSES] or list(tset.CLASSES))
        return {"models": rows, "pending": need, "queue": queue.state(), "skyrim": {"peakMb": int(peak) if peak else None, "peakDay": day, "hasGame": bool(has_game()), "running": bool(game_running())},
                "gpus": {"cards": [c.to_json() for c in real], "shown": len(real) > 1, "total": len(cards), "firstCardMb": real[0].memory_mb if real else None},
                "setup": saved["setup"], "defaults": {"levels": [1, 4, 8], "requestsPerLevel": 25, "maxRequestsPerLevel": 100, "prompts": list(bc.PROMPT_CLASSES), "advanced": ADVANCED_DEFAULTS,
                                                          "coolDownC": 55, "coolDown": True, "heatStop": True},
                "engineInstalled": bool(snap.get("engine", {}).get("installed")), "engine": brun.engine_state_of(bool(snap.get("engine", {}).get("installed")), load_cfg()), "gpuTotalMb": (snap.get("gpu") or {}).get("memoryGb")}

    @app.post("/config/benchmarks/setup")
    async def save_setup(request: Request):
        data = await body_of(request)
        raw = json.dumps(data)
        if len(raw) > MAX_SETUP_BYTES:
            return err(400, "That setup is too big.")
        saved = await asyncio.to_thread(read_setup, base)
        if isinstance(data.get("ticked"), list):
            saved["ticked"] = [t for t in data["ticked"] if isinstance(t, str)][:200]
        if isinstance(data.get("setup"), dict):
            saved["setup"] = data["setup"]
        await asyncio.to_thread(write_setup, base, saved)
        return {"ok": True}

    @app.post("/config/benchmarks/add-file")
    async def add_file(request: Request):
        path = await asyncio.to_thread(pick_dialog, "Choose a model file", GGUF_TYPES)
        if not path:
            return {"cancelled": True}
        if not _gguf_ok(path):
            return err(400, NOT_GGUF)
        saved = await asyncio.to_thread(read_setup, base)
        path = os.path.abspath(path)
        if path not in saved["files"]:
            saved["files"].append(path)
        await asyncio.to_thread(write_setup, base, saved)
        return {"ok": True, "key": "f:" + path}

    @app.post("/config/benchmarks/remove-file")
    async def remove_file(request: Request):
        data = await body_of(request)
        saved = await asyncio.to_thread(read_setup, base)
        saved["files"] = [p for p in saved["files"] if p != data.get("path")]
        saved["ticked"] = [t for t in saved["ticked"] if t != "f:" + str(data.get("path"))]
        await asyncio.to_thread(write_setup, base, saved)
        return {"ok": True}

    @app.post("/config/benchmarks/hide")
    async def hide(request: Request):
        data = await body_of(request)
        saved = await asyncio.to_thread(read_setup, base)
        mid = str(data.get("id") or "")
        if mid and mid not in saved["hidden"]:
            saved["hidden"].append(mid)
        saved["ticked"] = [t for t in saved["ticked"] if t != "c:" + mid]
        await asyncio.to_thread(write_setup, base, saved)
        return {"ok": True}

    @app.post("/config/benchmarks/download")
    async def download_route(request: Request):
        import local_models
        data = await body_of(request)
        ids = [i for i in data.get("ids", []) if isinstance(i, str)] if isinstance(data.get("ids"), list) else []
        if not ids:
            return err(400, DL_NOTHING)
        if queue.active():
            return err(409, DL_BUSY)
        then = data.get("then") if isinstance(data.get("then"), dict) else None
        todo = []
        for i in ids:
            if i not in todo and i in local_models.CATALOG_BY_ID and not local_svc.model_file(i):
                todo.append(i)
        try:
            need = sum(next(f["size"] for f in local_models.CATALOG_BY_ID[i]["files"] if f["role"] == "model") for i in todo)
            if todo:
                local_svc._need_folder()
                local_svc._check_space(need + 256 * 1024 * 1024)
        except local_models.LocalModelError as e:
            return err(e.status, e.message)
        queue.begin(todo, then)
        return {"ok": True, "ids": todo}

    @app.post("/config/benchmarks/download/cancel")
    async def download_cancel(request: Request):
        data = await body_of(request)
        one = data.get("id") if isinstance(data.get("id"), str) else None
        return {"ok": True, "cancelled": await queue.cancel(one) or bool(one)}

    @app.get("/config/benchmarks/downloads")
    async def downloads_state(request: Request):
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        return queue.state()

    @app.get("/config/benchmarks/results")
    async def results_route(request: Request):
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        return {"runs": await asyncio.to_thread(lambda: with_local_programs(base, results(base, wall())))}

    def valid(run_id: str) -> bool:
        return bc.valid_run_id(run_id) and (Path(base) / run_id).is_dir()

    @app.get("/config/benchmarks/runs/{run_id}/detail")
    async def detail_route(run_id: str, request: Request):
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        if not valid(run_id):
            return err(404, NO_SUCH)
        try:
            return await asyncio.to_thread(run_detail, base, run_id, wall())
        except bs.NewerSchemaError as e:
            return {"status": "newer_version", "schema_version": e.found, "detail": bs.NEWER_VERSION_LINE}
        except ValueError:
            return err(404, NO_SUCH)

    @app.get("/config/benchmarks/runs/{run_id}/plan")
    async def plan_route(run_id: str, request: Request):
        if has_body(request):
            return err(400, "That request doesn't take a body.")
        if not valid(run_id):
            return err(404, NO_SUCH)
        try:
            manifest = await asyncio.to_thread(bs.read_manifest, Path(base) / run_id)
        except ValueError:
            return err(404, NO_SUCH)
        path = find_model_file(manifest, local_svc, await asyncio.to_thread(read_setup, base))
        if not path:
            return err(404, NO_FILE_FOR_RUN)
        return plan_of(manifest, path)

    async def save_text(text: str, name: str, ext: str):
        kind = "JSON" if ext == ".json" else "CSV"
        path = await asyncio.to_thread(save_dialog, "Save", name, [(kind + " file", "*" + ext), ("All files", "*.*")], ext)
        if path is None:
            return {"cancelled": True}
        try:
            await asyncio.to_thread(lambda: Path(path).write_text(text, encoding="utf-8", newline=""))
        except OSError:
            return err(500, NO_WINDOW)
        return {"saved": True}

    @app.post("/config/benchmarks/runs/{run_id}/save")
    async def save_route(run_id: str, request: Request):
        data = await body_of(request)
        fmt = str(data.get("format") or "")
        if fmt not in ("csv", "json"):
            return err(400, BAD_FORMAT)
        if not valid(run_id):
            return err(404, NO_SUCH)
        try:
            text, name, ext = await asyncio.to_thread(export_text, base, run_id, fmt, wall())
        except (bs.NewerSchemaError, ValueError):
            return err(404, NO_SUCH)
        return await save_text(text, name, ext)

    @app.post("/config/benchmarks/save-results")
    async def save_results_route(request: Request):
        rows = await asyncio.to_thread(results, base, wall())
        return await save_text(json.dumps(bcu.strip_names({"schema_version": bc.SCHEMA_VERSION, "results": rows}), indent=2), "benchmark-results.json", ".json")

    @app.post("/config/benchmarks/add-to-multiproxy")
    async def add_to_multiproxy(request: Request):
        data = await body_of(request)
        run_id = str(data.get("run_id") or "")
        if not valid(run_id):
            return err(404, NO_SUCH)
        try:
            manifest = await asyncio.to_thread(bs.read_manifest, Path(base) / run_id)
        except ValueError:
            return err(404, NO_SUCH)
        saved = await asyncio.to_thread(read_setup, base)
        path = find_model_file(manifest, local_svc, saved)
        if not path:
            return err(404, NO_FILE_FOR_RUN)
        if any(str(m.path) == path for m in local_svc.installed()):
            return {"ok": True, "already": True, "message": ALREADY_THERE}
        try:
            m = await asyncio.to_thread(local_svc.add_custom, path, "")
        except local_models.LocalModelError as e:
            return err(e.status, e.message)
        return {"ok": True, "modelName": m.model_name}
