"""The "Which voice?" link and pop-up on the Voice tab (4.3), built to design/mockup-which-voice.html, revision 2.

One link on the Voice heading line opens one table of the four voices MultiProxy offers (Breeze with its three extra takes as sub-rows), each with a Man and a Woman
sample and a "Use this voice" button. The samples are bundled (voice-samples/: OGG files, samples.json, SAMPLES-LICENSE.md): the page plays them from this program's own
folder through the route below, nothing is fetched from anywhere else, and nothing plays until a button is pressed.

The numbers in the table are the ones measured in samples.json (never a guess): the time is the long line's, the memory is the peak over the idle card. Words are the
mockup's own (a Gemini pass is for release time). Plain strings in their own file, spliced into multiproxy.py's page (no doubled braces here).
"""
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

import voice_engine

FOLDER = Path(__file__).resolve().parent / "voice-samples"
SAMPLES_JSON = "samples.json"
PLAY_LINE = "short"            # the line the Man / Woman buttons play (the long one is bundled too, for a later use)
TIME_LINE = "long"             # the line the time column measures: the short line's file was made first, so it holds the engine's cold start (Qwen3-TTS: 3.3 s against 0.95 s warm)

# The words, in one place. The page script gets them as data.
WORDS = {
    "link": "Which voice?",
    "title": "Which voice?",
    "intro": "Press **Man** or **Woman** to hear the same line in each voice. Times and memory were tested on a fast graphics card (RTX 5090); yours might be slower.",
    "cols": ["Voice", "How it sounds", "A long line", "Memory", "Languages", "Listen", ""],
    "man": "Man", "woman": "Woman", "use": "Use this voice", "current": "(current)",
    "foot": "Higgs and Breeze are free for personal, non-commercial use.",
    "licences": "Read the licences", "close": "Close", "back": "Back",
    "licTitle": "The licences",
    "licIntro": "The voices you hear are made up by AI: no real person and no game voice. Each model's own licence is below.",
    "noSamples": "The sample clips are missing from this copy of MultiProxy.",
    "failed": "Couldn't play that sample.",
}

# The rows. `model` is the voice's key, `take` is a Breeze take (empty: the voice itself); `prefix`/`measure` are how samples.json names the clips and the measurements.
ROWS = [
    {"model": "higgs", "take": "", "name": "Higgs", "by": "Boson AI", "note": "", "sound": "Expressive and dramatic. Great for emotional scenes.", "langs": "Many",
     "prefix": "higgs", "measure": "higgs"},
    {"model": "qwen3tts", "take": "", "name": "Qwen3-TTS", "by": "Alibaba (Qwen)", "note": "", "sound": "Clear and even. A great all-rounder.", "langs": "10",
     "prefix": "qwen3-tts", "measure": "qwen3tts"},
    {"model": "chatterbox", "take": "", "name": "Chatterbox", "by": "Resemble AI", "note": "", "tip": "No longer maintained", "sound": "Lively English voices. The smallest of the four.", "langs": "English",
     "prefix": "chatterbox", "measure": "chatterbox"},
    {"model": "breeze", "take": "", "name": "Breeze", "by": "BreezeBlue", "note": "", "head": True, "sound": "Natural and warm. Follows directions on how to speak.", "langs": "English",
     "prefix": "breeze-standard", "measure": "breeze"},
    {"model": "breeze", "take": "quickest", "name": "Quickest", "by": "", "note": "", "sound": "A smaller, lighter version. Faster on long lines.", "langs": "",
     "prefix": "breeze-quickest", "measure": "breeze-quickest", "sub": True},
    {"model": "breeze", "take": "standard", "name": "Standard", "by": "", "note": "", "sound": "", "langs": "",
     "prefix": "breeze-standard", "measure": "breeze", "sub": True},
    {"model": "breeze", "take": "more", "name": "More natural", "by": "", "note": "", "sound": "Same speed, but sounds more natural. Needs a {download} extra download.", "langs": "",
     "prefix": "breeze-more-natural", "measure": "breeze-more", "sub": True},
    {"model": "breeze", "take": "most", "name": "Most natural", "by": "", "note": "", "sound": "The most natural sound. Slower on long lines and needs about {memory} of graphics memory.", "langs": "",
     "prefix": "breeze-most-natural", "measure": "breeze-most", "sub": True},
]

# One attribution line per model, exactly as each model's licence asks (voice-samples/SAMPLES-LICENSE.md, the director's decision of 2026-11-19).
ATTRIBUTION = {
    "higgs": "This audio was created with Boson AI's Higgs Audio.",
    "qwen3tts": "Voice made with Qwen3-TTS (Qwen team, Alibaba), Apache-2.0.",
    "chatterbox": "Voice made with Chatterbox (Resemble AI), MIT licence.",
    "breeze": "Breeze TTS 2 is licensed under the BreezeBlue Research and Non-Commercial License Agreement. Copyright (c) 2026 RESONIA, INC. All Rights Reserved.",
}
LICENCE_NAMES = {"higgs": "Boson AI's Higgs licence (personal, non-commercial use)", "qwen3tts": "Apache-2.0", "chatterbox": "MIT",
                 "breeze": "BreezeBlue Research and Non-Commercial License"}
LICENCE_LINKS = {"qwen3tts": "https://www.apache.org/licenses/LICENSE-2.0", "chatterbox": "https://opensource.org/license/mit"}


def load() -> Optional[dict]:
    """samples.json, or None when this copy has no samples (the pop-up then says so)."""
    try:
        return json.loads((FOLDER / SAMPLES_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clip_names(data: dict) -> set:
    """The clip files samples.json names and that are really here: the only files the audio route will ever serve."""
    out = set()
    for f in data.get("files", []):
        name = str(f.get("file", ""))
        if name.startswith("audio/") and name.endswith(".ogg") and "/" not in name[6:] and "\\" not in name and (FOLDER / name).is_file():
            out.add(name[6:])
    return out


def _round(x: float) -> str:
    return f"{x:.1f}"


def _card_figures(r: dict) -> tuple:
    """(graphics memory, extra download) for a row, from the same places the Voice card and its memory check read them, never typed here: a Breeze take's memory is the words the card
    shows for it (Take.memory_text); another voice's is the figure its memory check uses (voice_engine.gb of its vram_bytes). The download is the take's file size counted the
    way the card counts it (voice_engine.gb: the way Windows Explorer does, so 6.8 GB and not 7.3)."""
    vm = voice_engine.VOICE_MODELS.get(r["model"])
    if vm is None:
        return "", ""
    t = vm.take(r["take"]) if r["take"] else None
    if t is not None:
        return t.memory_text, voice_engine.gb(t.file.size)
    return voice_engine.gb(vm.vram_bytes), ""


def table() -> dict:
    """The pop-up's rows, from samples.json: names, plain lines, measured time and memory, and which clips exist."""
    data = load()
    if not data:
        return {"rows": [], "licences": [], "words": WORDS}
    have = _clip_names(data)
    meas = data.get("measurements", {})
    rows = []
    for r in ROWS:
        memory, download = _card_figures(r)
        head = bool(r.get("head"))
        times = [f["makeSeconds"] for f in data.get("files", []) if f.get("file", "").startswith("audio/" + r["prefix"] + "__") and f.get("line") == TIME_LINE and "makeSeconds" in f]
        m = meas.get(r["measure"], {})
        clips = {}
        for who in ("man", "woman"):
            name = f"{r['prefix']}__{who}__{PLAY_LINE}.ogg"
            if name in have:
                clips[who] = name
        sound = r["sound"].format(download=download, memory=memory)
        if r["model"] == "breeze" and r["take"] == "standard":
            sound = voice_engine.VOICE_MODELS["breeze"].take("standard").one_line      # the Voice card's own words for it, so the two places agree
        rows.append({"model": r["model"], "take": r["take"], "name": r["name"], "by": r["by"], "note": r["note"], "tip": r.get("tip", ""), "head": head, "sound": sound, "langs": r["langs"], "sub": bool(r.get("sub")),
                     "time": "" if head else ((_round(sum(times) / len(times)) + " s") if times else ""), "memory": "" if head else memory, "clips": {} if head else clips})
    licences = []
    for key in ("higgs", "qwen3tts", "chatterbox", "breeze"):
        vm = voice_engine.VOICE_MODELS.get(key)
        licences.append({"model": key, "name": next(x["name"] for x in ROWS if x["model"] == key), "licence": LICENCE_NAMES[key], "url": LICENCE_LINKS.get(key) or (vm.license_url if vm else ""),
                         "line": ATTRIBUTION[key], "note": "No longer updated by its makers." if key == "chatterbox" else ""})
    return {"rows": rows, "licences": licences, "words": WORDS, "measuredOn": data.get("madeOn", "")}


def setup(app: FastAPI) -> None:
    @app.get("/config/voice/samples")
    async def voice_samples():
        """What the Which voice? pop-up shows."""
        return table()

    @app.get("/config/voice/samples/audio")
    async def voice_samples_audio(file: str = ""):
        """One bundled clip. Only a file samples.json names (plain names, no folders) is ever served."""
        data = load()
        if not data or file not in _clip_names(data):
            return JSONResponse({"detail": "That sample isn't here."}, status_code=404)
        return FileResponse(FOLDER / "audio" / file, media_type="audio/ogg", headers={"Cache-Control": "no-store"})


PANE_HTML = """<div id="wvModal" class="op-modal" style="display:none" onclick="if (event.target === this) wvClose()"></div>"""

CSS = r"""
  /* Which voice? (4.3) -- wv-* classes */
  .vc-head { display:flex; justify-content:space-between; align-items:baseline; gap:4px 16px; flex-wrap:wrap; margin:0 0 4px }
  .vc-head h2 { margin:0 }
  .wv-link { font-size:0.85rem; margin-left:auto; text-decoration:none }
  .wv-link:hover { text-decoration:underline }
  /* Wide and tall enough that nothing wraps and nothing scrolls on a normal window (2026-11-19, from a screenshot): the box takes the page's width up to 1280 px, one line per row, and
     is as tall as its content up to the window's height minus the margin of the overlay (20 px each side); only a window shorter than the table scrolls the table, never the whole box. */
  .wv-box { box-sizing:border-box; width:min(1280px, 96vw); max-height:calc(100vh - 40px); padding:20px 24px 18px; gap:0 }
  .wv-box h3 { margin:0 0 4px; font-size:1.15rem }
  .wv-box .wv-intro { margin:0 0 12px; color:var(--muted); font-size:0.9rem; max-width:none }
  .wv-scroll { flex:0 1 auto; min-height:0; overflow:auto }
  .wv-tbl { width:100%; border-collapse:collapse; font-size:0.86rem; table-layout:auto }
  .wv-tbl th { text-align:left; color:var(--muted); font-weight:600; font-size:0.74rem; text-transform:uppercase; letter-spacing:0.04em; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap }
  .wv-tbl td { padding:8px; border-bottom:1px solid var(--line); vertical-align:middle; white-space:nowrap }
  .wv-tbl .wv-n b, .wv-tbl .wv-n .d { display:block }
  .wv-tbl button { white-space:nowrap }
  .wv-tbl button.primary { padding:5px 14px }
  .wv-tbl .wv-n .d { color:var(--muted); font-size:0.8rem }
  .wv-tbl .wv-n .wv-warn { color:var(--warn) }
  .wv-tbl .wv-s { line-height:1.4 }
  .wv-tbl tr.wv-sub td { padding-top:3px; padding-bottom:3px; border-bottom:0; font-size:0.82rem; color:var(--muted) }
  .wv-tbl tr.wv-sub td.wv-n { padding-left:26px }
  .wv-q { color:var(--warn); font-weight:700; cursor:help; margin-left:4px; outline-offset:2px }
  .wv-play { display:inline-flex; gap:6px; align-items:center }
  .wv-play button { padding:3px 10px; font-size:0.78rem }
  .wv-play button.on { border-color:var(--accent) }
  .wv-foot { display:flex; justify-content:space-between; align-items:center; gap:14px; border-top:1px solid var(--line); padding-top:12px; margin-top:10px }
  .wv-foot .wv-lic { color:var(--muted); font-size:0.88rem }
  .wv-err { color:var(--warn); font-size:0.85rem; min-height:1.4em; margin:8px 0 0 }
  .wv-lics { list-style:none; margin:0; padding:0 }
  .wv-lics li { padding:10px 0; border-bottom:1px solid var(--line); font-size:0.9rem; line-height:1.5 }
  .wv-lics li .d { color:var(--muted); font-size:0.84rem }
  /* A window narrower than the one-line table (it needs about 1150 px of window): the table keeps a width of its own, the descriptions may wrap again, and below 760 px it scrolls sideways
     inside the box. The buttons never wrap. */
  @media (max-width:1200px) { .wv-tbl { min-width:760px } .wv-tbl td.wv-sd { white-space:normal } }
  @media (max-width:520px) { .wv-box { padding:16px 14px 14px } .wv-foot { flex-direction:column; align-items:flex-start } .wv-foot button { align-self:flex-end } }
"""

JS = r"""// ---------------- Which voice? ----------------
let wvH = 0, wvData = null, wvView = 'table', wvFrom = null, wvAudio = null, wvPlaying = '', wvErr = '', wvPending = null, wvAuto = null;
function wvLink() { return '<a class="lm-link wv-link" id="wvLink" href="#" role="button" onclick="wvOpen(); return false">Which voice?</a>'; }
function wvRich(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>'); }
function wvIsCurrent(r) {
  if (typeof vcOv === 'undefined' || !vcOv) return false;
  const k = vcShownKey(), i = vcInfo(k);
  if (r.head || !i || !i.installed || vcActiveModel(k) !== r.model) return false;
  return !r.take || (i.take || 'standard') === r.take;
}
function wvBlock() { try { return typeof vcTakesOldText === 'function' && typeof vcOv !== 'undefined' && vcOv ? vcTakesOldText(vcShownKey()) : ''; } catch (e) { return ''; } }          // the older-Courier line, for the Breeze take rows
function wvLine() { const b = wvBlock(); return wvErr || (b ? '\u26a0\ufe0f ' + b : ''); }
function wvTip(t) { const e = document.getElementById('wvErr'); if (e) e.textContent = t || wvLine(); }          // (the ? shows its words when it is reached with the keyboard too, in the fixed line under the table)
function wvStop() { if (wvAudio) { try { wvAudio.pause(); } catch (e) {} wvAudio = null; } wvPlaying = ''; }
function wvPlay(file) {
  wvErr = '';
  if (wvPlaying === file) { wvStop(); wvDraw(); return; }
  wvStop();
  wvPlaying = file;
  const a = new Audio('/config/voice/samples/audio?file=' + encodeURIComponent(file));
  wvAudio = a;
  a.onended = a.onerror = () => { if (wvAudio === a) { wvStop(); wvDraw(); } };
  const p = a.play();
  if (p && p.catch) p.catch(() => { if (wvAudio === a) { wvStop(); wvErr = WV_WORDS.failed; wvDraw(); } });
  wvDraw();
}
function wvRow(r, i) {
  const w = WV_WORDS, cur = wvIsCurrent(r);
  const btn = who => r.clips[who] ? '<button class="ghost' + (wvPlaying === r.clips[who] ? ' on' : '') + '" onclick="wvPlay(\'' + jsq(r.clips[who]) + '\')">' + w[who] + '</button>' : '';
  const nm = '<b title="' + esc(r.name) + '">' + esc(r.name) + (cur ? ' ' + esc(w.current) : '') + (r.tip ? '<span class="wv-q" tabindex="0" role="img" aria-label="' + esc(r.tip) + '" title="' + esc(r.tip) + '" onfocus="wvTip(this.title)" onblur="wvTip(\'\')">?</span>' : '') + '</b>' +
    (r.by || r.note ? '<span class="d" title="' + esc((r.by + ' ' + r.note).trim()) + '">' + esc(r.by) + (r.note ? ' <span class="wv-warn">' + esc(r.note) + '</span>' : '') + '</span>' : '');
  return '<tr' + (r.sub ? ' class="wv-sub"' : '') + '><td class="wv-n">' + nm + '</td><td class="wv-sd"><span class="wv-s" title="' + esc(r.sound) + '">' + esc(r.sound) + '</span></td><td>' + esc(r.time) + '</td><td>' + esc(r.memory) + '</td><td>' +
    esc(r.langs) + '</td><td>' + (r.head ? '' : '<span class="wv-play">' + btn('man') + btn('woman') + '</span>') + '</td><td>' + (r.head || cur ? '' : '<button class="primary"' + (r.take && wvBlock() ? ' disabled' : '') + ' onclick="wvUse(' + i + ')">' + esc(w.use) + '</button>') + '</td></tr>';
}
function wvBody() {
  const w = WV_WORDS, d = wvData;
  if (!d) return '<div class="lm-muted">Looking…</div>';
  if (!d.rows.length) return '<div class="lm-err">' + esc(w.noSamples) + '</div>';
  if (wvView === 'licences') {
    return '<p class="wv-intro">' + esc(w.licIntro) + '</p><ul class="wv-lics">' + d.licences.map(l =>
      '<li><b>' + esc(l.name) + '</b>' + (l.note ? ' <span class="wv-warn" style="color:var(--warn)">' + esc(l.note) + '</span>' : '') + '<div class="d">' + (l.url ? '<a class="lm-link" href="' + esc(l.url) + '" target="_blank" rel="noopener noreferrer">' + esc(l.licence) + '</a>' : esc(l.licence)) + '</div><div class="d">' + esc(l.line) + '</div></li>').join('') + '</ul>';
  }
  return '<table class="wv-tbl"><thead><tr>' + w.cols.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>' + d.rows.map(wvRow).join('') + '</tbody></table>';
}
function wvDraw() {
  const m = document.getElementById('wvModal');
  if (!m || m.style.display === 'none') return;
  const w = WV_WORDS, lic = wvView === 'licences';
  const sc = document.getElementById('wvScroll'), top = sc ? sc.scrollTop : 0;
  if (sc && !lic && sc.offsetHeight) wvH = sc.offsetHeight;          // (the licences keep this height, so the pop-up never changes size because of what it shows)
  m.innerHTML = '<div class="op-modal-box wv-box" role="dialog" aria-modal="true" aria-labelledby="wvTitle" tabindex="-1" id="wvBox"><h3 id="wvTitle">' + esc(lic ? w.licTitle : w.title) + '</h3>' +
    (lic ? '' : '<p class="wv-intro">' + wvRich(w.intro) + '</p>') + '<div class="wv-scroll" id="wvScroll"' + (lic && wvH ? ' style="min-height:' + wvH + 'px"' : '') + '>' + wvBody() + '</div><div class="wv-err" id="wvErr">' + esc(wvLine()) + '</div>' +
    '<div class="wv-foot">' + (lic ? '<span></span>' : '<span class="wv-lic">' + esc(w.foot) + ' <a class="lm-link" href="#" id="wvLics" onclick="wvLicences(true); return false">' + esc(w.licences) + '</a></span>') +
    '<button class="' + (lic ? 'ghost' : 'primary') + '" id="wvCloseBtn" onclick="' + (lic ? 'wvLicences(false)' : 'wvClose()') + '">' + esc(lic ? w.back : w.close) + '</button></div></div>';
  const sc2 = document.getElementById('wvScroll'); if (sc2) sc2.scrollTop = top;
}
function wvLicences(on) { wvView = on ? 'licences' : 'table'; wvDraw(); const b = document.getElementById(on ? 'wvCloseBtn' : 'wvLics'); if (b && b.focus) b.focus(); }
async function wvOpen() {
  const m = document.getElementById('wvModal');
  if (!m) return;
  wvFrom = document.activeElement; wvView = 'table'; wvErr = '';
  m.style.display = 'flex';
  wvDraw();
  const box = document.getElementById('wvBox'); if (box && box.focus) box.focus();
  if (!wvData) {
    try { const r = await fetch('/config/voice/samples'); wvData = r.ok ? await r.json() : { rows: [], licences: [] }; } catch (e) { wvData = { rows: [], licences: [] }; }
    wvDraw();
  }
}
function wvClose() {
  const m = document.getElementById('wvModal');
  if (!m || m.style.display === 'none') return;
  wvStop();
  m.style.display = 'none'; m.innerHTML = '';
  const back = document.getElementById('wvLink');
  const t = back || (wvFrom && document.body.contains(wvFrom) ? wvFrom : null);
  if (t && t.focus) t.focus();
  wvFrom = null;
}
// "Use this voice": the voice is chosen on the card and closes the pop-up. A voice that is already on the device is simply used (Breeze: its take too); one that is not starts the
// normal set-up on the card: the licence box first (never ticked for the player), the memory and disk notes, then the download with its progress bar.
async function wvUse(n) {
  const r = wvData && wvData.rows[n];
  if (!r || typeof vcOv === 'undefined' || !vcOv) return;
  if (r.take && wvBlock()) return;          // a take on a Courier too old to change it: nothing is sent
  wvClose();
  const k = vcShownKey(), take = r.take && r.take !== 'standard' ? r.take : '';
  if (vcInstalledModels(k).includes(r.model)) {
    wvPending = null; wvAuto = null;
    if (vcActiveModel(k) !== r.model) await vcSwitchRun({ kind: 'model', k, model: r.model }, () => k === 'this' ? lmPost('/config/voice/model', { model: r.model }) : lmPost('/config/voice/pc/select', { id: k, model: r.model }));
    else vcModelPick = null;
    if (r.model === 'breeze' && r.take) { const i = vcInfo(k); if (i && i.take !== r.take) vcChooseTake(k, r.take); }
  } else {
    wvPending = take ? { k, take } : null; wvAuto = { k, model: r.model };
    vcChooseModel(r.model);
  }
  const b = document.getElementById('vcBody'); if (b && b.scrollIntoView) b.scrollIntoView({ block: 'start' });
}
// After each redraw of the card: a voice picked here whose licence was accepted before and that has nothing to warn about goes straight on to the download; Breeze's take
// that was asked for is chosen once Breeze is installed.
function wvAfterRender() {
  if (typeof vcOv === 'undefined' || !vcOv) return;
  if (wvPending && vcInstalledModels(wvPending.k).includes('breeze') && vcActiveModel(wvPending.k) === 'breeze') {
    const p = wvPending; wvPending = null; const i = vcInfo(p.k);
    if (i && i.installed && i.take !== p.take) vcChooseTake(p.k, p.take);
  }
  if (!wvAuto) return;
  const a = wvAuto, i = vcInfo(a.k);
  if (!i || vcInstalledModels(a.k).includes(a.model) || i.job) { wvAuto = null; return; }
  if (vcModelOf(a.k) !== a.model) { wvAuto = null; return; }
  if (!i.licenseOk) { wvAuto = null; return; }           // the licence is the player's to tick first
  const g = vcGate(a.k, i);
  if (g.busy) return;                                  // the fit check is still running: look again at the next redraw
  wvAuto = null;
  if (g.ok && !g.notes) vcNext(a.k);
}
document.addEventListener('keydown', e => {
  const m = document.getElementById('wvModal');
  if (!m || m.style.display === 'none') return;
  if (e.key === 'Escape') { e.preventDefault(); wvClose(); return; }
  if (e.key === 'Tab') {
    const els = Array.from(m.querySelectorAll('a[href], button'));
    if (!els.length) return;
    const first = els[0], last = els[els.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === document.getElementById('wvBox'))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
"""

JS = "const WV_WORDS = " + json.dumps(WORDS) + ";\n" + JS
