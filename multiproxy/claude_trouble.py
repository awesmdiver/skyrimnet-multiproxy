"""The one line under the Claude card when its dot is orange: what is wrong and the one thing to do (2026-10-25, issue #4).

The reason comes from `auth.last_error`, which is either a line MultiProxy writes itself or the first line the Claude program printed when its check failed
(`_extract_claude_failure_reason` in multiproxy.py). Only reasons whose text is in this codebase are named here; any other reason gets no line (never a guess).
The words are drafts: they live in LINES so the design side's Gemini pass is a one-spot swap. Nothing is shown while the dot is green or grey.
"""
import json
import os
import re
import subprocess
from typing import Optional

# The oldest Claude Code MultiProxy works with: 2.0.63 is the first release that has `--no-session-persistence`, which the sign-in check (`capture_auth`) passes.
# Every other option of that call (`--print`, `--output-format`, `--model`, `--system-prompt`) is older. Measured by running --help of the published releases: 2.0.62 lacks that flag, 2.0.63 has it.
MIN_VERSION = (2, 0, 63)
VERSION_TIMEOUT = 5

LINES = {
    "old": "Claude Code is out of date. Update it, then sign in again.",
    "timeout": "Claude didn't answer in time. Restart MultiProxy to try again.",
    "signin": "Sign in to Claude Code again.",
    "disabled": "Your Claude plan blocks Claude Code. Ask whoever manages the plan, or choose another provider.",
}

# (key, lowercase text to look for in the reason), checked in order. Each text is copied from this codebase:
#   timeout   -- capture_auth writes "claude --print timed out after 60s"
#   disabled  -- the Claude program's own "Your organization has disabled Claude subscription access for Claude Code" (quoted in multiproxy.py and its tests)
#   signin    -- the phrases claude_courier._classify already treats as "the sign-in is no good"
_MARKERS = (
    ("timeout", "timed out after"),
    ("disabled", "organization has disabled"),
    ("signin", "not logged in"),
    ("signin", "login expired"),
    ("signin", "failed to authenticate"),
    ("signin", "invalid bearer"),
)


def parse_version(text: Optional[str]) -> tuple:
    """(major, minor, patch) from what `claude --version` prints ("2.1.310 (Claude Code)", "2.0.63-beta.1"), or () when no number can be read."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else ()


def is_too_old(text: Optional[str]) -> bool:
    """True only for a version that was read and is below the minimum; an unreadable one is never "too old" (no guessing)."""
    v = parse_version(text)
    return bool(v) and v < MIN_VERSION


def read_version(path: Optional[str]) -> Optional[str]:
    """What `claude --version` prints, or None (no program, it did not answer within a few seconds, or it failed). Never raises."""
    if not path:
        return None
    try:
        import hidden_process
        proc = hidden_process.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=VERSION_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    return out if proc.returncode == 0 and parse_version(out) else None


def version_cache_key(path: Optional[str]) -> tuple:
    """The program's path and its file time, so updating Claude Code (a new file) is noticed without a restart."""
    try:
        return (path, os.stat(path).st_mtime_ns) if path else (path, 0)
    except OSError:
        return (path, 0)


def trouble_line(state: str, reason: Optional[str], version: Optional[str] = None) -> Optional[str]:
    """The line for an orange Claude card, or None (green or grey dot, still starting up, or a reason that isn't named here).
    A too-old version comes first: it is the likeliest cause of the other failures."""
    if state != "warn" or not reason:
        return None
    if is_too_old(version):
        return LINES["old"]
    low = reason.lower()
    for key, text in _MARKERS:
        if text in low:
            return LINES[key]
    return None


CLAUDE_TROUBLE_JS = r"""// ---------------- Claude card: why it is orange ----------------
// The page's one warning line (dot plus text, no box); the words come from the server (claude_trouble.py), so nothing here is worded.
// A device's Claude (the Courier row) reuses the version the Courier already reports: too old only when a number was read and it is below the minimum.
const CLAUDE_OLD_LINE = __OLD_LINE__;
const CLAUDE_MIN_VERSION = __MIN_VERSION__;
function claudeTooOld(v) {
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(String(v || ''));
  if (!m) return false;
  for (let i = 0; i < 3; i++) { const a = +m[i + 1], b = CLAUDE_MIN_VERSION[i]; if (a !== b) return a < b; }
  return false;
}
function claudeTroubleLine(p) {
  if (p.id !== 'claude' || !p.trouble) return '';
  return '<div class="vc-warn"><span class="op-dot warn"></span><span>' + esc(p.trouble) + '</span></div>';
}
"""

CLAUDE_TROUBLE_JS = CLAUDE_TROUBLE_JS.replace("__OLD_LINE__", json.dumps(LINES["old"])).replace("__MIN_VERSION__", json.dumps(list(MIN_VERSION)))
