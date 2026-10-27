"""Tests for EnableSpeedTracking (2026-09-20) -- a real on/off switch for speed tracking and the
Speeds tab. Deliberately defaults to True (fallback=True), the one flag on this page that isn't
off by default -- see multiproxy.py's own comment on ENABLE_SPEED_TRACKING for why (the feature was
already shipped and on for everyone before this flag existed; defaulting it False would be a
silent regression, not a neutral opt-in).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_response(status=200, text=""):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    if status == 200:
        import json as _json
        resp.json = AsyncMock(return_value=_json.loads(text))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


OK_CHAT_BODY = (
    '{"choices":[{"message":{"content":"hi there"},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":5,"completion_tokens":3}}'
)


class TestRecordingIsSkippedWhenDisabled:
    def test_non_streaming_call_does_not_touch_the_in_memory_tracker_or_history_db(self, proxy_module, test_client):
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False), \
             patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200
        assert proxy_module.speed_tracker.snapshot() == {}
        assert proxy_module._query_speed_history_rows("anthropic/claude-haiku-4.5", None) == []

    def test_streaming_call_does_not_touch_the_in_memory_tracker_or_history_db(self, proxy_module, test_client):
        sse = b'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n\ndata: [DONE]\n\n'
        mock_content = MagicMock()

        async def _iter():
            yield sse

        mock_content.iter_any.return_value = _iter()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.content = mock_content
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        session = _make_mock_session(mock_resp)

        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False), \
             patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/streamed-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
        assert r.status_code == 200
        assert proxy_module.speed_tracker.snapshot() == {}
        assert proxy_module._query_speed_history_rows("some/streamed-model", None) == []

    def test_failed_call_is_also_not_recorded_when_disabled(self, proxy_module, test_client):
        resp = _make_mock_response(500, "upstream exploded")
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False), \
             patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/model",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 500
        assert proxy_module.speed_tracker.snapshot() == {}

    def test_timed_stream_returns_the_source_completely_unwrapped_when_disabled(self, proxy_module):
        """_timed_stream must not even wrap the generator when tracking is off -- confirms it
        returns the exact same object, not a new async generator around it."""
        async def _source():
            yield "chunk"

        gen = _source()
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            result = proxy_module._timed_stream(gen, "some-model", 0.0)
        assert result is gen

    def test_timed_stream_wraps_when_enabled(self, proxy_module):
        async def _source():
            yield "chunk"

        gen = _source()
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", True):
            result = proxy_module._timed_stream(gen, "some-model", 0.0)
        assert result is not gen


class TestStatsSpeedsEndpointWhenDisabled:
    def test_returns_404_when_disabled(self, proxy_module, test_client):
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            r = test_client.get("/stats/speeds")
        assert r.status_code == 404

    def test_returns_200_when_enabled(self, proxy_module, test_client):
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", True):
            r = test_client.get("/stats/speeds")
        assert r.status_code == 200


class TestDashboardTabVisibility:
    def test_no_speeds_tab_or_pane_when_disabled(self, proxy_module, test_client):
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            html = test_client.get("/").text
        assert 'id="tabSpeeds"' not in html
        # Since Other devices (2026-09-30) the tab bar is always there: Setup + Other devices, just no Speeds.
        assert 'id="tabSetup"' in html and 'id="tabOther"' in html
        assert 'id="paneSpeeds"' not in html
        # The dashboard must still look like a normal dashboard otherwise.
        assert 'id="paneSetup"' in html
        assert "Quick Test" in html
        assert "Choose a provider" in html

    def test_speeds_tab_and_pane_present_when_enabled(self, proxy_module, test_client):
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", True):
            html = test_client.get("/").text
        assert 'id="tabSpeeds"' in html
        assert 'id="tabSetup"' in html
        assert 'id="paneSpeeds"' in html
        assert 'id="rangePicker"' in html
