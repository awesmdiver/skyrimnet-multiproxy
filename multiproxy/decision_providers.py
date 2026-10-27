"""Providers that answer a quick decision with a decision model of their own (2026-10-05).

A provider whose own model list says a model is for Decisions (`model_kinds.DECISIONS`) can answer SkyrimNet's quick decisions: Ollama's `clef`, Nano-GPT's seven
decision models, and any other that reports them. **To add a provider, register it once** (`register(DecisionProvider(...))`, in multiproxy.py next to the others):
its name, its model list and its call. The Quick decisions card, the order of answerers, the Quick Test and the saved model choice all read this registry, so none of
them is edited.

This file imports nothing from MultiProxy (tests use it on its own). The calls themselves live in multiproxy.py (they need its sessions and keys).
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import model_kinds


@dataclass
class DecisionProvider:
    id: str                                     # the step name in the order of answerers, and the key of the saved model choice ("ollama", "nano")
    name: str                                   # the real provider as Speeds and the log say it ("Ollama", "Nano-GPT")
    choice: str                                 # the "Answer decisions with" value that puts it first ("ollama", "nanogpt")
    label: str                                  # its entry in that list ("My Ollama decision model")
    read: Callable[[], Awaitable[list]]         # async: the decision models it reports right now (its own list, filtered with `decision_models`); [] when off or not running
    ask: Optional[Callable[..., Awaitable]] = None      # async (model, state, questions, request_id, via, limit) -> (status, body bytes, content type); None: it has its own step
    default: Optional[Callable[[list], str]] = None      # which model is used when the player chose none (the first when this is None)
    local: bool = False                         # runs on this device (the player's local waiting limit applies, not the paid one)
    off_reason: str = ""                        # the plain line for the Quick Test when it is off
    key_ok: Callable[[], bool] = lambda: True   # a saved key, for the ones that need one
    static: Optional[Callable[[], list]] = None  # a provider with a fixed model list (Perplexity has no list call): its models right now, with no reading and no waiting
    model_labels: Optional[dict] = None         # plain names for its models on the card ({"pplx-decider-flash-latest": "Cheaper and faster"}); a model without one shows its own name
    miss_line: Optional[Callable[[int], str]] = None     # the plain first half of the card's line when it could not answer (status -> "Perplexity didn't accept the key.")

    def models(self) -> list:
        """What was last read (never waits): the models it reports as Decisions, first one the default."""
        if self.static is not None:
            return list(self.static())
        return list(CACHE.get(self.id, (0.0, []))[1])

    def ready(self) -> bool:
        return bool(self.key_ok()) and bool(self.models())


REGISTRY: dict = {}
CACHE: dict = {}                    # provider id -> (when it was read, models)
KEEP_SECONDS = 30.0                 # a read is used this long; a decision never waits for a re-read
FIRST_READ_LIMIT = 2.0              # the very first read of a provider may hold a decision this long, once
_busy: set = set()


def register(p: DecisionProvider) -> DecisionProvider:
    REGISTRY[p.id] = p
    return p


async def refresh(force: bool = False, wait: bool = True) -> None:
    """Read every provider's decision models when they were never read (waited for, at most FIRST_READ_LIMIT seconds, once), or are older than KEEP_SECONDS (read in the
    background unless `wait`, which the dashboard and the tests use). A failed read leaves an empty list. Never raises."""
    now = time.time()
    for p in list(REGISTRY.values()):
        at = CACHE.get(p.id, (0.0, None))[0]
        if p.id in _busy or (not force and at and now - at < KEEP_SECONDS):
            continue
        if wait or not at:
            _busy.add(p.id)
            try:
                CACHE[p.id] = (time.time(), list(await asyncio.wait_for(p.read(), timeout=FIRST_READ_LIMIT if not wait else 8.0)))
            except Exception:
                CACHE[p.id] = (time.time(), [])
            finally:
                _busy.discard(p.id)
        else:
            _busy.add(p.id)

            async def go(p=p):
                try:
                    CACHE[p.id] = (time.time(), list(await asyncio.wait_for(p.read(), timeout=8.0)))
                except Exception:
                    CACHE[p.id] = (time.time(), [])
                finally:
                    _busy.discard(p.id)
            try:
                asyncio.get_running_loop().create_task(go())
            except RuntimeError:
                _busy.discard(p.id)


def by_choice(choice: str) -> Optional[DecisionProvider]:
    return next((p for p in REGISTRY.values() if p.choice == choice), None)


def choices() -> tuple:
    """The "Answer decisions with" values the registered providers add."""
    return tuple(p.choice for p in REGISTRY.values())


def step_of(choice: str) -> Optional[str]:
    p = by_choice(choice)
    return p.id if p else None


def decision_models(ids_and_kinds) -> list:
    """From [(model id, its kinds or None)]: the models the provider itself says are for Decisions, in the order given. An unknown kind is NOT included (a decision model is
    never guessed: the list has to say so)."""
    return [mid for mid, kinds in ids_and_kinds if kinds and model_kinds.DECISIONS in kinds]


def chosen_model(p: DecisionProvider, saved: dict) -> str:
    """The model to use: the player's saved choice when it is still one the provider reports, else the provider's default, else the first. "" when it has none."""
    models = p.models()
    pick = str((saved or {}).get(p.id) or "")
    if pick and pick in models:
        return pick
    if p.default is not None:
        d = p.default(models)
        if d:
            return d
    return models[0] if models else ""


def version_tuple(text) -> tuple:
    """'0.35.1' -> (0, 35, 1); anything unreadable -> (). Pre-release tags ('0.40.0-rc0') count as the plain number."""
    out = []
    for part in str(text or "").split("-")[0].split("."):
        if not part.isdigit():
            return tuple(out)
        out.append(int(part))
    return tuple(out)


# Decision models known NOT to answer on an Ollama version: {model name stem: the newest Ollama version it still fails on}. `clef-flash` answers every request with
# `Clef: non-finite logit` on Ollama 0.35.1 (docs/plans/2026-10-03-ollama-clef.md, re-tested 2026-10-05); it is left off the card until a newer Ollama is installed.
OLLAMA_BROKEN = {"clef-flash": (0, 35, 1)}


def ollama_model_works(name: str, version: str) -> bool:
    stem = str(name).split(":")[0].lower()
    newest_bad = OLLAMA_BROKEN.get(stem)
    if newest_bad is None:
        return True
    v = version_tuple(version)
    return bool(v) and v > newest_bad
