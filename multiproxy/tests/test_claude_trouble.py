"""The line under an orange Claude card (2026-10-25, issue #4): a reason in, the right line out; nothing when the state is good or the reason isn't named."""
import json
import shutil
import subprocess
import tempfile

import pytest

import claude_trouble as ct

NODE = shutil.which("node")


@pytest.mark.parametrize("reason,key", [
    ("claude --print timed out after 60s", "timeout"),
    ("Your organization has disabled Claude subscription access for Claude Code", "disabled"),
    ("Not logged in", "signin"),
    ("Login expired, please sign in", "signin"),
    ("Failed to authenticate. API Error: 401", "signin"),
    ("invalid bearer token", "signin"),
])
def test_each_named_reason_gets_its_line(reason, key):
    assert ct.trouble_line("warn", reason) == ct.LINES[key]


@pytest.mark.parametrize("state,reason", [
    ("ok", "Not logged in"), ("off", "Not logged in"), ("warn", None), ("warn", ""),
    ("warn", "claude --print failed (see the proxy's own log for details)"), ("warn", "Something nobody has seen"),
])
def test_no_line_when_good_or_unnamed(state, reason):
    assert ct.trouble_line(state, reason) is None


def test_card_carries_the_line(proxy_module):
    proxy_module.auth.last_error = "Not logged in"
    proxy_module.auth.invalidate()
    try:
        import asyncio
        entries = asyncio.run(proxy_module._dashboard_providers())
    finally:
        proxy_module.auth.last_error = None
    if entries is not None:
        claude = next(e for e in entries if e["id"] == "claude")
        assert claude["trouble"] == ct.LINES["signin"]


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_page_script_parses_and_draws_only_the_claude_line():
    esc = "function esc(s){return String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}\n"
    src = esc + ct.CLAUDE_TROUBLE_JS + (
        "\nconsole.log(JSON.stringify([claudeTroubleLine({id:'claude',trouble:'A <b>line'}), claudeTroubleLine({id:'claude',trouble:null}), claudeTroubleLine({id:'codex',trouble:'x'})]));")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    import os
    try:
        out = subprocess.run([NODE, f.name], capture_output=True, timeout=30).stdout.decode("utf-8")
    finally:
        os.unlink(f.name)
    got = json.loads(out)
    assert 'class="vc-warn"' in got[0] and "A &lt;b&gt;line" in got[0]
    assert got[1] == "" and got[2] == ""


# ---- the version check (2026-10-25, issue #4) ------------------------------------------------------------------------------------------

def _stand_in(tmp_path, text, code=0):
    """A fake `claude` that prints `text` for --version (a .bat on Windows, a shell script elsewhere)."""
    import os
    if os.name == "nt":
        p = tmp_path / "claude.bat"
        p.write_text(f"@echo off\r\necho {text}\r\nexit /b {code}\r\n")
    else:
        p = tmp_path / "claude"
        p.write_text(f"#!/bin/sh\necho '{text}'\nexit {code}\n")
        p.chmod(0o755)
    return str(p)


@pytest.mark.parametrize("text,old", [
    ("2.0.62 (Claude Code)", True), ("1.0.60 (Claude Code)", True), ("2.0.63 (Claude Code)", False), ("2.1.310 (Claude Code)", False),
    ("3.0.0", False), ("2.0.63-beta.1", False), ("2.0.62-beta.1", True), ("garbage", False), ("", False), (None, False),
])
def test_version_table(text, old):
    assert ct.is_too_old(text) is old


@pytest.mark.parametrize("text,old", [("2.0.62 (Claude Code)", True), ("2.0.63 (Claude Code)", False), ("2.5.1 (Claude Code)", False)])
def test_read_from_a_stand_in_program(tmp_path, text, old):
    got = ct.read_version(_stand_in(tmp_path, text))
    assert got and ct.is_too_old(got) is old


def test_unreadable_or_missing_program_says_nothing_new(tmp_path):
    assert ct.read_version(_stand_in(tmp_path, "no number here")) is None
    assert ct.read_version(_stand_in(tmp_path, "2.0.1", code=1)) is None
    assert ct.read_version(str(tmp_path / "nope")) is None and ct.read_version(None) is None
    assert ct.trouble_line("warn", "Not logged in", ct.read_version(None)) == ct.LINES["signin"]


def test_old_line_shows_only_when_orange_with_a_reason_and_comes_first():
    for reason in ("Not logged in", "claude --print timed out after 60s", "Your organization has disabled Claude", "Something nobody has seen"):
        assert ct.trouble_line("warn", reason, "2.0.1 (Claude Code)") == ct.LINES["old"]
    assert ct.trouble_line("warn", "Not logged in", "2.1.310 (Claude Code)") == ct.LINES["signin"]
    for state, reason in (("ok", "x"), ("off", "x"), ("warn", None), ("warn", "")):
        assert ct.trouble_line(state, reason, "2.0.1 (Claude Code)") is None


def test_card_asks_the_version_once_and_only_while_orange(proxy_module, tmp_path, monkeypatch):
    import asyncio
    asked = []
    monkeypatch.setattr(ct, "read_version", lambda p: asked.append(p) or "2.0.1 (Claude Code)")
    monkeypatch.setattr(proxy_module, "CLAUDE_PATH", _stand_in(tmp_path, "x"))
    monkeypatch.setattr(proxy_module, "_claude_version_cache", (None, None))
    assert asyncio.run(proxy_module._claude_version_if_orange("ok", None)) is None and not asked
    assert asyncio.run(proxy_module._claude_version_if_orange("warn", "Not logged in")) == "2.0.1 (Claude Code)"
    asyncio.run(proxy_module._claude_version_if_orange("warn", "Not logged in"))
    assert len(asked) == 1


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_device_row_script_agrees_with_the_server():
    src = ct.CLAUDE_TROUBLE_JS + "\nconsole.log(JSON.stringify([%s, CLAUDE_OLD_LINE]));" % ", ".join(
        f"claudeTooOld({json.dumps(v)})" for v in ("2.0.62 (Claude Code)", "2.0.63", "2.1.310", "1.9.9", "", None, "garbage"))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    import os
    try:
        out = subprocess.run([NODE, f.name], capture_output=True, timeout=30).stdout.decode("utf-8")
    finally:
        os.unlink(f.name)
    *flags, line = json.loads(out)
    assert flags == [True, False, False, True, False, False, False] and line == ct.LINES["old"]
