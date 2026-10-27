"""Graphics memory card: picking another device changes what's shown right away (2026-10-01).

Real bug (director, laptop paired): after choosing a device the <select> kept focus, and the render skipped "while the picker is open", so
the card kept showing this device. Now the picker is built once (rebuilt only when the list of devices changes) and only the body is redrawn.
The card's own script runs under Node with a stand-in page where the picker keeps focus, as in a browser.
"""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

import local_models_ui

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

_LM = local_models_ui.LOCAL_MODELS_JS
SCRIPT = _LM[_LM.index("function esc(s) {"):_LM.index("async function lmPost")] + \
    _LM[_LM.index("// ---------------- Graphics memory card"):_LM.rindex("lmRefresh();")]

HARNESS = """
const els = {};
const card = { _html: '', sets: 0, style: { display: 'none' }, dataset: {} };
Object.defineProperty(card, 'innerHTML', { get() { return this._html; }, set(v) { this._html = v; this.sets++; els.memBody = { innerHTML: '' }; els.memPicker = v.includes('id="memPicker"') ? { id: 'memPicker', value: 'this', textContent: '' } : null; } });
els.memCard = card; els.paneLocal = { style: { display: 'block' } };
global.document = { getElementById: id => els[id] || null, activeElement: null,
  querySelector: () => els.memPicker ? { textContent: window.__label || '' } : null };
global.window = {};
global.shBox = (id, html) => html;      // (the Start here hide button lives in start_here_ui's own script)
global.lmPost = async () => ({});
global.lmRefresh = () => {};
const PCS = [{ id: 'this', label: 'This device · NVIDIA GeForce RTX 5090', online: true }, { id: 'msi', label: 'msi · NVIDIA GeForce RTX 4090 Laptop GPU', online: true }];
const THIS = { pc: 'this', pcs: PCS, available: true, card: 'NVIDIA GeForce RTX 5090', totalMb: 32600, otherMb: 3600, freeMb: 29000, segments: [], skyrimShown: true, skyrimRunning: false, peakMb: 0 };
const MSI = { pc: 'msi', pcs: PCS, state: 'ok', name: 'msi', card: 'NVIDIA GeForce RTX 4090 Laptop GPU', totalMb: 16300, otherMb: 1000, freeMb: 15000, segments: [] };
const gates = {};
global.fetch = async url => { const pc = decodeURIComponent(url.split('pc=')[1]); await (gates[pc] ? gates[pc].promise : null); return { json: async () => pc === 'msi' ? MSI : THIS }; };
function gate(pc) { let r; const p = new Promise(x => r = x); gates[pc] = { promise: p, open: r }; }
const tick = () => new Promise(r => setTimeout(r, 20));
"""


def run(tail: str) -> dict:
    src = HARNESS + SCRIPT + "\n" + tail
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


OUT = "console.log(JSON.stringify({ body: els.memBody.innerHTML, sets: card.sets, selSame: els.memPicker === picker0, memPick }));"


def test_picking_another_pc_changes_the_card_even_though_the_picker_keeps_focus():
    r = run("""(async () => {
      memStart(); await tick();
      const picker0 = els.memPicker;
      const first = els.memBody.innerHTML;
      document.activeElement = els.memPicker;                 // the <select> keeps focus after a choice
      els.memPicker.value = 'msi'; window.__label = 'msi · NVIDIA GeForce RTX 4090 Laptop GPU';
      memPickChanged('msi'); await tick();
      console.log(JSON.stringify({ first, body: els.memBody.innerHTML, sets: card.sets, selSame: els.memPicker === picker0, value: els.memPicker.value }));
      clearTimeout(memTimer); process.exit(0);
    })();""")
    assert "NVIDIA GeForce RTX 5090" in r["first"]
    assert "msi · NVIDIA GeForce RTX 4090 Laptop GPU · 15.9 GB" in r["body"] and "5090" not in r["body"]
    assert r["sets"] == 1 and r["selSame"] and r["value"] == "msi", "the picker was never rebuilt, so focus and an open list are undisturbed"


def test_while_the_other_pcs_numbers_load_the_body_says_so_not_the_last_pcs_numbers():
    r = run("""(async () => {
      memStart(); await tick();
      gate('msi');
      document.activeElement = els.memPicker; window.__label = 'msi · NVIDIA GeForce RTX 4090 Laptop GPU';
      memPickChanged('msi'); await tick();
      console.log(JSON.stringify({ body: els.memBody.innerHTML }));
      clearTimeout(memTimer); process.exit(0);
    })();""")
    assert r["body"] == '<div class="mem-peak">Reading msi · NVIDIA GeForce RTX 4090 Laptop GPU…</div>'


def test_an_answer_for_a_pc_that_is_no_longer_picked_is_ignored():
    r = run("""(async () => {
      memStart(); await tick();
      gate('msi');
      memPickChanged('msi');                                  // slow answer pending
      memPickChanged('this'); await tick();                   // switched back; this device answers at once
      gates.msi.open(); await tick();                         // the old answer arrives late
      console.log(JSON.stringify({ body: els.memBody.innerHTML, memPick }));
      clearTimeout(memTimer); process.exit(0);
    })();""")
    assert "RTX 5090" in r["body"] and "4090" not in r["body"] and r["memPick"] == "this"


def test_the_picker_is_rebuilt_only_when_the_list_of_pcs_really_changes():
    r = run("""(async () => {
      memStart(); await tick();
      await memRefresh(); await memRefresh();
      const before = card.sets;
      PCS.push({ id: 'den', label: 'den (Ollama)', online: true });
      await memRefresh();
      console.log(JSON.stringify({ before, after: card.sets, hasDen: card.innerHTML.includes('den (Ollama)') }));
      clearTimeout(memTimer); process.exit(0);
    })();""")
    assert r["before"] == 1 and r["after"] == 2 and r["hasDen"]
