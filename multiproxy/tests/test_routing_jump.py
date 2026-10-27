"""The Routing tab's "Jump to" row (2026-10-24): four links in order, a fifth only while a job is open, every target exists, a click only scrolls (never the address),
and the highlighted link follows the scroll. The tab's script is run under node with a stand-in page."""
import json
import shutil
import subprocess

import pytest

import jump_ui
import routing_ui

NODE = shutil.which("node")

HARNESS = r"""
const els = {};
const scrolls = [];
const listeners = {};
global.document = { getElementById: id => els[id] || null, activeElement: null, querySelectorAll: () => [], documentElement: { scrollHeight: 3000 } };
global.window = { addEventListener(n, f) { listeners[n] = f; }, pageYOffset: 0, innerHeight: 800, scrollTo(o) { scrolls.push(o); } };
global.location = { hash: '#routing' };
global.history = { replaceState() {} };
global.esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
global.setInterval = () => 0; global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });
global.showMainTab = () => {};
// the row: its buttons are parsed back out of the html it was given
const row = { hidden: true, _html: '', offsetHeight: 40, style: {}, getBoundingClientRect: () => ({ bottom: 50 }),
  set innerHTML(h) { this._html = h; this._btns = [...h.matchAll(/<button[^>]*data-t="([^"]+)"[^>]*>([^<]*)<\/button>/g)].map(m => mkBtn(m[1], m[2])); },
  get innerHTML() { return this._html; },
  querySelectorAll() { return this._btns || []; } };
function mkBtn(t, text) { const cls = new Set(); return { t, text, classList: { toggle(c, on) { on ? cls.add(c) : cls.delete(c); }, has: c => cls.has(c) }, getAttribute: () => t }; }
els.rtJump = row; els.paneRouting = { style: { display: 'block' } };
const tops = {};
function target(id, top) { tops[id] = top; els[id] = { getBoundingClientRect: () => ({ top: tops[id] }) }; }
const view = {
  settings: { routes: {}, rest: { failures: 3, within_s: 60, for_s: 120 }, slow_handover: { enabled: false, streamed_dialogue: false }, aging_s: 30 },
  places: [{ id: 'this', name: 'This device', caption: 'c' }], ais: [], pickable: [],
  jobs: [{ key: 'memory', label: 'Memory', about: 'm', route: null, onAis: [], sharesWith: [], how: null, models: [] }],
  hows: [], groups: [], status: {}, skyrimNetLinked: true, priority: ['memory'],
};
"""


def _run(tmp_path, body):
    f = tmp_path / "j.js"
    f.write_text(HARNESS + jump_ui.JUMP_JS + routing_ui.ROUTING_JS + body, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_row_and_the_heading_are_in_the_page():
    assert 'id="rtJump"' in routing_ui.ROUTING_PANE_HTML
    assert "const RT_PICK_TITLE = 'Pick a model for each job';" in routing_ui.ROUTING_JS
    assert "position:sticky" in jump_ui.JUMP_CSS and "jump-row" in jump_ui.JUMP_CSS
    # the link row never touches the address: no hash is written anywhere in it
    jump = jump_ui.JUMP_JS[jump_ui.JUMP_JS.index("function jumpDraw"):jump_ui.JUMP_JS.index("function jumpDrawAll")]
    assert "location" not in jump and "history" not in jump and "href" not in jump


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_four_links_in_order_a_fifth_only_while_a_job_is_open(tmp_path):
    out = _run(tmp_path, r"""
const out = {};
els.rtBody = { innerHTML: '' }; els.rtLive = {};
const labels = () => (row._btns || []).map(b => b.text);
rtView = view;
rtRender();
out.html = els.rtBody.innerHTML;
// the cards exist in the stand-in only as ids in the html
for (const id of ['rtPickCard', 'rtFirstCard', 'rtUseSec']) if (els.rtBody.innerHTML.includes('id="' + id + '"')) els[id] = {};
rtRender();
out.closed = labels(); out.hiddenClosed = row.hidden;
rtOpenKey = 'memory'; els.rtJobCard = {}; rtRender();
out.open = labels();
rtOpenKey = null; delete els.rtJobCard; rtRender();
out.closedAgain = labels();
out.hasIds = ['rtPickCard', 'rtFirstCard', 'rtJobCard', 'rtUseSec'].map(id => out.html.includes('id="' + id + '"') || id === 'rtJobCard');
console.log(JSON.stringify(out));
""")
    four = ["Pick a model for each job", "Priority when busy", "Set it up in SkyrimNet", "Live"]
    assert out["closed"] == four and out["hiddenClosed"] is False
    assert out["open"] == ["Pick a model for each job", "Priority when busy", "This job: Memory", "Set it up in SkyrimNet", "Live"]
    assert out["closedAgain"] == four
    assert all(out["hasIds"])
    assert "<h2 style=\"margin:0 0 10px\">Pick a model for each job</h2>" in out["html"]
    assert ">Routing</h2>" not in out["html"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_click_only_scrolls_and_the_highlight_follows_the_scroll(tmp_path):
    out = _run(tmp_path, r"""
const out = {};
els.rtBody = { innerHTML: '' }; els.rtLive = {};
rtView = view; rtRender();
for (const [id, top] of [['rtPickCard', 100], ['rtFirstCard', 900], ['rtUseSec', 1300], ['rtLive', 2000]]) target(id, top);
JUMP_ROWS.rtJump.sig = ''; rtJumpDraw();
const here = () => row._btns.filter(b => b.classList.has('jump-here')).map(b => b.t);
out.atTop = here();
tops.rtPickCard = -700; tops.rtFirstCard = 40; tops.rtUseSec = 440; tops.rtLive = 1100; jumpUpdate('rtJump'); out.second = here();
tops.rtPickCard = -1500; tops.rtFirstCard = -700; tops.rtUseSec = 20; tops.rtLive = 600; jumpUpdate('rtJump'); out.third = here();
window.pageYOffset = 2200; window.innerHeight = 800; tops.rtLive = 300; jumpUpdate('rtJump'); out.bottom = here();   // scrolled to the very end: the last link
window.pageYOffset = 400; window.innerHeight = 800; jumpUpdate('rtJump');
window.pageYOffset = 500; jumpTo('rtJump', 'rtUseSec');
out.scroll = scrolls[0]; out.hash = location.hash;
console.log(JSON.stringify(out));
""")
    assert out["atTop"] == ["rtPickCard"]
    assert out["second"] == ["rtFirstCard"]
    assert out["third"] == ["rtUseSec"]
    assert out["bottom"] == ["rtLive"]
    assert out["scroll"] == {"top": 20 + 500 - 40 - 8, "behavior": "smooth"}
    assert out["hash"] == "#routing"
