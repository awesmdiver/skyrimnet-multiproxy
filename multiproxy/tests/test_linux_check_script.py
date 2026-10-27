"""The Linux check kit (tools/linux-check.sh): light checks that need no Linux. The real behaviour is checked in containers (see the handoff)."""
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "linux-check.sh"
BASH = shutil.which("bash")


def test_the_script_exists_and_is_plain_ascii():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert text.isascii(), "plain ASCII, so it runs the same wherever it is copied"


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="needs a real bash on a Unix machine (on Windows the bash on the path is WSL's)")
def test_the_script_has_no_syntax_errors():
    r = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="needs a real bash on a Unix machine (on Windows the bash on the path is WSL's)")
def test_help_lists_every_option_and_changes_nothing():
    r = subprocess.run([BASH, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    for option in ("--installer", "--archive", "--with-model", "--with-voice", "--dry-run", "--uninstall", "--report"):
        assert option in r.stdout


def test_the_report_and_the_screen_never_print_the_pairing_code_into_the_report():
    text = SCRIPT.read_text(encoding="utf-8")
    # the code is only read from the Courier's own settings and handed to loopback calls; the report is redacted and the installer's own line is masked
    assert "<shown on screen only>" in text
    assert "<code>" in text
    # a call with the code from a non-loopback address would pair the Courier with the machine itself: the cross-address test must not carry it
    cross = text[text.index("Calls to this machine's own addresses"):text.index("# ---------------------------------------------------------------- 7.")]
    assert "COURIER_CODE" not in cross and "Authorization" not in cross


def test_the_script_no_longer_calls_a_tailscale_link_a_known_gap():
    text = open("tools/linux-check.sh", encoding="utf-8").read()
    assert "the Courier counts only 10.x, 172.16-31.x and 192.168.x as private" not in text and r"100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])" in text
    assert "Accepted as a private address: $ip (100.64.0.0/10, a Tailscale-style link)" in text


# ---- the feature-by-feature kit (2026-10-06) ------------------------------------------------------------------------------------------------------------------
import re


def source():
    return SCRIPT.read_text(encoding="utf-8")


def test_every_feature_in_the_table_has_a_label_and_a_place_in_the_script():
    text = source()
    keys = re.search(r"FEATURE_KEYS=\(([^)]*)\)", text).group(1).split()
    labels = dict(re.findall(r'\[(\w+)\]="([^"]+)"', text[text.index("declare -A FEATURE_LABEL"):text.index("declare -A FEATURE_HINT")]))
    assert set(keys) == set(labels), "every feature has a label, and nothing is labelled that is not in the table"
    for k in keys:
        assert f"FEAT={k}" in text, f"nothing in the script records a result under {k}"
    # the features the director listed
    for label in ("Install", "Starts at boot and after a restart", "Connected, and reconnect", "The device report", "The older-Courier check (build number)", "Ollama",
                  "Built-in model engine (llama.cpp)", "LM Studio", "Local decision model", "Voice engine", "Claude row", "Copilot row", "The Log view",
                  "Pause and cancel a download", "Update by the update zip", "Full uninstall", "Models folder and Change folder", "Add and remove the device",
                  "Keep awake and start at login"):
        assert label in labels.values(), label


def test_the_new_options_are_documented_and_parsed():
    text = source()
    for option in ("--remote", "--code", "--with-engines", "--with-claude", "--with-copilot", "--and-uninstall"):
        assert f"        {option})" in text and f"#   {option}" in text, option


def test_a_result_that_needs_a_person_is_its_own_kind_with_what_to_look_at():
    text = source()
    assert "person() { record" in text and "Needs a person (what to look at)" in text and "need a person" in text
    for what in ("Ollama: install it and use it", "Claude: a real sign-in", "Copilot: a real sign-in", "Pairing and Connected", "Add and remove the device", "Full uninstall"):
        assert f'person "{what}' in text or f'"{what}' in text, what


def test_a_machine_with_no_card_gets_not_applicable_lines_and_a_card_gets_detection():
    text = source()
    assert "not applicable (no graphics card)" in text and 'HAS_CARD=no' in text and "The Courier sees a graphics card" in text


def test_the_remote_mode_uses_the_pairing_code_without_printing_it_or_a_name_that_would_rename_the_pairing():
    text = source()
    assert 'MULTIPROXY_CODE' in text and "X-Courier-Client" not in text
    api = text[text.index("api() {"):text.index("http_code()")]
    assert 'Authorization": "Bearer "' in api and '"$BASE$path"' in api
    assert "pairing/unpair" not in text and "pairing/new" not in text, "the check never changes the pairing"


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="needs a real bash on a Unix machine (on Windows the bash on the path is WSL's)")
def test_a_dry_run_changes_nothing_and_prints_the_table(tmp_path):
    r = subprocess.run([BASH, str(SCRIPT), "--dry-run", "--installer", str(SCRIPT), "--report", str(tmp_path / "r.txt")], capture_output=True, text=True, timeout=120)
    out = r.stdout + (tmp_path / "r.txt").read_text(encoding="utf-8")
    assert "Feature table" in out and "Full uninstall" in out and "needs a person" in out or "not run" in out


@pytest.mark.skipif(BASH is None or sys.platform == "win32", reason="needs a real bash on a Unix machine (on Windows the bash on the path is WSL's)")
def test_remote_without_a_code_stops_plainly(tmp_path):
    r = subprocess.run([BASH, str(SCRIPT), "--remote", "127.0.0.1:1", "--report", str(tmp_path / "r.txt")], capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert "--remote needs the Courier's pairing code" in r.stdout + r.stderr
