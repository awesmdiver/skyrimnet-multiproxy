"""Quick Test's chat, as a panel on the dashboard (2026-10-03).

It used to open with `window.open`, so Chrome showed a pop-up window with an `about:blank` address bar and its own download button, neither of which a page
can remove. Now Send opens this panel over the dashboard (the same `op-modal` look the Voice and Other devices cards use), large: about 900 px wide and most of
the window's height, full width on a narrow window. Plain strings in their own file, spliced into multiproxy.py's page, so none of it needs doubled braces.

  * × (top right) and Escape close it; a click outside does NOT, so a chat is never lost by a stray click. Closing asks nothing.
  * The page behind can't scroll while it is open; focus goes into the reply box; Tab stays inside the panel.
  * Every message carries `X-MultiProxy-Quick-Test: 1` (Routing skips Quick Test).
  * Opening another Quick Test replaces the content (a new chat); a reply still on its way to the old chat is dropped.
Everything the player or a provider typed goes in with textContent, never as markup.
"""

CSS = r"""
  /* Quick Test's chat panel (2026-10-03) -- qt-* classes */
  .qt-box { background:var(--panel); border:1px solid var(--line); border-radius:12px; width:min(900px, 100%); height:calc(100vh - 40px); display:flex; flex-direction:column; overflow:hidden; outline:none }
  .qt-meta { color:var(--soft); font-size:0.82rem; padding:16px 20px 12px; border-bottom:1px solid var(--line); flex:none }
  .qt-meta b { color:var(--text) }
  .qt-meta h3 { margin:0 0 4px; font-size:1rem; color:var(--text) }
  .qt-meta-row { display:flex; justify-content:space-between; align-items:flex-start; gap:12px }
  .qt-close { background:transparent; border:none; color:var(--soft); font-size:1.5rem; line-height:1; cursor:pointer; padding:0 6px; border-radius:6px }
  .qt-close:hover { background:var(--bg); color:var(--text) }
  .qt-meta details { margin-top:8px }
  .qt-meta summary { color:var(--soft); font-size:0.78rem; cursor:pointer; user-select:none }
  .qt-meta summary:hover { color:var(--text) }
  .qt-sent { white-space:pre-wrap; font-size:0.8rem; line-height:1.5; color:var(--soft); background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:10px; margin-top:6px; max-height:30vh; overflow-y:auto }
  .qt-transcript { flex:1; overflow-y:auto; padding:16px 20px }
  .qt-turn { margin-bottom:16px }
  .qt-turn .who { font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); margin-bottom:4px }
  .qt-turn.you .who { color:var(--accent-text) }
  .qt-turn.assistant .who { color:var(--ok-text) }
  .qt-turn.error .who { color:var(--err-text); display:flex; justify-content:space-between; align-items:center }
  .qt-turn .text { white-space:pre-wrap; font-size:0.95rem; line-height:1.6; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:12px 14px; overflow-wrap:anywhere }
  .qt-turn.you .text { background:color-mix(in srgb, var(--accent) 19%, transparent); border-color:color-mix(in srgb, var(--accent) 25%, transparent) }
  .qt-turn.error .text { background:color-mix(in srgb, var(--err) 14%, transparent); border-color:color-mix(in srgb, var(--err) 45%, transparent); color:color-mix(in srgb, var(--err) 60%, var(--text)) }
  .qt-dismiss { background:transparent; border:none; color:var(--err); font-size:1rem; line-height:1; cursor:pointer; padding:0 4px }
  .qt-dismiss:hover { background:var(--bg) }
  .qt-foot { display:flex; justify-content:space-between; align-items:center; margin-top:5px }
  .qt-timing { color:var(--ok-text); font-size:0.75rem }
  .qt-timing .tokens { color:var(--muted); margin-left:6px }
  .qt-copy { background:transparent; border:1px solid var(--line); color:var(--soft); padding:2px 9px; font-size:0.72rem; border-radius:5px; cursor:pointer }
  .qt-copy:hover { background:var(--bg); color:var(--text) }
  .qt-composer { flex:none; display:flex; gap:8px; padding:14px 20px; border-top:1px solid var(--line) }
  .qt-composer input { flex:1; min-width:0; background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:9px 12px; font-family:inherit; font-size:0.9rem }
  .qt-composer input:focus { outline:none; border-color:var(--accent) }
  .qt-composer button { background:var(--accent); color:var(--on-accent); border:none; padding:9px 18px; border-radius:6px; cursor:pointer; font-size:0.85rem; flex:none }
  .qt-composer button:hover { background:var(--accent-hover) }
  .qt-composer button:disabled { background:var(--line); color:var(--muted); cursor:wait }
  .qt-typing { color:var(--muted); font-size:0.85rem; font-style:italic }
  @media (max-width:700px) { .qt-modal { padding:0 } .qt-box { width:100%; height:100vh; border-radius:0; border:0 } }
"""

MODAL_HTML = """
  <div id="qtModal" class="op-modal qt-modal" style="display:none">
    <div class="qt-box" id="qtBox" role="dialog" aria-modal="true" aria-labelledby="qtTitle" tabindex="-1">
      <div class="qt-meta">
        <div class="qt-meta-row">
          <div><h3 id="qtTitle">Realtime chat</h3><div id="qtWho"></div></div>
          <button class="qt-close" id="qtClose" onclick="closeQuickTestPanel()" aria-label="Close" title="Close (Esc)">&times;</button>
        </div>
        <details><summary>What we sent</summary><div class="qt-sent" id="qtSent"></div></details>
      </div>
      <div class="qt-transcript" id="qtTranscript"></div>
      <div class="qt-composer">
        <input id="qtInput" autocomplete="off" placeholder="Say something else, as if you pressed F1 to keep talking..." aria-label="Your next line">
        <button id="qtSend" onclick="qtSendTurn()">Send</button>
      </div>
    </div>
  </div>
"""

JS = r"""// ---------------- Quick Test chat panel ----------------
let qtRun = 0;                 // which chat is current; an answer still on its way to an older chat is dropped
let qtMessages = [];
let qtModelId = '';
let qtBusy = false;

function qtIsOpen() { const m = $('qtModal'); return !!m && m.style.display !== 'none'; }

function qtAddTurn(who, text) {
  const t = $('qtTranscript');
  const div = document.createElement('div');
  div.className = 'qt-turn ' + who;
  const label = document.createElement('div');
  label.className = 'who';
  label.textContent = who === 'you' ? 'You' : who === 'error' ? 'Error' : 'Assistant';
  const body = document.createElement('div');
  body.className = 'text';
  body.textContent = text;
  div.appendChild(label);
  div.appendChild(body);
  // An error is never removed by a timer or a refresh: it stays until the player clicks the small x.
  if (who === 'error') {
    const x = document.createElement('button');
    x.className = 'qt-dismiss';
    x.textContent = '×';
    x.title = 'Close error';
    x.setAttribute('aria-label', 'Close error');
    x.onclick = () => div.remove();
    label.appendChild(x);
  }
  t.appendChild(div);
  t.scrollTop = t.scrollHeight;
  return div;
}

function qtAddTiming(div, time, tokens) {
  const foot = document.createElement('div');
  foot.className = 'qt-foot';
  const span = document.createElement('span');
  span.className = 'qt-timing';
  span.textContent = tokens ? time + ',' : time;
  const tok = document.createElement('span');
  tok.className = 'tokens';
  tok.textContent = tokens;
  span.appendChild(tok);
  const copy = document.createElement('button');
  copy.className = 'qt-copy';
  copy.textContent = 'Copy';
  copy.onclick = () => navigator.clipboard.writeText(div.querySelector('.text').textContent).catch(() => {});
  foot.appendChild(span);
  foot.appendChild(copy);
  div.appendChild(foot);
}

// A failed turn stays visible in the transcript (added by the caller before this runs) so the conversation's own history is never silently lost, but the failed
// user message itself is popped back out of the history: it was never answered, so the next attempt is a clean new turn.
async function qtSendReal(userMsg) {
  const run = qtRun;
  const t = $('qtTranscript'), input = $('qtInput'), btn = $('qtSend');
  qtMessages.push({role: 'user', content: userMsg});
  qtBusy = true; btn.disabled = true; btn.textContent = 'Waiting…';
  const typing = document.createElement('div');
  typing.className = 'qt-typing';
  typing.textContent = 'Assistant is replying...';
  t.appendChild(typing);
  t.scrollTop = t.scrollHeight;
  const start = Date.now();
  // A local backend (Ollama, LM Studio, a program like Codex) that isn't running can leave this in silence for minutes before the server's own timeout, so after a
  // threshold well past a normal working reply (a cold local model measured 14.8 s) the SAME line says what to check.
  const isLocalBackend = qtModelId.startsWith('ollama/') || qtModelId.startsWith('lmstudio/') || qtModelId.startsWith('codex/') || qtModelId.startsWith('geminicli/') || qtModelId.startsWith('grok/');
  const hintTimer = isLocalBackend ? setTimeout(() => {
    typing.textContent = "Still waiting. If you use Ollama, LM Studio, Codex, or Antigravity, make sure that app is running.";
  }, 20000) : null;
  const finish = () => {
    if (run !== qtRun) return;
    qtBusy = false; btn.disabled = false; btn.textContent = 'Send'; input.focus();
  };
  try {
    const r = await fetch(location.origin + '/v1/chat/completions', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-MultiProxy-Quick-Test': '1'},
      body: JSON.stringify({model: qtModelId, messages: qtMessages}),
    });
    const data = await r.json();
    const elapsed = ((Date.now() - start) / 1000).toFixed(1);
    clearTimeout(hintTimer);
    typing.remove();
    if (run !== qtRun) return;
    if (r.ok && data.choices) {
      const reply = data.choices[0].message.content;
      qtMessages.push({role: 'assistant', content: reply});
      const div = qtAddTurn('assistant', reply);
      const tokens = data.usage ? (data.usage.prompt_tokens + ' tokens in / ' + data.usage.completion_tokens + ' tokens out') : '';
      qtAddTiming(div, elapsed + 's', tokens);
    } else {
      qtMessages.pop();
      const raw = ((data.error && data.error.message) || 'Something went wrong').replace(/[.\s]+$/, '');
      // The Help tab has steps for a refused key, a missing sign-in or key, no credit and a program that is not installed; a problem on the provider's side is temporary.
      const helpHasSteps = /key|sign(ed)? in|credit|balance|billing|not installed|didn.t accept/i.test(raw);
      const temporary = r.status >= 500 || r.status === 429 || /temporar|try again|overload|unavailable|timed out|took too long|too busy/i.test(raw);
      const tail = helpHasSteps ? '. See the Help tab for steps to fix this.' : temporary ? '. Try again in a moment, or pick another model.' : '.';
      qtAddTurn('error', raw + tail);
    }
  } catch (e) {
    clearTimeout(hintTimer);
    typing.remove();
    if (run !== qtRun) return;
    qtMessages.pop();
    qtAddTurn('error', e.message);
  }
  finish();
}

function qtSendTurn() {
  if (qtBusy) return;
  const input = $('qtInput');
  const msg = input.value.trim();
  if (!msg) return;
  qtAddTurn('you', msg);
  input.value = '';
  qtSendReal(msg);
}

function openQuickTestPanel() {
  const modelSel = $('testModel').value;
  const model = modelSel === '__custom' ? $('customModel').value.trim() : modelSel;
  const providerName = (byId($('testProvider').value) || {}).name || 'Provider';
  const sys = $('sysPrompt').value;
  const extra = $('extraContext').value.trim();
  const fullSystem = extra ? sys + '\n\n' + extra : sys;
  const userMsg = $('userMsg').value;
  const scenarioLabel = $('testScenario').selectedOptions[0].text;

  // A new test is a new chat: the old history, transcript and any reply still on its way are dropped.
  qtRun++;
  qtMessages = [{role: 'system', content: fullSystem}];
  qtModelId = model;
  qtBusy = false;
  $('qtTranscript').replaceChildren();
  $('qtInput').value = '';
  $('qtSend').disabled = false; $('qtSend').textContent = 'Send';
  const who = $('qtWho');
  who.replaceChildren();
  const pn = document.createElement('b'); pn.textContent = providerName;
  const sc = document.createElement('b'); sc.textContent = scenarioLabel;
  who.append(pn, ' / ' + model + ' · scenario: ', sc);
  $('qtSent').textContent = 'System: ' + fullSystem;
  $('qtModal').style.display = 'flex';
  document.body.style.overflow = 'hidden';            // the page behind can't scroll while the panel is open
  $('qtBox').scrollTop = 0;
  qtAddTurn('you', userMsg);
  qtSendReal(userMsg);
  $('qtInput').focus();
}

function closeQuickTestPanel() {
  if (!qtIsOpen()) return;
  qtRun++;                                            // an answer still on its way has nowhere to go
  qtBusy = false;
  $('qtModal').style.display = 'none';
  document.body.style.overflow = '';
  const send = $('testBtn');
  if (send) send.focus();
}

// Escape closes; Tab stays inside the panel; Enter in the reply box sends. A click outside does nothing on purpose.
document.addEventListener('keydown', e => {
  if (!qtIsOpen()) return;
  if (e.key === 'Escape') { e.preventDefault(); closeQuickTestPanel(); return; }
  if (e.key === 'Enter' && e.target && e.target.id === 'qtInput') { e.preventDefault(); qtSendTurn(); return; }
  if (e.key === 'Tab') {
    const items = [...$('qtBox').querySelectorAll('button, input, summary, [tabindex]:not([tabindex="-1"])')].filter(el => !el.disabled && el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === $('qtBox'))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
"""
