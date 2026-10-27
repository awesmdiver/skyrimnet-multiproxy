"""Claude on another device, its row on the Other devices tab (2026-10-05). Matches `design/claude-on-another-pc-mockup.html`.

One more program on a Courier device's card, after Higgs voice and the local decision model (`other_pcs_ui.opCourierBody` calls `ccPcRow` when it exists), in the same
row pattern: a dot, the name, a short word for what it is, the state, and the buttons. Four states:

  Not installed   -> Install (a progress bar while it installs; Cancel)
  Needs sign-in   -> Sign in: that device runs the sign-in itself and the row shows (Signing in, below) what to do on any device with a browser. When it can't start,
                     one plain line says so and the older way (three numbered steps, a box to paste the long code, Save) opens: it stays as the fallback.
  Signing in      -> three numbered one-line steps, the button Open the sign-in page (with the link as selectable text for another device), the box Paste the code from the page,
                     Save and Cancel. Cancel, and a code that is refused, end it with one plain line. The long-lived code never reaches this page: only the short one-time code does.
  Ready           -> Test, Sign out
  Installed       -> Uninstall (the same confirm as the other programs: `opAskUninstall` in other_pcs_ui.py); while it runs the row says Uninstalling
  Something wrong -> the one warning line, in the existing amber line style (`vc-warn`)

Update shows beside the buttons only when a newer version is known. The sign-in code lives only in `ccCodeText` while the player types it: it is sent on Save and cleared at once.

Every on-screen string here is quoted in the handoff (the design side runs them through a Gemini pass).
"""

CLAUDE_JS = r"""// ---------------- Claude on another device (claude_ui.py) ----------------
const ccOpen = {};        // device id -> the sign-in steps are open
const ccCodeText = {};    // device id -> what is typed in the box (cleared when it is saved or cancelled; never stored anywhere else)
const ccBusy = {};        // device id -> which action is running
const ccPageCode = {};    // device id -> the one-time code typed in the sign-in page's box (cleared when it is saved; never stored anywhere else)
const CC_LINK_HOSTS = ['claude.com', 'www.claude.com', 'claude.ai', 'www.claude.ai', 'platform.claude.com', 'console.anthropic.com', 'anthropic.com', 'www.anthropic.com'];
function ccLinkOk(u) { try { const x = new URL(String(u || '')); return x.protocol === 'https:' && CC_LINK_HOSTS.indexOf(x.hostname) >= 0 && !x.username && !x.password && (x.port === '' || x.port === '443'); } catch (e) { return false; } }
// Whether a sign-in is on its way is decided on the OTHER device, and it can finish there on its own (approved in a browser on that device, no code pasted). So while a sign-in
// is in progress, and for 60 seconds after its steps go away, the page asks the device for its real state every 2 seconds, and the row follows it: Ready the moment it says
// signed in, never depending on the code box having been used. It stops at Ready, on Cancel, after the 60 seconds, or after 15 minutes of a sign-in that nobody finished.
const CC_WATCH_EVERY_MS = 2000, CC_WATCH_AFTER_MS = 60000, CC_WATCH_MAX_MS = 15 * 60000;
const ccWatch = {};       // device id -> { active: the steps are showing, until: when to stop }
let ccWatchTimer = null;
function ccWatchOn(id) { ccWatch[id] = { active: true, until: Date.now() + CC_WATCH_MAX_MS }; ccWatchStart(); }
function ccWatchNote(id, c) {        // called every time the row is drawn, with what the device last said
  const w = ccWatch[id];
  if (c.state === 'ready') { delete ccWatch[id]; return; }
  if (c.state === 'signing_in') { ccWatchOn(id); return; }
  if (w && w.active) ccWatch[id] = { active: false, until: Date.now() + CC_WATCH_AFTER_MS };
}
function ccWatchStart() { if (!ccWatchTimer && typeof setInterval === 'function') ccWatchTimer = setInterval(ccWatchTick, CC_WATCH_EVERY_MS); }
async function ccWatchTick() {
  const now = Date.now();
  for (const id of Object.keys(ccWatch)) if (ccWatch[id].until < now) delete ccWatch[id];
  if (!Object.keys(ccWatch).length) { if (ccWatchTimer) clearInterval(ccWatchTimer); ccWatchTimer = null; return; }
  if (typeof document !== 'undefined' && document.hidden) return;
  if (typeof opRefresh === 'function') await opRefresh(true);          // the existing fresh read of every device's report
}
const ccWay = {};         // device id -> 1 | 2: which way the sign-in steps show (a browser on that device | a link and a code); unset: by what the device says. The terminal way is `ccOpen`.
const ccLine = {};        // device id -> {kind: 'warn' | 'ok', text}: the last answer to something the player pressed
function ccFmt(t) { return esc(String(t || '')).replace(/`([^`]+)`/g, '<code>$1</code>'); }
function ccName(r) { const n = (typeof opName === 'function') ? opName(r) : r.id; return n; }
function ccPcText(text, r) { return String(text || '').split('<PC>').join(r ? ccName(r) : 'this device'); }   // Courier-side lines name the device as <PC>
function ccWarn(text, r) { return '<div class="vc-warn" style="padding:0 0 6px 17px">' + opDot('warn') + '<span>' + ccFmt(ccPcText(text, r)) + '</span></div>'; }
function ccPcRow(r) {
  const c = (r.report || {}).claude;
  if (!c) return '';
  ccWatchNote(r.id, c);
  const id = r.id, j = c.install, busy = ccBusy[id] || '';
  let dot, text, btn = '', bar = '';
  if (busy === 'uninstall') {
    dot = 'warn'; text = 'Uninstalling';
  } else if (c.state === 'installing' && j) {
    dot = 'warn'; text = (j.updating ? 'Updating' : 'Installing') + (j.step && j.step !== 'Downloading' ? ' · ' + String(j.step).toLowerCase() : (j.total ? ' · ' + (j.percent || 0) + '%' : ''));
    btn = '<button class="ghost" onclick="ccAct(\'' + jsq(id) + '\', \'cancel\')">Cancel</button>';
    if (j.total && j.step === 'Downloading') bar = '<div class="lm-bar" style="margin-left:17px"><i style="width:' + (j.percent || 0) + '%"></i></div>';
  } else if (c.state === 'not_installed') {
    dot = 'none'; text = 'Not installed';
    btn = '<button class="primary"' + (busy ? ' disabled' : '') + ' onclick="ccAct(\'' + jsq(id) + '\', \'install\')">Install</button>';
  } else if (c.state === 'checking') {
    dot = 'warn'; text = 'Checking the sign-in code';
  } else if (c.state === 'signing_in') {
    const st = (c.signin || {}).state;
    dot = 'warn'; text = st === 'checking' ? 'Checking the sign-in code' : ((st === 'link' || ccWayOf(id, c.signin) === 1) ? 'Waiting for the sign-in' : 'Getting a sign-in link');
    btn = '<button class="ghost"' + (st === 'checking' ? ' disabled' : '') + ' onclick="ccCancelSignin(\'' + jsq(id) + '\')">Cancel</button>';
  } else if (c.state === 'needs_signin') {
    dot = 'warn'; text = ccOpen[id] ? 'Sign in with a terminal' : 'Needs sign-in';
    btn = ccOpen[id] ? '<button class="ghost" onclick="ccToggle(\'' + jsq(id) + '\')">Cancel</button>' : '<button class="primary"' + (busy ? ' disabled' : '') + ' onclick="ccStartSignin(\'' + jsq(id) + '\')">' + (busy === 'signin-start' ? 'Starting…' : 'Sign in') + '</button>';
  } else {
    dot = 'on'; text = 'Ready';
    btn = '<button' + (busy ? ' disabled' : '') + ' onclick="ccAct(\'' + jsq(id) + '\', \'test\')">' + (busy === 'test' ? 'Testing…' : 'Test') + '</button>' +
      '<button class="ghost"' + (busy ? ' disabled' : '') + ' onclick="ccAct(\'' + jsq(id) + '\', \'signout\')">Sign out</button>';
  }
  if (c.installed && c.state !== 'installing' && c.state !== 'signing_in' && !(c.state === 'needs_signin' && ccOpen[id]) && busy !== 'uninstall') btn += '<button class="ghost"' + (busy ? ' disabled' : '') + ' onclick="opAskUninstall(\'' + jsq(id) + '\', \'claude\')">Uninstall</button>';
  if (c.updateAvailable && busy !== 'uninstall') btn += '<button class="ghost"' + (busy ? ' disabled' : '') + ' title="Version ' + esc(c.latestVersion || '') + '" onclick="ccAct(\'' + jsq(id) + '\', \'update\')">Update</button>';
  let h = '<div class="op-prog">' + opDot(dot, text) + '<b>Claude</b> <span class="lm-muted">Subscription · ' + esc(text) + '</span><span class="op-grow"></span>' + btn + '</div>' + bar;
  const line = ccLine[id];
  if (dot === 'warn' && (c.state === 'needs_signin' || c.warning) && typeof claudeTooOld === 'function' && claudeTooOld(c.version)) h += ccWarn(CLAUDE_OLD_LINE, r);   // an old program is the likeliest cause, so it comes first and alone
  else if (line && line.kind === 'ok') h += '<div class="lm-muted" style="padding:0 0 6px 17px">' + esc(line.text) + '</div>';
  else { const w = (line && line.kind === 'warn') ? line.text : (c.warning || ''); if (w) h += ccWarn(w, r); }
  if (c.state === 'needs_signin' && busy === 'signin-start') h += '<div class="lm-muted" style="padding:0 0 6px 17px">' + (/^win/.test(c.platform || '') ? 'Opening the sign-in page on ' + esc(ccName(r)) + '…' : 'Getting a sign-in link…') + '</div>';
  if (c.state === 'needs_signin' && ccOpen[id]) h += ccSignIn(r);
  if (c.state === 'signing_in' && c.signin && (c.signin.state === 'link' || c.signin.state === 'checking')) h += (c.signin.state === 'link' && ccWayOf(id, c.signin) === 1) ? ccWay1(r) : ccPageSignIn(r, c.signin);
  if (c.state === 'signing_in' && c.signin && c.signin.state === 'starting' && ccWayOf(id, c.signin) === 1) h += '<div class="lm-muted" style="padding:0 0 6px 17px">Opening the sign-in page on ' + esc(ccName(r)) + '…</div>';
  return h;
}
// The three ways to sign in (design/mockup-claude-signin-row.html): 1) a page that opens on that device itself and finishes by itself, 2) a link and a short code, 3) a terminal.
// Way 1 shows first when the device says the program opens its page there (or cannot tell), Way 2 first when nothing can open there; the small lines move between them.
function ccWayOf(id, sg) { return ccWay[id] || ((sg && sg.browser === 'none') ? 2 : 1); }
function ccSetWay(id, w) { ccWay[id] = w; if (typeof opRender === 'function') opRender(); }
function ccSmall(html) { return '<div class="lm-muted" style="margin-top:8px">' + html + '</div>'; }
function ccLinkTo(js, text) { return '<span class="lm-link" onclick="' + js + '">' + text + '</span>'; }
function ccWay1(r) {
  const id = r.id, n = esc(ccName(r));
  return '<div style="padding:4px 0 8px 17px"><ol class="op-steps"><li>A sign-in page opened on <b>' + n + '</b>.</li><li>Sign in to Claude there and authorize access.</li></ol>' +
    ccSmall('No browser on ' + n + '? ' + ccLinkTo('ccSetWay(\'' + jsq(id) + '\', 2)', 'Click here')) + '</div>';
}
function ccPageSignIn(r, sg) {
  const id = r.id, checking = sg.state === 'checking';
  return '<div style="padding:4px 0 8px 17px"><ol class="op-steps">' +
    '<li>Open the sign-in page on any device with a browser, sign in to Claude and authorize access.</li>' +
    '<li>Once authorized, Claude will provide a code.</li>' +
    '<li>Copy the code, paste it here, and press <b>Save</b>.</li></ol>' +
    '<div class="op-dl"><button class="primary"' + (ccLinkOk(sg.url) ? '' : ' disabled') + ' onclick="ccOpenLink(\'' + jsq(id) + '\')">Open the sign-in page</button>' +
    '<input readonly spellcheck="false" aria-label="The sign-in link" value="' + esc(sg.url || '') + '" onfocus="this.select()">' +
    '<button class="copy"' + (ccLinkOk(sg.url) ? '' : ' disabled') + ' onclick="ccCopyLink(this, \'' + jsq(id) + '\')">Copy</button></div>' +
    '<div class="op-dl" style="margin-top:6px"><input type="password" autocomplete="off" spellcheck="false" id="ccPageCode_' + esc(id) + '" placeholder="Paste the code from the page" value="' + esc(ccPageCode[id] || '') + '" ' +
    (checking ? 'disabled ' : '') + 'oninput="ccPageCode[\'' + jsq(id) + '\'] = this.value" onkeydown="if(event.key===\'Enter\')ccSavePageCode(\'' + jsq(id) + '\')">' +
    '<button class="primary"' + (checking || ccBusy[id] ? ' disabled' : '') + ' onclick="ccSavePageCode(\'' + jsq(id) + '\')">Save</button></div>' +
    ccWay2Small(id, sg) + '</div>';
}
function ccWay2Small(id, sg) {
  return ccSmall('Something went wrong? ' + ccLinkTo('ccToTerminal(\'' + jsq(id) + '\')', 'Get a sign-in code using a terminal instead') +
    (sg && sg.browser === 'none' ? '' : ' &nbsp;·&nbsp; ' + ccLinkTo('ccSetWay(\'' + jsq(id) + '\', 1)', 'Back')));
}
function ccCopyLink(btn, id) {
  const r = ccPcById(id), u = r && r.report && r.report.claude && r.report.claude.signin && r.report.claude.signin.url;
  if (ccLinkOk(u) && typeof opCopy === 'function') opCopy(btn, u);
}
// Way 3: the person runs the program's own command on that device and pastes the long code it prints (saved exactly like any pasted code: the `signin` action)
async function ccToTerminal(id) {
  await ccAct(id, 'signin-cancel');                                 // the Courier's own try is stopped: there is only one sign-in program at a time
  ccOpen[id] = true; ccCodeText[id] = '';
  if (typeof opRender === 'function') opRender();
}
async function ccFromTerminal(id) {
  ccOpen[id] = false; ccCodeText[id] = '';
  await ccStartSignin(id, 2);                                       // back to the link and a code
}
function ccPcById(id) { return ((typeof opState !== 'undefined' && opState) ? opState.servers : []).find(x => x.id === id); }
function ccOpenLink(id) {
  const r = ccPcById(id), u = r && r.report && r.report.claude && r.report.claude.signin && r.report.claude.signin.url;
  if (ccLinkOk(u)) window.open(u, '_blank', 'noopener');          // only Anthropic's own sign-in addresses, on the device this page runs on
}
async function ccStartSignin(id, way) {
  ccOpen[id] = false; ccPageCode[id] = ''; if (way) ccWay[id] = way; else delete ccWay[id];
  ccWatchOn(id);
  await ccAct(id, 'signin-start');
  if (ccLine[id] && ccLine[id].kind === 'warn') { ccOpen[id] = true; if (typeof opRender === 'function') opRender(); }      // it could not start: the older way opens, under the one plain line
}
async function ccSavePageCode(id) {
  const code = ccPageCode[id] || '';
  if (!code.trim()) return;
  await ccAct(id, 'signin-code', { code });
  ccPageCode[id] = '';                                             // the code leaves this page the moment it was sent
  if (typeof opRender === 'function') opRender();
}
async function ccCancelSignin(id) {
  ccPageCode[id] = '';
  delete ccWatch[id];
  await ccAct(id, 'signin-cancel');
}
function ccSignIn(r) {
  const id = r.id;
  return '<div style="padding:4px 0 8px 17px"><ol class="op-steps">' +
    '<li>On <b>' + esc(ccName(r)) + '</b>, open a terminal.</li>' +
    '<li>Run this command:<div class="op-dl" style="margin:4px 0"><code>claude setup-token</code><button class="copy" onclick="opCopy(this, \'claude setup-token\')">Copy</button></div></li>' +
    '<li>Claude will provide a link. Paste the link into a browser on any device, sign in to Claude and authorize access.</li>' +
    '<li>Copy the code, paste it here, and press <b>Save</b>.</li></ol>' +
    '<div class="op-dl"><input type="password" autocomplete="off" spellcheck="false" id="ccCode_' + esc(id) + '" placeholder="Paste the code from the terminal" value="' + esc(ccCodeText[id] || '') + '" ' +
    'oninput="ccCodeText[\'' + jsq(id) + '\'] = this.value" onkeydown="if(event.key===\'Enter\')ccSave(\'' + jsq(id) + '\')">' +
    '<button class="primary"' + (ccBusy[id] ? ' disabled' : '') + ' onclick="ccSave(\'' + jsq(id) + '\')">Save</button></div>' +
    ccSmall(ccLinkTo('ccFromTerminal(\'' + jsq(id) + '\')', 'Back')) + '</div>';
}
function ccToggle(id) {
  ccOpen[id] = !ccOpen[id];
  if (!ccOpen[id]) ccCodeText[id] = '';
  if (typeof opRender === 'function') opRender();
}
async function ccAct(id, what, body) {
  ccBusy[id] = what; ccLine[id] = null;
  if (what === 'signin-cancel') delete ccWatch[id];
  if (typeof opRender === 'function') opRender();
  try {
    const res = await lmPost('/config/claude/pc/' + what, Object.assign({ id }, body || {}));
    if (what === 'test') {
      const r = ((typeof opState !== 'undefined' && opState) ? opState.servers : []).find(x => x.id === id);
      ccLine[id] = res && res.ok ? { kind: 'ok', text: 'Answered by ' + (res.model ? ccModelLabel(res.model) : 'Claude') + ' on ' + (r ? ccName(r) : 'that device') + ' in ' + res.seconds + ' s' }
                                 : { kind: 'warn', text: (res && res.error) || "Claude didn't answer." };
    }
  } catch (e) { ccLine[id] = { kind: 'warn', text: e.message || "Couldn't reach that device." }; }
  ccBusy[id] = '';
  if (typeof opRefresh === 'function') opRefresh(true);
}
async function ccSave(id) {
  const code = ccCodeText[id] || '';
  if (!code.trim()) return;
  await ccAct(id, 'signin', { code });
  if (!ccLine[id]) { ccOpen[id] = false; }          // saved and tried: the row says Ready
  ccCodeText[id] = '';                              // the code leaves this page the moment it was sent
  if (typeof opRender === 'function') opRender();
}
function ccModelLabel(id) {
  const p = String(id).split('-').filter(x => !(x.length === 8 && /^\d+$/.test(x)));
  return (p.length >= 3 && p[0] === 'claude') ? p[1].charAt(0).toUpperCase() + p[1].slice(1) + ' ' + p.slice(2).join('.') : id;
}
"""
