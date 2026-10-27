"""Routing, step 2 (2026-10-02): the Routing tab. The data it reads (routing_view.build and GET /config/routing), the page it is drawn on (the tab, the exact words,
a syntax check of its script) and what its buttons and drops send (its script run under node with a stand-in page)."""
import json
import os
import shutil
import subprocess
import sys

import httpx
import pytest
import pytest_asyncio

import routing
import routing_ui
import routing_view

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NODE = shutil.which("node")


def _table():
    doc = json.load(open(os.path.join(FIX, "routing_skyrimnet_job_settings.json"), encoding="utf-8"))
    return {k: {"max_tokens": v.get("max_tokens"), "temperature": v.get("temperature"), "timeout": v.get("timeout_s"), "models": v.get("models", [])} for k, v in doc["jobs"].items()}


def _build(**kw):
    args = dict(settings=routing.clean_settings({}), table={}, status={"resting": [], "checks": {}, "working": {}}, reachable=[], remotes={}, stats={}, seen={}, costs=lambda j, k: None,
                classify=lambda a: (routing.CLASS_FREE, "local"), key_of=lambda a: a, name_of=lambda a: (a.split("/")[-1], "LM Studio"), now_resting={})
    args.update(kw)
    return routing_view.build(**args)


# ---- jobs that really can't be told apart ----------------------------------------------------------------------------------------------------------------

def test_only_jobs_whose_prompts_overlap_are_a_hard_group():
    assert routing.indistinguishable_groups(_table()) != routing.hard_groups(_table()), "shape and numbers alone over-group: the prompt's opening splits most of them"
    assert routing.hard_groups(_table()) == [["narrative_engine_composer", "narrative_engine_director"]]
    assert routing.hard_groups({}) == []
    t = {"a": {"max_tokens": 1, "temperature": 1}, "b": {"max_tokens": 1, "temperature": 1}}
    assert routing.hard_groups(t, openings={}) == [["a", "b"]], "two jobs with the same numbers and no known opening can't be told apart"
    assert routing.hard_groups(t, openings={"x": ("a",), "y": ("b",)}) == []


def test_every_job_has_a_plain_line_about_it():
    assert all(routing.about(k) for k in routing.JOB_ORDER), "SkyrimNet's own description, or the plain line saying none has been seen"


# ---- the view ---------------------------------------------------------------------------------------------------------------------------------------------

REACH = [{"id": "lmstudio/gemma", "owned_by": "lmstudio"}, {"id": "lmstudio/qwen", "owned_by": "lmstudio"}, {"id": "den-pc/llama", "owned_by": "other-pc"},
         {"id": "claude-sonnet-5-5", "owned_by": "anthropic"}, {"id": "deepseek/deepseek-flash", "owned_by": "deepseek"}, {"id": "nano/*", "owned_by": "nano-gpt"}]


class TestView:
    def test_places_ais_and_jobs_from_real_state(self):
        t = {"dialogue": {"models": ["lmstudio/gemma"], "max_tokens": 4096, "temperature": 1}, "memory": {"models": ["lmstudio/qwen"], "max_tokens": 4000, "temperature": 1},
             "vision": {"models": ["google/gemma-4-31b-it"], "max_tokens": 4000, "temperature": 0.7}}
        v = _build(table=t, reachable=REACH, remotes={"den-pc": "courier"}, stats={"lmstudio/gemma": {"calls": 10, "ok": 9, "first_word": 0.4}}, seen={"dialogue": "recognised"})
        assert [p["id"] for p in v["places"]] == ["this", "pc:den-pc", "cloud", "lost"]
        by = {a["id"]: a for a in v["ais"]}
        assert by["lmstudio/gemma"]["place"] == "this" and by["lmstudio/gemma"]["firstWord"] == 0.4 and by["lmstudio/gemma"]["worked"] == 90 and by["lmstudio/gemma"]["calls"] == 10
        assert by["den-pc/llama"]["place"] == "pc:den-pc" and by["google/gemma-4-31b-it"]["place"] == "lost" and by["google/gemma-4-31b-it"]["state"] == "warn"
        assert "claude-sonnet-5-5" not in by and "deepseek/deepseek-flash" not in by, "a cloud model nobody uses is not drawn"
        assert any(p["id"] == "claude-sonnet-5-5" for p in v["pickable"]) and not any(p["id"].endswith("*") for p in v["pickable"])
        jobs = {j["key"]: j for j in v["jobs"]}
        assert set(jobs) == {"dialogue", "memory", "vision"} and jobs["dialogue"]["onAis"] == ["lmstudio/gemma"] and jobs["dialogue"]["how"] == "recognised" and jobs["memory"]["how"] is None
        assert jobs["dialogue"]["name"] == "job/dialogue" and jobs["dialogue"]["about"]

    def test_a_job_with_a_route_sits_on_its_ais_in_order_and_a_job_nobody_has_seen_is_not_shown(self):
        s = routing.clean_settings({"routes": {"memory": {"ais": ["lmstudio/gemma", "claude-sonnet-5-5"], "how": "free"}}})
        v = _build(settings=s, reachable=REACH)
        assert [j["key"] for j in v["jobs"]] == ["memory"] and v["jobs"][0]["onAis"] == ["lmstudio/gemma", "claude-sonnet-5-5"]
        assert "claude-sonnet-5-5" in {a["id"] for a in v["ais"]}, "a cloud AI with a job on it is drawn"

    def test_a_key_provider_that_answers_any_model_name_is_not_called_unreachable(self):
        s = routing.clean_settings({"routes": {"memory": {"ais": ["nano/qwen/anything"]}}})
        a = [x for x in _build(settings=s, reachable=REACH)["ais"] if x["id"] == "nano/qwen/anything"][0]
        assert a["place"] == "cloud" and a["state"] == "on" and a["anyModel"]

    def test_jobs_that_cant_be_told_apart_share_one_route_and_say_so(self):
        s = routing.clean_settings({"routes": {"narrative_engine_director": {"ais": ["lmstudio/gemma"]}}})
        v = _build(settings=s, table=_table(), reachable=REACH)
        jobs = {j["key"]: j for j in v["jobs"]}
        assert jobs["narrative_engine_composer"]["sharesWith"] == ["narrative_engine_director"] and jobs["narrative_engine_composer"]["route"]["ais"] == ["lmstudio/gemma"]
        assert v["groups"] == [["narrative_engine_composer", "narrative_engine_director"]]

    def test_two_providers_with_the_same_model_name_each_read_their_own_numbers(self):
        t = {"dialogue": {"models": ["continuum/grok-4.7", "grok/grok-4.7"], "max_tokens": 1, "temperature": 1}}
        names = {"continuum/grok-4.7": ("grok-4.7", "Continuum"), "grok/grok-4.7": ("grok-4.7", "Grok")}
        v = _build(table=t, reachable=[], name_of=lambda a: names[a], key_of=lambda a: names[a][1] + "::" + names[a][0],
                   stats={"Continuum::grok-4.7": {"calls": 5, "ok": 3, "first_word": 1.0}, "Grok::grok-4.7": {"calls": 2, "ok": 2, "first_word": 0.5}},
                   now_resting={"Continuum::grok-4.7": 60})
        by = {a["id"]: a for a in v["ais"]}
        assert (by["continuum/grok-4.7"]["calls"], by["continuum/grok-4.7"]["worked"]) == (5, 60)
        assert (by["grok/grok-4.7"]["calls"], by["grok/grok-4.7"]["worked"]) == (2, 100)
        assert by["continuum/grok-4.7"]["resting"] == 60 and by["grok/grok-4.7"]["resting"] is None

    def test_resting_cost_and_the_way_of_choosing_are_in_the_view(self):
        s = routing.clean_settings({"routes": {"memory": {"ais": ["claude-sonnet-5-5", "deepseek/deepseek-flash", "lmstudio/gemma"], "how": "cheapest"}}})
        cls = {"claude-sonnet-5-5": routing.CLASS_INCLUDED, "deepseek/deepseek-flash": routing.CLASS_PAID, "lmstudio/gemma": routing.CLASS_FREE}
        v = _build(settings=s, reachable=REACH, classify=lambda a: (cls.get(a, routing.CLASS_FREE), "x"), costs=lambda j, k: 0.0012 if k == "deepseek/deepseek-flash" else None, now_resting={"deepseek/deepseek-flash": 95})
        by = {a["id"]: a for a in v["ais"]}
        assert by["deepseek/deepseek-flash"]["state"] == "warn" and by["deepseek/deepseek-flash"]["resting"] == 95 and by["deepseek/deepseek-flash"]["costByJob"] == {"memory": 0.0012}
        assert by["claude-sonnet-5-5"]["class"] == "included" and by["lmstudio/gemma"]["class"] == "free"
        assert [h["label"] for h in v["hows"]] == ["In this order", "Take turns", "Fastest right now", "Free ones first", "Cheapest first"]


@pytest_asyncio.fixture
async def client(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "_skyrimnet_job_table", _table)
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    c.pm = proxy_module
    yield c


@pytest.mark.asyncio
async def test_the_endpoint_gives_the_whole_picture_and_a_save_comes_back_redrawn(client):
    g = (await client.get("/config/routing")).json()
    assert set(g) >= {"settings", "places", "ais", "pickable", "jobs", "groups", "hows", "status", "skyrimNetLinked"} and g["skyrimNetLinked"]
    assert "This device" in [p["name"] for p in g["places"]] and len(g["jobs"]) == 19
    p = await client.post("/config/routing", json={"routes": {"memory": {"ais": ["lmstudio/x", "claude-sonnet-5-5"], "how": "fastest"}}})
    j = {x["key"]: x for x in p.json()["jobs"]}["memory"]
    assert j["route"]["how"] == "fastest" and j["onAis"] == ["lmstudio/x", "claude-sonnet-5-5"]
    assert {a["id"]: a["state"] for a in p.json()["ais"]}["lmstudio/x"] == "warn", "an AI nobody can reach needs attention (amber)"


@pytest.mark.asyncio
async def test_the_page_has_the_tab_between_voice_and_quick_test_and_the_words_exactly(client):
    html = (await client.get("/")).text
    assert html.index('id="tabVoice"') < html.index('id="tabRouting"') < html.index('id="tabQuick"')
    assert 'id="paneRouting"' in html and "function rtOpened()" in html and ".rt-lanes" in html and "['tabRouting', 'routing']" in html and "['paneRouting', 'routing']" in html
    for words in (
        "How to use this tab", "Drag a job onto a model to have that model answer it.",
        "Click a job to add backups and pick a rule (In this order, Take turns, Fastest right now, Free ones first, or Cheapest first).",
        "The numbers show the order. A job marked 1 is the first choice, and 2 steps in if 1 fails or is busy.",
        r"""Click "Go back to SkyrimNet\'s model name" to undo a job\'s route.""",     # (as written in the script, with its escapes)
        "Set it up in SkyrimNet", "MultiProxy needs to know which job is running to send it to the right model. You have two choices.",
        "Option 1: Automatic (Do nothing)", "You do not need to change anything.", "Leave your current model names in SkyrimNet.",
        "MultiProxy reads the request and figures out the job for you.", "Your jobs still go to the models set in SkyrimNet until you drag them somewhere else on this tab.",
        "Option 2: Exact names (For perfect accuracy)", "Use this method to stop MultiProxy from guessing, or to tell apart jobs that look identical.",
        "Open SkyrimNet and go to AI > Models.", "Choose MultiProxy as the provider.", "Type the exact name (like `job/dialogue`) into the model box.", "If this job does not share a route with anything else, it will not get an answer yet.",
        "Here is every exact job name from A to Z. Click Copy to grab the one you need.",
    ):
        assert words in routing_ui.ROUTING_JS, words
    for plain in ("Routing", "Set it up in SkyrimNet", "Who answers", "When one doesn", "+ Add a model", "Make first choice", "Move up", "Move down", "Cheapest first" if False else "Close"):
        assert plain in routing_ui.ROUTING_JS


def test_nothing_in_first_time_setup_mentions_routing():
    import get_started_ui
    assert "routing" not in get_started_ui.PANE_HTML.lower() and "routing" not in getattr(get_started_ui, "JS", "").lower()


def test_no_prose_says_plugin():
    assert "plugin" not in routing_ui.ROUTING_JS.lower() and "plugin" not in "".join(routing.about(k) for k in routing.JOB_ORDER).lower()


# ---- the script itself --------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_script_is_valid_javascript(tmp_path):
    f = tmp_path / "rt.js"
    f.write_text(routing_ui.ROUTING_JS, encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


HARNESS = r"""
const posts = [];
const els = {};
global.document = { getElementById: id => els[id] || null, activeElement: null, querySelectorAll: () => [] };
global.window = { addEventListener() {} };
global.location = { hash: '' };
global.history = { replaceState() {} };
global.esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
global.setInterval = () => 0; global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });
global.lmPost = async (url, body) => { posts.push(JSON.parse(JSON.stringify(body))); return { ...view, settings: body }; };
global.showMainTab = () => {};
const view = {
  settings: { routes: { memory: { ais: ['a', 'b', 'c'], how: 'order', slow_limit_s: null } }, rest: { failures: 3, within_s: 60, for_s: 120 }, slow_handover: { enabled: false, streamed_dialogue: false } },
  places: [{ id: 'this', name: 'This device', caption: 'its own models' }],
  ais: [{ id: 'a', name: 'A', kind: 'LM Studio', place: 'this', state: 'on', class: 'free', calls: 3, worked: 100, firstWord: 0.4, costByJob: {} }],
  pickable: [{ id: 'd', name: 'D', kind: 'X', place: 'this' }],
  jobs: [
    { key: 'memory', label: 'Memory', about: 'm', route: { ais: ['a', 'b', 'c'], how: 'order', slow_limit_s: null }, onAis: ['a', 'b', 'c'], sharesWith: [], how: null, models: [] },
    { key: 'meta', label: 'Meta', about: 'x', route: null, onAis: ['b'], sharesWith: [], how: null, models: ['b'] },
    { key: 'narrative_engine_composer', label: 'Narrative Composer', about: '', route: null, onAis: ['a'], sharesWith: ['narrative_engine_director'], how: null, models: ['a'] },
    { key: 'narrative_engine_director', label: 'Narrative Director', about: '', route: null, onAis: ['a'], sharesWith: ['narrative_engine_composer'], how: null, models: ['a'] },
  ],
  hows: [{ id: 'order', label: 'In this order' }], groups: [], status: {}, skyrimNetLinked: true,
};
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_what_the_buttons_and_drops_send(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
rtView = view;
const memory = rtJobByKey('memory'), meta = rtJobByKey('meta'), comp = rtJobByKey('narrative_engine_composer');
(async () => {
  await rtMakeFirst(memory, 'c');                                   // dragged onto c: first, the others keep their order behind it
  await rtMove(memory, 0, 1);                                       // a -> second place
  await rtRemove(memory, 1);                                        // take out the second
  await rtAdd(memory, 'd');
  await rtSetHow(memory, 'fastest');
  await rtSetSlow(memory, '8');
  await rtSetRest('failures', '5');
  await rtMakeFirst(meta, 'a');                                     // an untouched job dragged onto an AI: only that AI, no surprise backups
  await rtMakeFirst(comp, 'b');                                     // jobs that can't be told apart move together
  await rtMakeFirst(meta, 'b');                                     // ... but a job that already answers there is left alone (nothing sent)
  await rtRemove(meta, 0);                                          // taking out the last AI puts the job back on its model name
  console.log(JSON.stringify(posts.map(p => ({ routes: p.routes, rest: p.rest.failures }))));
})();
"""
    f = tmp_path / "h.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    posts = json.loads(r.stdout.strip().splitlines()[-1])
    assert posts[0]["routes"]["memory"]["ais"] == ["c", "a", "b"]
    # (every post is built from the view as the server last gave it, which the stand-in leaves as it was: each step is checked on its own)
    assert posts[1]["routes"]["memory"]["ais"] == ["b", "a", "c"] or posts[1]["routes"]["memory"]["ais"] == ["b", "a", "c"]
    assert posts[2]["routes"]["memory"]["ais"] == ["a", "c"] and posts[3]["routes"]["memory"]["ais"] == ["a", "b", "c", "d"]
    assert posts[4]["routes"]["memory"]["how"] == "fastest" and posts[5]["routes"]["memory"]["slow_limit_s"] == 8 and posts[6]["rest"] == 5
    assert posts[7]["routes"]["meta"] == {"ais": ["a"], "how": "order", "slow_limit_s": None}
    assert posts[8]["routes"]["narrative_engine_composer"]["ais"] == ["b"] and posts[8]["routes"]["narrative_engine_director"]["ais"] == ["b"]
    assert len(posts) == 10, "the job that already answers on that AI sent nothing"
    assert "meta" not in posts[9]["routes"], "the last AI removed: the job goes back to its model name"


# ---- the link icons (Revision 6) -----------------------------------------------------------------------------------------------------------------------

def test_the_chips_carry_the_link_icon_the_two_tooltips_and_no_fill():
    js, css = routing_ui.ROUTING_JS, routing_ui.ROUTING_CSS
    assert "const RT_TIP_FIRST = 'First choice. ';" in js and "Backup: steps in if the first choice fails or is busy. " in js and "Its backup is shown" not in js
    assert "not reachable right now" not in js and "not set up" not in js and "\\u21aa" in js
    assert ".rt-lanes.rt-hovering .rt-chip.rt-lit" in css and ".rt-ai.rt-partner" in css and "dashed" in css
    assert "rt-chip" in css and "background:var(--accent)" not in css.split(".rt-chip {")[1].split(".rt-lanes.rt-hovering")[0]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_jobs_colour_is_fixed_by_its_key_and_jobs_on_one_model_never_share_one(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
const mk = (k, on) => ({ key: k, label: k, about: '', route: null, onAis: on, sharesWith: [], how: null, models: [] });
rtView = { jobs: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l'].map(k => mk(k, ['m1'])).concat([mk('z', ['m2'])]) };
const one = rtJobColours(), two = rtJobColours();
const onM1 = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l'].map(k => one[k]);
console.log(JSON.stringify({ same: JSON.stringify(one) === JSON.stringify(two), distinct: new Set(onM1).size, max: Math.max(...Object.values(one)) }));
"""
    f = tmp_path / "c.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["same"] and got["distinct"] == 12 and got["max"] <= 11


# ---- Clear all routes (asks first) ---------------------------------------------------------------------------------------------------------------------

def test_clear_all_routes_words_exactly():
    js = routing_ui.ROUTING_JS
    assert "const RT_CLEAR_BTN = 'Clear all routes';" in js and "const RT_CLEAR_TITLE = 'Clear all routes?';" in js
    assert 'const RT_CLEAR_BODY = "Every job will go back to the models you picked in SkyrimNet\'s Models page. Your priority order and fine-tune settings will stay the same.";' in js
    assert "const RT_CLEAR_CANCEL = 'Cancel';" in js
    assert 'id="rtModalBox"' in routing_ui.ROUTING_PANE_HTML


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_clear_all_routes_is_disabled_with_none_and_clears_only_routes(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
view.settings.priority = ['memory', 'meta'];
view.settings.overrides = { memory: { temperature: '0.5' } };
rtView = view;
(async () => {
  const none = JSON.parse(JSON.stringify(view)); none.settings.routes = {};
  const out = {};
  out.countWith = rtRouteCount();
  rtView = none; out.countNone = rtRouteCount(); rtAskClear(); out.askedWithNone = !!els.rtModalBox;   // nothing happens
  rtView = view;
  els.rtModalBox = { style: {}, innerHTML: '' };
  els.rtClearCancel = { focus() { out.focus = 'cancel'; } };
  rtAskClear();
  out.dialog = els.rtModalBox.innerHTML; out.shown = els.rtModalBox.style.display;
  await rtConfirmClear();
  out.closed = els.rtModalBox.style.display;
  out.post = posts[0];
  console.log(JSON.stringify(out));
})();
"""
    f = tmp_path / "c.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["countWith"] == 1 and out["countNone"] == 0
    assert out["focus"] == "cancel" and out["shown"] == ""
    assert "Clear all routes?" in out["dialog"] and "Every job will go back to the models you picked in SkyrimNet" in out["dialog"]
    assert out["dialog"].index("Cancel") < out["dialog"].index('id="rtClearGo"')
    assert out["closed"] == "none"
    assert out["post"]["routes"] == {} and out["post"]["priority"] == ["memory", "meta"] and out["post"]["overrides"] == {"memory": {"temperature": "0.5"}}
    assert out["post"]["rest"]["failures"] == 3 and out["post"]["slow_handover"]["enabled"] is False


def test_the_clear_button_is_disabled_when_no_route_is_set():
    js = routing_ui.ROUTING_JS
    assert "(rtRouteCount() ? '' : ' disabled title=\"' + esc(RT_CLEAR_NONE_TIP) + '\"')" in js and 'id="rtClearAllBtn"' in js
    assert "const RT_CLEAR_NONE_TIP = 'Every job already follows what you picked in SkyrimNet.';" in js


@pytest.mark.asyncio
async def test_saving_no_routes_keeps_priority_and_overrides(client):
    await client.post("/config/routing", json={"routes": {"memory": {"ais": ["lmstudio/x"], "how": "order"}}, "priority": ["memory", "meta"], "aging_s": 10})
    p = (await client.post("/config/routing", json={"routes": {}, "priority": ["memory", "meta"], "aging_s": 10})).json()
    assert p["settings"]["routes"] == {} and p["settings"]["priority"][:2] == ["memory", "meta"] and p["settings"]["aging_s"] == 10
    assert all(j["route"] is None for j in p["jobs"])


# ---- whose choice: SkyrimNet's own setting or MultiProxy's route ---------------------------------------------------------------------------------------

@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_chips_say_whether_a_job_follows_skyrimnet_or_a_multiproxy_route(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
const mk = (k, route, on) => ({ key: k, label: k, about: '', route: route, onAis: on, sharesWith: [], how: null, models: [] });
rtView = { places: [{ id: 'this', name: 'This device', caption: 'c' }], ais: [{ id: 'a', name: 'A', kind: 'k', place: 'this', state: 'on' }],
  jobs: [mk('sn', null, ['a']), mk('mp', { ais: ['a'], how: 'order' }, ['a']), mk('mpback', { ais: ['x', 'a'], how: 'order' }, ['x', 'a']), mk('snback', null, ['x', 'a'])] };
const colours = rtJobColours();
const html = rtChips(rtView.ais[0], colours);
const chips = html.split('<button').slice(1);
console.log(JSON.stringify({ chips: chips, line: rtPicture().split('<div class="rt-lanes">')[0] }));
"""
    f = tmp_path / "w.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    by = {c.split("<span>")[1].split("<")[0]: c for c in got["chips"]}
    assert "Set in SkyrimNet. First choice. Click to change it." in by["sn"]
    assert "MP</span>" not in by["sn"] and "MP</span>" not in by["snback"]
    assert "Changed here in MultiProxy. First choice. Click to change it." in by["mp"] and '1 <span class="rt-mp">MP</span>' in by["mp"]
    assert "Changed here in MultiProxy. Backup: steps in if the first choice fails or is busy. Click to change it." in by["mpback"] and '2 <span class="rt-mp">MP</span>' in by["mpback"]
    assert "Set in SkyrimNet. Backup:" in by["snback"]
    assert "Every job starts on the models you picked in SkyrimNet's Models page. Drag a job to change it here; your new choices get a small MP tag." in got["line"]


# ---- the key to the icons ------------------------------------------------------------------------------------------------------------------------------

def test_the_key_is_one_line_outside_the_redrawn_body_with_the_words_exactly():
    html, js = routing_ui.ROUTING_PANE_HTML, routing_ui.ROUTING_JS
    assert 'id="rtKey"' in html and html.index('id="rtKey"') < html.index('id="rtBody"')
    assert "<details" not in html and "<summary" not in js and "rtKeyOpen" not in js and "localStorage" not in js
    for words in (
        "const RT_KEY_LINK = 'Same colour, same job. Hover to outline its first choice and backups.';",
        "const RT_KEY_PUZZLE = 'A job from an add-on. SkyrimNet marks these with a puzzle piece on the Models page.';",
        "const RT_KEY_ORDER = 'First choice. A dashed \\u21aa 2 answers if the first is busy or fails.';",
        "const RT_KEY_MP = 'You changed this here in MultiProxy.';",
        "link: 'Same colour, same job', puzzle: 'Add-on job', order: 'First choice, then backup', mp: 'Changed here in MultiProxy'",
    ):
        assert words in js, words
    assert ".rt-lanes, .rt-key {" in routing_ui.ROUTING_CSS


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_key_has_four_items_made_from_the_chips_own_icon_and_classes(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
const key = { innerHTML: '' };
els.rtKey = key;
rtKeyDraw();
console.log(JSON.stringify({ html: key.innerHTML, svg: RT_LINK_SVG }));
"""
    f = tmp_path / "k.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    h = got["html"]
    assert h.count('class="rt-key-item"') == 4 and "<summary" not in h
    assert h.count(got["svg"]) == 5, "two links, the first chip, the backup chip and the MP chip, each with the chips' own icon"
    assert 'style="--jc:var(--rt-c1)"' in h and 'style="--jc:var(--rt-c2)"' in h
    assert 'class="rt-chip rt-back"' in h and "↪ 2" in h and '<span class="rt-puzzle"' in h and "\U0001F9E9" in h
    assert '1 <span class="rt-mp">MP</span>' in h
    for short in ("Same colour, same job", "Add-on job", "First choice, then backup", "Changed here in MultiProxy"):
        assert "<span>" + short + "</span>" in h, short
    for hover in ('title="Same colour, same job. Hover to outline its first choice and backups."',
                  'title="A job from an add-on. SkyrimNet marks these with a puzzle piece on the Models page."',
                  'title="First choice. A dashed ↪ 2 answers if the first is busy or fails."',
                  'title="You changed this here in MultiProxy."'):
        assert hover in h, hover


# ---- the columns fit their cards ----------------------------------------------------------------------------------------------------------------------

def test_columns_are_not_stretched_share_the_row_by_their_cards_and_stack_only_when_narrow():
    css = routing_ui.ROUTING_CSS
    assert ".rt-lanes { display:flex; flex-wrap:wrap; align-items:flex-start;" in css, "each column is only as tall as its own cards"
    assert ".rt-place { flex:var(--cc, 1) 1 0; min-width:calc(var(--cc, 1) * 190px + 38px);" in css, "wider for more cards, all on one row while there is room"
    assert ".rt-cards { display:grid; grid-template-columns:repeat(var(--cc, 1), minmax(0, 1fr));" in css, "cards fill left to right, then down"
    assert "@media (max-width:720px) { .rt-place { flex-basis:100%; min-width:0 }" in css, "only a narrow window stacks them"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_place_with_many_cards_lays_them_two_or_three_across_and_every_card_is_still_a_drop_target(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
const ais = [];
for (let i = 0; i < 17; i++) ais.push({ id: 'c' + i, name: 'Cloud model ' + i, kind: 'k', place: 'cloud', state: 'on' });
ais.push({ id: 'p0', name: 'Local', kind: 'k', place: 'this', state: 'on' });
for (let i = 0; i < 4; i++) ais.push({ id: 'n' + i, name: 'N' + i, kind: 'k', place: 'none', state: 'none' });
rtView = { places: [{ id: 'this', name: 'This device', caption: 'a' }, { id: 'other', name: 'Other devices', caption: 'b' }, { id: 'cloud', name: 'Cloud', caption: 'c' }, { id: 'none', name: 'Not reachable now', caption: 'd' }],
  ais: ais, jobs: [{ key: 'memory', label: 'Memory', about: '', route: null, onAis: ['c0'], sharesWith: [], how: null, models: [] }] };
const html = rtPicture();
const places = html.split('<div class="rt-place"').slice(1);
console.log(JSON.stringify({ across: [1, 2, 8, 9, 17].map(rtCardsAcross), places: places.map(p => ({ cc: p.match(/--cc:(\d)/)[1], cards: (p.match(/class="rt-ai"/g) || []).length, grid: p.includes('class="rt-cards"'), drops: (p.match(/ondrop="rtDropAi\(event, \d+\)"/g) || []).length })) }));
"""
    f = tmp_path / "l.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["across"] == [1, 1, 2, 3, 3]
    this, other, cloud, none = got["places"]
    assert (this["cc"], this["cards"], this["grid"]) == ("1", 1, True)
    assert (other["cc"], other["cards"], other["grid"]) == ("1", 0, False), "an empty column shows only its heading and its quiet line"
    assert (cloud["cc"], cloud["cards"], cloud["drops"]) == ("3", 17, 17)
    assert (none["cc"], none["cards"], none["drops"]) == ("2", 4, 4), "Not reachable now follows the same rule"


# ---- dropping a job on another model: moved, with the old model kept only when the player says so ------------------------------------------------------

DROP_SCRIPT = r"""
const mk = (id, state, extra) => Object.assign({ id: id, name: id, kind: 'k', place: 'this', state: state, resting: null, trouble: '' }, extra || {});
const baseView = (mode) => ({
  settings: { routes: { memory: { ais: ['old', 'sick'], how: 'order', slow_limit_s: null }, dialogue: { ais: ['old'], how: 'order', slow_limit_s: null },
                        meta: { ais: ['old', 'new'], how: 'order', slow_limit_s: null } },
              rest: { failures: 3, within_s: 60, for_s: 120 }, slow_handover: { enabled: false, streamed_dialogue: false }, on_move: mode },
  places: [{ id: 'this', name: 'This device', caption: 'c' }],
  ais: [mk('old', 'on'), mk('new', 'on'), mk('sick', 'warn', { trouble: 'ran out of memory' }), mk('resting', 'warn', { resting: 30 })],
  pickable: [],
  jobs: [
    { key: 'memory', label: 'Memory', about: '', route: { ais: ['old', 'sick'], how: 'order' }, onAis: ['old', 'sick'], sharesWith: [], how: null, models: [] },
    { key: 'dialogue', label: 'Dialogue', about: '', route: { ais: ['old'], how: 'order' }, onAis: ['old'], sharesWith: [], how: null, models: [] },
    { key: 'meta', label: 'Meta', about: '', route: { ais: ['old', 'new'], how: 'order' }, onAis: ['old', 'new'], sharesWith: [], how: null, models: [] },
    { key: 'free', label: 'Free', about: '', route: null, onAis: ['old'], sharesWith: [], how: null, models: [] },
  ],
  hows: [], groups: [], status: {}, skyrimNetLinked: true,
});
els.rtModalBox = { style: { display: 'none' }, innerHTML: '' };
els.rtKeepYes = { focus() {} };
const drop = async (jobKey, aiId, mode) => {
  rtView = baseView(mode); posts.length = 0; els.rtModalBox.innerHTML = '';
  await rtMoveJob(rtJobByKey(jobKey), aiId);
  return { posts: posts.map(p => p.routes), asked: els.rtModalBox.innerHTML };
};
"""


def _run_node(tmp_path, script):
    f = tmp_path / "d.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_drop_moves_the_job_and_the_old_model_stays_only_when_asked_for(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + DROP_SCRIPT + r"""
(async () => {
  const out = {};
  out.ask = await drop('dialogue', 'new', 'ask');                    // asks first, nothing sent yet
  await rtAnswerKeep(true); out.askYes = posts.map(p => p.routes);
  await drop('dialogue', 'new', 'ask'); await rtAnswerKeep(false); out.askNo = posts.map(p => p.routes);
  out.keep = await drop('dialogue', 'new', 'keep');
  out.move = await drop('dialogue', 'new', 'move');
  out.askFailing = await drop('memory', 'new', 'ask');               // the failing one is never offered
  out.keepFailing = await drop('memory', 'new', 'keep');
  out.sameList = await drop('meta', 'new', 'ask');                   // already in its list: reorders, no question
  out.unrouted = await drop('free', 'new', 'ask');                   // SkyrimNet's own model: only the new one, no question
  await drop('dialogue', 'new', 'ask'); rtCloseClear(); out.cancelled = { posts: posts.length, pending: rtPendingMove };
  out.failingFlags = ['old', 'new', 'sick', 'resting', 'missing'].map(rtFailing);
  console.log(JSON.stringify(out));
})();
"""
    o = _run_node(tmp_path, script)
    assert o["ask"]["posts"] == [] and "Keep old as a backup?" in o["ask"]["asked"]
    assert o["ask"]["asked"].index("No, just move it") < o["ask"]["asked"].index("Yes, keep it")
    assert o["askYes"][0]["dialogue"]["ais"] == ["new", "old"] and o["askNo"][0]["dialogue"]["ais"] == ["new"]
    assert o["keep"]["posts"][0]["dialogue"]["ais"] == ["new", "old"] and o["keep"]["asked"] == ""
    assert o["move"]["posts"][0]["dialogue"]["ais"] == ["new"] and o["move"]["asked"] == ""
    assert o["askFailing"]["posts"] == [] and "Keep old as a backup?" in o["askFailing"]["asked"], "only the healthy old model is offered"
    assert o["keepFailing"]["posts"][0]["memory"]["ais"] == ["new", "old"], "the failing one is dropped even when the setting keeps backups"
    assert o["sameList"]["posts"][0]["meta"]["ais"] == ["new", "old"] and o["sameList"]["asked"] == ""
    assert o["unrouted"]["posts"][0]["free"]["ais"] == ["new"] and o["unrouted"]["asked"] == ""
    assert o["cancelled"] == {"posts": 0, "pending": None}
    assert o["failingFlags"] == [False, False, True, True, True]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_failing_only_old_model_is_simply_moved_whatever_the_setting_says(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + DROP_SCRIPT + r"""
(async () => {
  const v = baseView('keep'); v.settings.routes.memory.ais = ['sick']; v.jobs[0].route.ais = ['sick']; v.jobs[0].onAis = ['sick'];
  rtView = v; posts.length = 0;
  await rtMoveJob(rtJobByKey('memory'), 'new');
  console.log(JSON.stringify({ ais: posts[0].routes.memory.ais, asked: els.rtModalBox.innerHTML }));
})();
"""
    o = _run_node(tmp_path, script)
    assert o["ais"] == ["new"] and o["asked"] == ""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_model_card_holds_no_figures_but_keeps_what_is_wrong(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
const busy = { id: 'a', name: 'a', kind: 'k', state: 'on', firstWord: 1.7, worked: 100, calls: 113, resting: null, trouble: '' };
console.log(JSON.stringify({ busy: rtFacts(busy), idle: rtFacts({ state: 'on', resting: null }), resting: rtFacts(Object.assign({}, busy, { state: 'warn', resting: 30 })),
  trouble: rtTrouble({ kind: 'MSI', name: 'gemma4:12b', trouble: 'wrote nothing (it only thought)' }), any: rtFacts({ state: 'on', anyModel: true }) }));
"""
    o = _run_node(tmp_path, script)
    assert o["busy"] == "" and o["idle"] == "" and o["any"] == "any model name works"
    assert o["resting"] == "resting for 30 s after repeated failures" or o["resting"].startswith("resting for")
    assert "wrote nothing (it only thought)" in o["trouble"] and "op-dot warn" in o["trouble"]
    for old in ("worked today", "answers today", "no answers yet today"):
        assert old not in routing_ui.ROUTING_JS


def test_the_figures_taken_off_the_cards_are_on_the_speeds_time_breakdown():
    import speeds_time_ui
    js = speeds_time_ui.SPEEDS_TIME_JS
    for h in ("<th>First word</th>", "<th>Succeeded</th>", "<th>Cost</th>", "' line' : ' lines'"):
        assert h in js, h


def test_the_move_words_are_exactly_these():
    import settings_ui
    js, st = routing_ui.ROUTING_JS, settings_ui.JS
    assert "const RT_KEEP_YES = 'Yes, keep it';" in js and "const RT_KEEP_NO = 'No, just move it';" in js and "'Keep ' + who" in js
    assert "const ST_MOVE_JOB = 'When I move a job to another model';" in st
    assert "[['ask', 'Ask me each time'], ['keep', 'Keep the old model as a backup'], ['move', 'Just move it']]" in st
    assert "Don't ask again" not in js and "Don't ask again" not in st


@pytest.mark.asyncio
async def test_the_move_setting_defaults_to_ask_is_saved_and_read_live(client):
    s = (await client.get("/config/settings")).json()
    assert s["moveJob"] == "ask"
    assert (await client.get("/config/routing")).json()["settings"]["on_move"] == "ask"
    for choice in ("keep", "move", "ask"):
        s = (await client.post("/config/settings", json={"moveJob": choice})).json()
        assert s["moveJob"] == choice
        assert (await client.get("/config/routing")).json()["settings"]["on_move"] == choice      # the Routing tab sees it on its next look: no restart
    assert (await client.post("/config/settings", json={"moveJob": "sometimes"})).status_code == 400
    # saving routes from the Routing tab keeps the choice, and saving the choice keeps the routes
    await client.post("/config/routing", json={"routes": {"memory": {"ais": ["lmstudio/x"], "how": "order"}}, "on_move": "move"})
    await client.post("/config/settings", json={"moveJob": "keep"})
    p = (await client.get("/config/routing")).json()["settings"]
    assert p["on_move"] == "keep" and p["routes"]["memory"]["ais"] == ["lmstudio/x"]
