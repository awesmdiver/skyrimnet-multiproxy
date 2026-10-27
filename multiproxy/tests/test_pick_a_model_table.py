"""The "Pick a model" list is a table (2026-10-26): two lines per model, with Size, Status and the buttons in their own columns. Drawn by the page's own code, run in node."""
import json
import os
import subprocess
import tempfile

import local_models_ui
from tests.test_overview import ESC, HARNESS, NODE, needs_node

JS = local_models_ui.LOCAL_MODELS_JS


def _page(tail: str):
    pre = JS[JS.index("function jsq"):JS.index("// MultiProxy's own question pop-up")]
    words = JS[JS.index("const LM_REMOVE_WORDS"):JS.index("// The one Remove question")]
    parts = [pre, JS[JS.index("function lmStatus"):JS.index("function lmDlBlock")], JS[JS.index("function lmIcons"):JS.index("const LM_RUN")],
             JS[JS.index("const LM_TABLE_WORDS"):JS.index("function lmRender()")]]
    src = HARNESS + ESC + "\n" + words + "\n" + "\n".join(parts) + "\n" + tail
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


BASE = {"id": "g", "name": "Gemma 4 E2B", "blurb": "Small and quick.", "sizeText": "4.4 GB", "vision": True, "fit": "fits", "fitWhy": ""}
STATES = {
    "none": {},
    "downloading": {"download": {"state": "running", "percent": 73}},
    "downloaded": {"installed": True},
    "kept": {"kept": True},
    "update": {"update": True},
    "elsewhere": {"elsewhere": {"program": "LM Studio", "name": "x", "useName": "lmstudio/x"}},
    "error": {"download": {"state": "error", "error": "No room on the drive."}},
}


def _rows():
    tail = "const S = %s; const B = %s; const out = {};\nfor (const k in S) out[k] = lmModelRow(Object.assign({}, B, S[k]), { modelServer: false });\nconsole.log(JSON.stringify(out));" % (
        json.dumps(STATES), json.dumps(BASE))
    return _page(tail)


@needs_node
def test_every_state_has_the_same_columns_in_the_same_order_and_two_lines():
    for k, h in _rows().items():
        order = [h.index(x) for x in ('class="lm-name"', 'class="lm-meta', 'class="lm-tsize"', 'class="lm-tst"', 'class="lm-tslots"')]
        assert order == sorted(order), k
        assert h.count('class="lm-tslot"') == 2, k
        assert h.count('class="lm-name"') == 1 and h.count('class="lm-meta') == 1, f"{k}: name line + description line, nothing more"
        assert "4.4 GB" in h.split('class="lm-tsize"')[1].split("</div>")[0], f"{k}: the size is in its own column"


@needs_node
def test_each_state_has_its_status_and_its_buttons_side_by_side_in_two_slots():
    r = _rows()

    def slots(h):
        a, b = h.split('class="lm-tslot">')[1:3]
        return a.split("</div>")[0], b.split("</div>")[0]

    assert "lm-st" not in r["none"].split('class="lm-tst"')[1].split("lm-tslots")[0] and "Download" in slots(r["none"])[0]
    assert "Downloading… 73%" in r["downloading"] and 'class="lm-bar"' in r["downloading"].split('class="lm-tst"')[1] and "Cancel" in slots(r["downloading"])[1] and not slots(r["downloading"])[0]
    assert "Downloaded" in r["downloaded"] and "<button" not in r["downloaded"]
    a, b = slots(r["kept"])
    assert "On this PC" in r["kept"] and "Add" in a and "Delete" in b
    assert "Update available" in r["update"] and "Update" in slots(r["update"])[0]
    a, b = slots(r["elsewhere"])
    assert "Already in LM Studio" in r["elsewhere"] and "Use it" in a and "Download" in b
    assert "No room on the drive." in r["error"].split('class="lm-tst"')[0], "the problem takes the description line, not a third line"


@needs_node
def test_the_sentence_is_gone_and_the_fit_dot_and_eye_still_show():
    r = _rows()
    assert "Go to Your local models below to start it." not in JS and "Go to Your local models below" not in r["downloaded"]
    assert 'title="Sees pictures"' in r["none"]
    big = _page("console.log(JSON.stringify(lmModelRow(Object.assign({}, %s, { fit: 'too_big', fitWhy: 'Skyrim peak 1 GB' }), { modelServer: false })));" % json.dumps(BASE))
    assert "lm-dot" in big and "Skyrim peak 1 GB" in big


def test_the_table_has_a_header_row_and_scrolls_inside_its_card():
    assert "LM_TABLE_WORDS.model" in JS and "LM_TABLE_WORDS.size" in JS and "LM_TABLE_WORDS.status" in JS
    css = local_models_ui.LOCAL_MODELS_CSS
    assert ".lm-tbl { overflow-x:auto }" in css and ".lm-tslots { display:flex; justify-content:flex-end; gap:8px }" in css
    assert "min-height:3.1rem" in css and "grid-template-columns:minmax(0,1fr) 5rem 11rem 12.5rem" in css
