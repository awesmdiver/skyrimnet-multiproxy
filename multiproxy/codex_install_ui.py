"""The ChatGPT (Codex) card's install part (2026-10-26): "Not installed" gets an Install button, with a progress bar and Cancel while it runs, the same pattern as the Claude and
Copilot rows (`claude_ui.py`, `copilot_ui.py`). All the words that need to be said come from the server (`codex_install.py`), so the only words here are the button names.

  Not installed        -> a dot, "Not installed", Install, and one line saying what is wrong (signed in but no program, or just not installed)
  Installing           -> the step (and the percent while it downloads), a bar, Cancel
  The last one failed  -> the one warning line in the existing amber style, and Install again
  Installed            -> nothing here: the normal card

When it finishes the page asks the card for its real state (the same call as Check for models), so the dot turns the right colour without a restart.
"""

CODEX_INSTALL_JS = r"""// ---------------- ChatGPT (Codex) card: install (codex_install_ui.py) ----------------
let codexInstallTimer = null;
function codexInstallHtml(p) {
  const c = p.installCard;
  if (!c || !c.show) return '';
  const j = c.job;
  if (j && j.state === 'running') codexInstallWatch();       // (a page opened or reloaded while it runs keeps following it)
  if (j && j.state === 'running') {
    const bar = j.step === 'Downloading' ? '<div class="lm-bar"><i style="width:' + (j.percent || 0) + '%"></i></div>' : '';
    return '<div class="op-prog"><span class="op-dot warn"></span><b>Installing</b> <span class="lm-muted">' + esc(j.step) + (j.step === 'Downloading' ? ' · ' + (j.percent || 0) + '%' : '') +
      '</span><span class="op-grow"></span><button class="ghost" onclick="codexInstallCancel()">Cancel</button></div>' + bar;
  }
  const warn = j && j.state === 'error' ? '<div class="vc-warn"><span class="op-dot warn"></span><span>' + esc(j.error) + '</span></div>' : '';
  const line = j && j.state === 'error' ? '' : '<div class="lm-muted" style="padding:0 0 6px 17px">' + esc(c.line) + '</div>';
  const btn = c.canInstall ? '<button class="primary" onclick="codexInstallStart()">Install</button>' : '';
  return '<div class="op-prog"><span class="op-dot none"></span><b>Codex</b> <span class="lm-muted">Not installed</span><span class="op-grow"></span>' + btn + '</div>' + line + warn;
}
function codexInstallRedraw(p) {
  if (typeof selectedProvider !== 'undefined' && selectedProvider === 'codex') renderCodexPanel(p);
}
async function codexInstallStart() {
  const p = byId('codex');
  try {
    const r = await fetch('/config/codex/install', { method: 'POST' });
    const body = await r.json();
    if (!r.ok) { p.installCard = Object.assign({}, p.installCard, { show: true, job: { state: 'error', error: body.detail || "Couldn't start the install." } }); codexInstallRedraw(p); return; }
    p.installCard = body;
  } catch (e) { p.installCard = Object.assign({}, p.installCard, { show: true, job: { state: 'error', error: e.message } }); codexInstallRedraw(p); return; }
  codexInstallRedraw(p);
  codexInstallWatch();
}
async function codexInstallCancel() {
  try { const r = await fetch('/config/codex/install/cancel', { method: 'POST' }); byId('codex').installCard = await r.json(); } catch (e) {}
  codexInstallRedraw(byId('codex'));
}
function codexInstallWatch() {
  if (codexInstallTimer) return;
  codexInstallTimer = setInterval(async () => {
    const p = byId('codex');
    try {
      const c = await (await fetch('/config/codex/install')).json();
      const was = p.installCard && p.installCard.job && p.installCard.job.state === 'running';
      p.installCard = c;
      if (!c.job || c.job.state !== 'running') {
        clearInterval(codexInstallTimer); codexInstallTimer = null;
        if (was && !c.job) { await _refreshLiveEntry(p); updatePickerOption(p); }        // it finished: the card asks for its real state
        if (typeof selectedProvider !== 'undefined' && selectedProvider === 'codex') selectProvider('codex');
        return;
      }
    } catch (e) {}
    codexInstallRedraw(p);
  }, 1000);
}
"""
