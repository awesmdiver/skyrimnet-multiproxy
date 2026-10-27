"""The Speeds tab's "Time breakdown" view (2026-10-02, Routing step 5): per job and model, the typical line as a three-part bar (waiting in line, reading the prompt up to
the first word, writing), the total, the writing speed (and how much of the prompt a local model remembered), tokens and cost, for the range and the device filter the tab
already has. Plain strings in their own file, spliced into multiproxy.py's page; the view and its toggle are added to the Speeds card by the script itself, so a page
without a Speeds tab has none of it. The data is `GET /stats/speeds/time` (speeds_time.py). Everything from the server goes through _escapeHtml before it reaches HTML.
"""

SPEEDS_TIME_CSS = r"""
  /* Speeds tab: the time breakdown -- st-* classes; the card, view toggle and the role-table look are reused */
  .stt-legend { display:flex; flex-wrap:wrap; gap:6px 18px; font-size:0.8rem; color:var(--muted); margin:10px 0 4px }
  .stt-sw { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px }
  .stt-wait { background:var(--muted) } .stt-read { background:color-mix(in srgb, var(--accent) 40%, var(--bg)) } .stt-write { background:var(--accent) } .stt-whole { background:repeating-linear-gradient(45deg, var(--line), var(--line) 4px, transparent 4px, transparent 8px) }
  .stt-bar { display:flex; height:10px; border-radius:5px; overflow:hidden; min-width:180px; background:var(--panel) }
  .stt-bar i { display:block; height:100% }
  .stt-small { font-size:0.74rem; color:var(--muted); margin-top:3px }
  .stt-total { margin-top:14px; font-size:0.85rem }
  .stt-table td.n { font-variant-numeric:tabular-nums; white-space:nowrap }
  .stt-job { font-weight:600 }
"""

SPEEDS_TIME_JS = r"""// ---------------- Speeds: the time breakdown ----------------
let sttLoaded = false;
const STT_LEGEND = ['waiting in line', 'reading the prompt, up to the first word', 'writing'];
const STT_REMEMBER_NOTE = 'Local models also show how much of the prompt they remembered from last time.';

function sttInit() {
  const toggle = document.getElementById('optSpeedsByRole'), after = document.getElementById('speedsByRoleView');
  if (!toggle || !after || document.getElementById('optSpeedsTime')) return;
  toggle.insertAdjacentHTML('afterend', '<span class="view-opt" id="optSpeedsTime" onclick="showSpeedsView(\'time\')">Time breakdown</span>');
  after.insertAdjacentHTML('afterend', '<div id="speedsTimeView" style="display:none"><div class="stt-legend" id="sttLegend"></div><div id="sttBody"></div></div>');
}
function sttOpened() {
  if (!document.getElementById('speedsTimeView')) return;
  $('optSpeedsTime').className = 'view-opt active';
  sttLoaded = true;
  fetchTime();
}
function fmtCostNum(c) { return '$' + (c < 0.01 ? c.toFixed(4) : c.toFixed(3)); }
function fmtCount(n) { return n == null ? '—' : n.toLocaleString(); }
function sttBar(g) {
  const parts = [], total = (g.waitKnown ? (g.wait || 0) : 0) + (g.read != null ? g.read + (g.write || 0) : (g.whole || 0));
  if (!(total > 0)) return '<span class="stt-small">not reported</span>';
  const w = v => (100 * v / total).toFixed(1) + '%';
  if (g.waitKnown && g.wait > 0) parts.push('<i class="stt-wait" style="width:' + w(g.wait) + '" title="waiting in line ' + g.wait.toFixed(1) + ' s"></i>');
  if (g.read != null) {
    parts.push('<i class="stt-read" style="width:' + w(g.read) + '" title="reading the prompt, up to the first word ' + g.read.toFixed(1) + ' s"></i>');
    if (g.write > 0) parts.push('<i class="stt-write" style="width:' + w(g.write) + '" title="writing ' + g.write.toFixed(1) + ' s"></i>');
  } else if (g.whole != null) parts.push('<i class="stt-whole" style="width:' + w(g.whole) + '" title="the whole reply ' + g.whole.toFixed(1) + ' s"></i>');
  return '<div class="stt-bar">' + parts.join('') + '</div>' + (g.read == null && g.whole != null ? '<div class="stt-small">whole reply (not streamed)</div>' : '');
}
function sttSpeed(g) {
  const v = speedUnit === 'words' ? g.wordsPerSecond : g.tokensPerSecond;
  if (v == null) return '<span class="stt-small">not reported</span>';
  return (g.speedEstimated ? '≈ ' : '') + Math.round(v) + ' ' + (speedUnit === 'words' ? 'words/s' : 'tokens/s') +
    (g.remembered != null ? '<div class="stt-small">remembered ' + Math.round(g.remembered) + '% of the prompt</div>' : '');
}
function sttCost(g) {
  if (g.costTypical != null) return fmtCostNum(g.costTypical) + ' a line' + (g.costTotal != null ? '<div class="stt-small">' + fmtCostNum(g.costTotal) + ' in this range</div>' : '');
  return g.costKind;
}
function sttTokens(g) {
  if (g.tokensIn == null && g.tokensOut == null) return '<span class="stt-small">not reported</span>';
  return 'in ' + fmtCount(g.tokensIn) + '<div class="stt-small">out ' + fmtCount(g.tokensOut) + '</div>';
}
async function fetchTime() {
  if (!document.getElementById('speedsTimeView')) return;
  let data = null;
  try { data = await (await fetch('/stats/speeds/time?range=' + encodeURIComponent(currentSpeedRange) + pcParam())).json(); } catch (e) { return; }
  const groups = (data && data.groups) || [];
  $('sttLegend').innerHTML = '<span><i class="stt-sw stt-wait"></i>' + _escapeHtml(STT_LEGEND[0]) + '</span><span><i class="stt-sw stt-read"></i>' + _escapeHtml(STT_LEGEND[1]) +
    '</span><span><i class="stt-sw stt-write"></i>' + _escapeHtml(STT_LEGEND[2]) + '</span><span>' + _escapeHtml(STT_REMEMBER_NOTE) + '</span>';
  if (!groups.length) { $('sttBody').innerHTML = '<div class="empty">Nothing to show for this range yet.</div>'; return; }
  let h = '<table class="role-table stt-table"><thead><tr><th>Job</th><th>Model</th><th>Typical line</th><th>First word</th><th>Total</th><th>Succeeded</th><th>Writing speed</th><th>Tokens</th><th>Cost</th></tr></thead><tbody>';
  let lastJob = null;
  for (const g of groups) {
    const showJob = g.label !== lastJob;
    lastJob = g.label;
    h += '<tr><td class="stt-job">' + (showJob ? _escapeHtml(g.label) : '') + '</td><td>' + _escapeHtml(g.model) + '<div class="stt-small">' + _escapeHtml(g.where) + ' · ' + g.calls + (g.calls === 1 ? ' line' : ' lines') + '</div></td>' +
      '<td>' + sttBar(g) + '</td><td class="n">' + (g.read != null ? g.read.toFixed(1) + ' s' : '<span class="stt-small">not reported</span>') + '</td><td class="n">' + (g.total != null ? g.total.toFixed(1) + ' s' : '<span class="stt-small">not reported</span>') +
      '</td><td class="n">' + (g.calls ? Math.round(100 * g.ok / g.calls) + '%' : '<span class="stt-small">not reported</span>') + '</td><td>' + sttSpeed(g) + '</td><td class="n">' + sttTokens(g) + '</td><td class="n">' + sttCost(g) + '</td></tr>';
  }
  h += '</tbody></table>';
  const t = data.total || {};
  h += '<div class="stt-total">' + (t.cost != null ? 'Cost in this range for pay-as-you-go models that report it: <b>' + fmtCostNum(t.cost) + '</b>.' : 'No pay-as-you-go model reported a cost in this range.') +
    (t.notCounted && t.notCounted.length ? ' Not counted because they report no cost: ' + t.notCounted.map(_escapeHtml).join(', ') + '.' : '') + '</div>';
  $('sttBody').innerHTML = h;
}
setTimeout(sttInit, 0);
"""
