"""Start and stop with Skyrim also notices Skyrim ending without the mod telling it (2026-10-26). Skyrim killed hard never runs the mod's exit code, so no "closed" arrives; the tray
app looks at the process number from the mod's "started" and treats a vanished process like the closed message. A stand-in process (started here and killed hard here) is the
"Skyrim"; nothing else on this PC is touched."""
import subprocess
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import local_channel
import process_watch
from tests.test_tray import info, make_controller, program        # noqa: F401  (fixtures)

LINE = "Skyrim closed, so MultiProxy is quitting ('Start and stop with Skyrim' is on, and the mod started this). MultiProxy noticed the game process was gone before the mod sent a closing message."


def snap(pid, running=True, seq=1, closed=0, auto=True, boot="b"):
    return {"boot": boot, "autoClose": auto, "skyrim": {"running": running, "pid": pid, "startedSeq": seq, "closedSeq": closed}}


def stand_in():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- the look itself, on a real stand-in process we started ----
def test_a_running_process_is_alive_with_a_start_token_and_a_killed_one_is_gone():
    p = stand_in()
    try:
        state, token = process_watch.look(p.pid)
        assert state == "alive" and token is not None
        assert process_watch.look(p.pid) == (state, token), "the same process gives the same start token every time"
        p.kill()                                               # hard, the way Task Manager's End Task does
        p.wait(10)
        deadline = time.time() + 5
        while time.time() < deadline and process_watch.look(p.pid)[0] != "gone":
            time.sleep(0.1)
        assert process_watch.look(p.pid)[0] == "gone"
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(10)


def test_a_bad_number_is_never_alive():
    assert process_watch.look(0)[0] == "unknown" and process_watch.look(-5)[0] == "unknown" and process_watch.look("x")[0] == "unknown"


def test_a_reused_number_counts_as_ended_but_an_unreadable_process_never_does():
    assert process_watch.has_ended("gone", None, 5) is True
    assert process_watch.has_ended("alive", 100, 100) is False, "the same program"
    assert process_watch.has_ended("alive", 200, 100) is True, "the same number, a program that started later: Skyrim is gone"
    assert process_watch.has_ended("alive", None, 100) is False and process_watch.has_ended("alive", 100, None) is False, "no start token to compare: only the number counts"
    assert process_watch.has_ended("unknown", None, None) is False


# ---- the tray app ----
def test_the_watcher_quits_after_the_stand_in_is_killed_with_no_closed_message(program):
    ctl = make_controller(program, from_skyrim=True)
    logs = []
    ctl.log = logs.append
    p = stand_in()
    try:
        ctl._skyrim_events(snap(p.pid))
        assert not ctl.quit_requested.is_set(), "Skyrim is running"
        ctl._skyrim_events(snap(p.pid))
        assert not ctl.quit_requested.is_set()
        p.kill()
        p.wait(10)
        deadline = time.time() + 5
        while time.time() < deadline and not ctl.quit_requested.is_set():
            ctl._skyrim_events(snap(p.pid))                    # still no "closed": closedSeq stays 0
            time.sleep(0.1)
        assert ctl.quit_requested.is_set() and logs.count(LINE) == 1
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(10)


def test_the_closed_message_still_works_alone_and_only_one_quit_happens(program):
    ctl = make_controller(program, from_skyrim=True)
    logs = []
    ctl.log = logs.append
    ctl._look = lambda pid: ("alive", 7)
    ctl._skyrim_events(snap(4242))                            # baseline
    ctl._skyrim_events(snap(4242, running=False, closed=1))   # the mod said "closed" (the process is still there: the mod noticed first)
    assert ctl.quit_requested.is_set() and not any("noticed the end itself" in m for m in logs)
    ctl.quit_requested.clear()
    ctl._look = lambda pid: ("gone", None)
    ctl._skyrim_events(snap(4242, running=False, closed=1))   # the mod already said closed: the watcher has nothing left to watch and quits nothing again
    assert not ctl.quit_requested.is_set()


def test_a_reused_number_does_not_keep_it_alive_and_a_new_started_replaces_what_is_watched(program):
    ctl = make_controller(program, from_skyrim=True)
    ctl._look = lambda pid: ("alive", 100)
    ctl._skyrim_events(snap(4242, seq=1))
    ctl._look = lambda pid: ("alive", 100)
    ctl._skyrim_events(snap(4242, seq=1))
    assert not ctl.quit_requested.is_set()
    ctl._look = lambda pid: ("alive", 999)                    # Skyrim ended and Windows gave 4242 to another program
    ctl._skyrim_events(snap(4242, seq=1))
    assert ctl.quit_requested.is_set()
    ctl.quit_requested.clear()
    ctl._look = lambda pid: ("alive", 50)
    ctl._skyrim_events(snap(5000, seq=2))                     # a second Skyrim run: its number is the one watched now (and its own start time)
    ctl._look = lambda pid: ("alive", 50)
    ctl._skyrim_events(snap(5000, seq=2))
    assert not ctl.quit_requested.is_set() and ctl._watch["pid"] == 5000


def test_a_process_that_cannot_be_looked_at_never_quits_and_says_so_once(program):
    ctl = make_controller(program, from_skyrim=True)
    logs = []
    ctl.log = logs.append
    ctl._look = lambda pid: ("unknown", None)
    for _ in range(5):
        ctl._skyrim_events(snap(4242))
    assert not ctl.quit_requested.is_set() and sum("Couldn't look at Skyrim's game program" in m for m in logs) == 1


def test_it_only_watches_when_the_setting_is_on_and_the_mod_started_the_tray_app_and_has_a_number(program):
    gone = lambda pid: ("gone", None)
    for kw, st in (({"from_skyrim": True}, snap(4242, auto=False)), ({"from_skyrim": False}, snap(4242)), ({"from_skyrim": True}, snap(None))):
        ctl = make_controller(program, **kw)
        logs = []
        ctl.log = logs.append
        ctl._look = gone
        ctl._skyrim_events(st)
        assert not ctl.quit_requested.is_set(), (kw, st)
    assert any("didn't send Skyrim's game program number" in m for m in logs), "no number: said once, never an error"


# ---- the channel carries the number ----
@pytest.fixture
def channel(info):                                           # noqa: F811
    st = local_channel.SkyrimState()
    app = FastAPI()
    app.include_router(local_channel.build_router(st, lambda: "9.9.9", lambda: 8000, lambda f, v: None, lambda: {}, lambda: None))
    local_channel.write_info(port=8000, command=["x"], program_folder="p", settings_folder="p")
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {local_channel.read_info()['token']}"})
    c.st = st
    return c


def test_started_remembers_the_process_number_and_tray_status_hands_it_on(channel):
    channel.post("/local/skyrim/started", json={"folder": "E:\\Skyrim", "modVersion": "3.0.0", "pid": 4242})
    assert channel.st.pid == 4242
    channel.post("/local/skyrim/started", json={"processId": 77})
    assert channel.st.pid == 77, "the other spelling works too, and a second run replaces the number"
    for bad in ({"pid": -1}, {"pid": "12"}, {"pid": True}, {"pid": 2 ** 40}, {}):
        channel.post("/local/skyrim/started", json=bad)
        assert channel.st.pid is None, bad
    assert channel.st.to_json()["pid"] is None
