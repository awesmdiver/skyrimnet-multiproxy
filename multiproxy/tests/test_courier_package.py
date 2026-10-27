"""The small "MultiProxy Courier" download for your other devices (scripts/build_courier_package.py) and the scripts inside
it. The scripts themselves were run for real on Windows and in Linux containers (see TECHNICAL.md); these tests keep the
package honest: what's in it, what must never be, and the details that silently break a script on the other operating system.
"""
import importlib.util
import os
import re
import stat
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_pkg", ROOT / "scripts" / "build_courier_package.py")
build_pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_pkg)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("pkg") / build_pkg.ZIP_NAME
    names = build_pkg.build(out)
    return out, names


def test_it_contains_exactly_the_courier_and_the_setup_files(built):
    out, names = built
    assert out.name == "MultiProxy-Courier.zip"
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == sorted(names)
    import courier_files
    assert set(names) == set(courier_files.program()) | set(courier_files.data()) | set(courier_files.setup()) | {"courier-build.txt"}


def test_it_does_not_carry_the_full_multiproxy_or_anything_skyrim(built):
    _, names = built
    for gone in ("multiproxy.py", "proxy.py", "multiproxy.ini.example", "proxy.ini.example", "config.example.json", "start-proxy.sh", "local_models_ui.py", "other_pcs_ui.py",
                 "model_groups_ui.py", "courier_client.py"):
        assert gone not in names, gone


def test_every_module_the_courier_imports_is_in_the_package(built):
    """The zip must be able to start: any program file courier.py (transitively) imports has to ship in it."""
    _, names = built
    own = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}
    seen, todo = set(), ["courier"]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        text = (ROOT / f"{mod}.py").read_text(encoding="utf-8")
        deps = re.findall(r"^\s*from (\w+)", text, flags=re.M)
        for names_on_a_line in re.findall(r"^\s*import ([\w, ]+)", text, flags=re.M):
            deps += [n.split()[0] for n in names_on_a_line.split(",") if n.strip()]          # `import a, b as c` brings in both (the first word of each)
        for dep in deps:
            if dep in own:
                assert f"{dep}.py" in names, f"{dep}.py is imported by {mod}.py but not packaged"
                todo.append(dep)
    assert {"courier", "courier_system", "courier_adapters", "other_pcs", "local_models", "gpu_monitor"} <= seen


def test_the_shared_modules_never_import_the_full_multiproxy():
    for mod in ("other_pcs", "local_models", "gpu_monitor", "courier", "courier_adapters", "courier_system", "courier_ui"):
        text = (ROOT / f"{mod}.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*(?:import|from) proxy\b", text, flags=re.M), mod


def test_nothing_secret_or_personal_can_be_in_it(built):
    _, names = built
    assert not set(names) & build_pkg.NEVER
    for bad in ("config.json", "courier.json", "multiproxy.ini", "multiproxy.log", "proxy.ini", "proxy.log", "courier.log", "speed_history.db"):
        assert bad not in names
    assert not any(n.startswith(("tests/", "prompts/", "design/", "docs/")) for n in names)


def test_the_build_refuses_a_forbidden_file(monkeypatch, tmp_path):
    monkeypatch.setattr(build_pkg, "PROGRAM_FILES", build_pkg.PROGRAM_FILES + ["courier.json"])
    with pytest.raises(SystemExit) as e:
        build_pkg.build(tmp_path / "x.zip")
    assert "must never be published" in str(e.value)


def test_each_script_has_the_line_endings_its_operating_system_needs(built):
    out, _ = built
    with zipfile.ZipFile(out) as z:
        for name in ("Set up this PC.bat", "Start Courier.bat", "setup-courier.ps1"):
            data = z.read(name)
            assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), f"{name} must be CRLF throughout"
        data = z.read("install-courier.sh")
        assert b"\r" not in data and data.startswith(b"#!/usr/bin/env bash\n"), "install-courier.sh must be plain LF with a shebang"


def test_the_linux_installer_is_marked_runnable(built):
    out, _ = built
    with zipfile.ZipFile(out) as z:
        assert z.getinfo("install-courier.sh").external_attr >> 16 & stat.S_IXUSR


def test_windows_scripts_are_plain_ascii_so_no_code_page_can_garble_them():
    for rel in ("courier/Set up this PC.bat", "courier/Start Courier.bat", "courier/setup-courier.ps1"):
        (ROOT / rel).read_bytes().decode("ascii")


def test_the_windows_launcher_offers_powershell_7_instead_of_just_failing():
    bat = (ROOT / "courier/Set up this PC.bat").read_text(encoding="utf-8")
    assert "where pwsh" in bat and "Microsoft.PowerShell" in bat and "free update from Microsoft" in bat
    assert "call winget" in bat, "a bare 'winget' that is a .cmd would never return to the script"
    assert "aka.ms/powershell" in bat and "setup-courier.ps1" in bat


def test_the_setup_script_covers_every_promised_step():
    ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
    assert ps1.startswith("#Requires -Version 7.0")
    for needle in ("winget install --id Python.Python.3.12", "requirements-courier.txt", "profile=private", "name='MultiProxy Courier'",
                   "-Verb RunAs", "localport=$TcpPort", "localport=$UdpPort", "remoteip=LocalSubnet", "courier.py code", "Pairing code", "pythonw.exe",
                   "MultiProxy Model Server.lnk", "$NoFirewall", "$NoAutoStart"):
        assert needle in ps1, needle
    assert "profile=public" not in ps1.lower() and "profile=any" not in ps1.lower() and "profile=domain" not in ps1.lower(), \
        "the firewall rule is private networks only"
    assert "program='" in ps1, "the rule is for the Courier's own program (security review S3)"


def test_the_linux_installer_sets_up_a_service_and_prints_the_code():
    sh = (ROOT / "courier/install-courier.sh").read_text(encoding="utf-8")
    for needle in ("systemctl --user", "multiproxy-courier.service", "enable-linger", "Pairing code", "--no-service",
                   "python3 -m venv", "libgomp", "courier.py status", "ufw allow from $NET", "hostname -I"):
        assert needle in sh, needle
    assert "python3-venv" in sh and "--no-tray" in sh


def test_the_download_links_the_dashboard_shows_match_what_the_package_is_called():
    import other_pcs
    assert other_pcs.MODEL_SERVER_DOWNLOAD_URL.endswith("/" + build_pkg.ZIP_NAME)
    assert other_pcs.LINUX_INSTALL_COMMAND.endswith("install-courier.sh | bash")
    assert "install-courier.sh" in build_pkg.SETUP_FILES


def test_the_old_model_server_package_is_gone():
    assert not (ROOT / "model-server").exists() and not (ROOT / "scripts" / "build_model_server_package.py").exists()


def test_every_file_in_the_zip_imports_cleanly_from_the_unpacked_zip(built, tmp_path):
    """The check that cannot be forgotten with the list: unpack the real zip, and in a fresh Python that can only see that folder import every program file it carries.
    A file the list lacks (it was `program_uninstall.py`, #111) breaks the import of whatever needs it: ModuleNotFoundError, naming it."""
    import subprocess
    import sys
    out, names = built
    with zipfile.ZipFile(out) as z:
        z.extractall(tmp_path)
    modules = sorted(n[:-3] for n in names if n.endswith(".py"))
    code = ("import sys, os, importlib\n"
            "sys.path.insert(0, os.getcwd())\n"
            "bad = []\n"
            f"for m in {modules!r}:\n"
            "    try:\n        importlib.import_module(m)\n"
            "        if os.path.dirname(os.path.abspath(sys.modules[m].__file__)) != os.getcwd():\n            bad.append(m + ': loaded from outside the zip')\n"
            "    except Exception as e:\n        bad.append(m + ': ' + type(e).__name__ + ': ' + str(e)[:120])\n"
            "print('\\n'.join(bad))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONPATH": ""})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", r.stdout


def test_a_courier_install_runs_from_the_unpacked_zip(built, tmp_path):
    """Unpack the real zip and run it from there with nothing from the dev folder: the code command makes courier.json
    (id, code, defaults) and the status command says it isn't running."""
    import subprocess
    import sys
    out, _ = built
    with zipfile.ZipFile(out) as z:
        z.extractall(tmp_path)
    env = {**os.environ, "PYTHONPATH": ""}
    r = subprocess.run([sys.executable, "courier.py", "code", "--port", "58123"], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    code = r.stdout.strip().splitlines()[-1]
    assert re.fullmatch(r"[A-Z2-9]{3}-[A-Z2-9]{3}", code)
    import json
    cfg = json.loads((tmp_path / "courier.json").read_text())
    assert cfg["code"] == code and cfg["port"] == 58123 and cfg["start_with_windows"] is True
    r2 = subprocess.run([sys.executable, "courier.py", "status", "--port", "58123"], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert "is not running on this device." in r2.stdout and code in r2.stdout
    r3 = subprocess.run([sys.executable, "courier.py", "code", "--start-with-windows", "off"], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert json.loads((tmp_path / "courier.json").read_text())["start_with_windows"] is False


def test_the_setup_script_installs_exactly_the_files_the_one_list_names():
    """setup-courier.ps1 no longer keeps a list of its own: it reads courier-files.json from the package, the same file the zip was built from."""
    ps1 = (ROOT / "courier" / "setup-courier.ps1").read_text(encoding="utf-8")
    assert "courier-files.json" in ps1 and "'courier_system.py'" not in ps1
    assert "@($fileList.program) + @($fileList.data.PSObject.Properties.Name) + @('Start Courier.bat')" in ps1
