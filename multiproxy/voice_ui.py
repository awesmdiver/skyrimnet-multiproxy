"""The dashboard's "Voice" tab (2026-10-02): set up and run Higgs voice, on this device or on a Courier device, built to design/higgs-voice-mockup.html (sections 1 to 6;
section 6 is a warning with Install anyway, as the spec says) with the wording of the Gemini review. Plain strings in their own file, spliced into multiproxy.py's
page like the other tabs, so none of the CSS/JS needs doubled braces. Everything that came from the server or another device goes through esc() (defined by the Local
models card's script, on the same page) before it reaches HTML. The data comes from `GET /config/voice/overview` (voice_wiring.py) every 2 seconds while the tab is
open; the buttons call the plain routes next to it.
"""
import tab_desc_ui

VOICE_TAB_HTML = """<div class="tab" id="tabVoice" onclick="showMainTab('voice')">Voice</div>"""

VOICE_CSS = r"""
  /* Voice tab -- vc-* classes; the op-* dots, modal and log from the Other devices tab and the lm-* text styles are reused */
  .vc-row { display:flex; align-items:center; gap:14px; flex-wrap:wrap }
  .vc-grow { flex:1; min-width:220px }
  .vc-sub { margin-left:22px; padding:12px 0 2px; border-top:1px solid var(--line); margin-top:14px }
  .vc-sub h3 { margin:0 0 6px; font-size:0.9rem }
  .vc-meta { color:var(--muted); font-size:0.82rem; margin:2px 0 0 17px; line-height:1.5 }
  .vc-btns { display:flex; gap:6px; align-items:center; flex-wrap:wrap }
  .vc-pick { display:flex; gap:8px; align-items:center; flex-wrap:wrap }
  .vc-pick select { max-width:320px }
  .vc-warn { display:flex; gap:8px; align-items:flex-start; color:var(--muted); font-size:0.82rem; margin:12px 0 0; line-height:1.5 }
  .vc-warn .op-dot { margin-top:6px }
  .vc-warn > button { margin-left:auto; flex:none }
  .vc-warn > span.uc-r { margin-left:auto; text-align:right }          /* the update words sit right beside their button */
  .vc-warn > span.uc-r + button { margin-left:0 }          /* an action on a warning line (Update Courier) sits at the right edge, like Remove */
  .vc-bar { height:8px; border-radius:4px; background:var(--line); overflow:hidden; margin:10px 0 6px 17px }
  .vc-bar i { display:block; height:100%; background:var(--accent) }
  .vc-inline { display:flex; gap:8px; margin:6px 0 }
  .vc-inline input { flex:1; min-width:0; font-family:ui-monospace,Consolas,monospace; font-size:0.85rem }
  .vc-menu-wrap { position:relative }
  .vc-menu { position:absolute; right:0; top:100%; margin-top:6px; z-index:20; min-width:260px; background:var(--panel); border:1px solid var(--line2, var(--line)); border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,0.4); padding:6px }
  .vc-menu button { display:block; width:100%; text-align:left; background:transparent; border:0; border-radius:6px; padding:8px 10px; color:var(--text); font-size:0.82rem }
  .vc-menu button:hover { background:color-mix(in srgb, var(--text) 8%, transparent) }
  .vc-menu .vc-menu-note { color:var(--muted); font-size:0.75rem; padding:2px 10px 6px }
  .vc-other { margin:16px 0 0 22px; font-size:0.82rem }
  .vc-other .vc-orow { display:flex; align-items:center; gap:10px; padding:6px 0; border-top:1px solid var(--line); flex-wrap:wrap }
  .vc-dialog { background:var(--panel); border:1px solid var(--line2, var(--line)); border-radius:12px; padding:22px 26px; width:min(560px, 100%); display:flex; flex-direction:column; gap:10px; outline:none }
  .vc-dialog h3 { margin:0 0 2px; font-size:1.1rem }
  .vc-dialog p, .vc-dialog li { font-size:0.88rem; line-height:1.5; margin:0 }
  .vc-dialog ul { padding-left:20px; margin:4px 0; display:flex; flex-direction:column; gap:6px }
  .vc-chk { display:flex; gap:10px; align-items:flex-start; font-size:0.88rem; margin:10px 0 }
  .vc-chk input { margin-top:3px }
  .vc-ft { display:flex; justify-content:flex-end; gap:8px; margin-top:14px }
  .vc-how h3 { margin:0 0 4px; font-size:1rem }
  .vc-steps { margin:6px 0 14px 22px; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px; counter-reset:vc }
  .vc-steps > li { font-size:0.9rem; line-height:1.5 }
  .vc-steps > li::before { counter-increment:vc; content:counter(vc) ". "; font-weight:600 }
  /* Voice clips */
  .vc-clip { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:10px 0 2px 22px; margin-top:10px; border-top:1px solid var(--line) }
  .vc-clip .vc-grow { min-width:180px }
  .vc-clip.vc-bad .vc-voice { color:var(--muted) }
  .vc-clip .vc-words { font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:2px }
  .vc-voice { font-weight:600; overflow-wrap:anywhere }
  .vc-field label { display:block; font-size:0.78rem; color:var(--muted); margin:6px 0 4px }
  .vc-field input { width:100%; box-sizing:border-box }
  /* The three steps (Setup, Download, Install): one row, one step's content below it. Tabs and the copy icon never fill on hover (DESIGN.md). */
  .vc-stp { display:flex; margin:6px 0 18px; border-bottom:1px solid var(--line) }
  .vc-st, .vc-st:hover, .vc-st:active { flex:1; display:flex; gap:10px; align-items:center; text-align:left; font:inherit; font-size:0.9rem; padding:10px 14px 12px; margin-bottom:-1px; border:0; border-bottom:2px solid transparent; border-radius:0; background:none; color:var(--muted); cursor:pointer }
  .vc-st:hover { color:var(--text) }
  .vc-st small { display:block; color:var(--muted); font-size:0.78rem; font-weight:400 }
  .vc-st.done { color:var(--text) }
  .vc-st.now, .vc-st.now:hover { color:var(--text); border-bottom-color:var(--accent) }
  .vc-n { width:24px; height:24px; border-radius:50%; border:1px solid var(--line); display:inline-flex; align-items:center; justify-content:center; font-size:0.8rem; flex:none }
  .vc-st.done .vc-n { background:var(--ok); border-color:var(--ok); color:var(--bg) }
  .vc-st.now .vc-n { border-color:var(--accent); color:var(--accent-text) }
  .vc-st.done.now .vc-n { color:var(--bg) }
  .vc-pane { min-height:96px }
  .vc-lead { margin:0 0 6px }
  .vc-fld { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:10px 0 }
  .vc-lbl { color:var(--muted); min-width:110px }
  .vc-lic { display:flex; gap:10px; align-items:flex-start; margin:12px 0; padding:12px 14px; border:1px solid var(--line); border-radius:8px; background:var(--bg); font-size:0.9rem }
  .vc-lic input { margin-top:4px }
  .vc-barrow { display:flex; align-items:center; gap:12px; margin:8px 0 }
  .vc-bar2 { flex:1; height:8px; border-radius:6px; background:var(--line); overflow:hidden }
  .vc-bar2 i { display:block; height:100%; background:var(--accent) }
  .vc-pct { color:var(--muted); font-size:0.86rem; min-width:3.2em; text-align:right }
  .vc-fname { background:var(--bg); padding:1px 7px; border-radius:6px; border:1px solid var(--line); font-size:0.85rem; font-weight:400 }
  .vc-url { display:inline-flex; align-items:center; gap:14px; margin-left:17px; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:7px 12px; font-family:ui-monospace,Consolas,monospace; font-size:0.85rem }
  .vc-copyi, .vc-copyi:hover, .vc-copyi:active { background:none; border:0; padding:4px; border-radius:6px; display:inline-flex; color:var(--muted); cursor:pointer }
  .vc-copyi:hover { color:var(--text) }
  .vc-kept { margin-top:14px; padding-top:12px; border-top:1px solid var(--line) }
  @media (max-width:640px) { .vc-st small { display:none } .vc-st { padding:10px 8px } }
"""

VOICE_PANE_HTML = """
  <div id="paneVoice" style="display:none">
""" + tab_desc_ui.html("voice") + """    <div id="vcModalBox" class="op-modal" style="display:none" onclick="if (event.target === this) vcCloseModal()"></div>
    <div id="vcBody"><div class="lm-muted">Looking at your devices…</div></div>
  </div>
"""

VOICE_JS = r"""// ---------------- Voice tab ----------------
let vcOv = null, vcTimer = null, vcLoading = false;
let vcFit = {};              // device key ('this' or a device's name) -> { fit, error, at }
let vcPick = null;           // a device chosen in "Run it on" that Higgs isn't installed on yet (null: the card follows where Higgs runs)
let vcSwitching = null;       // a voice or device change waiting for the other device's answer: { kind: 'model'|'pc', k, model, pc, was } (the screen shows the new choice at once, with "Switching...")
let vcSwitchWarn = '';       // the one line shown when a switch did not work
const VC_SWITCH_LIMIT_MS = 20000;
let vcModelPick = null;      // a voice model chosen in "Voice" that isn't installed on the shown device yet (null: the card follows the model that device uses)
let vcPaused = {}, vcWas = {}, vcFinished = {}, vcDismiss = {};   // device key -> paused download / saw it install / ended on "set up" / a failed-check message the player closed
let vcLook = null, vcSig = '';   // a finished step being looked at again; which step the card was last on
let vcModal = null;          // { type: 'license' | 'uninstall' | 'clear' | 'log', key, ... }
let vcTick = {}, vcMenu = false, vcMoving = false;
let vcNote = '', vcErr = '', vcCarry = '';   // vcCarry: the "clips came along" line (lives until the tab is reloaded)
let vcSamples = {};          // device key -> [[seconds, bytes done]] (for an honest "time left")
let vcCancelled = {};        // device key -> true once its install was cancelled (until the next one starts)
let vcTest = { busy: false, note: '', warn: '' };      // the Test button: making a line / what to say after it (note: nothing was made, warn: it failed)
let vcChange = null;                                  // Change voice…: { k: the device, old: the voice being replaced } from the player's yes until the new voice is in and the old one is removed
let vcTestVoice = '';                                 // which of the player's own clips the Test line uses (only asked when there is more than one)
let vcSttErr = '';
let vcClips = null, vcClipsTick = 0, vcClipErr = '', vcClipNote = '', vcPlaying = '', vcAudio = null;

const VC_HOW = [
  '<b>Setup</b>: Choose a voice and which device will run it, tick the license box, and click Next: Download.',
  '<b>Download</b>: Wait for the big voice file to save to your main device (you can leave this page).',
  '<b>Install</b>: Everything sets itself up automatically and gives you an address to copy when ready.',
  '<b>SkyrimNet</b>: When the voice is ready, its card shows the steps for SkyrimNet, written for the voice you chose. They end with <b>Save Changes</b> and <b>Test Voice</b>.'
];
function vcRich(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>'); }
function vcSnSteps(i, thisAddress) {           // the SkyrimNet settings steps, in SkyrimNet's own words, chosen by the installed voice: Higgs has its own engine in SkyrimNet; Qwen3-TTS and Chatterbox go through its OpenAI-Compatible one (the Model names the voice)
  const key = (i && i.model) || 'higgs';
  const open = 'In SkyrimNet, open <b>Voice</b> &gt; <b>Text-to-Speech</b>.';
  const save = 'Press <b>Save</b>, then <b>Test Voice</b>.';
  const steps = key === 'higgs' ? [open,
    'Set <b>TTS Engine</b> to <b>Higgs</b>, then open the <b>Higgs</b> tab.',
    'Paste ' + thisAddress + ' into <b>Endpoint</b> and check that <b>tts.model</b> says <code>higgs</code>.',
    'Turn <b>Enable Audio Tags</b> on.', save] : [open,
    'Set <b>TTS Engine</b> to <b>OpenAI-Compatible</b>, then open the <b>OpenAI-Compatible</b> tab.',
    'Set <b>Service</b> to <b>Self-hosted</b> and paste ' + thisAddress + ' into <b>Endpoint</b>.',
    'Type <code>' + esc(key) + '</code> into <b>Model</b> and leave <b>API Key</b> empty.', save];
  return '<ol class="vc-steps">' + steps.map(t => '<li>' + t + '</li>').join('') + '</ol>';
}
const VC_CLIPS_WHAT = [
  'Voice clips are short recordings that the voice copies to give characters a unique sound.',
  'Adding your own custom clips replaces the game\'s flat defaults with clearer, much better voices.'
];
const VC_CLIPS_STEPS = [
  'Click "Add a clip..." and type the voice\'s name.',
  'Choose a WAV recording from your computer.',
  'Click "Play" to hear your new custom voice.'
];
const VC_CLIPS_TIP = 'Tip: The best clips are 8 to 15 seconds long, feature one voice in a quiet room, have no music, and are saved as a WAV file.';
function vcGb(bytes) { return (bytes / 1073741824).toFixed(1); }
// ---- which voice model: Higgs, Qwen3-TTS or Chatterbox (each PC has one chosen; the other installed ones stay on disk) ----
function vcModels() { return (vcOv && vcOv.models) || []; }
function vcMN(key) { const m = vcModels().find(x => x.key === key); return m ? m.name : 'Higgs voice'; }
function vcMRow(key) { return vcModels().find(x => x.key === key) || { key: 'higgs', name: 'Higgs voice', label: 'Higgs', oneLine: '', licenseWho: 'Boson AI', licenseUrl: (vcOv || {}).licenseUrl || '', licenseNote: '', sizeBytes: 0 }; }
function vcActiveModel(k) {                      // the model that PC uses now
  if (k === 'this') return (vcOv.thisEngine || {}).model || vcOv.model || 'higgs';
  const p = (vcOv.pcs || []).find(x => x.id === k);
  return (p && p.voice && p.voice.model) || 'higgs';
}
function vcInstalledModels(k) {
  if (k === 'this') return (vcOv.thisPc || {}).installedModels || [];
  const p = (vcOv.pcs || []).find(x => x.id === k);
  return (p && p.voice && p.voice.installedModels) || [];
}
function vcCanRun(p, model) { return !!p && p.hasVoice !== false && (p.supportedModels || ['higgs']).indexOf(model) >= 0; }          // (a Courier that lists no voices runs Higgs only)
function vcModelOf(k) { return (vcSwitching && vcSwitching.kind === 'model' && vcSwitching.k === k && vcSwitching.model) || vcModelPick || vcActiveModel(k); }          // the model the card is about
function vcFitKey(k) { return k + '|' + vcModelOf(k); }
function vcMbGb(mb) { return (mb / 1024).toFixed(1); }
function vcCard(gpu) { return gpu && gpu.name ? opShortCard(gpu.name) + (gpu.memoryGb ? ', ' + Math.round(gpu.memoryGb) + ' GB' : '') : ''; }
function vcShowPc(k) { const p = ((vcOv && vcOv.pcs) || []).find(x => x.id === k); return (p && p.name) || k; }          // a device's name as typed or found (MSI); its id (msi) is only the key
function vcPcName(key) { return key === 'this' ? 'this device' : vcShowPc(key); }
function vcCarryText(n, err, pc) {
  if (err) return err;
  return n > 0 ? n + ' of your clips came along to ' + vcPcName(pc) + '.' : '';
}

// ---- loading ----
function vcOpened() {
  try { history.replaceState(null, '', '#voice'); } catch (e) {}
  vcLoad();
  if (!vcTimer) vcTimer = setInterval(vcLoad, 2000);
}
try { document.addEventListener('visibilitychange', () => { if (!document.hidden && vcTimer) vcLoad(); }); } catch (e) {}
function vcClosed() {
  if (vcTimer) { clearInterval(vcTimer); vcTimer = null; }
  try { if (location.hash === '#voice') history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
}
async function vcLoad() {
  if (vcLoading || document.hidden) return;                  // (a hidden browser tab asks for nothing; it refreshes the moment it is shown again)
  vcLoading = true;
  try {
    const r = await fetch('/config/voice/overview');
    if (!r.ok) throw new Error('overview ' + r.status);
    vcOv = await r.json();
    if (vcOv.carried) vcCarry = vcCarryText(vcOv.carried.count, vcOv.carried.error, vcOv.carried.pc);
    vcErr = vcErr === 'LOAD' ? '' : vcErr;
    if (vcPick && vcPick === vcOv.location) vcPick = null;
    vcChangeFinish();
    if (vcModelPick && vcActiveModel(vcShownKey()) === vcModelPick) vcModelPick = null;
    vcWantFit();
  } catch (e) { if (!vcOv) vcErr = 'LOAD'; }
  vcLoading = false;
  if (vcOv && (!vcClips || ++vcClipsTick % 2 === 0 || vcClips.pc !== vcOv.location)) await vcLoadClips();
  vcRender();
}
function vcShownKey() {
  const ov = vcOv;
  if (vcSwitching && vcSwitching.kind === 'pc') return vcSwitching.pc;
  if (ov.thisEngine && ov.thisEngine.install && ov.thisEngine.install.state === 'running') return 'this';
  for (const p of ov.pcs || []) {
    if ((p.job && p.job.state === 'running') || (p.voice && p.voice.install && p.voice.install.state === 'running')) return p.id;
  }
  return vcPick || ov.location || 'this';
}
function vcWantFit() {
  const k = vcShownKey(), i = vcInfo(k);
  if (!i || i.installed || i.job || i.online === false) return;
  const fk = vcFitKey(k), m = vcModelOf(k);
  const f = vcFit[fk];
  if (f && f.loading) return;
  if (f && Date.now() - f.at < 15000) return;
  vcFit[fk] = { ...(f || {}), loading: true, at: f ? f.at : 0 };
  const done = (fit, error) => { vcFit[fk] = { fit, error, at: Date.now() }; vcRender(); };
  if (k === 'this') fetch('/config/voice/fit?model=' + encodeURIComponent(m)).then(r => r.json()).then(d => done(d, ''), () => done(null, "Couldn't check this device. Try again."));
  else fetch('/config/voice/pc?id=' + encodeURIComponent(k) + '&model=' + encodeURIComponent(m)).then(r => r.json()).then(d => done(d.fit || null, d.fitError || (d.fit ? '' : (d.detail || "Couldn't check that device. Try again."))),
    () => done(null, "Couldn't check that device. Try again."));
}

// ---- what is known about one device ----
function vcInfo(key) {
  const ov = vcOv;
  let i;
  if (key === 'this') {
    const e = ov.thisEngine || {}, t = ov.thisPc || {}, inst = e.install || null;
    i = { key, online: true, hasVoice: true, installed: !!e.installed, state: e.state, label: e.label, detail: e.detail, ready: !!e.ready,
      gpuMb: e.gpuMemoryMb, lastMs: e.lastLineMs, waiting: e.linesWaiting || 0, licenseOk: !!t.licenseOk, gpu: t.gpu, ramGb: t.ramGb, autostart: !!t.autostart,
      job: inst && inst.state === 'running' ? { step: inst.step, done: inst.done, total: inst.total, speed: 0, message: '' } : null,
      jobError: inst && inst.state === 'error' ? inst.error : '', jobReason: inst && inst.state === 'error' ? (inst.reason || '') : '', jobBad: inst && inst.state === 'error' ? (inst.badFile || '') : '', jobFailStage: 'this',
      jobDone: !!(inst && inst.state === 'done'), died: e.state === 'died' };
  } else {
    const p = (ov.pcs || []).find(x => x.id === key);
    if (!p) return null;
    const v = p.voice || {}, j = p.job, vi = v.install;
    let job = null, jobError = '', jobReason = '';
    if (j && j.state === 'running') job = { step: null, stage: j.stage, done: j.done, total: j.total, speed: j.speed_bps || 0, message: j.message || '' };
    else if (vi && vi.state === 'running') job = { step: null, stage: 'installing', done: vi.done, total: vi.total, speed: 0, message: '' };
    if (j && j.state === 'error') { jobError = j.error; jobReason = j.reason || ''; } else if (vi && vi.state === 'error') { jobError = vi.error; jobReason = vi.reason || ''; }
    i = { key, online: p.online, hasVoice: p.hasVoice, installed: !!v.installed, state: v.state || 'not_installed', label: v.label, detail: v.detail, ready: !!v.ready,
      gpuMb: v.gpuMemoryMb, lastMs: v.lastLineMs, waiting: v.linesWaiting || 0, licenseOk: !!(ov.thisPc || {}).licenseOk, gpu: p.gpu, ramGb: p.ramGb, autostart: !!p.autostart, job, jobError, jobReason,
      jobBad: j && j.state === 'error' ? (j.badFile || '') : '', jobFailStage: j ? j.stage : '', jobDone: !!((j && j.state === 'done') || (vi && vi.state === 'done')), died: v.state === 'died', pcRow: p };
  }
  i.activeModel = vcActiveModel(key); i.model = vcModelOf(key); i.modelName = vcMN(i.activeModel);
  i.canRun = key === 'this' || vcCanRun(i.pcRow, i.model);
  if (i.model !== i.activeModel) {                          // a voice that is not installed on that PC yet is being set up: the card is about that one
    i.installed = vcInstalledModels(key).includes(i.model); i.modelName = vcMN(i.model);
    i.licenseOk = !!(((vcOv.thisPc || {}).licenses || {})[i.model]);
  } else if (!i.licenseOk || key !== 'this') {
    i.licenseOk = !!(((vcOv.thisPc || {}).licenses || {})[i.model]);
  }
  if (vcDismiss[key] && vcDismiss[key] === i.jobBad) { i.jobBad = ''; i.jobError = ''; i.jobReason = ''; }
  if (key === ov.location && ov.status) {                  // where lines go: the routed status knows the queue and an unreachable device
    i.waiting = ov.status.linesWaiting || 0;
    if (ov.status.state === 'unreachable') { i.state = 'unreachable'; i.detail = ov.status.detail; i.ready = false; }
    else if (i.ready && ov.status.label && /^Busy/.test(ov.status.label)) i.busyLabel = ov.status.label;
  }
  return i;
}

// ---- words ----
function vcSpeedEta(key, done, total, bps) {
  const now = Date.now() / 1000;
  const s = (vcSamples[key] = (vcSamples[key] || []).filter(x => now - x[0] < 30 && x[1] <= done));
  if (!s.length || now - s[s.length - 1][0] >= 1) s.push([now, done]);
  let rate = bps > 0 ? bps : 0;
  if (!rate && s.length >= 2) {
    const a = s[0], b = s[s.length - 1];
    if (b[0] - a[0] >= 8 && b[1] > a[1]) rate = (b[1] - a[1]) / (b[0] - a[0]);
  }
  if (!rate) return '';
  const mb = rate / 1048576;
  let t = (mb >= 10 ? Math.round(mb) : mb.toFixed(1)) + ' MB/s';
  return t;
}
function vcTight(i, fit) {
  // The card's own memory is the clear case ("has 4 GB; needs about 6"); when the card is big and only busy right now, the engine's own plain warning says so.
  const flags = fit.flags || [];
  const vramWarn = flags.includes('vram') || (!fit.flags && (fit.warnings || []).some(w => /graphics memory/.test(w)));
  if (!vramWarn) return null;
  const total = i.gpu && i.gpu.memoryGb;
  if (total && total < 6) {
    const who = i.key === 'this' ? "This device's" : esc(i.key) + "'s";
    return who + ' graphics card may be too small. Voice lines might be slow and the game may stutter.';
  }
  return (fit.warnings || []).find(w => /graphics memory/.test(w)) || null;
}

// ---- the card: three steps in one row (Setup, Download, Install), one step's content at a time ----
function vcDot(cls, title) { return opDot(cls, title); }
const VC_STEPS = [['Setup', 'The voice, where it runs, and the license'], ['Download', 'Saved on this device'], ['Install', 'Send it to the PC where it runs']];
function vcPickRow(k, i) {
  const ov = vcOv, pcs = ov.pcs || [];
  const label = (name, gpu, extra) => name + (vcCard(gpu) ? ' (' + vcCard(gpu) + ')' : '') + extra;
  const opts = ['<option value="this"' + (k === 'this' ? ' selected' : '') + '>' + esc(label('This device', (ov.thisPc || {}).gpu, (ov.thisEngine || {}).installed ? ' · installed' : '')) + '</option>']
    .concat(pcs.map(p => '<option value="' + esc(p.id) + '"' + (k === p.id ? ' selected' : '') + (p.online ? '' : ' disabled') + '>' +
      esc(label(p.name || p.id, p.gpu, (p.voice && p.voice.installed ? ' · installed' : '') + (p.online ? '' : ' · offline') + (vcCanRun(p, vcModelOf(k)) ? '' : ' · needs updating'))) + '</option>'));
  let h = '<label class="vc-lbl" for="vcSelPc">Run voice on</label>';
  if (pcs.length) h += '<select id="vcSelPc" onchange="vcChoosePc(this.value)">' + opts.join('') + '</select>';
  else h += '<span id="vcSelPc">' + esc(label('This device', (ov.thisPc || {}).gpu, '')) + '</span>';
  return h;
}
function vcModelRow(k, i) {
  const installed = vcInstalledModels(k), cur = i.model;
  const opts = vcModels().map(m => '<option value="' + esc(m.key) + '"' + (m.key === cur ? ' selected' : '') + '>' + esc(m.label + (installed.includes(m.key) ? ' · installed' : '') + (vcChange && vcChange.k === k && m.key === vcChange.old ? ' · current' : '')) + '</option>').join('');
  const row = vcMRow(cur);
  return '<label class="vc-lbl" for="vcSelModel">Voice</label><select id="vcSelModel" onchange="vcChooseModel(this.value)">' + opts + '</select>' +
    '<div class="lm-muted" style="margin:4px 0 0">' + esc(row.oneLine) + (row.sizeBytes ? ' About ' + vcGb(row.sizeBytes) + ' GB to download.' : '') + '</div>';
}
function vcStepper(now, finished) {
  let h = '<div class="vc-stp" role="tablist">';
  VC_STEPS.forEach((s, n) => {
    const done = finished || n < now, on = (!finished && n === now) || vcLook === n;
    h += '<button class="vc-st' + (done ? ' done' : '') + (on ? ' now' : '') + '" role="tab" aria-selected="' + on + '"' +
      (done ? ' onclick="vcLookAt(' + n + ')"' : ' tabindex="-1" aria-disabled="true" style="cursor:default"') + '>' +
      '<span class="vc-n">' + (done ? '&#10003;' : n + 1) + '</span><span><b>' + s[0] + '</b><small>' + s[1] + '</small></span></button>';
  });
  return h + '</div>';
}
function vcPhase(k, i) {                        // which step a running install is on: 1 Download, 2 Install
  const j = i.job;
  if (k === 'this') return j.step === 'warmup' ? 2 : 1;
  return j.stage === 'preparing' ? 1 : 2;
}
function vcFailPhase(k, i) { return k === 'this' || i.jobFailStage === 'preparing' ? 1 : 2; }
function vcBarRow(pct, bad) {
  return '<div class="vc-barrow"><div class="vc-bar2"><i style="width:' + pct + '%' + (bad ? ';background:var(--err)' : '') + '"></i></div><span class="vc-pct">' + pct + '%</span></div>';
}
function vcGate(k, i) {                         // can "Next: Download" be pressed for this device, and what to tell the player
  const f = vcFit[vcFitKey(k)] || {}, fit = f.fit;
  if (i.hasVoice === false) {
    const uc = ucInline(k, vcShowPc(k));            // a Courier that can take the update gets the button; one that cannot keeps the old advice
    return { ok: false, busy: '', notes: '<div class="vc-warn">' + vcDot('warn') + '<span' + (uc.btn ? ' class="uc-r"' : '') + '>' + (uc.btn
      ? esc(vcShowPc(k)) + ' has an older version of MultiProxy Courier.' + (uc.text ? ' ' + uc.text : '')
      : esc(vcShowPc(k)) + " has an older MultiProxy Courier. Update it (use the newest MultiProxy Courier download) and come back.") + '</span>' + uc.btn + '</div>' };
  }
  if (i.online === false) return { ok: false, busy: '', notes: '<div class="vc-warn">' + vcDot('off') + '<span>' + esc(vcShowPc(k)) + " isn't answering. It may be asleep or turned off.</span></div>" };
  if (i.canRun === false) { const uc = ucInline(k, vcShowPc(k)); return { ok: false, busy: '', notes: '<div class="vc-warn">' + vcDot('warn') + '<span' + (uc.btn ? ' class="uc-r"' : '') + '>' + 'Courier on ' + esc(vcShowPc(k)) + ' needs an update.' + (uc.text ? ' ' + uc.text : '') + '</span>' + uc.btn + '</div>' }; }
  if (f.loading && !fit) return { ok: false, busy: 'Checking ' + esc(vcPcName(k)) + '…', notes: '' };
  if (!fit) return { ok: false, busy: '', notes: '<div class="vc-warn">' + vcDot('warn') + '<span>' + esc(f.error || "Couldn't check that device. Try again.") + '</span></div>' };
  if (fit.verdict === 'refuse') {
    return { ok: false, busy: '', notes: (fit.reasons || []).map(r => '<div class="vc-warn">' + vcDot('off') + '<span>' + esc(r.replace(/^This device's/, k === 'this' ? "This device's" : k + "'s")) + '</span></div>').join('') +
      ((vcOv.pcs || []).length ? '<div class="vc-warn"><span>Pick another device in <b>Run voice on</b>, if you have one.</span></div>' : '') };
  }
  let notes = '';
  if (fit.verdict === 'warn') {
    const tight = vcTight(i, fit);
    if (tight) notes += '<div class="vc-warn">' + vcDot('warn') + '<span>' + (tight.indexOf('graphics card may be too small') >= 0 ? tight : esc(tight)) + '</span></div>';
    (fit.warnings || []).filter(w => !/graphics memory/.test(w) || !tight).forEach(w => { notes += '<div class="vc-warn">' + vcDot('warn') + '<span>' + esc(k === 'this' ? w : w.replace(/^This device will/, k + ' will')) + '</span></div>'; });
  }
  return { ok: true, busy: '', notes };
}
function vcPaneSetup(k, i) {
  const gate = vcGate(k, i), lic = !!i.licenseOk, ticked = lic || !!vcTick[i.model];       // the box is per voice: ticking one never ticks another
  const mrow = vcMRow(i.model);
  let h = '<div class="vc-fld">' + vcModelRow(k, i) + '</div><div class="vc-fld">' + vcPickRow(k, i) + '</div>' + gate.notes;
  if (k === 'this') h += '<div class="vc-warn">' + vcDot('warn') + '<span>On this PC, it shares your graphics card with Skyrim and any local AI models. Using another PC keeps all that memory free for the game.</span></div>';
  const read = '<a class="lm-link" href="' + esc(mrow.licenseUrl) + '" target="_blank" rel="noopener noreferrer">Read it</a>' + (mrow.licenseNote ? '<br><span class="lm-muted">' + esc(mrow.licenseNote) + '</span>' : '');
  if (lic) h += '<div class="vc-lic" id="vcLicDone"><span>You accepted the ' + esc(mrow.licenseWho) + ' license for ' + esc(mrow.label) + '. ' + read + '</span></div>';      // already accepted: a plain line for this voice only
  else h += '<label class="vc-lic"><input type="checkbox" id="vcTickBox" data-model="' + esc(i.model) + '"' + (ticked ? ' checked' : '') + ' onchange="vcTickChange(this)"><span>I have read and agree to the <a class="lm-link" href="' + esc(mrow.licenseUrl) + '" target="_blank" rel="noopener noreferrer">' + esc(mrow.licenseWho) + ' license</a>.' + (mrow.licenseNote ? ' <span class="lm-muted">' + esc(mrow.licenseNote) + '</span>' : '') +
    '<br><span class="lm-muted">You only need to agree once per voice, and it covers every PC you install ' + esc(mrow.label) + ' on.</span></span></label>';
  const same = !!(vcChange && vcChange.k === k && i.model === vcChange.old);          // changing the voice, but still on the one it has: nothing to download
  h += '<div class="vc-fld"><button class="primary" id="vcNext" data-gate="' + (gate.ok && !same ? 1 : 0) + '"' + (gate.ok && ticked && !same ? '' : ' disabled') + ' onclick="vcNext(\'' + jsq(k) + '\')">Next: Download</button>' +
    (vcChange && vcChange.k === k ? '<button class="ghost" onclick="vcChangeCancel()">Cancel</button>' : '') +
    (same ? '<span class="lm-muted">Pick a different voice to change to.</span>' : (gate.busy ? '<span class="lm-muted">' + gate.busy + '</span>' : '')) + '</div>';
  return h;
}
function vcPaneDownload(k, i) {
  const j = i.job;
  let label, done, total;
  if (k === 'this') {
    const modelBytes = vcOv.modelBytes || 0, engineBytes = Math.max(0, j.total - modelBytes);
    if (j.step === 'engine') { done = j.done; total = engineBytes; label = 'Voice engine'; }
    else { done = Math.max(0, j.done - engineBytes); total = modelBytes; label = i.modelName + ' model'; }
  } else { done = j.done; total = j.total; label = i.modelName + ' files'; }
  const pct = total ? Math.min(100, Math.round(done * 100 / total)) : 0;
  const rate = vcSpeedEta(k, j.done, j.total, j.speed);
  const left = j.left ? ' · ' + j.left : '';                // worded and measured by the server (download_meter.py): nothing until the speed is known
  return '<p class="vc-lead">' + label + (total ? ' (' + vcGb(total) + ' GB) · ' + vcGb(done) + ' of ' + vcGb(total) + ' GB' + left : '') + '</p>' + vcBarRow(pct) +
    (rate ? '<div class="lm-muted">' + rate + '</div>' : '') +
    '<div class="vc-fld"><button class="ghost" onclick="vcPause(\'' + jsq(k) + '\')">Pause</button><button class="ghost" onclick="vcCancel(\'' + jsq(k) + '\')">Cancel</button>' +
    '<span class="lm-muted">You can leave this page. It keeps going and moves on to Install by itself.</span></div>';
}
function vcPanePaused(k, i) {
  const part = ((vcOv.downloads || {}).partials || [])[0];
  return '<p class="vc-lead">Paused' + (part ? ' · ' + vcGb(part.size) + ' GB downloaded' : '') + '</p>' +
    '<div class="vc-fld"><button class="primary" onclick="vcResume(\'' + jsq(k) + '\')">Resume</button><button class="ghost" onclick="vcStopPause(\'' + jsq(k) + '\')">Cancel</button>' +
    '<span class="lm-muted">Your download progress is saved.</span></div>';
}
function vcPaneInstall(k, i) {
  const j = i.job;
  if (k !== 'this' && j.stage === 'copying') {
    const pct = j.total ? Math.min(100, Math.round(j.done * 100 / j.total)) : 0;
    return '<p class="vc-lead">Copying from this device to ' + esc(vcShowPc(k)) + '…</p>' + vcBarRow(pct) +
      '<div class="vc-fld"><button class="ghost" onclick="vcCancel(\'' + jsq(k) + '\')">Cancel</button></div>';
  }
  return '<p class="vc-lead">Installing on ' + (k === 'this' ? 'this device' : esc(vcShowPc(k))) + '...</p>';
}
function vcPaneBad(k, i) {
  let h = '';
  if (k !== 'this' && vcFailPhase(k, i) === 2) h += '<p class="vc-lead">Copying from this device to ' + esc(vcShowPc(k)) + '…</p>' + vcBarRow(100, true);
  h += '<p class="vc-lead">' + vcDot('off') + '<b>Checksum verification failed for <code class="vc-fname">' + esc(i.jobBad) + '</code>.</b> ' +
    '<span class="lm-muted">The downloaded file doesn\'t match the official release and might be damaged.</span></p>' +
    '<div class="vc-fld"><button class="primary" onclick="vcRedownload(\'' + jsq(k) + '\', \'' + jsq(i.jobBad) + '\')">Download it again</button>' +
    '<button class="ghost" onclick="vcDismissBad(\'' + jsq(k) + '\')">Cancel</button></div>';
  return h;
}
function vcCopyIcon(btn) {
  const v = (document.getElementById('vcAddr') || {}).textContent || '';
  try { navigator.clipboard.writeText(v); } catch (e) {}
  const o = btn.innerHTML; btn.textContent = 'Copied'; setTimeout(() => { btn.innerHTML = o; }, 1200);
}
const VC_COPY_SVG = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>';
function vcPaneDone(k, i) {
  const msg = k === 'this' ? esc(i.modelName) + ' is set up on this device.' : 'The remote device (' + esc(vcShowPc(k)) + ') is set up to run ' + esc(i.modelName) + '.';
  return '<p class="vc-lead">' + vcDot('on') + '<b>' + msg + '</b></p>' +
    '<div class="lm-muted" style="margin:2px 0 10px 17px">' + vcSnSteps(i, 'the address below') + '</div>' +
    '<div class="vc-url"><span id="vcAddr">' + esc(vcOv.address) + '</span><button class="vc-copyi" title="Copy" aria-label="Copy the address" onclick="vcCopyIcon(this)">' + VC_COPY_SVG + '</button></div>';
}
function vcPaneLook(k, i, n) {
  const where = k === 'this' ? 'this device' : esc(vcShowPc(k));
  if (n === 0) return '<p class="vc-lead">Voice runs on <b>' + where + '</b>. The license is accepted.</p>';
  if (n === 1) return '<p class="vc-lead">The voice files are downloaded and saved on this device.</p>';
  return '<p class="vc-lead">Installed on <b>' + where + '</b>.</p>';
}
function vcKeptSteps() {
  const offer = vcOv.moveOffer;
  let h = '<div class="vc-kept"><span class="lm-muted">MultiProxy keeps downloads on this PC so it can send them to your other devices.</span>' +
    '<div class="vc-fld" style="margin-top:6px"><code>' + esc(vcOv.downloadsFolder || '') + '</code><button class="ghost" onclick="vcOpenKept()">Open folder</button>' +
    '<button class="ghost" onclick="vcChangeKept()">Change folder…</button></div>';
  if (offer) h += '<div class="vc-warn" style="margin-top:10px">' + vcDot('warn') + '<span>Move your kept voice downloads to <code>' + esc(offer.to) + '</code>?</span></div>' +
    '<div class="vc-inline" style="margin-top:6px"><button class="primary" onclick="vcMoveKept(true)">Move</button><button class="ghost" onclick="vcMoveKept(false)">Not now</button></div>';
  return h + '</div>';
}
function vcTrack(k, i) {                        // remember an install we watched run, so the card can end on "set up" instead of jumping to the running card
  if (i.job) { vcWas[k] = true; return; }
  if (vcWas[k]) {
    if (i.installed) { vcFinished[k] = true; delete vcWas[k]; }
    else if (!i.jobDone) delete vcWas[k];
  }
  if (!i.installed && !vcWas[k]) delete vcFinished[k];
}
function vcWantSteps(k, i) {
  return !!((vcChange && vcChange.k === k) || i.job || i.jobBad || vcFinished[k] || (vcWas[k] && i.jobDone) || (!i.installed && !['starting', 'getting_ready', 'unreachable'].includes(i.state)));
}
// A failed install, on every step: the red line, why it failed when that is known, and the Log button for the device the install was for.
function vcFailLine(k, i) {
  const why = i.jobReason ? '<div class="lm-muted vc-why">' + esc(i.modelName) + " didn't finish installing on " + esc(vcPcName(k)) + ': ' + esc(i.jobReason) + '</div>' : '';
  return '<div class="vc-failed" style="margin:0 0 10px"><div class="vc-inline"><div class="lm-err vc-grow">' + esc(i.jobError) + '</div>' +
    '<button onclick="vcOpenLog(\'' + jsq(k) + '\')">Log</button></div>' + why + '</div>';
}
function vcSteps(k, i) {
  const finished = !!(vcFinished[k] || (vcWas[k] && i.jobDone && !i.job));
  let now, pane;
  if (finished) { now = 3; pane = vcPaneDone(k, i); }
  else if (i.job) { now = vcPhase(k, i); pane = now === 1 ? vcPaneDownload(k, i) : vcPaneInstall(k, i); }
  else if (i.jobBad) { now = vcFailPhase(k, i); pane = vcPaneBad(k, i); }
  else if (vcPaused[k]) { now = 1; pane = vcPanePaused(k, i); }
  else { now = 0; pane = vcPaneSetup(k, i); }
  const sig = k + ':' + now + (finished ? 'f' : '');
  if (sig !== vcSig) { vcSig = sig; vcLook = null; }
  if (vcLook !== null && (finished || vcLook < now)) pane = vcPaneLook(k, i, vcLook); else vcLook = null;
  let h = vcStepper(now, finished) + '<div class="vc-pane">';
  if (!i.job && !finished && i.jobError && !i.jobBad) h += vcFailLine(k, i);
  if (vcNote && !finished) h += '<div class="lm-muted" style="margin:0 0 10px">' + esc(vcNote) + '</div>';
  h += pane + '</div>';
  if (!finished && now <= 1) h += vcKeptSteps();
  return h;
}
function vcKept() {
  const d = vcOv.downloads || {}, kept = d.totalBytes || 0, offer = vcOv.moveOffer;
  let h = '<div class="vc-sub"><div class="vc-meta" style="margin-left:0"><b>Downloads on this PC: ' + (kept > 0 ? vcGb(kept) + ' GB' : 'none yet') + '.</b></div>' +
    '<div class="vc-meta" style="margin-left:0"><code>' + esc(vcOv.downloadsFolder || '') + '</code></div>';
  if (!(vcOv.thisEngine || {}).install || (vcOv.thisEngine.install || {}).state !== 'running') {
    (d.partials || []).forEach(p => { h += '<div class="vc-meta" style="margin-left:0">Half-downloaded: ' + esc(p.name) + ' (' + vcGb(p.size) + ' GB). It carries on next time you set it up.</div>'; });
  }
  h += '<div class="vc-inline" style="margin-top:8px"><button onclick="vcOpenKept()">Open folder</button><button class="ghost" onclick="vcChangeKept()">Change folder…</button></div>';
  if (offer) h += '<div class="vc-warn" style="margin-top:10px">' + vcDot('warn') + '<span>Move your kept voice downloads to <code>' + esc(offer.to) + '</code>?</span></div>' +
    '<div class="vc-inline" style="margin-top:6px"><button class="primary" onclick="vcMoveKept(true)">Move</button><button class="ghost" onclick="vcMoveKept(false)">Not now</button></div>';
  return h + '</div>';
}
function vcOpenKept() { vcDo(() => lmPost('/config/voice/downloads/open-folder')); }
async function vcChangeKept() {
  vcMenu = false;
  try {
    const r = await lmPost('/config/settings/pick-folder', { what: 'voicedownloads' });
    if (r && r.path) await lmPost('/config/voice/folders', { downloadsFolder: r.path });
  } catch (e) { vcErr = e.message; }
  await vcLoad();
}
function vcMoveKept(go) {
  vcDo(async () => {
    const d = await lmPost('/config/voice/downloads/move', { move: go });
    vcNote = go ? (d.left ? 'Moved ' + d.moved + ' file(s). ' + d.left + ' couldn\'t be checked and stayed where they were.' : 'Moved.') : '';
  });
}
function vcFacts(i) {
  const bits = [];
  if (i.state === 'ready') {
    bits.push(i.busyLabel ? i.busyLabel + '.' : 'Ready.');
  }
  return bits.join(' ');
}
function vcRunningCard(k, i, finished) {
  const name = k === 'this' ? '' : ' on ' + esc(vcShowPc(k));
  let dot = 'on', text = '', extra = '';
  if (i.state === 'ready') text = vcFacts(i);
  else if (i.state === 'stopped') { dot = 'idle'; text = 'Stopped.'; }
  else if (i.state === 'starting' || i.state === 'getting_ready') { dot = 'warn'; text = esc(i.detail || 'Starting…'); }
  else if (i.state === 'too_slow') { dot = 'warn'; text = esc(i.detail || 'This graphics card is too slow for live talk.'); }
  else if (i.state === 'unreachable') { dot = 'off'; text = esc(i.detail || (k + " isn't answering.")); }
  else if (i.state === 'died') {
    dot = 'off';
    text = vcRich(i.detail || (i.modelName + ' stopped.'));
  } else { dot = 'off'; text = vcRich(i.detail || i.label || (i.modelName + ' has a problem.')); }
  const canStart = ['stopped', 'died', 'error'].includes(i.state);
  let btns = '';
  if (canStart) btns += '<button class="primary" onclick="vcAct(\'' + jsq(k) + '\', \'start\')">Start</button>';
  else if (i.state !== 'unreachable') btns += '<button onclick="vcAct(\'' + jsq(k) + '\', \'stop\')">Stop</button>';
  if (i.state === 'died' && k === 'this') btns += '<button onclick="vcOpenFolder()">Open folder</button>';
  if (i.state === 'too_slow') btns += '<button class="primary" onclick="vcStartMove()">Move to another device</button>';
  if (i.state === 'ready') btns += vcTestButtons();
  if (i.state !== 'unreachable') btns += '<button onclick="vcOpenLog(\'' + jsq(k) + '\')">Log</button><button onclick="vcOpenOutput(\'' + jsq(k) + '\')">Engine output</button>';
  btns += '<span class="vc-menu-wrap"><button onclick="vcToggleMenu(event)" aria-label="More">⋯</button>' + (vcMenu ? vcMenuHtml(k, i) : '') + '</span>';
  let h = '<div class="vc-row"><div class="vc-grow">' + vcDot(dot, i.label || '') + '<b>' + esc(vcMN(i.activeModel)) + '</b>' + name + '<div class="vc-meta">' + text + '</div></div><div class="vc-btns">' + btns + '</div></div>';
  if (k !== 'this' && !i.autostart) h += '<div class="vc-warn">' + vcDot('warn') + '<span>This won\'t start by itself. <a onclick="vcAuto(true, \'' + jsq(k) + '\')">Start when MultiProxy starts</a></span></div>';
  else if (k !== 'this' && i.online === false) h += '<div class="vc-meta">Waiting for ' + esc(vcShowPc(k)) + ' to come online</div>';
  else if (k !== 'this') h += '<div class="vc-meta">Starts by itself when MultiProxy starts and when this device comes online. Change it in the ⋯ menu.</div>';
  if (vcNote) h += '<div class="vc-meta">' + esc(vcNote) + '</div>';
  if (vcCarry) h += '<div class="vc-meta">' + esc(vcCarry) + '</div>';
  if (vcTest.busy) h += '<div class="vc-meta">Making a line…</div>';
  else if (vcTest.warn) h += '<div class="vc-warn">' + vcDot('warn') + '<span>' + esc(vcTest.warn) + '</span></div>';
  else if (vcTest.note) h += '<div class="vc-meta">' + esc(vcTest.note) + '</div>';
  const ask = (vcOv.status || {}).askedModel;
  if (ask) h += '<div class="vc-meta">SkyrimNet asked for ' + esc(ask.asked) + '; ' + esc(ask.answered) + ' answered.</div>';
  if (vcMoving) h += vcMovePicker(k);
  h += '<div class="vc-sub">' +
    (finished ? '' : '<div class="lm-muted" style="margin:0 0 4px">' + vcSnSteps(i, 'the address below') + '</div>' +
      '<div class="vc-inline"><input type="text" readonly id="vcAddr" value="' + esc(vcOv.address) + '" onfocus="this.select()"><button class="copy" onclick="vcCopy(this)">Copy</button></div>') +
    (vcOv.tagsSwitchedOn ? '<p class="lm-muted">✓ "My NPCs use the Higgs voice engine" was switched on for you in Settings.</p>' : '') + '</div>';
  return h;
}
// The Test button: one short line made with the installed voice, the way SkyrimNet's lines are, and played right here. A small choice of clip shows only when the player has
// more than one of their own.
function vcTestButtons() {
  const own = ((vcClips && vcClips.clips) || []).filter(c => c.source === 'yours' && c.supported);
  let h = '';
  if (own.length > 1) {
    const cur = own.some(c => c.voice === vcTestVoice) ? vcTestVoice : own[0].voice;
    h += '<select id="vcSelTest" aria-label="Voice clip for the test" onchange="vcTestVoice = this.value">' + own.map(c => '<option value="' + esc(c.voice) + '"' + (c.voice === cur ? ' selected' : '') + '>' + esc(c.voice) + '</option>').join('') + '</select>';
  }
  return h + '<button' + (vcTest.busy ? ' disabled' : '') + ' onclick="vcTestRun()">Test</button>';
}
async function vcTestRun() {
  if (vcTest.busy) return;
  const own = ((vcClips && vcClips.clips) || []).filter(c => c.source === 'yours' && c.supported);
  const voice = own.some(c => c.voice === vcTestVoice) ? vcTestVoice : (own[0] ? own[0].voice : '');
  vcTest = { busy: true, note: '', warn: '' }; vcRender();
  const failed = "The voice couldn't speak a line. Click **Log** to see why.";
  try {
    const r = await fetch('/config/voice/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice }) });
    if (r.ok && /audio/.test(r.headers.get('Content-Type') || '')) {
      const blob = await r.blob();
      vcTest = { busy: false, note: '', warn: '' };
      if (vcAudio) { try { vcAudio.pause(); } catch (e) {} }
      vcPlaying = '';
      const url = URL.createObjectURL(blob);
      vcAudio = new Audio(url);
      vcAudio.onended = vcAudio.onerror = () => { try { URL.revokeObjectURL(url); } catch (e) {} };
      try { await vcAudio.play(); } catch (e) { vcTest = { busy: false, note: '', warn: "Couldn't play the line in this browser." }; }
    } else {
      const d = await r.json().catch(() => ({}));
      vcTest = d.needsClip ? { busy: false, note: d.detail || 'Add a voice clip below to test it.', warn: '' } : { busy: false, note: '', warn: d.detail || failed };
    }
  } catch (e) { vcTest = { busy: false, note: '', warn: failed }; }
  vcRender();
}
function vcMenuHtml(k, i) {
  const kept = (vcOv.downloads && vcOv.downloads.totalBytes) || 0;
  const frees = k === 'this' ? (vcOv.uninstallFreesBytes || 0) : 0;
  let h = '<div class="vc-menu" onclick="event.stopPropagation()">';
  h += '<button onclick="vcAskChange(\'' + jsq(k) + '\')">Change voice…</button>';
  h += '<button onclick="vcAuto(' + (i.autostart ? 'false' : 'true') + ', \'' + jsq(k) + '\')" title="Higgs starts automatically when MultiProxy starts.">' + (i.autostart ? '✓ ' : '') + 'Start when MultiProxy starts</button>';
  h += '<button onclick="vcCheckEngine()">Check for an update</button>';
  if ((vcOv.pcs || []).length) h += '<button onclick="vcStartMove()">Move to another device</button>';
  if (kept > 0) h += '<button onclick="vcAskClear()">Clear kept downloads (' + vcGb(kept) + ' GB)</button>';
  h += '<button onclick="vcAskUninstall(\'' + jsq(k) + '\')">Uninstall' + (frees ? ' (frees ' + vcGb(frees) + ' GB)' : '') + '</button>';
  return h + '</div>';
}
function vcMovePicker(k) {
  const ov = vcOv;
  const rows = [{ id: 'this', name: 'This device', gpu: (ov.thisPc || {}).gpu, installed: (ov.thisEngine || {}).installed, online: true }]
    .concat((ov.pcs || []).map(p => ({ id: p.id, name: p.name || p.id, gpu: p.gpu, installed: p.voice && p.voice.installed, online: p.online, hasVoice: vcCanRun(p, vcActiveModel(ov.location || 'this')) ? p.hasVoice : false })))
    .filter(r => r.id !== ov.location);
  let h = '<div class="vc-sub"><h3>Move to another device</h3><p class="lm-muted" style="margin:0 0 6px">SkyrimNet\'s settings won\'t need to change.</p>';
  rows.forEach(r => {
    const note = !r.online ? ' <span class="lm-muted">· offline</span>' : r.hasVoice === false ? ' <span class="lm-muted">· needs updating</span>' : '';
    h += '<div class="vc-row" style="padding:5px 0"><div class="vc-grow">' + vcDot(r.online ? 'on' : 'off') + esc(r.name) + (vcCard(r.gpu) ? ' <span class="lm-muted">· ' + esc(vcCard(r.gpu)) + '</span>' : '') +
      note + '</div><button' + (!r.online || r.hasVoice === false ? ' disabled' : '') + ' onclick="vcMoveTo(\'' + jsq(r.id) + '\')">' + (r.installed ? 'Use it' : 'Set it up…') + '</button></div>';
  });
  return h + '<div class="vc-ft" style="justify-content:flex-start"><button class="ghost" onclick="vcMoving = false; vcRender()">Cancel</button></div></div>';
}
function vcOthers(k) {
  const ov = vcOv, out = [];
  const t = ov.thisEngine || {};
  if (k !== 'this' && t.installed) out.push({ id: 'this', name: 'This device', state: t.label || t.state });
  (ov.pcs || []).forEach(p => { if (p.id !== k && p.voice && p.voice.installed) out.push({ id: p.id, name: p.name || p.id, state: p.voice.label || p.voice.state }); });
  if (!out.length) return '';
  let h = '<div class="vc-other"><div class="lm-muted" style="margin-bottom:4px">' + esc(vcMN(vcActiveModel(k))) + ' is also installed on:</div>';
  out.forEach(o => {
    h += '<div class="vc-orow"><div class="vc-grow">' + esc(o.name) + ' <span class="lm-muted">· ' + esc(String(o.state || '').toLowerCase()) + '</span></div>' +
      (o.id === ov.location ? '' : '<button class="ghost" onclick="vcMoveTo(\'' + jsq(o.id) + '\')">Use it</button>') +
      '<button class="ghost" onclick="vcAskUninstall(\'' + jsq(o.id) + '\')">Uninstall</button></div>';
  });
  return h + '</div>';
}
function vcRender() {
  const box = document.getElementById('vcBody');
  if (!box) return;
  const ae = document.activeElement;
  if (ae && ae.id && /^vcSel/.test(ae.id) && !vcSwitching) return;                 // an open drop-down would close if the card were rebuilt under it (a choice just made has closed it: vcBlur)
  if (ae && ae.closest && ae.closest('#vcBody') && ae.tagName === 'INPUT') return;      // and so would the address someone is selecting to copy
  if (!vcOv) { box.innerHTML = vcErr === 'LOAD' ? '<div class="lm-err">Couldn\'t read Higgs voice\'s state. Is MultiProxy still running?</div>' : '<div class="lm-muted">Looking at your devices…</div>'; return; }
  const k = vcShownKey(), i = vcInfo(k) || vcInfo(vcOv.location) || vcInfo('this');
  vcTrack(i.key, i);
  const stepped = vcWantSteps(i.key, i);
  let body;
  if (vcSwitching) body = (stepped ? vcSteps(i.key, i) : '<div class="vc-fld">' + vcModelRow(i.key, i) + '</div><div class="vc-fld">' + vcPickRow(i.key, i) + '</div>') + vcSwitchingRow(i);
  else if (stepped) body = vcSteps(i.key, i) + (i.installed ? vcRunningCard(i.key, i, true) : '');
  else body = vcRunningCard(i.key, i);
  if (vcSwitchWarn && !vcSwitching) body = '<div class="vc-warn">' + vcDot('warn') + '<span>' + esc(vcSwitchWarn) + '</span></div>' + body;
  box.innerHTML = '<div class="card" onclick="if (vcMenu) { vcMenu = false; vcRender(); }"><h2 style="margin:0 0 4px">Voice</h2>' +
    '<p class="lm-hint">Give your NPCs a voice that runs on your PC. Choose Higgs, Qwen3-TTS or Chatterbox.</p>' +
    shBox('voice', '<div class="vc-how"><h3>Start here</h3><ol class="vc-steps">' + VC_HOW.map(t => '<li>' + t + '</li>').join('') + '</ol></div>') +
    (vcErr && vcErr !== 'LOAD' ? '<div class="lm-err" style="margin:0 0 10px">' + esc(vcErr) + '</div>' : '') + body + vcOthers(i.key) + (i.key === 'this' && !stepped ? vcKept() : '') + '</div>' +
    vcSttCard() + (!i.job && i.installed ? vcClipsCard() : '');
}

// ---- actions ----
async function vcDo(fn) {
  vcErr = '';
  try { await fn(); } catch (e) { vcErr = e.message; }
  await vcLoad();
}
// A choice in a drop-down: the box keeps the focus after it closes, and the card is not redrawn under a focused drop-down, so without this the line under the box
// stayed on the old voice until the player clicked elsewhere.
function vcBlur() { const ae = document.activeElement; if (ae && ae.id && /^vcSel/.test(ae.id) && ae.blur) ae.blur(); }
function vcSwitchingRow(i) {
  const s = vcSwitching, to = s.kind === 'model' ? vcMN(s.model) : vcShowPc(s.pc);
  return '<div class="vc-row"><div class="vc-grow">' + vcDot('warn') + '<b>' + esc(vcMN(vcActiveModel(i.key))) + '</b>' + (i.key === 'this' ? '' : ' on ' + esc(vcShowPc(i.key))) + '<div class="vc-meta">Switching to ' + esc(to) + '…</div></div></div>';
}
// Run a change on a device: the screen shows the new choice and "Switching..." at once; when the device answers the card settles, and when it fails or does not
// answer in time, the box and the line go back to what the device really uses and one warning says so.
async function vcSwitchRun(sw, fn) {
  vcMenu = false; vcNote = ''; vcErr = ''; vcSwitchWarn = '';
  const k0 = vcShownKey();
  sw.was = { pc: k0, model: vcActiveModel(k0) };
  vcBlur();
  vcSwitching = sw; vcRender();
  let why = null, timer;
  try {
    await Promise.race([fn(), new Promise((_, rej) => { timer = setTimeout(() => rej(new Error(vcPcName(sw.pc || sw.k).replace(/^./, c => c.toUpperCase()) + " didn't answer in time.")), VC_SWITCH_LIMIT_MS); })]);
  } catch (e) { why = (e && e.message) || "It didn't work."; }
  clearTimeout(timer);
  vcSwitching = null;
  if (sw.kind === 'model') vcModelPick = null;
  await vcLoad();
  if (why) {
    const done = sw.kind === 'model' ? vcActiveModel(sw.k) === sw.model : (vcOv.location || 'this') === sw.pc;      // (an answer that came late may have worked after all)
    if (!done) {
      vcSwitchWarn = sw.kind === 'model'
        ? "Couldn't switch to " + vcMN(sw.model) + ' on ' + vcPcName(sw.k) + '. ' + why + ' It is still using ' + vcMN(sw.was.model) + '.'
        : "Couldn't move voice to " + vcShowPc(sw.pc) + '. ' + why + ' It is still on ' + vcPcName(sw.was.pc) + '.';
      vcRender();
    }
  }
}
function vcChooseModel(m) {
  vcMenu = false; vcNote = ''; vcSwitchWarn = '';
  const k = vcShownKey();
  if (m === vcActiveModel(k)) { vcModelPick = null; vcBlur(); vcRender(); return; }
  if (vcInstalledModels(k).includes(m)) {                  // already on that PC: just use it (a running voice is stopped; start it again from the card)
    vcModelPick = null;
    vcSwitchRun({ kind: 'model', k, model: m }, () => k === 'this' ? lmPost('/config/voice/model', { model: m }) : lmPost('/config/voice/pc/select', { id: k, model: m }));
    return;
  }
  vcModelPick = m; vcBlur(); vcRender(); vcWantFit();
}
function vcChoosePc(v) {
  vcMenu = false; vcNote = ''; vcSwitchWarn = '';
  const p = (vcOv.pcs || []).find(x => x.id === v);
  if (v === (vcOv.location || 'this') || (p && p.voice && p.voice.installed) || (v === 'this' && (vcOv.thisEngine || {}).installed)) { vcPick = null; vcMoveTo(v); return; }
  vcPick = v; vcBlur(); vcRender(); vcWantFit();
}
function vcMoveTo(id) {
  vcMoving = false; vcMenu = false;
  const pc = (vcOv.pcs || []).find(x => x.id === id);
  const installed = id === 'this' ? !!(vcOv.thisEngine || {}).installed : !!(pc && pc.voice && pc.voice.installed);
  if (!installed) { vcPick = id; vcRender(); vcWantFit(); return; }
  vcSwitchRun({ kind: 'pc', k: id, pc: id }, async () => { const d = await lmPost('/config/voice/location', { pc: id }); vcCarry = vcCarryText(d.clipsCarried, d.clipsError, id); });
}
function vcStartMove() { vcMenu = false; vcMoving = true; vcRender(); }
function vcToggleMenu(ev) { ev.stopPropagation(); vcMenu = !vcMenu; vcRender(); }
function vcTickChange(el) {
  vcTick[el.dataset.model] = el.checked;
  const b = document.getElementById('vcNext');
  if (b) b.disabled = !(el.checked && b.dataset.gate === '1');
}
function vcLookAt(n) { vcLook = vcLook === n ? null : n; vcRender(); }
async function vcNext(k) {
  vcMenu = false; vcNote = ''; delete vcPaused[k];
  const i = vcInfo(k);
  if (!i.licenseOk && !vcTick[i.model]) return;
  await vcInstall(k);
}
async function vcInstall(k) {
  delete vcCancelled[k]; delete vcDismiss[k]; vcSamples[k] = [];
  const m = vcModelOf(k);
  await vcDo(async () => {
    if (!((vcOv.thisPc || {}).licenses || {})[m]) await lmPost('/config/voice/license', { model: m });           // asked once per voice: this device's yes covers the other devices (the server passes it on)
    if (k === 'this') await lmPost('/config/voice/install', { model: m });
    else await lmPost('/config/voice/pc/install', { id: k, model: m });
  });
}
function vcPause(k) {
  vcPaused[k] = true; vcNote = '';
  vcDo(() => k === 'this' ? lmPost('/config/voice/cancel') : lmPost('/config/voice/pc/cancel', { id: k }));
}
function vcResume(k) { delete vcPaused[k]; vcInstall(k); }
function vcStopPause(k) { delete vcPaused[k]; vcNote = 'Cancelled. What was downloaded is kept, so Set it up carries on from there.'; vcRender(); }
async function vcRedownload(k, file) {
  delete vcDismiss[k]; vcSamples[k] = [];
  await vcDo(() => lmPost('/config/voice/redownload', { pc: k, file }));
}
function vcDismissBad(k) { const i = vcInfo(k); vcDismiss[k] = i.jobBad; vcRender(); }
function vcCancel(k) {
  vcCancelled[k] = true;
  vcNote = 'Cancelled. What was downloaded is kept, so Set it up carries on from there.';
  vcDo(() => k === 'this' ? lmPost('/config/voice/cancel') : lmPost('/config/voice/pc/cancel', { id: k }));
}
function vcAct(k, what) {
  vcNote = ''; vcMenu = false;
  vcDo(() => k === 'this' ? lmPost('/config/voice/' + what) : lmPost('/config/voice/pc/' + what, { id: k }));
}
function vcAuto(on, k) { vcMenu = false; vcDo(() => lmPost('/config/voice/autostart', { on, pc: k || 'this' })); }
async function vcCheckEngine() {
  vcMenu = false;
  try {
    const d = await (await fetch('/config/voice/engine-check')).json();
    vcNote = d.upToDate ? 'You have the newest engine MultiProxy has been tested with (' + d.newest + ').'
      : d.installed ? 'A newer engine (' + d.newest + ') is ready. Uninstall and install again to update.'
      : 'The voice isn\'t installed on this device.';
  } catch (e) { vcNote = "Couldn't check right now. Try again later."; }
  vcRender();
}
function vcOpenFolder() { vcDo(() => lmPost('/config/voice/open-folder')); }
function vcCopy(btn) {
  const v = (document.getElementById('vcAddr') || {}).value || '';
  try { navigator.clipboard.writeText(v); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}


// ---- Voice clips: your own clip for any voice (voice_clips.py). They live on the device that runs Higgs. ----
async function vcLoadClips() {
  try {
    const r = await fetch('/config/voice/clips');
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { vcClips = { pc: vcOv.location, clips: [], onThisPc: vcOv.location === 'this', error: d.detail || "Couldn't read the voice clips." }; return; }
    vcClips = d; vcClips.error = '';
  } catch (e) { vcClips = { pc: vcOv.location, clips: [], onThisPc: vcOv.location === 'this', error: "Couldn't read the voice clips." }; }
}
function vcClipsHeader() {
  const c = vcClips || { clips: [] };
  const mine = c.clips.filter(x => x.source === 'yours' && x.supported).length;
  return '<div class="vc-row"><div class="vc-grow"><b>' + (mine ? mine + ' of your own clips in use' : 'No clips of your own yet.</b> <span class="lm-muted">Add one to make voices sound exactly right!</span><b>') + '</b></div>' +
    '<button class="primary" onclick="vcAskClip(\'\')">Add a clip…</button>' +
    (c.onThisPc ? '<button onclick="vcClipsOpenFolder()">Open folder</button>' : '') + '</div>';
}
function vcClipRow(x) {
  const v = jsq(x.voice);
  const qwen = !!vcClips && vcActiveModel(vcClips.pc) === 'qwen3tts';
  // Qwen3-TTS needs the words a clip says. The voice works them out by itself (when it can); a row speaks up only when that has failed or can't be done.
  const needWords = x.supported && qwen && !x.hasWords && (x.wordsFailed || !x.canTranscribe);
  const what = (x.supported ? 'your clip' + (x.seconds ? ' · ' + x.seconds.toFixed(1) + ' s' : '') : 'your file · format not supported') +
    (needWords ? '</span> <span class="lm-warn">⚠️ ' + (x.canTranscribe ? "The voice couldn't work out the words this clip says. Add them (use Edit words…)." : 'Add the words this clip says (use Replace…).') : '');
  const wordsLine = x.supported && qwen && x.hasWords
    ? '<div class="lm-muted vc-words" title="' + esc(x.words || '') + '">' + (x.wordsFrom === 'auto' ? 'Words the voice worked out: ' : 'Words you typed: ') + esc(x.words || '') + '</div>' : '';
  const btns = (x.supported ? '<button onclick="vcPlay(\'' + v + '\', \'yours\')">' + (vcPlaying === x.voice + '|yours' ? '■ Stop' : '▶ Play') + '</button>' : '') +
    (x.supported && qwen && (x.hasWords || x.canTranscribe) ? '<button onclick="vcEditWords(\'' + v + '\')">Edit words…</button>' : '') +
    '<button onclick="vcAskClip(\'' + v + '\')">Replace…</button><button onclick="vcRemoveClip(\'' + v + '\')">Remove</button>';
  return '<div class="vc-clip' + (x.supported ? '' : ' vc-bad') + '"><div class="vc-grow"><span class="vc-voice">' + esc(x.voice) + '</span> <span class="lm-muted">· ' + what + '</span>' + wordsLine + '</div>' +
    '<div class="vc-btns">' + btns + '</div></div>';
}
// Only the player's own clips are listed; the voices SkyrimNet has asked for show up only as suggestions inside the Add a clip pop-up.
function vcClipRows() {
  const c = vcClips || { clips: [] };
  return c.clips.filter(x => x.source === 'yours').map(vcClipRow).join('');
}
function vcClipsDraw() { const el = document.getElementById('vcClipRows'); if (el) el.innerHTML = vcClipRows(); }
function vcClipsCard() {
  const c = vcClips;
  let h = '<div class="card" id="vcClipsCard"><h2 style="margin:0 0 10px">Voice clips</h2>';
  if (!c) return h + '<div class="lm-muted">Looking for your clips…</div></div>';
  if (c.error) return h + '<div class="lm-err">' + esc(c.error) + '</div></div>';
  if (vcClipErr) h += '<div class="lm-err" style="margin:0 0 10px">' + esc(vcClipErr) + '</div>';
  if (vcClipNote) h += '<div class="lm-muted" style="margin:0 0 10px">' + esc(vcClipNote) + '</div>';
  h = h.replace('margin:0 0 10px">Voice clips', 'margin:0 0 6px">Voice clips');
  h += VC_CLIPS_WHAT.map(t => '<p class="lm-muted" style="margin:0 0 6px">' + t + '</p>').join('') +
    '<ol class="vc-steps" id="vcClipSteps" style="margin-top:10px">' + VC_CLIPS_STEPS.map(t => '<li>' + esc(t) + '</li>').join('') + '</ol>' +
    vcClipsHeader() + '<div id="vcClipRows">' + vcClipRows() + '</div>' +
    '<p class="lm-muted" style="margin-top:14px">' + VC_CLIPS_TIP + '</p></div>';
  return h;
}
function vcPlay(voice, which) {
  const key = voice + '|' + which;
  if (vcAudio) { try { vcAudio.pause(); } catch (e) {} vcAudio = null; }
  if (vcPlaying === key) { vcPlaying = ''; vcClipsDraw(); return; }
  vcPlaying = key;
  vcAudio = new Audio('/config/voice/clips/audio?voice=' + encodeURIComponent(voice) + '&which=' + which + '&t=' + Date.now());
  vcAudio.onended = vcAudio.onerror = () => { if (vcPlaying === key) { vcPlaying = ''; vcClipsDraw(); } };
  vcAudio.play().catch(() => { vcPlaying = ''; vcClipErr = "Couldn't play that clip."; vcRender(); });
  vcClipsDraw();
}
function vcAskClip(voice) { vcModal = { type: 'clip', voice: voice || '', error: '' }; vcModalDraw(); }
function vcEditWords(voice) {                        // the same pop-up, with only the words (no new file): an automatic transcript can be wrong
  const row = ((vcClips && vcClips.clips) || []).find(x => x.voice === voice) || {};
  vcModal = { type: 'clip', voice, wordsOnly: true, words: row.words || '', error: '' }; vcModalDraw();
}
async function vcWordsSave() {
  const m = vcModal; if (!m || !m.wordsOnly) return;
  m.error = ''; m.busy = true; vcModalDraw();
  try { await lmPost('/config/voice/clips/words', { voice: m.voice, words: (document.getElementById('vcClipWords').value || '').trim() }); }
  catch (e) { m.error = e.message; m.busy = false; vcModalDraw(); return; }
  await vcLoadClips();
  if (vcModal === m) vcCloseModal();
  vcRender();
}
async function vcPickClip(voice) {                    // Replace… / Use my own…: the voice is known, so straight to the file window
  vcClipErr = ''; vcClipNote = '';
  await vcChooseAndAdd(voice);
}
async function vcChooseAndAdd(voice, words) {
  let path = null;
  try { path = (await lmPost('/config/voice/clips/pick')).path; } catch (e) { return e.message; }
  if (!path) return '';                                // cancelled (or no screen): nothing to do
  try { await lmPost('/config/voice/clips/add', words ? { voice, path, words } : { voice, path }); } catch (e) { return e.message; }
  await vcLoadClips();
  return '';
}
async function vcClipDialogGo() {
  const m = vcModal; if (!m || m.type !== 'clip') return;
  const voice = (document.getElementById('vcClipVoice').value || '').trim();
  if (!voice) { m.error = 'Which voice is this clip for?'; vcModalDraw(); return; }
  m.voice = voice; m.error = ''; m.busy = true; vcModalDraw();
  const err = await vcChooseAndAdd(voice, (document.getElementById('vcClipWords').value || '').trim());
  if (err) { m.error = err; m.busy = false; vcModalDraw(); return; }
  if (vcModal === m) vcCloseModal();
  vcRender();
}
async function vcRemoveClip(voice) {
  vcClipErr = ''; vcClipNote = '';
  try { await lmPost('/config/voice/clips/remove', { voice }); } catch (e) { vcClipErr = e.message; }
  await vcLoadClips(); vcRender();
}
async function vcClipsOpenFolder() { try { await lmPost('/config/voice/clips/open-folder'); } catch (e) { vcClipErr = e.message; vcRender(); } }

// ---- dialogs: the license, uninstall, clear kept downloads, the log ----
function vcCloseModal() { vcModal = null; if (vcOutTimer) { clearTimeout(vcOutTimer); vcOutTimer = null; } vcOut = null; vcModalDraw(); }
async function vcOpenLog(k) {
  vcModal = { type: 'log', key: k, text: null, error: '' }; vcModalDraw();
  try {
    const r = await fetch(k === 'this' ? '/config/voice/log' : '/config/voice/pc-log?id=' + encodeURIComponent(k));
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "Couldn't read that log.");
    if (vcModal && vcModal.type === 'log') { vcModal.text = d.log || ''; vcModal.error = ''; }
  } catch (e) { if (vcModal && vcModal.type === 'log') vcModal.error = e.message; }
  vcModalDraw();
  const pre = document.getElementById('vcLogText'); if (pre) pre.scrollTop = pre.scrollHeight;
}
// ---- Engine output: what the voice engine itself prints, as it happens. It asks for the lines after the last one it has about once a second while this window is open and
// not paused; closing the window (or Pause) stops everything. The text shown is exactly what Copy all copies. ----
let vcOut = null, vcOutTimer = null;
const VC_OUT_SIZES = [200, 1000, 5000];
function vcOutLine(l) { return new Date(l.t * 1000).toTimeString().slice(0, 8) + '  ' + (l.src === 'multiproxy' ? 'MultiProxy: ' : '') + l.text; }
function vcOutText() { return vcOut.lines.map(vcOutLine).join('\n'); }
function vcOpenOutput(k) {
  vcOut = { key: k, lines: [], since: 0, size: 200, follow: true, error: '', model: '', rtf: null, tick: 0, busy: false };
  vcModal = { type: 'output', key: k }; vcModalDraw(); vcOutPoll();
}
async function vcOutPoll() {
  if (vcOutTimer) { clearTimeout(vcOutTimer); vcOutTimer = null; }
  if (!vcModal || vcModal.type !== 'output' || !vcOut || !vcOut.follow) return;
  if (!document.hidden && !vcOut.busy) await vcOutFetch();
  if (vcModal && vcModal.type === 'output' && vcOut && vcOut.follow && !vcOutTimer) vcOutTimer = setTimeout(vcOutPoll, 1000);
}
async function vcOutFetch(from) {
  const o = vcOut;
  if (!o) return;
  o.busy = true;
  try {
    const url = (o.key === 'this' ? '/config/voice/output?' : '/config/voice/pc-output?id=' + encodeURIComponent(o.key) + '&') + 'since=' + (from === undefined ? o.since : from) + '&limit=' + o.size;
    const r = await fetch(url);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "Couldn't read the engine output.");
    if (vcOut !== o) return;
    if (d.reset || from === 0) o.lines = [];
    o.lines = o.lines.concat(d.lines || []).slice(-o.size);
    o.since = d.next; o.model = d.modelName || ''; o.error = '';
    if (o.tick++ % 10 === 0) {
      try { const j = await (await fetch('/config/voice/speaking-rate')).json(); o.rtf = j.rtf; } catch (e) {}
    }
  } catch (e) { o.error = e.message; } finally { o.busy = false; }
  vcOutDraw();
}
function vcOutDraw() {
  const o = vcOut, pre = document.getElementById('vcOutText'), head = document.getElementById('vcOutHead');
  if (!o || !pre || !head) return;
  const rate = o.rtf == null ? '' : ' \u00b7 speaking ' + (o.rtf < 1 ? o.rtf.toFixed(2) : o.rtf.toFixed(1)) + ' s per second of audio';
  head.innerHTML = esc(o.model || '') + esc(rate) + (o.error ? ' <span class="lm-err">' + esc(o.error) + '</span>' : '');
  const text = o.lines.length ? vcOutText() : 'Nothing yet. Lines appear here as the voice prints them.';
  if (pre.textContent !== text) {
    pre.textContent = text;
    if (o.follow) pre.scrollTop = pre.scrollHeight;
  }
  const b = document.getElementById('vcOutPause'); if (b) b.textContent = o.follow ? 'Pause' : 'Follow';
}
function vcOutToggle() { if (!vcOut) return; vcOut.follow = !vcOut.follow; vcOutDraw(); if (vcOut.follow) vcOutPoll(); else if (vcOutTimer) { clearTimeout(vcOutTimer); vcOutTimer = null; } }
function vcOutClear() { if (!vcOut) return; vcOut.lines = []; vcOutDraw(); }          // (the view only: the lines already seen are not asked for again)
function vcOutSize(v) { if (!vcOut) return; vcOut.size = parseInt(v, 10) || 200; vcOut.lines = []; vcOut.since = 0; vcOutFetch(0); }
function vcCopyOut(btn) {
  try { navigator.clipboard.writeText(vcOut ? vcOutText() : ''); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}
function vcCopyLog(btn) {
  try { navigator.clipboard.writeText((vcModal && vcModal.text) || ''); } catch (e) {}
  const o = btn.textContent; btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = o; }, 1200);
}
function vcAskUninstall(k) { vcMenu = false; vcModal = { type: 'uninstall', key: k, model: vcActiveModel(k), installed: vcInstalledModels(k) }; vcModalDraw(); }
// Change voice…: ask first (it stops what is running), then the Setup step with the voice picker opens for that device, the current voice marked. Once the new voice is in,
// the old voice's own files are removed the same way Uninstall removes a voice (its clips and settings stay). Cancel at any step changes nothing.
function vcAskChange(k) { vcMenu = false; vcModal = { type: 'change', key: k, old: vcActiveModel(k) }; vcModalDraw(); }
async function vcConfirmChange() {
  const m = vcModal; vcCloseModal(); if (!m) return;
  vcChange = { k: m.key, old: m.old }; vcModelPick = null; vcPick = null; vcNote = '';
  vcRender();
}
function vcChangeCancel() { vcChange = null; vcModelPick = null; vcRender(); }
function vcChangeFinish() {            // after every read: the new voice is in and nothing is installing, so the old voice's files go
  if (!vcChange || !vcOv) return;
  const k = vcChange.k, i = vcInfo(k);
  if (!i || i.job) return;
  const now = vcActiveModel(k), old = vcChange.old;
  if (now === old || !vcInstalledModels(k).includes(now)) return;
  const had = vcInstalledModels(k).includes(old);
  vcChange = null;
  if (had) lmPost(k === 'this' ? '/config/voice/uninstall' : '/config/voice/pc/uninstall', k === 'this' ? { model: old } : { id: k, model: old }).then(() => vcLoad(), () => {});
}
function vcAskClear() { vcMenu = false; vcModal = { type: 'clear' }; vcModalDraw(); }
async function vcConfirmUninstall() {
  const m = vcModal; vcCloseModal(); if (!m) return;
  vcNote = '';
  await vcDo(() => m.key === 'this' ? lmPost('/config/voice/uninstall', { model: m.model }) : lmPost('/config/voice/pc/uninstall', { id: m.key, model: m.model }));
}
async function vcConfirmClear() {
  vcCloseModal(); vcNote = '';
  await vcDo(async () => { const d = await lmPost('/config/voice/downloads/clear'); vcNote = 'Cleared ' + vcGb(d.freedBytes || 0) + ' GB of kept downloads.'; });
}
// ---- Speech to text through xAI (stt_xai.py): off until the player says so, because their voice leaves the PC ----
const VC_STT_STEPS = [
  'In SkyrimNet, open the speech settings and choose <b>External Whisper</b>.',
  'Paste this address:',
  'Press the push-to-talk key and speak.'];
function vcSttCard() {
  const s = (vcOv && vcOv.stt) || { on: false, hasKey: false, last: null, address: '' };
  let last = 'No requests yet';
  if (s.last) last = (s.last.ok ? 'Last request: ' : 'Last request failed: ') + Math.round(s.last.ago) + ' s ago' + (s.last.ok ? ', answered in ' + s.last.took.toFixed(1) + ' s' : '');
  return '<div class="card" id="vcSttCard"><h2 style="margin:0 0 4px">Speech to text</h2>' +
    '<p class="lm-hint">Talk to your NPCs with SkyrimNet\'s push-to-talk. xAI can turn your voice into text. <span class="hp-how"><a onclick="openHelp(\'speech-to-text\')">How do I set this up?</a></span></p>' +
    '<label class="vc-lic"><input type="checkbox" id="vcSttBox"' + (s.on ? ' checked' : '') + (s.hasKey ? '' : ' disabled') + ' onchange="vcSttChange(this)"><span>Turn my voice into text with xAI</span></label>' +
    (s.hasKey ? '' : '<div class="vc-warn"><span class="lm-warn">This needs your xAI key. <a class="lm-link" onclick="showMainTab(\'setup\')">Add your xAI key</a></span></div>') +
    (vcSttErr ? '<div class="lm-err" style="margin:6px 0">' + esc(vcSttErr) + '</div>' : '') +
    '<div class="vc-meta" id="vcSttLast" style="margin-left:0">' + esc(last) + '</div>' +
    '<ol class="vc-steps" style="margin-top:12px">' + VC_STT_STEPS.map(t => '<li>' + t + '</li>').join('') + '</ol>' +
    '<div class="vc-url"><span id="vcSttAddr">' + esc(s.address) + '</span><button class="vc-copyi" title="Copy" aria-label="Copy the address" onclick="vcSttCopy(this)">' + VC_COPY_SVG + '</button></div></div>';
}
function vcSttCopy(btn) {
  const v = (document.getElementById('vcSttAddr') || {}).textContent || '';
  try { navigator.clipboard.writeText(v); } catch (e) {}
  const o = btn.innerHTML; btn.textContent = 'Copied'; setTimeout(() => { btn.innerHTML = o; }, 1200);
}
function vcSttChange(el) {
  if (el.checked) { el.checked = false; vcModal = { type: 'stt' }; vcModalDraw(); }          // turning it on is confirmed first; the box stays off until then
  else vcSttSet(false);
}
async function vcSttSet(on) {
  vcSttErr = '';
  try { await lmPost('/config/voice/stt', { on }); } catch (e) { vcSttErr = e.message; }
  await vcLoad();
}
function vcSttConfirm() { vcCloseModal(); vcSttSet(true); }
function vcModalDraw() {
  const box = document.getElementById('vcModalBox');
  if (!box) return;
  const m = vcModal;
  if (!m) { box.style.display = 'none'; box.innerHTML = ''; return; }
  let h;
  if (m.type === 'stt') {
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="vcDialog"><h3>Turn on speech to text?</h3>' +
      '<p>Your voice recordings will be sent to xAI to be turned into text. xAI\'s terms apply. Turn this on?</p>' +
      '<div class="vc-ft"><button class="ghost" onclick="vcCloseModal()">Cancel</button><button class="primary" onclick="vcSttConfirm()">Turn on</button></div></div>';
  } else if (m.type === 'clip' && m.wordsOnly) {
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" aria-labelledby="vcClipTitle" tabindex="-1" id="vcDialog"><h3 id="vcClipTitle">Edit the words for ' + esc(m.voice) + '</h3>' +
      '<div class="vc-field"><label for="vcClipWords">The words spoken in the clip</label>' +
      '<input type="text" id="vcClipWords" value="' + esc(m.words) + '" placeholder="Type exactly what the clip says" onkeydown="if (event.key === \'Enter\') vcWordsSave()"></div>' +
      (m.error ? '<div class="lm-err">' + esc(m.error) + '</div>' : '') +
      '<div class="vc-ft"><button class="ghost" onclick="vcCloseModal()">Cancel</button><button class="primary"' + (m.busy ? ' disabled' : '') + ' onclick="vcWordsSave()">Save</button></div></div>';
  } else if (m.type === 'clip') {
    const seen = ((vcClips && vcClips.clips) || []).map(x => '<option value="' + esc(x.voice) + '">').join('');
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" aria-labelledby="vcClipTitle" tabindex="-1" id="vcDialog"><h3 id="vcClipTitle">Add a clip</h3>' +
      '<div class="vc-field"><label for="vcClipVoice">Which voice is this clip for?</label>' +
      '<input type="text" id="vcClipVoice" list="vcClipVoices" value="' + esc(m.voice) + '" placeholder="femalenord" onkeydown="if (event.key === \'Enter\') vcClipDialogGo()"><datalist id="vcClipVoices">' + seen + '</datalist></div>' +
      '<div class="vc-field"><label for="vcClipWords">The words spoken in the clip (the Qwen3-TTS voice needs them)</label>' +
      '<input type="text" id="vcClipWords" placeholder="Type exactly what the clip says"></div>' +
      '<p class="lm-muted" style="margin:0">Type the exact name SkyrimNet uses for the voice, then choose your audio file.</p>' +
      (m.error ? '<div class="lm-err">' + esc(m.error) + '</div>' : '') +
      '<div class="vc-ft"><button class="ghost" onclick="vcCloseModal()">Cancel</button><button class="primary"' + (m.busy ? ' disabled' : '') + ' onclick="vcClipDialogGo()">' + (m.busy ? 'Waiting for the file window…' : 'Choose file…') + '</button></div></div>';
  } else if (m.type === 'uninstall') {
    const where = m.key === 'this' ? 'this device' : esc(m.key);
    const frees = m.key === 'this' && vcOv.uninstallFreesBytes ? ' It frees about ' + vcGb(vcOv.uninstallFreesBytes) + ' GB.' : '';
    const others = (m.installed || []).filter(x => x !== m.model);
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="vcDialog"><h3>Uninstall ' + esc(vcMN(m.model)) + '?</h3>' +
      (others.length ? '<p>This removes the ' + esc(vcMN(m.model)) + ' model from ' + where + '. ' + esc(vcMN(others[0])) + ' stays. Your own voice clips are kept. You can install it again any time.</p>'
        : '<p>This removes the voice engine and the voice model from ' + where + '.' + frees + ' Your own voice clips are kept. You can install it again any time.</p>') +
      '<div class="vc-ft"><button class="ghost" onclick="vcCloseModal()">Cancel</button><button class="primary" onclick="vcConfirmUninstall()">Uninstall</button></div></div>';
  } else if (m.type === 'change') {
    const where = m.key === 'this' ? 'this device' : esc(vcShowPc(m.key));
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="vcDialog"><h3>Change the voice on ' + where + '?</h3>' +
      '<p>' + esc(vcMN(m.old)) + ' stops now, and its files are removed once the new voice is installed. Your own voice clips are kept.</p>' +
      '<div class="vc-ft"><button class="ghost" onclick="vcCloseModal()">Cancel</button><button class="primary" onclick="vcConfirmChange()">Change voice</button></div></div>';
  } else if (m.type === 'clear') {
    const kept = (vcOv.downloads && vcOv.downloads.totalBytes) || 0;
    h = '<div class="vc-dialog" role="dialog" aria-modal="true" tabindex="-1" id="vcDialog"><h3>Clear kept downloads?</h3>' +
      '<p>This frees up ' + vcGb(kept) + ' GB. Your working voices won\'t break, but installing on another device later will mean downloading the files again.</p>' +
      '<div class="vc-ft"><button class="ghost" onclick="vcCloseModal()">Cancel</button><button class="primary" onclick="vcConfirmClear()">Clear kept downloads</button></div></div>';
  } else if (m.type === 'output') {
    h = '<div class="op-modal-box" role="dialog" aria-modal="true" tabindex="-1" id="vcDialog"><h3>Engine output on ' + (m.key === 'this' ? 'this device' : esc(vcShowPc(m.key))) + '</h3>' +
      '<div class="lm-muted" id="vcOutHead"></div><pre class="op-log" id="vcOutText"></pre>' +
      '<div class="vc-ft"><label class="lm-muted">Show more lines <select onchange="vcOutSize(this.value)">' + VC_OUT_SIZES.map(n => '<option value="' + n + '"' + (vcOut && vcOut.size === n ? ' selected' : '') + '>' + n + '</option>').join('') + '</select></label>' +
      '<button id="vcOutPause" onclick="vcOutToggle()">Pause</button><button onclick="vcOutClear()">Clear view</button><button onclick="vcCopyOut(this)">Copy all</button><button class="primary" onclick="vcCloseModal()">Close</button></div></div>';
  } else {
    const body = m.error ? '<div class="lm-err">' + esc(m.error) + '</div>' : m.text == null ? '<div class="lm-muted">Reading details…</div>' :
      !m.text.trim() ? '<div class="lm-muted">Nothing to show yet.</div>' : '<pre class="op-log" id="vcLogText">' + esc(m.text) + '</pre>';
    h = '<div class="op-modal-box" role="dialog" aria-modal="true" tabindex="-1" id="vcDialog"><h3>Voice log on ' + (m.key === 'this' ? 'this device' : esc(vcShowPc(m.key))) + '</h3>' + body +
      '<div class="vc-ft"><button onclick="vcCopyLog(this)">Copy</button><button class="primary" onclick="vcCloseModal()">Close</button></div></div>';
  }
  box.innerHTML = h;
  box.style.display = 'flex';
  if (m.type === 'output') vcOutDraw();
  const focus = document.getElementById(m.type === 'clip' ? (m.wordsOnly ? 'vcClipWords' : 'vcClipVoice') : 'vcDialog');
  if (focus && focus.focus) focus.focus();
}
document.addEventListener('keydown', e => { if (e.key === 'Escape' && vcModal) vcCloseModal(); });

// ---- the Other devices tab: Higgs as one more program on a device's card ----
function vcPcRow(r) {
  const rep = r.report || {}, v = rep.voice;
  if (!v) return '';
  const id = r.id, j = v.install;
  let dot, text, btn = '';
  if (typeof opUnBusy !== 'undefined' && opUnBusy[opUnKey(id, 'voice')]) { dot = 'warn'; text = 'Uninstalling'; }
  else if (j && j.state === 'running') { dot = 'warn'; text = 'installing'; }
  else if (!v.installed) {
    dot = 'none'; text = 'not installed';
    btn = '<button class="ghost" onclick="vcSetupOn(\'' + jsq(id) + '\')">Set it up</button>';
  } else if (v.state === 'ready') {
    dot = 'on'; text = 'ready' + (v.gpuMemoryMb ? ' · ' + vcMbGb(v.gpuMemoryMb) + ' GB' : '');
    btn = '<button class="ghost" onclick="vcPcAct(\'' + jsq(id) + '\', \'stop\')">Stop ' + esc(vcMN(v.model || 'higgs')) + '</button>';
  } else if (v.state === 'starting' || v.state === 'getting_ready') { dot = 'warn'; text = String(v.label || 'starting').toLowerCase(); }
  else {
    dot = v.state === 'stopped' ? 'idle' : 'off'; text = String(v.label || 'stopped').toLowerCase();
    btn = '<button class="primary" onclick="vcPcAct(\'' + jsq(id) + '\', \'start\')">Start ' + esc(vcMN(v.model || 'higgs')) + '</button>';
  }
  if (v.installed && text !== 'Uninstalling' && !(j && j.state === 'running')) btn += '<button class="ghost" onclick="opAskUninstall(\'' + jsq(id) + '\', \'voice\')">Uninstall</button>';
  let h = '<div class="op-prog">' + opDot(dot, v.label || '') + '<b>' + esc(vcMN(v.model || 'higgs')) + '</b> <span class="lm-muted">' + esc(text) + '</span><span class="op-grow"></span>' + btn + '</div>';
  const g = (rep.gpus || [])[0];
  const anyLoaded = (rep.programs || []).some(p => (p.models || []).some(m => m.loaded));
  if (v.state === 'ready' && anyLoaded && g && g.memory_total_mb && g.memory_used_mb != null && g.memory_used_mb / g.memory_total_mb >= 0.9)
    h += '<div class="lm-muted op-note" style="padding:0 0 6px 17px"><span class="op-dot warn"></span>' + esc(vcMN(v.model || 'higgs')) + ' and a loaded AI model are close to this card\'s limit. Lines might be slower.</div>';
  return h;
}
async function vcPcAct(id, what) {
  try { await lmPost('/config/voice/pc/' + what, { id }); } catch (e) { opNote = e.message; }
  if (typeof opRefresh === 'function') opRefresh(true);
}
function vcSetupOn(id) { vcPick = id; showMainTab('voice'); }

function vcFromHash() { if (location.hash === '#voice') showMainTab('voice'); }
window.addEventListener('hashchange', vcFromHash);
window.addEventListener('load', vcFromHash);
"""
