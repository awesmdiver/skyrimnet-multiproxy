"""The "Jump to" row every long tab shares (2026-10-24; built to design/mockup-routing-jump-links.html, variant B). One row of plain text links, stuck to the top while the tab scrolls;
the section you are in is bold with the accent underline; a click only scrolls (the address stays `#<tab>`, never a section).
A tab adds `jump_ui.row(row id)` under its description and, in its script, `jumpRegister(row id, pane id, links, {minLinks, minTall})`. `links()` gives [[element id, label], ...]
for the sections drawn right now (`jumpIds([...ids])` builds that list from the sections' own headings, so a link always uses the same words as its heading). The row is drawn on
its own about once a second, outside the tab's own redraw, so it never redraws a board under a drag. It shows only when enough links exist (`minLinks`, default 3) and the tab is
taller than `minTall` screens (default 2): a short tab gets no row.
"""

JUMP_CSS = r"""
  /* The Jump to row (design/mockup-routing-jump-links.html, variant B): plain text links in the page's link style, stuck to the top while the tab scrolls; the section you are in is bold with the accent underline. */
  .jump-row { position:sticky; top:0; z-index:30; background:var(--bg); display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 22px; padding:8px 0 10px; margin:0 0 14px; border-bottom:1px solid var(--line); font-size:0.9rem }
  .jump-row[hidden] { display:none }
  .jump-lead { color:var(--soft) }
  .jump-row button.jump-a, .jump-row button.jump-a:hover { background:none; border:0; border-bottom:2px solid transparent; border-radius:0; padding:0; font:inherit; color:var(--accent-text); text-decoration:underline; text-underline-offset:3px; cursor:pointer }
  .jump-row button.jump-a.jump-here { color:var(--text); text-decoration:none; font-weight:600; border-bottom-color:var(--accent) }
  .jump-row button.jump-a:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
"""


def row(row_id: str) -> str:
    """The empty row, hidden until the script finds enough sections."""
    return f'<div id="{row_id}" class="jump-row" role="navigation" aria-label="Jump to a section" hidden></div>'


JUMP_JS = r"""
// ---- the shared Jump to row (jump_ui.py): it only scrolls the page (never the address: `#routing` is the tab) ----
const JUMP_LEAD = 'Jump to:';
const JUMP_ROWS = {};        // row id -> { pane, links, minLinks, minTall, sig, pick }   (pick: the link just clicked stays lit, a section near the end of a short page cannot reach the top, until the player scrolls by hand)
function jumpRegister(rowId, paneId, links, opts) {
  const o = opts || {};
  JUMP_ROWS[rowId] = { pane: paneId, links, minLinks: o.minLinks == null ? 3 : o.minLinks, minTall: o.minTall == null ? 2 : o.minTall, sig: '', pick: '' };
}
// A section's own heading words: the heading itself, or the first heading inside the card (the grey "· name" after a heading is not part of it).
function jumpLabel(el) {
  try {
    const h = /^H[1-6]$/.test(el.tagName || '') ? el : (el.querySelector ? el.querySelector('h2,h3') : null);
    if (!h) return '';
    const c = h.cloneNode(true);
    for (const x of Array.from(c.querySelectorAll ? c.querySelectorAll('.ls-sub') : [])) x.remove();
    return (c.textContent || '').trim();
  } catch (e) { return ''; }
}
function jumpIds(ids) {
  const out = [];
  for (const id of ids) { const el = document.getElementById(id); const t = el ? jumpLabel(el) : ''; if (t) out.push([id, t]); }
  return out;
}
function jumpTall(cfg, el) {
  if (!cfg.minTall) return true;
  const pane = document.getElementById(cfg.pane);
  if (!pane) return false;
  const h = (pane.offsetHeight || 0) - (!el.hidden ? (el.offsetHeight || 0) : 0);
  return h >= cfg.minTall * (window.innerHeight || 0);
}
function jumpDraw(rowId) {
  try {
    const cfg = JUMP_ROWS[rowId], el = document.getElementById(rowId);
    if (!cfg || !el) return;
    const pane = document.getElementById(cfg.pane);
    if (pane && pane.style.display === 'none') return;
    const links = cfg.links(), sig = JSON.stringify(links);
    el.hidden = links.length < cfg.minLinks || !jumpTall(cfg, el);
    if (sig !== cfg.sig) {
      cfg.sig = sig;
      el.innerHTML = '<span class="jump-lead">' + esc(JUMP_LEAD) + '</span>' + links.map(l => '<button type="button" class="jump-a" data-t="' + l[0] + '" onclick="jumpTo(\'' + rowId + '\', \'' + l[0] + '\')">' + esc(l[1]) + '</button>').join('');
    }
    jumpUpdate(rowId);
  } catch (e) {}
}
function jumpTo(rowId, id) {
  try {
    const cfg = JUMP_ROWS[rowId], t = document.getElementById(id), row = document.getElementById(rowId);
    if (!cfg || !t) return;
    cfg.pick = id; jumpUpdate(rowId);
    const top = t.getBoundingClientRect().top + (window.pageYOffset || 0) - (row ? row.offsetHeight : 0) - 8;
    window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  } catch (e) {}
}
// The link for the section you are in: the last section whose top has passed just under the row (the last link once the page is scrolled to the bottom).
function jumpUpdate(rowId) {
  try {
    const cfg = JUMP_ROWS[rowId], el = document.getElementById(rowId);
    if (!cfg || !el || el.hidden) return;
    const pane = document.getElementById(cfg.pane);
    if (pane && pane.style.display === 'none') return;
    const btns = Array.from(el.querySelectorAll('[data-t]'));
    if (!btns.length) return;
    const line = el.getBoundingClientRect().bottom + 24;
    let cur = btns[0].getAttribute('data-t');
    for (const b of btns) { const t = document.getElementById(b.getAttribute('data-t')); if (t && t.getBoundingClientRect().top <= line) cur = b.getAttribute('data-t'); }
    const de = document.documentElement;
    if ((window.innerHeight || 0) + (window.pageYOffset || 0) >= (de.scrollHeight || Infinity) - 2) cur = btns[btns.length - 1].getAttribute('data-t');
    if (cfg.pick && btns.some(b => b.getAttribute('data-t') === cfg.pick)) cur = cfg.pick;
    for (const b of btns) b.classList.toggle('jump-here', b.getAttribute('data-t') === cur);
  } catch (e) {}
}
function jumpDrawAll() { for (const id of Object.keys(JUMP_ROWS)) jumpDraw(id); }
function jumpUpdateAll() { for (const id of Object.keys(JUMP_ROWS)) jumpUpdate(id); }
try { for (const ev of ['wheel', 'touchmove', 'keydown']) window.addEventListener(ev, () => { for (const id of Object.keys(JUMP_ROWS)) JUMP_ROWS[id].pick = ''; }, { passive: true }); } catch (e) {}
try { window.addEventListener('scroll', jumpUpdateAll, { passive: true }); window.addEventListener('resize', jumpDrawAll); } catch (e) {}
try { setInterval(jumpDrawAll, 1000); } catch (e) {}
"""
