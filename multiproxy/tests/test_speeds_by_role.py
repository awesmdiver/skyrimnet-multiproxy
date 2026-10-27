"""Tests for the Speeds tab's "compare by role" view (2026-09-20).

Role chips on the existing "By model" view are read LIVE from SkyrimNet's current config -- the
moment Dialogue moves from model A to model B, model A's chip vanishes and its older history can
no longer be labeled. This feature snapshots the role(s) a model was serving into each
speed_history row at write time (roles_json), so every model that's ever filled a given role can
be compared side by side, even after the config has since moved that role elsewhere.

Isolation: tests/conftest.py's autouse _isolate_speed_history_db fixture redirects
proxy.SPEED_HISTORY_DB to a throwaway file per test.
"""
import json
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_response(status=200, text=""):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    if status == 200:
        resp.json = AsyncMock(return_value=json.loads(text))
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


def _seed(db_path: str, model: str, ts: float, elapsed: float, success: bool = True, roles=None) -> None:
    """Direct-SQL test seeding -- bypasses _insert_speed_history_row (which always stamps
    time.time() itself) so tests can place rows at exact, controlled points in the past, with an
    exact roles_json snapshot rather than whatever the live role-chip lookup would produce."""
    roles_json = json.dumps(roles) if roles else None
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO speed_history (ts, model, elapsed, success, roles_json) VALUES (?, ?, ?, ?, ?)",
            (ts, model, elapsed, 1 if success else 0, roles_json),
        )
        conn.commit()


class TestRolesJsonPopulatedOnWrite:
    def test_record_speed_snapshots_whatever_role_chips_currently_says_for_that_model(self, proxy_module, test_client):
        fake_chips = {
            "Kilo Gateway::anthropic/claude-haiku-4.5": [
                {"label": "Dialogue", "class": "dialogue", "category": "dialogue"},
                {"label": "Memory", "class": "other", "category": "background"},
            ]
        }
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch.object(proxy_module, "_get_role_chips_cached", lambda: fake_chips), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200

        with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
            row = conn.execute(
                "SELECT roles_json FROM speed_history WHERE model = ?", ("anthropic/claude-haiku-4.5",)
            ).fetchone()
        assert json.loads(row[0]) == ["Dialogue", "Memory"]

    def test_record_speed_writes_null_roles_json_when_nothing_is_linked(self, proxy_module, test_client):
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch.object(proxy_module, "_get_role_chips_cached", lambda: {}), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/unlinked-model",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200

        with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
            row = conn.execute(
                "SELECT roles_json FROM speed_history WHERE model = ?", ("some/unlinked-model",)
            ).fetchone()
        assert row[0] is None


class TestSchemaUpgrade:
    def test_a_pre_existing_db_without_roles_json_upgrades_cleanly(self, proxy_module, tmp_path):
        """Simulates a real player's speed_history.db from before this column existed: a plain
        CREATE TABLE with the OLD 4-column schema, with a real row already in it. Re-running
        _init_speed_history_db against this file must add the column without erroring or losing
        the old row."""
        legacy_db = str(tmp_path / "legacy_speed_history.db")
        with sqlite3.connect(legacy_db) as conn:
            conn.execute(
                "CREATE TABLE speed_history (ts REAL NOT NULL, model TEXT NOT NULL, "
                "elapsed REAL NOT NULL, success INTEGER NOT NULL)"
            )
            conn.execute(
                "INSERT INTO speed_history (ts, model, elapsed, success) VALUES (?, ?, ?, ?)",
                (1700000000.0, "old/model", 2.5, 1),
            )
            conn.commit()

        with patch.object(proxy_module, "SPEED_HISTORY_DB", legacy_db):
            proxy_module._init_speed_history_db()  # must not raise

            with sqlite3.connect(legacy_db) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(speed_history)")}
                assert "roles_json" in cols
                old_row = conn.execute(
                    "SELECT model, elapsed, success FROM speed_history WHERE model = 'old/model'"
                ).fetchone()
                assert old_row == ("old/model", 2.5, 1)

            # A fresh write against the upgraded file must also work (the new column is usable,
            # not just present).
            proxy_module._insert_speed_history_row("new/model", 1.0, True, json.dumps(["Dialogue"]))
            with sqlite3.connect(legacy_db) as conn:
                new_row = conn.execute(
                    "SELECT roles_json FROM speed_history WHERE model = 'new/model'"
                ).fetchone()
                assert json.loads(new_row[0]) == ["Dialogue"]

    def test_running_init_twice_on_an_already_upgraded_db_is_a_harmless_no_op(self, proxy_module):
        proxy_module._init_speed_history_db()
        proxy_module._init_speed_history_db()  # must not raise (column already exists)
        with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(speed_history)")}
        assert "roles_json" in cols


class TestStatsSpeedsRoles:
    def test_returns_only_labels_actually_present_in_history(self, proxy_module, test_client):
        now = time.time()
        _seed(proxy_module.SPEED_HISTORY_DB, "model-a", now - 10, 1.0, roles=["Dialogue"])
        _seed(proxy_module.SPEED_HISTORY_DB, "model-b", now - 5, 2.0, roles=["Diary Generation", "Memory"])
        _seed(proxy_module.SPEED_HISTORY_DB, "model-c", now - 1, 0.5, roles=None)  # never linked

        r = test_client.get("/stats/speeds/roles")
        assert r.status_code == 200
        assert r.json()["roles"] == ["Dialogue", "Diary Generation", "Memory"]  # sorted

    def test_empty_history_returns_no_roles(self, proxy_module, test_client):
        r = test_client.get("/stats/speeds/roles")
        assert r.status_code == 200
        assert r.json()["roles"] == []

    def test_404s_when_speed_tracking_is_disabled(self, proxy_module, test_client):
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            r = test_client.get("/stats/speeds/roles")
        assert r.status_code == 404


class TestStatsSpeedsByRole:
    def test_aggregates_ranks_fastest_first_and_flags_the_current_model(self, proxy_module, test_client):
        now = time.time()
        db = proxy_module.SPEED_HISTORY_DB
        # model-old served Dialogue first, was slower, then got swapped out.
        _seed(db, "model-old", now - 20 * 86400, 3.0, roles=["Dialogue"])
        _seed(db, "model-old", now - 15 * 86400, 3.2, roles=["Dialogue"])
        # model-new took over Dialogue more recently, and is faster.
        _seed(db, "model-new", now - 5 * 86400, 1.0, success=True, roles=["Dialogue"])
        _seed(db, "model-new", now - 1 * 86400, 1.2, success=False, roles=["Dialogue"])

        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {
            "::model-new": [{"label": "Dialogue", "class": "dialogue", "category": "dialogue"}],
        }):
            r = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all")
        assert r.status_code == 200
        data = r.json()
        assert data["role"] == "Dialogue"
        models = data["models"]
        assert [m["model"] for m in models] == ["model-new", "model-old"]  # fastest avg first

        new = models[0]
        assert new["calls"] == 2
        assert new["success_pct"] == 50
        assert new["avg"] == pytest.approx(1.1)
        assert new["currently_serving"] is True
        assert new["first_seen"] == pytest.approx(now - 5 * 86400, abs=2)
        assert new["last_seen"] == pytest.approx(now - 1 * 86400, abs=2)

        old = models[1]
        assert old["calls"] == 2
        assert old["success_pct"] == 100
        assert old["avg"] == pytest.approx(3.1)
        assert old["currently_serving"] is False

    def test_range_window_excludes_rows_outside_it(self, proxy_module, test_client):
        now = time.time()
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "model-a", now - 40 * 86400, 1.0, roles=["Vision"])  # outside 30d
        _seed(db, "model-a", now - 2 * 86400, 2.0, roles=["Vision"])   # inside 30d

        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {}):
            r = test_client.get("/stats/speeds/by-role?role=Vision&range=30d")
        assert r.status_code == 200
        models = r.json()["models"]
        assert len(models) == 1
        assert models[0]["calls"] == 1
        assert models[0]["avg"] == pytest.approx(2.0)

    def test_exact_role_match_does_not_cross_contaminate_a_substring_label(self, proxy_module, test_client):
        """A role label that's a prefix/substring of another (Vision vs. Vision2) must never
        cross-match -- proves this uses an exact membership check, not a SQL LIKE/substring."""
        now = time.time()
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "model-vision", now - 1, 1.0, roles=["Vision"])
        _seed(db, "model-vision2", now - 1, 5.0, roles=["Vision2"])

        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {}):
            r = test_client.get("/stats/speeds/by-role?role=Vision&range=all")
        assert r.status_code == 200
        models = r.json()["models"]
        assert [m["model"] for m in models] == ["model-vision"]

    def test_unknown_role_returns_an_empty_model_list_not_an_error(self, proxy_module, test_client):
        _seed(proxy_module.SPEED_HISTORY_DB, "model-a", time.time(), 1.0, roles=["Dialogue"])
        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {}):
            r = test_client.get("/stats/speeds/by-role?role=NeverSeenRole&range=all")
        assert r.status_code == 200
        assert r.json()["models"] == []

    def test_invalid_range_is_rejected(self, proxy_module, test_client):
        r = test_client.get("/stats/speeds/by-role?role=Dialogue&range=bogus")
        assert r.status_code == 400

    def test_404s_when_speed_tracking_is_disabled(self, proxy_module, test_client):
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            r = test_client.get("/stats/speeds/by-role?role=Dialogue")
        assert r.status_code == 404
