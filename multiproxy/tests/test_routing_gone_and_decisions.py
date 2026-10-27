"""Routing says the real reason when a model in a route is gone, and shows where Decisions really run (2026-10-26)."""
import json
import shutil
import subprocess

import httpx
import pytest
import pytest_asyncio

import routing
import routing_ui
import routing_view

NODE = shutil.which("node")


def _build(reachable, route_ais, kind="Claude", cls="subscription", settings_extra=None):
    settings = routing.clean_settings({"routes": {"dialogue": {"ais": route_ais, "how": "order"}, **(settings_extra or {})}})
    return routing_view.build(
        settings=settings, table={}, status={"resting": [], "checks": {}, "working": {}}, reachable=reachable, remotes={}, stats={}, seen={}, costs=lambda j, k: None,
        classify=lambda a: (routing.CLASS_INCLUDED, cls), key_of=lambda a: a, name_of=lambda a: (a, kind), now_resting={})


CLAUDE_NOW = [{"id": "claude-sonnet-5-5", "owned_by": "anthropic"}, {"id": "claude-haiku-5-5", "owned_by": "anthropic"}, {"id": "claude-opus-5-5", "owned_by": "anthropic"}]


def _ai(view, id):
    return next(a for a in view["ais"] if a["id"] == id)


def test_a_model_the_set_up_provider_no_longer_lists_says_so_and_offers_the_closest_current_one():
    a = _ai(_build(CLAUDE_NOW, ["claude-haiku-4-5"]), "claude-haiku-4-5")
    assert a["place"] == "lost"
    assert a["gone"]["words"] == "Claude no longer offers this model"
    assert a["gone"]["use"] == {"id": "claude-haiku-5-5", "name": "claude-haiku-5-5"}, "haiku with haiku, never sonnet"


def test_no_clear_match_means_no_button():
    a = _ai(_build(CLAUDE_NOW, ["claude-instant-1"]), "claude-instant-1")
    assert a["gone"]["words"] == "Claude no longer offers this model" and a["gone"]["use"] is None


def test_a_provider_that_is_not_set_up_still_says_not_set_up():
    a = _ai(_build([], ["claude-haiku-4-5"]), "claude-haiku-4-5")
    assert a["place"] == "lost" and a["gone"] is None and a["state"] == "warn", "a real fault: amber"


def test_this_devices_own_models_never_get_the_new_reason():
    a = _ai(_build([{"id": "local/gemma", "owned_by": "local"}], ["local/old"], kind="Local models", cls="local"), "local/old")
    assert a["gone"] is None


def test_the_swap_changes_every_route_that_names_the_model_and_no_other_job():
    s = routing.clean_settings({"routes": {"decisions": {"ais": ["claude-haiku-4-5"], "how": "order"},
                                           "memory": {"ais": ["lmstudio/x", "claude-haiku-4-5, other"], "how": "order"},
                                           "combat": {"ais": ["lmstudio/y"], "how": "order"}}})
    out = routing_view.swap_model(s, "claude-haiku-4-5", "claude-haiku-5-5")
    assert out["routes"]["decisions"]["ais"] == ["claude-haiku-5-5"]
    assert out["routes"]["memory"]["ais"] == ["lmstudio/x", "claude-haiku-5-5, other"]
    assert out["routes"]["combat"]["ais"] == ["lmstudio/y"]


@pytest_asyncio.fixture
async def client(proxy_module):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    c.pm = proxy_module
    yield c


@pytest.mark.asyncio
async def test_the_button_saves_the_swap_through_the_routes_own_save_path(client, monkeypatch):
    pm = client.pm
    saved = {}
    cfg = {"routing": {"routes": {"decisions": {"ais": ["claude-haiku-4-5"], "how": "order"}, "memory": {"ais": ["lmstudio/x"], "how": "order"}}}}
    monkeypatch.setattr(pm, "_load_config", lambda: cfg)
    monkeypatch.setattr(pm, "_save_config", lambda c: saved.update(c))

    async def fake_view():
        return {"ok": True}
    monkeypatch.setattr(pm, "_routing_view", fake_view)
    r = await client.post("/config/routing/replace-model", json={"old": "claude-haiku-4-5", "new": "claude-haiku-5-5"})
    assert r.status_code == 200
    routes = saved["routing"]["routes"]
    assert routes["decisions"]["ais"] == ["claude-haiku-5-5"] and routes["memory"]["ais"] == ["lmstudio/x"]
    assert (await client.post("/config/routing/replace-model", json={"old": "", "new": "x"})).status_code == 400


def test_the_decisions_card_is_built_from_the_engine_that_answers(proxy_module, monkeypatch):
    pm = proxy_module

    class Eng:
        def __init__(self, st):
            self.st = st

        def status(self):
            return self.st

    class H:
        def __init__(self, loc, st):
            self.loc, self.eng = loc, Eng(st)

        def location(self):
            return self.loc

        def active(self):
            return self.eng
    monkeypatch.setattr(pm, "decision_handles", H("msi", {"installed": True, "model": "kev-4b", "state": "ready"}))
    c = pm._routing_decisions_card()
    assert c["place"] == "pc:msi" and c["state"] == "on" and c["word"] == "Running" and c["name"]
    monkeypatch.setattr(pm, "decision_handles", H("this", {"installed": True, "model": "kev-4b", "state": "stopped"}))
    c = pm._routing_decisions_card()
    assert c["place"] == "this" and c["state"] == "off" and c["word"] == "Stopped"
    monkeypatch.setattr(pm, "decision_handles", H("this", {"installed": False}))
    assert pm._routing_decisions_card() is None


HARNESS = r"""
const els = {}; const calls = [];
global.document = { getElementById: id => els[id] || null, activeElement: null, querySelectorAll: () => [] };
global.window = { addEventListener() {} };
global.location = { hash: '' };
global.history = { replaceState() {} };
global.esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
global.setInterval = () => 0; global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });
global.lmPost = async () => ({});
global.showMainTab = t => calls.push(t);
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_shows_the_new_reason_the_button_and_the_read_only_decisions_card(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
rtView = {
  places: [{ id: 'this', name: 'This device', caption: 'c' }, { id: 'pc:msi', name: 'MSI', caption: 'Courier device' }, { id: 'lost', name: 'Not reachable now', caption: 'x' }],
  ais: [{ id: 'old', name: 'claude-haiku-4-5', kind: 'Claude', place: 'lost', state: 'none', gone: { provider: 'Claude', words: 'Claude no longer offers this model', use: { id: 'new', name: 'claude-haiku-5-5' } } },
        { id: 'gone2', name: 'claude-x', kind: 'Claude', place: 'lost', state: 'none', gone: null }],
  jobs: [{ key: 'decisions', label: 'Decisions', about: '', route: { ais: ['old'], how: 'order' }, onAis: [], routable: false, sharesWith: [], how: null, models: [] }],
  decisions: { place: 'pc:msi', name: 'Lev', state: 'on', word: 'Running' }, hows: [], groups: [], status: {}, skyrimNetLinked: true,
};
const html = rtPicture();
const onMsi = html.split('<div class="rt-place"').filter(p => p.includes('<b>MSI</b>'))[0];
const onThis = html.split('<div class="rt-place"').filter(p => p.includes('<b>This device</b>'))[0];
rtView.decisions = null;
const chip = rtChips(rtView.ais[0], rtJobColours());
console.log(JSON.stringify({ chip, html, onMsi, onThis, none: rtPicture().includes('rt-dec'), kind: rtKindLine(rtView.ais[0]), kind2: rtKindLine(rtView.ais[1]) }));
"""
    f = tmp_path / "g.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert "Claude no longer offers this model" in o["html"] and "Use claude-haiku-5-5 instead" in o["html"]
    assert o["kind"] == "Claude no longer offers this model" and o["kind2"] == "Claude"
    assert o["html"].count("Use claude-") == 1, "no button without a clear match"
    assert 'class="rt-dec"' in o["onMsi"] and "Lev" in o["onMsi"] and "Decisions tab" in o["onMsi"] and "Running" in o["onMsi"]
    assert "rt-dec" not in o["onThis"] and 'draggable' not in o["onMsi"].split('class="rt-dec"')[1].split("</div></div>")[0]
    assert "showMainTab(\\'decisions\\')" in o["onMsi"] or "showMainTab('decisions')" in o["onMsi"]
    assert "Answers decisions" not in o["chip"] and "Decisions" not in o["chip"], "Decisions is no chip on Routing any more"
    assert o["none"] is False, "nothing new when the local decision model is not installed"
