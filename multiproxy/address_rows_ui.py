"""Settings > SkyrimNet connection shows every address a player pastes into SkyrimNet (2026-10-03), not only the chat one: Chat (always there), Voice (the Higgs
Endpoint) and Quick decisions (the System One API Endpoint). Voice and Quick decisions come from `GET /config/addresses`, which takes each address from the helper its
own card uses (`voice_wiring.endpoint_address`, `decision_wiring.endpoint_address`): one source, no copy. Until one is set up its row still shows, quieter, with
"Not set up yet" and a link to the tab where it is set up, so the player learns it exists. Plain strings, spliced into multiproxy.py's page.
"""

CSS = r"""
  /* Settings > SkyrimNet connection: the other addresses (2026-10-03) */
  .addr-row { margin-top:14px }
  .addr-row .endpoint { display:flex; justify-content:space-between; align-items:center; gap:10px }
  /* The "Paste into..." line: the same size as the row's name above it, never larger. */
  .addr-hint { color:var(--muted); font-size:0.8rem; margin-top:4px }
  /* Not set up yet: the same box as the chat address, with the words and a link in place of the address (no grey block, no fading). */
  .addr-row .endpoint .addr-off { color:var(--muted); font-family:system-ui,sans-serif }
"""

JS = r"""// ---------------- Settings > SkyrimNet connection: the voice and quick decisions addresses ----------------
const ADDR_ROWS = [
  { key: 'voice', name: 'Voice', hint: "Paste into SkyrimNet's Higgs tab under Endpoint.", tab: 'voice', link: 'Open the Voice tab' },
  { key: 'decisions', name: 'Quick decisions', hint: "Paste into SkyrimNet under AI > Providers, API Endpoint (API Style: System One).", tab: 'decisions', link: 'Open the Decisions tab' },
];
function addrRenderRows(data) {
  const host = document.getElementById('addrRows');
  if (!host) return;
  host.replaceChildren();
  // Speech to text: its address never changes, so the row always shows it (only when the read worked); the hint drops "Turn it on first" once the switch is on.
  const rows = ADDR_ROWS.slice();
  const stt = (data || {}).stt;
  if (stt && stt.address) rows.push({ key: 'stt', name: 'Speech to text', always: true,
    hint: "Paste into SkyrimNet's speech settings under External Whisper." + (stt.on ? '' : ' Turn it on first on the Voice tab.') });
  for (const r of rows) {
    const info = r.always ? { setUp: true, address: stt.address } : ((data || {})[r.key] || {});
    const row = document.createElement('div');
    row.className = 'addr-row';
    const name = document.createElement('span');
    name.className = 'label addr-name';
    name.textContent = r.name;
    row.appendChild(name);
    if (info.setUp) {
      const box = document.createElement('div');
      box.className = 'endpoint';
      const span = document.createElement('span');
      span.textContent = info.address;
      const btn = document.createElement('button');
      btn.className = 'copy';
      btn.textContent = 'Copy';
      btn.onclick = () => copyModel(btn, info.address);
      box.append(span, btn);
      row.appendChild(box);
    } else {
      const box = document.createElement('div');
      box.className = 'endpoint';
      const off = document.createElement('span');
      off.className = 'addr-off';
      off.append('Not set up yet. ');
      const a = document.createElement('a');
      a.className = 'hp-link';
      a.textContent = r.link;
      a.onclick = () => showMainTab(r.tab);
      off.appendChild(a);
      box.appendChild(off);
      row.appendChild(box);
    }
    const hint = document.createElement('div');
    hint.className = 'addr-hint';
    hint.textContent = r.hint;
    row.appendChild(hint);
    host.appendChild(row);
  }
}
async function addrLoad() {
  try { addrRenderRows(await (await fetch('/config/addresses')).json()); }
  catch (e) { addrRenderRows({}); }
}
// (The Settings page draws these rows into #addrRows each time it is drawn and calls addrLoad() itself.)
addrLoad();
"""
