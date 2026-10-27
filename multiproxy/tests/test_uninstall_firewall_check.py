"""The Courier's uninstall says plainly when the Windows Firewall rule is still there after it was asked to go (2026-10-06).

The look is installer_helper.py's `firewall-check` (read-only: `netsh advfirewall firewall show rule`, no administrator), run here against pasted netsh output; the Pascal that asks for it is
checked as text, the way tests/test_installer.py checks the installer scripts (Inno Setup is not installed on the test device, so the Pascal itself is not compiled here).
Nothing here adds, changes or removes a real firewall rule, and no installer is run.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import installer_helper as ih

ROOT = Path(__file__).resolve().parent.parent
NAME = "MultiProxy Courier"

RULE_THERE = """
Rule Name:                            MultiProxy Courier
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            In
Profiles:                             Private
Action:                               Allow
Ok.
"""
RULE_THERE_GERMAN = """
Regelname:                            MultiProxy Courier
----------------------------------------------------------------------
Aktiviert:                            Ja
Richtung:                             Ein
"""
NO_RULE = "No rules match the specified criteria.\n"
NO_RULE_GERMAN = "Es stimmt keine Regel mit den angegebenen Kriterien ueberein.\n"


def code_and_text(name):
    text = (ROOT / "installer" / name).read_text(encoding="utf-8")
    return re.sub(r"'\s*\+\s*[\r\n]+\s*'", "", text)


# ---- the look -------------------------------------------------------------------------------------------------------------------------------------
def test_a_rule_that_is_still_there_is_seen_in_any_language_of_windows():
    assert ih.firewall_rule_present(NAME, RULE_THERE) == {"present": True}
    assert ih.firewall_rule_present(NAME, RULE_THERE_GERMAN) == {"present": True}
    assert ih.firewall_rule_present(NAME.upper(), RULE_THERE) == {"present": True}


def test_a_rule_that_is_gone_is_seen_too():
    assert ih.firewall_rule_present(NAME, NO_RULE) == {"present": False}
    assert ih.firewall_rule_present(NAME, NO_RULE_GERMAN) == {"present": False}
    assert ih.firewall_rule_present(NAME, "") == {"present": False}


def test_the_command_line_answers_in_one_line_of_json_from_a_stand_in_for_netsh(tmp_path):
    stand_in = tmp_path / "netsh.txt"
    helper = str(ROOT / "installer_helper.py")
    for text, expected in ((RULE_THERE, True), (NO_RULE, False)):
        stand_in.write_text(text, encoding="utf-8")
        r = subprocess.run([sys.executable, helper, "firewall-check", "--name", NAME], capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
                           env={**os.environ, "INSTALLER_NETSH_OUTPUT": str(stand_in)})
        assert r.returncode == 0 and json.loads(r.stdout) == {"present": expected} and len(r.stdout.strip().splitlines()) == 1, r.stderr


def test_a_netsh_that_cannot_be_run_says_nothing_either_way(monkeypatch):
    import hidden_process
    monkeypatch.delenv("INSTALLER_NETSH_OUTPUT", raising=False)

    def boom(*a, **k):
        raise OSError("no netsh here")
    monkeypatch.setattr(hidden_process, "run", boom)
    assert ih.firewall_rule_present(NAME) == {"present": None}, "the installer then looks for itself"


def test_the_look_is_only_ever_a_show_never_an_add_or_a_delete(monkeypatch):
    import hidden_process
    monkeypatch.delenv("INSTALLER_NETSH_OUTPUT", raising=False)
    seen = []

    class R:
        stdout, stderr = NO_RULE, ""
    monkeypatch.setattr(hidden_process, "run", lambda cmd, **k: seen.append(cmd) or R())
    ih.firewall_rule_present(NAME)
    assert len(seen) == 1 and seen[0][1:5] == ["advfirewall", "firewall", "show", "rule"] and seen[0][-1] == f"name={NAME}"
    assert not any(w in " ".join(seen[0]) for w in ("add", "delete", "set"))


# ---- what the uninstaller does with it ----------------------------------------------------------------------------------------------------------
def test_after_the_removal_the_courier_uninstall_looks_and_the_window_is_not_hidden():
    t = code_and_text("courier.iss")
    block = t[t.index("if Pos('firewall', Items) > 0 then"):]
    block = block[:block.index("end;\nend;") + 4] if "end;\nend;" in block else block[:block.index("\nend;") + 5]
    assert "ShellExec('runas'" in block and "SW_SHOWNORMAL" in block and "SW_HIDE" not in block.split("ReportFirewallLeftover")[0]
    assert block.index("ShellExec('runas'") < block.index("ReportFirewallLeftover(App, '{#AppName}');"), "the rule is looked for after it was asked to go, not before"
    # only when the person asked for the rule to go: a silent uninstall (/REMOVEFIREWALL=0, the default) never reaches this block
    assert "if Pos('firewall', Items) > 0 then" in t and t.count("ReportFirewallLeftover(") == 1
    page = code_and_text("uninstall_page.iss")
    assert "if Courier and (ExpandConstant('{param:REMOVEFIREWALL|0}') = '1') then AppendId(Result, 'firewall');" in page


def test_the_plain_line_is_shown_only_when_the_rule_is_still_there_and_never_otherwise():
    t = code_and_text("uninstall_page.iss")
    body = t[t.index("procedure ReportFirewallLeftover"):]
    body = body[:body.index("\nend;") + 5]
    assert body.index("if not There then") < body.index("SuppressibleMsgBox("), "a rule that is gone gets no message"
    assert "exit;" in body.split("SuppressibleMsgBox(")[0]
    assert 'Pos(\'"present": true\', Json)' in body and 'Pos(\'"present": false\', Json)' in body and "There := FirewallRuleExists(Name);" in body, "no answer from the helper: the page's own look"
    assert "firewall-check --name" in body and "mbInformation" in body and "Log(" in body


def test_the_words_are_the_agreed_ones_and_the_command_is_the_one_that_finishes_the_job():
    t = code_and_text("uninstall_page.iss")
    body = t[t.index("procedure ReportFirewallLeftover"):]
    line = ("The Windows Firewall rule is still there, because Windows didn''t get your OK to remove it. It is only for your home network and does no harm. "
            "To remove it later, run this in an administrator command prompt:")
    assert line in body
    assert "'netsh advfirewall firewall delete rule name=\"' + Name + '\"'" in body
    assert "{#AppName}" in code_and_text("courier.iss") and "AppName" in code_and_text("courier.iss")
    assert re.search(r'#define AppName\s+"MultiProxy Courier"', (ROOT / "installer" / "courier.iss").read_text(encoding="utf-8")), "the name the message tells the person to delete"


def test_multiproxys_own_installer_has_no_firewall_rule_so_it_has_nothing_to_check():
    t = (ROOT / "installer" / "multiproxy.iss").read_text(encoding="utf-8").lower()
    assert "firewall" not in t and "netsh" not in t
    assert "ReportFirewallLeftover(" not in code_and_text("multiproxy.iss")


def test_the_linux_installers_never_add_a_rule_so_nothing_is_added_or_checked_there():
    for f in ("linux/install.sh", "linux/courier-install.sh", "linux/lib.sh", "courier/install-courier.sh"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(?:sudo\s+)?(?:ufw\s+(?:allow|delete)|firewall-cmd\s+--(?:add|remove))", text, re.M), f
