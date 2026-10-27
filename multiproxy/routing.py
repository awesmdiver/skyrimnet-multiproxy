"""Routing (step 1, 2026-10-02): which job a request is, which AIs may answer it, in which order, and what happens when one doesn't.

Nothing from MultiProxy is imported here (so it is tested on its own); multiproxy.py hands in what it knows (how to name an AI, what class it is, the clock).

A player who never sets a route sees no change: a request with no route for its job is answered exactly as before, by the model name it asked for.

Parts:
  1. The job of a request: the author's hint (one named place, empty until SkyrimNet defines it), an explicit `job/<name>` model, or our own recognition from the
     request's structure plus SkyrimNet's own per-job settings (max tokens, temperature), with the first words of the prompt only to split a tie. Measured in
     docs/plans/2026-10-02-routing-measurements.md.
  2. Routes: per job an ordered list of AIs and a way of choosing (In this order, Take turns, Fastest right now, Free ones first, Cheapest first).
  3. Hand-over: a failure, "busy" or an empty reply passes the line to the next AI; an AI that keeps failing rests for a while (never all of them: the last
     one standing is always tried). No hand-over for slowness unless the player turns it on.
"""
import asyncio
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import fine_tune
import job_names
import tested_settings
from routing_openings import OPENINGS

# ---- jobs -------------------------------------------------------------------------------------------------------------------------------------------------

OTHER = "other"
# The jobs MultiProxy knows by key, in the order they are listed (the `variants` keys of SkyrimNet's settings file; "dialogue" is the provider's own model name).
# Their NAMES and DESCRIPTIONS are not here: they come from SkyrimNet itself (job_names.py), so a player sees what SkyrimNet shows. A job from another mod, or one
# SkyrimNet adds later, is found from SkyrimNet's files and its settings and needs nothing added here.
JOB_ORDER = (
    "dialogue", "AgentDefault", "CharacterProfileGeneration", "DiaryGeneration", "UniversalTranslator", "action_evaluation", "agencyengine_impulse",
    "agencyengine_resolve", "combat", "decisions", "gamemaster_evaluation", "intel_story_dm", "memory", "meta", "narrative_engine_composer",
    "narrative_engine_director", "sever_background", "snrom_background", "vision",
)
JOB_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")             # what a job key may look like (any job a mod adds)
# What MultiProxy called some jobs before it read their names from SkyrimNet (the labels it made up, and the spaced-out title it made from a key). Rows stored under
# these in the Speeds history are shown under the job's current name; nothing stored is ever rewritten.
LEGACY_LABELS = {
    "Companion Impulses": "agencyengine_impulse", "Event Sorting": "agencyengine_resolve", "Social and Story Events": "intel_story_dm", "Narrative Composer": "narrative_engine_composer",
    "Narrative Director": "narrative_engine_director", "Companion Relationships": "sever_background", "Relationship Changes": "snrom_background",
    **{" ".join(re.sub(r"(?<!^)(?=[A-Z])", " ", k.replace("_", " ")).split()).title(): k for k in JOB_ORDER if "_" in k and k not in ("action_evaluation", "gamemaster_evaluation")},
}
STREAMED_JOBS = ("dialogue", "combat")             # the two jobs SkyrimNet streams
IMAGE_JOBS = ("vision",)
DIALOGUE_GROUP = ("dialogue", "combat")
HOW_HINT, HOW_NAME, HOW_RECOGNISED = "hint", "name", "recognised"

# The author's routing hint (agreed with SkyrimNet's author, names not defined yet): when he defines the header, put its name here and `job_from_hint` answers.
HINT_HEADERS: tuple = ()

EXPLICIT_PREFIX = "job/"


def label(key: str) -> str:
    """The name SkyrimNet shows for this job (a job another mod adds: its raw name, as SkyrimNet shows it)."""
    return "Other" if key == OTHER else job_names.NAMES.name(key)


def about(key: str) -> str:
    return "" if key == OTHER else job_names.NAMES.info(key)["about"]


def order_of(key: str) -> int:
    return JOB_ORDER.index(key) if key in JOB_ORDER else len(JOB_ORDER)


def current_role_label(lbl: str) -> str:
    """A label stored in the Speeds history before names came from SkyrimNet, shown under the job's current name."""
    key = LEGACY_LABELS.get(lbl)
    return label(key) if key else lbl


def label_of(keys) -> str:
    """'Dialogue', or 'Dialogue and Combat' when several jobs can't be told apart (each by the name SkyrimNet shows)."""
    names = [label(k) for k in keys]
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def job_from_hint(headers) -> Optional[str]:
    """The job the author's hint names, or None (always None until HINT_HEADERS is filled in)."""
    if not HINT_HEADERS:
        return None
    try:
        lowered = {str(k).lower(): v for k, v in headers.items()}
    except Exception:
        return None
    for name in HINT_HEADERS:
        value = str(lowered.get(name.lower()) or "").strip()
        if JOB_KEY.match(value):
            return value
    return None


def job_from_name(model: str, known=()) -> Optional[str]:
    """`job/memory` (a job's key or the name SkyrimNet shows, any case) -> 'memory'; anything else None. `known` adds the job keys SkyrimNet's settings have."""
    m = (model or "").strip()
    if not m.lower().startswith(EXPLICIT_PREFIX):
        return None
    name = m[len(EXPLICIT_PREFIX):].strip().lower().replace(" ", "_")
    for key in list(JOB_ORDER) + list(job_names.NAMES.known_keys()) + list(known):
        if name in (key.lower(), label(key).lower().replace(" ", "_")):
            return key
    return None


# ---- what SkyrimNet's own settings say about each job ---------------------------------------------------------------------------------------------------------

def settings_table(doc: Optional[dict]) -> dict:
    """{job key: {"max_tokens", "temperature", "timeout", "models"}} from the provider block of SkyrimNet's settings file: variants' `default_params` over the
    provider's own. Only these numbers are ever read from it (never a key)."""
    out: dict = {}
    if not isinstance(doc, dict):
        return out
    base = doc.get("default_params") if isinstance(doc.get("default_params"), dict) else {}

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    names = _names(doc.get("model_name"))
    out["dialogue"] = {"max_tokens": num(base.get("max_tokens")), "temperature": num(base.get("temperature")), "timeout": num(doc.get("timeout")), "models": names,
                       "providers": _provider_ids(doc.get("model_params"), names)}
    variants = doc.get("variants") if isinstance(doc.get("variants"), dict) else {}
    for key, v in variants.items():
        if not isinstance(v, dict):
            continue
        dp = {**base, **(v.get("default_params") if isinstance(v.get("default_params"), dict) else {})}
        names = _names(v.get("model_name"))
        out[str(key)] = {"max_tokens": num(dp.get("max_tokens")), "temperature": num(dp.get("temperature")), "timeout": num(v.get("timeout")), "models": names,
                         "providers": _provider_ids(v.get("model_params"), names)}
    return out


def _provider_ids(params, names: list) -> list:
    """The provider id SkyrimNet gives each model of a job (`model_params[].provider_id`, in the same order as the model names; one id covers them all). None where it is not said.
    Only the id is read, never anything else from that list (no key)."""
    ids = [(p.get("provider_id") if isinstance(p, dict) else None) for p in params] if isinstance(params, list) else []
    ids = [str(i).strip() if isinstance(i, str) and i.strip() else None for i in ids]
    if len(ids) == len(names):
        return ids
    if len(ids) == 1:
        return ids * len(names)
    return [None] * len(names)


# Providers SkyrimNet can send a job to straight from itself that MultiProxy does not carry. Named only when the provider id says so (the ids are the player's own names for
# SkyrimNet's providers, so anything else is left alone rather than guessed). OpenRouter was removed from MultiProxy on SkyrimNet's author's request (2026-09-30).
ELSEWHERE_PROVIDERS = {"openrouter": "OpenRouter"}


def elsewhere_name(provider_id) -> Optional[str]:
    """"OpenRouter" when this provider id is one MultiProxy does not carry, else None."""
    low = str(provider_id or "").lower()
    return next((name for key, name in ELSEWHERE_PROVIDERS.items() if key in low), None)


def _names(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(",") if s.strip()]


# ---- recognising the job ---------------------------------------------------------------------------------------------------------------------------------

_PERSONA = re.compile(r"^You are [^,\n]{1,40}, an? (?:Fe)?[Mm]ale [A-Za-z ]{2,20} in Skyrim")
_ACTION = re.compile(r"^You select the single most appropriate action for \S+")
_JUDGE = re.compile(r"^You judge how much a conversation actually changed how \S+")


OPENING_CHARS = 70                 # only this much of a prompt is ever looked at (and none of it kept)


def opening_key(text: str, words: int = 7) -> str:
    """The first words of a prompt, with an NPC's name taken out (SkyrimNet writes the same prompt for every NPC)."""
    s = " ".join((text or "")[:OPENING_CHARS * 2].split())[:OPENING_CHARS]
    if _PERSONA.match(s):
        return "PERSONA"
    if _ACTION.match(s):
        return "You select the single most appropriate action"
    if _JUDGE.match(s):
        return "You judge how much a conversation actually changed how"
    return " ".join(s.split()[:words])


@dataclass
class Features:
    """What a request shows of itself (nothing is kept: the prompt's text is cut to its opening)."""
    stream: bool = False
    images: int = 0
    questions: bool = False
    messages: int = 0
    max_tokens: Optional[float] = None
    temperature: Optional[float] = None
    opening: str = ""                  # opening_key of the system prompt, or of the first user message when there is no system prompt


def make_features(*, stream: bool, images: int, questions: bool, messages: int, max_tokens, temperature, system_text: str = "", first_user_text: str = "") -> Features:
    """Everything recognition looks at, from the parts of a request (the prompt's text is reduced to its opening here and nowhere kept)."""
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return Features(stream=bool(stream), images=int(images), questions=bool(questions), messages=int(messages), max_tokens=num(max_tokens), temperature=num(temperature),
                    opening=opening_key(system_text) if (system_text or "").strip() else opening_key(first_user_text, 4))


@dataclass
class Recognition:
    keys: tuple                        # one job, or several that can't be told apart; (OTHER,) when unknown
    how: str                           # hint | name | recognised
    certain: bool = True               # False when it is a guess (shown as "Looks like ...")

    @property
    def key(self) -> str:
        return self.keys[0]

    @property
    def label(self) -> str:
        return label_of(self.keys)

    @property
    def known(self) -> bool:
        return self.keys != (OTHER,)


def _same(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


def recognise(f: Features, table: dict, openings: Optional[dict] = None) -> Recognition:
    """Structure first, SkyrimNet's own numbers next, the prompt's opening only to split a tie. A job never seen (or a request that fits nothing) is OTHER."""
    openings = OPENINGS if openings is None else openings
    if f.questions:
        return Recognition(("decisions",), HOW_RECOGNISED, True)
    known = [k for k in (table or {}) if k != "decisions"]
    pool = set(known) | {"dialogue"}
    if not table:                                          # SkyrimNet not linked: shape is all there is
        if f.images:
            return Recognition(("vision",), HOW_RECOGNISED, True)
        if f.stream and f.messages > 10:
            return Recognition(DIALOGUE_GROUP, HOW_RECOGNISED, False)
        return Recognition((OTHER,), HOW_RECOGNISED, False)
    shaped = {j for j in pool if (j in IMAGE_JOBS) == (f.images > 0) and (j in STREAMED_JOBS) == f.stream}
    if f.images:
        shaped = {j for j in pool if j in IMAGE_JOBS} or {"vision"}
    by_numbers = {j for j in shaped if (table.get(j, {}).get("max_tokens") is None or f.max_tokens is None or _same(table[j]["max_tokens"], f.max_tokens))
                  and (table.get(j, {}).get("temperature") is None or f.temperature is None or _same(table[j]["temperature"], f.temperature))}
    cands = by_numbers
    certain = True
    if len(cands) != 1:
        certain = False
        said = openings.get(f.opening)
        if said:
            narrowed = {j for j in (cands or shaped) if j in said}
            if narrowed:
                cands = narrowed
        if not cands:                                      # SkyrimNet was set differently from what it sent: let the shape and the opening speak
            cands = {j for j in shaped if j in (openings.get(f.opening) or ())}
    if not cands:
        return Recognition((OTHER,), HOW_RECOGNISED, False)
    keys = tuple(sorted(cands, key=order_of))
    return Recognition(keys, HOW_RECOGNISED, certain and len(keys) == 1)


def indistinguishable_groups(table: dict) -> list:
    """Jobs whose shape and numbers are identical, so the prompt's opening is all that tells them apart (for the status: 'Dialogue and Combat')."""
    groups: dict = defaultdict(list)
    for k, v in (table or {}).items():
        if k == "decisions":
            continue
        groups[((k in IMAGE_JOBS), (k in STREAMED_JOBS), v.get("max_tokens"), v.get("temperature"))].append(k)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def exactly_named(table: dict, seen: Optional[dict] = None, also=()) -> set:
    """The jobs SkyrimNet names exactly (2026-10-24): its settings give the job the model `job/<key>`, or MultiProxy has seen it asked for by that name (or by the hint
    header; `seen` is {job key: how it was last known}). Such a job is told apart by its name, so it leaves any group; `also` adds the ones seen in this run."""
    named = {k for k in also if isinstance(k, str)}
    for k, v in (table or {}).items():
        if k != "decisions" and any(job_from_name(m, (k,)) == k for m in (v.get("models") or []) if isinstance(m, str)):
            named.add(k)
    for k, how in (seen or {}).items():
        if isinstance(k, str) and "/" not in k and k != "decisions" and how in (HOW_NAME, HOW_HINT):
            named.add(k)
    return named


def without_named(table: dict, named=()) -> dict:
    """The job table with the exactly-named jobs taken out: what recognising a request by its shape, and grouping the rest, should look at."""
    return {k: v for k, v in (table or {}).items() if k not in named} if named else table


def hard_groups(table: dict, openings: Optional[dict] = None, named=()) -> list:
    """Jobs MultiProxy can't tell apart even with the prompt's opening: same shape and numbers, and openings that overlap (or one we have never seen), so they
    have to move together. Fewer than `indistinguishable_groups`, which only looks at shape and numbers. A job in `named` (see `exactly_named`) is never in one."""
    openings = OPENINGS if openings is None else openings
    table = without_named(table, named)
    seen: dict = defaultdict(set)
    for key, jobs in openings.items():
        for j in jobs:
            seen[j].add(key)
    out = []
    for group in indistinguishable_groups(table):
        parent = {j: j for j in group}

        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if not seen[a] or not seen[b] or (seen[a] & seen[b]):
                    parent[find(a)] = find(b)
        comps: dict = defaultdict(list)
        for j in group:
            comps[find(j)].append(j)
        out += [sorted(c, key=order_of) for c in comps.values() if len(c) > 1]
    return out


# ---- routes (settings) -----------------------------------------------------------------------------------------------------------------------------------

HOWS = ("order", "turns", "fastest", "free", "cheapest")
HOW_LABELS = {"order": "In this order", "turns": "Take turns", "fastest": "Fastest right now", "free": "Free ones first", "cheapest": "Cheapest first"}
DEFAULT_REST = {"failures": 3, "within_s": 60.0, "for_s": 120.0}
DEFAULT_SLOW = {"enabled": False, "streamed_dialogue": False}
SLOW_MIN_S, SLOW_MAX_S, SLOW_FACTOR, SLOW_NEEDS = 5.0, 10.0, 2.5, 8
# What goes first when an AI is at its limit (spec part 3): one ordered list of jobs, saved in settings. The default puts the jobs a player notices in the game ahead
# of the background ones. A waiting request moves up one place in the order for every AGING_S seconds it has waited, so nothing waits forever.
DEFAULT_PRIORITY = ("dialogue", "combat", "decisions", "vision")
DEFAULT_AGING_S = 5.0
AGING_RANGE_S = (1.0, 120.0)
# What dragging a job from one model onto another does with the old model (the Settings page's "When I move a job to another model"): ask each time, keep it as a backup, or just move.
ON_MOVE = ("ask", "keep", "move")
DEFAULT_ON_MOVE = "ask"
EXPLICIT_MIN_S = 2.5               # a limit the player sets for a job is never shorter than SkyrimNet's own floor
MAX_AIS = 12


def _clean_route(r: Any) -> Optional[dict]:
    if not isinstance(r, dict):
        return None
    ais = [str(a).strip() for a in (r.get("ais") or []) if str(a).strip()][:MAX_AIS]
    how = r.get("how") if r.get("how") in HOWS else "order"
    slow = r.get("slow_limit_s")
    slow = float(slow) if isinstance(slow, (int, float)) and not isinstance(slow, bool) and slow > 0 else None
    if not ais:
        return None
    return {"ais": ais, "how": how, "slow_limit_s": slow}


def clean_settings(raw: Any) -> dict:
    """Whatever is in config.json under `routing`, made safe: only known jobs, known ways, numbers in range. An empty answer means "no routing"."""
    raw = raw if isinstance(raw, dict) else {}
    routes = {}
    for key, r in (raw.get("routes") or {}).items() if isinstance(raw.get("routes"), dict) else []:
        if isinstance(key, str) and JOB_KEY.match(key):
            c = _clean_route(r)
            if c:
                routes[key] = c
    rest = dict(DEFAULT_REST)
    if isinstance(raw.get("rest"), dict):
        for k, lo, hi in (("failures", 1, 20), ("within_s", 5, 3600), ("for_s", 10, 3600)):
            v = raw["rest"].get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rest[k] = min(hi, max(lo, v))
        rest["failures"] = int(rest["failures"])
    slow = dict(DEFAULT_SLOW)
    if isinstance(raw.get("slow_handover"), dict):
        slow["enabled"] = bool(raw["slow_handover"].get("enabled"))
        slow["streamed_dialogue"] = bool(raw["slow_handover"].get("streamed_dialogue"))
    aging = DEFAULT_AGING_S
    v = raw.get("aging_s")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        aging = min(AGING_RANGE_S[1], max(AGING_RANGE_S[0], float(v)))
    priority = []
    for k in raw.get("priority") if isinstance(raw.get("priority"), list) else []:
        if isinstance(k, str) and JOB_KEY.match(k) and k not in priority:
            priority.append(k)
    off = [x.strip() for x in (raw.get("keep_loaded_off") if isinstance(raw.get("keep_loaded_off"), list) else []) if isinstance(x, str) and x.strip()]
    on_move = raw.get("on_move") if raw.get("on_move") in ON_MOVE else DEFAULT_ON_MOVE
    split = [k for k in (raw.get("split_copied") if isinstance(raw.get("split_copied"), list) else []) if isinstance(k, str) and JOB_KEY.match(k)]
    return {"split_copied": list(dict.fromkeys(split))[:128], "routes": routes, "rest": rest, "slow_handover": slow, "priority": priority[:128], "aging_s": aging, "overrides": fine_tune.clean_overrides(raw.get("overrides")), "tested": tested_settings.clean_switches(raw.get("tested")),
            "keep_loaded_off": list(dict.fromkeys(off))[:64], "on_move": on_move}


def priority_order(settings: dict, shown=()) -> list:
    """The one ordered list of jobs for what goes first: the player's saved order, else the default (Dialogue, Combat, Decisions, Vision, then the background jobs);
    any job in `shown` that isn't in it yet (a job a mod added, say) goes at the end."""
    saved = list(settings.get("priority") or [])
    order = saved if saved else list(DEFAULT_PRIORITY) + [k for k in JOB_ORDER if k not in DEFAULT_PRIORITY]
    order += [k for k in shown if k not in order]
    return order


def priority_rank(settings: dict, job_key: Optional[str]) -> int:
    """Where a request's job stands in the order, 0 = first. Jobs that can't be told apart (a/b) rank as the best of them; a job not in the list, or one we didn't
    recognise, ranks after every listed job (all equal: they keep first come first served among themselves)."""
    order = priority_order(settings)
    ranks = [order.index(k) for k in str(job_key or "").split("/") if k in order]
    return min(ranks) if ranks else len(order)


QUICK_TEST_HEADER = "X-MultiProxy-Quick-Test"     # the dashboard's Quick Test sends this: it always goes to the model it names, never through a route


def is_quick_test(headers) -> bool:
    try:
        return any(str(k).lower() == QUICK_TEST_HEADER.lower() for k in headers.keys())
    except Exception:
        return False


def tied_to_one_job(found: "Recognition", table: dict, openings: Optional[dict] = None, named=()) -> bool:
    """True when a request can safely be routed: exactly one job, or several the player can only move together (a hard group, which share one route by design).
    False for a guess across several jobs (or all of them): that request goes to the model it asked for."""
    if not found.known:
        return False
    if len(found.keys) == 1:
        return True
    keys = set(found.keys)
    return any(keys <= set(g) for g in hard_groups(table, openings, named))
def named_without_route(settings: dict, name: str, table: dict) -> tuple:
    """(route or None, where it came from: "group" | "model" | None) for a job asked for as `job/<name>` that has no route of its own. It is answered the way it was before the
    player named it: by the route of the jobs it could not be told apart from, else by the model names SkyrimNet sends for it (never a `job/` name, which is no model)."""
    group = next((g for g in hard_groups(table) if name in g), [name])
    route, _ = route_for(settings, group)
    if route:
        return route, "group"
    models = [m for m in (table.get(name) or {}).get("models", []) if m and not str(m).lower().startswith(EXPLICIT_PREFIX)]
    if models:
        return _clean_route({"ais": models}), "model"
    return None, None


def route_for(settings: dict, keys) -> tuple:
    """(route or None, job key it came from). Jobs that can't be told apart share one route: the first of them that has one is used."""
    for k in keys:
        r = settings.get("routes", {}).get(k)
        if r:
            return r, k
    return None, None


# ---- what we remember about each AI ------------------------------------------------------------------------------------------------------------------------

RECENT_S = 1800.0                  # first-word times older than half an hour are not used for "fastest right now"
EWMA_ALPHA = 0.35                  # the newest of the (up to 20) recent times counts 35%, the one before 23%, and so on
IN_FLIGHT_PENALTY_S = 0.75         # every request an AI is working on right now counts as this many seconds slower
FIRST_TRIES = 3                    # an AI with fewer measured first words than this is tried first (a fair first try); the penalty still spreads those


class FirstWordBook:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._by: dict = defaultdict(lambda: deque(maxlen=60))

    def add(self, ai: str, seconds: Optional[float], when: Optional[float] = None) -> None:
        if isinstance(seconds, (int, float)) and seconds > 0:
            self._by[ai].append((self.clock() if when is None else when, float(seconds)))

    def recent(self, ai: str) -> list:
        now = self.clock()
        return [s for t, s in list(self._by.get(ai, ())) if now - t <= RECENT_S][-20:]

    def ewma(self, ai: str) -> tuple:
        """(moving average of the recent first-word times, how many), recent ones weighing most; (None, 0) with no data."""
        xs = self.recent(ai)
        if not xs:
            return None, 0
        v = xs[0]
        for x in xs[1:]:
            v = EWMA_ALPHA * x + (1 - EWMA_ALPHA) * v
        return v, len(xs)

    def median(self, ai: str, minimum: int = SLOW_NEEDS) -> Optional[float]:
        xs = sorted(s for _, s in list(self._by.get(ai, ()))[-30:])
        return xs[len(xs) // 2] if len(xs) >= minimum else None


class LoadBook:
    def __init__(self):
        self.n: dict = defaultdict(int)

    def start(self, ai: str) -> None:
        self.n[ai] += 1

    def finish(self, ai: str) -> None:
        self.n[ai] = max(0, self.n[ai] - 1)


class RestBook:
    """Three failures in a row within a minute rest an AI for two minutes (settings). One blip never does; a success clears the count."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._fails: dict = defaultdict(list)
        self._until: dict = {}
        self._why: dict = {}

    def fail(self, ai: str, why: str, rest: dict) -> bool:
        now = self.clock()
        xs = [t for t in self._fails[ai] if now - t <= rest["within_s"]] + [now]
        self._fails[ai] = xs
        if len(xs) >= rest["failures"]:
            self._until[ai] = now + rest["for_s"]
            self._why[ai] = why
            self._fails[ai] = []
            return True
        return False

    def ok(self, ai: str) -> None:
        self._fails.pop(ai, None)
        self._until.pop(ai, None)
        self._why.pop(ai, None)

    def until(self, ai: str) -> Optional[float]:
        t = self._until.get(ai)
        if t is None:
            return None
        if t <= self.clock():
            self._until.pop(ai, None)
            self._why.pop(ai, None)
            return None
        return t

    def resting(self) -> list:
        return [{"ai": ai, "until": self.until(ai), "why": self._why.get(ai)} for ai in list(self._until) if self.until(ai)]


class CostBook:
    """Recent real cost per request, by job and AI (only what the AI reported, or what its own price list works out to)."""

    def __init__(self):
        self._by: dict = defaultdict(lambda: deque(maxlen=30))

    def add(self, job: str, ai: str, cost: Optional[float]) -> None:
        if isinstance(cost, (int, float)):
            self._by[(job, ai)].append(float(cost))

    def average(self, job: str, ai: str) -> Optional[float]:
        xs = self._by.get((job, ai))
        return sum(xs) / len(xs) if xs else None


# ---- choosing --------------------------------------------------------------------------------------------------------------------------------------------

CLASS_FREE, CLASS_INCLUDED, CLASS_PAID = "free", "included", "paid"
CLASS_RANK = {CLASS_FREE: 0, CLASS_INCLUDED: 1, CLASS_PAID: 2}


@dataclass
class Candidate:
    ai: str
    note: str = ""                     # why it stands where it does ("no cost reported yet"), for the status
    resting_until: Optional[float] = None


class Engine:
    """All the state and the choosing. `classify(ai)` -> (CLASS_*, kind) and `key(ai)` -> the name the Speeds data uses for that AI are handed in."""

    def __init__(self, classify: Callable[[str], tuple], key: Callable[[str], str] = lambda a: a, clock: Callable[[], float] = time.time):
        self.classify, self.key, self.clock = classify, key, clock
        self.first_words = FirstWordBook(clock)
        self.load = LoadBook()
        self.rest = RestBook(clock)
        self.costs = CostBook()
        self._turn: dict = defaultdict(int)
        self.checks = {"agreed": 0, "disagreed": 0}          # SkyrimNet's own log against our recognition (status only)

    def order(self, job: str, route: dict, settings: dict, expand: Callable[[str], list] = lambda a: [a]) -> list:
        ais: list = []
        for entry in route["ais"]:
            for a in expand(entry):
                if a and a not in ais:
                    ais.append(a)
        how = route.get("how", "order")
        notes: dict = {}
        if how == "turns" and ais:
            i = self._turn[job] % len(ais)
            self._turn[job] += 1
            ais = ais[i:] + ais[:i]
        elif how == "fastest":
            def score(a):
                v, n = self.first_words.ewma(self.key(a))
                pen = IN_FLIGHT_PENALTY_S * self.load.n[self.key(a)]
                if n < FIRST_TRIES:
                    notes[a] = "no first-word data yet: a fair first try" if n == 0 else "little first-word data yet"
                    return (0 if n == 0 else (v or 0) * 0.8) + pen
                return v + pen
            ais = sorted(ais, key=score)
        elif how in ("free", "cheapest"):
            def rank(a):
                cls = self.classify(a)[0]
                if how == "cheapest" and cls == CLASS_PAID:
                    c = self.costs.average(job, self.key(a))
                    if c is None:
                        notes[a] = "no cost reported yet, so it goes last"
                        return (CLASS_RANK[cls], 1, 0.0)
                    return (CLASS_RANK[cls], 0, c)
                return (CLASS_RANK[cls], 0, 0.0)
            ais = sorted(ais, key=rank)
        cands = [Candidate(a, notes.get(a, "")) for a in ais]
        awake = [c for c in cands if not self.rest.until(self.key(c.ai))]
        asleep = sorted((c for c in cands if self.rest.until(self.key(c.ai))), key=lambda c: self.rest.until(self.key(c.ai)))
        for c in asleep:
            c.resting_until = self.rest.until(self.key(c.ai))
        return awake + asleep                              # the last one standing is always tried: a resting AI is only a last resort

    def slow_limit(self, cand: Candidate, route: dict, settings: dict, *, streamed: bool, job_keys: tuple) -> Optional[float]:
        """Seconds to wait for a first word (or a whole reply when not streamed) before passing on, or None: no limit. Off unless the player turned it on
        (or set one for the job); learned per AI from its own recent first words; never under 5 s; never for a local model; streamed dialogue only when allowed."""
        if self.classify(cand.ai)[1] == "local":
            return None
        explicit = route.get("slow_limit_s")
        if not explicit and not settings["slow_handover"]["enabled"]:
            return None
        if streamed and any(k in DIALOGUE_GROUP for k in job_keys) and not settings["slow_handover"]["streamed_dialogue"] and not explicit:
            return None
        if explicit:
            return max(EXPLICIT_MIN_S, float(explicit))
        m = self.first_words.median(self.key(cand.ai))
        if m is None:
            return None
        return min(SLOW_MAX_S, max(SLOW_MIN_S, SLOW_FACTOR * m))

    def status(self, settings: dict) -> dict:
        return {"resting": self.rest.resting(), "checks": dict(self.checks)}


# ---- hand-over -------------------------------------------------------------------------------------------------------------------------------------------

REQUEST_ERRORS = (400, 413, 422)         # the request itself is wrong: another AI would say the same, so it is never a reason to switch


def failure_kind(status: Optional[int], detail: str = "") -> str:
    """'request' (never hand over), 'busy' (our own line was full: hand over, but it says nothing about the AI), or 'failed' (hand over, counts toward resting)."""
    if status in REQUEST_ERRORS:
        return "request"
    if status == 429 and "line" in (detail or "").lower():
        return "busy"
    return "failed"


def is_empty_reply(response: Any) -> bool:
    """A non-streamed answer with no text (and no tool call) in it."""
    try:
        choices = response.get("choices") if isinstance(response, dict) else None
        if not choices:
            return False
        msg = choices[0].get("message") or {}
        return not (msg.get("content") or "").strip() and not msg.get("tool_calls")
    except Exception:
        return False


@dataclass
class Outcome:
    ai: str
    skipped: list = field(default_factory=list)       # [(ai, why)]
    response: Any = None


async def run_route(engine: Engine, job: str, cands: list, settings: dict, call: Callable[[str], Awaitable[Any]], *, streamed: bool, job_keys: tuple, route: dict,
                    skipped: Optional[list] = None, passthrough: tuple = (), error_status: Callable[[BaseException], tuple] = lambda e: (getattr(e, "status_code", None), str(getattr(e, "detail", e)))) -> Outcome:
    """Try the candidates in order. `call(ai)` returns the finished answer (a stream already opened and its first event read) or raises. Anything in `passthrough`
    (the client hanging up) goes straight out. The last candidate's failure is the one raised."""
    skipped = [] if skipped is None else skipped        # the caller may hand in the record's own list, so the log line sees it as it grows
    for i, c in enumerate(cands):
        last = i == len(cands) - 1
        k = engine.key(c.ai)
        limit = None if last else engine.slow_limit(c, route, settings, streamed=streamed, job_keys=job_keys)
        engine.load.start(k)
        t0 = time.monotonic()
        done = False
        try:
            coro = call(c.ai)
            resp = await (asyncio.wait_for(coro, limit) if limit else coro)
            if is_empty_reply(resp) and not last:
                skipped.append((c.ai, "empty reply"))
                continue
            engine.rest.ok(k)
            done = True
            return Outcome(c.ai, skipped, _release_with(resp, engine.load, k))
        except passthrough:
            raise
        except asyncio.TimeoutError as e:
            if not (limit and time.monotonic() - t0 >= limit * 0.95):   # a timeout from inside the AI's own call is a failure, not our limit
                status, detail = error_status(e)
                engine.rest.fail(k, "timed out", settings["rest"])
                skipped.append((c.ai, "failed (timed out)"))
                if last:
                    raise
                continue
            skipped.append((c.ai, "too slow"))             # slowness is never a failure: it does not count toward resting
            continue
        except Exception as e:
            status, detail = error_status(e)
            kind = failure_kind(status, detail)
            if kind == "request":
                raise
            if kind == "failed":
                engine.rest.fail(k, f"error {status}" if status else "dropped", settings["rest"])
            skipped.append((c.ai, "busy" if kind == "busy" else "empty reply" if getattr(e, "empty", False) else f"failed ({status})" if status else "failed"))
            if last:
                raise
        finally:
            if not done:
                engine.load.finish(k)
    raise RuntimeError("no AI to try")                    # not reachable: a route always has at least one candidate


def _release_with(resp: Any, load: LoadBook, k: str) -> Any:
    """A stream keeps counting as 'working on' until its last byte is sent; anything else is finished already."""
    body = getattr(resp, "body_iterator", None)
    if body is None:
        load.finish(k)
        return resp

    async def counted():
        try:
            async for chunk in body:
                yield chunk
        finally:
            load.finish(k)
    resp.body_iterator = counted()
    return resp


# ---- checking ourselves against SkyrimNet's own log ---------------------------------------------------------------------------------------------------------

_LOG_HEAD = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?\] \[([^\]]*)\] (\w+) request \[[^\]]+\]:\s*$")
_LOG_MODEL = re.compile(r'^  "model": "([^"]*)"')


def parse_request_log(text: str) -> list:
    """SkyrimNet's own request log (openrouter_input.log) tags every request with its job. -> [(local time 'YYYY-MM-DD HH:MM:SS', job key, model)], untagged streamed
    requests being dialogue. Only the head line and the top-level model line of each entry are read; the prompts are never kept."""
    out: list = []
    cur: Optional[list] = None
    for line in text.splitlines():
        m = _LOG_HEAD.match(line)
        if m:
            if cur is not None:
                out.append(tuple(cur))
            tag, verb = m.group(2), m.group(3)
            key = tag if tag else ("dialogue" if verb == "GenerateStreaming" else "")
            cur = [m.group(1), key, ""]
            continue
        if cur is not None and not cur[2]:
            mm = _LOG_MODEL.match(line)
            if mm:
                cur[2] = mm.group(1)
    if cur is not None:
        out.append(tuple(cur))
    return [e for e in out if e[1]]


class LogChecker:
    """Counts how often our recognition agrees with SkyrimNet's own tag. Never routes anything: it only says how well the recogniser is doing.
    A log entry counts when exactly one recent request of ours has the same model and started within 1.5 s of it."""

    def __init__(self):
        self.agreed = 0
        self.disagreed = 0
        self._seen: deque = deque(maxlen=400)              # (epoch, model tail, keys)

    def note_request(self, when: float, model: str, keys: tuple) -> None:
        self._seen.append((when, (model or "").split("/")[-1], tuple(keys)))

    def feed(self, entries: list, to_epoch: Callable[[str], float]) -> None:
        for ts, key, model in entries:
            try:
                t = to_epoch(ts)
            except Exception:
                continue
            tail = (model or "").split("/")[-1]
            hits = [s for s in self._seen if abs(s[0] - t) <= 1.5 and s[1] == tail and s[2] != (OTHER,)]
            if len(hits) == 1:
                if key in hits[0][2]:
                    self.agreed += 1
                else:
                    self.disagreed += 1
