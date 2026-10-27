"""The dashboard's Models tab (2026-10-26, design/mockup-model-management.html): every downloaded model on each PC, where it is used, with Start / Stop / Remove / Add / Delete.

One table per PC, a PC picker only when there is more than one. Every action calls the same routes the Local device, Decisions, Voice and Other devices tabs call (nothing is
written twice), and Remove uses the Local device tab's own one pop-up (`lmRemoveFlow`). All the words of the tab are in `MD_WORDS`, in one place. The status is a dot and a word
(the Overview's `ovDot`), and the buttons sit on the right in two fixed slots so the first buttons line up in one column and the second buttons in another.
"""

import tab_desc_ui

MODELS_TAB_HTML = """<div class="tab" id="tabModels" onclick="showMainTab('models')">Models</div>"""

MODELS_PANE_HTML = """
  <div id="paneModels" style="display:none">
    """ + tab_desc_ui.html("models") + """<div id="mdBody"><div class="lm-muted">Looking at your devices…</div></div>
  </div>
"""

MODELS_CSS = r"""
  /* Models tab -- md-* classes; the ov-table / ov-fixed table, the ov-dot dots, lm-* text and the op-modal pop-up are reused */
  .md-pick { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 12px }
  .md-table { table-layout:fixed; width:100% }
  .md-table col.c-size { width:6rem } .md-table col.c-used { width:14rem } .md-table col.c-st { width:8.5rem } .md-table col.c-act { width:13.5rem }
  .md-table td { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .md-table td.md-act { overflow:visible; text-align:right }
  .md-table th.md-act { text-align:right }
  .md-slots { display:flex; justify-content:flex-end; gap:8px }
  .md-slot { width:6rem; display:flex; justify-content:flex-end }
  .md-slot button { min-width:5.5rem }
  .md-own { color:var(--muted); font-size:0.78rem }
  .md-foot { margin:10px 0 0; display:flex; gap:12px; flex-wrap:wrap; align-items:center; color:var(--muted); font-size:0.85rem }
  .md-note { color:var(--muted); font-size:0.85rem; margin:8px 0 0 }
  .md-st { white-space:nowrap }
"""

MODELS_JS = r"""// ---------------- Models tab (every downloaded model on each PC) ----------------
const MD_WORDS = {
  heads: { model: 'Model', size: 'Size', used: 'Used by', status: 'Status' },
  notUsed: 'Not used', ownTag: '(your file)',
  start: 'Start', stop: 'Stop', remove: 'Remove', add: 'Add', del: 'Delete',
  folder: 'Models folder:', openFolder: 'Open folder',
  usedFree: (used, free, drive) => (used ? used + ' used, ' : '') + (free ? free + ' free' + (drive ? ' on ' + drive : '') : ''),
  runnerInUse: 'MultiProxy is using this engine right now.',
  noModels: 'No models are downloaded on this PC yet.',
  offline: 'This PC is offline.',
};
let mdSt = null, mdTimer = null, mdPc = 'this', mdErr = '', mdBusy = false;
function mdOpened() { mdLoad(); if (!mdTimer) mdTimer = setInterval(mdLoad, 5000); }
function mdClosed() { if (mdTimer) { clearInterval(mdTimer); mdTimer = null; } }
async function mdLoad() {
  if (mdBusy) return;
  try { mdSt = await (await fetch('/config/models-tab')).json(); } catch (e) { return; }
  mdRender();
}
// After any action: ask the page again, and the Local device tab too if it is there (the same list is drawn on both).
async function mdAct(fn) {
  mdBusy = true; mdErr = '';
  try { await fn(); } catch (e) { mdErr = e.message; }
  mdBusy = false;
  await mdLoad();
  if (typeof lmRefresh === 'function') lmRefresh();
}
// "Dialogue, Actions, Memory" for three or fewer; "Dialogue, Actions and 8 more" for a long list (the full list is the hover).
function mdNames(list) { return list.length <= 3 ? list.join(', ') : list.slice(0, 2).join(', ') + ' and ' + (list.length - 2) + ' more'; }
function mdUsedCell(r) {
  if (!(r.usedBy || []).length) return '<td><span class="soft">' + esc(MD_WORDS.notUsed) + '</span></td>';
  return '<td title="' + esc(r.usedBy.join(', ')) + '">' + esc(mdNames(r.usedBy)) + '</td>';
}
function mdLevel(s) { return s === 'running' || s === 'inuse' ? 'ok' : (s === 'loading' ? 'warn' : (s === 'error' ? 'bad' : 'off')); }
// The first button (Start / Stop / Add) and the second (Remove / Delete) each have a fixed slot, so they line up down the table.
function mdBtn(label, onclick, primary, off) { return '<button class="' + (primary ? 'primary' : 'ghost') + '"' + (off ? ' disabled title="' + esc(LM_OFF.hover) + '"' : '') + ' onclick="' + onclick + '">' + esc(label) + '</button>'; }
function mdActions(pc, r) {
  const q = s => "'" + jsq(s) + "'";
  let a = '', b = '';
  if (r.kind === 'kept') {
    a = mdBtn(MD_WORDS.add, 'mdAddKept(' + q(r.id) + ')', true);
    b = mdBtn(MD_WORDS.del, 'mdDeleteKept(' + q(r.id) + ', ' + q(r.name) + ', ' + q(r.sizeText || '') + ')');
  } else if (r.kind === 'llama-runner') {        // a llama.cpp runner build: the one in use is greyed (it can't be deleted from here)
    b = r.canDelete ? mdBtn(MD_WORDS.del, 'mdDeleteRunner(' + q(r.dirName) + ', ' + q(r.name) + ', ' + q(r.sizeText || '') + ')')
      : '<button class="ghost" disabled title="' + esc(MD_WORDS.runnerInUse) + '">' + esc(MD_WORDS.del) + '</button>';
  } else if (r.kind === 'kept-dl' || r.kind === 'runner' || r.kind === 'orphan') {        // a download in the kept store: a decision or voice model (Add + Delete), a runner or a leftover file (Delete)
    if (r.kind === 'kept-dl') a = mdBtn(MD_WORDS.add, 'mdAddDownload(' + q(r.what) + ', ' + q(r.modelKey) + ')', true);
    b = mdBtn(MD_WORDS.del, 'mdDeleteDownload(' + q(r.names.join('|')) + ', ' + q(r.name) + ', ' + q(r.sizeText || '') + ', ' + q((r.usedBy || []).join('|')) + ')');
  } else if (r.kind === 'remote') {
    if (r.canRun && r.status !== 'loading') a = mdBtn(r.status === 'running' ? MD_WORDS.stop : MD_WORDS.start, 'mdRemote(' + q(pc.id) + ', ' + q(r.program) + ', ' + q(r.id) + ', ' + q(r.status === 'running' ? 'unload' : 'load') + ')');
  } else {
    const run = r.kind !== 'decision' && r.kind !== 'voice' ? true : r.canRun !== false;
    if (run && r.status !== 'loading') a = mdBtn(r.status === 'running' ? MD_WORDS.stop : MD_WORDS.start, 'mdRun(' + q(r.kind) + ', ' + q(r.id) + ', ' + q(r.status === 'running' ? 'stop' : 'start') + ')', false, r.status !== 'running' && (r.kind === 'model' || r.kind === 'own') && !!(mdSt && mdSt.modelsOff));      // the Settings switch: no model starts here
    b = mdBtn(MD_WORDS.remove, 'mdRemove(' + q(r.kind) + ', ' + q(r.id) + ', ' + q(r.name) + ', ' + q(r.sizeText || '') + ', ' + q((r.usedBy || []).join('|')) + ')');
  }
  return '<td class="md-act"><div class="md-slots"><div class="md-slot">' + a + '</div><div class="md-slot">' + b + '</div></div></td>';
}
function mdRow(pc, r) {
  return '<tr><td title="' + esc(r.name) + '"><b>' + esc(r.name) + '</b>' + (r.kind === 'own' ? ' <span class="md-own">' + esc(MD_WORDS.ownTag) + '</span>' : '') + '</td>' +
    '<td>' + (r.sizeText ? esc(r.sizeText) : '<span class="soft">-</span>') + '</td>' + mdUsedCell(r) +
    '<td class="md-st" title="' + esc(r.word) + '">' + ovDot(mdLevel(r.status)) + esc(r.word) + '</td>' + mdActions(pc, r) + '</tr>';
}
function mdTable(pc) {
  const rows = pc.rows || [];
  let h = '<table class="ov-table md-table"><colgroup><col><col class="c-size"><col class="c-used"><col class="c-st"><col class="c-act"></colgroup><tr><th>' + esc(MD_WORDS.heads.model) + '</th><th>' + esc(MD_WORDS.heads.size) +
    '</th><th>' + esc(MD_WORDS.heads.used) + '</th><th>' + esc(MD_WORDS.heads.status) + '</th><th class="md-act"></th></tr>';
  for (const r of rows) h += mdRow(pc, r);
  h += '</table>';
  if (!rows.length) h += '<div class="md-note">' + esc(pc.online === false ? MD_WORDS.offline : MD_WORDS.noModels) + '</div>';
  if (pc.note) h += '<div class="md-note">' + esc(pc.note) + '</div>';
  if (pc.folder || pc.freeBytes || pc.usedBytes) {
    const fmt = n => n ? lmFmt(n) : '';
    h += '<div class="md-foot">' + (pc.folder ? '<span>' + esc(MD_WORDS.folder) + ' <code>' + esc(pc.folder) + '</code></span>' : '') +
      (pc.this && pc.folder ? '<button class="ghost" onclick="mdOpenFolder()">' + esc(MD_WORDS.openFolder) + '</button>' : '') +
      '<span>' + esc(MD_WORDS.usedFree(fmt(pc.usedBytes), fmt(pc.freeBytes), pc.drive)) + '</span></div>';
  }
  return h;
}
function mdRender() {
  const box = document.getElementById('mdBody');
  if (!box || !mdSt) return;
  const pcs = mdSt.pcs || [];
  if (!pcs.some(p => p.id === mdPc)) mdPc = 'this';
  let h = '';
  if (pcs.length > 1) h += '<div class="md-pick">' + pcs.map(p => '<button class="' + (p.id === mdPc ? 'primary' : 'ghost') + '" onclick="mdPick(\'' + jsq(p.id) + '\')">' + esc(p.name) + '</button>').join('') + '</div>';
  const pc = pcs.find(p => p.id === mdPc);
  if (pc) h += mdTable(pc);
  if (mdErr) h += '<div class="lm-err">' + esc(mdErr) + '</div>';
  box.innerHTML = h;
}
function mdPick(id) { mdPc = id; mdRender(); }
function mdOpenFolder() { mdAct(() => lmPost('/config/settings/open-folder', { what: 'models' })); }
function mdRun(kind, id, what) {
  const url = kind === 'decision' ? '/config/decisions/' + what : (kind === 'voice' ? '/config/voice/' + what : '/config/local-models/' + what);
  const body = kind === 'decision' ? { pc: 'this' } : (kind === 'voice' ? {} : { id });
  mdAct(() => lmPost(url, body));
}
function mdRemote(pcId, program, model, action) { mdAct(() => lmPost('/config/other-pcs/courier/action', { id: pcId, program, action, model })); }
function mdRemove(kind, id, name, sizeText, usedBy) {
  const used = usedBy ? usedBy.split('|') : [];
  if (kind === 'decision' || kind === 'voice') {        // the same one pop-up as a downloaded chat model: keep the files, or delete them too
    lmRemoveFlow(id, name, false, sizeText, used).then(r => {
      if (r) mdAct(() => kind === 'decision' ? lmPost('/config/decisions/uninstall', { pc: 'this', deleteFiles: r.deleteFiles }) : lmPost('/config/voice/uninstall', { model: id, deleteFiles: r.deleteFiles }));
    });
    return;
  }
  lmRemoveFlow(id, name, kind === 'own', sizeText, used).then(r => { if (r) mdAct(() => lmPost('/config/local-models/remove', { id, deleteFiles: r.deleteFiles })); });
}
// Add from the kept files: the engines' own install checks each file's size and checksum first and downloads nothing that is kept.
function mdAddDownload(what, key) {
  mdAct(() => what === 'decision' ? lmPost('/config/decisions/install', { pc: 'this', model: key }) : lmPost('/config/voice/install', { model: key }));
}
function mdDeleteDownload(names, name, sizeText, usedBy) {
  const used = usedBy ? usedBy.split('|') : [];
  const go = ok => { if (ok) mdAct(() => lmPost('/config/models-tab/delete-downloads', { names: names.split('|') })); };
  if (used.length) ownAskRemove(LM_REMOVE_WORDS.deleteGo + ' ' + name + '?', LM_REMOVE_WORDS.deleteAsk(name, sizeText), LM_REMOVE_WORDS.deleteGo, null, used).then(go);
  else ownAsk(LM_REMOVE_WORDS.deleteAsk(name, sizeText), LM_REMOVE_WORDS.deleteGo).then(go);
}
function mdDeleteRunner(dir, name, sizeText) {
  ownAsk(LM_REMOVE_WORDS.deleteAsk(name, sizeText), LM_REMOVE_WORDS.deleteGo).then(ok => { if (ok) mdAct(() => lmPost('/config/local-models/delete-engine-build', { dir })); });
}
function mdAddKept(id) { mdAct(() => lmPost('/config/local-models/add-kept', { id })); }
function mdDeleteKept(id, name, sizeText) {
  ownAsk(LM_REMOVE_WORDS.deleteAsk(name, sizeText), LM_REMOVE_WORDS.deleteGo).then(ok => { if (ok) mdAct(() => lmPost('/config/local-models/delete-kept', { id })); });
}
"""
