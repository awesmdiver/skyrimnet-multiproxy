"""Quick Test for Quick decisions (Jev) (2026-10-05): a second card on the Quick Test tab, in the chat test's own style (same labels, Start here box, Send button, green
timing line, red error box). Plain strings spliced into multiproxy.py's page. Everything the server sent goes in with textContent, never as markup."""

CSS = r"""
  /* Quick Test for Quick decisions (2026-10-05) -- dq-* classes; the rest is the chat test's own look */
  #dqScenario, #dqWho { width:100%; font-family:system-ui,sans-serif }
  .dq-state { white-space:pre-wrap; font-size:0.85rem; line-height:1.5; color:var(--soft); background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:10px; margin-top:6px }
  .dq-out { margin-top:16px }
  .dq-out h4 { margin:14px 0 6px; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted) }
  .dq-row { display:flex; justify-content:space-between; gap:12px; font-size:0.9rem; padding:6px 0; border-bottom:1px solid var(--line) }
  .dq-row .name { color:var(--soft); overflow-wrap:anywhere }
  .dq-row .ans { color:var(--text); text-align:right }
  .dq-row .ans .conf { color:var(--muted); margin-left:8px; font-size:0.78rem }
"""

PANE_HTML = """
  <div class="card" id="dqCard">
    <div class="su-box" data-sh-static="quick-test-decisions"><h4>Start here</h4><ol class="su-steps">
      <li>Pick who should answer.</li>
      <li>Keep the sample scenario or pick another.</li>
      <li>Press Send.</li>
      <li>Read the answers below.</li>
    </ol></div>
    <div class="qt-field" style="margin-top:10px">
      <label class="qt-label" for="dqWho">Who answers</label>
      <select id="dqWho" onchange="dqShowCost()"></select>
      <div class="purpose-hint" id="dqWhoHint"></div>
    </div>
    <div class="qt-field">
      <label class="qt-label" for="dqScenario">Scenario</label>
      <select id="dqScenario" onchange="dqShowScenario()"></select>
      <div class="dq-state" id="dqState"></div>
    </div>
    <div class="purpose-hint" id="dqCost" style="margin-top:10px"></div>
    <button style="margin-top:12px" onclick="dqSend()" id="dqBtn">Send</button>
    <div class="dq-out" id="dqOut"></div>
  </div>
"""

JS = r"""// ---------------- Quick Test for Quick decisions ----------------
let dqData = null, dqBusy = false, dqRun = 0;
const DQ_COST = 'This sends one real request.';

async function dqOpened() {
  if (dqBusy) return;
  try { dqData = await (await fetch('/quicktest/decision/options')).json(); } catch (e) { return; }
  const who = $('dqWho'), sc = $('dqScenario');
  const keepWho = who.value, keepSc = sc.value;
  who.replaceChildren(); sc.replaceChildren();
  let firstOn = '';
  for (const c of dqData.choices) {
    const o = document.createElement('option');
    o.value = c.id; o.textContent = c.label; o.disabled = !!c.reason;
    who.appendChild(o);
    if (!c.reason && !firstOn) firstOn = c.id;
  }
  const keep = dqData.choices.find(c => c.id === keepWho && !c.reason);
  who.value = keep ? keepWho : firstOn;
  for (const s of dqData.scenarios) {
    const o = document.createElement('option');
    o.value = s.id; o.textContent = s.name;
    sc.appendChild(o);
  }
  if (dqData.scenarios.some(s => s.id === keepSc)) sc.value = keepSc;
  $('dqWhoHint').textContent = dqData.choices.filter(c => c.reason).map(c => c.reason).join(' ');
  dqShowScenario(); dqShowCost();
}

function dqShowScenario() {
  const s = dqData && dqData.scenarios.find(x => x.id === $('dqScenario').value);
  $('dqState').textContent = s ? s.state : '';
}

// A paid choice says so before it runs, and it runs on the button only.
function dqShowCost() {
  const c = dqData && dqData.choices.find(x => x.id === $('dqWho').value);
  $('dqCost').textContent = c && c.paid ? DQ_COST : '';
}

function dqRow(name, text, conf) {
  const row = document.createElement('div'); row.className = 'dq-row';
  const n = document.createElement('span'); n.className = 'name'; n.textContent = name;
  const a = document.createElement('span'); a.className = 'ans'; a.textContent = text;
  if (conf !== null && conf !== undefined) {
    const k = document.createElement('span'); k.className = 'conf'; k.textContent = Math.round(conf * 100) + '% sure'; a.appendChild(k);
  }
  row.append(n, a);
  return row;
}

function dqAsked(out, r) {
  const h = document.createElement('h4'); h.textContent = 'What was asked'; out.appendChild(h);
  const st = document.createElement('div'); st.className = 'dq-state'; st.textContent = $('dqState').textContent; out.appendChild(st);
  for (const q of r.asked || []) out.appendChild(dqRow(q.name, q.text, null));
}

async function dqSend() {
  if (dqBusy || !dqData) return;
  const run = ++dqRun, btn = $('dqBtn'), out = $('dqOut');
  dqBusy = true; btn.disabled = true; btn.textContent = 'Waiting…';
  out.replaceChildren();
  const typing = document.createElement('div'); typing.className = 'qt-typing'; typing.textContent = 'Waiting for the answers…'; out.appendChild(typing);
  let r;
  try {
    const resp = await fetch(location.origin + '/quicktest/decision', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-MultiProxy-Quick-Test': '1'},
      body: JSON.stringify({who: $('dqWho').value, scenario: $('dqScenario').value}),
    });
    r = await resp.json();
    if (!resp.ok) r = {ok: false, error: (r.error && r.error.message) || 'Something went wrong'};
  } catch (e) { r = {ok: false, error: e.message}; }
  if (run !== dqRun) return;
  out.replaceChildren();
  if (r.ok) {
    dqAsked(out, r);
    const h = document.createElement('h4'); h.textContent = 'Answers'; out.appendChild(h);
    for (const a of r.answers) out.appendChild(dqRow(a.name, a.text, a.confidence));
    const t = document.createElement('div'); t.className = 'qt-timing'; t.style.marginTop = '10px'; t.textContent = r.line; out.appendChild(t);
  } else {
    if (r.asked) dqAsked(out, r);
    const turn = document.createElement('div'); turn.className = 'qt-turn error'; turn.style.marginTop = '12px';
    const label = document.createElement('div'); label.className = 'who'; label.textContent = 'Error';
    const body = document.createElement('div'); body.className = 'text'; body.textContent = r.error + (r.next ? ' ' + r.next : '');
    turn.append(label, body); out.appendChild(turn);
  }
  dqBusy = false; btn.disabled = false; btn.textContent = 'Send';
  dqOpened();
}
"""
