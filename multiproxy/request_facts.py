"""What arrived and what happened to it (2026-10-02): the facts about one request, in one place.

Two jobs, both small and with nothing from MultiProxy imported here (so they are tested on their own):

1. `Intake`: what SkyrimNet sent along with a request that is not ordinary plumbing (extra headers, extra body settings, its user agent), turned into log
   lines. A new hint from SkyrimNet shows up in the log the first time it appears and then stays quiet; every request logs it when full capture is on.
   Anything that looks like a key or token is masked before a line is made.
2. `RequestRecord`: one small record per request (which AI answered, the model really used, stand-in or retry, time to first word, total time, tokens, final
   result, how long it waited in line). The reply's log line is written from it, and `response_headers()` is the ONE place that will turn it into response
   headers for SkyrimNet's routing and telemetry. The header names are not agreed with SkyrimNet's author yet, so it returns none today.
"""
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Optional

# Ordinary plumbing: not worth a line. Everything else a client sends is a hint worth seeing.
PLUMBING_HEADERS = frozenset({
    "content-type", "content-length", "content-encoding", "transfer-encoding", "host", "connection", "keep-alive", "accept", "accept-encoding",
    "accept-language", "user-agent", "cache-control", "pragma", "expect", "te", "upgrade", "origin", "referer",
})
# A header whose name says it holds a secret is shown by name only, whatever its value looks like.
_SECRET_NAME = re.compile(r"key|token|auth|secret|cookie|password|credential|signature", re.I)
MASKED = "***"
VALUE_MAX = 120                     # a longer value is cut (and says so)
VALUES_PER_NAME_MAX = 12            # a header that changes on every request is named, not logged every time
USER_AGENTS_MAX = 20


def _shown(name: str, value: str, redact: Callable[[str], str]) -> str:
    if _SECRET_NAME.search(name):
        return MASKED
    value = redact(str(value))
    return value if len(value) <= VALUE_MAX else value[:VALUE_MAX] + "... (cut)"


def hint_headers(headers, redact: Callable[[str], str] = lambda s: s) -> list:
    """[(name, shown value)] for every header that is not ordinary plumbing, in the order sent, names lower-cased. `headers` is anything with .items()
    (Starlette's Headers, a dict)."""
    out = []
    try:
        items = list(headers.items())
    except Exception:
        return out
    for name, value in items:
        lname = str(name).lower()
        if lname in PLUMBING_HEADERS or lname.startswith("sec-"):
            continue
        out.append((lname, _shown(lname, value, redact)))
    return out


class Intake:
    """Remembers what it has already shown, so each new hint is logged once. One per program."""

    def __init__(self, redact: Callable[[str], str] = lambda s: s):
        self.redact = redact
        self._values: dict = {}            # header name -> set of values already shown
        self._capped: set = set()          # names that changed too often and are now only named
        self._agents: set = set()
        self._settings: set = set()        # (kind, frozenset of extra body setting names) already shown

    def observe(self, request_id: str, path: str, kind: str, headers, extra_settings=(), *, capture_all: bool = False) -> list:
        """The log lines (strings, without the timestamp) this request earns: its user agent on first contact, the extra headers when any is new (or always
        with `capture_all`), and the extra body settings when that set of names is new (or always with `capture_all`)."""
        lines = []
        agent = None
        try:
            agent = {str(k).lower(): v for k, v in headers.items()}.get("user-agent")
        except Exception:
            pass
        if agent:
            agent = self.redact(str(agent))[:VALUE_MAX]
            if agent not in self._agents and len(self._agents) < USER_AGENTS_MAX:
                self._agents.add(agent)
                lines.append(f'[{request_id}] First contact from user agent "{agent}"')
        hints = hint_headers(headers, self.redact)
        new = False
        for name, value in hints:
            seen = self._values.setdefault(name, set())
            if value in seen or name in self._capped:
                continue
            if len(seen) >= VALUES_PER_NAME_MAX:
                self._capped.add(name)
                lines.append(f"[{request_id}] The header {name} keeps changing; from now on its value is only logged with full capture on")
                continue
            seen.add(value)
            new = True
        if hints and (new or capture_all):
            lines.append(f"[{request_id}] SkyrimNet sent along ({kind} via {path}): " + ", ".join(f"{n}={v}" for n, v in hints))
        names = tuple(sorted(self.redact(str(s))[:60] for s in extra_settings))
        if names:
            key = (kind, frozenset(names))
            if key not in self._settings or capture_all:
                self._settings.add(key)
                lines.append(f"[{request_id}] SkyrimNet's {kind} request also carried these settings ({path}): " + ", ".join(names))
        return lines


@dataclass
class RequestRecord:
    request_id: str
    path: str = ""
    kind: str = "chat"                       # "chat" | "decision"
    job: Optional[str] = None                # the label the Speeds tab uses for what this model is for (Dialogue, Decisions, ...)
    questions: tuple = ()                    # a decision's question names
    provider: Optional[str] = None           # who answered: GLM, TypeSafe, a device's nickname, ...
    account: Optional[str] = None            # which account of that provider, when MultiProxy knows (none today: one login per provider)
    model: Optional[str] = None              # the model actually used
    stand_in: Optional[str] = None           # set when something other than the first choice answered: who it stood in for ("TypeSafe", a device's name)
    retried: bool = False                    # the reply was asked for a second time
    reply_repair: Optional[str] = None       # "fine", "repaired" or "asked_again" for a reply the program checked (what its Speeds row gets too); Check it like SkyrimNet reads it
    first_word_s: Optional[float] = None
    total_s: Optional[float] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    queue_wait_s: Optional[float] = None     # how long it waited in line before going to the AI
    overrides: dict = field(default_factory=dict)     # the player's fine-tune settings for this request's job (Routing tab), applied as it goes to the model
    override_applied: list = field(default_factory=list)    # which of them the answering model took
    override_skipped: list = field(default_factory=list)    # and which it can't take (never sent)
    queue_rank: Optional[int] = None         # where its job stands in the player's "what goes first" order (0 = first); None until the job is known
    queue_aging_s: Optional[float] = None    # a waiting request moves up one place in that order every this many seconds
    queue_behind: list = field(default_factory=list)   # the jobs that went ahead of it in the line although they came later
    status: Optional[int] = None             # the HTTP status of the answer, when known
    job_key: Optional[str] = None            # SkyrimNet's own name for the job (memory, vision, ...), "other" when not recognised
    job_how: Optional[str] = None            # how the job was known: "hint" (SkyrimNet said), "name" (job/<name> asked for), "recognised" (our own reading: a guess unless certain)
    job_certain: bool = True                 # False for a guess: the screen says "Looks like ..."
    routed: bool = False                     # a route was in force for this job
    skipped: list = field(default_factory=list)   # [(ai, why)] tried or passed over before the one that answered
    cost_usd: Optional[float] = None         # what it cost, when the AI reported it or its own prices give it; 0.0 for a free AI
    cost_note: Optional[str] = None          # "free", "included in your subscription", "reported by the AI", "worked out from the AI's own prices", "not reported"
    timings: Optional[dict] = None           # a local llama.cpp's own numbers for the reply: prompt_n, cache_n (remembered), prompt_ms, predicted_n, predicted_ms, tokens_per_second
    outcome: Optional[str] = None            # "ok", or why it failed (the Speeds tab's reason words)
    started: float = field(default_factory=time.time)
    in_flight: Optional[int] = None          # how many requests were being answered when this one started, itself included (saved with its row, for the Overview's "Most at once")
    # MultiProxy's own added time (2026-10-02): perf_counter marks, so the clock's coarse ticks on older Pythons can't blur a few milliseconds.
    # own = (now - arrival) - the time the AI was working (every attempt: slot given -> AI's last byte) - the time spent standing in line.
    arrived_pc: float = field(default_factory=time.perf_counter)
    waited_s: float = 0.0                    # total time spent standing in the request line, across attempts
    attempt_pc: Optional[float] = None       # when the current attempt was handed to the AI (its line place was given)
    ai_done_pc: Optional[float] = None       # when the AI's last byte of the current attempt arrived, if marked before the bookkeeping
    ai_s: float = 0.0                        # total AI working time of the attempts that ended

    def attempt_started(self, waited: float) -> None:
        self.waited_s += waited
        self.attempt_pc = time.perf_counter()
        self.ai_done_pc = None

    def ai_returned(self) -> None:
        """The AI's whole answer is in (a plain reply): everything after this moment is MultiProxy's own."""
        if self.attempt_pc is not None and self.ai_done_pc is None:
            self.ai_done_pc = time.perf_counter()

    def attempt_ended(self) -> None:
        if self.attempt_pc is None:
            return
        end = self.ai_done_pc if self.ai_done_pc is not None else time.perf_counter()
        self.ai_s += max(end - self.attempt_pc, 0.0)
        self.attempt_pc = self.ai_done_pc = None

    def own_ms(self) -> Optional[float]:
        """Milliseconds MultiProxy itself added to this chat request so far (None for a decision request: not measured there)."""
        if self.kind != "chat":
            return None
        return round(max((time.perf_counter() - self.arrived_pc) - self.ai_s - self.waited_s, 0.0) * 1000.0, 3)

    def note_timings(self, obj) -> None:
        """A local llama.cpp reply's (or last stream chunk's) `timings`, kept as they are."""
        import ai_costs
        t = ai_costs.timings_from(obj)
        if t:
            self.timings = t

    def note_usage(self, usage) -> None:
        """Token counts from a provider's usage object, whichever style it uses (OpenAI: prompt/completion; Anthropic and TypeSafe: input/output)."""
        if not isinstance(usage, dict):
            return
        import ai_costs
        c = ai_costs.reported_cost(usage)
        if c is not None:
            self.cost_usd, self.cost_note = c, ai_costs.NOTE_REPORTED
        if isinstance(usage.get("timings"), dict):
            self.note_timings({"timings": usage["timings"]})
        for attr, keys in (("tokens_in", ("prompt_tokens", "input_tokens")), ("tokens_out", ("completion_tokens", "output_tokens"))):
            for k in keys:
                v = usage.get(k)
                if isinstance(v, int) and not isinstance(v, bool):
                    setattr(self, attr, v)
                    break

    def line(self) -> str:
        """The reply's one log line, written only from the facts above. Parts that aren't known are left out."""
        what = "decision" if self.kind == "decision" else "chat"
        if self.questions:
            what += " (" + ", ".join(self.questions[:6]) + (", ..." if len(self.questions) > 6 else "") + ")"
        parts = [what + (f" via {self.path}" if self.path else "")]
        if self.job:
            parts.append(self.job + (" (looks like)" if self.job_how == "recognised" and not self.job_certain and self.job_key not in (None, "other") else ""))
        who = " ".join(x for x in (self.provider, self.model) if x)
        if self.account:
            who += f" ({self.account})"
        if who:
            parts.append(who)
        if self.stand_in:
            parts.append(f"stand-in for {self.stand_in}")
        if self.retried:
            parts.append("asked again")
        if self.total_s is not None:
            t = f"{self.total_s:.1f}s"
            extra = []
            if self.first_word_s is not None:
                extra.append(f"first word {self.first_word_s:.1f}s")
            if self.queue_wait_s is not None:
                extra.append(f"waited {self.queue_wait_s:.1f}s in line" + (f" behind {', '.join(self.queue_behind)}" if self.queue_behind else ""))
            parts.append(t + (f" ({', '.join(extra)})" if extra else ""))
        elif self.queue_wait_s is not None:
            parts.append(f"waited {self.queue_wait_s:.1f}s in line" + (f" behind {', '.join(self.queue_behind)}" if self.queue_behind else ""))
        if self.tokens_in is not None or self.tokens_out is not None:
            parts.append(f"in={self.tokens_in if self.tokens_in is not None else '?'} out={self.tokens_out if self.tokens_out is not None else '?'} tok")
        if self.cost_note:
            import ai_costs
            parts.append(ai_costs.format_cost(self.cost_usd, self.cost_note))
        if self.timings and self.timings.get("cache_n") is not None and self.timings.get("prompt_n") is not None:
            parts.append(f"remembered {int(self.timings['cache_n'])} of {int(self.timings['cache_n'] + self.timings['prompt_n'])} prompt tokens")
        if self.override_applied or self.override_skipped:
            bits = []
            if self.override_applied:
                bits.append("fine-tuned: " + ", ".join(f"{k} {self.overrides.get(k)}" for k in self.override_applied))
            if self.override_skipped:
                bits.append("not used here: " + ", ".join(self.override_skipped))
            parts.append("; ".join(bits))
        if self.skipped:
            parts.append("passed over " + ", ".join(f"{a} ({why})" for a, why in self.skipped[:6]))
        result = "ok" if self.outcome in (None, "ok") else f"failed ({self.outcome})"
        if self.status is not None and self.status != 200:
            result += f", status {self.status}"
        parts.append(result)
        return f"[{self.request_id}] answered: " + " | ".join(parts)


def response_headers(rec: Optional[RequestRecord]) -> dict:
    """The ONE place the facts above become response headers for SkyrimNet. Returns none for now: the header names are still to be agreed with SkyrimNet's
    author. When they are, this is the only function to change. What it could send today, from `rec`: provider, account, model, stand_in, retried,
    first_word_s, total_s, tokens_in, tokens_out, queue_wait_s, status. (Streamed replies have already sent their headers by the time the totals are known,
    so first-word and total time would go in a trailing event or only on non-streamed replies: a design question for the agreement.)"""
    return {}


CURRENT: ContextVar = ContextVar("multiproxy_request_record", default=None)


def begin(request_id: str, path: str, kind: str = "chat") -> RequestRecord:
    rec = RequestRecord(request_id=request_id, path=path, kind=kind)
    CURRENT.set(rec)
    return rec


def current() -> Optional[RequestRecord]:
    return CURRENT.get()
