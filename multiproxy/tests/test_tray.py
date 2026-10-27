"""MultiProxy in the tray (2026-10-02): the words, the one-instance rule, the server as a hidden child, the Skyrim mod's local channel, the pointer
file, Start with Windows (a stand-in registry, never the real one), the log windows, and the server-side tray mode. A fake server program
(tests/fake_multiproxy_server.py) stands in for multiproxy.py where a real process is needed."""
import io
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hidden_process
import local_channel
import multiproxy_tray as mt
import settings_files

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).resolve().parent / "fake_multiproxy_server.py"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeRegistry:
    """A winreg stand-in: nothing here ever touches the real registry."""
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, res, access):
        class K:
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return K()

    def SetValueEx(self, k, name, res, typ, value): self.values[name] = value

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def info(tmp_path, monkeypatch):
    p = tmp_path / "appdata" / "install-info.json"
    monkeypatch.setenv("MULTIPROXY_INSTALL_INFO", str(p))
    return p


@pytest.fixture
def program(tmp_path, monkeypatch, info):
    """A temp 'program folder' whose multiproxy.py is the fake server, on a spare port."""
    d = tmp_path / "program"
    d.mkdir()
    shutil.copy(FAKE, d / "multiproxy.py")
    port = free_port()
    monkeypatch.setenv("MULTIPROXY_PORT", str(port))
    local_channel.write_info(port=port, command=["x"], program_folder=str(d), settings_folder=str(d))
    return d


_made = []


@pytest.fixture(autouse=True)
def _no_server_left_behind():
    """Whatever a test started, a failing test included, is ended here: no fake server may outlive its test."""
    yield
    while _made:
        _made.pop().host.kill()


def make_controller(program, **kw):
    reg = kw.pop("registry", FakeRegistry())
    calls = []
    ctl = mt.Controller(program, registry=reg, open_url=calls.append, spawn_window=lambda cmd, flags: calls.append((cmd, flags)), **kw)
    ctl.calls = calls
    ctl.reg = reg
    _made.append(ctl)
    return ctl


def wait_state(ctl, state, seconds=15):
    t = time.time()
    while time.time() - t < seconds:
        ctl.poll()
        if ctl.state == state:
            return True
        time.sleep(0.1)
    return False


# ---- the words ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("state,ready,color", [("running", 3, "green"), ("running", 0, "yellow"), ("starting", 0, "yellow"), ("stopped", 0, "red"),
                                               ("failed", 0, "red"), ("crashed", 0, "red")])
def test_the_dot(state, ready, color):
    assert mt.icon_color(state, ready) == color


def test_the_hover_texts_are_the_specs():
    assert mt.hover_text("running", 10, 14) == "MultiProxy is running · 10 of 14 models ready"
    assert mt.hover_text("starting") == "MultiProxy: starting…"
    assert mt.hover_text("stopped") == "MultiProxy: stopped"
    assert mt.hover_text("failed", reason="another program is using MultiProxy's address. Check Help to change it.") == \
        "MultiProxy couldn't start: another program is using MultiProxy's address. Check Help to change it. Click 'View recent activity' to see why."
    assert mt.hover_text("failed", reason="a needed file is missing. Please reinstall MultiProxy.") == "MultiProxy couldn't start: a needed file is missing. Please reinstall MultiProxy. Click 'View recent activity' to see why."
    assert mt.hover_text("crashed") == "MultiProxy stopped. Click 'View recent activity' to see why, then try again."


def menu(**kw):
    base = dict(version="3.2.0", state="running", start_with_windows=False, start_with_windows_supported=True, start_stop_with_skyrim=False,
                skyrim_tick_enabled=True, update_available=False)
    base.update(kw)
    return mt.menu_model(**base)


def texts(items):
    return [i["text"] for i in items if i["text"]]


def test_the_menu_is_the_mockups_top_to_bottom():
    assert texts(menu()) == ["SkyrimNet MultiProxy", "3.2.0 · Running", "Open dashboard", "Stop MultiProxy", "View recent activity", "Watch activity live", "Start with Windows",
                             "Start and stop with Skyrim", "Quit MultiProxy"]
    assert menu()[1]["enabled"] is False and menu()[1]["key"] == "version" and menu()[0]["key"] == "title" and next(i for i in menu() if i["key"] == "open_dashboard")["default"] is True


@pytest.mark.parametrize("state,word,toggle", [("starting", "Starting", "Stop MultiProxy"), ("stopped", "Stopped", "Start MultiProxy"), ("failed", "Stopped", "Start MultiProxy"),
                                               ("crashed", "Stopped", "Start MultiProxy")])
def test_the_header_and_the_stop_start_item_follow_the_state(state, word, toggle):
    items = menu(state=state)
    assert items[1]["text"] == f"3.2.0 · {word}" and next(i for i in items if i["key"] == "toggle_server")["text"] == toggle


def test_a_failed_start_is_also_the_menus_first_line():
    items = menu(state="failed", problem="MultiProxy couldn't start: another program is using MultiProxy's address. Check Help to change it. Click 'View recent activity' to see why.")
    assert items[0] == {"key": "problem", "text": "MultiProxy couldn't start: another program is using MultiProxy's address. Check Help to change it. Click 'View recent activity' to see why.", "enabled": False}
    assert items[1]["key"] == "title" and items[2]["key"] == "version" and menu()[0]["key"] == "title"


def test_the_top_line_says_the_version_and_the_build_the_package_was_made_from():
    assert menu(build="2026-10-06-abc1234")[1]["text"] == "3.2.0 · built 2026-10-06-abc1234 · Running" and menu(build="2026-10-06-abc1234")[1]["enabled"] is False


def test_the_ticks_and_the_update_item():
    items = menu(start_with_windows=True, start_stop_with_skyrim=True)
    assert next(i for i in items if i["key"] == "start_with_windows")["checked"] is True and next(i for i in items if i["key"] == "start_stop_skyrim")["checked"] is True
    assert "● New version available…" not in texts(menu())
    items = menu(update_available=True)
    assert texts(items)[-2:] == ["● New version available…", "Quit MultiProxy"]
    assert "Start with Windows" not in texts(menu(start_with_windows_supported=False))


def test_the_skyrim_tick_is_disabled_with_its_reason_when_the_mod_isnt_there():
    it = next(i for i in menu(skyrim_tick_enabled=False) if i["key"] == "start_stop_skyrim")
    assert it["enabled"] is False and it["text"] == "Start and stop with Skyrim (install the mod from Settings)"


@pytest.mark.parametrize("log,code,expected", [
    (["MultiProxy can't start: port 8000 is already in use."], 1, "another program is using MultiProxy's address. Check Help to change it."),
    (["MultiProxy can't start: port 8000 is already in use.", "  This almost always means It is most likely already running"], 1, "another program is using MultiProxy's address. Check Help to change it."),
    (["MultiProxy can't start: port 8000 is already in use.", "  another MultiProxy is running"], 1, "MultiProxy is already running. Check the icons by your clock."),
    (["ModuleNotFoundError: No module named 'aiohttp'"], 1, "a needed file is missing. Please reinstall MultiProxy."),
    (["something odd"], 3, "something went wrong while starting. Click 'View recent activity' to see why."),
    ([], None, "something went wrong while starting. Click 'View recent activity' to see why."),
])
def test_why_it_could_not_start_in_plain_words(log, code, expected):
    assert mt.explain_start_failure(log, code, 8000) == expected


@pytest.mark.parametrize("auto,owned,seq,handled,expect", [
    (True, True, 1, 0, True), (True, False, 1, 0, False), (False, True, 1, 0, False), (True, True, 1, 1, False), (True, True, 1, None, False), (True, True, 5, 3, True)])
def test_quit_with_skyrim_only_when_on_and_the_mod_started_the_tray_app(auto, owned, seq, handled, expect):
    assert mt.should_quit_for_skyrim(auto_close=auto, owned_by_skyrim=owned, closed_seq=seq, handled_seq=handled) is expect


# ---- one tray app per device ---------------------------------------------------------------------------------------------------------------
def test_a_second_launch_opens_the_dashboard_and_does_not_start_another():
    port = free_port()
    first, second = mt.SingleInstance(port), mt.SingleInstance(port)
    opened = []
    assert first.acquire() and not second.acquire()
    first.serve(lambda: opened.append(1))
    assert second.tell_existing_to_open()
    for _ in range(50):
        if opened:
            break
        time.sleep(0.05)
    assert opened == [1]
    first.release()
    assert second.acquire(), "the lock goes away with the first one"
    second.release()


# ---- the pointer file and the local channel ---------------------------------------------------------------------------------------------
def test_the_pointer_file_has_what_the_mod_needs_and_keeps_its_token(info):
    a = local_channel.write_info(port=8000, command=["C:\\py\\pythonw.exe", "C:\\mp\\multiproxy_tray.py"], program_folder="C:\\mp", settings_folder="C:\\mp")
    b = local_channel.write_info(port=8123, command=["C:\\py\\pythonw.exe", "C:\\mp\\multiproxy_tray.py"], program_folder="C:\\mp", settings_folder="C:\\mp")
    got = json.loads(info.read_text(encoding="utf-8"))
    assert got["version"] == 1 and got["port"] == 8123 and a["token"] == b["token"] == got["token"] and len(got["token"]) >= 20
    assert got["tray"] == {"command": ["C:\\py\\pythonw.exe", "C:\\mp\\multiproxy_tray.py"], "workingDirectory": "C:\\mp", "fromSkyrimArg": "--from-skyrim"}


def test_the_default_pointer_location_is_under_appdata(monkeypatch):
    monkeypatch.delenv("MULTIPROXY_INSTALL_INFO", raising=False)
    monkeypatch.setenv("APPDATA", "C:\\Users\\x\\AppData\\Roaming")
    if os.name == "nt":
        assert str(local_channel.info_path()).endswith("SkyrimNet MultiProxy\\install-info.json")


@pytest.fixture
def channel(info):
    st = local_channel.SkyrimState()
    started, shut = [], []
    app = FastAPI()
    app.include_router(local_channel.build_router(st, lambda: "9.9.9", lambda: 8000, lambda f, v: started.append((f, v)), lambda: {"x": 1}, lambda: shut.append(1)))
    local_channel.write_info(port=8000, command=["x"], program_folder="p", settings_folder="p")
    token = local_channel.read_info()["token"]
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    c.st, c.started, c.shut, c.token, c.app = st, started, shut, token, app
    return c


def test_ping_needs_the_token(channel):
    assert channel.get("/local/ping").json() == {"ok": True, "version": "9.9.9", "port": 8000}
    assert channel.get("/local/ping", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert channel.get("/local/ping", headers={"Authorization": ""}).status_code == 401
    assert TestClient(channel.app).get("/local/ping").status_code == 401


def test_the_channel_is_this_pc_only(channel):
    far = TestClient(channel.app, client=("192.168.1.50", 50000))
    far.headers.update({"Authorization": f"Bearer {channel.token}"})
    assert far.get("/local/ping").status_code == 403 and far.post("/local/skyrim/closed").status_code == 403


def test_with_no_pointer_file_the_whole_channel_is_off(channel, info):
    info.unlink()
    for call in (lambda: channel.get("/local/ping"), lambda: channel.post("/local/skyrim/started", json={}), lambda: channel.post("/local/skyrim/closed"),
                 lambda: channel.get("/local/tray-status"), lambda: channel.post("/local/shutdown")):
        assert call().status_code == 404
    assert channel.started == [] and channel.shut == []


def test_started_remembers_the_folder_and_the_mod_version_and_closed_counts_up(channel):
    assert channel.post("/local/skyrim/started", json={"folder": "E:\\Skyrim", "modVersion": "3.0.0"}).json() == {"ok": True}
    assert channel.started == [("E:\\Skyrim", "3.0.0")] and channel.st.running and channel.st.folder == "E:\\Skyrim"
    channel.post("/local/skyrim/closed")
    channel.post("/local/skyrim/closed")
    s = channel.get("/local/tray-status").json()
    assert s["x"] == 1 and s["skyrim"] == {"running": False, "folder": "E:\\Skyrim", "modVersion": "3.0.0", "startedSeq": 1, "closedSeq": 2, "pid": None}
    assert channel.post("/local/skyrim/started", content=b"not json").status_code == 200, "a garbled body is still a 'started'"


def test_shutdown_asks_for_a_clean_stop(channel):
    assert channel.post("/local/shutdown").json() == {"ok": True} and channel.shut == [1]


# ---- the real server's side of it --------------------------------------------------------------------------------------------------
def test_a_run_from_a_terminal_has_no_channel_at_all(test_client, info):
    assert not info.exists()
    assert test_client.get("/local/ping").status_code == 404 and test_client.post("/local/skyrim/closed").status_code == 404


def test_the_real_servers_tray_status_and_the_started_message(proxy_module, test_client, info, monkeypatch, tmp_path):
    local_channel.write_info(port=8000, command=["x"], program_folder="p", settings_folder="p")
    h = {"Authorization": f"Bearer {local_channel.read_info()['token']}"}
    monkeypatch.setattr(proxy_module, "PLUGIN_VERSION", None)
    monkeypatch.setattr(proxy_module, "skyrim_install_path", "")
    monkeypatch.setattr(proxy_module, "_save_config", lambda c: None)
    s = test_client.get("/local/tray-status", headers=h).json()
    assert s["version"] == proxy_module.PROXY_VERSION and s["modDetected"] is False and s["totalAIs"] >= 1 and set(s) >= {"autoClose", "update", "readyCount", "skyrim", "port"}
    skyrim = tmp_path / "Skyrim"
    skyrim.mkdir()
    assert test_client.post("/local/skyrim/started", headers=h, json={"folder": str(skyrim), "modVersion": "3.1.0"}).status_code == 200
    assert proxy_module.PLUGIN_VERSION == "3.1.0" and proxy_module.skyrim_install_path == str(skyrim)
    assert test_client.get("/local/tray-status", headers=h).json()["modDetected"] is True


def test_started_does_not_replace_a_folder_the_player_already_linked(proxy_module, test_client, info, monkeypatch, tmp_path):
    local_channel.write_info(port=8000, command=["x"], program_folder="p", settings_folder="p")
    h = {"Authorization": f"Bearer {local_channel.read_info()['token']}"}
    other = tmp_path / "Other"
    other.mkdir()
    monkeypatch.setattr(proxy_module, "skyrim_install_path", "D:\\mine")
    monkeypatch.setattr(proxy_module, "PLUGIN_VERSION", None)                     # (put back afterwards: the started message sets it)
    monkeypatch.setattr(proxy_module, "_save_config", lambda c: None)
    test_client.post("/local/skyrim/started", headers=h, json={"folder": str(other), "modVersion": "3.1.0"})
    assert proxy_module.skyrim_install_path == "D:\\mine"


# ---- the server as a hidden child --------------------------------------------------------------------------------------------------------
def test_the_server_starts_hidden_in_tray_mode_and_answers_and_stops_cleanly(program, monkeypatch):
    seen = []
    real_popen = hidden_process.subprocess.Popen

    def recording_popen(*a, **k):                   # what really reaches the operating system, after hidden_process has added its flag
        seen.append(k)
        return real_popen(*a, **k)
    monkeypatch.setattr(hidden_process.subprocess, "Popen", recording_popen)
    host = mt.ServerHost(program)
    ctl = make_controller(program, host=host)
    assert ctl.version == "9.9.9"
    host.start()
    assert host.state == "starting" and ctl.hover() == "MultiProxy: starting…" and ctl.color() == "yellow"
    assert wait_state(ctl, "running")
    assert ctl.hover() == "MultiProxy is running · 3 of 14 models ready" and ctl.color() == "green"
    assert seen[0]["env"]["MULTIPROXY_TRAY"] == "1" and seen[0]["creationflags"] & hidden_process.CREATE_NO_WINDOW if os.name == "nt" else True
    assert "tray=1" in (program / "starts.txt").read_text(encoding="utf-8")
    pid = host.proc.pid
    ctl.do_toggle_server()
    assert host.state == "stopped" and host.proc is None and "shutdown asked for" in (program / "multiproxy.log").read_text(encoding="utf-8")
    assert ctl.hover() == "MultiProxy: stopped" and ctl.color() == "red" and menu_text(ctl, "toggle_server") == "Start MultiProxy"
    ctl.do_toggle_server()
    assert wait_state(ctl, "running") and host.proc.pid != pid
    ctl.shutdown()
    assert host.state == "stopped"


def menu_text(ctl, key):
    return next(i["text"] for i in ctl.menu() if i["key"] == key)


def test_a_server_that_cannot_start_says_why(program, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_PORT", "1")
    ctl = make_controller(program)
    ctl.host.start()
    assert wait_state(ctl, "failed")
    assert ctl.hover() == "MultiProxy couldn't start: another program is using MultiProxy's address. Check Help to change it. Click 'View recent activity' to see why." and ctl.color() == "red"


def test_a_missing_part_is_named(program, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_MODULE", "1")
    ctl = make_controller(program)
    ctl.host.start()
    assert wait_state(ctl, "failed") and "a needed file is missing. Please reinstall MultiProxy." in ctl.hover()


def test_a_server_that_dies_after_running_is_a_crash_not_a_failed_start(program, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT_AFTER", "1.5:3")
    ctl = make_controller(program)
    ctl.host.start()
    assert wait_state(ctl, "running")
    assert wait_state(ctl, "crashed") and ctl.hover() == "MultiProxy stopped. Click 'View recent activity' to see why, then try again."


def test_a_restart_request_from_the_server_starts_it_again(program, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT_AFTER", "1:75")
    ctl = make_controller(program)
    ctl.host.start()
    assert wait_state(ctl, "running")
    t = time.time()
    while time.time() - t < 15 and (program / "starts.txt").read_text(encoding="utf-8").count("tray=1") < 2:
        ctl.poll()
        time.sleep(0.1)
    assert (program / "starts.txt").read_text(encoding="utf-8").count("tray=1") >= 2, "exit code 75 means start me again"
    ctl.host.stop()


def test_a_server_stuck_while_starting_is_called_stuck(program, monkeypatch):
    monkeypatch.setenv("FAKE_NEVER_ANSWER", "1")
    monkeypatch.setattr(mt, "START_TIMEOUT_SECONDS", 1.5)
    ctl = make_controller(program)
    ctl.host.start()
    t = time.time()
    while time.time() - t < 12 and ctl.state != "failed":
        ctl.poll()
        time.sleep(0.2)
    assert ctl.state == "failed" and "took too long" in ctl.hover() and ctl.host.proc is None


def test_the_port_comes_from_the_environment_then_the_settings_page_then_8000(tmp_path, monkeypatch):
    monkeypatch.delenv("MULTIPROXY_PORT", raising=False)
    assert mt.configured_port(tmp_path) == 8000
    (tmp_path / "config.json").write_text(json.dumps({"main_port": 8123}))
    assert mt.configured_port(tmp_path) == 8123
    monkeypatch.setenv("MULTIPROXY_PORT", "8456")
    assert mt.configured_port(tmp_path) == 8456


def test_the_server_python_is_never_pythonw(monkeypatch):
    monkeypatch.setattr(sys, "executable", "C:\\Py\\pythonw.exe")
    if os.name == "nt":
        assert mt.server_python() == "C:\\Py\\python.exe"


# ---- the controller: ticks, windows, dashboard, update, Skyrim -----------------------------------------------------------------------------
def test_start_with_windows_writes_the_per_user_run_entry_and_the_config_never_the_real_registry(program):
    ctl = make_controller(program)
    assert ctl.start_with_windows_on() is False
    ctl.do_start_with_windows()
    assert list(ctl.reg.values) == ["SkyrimNet MultiProxy"]
    cmd = ctl.reg.values["SkyrimNet MultiProxy"]
    assert cmd.count('"') == 4 and cmd.endswith('multiproxy_tray.py"') and "--from-skyrim" not in cmd
    assert json.loads((program / "config.json").read_text())["start_with_windows"] is True and ctl.start_with_windows_on() is True
    ctl.do_start_with_windows()
    assert ctl.reg.values == {} and json.loads((program / "config.json").read_text())["start_with_windows"] is False


def test_the_run_command_uses_pythonw_so_there_is_no_console(program, monkeypatch):
    monkeypatch.setattr(sys, "executable", "C:\\Py\\python.exe")
    cmd = settings_files.tray_start_command("C:\\mp")
    if os.name == "nt":
        assert cmd == ["C:\\Py\\pythonw.exe", str(Path("C:\\mp") / "multiproxy_tray.py")]


def test_start_and_stop_with_skyrim_needs_the_mod_then_changes_the_server_live_or_the_file(program, monkeypatch):
    ctl = make_controller(program)
    ini = program / "multiproxy.ini"
    ini.write_text("[General]\n; my comment\nAutoCloseWithSkyrim = false\n", encoding="utf-8")
    ctl.do_start_stop_skyrim()                                   # mod not known yet: nothing happens
    assert "false" in ini.read_text(encoding="utf-8")
    ctl.snapshot = {"modDetected": True, "autoClose": False}
    ctl.do_start_stop_skyrim()                                   # server stopped: the file is edited, comment kept
    t = ini.read_text(encoding="utf-8")
    assert "AutoCloseWithSkyrim = true" in t and "; my comment" in t
    ctl.host.start()
    assert wait_state(ctl, "running")
    ctl.snapshot = {**ctl.snapshot, "autoClose": True}
    ctl.do_start_stop_skyrim()                                   # server running: it is told, so the change is live
    assert wait_state(ctl, "running") and ctl.snapshot["autoClose"] is False
    ctl.shutdown()


def test_open_dashboard_and_the_update_item_open_the_right_pages(program):
    ctl = make_controller(program)
    ctl.do_open_dashboard()
    assert ctl.calls == [f"http://127.0.0.1:{ctl.host.port}/"]
    ctl.snapshot = {"update": {"state": "available", "url": "https://example.test/r"}}
    assert ctl.update_available()
    ctl.do_update()
    assert ctl.calls[-1] == "https://example.test/r"
    ctl.snapshot = {"update": {"state": "available"}}
    ctl.do_update()
    assert ctl.calls[-1] == mt.RELEASES_URL


def test_the_log_windows_are_started_with_the_right_flags(program):
    ctl = make_controller(program)
    ctl.do_view_logs()
    ctl.do_live_log()
    (view, vflags), (live, lflags) = ctl.calls
    assert view[1].endswith("multiproxy_tray.py") and view[2:] == ["view-logs", "--log", str(program / "multiproxy.log")] and vflags == 0
    assert live[2] == "live-log" and lflags == 0x10, "a real console window, and closing it never touches the server"


def test_on_linux_the_live_log_opens_in_a_terminal_program_and_never_passes_windows_flags(program, monkeypatch):
    """creationflags only exists on Windows (a nonzero value is an error elsewhere), and "its own console window" is a terminal program on Linux."""
    started = []
    monkeypatch.setattr(mt, "IS_WINDOWS", False)
    monkeypatch.setattr(mt.hidden_process, "popen", lambda cmd, **kw: started.append((cmd, kw)))
    monkeypatch.setattr(mt.shutil, "which", lambda exe: "/usr/bin/" + exe if exe == "konsole" else None)
    ctl = mt.Controller(program, host=mt.ServerHost(program, popen=lambda *a, **k: None))
    ctl.do_view_logs()
    ctl.do_live_log()
    assert all("creationflags" not in kw for _, kw in started)
    assert started[0][0][1].endswith("multiproxy_tray.py") and started[0][0][2] == "view-logs"
    assert started[1][0][:2] == ["/usr/bin/konsole", "-e"] and "live-log" in started[1][0]


def test_on_a_linux_with_no_terminal_program_the_live_log_says_so_and_opens_nothing(program, monkeypatch):
    said, started = [], []
    monkeypatch.setattr(mt, "IS_WINDOWS", False)
    monkeypatch.setattr(mt.hidden_process, "popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(mt.shutil, "which", lambda exe: None)
    ctl = mt.Controller(program, host=mt.ServerHost(program, popen=lambda *a, **k: None))
    ctl.log = said.append
    ctl.do_live_log()
    assert started == [] and said and "multiproxy logs" in said[0]


def test_the_tray_app_writes_the_pointer_file_with_its_own_port_and_command(program, info):
    ctl = make_controller(program)
    got = ctl.write_pointer()
    assert got["port"] == ctl.host.port and got["tray"]["command"][-1].endswith("multiproxy_tray.py") and got["programFolder"] == str(program)


# ---- quit with Skyrim, end to end through a real (fake) server ------------------------------------------------------------------------------
def closed_event(ctl):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{ctl.host.port}/test/closed", method="POST")
    urllib.request.urlopen(req, timeout=3).read()


@pytest.mark.parametrize("from_skyrim,auto,quits", [(True, True, True), (False, True, False), (True, False, False)])
def test_closing_skyrim_quits_the_whole_tray_only_when_on_and_the_mod_started_it(program, from_skyrim, auto, quits):
    ctl = make_controller(program, from_skyrim=from_skyrim)
    ctl.host.start()
    assert wait_state(ctl, "running")
    assert not ctl.quit_requested.is_set(), "the first look is a baseline, never an event"
    ctl.snapshot["autoClose"] = auto
    ctl._http("POST", "/config/settings", {"startStopWithSkyrim": auto})
    closed_event(ctl)
    t = time.time()
    while time.time() - t < 5 and not ctl.quit_requested.is_set():
        ctl.poll()
        time.sleep(0.1)
        if not quits and time.time() - t > 2:
            break
    assert ctl.quit_requested.is_set() is quits
    ctl.shutdown()


def test_a_closed_message_from_before_the_tray_app_started_is_not_acted_on(program, monkeypatch):
    monkeypatch.setenv("FAKE_CLOSED_SEQ", "7")
    ctl = make_controller(program, from_skyrim=True)
    ctl.host.start()
    assert wait_state(ctl, "running")
    ctl._http("POST", "/config/settings", {"startStopWithSkyrim": True})
    for _ in range(5):
        ctl.poll()
        time.sleep(0.1)
    assert not ctl.quit_requested.is_set()
    ctl.shutdown()


# ---- the logs ------------------------------------------------------------------------------------------------------------------------------
def test_the_logs_are_masked_like_the_couriers(tmp_path):
    log = tmp_path / "multiproxy.log"
    log.write_text("2026-10-02 00:00:01 INFO     Ready. 10 of 14 AIs ready.\n"
                   "2026-10-02 00:00:02 INFO     key sk-ant-api03-ABCDEFGHIJKLMNOP used\n"
                   '2026-10-02 00:00:03 INFO     body {"messages": [{"role": "user", "content": "my secret story"}]}\n', encoding="utf-8")
    shown = "\n".join(mt.masked_tail(log))
    assert "Ready. 10 of 14 AIs ready." in shown and "sk-ant-api03" not in shown and "my secret story" not in shown and "•••" in shown
    assert mt.masked_tail(tmp_path / "nothing.log") == []


def test_the_live_log_shows_the_end_then_follows_new_lines_masked_and_survives_rotation(tmp_path):
    log = tmp_path / "multiproxy.log"
    log.write_text("old line one\nold line two\n", encoding="utf-8")
    out = io.StringIO()
    step = {"n": 0}

    def stop():
        step["n"] += 1
        if step["n"] == 2:
            with open(log, "a", encoding="utf-8") as f:
                f.write("new line with sk-ant-api03-ZZZZZZZZZZZZZZ\n")
        if step["n"] == 4:
            log.write_text("fresh file after rotation\n", encoding="utf-8")
        return step["n"] > 7
    mt.follow_log(log, out=out, poll=0.01, stop=stop)
    text = out.getvalue()
    assert text.startswith("SkyrimNet MultiProxy: live activity (close this anytime; MultiProxy will keep running)\n")
    assert "old line two" in text and "new line with" in text and "sk-ant-api03" not in text and "fresh file after rotation" in text


# ---- the server's own tray mode ---------------------------------------------------------------------------------------------------------
def test_tray_mode_keeps_a_rotating_log_always(proxy_module):
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
    # (the rotating file handler itself is built inside offloop_log.OffLoopFileHandler now: written by its own thread, not on the event loop)
    assert "OffLoopFileHandler" in src and "if ENABLE_LOGGING or _TRAY_MODE:" in src and '"maxBytes": 1_000_000' in src and '"backupCount": 3' in src
    assert "RotatingFileHandler" in (ROOT / "offloop_log.py").read_text(encoding="utf-8")


def test_tray_mode_restarts_by_exiting_with_the_restart_code(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "_TRAY_MODE", True)
    monkeypatch.setattr(proxy_module.local_svc, "stop_all_sync", lambda: None)
    monkeypatch.setattr(proxy_module.hidden_process, "popen", lambda *a, **k: pytest.fail("a tray copy must not start another server itself"))
    codes = []
    monkeypatch.setattr(proxy_module.os, "_exit", lambda c: (codes.append(c), (_ for _ in ()).throw(SystemExit(c))))
    import ast
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")                    # (the suite replaces _restart_self with a do-nothing: run the real one)
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_restart_self")
    ns = dict(proxy_module.__dict__)
    exec(ast.get_source_segment(src, fn), ns)
    with pytest.raises(SystemExit):
        ns["_restart_self"](8123)
    assert codes == [mt.RESTART_EXIT_CODE]


def test_tray_mode_has_no_skyrim_watcher_of_its_own(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "AUTO_CLOSE_WITH_SKYRIM", True)
    monkeypatch.setattr(proxy_module, "_skyrim_watcher_thread", None)
    monkeypatch.setattr(proxy_module, "_TRAY_MODE", True)
    proxy_module._ensure_skyrim_watcher()
    assert proxy_module._skyrim_watcher_thread is None


@pytest.mark.parametrize("policy,is_mp,expect_exit", [("ask", True, False), ("always", True, False), ("never", True, True), ("ask", False, True)])
def test_a_tray_copy_takes_over_a_multiproxy_left_running_without_its_tray(proxy_module, monkeypatch, policy, is_mp, expect_exit):
    monkeypatch.setattr(proxy_module, "_TRAY_MODE", True)
    monkeypatch.setattr(proxy_module, "REPLACE_RUNNING_PROXY_POLICY", policy)
    monkeypatch.setattr(proxy_module, "_identify_port_holder", lambda port: (4321, {"command_line": "x", "started_at": 0}, is_mp))
    stopped = []
    monkeypatch.setattr(proxy_module, "_stop_process_tree_by_pid", stopped.append)
    monkeypatch.setattr(proxy_module, "_wait_for_port_free", lambda port, timeout=5.0: True)
    monkeypatch.setattr(proxy_module, "_ask_yes_no", lambda q: pytest.fail("there is nobody to ask"))
    monkeypatch.setattr(proxy_module.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(proxy_module.os, "_exit", lambda c: (_ for _ in ()).throw(SystemExit(c)))
    if expect_exit:
        with pytest.raises(SystemExit):
            proxy_module._resolve_port_conflict(8000)
        assert stopped == []
    else:
        proxy_module._resolve_port_conflict(8000)
        assert stopped == [4321]


def test_start_with_windows_on_the_page_writes_the_real_entry_only_for_a_tray_copy(proxy_module, test_client, monkeypatch, tmp_path):
    reg = FakeRegistry()
    monkeypatch.setattr(proxy_module.settings_files, "run_entry", lambda folder, registry=None: settings_files.StartWithWindows if False else __import__("courier_system").StartWithWindows("cmd", registry=reg, name=settings_files.STARTUP_NAME))
    cfg = {}
    monkeypatch.setattr(proxy_module, "_load_config", lambda: cfg)
    monkeypatch.setattr(proxy_module, "_save_config", lambda c: cfg.update(c))
    monkeypatch.setattr(proxy_module, "_started_by_tray", lambda: False)
    test_client.post("/config/settings", json={"startWithWindows": True})
    assert reg.values == {} and cfg["start_with_windows"] is True, "a terminal run never touches the real entry"
    monkeypatch.setattr(proxy_module, "_started_by_tray", lambda: True)
    s = test_client.post("/config/settings", json={"startWithWindows": True}).json()
    assert reg.values == {"SkyrimNet MultiProxy": "cmd"} and s["startWithWindows"] is True
    s = test_client.post("/config/settings", json={"startWithWindows": False}).json()
    assert reg.values == {} and s["startWithWindows"] is False


def test_the_tray_files_start_programs_only_through_hidden_process():
    import ast
    for name in ("multiproxy_tray.py", "local_channel.py", "settings_files.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "subprocess" and n.attr in {"run", "Popen", "call"}]
        assert not bad, (name, bad)


def test_the_icon_is_drawn_in_three_colours():
    pytest.importorskip("PIL")
    px = {c: mt.make_icon(c).getpixel((50, 50)) for c in ("green", "yellow", "red")}
    assert px["green"] != px["yellow"] != px["red"] and px["green"][:3] == (52, 199, 89)


def test_every_menu_action_has_the_two_parameters_pystray_demands():
    """pystray refuses a menu action that doesn't take exactly 0, 1 or 2 parameters (found by running the real thing); keep that true."""
    import inspect
    ctl = mt.Controller(ROOT)
    tray = mt.Tray.__new__(mt.Tray)
    tray.ctl = ctl
    assert len(inspect.signature(mt.Tray._action(tray, "quit")).parameters) == 2
    assert len(inspect.signature(mt.Tray._checked({"checked": True})).parameters) == 1


def test_the_real_pystray_accepts_the_whole_menu():
    try:
        ps = pytest.importorskip("pystray")
    except Exception as e:      # noqa: BLE001 -- on Linux pystray itself refuses to import without a screen (a bare container, ssh)
        pytest.skip(f"pystray needs a display here: {type(e).__name__}")
    tray = mt.Tray(mt.Controller(ROOT))
    assert len(tray._menu().items) >= 8


def test_a_replaced_server_starts_the_closed_count_again(program):
    """Found by the real check: the tray app's first look was at an old server it was about to replace (it had counted one "closed" already);
    the new server started again from zero, so the real "closed" looked like an old one and nothing quit."""
    ctl = make_controller(program, from_skyrim=True)
    snap = lambda boot, seq: {"boot": boot, "autoClose": True, "skyrim": {"closedSeq": seq}}
    ctl._skyrim_events(snap("old", 1))                 # baseline: the old server had one "closed"
    assert not ctl.quit_requested.is_set()
    ctl._skyrim_events(snap("new", 0))                 # a different server: baseline again
    ctl._skyrim_events(snap("new", 1))                 # a real "closed" on the new server
    assert ctl.quit_requested.is_set()
