"""Overview quick stats (2026-10-05): success rate, backups, busiest moment, kind of job, dialogue speed, slowest request, voice and tokens.

Every figure is checked against a value worked out by hand from a small made-up history, a figure with nothing behind it must be absent (never a zero or a guess),
the numbers that Speeds also shows must match it for the same window, and what is recorded (in flight at start, who stood in, spoken lines) is saved off the request path.
"""
import itertools
import sqlite3
import time

import pytest

import overview
import overview_ui
import quick_stats
import request_facts
import speed_queries
import speed_store
from tests.test_overview import base, draw, needs_node, rows
from tests.test_voice_service import env, speak, wav  # noqa: F401  (the fixture and helpers that run a real spoken line)

NOW = 1000.0
COLS = ("model", "provider", "ts", "elapsed", "success", "first_token", "out_tokens", "out_chars", "fail_reason", "reply_repair",
        "job", "roles_json", "skipped", "stand_in", "in_flight", "tokens_in")
#        model          provider                ts     el    ok  out   job         roles  skipped                    stand_in  inflight tokens_in
HISTORY = [
    ("gpt",             "Claude",               900.0, 2.0,  1, 100, "dialogue", None, None,                        None,     1,  400),
    ("gpt",             "Claude",               910.0, 12.0, 1, 200, "dialogue", None, None,                        None,     4,  500),
    ("gpt",             "Claude",               920.0, 4.0,  1, 50,  "memory",   None, '[["Claude", "too slow"]]',  None,     2,  300),
    ("glm",             "GLM",                  930.0, 20.0, 0, None, "memory",  None, None,                        None,     6,  None),
    ("Laptop/qwen",     None,                   940.0, 1.0,  1, None, "vision",  None, None,                        "Laptop", 3,  None),
    ("local/decisions", "Local decision model", 950.0, 0.5,  1, None, None,      '["Decisions"]', None,             None,     None, None),
    ("x",               "Claude",               960.0, 6.0,  1, 10,  "combat",   None, None,                        None,     1,  20),
    ("x",               "Claude",               970.0, 3.0,  1, None, "other",   None, None,                        None,     1,  None),
]


_names = itertools.count()


def make_db(tmp_path, history=HISTORY, voice=()):
    db = str(tmp_path / f"h{next(_names)}.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE speed_history (" + ", ".join(f"{c} TEXT" if c in ("model", "provider", "fail_reason", "reply_repair", "job", "roles_json", "skipped", "stand_in") else f"{c} REAL" for c in COLS) + ")")
    for h in history:
        model, provider, ts, el, ok, out, job, roles, skipped, stand, infl, tin = h
        conn.execute("INSERT INTO speed_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (model, provider, ts, el, ok, None, out, None, None, None, job, roles, skipped, stand, infl, tin))
    conn.execute("CREATE TABLE voice_log (ts REAL NOT NULL, seconds REAL NOT NULL, ok INTEGER NOT NULL)")
    conn.executemany("INSERT INTO voice_log VALUES (?,?,?)", voice)
    conn.commit()
    conn.close()
    return db


def quick(tmp_path, **kw):
    return speed_queries.overview_figures(make_db(tmp_path, **kw), NOW, 0.0)


# ---- each of the eight, worked out by hand ----
def test_success_rate_is_seven_of_eight_and_failed_is_one(tmp_path):
    q = quick(tmp_path)["quick"]
    assert (q["n"], q["ok"], q["failed"]) == (8, 7, 1)
    tiles = {t["id"]: t for t in overview.quick_block(q, {})["tiles"]}
    assert tiles["success"]["value"] == "88%" and tiles["success"]["label"] == "answered, 1 failed" and tiles["success"]["level"] == "warn"


def test_backups_count_the_answers_that_a_backup_gave_from_the_routing_and_from_a_stand_in(tmp_path):
    q = quick(tmp_path)["quick"]
    assert q["backups"] == 2, "row 3 (routing passed over the first choice) and row 5 (another device's backup)"
    t = {t["id"]: t for t in overview.quick_block(q, {})["tiles"]}["backups"]
    assert t["value"] == "2" and t["level"] == "ok"


def test_none_in_the_hour_shows_zero_in_the_normal_colour(tmp_path):
    plain = [h for h in HISTORY if not h[8] and not h[9]]
    t = {t["id"]: t for t in overview.quick_block(quick(tmp_path, history=plain)["quick"], {})["tiles"]}["backups"]
    assert t["value"] == "0" and t["level"] == "ok"


def test_the_busiest_moment_is_the_most_in_flight_with_the_time_that_request_started(tmp_path):
    q = quick(tmp_path)["quick"]
    assert q["peak"] == {"n": 6, "at": 910.0}, "row 4: six at once, started at 930 - 20 s"
    t = {t["id"]: t for t in overview.quick_block(q, {})["tiles"]}["peak"]
    assert t["value"] == "6" and time.strftime("%H:%M", time.localtime(910.0)) in t["hover"]


def test_the_split_by_kind_has_each_kinds_count_and_average_of_the_answered_ones(tmp_path):
    k = {r["kind"]: r for r in overview.quick_block(quick(tmp_path)["quick"], {})["kinds"]}
    assert (k["Dialogue"]["requests"], k["Dialogue"]["avg"]) == ("2", "7.0 s"), "(2 + 12) / 2"
    assert (k["Decisions"]["requests"], k["Decisions"]["avg"]) == ("1", "0.5 s"), "the local decision model, and a row forced to the Decisions label"
    assert (k["Memories"]["requests"], k["Memories"]["avg"]) == ("2", "4.0 s"), "two asked, one failed: the average is the answered one"
    assert (k["Vision"]["requests"], k["Vision"]["avg"]) == ("1", "1.0 s")
    assert (k["Background"]["requests"], k["Background"]["avg"]) == ("1", "6.0 s"), "combat is background, as on Speeds"
    assert [r["kind"] for r in overview.quick_block(quick(tmp_path)["quick"], {})["kinds"]] == ["Dialogue", "Decisions", "Memories", "Background", "Vision"]


def test_a_job_that_is_not_recognised_is_in_no_kind_and_a_row_with_no_job_saved_neither(tmp_path):
    assert quick_stats.kind_of("other") is None and quick_stats.kind_of(None) is None
    assert quick_stats.kind_of("dialogue/combat") == "Dialogue" and quick_stats.kind_of("agencyengine_impulse") == "Background"
    assert sum(v["n"] for v in quick(tmp_path)["quick"]["kinds"].values()) == 7, "the 'other' row is not in the split"


def test_dialogue_within_ten_seconds_is_one_of_two(tmp_path):
    q = quick(tmp_path)["quick"]
    assert q["dialogue"] == {"n": 2, "within": 1, "limit": 10.0}
    t = {t["id"]: t for t in overview.quick_block(q, {})["tiles"]}["dialogue"]
    assert t["value"] == "50%" and t["label"] == "of dialogue lines within 10 s" and t["level"] == "warn"
    assert quick_stats.DIALOGUE_LIMIT_SECONDS == 10.0


def test_the_slowest_request_is_the_slowest_answered_one_and_names_the_provider_as_the_page_shows_it(tmp_path):
    q = quick(tmp_path)["quick"]
    assert q["slowest"]["seconds"] == 12.0, "the failed 20 s one is not a reply"
    t = {t["id"]: t for t in overview.quick_block(q, {"Claude": "Claude (Anthropic)"})["tiles"]}["slowest"]
    assert t["value"] == "12.0 s" and t["label"] == "Slowest: Claude (Anthropic)"


def test_tokens_are_the_sums_of_what_was_reported_and_an_ai_that_reported_none_shows_dashes(tmp_path):
    q = quick(tmp_path)["quick"]
    by = {t["name"]: t for t in q["tokens"]}
    assert (by["Claude"]["in"], by["Claude"]["out"]) == (1220, 360), "400+500+300+20 in, 100+200+50+10 out"
    assert (by["GLM"]["in"], by["GLM"]["out"]) == (None, None)
    block = overview.quick_block(q, {})
    assert {t["id"]: t["value"] for t in block["tiles"]}["tokens"] == "1.6k"
    assert {t["name"]: (t["in"], t["out"]) for t in block["tokens"]}["GLM"] == (None, None)


def test_voice_lines_and_their_average_come_from_the_saved_spoken_lines(tmp_path):
    figs = quick(tmp_path, voice=[(900.0, 1.0, 1), (910.0, 2.0, 1), (920.0, 9.0, 0), (-5000.0, 3.0, 1)])
    assert figs["voice"] == {"n": 3, "ok": 2, "avg": 1.5, "waiting": None, "speaking": None, "typical": None}, "the line from long before the hour is not counted; the failed one counts as asked, not in the average"
    voice = {"installed": True, "state": "ready", "label": "Running", "pc": ""}
    r = rows(base(figures=figs, voice=voice))["Voice"]
    assert r["requests"] == "3" and r["avg"] == "1.5 s" and r["failed"] == 1


def test_a_voice_row_with_nothing_saved_stays_dashes(tmp_path):
    voice = {"installed": True, "state": "ready", "label": "Running", "pc": ""}
    r = rows(base(figures={**quick(tmp_path), "voice": None}, voice=voice))["Voice"]
    assert r["requests"] is None and r["avg"] is None
    r = rows(base(figures=quick(tmp_path, voice=[]), voice=voice))["Voice"]
    assert r["requests"] == "0" and r["avg"] is None


# ---- nothing is drawn from nothing ----
def test_without_the_saved_facts_those_figures_are_absent_not_zero(tmp_path):
    old = [("gpt", "Claude", 900.0, 2.0, 1, None, None, None, None, None, None, None), ("gpt", "Claude", 910.0, 3.0, 0, None, None, None, None, None, None, None)]
    q = quick(tmp_path, history=old)["quick"]
    assert q["peak"] is None and q["dialogue"] is None and q["kinds"] == {} and q["backups"] == 0
    ids = [t["id"] for t in overview.quick_block(q, {})["tiles"]]
    assert ids == ["success", "backups", "slowest"], "no most-at-once, no dialogue share and no tokens tile when nothing behind them"
    assert not overview.quick_block(q, {})["tokens"] or all(t["in"] is None and t["out"] is None for t in overview.quick_block(q, {})["tokens"])


def test_no_requests_in_the_hour_means_no_quick_stats(tmp_path):
    figs = quick(tmp_path, history=[])
    assert figs["quick"] is None and overview.quick_block(figs["quick"], {}) is None
    d = base(figures=figs)
    assert d["quick"] is None


def test_an_old_history_file_with_only_the_first_columns_still_reads(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE speed_history (model TEXT, provider TEXT, ts REAL, elapsed REAL, success INTEGER, first_token REAL, out_tokens INTEGER)")
    conn.execute("INSERT INTO speed_history VALUES ('m', 'Claude', 990.0, 1.0, 1, NULL, 12)")
    conn.commit()
    conn.close()
    figs = speed_queries.overview_figures(db, NOW, 0.0)
    assert figs["voice"] is None and figs["quick"]["n"] == 1 and figs["quick"]["peak"] is None and figs["quick"]["tokens"] == [{"name": "Claude", "in": None, "out": 12}]


def test_the_tiles_never_number_more_than_six_and_a_normal_number_has_no_colour(tmp_path):
    tiles = overview.quick_block(quick(tmp_path)["quick"], {})["tiles"]
    assert len(tiles) == 6 and [t["id"] for t in tiles] == ["success", "backups", "peak", "dialogue", "slowest", "tokens"]
    assert all(t["level"] == "ok" for t in tiles if t["id"] in ("backups", "peak", "slowest", "tokens"))
    assert all(t["hover"] for t in tiles)


@pytest.mark.parametrize("share,level", [(0.95, "ok"), (0.9, "ok"), (0.89, "warn"), (0.5, "warn"), (0.49, "bad")])
def test_the_success_rate_is_amber_under_90_and_red_under_50(share, level):
    q = {"n": 100, "ok": int(share * 100), "failed": 100 - int(share * 100), "backups": 0, "peak": None, "kinds": {}, "dialogue": None, "slowest": None, "tokens": []}
    assert overview.quick_block(q, {})["tiles"][0]["level"] == level


def test_the_dialogue_share_is_amber_under_80_and_never_red():
    def level(within):
        q = {"n": 10, "ok": 10, "failed": 0, "backups": 0, "peak": None, "kinds": {}, "dialogue": {"n": 10, "within": within, "limit": 10.0}, "slowest": None, "tokens": []}
        return {t["id"]: t["level"] for t in overview.quick_block(q, {})["tiles"]}["dialogue"]
    assert (level(9), level(8), level(7), level(0)) == ("ok", "ok", "warn", "warn")


# ---- the same number as Speeds, for the same window ----
def test_success_rate_and_slowest_match_what_speeds_shows_for_the_same_hour(tmp_path):
    claude_only = [h for h in HISTORY if h[1] == "Claude" and h[7] is None][:3] + [("gpt", "Claude", 980.0, 30.0, 0, None, "memory", None, None, None, 1, None)]
    db = make_db(tmp_path, history=claude_only)
    q = speed_queries.overview_figures(db, NOW, 0.0)["quick"]
    card = speed_queries.models_summary(db, NOW - 3600.0, lambda pct: None)[0]
    assert round(100 * q["ok"] / q["n"]) == card["success_pct"] == 75
    assert q["slowest"]["seconds"] == card["v2"]["full"]["slowest"] == 12.0
    assert q["n"] == card["calls_total"] == speed_queries.overview_figures(db, NOW, 0.0)["calls"]


# ---- the page ----
@needs_node
def test_the_tiles_the_closed_section_and_the_links_are_drawn_and_a_quiet_hour_draws_nothing(tmp_path):
    d = base(figures=quick(tmp_path), skyrimnet={"connected": False})
    h = draw(d)
    for needle in ("88%", "answered, 1 failed", "Backups used", "Most at once", "of dialogue lines within 10 s", "Slowest: Claude", "Tokens used", "Last hour in more detail", "By kind of job",
                   "Tokens used</div>", "Dialogue", "Memories", "Background", "Vision"):
        assert needle in h, needle
    assert h.count("See details in Speeds.") == 1, "one link for the whole row, none under each tile or block"
    assert h.index("See details in Speeds.") < h.index("Backups used"), "at the top, not under the tiles"
    top = h[h.index('<div class="ov-top">'):h.index("</div></div>", h.index('<div class="ov-top">'))]
    assert "Waiting for SkyrimNet" in top and top.index("Waiting for SkyrimNet") < top.index("See details in Speeds.") and 'class="ov-sp"' in top, "on the SkyrimNet status line, at its right end"
    assert h[h.index("Backups used"):h.index("Last hour in more detail")].count("<a ") == 0, "nothing between the tiles and the closed section"
    assert '<details class="ov-idle" ontoggle="ovMoreToggle(this)">' in h and 'class="ov-idle" open' not in h, "closed by default"
    assert '<div class="n ov-n-warn">88%</div>' in h and '<div class="n">2</div>' in h, "only the figure that is wrong is coloured"
    quiet = draw(base(figures=quick(tmp_path, history=[])))
    assert "See details in Speeds." not in quiet and "Last hour in more detail" not in quiet


@needs_node
def test_the_section_opens_from_memory_and_remembers_its_own_state(tmp_path):
    d = base(figures=quick(tmp_path))
    assert '<details class="ov-idle" open ontoggle="ovMoreToggle(this)">' in draw(d, prelude="global.localStorage = { getItem: k => k === 'ovMoreOpen' ? '1' : '0', setItem() {} };\n")
    draw(d, prelude="global.saved = []; global.localStorage = { getItem: () => '0', setItem: (k, v) => saved.push([k, v]) };\n",
         after="ovMoreToggle({ open: true }); if (JSON.stringify(saved) !== JSON.stringify([['ovMoreOpen', '1']])) throw new Error('not saved');\n")


@needs_node
def test_a_row_says_how_many_failed_only_when_some_did(tmp_path):
    d = base(figures=quick(tmp_path))
    r = rows(d)
    assert r["Claude (Anthropic)"]["failed"] is None, "nothing failed there: nothing is said"
    d["groups"][1]["rows"][0]["failed"] = 2
    h = draw(d)
    assert '<span class="ov-msg soft" title="2 failed in the last hour">2 failed</span>' in h
    assert '<span class="ov-msg soft" title="1 failed in the last hour">1 failed</span>' in h, "GLM's one failed reply in the hour"
    assert h.count(" failed</span>") == 2, "only the two rows with a failure say so"


# ---- recording: cheap, off the request path ----
def test_in_flight_counts_a_request_at_its_start_and_forgets_it_when_it_ends():
    f = quick_stats.InFlight()
    assert [f.enter("a"), f.enter("b"), f.enter("c")] == [1, 2, 3]
    f.leave("b")
    f.leave("nobody")
    assert f.count() == 2 and f.enter("d") == 3


def test_a_request_that_never_ends_is_forgotten_after_a_while(monkeypatch):
    f = quick_stats.InFlight()
    f.enter("stuck")
    monkeypatch.setattr(quick_stats, "STALE_SECONDS", -1.0)
    assert f.enter("new") == 1


def test_a_request_is_saved_with_how_many_were_in_flight_and_who_it_stood_in_for(proxy_module):
    rec = request_facts.begin("r1", "/v1/chat/completions", "chat")
    rec.in_flight, rec.stand_in = 3, "Laptop"
    proxy_module._insert_speed_history_row("m", 1.0, True, None, provider="Claude", extra=proxy_module._routing_columns())
    proxy_module._insert_speed_history_row("m", 1.0, True, None, provider="Claude")
    speed_store.flush()
    with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
        got = list(conn.execute("SELECT in_flight, stand_in FROM speed_history ORDER BY ts, rowid"))
    assert got == [(3, "Laptop"), (None, None)]


def test_an_old_history_file_upgrades_in_place_with_the_new_columns_and_keeps_every_row(proxy_module, tmp_path, monkeypatch):
    old = str(tmp_path / "old.db")
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE speed_history (ts REAL NOT NULL, model TEXT NOT NULL, elapsed REAL NOT NULL, success INTEGER NOT NULL)")
    conn.execute("INSERT INTO speed_history VALUES (1.0, 'm', 2.0, 1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(proxy_module, "SPEED_HISTORY_DB", old)
    proxy_module._init_speed_history_db()
    with sqlite3.connect(old) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(speed_history)")}
        assert {"in_flight", "stand_in"} <= cols and c.execute("SELECT COUNT(*) FROM speed_history").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM voice_log").fetchone()[0] == 0


def test_the_streamed_answer_stops_counting_when_its_last_chunk_goes_out_and_a_plain_one_at_once(proxy_module):
    class Streamed:
        def __init__(self):
            async def body():
                yield b"a"
                yield b"b"
            self.body_iterator = body()

    import asyncio
    proxy_module._in_flight.enter("s1")
    result = Streamed()
    assert proxy_module._leave_when_sent(result, "s1") is True and proxy_module._in_flight.count() >= 1

    async def drain():
        return [c async for c in result.body_iterator]
    assert asyncio.run(drain()) == [b"a", b"b"] and "s1" not in proxy_module._in_flight._started
    assert proxy_module._leave_when_sent({"plain": True}, "s2") is False


def test_a_dashboard_test_does_not_count_as_load(proxy_module):
    class Req:
        url = type("U", (), {"path": "/x"})()
        headers = {"origin": "http://127.0.0.1:8000"}      # (a dashboard request: it must not mark SkyrimNet as connected)
    before = proxy_module._in_flight.count()
    proxy_module._begin_request(Req(), "t-test", "decision", count=False)
    assert proxy_module._in_flight.count() == before
    rec = proxy_module._begin_request(Req(), "t-real", "chat")
    assert rec.in_flight == before + 1
    proxy_module._in_flight.leave("t-real")


def test_a_spoken_line_is_queued_for_the_background_writer_and_never_written_on_the_spot(proxy_module, monkeypatch):
    queued = []
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    monkeypatch.setattr(proxy_module.speed_store, "enqueue_row", lambda *a, **k: queued.append((a, k)) or True)
    proxy_module._record_voice_line(1.25, True)
    assert len(queued) == 1 and queued[0][1] == {"table": "voice_log"} and queued[0][0][1] == ("ts", "seconds", "ok", "waited", "spoke", "audio", "chars") and queued[0][0][2][1:] == (1.25, 1, None, None, None, None)
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", False)
    proxy_module._record_voice_line(1.0, True)
    assert len(queued) == 1, "tracking off: nothing is saved"


def test_a_spoken_line_is_saved_with_its_seconds_and_whether_it_worked(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    proxy_module._record_voice_line(2.5, True)
    proxy_module._record_voice_line(0.5, False)
    speed_store.flush()
    with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
        assert [r[1:3] for r in conn.execute("SELECT * FROM voice_log ORDER BY ts, rowid")] == [(2.5, 1), (0.5, 0)]
    assert proxy_module.voice_handles.service.on_line is proxy_module._record_voice_line


@pytest.mark.asyncio
async def test_the_voice_service_tells_its_listener_each_real_line_but_not_a_health_check(env):  # noqa: F811
    seen = []
    env.service.on_line = lambda seconds, ok: seen.append((round(seconds, 1) >= 0, ok))
    audio, _ = await speak(env.client, "", "Cheers, sweetie.", wav())
    assert audio and seen == [(True, True)]
    await speak(env.client, "", "ping.")
    assert len(seen) == 1, "SkyrimNet's ping is answered without the engine: it is not a spoken line"


@pytest.mark.asyncio
async def test_a_listener_that_breaks_never_breaks_the_line(env):  # noqa: F811
    def boom(seconds, ok):
        raise RuntimeError("disk")
    env.service.on_line = boom
    audio, _ = await speak(env.client, "", "Still spoken.", wav())
    assert audio and audio[:4] == b"RIFF"


def test_the_tile_number_is_coloured_by_text_with_class_names_nothing_else_paints_as_a_box():
    css = overview_ui.CSS
    assert ".ov-tile .n.ov-n-warn { color:var(--warn-text) }" in css and ".ov-tile .n.ov-n-bad { color:var(--err-text) }" in css
    assert ".ov-tile .n.warn" not in css and ".ov-tile .n.bad" not in css
    import multiproxy
    page = open(multiproxy.__file__, encoding="utf-8").read()
    assert ".warn {{ background:var(--warn) }}" in page, "the page's own .warn fills a box: the reason the tile cannot use it"
    for name in ("ov-n-warn", "ov-n-bad"):
        assert page.count("." + name) == 0, "no other rule on the page uses these names"
        assert not any(("background" in line and name in line) for line in css.splitlines())


def test_the_link_is_right_aligned_keeps_the_line_height_and_needs_tiles():
    css = overview_ui.CSS
    assert ".ov-top .ov-sp { flex:0 0 auto; margin-left:auto" in css and "min-height:1.5em" in css
    assert "text-overflow:ellipsis" in css.split(".ov-top .ov-sn")[1].split("}")[0], "the status text shortens first"


@needs_node
def test_with_no_tiles_there_is_no_link_and_without_skyrimnet_status_the_link_still_has_its_line(tmp_path):
    assert "See details in Speeds." not in draw(base(figures=quick(tmp_path, history=[]), skyrimnet={"connected": True}))
    d = base(figures=quick(tmp_path))
    d["skyrimnet"] = None
    h = draw(d)
    assert h.count("See details in Speeds.") == 1 and '<div class="ov-top"><div class="ov-sn"></div><button class="ghost ov-ck" onclick="ovCheckRun()">Check everything</button><div class="ov-sp">' in h
