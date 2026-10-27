"""What a request cost, and what the AI told us about how it went (2026-10-02). Nothing from MultiProxy is imported here.

Rules (director, 2026-10-02): local models and other devices are free; a subscription is "included in your subscription"; a pay-as-you-go AI's cost is recorded only when
the AI reports it (a `cost` in the reply's usage) or when it can be worked out from the tokens and the prices in that provider's own model list (Nano-GPT's does);
anything else is "not reported". Never a guess, never a price list kept by hand. What each AI reports: docs/plans/2026-10-02-routing-measurements.md, section 7.
"""
import re
import time
from typing import Any, Awaitable, Callable, Optional

NOTE_FREE = "free"
NOTE_INCLUDED = "included in your subscription"
NOTE_REPORTED = "reported by the model"
NOTE_PRICES = "worked out from the model's own prices"
NOTE_NONE = "not reported"


def _num(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def reported_cost(usage: Any) -> Optional[float]:
    """The cost in US dollars an AI put in its own reply, or None. OpenRouter-style replies carry `usage.cost`; Kilo's usage tracking calls it
    `cost_microdollars` (millionths of a dollar)."""
    if not isinstance(usage, dict):
        return None
    c = _num(usage.get("cost"))
    if c is not None and c >= 0:
        return c
    micro = _num(usage.get("cost_microdollars"))
    if micro is not None and micro >= 0:
        return micro / 1_000_000
    return None


# Perplexity's Decisions API has no price list to read, so its two prices are written here from its public documentation (docs.perplexity.ai, "Decisions API", read 2026-10-25):
# $0.02 per million input tokens for pplx-decider-v1.2, $0.01 for the flash model, output free. If Perplexity changes them this is the one place to change.
PERPLEXITY_PRICES = {
    "pplx-decider-latest": {"prompt": 0.02, "completion": 0.0}, "pplx-decider-v1.2": {"prompt": 0.02, "completion": 0.0},
    "pplx-decider-flash-latest": {"prompt": 0.01, "completion": 0.0}, "pplx-decider-flash-v1.2": {"prompt": 0.01, "completion": 0.0},
}


def perplexity_price(model: str) -> Optional[dict]:
    """The per-million-token prices of a Perplexity decision model (the `perplexity/` form of a name counts too); None for a model it does not know."""
    name = str(model or "").lower()
    return PERPLEXITY_PRICES.get(name[len("perplexity/"):] if name.startswith("perplexity/") else name)


def cost_from_prices(tokens_in: Optional[int], tokens_out: Optional[int], price: Optional[dict]) -> Optional[float]:
    """Dollars from token counts and a per-million-token price pair {"prompt": x, "completion": y}; None unless both counts and both prices are known."""
    if not price or tokens_in is None or tokens_out is None:
        return None
    p, c = _num(price.get("prompt")), _num(price.get("completion"))
    if p is None or c is None:
        return None
    return (tokens_in * p + tokens_out * c) / 1_000_000


def work_out(cls: str, usage: Any, tokens_in: Optional[int], tokens_out: Optional[int], price: Optional[dict] = None) -> tuple:
    """(dollars or None, note) for one finished request. `cls` is 'free', 'included' or 'paid' (routing.CLASS_*)."""
    if cls == "free":
        return 0.0, NOTE_FREE
    if cls == "included":
        return None, NOTE_INCLUDED
    c = reported_cost(usage)
    if c is not None:
        return c, NOTE_REPORTED
    c = cost_from_prices(tokens_in, tokens_out, price)
    if c is not None:
        return c, NOTE_PRICES
    return None, NOTE_NONE


def format_cost(cost: Optional[float], note: Optional[str]) -> str:
    """For the log line: '$0.0012', 'free', 'included in your subscription' or 'cost not reported'."""
    if note == NOTE_FREE:
        return "free"
    if note == NOTE_INCLUDED:
        return NOTE_INCLUDED
    if cost is None:
        return "cost not reported"
    return f"${cost:.4f}" if cost < 0.01 else f"${cost:.3f}"


# ---- Nano-GPT's own prices -------------------------------------------------------------------------------------------------------------------------------

PRICE_TTL_S = 6 * 3600.0


def parse_nano_prices(payload: Any) -> dict:
    """GET /api/v1/models?detailed=true -> {model id lower-case: {"prompt": USD per million, "completion": USD per million}}. Entries without both prices are left out."""
    out: dict = {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    for m in rows if isinstance(rows, list) else []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        pr = m.get("pricing")
        if not isinstance(pr, dict):
            continue
        p, c = _num(pr.get("prompt")), _num(pr.get("completion"))
        unit = str(pr.get("unit") or "per_million_tokens")
        if p is None or c is None or "million" not in unit:
            continue
        out[str(m["id"]).lower()] = {"prompt": p, "completion": c}
    return out


class PriceList:
    """A provider's prices, fetched from its own model list at most every six hours (and never when there is no key). `fetch()` returns the parsed prices."""

    def __init__(self, fetch: Callable[[], Awaitable[dict]], clock: Callable[[], float] = time.time):
        self.fetch, self.clock = fetch, clock
        self._prices: dict = {}
        self._at: Optional[float] = None
        self._tried: Optional[float] = None

    async def lookup(self, model: str) -> Optional[dict]:
        now = self.clock()
        if (self._at is None or now - self._at > PRICE_TTL_S) and (self._tried is None or now - self._tried > 300):
            self._tried = now
            try:
                got = await self.fetch()
                if got:
                    self._prices, self._at = got, now
            except Exception:
                pass                                   # no prices is "not reported", never an error
        return self._prices.get((model or "").lower())


# ---- what a local llama.cpp says about a reply -------------------------------------------------------------------------------------------------------------------

def timings_from(obj: Any) -> Optional[dict]:
    """llama.cpp's `timings` object (in the reply, or on a stream's last chunk) -> {prompt_n, cache_n (the part of the prompt it already remembered), prompt_ms,
    predicted_n, predicted_ms, words_per_second}, or None. Tokens per second is reported as it is; 'words' is the same count the Speeds tab uses."""
    t = obj.get("timings") if isinstance(obj, dict) else None
    if not isinstance(t, dict):
        return None
    out = {}
    for k in ("prompt_n", "cache_n", "prompt_ms", "predicted_n", "predicted_ms"):
        v = _num(t.get(k))
        if v is not None:
            out[k] = v
    ps = _num(t.get("predicted_per_second"))
    if ps is None and out.get("predicted_n") and out.get("predicted_ms"):
        ps = out["predicted_n"] / (out["predicted_ms"] / 1000.0)
    if ps is not None:
        out["tokens_per_second"] = round(ps, 2)
    return out or None
