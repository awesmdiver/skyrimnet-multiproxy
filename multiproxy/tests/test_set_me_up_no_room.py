"""Set me up asks you to start Skyrim first, and points to another PC when there is no room here (2026-10-26). Stand-in hardware and stand-in paired PCs only."""
import json
import types

import pytest

import set_me_up as sm
from tests.test_set_me_up import GB, NODE, card, facts, run_js


def pc(name="Den PC", gb=24, pid="den-pc"):
    return {"id": pid, "name": name, "card": card(gb) if gb else None}


def no_room_plan(**kw):
    return sm.make_plan(facts(gb=16, skyrim_mb=10 * GB, skyrim_when="today", **kw), True)       # a 16 GB card with 10 GB of game: every chat model says "will not fit"


# ---- what the panel says, per case ----------------------------------------------------------------------------------------------------------
def test_no_room_with_a_paired_pc_names_it_and_the_model_that_fits_there():
    p = no_room_plan(other_pcs=[pc()])
    n = p["noRoom"]
    assert all(o["verdict"] == "no" for o in sm.chat_options(card(16), True, 10 * GB)) and p["chat"] is None
    assert n["warning"] == "There isn't enough VRAM (graphics memory) to run models alongside Skyrim on this PC."
    assert n["steps"] == [{"text": "Den PC is paired. Set up models there. It has room for Gemma 4 26B-A4B.", "button": "Open Other devices", "action": "pc", "id": "den-pc"}] or \
        n["steps"][0]["text"].startswith("Den PC is paired. Set up models there. It has room for Gemma 4")
    assert n["steps"][0]["button"] == "Open Other devices" and n["steps"][0]["id"] == "den-pc" and len(n["steps"]) == 1
    assert p["models"] == [] and sm.WORDS["no_chat"] not in p["notes"]
    assert [i["key"] for i in p["items"]] == ["decision", "engine"]          # the quick-decisions model is still offered as its own row


def test_a_paired_pc_with_an_unknown_card_says_nothing_about_a_model():
    n = no_room_plan(other_pcs=[pc(gb=None)])["noRoom"]
    assert n["steps"][0]["text"] == "Den PC is paired. Set up models there."


def test_a_paired_pc_too_small_for_any_model_says_nothing_about_one():
    n = no_room_plan(other_pcs=[pc(gb=2)])["noRoom"]
    assert n["steps"][0]["text"] == "Den PC is paired. Set up models there."


def test_no_room_and_no_paired_pc_offers_add_a_pc_and_the_cloud():
    n = no_room_plan()["noRoom"]
    assert [(s["text"], s["button"], s["action"]) for s in n["steps"]] == [
        ("Use another PC for the models. Add one here.", "Add a PC", "add"), ("Or use a cloud model.", "Open Providers", "providers")]


def test_a_pc_with_room_never_shows_the_panel():
    for gb, mb in ((32, None), (24, None), (32, 6 * GB), (16, 6 * GB)):
        assert sm.make_plan(facts(gb=gb, skyrim_mb=mb, other_pcs=[pc()]), True)["noRoom"] is None, (gb, mb)
    assert sm.make_plan(facts(gb=4, other_pcs=[pc()]), False)["noRoom"] is None          # no game here: the words about Skyrim do not apply
    assert sm.make_plan(facts(gb=None), True)["noRoom"] is None                         # no card: the old note stays


def test_the_install_path_is_unchanged_when_nothing_is_measured():
    p = sm.make_plan(facts(gb=32), True)
    assert p["noRoom"] is None and p["chat"] == "gemma-4-12b" and len(p["models"]) == 5 and p["canInstall"] and p["game"]["steps"]


@pytest.mark.asyncio
async def test_read_facts_lists_only_paired_couriers_with_their_card():
    class L:
        folder = None

        async def gpu_for(self, mid):
            return None

        def engine_exe_current(self):
            return None

        def installed(self):
            return []
    d = types.SimpleNamespace(installed=lambda key: False, model_key=lambda: "kev-4b")
    pcs = [types.SimpleNamespace(kind="courier", id="a", label="Den PC", gpu={"name": "RTX", "vendor": "nvidia", "memoryGb": 24.0}),
           types.SimpleNamespace(kind="courier", id="b", label="Old PC", gpu=None),
           types.SimpleNamespace(kind="multiproxy", id="c", label="Other MultiProxy", gpu=None)]
    f = await sm.read_facts(L(), d, cards_fn=lambda: [], pcs_fn=lambda: pcs)
    assert [(p["id"], p["name"], p["card"].memory_mb if p["card"] else None) for p in f.other_pcs] == [("a", "Den PC", 24 * GB), ("b", "Old PC", None)]


# ---- the screens ------------------------------------------------------------------------------------------------------------------------------------
FETCH = """
let answers = {}; let calls = [];
function fetch(path, opts) {
  calls.push(path);
  const a = answers[path]; const body = typeof a === 'function' ? a() : a;
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body || {}) });
}
"""


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_not_running_shows_the_steps_and_two_buttons_and_no_table_until_one_is_pressed():
    est = sm.make_plan(facts(gb=32), True)
    measured = sm.make_plan(facts(gb=32, skyrim_mb=14336, skyrim_when="today"), True)
    look = {"question": "Q", "card": {}, "job": None}
    out = run_js(FETCH + f"""
      (async () => {{
        smu.open = true; smu.look = {json.dumps(look)};
        const shots = [];
        answers['/config/set-me-up/measure'] = {{ running: false, measured: false }};
        answers['/config/set-me-up/plan'] = {json.dumps(est)};
        await smuAnswer(true); shots.push(el.innerHTML);                     // Yes, game not running
        await smuMeasure(true); shots.push(el.innerHTML);                    // pressed Measure, still not running
        smuContinue(); shots.push(el.innerHTML);                             // Continue with an estimate
        await smuAnswer(true);                                              // ask again, then measure for real
        answers['/config/set-me-up/measure'] = {{ running: true, measured: true }};
        answers['/config/set-me-up/plan'] = {json.dumps(measured)};
        await smuMeasure(true); shots.push(el.innerHTML);
        smu.skipped = false; await smuAnswer(true); shots.push(el.innerHTML); // running at once: the table straight away
        console.log(JSON.stringify(shots));
      }})();
    """)
    gate, miss, estimate, measured_, at_once = out
    for h in (gate, miss):
        assert "<table" not in h and "Start Skyrim and load a save in a busy place, such as Whiterun." in h and "Come back here." in h and "Press Measure." in h
        assert h.index("Continue with an estimate") < h.index(">Measure<") and "smu-ft" in h
    assert "Couldn&#39;t see Skyrim running" not in gate and "see Skyrim running" in miss
    assert "<table" in estimate and "Estimate" in estimate and "Continue with an estimate" not in estimate and "Install 3 things" in estimate
    assert "<table" in measured_ and "Skyrim is using" in measured_ and "14.0 GB" in measured_ and "Continue with an estimate" not in measured_
    assert "<table" in at_once and "Skyrim is using" in at_once and "Continue with an estimate" not in at_once


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
def test_no_room_panel_draws_the_numbered_steps_with_their_buttons_and_they_go_to_the_right_place():
    paired = no_room_plan(other_pcs=[pc()])
    alone = no_room_plan()
    ok = sm.make_plan(facts(gb=32), True)
    out = run_js(f"""
      const tabs = []; showMainTab = (t) => tabs.push(t);
      const opPcOpen = {{}}; let added = 0; function opAddNew() {{ added++; }} function opGoAdd() {{}} function opRender() {{}}
      smu.open = true; smu.look = {{ question: 'Q', card: {{}}, job: null }}; smu.game = true;
      const shots = [];
      smu.plan = {json.dumps(paired)}; smuDraw(); shots.push(el.innerHTML);
      smuGo(0); const afterPc = [tabs.slice(), JSON.stringify(opPcOpen)];
      smu.open = true; smu.plan = {json.dumps(alone)}; smuDraw(); shots.push(el.innerHTML);
      tabs.length = 0; smuGo(0); const afterAdd = [tabs.slice(), added];
      tabs.length = 0; smu.open = true; smuGo(1); const afterCloud = tabs.slice();
      smu.open = true; smu.plan = {json.dumps(ok)}; smuDraw(); shots.push(el.innerHTML);
      console.log(JSON.stringify([shots, afterPc, afterAdd, afterCloud]));
    """)
    (with_pc, without_pc, fine), after_pc, after_add, after_cloud = out
    assert "There isn&#39;t enough VRAM (graphics memory) to run models alongside Skyrim on this PC." in with_pc and "smu-warn" in with_pc
    assert "Den PC is paired. Set up models there." in with_pc and "Open Other devices" in with_pc and 'type="radio"' not in with_pc and "Quick decisions" in with_pc
    assert "Cloud models handle dialogue better" not in with_pc and "Add a PC" not in with_pc
    assert "Use another PC for the models. Add one here." in without_pc and ">Add a PC<" in without_pc and "Or use a cloud model." in without_pc and ">Open Providers<" in without_pc
    assert after_pc == [["other"], '{"den-pc":true}'] and after_add == [["other"], 1] and after_cloud == ["setup"]
    assert "There isn&#39;t room" not in fine and fine.count('type="radio"') == 5
