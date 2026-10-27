"""The Overview page (2026-10-05): overview.py builds the page's data from made-up inputs, overview_ui.py draws it (run under Node), and the real route answers.

Checked: each tile and row from fake data, a device that is offline gives an amber row and a Needs a look line (with the real fallback from the routes), nothing wrong
leaves no Needs a look at all, finished setup leaves no Setup box, a figure nothing measured is absent, no colour literals, and every dashboard script still parses.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

import local_models_ui
import overview
import overview_ui
from tests.sh_stub import SH_STUB

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

FIG = {"rows": {("Local", "gemma"): {"n": 212, "ok": 212, "typical": 1.2, "tokensPerSecond": 164.0},
                ("Claude", "sonnet"): {"n": 38, "ok": 38, "typical": 1.4, "tokensPerSecond": None},
                (None, "Laptop/qwen"): {"n": 64, "ok": 60, "typical": 2.0, "tokensPerSecond": None}},
       "perMinute": 12.4, "typical": 1.8, "failuresToday": 0, "calls": 314}


def base(**kw):
    inp = dict(tracking=True, figures=FIG, this_pc={"name": "msi", "host": "msi", "gpu": "RTX 5090", "usedMb": 9216, "totalMb": 32768},
               local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "running"}],
               local_speeds={"gemma": {"state": "ok", "tokensPerSecond": 164.2}},
               pcs=[{"id": "Laptop", "online": True, "gpu": "RTX 4060", "program": "LM Studio", "models": [{"id": "qwen", "name": "Qwen3-8B", "state": "running"}],
                     "problem": None, "coveredBy": None, "usedMb": 4096, "totalMb": 8192}],
               providers=[{"id": "claude", "name": "Claude (Anthropic)", "short": "Claude", "state": "ok", "status": "Ready", "cloud": True, "where": "Cloud"},
                          {"id": "typesafe", "name": "TypeSafe", "short": "TypeSafe", "state": "off", "status": "No key yet", "cloud": True, "where": "Cloud"},
                          {"id": "glm", "name": "GLM (z.ai)", "short": "GLM", "state": "off", "status": "No key yet", "cloud": True, "where": "Cloud"}],
               health={"Claude": {"level": "ok"}}, uses={"local:gemma": ["Dialogue"], "pc:Laptop/qwen": ["Memories"], "provider:Claude": ["Memories backup"]},
               routes=[{"job": "Memories", "ais": [{"key": "pc:Laptop/qwen", "pc": "Laptop", "name": "qwen"}, {"key": "provider:Claude", "pc": None, "name": "Claude"}]}],
               decisions=None, voice=None, setup={"done": True, "left": []})
    inp.update(kw)
    return overview.build(**inp)


def rows(d):
    return {r["name"]: r for g in d["groups"] for r in g["rows"]}


# ---- the data ----
def test_tiles_rows_and_machines_from_real_shaped_inputs():
    d = base()
    assert {t["id"]: t["value"] for t in d["tiles"]} == {"rate": "12 / min", "wait": "1.8 s", "tps": "164", "failed": "0", "pcs": "2 of 2"}
    assert [m["name"] for m in d["machines"]] == ["This device", "Laptop"]
    assert d["machines"][0]["memory"] == "9 of 32 GB" and d["machines"][0]["gpu"] == "RTX 5090" and "1 model" in d["machines"][0]["runs"]
    r = rows(d)
    assert r["gemma-4-12b"]["speed"] == "164 tokens/s" and r["gemma-4-12b"]["requests"] == "212" and r["gemma-4-12b"]["word"] == "Running"
    assert r["Qwen3-8B"]["where"] == "Laptop" and r["Qwen3-8B"]["speed"] == "2.0 s typical" and r["Qwen3-8B"]["does"] == "Memories"
    assert r["Claude (Anthropic)"]["word"] == "Ready" and r["Claude (Anthropic)"]["does"] == "Memories backup"
    assert [g["title"] for g in d["groups"]] == ["On your devices", "Online providers"]


def test_a_provider_never_set_up_and_not_used_has_no_row_but_one_a_route_names_does():
    r = rows(base())
    assert "GLM (z.ai)" not in r and "TypeSafe" not in r
    r = rows(base(uses={"provider:TypeSafe": ["Quick decisions backup"]}))
    assert r["TypeSafe"]["level"] == "off" and r["TypeSafe"]["word"] == "No key yet"


def test_nothing_wrong_means_no_needs_a_look_and_a_finished_setup_means_no_setup_box():
    d = base()
    assert d["attention"] == [] and d["setup"]["show"] is False


def test_an_offline_pc_is_amber_and_says_what_happens_meanwhile_from_the_routes():
    d = base(pcs=[{"id": "Laptop", "online": False, "gpu": "RTX 4060", "program": None, "models": [{"id": "qwen", "name": "Qwen3-8B", "state": "running"}],
                   "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}])
    assert d["machines"][1]["level"] == "warn" and d["machines"][1]["word"] == "Offline" and d["machines"][1]["memory"] is None
    assert rows(d)["Qwen3-8B"]["level"] == "warn"
    assert d["attention"][0]["name"] == "Laptop" and d["attention"][0]["line"] == "Claude covers Memories until it comes back."
    assert "ffline" not in d["attention"][0]["line"], "the status word is not repeated"
    assert {t["id"]: t["value"] for t in d["tiles"]}["pcs"] == "1 of 2"


def test_an_offline_pc_with_no_other_ai_in_the_route_says_so_and_with_no_route_says_nothing_uses_it():
    pc = [{"id": "Laptop", "online": False, "gpu": None, "program": None, "models": [], "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}]
    one = [{"job": "Memories", "ais": [{"key": "pc:Laptop/qwen", "pc": "Laptop", "name": "qwen"}]}]
    assert base(pcs=pc, routes=one)["attention"][0]["line"] == "Memories has no backup AI."
    assert base(pcs=pc, routes=[])["attention"][0]["line"] == ""
    pc[0]["coveredBy"] = "local/gemma"
    assert base(pcs=pc, routes=[])["attention"][0]["line"] == "", "a backup is only claimed when the device has models it would cover"
    pc[0]["models"] = [{"id": "qwen", "name": "Qwen3-8B", "state": "running"}]
    assert base(pcs=pc, routes=[])["attention"][0]["line"] == "local/gemma covers for it until it comes back."


def test_an_unmeasured_figure_is_absent_never_a_zero():
    d = base(tracking=False, figures=None)
    assert [t["id"] for t in d["tiles"]] == ["pcs"], "with the Speeds history off only the device count can be known"
    assert d["cols"]["speed"] is False and d["cols"]["requests"] is False
    assert all(r["requests"] is None for g in d["groups"] for r in g["rows"])
    d = base(figures={**FIG, "typical": None})
    assert "wait" not in [t["id"] for t in d["tiles"]]
    d = base(local_speeds={"gemma": {"state": "not_enough"}})
    assert "tps" not in [t["id"] for t in d["tiles"]] and rows(d)["gemma-4-12b"]["speed"] is None
    assert base(pcs=[])["tiles"][-1]["id"] == "failed", "no other device: no 'Devices online' tile"


def test_a_column_that_is_empty_for_most_rows_is_dropped():
    assert base()["cols"]["does"] is True
    assert base(uses={})["cols"]["does"] is False


def test_failing_often_and_a_stopped_or_failed_model_and_a_slow_or_erroring_provider():
    fail = {"rows": {("Local", "gemma"): {"n": 10, "ok": 5, "typical": 1.0, "tokensPerSecond": None}}, "perMinute": 1.0, "typical": 1.0, "failuresToday": 5, "calls": 10}
    d = base(figures=fail)
    assert rows(d)["gemma-4-12b"]["level"] == "warn" and any(a["name"] == "gemma-4-12b" for a in d["attention"])
    d = base(local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "failed"}])
    assert rows(d)["gemma-4-12b"]["level"] == "warn", "in no route: nothing depends on it"
    d = base(local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "stopped"}])
    assert rows(d)["gemma-4-12b"]["level"] == "off" and not [a for a in d["attention"] if a["name"] == "gemma-4-12b"], "stopped on purpose is not a problem"
    assert rows(base(health={"Claude": {"level": "warn"}}))["Claude (Anthropic)"]["word"] == "Slow"
    d = base(health={"Claude": {"level": "bad"}})
    assert rows(d)["Claude (Anthropic)"]["level"] == "warn", "the laptop model covers it"


def test_the_decision_model_and_its_fallback_line():
    dec = {"state": "died", "label": "Stopped unexpectedly", "pc": "", "installed": True, "fallback": "TypeSafe"}
    d = base(decisions=dec)
    assert rows(d)["Quick decisions"]["level"] == "warn" and rows(d)["Quick decisions"]["where"] == "This device"
    assert d["attention"][0]["line"] == "TypeSafe answers decisions until it comes back."
    assert "decision engine" in d["machines"][0]["runs"]
    assert "Quick decisions" not in rows(base(decisions={**dec, "installed": False}))


def test_setup_box_only_while_unfinished_and_steps_left_are_counted_in_words():
    d = base(setup={"done": False, "left": ["add a provider", "link your Skyrim folder"]})
    assert d["setup"] == {"show": True, "left": 2, "title": "Just two setup steps left.", "line": "Add a provider and link your Skyrim folder."}
    assert base(setup={"done": False, "left": []})["setup"]["show"] is False
    assert base(setup={"done": True, "left": ["add a provider"]})["setup"]["show"] is False


# ---- the page, drawn under Node ----
HARNESS = SH_STUB + """
global.document = { getElementById: id => null, activeElement: null, addEventListener() {} };
global.window = { getSelection: () => null };
global.showMainTab = () => {};
"""
_LM = local_models_ui.LOCAL_MODELS_JS
ESC = _LM[_LM.index("function esc(s) {"):_LM.index("async function lmPost")]


def draw(data: dict, prelude: str = "", after: str = "") -> str:
    src = HARNESS + prelude + ESC + "\n" + overview_ui.JS + "\n" + after + "console.log(JSON.stringify(ovHtml(" + json.dumps(data) + ")));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


@needs_node
def test_the_page_draws_every_tile_row_and_section():
    h = draw(base())
    for needle in ("12 / min", "Typical wait for a reply", "Machines", "This device", "RTX 5090", "9 of 32 GB", "Models and services", "On your devices", "Online providers", "gemma-4-12b",
                   "164 tokens/s", "Requests <br>(last hour)", "Running, in use or ready", "Slow or needs a look"):
        assert needle in h, needle
    assert "Needs a look" not in h and ">Setup<" not in h and "Continue setup" not in h, "hidden when there is nothing to say"
    assert "Everything is running" not in h


@needs_node
def test_the_page_shows_needs_a_look_and_setup_when_there_is_something_to_say():
    d = base(pcs=[{"id": "Laptop", "online": False, "gpu": None, "program": None, "models": [], "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}],
             setup={"done": False, "left": ["add a provider"]})
    h = draw(d)
    assert "Needs a look" in h and "<b>Laptop</b> <span class=\"soft\">Claude covers Memories until it comes back.</span>" in h
    assert "One setup step left." in h and "Continue setup" in h and "ovContinueSetup()" in h
    assert 'class="ov-dot warn"' in h


@needs_node
def test_the_page_escapes_what_the_server_sends_and_leaves_dropped_columns_out():
    d = base(uses={"local:gemma": ["Dialogue"]}, tracking=False, figures=None)
    d["groups"][0]["rows"][0]["name"] = "<img src=x onerror=alert(1)>"
    h = draw(d)
    assert "<img" not in h and "&lt;img" in h
    assert "Requests (last hour)" not in h and "<th class=\"num\">Speed</th>" not in h


@needs_node
def test_a_cut_off_status_name_and_cell_carry_their_full_text_as_the_hover():
    d = base()
    row = d["groups"][0]["rows"][0]
    row["word"] = "Installed, but not usable right now"
    row["line"] = "Using the backup for now"
    h = draw(d)
    assert '<td class="ov-st" title="Installed, but not usable right now">' in h
    assert 'title="' + row["name"] + ' - Using the backup for now"' in h
    assert re.search(r'<td class="soft" title="[^"]+">', h), "every cut-off text cell has a hover"
    assert "<td class=\"soft\">" not in h.replace('<td class="soft"><span class="soft">-</span>', "")


def test_no_colour_literals_icons_or_emoji_in_the_page_code():
    src = overview_ui.CSS + overview_ui.JS + overview_ui.PANE_HTML
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", src), "theme variables only"
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", src), "no icons or emoji"
    assert "max-width" not in overview_ui.CSS.replace("@media (max-width:760px)", ""), "text uses the card's full width"


# ---- the real route and the page ----
def test_the_page_opens_on_the_overview_tab(test_client):
    h = test_client.get("/").text
    assert 'class="tab active" id="tabOverview"' in h and h.index('id="tabOverview"') < h.index('id="tabSetup"')
    assert '<div id="paneOverview">' in h and '<div id="paneSetup" style="display:none">' in h
    assert "showMainTab('overview');" in h


def test_the_overview_route_answers_with_the_page_data(test_client, proxy_module):
    r = test_client.get("/stats/overview")
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {"tracking", "tiles", "machines", "groups", "attention", "setup"}
    assert d["machines"][0]["name"] == "This device"


# ---- add-on (2026-10-05): the Setup box on top, the real tab names ----
@needs_node
def test_the_setup_box_is_the_very_first_thing_while_unfinished_and_absent_once_done():
    h = draw(base(setup={"done": False, "left": ["add a provider"]}))
    assert h.startswith('<div class="ov-setup">') and h.index("Continue setup") < h.index("ov-tiles") < h.index("Machines")
    assert "ov-setup" not in draw(base(setup={"done": True, "left": []}))


def test_the_button_resumes_the_walk_through_and_a_new_install_opens_on_overview(test_client):
    assert "function ovContinueSetup() { if (typeof openGetStarted === 'function') openGetStarted(); }" in overview_ui.JS, "no restart flag: it resumes at the saved step"
    import get_started_ui
    assert "GET_STARTED_FIRST_RUN && !location.hash) openGetStarted" not in get_started_ui.JS, "nothing opens by itself on a first install"
    h = test_client.get("/").text
    assert 'class="tab active" id="tabOverview"' in h and "showMainTab('overview');" in h


def test_the_tab_row_is_the_real_one_and_every_row_click_goes_to_a_real_tab(test_client):
    h = test_client.get("/").text
    bar = h[h.index('<div class="tabs">'):h.index('<div id="paneOverview">')]
    assert re.findall(r"showMainTab\('(\w+)'\)\">([^<]+)<", bar) == [
        ("overview", "Overview"), ("setup", "Providers"), ("local", "Local device"), ("other", "Other devices"), ("voice", "Voice"), ("decisions", "Decisions"), ("models", "Models"), ("routing", "Routing"),
        ("quick", "Quick Test"), ("speeds", "Speeds"), ("help", "Help")]
    assert 'id="tabCog"' in bar
    tabs = {"local", "other", "setup", "voice", "decisions", "routing", "settings"}      # "settings" = the Settings page (ovGo opens its section), not a tab
    import inspect
    src = inspect.getsource(__import__("multiproxy")) + overview.__doc__
    assert set(re.findall(r'"to": "(\w+)"', open("overview.py", encoding="utf-8").read())) <= tabs


# ---- add-on (2026-10-05): everything an offline device hosts ----
def _pc(models=True):
    return [{"id": "msi", "online": False, "gpu": None, "program": None, "models": [{"id": "qwen", "name": "Qwen3-8B", "state": "running"}] if models else [],
             "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}]


VOICE = {"state": "unreachable", "label": "Not ready", "pc": "msi", "installed": True}
DEC = {"state": "unreachable", "label": "Not ready", "pc": "msi", "installed": True, "fallback": "TypeSafe"}
ROUTE = [{"job": "Memories", "ais": [{"key": "pc:msi/qwen", "pc": "msi", "name": "qwen"}, {"key": "provider:Claude", "pc": None, "name": "Claude"}]}]


def test_an_offline_pc_running_only_voice_names_voice_and_never_says_nothing_uses_it():
    d = base(pcs=_pc(False), routes=[], voice=VOICE)
    assert [a["line"] for a in d["attention"]] == ["The voice service is not answering right now."]
    assert "Nothing is set" not in json.dumps(d)
    row = rows(d)["Voice"]
    assert row["where"] == "msi" and row["level"] == "bad" and row["tab"] == {"to": "voice"}


def test_a_route_with_a_backup_voice_and_decisions_on_one_pc_are_all_mentioned_briefly():
    d = base(pcs=_pc(), routes=ROUTE, voice=VOICE, decisions=DEC)
    assert len(d["attention"]) == 1 and d["attention"][0]["name"] == "msi", "one line per problem device"
    assert d["attention"][0]["line"] == ("Claude covers Memories until it comes back. The voice service is not answering right now. "
                                         "TypeSafe answers decisions until it comes back.")


def test_an_offline_pc_running_nothing_shows_only_the_dot_and_its_name():
    d = base(pcs=_pc(False), routes=[])
    assert d["attention"] == [{"level": "warn", "name": "msi", "line": "", "tab": {"to": "other"}}]


@needs_node
def test_an_empty_line_draws_no_text_after_the_name():
    h = draw(base(pcs=_pc(False), routes=[]))
    assert '<b>msi</b></div>' in h and "Nothing is set" not in h


def test_voice_that_is_answering_is_green_and_not_a_problem():
    d = base(voice={"state": "ready", "label": "Running", "pc": "", "installed": True})
    assert rows(d)["Voice"]["level"] == "ok" and rows(d)["Voice"]["where"] == "This device" and d["attention"] == []
    assert "Voice" not in rows(base(voice={"state": "not_installed", "label": "", "pc": "", "installed": False}))


def test_no_pc_offline_means_no_section():
    assert base(voice={"state": "ready", "label": "Running", "pc": "", "installed": True}, decisions=None)["attention"] == []


# ---- add-on (2026-10-05): "In use" while requests go through an online provider ----
def _claude(d):
    return rows(d)["Claude (Anthropic)"]


def test_a_provider_with_a_reply_20_seconds_ago_is_in_use_and_5_minutes_ago_is_ready():
    assert _claude(base(figures={**FIG, "lastReplyAgo": {"Claude": 20.0}}))["word"] == "In use"
    assert _claude(base(figures={**FIG, "lastReplyAgo": {"Claude": 300.0}}))["word"] == "Ready"
    assert _claude(base())["word"] == "Ready", "no recent reply known"
    assert _claude(base(figures={**FIG, "lastReplyAgo": {"Claude": 20.0}}))["level"] == "ok"


def test_in_use_never_hides_a_problem_and_tracking_off_stays_ready():
    f = {**FIG, "lastReplyAgo": {"Claude": 5.0}}
    assert _claude(base(figures=f, health={"Claude": {"level": "bad"}}))["word"] == "Errors"
    assert _claude(base(figures=f, health={"Claude": {"level": "warn"}}))["word"] == "Slow"
    assert _claude(base(tracking=False, figures=None))["word"] == "Ready"


def test_local_models_stay_running_and_not_set_up_stays_grey():
    f = {**FIG, "lastReplyAgo": {"Local": 1.0, "TypeSafe": 1.0}}
    d = base(figures=f, uses={"provider:TypeSafe": ["Quick decisions backup"]})
    assert rows(d)["gemma-4-12b"]["word"] == "Running" and rows(d)["TypeSafe"]["level"] == "off"


def test_the_newest_reply_per_provider_comes_from_the_saved_replies(tmp_path):
    import sqlite3
    import speed_queries
    db = str(tmp_path / "h.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE speed_history (model TEXT, provider TEXT, ts REAL, elapsed REAL, success INTEGER, first_token REAL, out_tokens INTEGER)")
    conn.executemany("INSERT INTO speed_history VALUES (?,?,?,?,?,?,?)", [("m", "Claude", 980.0, 1.0, 1, 0.5, 10), ("m", "Claude", 990.0, 1.0, 1, 0.5, 10), ("n", "GLM", 700.0, 1.0, 1, None, None)])
    conn.commit()
    conn.close()
    out = speed_queries.overview_figures(db, 1000.0, 0.0)
    assert out["lastReplyAgo"] == {"Claude": 10.0, "GLM": 300.0}

# ---- add-on (2026-10-05): ready but unused providers sit in a closed section ----
IDLE_PROVIDERS = [{"id": pid, "name": nm, "short": nm, "state": "ok", "status": "Ready", "cloud": True, "where": "Cloud"} for pid, nm in (("glm", "GLM"), ("nano", "Nano-GPT"), ("openai", "OpenAI"))]
CLAUDE_P = {"id": "claude", "name": "Claude (Anthropic)", "short": "Claude", "state": "ok", "status": "Ready", "cloud": True, "where": "Cloud"}


def idle_page(**kw):
    return base(providers=[CLAUDE_P] + IDLE_PROVIDERS, **kw)


def test_used_recent_and_unhealthy_providers_stay_in_the_table_and_the_rest_go_to_the_closed_section():
    d = idle_page(figures={**FIG, "rows": {**FIG["rows"], ("OpenAI", "gpt"): {"n": 3, "ok": 3, "typical": 1.0, "tokensPerSecond": None}}},
                  health={"Claude": {"level": "ok"}, "GLM": {"level": "bad"}}, uses={"provider:Claude": ["Memories backup"]})
    main = [r["name"] for g in d["groups"] for r in g["rows"]]
    assert "Claude (Anthropic)" in main, "in a route"
    assert "OpenAI" in main, "asked in the last hour"
    assert "GLM" in main, "a problem is never hidden"
    assert [r["name"] for r in d["idle"]["rows"]] == ["Nano-GPT"] and d["idle"]["title"] == "Not in use (1 model on 1 device)"


def test_a_newly_routed_provider_moves_up_and_no_unused_provider_means_no_section():
    d = idle_page(uses={"provider:Claude": ["Memories backup"]})
    assert d["idle"]["title"] == "Not in use (3 models on 1 device)"
    d = idle_page(uses={"provider:Claude": ["Memories backup"], "provider:GLM": ["Dialogue"], "provider:Nano-GPT": ["Combat backup"], "provider:OpenAI": ["Vision"]})
    assert d["idle"] is None and "Not in use" not in json.dumps(d)


def test_a_provider_that_is_not_set_up_and_unused_is_not_listed_anywhere():
    d = base(providers=[{"id": "glm", "name": "GLM", "short": "GLM", "state": "off", "status": "No key yet", "cloud": True, "where": "Cloud"}])
    assert d["idle"] is None and "GLM" not in json.dumps(d["groups"])


@needs_node
def test_the_section_is_closed_by_default_opens_from_memory_and_a_failed_read_shows_it_closed():
    d = idle_page(uses={"provider:Claude": ["Memories backup"]})
    closed = draw(d)
    assert '<details class="ov-idle"' in closed and "Not in use (3 models on 1 device)" in closed and "Nano-GPT" in closed
    assert 'class="ov-idle" open' not in closed
    assert 'class="ov-idle" open' in draw(d, prelude="global.localStorage = { getItem: k => '1', setItem() {} };\n")
    broken = "global.localStorage = { getItem() { throw new Error('no'); }, setItem() { throw new Error('no'); } };\n"
    assert 'class="ov-idle" open' not in draw(d, prelude=broken) and 'class="ov-idle"' in draw(d, prelude=broken)
    after = "ovIdleToggle({ open: true }); if (JSON.stringify(saved) !== JSON.stringify([['ovIdleOpen', '1']])) throw new Error('not saved');\n"
    draw(d, prelude="global.saved = []; global.localStorage = { getItem: () => '0', setItem: (k, v) => saved.push([k, v]) };\n", after=after)


# ---- add-on (2026-10-05): red = nothing covers it, amber = something does ----
ONLY_GEMMA = [{"job": "Dialogue", "ais": [{"key": "local:gemma", "pc": None, "name": "gemma"}]}]
FAILED = [{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "failed"}]
BACKED = [{"job": "Dialogue", "ais": [{"key": "local:gemma", "pc": None, "name": "gemma"}, {"key": "provider:Claude", "pc": None, "name": "Claude"}]}]


def test_a_failed_model_on_an_online_pc_is_amber_with_or_without_a_backup_never_red():
    assert rows(base(local_models=FAILED, routes=BACKED))["gemma-4-12b"]["level"] == "warn"
    d = base(local_models=FAILED, routes=ONLY_GEMMA)
    assert rows(d)["gemma-4-12b"]["level"] == "warn" and d["attention"][0]["level"] == "warn"


def test_an_offline_pc_with_only_a_backed_up_model_is_amber_with_voice_red_and_with_an_unbacked_model_red():
    d = base(pcs=_pc(), routes=ROUTE, uses={"pc:msi/qwen": ["Memories"]})
    assert d["machines"][1]["level"] == "warn" and d["attention"][0]["level"] == "warn" and rows(d)["Qwen3-8B"]["level"] == "warn"
    d = base(pcs=_pc(), routes=ROUTE, voice=VOICE, uses={"pc:msi/qwen": ["Memories"]})
    assert d["machines"][1]["level"] == "bad" and d["attention"][0]["level"] == "bad", "mixed device: something on it has no cover"
    assert rows(d)["Qwen3-8B"]["level"] == "warn", "the model row itself is covered"
    alone = [{"job": "Memories", "ais": [{"key": "pc:msi/qwen", "pc": "msi", "name": "qwen"}]}]
    d = base(pcs=_pc(), routes=alone, uses={"pc:msi/qwen": ["Memories"]})
    assert d["machines"][1]["level"] == "bad" and rows(d)["Qwen3-8B"]["level"] == "bad"
    assert base(pcs=_pc(False), routes=[])["machines"][1]["level"] == "warn", "hosts nothing that matters"


def test_the_decision_model_is_amber_when_it_crashed_on_an_online_pc_and_red_only_on_an_offline_pc_with_no_stand_in():
    dec = {"state": "died", "label": "Stopped unexpectedly", "pc": "", "installed": True}
    assert rows(base(decisions={**dec, "fallback": "TypeSafe"}))["Quick decisions"]["level"] == "warn"
    assert rows(base(decisions={**dec, "fallback": None}))["Quick decisions"]["level"] == "warn", "crashed with its device up: needs a look, not an alarm"
    off = {"state": "unreachable", "label": "Not ready", "pc": "msi", "installed": True}
    assert rows(base(pcs=_pc(False), decisions={**off, "fallback": "TypeSafe"}))["Quick decisions"]["level"] == "warn"
    assert rows(base(pcs=_pc(False), decisions={**off, "fallback": None}))["Quick decisions"]["level"] == "bad"


def test_a_cloud_provider_is_never_red_even_when_it_is_the_only_one_for_a_job_and_needs_a_look_goes_red_first():
    only = [{"job": "Vision", "ais": [{"key": "provider:Claude", "pc": None, "name": "Claude"}]}]
    d = base(routes=only, health={"Claude": {"level": "bad"}})
    assert rows(d)["Claude (Anthropic)"]["level"] == "warn" and rows(d)["Claude (Anthropic)"]["word"] == "Errors" and d["attention"][0]["level"] == "warn"
    d2 = base(pcs=_pc(), routes=ROUTE, voice=VOICE, local_models=FAILED)
    assert d2["attention"][0]["level"] == "bad" and d2["attention"][0]["name"] == "msi" and [a["level"] for a in d2["attention"]][-1] == "warn"


# ---- add-on (2026-10-05): offline vs stopped, Start and Shut down ----
def _courier(online=True, state="available", kind="courier"):
    return [{"id": "Laptop", "kind": kind, "online": online, "gpu": None, "program": "LM Studio", "models": [{"id": "qwen", "name": "Qwen3-8B", "state": state, "program": "lmstudio"}],
             "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}]


def _act(d, name):
    return rows(d)[name]["action"]


def test_an_offline_pc_says_offline_and_its_services_not_ready_with_no_buttons():
    d = base(pcs=_courier(online=False), voice={"state": "unreachable", "label": "Laptop isn't answering", "pc": "Laptop", "installed": True})
    assert d["machines"][1]["word"] == "Offline"
    assert rows(d)["Qwen3-8B"]["word"] == "Not ready" and rows(d)["Voice"]["word"] == "Not ready"
    assert rows(d)["Qwen3-8B"]["action"] is None and rows(d)["Voice"]["action"] is None


def test_an_online_pc_with_a_stopped_model_says_stopped_and_offers_the_tabs_own_start():
    d = base(pcs=_courier(), routes=[])
    assert d["machines"][1]["word"] == "Online" and rows(d)["Qwen3-8B"]["word"] == "Stopped" and rows(d)["Qwen3-8B"]["level"] == "off", "stopped on purpose, no job uses it: grey"
    c = base(pcs=_courier())
    assert rows(c)["Qwen3-8B"]["level"] == "off" and not [a for a in c["attention"] if a["name"] == "Qwen3-8B"], "stopped, even with a job and a backup: grey, no line"
    a = _act(d, "Qwen3-8B")
    assert (a["kind"], a["label"], a["url"], a["body"]) == ("start", "Start", "/config/other-pcs/courier/action", {"id": "Laptop", "program": "lmstudio", "action": "load", "model": "qwen"})
    assert d["cols"]["actions"] is True


def test_a_running_model_offers_shut_down_and_a_pc_without_a_courier_gets_no_button():
    a = _act(base(pcs=_courier(state="running")), "Qwen3-8B")
    assert (a["kind"], a["label"], a["body"]["action"]) == ("stop", "Shut down", "unload")
    assert _act(base(pcs=_courier(kind="multiproxy")), "Qwen3-8B") is None, "only a Courier device can be told to load or unload"


def test_this_pcs_models_start_and_stop_through_the_local_models_routes_and_a_stopped_one_is_grey_unless_a_job_uses_it():
    stopped = [{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "stopped"}]
    d = base(local_models=stopped, routes=[])
    assert rows(d)["gemma-4-12b"]["level"] == "off" and d["attention"] == []
    assert _act(d, "gemma-4-12b") == {"kind": "start", "label": "Start", "url": "/config/local-models/start", "body": {"id": "gemma"}}
    assert _act(base(), "gemma-4-12b")["url"] == "/config/local-models/stop"
    for routes in (BACKED, ONLY_GEMMA):
        d = base(local_models=stopped, routes=routes)
        assert rows(d)["gemma-4-12b"]["level"] == "off" and d["attention"] == [], "stopped on purpose is grey whether covered or not"
    assert _act(base(local_models=[{**stopped[0], "state": "starting"}]), "gemma-4-12b") is None


def test_voice_and_decisions_use_their_own_tabs_calls_and_a_service_not_installed_has_no_row():
    v = {"state": "stopped", "label": "Stopped", "pc": "", "installed": True}
    assert _act(base(voice=v), "Voice") == {"kind": "start", "label": "Start", "url": "/config/voice/start", "body": {}}
    d = base(voice=v)
    assert rows(d)["Voice"]["level"] == "off" and d["attention"] == [], "stopped on purpose: grey, no line"
    crashed = base(voice={**v, "state": "died", "label": "Stopped unexpectedly"})
    assert rows(crashed)["Voice"]["level"] == "warn" and crashed["attention"][0]["name"] == "Voice", "crashed with its device up: amber with a line"
    r = base(voice={**v, "state": "ready", "pc": "Laptop"}, pcs=_courier())
    a = _act(r, "Voice")
    assert {k: a[k] for k in ("kind", "label", "url", "body")} == {"kind": "stop", "label": "Shut down", "url": "/config/voice/pc/stop", "body": {"id": "Laptop"}}
    dec = {"state": "stopped", "label": "Stopped", "pc": "", "installed": True, "fallback": "TypeSafe"}
    assert _act(base(decisions=dec), "Quick decisions") == {"kind": "start", "label": "Start", "url": "/config/decisions/start", "body": {"pc": "this"}}
    assert rows(base(decisions=dec))["Quick decisions"]["level"] == "off" and base(decisions=dec)["attention"] == []
    assert "Voice" not in rows(base(voice={**v, "installed": False, "state": "not_installed"}))
    assert _act(base(decisions={**dec, "installed": False, "state": "not_installed"}), "Quick decisions") is None if "Quick decisions" in rows(base(decisions={**dec, "installed": False})) else True


PEND_JS = """
global.fetch = async (url, o) => { calls.push([url, JSON.parse(o.body)]); if (failNext) return { ok: false, json: async () => ({ detail: 'Could not start it.', error: { message: 'Could not start it.' } }) }; return { ok: true, json: async () => (url === '/v1/chat/completions' ? { choices: [{ message: { content: 'hi' } }] } : {}) }; };
global.document.getElementById = id => id === 'ovBody' ? body : null;
"""


def run_page(data: dict, script: str) -> dict:
    src = (HARNESS + "const calls = []; let failNext = false; const body = { innerHTML: '', contains: () => false };\n" + PEND_JS + ESC + "\n" + overview_ui.JS +
           "\n(async () => { ovLast = " + json.dumps(data) + "; ovLoad = async () => {};\n" + script + "\nconsole.log(JSON.stringify({ html: ovHtml(ovLast), calls, body: body.innerHTML })); })();\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


@needs_node
def test_start_runs_the_same_call_shows_starting_then_a_failure_brings_the_button_back():
    d = base(pcs=_courier())
    ok = run_page(d, "ovHtml(ovLast); await ovDo(ovActs[ovActs.findIndex(r => r.key === 'pc:Laptop/qwen')], 'run', 'Starting\u2026');")
    assert ok["calls"] == [["/config/other-pcs/courier/action", {"id": "Laptop", "program": "lmstudio", "action": "load", "model": "qwen"}]]
    assert "Starting\u2026" in ok["html"] and ">Start<" not in ok["html"].split("Qwen3-8B")[1].split("</tr>")[0], "in progress: the word, no button"
    bad = run_page(d, "failNext = true; ovHtml(ovLast); await ovDo(ovActs[ovActs.findIndex(r => r.key === 'pc:Laptop/qwen')], 'run', 'Starting\u2026');")
    assert "Could not start it." in bad["html"] and ">Start<" in bad["html"], "the failure line shows and the button is back"


@needs_node
def test_shut_down_acts_at_once_with_no_question_and_start_is_back_when_it_has_stopped():
    d = base(pcs=_courier(state="running"))
    go = run_page(d, "ovHtml(ovLast); ovStop(ovActs.findIndex(r => r.key === 'pc:Laptop/qwen')); await new Promise(r => setTimeout(r, 20));")
    assert go["calls"][0][1]["action"] == "unload" and "cannot use it until you start it again" not in go["html"] and "Cancel" not in go["html"]
    assert "ovStop(" in run_page(d, "")["html"] and "ovAskStop" not in run_page(d, "")["html"]
    stopped = run_page(base(pcs=_courier(state="stopped")), "")
    assert any(">Start<" in row for row in stopped["html"].split("</tr>") if "Qwen3-8B" in row)


@needs_node
def test_shut_down_acts_at_once_on_every_kind_of_row():
    d = base(voice={"state": "ready", "label": "Running", "pc": "", "installed": True}, decisions={"state": "ready", "label": "Running", "pc": "", "installed": True, "fallback": "TypeSafe"})
    for key in ("voice", "decisions", "local:gemma"):
        r = run_page(d, "ovHtml(ovLast); ovStop(ovActs.findIndex(r => r.key === '" + key + "')); await new Promise(r => setTimeout(r, 20));")
        assert r["calls"] and "Shutting down\u2026" in r["html"] and "Cancel" not in r["html"], key


# ---- add-on (2026-10-05): Restart and Test ----
def test_restart_is_only_on_running_services_and_is_the_tabs_own_stop_then_start():
    calls = lambda a: [(c["url"], c["body"]) for c in a["restart"]["calls"]]
    d = base()
    assert calls(_act(d, "gemma-4-12b")) == [("/config/local-models/stop", {"id": "gemma"}), ("/config/local-models/start", {"id": "gemma"})]
    c = base(pcs=_courier(state="running"))
    assert [x[1]["action"] for x in calls(_act(c, "Qwen3-8B"))] == ["unload", "load"]
    v = base(voice={"state": "ready", "label": "Running", "pc": "Laptop", "installed": True}, pcs=_courier())
    assert [x[0] for x in calls(_act(v, "Voice"))] == ["/config/voice/pc/stop", "/config/voice/pc/start"]
    de = base(decisions={"state": "ready", "label": "Running", "pc": "", "installed": True, "fallback": "TypeSafe"})
    assert calls(_act(de, "Quick decisions")) == [("/config/decisions/stop", {"pc": "this"}), ("/config/decisions/start", {"pc": "this"})]
    stopped = [{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "stopped"}]
    assert "restart" not in _act(base(local_models=stopped), "gemma-4-12b"), "a stopped service is started, not restarted"


def test_the_in_use_flag_comes_from_the_newest_reply_and_voice_always_asks():
    recent = {**FIG, "rows": {**FIG["rows"], ("Local", "gemma"): {**FIG["rows"][("Local", "gemma")], "ago": 20.0}}}
    old = {**FIG, "rows": {**FIG["rows"], ("Local", "gemma"): {**FIG["rows"][("Local", "gemma")], "ago": 900.0}}}
    assert rows(base(figures=recent))["gemma-4-12b"]["inUse"] is True and rows(base(figures=old))["gemma-4-12b"]["inUse"] is False
    assert rows(base(voice={"state": "ready", "label": "Running", "pc": "", "installed": True}))["Voice"]["inUse"] is True


def test_test_is_on_running_models_and_set_up_providers_only_never_on_an_offline_pcs_service_or_voice():
    d = base(providers=[{**CLAUDE_P, "testModel": "claude-sonnet"}], pcs=_courier(state="running"), uses={"provider:Claude": ["Dialogue"], "pc:Laptop/qwen": ["Memories"]},
             voice={"state": "ready", "label": "Running", "pc": "", "installed": True})
    assert rows(d)["gemma-4-12b"]["test"] == {"model": "local/gemma"} and rows(d)["Qwen3-8B"]["test"] == {"model": "Laptop/qwen"}
    assert rows(d)["Claude (Anthropic)"]["test"] == {"model": "claude-sonnet"} and rows(d)["Voice"]["test"] is None
    assert rows(base(pcs=_courier(online=False)))["Qwen3-8B"]["test"] is None
    assert rows(base(local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "stopped"}]))["gemma-4-12b"]["test"] is None
    assert rows(base(providers=[{**CLAUDE_P, "state": "off"}], uses={"provider:Claude": ["Dialogue"]}))["Claude (Anthropic)"]["test"] is None
    assert d["cols"]["actions"] is True


@needs_node
def test_restart_never_asks_and_calls_stop_then_start_in_order_and_a_failure_brings_the_buttons_back():
    busy = base(); next(r for g in busy["groups"] for r in g["rows"] if r["name"] == "gemma-4-12b")["inUse"] = True
    i = "ovActs.findIndex(r => r.key === 'local:gemma')"
    ok = run_page(busy, "ovHtml(ovLast); await ovRestart(" + i + ");")
    assert "It will be unavailable for a moment" not in ok["html"]
    assert [c[0] for c in ok["calls"]] == ["/config/local-models/stop", "/config/local-models/start"]
    quiet = base(); j = "ovActs.findIndex(r => r.key === 'local:gemma')"
    r1 = run_page(quiet, "ovHtml(ovLast); ovRestart(" + j + ");")
    assert [c[0] for c in r1["calls"]][:1] == ["/config/local-models/stop"], "not in use: no question, it just restarts"
    assert "Restarting\u2026" in r1["html"] or "Restart" in r1["html"]
    bad = run_page(quiet, "failNext = true; ovHtml(ovLast); await ovRestart(ovActs.findIndex(r => r.key === 'local:gemma'));")
    assert "Could not start it." in bad["html"] and ">Restart<" in bad["html"] and ">Shut down<" in bad["html"]


@needs_node
def test_test_sends_quick_tests_own_request_shows_the_result_and_clears_it_and_never_touches_settings():
    d = base()
    ok = run_page(d, "ovHtml(ovLast); await ovTest(ovActs.findIndex(r => r.key === 'local:gemma'));")
    url, body = ok["calls"][0]
    assert url == "/v1/chat/completions" and body["model"] == "local/gemma" and body["max_tokens"] == 16 and len(body["messages"]) == 1
    assert "Answered in " in ok["html"] and len(ok["calls"]) == 1, "one request, nothing saved"
    cleared = run_page(d, "ovHtml(ovLast); await ovTest(ovActs.findIndex(r => r.key === 'local:gemma')); delete ovTestMsg['local:gemma'];")
    assert "Answered in" not in cleared["html"]
    bad = run_page(d, "failNext = true; ovHtml(ovLast); await ovTest(ovActs.findIndex(r => r.key === 'local:gemma'));")
    assert "No answer: Could not start it" in bad["html"]
    one = run_page(d, "ovHtml(ovLast); ovTesting = true; ovHtml(ovLast);")
    assert 'class="ghost" disabled' in one["html"], "disabled while another test runs"



# ---- add-on (2026-10-05): grey when stopped on purpose, red only for an offline device with nothing covering it ----
def test_red_appears_only_on_an_offline_device_with_something_uncovered():
    online_dead = base(voice={"state": "died", "label": "Stopped unexpectedly", "pc": "", "installed": True}, local_models=FAILED, routes=ONLY_GEMMA,
                       decisions={"state": "error", "label": "Problem", "pc": "", "installed": True, "fallback": None})
    levels = [r["level"] for g in online_dead["groups"] for r in g["rows"]] + [m["level"] for m in online_dead["machines"]] + [a["level"] for a in online_dead["attention"]]
    assert "bad" not in levels, "nothing on an online device is ever red"
    assert base(pcs=_pc(), routes=ROUTE, voice=VOICE)["machines"][1]["level"] == "bad"
    assert base(pcs=_pc(), routes=ROUTE)["machines"][1]["level"] == "warn", "offline but everything on it is covered"
    assert base(pcs=_pc(False), routes=[])["machines"][1]["level"] == "warn", "offline and hosts nothing that matters"


def test_a_plain_stop_on_an_online_pc_never_adds_a_machine_alarm_or_a_needs_a_look_line():
    v = {"state": "stopped", "label": "Stopped", "pc": "Laptop", "installed": True}
    d = base(pcs=_courier(), voice=v, routes=[])
    assert d["machines"][1]["level"] == "ok" and rows(d)["Voice"]["level"] == "off" and d["attention"] == []


def test_the_machines_dot_and_its_needs_a_look_dot_are_one_value():
    d = base(pcs=_pc(), routes=ROUTE, voice=VOICE)
    assert d["machines"][1]["level"] == d["attention"][0]["level"] == "bad"


@needs_node
def test_result_and_failure_sit_in_the_result_cell_cut_to_one_line_with_the_full_text_in_the_title():
    d = base()
    ok = run_page(d, "ovHtml(ovLast); await ovTest(ovActs.findIndex(r => r.key === 'local:gemma'));")
    assert 'class="ov-msg ov-pass" title="Answered in ' in ok["html"] and "<div class=\"ov-pass\"" not in ok["html"]
    bad = run_page(d, "failNext = true; ovHtml(ovLast); await ovTest(ovActs.findIndex(r => r.key === 'local:gemma'));")
    assert 'class="ov-msg ov-fail" title="No answer: Could not start it' in bad["html"]
    cell = [c for c in bad["html"].split('<td class="ov-res">') if "ov-msg" in c][0].split("</td>")[0]
    assert "<div" not in cell and "ov-msg" in cell, "no extra block under the status"
    assert "ov-msg" not in bad["html"].split('<td class="ov-st" title="')[1].split("</td>")[0], "the Status cell holds only the dot and the word"
    assert "scrollbar-gutter:stable" in open("multiproxy.py", encoding="utf-8").read()




# ---- a fixed Result column (2026-10-05): no column moves whatever the message says ----
LONG = 'No answer: Ollama: "clef-flash:latest" does not support chat ' + "and then some more words " * 8
SET_MSGS = ("ovFailState['local:gemma'] = 'running'; ovFail['local:gemma'] = " + json.dumps(LONG) + "; ovTestMsg['provider:Claude'] = { ok: true, text: 'Testing\u2026' };\n")


def _table(h):
    i = h.index('<table class="ov-table ov-fixed">')
    return h[i:h.index("</table>", i)]


@needs_node
def test_the_columns_are_the_same_with_and_without_a_long_message_and_the_message_is_only_in_the_result_cell():
    d = base()
    plain = _table(draw(d))
    full = draw(d, after=SET_MSGS)
    msg = _table(full)
    head = lambda t: t[:t.index("</tr>")]
    assert head(plain) == head(msg), "same colgroup and header cells: the widths come from these, never from the content"
    assert head(plain).index("Average time") < head(plain).index('<th class="ov-res">Result</th>')
    assert all("style" not in c for c in re.findall(r"<col[^>]*>", head(plain)))
    assert "does not support chat" not in "".join(c.split("</td>")[0] for c in msg.split('<td class="ov-st" title="')[1:]), "never in the Status cell"
    assert msg.count('<td class="ov-res">') == plain.count('<td class="ov-res">') > 0, "the cell is always there"
    assert 'title="' + LONG.replace('"', "&quot;") + '"' in msg, "the full text is in the hover title"
    assert "Testing" in msg and "text-overflow:ellipsis" in overview_ui.CSS and "table-layout:fixed" in overview_ui.CSS


@needs_node
def test_status_has_the_dot_and_the_word_only_and_result_stays_with_the_buttons_when_tracking_is_off():
    assert re.search(r'<td class="ov-st" title="[A-Za-z ]+"><span class="ov-dot [a-z]+"></span>[A-Za-z ]+</td>', draw(base()))
    off = draw(base(tracking=False, figures=None))
    assert '<th class="ov-res">Result</th>' in off and "Requests (last hour)" not in off


@needs_node
def test_headers_break_at_the_bracket_and_the_result_column_keeps_a_gap():
    h = _table(draw(base()))
    head = h[:h.index("</tr>")]
    assert "<th class=\"mid\">Requests <br>(last hour)</th>" in head and "<th class=\"mid\">Average time <br>(last hour)</th>" in head
    css = overview_ui.CSS
    assert re.search(r"\.ov-fixed th \{[^}]*height:3rem", css), "one fixed header height that holds two lines"
    assert re.search(r"th\.ov-res \{ padding-left:1rem", css), "a clear gap before Result"
    assert all("style" not in c for c in re.findall(r"<col[^>]*>", head))


# ---- 2026-10-06: the Skyrim folder is silent while found, one warning (to Settings) when lost ----
def test_the_skyrim_folder_is_silent_when_found_and_one_warning_to_settings_when_lost():
    assert base()["attention"] == [] and base(skyrim_folder_lost=False)["attention"] == []
    a = base(skyrim_folder_lost=True)["attention"]
    assert [(x["level"], x["name"], x["tab"]) for x in a] == [("warn", "Skyrim folder", {"to": "settings", "section": "folders"})]


def test_the_overview_route_warns_only_when_the_skyrim_folder_is_not_found(test_client, proxy_module, tmp_path):
    from unittest.mock import patch
    import poll_cache
    def names():
        poll_cache.forget()      # the page answer is shared for a few seconds
        return [x["name"] for x in test_client.get("/stats/overview").json()["attention"]]
    with patch.object(proxy_module, "skyrim_install_path", None):
        assert "Skyrim folder" in names()
    with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)), patch.object(proxy_module, "_resolve_skyrimnet_dir_cached", lambda p: None):
        assert "Skyrim folder" in names()
    with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)), patch.object(proxy_module, "_resolve_skyrimnet_dir_cached", lambda p: str(tmp_path)):
        assert "Skyrim folder" not in names()


def test_the_speeds_page_has_no_linked_to_your_skyrim_install_line(test_client):
    h = test_client.get("/").text
    assert "Linked to your Skyrim install" not in h
    assert "linkedPath" not in h and "skyrimnetNotFoundWarn" in h and "openSettings('folders')" in h


def test_a_stopped_local_model_the_player_chose_first_is_a_warning_with_a_start_and_the_plain_line():
    dec = {"state": "stopped", "label": "Stopped", "pc": "", "installed": True, "fallback": "Nano-GPT", "localFirst": True}
    d = base(decisions=dec)
    r = rows(d)["Quick decisions"]
    assert r["level"] == "warn" and r["word"] == "Stopped" and r["action"], "amber, and a Start button"
    assert d["attention"][0]["line"] == "The local decision model is off, so decisions are going to Nano-GPT. Press Start."
    plain = base(decisions={**dec, "localFirst": False})
    assert rows(plain)["Quick decisions"]["level"] == "off" and not [a for a in plain["attention"] if "decision" in a["line"].lower()], "stopped on purpose with another AI chosen first: grey, nothing said"
    none = base(decisions={**dec, "fallback": None})
    assert none["attention"][0]["line"] == "The local decision model is off, so decisions are not being answered. Press Start."


# ---- 2026-10-07: only the models and services in use are listed; the rest sit in one closed "Not in use" section ----
JUNK = [{"id": "asr", "name": "Qwen3 Asr", "modelName": "qwen3-asr", "state": "stopped"}, {"id": "g4", "name": "Gemma 4 E4B", "modelName": "gemma-4-e4b", "state": "stopped"}]
MANY = [{"id": f"c{i}", "name": f"Claude M{i}", "state": "running", "program": "claude"} for i in range(19)]


def _many(**kw):
    pcs = [{"id": "Laptop", "online": True, "gpu": None, "program": "LM Studio", "models": [{"id": "qwen", "name": "Qwen3-8B", "state": "running"}] + MANY,
            "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}]
    return base(local_models=base_local() + JUNK, pcs=pcs, **kw)


def base_local():
    return [{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "running"}]


def test_a_routed_model_shows_and_an_unrouted_stopped_one_does_not():
    d = _many()
    r, idle = rows(d), {x["name"] for x in d["idle"]["rows"]}
    assert "gemma-4-12b" in r and "Qwen3-8B" in r
    assert "Qwen3 Asr" not in r and "Gemma 4 E4B" not in r and "Claude M0" not in r
    assert {"Qwen3 Asr", "Gemma 4 E4B", "Claude M0", "Claude M18"} <= idle


def test_the_not_in_use_line_counts_the_rest():
    d = _many()
    assert d["idle"]["title"] == "Not in use (21 models on 2 devices)"
    assert d["idle"]["rows"][0]["action"] is not None or d["idle"]["rows"][0]["test"] is not None, "a person can still Start or Test one"


def test_a_routed_model_that_is_stopped_or_failing_still_shows_as_a_warning():
    d = base(local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "failed"}], routes=ONLY_GEMMA)
    assert rows(d)["gemma-4-12b"]["level"] == "warn" and d["attention"] and d["idle"] is None
    d = base(local_models=[{"id": "gemma", "name": "gemma-4-12b", "modelName": "local/gemma", "state": "stopped"}])
    assert rows(d)["gemma-4-12b"]["action"]["kind"] == "start"


def test_a_model_with_requests_in_the_last_hour_shows_even_when_nothing_routes_to_it():
    d = _many(uses={})
    assert "gemma-4-12b" in rows(d) and "Qwen3-8B" in rows(d), "both answered in the last hour"
    d = _many(uses={}, figures={**FIG, "rows": {}})
    assert "gemma-4-12b" not in rows(d) and "Qwen3-8B" not in rows(d)
    d = _many(uses={}, figures={**FIG, "rows": {("Local", "qwen3-asr"): {"n": 2, "ok": 0, "typical": None, "tokensPerSecond": None, "avg": None}}})
    assert rows(d)["Qwen3 Asr"]["failed"] == 2


def test_an_unrouted_crashed_model_is_not_a_needs_a_look_line_but_the_installed_voice_and_decision_model_always_show():
    d = base(local_models=[{"id": "x", "name": "Gemma 4 26B A4B", "modelName": "x", "state": "failed"}])
    assert not any(a["name"] == "Gemma 4 26B A4B" for a in d["attention"])
    d = base(voice={"state": "stopped", "label": "", "pc": "", "installed": True}, decisions={"state": "stopped", "label": "", "pc": "", "installed": True, "fallback": None}, uses={})
    assert {"Voice", "Quick decisions"} <= set(rows(d))


@needs_node
def test_the_not_in_use_section_is_drawn_closed_with_its_rows():
    h = draw(_many())
    assert '<details class="ov-idle"' in h and "Not in use (21 models on 2 devices)" in h and "Claude M5" in h
