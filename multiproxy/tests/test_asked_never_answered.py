"""A model that was asked and never answered still shows on Speeds, in the Overview and on the Routing page (2026-10-07).

The real case: every Dialogue line went to one local model, SkyrimNet hung up six times before it answered, and the Speeds page did not list that model at all
(a model only appeared after it had answered). Now a hang-up, a timeout and an error are all saved as failed requests, and the pages say so.
"""
import sqlite3
import time

import pytest
from fastapi import HTTPException

import overview
import speed_queries
import speed_store


def _seed(db, model, provider, ok, failed, *, reason="gave up", job="dialogue", waited=30.0, ts=None):
    ts = ts or time.time() - 60
    with sqlite3.connect(db) as conn:
        for i in range(ok):
            conn.execute("INSERT INTO speed_history (ts, model, elapsed, success, provider, job) VALUES (?, ?, 1.0, 1, ?, ?)", (ts + i, model, provider, job))
        for i in range(failed):
            conn.execute("INSERT INTO speed_history (ts, model, elapsed, success, fail_reason, provider, job) VALUES (?, ?, ?, 0, ?, ?, ?)",
                         (ts + 100 + i, model, waited + i, reason, provider, job))
        conn.commit()


@pytest.fixture()
def tracking(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    return proxy_module


# ---- what gets recorded ----

@pytest.mark.asyncio
async def test_a_timeout_before_the_first_word_is_saved_as_a_failure(tracking):
    async def never():
        raise HTTPException(status_code=504, detail="The AI did not answer in time.")
        yield ""
    with pytest.raises(HTTPException):
        await tracking._open_stream(None, never(), "abc", "m", "ollama/gemma", None, "Ollama")
    speed_store.flush()
    with sqlite3.connect(tracking.SPEED_HISTORY_DB) as conn:
        assert conn.execute("SELECT success, fail_reason, provider, model FROM speed_history").fetchall() == [(0, "timeout", "Ollama", "ollama/gemma")]


@pytest.mark.asyncio
async def test_an_error_from_the_model_is_saved_as_a_failure(tracking):
    async def broken():
        raise HTTPException(status_code=500, detail="boom")
        yield ""
    with pytest.raises(HTTPException):
        await tracking._open_stream(None, broken(), "abc", "m", "ollama/gemma", None, "Ollama")
    speed_store.flush()
    with sqlite3.connect(tracking.SPEED_HISTORY_DB) as conn:
        assert conn.execute("SELECT success, fail_reason FROM speed_history").fetchall() == [(0, "server")]


def test_nothing_but_counts_and_reasons_is_read_back(tracking):
    _seed(tracking.SPEED_HISTORY_DB, "ollama/gemma", "Ollama", 0, 6)
    got = speed_queries.hour_failures(tracking.SPEED_HISTORY_DB, time.time(), 3600)
    assert got == [{"provider": "Ollama", "model": "ollama/gemma", "ok": 0, "failed": 6, "longest": 35.0, "reasons": {"gave up": 6}, "note": None, "job": "dialogue"}]


# ---- the Speeds page ----

def test_a_model_with_only_failures_gets_a_card_by_the_colour_rules(tracking):
    fails = speed_queries.hour_failures(tracking.SPEED_HISTORY_DB, time.time(), 3600)
    assert fails == []
    _seed(tracking.SPEED_HISTORY_DB, "ollama/gemma", "Ollama", 0, 6)
    recent, baseline = speed_queries.health_rows(tracking.SPEED_HISTORY_DB, time.time(), 3600, tracking._V2_ROW_COLUMNS)
    fails = speed_queries.hour_failures(tracking.SPEED_HISTORY_DB, time.time(), 3600)
    alone = tracking._health_cards(recent, baseline, None, fails, {"dialogue": 1})[0]
    assert (alone["verdict"], alone["level"], alone["title"], alone["why"]) == ("silent", "bad", "Not answering", "0 replies, 6 gave up waiting")
    assert alone["model"] == "ollama/gemma" and alone["detail"] == ["Dialogue · longest wait 35 s"]
    covered = tracking._health_cards(recent, baseline, None, fails, {"dialogue": 2})[0]
    assert covered["level"] == "warn", "amber: the route has another AI to try"
    no_route = tracking._health_cards(recent, baseline, None, fails, {})[0]
    assert no_route["level"] == "bad"


def test_a_model_that_answers_shows_as_before(tracking):
    _seed(tracking.SPEED_HISTORY_DB, "claude-x", "Claude", 20, 0)
    recent, baseline = speed_queries.health_rows(tracking.SPEED_HISTORY_DB, time.time(), 3600, tracking._V2_ROW_COLUMNS)
    fails = speed_queries.hour_failures(tracking.SPEED_HISTORY_DB, time.time(), 3600)
    card = tracking._health_cards(recent, baseline, None, fails, {})[0]
    assert (card["verdict"], card["why"]) == ("smooth", "20 replies, none failed") and "detail" not in card and "model" not in card


def test_a_service_with_answers_and_one_silent_model_keeps_its_verdict_and_names_the_silent_one(tracking):
    _seed(tracking.SPEED_HISTORY_DB, "good", "Ollama", 30, 0)
    _seed(tracking.SPEED_HISTORY_DB, "silent", "Ollama", 0, 4, reason="timeout")
    recent, baseline = speed_queries.health_rows(tracking.SPEED_HISTORY_DB, time.time(), 3600, tracking._V2_ROW_COLUMNS)
    fails = speed_queries.hour_failures(tracking.SPEED_HISTORY_DB, time.time(), 3600)
    card = tracking._health_cards(recent, baseline, None, fails, {})[0]
    assert card["verdict"] != "silent" and card["detail"] == ["silent: 0 replies, 4 timed out · Dialogue · longest wait 33 s"]


def test_the_health_page_and_by_model_list_show_the_silent_model(tracking, test_client):
    _seed(tracking.SPEED_HISTORY_DB, "ollama/gemma", "Ollama", 0, 6)
    cards = test_client.get("/stats/speeds/health").json()["cards"]
    assert [(c["provider"], c["title"], c["why"], c["model"]) for c in cards] == [("Ollama", "Not answering", "0 replies, 6 gave up waiting", "ollama/gemma")]
    models = test_client.get("/stats/speeds?range=7d").json()["models"]
    row = next(m for m in models if m["model"] == "ollama/gemma")
    assert row["v2"]["reliability"] == {"pct": 0, "level": "bad"} and row["v2"]["failure_line"] == "6 failed: 6 gave up waiting"
    page = test_client.get("/").text
    assert "c.detail" in page and "Some of its requests failed" in page


# ---- the Overview ----

FIG = {"perMinute": 1.0, "typical": None, "failuresToday": 6, "calls": 6}


def _overview(rows, ais):
    inp = dict(tracking=True, figures={**FIG, "rows": rows}, this_pc={"name": "msi", "host": "msi", "gpu": None, "usedMb": None, "totalMb": None},
               local_models=[], local_speeds={}, pcs=[], providers=[{"id": "ollama", "name": "Ollama", "short": "Ollama", "state": "ok", "status": "Ready", "cloud": False}],
               health={}, uses={"provider:Ollama": ["Dialogue"]}, routes=[{"job": "Dialogue", "ais": ais}], decisions=None, voice=None, setup={"done": True, "left": []})
    return overview.build(**inp)


OLLAMA = {"key": "provider:Ollama", "pc": None, "name": "Ollama"}
CLAUDE = {"key": "provider:Claude", "pc": None, "name": "Claude"}
LINE = "Your NPCs might stay quiet: Ollama didn't answer 6 of 6 Dialogue requests in the last hour."


def test_needs_a_look_names_a_dialogue_ai_that_failed_more_than_it_answered():
    d = _overview({("Ollama", "gemma"): {"n": 6, "ok": 0, "typical": None, "tokensPerSecond": None}}, [OLLAMA])
    line = next(a for a in d["attention"] if a["name"] == "Ollama")
    assert line["level"] == "bad" and line["line"].startswith(LINE), "red: nothing covers it"
    d = _overview({("Ollama", "gemma"): {"n": 6, "ok": 0, "typical": None, "tokensPerSecond": None}}, [OLLAMA, CLAUDE])
    assert next(a for a in d["attention"] if a["name"] == "Ollama")["level"] == "warn"


def test_a_dialogue_ai_that_answers_or_fails_rarely_adds_no_line():
    ok = {("Ollama", "gemma"): {"n": 20, "ok": 19, "typical": 1.0, "tokensPerSecond": None}}
    assert not [a for a in _overview(ok, [OLLAMA])["attention"] if "Dialogue" in a["line"]]
    few = {("Ollama", "gemma"): {"n": 2, "ok": 0, "typical": None, "tokensPerSecond": None}}
    assert not [a for a in _overview(few, [OLLAMA])["attention"] if "Dialogue" in a["line"]]


# ---- the Routing page ----

def test_the_routing_page_has_the_no_backup_line_only_for_a_single_failing_ai(tracking, test_client):
    page = test_client.get("/").text
    assert "This route has no backup. Use + Add a model below to add one." in page
    assert "st.ais.length === 1" in page and "only.worked < 80" in page
