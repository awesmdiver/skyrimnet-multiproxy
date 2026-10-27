"""The two Linux packages (scripts/build_linux_packages.py, linux/*) and the small program behind the `multiproxy` command (multiproxy_cli.py).

What runs for real, in containers, is tests/manual/linux_packages_containers.py (install, start, stop, logs, uninstall, in a plain and a systemd
container). These tests keep the packages honest on any device: what is in them, what must never be, the details that silently break a script on Linux
(a CR in it, no executable bit), and the command's own answers against a stand-in server.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_linux_packages", ROOT / "scripts" / "build_linux_packages.py")
blp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blp)

BASH = shutil.which("bash")
SCRIPTS = ["lib.sh", "install.sh", "multiproxy", "courier-install.sh", "multiproxy-courier"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("linux-pkg")
    return {name: blp.build(name, out) for name in blp.PRODUCTS}


def members(path: Path) -> dict:
    with tarfile.open(path) as t:
        return {m.name: m for m in t.getmembers() if m.isfile()}


def read_member(path: Path, name: str) -> bytes:
    with tarfile.open(path) as t:
        return t.extractfile(name).read()


# ---- the packages -------------------------------------------------------------------------------------------------------------------
def test_the_tarballs_are_named_with_their_own_version(built):
    assert built["multiproxy"].name == f"MultiProxy-{blp.version_of('multiproxy')}-linux.tar.gz"
    assert built["courier"].name == f"MultiProxy-Courier-{blp.version_of('courier')}-linux.tar.gz"
    assert f'PROXY_VERSION = "{blp.version_of("multiproxy")}"' in (ROOT / "multiproxy.py").read_text(encoding="utf-8")


def test_multiproxy_package_has_the_program_the_installer_the_command_a_readme_and_the_license(built):
    names = set(members(built["multiproxy"]))
    for need in ("multiproxy.py", "multiproxy_tray.py", "multiproxy_cli.py", "install.sh", "lib.sh", "multiproxy", "README.txt", "LICENSE-proxy.txt",
                 "requirements.txt", "requirements-tray.txt", "app-icon.png", "docs/help/help-content.md", "multiproxy.ini.example", "config.example.json"):
        assert f"multiproxy/{need}" in names, need
    assert all(n.startswith("multiproxy/") for n in names)                       # one folder, so unpacking never scatters files


def test_courier_package_is_the_courier_only(built):
    names = {n.split("/", 1)[1] for n in members(built["courier"])}
    for need in ("courier.py", "install.sh", "lib.sh", "multiproxy-courier", "README.txt", "LICENSE-proxy.txt", "requirements-courier.txt", "voice_engine.py", "other_pcs.py"):
        assert need in names, need
    assert "multiproxy.py" not in names and "multiproxy_tray.py" not in names and "multiproxy" not in names
    assert all(n.startswith("multiproxy-courier/") for n in members(built["courier"]))


def test_every_program_module_the_entry_points_import_is_in_the_package(built):
    for name, entry in (("multiproxy", "multiproxy.py"), ("courier", "courier.py")):
        local = {p.name for p in ROOT.glob("*.py")}
        inside = {n.split("/", 1)[1] for n in members(built[name])}
        for mod in blp._ib.program_modules(blp.PRODUCTS[name]["roots"]):
            assert f"{mod}.py" in inside and f"{mod}.py" in local


def test_no_settings_secrets_or_logs_can_be_in_either_package(built):
    for path in built.values():
        for n in members(path):
            assert Path(n).name not in blp.NEVER, n
            assert not n.endswith((".log", ".db", ".pyc")) and "__pycache__" not in n and "config.json" != Path(n).name, n


def test_the_build_refuses_a_secret_file_in_the_list(monkeypatch):
    monkeypatch.setitem(blp.PRODUCTS["courier"]["data"], "config.json", "config.json")
    with pytest.raises(blp.BuildError, match="must never be in a package"):
        blp.collect("courier")


def test_scripts_are_executable_and_plain_lf_on_linux(built):
    for path in built.values():
        for n, m in members(path).items():
            base = Path(n).name
            data = read_member(path, n)
            if base in blp.EXECUTABLE:
                assert m.mode & 0o111 == 0o111, f"{n} must be executable"
            if base.endswith((".sh", ".txt")) or base in blp.EXECUTABLE:
                assert b"\r" not in data, f"{n} has a carriage return, which breaks a script on Linux"
    assert read_member(built["multiproxy"], "multiproxy/install.sh").startswith(b"#!/usr/bin/env bash")
    assert read_member(built["courier"], "multiproxy-courier/install.sh").startswith(b"#!/usr/bin/env bash")      # the courier's own installer, under the shared name


def test_the_build_is_repeatable_byte_for_byte(tmp_path):
    a = blp.build("courier", tmp_path / "a").read_bytes()
    b = blp.build("courier", tmp_path / "b").read_bytes()
    assert a == b


# ---- the shell scripts --------------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("script", SCRIPTS)
def test_every_script_parses(script):
    r = subprocess.run([BASH, "-n", str(ROOT / "linux" / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("script", ["install.sh", "courier-install.sh"])
def test_installers_explain_themselves_and_refuse_an_unknown_option(script):
    ok = subprocess.run([BASH, str(ROOT / "linux" / script), "--help"], capture_output=True, text=True)
    assert ok.returncode == 0 and "--yes" in ok.stdout and "--dir" in ok.stdout and "--settings" in ok.stdout and "--no-service" in ok.stdout
    bad = subprocess.run([BASH, str(ROOT / "linux" / script), "--bogus"], capture_output=True, text=True)
    assert bad.returncode == 2 and "--bogus" in bad.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_questions_take_their_default_with_yes_and_with_no_keyboard(tmp_path):
    """ask_folder / ask_yes answer with their default when nothing can be asked (--yes, or no keyboard): the way every scripted install works."""
    script = tmp_path / "ask.sh"
    script.write_text('. "$1/linux/lib.sh"\nASSUME_YES=yes\nask_folder "Where?" "$2"\necho\nask_yes "Login?" yes && echo YES || echo NO\nask_yes "Wipe?" no && echo YES || echo NO\n', encoding="utf-8")
    r = subprocess.run([BASH, str(script), str(ROOT).replace("\\", "/"), str(tmp_path / "program").replace("\\", "/")], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    lines = r.stdout.split("\n")
    assert lines[0].endswith("program") and lines[1:3] == ["YES", "NO"], r.stdout + r.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_tilde_helpers_round_trip(tmp_path):
    script = tmp_path / "t.sh"
    script.write_text('. "$1/linux/lib.sh"\nHOME=/home/you\ntilde /home/you/.config/multiproxy\necho\nuntilde "~/x y"\necho\ntilde /opt/z\necho\n', encoding="utf-8")
    r = subprocess.run([BASH, str(script), str(ROOT).replace("\\", "/")], capture_output=True, text=True)
    assert r.stdout.split("\n")[:3] == ["~/.config/multiproxy", "/home/you/x y", "/opt/z"], r.stdout + r.stderr


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_a_folder_with_a_space_or_a_mark_stays_one_word_in_the_service_file():
    """Found by the systemd container: an install folder named `my program` split the unit's ExecStart in two and the service never started."""
    r = subprocess.run([BASH, str(ROOT / "tests" / "fixtures" / "linux" / "unit_quote.sh"), str(ROOT).replace(chr(92), "/")], capture_output=True, text=True)
    bs = chr(92)
    assert r.stdout.split(chr(10))[:2] == ['"/tmp/my program/x"', '"/a%%b$$c' + bs + '"d' + bs + bs + 'e"'], r.stdout + r.stderr


def test_the_service_files_quote_every_folder_they_name():
    mp = (ROOT / "linux" / "install.sh").read_text(encoding="utf-8")
    co = (ROOT / "linux" / "courier-install.sh").read_text(encoding="utf-8")
    assert 'ExecStart=$(unit_quote "$INSTALL_DIR/.venv/bin/python") $(unit_quote "$INSTALL_DIR/multiproxy.py")' in mp
    assert 'Environment=$(unit_quote "MULTIPROXY_SETTINGS_DIR=$SETTINGS_DIR")' in mp and 'Exec=$(unit_quote "$INSTALL_DIR/multiproxy") tray' in mp
    assert 'ExecStart=$(unit_quote "$INSTALL_DIR/.venv/bin/python") $(unit_quote "$INSTALL_DIR/courier.py")' in co and '--config-dir $(unit_quote "$SETTINGS_DIR")' in co


def test_the_mockups_wording_is_what_the_installers_ask():
    mp = (ROOT / "linux" / "install.sh").read_text(encoding="utf-8")
    for q in ("Where should it be installed?", "Where should settings and logs go?", "Start it when you log in?",
              "MultiProxy is running. Dashboard: http://127.0.0.1:", "Type  multiproxy help  to see what it can do."):
        assert q in mp, q
    assert "Setting up SkyrimNet MultiProxy on this computer." in mp
    assert "Restart=on-failure" in mp and "enable-linger" in mp                         # restarts on failure, and the linger hint
    assert "steamapps" not in mp and "Skyrim Special Edition" not in mp and "/Data" not in mp         # nothing here ever touches a Skyrim folder
    help_text = (ROOT / "linux" / "multiproxy").read_text(encoding="utf-8")
    for cmd in ("start", "stop", "restart", "status", "logs", "open", "uninstall", "help"):
        assert f"multiproxy {cmd}" in help_text, cmd


def test_the_linux_readme_says_skyrim_just_connects_to_the_service():
    text = (ROOT / "linux" / "README-multiproxy.txt").read_text(encoding="utf-8")
    assert "Proton" in text and "can't start Linux programs" in text and "background service" in text and "just connects" in text


# ---- multiproxy_cli.py (what `multiproxy status|open|stop` ask the server) ----------------------------------------------------------
class FakeServer:
    """Answers like MultiProxy: /health for anyone, /local/* only with the right token."""

    def __init__(self, token: str, ready: int = 3, total: int = 7):
        self.token, self.ready, self.total, self.shutdown_asked = token, ready, total, False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self):
                return self.headers.get("Authorization") == f"Bearer {outer.token}"

            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, {"status": "healthy"})
                if self.path == "/local/tray-status":
                    return self._send(200, {"version": "9.9.9", "readyCount": outer.ready, "totalAIs": outer.total}) if self._authed() else self._send(401, {})
                self._send(404, {})

            def do_POST(self):
                if self.path == "/local/shutdown" and self._authed():
                    outer.shutdown_asked = True
                    threading.Thread(target=outer.httpd.shutdown, daemon=True).start()
                    return self._send(200, {"ok": True})
                self._send(404, {})

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        self.httpd.server_close()


def run_cli(args, tmp_path, port, extra_env=None):
    env = {**os.environ, "MULTIPROXY_SETTINGS_DIR": str(tmp_path / "settings"), "MULTIPROXY_PORT": str(port),
           "MULTIPROXY_INSTALL_INFO": str(tmp_path / "install-info.json"), "DISPLAY": "", "WAYLAND_DISPLAY": "", **(extra_env or {})}
    (tmp_path / "settings").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-I", str(ROOT / "multiproxy_cli.py"), *args], capture_output=True, text=True, env=env, timeout=60)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_status_when_nothing_is_running(tmp_path):
    r = run_cli(["status"], tmp_path, free_port())
    assert r.returncode == 1 and r.stdout.strip() == "MultiProxy is not running."


def test_status_counts_ready_ais_through_the_local_channel(tmp_path):
    srv = FakeServer("tok")
    try:
        (tmp_path / "install-info.json").write_text(json.dumps({"token": "tok"}), encoding="utf-8")
        r = run_cli(["status"], tmp_path, srv.port)
        assert r.returncode == 0 and r.stdout.strip() == "MultiProxy 9.9.9 is running. 3 of 7 AIs ready.", r.stdout + r.stderr
        assert "tok" not in r.stdout + r.stderr                                      # the token is never printed
    finally:
        srv.close()


def test_status_without_a_token_still_says_running(tmp_path):
    srv = FakeServer("tok")
    try:
        r = run_cli(["status"], tmp_path, srv.port)
        assert r.returncode == 0 and "is running." in r.stdout and "AIs ready" not in r.stdout
    finally:
        srv.close()


def test_port_comes_from_the_settings_when_nothing_overrides_it(tmp_path):
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "config.json").write_text(json.dumps({"main_port": 8123}), encoding="utf-8")
    env = {**os.environ, "MULTIPROXY_SETTINGS_DIR": str(tmp_path / "settings")}
    env.pop("MULTIPROXY_PORT", None)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "multiproxy_cli.py"), "port"], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "8123"
    r = subprocess.run([sys.executable, "-I", str(ROOT / "multiproxy_cli.py"), "url"], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "http://127.0.0.1:8123"


def load_cli(monkeypatch, tmp_path, port=8000):
    monkeypatch.setenv("MULTIPROXY_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setenv("MULTIPROXY_PORT", str(port))
    spec = importlib.util.spec_from_file_location("multiproxy_cli_under_test", ROOT / "multiproxy_cli.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_open_prints_the_address_when_there_is_no_desktop(tmp_path, monkeypatch, capsys):
    cli = load_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    opened = []
    assert cli.open_dashboard(opener=lambda u: opened.append(u) or True) == 0
    assert "http://127.0.0.1:8000" in capsys.readouterr().out and opened == []


def test_open_uses_the_browser_when_there_is_a_desktop(tmp_path, monkeypatch, capsys):
    cli = load_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    opened = []
    assert cli.open_dashboard(opener=lambda u: opened.append(u) or True) == 0
    assert opened == ["http://127.0.0.1:8000"] and capsys.readouterr().out == ""


def test_stop_asks_for_a_clean_shutdown_and_waits_for_it(tmp_path):
    srv = FakeServer("tok")
    try:
        (tmp_path / "install-info.json").write_text(json.dumps({"token": "tok"}), encoding="utf-8")
        r = run_cli(["stop"], tmp_path, srv.port)
        assert r.returncode == 0 and srv.shutdown_asked
    finally:
        srv.close()


def test_pointer_writes_the_file_the_mod_and_the_tray_read(tmp_path):
    r = run_cli(["pointer"], tmp_path, 8000)
    assert r.returncode == 0, r.stderr
    info = json.loads((tmp_path / "install-info.json").read_text(encoding="utf-8"))
    assert info["port"] == 8000 and len(info["token"]) >= 16 and info["programFolder"] == str(ROOT)
    assert info["settingsFolder"] == str(tmp_path / "settings") and info["tray"]["command"][-1].endswith("multiproxy_tray.py")
    token = info["token"]
    run_cli(["pointer"], tmp_path, 8000)                                           # writing it again keeps the token (the mod's copy never goes stale)
    assert json.loads((tmp_path / "install-info.json").read_text(encoding="utf-8"))["token"] == token


# ---- the security review (docs/security-review-linux-*.md) ------------------------------------------------------------------------------
def test_the_service_files_are_hardened_and_run_as_the_person():
    for script in ("install.sh", "courier-install.sh"):
        text = (ROOT / "linux" / script).read_text(encoding="utf-8")
        unit = text.split("[Service]", 1)[1].split("[Install]", 1)[0]
        assert "NoNewPrivileges=yes" in unit and "UMask=0077" in unit and "Restart=on-failure" in unit, script
        assert "User=" not in unit and "sudo" not in text.replace("sudo ufw allow", ""), script          # a user service: never root, never asks for sudo
        assert "umask 077" in text, script


def test_no_command_text_is_built_from_a_folder_name():
    """A folder name with a quote in it once became part of a `bash -c "..."` string. Folders go in as arguments."""
    import re
    for script in SCRIPTS:
        for n, line in enumerate((ROOT / "linux" / script).read_text(encoding="utf-8").splitlines(), 1):
            assert not re.search(r'bash -c "[^"]*\$', line), f"{script}:{n}: {line.strip()}"
            assert "eval " not in line.split("#")[0], f"{script}:{n}"


def test_the_installers_never_add_a_firewall_rule_or_ask_for_root():
    for script in SCRIPTS:
        text = (ROOT / "linux" / script).read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.strip()
            if code.startswith("#") or code.startswith("say "):
                continue                                  # (a hint printed to the person is allowed, and `ufw status` only looks; changing a rule is never done)
            assert not re.search(r"(^|[;&| ])(sudo|firewall-cmd|iptables|nft|ufw\s+(allow|deny|limit|reject|delete|insert|enable|disable|reset|route|default))\b", code), f"{script}: {code}"


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_install_and_settings_folders_are_refused_when_an_uninstall_could_wreck_them(tmp_path):
    r = subprocess.run([BASH, str(ROOT / "tests" / "fixtures" / "linux" / "folder_problem.sh"), str(ROOT).replace(chr(92), "/"),
                        str(tmp_path / "home").replace(chr(92), "/"), str(tmp_path / "scratch").replace(chr(92), "/")], capture_output=True, text=True)
    got = dict(line.split(": ", 1) for line in r.stdout.strip().splitlines())
    too_important, other_files = "it is too important to use", "it already has other files in it"
    assert got["home"] == got["root"] == got["etc"] == got["downloads"] == got["relative-home"] == too_important, r.stdout + r.stderr
    assert got["foreign-install"] == got["foreign-settings"] == other_files
    for fine in ("new", "empty", "old-install", "our-settings", "marked", "unchecked"):
        assert got[fine] == "", (fine, got[fine])


def test_uninstall_deletes_only_folders_the_installer_marked():
    for launcher, program in (("multiproxy", "multiproxy.py"), ("multiproxy-courier", "courier.py")):
        text = (ROOT / "linux" / launcher).read_text(encoding="utf-8")
        assert 'if [ ! -f "$DIR/$MARKER" ]; then' in text and "nothing was removed" in text
        assert 'if [ -f "$SETTINGS/$MARKER" ]; then rm -rf "$SETTINGS"' in text and "wasn't made by the installer" in text
        assert "umask 077" in text and f"pid_is \"$p\" {program}" in text           # a stale pid file can't point stop at another program
        assert 'bash -c "! kill' not in text


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_pid_is_only_true_for_the_named_program(tmp_path):
    if not Path("/proc/self/cmdline").exists():
        pytest.skip("needs /proc (Linux)")
    r = subprocess.run([BASH, str(ROOT / "tests" / "fixtures" / "linux" / "pid_is.sh"), str(ROOT).replace(chr(92), "/")], capture_output=True, text=True)
    assert r.stdout.split() == ["self-ok", "other-no", "bad-no", "empty-no"]


def test_the_courier_status_line_never_shows_the_pairing_code():
    text = (ROOT / "linux" / "multiproxy-courier").read_text(encoding="utf-8")
    assert "sed 's/^Pairing code:.*/Not paired yet." in text
    assert "status_text() { courier status" in text and "then status_text; else" in text
    # the one place the code is shown on purpose is `code` (and the install's last screen, which is the person's own terminal)
    assert "code) courier code" in text


def test_a_services_output_never_holds_the_pairing_code_but_a_terminal_does(tmp_path, monkeypatch, capsys):
    import courier
    code_line = "Ready for your main device (pairing code KX7-42M)."
    monkeypatch.setattr(courier, "_stdout_is_a_terminal", lambda: False)
    log = courier.setup_logging(tmp_path, console=True)
    log.info(code_line)
    out = capsys.readouterr().out
    assert "KX7-42M" not in out and "Ready for your main device" in out
    assert "KX7-42M" not in (tmp_path / "courier.log").read_text(encoding="utf-8")
    monkeypatch.setattr(courier, "_stdout_is_a_terminal", lambda: True)
    log = courier.setup_logging(tmp_path, console=True)
    log.info(code_line)
    assert "KX7-42M" in capsys.readouterr().out
    for h in list(log.handlers):
        h.close()


@pytest.mark.skipif(os.name == "nt", reason="file modes are a Linux thing")
def test_the_pointer_file_with_the_token_is_private(tmp_path):
    import local_channel
    p = tmp_path / "skyrimnet-multiproxy" / "install-info.json"
    local_channel.write_info(port=8000, command=["python", "x.py"], program_folder="/p", settings_folder="/s", path=p)
    assert oct(p.stat().st_mode & 0o777) == "0o600" and oct(p.parent.stat().st_mode & 0o777) == "0o700"
    local_channel.write_info(port=8001, command=["python", "x.py"], program_folder="/p", settings_folder="/s", path=p)      # rewriting keeps it
    assert oct(p.stat().st_mode & 0o777) == "0o600"


# ---- an update offers the earlier answers (lib.sh prefill_*, remember_install; the installers' wiring) -----------------------------------
def prefill(tmp_path, case):
    """Runs tests/fixtures/linux/prefill.sh with a scratch home. `python3` is this test run's own Python (on a device where it is only a Store shortcut)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    py = bindir / "python3"
    py.write_text('#!/bin/sh\nexec "' + sys.executable.replace(chr(92), "/") + '" "$@"\n', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "PATH": str(bindir).replace(chr(92), "/") + os.pathsep + os.environ.get("PATH", "")}
    r = subprocess.run([BASH, str(ROOT / "tests" / "fixtures" / "linux" / "prefill.sh"), str(ROOT).replace(chr(92), "/"), str(home).replace(chr(92), "/"), case],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.replace("\r", ""), r.stderr


def posix_home(tmp_path):
    out = subprocess.run([BASH, "-c", 'cd "$1" && pwd', "_", str(tmp_path / "home").replace(chr(92), "/")], capture_output=True, text=True).stdout.strip()
    return out


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_a_first_install_starts_from_the_defaults_and_says_nothing_about_a_missing_folder(tmp_path):
    out, _ = prefill(tmp_path, "first")
    h = posix_home(tmp_path)
    assert out.splitlines() == [f"dir: {h}/.local/share/multiproxy from=default", f"settings: {h}/.config/multiproxy from=default", "login: yes"]
    out, err = prefill(tmp_path, "fresh-no-note")
    assert out.strip() == f"answer: {h}/.local/share/multiproxy" and "That folder is missing" not in err + out


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_an_update_starts_from_the_pointer_the_earlier_install_left(tmp_path):
    out, _ = prefill(tmp_path, "pointer")
    h = posix_home(tmp_path)
    assert out.splitlines() == [f"dir: {h}/apps/mp from=pointer", f"settings: {h}/cfg/mp from=pointer", "login: no"]


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_a_newer_settings_value_beats_the_pointer(tmp_path):
    out, _ = prefill(tmp_path, "newer-settings")
    h = posix_home(tmp_path)
    assert f"settings: {h}/cfg/new from=settings" in out.splitlines()                    # the pointer still says cfg/old: never allowed to override


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_a_folder_that_is_gone_is_offered_and_flagged_in_one_plain_line(tmp_path):
    out, _ = prefill(tmp_path, "stale")
    h = posix_home(tmp_path)
    lines = out.splitlines()
    assert lines[0] == f"dir: {h}/gone/mp from=pointer" and lines[1] == f"settings: {h}/gone/cfg from=pointer"        # shown, never silently replaced
    note = "That folder is missing. Pick another, or one will be created."
    assert out.count(note) == 1 and lines[3] == f"yes-answer: {note}" and lines[4] == f"{h}/gone/mp"        # said once; the answer is still the remembered folder


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_damaged_files_never_crash_the_install_they_fall_back_to_the_next_place(tmp_path):
    out, _ = prefill(tmp_path, "damaged")
    h = posix_home(tmp_path)
    lines = out.splitlines()
    assert len(lines) == 9                                                                # three passes (bad json, a list, wrong kinds of value), none stopped
    for i in (0, 3, 6):
        assert lines[i] == f"dir: {h}/.local/share/multiproxy from=default"
        assert lines[i + 1] == f"settings: {h}/.config/multiproxy from=default"          # (the settings-location.txt that isn't a full path is skipped too)
        assert lines[i + 2] == "login: yes"


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_an_older_install_found_on_this_pc_is_the_third_place_to_look(tmp_path):
    out, _ = prefill(tmp_path, "older-install")
    if out.startswith("nosymlink"):
        pytest.skip("this device can't make a symbolic link")
    h = posix_home(tmp_path)
    assert out.splitlines() == [f"dir: {h}/old/mp from=install", f"settings: {h}/old/cfg from=install"]


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_question_shows_the_remembered_value_and_enter_keeps_it(tmp_path):
    out, _ = prefill(tmp_path, "prompt")
    assert out.splitlines()[0].endswith("/apps/mp") and "Where? [current: ~/apps/mp]" in out


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_remembering_answers_keeps_the_token_replaces_a_damaged_file_and_makes_the_folder(tmp_path):
    out, _ = prefill(tmp_path, "remember")
    blobs = [b for b in re.split(r"(?<=\})\n", out) if b.strip()]
    first, second, third = (json.loads(b) for b in blobs)
    assert first["token"] == "keep-me" and first["programFolder"].endswith("/p q") and first["startAtLogin"] is False
    assert second == {"startAtLogin": True} and third == {"a": "b"}


def test_the_installers_use_the_remembered_answers_and_the_courier_leaves_and_removes_its_own_pointer():
    for script, cmd in (("install.sh", "multiproxy"), ("courier-install.sh", "multiproxy-courier")):
        text = (ROOT / "linux" / script).read_text(encoding="utf-8")
        assert f'prefill_dir "$POINTER" {cmd} ' in text and f'prefill_settings "$POINTER" {cmd} ' in text and f'prefill_login "$POINTER" {cmd}.service' in text
        assert '"$DIR_FROM")' in text and '"$SETTINGS_FROM")' in text and '"$DEFAULT_LOGIN"' in text
        assert 'remember_install "$POINTER"' in text
    co = (ROOT / "linux" / "courier-install.sh").read_text(encoding="utf-8")
    assert "skyrimnet-multiproxy-courier/install-info.json" in co and 'programFolder=$INSTALL_DIR' in co and 'settingsFolder=$SETTINGS_DIR' in co and "startAtLogin=$LOGIN" in co
    un = (ROOT / "linux" / "multiproxy-courier").read_text(encoding="utf-8")
    assert 'json_field "$pointer" programFolder)" = "$DIR" ]; then rm -f "$pointer"' in un                  # only a pointer this install wrote
    mp = (ROOT / "linux" / "multiproxy").read_text(encoding="utf-8")
    assert 'grep -qF "\\"programFolder\\": \\"$DIR\\"" "$pointer"' in mp and 'then rm -f "$pointer"' in mp
