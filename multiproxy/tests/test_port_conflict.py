"""Tests for "When the port is busy, offer to stop the old proxy and start fresh" (2026-09-30).

Real report: the director hit "Port 9999 is already in use" with no visible proxy window to find
and close (a hidden proxy, started in the background by design side's own checks) -- he wants the
proxy able to stop the old copy and start a new one itself, but ONLY when it's confident the old
copy really is another MultiProxy, never anything else that happens to be using the port.

`os._exit` is mocked in every test that reaches it -- the real one would kill the test process
itself. `_resolve_port_conflict`'s own body has an explicit `return` right after every `os._exit(1)`
call specifically so a mocked (non-exiting) os._exit here still stops the function at the same
point a real one would, rather than falling through into whatever branch happens to follow.
"""
import datetime
import os
import json
from unittest.mock import MagicMock, patch

import pytest


def _netstat_line(port: int, pid: int) -> str:
    return f"  TCP    127.0.0.1:{port}         0.0.0.0:0              LISTENING       {pid}"


def _cim_json(command_line: str, created: str = "2026-09-30T14:41:00.000000-07:00") -> str:
    return json.dumps({"CommandLine": command_line, "CreationDate": created})


def _health_body(**extra) -> bytes:
    body = {"status": "healthy", "claude_cli_found": True, "claude_path": None}
    body.update(extra)
    return json.dumps(body).encode("utf-8")


class _FakeHttpResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --- _port_listener_pid -------------------------------------------------------------------------

def test_port_listener_pid_finds_the_real_listening_pid(proxy_module):
    fake = MagicMock(stdout=_netstat_line(8000, 4242) + "\n" + _netstat_line(9999, 777))
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", return_value=fake):
        assert proxy_module._port_listener_pid(8000) == 4242
        assert proxy_module._port_listener_pid(9999) == 777


def test_port_listener_pid_none_when_nothing_is_listening(proxy_module):
    fake = MagicMock(stdout=_netstat_line(8000, 4242))
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", return_value=fake):
        assert proxy_module._port_listener_pid(9999) is None


def test_port_listener_pid_none_on_non_windows(proxy_module):
    with patch.object(proxy_module.os, "name", "posix"):
        assert proxy_module._port_listener_pid(8000) is None


def test_port_listener_pid_none_if_netstat_itself_fails(proxy_module):
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", side_effect=OSError("no netstat")):
        assert proxy_module._port_listener_pid(8000) is None


# --- _process_info -------------------------------------------------------------------------------

def test_process_info_parses_a_real_cim_response_pwsh7_shape(proxy_module):
    """PowerShell 7+ ("pwsh")'s own ConvertTo-Json shape for a [datetime] -- a clean ISO-8601
    string. Confirmed live via this session's own interactive PowerShell tool."""
    real_cmdline = r'"C:\Python\py.exe" multiproxy.py'  # a real Windows path, one literal backslash each
    fake = MagicMock(stdout=_cim_json(real_cmdline))
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", return_value=fake):
        info = proxy_module._process_info(4242)
    assert info["command_line"] == real_cmdline
    assert info["started_at"] == datetime.datetime.fromisoformat("2026-09-30T14:41:00.000000-07:00")


def test_process_info_parses_a_real_cim_response_windows_powershell_5_1_shape(proxy_module):
    """Windows PowerShell 5.1 (plain "powershell.exe", what every Windows install ships with, and
    what THIS dev machine's own "powershell" resolves to) serializes the SAME [datetime] value
    completely differently: the legacy JSON.NET `"/Date(ms)/"` wrapper, not an ISO string. Real,
    live-confirmed bug this session hit on its own first end-to-end test: code written against only
    the pwsh shape above silently returned None for every real running process on this exact
    machine, because "powershell" here IS 5.1."""
    real_cmdline = r'"C:\Python\py.exe" multiproxy.py'
    fake = MagicMock(stdout=_cim_json(real_cmdline, created="/Date(1790808812774)/"))
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", return_value=fake):
        info = proxy_module._process_info(4242)
    assert info["command_line"] == real_cmdline
    assert info["started_at"] == datetime.datetime.fromtimestamp(1790808812774 / 1000.0)


def test_process_info_none_if_the_process_is_already_gone(proxy_module):
    fake = MagicMock(stdout="null")
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", return_value=fake):
        assert proxy_module._process_info(4242) is None


def test_process_info_none_on_unparseable_output(proxy_module):
    fake = MagicMock(stdout="not json at all")
    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.subprocess, "run", return_value=fake):
        assert proxy_module._process_info(4242) is None


# --- _health_check_says_multiproxy -----------------------------------------------------------

def test_health_check_true_for_a_real_looking_response(proxy_module):
    with patch.object(proxy_module.urllib.request, "urlopen", return_value=_FakeHttpResponse(_health_body())):
        assert proxy_module._health_check_says_multiproxy() is not None


def test_health_check_false_when_unreachable(proxy_module):
    with patch.object(proxy_module.urllib.request, "urlopen", side_effect=OSError("refused")):
        assert proxy_module._health_check_says_multiproxy() is None


def test_health_check_false_for_an_unrelated_services_response(proxy_module):
    """Some other, unrelated local service that merely happens to also serve a 200 on /health --
    never enough on its own; genuinely different shape (no claude_cli_found, not "healthy")."""
    body = json.dumps({"status": "ok"}).encode()
    with patch.object(proxy_module.urllib.request, "urlopen", return_value=_FakeHttpResponse(body)):
        assert proxy_module._health_check_says_multiproxy() is None


# --- _identify_port_holder (the two-signal combination) ----------------------------------------

def test_identify_port_holder_true_when_both_signals_agree(proxy_module):
    with patch.object(proxy_module, "_port_listener_pid", return_value=4242), \
         patch.object(proxy_module, "_process_info", return_value={"command_line": "py multiproxy.py", "started_at": datetime.datetime.now()}), \
         patch.object(proxy_module, "_health_check_says_multiproxy", return_value={"status": "healthy"}):
        pid, info, is_multiproxy = proxy_module._identify_port_holder(8000)
    assert pid == 4242
    assert is_multiproxy is True


def test_identify_port_holder_false_when_cmdline_does_not_mention_proxy_py(proxy_module):
    """An unrelated program that merely happens to hold this port -- never stopped, no matter what
    /health says (and in practice it wouldn't answer this shape at all, but the command line alone
    already disqualifies it)."""
    with patch.object(proxy_module, "_port_listener_pid", return_value=9001), \
         patch.object(proxy_module, "_process_info", return_value={"command_line": "some_other_app.exe", "started_at": datetime.datetime.now()}), \
         patch.object(proxy_module, "_health_check_says_multiproxy", return_value={"status": "healthy"}):
        pid, info, is_multiproxy = proxy_module._identify_port_holder(8000)
    assert pid == 9001
    assert is_multiproxy is False


def test_identify_port_holder_false_when_health_check_fails_even_if_cmdline_matches(proxy_module):
    """Command line alone is not enough either -- a coincidental "multiproxy.py" match with nothing
    genuinely answering /health must not be trusted."""
    with patch.object(proxy_module, "_port_listener_pid", return_value=4242), \
         patch.object(proxy_module, "_process_info", return_value={"command_line": "py multiproxy.py", "started_at": datetime.datetime.now()}), \
         patch.object(proxy_module, "_health_check_says_multiproxy", return_value=None):
        _, _, is_multiproxy = proxy_module._identify_port_holder(8000)
    assert is_multiproxy is False


def test_identify_port_holder_handles_nobody_listening(proxy_module):
    with patch.object(proxy_module, "_port_listener_pid", return_value=None):
        pid, info, is_multiproxy = proxy_module._identify_port_holder(8000)
    assert (pid, info, is_multiproxy) == (None, None, False)


def test_identify_port_holder_handles_pid_found_but_info_unavailable(proxy_module):
    with patch.object(proxy_module, "_port_listener_pid", return_value=4242), \
         patch.object(proxy_module, "_process_info", return_value=None):
        pid, info, is_multiproxy = proxy_module._identify_port_holder(8000)
    assert pid == 4242
    assert info is None
    assert is_multiproxy is False


# --- _friendly_time / _proxy_path_for_message ---------------------------------------------------

def test_friendly_time_strips_a_leading_zero():
    import multiproxy as proxy_module
    dt = datetime.datetime(2026, 9, 30, 14, 41)
    assert proxy_module._friendly_time(dt) == "2:41 PM"


def test_friendly_time_keeps_a_real_leading_digit():
    import multiproxy as proxy_module
    dt = datetime.datetime(2026, 9, 30, 22, 5)  # 10:05 PM
    assert proxy_module._friendly_time(dt) == "10:05 PM"


def test_proxy_path_prefers_the_health_response_over_the_raw_command_line(proxy_module):
    info = {"command_line": "py multiproxy.py"}  # no real folder in the command line at all
    with patch.object(proxy_module, "_health_check_says_multiproxy", return_value={"proxy_path": "D:\\Real\\multiproxy.py"}):
        assert proxy_module._proxy_path_for_message(info) == "D:\\Real\\multiproxy.py"


def test_proxy_path_falls_back_to_the_command_line_when_health_has_no_path(proxy_module):
    info = {"command_line": "py multiproxy.py"}
    with patch.object(proxy_module, "_health_check_says_multiproxy", return_value=None):
        assert proxy_module._proxy_path_for_message(info) == "py multiproxy.py"


# --- _ask_yes_no ---------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("", True), ("y", True), ("Y", True), ("yes", True), ("n", False), ("no", False), ("anything else", False)])
def test_ask_yes_no(proxy_module, raw, expected):
    with patch("builtins.input", return_value=raw):
        assert proxy_module._ask_yes_no("? ") is expected


def test_ask_yes_no_false_on_eof_never_assumes_yes(proxy_module):
    with patch("builtins.input", side_effect=EOFError()):
        assert proxy_module._ask_yes_no("? ") is False


# --- _stop_process_tree_by_pid / _wait_for_port_free --------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="taskkill is Windows-only; the Linux version is tested below")
def test_stop_process_tree_by_pid_calls_real_taskkill_recursive_force(proxy_module):
    with patch.object(proxy_module.subprocess, "run") as mock_run:
        proxy_module._stop_process_tree_by_pid(4242)
    args = mock_run.call_args[0][0]
    assert args == ["taskkill", "/T", "/F", "/PID", "4242"]


def test_stop_process_tree_by_pid_never_raises_if_taskkill_itself_fails(proxy_module):
    with patch.object(proxy_module.subprocess, "run", side_effect=OSError("no taskkill")):
        proxy_module._stop_process_tree_by_pid(4242)  # must not raise


def test_wait_for_port_free_returns_true_once_the_pid_check_says_free(proxy_module):
    with patch.object(proxy_module, "_port_listener_pid", return_value=None):
        assert proxy_module._wait_for_port_free(8000, timeout=1) is True


def test_wait_for_port_free_times_out_if_still_busy(proxy_module):
    with patch.object(proxy_module, "_port_listener_pid", return_value=4242), \
         patch.object(proxy_module.time, "sleep"):
        assert proxy_module._wait_for_port_free(8000, timeout=0.01) is False


# --- _resolve_port_conflict: the full decision tree ----------------------------------------------

def _patch_holder(proxy_module, *, pid=4242, cmdline="py multiproxy.py", is_multiproxy=True, started=None):
    info = None if pid is None else {
        "command_line": cmdline,
        "started_at": started or datetime.datetime(2026, 9, 30, 14, 41),
    }
    return patch.object(proxy_module, "_identify_port_holder", return_value=(pid, info, is_multiproxy))


def test_fake_multiproxy_answer_yes_stops_it_and_returns_normally(proxy_module):
    with _patch_holder(proxy_module), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="y") as mock_input, \
         patch.object(proxy_module, "_stop_process_tree_by_pid") as mock_stop, \
         patch.object(proxy_module, "_wait_for_port_free", return_value=True), \
         patch.object(proxy_module.os, "_exit") as mock_exit:
        proxy_module._resolve_port_conflict(8000)
    assert "Another MultiProxy is already running" in mock_input.call_args[0][0]
    assert "started 2:41 PM" in mock_input.call_args[0][0]
    mock_stop.assert_called_once_with(4242)
    mock_exit.assert_not_called()


def test_fake_multiproxy_answer_no_exits_with_the_original_message(proxy_module):
    with _patch_holder(proxy_module), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n"), \
         patch.object(proxy_module, "_stop_process_tree_by_pid") as mock_stop, \
         patch.object(proxy_module.os, "_exit") as mock_exit, \
         patch.object(proxy_module.logger, "critical") as mock_log:
        proxy_module._resolve_port_conflict(8000)
    mock_stop.assert_not_called()
    mock_exit.assert_called_once_with(1)
    assert "already in use" in mock_log.call_args_list[0].args[0]


def test_policy_always_stops_it_without_asking(proxy_module):
    with _patch_holder(proxy_module), \
         patch.object(proxy_module, "REPLACE_RUNNING_PROXY_POLICY", "always"), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input") as mock_input, \
         patch.object(proxy_module, "_stop_process_tree_by_pid") as mock_stop, \
         patch.object(proxy_module, "_wait_for_port_free", return_value=True), \
         patch.object(proxy_module.os, "_exit") as mock_exit:
        proxy_module._resolve_port_conflict(8000)
    mock_input.assert_not_called()
    mock_stop.assert_called_once_with(4242)
    mock_exit.assert_not_called()


def test_policy_never_exits_without_asking_or_stopping(proxy_module):
    with _patch_holder(proxy_module), \
         patch.object(proxy_module, "REPLACE_RUNNING_PROXY_POLICY", "never"), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input") as mock_input, \
         patch.object(proxy_module, "_stop_process_tree_by_pid") as mock_stop, \
         patch.object(proxy_module.os, "_exit") as mock_exit:
        proxy_module._resolve_port_conflict(8000)
    mock_input.assert_not_called()
    mock_stop.assert_not_called()
    mock_exit.assert_called_once_with(1)


def test_unrelated_program_holding_the_port_is_never_stopped(proxy_module):
    """Real, unrelated program -- shown who it is, but never touched, regardless of policy."""
    with _patch_holder(proxy_module, cmdline="some_other_app.exe", is_multiproxy=False), \
         patch.object(proxy_module, "REPLACE_RUNNING_PROXY_POLICY", "always"), \
         patch("sys.stdin.isatty", return_value=True), \
         patch.object(proxy_module, "_stop_process_tree_by_pid") as mock_stop, \
         patch.object(proxy_module.os, "_exit") as mock_exit, \
         patch.object(proxy_module.logger, "critical") as mock_log:
        proxy_module._resolve_port_conflict(8000)
    mock_stop.assert_not_called()
    mock_exit.assert_called_once_with(1)
    assert any("process ID 4242" in c.args[0] and "some_other_app.exe" in c.args[0] for c in mock_log.call_args_list)


def test_non_console_start_never_asks_and_keeps_todays_behavior(proxy_module):
    """The SKSE plugin's own launch (or anything else with no attached console) -- even a real
    MultiProxy holding the port is left alone; there's no one here to answer, and the original
    message/exit is exactly what happened before this feature existed."""
    with _patch_holder(proxy_module), \
         patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.input") as mock_input, \
         patch.object(proxy_module, "_stop_process_tree_by_pid") as mock_stop, \
         patch.object(proxy_module.os, "_exit") as mock_exit:
        proxy_module._resolve_port_conflict(8000)
    mock_input.assert_not_called()
    mock_stop.assert_not_called()
    mock_exit.assert_called_once_with(1)


def test_nobody_identifiable_falls_back_to_the_generic_message(proxy_module):
    with _patch_holder(proxy_module, pid=None), \
         patch.object(proxy_module.os, "_exit") as mock_exit, \
         patch.object(proxy_module.logger, "critical") as mock_log:
        proxy_module._resolve_port_conflict(8000)
    mock_exit.assert_called_once_with(1)
    assert "already in use" in mock_log.call_args_list[0].args[0]


def test_stopped_but_port_still_busy_afterward_exits_with_its_own_message(proxy_module):
    with _patch_holder(proxy_module), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="y"), \
         patch.object(proxy_module, "_stop_process_tree_by_pid"), \
         patch.object(proxy_module, "_wait_for_port_free", return_value=False), \
         patch.object(proxy_module.os, "_exit") as mock_exit, \
         patch.object(proxy_module.logger, "critical") as mock_log:
        proxy_module._resolve_port_conflict(8000)
    mock_exit.assert_called_once_with(1)
    assert "still in use" in mock_log.call_args_list[-1].args[0]


# --- _exit_if_port_in_use / _preflight_main_port / start_interceptor's retry --------------------

def test_exit_if_port_in_use_reraises_non_eaddrinuse_errors(proxy_module):
    other = OSError("permission denied")
    other.errno = 13
    with pytest.raises(OSError):
        proxy_module._exit_if_port_in_use(other, 9999)


def test_exit_if_port_in_use_resolves_and_returns_true_on_eaddrinuse(proxy_module):
    import errno as errno_module
    e = OSError("in use")
    e.errno = errno_module.EADDRINUSE
    with patch.object(proxy_module, "_resolve_port_conflict") as mock_resolve:
        assert proxy_module._exit_if_port_in_use(e, 9999) is True
    mock_resolve.assert_called_once_with(9999)


def test_preflight_main_port_does_nothing_when_free(proxy_module):
    with patch.object(proxy_module, "_port_listener_pid", return_value=None), \
         patch.object(proxy_module, "_resolve_port_conflict") as mock_resolve:
        proxy_module._preflight_main_port()
    mock_resolve.assert_not_called()


def test_preflight_main_port_resolves_when_busy(proxy_module):
    """One port busy only (item 6 in the build task): MAIN_PORT busy, INTERCEPTOR_PORT untouched
    by this call at all -- confirms each port's own check is independent."""
    with patch.object(proxy_module, "_port_listener_pid", return_value=4242), \
         patch.object(proxy_module, "_resolve_port_conflict") as mock_resolve:
        proxy_module._preflight_main_port()
    mock_resolve.assert_called_once_with(proxy_module.MAIN_PORT)


@pytest.mark.asyncio
async def test_start_interceptor_retries_once_after_a_resolved_conflict(proxy_module):
    """The interceptor's own port (9999) busy only -- resolved, then retried exactly once, without
    ever touching MAIN_PORT."""
    import errno as errno_module

    # A SHARED counter across every TCPSite instance created -- the real code creates a genuinely
    # NEW TCPSite object for the retry (site.start() failing once doesn't make that object usable
    # for a second attempt), so the fake's "fail once, then succeed" behavior has to live outside
    # any single instance, or the retry's own fresh instance would just fail on ITS first call too.
    calls = {"n": 0}

    class _FakeSite:
        def __init__(self, *a, **kw):
            pass

        async def start(self):
            calls["n"] += 1
            if calls["n"] == 1:
                e = OSError("in use")
                e.errno = errno_module.EADDRINUSE
                raise e

    sites = []

    def _make_site(*a, **kw):
        s = _FakeSite()
        sites.append(s)
        return s

    fake_runner = MagicMock()

    async def _fake_setup():
        return None
    fake_runner.setup = _fake_setup

    with patch.object(proxy_module.web, "AppRunner", return_value=fake_runner), \
         patch.object(proxy_module.web, "TCPSite", side_effect=_make_site), \
         patch.object(proxy_module, "_resolve_port_conflict") as mock_resolve:
        await proxy_module.start_interceptor()

    mock_resolve.assert_called_once_with(proxy_module.INTERCEPTOR_PORT)
    # One fresh TCPSite is created for the retry (mirrors the real site.start() contract: a TCPSite
    # that already failed to bind once is not reused for a second attempt), and start() was
    # actually called twice in total -- the original failing attempt, then the one real retry.
    assert len(sites) == 2
    assert calls["n"] == 2
