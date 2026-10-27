"""Check it like SkyrimNet (2026-10-25): how the three parts of the check are judged. Pure: nothing from MultiProxy is imported (tests use it alone).

The route in multiproxy.py sends the sample requests and hands the plain results here; this file decides green, amber or red and builds the numbers the card shows.
A result that did not happen is never drawn: a part with nothing answered says so, it does not show a zero as if it were a time.

  * One at a time: 5 requests, one after the other.  Result: how many answered and the slowest.
  * A busy room: 6 requests at once.  Result: the slowest and how many stood in line. Amber over `BUSY_SLOW_SECONDS`.
  * Actions: 10 requests that ask for a JSON object.  Result: usable (the reply passes the program's own JSON test, or only after its repair) and repaired.
"""
from typing import Optional

BUSY_SLOW_SECONDS = 20.0          # the director's number for now (SkyrimNet's author may give a better one): the slowest of the busy room above this is amber
WAITED_SECONDS = 0.25             # a request that stood in line at least this long "had to wait"
ONE_AT_A_TIME = 5
BUSY_ROOM = 6
ACTION_COUNT = 10
ACTIONS_GREEN = 9                 # usable 9 or 10 of 10: green
ACTIONS_AMBER = 6                 # 6 to 8: amber; 5 or fewer: red
REQUESTS = ONE_AT_A_TIME + BUSY_ROOM + ACTION_COUNT

GREEN, AMBER, RED = "green", "amber", "red"

# A request that fails because of the account (a key, money) stops the whole check: trying again 20 times only repeats the same refusal.
_ACCOUNT_WORDS = ("key", "api key", "sign in", "signed in", "credit", "balance", "billing", "payment", "quota", "unauthorized", "forbidden", "not accepted", "didn't accept")


def is_account_problem(status: Optional[int], message: str) -> bool:
    """True for a refusal about the key or the payment (401, 402, 403 or words that say so): the check stops and shows the reason."""
    if status in (401, 402, 403):
        return True
    m = (message or "").lower()
    return any(w in m for w in _ACCOUNT_WORDS)


def _seconds(values) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return max(vals) if vals else None


def one_at_a_time(results: list) -> dict:
    """results: [{"ok": bool, "seconds": float}] in the order they ran. -> {"state", "answered", "total", "slowest"}."""
    total = len(results)
    ok = [r for r in results if r.get("ok")]
    slowest = _seconds(r.get("seconds") for r in ok)
    if not total:
        return {"state": None, "answered": 0, "total": 0, "slowest": None}
    state = GREEN if len(ok) == total else (AMBER if ok else RED)
    return {"state": state, "answered": len(ok), "total": total, "slowest": slowest}


def busy_room(results: list) -> dict:
    """results: [{"ok", "seconds", "waited" (seconds in line, or None when the request never went through a line)}] of the requests sent together.
    -> {"state", "answered", "total", "slowest", "waited" (how many stood in line), "longest_wait" (or None)}."""
    total = len(results)
    ok = [r for r in results if r.get("ok")]
    slowest = _seconds(r.get("seconds") for r in ok)
    waits = [r.get("waited") for r in results if r.get("waited") is not None]
    waited = sum(1 for w in waits if w >= WAITED_SECONDS)
    if not total:
        state = None
    elif not ok:
        state = RED
    elif len(ok) < total or (slowest is not None and slowest > BUSY_SLOW_SECONDS):
        state = AMBER
    else:
        state = GREEN
    return {"state": state, "answered": len(ok), "total": total, "slowest": slowest, "waited": waited, "longest_wait": _seconds(waits)}


def actions(results: list) -> dict:
    """results: [{"ok": bool, "label": "fine" | "repaired" | "broken" | None}] one per action prompt. A reply is usable when it came back and is a JSON object the program
    accepts as it is ("fine") or after its own repair ("repaired"). -> {"state", "usable", "repaired", "total"}."""
    total = len(results)
    usable = sum(1 for r in results if r.get("ok") and r.get("label") in ("fine", "repaired"))
    repaired = sum(1 for r in results if r.get("ok") and r.get("label") == "repaired")
    if not total:
        state = None
    elif usable >= ACTIONS_GREEN:
        state = GREEN
    elif usable >= ACTIONS_AMBER:
        state = AMBER
    else:
        state = RED
    return {"state": state, "usable": usable, "repaired": repaired, "total": total}


def reply_label(repair_label: Optional[str], status: str) -> str:
    """The program's own word for a JSON reply. `repair_label` is what the request path saved ("fine", "repaired", "asked_again" or None); `status` is
    `_repair_json_reply`'s reading of the text ("valid", "repaired", "broken"), used when the path saved nothing (FixBrokenReplies off). A reply asked for again
    counts as repaired: it needed the program's help."""
    if repair_label in ("repaired", "asked_again"):
        return "repaired"
    if repair_label == "fine":
        return "fine"
    return {"valid": "fine", "repaired": "repaired"}.get(status, "broken")


def decision_row(sample: dict, answer: Optional[str], confidence: Optional[float], seconds: Optional[float]) -> dict:
    """One row of "Check my decision model": the answer as read, how sure, how long, and whether it is one the sample expects. `answer` None means no answer."""
    got = (answer or "").strip().lower()
    expected = [e.lower() for e in sample.get("expected") or ()]
    return {"id": sample["id"], "name": sample["name"], "answer": answer, "confidence": confidence, "seconds": seconds,
            "expected": sample.get("expected") or [], "as_expected": bool(got) and got in expected}


def own_play(rows) -> list:
    """The "From your own play" card. rows: (job key text, success, reply_repair, queue_wait) for the last seven days. A row for jobs that cannot be told apart
    ("dialogue/action") counts for each of them, as the Speeds tab does. -> [{"job" key, "replies", "fine", "repaired", "asked_again", "longest_wait"}] busiest
    first; a job with no replies is not listed."""
    by: dict = {}
    for job, success, repair, wait in rows:
        if not job or not success:
            continue
        for key in str(job).split("/"):
            a = by.setdefault(key, {"job": key, "replies": 0, "fine": 0, "repaired": 0, "asked_again": 0, "longest_wait": None})
            a["replies"] += 1
            if repair in ("fine", "repaired", "asked_again"):
                a[repair] += 1
            if wait is not None:
                a["longest_wait"] = wait if a["longest_wait"] is None else max(a["longest_wait"], wait)
    return sorted(by.values(), key=lambda a: (-a["replies"], a["job"]))
