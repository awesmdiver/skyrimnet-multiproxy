"""The Overview tab (2026-10-05), built to design/overview-page-mockup.html as approved: the first thing you see, one look at every machine, model and service.

Everything on the page comes from `GET /stats/overview` (overview.py builds it from what MultiProxy really measures; a figure that cannot be measured is simply not
in it, and this file draws only what is there). The page is five short parts: a row of number tiles, Machines, Models and services, Needs a look (hidden when nothing is
wrong). Above them all sits the Setup box, only while setup is unfinished. Status is a dot and one or two words, nothing else: no icons, pills or coloured boxes.

Live, quietly: the numbers refresh every 5 s while this tab is the one on screen. A refresh that fails keeps the last numbers and shows nothing new; a refresh is skipped
while text is selected in the page or a control in it has focus. Rows keep their width (tabular numbers) so nothing jumps. The strings are plain on purpose.
Everything that came from the server goes through esc() (defined by the Local models card's script, on the same page).
"""

import jump_ui

TAB_HTML = """<div class="tab active" id="tabOverview" onclick="showMainTab('overview')">Overview</div>"""

PANE_HTML = """
  <div id="paneOverview">
    """ + jump_ui.row("ovJump") + """
    <div id="ovBody"><div class="lm-muted">Reading your setup…</div></div>
  </div>
"""

CSS = r"""
  /* Overview (2026-10-05) -- ov-* classes */
  .ov-tiles { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 6px }
  .ov-tile { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:7px 12px; min-width:96px }
  .ov-tile .n { font-size:1.1rem; font-weight:600; line-height:1.2; font-variant-numeric:tabular-nums }
  .ov-tile .l { color:var(--muted); font-size:0.78rem }
  .ov-tiles.q { margin:0 0 6px }
  .ov-tile .n.ov-n-warn { color:var(--warn-text) }          /* (not `warn` / `bad`: the page's own .warn and .bad fill a box with a colour) */
  .ov-tile .n.ov-n-bad { color:var(--err-text) }
  .ov-tiles.q .ov-tile .l { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .ov-sub { color:var(--muted); font-size:0.82rem; font-weight:600; margin:14px 2px 6px }
  .ov-h { font-size:0.82rem; margin:26px 0 10px; color:var(--muted); font-weight:600; letter-spacing:0.04em; text-transform:uppercase }
  .ov-table { width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:0.88rem }
  .ov-table th { text-align:left; color:var(--muted); font-weight:500; font-size:0.76rem; padding:9px 14px; border-bottom:1px solid var(--line) }
  .ov-table td { padding:11px 14px; border-bottom:1px solid var(--line) }
  .ov-table tr:last-child td { border-bottom:0 }
  .ov-table .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }
  .ov-table .mid { text-align:center; font-variant-numeric:tabular-nums; white-space:nowrap }
  .ov-table .soft { color:var(--muted) }
  .ov-machead .ov-h { margin:26px 0 10px }
  .ov-table tr.ov-uc td { padding:0 14px 10px }          /* the update line sits under its device, its button at the table's right edge */
  .ov-table tr.ov-uc .uc-line { padding:0 }
  .ov-table tr.ov-go { cursor:pointer }
  .ov-table tr.ov-go:hover td { background:color-mix(in srgb, var(--text) 5%, var(--panel)) }
  .ov-table tr.ov-group td { background:color-mix(in srgb, var(--text) 4%, var(--panel)); color:var(--soft); font-size:0.76rem; letter-spacing:0.04em; text-transform:uppercase; padding:7px 14px }
  .ov-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px; background:var(--ok) }
  .ov-dot.warn { background:var(--warn); animation:ov-issue 2.4s ease-in-out infinite }
  .ov-dot.bad { background:var(--err); animation:ov-issue 2.4s ease-in-out infinite }
  .ov-dot.off { background:var(--muted) }
  @keyframes ov-issue { 0%,100% { opacity:1 } 50% { opacity:0.45 } }
  @media (prefers-reduced-motion:reduce) { .ov-dot.warn, .ov-dot.bad { animation:none } }
  .ov-idle { margin:10px 0 0 }
  .ov-idle summary { cursor:pointer; color:var(--muted); font-size:0.85rem; padding:6px 2px; list-style:none }
  .ov-idle summary::-webkit-details-marker { display:none }
  .ov-idle summary::before { content:''; display:inline-block; width:6px; height:6px; margin:0 9px 2px 3px; border-right:2px solid var(--muted); border-bottom:2px solid var(--muted); transform:rotate(-45deg); transition:transform 0.15s }
  .ov-idle[open] summary::before { transform:rotate(45deg) }
  .ov-idle .ov-table { margin-top:6px }
  .ov-table td.ov-act { text-align:right; white-space:nowrap }
  .ov-table td.ov-act button { font-size:0.78rem; padding:3px 12px }
  .ov-pass { color:var(--ok-text); font-size:0.78rem; margin-top:3px }
  .ov-fail { color:var(--err-text); font-size:0.78rem; margin-top:3px }
  .ov-table td.ov-st { white-space:nowrap }
  /* Models and services: fixed columns whose widths never depend on what is in them; only Name and Result take the rest. */
  .ov-table.ov-fixed { table-layout:fixed; min-width:60rem }
  .ov-fixed col.c-does { width:10rem } .ov-fixed col.c-where { width:11rem } .ov-fixed col.c-st { width:9rem } .ov-fixed col.c-speed { width:7.5rem }
  .ov-fixed col.c-dec { width:15rem } .ov-fixed col.c-req { width:7.5rem } .ov-fixed col.c-avg { width:7.5rem } .ov-fixed col.c-res { width:14rem } .ov-fixed col.c-act { width:15rem }
  .ov-fixed th { height:3rem; vertical-align:bottom; line-height:1.25; white-space:normal; overflow:hidden }
  .ov-fixed td.ov-res, .ov-fixed th.ov-res { padding-left:1rem }
  .ov-fixed td { overflow:hidden; text-overflow:ellipsis }
  .ov-fixed td.ov-res { white-space:nowrap }
  .ov-fixed td.ov-res .ov-msg { display:block; max-inline-size:none; margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .ov-table .ov-msg { display:inline-block; vertical-align:bottom; max-inline-size:22rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin:0 0 0 10px }
  .ov-step { display:block; margin:0 0 2px } .ov-step .n { color:var(--muted); font-size:0.78rem; margin-right:6px } .ov-step .soft { color:var(--muted) }
  .ov-jobs td { vertical-align:top }
  .ov-warnline { margin:12px 2px 0; font-size:0.88rem }
  .ov-dec { display:flex; justify-content:space-between; gap:10px } .ov-dec span:first-child { color:var(--muted) }
  .ov-legend { color:var(--muted); font-size:0.78rem; margin:8px 2px 0; display:flex; gap:16px; flex-wrap:wrap }
  .ov-legend .ov-dot { margin-right:5px }
  .ov-look { margin:4px 2px 0; font-size:0.95rem }
  .ov-look > div { padding:5px 0; cursor:pointer }
  .ov-look .soft { color:var(--muted) }
  .ov-setup { margin:0 0 14px; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 18px; display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap }
  .ov-setup .soft { color:var(--muted) }
  .ov-check { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin:0 0 14px; font-size:0.9rem }
  .ov-check + .ov-table { margin:-4px 0 14px }
  .ov-steps { margin:2px 0 4px 1.3rem; padding:0 } .ov-steps li { margin:3px 0 }
  .ov-steps code { font-size:0.85em }
  .ov-top { display:flex; align-items:baseline; flex-wrap:wrap; gap:8px 16px; min-height:1.5em; margin:0 0 14px; font-size:0.9rem }
  .ov-top .ov-sn { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:pointer }
  .ov-top .ov-ck { flex:0 0 auto }
  .ov-top .ov-sp { flex:0 0 auto; margin-left:auto; white-space:nowrap }
  @media (max-width:760px) { .ov-table { display:block; overflow-x:auto } }
"""

JS = r"""// ---------------- Overview ----------------
if (typeof jumpRegister === 'function') jumpRegister('ovJump', 'paneOverview', () => jumpIds(['ovMachines', 'ovModels', 'ovJobs', 'ovLook']), {});
let ovTimer = null, ovBusy = false, ovJumps = [], ovHave = false;
let ovFailState = {};
let ovTesting = false, ovTestMsg = {};                                              // Test: one at a time; the result shows in its row for a few seconds
let ovPending = {}, ovFail = {}, ovActs = [], ovLast = null;     // Start / Shut down: what is in progress, what failed
const OV_SECONDS = 5;

function ovDot(level) { return '<span class="ov-dot ' + esc(level) + '"></span>'; }
function ovCell(v, cls) { return '<td' + (cls ? ' class="' + cls + '"' : '') + (v ? ' title="' + esc(v) + '"' : '') + '>' + (v ? esc(v) : '<span class="soft">-</span>') + '</td>'; }   // a cell cut with an ellipsis shows its full text on hover
function ovJump(tab) { ovJumps.push(tab || { to: 'local' }); return ' class="ov-go" onclick="ovGo(' + (ovJumps.length - 1) + ')"'; }

function ovMachines(d) {
  const c = d.mcols || {};
  const upN = typeof ucBehindCount === 'function' ? ucBehindCount() : 0, cols = 2 + (c.gpu ? 1 : 0) + (c.memory ? 1 : 0) + (c.runs ? 1 : 0);
  let h = '<div class="uc-head ov-machead"><h2 class="ov-h" id="ovMachines">Machines</h2>' + (upN >= 2 ? '<button class="primary" onclick="ucAll()">Update all (' + upN + ')</button>' : '') + '</div><table class="ov-table"><tr><th>Machine</th><th>Status</th>' + (c.gpu ? '<th>Graphics card</th>' : '') +
    (c.memory ? '<th class="num">Memory in use</th>' : '') + (c.runs ? '<th>Running here</th>' : '') + '</tr>';
  for (const m of d.machines || []) {
    h += '<tr' + ovJump(m.tab) + '><td><b>' + esc(m.name) + '</b>' + (m.sub ? ' <span class="soft">(' + esc(m.sub) + ')</span>' : '') + '</td><td>' + ovDot(m.level) + esc(m.word) + '</td>' +
      (c.gpu ? ovCell(m.gpu, 'soft') : '') + (c.memory ? ovCell(m.memory, 'num') : '') + (c.runs ? ovCell(m.runs, 'soft') : '') + '</tr>';
    const uc = m.id && typeof ucOverviewLine === 'function' ? ucOverviewLine(m.id, m.name) : '';          // a Courier with an update waiting: the same line and button as the Other devices tab
    if (uc) h += '<tr class="ov-uc"><td colspan="' + cols + '">' + uc + '</td></tr>';
  }
  return h + '</table>';
}
// A row while its Start / Shut down is in progress shows the in-progress word (and no button) until the server's own state catches up (or two minutes pass).
function ovShape(r) {
  const p = ovPending[r.key];
  if (p) {
    const done = !p.hold && (p.want === 'run' ? r.state === 'running' : (r.state === 'stopped' || r.state === 'offline'));
    if (done || Date.now() > p.until) delete ovPending[r.key];
    else return Object.assign({}, r, { level: 'warn', word: p.word, action: null });
  }
  if (ovFail[r.key] && ovFailState[r.key] !== r.state) { delete ovFail[r.key]; delete ovFailState[r.key]; }       // a failure line stays until the service changes state or the next click
  return r;
}
function ovActCell(r) {
  if (!r.action && !r.test) return '';
  ovActs.push(r);
  const i = ovActs.length - 1;
  let h = '';
  if (r.test) h += '<button class="ghost"' + (ovTesting ? ' disabled' : '') + ' onclick="event.stopPropagation(); ovTest(' + i + ')">Test</button> ';
  if (r.action && r.action.restart) h += '<button class="ghost" onclick="event.stopPropagation(); ovRestart(' + i + ')">' + esc(r.action.restart.label) + '</button> ';
  if (r.action) h += '<button' + (r.action.kind === 'start' ? ' class="primary"' : '') + ' onclick="event.stopPropagation(); ' + (r.action.kind === 'start' ? 'ovStart(' : 'ovStop(') + i + ')">' + esc(r.action.label) + '</button>';
  return h;
}
// A result or failure sits in the row's Result cell (one line, cut with an ellipsis, full text on hover), so no row ever grows and no column moves.
function ovMsg(m) { return m ? '<span class="ov-msg ' + (m.ok ? 'ov-pass' : 'ov-fail') + '" title="' + esc(m.text) + '">' + esc(m.text) + '</span>' : ''; }
// What the Result cell says: a failure or test result when there is one, else how many requests failed in the last hour (only when some did).
function ovStatusNote(r) {
  const m = ovFail[r.key] ? { ok: false, text: ovFail[r.key] } : ovTestMsg[r.key];
  if (m) return ovMsg(m);
  return r.failed ? '<span class="ov-msg soft" title="' + esc(r.failed + ' failed in the last hour') + '">' + esc(r.failed + ' failed') + '</span>' : '';
}
// The Details column: short label-and-number lines for the Quick decisions row (Total decisions, Answered, Passed to another AI, Certainty) and the Voice row (Waiting, Speaking), label muted, number normal; every other row, and a row with nothing yet, shows a dash.
function ovDetailCell(x) {
  if (!x || !x.length) return '<td><span class="soft">-</span></td>';
  return '<td>' + x.map(l => '<div class="ov-dec" title="' + esc(l[0] + ': ' + l[1]) + '"><span>' + esc(l[0]) + '</span><span>' + esc(l[1]) + '</span></div>').join('') + '</td>';
}
function ovRow(r0, c) {
  if (ovHiddenRow(r0)) return '';
  const r = ovShape(r0);
  return '<tr' + ovJump(r.tab) + '><td title="' + esc(r.name + (r.line ? ' - ' + r.line : '')) + '"><b>' + esc(r.name) + '</b>' + (r.line ? '<div class="ov-fail soft">' + esc(r.line) + '</div>' : '') + '</td>' + (c.does ? ovCell(r.does, 'soft') : '') + ovCell(r.where, 'soft') +
    '<td class="ov-st" title="' + esc(r.word) + '">' + ovDot(r.level) + esc(r.word) + '</td>' + (c.speed ? ovCell(r.speed, 'num') : '') + (c.details ? ovDetailCell(r.details) : '') + (c.requests ? ovCell(r.requests, 'mid') : '') + (c.avg ? ovCell(r.avg, 'mid') : '') +
    (c.result ? '<td class="ov-res">' + ovStatusNote(r) + '</td>' : '') + (c.actions ? '<td class="ov-act">' + ovActCell(r) + '</td>' : '') + '</tr>';
}
// Online providers that are ready but not used yet: one closed section (its state is remembered in this browser; a failed read just shows it closed).
// A program the player hid on Other devices (one remembered choice per device, shared with that page) is not listed here while it is not running; "Show hidden programs" there brings it back here too.
function ovHiddenRow(r) {
  const m = /^engine:([^\/]+)\/(.+)$/.exec(r.key || '');
  if (!m || r.state === 'running' || typeof opHiddenGet !== 'function') return false;
  return !!opHiddenGet()[m[1] + '|' + m[2]] && !(typeof opShowHidden !== 'undefined' && opShowHidden[m[1]]);
}
function ovOpenGet(k) { try { return localStorage.getItem(k) === '1'; } catch (e) { return false; } }
function ovOpenSet(k, el) { try { localStorage.setItem(k, el.open ? '1' : '0'); } catch (e) {} }
function ovIdleOpen() { return ovOpenGet('ovIdleOpen'); }
function ovIdleToggle(el) { ovOpenSet('ovIdleOpen', el); }
function ovMoreToggle(el) { ovOpenSet('ovMoreOpen', el); }
// The header and the column widths of the Models and services table (and the closed section's copy of it): the same widths in both, none of them from the content.
function ovCols(c) {
  return '<colgroup><col>' + (c.does ? '<col class="c-does">' : '') + '<col class="c-where"><col class="c-st">' + (c.speed ? '<col class="c-speed">' : '') + (c.details ? '<col class="c-dec">' : '') + (c.requests ? '<col class="c-req">' : '') +
    (c.avg ? '<col class="c-avg">' : '') + (c.result ? '<col class="c-res">' : '') + (c.actions ? '<col class="c-act">' : '') + '</colgroup>';
}
function ovHead(c) {
  return '<tr><th>Name</th>' + (c.does ? '<th>What it does</th>' : '') + '<th>Where</th><th>Status</th>' + (c.speed ? '<th class="num">Speed</th>' : '') + (c.details ? '<th>Details <br>(last hour)</th>' : '') + (c.requests ? '<th class="mid">Requests <br>(last hour)</th>' : '') +
    (c.avg ? '<th class="mid">Average time <br>(last hour)</th>' : '') + (c.result ? '<th class="ov-res">Result</th>' : '') + (c.actions ? '<th></th>' : '') + '</tr>';
}
function ovIdle(d, c) {
  const g = d.idle;
  if (!g || !(g.rows || []).length) return '';
  return '<details class="ov-idle"' + (ovIdleOpen() ? ' open' : '') + ' ontoggle="ovIdleToggle(this)"><summary>' + esc(g.title) + '</summary><table class="ov-table ov-fixed">' + ovCols(c) + ovHead(c) +
    g.rows.map(r => ovRow(r, c)).join('') + '</table></details>';
}
function ovModels(d) {
  const c = d.cols || {}, groups = d.groups || [];
  if (!groups.length && !(d.idle && (d.idle.rows || []).length)) return '';
  const span = 4 + (c.does ? 1 : 0) + (c.speed ? 1 : 0) + (c.details ? 1 : 0) + (c.requests ? 1 : 0) + (c.avg ? 1 : 0) + (c.result ? 1 : 0) + (c.actions ? 1 : 0);
  let h = '<h2 class="ov-h" id="ovModels">Models and services</h2><table class="ov-table ov-fixed">' + ovCols(c) + ovHead(c);
  for (const g of groups) {
    h += '<tr class="ov-group"><td colspan="' + (span - 1) + '">' + esc(g.title) + '</td></tr>';
    for (const r of g.rows) h += ovRow(r, c);
  }
  return h + '</table>' + ovIdle(d, c) + '<div class="ov-legend"><span>' + ovDot('ok') + 'Running, in use or ready</span><span>' + ovDot('warn') + 'Slow or needs a look</span><span>' + ovDot('bad') + 'Not working</span><span>' + ovDot('off') + 'Off or not set up</span></div>';
}
// Where each job runs: every job in the Routing tab's order, its steps in route order (1 first choice, 2 steps in if 1 fails or is busy), each with its model's own dot and device.
function ovJobs(d) {
  const card = d.jobCard;
  if (!card || !(card.rows || []).length) return '';
  let h = '<h2 class="ov-h" id="ovJobs">' + esc(card.title) + '</h2><table class="ov-table ov-jobs"><tr><th>Job</th><th>Runs on, in this order</th><th class="mid">Requests <br>(last hour)</th><th class="mid">Average time <br>(last hour)</th></tr>';
  for (const r of card.rows) {
    h += '<tr' + ovJump(r.tab) + '><td><b>' + esc(r.job) + '</b></td><td>' + (r.steps || []).map((s, i) => '<span class="ov-step"><span class="n">' + esc(s.n) + '</span>' + ovDot(s.level) +
      (i === 0 ? '<b>' + esc(s.name) + '</b>' : esc(s.name)) + ' <span class="soft">' + esc(s.where) + '</span></span>').join('') + '</td>' + ovCell(r.requests, 'mid') + ovCell(r.avg, 'mid') + '</tr>';
  }
  h += '</table>';
  for (const line of d.cardLines || []) h += '<div class="lm-warn ov-warnline">\u26a0\ufe0f ' + esc(line) + '</div>';
  return h;
}
// The quick stats of the last hour: a row of tiles (each ends with a link to Speeds), and a closed section with the split by kind of job and the tokens used.
function ovSpeedsLink() { ovJumps.push({ to: 'speeds' }); return '<a class="lm-link" onclick="ovGo(' + (ovJumps.length - 1) + ')">See details in Speeds.</a>'; }
function ovQuick(d, pre) {
  const q = d.quick;
  if (!q || !(q.tiles || []).length) return pre ? '<div class="ov-tiles">' + pre + '</div>' : '';
  let h = '<div class="ov-tiles q">' + (pre || '') + q.tiles.map(t => '<div class="ov-tile" title="' + esc(t.hover || '') + '"><div class="n' + (t.level && t.level !== 'ok' ? ' ov-n-' + esc(t.level) : '') + '">' + esc(t.value) +
    '</div><div class="l" title="' + esc(t.label) + '">' + esc(t.label) + '</div></div>').join('') + '</div>';
  const kinds = q.kinds || [], tokens = q.tokens || [];
  if (!kinds.some(k => k.requests) && !tokens.length) return h;
  let body = '';
  if (kinds.some(k => k.requests)) {
    body += '<div class="ov-sub">By kind of job</div><table class="ov-table"><tr><th>Kind of job</th><th class="mid">Requests (last hour)</th><th class="mid">Average time (last hour)</th></tr>' +
      kinds.map(k => '<tr><td><b>' + esc(k.kind) + '</b></td>' + ovCell(k.requests, 'mid') + ovCell(k.avg, 'mid') + '</tr>').join('') + '</table>';
  }
  if (tokens.length) {
    body += '<div class="ov-sub">Tokens used</div><table class="ov-table"><tr><th>AI</th><th class="mid">In (last hour)</th><th class="mid">Out (last hour)</th></tr>' +
      tokens.map(t => '<tr><td><b>' + esc(t.name) + '</b></td>' + ovCell(t.in, 'mid') + ovCell(t.out, 'mid') + '</tr>').join('') + '</table>';
  }
  return h + '<details class="ov-idle"' + (ovOpenGet('ovMoreOpen') ? ' open' : '') + ' ontoggle="ovMoreToggle(this)"><summary>Last hour in more detail</summary>' + body + '</details>';
}
function ovAttention(d) {
  const a = d.attention || [];
  if (!a.length) return '';
  return '<h2 class="ov-h" id="ovLook">Needs a look</h2><div class="ov-look">' + a.map(x => {
    ovJumps.push(x.tab || { to: 'local' });
    const go = ovJumps.length - 1;
    let btn = '';
    if (x.action) { ovActs.push({ key: x.key, action: x.action, state: 'stopped' }); btn = ' <button class="primary" onclick="event.stopPropagation(); ovStart(' + (ovActs.length - 1) + ')">' + esc(x.action.label) + '</button>'; }       // the row's own Start
    return '<div onclick="ovGo(' + go + ')">' + ovDot(x.level) + '<b>' + esc(x.name) + '</b>' + (x.line ? ' <span class="soft">' + esc(x.line) + '</span>' : '') + (x.help ? ' <span class="hp-how"><a onclick="event.stopPropagation(); openHelp(\'' + x.help + '\')">Help</a></span>' : '') + btn + '</div>';
  }).join('') + '</div>';
}
// One small status row: is SkyrimNet talking to MultiProxy? (a request from a program that is not this dashboard has arrived since MultiProxy started). Left out when not measured.
// The one `See details in Speeds.` link sits at the right end of this line (only when there are quick-stats tiles for it to explain); the line is as tall with or without it.
function ovSkyrimNet(d) {
  const s = d.skyrimnet, link = d.quick && (d.quick.tiles || []).length;
  let left = '';
  if (s) {
    ovJumps.push({ to: 'settings', section: 'skyrimnet' });
    const sw = s.line || (s.connected ? 'Connected' : 'Waiting for SkyrimNet');
    left = '<div class="ov-sn" title="' + esc('SkyrimNet ' + sw) + '" onclick="ovGo(' + (ovJumps.length - 1) + ')">' + ovDot(s.connected ? 'ok' : 'off') + '<b>SkyrimNet</b> ' + esc(sw) + '</div>';
  }
  return '<div class="ov-top">' + (left || '<div class="ov-sn"></div>') + ovCheckButton() + (link ? '<div class="ov-sp">' + ovSpeedsLink() + '</div>' : '') + '</div>';
}
// Check everything: one button; it shows only what is wrong (the checks and their words come from the server, fix_it.py). The result stays across the page's quiet refreshes.
const OV_CHECK_WORDS = { button: 'Check everything', busy: 'Checking…', how: 'Show how', hide: 'Hide', updating: 'Updating…', failed: 'Something went wrong.' };
let ovCheck = { busy: false, result: null, open: {}, acted: {}, err: {} };
function ovSteps(list) { return '<ol class="ov-steps">' + list.map(s => '<li>' + esc(s).replace(/`([^`]*)`/g, '<code>$1</code>') + '</li>').join('') + '</ol>'; }
// The button sits on the SkyrimNet status line (ovSkyrimNet), just before the Speeds link; what the check found is drawn under that line by ovCheckBox.
function ovCheckButton() {
  const c = ovCheck;
  return '<button class="ghost ov-ck"' + (c.busy ? ' disabled' : '') + ' onclick="ovCheckRun()">' + esc(c.busy ? OV_CHECK_WORDS.busy : OV_CHECK_WORDS.button) + '</button>';
}
function ovCheckBox() {
  const c = ovCheck, res = c.result;
  let h = '';
  if (res && !c.busy && !(res.problems || []).length && res.allWell) h += '<div class="ov-check"><span>' + ovDot('ok') + esc(res.allWell) + '</span></div>';
  const items = res && !c.busy ? (res.problems || []) : [];
  if (!items.length) return h;
  h += '<table class="ov-table">';
  items.forEach((p, i) => {
    const act = p.action ? (c.acted[p.key] ? '<button disabled>' + esc(OV_CHECK_WORDS.updating) + '</button>' : '<button class="primary" onclick="ovCheckAct(' + i + ')">' + esc(p.action.label) + '</button>') : '';
    const how = p.steps ? '<button class="ghost" onclick="ovCheckHow(' + i + ')">' + esc(c.open[p.key] ? OV_CHECK_WORDS.hide : OV_CHECK_WORDS.how) + '</button> ' : '';
    h += '<tr><td>' + ovDot('warn') + '<b>' + esc(p.title) + '</b>' + (p.detail ? '<div class="soft">' + esc(p.detail) + '</div>' : '') + (c.err[p.key] ? '<div class="ov-fail">' + esc(c.err[p.key]) + '</div>' : '') +
      (p.steps && c.open[p.key] ? ovSteps(p.steps) : '') + '</td><td class="ov-act">' + how + act + '</td></tr>';
  });
  return h + '</table>';
}
async function ovCheckRun() {
  if (ovCheck.busy) return;
  ovCheck = { busy: true, result: null, open: ovCheck.open, acted: {}, err: {} };
  ovRepaint();
  try { const r = await fetch('/config/check-everything', { method: 'POST' }); ovCheck.result = r.ok ? await r.json() : { problems: [], allWell: null }; }
  catch (e) { ovCheck.result = { problems: [], allWell: null }; }          // a check that cannot run says nothing
  ovCheck.busy = false;
  ovRepaint();
}
function ovCheckHow(i) { const p = ((ovCheck.result || {}).problems || [])[i]; if (!p) return; ovCheck.open[p.key] = !ovCheck.open[p.key]; ovRepaint(); }
async function ovCheckAct(i) {
  const p = ((ovCheck.result || {}).problems || [])[i];
  if (!p || !p.action || ovCheck.acted[p.key]) return;
  ovCheck.acted[p.key] = true; delete ovCheck.err[p.key];
  ovRepaint();
  try { await ovPost({ url: p.action.url, body: p.action.body }); }
  catch (e) { delete ovCheck.acted[p.key]; ovCheck.err[p.key] = e.message || OV_CHECK_WORDS.failed; }
  ovRepaint();
}
function ovSetup(d) {
  const s = d.setup || {};
  if (!s.show) return '';
  return '<div class="ov-setup"><div><b>' + esc(s.title) + '</b> <span class="soft">' + esc(s.line) + '</span></div><button onclick="ovContinueSetup()">Continue setup</button></div>';
}
// The whole page as one string, from the server's answer (pure: the tests run it with made-up answers).
function ovHtml(d) {
  ovJumps = [];
  ovActs = [];
  let h = '';
  // The first tiles and the quick-stats tiles share one row (it wraps only when the window is too narrow).
  if ((d.tiles || []).length) h += d.tiles.map(t => '<div class="ov-tile"><div class="n">' + esc(t.value) + '</div><div class="l">' + esc(t.label) + '</div></div>').join('');
  return ovSetup(d) + (typeof ovSetMeUp === 'function' ? ovSetMeUp(d) : '') + ovSkyrimNet(d) + ovCheckBox() + ovQuick(d, h) + ovMachines(d) + ovModels(d) + ovJobs(d) + ovAttention(d);
}

function ovGo(i) {
  const t = ovJumps[i];
  if (!t) return;
  if (t.section && typeof openSettings === 'function') { openSettings(t.section); return; }       // a section of the Settings page
  showMainTab(t.to);
  if (t.job && typeof rtOpenKey !== 'undefined') { rtOpenKey = t.job; try { rtRender(); } catch (e) {} }          // that job opens on the Routing tab
  if (t.provider && typeof selectProvider === 'function') { try { selectProvider(t.provider); } catch (e) {} }
  if (t.row !== undefined && typeof dcGoRow === 'function') dcGoRow(t.row);              // the Decisions tab opens with that device's row in view
  if (t.scroll) setTimeout(() => { const el = document.getElementById(t.scroll); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' }); }, 60);
}
function ovContinueSetup() { if (typeof openGetStarted === 'function') openGetStarted(); }

function ovBusyWithPage() {
  const box = document.getElementById('ovBody');
  if (!box) return true;
  try { const sel = window.getSelection(); if (sel && !sel.isCollapsed && box.contains(sel.anchorNode)) return true; } catch (e) {}   // text being selected to copy stays put
  const a = document.activeElement;
  return !!(a && ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(a.tagName) && box.contains(a));                                                                          // so does a control being used
}
async function ovLoad(force) {
  if (ovBusy) return;
  ovBusy = true;
  try {
    if (typeof ucLoad === 'function') ucLoad();          // (which Couriers have an update waiting; it repaints this page itself when that changes)
    const r = await fetch('/stats/overview');
    if (!r.ok) return;                                    // keep the last numbers; say nothing new
    const d = await r.json();
    ovLast = d;
    if (!force && ovBusyWithPage()) return;
    document.getElementById('ovBody').innerHTML = ovHtml(d);
    ovHave = true;
  } catch (e) {} finally { ovBusy = false; }
}
function ovRepaintSoft() {          // after an update-state change: repaint, unless text in the page is being selected
  try { const sel = window.getSelection(), box = document.getElementById('ovBody'); if (sel && !sel.isCollapsed && box && box.contains(sel.anchorNode)) return; } catch (e) {}
  ovRepaint();
}
function ovRepaint() { const b = document.getElementById('ovBody'); if (b && ovLast) b.innerHTML = ovHtml(ovLast); }
// Start / Shut down: the very call the owning tab makes (the row carries its url and body). Neither Shut down nor Restart asks: Start is right there.
async function ovPost(a) {
  const r = await fetch(a.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(a.body || {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || 'Something went wrong.');
  if (d && d.status === 'failed') throw new Error(d.message || 'Something went wrong.');
  return d;
}
async function ovDo(r, want, word) {
  delete ovFail[r.key];
  ovPending[r.key] = { want, word, until: Date.now() + 120000 };
  ovRepaint();
  try { await ovPost(r.action); }
  catch (e) { delete ovPending[r.key]; ovFail[r.key] = e.message; ovFailState[r.key] = r.state; }
  ovBusy = false;
  await ovLoad(true);
}
// Restart: the owning tab's own stop, then its own start, one after the other (the row carries both calls). 
async function ovRestart(i) {
  const r = ovActs[i];
  if (!r || !r.action || !r.action.restart) return;
  delete ovFail[r.key];
  const p = ovPending[r.key] = { want: 'run', word: 'Restarting\u2026', until: Date.now() + 180000, hold: true };
  ovRepaint();
  try { for (const call of r.action.restart.calls) await ovPost(call); }
  catch (e) { delete ovPending[r.key]; ovFail[r.key] = e.message; ovFailState[r.key] = r.state; }
  p.hold = false;
  ovBusy = false;
  await ovLoad(true);
}
// Test: Quick Test's own check for this one model or provider -- one real tiny chat request, sent the way the Quick Test tab sends it (it never goes through a route and changes no setting).
const OV_TEST_PROMPT = 'Say hello in a few words.';
async function ovTest(i) {
  const r = ovActs[i];
  if (!r || !r.test || ovTesting) return;
  ovTesting = true;
  ovTestMsg[r.key] = { ok: true, text: 'Testing\u2026' };
  ovRepaint();
  const t0 = Date.now();
  let res;
  // One try per model: the chosen one, and (only when it says "does not support chat") the next one once.
  const models = [r.test.model].concat((r.test.next || []).slice(0, 1));
  for (let n = 0; n < models.length; n++) {
    try {
      const resp = await fetch('/v1/chat/completions', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MultiProxy-Quick-Test': '1' },
        body: JSON.stringify({ model: models[n], max_tokens: 16, messages: [{ role: 'user', content: OV_TEST_PROMPT }] }) });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.choices) { res = { ok: true, text: 'Answered in ' + ((Date.now() - t0) / 1000).toFixed(1) + ' s' }; break; }
      const why = (data.error && data.error.message) || 'Something went wrong';
      res = { ok: false, text: 'No answer: ' + why.replace(/[.\s]+$/, '') };
      if (!/does not support chat/i.test(why)) break;
    } catch (e) { res = { ok: false, text: 'No answer: ' + e.message }; break; }
  }
  ovTesting = false;
  ovTestMsg[r.key] = res;
  ovRepaint();
  setTimeout(() => { delete ovTestMsg[r.key]; ovRepaint(); }, 6000);
}
function ovStart(i) { const r = ovActs[i]; if (r) ovDo(r, 'run', 'Starting\u2026'); }
function ovStop(i) { const r = ovActs[i]; if (r) ovDo(r, 'stop', 'Shutting down\u2026'); }
function ovShown() { const p = document.getElementById('paneOverview'); return !!p && p.style.display !== 'none' && !document.hidden; }
function ovTick() { if (ovShown()) ovLoad(); }
function ovOpened() {
  ovLoad(true);                                           // the tab was just switched to: nothing on it is being used, so paint what the server says now
  if (!ovTimer) ovTimer = setInterval(ovTick, OV_SECONDS * 1000);
}
function ovClosed() { if (ovTimer) { clearInterval(ovTimer); ovTimer = null; } }
document.addEventListener('visibilitychange', () => { if (ovShown()) ovLoad(); });
"""
