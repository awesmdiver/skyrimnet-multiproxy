"""The dashboard's "Other devices" tab (2026-09-30; every device run through its Courier 2026-10-01), built to
design/dashboard-multi-pc-control-mockup.html (Revision 2) and design/multiproxy-courier-mockup.html ("On your gaming
desktop"). Plain strings in their own file, spliced into multiproxy.py's page, so none of the CSS/JS needs doubled braces.
Everything that came from the server or another device goes through esc() (defined by the Local models card's script, which
is on the same page) before it reaches HTML.
"""
import jump_ui
import tab_desc_ui

OTHER_PCS_CSS = r"""
  /* Other devices tab -- op-* classes; the lm-* ones from the Local models card are reused */
  .op-sw { width:38px; height:20px; border-radius:10px; background:var(--line2); position:relative; flex-shrink:0; margin-top:2px; cursor:pointer; border:none; padding:0 }
  .op-sw::after { content:""; position:absolute; top:3px; left:3px; width:14px; height:14px; border-radius:50%; background:var(--bg) }
  .op-sw.on { background:var(--accent) } .op-sw.on::after { left:21px }
  .op-toggle { display:flex; gap:12px; align-items:flex-start; margin:6px 0 12px }
  .op-boxes { display:flex; gap:16px; flex-wrap:wrap; margin:10px 0 }
  .op-box { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 16px }
  .op-code { font-family:ui-monospace,Consolas,monospace; font-size:1.5rem; letter-spacing:0.15em; color:var(--accent-text) }
  .op-addr { font-family:ui-monospace,Consolas,monospace; font-size:0.95rem; color:var(--accent-text) }
  .op-cmd { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 14px; display:flex; justify-content:space-between; gap:10px; align-items:center; margin:6px 0 }
  .op-cmd code { font-size:0.78rem; overflow-wrap:anywhere }
  .op-found { display:grid; grid-template-columns:1fr 110px auto; gap:12px; align-items:center; border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:9px 0; background:var(--panel) }
  .op-banner { border:1px solid color-mix(in srgb, var(--accent) 45%, transparent); background:color-mix(in srgb, var(--accent) 7%, transparent); border-radius:10px; padding:16px 18px; margin:16px 0 }
  .op-banner h2 { margin:0 0 4px; font-size:1.15rem }
  /* One device: a collapsible row. The dots carry the only color; icons are grey. */
  .op-server { border:1px solid var(--line); border-radius:8px; margin:9px 0; background:var(--panel) }
  .op-head { display:flex; align-items:center; gap:10px; padding:12px 14px; cursor:pointer; flex-wrap:wrap }
  .op-head.op-off { opacity:0.8 }          /* (not .off: the page's own .off fills a box with grey) */
  .op-arrow { color:var(--muted); width:12px; font-size:0.75rem }
  .op-grow { flex:1 }
  .op-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:8px; vertical-align:1px; flex-shrink:0 }
  .op-dot.on { background:var(--ok) } .op-dot.off { background:var(--err) } .op-dot.warn { background:var(--warn) } .op-dot.idle { background:var(--muted) }        /* idle = off or not loaded on purpose (grey); off = a real fault or an offline device (red) */
  .op-dot.none { background:transparent; border:1px solid var(--line) }
  .op-app { font-size:0.75rem; color:var(--muted); border:1px solid var(--line); border-radius:6px; padding:0 7px; cursor:help }
  .op-pw { font-size:0.95rem; cursor:help; filter:grayscale(1) brightness(1.4); opacity:0.75 }
  .op-steps { margin:6px 0 0 22px; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px; counter-reset:op }
  .op-steps > li { font-size:0.9rem; line-height:1.5 }
  .op-steps > li::before { counter-increment:op; content:counter(op) ". "; font-weight:600 }
  .op-start { margin:2px 0 14px }
  .op-start h4 { margin:0 0 2px; font-size:0.95rem }
  .op-body { padding:0 14px 14px 34px }
  .op-stats { display:flex; gap:6px 18px; flex-wrap:wrap; font-size:0.78rem; color:var(--muted); margin:2px 0 12px }
  .op-stats b { color:var(--text); font-weight:600 }
  .op-prog { display:flex; align-items:center; gap:6px; flex-wrap:wrap; padding:8px 0 4px; border-top:1px solid var(--line); margin-top:8px }
  .op-prog .op-note { flex-basis:100%; margin-left:17px }
  .op-prog .lm-link { font-size:0.85rem }
  .op-m { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px; align-items:center; padding:7px 0 7px 17px }
  .op-m code { font-size:0.74rem; color:var(--accent-text) }
  .op-m .lm-meta { white-space:normal }
  .op-btns { display:flex; gap:6px; align-items:center }
  .uc-head { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding-right:14px }          /* Update all ends where the Remove buttons below it end */
  .uc-head h3 { margin:0 }
  .uc-line { display:flex; align-items:center; gap:8px 14px; flex-wrap:wrap; padding:0 14px 10px 34px }          /* text on the left, the action at the right edge (the same edge as Remove) */
  .uc-line > button { margin-left:auto; flex:none }
  .uc-line > .uc-r, .uc-line > .uc-fail.uc-r { margin-left:auto }          /* the words sit right beside their button, at the right edge */
  .uc-line > .uc-r ~ button { margin-left:0 }
  .uc-line > .uc-r ~ .uc-fail, .uc-line > .uc-fail.uc-r { flex:none }
  .uc-line .uc-note { color:var(--muted); font-size:0.82rem }
  .uc-line .lm-bar { margin:0; flex:1 1 160px; min-width:120px }
  .uc-line .uc-fail { margin:0; flex:1 1 auto }
  .uc-fail span:last-child { color:var(--warn) }
  .uc-indet i { width:35%; animation:ucSlide 1.4s ease-in-out infinite }
  @keyframes ucSlide { 0% { margin-left:0 } 50% { margin-left:65% } 100% { margin-left:0 } }
  .op-dl { display:flex; gap:8px; margin-top:12px; align-items:center; flex-wrap:wrap }
  .op-dl input { flex:1; min-width:220px; font-family:system-ui,sans-serif }
  .op-set { display:flex; gap:16px; flex-wrap:wrap; align-items:center; margin-top:14px; padding-top:10px; border-top:1px solid var(--line); font-size:0.78rem; color:var(--muted) }
  .op-set label { display:flex; gap:6px; align-items:center; cursor:pointer }
  .op-set input { margin:0 }
  .op-set .op-models { margin-right:auto }
  .op-ro { padding:4px 0 4px 17px }
  .op-modal { position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:50; display:flex; align-items:center; justify-content:center; padding:20px }
  .op-modal-box { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px 20px; width:min(860px, 100%); max-height:85vh; display:flex; flex-direction:column; gap:10px }
  .op-modal-box h3 { margin:0 }
  .op-log { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin:0; overflow:auto; flex:1; min-height:200px; font-family:ui-monospace,Consolas,monospace; font-size:0.75rem; line-height:1.5; white-space:pre-wrap; overflow-wrap:anywhere }
  .op-problem { font-size:0.78rem !important; padding:0 14px 10px 34px; margin-top:-6px }
  .op-covered { padding:0 14px 10px 34px; margin-top:-6px }
  .op-confirm { color:var(--text); font-size:0.85rem; line-height:1.4 }
  .op-pick { border:1px solid var(--line); border-radius:8px; background:var(--bg); padding:12px 14px; margin:10px 0 6px 17px }
  .op-lm { margin:6px 0 10px 17px }           /* the Local device tab's screens, inside a PC's card */
  .op-pick h4 { margin:0 0 2px; font-size:0.9rem }
  .op-fm { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:10px; align-items:center; padding:7px 0; border-bottom:1px solid color-mix(in srgb, var(--line) 60%, transparent); font-size:0.83rem }
  .op-fm:last-of-type { border-bottom:0 }
  .op-fm .lm-meta { white-space:normal; overflow-wrap:anywhere }
  .op-drive { display:grid; grid-template-columns:auto 1fr auto; gap:10px; align-items:center; padding:7px 10px; border:1px solid var(--line); border-radius:6px; margin:6px 0; font-size:0.83rem; cursor:pointer }
  .op-drive.sel { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 8%, var(--panel)) }
  .op-row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px }
  .op-row input[type=text] { flex:1; min-width:240px }
  @media (max-width:700px) { .op-found { grid-template-columns:1fr } .op-body { padding-left:14px } }
"""

OTHER_PCS_TAB_HTML = """<div class="tab" id="tabOther" onclick="showMainTab('other')">Other devices</div>"""

OTHER_PCS_PANE_HTML = """
  <div id="paneOther" style="display:none">
""" + tab_desc_ui.html("other") + """    """ + jump_ui.row("opJump") + """
    <div id="opLogModal" class="op-modal" style="display:none" onclick="if (event.target === this) opCloseLog()"></div>
    <div id="opFolderModal" class="op-modal" style="display:none" onclick="if (event.target === this) opFolderClose()"></div>
    <div id="opUnModal" class="op-modal" style="display:none" onclick="if (event.target === this) opUnClose()"></div>
    <div id="opBody"><div class="lm-muted">Looking at your network…</div></div>
  </div>
"""

OTHER_PCS_JS = r"""// ---------------- Other devices tab ----------------
if (typeof jumpRegister === 'function') jumpRegister('opJump', 'paneOther', () => jumpIds(['opDevicesCard', 'opBackupCard', 'opAddCard']), {});
// Built to design/dashboard-multi-pc-control-mockup.html (Revision 2) and design/multiproxy-courier-mockup.html. Re-fetches after
// every action, and every few seconds while the tab is open (fast while something is loading or downloading), so what you see is
// what the proxy really has: each Courier device pushes its report over a live connection and this page just reads the latest one.
let opState = null;
let opFound = null;           // null = not scanned yet; [] = scanned, nothing found
let opScanning = false;
let opTimer = null;
let opOpen = { address: false, own: false, code: false };
// Every word of the Add a PC block, in one place.
const OP_ADD_TEXT = {
  hint: 'Use another PC at home to run models, voice or Claude.',
  button: 'Add a PC',
  steps: ['On the other PC, open this address in a web browser:', 'Press Download and run the file.', 'It will appear here by itself.'],
  copy: 'Copy',
  waiting: 'Waiting for the other PC',
  seen: ' is paired',
  works: 'This address works for 15 minutes.',
  fresh: 'New address',
  done: 'Done',
  onList: 'It is now on your Other devices list.',
  notSeen: 'Not seen yet. The other PC must be on the same home network as this one.',
  other: 'Use a different address',
  otherWrong: 'Wrong network? Use a different address',
  expired: 'This address has expired. Make a new one.',
  joins: ' wants to join.',
  allow: 'Add',
  deny: 'No',
  codeLink: 'Have a code instead?',
  codeBack: 'Hide code option'
};

const opForm = { address: '', code: '', nick: '', ownAddress: '', ownNick: '', ownKey: '' };
const opCodes = {};           // pairing code typed for each found device
const opPcOpen = {};          // which devices are opened (kept while the page is open)
const opDlText = {};          // what was typed in each Ollama "get a model" box
const opDlOpen = {};          // devices whose Ollama "Add a model" box is shown (it stays away until the player asks)
let opDelAsk = null;          // { id, program, model, name }: the delete being confirmed
let opNote = '';              // a short confirmation shown at the top (a new pairing code was made...)
let opCodeAsk = null;         // the device whose "make a new pairing code?" question is open
const opFind = {};            // per device: the "Find models on <PC>" panel (null/absent = closed)
let opFolder = null;          // the "Where should models be saved?" chooser: {id, pending, drives, text, error, loading, saving}
let opBackupOptions = null;   // [{id, label}] every ready model on this device and in the cloud (fetched when the tab opens)

async function opGet(url) { return await (await fetch(url)).json(); }

async function opRefresh(force) {
  try { opState = await opGet('/config/other-pcs' + (force ? '?refresh=true' : '')); } catch (e) { return; }
  ucAdopt(opState);
  opRender();
}
// The red line at the top after a failed action stays until the player's next press (a refresh never wipes it: the reason has to be readable).
let opErrAuto = false;
function opClearError() {
  opErrAuto = false;
  const box = document.getElementById('opTopError');
  if (box) { box.textContent = ''; box.style.display = 'none'; }
}
// Programs the player hid on a device (kept in this browser), and programs that turned out not to be installed when Start failed.
const opNotInst = {};          // device|program -> true: Start failed, so the program is not there
let opShowHidden = {};         // device -> true: hidden programs are shown again for now
function opHiddenGet() { try { return JSON.parse(localStorage.getItem('opHiddenPrograms') || '{}') || {}; } catch (e) { return {}; } }
function opHiddenSet(h) { try { localStorage.setItem('opHiddenPrograms', JSON.stringify(h)); } catch (e) {} }
function opHideProgram(id, program) { const h = opHiddenGet(); h[id + '|' + program] = true; opHiddenSet(h); opRender(); }
function opShowHiddenToggle(id) { opShowHidden[id] = !opShowHidden[id]; opRender(); }
function opHiddenCount(r) {
  const h = opHiddenGet();
  return ((r.report && r.report.programs) || []).filter(p => p.id !== 'llamacpp' && h[r.id + '|' + p.id] && opIsNotInstalled(r.id, p)).length;
}
function opIsNotInstalled(id, p) { return p.id !== 'llamacpp' && !p.running && (p.installed === false || !!opNotInst[id + '|' + p.id]); }
function opBusyNow() {
  if (!opState) return false;
  if (opState.addAPc && opState.addAPc.active) return true;                 // an address is waiting for the other PC: look more often
  if (ucActive()) return true;                 // an update is on its way: look more often, so its bar moves
  return opState.servers.some(r => r.kind === 'courier' && opPcOpen[r.id] && r.report && r.report.programs.some(p =>
    p.state === 'loading' || p.state === 'busy' || (p.downloads || []).some(d => d.state === 'running')));
}
function opSchedule() {
  clearTimeout(opTimer);
  if (document.getElementById('paneOther').style.display === 'none') return;
  opTimer = setTimeout(async () => { if (!document.hidden) await opRefresh(false); opSchedule(); }, opBusyNow() ? 1500 : 5000);
}
async function opLoadBackupOptions() {
  try { opBackupOptions = (await opGet('/config/other-pcs/backup-options')).options; } catch (e) { opBackupOptions = opBackupOptions || []; }
  opRender();
}
function opSetBackup(enabled, model) {
  opAct(async () => { await lmPost('/config/other-pcs/backup', { enabled, model }); await opLoadBackupOptions(); });
}
function opToggleBackup() {
  const b = (opState && opState.backup) || { enabled: false, model: '' };
  const model = b.model || (opBackupOptions && opBackupOptions[0] ? opBackupOptions[0].id : '');
  opSetBackup(!b.enabled, model);
}
function opPickBackup(model) { const b = (opState && opState.backup) || { enabled: false }; opSetBackup(!!b.enabled, model); }
async function opOpened() {
  opLoadBackupOptions();
  await opRefresh(true);
  opSchedule();
  if (opFound === null) opScan();
}
async function opScan() {
  opScanning = true; opRender();
  try { opFound = (await lmPost('/config/other-pcs/scan')).found; } catch (e) { opFound = []; }
  opScanning = false; opRender();
}
// An action on a model row puts its error on THAT row (rowKey = device|program|model), not at the top of the tab, far from the button.
// Tab-wide things (pairing, adding a device, a program's Start / Stop, settings) still use the box at the top.
const opRowErr = {};
const opRowAct = {};          // the action each row error came from (only a failed Load gets "Try loading it again.")
function opRowKey(id, program, model) { return id + '|' + program + '|' + model; }
async function opAct(fn, rowKey) {
  let err = null;
  opClearError();
  try { await fn(); } catch (e) { err = e.message; }
  await opRefresh(false);
  if (err) { if (rowKey) { opRowErr[rowKey] = err; opRender(); } else opShowError(err); }
  opPillRefresh();
}
function opShowError(msg, plain) {
  const box = document.getElementById('opTopError');
  if (box) { box.textContent = (plain || msg.startsWith('Action failed: ')) ? msg : 'Action failed: ' + msg; box.style.display = 'block'; opErrAuto = !plain; }
}
// "Start here": a short numbered list, shown where it is seen first
function opSteps(steps) {
  return shBox('other-pcs', '<div class="op-start"><h4>Start here</h4><ol class="op-steps">' + steps.map(t => '<li>' + esc(t) + '</li>').join('') + '</ol></div>');
}
// a sentence from the device + what to try, without doubling the full stop
function opThen(msg, next) { return esc(String(msg || '').replace(/[.\s]+$/, '') + '. ' + next); }
async function opPillRefresh() {
  try {
    const fresh = await opGet('/config/other-pcs/provider');
    const i = PROVIDERS.findIndex(p => p.id === 'remote');
    if (i < 0) return;
    const changed = JSON.stringify(PROVIDERS[i]) !== JSON.stringify(fresh);
    PROVIDERS[i] = fresh;
    if (changed) renderChips();
  } catch (e) {}
}

function opPair(address, instance) {
  const code = (opCodes[instance || address] || '').trim();
  opAct(async () => { await lmPost('/config/other-pcs/servers/add-multiproxy', { address, code, nickname: '' }); delete opCodes[instance || address]; await opScan(); });
}
function opAddByAddress() {
  opForm.address = document.getElementById('opAddr').value; opForm.code = document.getElementById('opCode').value; opForm.nick = document.getElementById('opNick').value;
  opAct(async () => {
    await lmPost('/config/other-pcs/servers/add-multiproxy', { address: opForm.address, code: opForm.code, nickname: opForm.nick });
    opOpen.address = false; opForm.address = opForm.code = opForm.nick = '';
  });
}
function opAddOwn() {
  opForm.ownAddress = document.getElementById('opOwnAddr').value; opForm.ownNick = document.getElementById('opOwnNick').value; opForm.ownKey = document.getElementById('opOwnKey').value;
  opAct(async () => {
    await lmPost('/config/other-pcs/servers/add-own', { address: opForm.ownAddress, nickname: opForm.ownNick, key: opForm.ownKey });
    opOpen.own = false; opForm.ownAddress = opForm.ownNick = opForm.ownKey = '';
  });
}
// a device's name as the player typed it or the device calls itself (MSI); its id (msi) is only the key for lookups
function opName(r) { return (r && r.name) || (r && r.id) || ''; }
function opNameOf(id) { return opName(((opState && opState.servers) || []).find(x => x.id === id) || { id }); }
function opRemove(id, name) {
  ownAsk('Remove ' + name + '? Its models stop showing up here.', 'Remove').then(ok => {
    if (!ok) return;
    let note = '';
    opAct(async () => { const res = await lmPost('/config/other-pcs/servers/remove', { id }); note = (res && res.note) || ''; })
      .then(() => { if (note) opShowError(note, true); });
  });
}
function opToggleForm(which) { opOpen[which] = !opOpen[which]; opRender(); }
// ---- Add a PC: an address to open on the other PC (add_a_pc.py) ----
let opAddErr = '';
let opAddPick = false;        // the list of this PC's other addresses is open
let opAddPaired = '';         // the PC that paired while this card was open (kept until Done)
function opAddPc(a) {
  const T = OP_ADD_TEXT;
  if (a && a.pairedName) opAddPaired = a.pairedName;
  if (opAddPaired) {
    return '<div>' + opDot('on') + '<b>' + esc(opAddPaired) + '</b>' + esc(T.seen) + '</div><div class="op-row" style="justify-content:space-between"><span class="lm-muted">' + esc(T.onList) + '</span>' +
      '<button class="primary" onclick="opAddDone()">' + esc(T.done) + '</button></div>';
  }
  if (!a || !a.active) {
    return '<p class="lm-hint" style="margin:0 0 10px">' + esc(T.hint) + '</p>' + (a && a.expired ? '<div class="vc-warn">' + opDot('warn') + '<span>' + esc(T.expired) + '</span></div>' : '') +
      (opAddErr ? '<div class="vc-warn">' + opDot('warn') + '<span>' + esc(opAddErr) + '</span></div>' : '') +
      '<div class="op-row"><button class="primary" onclick="opAddNew()">' + esc(T.button) + '</button></div>';
  }
  const ask = a.joinName ? '<div>' + opDot('warn') + '<b>' + esc(a.joinName) + '</b>' + esc(T.joins) + '</div><div class="op-row"><button class="primary" onclick="opAddAnswer(true)">' + esc(T.allow) +
    '</button><button class="ghost" onclick="opAddAnswer(false)">' + esc(T.deny) + '</button></div>' : '';
  return ask + '<ol class="op-steps"><li>' + esc(T.steps[0]) + '<br><b style="word-break:break-all">' + esc(a.url) + '</b> &nbsp; <button class="ghost" onclick="opCopy(this, \'' + jsq(a.url) + '\')">' + esc(T.copy) + '</button></li>' +
    '<li>' + esc(T.steps[1]) + '</li><li>' + esc(T.steps[2]) + '</li></ol>' +
    '<div>' + opDot('none') + '<span class="lm-muted">' + esc(T.waiting) + '</span></div>' +
    (a.warnNetwork ? '<div class="vc-warn">' + opDot('warn') + '<span>' + esc(T.notSeen) + '</span></div>' : '') +
    opAddOther(a) +
    '<div class="op-row" style="justify-content:space-between"><span class="lm-muted">' + esc(T.works) + '</span><button class="ghost" onclick="opAddNew()">' + esc(T.fresh) + '</button></div>';
}
function opAddOther(a) {
  const T = OP_ADD_TEXT, list = (a && a.others) || [];
  if (!list.length) return '';
  const link = '<a href="#" onclick="opAddTogglePick();return false">' + esc(a.wrongNetwork ? T.otherWrong : T.other) + '</a>';
  if (!opAddPick) return '<div class="op-row">' + link + '</div>';
  return '<div class="op-row">' + link + '</div><div class="op-row">' + list.map(c => '<button class="ghost" onclick="opAddNew(\'' + jsq(c.ip) + '\')">' + esc(c.label) + '</button>').join('') + '</div>';
}
function opAddTogglePick() { opAddPick = !opAddPick; opRender(); }
async function opAddNew(ip) {
  opAddErr = ''; opAddPaired = ''; opAddPick = false;
  try { await lmPost('/config/other-pcs/add-a-pc/new', ip ? { ip: ip } : {}); } catch (e) { opAddErr = e.message; }
  await opRefresh(false);
}
async function opAddAnswer(allow) {
  try { await lmPost('/config/other-pcs/add-a-pc/answer', { allow: allow }); } catch (e) {}
  await opRefresh(false);
}
async function opAddDone() {
  opAddPaired = '';
  try { await lmPost('/config/other-pcs/add-a-pc/done', {}); } catch (e) {}
  await opRefresh(false);
}
function opCopy(btn, text) { copyModel(btn, text); }
function opCodeInput(key, v) { opCodes[key] = v; }
function opTogglePc(id) { opPcOpen[id] = !opPcOpen[id]; opDelAsk = null; opRender(); opSchedule(); }
function opNick(r) { const n = opName(r); return n.length > 18 ? n.slice(0, 17) + '…' : n; }          // a long nickname is cut with an ellipsis; the full text is in the hover
function opDlToggle(id) { if (opDlOpen[id]) opDlForget(id); opDlOpen[id] = !opDlOpen[id]; opRender(); }
function opGoAdd() { const el = document.getElementById('opAddCard'); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }

// ---- a Courier device's actions ----
function opCAct(id, program, action, model) {
  opNote = '';
  if (program === 'llamacpp' && action === 'load') {                         // the first Load on a device with no engine fetches it: ask where first
    const r = (opState && opState.servers || []).find(x => x.id === id);
    const lp = r && r.report && (r.report.programs || []).find(x => x.id === 'llamacpp');
    const st = (r && r.report && r.report.settings) || {};
    if (lp && !lp.installed && !st.models_folder_chosen) { opFolderOpen(id, model, 'load'); return; }
  }
  if (program === 'llamacpp' && action === 'download') {                       // the first download to a device asks where the models should go
    const r = (opState && opState.servers || []).find(x => x.id === id);
    const st = (r && r.report && r.report.settings) || {};
    if (r && r.report && !st.models_folder_chosen) { opFolderOpen(id, model); return; }
  }
  const rowKey = model && ['load', 'unload', 'delete', 'download', 'cancel'].includes(action) ? opRowKey(id, program, model) : null;
  if (rowKey) { delete opRowErr[rowKey]; opRowAct[rowKey] = action; }
  if (action === 'start' && (program === 'ollama' || program === 'lmstudio')) {          // a Start that fails means the program is not on that device: say so on its row, no red line
    opAct(async () => {
      try { await lmPost('/config/other-pcs/courier/action', { id, program, action, model: '' }); delete opNotInst[id + '|' + program]; }
      catch (e) { opNotInst[id + '|' + program] = true; }
    });
    return;
  }
  opAct(() => lmPost('/config/other-pcs/courier/action', { id, program, action, model: model || '' }), rowKey);
}
// Stop never asks (2026-10-23): Start is right there once it has stopped.
function opStop(id, program) { opCAct(id, program, 'stop'); }
// ---- "Download once": copy a model from this device instead of downloading it again on the other device ----
function opSend(id, model) {
  opNote = '';
  const r = (opState && opState.servers || []).find(x => x.id === id);
  const st = (r && r.report && r.report.settings) || {};
  if (r && r.report && !st.models_folder_chosen) { opFolderOpen(id, model, 'send'); return; }     // like a download: the first model asks where models should go
  opAct(() => lmPost('/config/other-pcs/courier/send', { id, model }));
}
function opSendCancel(id, model) { opAct(() => lmPost('/config/other-pcs/courier/send/cancel', { id, model })); }
function opGamingHas(modelId) { return ((opState && opState.gamingModels) || []).find(g => g.id === modelId) || null; }
function opSendJob(r, modelId) { return ((r && r.sends) || []).find(j => j.model === modelId && (j.state === 'sending' || j.state === 'paused')) || null; }
function opAskDelete(id, program, model, name, custom) { opDelAsk = { id, program, model, name, custom: !!custom }; opRender(); }
function opCancelDelete() { opDelAsk = null; opRender(); }
function opDoDelete() { const d = opDelAsk; opDelAsk = null; if (d) opCAct(d.id, d.program, 'delete', d.model); }
// ---- the Ollama box: suggestions from what that device has, and plain answers when a name is wrong ----
const opDlHide = {};          // "<device id>|<model>" -> true once the person typed again or pressed Close: that old failed-download line stays away
// MultiProxy's own "ollama/" routing prefix (and a pasted "ollama run ") is not part of an Ollama model name
function opOllamaClean(v) { return String(v || '').trim().replace(/^ollama\s+(run|pull)\s+/i, '').replace(/^ollama\//i, '').trim(); }
function opOllamaProg(id) {
  const r = (opState && opState.servers || []).find(x => x.id === id);
  return { r, p: r && r.report && (r.report.programs || []).find(x => x.id === 'ollama') };
}
function opOllamaHas(p, name) {
  const n = String(name || '').toLowerCase(); if (!n) return false;
  return ((p && p.models) || []).some(m => { const k = String(m.id || m.name || '').toLowerCase(); return k === n || k === n + ':latest'; });
}
function opOllamaList(id) {                     // <option>s: what is installed first, then the catalog's names that are not
  const p = opOllamaProg(id).p;
  const have = ((p && p.models) || []).map(m => m.id || m.name).filter(Boolean);
  let h = have.map(n => '<option value="' + esc(n) + '" label="installed"></option>').join('');
  ((opState && opState.ollamaCatalog) || []).filter(n => !opOllamaHas(p, n)).forEach(n => { h += '<option value="' + esc(n) + '" label="easy to get"></option>'; });
  return h;
}
function opDlSync(id) {                         // the Download button and the quiet "Using ..." line follow what is typed
  const { p } = opOllamaProg(id);
  const raw = (opDlText[id] || '').trim(), v = opOllamaClean(raw);
  const btn = document.getElementById('opDlGo_' + id), note = document.getElementById('opDlNote_' + id);
  if (btn) btn.textContent = opOllamaHas(p, v) ? 'Already installed' : 'Download';
  if (note) note.textContent = v && v !== raw ? 'Using ' + v : '';
}
function opDlForget(id) {                       // the old red lines from earlier tries go away when the person types again or closes the box
  const { p } = opOllamaProg(id);
  ((p && p.downloads) || []).forEach(d => { if (d.state === 'error') opDlHide[id + '|' + d.model] = true; });
  try { Array.from(document.querySelectorAll('.op-dl-err')).forEach(e => { e.style.display = 'none'; }); } catch (e) {}
}
function opDlInput(id, v) { opDlText[id] = v; opDlForget(id); opDlSync(id); }
// One short plain sentence for what Ollama (through the Courier) answered. Never its own words.
function opOllamaWords(raw, model, pc, version) {
  const t = String(raw || ''), m = String(model || '');
  const ver = version ? ' (' + version + ')' : '';
  if (/newer version|requires a newer|upgrade ollama/i.test(t)) return 'This model needs a newer Ollama than ' + pc + ' has' + ver + '.';
  if (/no space|not enough (disk )?space|disk full|out of space/i.test(t)) return pc + ' doesn\'t have room for this model. Free some space and try again.';
  if (/cannot connect|connection refused to .*11434|11434|server disconnected|did not answer|didn.t answer|not answering/i.test(t)) return 'Ollama on ' + pc + ' isn\'t answering. Start it and try again.';
  if (/dial tcp|no such host|i\/o timeout|timed? ?out|lookup |network|unreachable|tls|eof/i.test(t) && !/does not exist/i.test(t)) return pc + ' couldn\'t reach the internet to download it. Check its connection and try again.';
  if (/hugging ?face/i.test(t) || (m.indexOf('/') > 0 && !/^hf\.co\//i.test(m) && m.indexOf(':') < 0 && /file does not exist|manifest|not found|pull/i.test(t)))
    return 'That looks like a Hugging Face name, not an Ollama one.';
  if (/does not exist|manifest|not found|pull model|404|unknown model|invalid model/i.test(t))
    return "Ollama doesn't have a model with that name. Check the spelling, or pick one from the suggestions." + (version ? ' ' + pc + ' runs Ollama ' + version + '.' : '');
  return "Ollama couldn't download that model. Check the name and try again.";
}
function opOllamaGet(id) {
  const { p } = opOllamaProg(id);
  const v = opOllamaClean(opDlText[id]);
  if (!v) { opShowError('Type a model name to download it.', true); return; }
  if (opOllamaHas(p, v)) return;                                              // already there: nothing to pull again
  delete opDlHide[id + '|' + v];
  opDlText[id] = '';
  opDlOpen[id] = false;
  opCAct(id, 'ollama', 'download', v);
}
function opCSet(id, key, on) {
  opNote = '';
  opAct(() => lmPost('/config/other-pcs/courier/settings', { id, [key]: on }));
}
// ---- Uninstall a program that was installed on a device from here: one confirm for all four (same look as Higgs' own) ----
const opUnBusy = {};          // "<device id>:<program>" -> true while it is being removed: the row says Uninstalling
let opUnAsk = null;           // {id, program}: the "Uninstall ... from <PC>?" question is open
function opUnKey(id, program) { return id + ':' + program; }
function opUnWords(r, program) {
  const pc = opName(r), rep = r.report || {};
  const frees = (b) => b > 0 ? ' It frees about ' + lmFmt(b) + '.' : '';
  if (program === 'claude') return { title: 'Uninstall Claude from ' + pc + '?',
    body: 'This removes Claude and its sign-in from ' + pc + '. Your Claude account is not touched.' + frees((rep.claude || {}).diskBytes) + ' You can install it again any time.' };
  if (program === 'copilot') return { title: 'Uninstall Copilot from ' + pc + '?',
    body: 'This removes Copilot and its sign-in from ' + pc + '. Your GitHub account is not touched.' + frees((rep.copilot || {}).diskBytes) + ' You can install it again any time.' };
  if (program === 'llamacpp') {
    const p = (rep.programs || []).find(x => x.id === 'llamacpp') || {};
    return { title: 'Uninstall the model engine (llama.cpp) from ' + pc + '?',
      body: 'This removes the engine program from ' + pc + '. Your downloaded models stay.' + frees(((p.engine || {}).diskBytes) || 0) + ' You can install it again any time: loading a model gets it again.' };
  }
  if (program === 'voice') return { title: 'Uninstall Higgs voice from ' + pc + '?',
    body: 'This removes the voice engine and model from ' + pc + '. Your own voice clips stay. You can install it again any time.' };
  return { title: 'Uninstall the local decision model from ' + pc + '?',
    body: 'This removes the model from ' + pc + '. The copied files stay on that device, so setting it up again later is quick. Your other providers are not affected.' };
}
function opAskUninstall(id, program) { opUnAsk = { id, program }; opUnDraw(); }
function opUnClose() { opUnAsk = null; opUnDraw(); }
function opUnDraw() {
  const box = document.getElementById('opUnModal');
  if (!box) return;
  const r = opUnAsk && ((opState && opState.servers) || []).find(x => x.id === opUnAsk.id);
  if (!opUnAsk || !r) { box.style.display = 'none'; box.innerHTML = ''; return; }
  const w = opUnWords(r, opUnAsk.program);
  box.innerHTML = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="opUnDialog"><h3>' + esc(w.title) + '</h3><p>' + esc(w.body) + '</p>' +
    '<div class="vc-ft"><button class="ghost" onclick="opUnClose()">Cancel</button><button class="primary" onclick="opConfirmUninstall()">Uninstall</button></div></div>';
  box.style.display = 'flex';
  const d = document.getElementById('opUnDialog'); if (d && d.focus) d.focus();
}
async function opConfirmUninstall() {
  const a = opUnAsk; opUnClose(); if (!a) return;
  if (a.program === 'claude') { ccAct(a.id, 'uninstall'); return; }                    // Claude's own row shows Uninstalling and any trouble
  if (a.program === 'copilot') { cpAct(a.id, 'uninstall'); return; }                   // so does Copilot's
  const key = opUnKey(a.id, a.program), pc = opNameOf(a.id);
  opNote = ''; opUnBusy[key] = true; opRender();
  await opAct(async () => {
    try {
      if (a.program === 'voice') await lmPost('/config/voice/pc/uninstall', { id: a.id });
      else if (a.program === 'decisions') await lmPost('/config/decisions/uninstall', { pc: a.id });
      else await lmPost('/config/other-pcs/courier/action', { id: a.id, program: 'llamacpp', action: 'uninstall' });
    } catch (e) { throw new Error(String(e.message || '').split('<PC>').join(pc)); }
  });
  delete opUnBusy[key]; opRender();
}
if (document.addEventListener) document.addEventListener('keydown', e => { if (e.key === 'Escape' && opUnAsk) opUnClose(); });
// ---- a Courier device's log, in a panel (the last ~200 lines, newest at the bottom) ----
let opLog = null;
function opOpenLog(id) { opLog = { id, lines: null, error: '', loading: true }; opLogDraw(); opLogLoad(); }
function opCloseLog() { opLog = null; opLogDraw(); }
async function opLogLoad() {
  const id = opLog && opLog.id;
  if (!id) return;
  opLog.loading = true; opLogDraw();
  try {
    const r = await fetch('/config/other-pcs/courier/log?id=' + encodeURIComponent(id));
    const d = await r.json().catch(() => ({}));
    if (!opLog || opLog.id !== id) return;
    if (!r.ok) throw new Error(d.detail || "Couldn't read details.");
    opLog.lines = d.lines || []; opLog.error = '';
  } catch (e) { if (!opLog || opLog.id !== id) return; opLog.lines = null; opLog.error = e.message; }
  opLog.loading = false; opLogDraw();
  const pre = document.getElementById('opLogText'); if (pre) pre.scrollTop = pre.scrollHeight;
}
function opCopyLog(btn) {
  const text = opLog && opLog.lines ? opLog.lines.join('\n') : '';
  try { navigator.clipboard.writeText(text); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}
function opLogDraw() {
  const box = document.getElementById('opLogModal');
  if (!box) return;
  if (!opLog) { box.style.display = 'none'; box.innerHTML = ''; return; }
  let body;
  if (opLog.error) body = '<div class="lm-err">' + esc(opLog.error) + '</div>';
  else if (opLog.loading && !opLog.lines) body = '<div class="lm-muted">Reading details for ' + esc(opNameOf(opLog.id)) + '…</div>';
  else if (!opLog.lines || !opLog.lines.length) body = '<div class="lm-muted">Nothing to show yet.</div>';
  else body = '<pre class="op-log" id="opLogText">' + esc(opLog.lines.join('\n')) + '</pre>';
  box.innerHTML = '<div class="op-modal-box"><h3>Technical details for ' + esc(opNameOf(opLog.id)) + '</h3>' + body +
    '<div class="op-btns" style="justify-content:flex-end"><button class="ghost" onclick="opLogLoad()">Refresh</button>' +
    '<button class="ghost" onclick="opCopyLog(this)">Copy</button><button onclick="opCloseLog()">Close</button></div></div>';
  box.style.display = 'flex';
}
function opAskNewCode(id) { opCodeAsk = id; opRender(); }
function opCNewCode(id, name) {
  opCodeAsk = null;
  opAct(async () => { await lmPost('/config/other-pcs/courier/new-code', { id }); opNote = 'Made a new pairing code on ' + name + '. This device already has it.'; });
}

// ---- giving a saved device the new pairing code it now shows ----
const opNetOpen = {};         // device id -> the "VPN networks" input is open
const opNets = {};            // device id -> what is typed in it
function opToggleNets(id, current) { opNetOpen[id] = !opNetOpen[id]; if (opNetOpen[id]) opNets[id] = (current || []).join(', '); delete opRowErr['net:' + id]; opRender(); }
function opSaveNets(id) {
  opAct(async () => { await lmPost('/config/other-pcs/courier/settings', { id, private_networks: opNets[id] || '' }); opNetOpen[id] = false; }, 'net:' + id);
}
function opNetForm(r, st) {
  const k = 'net:' + r.id;
  return '<div class="op-row"><input type="text" placeholder="10.8.0.0/16" value="' + esc(opNets[r.id] || '') + '" oninput="opNets[\'' + jsq(r.id) + '\'] = this.value">' +
    '<button class="primary" onclick="opSaveNets(\'' + jsq(r.id) + '\')">Save</button><button class="ghost" onclick="opToggleNets(\'' + jsq(r.id) + '\')">Cancel</button></div>' +
    '<div class="lm-muted">Only needed if your VPN uses addresses outside normal home ranges. Separate them with commas.</div>' +
    (opRowErr[k] ? '<div class="lm-err">' + esc(opRowErr[k]) + '</div>' : '');
}
const opCodeOpen = {};        // device id -> the "Change pairing code" input is open
function opCodeKey(id) { return 'newcode|' + id; }
function opToggleSetCode(id) { opCodeOpen[id] = !opCodeOpen[id]; delete opRowErr[opCodeKey(id)]; opRender(); }
function opSetCode(id) {
  const k = opCodeKey(id);
  const code = (opCodes[k] || '').trim();
  delete opRowErr[k];
  opAct(async () => { await lmPost('/config/other-pcs/courier/set-code', { id, code }); delete opCodes[k]; opCodeOpen[id] = false; }, k);
}
function opCodeForm(r) {
  const k = opCodeKey(r.id);
  return '<div class="op-row"><input type="text" placeholder="Code, like KX7-42M" value="' + esc(opCodes[k] || '') + '" oninput="opCodeInput(\'' + jsq(k) + '\', this.value)">' +
    '<button class="primary" onclick="opSetCode(\'' + jsq(r.id) + '\')">Update code</button>' +
    (r.codeChanged ? '' : '<button class="ghost" onclick="opToggleSetCode(\'' + jsq(r.id) + '\')">Cancel</button>') + '</div>' +
    (opRowErr[k] ? '<div class="lm-err">' + esc(opRowErr[k]) + '</div>' : '');
}

// ---- The Model engine (llama.cpp) row opens into the Local device tab's own screens, scoped to one PC ----
const opLmOpen = {};          // PCs whose Model engine (llama.cpp) screens are open (closed at first)
const opEng = {};             // per PC: what its Courier says about the engine (kind, build, a newer build, faster versions), asked once when the screens open
function opLmToggle(id) { opLmOpen[id] = !opLmOpen[id]; if (!opLmOpen[id] && opFind[id]) opFindClose(id); opRender(); }
async function opEngLoad(id, installed, at) {
  at = at || '';        // the build that PC's report showed when this was asked: a different build later (an update finished) means ask again
  opEng[id] = { loading: true, installed, at };
  try { opEng[id] = Object.assign(await opGet('/config/other-pcs/courier/engine?id=' + encodeURIComponent(id)), { installed, at }); if (opEng[id].detail) opEng[id] = { installed, at, failed: true }; }
  catch (e) { opEng[id] = { installed, at, failed: true }; }
  opRender();
}
function opLmSummary(p) {
  const n = (p.models || []).length, m = (p.models || []).filter(x => x.loaded).length;
  return (p.version ? p.version + ' · ' : '') + n + (n === 1 ? ' model' : ' models') + ', ' + m + ' running';
}
// The state the Local device tab's own screens draw, made from that PC's report (same shape as /config/local-models). That PC's card decides the fit dots; no game runs on a model PC, so no Skyrim share.
function opLmState(r, p) {
  const id = r.id, caps = p.capabilities || {}, e = opEng[id] || {}, dls = p.downloads || [];
  const catalog = (p.available || []).map(a => {
    const job = dls.find(d => d.model === a.id && d.state === 'running'), send = opSendJob(r, a.id);
    const sendErr = (r.sends || []).find(j => j.model === a.id && j.state === 'error');
    const err = dls.find(d => d.model === a.id && d.state === 'error' && !opDlHide[id + '|' + a.id]);
    const rowErr = opRowErr[opRowKey(id, p.id, a.id)];
    let download = null;
    const note = send && send.state === 'paused' ? 'Waiting for ' + opName(r) + ' to answer. It will finish on its own.' : '';
    if (job) download = { state: 'running', percent: job.percent == null ? 0 : job.percent, copy: job.source === 'gaming-pc', note };
    else if (send) download = { state: 'running', percent: send.percent == null ? 0 : send.percent, copy: true, note };
    else if (err) download = { state: 'error', error: err.error };
    else if (sendErr) download = { state: 'error', error: 'Copy failed: ' + sendErr.message };
    else if (rowErr) download = { state: 'error', error: rowErr };
    return { id: a.id, name: a.name, blurb: a.blurb || '', vision: !!a.vision, sizeText: lmFmt(a.size_bytes), fit: a.fit, installed: !!a.installed, download,
             copyFrom: !a.installed && !!caps.receive && !!opGamingHas(a.id) };
  });
  const installed = (p.models || []).map(m => {
    const rowErr = opRowErr[opRowKey(id, p.id, m.id)];
    return { id: m.id, name: m.name, modelName: id + '/' + m.id, vision: !!m.vision, state: m.loaded ? 'running' : (m.state === 'loading' ? 'starting' : (m.state === 'error' ? 'failed' : 'stopped')),
             sizeText: m.size_bytes ? lmFmt(m.size_bytes) : '', custom: !!m.custom, message: rowErr || (m.state === 'error' ? m.error : '') || '',
             engineWait: !!m.engine_download, engineDl: m.engine_download ? { state: 'running', done: m.engine_download.done, total: m.engine_download.total } : null, engineNote: m.engine_note || '' };
  });
  return { scope: id, supported: true, modelServer: true, hasGame: false, folder: ((r.report || {}).settings || {}).models_folder || '', gpu: r.gpu, modelsOff: false,
           engine: { installed: !!p.installed, kind: e.kind || '', label: e.label || '', build: p.version || e.build || '', note: (p.notes || [])[0] || '', download: null,
                     options: (e.options || []).map(o => Object.assign({ advanced: true }, o)) },
           engineUpdate: e.update || null, catalog, installed, caps: { load: !!caps.load, unload: !!caps.unload, delete: !!caps.delete } };
}
function opLmBlock(r, p) {
  const id = r.id, f = opFind[id];
  if (!opEng[id] || opEng[id].installed !== !!p.installed || opEng[id].at !== (p.version || '')) opEngLoad(id, !!p.installed, p.version);       // asked when the screens open, when the engine arrives and whenever that PC's build changes (an update finished)
  return '<div class="op-lm">' + lmDeviceHtml(opLmState(r, p), { addOpen: !!f, addForm: f ? opFindPanel(r, p) : '' }) + '</div>';
}
// What the Local device tab's buttons do here: the same words, sent to that PC's Courier (never to this PC).
function opLmDo(id, verb, a, b, c, d) {
  const r = (opState && opState.servers || []).find(x => x.id === id);
  const p = r && r.report && (r.report.programs || []).find(x => x.id === 'llamacpp');
  if (!p) return;
  if (verb === 'download') opCAct(id, 'llamacpp', 'download', a);
  else if (verb === 'send') opSend(id, a);
  else if (verb === 'start') opCAct(id, 'llamacpp', 'load', a);
  else if (verb === 'stop') opCAct(id, 'llamacpp', ((p.models || []).find(m => m.id === a) || {}).engine_download ? 'cancel' : 'unload', a);
  else if (verb === 'cancel') { const m = String(a).replace(/^model:/, ''); if (opSendJob(r, m)) opSendCancel(id, m); else opCAct(id, 'llamacpp', 'cancel', m); }
  else if (verb === 'remove') {
    (async () => {                      // the Local device tab's own questions; that PC's Courier has no keep-the-files choice, so a downloaded model can only be deleted
      const ok = c ? await ownAskRemove(LM_REMOVE_WORDS.title(b), LM_REMOVE_WORDS.own, LM_REMOVE_WORDS.remove, null, [])
                   : await ownAsk(LM_REMOVE_WORDS.deleteAsk(b, d), LM_REMOVE_WORDS.deleteGo);
      if (ok) opCAct(id, 'llamacpp', 'delete', a);
    })();
  }
  else if (verb === 'updateEngine') opAct(async () => { await lmPost('/config/other-pcs/courier/engine', { id, action: 'update' }); await opEngLoad(id, !!p.installed, p.version); });
  else if (verb === 'useEngine') opAct(async () => { await lmPost('/config/other-pcs/courier/engine', { id, action: 'use', kind: a }); await opEngLoad(id, !!p.installed, p.version); });
  else if (verb === 'addToggle') { if (opFind[id]) opFindClose(id); else opFindOpen(id); }
}

// ---- "Find models on <PC>": model files already there are used where they sit, never copied ----
function opFindOpen(id) {
  opFind[id] = { loading: true, error: '', items: [], searched: [], selected: {}, scan: null, extraOpen: false, extraText: '', adding: false };
  opRender();
  opFindLoad(id, '');
}
function opFindClose(id) { opScanStop(id, true); delete opFind[id]; opRender(); }
function opFindMerge(f, items, preTick) {
  const have = new Set(f.items.map(i => i.path));
  items.forEach(i => {
    if (!have.has(i.path)) { f.items.push(i); if (preTick && !i.added) f.selected[i.path] = true; }
    else { const old = f.items.find(x => x.path === i.path); old.added = i.added; }
  });
}
async function opFindLoad(id, folder) {
  const f = opFind[id]; if (!f) return;
  f.loading = true; f.error = ''; opRender();
  try {
    const d = await lmPost('/config/other-pcs/courier/models/find', { id, folder });
    if (opFind[id] !== f) return;
    opFindMerge(f, d.found || [], false);
    f.searched = d.searched || [];
    if (d.scan && d.scan.state === 'running') { f.scan = d.scan; opScanPoll(id); }
    if (folder) f.extraOpen = false;
  } catch (e) { if (opFind[id] === f) f.error = e.message; }
  if (opFind[id] === f) { f.loading = false; opRender(); }
}
function opFindAll(id, on) {
  const f = opFind[id]; if (!f) return;
  f.selected = {};
  if (on) f.items.forEach(i => { if (!i.added) f.selected[i.path] = true; });
  opRender();
}
function opFindTickAt(id, n, on) { const f = opFind[id]; const i = f && f.items[n]; if (i) opFindTick(id, i.path, on); }
function opFindTick(id, path, on) { const f = opFind[id]; if (f) { if (on) f.selected[path] = true; else delete f.selected[path]; opRender(); } }
function opFindExtraToggle(id) { const f = opFind[id]; if (f) { f.extraOpen = !f.extraOpen; opRender(); } }
function opFindExtraInput(id, v) { const f = opFind[id]; if (f) f.extraText = v; }
function opFindExtraGo(id) {
  const f = opFind[id]; if (!f) return;
  const el = document.getElementById('opFindExtra_' + id);
  f.extraText = el ? el.value : f.extraText;
  if (!f.extraText.trim()) { f.error = 'Type the folder to look in, like D:\\AI\\gguf.'; opRender(); return; }
  opFindLoad(id, f.extraText.trim());
}
async function opScanStart(id) {
  const f = opFind[id]; if (!f) return;
  try { f.scan = await lmPost('/config/other-pcs/courier/models/scan', { id, action: 'start' }); } catch (e) { f.error = e.message; opRender(); return; }
  opFindMerge(f, f.scan.found || [], false); opRender(); opScanPoll(id);
}
function opScanPoll(id) {
  setTimeout(async () => {
    const f = opFind[id]; if (!f || !f.scan || f.scan.state !== 'running') return;
    try {
      f.scan = await lmPost('/config/other-pcs/courier/models/scan', { id, action: 'status' });
      opFindMerge(f, f.scan.found || [], false);
    } catch (e) { f.error = e.message; f.scan = null; opRender(); return; }
    opRender();
    opScanPoll(id);
  }, 1500);
}
async function opScanStop(id, quiet) {
  const f = opFind[id]; if (!f || !f.scan || f.scan.state !== 'running') return;
  try { f.scan = await lmPost('/config/other-pcs/courier/models/scan', { id, action: 'stop' }); } catch (e) { if (!quiet) f.error = e.message; }
  if (!quiet) { opFindMerge(f, (f.scan && f.scan.found) || [], false); opRender(); }
}
function opFindAdd(id) {
  const f = opFind[id]; if (!f) return;
  const paths = f.items.filter(i => f.selected[i.path] && !i.added).map(i => i.path);
  if (!paths.length) return;
  f.adding = true; opRender();
  opAct(async () => {
    try {
      const d = await lmPost('/config/other-pcs/courier/models/add', { id, paths });
      const n = (d.added || []).length;
      if (d.problems && d.problems.length) { f.adding = false; f.error = d.problems.map(p => p.message).join(' '); }
      if (n) { opScanStop(id, true); if (!(d.problems && d.problems.length)) delete opFind[id]; opNote = 'Added ' + n + (n === 1 ? ' model' : ' models') + ' on ' + id + '. They stay where they are.'; }
    } finally { if (opFind[id]) opFind[id].adding = false; }
  });
}
function opFindPanel(r, p) {
  const f = opFind[r.id];
  const name = esc(opName(r));
  let h = '<div class="op-pick"><div class="lm-muted">Model files already on ' + name + '. They run from where they are, so nothing is copied.</div>';
  if (f.error) h += '<div class="lm-err" style="margin:8px 0 0">' + esc(f.error) + '</div>';
  if (f.loading && !f.items.length) h += '<div class="lm-muted" style="margin-top:8px">Looking on ' + name + '…</div>';
  else if (!f.items.length) h += '<div class="lm-muted" style="margin-top:8px">' + (f.scan && f.scan.state === 'running' ? '' :
    'No models found in the usual places. Press Look in another folder or Scan all drives.') + '</div>';
  if (f.items.some(i => !i.added)) h += '<div class="lm-muted" style="margin:8px 0 0"><span class="lm-link" onclick="opFindAll(\'' + jsq(r.id) + '\', true)">Select all</span> · <span class="lm-link" onclick="opFindAll(\'' + jsq(r.id) + '\', false)">Select none</span></div>';
  f.items.forEach((i, ix) => {
    const box = i.added ? '<span></span>' :
      '<input type="checkbox"' + (f.selected[i.path] ? ' checked' : '') + ' onchange="opFindTickAt(\'' + jsq(r.id) + '\', ' + ix + ', this.checked)">';
    h += '<div class="op-fm">' + box + '<div style="min-width:0"><div class="lm-name">' + esc(i.name) +
      (i.vision ? '<span class="lm-ic" title="Has a vision file next to it, so it can see pictures">👁</span>' : '') + '</div>' +
      '<div class="lm-meta">' + lmFmt(i.size_bytes) + ' · <code>' + esc(i.folder) + '</code></div></div>' +
      '<div class="lm-muted">' + (i.added ? 'Already added' : '') + '</div></div>';
  });
  const n = f.items.filter(i => f.selected[i.path] && !i.added).length;
  const scanning = f.scan && f.scan.state === 'running';
  if (scanning) h += '<div class="op-row"><span class="lm-muted">Scanning ' + name + '\'s drives…</span><button class="ghost" onclick="opScanStop(\'' + jsq(r.id) + '\')">Stop</button></div>';
  else if (f.scan && f.scan.state === 'done') h += '<div class="lm-muted" style="margin-top:8px">Finished scanning ' + name + '\'s drives.</div>';
  else if (f.scan && f.scan.state === 'stopped') h += '<div class="lm-muted" style="margin-top:8px">Stopped scanning.</div>';
  if (f.extraOpen) h += '<div class="op-row"><input type="text" id="opFindExtra_' + esc(r.id) + '" placeholder="A folder on ' + name + ', like C:\\Models" value="' + esc(f.extraText) + '" ' +
    'oninput="opFindExtraInput(\'' + jsq(r.id) + '\', this.value)" onkeydown="if(event.key===\'Enter\')opFindExtraGo(\'' + jsq(r.id) + '\')">' +
    '<button onclick="opFindExtraGo(\'' + jsq(r.id) + '\')">Look here</button></div>';
  h += '<div class="op-row"><button class="primary"' + (n && !f.adding ? '' : ' disabled') + ' onclick="opFindAdd(\'' + jsq(r.id) + '\')">' +
    (n ? 'Add ' + n + (n === 1 ? ' model' : ' models') : 'Add selected models') + '</button>' +
    '<button class="ghost" onclick="opFindExtraToggle(\'' + jsq(r.id) + '\')">Look in another folder…</button>' +
    (scanning ? '' : '<button class="ghost" onclick="opScanStart(\'' + jsq(r.id) + '\')">Scan all drives</button>') +
    '<button class="ghost" onclick="opFindClose(\'' + jsq(r.id) + '\')">Cancel</button></div>';
  if ((r.report.programs || []).some(x => x.id === 'ollama' && !opIsNotInstalled(r.id, x)))
    h += '<div class="lm-muted" style="margin-top:4px">Ollama models can\'t be added here. Use the Ollama section above to download them.</div>';
  return h + '</div>';
}

// ---- "Where should new downloads be saved on <PC>?" (a drive list plus a typed folder: no folder window, it would open on that device's screen) ----
async function opFolderOpen(id, pending, kind) {
  opFolder = { id, pending: pending || '', kind: kind || 'download', drives: [], text: '', current: '', error: '', loading: true, saving: false };
  opFolderDraw();
  try {
    const d = await opGet('/config/other-pcs/courier/drives?id=' + encodeURIComponent(id));
    if (!opFolder || opFolder.id !== id) return;
    if (d.detail) throw new Error(d.detail);
    opFolder.drives = d.drives || []; opFolder.current = d.current || ''; opFolder.text = (d.chosen ? d.current : d.default) || d.current || '';
  } catch (e) { if (!opFolder || opFolder.id !== id) return; opFolder.error = e.message; }
  opFolder.loading = false; opFolderDraw();
}
function opFolderClose() { opFolder = null; opFolderDraw(); }
function opFolderInput(v) { if (opFolder) opFolder.text = v; }
function opFolderPick(i) { if (opFolder && opFolder.drives[i]) { opFolder.text = opFolder.drives[i].suggested; opFolder.error = ''; opFolderDraw(); } }
function opFolderUse() {
  const f = opFolder; if (!f || f.saving) return;
  const el = document.getElementById('opFolderText'); if (el) f.text = el.value;
  f.saving = true; f.error = ''; opFolderDraw();
  opAct(async () => {
    try {
      await lmPost('/config/other-pcs/courier/models-folder', { id: f.id, path: f.text });
    } catch (e) { f.saving = false; f.error = e.message; opFolderDraw(); return; }
    opFolder = null; opFolderDraw();
    await opRefresh(false);
    if (f.pending && f.kind === 'send') await lmPost('/config/other-pcs/courier/send', { id: f.id, model: f.pending });
    else if (f.pending && f.kind === 'load') await lmPost('/config/other-pcs/courier/action', { id: f.id, program: 'llamacpp', action: 'load', model: f.pending });
    else if (f.pending) await lmPost('/config/other-pcs/courier/action', { id: f.id, program: 'llamacpp', action: 'download', model: f.pending });
  });
}
function opFolderDraw() {
  const box = document.getElementById('opFolderModal');
  if (!box) return;
  const f = opFolder;
  if (!f) { box.style.display = 'none'; box.innerHTML = ''; return; }
  const low = (f.text || '').toLowerCase();
  let body = '';
  if (f.loading) body = '<div class="lm-muted">Looking at ' + esc(opNameOf(f.id)) + '\'s drives…</div>';
  else {
    body = '<div class="lm-muted">These files are big, up to about 20 GB each. Pick a drive with room.</div>';
    f.drives.forEach((d, i) => {
      const sel = low && d.path && low.startsWith(d.path.toLowerCase().replace(/[\\/]+$/, '')) && d.path.length > 1;
      body += '<div class="op-drive' + (sel ? ' sel' : '') + '" onclick="opFolderPick(' + i + ')"><b>' + esc(d.name) + '</b><span>' + esc(d.label || '') + '</span><span class="lm-muted">' + d.free_gb + ' GB free</span></div>';
    });
    body += '<div class="op-row"><input type="text" id="opFolderText" value="' + esc(f.text) + '" oninput="opFolderInput(this.value)" onkeydown="if(event.key===\'Enter\')opFolderUse()"></div>' +
      '<div class="lm-muted" style="margin-top:6px">Picking a drive fills in a folder for you. You can also type any folder on ' + esc(opNameOf(f.id)) + '.</div>';
  }
  if (f.error) body += '<div class="lm-err">' + esc(f.error) + '</div>';
  box.innerHTML = '<div class="op-modal-box"><h3>Where should new downloads be saved on ' + esc(opNameOf(f.id)) + '?</h3>' + body +
    '<div class="op-btns" style="justify-content:flex-end"><button class="ghost" onclick="opFolderClose()">Cancel</button>' +
    '<button class="primary"' + (f.loading || f.saving ? ' disabled' : '') + ' onclick="opFolderUse()">Use this folder</button></div></div>';
  box.style.display = 'flex';
}

// ---- small formatters ----
function opGb(mb) { const g = mb / 1024; return (g >= 10 ? Math.round(g) : Math.round(g * 10) / 10) + ''; }
function opAgo(sec) {
  if (sec == null) return '';
  if (sec < 90) return 'just now';
  if (sec < 5400) return Math.round(sec / 60) + ' minutes ago';
  return Math.round(sec / 3600) + ' hours ago';
}
function opGpuLine(gpu, models) {
  const parts = [];
  if (gpu && gpu.name) parts.push(gpu.name + (gpu.memoryGb ? ' · ' + gpu.memoryGb + ' GB' : ''));
  if (models && models.length) parts.push(models.join(', ') + ' ready');
  return parts.join(' · ') || 'No model ready yet';
}
function opShortCard(name) { return String(name || '').replace(/^(NVIDIA|AMD|Intel)\s+/i, '').replace(/^GeForce\s+/i, ''); }
function opCardText(rep) {
  return (rep.gpus || []).map(g => opShortCard(g.name) + (g.memory_total_mb ? ' · ' + opGb(g.memory_total_mb) + ' GB' : '')).join(' + ');
}
function opDot(cls, title) { return '<span class="op-dot ' + cls + '"' + (title ? ' title="' + esc(title) + '"' : '') + '></span>'; }
function opPower(rep) {
  const p = rep && rep.power;
  if (!p || !p.has_battery) return '';
  if (p.plugged_in === true) return '<span class="op-pw" title="Plugged in">🔌</span>';
  if (p.plugged_in === false) return '<span class="op-pw" title="On battery. Its graphics card slows way down unplugged.">🔋</span>';
  return '';
}
function opFoundLine(f) {
  const parts = [];
  if (f.gpu && f.gpu.name) parts.push(opShortCard(f.gpu.name) + (f.gpu.memoryGb ? ' · ' + Math.round(f.gpu.memoryGb) + ' GB' : ''));
  return parts.join(' · ');
}
function opProgramTip(r) {
  if (r.kind === 'multiproxy') return 'Needs MultiProxy Courier to manage from here';
  return r.program;
}

// ---- one opened Courier device ----
function opStats(rep) {
  const parts = [];
  const gpus = rep.gpus || [];
  gpus.forEach(g => {
    const pre = gpus.length > 1 ? esc(opShortCard(g.name)) + ' ' : '';
    if (g.memory_total_mb) parts.push('<span>' + pre + 'Graphics memory <b>' + (g.memory_used_mb != null ? opGb(g.memory_used_mb) + ' / ' : '') + opGb(g.memory_total_mb) + ' GB</b></span>');
  });
  if (rep.pc_memory) parts.push('<span>System memory <b>' + opGb(rep.pc_memory.used_mb) + ' / ' + opGb(rep.pc_memory.total_mb) + ' GB</b></span>');
  if (rep.disk) parts.push('<span>Disk <b>' + Math.round(rep.disk.free_gb) + ' GB</b> free</span>');
  return '<div class="op-stats">' + parts.join('') + '</div>';
}
function opModelRow(r, p, m) {
  const id = r.id;
  if (opDelAsk && opDelAsk.id === id && opDelAsk.program === p.id && opDelAsk.model === m.id && opDelAsk.custom) {
    return '<div class="op-m"><div class="op-confirm">Remove <b>' + esc(m.name) + '</b> from this list? The file stays where it is.</div>' +
      '<div class="op-btns"><button class="ghost" onclick="opCancelDelete()">Cancel</button><button onclick="opDoDelete()">Remove</button></div></div>';
  }
  if (opDelAsk && opDelAsk.id === id && opDelAsk.program === p.id && opDelAsk.model === m.id) {
    return '<div class="op-m"><div class="op-confirm">Delete <b>' + esc(m.name) + '</b> from <b>' + esc(opName(r)) + '</b>? This removes the file from that device and can\'t be undone.</div>' +
      '<div class="op-btns"><button class="ghost" onclick="opCancelDelete()">Cancel</button><button onclick="opDoDelete()">Delete</button></div></div>';
  }
  const loaded = !!m.loaded, loading = m.state === 'loading';
  const pct = m.on_card_percent;
  const spill = loaded && pct != null && pct < 100;
  const dot = loading ? 'none' : loaded ? (spill ? 'warn' : 'on') : (m.state === 'error' ? 'off' : 'idle');          // not loaded is nothing wrong: grey; red only for a real problem
  const tip = loading ? 'Loading' : loaded ? (spill ? 'Loaded, but ' + (100 - pct) + '% spilled into system memory: slower' :
    (pct === 100 ? 'Loaded, all on the graphics card' : 'Loaded')) : 'Downloaded, not loaded';
  let note = '';
  if (loaded && pct === 100) note = ' · all on the graphics card';
  else if (spill) note = ' · ' + (100 - pct) + '% in system memory (slower)';
  const ed = m.engine_download;                       // loading a model fetches the llama.cpp engine first when it isn't there yet
  let engineLine = '';
  if (loading && ed) {
    const pctE = ed.total ? Math.min(100, Math.round(ed.done * 100 / ed.total)) : 0;
    engineLine = '<div class="lm-muted" style="margin-top:4px">Downloading the engine first (one time)…' +
      (ed.total ? ' ' + (ed.done / 1e9).toFixed(1) + ' of ' + lmFmt(ed.total) : '') + '</div>' +
      '<div class="lm-bar"><i style="width:' + pctE + '%"></i></div>';
  }
  const rowErr = (!loaded && !loading) ? opRowErr[opRowKey(id, p.id, m.id)] : '';
  let right;
  if (loading && ed) right = '<button class="ghost" onclick="opCAct(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'cancel\', \'' + jsq(m.id) + '\')">Cancel</button>';
  else if (loading) right = '<span class="lm-muted">Loading…</span>';
  else if (loaded) right = '<button onclick="opCAct(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'unload\', \'' + jsq(m.id) + '\')">Unload</button>';
  else right = '<button class="primary" onclick="opCAct(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'load\', \'' + jsq(m.id) + '\')">' + (m.state === 'error' ? 'Try again' : 'Load') + '</button>' +
    (!(p.capabilities || {}).delete ? '' : m.custom ?
      '<button class="ghost" title="Take it off this list (the file stays where it is)" onclick="opAskDelete(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'' + jsq(m.id) + '\', \'' + jsq(m.name) + '\', true)">Remove</button>' :
      '<button class="ghost" title="Delete from ' + esc(opName(r)) + '" onclick="opAskDelete(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'' + jsq(m.id) + '\', \'' + jsq(m.name) + '\')">🗑</button>');
  return '<div class="op-m"><div style="min-width:0"><div class="lm-name">' + opDot(dot, tip) + esc(m.name) +
    (m.vision ? '<span class="lm-ic" title="Sees pictures">👁</span>' : '') + '</div>' +
    '<div class="lm-meta" style="margin-left:17px">Paste into SkyrimNet: <code>' + esc(id + '/' + m.id) + '</code>' + esc(note) +
    ' <button class="copy" onclick="opCopy(this, \'' + jsq(id + '/' + m.id) + '\')">Copy</button>' +
    engineLine +
    (m.state === 'error' && m.error ? '<div class="lm-err">' + opThen(m.error, 'Try loading it again.') + '</div>' : '') +
    (rowErr ? '<div class="lm-err">' + (opRowAct[opRowKey(id, p.id, m.id)] === 'load' ? opThen(rowErr, 'Try loading it again.') : esc(rowErr)) + '</div>' : '') + '</div></div>' +
    '<div class="op-btns">' + right + '</div></div>';
}
function opSpeed(bps) { return bps ? ' · ' + (bps >= 1e6 ? Math.round(bps / 1e6) + ' MB/s' : Math.round(bps / 1e3) + ' KB/s') : ''; }
function opCopyRow(r, p, d) {
  const job = opSendJob(r, d.model);
  const waiting = job && job.state === 'paused';
  const pct = d.percent == null ? 0 : d.percent;
  const line = waiting ? 'Waiting for ' + esc(opName(r)) + ' to answer. It will finish on its own.'
    : 'Copying from this device… ' + (d.done / 1e9).toFixed(1) + ' of ' + lmFmt(d.total) + opSpeed(d.speed_bps);
  return '<div class="op-m"><div style="min-width:0"><div class="lm-name">' + opDot('none') + esc(d.model) + '</div>' +
    '<div class="lm-meta" style="margin-left:17px">' + line + '</div><div class="lm-bar" style="margin-left:17px"><i style="width:' + pct + '%"></i></div></div>' +
    '<div class="op-btns"><button onclick="opSendCancel(\'' + jsq(r.id) + '\', \'' + jsq(d.model) + '\')">Cancel</button></div></div>';
}
function opDownloadRow(r, p, d) {
  if (d.source === 'gaming-pc') return opCopyRow(r, p, d);
  let line, bar = '';
  if (d.progress_known && d.total) {
    line = d.bytes ? 'Downloading to ' + esc(opName(r)) + '… ' + (d.done / 1e9).toFixed(1) + ' of ' + lmFmt(d.total)
                   : 'Downloading to ' + esc(opName(r)) + '… ' + (d.percent == null ? 0 : d.percent) + '%';
    bar = '<div class="lm-bar" style="margin-left:17px"><i style="width:' + (d.percent == null ? 0 : d.percent) + '%"></i></div>';
  } else line = 'Downloading to ' + esc(opName(r)) + '…';
  return '<div class="op-m"><div style="min-width:0"><div class="lm-name">' + opDot('none') + esc(d.model) + '</div>' +
    '<div class="lm-meta" style="margin-left:17px">' + line + '</div>' + bar + '</div>' +
    '<div class="op-btns"><button onclick="opCAct(\'' + jsq(r.id) + '\', \'' + jsq(p.id) + '\', \'cancel\', \'' + jsq(d.model) + '\')">Cancel</button></div></div>';
}
function opProgramBlock(r, p) {
  const id = r.id;
  const caps = p.capabilities || {};
  if (p.id !== 'llamacpp') {
    const hk = id + '|' + p.id;
    const hh = opHiddenGet();
    if (p.running) { delete opNotInst[hk]; if (hh[hk]) { delete hh[hk]; opHiddenSet(hh); } }       // it came back: the row returns by itself
    else if (p.installed === true && hh[hk] === 'gone') { delete opNotInst[hk]; delete hh[hk]; opHiddenSet(hh); }       // hidden while the device said it was gone, now reported installed again (stopped): back, and the Hide is forgotten
    else if (opIsNotInstalled(id, p)) {
      if (hh[hk] && p.installed === false && hh[hk] !== 'gone') { hh[hk] = 'gone'; opHiddenSet(hh); }       // remember the device itself said it was gone (a failed Start alone does not count)
      if (hh[hk] && !opShowHidden[id]) return '';
      const word = p.installed === false ? 'Not installed' : p.name + ' isn\'t installed on ' + opName(r) + '.';
      return '<div class="op-prog">' + opDot('idle', 'Not installed') + '<b>' + esc(p.name) + '</b> <span class="lm-muted">' + esc(word) + '</span><span class="op-grow"></span>' +
        '<button class="ghost" onclick="opHideProgram(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\')">Hide</button></div>';
    }
  }
  const ver = p.id === 'llamacpp' ? '' : (p.version ? ' ' + esc(p.version) : '');
  let dot, extra = '', btn = '';
  if (p.id === 'llamacpp') {
    const noEngine = !p.installed;                    // the built-in engine isn't on that device yet: no green dot, and say when it arrives
    dot = p.state === 'error' ? 'off' : (noEngine ? 'none' : 'on');
  } else {
    dot = p.state === 'error' ? 'off' : (p.state === 'stopped' ? 'idle' : 'on');
    if (p.state === 'stopped') extra = 'isn\'t serving models right now';
  }
  const blocked = (p.notes || []).length > 0 && p.id === 'lmstudio';           // its command-line tool wasn't found: buttons would only fail
  if (!blocked && caps.start && p.state === 'stopped') btn = '<button class="primary" onclick="opCAct(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'start\')">Start ' + esc(p.name) + '</button>';
  else if (!blocked && (caps.stop || caps.restart) && p.state !== 'stopped') {
    {
      btn = caps.stop ? '<button class="ghost" onclick="opStop(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\')">Stop ' + esc(p.name) + '</button>'
        : '<button class="ghost" onclick="opCAct(\'' + jsq(id) + '\', \'' + jsq(p.id) + '\', \'restart\')">Restart ' + esc(p.name) + '</button>';   // no Stop on that program: Restart is all there is
    }
  }
  const lmShow = p.id === 'llamacpp' && Array.isArray(p.available);        // a Courier that can't manage models yet keeps the row as it is
  const lmOpen = lmShow && !!opLmOpen[id];
  if (lmShow) {
    btn = '<button class="ghost" onclick="opLmToggle(\'' + jsq(id) + '\')">' + (lmOpen ? 'Hide' : 'Show') + '</button>';
    extra = opLmSummary(p);
  }
  if (p.id === 'ollama' && p.running && !opDlOpen[id]) btn += '<button class="ghost" onclick="opDlToggle(\'' + jsq(id) + '\')">Add a model</button>';
  if (p.id === 'llamacpp' && opUnBusy[opUnKey(id, 'llamacpp')]) { dot = 'warn'; btn = ''; extra = 'Uninstalling'; }
  else if (p.id === 'llamacpp' && (p.engine || {}).removable && !(p.downloads || []).some(d => d.state === 'running')) btn += '<button class="ghost" onclick="opAskUninstall(\'' + jsq(id) + '\', \'llamacpp\')">Uninstall</button>';
  const name = p.id === 'llamacpp' ? 'Model engine (llama.cpp)' : esc(p.name);
  let h = '<div class="op-prog">' + opDot(dot, p.state) + '<b>' + name + ver + '</b>' + (extra ? ' <span class="lm-muted">' + esc(extra) + '</span>' : '') +
    '<span class="op-grow"></span>' + btn;
  if (blocked) h += '<div class="lm-err op-note">' + opThen(p.notes[0], 'Check Help to fix this on that device.') + '</div>';
  if (p.stop_note) h += '<div class="lm-err op-note">Couldn\'t stop the program. Restart the other device.</div>';
  if (p.error && p.state === 'error') h += '<div class="lm-err op-note">' + esc(p.error) + '</div>';
  h += '</div>';
  if (lmOpen) h += opLmBlock(r, p);
  if (lmShow) return h;                                         // its models, downloads and copies are drawn inside the screens above
  (p.models || []).forEach(m => { h += opModelRow(r, p, m); });
  if (p.id === 'llamacpp') {
    ((r && r.sends) || []).forEach(j => {                       // a copy that has started but isn't in the report yet, or that hit a problem
      const inReport = (p.downloads || []).some(d => d.source === 'gaming-pc' && d.model === j.model);
      if (!inReport && (j.state === 'sending' || j.state === 'paused'))
        h += opCopyRow(r, p, { model: j.name || j.model, done: j.done, total: j.total, percent: j.percent, speed_bps: j.speed_bps });
      else if (j.state === 'error') h += '<div class="op-m"><div class="lm-err">Copying ' + esc(j.name || j.model) + ' didn\'t finish: ' + esc(j.message) + '</div></div>';
    });
  }
  (p.downloads || []).forEach(d => {
    if (d.state === 'running') h += opDownloadRow(r, p, d);
    else if (d.state === 'error' && !opDlHide[id + '|' + d.model]) {
      const why = p.id === 'ollama' ? opOllamaWords(d.error, d.model, opName(r), p.version) : d.error;       // Ollama's own words never reach the screen
      h += '<div class="op-m op-dl-err"><div class="lm-err">Download of ' + esc(d.model) + ' didn\'t finish: ' + esc(why) + '</div></div>';
    }
  });
  if (p.id === 'ollama' && p.running && opDlOpen[id]) {
    const typed = opOllamaClean(opDlText[id]), q = jsq(id);
    h += '<div class="op-dl"><input type="text" id="opDl_' + esc(id) + '" list="opDlList_' + esc(id) + '" autocomplete="off" placeholder="Type the Ollama model name, like gemma:2b" value="' + esc(opDlText[id] || '') + '" ' +
      'oninput="opDlInput(\'' + q + '\', this.value)" onkeydown="if(event.key===\'Enter\')opOllamaGet(\'' + q + '\')"><datalist id="opDlList_' + esc(id) + '">' + opOllamaList(id) + '</datalist>' +
      '<button class="primary" id="opDlGo_' + esc(id) + '" onclick="opOllamaGet(\'' + q + '\')">' + (opOllamaHas(p, typed) ? 'Already installed' : 'Download') + '</button><button class="ghost" onclick="opDlToggle(\'' + q + '\')">Close</button>' +
      '<span class="lm-muted" id="opDlNote_' + esc(id) + '" style="flex-basis:100%">' + (typed && typed !== (opDlText[id] || '').trim() ? 'Using ' + esc(typed) : '') + '</span></div>';
  }
  return h;
}
function opCourierBody(r) {
  const rep = r.report;
  if (!rep) return '<div class="lm-muted op-ro">Reading that device…</div>';
  let h = opSteps(['If the device has no models, press Show on Model engine (llama.cpp) to download one (or press Add it here… to use an existing file).',
    'Pick a model and press Download (or press Copy if it is faster from this device).', 'When it finishes, press Start.',
    'Click Copy to grab the model name, then paste it into SkyrimNet.', 'Press Stop to free up memory.']) + opStats(rep);
  rep.programs.forEach(p => { h += opProgramBlock(r, p); });
  if (typeof vcPcRow === 'function') h += vcPcRow(r);               // Higgs voice, one more program on the card (voice_ui.py)
  if (typeof dcPcRow === 'function') h += dcPcRow(r);               // the local decision model, another one (decision_ui.py)
  if (typeof ccPcRow === 'function') h += ccPcRow(r);               // Claude with the player's own subscription, one more (claude_ui.py)
  if (typeof cpPcRow === 'function') h += cpPcRow(r);               // and Copilot, the same kind of row (copilot_ui.py)
  const st = rep.settings || {};
  h += '<div class="op-set">' + (st.models_folder ? '<span class="op-models">Models are saved in <code>' + esc(st.models_folder) + '</code> <button class="ghost" onclick="opFolderOpen(\'' + jsq(r.id) + '\', \'\')">Change…</button></span>' : '') + '<label><input type="checkbox"' + (st.keep_awake ? ' checked' : '') + ' onchange="opCSet(\'' + jsq(r.id) + '\', \'keep_awake\', this.checked)">Keep this device awake</label>';
  if (st.start_with_windows_supported) h += '<label><input type="checkbox"' + (st.start_with_windows ? ' checked' : '') + ' onchange="opCSet(\'' + jsq(r.id) + '\', \'start_with_windows\', this.checked)">Start with Windows</label>';
  if (opCodeAsk === r.id) {
    const q = (opState && opState.newCodeConfirm) || { question: '', cancel: 'Cancel', confirm: 'Make new code' };
    h += '<span class="op-confirm">' + esc(q.question) + '</span><span class="op-btns"><button class="ghost" onclick="opAskNewCode(null)">' + esc(q.cancel) + '</button>' +
      '<button onclick="opCNewCode(\'' + jsq(r.id) + '\', \'' + jsq(opName(r)) + '\')">' + esc(q.confirm) + '</button></span>';
  } else h += '<button class="ghost" onclick="opAskNewCode(\'' + jsq(r.id) + '\')">Make a new pairing code…</button>' +
    '<button class="ghost" onclick="opToggleSetCode(\'' + jsq(r.id) + '\')">Change pairing code…</button>' +
    '<button class="ghost" onclick="opToggleNets(\'' + jsq(r.id) + '\', ' + esc(JSON.stringify(st.private_networks || [])) + ')">Other VPN or Tailscale networks…</button>' +
    (opHiddenCount(r) ? '<button class="ghost" onclick="opShowHiddenToggle(\'' + jsq(r.id) + '\')">' + (opShowHidden[r.id] ? 'Hide them again' : 'Show hidden programs') + '</button>' : '') +
    '<button class="ghost" onclick="opOpenLog(\'' + jsq(r.id) + '\')">Log</button>';
  if (st.keep_awake && st.keep_awake_active === false) h += '<span class="lm-err" style="margin:0">Couldn\'t keep this device awake. Check its Windows power settings.</span>';
  h += '</div>';
  if (opCodeOpen[r.id]) h += opCodeForm(r);
  if (opNetOpen[r.id]) h += opNetForm(r, st);
  return h;
}
function opReadOnlyBody(r) {
  let h = opSteps(['Press the link to jump to Add a device.', 'Follow the Add a device steps on that device.', 'Remove this old entry after the new one is paired.']);
  if (r.kind === 'multiproxy') h += '<div class="lm-muted op-ro">' + esc(opName(r)) + ' is running an old version. Install MultiProxy Courier there to manage it from here. <span class="lm-link" onclick="opGoAdd()">Jump to Add a device</span></div>';
  else h += '<div class="lm-muted op-ro">' + esc(opName(r)) + ' was added by network address. Install MultiProxy Courier there to manage it from here. <span class="lm-link" onclick="opGoAdd()">Jump to Add a device</span></div>';
  if (!r.models.length) return h + '<div class="lm-muted op-ro">No models listed.</div>';
  r.models.forEach(m => {
    const dot = (m.state === 'running' || m.state === 'starting') ? 'on' : 'idle';
    h += '<div class="op-m"><div style="min-width:0"><div class="lm-name">' + opDot(dot, m.state === 'stopped' ? 'Stopped' : 'Ready') + '<code>' + esc(m.modelName) + '</code>' +
      (m.vision ? '<span class="lm-ic" title="Sees pictures">👁</span>' : '') + '</div></div>' +
      '<div class="op-btns"><button class="copy" onclick="opCopy(this, \'' + jsq(m.modelName) + '\')">Copy</button></div></div>';
  });
  return h;
}
// ---- Update Courier (the signed Courier update the main device carries; courier_update_sender.py) ------------------------------------------------
// One shared picture of "which device is behind and how its update is going" for the Other devices tab and the places that say a Courier is too old (the Decisions and
// Voice rows). It comes with the Other devices state, or from /config/other-pcs/courier/update when another tab needs it; a timer runs only while an update is under way.
let ucInfo = null, ucTimer = null, ucLoading = false;
const ucErr = {};             // per device: the sentence the server gave when a start was refused
function ucAdopt(s) {
  const devs = {};
  (s.servers || []).forEach(r => { if (r.kind === 'courier') devs[r.id] = { build: r.courierBuild == null ? null : r.courierBuild, canUpdate: !!r.canUpdate, online: r.online, job: r.update || null }; });
  const cu = s.courierUpdate || {};
  ucInfo = { package: cu.package || { ok: false, build: null }, devices: devs, eligible: cu.eligible || [], allRunning: !!cu.allRunning };
}
function ucActive() {
  return !!ucInfo && (ucInfo.allRunning || Object.values(ucInfo.devices).some(d => d.job && (d.job.state === 'sending' || d.job.state === 'installing')));
}
function ucPkgBuild() { return ucInfo && ucInfo.package && ucInfo.package.ok ? ucInfo.package.build : null; }
function ucDev(id) { return (ucInfo && ucInfo.devices && ucInfo.devices[id]) || null; }
function ucBehind(d) { const b = ucPkgBuild(); return b !== null && !!d && (d.build == null || d.build < b); }
function ucPaneShown(id) { const el = document.getElementById(id); return !!el && el.style.display !== 'none'; }
async function ucLoad() {
  if (ucLoading) return;
  ucLoading = true;
  try { ucInfo = await (await fetch('/config/other-pcs/courier/update')).json(); } catch (e) { /* the page keeps what it had */ }
  ucLoading = false;
  ucRedraw();
  ucSchedule();
}
function ucRedraw() {
  if (ucPaneShown('paneOther') && typeof opRender === 'function') opRender();
  if (ucPaneShown('paneDecisions') && typeof dcRender === 'function') dcRender();
  if (ucPaneShown('paneVoice') && typeof vcRender === 'function') vcRender();
  if (ucPaneShown('paneOverview') && typeof ovRepaintSoft === 'function') ovRepaintSoft();
}
function ucSchedule() {
  clearTimeout(ucTimer);
  if (!ucActive() || ucPaneShown('paneOther')) return;           // (the Other devices tab already refreshes itself, faster while an update runs)
  ucTimer = setTimeout(() => { if (!document.hidden) ucLoad(); else ucSchedule(); }, 1500);
}
function ucEnsure() { if (!ucInfo && !ucLoading) ucLoad(); }
function ucWhy(id, code, name) {          // the short reason after "Update failed: "
  const b = (ucDev(id) || {}).build;
  const m = {
    bad_signature: "The update file isn't trusted.",
    bad_package: 'the update file is damaged.',
    not_newer: name + ' is already up to date.',
    no_space: 'not enough disk space on ' + name + '.',
    runtime_changed: name + ' needs a manual update this time.',
    rolled_back: name + " didn't answer and was put back to " + (b == null ? 'the old version' : '1.' + b) + '.',
    unreachable: name + ' stopped answering.',
    no_package: 'No update file here.',
    update_failed: "The update didn't go through."
  };
  return m[code] || m.update_failed;
}
function ucOldText(d) {
  return (d.build == null ? 'This Courier' : '1.' + d.build) + ' is too old to update from here. Update it on that device once.';
}
async function ucStart(id, anyway) {
  delete ucErr[id];
  try { await lmPost('/config/other-pcs/courier/update', { id, anyway: !!anyway }); } catch (e) { ucErr[id] = e.message; }
  if (ucPaneShown('paneOther') && typeof opRefresh === 'function') { await opRefresh(false); opSchedule(); } else await ucLoad();     // (the tab's own timer, now faster)
  ucSchedule();
}
async function ucAll() {
  try { await lmPost('/config/other-pcs/courier/update/all', {}); } catch (e) { if (typeof opShowError === 'function') opShowError(e.message); }
  if (ucPaneShown('paneOther') && typeof opRefresh === 'function') { await opRefresh(false); opSchedule(); } else await ucLoad();
  ucSchedule();
}
async function ucCancel(id) {
  try { await lmPost('/config/other-pcs/courier/update/cancel', { id }); } catch (e) { /* it finished first */ }
  if (typeof opRefresh === 'function') await opRefresh(false);
}
const UC_ANYWAY_HELP = 'Update anyway stops what the device is doing. A model download can be started again later, and a voice line is made again.';
function ucBtn(id, label, anyway, cls) {
  return '<button' + (cls ? ' class="' + cls + '"' : '') + (anyway ? ' title="' + esc(UC_ANYWAY_HELP) + '"' : '') + ' onclick="event.stopPropagation(); ucStart(\'' + jsq(id) + '\'' + (anyway ? ', true' : '') + ')">' + esc(label) + '</button>';
}
// The whole line under a device's name on the Other devices tab (empty when there is nothing to say: no "up to date" line).
function ucRow(r) {
  if (r.kind !== 'courier') return '';
  const d = ucDev(r.id), pkg = ucPkgBuild();
  if (!d) return '';
  const name = opName(r), job = d.job, id = r.id;
  if (job && (job.state === 'sending' || job.state === 'installing')) {
    const sending = job.state === 'sending';
    return '<div class="uc-line"><span class="uc-note">Updating to 1.' + job.to + '</span><div class="lm-bar' + (sending ? '' : ' uc-indet') + '"><i' + (sending ? ' style="width:' + job.percent + '%"' : '') + '></i></div>' +
      (sending ? '<button onclick="event.stopPropagation(); ucCancel(\'' + jsq(id) + '\')">Cancel</button>' : '') + '</div>';
  }
  if (job && job.state === 'busy') {
    return '<div class="uc-line"><span class="uc-note uc-r">' + esc(name) + ' is busy. Update when it finishes.</span>' + ucBtn(id, 'Update anyway', true) + '</div>';
  }
  const failed = job && job.state === 'failed';
  const failLine = failed ? '<div class="vc-warn uc-fail">' + opDot('warn') + '<span>Update failed: ' + esc(ucWhy(id, job.code, name)) + '</span></div>'
    : (ucErr[id] ? '<div class="vc-warn uc-fail">' + opDot('warn') + '<span>' + esc(ucErr[id]) + '</span></div>' : '');
  if (!ucBehind(d)) return failLine ? '<div class="uc-line">' + failLine + '</div>' : '';
  if (r.online === false) return '<div class="uc-line"><span class="uc-note">1.' + pkg + ' available</span>' + failLine + '</div>';
  if (r.online !== true) return '';
  if (!d.canUpdate) return '<div class="uc-line"><span class="uc-note">' + esc(ucOldText(d)) + '</span></div>';
  return '<div class="uc-line"><span class="uc-note uc-r">1.' + pkg + ' available</span>' + failLine + ucBtn(id, failed ? 'Try again' : 'Update Courier', false, 'primary') + '</div>';
}
// For the Decisions and Voice rows: the same help in a few words (text) and the button, so a row that says a Courier is too old can fix it.
function ucInline(id, name) {
  ucEnsure();
  const d = ucDev(id), pkg = ucPkgBuild();
  if (!d || pkg === null || !ucBehind(d) || d.online !== true) return { text: '', btn: '' };
  const job = d.job;
  if (job && (job.state === 'sending' || job.state === 'installing')) return { text: 'Updating to 1.' + job.to, btn: '' };
  if (job && job.state === 'busy') return { text: esc(name) + ' is busy. Update when it finishes.', btn: ucBtn(id, 'Update anyway', true) };
  const failed = job && job.state === 'failed';
  const text = failed ? 'Update failed: ' + esc(ucWhy(id, job.code, name)) : (ucErr[id] ? esc(ucErr[id]) : '');
  if (!d.canUpdate) return { text: esc(ucOldText(d)), btn: '' };
  return { text, btn: ucBtn(id, failed ? 'Try again' : 'Update Courier', false, 'primary') };
}
// The Overview's line under a device (the same words and the same button as above; empty when the device is up to date): "1.8 available" right beside Update Courier.
function ucOverviewLine(id, name) {
  const u = ucInline(id, name);
  if (!u.text && !u.btn) return '';
  const d = ucDev(id), job = d && d.job, bad = !!(job && job.state === 'failed') || !!ucErr[id];
  const text = u.text || '1.' + ucPkgBuild() + ' available';
  return '<div class="uc-line">' + (bad ? '<div class="vc-warn uc-fail' + (u.btn ? ' uc-r' : '') + '">' + opDot('warn') + '<span>' + text + '</span></div>' : '<span class="uc-note' + (u.btn ? ' uc-r' : '') + '">' + text + '</span>') + u.btn + '</div>';
}
// How many devices are behind and can take the update right now (Update all shows from two).
function ucBehindCount() { return ucInfo && !ucInfo.allRunning ? (ucInfo.eligible || []).length : 0; }

function opServerRow(r) {
  const open = !!opPcOpen[r.id];
  const online = r.online !== false;
  const needsCode = !online && !!r.codeChanged;       // it answers, only its code changed: amber, not offline
  const rep = r.report;
  let right;
  if (r.kind === 'courier' && rep && (rep.gpus || []).length) right = opCardText(rep);
  else right = r.models.length + (r.models.length === 1 ? ' model' : ' models');
  if (r.kind === 'courier' && r.courierBuild != null) right += ' · Courier 1.' + r.courierBuild;
  let h = '<div class="op-server"><div class="op-head' + (online || needsCode ? '' : ' op-off') + '" onclick="opTogglePc(\'' + jsq(r.id) + '\')">' +
    '<span class="op-arrow">' + (open ? '▼' : '▶') + '</span><span class="lm-name">' + (needsCode ? opDot('warn', 'Needs the new pairing code') : opDot(online ? 'on' : 'off', online ? 'Online' : 'Offline')) + esc(opName(r)) + '</span>' +
    (r.program ? '<span class="op-app" title="' + esc(opProgramTip(r)) + '">' + esc(r.program) + '</span>' : '') +
    (r.kind === 'courier' ? opPower(rep) : '') +
    '<span class="op-grow"></span><span class="lm-muted">' + esc(right) + '</span>' +
    '<button class="ghost" onclick="event.stopPropagation(); opRemove(\'' + jsq(r.id) + '\', \'' + jsq(opName(r)) + '\')">Remove</button></div>';
  if (r.problem) h += '<div class="lm-muted op-problem"' + (r.problem.agoSeconds == null ? '' : ' title="' + esc(opAgo(r.problem.agoSeconds)) + '"') + '>' + opThen(r.problem.message, 'Try restarting it on that device.') + '</div>';
  h += ucRow(r);
  if (r.courierOlder && online && ucPkgBuild() === null) h += '<div class="vc-warn">' + opDot('warn') + '<span>' + 'Courier on ' + esc(opName(r)) + ' needs an update. <span class="lm-link" onclick="opGoAdd()">Jump to Add a device</span></span></div>';
  if (r.coveredBy) h += '<div class="lm-muted op-covered">Covered by ' + esc(r.coveredBy) + ' until it\'s back</div>';
  if (open) {
    h += '<div class="op-body">';
    if (needsCode) h += '<div class="vc-warn">' + opDot('warn') + '<span>This device\'s pairing code has changed. Type the new code shown in its Courier window.</span></div>' + opCodeForm(r);
    else if (r.online === false) h += '<div class="lm-err">That device isn\'t answering. Make sure it\'s turned on, on your home network or VPN, and running MultiProxy Courier.</div>';
    else h += r.kind === 'courier' ? opCourierBody(r) : opReadOnlyBody(r);
    h += '</div>';
  }
  return h + '</div>';
}

function opRender() {
  const s = opState;
  if (!s) return;
  const ae = document.activeElement;
  if (ae && ae.closest && ae.closest('#opBody') && (ae.tagName === 'INPUT') && ae.type !== 'checkbox') return;   // don't rebuild under someone typing
  const keep = document.getElementById('opTopError');
  const errText = keep && keep.style.display !== 'none' ? keep.textContent : '';
  let h = '<div id="opTopError" class="lm-err" style="display:none;margin:0 0 10px"></div>';
  if (opNote) h += '<div class="lm-muted" style="margin:0 0 10px">' + esc(opNote) + '</div>';

  // ---- Other devices (Main device): every other device, each run through its Courier ----
  {
    h += '<div class="card">' + opSteps(['Run models on a second device to save your main computer\'s memory.',
      'If the list says "None yet", scroll down to Add a device.', 'Click a device\'s name to see its models.',
      'Press Load next to a model to use it, or Remove to delete the device from the list.',
      'If a device has a red dot, make sure it is turned on and MultiProxy Courier is running on it.']) + '</div>';
    const upN = ucInfo && !ucInfo.allRunning ? (ucInfo.eligible || []).length : 0;
    h += '<div class="card" id="opDevicesCard"><div class="uc-head"><h3>Other devices</h3>' + (upN >= 2 ? '<button class="primary" onclick="ucAll()">Update all (' + upN + ')</button>' : '') + '</div>';
    if (ucInfo && ucPkgBuild() === null && s.servers.some(x => x.kind === 'courier' && x.courierOlder && x.online === true)) h += '<div class="vc-warn">' + opDot('warn') + '<span>No update file here.</span></div>';
    if (!s.servers.length) h += '<p class="lm-muted" style="margin-top:8px">None yet. Scroll down to Add a device.</p>';
    s.servers.forEach(r => { h += opServerRow(r); });
    h += '</div>';

    // ---- When a device doesn't answer ----
    const bk = s.backup || { enabled: false, model: '' };
    const opts = opBackupOptions || [];
    const known = opts.some(o => o.id === bk.model);
    const optHtml = (bk.model && !known ? '<option value="' + esc(bk.model) + '" selected>' + esc(bk.model) + ' (not ready right now)</option>' : '') +
      (opts.length ? '' : (bk.model ? '' : '<option value="">Nothing ready to pick yet</option>')) +
      opts.map(o => '<option value="' + esc(o.id) + '"' + (o.id === bk.model ? ' selected' : '') + '>' + esc(o.label) + '</option>').join('');
    h += '<div class="card" id="opBackupCard"><h3>When a device doesn\'t answer</h3>' +
      '<p class="lm-hint" style="margin-top:8px">If one of your other devices goes quiet mid-game, MultiProxy answers with your backup instead, so NPCs keep talking.</p>' +
      '<div class="op-toggle"><button class="op-sw' + (bk.enabled ? ' on' : '') + '" onclick="opToggleBackup()" aria-label="Use a backup"></button><span><b>Use a backup</b></span></div>' +
      '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="lm-muted">Backup:</span>' +
      '<select onchange="opPickBackup(this.value)">' + optHtml + '</select></div></div>';

    // ---- Add a device ----
    h += '<div class="card" id="opAddCard"><h3 style="margin:0 0 6px">Add a device</h3>' + opAddPc(s.addAPc) +
      '<p class="lm-muted" style="margin:14px 0 6px"><span class="lm-link" onclick="opToggleForm(\'code\')">' + esc(opOpen.code ? OP_ADD_TEXT.codeBack : OP_ADD_TEXT.codeLink) + '</span></p>';
    if (opOpen.code) {
    h += '<p class="lm-hint" style="margin:0 0 10px">Download Courier for the other device.</p>' +
      '<div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:6px">' +
      '<div><button class="ghost" onclick="opCopy(this, \'' + jsq(s.downloadUrl) + '\')">Windows</button><div class="lm-muted" style="font-size:0.78rem;margin-top:4px">Copies the download link</div></div>' +
      '<div><button class="ghost" onclick="opCopy(this, \'' + jsq(s.linuxCommand) + '\')">Linux</button><div class="lm-muted" style="font-size:0.78rem;margin-top:4px">Copies the install command</div></div></div>' +
      opSteps(['On the other device, download MultiProxy Courier and double-click Set up this PC.', 'Back here, wait for the device to appear under "Found on your network".',
        'Type the pairing code shown on the other device\'s window and press Pair.',
        'If it doesn\'t appear, check that both devices are on the same network and press scan again, or add it manually below.',
        '(Optional) Turn on "Use a backup" so NPCs keep talking if a device goes offline mid-game.']);
    h += '<div class="lm-muted" style="margin:14px 0 6px">Found on your network</div>';
    if (opScanning) h += '<div class="lm-muted">Looking…</div>';
    else if (opFound && opFound.length) {
      for (const f of opFound) {
        const key = f.instance || f.address;
        h += '<div class="op-found"><div><div class="lm-name">' + esc(f.name) + '</div><div class="lm-meta">' + esc(opFoundLine(f)) + '</div></div>' +
          (f.paired ? '<div class="lm-muted">Already added</div><div></div>' :
           '<div><input type="text" style="width:100%" placeholder="Code" value="' + esc(opCodes[key] || '') + '" oninput="opCodeInput(\'' + jsq(key) + '\', this.value)"></div>' +
           '<div><button class="primary" onclick="opPair(\'' + jsq(f.address) + '\', \'' + jsq(f.instance || '') + '\')">Pair</button></div>') + '</div>';
      }
    } else h += '<div class="lm-muted">Nothing found yet. Make sure the other device is running MultiProxy Courier on the same network.</div>';
    h += '<div class="op-row"><button class="ghost" onclick="opScan()">Scan again</button>' +
      '<button class="ghost" onclick="opToggleForm(\'address\')">' + (opOpen.address ? 'Cancel' : 'Add manually') + '</button></div>';
    if (opOpen.address) {
      h += '<div class="lm-form"><label>MultiProxy\'s address on the other device (like 192.168.1.50)</label><input type="text" id="opAddr" placeholder="192.168.1.50" value="' + esc(opForm.address) + '">' +
        '<label>Pairing code</label><input type="text" id="opCode" placeholder="Code, like KX7-42M" value="' + esc(opForm.code) + '">' +
        '<label>Nickname (optional)</label><input type="text" id="opNick" placeholder="Defaults to the other device\'s name" value="' + esc(opForm.nick) + '">' +
        '<div class="lm-actions"><button onclick="opAddByAddress()">Add</button></div></div>';
    }
    }
    h += '<p class="lm-muted" style="margin-top:12px"><span class="lm-link" onclick="opToggleForm(\'own\')">' + (opOpen.own ? 'Cancel' : 'Add a model running elsewhere (Advanced)') + '</span></p>';
    if (opOpen.own) {
      h += '<div class="lm-form"><label>Network address (like http://192.168.1.60:8080)</label><input type="text" id="opOwnAddr" placeholder="http://192.168.1.60:8080" value="' + esc(opForm.ownAddress) + '">' +
        '<label>Nickname</label><input type="text" id="opOwnNick" placeholder="Like den-llama" value="' + esc(opForm.ownNick) + '">' +
        '<label>API Key (if needed)</label><input type="text" id="opOwnKey" placeholder="Leave empty if it doesn\'t" value="' + esc(opForm.ownKey) + '">' +
        '<div class="lm-muted" style="margin-top:8px">Add a connection to an AI provider already running on your network. <span class="hp-how" style="margin-left:0">' + "<a onclick=\"openHelp('remote')\">More in Help.</a>" + '</span></div>' +
        '<div class="lm-actions"><button onclick="opAddOwn()">Add</button></div></div>';
    }
    h += '</div>';
  }

  document.getElementById('opBody').innerHTML = h;
  if (errText) opShowError(errText);
}
"""
