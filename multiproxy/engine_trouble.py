"""Why a local AI engine (Ollama, LM Studio) did not answer, in one short plain sentence, and what counts as an empty reply (2026-10-07).

The real case: a Dialogue model on Ollama could not load (the engine ran out of memory while starting it). Ollama said so after nearly three minutes with an HTTP 500, but
SkyrimNet had hung up long before, and no page said why. The second case: a model answered with nothing at all, MultiProxy logged "ok", and the NPCs stayed silent.

Nothing from MultiProxy is imported here. What an engine returns is read for its MEANING only: the sentences below never carry a path, a size, a code or the engine's own
words, so they are safe to show on any page.
"""
import json
import re
import time
from dataclasses import dataclass
from typing import Optional

LOCAL_ENGINES = ("Ollama", "LM Studio")        # engines MultiProxy cannot see inside: their errors are read, and a request that never starts is given up on

FIRST_WORD_S = 20.0           # how long a local engine gets to say its first word. SkyrimNet's own patience is about 15 s for a line (30 s at most), so waiting longer than 30 s is waiting for nobody.
WHOLE_REPLY_S = 30.0          # a reply that is not streamed has no first word: the whole reply gets this long
SKYRIMNET_PATIENCE_S = 30.0   # no budget MultiProxy picks for itself is longer than this
TEST_PATIENCE_S = 240.0       # the Test button waits for a cold model to load (that is what it is for)
PROBE_PATIENCE_S = 240.0      # the load check after a given-up request waits this long for the engine's own answer
PROBE_EVERY_S = 300.0         # ... and is made at most this often per model
TROUBLE_KEEP_S = 3600.0       # a note about a model is forgotten after an hour, or the moment it answers


@dataclass(frozen=True)
class Trouble:
    clause: str       # what comes after the model's name on a card: "couldn't load (ran out of memory while starting)"
    sentence: str     # the same as a sentence of its own, for an error message and the log
    kind: str


EMPTY_CLAUSE = "answered with nothing"
EMPTY_SENTENCE = "It answered with nothing."
THOUGHT_CLAUSE = "wrote nothing (it only thought)"
THOUGHT_SENTENCE = "It spent the whole time thinking and wrote nothing."
SLOW_CLAUSE = "took too long to start answering"

_MEMORY = re.compile(r"out of memory|unable to allocate|failed to allocate|cudamalloc|cuda_host|requires more (system )?memory|insufficient (memory|vram)|not enough (memory|vram)|\boom\b")
_MAYBE_MEMORY = re.compile(r"resource limitations")
_TOO_BIG = re.compile(r"too (big|large) for|does not fit|doesn't fit|exceeds .{0,40}(vram|gpu|memory)")
_NOT_LOADED = re.compile(r"no models? (are )?loaded|model (is )?not loaded|no model loaded")
_MISSING = re.compile(r"not found|try pulling|no such model|unknown model|does not exist|model_not_found")
_LOADING = re.compile(r"still loading|is loading|loading model|model is being loaded")
_LOAD_FAILED = re.compile(r"failed to load|unable to load|runner process|has terminated|exit status|llama runner|model load")


def explain(provider: str, status: Optional[int], body: str = "") -> Optional[Trouble]:
    """What an engine's error answer means. None when it is not an engine problem worth a sentence (a 4xx the request itself caused, say)."""
    text = (body or "").lower()
    p = provider
    if _MEMORY.search(text):
        return Trouble("couldn't load (ran out of memory while starting)", f"{p} couldn't load this model because it ran out of memory.", "memory")
    if _TOO_BIG.search(text):
        return Trouble("couldn't load (too big for the graphics card)", f"{p} couldn't load this model because it's too big for your graphics card.", "too-big")
    if _NOT_LOADED.search(text):
        return Trouble("has no model loaded", f"{p} has no model loaded.", "not-loaded")
    if status == 404 or _MISSING.search(text):
        return Trouble("doesn't have this model", f"{p} doesn't have this model.", "missing")
    if _LOADING.search(text):
        return Trouble("is still loading this model", f"{p} is still loading this model. Try again in a moment.", "loading")
    if _MAYBE_MEMORY.search(text):
        return Trouble("couldn't load (it may have run out of memory)", f"{p} couldn't load this model (it may have run out of memory).", "maybe-memory")
    if _LOAD_FAILED.search(text):
        return Trouble("couldn't load (it stopped while starting)", f"{p} couldn't load this model (it stopped while starting).", "load-failed")
    if status is not None and status >= 500:
        return Trouble("couldn't answer (its own log may say why)", f"{p} couldn't answer. Its log may say why.", "unknown")
    return None


def not_running(provider: str) -> Trouble:
    return Trouble("isn't running", f"{provider} isn't running.", "not-running")


def dropped(provider: str) -> Trouble:
    return Trouble("stopped answering", f"{provider} stopped answering.", "dropped")


def too_slow(provider: str) -> Trouble:
    return Trouble(SLOW_CLAUSE, f"{provider} took too long to answer. If the model is still loading, wait a moment and try again.", "slow")


def empty(thought: bool = False) -> Trouble:
    return Trouble(THOUGHT_CLAUSE, THOUGHT_SENTENCE, "thought") if thought else Trouble(EMPTY_CLAUSE, EMPTY_SENTENCE, "empty")


# ---- an empty reply ----

_TAG = re.compile(r"<[^<>]*>|\[[^\[\]]*\]")
_OPEN_TAG_TAIL = re.compile(r"[<\[][^<>\[\]]*$")


def says_nothing(text, *, structured: bool = False) -> bool:
    """True for a reply with no words in it: nothing, only whitespace, or only tags. A reply that must be JSON (`structured`) is empty only when it is blank, since `{}` and `[]` can be a real answer."""
    if not isinstance(text, str):
        return True
    if structured:
        return not text.strip()
    stripped = _OPEN_TAG_TAIL.sub("", _TAG.sub("", text))
    return not re.search(r"\w", stripped)


def event_facts(event: str) -> tuple:
    """(reply text, the reply carried 'thinking' text, a tool call, the error it carries or None) of one `data: {...}` stream event."""
    stripped = event.strip()
    if not stripped.startswith("data: ") or "\n" in stripped:
        return "", False, False, None
    try:
        obj = json.loads(stripped[6:])
    except (json.JSONDecodeError, ValueError):
        return "", False, False, None
    if not isinstance(obj, dict):
        return "", False, False, None
    err = obj.get("error")
    if err:
        return "", False, False, (err.get("message") if isinstance(err, dict) else str(err)) or "error"
    choices = obj.get("choices")
    delta = choices[0].get("delta") if choices and isinstance(choices[0], dict) else None
    if not isinstance(delta, dict):
        return "", False, False, None
    content = delta.get("content")
    thought = delta.get("reasoning_content") or delta.get("reasoning")
    return (content if isinstance(content, str) else ""), bool(thought), bool(delta.get("tool_calls") or delta.get("function_call")), None


# ---- what is known about each model right now ----

class TroubleBook:
    """The latest trouble per model (keyed like the Speeds cards), kept for an hour or until the model answers."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._by: dict = {}

    def set(self, key: str, clause: str, sentence: str = "") -> None:
        self._by[key] = {"clause": clause, "sentence": sentence or clause, "at": self.clock()}

    def clear(self, key: str) -> None:
        self._by.pop(key, None)

    def get(self, key: str) -> Optional[dict]:
        t = self._by.get(key)
        if t is None:
            return None
        if self.clock() - t["at"] > TROUBLE_KEEP_S:
            self._by.pop(key, None)
            return None
        return t

    def clause(self, key: str) -> Optional[str]:
        t = self.get(key)
        return t["clause"] if t else None
