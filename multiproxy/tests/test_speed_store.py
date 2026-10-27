"""The Speeds history's background writer, indexes, cheap reads and MultiProxy's own time (2026-10-02, speed_store.py / speed_queries.py).

The fast SQL reads are checked against the OLD Python functions on the same random rows: the old functions stay in multiproxy.py / speeds_time.py as the reference.
"""
import asyncio
import json
import logging
import random
import sqlite3
import threading
import time

import pytest

import speed_queries
import speed_store
import speeds_time


@pytest.fixture
def real_async_writer():
    """The real thing: tests/conftest.py puts the writer in test mode (each row written before the call returns); here it is the production mode."""
    speed_store.set_sync(False)
    yield
    speed_store.stop()
    speed_store.set_sync(True)


def _rows(db, sql="SELECT COUNT(*) FROM speed_history"):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


COLS = ("ts", "model", "elapsed", "success")


class TestWriter:
    def test_enqueue_returns_before_the_write_and_flush_waits_for_it(self, proxy_module, real_async_writer):
        db = proxy_module.SPEED_HISTORY_DB
        for i in range(300):
            assert speed_store.enqueue_row(db, COLS, (1000.0 + i, "m", 1.0, 1))
        assert speed_store.flush()
        assert _rows(db) == [(300,)]

    def test_the_file_is_in_wal_mode_and_rows_are_batched_into_few_commits(self, proxy_module, real_async_writer):
        db = proxy_module.SPEED_HISTORY_DB
        before = speed_store.stats()["commits"]
        for i in range(500):
            speed_store.enqueue_row(db, COLS, (1000.0 + i, "m", 1.0, 1))
        speed_store.flush()
        assert _rows(db, "PRAGMA journal_mode") == [("wal",)]
        assert speed_store.stats()["commits"] - before < 500          # one commit per batch, not one per row

    def test_stop_writes_what_is_queued(self, proxy_module, real_async_writer):
        db = proxy_module.SPEED_HISTORY_DB
        for i in range(120):
            speed_store.enqueue_row(db, COLS, (1000.0 + i, "m", 1.0, 1))
        speed_store.stop()
        assert _rows(db) == [(120,)]
        speed_store.enqueue_row(db, COLS, (5000.0, "m", 1.0, 1))      # a later row starts a fresh writer
        speed_store.flush()
        assert _rows(db) == [(121,)]

    def test_a_bad_row_is_logged_and_never_raised_and_its_neighbours_are_kept(self, proxy_module, real_async_writer, caplog):
        db = proxy_module.SPEED_HISTORY_DB
        with caplog.at_level(logging.WARNING, logger="proxy"):
            speed_store.enqueue_row(db, COLS, (1.0, "good-1", 1.0, 1))
            speed_store.enqueue_row(db, ("ts", "model", "elapsed", "success", "no_such_column"), (2.0, "bad", 1.0, 1, 5))
            speed_store.enqueue_row(db, COLS, (3.0, "good-2", 1.0, 1))
            assert speed_store.flush()
        assert sorted(r[0] for r in _rows(db, "SELECT model FROM speed_history")) == ["good-1", "good-2"]
        assert any("Speed history write failed" in r.message for r in caplog.records)
        assert speed_store.stats()["failed"] >= 1

    def test_a_long_read_does_not_hold_up_a_write(self, proxy_module, real_async_writer):
        db = proxy_module.SPEED_HISTORY_DB
        for i in range(50):
            speed_store.enqueue_row(db, COLS, (1000.0 + i, "m", 1.0, 1))
        speed_store.flush()
        reader = speed_store.reader(db)
        cur = reader.execute("SELECT * FROM speed_history")
        cur.fetchone()                                                 # a read is open and in progress, as a big Speeds query would be
        t0 = time.perf_counter()
        speed_store.enqueue_row(db, COLS, (9999.0, "late", 1.0, 1))
        assert speed_store.flush(timeout=3.0)
        assert time.perf_counter() - t0 < 2.0
        cur.fetchall()
        reader.close()
        assert _rows(db) == [(51,)]

    def test_the_generation_counter_moves_on_every_commit(self, proxy_module, real_async_writer):
        db = proxy_module.SPEED_HISTORY_DB
        g = speed_store.generation()
        speed_store.enqueue_row(db, COLS, (1.0, "m", 1.0, 1))
        speed_store.flush()
        assert speed_store.generation() > g

    def test_record_speed_queues_the_row_and_does_not_wait_for_the_disk(self, proxy_module, real_async_writer, monkeypatch):
        gate = threading.Event()
        real = speed_store._writer._write

        def slow_write(rows):
            gate.wait(5)
            real(rows)
        monkeypatch.setattr(speed_store._writer, "_write", slow_write)
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)

        async def go():
            t0 = time.perf_counter()
            await proxy_module._record_speed("slow-disk-model", 1.0, True)
            return time.perf_counter() - t0

        took = asyncio.run(go())
        assert took < 0.5                                              # the disk is stuck, the call returned anyway
        gate.set()
        assert speed_store.flush()
        assert _rows(proxy_module.SPEED_HISTORY_DB, "SELECT model FROM speed_history") == [("slow-disk-model",)]


class TestUpgradeInPlace:
    def test_an_old_schema_file_gets_the_new_column_indexes_and_wal_and_keeps_its_rows(self, proxy_module, tmp_path, monkeypatch):
        old = str(tmp_path / "old_schema.db")
        with sqlite3.connect(old) as conn:                              # exactly what the first version made: four columns and no index
            conn.execute("CREATE TABLE speed_history (ts REAL NOT NULL, model TEXT NOT NULL, elapsed REAL NOT NULL, success INTEGER NOT NULL)")
            conn.executemany("INSERT INTO speed_history VALUES (?, ?, ?, ?)", [(1000.0 + i, "old-model", 2.0, 1) for i in range(40)])
            conn.commit()
        monkeypatch.setattr(proxy_module, "SPEED_HISTORY_DB", old)
        proxy_module._init_speed_history_db()
        proxy_module._init_speed_history_db()                           # twice: still fine
        with sqlite3.connect(old) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(speed_history)")}
            idx = {r[1] for r in conn.execute("PRAGMA index_list(speed_history)")}
            assert {"own_ms", "job", "queue_wait", "roles_json"} <= cols
            assert {"idx_speed_history_ts", "idx_speed_history_job_ts", "idx_speed_history_model_ts"} <= idx
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("SELECT COUNT(*) FROM speed_history").fetchone()[0] == 40
        proxy_module._insert_speed_history_row("new-model", 1.0, True, None, extra={"own_ms": 2.5})     # and the old file takes new rows
        speed_store.flush()
        assert _rows(old, "SELECT own_ms FROM speed_history WHERE model = 'new-model'") == [(2.5,)]

    def test_the_time_queries_use_the_ts_index_not_a_full_scan(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        with sqlite3.connect(db) as conn:
            for sql in ("SELECT model, success FROM speed_history WHERE ts > 5",
                        "SELECT job, job_how FROM speed_history WHERE job IS NOT NULL ORDER BY ts DESC LIMIT 600"):
                plan = " ".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
                assert "USING" in plan and "INDEX" in plan and "idx_speed_history" in plan, plan


def _seed_random(db, n=2500, seed=7, days=100, with_own=True):
    rnd = random.Random(seed)
    now = time.time()
    models = [("alpha", "GLM"), ("beta", "Local"), ("gamma", "Claude"), ("delta", None), ("eps/pc", "Device-One")]
    rows = []
    for i in range(n):
        model, provider = rnd.choice(models)
        ts = now - rnd.random() * days * 86400
        ok = rnd.random() > 0.12
        elapsed = round(rnd.uniform(0.3, 9.0), 3)
        streamed = rnd.random() > 0.35
        ft = round(rnd.uniform(0.1, elapsed), 3) if streamed else None
        out_tokens = rnd.choice([None, 0, rnd.randint(5, 400)])
        out_chars = rnd.choice([None, 0, rnd.randint(20, 1500)])
        reason = None if ok else rnd.choice(["busy", "timeout", "server", "broken reply", None])
        repair = rnd.choice([None, "fine", "repaired", "asked_again"]) if ok else None
        job = rnd.choice([None, "dialogue", "memory", "combat"])
        roles = json.dumps([rnd.choice(["Dialogue", "Memory"])]) if rnd.random() > 0.3 else None
        timings = json.dumps({"cache_n": rnd.randint(0, 900), "prompt_n": rnd.randint(1, 300)}) if provider == "Local" and rnd.random() > 0.3 else None
        cost_note = rnd.choice([None, "free", "included in your subscription", "reported by the AI", "not reported"]) if i % 2 else None
        cost = round(rnd.uniform(0.0001, 0.02), 6) if cost_note in ("reported by the AI",) else (0.0 if cost_note == "free" else None)
        rows.append((ts, model, elapsed, 1 if ok else 0, roles, ft if ok else None, out_tokens if ok else None, out_chars if ok else None, reason, provider, repair,
                     job, "hint" if job else None, rnd.choice([None, 300, 900]), cost, cost_note, timings, rnd.choice([None, 0.0, 1.5]),
                     round(rnd.uniform(0.5, 6.0), 3) if with_own and rnd.random() > 0.2 else None))
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO speed_history (ts, model, elapsed, success, roles_json, first_token, out_tokens, out_chars, fail_reason, provider, reply_repair, "
            "job, job_how, tokens_in, cost, cost_note, timings_json, queue_wait, own_ms) VALUES (" + ",".join("?" * 19) + ")", rows)
        conn.commit()


def _close(a, b, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (path, sorted(set(a) ^ set(b)))
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert isinstance(b, (list, tuple)) and len(a) == len(b), (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(a)), (path, a, b)
    else:
        assert a == b, (path, a, b)


class TestFastReadsMatchTheOldOnes:
    @pytest.mark.parametrize("window", [None, 7 * 86400.0, 30 * 86400.0, 86400.0])
    def test_models_summary_equals_the_python_version(self, proxy_module, window):
        db = proxy_module.SPEED_HISTORY_DB
        _seed_random(db)
        since = None if window is None else time.time() - window
        fast = speed_queries.models_summary(db, since, proxy_module._reliability_level)
        slow = proxy_module._models_summary_legacy(since)
        assert [m["model"] for m in fast] == [m["model"] for m in slow]
        _close(slow, fast)

    def test_a_model_that_never_succeeded_reads_like_before(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO speed_history (ts, model, elapsed, success, fail_reason, provider) VALUES (?, 'down', 1.0, 0, 'busy', 'GLM')",
                             [(now - 10 - i,) for i in range(3)])
        _close(proxy_module._models_summary_legacy(None), speed_queries.models_summary(db, None, proxy_module._reliability_level))

    def test_a_model_with_a_single_row_reads_like_before(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO speed_history (ts, model, elapsed, success) VALUES (?, 'solo', 2.0, 1)", (time.time(),))
        _close(proxy_module._models_summary_legacy(None), speed_queries.models_summary(db, None, proxy_module._reliability_level))

    @pytest.mark.parametrize("pc", ["all", "cloud", "this"])
    def test_streaming_time_answer_equals_the_row_by_row_one(self, proxy_module, pc):
        db = proxy_module.SPEED_HISTORY_DB
        _seed_random(db, n=2000, seed=11)
        where = lambda model, provider: "This device" if provider == "Local" else "Cloud"
        keep = lambda w: pc == "all" or (pc == "this" and w == "This device") or (pc == "cloud" and w == "Cloud")
        rows = proxy_module._query_time_rows(None)
        old = speeds_time.aggregate(rows, label_of=lambda j: (j or "").title(), where_of=lambda r: where(r["model"], r.get("provider")), keep=keep, order_of=lambda j: 0)
        new = speed_queries.time_answer(db, None, label_of=lambda j: (j or "").title(), where_of=where, keep=keep, order_of=lambda j: 0)
        for answer in (old, new):                                      # (a tie between two cost notes is settled by set order in the old code: not compared)
            for g in answer["groups"]:
                g.pop("costKind")
        # groups come out in a fixed order; rows tied on every sort key may swap, so compare by identity
        key = lambda g: (str(g["job"]), g["label"], g["model"])
        assert sorted(old["groups"], key=key) and len(old["groups"]) == len(new["groups"])
        _close(sorted(old["groups"], key=key), sorted(new["groups"], key=key))
        _close(old["total"], new["total"])

    def test_health_rows_equal_the_old_query(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        _seed_random(db, n=3000, seed=5, days=2)
        now = time.time()
        _close(proxy_module._query_health_rows(now), speed_queries.health_rows(db, now, proxy_module.HEALTH_WINDOW_SECONDS, proxy_module._V2_ROW_COLUMNS))

    @pytest.mark.parametrize("window", [None, 7 * 86400.0])
    def test_by_role_rows_equal_the_old_queries(self, proxy_module, window):
        db = proxy_module.SPEED_HISTORY_DB
        _seed_random(db)
        since = None if window is None else time.time() - window
        rows, v2 = speed_queries.by_role_rows(db, "Dialogue", since, proxy_module._V2_ROW_COLUMNS, proxy_module._roles_of)
        _close(proxy_module._query_speed_history_rows_by_role("Dialogue", since), rows)
        _close(proxy_module._query_v2_rows_by_role("Dialogue", since), v2)

    def test_by_role_never_mixes_up_labels_that_contain_each_other(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO speed_history (ts, model, elapsed, success, roles_json) VALUES (?, ?, 1.0, 1, ?)",
                             [(time.time(), "a", json.dumps(["Vision"])), (time.time(), "b", json.dumps(["Vision2"])), (time.time(), "c", json.dumps(["100%_x"]))])
        rows, _ = speed_queries.by_role_rows(db, "Vision", None, proxy_module._V2_ROW_COLUMNS, proxy_module._roles_of)
        assert [r[0] for r in rows] == [(None, "a")]
        rows, _ = speed_queries.by_role_rows(db, "100%_x", None, proxy_module._V2_ROW_COLUMNS, proxy_module._roles_of)
        assert [r[0] for r in rows] == [(None, "c")]
        assert speed_queries.distinct_roles(db, proxy_module._roles_of) == ["100%_x", "Vision", "Vision2"]

    def test_routing_history_keeps_two_providers_with_the_same_model_name_apart(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO speed_history (ts, model, elapsed, success, provider) VALUES (?, ?, 1.0, ?, ?)",
                             [(now, "grok-4.7", 1, "Grok")] * 4 + [(now, "grok-4.7", 0, "Continuum")] * 3 + [(now, "grok-4.7", 1, None)] * 2)
        stats, _ = speed_queries.routing_history(db, time.time())
        assert stats["Grok::grok-4.7"]["calls"] == 4 and stats["Grok::grok-4.7"]["ok"] == 4
        assert stats["Continuum::grok-4.7"]["calls"] == 3 and stats["Continuum::grok-4.7"]["ok"] == 0
        assert len(stats) == 2, "old rows saved with no provider can't be told apart, so they are left out"

    def test_routing_history_equals_the_old_read(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        _seed_random(db, n=2500, seed=3, days=3)
        fast = speed_queries.routing_history(db, time.time())
        # the old read, spelled out (it lived in _routing_history before): per-model counts and the median-ish first word, and the last 600 jobs
        with sqlite3.connect(db) as conn:
            by: dict = {}
            for provider, model, success, first_token in conn.execute("SELECT provider, model, success, first_token FROM speed_history WHERE ts > ? AND provider IS NOT NULL AND provider != ''", (time.time() - 86400,)):
                e = by.setdefault(provider + "::" + model, {"calls": 0, "ok": 0, "fw": []})
                e["calls"] += 1
                e["ok"] += 1 if success else 0
                if success and first_token:
                    e["fw"].append(first_token)
            stats = {m: {"calls": e["calls"], "ok": e["ok"], "first_word": round(sorted(e["fw"])[len(e["fw"]) // 2], 2) if e["fw"] else None} for m, e in by.items()}
            seen: dict = {}
            for job, how in conn.execute("SELECT job, job_how FROM speed_history WHERE job IS NOT NULL ORDER BY ts DESC LIMIT 600"):
                for k in str(job).split("/"):
                    seen.setdefault(k, how)
        assert fast == (stats, seen)
        assert proxy_module._routing_history() == (stats, seen)               # and through the module's own function


class TestOwnTime:
    def test_median_over_the_rows_that_have_it_and_null_when_none(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        assert speed_queries.own_time(db, None) == {"medianMs": None, "requests": 0}
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO speed_history (ts, model, elapsed, success, own_ms, provider) VALUES (?, ?, 1.0, 1, ?, ?)",
                             [(now, "m", v, "GLM") for v in (1.0, 2.0, 9.0, 4.0)] + [(now, "m", None, "GLM"), (now - 90 * 86400, "old", 50.0, "GLM")])
        assert speed_queries.own_time(db, None) == {"medianMs": 4.0, "requests": 5}           # 1, 2, 4, 9 and the old 50: the middle of five is 4
        assert speed_queries.own_time(db, now - 86400) == {"medianMs": 3.0, "requests": 4}    # 1, 2, 4, 9 -> 3

    def test_the_pc_filter_narrows_the_median(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        with sqlite3.connect(db) as conn:
            conn.executemany("INSERT INTO speed_history (ts, model, elapsed, success, own_ms, provider) VALUES (?, ?, 1.0, 1, ?, ?)",
                             [(now, "cloudy", 2.0, "GLM"), (now, "cloudy", 4.0, "GLM"), (now, "homey", 10.0, "Local"), (now, "nullprov", 30.0, None)])
        only_local = lambda model, provider: provider == "Local"
        assert speed_queries.own_time(db, None, only_local) == {"medianMs": 10.0, "requests": 1}
        assert speed_queries.own_time(db, None, lambda m, p: False) == {"medianMs": None, "requests": 0}
        assert speed_queries.own_time(db, None, lambda m, p: p is None) == {"medianMs": 30.0, "requests": 1}

    def test_the_record_measures_everything_but_the_ai_and_the_line(self):
        import request_facts
        rec = request_facts.RequestRecord(request_id="t1")
        time.sleep(0.02)                                   # MultiProxy's own work before the AI is handed the request
        rec.attempt_started(waited=0.0)
        time.sleep(0.05)                                   # the AI working
        rec.ai_returned()
        time.sleep(0.01)                                   # MultiProxy's own work after the AI's last byte
        rec.attempt_ended()
        own = rec.own_ms()
        assert 25 <= own <= 120, own                       # about 30 ms; the 50 ms the AI took is not in it
        assert request_facts.RequestRecord(request_id="t2", kind="decision").own_ms() is None

    def test_time_in_line_and_failed_attempts_are_not_counted(self):
        import request_facts
        rec = request_facts.RequestRecord(request_id="t3")
        rec.waited_s += 0.0                                # (the line time is handed in with the attempt)
        time.sleep(0.03)                                   # standing in line
        rec.attempt_started(waited=0.03)
        time.sleep(0.04)                                   # first AI fails
        rec.attempt_ended()
        rec.attempt_started(waited=0.0)
        time.sleep(0.04)                                   # backup AI answers
        rec.attempt_ended()
        assert rec.own_ms() < 40

    def _post(self, proxy_module, test_client, stream=False):
        from unittest.mock import AsyncMock, MagicMock, patch
        if stream:
            sse = b'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n\ndata: [DONE]\n\n'
            content = MagicMock()

            async def _iter():
                yield sse
            content.iter_any.return_value = _iter()
            resp = AsyncMock()
            resp.content = content
        else:
            resp = AsyncMock()
            body = '{"choices":[{"message":{"content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3}}'
            resp.text = AsyncMock(return_value=body)
            resp.json = AsyncMock(return_value=json.loads(body))
        resp.status = 200
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        session.close = AsyncMock()
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), patch.object(proxy_module, "auth", MagicMock(session=None)),              patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={"model": "kilo/anthropic/claude-haiku-4.5", "stream": stream,
                                                               "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("stream", [False, True])
    def test_a_real_request_stores_own_ms_and_the_speeds_page_answer_carries_it(self, proxy_module, test_client, monkeypatch, stream):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        self._post(proxy_module, test_client, stream)
        rows = _rows(proxy_module.SPEED_HISTORY_DB, "SELECT own_ms FROM speed_history")
        assert len(rows) == 1 and rows[0][0] is not None and 0 <= rows[0][0] < 2000, rows
        for rng in ("7d", "today"):
            body = test_client.get(f"/stats/speeds?range={rng}").json()
            assert body["ownTime"]["requests"] == 1 and body["ownTime"]["medianMs"] == pytest.approx(rows[0][0], abs=0.01)

    def test_the_speeds_answer_says_null_when_no_request_has_it(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        assert test_client.get("/stats/speeds?range=7d").json()["ownTime"] == {"medianMs": None, "requests": 0}


class TestPolledAnswers:
    def test_etag_gives_a_bodiless_304_when_nothing_changed(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        first = test_client.get("/stats/speeds?range=7d")
        assert first.status_code == 200 and first.headers["etag"]
        again = test_client.get("/stats/speeds?range=7d", headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b""
        assert test_client.get("/stats/speeds?range=7d", headers={"If-None-Match": '"something-else"'}).status_code == 200

    def test_the_other_speeds_answers_carry_an_etag_too(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        for url in ("/stats/speeds/time?range=7d", "/stats/speeds/health", "/stats/speeds/by-role?role=Dialogue&range=7d"):
            r = test_client.get(url)
            assert r.status_code == 200 and r.headers.get("etag"), url
            assert test_client.get(url, headers={"If-None-Match": r.headers["etag"]}).status_code == 304, url


class TestAnswerCache:
    def test_two_polls_at_once_share_one_read_and_a_recent_answer_is_reused(self):
        calls = []

        def slow():
            calls.append(1)
            time.sleep(0.15)
            return {"n": len(calls)}

        async def go():
            cache = speed_store.AnswerCache(ttl_s=5.0, ignore_test_mode=True)
            a, b = await asyncio.gather(cache.get("k", slow), cache.get("k", slow))
            c = await cache.get("k", slow)
            return a, b, c, cache

        a, b, c, cache = asyncio.run(go())
        assert a == b == c == {"n": 1} and len(calls) == 1
        assert cache.shared == 1 and cache.hits == 1

    def test_an_old_answer_is_read_again_and_different_keys_do_not_share(self):
        calls = []

        def fn(tag):
            calls.append(tag)
            return tag

        async def go():
            cache = speed_store.AnswerCache(ttl_s=0.0, still_s=0.0, slow_factor=0.0, ignore_test_mode=True)
            await cache.get(("a", 1), fn, "a1")
            await cache.get(("a", 1), fn, "a1")
            await cache.get(("a", 2), fn, "a2")
        asyncio.run(go())
        assert calls == ["a1", "a1", "a2"]

    def test_a_write_since_does_not_force_a_re_read_inside_the_ttl_but_does_after_it(self, proxy_module, real_async_writer):
        calls = []

        def fn():
            calls.append(1)
            return len(calls)

        async def go():
            cache = speed_store.AnswerCache(ttl_s=0.2, still_s=0.0, ignore_test_mode=True)
            await cache.get("k", fn)
            speed_store.enqueue_row(proxy_module.SPEED_HISTORY_DB, COLS, (1.0, "m", 1.0, 1))
            speed_store.flush()
            await cache.get("k", fn)                       # inside the TTL: the same answer (a few seconds old is fine)
            await asyncio.sleep(0.3)
            await cache.get("k", fn)                       # past it, and a row was written since: read again
        asyncio.run(go())
        assert len(calls) == 2

    def test_a_failed_read_reaches_every_waiter_and_is_not_kept(self):
        def boom():
            time.sleep(0.05)
            raise ValueError("no")

        async def go():
            cache = speed_store.AnswerCache(ignore_test_mode=True)
            results = await asyncio.gather(cache.get("k", boom), cache.get("k", boom), return_exceptions=True)
            assert all(isinstance(r, ValueError) for r in results)
            assert await cache.get("k", lambda: 5) == 5
        asyncio.run(go())


class TestEdgeCases:
    def test_rows_saved_out_of_time_order_still_read_like_before(self, proxy_module):
        """If the clock ever went back, rowid order is not time order: the last-40 strip must still follow the time."""
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        with sqlite3.connect(db) as conn:
            for i, ts_off in enumerate([50, 10, 30, 20, 60, 5]):
                conn.execute("INSERT INTO speed_history (ts, model, elapsed, success, provider) VALUES (?, 'm', ?, 1, ?)", (now - ts_off, float(i + 1), "P"))
        fast = speed_queries.models_summary(db, None, proxy_module._reliability_level)
        _close(proxy_module._models_summary_legacy(None), fast)
        assert [s["e"] for s in fast[0]["v2"]["strip"]] == [5.0, 1.0, 3.0, 4.0, 2.0, 6.0]       # oldest first by time: the newest row (5 s ago) is last

    def test_the_time_answer_is_the_same_without_sqlite_json_functions(self, proxy_module, monkeypatch):
        db = proxy_module.SPEED_HISTORY_DB
        _seed_random(db, n=1500, seed=21)
        kw = dict(label_of=lambda j: (j or "").title(), where_of=lambda m, p: "This device" if p == "Local" else "Cloud", keep=lambda w: True, order_of=lambda j: 0)
        with_json = speed_queries.time_answer(db, None, **kw)
        monkeypatch.setattr(speed_queries, "JSON_OK", False)
        without = speed_queries.time_answer(db, None, **kw)
        for a in (with_json, without):
            for g in a["groups"]:
                g.pop("costKind")
        key = lambda g: (str(g["job"]), g["label"], g["model"])
        _close(sorted(with_json["groups"], key=key), sorted(without["groups"], key=key))

    def test_an_empty_file_gives_empty_answers(self, proxy_module):
        db = proxy_module.SPEED_HISTORY_DB
        assert speed_queries.models_summary(db, None, proxy_module._reliability_level) == []
        assert speed_queries.routing_history(db, time.time()) == ({}, {})
        assert speed_queries.time_answer(db, None, label_of=str, where_of=lambda m, p: "Cloud", keep=lambda w: True, order_of=lambda j: 0)["groups"] == []
