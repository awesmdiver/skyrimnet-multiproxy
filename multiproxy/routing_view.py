"""What the Routing tab shows (2026-10-02, step 2): the picture of places, AIs and jobs, built from real state. Nothing from MultiProxy is imported here; multiproxy.py
hands in what it knows (the AIs it can reach now, its Speeds history, the engine's memory) and this builds the one JSON answer `GET /config/routing` gives.

Places: This device (its own models, Ollama, LM Studio), each other device by its nickname, Cloud (logins and keys), and "Not reachable now" for a model a job points at that
nothing answers for. Jobs: the ones SkyrimNet's settings know, the ones seen so far in the Speeds history, and the ones with a route. A job with no route sits on the
AI(s) its model name points at (that is who answers it today); a job with a route sits on its AIs in order (the first one is the answerer, the others back it up).
"""
import re
from typing import Any, Callable, Optional

import fine_tune
import job_names
import routing

PLACE_THIS, PLACE_CLOUD, PLACE_LOST, PLACE_ELSEWHERE, PLACE_UNCHOSEN = "this", "cloud", "lost", "elsewhere", "unchosen"
NO_MODEL_WORDS = "No model chosen yet. Answered as before."          # a job SkyrimNet named as job/<name> that has no route yet, but something else answers it (a group-mate's route, or real model names SkyrimNet sends): nothing is wrong
NO_ANSWER_WORDS = "Drag it onto a model."                            # the same, but nothing answers it at all (routing.named_without_route finds no route: the request gets a 404)
NO_ANSWER_CAPTION = "These jobs have no assigned model. Drag them onto a model to get an answer."          # the director's own sentence
UNCHOSEN_TITLE, UNCHOSEN_CAPTION = "Jobs with no model yet", "answered as before"
LOST_TITLE, LOST_CAPTION = "Needs attention", "Something here is broken and needs fixing."
SET_IN_WORDS = "{provider}, set in SkyrimNet"                         # a model SkyrimNet sends to a provider MultiProxy never sees: nothing to test or track here
# What a real model that nothing answers for is missing, by kind of provider (the Routing card's second line); only where it is known.
MISSING_WORDS = {"cloud": "Needs your {provider} key.", "subscription": "Sign in to {provider}.", "other": "{provider} isn't running, or doesn't have this model.", "local": "Not downloaded on this device.",
                 "nameless": "No provider here uses this name. Add that provider, or pick another model."}
THIS_PC_OWNERS = ("local", "ollama", "lmstudio")


def place_of(owned_by: str, ai: str) -> str:
    if owned_by in THIS_PC_OWNERS:
        return PLACE_THIS
    if owned_by == "other-pc":
        return "pc:" + ai.split("/", 1)[0]
    return PLACE_CLOUD


# The words of a model that a set-up provider no longer lists (2026-10-26); the screen adds "Use <model> instead" itself (RT_GONE in routing_ui.py).
GONE_WORDS = "{provider} no longer offers this model"


def _words(model_id: str) -> tuple:
    """(the letters-only words after the first one, the numbers) of a model name: 'claude-haiku-4-5' -> (('haiku',), (4, 5))."""
    parts = [p for p in re.split(r"[-_.:/ ]+", model_id.lower()) if p]
    family = tuple(p for p in parts[1:] if p.isalpha())
    numbers = tuple(int(p) for p in parts if p.isdigit() and len(p) < 8)
    return family, numbers


def closest_current(old: str, current: list) -> Optional[str]:
    """The newest model in `current` with the same family word as `old` (haiku with haiku), or None when there is no clear match."""
    family, _numbers = _words(old)
    if not family:
        return None
    same = [m for m in current if m != old and _words(m)[0] == family and not m.endswith("*")]
    return max(same, key=lambda m: _words(m)[1]) if same else None


def swap_model(settings: dict, old: str, new: str) -> dict:
    """The routes with `old` replaced by `new` wherever a route names it (also inside a comma list), every other job left as it is."""
    out = dict(settings)
    routes = {}
    for key, route in (settings.get("routes") or {}).items():
        ais = []
        for entry in route.get("ais", []):
            parts = [p.strip() for p in entry.split(",")] if "," in entry else [entry]
            swapped = [new if p == old else p for p in parts]
            ais.append(", ".join(swapped) if "," in entry else swapped[0])
        deduped = []
        for e in ais:
            if e not in deduped:
                deduped.append(e)
        routes[key] = {**route, "ais": deduped}
    out["routes"] = routes
    return out


def _expand(entry: str) -> list:
    return [m.strip() for m in entry.split(",") if m.strip()] if "," in entry else [entry]


def route_ais(route: Optional[dict]) -> list:
    out: list = []
    for e in (route or {}).get("ais", []):
        for a in _expand(e):
            if a not in out:
                out.append(a)
    return out


def _fine_tune(key, on, settings, last_sent, accepts, name_of) -> dict:
    """What SkyrimNet last sent for this job, the player's overrides, and which of the models that answer it can't take each overridden setting."""
    sent = last_sent(key) if last_sent else {}
    over = dict((settings.get("overrides") or {}).get(key) or {})
    skipped = {}
    if accepts:
        for name in over:
            skipped[name] = [name_of(a)[0] for a in on if not accepts(a, name)]
    rows = [{"name": n, "plain": fine_tune.plain_name(n), "sent": sent.get(n), "override": over.get(n), "skippedOn": skipped.get(n, [])} for n in fine_tune.FIELD_NAMES]
    others = [{"name": n, "plain": n, "sent": v} for n, v in sorted(sent.items()) if n not in fine_tune.FIELD_NAMES]
    return {"fineTune": key != "decisions", "settingsSent": rows, "alsoSent": others, "hasSent": bool(sent)}


def _missing(ai: str, provider: str, kind: str, place: str, set_in, gone) -> Optional[str]:
    """What a real model that nothing answers for is missing, in the player's words, where that is known; None otherwise (the card then just names the provider)."""
    if place != PLACE_LOST or set_in or gone:
        return None
    if provider == "not set up":
        return MISSING_WORDS["nameless"]
    return MISSING_WORDS.get(kind, "").format(provider=provider) or None


def build(*, settings: dict, table: dict, status: dict, reachable: list, remotes: dict, stats: dict, seen: dict, costs: Callable[[str, str], Optional[float]],
          classify: Callable[[str], tuple], key_of: Callable[[str], str], name_of: Callable[[str], tuple], now_resting: dict,
          last_sent: Optional[Callable[[str], dict]] = None, accepts: Optional[Callable[[str, str], bool]] = None, remote_names: Optional[dict] = None,
          trouble: Optional[Callable[[str], Optional[str]]] = None, named=()) -> dict:
    """reachable: [{"id", "owned_by"}]; remotes: {nickname: kind}; stats: {"<provider>::<model>" (key_of(ai)): {"calls", "ok", "first_word"}} (last 24 h); seen: {job key: how it was last known}
    ; costs(job, ai key) -> typical cost per line or None; name_of(ai) -> (short name, provider label); now_resting: {key_of(ai): seconds left}."""
    wild = [r["id"][:-1].lower() for r in reachable if r["id"].endswith("*")]
    listed = {r["id"].lower(): r for r in reachable if not r["id"].endswith("*")}
    named = set(named) | routing.exactly_named(table, seen)        # a job SkyrimNet names exactly has its own chip and route, so it is in no group
    groups = routing.hard_groups(table, named=named)
    in_group = {k: g for g in groups for k in g}

    jobs: list = []
    wanted: list = []                                         # every AI id a job or route mentions, in the order met
    keys = list(routing.JOB_ORDER) + [k for k in list(table) + list(seen) + list(settings["routes"]) + job_names.NAMES.known_keys() if k not in routing.JOB_ORDER]
    for key in dict.fromkeys(keys):                         # the jobs MultiProxy knows, then any a mod added or SkyrimNet added since: nothing has to be listed in code
        route = settings["routes"].get(key)
        if not route and key in in_group:                       # jobs that can't be told apart share the first route any of them has
            route = routing.route_for(settings, in_group[key])[0]
        known = key in table
        if not (known or key in seen or route):
            continue
        models = list(table.get(key, {}).get("models", []))
        provs = list(table.get(key, {}).get("providers") or [])
        elsewhere = [{"model": m, "provider": routing.elsewhere_name(p)} for m, p in zip(models, provs + [None] * len(models)) if routing.elsewhere_name(p)]
        on = route_ais(route) if route else models
        if key == "decisions":                                  # Decisions are answered by the order on the Decisions tab, not by a route: no chip, and its model name (typesafe/jev-1.13) is no card
            on = []
        for a in on:
            if a not in wanted:
                wanted.append(a)
        on = [a for a in on if not a.strip().lower().startswith(routing.EXPLICIT_PREFIX)]          # the job's own name (job/<name>) is not a model: never a row in its list (it still has its card in "Jobs with no model yet")
        info = job_names.NAMES.info(key)
        jobs.append({"key": key, "label": info["name"], "about": info["about"], "mod": info["mod"], "puzzle": info["puzzle"], "source": info["source"], "models": models, "inSkyrimNet": known, "route": route,
                     "seen": key in seen, "how": seen.get(key), "onAis": on, "elsewhere": elsewhere, "sharesWith": [k for k in in_group.get(key, []) if k != key],
                     "name": "job/" + key, "routable": key != "decisions", "namedExactly": key in named, **_fine_tune(key, on, settings, last_sent, accepts, name_of)})

    # A model name SkyrimNet sends to a provider MultiProxy does not carry (OpenRouter): never asked of MultiProxy, so it is "set in SkyrimNet", not "not reachable". Only when no
    # job names the same model under any other provider.
    other: dict = {}
    mine: set = set()
    for j in jobs:
        provs = list((table.get(j["key"]) or {}).get("providers") or [])
        for m, p in zip(j["models"], provs + [None] * len(j["models"])):
            (other.__setitem__(m, routing.elsewhere_name(p)) if routing.elsewhere_name(p) else mine.add(m))
    other = {m: p for m, p in other.items() if m not in mine}
    ais: dict = {}
    for r in reachable:
        if r["id"].endswith("*"):
            continue
        ais[r["id"]] = {"id": r["id"], "owned_by": r["owned_by"], "reachable": True}
    for a in wanted:
        if a not in ais:
            low = a.lower()
            if low in listed:
                continue
            w = next((p for p in wild if low.startswith(p)), None)
            ais[a] = {"id": a, "owned_by": "wild" if w else "", "reachable": bool(w), "anyModel": bool(w)}
    out_ais = []
    for a, row in ais.items():
        k = key_of(a)
        short, kind = name_of(a)
        st = stats.get(k) or {}                                   # k names a provider's model (not a name two providers share)
        rest = now_resting.get(k)
        wrong = trouble(k) if trouble else None                   # what the model's own engine says is wrong with it (engine_trouble.py), or None
        cls, _kind = classify(a)
        place = place_of(row["owned_by"], a) if row["reachable"] else PLACE_LOST
        if row.get("anyModel"):
            place = PLACE_CLOUD
        set_in = other.get(a) if place == PLACE_LOST else None
        if set_in:
            place = PLACE_ELSEWHERE
        unchosen = place == PLACE_LOST and a.strip().lower().startswith(routing.EXPLICIT_PREFIX)       # not a model: the name SkyrimNet gave a job, which nobody has routed
        unchosen_case = None
        if unchosen:
            place, kind = PLACE_UNCHOSEN, ""
            try:                                                      # the same code that decides the 404 (multiproxy.py: a named job with no route): no route found means nothing answers it
                unchosen_case = "answered" if routing.named_without_route(settings, a.strip()[len(routing.EXPLICIT_PREFIX):], table)[0] else "none"
            except Exception:
                unchosen_case = "answered"
        gone = None                                                  # the provider is set up (it lists other models) but no longer lists this one
        if place == PLACE_LOST and not set_in and _kind in ("subscription", "cloud") and not unchosen:
            mates = [r["id"] for r in reachable if r["id"] != a and name_of(r["id"])[1] == kind]
            if mates:
                new = closest_current(a, mates)
                gone = {"provider": kind, "words": GONE_WORDS.format(provider=kind), "use": ({"id": new, "name": name_of(new)[0]} if new else None)}
        out_ais.append({
            "id": a, "name": short, "kind": ("" if place == PLACE_LOST and kind == "not set up" else SET_IN_WORDS.format(provider=set_in) if set_in else kind), "place": place, "gone": gone,
            "state": "warn" if rest or wrong or place == PLACE_LOST else ("on" if row["reachable"] else "idle"), "trouble": wrong,          # amber: a real fault; grey: not a fault (not chosen yet, or set in SkyrimNet)
            "unchosen": unchosen, "unchosenCase": unchosen_case, "words": (NO_ANSWER_WORDS if unchosen_case == "none" else NO_MODEL_WORDS) if unchosen else None,
            "missing": _missing(a, kind, _kind, place, set_in, gone),
            "resting": rest, "calls": st.get("calls", 0), "worked": (round(100 * st["ok"] / st["calls"]) if st.get("calls") else None),
            "firstWord": st.get("first_word"), "class": cls, "costByJob": {j["key"]: costs(j["key"], k) for j in jobs if costs(j["key"], k) is not None},
            "anyModel": bool(row.get("anyModel")), "setIn": set_in,
        })
    pickable = [{"id": r["id"], "name": name_of(r["id"])[0], "kind": name_of(r["id"])[1], "place": place_of(r["owned_by"], r["id"]), "kinds": r.get("kinds")} for r in reachable if not r["id"].endswith("*")]
    shown = [a for a in out_ais if a["place"] == PLACE_THIS or a["place"].startswith("pc:") or a["id"] in wanted or a["calls"] > 0]    # the cloud lists dozens of models: only the ones in use
    places = [{"id": PLACE_THIS, "name": "This device", "caption": "its own models"}]
    for nick, kind in remotes.items():
        places.append({"id": "pc:" + nick, "name": (remote_names or {}).get(nick) or nick, "caption": "Courier device" if kind == "courier" else "another device"})
    places.append({"id": PLACE_CLOUD, "name": "Cloud", "caption": "subscriptions and keys"})
    if any(a["place"] == PLACE_ELSEWHERE for a in out_ais):
        places.append({"id": PLACE_ELSEWHERE, "name": "Set in SkyrimNet", "caption": "SkyrimNet sends these to a provider MultiProxy does not support."})
    if any(a["place"] == PLACE_LOST for a in out_ais):
        places.append({"id": PLACE_LOST, "name": LOST_TITLE, "caption": LOST_CAPTION})
    if any(a["place"] == PLACE_UNCHOSEN for a in out_ais):
        none_answers = any(a["unchosenCase"] == "none" for a in out_ais if a["place"] == PLACE_UNCHOSEN)
        places.append({"id": PLACE_UNCHOSEN, "name": UNCHOSEN_TITLE, "caption": NO_ANSWER_CAPTION if none_answers else UNCHOSEN_CAPTION})
    return {
        "fineTuneFields": [{"name": f[0], "plain": f[1], "kind": f[2], "min": f[3], "max": f[4]} for f in fine_tune.FIELDS],
        "settings": settings,
        "priority": routing.priority_order(settings, [j["key"] for j in jobs]),
        "places": places,
        "ais": shown,
        "pickable": pickable,
        "jobs": jobs,
        "groups": groups,
        "hows": [{"id": h, "label": routing.HOW_LABELS[h]} for h in routing.HOWS],
        "sameAs": [[routing.label(k) for k in group] for group in routing.indistinguishable_groups(table)],
        "skyrimNetLinked": bool(table),
        "status": {**status, "names": job_names.NAMES.status()},
    }
