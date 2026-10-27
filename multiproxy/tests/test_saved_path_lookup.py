"""A program installed after MultiProxy started is found through Windows' saved PATH (2026-10-25). A fake registry reader gives the folder; nothing real is read or written."""
import os
import shutil
import stat

import pytest

import poll_cache

NAME = "mp-stand-in-tool"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    poll_cache.forget_which()
    monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: "")
    yield
    poll_cache.forget_which()


def make_tool(folder):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (NAME + (".exe" if os.name == "nt" else ""))
    p.write_text("x")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_not_on_the_process_path_but_found_through_the_saved_path(tmp_path, monkeypatch):
    tool = make_tool(tmp_path / "bin")
    assert poll_cache.cached_which(NAME) is None
    poll_cache.forget_which(NAME)
    monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: str(tmp_path / "bin"))
    assert os.path.samefile(poll_cache.cached_which(NAME), tool)


def test_a_missing_program_is_still_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: str(tmp_path / "empty"))
    assert poll_cache.cached_which(NAME) is None


def test_a_reader_that_fails_is_not_found_not_an_error(monkeypatch):
    def boom():
        raise OSError("no registry")
    monkeypatch.setattr(poll_cache, "_saved_path_reader", boom)
    assert poll_cache.cached_which(NAME) is None


def test_the_registry_is_read_only_when_the_normal_lookup_fails_and_not_again_for_20_seconds(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: calls.append(1) or "")
    poll_cache.cached_which(NAME)
    poll_cache.cached_which(NAME)
    assert len(calls) == 1
    calls.clear()
    on_path = next(n for n in ("python", "python3", "py") if shutil.which(n))        # a name this machine really has (Linux has python3, not python)
    found = poll_cache.cached_which(on_path)
    assert found and calls == [], "a program found on the process path never reads the registry"


def test_refresh_forgets_the_answer(tmp_path, monkeypatch):
    assert poll_cache.cached_which(NAME) is None
    make_tool(tmp_path / "bin")
    monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: str(tmp_path / "bin"))
    assert poll_cache.cached_which(NAME) is None, "remembered"
    poll_cache.forget_which(NAME)
    assert poll_cache.cached_which(NAME)


def test_saved_path_is_system_then_user_with_variables_expanded(monkeypatch):
    monkeypatch.setattr(poll_cache.os, "name", "nt")
    monkeypatch.setenv("MP_TEST_HOME", "C:\\Users\\someone")
    rows = {"HKEY_LOCAL_MACHINE": "C:\\Windows;%MP_TEST_HOME%\\sys", "HKEY_CURRENT_USER": "%MP_TEST_HOME%\\.tool\\bin", }
    monkeypatch.setattr(poll_cache, "_read_registry_path", lambda hive, sub: rows.get(hive))
    monkeypatch.setattr(poll_cache.os.path, "expandvars", lambda v: v.replace("%MP_TEST_HOME%", "C:\\Users\\someone"))
    want = r"C:\Windows;C:\Users\someone\sys" + os.pathsep + r"C:\Users\someone\.tool\bin"
    assert poll_cache.read_saved_path() == want


def test_nothing_new_off_windows(monkeypatch):
    monkeypatch.setattr(poll_cache.os, "name", "posix")
    assert poll_cache.read_saved_path() == ""
