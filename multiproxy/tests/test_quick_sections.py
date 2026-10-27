"""The Quick Test tab as two collapsible sections, "Try a provider" (open) and "Try a Quick decision" (closed) (queue: quick-test-tab-has-two-collapsible-sections-up-top, 2026-10-05).
The section code runs in Node against a tiny stand-in page; the real page is checked for the headers and the two tests' own controls."""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

import quick_sections_ui

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

HARNESS = r"""
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ''; this.id = ''; this.open = false; this.listeners = {}; this._text = ''; this.parent = null; }
  appendChild(c) { if (c.parent) c.parent.children = c.parent.children.filter(x => x !== c); c.parent = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.appendChild(c)); }
  get childElementCount() { return this.children.length; }
  set textContent(t) { this._text = t; }
  get textContent() { return this._text; }
  addEventListener(n, f) { this.listeners[n] = f; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); this.removed = true; }
}
const els = {};
for (const id of ['qsList', 'qsBodies', 'qsBody-provider', 'qsBody-decision', 'qsBody-check']) { els[id] = new El('div'); els[id].id = id; }
els['qsBodies'].appendChild(els['qsBody-provider']); els['qsBodies'].appendChild(els['qsBody-decision']); els['qsBodies'].appendChild(els['qsBody-check']);
const $ = id => els[id];
const document = { createElement: t => new El(t) };
const store = JSON.parse(process.env.QS_STORE || '{}');
const localStorage = process.env.QS_BROKEN ? { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } }
  : { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
"""


def run(tail, store=None, broken=False):
    src = HARNESS + quick_sections_ui.JS + "\n" + tail
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        out = subprocess.run([NODE, f.name], capture_output=True, timeout=30,
                             env={**os.environ, "QS_STORE": json.dumps(store or {}), **({"QS_BROKEN": "1"} if broken else {})})
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


SHOW = "console.log(JSON.stringify($('qsList').children.map(d => ({ id: d.id, title: d.children[0].textContent, open: d.open, body: d.children[1].children.map(c => c.id) })), null, 0));"


@needs_node
def test_two_headers_in_order_the_first_open_the_second_closed():
    r = run(SHOW)
    assert r == [{"id": "qs-provider", "title": "Try a provider", "open": True, "body": ["qsBody-provider"]},
                 {"id": "qs-decision", "title": "Try a Quick decision", "open": False, "body": ["qsBody-decision"]},
                 {"id": "qs-check", "title": "Check it like SkyrimNet", "open": False, "body": ["qsBody-check"]}]


@needs_node
def test_the_choice_is_remembered_and_a_failed_read_uses_the_defaults():
    assert [s["open"] for s in run(SHOW, store={"qsOpen-provider": "0", "qsOpen-decision": "1"})] == [False, True, False]
    assert [s["open"] for s in run(SHOW, broken=True)] == [True, False, False]


@needs_node
def test_toggling_saves_each_one_on_its_own_and_does_not_touch_the_other():
    r = run("const a = $('qsList').children[0], b = $('qsList').children[1]; b.open = true; b.listeners.toggle(); a.open = false; a.listeners.toggle();"
            "console.log(JSON.stringify(store));")
    assert r == {"qsOpen-decision": "1", "qsOpen-provider": "0"}
    run("$('qsList').children[0].open = false; $('qsList').children[0].listeners.toggle(); console.log('null');", broken=True)       # a blocked save is ignored, not an error


@needs_node
def test_the_bodies_are_moved_not_copied_so_a_result_survives_closing_and_building_twice_does_nothing():
    r = run("const keep = $('qsBody-decision'); keep.marker = 'result'; $('qsList').children[1].open = false; qsBuild();"
            "console.log(JSON.stringify({ n: $('qsList').childElementCount, same: $('qsList').children[1].children[1].children[0] === keep, marker: keep.marker, gone: $('qsBodies').removed === true }));")
    assert r == {"n": 3, "same": True, "marker": "result", "gone": True}


@needs_node
def test_the_entry_list_drives_the_tab():
    r = run("QS_SECTIONS.push({ id: 'third', title: 'Try a third thing', open: false, body: () => { const e = new El('div'); e.id = 'qsBody-third'; return e; } });"
            "$('qsList').children.length = 0; $('qsList').children.splice(0); qsBuild();" + SHOW)
    assert [s["title"] for s in r] == ["Try a provider", "Try a Quick decision", "Check it like SkyrimNet", "Try a third thing"]


def test_the_page_has_the_headers_up_top_and_both_tests_untouched(test_client):
    h = test_client.get("/").text
    pane = h[h.index('<div id="paneQuick"'):h.index('<div id="paneSpeeds"')]
    assert pane.index('id="qsList"') < pane.index('id="qsBodies"') < pane.index('id="qsBody-provider"') < pane.index('id="qsBody-decision"')
    for needle in ('id="testProvider"', 'id="testModel"', 'id="testScenario"', 'id="sysPrompt"', 'id="userMsg"', 'onclick="openQuickTestPanel()"',
                   'id="dqWho"', 'id="dqScenario"', 'onclick="dqSend()"', 'id="dqOut"'):
        assert needle in pane, needle
    assert pane.count("<h4>Start here</h4>") == 3, "each test keeps its own Start here steps"
    assert "Try Quick decisions" not in pane, "the section title replaces the card's own heading"
    assert "title: 'Try a provider', open: true" in h and "title: 'Try a Quick decision', open: false" in h


def test_the_summary_is_a_real_details_header_so_enter_and_space_work():
    assert "document.createElement('details')" in quick_sections_ui.JS and "document.createElement('summary')" in quick_sections_ui.JS
    assert "innerHTML" not in quick_sections_ui.JS and "max-width" not in quick_sections_ui.CSS
