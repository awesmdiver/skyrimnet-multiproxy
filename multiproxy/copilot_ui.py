"""Copilot on another device, its row on the Other devices tab (2026-10-06). The same row as Claude's (`claude_ui.py`), after it on a Courier device's card
(`other_pcs_ui.opCourierBody` calls `cpPcRow` when it exists). It borrows Claude's small helpers (`ccFmt`, `ccName`, `ccWarn`): the two files are one page.

  Not installed   -> Install (a progress bar while it installs; Cancel)
  Needs sign-in   -> Sign in (asks that device for a sign-in code), and the one warning line when a sign-in ran out or didn't finish
  Signing in      -> three numbered steps with the sign-in code shown large; the row turns Ready by itself when the person has approved it at GitHub
  Ready           -> Test, Sign out
  Installed       -> Uninstall (the same confirm as the other programs: `opAskUninstall` in other_pcs_ui.py)

Update shows beside the buttons only when a newer version is known. Nothing secret is typed or kept here: the sign-in code is a one-time code the person types at GitHub.

Every on-screen string here is quoted in the handoff (the design side runs them through a Gemini pass).
"""

COPILOT_JS = r"""// ---------------- Copilot on another device (copilot_ui.py) ----------------
const cpBusy = {};        // device id -> which action is running
const cpLine = {};        // device id -> {kind: 'warn' | 'ok', text}: the last answer to something the player pressed
function cpPcRow(r) {
  const c = (r.report || {}).copilot;
  if (!c) return '';
  const id = r.id, j = c.install, busy = cpBusy[id] || '';
  let dot, text, btn = '', bar = '';
  if (busy === 'uninstall') {
    dot = 'warn'; text = 'Uninstalling';
  } else if (c.state === 'installing' && j) {
    dot = 'warn'; text = (j.updating ? 'Updating' : 'Installing') + (j.step && j.step !== 'Downloading' ? ' · ' + String(j.step).toLowerCase() : (j.total ? ' · ' + (j.percent || 0) + '%' : ''));
    btn = '<button class="ghost" onclick="cpAct(\'' + jsq(id) + '\', \'cancel\')">Cancel</button>';
    if (j.total && j.step === 'Downloading') bar = '<div class="lm-bar" style="margin-left:17px"><i style="width:' + (j.percent || 0) + '%"></i></div>';
  } else if (c.state === 'not_installed') {
    dot = 'none'; text = 'Not installed';
    btn = '<button class="primary"' + (busy ? ' disabled' : '') + ' onclick="cpAct(\'' + jsq(id) + '\', \'install\')">Install</button>';
  } else if (c.state === 'signing_in') {
    dot = 'warn'; text = (c.signin && c.signin.code) ? 'Waiting for the sign-in' : 'Getting a sign-in code';
    btn = '<button class="ghost" onclick="cpAct(\'' + jsq(id) + '\', \'cancelsignin\')">Cancel</button>';
  } else if (c.state === 'needs_signin') {
    dot = 'warn'; text = 'Needs sign-in';
    btn = '<button class="primary"' + (busy ? ' disabled' : '') + ' onclick="cpAct(\'' + jsq(id) + '\', \'signin\')">Sign in</button>';
  } else {
    dot = 'on'; text = 'Ready';
    btn = '<button' + (busy ? ' disabled' : '') + ' onclick="cpAct(\'' + jsq(id) + '\', \'test\')">' + (busy === 'test' ? 'Testing…' : 'Test') + '</button>' +
      '<button class="ghost"' + (busy ? ' disabled' : '') + ' onclick="cpAct(\'' + jsq(id) + '\', \'signout\')">Sign out</button>';
  }
  if (c.installed && c.state !== 'installing' && c.state !== 'signing_in' && busy !== 'uninstall') btn += '<button class="ghost"' + (busy ? ' disabled' : '') + ' onclick="opAskUninstall(\'' + jsq(id) + '\', \'copilot\')">Uninstall</button>';
  if (c.updateAvailable && busy !== 'uninstall' && c.state !== 'signing_in') btn += '<button class="ghost"' + (busy ? ' disabled' : '') + ' title="Version ' + esc(c.latestVersion || '') + '" onclick="cpAct(\'' + jsq(id) + '\', \'update\')">Update</button>';
  let h = '<div class="op-prog">' + opDot(dot, text) + '<b>Copilot</b> <span class="lm-muted">Subscription · ' + esc(text) + '</span><span class="op-grow"></span>' + btn + '</div>' + bar;
  const line = cpLine[id];
  if (line && line.kind === 'ok') h += '<div class="lm-muted" style="padding:0 0 6px 17px">' + esc(line.text) + '</div>';
  else { const w = (line && line.kind === 'warn') ? line.text : (c.warning || ''); if (w) h += ccWarn(w, r); }
  if (c.state === 'signing_in' && c.signin && c.signin.code) h += cpSignIn(r, c.signin);
  return h;
}
function cpSignIn(r, s) {
  const where = String(s.url || 'https://github.com/login/device').replace(/^https:\/\//, '');
  return '<div style="padding:4px 0 8px 17px"><ol class="op-steps">' +
    '<li>On any device, open <code>' + esc(where) + '</code>.</li>' +
    '<li>Type this sign-in code: <code style="font-size:1.5em;letter-spacing:.12em">' + esc(s.code) + '</code></li>' +
    '<li>Approve the sign-in. This row turns Ready by itself.</li></ol>' +
    '</div>';
}
async function cpAct(id, what) {
  cpBusy[id] = what; cpLine[id] = null;
  if (typeof opRender === 'function') opRender();
  try {
    const res = await lmPost('/config/copilot/pc/' + what, { id });
    if (what === 'test') {
      const r = ((typeof opState !== 'undefined' && opState) ? opState.servers : []).find(x => x.id === id);
      cpLine[id] = res && res.ok ? { kind: 'ok', text: 'Answered by Copilot on ' + (r ? ccName(r) : 'that device') + ' in ' + res.seconds + ' s' }
                                 : { kind: 'warn', text: (res && res.error) || "Copilot didn't answer." };
    }
  } catch (e) { cpLine[id] = { kind: 'warn', text: e.message || "Couldn't reach that device." }; }
  cpBusy[id] = '';
  if (typeof opRefresh === 'function') opRefresh(true);
}
"""
