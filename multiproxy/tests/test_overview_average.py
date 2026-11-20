"""Overview (2026-10-05): the Requests numbers are centred, the Average time column, and the Quick decisions line. All figures come from saved rows, hand-computed here."""
import sqlite3

import overview
import overview_ui
import speed_queries
from tests.test_overview import DEC, FIG, base, draw, needs_node, rows

DB_ROWS = [("m", "Claude", 990.0, 1.0, 1, None, None), ("m", "Claude", 980.0, 2.0, 1, None, None), ("m", "Claude", 970.0, 9.0, 0, None, None),
           ("local/decisions", "Local decision model", 990.0, 0.4, 1, None, None), ("local/decisions", "Local decision model", 991.0, 0.6, 1, None, None)]


def _db(tmp_path, decisions=()):
    db = str(tmp_path / "h.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE speed_history (model TEXT, provider TEXT, ts REAL, elapsed REAL, success INTEGER, first_token REAL, out_tokens INTEGER)")
    conn.executemany("INSERT INTO speed_history VALUES (?,?,?,?,?,?,?)", DB_ROWS)
    conn.execute("CREATE TABLE decision_log (ts REAL NOT NULL, confidence REAL, asked INTEGER NOT NULL, answered_by TEXT)")
    conn.executemany("INSERT INTO decision_log VALUES (?,?,?,?)", decisions)
    conn.commit()
    conn.close()
    return db


def test_the_average_is_the_hand_computed_mean_of_the_successful_replies_and_uses_the_requests_rows(tmp_path):
    figs = speed_queries.overview_figures(_db(tmp_path), 1000.0, 0.0)
    d = base(figures=figs, providers=[{"id": "claude", "name": "Claude (Anthropic)", "short": "Claude", "state": "ok", "status": "Ready", "cloud": True, "where": "Cloud"}],
             uses={"provider:Claude": ["Memories"]})
    r = rows(d)["Claude (Anthropic)"]
    assert r["requests"] == "3" and r["avg"] == "1.5 s", "(1.0 + 2.0) / 2 successful; the failed 9.0 is left out, the count keeps all 3"


def test_under_a_second_keeps_one_decimal_and_no_history_is_a_dash_not_a_number():
    f = {**FIG, "rows": {("Local", "gemma"): {"n": 2, "ok": 2, "typical": 0.5, "tokensPerSecond": None, "avg": 0.44}}}
    assert rows(base(figures=f))["gemma-4-12b"]["avg"] == "0.4 s"
    assert rows(base(figures={**FIG, "rows": {}}))["gemma-4-12b"]["avg"] is None
    d = base(tracking=False, figures=None)
    assert d["cols"]["avg"] is False and all(r["avg"] is None for g in d["groups"] for r in g["rows"])


def _decision_figs(local, stepped, others=None, conf=None):
    """Figures as the saved rows give them: `local` rows of the local model (the ones a stronger AI took over included), `stepped` of them taken over, `others` {provider: answers}."""
    da = {"Local decision model": {"n": local, "sum": 0.4 * local}} if local else {}
    da.update({k: {"n": n, "sum": 0.5 * n} for k, n in (others or {}).items()})
    return {"decisionAnswers": da, "decisions": {"n": local, "own": local - stepped, "stepped": stepped, "avgConfidence": conf}}


def test_the_quick_decisions_line_shows_nine_of_ten_on_their_own_and_one_step_in(tmp_path):
    dec = [(900.0 + i, 0.95, 0, "local") for i in range(9)] + [(990.0, 0.4, 1, "TypeSafe")]
    figs = speed_queries.overview_figures(_db(tmp_path, dec), 1000.0, 0.0)
    assert figs["decisions"]["n"] == 10 and figs["decisions"]["own"] == 9 and figs["decisions"]["stepped"] == 1
    figs = {**figs, "decisionAnswers": {"Local decision model": {"n": 10, "sum": 4.0}, "TypeSafe": {"n": 1, "sum": 0.5}}}          # (the saved answers: ten of the local model's, one of them taken over by TypeSafe)
    row = rows(base(figures=figs, decisions={**DEC, "state": "ready", "label": "Running", "pc": ""}))["Quick decisions"]
    assert row["line"] is None and row["details"] == [["Total decisions", "10"], ["Answered locally", "9 (90%)"], ["Passed to another AI", "1 (10%)"], ["Certainty", "90%"]]


def test_the_four_details_lines_for_25_calls_and_they_add_up():
    cells = overview._decision_cells(_decision_figs(25, 10, {"TypeSafe": 10}, 0.55))
    assert cells == [["Total decisions", "25"], ["Answered locally", "15 (60%)"], ["Passed to another AI", "10 (40%)"], ["Certainty", "55%"]]
    # three that split unevenly: the shares still make 100
    c = overview._decision_cells(_decision_figs(3, 2, {"TypeSafe": 2}))
    assert c[1][1] == "1 (33%)" and c[2][1] == "2 (67%)" and c[3] == ["Certainty", "-"]
    # the stronger AI was asked but never answered: the local answer stood, so it is a local answer and is counted
    c = overview._decision_cells(_decision_figs(12, 4, {"TypeSafe": 4}, 0.8))
    assert c[0] == ["Total decisions", "12"] and c[1][1] == "8 (67%)" and c[2][1] == "4 (33%)"
    assert overview._decision_cells(_decision_figs(0, 0)) is None, "nothing answered by anyone: dashes, nothing invented"
    assert overview._decision_cells(None) is None


def test_no_decisions_leaves_the_card_empty_and_a_confidence_is_the_average_of_those_that_report_one(tmp_path):
    ready = {**DEC, "state": "ready", "label": "Running", "pc": ""}
    figs = speed_queries.overview_figures(_db(tmp_path), 1000.0, 0.0)
    assert figs["decisions"] is None and rows(base(figures=figs, decisions=ready))["Quick decisions"]["details"][0] == ["Total decisions", "2"]          # (the two saved answers of the local model; no decision records)
    assert rows(base(figures={**figs, "decisionAnswers": {}}, decisions=ready))["Quick decisions"]["details"] is None
    (tmp_path / "b").mkdir()
    figs = speed_queries.overview_figures(_db(tmp_path / "b", [(990.0, None, 0, "local"), (991.0, 0.9, 0, "local"), (992.0, 0.7, 0, "Google Decisions")]), 1000.0, 0.0)
    assert figs["decisions"]["avgConfidence"] == 0.8 and figs["decisions"]["direct"] == 1, "the local model's and Google Decisions' answers that report one, averaged; the one that reported none is left out"
    (tmp_path / "c").mkdir()
    assert speed_queries.overview_figures(_db(tmp_path / "c", [(990.0, None, 0, "local")]), 1000.0, 0.0)["decisions"]["avgConfidence"] is None
    assert overview._decision_cells(_decision_figs(2, 0))[-1] == ["Certainty", "-"]


def test_an_old_history_file_without_the_decision_table_still_reads(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE speed_history (model TEXT, provider TEXT, ts REAL, elapsed REAL, success INTEGER, first_token REAL, out_tokens INTEGER)")
    conn.commit()
    conn.close()
    assert speed_queries.overview_figures(db, 1000.0, 0.0)["decisions"] is None


def test_a_decision_is_saved_with_its_confidence_whether_a_stronger_ai_was_asked_and_who_answered(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    proxy_module._record_decision(0.62, True, "TypeSafe")
    proxy_module._record_decision(None, False, "local")
    with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
        got = [r[1:] for r in conn.execute("SELECT * FROM decision_log ORDER BY ts, rowid")]
    assert got == [(0.62, 1, "TypeSafe"), (None, 0, "local")]


@needs_node
def test_the_requests_and_average_columns_are_centred_and_the_decision_line_is_drawn():
    d = base()
    r = d["groups"][0]["rows"][0]
    r["avg"] = "1.9 s"
    d["cols"]["details"] = True
    r["details"] = [["Total decisions", "42"], ["Answered locally", "39 (93%)"], ["Passed to another AI", "3 (7%)"], ["Certainty", "48%"]]
    h = draw(d)
    assert '<th class="mid">Requests <br>(last hour)</th><th class="mid">Average time <br>(last hour)</th>' in h
    assert '<td class="mid" title="1.9 s">1.9 s</td>' in h and '<td class="mid" title="212">212</td>' in h
    assert '<th class="num">Speed</th>' in h, "the Speed column keeps its alignment"
    assert "<th>Details <br>(last hour)</th>" in h and h.count("ov-dec") >= 3
    for lab, num in (("Total decisions", "42"), ("Answered locally", "39 (93%)"), ("Passed to another AI", "3 (7%)"), ("Certainty", "48%")):
        assert '<span>' + lab + '</span><span>' + num + '</span>' in h, lab
    assert "On its own" not in h and "stronger AI stepped in" not in h and "Sure on average" not in h, "the old labels are gone"
    assert "Answered on its own" not in h and "decisions ·" not in h, "no run-on sentence"
    assert '<td><span class="soft">-</span></td>' in h, "other rows show a dash in the column"
    assert "text-align:center" in overview_ui.CSS
