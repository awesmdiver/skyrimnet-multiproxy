"""The Get started flow (2026-10-02), built to design/get-started-mockup.html, section 1 (Revision 2, wording as approved).

Five short steps in the dashboard itself (not a separate window; the tabs stay reachable): Welcome, Skyrim, Your AI, Preferences, SkyrimNet.
A first install lands on the Overview page, whose Setup box (on top while setup is unfinished) leads in here with Continue setup; it is also reopened by "Run Get started again" on the Settings page, and
has the deep link `#get-started`. Skip setup and Finish both mark it done; leaving part way resumes where they were (the step is saved by
`POST /config/get-started`). Plain strings in their own file, spliced into multiproxy.py's page, so none of the CSS/JS needs doubled braces.

Nothing here keeps a second copy of anything:
  * Preferences are the same settings as on the Settings page (same routes, same state: `GET/POST /config/settings`).
  * "Set it up" for an AI moves the Providers tab's own provider panel (`#panel`, with its key box, its help and its models) into the step, together
    with that AI's Help page steps, and puts it back when the step is left. The dots are the providers' real state.
  * The Skyrim folder is the same setting (`/config/skyrim-path`), picked with the same native dialog.
  * It never writes into Skyrim's folder or the mod's own file: step 2 only *shows* the four lines for that file, filled in with this install's real values.
Everything that came from the server goes through esc() (defined by the Local models card's script, on the same page).
"""

import json

import skyrimnet_finder

CSS = r"""
  /* Get started (2026-10-02) -- gs-* classes */
  .gs h2 { font-size:1.15rem; margin:0 0 8px }
  .gs h3 { font-size:0.9rem; margin:18px 0 6px }
  .gs-steps { display:flex; gap:6px; align-items:center; font-size:0.74rem; color:var(--muted); margin-bottom:22px; flex-wrap:wrap }
  .gs-steps span { padding:3px 10px; border-radius:999px; border:1px solid var(--line) }
  .gs-steps span.on { background:var(--accent); border-color:var(--accent); color:var(--on-accent); font-weight:600 }
  .gs-steps span.done { color:var(--ok); border-color:color-mix(in srgb, var(--ok) 40%, var(--bg)) }
  .gs-ft { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:26px; flex-wrap:wrap }
  .gs-ft > div { display:flex; gap:8px }
  .gs-link { background:none !important; border:none !important; color:var(--muted); text-decoration:underline; padding:5px 4px; cursor:pointer }
  .gs-hint { color:var(--muted); font-size:0.85rem; margin:2px 0 12px }
  .gs-field { margin:10px 0 }
  .gs-field label { display:block; font-size:0.76rem; color:var(--muted); margin:0 0 3px }
  .gs-row { display:flex; gap:8px; margin:6px 0; flex-wrap:wrap; align-items:center }
  .gs-row input[type=text], .gs-row select { flex:1; min-width:200px }
  .gs-warn { display:flex; gap:8px; align-items:center; margin:8px 0; padding:8px 12px; border:1px solid color-mix(in srgb, var(--warn) 50%, var(--bg)); border-radius:8px; background:color-mix(in srgb, var(--warn) 9%, var(--bg)); font-size:0.82rem }
  .gs-pick { display:grid; grid-template-columns:repeat(3, 1fr); gap:12px }
  @media (max-width:760px) { .gs-pick { grid-template-columns:1fr } }
  .gs-pick div { border:1px solid var(--line); border-radius:8px; padding:14px; background:var(--panel2, var(--panel)); font-size:0.84rem; cursor:pointer }
  .gs-pick div.sel { border-color:var(--accent) }
  .gs-pick b { display:block; margin-bottom:4px }
  .gs-setup { border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:12px 0 }
  .gs-status { font-size:0.85rem; margin-bottom:8px }
  .gs-dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:var(--muted); margin-right:6px }
  .gs-dot.ok { background:var(--ok) } .gs-dot.warn { background:var(--warn) }
  .gs-toggle { display:flex; gap:12px; align-items:flex-start; margin:14px 0; font-size:0.88rem }
  .gs-toggle small { display:block; color:var(--muted); font-size:0.8rem }
  .gs-toggle.off-disabled { opacity:0.75 }
  .gs-pre { background:#0c0c0c; border:1px solid #2b2b31; border-radius:6px; padding:12px 14px; font-size:0.78rem; color:#cccccc; white-space:pre-wrap; margin:6px 0; font-family:ui-monospace,Consolas,monospace; overflow-wrap:anywhere }
  .gs details { border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:14px 0; font-size:0.85rem }
  .gs summary { cursor:pointer; color:var(--muted) }
  .gs-err { color:var(--err); font-size:0.82rem; margin-top:4px }
  .gs-start { margin:6px 0 16px; padding:12px 14px; border:1px solid var(--line2); border-radius:8px }
  .gs-start h3 { margin:0 0 6px }
  .gs-start ol { margin:6px 0 0 4px; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px; counter-reset:gs }
  .gs-start li { font-size:0.9rem; line-height:1.5 }
  .gs-start li::before { counter-increment:gs; content:counter(gs) ". "; font-weight:600 }
"""

PANE_HTML = """
  <div id="paneStart" style="display:none">
    <div class="card gs" id="gsCard"><div class="lm-muted">Getting ready…</div></div>
  </div>
"""

JS = r"""// ---------------- Get started ----------------
// State from GET /config/get-started (this install's real values) and GET /config/settings (the preferences).
// The two SkyrimNet steps, worded once: the walk-through's list and Settings > SkyrimNet connection both use them.
const SN_STEPS = [
  'In SkyrimNet, go to <b>AI &gt; Providers</b> and press <b>+ Add Custom Provider</b>. Copy the Name and MultiProxy\'s address, set <b>API Style</b> to Chat Completions, and leave the key blank.',
  'In SkyrimNet: go to <b>AI &gt; Models</b>. For each job, pick the new provider and paste the model name.',
];
const GS_START = [
  "Press Let's go.",
  'Install the Skyrim mod in Vortex or Mod Organizer 2 (open the folder, drag the file in).',
  'Press Browse… and pick your Skyrim folder (for Mod Organizer 2, choose the Stock Game folder).',
  'Pick a provider, press Set it up, then sign in or paste your key until the dot turns green.',
  'Press Next and switch on the options you want.',
  SN_STEPS[0],
  SN_STEPS[1],
  'Press Finish.',
];
const GS_STEPS = ['Welcome', 'Skyrim', 'Your AI', 'Preferences', 'SkyrimNet'];
const GS_GROUPS = [
  { id: 'sub', title: 'A subscription I already have', blurb: 'Claude, ChatGPT (Codex), Google Antigravity or Grok (SuperGrok). No key needed.', heading: 'Subscriptions', ids: ['claude', 'codex', 'geminicli', 'grok'] },
  { id: 'pay', title: 'Pay as you go', blurb: 'You paste a key. (GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, xAI, Continuum, Kilo, TypeSafe, Perplexity, and Cloudflare, Mistral and Groq, which have a small free allowance)', heading: 'Pay as you go', ids: ['glm', 'nano', 'openai', 'deepseek', 'gemini', 'xai', 'continuum', 'kilo', 'typesafe', 'perplexity', 'cloudflare', 'mistral', 'groq'] },
  { id: 'own', title: 'My own hardware (free)', blurb: 'A model on this device or another device in the house.', heading: 'My own hardware', ids: ['local', 'ollama', 'lmstudio', 'remote'] },
];
let gs = null;               // GET /config/get-started
let gsPrefs = null;          // GET /config/settings
let gsStep = 0;
let gsGroup = null;          // the AI group card that is picked
let gsPick = {};             // per group: the provider chosen in its list
let gsAi = '';               // the AI "Set it up" was pressed for
let gsMsg = {};
let gsPanelHome = null;      // where the Providers tab's provider panel lives, so it can be put back
let gsTimer = null;

async function gsFetch(path, opts) {
  let r;
  try { r = await fetch(path, opts); } catch (e) { throw new Error("MultiProxy isn't answering. Is it still running?"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Something went wrong.');
  return d;
}
function gsPost(path, body) { return gsFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); }

async function openGetStarted(restart) {
  showMainTab('start');
  try { history.replaceState(null, '', '#get-started'); } catch (e) {}
  const card = document.getElementById('gsCard');
  try {
    if (restart) await gsPost('/config/get-started', { restart: true });
    gs = await gsFetch('/config/get-started');
    gsPrefs = await gsFetch('/config/settings');
  } catch (e) { card.innerHTML = '<div class="lm-err">' + esc(e.message) + '</div>'; return; }
  gsStep = Math.max(0, Math.min(4, gs.step || 0));
  gsAi = gs.ai || '';
  gsMsg = {};
  gsRender();
}
function gsSave(extra) { return gsPost('/config/get-started', Object.assign({ step: gsStep }, extra || {})).then(s => { gs = Object.assign(gs || {}, s); }).catch(() => {}); }
function gsGo(n) { gsRestorePanel(); gsStep = Math.max(0, Math.min(4, n)); gsSave(); gsRender(); window.scrollTo(0, 0); }
async function gsLeave(done) {
  gsRestorePanel();
  await gsPost('/config/get-started', { done: true, step: gsStep, ai: gsAi }).catch(() => {});
  try { history.replaceState(null, '', location.pathname); } catch (e) {}
  showMainTab('overview');
}
function gsIsReady(p) { return !!p && p.state === 'ok'; }
function gsReadyList() { return PROVIDERS.filter(gsIsReady); }
function gsChosen() {
  const p = PROVIDERS.find(x => x.id === gsAi);
  if (gsIsReady(p)) return p;
  return gsReadyList()[0] || null;
}

// ---- the Providers tab's own provider panel, shown right in the step ----
function gsRestorePanel() {
  const panel = document.getElementById('panel');
  if (panel && gsPanelHome && panel.parentNode !== gsPanelHome.parent) gsPanelHome.parent.insertBefore(panel, gsPanelHome.next);
}
function gsMovePanelHere() {
  const panel = document.getElementById('panel'), host = document.getElementById('gsPanelHost');
  if (!panel || !host) return;
  if (!gsPanelHome) gsPanelHome = { parent: panel.parentNode, next: panel.nextSibling };
  if (panel.parentNode !== host) host.appendChild(panel);
}
async function gsSetUp(id) {
  gsAi = id;
  gsSave({ ai: id });
  if (typeof helpLoad === 'function') await helpLoad();
  selectProvider(id);                       // fills the Providers tab's panel for this AI (its key box, its help, its models)
  gsRender();
}
function gsPickGroup(id) { gsGroup = id; gsRender(); }
function gsPickProvider(group, id) { gsPick[group] = id; }

function gsToggle(key, on, title, hint, disabled, disabledLine) {
  return '<div class="gs-toggle' + (disabled ? ' off-disabled' : '') + '"><button class="op-sw' + (on ? ' on' : '') + '" aria-label="' + esc(title) + '"' +
    (disabled ? ' disabled style="cursor:not-allowed"' : ' onclick="gsSetToggle(\'' + key + '\', ' + (!on) + ')"') + '></button><div>' + esc(title) +
    (hint ? '<small>' + esc(hint) + '</small>' : '') + (disabled && disabledLine ? '<small>' + esc(disabledLine) + '</small>' : '') +
    (gsMsg[key] ? '<div class="gs-err">' + esc(gsMsg[key]) + '</div>' : '') + '</div></div>';
}
async function gsSetToggle(key, on) {
  delete gsMsg[key];
  try { gsPrefs = Object.assign(gsPrefs, await gsPost('/config/settings', { [key]: on })); }
  catch (e) { gsMsg[key] = e.message; }
  gsRender();
  if (key === 'speedTracking' && !gsMsg[key]) { try { history.replaceState(null, '', '#get-started'); } catch (e) {} location.reload(); }   // the Speeds tab is part of the page
}

function gsCopy(btn, text) {
  try { navigator.clipboard.writeText(text); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}
function gsCopyId(btn, id) { gsCopy(btn, (document.getElementById(id) || {}).value || (document.getElementById(id) || {}).textContent || ''); }
async function gsOpenModFolder() {
  delete gsMsg.mod;
  try { await gsPost('/config/settings/open-folder', { what: 'mod' }); } catch (e) { gsMsg.mod = e.message; gsRender(); }
}
// "Where is your SkyrimNet folder?" (skyrimnet_finder.WORDS): shown only while the folder was not found.
var SN_WORDS = __SN_WORDS__;       // (a var: the Settings page's script declares the same words and both live in one page)
async function gsBrowseSkyrimNet() {
  delete gsMsg.skyrimnet;
  try {
    const d = await gsPost('/config/settings/pick-folder', { what: 'skyrimnet' });
    if (d.path) {
      const r = await gsPost('/config/skyrimnet-folder', { path: d.path });
      gsMsg.skyrimnet = r.line; gsMsg.skyrimnetOk = !!r.ok;
      gs = Object.assign(gs, await gsFetch('/config/get-started'));
    }
  } catch (e) { gsMsg.skyrimnet = e.message; gsMsg.skyrimnetOk = false; }
  gsRender();
}
async function gsBrowse() {
  delete gsMsg.skyrim;
  try {
    const d = await gsPost('/config/settings/pick-folder', { what: 'skyrim' });
    if (d.path) {
      await gsPost('/config/skyrim-path', { path: d.path });
      gs = Object.assign(gs, await gsFetch('/config/get-started'));
      if (typeof detectSkyrimPath === 'function') { try { detectSkyrimPath(); } catch (e) {} }
    }
  } catch (e) { gsMsg.skyrim = e.message; }
  gsRender();
}

function gsHeader() {
  return '<div class="gs-steps">' + GS_STEPS.map((n, i) => '<span class="' + (i < gsStep ? 'done' : i === gsStep ? 'on' : '') + '">' + (i < gsStep ? '✓ ' : (i + 1) + ' ') + n + '</span>').join('') + '</div>';
}
function gsFooter(skipLabel, onSkip, nextLabel, onNext, nextDisabled) {
  const back = gsStep > 0 ? '<button onclick="gsGo(' + (gsStep - 1) + ')">Back</button>' : '';
  return '<div class="gs-ft">' + (skipLabel ? '<button class="gs-link" onclick="' + onSkip + '">' + esc(skipLabel) + '</button>' : '<span></span>') +
    '<div>' + back + '<button class="primary" id="gsNext"' + (nextDisabled ? ' disabled' : '') + ' onclick="' + onNext + '">' + esc(nextLabel) + '</button></div></div>';
}

function gsStepWelcome() {
  return '<h2>Welcome to MultiProxy</h2>' +
    (typeof smuBlock === 'function' ? smuBlock() : '') +
    shBox('get-started', '<div class="gs-start"><h3>Start here</h3><ol>' + GS_START.map(t => '<li>' + t + '</li>').join('') + '</ol></div>') +
    '<p>MultiProxy connects SkyrimNet to your chosen Providers. You can use your own subscriptions, free models on this PC, or Other devices in your home.</p>' +
    '<p class="lm-muted">Five quick steps. You can skip any step and change things later in Settings at the top right.</p>' +
    '<div class="gs-ft"><button class="gs-link" onclick="gsLeave()">Skip setup</button><div><button class="primary" onclick="gsGo(1)">Let\'s go</button></div></div>';
}
function gsStepSkyrim() {
  const mod = gs.mod || {};
  let h = '<h2>Connect it to Skyrim</h2><p>A small mod lets Skyrim start MultiProxy for you.</p>';
  if (mod.package) {
    const name = String(mod.package).split(/[\\/]/).pop();
    h += '<p class="gs-hint">Open the folder and drag <b>' + esc(name) + '</b> straight into Vortex or MO2.</p>' +
      '<div class="gs-row"><input type="text" id="gsModPath" readonly value="' + esc(mod.package) + '"><button class="primary" onclick="gsOpenModFolder()">Open folder</button></div>' + (gsMsg.mod ? '<div class="gs-err">' + esc(gsMsg.mod) + '</div>' : '');
  } else {
    h += '<p class="gs-hint">Download the Skyrim mod from the release page and add it to your mod manager (Vortex or Mod Organizer 2). <a href="' + esc(mod.releaseUrl || '#') + '" target="_blank" rel="noopener" style="color:var(--accent-text)">Open the release page</a></p>';
  }
  h += '<div class="gs-field"><label>Your Skyrim folder (so MultiProxy can read your SkyrimNet settings)</label><div class="gs-hint" style="margin:2px 0">Using Mod Organizer 2? Pick its Stock Game folder.</div><div class="gs-row"><input type="text" readonly value="' + esc(gs.skyrimFolder || '') + '" placeholder="Not linked yet">' +
    '<button onclick="gsBrowse()">Browse…</button></div>' +
    (gsMsg.skyrim ? '<div class="gs-err">' + esc(gsMsg.skyrim) + '</div>' : '') + '</div>';
  if (gs.skyrimFolder && gs.skyrimnetFound === false) {
    h += '<div class="gs-warn"><span class="gs-dot warn"></span><b>' + esc(SN_WORDS.ask) + '</b></div><div class="gs-row"><input type="text" readonly value="" placeholder="Not chosen yet">' +
      '<button onclick="gsBrowseSkyrimNet()">' + esc(SN_WORDS.browse) + '</button></div><div class="gs-hint">' + esc(SN_WORDS.hint) + '</div>';
  }
  if (gsMsg.skyrimnet) h += '<div class="' + (gsMsg.skyrimnetOk ? 'gs-hint' : 'gs-err') + '">' + esc(gsMsg.skyrimnet) + '</div>';
  h += '<details><summary>The mod\'s own settings file (most people never need this)</summary>' +
    '<p class="lm-muted">If your mod manager can\'t find MultiProxy, put these lines in <code>SkyrimNetMultiProxy.ini</code> (this lives with the mod in your mod manager):</p>' +
    '<pre class="gs-pre" id="gsIni">' + esc(gs.iniLines || '') + '</pre><button onclick="gsCopyId(this, \'gsIni\')">Copy these lines</button></details>';
  return h + gsFooter('Skip for now', 'gsGo(2)', 'Next', 'gsGo(2)');
}
function gsStepAi() {
  if (!gsGroup) {
    const cur = GS_GROUPS.find(g => g.ids.includes(gsAi));
    gsGroup = (cur || GS_GROUPS[0]).id;
  }
  const g = GS_GROUPS.find(x => x.id === gsGroup);
  const list = PROVIDERS.filter(p => g.ids.includes(p.id));
  const sel = gsPick[g.id] && list.some(p => p.id === gsPick[g.id]) ? gsPick[g.id] : (list.some(p => p.id === gsAi) ? gsAi : '');
  gsPick[g.id] = sel;
  let h = '<h2>Which AI do you want to start with?</h2><p class="gs-hint">Pick one to begin. You can add more afterwards.</p><div class="gs-pick">' +
    GS_GROUPS.map(x => '<div class="' + (x.id === gsGroup ? 'sel' : '') + '" onclick="gsPickGroup(\'' + x.id + '\')"><b>' + esc(x.title) + '</b>' + esc(x.blurb) + '</div>').join('') + '</div>';
  h += '<h3>' + esc(g.heading) + '</h3><div class="gs-row"><select id="gsSelect" onchange="gsPickProvider(\'' + g.id + '\', this.value)">' +
    '<option value="">Pick a provider</option>' + list.map(p => '<option value="' + esc(p.id) + '"' + (p.id === sel ? ' selected' : '') + '>' + esc((p.state === 'ok' ? '✓  ' : '○  ') + pillLabel(p)) + '</option>').join('') +
    '</select><button class="primary" onclick="if (document.getElementById(\'gsSelect\').value) gsSetUp(document.getElementById(\'gsSelect\').value)">Set it up</button></div>';
  const cur = PROVIDERS.find(p => p.id === gsAi);
  if (cur && g.ids.includes(cur.id)) {
    const topic = (typeof helpTopics !== 'undefined' && helpTopics) ? helpTopics.find(t => t.provider === cur.id) : null;
    h += '<div class="gs-setup"><p style="margin:0 0 8px">Follow the steps to connect this provider.</p><div class="gs-status" id="gsStatus"></div>' + (topic ? '<div class="gs-steps-help">' + topic.html + '</div>' : '') + '<div id="gsPanelHost"></div></div>';
  } else {
    h += '<p class="lm-muted">Press Set it up to see short instructions for that provider. Not sure which? ' +
      '<span class="lm-link" onclick="gsGoHelp()">Which AI should I use?</span></p>';
  }
  if (cur && g.ids.includes(cur.id)) h += '<p class="lm-muted">Not sure which? <span class="lm-link" onclick="gsGoHelp()">Which AI should I use?</span></p>';
  h += '<p class="lm-muted" id="gsQuick"></p>';
  h += '<p class="lm-muted" id="gsNextNote"' + (gsReadyList().length ? ' style="display:none"' : '') + '>Pick a provider and set it up to continue.</p>';
  return h + gsFooter("I'll do this later", 'gsGo(3)', 'Next', 'gsGo(3)', !gsReadyList().length);
}
function gsGoHelp() { gsRestorePanel(); openHelp('which-ai'); }
function gsGoDecisions() { gsRestorePanel(); showMainTab('decisions'); }
function gsGoQuick() { gsRestorePanel(); showMainTab('quick'); }
function gsRefreshAiBits() {
  const st = document.getElementById('gsStatus'), cur = PROVIDERS.find(p => p.id === gsAi);
  if (st && cur) st.innerHTML = '<span class="gs-dot ' + (cur.state === 'ok' ? 'ok' : cur.state === 'warn' ? 'warn' : '') + '"></span>' + esc(cur.status || '') +
    (cur.state === 'ok' ? ' — ready' : '');
  const q = document.getElementById('gsQuick');
  if (q) q.innerHTML = PROVIDERS.some(p => p.testable) ? 'Want to make sure it works before you open the game? <span class="lm-link" onclick="gsGoQuick()">Try it now in Quick Test</span>.' :
    'Want to make sure it works before you open the game? <span class="lm-muted">Try it now in Quick Test</span> (once an AI is ready).';
  const nx = document.getElementById('gsNext');
  if (nx && gsStep === 2) nx.disabled = !gsReadyList().length;
  const nn = document.getElementById('gsNextNote');
  if (nn) nn.style.display = gsReadyList().length ? 'none' : '';
}
function gsStepPrefs() {
  const s = gsPrefs || {}, o = s.options || {};
  return '<h2>A few preferences</h2>' +
    gsToggle('startStopWithSkyrim', !!s.startStopWithSkyrim, 'Start and stop with Skyrim', '(MultiProxy closes when the game closes)', !s.skyrimToggleEnabled, s.skyrimToggleNote) +
    gsToggle('openBrowserOnStart', !!o.openBrowserOnStart, 'Open the dashboard when MultiProxy starts', '') +
    gsToggle('speedTracking', !!o.speedTracking, 'Track how fast each AI is', 'Shows the Speeds tab. Nothing leaves your device.') +
    gsToggle('fixHiggsVoiceTags', !!o.fixHiggsVoiceTags, 'My NPCs speak with Higgs voice', 'Turn this on so Higgs reads dialogue correctly.') +
    gsFooter('', '', 'Next', 'gsGo(4)');
}
// What to paste in the Model box: a list of { name, ai, any, local }. The local model is the one Set me up installed, else the newest installed on this PC; the cloud one is the AI
// picked in "Your AI". Both are listed when both are set up (the local one first when Set me up installed it).
function gsModelName() {
  const local = (gs && gs.setMeUpChat) || ((gs && gs.localModels && gs.localModels[0]) || {}).name || '';
  const out = local ? [{ name: local, ai: '', local: true }] : [];
  const p = gsChosen();
  if (p) {
    const cloud = p.models && p.models.length ? { name: p.models[0][0], ai: p.name } : { name: '', ai: p.name, any: p.any || '' };
    if (gs && gs.setMeUpChat) out.push(cloud); else out.unshift(cloud);
  }
  return out;
}
function gsStepSkyrimNet() {
  const name = gs.providerName || 'SkyrimNet MultiProxy';
  const ms = gsModelName();
  let h = '<h2>Last step: tell SkyrimNet</h2><p class="gs-hint">Two short stops in SkyrimNet\'s own settings page. You can open it from inside the game, or in your browser while Skyrim is running.</p>' +
    '<h3>1. Add MultiProxy as a provider</h3><p style="font-size:0.88rem;margin:4px 0">Go to <b>AI &gt; Providers</b>, press <b>+ Add Custom Provider</b>, and fill it in like this:</p>' +
    '<div class="gs-field"><label>Name</label><div class="gs-row"><input type="text" id="gsName" readonly value="' + esc(name) + '"><button onclick="gsCopyId(this, \'gsName\')">Copy</button></div></div>' +
    '<div class="gs-field"><label>MultiProxy\'s address</label><div class="gs-row"><input type="text" id="gsEndpoint" readonly value="' + esc(gs.endpoint || '') + '"><button onclick="gsCopyId(this, \'gsEndpoint\')">Copy</button></div></div>' +
    '<p class="lm-muted">Set <b><a class="gl-term" data-gl="api-style">API Style</a></b> to <b>Chat Completions</b>.</p>' +
    '<p class="lm-muted">Leave the <b>API Key</b> box completely empty. MultiProxy keeps your keys safe, so SkyrimNet does not need them.</p>' +
    '<h3>2. Choose it for the jobs you want</h3><p style="font-size:0.88rem;margin:4px 0">Go to <b>AI &gt; Models</b>. For each job, pick <b>' + esc(name) + '</b> in the <b>Provider</b> list. Then paste this model into the <b>Model</b> box:</p>';
  const both = ms.filter(m => m.name).length > 1;
  ms.forEach((m, i) => {
    if (m.name) {
      const label = both ? (m.local ? 'Model name (the local model you installed)' : 'Model name (' + m.ai + ')') : 'Model name (for the AI you just set up)';
      h += '<div class="gs-field"><label>' + esc(label) + '</label><div class="gs-row"><input type="text" id="gsModel' + i + '" readonly value="' + esc(m.name) + '"><button onclick="gsCopyId(this, \'gsModel' + i + '\')">Copy</button></div></div>';
    } else {
      h += '<p class="lm-muted">' + esc(m.ai) + ' takes any model' + (m.any ? '. Use <code>' + esc(m.any) + '</code>' : '') + '.</p>';
    }
  });
  if (!ms.length) {
    h += '<p class="lm-muted">You skipped choosing an AI, so there is no model name to paste yet. Set one up on the Providers tab, then copy its model name from there.</p>' +
      '<div class="gs-row"><button onclick="gsLeaveToSetup()">Open the Providers tab</button></div>';
  }
  if (gs && gs.setMeUpDecision) h += '<p class="lm-muted">Quick decisions has its own provider in SkyrimNet. Its address is on the <span class="lm-link" onclick="gsGoDecisions()">Decisions tab</span>.</p>';
  return h + gsFooter('', '', 'Finish', 'gsLeave(true)');
}
async function gsLeaveToSetup() { await gsLeave(); }

function gsRender() {
  if (!gs) return;
  clearInterval(gsTimer); gsTimer = null;
  gsRestorePanel();                 // (the panel is moved back before the step is redrawn, so redrawing never throws it away)
  const body = [gsStepWelcome, gsStepSkyrim, gsStepAi, gsStepPrefs, gsStepSkyrimNet][gsStep]();
  document.getElementById('gsCard').innerHTML = gsHeader() + body;
  if (gsStep === 2) {
    if (document.getElementById('gsPanelHost')) gsMovePanelHere();
    gsRefreshAiBits();
    gsTimer = setInterval(async () => {                      // the green dot and the Next button follow the AI's real state (nothing is redrawn under typing)
      if (document.hidden) return;
      if (typeof helpRefreshStatus === 'function') await helpRefreshStatus();
      gsRefreshAiBits();
    }, 2500);
  }
}

// Any other tab leaves the step: the Providers tab's provider panel goes back where it lives.
(function () {
  const orig = showMainTab;
  showMainTab = function (which) { if (which !== 'start') { gsRestorePanel(); clearInterval(gsTimer); gsTimer = null; } return orig.apply(this, arguments); };
})();
function gsHash() {
  if ((location.hash || '') === '#get-started') { openGetStarted(); return true; }
  return false;
}
window.addEventListener('hashchange', gsHash);
gsHash();                  // (a first install opens on the Overview page, whose Setup box leads in here: nothing opens by itself)
"""
JS = JS.replace("__SN_WORDS__", json.dumps(skyrimnet_finder.WORDS))
