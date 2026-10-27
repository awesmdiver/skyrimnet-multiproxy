"""Keep a local Ollama model loaded (2026-10-07), so the first line of dialogue after a quiet spell is not a cold start.

Ollama drops a model after about 5 idle minutes; loading it again takes ~30 s and SkyrimNet gives up after ~15 s. Ollama's OpenAI-style endpoint (the one real requests use)
ignores a keep-alive value, its own API (/api/generate) honours it. So this sends one empty request to the native API for each model that is the FIRST choice of a route:
at start, when a route changes (`kick`), and whenever /api/ps says the model is not loaded. Never per request, never in the way of a real one (it runs as a background task).

Nothing from MultiProxy is imported here; multiproxy.py hands in the Ollama calls and the free graphics memory.
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional

KEEP_FOREVER = -1                  # Ollama's keep_alive: stay loaded until Ollama stops or another model needs the room
OLLAMA_DEFAULT = "5m"              # what Ollama itself uses when nothing is said
FAIL_REST_S = 600.0                # a model that failed to load is not tried again for ten minutes
CHECK_EVERY_S = 30.0
LOAD_TIMEOUT_S = 300

LOADED, NOT_LOADED, LOADING, NO_ROOM, FAILED, UNKNOWN = "loaded", "not_loaded", "loading", "no_room", "failed", "unknown"

# The words on the model's card and beside its checkbox (reviewed wording comes from the design side's Gemini pass).
LABEL = "Keep this model loaded"
WHY = "Uses graphics memory all the time."
WORDS = {LOADED: "Loaded", NOT_LOADED: "Not loaded yet", LOADING: "Loading", NO_ROOM: "Not loaded: not enough graphics memory", FAILED: "Not loaded: it would not start", UNKNOWN: ""}
DOT = {LOADED: "on", NOT_LOADED: "none", LOADING: "warn", NO_ROOM: "warn", FAILED: "warn", UNKNOWN: "none"}


def full_name(model: str) -> str:
    """Ollama lists a model with its tag ('gemma4:latest'); a route may leave it off."""
    return model if ":" in model else model + ":latest"


def first_choices(routes: dict, expand: Callable[[str], list], is_ollama: Callable[[str], bool], strip: Callable[[str], str]) -> list:
    """The Ollama models (full names, no repeats) that are the first choice of at least one saved route."""
    out = []
    for r in (routes or {}).values():
        ais = [x for a in (r.get("ais") or [])[:1] for x in expand(a)][:1]
        for ai in ais:
            if is_ollama(ai):
                n = full_name(strip(ai))
                if n not in out:
                    out.append(n)
    return out


class KeepWarm:
    def __init__(self, *, ps: Callable[[], Awaitable[Optional[dict]]], sizes: Callable[[], Awaitable[Optional[dict]]], load: Callable[[str, object], Awaitable[None]],
                 free_bytes: Callable[[], Awaitable[Optional[int]]], clock: Callable[[], float] = time.time, log=None):
        self._ps, self._sizes, self._load, self._free = ps, sizes, load, free_bytes
        self._clock = clock
        self._log = log
        self.states: dict = {}              # full model name -> a state above
        self.reason: dict = {}              # full model name -> why it failed (short, plain)
        self._failed_at: dict = {}
        self._loading: dict = {}            # full model name -> the running task
        self._pinned: set = set()           # models this class asked to stay loaded (and so puts back when the person turns it off)
        self.asked: list = []               # (model, keep_alive) of every request sent, for the tests and the log

    async def _send(self, model: str, keep_alive) -> None:
        self.asked.append((model, keep_alive))
        await self._load(model, keep_alive)

    async def _warm(self, model: str, keep_alive) -> None:
        self.states[model] = LOADING
        try:
            await asyncio.wait_for(self._send(model, keep_alive), LOAD_TIMEOUT_S)
            self._pinned.add(model)
            self.states[model] = LOADED
            self.reason.pop(model, None)
        except Exception as e:
            self._failed_at[model] = self._clock()
            self.states[model] = FAILED
            self.reason[model] = str(e)[:160] or e.__class__.__name__
            if self._log:
                self._log(f"Keeping {model} loaded didn't work ({e.__class__.__name__}); will not try again for {int(FAIL_REST_S // 60)} minutes")
        finally:
            self._loading.pop(model, None)

    async def tick(self, wanted: list, off=(), keep_alive=KEEP_FOREVER) -> None:
        """One look: start a warm-up for each wanted model that is not loaded (and fits, and has not just failed); put back Ollama's default for a model the person switched off."""
        loaded = await self._ps()
        if loaded is None:                                        # Ollama not running: nothing to say, nothing to send
            for m in wanted:
                if m not in self._loading:
                    self.states[m] = UNKNOWN
            return
        sizes = None
        free = None
        for m in wanted:
            if m in self._loading:
                continue
            if m in off:
                self.states[m] = LOADED if m in loaded else NOT_LOADED
                if m in self._pinned:
                    self._pinned.discard(m)
                    if m in loaded:
                        try:
                            await self._send(m, OLLAMA_DEFAULT)           # back to Ollama's own idle time
                        except Exception:
                            pass
                continue
            if m in loaded:
                self.states[m] = LOADED
                # Loaded by a real request with Ollama's short idle time? Ask once for "stay loaded".
                if m not in self._pinned:
                    try:
                        await self._send(m, keep_alive)
                        self._pinned.add(m)
                    except Exception:
                        pass
                continue
            if self._clock() - self._failed_at.get(m, -FAIL_REST_S * 2) < FAIL_REST_S:
                self.states[m] = FAILED
                continue
            if sizes is None:
                sizes = await self._sizes() or {}
                free = await self._free()
            size = sizes.get(m)
            if size and free is not None and size > free:
                self.states[m] = NO_ROOM
                continue
            self.states[m] = LOADING
            self._loading[m] = asyncio.ensure_future(self._warm(m, keep_alive))

    async def wait(self) -> None:
        """Wait for the warm-ups now running (tests)."""
        if self._loading:
            await asyncio.gather(*list(self._loading.values()), return_exceptions=True)

    def card(self, model: str, off=()) -> dict:
        """What a model's card shows: {"on": bool, "state", "words", "dot"}."""
        full = full_name(model)
        st = self.states.get(full, UNKNOWN)
        return {"on": full not in off, "state": st, "words": WORDS[st], "dot": DOT[st], "problem": self.reason.get(full, "") if st == FAILED else ""}
