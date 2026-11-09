"""Benchmarks: the measurement rules as pure code (contract: docs/roadmap/benchmarks-measurement-contract.md, v1).

No network, no processes, no files, no clock: every time is passed in, so each rule is fast to test. The result folder lives in `benchmarks_store.py`. Nothing here scores or ranks:
it reports what was measured, and a number that cannot be measured says so (`Availability`) and is never guessed or derived from another source. Every number carries its unit in its
name (`_ms`, `_s`, `_mb`, `_pct`, `_tok_s`).
"""
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, fields
from fractions import Fraction
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1
NOT_REPORTED_TEXT = "not reported"
NOT_ENOUGH_REQUESTS = "not enough requests"
P95_MIN_REQUESTS = 20                                         # contract section 4: p95 only from this many requests
HEADROOM_GREEN_MB = 4096                                      # 4 GB
RUN_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,80}\Z")         # a run id is also a folder name: nothing else is accepted


# --- 8. Availability: used by every metric ---------------------------------------------------------------------------------------------------------------------------------

MEASURED, NOT_MEASURED, NOT_REPORTED = "measured", "not_measured", "not_reported"


@dataclass(frozen=True)
class Availability:
    """`measured` (has a value), `not_measured` (cannot be, with the reason) or `not_reported` (the engine gave no figure for it)."""
    status: str
    value: Any = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == MEASURED

    def to_dict(self) -> dict:
        out: dict = {"status": self.status}
        if self.status == MEASURED:
            out["value"] = self.value
        if self.reason:
            out["reason"] = self.reason
        return out

    @staticmethod
    def from_dict(d: dict) -> "Availability":
        st = d.get("status")
        if st not in (MEASURED, NOT_MEASURED, NOT_REPORTED):
            return Availability(NOT_MEASURED, reason="unknown status in the file")
        return Availability(st, d.get("value"), str(d.get("reason", "")))


def measured(value: Any) -> Availability:
    return Availability(MEASURED, value)


def not_measured(reason: str) -> Availability:
    return Availability(NOT_MEASURED, reason=reason)


NOT_REPORTED_AVAILABILITY = Availability(NOT_REPORTED)


def not_reported_dict() -> dict:
    return NOT_REPORTED_AVAILABILITY.to_dict()


# --- 1. Identifiers ---------------------------------------------------------------------------------------------------------------------------------------------------------

def _normalize(x: Any) -> Any:
    """Make equal settings serialize equally: floats written one way (a whole float is an integer, others 12 significant digits), no NaN or infinity."""
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError("a setting cannot be NaN or infinity")
        return int(x) if x == int(x) else float(f"{x:.12g}")
    if isinstance(x, dict):
        return {str(k): _normalize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_normalize(v) for v in x]
    raise TypeError(f"cannot write {type(x).__name__} into a setup id")


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace, ASCII, floats one way."""
    return json.dumps(_normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def model_id(sha256_hex: str, size_bytes: int) -> str:
    """The exact bytes tested: `sha256:<hex>:<size>`."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256_hex or ""):
        raise ValueError("not a sha256 hex digest")
    return f"sha256:{sha256_hex}:{int(size_bytes)}"


def setup_id(setup: dict) -> str:
    """A short hash of the canonical JSON of what was asked and what was resolved (kind, test-set version, requested and resolved settings, levels, requests per level, prompt
    lengths, warm-up count, temperature, sampling, engine build, profile). Any changed field gives another id."""
    return hashlib.sha256(canonical_json(setup).encode("ascii")).hexdigest()[:16]


def new_run_id(start_epoch_s: float, unique: Optional[str] = None) -> str:
    """A UUID plus the start time: `20261027T101500Z-<uuid hex>`. Safe as a folder name. `unique` is for tests."""
    stamp = _utc_stamp(start_epoch_s)
    return f"{stamp}-{unique or uuid.uuid4().hex}"


def _utc_stamp(epoch_s: float) -> str:
    import time
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(epoch_s))


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_PATTERN.match(run_id or "")) and run_id not in (".", "..")


# --- 2. States --------------------------------------------------------------------------------------------------------------------------------------------------------------

RUN_RUNNING, RUN_COMPLETED, RUN_FAILED, RUN_INTERRUPTED, RUN_CANCELLED, RUN_INVALIDATED = "running", "completed", "failed", "interrupted", "cancelled", "invalidated"
RUN_STOPPED_MEMORY, RUN_STOPPED_TIME = "stopped_for_memory", "stopped_too_long"        # the PC was protected (memory ran low) / the model took longer than its time limit: neither failed nor cancelled
RUN_STATES_FINAL = (RUN_COMPLETED, RUN_FAILED, RUN_INTERRUPTED, RUN_CANCELLED, RUN_INVALIDATED, RUN_STOPPED_MEMORY, RUN_STOPPED_TIME)
RUN_STATES_WITH_REASON = (RUN_FAILED, RUN_INTERRUPTED, RUN_INVALIDATED, RUN_STOPPED_MEMORY, RUN_STOPPED_TIME)
RUN_STATES_STOPPED = (RUN_STOPPED_MEMORY, RUN_STOPPED_TIME)
RUN_STATES = ("queued", RUN_RUNNING) + RUN_STATES_FINAL
_RUN_MOVES = {"queued": {RUN_RUNNING, RUN_CANCELLED}, RUN_RUNNING: set(RUN_STATES_FINAL)}      # a final state never moves again

STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_SKIPPED, STEP_FAILED, STEP_INTERRUPTED = "pending", "running", "done", "skipped", "failed", "interrupted"
STEP_STATES = (STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_SKIPPED, STEP_FAILED, STEP_INTERRUPTED)
_STEP_MOVES = {STEP_PENDING: {STEP_RUNNING, STEP_SKIPPED, STEP_INTERRUPTED},
               STEP_RUNNING: {STEP_DONE, STEP_FAILED, STEP_INTERRUPTED, STEP_SKIPPED}}


def can_move_run(old: str, new: str) -> bool:
    return new in _RUN_MOVES.get(old, set())


def can_move_step(old: str, new: str) -> bool:
    return new in _STEP_MOVES.get(old, set())


def move_run(old: str, new: str, reason: str = "") -> str:
    """The new run state, or ValueError for an illegal move or a failed / interrupted / invalidated run with no reason."""
    if not can_move_run(old, new):
        raise ValueError(f"a run cannot go from {old} to {new}")
    if new in RUN_STATES_WITH_REASON and not reason:
        raise ValueError(f"a {new} run needs a reason")
    return new


def step_plan(levels: Iterable[int], sustained: bool = True, cooldown: bool = True) -> list:
    """The steps of one model, in order: load, warmup, one `level:<n>` per ticked concurrency, sustained, unload, cooldown."""
    plan = ["load", "warmup"] + [f"level:{int(n)}" for n in levels]
    if sustained:
        plan.append("sustained")
    plan.append("unload")
    if cooldown:
        plan.append("cooldown")
    return plan


def steps_total(levels: Iterable[int], sustained: bool = True, cooldown: bool = True) -> int:
    return len(step_plan(levels, sustained, cooldown))


def steps_done(step_states: dict) -> int:
    """How many steps finished (`done`); a skipped, failed or interrupted step is not counted."""
    return sum(1 for s in step_states.values() if s == STEP_DONE)


# --- 4. Statistics ----------------------------------------------------------------------------------------------------------------------------------------------------------

def percentile(values: Iterable[float], p) -> float:
    """Nearest rank: `sorted[ceil(p/100 * n) - 1]` (p in 0..100; p of 0 gives the smallest). Exact arithmetic, so 95% of 20 is rank 19 with no float wobble."""
    data = sorted(values)
    if not data:
        raise ValueError("no values")
    rank = math.ceil(Fraction(str(p)) * len(data) / 100)
    return data[min(max(rank, 1), len(data)) - 1]


def level_statistics(values: Iterable[float]) -> dict:
    """n, mean, min, max, p50, p95; p95 only when n >= 20, else `not enough requests`. An empty list gives n of 0 and no numbers."""
    data = [float(v) for v in values]
    n = len(data)
    if n == 0:
        return {"n": 0, "mean": None, "min": None, "max": None, "p50": None, "p95": NOT_ENOUGH_REQUESTS}
    return {"n": n, "mean": sum(data) / n, "min": min(data), "max": max(data), "p50": percentile(data, 50),
            "p95": percentile(data, 95) if n >= P95_MIN_REQUESTS else NOT_ENOUGH_REQUESTS}


# --- 3. Per-request records -------------------------------------------------------------------------------------------------------------------------------------------------

STATUS_OK, STATUS_ERROR, STATUS_TIMEOUT = "ok", "error", "timeout"
PROMPT_CLASSES = ("short", "medium", "long")


@dataclass
class RequestRecord:
    """One request. Times are seconds from the run's start on MultiProxy's own stopwatch (`time.monotonic()`). The `prompt_*` / `predicted_*` fields are the engine's own timing report
    (llama.cpp `timings`: prompt_n, prompt_ms, predicted_n, predicted_ms); None means the engine gave none."""
    request_id: str
    level: int
    prompt_id: str = ""
    prompt_length_class: str = "medium"
    warmup: bool = False
    t_submit_s: float = 0.0
    t_first_token_s: Optional[float] = None                    # None: the engine could not stream
    t_done_s: Optional[float] = None
    status: str = STATUS_OK
    error: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    prompt_n: Optional[int] = None
    prompt_ms: Optional[float] = None
    predicted_n: Optional[int] = None
    predicted_ms: Optional[float] = None
    finish_reason: str = ""
    answer: str = ""                                          # what the model wrote (at most 4,000 characters): the automated checks read it

    def ttft_ms(self) -> Availability:
        if self.t_first_token_s is None or self.t_first_token_s < self.t_submit_s:
            return NOT_REPORTED_AVAILABILITY
        return measured((self.t_first_token_s - self.t_submit_s) * 1000.0)

    def completion_time_s(self) -> Availability:
        if self.t_done_s is None or self.t_done_s < self.t_submit_s:
            return NOT_REPORTED_AVAILABILITY
        return measured(self.t_done_s - self.t_submit_s)

    def prompt_tok_s(self) -> Availability:
        """From the engine's timing report only; never from the stopwatch."""
        if not self.prompt_n or not self.prompt_ms or self.prompt_ms <= 0:
            return NOT_REPORTED_AVAILABILITY
        return measured(self.prompt_n / self.prompt_ms * 1000.0)

    def generation_tok_s(self) -> Availability:
        if not self.predicted_n or not self.predicted_ms or self.predicted_ms <= 0:
            return NOT_REPORTED_AVAILABILITY
        return measured(self.predicted_n / self.predicted_ms * 1000.0)

    def to_row(self) -> dict:
        """The line written to `requests.jsonl`: the stored fields plus the four derived metrics (a number, or null for not reported)."""
        row = {f.name: getattr(self, f.name) for f in fields(self)}
        for key, av in (("ttft_ms", self.ttft_ms()), ("completion_time_s", self.completion_time_s()),
                        ("prompt_tok_s", self.prompt_tok_s()), ("generation_tok_s", self.generation_tok_s())):
            row[key] = av.value if av.ok else None
        return row

    @staticmethod
    def from_row(row: dict) -> "RequestRecord":
        """Unknown keys (added by a later version, and the derived metrics) are ignored; a missing key takes its default."""
        names = {f.name for f in fields(RequestRecord)}
        return RequestRecord(**{k: v for k, v in row.items() if k in names})


def max_overlap(records: Iterable[RequestRecord]) -> int:
    """The most requests in flight at once (every request counts, failed ones too). At an equal time a finish is counted before a start."""
    events = []
    for r in records:
        if r.t_done_s is None:
            continue
        events.append((r.t_submit_s, 1))
        events.append((r.t_done_s, -1))
    events.sort(key=lambda e: (e[0], e[1]))                    # -1 sorts before +1
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def level_summary(records: Iterable[RequestRecord], level: int, slots: Optional[int] = None) -> dict:
    """The per-level numbers (section 4) from the records of that level. Warm-up requests are left out of every statistic; errors and timeouts are counted, not averaged."""
    mine = [r for r in records if r.level == level and not r.warmup]
    ok = [r for r in mine if r.status == STATUS_OK and r.t_done_s is not None]

    def stat(get) -> dict:
        return level_statistics(a.value for a in (get(r) for r in ok) if a.ok)

    out = {"level": level, "slots": slots, "completed": len(ok),
           "failed": sum(1 for r in mine if r.status == STATUS_ERROR), "timed_out": sum(1 for r in mine if r.status == STATUS_TIMEOUT),
           "actual_overlap": max_overlap(mine),
           "ttft_ms": stat(RequestRecord.ttft_ms), "completion_time_s": stat(RequestRecord.completion_time_s),
           "prompt_tok_s": stat(RequestRecord.prompt_tok_s), "generation_tok_s": stat(RequestRecord.generation_tok_s),
           "percentile_method": "nearest rank: sorted[ceil(p/100*n)-1]"}
    interval = (max(r.t_done_s for r in ok) - min(r.t_submit_s for r in ok)) if ok else 0.0
    if not ok:
        why = "no completed requests"
    elif interval <= 0:
        why = "no time passed"
    else:
        why = ""
    if why:
        out["aggregate_tok_s"] = out["requests_per_s"] = not_measured(why).to_dict()
        return out
    out["requests_per_s"] = measured(len(ok) / interval).to_dict()
    if all(r.completion_tokens is not None for r in ok):
        out["aggregate_tok_s"] = measured(sum(r.completion_tokens for r in ok) / interval).to_dict()
    else:
        out["aggregate_tok_s"] = not_reported_dict()
    return out


# --- 6. The headroom estimate -----------------------------------------------------------------------------------------------------------------------------------------------

SKYRIM_STATES = ("running", "closed", "typed", "history", "not_applicable")       # `not_applicable`: the device does not run the game (contract 12b): no Skyrim term
DOT_GREEN, DOT_AMBER, DOT_RED, DOT_GREY = "green", "amber", "red", "grey"
BG_NONE, BG_LIST, BG_IDLE_READING, BG_UNKNOWN = "none", "list", "idle_reading", "unknown"


def device_of(manifest: dict) -> dict:
    """Which device a run was made on, from its manifest: `{"kind": "this"}` for a run made on this PC (the manifest says `"this"`), else `{"kind": "courier", "id", "name"}` from the
    device facts a Courier wrote. Anything else reads as this PC. The name is cut to 80 characters (it came from another PC)."""
    dev = (manifest or {}).get("device")
    if isinstance(dev, dict) and dev.get("kind") == "courier":
        return {"kind": "courier", "id": str(dev.get("id") or "")[:80] or None, "name": str(dev.get("name") or "")[:80] or None}
    return {"kind": "this", "id": None, "name": None}


def model_vram_delta_mb(highest_overall_mb: Optional[float], idle_before_load_mb: Optional[float]) -> Optional[int]:
    """Highest sampled use minus the card's use before the load, in whole MB. Never negative (another program freeing memory during the run must not make the model look free);
    None when either reading is missing. `idle_before_load` already holds the background models, so this stays the tested model's own cost."""
    if highest_overall_mb is None or idle_before_load_mb is None:
        return None
    return max(0, _round_mb(highest_overall_mb - idle_before_load_mb))


def _round_mb(x: float) -> int:
    return int(math.floor(x + 0.5))


def background_vram_mb(background: Optional[Iterable[dict]], gpu_index, vram_idle_before_load_mb: Optional[float] = None, card_own_idle_mb: Optional[float] = None) -> tuple:
    """`(megabytes or None, source)`: what the background models hold on this card (contract 12).

    `background` is the run's list: dicts with `vram_mb`, a map from `gpu_index` (as text) to what the model holds on that card, or None when MultiProxy has no figure for it.
      * no background at all -> `(0, "none")`;
      * at least one entry has a figure for this card -> the sum of the figures, source `list` (an entry with no figure adds nothing and `background_unmeasured` counts it in the estimate);
      * no entry has a figure -> `vram_idle_before_load` minus the card's own idle (what it held before any of these models), source `idle_reading`, only when both are known;
      * otherwise `(None, "unknown")`: the estimate then has not enough data (grey) rather than a guess.
    """
    items = [b for b in (background or []) if isinstance(b, dict)]
    if not items:
        return 0, BG_NONE
    key = str(gpu_index)
    figures = [(b.get("vram_mb") or {}).get(key) for b in items]
    figures = [f for f in figures if isinstance(f, (int, float)) and not isinstance(f, bool)]
    if figures:
        return _round_mb(sum(figures)), BG_LIST
    if vram_idle_before_load_mb is not None and card_own_idle_mb is not None:
        return max(0, _round_mb(vram_idle_before_load_mb - card_own_idle_mb)), BG_IDLE_READING
    return None, BG_UNKNOWN


def headroom_estimate(card_total_vram_mb: Optional[float], skyrim_peak_mb: Optional[float], model_vram_delta_mb_: Optional[float],
                      skyrim_state: str, usual_skyrim_mb: Optional[float] = None, background_vram_mb_: Optional[float] = 0, background_source: str = BG_NONE,
                      background_unmeasured: int = 0) -> dict:
    """`card_total_vram - skyrim_peak - background_vram - model_vram_delta` for one GPU, always `kind: "estimate"`. Whole MB are used for the dot and for the figure, so a rounded
    figure can never sit on the other side of the 4 GB edge from its dot: green is 4096 MB or more, amber is 0 up to 4095, red is below 0, grey is not enough data. `skyrim_state`
    `closed` means a skyrim_peak of 0; `not_applicable` (a device that does not run the game) leaves the Skyrim term out. The background term is 0 with no background, the sum of the
    list's figures, or the idle reading (see `background_vram_mb`); when it cannot be known the estimate is grey. The usual figure is shown beside the estimate and never used in it."""
    if skyrim_state not in SKYRIM_STATES:
        raise ValueError(f"skyrim_state must be one of {SKYRIM_STATES}")
    peak = 0 if skyrim_state in ("closed", "not_applicable") else skyrim_peak_mb
    out = {"kind": "estimate", "skyrim_state": skyrim_state, "card_total_vram_mb": card_total_vram_mb, "skyrim_peak_mb": None if skyrim_state == "not_applicable" else peak,
           "background_vram_mb": background_vram_mb_, "background_source": background_source, "background_unmeasured": background_unmeasured,
           "model_vram_delta_mb": model_vram_delta_mb_, "usual_skyrim_mb": usual_skyrim_mb}
    if card_total_vram_mb is None or peak is None or model_vram_delta_mb_ is None or background_vram_mb_ is None:
        out.update(headroom_mb=None, headroom_gb=None, dot=DOT_GREY)
        return out
    mb = _round_mb(card_total_vram_mb - peak - background_vram_mb_ - model_vram_delta_mb_)
    out["headroom_mb"] = mb
    out["headroom_gb"] = math.floor(mb / 1024 * 100) / 100 + 0.0       # cut, not rounded: 4095 MB reads 3.99 (amber), never 4.0 (green); +0.0 so zero is never -0.0
    out["dot"] = DOT_GREEN if mb >= HEADROOM_GREEN_MB else (DOT_AMBER if mb >= 0 else DOT_RED)
    return out


def headroom_per_gpu(gpus: Iterable[dict], skyrim_peak_mb: Optional[float], skyrim_state: str, usual_skyrim_mb: Optional[float] = None,
                     background: Optional[Iterable[dict]] = None) -> list:
    """`gpus`: dicts with `gpu_index`, `vram_total_mb`, `vram_idle_before_load_mb`, `vram_highest_overall_mb`, and optionally `card_own_idle_mb` (the card's use before any background
    model, when it was read). One estimate per card, tagged with its `gpu_index`. `background`: the run's background list (see `background_vram_mb`)."""
    background = list(background or [])
    out = []
    for g in gpus:
        delta = model_vram_delta_mb(g.get("vram_highest_overall_mb"), g.get("vram_idle_before_load_mb"))
        bg_mb, source = background_vram_mb(background, g.get("gpu_index"), g.get("vram_idle_before_load_mb"), g.get("card_own_idle_mb"))
        unmeasured = sum(1 for b in background if not isinstance((b.get("vram_mb") or {}).get(str(g.get("gpu_index"))), (int, float))) if source == BG_LIST else 0
        est = headroom_estimate(g.get("vram_total_mb"), skyrim_peak_mb, delta, skyrim_state, usual_skyrim_mb, bg_mb, source, unmeasured)
        est["gpu_index"] = g.get("gpu_index")
        out.append(est)
    return out


# --- 7. The sustained-run verdict -------------------------------------------------------------------------------------------------------------------------------------------

SUSTAINED_RULE = {"baseline_window_s": 60, "window_s": 60, "slowdown_below_pct_of_baseline": 80, "baseline_stat": "p50 (nearest rank)", "slowdown_window": "any later 60 s window"}


def sustained_verdict(samples: Iterable[tuple], duration_s: float = 300.0, ran: bool = True, failed_reason: str = "", interrupted_reason: str = "",
                      baseline_window_s: float = 60.0, window_s: float = 60.0, threshold_pct: int = 80) -> dict:
    """`samples`: (completion time in seconds from the start of the sustained step, generation tok/s or None) for each request that completed.

    Baseline = the p50 of generation tok/s of requests that complete in the first `baseline_window_s`. Windows: back-to-back `window_s` slices that start at the end of the baseline
    window and lie fully inside `duration_s`; a window with no completed request has no median and is counted in `empty_windows`, not judged. Slowdown = a window whose p50 is below
    `threshold_pct` percent of the baseline; `after_minutes` is when that first window began. Order of outcomes: `not_run`, `failed`, `interrupted`, `slowdown_detected`, `completed`."""
    rule = dict(SUSTAINED_RULE, duration_s=duration_s, baseline_window_s=baseline_window_s, window_s=window_s, slowdown_below_pct_of_baseline=threshold_pct)
    pts = sorted((t, v) for t, v in samples if v is not None)
    out = {"rule": rule, "baseline_tok_s": None, "windows": [], "empty_windows": 0, "after_minutes": None, "reason": ""}
    if not ran:
        return dict(out, status="not_run")
    base_vals = [v for t, v in pts if t < baseline_window_s]
    if base_vals:
        out["baseline_tok_s"] = percentile(base_vals, 50)
    start = baseline_window_s
    while base_vals and start + window_s <= duration_s + 1e-9:
        vals = [v for t, v in pts if start <= t < start + window_s]
        if not vals:
            out["empty_windows"] += 1
            out["windows"].append({"start_s": start, "n": 0, "p50_tok_s": None, "below_threshold": False})
        else:
            p50 = percentile(vals, 50)
            below = p50 * 100 < out["baseline_tok_s"] * threshold_pct
            out["windows"].append({"start_s": start, "n": len(vals), "p50_tok_s": p50, "below_threshold": below})
        start += window_s
    slowed = next((w for w in out["windows"] if w["below_threshold"]), None)
    if failed_reason:
        return dict(out, status="failed", reason=failed_reason)
    if interrupted_reason:
        return dict(out, status="interrupted", reason=interrupted_reason)
    if not base_vals:
        return dict(out, status="failed", reason="no request finished in the first 60 s")
    if slowed:
        return dict(out, status="slowdown_detected", after_minutes=round(slowed["start_s"] / 60.0, 1))
    return dict(out, status="completed")


# --- 10. Automated checks (reserved structure) --------------------------------------------------------------------------------------------------------------------------------

def new_check(check_id: str, title: str, passed: int, total: int, failures: Optional[list] = None) -> dict:
    """One entry of `checks[]`: each failure is `{"request_id": ..., "line": <what the model wrote>}`. The fixed test set is a later item."""
    if passed > total or passed < 0:
        raise ValueError("passed must be between 0 and total")
    return {"id": check_id, "title": title, "passed": passed, "total": total, "failures": list(failures or [])}
