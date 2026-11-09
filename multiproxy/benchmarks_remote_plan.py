"""Benchmarks on another PC, the rules a plan must pass BEFORE anything runs (protocol: docs/roadmap/benchmarks-remote-protocol.md, security review: docs/security-review-benchmarks-remote-2026-10-28.md).

The Courier is asked over the home network to start processes and write files. So what a plan may contain is decided here, by pure functions that read nothing and write nothing:
a plan names model IDs (never a path, never a URL), a fixed list of engine options with ranges, and a few switches; anything else is refused with a code and the field's name.
The same module holds the path-containment helper and the result-filename check (used on both PCs), the job state table, and the compatibility check of a Courier's capability list.
Nothing here is wired to a route yet (step 2 does that).
"""
import hashlib
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

import benchmarks_background as bg
import benchmarks_contract as bc
import local_engine_flags as lef

PROTOCOL_VERSION = 1
KIND_CHAT = "chat"

# --- caps ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
MAX_BODY_BYTES = 64 * 1024                        # the whole start request; the route reads at most this many bytes before parsing
MAX_DEPTH = 4
MAX_STRING = 300
MAX_MODELS = 8
MAX_LEVELS = 5
ALLOWED_LEVELS = (1, 2, 4, 8, 16)                 # the concurrency choices the screen offers (the local runner takes 1 to 32; a remote plan takes this set)
MAX_REQUESTS_PER_LEVEL = 100
MAX_SUSTAINED_S = 1800
MAX_REQUEST_TIMEOUT_S = 600
MAX_MODEL_BYTES = 120 * 1024 ** 3                 # the same ceiling the transfer takes (file_transfer.MAX_FILE_BYTES)
MAX_JOB_BYTES = 240 * 1024 ** 3                   # and the same total
MAX_RESULT_FILE_BYTES = 64 * 1024 ** 2            # one result file coming back
MAX_RESULT_TOTAL_BYTES = 256 * 1024 ** 2          # all the files of one run

JOBS_SUBDIR = "benchmark-jobs"                    # under the Courier's models folder: <models>/benchmark-jobs/<job id>/<file>

REQUEST_ID = re.compile(r"^[0-9A-Za-z_-]{8,64}\Z")
JOB_ID = re.compile(r"^[0-9A-Za-z_-]{8,64}\Z")
OWNER_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.-]{7,63}\Z")        # the controlling PC's permanent id
CATALOGUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\Z")    # the same shape file_transfer takes for a catalogue model
CONTENT_ID = re.compile(r"^sha256:([0-9a-f]{64}):([0-9]{1,13})\Z")  # a model by its bytes (benchmarks_store.model_id_for)
SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
GGUF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._() \[\]-]{0,200}\.gguf\Z")        # file_transfer.valid_gguf_name
GPU_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._ -]{0,63}\Z")     # a card id from the device's own list (the Courier checks it against that list)

PROMPT_CLASSES = ("short", "medium", "long")
WHILE_TESTING = bg.BACKGROUND_CHOICES              # stop | keep | start_normal (contract 12 and 12b)
ALLOWED_OVERRIDES = ("layers", "flashAttn", "cacheType", "ctx", "batch", "threads", "gpu")
TOP_KEYS = frozenset({"protocol_version", "request_id", "owner_id", "kind", "models", "levels", "requests_per_level", "prompts", "settings", "temperature", "sustained", "sustained_s",
                      "request_timeout_s", "warmup", "cooldown", "cooldown_c", "cooldown_max_s", "ceiling_c", "heat_stop", "while_testing", "keep_started", "keep_models", "take_over"})
REQUIRED_KEYS = frozenset({"protocol_version", "request_id", "owner_id", "models"})
MODEL_KEYS = frozenset({"id", "file", "sha256", "size"})

# --- refusals -----------------------------------------------------------------------------------------------------------------------------------------------------------------
# One code per kind of refusal, the field it is about, and a plain line (the main PC may show the line; the code is what code tests).
PLAN_BAD = "{pc} refused that benchmark plan: {why}."
CODES = ("too_big", "bad_type", "unknown_key", "missing_key", "bad_value", "out_of_range", "url_not_allowed", "path_not_allowed", "unknown_model", "model_mismatch", "bad_protocol", "bad_id")


class PlanRefused(ValueError):
    """A plan that is not accepted. `code` is one of CODES, `field` the dotted name of what was wrong, `message` the plain line."""

    def __init__(self, code: str, field: str, why: str):
        super().__init__(f"{field}: {why}")
        self.code, self.field, self.why = code, field, why
        self.message = PLAN_BAD.format(pc="This PC", why=why)


def _refuse(code: str, field: str, why: str):
    raise PlanRefused(code, field, why)


# --- shape scan ---------------------------------------------------------------------------------------------------------------------------------------------------------------

_URLISH = re.compile(r"(^|[^A-Za-z0-9])([A-Za-z][A-Za-z0-9+.-]{1,15}://|www\.)", re.I)
_PATHISH = re.compile(r"(^[A-Za-z]:)|[\\/]|(^|[\\/])\.\.?([\\/]|$)|^~")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _scan(value: Any, field: str, depth: int = 0) -> None:
    """Every key is a short plain string, every string short and free of control characters, every number finite, nothing nested deeper than MAX_DEPTH."""
    if depth > MAX_DEPTH:
        _refuse("too_big", field, "the plan is nested too deeply")
    if isinstance(value, dict):
        if len(value) > 64:
            _refuse("too_big", field, "too many entries")
        for k, v in value.items():
            if not isinstance(k, str) or not k or len(k) > 40 or _CONTROL.search(k):
                _refuse("bad_type", field, "a name in the plan is not valid")
            _scan(v, f"{field}.{k}" if field else k, depth + 1)
    elif isinstance(value, list):
        if len(value) > 64:
            _refuse("too_big", field, "too many entries")
        for i, v in enumerate(value):
            _scan(v, f"{field}[{i}]", depth + 1)
    elif isinstance(value, str):
        if len(value) > MAX_STRING:
            _refuse("too_big", field, "a value is too long")
        if _CONTROL.search(value):
            _refuse("bad_value", field, "a value holds a control character")
    elif isinstance(value, float):
        if not math.isfinite(value):
            _refuse("bad_value", field, "a number is not finite")
    elif value is None or isinstance(value, (bool, int)):
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 10 ** 15:
            _refuse("out_of_range", field, "a number is too large")
    else:
        _refuse("bad_type", field, "a value of an unknown kind")


def _where_an_id_belongs(value: str, field: str) -> None:
    """A URL or a path where an ID belongs gets its own code, so a log (and a test) can tell an attack from a typo."""
    if _URLISH.search(value):
        _refuse("url_not_allowed", field, "a plan names things by ID, never by web address")
    if _PATHISH.search(value):
        _refuse("path_not_allowed", field, "a plan names things by ID, never by file path")


def _text(body: Mapping, key: str, pattern: "re.Pattern", field: str, code: str = "bad_id") -> str:
    v = body.get(key)
    if not isinstance(v, str):
        _refuse("bad_type", field, "must be text")
    _where_an_id_belongs(v, field)
    if not pattern.match(v):
        _refuse(code, field, "is not a valid ID")
    return v


def _int(v: Any, lo: int, hi: int, field: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        _refuse("bad_type", field, "must be a whole number")
    if not lo <= v <= hi:
        _refuse("out_of_range", field, f"must be from {lo} to {hi}")
    return v


def _num(v: Any, lo: float, hi: float, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _refuse("bad_type", field, "must be a number")
    if not math.isfinite(v) or not lo <= v <= hi:
        _refuse("out_of_range", field, f"must be from {lo:g} to {hi:g}")
    return v


def _flag(v: Any, field: str) -> bool:
    if not isinstance(v, bool):
        _refuse("bad_type", field, "must be true or false")
    return v


# --- the models ---------------------------------------------------------------------------------------------------------------------------------------------------------------

def _model(entry: Any, index: int, catalogue: Mapping[str, Mapping]) -> dict:
    """One model of the plan, resolved. `id` is a catalogue id (the catalogue says the file name, and the size and hash when it knows them) or a content id `sha256:<hex>:<size>`
    (then the entry carries its own `file`, `sha256` and `size`, and `sha256` and `size` must agree with the id). Never a path or a URL; the file name is a plain `.gguf` name."""
    f = f"models[{index}]"
    if not isinstance(entry, dict):
        _refuse("bad_type", f, "a model must be an object with an id")
    extra = set(entry) - MODEL_KEYS
    if extra:
        _refuse("unknown_key", f"{f}.{sorted(extra)[0]}", "that model setting isn't accepted")
    if "id" not in entry:
        _refuse("missing_key", f"{f}.id", "a model needs an id")
    mid = _text(entry, "id", re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}\Z"), f"{f}.id", "unknown_model")
    out: dict = {"id": mid}
    content = CONTENT_ID.match(mid)
    if content:
        for key in ("file", "sha256", "size"):
            if key not in entry:
                _refuse("missing_key", f"{f}.{key}", "a model sent by its bytes needs its file name, hash and size")
        digest, size = content.group(1), int(content.group(2))
        if entry["sha256"] != digest:
            _refuse("model_mismatch", f"{f}.sha256", "the hash doesn't match the model's id")
        if _int(entry["size"], 1, MAX_MODEL_BYTES, f"{f}.size") != size:
            _refuse("model_mismatch", f"{f}.size", "the size doesn't match the model's id")
        out.update(file=_gguf_name(entry["file"], f"{f}.file"), sha256=digest, size=size, source="content")
        return out
    if not CATALOGUE_ID.match(mid):
        _refuse("unknown_model", f"{f}.id", "that model id isn't valid")
    known = catalogue.get(mid)
    if known is None:
        _refuse("unknown_model", f"{f}.id", "that model isn't in this PC's list")
    cat_file = known.get("file")
    out["file"] = _gguf_name(cat_file, f"{f}.file")
    if "file" in entry and _gguf_name(entry["file"], f"{f}.file") != cat_file:
        _refuse("model_mismatch", f"{f}.file", "the file name doesn't match that model")
    for key in ("sha256", "size"):
        if key in entry:
            if known.get(key) is not None and entry[key] != known[key]:
                _refuse("model_mismatch", f"{f}.{key}", f"the {key} doesn't match that model")
            if key == "sha256" and not (isinstance(entry[key], str) and SHA256.match(entry[key])):
                _refuse("bad_value", f"{f}.sha256", "is not a valid hash")
            if key == "size":
                _int(entry[key], 1, MAX_MODEL_BYTES, f"{f}.size")
    sha = entry.get("sha256", known.get("sha256"))
    size = entry.get("size", known.get("size"))
    out.update(sha256=sha, size=size, source="catalogue")
    return out


def _gguf_name(name: Any, field: str) -> str:
    if not isinstance(name, str):
        _refuse("bad_type", field, "must be text")
    _where_an_id_belongs(name, field)
    if not GGUF_NAME.match(name) or reserved_name(name):
        _refuse("bad_value", field, "is not a plain .gguf file name")
    return name


# --- the settings -------------------------------------------------------------------------------------------------------------------------------------------------------------

def _settings(over: Any) -> dict:
    """The engine options a plan may set: this list and these ranges only. Each becomes a fixed flag on the engine's argument list on the Courier; no text is passed through."""
    if over is None:
        return {}
    if not isinstance(over, dict):
        _refuse("bad_type", "settings", "must be an object")
    for k in over:
        if k not in ALLOWED_OVERRIDES:
            _refuse("unknown_key", f"settings.{k}", "that engine setting isn't accepted")
    out: dict = {}
    for k, v in over.items():
        f = f"settings.{k}"
        if k == "layers":
            if isinstance(v, str):
                if v not in lef.LAYER_WORDS:
                    _refuse("bad_value", f, "unknown value")
            else:
                _int(v, 0, 999, f)
        elif k == "flashAttn":
            if v not in ("auto", "on", "off"):
                _refuse("bad_value", f, "unknown value")
        elif k == "cacheType":
            if v not in lef.SIMPLE_CACHE_TYPES:
                _refuse("bad_value", f, "unknown value")
        elif k == "ctx":
            _int(v, 512, 262144, f)
        elif k == "batch":
            _int(v, 32, 8192, f)
        elif k == "threads":
            _int(v, 1, 256, f)
        elif k == "gpu":
            if not isinstance(v, str):
                _refuse("bad_type", f, "must be text")
            _where_an_id_belongs(v, f)
            if not GPU_TOKEN.match(v):
                _refuse("bad_value", f, "is not a valid card id")
        out[k] = v
    return out


# --- the plan -----------------------------------------------------------------------------------------------------------------------------------------------------------------

def validate_plan(body: Any, catalogue: Optional[Mapping[str, Mapping]] = None) -> dict:
    """The normalised, frozen plan for `body`, or PlanRefused. `catalogue` maps a catalogue model id to {"file", and optionally "sha256" and "size"}; the caller (the Courier) builds it from
    its own built-in list, never from the request. The result has every default filled in, lists sorted, and a `plan_hash` over its canonical form: it is what the job stores and what a
    retried start is compared with."""
    catalogue = catalogue or {}
    if not isinstance(body, dict):
        _refuse("bad_type", "", "the plan must be a JSON object")
    _scan(body, "")
    for k in body:
        if k not in TOP_KEYS:
            _refuse("unknown_key", k, "that setting isn't accepted")
    for k in sorted(REQUIRED_KEYS):
        if k not in body:
            _refuse("missing_key", k, "this is required")
    if body["protocol_version"] != PROTOCOL_VERSION or isinstance(body["protocol_version"], bool):
        _refuse("bad_protocol", "protocol_version", "this PC speaks a different benchmark protocol")
    request_id = _text(body, "request_id", REQUEST_ID, "request_id")
    owner_id = _text(body, "owner_id", OWNER_ID, "owner_id")
    if body.get("kind", KIND_CHAT) != KIND_CHAT:
        _refuse("bad_value", "kind", "only Chat tests exist so far")
    models = body["models"]
    if not isinstance(models, list):
        _refuse("bad_type", "models", "must be a list")
    if not 1 <= len(models) <= MAX_MODELS:
        _refuse("out_of_range", "models", f"pick from 1 to {MAX_MODELS} models")
    resolved = [_model(m, i, catalogue) for i, m in enumerate(models)]
    if len({m["id"] for m in resolved}) != len(resolved):
        _refuse("bad_value", "models", "a model is listed twice")
    if len({m["file"].lower() for m in resolved}) != len(resolved):
        _refuse("bad_value", "models", "two models share a file name")
    sizes = [m["size"] for m in resolved if m.get("size")]
    if sum(sizes) > MAX_JOB_BYTES:
        _refuse("too_big", "models", "the models together are too big")
    levels = body.get("levels", [1, 4, 8])
    if not isinstance(levels, list) or not 1 <= len(levels) <= MAX_LEVELS:
        _refuse("out_of_range", "levels", f"pick from 1 to {MAX_LEVELS} levels")
    for i, n in enumerate(levels):
        if isinstance(n, bool) or not isinstance(n, int) or n not in ALLOWED_LEVELS:
            _refuse("out_of_range", f"levels[{i}]", f"must be one of {', '.join(map(str, ALLOWED_LEVELS))}")
    prompts = body.get("prompts", list(PROMPT_CLASSES))
    if not isinstance(prompts, list) or not prompts or any(p not in PROMPT_CLASSES for p in prompts):
        _refuse("bad_value", "prompts", "must be short, medium or long")
    choice = body.get("while_testing", bg.BACKGROUND_START_NORMAL)
    if choice not in WHILE_TESTING:
        _refuse("bad_value", "while_testing", "must be stop, keep or start_normal")
    ceiling = body.get("ceiling_c")
    plan = {
        "protocol_version": PROTOCOL_VERSION, "request_id": request_id, "owner_id": owner_id, "kind": KIND_CHAT, "models": resolved,
        "levels": sorted(set(levels)), "requests_per_level": _int(body.get("requests_per_level", 25), 1, MAX_REQUESTS_PER_LEVEL, "requests_per_level"),
        "prompts": [c for c in PROMPT_CLASSES if c in prompts], "settings": _settings(body.get("settings")),
        "temperature": _num(body.get("temperature", 0.0), 0, 2, "temperature"),
        "sustained": _flag(body.get("sustained", True), "sustained"), "sustained_s": _num(body.get("sustained_s", 300.0), 1, MAX_SUSTAINED_S, "sustained_s"),
        "request_timeout_s": _num(body.get("request_timeout_s", 300.0), 1, MAX_REQUEST_TIMEOUT_S, "request_timeout_s"),
        "warmup": _int(body.get("warmup", 2), 0, 5, "warmup"), "cooldown": _flag(body.get("cooldown", True), "cooldown"),
        "cooldown_c": _num(body.get("cooldown_c", 55.0), 30, 90, "cooldown_c"), "cooldown_max_s": _num(body.get("cooldown_max_s", 300.0), 0, 900, "cooldown_max_s"),
        "ceiling_c": None if ceiling is None else _num(ceiling, 50, 120, "ceiling_c"),
        "heat_stop": _flag(body.get("heat_stop", True), "heat_stop"), "while_testing": choice,
        "keep_started": _flag(body.get("keep_started", False), "keep_started"), "keep_models": _flag(body.get("keep_models", False), "keep_models"),
        "take_over": _flag(body.get("take_over", False), "take_over"),
    }
    plan["plan_hash"] = plan_hash(plan)
    return plan


def plan_hash(plan: Mapping) -> str:
    """The hash of a plan's canonical form, `request_id` and any earlier `plan_hash` left out: a retried start with the same plan hashes the same; the same request id with another plan does not."""
    core = {k: v for k, v in plan.items() if k not in ("plan_hash", "request_id")}
    return hashlib.sha256(bc.canonical_json(core).encode("utf-8")).hexdigest()


# --- names and paths ----------------------------------------------------------------------------------------------------------------------------------------------------------

_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)} | {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"})
_BAD_CHARS = re.compile(r'[<>:"|?*\\/\x00-\x1f\x7f]')
RESULT_FILES = ("manifest.json", "requests.jsonl", "samples.jsonl", "summary.json", "progress.json")      # the files of one run folder (benchmarks_store); nothing else comes back


def reserved_name(name: str) -> bool:
    """A name Windows treats as a device (`CON`, `nul.txt`, `COM1`, ...), with or without an extension."""
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    return stem in _RESERVED


def safe_component(name: Any) -> bool:
    """One path piece that is safe on Windows and Linux: plain, not `.` or `..`, no separator or stream colon or wildcard, no trailing dot or space (Windows drops them, so two names could be one), not a device name."""
    if not isinstance(name, str) or not name or len(name) > 200:
        return False
    if name in (".", "..") or _BAD_CHARS.search(name) or name != name.strip() or name.endswith(".") or reserved_name(name):
        return False
    return True


def valid_result_filename(name: Any) -> bool:
    """A file that may come back from a run: exactly one of RESULT_FILES. Used on the Courier (what it lists) and on the main PC (what it accepts); an unlisted name is refused, not cleaned."""
    return isinstance(name, str) and name in RESULT_FILES


def valid_job_id(job_id: Any) -> bool:
    return isinstance(job_id, str) and bool(JOB_ID.match(job_id)) and safe_component(job_id)


def _is_link(path: str) -> bool:
    """True for a symbolic link or a Windows junction (a directory reparse point). A junction is not a symlink to `os.path.islink`, so the file attributes are read."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def contain(root, relative: Any, is_link: Callable[[str], bool] = _is_link) -> Path:
    """`root / relative`, or ValueError. `relative` must be a forward-slash path of safe pieces (no `..`, no absolute path, no drive letter, no device name); then every piece that already
    exists is checked and a link or junction anywhere under `root` is refused, and the real path after resolution must still sit inside the real `root`. Nothing is created.
    (The check and the later open are two steps: the open must also create with O_EXCL / refuse links; see the security review.)"""
    if not isinstance(relative, str) or not relative or len(relative) > 400 or "\\" in relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        raise ValueError("not a relative path")
    pieces = relative.split("/")
    if not all(safe_component(p) for p in pieces):
        raise ValueError("a piece of the path is not allowed")
    base = os.path.realpath(str(root))
    cur = base
    for piece in pieces:
        cur = os.path.join(cur, piece)
        if is_link(cur):
            raise ValueError("a link or junction is in the path")
    real = os.path.realpath(cur)
    try:
        inside = os.path.commonpath([os.path.normcase(base), os.path.normcase(real)]) == os.path.normcase(base)
    except ValueError:                                             # (another drive)
        inside = False
    if not inside or os.path.normcase(real) == os.path.normcase(base):
        raise ValueError("the path leaves its folder")
    return Path(cur)


def job_file(models_root, job_id: str, file_name: str, is_link: Callable[[str], bool] = _is_link) -> Path:
    """Where a model file of a job may be created: `<models>/benchmark-jobs/<job id>/<file>`. Both pieces are re-checked here."""
    if not valid_job_id(job_id) or not GGUF_NAME.match(file_name or ""):
        raise ValueError("not a valid job file")
    return contain(models_root, f"{JOBS_SUBDIR}/{job_id}/{file_name}", is_link)


def result_file(runs_root, run_id: str, name: str, is_link: Callable[[str], bool] = _is_link) -> Path:
    """Where a collected result file of run `run_id` may be written on the main PC (`<benchmarks>/<run id>/<name>`)."""
    if not bc.valid_run_id(run_id) or not safe_component(run_id) or not valid_result_filename(name):
        raise ValueError("not a valid result file")
    return contain(runs_root, f"{run_id}/{name}", is_link)


# --- the job states -----------------------------------------------------------------------------------------------------------------------------------------------------------

J_QUEUED, J_PREPARING, J_RECEIVING, J_RUNNING, J_FINISHED, J_FAILED, J_INTERRUPTED, J_CANCELLED, J_COLLECTED = (
    "queued", "preparing", "receiving", "running", "finished", "failed", "interrupted", "cancelled", "collected")
JOB_STATES = (J_QUEUED, J_PREPARING, J_RECEIVING, J_RUNNING, J_FINISHED, J_FAILED, J_INTERRUPTED, J_CANCELLED, J_COLLECTED)
ACTIVE_STATES = (J_QUEUED, J_PREPARING, J_RECEIVING, J_RUNNING)                   # the device is reserved
ENDED_STATES = (J_FINISHED, J_FAILED, J_INTERRUPTED, J_CANCELLED)                   # results (maybe partial) wait to be collected
ACTOR_COURIER, ACTOR_OWNER = "courier", "owner"
# The owner (the main PC that started the job, proving it with the job token) can do exactly one thing to a job's state: acknowledge its results. Cancelling is a request the Courier carries
# out (it stops the engine first, then moves the job): see `cancel_requested` in the protocol. Everything else the Courier decides.
_JOB_MOVES: Mapping = {
    J_QUEUED: {J_PREPARING: ACTOR_COURIER, J_CANCELLED: ACTOR_COURIER, J_FAILED: ACTOR_COURIER, J_INTERRUPTED: ACTOR_COURIER},
    J_PREPARING: {J_RECEIVING: ACTOR_COURIER, J_RUNNING: ACTOR_COURIER, J_CANCELLED: ACTOR_COURIER, J_FAILED: ACTOR_COURIER, J_INTERRUPTED: ACTOR_COURIER},
    J_RECEIVING: {J_PREPARING: ACTOR_COURIER, J_RUNNING: ACTOR_COURIER, J_CANCELLED: ACTOR_COURIER, J_FAILED: ACTOR_COURIER, J_INTERRUPTED: ACTOR_COURIER},
    J_RUNNING: {J_FINISHED: ACTOR_COURIER, J_CANCELLED: ACTOR_COURIER, J_FAILED: ACTOR_COURIER, J_INTERRUPTED: ACTOR_COURIER},
    J_FINISHED: {J_COLLECTED: ACTOR_OWNER}, J_FAILED: {J_COLLECTED: ACTOR_OWNER}, J_INTERRUPTED: {J_COLLECTED: ACTOR_OWNER}, J_CANCELLED: {J_COLLECTED: ACTOR_OWNER},
}                                                                                  # `collected` never moves again
REASON_STATES = (J_FAILED, J_INTERRUPTED, J_CANCELLED)                              # these say why (`reason`)


def can_move_job(old: str, new: str, actor: str) -> bool:
    return _JOB_MOVES.get(old, {}).get(new) == actor


def move_job(old: str, new: str, actor: str, reason: str = "") -> str:
    """The new state, or ValueError for a move that isn't in the table, one the actor may not make, or an ended job with no reason."""
    if old not in JOB_STATES or new not in JOB_STATES:
        raise ValueError("unknown job state")
    if new not in _JOB_MOVES.get(old, {}):
        raise ValueError(f"a job cannot go from {old} to {new}")
    if not can_move_job(old, new, actor):
        raise ValueError(f"{actor} cannot move a job from {old} to {new}")
    if new in REASON_STATES and not reason:
        raise ValueError(f"a {new} job needs a reason")
    return new


# --- compatibility ------------------------------------------------------------------------------------------------------------------------------------------------------------

# What the Courier reports in `/courier/v1/report` under `courier.benchmarks`: {"protocol_version": 1, "capabilities": [...]}. The main PC refuses a Courier that lacks any of the
# capabilities its plan needs, whatever its build number says.
CAP_START, CAP_STATUS, CAP_CANCEL, CAP_HEARTBEAT, CAP_COLLECT, CAP_ACK = "start.v1", "status.v1", "cancel.v1", "heartbeat.v1", "collect.v1", "acknowledge.v1"
CAP_RECEIVE_MODELS, CAP_KEEP_AWAKE, CAP_BATTERY_STOP, CAP_START_SERVICES = "receive-models.v1", "keep-awake.v1", "battery-stop.v1", "start-services.v1"
BASE_CAPABILITIES = (CAP_START, CAP_STATUS, CAP_CANCEL, CAP_HEARTBEAT, CAP_COLLECT, CAP_ACK)


def required_capabilities(plan: Mapping, models_missing: bool = False, collect: bool = True) -> tuple:
    """What a Courier must report to carry out `plan`: the basics always, plus model receiving when a model has to be sent, and starting services for `start_normal`.
    `collect=False` leaves out collecting and acknowledging results (a caller that leaves the run on the Courier, as the step-2 client does)."""
    need = [c for c in BASE_CAPABILITIES if collect or c not in (CAP_COLLECT, CAP_ACK)] + [CAP_KEEP_AWAKE]
    if models_missing:
        need.append(CAP_RECEIVE_MODELS)
    if plan.get("while_testing") == bg.BACKGROUND_START_NORMAL:
        need.append(CAP_START_SERVICES)
    return tuple(need)


def missing_capabilities(reported: Any, required: Iterable[str]) -> list:
    """The required capabilities a Courier's report lacks (all of them when it reports no benchmark block, or another protocol version)."""
    required = list(required)
    if not isinstance(reported, dict) or reported.get("protocol_version") != PROTOCOL_VERSION:
        return required
    have = reported.get("capabilities")
    have = set(have) if isinstance(have, list) and all(isinstance(c, str) for c in have) else set()
    return [c for c in required if c not in have]
