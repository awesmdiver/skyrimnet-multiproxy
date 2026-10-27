"""Share a setup as a file (2026-10-25): Save my setup / Load a setup. Nothing from MultiProxy is imported here (multiproxy.py hands in what it knows), so it is tested on its own.

The file (`multiproxy_<date>.mpsetup`) is plain, readable JSON: a format number and, per job, which models do it (and the backups, and how they are taken), the fine-tune
settings, and the "tested settings" switches. It is built ONLY from known fields, so nothing free-form can slip in, and the finished text is scanned once more: **no key or token, no
web address and no file path ever reaches it** (`assert_clean`). A route that points at another PC or a custom address is written as the job name only, marked "needs setting up here".

Loading is two steps: `parse` + `plan` show what would change (a line the PC cannot use says why and is skipped by itself, nothing is downloaded), `apply` changes it. Before it does,
the caller backs the settings file up (`backup_file`), and `restore_backup` undoes the last load.
"""
import json
import os
import re
import shutil
import time
from typing import Any, Callable, Optional

import fine_tune
import routing
import tested_settings

FORMAT_VERSION = 1
KIND = "multiproxy-setup"
EXTENSION = ".mpsetup"
MAX_BYTES = 1_000_000
BACKUP_NAME = "config.before-last-load.json"

TOO_NEW = "This file is from a newer version of MultiProxy. Update MultiProxy to load it."
NOT_A_SETUP = "This is not a MultiProxy setup file."
NOTHING_NEW = "Nothing in this file will change anything on this PC."
NEEDS_SETUP = "needs setting up here"
KEY_NEEDS = "needs_setting_up_here"           # the file's own marker on a route
NOTE_NEEDS_SETUP = "This points to another PC or a custom address. Set it up yourself."

# Anything that looks like a web address, a file path, a network address or a key. Checked on every string that goes into, or comes out of, a file.
_ADDRESSY = re.compile(
    r"(://|^[A-Za-z]:[\\/]|\\|^/|^~|^\.{1,2}[\\/]|\bwww\.|[A-Za-z0-9-]+\.(com|net|org|io|ai|dev|app|local|lan|home|internal)\b|\blocalhost\b|\b\d{1,3}(\.\d{1,3}){3}\b|:\d{4,5}\b|@[A-Za-z0-9-]+\.)",
    re.I)
_KEYISH = re.compile(r"(\b(sk|pk|rk)[-_][A-Za-z0-9_\-]{8,}|\bghp_|\bgho_|\bxox[abp]-|\bAIza[A-Za-z0-9_\-]{10,}|\bBearer\s|\beyJ[A-Za-z0-9_\-]{10,}|\b[A-Fa-f0-9]{40,}\b|\b[A-Za-z0-9+/]{44,}={0,2}(?![A-Za-z0-9]))")
_SAFE_AI = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+\-/ ()\[\],]{0,299}$")


class SetupError(Exception):
    """A message a player can read as it is."""


def looks_unsafe(text: Any) -> bool:
    """True for a string that is, or holds, a web address, a file path or a key."""
    return not isinstance(text, str) or bool(_ADDRESSY.search(text) or _KEYISH.search(text))


def safe_ai(entry: Any) -> bool:
    """A route entry (a model name, or a comma list of them) that may be written to or read from a file."""
    if not isinstance(entry, str) or not _SAFE_AI.match(entry):
        return False
    return not any(looks_unsafe(part) for part in entry.split(","))


def suggested_name(today: Optional[time.struct_time] = None) -> str:
    return time.strftime("multiproxy_%Y-%m-%d", today or time.localtime()) + EXTENSION


# ---- saving ---------------------------------------------------------------------------------------------------------------------------------------------

def build(block: dict, portable: Callable[[str], bool] = lambda ai: True) -> dict:
    """The file's contents from a cleaned `routing` block. `portable(ai)` is False for a model on another PC or at a custom address."""
    jobs: dict = {}
    overrides = fine_tune.clean_overrides(block.get("overrides"))
    tested = tested_settings.clean_switches(block.get("tested"))
    for key in sorted(set(block.get("routes") or {}) | set(overrides) | set(tested)):
        if not routing.JOB_KEY.match(key):
            continue
        job: dict = {}
        r = (block.get("routes") or {}).get(key)
        if r:
            keep, needs = [], False
            for ai in r.get("ais") or []:
                if safe_ai(ai) and portable(ai) and all(portable(p.strip()) for p in ai.split(",")):
                    keep.append(ai)
                else:
                    needs = True
            route: dict = {"how": r.get("how") if r.get("how") in routing.HOWS else "order"}
            if keep:
                route["ais"] = keep
                if isinstance(r.get("slow_limit_s"), (int, float)) and not isinstance(r.get("slow_limit_s"), bool):
                    route["slow_limit_s"] = r["slow_limit_s"]
            if needs:
                route[KEY_NEEDS] = True
            job["route"] = route
        if key in overrides:
            job["fine_tune"] = overrides[key]
        if key in tested:
            job["tested"] = tested[key]
        if job:
            jobs[key] = job
    return {"kind": KIND, "format": FORMAT_VERSION, "jobs": jobs}


def dumps(doc: dict) -> str:
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    assert_clean(text)
    return text


def _strings(x: Any):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def assert_clean(text: str) -> None:
    """The last check before a file is written: no string in it may look like an address, a path or a key."""
    for s in _strings(json.loads(text)):
        if looks_unsafe(s):
            raise SetupError("Your setup holds something that looks like an address or a key, so it was not saved.")


# ---- loading --------------------------------------------------------------------------------------------------------------------------------------------

def parse(text: str) -> dict:
    """The file's contents made safe: only known fields, sensible values, and no entry that looks like an address or a key. Raises SetupError with a plain line."""
    if len(text) > MAX_BYTES:
        raise SetupError(NOT_A_SETUP)
    try:
        raw = json.loads(text)
    except ValueError:
        raise SetupError(NOT_A_SETUP)
    if not isinstance(raw, dict) or raw.get("kind") != KIND or not isinstance(raw.get("jobs"), dict):
        raise SetupError(NOT_A_SETUP)
    ver = raw.get("format")
    if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
        raise SetupError(NOT_A_SETUP)
    if ver > FORMAT_VERSION:
        raise SetupError(TOO_NEW)
    jobs: dict = {}
    for key, j in raw["jobs"].items():
        if not isinstance(key, str) or not routing.JOB_KEY.match(key) or not isinstance(j, dict):
            continue
        out: dict = {}
        r = j.get("route")
        if isinstance(r, dict):
            ais_raw = [a for a in (r.get("ais") if isinstance(r.get("ais"), list) else [])][:routing.MAX_AIS]
            ais = [a.strip() for a in ais_raw if isinstance(a, str) and safe_ai(a.strip())]
            needs = bool(r.get(KEY_NEEDS)) or len(ais) != len(ais_raw)
            route = routing._clean_route({"ais": ais, "how": r.get("how"), "slow_limit_s": r.get("slow_limit_s")}) if ais else None
            out["route"] = route or {"ais": [], "how": r.get("how") if r.get("how") in routing.HOWS else "order", "slow_limit_s": None}
            out["needs_setup"] = needs
        ft = fine_tune.clean_overrides({key: j.get("fine_tune")}).get(key)
        if ft:
            out["fine_tune"] = ft
        tv = tested_settings.clean_switches({key: j.get("tested")}).get(key)
        if tv:
            out["tested"] = tv
        if out:
            jobs[key] = out
    return {"format": ver, "jobs": jobs}


def _names(ais: list, name_of: Callable[[str], str]) -> str:
    return ", then ".join(name_of(a) for a in ais)


def plan(doc: dict, current: dict, have: Callable[[str], str], name_of: Callable[[str], str], label_of: Callable[[str], str]) -> dict:
    """What loading would do, line by line. `have(ai)` is "" when this PC can use that model, else a short plain reason. `current` is this PC's cleaned `routing` block.

    Returns {"lines": [{"text", "skip", "warn", "job", "part"}], "count": N}. A line with a `skip` is never applied. A line that changes nothing is left out."""
    lines: list = []
    for key, j in doc["jobs"].items():
        label = label_of(key)
        r = j.get("route")
        if r is not None:
            usable = [a for a in r["ais"] if not have(a)]
            left_out = [(a, have(a)) for a in r["ais"] if have(a)]
            warn = " ".join(f"{name_of(a)}: {why}" for a, why in left_out)
            if not usable:
                why = warn or NOTE_NEEDS_SETUP
                lines.append({"text": f"{label} would go to {_names(r['ais'], name_of)}" if r["ais"] else f"{label} has a route in this file", "skip": why, "warn": "", "job": key, "part": "route"})
            else:
                same = (current.get("routes") or {}).get(key)
                new = {"ais": usable, "how": r["how"], "slow_limit_s": r.get("slow_limit_s")}
                if not (same and same.get("ais") == new["ais"] and same.get("how") == new["how"] and same.get("slow_limit_s") == new["slow_limit_s"]):
                    how = f" ({routing.HOW_LABELS[r['how']].lower()})" if len(usable) > 1 and r["how"] != "order" else ""
                    if r.get("needs_setup") or j.get("needs_setup"):
                        warn = (warn + " " if warn else "") + NOTE_NEEDS_SETUP
                    lines.append({"text": f"{label} will use {_names(usable, name_of)}{how}", "skip": "", "warn": warn, "job": key, "part": "route", "value": new})
        ft = j.get("fine_tune")
        if ft and (current.get("overrides") or {}).get(key) != ft:
            lines.append({"text": f"{label}: " + ", ".join(f"{fine_tune.plain_name(n)} {v}" for n, v in ft.items()), "skip": "", "warn": "", "job": key, "part": "fine_tune", "value": ft})
        tv = j.get("tested")
        if tv and (current.get("tested") or {}).get(key) != tv:
            lines.append({"text": f"{label}: " + ", ".join(f"\"{tested_settings.LABELS[s]}\" is {'on' if on else 'off'}" for s, on in tv.items()), "skip": "", "warn": "", "job": key, "part": "tested", "value": tv})
    return {"lines": lines, "count": sum(1 for ln in lines if not ln["skip"])}


def apply(plan_: dict, block: dict) -> dict:
    """The `routing` block with every usable line of the plan applied (nothing else touched), cleaned the way the program cleans any saved block."""
    out = json.loads(json.dumps(block))
    for ln in plan_["lines"]:
        if ln["skip"]:
            continue
        sect = {"route": "routes", "fine_tune": "overrides", "tested": "tested"}[ln["part"]]
        out.setdefault(sect, {})[ln["job"]] = ln["value"]
    return routing.clean_settings(out)


# ---- the undo -------------------------------------------------------------------------------------------------------------------------------------------

def backup_path(settings_dir: str) -> str:
    return os.path.join(settings_dir, "backups", BACKUP_NAME)


def _read_config(config_path: str) -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def backup_file(config_path: str, settings_dir: str) -> bool:
    """Keep the `routing` block aside before a load (the only thing a load changes); True when a copy exists afterwards. Never the whole settings file: that holds every API key
    (security review 4.1.0, S1). A PC with no settings file yet has nothing to lose, and gets an empty copy to go back to."""
    dest = backup_path(settings_dir)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    block = _read_config(config_path).get("routing")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump({"routing": block} if block is not None else {}, f)
    return os.path.exists(dest)


def can_undo(settings_dir: str) -> bool:
    return os.path.isfile(backup_path(settings_dir))


def restore_backup(config_path: str, settings_dir: str) -> bool:
    """Put the saved `routing` block back (nothing else in the settings is touched) and remove the backup (one undo). False when there is none."""
    src = backup_path(settings_dir)
    if not os.path.isfile(src):
        return False
    saved = _read_config(src).get("routing")           # (an older backup holding the whole file is read for its routing block only)
    cfg = _read_config(config_path)
    if saved is None:
        cfg.pop("routing", None)
    else:
        cfg["routing"] = saved
    with open(config_path + ".restoring", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(config_path + ".restoring", config_path)
    os.remove(src)
    return True
