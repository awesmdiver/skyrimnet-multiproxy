"""Tested settings for each job (Routing, 2026-10-26): the settings the research proved for a job on a model family, the card that shows them, and how they go into a request.

Source of truth: `settings_presets.json` (shipped with the program) and docs/roadmap/tested-settings-research-2026-10.md. A row exists only where the research measured it working
on that family and engine; any other model gets "Not tested on this model" and a switch that cannot be turned on. Nothing from MultiProxy is imported here.

Rules for applying (`apply`): only the job's own prompt shape (a prompt that does not look like the one tested gets nothing), only a field the request does not already carry, never
over a setting the person made in MultiProxy (their fine-tune overrides), and a fixed answer format only on a reply that is not streamed, so it can be asked again without it when the
reply comes back empty or broken (`reply_ok`). A switch that is off changes nothing: the request is exactly what it was.
"""
import json
import os
import re
from typing import Any, Optional

# The director's call (2026-10-26 mockup question 2): the models MultiProxy installs itself start with the proven settings ON; every other model starts off and asks.
BUILT_IN_ON_BY_DEFAULT = True

SETTING_ORDER = ("answer_format", "temperature", "thinking_off")
LABELS = {"answer_format": "Ask for a fixed answer format", "temperature": "Keep answers steady", "thinking_off": "Thinking off"}
NOT_TESTED = "Not tested on this model"
SKYRIMNET_CHOICE = "SkyrimNet's choice"

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_presets.json")
_cache: dict = {}


def presets() -> dict:
    if "p" not in _cache:
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                _cache["p"] = json.load(f)
        except (OSError, ValueError):
            _cache["p"] = {"engines": {}, "families": {}, "jobs": {}}
    return _cache["p"]


def job_kind(keys) -> Optional[str]:
    """The preset job (`action`, `memory`, `dialogue`) a request's possible routing jobs belong to, or None."""
    for kind, j in presets().get("jobs", {}).items():
        if any(k in j.get("routing_keys", ()) for k in keys):
            return kind
    return None


def family_of(model_id: str) -> Optional[str]:
    low = (model_id or "").lower()
    for fid, fam in presets().get("families", {}).items():
        if any(m in low for m in fam.get("match", ())):
            return fid
    return None


def engine_of(line_key: Optional[str], program: Optional[str] = None) -> Optional[str]:
    """`builtin` (the llama.cpp MultiProxy started), `llamacpp`, `ollama`, `lmstudio`, or None (a cloud model, another MultiProxy, anything not a local engine)."""
    if line_key == "local":
        return "builtin"
    if line_key in ("ollama", "lmstudio"):
        return line_key
    if line_key == "remote":
        p = (program or "").replace(" ", "").replace(".", "").lower()
        return p if p in ("llamacpp", "ollama", "lmstudio") else None
    return None


def _tested_entry(kind: str, setting: str, family: Optional[str], engine: Optional[str]) -> Optional[dict]:
    if not family or not engine:
        return None
    key = "llamacpp" if engine == "builtin" else engine
    return (((presets().get("jobs", {}).get(kind, {}).get("settings", {}).get(setting) or {}).get(family) or {}).get(key))


def evidence(entry: dict) -> str:
    s = f"{entry['result']} of {entry['of']}"
    if "without" in entry:
        s += f" (was {entry['without']} of {entry['of']})"
    return s


def tested_on(kind: str, family: Optional[str], engine: Optional[str]) -> str:
    """One line saying exactly what was tested: the model, the engine, its build and the date."""
    key = "llamacpp" if engine == "builtin" else engine
    eng = presets().get("engines", {}).get(key or "", {})
    fam = presets().get("families", {}).get(family or "", {})
    if not eng or not fam:
        return ""
    return f"Tested on {fam['tested_model']} with {eng['name']} {eng['build']} on {eng['date']}."


def switches_for(settings: dict, keys) -> dict:
    """The saved switches for a request's job: the first of the jobs it could be that has any."""
    for k in keys:
        s = (settings.get("tested") or {}).get(k)
        if s:
            return dict(s)
    return {}


def clean_switches(raw: Any) -> dict:
    out: dict = {}
    for job, per in (raw.items() if isinstance(raw, dict) else []):
        if not isinstance(job, str) or not re.match(r"^[A-Za-z0-9_.\-]{1,64}$", job) or not isinstance(per, dict):
            continue
        kept = {s: bool(v) for s, v in per.items() if s in SETTING_ORDER and isinstance(v, bool)}
        if kept:
            out[job] = kept
    return out


def _available(kind: str, setting: str, engine: Optional[str]) -> bool:
    """Whether the setting can be applied at all on this engine (a grammar is llama.cpp's own; a fixed format is not offered for LM Studio)."""
    if setting == "answer_format":
        return engine in (("builtin", "llamacpp") if kind == "action" else ("builtin", "llamacpp", "ollama"))
    return engine is not None


def card(job_key: str, model_id: str, line_key: Optional[str], program: Optional[str], saved: dict, last_sent: Optional[dict] = None) -> Optional[dict]:
    """What the "Settings for this model" section of a job's card shows, or None when the job has no tested settings or the model is not a local one."""
    kind = job_kind((job_key,))
    engine = engine_of(line_key, program)
    if kind is None or engine is None:
        return None
    family = family_of(model_id)
    built_in = engine == "builtin"
    rows = []
    for setting in SETTING_ORDER:
        if setting not in (presets()["jobs"][kind].get("settings") or {}):
            continue                                         # this job has no row for it at all
        entry = _tested_entry(kind, setting, family, engine)
        if entry is not None and not _available(kind, setting, engine):
            entry = None
        if entry is None and setting == "temperature":
            continue                                         # a steady temperature is only offered where the research found it helps
        tested = entry is not None
        on = bool(saved.get(setting)) if setting in saved else (BUILT_IN_ON_BY_DEFAULT and built_in and tested)
        on = on and tested
        sent = (last_sent or {}).get("thinking")
        if setting == "thinking_off":
            recommended = "On"
            now = "Always (built in)" if built_in else ("On" if on else ("SkyrimNet turns it off" if sent == "off" else SKYRIMNET_CHOICE))
        elif setting == "temperature":
            recommended = "On" if tested else ""
            now = f"{entry['value']}" if (tested and on) else "The model's own"
        else:
            recommended = "On"
            now = "On" if on else SKYRIMNET_CHOICE
        if setting == "temperature" and tested:
            label = f"{LABELS[setting]} (temperature {entry['value']})"
        else:
            label = LABELS[setting]
        rows.append({"id": setting, "label": label, "recommended": recommended if tested else NOT_TESTED, "tested": tested, "on": on, "now": now,
                     "evidence": evidence(entry) if tested else "", "builtInAlways": setting == "thinking_off" and built_in})
    if not rows:
        return None
    fam = presets().get("families", {}).get(family or "", {})
    return {"job": job_key, "kind": kind, "jobName": presets()["jobs"][kind]["name"], "family": fam.get("name"), "testedOn": tested_on(kind, family, engine) if any(r["tested"] for r in rows) else "",
            "rows": rows, "anyTested": any(r["tested"] for r in rows), "allOn": all(r["on"] for r in rows if r["tested"] and not r["builtInAlways"]) and any(r["tested"] and not r["builtInAlways"] for r in rows),
            "anyOn": any(r["on"] and not r["builtInAlways"] for r in rows)}


# ---- the two prompts the answer format is built for ----------------------------------------------------------------------------------------------------------------

MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"}, "location": {"type": "string"},
        "emotion": {"type": "string", "enum": ["joyful", "content", "neutral", "concerned", "fearful", "angry", "disgusted", "surprised", "sad", "traumatized"]},
        "importance_score": {"type": "number", "minimum": 0, "maximum": 1},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
        "type": {"type": "string", "enum": ["EXPERIENCE", "RELATIONSHIP", "KNOWLEDGE", "LOCATION", "SKILL", "TRAUMA", "JOY"]},
    },
    "required": ["content", "location", "emotion", "importance_score", "tags", "type"], "additionalProperties": False,
}
MEMORY_FORMAT = {"type": "json_schema", "json_schema": {"name": "memory", "strict": True, "schema": MEMORY_SCHEMA}}


def looks_like(kind: str, text: str) -> bool:
    """Whether the prompt has the shape the research tested (2026-09-29 captures). A prompt that does not gets nothing added."""
    t = text or ""
    if kind == "action":
        return "ACTION:" in t and "## Available Actions" in t and "exactly one line" in t.lower()
    if kind == "memory":
        return "Respond ONLY with a JSON object" in t and all(f'"{f}"' in t for f in ("content", "location", "emotion", "importance_score", "tags", "type"))
    return kind == "dialogue"


_ACTION_LINE = re.compile(r"^- `([A-Za-z0-9_]+)`(?: PARAMS: (\{.*?\}))?(?= —| -|$)", re.M)


def _lit(s: str) -> Optional[str]:
    if '"' in s or "\\" in s or "\n" in s:
        return None
    return '"' + s + '"'


def action_grammar(prompt_text: str) -> Optional[str]:
    """A llama.cpp grammar for the one-line `ACTION: Name PARAMS: {...}` answer, built from the request's own `## Available Actions` list; None when the list cannot be read exactly
    (an unknown kind of parameter, a name with a quote in it): then nothing is added and the request goes as it is."""
    head, sep, tail = (prompt_text or "").partition("## Available Actions")
    if not sep:
        return None
    tail = re.split(r"\n## ", tail, maxsplit=1)[0]
    rules, alts, names = [], [], []
    for m in _ACTION_LINE.finditer(tail):
        name, params = m.group(1), m.group(2)
        if name in names:
            return None
        names.append(name)
        if not params:
            alts.append(_lit(name))
            continue
        try:
            obj = json.loads(params)
        except ValueError:
            return None
        if not isinstance(obj, dict) or not obj:
            return None
        parts = []
        for i, (k, v) in enumerate(obj.items()):
            if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_]+", k):
                return None
            rid = f"v{len(names)}x{i}"
            if v == "string":
                rules.append(f'{rid} ::= [a-zA-Z0-9 ,.\'!?-]+')
            else:
                opts = [_lit(o) for o in v.split("|")]
                if not all(opts) or not v:
                    return None
                rules.append(f"{rid} ::= " + " | ".join(opts))
            parts.append(f'"\\"{k}\\":\\"" {rid} "\\""')
        alts.append(_lit(name + " PARAMS: ") + ' "{" ' + ' "," '.join(parts) + ' "}"')
    if not alts:
        return None
    if "None" not in names:
        alts.append('"None"')
    lines = ["root ::= \"ACTION: \" (" + " | ".join(f"({a})" for a in alts) + ")"] + rules
    return "\n".join(lines) + "\n"


def reply_ok(kind: str, text: str) -> bool:
    """A reply good enough to keep when a fixed answer format was asked for; anything else is asked again once without it."""
    t = (text or "").strip()
    if not t:
        return False
    if kind == "action":
        return bool(re.search(r"^\s*ACTION:", t, re.M))
    if kind == "memory":
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return False
        try:
            return isinstance(json.loads(m.group(0)), dict)
        except ValueError:
            return False
    return True


FORMAT_KEYS = ("response_format", "grammar", "json_schema")


def apply(kind: str, family: Optional[str], engine: Optional[str], switches: dict, built_in: bool, extra: dict, *, prompt_text: str, streamed: bool,
          own: dict) -> tuple:
    """(the request's extra fields with the tested settings in, the same without the fixed answer format or None when none was added, the names applied).
    `own` is what the person set in MultiProxy for this job (fine-tune overrides): it always wins."""
    out = dict(extra)
    applied: list = []
    if kind is None or engine is None or not looks_like(kind, prompt_text):
        return extra, None, []

    def on(setting: str) -> bool:
        entry = _tested_entry(kind, setting, family, engine)
        if entry is None:
            return False
        return bool(switches[setting]) if setting in switches else (BUILT_IN_ON_BY_DEFAULT and built_in)

    if on("thinking_off") and engine != "builtin" and "thinking" not in own and not any(k in out for k in ("reasoning", "reasoning_effort", "thinking")):
        out["reasoning"] = {"enabled": False}                    # said SkyrimNet's way: thinking_off.py turns it into what this engine reads
        applied.append("thinking_off")
    if on("temperature") and "temperature" not in own and "temperature" not in out:
        out["temperature"] = _tested_entry(kind, "temperature", family, engine)["value"]
        applied.append("temperature")
    added_format = False
    if on("answer_format") and not streamed and _available(kind, "answer_format", engine) and not any(k in out for k in FORMAT_KEYS):
        if kind == "memory":
            out["response_format"] = MEMORY_FORMAT
            added_format = True
        elif kind == "action":
            g = action_grammar(prompt_text)
            if g:
                out["grammar"] = g
                added_format = True
        if added_format:
            applied.append("answer_format")
    if not applied:
        return extra, None, []
    plain = {k: v for k, v in out.items() if not (added_format and k in FORMAT_KEYS and k not in extra)} if added_format else None
    return out, plain, applied
