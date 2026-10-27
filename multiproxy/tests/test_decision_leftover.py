"""A decision engine left running on this device (2026-10-26): it is shown, it can be shut down, and only that one program is ever stopped.
Stand-in programs only (plain Python sleepers); the checks that a program is a llama-server are replaced, never a real engine."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

import decision_engine as de
from tests.decision_helpers import make_engine


def sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=os.name != "nt")   # its own group, as a real engine has: stopping it kills its group


@pytest.fixture
def procs():
    made = []

    def make():
        p = sleeper()
        made.append(p)
        return p
    yield make
    for p in made:
        if p.poll() is None:
            p.kill()
        p.wait()


def write_pid(engine, pid, owner=0, token=""):
    engine.folder.mkdir(parents=True, exist_ok=True)
    engine.pid_file.write_text(json.dumps({"engine": pid, "owner": owner, "token": token}))


def test_an_engine_the_pid_file_names_is_reported_as_running_and_shut_down_alone(tmp_path, procs, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    ours, other = procs(), procs()
    monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
    write_pid(engine, ours.pid)
    assert engine.status()["leftoverRunning"] is True
    asyncio.run(engine.stop())
    ours.wait(timeout=20)
    assert ours.poll() is not None and other.poll() is None, "only the named engine stopped"
    assert engine.status()["leftoverRunning"] is False


def test_when_it_cannot_be_told_which_program_it_is_nothing_is_stopped_and_it_says_so(tmp_path, procs, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    p = procs()
    monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: False)       # the recorded number now belongs to some other program
    write_pid(engine, p.pid)
    with pytest.raises(de.DecisionError) as e:
        asyncio.run(engine.stop())
    assert "nothing was stopped" in e.value.message and p.poll() is None


def test_an_engine_that_a_running_multiproxy_started_is_left_alone(tmp_path, procs, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    p, owner = procs(), procs()
    monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
    write_pid(engine, p.pid, owner=owner.pid, token=de._process_token(owner.pid))
    assert engine.stop_leftover() == "unsure" and p.poll() is None


def test_with_no_pid_file_and_no_match_nothing_happens(tmp_path, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    monkeypatch.setattr(engine, "_find_by_command_line", lambda: [])
    assert engine.leftover_running() is False and engine.stop_leftover(by_button=True) == "none"
    monkeypatch.setattr(engine, "_find_by_command_line", lambda: [1, 2])
    assert engine.stop_leftover(by_button=True) == "unsure"


def test_an_automatic_stop_with_no_record_stops_nothing_and_never_looks_by_command_line(tmp_path, procs, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    p = procs()
    monkeypatch.setattr(engine, "_find_by_command_line", lambda: pytest.fail("looked a program up by its command line"))
    assert engine.stop_leftover() == "none" and p.poll() is None
    asyncio.run(engine.stop())                                         # what Move decisions and Cloud only call
    assert p.poll() is None


def test_the_button_stops_the_one_program_that_matches_and_not_a_second_one(tmp_path, procs, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    mine, other_model = procs(), procs()
    monkeypatch.setattr(engine, "_find_by_command_line", lambda: [mine.pid])     # the second llama-server has another model folder, so it never matches
    assert engine.stop_leftover(by_button=True) == "stopped"
    mine.wait(timeout=20)
    assert mine.poll() is not None and other_model.poll() is None


def test_the_command_line_match_needs_this_devices_model_folder_and_a_decision_port(tmp_path, monkeypatch):
    engine, _, _ = make_engine(tmp_path)
    lo, hi = engine._port_range
    class R:
        stdout = (f"11\t llama-server.exe -m {engine.folder}\\m.gguf --port {lo}\n"
                  f"12\t llama-server.exe -m {tmp_path}\\elsewhere\\m.gguf --port {lo}\n"
                  f"13\t llama-server.exe -m {engine.folder}\\m.gguf --port {hi + 1}\n")
    monkeypatch.setattr(de.hidden_process, "run", lambda *a, **k: R())
    monkeypatch.setattr(de.os, "name", "nt")
    assert engine._find_by_command_line() == [11]
