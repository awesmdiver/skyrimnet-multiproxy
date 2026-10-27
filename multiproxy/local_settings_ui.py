"""The Local models tab's settings screens (2026-10-04), built to design/local-model-settings-mockup.html (revision 2 and its "Gemini review applied" block).

Four things, all in the `ls-` family and all drawn into one mount point under the model list (`#lsMount`), so the model list's own redraw (every few seconds) never
wipes what the player is typing:

  * the "engine did not start" card (the engine's own words, "Start without the extra flags", "Edit the flags") for every model that failed;
  * **This device**: the presets, graphics card layers, memory to keep free for the game, memory per conversation, requests at the same time, speed boost, smaller
    cache, the graphics-memory bar and Save / Save and restart running models; then **Advanced: your own flags** (paste a command line or type flags, what was
    understood / kept / not used / not available, "The command MultiProxy will run");
  * a **per-model panel** (memory per conversation and requests at the same time only), opened from "Change for this model" on a model's row;
  (The "Decision engine: Managed by MultiProxy" note moved to the Decisions tab's card, 2026-10-23.)

There is NO settings logic here. Everything comes from the API (`GET /config/local-models/settings`, `POST .../settings`, `/parse`, `/estimate`, `/failure`,
`/start-without-extras`; local_engine_api.py): the presets and their values, which checks apply, which box is greyed out (`fit-target-ignored`), the estimate, the preset
a set of values is, the command. The page only draws that and sends the player's edits back. Every builder below is a pure function of its inputs (no document, no
fetch), so tests/test_local_settings_ui.py runs them in Node. Everything from the server goes through esc() before it reaches HTML.

Only this device's card is mounted for now (`LS_DEVICE`); the card itself takes any device and shows "Waiting to send" for one whose settings have not reached it.
"""

LOCAL_SETTINGS_CSS = r"""
  /* Local model settings (2026-10-04) -- ls-* classes; the page's cards, ghost buttons and theme variables are reused. Text uses the card's full width. */
  .ls-card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px 22px; margin:14px 0 }
  .ls-head { display:flex; align-items:center; gap:12px; flex-wrap:wrap }
  .ls-head h3 { margin:0; font-size:1.05rem }
  .ls-sub { font-weight:400; color:var(--muted); font-size:0.85rem }
  .ls-grow { flex:1 }
  .ls-st { display:inline-flex; align-items:center; gap:7px; white-space:nowrap; font-size:0.85rem }
  .ls-dot { width:9px; height:9px; border-radius:50%; flex:none; background:var(--ok) }
  .ls-dot.warn { background:var(--warn); animation:lsPulse 2.4s ease-in-out infinite } .ls-dot.err { background:var(--err); animation:lsPulse 2.4s ease-in-out infinite }
  @keyframes lsPulse { 50% { opacity:.35 } } @media (prefers-reduced-motion: reduce) { .ls-dot.warn, .ls-dot.err { animation:none } }
  .ls-pill { border:1px solid var(--line); border-radius:999px; padding:1px 10px; font-size:0.78rem; color:var(--muted); white-space:nowrap }
  .ls-pill.on { border-color:var(--accent); color:var(--text) } .ls-pill.ls-amber { border-color:var(--warn); color:var(--warn) }
  .ls-note { color:var(--muted); font-size:0.85rem; border-left:2px solid var(--line); padding-left:12px; margin:10px 0 }
  .ls-presets { display:flex; gap:10px; flex-wrap:wrap; margin:12px 0 6px }
  .ls-preset { flex:1; min-width:200px; border:1px solid var(--line); border-radius:10px; padding:10px 14px; background:var(--bg); cursor:pointer; text-align:left; font:inherit; color:var(--text) }
  .ls-preset:hover, .ls-preset:active { background:var(--bg); border-color:var(--muted) }
  .ls-preset.sel { border-color:var(--accent) } .ls-preset b { display:block } .ls-preset span { color:var(--muted); font-size:0.82rem; display:block }
  .ls-preset.ls-dim { opacity:.55; cursor:default } .ls-preset.ls-dim:hover { border-color:var(--line) }
  .ls-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px 28px; margin:16px 0 }
  @media (max-width:760px) { .ls-grid { grid-template-columns:1fr } }
  #lsDeviceCard { max-width:1100px }   /* this card only (director, 2026-10-06): two columns under about 1,100 px; text still fills the width its column has */
  .ls-grid > .ls-coltitle { grid-column:1 / -1; margin:0; color:var(--muted); font-size:0.72rem; letter-spacing:.08em; text-transform:uppercase; border-bottom:1px solid var(--line); padding-bottom:6px }
  .ls-f label { display:block; font-weight:600; margin:0 0 5px }
  .ls-f .ls-why { color:var(--muted); font-size:0.8rem; margin:5px 0 0 }
  .ls-flag { color:var(--muted); opacity:.75; font-family:Consolas, monospace; font-size:0.7rem; font-weight:400; margin-left:8px }
  .ls-ctl.ls-one { flex-wrap:nowrap }
  .ls-ctl { display:flex; gap:10px; align-items:center; flex-wrap:wrap }
  .ls-ctl input[type=number], .ls-ctl select, .ls-text { font:inherit; background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 10px }
  .ls-ctl input[type=number] { width:110px } .ls-ctl input[type=range] { flex:1; min-width:120px; padding:0 }
  .ls-f.ls-dim label, .ls-f.ls-dim .ls-why, .ls-f.ls-dim .ls-ctl { opacity:.8 } .ls-f.ls-dim .ls-flag { opacity:1 }
  .ls-reason { color:var(--muted); font-size:0.82rem; margin-top:6px }
  .ls-text { width:100%; min-height:110px; font-family:Consolas, monospace; font-size:0.85rem; box-sizing:border-box }
  .ls-est { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:6px 0 4px; font-size:0.9rem }
  .ls-bar { flex:1; min-width:120px; height:8px; border-radius:6px; background:var(--panel); border:1px solid var(--line); overflow:hidden } .ls-bar i { display:block; height:100% }
  .ls-callout { border:1px solid; border-radius:8px; padding:10px 14px; margin:12px 0; font-size:0.9rem }
  .ls-callout.ok { border-color:var(--ok); background:color-mix(in srgb, var(--ok) 10%, var(--bg)) }
  .ls-callout.info { border-color:var(--info, #60a5fa); background:color-mix(in srgb, var(--info, #60a5fa) 10%, var(--bg)) }
  .ls-callout.warn { border-color:var(--warn); background:color-mix(in srgb, var(--warn) 12%, var(--bg)) }
  .ls-callout.err { border-color:var(--err); background:color-mix(in srgb, var(--err) 12%, var(--bg)) }
  .ls-callout code, .ls-pre { font-family:Consolas, monospace; font-size:0.82rem; color:inherit }
  .ls-callout button { margin-left:8px }
  .ls-callout pre { margin:6px 0; white-space:pre-wrap; overflow-wrap:anywhere }
  .ls-nav { display:flex; justify-content:flex-end; gap:10px; margin-top:16px; align-items:center; flex-wrap:wrap }
  .ls-nav .ls-left { margin-right:auto; color:var(--muted); font-size:0.85rem }
  .ls-pre { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:8px 0; white-space:pre-wrap; overflow-wrap:anywhere }
  .ls-details summary { color:var(--muted); cursor:pointer; font-size:0.9rem; margin-top:12px }
  .ls-muted { color:var(--muted); font-size:0.85rem }
  .ls-panel { margin-top:16px; border-top:1px solid var(--line); padding-top:16px }
"""

LOCAL_SETTINGS_JS = r"""// ---------------- Local model settings ----------------
// See local_settings_ui.py. Builders (ls*Html) are pure; the rest talks to /config/local-models/settings.
const LS_DEVICE = 'local';
let lsState = null;          // GET /config/local-models/settings
let lsDraft = null;          // the player's edits to the device settings (not saved yet)
let lsRaw = '';              // the Advanced box's text, exactly as typed
let lsParse = null;          // what /parse made of it
let lsUndo = null;           // the draft before a pasted line filled it in
let lsEst = null;            // /estimate for the draft
let lsModelEst = {};         // model id -> /estimate for that model (its panel's checks)
let lsOpen = {};             // model id -> {ctx, parallel} while its panel is open
let lsDirty = false;
let lsNote = '';             // what the last save said
let lsErr = null;            // a refused save: {text, checks}
let lsChecking = false;      // the engine's own memory check is running
let lsFails = {};            // model id -> /failure
let lsTimer = null;
const LS_HANDLED = ['fit-target-ignored', 'extra-flags-untested', 'experimental-hardware'];
const LS_FLAG_WORDS = { layers: '--n-gpu-layers', ctx: '--ctx-size', parallel: '--parallel' };

function lsGb(mb) { return (mb / 1024).toFixed(mb % 1024 ? 1 : 0).replace(/\.0$/, ''); }
function lsPages(tokens, st) { return Math.max(1, Math.round(tokens / ((st && st.pageTokens) || 680))); }
function lsDraftBase(st) { const s = Object.assign({}, st.settings); delete s.preset; return s; }
function lsFlagLines(tokens) {
  const lines = [];
  for (const t of (tokens || [])) {
    if (/^-/.test(t) && !/^-\d/.test(t) || !lines.length) lines.push(t); else lines[lines.length - 1] += ' ' + t;
  }
  return lines.join('\n');
}
function lsFitDisabled(S) { return ((S.est && S.est.checks) || (S.state && S.state.checks) || []).some(c => c.code === 'fit-target-ignored'); }

function lsCalloutHtml(kind, inner) { return '<div class="ls-callout ' + kind + '">' + inner + '</div>'; }
function lsCheckHtml(c) {
  if (LS_HANDLED.indexOf(c.code) >= 0) return '';
  if (c.level === 'error') return lsCalloutHtml('err', '🛑 ' + esc(c.text));
  if (c.level === 'warning') return lsCalloutHtml('warn', '⚠️ ' + (c.strong ? '<b>' + esc(c.text) + '</b>' : esc(c.text)));
  return '<div class="ls-reason">' + esc(c.text) + '</div>';
}
function lsChecksHtml(checks) { return (checks || []).map(lsCheckHtml).join(''); }

// ---- the graphics-memory line ----
function lsEstHtml(est, checking) {
  const label = '<span class="ls-muted">Graphics memory with your busiest model (estimate)</span>';
  if (checking) return label + '<span class="ls-muted">Checking memory...</span>';
  if (!est || est.totalMb == null || !est.summary) return label + '<span class="ls-muted">Add a model to see this.</span>';
  const pct = est.cardMb ? Math.min(100, Math.round(100 * est.totalMb / est.cardMb)) : 0;
  const over = est.cardMb && est.totalMb > est.cardMb;
  return label + '<div class="ls-bar"><i style="width:' + pct + '%;background:var(' + (over ? '--warn' : '--ok') + ')"></i></div><b>' + esc(est.summary) + '</b> <span class="ls-muted">(' +
    (est.checked ? esc(est.exactNote) : 'the cache is exact, the rest is an estimate') + ')</span>';
}

// ---- the presets ----
function lsPresetsHtml(st, selected) {
  return (st.presets || []).map(p => {
    const can = p.enabled && (p.settings || p.id === 'custom');
    const click = can && p.id !== 'custom' ? ' onclick="lsPickPreset(\'' + jsq(p.id) + '\')"' : '';
    return '<button type="button" class="ls-preset' + (selected === p.id ? ' sel' : '') + (can ? '' : ' ls-dim') + '"' + click + (can ? '' : ' disabled') + '><b>' + esc(p.label) + '</b><span>' + esc(p.blurb) + '</span>' +
      (p.enabled ? '' : '<span>' + esc(p.reason) + '</span>') + '</button>';
  }).join('');
}

// ---- the boxes ----
function lsLayersHtml(S) {
  const d = S.draft, num = typeof d.layers === 'number';
  const sel = num ? 'number' : d.layers;
  const opt = (v, t) => '<option value="' + v + '"' + (sel === v ? ' selected' : '') + '>' + t + '</option>';
  return '<div class="ls-f"><label>Graphics card layers <span class="ls-flag">--n-gpu-layers</span></label>' +
    '<div class="ls-ctl"><select id="lsLayers" onchange="lsSetLayers(this.value)">' + opt('auto', 'Automatic (recommended)') + opt('all', 'All') + opt('number', 'Pick a number') + opt('none', 'None (processor only)') + '</select>' +
    (num ? '<input type="number" id="lsLayersNum" min="1" value="' + d.layers + '" oninput="lsSetLayersNum(this.value)">' : '') + '</div>' +
    '<div class="ls-why">Automatic fits the model into your free space.</div></div>';
}
function lsFitHelp(S, off) {
  const st = S.state, d = S.draft;
  if (off) return 'Not used while a layer count is picked.';
  if (d.fitTargetMb !== 'auto') return 'Set by you. Needs Automatic layers.';
  if (st.hasGame && st.skyrimPeakMb) return 'Leaves room for Skyrim (it peaked at ' + lsGb(st.skyrimPeakMb) + ' GB). Needs Automatic layers.';
  if (st.hasGame) return 'Leaves room for Skyrim (a guess until the game has been measured). Needs Automatic layers.';
  return 'No game here, so it keeps 1 GB. Needs Automatic layers.';
}
function lsFitHtml(S) {
  const st = S.state, d = S.draft, off = lsFitDisabled(S);
  const mb = d.fitTargetMb === 'auto' ? st.fitTarget.mb : d.fitTargetMb;
  const max = Math.max(4, Math.ceil(((st.hardware && st.hardware.memoryMb) || 8192) / 1024));
  const dis = off ? ' disabled' : '';
  return '<div class="ls-f' + (off ? ' ls-dim' : '') + '" id="lsFitBox"><label>Memory to keep free for the game <span class="ls-flag">--fit-target</span></label>' +
    '<div class="ls-ctl ls-one"><input type="number" id="lsFit" min="0" max="' + max + '" step="0.5" value="' + lsGb(mb) + '" style="width:80px" oninput="lsSetFit(this.value)"' + dis + '> <span class="ls-muted">GB</span>' +
    '<input type="range" id="lsFitRange" min="0" max="' + max + '" step="0.5" value="' + lsGb(mb) + '" oninput="lsSetFit(this.value, true)"' + dis + '>' +
    (d.fitTargetMb !== 'auto' && !off ? ' <a class="lm-link" onclick="lsFitAuto()">Use the worked-out value</a>' : '') + '</div>' +
    '<div class="ls-why" id="lsFitWhy">' + esc(lsFitHelp(S, off)) + '</div></div>';
}
function lsShareText(S) {
  const d = S.draft, each = Math.floor(d.ctx / Math.max(1, d.parallel)), pages = lsPages(each, S.state);
  return d.parallel > 1 ? 'Shared: each of the ' + d.parallel + ' requests gets about ' + pages + ' pages.' : 'One request gets all of it: about ' + pages + ' pages.';
}
function lsCtxHtml(S) {
  const st = S.state, d = S.draft;
  return '<div class="ls-f"><label>Memory per conversation <span class="ls-flag">--ctx-size</span></label>' +
    '<div class="ls-ctl"><input type="number" id="lsCtx" min="256" step="256" value="' + d.ctx + '" oninput="lsSetNum(\'ctx\', this.value)"> <span class="ls-muted" id="lsCtxPages">tokens (about ' + lsPages(d.ctx, st) + ' pages)</span></div>' +
    '<div class="ls-why" id="lsCtxShare">' + esc(lsShareText(S)) + '</div></div>';
}
function lsParallelHtml(S) {
  return '<div class="ls-f"><label>Requests at the same time <span class="ls-flag">--parallel</span></label>' +
    '<div class="ls-ctl"><input type="number" id="lsPar" min="1" value="' + S.draft.parallel + '" oninput="lsSetNum(\'parallel\', this.value)"></div>' +
    '<div class="ls-why">1 is fastest. More lets several NPCs talk at once.</div></div>';
}
function lsFlashHelp(d) { return d.cacheType !== 'f16' ? 'Stays on while the smaller cache is used.' : 'Usually faster and lighter.'; }
function lsFlashHtml(S) {
  const d = S.draft, smaller = d.cacheType !== 'f16';
  const opt = (v, t, dis) => '<option value="' + v + '"' + (d.flashAttn === v ? ' selected' : '') + (dis ? ' disabled' : '') + '>' + t + '</option>';
  return '<div class="ls-f"><label>Speed boost for long prompts <span class="ls-flag">--flash-attn</span></label>' +
    '<div class="ls-ctl"><select id="lsFlash" onchange="lsSetSel(\'flashAttn\', this.value)">' + opt('auto', 'Auto (recommended)') + opt('on', 'On') + opt('off', 'Off (only with the full-size cache)', smaller) + '</select></div>' +
    '<div class="ls-why" id="lsFlashWhy">' + esc(lsFlashHelp(d)) + '</div></div>';
}
function lsCacheHtml(S) {
  const d = S.draft;
  const opt = (v, t) => '<option value="' + v + '"' + (d.cacheType === v ? ' selected' : '') + '>' + t + '</option>';
  return '<div class="ls-f"><label>Smaller cache <span class="ls-flag">--cache-type-k / -v</span></label>' +
    '<div class="ls-ctl"><select id="lsCache" onchange="lsSetSel(\'cacheType\', this.value)">' + opt('q8_0', 'q8_0 (recommended)') + opt('f16', 'f16 (full size)') + opt('q4_0', 'q4_0 (smallest)') + '</select></div>' +
    '<div class="ls-why">q8_0 halves the cache memory with almost no loss in quality.</div></div>';
}

// ---- messages under the boxes ----
function lsMessagesHtml(S) {
  let h = '';
  if (S.err) h += lsCalloutHtml('err', '🛑 ' + esc(S.err.text));
  const shown = S.err && S.err.checks && S.err.checks.length ? S.err.checks : ((S.est && S.est.checks) || []);
  h += lsChecksHtml(shown.filter(c => !(S.err && c.level === 'error' && c.text === S.err.text)));
  if (S.note) {
    h += lsCalloutHtml('info', esc(S.note) + (S.restartOffer ? '<button type="button" onclick="lsSave(\'restart-all\')">Restart running models now</button>' : ''));
  }
  return h;
}
function lsResetNoteHtml(st) {
  if (!st.reset || !st.reset.length) return '';
  const items = st.reset.map(r => '<li>' + esc(r.was) + ' (' + esc(String(r.reason || '').replace(/\.$/, '')) + ')</li>').join('');
  return lsCalloutHtml('warn', '⚠️ The engine was updated, so ' + (st.reset.length === 1 ? '1 of your settings went back to its default' : st.reset.length + ' of your settings went back to their defaults') + ':<ul style="margin:6px 0 0">' + items + '</ul>');
}

// ---- This device ----
function lsDeviceHtml(S) {
  const st = S.state, hw = st.hardware || {};
  const card = hw.name ? ' · ' + esc(hw.name) + (hw.memoryGb ? ' · ' + esc(String(hw.memoryGb)) + ' GB graphics memory' : '') : '';
  const pending = S.restartOffer || false;
  const bad = ((S.est && S.est.checks) || []).some(c => c.level === 'error');
  const dot = bad ? 'err' : (pending || ((S.est && S.est.checks) || []).some(c => c.level === 'warning') ? 'warn' : '');
  const waiting = st.sync && st.sync.needed && !st.sync.sent;
  let h = '<div class="ls-card" id="lsDeviceCard"><div class="ls-head"><span class="ls-dot ' + dot + '"></span><h3>This device <span class="ls-sub">· ' + esc(st.deviceName || '') + card + '</span></h3>' +
    (hw.experimental ? '<span class="ls-pill ls-amber" title="Experimental">Experimental</span>' : '') +
    (waiting ? '<span class="ls-st"><span class="ls-dot warn"></span>Waiting to send</span>' : '') +
    '<span class="ls-grow"></span><button type="button" class="ghost" onclick="lsResetSuggested()">Reset to suggested</button></div>' +
    '<div class="ls-note">These settings apply to every local model on this device, unless you change them for a specific model below.</div>';
  if (waiting) h += '<div class="ls-reason">' + (st.sync.online === false ? 'This device is offline. ' : '') + 'Saved here. It is sent when the device can take it.</div>';
  const exp = (st.checks || []).find(c => c.code === 'experimental-hardware');
  if (exp) h += lsCalloutHtml('info', esc(exp.text));
  h += lsResetNoteHtml(st);
  h += '<div class="ls-presets" id="lsPresets">' + lsPresetsHtml(st, S.preset) + '</div>';
  h += '<div class="ls-grid"><div class="ls-coltitle">Fit in graphics memory</div>' + lsLayersHtml(S) + lsParallelHtml(S) + lsFitHtml(S) + lsCtxHtml(S) + lsCacheHtml(S) + lsFlashHtml(S) + '</div>';
  h += '<div class="ls-est" id="lsEst">' + lsEstHtml(S.est, S.checking) + '</div>';
  h += '<div id="lsMsgs">' + lsMessagesHtml(S) + '</div>';
  h += '<div class="ls-nav"><span class="ls-left" id="lsApplies">' + (S.note && !S.restartOffer ? 'Saved.' : (S.restartOffer ? 'Saved.' : 'Takes effect the next time a model starts.')) + '</span>' +
    '<button type="button" class="ghost" id="lsSave" onclick="lsSave(\'save\')"' + (S.dirty ? '' : ' disabled') + '>Save</button>' +
    '<button type="button" id="lsSaveRestart" onclick="lsSave(\'restart-all\')">Save and restart running models</button></div></div>';
  return h;
}

// ---- Advanced: your own flags ----
function lsFlagList(items) { return items.map(i => '<code>' + esc(i) + '</code>').join(', '); }
function lsParseHtml(p, canUndo) {
  if (!p) return '';
  let h = '';
  const labels = (p.understood || []).map(u => u.label);
  if (labels.length) h += lsCalloutHtml('ok', '<b>Understood and filled in above:</b> ' + esc(labels.join(', ')) + '.' + (canUndo ? '<button type="button" onclick="lsUndoParse()">Undo</button>' : ''));
  const extras = (p.extraItems || []).map(i => (i.flag + (i.value ? ' ' + i.value : '')));
  if (extras.length) h += lsCalloutHtml('info', '<b>Kept as extra flags (MultiProxy has not tested these):</b> ' + extras.map(e => '<code>' + esc(e) + '</code>').join(' · ') + '. They are passed to llama-server as they are.');
  const managed = (p.refused || []).filter(r => /sets this one itself|has its own choice|chooses how much/.test(r.reason));
  const other = (p.refused || []).filter(r => managed.indexOf(r) < 0);
  const gone = p.unavailable || [], bad = p.invalid || [];
  let w = '';
  if (managed.length) w += '<b>Not used (MultiProxy sets these itself):</b> ' + lsFlagList(managed.map(r => r.flag)) + '. ';
  if (gone.length) w += '<b>Not available in this version of llama.cpp:</b> ' + gone.map(g => '<code>' + esc(g.flag) + '</code> (' + esc(String(g.reason || '').replace(/\.$/, '').replace(/^./, c => c.toLowerCase())) + (g.replacement ? '; use <code>' + esc(g.replacement) + '</code> instead' : '') + ')').join(', ') + '. ';
  if (other.length) w += '<b>Not allowed:</b> ' + other.map(r => '<code>' + esc(r.flag) + '</code> (' + esc(String(r.reason || '').replace(/\.$/, '').replace(/^./, c => c.toLowerCase())) + ')').join(', ') + '. ';
  if (bad.length) w += '<b>Not understood:</b> ' + bad.map(r => '<code>' + esc(r.flag) + '</code> (' + esc(String(r.reason || '').replace(/\.$/, '').replace(/^./, c => c.toLowerCase())) + ')').join(', ') + '.';
  if (w) h += lsCalloutHtml('warn', '⚠️ ' + w);
  return h;
}
function lsAdvancedHtml(S) {
  const st = S.state;
  return '<div class="ls-card" id="lsAdvCard"><div class="ls-head"><h3>Advanced: your own flags <span class="ls-sub">· this device</span></h3></div>' +
    '<p class="ls-note">If the boxes above don\'t cover what you need, pass extra commands to llama-server here. You can also paste a command line from someone else. MultiProxy uses your exact text.</p>' +
    '<textarea class="ls-text" id="lsRaw" placeholder="Paste llama-server flags here to fill the boxes above." oninput="lsRawInput(this.value)">' + esc(S.raw) + '</textarea>' +
    '<div id="lsParseRes">' + lsParseHtml(S.parse, !!S.undo) + '</div>' +
    '<details class="ls-details"><summary>The command MultiProxy will run</summary><div class="ls-pre" id="lsCmd">' + esc((S.est && S.est.command) || '') + '</div>' +
    '<button type="button" class="ghost" onclick="lsCopyCommand(this)">Copy command</button></details>' +
    '<div class="ls-nav"><span class="ls-left">Takes effect the next time a model starts.</span><button type="button" class="ghost" onclick="lsClearFlags()">Clear</button>' +
    '<button type="button" class="ghost" onclick="lsSave(\'save\')">Save</button><button type="button" onclick="lsSave(\'restart-all\')">Save and restart running models</button></div></div>';
}

// ---- the per-model panel ----
function lsModelTagText(m) {
  const n = (m.overridden || []).length;
  return n ? n + (n === 1 ? ' setting changed for this model' : ' settings changed for this model') : "Uses this device's settings";
}
function lsModelPanelHtml(S, m) {
  const st = S.state, o = S.open[m.id] || {};
  const ctx = o.ctx != null ? o.ctx : (m.override && m.override.ctx) || S.draft.ctx;
  const par = o.parallel != null ? o.parallel : (m.override && m.override.parallel) || S.draft.parallel;
  const each = Math.floor(ctx / Math.max(1, par));
  const share = par > 1 ? 'Shared by the ' + par + ' requests at the same time: each gets about ' + lsPages(each, st) + ' pages.'
                        : 'One request at a time gets all of it: about ' + lsPages(each, st) + ' pages.';
  const checks = (S.modelChecks && S.modelChecks[m.id]) || m.checks || [];
  return '<div class="ls-card" id="lsPanel-' + esc(m.id) + '"><div class="ls-head"><h3>' + esc(m.name) + '</h3><span class="ls-pill' + ((m.overridden || []).length ? ' on' : '') + '" id="lsTag-' + esc(m.id) + '">' + esc(lsModelTagText(m)) + '</span><span class="ls-grow"></span>' +
    '<button type="button" class="ghost" onclick="lsCloseModel(\'' + jsq(m.id) + '\')">Close</button></div>' +
    '<div class="ls-panel"><div class="ls-note" style="margin-top:0">Only what really depends on this model. Everything else uses this device\'s settings.</div>' +
    '<div class="ls-grid"><div class="ls-f"><label>Memory per conversation <span class="ls-flag">--ctx-size</span></label>' +
    '<div class="ls-why">How much text it can hold in mind at once. Bigger uses more graphics memory. (This device\'s default: about ' + lsPages(S.draft.ctx, st) + ' pages)</div>' +
    '<div class="ls-ctl"><input type="number" min="256" step="256" id="lsMCtx-' + esc(m.id) + '" value="' + ctx + '" oninput="lsSetModel(\'' + jsq(m.id) + '\', \'ctx\', this.value)"> <span class="ls-muted" id="lsMPages-' + esc(m.id) + '">tokens, about ' + lsPages(ctx, st) + ' pages of text</span></div>' +
    '<div class="ls-why" style="margin-top:6px" id="lsMShare-' + esc(m.id) + '">' + esc(share) + '</div></div>' +
    '<div class="ls-f"><label>Requests at the same time <span class="ls-flag">--parallel</span></label>' +
    '<div class="ls-why" id="lsMWhy-' + esc(m.id) + '">This model answers up to ' + par + ' at a time. (This device\'s default: ' + S.draft.parallel + ')</div>' +
    '<div class="ls-ctl"><input type="number" min="1" id="lsMPar-' + esc(m.id) + '" value="' + par + '" oninput="lsSetModel(\'' + jsq(m.id) + '\', \'parallel\', this.value)"></div></div></div>' +
    '<div id="lsMMsgs-' + esc(m.id) + '">' + lsChecksHtml(checks) + '</div>' +
    '<div class="ls-nav"><span class="ls-left">Everything else uses this device\'s settings.</span><button type="button" class="ghost" onclick="lsUseDevice(\'' + jsq(m.id) + '\')">Use this device\'s settings</button>' +
    '<button type="button" class="ghost" onclick="lsSaveModel(\'' + jsq(m.id) + '\', \'save\')">Save</button>' +
    '<button type="button" onclick="lsSaveModel(\'' + jsq(m.id) + '\', \'restart-model\')">Save and restart this model</button></div></div></div>';
}

// ---- when the engine does not start ----
function lsFailHtml(f) {
  const lines = (f.engineLines && f.engineLines.length) ? f.engineLines : [f.message || ''];
  return lsCalloutHtml('warn', '<b>' + esc(f.name) + ' did not start.</b> llama-server said: <pre><code>' + lines.map(esc).join('\n') + '</code></pre>' +
    (f.cameFromExtraFlags ? 'Your extra flags caused this error. ' : '') +
    (f.canStartWithoutExtras ? '<button type="button" onclick="lsStartWithout(\'' + jsq(f.id) + '\')">Start without the extra flags</button>' : '') +
    (f.usedExtraFlags ? '<button type="button" class="ghost" onclick="lsEditFlags()">Edit the flags</button>' : ''));
}

// ---- the model list's tag ----
function lsModelTag(id) {
  if (!lsState) return '';
  const m = (lsState.models || []).find(x => x.id === id);
  if (!m) return '';
  return '<div class="lm-speed"><span class="ls-pill' + ((m.overridden || []).length ? ' on' : '') + '">' + esc(lsModelTagText(m)) + '</span> <button type="button" class="ghost" onclick="lsOpenModel(\'' + jsq(id) + '\')">Change for this model</button></div>' +
    (typeof pszLocalWarn === 'function' ? pszLocalWarn(id) : '');       // (prompt_size_ui.py: a line only when today's biggest prompt is close to this model's context)
}

// ---- everything below talks to the page and the API ----
function lsS() {
  return { state: lsState, draft: lsDraft, est: lsEst, preset: (lsEst && lsEst.preset) || (lsState && lsState.settings.preset), raw: lsRaw, parse: lsParse, undo: lsUndo, dirty: lsDirty,
           note: lsNote, restartOffer: lsNote && lsNote.indexOf('apply the next time') >= 0, err: lsErr, checking: lsChecking, open: lsOpen, modelChecks: lsModelChecks() };
}
function lsModelChecks() { const o = {}; for (const id in lsModelEst) o[id] = lsModelEst[id].checks; return o; }
function lsPayload() {
  const s = Object.assign({}, lsDraft);
  delete s.preset;
  return s;
}
function lsOverrides(openOnly) {
  const out = {};
  for (const id in lsOpen) {
    const o = lsOpen[id], one = {};
    if (o.ctx != null && o.ctx !== lsDraft.ctx) one.ctx = o.ctx;
    if (o.parallel != null && o.parallel !== lsDraft.parallel) one.parallel = o.parallel;
    out[id] = Object.keys(one).length ? one : null;
  }
  return out;
}
async function lsGet(url) { return await (await fetch(url)).json(); }
async function lsLoad(force) {
  let st;
  try { st = await lsGet('/config/local-models/settings?device=' + encodeURIComponent(LS_DEVICE)); } catch (e) { return; }
  if (!st || !st.settings) return;
  lsState = st;
  if (!lsDraft || force || !lsDirty) { lsDraft = lsDraftBase(st); lsRaw = lsFlagLines(lsDraft.extraArgs); lsParse = null; lsUndo = null; lsDirty = false; }
  lsEst = { checks: st.checks, preset: st.settings.preset, command: '', totalMb: st.estimate ? st.estimate.totalMb : null, summary: st.estimate ? st.estimate.summary : '', cardMb: st.estimate ? st.estimate.cardMb : null,
            exactNote: st.estimate ? st.estimate.exactNote : '', checked: false };
  lsDraw();
  if (typeof lmRender === 'function' && typeof lmState !== 'undefined' && lmState) lmRender();       // the model rows show each model's tag
  lsEstimateSoon(0);
  if (typeof pszLoadToday === 'function') pszLoadToday();
}
function lsDraw() {
  const box = document.getElementById('lsMount');
  if (!box || !lsState || !lsDraft) return;
  const S = lsS();
  let h = '<div id="lsFails">' + lsFailsHtml() + '</div>';
  h += '<h3 class="lm-step">How models run on this device</h3>' + lsDeviceHtml(S) + lsAdvancedHtml(S);
  const open = Object.keys(lsOpen);
  for (const m of (lsState.models || [])) if (open.indexOf(m.id) >= 0) h += lsModelPanelHtml(S, m);
  const keep = document.activeElement && document.activeElement.id === 'lsRaw' ? document.activeElement.selectionStart : null;
  box.innerHTML = h;
  if (keep != null) { const t = document.getElementById('lsRaw'); if (t) { t.focus(); t.setSelectionRange(keep, keep); } }
}
function lsFailsHtml() { return Object.keys(lsFails).map(id => lsFails[id] && lsFails[id].failed ? lsFailHtml(lsFails[id]) : '').join(''); }
function lsDrawLive() {
  const S = lsS();
  const set = (id, html) => { const e = document.getElementById(id); if (e) e.innerHTML = html; };
  set('lsEst', lsEstHtml(S.est, S.checking));
  set('lsMsgs', lsMessagesHtml(S));
  set('lsPresets', lsPresetsHtml(S.state, S.preset));
  const cmd = document.getElementById('lsCmd'); if (cmd) cmd.textContent = (S.est && S.est.command) || '';
  const off = lsFitDisabled(S);
  for (const id of ['lsFit', 'lsFitRange']) { const e = document.getElementById(id); if (e) e.disabled = off; }
  const box = document.getElementById('lsFitBox'); if (box) box.classList.toggle('ls-dim', off);
  set('lsFitWhy', esc(lsFitHelp(S, off)));
  const sv = document.getElementById('lsSave'); if (sv) sv.disabled = !lsDirty;
  const ap = document.getElementById('lsApplies'); if (ap) ap.textContent = lsNote ? 'Saved.' : 'Takes effect the next time a model starts.';
  const pg = document.getElementById('lsCtxPages'); if (pg) pg.textContent = 'tokens (about ' + lsPages(lsDraft.ctx, lsState) + ' pages)';
  const sh = document.getElementById('lsCtxShare');
  if (sh) sh.textContent = lsShareText(S);
  const fw = document.getElementById('lsFlashWhy'); if (fw) fw.textContent = lsFlashHelp(lsDraft);
  const fl = document.getElementById('lsFlash');
  if (fl) { const o = [...fl.options].find(x => x.value === 'off'); if (o) o.disabled = lsDraft.cacheType !== 'f16'; }
}
function lsEdited() { lsDirty = true; lsNote = ''; lsErr = null; lsEstimateSoon(450); lsDrawLive(); }
function lsEstimateSoon(ms) { clearTimeout(lsTimer); lsTimer = setTimeout(lsEstimate, ms); }
async function lsEstimate(confirm) {
  if (!lsDraft) return;
  try {
    const r = await fetch('/config/local-models/settings/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device: LS_DEVICE, settings: lsPayload(), confirm: !!confirm }) });
    const d = await r.json();
    if (r.ok) { lsEst = d; lsErr = null; }
    else if (d.checks) lsEst = Object.assign({}, lsEst || {}, { checks: d.checks, totalMb: null });
  } catch (e) {}
  for (const id of Object.keys(lsOpen)) {
    try {
      const ov = lsOverrides();
      const r = await fetch('/config/local-models/settings/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device: LS_DEVICE, settings: lsPayload(), models: { [id]: ov[id] || {} }, modelId: id }) });
      const d = await r.json(); if (r.ok) lsModelEst[id] = d;
    } catch (e) {}
  }
  lsChecking = false;
  lsDrawLive();
  for (const id of Object.keys(lsOpen)) { const e = document.getElementById('lsMMsgs-' + id); if (e && lsModelEst[id]) e.innerHTML = lsChecksHtml(lsModelEst[id].checks); }
}
function lsPickPreset(id) {
  const p = (lsState.presets || []).find(x => x.id === id);
  if (!p || !p.settings) return;
  lsDraft = Object.assign({}, lsDraft, p.settings); delete lsDraft.preset;
  lsRaw = lsFlagLines(lsDraft.extraArgs); lsParse = null; lsUndo = null;
  lsDirty = true; lsNote = ''; lsErr = null;
  lsDraw(); lsEstimateSoon(0);
}
function lsResetSuggested() { lsPickPreset('suggested'); }
function lsSetLayers(v) { lsDraft.layers = v === 'number' ? 20 : v; lsEdited(); lsDraw(); }
function lsSetLayersNum(v) { const n = parseInt(v, 10); if (n >= 1) { lsDraft.layers = n; lsEdited(); } }
function lsSetFit(v, fromRange) {
  const gb = parseFloat(v); if (!(gb >= 0)) return;
  lsDraft.fitTargetMb = Math.round(gb * 1024);
  const a = document.getElementById('lsFit'), b = document.getElementById('lsFitRange');
  if (fromRange && a) a.value = v; if (!fromRange && b) b.value = v;
  lsEdited();
}
function lsFitAuto() { lsDraft.fitTargetMb = 'auto'; lsEdited(); lsDraw(); }
function lsSetNum(key, v) { const n = parseInt(v, 10); if (n >= 1) { lsDraft[key] = n; lsEdited(); } }
function lsSetSel(key, v) {
  lsDraft[key] = v;
  if (key === 'cacheType' && v !== 'f16' && lsDraft.flashAttn === 'off') lsDraft.flashAttn = 'auto';
  lsEdited();
  if (key === 'cacheType') lsDraw();
}
function lsOpenModel(id) { if (!(id in lsOpen)) { const m = (lsState.models || []).find(x => x.id === id); lsOpen[id] = Object.assign({}, m ? m.override : {}); } lsDraw(); lsEstimateSoon(0); const e = document.getElementById('lsPanel-' + id); if (e) e.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
function lsCloseModel(id) { delete lsOpen[id]; lsDraw(); }
function lsSetModel(id, key, v) {
  const n = parseInt(v, 10); if (!(n >= 1)) return;
  lsOpen[id] = Object.assign({}, lsOpen[id], { [key]: n });
  lsDrawModelLive(id);
  lsEstimateSoon(450);
}
function lsDrawModelLive(id) {
  const m = (lsState.models || []).find(x => x.id === id); if (!m) return;
  const o = lsOpen[id] || {}, ctx = o.ctx != null ? o.ctx : lsDraft.ctx, par = o.parallel != null ? o.parallel : lsDraft.parallel, each = Math.floor(ctx / Math.max(1, par));
  const set = (k, t) => { const e = document.getElementById(k + '-' + id); if (e) e.textContent = t; };
  set('lsMPages', 'tokens, about ' + lsPages(ctx, lsState) + ' pages of text');
  set('lsMShare', par > 1 ? 'Shared by the ' + par + ' requests at the same time: each gets about ' + lsPages(each, lsState) + ' pages.' : 'One request at a time gets all of it: about ' + lsPages(each, lsState) + ' pages.');
  set('lsMWhy', 'This model answers up to ' + par + ' at a time. (This device\'s default: ' + lsDraft.parallel + ')');
  const n = (ctx !== lsDraft.ctx ? 1 : 0) + (par !== lsDraft.parallel ? 1 : 0);
  const tag = document.getElementById('lsTag-' + id);
  if (tag) { tag.textContent = lsModelTagText({ overridden: new Array(n) }); tag.classList.toggle('on', n > 0); }
}
async function lsPost(body) {
  const r = await fetch('/config/local-models/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ device: LS_DEVICE }, body)) });
  let d = {}; try { d = await r.json(); } catch (e) {}
  return { ok: r.ok, data: d };
}
async function lsSave(mode) {
  lsErr = null;
  const res = await lsPost({ settings: lsPayload(), apply: mode });
  if (!res.ok) { lsErr = { text: res.data.detail || 'Couldn\'t save that.', checks: res.data.checks || [] }; lsDrawLive(); return; }
  lsNote = mode === 'save' ? 'Saved. The new settings apply the next time a model starts.' : 'Saved. Running models are restarting with them.';
  const need = res.data.needsMemoryCheck;
  lsDirty = false;
  await lsLoad(true);
  if (typeof lmRefresh === 'function') lmRefresh();
  if (need) { lsChecking = true; lsDrawLive(); lsEstimate(true); }
  lsDrawLive();
}
async function lsSaveModel(id, mode) {
  const ov = lsOverrides();
  lsErr = null;
  const res = await lsPost({ models: { [id]: ov[id] || null }, apply: mode, modelId: id });
  const e = document.getElementById('lsMMsgs-' + id);
  if (!res.ok) { if (e) e.innerHTML = lsCalloutHtml('err', '🛑 ' + esc(res.data.detail || 'Couldn\'t save that.')); return; }
  const keep = Object.assign({}, lsOpen);
  await lsLoad(false);
  lsOpen = keep;
  if (typeof lmRefresh === 'function') lmRefresh();
  lsDraw(); lsEstimateSoon(0);
}
async function lsUseDevice(id) {
  const res = await lsPost({ models: { [id]: null }, apply: 'save' });
  if (res.ok) { delete lsOpen[id]; await lsLoad(false); if (typeof lmRefresh === 'function') lmRefresh(); }
}
let lsParseTimer = null;
function lsRawInput(v) { lsRaw = v; clearTimeout(lsParseTimer); lsParseTimer = setTimeout(lsRunParse, 600); }
async function lsRunParse() {
  if (!lsRaw.trim()) { lsParse = null; lsDraft.extraArgs = []; lsEdited(); document.getElementById('lsParseRes').innerHTML = ''; return; }
  let p; try { p = await (await fetch('/config/local-models/settings/parse', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: lsRaw }) })).json(); } catch (e) { return; }
  lsParse = p;
  if ((p.understood || []).length) lsUndo = Object.assign({}, lsDraft);
  Object.assign(lsDraft, p.settings || {});
  lsDraft.extraArgs = p.extra || [];
  lsDirty = true; lsNote = ''; lsErr = null;
  const dev = document.getElementById('lsDeviceCard');
  if (dev) { const S = lsS(); const tmp = document.createElement('div'); tmp.innerHTML = lsDeviceHtml(S); dev.replaceWith(tmp.firstChild); }
  const res = document.getElementById('lsParseRes'); if (res) res.innerHTML = lsParseHtml(lsParse, !!lsUndo);
  lsEstimateSoon(0);
}
function lsUndoParse() { if (!lsUndo) return; lsDraft = lsUndo; lsUndo = null; lsParse = null; lsRaw = lsFlagLines(lsDraft.extraArgs); lsDirty = true; lsDraw(); lsEstimateSoon(0); }
function lsClearFlags() { lsRaw = ''; lsParse = null; lsUndo = null; lsDraft.extraArgs = []; lsDirty = true; lsDraw(); lsEstimateSoon(0); }
function lsEditFlags() { const t = document.getElementById('lsRaw'); if (t) { t.scrollIntoView({ behavior: 'smooth', block: 'center' }); t.focus(); } }
function lsCopyCommand(btn) { if (typeof copyModel === 'function') copyModel(btn, (lsEst && lsEst.command) || ''); }
async function lsStartWithout(id) {
  try { await fetch('/config/local-models/settings/start-without-extras', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) }); } catch (e) {}
  delete lsFails[id]; lsDrawFails();
  if (typeof lmRefresh === 'function') lmRefresh();
}
function lsDrawFails() { const e = document.getElementById('lsFails'); if (e) e.innerHTML = lsFailsHtml(); }
// Called after every redraw of the model list: loads the settings once, then keeps the failed-model cards current.
async function lsSync() {
  if (!document.getElementById('lsMount') || typeof lmState === 'undefined' || !lmState) return;
  const ids = lmState.installed.map(m => m.id).join(',');
  if (!lsState || (!lsDirty && lsState.models.map(m => m.id).join(',') !== ids)) await lsLoad(false);
  let changed = false;
  for (const m of lmState.installed) {
    if (m.state === 'failed') {
      if (!lsFails[m.id] || lsFails[m.id].message !== m.message) { try { lsFails[m.id] = await lsGet('/config/local-models/settings/failure?id=' + encodeURIComponent(m.id)); changed = true; } catch (e) {} }
    } else if (lsFails[m.id]) { delete lsFails[m.id]; changed = true; }
  }
  if (changed) lsDrawFails();
}
"""

LOCAL_SETTINGS_HTML = """
  <div id="lsMount"></div>
"""
