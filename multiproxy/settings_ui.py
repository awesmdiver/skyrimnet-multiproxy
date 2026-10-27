"""The dashboard's Settings page behind a cog (2026-10-01), built to design/installer-and-tray-mockup.html, section 3.

A cog at the right end of the tab line (not a tab in the row) opens it; `#settings` is its deep link. Plain strings in their own
file, spliced into multiproxy.py's page, so none of the CSS/JS needs doubled braces. The page is drawn by `settings_ui.JS` from
`GET /config/settings`; everything that came from the server goes through esc() (defined by the Local models card's script).
The "I make QOL mods to make things easier." section (moved here from the Help tab) is built by help_ui._more_html().
"""
import json

import skyrimnet_finder
import tab_desc_ui

COG_HTML = """<div class="tab-cog" id="tabCog" onclick="openSettings()" title="Settings" role="button" aria-label="Settings">&#9881;</div>"""

CSS = r"""
  /* Settings page (2026-10-01) -- st-* classes */
  .tabs { align-items:flex-end }
  .tab-cog { margin-left:auto; padding:6px 12px; font-size:1.15rem; color:var(--muted); border-bottom:2px solid transparent; cursor:pointer; line-height:1.2 }
  .tab-cog:hover { color:var(--text) }
  .tab-cog.active { color:var(--accent-text); border-color:var(--accent) }
  .st h2 { font-size:1.15rem; margin:0 0 4px }
  .st-start { margin:8px 0 6px; padding:12px 14px; border:1px solid var(--line2); border-radius:8px }
  .st-start h3 { margin:0 0 6px; font-size:0.95rem }
  .st-steps { margin:6px 0 0 4px; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px; counter-reset:st }
  .st-steps > li { font-size:0.9rem; line-height:1.5 }
  .st-steps > li::before { counter-increment:st; content:counter(st) ". "; font-weight:600 }
  .st h3.st-sec { font-size:0.9rem; margin:22px 0 6px; color:var(--accent-text) }
  .st-toggle { display:flex; gap:12px; align-items:flex-start; margin:12px 0; font-size:0.88rem }
  .st-toggle.off-disabled { opacity:0.75 }
  .st-toggle .lm-muted { max-width:none }
  .st-field { margin:10px 0 }
  .st-field label { display:block; font-size:0.78rem; color:var(--muted); margin-bottom:3px }
  .st-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; max-width:640px }
  .st-row input[type=text] { flex:1; min-width:200px }
  .st-hint { color:var(--muted); font-size:0.8rem; margin-top:4px }
  .st-err { color:var(--err); font-size:0.82rem; margin-top:4px }
  .st-ok { color:var(--ok); font-size:0.82rem; margin-top:4px }
  .st-about { font-size:0.88rem }
  .st-about .st-hint { display:inline; margin:0 0 0 4px }
  .st-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:8px; background:var(--muted) }
  .st-dot.on { background:var(--ok) }
  .st-seg { display:inline-flex; border:1px solid var(--line); border-radius:6px; overflow:hidden }
  .st-seg button { border:0; border-radius:0; background:var(--bg); color:var(--muted); padding:7px 16px; cursor:pointer; font-size:0.85rem }
  .st-seg button + button { border-left:1px solid var(--line) }
  .st-seg button.on { background:var(--accent); color:var(--on-accent); font-weight:600 }
  .st-conn + .st-steps { margin-top:14px }
  .st-adv { margin:14px 0 4px; font-size:0.88rem }
  .st-adv summary { cursor:pointer; color:var(--accent-text); font-weight:600 }
  .st-limits { border-collapse:collapse; margin:8px 0; font-size:0.85rem }
  .st-limits td, .st-limits th { text-align:left; padding:4px 14px 4px 0; vertical-align:top }
  .st-limits th { color:var(--muted); font-weight:500; font-size:0.78rem }
  .st-num { width:64px }
  .st-share-list { max-height:50vh; overflow:auto; border-top:1px solid var(--line); margin-top:6px }
  .st-share-line { padding:7px 0; border-bottom:1px solid var(--line); font-size:0.9rem }
  .st-share-line > div { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
"""

PANE_HTML = """
  <div id="paneSettings" style="display:none">
""" + tab_desc_ui.html("settings") + """    <div class="card st" id="stCard"><div class="lm-muted">Reading your settings…</div></div>
    <div class="card st" id="stGpuCard" style="display:none"></div>
    <div id="stModalBox" class="op-modal" style="display:none" onclick="if (event.target === this) stCloseShare()"></div>
  </div>
"""

JS = r"""// ---------------- Settings page ----------------
// State from GET /config/settings. Opened by the cog (or #settings); drawn here, saved by small routes.
let stState = null;
let stMsg = {};            // per-control message {key: {text, ok}}
let stNetText = null;      // what's typed in the VPN networks box
let stPortText = null;     // what's typed in the port box, kept across redraws
let stBusy = false;

function openSettings(section) {
  showMainTab('settings');
  try { history.replaceState(null, '', '#settings'); } catch (e) {}
  if (section) setTimeout(() => { const el = document.getElementById('st-' + section); if (el && el.scrollIntoView) el.scrollIntoView(); }, 50);
}
const ST_STEPS = [
  'Check if the Skyrim mod section says Detected. If not, press Open folder and drag the mod into your mod manager.',
  'Under Folders, press Browse next to Your Skyrim folder and pick your game folder (use the Stock Game folder for Mod Organizer 2).',
  'Choose if MultiProxy starts and stops with Skyrim. Leave this on when you play.',
  'Leave the rest alone unless you are fixing a problem.',
  'You can change Page width under Appearance.',
];
const ST_UNREACHABLE = "MultiProxy isn't answering. Is it still running?";
// The one way every Settings call goes out. An unreachable server (the browser's own "Failed to fetch") becomes a plain line; a real error
// from the server keeps the server's own plain message.
async function stFetch(path, opts) {
  let r;
  try { r = await fetch(path, opts); }
  catch (e) { throw new Error(ST_UNREACHABLE); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || (d.error && d.error.message) || 'Something went wrong.');
  return d;
}
async function stLoad() {
  try {
    stState = await stFetch('/config/settings');
  } catch (e) {
    if (!stState) { $('stCard').innerHTML = '<div class="lm-err">' + esc(e.message === ST_UNREACHABLE ? ST_UNREACHABLE : "Couldn't read your settings.") + '</div>'; return; }
  }
  try { stCanUndo = !!(await stFetch('/config/setup')).canUndo; } catch (e) {}
  stRender();
  if (typeof gcLoad === 'function') gcLoad();      // the "Which graphics card" card (gpu_choice_ui.py): hidden unless a device has two cards
}
// The address, the Name to use and the two numbered SkyrimNet steps. The markup is the page's own <template id="stConnTemplate"> (made by the server, which knows the port and
// whether the mod and MultiProxy versions match); the steps are the walk-through's (SN_STEPS, get_started_ui.py), worded once.
function stConnHtml() {
  const t = document.getElementById('stConnTemplate');
  return (t ? t.innerHTML : '') + '<ol class="st-steps">' + (typeof SN_STEPS !== 'undefined' ? SN_STEPS : []).map(x => '<li>' + x + '</li>').join('') + '</ol>';
}
function stNote(key) {
  const m = stMsg[key];
  return m ? '<div class="' + (m.ok ? 'st-ok' : 'st-err') + '">' + esc(m.text) + '</div>' : '';
}
function stToggle(key, on, title, hint, disabled, disabledLine) {
  return '<div class="st-toggle' + (disabled ? ' off-disabled' : '') + '"><button class="op-sw' + (on ? ' on' : '') + '" aria-label="' + esc(title) + '"' +
    (disabled ? ' disabled style="cursor:not-allowed"' : ' onclick="stSetToggle(\'' + key + '\', ' + (!on) + ')"') + '></button><div><b>' + esc(title) + '</b>' +
    '<div class="lm-muted">' + hint + '</div>' + (disabled && disabledLine ? '<div class="lm-muted" style="margin-top:4px">' + esc(disabledLine) + '</div>' : '') + stNote(key) + '</div></div>';
}
function stRender() {
  const s = stState;
  if (!s) return;
  let h = '<h2>Settings</h2>';
  h += shBox('settings', '<div class="st-start"><h3>Start here</h3><ol class="st-steps">' + ST_STEPS.map(t => '<li>' + t + '</li>').join('') + '</ol>' +
    (s.getStarted ? '<div style="margin-top:12px"><button onclick="stRunGetStarted()">Run first-time setup again</button></div>' : '') + '</div>');
  // ---- Skyrim mod ----
  const mod = s.mod || {};
  h += '<h3 class="st-sec" id="st-mod">Skyrim mod</h3>';
  h += '<div class="st-about"><span class="st-dot' + (mod.detected ? ' on' : '') + '"></span>' +
    (mod.detected ? 'Detected' + (mod.version ? ' (version ' + esc(mod.version) + ')' : '') : 'Mod not detected') + '</div>';
  if (mod.package) {
    h += '<div class="st-field"><label>The mod package</label><div class="st-row"><input type="text" readonly value="' + esc(mod.package) + '">' +
      '<button class="primary" onclick="stOpenFolder(\'mod\')">Open folder</button><button onclick="stCopy(this, ' + "'mod'" + ')">Copy location</button></div>' +
      '<div class="st-hint">Drag this file into your mod manager (in Vortex, drop it on "Drop File(s)" at the bottom of the Mods page). Or copy the location and use your manager\'s "Install from file" button.</div>' + stNote('mod') + '</div>';
  } else {
    h += '<div class="st-hint" style="margin:6px 0">Download the Skyrim mod from the release page and add it to your mod manager (Vortex or Mod Organizer 2). <a href="' + esc(mod.releaseUrl || '#') + '" target="_blank" rel="noopener" style="color:var(--accent-text)">Open the release page ↗</a></div>';
  }
  // ---- SkyrimNet connection (the old Connect card of the Providers tab) ----
  h += '<h3 class="st-sec" id="st-skyrimnet">SkyrimNet connection</h3>' + stConnHtml();
  // ---- Starting and stopping ----
  h += '<h3 class="st-sec" id="st-start">Starting and stopping</h3>';
  h += stToggle('startStopWithSkyrim', !!s.startStopWithSkyrim, 'Start and stop with Skyrim',
    'On: Opens and closes with the game. <br>Off: Stays running in the background so models load faster and other devices stay connected.',
    !s.skyrimToggleEnabled, 'Install the Skyrim mod above to use this.');   // disabled with its own line when the mod isn't detected
  if (s.windowsSupported !== false)       // not on Linux: the installer asks about starting at login and the systemd user service does it
    h += stToggle('startWithWindows', !!s.startWithWindows, 'Start with Windows', 'Runs in the background until you need it.',
      !s.windowsToggleEnabled, s.windowsToggleNote);
  if (s.graphicsGuardWords)       // the memory guard's switch and its words come from gpu_guard.WORDS
    h += stToggle('graphicsGuard', !!s.graphicsGuard, s.graphicsGuardWords.title, s.graphicsGuardWords.hint);
  // ---- Fixing AI replies / Dashboard / Logs / Advanced (every option that used to mean editing multiproxy.ini) ----
  const o = s.options || {};
  h += '<h3 class="st-sec" id="st-fixing">Fixing provider replies</h3>';
  h += stToggle('fixNpcTags', !!o.fixNpcTags, 'Fix small mistakes in NPC names', 'Catches small spelling mistakes before SkyrimNet sees them.');
  h += stToggle('fixBrokenReplies', !!o.fixBrokenReplies, 'Repair or retry a broken reply', 'Some jobs, like memories, need an exact format. This fixes bad replies or asks the AI again.');
  h += stToggle('fixHiggsVoiceTags', !!o.fixHiggsVoiceTags, 'My NPCs use the Higgs voice engine', 'Turn this on only if you use the Higgs voice engine.');
  h += '<h3 class="st-sec" id="st-dashboard">Dashboard</h3>';
  h += stToggle('openBrowserOnStart', !!o.openBrowserOnStart, 'Open the dashboard when MultiProxy starts', '');
  h += stToggle('speedTracking', !!o.speedTracking, 'Track how fast each AI is (the Speeds tab)', '');
  const lim = s.limits || { providers: [], queueSeconds: 10 };
  h += '<details class="st-adv" id="st-advanced"' + (stAdvOpen ? ' open' : '') + ' ontoggle="stAdvOpen = this.open"><summary>Advanced: Concurrent jobs</summary>' +
    '<p class="lm-muted">Set how many jobs each provider can run at once. Type 0 for unlimited. Check your provider\'s plan for its limits.</p>' +
    '<table class="st-limits"><tr><th>Provider</th><th>Jobs at once</th><th>Provider</th><th>Jobs at once</th></tr>';
  for (let i = 0; i < lim.providers.length; i += 2) {
    h += '<tr>' + [lim.providers[i], lim.providers[i + 1]].map(p => p ? '<td>' + esc(p.label) + '</td><td>' + stLimitBox(p.id, p.value) + '</td>' : '<td></td><td></td>').join('') + '</tr>';
  }
  h += '</table><div class="st-field"><label>How many seconds a job waits before trying another provider</label><div class="st-row">' + stLimitBox('queueSeconds', lim.queueSeconds, 'width:56px;flex:none') + '</div></div></details>';
  // ---- Routing ----
  const mj = s.moveJob || 'ask';
  h += '<h3 class="st-sec" id="st-routing">Routing</h3>';
  h += '<div class="st-field"><label>' + esc(ST_MOVE_JOB) + '</label><div class="st-seg" role="radiogroup" aria-label="' + esc(ST_MOVE_JOB) + '">' +
    ST_MOVE_CHOICES.map(p =>
      '<button role="radio" aria-checked="' + (mj === p[0]) + '" class="' + (mj === p[0] ? 'on' : '') + '" onclick="stSetMoveJob(\'' + p[0] + '\')">' + esc(p[1]) + '</button>').join('') +
    '</div>' + stNote('moveJob') + '</div>';
  // ---- Share your setup ----
  h += '<h3 class="st-sec" id="st-share">' + esc(ST_SHARE.title) + '</h3><div class="st-hint" style="margin:0 0 8px">' + esc(ST_SHARE.sub) + '</div>' +
    '<div class="st-row"><button class="primary" onclick="stShareSave()">' + esc(ST_SHARE.save) + '</button><button onclick="stShareOpen()">' + esc(ST_SHARE.load) + '</button>' +
    (stCanUndo ? '<button onclick="stShareUndo()">' + esc(ST_SHARE.undo) + '</button>' : '') + '</div>' +
    '<div class="st-hint">' + esc(ST_SHARE.hint) + '</div>' + stNote('share');
  // ---- Folders ----
  const f = s.folders || {};
  h += '<h3 class="st-sec" id="st-folders">Folders</h3>';
  h += '<div class="st-field"><label>Your Skyrim folder</label><div class="st-row"><input type="text" readonly value="' + esc(f.skyrim || '') + '" placeholder="Not chosen yet">' +
    '<button onclick="stBrowse(\'skyrim\')">Browse…</button></div><div class="st-hint">If you use Mod Organizer 2, pick its Stock Game folder.</div>' +
    (f.skyrim && f.skyrimnetFound === false ? stAskSkyrimNet() : '') +
    stNote('skyrim') + '</div>';
  if (s.localModelsOffWords) h += stToggle('localModelsOff', !!s.localModelsOff, s.localModelsOffWords.title, esc(s.localModelsOffWords.hint));       // the switch sits with the models folder
  h += '<div class="st-field"><label>Models folder (stores text and voice models)</label><div class="st-row"><input type="text" readonly value="' + esc(f.models || '') + '" placeholder="Not chosen yet">' +
    '<button onclick="stBrowse(\'models\')">Browse…</button></div><div class="st-hint">Voice models and downloads go in a "voice" folder inside it.</div>' + stNote('models') + '</div>';
  // ---- Connection ----
  const c = s.connection || {};
  if (stPortText === null) stPortText = String(c.port);
  h += '<h3 class="st-sec" id="st-connection">Connection</h3>';
  h += '<div class="st-field"><label><a class="gl-term" data-gl="port">Port</a> SkyrimNet connects to</label><div class="st-row" style="max-width:420px"><input type="text" id="stPort" value="' + esc(stPortText) + '" oninput="stPortText = this.value" onkeydown="if (event.key === \'Enter\') stSavePort()">' +
    '<button onclick="stSavePort()">Save</button></div>' +
    '<div class="st-hint">' + "Change this only if another program uses this port. You must also update SkyrimNet's address to match." + '</div>' + stNote('port') + '</div>';
  if (stNetText === null) stNetText = (c.privateNetworks || []).join(', ');
  h += '<div class="st-field"><label>Other VPN or Tailscale networks</label><div class="st-row"><input type="text" id="stNets" value="' + esc(stNetText) + '" placeholder="10.8.0.0/16" oninput="stNetText = this.value" onkeydown="if (event.key === \'Enter\') stSaveNets()">' +
    '<button onclick="stSaveNets()">Save</button></div>' +
    '<div class="st-hint">Only needed if your VPN uses addresses outside normal home ranges. Separate them with commas.</div>' + stNote('nets') + '</div>';
  // ---- Appearance ----
  const pw = s.pageWidth || 'wide';
  h += '<h3 class="st-sec" id="st-appearance">Appearance</h3>';
  h += '<div class="st-field"><label>Page width</label><div class="st-seg" role="radiogroup" aria-label="Page width">' +
    [['comfortable', 'Comfortable'], ['wide', 'Wide'], ['full', 'Full']].map(p =>
      '<button role="radio" aria-checked="' + (pw === p[0]) + '" class="' + (pw === p[0] ? 'on' : '') + '" onclick="stSetWidth(\'' + p[0] + '\')">' + p[1] + '</button>').join('') +
    '</div><div class="st-hint">How much of the window the dashboard uses.</div>' + stNote('pageWidth') + '</div>';
  // ---- Troubleshooting ----
  const lf = s.logFields || { on: false, secondsLeft: 0 };
  h += '<h3 class="st-sec" id="st-troubleshooting">Troubleshooting</h3>';
  h += stToggle('logFields', !!lf.on, ST_LOG_FIELDS, ST_LOG_FIELDS_HINT + (lf.on ? '<br>' + esc(stOffIn(lf.secondsLeft)) : ''));
  stWatchLogFields(lf);
  h += shGuidesRow();      // "Start here guides": Hidden N of M and Show all again (start_here_ui.py)
  // ---- About ----
  const a = s.about || {};
  const u = a.update || {};
  let status;
  if (u.state === 'available') status = '<span class="st-hint">· </span><b style="color:var(--accent-text)">New version available</b> <a href="' + esc(u.url || '#') + '" target="_blank" rel="noopener" style="color:var(--accent-text)">' + esc(u.latest || 'See what\'s new') + ' ↗</a>';
  else if (u.state === 'current') status = '<span class="st-hint">· you\'re up to date</span>';
  else status = '';
  h += '<h3 class="st-sec" id="st-about">About</h3>';
  h += '<div class="st-about">SkyrimNet MultiProxy ' + esc(a.version || '') + (a.build ? ', built ' + esc(a.build) : '') + ' ' + status + ' <button style="margin-left:8px" onclick="stCheckNow(this)">Check now</button></div>' + stNote('about');
  const nt = (a.notices || []);
  if (nt.length) {
    h += '<details class="st-notices" style="margin-top:10px"><summary>Credits and notices</summary>' + nt.map(n =>
      '<div class="st-hint" style="margin-top:8px"><b>' + esc(n.name) + '</b>' + (n.url ? ' <a href="' + esc(n.url) + '" target="_blank" rel="noopener" style="color:var(--accent-text)">' + esc(n.url.replace(/^https:\/\//, '')) + ' ↗</a>' : '') +
      '<br>' + esc(n.text) + (n.notice ? '<br>' + esc(n.notice) : '') + '</div>').join('') + '</details>';
  }
  $('stCard').innerHTML = h;
  if (typeof addrLoad === 'function') addrLoad();        // the voice and quick decisions addresses (address_rows_ui.py)
}
function stPost(path, body) {
  return stFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
}
const ST_LOG_FIELDS = 'Log what SkyrimNet sends';
const ST_LOG_FIELDS_HINT = 'Writes the names and sizes of each voice and speech request to the log: never the words or the sound.';
// "Off in 52 minutes", from the real time left (the server turns the switch off by itself after an hour).
function stOffIn(secs) {
  const m = Math.ceil((secs || 0) / 60);
  return m <= 1 ? 'Off in less than a minute' : 'Off in ' + m + ' minutes';
}
// While the switch is on, ask again every half minute so the countdown stays true and the page shows it off the moment it runs out.
let stLogTimer = null;
function stWatchLogFields(lf) {
  if (stLogTimer) { clearTimeout(stLogTimer); stLogTimer = null; }
  if (!lf.on) return;
  stLogTimer = setTimeout(() => { stLogTimer = null; stLoad(); }, Math.min(30, Math.max(1, lf.secondsLeft)) * 1000);
  if (stLogTimer && stLogTimer.unref) stLogTimer.unref();
}
let stAdvOpen = false;
let stLimitDraft = {};
let stLimitErr = {};
function stLimitBox(id, value, style) {
  const shown = (id in stLimitDraft) ? stLimitDraft[id] : value;
  return '<input type="text" class="st-num" id="stLim_' + id + '" value="' + esc(shown) + '" style="' + (style || '') + '" onchange="stSaveLimit(\'' + id + '\', this.value)" onkeydown="if (event.key === \'Enter\') this.blur()">' +
    (stLimitErr[id] ? '<div class="st-err">' + esc(stLimitErr[id]) + '</div>' : '');
}
async function stSaveLimit(id, value) {
  delete stLimitErr[id];
  stLimitDraft[id] = value;
  try {
    const d = await stPost('/config/settings/limits', { [id]: value });
    delete stLimitDraft[id];
    stState.limits = d;
  } catch (e) { stLimitErr[id] = e.message; }
  stRender();
}
const ST_MOVE_JOB = 'When I move a job to another model';
const ST_MOVE_CHOICES = [['ask', 'Ask me each time'], ['keep', 'Keep the old model as a backup'], ['move', 'Just move it']];
async function stSetMoveJob(v) {
  delete stMsg.moveJob;
  try { stState = Object.assign(stState, await stPost('/config/settings', { moveJob: v })); }
  catch (e) { stMsg.moveJob = { text: e.message, ok: false }; }
  stRender();
}
async function stSetWidth(w) {
  delete stMsg.pageWidth;
  try {
    stState = Object.assign(stState, await stPost('/config/settings', { pageWidth: w }));
    document.documentElement.setAttribute('data-width', w);
  } catch (e) { stMsg.pageWidth = { text: e.message, ok: false }; }
  stRender();
}
function stRunGetStarted() { if (typeof openGetStarted === 'function') openGetStarted(true); }
// Turning "Don't run models on this PC" on while models are running asks first, naming them; they are stopped only on yes (Cancel on the left).
async function stLocalOffAsk() {
  let names = [];
  try { const d = await (await fetch('/config/local-models')).json(); names = (d.installed || []).filter(m => m.state === 'running' || m.state === 'starting').map(m => m.name); } catch (e) {}
  if (!names.length) return {};
  const w = stState.localModelsOffWords;
  const ok = await ownAsk(w.confirm.replace('{names}', names.join(', ')), w.confirmGo);
  return ok ? { stopRunning: true } : null;
}
async function stSetToggle(key, on) {
  stMsg[key] = null;
  let extra = {};
  if (key === 'localModelsOff' && on) { extra = await stLocalOffAsk(); if (extra === null) { stRender(); return; } }
  try { stState = Object.assign(stState, await stPost('/config/settings', Object.assign({ [key]: on }, extra))); }
  catch (e) { stMsg[key] = { text: e.message, ok: false }; }
  stRender();
  if (key === 'speedTracking' && !stMsg[key]) location.reload();      // the Speeds tab is part of the page: it appears or goes on a reload
}
// "Where is your SkyrimNet folder?" (skyrimnet_finder.WORDS): shown only while the folder was not found; the SkyrimNet folder, its mod folder or an MO2 setup all work.
var SN_WORDS = __SN_WORDS__;
function stAskSkyrimNet() {
  return '<div class="st-err" style="color:var(--warn)"><b>' + esc(SN_WORDS.ask) + '</b></div><div class="st-row"><input type="text" readonly value="" placeholder="Not chosen yet">' +
    '<button onclick="stBrowse(\'skyrimnet\')">' + esc(SN_WORDS.browse) + '</button></div><div class="st-hint">' + esc(SN_WORDS.hint) + '</div>';
}
async function stBrowse(what) {
  stMsg[what === 'skyrimnet' ? 'skyrim' : what] = null;
  try {
    const d = await stPost('/config/settings/pick-folder', { what });
    if (d.path && what === 'skyrimnet') {
      const r = await stPost('/config/skyrimnet-folder', { path: d.path });
      await stLoad();
      stMsg.skyrim = { text: r.line, ok: !!r.ok };
      stRender();
    } else if (d.path) {
      if (what === 'skyrim') await stPost('/config/skyrim-path', { path: d.path });
      else await stPost('/config/local-models/folder', { path: d.path });
      await stLoad();
      if (typeof lmRefresh === 'function' && what === 'models') lmRefresh();
      if (typeof detectSkyrimPath === 'function' && what === 'skyrim') { try { detectSkyrimPath(); } catch (e) {} }
    }
  } catch (e) { stMsg[what] = { text: e.message, ok: false }; stRender(); }
}
async function stOpenFolder(what) {
  stMsg[what] = null;
  try { await stPost('/config/settings/open-folder', { what }); }
  catch (e) { stMsg[what] = { text: e.message, ok: false }; stRender(); }
}
function stCopy(btn, what) {
  const text = what === 'mod' ? ((stState.mod || {}).package || '') : '';
  try { navigator.clipboard.writeText(text); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}
async function stSaveNets() {
  stMsg.nets = null;
  try {
    const d = await stPost('/config/settings/private-networks', { networks: stNetText || '' });
    stState.connection = Object.assign(stState.connection || {}, { privateNetworks: d.privateNetworks });
    stNetText = d.privateNetworks.join(', ');
    stMsg.nets = { text: 'Saved.', ok: true };
  } catch (e) { stMsg.nets = { text: e.message, ok: false }; }
  stRender();
}
async function stSavePort() {
  stMsg.port = null;
  const port = parseInt((stPortText || '').trim(), 10);
  try {
    const d = await stPost('/config/settings/port', { port: (stPortText || '').trim() });
    if (d.restarting) {
      stMsg.port = { text: 'Restarting on port ' + d.port + '…', ok: true };
      stRender();
      // the dashboard moves to the new address once the new copy answers
      const target = location.protocol + '//' + location.hostname + ':' + d.port + '/#settings';
      for (let i = 0; i < 40; i++) {
        await new Promise(r => setTimeout(r, 1000));
        try { const r = await fetch(location.protocol + '//' + location.hostname + ':' + d.port + '/health', { mode: 'no-cors' }); location.href = target; return; } catch (e) {}
      }
      return;
    }
    stState.connection = Object.assign(stState.connection || {}, { port: d.port });
    stMsg.port = { text: 'Saved.', ok: true };
  } catch (e) { stMsg.port = { text: e.message, ok: false }; }
  stRender();
}
async function stCheckNow(btn) {
  stMsg.about = null;
  const o = btn.textContent; btn.textContent = 'Checking…'; btn.disabled = true;
  try {
    const d = await stPost('/config/settings/check-update');
    stState.about = Object.assign(stState.about || {}, { update: d });
    if (d.state === 'unknown') stMsg.about = { text: "Couldn't check right now.", ok: false };
  } catch (e) { stMsg.about = { text: e.message === ST_UNREACHABLE ? ST_UNREACHABLE : "Couldn't check right now.", ok: false }; }
  stRender();
}
// ---- Share your setup (setup_file.py): Save my setup / Load a setup. All the words are here. ----
const ST_SHARE = {
  title: 'Share your setup',
  sub: "Save your routes and settings to a file, or load someone else's.",
  save: 'Save my setup', load: 'Load a setup', undo: 'Undo the last load',
  hint: 'Saved: which model does which job, backups, speed and thinking settings. Not saved: keys, sign-ins, pairing codes and addresses.',
  nothing: 'Nothing changes until you press Load.',
  skipped: 'Skipped.',
  cancel: 'Cancel',
  loadN: n => 'Load ' + n + (n === 1 ? ' change' : ' changes'),
  loadTitle: f => 'Load "' + f + '"',
};
let stCanUndo = false;
let stShare = null;        // the load pop-up: {file, lines, count, token, busy, error}
function stCloseShare() { stShare = null; stShareDraw(); }
function stShareDraw() {
  const box = $('stModalBox');
  if (!box) return;
  const m = stShare;
  if (!m) { box.style.display = 'none'; box.innerHTML = ''; return; }
  const rows = m.lines.map(l => '<div class="st-share-line"><div title="' + esc(l.text) + '"' + (l.skip ? ' class="lm-muted"' : '') + '>' + esc(l.text) + '</div>' +
    (l.skip ? '<div class="lm-warn" title="' + esc(l.skip) + '">⚠️ ' + esc(l.skip + ' ' + ST_SHARE.skipped) + '</div>'
      : l.warn ? '<div class="lm-warn" title="' + esc(l.warn) + '">⚠️ ' + esc(l.warn) + '</div>' : '') + '</div>').join('');
  box.innerHTML = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="stShareDialog"><h3>' + esc(ST_SHARE.loadTitle(m.file)) + '</h3>' +
    '<div class="st-share-list">' + rows + '</div>' + (m.error ? '<div class="lm-err">' + esc(m.error) + '</div>' : '') +
    '<div class="lm-muted">' + esc(ST_SHARE.nothing) + '</div>' +
    '<div class="vc-ft"><button class="ghost" onclick="stCloseShare()">' + esc(ST_SHARE.cancel) + '</button><button class="primary"' + (m.busy || !m.count ? ' disabled' : '') + ' onclick="stShareApply()">' + esc(ST_SHARE.loadN(m.count)) + '</button></div></div>';
  box.style.display = 'flex';
  const d = $('stShareDialog'); if (d && d.focus) d.focus();
}
window.addEventListener('keydown', e => { if (e.key === 'Escape' && stShare) stCloseShare(); });
async function stShareSave() {
  delete stMsg.share;
  try { await stPost('/config/setup/save'); }
  catch (e) { stMsg.share = { text: e.message, ok: false }; }
  stRender();
}
async function stShareOpen() {
  delete stMsg.share;
  try {
    const d = await stPost('/config/setup/open');
    if (!d.cancelled) { stShare = { file: d.file, lines: d.lines, count: d.count, token: d.token, busy: false, error: '' }; stShareDraw(); }
  } catch (e) { stMsg.share = { text: e.message, ok: false }; }
  stRender();
}
async function stShareApply() {
  if (!stShare || stShare.busy) return;
  stShare.busy = true; stShare.error = ''; stShareDraw();
  try {
    const d = await stPost('/config/setup/apply', { token: stShare.token });
    stCanUndo = !!d.canUndo;
    stShare = null; stShareDraw(); stRender();
  } catch (e) { stShare.busy = false; stShare.error = e.message; stShareDraw(); }
}
async function stShareUndo() {
  delete stMsg.share;
  try { stCanUndo = !!(await stPost('/config/setup/undo')).canUndo; }
  catch (e) { stMsg.share = { text: e.message, ok: false }; }
  stRender();
}
function stHash() {
  if ((location.hash || '') === '#settings') { openSettings(); return true; }
  return false;
}
window.addEventListener('hashchange', stHash);
stHash();
"""

JS = JS.replace("__SN_WORDS__", json.dumps(skyrimnet_finder.WORDS))
