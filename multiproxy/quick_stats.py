"""The Overview's quick stats for the last hour (2026-10-05): success rate, backups, busiest moment, kind of job, dialogue speed, slowest request, tokens.

Pure, with nothing from MultiProxy imported (tests use it on its own). Two parts:

* `InFlight`: how many requests are being answered right now. A request is counted when it starts, and the count it sees (itself included) is saved with its row, so
  "most at once" is read back from saved rows, never estimated. It lives in memory only; a request that never reports back is dropped after `STALE_SECONDS`.
* `QuickFigures`: adds up the last hour's saved rows (speed_queries.overview_figures feeds it one row at a time) and gives every figure the page shows.
  A figure with nothing behind it is None (or absent): it is never drawn as a zero and never worked out from a guess.
"""
import threading
import time
from typing import Optional

DIALOGUE_LIMIT_SECONDS = 10.0      # SkyrimNet's own limit for a dialogue line to come back
STALE_SECONDS = 1800.0             # a request that never said it finished is forgotten after this long (a stuck one must not hold the count up forever)

# The kinds of job the page shows, in the order it shows them. A job's SkyrimNet key says which; Speeds groups the same keys (dialogue, vision, everything else is background).
KINDS = ("Dialogue", "Decisions", "Memories", "Background", "Vision")
_JOB_KIND = {"dialogue": "Dialogue", "decisions": "Decisions", "memory": "Memories", "vision": "Vision"}


def kind_of(job: Optional[str], roles_json: Optional[str] = None, provider: Optional[str] = None) -> Optional[str]:
    """The kind of job a saved row was, or None when it cannot be told (a row from before jobs were saved, or a job MultiProxy did not recognise): such a row is left out
    of the split, never guessed. The local decision model and a row forced to the Decisions label (a stand-in) are decisions."""
    if provider == "Local decision model" or (roles_json and '"Decisions"' in roles_json):
        return "Decisions"
    if not job:
        return None
    key = str(job).split("/")[0]
    if key == "other":
        return None
    return _JOB_KIND.get(key, "Background")


class InFlight:
    """Requests being answered right now. `enter` returns how many there are, this one included."""

    def __init__(self) -> None:
        self._started: dict = {}
        self._lock = threading.Lock()

    def enter(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            for k in [k for k, t in self._started.items() if now - t > STALE_SECONDS]:
                del self._started[k]
            self._started[key] = now
            return len(self._started)

    def leave(self, key: str) -> None:
        with self._lock:
            self._started.pop(key, None)

    def count(self) -> int:
        return len(self._started)


class QuickFigures:
    """One pass over the hour's rows. `add` takes a row's values as saved; `result` gives the figures."""

    def __init__(self, limit_seconds: float = DIALOGUE_LIMIT_SECONDS) -> None:
        self.limit = limit_seconds
        self.n = self.ok = self.backups = 0
        self.peak: Optional[tuple] = None                        # (most at once, when that request started)
        self.kinds: dict = {}                                    # kind -> [n, ok, sum of ok seconds]
        self.dialogue = [0, 0]                                   # [answered dialogue lines, those within the limit]
        self.slowest: Optional[tuple] = None                     # (seconds, ts, provider, model)
        self.tokens: dict = {}                                   # name -> [in or None, out or None]

    def add(self, *, model, provider, ts, elapsed, success, out_tokens=None, tokens_in=None, job=None, roles_json=None, skipped=None, stand_in=None, in_flight=None) -> None:
        self.n += 1
        kind = kind_of(job, roles_json, provider)
        if kind:
            k = self.kinds.setdefault(kind, [0, 0, 0.0])
            k[0] += 1
        if in_flight is not None and (self.peak is None or (in_flight, ts) > (self.peak[0], self.peak[2])):
            self.peak = (int(in_flight), max(ts - (elapsed or 0.0), 0.0), ts)
        if out_tokens is not None or tokens_in is not None:
            t = self.tokens.setdefault(provider or model, [None, None])
            if tokens_in is not None:
                t[0] = (t[0] or 0) + int(tokens_in)
            if out_tokens is not None:
                t[1] = (t[1] or 0) + int(out_tokens)
        elif (provider or model) not in self.tokens:
            self.tokens[provider or model] = [None, None]
        if not success:
            return
        self.ok += 1
        if kind:
            k = self.kinds[kind]
            k[1] += 1
            k[2] += elapsed
        if skipped or stand_in:
            self.backups += 1                                    # an AI other than the first choice answered
        if kind == "Dialogue":
            self.dialogue[0] += 1
            if elapsed <= self.limit:
                self.dialogue[1] += 1
        if self.slowest is None or elapsed > self.slowest[0]:
            self.slowest = (elapsed, ts, provider, model)

    def result(self) -> Optional[dict]:
        if not self.n:
            return None
        names = sorted(self.tokens, key=lambda k: (-((self.tokens[k][0] or 0) + (self.tokens[k][1] or 0)), k))
        return {
            "n": self.n, "ok": self.ok, "failed": self.n - self.ok, "backups": self.backups,
            "peak": {"n": self.peak[0], "at": self.peak[1]} if self.peak else None,
            "kinds": {k: {"n": v[0], "ok": v[1], "avg": (v[2] / v[1]) if v[1] else None} for k, v in self.kinds.items()},
            "dialogue": {"n": self.dialogue[0], "within": self.dialogue[1], "limit": self.limit} if self.dialogue[0] else None,
            "slowest": {"seconds": self.slowest[0], "at": self.slowest[1], "provider": self.slowest[2], "model": self.slowest[3]} if self.slowest else None,
            "tokens": [{"name": k, "in": self.tokens[k][0], "out": self.tokens[k][1]} for k in names],
        }
