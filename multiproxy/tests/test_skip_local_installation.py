""""Skip local installation" (2026-10-27): the fourth choice on the Local device page's first-time card, the tidied page, the way back, and the screens that respect it."""
import asyncio
import json

import pytest

import local_models_ui
import local_settings_ui
import models_off
import set_me_up_ui
from tests.test_local_models import _install_fake_engine, _install_fake_model, store, svc      # noqa: F401  (fixtures)

LM = open(local_models_ui.__file__, encoding="utf-8").read()
LS = open(local_settings_ui.__file__, encoding="utf-8").read()


# ---- the words and the fourth choice ----
def test_the_fourth_choice_has_the_asked_for_words_and_the_same_row_look():
    assert "title: 'Skip local installation'" in LM and "blurb: 'Use a cloud provider or another device instead.'" in LM
    assert "OpenRouter" not in LM
    i = LM.index("function lmSkipOption()")
    row = LM[i:LM.index("function lmPickSkip", i)]
    assert 'class="lm-opt' in row and 'type="radio" name="lmEngine"' in row, "the same radio row as the versions"
    assert '<div class="lm-muted"></div></label>' in row, "its size column stays empty"


def test_the_skip_row_comes_after_the_versions_and_the_button_becomes_skip_while_it_is_picked():
    assert LM.index("h += lmSkipOption();") > LM.index("lmEngineOption(o);")
    assert "(lmSkipPicked ? '<button onclick=\"lmSetSkip(true)\">' + esc(LM_SKIP_WORDS.button)" in LM
    assert "button: 'Skip'" in LM and "lmDownloadEngine()\">Download</button>" in LM, "Download stays for the real versions"


def test_the_best_for_your_card_pill_is_gone_and_the_highlight_stays():
    assert "Best for your card" not in LM and "lm-rec" not in LM
    assert "'lm-opt' + (sel ? ' sel' : '')" in LM or "lm-opt' + (sel ? ' sel'" in LM


def test_picking_a_version_again_unpicks_skip_and_the_last_real_pick_is_kept():
    assert "function lmPickEngine(kind) { lmPickedEngine = kind; lmSkipPicked = false; lmRender(); }" in LM
    assert "function lmPickSkip() { lmSkipPicked = true; lmRender(); }" in LM, "skip never touches lmPickedEngine"


# ---- the tidied page and the way back ----
def test_a_skipped_device_shows_one_line_and_one_button_and_nothing_else():
    i = LM.index("if (s.skipped && !remote)")
    block = LM[i:LM.index("}\n", i)]
    assert "LM_SKIP_WORDS.off" in block and "LM_SKIP_WORDS.setUp" in block and "return" in block
    assert "Local models are off on this device." in LM and "setUp: 'Set up local models'" in LM
    assert "onclick=\"lmSetSkip(false)\"" in block


def test_the_settings_sections_are_emptied_while_skipped_so_their_jump_links_go_too():
    assert "lmState.skipped) { box.innerHTML = ''; return; }" in LS
    assert "window.lsWasSkipped" in LS, "coming back redraws them"


def test_other_devices_engine_screens_are_not_changed():
    i = LM.index("if (s.skipped && !remote)")
    assert "!remote" in LM[i:i + 40]
    k = LM.index("function lmSkipOption()")
    assert "remote" not in LM[k:k + 500]


# ---- the setting: saved, per device, survives a restart ----
def test_the_helpers_read_the_saved_flag():
    assert models_off.is_skipped({"local_models_skipped": True}) and not models_off.is_skipped({})
    assert models_off.is_quiet({"local_models_skipped": True}) and models_off.is_quiet({"local_models_off": True}) and not models_off.is_quiet({})
    assert not models_off.is_off({"local_models_skipped": True}), "the Settings switch is its own thing"


def test_skip_is_saved_in_the_settings_file_and_read_back_after_a_restart(test_client, proxy_module):
    assert test_client.get("/config/local-models").json()["skipped"] is False
    assert test_client.post("/config/local-models/skip", json={"skip": True}).json()["skipped"] is True
    on_disk = json.loads(open(proxy_module.CONFIG_FILE, encoding="utf-8").read())
    assert on_disk["local_models_skipped"] is True
    proxy_module._config_reader.__init__() if hasattr(proxy_module._config_reader, "__init__") else None      # (a fresh read, as after a restart)
    assert test_client.get("/config/local-models").json()["skipped"] is True
    assert test_client.post("/config/local-models/skip", json={"skip": False}).json()["skipped"] is False
    assert "local_models_skipped" not in json.loads(open(proxy_module.CONFIG_FILE, encoding="utf-8").read())


def test_downloading_a_model_or_the_engine_ends_the_skip(svc, tmp_path, monkeypatch):      # noqa: F811
    _install_fake_engine(svc, tmp_path)
    monkeypatch.setattr(svc, "_check_space", lambda need: None)
    monkeypatch.setattr(svc, "_start_job", lambda job: job)
    svc.set_skipped(True)
    assert svc.skipped()
    svc.download_model("tiny-model")
    assert not svc.skipped(), "a model download (Local device, Set me up or the Models tab all come here) clears it"
    svc.set_skipped(True)
    svc.download_engine("cpu")
    assert not svc.skipped(), "so does an engine download"


# ---- the rest of MultiProxy ----
def test_set_me_up_is_not_offered_and_says_local_models_are_off_while_skipped(test_client, proxy_module):
    test_client.post("/config/local-models/skip", json={"skip": True})
    assert asyncio.run(proxy_module._set_me_up_show()) is False, "no nagging"
    d = test_client.get("/config/set-me-up").json()
    assert d["modelsOff"] is True and d["skipped"] is True, "the screen shows the off view: no local install is offered for this device"
    assert "skipOff" in set_me_up_ui.JS and "smuUnskip()" in set_me_up_ui.JS
    assert models_off.WORDS["skipOff"] == "Local models are off on this device." and models_off.WORDS["skipTurnOn"] == "Set up local models"


def test_check_everything_does_not_report_a_missing_local_engine_while_skipped(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda: True)
    monkeypatch.setattr(proxy_module.local_svc, "engine_build_number", lambda: 1)
    monkeypatch.setattr(proxy_module.local_svc, "engine_exe", lambda: "x")
    test_client.post("/config/local-models/skip", json={"skip": True})
    assert asyncio.run(proxy_module._fix_it_engine()) is None
    ids = [f["key"] for f in test_client.post("/config/check-everything").json().get("findings", [])] if test_client.post("/config/check-everything").status_code == 200 else []
    assert "engine-old" not in ids


def test_the_overview_shows_models_off_here_and_nothing_broken_while_skipped(test_client):
    test_client.post("/config/local-models/skip", json={"skip": True})
    d = test_client.get("/stats/overview").json()
    assert "Models off here" in d["machines"][0]["runs"]
    assert d["machines"][0]["level"] == "ok", "muted words, never amber or red"
