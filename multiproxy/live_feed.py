"""The Live view (Routing, step 6, 2026-10-02): requests as they happen and, for models that share it, their reasoning as it is written.

Rules this module is built around:
- **Nothing is written to disk.** A short tail of request lines (the last TAIL_LINES) is kept in memory so an opened view isn't empty; thinking is never kept.
- **Closed costs nothing.** Request lines are one small dict appended to a bounded deque. Thinking is only read out of a reply while somebody is watching it
  (`watching_thinking()`), so with the view closed a streamed reply is not even parsed for it.
- **A slow page can never slow a request.** Every watcher has its own small queue that is only ever written with `put_nowait`; when it is full the oldest event is
  dropped (and counted), so `publish` never waits and never raises. A watcher that vanishes is removed when its stream ends.
- **Keys and addresses never appear.** Everything that goes out passes `scrub` (web addresses, IP addresses, key-looking strings are replaced).
- No MultiProxy import: it is handed what it needs.
"""
import asyncio
import collections
import json
import re
import time
from typing import Any, AsyncIterator, Optional

TAIL_LINES = 60                 # request lines kept in memory for a view that has just been opened
QUEUE_SIZE = 300                # events one watcher can be behind before the oldest are dropped
THINK_CHUNK = 600               # one thinking event carries at most this many characters (longer pieces are split)
HEARTBEAT_S = 15.0

_URL = re.compile(r"(?i)\b(?:https?|wss?|ftp)://\S+|\bwww\.\S+")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?\b")
_HOSTPORT = re.compile(r"(?i)\b(?:localhost|[a-z0-9-]+\.(?:local|lan|home|internal))(?::\d{2,5})?\b")
_KEYLIKE = re.compile(r"\b(?:sk|pk|rk|key|tok|ghp|gho|xox[a-z]|AIza|AKIA|Bearer)[-_ ]?[A-Za-z0-9_\-]{16,}\b|\b[A-Za-z0-9_\-]{32,}\b")


def scrub(text: Any) -> str:
    """Anything that could be a web address, an IP address or a key is replaced; nothing else is touched."""
    s = str(text) if text is not None else ""
    s = _URL.sub("[address]", s)
    s = _IPV4.sub("[address]", s)
    s = _HOSTPORT.sub("[address]", s)
    return _KEYLIKE.sub("[key]", s)


def _s(v: float) -> str:
    return f"{v:.1f} s"


_WHY = {"too slow": "was too slow", "busy": "was busy", "empty reply": "gave an empty reply"}


def _why(why: str) -> str:
    if why.startswith("failed"):
        return "failed" + (why[len("failed"):] if "(" in why else "")
    return _WHY.get(why, why)


def request_event(rec: Any) -> dict:
    """One request line from a finished record: only facts the record already holds. `rec` is a request_facts.RequestRecord."""
    guessed = rec.job_how == "recognised" and not rec.job_certain and rec.job_key not in (None, "other")
    job = rec.job or "Request"
    if guessed:
        job = "Looks like " + job
    ai = (rec.model or rec.provider or "no model")
    if rec.model and rec.provider and rec.provider != rec.model:
        ai += f" ({rec.provider})"
    if rec.stand_in:
        ai += f" (stand-in for {rec.stand_in})"
    bits = []
    if rec.questions:
        n = len(rec.questions)
        bits.append(f"{n} question" + ("" if n == 1 else "s"))
    if rec.queue_wait_s is not None and rec.queue_wait_s >= 0.05:
        bits.append(f"waited {_s(rec.queue_wait_s)} behind {', '.join(rec.queue_behind)}" if rec.queue_behind else f"waited {_s(rec.queue_wait_s)} in line")
    if rec.first_word_s is not None:
        bits.append(f"first word {_s(rec.first_word_s)}")
    if rec.total_s is not None:
        bits.append(_s(rec.total_s))
    if rec.tokens_in is not None or rec.tokens_out is not None:
        bits.append(", ".join(x for x in (f"{rec.tokens_in:,} in" if rec.tokens_in is not None else "", f"{rec.tokens_out:,} out" if rec.tokens_out is not None else "") if x))
    if rec.cost_note and rec.cost_note != "not reported":
        import ai_costs
        bits.append(ai_costs.format_cost(rec.cost_usd, rec.cost_note))
    handover = ""
    if rec.skipped:
        first = rec.skipped[0]
        handover = f"{first[0]} {_why(first[1])} → " if len(rec.skipped) == 1 else ", ".join(f"{a} {_why(w)}" for a, w in rec.skipped[:3]) + " → "
    failed = rec.outcome not in (None, "ok")
    if failed:
        bits.append(f"failed ({rec.outcome})")
    return {"k": "req", "id": rec.request_id, "t": time.strftime("%H:%M:%S"), "job": scrub(job), "jobKey": scrub(rec.job_key or "other"), "guess": bool(guessed),
            "handover": scrub(handover), "ai": scrub(ai), "answered": bool(handover) and not failed, "failed": failed, "detail": scrub(" · ".join(b for b in bits if b))}


class Feed:
    def __init__(self) -> None:
        self.tail: collections.deque = collections.deque(maxlen=TAIL_LINES)
        self._watchers: set = set()
        self.dropped = 0

    # -- the request side: always cheap, never waits ----------------------------------------------------------------------------------------------------------
    def request(self, rec: Any) -> None:
        try:
            ev = request_event(rec)
        except Exception:                                    # the view is a convenience: never a reason to hurt a request
            return
        self.tail.append(ev)
        self._fan_out(ev)

    def watching_thinking(self) -> bool:
        return any(w.thinking for w in self._watchers)

    def thinking(self, request_id: str, job: str, text: str) -> None:
        """A piece of a model's reasoning as it arrives. Dropped at once when nobody is watching thinking."""
        if not text or not self.watching_thinking():
            return
        text = scrub(text)
        for i in range(0, len(text), THINK_CHUNK):
            self._fan_out({"k": "think", "id": request_id, "job": scrub(job or "Request"), "text": text[i:i + THINK_CHUNK]}, thinking=True)

    def _fan_out(self, ev: dict, thinking: bool = False) -> None:
        for w in list(self._watchers):
            if thinking and not w.thinking:
                continue
            if w.queue.full():
                try:
                    w.queue.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                w.queue.put_nowait(ev)
            except asyncio.QueueFull:
                self.dropped += 1

    # -- the page side ----------------------------------------------------------------------------------------------------------------------------------------
    def snapshot(self) -> list:
        return list(self.tail)

    async def stream(self, thinking: bool = True, heartbeat_s: float = HEARTBEAT_S) -> AsyncIterator[str]:
        """Server-sent events for one open page: the tail first, then each new event. Ends (and stops counting as watched) when the page goes."""
        w = _Watcher(thinking)
        self._watchers.add(w)
        try:
            yield "retry: 3000\n\n"
            for ev in self.snapshot():
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(w.queue.get(), heartbeat_s)
                except asyncio.TimeoutError:
                    yield ": still here\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            self._watchers.discard(w)

    @property
    def watchers(self) -> int:
        return len(self._watchers)


class _Watcher:
    def __init__(self, thinking: bool) -> None:
        self.thinking = thinking
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)


def reasoning_from_event(event: str) -> str:
    """The reasoning text in one streamed reply event ('data: {...}'), or ''. Both spellings models use (`reasoning_content`, `reasoning`)."""
    if not event.startswith("data:") or '"reasoning' not in event:
        return ""
    try:
        obj = json.loads(event[5:].strip())
        delta = ((obj.get("choices") or [{}])[0] or {}).get("delta") or {}
    except (ValueError, AttributeError, IndexError, TypeError):
        return ""
    for k in ("reasoning_content", "reasoning"):
        v = delta.get(k)
        if isinstance(v, str) and v:
            return v
    return ""
