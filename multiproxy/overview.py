"""The Overview page's data (2026-10-05): one look at every machine, model and service, its status and its current numbers.

Pure: multiproxy.py gathers what it already knows (the local models, the other devices' reports, the providers' states, the routes, the last hour of the saved replies)
and hands it to `build`, which returns exactly what overview_ui.py draws. Nothing here reads a file or asks a program.

The rule for every number: it is shown only if something really measured it. A figure with nothing behind it is None and its tile / column / row part is left out
(never drawn as a zero, never estimated). `docs`: the figure-by-figure table is in the handoff for this page, and TECHNICAL.md names each source.

Levels: `ok` (running or ready), `warn` (slow, or needs a look), `bad` (not working), `off` (off or not set up).
"""
import time
from typing import Optional

import quick_stats

ERRORS_MIN_CALLS = 4          # a model with at least this many replies in the hour ...
ERRORS_BELOW = 0.8            # ... of which fewer than this share worked is called out
# The quick stats' colours, in one place: a share under the first is amber, under the second red (a normal number is never coloured).
SUCCESS_AMBER, SUCCESS_RED = 0.90, 0.50
DIALOGUE_AMBER = 0.80
DIALOGUE_FAILED_MIN = 3        # the Dialogue AI is called out when it did not answer at least this many requests in the hour, and more than it did answer
IN_USE_SECONDS = 60          # an online provider that handled a request this recently reads "In use"
WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}


def _num(n: float) -> str:
    return f"{n:,.0f}"


def _secs(s: float) -> str:
    return f"{s:.1f} s"


def _row_n(figures: Optional[dict], match) -> int:
    if not figures:
        return 0
    return sum(v["n"] for k, v in figures["rows"].items() if match(k[0], k[1]))


def _failed_n(figures: Optional[dict], match) -> Optional[int]:
    """Replies that failed in the matching rows (the Requests count minus the ones that worked), or None when none did: a row says so only when something went wrong."""
    if not figures:
        return None
    n = sum(v["n"] - v["ok"] for k, v in figures["rows"].items() if match(k[0], k[1]))
    return n or None


def _avg_text(figures: Optional[dict], match) -> Optional[str]:
    """The average seconds of the successful replies in the matching rows: the very rows the Requests count adds up (`_row_n`), so the two always agree. None for none."""
    if not figures:
        return None
    ok = sum(v["ok"] for k, v in figures["rows"].items() if match(k[0], k[1]) and v.get("avg") is not None)
    if not ok:
        return None
    return _secs(sum(v["avg"] * v["ok"] for k, v in figures["rows"].items() if match(k[0], k[1]) and v.get("avg") is not None) / ok)


def _decision_cells(d: Optional[dict]) -> Optional[list]:
    """The Quick decisions row's four figures for the last hour, only from the saved per-decision records (None when there were none): Total decisions, Answered (the local model
    on its own), Passed to another AI (a stronger AI took over) and Certainty (its average confidence). Total decisions is Answered plus Passed to another AI, so the two always add up: a decision where the
    stronger AI was asked but did not answer (too slow or down, the local answer stood) is in neither, and is not counted. Decisions that went to a paid answerer first by the
    player's choice are not counted either. The two shares are whole numbers that add up to 100."""
    if not d or not d.get("n"):
        return None
    own, stepped = int(d.get("own") or 0), int(d.get("stepped") or 0)
    total = own + stepped
    if not total:
        return None
    own_pct = round(100 * own / total)
    return [["Total decisions", _num(total)], ["Answered locally", f"{_num(own)} ({own_pct}%)"], ["Passed to another AI", f"{_num(stepped)} ({100 - own_pct}%)"],
            ["Certainty", f"{round(100 * d['avgConfidence'])}%" if d.get("avgConfidence") is not None else "-"]]


def _service_requests(key: str, figures: Optional[dict], match) -> Optional[str]:
    """The Requests cell of the Quick decisions row (its saved replies) and of the Voice row (its saved spoken lines). None when nothing was ever saved for it."""
    if key == "decisions":
        return _num(_row_n(figures, match))
    v = (figures or {}).get("voice")
    return _num(v["n"]) if v else None


def _service_avg(key: str, figures: Optional[dict], match) -> Optional[str]:
    if key == "decisions":
        return _avg_text(figures, match)
    v = (figures or {}).get("voice")
    return _secs(v["avg"]) if v and v.get("avg") is not None else None


def _service_failed(key: str, figures: Optional[dict], match) -> Optional[int]:
    if key == "decisions":
        return _failed_n(figures, match)
    v = (figures or {}).get("voice")
    return (v["n"] - v["ok"]) or None if v else None


def pick_test_models(chat: list, routed: list, recent: list) -> list:
    """The models to try for a provider's Test button, best first: a chat model a job route already uses, then one that answered a request in the last day, then the rest in
    the order listed. `chat` is the models that can chat (a non-chat model is never in it); `routed` and `recent` may name models by the provider's own prefix or without it."""
    def bare(x: str) -> str:
        return x[len("ollama/"):] if x.lower().startswith("ollama/") else x
    out: list = []
    for group in (routed, recent, chat):
        for name in group:
            for c in chat:
                if c not in out and (c == name or bare(c) == bare(name)):
                    out.append(c)
    return out


def _compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _at(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _level(share: float, amber: float, red: Optional[float] = None) -> str:
    return "bad" if red is not None and share < red else "warn" if share < amber else "ok"


def quick_block(q: Optional[dict], names: dict) -> Optional[dict]:
    """The Overview's quick stats for the last hour, only from the saved replies (`q` is quick_stats.QuickFigures.result): the six headline tiles, the by-kind table and the
    tokens table. A tile with nothing behind it is left out. `names` maps the short name the saved replies use to the name the page shows. None when nothing was asked in the hour."""
    if not q:
        return None
    tiles = []
    share = q["ok"] / q["n"]
    tiles.append({"id": "success", "value": f"{round(100 * share)}%", "label": "answered" + (f", {_num(q['failed'])} failed" if q["failed"] else ""),
                  "hover": f"{_num(q['ok'])} of {_num(q['n'])} requests were answered in the last hour.", "level": _level(share, SUCCESS_AMBER, SUCCESS_RED)})
    tiles.append({"id": "backups", "value": _num(q["backups"]), "label": "Backups used",
                  "hover": "Times a backup stepped in because your first choice was slow, busy, or down in the last hour.", "level": "ok"})
    if q.get("peak"):
        tiles.append({"id": "peak", "value": _num(q["peak"]["n"]), "label": "Most at once", "hover": f"Most requests handled at once in the last hour, at {_at(q['peak']['at'])}.",
                      "level": "ok"})
    d = q.get("dialogue")
    if d:
        within = d["within"] / d["n"]
        tiles.append({"id": "dialogue", "value": f"{round(100 * within)}%", "label": f"of dialogue lines within {quick_stats.DIALOGUE_LIMIT_SECONDS:.0f} s",
                      "hover": f"{_num(d['within'])} of {_num(d['n'])} dialogue lines came back in {quick_stats.DIALOGUE_LIMIT_SECONDS:.0f} seconds or less in the last hour.",
                      "level": _level(within, DIALOGUE_AMBER)})
    s = q.get("slowest")
    if s:
        who = names.get(s["provider"]) or s["provider"] or s["model"]
        tiles.append({"id": "slowest", "value": _secs(s["seconds"]), "label": f"Slowest: {who}", "hover": f"Slowest reply in the last hour: {s['model']}, at {_at(s['at'])}.", "level": "ok"})
    tok = [t for t in q.get("tokens") or [] if t["in"] is not None or t["out"] is not None]
    if tok:
        tin, tout = sum(t["in"] or 0 for t in tok), sum(t["out"] or 0 for t in tok)
        tiles.append({"id": "tokens", "value": _compact(tin + tout), "label": "Tokens used",
                      "hover": f"In the last hour: {_num(tin)} in and {_num(tout)} out, from what each AI reported.", "level": "ok"})
    kinds = [{"kind": k, "requests": _num(q["kinds"][k]["n"]) if k in q["kinds"] else None,
              "avg": _secs(q["kinds"][k]["avg"]) if k in q["kinds"] and q["kinds"][k]["avg"] is not None else None} for k in quick_stats.KINDS]
    tokens = [{"name": names.get(t["name"]) or t["name"], "in": _num(t["in"]) if t["in"] is not None else None, "out": _num(t["out"]) if t["out"] is not None else None}
              for t in q.get("tokens") or []]
    return {"tiles": tiles, "kinds": kinds, "tokens": tokens if tok else []}


def _typical(figures: Optional[dict], match) -> Optional[float]:
    """The middle reply time of the matching rows (weighted by nothing: the median of each row's own typical, which is exact when one row matches)."""
    if not figures:
        return None
    vals = sorted(v["typical"] for k, v in figures["rows"].items() if match(k[0], k[1]) and v["typical"] is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _failing_often(figures: Optional[dict], match) -> bool:
    if not figures:
        return False
    n = ok = 0
    for k, v in figures["rows"].items():
        if match(k[0], k[1]):
            n += v["n"]
            ok += v["ok"]
    return n >= ERRORS_MIN_CALLS and ok / n < ERRORS_BELOW


def _ai_match(ai: dict, local_models: list):
    """A test for the saved-reply rows (provider, model) of one AI in a route, or None when its row cannot be told (an unknown kind of key)."""
    key = ai.get("key") or ""
    kind, _, rest = key.partition(":")
    if kind == "provider":
        return lambda prov, model, s=rest: prov == s
    if kind == "pc":
        return lambda prov, model, full=rest: model == full
    if kind == "local":
        names = {rest} | {m.get("modelName") for m in local_models if m["id"] == rest}
        return lambda prov, model, ids=names: prov == "Local" and model in ids
    return None


def _counts(figures: Optional[dict], match) -> tuple:
    """(requests, answered) in the last hour for the rows `match` picks."""
    if not figures or match is None:
        return 0, 0
    n = ok = 0
    for k, v in figures["rows"].items():
        if match(k[0], k[1]):
            n += v["n"]
            ok += v["ok"]
    return n, ok


def _join(names: list) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def meanwhile(routes: list, is_down, suffix: str, nothing: str) -> str:
    """What happens meanwhile, from the real routes. `routes` is [{"job": "Memories", "ais": [{"key", "pc", "name"}, ...]}] in each job's order; `is_down(ai)` says
    whether an AI is the one that is not working. For every job whose route uses a down AI, the first AI in its route that is not down takes over; a job with no other
    AI says so. Jobs that share the same stand-in read as one sentence. `nothing` is the line when no route uses it."""
    covers: dict = {}
    alone = []
    for r in routes:
        ais = r.get("ais") or []
        if not any(is_down(a) for a in ais):
            continue
        nxt = next((a for a in ais if not is_down(a)), None)
        if nxt is None:
            alone.append(r["job"])
        else:
            covers.setdefault(nxt["name"], []).append(r["job"])
    parts = [f"{name} covers {_join(jobs)}{suffix}." for name, jobs in covers.items()]
    if alone:
        parts.append(f"{_join(alone)} {'has' if len(alone) == 1 else 'have'} no backup AI.")
    return " ".join(parts) if parts else nothing


def uncovered(routes: list, is_down) -> bool:
    """True when some job's whole route is down, so nothing covers it. A job with another AI that is up is covered; an AI that is in no route has no job to lose."""
    for r in routes:
        ais = r.get("ais") or []
        if any(is_down(a) for a in ais) and all(is_down(a) for a in ais):
            return True
    return False


def _pc_uncovered(p: dict, routes: list, decisions: Optional[dict], voice: Optional[dict]) -> bool:
    """Does anything an offline device hosts have no cover? A routed job with no other AI, the voice service (never has a backup), the decision model with no stand-in."""
    if uncovered(routes, lambda a, pid=p["id"]: a.get("pc") == pid):
        return True
    if voice and voice.get("pc") == p["id"] and (voice.get("installed") or voice.get("state") == "unreachable"):
        return True
    return bool(decisions and decisions.get("pc") == p["id"] and decisions.get("installed") and not decisions.get("fallback"))


SKYRIMNET_TALKING_S = 2 * 60          # a request from SkyrimNet this recently: connected
SKYRIMNET_GAME_RUNNING_S = 10 * 60    # the mod says Skyrim is running: a quiet moment or a loading screen this long still counts as connected


def skyrimnet_status(seen_at: Optional[float], game_running: bool, now: float) -> dict:
    """{"connected", "line"} for the Overview row. Connected = SkyrimNet asked something in the last 2 minutes, or the mod says Skyrim is running and it asked in the last 10.
    Otherwise grey: how long ago it was last seen, or that we are still waiting if it never has been this run."""
    if seen_at is None:
        return {"connected": False, "line": "Waiting for SkyrimNet"}
    age = max(0.0, now - seen_at)
    if age <= SKYRIMNET_TALKING_S or (game_running and age <= SKYRIMNET_GAME_RUNNING_S):
        return {"connected": True, "line": "Connected"}
    if age < 3600:
        ago = f"{int(age // 60)} min ago"
    elif age < 12 * 3600:
        ago = f"{int(age // 3600)} h ago"
    else:
        ago = "earlier today"
    return {"connected": False, "line": "last seen " + ago}


def build(*, tracking: bool, figures: Optional[dict], this_pc: dict, local_models: list, local_speeds: dict, pcs: list, providers: list, health: dict,
          uses: dict, routes: list, decisions: Optional[dict], voice: Optional[dict], setup: dict, skyrimnet: Optional[dict] = None, models_off: bool = False, skyrim_folder_lost: bool = False,
          jobs: Optional[list] = None, gpu: Optional[dict] = None, set_me_up: Optional[dict] = None) -> dict:
    """The whole page. See the module comment. Inputs (all plain data):
      this_pc        {"name", "host", "gpu" (name or None), "usedMb", "totalMb"}
      local_models   [{"id", "name", "modelName", "state" (stopped|starting|running|failed)}]            local_speeds {id: {"state", "tokensPerSecond"}}
      pcs            [{"id", "online" (True|False|None), "gpu", "program", "models": [{"id", "name", "state"}], "problem" (str|None), "coveredBy", "usedMb", "totalMb"}]
      providers      [{"id", "name", "short" (the name the saved replies use), "state" (ok|warn|off), "status", "cloud" (bool)}]     health {short: {"level"}}
      uses           {row key: ["Dialogue", "Memories backup"]}      routes  [{"job", "ais": [{"key", "pc", "name"}]}]
      decisions      {"state", "label", "pc", "installed", "fallback"} or None        voice {"state", "label", "pc", "installed"} or None
      setup          {"done": bool, "left": ["Add a provider", ...]}
      skyrimnet      {"connected": bool} or None when it was not measured (the row is then left out)
      jobs           [{"key", "job" (its name), "ais": [{"key", "pc", "name"}, ...]}] every job in the Routing tab's order, its steps in route order (expanded and shared exactly as that tab shows them)
      gpu            {device name ("This device" or a nickname): {"cards": real cards, "choices": {"local:<id>" | "decisions" | "voice": card id or None (Automatic)}}}"""
    track = bool(tracking and figures is not None)
    rows_pcs, rows_online, rows_idle, rows_unused, attention, machines = [], [], [], [], [], []

    def does_of(key: str) -> str:
        jobs = uses.get(key) or []
        return ", ".join(jobs[:3]) + (f" and {len(jobs) - 3} more" if len(jobs) > 3 else "")

    def speed_text(tps, typ) -> Optional[str]:
        if tps:
            return f"{tps:,.0f} tokens/s"
        if typ is not None:
            return f"{_secs(typ)} typical"
        return None

    def add_attention(level: str, name: str, line: str, tab: dict, action: Optional[dict] = None) -> None:
        attention.append({"level": level, "name": name, "line": line, "tab": tab, **({"action": action, "key": "local:" + action["body"].get("id", "")} if action else {})})

    # ---- this device ----
    runs = ["MultiProxy"]
    running = [m for m in local_models if m["state"] == "running"]
    if models_off:                                        # the Settings switch: this PC runs no models (muted words, never amber or red)
        runs.append("Models off here")
    elif running:
        runs.append(f"{len(running)} model{'s' if len(running) != 1 else ''}")
    if decisions and decisions.get("installed") and (decisions.get("pc") or "") in ("", this_pc["name"]):
        runs.append("decision engine")
    if voice and voice.get("installed") and (voice.get("pc") or "") in ("", this_pc["name"]):
        runs.append("voice")
    machines.append({"name": "This device", "sub": this_pc.get("host") or "", "level": "ok", "word": "Online", "gpu": this_pc.get("gpu"),
                     "memory": _memory(this_pc.get("usedMb"), this_pc.get("totalMb")), "runs": ", ".join(runs), "tab": {"to": "local"}})

    # ---- other devices ----
    offline_pcs = {p["id"] for p in pcs if p["online"] is False}
    pc_names = {p["id"]: p.get("name") or p["id"] for p in pcs}
    for p in pcs:
        online = p["online"]
        ready = [m for m in p["models"] if m["state"] == "running"]
        if online is False:
            if p.get("codeChanged"):                      # reachable, only its code changed: amber, and the Other devices tab asks for the new code
                level, word = "warn", "Needs the new pairing code"
            else:
                level, word = ("bad" if _pc_uncovered(p, routes, decisions, voice) else "warn"), "Offline"
        elif online is None:
            level, word = "off", "Checking"
        elif p.get("problem"):
            level, word = "warn", "Needs a look"
        elif p.get("courierOlder"):
            level, word = "warn", "Courier needs an update"
        else:
            level, word = "ok", "Online"
        pc_runs = []
        if online:
            if p.get("program"):
                pc_runs.append(p["program"])
            if ready:
                pc_runs.append(f"{len(ready)} model{'s' if len(ready) != 1 else ''}")
            if decisions and decisions.get("installed") and decisions.get("pc") == p["id"]:
                pc_runs.append("decision engine")
            if voice and voice.get("installed") and voice.get("pc") == p["id"]:
                pc_runs.append("voice")
        machines.append({"id": p["id"], "name": p.get("name") or p["id"], "sub": "", "level": level, "word": word, "gpu": p.get("gpu"),
                         "memory": _memory(p.get("usedMb"), p.get("totalMb")) if online else None,
                         "runs": ", ".join(pc_runs) or None, "tab": {"to": "other"}})
        if online is False:
            add_attention(level, p.get("name") or p["id"], _hosted_line(p, routes, decisions, voice), {"to": "other"})
        elif p.get("problem"):
            add_attention("warn", p.get("name") or p["id"], meanwhile(routes, lambda a, pid=p["id"]: a.get("pc") == pid, " until it is fixed", ""), {"to": "other"})
        for m in p["models"]:
            key = f"pc:{p['id']}/{m['id']}"
            name_match = lambda prov, model, full=f"{p['id']}/{m['id']}": model == full
            state, action, test = "other", None, None
            courier = p.get("kind") == "courier" and m.get("program") and m.get("program") not in ("claude", "copilot")      # Claude and Copilot on that device have no load / unload: they answer when asked
            if online is False:
                lv, wd, state = ("bad" if not p.get("codeChanged") and uncovered([r for r in routes if any(a.get("key") == key for a in r["ais"])], lambda a, pid=p["id"]: a.get("pc") == pid) else "warn"), "Not ready", "offline"
            elif online is None:
                lv, wd = "off", "Checking"
            elif m["state"] == "running":
                lv, wd, state = "ok", "Running", "running"
                action = _action("stop", "/config/other-pcs/courier/action", {"id": p["id"], "program": m.get("program"), "action": "unload", "model": m["id"]},
                                 ("/config/other-pcs/courier/action", {"id": p["id"], "program": m.get("program"), "action": "load", "model": m["id"]})) if courier else None
                test = {"model": f"{p['id']}/{m['id']}"}
            elif m["state"] == "starting":
                lv, wd, state = "off", "Starting…", "starting"
            else:
                bad_state = m["state"] == "error"
                lv, wd, state = ("warn", "Not working", "stopped") if bad_state else ("off", "Stopped", "stopped")      # a crashed model on an online device needs a look (amber); a plain stop is on purpose (grey)
                action = _action("start", "/config/other-pcs/courier/action", {"id": p["id"], "program": m.get("program"), "action": "load", "model": m["id"]}) if courier else None
            if lv in ("ok",) and track and _failing_often(figures, name_match):
                lv, wd = "warn", "Failing often"
            n_req = _row_n(figures, name_match) if track else 0
            loaded = online and state in ("running", "starting") and m.get("program") not in (None, "claude", "copilot")      # loaded in Ollama / LM Studio / llama.cpp right now (Claude and Copilot are always 'ready': they only count when used)
            in_use = bool(uses.get(key)) or n_req > 0 or bool(loaded)            # routed, named by SkyrimNet, loaded now, or it answered / failed in the last hour; anything else goes in the closed "Not in use" section
            if in_use and ((state == "stopped" and lv in ("bad", "warn")) or (lv == "warn" and wd == "Failing often")):
                add_attention(lv, m.get("name") or m["id"],
                              meanwhile(routes, lambda a, k=key: a.get("key") == k, " until it works again", ""), {"to": "other"})
            (rows_pcs if in_use else rows_unused).append({"key": key, "name": m.get("name") or m["id"], "does": does_of(key), "where": p.get("name") or p["id"], "level": lv, "word": wd,
                             "speed": speed_text(None, _typical(figures, name_match)) if track and online else None,
                             "requests": _num(_row_n(figures, name_match)) if track else None, "avg": _avg_text(figures, name_match) if track else None, "failed": _failed_n(figures, name_match) if track else None, "tab": {"to": "other"}, "state": state, "action": action, "test": test,
                             "inUse": _in_use(figures, name_match) if track else False, "engine": m.get("program") not in ("claude", "copilot")})

        # the programs that device runs: one row each (Ollama, LM Studio, Claude ...), so the table names what the card says it runs. Requests and time add up the program's own models.
        for e in p.get("engines") or []:
            fulls = {f"{p['id']}/{m['id']}" for m in p["models"] if m.get("program") == e["id"]}
            match = lambda prov, model, fulls=fulls: model in fulls
            if online is False:
                lv, wd, state = "warn" if p.get("codeChanged") else ("bad" if _pc_uncovered(p, routes, decisions, voice) else "warn"), "Not ready", "offline"
            elif online is None:
                lv, wd, state = "off", "Checking", "other"
            elif e["id"] in ("claude", "copilot"):
                lv, wd, state = "ok", "Ready", "running"
            elif e.get("running"):
                lv, wd, state = "ok", "Running", "running"
            else:
                lv, wd, state = "off", "Stopped", "stopped"
            call = {"id": p["id"], "program": e["id"], "model": ""}            # the very call Other devices makes for its Start / Stop buttons
            can = e.get("can") or {}
            action = None
            if online and p.get("kind") == "courier" and e["id"] not in ("claude", "copilot"):
                url = "/config/other-pcs/courier/action"
                if state == "stopped" and can.get("start"):
                    action = _action("start", url, {**call, "action": "start"})
                elif state == "running" and can.get("stop"):
                    action = _action("stop", url, {**call, "action": "stop"}, (url, {**call, "action": "start"}))
            rows_pcs.append({"key": f"engine:{p['id']}/{e['id']}", "name": ENGINE_NAMES.get(e["id"], e["name"]), "does": "", "where": p.get("name") or p["id"], "level": lv, "word": wd,
                             "speed": None, "requests": _num(_row_n(figures, match)) if track else None, "avg": _avg_text(figures, match) if track else None,
                             "failed": _failed_n(figures, match) if track else None, "tab": {"to": "other"}, "state": state, "action": action, "test": None,
                             "inUse": _in_use(figures, match) if track else False})

    # ---- models on this device ----
    for m in local_models:
        key = f"local:{m['id']}"
        match = lambda prov, model, ids=(m["id"], m.get("modelName")): prov == "Local" and model in ids
        if m.get("guardNote"):      # the graphics memory guard stopped it to protect Skyrim: said once, with the same Start the row has (it never starts itself while the game runs)
            add_attention("warn", "Graphics memory", m["guardNote"], {"to": "local"}, _action("start", "/config/local-models/start", {"id": m["id"]}))
            attention[-1]["help"] = "memory-guard"
        lv, wd = {"running": ("ok", "Running"), "starting": ("off", "Starting…"), "failed": ("warn", "Not working")}.get(m["state"], ("off", "Stopped"))
        state = "running" if m["state"] == "running" else "starting" if m["state"] == "starting" else "stopped"
        test = {"model": m.get("modelName") or f"local/{m['id']}"} if state == "running" else None
        action = (_action("stop", "/config/local-models/stop", {"id": m["id"]}, ("/config/local-models/start", {"id": m["id"]})) if state == "running" else _action("start", "/config/local-models/start", {"id": m["id"]}) if state == "stopped" else None)
        if lv == "ok" and track and _failing_often(figures, match):
            lv, wd = "warn", "Failing often"
        in_use = bool(uses.get(key)) or (track and _row_n(figures, match) > 0)
        if in_use and lv in ("bad", "warn"):
            add_attention(lv, m["name"], meanwhile(routes, lambda a, k=key: a.get("key") == k, " until it works again", ""), {"to": "local"})
        sp = local_speeds.get(m["id"]) or {}
        tps = sp.get("tokensPerSecond") if sp.get("state") == "ok" and m["state"] == "running" else None
        (rows_pcs if in_use else rows_unused).append({"key": key, "name": m["name"], "does": does_of(key), "where": "This device", "level": lv, "word": wd,
                         "speed": speed_text(tps, None) if m["state"] == "running" else None,
                         "requests": _num(_row_n(figures, match)) if track else None, "avg": _avg_text(figures, match) if track else None, "failed": _failed_n(figures, match) if track else None, "tab": {"to": "local"}, "tps": tps, "state": state, "action": action, "test": test,
                         "inUse": _in_use(figures, match) if track else False})

    # ---- the decision model and voice ----
    for key, svc, title, does, tab in (("decisions", decisions, "Quick decisions", "Quick decisions", {"to": "decisions", "row": (decisions or {}).get("pc") or "this"}),
                                        ("voice", voice, "Voice", "Voices", {"to": "voice"})):
        if key == "decisions" and svc and svc.get("cloudOnly"):
            rows_pcs.append({"key": key, "name": title, "does": does, "where": pc_names.get(svc.get("pc") or "", svc.get("pc")) or "This device", "level": "off", "word": "Not used",
                             "speed": None, "requests": None, "avg": None, "failed": None, "line": None, "details": None, "tab": tab, "state": "other", "action": None, "test": None, "inUse": False})
            continue
        if not svc or not (svc.get("installed") or svc.get("state") == "unreachable"):
            continue
        st = svc.get("state") or ""
        cover_lv = "bad" if key == "voice" or not svc.get("fallback") else "warn"          # (only for a device that is offline) voice never has a backup; the decision model is covered by its stand-in
        pc_id = svc.get("pc") or ""
        can = bool(svc.get("installed"))
        url = "/config/decisions/%s" if key == "decisions" else ("/config/voice/pc/%s" if pc_id else "/config/voice/%s")
        body = ({"pc": pc_id or "this"} if key == "decisions" else ({"id": pc_id} if pc_id else {}))
        state, action = "other", None
        if st in ("ready", "too_slow"):
            lv, wd, state = "ok", svc.get("label") or "Running", "running"
            action = _action("stop", url % "stop", body, (url % "start", body)) if can else None
        elif st == "unreachable":
            lv, wd, state = cover_lv, "Not ready", "offline"
        elif st in ("died", "error", "stopped"):
            lv, wd, state = ("off" if st == "stopped" else "warn"), "Stopped" if st == "stopped" else (svc.get("label") or "Stopped"), "stopped"      # stopped on purpose: grey; crashed with its device up: amber
            action = _action("start", url % "start", body) if can else None
            if key == "decisions" and svc.get("localFirst") and can:         # the player chose it to answer first and it is off: decisions are quietly going somewhere else
                lv = "warn"
        elif st in ("starting", "getting_ready", "installing"):
            lv, wd, state = "off", "Starting…" if st != "installing" else (svc.get("label") or "Installing"), "starting"
        else:
            lv, wd = "off", svc.get("label") or "Stopped"
        where = pc_names.get(svc.get("pc") or "", svc.get("pc")) or "This device"           # (the id is only the key: the page shows the name)
        match = (lambda prov, model: prov == "Local decision model") if key == "decisions" else (lambda prov, model: False)
        typ = _typical(figures, match) if track else None
        details = None
        if key == "voice" and track:
            v = (figures or {}).get("voice") or {}
            typ = v.get("typical")                                              # the middle SPEAKING time of the hour's lines (None under 5 lines with it)
            if v.get("waiting") is not None and v.get("speaking") is not None:
                details = [["Waiting", _secs(v["waiting"])], ["Speaking", _secs(v["speaking"])]]      # (averages of the hour's lines that have both times)
        rows_pcs.append({"key": key, "name": title, "does": does, "where": where, "level": lv, "word": wd, "speed": speed_text(None, typ) if st == "ready" else None,
                         "requests": _service_requests(key, figures, match) if track else None,
                         "avg": _service_avg(key, figures, match) if track else None,
                         "failed": _service_failed(key, figures, match) if track else None,
                         "line": None, "details": _decision_cells((figures or {}).get("decisions")) if track and key == "decisions" else details, "tab": tab, "state": state, "action": action, "test": None,
                         "inUse": True if key == "voice" else (_in_use(figures, match) if track else False)})       # (voice leaves no per-service trace: the question is always asked)
        if lv in ("bad", "warn") and svc.get("pc") not in offline_pcs:          # (on an offline device the device's own line already names it)
            add_attention(lv, title, _service_line(key, svc) if not (key == "decisions" and svc.get("localFirst") and st in ("stopped", "died", "error")) else _local_off_line(svc), tab)

    # ---- providers ----
    for p in providers:
        key = f"provider:{p['short']}"
        used = bool(uses.get(key))
        n_req = _row_n(figures, lambda prov, model, s=p["short"]: prov == s) if track else 0
        if p["state"] == "off" and not used and not n_req:
            continue                                   # never set up and nothing points at it: not worth a row
        h = (health.get(p["short"]) or {}).get("level") if track else None
        if p["state"] == "ok":
            ago = ((figures or {}).get("lastReplyAgo") or {}).get(p["short"]) if track else None
            lv, wd = ("warn", "Errors") if h == "bad" else ("warn", "Slow") if h == "warn" else ("ok", "In use" if ago is not None and ago <= IN_USE_SECONDS else "Ready")
        elif p["state"] == "warn":
            lv, wd = "warn", p.get("status") or "Needs a look"
        else:
            lv, wd = "off", p.get("status") or "Not set up"
        if lv in ("warn", "bad"):
            suffix = " until it speeds up" if wd == "Slow" else " until it works again"
            add_attention(lv, p["name"], meanwhile(routes, lambda a, s=p["short"]: a.get("key") == key, suffix, ""),
                          {"to": "setup", "provider": p["id"]})
        typ = _typical(figures, lambda prov, model, s=p["short"]: prov == s) if track else None
        row = {"key": key, "name": p["name"], "does": does_of(key), "where": p.get("where") or "Cloud", "level": lv, "word": wd,
               "speed": speed_text(None, typ), "requests": _num(n_req) if track else None, "avg": _avg_text(figures, lambda prov, model, s=p["short"]: prov == s) if track else None,
               "failed": _failed_n(figures, lambda prov, model, s=p["short"]: prov == s) if track else None, "tab": {"to": "setup", "provider": p["id"]},
               "test": ({"model": p["testModel"], **({"next": list(p["testNext"])} if p.get("testNext") else {})} if p.get("testModel") and p["state"] == "ok" else None)}
        if not p.get("cloud", True):
            rows_pcs.append(row)
        elif used or n_req or lv in ("warn", "bad"):
            rows_online.append(row)                    # in a route, the decision backup, asked in the last hour, or not healthy: a problem is never hidden
        else:
            rows_idle.append(row)                      # ready but not used yet: one closed section

    # ---- a model its own engine says is failing (couldn't load, answered with nothing...): one line with the reason; a route with nothing behind it says so ----
    for r in routes:
        ais_in = r.get("ais") or []
        for a in ais_in:
            note = a.get("trouble")
            if not note:
                continue
            if len(ais_in) == 1:
                add_attention("bad", a["name"], f"{r['job']} has no backup and its model is failing. {note}.", {"to": "routing"})
            elif not any(x["name"] == a["name"] for x in attention):
                add_attention("warn", a["name"], f"{note}. {r['job']} uses its backup.", {"to": "routing"})

    # ---- the Dialogue AI that was asked and mostly did not answer: NPCs go quiet, so it gets its own plain line ----
    if track:
        for r in routes:
            if r.get("job") != "Dialogue":
                continue
            for a in r.get("ais") or []:
                match = _ai_match(a, local_models)
                n, ok = _counts(figures, match)
                failed = n - ok
                if match is None or failed < DIALOGUE_FAILED_MIN or failed <= ok:
                    continue
                backup = any(b is not a and (b.get("key") != a.get("key")) for b in r["ais"])
                line = f"Your NPCs might stay quiet: {a['name']} didn't answer {failed} of {n} Dialogue requests in the last hour."
                same = next((x for x in attention if x["name"] == a["name"]), None)
                if same:                                         # that AI already has a line: this one leads it
                    same["line"] = (line + " " + same["line"]).strip()
                    if not backup:
                        same["level"] = "bad"
                else:
                    add_attention("warn" if backup else "bad", a["name"], line, {"to": {"local": "local", "pc": "other"}.get((a.get("key") or "").partition(":")[0], "setup")})

    # ---- columns: a column that is empty for most rows is left out ----
    rows_idle = rows_unused + rows_idle                  # the models nobody uses come first, then the ready online providers: one closed section
    all_rows = rows_pcs + rows_online + rows_idle
    shown_rows = rows_pcs + rows_online or all_rows      # (the closed section never decides which columns exist)

    def mostly_empty(field: str) -> bool:
        return not shown_rows or sum(1 for r in shown_rows if r.get(field)) * 2 < len(shown_rows)

    cols = {"actions": any(r.get("action") or r.get("test") for r in all_rows), "does": not mostly_empty("does"), "speed": track and not mostly_empty("speed"), "requests": track, "avg": track, "result": track or any(r.get("action") or r.get("test") for r in all_rows), "details": track and any(r.get("key") in ("decisions", "voice") for r in all_rows)}
    groups = [g for g in ({"title": "On your devices", "rows": rows_pcs}, {"title": "Online providers", "rows": rows_online}) if g["rows"]]
    job_card, card_lines = _job_card(jobs or [], all_rows, figures if track else None, decisions, voice, pc_names, providers, gpu or {})
    mcols = {"gpu": any(m.get("gpu") for m in machines), "memory": any(m.get("memory") for m in machines), "runs": any(m.get("runs") for m in machines)}

    # ---- the tiles: only what can really be measured ----
    tiles = []
    if track:
        tiles.append({"id": "rate", "value": f"{figures['perMinute']:.0f} / min" if figures["perMinute"] >= 10 else f"{figures['perMinute']:.1f} / min", "label": "Requests per minute"})
        if figures.get("typical") is not None:
            tiles.append({"id": "wait", "value": _secs(figures["typical"]), "label": "Typical wait for a reply"})
        tps = sorted(r["tps"] for r in rows_pcs if r.get("tps"))
        if tps:
            tiles.append({"id": "tps", "value": f"{tps[len(tps) // 2]:,.0f}", "label": "Tokens per second (local)"})
        tiles.append({"id": "failed", "value": _num(figures["failuresToday"]), "label": "Failures today"})
    if pcs:
        tiles.append({"id": "pcs", "value": f"{1 + sum(1 for p in pcs if p['online'])} of {1 + len(pcs)}", "label": "Devices online"})

    # ---- setup ----
    left = [] if setup.get("done") else list(setup.get("left") or [])
    setup_box = {"show": bool(left), "left": len(left),
                 "title": "Just two setup steps left." if len(left) == 2 else f"{WORDS.get(len(left), str(len(left)))} setup step{'s' if len(left) != 1 else ''} left.",
                 "line": (_join(left)[0].upper() + _join(left)[1:] + ".") if left else ""}

    quick = quick_block((figures or {}).get("quick"), {p["short"]: p["name"] for p in providers}) if track else None

    if skyrim_folder_lost:       # silent while the folder is fine; one warning line (opens Settings > folders) when it is not set, gone or unreadable
        add_attention("warn", "Skyrim folder", "We couldn't find your SkyrimNet folder. Pick it in Settings.", {"to": "settings", "section": "folders"})

    order = {"bad": 0, "warn": 1}
    attention.sort(key=lambda a: order.get(a["level"], 2))
    return {"tracking": track, "tiles": tiles, "quick": quick, "machines": machines, "mcols": mcols, "groups": groups, "jobCard": job_card, "cardLines": card_lines, "cols": cols, "attention": attention, "setup": setup_box, "setMeUp": set_me_up or {"show": False}, "skyrimnet": ({"connected": bool(skyrimnet.get("connected")), "line": str(skyrimnet.get("line") or "")} if isinstance(skyrimnet, dict) else None),
            "idle": {"title": f"Not in use ({len(rows_idle)} model{'s' if len(rows_idle) != 1 else ''} on {len({r['where'] for r in rows_idle})} device{'s' if len({r['where'] for r in rows_idle}) != 1 else ''})", "rows": rows_idle} if rows_idle else None}


def _job_card(jobs: list, all_rows: list, figures: Optional[dict], decisions: Optional[dict], voice: Optional[dict], pc_names: dict, providers: list, gpu: dict) -> tuple:
    """The "Where each job runs" card and the shared-graphics-card lines. Built from the routes themselves (the very list the Routing tab shows), so the first choice here is the
    first choice there. A step's dot and device are those of its row in Models and services. -> (card or None, [lines])"""
    import routing
    lookup = {r["key"]: r for r in all_rows}
    local_providers = {f"provider:{p['short']}" for p in providers if not p.get("cloud", True)}

    def step(n: int, t: dict) -> dict:
        if t.get("elsewhere"):                         # SkyrimNet sends this model to a provider MultiProxy does not carry: nothing is wrong, MultiProxy just never sees it
            return {"n": n, "name": t["name"], "where": "SkyrimNet", "level": "off", "key": t["key"]}
        row = lookup.get(t["key"])
        if t["key"] == "decisions" and not row and decisions:      # the Quick decisions model, on the device it runs on, whether or not it is installed yet
            return {"n": n, "name": "Quick decisions", "where": pc_names.get(decisions.get("pc") or "", decisions.get("pc")) or "This device", "level": "off", "key": "decisions"}
        where = row["where"] if row else ("Cloud" if t["key"].startswith("provider:") else (t.get("pc") or "This device"))
        return {"n": n, "name": row["name"] if row else t["name"], "where": where, "level": row["level"] if row else "off", "key": t["key"]}

    def job_figs(key: str) -> tuple:
        j = ((figures or {}).get("jobs") or {}).get(key)
        return (_num(j["n"]) if j and j["n"] else None, _secs(j["avg"]) if j and j.get("avg") is not None else None)

    rows = []
    for j in jobs:
        n, avg = job_figs(j["key"])
        rows.append({"key": j["key"], "job": j["job"], "steps": [step(i + 1, t) for i, t in enumerate(j["ais"])], "requests": n, "avg": avg, "tab": {"to": "routing", "job": j["key"]}})
    extra = []
    if decisions and decisions.get("installed") and not any(j["key"] == "decisions" for j in jobs):
        local = step(1, {"key": "decisions", "name": "Quick decisions", "pc": decisions.get("pc") or None})
        local["where"] = pc_names.get(decisions.get("pc") or "", decisions.get("pc")) or "This device"
        steps = [local]
        fb = decisions.get("fallback")
        if fb:
            fbs = step(2, {"key": f"provider:{fb}", "name": fb, "pc": None})
            steps = [local, fbs] if decisions.get("localFirst") else [fbs, local]
            steps = [{**s, "n": i + 1} for i, s in enumerate(steps)]
        n, avg = job_figs("decisions")
        if n is None and figures and (figures.get("decisions") or {}).get("n"):
            n = _num(figures["decisions"]["n"])
        extra.append({"key": "decisions", "job": "Quick decisions", "steps": steps, "requests": n, "avg": avg or _avg_text(figures, lambda prov, model: prov == "Local decision model"),
                      "tab": {"to": "decisions", "row": decisions.get("pc") or "this"}})
    if voice and voice.get("installed"):
        vs = step(1, {"key": "voice", "name": "Voice", "pc": voice.get("pc") or None})
        vs["where"] = pc_names.get(voice.get("pc") or "", voice.get("pc")) or "This device"
        v = (figures or {}).get("voice")
        extra.append({"key": "voice", "job": "Voice", "steps": [vs], "requests": _num(v["n"]) if v and v.get("n") else None, "avg": _secs(v["avg"]) if v and v.get("avg") is not None else None,
                      "tab": {"to": "voice"}})
    # Quick decisions and Voice sit where the Routing tab lists "decisions": after the jobs that come before it, then the rest
    dec_at = routing.order_of("decisions")
    at = next((i for i, r in enumerate(rows) if routing.order_of(r["key"]) > dec_at), len(rows))
    rows = rows[:at] + extra + rows[at:]

    # ---- one line when a local first choice and the voice (or the decision model) run on the same device and the same card ----
    lines = []
    if voice and voice.get("installed"):
        dev = pc_names.get(voice.get("pc") or "", voice.get("pc")) or "This device"
        info = gpu.get(dev) or {}

        def same_card(choice_key: Optional[str]) -> bool:
            cards = info.get("cards") or 0
            if cards <= 1:
                return cards == 1                      # (one real card: they are on it. No card information: nothing is claimed)
            ch = info.get("choices") or {}
            return bool(choice_key and ch.get(choice_key) and ch.get(choice_key) == ch.get("voice"))      # (several cards: only when both were put on the same one)

        users = []
        for r in rows:
            if r["key"] == "voice" or not r["steps"]:
                continue
            first = r["steps"][0]
            k = first["key"]
            if k == "decisions":
                on, ck = first["where"], "decisions"
            elif k.startswith("local:"):
                on, ck = "This device", k
            elif k.startswith("pc:"):
                row = next((x for x in all_rows if x["key"] == k), None)
                if not row or not row.get("engine"):
                    continue                           # (Claude and Copilot on that device answer from the cloud)
                on, ck = first["where"], None
            elif k in local_providers:
                on, ck = "This device", None
            else:
                continue                               # an online model never uses the card
            if on == dev and same_card(ck):
                users.append(r["job"])
        if users:
            names = users + ["Voice"]
            lead = ", ".join(names[:-1]) + " and " + names[-1]
            lines.append(f"{lead} both use {dev}'s graphics card and may slow each other down.")
    return ({"title": "Where each job runs", "rows": rows} if rows else None), lines


ENGINE_NAMES = {"llamacpp": "Model engine (llama.cpp)"}          # the director's name for the llama.cpp engine, wherever it is listed


def _memory(used, total) -> Optional[str]:
    if not total or used is None:
        return None
    return f"{used / 1024:.0f} of {total / 1024:.0f} GB"


def _local_off_line(svc: dict) -> str:
    """The local decision model is the player's first choice and it is not running."""
    fb = svc.get("fallback")
    return f"The local decision model is off, so decisions are going to {fb or 'another AI'}. Press Start." if fb else "The local decision model is off, so decisions are not being answered. Press Start."


def _service_line(key: str, svc: dict) -> str:
    if key == "voice":
        return "The voice service is not answering right now."
    fb = svc.get("fallback")
    return f"{fb} answers decisions until it comes back." if fb else "Quick decisions are not answering right now."


def _hosted_line(p: dict, routes: list, decisions: Optional[dict], voice: Optional[dict]) -> str:
    """What an offline device was doing, from every kind of service it hosts: the routed AIs (with the backup that takes over), the voice service and the decision model. Empty when
    nothing uses it: the Machines table already says it is offline, and a sentence is never made up."""
    parts = []
    routed = meanwhile(routes, lambda a, pid=p["id"]: a.get("pc") == pid, " until it comes back", "")
    if routed:
        parts.append(routed)
    elif p.get("coveredBy") and p["models"]:
        parts.append(f"{p['coveredBy']} covers for it until it comes back.")
    if voice and voice.get("pc") == p["id"] and (voice.get("installed") or voice.get("state") == "unreachable"):
        parts.append(_service_line("voice", voice))
    if decisions and decisions.get("pc") == p["id"] and decisions.get("installed"):
        parts.append(_service_line("decisions", decisions))
    return " ".join(parts)


def _action(kind: str, url: str, body: dict, start: Optional[tuple] = None) -> dict:
    """The button on a row: the very call the owning tab makes (Local models, Voice, Quick decisions, Other devices), so there is one way to start or stop anything.
    A stop also carries `restart` (2026-10-05): the same stop, then the same start, called in that order. `start` is that (url, body)."""
    a = {"kind": kind, "label": "Start" if kind == "start" else "Shut down", "url": url, "body": body}
    if kind == "stop" and start:
        a["restart"] = {"label": "Restart", "calls": [{"url": url, "body": body}, {"url": start[0], "body": start[1]}]}
    return a


def _ago(figures: Optional[dict], match) -> Optional[float]:
    """Seconds since the newest reply in the hour from the matching rows, or None. 'Is SkyrimNet using it right now' for the Restart question."""
    if not figures:
        return None
    vals = [v["ago"] for k, v in figures["rows"].items() if match(k[0], k[1]) and v.get("ago") is not None]
    return min(vals) if vals else None


def _in_use(figures: Optional[dict], match) -> bool:
    ago = _ago(figures, match)
    return ago is not None and ago <= IN_USE_SECONDS

