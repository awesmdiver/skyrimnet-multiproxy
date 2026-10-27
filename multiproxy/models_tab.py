"""The Models tab's data (2026-10-26, design/mockup-model-management.html): every downloaded model on each PC, where it is used, what state it is in.

Nothing here is measured or guessed: files and sizes, the running state, and the jobs the Routing settings name. multiproxy.py hands in what it already knows (the Local device
tab's list, the kept models, the decision and voice engines' own state, the paired PCs' last reports, `uses` from the Overview's route reader), so this is tested on its own.
A field a PC did not report is `None` (the page draws a dash).
"""
from typing import Optional

STATUS_WORDS = {"inuse": "In use", "running": "Running", "stopped": "Stopped", "kept": "On this PC", "keptfile": "Kept on this PC", "loading": "Loading…", "error": "Problem"}
READ_ONLY_NOTE = "Models on this PC cannot be started or stopped from here yet."


def fmt_gb(n: Optional[int]) -> Optional[str]:
    if n is None:
        return None
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def job_names(tags: list) -> list:
    """The jobs behind a `uses` list: 'Memories backup' is the job 'Memories'. Each job once, in the order given."""
    out = []
    for t in tags or []:
        n = str(t)[:-7] if str(t).endswith(" backup") else str(t)
        if n not in out:
            out.append(n)
    return out


def _row(key: str, rid: str, name: str, kind: str, status: str, size: Optional[int], used_by: list, **extra) -> dict:
    return {"key": key, "id": rid, "name": name, "kind": kind, "status": status, "word": STATUS_WORDS.get(status, status), "sizeBytes": size, "sizeText": fmt_gb(size),
            "usedBy": list(used_by), **extra}


def this_pc_rows(installed: list, kept: list, uses: dict, decision: Optional[dict], voice: Optional[dict]) -> list:
    """Rows for this PC. `installed` is the Local device tab's own list (id, name, state, sizeBytes, custom, vision), `kept` the removed models whose files are still here,
    `decision` / `voice` {"id", "name", "sizeBytes", "state"} for the installed quick-decisions and voice model (or None)."""
    rows = []
    for m in installed:
        st = {"running": "running", "starting": "loading", "failed": "error"}.get(m.get("state"), "stopped")
        rows.append(_row("model:" + m["id"], m["id"], m["name"], "own" if m.get("custom") else "model", st, m.get("sizeBytes"), job_names(uses.get("local:" + m["id"]))))
    for k in kept:
        rows.append(_row("kept:" + k["id"], k["id"], k["name"], "kept", "kept", k.get("sizeBytes"), []))
    if decision:
        rows.append(_row("decision:" + decision["id"], decision["id"], decision["name"], "decision", decision["state"], decision.get("sizeBytes"), ["Quick decisions"]))
    if voice:
        rows.append(_row("voice:" + voice["id"], voice["id"], voice["name"], "voice", voice["state"], voice.get("sizeBytes"), ["Voice"]))
    return rows


def runner_rows(builds: list, needed_by: list) -> list:
    """The llama.cpp runner builds on this PC (`LocalModels.engine_builds()`), one row each. The one in use is "In use" while any installed model needs it (their names are the
    hover) and can't be deleted from here; an older build left after an update is "Kept on this PC" and can."""
    rows = []
    for b in builds:
        used = b["current"] and bool(needed_by)
        rows.append(_row("runner:" + b["dir"], b["dir"], f"Model engine (llama.cpp {b['build']})", "llama-runner", "inuse" if used else "keptfile", b["bytes"], list(needed_by) if used else [],
                         dirName=b["dir"], canDelete=not b["current"]))
    return rows


def remote_table(rid: str, name: str, online: Optional[bool], report: Optional[dict], uses: dict) -> dict:
    """One paired PC's table, from the last report its Courier sent. Start and Stop are offered only for a program whose report says it can load and unload; there is no
    Remove here (the Other devices tab has it) and no copy to another PC."""
    out = {"id": rid, "name": name, "this": False, "online": online, "rows": [], "folder": None, "usedBytes": None, "freeBytes": None, "drive": None, "readOnly": False, "note": ""}
    if report is None:
        out["readOnly"] = True
        out["note"] = READ_ONLY_NOTE if online else ""
        return out
    can_any = False
    for p in report.get("programs") or []:
        caps = p.get("capabilities") or {}
        can = bool(caps.get("load") and caps.get("unload"))
        for m in p.get("models") or []:
            loaded = bool(m.get("loaded"))
            st = "running" if loaded else ("loading" if m.get("state") == "loading" else ("error" if m.get("state") == "error" else "stopped"))
            size = m.get("size_bytes") if isinstance(m.get("size_bytes"), int) else None
            can_any = can_any or can
            out["rows"].append(_row(f"remote:{rid}:{p.get('id')}:{m['id']}", m["id"], m.get("name") or m["id"], "own" if m.get("custom") else "remote", st, size,
                                    job_names(uses.get(f"pc:{rid}/{m['id']}")), program=p.get("id"), canRun=can))
    out["readOnly"] = not can_any
    out["note"] = READ_ONLY_NOTE if out["readOnly"] and out["rows"] else ""
    settings, disk = report.get("settings") or {}, report.get("disk") or {}
    out["folder"] = settings.get("models_folder")
    out["usedBytes"] = sum(r["sizeBytes"] for r in out["rows"] if r["sizeBytes"]) or None
    if isinstance(disk.get("free_gb"), (int, float)):
        out["freeBytes"] = int(disk["free_gb"] * 1024 ** 3)
    path = str(disk.get("path") or settings.get("models_folder") or "")
    out["drive"] = path[:2] if len(path) > 1 and path[1] == ":" else (path or None)
    return out


def this_table(name: str, rows: list, folder: Optional[str], free_bytes: Optional[int], drive: Optional[str]) -> dict:
    return {"id": "this", "name": name, "this": True, "online": True, "rows": rows, "folder": folder, "drive": drive, "freeBytes": free_bytes, "readOnly": False, "note": "",
            "usedBytes": sum(r["sizeBytes"] for r in rows if r["sizeBytes"]) or None}


def download_rows(files: dict, decisions: list, voices: list, runners: list) -> list:
    """Rows for what sits in the kept downloads store (`files` = {file name: bytes}) that is not an installed model's own row, so nothing kept is hidden.
    `decisions` / `voices`: [{key, name, names (its files), installed}], `runners`: [{key, name, names, inUse}].
    - a decision or voice model that is not installed but whose files are kept: a "kept download" row (On this PC, Add + Delete); a file an installed voice model also uses
      (the shared words-from-speech model) is left out of the other model's row and never offered for deletion there;
    - a runner build: a row of its own ("In use" while the voice engine is installed from it);
    - any other file in the store (a build left over from an old version): an orphan row named by its file name."""
    rows, claimed = [], set()
    held = {n for v in voices if v["installed"] for n in v["names"]}
    for d in decisions:
        claimed |= set(d["names"])
        names = [n for n in d["names"] if n in files]
        if names and not d["installed"]:
            rows.append(_row("dl:decision:" + d["key"], "decision:" + d["key"], d["name"], "kept-dl", "kept", sum(files[n] for n in names), [], names=names, what="decision", modelKey=d["key"]))
    for v in voices:
        claimed |= set(v["names"])
        names = [n for n in v["names"] if n in files and (n not in held or v["installed"])]
        if names and not v["installed"]:
            rows.append(_row("dl:voice:" + v["key"], "voice:" + v["key"], v["name"], "kept-dl", "kept", sum(files[n] for n in names), [], names=names, what="voice", modelKey=v["key"]))
    for r in runners:
        claimed |= set(r["names"])
        names = [n for n in r["names"] if n in files]
        if names:
            rows.append(_row("dl:runner:" + r["key"], "runner:" + r["key"], r["name"], "runner", "inuse" if r["inUse"] else "keptfile", sum(files[n] for n in names),
                             ["Voice"] if r["inUse"] else [], names=names))
    for n in sorted(files):
        if n not in claimed:
            rows.append(_row("dl:orphan:" + n, "orphan:" + n, f"Leftover download ({n})", "orphan", "keptfile", files[n], [], names=[n]))
    return rows
