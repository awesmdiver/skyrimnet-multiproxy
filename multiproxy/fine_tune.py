"""Fine-tune a job (Routing step 4, 2026-10-02): what SkyrimNet last sent for a job, and optional per-job overrides MultiProxy applies before the request goes to whichever
model answers. Nothing from MultiProxy is imported here.

- **What is remembered** (`sent_settings`, `LastSent`): the settings of the last real request for each job: temperature and the other sampler settings, longest reply,
  thinking, and any other plain number, switch or short word SkyrimNet sends along. **Never message text**, and nothing that isn't a plain setting.
- **Overrides** (`routing.overrides` in the settings): per job, per setting, one value. They are applied to a request as it goes to the model that answers it
  (`apply`), translated the way that kind of model needs, and a setting a model can't take is skipped (it is never sent, and never an error in game).
"""
import json
import os
import re
import threading
from typing import Any, Callable, Optional

# name, plain name, kind, lowest, highest. "thinking" is on/off. max_tokens is the Longest reply.
FIELDS = (
    ("temperature", "Temperature", "number", 0.0, 2.0),
    ("max_tokens", "Longest reply", "int", 1, 200000),
    ("thinking", "Thinking", "onoff", None, None),
    ("top_p", "Variety", "number", 0.0, 1.0),
    ("top_k", "Word choices", "int", 0, 1000),
    ("frequency_penalty", "Repeating words", "number", -2.0, 2.0),
    ("presence_penalty", "Repeating topics", "number", -2.0, 2.0),
)
FIELD_NAMES = tuple(f[0] for f in FIELDS)
_SPEC = {f[0]: f for f in FIELDS}
_WORD = re.compile(r"^[A-Za-z0-9_.\-]{1,24}$")
SKIP_KEYS = frozenset({"model", "messages", "stream", "stream_options", "tools", "tool_choice", "response_format", "metadata", "user", "state", "questions", "multiproxy", "extra_body",
                       "provider", "route", "cache", "usage", "transforms", "plugins", "models", "logit_bias", "functions", "function_call", "prediction", "input", "prompt"})


def plain_name(name: str) -> str:
    return _SPEC[name][1] if name in _SPEC else name


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v == v and abs(v) < 1e12:
        return float(v)
    return None


def clean_value(name: str, v: Any) -> Any:
    """The value kept for an override, or None when it isn't a sensible one for that setting."""
    spec = _SPEC.get(name)
    if not spec:
        return None
    kind, lo, hi = spec[2], spec[3], spec[4]
    if kind == "onoff":
        s = str(v).strip().lower() if not isinstance(v, bool) else ("on" if v else "off")
        return s if s in ("on", "off") else None
    n = _num(v) if not isinstance(v, str) else _num(_to_float(v))
    if n is None or n < lo or n > hi:
        return None
    return int(round(n)) if kind == "int" else round(n, 4)


def _to_float(s: str) -> Any:
    try:
        return float(s.strip())
    except ValueError:
        return None


def clean_overrides(raw: Any) -> dict:
    """{job key: {setting: value}} with only known settings and sensible values; a job with none left is dropped."""
    out: dict = {}
    for job, per in (raw.items() if isinstance(raw, dict) else []):
        if not isinstance(job, str) or not re.match(r"^[A-Za-z0-9_.\-]{1,64}$", job) or not isinstance(per, dict):
            continue
        kept = {n: clean_value(n, v) for n, v in per.items() if n in _SPEC}
        kept = {n: v for n, v in kept.items() if v is not None}
        if kept:
            out[job] = kept
    return out


def overrides_for(settings: dict, keys) -> dict:
    """The overrides for a request's job: the first of the jobs it could be (jobs that can't be told apart share them) that has any."""
    for k in keys:
        o = (settings.get("overrides") or {}).get(k)
        if o:
            return dict(o)
    return {}


# ---- what SkyrimNet sent ----------------------------------------------------------------------------------------------------------------------------------

def sent_settings(*, max_tokens: Any = None, temperature: Any = None, extra: Optional[dict] = None) -> dict:
    """The plain settings of one request: numbers, switches and short words only. Thinking is read from `reasoning: {enabled}` (or `thinking: {type}`) as on/off."""
    out: dict = {}
    if _num(max_tokens) is not None:
        out["max_tokens"] = int(_num(max_tokens))
    if _num(temperature) is not None:
        out["temperature"] = _num(temperature)
    for k, v in (extra or {}).items():
        if k in SKIP_KEYS or not isinstance(k, str) or not re.match(r"^[A-Za-z0-9_]{1,40}$", k):
            continue
        if k == "reasoning" and isinstance(v, dict) and isinstance(v.get("enabled"), bool):
            out["thinking"] = "on" if v["enabled"] else "off"
        elif k == "thinking" and isinstance(v, dict) and v.get("type") in ("enabled", "disabled"):
            out["thinking"] = "on" if v["type"] == "enabled" else "off"
        elif isinstance(v, bool):
            out[k] = v
        elif _num(v) is not None:
            n = _num(v)
            out[k] = int(n) if n == int(n) and abs(n) < 1e9 else round(n, 6)
        elif isinstance(v, str) and _WORD.match(v):
            out[k] = v
    return out


class LastSent:
    """The settings of the last real request for each job, kept in memory and saved to a small file (changes only) so they survive a restart."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._lock = threading.Lock()
        self._by: dict = {}
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                if isinstance(doc, dict):
                    self._by = {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, dict)}
            except (OSError, ValueError):
                pass

    def note(self, job_keys, sent: dict) -> None:
        if not sent:
            return
        changed = False
        with self._lock:
            for k in job_keys:
                if self._by.get(k) != sent:
                    self._by[k] = dict(sent)
                    changed = True
            snapshot = json.dumps(self._by, indent=1, sort_keys=True) if changed and self.path else None
        if snapshot:
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(snapshot)
                os.replace(tmp, self.path)
            except OSError:
                pass                                         # remembering is a convenience: never a reason to fail a request

    def get(self, job_key: str) -> dict:
        with self._lock:
            return dict(self._by.get(job_key, {}))


# ---- applying them ----------------------------------------------------------------------------------------------------------------------------------------

def apply(overrides: dict, accepts: Callable[[str], bool], max_tokens: int, extra: dict, thinking_for: Callable[[str], Optional[dict]]) -> tuple:
    """(max_tokens, extra with the overrides in, applied [names], skipped [names]) for one request going to one kind of model. `accepts(name)` says whether that kind of
    model can take the setting; `thinking_for("on"|"off")` gives the field a model uses for the thinking switch (or None). A setting the model can't take is skipped,
    never sent. Nothing is changed when there are no overrides."""
    applied, skipped = [], []
    out = dict(extra)
    for name in FIELD_NAMES:
        if name not in overrides:
            continue
        v = overrides[name]
        if not accepts(name):
            skipped.append(name)
            continue
        if name == "max_tokens":
            max_tokens = int(v)
        elif name == "thinking":
            field = thinking_for(v)
            if field is None:
                skipped.append(name)
                continue
            out.update(field)
        else:
            out[name] = v
        applied.append(name)
    return max_tokens, out, applied, skipped
