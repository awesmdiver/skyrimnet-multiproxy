"""Time breakdown (Speeds tab, 2026-10-02): for each job and model, a typical line split into waiting in line, reading the prompt up to the first word, and writing;
the writing speed, how much of the prompt a local model remembered, tokens, and cost. Everything is worked out from saved Speeds history rows and nothing is made up:
a number is only shown when the rows really have it ("not reported" otherwise), and rows from before these facts were recorded still count for what they do have.
Nothing from MultiProxy is imported here; multiproxy.py hands in the rows and how to label them.

A row is a dict with: model, provider, job (SkyrimNet's key, or None), roles_json, success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note,
timings_json, queue_wait. `elapsed` is the model's own time (it does not include the wait in line); a streamed reply has `first_token` (seconds to the first word).
"""
import json
from typing import Any, Callable, Optional

CHARS_PER_TOKEN = 4.0                 # only to ESTIMATE tokens from a reply's length when the model didn't say (shown with "≈")
WORDS_PER_TOKEN = 0.75
MIN_WRITING_SECONDS = 0.25            # a reply that finished faster than this after its first word gives no honest speed
NOTE_FREE = "free"
NOTE_INCLUDED = "included in your subscription"
NOTE_NONE = "not reported"
COST_FREE, COST_SUBSCRIPTION, COST_NONE = "free", "subscription", "not reported"


def median(values) -> Optional[float]:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _r(v: Optional[float], digits: int = 2) -> Optional[float]:
    return None if v is None else round(v, digits)


def writing_speed(row: dict) -> Optional[tuple]:
    """(tokens per second, estimated?) for one successful streamed reply, or None when it can't be told honestly."""
    if not row.get("success") or row.get("first_token") is None:
        return None
    generating = (row.get("elapsed") or 0) - row["first_token"]
    if generating < MIN_WRITING_SECONDS:
        return None
    if row.get("out_tokens"):
        return row["out_tokens"] / generating, False
    if row.get("out_chars"):
        return (row["out_chars"] / CHARS_PER_TOKEN) / generating, True
    return None


def remembered_pct(row: dict) -> Optional[float]:
    """The share of the prompt a local llama.cpp already had (its own `timings`): cache / (cache + new), or None."""
    try:
        t = json.loads(row.get("timings_json") or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(t, dict) or t.get("cache_n") is None or t.get("prompt_n") is None:
        return None
    whole = t["cache_n"] + t["prompt_n"]
    return 100.0 * t["cache_n"] / whole if whole > 0 else None


def _row_job(row: dict) -> Optional[str]:
    return row.get("job") or None


def _cost_kind(rows: list) -> str:
    """How to say a group's cost when it has no number: free, subscription, or not reported (the most common note wins; a number always wins)."""
    notes = [r.get("cost_note") for r in rows if r.get("cost_note")]
    if not notes:
        return COST_NONE
    top = max(set(notes), key=notes.count)
    return COST_FREE if top == NOTE_FREE else COST_SUBSCRIPTION if top == NOTE_INCLUDED else COST_NONE


def group_stats(rows: list) -> dict:
    ok = [r for r in rows if r.get("success")]
    streamed = [r for r in ok if r.get("first_token") is not None]
    waits = [r["queue_wait"] for r in ok if r.get("queue_wait") is not None]
    wait = median(waits)
    read = median([r["first_token"] for r in streamed])
    write = median([r["elapsed"] - r["first_token"] for r in streamed])
    whole = median([r["elapsed"] for r in ok if r.get("first_token") is None])              # a reply that wasn't streamed has no start to split at
    total = median([(r.get("queue_wait") or 0.0) + r["elapsed"] for r in ok])
    speeds = [s for s in (writing_speed(r) for r in ok) if s]
    tps = median([s[0] for s in speeds])
    estimated = bool(speeds) and sum(1 for s in speeds if s[1]) * 2 > len(speeds)
    mem = median([m for m in (remembered_pct(r) for r in ok) if m is not None])
    tin = [r["tokens_in"] for r in rows if isinstance(r.get("tokens_in"), int)]
    tout = [r["out_tokens"] for r in rows if isinstance(r.get("out_tokens"), int)]
    costs = [r["cost"] for r in rows if isinstance(r.get("cost"), (int, float)) and r.get("cost_note") not in (NOTE_FREE, NOTE_INCLUDED)]
    paid_unreported = sum(1 for r in ok if r.get("cost_note") == NOTE_NONE)
    return {
        "calls": len(rows), "ok": len(ok),
        "wait": _r(wait, 2), "read": _r(read, 2), "write": _r(write, 2), "whole": _r(whole, 2), "total": _r(total, 2),
        "waitKnown": bool(waits),
        "tokensPerSecond": _r(tps, 1), "wordsPerSecond": _r(tps * WORDS_PER_TOKEN, 1) if tps is not None else None, "speedEstimated": estimated,
        "remembered": _r(mem, 0),
        "tokensIn": sum(tin) if tin else None, "tokensOut": sum(tout) if tout else None,
        "costTypical": _r(median(costs), 6), "costTotal": _r(sum(costs), 6) if costs else None, "costKind": _cost_kind(rows),
        "costRows": len(costs), "unreportedRows": paid_unreported,
    }


def aggregate(rows: list, *, label_of: Callable[[Optional[str]], str], where_of: Callable[[dict], str], keep: Callable[[str], bool], order_of: Callable[[Optional[str]], int]) -> dict:
    """Group rows by job, model and provider (the same model under two providers is two groups): {"groups": [...], "total": {...}}. `where_of(row)` says where a row ran (for the device filter, `keep(where)`); `label_of(job key or None)`
    is the name shown for a job; rows with no job recorded (older ones) are grouped under whatever role label they carried, else "Unknown"."""
    by: dict = {}
    for r in rows:
        if not keep(where_of(r)):
            continue
        job = _row_job(r)
        if job is None:
            try:
                roles = json.loads(r.get("roles_json") or "null")
            except (TypeError, ValueError):
                roles = None
            job_label = roles[0] if isinstance(roles, list) and roles else "Unknown"
            key = ("label", job_label)
        else:
            key = ("job", job)
            job_label = label_of(job)
        by.setdefault((key, r["model"], r.get("provider") or None), {"job": job, "label": job_label, "model": r["model"], "provider": r.get("provider"), "rows": []})["rows"].append(r)
    groups = []
    for g in by.values():
        stats = group_stats(g["rows"])
        groups.append({"job": g["job"], "label": g["label"], "model": g["model"], "provider": g["provider"], "where": where_of(g["rows"][0]), **stats})
    groups.sort(key=lambda g: (order_of(g["job"]) if g["job"] else 10_000, g["label"].lower(), -g["calls"], g["model"], g["provider"] or ""))
    paid = [g for g in groups if g["costTotal"] is not None]
    total = sum(g["costTotal"] for g in paid)
    unreported = sorted({g["model"] for g in groups if g["unreportedRows"] and g["costTotal"] is None})
    return {
        "groups": groups,
        "total": {"cost": _r(total, 6) if paid else None, "countedModels": sorted({g["model"] for g in paid}), "notCounted": unreported},
    }


# ---- The same answer, worked out as the rows stream past (2026-10-02) ----
# `aggregate` above needs every row of the range as a dict in memory (866 MB at a million rows). `aggregate_stream` takes rows one at a time (tuples, in the
# column order of STREAM_COLUMNS), keeps only the few numbers each group needs, and gives the same answer; the tests hold the two equal on random data.
# The row a stream carries: model, provider, job, the first role label (for a row with no job: SQLite reads it out of roles_json), success, elapsed, first_token,
# out_tokens, out_chars, tokens_in, cost, cost_note, remembered % (SQLite works it out of timings_json: cache / (cache + new) of a local llama.cpp's own numbers),
# queue_wait. STREAM_SELECT is the SQL that makes that row from a speed_history row (and STREAM_SELECT_PLAIN the same without SQLite's JSON functions: the raw
# roles_json / timings_json, which `rows_from_raw` turns into the same row in Python).
_CACHE, _NEW = "json_extract(timings_json, '$.cache_n')", "json_extract(timings_json, '$.prompt_n')"
STREAM_SELECT = (
    "model, provider, job, "
    "CASE WHEN COALESCE(job, '') = '' AND roles_json IS NOT NULL AND json_valid(roles_json) THEN json_extract(roles_json, '$[0]') END, "
    "success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note, "
    f"CASE WHEN timings_json IS NOT NULL AND json_valid(timings_json) AND {_CACHE} IS NOT NULL AND {_NEW} IS NOT NULL AND ({_CACHE} + {_NEW}) > 0 "
    f"THEN 100.0 * {_CACHE} / ({_CACHE} + {_NEW}) END, queue_wait"
)
STREAM_SELECT_PLAIN = "model, provider, job, roles_json, success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note, timings_json, queue_wait"


def rows_from_raw(rows):
    """Rows made with STREAM_SELECT_PLAIN, turned into the stream row (for a SQLite without JSON functions)."""
    for model, provider, job, roles_json, success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note, timings_json, queue_wait in rows:
        label = None
        if not job:
            try:
                parsed = json.loads(roles_json or "null")
            except (TypeError, ValueError):
                parsed = None
            label = parsed[0] if isinstance(parsed, list) and parsed else None
        yield (model, provider, job, label, success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note,
               remembered_pct({"timings_json": timings_json}) if timings_json else None, queue_wait)


def _fast_median(vals: list) -> Optional[float]:
    """`median` for a list that holds only numbers (sorted in place: no per-value checks)."""
    if not vals:
        return None
    vals.sort()
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


class _Group:
    __slots__ = ("job", "label", "model", "provider", "where", "calls", "ok", "waits", "reads", "writes", "wholes", "totals", "tps", "tps_est", "mems",
                 "tin", "tin_n", "tout", "tout_n", "costs", "notes", "unreported")

    def __init__(self, job, label, model, provider, where):
        self.job, self.label, self.model, self.provider, self.where = job, label, model, provider, where
        self.calls = self.ok = self.tps_est = self.tin_n = self.tout_n = self.unreported = 0
        self.tin = self.tout = 0
        self.waits, self.reads, self.writes, self.wholes, self.totals, self.tps, self.mems, self.costs = [], [], [], [], [], [], [], []
        self.notes: dict = {}

    def add(self, success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note, mem, queue_wait) -> None:
        self.calls += 1
        if isinstance(tokens_in, int):
            self.tin += tokens_in
            self.tin_n += 1
        if isinstance(out_tokens, int):
            self.tout += out_tokens
            self.tout_n += 1
        if cost_note:
            self.notes[cost_note] = self.notes.get(cost_note, 0) + 1
        if isinstance(cost, (int, float)) and cost_note not in (NOTE_FREE, NOTE_INCLUDED):
            self.costs.append(cost)
        if not success:
            return
        self.ok += 1
        if cost_note == NOTE_NONE:
            self.unreported += 1
        if queue_wait is not None:
            self.waits.append(queue_wait)
        self.totals.append((queue_wait or 0.0) + elapsed)
        if first_token is None:
            self.wholes.append(elapsed)
        else:
            self.reads.append(first_token)
            gen = (elapsed or 0) - first_token
            self.writes.append(elapsed - first_token)
            if gen >= MIN_WRITING_SECONDS:
                if out_tokens:
                    self.tps.append(out_tokens / gen)
                elif out_chars:
                    self.tps.append((out_chars / CHARS_PER_TOKEN) / gen)
                    self.tps_est += 1
        if mem is not None:
            self.mems.append(mem)

    def stats(self) -> dict:
        tps = _fast_median(self.tps)
        n_speeds = len(self.tps)
        top_note = max(self.notes, key=self.notes.get) if self.notes else None
        kind = COST_NONE if top_note is None else COST_FREE if top_note == NOTE_FREE else COST_SUBSCRIPTION if top_note == NOTE_INCLUDED else COST_NONE
        return {
            "calls": self.calls, "ok": self.ok,
            "wait": _r(_fast_median(self.waits), 2), "read": _r(_fast_median(self.reads), 2), "write": _r(_fast_median(self.writes), 2), "whole": _r(_fast_median(self.wholes), 2),
            "total": _r(_fast_median(self.totals), 2),
            "waitKnown": bool(self.waits),
            "tokensPerSecond": _r(tps, 1), "wordsPerSecond": _r(tps * WORDS_PER_TOKEN, 1) if tps is not None else None,
            "speedEstimated": bool(n_speeds) and self.tps_est * 2 > n_speeds,
            "remembered": _r(_fast_median(self.mems), 0),
            "tokensIn": self.tin if self.tin_n else None, "tokensOut": self.tout if self.tout_n else None,
            "costTypical": _r(_fast_median(self.costs), 6), "costTotal": _r(sum(self.costs), 6) if self.costs else None, "costKind": kind,
            "costRows": len(self.costs), "unreportedRows": self.unreported,
        }


def aggregate_stream(rows, *, label_of: Callable[[Optional[str]], str], where_of: Callable[[str, Optional[str]], str], keep: Callable[[str], bool],
                     order_of: Callable[[Optional[str]], int]) -> dict:
    """Same result as `aggregate`, from an iterable of row tuples (STREAM_COLUMNS order). `where_of(model, provider)` says where a call ran."""
    groups: dict = {}
    where_memo: dict = {}
    for model, provider, job, role_label, success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note, mem, queue_wait in rows:
        pair = (model, provider)
        w = where_memo.get(pair)
        if w is None:
            where = where_of(model, provider)
            w = where_memo[pair] = (where, keep(where))
        if not w[1]:
            continue
        if job:
            key = (job, model, provider or None, False)
        else:
            job = None
            role_label = role_label if role_label is not None else "Unknown"
            key = (role_label, model, provider or None, True)
        g = groups.get(key)
        if g is None:
            g = groups[key] = _Group(job, role_label if job is None else label_of(job), model, provider, w[0])
        g.add(success, elapsed, first_token, out_tokens, out_chars, tokens_in, cost, cost_note, mem, queue_wait)
    out = []
    for g in groups.values():
        out.append({"job": g.job, "label": g.label, "model": g.model, "provider": g.provider, "where": g.where, **g.stats()})
    return _assemble(out, order_of)


def _assemble(groups: list, order_of: Callable[[Optional[str]], int]) -> dict:
    groups.sort(key=lambda g: (order_of(g["job"]) if g["job"] else 10_000, g["label"].lower(), -g["calls"], g["model"], g["provider"] or ""))
    paid = [g for g in groups if g["costTotal"] is not None]
    total = sum(g["costTotal"] for g in paid)
    unreported = sorted({g["model"] for g in groups if g["unreportedRows"] and g["costTotal"] is None})
    return {
        "groups": groups,
        "total": {"cost": _r(total, 6) if paid else None, "countedModels": sorted({g["model"] for g in paid}), "notCounted": unreported},
    }
