"""The dashboard never uses the browser's own grey boxes ("127.0.0.1:8000 says ..."): every question is MultiProxy's own pop-up
(ownAsk in local_models_ui.py), every error is the page's warning line. A scan fails if a native call comes back; the pop-up's
behaviour (Cancel keeps, the action button removes, Esc cancels, focus starts on Cancel) is run under Node with a stand-in page."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import local_models_ui
import lmstudio_ui
import other_pcs_ui

ROOT = Path(__file__).resolve().parent.parent
NATIVE = re.compile(r"(?<![\w.$])(?:window\.)?(?:confirm|alert|prompt)\(")


def _code_lines(text: str):
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith(("//", "#", "*", "/*")):
            continue
        yield n, re.sub(r"\s//\s.*$", "", line)


def test_no_native_dialog_call_anywhere_in_the_dashboard_source():
    found = []
    for p in list(ROOT.glob("*.py")) + list(ROOT.glob("*.html")) + list(ROOT.glob("*.js")):
        for n, line in _code_lines(p.read_text(encoding="utf-8", errors="replace")):
            if NATIVE.search(line) and not re.search(r"\bdef\s+(confirm|alert|prompt)\b", line):
                found.append(f"{p.name}:{n}: {line.strip()[:100]}")
    assert not found, "the browser's own dialog came back:\n" + "\n".join(found)


NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

_LM = local_models_ui.LOCAL_MODELS_JS
ASK = _LM[_LM.index("function ownAsk("):_LM.index("// A visible \"Start here\" list")]

HARNESS = """
const kids = {}; const docHandlers = []; let wrapEl = null; let focused = null; let body = [];
function mk(id) { const e = { id, textContent: '', onclick: null, focus() { focused = id; } }; return e; }
global.document = {
  activeElement: null,
  createElement() {
    const w = { className: '', _l: {}, set innerHTML(v) { this._h = v; }, addEventListener(t, f) { this._l[t] = f; }, remove() { wrapEl = null; },
      querySelector(sel) { return kids[sel] || (kids[sel] = mk(sel)); } };
    return w;
  },
  body: { appendChild(w) { wrapEl = w; } },
  addEventListener(t, f) { docHandlers.push(f); }, removeEventListener(t, f) { const i = docHandlers.indexOf(f); if (i >= 0) docHandlers.splice(i, 1); },
};
const key = k => { const ev = { key: k, target: {}, preventDefault() {}, stopPropagation() {} }; docHandlers.slice().forEach(h => h(ev)); };
"""


def _run(script: str) -> dict:
    src = HARNESS + ASK + "\n(async () => {\n" + script + "\n})();\n"
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
class TestOwnAsk:
    def test_the_question_and_both_buttons_and_focus_starts_on_cancel(self):
        got = _run("""
  const p = ownAsk('Remove msi? Its models stop showing up here.', 'Remove');
  const shown = { q: kids['#ownAskText'].textContent, go: kids['#ownAskGo'].textContent, focus: focused, open: !!wrapEl };
  kids['#ownAskCancel'].onclick(); await p;
  console.log(JSON.stringify({ shown, closed: !wrapEl }));""")
        assert got["shown"] == {"q": "Remove msi? Its models stop showing up here.", "go": "Remove", "focus": "#ownAskCancel", "open": True}
        assert got["closed"] is True

    def test_cancel_and_escape_say_no_and_the_action_button_says_yes(self):
        got = _run("""
  let p = ownAsk('q', 'Delete'); kids['#ownAskCancel'].onclick(); const a = await p;
  p = ownAsk('q', 'Delete'); key('Escape'); const b = await p;
  p = ownAsk('q', 'Delete'); kids['#ownAskGo'].onclick(); const c = await p;
  p = ownAsk('q', 'Delete'); key('Enter'); const d = await p;
  console.log(JSON.stringify({ a, b, c, d, handlersLeft: docHandlers.length }));""")
        assert got == {"a": False, "b": False, "c": True, "d": True, "handlersLeft": 0}

    def test_remove_a_pc_and_remove_a_model_only_act_after_the_action_button(self):
        got = _run("""
  const posted = []; let asked = [];
  global.lmPost = async (u, b) => { posted.push(u); return {}; };
  global.lmAct = fn => fn();
  global.opAct = async fn => { await fn(); };
  global.opShowError = () => {};
  global.lmRemoveFlow = async (id, name, custom) => { asked.push(custom ? 'own' : 'file'); return { deleteFiles: false }; };       // the Remove pop-up itself (keep or delete files) is tested with the Local device tab
  eval(global.__REMOVE_FNS__);
  opRemove('msi', 'msi'); kids['#ownAskCancel'].onclick(); await new Promise(r => setTimeout(r, 0));
  const afterCancel = posted.length;
  opRemove('msi', 'msi'); kids['#ownAskGo'].onclick(); await new Promise(r => setTimeout(r, 0));
  lmRemove('m1', 'Gemma', true); await new Promise(r => setTimeout(r, 0));
  console.log(JSON.stringify({ afterCancel, posted, asked }));""".replace(
            "global.__REMOVE_FNS__", json.dumps(
                _fn(other_pcs_ui.OTHER_PCS_JS, "function opRemove(") + "\n" + _fn(local_models_ui.LOCAL_MODELS_JS, "function lmRemove(")
                + "\nglobal.opRemove = opRemove; global.lmRemove = lmRemove;")))
        assert got["afterCancel"] == 0
        assert got["posted"] == ["/config/other-pcs/servers/remove", "/config/local-models/remove"] and got["asked"] == ["own"], "a model's Remove goes through the one shared pop-up and posts only after its answer"


def _fn(js: str, start: str) -> str:
    """One top-level function's source, from its first line to the line that is just `}`."""
    i = js.index(start)
    j = js.index("\n}\n", i) + 3
    return js[i:j]
