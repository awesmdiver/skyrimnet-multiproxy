"""The Windows installers (2026-10-02): the settings-folder rule the program follows, the helper the installers call, the build's own rules, and the installer
scripts' wording. The installers themselves were also built, installed, upgraded and uninstalled for real (see TECHNICAL.md); what can be checked without
Inno Setup is checked here."""
import io
import json
import shutil
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import installer_helper as ih
import multiproxy_tray as mt
import settings_files

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))
import build as bd  # noqa: E402


# ---- where the settings live --------------------------------------------------------------------------------------------------
def test_settings_default_to_the_program_folder_as_they_always_have(tmp_path, monkeypatch):
    monkeypatch.delenv("MULTIPROXY_SETTINGS_DIR", raising=False)
    assert settings_files.settings_dir(tmp_path) == str(tmp_path)


def test_the_installer_written_location_file_moves_them(tmp_path, monkeypatch):
    monkeypatch.delenv("MULTIPROXY_SETTINGS_DIR", raising=False)
    (tmp_path / "settings-location.txt").write_text('  "D:\\My MultiProxy settings"  \n', encoding="utf-8")
    assert settings_files.settings_dir(tmp_path) == "D:\\My MultiProxy settings"
    (tmp_path / "settings-location.txt").write_text("\n", encoding="utf-8")
    assert settings_files.settings_dir(tmp_path) == str(tmp_path), "an empty file means nothing"


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    (tmp_path / "settings-location.txt").write_text("D:\\from-file", encoding="utf-8")
    monkeypatch.setenv("MULTIPROXY_SETTINGS_DIR", "E:\\from-env")
    assert settings_files.settings_dir(tmp_path) == "E:\\from-env"


def test_every_settings_file_the_server_writes_goes_through_the_settings_folder():
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
    for name in ("multiproxy.ini", "prompt-captures", "multiproxy.log", "config.json", "speed_history.db", "local_models_pids.json"):
        lines = [l for l in src.splitlines() if f'"{name}"' in l and "os.path.join(" in l]
        assert lines, name
        assert all("SETTINGS_DIR" in l for l in lines), (name, lines)
    assert "SETTINGS_DIR = settings_files.settings_dir(" in src


def test_the_tray_app_reads_and_writes_in_the_settings_folder_too(tmp_path, monkeypatch):
    monkeypatch.delenv("MULTIPROXY_SETTINGS_DIR", raising=False)
    prog, sett = tmp_path / "prog", tmp_path / "sett"
    prog.mkdir()
    sett.mkdir()
    (prog / "settings-location.txt").write_text(str(sett), encoding="utf-8")
    (sett / "config.json").write_text(json.dumps({"main_port": 8123}), encoding="utf-8")
    monkeypatch.delenv("MULTIPROXY_PORT", raising=False)
    assert mt.configured_port(prog) == 8123
    ctl = mt.Controller(prog)
    assert ctl.settings_dir == sett and ctl.ini_path == str(sett / "multiproxy.ini") and ctl.host.log_path == sett / "multiproxy.log"


# ---- the tray app can be asked to quit (the installer does, before an upgrade or an uninstall) -----------------------------------------------
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_a_running_tray_app_is_asked_to_quit_and_the_quit_command_waits_for_it(monkeypatch):
    port = free_port()
    monkeypatch.setenv("MULTIPROXY_TRAY_LOCK_PORT", str(port))
    holder = mt.SingleInstance(port)
    assert holder.acquire()
    quitting = []

    def on_quit():
        quitting.append(1)
        holder.release()                                  # a real tray app quits and lets go of the lock
    holder.serve(lambda: None, on_quit)
    assert mt.main(["quit"]) == 0 and quitting == [1]


def test_quit_with_no_tray_app_running_is_simply_done(monkeypatch):
    monkeypatch.setenv("MULTIPROXY_TRAY_LOCK_PORT", str(free_port()))
    t = time.time()
    assert mt.main(["quit"]) == 0 and time.time() - t < 3


def test_the_couriers_sign_in_command_follows_a_chosen_settings_folder(tmp_path):
    import courier
    assert courier.startup_command().endswith(' run') and "--config-dir" not in courier.startup_command()
    assert "--config-dir" not in courier.startup_command(courier.default_config_dir())
    assert courier.startup_command(tmp_path).endswith(f' run --config-dir "{tmp_path.resolve()}"')


# ---- the helper the installers call ---------------------------------------------------------------------------------------------------
def test_an_old_zip_install_is_recognised_by_what_it_has_never_by_scanning(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    assert ih.find_old_install(str(old))["ok"] is False
    (old / "proxy.py").write_text("x")
    (old / "config.json").write_text("{}")
    found = ih.find_old_install(str(old))
    assert found["ok"] and found["files"] == ["config.json"] and found["isProgram"]
    assert ih.find_old_install(str(tmp_path / "nowhere"))["ok"] is False


def test_importing_old_settings_never_overwrites_what_is_already_there(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "config.json").write_text('{"k": "old"}')
    (old / "proxy.ini").write_text("[General]\n; note\n")
    (new / "config.json").write_text('{"k": "new"}')
    done = ih.import_old_settings(str(old), str(new))
    assert (new / "config.json").read_text() == '{"k": "new"}' and "config.json" not in done
    assert (new / "multiproxy.ini").read_text() == "[General]\n; note\n", "the old proxy.ini becomes multiproxy.ini, comments kept"
    assert (old / "config.json").read_text() == '{"k": "old"}' and (old / "proxy.ini").exists(), "the old folder is never changed"


def test_seed_multiproxy_writes_the_location_the_models_folder_and_imports(tmp_path):
    prog, sett, models, old = tmp_path / "prog", tmp_path / "sett", tmp_path / "models", tmp_path / "old"
    for d in (prog, old):
        d.mkdir()
    (old / "multiproxy.py").write_text("x")
    (old / "config.json").write_text('{"glm_api_key": "K", "local_models": {"folder": "X:/mine"}}')
    r = ih.seed_multiproxy(str(prog), str(sett), str(models), str(old))
    assert r["ok"] and (prog / "settings-location.txt").read_text() == str(sett) and r["imported"] == ["config.json"]
    cfg = json.loads((sett / "config.json").read_text())
    assert cfg["glm_api_key"] == "K" and cfg["local_models"]["folder"] == "X:/mine", "a models folder the player already chose is never replaced"
    assert not models.exists(), "...so the new one isn't made either"


def test_seed_multiproxy_fresh_install_and_the_same_folder_case(tmp_path):
    prog, sett, models = tmp_path / "prog", tmp_path / "sett", tmp_path / "models"
    prog.mkdir()
    ih.seed_multiproxy(str(prog), str(sett), str(models))
    assert json.loads((sett / "config.json").read_text()) == {"local_models": {"folder": str(models)}} and models.is_dir()
    ih.seed_multiproxy(str(prog), str(sett), str(models))                       # run again (an upgrade): nothing changes
    assert json.loads((sett / "config.json").read_text()) == {"local_models": {"folder": str(models)}}
    ih.seed_multiproxy(str(prog), str(prog), None)                              # settings next to the program: no location file needed
    assert not (prog / "settings-location.txt").exists()


def test_seed_courier_sets_the_models_folder_and_port_and_records_where_for_the_uninstaller(tmp_path):
    prog, cfg_dir, models = tmp_path / "prog", tmp_path / "cfg", tmp_path / "models"
    prog.mkdir()
    ih.seed_courier(str(cfg_dir), str(models), 8110, str(prog))
    c = json.loads((cfg_dir / "courier.json").read_text())
    assert c["port"] == 8110 and c["local_models"]["folder"] == str(models) and c["models_folder_chosen"] is True
    assert (prog / "settings-location.txt").read_text() == str(cfg_dir)


def _leftover_tree(tmp_path, courier=False):
    """A settings folder with every kind of leftover, a models folder beside it, a model added by path, and a stand-in Skyrim folder that must never be touched."""
    sett, models, own, skyrim = tmp_path / "sett", tmp_path / "models", tmp_path / "own", tmp_path / "Skyrim"
    for d in (sett, models / "models", models / "engine", sett / "voice" / "engine", sett / "voice" / "clips", sett / "voice-downloads", own, skyrim / "Data"):
        d.mkdir(parents=True)
    (sett / ("courier.json" if courier else "config.json")).write_text(json.dumps({"local_models": {"folder": str(models), "custom": [{"path": str(own / "mine.gguf")}]}}))
    (models / "models" / "m.gguf").write_bytes(b"y" * 5000)
    (models / "engine" / "server.bin").write_bytes(b"z" * 700)
    (sett / "voice" / "engine" / "a.bin").write_bytes(b"v" * 300)
    (sett / "voice" / "voice-state.json").write_text("{}")       # an install made before voice moved under the models folder: it stays where it is
    (sett / "voice" / "clips" / "me.wav").write_bytes(b"c" * 200)
    (sett / "voice-downloads" / "kept.bin").write_bytes(b"d" * 100)
    (sett / "voice-downloads" / "index.json").write_text(json.dumps({"kept.bin": {}}))        # only what the index names (and the index) is the program's own
    (own / "mine.gguf").write_bytes(b"o" * 50)
    (skyrim / "Data" / "mod.esp").write_bytes(b"s")
    return sett, models, own, skyrim


def test_leftovers_lists_only_what_exists_and_sizes_are_optional(tmp_path):
    sett, models, own, skyrim = _leftover_tree(tmp_path)
    got = ih.leftovers(str(sett))
    assert [i["id"] for i in got["items"]] == ["settings", "models", "voice", "clips", "downloads"]
    assert all("bytes" not in i or i["bytes"] is None for i in got["items"])
    sized = {i["id"]: i["bytes"] for i in ih.leftovers(str(sett), sizes=True)["items"]}
    assert sized["models"] == 5700 and sized["voice"] == 302 and sized["clips"] == 200 and sized["downloads"] == 100 + len(json.dumps({"kept.bin": {}}))
    shutil.rmtree(sett / "voice-downloads")
    shutil.rmtree(sett / "voice" / "clips")
    assert "downloads" not in [i["id"] for i in ih.leftovers(str(sett))["items"]] and "clips" not in [i["id"] for i in ih.leftovers(str(sett))["items"]]
    assert ih.leftovers(str(tmp_path / "gone"))["items"] == []


def test_remove_takes_only_the_ticked_kinds_and_never_skyrim_or_a_model_added_by_path(tmp_path):
    sett, models, own, skyrim = _leftover_tree(tmp_path)
    assert ih.remove(str(sett), ["clips"])["failed"] == []
    assert not (sett / "voice" / "clips").exists() and (sett / "voice" / "engine" / "a.bin").exists() and (models / "models" / "m.gguf").exists()
    ih.remove(str(sett), ["models", "voice", "downloads"])
    assert not (models / "models").exists() and not (models / "engine").exists() and not (sett / "voice").exists() and not (sett / "voice-downloads").exists()
    assert (sett / "config.json").exists(), "settings were not ticked"
    ih.remove(str(sett), ["settings"])
    assert not (sett / "config.json").exists()
    assert (own / "mine.gguf").exists() and (skyrim / "Data" / "mod.esp").exists(), "a model added by path and Skyrim are never touched"


def test_remove_with_nothing_ticked_removes_nothing_and_an_unknown_kind_is_ignored(tmp_path):
    sett, models, own, skyrim = _leftover_tree(tmp_path)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    assert ih.remove(str(sett), [])["removed"] == [] and ih.remove(str(sett), ["not-a-kind"])["removed"] == []
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_the_couriers_leftovers_follow_its_own_layout(tmp_path):
    sett, models, own, skyrim = _leftover_tree(tmp_path, courier=True)
    assert "settings" in [i["id"] for i in ih.leftovers(str(sett), courier=True)["items"]]
    ih.remove(str(sett), ["settings", "voice", "clips"], courier=True)
    assert not (sett / "courier.json").exists() and not (sett / "voice").exists() and not (sett / "voice-downloads").exists()
    assert (models / "models" / "m.gguf").exists() and (own / "mine.gguf").exists()


def test_a_folder_that_cannot_be_removed_is_reported_not_raised(tmp_path, monkeypatch):
    sett, models, own, skyrim = _leftover_tree(tmp_path)
    monkeypatch.setattr(ih, "_force_remove", lambda path, failed: failed.append({"path": str(path), "reason": "in use"}))
    r = ih.remove(str(sett), ["clips"])
    assert r["failed"] and r["failed"][0]["path"].endswith("clips") and r["failed"][0]["reason"] == "in use"


def test_the_helper_prints_one_line_of_json_and_the_couriers_copy_runs_without_the_multiproxy_only_modules(tmp_path):
    code = ("import sys; sys.modules['settings_files'] = None; sys.argv = ['x', 'seed-courier', '--config-dir', r'%s', '--port', '8110']; "
            "import installer_helper; sys.exit(installer_helper.main())" % (tmp_path / "c"))
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True and (tmp_path / "c" / "courier.json").exists()


# ---- the build ---------------------------------------------------------------------------------------------------------------------------------
def test_the_programs_file_list_is_followed_from_its_imports_not_typed_in():
    mods = bd.program_modules(bd.PRODUCTS["multiproxy"]["roots"])
    for need in ("multiproxy", "multiproxy_tray", "local_channel", "settings_files", "settings_ui", "voice_engine", "voice_downloads", "voice_service", "voice_wiring",
                 "file_transfer", "model_transfer", "hidden_process", "installer_helper"):
        assert need in mods, need
    assert "tested_settings" in mods, "tested_settings.py is a program file (Routing recommended settings), not a test file"
    assert "settings_presets.json" in bd.PRODUCTS["multiproxy"]["data"], "tested_settings.py reads this file next to itself"
    assert not any(m.startswith("test_") for m in mods) and "build" not in mods
    courier = bd.program_modules(bd.PRODUCTS["courier"]["roots"])
    # The Courier runs Higgs voice on its own device (voice step 2), so it carries the engine side of voice; never the main device's side.
    assert "courier" in courier and "installer_helper" in courier and not any(m.startswith("multiproxy") for m in courier)
    assert {"voice_courier", "voice_engine", "voice_downloads"} <= set(courier) and not {"voice_service", "voice_wiring", "voice_remote"} & set(courier)


def test_third_party_packages_are_found_without_stdlib_or_program_modules():
    third = bd.third_party(bd.program_modules(bd.PRODUCTS["multiproxy"]["roots"]))
    assert {"aiohttp", "fastapi", "uvicorn", "yaml", "PIL", "pystray"} <= set(third) and "json" not in third and "voice_engine" not in third


def test_a_new_import_in_a_program_file_joins_the_list_by_itself(tmp_path):
    (tmp_path / "a.py").write_text("import b\nimport json\n")
    (tmp_path / "b.py").write_text("def f():\n    import c\n")
    (tmp_path / "c.py").write_text("import requests\n")
    (tmp_path / "unrelated.py").write_text("x = 1\n")
    assert bd.program_modules(["a"], tmp_path) == ["a", "b", "c"] and bd.third_party(["a", "b", "c"], tmp_path) == ["requests"]
    with pytest.raises(bd.BuildError):
        bd.program_modules(["missing"], tmp_path)


def test_staging_copies_exactly_the_program_and_refuses_anything_secret(tmp_path, monkeypatch):
    files = bd.stage_program("multiproxy", tmp_path / "stage")
    names = {Path(f).name for f in files}
    assert {"multiproxy.py", "multiproxy_tray.py", "help-content.md", "LICENSE-proxy.txt", "multiproxy.ini.example", "requirements-tray.txt"} <= names
    assert not (names & bd.NEVER) and not any(n.endswith(".db") or n.endswith(".log") for n in names)
    monkeypatch.setitem(bd.PRODUCTS, "evil", {"roots": ["installer_helper"], "data": {"config.example.json": "config.json"}})
    with pytest.raises(bd.BuildError) as e:
        bd.stage_program("evil", tmp_path / "stage2")
    assert "never be in an installer" in str(e.value)


def test_the_versions_are_read_from_the_programs_own_files():
    assert re.fullmatch(r"\d+\.\d+\.\d+(-beta\.\d+)?", bd.read_version(bd.PRODUCTS["multiproxy"])) and bd.read_version(bd.PRODUCTS["courier"]) == "1.%d" % __import__("courier").COURIER_BUILD


def test_the_pinned_python_is_python_orgs_value_and_the_download_must_match(tmp_path, monkeypatch):
    assert re.fullmatch(r"[0-9a-f]{64}", bd.EMBED_SHA256) and bd.EMBED_URL.startswith("https://www.python.org/ftp/python/3.14.10/")
    monkeypatch.setattr(bd, "CACHE", tmp_path)
    (tmp_path / bd.EMBED_NAME).write_bytes(b"not python")
    with pytest.raises(bd.BuildError) as e:
        bd.get_embed_zip()
    assert "didn't match python.org's published checksum" in str(e.value) and not (tmp_path / bd.EMBED_NAME).exists(), "a bad download is deleted"


def test_the_runtime_gets_the_program_folder_on_its_path(tmp_path):
    src = (ROOT / "installer" / "build.py").read_text(encoding="utf-8")
    assert 'Lib\\\\site-packages\\n..\\nimport site' in src


def test_a_missing_mod_package_or_inno_setup_says_plainly_what_to_do(tmp_path, monkeypatch):
    monkeypatch.delenv("MULTIPROXY_MOD_PACKAGE", raising=False)
    monkeypatch.setenv("MULTIPROXY_PUBLIC_REPO", str(tmp_path))
    with pytest.raises(bd.BuildError) as e:
        bd.find_mod_package()
    assert "SkyrimNetMultiProxy.zip" in str(e.value) and "isn't built yet" in str(e.value) and "--mod-package" in str(e.value)
    (tmp_path / "release-staging").mkdir()
    (tmp_path / "release-staging" / "SkyrimNetMultiProxy-Mod-3.2.0.zip").write_bytes(b"zip")
    with pytest.raises(bd.BuildError):
        bd.find_mod_package()          # an old versioned name is not the package any more
    (tmp_path / "release-staging" / "SkyrimNetMultiProxy.zip").write_bytes(b"zip")
    assert bd.find_mod_package().name == "SkyrimNetMultiProxy.zip"
    monkeypatch.setenv("ISCC", str(tmp_path / "nope.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(bd.shutil, "which", lambda n: None)
    if not Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe").exists() and not Path(r"C:\Program Files\Inno Setup 6\ISCC.exe").exists():
        with pytest.raises(bd.BuildError) as e:
            bd.find_iscc()
        assert "winget install --id JRSoftware.InnoSetup" in str(e.value)


# ---- the installer scripts: the mockup's wording, and nothing written where it must not be ----------------------------------------------------
def code_and_text(name):
    text = (ROOT / "installer" / name).read_text(encoding="utf-8")
    return re.sub(r"'\s*\+\s*[\r\n]+\s*'", "", text)          # a long sentence is written as several joined pieces; read it as the one sentence the player sees


@pytest.mark.parametrize("words", [
    "Where should MultiProxy be installed?", "Program folder", "Settings folder", "Connect it to Skyrim",
    "Skyrim connects to MultiProxy using a small mod.",
    "Install it with Vortex or Mod Organizer 2 so it acts like your other mods.",
    "The mod package (included)", "Open folder", "Copy location",
    "Click ''Open folder'' and drag the mod package into Vortex or MO2 (Install from file). You can also do this later from Settings.",
    "A few choices", "Start MultiProxy when Windows starts", "Add it to the Start menu", "Add a desktop shortcut",
    "Where to save local AI models (these are big; you can choose later)", "MultiProxy is ready. Leave the box ticked and press Finish to start.",
    "The Skyrim mod, for your mod manager", "Open the dashboard now"])
def test_the_multiproxy_installer_uses_the_mockups_words(words):
    assert words in code_and_text("multiproxy.iss")


def test_the_defaults_are_the_mockups():
    t = code_and_text("multiproxy.iss")
    assert re.search(r"ChkWindows\.Checked := True", t) and re.search(r"ChkStartMenu\.Checked := True", t) and re.search(r"ChkDesktop\.Checked := False", t)
    assert "PrivilegesRequired=lowest" in t and "DisableDirPage=yes" in t and "Flags: postinstall shellexec skipifsilent" in t


@pytest.mark.parametrize("name", ["multiproxy.iss", "courier.iss", "common.iss", "uninstall_page.iss"])
def test_it_is_a_mod_never_a_plugin_for_anything_a_player_reads(name):
    for line in code_and_text(name).splitlines():
        stripped = line.strip()
        if stripped.startswith(";") or stripped.startswith("//") or stripped.startswith("{"):
            continue
        assert "plugin" not in stripped.lower(), (name, line)


@pytest.mark.parametrize("name", ["multiproxy.iss", "courier.iss", "common.iss", "uninstall_page.iss"])
def test_nothing_is_written_into_skyrims_folder_or_a_mod_manager(name):
    t = code_and_text(name)
    for forbidden in ("Skyrim Special Edition", "\\Data\\", "SKSE", "Mod Organizer", "Vortex\\"):
        for line in t.splitlines():
            if line.strip().startswith(("Source:", "Name:", "Filename:", "Root:")) or "DestDir" in line:
                assert forbidden not in line, (name, line)
    if name not in ("common.iss", "uninstall_page.iss"):
        assert "DestDir: \"{app}" in t


def test_the_run_entry_is_the_one_the_settings_page_and_the_tray_menu_write():
    t = code_and_text("multiproxy.iss")
    assert 'ValueName: "{#AppName}"' in t and 'ValueData: """{app}\\{#StartExe}"""' in t and '#define StartExe "SkyrimNet MultiProxy.exe"' in t      # (the start program: Windows' Startup list says MultiProxy, not Python)
    assert settings_files.STARTUP_NAME == "SkyrimNet MultiProxy"
    cmd = " ".join(f'"{p}"' for p in settings_files.tray_start_command("C:\\Program"))
    assert cmd.endswith('multiproxy_tray.py"')


def test_the_courier_installers_questions_and_its_one_elevated_step():
    t = code_and_text("courier.iss")
    assert "profile=private" in t and "protocol=TCP" in t and "protocol=UDP" in t and "ShellExec('runas'" in t
    assert t.count("remoteip=LocalSubnet") == 1 and t.count('program="') == 2, "both rules: this network only, and for the Courier's own program (security review S3)"
    assert "powershell" not in t.lower().replace("no powershell", ""), "no PowerShell anywhere in the elevated steps (security review S15)"
    assert "Install folder" in t and "Settings folder" in t and "Start the Courier when Windows starts" in t
    assert "CurStep = ssInstall" in t, "the firewall answer must be known before the Run entries use it"
    assert "ClickFinish=" in t


def test_both_installers_close_a_running_program_before_an_upgrade():
    for name in ("multiproxy.iss", "courier.iss"):
        t = code_and_text(name)
        assert "function PrepareToInstall" in t and "StopRunningFrom(" in t
        assert "UsePreviousAppDir=yes" in t


# ---- the one-page uninstall (2026-10-02) ---------------------------------------------------------------------------------------------------------
PAGE_WORDING = [
    "Ticked items will be removed. Your settings and downloads are kept unless you tick them too.",
    "The program", "Skyrim, your mod manager and models you added yourself are never touched.",
    "These could not be removed because something is still using them:",
    "Higgs voice models", "Files downloaded for your other devices", "working out the size…", "'Uninstall'", "'Cancel'",
    "The Windows Firewall rule (Windows will ask for permission)",
]


def test_the_uninstall_page_wording_is_in_the_shared_script():
    t = code_and_text("uninstall_page.iss")
    for line in PAGE_WORDING:
        assert line in t, line
    for line in ("This device''s settings and pairing code", "Local AI models this Courier downloaded", "Voice clips kept on this device",
                 "Your settings and saved keys", "Local AI models MultiProxy downloaded", "Your own voice clips"):
        assert line in t, line
    assert "plugin" not in t.lower()


def test_each_installer_shows_the_page_once_and_has_no_question_per_item():
    for name, title in (("multiproxy.iss", "Uninstall SkyrimNet MultiProxy"), ("courier.iss", "Uninstall MultiProxy Courier")):
        t = code_and_text(name)
        assert "function InitializeUninstall" in t and "AskWhatToRemove('" + title + "'" in t
        assert "IDNO" not in t and "MB_DEFBUTTON2" not in t and "Choose No to keep" not in t, "the old question per item is gone"
        assert t.count("SuppressibleMsgBox(") == 0 and "describe" not in t
        assert "/REMOVE" not in t or "PAGE" in t
    assert t.count("AskWhatToRemove(") == 1


def test_the_page_defaults_keep_settings_models_and_voice_files_and_tick_only_the_program_and_the_firewall_rule():
    t = code_and_text("uninstall_page.iss")
    assert "AddUnRow(Scroll, 'program', 'The program', ProgramDir, True, True, Top)" in t, "the program: ticked and locked"
    assert "ItemLabel(Id, Courier), FirstPath(ItemBlock(Info, Id)), False, False, Top)" in t, "every leftover kind starts unticked"
    assert "(Windows will ask for permission)', '', True, False, Top)" in t, "the firewall rule starts ticked"


def test_a_silent_uninstall_removes_only_what_a_switch_names_and_an_upgrade_never_reaches_the_page():
    t = code_and_text("uninstall_page.iss")
    for switch in ("REMOVESETTINGS", "REMOVEMODELS", "REMOVEVOICE", "REMOVECLIPS", "REMOVEDOWNLOADS", "REMOVEFIREWALL"):
        assert "{param:" + switch + "|0}" in t
    for name in ("multiproxy.iss", "courier.iss"):
        c = code_and_text(name)
        assert "UninstallSilent and (ExpandConstant('{param:PAGE|0}') <> '1')" in c
        assert "UseThePageFromTheAppsList(" in c
        # an upgrade runs the installer, which only ever closes the old program: removal lives in CurUninstallStepChanged, which the installer never calls
        assert c.count("ih_remove") == 0 and "'remove --" in c and c.index("'remove --") > c.index("procedure CurUninstallStepChanged")


# ---- the wording pass (2026-10-02): the lines that weren't in the mockup, exactly as Gemini wrote them ---------------------------------------------
MULTIPROXY_WORDING = [
    "If you used the zipped version of MultiProxy before, pick that folder to keep your keys and settings. Otherwise, leave this empty and press Next.",
    "Pick the folder that has your old MultiProxy files.",
    "Pick your old MultiProxy folder",
    "That does not look like an old MultiProxy folder. Pick the correct folder, or leave the box empty to start fresh.",
    "Leave this blank to decide when you download your first model.", "Creating the settings folder...",
    "Where to save local AI models (these are big; you can choose later)",       # unchanged: the mockup's own line
]
COURIER_WORDING = [
    "Where should the Courier be installed?", "Install folder", "Settings folder", "Let my main device reach this one",
    "Windows will ask for permission in a pop-up. Click Yes so your main device can find this Courier.",
    "This device is now a MultiProxy Courier. A window with your code is open. Leave this device running, go to MultiProxy on your main device, click Other devices, enter the code and click Pair.",
    "Setting up...", "Getting ready...", "Starting the Courier...",
    "Where to save local AI models (these are big; you can choose later)",
]


def test_the_wording_pass_is_in_the_scripts_and_the_old_lines_are_gone():
    for name, lines in (("multiproxy.iss", MULTIPROXY_WORDING), ("courier.iss", COURIER_WORDING)):
        t = code_and_text(name)
        for line in lines:
            assert line in t, (name, line)
        for old in ("If you used an older MultiProxy", "We couldn''t find any settings in that folder", "lives here", "so a later install", "Empty means", "Pick that older folder", "Pick the folder of your older", "Setting up this device's settings",
                    "Making this device's pairing code", "so there is nothing to bring along", "Windows will ask for permission to add the rule"):
            assert old not in t, (name, old)


# ---- the page is sized from what is on it, and no text is cut off at any scaling (2026-10-02) ------------------------------------------------------
def test_the_page_window_is_sized_from_its_rows_and_scaled_once():
    """The window was about 1560 x 1640 on a high-DPI screen: CreateCustomForm scales the size it is given, and it was given an already scaled one. Now it is given
    a token size, and the real one (a normal wizard-page width, then heading + sentence + rows + quiet line + buttons) is set in pixels afterwards."""
    t = code_and_text("uninstall_page.iss")
    assert "CreateCustomForm(ScaleX(100), ScaleY(100), True, False)" in t and "CreateCustomForm(ScaleX(560), ScaleY(480)" not in t
    assert "UnForm.ClientWidth := ScaleX(580)" in t and "UnForm.ClientHeight := Surface.Height + BtnH" in t
    assert "Scroll.Height := ListH" in t and "ListH := Top" in t, "the rows' own height decides the window's"
    assert "SPI_GETWORKAREA" in t and "MaxListH" in t, "never taller than the screen's work area"
    assert "TNewCheckListBox" in t, "rows that would not fit the screen scroll while the heading and buttons stay put"


def test_no_text_control_on_the_page_has_a_fixed_pixel_height():
    """A tick box's own height was left at its default (17 px), shorter than the font at 150% scaling, so the lower part of p, g and y was cut off. Every
    height is read from the font as drawn (UnLineH), never a fixed number."""
    import re
    t = code_and_text("uninstall_page.iss")
    assert "UnLineH := UnMeter.Height" in t
    for line in t.splitlines():
        m = re.search(r"\.Height := (.*);", line)
        if m and not line.lstrip().startswith("{"):
            assert not re.fullmatch(r"ScaleY\(\d+\)", m.group(1).strip()), line
    assert "UnChecks[N].Height := UnLineH + ScaleY(6)" in t and "UnDetails[N].Height := UnLineH + ScaleY(2)" in t
    assert "Ok.Height := BtnH" in t and "Cancel.Height := BtnH" in t


def test_rows_have_a_clear_gap_and_a_long_path_gives_up_its_middle_with_the_whole_path_as_hover_text():
    t = code_and_text("uninstall_page.iss")
    assert "Top := Top + ScaleY(16)" in t, "a bigger gap between rows than inside one"
    assert "function ShortPath" in t and "UnDetails[N].Hint := UnPlaces[N]" in t and "UnDetails[N].ShowHint := True" in t


def test_the_program_row_is_measured_like_the_others(tmp_path):
    prog = tmp_path / "prog"
    prog.mkdir()
    (prog / "a.bin").write_bytes(b"x" * 1000)
    out = ih.leftovers(str(tmp_path / "nothing-here"), sizes=True, program_dir=str(prog))
    assert out["items"][0] == {"id": "program", "paths": [str(prog)], "bytes": 1000}
    assert ih.leftovers(str(tmp_path / "nothing-here"), sizes=False, program_dir=str(prog))["items"] == [], "no sizes pass, no program entry"
    assert "--program-dir" in code_and_text("uninstall_page.iss") and "UnIds[I] <> 'firewall'" in code_and_text("uninstall_page.iss")


# ---- the installers follow Windows' light or dark setting (2026-10-02) -------------------------------------------------------------------------
def test_both_installers_follow_the_windows_app_setting_with_the_built_in_style():
    """Inno Setup 6.7.3: `WizardStyle=modern dynamic` (light or dark by the system, checked once at start; covers the wizard, message boxes, the uninstaller)."""
    for name in ("multiproxy.iss", "courier.iss"):
        t = (Path(__file__).resolve().parent.parent / "installer" / name).read_text(encoding="utf-8")
        assert re.search(r"^WizardStyle=modern dynamic\s*$", t, re.M), name
        assert "WizardStyle=modern\n" not in t and "WizardStyle=dark" not in t and "NOSTYLE" not in t, "no forced style and no option for the player"


def test_our_own_text_colours_come_from_one_shared_function_never_a_literal():
    """The built-in style does not reach a colour we pick ourselves. Every colour in the scripts is HintColor (common.iss), chosen by IsDarkInstallMode."""
    root = Path(__file__).resolve().parent.parent / "installer"
    common = code_and_text("common.iss")
    assert "function HintColor: TColor" in common and "IsDarkInstallMode" in common
    for name in ("multiproxy.iss", "courier.iss", "uninstall_page.iss"):
        t = "\n".join(l for l in (root / name).read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("{"))
        for m in re.finditer(r"\.Color := ([^;]+);", t):
            assert m.group(1).strip() in ("HintColor", "WarnColor"), (name, m.group(0))
        assert not re.search(r"\bcl[A-Z][A-Za-z]+\b", re.sub(r"\{[^}]*\}", "", t)), name        # no clGrayText, clWindow, ... (system colours do not follow the dark style)
        assert not re.search(r"\$00[0-9A-Fa-f]{6}", t), name
    # the only literals are the two greys inside HintColor, readable on each style (about 6:1 on white, 7:1 on near-black)
    assert re.findall(r"\$00[0-9A-Fa-f]{6}", common) == ["$00B0B0B0", "$00595959", "$001A94E8", "$00064A8A"]
    # ...and the two ambers of WarnColor (the dashboard's own warning colours: #e8941a on dark, #8a4a06 on light), for a line that says something is wrong but nothing is lost
    assert "function WarnColor: TColor" in common and "IsDarkInstallMode" in common.split("function WarnColor: TColor")[1].split("end;")[0]


def test_the_uninstall_page_takes_its_background_from_the_form_not_a_default_light_panel():
    t = code_and_text("uninstall_page.iss")
    assert "Surface.ParentColor := True" in t and "Holder.ParentColor := True" in t and "UnList.ParentColor := True" in t


def test_tick_boxes_on_the_wizard_pages_are_as_tall_as_the_font():
    for name in ("multiproxy.iss", "courier.iss"):
        t = code_and_text(name)
        for box in re.findall(r"(Chk\w+)\.Top := ", t):
            assert f"{box}.Height := CheckHeight;" in t, (name, box)
    assert "function CheckHeight" in code_and_text("common.iss")


# ---------------------------------------------------------------------------------------------------------------------------------
# The one Skyrim mod file for everyone: a single DLL, no extra library, no Address Library, MIT like the rest of MultiProxy
# ---------------------------------------------------------------------------------------------------------------------------------
def _mod_zip(tmp_path, entries):
    import zipfile
    p = tmp_path / "SkyrimNetMultiProxy.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return p


_GOOD_MOD = {bd.MOD_DLL: b"MZ\x00dll 4.0.0"}


def test_the_mod_package_is_exactly_the_one_dll(tmp_path):
    assert bd.MOD_DLL == "SKSE/Plugins/SkyrimNetMultiProxy.dll" and bd.MOD_PACKAGE_NAME == "SkyrimNetMultiProxy.zip"
    bd.check_mod_package(_mod_zip(tmp_path, _GOOD_MOD))                                                   # the right shape passes
    with pytest.raises(bd.BuildError) as e:
        bd.check_mod_package(_mod_zip(tmp_path, {"SKSE/Plugins/Other.dll": b"MZ"}))
    assert "Missing" in str(e.value) and "SkyrimNetMultiProxy.dll" in str(e.value)
    with pytest.raises(bd.BuildError) as e:
        bd.check_mod_package(_mod_zip(tmp_path, {**_GOOD_MOD, "SKSE/Plugins/SkyrimNetMultiProxy.ini": b"x"}))
    assert "Not allowed" in str(e.value) and "SkyrimNetMultiProxy.ini" in str(e.value), "an extra file is refused"
    (tmp_path / "x.zip").write_bytes(b"not a zip")
    with pytest.raises(bd.BuildError):
        bd.check_mod_package(tmp_path / "x.zip")


def test_a_licence_folder_in_the_mod_package_is_refused(tmp_path):
    with pytest.raises(bd.BuildError) as e:
        bd.check_mod_package(_mod_zip(tmp_path, {**_GOOD_MOD, "SKSE/Plugins/SkyrimNetMultiProxy/LICENSE-GPL-3.0.txt": b"text"}))
    assert "Not allowed" in str(e.value) and "LICENSE-GPL-3.0.txt" in str(e.value)


def test_a_mod_file_with_a_beta_label_commonlib_or_an_address_library_name_is_refused(tmp_path):
    for word, said in (("4.0.0-beta.1", "beta"), ("CommonLibSSE", "CommonLibSSE"), ("versionlib-1-6-1170-0.bin", "Address Library")):
        for raw in (word.encode("ascii"), word.encode("utf-16-le")):
            with pytest.raises(bd.BuildError) as e:
                bd.check_mod_package(_mod_zip(tmp_path, {bd.MOD_DLL: b"MZ" + raw}))
            assert said in str(e.value), word
    bd.check_mod_package(_mod_zip(tmp_path, {bd.MOD_DLL: b"MZ metadata alphabet library"}))               # ordinary words are not labels


def test_the_installer_offers_one_mod_file_and_no_second_download_or_address_library_step():
    for f in ("multiproxy.iss", "common.iss", "build.py", "build-installers.ps1"):
        text = (ROOT / "installer" / f).read_text(encoding="utf-8")
        assert "Skyrim 1.7" not in text and "Skyrim-1.7" not in text and "1.7 mod" not in text, f
        if f != "build.py":                      # build.py names the Address Library only in the check that refuses it
            assert "address library" not in text.lower(), f
    assert (ROOT / "installer" / "multiproxy.iss").read_text(encoding="utf-8").count('Source: "{#ModPackage}"; DestDir: "{app}\\mod"') == 1


def test_the_notices_say_the_mod_is_part_of_multiproxy_written_against_skses_interface():
    md = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert "CommonLibSSE" not in md and "GPL" not in md and "Skyrim-1.7" not in md
    section = md.split("## SKSE (Skyrim Script Extender)", 1)[1].split("\n## ", 1)[0]
    assert "MIT" in section and "SkyrimNetMultiProxy.zip" in section and "plugin interface" in section
    assert "https://skse.silverlock.org/" in section and "https://github.com/ianpatt/skse64" in section
    assert "Ian Patterson" in section
