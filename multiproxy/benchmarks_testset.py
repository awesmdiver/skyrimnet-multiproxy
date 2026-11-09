"""Benchmarks: the fixed Chat test set (`benchmarks/testset-v1.json`) and how the runner uses it.

Version 1 is fixed and public, so two people's numbers can be compared: it is never randomised, and once published it is never edited in place (a change is then `v2`, a new file; it was edited freely before its first release). The file carries its own
`sha256` (of everything else in it, in a canonical form); `load()` refuses a file whose hash does not match, and every result stores the version and the hash. The prompts are invented
(names, places and events are made up; nothing is quoted from a game or a player) and have the SHAPE of SkyrimNet's chat requests: a system prompt, a character sheet, memories, recent
events, a scene and several turns, with the tags SkyrimNet uses. Their three lengths come from the real size distribution of the author's own SkyrimNet traffic (`lengths_from_sizes`).
`scripts/build_benchmark_testset.py` writes the file; this module only reads it.
"""
import hashlib
import json
import math
import os
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
TESTSET_PATH = os.path.join(_HERE, "benchmarks", "testset-v1.json")
if not os.path.isfile(TESTSET_PATH):                                               # (a Courier package is flat, and an update to an older Courier may only carry .py .txt .png .ico files: the same JSON sits at the top level as .txt)
    TESTSET_PATH = os.path.join(_HERE, "benchmarks-testset-v1.txt")
CLASSES = ("short", "medium", "long")
CATEGORIES = ("dialogue", "roleplay", "structured", "instruction", "edge", "choice")
CHECK_KEYS = ("tags", "format", "language", "choice")           # which checks a prompt asks for (the length check applies to every prompt)
SUSTAINED_PROMPT_ID = "dialogue-medium"                          # the medium prompt the 5-minute run repeats
REPLY_ROOM = 256                                                 # tokens kept free per slot beyond the prompt and its output limit
TOKENS_PER_CHAR = 0.27                                           # a safe over-estimate when only characters are known (English text is about 0.25 tokens a character)


class TestSetError(ValueError):
    pass


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_of(data: dict) -> str:
    """sha256 of the file's content without its own `sha256` field."""
    body = {k: v for k, v in data.items() if k != "sha256"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def validate(data: dict) -> None:
    """Every prompt has its stable id, length class, category, messages, an output limit and (for the checks it asks for) their specs. Raises `TestSetError`."""
    if not isinstance(data, dict) or data.get("version") != "v1" or not isinstance(data.get("prompts"), list) or not data["prompts"]:
        raise TestSetError("not a version 1 test set")
    if data.get("sha256") != hash_of(data):
        raise TestSetError("the test set does not match its own hash")
    seen = set()
    for p in data["prompts"]:
        pid = p.get("id")
        if not isinstance(pid, str) or not pid or pid in seen:
            raise TestSetError(f"prompt id missing or repeated: {pid!r}")
        seen.add(pid)
        if p.get("length") not in CLASSES or p.get("category") not in CATEGORIES:
            raise TestSetError(f"{pid}: unknown length or category")
        msgs = p.get("messages")
        if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) and m.get("role") in ("system", "user", "assistant") and isinstance(m.get("content"), str) and m["content"] for m in msgs):
            raise TestSetError(f"{pid}: messages")
        if not isinstance(p.get("max_tokens"), int) or not 16 <= p["max_tokens"] <= 1024:
            raise TestSetError(f"{pid}: max_tokens")
        checks = p.get("checks") or {}
        if set(checks) - set(CHECK_KEYS):
            raise TestSetError(f"{pid}: unknown check")
        if "tags" in checks and not (isinstance(checks["tags"], list) and checks["tags"]):
            raise TestSetError(f"{pid}: tags")
        if "format" in checks and checks["format"].get("kind") not in ("json", "labelled", "numbered"):
            raise TestSetError(f"{pid}: format")
        if "choice" in checks and not (isinstance(checks["choice"], list) and len(checks["choice"]) >= 2):
            raise TestSetError(f"{pid}: choice")
    if SUSTAINED_PROMPT_ID not in seen:
        raise TestSetError("the sustained-run prompt is missing")


_cache: dict = {}


def load(path: str = TESTSET_PATH) -> dict:
    """The test set, checked against its own hash (read once)."""
    if path not in _cache:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        validate(data)
        _cache[path] = data
    return _cache[path]


def version_info(data: dict) -> dict:
    return {"test_set_version": data["version"], "test_set_hash": data["sha256"]}


def prompts_for(data: dict, classes) -> list:
    """The prompts of the ticked lengths, in the file's own fixed order (short, medium, long within each category)."""
    want = set(classes)
    return [p for p in data["prompts"] if p["length"] in want]


def sustained_prompt(data: dict) -> dict:
    return next(p for p in data["prompts"] if p["id"] == SUSTAINED_PROMPT_ID)


def approx_prompt_tokens(prompt: dict) -> int:
    """The file's own estimate of a prompt's size (`approx_tokens`, counted with a real tokenizer when the file was built), else a safe over-estimate from the characters."""
    if isinstance(prompt.get("approx_tokens"), int):
        return prompt["approx_tokens"]
    return int(sum(len(m["content"]) for m in prompt["messages"]) * TOKENS_PER_CHAR) + 1


def tokens_per_slot(prompts: list, margin: float = 1.1) -> int:
    """What one slot of the engine needs to hold the biggest of these prompts and its reply, rounded up to 1,024 (so the engine's context is slots x this)."""
    need = max((approx_prompt_tokens(p) * margin + p["max_tokens"] + REPLY_ROOM for p in prompts), default=4096)
    return int(math.ceil(need / 1024.0) * 1024)


def percentile(values, p) -> float:
    """Nearest rank (the same method as the results)."""
    data = sorted(values)
    if not data:
        raise ValueError("no values")
    return data[min(max(math.ceil(p / 100 * len(data)), 1), len(data)) - 1]


def lengths_from_sizes(sizes, round_to: int = 100) -> dict:
    """The three prompt lengths from a sample of real request sizes (tokens): short = the 25th percentile, medium = the median, long = the 90th percentile, each rounded to
    `round_to`. Requests with no size are left out, never counted as zero."""
    vals = [int(s) for s in sizes if s and s > 0]
    if not vals:
        raise ValueError("no sizes")
    r = lambda v: int(round(v / round_to) * round_to)
    return {"short": r(percentile(vals, 25)), "medium": r(percentile(vals, 50)), "long": r(percentile(vals, 90)),
            "distribution": {"n": len(vals), "median": percentile(vals, 50), "p90": percentile(vals, 90), "largest": max(vals)}}
