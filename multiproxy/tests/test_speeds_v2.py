"""Tests for Speeds v2 (2026-09-27): health at a glance, "starts talking" time, reliability labels.

Covers: the new speed_history.db columns (migration of an OLD-format file included), what gets recorded
per call, the speed estimate vs a real token count, reliability thresholds, the failure-reason mapping,
the health-card verdict rules, the By-role ranking, the thresholds endpoint, and the page's
Dialogue-first ordering and remembered choices (the page's own script run under Node).
"""
import json
import os
import shutil
import sqlite3
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi import HTTPException

NODE = shutil.which("node")


def _seed(db, model, ts, elapsed, success=True, *, roles=None, first_token=None, out_tokens=None,
          out_chars=None, fail_reason=None, provider=None):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO speed_history (ts, model, elapsed, success, roles_json, first_token, out_tokens, out_chars, "
            "fail_reason, provider) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, model, elapsed, 1 if success else 0, json.dumps(roles) if roles else None, first_token,
             out_tokens, out_chars, fail_reason, provider),
        )
        conn.commit()


def _rows(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM speed_history ORDER BY ts")]


# ---------------- the database ----------------

class TestMigration:
    def test_an_old_format_file_gains_the_new_columns_and_keeps_its_rows(self, proxy_module, tmp_path):
        old = tmp_path / "old_speed_history.db"
        with sqlite3.connect(old) as conn:   # exactly what the very first release created
            conn.execute("CREATE TABLE speed_history (ts REAL NOT NULL, model TEXT NOT NULL, elapsed REAL NOT NULL, success INTEGER NOT NULL)")
            conn.execute("INSERT INTO speed_history VALUES (1000.0, 'old-model', 2.5, 1)")
            conn.commit()
        with patch.object(proxy_module, "SPEED_HISTORY_DB", str(old)):
            proxy_module._init_speed_history_db()
            proxy_module._init_speed_history_db()   # running it again must be harmless
        cols = {c[1] for c in sqlite3.connect(old).execute("PRAGMA table_info(speed_history)")}
        assert {"roles_json", "first_token", "out_tokens", "out_chars", "fail_reason", "provider"} <= cols
        row = _rows(str(old))[0]
        assert (row["model"], row["elapsed"], row["success"]) == ("old-model", 2.5, 1)
        assert all(row[c] is None for c in ("first_token", "out_tokens", "out_chars", "fail_reason", "provider", "roles_json"))

    def test_a_file_that_already_has_roles_json_is_upgraded_too(self, proxy_module, tmp_path):
        old = tmp_path / "mid.db"
        with sqlite3.connect(old) as conn:
            conn.execute("CREATE TABLE speed_history (ts REAL NOT NULL, model TEXT NOT NULL, elapsed REAL NOT NULL, success INTEGER NOT NULL, roles_json TEXT)")
            conn.execute("INSERT INTO speed_history VALUES (1000.0, 'm', 1.0, 1, '[\"Dialogue\"]')")
            conn.commit()
        with patch.object(proxy_module, "SPEED_HISTORY_DB", str(old)):
            proxy_module._init_speed_history_db()
        assert _rows(str(old))[0]["roles_json"] == '["Dialogue"]'

    def test_old_rows_read_as_not_measured_never_as_zero(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "old", time.time(), 3.0)   # a row with none of the new numbers
        stats = proxy_module._v2_model_stats(proxy_module._query_v2_rows(None)[(None, "old")])
        assert stats["first_token"] is None and stats["speed"] is None and stats["provider"] is None
        assert stats["failure_line"] is None
        assert stats["full"]["typical"] == 3.0


# ---------------- what gets recorded ----------------

class TestRecording:
    @pytest.mark.asyncio
    async def test_record_speed_saves_the_new_columns(self, proxy_module):
        await proxy_module._record_speed("m", 2.0, True, first_token=0.4, out_tokens=120, out_chars=500, provider="GLM")
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert (row["first_token"], row["out_tokens"], row["out_chars"], row["provider"], row["fail_reason"]) == (0.4, 120, 500, "GLM", None)

    @pytest.mark.asyncio
    async def test_a_reason_is_only_saved_for_a_failure(self, proxy_module):
        await proxy_module._record_speed("m", 1.0, True, fail_reason="busy")
        await proxy_module._record_speed("m", 1.0, False, fail_reason="busy")
        await proxy_module._record_speed("m", 1.0, False)   # an old-style failure with no reason
        assert [r["fail_reason"] for r in _rows(proxy_module.SPEED_HISTORY_DB)] == [None, "busy", None]

    def test_a_streamed_reply_records_start_time_length_and_service(self, proxy_module, test_client, monkeypatch):
        async def streaming(system_prompt, messages, model, max_tokens, **extra):
            import asyncio
            await asyncio.sleep(0.12)   # the wait before the first word
            yield 'data: {"choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}\n\n'
            await asyncio.sleep(0.05)
            yield 'data: {"choices":[{"index":0,"delta":{"content":"there."},"finish_reason":null}],"usage":{"completion_tokens":7}}\n\n'
            yield "data: [DONE]\n\n"

        monkeypatch.setattr(proxy_module, "call_glm_streaming", streaming)
        r = test_client.post("/v1/chat/completions", json={
            "model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert row["provider"] == "GLM" and row["success"] == 1
        assert row["out_chars"] == len("Hello there.") and row["out_tokens"] == 7
        assert 0.10 <= row["first_token"] < row["elapsed"]

    def test_a_non_streamed_reply_has_no_start_time_but_records_its_size(self, proxy_module, test_client, monkeypatch):
        async def direct(system_prompt, messages, model, max_tokens, **extra):
            return "A whole reply.", {"prompt_tokens": 3, "completion_tokens": 4, "finish_reason": "stop"}

        monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
        test_client.post("/v1/chat/completions", json={
            "model": proxy_module.GLM_PREFIX + "glm-4.6", "messages": [{"role": "user", "content": "hi"}]})
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert row["first_token"] is None and row["out_chars"] == len("A whole reply.") and row["out_tokens"] == 4

    def test_codex_never_gets_a_start_time(self, proxy_module, test_client):
        async def one_chunk(*a, **k):
            yield 'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"all at once"},"finish_reason":null}]}\n\n'
            yield "data: [DONE]\n\n"

        with patch.object(proxy_module, "call_codex_streaming", one_chunk):
            test_client.post("/v1/chat/completions", json={
                "model": "codex/gpt-5.5", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert row["provider"] == "Codex" and row["first_token"] is None and row["out_chars"] == len("all at once")

    def test_an_upstream_429_is_saved_as_busy(self, proxy_module, test_client):
        resp = AsyncMock()
        resp.status = 429
        resp.text = AsyncMock(return_value='{"error":{"message":"slow down"}}')
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock(post=MagicMock(return_value=resp), close=AsyncMock())
        with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 429
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert (row["success"], row["fail_reason"], row["provider"]) == (0, "busy", "GLM")

    def test_a_full_request_line_is_saved_as_busy(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "glm": 1})
        monkeypatch.setattr(proxy_module, "QUEUE_TIMEOUT_SECONDS", 0.05)
        line = proxy_module._get_request_line("glm", 1)
        line.active = 1   # someone else holds the only spot
        r = test_client.post("/v1/chat/completions", json={
            "model": proxy_module.GLM_PREFIX + "glm-4.6", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 429
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert (row["success"], row["fail_reason"], row["provider"]) == (0, "busy", "GLM")

    def test_a_stream_that_fails_partway_is_a_failure(self, proxy_module, test_client, monkeypatch):
        async def half(system_prompt, messages, model, max_tokens, **extra):
            yield 'data: {"choices":[{"index":0,"delta":{"content":"Half"},"finish_reason":null}]}\n\n'
            raise HTTPException(status_code=504, detail="It didn't respond in time.")

        monkeypatch.setattr(proxy_module, "call_glm_streaming", half)
        r = test_client.post("/v1/chat/completions", json={
            "model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200 and r.text.rstrip().endswith("data: [DONE]")
        row = _rows(proxy_module.SPEED_HISTORY_DB)[0]
        assert (row["success"], row["fail_reason"]) == (0, "timeout")


class TestFailReason:
    @pytest.mark.parametrize("exc,expected", [
        (HTTPException(status_code=429, detail="x"), "busy"),
        (HTTPException(status_code=401, detail="x"), "auth"),
        (HTTPException(status_code=403, detail="x"), "auth"),
        (HTTPException(status_code=503, detail="Claude isn't available right now: login expired"), "auth"),
        (HTTPException(status_code=504, detail="Codex didn't respond in time."), "timeout"),
        (HTTPException(status_code=408, detail="x"), "timeout"),
        (HTTPException(status_code=500, detail="boom"), "server"),
        (HTTPException(status_code=502, detail="bad gateway"), "server"),
        (HTTPException(status_code=503, detail="overloaded"), "server"),
        (HTTPException(status_code=400, detail="bad request"), "other"),
        (HTTPException(status_code=404, detail="no such model"), "other"),
        (asyncio_timeout := TimeoutError(), "timeout"),
        (ValueError("weird"), "other"),
        (aiohttp.ClientConnectionError("x"), "other"),
    ])
    def test_mapping(self, proxy_module, exc, expected):
        assert proxy_module._fail_reason(exc) == expected


# ---------------- the numbers ----------------

def _row(elapsed=2.0, success=True, first_token=None, out_tokens=None, out_chars=None, fail_reason=None, provider=None, ts=None,
          reply_repair=None):
    return (ts if ts is not None else time.time(), elapsed, 1 if success else 0, first_token, out_tokens, out_chars, fail_reason,
             provider, reply_repair)


class TestSpeed:
    def test_a_real_token_count_is_used_and_not_marked_estimated(self, proxy_module):
        words, tokens, estimated = proxy_module._row_speed(_row(elapsed=5.0, first_token=1.0, out_tokens=200, out_chars=999))
        assert tokens == pytest.approx(50.0)           # 200 tokens over the 4 seconds it was talking
        assert words == pytest.approx(37.5)            # words are three quarters of tokens
        assert estimated is False

    def test_without_a_token_count_it_is_estimated_from_length_and_marked(self, proxy_module):
        words, tokens, estimated = proxy_module._row_speed(_row(elapsed=3.0, first_token=1.0, out_chars=400))
        assert tokens == pytest.approx(50.0)           # 400 characters ~ 100 tokens, over 2 seconds
        assert estimated is True

    @pytest.mark.parametrize("row", [
        _row(elapsed=4.0, out_tokens=100),                                   # not streamed: nothing to measure between
        _row(elapsed=4.0, success=False, first_token=1.0, out_tokens=100),   # failed
        _row(elapsed=1.1, first_token=1.0, out_tokens=100),                  # one burst: no speed to speak of
        _row(elapsed=4.0, first_token=1.0),                                  # nothing says how much it wrote
    ])
    def test_no_speed_when_it_cannot_be_told(self, proxy_module, row):
        assert proxy_module._row_speed(row) is None

    def test_the_card_speed_is_the_median_and_estimated_if_any_part_was(self, proxy_module):
        rows = [_row(elapsed=3.0, first_token=1.0, out_tokens=100), _row(elapsed=3.0, first_token=1.0, out_tokens=200),
                _row(elapsed=3.0, first_token=1.0, out_chars=1200)]
        speed = proxy_module._v2_model_stats(rows)["speed"]
        assert speed["tokens"] == pytest.approx(100.0)   # medians of 50, 100, 150
        assert speed["estimated"] is True
        real = proxy_module._v2_model_stats(rows[:2])["speed"]
        assert real["estimated"] is False


class TestReliability:
    @pytest.mark.parametrize("pct,level", [(100, "ok"), (98, "ok"), (97, "warn"), (90, "warn"), (89, "bad"), (0, "bad"), (None, None)])
    def test_thresholds(self, proxy_module, pct, level):
        assert proxy_module._reliability_level(pct) == level

    def test_card_reliability_uses_the_success_rate(self, proxy_module):
        rows = [_row(success=(i != 3)) for i in range(20)]   # 19 of 20 = 95%
        rel = proxy_module._v2_model_stats(rows)["reliability"]
        assert rel == {"pct": 95, "level": "warn"}


class TestModelStats:
    def test_starts_talking_is_the_median_of_replies_that_have_one(self, proxy_module):
        rows = [_row(first_token=1.0), _row(first_token=3.0), _row(first_token=2.0), _row()]   # last: not streamed
        stats = proxy_module._v2_model_stats(rows)
        assert stats["first_token"] == 2.0 and stats["first_token_samples"] == 3

    def test_no_streamed_replies_means_no_start_time(self, proxy_module):
        stats = proxy_module._v2_model_stats([_row(), _row()])
        assert stats["first_token"] is None and stats["first_token_samples"] == 0

    def test_fastest_typical_slowest_use_successful_replies_only(self, proxy_module):
        rows = [_row(elapsed=1.0), _row(elapsed=2.0), _row(elapsed=9.0), _row(elapsed=99.0, success=False)]
        assert proxy_module._v2_model_stats(rows)["full"] == {"fastest": 1.0, "typical": 2.0, "slowest": 9.0}

    def test_the_strip_is_the_last_forty_oldest_first_with_failures_marked(self, proxy_module):
        t0 = time.time()
        rows = [_row(elapsed=float(i), success=(i != 50), ts=t0 + i) for i in range(60)]
        strip = proxy_module._v2_model_stats(rows)["strip"]
        assert len(strip) == 40
        assert strip[0]["e"] == 20.0 and strip[-1]["e"] == 59.0
        assert [s["ok"] for s in strip].count(False) == 1 and strip[30]["ok"] is False

    def test_the_provider_is_the_most_recent_one_saved(self, proxy_module):
        t0 = time.time()
        rows = [_row(ts=t0, provider="OpenRouter"), _row(ts=t0 + 1, provider="GLM"), _row(ts=t0 + 2)]
        assert proxy_module._v2_model_stats(rows)["provider"] == "GLM"


class TestFailureLine:
    def test_matches_the_mockups_wording(self, proxy_module):
        rows = [_row(success=False, fail_reason="busy"), _row(success=False, fail_reason="busy"),
                _row(success=False, fail_reason="timeout"), _row()]
        assert proxy_module._failure_line(rows) == '3 failed: 2 "too busy", 1 took too long'

    def test_other_reasons(self, proxy_module):
        rows = [_row(success=False, fail_reason="server"), _row(success=False, fail_reason="auth"), _row(success=False, fail_reason="other")]
        assert proxy_module._failure_line(rows) == "3 failed: 1 server error, 1 login problem, 1 other"

    def test_no_line_when_nothing_failed(self, proxy_module):
        assert proxy_module._failure_line([_row(), _row()]) is None

    def test_no_line_when_no_failure_has_a_saved_reason(self, proxy_module):
        assert proxy_module._failure_line([_row(success=False), _row(success=False)]) is None

    def test_older_failures_without_a_reason_are_counted_honestly(self, proxy_module):
        rows = [_row(success=False, fail_reason="busy"), _row(success=False)]
        assert proxy_module._failure_line(rows) == '2 failed: 1 "too busy", 1 with no reason saved'


# ---------------- health cards ----------------

def _hour(n_ok=10, n_fail=0, first_token=1.0, reason="busy", provider="GLM"):
    t0 = time.time() - 600
    rows = [_row(first_token=first_token, provider=provider, ts=t0 + i) for i in range(n_ok)]
    rows += [_row(success=False, fail_reason=reason, provider=provider, ts=t0 + n_ok + i) for i in range(n_fail)]
    return rows


class TestHealth:
    def test_running_smooth(self, proxy_module):
        card = proxy_module._health_card("Claude", _hour(48), [1.0] * 20)
        assert (card["verdict"], card["level"], card["title"], card["why"]) == ("smooth", "ok", "Running smooth", "48 replies, none failed")

    def test_singular_reply(self, proxy_module):
        assert proxy_module._health_card("Claude", _hour(1), [])["why"] == "1 reply, none failed"

    def test_a_few_failures_under_the_line_are_still_smooth_but_counted(self, proxy_module):
        card = proxy_module._health_card("GLM", _hour(30, 1), [])   # 1 of the last 20 = 5%
        assert card["verdict"] == "smooth" and card["why"] == "31 replies, 1 failed"

    def test_hitting_errors_over_ten_percent_of_the_last_twenty(self, proxy_module):
        rows = _hour(16, 4, reason="busy")
        card = proxy_module._health_card("GLM", rows, [])
        assert (card["verdict"], card["level"], card["title"]) == ("errors", "bad", "Hitting errors")
        assert card["why"] == '4 of the last 20 failed · 4 were "too busy"'

    def test_exactly_ten_percent_is_not_yet_errors(self, proxy_module):
        assert proxy_module._health_card("GLM", _hour(18, 2), [])["verdict"] == "smooth"

    def test_errors_only_look_at_the_last_twenty(self, proxy_module):
        t0 = time.time() - 900
        old_failures = [_row(success=False, provider="GLM", ts=t0 + i) for i in range(10)]
        recent_ok = [_row(first_token=1.0, provider="GLM", ts=t0 + 100 + i) for i in range(20)]
        assert proxy_module._health_card("GLM", old_failures + recent_ok, [])["verdict"] == "smooth"

    def test_slow_to_start_talking_against_the_services_own_usual(self, proxy_module):
        card = proxy_module._health_card("OpenRouter", _hour(12, first_token=3.4), [1.2] * 30)
        assert (card["verdict"], card["level"], card["title"]) == ("slow", "warn", "Slow to start talking")
        assert card["why"] == "Replies starting in 3.4s, usually 1.2s"

    def test_a_modest_slowdown_is_not_flagged(self, proxy_module):
        assert proxy_module._health_card("X", _hour(12, first_token=1.9), [1.2] * 30)["verdict"] == "smooth"   # not 2x
        assert proxy_module._health_card("X", _hour(12, first_token=0.6), [0.2] * 30)["verdict"] == "smooth"   # 3x, but under half a second more

    def test_not_enough_history_to_know_what_usual_is(self, proxy_module):
        assert proxy_module._health_card("X", _hour(12, first_token=9.0), [1.0] * 4)["verdict"] == "smooth"

    def test_errors_beat_slow(self, proxy_module):
        rows = _hour(10, 5, first_token=9.0)
        assert proxy_module._health_card("X", rows, [1.0] * 30)["verdict"] == "errors"

    def test_no_replies_yet(self, proxy_module):
        card = proxy_module._health_card("Gemini", [], [])
        assert (card["verdict"], card["level"], card["title"]) == ("none", "none", "No replies yet")

    def test_cards_are_worst_first(self, proxy_module):
        rows = _hour(10, provider="Claude") + _hour(10, 6, provider="GLM")
        cards = proxy_module._health_cards(rows, {}, None)
        assert [c["provider"] for c in cards] == ["GLM", "Claude"]

    def test_every_ready_service_adds_the_unused_ones_last(self, proxy_module):
        cards = proxy_module._health_cards(_hour(10, provider="Claude"), {}, ["Claude", "Ollama", "Gemini"])
        assert [(c["provider"], c["verdict"]) for c in cards] == [("Claude", "smooth"), ("Gemini", "none"), ("Ollama", "none")]

    def test_endpoint_defaults_to_the_last_hour_and_ignores_older_calls(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "m", time.time() - 60, 1.0, first_token=0.5, provider="GLM")
        _seed(db, "m", time.time() - 7200, 1.0, first_token=0.5, provider="Claude")   # two hours ago
        _seed(db, "m", time.time() - 60, 1.0)                                          # no provider saved (old row)
        body = test_client.get("/stats/speeds/health").json()
        assert body["show"] == "hour"
        assert [c["provider"] for c in body["cards"]] == ["GLM"]

    def test_endpoint_ready_view_includes_ready_services_with_no_calls(self, proxy_module, test_client):
        _seed(proxy_module.SPEED_HISTORY_DB, "m", time.time() - 60, 1.0, first_token=0.5, provider="GLM")
        with patch.object(proxy_module, "nano_api_key", "k"):
            body = test_client.get("/stats/speeds/health?show=ready").json()
        by_name = {c["provider"]: c for c in body["cards"]}
        assert by_name["GLM"]["verdict"] == "smooth"
        assert by_name["Nano-GPT"]["title"] == "No replies yet"

    def test_endpoint_rejects_an_unknown_view_and_respects_the_off_switch(self, proxy_module, test_client):
        assert test_client.get("/stats/speeds/health?show=bogus").status_code == 400
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            assert test_client.get("/stats/speeds/health").status_code == 404


# ---------------- /stats/speeds and By role ----------------

class TestEndpoints:
    def test_each_model_carries_its_v2_stats_for_every_range(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "glm-4.6", time.time() - 100, 3.0, first_token=1.0, out_tokens=100, provider="GLM")
        for rng in ("today", "7d", "30d", "all"):
            models = test_client.get(f"/stats/speeds?range={rng}").json()["models"]
            if rng == "today":
                models = [m for m in models if m["model"] == "glm-4.6"] or [{"model": "glm-4.6", "v2": proxy_module._v2_model_stats(proxy_module._query_v2_rows(None)[("GLM", "glm-4.6")])}]
            v2 = models[0]["v2"]
            assert v2["first_token"] == 1.0 and v2["provider"] == "GLM"

    def test_by_role_gives_share_reliability_and_start_time(self, proxy_module, test_client):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        for i in range(3):
            _seed(db, "fast-start", now - 10 - i, 2.0, roles=["Dialogue"], first_token=0.5)
        _seed(db, "slow-start", now - 5, 1.0, roles=["Dialogue"], first_token=2.0)
        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {}):
            models = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all").json()["models"]
        by = {m["model"]: m for m in models}
        assert by["fast-start"]["share"] == 75 and by["slow-start"]["share"] == 25
        assert by["fast-start"]["first_token"] == 0.5 and by["fast-start"]["reliability"] == {"pct": 100, "level": "ok"}

    def test_dialogue_is_ranked_by_how_fast_it_starts_talking(self, proxy_module):
        models = [
            {"model": "quick-overall", "avg": 1.0, "first_token": 1.5},
            {"model": "quick-start", "avg": 3.0, "first_token": 0.4},
            {"model": "whole-reply", "avg": 0.5, "first_token": None},
        ]
        assert [m["model"] for m in proxy_module._rank_by_role("Dialogue", models)] == ["quick-start", "quick-overall", "whole-reply"]

    def test_other_jobs_are_ranked_by_full_reply_time(self, proxy_module):
        models = [
            {"model": "quick-start", "avg": 3.0, "first_token": 0.4},
            {"model": "quick-overall", "avg": 1.0, "first_token": 1.5},
        ]
        assert [m["model"] for m in proxy_module._rank_by_role("Memory", models)] == ["quick-overall", "quick-start"]

    def test_by_role_endpoint_applies_the_ranking(self, proxy_module, test_client):
        db, now = proxy_module.SPEED_HISTORY_DB, time.time()
        _seed(db, "slow-start-fast-total", now - 5, 1.0, roles=["Dialogue"], first_token=0.9)
        _seed(db, "fast-start-slow-total", now - 4, 6.0, roles=["Dialogue"], first_token=0.3)
        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {}):
            models = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all").json()["models"]
        assert [m["model"] for m in models] == ["fast-start-slow-total", "slow-start-fast-total"]


class TestThresholds:
    def test_starts_talking_bounds_round_trip_and_persist(self, proxy_module, test_client):
        payload = {k: dict(v) for k, v in proxy_module.DEFAULT_SPEED_THRESHOLDS.items()}
        payload["starts_talking"] = {k: {"good": 1.0, "slow": 2.0} for k in proxy_module.DEFAULT_SPEED_THRESHOLDS}
        r = test_client.post("/config/speed-thresholds", json=payload)
        assert r.status_code == 200
        assert r.json()["starts_talking_thresholds"]["dialogue"] == {"good": 1.0, "slow": 2.0}
        assert proxy_module._load_config()["starts_talking_thresholds"]["vision"] == {"good": 1.0, "slow": 2.0}
        assert test_client.get("/stats/speeds").json()["starts_talking_thresholds"]["dialogue"]["good"] == 1.0

    def test_leaving_them_out_keeps_what_was_saved(self, proxy_module, test_client):
        before = {k: dict(v) for k, v in proxy_module.starts_talking_thresholds.items()}
        test_client.post("/config/speed-thresholds", json={k: dict(v) for k, v in proxy_module.DEFAULT_SPEED_THRESHOLDS.items()})
        assert proxy_module.starts_talking_thresholds == before

    def test_bad_starts_talking_bounds_are_refused(self, proxy_module, test_client):
        payload = {k: dict(v) for k, v in proxy_module.DEFAULT_SPEED_THRESHOLDS.items()}
        payload["starts_talking"] = {k: {"good": 5.0, "slow": 1.0} for k in proxy_module.DEFAULT_SPEED_THRESHOLDS}
        assert test_client.post("/config/speed-thresholds", json=payload).status_code == 400
        payload["starts_talking"] = {"dialogue": {"good": 1.0, "slow": 2.0}}
        assert test_client.post("/config/speed-thresholds", json=payload).status_code == 400

    def test_dialogue_is_quick_under_one_and_a_half_seconds_by_default(self, proxy_module):
        assert proxy_module.DEFAULT_STARTS_TALKING_THRESHOLDS["dialogue"]["good"] == 1.5


# ---------------- the page ----------------

def _run_node(proxy_module, body: str) -> str:
    """Run the page's own Speeds script under Node with a tiny stub of the browser."""
    prelude = """
      const store = {}; let storeBroken = false;
      global.window = { localStorage: {
        getItem: k => { if (storeBroken) throw new Error('blocked'); return k in store ? store[k] : null; },
        setItem: (k, v) => { if (storeBroken) throw new Error('blocked'); store[k] = String(v); } } };
      const els = {};
      global.$ = id => els[id] || (els[id] = { style: {}, className: '', classList: { toggle() {} } });
      global._escapeHtml = s => String(s);
      global.document = { querySelectorAll: () => [], activeElement: null };
    """
    import tempfile
    if NODE is None:
        pytest.skip("Node isn't installed")          # (a plain Linux container has no node; several tests below run the page script through it)
    src = proxy_module._SPEEDS_V2_JS
    # a real file, not `node -e`: the script has non-ASCII text (the middle dot, the dash, the approx sign)
    # that a Windows command line mangles
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(prelude + src + "\n" + body)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return out.stdout.decode("utf-8").strip()


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
class TestPageScript:
    def test_by_role_puts_dialogue_first_whatever_order_the_server_sends(self, proxy_module):
        assert _run_node(proxy_module, "console.log(JSON.stringify(orderRoles(['Diary Generation','Memory','Dialogue','Vision'])))") == \
            '["Dialogue","Diary Generation","Memory","Vision"]'

    def test_without_dialogue_the_order_is_untouched(self, proxy_module):
        assert _run_node(proxy_module, "console.log(JSON.stringify(orderRoles(['Memory','Vision'])))") == '["Memory","Vision"]'

    def test_the_defaults_are_words_and_last_hour(self, proxy_module):
        assert _run_node(proxy_module, "console.log(speedUnit, healthShow)") == "words hour"

    def test_a_saved_choice_is_restored(self, proxy_module):
        script = "store['speeds.unit']='tokens'; store['speeds.healthShow']='ready';\n"
        # re-evaluate the two lines that read the store, the way a fresh page load would
        out = _run_node(proxy_module, script + "console.log(prefGet('speeds.unit','words'), prefGet('speeds.healthShow','hour'))")
        assert out == "tokens ready"

    def test_a_blocked_store_falls_back_to_the_defaults_without_throwing(self, proxy_module):
        out = _run_node(proxy_module, "storeBroken = true; prefSet('speeds.unit','tokens'); console.log(prefGet('speeds.unit','words'))")
        assert out == "words"

    def test_choices_are_saved_when_changed(self, proxy_module):
        script = "setSpeedUnit = function(u){ speedUnit = u; prefSet('speeds.unit', u); };\nsetSpeedUnit('tokens'); console.log(store['speeds.unit'])"
        assert _run_node(proxy_module, script) == "tokens"

    def test_reliability_words(self, proxy_module):
        out = _run_node(proxy_module, "console.log([reliabilityText({pct:99,level:'ok'}), reliabilityText({pct:94,level:'warn'}), reliabilityText({pct:80,level:'bad'})].join('|'))")
        assert out == "Reliable · 99%|Some errors · 94%|Unreliable · 80%"

    def test_short_verdict_wording(self, proxy_module):
        out = _run_node(proxy_module, "console.log([shortVerdict('ok','dialogue','Dialogue'), shortVerdict('warn','gamemaster','Game Master'), shortVerdict('ok','background','Memory'), shortVerdict('bad','vision','Vision')].join('|'))")
        assert out == "Quick for Dialogue|A bit slow for Game Master|Fine for Memory|Slow for Vision"

    def test_a_card_shows_the_mockups_numbers(self, proxy_module):
        script = """
          currentThresholds = {dialogue:{good:2,slow:4}}; currentStartsThresholds = {dialogue:{good:1.5,slow:3}};
          const html = renderSpeedRow({model:'claude-sonnet-5', avg:1.6, calls_total:5, roles:[{label:'Dialogue',class:'dialogue',category:'dialogue'}],
            v2:{provider:'Claude', reliability:{pct:99,level:'ok'}, first_token:0.9, full:{fastest:0.8,typical:1.6,slowest:4.1},
                speed:{words:62.4,tokens:83.2,estimated:false}, strip:[{e:1.0,ok:true},{e:0,ok:false}], failure_line:'1 failed: 1 "too busy"'}});
          console.log(['Starts talking in','0.9s','Full reply in','1.6s','62 <small>words/s','via Claude','Reliable · 99%','Quick for Dialogue','0.8s fastest','typical 1.6s','4.1s slowest','Last 2 replies','1 failed'].every(t => html.includes(t)));
        """
        assert _run_node(proxy_module, script) == "true"

    def test_estimated_speed_gets_the_approx_sign_and_tokens_switch_changes_units(self, proxy_module):
        script = """
          currentThresholds = {}; currentStartsThresholds = {};
          const m = {model:'m', avg:2, calls_total:1, roles:[], v2:{reliability:{pct:100,level:'ok'}, first_token:null, first_token_note:'whole_reply',
            full:{fastest:2,typical:2,slowest:2}, speed:{words:38,tokens:51,estimated:true}}};
          const words = renderSpeedRow(m); speedUnit = 'tokens'; const toks = renderSpeedRow(m);
          console.log(words.includes('≈ 38 <small>words/s') && toks.includes('≈ 51 <small>tokens/s') && words.includes('Sends the whole reply at once'));
        """
        assert _run_node(proxy_module, script) == "true"

    def test_no_data_shows_dashes_not_zeros(self, proxy_module):
        script = """
          currentThresholds = {}; currentStartsThresholds = {};
          const html = renderSpeedRow({model:'codex/x', avg:6.2, calls_total:3, roles:[], v2:{reliability:{pct:100,level:'ok'}, first_token:null, first_token_note:'whole_reply',
            full:{fastest:6.2,typical:6.2,slowest:6.2}, speed_note:null, speed:null}});
          console.log(html.includes('<span class="n">—</span>') && !html.includes('0 <small>'));
        """
        assert _run_node(proxy_module, script) == "true"


# ---------------- honest labels on older history, and one "full reply" number (Speeds v2 fixes) ----------------

def _card_html(proxy_module, model_js: str) -> str:
    """The rendered By-model card for one model object, as text."""
    script = f"currentThresholds = {{}}; currentStartsThresholds = {{}}; console.log(JSON.stringify(renderSpeedRow({model_js})));"
    return json.loads(_run_node(proxy_module, script))


def _text(html: str) -> str:
    import re
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class TestOlderHistoryLabels:
    def _notes(self, proxy_module, rows):
        stats = proxy_module._v2_model_stats(rows)
        return stats["first_token_note"], stats["speed_note"]

    def test_an_old_streaming_providers_rows_are_not_measured_yet_not_whole_reply(self, proxy_module):
        # Claude streams, but these rows predate the new columns: no provider, no start time, no size.
        rows = [_row(elapsed=1.7) for _ in range(5)]
        assert self._notes(proxy_module, rows) == ("not_measured", "not_measured")

    def test_a_new_format_reply_that_came_in_one_piece_is_whole_reply(self, proxy_module):
        rows = [_row(elapsed=6.0, out_chars=700, provider="OpenRouter") for _ in range(3)]   # saved by v2, not streamed
        assert self._notes(proxy_module, rows) == ("whole_reply", None)

    def test_codex_is_whole_reply(self, proxy_module):
        rows = [_row(elapsed=6.2, out_chars=700, provider="Codex")]
        assert self._notes(proxy_module, rows) == ("whole_reply", None)

    def test_a_measured_start_time_needs_no_note(self, proxy_module):
        rows = [_row(elapsed=2.0, first_token=0.6, out_chars=400, provider="Claude")]
        assert self._notes(proxy_module, rows)[0] is None

    def test_old_rows_mixed_with_new_streamed_ones_show_the_number(self, proxy_module):
        rows = [_row(elapsed=1.7)] * 4 + [_row(elapsed=2.0, first_token=0.6, out_chars=400, provider="Claude")]
        stats = proxy_module._v2_model_stats(rows)
        assert stats["first_token"] == 0.6 and stats["first_token_note"] is None

    def test_old_rows_mixed_with_new_non_streamed_ones_are_whole_reply(self, proxy_module):
        rows = [_row(elapsed=1.7)] * 4 + [_row(elapsed=2.0, out_chars=400, provider="GLM")]
        assert self._notes(proxy_module, rows) == ("whole_reply", None)

    def test_nothing_succeeded_is_no_data(self, proxy_module):
        rows = [_row(success=False, provider="GLM", fail_reason="busy")]
        assert proxy_module._v2_model_stats(rows)["first_token_note"] == "no_data"

    def test_through_the_endpoint_an_old_claude_row_is_not_measured_yet(self, proxy_module, test_client):
        _seed(proxy_module.SPEED_HISTORY_DB, "claude-sonnet-5", time.time() - 60, 1.7)   # exactly what an old file holds
        v2 = test_client.get("/stats/speeds?range=all").json()["models"][0]["v2"]
        assert (v2["first_token"], v2["first_token_note"], v2["speed"], v2["speed_note"]) == (None, "not_measured", None, "not_measured")

    @pytest.mark.skipif(NODE is None, reason="Node isn't installed")
    def test_the_card_says_not_measured_yet_for_both_and_never_whole_reply(self, proxy_module):
        text = _text(_card_html(proxy_module, """{model:'claude-sonnet-5', avg:1.7, calls_total:5, roles:[],
            v2:{provider:'Claude', reliability:{pct:100,level:'ok'}, first_token:null, first_token_note:'not_measured', speed:null, speed_note:'not_measured',
                full:{fastest:1.2,typical:1.7,slowest:2.4}, strip:[]}}"""))
        assert text.count("Not measured yet") == 2
        assert "Sends the whole reply at once" not in text

    def test_the_card_says_whole_reply_only_when_that_is_known(self, proxy_module):
        text = _text(_card_html(proxy_module, """{model:'gpt-5.3-codex', avg:6.2, calls_total:3, roles:[],
            v2:{provider:'Codex', reliability:{pct:100,level:'ok'}, first_token:null, first_token_note:'whole_reply', speed:null, speed_note:null,
                full:{fastest:6.2,typical:6.2,slowest:6.2}, strip:[]}}"""))
        assert "Sends the whole reply at once" in text and "Not measured yet" not in text

    def test_a_model_with_no_successes_says_no_data_yet(self, proxy_module):
        text = _text(_card_html(proxy_module, """{model:'x', avg:0.2, calls_total:1, roles:[],
            v2:{provider:'OpenRouter', reliability:{pct:0,level:'bad'}, first_token:null, first_token_note:'no_data', speed:null, speed_note:'not_measured',
                full:{fastest:null,typical:null,slowest:null}, strip:[{e:0.2,ok:false}]}}"""))
        assert "No data yet" in text and "Not measured yet" not in text


class TestOneFullReplyNumber:
    def test_the_headline_is_the_median_of_successes_never_below_fastest(self, proxy_module):
        # fast failures (which fail in a fraction of a second) mixed with slower successes
        rows = [_row(elapsed=0.2, success=False, provider="GLM") for _ in range(6)] + \
               [_row(elapsed=6.6, provider="GLM"), _row(elapsed=7.6, provider="GLM"), _row(elapsed=9.0, provider="GLM")]
        full = proxy_module._v2_model_stats(rows)["full"]
        assert full == {"fastest": 6.6, "typical": 7.6, "slowest": 9.0}
        assert full["typical"] >= full["fastest"]

    def test_the_card_headline_equals_the_bars_typical(self, proxy_module):
        text = _text(_card_html(proxy_module, """{model:'glm-5.3', avg:3.5, calls_total:9, roles:[],
            v2:{provider:'GLM', reliability:{pct:37,level:'bad'}, first_token:null, first_token_note:'whole_reply', speed:null, speed_note:null,
                full:{fastest:6.6,typical:7.6,slowest:9.0}, strip:[]}}"""))
        assert "Full reply in 7.6s" in text          # NOT the old average (3.5s, dragged down by fast failures)
        assert "typical 7.6s" in text and "6.6s fastest" in text
        assert "3.5s" not in text

    def test_no_successes_means_a_dash_not_the_failures_time(self, proxy_module):
        text = _text(_card_html(proxy_module, """{model:'openrouter/qwen/qwen3-vl', avg:0.2, calls_total:1, roles:[],
            v2:{provider:'OpenRouter', reliability:{pct:0,level:'bad'}, first_token:null, first_token_note:'no_data', speed:null, speed_note:'not_measured',
                full:{fastest:null,typical:null,slowest:null}, strip:[{e:0.2,ok:false}]}}"""))
        assert "Full reply in —" in text and "0.2s" not in text

    def test_through_the_endpoint_the_numbers_agree(self, proxy_module, test_client):
        db, now = proxy_module.SPEED_HISTORY_DB, time.time()
        for i in range(6):
            _seed(db, "glm-5.3", now - 100 + i, 0.2, success=False, provider="GLM", fail_reason="server")
        for i, e in enumerate((6.6, 7.6, 9.0)):
            _seed(db, "glm-5.3", now - 50 + i, e, provider="GLM")
        m = test_client.get("/stats/speeds?range=all").json()["models"][0]
        assert m["v2"]["full"]["typical"] == 7.6 and m["v2"]["full"]["fastest"] == 6.6
        assert m["avg"] < m["v2"]["full"]["fastest"]   # the old average IS below fastest -- which is why the card no longer uses it

    def test_by_role_full_reply_column_is_the_typical_success_and_dash_without_one(self, proxy_module):
        script = """
          const ok = renderByRoleRow('Memory', {model:'a', avg:3.5, full_typical:7.6, first_token:null, share:60, reliability:{pct:37,level:'bad'}});
          const none = renderByRoleRow('Memory', {model:'b', avg:0.2, full_typical:null, first_token:null, share:40, reliability:{pct:0,level:'bad'}});
          console.log(ok.includes('7.6s') && !ok.includes('3.5s') && !none.includes('0.2s'));
        """
        assert _run_node(proxy_module, script) == "true"

    def test_by_role_ranks_other_jobs_by_the_typical_success_not_the_average(self, proxy_module):
        models = [
            {"model": "fast-fails", "avg": 1.0, "full_typical": 8.0, "first_token": None},
            {"model": "steady", "avg": 5.0, "full_typical": 5.0, "first_token": None},
            {"model": "all-failed", "avg": 0.1, "full_typical": None, "first_token": None},
        ]
        assert [m["model"] for m in proxy_module._rank_by_role("Memory", models)] == ["steady", "fast-fails", "all-failed"]


class TestPageMarkup:
    def test_the_new_pieces_are_on_the_page(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        html = " ".join(test_client.get("/").text.split())
        for text in (
            "How your AIs are doing right now",
            "A quick verdict for each service you've used lately, so you can spot a struggling one before your NPCs do.",
            "Used in the last hour", "Every ready service", "words/s", "tokens/s",
            "Starts talking", "Share of this job", "Reliability",
            'id="healthShow"', 'id="roleTabs"', 'id="unitToggle"',
        ):
            assert text in html, text

    def test_the_stats_grid_drops_to_two_columns_on_a_narrow_window(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        html = test_client.get("/").text
        assert "@media (max-width:600px) { .sv-stats { grid-template-columns:1fr 1fr } }" in html

    def test_thresholds_editor_has_starts_talking_rows_for_every_category(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        html = test_client.get("/").text
        for cat in ("dialogue", "gamemaster", "vision", "background"):
            for pre in ("st-", "th-"):
                assert f'id="{pre}{cat}-good"' in html and f'id="{pre}{cat}-slow"' in html


class TestRoleExplainerCopy:
    def test_plain_one_line_descriptions_are_in_the_page_script(self, proxy_module):
        js = proxy_module._SPEEDS_V2_JS
        for role, text in (("Dialogue", "The wait you feel before an NPC speaks."), ("GameMaster", "Runs in the background."),
                           ("Memory", "Runs in the background."), ("Vision", "Runs in the background."),
                           ("Diary Generation", "Runs in the background."), ("Character Profiles", "Runs in the background."),
                           ("Universal Translator", "Runs in the background."), ("Action Evaluation", "Runs in the background."),
                           ("Combat", "Needs to be instant."), ("Meta", "Runs in the background."),
                           ("AI Assistant", "The wait for your answer."), ("Decisions", "Needs to be instant.")):
            assert f"'{role}': \"{text}\"" in js, role
