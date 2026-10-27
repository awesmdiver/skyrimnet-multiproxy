"""The Setup tab shows every address a player pastes into SkyrimNet (2026-10-03): Chat, Voice and Quick decisions.

Chat is in the page as before; Voice and Quick decisions come from GET /config/addresses, each address from the helper its own card uses (one source). Until
one is set up its row still shows, quieter, with "Not set up yet" and a link to its tab.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import address_rows_ui
import decision_wiring
import voice_wiring

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")


def _fake_engine(state):
    return SimpleNamespace(status=lambda: {"state": state})


def test_the_chat_row_is_in_the_page_with_its_copy_button_and_where_it_goes(proxy_module, test_client):
    html = test_client.get("/").text
    chat = f"http://127.0.0.1:{proxy_module.MAIN_PORT}/v1/chat/completions"
    assert f"<span>{chat}</span>" in html and f"copyModel(this, '{chat}')" in html
    assert "Connect to SkyrimNet" not in html.replace("Settings > SkyrimNet connection", ""), "no card of that name any more: it is a Settings section"
    assert '<span class="label addr-name">Chat</span>' in html
    assert "Paste into SkyrimNet under AI &gt; Providers, API Endpoint (API Style: Chat Completions)." in html
    assert 'id="addrRows"' in html


def test_the_two_other_addresses_come_from_the_helpers_their_own_cards_use(proxy_module, test_client, monkeypatch):
    monkeypatch.setattr(proxy_module.voice_handles.routed, "status", lambda: {"state": "ready"}, raising=False)
    monkeypatch.setattr(proxy_module.decision_handles, "active", lambda: _fake_engine("ready"))
    d = test_client.get("/config/addresses").json()
    port = proxy_module.MAIN_PORT
    assert d["chat"] == f"http://127.0.0.1:{port}/v1/chat/completions"
    assert d["voice"] == {"setUp": True, "address": voice_wiring.endpoint_address(port)} and d["voice"]["address"] == f"http://127.0.0.1:{port}"
    assert d["decisions"] == {"setUp": True, "address": decision_wiring.endpoint_address(port)}
    assert d["decisions"]["address"] == f"http://127.0.0.1:{port}/v1/systemone"


def test_the_cards_own_routes_show_the_very_same_address(proxy_module, test_client):
    port = proxy_module.MAIN_PORT
    assert test_client.get("/config/voice/overview").json()["address"] == voice_wiring.endpoint_address(port)
    assert test_client.get("/config/addresses").json()["voice"]["address"] == voice_wiring.endpoint_address(port)
    assert test_client.get("/config/decisions/status").json()["address"] == decision_wiring.endpoint_address(port)
    assert test_client.get("/config/addresses").json()["decisions"]["address"] == decision_wiring.endpoint_address(port)


@pytest.mark.parametrize("state,set_up", [("not_installed", False), ("installing", False), ("", False), ("stopped", True), ("ready", True), ("died", True)])
def test_set_up_means_installed(proxy_module, test_client, monkeypatch, state, set_up):
    monkeypatch.setattr(proxy_module.voice_handles.routed, "status", lambda: {"state": state}, raising=False)
    monkeypatch.setattr(proxy_module.decision_handles, "active", lambda: _fake_engine(state))
    d = test_client.get("/config/addresses").json()
    assert d["voice"]["setUp"] is set_up and d["decisions"]["setUp"] is set_up


def test_an_engine_that_cannot_be_read_counts_as_not_set_up(proxy_module, test_client, monkeypatch):
    def boom():
        raise RuntimeError("no")
    monkeypatch.setattr(proxy_module.voice_handles.routed, "status", boom, raising=False)
    d = test_client.get("/config/addresses").json()
    assert d["voice"]["setUp"] is False


def _run(data):
    harness = r"""
const made = [];
function mk(tag) { const e = { tag, children: [], className: '', textContent: '', style: {}, append(...c) { for (const x of c) this.children.push(x); }, appendChild(c) { this.children.push(c); }, replaceChildren() { this.children = []; } }; made.push(e); return e; }
const host = mk('div');
global.document = { getElementById: id => id === 'addrRows' ? host : null, createElement: mk };
global.showMainTab = w => { global.shown = w; };
global.copyModel = (b, v) => { global.copied = v; };
global.fetch = async () => ({ json: async () => DATA });
const orig = global.showMainTab;
function flat(e) { return [e.textContent, ...(e.children || []).flatMap(c => typeof c === 'string' ? [c] : flat(c))]; }
"""
    src = harness + address_rows_ui.JS + "\n" + r"""
setTimeout(() => {
  const rows = host.children.map(r => ({ cls: r.className, text: flat(r).join(' | '), kids: r.children.map(c => c.className || '') }));
  console.log(JSON.stringify({ rows }));
}, 50);
"""
    src = src.replace("const orig = global.showMainTab;", "").replace("DATA", "(" + json.dumps(data) + ")", 1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        Path(path).unlink()
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8").strip().splitlines()[-1])


@needs_node
def test_both_set_up_rows_show_the_address_a_copy_button_and_where_it_goes():
    r = _run({"voice": {"setUp": True, "address": "http://127.0.0.1:8000"}, "decisions": {"setUp": True, "address": "http://127.0.0.1:8000/v1/systemone"}})["rows"]
    assert [x["cls"] for x in r] == ["addr-row", "addr-row"]
    assert "Voice" in r[0]["text"] and "http://127.0.0.1:8000 " in r[0]["text"] + " " and "Copy" in r[0]["text"] and "Paste into SkyrimNet's Higgs tab under Endpoint." in r[0]["text"]
    assert "Quick decisions" in r[1]["text"] and "http://127.0.0.1:8000/v1/systemone" in r[1]["text"] and "Copy" in r[1]["text"]
    assert "Paste into SkyrimNet under AI > Providers, API Endpoint (API Style: System One)." in r[1]["text"]
    assert "Not set up yet" not in r[0]["text"] + r[1]["text"]


@needs_node
def test_a_row_not_set_up_looks_like_the_chat_row_with_not_set_up_yet_and_a_link_to_its_tab_and_no_address():
    r = _run({"voice": {"setUp": False, "address": "http://127.0.0.1:8000"}, "decisions": {"setUp": False, "address": "http://127.0.0.1:8000/v1/systemone"}})["rows"]
    assert [x["cls"] for x in r] == ["addr-row", "addr-row"], "the same look as the chat row (director: \"need to look the same as the first one\")"
    assert "Not set up yet." in r[0]["text"] and "Open the Voice tab" in r[0]["text"] and "127.0.0.1" not in r[0]["text"] and "Copy" not in r[0]["text"]
    assert "Not set up yet." in r[1]["text"] and "Open the Decisions tab" in r[1]["text"] and "127.0.0.1" not in r[1]["text"]
    assert "Paste into SkyrimNet's Higgs tab under Endpoint." in r[0]["text"], "the row still says where it goes, so the player learns it exists"


@needs_node
def test_speech_to_text_row_shows_its_address_and_the_turn_it_on_sentence_only_while_off():
    base = {"voice": {"setUp": False}, "decisions": {"setUp": False}}
    off = _run({**base, "stt": {"on": False, "address": "http://127.0.0.1:8123/v1/audio/transcriptions"}})["rows"]
    assert len(off) == 3 and "Speech to text" in off[2]["text"] and "http://127.0.0.1:8123/v1/audio/transcriptions" in off[2]["text"] and "Copy" in off[2]["text"]
    assert "Paste into SkyrimNet's speech settings under External Whisper. Turn it on first on the Voice tab." in off[2]["text"]
    on = _run({**base, "stt": {"on": True, "address": "http://127.0.0.1:9000/v1/audio/transcriptions"}})["rows"]
    assert "http://127.0.0.1:9000/v1/audio/transcriptions" in on[2]["text"] and "Turn it on first" not in on[2]["text"]
    assert "External Whisper." in on[2]["text"]


def test_the_stt_address_has_one_source_and_follows_the_port():
    import voice_wiring
    assert voice_wiring.stt_address(8123) == "http://127.0.0.1:8123/v1/audio/transcriptions"


@needs_node
def test_a_failed_read_shows_both_as_not_set_up_instead_of_nothing():
    r = _run({})["rows"]
    assert len(r) == 2 and all("Not set up yet." in x["text"] for x in r)


def test_the_script_reloads_whenever_the_settings_page_is_drawn():
    import settings_ui
    assert "if (typeof addrLoad === 'function') addrLoad();" in settings_ui.JS and "showMainTab = function" not in address_rows_ui.JS
    assert "innerHTML" not in address_rows_ui.JS


def test_css_uses_only_the_theme_colours():
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", address_rows_ui.CSS)
