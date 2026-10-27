"""Starting the Courier without running setup again, and updating it in its own folder (2026-10-01).

The director unpacked a newer package into the folder the Courier was installed in and found nothing to double-click to start it; and
running "Set up this PC" there stopped with `Cannot overwrite the item ...\\courier.py with itself.` These tests cover `Start Courier.bat`
(starts hidden, a second run doesn't start another, a missing Python package gets a plain message), setup run from the install folder, the
Start menu shortcut, and the Linux `multiproxy-courier start|stop|restart|status` command and in-place update.
"""
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_pkg", ROOT / "scripts" / "build_courier_package.py")
build_pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_pkg)

BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
WIN = sys.platform == "win32"
BAT_STEP = 'This update needs one more step. Double-click "Set up this PC" in this folder.'


def run_captured(cmd, cwd=None, timeout=120, **kw):
    """Like subprocess.run(capture_output=True), but through files: a Courier started hidden by the thing under test inherits the
    output handles, and a pipe would then wait for the Courier to exit."""
    import tempfile
    with tempfile.TemporaryFile() as out:
        r = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=timeout, **kw)
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
    return subprocess.CompletedProcess(r.args, r.returncode, text, "")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def unpack(tmp_path: Path, name: str) -> Path:
    out = tmp_path / f"{name}.zip"
    build_pkg.build(out)
    dest = tmp_path / name
    zipfile.ZipFile(out).extractall(dest)
    return dest


def kill_installed(inst: Path) -> None:
    """Stop whatever runs from this scratch install's Python (and nothing else)."""
    if not WIN:
        return
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{inst}\\.venv\\*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
                   capture_output=True)
    time.sleep(1.5)


def status(inst: Path) -> str:
    return subprocess.run([str(inst / ".venv" / "Scripts" / "python.exe"), "courier.py", "status"], cwd=inst, capture_output=True, text=True).stdout


def listeners(port: int) -> list[str]:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
                         capture_output=True, text=True).stdout
    return sorted(set(out.split()))


# ---- the package and the setup lists ------------------------------------------------------------------------------------------
class TestPackageCarriesIt:
    def test_the_package_and_the_setup_copy_list_both_include_start_courier(self):
        assert "Start Courier.bat" in build_pkg.SETUP_FILES
        ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
        assert "@('Start Courier.bat')" in ps1.split("function Get-ProgramFiles")[1].split("\n}\n")[0], "setup copies it into the install folder on every update"

    def test_it_is_crlf_and_plain_ascii_in_the_zip(self, tmp_path):
        out = tmp_path / "p.zip"
        build_pkg.build(out)
        data = zipfile.ZipFile(out).read("Start Courier.bat")
        data.decode("ascii")
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")

    def test_it_says_the_exact_words(self):
        bat = (ROOT / "courier/Start Courier.bat").read_text(encoding="ascii")
        for line in ("Courier is already running quietly by your clock.", BAT_STEP,
                     "Courier isn't set up here yet. Double-click \"Set up this PC\" first.",
                     "Courier didn't start. Double-click \"Set up this PC\" to fix it."):
            assert line in bat, line

    def test_the_readme_has_the_starting_and_updating_section(self):
        txt = (ROOT / "courier/README.txt").read_text(encoding="utf-8")
        assert "STARTING AND UPDATING" in txt and "multiproxy-courier start" in txt and "Start menu" in txt
        assert "shortcut in the Start menu" in txt.split("TO REMOVE IT (Windows)")[1].split("TO REMOVE IT (Linux)")[0], "the removal steps cover the shortcut"


# ---- Start Courier.bat, for real, in a scratch install on a spare port -----------------------------------------------------------
@pytest.mark.skipif(not WIN, reason="Windows batch file")
class TestStartCourierBat:
    @pytest.fixture
    def inst(self, tmp_path, monkeypatch):
        """A scratch install: the packaged files, a private Python environment that can see this Python's packages, a spare port."""
        monkeypatch.setenv("MULTIPROXY_COURIER_BIND", "127.0.0.1")        # a scratch Courier is a brand-new program to Windows Firewall: it must listen on this device only, never on the home network (that asks a firewall question)
        pkg = unpack(tmp_path, "pkg")
        inst = tmp_path / "inst"
        shutil.copytree(pkg, inst)
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(inst / ".venv")], check=True, capture_output=True)
        (inst / "requirements-courier.txt").write_text("aiohttp\n")                    # (the tray packages aren't in this test Python)
        py = str(inst / ".venv" / "Scripts" / "python.exe")
        subprocess.run([py, "courier.py", "code", "--port", str(free_port()), "--start-with-windows", "off"], cwd=inst, check=True, capture_output=True)
        import json
        cj = json.loads((inst / "courier.json").read_text())
        cj.update(keep_awake=False, start_with_windows=False, ready_shown=True)       # (no ready window popping up in the test)
        (inst / "courier.json").write_text(json.dumps(cj))
        yield inst
        kill_installed(inst)

    def run_bat(self, inst):
        return run_captured([str(inst / "Start Courier.bat")])

    def test_it_starts_the_courier_and_a_second_run_does_not_start_another(self, inst):
        port = __import__("json").loads((inst / "courier.json").read_text())["port"]
        r = self.run_bat(inst)
        assert r.returncode == 0 and "Courier is running quietly by your clock." in r.stdout, r.stdout + r.stderr
        assert "is running" in status(inst)
        first = listeners(port)
        assert len(first) == 1
        r = self.run_bat(inst)
        assert r.returncode == 0 and "Courier is already running quietly by your clock." in r.stdout
        assert listeners(port) == first, "the same Courier, not a second copy"

    def test_it_works_when_started_from_another_folder_like_a_shortcut_does(self, inst, tmp_path):
        r = run_captured([str(inst / "Start Courier.bat")], cwd=tmp_path)
        assert r.returncode == 0 and "is running" in status(inst), r.stdout

    def test_a_newer_requirement_the_environment_lacks_gets_a_plain_message_not_a_python_error(self, inst):
        req = inst / "requirements-courier.txt"
        req.write_text(req.read_text() + "some-package-that-is-not-installed-xyz>=1.0\n")
        r = self.run_bat(inst)
        assert r.returncode == 1 and BAT_STEP in r.stdout
        assert "Traceback" not in r.stdout + r.stderr and "ModuleNotFoundError" not in r.stdout + r.stderr
        assert "is running" not in status(inst), "it did not start half-way"

    def test_a_folder_that_was_never_set_up_says_to_run_setup(self, tmp_path):
        pkg = unpack(tmp_path, "bare")
        r = subprocess.run([str(pkg / "Start Courier.bat")], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and "Courier isn't set up here yet. Double-click \"Set up this PC\" first." in r.stdout


# ---- setup run from inside the install folder (the failure the director hit) ----------------------------------------------------------
def pypi_reachable() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(PWSH is None or not WIN, reason="Windows PowerShell 7 only")
class TestSetupFromTheInstallFolder:
    def lib(self, tmp_path, body: str) -> str:
        script = tmp_path / "lib.ps1"
        script.write_text(f"$env:COURIER_INSTALL_LIB='1'; . '{ROOT / 'courier/setup-courier.ps1'}'\n{body}\n", encoding="utf-8")
        return subprocess.run([PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=90).stdout

    def test_the_setup_script_reads_the_one_shared_file_list_from_the_unpacked_package(self, tmp_path):
        pkg = unpack(tmp_path, "pkg")
        script = tmp_path / "list.ps1"
        script.write_text(f"$env:COURIER_INSTALL_LIB='1'; . '{pkg / 'setup-courier.ps1'}'\n(Get-ProgramFiles) -join '|'\n", encoding="utf-8")
        out = subprocess.run([PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=90).stdout.strip()
        import courier_files
        assert out.split("|") == courier_files.program() + list(courier_files.data()) + ["Start Courier.bat"]
        assert all((pkg / n).is_file() for n in out.split("|")), "every name it will copy is in the package"

    def test_a_package_without_its_file_list_stops_setup_with_a_plain_line(self, tmp_path):
        pkg = unpack(tmp_path, "pkg")
        (pkg / "courier-files.json").unlink()
        script = tmp_path / "list.ps1"
        script.write_text(f"$env:COURIER_INSTALL_LIB='1'; . '{pkg / 'setup-courier.ps1'}'\nGet-ProgramFiles\nWrite-Output 'carried on'\n", encoding="utf-8")
        r = subprocess.run([PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=90)
        assert "missing its file list" in r.stdout and "carried on" not in r.stdout

    def test_the_same_folder_is_recognised_by_real_path_not_by_text(self, tmp_path):
        a = tmp_path / "Inst Folder"
        a.mkdir()
        out = self.lib(tmp_path, f"""
          Write-Output ("same-trailing=" + (Test-SameFolder '{a}' '{a}\\'))
          Write-Output ("same-case=" + (Test-SameFolder '{str(a).upper()}' '{a}'))
          Write-Output ("same-dots=" + (Test-SameFolder '{a}\\sub\\..' '{a}'))
          New-Item -ItemType Directory -Force '{tmp_path / "other"}' | Out-Null
          Write-Output ("different=" + (Test-SameFolder '{tmp_path / "other"}' '{a}'))
          New-Item -ItemType Junction -Path '{tmp_path / "link"}' -Target '{a}' | Out-Null
          Write-Output ("junction=" + (Test-SameFolder '{tmp_path / "link"}' '{a}'))""")
        assert "same-trailing=True" in out and "same-case=True" in out and "same-dots=True" in out and "different=False" in out and "junction=True" in out, out

    def test_setup_run_from_inside_an_install_treats_that_folder_as_the_install(self, tmp_path):
        inst = tmp_path / "Somewhere Odd"
        (inst / ".venv" / "Scripts").mkdir(parents=True)
        (inst / ".venv" / "Scripts" / "python.exe").write_bytes(b"x")
        (inst / "courier.py").write_text("x")
        shutil.copy(ROOT / "courier/setup-courier.ps1", inst / "setup-courier.ps1")
        script = tmp_path / "t.ps1"
        script.write_text(f"$env:APPDATA='{tmp_path / 'AD'}'; $env:LOCALAPPDATA='{tmp_path / 'LD'}'; $env:COURIER_INSTALL_LIB='1'\n. '{inst / 'setup-courier.ps1'}'\n"
                          f"$r = Resolve-InstallDir '' $true {{ throw 'must not ask' }}\nWrite-Output \"DIR=$r\"\n", encoding="utf-8")
        out = subprocess.run([PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=90).stdout
        assert f"DIR={inst}" in out, out

    def test_the_copy_is_skipped_when_it_is_the_same_folder_and_the_line_is_said(self):
        ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
        assert "$inPlace = Test-SameFolder $source $InstallDir" in ps1 and 'Step "Updating Courier in this folder."' in ps1
        body = ps1.split("$inPlace = Test-SameFolder")[1]
        assert body.index("if ($inPlace)") < body.index("Copy-Item"), "the copy only happens on the other branch"

    @pytest.mark.skipif(not pypi_reachable(), reason="needs the internet to set up a Python environment")
    def test_end_to_end_a_newer_package_copied_over_the_install_is_updated_in_place_and_started(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MULTIPROXY_COURIER_BIND", "127.0.0.1")        # (this device only: a scratch Courier must not open the home network, which asks a firewall question)
        port = free_port()
        menu = tmp_path / "menu"
        pkg = unpack(tmp_path, "pkg")
        inst = tmp_path / "inst"
        base = ["-Yes", "-NoFirewall", "-NoAutoStart", "-Port", str(port), "-StartMenuDir", str(menu)]
        try:
            r = run_captured([PWSH, "-NoProfile", "-File", str(pkg / "setup-courier.ps1"), "-InstallDir", str(inst), "-NoStart", *base], cwd=pkg, timeout=900)
            assert r.returncode == 0, r.stdout + r.stderr
            assert (inst / "Start Courier.bat").is_file() and (menu / "MultiProxy Courier.lnk").is_file(), "setup puts the bat and the Start menu shortcut in place"
            import json
            cj = json.loads((inst / "courier.json").read_text())
            cj["ready_shown"] = True
            (inst / "courier.json").write_text(json.dumps(cj))
            marker = (inst / "courier.py").read_text() + "\n# NEW VERSION MARKER\n"
            (pkg / "courier.py").write_text(marker)                         # "a newer package", copied over the install folder by hand
            for f in pkg.iterdir():
                if f.is_file():
                    shutil.copy2(f, inst / f.name)
            r = run_captured([PWSH, "-NoProfile", "-File", str(inst / "setup-courier.ps1"), *base], cwd=inst, timeout=900)
            assert r.returncode == 0, r.stdout + r.stderr
            assert "Updating Courier in this folder." in r.stdout and "Cannot overwrite" not in r.stdout + r.stderr
            assert "is running" in status(inst)
            assert (inst / "courier.py").read_text().endswith("# NEW VERSION MARKER\n"), "the new files are the ones that run"
            assert json.loads((inst / "courier.json").read_text())["code"] == cj["code"], "the pairing code and settings are kept"
        finally:
            kill_installed(inst)


# ---- Linux: the multiproxy-courier command and the in-place update ---------------------------------------------------------------------
def posix(p) -> str:
    p = Path(p)
    if not WIN:
        return str(p)
    return subprocess.run([BASH, "-c", 'cygpath -u "$1"', "_", str(p)], capture_output=True, text=True).stdout.strip()


def sh(script: str, env_extra=None, cwd=None):
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env={**os.environ, **(env_extra or {})}, cwd=cwd, timeout=60)
    return out.stdout + out.stderr, out.returncode


@pytest.mark.skipif(BASH is None, reason="bash isn't installed")
class TestLinuxLauncherAndInPlaceUpdate:
    def load(self, tmp_path, body: str, self_dir: Path = None):
        """Load install-courier.sh's functions (as if the script sat in `self_dir`) and run `body`."""
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        src = (ROOT / "courier/install-courier.sh").read_text(encoding="utf-8")
        copy = (self_dir or tmp_path / "script") / "install-courier.sh"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(src, encoding="utf-8", newline="\n")
        return sh(f'export HOME="{posix(home)}" COURIER_INSTALL_LIB=1\nset -u\nsource "{posix(copy)}"\nASSUME_YES=no\nhave_keyboard() {{ return 1; }}\n{body}')

    def fake_install(self, d: Path, running=True):
        (d / ".venv" / "bin").mkdir(parents=True)
        py = d / ".venv" / "bin" / "python"
        py.write_text("#!/bin/sh\n" + ('echo "MultiProxy Courier is running (on 8110)."\n' if running else 'echo "MultiProxy Courier is not running."\n') + 'echo "$@" >> "$(dirname "$0")/calls.log"\n')
        py.chmod(0o755)
        (d / "courier.py").write_text("x")

    def test_the_launcher_has_start_stop_restart_and_status(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        out, rc = self.load(tmp_path, f'INSTALL_DIR="{posix(inst)}"\nwrite_launcher\ntest -x "$INSTALL_DIR/multiproxy-courier" && test -e "$HOME/.local/bin/multiproxy-courier" && echo LAUNCHER-OK')
        assert "LAUNCHER-OK" in out, out
        text = (inst / "multiproxy-courier").read_text()
        for word in ("start)", "stop)", "restart)", "status)", "Usage: multiproxy-courier start|stop|restart|status"):
            assert word in text

    def test_with_a_systemd_service_each_word_goes_to_it(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        self.fake_install(inst)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "systemctl.log"
        fake = bindir / "systemctl"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{posix(log)}"\n[ "$2" = is-active ] && echo active\nexit 0\n')
        fake.chmod(0o755)
        env = {"PATH": posix(bindir) + ":" + os.environ["PATH"]}
        self.load(tmp_path, f'INSTALL_DIR="{posix(inst)}"\nwrite_launcher')
        for word in ("start", "stop", "restart"):
            out, rc = sh(f'L="{posix(inst)}/multiproxy-courier"; "$L" {word}', env)
            assert rc == 0, out
        calls = log.read_text()
        assert "--user start multiproxy-courier.service" in calls and "--user stop multiproxy-courier.service" in calls
        out, rc = sh(f'L="{posix(inst)}/multiproxy-courier"; "$L" status', env)
        assert "is running" in out and "Service: active" in out, out
        out, rc = sh(f'L="{posix(inst)}/multiproxy-courier"; "$L" nonsense', env)
        assert rc == 2 and "Usage: multiproxy-courier start|stop|restart|status" in out

    def test_an_unknown_word_or_no_word_prints_the_usage(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        self.fake_install(inst)
        self.load(tmp_path, f'INSTALL_DIR="{posix(inst)}"\nwrite_launcher')
        out, rc = sh(f'L="{posix(inst)}/multiproxy-courier"; "$L"')
        assert rc == 2 and "Usage:" in out

    def test_a_script_sitting_in_an_install_folder_treats_it_as_the_install_and_skips_the_download(self, tmp_path):
        inst = tmp_path / "Odd Place"
        self.fake_install(inst)
        out, _ = self.load(tmp_path, 'INSTALL_DIR=""\nASSUME_YES=yes\nresolve_install_dir\necho "DIR=$INSTALL_DIR"\nin_place && echo IN-PLACE', self_dir=inst)
        assert f"DIR={posix(inst)}" in out and "IN-PLACE" in out, out

    def test_a_script_run_from_somewhere_else_still_downloads_and_copies(self, tmp_path):
        inst = tmp_path / "inst"
        self.fake_install(inst)
        out, _ = self.load(tmp_path, f'INSTALL_DIR="{posix(inst)}"\nin_place && echo IN-PLACE || echo COPY')
        assert "COPY" in out and "IN-PLACE" not in out

    def test_the_download_and_copy_only_happen_on_the_other_branch(self):
        sh_src = (ROOT / "courier/install-courier.sh").read_text(encoding="utf-8")
        assert 'step "Updating Courier in this folder."' in sh_src
        body = sh_src.split("if in_place; then")[1]
        assert body.index("else") < body.index("curl -fsSL") and body.index("curl -fsSL") < body.index('cp -r "$SRC"/. "$INSTALL_DIR"/') < body.index("write_launcher")


# ---- the ready window after every install (2026-10-02) ------------------------------------------------------------------------------------------
class TestReadyWindowEveryInstall:
    def test_who_gets_the_window(self):
        import courier as c
        want = c.ready_window_wanted
        assert want(False, False, False, "win32") is True, "the first start ever"
        assert want(True, False, False, "win32") is False, "a later start by itself (start with Windows) never shows it"
        assert want(True, True, False, "win32") is True, "the installer / setup script asks: it opens even though an earlier Courier on this device already showed it"
        assert want(False, True, True, "win32") is False and want(True, True, True, "win32") is False, "--no-ready-window always wins"
        assert want(False, True, False, "linux") is False, "Linux has no such window"

    def test_the_installer_and_the_setup_script_ask_and_start_with_windows_does_not(self):
        import courier as c
        iss = (ROOT / "installer" / "courier.iss").read_text(encoding="utf-8")
        assert "--show-ready-window" in iss.split("function RunArgs")[1].split("end;")[0]
        assert "'--show-ready-window'" in (ROOT / "courier" / "setup-courier.ps1").read_text(encoding="utf-8")
        assert "show-ready-window" not in c.startup_command(Path("C:/somewhere/settings"))

    def test_a_start_that_asks_opens_the_window_even_when_it_was_shown_before(self, monkeypatch, tmp_path):
        import courier as c
        started = []
        monkeypatch.setattr(c.hidden_process, "popen", lambda cmd, **kw: started.append(cmd))
        assert c.launch_ready_window(tmp_path) is True
        assert started and started[0][-3] == "ready-window" and started[0][-2] == "--config-dir" and started[0][-1] == str(tmp_path)

    def test_a_window_that_cannot_start_is_written_to_the_couriers_log(self, monkeypatch, tmp_path):
        import courier as c

        def boom(cmd, **kw):
            raise OSError("no desktop")
        monkeypatch.setattr(c.hidden_process, "popen", boom)
        assert c.launch_ready_window(tmp_path) is False
        assert "The ready window couldn't be shown: no desktop" in (tmp_path / "courier.log").read_text(encoding="utf-8")

    def test_a_window_that_fails_inside_its_own_process_says_so_in_the_log(self, monkeypatch, tmp_path):
        import courier as c
        import courier_ui

        def boom(settings, **kw):
            raise RuntimeError("no display")
        monkeypatch.setattr(courier_ui, "show_ready_window", boom)
        assert c.main(["ready-window", "--config-dir", str(tmp_path)]) == 1
        assert "The ready window couldn't be shown: RuntimeError: no display" in (tmp_path / "courier.log").read_text(encoding="utf-8")

    def test_the_window_still_says_the_computers_name_and_the_code(self):
        import courier_ui
        t = courier_ui.ready_window_text("ABC-123", {"courier": {"name": "laptop"}, "programs": []})
        assert t["code"] == "ABC-123" and any("laptop" in s["text"] for s in t["steps"])
