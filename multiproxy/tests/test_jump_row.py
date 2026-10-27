"""The shared "Jump to" row (jump_ui.py, 2026-10-24): links in order with the headings' own words, the highlight follows the scroll, a click only scrolls, a short tab shows no row,
and every tab that uses the row names sections that really exist in its own source. The script is run under node with a stand-in page."""
import json
import re
import shutil
import subprocess

import pytest

import decision_ui
import jump_ui
import local_models_ui
import local_settings_ui
import other_pcs_ui
import overview_ui

NODE = shutil.which("node")

HARNESS = r"""
const els = {}, scrolls = [], listeners = {};
global.document = { getElementById: id => els[id] || null, documentElement: { scrollHeight: 5000 } };
global.window = { addEventListener(n, f) { listeners[n] = f; }, pageYOffset: 0, innerHeight: 800, scrollTo(o) { scrolls.push(o); } };
global.location = { hash: '#local' };
global.esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
global.setInterval = () => 0;
const row = { hidden: true, _html: '', offsetHeight: 40, getBoundingClientRect: () => ({ bottom: 50 }),
  set innerHTML(h) { this._html = h; this._btns = [...h.matchAll(/<button[^>]*data-t="([^"]+)"[^>]*>([^<]*)<\/button>/g)].map(m => mkBtn(m[1], m[2])); },
  get innerHTML() { return this._html; }, querySelectorAll() { return this._btns || []; } };
function mkBtn(t, text) { const cls = new Set(); return { t, text, classList: { toggle(c, on) { on ? cls.add(c) : cls.delete(c); }, has: c => cls.has(c) }, getAttribute: () => t }; }
els.tRow = row;
const pane = { style: { display: 'block' }, offsetHeight: 4000 };
els.tPane = pane;
const tops = {};
// a section: a card with a heading inside (with an optional grey "· name" span), or a heading itself
function section(id, top, title, sub, isHeading) {
  tops[id] = top;
  const h = { tagName: 'H3', textContent: title + (sub ? ' · ' + sub : ''), querySelectorAll: () => [], cloneNode() { return { textContent: title, querySelectorAll: () => [] }; } };
  els[id] = isHeading ? Object.assign(h, { getBoundingClientRect: () => ({ top: tops[id] }) }) : { tagName: 'DIV', querySelector: () => h, getBoundingClientRect: () => ({ top: tops[id] }) };
}
section('a', 100, 'First'); section('b', 900, 'Second', 'the name'); section('c', 1500, 'Third', '', true); section('d', 2400, 'Fourth');
jumpRegister('tRow', 'tPane', () => jumpIds(['a', 'b', 'c', 'd', 'gone']), {});
const here = () => (row._btns || []).filter(b => b.classList.has('jump-here')).map(b => b.t);
"""


def _run(tmp_path, body):
    f = tmp_path / "j.js"
    f.write_text("global.setInterval = () => 0;\n" + jump_ui.JUMP_JS + HARNESS + body, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_links_follow_the_headings_in_order_and_a_missing_section_has_no_link(tmp_path):
    out = _run(tmp_path, r"""
jumpDraw('tRow');
console.log(JSON.stringify({ labels: row._btns.map(b => b.text), hidden: row.hidden }));
""")
    assert out["labels"] == ["First", "Second", "Third", "Fourth"] and out["hidden"] is False


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_highlight_follows_the_scroll_and_a_click_never_changes_the_address(tmp_path):
    out = _run(tmp_path, r"""
const out = {};
jumpDraw('tRow'); out.top = here();
tops.a = -700; tops.b = 40; tops.c = 700; tops.d = 1600; jumpUpdate('tRow'); out.second = here();
tops.a = -2000; tops.b = -1300; tops.c = -500; tops.d = 30; jumpUpdate('tRow'); out.fourth = here();
window.pageYOffset = 4300; jumpUpdate('tRow'); out.end = here();
window.pageYOffset = 500; tops.c = 20; jumpTo('tRow', 'c');
out.scroll = scrolls[0]; out.hash = location.hash; out.picked = here();
console.log(JSON.stringify(out));
""")
    assert out["top"] == ["a"] and out["second"] == ["b"] and out["fourth"] == ["d"] and out["end"] == ["d"]
    assert out["scroll"] == {"top": 20 + 500 - 40 - 8, "behavior": "smooth"} and out["hash"] == "#local" and out["picked"] == ["c"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_short_tab_or_one_with_too_few_sections_gets_no_row(tmp_path):
    out = _run(tmp_path, r"""
const out = {};
pane.offsetHeight = 900; jumpDraw('tRow'); out.short = row.hidden;           // 900 px is about one screen: no row
pane.offsetHeight = 4000; jumpDraw('tRow'); out.tall = row.hidden;
delete els.b; delete els.c; JUMP_ROWS.tRow.sig = ''; jumpDraw('tRow'); out.two = row.hidden;      // only two sections left
console.log(JSON.stringify(out));
""")
    assert out == {"short": True, "tall": False, "two": True}


def test_the_row_never_writes_the_address():
    assert "location" not in jump_ui.JUMP_JS and "history" not in jump_ui.JUMP_JS and "href" not in jump_ui.JUMP_JS


# Each tab's links are its real headings: the id is on the section and the heading's words are in the tab's own source.
TABS = [
    ("overview", overview_ui.JS, "ovJump", [("ovMachines", "Machines"), ("ovModels", "Models and services"), ("ovLook", "Needs a look")]),
    ("local", local_models_ui.LOCAL_MODELS_CARD_HTML + local_models_ui.LOCAL_MODELS_JS + local_settings_ui.LOCAL_SETTINGS_JS, "lmJump",
     [("memCard", "Graphics memory"), ("localCard", "Local models"), ("lsDeviceCard", "This device"), ("lsAdvCard", "Advanced: your own flags")]),
    ("other", other_pcs_ui.OTHER_PCS_JS, "opJump", [("opDevicesCard", "Other devices"), ("opBackupCard", "When a device doesn\\'t answer"), ("opAddCard", "Add a device")]),
    ("decisions", decision_ui.DECISION_JS, "dcJump", [("dcQuick", "Quick decisions"), ("dcDevices", "Where decisions run"), ("dcWho", "Who answers decisions")]),
]


@pytest.mark.parametrize("name,source,row_id,sections", TABS, ids=[t[0] for t in TABS])
def test_each_tabs_links_match_the_headings_that_exist(name, source, row_id, sections):
    assert f"jumpRegister('{row_id}'" in source
    for sec_id, heading in sections:
        assert f"'{sec_id}'" in source.split("jumpRegister", 1)[1].split("\n", 1)[0], (name, sec_id)
        assert re.search(r'id="%s"' % re.escape(sec_id), source), (name, sec_id)
        assert ">" + heading in source or heading + " <span" in source, (name, sec_id, heading)       # the heading's own words (a card's heading may be drawn by the tab's script)


def test_the_shared_script_comes_before_every_tab_that_registers_a_row():
    src = open(multiproxy_path(), encoding="utf-8").read()
    assert "{jump_ui.JUMP_JS}" in src and "{jump_ui.JUMP_CSS}" in src and "jump_ui.row('lmJump')" in src
    assert src.index("{jump_ui.JUMP_JS}") < src.index("{local_models_ui.LOCAL_MODELS_JS}") < src.index("{routing_ui.ROUTING_JS}")


def multiproxy_path():
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "multiproxy.py")
