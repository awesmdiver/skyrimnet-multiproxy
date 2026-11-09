"""Watch activity live (2026-11-05): the window used to open and close at once. Cause, proven with throwaway programs: the tray has no console, and a child started with
stdin=DEVNULL gets the tray's empty output handles instead of its new console, so its first print failed (exit 120). Fake processes only."""
import io

import hidden_process
import multiproxy_tray as mt
from tests.test_tray import _no_server_left_behind, info, make_controller, program  # noqa: F401  (the scratch program-folder fixtures)


def test_the_live_log_window_is_started_with_no_standard_stream_arguments_and_its_own_console(program, monkeypatch):
    seen = []
    monkeypatch.setattr(mt, "IS_WINDOWS", True)
    monkeypatch.setattr(mt.hidden_process, "popen", lambda cmd, **kw: seen.append((cmd, kw)))
    ctl = make_controller(program)
    ctl._default_spawn([mt.server_python(), "multiproxy_tray.py", "live-log"], 0x10)
    cmd, kw = seen[0]
    assert kw["creationflags"] == 0x10 and "stdin" not in kw and "stdout" not in kw and "stderr" not in kw


def test_the_other_log_window_and_helpers_keep_stdin_closed(program, monkeypatch):
    seen = []
    monkeypatch.setattr(mt, "IS_WINDOWS", True)
    monkeypatch.setattr(mt.hidden_process, "popen", lambda cmd, **kw: seen.append(kw))
    make_controller(program)._default_spawn(["x", "view-logs"], 0)
    assert seen[0]["stdin"] is not None and seen[0]["creationflags"] == 0


def test_hidden_process_never_mixes_no_window_with_a_window_the_caller_asked_for(monkeypatch):
    monkeypatch.setattr(hidden_process.os, "name", "nt")
    assert hidden_process._with_flags({"creationflags": 0x10})["creationflags"] == 0x10, "the one deliberate window"
    assert hidden_process._with_flags({})["creationflags"] == hidden_process.CREATE_NO_WINDOW
    assert hidden_process._with_flags({"creationflags": 0x200})["creationflags"] == 0x200 | hidden_process.CREATE_NO_WINDOW, "every helper still gets no window"


def test_a_live_log_that_fails_stays_open_says_what_went_wrong_and_writes_it_down(tmp_path):
    out, said, waited = io.StringIO(), [], []

    def broken(path):
        raise OSError(22, "Invalid argument")
    code = mt.run_live_log(tmp_path / "x.log", out=out, wait=lambda: waited.append(1), note=said.append, follow=broken)
    shown = out.getvalue()
    assert code == 1 and waited == [1], "it waits for Enter instead of closing"
    assert "Could not start the live activity window: OSError: [Errno 22] Invalid argument" in shown and "Press Enter to close this window." in shown
    assert said and "Could not start the live activity window: OSError" in said[0], "the same text goes to the tray's log"


def test_a_broken_screen_does_not_hide_the_failure_from_the_log_and_ctrl_c_or_success_change_nothing(tmp_path):
    class Dead(io.StringIO):
        def write(self, s):
            raise OSError(22, "Invalid argument")
    said = []

    def broken(path):
        raise RuntimeError("boom")
    assert mt.run_live_log(tmp_path / "x.log", out=Dead(), wait=lambda: (_ for _ in ()).throw(EOFError()), note=said.append, follow=broken) == 1 and "RuntimeError: boom" in said[0]

    def interrupted(path):
        raise KeyboardInterrupt
    assert mt.run_live_log(tmp_path / "x.log", follow=interrupted, wait=lambda: (_ for _ in ()).throw(AssertionError("must not wait"))) == 0
    assert mt.run_live_log(tmp_path / "x.log", follow=lambda p: 0, wait=lambda: (_ for _ in ()).throw(AssertionError("must not wait"))) == 0
