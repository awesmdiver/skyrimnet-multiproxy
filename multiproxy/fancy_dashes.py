"""Long dashes in NPC lines become a plain hyphen (4.2.1).

Skyrim's subtitle font has no glyph for the em dash (U+2014), so the game draws an empty square where the AI wrote one. Replaced, and only these four (the others a
reply may hold, like curly quotes, the ellipsis and accented letters, do draw): em dash U+2014, en dash U+2013, horizontal bar U+2015, true minus U+2212 -> "-".
Spaces around a dash are kept as written. Every function is stateless, so a streamed reply is fixed chunk by chunk (each dash is one character and cannot straddle a chunk).

Text is fixed as text; a JSON reply is fixed inside its string VALUES only (never a key); a bracket tag such as [emotion-amusement] or <internal_thought> that sits whole in
the text is left exactly as it is.
"""
import json
import re
from typing import Optional

DASHES = "—–―−"
_ESCAPED = re.compile(r"(?<!\\)((?:\\\\)*)\\u(?:2014|2013|2015|2212)", re.I)          # an even run of backslashes, then —: a real escape ("\\u2014" is the text — and stays)
_STRING_EDGE = re.compile(r'["\\\n]')
_TEXT = re.compile(r"(\[[^\[\]\n]{1,64}\]|<[^<>\n]{1,64}>)|[" + DASHES + "]")
_HAS_DASH = re.compile("[" + DASHES + r"]|\\u(?:2014|2013|2015|2212)", re.I)


def has_dash(text) -> bool:
    return bool(text) and bool(_HAS_DASH.search(text))


def fix_text(text: str) -> tuple:
    """(text, how many were replaced). Text with none comes back as the very same string."""
    if not text or not any(c in text for c in DASHES):
        return text, 0
    n = 0

    def one(m):
        nonlocal n
        if m.group(1) is not None:          # a whole tag: left alone
            return m.group(1)
        n += 1
        return "-"
    out = _TEXT.sub(one, text)
    return (out, n) if n else (text, 0)


def fix_json(text: str) -> tuple:
    """(text, how many). Only the inside of a string that is a VALUE changes (a string followed by a colon is a key); the layout of the JSON is untouched."""
    if not text or not _HAS_DASH.search(text):
        return text, 0
    n = 0
    out, last = [], 0
    for start, end in _strings(text):
        rest = text[end:end + 40].lstrip()
        if rest.startswith(":"):
            continue
        s = text[start:end]
        s2, k = fix_text_inside_string(s)
        if k:
            out.append(text[last:start])
            out.append(s2)
            last = end
            n += k
    out.append(text[last:])
    return ("".join(out), n) if n else (text, 0)


def _strings(text: str):
    """(start, end) of every complete JSON string in the text, in ONE pass: a quote opens a string, a backslash skips the next character, the next plain quote closes it.
    A string that meets a line break or the end of the text is not a string, so no stretch of text is ever scanned twice."""
    i = 0
    while True:
        i = text.find('"', i)
        if i < 0:
            return
        j = i + 1
        while True:
            m = _STRING_EDGE.search(text, j)
            if m is None:
                return
            c = m.group(0)
            if c == '"':
                yield i, m.end()
                i = m.end()
                break
            if c == "\\":
                j = m.end() + 1
                continue
            i = m.end()          # a line break: that was not a string; look for the next opening quote after it
            break


def fix_text_inside_string(s: str) -> tuple:
    k = sum(s.count(c) for c in DASHES)
    s = s.translate({ord(c): "-" for c in DASHES})
    s, e = _ESCAPED.subn(lambda m: m.group(1) + "-", s)
    return s, k + e


def fix_event(event: str) -> tuple:
    """One streamed server-sent event: (event, how many). Only the text of the reply (choices[].delta.content) changes."""
    if not event or not _HAS_DASH.search(event):
        return event, 0
    stripped = event.strip()
    if not stripped.startswith("data: ") or "\n" in stripped:
        return event, 0
    try:
        obj = json.loads(stripped[6:])
    except ValueError:
        return event, 0
    total = 0
    for ch in (obj.get("choices") or []) if isinstance(obj, dict) else []:
        delta = ch.get("delta") if isinstance(ch, dict) else None
        content = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(content, str):
            fixed, k = fix_text(content)
            if k:
                delta["content"] = fixed
                total += k
    if not total:
        return event, 0
    return f"data: {json.dumps(obj)}\n\n", total


def fix_reply(text: str, as_json: bool) -> tuple:
    return fix_json(text) if as_json else fix_text(text)
