"""What the installers pre-fill on their folder page (installer/prefill.ps1), 2026-10-06.

The real wizard page cannot be driven without really installing, and an installer is never run for real on this device. So the looking-and-choosing lives in prefill.ps1, and this
runs that script against scratch folders, a scratch registry key (`Software\\MultiProxyTest-<random>`, removed after every test) and a scratch Run key. The real
`HKCU\\Software\\MultiProxy Courier` key is never read or written here: every call names its own key.
"""
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "prefill.ps1"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

pytestmark = pytest.mark.skipif(os.name != "nt" or not Path(POWERSHELL).exists(), reason="the installer script is PowerShell on Windows")


class Scratch:
    """A fake user: its own AppData, LocalAppData, Start menu, registry note key and Run key."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.appdata = tmp_path / "AppData" / "Roaming"
        self.local = tmp_path / "AppData" / "Local"
        self.menu = tmp_path / "StartMenu"
        for d in (self.appdata, self.local, self.menu):
            d.mkdir(parents=True)
        tag = "MultiProxyTest-" + uuid.uuid4().hex[:10]
        self.regkey = "Software\\" + tag
        self.runkey = "HKCU:\\Software\\" + tag + "\\Run"
        self.default_install = str(self.local / "Programs" / "MultiProxy Courier")

    def run(self, mode, app="courier", previous=None, **extra):
        out = self.tmp / "answers.txt"
        out.unlink(missing_ok=True)
        cmd = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), "-App", app, "-Mode", mode,
               "-RegKey", self.regkey, "-AppData", str(self.appdata), "-LocalAppData", str(self.local), "-RunKey", self.runkey, "-StartMenuDir", str(self.menu)]
        if mode == "discover":
            cmd += ["-Out", str(out), "-DefaultInstall", self.default_install, "-PreviousInstall", previous or self.default_install,
                    "-DefaultSettings", str(self.appdata / ("MultiProxy Courier" if app == "courier" else "SkyrimNet MultiProxy"))]
        for k, v in extra.items():
            cmd += ["-" + k, str(v)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr + r.stdout
        if mode == "discover":
            text = out.read_text(encoding="utf-8-sig")
            return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        return None

    def note(self):
        r = subprocess.run([POWERSHELL, "-NoProfile", "-Command",
                            f"try {{ (Get-ItemProperty -LiteralPath 'Registry::HKEY_CURRENT_USER\\{self.regkey}' -ErrorAction Stop | Select-Object InstallDir,SettingsDir,ModelsDir,StartWithWindows,StartMenu | ConvertTo-Json -Compress) }} catch {{ 'none' }}"],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout.strip()
        return None if out == "none" else json.loads(out)

    def set_run(self, name, command):
        subprocess.run([POWERSHELL, "-NoProfile", "-Command",
                        f"New-Item -Path '{self.runkey}' -Force | Out-Null; New-ItemProperty -LiteralPath '{self.runkey}' -Name '{name}' -Value '{command}' -PropertyType String -Force | Out-Null"],
                       capture_output=True, text=True, timeout=60, check=True)

    def cleanup(self):
        subprocess.run([POWERSHELL, "-NoProfile", "-Command", f"Remove-Item -LiteralPath 'Registry::HKEY_CURRENT_USER\\{self.regkey}' -Recurse -Force -ErrorAction SilentlyContinue"],
                       capture_output=True, text=True, timeout=60)


@pytest.fixture
def pc(tmp_path):
    s = Scratch(tmp_path)
    assert s.regkey.startswith("Software\\MultiProxyTest-") and "Courier" not in s.regkey
    yield s
    s.cleanup()


def courier_folder(base: Path, paired=True, models=None, win=None, name="MultiProxy Courier", age=0, older=False, text=None):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"paired": paired, "paired_name": "Gaming device" if paired else "", "port": 8110}
    if models:
        cfg["local_models"] = {"folder": str(models)}
    if win is not None:
        cfg["start_with_windows"] = win
    f = d / "courier.json"
    f.write_text(text if text is not None else json.dumps(cfg, indent=2), encoding="utf-8")
    if age:
        t = time.time() - age
        os.utime(f, (t, t))
    if older:
        (d / "courier.py").write_text("# older", encoding="utf-8")
        (d / ".venv").mkdir(exist_ok=True)
    return d


# ---- first install, and the note -------------------------------------------------------------------------------------------------
def test_a_first_install_gets_the_defaults_and_says_nothing_is_wrong(pc):
    a = pc.run("discover")
    assert a["install"] == pc.default_install and a["installSource"] == "default"
    assert a["settings"] == str(pc.appdata / "MultiProxy Courier") and a["settingsSource"] == "default"
    assert a["models"] == "" and a["modelsSource"] == "none"
    assert (a["startWithWindows"], a["startMenu"], a["evidence"]) == ("1", "1", "0")
    assert (a["staleInstall"], a["staleSettings"], a["staleModels"], a["pairedFolder"]) == ("0", "0", "0", "")


def test_the_first_install_writes_the_note_and_only_under_the_key_it_was_given(pc, tmp_path):
    assert pc.note() is None
    pc.run("write", Install=str(tmp_path / "prog"), Settings=str(tmp_path / "sett"), Models=str(tmp_path / "models"), StartWithWindows="0", StartMenu="1")
    n = pc.note()
    assert n == {"InstallDir": str(tmp_path / "prog"), "SettingsDir": str(tmp_path / "sett"), "ModelsDir": str(tmp_path / "models"), "StartWithWindows": "0", "StartMenu": "1"}


def test_an_update_pre_fills_everything_from_the_note(pc, tmp_path):
    for d in ("prog", "sett", "models"):
        (tmp_path / d).mkdir()
    pc.run("write", Install=str(tmp_path / "prog"), Settings=str(tmp_path / "sett"), Models=str(tmp_path / "models"), StartWithWindows="0", StartMenu="0")
    a = pc.run("discover")
    assert (a["install"], a["installSource"]) == (str(tmp_path / "prog"), "note")
    assert (a["settings"], a["settingsSource"]) == (str(tmp_path / "sett"), "note")
    assert (a["models"], a["modelsSource"]) == (str(tmp_path / "models"), "note")
    assert (a["startWithWindows"], a["startMenu"]) == ("0", "0")
    assert (a["staleInstall"], a["staleSettings"], a["staleModels"]) == ("0", "0", "0")


# ---- the settings file wins over the note -----------------------------------------------------------------------------------------
def test_the_settings_files_models_folder_wins_over_a_different_note(pc, tmp_path):
    sett = courier_folder(tmp_path, models=tmp_path / "models-new")
    (tmp_path / "models-new").mkdir()
    (tmp_path / "models-old").mkdir()
    pc.run("write", Install=str(tmp_path / "prog"), Settings=str(sett), Models=str(tmp_path / "models-old"), StartWithWindows="1", StartMenu="1")
    a = pc.run("discover")
    assert (a["models"], a["modelsSource"]) == (str(tmp_path / "models-new"), "settings")


def test_the_settings_files_start_with_windows_choice_wins_over_the_note(pc, tmp_path):
    sett = courier_folder(tmp_path, win=False)
    pc.run("write", Install=str(tmp_path), Settings=str(sett), Models="", StartWithWindows="1", StartMenu="1")
    a = pc.run("discover")
    assert (a["startWithWindows"], a["startWithWindowsSource"]) == ("0", "settings")


def test_a_note_models_folder_is_used_when_the_settings_file_has_none(pc, tmp_path):
    sett = courier_folder(tmp_path)
    (tmp_path / "m").mkdir()
    pc.run("write", Install=str(tmp_path), Settings=str(sett), Models=str(tmp_path / "m"), StartWithWindows="1", StartMenu="1")
    assert pc.run("discover")["modelsSource"] == "note"


# ---- an older script-made Courier: no note at all ---------------------------------------------------------------------------------
def test_an_old_script_install_with_no_note_pre_fills_from_its_own_settings_file_and_folder(pc, tmp_path):
    (tmp_path / "ai-models").mkdir()
    old = courier_folder(pc.local, models=tmp_path / "ai-models", older=True)          # %LOCALAPPDATA%\MultiProxy Courier: where an older setup put program AND settings
    a = pc.run("discover")
    assert (a["settings"], a["settingsSource"]) == (str(old), "paired")
    assert (a["models"], a["modelsSource"]) == (str(tmp_path / "ai-models"), "settings")
    assert (a["install"], a["installSource"]) == (str(old), "older-install")
    assert a["pairedFolder"] == str(old) and a["evidence"] == "1"


def test_the_same_in_the_default_appdata_folder(pc, tmp_path):
    (tmp_path / "ai").mkdir()
    old = courier_folder(pc.appdata, models=tmp_path / "ai", older=True)
    a = pc.run("discover")
    assert (a["settings"], a["models"], a["pairedFolder"]) == (str(old), str(tmp_path / "ai"), str(old))


def test_a_pairing_in_the_programs_folder_is_found_too(pc):
    old = courier_folder(pc.local / "Programs", paired=True)
    assert pc.run("discover")["settings"] == str(old)


def test_a_pairing_is_found_through_the_start_with_windows_entry_of_the_old_install(pc, tmp_path):
    odd = courier_folder(tmp_path / "somewhere else", paired=True, name="Courier settings")
    pc.set_run("MultiProxy Courier", f'"C:\\py\\pythonw.exe" "C:\\x\\courier.py" run --config-dir "{odd}"')
    a = pc.run("discover")
    assert (a["settings"], a["pairedFolder"], a["startWithWindows"], a["startWithWindowsSource"]) == (str(odd), str(odd), "1", "detected")


def test_with_two_pairings_the_newest_wins_and_the_page_is_told(pc):
    older = courier_folder(pc.appdata, age=86400 * 30)
    newer = courier_folder(pc.local, age=60)
    a = pc.run("discover")
    assert a["settings"] == str(newer) and a["newestOfSeveral"] == str(newer)
    assert str(older) != a["settings"]


def test_one_pairing_among_unpaired_folders_wins_over_them(pc):
    courier_folder(pc.appdata, paired=False)
    paired = courier_folder(pc.local, paired=True, age=9999)
    a = pc.run("discover")
    assert (a["settings"], a["newestOfSeveral"]) == (str(paired), "")


def test_no_pairing_anywhere_means_the_code_is_shown_as_before(pc):
    a = pc.run("discover")
    assert a["pairedFolder"] == ""
    courier_folder(pc.appdata, paired=False)
    a = pc.run("discover")
    assert a["pairedFolder"] == "" and a["settingsSource"] == "settings"


# ---- stale and damaged -------------------------------------------------------------------------------------------------------------
def test_a_folder_that_is_gone_is_shown_as_typed_and_flagged_never_replaced(pc, tmp_path):
    pc.run("write", Install=str(tmp_path / "gone-prog"), Settings=str(tmp_path / "gone-sett"), Models=str(tmp_path / "gone-models"), StartWithWindows="1", StartMenu="1")
    a = pc.run("discover")
    assert (a["install"], a["settings"], a["models"]) == (str(tmp_path / "gone-prog"), str(tmp_path / "gone-sett"), str(tmp_path / "gone-models"))
    assert (a["staleInstall"], a["staleSettings"], a["staleModels"]) == ("1", "1", "1")


def test_a_models_folder_in_the_settings_file_that_is_gone_is_flagged(pc, tmp_path):
    courier_folder(pc.appdata, models=tmp_path / "unplugged-drive" / "models")
    a = pc.run("discover")
    assert a["models"].endswith("models") and a["staleModels"] == "1"


@pytest.mark.parametrize("text", ["{ not json", "", "[1, 2]", "null", "\x00\x01"])
def test_a_damaged_settings_file_never_stops_the_installer_and_falls_to_the_next_source(pc, tmp_path, text):
    sett = courier_folder(tmp_path, text=text)
    (tmp_path / "m").mkdir()
    pc.run("write", Install=str(tmp_path), Settings=str(sett), Models=str(tmp_path / "m"), StartWithWindows="1", StartMenu="1")
    a = pc.run("discover")
    assert a["models"] == str(tmp_path / "m") and a["modelsSource"] == "note" and a["pairedFolder"] == ""


def test_odd_values_in_the_registry_or_the_file_do_not_break_it(pc, tmp_path):
    pc.run("write", Install="", Settings="", Models="", StartWithWindows="x", StartMenu="")
    courier_folder(pc.appdata, text=json.dumps({"paired": "yes", "local_models": {"folder": 5}, "start_with_windows": "maybe"}))
    a = pc.run("discover")
    assert a["install"] == pc.default_install and a["pairedFolder"] == ""


# ---- the final page: the folder it picked really is the paired one ---------------------------------------------------------------------
def test_courier_reports_the_pairing_of_the_folder_the_page_picked(pc, tmp_path):
    old = courier_folder(pc.local, paired=True)
    picked = pc.run("discover")["settings"]
    r = subprocess.run([os.environ.get("PYTHON", "py"), "-X", "utf8", str(ROOT / "courier.py"), "paired", "--config-dir", picked], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert picked == str(old) and r.stdout.strip() == "Gaming device", r.stderr


# ---- MultiProxy's own installer ---------------------------------------------------------------------------------------------------------
def test_multiproxy_pre_fills_its_models_folder_from_the_settings_file_it_found(pc, tmp_path):
    prog, sett, models = tmp_path / "prog", tmp_path / "sett", tmp_path / "models"
    for d in (prog, sett, models):
        d.mkdir()
    (prog / "settings-location.txt").write_text(str(sett), encoding="utf-8")
    (sett / "config.json").write_text(json.dumps({"local_models": {"folder": str(models)}}), encoding="utf-8")
    a = pc.run("discover", app="multiproxy", previous=str(prog))
    assert (a["settings"], a["models"], a["modelsSource"]) == (str(sett), str(models), "settings")
    assert a["install"] == str(prog) and a["installSource"] == "previous"


def test_multiproxy_first_install_is_unchanged(pc):
    a = pc.run("discover", app="multiproxy")
    assert a["settings"] == str(pc.appdata / "SkyrimNet MultiProxy") and a["models"] == "" and a["startWithWindows"] == "1"


def test_multiproxy_uses_its_own_note_key_name_not_the_couriers():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "'SkyrimNet MultiProxy'" in text and "'MultiProxy Courier'" in text
    common = (ROOT / "installer" / "common.iss").read_text(encoding="utf-8")
    assert "'Software\\MultiProxy Courier'" in common and "'Software\\SkyrimNet MultiProxy'" in common


# ---- the installer scripts follow the rules (checked as text: an installer is never run for real here) ------------------------------------
def iss(name):
    return "\n".join(l for l in (ROOT / "installer" / name).read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith(";"))


@pytest.mark.parametrize("name", ["courier.iss", "multiproxy.iss"])
def test_each_installer_reads_what_is_there_before_it_shows_the_page_and_saves_the_note_after_installing(name):
    t = iss(name)
    assert 'Source: "prefill.ps1"; DestDir: "{tmp}"; Flags: dontcopy' in t
    assert "RunPrefill(" in t and "WritePrefillNote(" in t and "ForgetPrefillNote(" in t
    assert t.index("RunPrefill(") < t.index("Scripted installs (and tests)"), "the switches still win over everything pre-filled"
    assert re.search(r"if CurStep = ssPostInstall then\s+WritePrefillNote\(", t)
    assert "StaleFolderLines(" in t


def test_the_models_folder_is_no_longer_always_blank_on_the_courier_page():
    t = iss("courier.iss")
    assert "FolderInputPage.Values[2] := PrefillValue('models');" in t and "FolderInputPage.Values[2] := '';" in t       # (blank only first, then filled)
    assert t.index("FolderInputPage.Values[2] := '';") < t.index("FolderInputPage.Values[2] := PrefillValue('models');")


def test_the_note_only_ever_names_the_installers_own_two_keys_and_a_test_switch():
    common = iss("common.iss")
    assert common.count("RegDeleteKeyIncludingSubkeys(") == 1 and "RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, RegNoteKey(App))" in common
    assert "{param:REGKEY|" in common
    assert "-RegKey ' + Quoted(RegNoteKey(App))" in common, "every run of the script names its key, so a test's /REGKEY= is always honoured"
    for name in ("courier.iss", "multiproxy.iss"):
        assert "ForgetPrefillNote('" in iss(name) and "RegDeleteKey" not in iss(name)


def test_the_stale_folder_and_lost_pairing_lines_are_the_words_in_the_handoff():
    common, courier = iss("common.iss"), iss("courier.iss")
    for kind in ("install", "settings", "models"):
        assert f"The {kind} folder is missing. Pick a new one, or it will be created." in common
    assert "This looks like an update, but the settings folder you chose has no pairing. You will need to pair your main device again." in courier
    assert "We found more than one older Courier. Here are the settings for the newest one." in courier


def test_the_lost_pairing_line_is_amber_and_watches_the_settings_folder():
    t = iss("courier.iss")
    assert "GoneNote.Font.Color := WarnColor;" in t and "FolderInputPage.Edits[1].OnChange := @SettingsFolderChanged;" in t
    assert '"paired": true' in t and "courier.json" in t
