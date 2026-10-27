"""Tests for OpenBrowserOnStart (2026-09-20) -- multiproxy.ini flag that opens the dashboard in the
default browser when the proxy starts, now that there's a real Speeds tab worth landing on.

OPEN_BROWSER_ON_START is a module-level constant read once from multiproxy.ini at import time, same
pattern AUTO_CLOSE_WITH_SKYRIM already uses -- so these tests toggle it via monkeypatch rather than
touching the real multiproxy.ini, and always run the real `lifespan()` context manager directly (same
approach test_claude_availability.py's test_lifespan_survives_failing_auth_capture already uses)
with a mocked webbrowser.open so a real browser window can never actually pop during `pytest`.
"""
from unittest.mock import AsyncMock, patch

import pytest


class _FakeRunner:
    async def cleanup(self):
        pass


async def _fake_start_interceptor():
    return _FakeRunner()


@pytest.mark.asyncio
async def test_opens_the_dashboard_when_flag_is_on(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "start_interceptor", _fake_start_interceptor)
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_module, "OPEN_BROWSER_ON_START", True)

    with patch("webbrowser.open") as mock_open:
        async with proxy_module.lifespan(proxy_module.app):
            pass

    mock_open.assert_called_once_with("http://127.0.0.1:8000")


@pytest.mark.asyncio
async def test_does_not_open_a_browser_when_flag_is_off(proxy_module, monkeypatch):
    """The default -- also the implicit case for every OTHER test in this whole suite that runs
    `lifespan()` or the app's startup, which is exactly why this must never fire unless asked."""
    monkeypatch.setattr(proxy_module, "start_interceptor", _fake_start_interceptor)
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_module, "OPEN_BROWSER_ON_START", False)

    with patch("webbrowser.open") as mock_open:
        async with proxy_module.lifespan(proxy_module.app):
            pass

    mock_open.assert_not_called()
