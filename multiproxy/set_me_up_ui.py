"""The Set me up screens (2026-10-26), built to design/mockup-easier-installing.html section 1 (1a to 1e), design/easier-installing-decisions.md wins on differences.

Where it lives: a block first on Get started's Welcome step, and a card on Overview until it has been used. Both open the same screens inside the Get started card.
The plan, the sizes and every sentence about this PC come from the server (set_me_up.py WORDS); the words of the buttons and headings are in SMU_WORDS below, so a Gemini pass
is a one-spot swap. Nothing is drawn that the server did not measure: the bars are the install's real byte counts. Everything from the server goes through esc().
"""

import models_off

CSS = r"""
  /* Set me up (2026-10-26) -- smu-* classes. Text uses the full width of its card. */
  .smu-q { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 0; border-top:1px solid var(--line) }
  .smu-row { display:grid; grid-template-columns:22px 1fr auto; gap:12px; align-items:center; padding:12px 0; border-top:1px solid var(--line) }
  .smu-row.run { grid-template-columns:1fr auto }
  .smu-row .nm { font-weight:600 }
  .smu-row .dt { color:var(--muted); font-size:0.82rem }
  .smu-row .sz { color:var(--muted); white-space:nowrap }
  .smu-row .lm-bar { width:100%; margin-left:0 }
  .smu-ft { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-top:16px }
  .smu-ft > div { display:flex; gap:8px }
  .smu-table { margin:12px 0 0 } .smu-table th.num { text-align:right } .smu-table td:first-child, .smu-table th:first-child { width:22px }
  .smu-warn { display:flex; gap:8px; align-items:flex-start; color:var(--warn); font-size:0.88rem; margin:10px 0 0; line-height:1.5 }
  .smu-warn .op-dot { margin-top:6px }
"""

SMU_WORDS = r"""const SMU_WORDS = {
  title: 'Set me up',
  sub: 'Look at this PC and install a small set of models that works on it. You can change anything later.',
  button: 'Set me up',
  pickMyself: 'or pick the models yourself',
  cardTitle: 'Set me up',
  cardLine: 'Look at this PC and install a small set of models that works on it. You can change anything later.',
  cardShow: 'Show progress',
  fits: 'Here is what fits this PC',
  yes: 'Yes',
  no: 'No, another PC',
  installing: 'Installing',
  ready: 'Ready',
  tell: 'Tell SkyrimNet',
  choose: 'Choose…',
  chooseWhere: 'Where to keep the models',
  back: 'Back',
  cancel: 'Cancel',
  pause: 'Pause',
  resume: 'Resume',
  paused: 'Paused',
  soFar: 'GB downloaded',
  waiting: 'Waiting',
  done: 'Done',
  toDownload: 'to download',
  install: 'Install',
  thing: 'thing',
  things: 'things',
  none: 'Nothing to install.',
  measuring: 'Measuring…',
  measure: 'Measure',
  estimate: 'Continue with an estimate',
  notSeen: "Couldn't see Skyrim running. Start it and try again.",
};
"""

JS = SMU_WORDS + models_off.js_words("SMU_OFF") + r"""// ---------------- Set me up ----------------
let smu = { gate: false, skipped: false, miss: false, measuring: false, open: false, look: null, game: null, chat: null, voice: false, plan: null, job: null, err: '', timer: null, busy: false };

async function smuApi(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, opts); } catch (e) { throw new Error("MultiProxy isn't answering. Is it still running?"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Something went wrong.');
  return d;
}
const SMU_DASH = '–';
function smuGb(n) { return (n / 1e9).toFixed(1) + ' GB'; }
function smuBlock() {                        // the first thing on Get started's Welcome step, only on a brand-new install
  if (typeof gs === 'undefined' || !gs || !(gs.setMeUp || {}).show) return '';
  return '<div class="gs-start"><h3>' + esc(SMU_WORDS.title) + '</h3><p class="gs-hint">' + esc(SMU_WORDS.sub) + ' <span class="hp-how"><a onclick="openHelp(\'set-me-up\')">Help</a></span></p>' +
    '<div class="gs-row"><button class="primary" onclick="smuOpen()">' + esc(SMU_WORDS.button) + '</button><button class="gs-link" onclick="smuPickMyself()">' + esc(SMU_WORDS.pickMyself) + '</button></div></div>';
}
function smuPickMyself() { smuStopTimer(); smu.open = false; if (typeof gsRestorePanel === 'function') gsRestorePanel(); showMainTab('local'); }
function smuStopTimer() { if (smu.timer) { clearInterval(smu.timer); smu.timer = null; } }

async function smuOpen() {
  smu.open = true; smu.err = ''; smu.game = null; smu.plan = null; smu.gate = false; smu.skipped = false; smu.miss = false;
  if (typeof gsRestorePanel === 'function') gsRestorePanel();
  showMainTab('start');
  const card = document.getElementById('gsCard');
  if (card) card.innerHTML = '<div id="smuRoot" class="lm-muted">Looking at this PC…</div>';
  await smuRefresh();
}
async function smuRefresh() {
  try { smu.look = await smuApi('/config/set-me-up'); smu.job = smu.look.job; }
  catch (e) { smu.err = e.message; }
  if (smu.job && smu.job.state === 'running') smuStartTimer();
  smuDraw();
}
function smuStartTimer() {
  if (smu.timer) return;
  smu.timer = setInterval(async () => {
    if (!document.getElementById('smuRoot')) { smuStopTimer(); return; }
    try { const d = await smuApi('/config/set-me-up/job'); smu.job = d.job; } catch (e) { return; }
    if (!smu.job || smu.job.state !== 'running') smuStopTimer();
    smuDraw();
  }, 1000);
}
async function smuAnswer(game) {
  smu.game = game; smu.err = ''; smu.chat = null; smu.gate = false; smu.skipped = false; smu.miss = false;
  if (game) { await smuMeasure(false); return; }              // Yes: look for the running game first; if it is not running, the "Start Skyrim first" steps come before the table
  await smuReplan();
}
async function smuMeasure(pressed) {
  if (smu.measuring) return;
  smu.measuring = true; smu.err = ''; smu.miss = false;
  smuDraw();
  let res = {};
  try { res = await smuApi('/config/set-me-up/measure', {}) || {}; } catch (e) { smu.err = e.message; }
  smu.measuring = false;
  smu.miss = !!pressed && !res.measured;                      // pressed Measure and nothing was read: one amber line
  await smuReplan();                                          // the verdicts and the pick follow the new figure; a pick that is still valid stays
}
async function smuReplan() {
  try { smu.plan = await smuApi('/config/set-me-up/plan', { game: !!smu.game, voice: !!smu.voice, chat: smu.chat }); smu.chat = smu.plan.chat; smu.gate = !!(smu.game && smu.plan.game && !smu.plan.game.measured && !smu.skipped); }          // Yes and no figure yet: Start Skyrim first
  catch (e) { smu.err = e.message; }
  smuDraw();
}
function smuContinue() { smu.skipped = true; smu.gate = false; smu.miss = false; smuDraw(); }
function smuGo(i) {                          // a button of the no-room panel (its steps come from the server)
  const s = (((smu.plan || {}).noRoom || {}).steps || [])[i];
  if (!s) return;
  smuStopTimer(); smu.open = false;
  if (typeof gsRestorePanel === 'function') gsRestorePanel();
  if (s.action === 'providers') { showMainTab('setup'); return; }
  showMainTab('other');
  if (s.action === 'pc') { if (typeof opPcOpen !== 'undefined') opPcOpen[s.id] = true; if (typeof opRender === 'function') opRender(); }
  else if (s.action === 'add') { if (typeof opAddNew === 'function') opAddNew(); if (typeof opGoAdd === 'function') setTimeout(opGoAdd, 600); }
}
function smuPickModel(id) { smu.chat = id; smuReplan(); }
function smuVoice(on) { smu.voice = !!on; smuReplan(); }
async function smuChoose() {
  smu.err = '';
  try {
    const d = await smuApi('/config/local-models/pick-folder', {});
    if (d.path) { await smuApi('/config/local-models/folder', { path: d.path }); smu.look = await smuApi('/config/set-me-up'); await smuReplan(); return; }
  } catch (e) { smu.err = e.message; }
  smuDraw();
}
async function smuInstall() {
  if (smu.busy) return;
  smu.busy = true; smu.err = '';
  try { const d = await smuApi('/config/set-me-up/install', { game: !!smu.game, voice: !!smu.voice, chat: smu.chat }); smu.job = d.job; smuStartTimer(); }
  catch (e) { smu.err = e.message; }
  smu.busy = false;
  smuDraw();
}
async function smuPause() { try { const d = await smuApi('/config/set-me-up/pause', {}); smu.job = d.job; } catch (e) { smu.err = e.message; } smuStopTimer(); smuDraw(); }
async function smuResume() { try { const d = await smuApi('/config/set-me-up/resume', {}); smu.job = d.job; smuStartTimer(); } catch (e) { smu.err = e.message; } smuDraw(); }
async function smuCancel() { try { const d = await smuApi('/config/set-me-up/cancel', {}); smu.job = d.job; } catch (e) { smu.err = e.message; } smuStopTimer(); smuDraw(); }
async function smuAgain() { try { await smuApi('/config/set-me-up/dismiss', {}); } catch (e) {} smu.job = null; smu.plan = null; await smuReplan(); }
async function smuTell() {                   // the last Get started step that already exists: nothing new there
  smuStopTimer(); smu.open = false;
  try { await smuApi('/config/set-me-up/dismiss', {}); } catch (e) {}
  await openGetStarted();
  gsGo(4);
}
function smuClose() { smuStopTimer(); smu.open = false; if (typeof openGetStarted === 'function') openGetStarted(); }

function smuWarn(text) { return text ? '<div class="smu-warn"><span class="op-dot warn"></span><span>' + esc(text) + '</span></div>' : ''; }
function smuHeadline() {
  const l = smu.look || {};
  const card = l.card && l.card.name ? esc(l.card.name) + (l.card.memoryGb ? ', ' + l.card.memoryGb + ' GB graphics memory' : '') : '';
  const disk = l.freeGb != null ? l.freeGb + ' GB free on ' + esc(l.drive || 'this drive') : '';
  return '<p class="gs-hint">' + [card, disk].filter(Boolean).join(', ') + (card || disk ? '.' : '') + '</p>' + (l.cardNote ? '<p class="gs-hint">' + esc(l.cardNote) + '</p>' : '');
}
function smuAsk() {
  return '<div class="smu-q"><div><b>' + esc((smu.look || {}).question || '') + '</b></div><div class="gs-row" style="margin:0"><button class="' + (smu.game === true ? 'primary' : '') + '" onclick="smuAnswer(true)">' + esc(SMU_WORDS.yes) +
    '</button><button class="' + (smu.game === false ? 'primary' : '') + '" onclick="smuAnswer(false)">' + esc(SMU_WORDS.no) + '</button></div></div>' +
    (smu.game === null ? '<div class="gs-hint">' + esc((smu.look || {}).questionHint || '') + '</div>' : '');
}
function smuGame(p) {
  const g = p.game;
  if (!g) return '';
  const gb = (mb) => (mb / 1024).toFixed(1) + ' GB';
  const data = '<span class="lm-muted">' + esc(g.label) + '</span> <b>' + gb(g.mb) + '</b>' + (g.measured ? ' &nbsp; <span class="lm-muted">' + esc(g.bufferLabel) + '</span> <b>' + gb(g.bufferMb) + '</b>' +
    (g.when ? ' &nbsp; <span class="lm-muted">' + esc(g.when) + '</span>' : '') : '');
  const btn = '<button' + (smu.measuring ? ' disabled' : '') + ' onclick="smuMeasure(true)">' + esc(smu.measuring ? SMU_WORDS.measuring : g.button) + '</button>';
  const steps = !g.measured && g.steps ? '<ol class="op-steps">' + g.steps.map(t => '<li>' + esc(t) + '</li>').join('') + '</ol>' : '';
  return '<div class="smu-q"><div>' + data + '</div>' + btn + '</div>' + steps;
}
function smuGateHtml() {                     // Yes, and the game is not running: the steps first, the table only after Measure or Continue
  const p = smu.plan;
  const steps = (((p || {}).game || {}).steps) || [];
  return '<ol class="op-steps">' + steps.map(t => '<li>' + esc(t) + '</li>').join('') + '</ol>' + (smu.miss ? smuWarn(SMU_WORDS.notSeen) : '') +
    '<div class="smu-ft"><span></span><div><button' + (smu.measuring ? ' disabled' : '') + ' onclick="smuContinue()">' + esc(SMU_WORDS.estimate) + '</button><button class="primary"' + (smu.measuring ? ' disabled' : '') +
    ' onclick="smuMeasure(true)">' + esc(smu.measuring ? SMU_WORDS.measuring : SMU_WORDS.measure) + '</button></div></div>';
}
function smuNoRoomHtml(n) {                  // no chat model fits next to the game: the next steps instead of an empty list
  return smuWarn(n.warning) + '<ol class="op-steps">' + (n.steps || []).map((s, i) => '<li>' + esc(s.text) + ' &nbsp; <button class="ghost" onclick="smuGo(' + i + ')">' + esc(s.button) + '</button></li>').join('') + '</ol>';
}
function smuPlanHtml() {
  const p = smu.plan;
  if (!p) return smu.measuring ? '<div class="lm-muted" style="margin:12px 0 0">' + esc(SMU_WORDS.measuring) + '</div>' : '';
  if (smu.gate) return smuGateHtml();
  let h = smuGame(p) + smuWarn(p.chatWarning) + smuWarn(p.warning && p.folder ? p.warning : '') + (p.noRoom ? smuNoRoomHtml(p.noRoom) : '');         // the only warning lines: at the top, above the table
  if (!p.folder) {
    h += '<div class="smu-q"><div><b>' + esc(SMU_WORDS.chooseWhere) + '</b></div><button onclick="smuChoose()">' + esc(SMU_WORDS.choose) + '</button></div>';
  }
  const cols = p.columns || [];
  const cell = (t) => t ? esc(t) : '';
  const stat = (t) => t === SMU_DASH ? '<span class="soft">' + esc(t) + '</span>' : '<span class="op-dot on"></span>' + esc(t);
  let rows = (p.models || []).map(m => {
    const dim = m.selectable ? '' : ' style="opacity:.55"';
    return '<tr' + dim + '><td><input type="radio" name="smuModel" aria-label="' + esc(m.name) + '" value="' + esc(m.id) + '"' + (m.selected ? ' checked' : '') + (m.selectable ? '' : ' disabled') +
      ' onchange="smuPickModel(this.value)"></td><td>' + esc(m.name) + '</td><td class="num">' + smuGb(m.bytes) + '</td><td>' + cell(m.text) + '</td><td class="ov-st">' + stat(m.status) + '</td></tr>';
  }).join('');
  rows += p.items.filter(i => i.key !== 'chat').map(i => {
    const box = i.optional ? '<input type="checkbox" aria-label="' + esc(i.name) + '"' + (i.ticked ? ' checked' : '') + (i.disabled ? ' disabled' : '') + ' onchange="smuVoice(this.checked)">' : '';
    return '<tr><td>' + box + '</td><td>' + esc(i.name) + (i.optionalNote ? ' <span class="soft">' + esc(i.optionalNote) + '</span>' : '') + '</td><td class="num">' + (i.size ? smuGb(i.size) : '') + '</td><td>' + cell(i.fits) + '</td><td class="ov-st">' + stat(i.status) + '</td></tr>';
  }).join('');
  h += '<table class="ov-table smu-table"><thead><tr><th></th>' + cols.map((c, k) => '<th' + (k === 1 ? ' class="num"' : '') + '>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>' + rows + '</tbody></table>';
  h += (p.notes || []).map(n => '<div class="gs-hint" style="margin:10px 0 0">' + esc(n) + '</div>').join('');
  const n = p.installCount;
  h += '<div class="smu-ft"><span class="lm-muted">' + (p.needBytes ? esc(smuGb(p.needBytes) + ' ' + SMU_WORDS.toDownload) : '') + '</span><div><button class="primary"' + (p.canInstall && !smu.busy ? '' : ' disabled') +
    ' onclick="smuInstall()">' + esc(p.allHere ? p.useWord : SMU_WORDS.install + ' ' + n + ' ' + (n === 1 ? SMU_WORDS.thing : SMU_WORDS.things)) + '</button></div></div>';
  return h;
}
function smuRunHtml() {
  const j = smu.job;
  let h = '<h2>' + esc(SMU_WORDS.installing) + '</h2>';
  h += j.steps.map(s => {
    const size = s.state === 'done' ? SMU_WORDS.done : s.state === 'waiting' ? SMU_WORDS.waiting : (s.total ? smuGb(s.done) + ' of ' + smuGb(s.total) + (s.left ? ' · ' + s.left : '') : '');
    return '<div class="smu-row run"><div><div class="nm">' + esc(s.label) + '</div><div class="lm-bar"><i style="width:' + (s.state === 'done' ? 100 : s.percent) + '%"></i></div></div><span class="sz">' + esc(size) + '</span></div>';
  }).join('');
  h += smuWarn(j.warning);
  h += '<div class="smu-ft"><span></span><div><button class="ghost" onclick="smuPause()">' + esc(SMU_WORDS.pause) + '</button> <button onclick="smuCancel()">' + esc(SMU_WORDS.cancel) + '</button></div></div>';
  return h;
}
function smuPausedHtml() {
  const j = smu.job;
  let h = '<h2>' + esc(SMU_WORDS.paused) + ' · ' + esc((j.pausedBytes / 1e9).toFixed(1) + ' ' + SMU_WORDS.soFar) + '</h2>';
  h += j.steps.map(s => '<div class="smu-row run"><div><div class="nm">' + esc(s.label) + '</div><div class="lm-bar"><i style="width:' + (s.state === 'done' ? 100 : s.percent) + '%"></i></div></div><span class="sz">' + esc(s.state === 'done' ? SMU_WORDS.done : s.total ? smuGb(s.done) + ' of ' + smuGb(s.total) : '') + '</span></div>').join('');
  h += '<div class="smu-ft"><span></span><div><button class="primary" onclick="smuResume()">' + esc(SMU_WORDS.resume) + '</button> <button class="ghost" onclick="smuCancel()">' + esc(SMU_WORDS.cancel) + '</button></div></div>';
  return h;
}
// The Settings switch "Don't run models on this PC" is on: no question and no measuring, just where to go next and a way to turn it back on.
function smuOffHtml() {
  const go = ['smuGoTab(\'other\')', 'smuGoAddPc()', 'smuGoTab(\'setup\')'];
  return '<h2>' + esc(SMU_OFF.off) + '</h2><ol class="op-steps">' + SMU_OFF.setMeUpNext.map((t, i) => '<li><a class="lm-link" onclick="' + go[i] + '">' + esc(t) + '</a></li>').join('') + '</ol>' +
    (smu.look.cloudOffer ? '<div class="lm-muted" style="margin:0 0 10px">' + esc(SMU_OFF.cloudQ) + ' <button onclick="smuCloudOnly()">' + esc(SMU_OFF.cloudGo) + '</button></div>' : '') +
    '<div class="smu-ft"><button class="gs-link" onclick="smuClose()">' + esc(SMU_WORDS.back) + '</button><a class="lm-link" onclick="smuTurnOn()">' + esc(SMU_OFF.turnOn) + '</a></div>';
}
async function smuCloudOnly() {                      // the player pressed the button: Decisions > Who answers first > Cloud only is turned on (never on its own)
  try { await smuApi('/config/decisions/settings', { cloudOnly: true }); } catch (e) { smu.err = e.message; }
  await smuRefresh();
}
function smuGoTab(t) { smuStopTimer(); smu.open = false; showMainTab(t); }
function smuGoAddPc() { smuGoTab('other'); if (typeof opAddNew === 'function') { try { opAddNew(); } catch (e) {} } }
function smuTurnOn() { smuStopTimer(); smu.open = false; if (typeof openSettings === 'function') openSettings('folders'); }
function smuDraw() {
  const root = document.getElementById('gsCard');
  if (!root || !smu.open) return;
  const j = smu.job;
  let h = '<div id="smuRoot">';
  if (smu.look && smu.look.modelsOff && !(j && (j.state === 'running' || j.state === 'done'))) h += smuOffHtml();
  else if (j && j.state === 'running') h += smuRunHtml();
  else if (j && j.state === 'paused') h += smuPausedHtml();
  else if (j && j.state === 'done') {
    h += '<h2>' + esc(SMU_WORDS.ready) + '</h2><p>' + esc(j.message) + '</p><div class="smu-ft"><span></span><div><button class="primary" onclick="smuTell()">' + esc(SMU_WORDS.tell) + '</button></div></div>';
  } else {
    h += '<h2>' + esc(SMU_WORDS.fits) + '</h2>' + smuHeadline() + smuAsk();
    if (j && j.state === 'error') h += smuWarn(j.error);
    h += smuPlanHtml();
    h += '<div class="smu-ft"><button class="gs-link" onclick="smuClose()">' + esc(SMU_WORDS.back) + '</button><span></span></div>';
  }
  if (smu.err) h += smuWarn(smu.err);
  root.innerHTML = h + '</div>';
}

// Overview's card, until Set me up has been used.
function ovSetMeUp(d) {
  const s = d.setMeUp || {};
  if (!s.show) return '';
  return '<div class="ov-setup"><div><b>' + esc(SMU_WORDS.cardTitle) + '</b> <span class="soft">' + esc(SMU_WORDS.cardLine) + '</span></div><button class="primary" onclick="smuOpen()">' + esc(s.running ? SMU_WORDS.cardShow : SMU_WORDS.button) + '</button></div>';
}
// A hand-over from Get started: its Welcome step draws the block, and a redraw of that step must not wipe a Set me up screen that is open.
(function () {
  const orig = gsRender;
  gsRender = function () { if (smu.open) { smuDraw(); return; } return orig.apply(this, arguments); };
})();
"""
