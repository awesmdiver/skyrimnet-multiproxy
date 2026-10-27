"""The Speeds tab's "Prompt size" view and the Local models warning (2026-10-25). Built to design/mockup-listening-top-3.html, section 1: how big SkyrimNet's prompts are for each
job, set against the context of the model that answers it. Plain strings in their own file, spliced into multiproxy.py's page; the view and its toggle are added to the Speeds
card by the script itself (the same way speeds_time_ui.py and speeds_local_ui.py do it), so a page without a Speeds tab has none of it. The data is `GET /config/prompt-size`
(prompt_size.py): every number and every verdict sentence is worked out there, this file only draws them. Everything from the server goes through _escapeHtml.

Two places show it: the card (always), and one warning line on a local model's row on the Local models tab, only when today's biggest prompt for a job that model answers is over
70% of its context (nothing is drawn when all is well).
"""

PROMPT_SIZE_CSS = r"""
  /* Speeds tab: Prompt size -- psz-* classes; the card, view toggle, range picker and role-table look are reused. Text uses the card's full width. */
  .psz-hint { color:var(--muted); font-size:0.85rem; margin:10px 0 12px }
  .psz-hint ol { margin:6px 0 0; padding-left:20px }
  .psz-table td.n { font-variant-numeric:tabular-nums; white-space:nowrap }
  .psz-table tr.psz-row { cursor:pointer }
  .psz-table tr.psz-on td { background:color-mix(in srgb, var(--accent) 8%, transparent) }
  .psz-job { font-weight:600 }
  .psz-small { font-size:0.74rem; color:var(--muted); margin-top:3px }
  .psz-strip { position:relative; height:10px; min-width:160px; background:var(--panel); border:1px solid var(--line); border-radius:6px }
  .psz-typ { position:absolute; left:0; top:0; bottom:0; background:var(--muted); opacity:0.55; border-radius:6px 0 0 6px }
  .psz-max { position:absolute; top:-3px; bottom:-3px; width:3px; margin-left:-3px; border-radius:2px; background:var(--accent) }
  .psz-max.lv-warn { background:var(--warn) } .psz-max.lv-bad { background:var(--err) }
  .psz-warn { color:var(--warn) }
  .psz-spark { display:flex; align-items:flex-end; gap:2px; height:28px; margin-top:6px }
  .psz-spark i { display:block; width:6px; min-height:1px; background:var(--muted); opacity:0.7; border-radius:1px }
  .psz-range { display:inline-flex; margin:0 0 12px }
"""

PROMPT_SIZE_JS = r"""// ---------------- Speeds: prompt size ----------------
let pszLoaded = false;
let pszRange = 'today';
let pszSel = null;                // the job whose last 24 hours are drawn under the table
let pszData = null;               // the last answer
let pszToday = [];                // today's jobs, for the warning line on the Local models tab
const PSZ_STEPS = ['Play for a while, then come back here.',
  'Find your job. The orange mark is its biggest prompt. The grey bar is a typical one.',
  'On the Local device tab, set the context slightly above the orange mark.'];
const PSZ_RANGES = [['hour', 'Last hour'], ['today', 'Today'], ['week', '7 days']];
function pszInit() {
  const toggle = document.getElementById('optSpeedsLocal') || document.getElementById('optSpeedsTime') || document.getElementById('optSpeedsByRole');
  const after = document.getElementById('speedsLocalView') || document.getElementById('speedsTimeView') || document.getElementById('speedsByRoleView');
  if (!toggle || !after || document.getElementById('optSpeedsSize')) return;
  toggle.insertAdjacentHTML('afterend', '<span class="view-opt" id="optSpeedsSize" onclick="showSpeedsView(\'size\')">Prompt size</span>');
  after.insertAdjacentHTML('afterend', '<div id="speedsSizeView" style="display:none"><div class="psz-hint"><b>Prompt size</b><ol>' +
    PSZ_STEPS.map(t => '<li>' + _escapeHtml(t) + '</li>').join('') + '</ol></div><div class="range-picker psz-range" id="pszRangePicker"></div><div id="pszBody"></div></div>');
}
function pszOpened() {
  if (!document.getElementById('speedsSizeView')) return;
  pszLoaded = true;
  pszFetch();
}
function pszPickRange(r) { pszRange = r; pszFetch(); }
function pszPick(job) { pszSel = job; pszDraw(); }
async function pszFetch() {
  if (!document.getElementById('speedsSizeView')) return;
  let data = null;
  try { data = await (await fetch('/config/prompt-size?range=' + encodeURIComponent(pszRange))).json(); } catch (e) { return; }
  pszData = data && data.jobs ? data : { jobs: [] };
  if (!pszData.jobs.some(j => j.job === pszSel)) pszSel = pszData.jobs.length ? pszData.jobs[0].job : null;
  pszDraw();
}
function pszMeans(j) {
  const v = j.verdict || {};
  if (v.level === 'ok') return '<span class="hverdict lv-ok"><span class="hdot"></span>' + _escapeHtml(v.text) + '</span>';
  if (v.level === 'warn') return '<span class="hverdict lv-warn"><span class="hdot"></span><span class="psz-warn">⚠️ ' + _escapeHtml(v.text) + '</span></span>';
  if (v.level === 'bad') return '<span class="hverdict lv-bad"><span class="hdot"></span>' + _escapeHtml(v.text) + '</span>';
  return '—';
}
function pszStrip(j) {
  const v = j.verdict || {};
  if (!j.context) return '<span class="psz-small">' + _escapeHtml(v.text || '') + '</span>';
  const typ = Math.min(100, 100 * j.typical / j.context), max = Math.min(100, 100 * j.biggest / j.context);
  return '<div class="psz-strip"><i class="psz-typ" style="width:' + typ.toFixed(1) + '%"></i><i class="psz-max lv-' + _escapeHtml(v.level || 'ok') + '" style="left:' + max.toFixed(1) + '%"></i></div>' +
    '<div class="psz-small">context ' + _escapeHtml(j.contextText) + '</div>';
}
function pszSpark(j) {
  const hours = j.hours || [], top = Math.max.apply(null, hours.concat([1]));
  return '<div class="psz-spark" aria-hidden="true">' + hours.map((v, i) => '<i style="height:' + Math.max(0, Math.round(26 * v / top)) + 'px" title="' +
    (v ? _escapeHtml(String(v.toLocaleString())) + ' tokens' : 'no prompt') + ', ' + (hours.length - 1 - i) + ' h ago"></i>').join('') + '</div>';
}
function pszDraw() {
  const picker = document.getElementById('pszRangePicker'), body = document.getElementById('pszBody');
  if (!picker || !body) return;
  picker.innerHTML = PSZ_RANGES.map(r => '<span class="range-opt' + (r[0] === pszRange ? ' active' : '') + '" onclick="pszPickRange(\'' + r[0] + '\')">' + _escapeHtml(r[1]) + '</span>').join('');
  const jobs = (pszData && pszData.jobs) || [];
  if (!jobs.length) { body.innerHTML = '<div class="empty">Play for a while, then come back here.</div>'; return; }
  let h = '<table class="role-table psz-table"><thead><tr><th>Job</th><th>Answered by</th><th>Typical</th><th>Biggest</th><th>Compared to context</th><th>What it means</th></tr></thead><tbody>';
  for (const j of jobs) {
    h += '<tr class="psz-row' + (j.job === pszSel ? ' psz-on' : '') + '" data-job="' + _escapeHtml(j.job) + '" onclick="pszPick(this.dataset.job)"><td class="psz-job">' + _escapeHtml(j.label) + '</td>' +
      '<td>' + _escapeHtml(modelDisplayName(j.model)) + '<div class="psz-small">' + _escapeHtml(j.where) + ' · ' + j.requests + (j.requests === 1 ? ' request' : ' requests') + '</div></td>' +
      '<td class="n">' + _escapeHtml(j.typicalText) + '</td><td class="n">' + _escapeHtml(j.biggestText) + '</td><td>' + pszStrip(j) + '</td><td>' + pszMeans(j) + '</td></tr>';
  }
  h += '</tbody></table>';
  const sel = jobs.find(j => j.job === pszSel) || jobs[0];
  h += '<p class="psz-small" style="margin:12px 0 0">Biggest prompt per hour over the last 24 hours:' + _escapeHtml(sel.label) + '</p>' + pszSpark(sel);
  body.innerHTML = h;
}
// ---- the warning line on the Local models tab: only when something is wrong ----
async function pszLoadToday() {
  let data = null;
  try { data = await (await fetch('/config/prompt-size?range=today')).json(); } catch (e) { return; }
  const jobs = (data && data.jobs) || [];
  const sig = JSON.stringify(jobs.map(j => [j.provider, j.model, j.biggest, j.context]));
  if (sig === JSON.stringify(pszToday.map(j => [j.provider, j.model, j.biggest, j.context]))) return;
  pszToday = jobs;
  if (typeof lmRender === 'function' && typeof lmState !== 'undefined' && lmState) lmRender();
}
function pszGo() { showMainTab('speeds'); showSpeedsView('size'); }
function pszLocalWarn(modelId) {
  const bad = pszToday.filter(j => j.provider === 'Local' && j.model === modelId && j.verdict && (j.verdict.level === 'warn' || j.verdict.level === 'bad'));
  if (!bad.length) return '';
  const j = bad.reduce((a, b) => (b.biggest > a.biggest ? b : a));
  return '<div class="psz-small psz-warn">⚠️ SkyrimNet\'s biggest prompt today was ' + _escapeHtml(j.biggestText) + ', which is ' + (j.verdict.level === 'bad' ? 'larger than ' : 'close to ') + _escapeHtml(j.contextText) + '. ' +
    '<a href="#" style="color:var(--accent); text-decoration:underline" onclick="pszGo(); return false">See Prompt size</a></div>';
}
setTimeout(pszInit, 0);
"""
