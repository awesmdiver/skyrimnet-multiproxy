"""The dashboard's "Decisions" tab (2026-10-23): where the local decision model runs, one row per device, built to design/mockup-decisions-tab.html (revision 2, approved).
Before this it was a "Quick decisions" card far down the Local models tab (2026-10-02, design/local-jev-mockup.html); the card's parts moved here as they were.

Plain strings in their own file, spliced into multiproxy.py's page like the other tabs, so none of the CSS/JS needs doubled braces. Everything that came from the server
goes through esc() (defined by the Local models card's script, on the same page) before it reaches HTML. The data is `GET /config/decisions/status` every 2 seconds while
the tab is open (plus `/config/decisions/fit` for each device that doesn't have the model yet); the buttons call the plain routes next to it (decision_wiring.py).
The sentences about where it runs, the problems and the labels come from the server and are shown as they are. Reuses the Voice tab's look (its dialog, menu and button
classes: vc-*, op-modal, op-log) so the two tabs behave alike.

The tab is a list of devices (this one and every paired one) in the Voice tab's row style, then the card of the device in use, then "Who answers decisions". "Set it up" on a
row installs on that device at once with the recommended model that fits and stays on the tab; the server (decision_wiring.py) moves the decisions there only when the
install has finished and the model is running. `dcGoRow(device)` is how every other page (Overview, Other devices, Help, Local models) lands on a device's row.
"""

import jump_ui

DECISION_TAB_HTML = """<div class="tab" id="tabDecisions" onclick="showMainTab('decisions')">Decisions</div>"""

DECISION_CSS = r"""
  /* Decisions tab -- dc-* classes; the vc-* ones from the Voice tab, op-* dots/modal/log and lm-* text are reused */
  /* the settings use the run-settings card's two-column classes (ls-grid, ls-coltitle, ls-f, ls-why in local_settings_ui.py); only the checkbox and its numbers are new */
  .dc-check { display:flex; gap:8px; align-items:center; font-weight:600; margin-top:6px }
  .dc-row { display:flex; gap:28px; flex-wrap:wrap; align-items:center; margin-top:12px; color:var(--muted); font-size:0.9rem }
  .dc-off { opacity:.55 }
  .dc-state.dc-r { text-align:right }          /* the update words sit right beside their button */
  .dc-steps { margin:6px 0 0 22px; padding:0; list-style:none; display:flex; flex-direction:column; gap:10px; counter-reset:dc }
  .dc-steps > li { font-size:0.85rem; line-height:1.5 }
  .dc-steps > li::before { counter-increment:dc; content:counter(dc) ". "; font-weight:600 }
  .dc-steps ul { margin:6px 0 0 18px; padding:0; list-style:none; display:flex; flex-direction:column; gap:7px }
  .dc-field { display:flex; align-items:center; gap:8px; flex-wrap:wrap }
  .dc-field input { flex:1; min-width:180px; max-width:420px; font-family:ui-monospace,Consolas,monospace; font-size:0.82rem }
  .dc-field b { min-width:96px }
  .dc-num { width:90px !important; flex:0 0 auto !important }
  .dc-sentence { display:flex; gap:6px 8px; flex-wrap:wrap; align-items:center; cursor:default }
  .dc-sentence .dc-num { width:56px !important }
  /* the numbered list of who answers: handle, number, name, model, status, link */
  .dc-ord { width:100%; border-collapse:collapse; table-layout:fixed }
  .dc-ord col.c-h { width:2.8rem } .dc-ord col.c-n { width:2rem } .dc-ord col.c-as { width:30% } .dc-ord col.c-st { width:9rem } .dc-ord col.c-ln { width:7rem }
  .dc-ord th { font-weight:500; color:var(--muted); font-size:0.78rem; text-align:left; padding:6px 8px; border-bottom:1px solid var(--line) }
  .dc-ord td { padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:middle; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .dc-ord td.dc-ln { text-align:right }
  .dc-ord td.dc-h { overflow:visible; text-overflow:clip }
  .dc-ord select { max-width:100% }
  .dc-ord-note td { white-space:normal; padding-top:0 }
  .dc-n { color:var(--muted) } .dc-grip { cursor:grab; color:var(--muted); user-select:none; padding:2px 4px; border-radius:4px } .dc-grip:focus-visible { outline:2px solid var(--accent) }
  .dc-ord-row[draggable="true"]:active { opacity:.7 }
  /* one row per device, like the Voice tab's */
  .dc-devs { border-top:1px solid var(--line); margin-top:8px; display:grid; grid-template-columns:minmax(0, 1.2fr) minmax(0, 1.4fr) auto; column-gap:16px }
  .dc-dev { display:grid; grid-column:1 / -1; grid-template-columns:subgrid; gap:6px 16px; align-items:center; padding:12px 6px; border-bottom:1px solid var(--line) }
  .dc-dev > * { min-width:0; overflow-wrap:anywhere }
  .dc-nm { display:flex; align-items:center; gap:6px; min-width:0 }
  .dc-nm > .op-dot { flex:none }
  .dc-nm > b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0 }
  .dc-nm .dc-sm { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0 }
  .dc-dev.dc-grey .dc-nm, .dc-dev.dc-grey .dc-state { opacity:.55 }
  .dc-sm { display:block; color:var(--muted); font-size:0.8rem; line-height:1.5 }
  .dc-nm .dc-sm { display:inline }
  .dc-act { display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap }
  .dc-extra { grid-column:1 / -1 }
  .dc-extra .vc-warn { margin:0 }
  .dc-extra .op-dot { flex:none; margin-top:5px }
  .dc-state .vc-bar { margin:6px 0 4px 0 }
  .dc-flash { outline:2px solid var(--accent); outline-offset:-2px; border-radius:8px }
  .dc-head { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:4px }
  @media (max-width:900px) { .dc-devs { grid-template-columns:minmax(0, 1fr) } .dc-act { justify-content:flex-start } }
"""

DECISION_PANE_HTML = """
  <div id="paneDecisions" style="display:none">
    """ + jump_ui.row("dcJump") + """
    <div id="dcModalBox" class="op-modal" style="display:none" onclick="if (event.target === this) dcCloseModal()"></div>
    <div id="dcBody"><div class="lm-muted">Looking at your devices…</div></div>
  </div>
"""

# What stays on the Local models tab (the chat models and the graphics-memory panel only): one muted line, and no card.
LOCAL_MODELS_LINE_HTML = """
  <p class="lm-muted" id="dcLocalLine" style="margin:14px 0 0">Quick decisions have their own tab. <a class="lm-link" onclick="dcGoRow()">Open Decisions</a></p>
"""

DECISION_JS = r"""// ---------------- Decisions tab (the local decision model, one row per device) ----------------
if (typeof jumpRegister === 'function') jumpRegister('dcJump', 'paneDecisions', () => jumpIds(['dcQuick', 'dcDevices', 'dcCard', 'dcWho']), {});
let dcSt = null, dcTimer = null, dcLoading = false;
let dcFits = {};                  // per device: { fit, error, at, loading }
let dcMenu = false, dcModal = null, dcNote = '', dcErr = '', dcRestart = false;
let dcSwitched = '';              // the one amber line after Cloud only moved the first choice to a key (until the next change)
const DC_CLOUD = { box: 'Cloud only: never start a local decision model.' };
let dcSamples = {};               // device -> [[seconds, bytes done]] for an honest "time left"
let dcSource = 'gaming-pc';       // where another device gets its files: 'gaming-pc' (copied from this device) | 'that-pc' (it downloads them itself); the one click always uses the first
let dcFail = {};                  // device -> the message of a Set it up that was refused or failed, until the next try
let dcCancelled = {};             // device -> true once its install was cancelled (until the next one starts)
let dcPaused = {};                // another device -> true once its install was paused (this device's own pause is remembered by the server, so it survives a restart)
let dcWantRow = null, dcFlash = null, dcLast = '';   // a device row another page asked to be shown; the row being flashed; the markup last drawn

function dcGb(bytes) { return (bytes / 1073741824).toFixed(1); }
function dcSelected() { return dcSt && (dcSt.models || []).find(m => m.key === dcSt.settings.model) || { key: 'kev-4b', label: 'Kev-4B', blurb: '', graphicsBytes: 5 * 1073741824, diskBytes: 4.5e9 }; }
function dcSafe(k) { return String(k).replace(/[^A-Za-z0-9_-]/g, c => '_' + c.charCodeAt(0).toString(16)); }

// ---- loading ----
function dcOpened() {
  dcLoad();
  if (!dcTimer) dcTimer = setInterval(dcLoad, 2000);
}
try { document.addEventListener('visibilitychange', () => { if (!document.hidden && dcTimer) dcLoad(); }); } catch (e) {}
function dcClosed() { if (dcTimer) { clearInterval(dcTimer); dcTimer = null; } }
async function dcLoad() {
  if (dcLoading || document.hidden) return;                  // (a hidden browser tab asks for nothing; it refreshes the moment it is shown again; a round never starts while one is running)
  dcLoading = true;
  try {
    const r = await fetch('/config/decisions/status');
    if (!r.ok) throw new Error('status ' + r.status);
    dcSt = await r.json();
    dcErr = dcErr === 'LOAD' ? '' : dcErr;
    for (const dev of dcDevices()) {
      const i = dcInfo(dev.k);
      if (dcCanSetUp(i) && !i.installed && !(i.install && i.install.state === 'running')) dcWantFit(dev.k);
    }
  } catch (e) { if (!dcSt) dcErr = 'LOAD'; }
  dcLoading = false;
  dcRender();
}
function dcCard(gpu) { return gpu && gpu.name ? opShortCard(gpu.name) + (gpu.memoryGb ? ', ' + Math.round(gpu.memoryGb) + ' GB' : '') : ''; }
function dcShowPc(k) { const p = ((dcSt && dcSt.pcs) || []).find(x => x.id === k); return (p && p.name) || k; }          // a device's name as typed or found (MSI); its id (msi) is only the key
function dcPcName(k) { return k === 'this' ? 'this device' : dcShowPc(k); }
function dcDevName(k) { return k === 'this' ? 'This device' : dcShowPc(k); }
function dcCanSetUp(i) { return i.online !== false && i.hasSupport !== false; }
function dcWantFit(k) {
  const cur = dcFits[k];
  if (cur && (cur.loading || Date.now() - cur.at < 30000)) return;
  dcFits[k] = { ...(cur || {}), loading: true, at: cur ? cur.at : 0 };
  fetch('/config/decisions/fit' + (k === 'this' ? '' : '?pc=' + encodeURIComponent(k))).then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || ''); return d; })
    .then(d => { dcFits[k] = { fit: d, at: Date.now() }; dcRender(); },
      e => { dcFits[k] = { fit: null, error: (e && e.message) || (k === 'this' ? "Couldn't check this device." : "Couldn't check " + dcShowPc(k) + '.'), at: Date.now() }; dcRender(); });
}
// this device first, then every paired device
function dcDevices() {
  const st = dcSt, out = [{ k: 'this', name: 'This device', sub: [(st.thisPc || {}).name, dcCard((st.thisPc || {}).gpu).replace(', ', ' · ')].filter(Boolean).join(' · '), gpu: (st.thisPc || {}).gpu }];
  (st.pcs || []).forEach(p => out.push({ k: p.id, name: p.name || p.id, sub: dcCard(p.gpu).replace(', ', ' · '), gpu: p.gpu }));
  return out;
}
// everything the tab draws for one device, in the shape the status has for the one that runs it
function dcInfo(k) {
  const st = dcSt;
  const shared = { key: k, settings: st.settings, models: st.models, cloudKey: st.cloudKey, cloudOnlyWords: st.cloudOnlyWords, typesafeKey: st.typesafeKey, nanoKey: st.nanoKey, nanoLine: st.nanoLine, typesafeLine: st.typesafeLine, nanoActive: st.nanoActive, providers: st.providers || [], lastAnswer: st.lastAnswer, providerName: st.providerName, apiKeyWord: st.apiKeyWord,
    address: st.address, localName: st.localName, downloads: st.downloads, online: true, hasSupport: true, jobError: '' };
  if (k === 'this') return { ...(k === st.location ? st : (st.thisEngine || {})), ...shared };
  const p = (st.pcs || []).find(x => x.id === k) || {};
  const base = k === st.location ? st : { ...(p.decisions || {}), installed: !!(p.decisions && p.decisions.installed) };
  const i = { ...base, ...shared, online: p.online !== false, hasSupport: p.hasDecisions !== false };
  const job = p.job;
  if (job && job.state === 'running') i.install = job;
  else if (job && job.state === 'error') i.jobError = job.error;
  if (!i.online) i.state = 'unreachable';
  return i;
}

// ---- the model "Set it up" installs: the recommended one that fits (the server picks the same way, decision_wiring.py) ----
function dcPlannedModel(dev) {
  const models = dcSt.models || [], saved = dcSt.settings.model;
  const mb = dev.gpu && dev.gpu.memoryGb ? dev.gpu.memoryGb * 1024 : 0;
  let pick = mb && mb < 4 * 1024 ? 'laya' : 'kev-4b';
  if (saved !== pick && (dcSt.installedModels || []).includes(saved)) pick = saved;          // a model the device in use already has working is kept
  return models.find(m => m.key === pick) || models[0] || dcSelected();
}
function dcNeeds(m) {
  return 'needs about ' + Math.max(1, Math.round(m.graphicsBytes / 1073741824)) + ' GB of graphics memory' + (m.diskBytes > 0 ? ' and ' + dcGb(m.diskBytes) + ' GB of disk space' : '');
}
function dcAbout(m) {
  return 'Needs about ' + Math.max(1, Math.round(m.graphicsBytes / 1073741824)) + ' GB of graphics memory' + (m.diskBytes > 0 ? ' and ' + dcGb(m.diskBytes) + ' GB of disk space' : '') + '.';
}
function dcModelLabel(key) { const m = (dcSt.models || []).find(x => x.key === key); return m ? m.label : (key || 'The decision model'); }
function dcRunOnOptions(cur) {
  return [['automatic', 'Automatic'], ['gpu', 'Graphics card'], ['cpu', 'System memory']].map(o => '<option value="' + o[0] + '"' + (cur === o[0] ? ' selected' : '') + '>' + o[1] + '</option>').join('');
}
function dcModelOptions(cur) {
  return (dcSt.models || []).map(m => '<option value="' + esc(m.key) + '"' + (cur === m.key ? ' selected' : '') + '>' + esc(m.label) + '</option>').join('');
}

// ---- the rows: "Where decisions run" ----
function dcStatusText(s) {
  if (s.state === 'ready') {
    const busy = s.waiting ? esc(s.label) + '. ' : '';
    if (s.runOn === 'cpu') {
      if (/graphics card is too full/.test(s.runOnReason || '')) return busy + 'Ready. Using system memory because your graphics card is full. Answers will be slightly slower.';
      return busy + 'Ready. Using system memory.';
    }
    return busy + 'Ready.';
  }
  if (s.state === 'stopped') return s.runnerMissing ? 'Needs the engine.' : 'Stopped.';
  return esc(s.detail || s.label || '');
}
// the install in a row: which step of how many, the bar, and the words under it (the main device's own words while it downloads or copies for that device)
function dcInstallBits(k, j) {
  const own = !!(j.message && j.stage !== 'installing'), only = !!j.runnerOnly;
  let step, of;
  if (k === 'this') { of = 3; step = { engine: 1, model: 2, warmup: 3 }[j.step] || 1; }
  else { of = 4; step = j.stage === 'preparing' ? 1 : j.stage === 'copying' ? 2 : (j.step === 'warmup' ? 4 : 3); }
  let line;
  if (own) line = j.message + (j.total ? ' (' + dcGb(j.done) + ' of ' + dcGb(j.total) + ' GB).' : '');
  else if (j.step === 'engine') line = 'Getting the engine software (one time)…';
  else if (j.step === 'model') line = 'Downloading the decision model (' + dcGb(j.done) + ' of ' + dcGb(j.total) + ' GB).';
  else line = 'Getting ready: doing a quick test.';
  const eta = (own ? !!j.total : j.step === 'model') && j.left ? '· ' + j.left : '';          // worded and measured by the server (download_meter.py): nothing until the speed is known
  const pct = only ? (j.total ? Math.min(100, Math.round(j.done * 100 / j.total)) : 0) : j.step === 'warmup' && !own ? 100 : ((j.step === 'engine' && !own) ? 0 : Math.min(100, Math.round(j.done * 100 / (j.total || 1))));
  return { line: line.replace(/\.$/, '') + (eta ? ' ' + eta : '.'), pct, step, of, only, cancel: !(j.step === 'warmup' && !own), pause: !(j.step === 'warmup' && !own) };
}
function dcUsingLine(dev) {              // "Nothing was changed: ..." names the device the decisions are still going to (nothing, when no device has the model)
  const loc = dcSt.location || 'this';
  return loc !== dev.k && dcInfo(loc).installed ? 'Nothing was changed: decisions still go to ' + esc(dcDevName(loc)) + '.' : 'Nothing was changed.';
}
function dcWarn(text, dot) { return '<div class="vc-warn">' + opDot(dot || 'warn') + '<span>' + text + '</span></div>'; }
function dcRowBits(dev) {
  const k = dev.k, i = dcInfo(k), st = dcSt, here = k === (st.location || 'this'), q = jsq(k);
  const j = i.install && i.install.state === 'running' ? i.install : null;
  const r = { dot: 'off', word: '', sub: '', bar: '', acts: '', extra: '', grey: false };
  if (st.settings && st.settings.cloud_only) {            // Cloud only: no device runs one, so every row reads Not used (an installed copy can still be uninstalled)
    r.grey = true; r.dot = 'idle'; r.word = 'Not used';
    if (i.installed && i.online !== false && i.hasSupport !== false) r.acts = '<button onclick="dcAskUninstall(\'' + q + '\')">Uninstall</button>';
    return r;
  }
  if (i.online === false) {
    r.grey = true; r.word = 'Offline';
    if (here && i.installed) {
      if (((st.pcs || []).find(x => x.id === k) || {}).autostart) r.sub = 'Waiting for ' + esc(dcShowPc(k)) + ' to come online';
      else r.extra += dcWarn('This won\'t start by itself. <a onclick="dcAuto(true, \'' + q + '\')">Start when MultiProxy starts</a>');
    }
  } else if (i.hasSupport === false) {
    r.grey = true; r.word = 'Needs a newer MultiProxy Courier'; r.dot = 'warn';
    const uc = ucInline(k, dcDevName(k));          // the same Update Courier help as the Other devices tab
    r.sub = uc.text; r.acts = uc.btn; r.right = !!uc.btn;
  } else if (j) {
    const b = dcInstallBits(k, j);
    r.dot = 'warn'; r.word = 'Installing, step ' + b.step + ' of ' + b.of;
    r.bar = '<div class="vc-bar"><i style="width:' + b.pct + '%"></i></div>';
    r.sub = esc(b.line);
    if (b.cancel) r.acts = (b.pause ? '<button class="ghost" onclick="dcPause(\'' + q + '\')">Pause</button>' : '') + '<button onclick="dcCancel(\'' + q + '\')">Cancel</button>';
  } else if (!i.installed && (i.paused || dcPaused[k])) {
    const part = i.paused && i.paused.done ? ' · ' + dcGb(i.paused.done) + ' GB downloaded' : '';
    r.dot = 'idle'; r.word = 'Paused' + part;
    if (i.paused && i.paused.total) r.bar = '<div class="vc-bar"><i style="width:' + Math.min(100, Math.round(i.paused.done * 100 / i.paused.total)) + '%"></i></div>';
    r.sub = 'Your download progress is saved.';
    r.acts = '<button class="primary" onclick="dcResume(\'' + q + '\')">Resume</button><button class="ghost" onclick="dcStopPause(\'' + q + '\')">Cancel</button>';
  } else if (!i.installed && !['starting', 'getting_ready'].includes(i.state)) {
    r.word = 'Not installed'; r.dot = 'none';
    const f = dcFits[k] || {}, fit = f.fit, planned = dcPlannedModel(dev);
    r.sub = esc(dcNeeds(planned));
    let ok = true;
    if (f.loading && !fit) { ok = false; r.sub += '<span class="dc-sm">Checking ' + esc(dcPcName(k)) + '…</span>'; }
    else if (!fit) { ok = false; r.extra += dcWarn(esc(f.error || "Couldn't check " + dcPcName(k) + '.')); }
    else if (fit.verdict === 'refuse') { ok = false; r.extra += (fit.reasons || []).map(x => dcWarn(esc(x))).join(''); }
    else r.extra += (fit.warnings || []).map(w => dcWarn(esc(w))).join('');
    const bad = dcFail[k] || i.jobError || (i.install && i.install.state === 'error' ? i.install.error : '');
    if (bad) r.extra += dcWarn(esc(bad) + ' ' + dcUsingLine(dev));
    else if (dcCancelled[k]) r.extra += dcWarn('Cancelled. What was downloaded is kept, so Set it up carries on from there. ' + dcUsingLine(dev));
    r.acts = '<button class="primary"' + (ok ? '' : ' disabled') + ' onclick="dcSetUp(\'' + q + '\')">' + (bad ? 'Try again</button>' : 'Set it up</button>');
  } else if (!here) {
    const still = ['ready', 'starting', 'getting_ready'].includes(i.state) || i.leftoverRunning;      // an engine left running that nothing uses (amber, with a way to shut it down)
    r.dot = still ? 'warn' : 'idle'; r.word = still ? 'Still running, not in use' : 'Installed, not in use';        // nothing is wrong when it is only not the one in use (grey)
    r.acts = (still ? '<button onclick="dcAct(\'' + q + '\', \'stop\')">Shut down</button>' : '') +
      '<button onclick="dcUse(\'' + q + '\')">Use it</button><button onclick="dcAskUninstall(\'' + q + '\')">Uninstall</button>';
    const bad = dcFail[k] || i.jobError;
    if (bad) r.extra += dcWarn(esc(bad));
  } else {
    const canStart = ['stopped', 'died', 'error'].includes(i.state);
    r.dot = i.state === 'ready' ? 'on' : (i.state === 'starting' || i.state === 'getting_ready') ? 'warn' : (i.state === 'stopped' ? (i.runnerMissing ? 'warn' : 'idle') : 'off');       // stopped is grey; a missing runner needs a fix (amber); died / problem stay red
    if (i.state === 'ready') {
      r.word = 'Running';
      r.sub = esc(dcModelLabel(i.model) + ' · decisions run here');
      if (i.waiting) r.sub += '<span class="dc-sm">' + esc(i.label) + '</span>';
      if (i.runOn === 'cpu') r.sub += '<span class="dc-sm">' + dcStatusText({ ...i, waiting: 0 }).replace(/^Ready\.\s*/, '') + '</span>';
    } else if (i.state === 'stopped') {
      r.word = i.runnerMissing ? 'Needs the engine' : 'Stopped';
    } else if (i.state === 'starting' || i.state === 'getting_ready') {
      r.word = esc(i.label || 'Starting'); r.sub = esc(i.detail || '');
    } else if (i.state === 'died') {
      r.word = 'Stopped unexpectedly'; r.sub = esc(i.detail || '');
    } else {
      r.word = esc(i.label || 'Problem'); r.sub = esc(i.detail || '');
    }
    if (i.state === 'unreachable') { r.word = 'Not ready'; r.sub = esc(i.detail || ''); }
    const badRunner = i.runnerMissing && canStart ? (dcFail[k] || i.jobError || (i.install && i.install.state === 'error' ? i.install.error : '')) : '';
    if (canStart && i.runnerMissing) r.acts += '<button class="primary" onclick="dcGetRunner(\'' + q + '\')">' + (badRunner ? 'Try again</button>' : 'Get the engine</button>');
    else if (canStart) r.acts += '<button class="primary" onclick="dcAct(\'' + q + '\', \'start\')">Start</button>';
    else if (i.state === 'ready') r.acts += '<button onclick="dcAct(\'' + q + '\', \'stop\')">Shut down</button><button onclick="dcAct(\'' + q + '\', \'restart\')">Restart</button>';
    if (i.state !== 'unreachable') r.acts += '<button onclick="dcOpenLog(\'' + q + '\')">Log</button>';
    if (i.runnerMissing && canStart) r.extra += dcWarn(badRunner ? esc(badRunner) + ' ' + dcUsingLine(dev) : 'The engine software is missing.');
    if (((dcSt.settings.decision_order || [])[0] || 'local') === 'local' && canStart && !i.runnerMissing && i.state !== 'unreachable') r.extra += dcWarn('The local decision model is off, so decisions are going to another AI. Press <b>Start</b>.');
    if (dcNote) r.sub += '<span class="dc-sm">' + esc(dcNote) + '</span>';
    const bad = dcFail[k] || (i.install && i.install.state === 'error' ? i.install.error : '');
    if (bad) r.extra += dcWarn(esc(bad));
    if (k !== 'this') {
      if (((st.pcs || []).find(x => x.id === k) || {}).autostart) r.sub += '<span class="dc-sm">Starts by itself when MultiProxy starts and when this device comes online. Change it in the ⋯ menu.</span>';
      else r.extra += dcWarn('This won\'t start by itself. <a onclick="dcAuto(true, \'' + q + '\')">Start when MultiProxy starts</a>');
    }
  }
  return r;
}
function dcRow(dev) {
  const r = dcRowBits(dev);
  return '<div class="dc-dev' + (r.grey ? ' dc-grey' : '') + '" id="dcRow-' + dcSafe(dev.k) + '" data-k="' + esc(dev.k) + '"><div class="dc-nm" title="' + esc(dev.name + (dev.sub ? ' · ' + dev.sub : '')) + '">' + opDot(r.dot) + '<b>' + esc(dev.name) + '</b>' +
    (dev.sub ? '<span class="dc-sm">· ' + esc(dev.sub) + '</span>' : '') + '</div><div class="dc-state' + (r.right ? ' dc-r' : '') + '">' + r.word + r.bar + (r.sub ? '<span class="dc-sm">' + r.sub + '</span>' : '') + '</div>' +
    '<div class="dc-act">' + r.acts + '</div>' + (r.extra ? '<div class="dc-extra">' + r.extra + '</div>' : '') + '</div>';
}

// "Add it here": the Providers tab with TypeSafe selected (the same jump the Help pages use), then its key box in view and ready to type in
function dcGoKey(id) {
  helpGoSetup(id || 'typesafe');
  const box = document.getElementById('keyInput');
  if (box) { box.scrollIntoView({ block: 'center' }); box.focus(); }
}
// ---- the card of the device in use ----
function dcDeviceCard(k, s) {
  const set = s.settings, q = jsq(k), name = k === 'this' ? 'this device' : esc(dcShowPc(k));
  const model = '<div class="ls-f"><label for="dcSelModel">Model</label><div class="vc-pick"><select id="dcSelModel" onchange="dcSetting({ model: this.value })">' + dcModelOptions(set.model) + '</select></div>' +
    '<div class="ls-why">' + esc(dcSelected().blurb) + ' ' + esc(dcAbout(dcSelected())) + '</div></div>';
  const runOn = '<div class="ls-f"><label for="dcSelRun">Run on</label><div class="vc-pick"><select id="dcSelRun" onchange="dcSetting({ runOn: this.value })">' + dcRunOnOptions(set.run_on) + '</select>' +
    (dcRestart ? '<span class="lm-muted">Restart it to use this.</span><button onclick="dcAct(\'' + q + '\', \'restart\')">Restart now</button>' : '') + '</div>' +
    (dcRestart ? '' : '<div class="ls-why">Which graphics card. This takes effect next time it starts.</div>') + '</div>';
  return '<div class="dc-head"><h3 style="margin:0;flex:1">Local decision model on ' + name + '</h3><span class="vc-menu-wrap"><button onclick="dcToggleMenu(event)" aria-label="More">⋯</button>' + (dcMenu ? dcMenuHtml(k) : '') + '</span></div>' +
    (s.state === 'unreachable' ? '' : dcSkyrimNetSteps(s)) +
    '<div class="ls-grid"><div class="ls-coltitle">Local model</div>' + model + runOn + '</div>' +
    '<p class="lm-muted" style="margin:12px 0 0">The decision models have a few settings that must stay exactly as MultiProxy sets them (the small one crashes the engine if its batch size is smaller than the scene it reads). The settings on the Local device tab do not change them: no batch, memory-per-conversation or requests-at-the-same-time boxes. Only the speed and the device it runs on are here.</p>';
}
// "Who answers decisions": a numbered list, dragged into the player's own order. One row per service that can answer a decision (the saved order is `settings.decision_order`):
// the first one that is ready answers, a row that is not ready (no key, not installed, stopped) is skipped, and the sentence under the list hands a question to the next
// model down when the current one is not sure enough or takes too long.
let dcOrdDrag = null, dcOrdFocus = null;
function dcSvc(id, s, li) {
  const loc = dcSt.location || 'this', set = s.settings;
  if (id === 'local') {
    const label = dcModelLabel(set.model);
    const o = { id, name: 'Local model', sub: label + ', on ' + (loc === 'this' ? ((dcSt.thisPc || {}).name || 'this device') : dcShowPc(loc)), model: '<span class="lm-muted">' + esc(label) + '</span>', dot: 'idle', word: 'Not installed', link: '' };
    if (li.installed) {
      if (li.state === 'ready') { o.dot = 'on'; o.word = 'Ready'; }
      else if (li.state === 'starting' || li.state === 'getting_ready') { o.dot = 'warn'; o.word = 'Starting'; }
      else { o.dot = 'idle'; o.word = 'Stopped'; }
    }
    if (set.cloud_only) { o.dot = 'idle'; o.word = 'Not used'; o.link = ''; }
    return o;
  }
  if (id === 'typesafe') {
    return { id, name: 'TypeSafe', sub: '', model: '<span class="lm-muted">-</span>', dot: s.typesafeKey ? 'on' : 'idle', word: s.typesafeKey ? 'Ready' : 'No key', link: s.typesafeKey ? '' : dcKeyLink('typesafe') };
  }
  const p = (s.providers || []).find(x => x.id === id) || { id, label: id, models: [] };
  const keyed = id === 'nano' ? !!s.nanoKey : p.keyOk !== false;
  const ready = id === 'nano' ? (!!s.nanoKey) : !!p.ready;
  const name = id === 'nano' ? 'Nano-GPT' : String(p.label || id).replace(/^My /, '').replace(/ decision model$/, '').replace(/ key$/, '');
  const models = p.models || [];
  let model = '<span class="lm-muted">-</span>';
  if (ready && models.length > 1) {
    model = '<select id="dcSelModelOf' + esc(id) + '" onchange="dcSetting({ decisionModel: { provider: \'' + jsq(id) + '\', model: this.value } })">' +
      models.map(m => '<option value="' + esc(m) + '"' + (m === p.model ? ' selected' : '') + '>' + esc((p.labels || {})[m] || m) + '</option>').join('') + '</select>';
  } else if (ready && models.length === 1) model = '<span class="lm-muted">' + esc((p.labels || {})[models[0]] || models[0]) + '</span>';
  return { id, name, sub: '', model, dot: ready ? 'on' : 'idle', word: ready ? 'Ready' : (keyed ? 'Stopped' : 'No key'), link: keyed ? '' : dcKeyLink(id) };
}
function dcKeyLink(id) { return '<a class="lm-link" onclick="dcGoKey(\'' + jsq(id) + '\')">Add a key</a>'; }
function dcOrdTable(s, li) {
  const ids = s.settings.decision_order || [];
  let h = '<table class="dc-ord"><colgroup><col class="c-h"><col class="c-n"><col class="c-as"><col class="c-md"><col class="c-st"><col class="c-ln"></colgroup>' +
    '<tr><th></th><th></th><th>Order</th><th>Model</th><th>Status</th><th></th></tr>';
  ids.forEach((id, i) => {
    const v = dcSvc(id, s, li), line = (s.answerLines || {})[id];
    h += '<tr class="dc-ord-row" draggable="true" data-i="' + i + '" ondragstart="dcOrdStart(event, ' + i + ')" ondragend="dcOrdEnd()" ondragover="dcOrdOver(event)" ondrop="dcOrdDrop(event, ' + i + ')">' +
      '<td class="dc-h"><span class="dc-grip" id="dcH-' + esc(id) + '" tabindex="0" role="button" aria-label="Move ' + esc(v.name) + ' (Up and Down arrow keys)" onkeydown="dcOrdKey(event, ' + i + ')">⋮⋮</span></td>' +
      '<td class="dc-n">' + (i + 1) + '</td><td><b>' + esc(v.name) + '</b>' + (v.sub ? ' <span class="lm-muted">' + esc(v.sub) + '</span>' : '') + '</td><td>' + v.model + '</td>' +
      '<td class="dc-st">' + opDot(v.dot) + esc(v.word) + '</td><td class="dc-ln">' + v.link + '</td></tr>' +
      (line ? '<tr class="dc-ord-note"><td></td><td></td><td colspan="4"><div class="vc-warn">' + opDot('warn') + '<span>' + esc(line) + '</span></div></td></tr>' : '');
  });
  return h + '</table>';
}
function dcOrdMove(from, to) {
  const l = (dcSt.settings.decision_order || []).slice();
  if (from === to || from < 0 || to < 0 || from >= l.length || to >= l.length) return;
  const x = l.splice(from, 1)[0];
  l.splice(to, 0, x);
  dcSt.settings.decision_order = l;                    // the numbers follow at once; the server keeps the order
  dcLast = ''; dcRender();
  dcSetting({ decisionOrder: l });
}
function dcOrdStart(ev, i) { dcOrdDrag = i; try { ev.dataTransfer.effectAllowed = 'move'; ev.dataTransfer.setData('text/plain', String(i)); } catch (e) {} }
function dcOrdEnd() { dcOrdDrag = null; }
function dcOrdOver(ev) { if (dcOrdDrag !== null) ev.preventDefault(); }
function dcOrdDrop(ev, i) { ev.preventDefault(); const from = dcOrdDrag; dcOrdDrag = null; if (from !== null) dcOrdMove(from, i); }
function dcOrdKey(ev, i) {
  const to = ev.key === 'ArrowUp' ? i - 1 : ev.key === 'ArrowDown' ? i + 1 : null;
  if (to === null) return;
  ev.preventDefault();
  const l = dcSt.settings.decision_order || [];
  if (to < 0 || to >= l.length) return;
  dcOrdFocus = l[i];                                    // the handle keeps the keyboard focus after the list is drawn again
  dcOrdMove(i, to);
}
// The one sentence and its box: the director's own words. The two numbers are disabled while the box is unticked.
function dcHandOver(set) {
  const on = set.ask_when_unsure !== false;
  return '<label class="dc-sentence"><input type="checkbox" id="dcAskUnsure"' + (on ? ' checked' : '') + ' onchange="dcSetting({ askWhenUnsure: this.checked })"> Try the next model down if the current one takes longer than ' +
    '<input type="text" class="dc-num" id="dcPaidLimit" inputmode="decimal" value="' + esc(String(set.paid_limit_seconds || 6)) + '"' + (on ? '' : ' disabled') + ' onchange="dcSetting({ paidLimitSeconds: this.value })"> seconds or is less than ' +
    '<input type="text" class="dc-num" id="dcSurePct" inputmode="numeric" value="' + esc(String(set.sure_percent || 60)) + '"' + (on ? '' : ' disabled') + ' onchange="dcSetting({ surePercent: this.value })"> % sure.</label>';
}
// the numbered steps at the top of the card; the values SkyrimNet needs sit inside them, each with its Copy button
function dcSkyrimNetSteps(s) {
  const copy = (v) => '<button class="copy" onclick="copyModel(this, \'' + jsq(v) + '\')">Copy</button>';
  const field = (label, v) => '<li class="dc-field"><b>' + label + '</b><input type="text" readonly value="' + esc(v) + '" onfocus="this.select()">' + copy(v) + '</li>';
  const steps = ['Make sure the status says Ready. If it says <b>Stopped</b>, press <b>Start</b>.',
    'In SkyrimNet, go to <b>AI &gt; Providers</b>, press <b>+ Add Custom Provider</b>, and fill in the <b>Name</b>, web address, <b>API Style</b> "System One", and <b>API Key</b>.<ul>' +
      field('Name:', s.providerName) + field('API Endpoint:', s.address) + '<li><b>API Style:</b> System One</li>' + field('API Key:', s.apiKeyWord) + '</ul>',
    'In SkyrimNet, go to <b>AI &gt; Models &gt; Decisions</b>, pick <b>MultiProxy Decisions</b>, and paste the model name.<ul>' + field('Model name:', s.localName) + '</ul>',
    '(Optional) Open the <b>⋯</b> menu to uninstall it, or change when it starts.'];
  return shBox('decisions-skyrimnet', '<div class="vc-sub"><h3>Start here</h3><ol class="dc-steps">' + steps.map(x => '<li>' + x + '</li>').join('') + '</ol>' +
    '<p class="lm-muted" style="margin-top:10px">You can move the model to another device later without changing these SkyrimNet settings. <span class="hp-how"><a onclick="openHelp(\'decisions\')">See Help for more.</a></span></p></div>');
}
function dcMenuHtml(k) {
  const m = dcSelected(), q = jsq(k);
  let h = '<div class="vc-menu" onclick="event.stopPropagation()">';
  { const on = k === 'this' ? !!dcSt.settings.autostart : !!((dcSt.pcs || []).find(x => x.id === k) || {}).autostart;
    h += '<button onclick="dcAuto(' + (on ? 'false' : 'true') + ', \'' + q + '\')">' + (on ? '✓ ' : '') + 'Start when MultiProxy starts</button>'; }
  h += '<button onclick="dcAskLimit()">Waiting limit…</button>';
  h += '<button onclick="dcAskUninstall(\'' + q + '\')">' + (k === 'this' ? 'Uninstall (frees about ' + dcGb(m.diskBytes) + ' GB)' : 'Uninstall') + '</button></div>';
  return h;
}
// The numbered steps shown while no device has the model yet (the Start here box can be hidden, like the others)
function dcStartHere() {
  if (dcDevices().some(d => dcInfo(d.k).installed)) return '';
  const steps = ['Press <b>Set it up</b> on the device you want in the list below.', 'Wait for the install to finish (you can keep using MultiProxy in the meantime).',
    'Follow the new <b>Start here</b> steps that appear on this tab when it finishes.'];
  return shBox('decisions', '<div class="vc-sub" style="margin:10px 0 4px"><h3>Start here</h3><ol class="dc-steps">' + steps.map(x => '<li>' + x + '</li>').join('') + '</ol></div>');
}
// The "Cloud only" box (same words and behaviour as before the list existed): needs a key, says why when it cannot be ticked, and the one-time line when it switched the first choice.
function dcCloudBox(s) {
  const co = !!(s.settings || {}).cloud_only;
  return '<label class="dc-check"><input type="checkbox" id="dcCloudOnly"' + (co ? ' checked' : '') + (!co && !s.cloudKey ? ' disabled' : '') + ' onchange="dcSetting({ cloudOnly: this.checked })"> ' + esc(DC_CLOUD.box) + '</label>' +
    (!co && !s.cloudKey ? '<div class="lm-muted">' + esc((s.cloudOnlyWords || {}).needKey || 'Add a key first.') + '</div>' : '') +
    (dcSwitched ? '<div class="lm-warn" id="dcCloudSwitched" style="margin-top:6px">\u26a0\ufe0f ' + esc(dcSwitched) + '</div>' : '');
}
// "Who answers decisions": the numbered list, the sentence, and what the last decision said about whoever could not answer
// SkyrimNet's Decisions is set to a different model than the one MultiProxy answers to: one amber line (the page's warning style), then the same SkyrimNet steps the device card has
// (a link to them when that card is on this page). Nothing when the names match, when decisions are not set up here, or when SkyrimNet's settings could not be read.
const DC_NAME_WORDS = { line: n => 'SkyrimNet\'s Decisions is set to ' + n + '. MultiProxy is not answering them.', show: 'Show the steps' };
function dcNameWarn(li) {
  if (!dcSt || !dcSt.skyrimnetName) return '';
  const cardShown = li.installed && !(dcSt.settings && dcSt.settings.cloud_only);
  return '<div class="card" id="dcNameWarn"><div class="lm-warn" id="dcNameLine">⚠️ ' + esc(DC_NAME_WORDS.line(dcSt.skyrimnetName)) + '</div>' +
    (cardShown ? '<p class="lm-muted" style="margin:6px 0 0"><a class="lm-link" onclick="dcShowSteps()">' + esc(DC_NAME_WORDS.show) + '</a></p>' : dcSkyrimNetSteps(li)) + '</div>';
}
function dcShowSteps() { const el = document.getElementById('dcCard'); if (el && el.scrollIntoView) { try { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {} } }
function dcWhoCard(li) {
  const set = dcSt.settings;
  return '<h3 style="margin:0 0 6px">Who answers decisions</h3><div class="lm-muted" style="margin-bottom:6px">Drag a row by its handle to change the order. The first one that is ready answers.</div>' + dcOrdTable(dcSt, li) + '<div style="margin-top:14px">' + dcHandOver(set) + '</div>' +
    dcCloudBox(dcSt) + (dcSt.lastAnswer && dcSt.lastAnswer.line ? '<div class="lm-muted" style="margin-top:8px">' + esc(dcSt.lastAnswer.line) + '</div>' : '');
}
function dcRender() {
  const box = document.getElementById('dcBody');
  if (!box) return;
  const ae = document.activeElement;
  if (ae && ae.id && /^dcSel/.test(ae.id)) return;                    // an open drop-down would close if the tab were rebuilt under it
  if (ae && ae.closest && ae.closest('#dcBody') && ae.tagName === 'INPUT') return;
  if (dcOrdDrag !== null) return;                                      // a drag in progress would be cut off by a redraw
  if (!dcSt) { box.innerHTML = dcErr === 'LOAD' ? '<div class="lm-err">Couldn\'t read the local decision model\'s state. Is MultiProxy still running?</div>' : '<div class="lm-muted">Looking at your devices…</div>'; return; }
  const loc = dcSt.location || 'this', li = dcInfo(loc);
  let h = '<div onclick="if (dcMenu) { dcMenu = false; dcRender(); }"><div class="card" id="dcQuick"><h3 style="margin:0 0 4px">Quick decisions</h3>' +
    '<p class="lm-hint">SkyrimNet asks dozens of tiny background questions while you play, such as "who is speaking next?". MultiProxy answers them with a small model on your own device. It never writes dialogue; it only directs the flow of the game.</p>' +
    '<p class="lm-hint">Models: Kev-4B, Laya and Lev, small decision models from ggml-org (the group behind llama.cpp), <a href="https://huggingface.co/ggml-org" target="_blank" rel="noopener">huggingface.co/ggml-org</a>.</p>' +
    '<p class="lm-hint"><span class="hp-how"><a onclick="openHelp(\'decisions\')">How do I set this up?</a></span></p></div>' +
    (dcErr && dcErr !== 'LOAD' ? '<div class="lm-err" style="margin:0 0 10px">' + esc(dcErr) + '</div>' : '') + dcNameWarn(li) +
    '<div class="card" id="dcDevices"><h3 style="margin:0">Where decisions run</h3>' + dcStartHere() + '<div class="dc-devs">' + dcDevices().map(dcRow).join('') + '</div></div>';
  if (li.installed && !(dcSt.settings && dcSt.settings.cloud_only)) h += '<div class="card" id="dcCard">' + dcDeviceCard(loc, li) + '</div>';
  h += '<div class="card" id="dcWho">' + dcWhoCard(li) + '</div></div>';
  if (h !== dcLast) { box.innerHTML = h; dcLast = h; }                 // (the same page is left alone: a focused button keeps its focus)
  if (dcOrdFocus) { const g = document.getElementById('dcH-' + dcOrdFocus); dcOrdFocus = null; if (g) try { g.focus(); } catch (e) {} }
  dcFocusRow();
}
// Every "set it up" or "move" link lands here: the Decisions tab with that device's row in view, flashed once, its button focused
function dcGoRow(k) {
  dcWantRow = k || null;
  if (typeof showMainTab === 'function') showMainTab('decisions');
  if (dcSt) { dcLast = ''; dcRender(); }
}
function dcFocusRow() {
  if (dcWantRow === null || !dcSt) return;
  const el = document.getElementById('dcRow-' + dcSafe(dcWantRow));
  dcWantRow = null;
  if (!el) return;
  try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
  dcFlash = el;
  try { el.classList.add('dc-flash'); } catch (e) {}
  setTimeout(() => { try { if (dcFlash) dcFlash.classList.remove('dc-flash'); } catch (e) {} dcFlash = null; }, 1800);
  try { const b = el.querySelector('button:not([disabled])'); if (b && b.focus) b.focus(); } catch (e) {}
}

// ---- actions ----
async function dcDo(fn) {
  dcErr = '';
  try { await fn(); } catch (e) { dcErr = e.message; }
  await dcLoad();
}
// One click: the install starts on that device at once with the recommended model that fits, and the tab stays where it is. The decisions move there only when it is ready.
async function dcSetUp(k) {
  delete dcFail[k]; delete dcCancelled[k]; dcSamples[k] = []; dcNote = ''; dcErr = '';
  try { await lmPost('/config/decisions/install', { pc: k, source: dcSource, recommended: true }); } catch (e) { dcFail[k] = e.message; }
  await dcLoad();
}
function dcGetRunner(k) { dcNote = ''; dcDo(() => lmPost('/config/decisions/install', { pc: k, source: dcSource, runnerOnly: true })); }
function dcCancel(k) { dcCancelled[k] = true; delete dcPaused[k]; dcDo(() => lmPost('/config/decisions/cancel', { pc: k })); }
function dcPause(k) { if (k !== 'this') dcPaused[k] = true; dcDo(() => lmPost('/config/decisions/pause', { pc: k })); }
function dcResume(k) { delete dcPaused[k]; dcSetUp(k); }
function dcStopPause(k) { delete dcPaused[k]; dcCancelled[k] = true; dcDo(() => lmPost('/config/decisions/cancel', { pc: k })); }
function dcAct(k, what) { dcNote = ''; dcMenu = false; dcRestart = false; dcDo(() => lmPost('/config/decisions/' + what, { pc: k })); }
function dcSetting(change) {
  dcMenu = false;
  dcSwitched = '';
  dcDo(async () => {
    const d = await lmPost('/config/decisions/settings', change);
    if (d.cloudOnlySwitched) dcSwitched = d.cloudOnlySwitched;
    if (d.restartNeeded) dcRestart = true;
    dcFits = {};
  });
}
// "Use it": the decisions go to a device that already has the model installed
function dcUse(k) { dcMenu = false; dcDo(async () => { delete dcFail[k]; const d = await lmPost('/config/decisions/location', { pc: k }); if (d && d.moveWarning) dcFail[d.moveWarningPc] = d.moveWarning; }); }        // the copy they came from is stopped by the server; if that failed, its row says so
function dcToggleMenu(ev) { ev.stopPropagation(); dcMenu = !dcMenu; dcLast = ''; dcRender(); }
function dcAuto(on, k) { dcMenu = false; dcDo(() => lmPost('/config/decisions/settings', k && k !== 'this' ? { autostartPc: { pc: k, on } } : { autostart: on })); }

// ---- dialogs: waiting limit, uninstall, the log ----
function dcCloseModal() { dcModal = null; dcModalDraw(); }
function dcAskLimit() { dcMenu = false; dcModal = { type: 'limit', error: '' }; dcModalDraw(); }
function dcAskUninstall(k) { dcMenu = false; dcModal = { type: 'uninstall', pc: k || 'this' }; dcModalDraw(); }
async function dcSaveLimit(useUsual) {
  const m = dcModal; if (!m) return;
  let v = 0;
  if (!useUsual) {
    const raw = (document.getElementById('dcLimitBox').value || '').trim();
    if (raw !== '') { v = parseFloat(raw.replace(',', '.')); if (!(v >= 0)) { m.error = 'Enter a number of seconds.'; dcModalDraw(); return; } }
  }
  try { await lmPost('/config/decisions/settings', { limitSeconds: v }); } catch (e) { m.error = e.message; dcModalDraw(); return; }
  dcCloseModal(); await dcLoad();
}
async function dcConfirmUninstall() {
  const pc = (dcModal && dcModal.pc) || 'this';
  dcCloseModal(); dcNote = '';
  await dcDo(async () => { const d = await lmPost('/config/decisions/uninstall', { pc }); dcFits = {}; });
}
async function dcOpenLog(k) {
  dcModal = { type: 'log', text: null, error: '' }; dcModalDraw();
  try {
    const r = await fetch('/config/decisions/log' + (k && k !== 'this' ? '?pc=' + encodeURIComponent(k) : '')); const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "Couldn't read that log.");
    if (dcModal && dcModal.type === 'log') dcModal.text = d.log || '';
  } catch (e) { if (dcModal && dcModal.type === 'log') dcModal.error = e.message; }
  dcModalDraw();
  const pre = document.getElementById('dcLogText'); if (pre) pre.scrollTop = pre.scrollHeight;
}
function dcCopyLog(btn) {
  try { navigator.clipboard.writeText((dcModal && dcModal.text) || ''); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}
function dcModalDraw() {
  const box = document.getElementById('dcModalBox');
  if (!box) return;
  const m = dcModal;
  if (!m) { box.style.display = 'none'; box.innerHTML = ''; return; }
  const set = (dcSt && dcSt.settings) || {};
  let h;
  if (m.type === 'limit') {
    const usual = dcSt && dcSt.runOn === 'cpu' ? 6 : 0.8;
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" aria-labelledby="dcLimTitle" tabindex="-1" id="dcDialog"><h3 id="dcLimTitle">Waiting limit</h3>' +
      '<p>Skip the local model if it hasn\'t answered in</p>' +
      '<div class="dc-field"><input type="text" class="dc-num" id="dcLimitBox" inputmode="decimal" value="' + (set.limit_seconds > 0 ? esc(String(set.limit_seconds)) : '') + '" placeholder="' + usual + '" onkeydown="if (event.key === \'Enter\') dcSaveLimit(false)"> <span>seconds</span></div>' +
      '<p class="lm-muted" style="margin:0">The usual is 0.8 seconds on a graphics card, or 6 seconds on system memory. A slower device might need more time. If skipped, your next available provider will answer.</p>' +
      (m.error ? '<div class="lm-err">' + esc(m.error) + '</div>' : '') +
      '<div class="vc-ft"><button class="ghost" onclick="dcCloseModal()">Cancel</button><button class="ghost" onclick="dcSaveLimit(true)">Use the usual</button><button class="primary" onclick="dcSaveLimit(false)">Save</button></div></div>';
  } else if (m.type === 'uninstall') {
    const kept = dcSt && dcSt.downloads ? dcSt.downloads.totalBytes : 0;
    const there = m.pc && m.pc !== 'this' ? dcShowPc(m.pc) : '';
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="dcDialog"><h3>Uninstall the local decision model' + (there ? ' from ' + esc(there) : '') + '?</h3>' +
      (there ? '<p>This removes the model from ' + esc(there) + '. The copied files are kept on that device, so setting it up again later is quick. Your other providers are not affected.</p>'
        : '<p>This removes the model from this device. The downloaded files are kept' + (kept ? ' (' + dcGb(kept) + ' GB)' : '') + ', so installing it again later is quick. Your other providers are not affected.</p>') +
      '<div class="vc-ft"><button class="ghost" onclick="dcCloseModal()">Cancel</button><button class="primary" onclick="dcConfirmUninstall()">Uninstall</button></div></div>';
  } else {
    const body = m.error ? '<div class="lm-err">' + esc(m.error) + '</div>' : m.text == null ? '<div class="lm-muted">Reading the log…</div>' :
      !m.text.trim() ? '<div class="lm-muted">Nothing in the log yet.</div>' : '<pre class="op-log" id="dcLogText">' + esc(m.text) + '</pre>';
    h = '<div class="op-modal-box" role="dialog" aria-modal="true" tabindex="-1" id="dcDialog"><h3>Local decision model log</h3>' + body +
      '<div class="vc-ft"><button onclick="dcCopyLog(this)">Copy</button><button class="primary" onclick="dcCloseModal()">Close</button></div></div>';
  }
  box.innerHTML = h;
  box.style.display = 'flex';
  const focus = document.getElementById(m.type === 'limit' ? 'dcLimitBox' : 'dcDialog');
  if (focus && focus.focus) focus.focus();
}
document.addEventListener('keydown', e => { if (e.key === 'Escape' && dcModal) dcCloseModal(); });

// ---- the decision model's short line on a device's card under Other devices (other_pcs_ui.py calls this, as it does the voice row): the state and a link to the Decisions tab ----
function dcPcRow(r) {
  const rep = r.report || {}, d = rep.decisions;
  if (!d) return '';
  const id = r.id, j = d.install;
  let dot, text;
  if (typeof opUnBusy !== 'undefined' && opUnBusy[opUnKey(id, 'decisions')]) { dot = 'warn'; text = 'Uninstalling'; }
  else if (typeof dcSt !== 'undefined' && dcSt && dcSt.settings && dcSt.settings.cloud_only) { dot = 'idle'; text = 'not used'; }
  else if (j && j.state === 'running') { dot = 'warn'; text = 'installing'; }
  else if (!d.installed) { dot = 'none'; text = 'not installed'; }
  else if (typeof dcSt !== 'undefined' && dcSt && (dcSt.location || 'this') !== id) { dot = 'idle'; text = 'installed, not in use'; }
  else if (d.state === 'ready') { dot = 'on'; text = 'running'; }
  else if (d.state === 'starting' || d.state === 'getting_ready') { dot = 'warn'; text = String(d.label || 'starting').toLowerCase(); }
  else if (d.runnerMissing) { dot = 'warn'; text = 'needs the engine'; }
  else { dot = d.state === 'stopped' ? 'idle' : 'off'; text = String(d.label || 'stopped').toLowerCase(); }
  return '<div class="op-prog">' + opDot(dot, d.label || '') + '<b>Local decision model</b> <span class="lm-muted">' + esc(text) + '</span><span class="op-grow"></span>' +
    '<a class="lm-link" onclick="dcGoRow(\'' + jsq(id) + '\')">Open Decisions</a>' +
    (d.installed && text !== 'Uninstalling' && !(j && j.state === 'running') ? '<button class="ghost" onclick="opAskUninstall(\'' + jsq(id) + '\', \'decisions\')">Uninstall</button>' : '') + '</div>';
}
"""
