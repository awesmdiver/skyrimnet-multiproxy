"""Courier setup asks for the firewall rule only when it isn't already there (2026-10-02).

The check reads `netsh advfirewall firewall show rule name="MultiProxy Courier"` (no administrator needed). The rules count as in place only when there are exactly
two and they are the right ones: enabled, inbound, allow, private networks, TCP on the Courier's port and UDP on the discovery port. The PowerShell check is run
for real here against pasted netsh output; the installer's Pascal copy of the same rule is checked as text and was run for real on the wizard page.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh") or shutil.which("powershell")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="PowerShell isn't installed")

RULE = """Rule Name:                            MultiProxy Courier
----------------------------------------------------------------------
Enabled:                              {enabled}
Direction:                            {direction}
Profiles:                             {profiles}
Grouping:
LocalIP:                              Any
RemoteIP:                             {remote}
Protocol:                             {proto}
LocalPort:                            {port}
RemotePort:                           Any
Edge traversal:                       No
Action:                               {action}
{program}"""


def rule(proto="TCP", port=8110, enabled="Yes", direction="In", profiles="Private", action="Allow", remote="LocalSubnet", program=None) -> str:
    line = f"Program:                              {program}\n" if program else ""
    return RULE.format(proto=proto, port=port, enabled=enabled, direction=direction, profiles=profiles, action=action, remote=remote, program=line)


GOOD = "\r\n" + rule("UDP", 8099) + "\r\n" + rule("TCP", 8110)
NOTHING = "No rules match the specified criteria.\r\n"


def check(netsh_text: str, port=8110, discovery=8099, program="") -> bool:
    src = (ROOT / "courier" / "setup-courier.ps1").read_text(encoding="utf-8")
    fn = src[src.index("function Test-CourierFirewall"):src.index("$firewall = 'unknown'")]
    with tempfile.TemporaryDirectory() as d:
        text = Path(d) / "netsh.txt"
        text.write_text(netsh_text, encoding="utf-8")
        script = Path(d) / "t.ps1"
        script.write_text(fn + f"\n$t = Get-Content -Raw '{text}'\nWrite-Output (Test-CourierFirewall $t {port} {discovery} '{program}')\n", encoding="utf-8")
        out = subprocess.run([PWSH, "-NoProfile", "-File", str(script)], capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return out.stdout.decode().strip() == "True"


@needs_pwsh
def test_the_two_right_rules_are_in_place_in_either_order():
    assert check(GOOD)
    assert check("\r\n" + rule("TCP", 8110) + "\r\n" + rule("UDP", 8099))


@needs_pwsh
def test_no_rule_at_all_is_not_in_place():
    assert not check(NOTHING) and not check("")


@needs_pwsh
@pytest.mark.parametrize("name,text", [
    ("a changed port", rule("UDP", 8099) + rule("TCP", 9000)),
    ("a missing UDP rule", rule("TCP", 8110)),
    ("a missing TCP rule", rule("UDP", 8099)),
    ("two TCP rules and no UDP", rule("TCP", 8110) + rule("TCP", 8110)),
    ("a wrong discovery port", rule("UDP", 9999) + rule("TCP", 8110)),
    ("public networks too", rule("UDP", 8099) + rule("TCP", 8110, profiles="Domain,Private,Public")),
    ("a disabled rule", rule("UDP", 8099) + rule("TCP", 8110, enabled="No")),
    ("a block rule", rule("UDP", 8099) + rule("TCP", 8110, action="Block")),
    ("an outbound rule", rule("UDP", 8099) + rule("TCP", 8110, direction="Out")),
    ("open to every address, as the old rule was", rule("UDP", 8099, remote="Any") + rule("TCP", 8110, remote="Any")),
    ("open to one address only on one of them", rule("UDP", 8099) + rule("TCP", 8110, remote="Any")),
])
def test_a_rule_that_does_not_match_is_not_in_place(name, text):
    assert not check(text), name


@needs_pwsh
def test_duplicates_are_not_in_place_so_they_get_replaced_by_exactly_two():
    assert not check(GOOD + "\r\n" + rule("TCP", 8110)), "three rules"
    assert not check(GOOD + "\r\n" + rule("UDP", 8099) + "\r\n" + rule("TCP", 8110)), "four rules"


@needs_pwsh
def test_this_couriers_own_port_is_what_counts():
    assert check(rule("UDP", 8099) + rule("TCP", 9000), port=9000)
    assert not check(GOOD, port=9000)


@needs_pwsh
def test_a_windows_that_prints_other_words_is_asked_as_before():
    assert not check(GOOD.replace("Enabled:", "Aktiviert:").replace("Rule Name:", "Regelname:"))


def test_the_script_says_one_line_and_skips_the_question_and_the_permission_prompt_when_in_place():
    s = (ROOT / "courier" / "setup-courier.ps1").read_text(encoding="utf-8")
    block = s[s.index("if (Test-CourierFirewall $existing"):s.index("if ($NoStart)")]
    assert 'Say "The firewall rule is already in place."' in block and "$firewall = 'added'" in block
    in_place, ask = block.split("} else {", 1)
    assert "Ask " not in in_place and "RunAs" not in in_place, "nothing is asked or elevated when the rules match"
    fn = s[s.index("function New-CourierFirewallCommand"):s.index("function Get-CourierListenerProgram")]
    assert "Ask " in ask and "New-CourierFirewallCommand $Port $discoveryPort" in ask, "otherwise as before: ask, then run the one command"
    assert "delete rule" in fn and fn.count("add rule") == 2, "that command deletes every old rule and adds exactly two"


def test_the_installer_starts_the_tick_box_unticked_with_a_quiet_note_when_the_rules_are_in_place():
    t = (ROOT / "installer" / "courier.iss").read_text(encoding="utf-8")
    assert "ChkFirewall.Checked := not FirewallInPlace;" in t
    assert "Caption := 'Already allowed.';" in t
    assert "Windows will ask for permission in a pop-up. Click Yes so your main device can find this Courier." in t, "the old wording stays when it will ask"
    body = t[t.index("function FirewallInPlace"):t.index("function SettingsFolder")]
    for needle in ("netsh advfirewall firewall show rule", "'Yes'", "'In'", "'Allow'", "'Private'", "CourierPortText", "{#DiscoveryPort}", "(Blocks = 2)"):
        assert needle in body, needle
    assert "RunAs" not in body, "reading the rules needs no administrator"
    # the replace step is unchanged: delete every old rule, add exactly two, and only when the box is ticked
    assert t.count('add rule name="{#AppName}"') == 2 and "(CurStep = ssInstall) and ChkFirewall.Checked" in t


def test_linux_prints_the_ufw_hint_only_when_the_rules_are_not_already_there():
    s = (ROOT / "courier" / "install-courier.sh").read_text(encoding="utf-8")
    assert 'say "The firewall rule is already in place."' in s
    assert re.search(r'grep -Eq "\^\$\{PORT\}/tcp\[\[:space:\]\]\+ALLOW"', s) and "DISCOVERY_PORT}/udp" in s
    assert s.index("is already in place") < s.index("sudo ufw allow from $NET to any port $PORT proto tcp")
    assert "ufw status 2>/dev/null | grep -qi 'status: active'" in s, "unchanged: without root ufw can't be read, so nothing is printed, as before"


def test_no_installer_script_has_a_stray_control_character():
    """A `\f` typed inside a path string turns into a form feed and quietly breaks the path (this broke the first version of the check: it read an empty file)."""
    for name in ("courier.iss", "multiproxy.iss", "common.iss", "uninstall_page.iss"):
        t = (ROOT / "installer" / name).read_text(encoding="utf-8")
        assert not re.search("[\x00-\x08\x0b\x0c\x0e-\x1f]", t), name
        assert r"\fw-check.txt" in (ROOT / "installer" / "courier.iss").read_text(encoding="utf-8")
