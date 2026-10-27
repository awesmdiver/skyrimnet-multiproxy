"""The LM Studio provider's two small extras on its panel (2026-10-01): where its server is (default localhost:1234, changeable,
the way a player points MultiProxy at another address) and a Start button when LM Studio's server is off and its `lms` tool is
on this device. Plain string in its own file, spliced into multiproxy.py's page (so none of it needs doubled braces); everything that came
from the server goes through esc() (defined by the Local models card's script on the same page).
"""

LMSTUDIO_JS = r"""// ---------------- LM Studio panel extras ----------------
// The LM Studio panel's own "Start here" list (suSteps() in the page hands LM Studio's to this).
function lmsSteps() {
  return shBox('setup-lmstudio', '<div class="su-box"><h4>Start here</h4><ol class="su-steps">' +
    ['Open LM Studio and download a model.',
     'Back here, press Start LM Studio\'s server if you see the button.',
     'Leave the address alone unless LM Studio runs on another device.',
     'Pick the model from the list below.'].map(t => '<li>' + t + '</li>').join('') + '</ol></div>');
}
function lmsPanelExtras(p) {
  if (p.id !== 'lmstudio') return '';
  let h = '<div class="key-row"><input type="text" id="lmsAddr" placeholder="LM Studio\'s address" value="' + esc(p.address || '') + '">' +
    '<button onclick="lmsSaveAddress(false)">Save</button>' +
    (p.address ? '<button class="ghost" onclick="lmsSaveAddress(true)">Use the default</button>' : '') + '</div>';
  if (p.canStart) h += '<div class="key-row" style="margin-top:10px"><button onclick="lmsStart(this)">Start LM Studio\'s server</button></div>';
  return h + '<div id="lmsErr"></div>';
}
// An error where the action was: the page's warning line (dot plus text, no box), not the browser's alert box.
function lmsShowError(msg) {
  const box = document.getElementById('lmsErr');
  if (box) box.innerHTML = '<div class="vc-warn"><span class="op-dot warn"></span><span>' + esc(msg) + '</span></div>';
}
async function lmsApply(r) {
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'Couldn\'t connect to LM Studio. Make sure it is running.');
  const p = byId('lmstudio');
  Object.assign(p, data);
  updatePickerOption(p);
  selectProvider('lmstudio');
}
async function lmsSaveAddress(reset) {
  const address = reset ? '' : document.getElementById('lmsAddr').value.trim();
  try {
    await lmsApply(await fetch('/config/lmstudio-address', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ address }) }));
  } catch (e) { lmsShowError(e.message); }
}
async function lmsStart(btn) {
  const original = btn.textContent;
  btn.textContent = 'Starting…';
  btn.disabled = true;
  try {
    await lmsApply(await fetch('/config/lmstudio-start', { method: 'POST' }));
  } catch (e) {
    btn.disabled = false;
    btn.textContent = original;
    lmsShowError(e.message);
  }
}
"""
