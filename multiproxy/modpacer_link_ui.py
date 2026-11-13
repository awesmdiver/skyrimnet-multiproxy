"""The "Track with ModPacer" link and its pop-up (4.2.1), built to design/mockup-modpacer-link.html, Revision 2; the words are design/gemini-modpacer-modal-reply.md's.

A plain text link at the right end of the page header line (the line with the title, not the tab row), shown on the Overview tab only. It opens a pop-up that says how to
track MultiProxy in ModPacer. The pop-up is the page's own `op-modal` / `op-modal-box` pattern (same scrim, card and Esc / click-outside closing), at a fixed width.

"Don't show this link again" (ticked, then closed any way) saves `show_modpacer_link: false` and the link goes at once. The way back is a Settings checkbox
(settings_ui.py, Appearance) that is the same saved setting. Plain strings in their own file, spliced into multiproxy.py's page (no doubled braces here).
"""
import json

# Every word of the link and its pop-up, in one place. The steps carry <b> for the button words; nothing else is markup.
WORDS = {
    "link": "Track with ModPacer",
    "title": "Track MultiProxy with ModPacer",
    "intro": "ModPacer is a free tool that checks the fateless.ai Plugin Hub to tell you when your SkyrimNet add-ons have an update. It can keep an eye on MultiProxy for you, too!",
    "steps": [
        "Open ModPacer and press <b>Check now</b>.",
        "Find <b>SkyrimNet MultiProxy</b> in the <b>Not installed</b> list.",
        "Press <b>I have this</b>, then <b>The latest</b> (or type your version and press <b>Save</b>).",
        "When a new version appears under <b>Updates</b>, press <b>Open download page</b>, install it, then press <b>I updated it</b>.",
    ],
    "note": "This requires ModPacer 1.5 or newer.",
    "noModpacer": "Don't have ModPacer yet?",
    "get": "Get ModPacer",
    "getUrl": "https://fateless.ai/plugins/awesmdiver/modpacer",
    "hide": "Don't show this link again",
    "close": "Close",
    "setting": "Show the Track with ModPacer link",
}

# The one saved setting (config.json). Missing means shown; only an explicit false hides it.
CONFIG_KEY = "show_modpacer_link"


def link_html(show: bool) -> str:
    """The header line: the title and, at its right end, the link (hidden for now when the player turned it off; the script shows it on Overview only)."""
    return ('<div class="page-head"><h1>SkyrimNet MultiProxy</h1>'
            '<a class="mp-link lm-link" id="mpLink" href="#" role="button" data-on="' + ("1" if show else "0") + '" hidden onclick="mpOpen(); return false">'
            + WORDS["link"] + '</a></div>')


PANE_HTML = """<div id="mpModal" class="op-modal" style="display:none" onclick="if (event.target === this) mpClose()"></div>"""

CSS = r"""
  /* Track with ModPacer (4.2.1) -- mp-* classes */
  .page-head { display:flex; justify-content:space-between; align-items:baseline; gap:4px 16px; flex-wrap:wrap }
  .page-head h1 { margin:0 0 4px }
  .mp-link { font-size:0.85rem; margin-left:auto; text-decoration:none }
  .mp-link:hover { text-decoration:underline }
  .mp-link[hidden] { display:none }
  .mp-box { width:min(560px, 100%); padding:22px 26px 20px; gap:0 }
  .mp-box h3 { font-size:1.15rem; margin:0 0 8px }
  .mp-box p { margin:0 0 12px; max-width:none }
  .mp-box ol { margin:0 0 12px 22px; padding:0 }
  .mp-box li { margin:6px 0 }
  .mp-box .mp-note { color:var(--muted); font-size:0.88rem; margin:0 0 14px }
  .mp-box .bm-opt { font-size:0.9rem; margin:0 0 12px }
  .mp-foot { display:flex; justify-content:space-between; align-items:center; gap:14px; border-top:1px solid var(--line); padding-top:14px; margin-top:6px }
  .mp-foot .mp-no { color:var(--muted); font-size:0.88rem }
  @media (max-width:520px) {
    .mp-box { padding:18px 18px 16px }
    .mp-foot { flex-direction:column; align-items:flex-start }
    .mp-foot button { align-self:flex-end }
  }
"""

JS = r"""// ---------------- Track with ModPacer ----------------
const MP_WORDS = __MP_WORDS__;
let mpOn = true;           // the saved setting (the link's data-on says it before the first draw)
let mpTab = 'overview';    // the tab on screen: the link shows on Overview only
let mpFrom = null;         // what had focus when the pop-up opened
function mpSync() {
  const a = document.getElementById('mpLink');
  if (!a) return;
  a.hidden = !(mpOn && mpTab === 'overview');
}
function mpInit() {
  const a = document.getElementById('mpLink');
  mpOn = !a || a.getAttribute('data-on') !== '0';
  mpSync();
}
// Called by showMainTab and by Settings when the checkbox changes.
function mpTabChanged(which) { mpTab = which; mpSync(); }
function mpSetOn(on) { mpOn = !!on; mpSync(); }
function mpOpen() {
  const m = document.getElementById('mpModal');
  if (!m) return;
  mpFrom = document.activeElement;
  m.innerHTML = '<div class="op-modal-box mp-box" role="dialog" aria-modal="true" aria-labelledby="mpTitle" tabindex="-1">' +
    '<h3 id="mpTitle">' + esc(MP_WORDS.title) + '</h3>' +
    '<p>' + esc(MP_WORDS.intro) + '</p>' +
    '<ol>' + MP_WORDS.steps.map(s => '<li>' + s + '</li>').join('') + '</ol>' +
    '<p class="mp-note">' + esc(MP_WORDS.note) + '</p>' +
    '<label class="bm-opt"><input type="checkbox" id="mpHide"> <span>' + esc(MP_WORDS.hide) + '</span></label>' +
    '<div class="mp-foot"><span class="mp-no">' + esc(MP_WORDS.noModpacer) + ' <a class="lm-link" href="' + esc(MP_WORDS.getUrl) + '" target="_blank" rel="noopener noreferrer">' + esc(MP_WORDS.get) + '</a></span>' +
    '<button class="primary" id="mpCloseBtn" onclick="mpClose()">' + esc(MP_WORDS.close) + '</button></div></div>';
  m.style.display = 'flex';
  const box = m.firstChild;
  if (box && box.focus) box.focus();
}
async function mpClose() {
  const m = document.getElementById('mpModal');
  if (!m || m.style.display === 'none') return;
  const hide = document.getElementById('mpHide');
  const ticked = !!(hide && hide.checked);
  m.style.display = 'none';
  m.innerHTML = '';
  if (ticked) {
    mpSetOn(false);           // gone at once; the save follows (a failed save puts it back at the next page load, nothing to say)
    try {
      await fetch('/config/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ showModpacerLink: false }) });
      if (typeof stState !== 'undefined' && stState) stState.showModpacerLink = false;
    } catch (e) {}
  }
  const back = document.getElementById('mpLink');
  const target = (back && !back.hidden) ? back : (mpFrom && document.body.contains(mpFrom) ? mpFrom : document.body);
  if (target && target.focus) target.focus();
  mpFrom = null;
}
if (document.addEventListener) {
  document.addEventListener('keydown', e => {
    const m = document.getElementById('mpModal');
    if (!m || m.style.display === 'none') return;
    if (e.key === 'Escape') { e.preventDefault(); mpClose(); return; }
    if (e.key === 'Tab') {          // keep the Tab key inside the pop-up
      const els = Array.from(m.querySelectorAll('input, a[href], button'));
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === m.firstChild)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener('DOMContentLoaded', mpInit);
}
if (typeof document !== 'undefined' && document.readyState && document.readyState !== 'loading') mpInit();
""".replace("__MP_WORDS__", json.dumps(WORDS))
