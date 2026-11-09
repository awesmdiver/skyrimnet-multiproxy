"""The start-program build finds rc.exe and cl.exe by itself (any prompt), and says plainly which tool is missing instead of a traceback. Fake folder layouts only."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer" / "launcher"))
import build_launcher as bl  # noqa: E402

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own tool names")


def make_exe(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{name}.exe"
    f.write_bytes(b"MZ")
    return f


@win
def test_a_tool_on_the_path_is_found_there(tmp_path):
    exe = make_exe(tmp_path / "bin", "rc")
    assert bl.find_tool("rc", {"PATH": str(tmp_path / "bin")}) == exe


@win
def test_a_tool_off_the_path_is_found_in_the_newest_sdk_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "_program_files", lambda: [str(tmp_path)])
    monkeypatch.setattr(bl, "_vswhere_bin_dirs", lambda name: [])
    make_exe(tmp_path / "Windows Kits" / "10" / "bin" / "10.0.19041.0" / "x64", "rc")
    newest = make_exe(tmp_path / "Windows Kits" / "10" / "bin" / "10.0.26100.0" / "x64", "rc")
    assert bl.find_tool("rc", {"PATH": ""}) == newest


@win
def test_a_tool_off_the_path_is_found_where_vswhere_says(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "_program_files", lambda: [str(tmp_path / "none")])
    exe = make_exe(tmp_path / "vs" / "Hostx64" / "x64", "cl")
    monkeypatch.setattr(bl, "_vswhere_bin_dirs", lambda name: [exe.parent])
    assert bl.find_tool("cl", {"PATH": ""}) == exe


@win
def test_a_folder_found_this_way_is_added_to_this_builds_path_only(tmp_path, monkeypatch):
    rc = make_exe(tmp_path / "sdk", "rc")
    cl = make_exe(tmp_path / "vc", "cl")
    monkeypatch.setenv("INCLUDE", "x")
    monkeypatch.setenv("PATH", str(tmp_path / "vc"))
    monkeypatch.setattr(bl, "_sdk_bin_dirs", lambda: [rc.parent])
    before = bl.os.environ["PATH"]
    env, tools = bl.toolchain()
    assert {k: v.lower() for k, v in tools.items()} == {"rc": str(rc).lower(), "cl": str(cl).lower()} and str(rc.parent) in env["PATH"] and bl.os.environ["PATH"] == before


def test_a_missing_tool_gives_the_plain_sentence_and_no_traceback(monkeypatch):
    monkeypatch.setattr(bl, "_vcvars_env", lambda: None)
    monkeypatch.setattr(bl, "find_tool", lambda name, env: None)
    with pytest.raises(bl.LauncherError) as e:
        bl.toolchain()
    assert str(e.value).startswith("rc.exe was not found. It comes with the Windows SDK. Run this from a Claude terminal, or install the Windows SDK")


def test_the_install_builder_turns_a_start_program_failure_into_its_plain_stop():
    src = (ROOT / "installer" / "build.py").read_text(encoding="utf-8")
    assert "except build_launcher.LauncherError as e:" in src and "raise BuildError(" in src.split("except build_launcher.LauncherError as e:")[1][:200]
