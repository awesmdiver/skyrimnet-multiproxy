"""Text on the dashboard can be selected and copied (2026-10-05): one shared place that makes a redraw and a click leave a selection alone.

Why it failed (found, not guessed):
  1. EVERY card redraws itself on a timer (the model list every 6 s, the Quick decisions card every 2 s, Voice 2 s, Routing 2 s, Other devices 5 s, Speeds 5 s, memory 3.5 s ...) by
     assigning `box.innerHTML = ...`. Replacing the HTML throws away the page's selection and the focus of a field, so a sentence could not be selected for more than a moment
     (sometimes it worked: between two redraws). One redraw is enough to lose the whole thing.
  2. A click on a big wrapper does something: the Quick decisions card's outer `div` has `onclick="if (dcMenu) { ...; dcRender(); }"`. The mouse-up that ends a drag-select is a
     click on the common ancestor, so finishing a selection could redraw the card (losing the selection) or close a menu.
  3. `user-select: none` sat on rows that hold text (`.vendor-head`, `.mg-row`), so nothing in them could be selected.
(Draggable job chips and tabs / chips / view toggles keep `user-select: none`: they are controls, not text.)

The fix is here, once, for every card file instead of card by card:
  * `Element.prototype.innerHTML`'s setter is wrapped. If the markup is exactly what is already there (and no field of it was typed in and left unfocused), nothing is touched: the
    selection, the focus, the scroll position and a hover all stay, which is what nearly every timed redraw is. If the markup really changed, the selection (as paths of child
    positions) and the focused field (its id, caret and selection) are put back afterwards when the same text is still there.
  * A click that ends a drag with text selected is stopped before any handler sees it; a plain click is untouched.
  * `.endpoint` text, the folder path and a command are `user-select: all`: one click, double click or triple click selects the whole address, path or command.

The helpers are plain functions on `dashSelection`, so tests/test_selection_ui.py runs them in Node (no browser).
"""

SELECTION_CSS = r"""
  /* Text is selectable everywhere except real controls (tabs, chips, view toggles and drag handles keep user-select:none). An address, a path or a command selects whole. */
  .endpoint > span:first-child, .lm-path, .ls-pre, .lm-copyable { user-select:all; -webkit-user-select:all }
  .lm-name-copy { font-family:monospace; color:var(--accent-text); user-select:all; -webkit-user-select:all }
"""

SELECTION_JS = r"""// ---------------- text can be selected and copied (selection_ui.py) ----------------
(function (root) {
  'use strict';
  const DRAG_PIXELS = 4;
  function pathOf(top, node) {
    const path = [];
    while (node && node !== top) {
      const parent = node.parentNode;
      if (!parent) return null;
      path.unshift(Array.prototype.indexOf.call(parent.childNodes, node));
      node = parent;
    }
    return node === top ? path : null;
  }
  function nodeAt(top, path) {
    let node = top;
    for (const i of path) { node = node && node.childNodes ? node.childNodes[i] : null; if (!node) return null; }
    return node;
  }
  // A click that ends a drag with text selected is the end of a selection, not a click on whatever lies under the pointer.
  function dragEndedASelection(movedPixels, selectedText) { return movedPixels > DRAG_PIXELS && !!selectedText; }
  root.dashSelection = { pathOf, nodeAt, dragEndedASelection };

  if (typeof Element === 'undefined' || typeof document === 'undefined') return;

  // ---- a selection and a focused field survive a redraw ----
  const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
  if (!desc || !desc.set || !desc.get) return;
  const probe = document.createElement('template');
  function selection() { return window.getSelection ? window.getSelection() : null; }
  function dirtyAndLeftAlone(top) {
    // a field the person typed in (or toggled) and is no longer in: its HTML looks unchanged but a redraw is how it goes back to what the page says
    const active = document.activeElement;
    for (const el of top.querySelectorAll('input, textarea, select')) {
      if (el === active) continue;
      if (el.tagName === 'SELECT') { if (Array.prototype.some.call(el.options, o => o.selected !== o.defaultSelected)) return true; }
      else if (el.type === 'checkbox' || el.type === 'radio') { if (el.checked !== el.defaultChecked) return true; }
      else if (el.value !== el.defaultValue) return true;
    }
    return false;
  }
  function grab(top) {
    const keep = {};
    const sel = selection();
    try {
      if (sel && sel.rangeCount && !sel.isCollapsed && top.contains(sel.anchorNode) && top.contains(sel.focusNode)) {
        keep.sel = { a: pathOf(top, sel.anchorNode), ao: sel.anchorOffset, f: pathOf(top, sel.focusNode), fo: sel.focusOffset, text: String(sel) };
      }
    } catch (e) {}
    const ae = document.activeElement;
    if (ae && ae !== top && ae.id && top.contains(ae)) {
      keep.focus = { id: ae.id };
      try { keep.focus.start = ae.selectionStart; keep.focus.end = ae.selectionEnd; } catch (e) {}
    }
    return keep;
  }
  function putBack(top, keep) {
    try {
      if (keep.focus) {
        const el = document.getElementById(keep.focus.id);
        if (el && top.contains(el)) {
          el.focus({ preventScroll: true });
          if (keep.focus.start != null) el.setSelectionRange(keep.focus.start, keep.focus.end);
        }
      }
      if (keep.sel && keep.sel.a && keep.sel.f) {
        const sel = selection(), a = nodeAt(top, keep.sel.a), f = nodeAt(top, keep.sel.f);
        if (sel && a && f) {
          sel.setBaseAndExtent(a, keep.sel.ao, f, keep.sel.fo);
          if (String(sel) !== keep.sel.text) sel.removeAllRanges();      // not the same text any more: no selection is better than a wrong one
        }
      }
    } catch (e) {}
  }
  Object.defineProperty(Element.prototype, 'innerHTML', {
    configurable: true, enumerable: desc.enumerable, get: desc.get,
    set: function (html) {
      html = String(html);
      try {
        desc.set.call(probe, html);
        if (desc.get.call(probe) === desc.get.call(this) && !dirtyAndLeftAlone(this)) return;      // exactly what is there: leave the page as it is
      } catch (e) {}
      const keep = grab(this);
      desc.set.call(this, html);
      if (keep.sel || keep.focus) putBack(this, keep);
    }
  });

  // ---- the end of a drag-select is not a click ----
  let down = null;
  document.addEventListener('mousedown', e => { down = { x: e.clientX, y: e.clientY }; }, true);
  document.addEventListener('click', e => {
    if (!down) return;
    const sel = selection();
    if (dragEndedASelection(Math.hypot(e.clientX - down.x, e.clientY - down.y), sel ? String(sel) : '')) { e.stopPropagation(); e.preventDefault(); }
  }, true);
})(typeof window !== 'undefined' ? window : globalThis);
"""
