"""Quick Test for Quick decisions (Jev) (2026-10-05): the canned scenarios, the typed questions they become, and how an answer is read back in plain words.

The scenarios are the director's own text (state sentences and plain questions, word for word). Each plain question becomes the real typed question System One
expects: a `choice` with its named options as `criteria`, a `score` with its levels as a list (the local model takes 2 to 10 levels, so the 1 to 100 scale is sent as
ten bands of ten), and a `noul` for yes/no. Nothing here calls anything: the route in multiproxy.py sends them through the same functions SkyrimNet's request uses.
"""
from typing import Optional

# (id, name, state, [(question name, plain question, kind, options or (low, high))])
SCENARIOS = [
    ("combat", "Combat Survival Decision",
     "The NPC, a Whiterun Guard, currently has 15% health remaining. The player, a level 45 Dragonborn, has just used the Unrelenting Force shout and drawn a glowing Daedric "
     "Greatsword. The guard has no backup nearby.",
     [("action_choice", "Based on the state, does the guard 'attack', 'flee', or 'surrender'?", "choice", ["attack", "flee", "surrender"]),
      ("fear_score", "Rate the guard's fear level from 1 to 10.", "score", (1, 10))]),
    ("stealth", "Stealth and Detection Routing",
     "A bandit is standing watch inside Embershard Mine. An arrow just struck the wooden wall two feet to their left. The room is dimly lit, and the bandit cannot see the player.",
     [("reaction_choice", "Should the bandit 'ignore' the noise, 'search_area', or 'warn_others'?", "choice", ["ignore", "search_area", "warn_others"]),
      ("alertness_score", "Rate the bandit's current alertness level from 1 to 100.", "score", (1, 100))]),
    ("dialogue", "Dynamic Dialogue and Bounty Check",
     "The player approaches a Solitude guard to ask about local rumors. The player has a 3,500 gold bounty in Haafingar hold, has a weapon sheathed, and is wearing Thieves Guild armor.",
     [("dialogue_route", "Does the guard respond with 'give_rumor', 'attempt_arrest', or 'extort_bribe'?", "choice", ["give_rumor", "attempt_arrest", "extort_bribe"]),
      ("suspicion_score", "Calculate a suspicion score for the guard from 1 to 10.", "score", (1, 10))]),
    ("merchant", "Stolen Goods Merchant Check",
     "The player is attempting to sell a 'Stolen Diamond Ring' to Belethor at Belethor's General Goods in Whiterun. The player's Speech skill is 25, they do not possess the "
     "'Fence' perk, and Belethor is not affiliated with the Thieves Guild.",
     [("will_buy_stolen_goods", "Answer strictly 'yes' or 'no': Will the merchant purchase this item from the player?", "noul", None)]),
]

MAX_LEVELS = 10


def levels(low: int, high: int) -> list:
    """The levels of a score question: the numbers themselves up to ten of them, else ten equal bands ("1-10", "11-20", ...)."""
    n = high - low + 1
    if n <= MAX_LEVELS:
        return [str(i) for i in range(low, high + 1)]
    step = n // MAX_LEVELS
    return [f"{low + i * step}-{low + (i + 1) * step - 1}" for i in range(MAX_LEVELS)]


def find(scenario_id: str) -> Optional[tuple]:
    return next((s for s in SCENARIOS if s[0] == scenario_id), None)


def typed_question(name: str, text: str, kind: str, extra) -> dict:
    if kind == "choice":
        return {"type": "choice", "instructions": text, "criteria": {o: None for o in extra}}
    if kind == "score":
        return {"type": "score", "instructions": text, "criteria": levels(*extra)}
    return {"type": "noul", "instructions": text}


def request(scenario_id: str) -> Optional[tuple]:
    """(state, questions) for the real path, or None for an unknown scenario."""
    s = find(scenario_id)
    if s is None:
        return None
    return s[2], {q[0]: typed_question(*q) for q in s[3]}


def listing() -> list:
    """What the page needs to show a scenario before it is sent."""
    return [{"id": s[0], "name": s[1], "state": s[2], "questions": [{"name": q[0], "text": q[1]} for q in s[3]]} for s in SCENARIOS]


def read_answers(questions: dict, answers: dict) -> list:
    """The answers the way a person reads them: [{name, text, confidence}]. A choice is its option, a score is its level, yes/no is Yes or No."""
    out = []
    for name, q in questions.items():
        a = (answers or {}).get(name)
        if not isinstance(a, dict):
            out.append({"name": name, "text": "No answer", "confidence": None})
            continue
        kind = q.get("type")
        conf = a.get("confidence")
        if kind == "choice":
            text = str(a.get("choice"))
            conf = conf if conf is not None else (a.get("probabilities") or {}).get(a.get("choice"))
        elif kind == "score":
            lv = q.get("criteria") or []
            try:
                i = min(max(int(round(float(a.get("score")))), 0), len(lv) - 1)
                text = str(lv[i])
            except (TypeError, ValueError, IndexError):
                text = str(a.get("score"))
        else:
            try:
                p = float(a.get("noul"))
            except (TypeError, ValueError):
                p = None
            text = "No answer" if p is None else ("Yes" if p >= 0.5 else "No")
            conf = None if p is None else (p if p >= 0.5 else 1 - p)
        out.append({"name": name, "text": text, "confidence": None if conf is None else round(float(conf), 2)})
    return out


def lowest_confidence(questions: dict, answers: dict) -> Optional[float]:
    """How sure the answerer was about its least sure choice or yes/no answer (0 to 1). Scores are left out on purpose: the local model reports no confidence on
    them, so they would always look unsure. None when there is no choice or yes/no answer to judge."""
    low = None
    for name, q in (questions or {}).items():
        a = (answers or {}).get(name)
        if not isinstance(a, dict):
            continue
        kind = q.get("type") if isinstance(q, dict) else a.get("type")
        try:
            if kind == "choice":
                c = float(a["confidence"])
            elif kind == "noul":
                p = float(a["noul"])
                c = max(p, 1 - p)
            else:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        low = c if low is None else min(low, c)
    return low


STEP_NAMES = {"local": "the local model", "typesafe": "TypeSafe", "nano": "Nano-GPT", "perplexity": "Perplexity", "ollama": "Ollama", "standin": "another AI standing in"}


def next_step(order: list, tried: str) -> str:
    """Which step would answer next in the real order after `tried` ("" when nothing is left)."""
    if tried in order:
        rest = order[order.index(tried) + 1:]
    else:
        rest = order
    return STEP_NAMES[rest[0]] if rest else ""
