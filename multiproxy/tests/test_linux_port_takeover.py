"""The "another MultiProxy is already running -- stop it and start fresh" takeover on Linux (2026-09-30).
On Windows it asks netstat/PowerShell/taskkill; on Linux the same three questions are answered from
/proc and with signals. These tests run only on Linux (there is nothing to look at in /proc elsewhere).
"""
import datetime
import os
import signal
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc, Linux only")


@pytest.fixture
def listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    yield s
    s.close()


def test_the_listener_of_a_port_is_found_by_pid(proxy_module, listener):
    assert proxy_module._port_listener_pid(listener.getsockname()[1]) == os.getpid()


def test_a_port_nobody_listens_on_is_none(proxy_module):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert proxy_module._port_listener_pid(free) is None


def test_process_info_has_a_command_line_and_a_sensible_start_time(proxy_module):
    info = proxy_module._process_info(os.getpid())
    assert info is not None and "python" in info["command_line"].lower()
    now = datetime.datetime.now()
    assert now - datetime.timedelta(days=1) < info["started_at"] <= now + datetime.timedelta(seconds=5)


def test_process_info_for_a_pid_that_does_not_exist_is_none(proxy_module):
    assert proxy_module._process_info(4194000) is None


def test_the_other_proxy_is_stopped_politely_first(proxy_module):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        proxy_module._stop_process_tree_by_pid(p.pid)
        assert p.wait(timeout=10) == -signal.SIGTERM
    finally:
        if p.poll() is None:
            p.kill()


def test_a_stubborn_proxy_is_killed_after_a_few_seconds(proxy_module):
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    p = subprocess.Popen([sys.executable, "-c", code], start_new_session=True, stdout=subprocess.PIPE)
    try:
        p.stdout.readline()
        started = time.monotonic()
        proxy_module._stop_process_tree_by_pid(p.pid)
        assert p.wait(timeout=10) == -signal.SIGKILL
        assert 2.5 < time.monotonic() - started < 8
    finally:
        if p.poll() is None:
            p.kill()


def test_stopping_something_already_gone_is_quiet(proxy_module):
    proxy_module._stop_process_tree_by_pid(4194000)
