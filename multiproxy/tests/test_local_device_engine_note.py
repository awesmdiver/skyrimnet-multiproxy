"""The Local device tab names the llama.cpp engine (only while it is not installed) and shows the graphics card row without a picture."""
import local_models_ui

JS = local_models_ui.LOCAL_MODELS_JS
NOTE = "To run local models, you need the llama.cpp engine. Just pick the version that matches your PC and click Download."


def test_the_engine_note_sits_before_the_first_time_steps_and_only_while_the_engine_is_missing():
    note, steps = JS.index(NOTE), JS.index("First time: set up local models")
    assert note < steps
    branch = JS.rindex("if (!eng.installed) {", 0, note)                       # the note is inside the not-installed branch...
    assert "else {" in JS[branch:note] and "if (remote)" in JS[branch:note]    # ...in its local (not remote) half
    assert JS.count(NOTE) == 1


def test_the_graphics_card_row_has_no_emoji_and_no_picture_cell():
    assert "🎮" not in JS and "🎮" not in local_models_ui.LOCAL_MODELS_CSS
    assert ".lm-gpu .big" not in local_models_ui.LOCAL_MODELS_CSS and 'class="big"' not in JS
    assert '<div class="lm-gpu"><div><b>' in JS
