"""Every status is a dot and a word (2026-10-26): the Local models rows and the device card no longer draw a state as a coloured pill."""
import local_models_ui
import local_settings_ui


def test_a_local_model_state_is_a_dot_and_its_word_with_no_pill():
    js = local_models_ui.JS if hasattr(local_models_ui, "JS") else open(local_models_ui.__file__, encoding="utf-8").read()
    assert "lmStatus(st[0], st[1])" in js and "lmStatus('Downloading" in js
    assert 'class="lm-pill ' not in js and 'class="lm-pill lm-info"' not in js, "no state is a pill"
    assert "'● Running'" not in js, "the word is Running, the dot is drawn by the style"
    for word in ("'Running'", "'Loading…'", "'Stopped'", "'Problem'"):
        assert word in js, "the words stay as they were"


def test_the_dot_uses_the_theme_colours_only():
    css = open(local_models_ui.__file__, encoding="utf-8").read()
    rule = [l for l in css.splitlines() if ".lm-st i" in l]
    assert rule and all("#" not in l.split("{", 1)[1] for l in rule if "{" in l), "theme variables, no literal colours"


def test_waiting_to_send_is_a_dot_and_a_word():
    src = open(local_settings_ui.__file__, encoding="utf-8").read()
    assert '<span class="ls-st"><span class="ls-dot warn"></span>Waiting to send</span>' in src
    assert 'ls-pill ls-amber">Waiting to send' not in src
