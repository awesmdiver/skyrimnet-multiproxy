"""Keeping the Claude lane's hidden thinking from eating a small answer budget (4.2.1), and noticing a cut-off answer.

Why: on the newer Claude models, leaving the `thinking` field out does NOT mean "no thinking": Claude Opus 5.5 and Claude Haiku 5.5 run adaptive thinking when it is omitted, and
thinking tokens are counted against `max_tokens`. SkyrimNet's small budgets (256 for the GameMaster job) were spent on thinking, so the reply was cut off with 0 to 168
characters and the JSON check failed (measured on the real lane 2026-11-10; see the handoff). Each model has its own off-switch (or none), so it is looked up per model here.

Measured through the subscription lane (tiny requests, effort medium):
  claude-sonnet-5-5   {"type": "between_tools"} accepted (200). {"type": "disabled"} is refused (400, per Anthropic's notes). No thinking block was seen with it omitted either.
  claude-haiku-5-5    omitted: thinks (256 tokens, 0 characters of answer). {"type": "disabled"} accepted at effort high or below (235 tokens, 406 characters).
  claude-opus-5-5     omitted: thinks (174 tokens, 2 characters). "disabled" and "between_tools" are both refused (400): thinking cannot be turned off. Only a bigger budget helps.
  claude-haiku-4-5    does not take `output_config.effort` at all (400), so the captured effort is left out for it.
An unknown or newer model: the field is left out (never send a value the model might refuse; a refusal is worse than a short answer) and the cut-off retry covers the rest.
"""
import re
from typing import Optional

OMIT = "omit"
BETWEEN_TOOLS = {"type": "between_tools"}
DISABLED = {"type": "disabled"}
_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]

# model id prefix (the longest match wins) -> how thinking is switched off. "omit" = nothing can be sent safely.
SWITCH = {
    "claude-sonnet-5-5": BETWEEN_TOOLS,
    "claude-haiku-5-5": DISABLED,
    "claude-opus-5-5": OMIT,           # thinking cannot be disabled at all
    "claude-opus-5": DISABLED,         # accepted only at effort high or below
    "claude-sonnet-5": DISABLED,
    "claude-opus-4-8": OMIT,           # omitted means off
    "claude-opus-4-7": OMIT,
    "claude-opus-4-6": OMIT,
    "claude-sonnet-4-6": OMIT,
    "claude-haiku-4-5": OMIT,          # never thinks when omitted
    "claude-fable-5-2": OMIT,            # always thinking; a value would be refused
    "claude-fable-5-1": OMIT,
    "claude-fable-5": OMIT,
    "claude-mythos-5-2": OMIT,
    "claude-mythos-5-1": OMIT,
    "claude-mythos-5": OMIT,
}
NEEDS_EFFORT_AT_MOST_HIGH = ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5", "claude-sonnet-5")      # "between_tools" / "disabled" are refused at xhigh and max
NO_EFFORT_PARAMETER = ("claude-haiku-4-5",)
_rejected: set = set()               # models whose thinking field was refused this session: not tried again


def _match(model: str, table) -> Optional[str]:
    m = (model or "").lower()
    if m.startswith("claude/"):
        m = m[7:]
    keys = table.keys() if isinstance(table, dict) else table
    best = None
    for k in keys:
        if (m == k or re.fullmatch(re.escape(k) + r"(-\d{8}|-latest|@.*)", m)) and (best is None or len(k) > len(best)):          # (a newer "-5-6" is NOT the same model)
            best = k
    return best


def switch_for(model: str):
    """The `thinking` value that turns thinking off for this model, or OMIT. An unknown model, or one that refused it this session, is OMIT."""
    if (model or "").lower() in _rejected:
        return OMIT
    k = _match(model, SWITCH)
    return SWITCH[k] if k else OMIT


def apply(body: dict, model: str) -> dict:
    """Set (or leave out) `thinking` on a Claude request body for this model, and keep the effort the model's off-switch needs. Returns the same body."""
    body.pop("thinking", None)
    sw = switch_for(model)
    oc = body.get("output_config")
    if _match(model, NO_EFFORT_PARAMETER) and isinstance(oc, dict):
        oc.pop("effort", None)
        if not oc:
            body.pop("output_config", None)
        return body
    if sw is OMIT or sw == OMIT:
        return body
    if isinstance(oc, dict) and _match(model, NEEDS_EFFORT_AT_MOST_HIGH):
        eff = oc.get("effort")
        if eff in _EFFORT_ORDER and _EFFORT_ORDER.index(eff) > _EFFORT_ORDER.index("high"):
            oc["effort"] = "high"
    body["thinking"] = dict(sw)
    return body


def is_thinking_refusal(status: int, text: str) -> bool:
    """A 400 that is about the thinking field (the model does not take the value we sent)."""
    return status == 400 and "thinking" in (text or "").lower()


def note_rejected(model: str) -> None:
    _rejected.add((model or "").lower())


def forget_rejected() -> None:
    _rejected.clear()


# ---- a cut-off answer ----------------------------------------------------------------------------------------------------------
MORE_ROOM_FLOOR = 1024
MORE_ROOM_CAP = 8192
OUT_OF_ROOM = "The AI ran out of room before it finished its answer."


def bigger_budget(max_tokens: int) -> int:
    return min(MORE_ROOM_CAP, max(2 * int(max_tokens), MORE_ROOM_FLOOR))


def used_whole_budget(usage: Optional[dict], max_tokens: int) -> bool:
    if not isinstance(usage, dict):
        return False
    return usage.get("stop_reason") == "max_tokens" or (isinstance(usage.get("output_tokens"), int) and max_tokens and usage["output_tokens"] >= max_tokens)


def is_cut_off(text: str, usage: Optional[dict], max_tokens: int, wants_json: bool) -> bool:
    """The answer was cut off by its token budget: it stopped for `max_tokens`, and either a JSON reply was expected (a half of one is no answer) or almost nothing came
    out for the tokens spent (the budget went on thinking)."""
    if not used_whole_budget(usage, max_tokens):
        return False
    if usage.get("stop_reason") not in ("max_tokens", None):
        return False
    out = usage.get("output_tokens") or 0
    short = len((text or "").strip()) < max(1, out // 2)
    return bool(wants_json or short)


def budget_line(request_id: str, model: str, max_tokens: int, chars: int) -> str:
    return f"[{request_id}] {model} used its whole {max_tokens}-token budget ({chars} characters of answer): the answer was cut off"
