"""Check it like SkyrimNet (2026-10-25): a third section on the Quick Test tab, as in design/mockup-listening-top-3.html (section 2). Plain strings spliced into
multiproxy.py's page. Everything the server sent goes in with textContent, never as markup.

The check runs only on the button. The page asks the server for one part at a time ("one", "busy", "actions") and fills each line as it lands, so a person sees
progress without a bar the server could not honestly fill. A key or payment problem stops the check and shows the reason.
"""

CSS = r"""
  /* Check it like SkyrimNet (2026-10-25) -- ck-* classes; the steps box, labels and muted lines are the page's own */
  .ck-pick { display:grid; grid-template-columns:1fr 1.4fr; gap:8px; margin-top:10px }
  .ck-pick select { width:100%; font-family:system-ui,sans-serif; font-size:0.85rem }
  .ck-buttons { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px }
  .ck-table { margin-top:14px; font-size:0.9rem }
  .ck-head, .ck-line { display:grid; grid-template-columns:26% 1fr 1.2fr; gap:12px; padding:7px 0; border-bottom:1px solid var(--line) }
  .ck-head { font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted) }
  .ck-line .what { color:var(--soft) }
  .ck-line .num, .ck-head .num { text-align:right; color:var(--soft) }
  .ck-dec .ck-head, .ck-dec .ck-line { grid-template-columns:42% 1fr 5rem 5rem }
  .ck-own .ck-head, .ck-own .ck-line { grid-template-columns:1.4fr repeat(5, 1fr) }
  .ck-own .num { text-align:right }
  .ck-state { display:flex; align-items:center; gap:8px; overflow-wrap:anywhere }
  .ck-state .dot { flex:none }
  .ck-warn { color:var(--warn) }
  .ck-sub { margin:18px 0 0; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted) }
  @media (max-width:700px) { .ck-head { display:none } .ck-line, .ck-dec .ck-line, .ck-own .ck-line { grid-template-columns:1fr } .ck-line .num { text-align:left } }
"""

PANE_HTML = """
  <div class="card" id="ckCard">
    <div class="su-box" data-sh-static="quick-test-check"><h4>Start here</h4><ol class="su-steps">
      <li>Pick a model.</li>
      <li>Press Check it. It takes about a minute and uses the model the way the game does.</li>
      <li>Read the three lines. A green dot means the model can handle it.</li>
    </ol></div>
    <div class="ck-pick">
      <select id="ckProvider" onchange="ckFillModels()"></select>
      <select id="ckModel"></select>
    </div>
    <div class="purpose-hint" id="ckCost" style="margin-top:10px"></div>
    <div class="ck-buttons">
      <button onclick="ckRun()" id="ckBtn">Check it</button>
      <button onclick="ckRunDecision()" id="ckDecBtn" hidden>Check my decision model</button>
    </div>
    <div class="ck-table" id="ckTable" hidden>
      <div class="ck-head"><span>What was checked</span><span>Result</span><span class="num">Numbers</span></div>
      <div id="ckLines"></div>
      <div class="ck-warn" id="ckStopped" style="margin-top:10px" hidden></div>
      <div class="purpose-hint" style="margin-top:10px">The sample prompts are built in. They are shaped like the game's, but they are not your game.</div>
    </div>
    <div id="ckDecision" hidden>
      <h4 class="ck-sub">Check my decision model</h4>
      <div class="ck-table ck-dec">
        <div class="ck-head"><span>Sample question</span><span>Answer</span><span class="num">Confidence</span><span class="num">Time</span></div>
        <div id="ckDecLines"></div>
      </div>
      <div class="ck-warn" id="ckDecError" style="margin-top:10px" hidden></div>
      <div class="purpose-hint" style="margin-top:10px">The expected answers are built in. A warning appears if the model gives a different answer.</div>
    </div>
    <div id="ckOwn" hidden>
      <h4 class="ck-sub">From your own play (last 7 days)</h4>
      <div class="ck-table ck-own">
        <div class="ck-head"><span>Job</span><span class="num">Replies</span><span class="num">Fine</span><span class="num">Repaired</span><span class="num">Asked again</span><span class="num">Longest wait in line</span></div>
        <div id="ckOwnLines"></div>
      </div>
    </div>
  </div>
"""

JS = r"""// ---------------- Check it like SkyrimNet ----------------
let ckData = null, ckBusy = false, ckRunId = 0;
const CK_PARTS = [['one', 'One at a time'], ['busy', 'A busy room (6 at once)'], ['actions', 'Actions (10 sample commands)']];

function ckEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function ckSecs(s) { return s >= 10 ? Math.round(s) + ' s' : s.toFixed(1) + ' s'; }
function ckMs(s) { return s < 1 ? Math.round(s * 1000) + ' ms' : s.toFixed(1) + ' s'; }

// The model picker is Quick Test's own list: the providers that can answer now, and the chat models each one offers.
function ckFillProviders() {
  const sel = $('ckProvider'), keep = sel.value;
  const list = PROVIDERS.filter(usable);
  sel.replaceChildren();
  if (!list.length) {
    const o = document.createElement('option'); o.value = ''; o.textContent = 'No providers set up yet. Go to the Providers tab to add one first.'; sel.appendChild(o);
  }
  for (const p of list) { const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; sel.appendChild(o); }
  sel.disabled = !list.length;
  if (list.some(p => p.id === keep)) sel.value = keep;
  ckFillModels();
}

function ckFillModels() {
  const p = byId($('ckProvider').value), sel = $('ckModel'), keep = sel.value;
  sel.replaceChildren();
  if (!p) { sel.disabled = true; $('ckBtn').disabled = true; return; }
  for (const [mid, name] of p.models.filter(([mid]) => !(p.kinds && p.kinds[mid]) || p.kinds[mid].includes('Chat'))) {
    const o = document.createElement('option'); o.value = mid; o.textContent = name; sel.appendChild(o);
  }
  sel.disabled = !sel.options.length;
  if (keep && sel.querySelector('option[value="' + CSS.escape(keep) + '"]')) sel.value = keep;
  $('ckBtn').disabled = ckBusy || !sel.options.length;
}

async function ckOpened() {
  if (ckBusy) return;
  ckFillProviders();
  try { ckData = await (await fetch('/quicktest/check/options')).json(); } catch (e) { return; }
  $('ckCost').textContent = 'A check sends about 20 short requests to your chosen model. For paid models, these count against your account.';
  $('ckDecBtn').hidden = !ckData.decision;
  const lines = $('ckOwnLines'); lines.replaceChildren();
  for (const a of ckData.own || []) {
    const row = ckEl('div', 'ck-line');
    row.append(ckEl('span', 'what', a.job), ckEl('span', 'num', String(a.replies)), ckEl('span', 'num', String(a.fine)), ckEl('span', 'num', String(a.repaired)),
               ckEl('span', 'num', String(a.asked_again)), ckEl('span', 'num', a.longest_wait === null ? '-' : ckSecs(a.longest_wait)));
    lines.appendChild(row);
  }
  $('ckOwn').hidden = !(ckData.own || []).length;
}

// One result line: a dot, then the words. A green line says "Fine"; anything else is the warning line (amber text with the warning sign).
function ckLine(name, state, text, numbers) {
  const row = ckEl('div', 'ck-line');
  const s = ckEl('span', 'ck-state');
  if (state === null) {
    s.append(ckEl('span', 'dot off'), ckEl('span', '', text));
  } else {
    s.append(ckEl('span', 'dot ' + ({green: 'ok', amber: 'warn', red: 'bad'}[state])), ckEl('span', state === 'green' ? '' : 'ck-warn', (state === 'green' ? '' : '⚠️ ') + text));
  }
  row.append(ckEl('span', 'what', name), s, ckEl('span', 'num', numbers || ''));
  return row;
}

function ckPartWords(part, r) {
  if (part === 'one') {
    const nums = (r.answered === r.total ? r.answered + ' answers' : r.answered + ' of ' + r.total + ' answered') + (r.slowest === null ? '' : ', slowest ' + ckSecs(r.slowest));
    return [r.state === 'green' ? 'Fine' : r.state === 'amber' ? 'Some failed to answer' : 'None answered', nums];
  }
  if (part === 'busy') {
    const bits = [];
    if (r.slowest !== null) bits.push('slowest ' + ckSecs(r.slowest));
    if (r.answered < r.total) bits.push(r.answered + ' of ' + r.total + ' answered');
    bits.push(r.waited ? r.waited + ' had to wait' : 'none had to wait');
    const slow = r.slowest !== null && r.slowest > ckData.slow;
    return [r.state === 'green' ? 'Fine' : r.state === 'red' ? 'None answered' : slow ? 'Slow when busy' : 'Some failed to answer', bits.join(', ')];
  }
  return [r.state === 'green' ? 'Fine' : r.usable + ' of ' + r.total + ' were usable',
          r.repaired ? r.repaired + ' needed repair' : (r.state === 'green' ? '' : r.total - r.usable + ' were unusable')];
}

async function ckRun() {
  if (ckBusy || !ckData) return;
  const model = $('ckModel').value;
  if (!model) return;
  const run = ++ckRunId;
  ckBusy = true; $('ckBtn').disabled = true; $('ckDecBtn').disabled = true; $('ckBtn').textContent = 'Checking…';
  const lines = $('ckLines'); lines.replaceChildren();
  $('ckTable').hidden = false; $('ckStopped').hidden = true;
  let stopped = null;
  for (const [part, name] of CK_PARTS) {
    if (stopped) break;
    const pending = ckLine(name, null, 'Checking…', '');
    lines.appendChild(pending);
    let r;
    try {
      const resp = await fetch(location.origin + '/quicktest/check', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-MultiProxy-Quick-Test': '1', 'X-MultiProxy': '1'},
        body: JSON.stringify({model: model, part: part}),
      });
      r = await resp.json();
      if (!resp.ok) r = {error: (r.error && r.error.message) || 'Something went wrong', total: 0, state: null};
    } catch (e) { r = {error: e.message, total: 0, state: null}; }
    if (run !== ckRunId) return;
    if (r.stopped) { stopped = r.stopped; pending.remove(); break; }
    if (!r.total || r.state === null) { pending.remove(); stopped = r.error || 'The model did not answer.'; break; }
    const [words, nums] = ckPartWords(part, r);
    pending.replaceWith(ckLine(name, r.state, words, nums));
    if (r.error) { stopped = r.error; }
  }
  if (stopped) { $('ckStopped').textContent = '⚠️ The check stopped. ' + stopped; $('ckStopped').hidden = false; }
  ckBusy = false; $('ckBtn').disabled = false; $('ckDecBtn').disabled = false; $('ckBtn').textContent = 'Check it';
  ckOpened();
}

async function ckRunDecision() {
  if (ckBusy) return;
  ckBusy = true; $('ckBtn').disabled = true; $('ckDecBtn').disabled = true; $('ckDecBtn').textContent = 'Checking…';
  $('ckDecision').hidden = false; $('ckDecError').hidden = true;
  const lines = $('ckDecLines'); lines.replaceChildren();
  let r;
  try {
    const resp = await fetch(location.origin + '/quicktest/check/decision', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-MultiProxy-Quick-Test': '1', 'X-MultiProxy': '1'}, body: '{}',
    });
    r = await resp.json();
    if (!resp.ok) r = {ok: false, error: (r.error && r.error.message) || 'Something went wrong'};
  } catch (e) { r = {ok: false, error: e.message}; }
  if (r.ok) {
    for (const d of r.rows) {
      const row = ckEl('div', 'ck-line');
      const s = ckEl('span', 'ck-state');
      if (d.answer === null) {
        s.append(ckEl('span', 'dot warn'), ckEl('span', 'ck-warn', '⚠️ No answer'));
      } else if (d.as_expected) {
        s.append(ckEl('span', '', d.answer));
      } else {
        s.append(ckEl('span', 'dot warn'), ckEl('span', 'ck-warn', '⚠️ ' + d.answer));
      }
      row.append(ckEl('span', 'what', d.name), s, ckEl('span', 'num', d.confidence === null ? '-' : Math.round(d.confidence * 100) + '%'), ckEl('span', 'num', ckMs(d.seconds)));
      lines.appendChild(row);
    }
  } else {
    $('ckDecError').textContent = '⚠️ ' + r.error; $('ckDecError').hidden = false;
  }
  ckBusy = false; $('ckBtn').disabled = false; $('ckDecBtn').disabled = false; $('ckDecBtn').textContent = 'Check my decision model';
}
"""
