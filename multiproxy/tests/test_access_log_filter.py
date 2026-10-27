"""Tests for suppressing successful (200) uvicorn access-log lines (2026-09-20), reported live:
the dashboard alone polls /stats/speeds every few seconds, drowning routine 200s out over anything
actually worth seeing. Errors/redirects/etc. must still print.

Covers both requirements from the queue item:
1. A direct unit test of _SuppressSuccessfulAccessLogs.filter() against fake LogRecords.
2. An end-to-end test that runs the REAL logging.config.dictConfig(proxy._UVICORN_LOG_CFG) --
   exactly what uvicorn.run() does internally -- and confirms the filter (declared inside the
   dict, not added imperatively) actually survives and works. This is the test that would catch
   the "dictConfig resets filters" trap if the fix didn't actually survive it.
"""
import copy
import logging
import logging.config

import pytest


def _access_record(status_code, args_override=None):
    """Mirrors uvicorn's own real access_logger.info(...) call shape exactly (see
    uvicorn/protocols/http/h11_impl.py and httptools_impl.py): '%s - "%s %s HTTP/%s" %d' with
    (client_addr, method, path, http_version, status)."""
    args = args_override if args_override is not None else ("127.0.0.1", "GET", "/stats/speeds", "1.1", status_code)
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname="", lineno=0,
        msg='%s - "%s %s HTTP/%s" %d', args=args, exc_info=None,
    )


class TestSuppressSuccessfulAccessLogsUnit:
    def test_200_is_suppressed(self, proxy_module):
        f = proxy_module._SuppressSuccessfulAccessLogs()
        assert f.filter(_access_record(200)) is False

    @pytest.mark.parametrize("status", [404, 500, 301, 204, 101])
    def test_non_200_passes_through(self, proxy_module, status):
        f = proxy_module._SuppressSuccessfulAccessLogs()
        assert f.filter(_access_record(status)) is True

    def test_empty_args_fails_safe(self, proxy_module):
        f = proxy_module._SuppressSuccessfulAccessLogs()
        assert f.filter(_access_record(None, args_override=())) is True

    def test_none_args_fails_safe(self, proxy_module):
        f = proxy_module._SuppressSuccessfulAccessLogs()
        record = _access_record(None, args_override=None)
        assert f.filter(record) is True

    def test_non_access_shaped_args_fails_safe(self, proxy_module):
        """A record whose last arg isn't a real status code (e.g. a plain %s-style log with
        unrelated args) must still pass through rather than being silently eaten."""
        f = proxy_module._SuppressSuccessfulAccessLogs()
        record = logging.LogRecord(
            name="uvicorn.access", level=logging.INFO, pathname="", lineno=0,
            msg="%s", args=("some unrelated message",), exc_info=None,
        )
        assert f.filter(record) is True


class TestFilterSurvivesRealDictConfig:
    """The trap check: uvicorn.run() calls logging.config.dictConfig(log_config) internally at
    startup. If the filter were added imperatively (logger.addFilter(...)) instead of declared
    inside the dict, dictConfig reconfiguring "uvicorn.access" could silently drop it. This calls
    the REAL dictConfig with the REAL proxy._UVICORN_LOG_CFG object and proves the filter is still
    there and actually filtering -- not just present in the dict, but functioning after the exact
    call uvicorn itself makes."""

    @pytest.fixture(autouse=True)
    def _restore_logging_state(self):
        """dictConfig mutates GLOBAL logging state (handlers/filters/level/propagate on named
        loggers) -- snapshot and restore the three loggers multiproxy.py's own config touches so this
        test doesn't leak state into whatever runs after it in the same pytest session."""
        names = ("uvicorn", "uvicorn.access", "uvicorn.error", "root")
        snapshot = {}
        for name in names:
            lg = logging.getLogger(name) if name != "root" else logging.getLogger()
            snapshot[name] = (list(lg.handlers), list(lg.filters), lg.level, lg.propagate)
        yield
        for name in names:
            lg = logging.getLogger(name) if name != "root" else logging.getLogger()
            handlers, filters, level, propagate = snapshot[name]
            lg.handlers = handlers
            lg.filters = filters
            lg.level = level
            lg.propagate = propagate

    def test_real_dictconfig_suppresses_200_but_not_404(self, proxy_module, tmp_path):
        cfg = copy.deepcopy(proxy_module._UVICORN_LOG_CFG)
        # Never let this test touch the real multiproxy.log, even on a machine where EnableLogging=true.
        if "file" in cfg.get("handlers", {}):
            cfg["handlers"]["file"]["filename"] = str(tmp_path / "test-proxy.log")

        logging.config.dictConfig(cfg)
        access_logger = logging.getLogger("uvicorn.access")

        captured = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record)

        access_logger.addHandler(_Capture())

        # The exact call shape uvicorn's own h11_impl.py/httptools_impl.py make.
        access_logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", "/stats/speeds", "1.1", 200)
        access_logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1", "GET", "/nope", "1.1", 404)

        statuses = [r.args[-1] for r in captured]
        assert 200 not in statuses
        assert 404 in statuses
