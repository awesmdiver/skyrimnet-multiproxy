"""The models folder is changed in Settings > Folders only (2026-10-26); the Local device tab shows a folder control only when none is chosen yet."""
import local_models_ui
import settings_ui


def _js():
    return open(local_models_ui.__file__, encoding="utf-8").read()


def test_the_saved_in_row_and_its_change_button_are_gone_from_the_local_device_tab():
    js = _js()
    for gone in ("Saved in", "lmChangeFolder", "lmUseTypedFolder", "lmFolderTyped", "Where should models be saved?"):
        assert gone not in js, gone


def test_with_no_folder_the_tab_shows_one_amber_line_and_a_choose_button_that_saves_the_pick():
    js = _js()
    assert "if (!remote && !s.folder) {" in js      # only this device shows the choose-a-folder line
    assert "Choose where to keep the models." in js and ">Choose a folder</button>" in js
    assert "lmAct(lmChooseFolder)" in js
    assert "'/config/local-models/folder', { path: r.path }" in js      # the pick is saved by the same route Settings uses; nothing is moved


def test_a_newer_engine_is_its_own_amber_line_with_a_primary_update_button():
    js = _js()
    assert '<span class="lm-warn">⚠️ A newer version of llama.cpp is available (' in js
    assert "updateEngine" in js and 'class="primary" style="margin-left:auto" onclick="lmDo(' in js and "Update llama.cpp</button>" in js      # the one button goes through lmDo, so it works on this device and on another


def test_settings_uses_the_new_label_and_the_old_one_is_nowhere_on_a_screen():
    st = open(settings_ui.__file__, encoding="utf-8").read()
    assert "Models folder (stores text and voice models)" in st and "Local models folder" not in st
    de = open("decision_engine.py", encoding="utf-8").read()
    assert "Add the models folder to your antivirus allowlist" in de and "Add the Local models folder" not in de
