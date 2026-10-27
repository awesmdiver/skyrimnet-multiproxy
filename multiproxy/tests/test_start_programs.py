"""Windows' Startup list names MultiProxy and the Courier by name, not as "Python" (2026-10-26). Each installed copy has a small start program of its own (installer/launcher/):
its version resource says who it is, and all it does is start `python\\pythonw.exe <script> <arguments>` and exit. The Run entry, the shortcuts and the installer's own start
use it; an older entry that points at pythonw.exe is rewritten. Everything here happens in a scratch folder, with a stand-in `pythonw.exe` and a stand-in registry."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import courier
import courier_system as cs
import settings_files

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer" / "launcher"))
import build_launcher as bl                      # noqa: E402

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own program files")


def _msvc():
    try:
        bl.msvc_env()
        return True
    except bl.LauncherError:
        return False


needs_msvc = pytest.mark.skipif(sys.platform != "win32" or not _msvc(), reason="needs the Microsoft C++ build tools (cl.exe and rc.exe)")

STAND_IN_C = r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
int WINAPI wWinMain(HINSTANCE i, HINSTANCE p, LPWSTR line, int show)
{
    wchar_t cwd[MAX_PATH * 2];
    FILE *f;
    GetCurrentDirectoryW(MAX_PATH * 2, cwd);
    f = _wfopen(L"seen.txt", L"w, ccs=UTF-8");
    if (f) { fwprintf(f, L"%s\n%s\n%lu\n", GetCommandLineW(), cwd, GetCurrentProcessId()); fclose(f); }
    return 0;
}
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Both start programs and a stand-in pythonw.exe, built once."""
    d = tmp_path_factory.mktemp("start-programs")
    out = {}
    for product in ("multiproxy", "courier"):
        out[product] = bl.build(product, "4.0.0" if product == "multiproxy" else "1.10", d / bl.PRODUCTS[product]["exe"], work=d / ("w-" + product))
    (d / "stand.c").write_text(STAND_IN_C, encoding="utf-8")
    env = bl.msvc_env()
    r = subprocess.run(["cl", "/nologo", "/MT", "/DUNICODE", "/D_UNICODE", f"/Fo{d}\\stand.obj", f"/Fe{d}\\stand.exe", str(d / "stand.c"), "/link", "/SUBSYSTEM:WINDOWS"],
                       cwd=str(d), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out["stand"] = d / "stand.exe"
    return out


def _version_info(path: Path) -> dict:
    ps = "(Get-Item -LiteralPath $env:START_PROGRAM).VersionInfo | Select-Object FileDescription,CompanyName,ProductName,FileVersion,OriginalFilename | ConvertTo-Json"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60, env=dict(os.environ, START_PROGRAM=str(path)))
    import json
    return json.loads(r.stdout)


@needs_msvc
def test_each_start_program_says_who_it_is_in_its_version_resource(built):
    for product, name, version in (("multiproxy", "SkyrimNet MultiProxy", "4.0.0"), ("courier", "MultiProxy Courier", "1.10")):
        info = _version_info(built[product])
        assert info["FileDescription"] == name and info["ProductName"] == name and info["CompanyName"] == name, info
        assert info["FileVersion"] == version and info["OriginalFilename"] == name + ".exe"
        assert info["FileDescription"] != "Python" and "Python" not in info["CompanyName"]
        assert built[product].stat().st_size < 600_000, "a small program"


def _install_like(tmp_path, built, product):
    app = tmp_path / "app dir with spaces"
    (app / "python").mkdir(parents=True)
    shutil.copyfile(built["stand"], app / "python" / "pythonw.exe")           # the stand-in for the bundled pythonw.exe
    exe = app / bl.PRODUCTS[product]["exe"]
    shutil.copyfile(built[product], exe)
    return app, exe


def _wait_seen(app):
    seen = app / "seen.txt"
    deadline = time.time() + 15
    while time.time() < deadline and not (seen.exists() and seen.read_text(encoding="utf-8-sig").count("\n") >= 3):
        time.sleep(0.1)
    assert seen.exists(), "the start program never started pythonw"
    return seen.read_text(encoding="utf-8-sig").splitlines()


@needs_msvc
def test_it_starts_pythonw_with_the_script_and_the_given_arguments_in_its_own_folder_and_exits(tmp_path, built):
    app, exe = _install_like(tmp_path, built, "courier")
    t0 = time.time()
    p = subprocess.Popen([str(exe), "run", "--config-dir", r"C:\some folder\settings", "--show-ready-window"], cwd=str(tmp_path))
    assert p.wait(10) == 0 and time.time() - t0 < 5, "it starts the program and exits at once"
    cmd, cwd, pid = _wait_seen(app)
    assert cmd == f'"{app}\\python\\pythonw.exe" "{app}\\courier.py" run --config-dir "C:\\some folder\\settings" --show-ready-window'
    assert cwd == str(app), "its own folder is the working directory"
    assert int(pid) != p.pid, "the process that runs is pythonw's own, not the start program (every recorded process number is the real one)"


@needs_msvc
def test_the_main_program_start_runs_the_tray_script_and_keeps_its_arguments_exactly(tmp_path, built):
    app, exe = _install_like(tmp_path, built, "multiproxy")
    assert subprocess.run([str(exe), "--from-skyrim"], cwd=str(tmp_path), timeout=10).returncode == 0
    assert _wait_seen(app)[0] == f'"{app}\\python\\pythonw.exe" "{app}\\multiproxy_tray.py" --from-skyrim'
    (app / "seen.txt").unlink()
    assert subprocess.run([str(exe)], cwd=str(tmp_path), timeout=10).returncode == 0
    assert _wait_seen(app)[0] == f'"{app}\\python\\pythonw.exe" "{app}\\multiproxy_tray.py"', "no arguments: none added"


@needs_msvc
def test_with_no_bundled_python_it_does_nothing_and_says_so_by_its_exit_code(tmp_path, built):
    exe = tmp_path / "SkyrimNet MultiProxy.exe"
    shutil.copyfile(built["multiproxy"], exe)
    assert subprocess.run([str(exe)], cwd=str(tmp_path), timeout=10).returncode == 1
    assert not (tmp_path / "seen.txt").exists()


# ---- what Windows is told to run ----
def _fake_install(tmp_path, with_launcher=True):
    app = tmp_path / "MultiProxy"
    (app / "python").mkdir(parents=True)
    (app / "python" / "pythonw.exe").write_bytes(b"x")
    (app / "multiproxy_tray.py").write_text("", encoding="utf-8")
    if with_launcher:
        (app / settings_files.LAUNCHER_NAME).write_bytes(b"x")
    return app


@win
def test_the_startup_command_is_the_start_program_when_it_is_there_and_the_old_command_when_it_is_not(tmp_path):
    app = _fake_install(tmp_path)
    assert settings_files.startup_command(str(app)) == f'"{app}\\SkyrimNet MultiProxy.exe"'
    bare = _fake_install(tmp_path / "bare", with_launcher=False)
    assert settings_files.startup_command(str(bare)).endswith('multiproxy_tray.py"') and "SkyrimNet MultiProxy.exe" not in settings_files.startup_command(str(bare))
    assert settings_files.run_entry(str(app), registry=_FakeReg()).command == f'"{app}\\SkyrimNet MultiProxy.exe"', "the Settings switch and the tray menu write this"
    assert settings_files.tray_start_command(str(app))[0].lower().endswith("python.exe") or settings_files.tray_start_command(str(app))[0].lower().endswith("pythonw.exe"), \
        "the Skyrim mod's pointer file still starts the tray app directly: that one is not a Startup item"


@win
def test_the_couriers_own_startup_command_uses_its_start_program_beside_courier_py(tmp_path, monkeypatch):
    fake = tmp_path / "app"
    (fake / "python").mkdir(parents=True)
    (fake / "python" / "pythonw.exe").write_bytes(b"x")
    (fake / "MultiProxy Courier.exe").write_bytes(b"x")
    monkeypatch.setattr(courier, "__file__", str(fake / "courier.py"))
    assert courier.startup_command() == f'"{fake}\\MultiProxy Courier.exe" run'
    other = tmp_path / "settings elsewhere"
    assert courier.startup_command(other) == f'"{fake}\\MultiProxy Courier.exe" run --config-dir "{other.resolve()}"'
    (fake / "MultiProxy Courier.exe").unlink()
    assert courier.startup_command().startswith('"') and "pythonw" in courier.startup_command().lower() or "python" in courier.startup_command().lower()


class _FakeReg:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self, **values):
        self.values = dict(values)

    def OpenKey(self, root, path, res, access):
        class K:
            def __enter__(s): return s
            def __exit__(s, *a): return False
        assert root == "HKCU" and path == cs.RUN_KEY
        return K()

    def SetValueEx(self, k, name, res, typ, value): self.values[name] = value

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def DeleteValue(self, k, name): self.values.pop(name, None)


def test_an_older_entry_on_pythonw_is_rewritten_once_and_only_this_programs_own_entry(tmp_path):
    app = r"D:\SkyrimNet MultiProxy"
    new = f'"{app}\\SkyrimNet MultiProxy.exe"'
    old = f'"{app}\\python\\pythonw.exe" "{app}\\multiproxy_tray.py"'
    reg = _FakeReg(**{"SkyrimNet MultiProxy": old, "ModPacer": r"d:\modpacer\ModPacer.exe", "Steam": "steam"})
    entry = cs.StartWithWindows(new, registry=reg, name="SkyrimNet MultiProxy")
    assert entry.refresh(app) is True
    assert reg.values["SkyrimNet MultiProxy"] == new and reg.values["ModPacer"] == r"d:\modpacer\ModPacer.exe" and reg.values["Steam"] == "steam", "no other entry is touched"
    assert entry.refresh(app) is False, "already current: nothing is written again"
    # switched off: never created; another install's entry: never taken over
    off = _FakeReg(ModPacer="x")
    assert cs.StartWithWindows(new, registry=off, name="SkyrimNet MultiProxy").refresh(app) is False and "SkyrimNet MultiProxy" not in off.values
    other = _FakeReg(**{"SkyrimNet MultiProxy": r'"E:\Elsewhere\python\pythonw.exe" "E:\Elsewhere\multiproxy_tray.py"'})
    assert cs.StartWithWindows(new, registry=other, name="SkyrimNet MultiProxy").refresh(app) is False
    assert other.values["SkyrimNet MultiProxy"].startswith('"E:\\Elsewhere')


def test_the_tray_app_rewrites_its_own_old_entry_at_start(tmp_path, monkeypatch):
    app = str(tmp_path)
    reg = _FakeReg(**{"SkyrimNet MultiProxy": f'"{app}\\python\\pythonw.exe" "{app}\\multiproxy_tray.py"'})
    import multiproxy_tray as mt
    c = mt.Controller(tmp_path, registry=reg)
    monkeypatch.setattr(settings_files, "startup_command", lambda d: f'"{d}\\SkyrimNet MultiProxy.exe"')
    assert c.refresh_run_entry() is True
    assert reg.values["SkyrimNet MultiProxy"] == f'"{app}\\SkyrimNet MultiProxy.exe"'
    assert c.refresh_run_entry() is False


# ---- the installer scripts ----
def _iss(name):
    return (ROOT / "installer" / name).read_text(encoding="utf-8")


def test_the_installer_scripts_start_and_register_the_start_program_not_pythonw():
    for name, exe in (("multiproxy.iss", "SkyrimNet MultiProxy.exe"), ("courier.iss", "MultiProxy Courier.exe")):
        text = _iss(name)
        assert f'#define StartExe "{exe}"' in text
        sections = text.split("[Icons]", 1)[1].split("[Code]", 1)[0]
        assert "{app}\\{#StartExe}" in sections
        assert "pythonw.exe" not in sections.replace('[Run]\nFilename: "{app}\\python\\python.exe"', ""), (name, "Icons, Registry and Run name the start program")
    run_value = [l for l in _iss("multiproxy.iss").splitlines() if "CurrentVersion\\Run" in l and "ValueData" in l]
    assert len(run_value) == 1 and 'ValueData: """{app}\\{#StartExe}"""' in run_value[0]
    assert "Parameters: {code:RunArgs}" not in _iss("courier.iss")
    assert "'run --config-dir ' + Quoted(SettingsFolder)" in _iss("courier.iss"), "the Courier's start program adds courier.py itself, so its arguments start at run"


def test_the_build_makes_the_start_program_for_each_product_and_it_goes_into_the_stage():
    src = (ROOT / "installer" / "build.py").read_text(encoding="utf-8")
    assert "build_launcher.build(name, version, stage / build_launcher.PRODUCTS[name][\"exe\"])" in src
    assert set(bl.PRODUCTS) == {"multiproxy", "courier"} and bl.PRODUCTS["multiproxy"]["script"] == "multiproxy_tray.py" and bl.PRODUCTS["courier"]["script"] == "courier.py"
    assert bl.version_parts("1.10") == (1, 10, 0, 0) and bl.version_parts("4.0.0") == (4, 0, 0, 0)
