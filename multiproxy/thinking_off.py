"""Turning a model's thinking off when SkyrimNet asks for it (2026-10-07).

The real case: SkyrimNet sends `"reasoning": {"enabled": false}`, MultiProxy dropped that field for the local engines, and a Gemma 4 model wrote its reasoning first
and used up the reply budget before it wrote one word: the NPCs said nothing.

What each engine takes (tried against a real Ollama 0.40 with qwen3:8b, and a real LM Studio with Gemma 4):
  * Ollama's OpenAI-style route: `reasoning_effort: "none"` (also `reasoning: {"effort": "none"}`) turns thinking off (385 tokens down to 3). Its native `think: false`
    is NOT read on this route (the model kept thinking), so it is not used.
  * LM Studio: accepts `reasoning_effort: "none"` and `chat_template_kwargs: {"enable_thinking": false}` without complaint. Both are sent: whichever the model's
    own template listens to takes effect, and the other is ignored by it.
  * The built-in llama.cpp engine needs nothing here: it is started with `--reasoning off` (local_engine_flags.py). It never reaches this module at all (call_local_* do not
    call `_thinking_for`).
  * A llama.cpp server MultiProxy did NOT start (a Courier's own, a hand-run `llama-server`, an AI server added by address that answers like llama.cpp) reads neither
    `reasoning: {"enabled": false}` nor `thinking`: tried 2026-10-25 against build b11886, the model kept thinking and the thinking used up the whole reply (19 of 20 Action replies
    lost; see docs/roadmap/tested-settings-research-2026-10.md). It reads both `reasoning_effort: "none"` and `chat_template_kwargs: {"enable_thinking": false}` (tested working
    on Gemma 4 and Qwen 3.5), so it gets both, like LM Studio. Unknown fields are ignored by it (200, not an error); a build that did refuse them is retried once without them by
    the shared request helper (`_unknown_fields_from_error`).
A model with no thinking switch ignores the field. Nothing else is sent to an engine that does not take it.

SkyrimNet Beta 27 gave "Allow Reasoning" six settings (off, model default, minimal, low, medium, high) and Beta 28 uses them per model (2026-10-20). Every way a request can say
it (`reasoning: false`, `reasoning: {"enabled": false}`, `reasoning: {"effort": ...}`, `reasoning_effort`, `thinking: {"type": "disabled"}`) is read by `level_of`:
  * off ("none", "off", "disabled", false): thinking is turned off, as above.
  * minimal: **treated as off for Ollama and LM Studio.** Neither has a minimal level of its own (Ollama's levels are none, low, medium, high; the gpt-oss models that take levels
    have low, medium and high), and the nearest real level to "hardly any thinking" is none. SkyrimNet itself retries a model that refuses "off" at minimal, so a model that can
    never stop thinking still gets a request it takes.
  * low, medium, high: sent as `reasoning_effort` (the one spelling both engines read), so the level the player chose reaches the model. A model with no levels ignores it.
  * model default (`reasoning: true`, `enabled: true`, no field): nothing is sent.
"""
from typing import Optional

THINKS_NOTE = "This model thinks before it answers, so dialogue can be slow."

_OFF_WORDS = frozenset({"none", "off", "minimal", "disabled", "false"})
ENGINES = ("ollama", "lmstudio", "llamacpp")
SWITCH_FIELDS = frozenset({"reasoning_effort", "chat_template_kwargs"})
BOTH_SWITCHES = ("lmstudio", "llamacpp")           # the engines that get `chat_template_kwargs` as well as `reasoning_effort`
LEVELS = ("low", "medium", "high")


def _word_level(word) -> Optional[str]:
    """One effort word as a level: "off", "minimal", "low", "medium", "high", or None for a word that says nothing (default, auto, a level this does not know)."""
    if word is False:
        return "off"
    w = str(word or "").strip().lower()
    if w in ("none", "off", "disabled", "false"):
        return "off"
    return w if w == "minimal" or w in LEVELS else None


def level_of(extra: dict) -> Optional[str]:
    """What the request asks of thinking: "off", "minimal", "low", "medium", "high", or None (the model's own default, or something this does not know)."""
    reasoning = extra.get("reasoning")
    if reasoning is False:
        return "off"
    if isinstance(reasoning, dict):
        if reasoning.get("enabled") is False:
            return "off"
        got = _word_level(reasoning.get("effort"))
        if got:
            return got
    elif isinstance(reasoning, str):
        got = _word_level(reasoning)
        if got:
            return got
    got = _word_level(extra.get("reasoning_effort"))
    if got:
        return got
    thinking = extra.get("thinking")
    if isinstance(thinking, dict) and str(thinking.get("type") or "").lower() == "disabled":
        return "off"
    return None


def off_requested(extra: dict) -> bool:
    """The request asks for no thinking (or, for a local engine, the least there is): `reasoning: false`, `reasoning.enabled == false`, a `reasoning` effort or `reasoning_effort`
    of none/minimal, or `thinking: {"type": "disabled"}`."""
    return level_of(extra) in ("off", "minimal")


def normalised(extra: dict) -> dict:
    """For an AI with a `thinking` field of its own and a `reasoning_effort` (GLM, DeepSeek): the wish said in one way. Off or minimal: every spelling of it removed, so the AI's
    own "thinking off" is what is sent. Low, medium or high: `reasoning_effort` with that level and no other spelling. Nothing asked: untouched. A request that already
    carries the AI's own `thinking` field is in its dialect and stays exactly as it is."""
    level = level_of(extra)
    if level is None or extra.get("thinking") is not None:
        return extra
    out = {k: v for k, v in extra.items() if k not in ("reasoning", "reasoning_effort", "thinking")}
    if level in LEVELS:
        out["reasoning_effort"] = level
    return out


def translate(engine: Optional[str], extra: dict) -> tuple[dict, bool]:
    """(the request's extra fields with thinking-off said the way `engine` understands it, whether that was done). An engine that is not one of ENGINES, or a request that
    does not ask for it, comes back untouched."""
    if engine not in ENGINES or not off_requested(extra):
        return extra, False
    out = {k: v for k, v in extra.items() if k not in ("reasoning", "thinking")}
    out["reasoning_effort"] = "none"
    if engine in BOTH_SWITCHES and not isinstance(out.get("chat_template_kwargs"), dict):
        out["chat_template_kwargs"] = {"enable_thinking": False}
    return out, True


def translate_level(engine: Optional[str], extra: dict) -> tuple[dict, bool]:
    """(the extra fields with a low, medium or high level said as `reasoning_effort`, whether that was done). Only for the engines in ENGINES; anything else, and any other level,
    comes back untouched."""
    if engine not in ENGINES or level_of(extra) not in LEVELS:
        return extra, False
    out = {k: v for k, v in extra.items() if k not in ("reasoning", "thinking")}
    out["reasoning_effort"] = level_of(extra)
    return out, True


def engine_of_program(program: str) -> Optional[str]:
    """A Courier's program id (`ollama`, `lmstudio`, `llamacpp`) or an AI server's label (`Ollama`, `LM Studio`, `llama.cpp`) as one of ENGINES, else None.
    "MultiProxy" (another MultiProxy as the model server) and "AI server" (unknown) are not engines: the first does its own translating, the second is not guessed."""
    p = (program or "").replace(" ", "").replace(".", "").lower()
    return p if p in ENGINES else None


class Thinkers:
    """The models that wrote thoughts although thinking was turned off for the request, so the card can say they are slow. Kept for this run only."""

    def __init__(self):
        self._keys: set = set()

    def note(self, key: str) -> None:
        self._keys.add(key)

    def add_to(self, key: str, note: str) -> str:
        """`note` (a card's muted text) with THINKS_NOTE added when `key` is a model that thinks anyway."""
        if key not in self._keys:
            return note
        return f"{note} · {THINKS_NOTE}" if note else THINKS_NOTE


THINKERS = Thinkers()
