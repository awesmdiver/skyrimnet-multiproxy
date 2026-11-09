"""Benchmarks: the automated checks on what a model wrote (contract section 10). Yes or no only: nothing is scored, ranked or weighed.

Five checks, each with a disclosed rule. A prompt of the fixed set (`benchmarks_testset`) names the checks that apply to it:
- **tags** (Keeps SkyrimNet's tags intact): every required marker of the prompt is in the answer as asked. A tag like `<internal_thought>` must appear as exactly one block: one opening and
  one closing tag, the open first, written exactly (no stray spaces, no square brackets instead of angle brackets), with something inside it, and spoken text after it. A voice tag like
  `[sighs]` must appear exactly once, written exactly.
- **format** (Answers in the format asked): the answer has the shape the prompt asked for. `json`: the whole answer, trimmed, parses as JSON (no code fence, no text around it), is an object
  whose keys are exactly the asked keys (no others), and each value is a non-empty string. `labelled`: one line per asked label, in order, written `Label: text`. `numbered`: exactly the asked
  number of non-empty lines, written `1. ...`, `2. ...` (a full stop, not a bracket), each with text after the number, nothing else.
- **language** (Stays in the language): a simple word-list rule. The answer's words are matched against about 30 very common words of each of English, Spanish, French and German (`STOPWORDS`);
  the asked language passes when it has the highest share and that share is at least `LANGUAGE_MIN_SHARE` (8%) of the words, and the answer has at least `LANGUAGE_MIN_WORDS` (5) words. Limits: a short answer, names, a mix of languages or a
  language without a list are not judged well (a prompt only asks for this check in one of those four languages), and it says nothing about grammar or quality.
- **length** (Stops at the asked length): the engine said the answer ended by itself (`finish_reason` is `stop`), not because it hit the prompt's output limit (`length`). Applies to every
  prompt. (The engine also reports `stop` for a configured stop word; this test set sets none.)
- **choice** (Gives a valid choice): the whole answer, trimmed, is exactly one of the options offered: capital letters and a final full stop are ignored, nothing else may be there (no second line).
Each check keeps the failing request and the line the model wrote.
"""
import json
import re
from typing import Optional

import benchmarks_contract as bc

TITLES = {"tags": "Keeps SkyrimNet's tags intact", "format": "Answers in the format asked", "language": "Stays in the language", "length": "Stops at the asked length",
          "choice": "Gives a valid choice (decision question)"}
ORDER = ("tags", "format", "language", "length", "choice")
LINE_CHARS = 240                                              # how much of the model's answer a failure keeps

LANGUAGE_MIN_WORDS = 5                                        # fewer words than this cannot be judged, so the check fails
LANGUAGE_MIN_SHARE = 0.08                                     # the asked language must hold at least this share of the words, and the biggest share
MIN_WORDS, MIN_SHARE = LANGUAGE_MIN_WORDS, LANGUAGE_MIN_SHARE
STOPWORDS = {
    "en": "the and of to a in is that it you i for with on be are this have not as at but we they your was my me what from or an there will can all".split(),
    "es": "el la de que y en los las un una es por con no para se lo su al como más pero sus le ya o este sí porque esta entre cuando muy sin sobre también me hasta".split(),
    "fr": "le la les de des du et un une est que qui dans pour pas sur au avec ce il elle je vous nous ne se en plus par mais ou son sa ses été très".split(),
    "de": "der die das und ist nicht ein eine zu den dem mit sich auf für von im es ich du sie wir aber auch wie als oder nur noch bei nach so wenn".split(),
}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def line_of(text: str) -> str:
    return " ".join((text or "").split())[:LINE_CHARS]


# --- the checks: each returns (passed, why) ---
def check_tags(required: list, answer: str) -> tuple:
    for tag in required:
        if tag.startswith("<") and tag.endswith(">") and not tag.startswith("</"):
            name = tag[1:-1]
            op, cl = f"<{name}>", f"</{name}>"
            if answer.count(op) != 1 or answer.count(cl) != 1:
                return False, f"{op} and {cl} must each appear exactly once"
            if answer.index(op) > answer.index(cl):
                return False, f"{cl} comes before {op}"
            inside = answer[answer.index(op) + len(op):answer.index(cl)]
            if not inside.strip():
                return False, f"{op} is empty"
            if not answer[answer.index(cl) + len(cl):].strip():
                return False, f"nothing is said after {cl}"
        elif answer.count(tag) != 1:
            return False, f"{tag} is missing" if tag not in answer else f"{tag} must appear exactly once"
    return True, ""


def check_format(spec: dict, answer: str) -> tuple:
    text = answer.strip()
    kind = spec.get("kind")
    if kind == "json":
        try:
            obj = json.loads(text)
        except ValueError:
            return False, "the answer is not valid JSON"
        if not isinstance(obj, dict):
            return False, "the JSON is not an object"
        keys = list(spec.get("keys", []))
        missing = [k for k in keys if k not in obj]
        if missing:
            return False, "missing key " + ", ".join(missing)
        extra = [k for k in obj if k not in keys]
        if extra:
            return False, "extra key " + ", ".join(str(k) for k in extra)
        bad = [k for k in keys if not isinstance(obj[k], str) or not obj[k].strip()]
        return (False, "not a non-empty text: " + ", ".join(bad)) if bad else (True, "")
    lines = [l for l in text.splitlines() if l.strip()]
    if kind == "labelled":
        labels = spec.get("labels", [])
        if len(lines) != len(labels):
            return False, f"{len(lines)} lines, {len(labels)} asked"
        for line, label in zip(lines, labels):
            if not re.match(rf"^\s*{re.escape(label)}\s*:\s*\S", line):
                return False, f"a line does not start with '{label}:'"
        return True, ""
    if kind == "numbered":
        n = int(spec.get("count", 0))
        if len(lines) != n:
            return False, f"{len(lines)} lines, {n} asked"
        for i, line in enumerate(lines, 1):
            if not re.match(rf"^\s*{i}\.\s+\S", line):
                return False, f"line {i} is not numbered '{i}.' with text after it"
        return True, ""
    return False, "unknown format"


def language_scores(text: str) -> dict:
    words = [w.lower() for w in _WORD.findall(text or "")]
    if not words:
        return {}
    return {lang: sum(1 for w in words if w in set(sw)) / len(words) for lang, sw in STOPWORDS.items()} | {"_words": len(words)}


def check_language(expected: str, answer: str) -> tuple:
    if expected not in STOPWORDS:
        return True, ""                                          # a language without a list is not judged
    sc = language_scores(answer)
    if not sc or sc["_words"] < MIN_WORDS:
        return False, "the answer is too short to tell the language"
    best = max((l for l in STOPWORDS), key=lambda l: sc[l])
    if best != expected or sc[expected] < MIN_SHARE:
        return False, f"reads as {best} ({sc[best]:.0%}), {expected} asked"
    return True, ""


def check_length(finish_reason: str) -> tuple:
    return (True, "") if finish_reason == "stop" else (False, "it ran into the output limit" if finish_reason == "length" else "it did not end by itself")


def _norm_choice(s: str) -> str:
    text = s.strip().lower()
    return text[:-1].rstrip() if text.endswith(".") else text


def check_choice(options: list, answer: str) -> tuple:
    opts = {_norm_choice(o) for o in options}
    text = answer.strip()
    if "\n" in text:
        return False, "the answer has more than one line"
    if _norm_choice(text) in opts:
        return True, ""
    return False, "the answer is not exactly one of the options offered"


def evaluate(prompt: dict, answer: str, finish_reason: str) -> dict:
    """`{check id: (passed, why)}` for the checks that apply to this prompt (length applies to every prompt)."""
    spec = prompt.get("checks") or {}
    out = {}
    if "tags" in spec:
        out["tags"] = check_tags(spec["tags"], answer)
    if "format" in spec:
        out["format"] = check_format(spec["format"], answer)
    if "language" in spec:
        out["language"] = check_language(spec["language"], answer)
    out["length"] = check_length(finish_reason)
    if "choice" in spec:
        out["choice"] = check_choice(spec["choice"], answer)
    return out


def summarize(rows: list, prompts_by_id: dict) -> list:
    """`checks[]` for the summary file from the request rows of the measured requests that finished: each check's `passed` and `total` over the prompts that asked for it, and every failure
    with its request id and the line the model wrote. A request that errored or timed out has no answer to check and is left out (it is counted as failed or timed out elsewhere)."""
    tally = {c: {"passed": 0, "total": 0, "failures": []} for c in ORDER}
    for r in rows:
        if r.get("warmup") or r.get("sustained") or r.get("status") != "ok":
            continue
        prompt = prompts_by_id.get(r.get("prompt_id"))
        if not prompt:
            continue
        for cid, (ok, why) in evaluate(prompt, r.get("answer") or "", r.get("finish_reason") or "").items():
            t = tally[cid]
            t["total"] += 1
            if ok:
                t["passed"] += 1
            else:
                t["failures"].append({"request_id": r.get("request_id"), "prompt_id": r.get("prompt_id"), "why": why, "line": line_of(r.get("answer") or "")})
    return [bc.new_check(cid, TITLES[cid], t["passed"], t["total"], t["failures"]) for cid, t in tally.items() if t["total"]]
