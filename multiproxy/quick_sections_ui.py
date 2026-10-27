"""The Quick Test tab as a list of collapsible test sections (2026-10-05): "Try a provider" (the chat test, open) and "Try a Quick decision" (the Jev test, closed).

Each test's body is plain markup in the pane (inside `#qsBodies`, hidden until built); `QS_SECTIONS` below is the list the tab walks. Each entry has a title, an id, whether it starts
open, and `body()`, the function that gives the element to show. Closing a section only hides it (the markup stays), so a running test and its result survive closing and reopening.
A section's open or closed choice is kept in this browser; a failed read uses the entry's own default.

To add a quick test: put its markup in a `<div id="qsBody-<id>">` inside `#qsBodies` (multiproxy.py's Quick Test pane) and add one line to `QS_SECTIONS`.
"""

CSS = r"""
  /* Quick Test sections (2026-10-05) -- qs-* classes; the chevron is the Overview's own */
  .qs-sec { background:var(--panel); border-radius:8px; margin:16px 0; border:1px solid var(--line) }
  .qs-sec > summary { cursor:pointer; list-style:none; padding:14px 20px; font-size:1rem; font-weight:600; color:var(--text); border-radius:8px }
  .qs-sec > summary::-webkit-details-marker { display:none }
  .qs-sec > summary::before { content:''; display:inline-block; width:6px; height:6px; margin:0 11px 2px 2px; border-right:2px solid var(--muted); border-bottom:2px solid var(--muted); transform:rotate(-45deg); transition:transform 0.15s }
  .qs-sec[open] > summary::before { transform:rotate(45deg) }
  .qs-sec > summary:focus-visible { outline:2px solid var(--accent); outline-offset:-2px }
  .qs-body { padding:0 20px 20px }
  .qs-body > .card, .qs-body > div > .card { border:0; padding:0; margin:0; background:transparent }
"""

JS = r"""// ---------------- Quick Test sections ----------------
// One entry per quick test: id, title, whether it starts open, and the function that gives its body element.
const QS_SECTIONS = [
  { id: 'provider', title: 'Try a provider', open: true, body: () => $('qsBody-provider') },
  { id: 'decision', title: 'Try a Quick decision', open: false, body: () => $('qsBody-decision') },
  { id: 'check', title: 'Check it like SkyrimNet', open: false, body: () => $('qsBody-check') },
];

function qsIsOpen(s) {
  try {
    const v = localStorage.getItem('qsOpen-' + s.id);
    return v === '1' ? true : v === '0' ? false : s.open;
  } catch (e) { return s.open; }
}
function qsToggle(id, el) { try { localStorage.setItem('qsOpen-' + id, el.open ? '1' : '0'); } catch (e) {} }

function qsBuild() {
  const host = $('qsList');
  if (!host || host.childElementCount) return;
  for (const s of QS_SECTIONS) {
    const d = document.createElement('details');
    d.className = 'qs-sec'; d.id = 'qs-' + s.id; d.open = qsIsOpen(s);
    const sum = document.createElement('summary'); sum.textContent = s.title;
    const body = document.createElement('div'); body.className = 'qs-body';
    body.appendChild(s.body());                      // moved, not copied: every control keeps its id and its state
    d.append(sum, body);
    d.addEventListener('toggle', () => qsToggle(s.id, d));
    host.appendChild(d);
  }
  $('qsBodies').remove();
}
qsBuild();
"""
