"""Uninstall the llama.cpp engine from the Local device page (2026-10-27): the route, what goes and what stays, and the pop-up.
A fake engine and fake model files in a scratch folder; the director's real engine and models are never touched."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

import local_models as lm
import local_models_ui
from tests.test_local_models_routing import FAKE_SERVER

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"


@pytest_asyncio.fixture
async def world(proxy_module, tmp_path, monkeypatch):
    """The proxy's own local_svc pointed at a scratch models folder: a fake engine, one downloaded catalog model, one file of the player's own."""
    monkeypatch.setattr(lm, "HEALTH_POLL_SECONDS", 0.05)
    svc = proxy_module.local_svc
    svc._command_builder = lambda exe, model, port, gpu: [sys.executable, FAKE_SERVER, "--port", str(port)]
    svc._engine_size.forget()
    svc._models_size.forget()
    folder = svc.set_folder(str(tmp_path / "models"))
    eng = folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-cpu"
    eng.mkdir(parents=True)
    (eng / EXE).write_bytes(b"e" * 2000)
    svc._update(engine={"kind": "cpu", "build": lm.LLAMA_CPP_BUILD})
    cat_id = lm.CATALOG[0]["id"]
    cat = folder / "models" / cat_id
    cat.mkdir(parents=True)
    (cat / "model.gguf").write_bytes(b"m" * 5000)
    own = tmp_path / "Tiny Chat.gguf"
    own.write_bytes(b"x")
    svc.add_custom(str(own))
    svc.engine_dir_path, svc.cat_dir, svc.own_file = eng, cat, own
    yield svc
    await svc.stop_all()
    svc._engine_size.forget()
    svc._models_size.forget()


@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


@pytest.mark.asyncio
async def test_the_state_says_the_engine_can_be_removed_and_how_big_it_and_the_models_are(world, client):
    e = (await client.get("/config/local-models")).json()["engine"]
    assert e["installed"] and e["removable"] is True and e["diskBytes"] == 2000 and e["modelsBytes"] == 5000


@pytest.mark.asyncio
async def test_uninstall_removes_the_engine_and_keeps_the_models(world, client):
    r = await client.post("/config/local-models/engine/uninstall", json={})
    assert r.status_code == 200 and r.json()["freedBytes"] == 2000
    assert not (world.folder / "engine").exists()
    assert (world.cat_dir / "model.gguf").exists() and world.own_file.exists(), "no model was touched"
    s = (await client.get("/config/local-models")).json()
    assert s["engine"]["installed"] is False and s["engine"]["removable"] is False, "the page goes back to the first-time state"


@pytest.mark.asyncio
async def test_the_also_delete_models_option_removes_downloaded_models_but_never_the_players_own_file(world, client):
    r = await client.post("/config/local-models/engine/uninstall", json={"deleteModels": True})
    assert r.status_code == 200 and r.json()["freedBytes"] == 2000 + 5000
    assert not world.cat_dir.exists() and not (world.folder / "engine").exists()
    assert world.own_file.exists() and (world.folder / "models").is_dir(), "the player's own file and the models folder stay"
    assert world.find(world.cat_dir.name) is None


@pytest.mark.asyncio
async def test_only_a_literal_true_deletes_the_models(world, client):
    await client.post("/config/local-models/engine/uninstall", json={"deleteModels": "yes"})
    assert (world.cat_dir / "model.gguf").exists()


@pytest.mark.asyncio
async def test_a_running_model_is_stopped_first(world, client):
    s = await world.start("tiny-chat-gguf" if world.find("tiny-chat-gguf") else world.installed()[-1].id)
    await s.ready.wait()
    assert s.state == "running"
    r = await client.post("/config/local-models/engine/uninstall", json={})
    assert r.status_code == 200
    assert s.state != "running" and not any(x.state == "running" for x in world.servers.values())


@pytest.mark.asyncio
async def test_an_engine_installed_by_hand_is_never_touched(world, client, tmp_path):
    hand = tmp_path / "my-llama-cpp"
    hand.mkdir()
    (hand / EXE).write_bytes(b"h")
    shutil.rmtree(world.folder / "engine")
    world._update(engine=None)                                   # nothing MultiProxy downloaded: only the player's own copy exists
    e = (await client.get("/config/local-models")).json()["engine"]
    assert e["removable"] is False, "no button for it"
    r = await client.post("/config/local-models/engine/uninstall", json={})
    assert r.status_code == 404
    assert (hand / EXE).exists() and (world.cat_dir / "model.gguf").exists()


@pytest.mark.asyncio
async def test_a_download_in_progress_blocks_it_and_nothing_is_removed(world, client):
    world.jobs["engine"] = lm.Job("engine", "Engine", [], total=1)
    world.jobs["engine"].state = "running"
    r = await client.post("/config/local-models/engine/uninstall", json={})
    assert r.status_code == 409 and (world.engine_dir_path / EXE).exists()
    world.jobs.clear()


@pytest.mark.asyncio
async def test_the_route_is_one_of_the_guarded_config_routes(proxy_module):
    import local_guard
    app_routes = {getattr(r, "path", "") for r in proxy_module.app.routes}
    assert "/config/local-models/engine/uninstall" in app_routes
    assert "/config/" in Path(local_guard.__file__).read_text(encoding="utf-8"), "everything under /config/ needs the custom header"


# ---- the page ----
JS = local_models_ui.LOCAL_MODELS_JS
START = JS.index("const LM_ENGINE_UN_WORDS")
POPUP = JS[START:JS.index("function lmAddKept(")]

HARNESS = """
const kids = {}; const docHandlers = []; let wrapEl = null; let html = '';
function mk(id) { const e = { id, textContent: '', checked: false, onclick: null, focus() {} }; return e; }
global.document = {
  activeElement: null,
  createElement() { return { className: '', _l: {}, set innerHTML(v) { html = v; }, addEventListener(t, f) { this._l[t] = f; }, remove() { wrapEl = null; }, querySelector(sel) { return kids[sel] || (kids[sel] = mk(sel)); } }; },
  body: { appendChild(w) { wrapEl = w; } },
  addEventListener(t, f) { docHandlers.push(f); }, removeEventListener(t, f) { const i = docHandlers.indexOf(f); if (i >= 0) docHandlers.splice(i, 1); },
};
const key = k => { const ev = { key: k, target: {}, preventDefault() {}, stopPropagation() {} }; docHandlers.slice().forEach(h => h(ev)); };
function lmFmt(bytes) { return bytes >= 1e8 ? (bytes / 1e9).toFixed(1) + ' GB' : Math.round(bytes / 1e6) + ' MB'; }
"""


def _run(script: str) -> dict:
    src = HARNESS + POPUP + "\n(async () => {\n" + script + "\n})();\n"
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
def test_the_popup_says_what_goes_and_what_stays_and_answers_only_when_a_button_is_pressed():
    got = _run("""
  let p = ownAskUninstallEngine(1.5e9, 7.6e9);
  const shown = { title: kids['#ownUeTitle'].textContent, body: kids['#ownUeBody'].textContent, box: kids['#ownUeModelsText'].textContent, go: kids['#ownUeGo'].textContent, hasBox: html.includes('type="checkbox" id="ownUeModels"'), unticked: !html.includes('checked') };
  kids['#ownUeCancel'].onclick(); const a = await p;
  p = ownAskUninstallEngine(1.5e9, 7.6e9); key('Escape'); const b = await p;
  p = ownAskUninstallEngine(1.5e9, 7.6e9); kids['#ownUeGo'].onclick(); const c = await p;
  kids['#ownUeModels'].checked = true;
  p = ownAskUninstallEngine(1.5e9, 7.6e9); kids['#ownUeModels'].checked = true; kids['#ownUeGo'].onclick(); const d = await p;
  p = ownAskUninstallEngine(1.5e9, 0); const noBox = !html.includes('ownUeModels"'); kids['#ownUeCancel'].onclick(); await p;
  console.log(JSON.stringify({ shown, a, b, c, d, noBox, handlersLeft: docHandlers.length }));""")
    s = got["shown"]
    assert s["title"] == "Uninstall the model engine (llama.cpp)?"
    assert "frees about 1.5 GB" in s["body"] and "Your downloaded models stay." in s["body"] and "Any running model stops first." in s["body"]
    assert s["box"] == "Also delete my downloaded models (7.6 GB)" and s["go"] == "Uninstall" and s["hasBox"] and s["unticked"]
    assert got["a"] is None and got["b"] is None, "Cancel and Esc do nothing"
    assert got["c"] == {"deleteModels": False}, "the box is unticked by default"
    assert got["d"] == {"deleteModels": True}
    assert got["noBox"] is True, "no models downloaded: no box"
    assert got["handlersLeft"] == 0


def test_the_popup_has_cancel_on_the_left_the_normal_button_on_the_right_and_no_red():
    order = [POPUP.index(x) for x in ('id="ownUeCancel"', 'id="ownUeGo"')]
    assert order == sorted(order)
    assert 'class="ghost" id="ownUeCancel"' in POPUP and 'class="primary" id="ownUeGo"' in POPUP and "red" not in POPUP.lower().replace("required", "")


def test_the_button_shows_only_for_this_pcs_downloaded_engine_and_not_while_it_downloads():
    i = JS.index("const unBtn")
    line = JS[i:JS.index("\n", i)]
    assert "!remote" in line and "eng.removable" in line and "!dlRunning" in line and "lmAskUninstallEngine()" in line and ">Uninstall</button>" in line
    assert "/config/local-models/engine/uninstall" in JS and "lmAct(" in JS[JS.index("function lmAskUninstallEngine"):JS.index("function lmAddKept(")], "the page refreshes the way Stop does"
