"""The rename (2026-10-01): proxy.py -> multiproxy.py and proxy.ini -> multiproxy.ini, without breaking anyone's setup.

An existing player's mod settings still start `proxy.py`: the shim must start the real program with the same arguments. An existing
proxy.ini must be carried over once, never deleted.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_the_shim_is_tiny_says_why_it_exists_and_imports_nothing_of_the_program():
    src = (ROOT / "proxy.py").read_text(encoding="utf-8")
    assert len(src.splitlines()) < 25
    assert "SkyrimNet MultiProxy mod settings still say" in src and "multiproxy.py" in src and "proxy.ini" in src
    assert "import multiproxy" not in src and "from multiproxy" not in src


def test_the_real_program_and_its_settings_have_the_new_names(proxy_module):
    assert os.path.basename(proxy_module.__file__) == "multiproxy.py"
    assert (ROOT / "multiproxy.py").exists() and (ROOT / "multiproxy.ini.example").exists() and not (ROOT / "proxy.ini.example").exists()
    assert os.path.basename(proxy_module._PROXY_INI.replace("\\", "/")) == "multiproxy.ini" or proxy_module._PROXY_INI.endswith("proxy.ini")


def test_the_log_file_is_multiproxy_log():
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
    assert '"multiproxy.log"' in src and '"proxy.log"' not in src


# ---- the old proxy.ini is carried over once ---------------------------------------------------------------------------------
def test_an_old_proxy_ini_is_copied_to_multiproxy_ini_once_comments_and_all_and_never_deleted(proxy_module, tmp_path):
    tmp_path = tmp_path / "install"
    tmp_path.mkdir()
    old, new = tmp_path / "proxy.ini", tmp_path / "multiproxy.ini"
    text = "[General]\n; my own note\nAutoCloseWithSkyrim = true\n[Concurrency]\nGLM = 4\n"
    old.write_text(text, encoding="utf-8")
    line = proxy_module._migrate_legacy_ini(str(new), str(old))
    assert line == "Your settings moved: proxy.ini was copied to multiproxy.ini (the old file is still there, untouched)."
    assert new.read_text(encoding="utf-8") == text and old.read_text(encoding="utf-8") == text
    new.write_text("[General]\nAutoCloseWithSkyrim = false\n", encoding="utf-8")        # later changes to the new file are never overwritten
    assert proxy_module._migrate_legacy_ini(str(new), str(old)) is None
    assert "false" in new.read_text(encoding="utf-8") and old.read_text(encoding="utf-8") == text


def test_nothing_happens_when_there_is_no_old_file_or_the_new_one_exists(proxy_module, tmp_path):
    tmp_path = tmp_path / "install"
    tmp_path.mkdir()
    assert proxy_module._migrate_legacy_ini(str(tmp_path / "multiproxy.ini"), str(tmp_path / "proxy.ini")) is None
    assert not (tmp_path / "multiproxy.ini").exists()


def test_importing_the_program_in_a_test_run_never_writes_into_the_real_folder():
    assert os.environ.get("MULTIPROXY_NO_INI_MIGRATION") == "1"


# ---- start it for real, through the new name and through the old shim ---------------------------------------------------------
def _start(tmp_path, script, port):
    work = tmp_path / script.replace(".", "_")
    work.mkdir()
    for f in ROOT.glob("*.py"):
        if not f.name.startswith("test_"):       # (tested_settings.py is a program file that only begins with "test")
            shutil.copy(f, work)
    (work / "docs" / "help").mkdir(parents=True)
    shutil.copy(ROOT / "docs" / "help" / "help-content.md", work / "docs" / "help")
    (work / "config.json").write_text("{}")
    env = {**os.environ, "MULTIPROXY_PORT": str(port), "MULTIPROXY_INTERCEPTOR_PORT": str(free_port()), "MULTIPROXY_NO_INI_MIGRATION": "1"}
    proc = subprocess.Popen([sys.executable, script, "--plugin-version=9.9.9"], cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, work


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        return json.loads(r.read())


@pytest.mark.parametrize("script", ["multiproxy.py", "proxy.py"])
def test_it_starts_and_answers_through_the_new_name_and_through_the_old_shim_with_the_same_arguments(tmp_path, script):
    port = free_port()
    proc, work = _start(tmp_path, script, port)
    try:
        health = None
        for _ in range(120):
            if proc.poll() is not None:
                break
            try:
                health = _get(port, "/health")
                break
            except OSError:
                time.sleep(0.5)
        assert health and health["status"] == "healthy", f"{script} didn't answer"
        assert health["proxy_path"].endswith("multiproxy.py"), "the real program is what runs, whichever name started it"
        settings = _get(port, "/config/settings")
        assert settings["mod"]["detected"] is True and settings["mod"]["version"] == "9.9.9", "the arguments were passed on"
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
