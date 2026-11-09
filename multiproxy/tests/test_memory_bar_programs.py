"""The Local device tab's Graphics memory bar names the programs using the card when you point at its colours. Fake readings only: nothing here reads a real card."""
import asyncio
import json
import shutil
import subprocess

import pytest

import local_models_ui

NODE = shutil.which("node")
JS = local_models_ui.LOCAL_MODELS_JS
START = JS.index("let memPick = 'this';")
END = JS.index("function memRender(d)")
CODE = JS[START:END]

PRELUDE = r"""
const elements = {}; let fetched = [];
const document = { getElementById: id => elements[id] || (id === 'paneLocal' ? { style: { display: globalThis.tabOpen === false ? 'none' : '' } } : null), hidden: false, querySelector: () => null };
const window = {};
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
globalThis.answer = { available: true, programs: [] };
async function fetch(url) { fetched.push(url); return { json: async () => globalThis.answer }; }
function setTimeout() { return 1; } function clearTimeout() {}
function lmPost() {} function lmRefresh() {} function memRender() {}
"""


def run_js(snippet: str, tmp_path):
    f = tmp_path / "t.js"
    f.write_text(PRELUDE + CODE + "\n(async () => { const OUT = {};\n" + snippet + "\nconsole.log(JSON.stringify(OUT)); })().catch(e => { console.error(e.stack); process.exit(1); });", encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


pytestmark_js = pytest.mark.skipif(NODE is None, reason="Node isn't installed")
PROGS = "[{name:'Firefox',mb:1229},{name:'Windows desktop',mb:922},{name:'Obs64',mb:300},{name:'Steam',mb:120},{name:'Discord',mb:100},{name:'Code',mb:60},{name:'Zed',mb:40}]"


@pytestmark_js
def test_everything_else_lists_the_top_five_biggest_first_and_n_more(tmp_path):
    out = run_js("memPrograms = %s; OUT.t = memTipText({key:'other', label:'Everything else', mb:2800}, {peakMb: 1}, true);" % PROGS, tmp_path)
    assert out["t"].split("\n") == ["Using the card now", "Firefox 1.2 GB", "Windows desktop 0.9 GB", "Obs64 0.3 GB", "Steam 0.1 GB", "Discord 0.1 GB", "2 more 0.1 GB"]


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_skyrim_is_left_out_of_the_list_when_it_has_its_own_segment_and_shows_now_and_highest(tmp_path):
    out = run_js("""memPrograms = [{name:'Skyrim',mb:6246},{name:'Firefox',mb:1229}];
      OUT.a = memTipText({key:'other', label:'Everything else', mb:1229}, {}, true); OUT.b = memTipText({key:'other', label:'Everything else', mb:1229}, {}, false);
      OUT.s = memTipText({key:'skyrim', label:'Skyrim', mb:28672}, {peakMb: 28979}, true); OUT.f = memTipText({key:'free'}, {}, true);""", tmp_path)
    assert "Skyrim" not in out["a"] and "Firefox 1.2 GB" in out["a"] and "Skyrim 6.1 GB" in out["b"]
    assert out["s"] == "Skyrim 28.0 GB now, highest 28.3 GB" and out["f"] == "Free: what is left for models."


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_no_reading_means_no_list_and_the_old_hover(tmp_path):
    out = run_js("""memPrograms = null; OUT.none = memTipText({key:'other', label:'Everything else', mb:2355}, {}, true);
      memPrograms = []; OUT.empty = memTipText({key:'other', label:'Everything else', mb:2355}, {}, true);
      memPrograms = [{name:'Skyrim',mb:100}]; OUT.onlySkyrim = memTipText({key:'other', label:'Everything else', mb:2355}, {}, true);
      memPick = 'laptop'; memPrograms = %s; OUT.other = memTipText({key:'other', label:'Everything else', mb:2355}, {}, true);""" % PROGS, tmp_path)
    assert out["none"] == out["empty"] == out["onlySkyrim"] == out["other"] == "Everything else 2.3 GB"


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_the_page_reads_at_most_every_5_seconds_only_while_the_tab_is_open_and_only_for_this_pc(tmp_path):
    out = run_js("""
      await memProgramsMaybe(); await memProgramsMaybe(); await memProgramsMaybe();      // three refreshes in a row: one read
      OUT.first = fetched.length;
      memProgramsAt = Date.now() - 6000; await memProgramsMaybe(); OUT.later = fetched.length;
      globalThis.tabOpen = false; memProgramsAt = 0; await memProgramsMaybe(); OUT.closed = fetched.length;
      globalThis.tabOpen = true; memPick = 'laptop'; await memProgramsMaybe(); OUT.other = fetched.length;
      OUT.url = fetched[0];""", tmp_path)
    assert out["first"] == 1 and out["later"] == 2 and out["closed"] == 2 and out["other"] == 2
    assert out["url"].endswith("/memory/programs?pc=this")
    assert "memProgramsMaybe();" in JS[JS.index("async function memRefresh()"):JS.index("// Who is using the card")]        # asked from the live refresh, which stops when the tab is closed


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_the_bar_and_its_legend_carry_the_text_and_a_tap_opens_it(tmp_path):
    out = run_js("""memPrograms = %s; memPick = 'this';
      const d = {totalMb: 32768, freeMb: 1500, otherMb: 2800, peakMb: 28979, segments: [{key:'skyrim', label:'Skyrim', mb: 28672}]};
      OUT.h = memBar(d, true);
      elements.memTip = {textContent: ''}; memTip(1); OUT.open = elements.memTip.textContent; memTip(1); OUT.closed = elements.memTip.textContent;""" % PROGS, tmp_path)
    h = out["h"]
    assert h.count("Using the card now") == 2                               # on the colour AND on its legend entry
    assert h.count("Skyrim 28.0 GB now, highest 28.3 GB") == 2 and h.count("Free: what is left for models.") == 2
    assert out["open"].startswith("Using the card now") and out["closed"] == ""
    assert 'id="memTip"' in h and "Everything else <b>2.7 GB</b>" in h       # the words that were there stay


def test_the_route_reads_once_per_5_seconds_gives_nothing_for_another_device_and_nothing_when_unreadable(monkeypatch):
    import multiproxy
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(multiproxy.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(multiproxy, "_card_programs_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(multiproxy, "_read_card_programs", lambda: calls.append(1) or {"available": True, "programs": [{"name": "Firefox", "mb": 1229, "shared_mb": 0}]})
    run = lambda pc="this": asyncio.run(multiproxy.local_models_memory_programs(pc))
    assert run()["programs"] == [{"name": "Firefox", "mb": 1229}]
    clock[0] += 3
    run(); run()
    assert len(calls) == 1                                                  # inside 5 seconds: the last reading
    clock[0] += 3
    run()
    assert len(calls) == 2
    assert run("laptop") == {"available": False} and len(calls) == 2         # another device: no list, no read
    monkeypatch.setattr(multiproxy, "_read_card_programs", lambda: {"available": False})
    clock[0] += 10
    assert run() == {"available": False, "programs": []}
