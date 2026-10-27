"""The Courier installer takes over an older (zip-setup) Courier instead of running beside it (2026-10-02), and the zip setup refuses to run beside an installer-made one.
What can be checked without Inno Setup is checked here; the installer itself was also run for real against a scratch folder (see TECHNICAL.md)."""
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh")
WIN = sys.platform == "win32"


def iss(name):
    return (ROOT / "installer" / name).read_text(encoding="utf-8")


def pascal_block(text, header):
    start = text.index(header)
    return text[start:text.index("\nend;", start)]


def test_the_older_programs_file_list_is_the_one_shared_list():
    """The installer no longer keeps its own copy: build.py hands it the Courier's one file list (courier/courier-files.json)."""
    t = iss("courier.iss")
    assert "OlderProgramFiles = '{#OlderProgramFiles}'" in t
    assert "/DOlderProgramFiles=" in (ROOT / "installer/build.py").read_text(encoding="utf-8")
    ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
    assert "courier-files.json" in ps1 and "'courier_system.py'" not in ps1, "the set-up script reads the shared list, not its own copy"


def test_the_install_takes_over_an_older_courier_before_anything_is_copied():
    t = iss("courier.iss")
    prep = t[t.index("function PrepareToInstall"):t.index("{ The firewall rule, private networks only")]
    assert "StopRunningFrom(ExpandConstant('{app}'), '')" in prep and "TakeOverOlderCourier(" in prep
    for where in ("{userappdata}", "{localappdata}", "SettingsFolder"):
        assert where in prep, where
    take = pascal_block(t, "procedure TakeOverOlderCourier")
    assert "StopProcessesUnder(Dir + '\\.venv')" in take
    assert "RegDeleteValue(HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\Run'" in take
    assert "{autoprograms}" in take and "RemoveOlderProgramFiles(Dir)" in take and "Log('Found an older Courier" in take
    assert "courier.json" not in t[t.index("OlderProgramFiles ="):t.index("procedure TakeOverOlderCourier")].split("{ Stops it")[0], "the settings are never in the removal list"
    assert "RemoveOlderProgramFiles" in t and "DelTree(Dir + '\\.venv'" in t


def test_stopping_matches_by_the_programs_folder_never_by_name():
    c = iss("common.iss")
    body = pascal_block(c, "procedure StopProcessesUnder")
    assert "StartsWith(" in body and "ExecutablePath" in body
    for by_name in ("Name -", ".Name", "ProcessName", "CommandLine", "taskkill", "/IM"):
        assert by_name not in body, by_name
    assert "StopProcessesUnder(AppDir + '\\python')" in c


def test_the_uninstall_also_stops_a_courier_left_in_the_settings_folder():
    t = iss("courier.iss")
    un = t[t.index("procedure CurUninstallStepChanged"):]
    assert un.index("StopRunningFrom(App, '')") < un.index("StopProcessesUnder(ThisSettingsFolder + '\\.venv')") < un.index("RunHelper(")


def test_the_zip_setup_refuses_beside_an_installer_made_courier():
    ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
    assert "Find-InstallerCourier" in ps1 and "Please uninstall it in Windows Settings" in ps1
    assert ps1.index("$fromInstaller = Find-InstallerCourier") < ps1.index("Resolve-InstallDir $InstallDir")
    ps1.encode("ascii")


@pytest.mark.skipif(PWSH is None or not WIN, reason="Windows PowerShell 7 only")
def test_the_zip_setup_finds_an_installer_made_courier_from_the_apps_list_entry(tmp_path):
    inst = tmp_path / "Programs" / "MultiProxy Courier"
    (inst / "python").mkdir(parents=True)
    (inst / "python" / "python.exe").write_bytes(b"x")
    scratch = r"HKCU:\Software\ClaudeScratch-" + uuid.uuid4().hex
    script = tmp_path / "t.ps1"
    script.write_text(
        f"$env:COURIER_INSTALL_LIB='1'; . '{ROOT / 'courier/setup-courier.ps1'}'\n"
        f"$k = '{scratch}'\n"
        "Write-Output ('none=' + [bool](Find-InstallerCourier $k))\n"
        "try {\n"
        "  New-Item -Path $k -Force | Out-Null\n"
        f"  New-ItemProperty -Path $k -Name InstallLocation -Value '{inst}\\' | Out-Null\n"
        "  Write-Output ('found=' + (Find-InstallerCourier $k))\n"
        f"  Set-ItemProperty -Path $k -Name InstallLocation -Value '{tmp_path / 'gone'}\\'\n"
        "  Write-Output ('gone=' + [bool](Find-InstallerCourier $k))\n"
        "} finally { Remove-Item -Path $k -Recurse -Force -ErrorAction SilentlyContinue }\n", encoding="utf-8")
    out = subprocess.run([PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=90).stdout
    assert "none=False" in out and f"found={inst}" in out and "gone=False" in out, out
