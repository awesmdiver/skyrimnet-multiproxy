"""The Settings card "Which graphics card" (Routing, step 8, 2026-10-02), built to design/routing-mockup.html section 6 (right).

Plain strings in their own file, spliced into multiproxy.py's page (CSS and JS) and into the Settings pane (settings_ui.PANE_HTML holds the empty `stGpuCard`
box). Drawn from `GET /config/gpu-choice`; the whole card stays hidden while no device has more than one graphics card, so a player with one card sees no change at
all. Uses stFetch / stPost / esc() from the Settings page and the Local models card's script. Every word that came from a server goes through esc().
"""

CSS = r"""
  /* Which graphics card (Settings, 2026-10-02) -- gc-* classes */
  .gc-pc { font-size:0.9rem; font-weight:600; margin:14px 0 4px }
  .gc-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:8px; max-width:640px }
  .gc-name { flex:1; min-width:160px }
  .gc-row select { min-width:220px }
  .gc-off { color:var(--muted); font-size:0.8rem }
  .gc-sub { margin:2px 0 0 0 }
"""

HEADING = "Which graphics card"
HINT = "With two graphics cards, MultiProxy remembers which card each local AI uses, so a restart or a driver update does not move it."
AUTOMATIC = "Automatic"
RESTART_HINT = "Restart it to use this."
RESTART_BUTTON = "Restart now"
RESTARTING = "Restarting…"
PC_OFFLINE = "This device is offline right now."

JS = r"""// ---------------- Which graphics card (Settings card) ----------------
let gcState = null;
let gcMsg = {};            // "<pc>|<ai key>" -> text of a problem
let gcBusy = {};           // "<pc>|<ai key>" -> true while it is restarting
const GC_HEADING = 'Which graphics card';
const GC_HINT = 'With two graphics cards, MultiProxy remembers which card each local AI uses, so a restart or a driver update does not move it.';
const GC_AUTOMATIC = 'Automatic';
const GC_RESTART_HINT = 'Restart it to use this.';
const GC_RESTART_BUTTON = 'Restart now';
const GC_RESTARTING = 'Restarting…';
const GC_OFFLINE = 'This device is offline right now.';

async function gcLoad() {
  try { gcState = await stFetch('/config/gpu-choice'); } catch (e) { gcState = null; }
  gcRender();
}
function gcCardLabel(card, all) {
  const name = card.shortName || card.name;
  const same = all.filter(c => (c.shortName || c.name) === name);
  const mem = card.memoryGb ? ' (' + card.memoryGb + ' GB)' : '';
  return name + mem + (same.length > 1 ? ' #' + (same.indexOf(card) + 1) : '');
}
function gcRender() {
  const el = document.getElementById('stGpuCard');
  if (!el) return;
  const pcs = (gcState && gcState.pcs) || [];
  if (!gcState || !gcState.shown || !pcs.length) { el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = '';
  let h = '<h2>' + esc(GC_HEADING) + '</h2><p class="st-hint" style="margin:0 0 4px">' + esc(GC_HINT) + '</p>';
  pcs.forEach((pc, i) => {
    if (pcs.length > 1) h += '<div class="gc-pc">' + esc(pc.name) + (pc.online ? '' : ' <span class="gc-off">· ' + esc(GC_OFFLINE) + '</span>') + '</div>';
    if (!(pc.ais || []).length) return;
    pc.ais.forEach((ai, j) => {
      const k = pc.id + '|' + ai.key;
      const chosen = ai.choice && pc.cards.some(c => c.id === ai.choice.id) ? ai.choice.id : '';
      h += '<div class="gc-row"><span class="gc-name">' + esc(ai.label) + '</span><select' + (pc.online && !gcBusy[k] ? '' : ' disabled') +
        ' onchange="gcSet(' + i + ',' + j + ', this.value)">' +
        pc.cards.map(c => '<option value="' + esc(c.id) + '"' + (c.id === chosen ? ' selected' : '') + '>' + esc(gcCardLabel(c, pc.cards)) + '</option>').join('') +
        '<option value=""' + (chosen === '' ? ' selected' : '') + '>' + esc(GC_AUTOMATIC) + '</option></select></div>';
      if (ai.note) h += '<div class="st-hint gc-sub">' + esc(ai.note) + '</div>';
      if (ai.restartNeeded) {
        h += '<div class="st-hint gc-sub">' + esc(GC_RESTART_HINT) + ' <button' + (gcBusy[k] || !pc.online ? ' disabled' : '') + ' onclick="gcRestart(' + i + ',' + j + ')">' +
          esc(gcBusy[k] ? GC_RESTARTING : GC_RESTART_BUTTON) + '</button></div>';
      }
      if (gcMsg[k]) h += '<div class="st-err gc-sub">' + esc(gcMsg[k]) + '</div>';
    });
  });
  el.innerHTML = h;
}
async function gcSet(i, j, value) {
  const pc = gcState.pcs[i], ai = pc.ais[j], k = pc.id + '|' + ai.key;
  delete gcMsg[k];
  try {
    const d = await stPost('/config/gpu-choice', { pc: pc.id, ai: ai.key, cardId: value || null });
    gcState = d;
  } catch (e) { gcMsg[k] = e.message; await gcLoad(); return; }
  gcRender();
}
async function gcRestart(i, j) {
  const pc = gcState.pcs[i], ai = pc.ais[j], k = pc.id + '|' + ai.key;
  const here = pc.id === 'this';
  delete gcMsg[k];
  gcBusy[k] = true;
  gcRender();
  try {
    if (ai.key === 'decisions') await stPost('/config/decisions/restart', here ? {} : { pc: pc.id });
    else if (ai.key === 'voice') await stPost(here ? '/config/voice/restart' : '/config/voice/pc/restart', here ? {} : { id: pc.id });
    else {
      const id = ai.key.slice(6);
      if (here) { await stPost('/config/local-models/stop', { id }); await stPost('/config/local-models/start', { id }); }
      else {
        await stPost('/config/other-pcs/courier/action', { id: pc.id, program: 'llamacpp', action: 'unload', model: id });
        await stPost('/config/other-pcs/courier/action', { id: pc.id, program: 'llamacpp', action: 'load', model: id });
      }
    }
  } catch (e) { gcMsg[k] = e.message; }
  delete gcBusy[k];
  await gcLoad();
}
// While the Settings page is open the card follows the AIs (a model started or stopped elsewhere changes whether it needs a restart).
setInterval(() => { try { if (document.getElementById('paneSettings') && document.getElementById('paneSettings').style.display !== 'none' && !Object.keys(gcBusy).length && !(document.activeElement && document.activeElement.tagName === 'SELECT')) gcLoad(); } catch (e) {} }, 10000);
"""
