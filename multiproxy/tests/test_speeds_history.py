"""Tests for persisted speed history (2026-09-20) -- the Speeds tab's time-range picker
(Today / 7 days / 30 days / All time), backed by speed_history.db so the longer ranges survive a
proxy restart while the in-memory _SpeedTracker (still exactly right for "Today") is untouched.

Isolation: tests/conftest.py's autouse _isolate_speed_history_db fixture redirects
proxy.SPEED_HISTORY_DB to a throwaway file per test -- see that fixture's own docstring for the
real, confirmed-live incident (12KB written into the real file in one suite run) that made this
autouse rather than opt-in.
"""
import sqlite3
import time
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


def _seed(db_path: str, model: str, ts: float, elapsed: float, success: bool = True, provider=None) -> None:
    """Direct-SQL test seeding -- bypasses _insert_speed_history_row (which always stamps
    time.time() itself) so tests can place rows at exact, controlled points in the past."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO speed_history (ts, model, elapsed, success, provider) VALUES (?, ?, ?, ?, ?)",
            (ts, model, elapsed, 1 if success else 0, provider),
        )
        conn.commit()


class TestPersistenceOnRealDispatch:
    """Proves a completed request writes a row to speed_history.db, not just the in-memory
    tracker -- both the non-streaming and streaming paths, mirroring
    test_speeds_tab.py::TestSpeedTrackerViaRealDispatch's own shape for the in-memory tracker."""

    def test_non_streaming_call_writes_a_persistent_row(self, proxy_module, test_client):
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200

        rows = proxy_module._query_speed_history_rows("anthropic/claude-haiku-4.5", None)
        assert len(rows) == 1
        ts, elapsed, success = rows[0]
        assert success == 1
        assert elapsed >= 0
        assert abs(ts - time.time()) < 5  # sanity: really "just now", not a stale/garbage timestamp

    def test_streaming_call_writes_a_persistent_row(self, proxy_module, test_client):
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

        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/streamed-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
        assert r.status_code == 200

        rows = proxy_module._query_speed_history_rows("some/streamed-model", None)
        assert len(rows) == 1
        assert rows[0][2] == 1  # success

    def test_failed_call_is_persisted_as_a_failure(self, proxy_module, test_client):
        resp = _make_mock_response(500, "upstream exploded")
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/model",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 500
        rows = proxy_module._query_speed_history_rows("some/model", None)
        assert len(rows) == 1
        assert rows[0][2] == 0  # failure


class TestWriteFailureIsNonFatal:
    def test_forced_write_failure_does_not_break_the_chat_response(self, proxy_module, test_client):
        """A locked/unwritable db file must never break the actual chat reply this timing came
        from -- _record_speed catches and logs, never raises."""
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session), \
             patch.object(proxy_module, "_insert_speed_history_row", side_effect=OSError("disk full")):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "hi there"
        # The in-memory tracker (independent of the failed persistent write) still got it.
        snap = proxy_module.speed_tracker.snapshot()
        assert "Kilo Gateway::anthropic/claude-haiku-4.5" in snap
        assert snap["Kilo Gateway::anthropic/claude-haiku-4.5"]["success_pct"] == 100


class TestRangeQueryEndpoint:
    def test_default_range_and_explicit_today_are_byte_for_byte_identical(self, proxy_module, test_client):
        """The core compatibility requirement: this feature must not change range=today (or no
        range at all) one bit -- both must still come straight from the in-memory tracker."""
        proxy_module.speed_tracker.record("claude-sonnet-5", 1.2, True)
        r1 = test_client.get("/stats/speeds")
        r2 = test_client.get("/stats/speeds?range=today")
        assert r1.json() == r2.json()
        assert "calls_last_hour" in r1.json()["models"][0]
        assert "bucket_unit" not in r1.json()["models"][0]  # today never gets the new field

    def test_invalid_range_is_rejected(self, proxy_module, test_client):
        r = test_client.get("/stats/speeds?range=bogus")
        assert r.status_code == 400

    def test_7d_excludes_rows_outside_the_window_and_aggregates_the_rest(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        _seed(db, "m1", now - 86400 * 1, 1.0, True)
        _seed(db, "m1", now - 86400 * 2, 2.0, True)
        _seed(db, "m1", now - 86400 * 3, 3.0, False)
        _seed(db, "m1", now - 86400 * 10, 99.0, True)  # outside the 7-day window

        r = test_client.get("/stats/speeds?range=7d")
        assert r.status_code == 200
        m = next(x for x in r.json()["models"] if x["model"] == "m1")
        assert m["calls_total"] == 3
        assert m["success_pct"] == round(100 * 2 / 3)
        assert m["avg"] == pytest.approx((1.0 + 2.0 + 3.0) / 3)
        assert m["last_reply"] == 1.0  # the most recent row within the window
        assert m["bucket_unit"] == "day"
        assert 99.0 not in m["recent"]

    def test_30d_includes_rows_7d_would_exclude(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        _seed(db, "m2", now - 86400 * 1, 1.0, True)
        _seed(db, "m2", now - 86400 * 20, 5.0, True)

        r7 = test_client.get("/stats/speeds?range=7d")
        r30 = test_client.get("/stats/speeds?range=30d")
        m7 = next((x for x in r7.json()["models"] if x["model"] == "m2"), None)
        m30 = next((x for x in r30.json()["models"] if x["model"] == "m2"), None)
        assert m7["calls_total"] == 1
        assert m30["calls_total"] == 2
        assert m30["bucket_unit"] == "day"  # 30 days never crosses the weekly-bucket cutoff

    def test_all_time_buckets_by_week_past_the_sixty_day_cutoff(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        _seed(db, "m3", now - 86400 * 90, 5.0, True)  # 90 days ago -- span exceeds the cutoff
        _seed(db, "m3", now - 86400 * 1, 2.0, True)

        r = test_client.get("/stats/speeds?range=all")
        m = next(x for x in r.json()["models"] if x["model"] == "m3")
        assert m["calls_total"] == 2
        assert m["bucket_unit"] == "week"

    def test_all_time_stays_daily_under_the_cutoff(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        _seed(db, "m4", now - 86400 * 5, 5.0, True)
        _seed(db, "m4", now - 86400 * 1, 2.0, True)

        r = test_client.get("/stats/speeds?range=all")
        m = next(x for x in r.json()["models"] if x["model"] == "m4")
        assert m["bucket_unit"] == "day"

    def test_model_with_no_rows_in_window_does_not_appear(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        _seed(db, "old-only-model", now - 86400 * 40, 1.0, True)  # outside 7d and 30d

        r7 = test_client.get("/stats/speeds?range=7d")
        r30 = test_client.get("/stats/speeds?range=30d")
        rall = test_client.get("/stats/speeds?range=all")
        assert not any(x["model"] == "old-only-model" for x in r7.json()["models"])
        assert not any(x["model"] == "old-only-model" for x in r30.json()["models"])
        assert any(x["model"] == "old-only-model" for x in rall.json()["models"])

    def test_role_chips_still_merge_in_for_historical_ranges(self, proxy_module, test_client, tmp_path):
        config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "OpenRouter.yaml").write_text(
            "openrouter:\n  model_name: deepseek/deepseek-v3.2\n  variants: {}\n",
            encoding="utf-8",
        )
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "deepseek-v3.2", time.time() - 3600, 1.0, True, provider="DeepSeek")

        with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
            r = test_client.get("/stats/speeds?range=7d")
        m = next(x for x in r.json()["models"] if x["model"] == "deepseek-v3.2")
        assert m["roles"] == [{"label": "Dialogue", "class": "dialogue", "category": "dialogue"}]

    def test_empty_history_returns_no_models_for_historical_ranges(self, proxy_module, test_client):
        for rng in ("7d", "30d", "all"):
            r = test_client.get(f"/stats/speeds?range={rng}")
            assert r.status_code == 200
            assert r.json()["models"] == []
