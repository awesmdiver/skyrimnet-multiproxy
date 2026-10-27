"""The hide button every "Start here" box shares (2026-10-02), built once.

Every Start here box has its own stable name. A box built in the page's scripts is wrapped with `shBox('name', html)`; a box written straight into
the page's HTML carries `data-sh-static="name"` and `shInit()` gives it the same button. Hidden names are kept by MultiProxy itself (config.json,
"hidden_start_here"), not the browser, and the page is handed the list when it is drawn (`SH_HIDDEN`). A hidden box stays in the page with
`display:none`, so "Show all again" brings every box back at once with no redraw. Plain strings spliced into multiproxy.py's page.

A hidden box leaves nothing behind (2026-10-03): when the box was the only visible content of the element around it (the card it sits in, and that card's own
wrapper, and so on up to the tab's page), that element is hidden with it (`sh-wrap-gone`), in one rule for every tab: `shCollapseWrappers()` walks up from every
hidden box and is run after a hide, after Show all again, once at the start and again whenever a script redraws part of the page, so a redrawn tab keeps it too.
The hiding animation collapses the outermost such element together with the box (one smooth shrink, no jump, none under reduced motion).
"""
import re

# The boxes that are always there. The Providers tab also has one per provider, named setup-<provider id>.
FIXED_NAMES = ["get-started", "setup", "speeds", "quick-test", "quick-test-decisions", "quick-test-check", "local-models", "decisions", "decisions-skyrimnet", "other-pcs", "voice", "help", "settings"]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
MAX_HIDDEN = 200


def clean_names(names) -> list:
    """The saved list as a list of valid, unique names (anything else in the file is ignored)."""
    out = []
    for n in names if isinstance(names, list) else []:
        if isinstance(n, str) and NAME_RE.match(n) and n not in out:
            out.append(n)
    return out[:MAX_HIDDEN]


CSS = r"""
  /* Start here boxes: the hide button (2026-10-02) -- sh-* classes */
  .sh-box { position:relative }
  .sh-box > h3, .sh-box > h4 { padding-right:34px }
  .sh-hide { position:absolute; top:6px; right:6px; width:26px; height:26px; padding:0; display:flex; align-items:center; justify-content:center;
    background:transparent; border:0; border-radius:6px; color:var(--muted); cursor:pointer; opacity:0.75 }
  .sh-hide:hover, .sh-hide:focus-visible { color:var(--text); opacity:1; background:var(--panel) }
  .sh-hide:focus-visible { outline:2px solid var(--accent); outline-offset:1px }
  .sh-hide svg { width:16px; height:16px; display:block }
  .sh-gone, .sh-wrap-gone { display:none !important }
  /* the same thing for the common case, with no script and no wait: an element whose every child is a hidden box (a card redrawn around one stays hidden from its first paint) */
  :not([id^="pane"]):has(> .sh-box.sh-gone):not(:has(> :not(.sh-box.sh-gone))) { display:none !important }
  .sh-hiding { overflow:hidden; opacity:0; margin-top:0 !important; margin-bottom:0 !important; padding-top:0 !important; padding-bottom:0 !important; border-width:0 !important;
    height:0 !important; transition:all 0.18s ease }
  @media (prefers-reduced-motion:reduce) { .sh-hiding { transition:none } }
  .sh-guides { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:10px 0 }
"""

JS = r"""// ---------------- Start here boxes: hide, and show all again ----------------
const SH_FIXED = __FIXED__;
const SH_EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 4l16 16"/></svg>';
function shIsHidden(name) { return SH_HIDDEN.indexOf(name) >= 0; }
function shButton(name) {
  return '<button type="button" class="sh-hide" data-sh-hide="' + name + '" title="Hide Start here" aria-label="Hide Start here">' + SH_EYE + '</button>';
}
// A box built in a script: the same box with its name, the hide button in its corner, and gone if it was hidden.
function shBox(name, html) {
  return html.replace(/^<(\w+)\s+class="([^"]*)"([^>]*)>/, function (m, tag, cls, rest) {
    return '<' + tag + ' class="' + cls + ' sh-box' + (shIsHidden(name) ? ' sh-gone' : '') + '" data-sh="' + name + '"' + rest + '>' + shButton(name);
  });
}
// A box written into the page's HTML (data-sh-static="name").
function shInit() {
  document.querySelectorAll('[data-sh-static]').forEach(function (el) {
    const n = el.getAttribute('data-sh-static');
    el.setAttribute('data-sh', n);
    el.classList.add('sh-box');
    if (shIsHidden(n)) el.classList.add('sh-gone');
    el.insertAdjacentHTML('afterbegin', shButton(n));
  });
}
// ---- the wrapper around a hidden box (2026-10-03) ----
// Does this child still show something a player can see? (Not the box being hidden, not an element already hidden, and not an empty element.)
function shHasContent(c, except) {
  if (c === except || c.classList.contains('sh-gone') || c.classList.contains('sh-wrap-gone')) return false;
  if (window.getComputedStyle(c).display === 'none') return false;
  const media = 'img, svg, input, select, textarea, canvas, button, video';
  return !!(c.textContent.trim() || c.matches(media) || c.querySelector(media));
}
// The elements that become empty when `box` goes, innermost first: each is only left holding what is hidden. It stops at the tab's page (paneX), at the page itself,
// and at an element that is a Start here box in its own right.
function shWrapperChain(box) {
  const chain = [];
  let cur = box;
  while (cur.parentElement) {
    const p = cur.parentElement;
    if (p === document.body || (p.id && /^pane/.test(p.id)) || p.hasAttribute('data-sh')) break;
    if (Array.prototype.some.call(p.childNodes, n => n.nodeType === 3 && n.textContent.trim())) break;      // text of its own sits in it
    if (Array.prototype.some.call(p.children, c => shHasContent(c, cur))) break;
    chain.push(p);
    cur = p;
  }
  return chain;
}
function shCollapseWrappers() {
  document.querySelectorAll('.sh-wrap-gone').forEach(function (el) { el.classList.remove('sh-wrap-gone'); });
  document.querySelectorAll('.sh-box.sh-gone').forEach(function (box) {
    shWrapperChain(box).forEach(function (w) { w.classList.add('sh-wrap-gone'); });
  });
}
let shCollapseTimer = null;
function shCollapseSoon() { clearTimeout(shCollapseTimer); shCollapseTimer = setTimeout(shCollapseWrappers, 40); }
function shPost(path, body) {
  return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).catch(function () {});
}
function shTotal() { return SH_FIXED.length + (typeof PROVIDERS !== 'undefined' ? PROVIDERS.length : 0); }
function shHiddenCount() {
  const known = new Set(SH_FIXED);
  if (typeof PROVIDERS !== 'undefined') PROVIDERS.forEach(function (p) { known.add('setup-' + p.id); });
  return SH_HIDDEN.filter(function (n) { return known.has(n); }).length;
}
function shGuidesRow() {
  const n = shHiddenCount();
  // Its own gold section heading (like Appearance and About), then the button and a quiet hint line.
  return '<h3 class="st-sec" id="st-start-here">Start here guides</h3><div class="st-field"><div class="sh-guides">' +
    '<button id="shShowAll"' + (n ? '' : ' disabled') + ' onclick="shShowAll()">Show all again</button></div>' +
    '<div class="st-hint" id="shCount">Hidden: ' + n + ' of ' + shTotal() + '</div></div>';
}
function shRefreshRow() {
  const c = document.getElementById('shCount'), b = document.getElementById('shShowAll');
  if (c) c.textContent = 'Hidden: ' + shHiddenCount() + ' of ' + shTotal();
  if (b) b.disabled = !shHiddenCount();
}
function shHide(name) {
  if (!shIsHidden(name)) SH_HIDDEN.push(name);
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.querySelectorAll('[data-sh="' + name + '"]').forEach(function (el) {
    // The box and whatever it leaves empty shrink together: the outermost of them is the one that animates.
    const chain = shWrapperChain(el);
    const target = chain.length ? chain[chain.length - 1] : el;
    if (reduced) { el.classList.add('sh-gone'); shCollapseWrappers(); return; }
    target.style.height = target.offsetHeight + 'px';
    void target.offsetHeight;
    target.classList.add('sh-hiding');
    setTimeout(function () {
      target.classList.remove('sh-hiding'); target.style.height = '';
      el.classList.add('sh-gone');
      shCollapseWrappers();
    }, 220);
  });
  shPost('/start-here/hide', { name: name });
  shRefreshRow();
}
function shShowAll() {
  SH_HIDDEN.length = 0;
  document.querySelectorAll('[data-sh].sh-gone').forEach(function (el) { el.classList.remove('sh-gone'); });
  shCollapseWrappers();                          // (every wrapper comes back with its box)
  shPost('/start-here/show-all');
  shRefreshRow();
}
document.addEventListener('click', function (e) {
  const b = e.target.closest && e.target.closest('.sh-hide');
  if (b) shHide(b.getAttribute('data-sh-hide'));
});
shInit();
shCollapseWrappers();
// A tab that draws part of itself again (a box built in a script comes back hidden) gets its wrappers collapsed again too.
try { new MutationObserver(shCollapseSoon).observe(document.body, { childList: true, subtree: true }); } catch (e) {}
"""
JS = JS.replace("__FIXED__", str(FIXED_NAMES).replace("'", '"'))
