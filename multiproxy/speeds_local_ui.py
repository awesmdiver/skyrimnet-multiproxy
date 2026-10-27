"""The Speeds tab's "Local models" view (2026-10-04): every local model side by side with the setup it is running under, its real tokens per second (the average of its
last 20 warm replies under that setup), and the number from before the last settings change. Built to design/local-model-settings-mockup.html ("Speeds tab: local models,
side by side"). Plain strings in their own file, spliced into multiproxy.py's page; the toggle and the view are added to the Speeds card by the script itself, the same way
speeds_time_ui.py does it. The data is `GET /config/local-models/speeds` (speed_queries.local_model_speeds). Everything from the server goes through _escapeHtml.
"""

SPEEDS_LOCAL_CSS = r"""
  /* Speeds tab: local models side by side -- sl-* classes; the card, view toggle and role-table look are reused. Text uses the card's full width. */
  .sl-hint { color:var(--muted); font-size:0.85rem; margin:10px 0 12px }
  .sl-hint ol { margin:6px 0 0; padding-left:20px }
  .sl-table td.n { font-variant-numeric:tabular-nums; white-space:nowrap }
  .sl-name { font-weight:600 }
  .sl-small { font-size:0.74rem; color:var(--muted); margin-top:3px }
  .sl-big { font-size:1.05rem; font-weight:600 }
  .sl-none { color:var(--muted) }
"""

SPEEDS_LOCAL_JS = r"""// ---------------- Speeds: local models side by side ----------------
let slLoaded = false;
const SL_HINT = ['This is the speed each model reports, averaged over its last 20 replies.',
  'When you change a setting, the old number stays next to the new one so you can see if it helped.'];
function slInit() {
  const toggle = document.getElementById('optSpeedsTime') || document.getElementById('optSpeedsByRole');
  const after = document.getElementById('speedsTimeView') || document.getElementById('speedsByRoleView');
  if (!toggle || !after || document.getElementById('optSpeedsLocal')) return;
  toggle.insertAdjacentHTML('afterend', '<span class="view-opt" id="optSpeedsLocal" onclick="showSpeedsView(\'local\')">Local models</span>');
  after.insertAdjacentHTML('afterend', '<div id="speedsLocalView" style="display:none"><div class="sl-hint" id="slHint"></div><div id="slBody"></div></div>');
}
function slOpened() {
  if (!document.getElementById('speedsLocalView')) return;
  $('optSpeedsLocal').className = 'view-opt active';
  slLoaded = true;
  fetchLocalSpeeds();
}
function slSpeedCell(m) {
  if (m.state === 'ok') {
    return '<div><span class="sl-big">' + Math.round(m.tokensPerSecond) + '</span> <span class="sl-small">tokens/s</span></div>' +
      '<div class="sl-small">last ' + m.requests + ' request' + (m.requests === 1 ? '' : 's') +
      (m.previous ? ' (was ' + Math.round(m.previous.tokensPerSecond) + ' before the last change)' : '') + '</div>';
  }
  const word = m.state === 'not_reported' ? 'Not reported' : 'Not enough requests yet';
  return '<span class="sl-none">' + word + '</span>' +
    (m.previous ? '<div class="sl-small">(was ' + Math.round(m.previous.tokensPerSecond) + ' before the last change)</div>' : '');
}
async function fetchLocalSpeeds() {
  if (!document.getElementById('speedsLocalView')) return;
  let data = null;
  try { data = await (await fetch('/config/local-models/speeds')).json(); } catch (e) { return; }
  const models = (data && data.models) || [];
  $('slHint').innerHTML = '<b>Local models, side by side.</b><ol>' + SL_HINT.map(t => '<li>' + _escapeHtml(t) + '</li>').join('') + '</ol>';
  if (!models.length) { $('slBody').innerHTML = '<div class="empty">No local models yet. Download one from the Local device tab.</div>'; return; }
  let h = '<table class="role-table sl-table"><thead><tr><th>Local model</th><th>Setup</th><th>Tokens per second</th><th>Typical wait for a reply</th></tr></thead><tbody>';
  for (const m of models) {
    h += '<tr><td class="sl-name">' + _escapeHtml(m.name) + '</td><td>' + _escapeHtml(m.setupWords || '') + '</td><td>' + slSpeedCell(m) + '</td><td class="n">' +
      (m.state === 'ok' && m.typicalWaitSeconds != null ? m.typicalWaitSeconds.toFixed(1) + ' s' : '<span class="sl-none">—</span>') + '</td></tr>';
  }
  $('slBody').innerHTML = h + '</tbody></table>';
}
setTimeout(slInit, 0);
"""
