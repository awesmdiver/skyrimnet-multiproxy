"""The dashboard's "Local models" card (2026-09-30), built to design/dashboard-local-models-mockup.html.

Kept as plain strings in their own file (spliced into multiproxy.py's dashboard page) so none of the
CSS/JS needs the doubled-brace escaping the page's f-string would otherwise force on it. Every
piece of text that came from the server or the player's own files goes through esc() before it is
put into the page.
"""

import models_off

LOCAL_MODELS_CSS = r"""
  /* Local models card (2026-09-30) -- lm-* classes so nothing here collides with the page's own */
  .lm-hint { color:var(--muted); font-size:0.85rem; margin:0 0 14px; line-height:1.5 }
  .lm-step { display:block; font-size:0.9rem; font-weight:600; color:var(--accent-text); margin:22px 0 6px }
  .lm-gpu { display:flex; gap:14px; align-items:center; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin-bottom:12px }
  .lm-gpu .big { font-size:1.6rem }
  .lm-opt { display:grid; grid-template-columns:22px 1fr auto; gap:10px; align-items:center; border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:8px 0; cursor:pointer }
  .lm-opt.sel { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 8%, transparent) }
  .lm-opt input { margin:0 }
  .lm-rec { font-size:0.68rem; color:var(--on-accent); background:var(--accent); border-radius:8px; padding:1px 7px; margin-left:6px }
  .lm-muted { color:var(--muted); font-size:0.8rem }
  .lm-rm-title { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-bottom:6px }
  .lm-speed { margin-top:6px; font-size:0.78rem; color:var(--muted) }
  .lm-speed .lm-pill { border-color:var(--line); color:var(--muted) } .lm-speed .lm-pill b { color:var(--ok) }
  .lm-row { display:grid; grid-template-columns:1fr 110px 170px 150px; gap:12px; align-items:center; border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:9px 0; background:var(--panel) }
  .lm-row > div:last-child { justify-self:end; text-align:right }
  .lm-name { font-weight:600; font-size:0.9rem; overflow-wrap:anywhere }
  .lm-meta { color:var(--muted); font-size:0.75rem; margin-top:2px; overflow-wrap:anywhere }
  .lm-pill { font-size:0.72rem; border-radius:10px; padding:2px 9px; border:1px solid currentColor; white-space:nowrap; display:inline-block }
  .lm-st { white-space:nowrap; font-size:0.9rem } .lm-st i { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px; background:var(--ok) }
  .lm-st i.warn { background:var(--warn) } .lm-st i.bad { background:var(--err) } .lm-st i.off { background:var(--muted) }
  .lm-ok { color:var(--ok) } .lm-warn { color:var(--warn-text) } .lm-bad { color:var(--err) } .lm-info { color:var(--info, #60a5fa) }
  .lm-bar { height:5px; background:var(--line); border-radius:3px; margin-top:6px; overflow:hidden; width:150px; margin-left:auto }
  .lm-bar i { display:block; height:5px; background:var(--accent) }
  .lm-engine-note { color:var(--warn-text); font-size:0.78rem; margin-top:6px; line-height:1.4 }
  .lm-adv { margin:10px 0 4px } .lm-adv summary { cursor:pointer; color:var(--accent-text); font-size:0.85rem }
  .lm-err { color:var(--err); font-size:0.78rem; margin-top:6px; line-height:1.4 }
  .lm-folder { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:0 0 6px }
  .lm-statusline { display:flex; gap:8px 14px; align-items:center; flex-wrap:wrap; margin:0 0 6px }
  .lm-statusline .lm-warn { font-size:0.8rem }
  .lm-statusline .lm-path { font-family:monospace; font-size:0.78rem; color:var(--accent-text); overflow-wrap:anywhere }
  .lm-folder .lm-path { font-family:monospace; font-size:0.78rem; color:var(--accent-text); overflow-wrap:anywhere }
  .lm-pick { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:14px; margin:8px 0 }
  .lm-form { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:14px; margin-top:10px }
  .lm-form label { display:block; color:var(--soft); font-size:0.8rem; margin:10px 0 4px }
  .lm-form .lm-line { display:flex; gap:8px }
  .lm-form input { flex:1; font-family:system-ui,sans-serif }
  .lm-link { color:var(--accent-text); cursor:pointer; text-decoration:underline }
  .lm-actions { text-align:right; margin-top:12px }
  /* Quieter model rows (Revision 4): icons after the name, one-line description, size under the button */
  .lm-mrow { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px; align-items:center; border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin:9px 0; background:var(--panel) }
  .lm-mrow .lm-meta { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .lm-act { display:flex; flex-direction:column; align-items:flex-end; gap:4px }
  /* "Pick a model" as a table (2026-10-26): every model is two lines (name, then description) with Size, Status and the buttons in their own fixed columns, like the Models tab */
  .lm-tbl { overflow-x:auto }
  .lm-tbl-in { min-width:44rem }
  .lm-trow, .lm-thead { display:grid; grid-template-columns:minmax(0,1fr) 5rem 11rem 12.5rem; gap:14px; align-items:center }
  .lm-trow { border:1px solid var(--line); border-radius:8px; padding:10px 16px; margin:8px 0; background:var(--panel); min-height:3.1rem }
  .lm-thead { padding:0 16px; color:var(--muted); font-size:0.75rem }
  .lm-thead span:nth-child(2), .lm-trow .lm-tsize { text-align:right }
  .lm-trow .lm-tsize { color:var(--muted); font-size:0.85rem; white-space:nowrap }
  .lm-trow .lm-meta { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .lm-trow .lm-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .lm-tst { min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:0.85rem }
  .lm-tst .lm-bar { width:100%; margin:5px 0 0 }
  .lm-tslots { display:flex; justify-content:flex-end; gap:8px }
  .lm-tslot { width:6rem; display:flex; justify-content:flex-end }
  .lm-tslot button { min-width:5.5rem }
  .lm-size { color:var(--muted); font-size:0.72rem }
  /* Only status dots carry color: the eye icon is plain and muted, and one yellow dot says "heads up on fit" */
  .lm-ic { margin-left:7px; font-size:0.85rem; cursor:help; color:var(--muted) }
  .lm-dot { margin-left:7px; font-size:0.85rem; cursor:help; color:var(--warn) }
  .lm-legend { display:flex; gap:18px; flex-wrap:wrap; font-size:0.75rem; color:var(--muted); margin:2px 0 10px }
  .lm-legend .lm-ic, .lm-legend .lm-dot { margin:0 4px 0 0; cursor:default }
  /* Graphics memory card (Revisions 5 and 6) */
  .mem-head { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap }
  .mem-head h3 { margin:0 }
  .mem-head select { max-width:100%; font-family:system-ui,sans-serif; font-size:0.85rem; padding:6px 10px }
  .mem-sub { color:var(--muted); font-size:0.8rem; margin-top:6px }
  .mem-live { color:var(--ok); font-size:0.72rem; margin-left:8px }
  .mem-bar { display:flex; height:22px; border-radius:6px; overflow:hidden; border:1px solid var(--line); margin:10px 0 8px }
  .mem-bar i { display:block; height:100% }
  .mem-key { display:flex; gap:16px; flex-wrap:wrap; font-size:0.78rem; color:var(--muted) }
  .mem-key span::before { content:""; display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px; background:var(--c); outline:1px solid var(--line); outline-offset:-1px }
  .mem-key b { color:var(--text); font-weight:600 }
  .mem-peak { font-size:0.8rem; color:var(--muted); margin-top:10px }
  .mem-peak b { color:var(--text) }
  .mem-peak button { font-size:0.72rem; padding:2px 9px; margin-left:6px }
  @media (max-width:700px) { .lm-row { grid-template-columns:1fr } .lm-row > div:last-child { justify-self:start; text-align:left } .lm-bar { margin-left:0 } }
"""

LOCAL_MODELS_CARD_HTML = """
  <div class="card" id="memCard" style="display:none"></div>
  <div class="card" id="localCard">
    <div class="card-head"><h3>Local device</h3><span class="label" id="lmStatus"></span></div>
    <p class="lm-hint">Run AI models on your own graphics card. Free, private, and no internet needed once they're downloaded. <span class="hp-how"><a onclick="openHelp('local')">How do I set this up?</a></span></p>
    <div id="lmMain"><div class="lm-muted">Looking at this device…</div></div>
  </div>
"""

LOCAL_MODELS_JS = models_off.js_words("LM_OFF") + r"""// ---------------- Local models card ----------------
if (typeof jumpRegister === 'function') jumpRegister('lmJump', 'paneLocal', () => jumpIds(['memCard', 'localCard', 'lsMount', 'lsDeviceCard', 'lsAdvCard']), {});
// Built to design/dashboard-local-models-mockup.html. Polls /config/local-models while anything is
// downloading or loading, and re-fetches after every action so what's on screen is always what the
// proxy really has.
let lmState = null;
let lmSpeeds = {};
let lmPickedEngine = null;
let lmEngUpdate = null, lmEngUpdateAsked = false;   // the answer to "is a newer engine available": asked again each time the tab opens and after any engine change (the server keeps the half-hour network check)
let lmAddOpen = false;
let lmPollTimer = null;
const lmAddForm = { path: '', mmproj: '' };

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
// A value headed for an inline onclick="...('HERE')" -- quotes and backslashes can't survive there.
function jsq(s) { return esc(String(s == null ? '' : s).replace(/[\\']/g, '')); }
// MultiProxy's own question pop-up (the Other devices / Voice dialogs' look), never the browser's "127.0.0.1:8000 says" box.
// ownAsk(question, actionLabel) resolves true on the action button, false on Cancel, Esc or a click outside. Focus starts on Cancel,
// so Enter right away is the safe answer; Enter on the action button (or anywhere else in the pop-up) confirms.
function ownAsk(question, actionLabel) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'op-modal';
    wrap.innerHTML = '<div class="op-modal-box" role="alertdialog" aria-modal="true" style="width:min(460px,100%)"><div class="op-confirm" id="ownAskText"></div>' +
      '<div class="op-btns" style="justify-content:flex-end"><button class="ghost" id="ownAskCancel">Cancel</button><button id="ownAskGo"></button></div></div>';
    wrap.querySelector('#ownAskText').textContent = question;
    wrap.querySelector('#ownAskGo').textContent = actionLabel;
    const prevFocus = document.activeElement;
    const done = ans => { document.removeEventListener('keydown', onKey, true); wrap.remove(); if (prevFocus && prevFocus.focus) try { prevFocus.focus(); } catch (e) {} resolve(ans); };
    const onKey = ev => {
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); done(false); }
      else if (ev.key === 'Enter' && !(ev.target && ev.target.tagName === 'BUTTON')) { ev.preventDefault(); done(true); }
    };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('click', ev => { if (ev.target === wrap) done(false); });
    wrap.querySelector('#ownAskCancel').onclick = () => done(false);
    wrap.querySelector('#ownAskGo').onclick = () => done(true);
    document.body.appendChild(wrap);
    wrap.querySelector('#ownAskCancel').focus();
  });
}
// The Remove question for a downloaded model: ONE pop-up, a title, one plain line and three buttons on the right (Cancel, Remove keeping the files, Remove deleting them).
// Nothing happens until a button is pressed; Esc, a click outside and Cancel all mean Cancel. Resolves 'keep', 'delete' or false.
function ownAskRemove(title, line, keepLabel, deleteLabel, usedBy) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'op-modal';
    wrap.innerHTML = '<div class="op-modal-box" role="alertdialog" aria-modal="true" style="width:min(520px,100%)"><div class="op-confirm"><b id="ownRmTitle" class="lm-rm-title"></b><div id="ownRmUsed"></div><div id="ownRmLine"></div></div>' +
      '<div class="op-btns" style="justify-content:flex-end"><button class="ghost" id="ownRmCancel">Cancel</button><button id="ownRmKeep"></button><button class="ghost" id="ownRmDelete"></button></div></div>';
    wrap.querySelector('#ownRmTitle').textContent = title;
    wrap.querySelector('#ownRmLine').textContent = line;
    wrap.querySelector('#ownRmKeep').textContent = keepLabel;
    if (deleteLabel) wrap.querySelector('#ownRmDelete').textContent = deleteLabel; else wrap.querySelector('#ownRmDelete').remove();
    if (usedBy && usedBy.length) {                         // a model jobs use: the one short warning line, the job names on hover
      const w = wrap.querySelector('#ownRmUsed');
      w.className = 'lm-warn'; w.style.margin = '0 0 6px'; w.textContent = '\u26a0\ufe0f ' + LM_REMOVE_WORDS.inUse; w.title = usedBy.join(', ');
    }
    const prevFocus = document.activeElement;
    const done = ans => { document.removeEventListener('keydown', onKey, true); wrap.remove(); if (prevFocus && prevFocus.focus) try { prevFocus.focus(); } catch (e) {} resolve(ans); };
    const onKey = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); done(false); } };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('click', ev => { if (ev.target === wrap) done(false); });
    wrap.querySelector('#ownRmCancel').onclick = () => done(false);
    wrap.querySelector('#ownRmKeep').onclick = () => done('keep');
    if (deleteLabel) wrap.querySelector('#ownRmDelete').onclick = () => done('delete');
    document.body.appendChild(wrap);
    wrap.querySelector('#ownRmCancel').focus();
  });
}
// A visible "Start here" list (the page's own su-box / su-steps look, the same as the Providers tab's).
function lmSteps(steps) {
  return shBox('local-models', '<div class="su-box"><h4>Start here</h4><ol class="su-steps">' + steps.map(t => '<li>' + t + '</li>').join('') + '</ol></div>');
}
function lmFmt(bytes) { return bytes >= 1e8 ? (bytes / 1e9).toFixed(1) + ' GB' : Math.round(bytes / 1e6) + ' MB'; }

async function lmPost(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.detail || ('Something went wrong (' + r.status + ')'));
  return data;
}

function lmOpened() { lmEngUpdateAsked = false; lmRefresh(); }         // the Local models tab was opened
async function lmRefresh() {
  try {
    lmState = await (await fetch('/config/local-models')).json();
  } catch (e) { lmSchedule(); return; }
  try {
    const sp = await (await fetch('/config/local-models/speeds')).json();
    lmSpeeds = {};
    for (const m of (sp.models || [])) lmSpeeds[m.id] = m;
  } catch (e) {}
  if (lmPickedEngine == null || !lmState.engine.options.some(o => o.kind === lmPickedEngine)) {
    const rec = lmState.engine.options.find(o => o.recommended) || lmState.engine.options[0];
    lmPickedEngine = rec ? rec.kind : null;
  }
  lmRender();
  lmSchedule();
  if (typeof lsSync === 'function') lsSync();         // the settings screens (local_settings_ui.py)
}

function lmSpeedLine(id) {
  const m = lmSpeeds[id];
  if (!m) return '';
  const was = m.previous ? ' (was ' + Math.round(m.previous.tokensPerSecond) + ' before the last change)' : '';
  if (m.state === 'ok') {
    return '<div class="lm-speed"><span class="lm-pill"><b>' + Math.round(m.tokensPerSecond) + '</b> tokens/s · last ' + m.requests + ' request' + (m.requests === 1 ? '' : 's') + '</span>' + esc(was) + '</div>';
  }
  return '<div class="lm-speed"><span class="lm-muted">' + (m.state === 'not_reported' ? 'Not reported' : 'Not enough requests yet') + esc(was) + '</span></div>';
}

function lmBusy() {
  if (!lmState) return false;
  const e = lmState.engine.download;
  if (e && e.state === 'running') return true;
  if (lmState.catalog.some(m => m.download && m.download.state === 'running')) return true;
  return lmState.installed.some(m => m.state === 'starting');
}
function lmSchedule() {
  clearTimeout(lmPollTimer);
  lmPollTimer = setTimeout(async () => { if (document.hidden) { lmSchedule(); return; } await lmRefresh(); lmProviderPillRefresh(); }, lmBusy() ? 1200 : 6000);
}

async function lmProviderPillRefresh() {
  try {
    const fresh = await (await fetch('/config/local-models/provider')).json();
    const i = PROVIDERS.findIndex(p => p.id === 'local');
    if (i < 0) return;
    const changed = JSON.stringify(PROVIDERS[i]) !== JSON.stringify(fresh);
    PROVIDERS[i] = fresh;
    if (changed) renderChips();
  } catch (e) {}
}

async function lmAct(fn) {
  let err = null;
  try { await fn(); } catch (e) { err = e.message; }
  lmEngUpdateAsked = false;              // an install, update, removal or switch may have changed the engine: ask again
  await lmRefresh();
  if (err) lmShowError(err);
  lmProviderPillRefresh();
}
function lmShowError(msg) {
  const box = document.getElementById('lmTopError');
  if (box) { box.textContent = msg; box.style.display = 'block'; }
}

async function lmChooseFolder() {
  const r = await lmPost('/config/local-models/pick-folder');
  if (!r.path) return false;
  await lmPost('/config/local-models/folder', { path: r.path });
  return true;
}
async function lmEnsureFolder() {
  if (lmState && lmState.folder) return true;
  return await lmChooseFolder();
}

function lmDownloadEngine() {
  lmAct(async () => {
    if (!(await lmEnsureFolder())) return;
    await lmPost('/config/local-models/engine/download', { kind: lmPickedEngine });
  });
}
function lmDownloadModel(id) {
  lmAct(async () => {
    if (!(await lmEnsureFolder())) return;
    await lmPost('/config/local-models/model/download', { id });
  });
}
function lmCancel(key) { lmAct(() => lmPost('/config/local-models/cancel', { key })); }
function lmPause(key) { lmAct(() => lmPost('/config/local-models/pause', { key })); }
function lmResume(key) { lmAct(() => lmPost('/config/local-models/resume', { key })); }
function lmStop(id) { lmAct(() => lmPost('/config/local-models/stop', { id })); }
function lmStart(id) { lmAct(() => lmPost('/config/local-models/start', { id })); }
// Every button of the screens below goes through here: sc is 'this' (this PC) or a paired PC's id (other_pcs_ui.py's opLmDo sends it to that PC's Courier).
function lmDo(sc, verb, a, b, c, d) {
  if (sc && sc !== 'this') return opLmDo(sc, verb, a, b, c, d);
  const f = { download: lmDownloadModel, cancel: lmCancel, pause: lmPause, resume: lmResume, start: lmStart, stop: lmStop, remove: lmRemove, addKept: lmAddKept, deleteKept: lmDeleteKept,
              updateEngine: lmUpdateEngine, useEngine: lmUseEngine, addToggle: lmToggleAdd }[verb];
  return f(a, b, c, d);
}
const LM_REMOVE_WORDS = {
  title: n => 'Remove ' + n + '?',
  own: 'Your file stays where it is.',
  keepLine: size => 'The files use ' + size + '. Keep them to add the model back or copy it to another PC without downloading it again.',
  keep: 'Remove, keep files', del: 'Remove, delete files', remove: 'Remove',
  deleteAsk: (n, size) => 'Delete ' + n + ' from your computer? The files use ' + size + '. You can download it again later.', deleteGo: 'Delete',
  kept: 'On this PC', add: 'Add', delete: 'Delete', inUse: 'In use',
};
// The one Remove question, shared by the Local device tab and the Models tab. Resolves { deleteFiles } or null (Cancel). `usedBy` (job names) adds the short "In use" warning line.
async function lmRemoveFlow(id, name, custom, sizeText, usedBy) {
  if (custom) {
    const ok = await ownAskRemove(LM_REMOVE_WORDS.title(name), LM_REMOVE_WORDS.own, LM_REMOVE_WORDS.remove, null, usedBy);
    return ok ? { deleteFiles: false } : null;
  }
  const answer = await ownAskRemove(LM_REMOVE_WORDS.title(name), LM_REMOVE_WORDS.keepLine(sizeText), LM_REMOVE_WORDS.keep, LM_REMOVE_WORDS.del, usedBy);
  return answer ? { deleteFiles: answer === 'delete' } : null;
}
function lmRemove(id, name, custom, sizeText) {
  lmRemoveFlow(id, name, custom, sizeText, []).then(r => { if (r) lmAct(() => lmPost('/config/local-models/remove', r.deleteFiles ? { id, deleteFiles: true } : { id })); });
}
function lmAddKept(id) { lmAct(() => lmPost('/config/local-models/add-kept', { id })); }
function lmDeleteKept(id, name, sizeText) {
  ownAsk(LM_REMOVE_WORDS.deleteAsk(name, sizeText), LM_REMOVE_WORDS.deleteGo).then(ok => { if (ok) lmAct(() => lmPost('/config/local-models/delete-kept', { id })); });
}

async function lmBrowse(which) {
  try {
    const r = await lmPost('/config/local-models/pick-file');
    if (r.path) { lmAddForm[which] = r.path; document.getElementById(which === 'path' ? 'lmAddPath' : 'lmAddMm').value = r.path; }
  } catch (e) { lmShowError(e.message); }
}
function lmAddSubmit() {
  lmAddForm.path = document.getElementById('lmAddPath').value;
  lmAddForm.mmproj = document.getElementById('lmAddMm').value;
  lmAct(async () => {
    await lmPost('/config/local-models/add', lmAddForm);
    lmAddOpen = false; lmAddForm.path = ''; lmAddForm.mmproj = '';
  });
}
function lmToggleAdd() { lmAddOpen = !lmAddOpen; lmRender(); }
function lmEngineOption(o) {
  return '<label class="lm-opt' + (o.kind === lmPickedEngine ? ' sel' : '') + '" onclick="lmPickEngine(\'' + jsq(o.kind) + '\')">' +
         '<input type="radio" name="lmEngine"' + (o.kind === lmPickedEngine ? ' checked' : '') + '>' +
         '<div><b>' + esc(o.label) + '</b>' + (o.recommended ? '<span class="lm-rec">Best for your card</span>' : '') +
         '<div class="lm-muted">' + esc(o.blurb) + '</div></div><div class="lm-muted">' + lmFmt(o.sizeBytes) + '</div></label>';
}
// Installed engine: a newer numbered build (only when one exists), and the Advanced faster versions. Nothing changes until a button here is pressed.
async function lmCheckEngineUpdate() {
  if (lmEngUpdateAsked) return;
  lmEngUpdateAsked = true;
  try {
    const r = await (await fetch('/config/local-models/engine/update')).json();
    const changed = JSON.stringify(r) !== JSON.stringify(lmEngUpdate);
    lmEngUpdate = r;
    if (changed) lmRender();
  } catch (e) {}
}
function lmUpdateEngine() { lmAct(async () => { await lmPost('/config/local-models/engine/update'); lmEngUpdate = null; }); }
function lmBuildNumber(tag) { const m = /(\d+)/.exec(String(tag == null ? '' : tag)); return m ? parseInt(m[1], 10) : 0; }
function lmUseEngine(kind) { lmAct(async () => { await lmPost('/config/local-models/engine/download', { kind }); }); }
function lmEngineUpdateInline(s) {
  const remote = s.scope && s.scope !== 'this';
  if (!remote) lmCheckEngineUpdate();
  const upd = remote ? s.engineUpdate : lmEngUpdate;
  if (!(upd && upd.available)) return '';
  if (s.engine && lmBuildNumber(upd.build) <= lmBuildNumber(s.engine.build)) return '';       // never for the build already installed (or an older one)
  return '<div class="lm-statusline"><span class="lm-warn">⚠️ A newer version of llama.cpp is available (' + esc(upd.build) + ', ' + lmFmt(upd.sizeBytes) + ').</span><button class="primary" style="margin-left:auto" onclick="lmDo(\'' + jsq(s.scope || 'this') + '\', \'updateEngine\')">Update llama.cpp</button></div>';
}
function lmInstalledEngineExtras(eng, sc) {
  let h = '';
  const dl = eng.download;
  if (dl && (dl.state === 'running' || dl.state === 'paused')) return '<div class="lm-row" style="grid-template-columns:1fr auto"><div><div class="lm-name">' + (dl.state === 'paused' ? 'The software download' : 'Downloading the software…') + '</div></div><div>' + lmDlBlock(dl, 'engine', sc) + '</div></div>';
  if (dl && dl.state === 'error') h += '<div class="lm-err">' + esc(dl.error) + '</div>';
  const onFast = eng.options.some(x => x.kind === eng.kind && x.advanced);
  const others = eng.options.filter(o => o.kind !== eng.kind && (o.advanced || (onFast && o.kind === 'vulkan')));
  if (others.length) {
    h += '<details class="lm-adv"><summary>Advanced: switch version</summary>' + others.map(o =>
      '<div class="lm-muted" style="margin:8px 0"><b>' + esc(o.label) + '</b> (' + lmFmt(o.sizeBytes) + '). ' + esc(o.blurb) + ' <button class="ghost" onclick="lmDo(\'' + jsq(sc) + '\', \'useEngine\', \'' + jsq(o.kind) + '\')">Use this one</button></div>').join('') + '</details>';
  }
  return h;
}
function lmPickEngine(kind) { lmPickedEngine = kind; lmRender(); }

// Icons after a model's name (no icon when it fits). On a model server there's no Skyrim to wait for.
function lmIcons(m, modelServer, st) {
  let h = m.vision ? '<span class="lm-ic" title="Sees pictures">👁</span>' : '';
  if (st && st.modelsOff && (!st.scope || st.scope === 'this')) return h;           // the Settings switch is on: no fit dots
  const why = m.fitWhy ? '. ' + m.fitWhy : '';        // the numbers the verdict used, so a wrong figure shows at once
  if (m.fit === 'tight' && modelServer) h += '<span class="lm-dot" title="' + esc('A tight fit on this device' + why) + '">●</span>';        // (no game runs there)
  else if (m.fit === 'tight') h += '<span class="lm-dot" title="' + esc('Tight fit alongside Skyrim' + why) + '">●</span>';
  if (m.fit === 'too_big') h += '<span class="lm-dot" title="' + esc((modelServer ? 'Too big for this device' : 'Too big to run alongside Skyrim. Fine on a spare PC.') + why) + '">●</span>';
  return h;
}
function lmLegend(modelServer, st) {
  if (st && st.modelsOff && (!st.scope || st.scope === 'this')) return '';                  // the Settings switch is on: no legend, no busiest-moment line
  return '<div class="lm-legend"><span><b class="lm-ic">👁</b>Sees pictures</span>' +
    '<span><b class="lm-dot">●</b> Too big or a tight fit (hover to see why)</span></div>' + lmSkyrimPeakLine(modelServer, st);
}
// Skyrim's busiest moment, the figure the dots use: with its date, and a Reset that clears it (it is learned again while playing).
const LM_PEAK_WORDS = { line: (gb, when) => "Skyrim's peak memory: " + gb + (when ? ' (' + when + ')' : ''), none: "Skyrim's peak memory: not measured yet", reset: 'Reset tracker', resetTip: 'Reset (relearns as you play)' };
function lmSkyrimPeakLine(modelServer, st) {
  if (modelServer || !st || !st.hasGame) return '';
  let when = '';
  if (st.skyrimPeakDay) { const d = new Date(st.skyrimPeakDay + 'T00:00:00'); when = d.getDate() + ' ' + d.toLocaleString('en', { month: 'short' }); }
  return '<div class="lm-muted" style="margin:0 0 10px">' + esc(st.skyrimPeakMb ? LM_PEAK_WORDS.line((st.skyrimPeakMb / 1024).toFixed(1) + ' GB', when) : LM_PEAK_WORDS.none) +
    (st.skyrimPeakMb ? ' <span class="hp-how"><a title="' + esc(LM_PEAK_WORDS.resetTip) + '" onclick="lmPeakReset()">' + LM_PEAK_WORDS.reset + '</a></span>' : '') + '</div>';
}
function lmPeakReset() { lmAct(() => lmPost('/config/local-models/memory/reset')); }
const LM_RUN = {
  running: ['Running', ''],            // the word and the colour of its dot (green is the default; grey = stopped on purpose, amber = on its way, red = a problem)
  starting: ['Loading…', 'warn'],
  stopped: ['Stopped', 'off'],
  failed: ['Problem', 'bad'],
};
function lmStatus(word, level) { return '<span class="lm-st"><i' + (level ? ' class="' + level + '"' : '') + '></i>' + word + '</span>'; }

// "<done> of <total> GB", then "· about 40 min left" (or "waiting for network") once the program has really measured the speed; `left` is worded by the server (download_meter.py).
function lmDlSizes(d) { return (d.done / 1e9).toFixed(1) + ' of ' + lmFmt(d.total) + (d.left ? ' · ' + d.left : ''); }
function lmDlBlock(d, key, sc) {
  if (!d || (d.state !== 'running' && d.state !== 'paused')) return null;
  const k = jsq(key), c = jsq(sc || 'this');
  if (d.state === 'paused') {
    return lmStatus('Paused · ' + (d.done / 1e9).toFixed(1) + ' GB downloaded', 'off') +
      '<div class="lm-bar"><i style="width:' + d.percent + '%"></i></div>' +
      '<div class="lm-muted" style="margin-top:4px"><button onclick="lmDo(\'' + c + '\', \'resume\', \'' + k + '\')">Resume</button> <button class="ghost" onclick="lmDo(\'' + c + '\', \'cancel\', \'' + k + '\')">Cancel</button></div>';
  }
  return lmStatus('Downloading… ' + d.percent + '%', 'warn') +
    '<div class="lm-bar"><i style="width:' + d.percent + '%"></i></div>' +
    '<div class="lm-muted" style="margin-top:4px">' + lmDlSizes(d) + '</div>' +
    '<div class="lm-muted" style="margin-top:4px"><button class="ghost" onclick="lmDo(\'' + c + '\', \'pause\', \'' + k + '\')">Pause</button> <button class="ghost" onclick="lmDo(\'' + c + '\', \'cancel\', \'' + k + '\')">Cancel</button></div>';
}

const LM_TABLE_WORDS = { model: 'Model', size: 'Size', status: 'Status', downloaded: 'Downloaded', updateAvail: 'Update available' };
// One model on "Pick a model": always two lines. Line 1 the name, the eye and the fit dot; line 2 the description. Then Size, Status (a dot and a word; the bar of a running
// download sits under it) and two fixed button slots, so no state makes the row taller.
function lmModelRow(m, s) {
  const id = jsq(m.id);
  const sc = jsq(s.scope || 'this');
  const go = (verb, arg, more) => 'lmDo(\'' + sc + '\', \'' + verb + '\', \'' + arg + '\'' + (more || '') + ')';
  const btn = (label, onclick, ghost, title) => '<button' + (ghost ? ' class="ghost"' : '') + (title ? ' title="' + esc(title) + '"' : '') + ' onclick="' + onclick + '">' + esc(label) + '</button>';
  const dlBtn = (label, ghost) => btn(label, go('download', id), ghost);
  const ew = m.elsewhere, d = m.download;
  let st = '', a = '', b = '', stTitle = '';
  if (d && d.state === 'running') {
    st = lmStatus((d.copy ? 'Copying… ' : 'Downloading… ') + d.percent + '%', 'warn') + '<div class="lm-bar"><i style="width:' + d.percent + '%"></i></div>';
    b = btn('Cancel', go('cancel', jsq('model:' + m.id)), true);
    if (d.note) stTitle = d.note;                            // (a copy to a sleeping PC: it carries on by itself)
  } else if (m.update) {
    st = lmStatus(LM_TABLE_WORDS.updateAvail, 'warn');
    a = dlBtn('Update');
  } else if (m.kept) {
    st = lmStatus(LM_REMOVE_WORDS.kept, 'off');
    a = btn(LM_REMOVE_WORDS.add, go('addKept', id));
    b = btn(LM_REMOVE_WORDS.delete, go('deleteKept', id, ', \'' + jsq(m.name) + '\', \'' + jsq(m.sizeText) + '\''), true);
  } else if (m.installed) {
    st = lmStatus(LM_TABLE_WORDS.downloaded, '');
  } else if (ew) {
    st = lmStatus('Already in ' + esc(ew.program), '');
    stTitle = ew.name;
    if (ew.useName) a = btn('Use it', 'copyModel(this, \'' + jsq(ew.useName) + '\')');
    b = dlBtn('Download', true);
  } else if (m.copyFrom) {                                  // another PC, and this PC has the file: copying is faster than downloading again
    a = btn('Copy', go('send', id), false, 'Copy from this device (faster)');
    b = dlBtn('Download', true);
  } else {
    a = dlBtn('Download');
  }
  const err = d && d.state === 'error';
  const line2 = err ? '<div class="lm-meta lm-bad" title="' + esc(d.error) + '">' + esc(d.error) + '</div>' : '<div class="lm-meta" title="' + esc(m.blurb) + '">' + esc(m.blurb) + '</div>';
  return '<div class="lm-trow"><div style="min-width:0"><div class="lm-name">' + esc(m.name) + lmIcons(m, s.modelServer, s) + '</div>' + line2 + '</div>' +
    '<div class="lm-tsize">' + esc(m.sizeText) + '</div><div class="lm-tst"' + (stTitle ? ' title="' + esc(stTitle) + '"' : '') + '>' + st + '</div>' +
    '<div class="lm-tslots"><div class="lm-tslot">' + a + '</div><div class="lm-tslot">' + b + '</div></div></div>';
}

// The one place the "Engine / Pick a model / Your local models / own file" screens are drawn. The Local device tab draws it for this PC (s from /config/local-models);
// Other devices draws it for a paired PC (s from opLmState(), the same shape, read from that PC's Courier). s.scope is 'this' or the PC's id; every button goes through lmDo(),
// which sends it to this PC or to that PC. A button that PC's Courier can't do is simply not drawn (s.caps).
// x: { addOpen, addForm } -- the "Add it here…" panel belongs to the scope (a form here, a scan of that PC there).
function lmDeviceHtml(s, x) {
  const sc = s.scope || 'this';
  const remote = sc !== 'this';
  const q = jsq(sc);
  const caps = s.caps || {};
  const steps = list => remote ? '' : lmSteps(list);
  let h = (s.modelsOff && !remote ? '<div class="lm-muted" id="lmOffLine" style="margin:0 0 10px">' + esc(s.runsOn ? LM_OFF.runsOn.replace('{pc}', s.runsOn) : LM_OFF.off) + '</div>' : '') + steps([
    'Pick a drive with enough space for your models (Settings > Folders), or wait for your first download to ask.',
    'Press Download for the software below, then for a model.',
    'Press Start next to the model under "Your local models".',
    'Copy its name and paste it into SkyrimNet.']);

  // The folder is changed in Settings > Folders. Here it only shows when none is chosen yet (one amber line and the button).
  if (!remote && !s.folder) {
    h += '<div class="lm-statusline"><span class="lm-warn">⚠️ Choose where to keep the models.</span><button onclick="lmAct(lmChooseFolder)">Choose a folder</button></div>';
  }

  // 1. Engine
  const eng = s.engine;
  if (!eng.installed) {
    if (remote) {
      if (eng.note) h += '<div class="lm-statusline"><span class="lm-muted">' + esc(eng.note) + '</span></div>';
    } else {
      h += '<h3 class="lm-step">First time: set up local models</h3>' + lmSteps([
        'Make sure the graphics card shown is yours.',
        'Leave the highlighted "Best for your card" option selected.',
        'Press Download and wait for the bar to fill.',
        'Move on to "Pick a model" below.']);
      if (s.gpu) {
        const mem = s.gpu.memoryGb ? ' · ' + s.gpu.memoryGb + ' GB of <a class="gl-term" data-gl="graphics-memory">graphics memory</a>' : '';
        h += '<div class="lm-gpu"><div class="big">🎮</div><div><b>' + esc(s.gpu.name) + '</b>' + mem + '<div class="lm-muted">Found on this device</div></div></div>';
      } else {
        h += '<div class="lm-gpu"><div class="big">🎮</div><div><b>No graphics card found</b><div class="lm-muted">The processor-only version works, but it\'s slow.</div></div></div>';
      }
      const dl = eng.download;
      if (dl && (dl.state === 'running' || dl.state === 'paused')) {
        h += '<div class="lm-row" style="grid-template-columns:1fr auto"><div><div class="lm-name">' + (dl.state === 'paused' ? 'The software download' : 'Downloading the software…') + '</div></div><div>' + lmDlBlock(dl, 'engine', sc) + '</div></div>';
      } else {
        for (const o of eng.options.filter(o => !o.advanced)) h += lmEngineOption(o);
        const fast = eng.options.filter(o => o.advanced);
        if (fast.length) h += '<details class="lm-adv"' + (fast.some(o => o.kind === lmPickedEngine) ? ' open' : '') + '><summary>Advanced: a faster version for your card</summary>' + fast.map(lmEngineOption).join('') + '</details>';
        if (dl && dl.state === 'error') h += '<div class="lm-err">' + esc(dl.error) + '</div>';
        h += '<div class="lm-actions"><button onclick="lmDownloadEngine()">Download</button></div>';
      }
    }
  } else {
    const label = (eng.options.find(o => o.kind === eng.kind) || {}).label || eng.label || eng.kind;
    h += '<div class="lm-statusline"><span class="lm-muted">✓ Ready to run models' + (label ? ' (' + esc(label) + ')' : '') + '</span></div>' + lmEngineUpdateInline(s) + lmInstalledEngineExtras(eng, sc);
  }

  // 2. Model picker
  h += '<h3 class="lm-step">Pick a model</h3>' + steps([
    'Pick a model from the list that fits (no yellow dot).',
    'Press Download and wait for the bar to fill.',
    'Go to "Your local models" below and press Start.',
    'If you already have a model file, press Add your own file…, Browse… to find it, then press Add.']);
  h += '<div class="lm-hint" style="margin:0 0 8px">' + (s.modelServer
    ? 'These models work great with SkyrimNet. This device isn\'t running the game, so models can use the whole graphics card.'
    : 'These models work great with SkyrimNet. We check your graphics card to see which ones fit alongside the game.') + '</div>' + lmLegend(s.modelServer, s);
  h += '<div class="lm-tbl"><div class="lm-tbl-in"><div class="lm-thead"><span>' + LM_TABLE_WORDS.model + '</span><span>' + LM_TABLE_WORDS.size + '</span><span>' + LM_TABLE_WORDS.status + '</span><span></span></div>';
  for (const m of s.catalog) h += lmModelRow(m, s);
  h += '</div></div>';
  h += '<p class="lm-muted" style="margin-top:10px">Have your own model file? <span class="lm-link" onclick="lmDo(\'' + q + '\', \'addToggle\')">' + (x.addOpen ? 'Never mind' : 'Add it here…') + '</span>' +
       (!remote && typeof smuOpen === 'function' ? ' &nbsp; <span class="lm-link" onclick="smuOpen()">Set me up</span>' : '') + '</p>';
  if (x.addOpen) h += x.addForm;

  // 3. Installed
  if (s.installed.length) {
    h += '<h3 class="lm-step">Your local models</h3>' + steps([
      'Press Start next to the model and wait for it to say Running.',
      'Press Copy next to "Paste this into SkyrimNet".',
      'In SkyrimNet, choose the MultiProxy provider and paste that name as the model.',
      'If it says Problem, read the red line under the name and press Try again.']);
    for (const m of s.installed) {
      const st = LM_RUN[m.state] || LM_RUN.stopped;
      const id = jsq(m.id);
      // Starting a model fetches the engine first when it isn't here yet, so Start is never greyed out for that
      const ed = m.engineDl || ((m.engineWait && eng.download && eng.download.state === 'running') ? eng.download : null);
      const btn = (m.state === 'running' || m.state === 'starting')
        ? (caps.unload === false ? '' : '<button onclick="lmDo(\'' + q + '\', \'stop\', \'' + jsq(id) + '\')">' + (m.engineWait ? 'Cancel' : 'Stop') + '</button>')
        : (caps.load === false ? '' : '<button' + (s.modelsOff && !remote ? ' disabled title="' + esc(LM_OFF.hover) + '"' : '') + ' onclick="lmDo(\'' + q + '\', \'start\', \'' + jsq(id) + '\')">' + (m.state === 'failed' ? 'Try again' : 'Start') + '</button>');
      const rm = caps.delete === false ? '' : ' <button class="ghost" onclick="lmDo(\'' + q + '\', \'remove\', \'' + jsq(id) + '\', \'' + jsq(m.name) + '\', ' + (m.custom ? 'true' : 'false') + ', \'' + jsq(m.sizeText) + '\')">Remove</button>';
      const engineLine = m.engineWait ? '<div class="lm-muted">Downloading the engine software (first time only)…' +
        (ed && ed.total ? ' ' + (ed.done / 1e9).toFixed(1) + ' of ' + lmFmt(ed.total) : '') + '</div>' +
        (ed && ed.total ? '<div class="lm-bar"><i style="width:' + Math.min(100, Math.round(ed.done * 100 / ed.total)) + '%"></i></div>' : '') : '';
      h += '<div class="lm-row"><div><div class="lm-name">' + esc(m.name) + lmIcons({ vision: m.vision }, s.modelServer, s) + '</div>' +
           '<div class="lm-meta">Paste this into SkyrimNet\'s model box: <span class="lm-name-copy">' + esc(m.modelName) + '</span> ' +
           '<button class="copy" onclick="copyModel(this, \'' + jsq(m.modelName) + '\')">Copy</button></div>' +
           engineLine + (remote ? '' : lmSpeedLine(m.id) + (typeof lsModelTag === 'function' ? lsModelTag(m.id) : '')) +
           (m.message ? '<div class="lm-err">' + esc(m.message) + '</div>' : '') +
           (m.engineNote ? '<div class="lm-engine-note">⚠️ ' + esc(m.engineNote) + '</div>' : '') +
           (m.guardNote ? '<div class="lm-engine-note">⚠️ ' + esc(m.guardNote) + ' <span class="hp-how"><a onclick="openHelp(\'memory-guard\')">Help</a></span></div>' : '') + '</div>' +
           '<div class="lm-muted">' + esc(m.sizeText) + '</div>' +
           '<div>' + lmStatus(st[0], st[1]) + '</div>' +
           '<div>' + btn + rm + '</div></div>';
    }
    if (!remote) h += '<p class="lm-muted">Models start when MultiProxy starts, and stop when it closes.</p>';
  }
  return h;
}
function lmAddFormHtml() {
  return '<div class="lm-form"><label>Model file</label><div class="lm-line"><input type="text" id="lmAddPath" placeholder="Full path to the file" value="' + esc(lmAddForm.path) + '"><button class="ghost" onclick="lmBrowse(\'path\')">Browse…</button></div>' +
         '<label>Vision file (optional, for models that see pictures)</label><div class="lm-line"><input type="text" id="lmAddMm" placeholder="Full path to the matching vision file" value="' + esc(lmAddForm.mmproj) + '"><button class="ghost" onclick="lmBrowse(\'mmproj\')">Browse…</button></div>' +
         '<div class="lm-actions"><button onclick="lmAddSubmit()">Add</button></div></div>';
}
function lmRender() {
  const box = document.getElementById('lmMain');
  const s = lmState;
  if (!s) return;
  // Don't rebuild the page under someone who is typing in the Add form.
  const ae = document.activeElement;
  if (ae && (ae.id === 'lmAddPath' || ae.id === 'lmAddMm')) return;
  const running = s.installed.filter(m => m.state === 'running').length;
  document.getElementById('lmStatus').textContent = s.installed.length ? (running + ' of ' + s.installed.length + ' running') : '';
  if (!s.supported) {
    box.innerHTML = '<div class="lm-muted">Local models don\'t run on Mac yet. Use a cloud provider instead.</div>';
    return;
  }
  const keepError = document.getElementById('lmTopError');
  const errText = keepError && keepError.style.display !== 'none' ? keepError.textContent : '';
  s.scope = 'this';
  box.innerHTML = '<div id="lmTopError" class="lm-err" style="display:none;margin:0 0 10px"></div>' + lmDeviceHtml(s, { addOpen: lmAddOpen, addForm: lmAddFormHtml() });
  if (errText) lmShowError(errText);
}
// ---------------- Graphics memory card (Revisions 5 and 6) ----------------
// Polls every few seconds ONLY while the Local models tab is showing. `memPick` is which device's card is on
// screen ('this' or a nickname); it's remembered while the page stays open.
let memPick = 'this';
let memTimer = null;
const MEM_COLORS = { skyrim: '#8b6fd6', other: '#6b7280' };
const MEM_MODEL_COLORS = ['var(--accent)', '#4aa3a2', '#c26d9a', '#7f9f4a', '#5b8bd6'];

function memTabOpen() {
  const pane = document.getElementById('paneLocal');
  return !!pane && pane.style.display !== 'none';
}
function memStart() { clearTimeout(memTimer); memRefresh(); }
function memGb(mb) { return (mb / 1024).toFixed(1) + ' GB'; }

async function memRefresh() {
  clearTimeout(memTimer);
  if (!memTabOpen()) return;
  if (document.hidden) { memTimer = setTimeout(memRefresh, 3500); return; }       // (a hidden browser tab asks for nothing)
  const asked = memPick;
  let data = null;
  try { data = await (await fetch('/config/local-models/memory?pc=' + encodeURIComponent(asked))).json(); } catch (e) {}
  if (data && asked === memPick) memRender(data);     // an answer for a device that's no longer the picked one is ignored
  memTimer = setTimeout(memRefresh, 3500);
}
function memPickChanged(v) {
  memPick = v;
  const body = document.getElementById('memBody');
  const opt = document.querySelector('#memPicker option:checked');
  if (body) body.innerHTML = '<div class="mem-peak">Reading ' + esc(opt ? opt.textContent.replace(/ \(offline\)$/, '') : v) + '…</div>';   // never the last device's numbers under the new name
  memRefresh();
}
async function memReset() {
  try { await lmPost('/config/local-models/memory/reset'); } catch (e) {}
  await memRefresh();
  lmRefresh();
}

function memBar(d, showSkyrim) {
  const total = d.totalMb || 1;
  const segs = [];
  let mi = 0;
  for (const g of d.segments || []) {
    if (g.key === 'skyrim' && !showSkyrim) continue;
    segs.push({ label: g.label, mb: g.mb, color: g.key === 'skyrim' ? MEM_COLORS.skyrim : MEM_MODEL_COLORS[mi++ % MEM_MODEL_COLORS.length] });
  }
  segs.push({ label: 'Everything else', mb: d.otherMb, color: MEM_COLORS.other });
  const bar = segs.map(g => '<i style="width:' + Math.min(100, g.mb * 100 / total).toFixed(1) + '%;background:' + g.color + '" title="' + esc(g.label) + ' ' + memGb(g.mb) + '"></i>').join('');
  const key = segs.map(g => '<span style="--c:' + g.color + '">' + esc(g.label) + ' <b>' + memGb(g.mb) + '</b></span>').join('') +
    '<span style="--c:transparent">Free <b>' + memGb(d.freeMb) + '</b></span>';
  return '<div class="mem-bar">' + bar + '</div><div class="mem-key">' + key + '</div>';
}

function memRender(d) {
  const card = document.getElementById('memCard');
  const multi = (d.pcs || []).length > 1;
  if (!multi && d.pc === 'this' && !d.available) { card.style.display = 'none'; return; }   // nothing to show: no fake bar
  // The picker is built once and only rebuilt when the list of devices really changes, so an open dropdown (or one that still has
  // focus after a pick) is never disturbed; only the body below it is redrawn on every refresh.
  const picker = multi ? '<select id="memPicker" onchange="memPickChanged(this.value)">' + d.pcs.map(p =>
    '<option value="' + esc(p.id) + '"' + (p.online ? '' : ' style="color:var(--muted)"') + '>' + esc(p.label) + (p.online ? '' : ' (offline)') + '</option>').join('') + '</select>' : '';
  const pickerKey = multi ? d.pcs.map(p => p.id + '|' + p.label + '|' + p.online).join(';') : '';
  let body = '';
  let live = '';
  if (d.pc === 'this' || d.state === 'ok') {
    if (!d.available && d.pc === 'this') { card.style.display = 'none'; return; }
    const showSkyrim = d.pc === 'this' && d.skyrimShown;
    live = d.pc !== 'this' || !d.skyrimShown || d.skyrimRunning ? '<span class="mem-live">● Live</span>' : '<span class="mem-live" style="color:var(--muted)">· Skyrim isn\'t running</span>';
    body += '<div class="mem-sub">' + esc(d.pc === 'this' ? d.card : d.name + ' · ' + d.card) + ' · ' + memGb(d.totalMb) + live + '</div>' + memBar(d, showSkyrim);
    if (showSkyrim) {
      if (d.peakMb) {
        body += '<div class="mem-peak">Skyrim\'s highest memory use: <b>' + memGb(d.peakMb) + '</b>' + (d.skyrimRunning ? '' : ' (seen ' + esc(d.peakWhen) + ')') +
          '. Models will only use the free space left over. <button class="ghost" onclick="memReset()">Reset</button></div>';
      } else {
        body += '<div class="mem-peak">Play Skyrim once so MultiProxy can measure how much space it uses.</div>';
      }
    } else if (d.pc !== 'this') {
      body += '<div class="mem-peak">This device doesn\'t run Skyrim, so models can use the whole graphics card.</div>';
    }
  } else if (d.state === 'own') {
    body = '<div class="mem-peak">' + esc(d.name) + ' was connected directly (without the MultiProxy Courier). We can\'t see its graphics memory here, but its models will still work.</div>';
  } else if (d.state === 'offline') {
    body = '<div class="mem-peak">' + esc(d.name) + ' is offline.</div>';
  } else if (d.state === 'older') {
    body = '<div class="mem-peak">' + esc(d.name) + ' needs the newer MultiProxy to show this.</div>';
  }
  if (!document.getElementById('memBody') || card.dataset.pickerKey !== pickerKey) {
    card.innerHTML = '<div class="mem-head"><h3>Graphics memory</h3>' + picker + '</div>' + lmSteps([
      'The free space on the bar is what a model can use.',
      'Play Skyrim once so MultiProxy can measure the game\'s size. Press Reset if you change your mods.',
      'If you have more than one device, pick it from the drop-down to see its graphics card.']) + '<div id="memBody"></div>';
    card.dataset.pickerKey = pickerKey;
  }
  const sel = document.getElementById('memPicker');
  if (sel && sel.value !== d.pc && !(document.activeElement === sel)) sel.value = d.pc;
  document.getElementById('memBody').innerHTML = body;
  card.style.display = '';
}

lmRefresh();
"""
