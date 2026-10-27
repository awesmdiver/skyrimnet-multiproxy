"""Tests for the uncaught-exception logging hooks (2026-09-20).

Real, confirmed-live incident: the SKSE plugin launches this proxy in its own console window
(CreateProcess CREATE_NEW_CONSOLE) rather than a hidden one -- but that console closes the instant
the process exits, whether from a clean exit or a crash. A genuine uncaught exception's traceback
prints straight to that vanishing console, leaving multiproxy.log with no trace anything went wrong.
sys.excepthook / threading.excepthook route it through `logger` first so it survives in multiproxy.log.
"""
import logging
import threading


def test_sys_excepthook_logs_the_exception_before_delegating(proxy_module, caplog):
    try:
        raise ValueError("boom")
    except ValueError:
        exc_type, exc_value, exc_tb = __import__("sys").exc_info()

    with caplog.at_level(logging.CRITICAL, logger="proxy"):
        proxy_module._log_uncaught_exception(exc_type, exc_value, exc_tb)

    records = [r for r in caplog.records if r.name == "proxy"]
    assert len(records) == 1
    assert "UNCAUGHT EXCEPTION" in records[0].message
    assert records[0].exc_info == (exc_type, exc_value, exc_tb)


def test_threading_excepthook_logs_the_thread_name_and_exception(proxy_module, caplog):
    try:
        raise RuntimeError("thread boom")
    except RuntimeError:
        exc_type, exc_value, exc_tb = __import__("sys").exc_info()

    fake_args = threading.ExceptHookArgs((exc_type, exc_value, exc_tb, threading.current_thread()))

    with caplog.at_level(logging.CRITICAL, logger="proxy"):
        proxy_module._log_uncaught_thread_exception(fake_args)

    records = [r for r in caplog.records if r.name == "proxy"]
    assert len(records) == 1
    assert "UNCAUGHT EXCEPTION" in records[0].message
    assert threading.current_thread().name in records[0].message
    assert records[0].exc_info == (exc_type, exc_value, exc_tb)


# Neither hook's global installation (sys.excepthook / threading.excepthook) is asserted by
# identity here -- other tests in this suite legitimately re-import/reload multiproxy.py (e.g. for a
# real "no CLI found at all" fresh-import case), which re-runs the module-level assignment and
# leaves the *previous* reload's function object referenced by the hook that was live at THAT
# time -- an order-dependent artifact of module reloading, not a real bug. The behavior that
# actually matters (what each hook function does when called) is covered above.
