"""The dashboard's "Routing" tab (2026-10-02, step 2): the picture of where each SkyrimNet job runs, drag a job to a new model, and one job opened to set up in detail.
Built to design/routing-mockup.html sections 1, 2 and 7 with the changes the Gemini review and step 0 made (see docs/plans/2026-10-02-routing-spec.md). Plain strings in
their own file, spliced into multiproxy.py's page like the other tabs. Everything that came from the server goes through esc() (defined by the Local models card's
script, on the same page) before it reaches HTML; the buttons and drops use positions in the lists the page already holds, never an id written into an attribute.
The data is `GET /config/routing` (routing_view.py) every 2 seconds while the tab is open; every change is one `POST /config/routing` with the whole block.
Later steps (fine-tune per job, what goes first, the time breakdown, Live) are not here.
"""
import jump_ui
import tab_desc_ui

ROUTING_TAB_HTML = """<div class="tab" id="tabRouting" onclick="showMainTab('routing')">Routing</div>"""

ROUTING_CSS = r"""
  /* Routing tab -- rt-* classes; the op-* dots, the lm-* text styles and the card are reused */
  /* The tab uses the whole window whatever the page-width setting is (the picture needs room); every other tab keeps the setting. */
  html.rt-wide[data-width] { --page-w:none; --page-pad:32px }
  .rt-steps { margin:6px 0 0 22px; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px; counter-reset:rt }
  .rt-steps > li { font-size:0.9rem; line-height:1.5 }
  .rt-steps > li::before { counter-increment:rt; content:counter(rt) ". "; font-weight:600 }
  .rt-how h3 { margin:0 0 4px; font-size:1rem }
  /* The places sit side by side on one row and share it by how many model cards each has (--cc: 1, 2 or 3 cards across). Each is only as tall as its own cards, never stretched
     to the tallest one. A window too narrow for the row stacks them, and a row still wider than the window scrolls sideways inside the picture. */
  .rt-lanes { display:flex; flex-wrap:wrap; align-items:flex-start; gap:18px; margin-top:16px; overflow-x:auto }
  .rt-place { flex:var(--cc, 1) 1 0; min-width:calc(var(--cc, 1) * 190px + 38px); border:1px solid var(--line); border-radius:10px; padding:16px 18px; background:var(--bg) }
  .rt-cards { display:grid; grid-template-columns:repeat(var(--cc, 1), minmax(0, 1fr)); gap:12px; margin-top:12px }
  .rt-cards > .rt-ai { margin:0; min-width:0 }
  @media (max-width:720px) { .rt-place { flex-basis:100%; min-width:0 } .rt-cards { grid-template-columns:minmax(0, 1fr) } }
  .rt-place > b { font-size:0.9rem }
  .rt-cap { font-size:0.76rem; color:var(--muted); margin:2px 0 4px }
  .rt-ai { border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:12px 0 0; background:var(--panel) }
  .rt-dec { border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:12px 0 0; background:var(--panel); cursor:pointer }
  .rt-dec:hover { border-color:var(--accent) }
  .rt-ai.rt-over { border-color:var(--accent) }
  .rt-name { font-size:0.85rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .rt-kind { display:block; font-size:0.74rem; color:var(--muted); margin:1px 0 0 17px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .rt-stat { font-size:0.74rem; color:var(--muted); margin:2px 0 8px 17px; line-height:1.4 }
  .rt-flash { font-size:0.74rem; color:var(--err); margin:2px 0 6px 17px; line-height:1.4 }
  .rt-jobs { display:flex; flex-wrap:wrap; gap:6px; margin-left:17px; min-height:26px }
  .rt-job { font:inherit; font-size:0.74rem; border-radius:20px; padding:3px 11px; border:1px solid var(--accent); color:var(--accent); cursor:grab; background:color-mix(in srgb, var(--accent) 10%, var(--bg)); white-space:normal; text-align:left; user-select:none; -webkit-user-select:none }
  .rt-job.rt-back { border-color:color-mix(in srgb, var(--accent) 45%, var(--line)); color:color-mix(in srgb, var(--accent) 60%, var(--muted)); background:none }
  .rt-job.rt-sel { background:var(--accent); color:var(--on-accent); border-color:var(--accent) }
  /* A chip being dragged goes quiet until it is dropped: no fill, lower contrast, the same outline. */
  .rt-job.rt-dragging, .rt-job.rt-sel.rt-dragging, .rt-job.rt-back.rt-dragging, .rt-chip.rt-dragging { background:transparent; color:var(--muted); border-color:var(--line); opacity:0.75 }
  .rt-job:focus-visible, .rt-drop:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
  /* The picture's chips (design/routing-mockup.html, Revision 6): the theme's own colours; the only colour is the small link icon, one colour per job, the same on every chip of
     that job. Hovering or focusing a chip outlines it and its partners in that colour, fades the rest and softly lights the partner's model card. Never a fill. */
  .rt-lanes, .rt-key { --rt-c1:light-dark(#c2610f, #f2994a); --rt-c2:light-dark(#0b7fa8, #56ccf2); --rt-c3:light-dark(#8e3fb0, #bb6bd9); --rt-c4:light-dark(#1f8a50, #6fcf97);
              --rt-c5:light-dark(#c02f2f, #eb5757); --rt-c6:light-dark(#a77c00, #f2c94c); --rt-c7:light-dark(#1a62b0, #2d9cdb); --rt-c8:light-dark(#b8337e, #e86fb0);
              --rt-c9:light-dark(#6b6b6b, #9b9b9b); --rt-c10:light-dark(#0e8577, #7ee0d0); --rt-c11:light-dark(#4a5bd0, #a3b8ff); --rt-c12:light-dark(#6f8a12, #c8e26b) }
  .rt-chip { font:inherit; font-size:0.78rem; display:inline-flex; align-items:center; gap:7px; border-radius:20px; padding:4px 11px 4px 9px; border:1px solid var(--line); background:var(--panel);
             color:var(--text); cursor:grab; text-align:left; user-select:none; -webkit-user-select:none; overflow-wrap:anywhere; transition:opacity .12s, box-shadow .12s, border-color .12s }
  .rt-chip.rt-back { background:transparent; border:1px dashed var(--line2, var(--line)); color:var(--muted) }
  .rt-chip.rt-sel { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) }
  .rt-chip .rt-link { width:13px; height:13px; flex:none; color:var(--jc) }
  .rt-key { display:flex; flex-wrap:wrap; align-items:center; gap:8px 18px; margin:0 0 12px; font-size:0.82rem; color:var(--muted) }
  .rt-key-item { display:inline-flex; align-items:center; gap:7px }
  .rt-key-icons { display:inline-flex; align-items:center; gap:5px; flex:none }
  .rt-key .rt-chip { cursor:default; pointer-events:none; padding:1px 8px 1px 7px; font-size:0.75rem; gap:5px }
  .rt-chip .rt-mp { font-size:0.66rem; letter-spacing:0.04em; color:var(--muted); margin-left:3px }
  .rt-chip .rt-n { font-size:0.72rem; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap }
  .rt-chip:focus-visible { outline:none }
  /* The page's general button:hover fills with the accent; a chip never fills. */
  .rt-chip:hover, .rt-chip:active { background:var(--panel) }
  .rt-chip.rt-back:hover, .rt-chip.rt-back:active { background:transparent }
  .rt-lanes.rt-hovering .rt-chip { opacity:0.35 }
  .rt-lanes.rt-hovering .rt-chip.rt-lit { opacity:1; border-color:var(--jc); box-shadow:0 0 0 1px var(--jc) }
  .rt-ai.rt-partner { box-shadow:0 0 0 1px var(--glow), 0 0 14px -4px var(--glow) }
  .rt-drop { border:1px dashed var(--muted); border-radius:20px; padding:3px 11px; font-size:0.74rem; color:var(--muted) }
  .rt-row { display:flex; align-items:center; gap:14px; flex-wrap:wrap }
  .rt-grow { flex:1; min-width:200px }
  .rt-sub { margin-left:0; padding:12px 0 2px; border-top:1px solid var(--line); margin-top:16px }
  .rt-sub h3 { margin:0 0 6px; font-size:0.9rem; color:var(--accent) }
  .rt-btns { display:flex; gap:6px; flex-wrap:wrap }
  .rt-btns button.ghost.on { border-color:var(--accent); color:var(--accent); background:color-mix(in srgb, var(--accent) 12%, transparent) }
  .rt-table { border-collapse:collapse; width:100%; font-size:0.82rem; margin-top:10px }
  .rt-table th { text-align:left; color:var(--muted); font-weight:500; font-size:0.74rem; padding:6px 10px 6px 0; border-bottom:1px solid var(--line) }
  .rt-table td { padding:9px 10px 9px 0; border-bottom:1px solid var(--line); vertical-align:top }
  .rt-table td.n, .rt-table td.nw { font-variant-numeric:tabular-nums; white-space:nowrap }
  .rt-menu-wrap { position:relative; display:inline-block }
  .rt-menu { position:absolute; right:0; top:100%; margin-top:6px; z-index:20; min-width:230px; background:var(--panel); border:1px solid var(--line2, var(--line)); border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,0.35); padding:6px }
  .rt-menu button { display:block; width:100%; text-align:left; background:transparent; border:0; border-radius:6px; padding:8px 10px; color:var(--text); font-size:0.82rem }
  .rt-menu button:hover:not(:disabled) { background:color-mix(in srgb, var(--text) 8%, transparent) }
  .rt-menu button:disabled { color:var(--muted) }
  .rt-prio { display:flex; flex-wrap:wrap; gap:8px }
  .rt-prio-chip { cursor:grab; display:inline-flex; align-items:center; gap:4px; padding:3px 6px }
  .rt-prio-name { padding:0 4px }
  .rt-arrow { font:inherit; font-size:0.9rem; line-height:1; background:transparent; border:0; color:inherit; padding:2px 6px; border-radius:10px; cursor:pointer }
  .rt-arrow:hover:not(:disabled) { background:color-mix(in srgb, var(--text) 14%, transparent) }
  .rt-arrow:disabled { opacity:0.3; cursor:default }
  .rt-tech { font-size:0.72rem; margin-left:4px }
  .rt-table input[type=text] { width:90px }
  .rt-live-box { margin-top:12px; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 12px; max-height:340px; overflow:auto; font-family:ui-monospace,Consolas,monospace; font-size:0.76rem; line-height:1.7; user-select:text }
  .rt-live-box .rt-think { color:var(--muted); white-space:pre-wrap; margin:2px 0 6px 14px; border-left:2px solid var(--line); padding-left:8px }
  .rt-live-box .rt-bad { color:var(--err) }
  .rt-puzzle { margin-left:5px; font-size:0.85em; opacity:0.75 }
  .rt-job { overflow-wrap:anywhere }
  .rt-aboutrow { margin:2px 0 }
  .rt-note { color:var(--muted); font-size:0.82rem; margin:8px 0 0; line-height:1.5 }
  .rt-joblist { margin:12px 0 0 }
  .rt-jobrow { margin:0 0 10px; line-height:1.5 }
  .rt-jobrow .ghost { margin-left:8px; padding:1px 9px; font-size:0.78rem }
  .rt-jobrow .rt-aboutrow { margin:4px 0 0 }
  .rt-steps { margin:6px 0 10px; padding-left:22px; font-size:0.88rem; line-height:1.6 }
  .rt-exact { display:grid; row-gap:8px; margin:0 0 4px }
  .rt-exact .rt-nameitem { padding:2px 0 }
  .rt-name-chip { display:inline-flex; align-items:center; gap:6px; margin:3px 8px 3px 0; font-size:0.8rem }
  .rt-name-chip code { font-family:ui-monospace,Consolas,monospace; color:var(--accent) }
  .rt-inline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:6px 0 }
  .rt-setup { display:flex; flex-wrap:wrap; gap:18px; margin:14px 0 6px }
  .rt-option { flex:1 1 340px; border:1px solid var(--line); border-radius:10px; padding:16px 18px; background:var(--bg) }
  .rt-option b { font-size:0.95rem }
  .rt-option p { margin:4px 0 6px; font-size:0.88rem; line-height:1.5 }
  .rt-names { display:grid; grid-auto-flow:column; grid-template-rows:repeat(5, auto); grid-auto-columns:max-content; column-gap:44px; row-gap:6px; margin-top:8px; overflow-x:auto }
  .rt-nameitem { display:flex; align-items:center; justify-content:space-between; gap:14px; font-size:0.82rem }
  .rt-nameitem code { font-family:ui-monospace,Consolas,monospace; color:var(--accent) }
"""

ROUTING_PANE_HTML = """
  <div id="paneRouting" style="display:none">
""" + tab_desc_ui.html("routing") + """    """ + jump_ui.row("rtJump") + """
    <div id="rtModalBox" class="op-modal" style="display:none" onclick="if (event.target === this) rtCloseClear()"></div>
    <div id="rtKey" class="rt-key"></div>
    <div id="rtBody"><div class="lm-muted">Looking at your models…</div></div>
    <div id="rtLive"></div>
  </div>
"""

ROUTING_JS = r"""// ---------------- Routing tab ----------------
let rtPDrag = null;
let rtView = null, rtTimer = null, rtLoading = false, rtOpenKey = null, rtErr = '', rtMenu = -1, rtDrag = null, rtOver = -1, rtBusy = false, rtAdding = false, rtLastText = '', rtFlash = null, rtFlashTimer = null, rtTestRun = null, rtAboutOpen = {};

// How to use the tab: steps, at the top (wording from the design side's Gemini pass, 2026-10-02).
const RT_HOW_TITLE = 'How to use this tab';
const RT_HOW = [
  'Drag a job onto a model to have that model answer it.',
  'Click a job to add backups and pick a rule (In this order, Take turns, Fastest right now, Free ones first, or Cheapest first).',
  'The numbers show the order. A job marked 1 is the first choice, and 2 steps in if 1 fails or is busy.',
  'Click "Go back to SkyrimNet\'s model name" to undo a job\'s route.',
];
const RT_SETUP_TITLE = 'Set it up in SkyrimNet';
const RT_SETUP_INTRO = 'MultiProxy needs to know which job is running to send it to the right model. You have two choices.';
const RT_OPT1 = { title: 'Option 1: Automatic (Do nothing)', line: 'You do not need to change anything.', steps: [
  'Leave your current model names in SkyrimNet.',
  'MultiProxy reads the request and figures out the job for you.',
  'Your jobs still go to the models set in SkyrimNet until you drag them somewhere else on this tab.'] };
const RT_OPT2 = { title: 'Option 2: Exact names (For perfect accuracy)', line: 'Use this method to stop MultiProxy from guessing, or to tell apart jobs that look identical.', steps: [
  'Open SkyrimNet and go to AI > Models.',
  'Choose MultiProxy as the provider.',
  'Type the exact name (like `job/dialogue`) into the model box.',
  'If this job does not share a route with anything else, it will not get an answer yet. Open MultiProxy\'s Routing tab and drag the job onto a model to connect it.'] };
const RT_NAMES_HEAD = 'Here is every exact job name from A to Z. Click Copy to grab the one you need.';
const RT_SLOW = [['Off', 0], ['5 seconds', 5], ['8 seconds', 8], ['10 seconds', 10], ['15 seconds', 15]];

// ---- Live (step 6): requests and thinking as they happen. Closed until opened: nothing is streamed to the page until then; nothing is saved anywhere ----
const RT_LIVE_CLOSED = 'Watch requests as they happen, along with the reasoning from the models handling them. Nothing is shown or saved until you open this.';
const RT_LIVE_OPEN_NOTE = '"Thinking" shows a model\'s reasoning as it is written, for the models that share it. Nothing is saved.';
const RT_LIVE_EMPTY = 'Waiting for the next request…';
const RT_LIVE_COLORS = ['#e8a838', '#7cefab', '#22b8cf', '#c792ea', '#ff8a80', '#82aaff', '#f2d16b', '#5eead4'];
let rtLiveOpen = false, rtLiveMode = 'both', rtLivePaused = false, rtLiveSrc = null, rtLiveItems = [], rtLivePending = [], rtLiveDirty = false;
function rtLiveColor(key) { let h = 0; for (const c of String(key || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0; return RT_LIVE_COLORS[h % RT_LIVE_COLORS.length]; }
function rtLiveShouldStream() { const pane = document.getElementById('paneRouting'); return rtLiveOpen && !!pane && pane.style.display !== 'none' && !document.hidden; }
function rtLiveSync() {
  const want = rtLiveShouldStream();
  if (want && !rtLiveSrc && typeof EventSource !== 'undefined') {
    try {
      rtLiveSrc = new EventSource('/live/stream?thinking=' + (rtLiveMode === 'req' ? 0 : 1));
      rtLiveSrc.onmessage = ev => { try { rtLiveTake(JSON.parse(ev.data)); } catch (e) {} };
    } catch (e) { rtLiveSrc = null; }
  } else if (!want && rtLiveSrc) { rtLiveSrc.close(); rtLiveSrc = null; }
}
function rtLiveTake(ev) {
  if (!ev || (ev.k !== 'req' && ev.k !== 'think')) return;
  if (rtLivePaused) { rtLivePending.push(ev); if (rtLivePending.length > 300) rtLivePending.shift(); return; }
  rtLivePut(ev);
  rtLiveDraw();
}
function rtLivePut(ev) {
  if (ev.k === 'think') {
    const last = rtLiveItems.slice().reverse().find(x => x.k === 'think' && x.id === ev.id);
    if (last) last.text = (last.text + ev.text).slice(-4000); else rtLiveItems.push({ k: 'think', id: ev.id, job: ev.job, text: ev.text.slice(-4000) });
  } else if (!rtLiveItems.some(x => x.k === 'req' && x.id === ev.id && x.t === ev.t && x.ai === ev.ai && x.detail === ev.detail)) rtLiveItems.push(ev);   // a page that reconnects is sent the recent lines again
  if (rtLiveItems.length > 200) rtLiveItems.splice(0, rtLiveItems.length - 200);
}
function rtLiveLine(ev) {
  const col = rtLiveColor(ev.jobKey);
  return '<div' + (ev.failed ? ' class="rt-bad"' : '') + '><span style="color:' + col + '">' + esc(ev.job) + '</span> → ' + (ev.handover ? esc(ev.handover) : '') + esc(ev.ai) + (ev.answered ? ' answered' : '') +
    (ev.detail ? ' · ' + esc(ev.detail) : '') + ' <span class="lm-muted">' + esc(ev.t) + '</span></div>';
}
function rtLiveHtmlRows() {
  const rows = rtLiveItems.filter(x => rtLiveMode === 'both' || (rtLiveMode === 'req' && x.k === 'req') || (rtLiveMode === 'think' && x.k === 'think'));
  if (!rows.length) return '<div class="lm-muted">' + esc(RT_LIVE_EMPTY) + '</div>';
  return rows.map(x => x.k === 'req' ? rtLiveLine(x) : '<div><span style="color:' + rtLiveColor(x.job) + '">' + esc(x.job) + '</span> <span class="lm-muted">is thinking</span></div><div class="rt-think">' + esc(x.text) + '</div>').join('');
}
function rtLiveDraw() {
  const box = document.getElementById('rtLiveBox');
  if (!box) return;
  try { const sel = window.getSelection(); if (sel && !sel.isCollapsed && box.contains(sel.anchorNode)) { rtLiveDirty = true; return; } } catch (e) {}   // text being selected to copy stays put
  const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
  box.innerHTML = rtLiveHtmlRows();
  rtLiveDirty = false;
  if (atEnd) box.scrollTop = box.scrollHeight;
}
function rtLiveRender() {
  const el = document.getElementById('rtLive');
  if (!el) return;
  let h = '<div class="card"><div class="rt-row"><div class="rt-grow"><h2 style="margin:0">Live</h2></div>';
  if (rtLiveOpen) {
    h += [['req', 'Requests'], ['think', 'Thinking'], ['both', 'Both']].map(m => '<button type="button" class="ghost' + (rtLiveMode === m[0] ? ' on' : '') + '" onclick="rtLiveSetMode(\'' + m[0] + '\')">' + m[1] + '</button>').join('') +
      '<button type="button" class="ghost' + (rtLivePaused ? ' on' : '') + '" onclick="rtLivePause()">' + (rtLivePaused ? 'Resume' : 'Pause') + '</button>';
  }
  h += '<button type="button" class="ghost" onclick="rtLiveToggle()">' + (rtLiveOpen ? 'Close' : 'Open') + '</button></div>';
  h += rtLiveOpen ? '<div class="rt-live-box" id="rtLiveBox" aria-live="off">' + rtLiveHtmlRows() + '</div><div class="rt-note" style="margin-top:10px">' + esc(RT_LIVE_OPEN_NOTE) + '</div>'
                  : '<div class="rt-note" style="margin-top:8px">' + esc(RT_LIVE_CLOSED) + '</div>';
  el.innerHTML = h + '</div>';
  const box = document.getElementById('rtLiveBox');
  if (box) box.scrollTop = box.scrollHeight;
}
function rtLiveToggle() {
  rtLiveOpen = !rtLiveOpen;
  if (!rtLiveOpen) { rtLiveItems = []; rtLivePending = []; rtLivePaused = false; }
  rtLiveRender();
  rtLiveSync();
}
function rtLiveSetMode(m) {
  const reopen = (m === 'req') !== (rtLiveMode === 'req');
  rtLiveMode = m;
  if (reopen && rtLiveSrc) { rtLiveSrc.close(); rtLiveSrc = null; rtLiveItems = []; }
  rtLiveRender();
  rtLiveSync();
}
function rtLivePause() {
  rtLivePaused = !rtLivePaused;
  if (!rtLivePaused) { rtLivePending.forEach(rtLivePut); rtLivePending = []; }
  rtLiveRender();
}
setInterval(() => { rtLiveSync(); if (rtLiveDirty) rtLiveDraw(); }, 1500);
try { document.addEventListener('visibilitychange', rtLiveSync); } catch (e) {}
try { document.addEventListener('visibilitychange', () => { if (!document.hidden && rtTimer) rtLoad(); }); } catch (e) {}

function rtOpened() {
  try { document.documentElement.classList.add('rt-wide'); } catch (e) {}
  try { history.replaceState(null, '', '#routing'); } catch (e) {}
  rtLoad();
  rtLiveRender();
  rtLiveSync();
  rtJumpDraw();
  if (!rtTimer) rtTimer = setInterval(rtLoad, 2000);
}
function rtClosed() {
  try { document.documentElement.classList.remove('rt-wide'); } catch (e) {}
  if (rtTimer) { clearInterval(rtTimer); rtTimer = null; }
  setTimeout(rtLiveSync, 0);
  try { if (location.hash === '#routing') history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
}
async function rtLoad() {
  if (rtLoading || rtBusy || document.hidden) return;           // (a hidden browser tab asks for nothing; it refreshes the moment it is shown again, below)
  rtLoading = true;
  try {
    const r = await fetch('/config/routing');
    if (!r.ok) throw new Error('routing ' + r.status);
    const text = await r.text();
    const same = text === rtLastText && rtView;                             // nothing changed: leave the page alone (a selection being copied stays)
    rtLastText = text;
    rtView = JSON.parse(text);
    if (rtErr === 'LOAD') rtErr = '';
    if (same) { rtLoading = false; return; }
  } catch (e) { if (!rtView) rtErr = 'LOAD'; }
  rtLoading = false;
  rtRender();
}

// ---- reading the view ----
function rtJobAt(i) { return rtView.jobs[i]; }
function rtJobByKey(k) { return (rtView.jobs || []).find(j => j.key === k) || null; }
function rtAiById(id) { return (rtView.ais || []).find(a => a.id === id) || null; }
function rtKeys(job) { return [job.key].concat(job.sharesWith || []); }
function rtNames(job) { return rtKeys(job).map(k => (rtJobByKey(k) || { label: k }).label); }
// Up to two jobs that move together read "A and B"; three or more read "7 background jobs" (the names are listed once, under the heading in the job's card, not strung together).
function rtGroupLabel(job) {
  const names = rtNames(job);
  if (names.length < 3) return names.join(' and ');
  const noticed = rtKeys(job).some(k => ['dialogue', 'combat', 'decisions', 'vision'].includes(k));
  return names.length + (noticed ? ' jobs' : ' background jobs');
}
// A long raw job name (sever_background) may wrap after an underscore, never in the middle of a word; the text itself, and the hover, keep the real name.
function rtBreakable(label) { return esc(label).replace(/_/g, '_<wbr>'); }
function rtExpand(list) {
  const out = [];
  for (const e of list || []) for (const m of String(e).split(',')) { const t = m.trim(); if (t && !out.includes(t)) out.push(t); }
  return out;
}
function rtState(job) {
  const r = job.route;
  return r ? { routed: true, ais: rtExpand(r.ais), how: r.how, slow: r.slow_limit_s || 0 } : { routed: false, ais: (job.onAis || []).slice(), how: 'order', slow: 0 };
}
function rtSecs(s) { return s < 90 ? s + ' s' : Math.round(s / 60) + ' min'; }
function rtFirstWord(a) { return a && a.firstWord != null ? a.firstWord.toFixed(1) + ' s' : '—'; }
function rtWorked(a) { return a && a.worked != null ? a.worked + '%' : '—'; }
const RT_NO_MODEL_YET = 'No model assigned yet. Add a model, or drag the job onto one.';
const RT_NOT_TRACKED = 'Not tracked here';          // a model SkyrimNet sends to a provider MultiProxy never sees (OpenRouter): its cost is not measured here
function rtSetInWords(a) { return a.setIn + ', set in SkyrimNet'; }
function rtCost(a, job) {
  if (!a) return 'not reported';
  if (a.setIn) return RT_NOT_TRACKED;
  if (a.class === 'free') return 'free';
  if (a.class === 'included') return 'subscription';
  const c = a.costByJob ? a.costByJob[job.key] : null;
  return c != null ? '$' + (c < 0.01 ? c.toFixed(4) : c.toFixed(3)) + ' a line' : 'not reported';
}
function rtDot(a) { return '<span class="op-dot ' + (a ? a.state : 'none') + '"></span>'; }
function rtAiName(id) { const a = rtAiById(id); if (a) return a.name; const p = (rtView.pickable || []).find(x => x.id === id); return p ? p.name : id; }
function rtPlaceName(a) {
  if (!a) return 'Needs attention';
  if (a.setIn) return rtSetInWords(a);
  const p = (rtView.places || []).find(x => x.id === a.place);
  return p ? p.name : '';
}
// What the model's own engine says is wrong with it ("Ollama gemma4:26b: couldn't load (ran out of memory while starting)"), in the page's one warning-line style.
function rtTrouble(a, margin) {
  if (!a || !a.trouble) return '';
  return '<div class="rt-note" style="margin:' + (margin || '3px 0 0 17px') + '"><span class="op-dot warn"></span>' + esc(a.kind + ' ' + a.name + ': ' + a.trouble) + '</div>';
}
function rtFacts(a) {
  if (a.resting != null) return 'resting for ' + rtSecs(a.resting) + ' after repeated failures';
  const bits = [];
  if (a.anyModel) bits.push('any model name works');
  if (a.state === 'none' || a.unchosen) return bits.join(' \u00b7 ');                 // a model nothing answers for says why on its own line (rtKindLine), not again here
  return bits.length ? bits.join(' · ') : '';
}
// "Use <current model> instead" on a card whose model its provider no longer offers: right-aligned, no pop-up (the drag puts it back).
function rtGoneButton(a) {
  if (!a.gone || !a.gone.use) return '';
  return '<div style="text-align:right;margin:8px 0 0"><button type="button" class="ghost" onclick="rtUseInstead(' + JSON.stringify(a.id).replace(/"/g, '&quot;') + ', ' + JSON.stringify(a.gone.use.id).replace(/"/g, '&quot;') + ')">' + esc(RT_GONE_USE(a.gone.use.name)) + '</button></div>';
}
async function rtUseInstead(oldId, newId) {
  try {
    const r = await fetch('/config/routing/replace-model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ old: oldId, new: newId }) });
    if (!r.ok) throw new Error('status ' + r.status);
    rtView = await r.json();
    rtErr = '';
  } catch (e) { rtErr = "Couldn't change that. Is MultiProxy still running?"; }
  rtRender();
}
function rtKeepHtml(a) {                                   // an Ollama model that starts a route: is it loaded, and the one switch to keep it loaded
  const k = (rtView.keepLoaded || {})[a.id];
  if (!k) return '';
  const id = 'rtKeep' + rtView.ais.indexOf(a);
  return (k.words ? '<div class="rt-stat"><span class="op-dot ' + k.dot + '"></span>' + esc(k.words) + (k.problem ? ' <span title="' + esc(k.problem) + '">(why)</span>' : '') + '</div>' : '') +
    '<div class="rt-stat"><label for="' + id + '"><input type="checkbox" id="' + id + '"' + (k.on ? ' checked' : '') + ' onchange="rtSetKeep(' + JSON.stringify(k.name).replace(/"/g, '&quot;') + ', this.checked)"> ' + esc(k.label) + '</label><br>' + esc(k.why) + '</div>';
}
function rtSetKeep(name, on) {
  const s = rtClone(); const off = (s.keep_loaded_off || []).filter(x => x !== name);
  if (!on) off.push(name);
  s.keep_loaded_off = off;
  return rtSave(s, '');
}
function rtKindLine(a) { return a.unchosen ? a.words : a.setIn ? rtSetInWords(a) : a.gone ? a.gone.words : a.missing ? a.missing : a.kind; }       // the provider, or why there is none, on its own line under the name

// One short row per job: the name (bold, only when several move together) and the add-on in muted text; the add-on author's own description stays shut until "What does it do?" is pressed.
function rtAboutOne(k, many) {
  const j = rtJobByKey(k) || { label: k, about: '', mod: null };
  const open = !!rtAboutOpen[k.replace(/[^\w]/g, '')];
  return '<div class="rt-jobrow">' + (many ? '<b>' + esc(j.label) + '</b>' : '') + (j.mod ? (many ? ' ' : '') + '<span class="lm-muted">' + (many ? '' : 'from ') + esc(j.mod) + '</span>' : '') +
    (j.about ? '<button type="button" class="ghost" aria-expanded="' + open + '" onclick="rtToggleAbout(' + "'" + k.replace(/[^\w]/g, '') + "'" + ')">' + (open ? 'Hide' : 'What does it do?') + '</button>' : '') +
    (j.about && open ? '<div class="rt-aboutrow lm-muted">' + esc(j.about) + '</div>' : '') + '</div>';
}
function rtToggleAbout(k) { rtAboutOpen[k] = !rtAboutOpen[k]; rtRender(); }
// "a", "a and b", "a, b, and c"
function rtAndList(l) { return l.length < 3 ? l.join(' and ') : l.slice(0, -1).join(', ') + ', and ' + l[l.length - 1]; }

// ---- changing things (always the whole block, one POST) ----
function rtClone() { return JSON.parse(JSON.stringify(rtView.settings)); }
function rtWith(job, ais, how, slow) {
  const s = rtClone();
  for (const k of rtKeys(job)) {
    if (ais.length) s.routes[k] = { ais: ais, how: how, slow_limit_s: slow || null };
    else delete s.routes[k];
  }
  return s;
}
function rtFail(where, text) {
  rtFlash = { where: where || { job: true }, text: text };
  if (rtFlashTimer) clearTimeout(rtFlashTimer);
  rtFlashTimer = setTimeout(() => { rtFlash = null; rtRender(); }, 6000);                    // it goes away by itself
}
async function rtSave(settings, where) {
  if (JSON.stringify(settings) === JSON.stringify(rtView.settings)) { rtMenu = -1; rtAdding = false; rtRender(); return; }   // nothing would change: do nothing
  rtBusy = true; rtErr = '';
  try { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); } catch (e) {}   // a chosen drop-down must not keep the page from redrawing
  try { rtView = await lmPost('/config/routing', settings); rtLastText = JSON.stringify(rtView); }
  catch (e) { rtFail(where, e.message); }
  rtBusy = false; rtMenu = -1; rtAdding = false;
  rtRender();
}
function rtMakeFirst(job, aiId) {
  const st = rtState(job);
  if (!st.routed && st.ais.length === 1 && st.ais[0] === aiId) return;                 // it already answers there: nothing changes, nothing happens
  const ais = st.routed ? [aiId].concat(st.ais.filter(a => a !== aiId)) : [aiId];
  return rtSave(rtWith(job, ais, st.how, st.slow), { ai: aiId });
}
function rtMove(job, i, d) {
  const st = rtState(job), ais = st.ais.slice(), j = i + d;
  if (j < 0 || j >= ais.length) return;
  [ais[i], ais[j]] = [ais[j], ais[i]];
  return rtSave(rtWith(job, ais, st.how, st.slow), { job: true });
}
function rtRemove(job, i) {
  const st = rtState(job), ais = st.ais.slice();
  ais.splice(i, 1);
  return rtSave(rtWith(job, ais, st.how, st.slow), { job: true });
}
function rtAdd(job, id) {
  if (!id) return;
  const st = rtState(job);
  if (st.ais.includes(id)) return;
  return rtSave(rtWith(job, st.ais.concat([id]), st.how, st.slow), { job: true });
}
function rtSetHow(job, how) { const st = rtState(job); return rtSave(rtWith(job, st.ais, how, st.slow), { job: true }); }
function rtSetSlow(job, secs) { const st = rtState(job); return rtSave(rtWith(job, st.ais, st.how, Number(secs) || 0), { job: true }); }
function rtSetRest(field, value) { const s = rtClone(); s.rest[field] = Number(value); return rtSave(s, { job: true }); }
function rtGoBack(job) { return rtSave(rtWith(job, [], 'order', 0), { job: true }); }
function rtCopy(text, btn) {
  try { navigator.clipboard.writeText(text); } catch (e) {}
  if (btn) { const was = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = was; }, 1500); }   // said on the button itself, no redraw
}

// ---- opening, dragging, menus ----
function rtOpenJob(i) { rtOpenKey = rtJobAt(i).key; rtMenu = -1; rtFlash = null; rtRender(); try { document.getElementById('rtJobCard').scrollIntoView({ block: 'nearest' }); } catch (e) {} }
function rtCloseJob() { rtOpenKey = null; rtMenu = -1; rtRender(); }
function rtToggleMenu(i, ev) { ev.stopPropagation(); rtMenu = rtMenu === i ? -1 : i; rtRender(); }
function rtDragStart(ev, i) {
  rtDrag = i;
  try { ev.currentTarget.classList.add('rt-dragging'); } catch (e) {}                       // muted (no fill) until it is dropped
  try { ev.dataTransfer.setData('text/plain', rtJobAt(i).key); ev.dataTransfer.effectAllowed = 'move'; } catch (e) {}
}
function rtDragEnd() {
  rtDrag = null; rtOver = -1;
  document.querySelectorAll('.rt-dragging, .rt-over').forEach(e => e.classList.remove('rt-dragging', 'rt-over'));    // back to normal even if a text selection keeps the page from redrawing
  rtRender();
}
// `i` is the model's place in `rtView.ais`, which is NOT its place among the cards on the page (the cards sit in columns by place), so the highlight goes on the card the event
// came from (`ev.currentTarget`), never on "the i-th card in the page". Only a model that can answer a job takes a drop: not one under Needs attention, not one SkyrimNet sends
// somewhere else, and not a job's own name.
function rtCanTake(a) { return !!a && a.place !== 'lost' && a.place !== 'elsewhere' && a.place !== 'unchosen'; }
function rtClearOver() { document.querySelectorAll('.rt-over').forEach(e => e.classList.remove('rt-over')); }
function rtOverAi(ev, i) {
  if (rtDrag === null || !rtCanTake(rtView.ais[i])) return;
  ev.preventDefault();
  if (rtOver !== i) { rtClearOver(); rtOver = i; try { ev.currentTarget.classList.add('rt-over'); } catch (e) {} }
}
function rtLeaveAi(ev, i) {
  const el = ev.currentTarget;
  if (el && ev.relatedTarget && el.contains && el.contains(ev.relatedTarget)) return;        // moving onto something inside the same card is not leaving it
  if (el) el.classList.remove('rt-over');
  if (rtOver === i) rtOver = -1;
}
function rtDropAi(ev, i) {
  ev.preventDefault();
  const job = rtDrag === null ? null : rtJobAt(rtDrag), ai = rtView.ais[i];
  rtDrag = null; rtOver = -1;
  rtClearOver();
  if (job && rtCanTake(ai)) return rtMoveJob(job, ai.id);
  rtRender();
}
// ---- dropping a job on another model: move it, and keep the old model as a backup only when the player says so ----
const RT_KEEP_YES = 'Yes, keep it';
const RT_KEEP_NO = 'No, just move it';
let rtPendingMove = null;
// A model that is failing (amber or red dot, resting, or not reachable) is never offered as a backup.
function rtFailing(id) { const a = rtAiById(id); return !a || a.state !== 'on' || a.resting != null || !!a.trouble; }
function rtKeepTitle(names) {
  const who = names.length === 1 ? names[0] : names.length === 2 ? names[0] + ' and ' + names[1] : names[0] + ' and ' + (names.length - 1) + ' others';
  return 'Keep ' + who + (names.length === 1 ? ' as a backup?' : ' as backups?');
}
function rtMoveJob(job, aiId) {
  const st = rtState(job);
  if (st.ais.includes(aiId) || !st.routed) return rtMakeFirst(job, aiId);              // already in its list: only the order changes. Not routed yet: only that model, as before
  const keepable = st.ais.filter(id => !rtFailing(id));
  const mode = (rtView.settings && rtView.settings.on_move) || 'ask';
  if (!keepable.length || mode === 'move') return rtApplyMove(job, aiId, []);
  if (mode === 'keep') return rtApplyMove(job, aiId, keepable);
  rtAskKeep(job, aiId, keepable);
}
function rtApplyMove(job, aiId, keepIds) {
  const st = rtState(job);
  return rtSave(rtWith(job, [aiId].concat(keepIds), st.how, st.slow), { ai: aiId });
}
function rtAskKeep(job, aiId, keepIds) {
  const box = document.getElementById('rtModalBox');
  if (!box) return rtApplyMove(job, aiId, []);
  rtPendingMove = { job: job.key, ai: aiId, keep: keepIds };
  box.innerHTML = '<div class="vc-dialog" role="dialog" aria-modal="true" aria-labelledby="rtKeepTitle" tabindex="-1" id="rtKeepDialog"><h3 id="rtKeepTitle">' + esc(rtKeepTitle(keepIds.map(rtAiName))) + '</h3>' +
    '<div class="vc-ft"><button type="button" class="ghost" id="rtKeepNo" onclick="rtAnswerKeep(false)">' + esc(RT_KEEP_NO) + '</button><button type="button" class="primary" id="rtKeepYes" onclick="rtAnswerKeep(true)">' + esc(RT_KEEP_YES) + '</button></div></div>';
  box.style.display = '';
  const y = document.getElementById('rtKeepYes');
  if (y) y.focus();
}
function rtAnswerKeep(keep) {
  const p = rtPendingMove;
  rtPendingMove = null;
  rtCloseClear();
  const job = p && rtJobByKey(p.job);
  if (!job) { rtRender(); return; }
  return rtApplyMove(job, p.ai, keep ? p.keep : []);
}

// ---- drawing ----
function rtRender() {
  const box = document.getElementById('rtBody');
  if (!box) return;
  const ae = document.activeElement;
  if (rtDrag !== null || rtPDrag !== null) return;                                              // a drag in progress would be cut off by a redraw
  if (ae && ae.closest && ae.closest('#rtBody') && (ae.tagName === 'SELECT' || ae.tagName === 'INPUT')) return;   // an open drop-down would close if the page were rebuilt under it
  try { const sel = window.getSelection(); if (sel && !sel.isCollapsed && box.contains(sel.anchorNode)) return; } catch (e) {}   // text being selected to copy stays put
  if (!rtView) { box.innerHTML = rtErr === 'LOAD' ? '<div class="lm-err">Couldn\'t read the routing. Is MultiProxy still running?</div>' : '<div class="lm-muted">Looking at your models…</div>'; rtJumpDraw(); return; }
  let h = '<div class="card rt-how" id="rtPickCard" onclick="if (rtMenu >= 0) { rtMenu = -1; rtRender(); }"><h2 style="margin:0 0 10px">' + esc(RT_PICK_TITLE) + '</h2><h3>' + esc(RT_HOW_TITLE) + '</h3><ol class="rt-steps">' +
    RT_HOW.map(t => '<li>' + esc(t) + '</li>').join('') + '</ol>';
  if (rtErr && rtErr !== 'LOAD') h += '<div class="lm-err" style="margin:0 0 10px">' + esc(rtErr) + '</div>';
  h += rtPicture();
  h += '<div style="margin-top:12px"><button type="button" class="ghost" id="rtClearAllBtn" onclick="rtAskClear()"' + (rtRouteCount() ? '' : ' disabled title="' + esc(RT_CLEAR_NONE_TIP) + '"') + '>' + esc(RT_CLEAR_BTN) + '</button></div></div>';
  h += rtPriorityCard();
  if (rtOpenKey && rtJobByKey(rtOpenKey)) h += rtJobCard(rtJobByKey(rtOpenKey));
  h += rtUseCard();
  box.innerHTML = h;
  rtJumpDraw();
}
// ---- the Jump to row: it only scrolls the page (never the address: `#routing` is the tab) and is drawn on its own, so it never redraws the board under a drag ----
const RT_PICK_TITLE = 'Pick a model for each job';
const RT_JUMP = [['rtPickCard', RT_PICK_TITLE], ['rtFirstCard', 'Priority when busy'], ['rtJobCard', null], ['rtUseSec', RT_SETUP_TITLE], ['rtLive', 'Live']];
function rtJumpLinks() {
  const links = [];
  for (const [id, label] of RT_JUMP) {
    if (id === 'rtJobCard') { const job = rtView && rtOpenKey && rtJobByKey(rtOpenKey); if (job) links.push([id, 'This job: ' + rtGroupLabel(job)]); continue; }
    if (document.getElementById(id)) links.push([id, label]);
  }
  return links;
}
// (drawing, scrolling and the highlight are the shared row's: jump_ui.py)
if (typeof jumpRegister === 'function') jumpRegister('rtJump', 'paneRouting', rtJumpLinks, { minLinks: 2, minTall: 0 });
function rtJumpDraw() { if (typeof jumpDraw === 'function') jumpDraw('rtJump'); }
const RT_LINK_SVG = '<svg class="rt-link" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L11.5 4.4"/><path d="M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 0 0 7.07 7.07l1.33-1.33"/></svg>';
// A job chip's hover says three things in this order: where the choice was made, its place, and what to do.
const RT_TIP_FIRST = 'First choice. ';
const RT_TIP_BACK = 'Backup: steps in if the first choice fails or is busy. ';
const RT_TIP_MP = 'Changed here in MultiProxy. ';
const RT_TIP_SN = 'Set in SkyrimNet. ';
const RT_TIP_SN_ADDON = "Set on SkyrimNet's Models page. ";
const RT_TIP_DO = 'Click to change it.';
const RT_GONE_USE = m => 'Use ' + m + ' instead';
const RT_DEC_CAPTION = 'Decisions tab';
// A chip for add-on jobs names each job and where its model is chosen, one per line, before the usual sentence.
function rtTipJobs(job) {
  const lines = [];
  for (const k of rtKeys(job)) {
    const j = rtJobByKey(k);
    if (!j || !j.puzzle) continue;
    lines.push(j.label + (j.mod ? ' (' + j.mod + ')' : ''));
  }
  return lines.length ? lines.join('\n') + '\n' : '';
}
const RT_PICTURE_LINE = "Every job starts on the models you picked in SkyrimNet's Models page. Drag a job to change it here; your new choices get a small MP tag.";
const RT_CLEAR_NONE_TIP = 'Every job already follows what you picked in SkyrimNet.';
const RT_COLOURS = 12;
function rtGroupKey(job) { return rtKeys(job).slice().sort().join('+'); }
// A job's colour comes from its key, so it is the same every day and every reload. Two jobs that share a model on screen never share a colour (while there are colours left).
function rtJobColours() {
  const groups = {};
  for (const job of rtView.jobs || []) { const gk = rtGroupKey(job); if (!groups[gk]) groups[gk] = { gk: gk, ais: new Set() }; for (const a of job.onAis || []) groups[gk].ais.add(a); }
  const out = {}, list = Object.keys(groups).sort();
  for (const gk of list) {
    let h = 0; for (const c of gk) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    const used = new Set();
    for (const other of list) if (out[other] !== undefined && [...groups[other].ais].some(a => groups[gk].ais.has(a))) used.add(out[other]);
    let i = h % RT_COLOURS;
    for (let t = 0; t < RT_COLOURS && used.has(i); t++) i = (i + 1) % RT_COLOURS;
    out[gk] = used.has(i) ? h % RT_COLOURS : i;
  }
  return out;
}
function rtLink(el) {
  const g = el.getAttribute('data-g'), lanes = document.querySelector('.rt-lanes');
  if (!g || !lanes) return;
  lanes.classList.add('rt-hovering');
  lanes.querySelectorAll('.rt-chip').forEach(c => {
    if (c.getAttribute('data-g') !== g) return;
    c.classList.add('rt-lit');
    const card = c !== el && c.closest ? c.closest('.rt-ai') : null;
    if (card) { card.style.setProperty('--glow', c.style.getPropertyValue('--jc')); card.classList.add('rt-partner'); }
  });
}
function rtUnlink() {
  const lanes = document.querySelector('.rt-lanes');
  if (!lanes) return;
  lanes.classList.remove('rt-hovering');
  lanes.querySelectorAll('.rt-lit').forEach(c => c.classList.remove('rt-lit'));
  lanes.querySelectorAll('.rt-partner').forEach(c => c.classList.remove('rt-partner'));
}
function rtChips(ai, colours) {
  const seen = {};
  let h = '';
  (rtView.jobs || []).forEach((job, ji) => {
    const pos = (job.onAis || []).indexOf(ai.id);
    if (pos < 0) return;
    const gk = rtGroupKey(job);
    if (seen[gk]) return;
    seen[gk] = true;
    const puzzle = rtKeys(job).some(k => (rtJobByKey(k) || {}).puzzle) ? '<span class="rt-puzzle" aria-hidden="true">\uD83E\uDDE9</span>' : '';
    h += '<button type="button" class="rt-chip' + (pos > 0 ? ' rt-back' : '') + (rtOpenKey === job.key ? ' rt-sel' : '') + '" draggable="true" data-g="' + esc(gk) + '" style="--jc:var(--rt-c' + ((colours[gk] || 0) + 1) + ')" ' +
      'ondragstart="rtDragStart(event, ' + ji + ')" ondragend="rtDragEnd()" onclick="rtOpenJob(' + ji + ')" onmouseenter="rtLink(this)" onmouseleave="rtUnlink()" onfocus="rtLink(this)" onblur="rtUnlink()" ' +
      'title="' + esc(rtTipJobs(job) + (job.route ? RT_TIP_MP : rtTipJobs(job) ? RT_TIP_SN_ADDON : RT_TIP_SN) + (pos > 0 ? RT_TIP_BACK : RT_TIP_FIRST) + RT_TIP_DO) + '">' + RT_LINK_SVG + '<span class="rt-n">' + (pos > 0 ? '\u21aa ' : '') + (pos + 1) + (job.route ? ' <span class="rt-mp">MP</span>' : '') + '</span><span>' + rtBreakable(rtGroupLabel(job)) + puzzle + '</span></button>';
  });
  return h || '<span class="rt-drop">drop a job here</span>';
}
const RT_FIRST_HINT = 'When a model is busy, the game\'s own jobs go first.';
const RT_FIRST_CHANGE = 'Change the order';
const RT_FIRST_DRAG = 'Drag to reorder.';
let rtPrioOpen = false;
function rtPrioToggle() { rtPrioOpen = !rtPrioOpen; rtRender(); }
const RT_FIRST_NOTE = 'A background job already writing is never cut off. It just won\'t start ahead of waiting dialogue.';
const RT_FIRST_TITLE = 'Priority when busy';
const RT_AGING = [3, 5, 10, 20, 30];

// ---- what goes first ----
function rtPrioItems() {
  const shown = {};
  for (const j of rtView.jobs || []) shown[j.key] = j;
  return (rtView.priority || []).filter(k => shown[k]);
}
function rtPrioSave(items) {
  const keep = (rtView.priority || []).filter(k => items.indexOf(k) < 0);          // jobs not drawn here keep their saved places, after the ones that are
  const s = rtClone();
  s.priority = items.concat(keep);
  return rtSave(s, '');
}
function rtPrioMove(i, d) {
  const items = rtPrioItems(), j = i + d;
  if (j < 0 || j >= items.length) return;
  const it = items.splice(i, 1)[0];
  items.splice(j, 0, it);
  return rtPrioSave(items);
}
function rtPDragStart(ev, i) { rtPDrag = i; try { ev.dataTransfer.setData('text/plain', String(i)); ev.dataTransfer.effectAllowed = 'move'; } catch (e) {} }
function rtPDragEnd() { rtPDrag = null; rtRender(); }
function rtPOver(ev) { if (rtPDrag !== null) ev.preventDefault(); }
function rtPDrop(ev, j) {
  ev.preventDefault();
  const i = rtPDrag;
  rtPDrag = null;
  const items = rtPrioItems();
  if (i === null || i === j || i < 0 || i >= items.length) { rtRender(); return; }
  const it = items.splice(i, 1)[0];
  items.splice(j, 0, it);
  return rtPrioSave(items);
}
function rtSetAging(v) { const s = rtClone(); s.aging_s = Number(v); return rtSave(s, ''); }
function rtPriorityCard() {
  const items = rtPrioItems();
  if (!items.length) return '';
  const aging = rtView.settings.aging_s;
  let h = '<div class="card" id="rtFirstCard"><h2 style="margin:0 0 4px">' + esc(RT_FIRST_TITLE) + '</h2><p class="lm-hint">' + esc(RT_FIRST_HINT) + ' <a class="lm-link" role="button" tabindex="0" aria-expanded="' + (rtPrioOpen ? 'true' : 'false') +
    '" onclick="rtPrioToggle()" onkeydown="if (event.key === \'Enter\' || event.key === \' \') { event.preventDefault(); rtPrioToggle(); }">' + esc(RT_FIRST_CHANGE) + '</a></p>';
  if (!rtPrioOpen) return h + '</div>';
  h += '<div class="rt-prio" role="list">';
  items.forEach((k, i) => {
    const lab = (rtJobByKey(k) || { label: k }).label;
    h += '<span class="rt-job rt-prio-chip' + (i < 4 ? '' : ' rt-back') + '" role="listitem" draggable="true" ondragstart="rtPDragStart(event, ' + i + ')" ondragend="rtPDragEnd()" ondragover="rtPOver(event)" ondrop="rtPDrop(event, ' + i + ')">' +
      '<button type="button" class="rt-arrow" aria-label="Move ' + esc(lab) + ' earlier"' + (i === 0 ? ' disabled' : '') + ' onclick="rtPrioMove(' + i + ', -1)">‹</button>' +
      '<span class="rt-prio-name">' + (i + 1) + ' ' + esc(lab) + '</span>' +
      '<button type="button" class="rt-arrow" aria-label="Move ' + esc(lab) + ' later"' + (i === items.length - 1 ? ' disabled' : '') + ' onclick="rtPrioMove(' + i + ', 1)">›</button></span>';
  });
  h += '</div><p class="rt-note">' + esc(RT_FIRST_NOTE) + '</p>' +
    '<div class="rt-inline"><label for="rtAging">' + esc(RT_FIRST_DRAG) + ' A waiting job moves up one place every</label><select id="rtAging" onchange="rtSetAging(this.value)">' +
    RT_AGING.map(n => '<option value="' + n + '"' + (n === aging ? ' selected' : '') + '>' + n + ' seconds</option>').join('') + (RT_AGING.indexOf(aging) < 0 ? '<option value="' + aging + '" selected>' + aging + ' seconds</option>' : '') +
    '</select><span style="margin-left:-8px">, so nothing waits forever.</span></div></div>';
  return h;
}
function rtPicture() {
  const jobs = rtView.jobs || [];
  if (!jobs.length) return '<p class="rt-note">No jobs yet. These appear once SkyrimNet sends its first request' + (rtView.skyrimNetLinked ? '' : ', or when you link MultiProxy to your Skyrim folder on the Speeds tab') + '.</p>';
  const colours = rtJobColours();
  let h = '<p class="rt-note" style="margin:14px 0 10px">' + esc(RT_PICTURE_LINE) + '</p><div class="rt-lanes">';
  for (const pl of rtView.places || []) {
    const ais = (rtView.ais || []).map((a, i) => [a, i]).filter(x => x[0].place === pl.id);
    h += '<div class="rt-place" style="--cc:' + rtCardsAcross(ais.length) + '"><b>' + esc(pl.name) + '</b><div class="rt-cap">' + esc(pl.caption) + '</div>';
    const dec = rtView.decisions && rtView.decisions.place === pl.id ? rtView.decisions : null;
    if (!ais.length && !dec) h += '<div class="rt-note">Nothing here yet.</div>';
    else h += '<div class="rt-cards">';
    for (const [a, i] of ais) {
      h += '<div class="rt-ai"' + (rtCanTake(a) ? ' ondragover="rtOverAi(event, ' + i + ')" ondragleave="rtLeaveAi(event, ' + i + ')" ondrop="rtDropAi(event, ' + i + ')"' : '') + '>' +
        '<div class="rt-name" title="' + esc(a.name) + '">' + rtDot(a) + esc(a.name) + '</div><div class="rt-kind" title="' + esc(rtKindLine(a)) + '">' + esc(rtKindLine(a)) + '</div>' + (rtFacts(a) ? '<div class="rt-stat">' + esc(rtFacts(a)) + '</div>' : '') + rtTrouble(a, '0 0 8px 17px') + rtKeepHtml(a) +
        (rtFlash && rtFlash.where.ai === a.id ? '<div class="rt-flash" role="status">' + esc(rtFlash.text) + '</div>' : '') + rtGoneButton(a) + '<div class="rt-jobs">' + rtChips(a, colours) + '</div></div>';
    }
    // Where Decisions really run: a read-only card (not a route, nothing to drop on it); a click opens the Decisions tab.
    if (dec) h += '<div class="rt-dec" role="button" tabindex="0" onclick="showMainTab(\'decisions\')" onkeydown="if (event.key === \'Enter\') showMainTab(\'decisions\')">' +
      '<div class="rt-name" title="' + esc(dec.name) + '"><span class="op-dot ' + dec.state + '"></span>' + esc(dec.name) + '</div><div class="rt-kind">' + esc(RT_DEC_CAPTION) + '</div><div class="rt-stat">' + esc(dec.word) + '</div></div>';
    h += (ais.length || dec ? '</div>' : '') + '</div>';
  }
  return h + '</div>';
}
// How many model cards sit across in one place: a place with a few stays one card wide, one with many fills left to right, then down (and takes more of the row).
function rtCardsAcross(n) { return n <= 2 ? 1 : n <= 8 ? 2 : 3; }
function rtJobCard(job) {
  const st = rtState(job), keys = rtKeys(job);
  let h = '<div class="card" id="rtJobCard" onclick="if (rtMenu >= 0) { rtMenu = -1; rtRender(); }"><div class="rt-row"><div class="rt-grow"><h2 style="margin:0">' + esc(rtGroupLabel(job)) + '</h2>' + (rtFlash && rtFlash.where.job ? '<div class="rt-flash" style="margin-left:0" role="status">' + esc(rtFlash.text) + '</div>' : '') + (keys.length > 1 ? '<div class="lm-muted">These jobs look the same to MultiProxy, so they move together.</div>' : (job.namedExactly ? '<div class="lm-muted">Named exactly in SkyrimNet, so it has its own route.</div>' : '')) + '<div class="rt-joblist">' +
    keys.map(k => rtAboutOne(k, keys.length > 1)).join('') + '</div></div><button class="ghost" onclick="rtCloseJob()">Close</button></div>';
  const pinned = keys.length === 1 && job.namedExactly;          // already named exactly in SkyrimNet: nothing left to pin, so no steps
  h += '<div class="rt-sub">' + (pinned ? '' : '<h3>' + (keys.length > 1 ? 'Move one job on its own' : 'Pin this job by name') + '</h3><ol class="rt-steps"><li>In SkyrimNet, open <b>AI &gt; Models</b> and set ' + (keys.length > 1 ? 'that job\'s' : 'the job\'s') + ' model to its exact name below.</li><li>Come back here and pick where it goes.</li></ol>') +
    '<div class="rt-exact">' + keys.map(k => '<div class="rt-nameitem"><code>' + esc('job/' + k) + '</code><button type="button" class="ghost" onclick="rtCopy(' + "'" + 'job/' + k.replace(/[^\w]/g, '') + "'" + ', this)">Copy</button></div>').join('') + '</div></div>';
  h += '<div class="rt-sub"><h3>Who answers</h3><div class="rt-btns">' + (rtView.hows || []).map(w => '<button type="button" class="ghost' + (w.id === st.how ? ' on' : '') + '" onclick="rtSetHow(rtJobByKey(rtOpenKey), \'' + w.id + '\')">' + esc(w.label) + '</button>').join('') + '</div>';
  if (!st.routed) {
    // What SkyrimNet itself says about each job: a model it sends to OpenRouter (or another provider MultiProxy does not carry) never reaches MultiProxy, so the tab says so, by name.
    // One muted line per place the jobs go (not one per job), the models only in the hover.
    const places = {};
    for (const k of keys) {
      const jj = rtJobByKey(k) || {}, el = jj.elsewhere || [], all = el.length > 0 && el.length >= (jj.models || []).length;
      for (const e of el) {
        const p = places[e.provider] = places[e.provider] || { whole: [], part: [], models: [] };
        const name = jj.label || k, only = el.every(x => x.provider === e.provider);
        const list = all && only ? p.whole : p.part;
        if (!list.includes(name)) list.push(name);
        if (!p.models.includes(e.model)) p.models.push(e.model);
      }
    }
    const sent = Object.keys(places).length;
    for (const prov of Object.keys(places)) {
      const p = places[prov], items = p.whole.concat(p.part.length ? ['part of ' + rtAndList(p.part)] : []);
      h += '<div class="rt-note" style="margin-top:14px" title="' + esc((p.models.length === 1 ? 'Model: ' : 'Models: ') + p.models.join(', ') + ". MultiProxy doesn't carry " + prov + '.') + '">' + esc('Sent to ' + prov + ', so not through MultiProxy: ') + esc(rtAndList(items)) + '.</div>';
    }
    const mine = (job.models || []).filter(m => !(job.elsewhere || []).some(e => e.model === m) && !/^job\//i.test(m));
    if ((job.models || []).some(m => /^job\//i.test(m))) h += '<div class="rt-note">Named exactly. No route yet: answered like the group.</div>';
    else if (mine.length) h += '<div class="rt-note">No route yet: the model name SkyrimNet sends answers this job (' + esc(mine.join(', ')) + '). Change anything below, or drag the job to a model, to start one.</div>';
    else if (sent) h += '<div class="rt-note">To route it here, set the job\'s provider to MultiProxy in SkyrimNet, or add a model below and drag the job to it.</div>';
    else h += '<div class="rt-note">No route yet: the model name SkyrimNet sends answers this job. Change anything below, or drag the job to a model, to start one.</div>';
  }
  if (st.routed && st.ais.length === 1) {       // one AI and it is failing: say there is nothing to fall back on (the add button is just below)
    const only = rtAiById(st.ais[0]);
    if (only && (only.trouble || (only.calls >= 4 && only.worked != null && only.worked < 80))) h += '<div class="rt-note"><span class="op-dot warn"></span>This route has no backup. Use + Add a model below to add one.</div>';
  }
  if (!st.ais.length) h += '<div class="rt-note">' + esc(RT_NO_MODEL_YET) + '</div>';
  h += '<table class="rt-table"><tr><th></th><th>Model</th><th>Where</th><th>First word</th><th>Succeeded</th><th>Cost</th><th></th></tr>';
  st.ais.forEach((id, i) => {
    const a = rtAiById(id);
    h += '<tr><td>' + (i + 1) + '</td><td class="nw">' + rtDot(a) + esc(rtAiName(id)) + (a ? '<div class="lm-muted" style="margin-left:17px">' + esc(a.kind) + '</div>' : '') + (a && a.resting != null ? '<div class="lm-muted">resting for ' + esc(rtSecs(a.resting)) + '</div>' : '') + rtTrouble(a) +
      '</td><td>' + esc(rtPlaceName(a)) + '</td><td class="n">' + esc(rtFirstWord(a)) + '</td><td class="n">' + esc(rtWorked(a)) + '</td><td class="n">' + esc(rtCost(a, job)) + '</td>' +
      '<td><div class="rt-menu-wrap"><button type="button" class="ghost" aria-label="More for ' + esc(rtAiName(id)) + '" aria-haspopup="true" onclick="rtToggleMenu(' + i + ', event)">⋯</button>' + (rtMenu === i ? rtMenuFor(job, st, i) : '') + '</div></td></tr>';
  });
  // A model its provider says is not for chat (embeddings, or made for decisions only) is never offered for a job; one whose kind is not known is offered as always.
  const pick = (rtView.pickable || []).filter(p => !st.ais.includes(p.id) && (!p.kinds || p.kinds.includes('Chat')));
  h += '<tr><td colspan="7">';
  if (rtAdding) {
    const by = {};
    for (const p of pick) (by[p.place] = by[p.place] || []).push(p);
    h += '<select id="rtAddSel" onchange="rtAdd(rtJobByKey(rtOpenKey), this.value)" aria-label="Add a model"><option value="">Choose a model…</option>' +
      (rtView.places || []).filter(pl => by[pl.id]).map(pl => '<optgroup label="' + esc(pl.name) + '">' + by[pl.id].map(p => '<option value="' + esc(p.id) + '">' + esc(p.name + ' · ' + p.kind + (p.kinds ? ' · ' + p.kinds.join(' · ') : '')) + '</option>').join('') + '</optgroup>').join('') +
      '</select> <button type="button" class="ghost" onclick="rtAdding = false; rtRender()">Cancel</button>';
  } else h += '<button type="button" class="ghost" onclick="rtAdding = true; rtRender(); setTimeout(() => { const s = document.getElementById(\'rtAddSel\'); if (s) s.focus(); }, 0)"' + (pick.length ? '' : ' disabled') + '>+ Add a model</button>';
  h += '</td></tr></table>';
  if (st.routed) h += '<div style="margin-top:10px"><button type="button" class="ghost" onclick="rtGoBack(rtJobByKey(rtOpenKey))">Go back to SkyrimNet\'s model name</button> ' + (st.ais.every(id => (rtAiById(id) || {}).setIn) ? '' : rtTestHtml(job)) + '</div>';
  h += '</div>';
  const rest = rtView.settings.rest;
  h += '<div class="rt-sub"><h3>When one doesn\'t answer</h3><div class="rt-note" style="margin:0 0 8px">A failure, "busy" or an empty reply always passes the line to the next model. A reply that has already started is never switched to another model. A model on this device (Ollama or LM Studio) that has not said its first word after about 20 seconds is given up on, so the next model can answer.</div>' +
    '<div class="rt-inline"><label for="rtSlow">Also pass the line on if there is no first word after</label><select id="rtSlow" onchange="rtSetSlow(rtJobByKey(rtOpenKey), this.value)"' + (st.routed ? '' : ' disabled') + '>' +
    RT_SLOW.map(o => '<option value="' + o[1] + '"' + (o[1] === (st.slow || 0) ? ' selected' : '') + '>' + o[0] + '</option>').join('') + '</select></div>' +
    '<div class="rt-note" style="margin:0 0 10px">Off is recommended. SkyrimNet already starts another try if a model is slow, and big cloud models often take 5 seconds or more.</div>' +
    '<div class="rt-inline"><span>Rest a model that keeps failing: after</span><select id="rtRestN" onchange="rtSetRest(\'failures\', this.value)" aria-label="Failures before resting">' +
    [2, 3, 5, 10].map(n => '<option value="' + n + '"' + (n === rest.failures ? ' selected' : '') + '>' + n + ' failures in a row</option>').join('') + '</select><span>within</span><select id="rtRestW" onchange="rtSetRest(\'within_s\', this.value)" aria-label="Within how long">' +
    [30, 60, 120, 300].map(n => '<option value="' + n + '"' + (n === rest.within_s ? ' selected' : '') + '>' + rtSecs(n) + '</option>').join('') + '</select><span>rest it for</span><select id="rtRestF" onchange="rtSetRest(\'for_s\', this.value)" aria-label="How long to rest">' +
    [60, 120, 300, 600].map(n => '<option value="' + n + '"' + (n === rest.for_s ? ' selected' : '') + '>' + rtSecs(n) + '</option>').join('') + '</select></div>' +
    '<div class="rt-note">Resting applies to every job. A slow or still-loading model on this device is never rested for being slow, and when every model in a list is resting the last one is still tried.</div></div>' + rtTested(job) + rtFineTune(job) + '</div>';
  return h;
}
// ---- Test this route: one short request through it, the way SkyrimNet would send it ----
const RT_TEST_BUSY = 'Testing… a model that isn\'t loaded yet can take a few minutes.';
function rtTestHtml(job) {
  const run = rtTestRun && rtTestRun.job === job.key ? rtTestRun : null;
  return '<button type="button" class="ghost" onclick="rtTest()"' + (run && run.busy ? ' disabled' : '') + '>Test this route</button>' +
    (run ? '<div class="rt-note">' + (run.busy ? esc(RT_TEST_BUSY) : (run.ok ? '' : '<span class="op-dot warn"></span>') + esc(run.line)) + '</div>' : '');
}
async function rtTest() {
  const job = rtJobByKey(rtOpenKey);
  if (!job) return;
  rtTestRun = { job: job.key, busy: true };
  rtRender();
  try {
    const r = await lmPost('/config/routing/test', { job: job.key });
    rtTestRun = { job: job.key, ok: !!r.ok, line: r.line || 'Nothing came back.' };
  } catch (e) {
    rtTestRun = { job: job.key, ok: false, line: e.message || 'The test could not run.' };
  }
  rtRender();
}
// ---- fine-tune this job (step 4) ----
const RT_FT_HELP = '"SkyrimNet sends" shows the exact numbers from the last real request for this job. You can type a new number to override it, or clear it to go back to normal.';
function rtFtSent(name, v) {
  if (v == null) return '—';
  return name === 'max_tokens' ? v + ' tokens' : String(v);
}
function rtFtField(name) { return (rtView.fineTuneFields || []).find(f => f.name === name) || { name: name, plain: name }; }
function rtFtInput(r) {
  if (r.name === 'thinking') {
    return '<select id="rtFt_thinking" aria-label="Thinking" onchange="rtSetOverride(\'thinking\', this.value)">' + [['', 'as sent'], ['off', 'off'], ['on', 'on']].map(o =>
      '<option value="' + o[0] + '"' + (o[0] === (r.override || '') ? ' selected' : '') + '>' + o[1] + '</option>').join('') + '</select>';
  }
  return '<input type="text" size="8" id="rtFt_' + esc(r.name) + '" aria-label="' + esc(r.plain) + '" placeholder="as sent" value="' + (r.override == null ? '' : esc(String(r.override))) +
    '" onchange="rtSetOverride(\'' + jsq(r.name) + '\', this.value)">';
}
function rtFineTune(job) {
  if (!job.fineTune) return '';
  const rows = job.settingsSent || [], any = rows.some(r => r.override != null);
  let h = '<div class="rt-sub"><h3>Fine-tune this job <span class="lm-muted" style="font-weight:400">(optional)</span></h3>';
  if (!job.hasSent) h += '<div class="rt-note" style="margin:0 0 8px">Nothing seen yet: SkyrimNet\'s numbers appear here after its next request for this job.</div>';
  h += '<table class="rt-table"><tr><th>Setting</th><th>SkyrimNet sends</th><th>Use instead</th><th></th></tr>';
  for (const r of rows) {
    h += '<tr><td>' + esc(r.plain) + ' <span class="lm-muted rt-tech">' + esc(r.name) + '</span></td><td class="n">' + esc(rtFtSent(r.name, r.sent)) + '</td><td>' + rtFtInput(r) +
      (r.override != null && r.skippedOn && r.skippedOn.length ? '<div class="rt-note" style="margin:3px 0 0">Not used by ' + esc(r.skippedOn.join(', ')) + ': ' + (r.skippedOn.length === 1 ? 'it doesn\'t' : 'they don\'t') + ' take this setting.</div>' : '') +
      '</td><td>' + (r.override != null ? '<button type="button" class="ghost" onclick="rtSetOverride(\'' + jsq(r.name) + '\', \'\')">Clear</button>' : '') + '</td></tr>';
  }
  h += '</table>';
  if ((job.alsoSent || []).length) h += '<div class="rt-note">Also sent: ' + job.alsoSent.map(o => esc(o.name) + ' ' + esc(String(o.sent))).join(', ') + '.</div>';
  h += '<div class="rt-note">' + esc(RT_FT_HELP) + '</div>';
  if (any) h += '<div style="margin-top:10px"><button type="button" class="ghost" onclick="rtClearOverrides()">Clear all</button></div>';
  return h + '</div>';
}
// ---- settings for this model (tested settings per job, settings_presets.json) ----
let rtTestedOpen = {};
function rtTested(job) {
  const c = (rtView.tested || {})[job.key];
  if (!c) return '';
  const shown = rtTestedOpen[job.key] || c.anyOn;
  let h = '<div class="rt-sub"><h3>Settings for this model <span class="lm-muted" style="font-weight:400">· answered by ' + esc(c.answeredBy) + '</span></h3>';
  if (c.anyTested) {
    h += '<ol class="rt-steps"><li>Press <b>Use the recommended settings</b>.</li><li>Play, then run <a href="#" style="color:var(--accent);text-decoration:underline" onclick="showMainTab(\'quick\'); return false">Check it like SkyrimNet</a> on Quick Test.</li></ol>' +
      '<div class="rt-btns" style="margin-top:10px"><button type="button" class="ghost" onclick="rtTestedAll(\'' + jsq(job.key) + '\', true)"' + (c.allOn ? ' disabled' : '') + '>Use the recommended settings</button>' +
      '<button type="button" class="ghost' + (shown ? ' on' : '') + '" onclick="rtTestedToggle(\'' + jsq(job.key) + '\')">Choose one by one</button>' +
      (c.anyOn ? '<button type="button" class="ghost" onclick="rtTestedAll(\'' + jsq(job.key) + '\', false)">Switch them all off</button>' : '') + '</div>';
  }
  if (shown || !c.anyTested) {
    h += '<table class="rt-table" style="margin-top:10px"><tr><th>Setting</th><th>For ' + esc(c.family || 'this model') + ', job ' + esc(c.jobName) + '</th><th>Now</th></tr>';
    for (const r of c.rows) {
      const sw = r.tested && !r.builtInAlways
        ? '<input type="checkbox" aria-label="' + esc(r.label) + '"' + (r.on ? ' checked' : '') + ' onchange="rtTestedSet(\'' + jsq(job.key) + '\', \'' + jsq(r.id) + '\', this.checked)"> ' : '';
      h += '<tr><td>' + sw + esc(r.label) + '</td><td>' + (r.tested ? 'On' + (r.evidence ? ' <span class="lm-muted">' + esc(r.evidence) + '</span>' : '') : '<span class="lm-muted">' + esc(r.recommended) + '</span>') +
        '</td><td class="lm-muted">' + esc(r.now) + '</td></tr>';
    }
    h += '</table>';
  }
  if (c.testedOn) h += '<div class="rt-note">' + esc(c.testedOn) + '</div>';
  if (!c.anyTested) h += '<div class="rt-note"><a href="#" style="color:var(--accent);text-decoration:underline" onclick="showMainTab(\'quick\'); return false">Run Check it like SkyrimNet</a> to see how this model does.</div>';
  return h + '</div>';
}
function rtTestedToggle(key) { rtTestedOpen[key] = !rtTestedOpen[key]; rtRender(); }
function rtTestedWith(job, fn) {
  const s = rtClone();
  s.tested = s.tested || {};
  for (const k of rtKeys(job)) {
    const per = Object.assign({}, s.tested[k] || {});
    fn(per);
    if (Object.keys(per).length) s.tested[k] = per; else delete s.tested[k];
  }
  return rtSave(s, { job: true });
}
function rtTestedSet(key, id, on) {
  const job = rtJobByKey(key); if (!job) return;
  return rtTestedWith(job, per => { per[id] = !!on; });
}
function rtTestedAll(key, on) {
  const job = rtJobByKey(key), c = (rtView.tested || {})[key]; if (!job || !c) return;
  return rtTestedWith(job, per => { for (const r of c.rows) if (r.tested && !r.builtInAlways) per[r.id] = !!on; });
}
function rtSetOverride(name, value) {
  const job = rtJobByKey(rtOpenKey);
  if (!job) return;
  const s = rtClone();
  s.overrides = s.overrides || {};
  for (const k of rtKeys(job)) {
    const per = Object.assign({}, s.overrides[k] || {});
    if (value === '' || value == null || String(value).trim() === '') delete per[name]; else per[name] = String(value).trim();
    if (Object.keys(per).length) s.overrides[k] = per; else delete s.overrides[k];
  }
  return rtSave(s, { job: true }).then(() => {
    const kept = ((rtView.settings.overrides || {})[job.key] || {})[name];
    if (String(value).trim() !== '' && kept == null) {
      const f = rtFtField(name);
      rtFail({ job: true }, f.plain + (f.kind === 'onoff' ? ' is on or off.' : ' needs a number from ' + f.min + ' to ' + f.max + '.'));
      rtRender();
    }
  });
}
function rtClearOverrides() {
  const job = rtJobByKey(rtOpenKey);
  if (!job) return;
  const s = rtClone();
  for (const k of rtKeys(job)) if (s.overrides) delete s.overrides[k];
  return rtSave(s, { job: true });
}
function rtMenuFor(job, st, i) {
  const n = st.ais.length;
  return '<div class="rt-menu" role="menu" onclick="event.stopPropagation()">' +
    '<button type="button" role="menuitem" onclick="rtMakeFirst(rtJobByKey(rtOpenKey), rtState(rtJobByKey(rtOpenKey)).ais[' + i + '])"' + (i === 0 && st.routed ? ' disabled' : '') + '>Make first choice</button>' +
    '<button type="button" role="menuitem" onclick="rtMove(rtJobByKey(rtOpenKey), ' + i + ', -1)"' + (i === 0 ? ' disabled' : '') + '>Move up</button>' +
    '<button type="button" role="menuitem" onclick="rtMove(rtJobByKey(rtOpenKey), ' + i + ', 1)"' + (i === n - 1 ? ' disabled' : '') + '>Move down</button>' +
    '<button type="button" role="menuitem" onclick="rtRemove(rtJobByKey(rtOpenKey), ' + i + ')">' + (n === 1 ? 'Remove (back to SkyrimNet\'s model name)' : 'Remove') + '</button></div>';
}
function rtStepsHtml(o) {
  return '<div class="rt-option"><b>' + esc(o.title) + '</b><p>' + esc(o.line) + '</p><ol class="rt-steps">' + o.steps.map(t => '<li>' + esc(t).replace(/`([^`]+)`/g, '<code>$1</code>') + '</li>').join('') + '</ol></div>';
}
function rtUseCard() {
  const names = (rtView.jobs || []).filter(j => j.routable !== false).map(j => 'job/' + j.key).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
  let h = '<div class="card rt-use" id="rtUseSec"><h2 style="margin:0 0 8px">' + esc(RT_SETUP_TITLE) + '</h2><p class="lm-hint" style="margin:0">' + esc(RT_SETUP_INTRO) + '</p>' +
    '<div class="rt-setup">' + rtStepsHtml(RT_OPT1) + rtStepsHtml(RT_OPT2) + '</div>';
  if (names.length) h += '<p class="lm-hint" style="margin:12px 0 0">' + esc(RT_NAMES_HEAD) + '</p><div class="rt-names">' +
    names.map(n => '<div class="rt-nameitem"><code>' + esc(n) + '</code><button type="button" class="ghost" onclick="rtCopy(' + "'" + n.replace(/[^\w\/]/g, '') + "'" + ', this)">Copy</button></div>').join('') + '</div>';
  return h + '</div>';
}
// ---- Clear all routes (asks first) ----
const RT_CLEAR_BTN = 'Clear all routes';
const RT_CLEAR_TITLE = 'Clear all routes?';
const RT_CLEAR_BODY = "Every job will go back to the models you picked in SkyrimNet's Models page. Your priority order and fine-tune settings will stay the same.";
const RT_CLEAR_CANCEL = 'Cancel';
function rtRouteCount() { return Object.keys((rtView && rtView.settings && rtView.settings.routes) || {}).length; }
function rtAskClear() {
  if (!rtRouteCount()) return;
  const box = document.getElementById('rtModalBox');
  if (!box) return;
  box.innerHTML = '<div class="vc-dialog" role="dialog" aria-modal="true" aria-labelledby="rtClearTitle" tabindex="-1" id="rtClearDialog"><h3 id="rtClearTitle">' + esc(RT_CLEAR_TITLE) + '</h3>' +
    '<p>' + esc(RT_CLEAR_BODY) + '</p>' +
    '<div class="vc-ft"><button type="button" class="ghost" id="rtClearCancel" onclick="rtCloseClear()">' + esc(RT_CLEAR_CANCEL) + '</button><button type="button" class="primary" id="rtClearGo" onclick="rtConfirmClear()">' + esc(RT_CLEAR_BTN) + '</button></div></div>';
  box.style.display = '';
  const c = document.getElementById('rtClearCancel');
  if (c) c.focus();
}
function rtCloseClear() {
  rtPendingMove = null;
  const box = document.getElementById('rtModalBox');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
}
async function rtConfirmClear() {
  rtCloseClear();
  const s = rtClone();
  s.routes = {};                                                                                // routes only: priority, fine-tune, resting and slow settings stay as they are
  return rtSave(s, { job: true });
}
try { document.addEventListener('keydown', e => { if (e.key === 'Escape') rtCloseClear(); }); } catch (e) {}
// ---- the key: one quiet line saying what the icons on the chips mean (fixed part of the page, never redrawn with the picture) ----
// The hover sentences are Gemini's; the short words beside each icon are what shows.
const RT_KEY_LINK = 'Same colour, same job. Hover to outline its first choice and backups.';
const RT_KEY_PUZZLE = 'A job from an add-on. SkyrimNet marks these with a puzzle piece on the Models page.';
const RT_KEY_ORDER = 'First choice. A dashed \u21aa 2 answers if the first is busy or fails.';
const RT_KEY_MP = 'You changed this here in MultiProxy.';
const RT_KEY_SHORT = { link: 'Same colour, same job', puzzle: 'Add-on job', order: 'First choice, then backup', mp: 'Changed here in MultiProxy' };
function rtKeyChip(n, back, colour, mp) {
  return '<span class="rt-chip' + (back ? ' rt-back' : '') + '" style="--jc:var(--rt-c' + colour + ')">' + RT_LINK_SVG + '<span class="rt-n">' + (back ? '\u21aa ' : '') + n + (mp ? ' <span class="rt-mp">MP</span>' : '') + '</span></span>';
}
function rtKeyItem(icons, words, hover) { return '<span class="rt-key-item" title="' + esc(hover) + '"><span class="rt-key-icons">' + icons + '</span><span>' + esc(words) + '</span></span>'; }
function rtKeyDraw() {
  const k = document.getElementById('rtKey');
  if (!k) return;
  k.innerHTML =
    rtKeyItem('<span class="rt-chip" style="--jc:var(--rt-c1)">' + RT_LINK_SVG + '</span><span class="rt-chip" style="--jc:var(--rt-c2)">' + RT_LINK_SVG + '</span>', RT_KEY_SHORT.link, RT_KEY_LINK) +
    rtKeyItem('<span class="rt-puzzle" aria-hidden="true" style="margin-left:0">🧩</span>', RT_KEY_SHORT.puzzle, RT_KEY_PUZZLE) +
    rtKeyItem(rtKeyChip(1, false, 1, false) + rtKeyChip(2, true, 1, false), RT_KEY_SHORT.order, RT_KEY_ORDER) +
    rtKeyItem(rtKeyChip(1, false, 1, true), RT_KEY_SHORT.mp, RT_KEY_MP);
}
rtKeyDraw();
function rtFromHash() { if (location.hash === '#routing') showMainTab('routing'); }
window.addEventListener('hashchange', rtFromHash);
window.addEventListener('load', rtFromHash);
"""
