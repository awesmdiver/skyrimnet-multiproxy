"""The Local models tab's settings screens (local_settings_ui.py): the exact words of the mockup, each state, the greyed-out controls and their reasons, and the Save paths.

The page builders are pure functions, so this runs them in Node (no browser): a script is built from the real JS, fed an API-shaped state, and prints the HTML each builder
makes. The Save paths run the real functions against a stub `fetch` that records what the page sends. What the page looks like was checked for real on a scratch copy
(see the handoff); what it SAYS and DOES is pinned here.
"""
import json
import re
import shutil
import subprocess
import textwrap

import pytest

import local_engine_flags as flags
import local_settings_ui as ui

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")


def state(**over):
    st = {
        "device": "local", "deviceName": "msi", "build": "b11361", "saved": True, "hash": "abc", "sync": {"needed": False, "sent": False, "online": None},
        "settings": {"layers": "auto", "fitTargetMb": "auto", "ctx": 32768, "parallel": 2, "flashAttn": "auto", "cacheType": "f16", "extraArgs": [], "preset": "suggested"},
        "hardware": {"name": "NVIDIA GeForce RTX 4090", "vendor": "nvidia", "cardCount": 1, "memoryMb": 24564, "memoryGb": 24.0, "experimental": False},
        "hasGame": True, "skyrimPeakMb": 3686, "pageTokens": 680, "fitTarget": {"mb": 4710, "source": "your game's busiest point", "ignored": False},
        "presets": [
            {"id": "suggested", "label": "Suggested for this device", "blurb": "Chosen for this graphics card. Fast, and leaves room for the game.", "settings": {"layers": "auto", "ctx": 32768}, "enabled": True, "experimental": False, "reason": ""},
            {"id": "fastest", "label": "Fastest replies", "blurb": "One request at a time, as fast as it can go.", "settings": {"layers": "auto", "ctx": 16384}, "enabled": True, "experimental": False, "reason": ""},
            {"id": "share", "label": "Share the card", "blurb": "Leaves more graphics memory free for the game.", "settings": {"layers": "auto", "ctx": 32768}, "enabled": True, "experimental": False, "reason": ""},
            {"id": "custom", "label": "Custom", "blurb": "You changed something below.", "settings": None, "enabled": True, "experimental": False, "reason": ""}],
        "checks": [], "reset": [],
        "models": [{"id": "kev-4b", "name": "Kev 4B", "override": {}, "overridden": [], "effective": {}, "checks": [], "estimate": {}},
                   {"id": "gemma-4-e4b", "name": "Gemma 4 E4B", "override": {"ctx": 16384, "parallel": 2}, "overridden": ["ctx", "parallel"], "effective": {}, "checks": [], "estimate": {}}],
        "estimate": {"model": "Kev 4B", "totalMb": 8192, "cardMb": 24564, "summary": "about 8 of 24 GB", "exactNote": "The conversation cache is exact; the rest is an estimate.", "checked": False},
    }
    st.update(over)
    return st


DRAFT = {"layers": "auto", "fitTargetMb": "auto", "ctx": 32768, "parallel": 2, "flashAttn": "auto", "cacheType": "f16", "extraArgs": []}
EST = {"checks": [], "preset": "suggested", "command": "llama-server -m a.gguf", "totalMb": 8192, "cardMb": 24564, "summary": "about 8 of 24 GB", "exactNote": "x", "checked": False}

HARNESS = r"""
const vm = require('vm');
const ctx = { console, JSON, Math, Object, Array, parseInt, parseFloat, clearTimeout, setTimeout: () => 0, jsq: s => String(s == null ? '' : s).replace(/[\']/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'), esc: s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') };
vm.createContext(ctx);
const out = {};
const run = (code) => vm.runInContext(code, ctx);
run(process.env.LS_JS);
const cases = JSON.parse(process.env.LS_CASES);
for (const [name, code] of Object.entries(cases)) { try { out[name] = run(code); } catch (e) { out[name] = 'ERROR ' + e.message; } }
console.log(JSON.stringify(out));
"""


def node_run(cases: dict, js=None) -> dict:
    import os
    env = dict(os.environ, LS_JS=js or ui.LOCAL_SETTINGS_JS, LS_CASES=json.dumps(cases))
    r = subprocess.run([NODE, "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == 0, r.stderr[:1500]
    return json.loads(r.stdout)


def S(**kw):
    """JS source of a render-input object."""
    base = {"state": state(), "draft": DRAFT, "est": EST, "preset": "suggested", "raw": "", "parse": None, "undo": None, "dirty": False, "note": "", "restartOffer": False, "err": None,
            "checking": False, "open": {}, "modelChecks": {}}
    base.update(kw)
    return json.dumps(base)


def device(**kw):
    return node_run({"h": f"lsDeviceHtml({S(**kw)})"})["h"]


@needs_node
class TestTheWordsOfTheMockup:
    def test_the_device_card(self):
        h = device()
        for words in ("This device", "msi", "NVIDIA GeForce RTX 4090", "24 GB graphics memory", "Reset to suggested",
                      "These settings apply to every local model on this device, unless you change them for a specific model below.",
                      "Suggested for this device", "Chosen for this graphics card. Fast, and leaves room for the game.", "Fastest replies", "One request at a time, as fast as it can go.",
                      "Share the card", "Leaves more graphics memory free for the game.", "Custom", "You changed something below.",
                      "Graphics card layers", "--n-gpu-layers", "Automatic fits the model into your free space.",
                      "Automatic (recommended)", "All", "Pick a number", "None (processor only)",
                      "Memory to keep free for the game", "--fit-target", "Fit in graphics memory", "Leaves room for Skyrim (it peaked at 3.6 GB). Needs Automatic layers.",
                      "Memory per conversation", "--ctx-size", "tokens (about 48 pages)", "Shared: each of the 2 requests gets about 24 pages.",
                      "Requests at the same time", "--parallel", "1 is fastest. More lets several NPCs talk at once.",
                      "Speed boost for long prompts", "--flash-attn", "Usually faster and lighter.",
                      "Auto (recommended)", "On", "Off (only with the full-size cache)",
                      "Smaller cache", "--cache-type-k / -v", "q8_0 halves the cache memory with almost no loss in quality.",
                      "q8_0 (recommended)", "f16 (full size)", "q4_0 (smallest)",
                      "Graphics memory with your busiest model (estimate)", "about 8 of 24 GB", "(the cache is exact, the rest is an estimate)",
                      "Takes effect the next time a model starts.", "Save", "Save and restart running models"):
            assert words in h, words

    def test_batch_sizes_and_cache_reuse_are_not_on_the_main_card(self):
        h = device()
        for gone in ("Batch sizes", "--batch-size", "--ubatch-size", "Reuse cache", "--cache-reuse"):
            assert gone not in h, gone

    def test_the_advanced_card(self):
        h = node_run({"h": f"lsAdvancedHtml({S()})"})["h"]
        for words in ("Advanced: your own flags", "this device",
                      "If the boxes above don't cover what you need, pass extra commands to llama-server here. You can also paste a command line from someone else. MultiProxy uses your exact text.",
                      "Paste llama-server flags here to fill the boxes above.", "The command MultiProxy will run", "Copy command", "Clear", "Save", "Save and restart running models",
                      "Takes effect the next time a model starts."):
            assert words in h, words

    def test_the_model_panel(self):
        st = state()
        h = node_run({"h": f"lsModelPanelHtml({S(open={'gemma-4-e4b': {'ctx': 16000, 'parallel': 2}})}, {json.dumps(st['models'][1])})"})["h"]
        for words in ("Gemma 4 E4B", "2 settings changed for this model", "Only what really depends on this model. Everything else uses this device's settings.",
                      "Memory per conversation", "How much text it can hold in mind at once. Bigger uses more graphics memory. (This device's default: about 48 pages)",
                      "tokens, about 24 pages of text", "Shared by the 2 requests at the same time: each gets about 12 pages.",
                      "Requests at the same time", "This model answers up to 2 at a time. (This device's default: 2)",
                      "Everything else uses this device's settings.", "Use this device's settings", "Save and restart this model"):
            assert words in h.replace("&#39;", "'") or words in h, words

    def test_the_decision_engine_card_moved_to_the_decisions_tab(self):
        import decision_ui
        assert "lsDecisionHtml" not in ui.LOCAL_SETTINGS_JS and "Managed by MultiProxy" not in ui.LOCAL_SETTINGS_JS
        assert "The decision models have a few settings that must stay exactly as MultiProxy sets them" in decision_ui.DECISION_JS

    def test_the_engine_did_not_start_card(self):
        f = {"failed": True, "id": "kev-4b", "name": "Kev 4B", "message": "m", "engineLines": ["error: invalid value for --cache-type-v: iq9_9"], "usedExtraFlags": True, "canStartWithoutExtras": True, "cameFromExtraFlags": True}
        h = node_run({"h": f"lsFailHtml({json.dumps(f)})", "plain": f"lsFailHtml({json.dumps(dict(f, usedExtraFlags=False, canStartWithoutExtras=False, cameFromExtraFlags=False, engineLines=[]))})"})
        for words in ("Kev 4B did not start.", "llama-server said:", "error: invalid value for --cache-type-v: iq9_9", "Your extra flags caused this error.", "Start without the extra flags", "Edit the flags"):
            assert words in h["h"], words
        assert "Start without" not in h["plain"] and "That came from" not in h["plain"] and "Edit the flags" not in h["plain"], "no extra flags in that start: no such choices"
        assert "m" in h["plain"]


@needs_node
class TestTheStates:
    def test_the_selected_preset_and_custom(self):
        assert 'ls-preset sel"' in device(preset="suggested").split("Fastest replies")[0]
        h = device(preset="custom")
        assert h.index("ls-preset sel") > h.index("Share the card")

    def test_a_device_still_on_the_standard_settings_selects_no_preset(self):
        assert "ls-preset sel" not in device(preset="default")

    def test_other_graphics_setups_are_experimental_with_no_suggestions(self):
        st = state(hardware=dict(state()["hardware"], experimental=True), checks=[{"level": "info", "code": "experimental-hardware", "text": "This device has an AMD graphics card. MultiProxy's memory suggestions were only tested on one NVIDIA card, so it starts with the safe settings.", "experimental": True}],
                   presets=[dict(p, enabled=p["id"] in ("suggested", "custom"), reason="" if p["id"] in ("suggested", "custom") else "Memory suggestions are only built for one NVIDIA card so far.") for p in state()["presets"]])
        h = device(state=st)
        assert ">Experimental<" in h and "only tested on one NVIDIA card" in h.replace("&#39;", "'")
        assert h.count("Memory suggestions are only built for one NVIDIA card so far.") == 2
        assert h.count("ls-preset ls-dim") == 2 and h.count(" disabled>") >= 2 and "lsPickPreset('fastest')" not in h

    def test_waiting_to_send_on_a_device_that_is_offline(self):
        h = device(state=state(sync={"needed": True, "sent": False, "online": False}))
        assert "Waiting to send" in h and "This device is offline." in h
        assert "Waiting to send" not in device(state=state(sync={"needed": True, "sent": True, "online": True}))
        assert "Waiting to send" not in device()
        assert "Waiting to send" in device(state=state(sync={"needed": True, "sent": False, "online": True})) and "offline" not in device(state=state(sync={"needed": True, "sent": False, "online": True}))

    def test_memory_to_keep_free_is_greyed_out_with_its_reason_unless_layers_are_automatic(self):
        grey = {"level": "info", "code": "fit-target-ignored", "text": "Memory to keep free only works when graphics card layers is on Automatic.", "field": "fitTargetMb", "disabled": True}
        h = device(draft=dict(DRAFT, layers="all"), est=dict(EST, checks=[grey]))
        assert "ls-f ls-dim" in h and 'id="lsFit" min="0"' in h and "disabled>" in h.split('id="lsFit"')[1].split(">")[0] + ">"
        assert "Not used while a layer count is picked." in h.split('id="lsFitWhy"')[1][:100]
        on = device()
        assert "ls-dim" not in on.split("Memory per conversation")[0].split("Memory to keep free")[1] and " disabled" not in on.split('id="lsFit"')[1].split(">")[0]

    def test_the_worked_out_default_and_what_it_came_from(self):
        assert "Leaves room for Skyrim (it peaked at 3.6 GB)." in device()
        assert "a guess until the game has been measured" in device(state=state(skyrimPeakMb=None))
        assert "No game here, so it keeps 1 GB." in device(state=state(hasGame=False, skyrimPeakMb=None))
        h = device(draft=dict(DRAFT, fitTargetMb=2048))
        assert "Set by you. Needs Automatic layers." in h and "Use the worked-out value" in h and 'value="2"' in h

    def test_off_is_not_allowed_for_flash_attention_while_the_smaller_cache_is_chosen(self):
        off = device(draft=dict(DRAFT, cacheType="q8_0")).split('id="lsFlash"')[1].split("</select>")[0]
        assert 'value="off" disabled' in off
        assert 'value="off" disabled' not in device().split('id="lsFlash"')[1].split("</select>")[0]

    def test_a_picked_layer_count_gets_its_own_box(self):
        h = device(draft=dict(DRAFT, layers=30))
        assert 'id="lsLayersNum"' in h and 'value="30"' in h and '<option value="number" selected>' in h
        assert "lsLayersNum" not in device()

    def test_the_memory_bar(self):
        assert "var(--ok)" in device() and "width:33%" in device()
        over = device(est=dict(EST, totalMb=26624, cardMb=24564, summary="about 26 of 24 GB"))
        assert "var(--warn)" in over and "width:100%" in over and "about 26 of 24 GB" in over
        assert "Checking memory..." in device(checking=True) and "about 8 of 24 GB" not in device(checking=True)
        assert "Checked with the engine's own memory tool." in device(est=dict(EST, checked=True, exactNote="Checked with the engine's own memory tool. Still an estimate.")).replace("&#39;", "'")
        assert "Add a model to see this." in device(est=dict(EST, totalMb=None, summary=""))

    def test_messages_are_as_the_checks_module_gave_them(self):
        checks = [{"level": "error", "code": "per-request-floor", "text": "Each request gets only 2,730 tokens. Raise it."},
                  {"level": "warning", "code": "memory-too-big", "text": "These settings need about 26 of 24 GB of graphics memory. They probably won't fit.", "strong": True},
                  {"level": "warning", "code": "per-request-low", "text": "Each request gets 8,192 tokens."},
                  {"level": "info", "code": "total-grows", "text": "3 requests don't divide it."},
                  {"level": "info", "code": "fit-target-ignored", "text": "HIDDEN"}, {"level": "info", "code": "extra-flags-untested", "text": "HIDDEN2"}]
        h = device(est=dict(EST, checks=checks)).replace("&#39;", "'")
        assert "🛑 Each request gets only 2,730 tokens. Raise it." in h and "ls-callout err" in h
        assert "⚠️ <b>These settings need about 26 of 24 GB of graphics memory. They probably won't fit.</b>" in h and "⚠️ Each request gets 8,192 tokens." in h
        assert "3 requests don't divide it." in h and "HIDDEN2" not in h and "HIDDEN" not in h.split('id="lsMsgs"')[1]

    def test_saved_says_so_and_offers_the_restart(self):
        h = device(note="Saved. The new settings apply the next time a model starts.", restartOffer=True)
        assert "Saved. The new settings apply the next time a model starts." in h and "Restart running models now" in h and 'id="lsApplies">Saved.<' in h
        assert "Restart running models now" not in device(note="Saved. Running models are restarting with them.", restartOffer=False)

    def test_a_refused_save_shows_the_reason_and_keeps_the_boxes(self):
        h = device(err={"text": "The smaller cache needs flash attention. Set it to Auto or On.", "checks": []}, dirty=True)
        assert "🛑 The smaller cache needs flash attention." in h and 'id="lsSave" onclick="lsSave(\'save\')">Save<' in h.replace("&#39;", "'").replace(' disabled', '') or "lsSave" in h

    def test_save_is_greyed_out_until_something_changed(self):
        assert 'id="lsSave" onclick="lsSave(\'save\')" disabled' in device().replace("&#39;", "'")
        assert " disabled" not in device(dirty=True).split('id="lsSave"')[1].split(">")[0]

    def test_what_was_reset_after_an_engine_update_is_listed(self):
        h = device(state=state(reset=[{"was": "--no-mmap", "reason": "No longer allowed."}]))
        assert "The engine was updated, so 1 of your settings went back to its default" in h and "--no-mmap (No longer allowed)" in h
        two = device(state=state(reset=[{"was": "a", "reason": "x."}, {"was": "b", "reason": "y."}]))
        assert "2 of your settings went back to their defaults" in two
        assert "The engine was updated" not in device()

    def test_the_dot_is_a_status_never_a_decoration(self):
        assert 'class="ls-dot "' in device()
        assert 'ls-dot warn' in device(est=dict(EST, checks=[{"level": "warning", "code": "x", "text": "t"}]))
        assert 'ls-dot err' in device(est=dict(EST, checks=[{"level": "error", "code": "x", "text": "t"}]))

    def test_everything_from_the_server_is_escaped(self):
        evil = '<img src=x onerror=alert(1)>'
        h = device(state=state(deviceName=evil, hardware=dict(state()["hardware"], name=evil)), est=dict(EST, checks=[{"level": "warning", "code": "x", "text": evil}]))
        assert "<img" not in h and "&lt;img" in h
        assert "<img" not in node_run({"h": f"lsModelTag('x'); lsState = {json.dumps(state())}; lsModelTag('kev-4b')"})["h"]


@needs_node
class TestPerModelTags:
    def test_the_tag_and_the_button_on_a_models_row(self):
        r = node_run({"set": f"lsState = {json.dumps(state())}", "a": "lsModelTag('kev-4b')", "b": "lsModelTag('gemma-4-e4b')", "c": "lsModelTag('nope')"})
        assert "Uses this device's settings".replace("'", "&#39;") in r["a"] or "Uses this device's settings" in r["a"]
        assert "Change for this model" in r["a"] and "2 settings changed for this model" in r["b"] and r["c"] == ""
        one = node_run({"set": f"lsState = {json.dumps(state(models=[dict(state()['models'][1], overridden=['ctx'])]))}", "a": "lsModelTag('gemma-4-e4b')"})["a"]
        assert "1 setting changed for this model" in one

    def test_before_anything_is_loaded_there_is_no_tag(self):
        assert node_run({"a": "lsModelTag('kev-4b')"})["a"] == ""


@needs_node
class TestAdvancedAnswers:
    def parse(self, text):
        return flags.parse_command_line(text).to_json()

    def test_what_a_pasted_line_made_of_it(self):
        p = self.parse("--n-gpu-layers 99 --ctx-size 32000 --parallel 1 --batch-size 512 --ubatch-size 512 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on --cache-reuse 0 "
                       "--host 127.0.0.1 --port 8602 -m D:\\models\\Kev-4B.gguf --threads 8 --rope-scaling yarn --no-mmap")
        h = node_run({"h": f"lsParseHtml({json.dumps(p)}, true)"})["h"]
        assert "Understood and filled in above:" in h and "graphics card layers, memory per conversation, requests at the same time, flash attention, smaller cache." in h and ">Undo<" in h
        assert "Kept as extra flags (MultiProxy has not tested these):" in h and "<code>--threads 8</code> · <code>--rope-scaling yarn</code>" in h and "They are passed to llama-server as they are." in h
        assert "Not used (MultiProxy sets these itself):" in h and "<code>--host</code>, <code>--port</code>, <code>-m</code>" in h
        assert "Not available in this version of llama.cpp:" in h and "<code>--no-mmap</code> (the engine now spells this differently; use <code>-lm none</code> instead)" in h
        assert "⚠️" in h

    def test_flags_that_are_not_allowed_say_why_and_there_is_no_undo_without_anything_filled_in(self):
        h = node_run({"h": f"lsParseHtml({json.dumps(self.parse('--api-key abc -np 0 --threads 4'))}, false)"})["h"]
        assert "Not allowed:" in h and "that would open a way into or out of this device" in h.lower() and "Not understood:" in h and "That needs a whole number from 1 up".lower() in h.lower()
        assert ">Undo<" not in h and "Understood and filled in above" not in h

    def test_nothing_pasted_nothing_shown(self):
        assert node_run({"h": "lsParseHtml(null, false)"})["h"] == ""

    def test_the_command_box_and_the_textarea_keep_what_was_typed(self):
        h = node_run({"h": f"lsAdvancedHtml({S(raw='--threads 8', est=dict(EST, command='llama-server -m a.gguf --threads 8'))})"})["h"]
        assert ">--threads 8</textarea>" in h and "llama-server -m a.gguf --threads 8" in h

    def test_flag_lines_for_the_box(self):
        r = node_run({"a": "lsFlagLines(['--batch-size','512','--ubatch-size','512','--threads','8'])", "b": "lsFlagLines([])", "c": "lsFlagLines(['-ngl','-1'])"})
        assert r["a"] == "--batch-size 512\n--ubatch-size 512\n--threads 8" and r["b"] == "" and r["c"] == "-ngl -1"


STUB = r"""
const calls = [];
const el = () => ({ innerHTML: '', textContent: '', value: '', disabled: false, classList: { toggle() {} }, options: [], scrollIntoView() {}, focus() {}, replaceWith() {}, setSelectionRange() {} });
const reply = (url, opts) => {
  calls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  let data = {};
  if (url.indexOf('/settings?device=') >= 0) data = STATE;
  else if (url.indexOf('/settings/estimate') >= 0) data = { checks: [], preset: 'custom', command: 'cmd', totalMb: 1000, cardMb: 24000, summary: 'about 1 of 23 GB', exactNote: 'x', checked: false };
  else if (url.indexOf('/failure') >= 0) data = { failed: false };
  else if (url.indexOf('/decisions/status') >= 0) data = { installedModels: [] };
  else if (opts && opts.method === 'POST') data = SAVE_REPLY;
  return Promise.resolve({ ok: !(opts && opts.method === 'POST' && SAVE_STATUS >= 400), status: SAVE_STATUS, json: () => Promise.resolve(data) });
};
"""


def save_paths(code: str, st=None, save_status=200, save_reply=None):
    """Run `code` (async JS) against the real page functions and a stub `fetch`; -> the list of requests the page sent and what `code` returned."""
    prelude = f"var STATE = {json.dumps(st or state())}; var SAVE_STATUS = {save_status}; var SAVE_REPLY = {json.dumps(save_reply or {'saved': True, 'needsMemoryCheck': False})};" + STUB
    harness = textwrap.dedent(r"""
        const vm = require('vm');
        const ctx = { console, JSON, Math, Object, Array, Promise, parseInt, parseFloat, clearTimeout, setTimeout: () => 0, jsq: s => String(s == null ? '' : s).replace(/[\']/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'), esc: s => String(s == null ? '' : s) };
        ctx.fetch = null; ctx.document = { getElementById: () => null, activeElement: null };
        vm.createContext(ctx);
        vm.runInContext(process.env.LS_PRELUDE, ctx);
        ctx.fetch = (u, o) => vm.runInContext('reply', ctx)(u, o);
        vm.runInContext(process.env.LS_JS, ctx);
        (async () => {
          const result = await vm.runInContext('(async () => {' + process.env.LS_CODE + '})()', ctx);
          console.log(JSON.stringify({ calls: vm.runInContext('calls', ctx), result }));
        })().catch(e => { console.error(e); process.exit(1); });
    """)
    import os
    env = dict(os.environ, LS_JS=ui.LOCAL_SETTINGS_JS, LS_PRELUDE=prelude, LS_CODE=code)
    r = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == 0, r.stderr[:1500]
    return json.loads(r.stdout)


def posts(out):
    return [c for c in out["calls"] if c["body"] is not None]


@needs_node
class TestSavePaths:
    LOAD = "await lsLoad(true); "

    def test_save_sends_the_draft_without_its_preset_and_applies_on_the_next_start(self):
        out = save_paths(self.LOAD + "lsDraft.ctx = 16384; lsDirty = true; await lsSave('save'); return [lsNote, lsDirty];")
        save = [c for c in posts(out) if c["url"].endswith("/config/local-models/settings")][0]["body"]
        assert save["device"] == "local" and save["apply"] == "save" and save["settings"]["ctx"] == 16384 and "preset" not in save["settings"]
        assert out["result"] == ["Saved. The new settings apply the next time a model starts.", False]

    def test_save_and_restart_running_models(self):
        out = save_paths(self.LOAD + "await lsSave('restart-all'); return lsNote;")
        assert [c for c in posts(out) if c["url"].endswith("/config/local-models/settings")][0]["body"]["apply"] == "restart-all"
        assert out["result"] == "Saved. Running models are restarting with them."

    def test_a_refused_save_keeps_what_was_typed_and_shows_why(self):
        out = save_paths(self.LOAD + "lsDraft.cacheType = 'q8_0'; lsDraft.flashAttn = 'off'; lsDirty = true; await lsSave('save'); return [lsErr.text, lsDirty, lsDraft.flashAttn];",
                         save_status=400, save_reply={"detail": "The smaller cache needs flash attention.", "checks": []})
        assert out["result"] == ["The smaller cache needs flash attention.", True, "off"]

    def test_a_custom_or_pasted_setup_gets_the_engines_own_memory_check_after_saving(self):
        out = save_paths(self.LOAD + "await lsSave('save'); return 1;", save_reply={"saved": True, "needsMemoryCheck": True})
        est = [c for c in posts(out) if c["url"].endswith("/estimate")]
        assert any(c["body"].get("confirm") is True for c in est)

    def test_a_standard_setup_does_not(self):
        out = save_paths(self.LOAD + "await lsSave('save'); return 1;", save_reply={"saved": True, "needsMemoryCheck": False})
        assert not any(c["body"].get("confirm") is True for c in posts(out) if c["url"].endswith("/estimate"))

    def test_picking_a_preset_fills_the_boxes_and_marks_it_unsaved(self):
        out = save_paths(self.LOAD + "lsPickPreset('fastest'); return [lsDraft.ctx, lsDirty, 'preset' in lsDraft];")
        assert out["result"] == [16384, True, False]

    def test_a_preset_that_is_off_does_nothing(self):
        st = state(presets=[dict(p, settings=None) if p["id"] == "fastest" else p for p in state()["presets"]])
        out = save_paths(self.LOAD + "lsPickPreset('fastest'); return [lsDraft.ctx, lsDirty];", st=st)
        assert out["result"] == [32768, False]

    def test_only_what_differs_from_the_device_is_a_models_override(self):
        out = save_paths(self.LOAD + "lsOpenModel('kev-4b'); lsSetModel('kev-4b', 'parallel', '1'); lsSetModel('kev-4b', 'ctx', '32768'); await lsSaveModel('kev-4b', 'save'); return 1;")
        body = [c for c in posts(out) if c["url"].endswith("/config/local-models/settings") and c["body"].get("models")][0]["body"]
        assert body["models"] == {"kev-4b": {"parallel": 1}} and body["apply"] == "save" and "settings" not in body

    def test_a_model_that_ends_up_like_the_device_follows_the_device_again(self):
        out = save_paths(self.LOAD + "lsOpenModel('gemma-4-e4b'); lsSetModel('gemma-4-e4b', 'ctx', '32768'); lsSetModel('gemma-4-e4b', 'parallel', '2'); await lsSaveModel('gemma-4-e4b', 'restart-model'); return 1;")
        body = [c for c in posts(out) if c["url"].endswith("/config/local-models/settings") and c["body"].get("models")][0]["body"]
        assert body["models"] == {"gemma-4-e4b": None} and body["apply"] == "restart-model" and body["modelId"] == "gemma-4-e4b"

    def test_use_this_devices_settings_clears_the_override(self):
        out = save_paths(self.LOAD + "lsOpenModel('gemma-4-e4b'); await lsUseDevice('gemma-4-e4b'); return Object.keys(lsOpen);")
        body = [c for c in posts(out) if c["url"].endswith("/config/local-models/settings") and c["body"].get("models")][0]["body"]
        assert body["models"] == {"gemma-4-e4b": None} and out["result"] == []

    def test_start_without_the_extra_flags(self):
        out = save_paths(self.LOAD + "lsFails['kev-4b'] = { failed: true }; await lsStartWithout('kev-4b'); return Object.keys(lsFails);")
        assert [c for c in posts(out) if c["url"].endswith("/start-without-extras")][0]["body"] == {"id": "kev-4b"} and out["result"] == []

    def test_editing_a_box_asks_for_a_fresh_estimate_and_marks_it_unsaved(self):
        out = save_paths(self.LOAD + "lsSetNum('parallel', '1'); lsSetSel('cacheType', 'q8_0'); return [lsDraft.parallel, lsDraft.cacheType, lsDirty];")
        assert out["result"] == [1, "q8_0", True]

    def test_choosing_the_smaller_cache_moves_flash_attention_off_off(self):
        out = save_paths(self.LOAD + "lsDraft.flashAttn = 'off'; lsSetSel('cacheType', 'q4_0'); return lsDraft.flashAttn;")
        assert out["result"] == "auto"

    def test_memory_to_keep_free_in_gb_is_sent_as_megabytes_and_can_go_back_to_worked_out(self):
        out = save_paths(self.LOAD + "lsSetFit('2.5'); const a = lsDraft.fitTargetMb; lsFitAuto(); return [a, lsDraft.fitTargetMb];")
        assert out["result"] == [2560, "auto"]

    def test_a_pasted_line_fills_the_boxes_and_can_be_undone(self):
        parse = flags.parse_command_line("--ctx-size 16000 --threads 8").to_json()
        out = save_paths(self.LOAD + f"lsRaw = '--ctx-size 16000 --threads 8'; lsParse = {json.dumps(parse)}; lsUndo = Object.assign({{}}, lsDraft); Object.assign(lsDraft, lsParse.settings); lsDraft.extraArgs = lsParse.extra; "
                         "const filled = [lsDraft.ctx, lsDraft.extraArgs]; lsUndoParse(); return [filled, lsDraft.ctx, lsDraft.extraArgs, lsRaw];")
        assert out["result"] == [[16000, ["--threads", "8"]], 32768, [], ""]


class TestThePageGetsIt:
    def test_the_screens_are_in_the_dashboard(self, test_client):
        html = test_client.get("/").text
        assert 'id="lsMount"' in html and "function lsDeviceHtml" in html and ".ls-card" in html and "lsSync()" in html
        assert "lsModelTag(m.id)" in html

    def test_no_narrow_text_columns_and_only_theme_colours(self):
        css = re.sub(r"@media \(max-width:\d+px\)", "", ui.LOCAL_SETTINGS_CSS).replace("#lsDeviceCard { max-width:1100px }", "")     # the one cap: the whole card (two columns), never a text column
        assert "max-width" not in css
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", ui.LOCAL_SETTINGS_CSS.replace("#60a5fa", "")), "theme variables only"

    def test_the_api_state_has_everything_the_page_reads(self, proxy_module):
        import asyncio
        st = asyncio.run(proxy_module.engine_settings_api.state("local"))
        for key in ("deviceName", "sync", "settings", "hardware", "hasGame", "skyrimPeakMb", "pageTokens", "fitTarget", "presets", "checks", "reset", "models", "estimate"):
            assert key in st, key
        assert {"needed", "sent", "online"} <= set(st["sync"])
        assert st["hardware"].keys() >= {"name", "memoryGb", "memoryMb", "experimental"}


@needs_node
class TestTheTwoColumnLayout:
    def test_the_settings_come_in_the_mockups_pairs(self):
        h = device()
        order = ["Fit in graphics memory", "Graphics card layers", "Requests at the same time", "Memory to keep free for the game", "Memory per conversation", "Smaller cache", "Speed boost for long prompts"]
        at = [h.index(w) for w in order]
        assert at == sorted(at), "layers | requests, then keep free | per conversation, then cache | speed boost"

    def test_the_slider_sits_on_one_line_with_its_number(self):
        h = device().split('id="lsFitBox"')[1].split("</div>")[0]
        assert 'class="ls-ctl ls-one"' in h and 'id="lsFit"' in h and 'id="lsFitRange"' in h

    def test_the_flash_line_says_it_stays_on_with_the_smaller_cache(self):
        assert "Stays on while the smaller cache is used." in device(draft=dict(DRAFT, cacheType="q8_0"))
        assert "Stays on while the smaller cache is used." not in device(draft=dict(DRAFT, cacheType="f16"))

    def test_one_request_gets_all_of_it(self):
        assert "One request gets all of it: about 48 pages." in device(draft=dict(DRAFT, parallel=1))
