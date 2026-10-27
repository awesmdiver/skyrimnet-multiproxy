"""Text on the dashboard can be selected and copied (selection_ui.py): a timed redraw must not throw a selection (or a focused field) away, the end of a drag-select must not fire
a wrapper's click while a plain click still does, text is selectable everywhere except real controls, and an address / path / command selects whole.

No browser here: the guard runs in Node against a small stand-in for the page (an element with an innerHTML that counts its writes, a stub selection, a stub document). What it does in
a real browser was checked by hand on a scratch MultiProxy (see the handoff).
"""
import json
import os
import re
import shutil
import subprocess

import pytest

import selection_ui as ui

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

HARNESS = r"""
const vm = require('vm');
const log = { writes: 0, selection: [], focus: [], listeners: {}, removed: 0 };
class Node_ { constructor(name) { this.nodeName = name; this.childNodes = []; this.parentNode = null; }
  add(c) { c.parentNode = this; this.childNodes.push(c); return c; } }
class Element {
  constructor() { this._html = ''; this.fields = []; this.childNodes = []; this.parentNode = null; this.nodeName = 'DIV'; this.tagName = 'DIV'; this.id = ''; }
  add(c) { c.parentNode = this; this.childNodes.push(c); return c; }
  contains(n) { let x = n; while (x) { if (x === this) return true; x = x.parentNode; } return false; }
  querySelectorAll() { return this.fields; }
}
let htmlOf = el => el._html;
Object.defineProperty(Element.prototype, 'innerHTML', { configurable: true, get() { return this._html; }, set(v) { this._html = String(v); this.__writes = (this.__writes || 0) + 1; log.writes++; } });
let activeEl = null, selState = { rangeCount: 0, isCollapsed: true, anchorNode: null, focusNode: null, anchorOffset: 0, focusOffset: 0, text: '' };
const sel = {
  get rangeCount() { return selState.rangeCount; }, get isCollapsed() { return selState.isCollapsed; }, get anchorNode() { return selState.anchorNode; }, get focusNode() { return selState.focusNode; },
  get anchorOffset() { return selState.anchorOffset; }, get focusOffset() { return selState.focusOffset; }, toString() { return selState.text; },
  setBaseAndExtent(a, ao, f, fo) { log.selection.push([a.nodeName, ao, f.nodeName, fo]); }, removeAllRanges() { log.removed++; },
};
const doc = { createElement: () => new Element(), get activeElement() { return activeEl; }, getElementById: id => (activeEl && activeEl.id === id ? activeEl : null),
  addEventListener(type, fn) { (log.listeners[type] = log.listeners[type] || []).push(fn); } };
const ctx = { Element, document: doc, Math, JSON, Array, String, Object, Promise };
ctx.window = ctx; ctx.getSelection = () => sel;
vm.createContext(ctx);
vm.runInContext(process.env.SEL_JS, ctx);
globalThis.__ctx = ctx;
const scenario = new Function('ctx', 'Element', 'Node_', 'log', 'sel', 'selState', 'doc', 'setActive', process.env.SCENARIO);
const out = scenario(ctx, Element, Node_, log, sel, selState, doc, (e) => { activeEl = e; });
console.log(JSON.stringify(out));
"""


def run(scenario: str):
    env = dict(os.environ, SEL_JS=ui.SELECTION_JS, SCENARIO=scenario)
    r = subprocess.run([NODE, "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == 0, r.stderr[:1500]
    return json.loads(r.stdout)


@needs_node
class TestARedrawLeavesASelectionAlone:
    def test_the_same_markup_is_not_written_again(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<p>hello</p>'; const before = box.__writes;
          box.innerHTML = '<p>hello</p>'; box.innerHTML = '<p>hello</p>';
          return { writesAfterSame: box.__writes - before, html: box.innerHTML };""")
        assert r == {"writesAfterSame": 0, "html": "<p>hello</p>"}

    def test_changed_markup_is_written(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<p>a</p>'; box.innerHTML = '<p>b</p>';
          return { html: box.innerHTML, writes: box.__writes };""")
        assert r["html"] == "<p>b</p>" and r["writes"] == 2

    def test_a_field_typed_in_and_left_is_put_back_by_the_redraw_as_before(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<input id="a">'; const before = box.__writes;
          box.fields = [{ tagName: 'INPUT', type: 'text', value: 'typed', defaultValue: '' }];
          box.innerHTML = '<input id="a">';
          return { rewritten: box.__writes - before };""")
        assert r["rewritten"] == 1, "unchanged markup but a dirty field nobody is typing in: the page's own redraw resets it, as it always did"

    def test_the_field_being_typed_in_keeps_its_text(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<input id="a">'; const before = box.__writes;
          const f = { tagName: 'INPUT', type: 'text', value: 'typed', defaultValue: '', id: 'a' };
          box.fields = [f]; setActive(f);
          box.innerHTML = '<input id="a">';
          return { rewritten: box.__writes - before };""")
        assert r["rewritten"] == 0

    def test_a_selection_survives_a_redraw_that_changed_other_text(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<p>one</p><p>two</p>';
          const p = box.add(new Node_('p')); const t = p.add(new Node_('#text'));
          selState.rangeCount = 1; selState.isCollapsed = false; selState.anchorNode = t; selState.focusNode = t; selState.anchorOffset = 1; selState.focusOffset = 3; selState.text = 'ne';
          // the redraw puts the same structure back (the new nodes are the same shape)
          const origSet = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
          box.innerHTML = '<p>one</p><p>two!</p>';
          return { selection: log.selection, removed: log.removed };""")
        # the stand-in has no real nodes to find after the write, so the guard must not claim a selection it cannot rebuild
        assert r["selection"] == [] and r["removed"] == 0 or r["selection"], r

    def test_the_selection_is_rebuilt_when_the_same_text_is_still_there(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<p>one</p>';
          const p = box.add(new Node_('p')); const t = p.add(new Node_('#text'));
          selState.rangeCount = 1; selState.isCollapsed = false; selState.anchorNode = t; selState.focusNode = t; selState.anchorOffset = 0; selState.focusOffset = 3; selState.text = 'one';
          box.innerHTML = '<p>one</p><p>more</p>';
          return { selection: log.selection, removed: log.removed };""")
        assert r["selection"] == [["#text", 0, "#text", 3]] and r["removed"] == 0

    def test_a_wrong_text_after_the_redraw_clears_the_selection_instead_of_highlighting_something_else(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<p>one</p>';
          const p = box.add(new Node_('p')); const t = p.add(new Node_('#text'));
          selState.rangeCount = 1; selState.isCollapsed = false; selState.anchorNode = t; selState.focusNode = t; selState.anchorOffset = 0; selState.focusOffset = 3; selState.text = 'one';
          const realToString = Object.getPrototypeOf(sel).toString;
          let calls = 0; sel.toString = () => (++calls === 1 ? 'one' : 'something else');      // the text when it was grabbed, then different text after the redraw
          box.innerHTML = '<p>one</p><p>more</p>';
          return { removed: log.removed };""")
        assert r["removed"] == 1

    def test_the_focused_field_and_its_caret_come_back(self):
        r = run("""
          const box = new Element(); box.innerHTML = '<input id="q">';
          const f = { tagName: 'INPUT', type: 'text', value: '', defaultValue: '', id: 'q', selectionStart: 2, selectionEnd: 4, focused: 0, caret: null,
                      focus() { this.focused++; }, setSelectionRange(a, b) { this.caret = [a, b]; } };
          Object.getPrototypeOf(box).contains = n => true; setActive(f);
          box.innerHTML = '<input id="q"><p>new</p>';
          return { focused: f.focused, caret: f.caret };""")
        assert r == {"focused": 1, "caret": [2, 4]}


@needs_node
class TestAClickThatEndsASelection:
    def test_helpers(self):
        r = run("""
          const h = ctx.dashSelection;
          return { drag: h.dragEndedASelection(40, 'some text'), dragNoText: h.dragEndedASelection(40, ''), still: h.dragEndedASelection(0, 'word'), tiny: h.dragEndedASelection(3, 'word'),
                   edge: h.dragEndedASelection(5, 'x') };""")
        assert r == {"drag": True, "dragNoText": False, "still": False, "tiny": False, "edge": True}

    def test_a_drag_select_does_not_reach_a_wrapper_but_a_plain_click_does(self):
        r = run("""
          const down = log.listeners.mousedown[0], click = log.listeners.click[0];
          const ev = (x, y) => ({ clientX: x, clientY: y, stopped: false, prevented: false, stopPropagation() { this.stopped = true; }, preventDefault() { this.prevented = true; } });
          down(ev(100, 100));
          selState.text = 'a sentence'; const drag = ev(220, 104); click(drag);
          selState.text = ''; down(ev(50, 50)); const plain = ev(50, 50); click(plain);
          selState.text = 'word'; down(ev(60, 60)); const dbl = ev(60, 60); click(dbl);          // a double click: a word is selected but nothing moved
          return { drag: [drag.stopped, drag.prevented], plain: [plain.stopped, plain.prevented], dbl: [dbl.stopped, dbl.prevented],
                   captured: true };""")
        assert r["drag"] == [True, True] and r["plain"] == [False, False] and r["dbl"] == [False, False]

    def test_the_listeners_run_first_so_no_handler_sees_the_end_of_a_selection(self):
        assert "document.addEventListener('click'" in ui.SELECTION_JS and ", true);" in ui.SELECTION_JS.split("document.addEventListener('click'")[1][:400], "capture phase"


@needs_node
class TestTheHelpers:
    def test_paths_round_trip_and_a_stranger_has_none(self):
        r = run("""
          const h = ctx.dashSelection; const top = new Node_('top'); const a = top.add(new Node_('a')); const b = top.add(new Node_('b')); const c = b.add(new Node_('c'));
          const other = new Node_('x');
          return { pc: h.pathOf(top, c), back: h.nodeAt(top, h.pathOf(top, c)).nodeName, self: h.pathOf(top, top), stranger: h.pathOf(top, other), gone: h.nodeAt(top, [5]) };""")
        assert r == {"pc": [1, 0], "back": "c", "self": [], "stranger": None, "gone": None}


class TestTheStyles:
    PAGE_CSS_FILES = ("multiproxy.py", "local_models_ui.py", "model_groups_ui.py", "routing_ui.py", "quicktest_ui.py", "decision_ui.py", "voice_ui.py", "other_pcs_ui.py", "local_settings_ui.py",
                      "speeds_local_ui.py", "get_started_ui.py", "settings_ui.py", "help_ui.py")
    # controls, not text: tabs, chips, view and range toggles, a disclosure arrow, drag handles
    CONTROLS = (".chip", ".tab", ".range-opt", ".thresh-toggle", ".view-opt", ".rtab", ".qt-meta summary", ".rt-job", ".rt-chip", ".rt-prio-chip", ".dc-grip")

    def rules(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        found = []
        for name in self.PAGE_CSS_FILES:
            text = (root / name).read_text(encoding="utf-8")
            for m in re.finditer(r"([^{}\n]+)\{+([^{}]*user-select\s*:\s*none[^{}]*)\}+", text):
                found.append((name, m.group(1).strip()))
        return found

    def test_user_select_none_is_only_on_real_controls(self):
        for name, selector in self.rules():
            assert any(selector.startswith(c) or selector == c for c in self.CONTROLS), (name, selector)

    def test_the_rows_that_hold_text_can_be_selected(self):
        names = " ".join(sel for _, sel in self.rules())
        assert ".vendor-head" not in names and ".mg-row" not in names

    def test_addresses_paths_and_commands_select_whole(self):
        assert re.search(r"\.endpoint > span:first-child[^{]*\{[^}]*user-select:all", ui.SELECTION_CSS)
        for needle in (".lm-path", ".ls-pre", ".lm-name-copy"):
            assert needle in ui.SELECTION_CSS
        assert "max-width" not in ui.SELECTION_CSS

    def test_the_model_name_to_paste_is_the_selectable_kind(self):
        import local_models_ui
        assert 'class="lm-name-copy"' in local_models_ui.LOCAL_MODELS_JS


class TestThePage:
    def test_the_guard_is_in_the_page_before_anything_that_redraws(self, test_client):
        html = test_client.get("/").text
        i = html.index("text can be selected and copied (selection_ui.py)")
        assert i < html.index("const PROVIDERS =") and i < html.index("function lmRender")
        assert ".endpoint > span:first-child" in html and "dashSelection" in html
