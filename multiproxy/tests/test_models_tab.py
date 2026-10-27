"""The Models tab (2026-10-26): every downloaded model on each PC, where it is used, with its actions. Small stand-in data only, no real model files."""
import json
import os
import subprocess
import tempfile

import pytest

import local_models_ui
import models_tab
import models_ui
import overview_ui
from tests.test_overview import ESC, HARNESS, NODE, needs_node

INSTALLED = [
    {"id": "gemma-4-12b", "name": "Gemma 4 12B", "state": "running", "sizeBytes": 7_600_000_000, "custom": False},
    {"id": "qwen", "name": "Qwen 3", "state": "failed", "sizeBytes": 3_000_000_000, "custom": False},
    {"id": "my-own", "name": "my-own-model.gguf", "state": "stopped", "sizeBytes": 4_200_000_000, "custom": True},
]
KEPT = [{"id": "gemma-4-26b", "name": "Gemma 4 26B A4B", "sizeBytes": 18_000_000_000}]
USES = {"local:gemma-4-12b": ["Dialogue", "Actions", "Memory backup"], "local:qwen": ["Dialogue backup"]}
DEC = {"id": "kev-4b", "name": "Kev-4B", "sizeBytes": 3_000_000_000, "state": "running"}
VOICE = {"id": "higgs", "name": "Higgs voice", "sizeBytes": 5_300_000_000, "state": "stopped"}


def by_name(rows):
    return {r["name"]: r for r in rows}


# ---- this PC ----
def test_this_pc_lists_installed_kept_own_decision_and_voice_models_with_where_they_are_used():
    rows = by_name(models_tab.this_pc_rows(INSTALLED, KEPT, USES, DEC, VOICE))
    assert list(rows) == ["Gemma 4 12B", "Qwen 3", "my-own-model.gguf", "Gemma 4 26B A4B", "Kev-4B", "Higgs voice"]
    g = rows["Gemma 4 12B"]
    assert (g["kind"], g["word"], g["sizeText"], g["usedBy"]) == ("model", "Running", "7.6 GB", ["Dialogue", "Actions", "Memory"]), "a backup is the same job"
    assert rows["Qwen 3"]["word"] == "Problem" and rows["Qwen 3"]["usedBy"] == ["Dialogue"]
    assert rows["my-own-model.gguf"]["kind"] == "own" and rows["my-own-model.gguf"]["usedBy"] == [] and rows["my-own-model.gguf"]["word"] == "Stopped"
    k = rows["Gemma 4 26B A4B"]
    assert (k["kind"], k["status"], k["word"], k["sizeText"], k["usedBy"]) == ("kept", "kept", "On this PC", "18.0 GB", [])
    assert rows["Kev-4B"]["usedBy"] == ["Quick decisions"] and rows["Higgs voice"]["usedBy"] == ["Voice"]


def test_a_missing_decision_or_voice_model_leaves_no_row_and_the_totals_add_up():
    rows = models_tab.this_pc_rows(INSTALLED[:1], [], {}, None, None)
    assert [r["name"] for r in rows] == ["Gemma 4 12B"] and rows[0]["usedBy"] == []
    t = models_tab.this_table("Area-51", models_tab.this_pc_rows(INSTALLED, KEPT, USES, None, None), "G:\\Models", 113_800_000_000, "G:")
    assert t["usedBytes"] == 7_600_000_000 + 3_000_000_000 + 4_200_000_000 + 18_000_000_000 and t["freeBytes"] == 113_800_000_000 and t["this"] is True


# ---- another PC ----
def _report(can=True):
    return {"programs": [{"id": "llamacpp", "capabilities": {"load": can, "unload": can}, "models": [
        {"id": "gemma", "name": "Gemma", "loaded": True, "size_bytes": 7_600_000_000},
        {"id": "qwen", "name": "Qwen", "loaded": False, "state": "available", "size_bytes": None}]}],
        "settings": {"models_folder": "D:\\Models"}, "disk": {"path": "D:\\Models", "free_gb": 100.0, "total_gb": 500.0}}


def test_a_paired_pcs_table_comes_from_its_report_with_dashes_for_what_it_did_not_say():
    t = models_tab.remote_table("msi", "MSI", True, _report(), {"pc:msi/gemma": ["Dialogue"]})
    rows = by_name(t["rows"])
    assert rows["Gemma"]["usedBy"] == ["Dialogue"] and rows["Gemma"]["word"] == "Running" and rows["Gemma"]["canRun"] is True and rows["Gemma"]["program"] == "llamacpp"
    assert rows["Qwen"]["sizeBytes"] is None and rows["Qwen"]["sizeText"] is None, "no size reported: a dash, never a guess"
    assert t["readOnly"] is False and t["note"] == "" and t["folder"] == "D:\\Models" and t["drive"] == "D:"


def test_a_pc_whose_courier_cannot_start_or_stop_is_read_only_and_says_so_once():
    t = models_tab.remote_table("msi", "MSI", True, _report(can=False), {})
    assert t["readOnly"] is True and t["note"] == "Models on this PC cannot be started or stopped from here yet."
    assert all(r["canRun"] is False for r in t["rows"])
    nothing = models_tab.remote_table("msi", "MSI", True, None, {})
    assert nothing["rows"] == [] and nothing["readOnly"] is True


# ---- the page ----
def _run_js(tail: str):
    lm = local_models_ui.LOCAL_MODELS_JS
    pre = lm[lm.index("function jsq"):lm.index("// MultiProxy's own question pop-up")] + lm[lm.index("function lmFmt"):lm.index("function lmFmt") + 140].split("\n")[0] + "\n"
    src = HARNESS + ESC + "\n" + pre + "const LM_REMOVE_WORDS = { inUse: 'In use' };\n" + overview_ui.JS + "\n" + models_ui.MODELS_JS + "\n" + tail
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


@needs_node
def test_the_table_draws_the_columns_words_dots_and_two_button_slots():
    rows = models_tab.this_pc_rows(INSTALLED, KEPT, USES, DEC, VOICE)
    pc = models_tab.this_table("Area-51", rows, "G:\\Models", 113_800_000_000, "G:")
    h = _run_js("console.log(JSON.stringify(mdTable(" + json.dumps(pc) + ")));")
    for s in ("<th>Model</th>", "<th>Size</th>", "<th>Used by</th>", "<th>Status</th>", "Not used", "(your file)", "On this PC", "Dialogue, Actions, Memory", "7.6 GB",
              "Models folder:", "Open folder", "113.8 GB free on G:", "ov-dot ok", "ov-dot off"):
        assert s in h, s
    assert h.count('class="md-slot"') == 2 * len(rows), "every row has the first-button slot and the second-button slot"
    kept = h[h.index("Gemma 4 26B A4B"):]
    kept = kept[:kept.index("</tr>")]
    assert kept.index(">Add<") < kept.index(">Delete<") and 'class="primary" onclick="mdAddKept' in kept, "Add is the primary button, then Delete"
    run = h[h.index("Gemma 4 12B"):]
    run = run[:run.index("</tr>")]
    assert run.index(">Stop<") < run.index(">Remove<") and "primary" not in run, "Stop then Remove, both outlined"


@needs_node
def test_a_long_used_by_list_is_shortened_and_the_other_pc_has_no_remove_and_a_pc_picker_only_with_two_pcs():
    assert _run_js("console.log(JSON.stringify([mdNames(['A','B','C']), mdNames(['A','B','C','D','E','F','G','H','I','J'])]));") == ["A, B, C", "A, B and 8 more"]
    one = {"pcs": [models_tab.this_table("Area-51", [], None, None, None)]}
    two = {"pcs": one["pcs"] + [models_tab.remote_table("msi", "MSI", True, _report(), {})]}
    tail = ("const box = { innerHTML: '' }; document.getElementById = id => id === 'mdBody' ? box : null;\n"
            "mdSt = %s; mdRender(); const a = box.innerHTML; mdSt = %s; mdRender(); const b = box.innerHTML; mdPc = 'msi'; mdRender(); const c = box.innerHTML;\n"
            "console.log(JSON.stringify({a, b, c}));") % (json.dumps(one), json.dumps(two))
    o = _run_js(tail)
    assert "md-pick" not in o["a"] and "md-pick" in o["b"]
    assert "Remove" not in o["c"] and ">Stop<" in o["c"] and ">Start<" in o["c"] and "Delete" not in o["c"]


# ---- the pop-ups ----
def test_the_remove_pop_up_has_cancel_left_and_the_main_button_next_to_the_delete_one():
    js = local_models_ui.LOCAL_MODELS_JS
    pop = js[js.index("function ownAskRemove"):js.index("// A visible")]
    order = [pop.index(x) for x in ('id="ownRmCancel"', 'id="ownRmKeep"', 'id="ownRmDelete"')]
    assert order == sorted(order), "Cancel, Remove (keep files), Remove (delete files), left to right"
    assert 'class="ghost" id="ownRmCancel"' in pop and 'class="ghost" id="ownRmDelete"' in pop and '<button id="ownRmKeep">' in pop, "the safe one is the main button, the others outlined"
    assert "justify-content:flex-end" in pop
    assert "'In use'" in js and "w.className = 'lm-warn'" in pop and "w.title = usedBy.join(', ')" in pop, "one short amber line; the job names are the hover"
    assert "deleteLabel) wrap.querySelector('#ownRmDelete').textContent" in pop.replace("if (deleteLabel) wrap.querySelector('#ownRmDelete').textContent", "deleteLabel) wrap.querySelector('#ownRmDelete').textContent"), "an own file gets no delete button"


def test_the_models_page_has_one_set_of_words_and_calls_the_existing_routes_only():
    js = models_ui.MODELS_JS
    for route in ("/config/local-models/' + what", "/config/local-models/remove", "/config/local-models/add-kept", "/config/local-models/delete-kept", "/config/decisions/", "/config/voice/",
                  "/config/other-pcs/courier/action", "/config/settings/open-folder"):
        assert route in js, route
    assert "lmRemoveFlow(" in js, "Remove is the Local device tab's own pop-up"
    assert "copy" not in js.lower().replace("copyright", ""), "no copy-to-PC button in this item"


# ---- the route, through the real app (nothing downloaded here: an empty table, no guesses) ----
def test_the_route_answers_with_this_pc_first_and_nothing_made_up(test_client):
    d = test_client.get("/config/models-tab").json()
    assert d["pcs"][0]["id"] == "this" and d["pcs"][0]["this"] is True and isinstance(d["pcs"][0]["rows"], list)
    assert all(p["id"] != "this" for p in d["pcs"][1:])


# ---- the kept downloads (2026-10-26): Quick decisions and Voice use the same Remove pop-up, and what is kept is never hidden ----
FILES = {"Kev-4B-Q4_K_M.gguf": 3_000_000_000, "higgs.gguf": 5_000_000_000, "asr.gguf": 600_000_000, "chatter.gguf": 2_000_000_000, "runner-a.zip": 90_000_000, "old-build.zip": 40_000_000}
DECS = [{"key": "kev-4b", "name": "Kev-4B", "names": ["Kev-4B-Q4_K_M.gguf"], "installed": False}]
VOICES = [{"key": "higgs", "name": "Higgs voice", "names": ["higgs.gguf", "asr.gguf"], "installed": False},
          {"key": "chatter", "name": "Chatterbox", "names": ["chatter.gguf", "asr.gguf"], "installed": True}]
RUNNERS = [{"key": "a", "name": "Voice engine (A)", "names": ["runner-a.zip"], "inUse": True}]


def test_kept_only_models_runners_and_leftovers_all_show_with_the_right_state():
    rows = by_name(models_tab.download_rows(FILES, DECS, VOICES, RUNNERS))
    k = rows["Kev-4B"]
    assert (k["kind"], k["word"], k["names"], k["what"], k["modelKey"]) == ("kept-dl", "On this PC", ["Kev-4B-Q4_K_M.gguf"], "decision", "kev-4b")
    h = rows["Higgs voice"]
    assert h["names"] == ["higgs.gguf"], "the words-from-speech file an installed voice also uses is not offered for deletion here"
    assert "Chatterbox" not in rows, "an installed model has its own row"
    r = rows["Voice engine (A)"]
    assert (r["kind"], r["word"], r["usedBy"]) == ("runner", "In use", ["Voice"])
    o = rows["Leftover download (old-build.zip)"]
    assert (o["kind"], o["word"], o["names"]) == ("orphan", "Kept on this PC", ["old-build.zip"]), "a leftover file and a runner nothing needs are Kept on this PC; a kept model stays On this PC"


def test_an_installed_decision_model_has_no_kept_row_and_a_runner_nothing_needs_is_on_this_pc():
    rows = by_name(models_tab.download_rows({"Kev-4B-Q4_K_M.gguf": 5, "runner-a.zip": 7}, [{**DECS[0], "installed": True}], [], [{**RUNNERS[0], "inUse": False}]))
    assert list(rows) == ["Voice engine (A)"] and rows["Voice engine (A)"]["word"] == "Kept on this PC" and rows["Voice engine (A)"]["usedBy"] == []


def test_decision_and_voice_remove_use_the_same_one_pop_up_and_pass_the_choice_on():
    js = models_ui.MODELS_JS
    flow = js[js.index("function mdRemove"):js.index("function mdAddDownload")]
    assert flow.count("lmRemoveFlow(") == 2, "decision/voice and the others share the one pop-up"
    assert "deleteFiles: r.deleteFiles" in flow and "/config/decisions/uninstall" in flow and "/config/voice/uninstall" in flow
    assert "files are deleted" not in js and "removeDecision" not in js and "removeVoice" not in js
    assert "/config/models-tab/delete-downloads" in js and "'kept-dl'" in js and "mdAddDownload" in js


def test_deleting_downloads_clears_only_the_named_kept_files(tmp_path):
    from voice_downloads import DownloadStore
    store = DownloadStore(lambda: tmp_path)
    for n in ("a.gguf", "b.gguf"):
        (tmp_path / n).write_bytes(b"x" * 10)
    (tmp_path / "index.json").write_text(json.dumps({n: {"size": 10, "sha256": "", "url": "", "kept": 1} for n in ("a.gguf", "b.gguf")}))
    assert store.clear(["a.gguf"]) == 10
    assert not (tmp_path / "a.gguf").exists() and (tmp_path / "b.gguf").exists()


def test_runner_rows_show_in_use_or_on_this_pc_and_only_an_old_build_can_be_deleted():
    builds = [{"dir": "b100-cpu", "build": "b100", "kind": "cpu", "bytes": 5, "current": False}, {"dir": "b200-cuda", "build": "b200", "kind": "cuda", "bytes": 9, "current": True}]
    rows = by_name(models_tab.runner_rows(builds, ["Gemma 4 12B", "Quick decisions"]))
    assert list(rows) == ["Model engine (llama.cpp b100)", "Model engine (llama.cpp b200)"]
    old, cur = rows["Model engine (llama.cpp b100)"], rows["Model engine (llama.cpp b200)"]
    assert (old["word"], old["canDelete"], old["usedBy"]) == ("Kept on this PC", True, [])
    assert (cur["word"], cur["canDelete"], cur["usedBy"]) == ("In use", False, ["Gemma 4 12B", "Quick decisions"])
    assert by_name(models_tab.runner_rows(builds[1:], []))["Model engine (llama.cpp b200)"]["word"] == "Kept on this PC", "nothing needs it: kept, not in use"
    js = models_ui.MODELS_JS
    assert "disabled title=" in js and "delete-engine-build" in js
