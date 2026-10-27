"""Prompt size (Speeds tab, 2026-10-25): how big the prompts SkyrimNet sends for each job really are, set against the context of the model that answers them.

Everything comes from the Speeds history rows: every request already saves its job, the model and place that answered, the time and `tokens_in` (what the AI itself reported
for the prompt). A request with no token count is left out, never counted as zero. "Typical" is the middle value and "biggest" the largest of a job's counted prompts in the
range; the strip under the chart is the largest prompt in each of the last 24 hours. The context is only ever MultiProxy's own setting or what a program reported (None when
unknown: the numbers are shown without a verdict). Nothing here is estimated.

Sizes are in tokens, written with K = 1,024 (32,768 is "32K"), the way people set a context.
`read` is blocking (a worker thread, through `speed_store.AnswerCache`); `finish` is cheap and runs where the contexts are known.
"""
import math
import sqlite3
from array import array
from contextlib import closing
from typing import Callable, Optional

import speed_queries
import speed_store

RANGES = {"hour": 3600.0, "today": 86400.0, "week": 7 * 86400.0}
HOURS = 24
K = 1024
ROOM = 0.7                                   # a biggest prompt up to this share of the context "fits with room"
SAFE_MARGIN = 1.25                           # the safe size is the biggest prompt plus a quarter, rounded up to the next 4K
SAFE_STEP = 4 * K
SIZES = tuple(k * K for k in (2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024))      # the sizes people set


def fmt(n: float) -> str:
    """16384 -> '16K', 30310 -> '29.6K', 900 -> '900'."""
    if n < K:
        return str(int(n))
    v = n / K
    return f"{v:.0f}K" if v >= 100 or abs(v - round(v)) < 0.05 else f"{v:.1f}K"


def verdict(biggest: int, context: Optional[int], where: str = "") -> dict:
    """{"level": "ok" | "warn" | "bad" | None, "text": ...}: green up to 70% of the context, amber up to the context, red above it; no level when the context is not known."""
    if not context:
        return {"level": None, "text": "Cloud model: no limit to compare" if where == "Cloud" else "Unknown context"}
    if biggest > context:
        return {"level": "bad", "text": f"Too small: the biggest prompt ({fmt(biggest)}) is larger than the context ({fmt(context)})"}
    if biggest * 10 <= context * 7:
        return {"level": "ok", "text": "Fits easily"}
    safe = math.ceil(biggest * SAFE_MARGIN / SAFE_STEP) * SAFE_STEP
    too_small = max((s for s in SIZES if s < biggest), default=None)
    if too_small is None:
        return {"level": "warn", "text": f"Close: {fmt(safe)} is safe"}
    return {"level": "warn", "text": f"Close: {fmt(too_small)} is too small, {fmt(safe)} is safe"}


class _Job:
    __slots__ = ("tokens", "models", "hours")

    def __init__(self):
        self.tokens = array("l")
        self.models: dict = {}
        self.hours = [0] * HOURS                 # the largest prompt in each of the last 24 hours, oldest first (0 = none)


def read(db: str, now: float, range_: str) -> list:
    """Per job seen in the range, one read: [{"job", "provider", "model" (the one that answered it most), "requests", "typical", "biggest", "hours"}], newest-first order not
    promised (`finish` sorts). A row without a job (older ones), or without a token count, is left out."""
    span = RANGES[range_]
    since = now - max(span, RANGES["today"])                       # the 24-hour strip is read in the same pass
    in_range = now - span
    by: dict = {}
    try:
        with closing(speed_store.reader(db)) as conn:
            cur = speed_queries._execute(conn, "SELECT job, provider, model, ts, tokens_in FROM " + speed_queries._table(since)
                                         + " WHERE ts >= ? AND tokens_in > 0 AND job IS NOT NULL", (since,))
            while True:
                chunk = cur.fetchmany(10000)
                if not chunk:
                    break
                for job, provider, model, ts, tokens in chunk:
                    j = by.get(job)
                    if j is None:
                        j = by[job] = _Job()
                    if ts >= in_range:
                        j.tokens.append(tokens)
                        pair = (provider or None, model)
                        j.models[pair] = j.models.get(pair, 0) + 1
                    h = HOURS - 1 - int((now - ts) // 3600)       # 23 = the last hour
                    if 0 <= h < HOURS and tokens > j.hours[h]:
                        j.hours[h] = tokens
    except sqlite3.Error:
        return []                                              # a file from before the token count was saved, or a read problem: nothing to show
    out = []
    for job, j in by.items():
        if not j.tokens:
            continue
        vals = sorted(j.tokens)
        mid = len(vals) // 2
        typical = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
        (provider, model), _n = max(j.models.items(), key=lambda kv: (kv[1], str(kv[0])))
        out.append({"job": job, "provider": provider, "model": model, "requests": len(vals), "typical": round(typical), "biggest": vals[-1], "hours": j.hours})
    return out


def finish(groups: list, *, context_of: Callable[[str, Optional[str]], Optional[int]], where_of: Callable[[str, Optional[str]], str],
           shown: Callable[[str], str], label_of: Callable[[list], str], order_of: Callable[[str], int]) -> list:
    """The jobs as the page shows them: a name, where the answering model runs, its context (None when not known) and the verdict, in the jobs' usual order."""
    out = []
    for g in groups:
        keys = str(g["job"]).split("/")
        where = where_of(g["model"], g["provider"])
        try:
            context = context_of(g["model"], g["provider"])
        except Exception:
            context = None                                     # a context that cannot be read is simply not known
        out.append(dict(g, label=label_of(keys), where=shown(where), context=context or None, verdict=verdict(g["biggest"], context, where), _order=order_of(keys[0]),
                        typicalText=fmt(g["typical"]), biggestText=fmt(g["biggest"]), contextText=fmt(context) if context else None))
    out.sort(key=lambda g: (g.pop("_order"), g["label"].lower()))
    return out
