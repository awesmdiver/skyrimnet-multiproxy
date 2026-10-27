"""The Speeds tab's reads, made cheap at a million rows (2026-10-02). Every function here is blocking and meant for a worker thread (`speed_store.AnswerCache`).

The old way read every row of the range into Python (a dict or tuple per row) ON THE EVENT LOOP, and read the whole table again for each model. Here a read
runs in a worker thread, walks the rows once with the `ts` index narrowing it to the range, and keeps only a few numbers per model (never a list of row
objects). Each function mirrors one old Python function exactly (the old ones stay in multiproxy.py / speeds_time.py as the reference the tests compare against):

* `models_summary`      = `_aggregate_speed_history` + `_v2_model_stats` for every model in a window, in ONE pass (the By-model cards)
* `own_time`            = the median of `own_ms` (MultiProxy's own added time per request) over a window, for the device filter in use
* `health_rows`         = `_query_health_rows`
* `by_role_rows`        = `_query_speed_history_rows_by_role` + `_query_v2_rows_by_role` from one read, narrowed in SQL before the exact check in Python
* `routing_history`     = the Routing view's 24-hour read
* `time_answer`         = the "Time breakdown" answer, streamed (speeds_time.aggregate_stream)

(Tried and dropped: SQL window functions for the medians. SQLite sorts them at about 2 microseconds a row, three times over, which was slower than one pass in Python.)

Nothing from MultiProxy is imported here: the little bits of wording and thresholds it needs are passed in or repeated with a test that keeps them equal.
"""
import json
import sqlite3
from array import array
from collections import deque
from contextlib import closing
from typing import Callable, Iterable, Optional

import quick_stats
import speed_store

# The saved columns the Overview's quick stats read besides the basic ones (a file from before one existed has no such column: it reads as None).
_QUICK_COLUMNS = ("job", "roles_json", "skipped", "stand_in", "in_flight", "tokens_in")

SPEED_STRIP_LEN = 40
SPEED_MIN_GENERATION_SECONDS = 0.25
CHARS_PER_TOKEN = 4.0
WORDS_PER_TOKEN = 0.75
WEEKLY_BUCKET_CUTOFF_DAYS = 60
DAY = 86400.0

_REASON_PHRASES = {
    "busy": lambda n: f'{n} "too busy"',
    "timeout": lambda n: f"{n} took too long",
    "gave up": lambda n: f"{n} gave up waiting",
    "empty": lambda n: f"{n} answered with nothing",
    "server": lambda n: f"{n} server error{'s' if n != 1 else ''}",
    "auth": lambda n: f"{n} login problem{'s' if n != 1 else ''}",
    "broken reply": lambda n: f"{n} broken repl{'y' if n == 1 else 'ies'}",
    "other": lambda n: f"{n} other",
}


def _where(since_ts: Optional[float], extra: str = "") -> tuple:
    clauses = []
    params: list = []
    if since_ts is not None:
        clauses.append("ts >= ?")
        params.append(since_ts)
    if extra:
        clauses.append(extra)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _median(values) -> Optional[float]:
    """Middle value, or the mean of the two middle ones, of any iterable of numbers (None for none)."""
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _table(since_ts: Optional[float]) -> str:
    """The table, with the `ts` index named when the read has a time range: without statistics SQLite sometimes walks the model index instead, which reads the
    whole file for "today" (the index always exists: speed_store.prepare makes it on every start)."""
    return "speed_history INDEXED BY idx_speed_history_ts" if since_ts is not None and _USE_HINTS else "speed_history"


_USE_HINTS = True


def _execute(conn, sql: str, params=()):
    """Run a read; if the hinted index is somehow missing, run it again without the hint (and stop using hints)."""
    global _USE_HINTS
    try:
        return conn.execute(sql, params)
    except Exception as e:
        if "no such index" in str(e) and _USE_HINTS:
            _USE_HINTS = False
            return conn.execute(sql.replace(" INDEXED BY idx_speed_history_ts", ""), params)
        raise


def _repair_line_from_counts(repaired: int, asked_again: int, still_broken: int) -> Optional[str]:
    parts = []
    if repaired:
        parts.append(f"{repaired} broken repl{'y' if repaired == 1 else 'ies'} fixed")
    if asked_again:
        parts.append(f"{asked_again} asked again")
    if still_broken:
        parts.append(f"{still_broken} still broken")
    return " · ".join(parts) if parts else None


def _failure_line_from_strip(strip_rows: list) -> Optional[str]:
    """Same text as multiproxy._failure_line over the last-40 strip. strip_rows: [(elapsed, success, fail_reason)]."""
    failed = [r for r in strip_rows if not r[1]]
    reasons: dict = {}
    for r in failed:
        if r[2]:
            reasons[r[2]] = reasons.get(r[2], 0) + 1
    if not failed or not reasons:
        return None
    order = ("busy", "timeout", "gave up", "empty", "server", "auth", "broken reply", "other")
    parts = [_REASON_PHRASES.get(k, _REASON_PHRASES["other"])(reasons[k]) for k in order if k in reasons]
    unrecorded = len(failed) - sum(reasons.values())
    if unrecorded:
        parts.append(f"{unrecorded} with no reason saved")
    return f"{len(failed)} failed: " + ", ".join(parts)


class _ModelAcc:
    __slots__ = ("n", "ok", "sum", "min_ts", "max_ts", "last_ts", "days", "ok_el", "ft", "tps", "tps_est", "new_ok", "rep", "asked", "broken", "provider", "strip")

    def __init__(self):
        self.n = self.ok = self.tps_est = self.new_ok = self.rep = self.asked = self.broken = 0
        self.sum = 0.0
        self.min_ts = float("inf")
        self.max_ts = float("-inf")
        self.last_ts = float("-inf")
        self.days: dict = {}
        self.ok_el, self.ft, self.tps = array("d"), array("d"), array("d")
        self.provider = None
        self.strip: deque = deque(maxlen=SPEED_STRIP_LEN)


def card_key(provider: Optional[str], model: str) -> str:
    """One Speeds card (and the Routing numbers behind it) is one provider's model: 'Grok::grok-4.7', 'Continuum::grok-4.7'. A call saved with no provider has no
    name in front ('::grok-4.7'): it can't be attributed, so it never mixes into a provider's numbers."""
    return (provider or "") + "::" + model


def _scan_models(conn, since_ts: Optional[float], ordered: bool) -> tuple:
    where_sql, params = _where(since_ts)
    sql = ("SELECT model, ts, elapsed, success, first_token, out_tokens, out_chars, fail_reason, provider, reply_repair FROM " + _table(since_ts) + where_sql
           + (" ORDER BY ts, rowid" if ordered else ""))
    cur = _execute(conn, sql, params)
    accs: dict = {}
    get = accs.get
    out_of_order = False
    while True:
        chunk = cur.fetchmany(10000)
        if not chunk:
            break
        for model, ts, el, ok, ft, ot, oc, fr, prov, rep in chunk:
            k = (prov or None, model)
            a = get(k)
            if a is None:
                a = accs[k] = _ModelAcc()
            a.n += 1
            a.sum += el
            if ts < a.last_ts:
                out_of_order = True
            a.last_ts = ts
            if ts < a.min_ts:
                a.min_ts = ts
            if ts > a.max_ts:
                a.max_ts = ts
            d = int(ts // DAY)
            b = a.days.get(d)
            if b is None:
                a.days[d] = [el, 1]
            else:
                b[0] += el
                b[1] += 1
            a.strip.append((el, ok, fr))
            if prov:
                a.provider = prov
            if rep:
                if rep == "repaired":
                    a.rep += 1
                elif rep == "asked_again":
                    a.asked += 1
            if ok:
                a.ok += 1
                a.ok_el.append(el)
                if prov:
                    a.new_ok += 1
                if ft is not None:
                    a.ft.append(ft)
                    gen = el - ft
                    if gen >= SPEED_MIN_GENERATION_SECONDS:
                        if ot:
                            a.tps.append(ot / gen)
                        elif oc:
                            a.tps.append(oc / CHARS_PER_TOKEN / gen)
                            a.tps_est += 1
            elif fr == "broken reply":
                a.broken += 1
    return accs, out_of_order


def models_summary(db: str, since_ts: Optional[float], reliability_level: Callable[[Optional[int]], Optional[str]]) -> list:
    """One entry per provider + model with at least one row in the window, sorted by model name then provider ("model", "provider" and "key" say which): the keys `_aggregate_speed_history` gives (last_reply, avg, success_pct,
    calls_total, recent, bucket_unit) plus `v2` (the dict `_v2_model_stats` gives). One pass over the window's rows; each model keeps counts, its day sums and
    compact lists of the numbers it needs a median of."""
    with closing(speed_store.reader(db)) as conn:
        accs, out_of_order = _scan_models(conn, since_ts, False)
        if out_of_order:                                # rows are normally saved in time order; if the clock ever went back, read them sorted instead
            accs, _ = _scan_models(conn, since_ts, True)
    out = []
    for pair in sorted(accs, key=lambda p: (p[1], p[0] or "")):
        provider, model = pair
        a = accs[pair]
        n, ok = a.n, a.ok
        span_days = (a.max_ts - a.min_ts) / DAY
        unit = "week" if span_days > WEEKLY_BUCKET_CUTOFF_DAYS else "day"
        if unit == "week":
            weeks: dict = {}
            for day, (sm, cnt) in a.days.items():
                w = weeks.setdefault(day // 7, [0.0, 0])
                w[0] += sm
                w[1] += cnt
            series = [sm / cnt for _k, (sm, cnt) in sorted(weeks.items())]
        else:
            series = [sm / cnt for _k, (sm, cnt) in sorted(a.days.items())]
        pct = round(100 * ok / n) if n else None
        strip = list(a.strip)
        tps = _median(a.tps) if a.tps else None
        speed = {"words": tps * WORDS_PER_TOKEN, "tokens": tps, "estimated": a.tps_est > 0} if a.tps else None
        if a.ft:
            first_note = None
        elif not ok:
            first_note = "no_data"
        elif a.new_ok:
            first_note = "whole_reply"
        else:
            first_note = "not_measured"
        out.append({
            "model": model,
            "provider": provider,
            "key": card_key(provider, model),
            "last_reply": strip[-1][0],
            "avg": a.sum / n,
            "success_pct": pct,
            "calls_total": n,
            "recent": series,
            "bucket_unit": unit,
            "v2": {
                "calls": n,
                "provider": a.provider,
                "reliability": {"pct": pct, "level": reliability_level(pct)},
                "first_token": _median(a.ft) if a.ft else None,
                "first_token_samples": len(a.ft),
                "first_token_note": first_note,
                "speed_note": None if a.new_ok else "not_measured",
                "full": {"fastest": min(a.ok_el) if a.ok_el else None, "typical": _median(a.ok_el) if a.ok_el else None, "slowest": max(a.ok_el) if a.ok_el else None},
                "speed": speed,
                "strip": [{"e": r[0], "ok": bool(r[1])} for r in strip],
                "failure_line": _failure_line_from_strip(strip),
                "repair_line": _repair_line_from_counts(a.rep, a.asked, a.broken),
            },
        })
    return out


def own_time(db: str, since_ts: Optional[float], pairs_ok: Optional[Callable[[str, Optional[str]], bool]] = None) -> dict:
    """{"medianMs": ..., "requests": n} over the window: the median of own_ms (MultiProxy's own added time per request) among the rows that have it. With
    `pairs_ok(model, provider)` only the (model, provider) pairs it accepts count (the device filter)."""
    where_sql, params = _where(since_ts, "own_ms IS NOT NULL")
    table = _table(since_ts)
    with closing(speed_store.reader(db)) as conn:
        if pairs_ok is not None:
            pairs = _execute(conn, "SELECT model, provider FROM " + table + where_sql + " GROUP BY model, provider", params).fetchall()
            accepted = [(m, p) for m, p in pairs if pairs_ok(m, p)]
            if not accepted:
                return {"medianMs": None, "requests": 0}
            if len(accepted) < len(pairs):
                where_sql += " AND (" + " OR ".join("(model IS ? AND provider IS ?)" for _ in accepted) + ")"
                for m, p in accepted:
                    params = params + [m, p]
        values = array("d")
        cur = _execute(conn, "SELECT own_ms FROM " + table + where_sql, params)
        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            values.extend(v[0] for v in chunk)
    med = _median(values) if values else None
    return {"medianMs": None if med is None else round(med, 3), "requests": len(values)}


def health_rows(db: str, now: float, window_s: float, v2_columns: str) -> tuple:
    """Same as multiproxy._query_health_rows: (rows in the last hour that name a provider, {provider: first-token times from before that hour})."""
    since = now - window_s
    with closing(speed_store.reader(db)) as conn:
        recent = [tuple(r) for r in conn.execute(
            f"SELECT {v2_columns} FROM speed_history WHERE provider IS NOT NULL AND ts >= ? ORDER BY ts", (since,))]
        baseline: dict = {}
        for provider, first_token in conn.execute(
                "SELECT provider, first_token FROM speed_history WHERE provider IS NOT NULL AND success = 1 AND first_token IS NOT NULL AND ts < ? "
                "ORDER BY ts DESC LIMIT 5000", (since,)):
            baseline.setdefault(provider, []).append(first_token)
    return recent, baseline


def hour_failures(db: str, now: float, window_s: float) -> list:
    """Every (provider, model) with a failed request in the window, for the Speeds health cards: [{"provider", "model", "ok", "failed", "longest" (seconds the longest failed
    request waited), "reasons" {reason: n}, "note" (the latest failure's plain reason from a local engine, or None), "job" (the job most of its failed requests were for, or None)}].
    Only counts, reasons and job keys are read: no text, sound or key."""
    out: dict = {}
    with closing(speed_store.reader(db)) as conn:
        for provider, model, success, elapsed, reason, job, note in conn.execute(
                "SELECT provider, model, success, elapsed, fail_reason, job, fail_note FROM speed_history WHERE provider IS NOT NULL AND ts >= ? ORDER BY ts", (now - window_s,)):
            a = out.setdefault((provider, model), {"provider": provider, "model": model, "ok": 0, "failed": 0, "longest": 0.0, "reasons": {}, "jobs": {}, "note": None})
            if success:
                a["ok"] += 1
                continue
            a["failed"] += 1
            a["longest"] = max(a["longest"], float(elapsed or 0.0))
            a["reasons"][reason or "other"] = a["reasons"].get(reason or "other", 0) + 1
            if note:
                a["note"] = note                        # the latest failure's plain reason from a local engine (engine_trouble.py)
            if job:
                a["jobs"][job] = a["jobs"].get(job, 0) + 1
    rows = []
    for a in out.values():
        if a["failed"]:
            jobs = a.pop("jobs")
            a["job"] = max(jobs, key=lambda k: jobs[k]) if jobs else None
            rows.append(a)
    return rows


def own_play_rows(db: str, now: float, window_s: float) -> list:
    """For "From your own play" on Quick Test: (job key, success, reply_repair, queue_wait) for every request of the window that has a job. Counts and times only."""
    since = now - window_s
    with closing(speed_store.reader(db)) as conn:
        return list(conn.execute("SELECT job, success, reply_repair, queue_wait FROM " + _table(since) + " WHERE ts >= ? AND job IS NOT NULL", (since,)))


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def by_role_rows(db: str, role: str, since_ts: Optional[float], v2_columns: str, roles_of: Callable[[str], list]) -> tuple:
    """(rows [((provider, model), ts, elapsed, success)], v2 {(provider, model): [row tuples]}) for every call that carried this exact role label, from ONE read. SQL keeps only rows whose
    roles text contains the label's JSON spelling (a superset, via the index-free LIKE on a column that is NULL for most old rows); the exact match on the parsed
    list stays in Python, as before, so "Vision" never picks up "Vision2"."""
    where_sql, params = _where(since_ts, "roles_json IS NOT NULL AND roles_json LIKE ? ESCAPE '\\'")
    params = params + ["%" + _like_escape(json.dumps(role)) + "%"]
    rows: list = []
    v2: dict = {}
    with closing(speed_store.reader(db)) as conn:
        cur = conn.execute(f"SELECT model, roles_json, {v2_columns} FROM speed_history{where_sql} ORDER BY ts", params)
        while True:
            chunk = cur.fetchmany(5000)
            if not chunk:
                break
            for model, roles_json, *row in chunk:
                try:
                    if role not in roles_of(roles_json):
                        continue
                except (TypeError, ValueError):
                    continue
                pair = (row[7] or None, model)                           # v2_columns' provider is its 8th column
                rows.append((pair, row[0], row[1], row[2]))               # ((provider, model), ts, elapsed, success): v2_columns starts with ts, elapsed, success
                v2.setdefault(pair, []).append(tuple(row))
    return rows, v2


def distinct_roles(db: str, roles_of: Callable[[str], list]) -> list:
    role_set: set = set()
    with closing(speed_store.reader(db)) as conn:
        for (roles_json,) in conn.execute("SELECT DISTINCT roles_json FROM speed_history WHERE roles_json IS NOT NULL"):
            try:
                role_set.update(roles_of(roles_json))
            except (TypeError, ValueError):
                continue
    return sorted(role_set)


def routing_history(db: str, now: float) -> tuple:
    """(per provider and model, last 24 hours, keyed by card_key (rows saved without a provider are left out): {calls, ok, first_word}; {job key: how it was last known}). Both reads walk an index (ts, and the partial job index) so the
    cost follows the day's rows, not the file's. Raises on a read error (the caller decides what to show)."""
    stats: dict = {}
    seen: dict = {}
    since = now - 86400
    with closing(speed_store.reader(db)) as conn:
        by: dict = {}
        cur = _execute(conn, "SELECT provider, model, success, first_token FROM " + _table(since) + " WHERE ts > ? AND provider IS NOT NULL AND provider != ''", (since,))
        while True:
            chunk = cur.fetchmany(10000)
            if not chunk:
                break
            for provider, model, success, first_token in chunk:
                k = card_key(provider, model)
                e = by.get(k)
                if e is None:
                    e = by[k] = [0, 0, []]
                e[0] += 1
                if success:
                    e[1] += 1
                    if first_token:
                        e[2].append(first_token)
        for model, (calls, ok, fw) in by.items():
            fw.sort()
            stats[model] = {"calls": calls, "ok": ok, "first_word": round(fw[len(fw) // 2], 2) if fw else None}
        for job, how in conn.execute("SELECT job, job_how FROM speed_history WHERE job IS NOT NULL ORDER BY ts DESC LIMIT 600"):
            for k in str(job).split("/"):
                seen.setdefault(k, how)
    return stats, seen


def _stream(cur, size: int = 5000) -> Iterable[tuple]:
    while True:
        chunk = cur.fetchmany(size)
        if not chunk:
            return
        yield from chunk


def _json_functions_ok() -> bool:
    try:
        with closing(speed_store.reader(":memory:")) as c:
            return c.execute("SELECT json_valid('[1]'), json_extract('[1]', '$[0]')").fetchone() == (1, 1)
    except Exception:
        return False


JSON_OK = _json_functions_ok()


def time_answer(db: str, since_ts: Optional[float], **callbacks) -> dict:
    """The "the time breakdown" answer (speeds_time.aggregate_stream) worked out while the rows stream past in chunks: no list of a million dicts."""
    import speeds_time
    where_sql, params = _where(since_ts)
    with closing(speed_store.reader(db)) as conn:
        select = speeds_time.STREAM_SELECT if JSON_OK else speeds_time.STREAM_SELECT_PLAIN
        cur = _execute(conn, f"SELECT {select} FROM {_table(since_ts)}{where_sql}", params)
        rows = _stream(cur)
        return speeds_time.aggregate_stream(rows if JSON_OK else speeds_time.rows_from_raw(rows), **callbacks)


# ---------------------------------------------------------------- local models: real tokens per second

LOCAL_SPEED_WINDOW = 20             # the last this many warm replies under one setup are averaged
LOCAL_SPEED_MIN = 5                 # fewer than this under the current setup is "not enough requests yet": never an estimate
LOCAL_SPEED_SCAN_MAX = 50000        # rows read per model at most (newest first), so a model with a huge history can't make the read slow


def _timing_numbers(timings_json) -> Optional[tuple]:
    """(generation tokens per second, prompt tokens, prompt milliseconds) from the llama.cpp timing block saved with a reply, or None when it has no generation
    speed (the server reported no timing block, or the reply wrote nothing)."""
    if not timings_json:
        return None
    try:
        t = json.loads(timings_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(t, dict):
        return None
    tps = t.get("tokens_per_second")
    if not isinstance(tps, (int, float)) and t.get("predicted_n") and t.get("predicted_ms"):
        tps = t["predicted_n"] / (t["predicted_ms"] / 1000.0)
    if not isinstance(tps, (int, float)) or isinstance(tps, bool) or not (tps > 0) or tps != tps or tps == float("inf"):
        return None
    pn, pms = t.get("prompt_n"), t.get("prompt_ms")
    ok = isinstance(pn, (int, float)) and isinstance(pms, (int, float)) and pn > 0 and pms > 0
    return float(tps), (float(pn) if ok else 0.0), (float(pms) if ok else 0.0)


class _SetupGroup:
    """The warm replies of ONE setup, newest first, up to LOCAL_SPEED_WINDOW that carry a timing block."""
    __slots__ = ("tps", "prompt_n", "prompt_ms", "waits", "warm_ok", "cold")

    def __init__(self):
        self.tps, self.waits = [], []
        self.prompt_n = self.prompt_ms = 0.0
        self.warm_ok = self.cold = 0

    def full(self) -> bool:
        return len(self.tps) >= LOCAL_SPEED_WINDOW

    def add(self, elapsed, success, timings_json, cold) -> None:
        if not success:
            return
        if cold:
            self.cold += 1                      # the first reply after the engine started: slow for reasons that are not the setup
            return
        self.warm_ok += 1
        nums = _timing_numbers(timings_json)
        if nums is None or self.full():
            return
        self.tps.append(nums[0])
        self.prompt_n += nums[1]
        self.prompt_ms += nums[2]
        if elapsed is not None:
            self.waits.append(elapsed)

    def summary(self) -> dict:
        n = len(self.tps)
        if n >= LOCAL_SPEED_MIN:
            state = "ok"
        elif n == 0 and self.warm_ok >= LOCAL_SPEED_MIN:
            state = "not_reported"              # it answered plenty of times and its server never said how fast
        else:
            state = "not_enough"
        out = {"state": state, "requests": n, "coldSkipped": self.cold}
        if state == "ok":
            out["tokensPerSecond"] = round(sum(self.tps) / n, 1)
            out["promptTokensPerSecond"] = round(self.prompt_n / (self.prompt_ms / 1000.0), 1) if self.prompt_ms > 0 else None
            out["typicalWaitSeconds"] = round(_median(self.waits), 2) if self.waits else None
        return out


def local_model_speeds(db: str, models: dict) -> dict:
    """Real tokens per second for each local model, from the timing blocks saved with its replies: the average over the last 20 WARM replies (the first reply after
    the engine started is skipped) measured under the model's CURRENT setup. `models` is {name the model is saved under: the current setup's fingerprint, or None
    to take the newest reply's}.

    -> {name: {state, requests, tokensPerSecond, promptTokensPerSecond, typicalWaitSeconds, previous, setup, coldSkipped}}. `state` is `ok` (at least 5 replies under
    this setup), `not_enough` (fewer: no number is shown, never an estimate) or `not_reported` (it answered but its server reports no timing block). `previous` is the
    same numbers for the setup that ran just before, shown only until 20 replies have been measured under the current one (a setup is never averaged with another);
    it needs 5 replies of its own. Replies saved before setups were tracked share one unnamed setup."""
    out: dict = {}
    with closing(speed_store.reader(db)) as conn:
        for name, current in models.items():
            cur_fp = current
            prev_fp = None                      # the newest setup that is not the current one
            cur_group, prev_group = _SetupGroup(), _SetupGroup()
            scanned = 0
            cur = _execute(conn, "SELECT elapsed, success, timings_json, setup_fp, cold FROM speed_history WHERE model = ? AND provider = 'Local' ORDER BY ts DESC, rowid DESC",
                           (name,))
            while scanned < LOCAL_SPEED_SCAN_MAX and not cur_group.full():
                chunk = cur.fetchmany(500)
                if not chunk:
                    break
                for elapsed, success, timings_json, fp, cold in chunk:
                    scanned += 1
                    fp = fp or ""
                    if cur_fp is None:
                        cur_fp = fp             # no running engine to ask: the newest reply's setup is the current one
                    if fp == cur_fp:
                        cur_group.add(elapsed, success, timings_json, cold)
                    else:
                        if prev_fp is None:
                            prev_fp = fp
                        if fp == prev_fp:
                            prev_group.add(elapsed, success, timings_json, cold)
                    if cur_group.full():
                        break
            entry = cur_group.summary()
            entry["setup"] = cur_fp or ""
            entry["previous"] = None
            if prev_fp is not None and not cur_group.full():
                p = prev_group.summary()
                if p["state"] == "ok":
                    entry["previous"] = {"tokensPerSecond": p["tokensPerSecond"], "requests": p["requests"]}
            out[name] = entry
    return out


def recent_models(db: str, provider: str, since_ts: float) -> list:
    """The models `provider` answered a request with since `since_ts`, newest first, each once (as saved: may carry the provider's own prefix). [] on any read problem."""
    try:
        with closing(speed_store.reader(db)) as conn:
            seen: list = []
            for (model,) in _execute(conn, "SELECT model FROM " + _table(since_ts) + " WHERE ts >= ? AND provider = ? AND success = 1 ORDER BY ts DESC LIMIT 200", (since_ts, provider)):
                if model not in seen:
                    seen.append(model)
            return seen
    except sqlite3.Error:
        return []


def voice_speaking_rate(db: str, now: float, window_s: float = 3600.0) -> dict:
    """{"rtf": seconds the engine spent per second of sound over the hour's spoken lines (under 1 is faster than real time), "lines": how many lines it rests on}; rtf is None under
    5 lines that have both figures (an older line, or one that failed, has neither) -- nothing is estimated."""
    try:
        with closing(speed_store.reader(db)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(voice_log)")}
            if not {"spoke", "audio"} <= cols:
                return {"rtf": None, "lines": 0}
            rows = conn.execute("SELECT spoke, audio FROM voice_log WHERE ts >= ? AND ok = 1 AND spoke IS NOT NULL AND audio IS NOT NULL AND audio > 0", (now - window_s,)).fetchall()
    except sqlite3.Error:
        return {"rtf": None, "lines": 0}
    if len(rows) < VOICE_TYPICAL_MIN_LINES:
        return {"rtf": None, "lines": len(rows)}
    return {"rtf": sum(r[0] for r in rows) / sum(r[1] for r in rows), "lines": len(rows)}


VOICE_TYPICAL_MIN_LINES = 5      # fewer spoken lines than this in the hour: no "typical" time (the Overview's other rows use the same bar)


def overview_figures(db: str, now: float, day_start: float, window_s: float = 3600.0, recent_s: float = 300.0) -> dict:
    """What the Overview page's numbers come from (2026-10-05), in one cheap read of the saved replies: the last hour's rows (small), plus one count for today's failures.

    -> {"rows": {(provider, model): {"n", "ok", "typical" (median seconds of the successful replies), "tokensPerSecond" (median, warm replies that gave a real count)}},
        "perMinute": replies finished in the last `recent_s` seconds / minutes, "typical": median seconds of every successful reply in the hour (None for none),
        "failuresToday": failed replies since `day_start`, "lastReplyAgo": {provider: seconds since its newest reply in the hour}, "calls": replies in the hour,
        "quick": the quick stats of the hour (quick_stats.QuickFigures.result), "jobs": {job key: {"n", "ok", "avg"}} the hour per job, "voice": {"n", "ok", "avg", "waiting", "speaking", "typical"} spoken lines in the hour (None before they were recorded)}.
    Nothing here is estimated: a figure with nothing behind it is None."""
    since = now - window_s
    rows: dict = {}
    all_ok = array("d")
    recent = 0
    newest: dict = {}
    quick = quick_stats.QuickFigures()
    with closing(speed_store.reader(db)) as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(speed_history)")}
        extra = ", ".join(c if c in have else "NULL" for c in _QUICK_COLUMNS)           # a file from before a column existed gives None for it, never a made-up value
        cur = _execute(conn, "SELECT model, provider, ts, elapsed, success, first_token, out_tokens, " + extra + " FROM " + _table(since) + " WHERE ts >= ?", (since,))
        acc: dict = {}
        by_job: dict = {}                                                       # job key -> [replies, worked, seconds of the ones that worked] (a row for jobs that cannot be told apart counts for each of them: they share one route)
        for model, provider, ts, el, ok, ft, ot, job, roles_json, skipped, stand_in, in_flight, tokens_in in cur:
            for jk in (str(job).split("/") if job else ()):
                j = by_job.setdefault(jk, [0, 0, 0.0])
                j[0] += 1
                if ok:
                    j[1] += 1
                    j[2] += el
            quick.add(model=model, provider=provider, ts=ts, elapsed=el, success=ok, out_tokens=ot, tokens_in=tokens_in, job=job, roles_json=roles_json, skipped=skipped,
                      stand_in=stand_in, in_flight=in_flight)
            a = acc.setdefault((provider or None, model), [0, 0, array("d"), array("d"), 0.0])
            a[0] += 1
            if ts > a[4]:
                a[4] = ts
            if provider and ts > newest.get(provider, 0.0):
                newest[provider] = ts
            if ts >= now - recent_s:
                recent += 1
            if ok:
                a[1] += 1
                a[2].append(el)
                all_ok.append(el)
                if ft is not None and ot:
                    gen = el - ft
                    if gen >= SPEED_MIN_GENERATION_SECONDS:
                        a[3].append(ot / gen)
        fail_where, fail_params = _where(day_start, "success = 0")
        failures = _execute(conn, "SELECT COUNT(*) FROM " + _table(day_start) + fail_where, fail_params).fetchone()[0]
        decisions = None
        try:
            n_dec = n_asked = n_stepped = n_conf = 0
            conf_sum = 0.0
            for conf, asked, by in conn.execute("SELECT confidence, asked, answered_by FROM decision_log WHERE ts >= ?", (since,)):
                n_dec += 1
                if asked:
                    n_asked += 1
                    if by and by != "local":
                        n_stepped += 1
                if conf is not None:
                    n_conf += 1
                    conf_sum += conf
            if n_dec:
                decisions = {"n": n_dec, "own": n_dec - n_asked, "stepped": n_stepped, "avgConfidence": (conf_sum / n_conf) if n_conf == n_dec else None}
        except sqlite3.OperationalError:
            decisions = None                          # a history file from before decisions were recorded: nothing to show
        voice = None
        try:
            n_v = ok_v = 0
            sum_v = 0.0
            spoke_all, wait_sum, spoke_sum, split_n = array("d"), 0.0, 0.0, 0
            cols = {r[1] for r in conn.execute("PRAGMA table_info(voice_log)")}
            extra = ", ".join(c if c in cols else "NULL" for c in ("waited", "spoke"))             # a file from before the split gives None, never a made-up value
            for seconds, ok, waited, spoke in conn.execute("SELECT seconds, ok, " + extra + " FROM voice_log WHERE ts >= ?", (since,)):
                n_v += 1
                if ok:
                    ok_v += 1
                    sum_v += seconds
                    if waited is not None and spoke is not None:
                        split_n += 1
                        wait_sum += waited
                        spoke_sum += spoke
                        spoke_all.append(spoke)
            voice = {"n": n_v, "ok": ok_v, "avg": (sum_v / ok_v) if ok_v else None,
                     "waiting": (wait_sum / split_n) if split_n else None, "speaking": (spoke_sum / split_n) if split_n else None,      # (averages of the hour's lines that have both times)
                     "typical": _median(spoke_all) if len(spoke_all) >= VOICE_TYPICAL_MIN_LINES else None}      # (the median SPEAKING time; fewer than 5 lines with it: None)
        except sqlite3.OperationalError:
            voice = None                              # a history file from before spoken lines were recorded: the Voice row shows dashes
    for key, (n, ok, els, tps, newest_ts) in acc.items():
        rows[key] = {"n": n, "ok": ok, "typical": _median(els) if els else None, "tokensPerSecond": _median(tps) if tps else None, "ago": max(0.0, now - newest_ts),
                     "avg": (sum(els) / len(els)) if els else None}
    jobs = {k: {"n": n, "ok": ok, "avg": (s / ok) if ok else None} for k, (n, ok, s) in by_job.items()}
    return {"decisions": decisions, "voice": voice, "jobs": jobs, "quick": quick.result(), "rows": rows, "perMinute": recent / (recent_s / 60.0), "typical": _median(all_ok) if all_ok else None,
            "failuresToday": failures, "lastReplyAgo": {k: max(0.0, now - v) for k, v in newest.items()}, "calls": sum(r["n"] for r in rows.values())}
