"""The Setup tab became Providers, and the SkyrimNet connection details moved to a section of Settings (2026-10-05).

The tab's internal id stays `setup` (every jump, hash and test that uses it keeps working); its text is Providers. What was the "Connect to SkyrimNet" card (the address with Copy,
the voice and quick decisions addresses, the version warning) is now the Settings section "SkyrimNet connection", with the Name to use and the two SkyrimNet steps the Get started
walk-through already has (worded once, in `SN_STEPS`). The Overview shows a small "SkyrimNet: Connected / Waiting for SkyrimNet" row when it can be measured honestly.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import get_started_ui
import overview
import overview_ui
import settings_ui
from tests.test_overview import HARNESS, ESC, base, draw

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")


# ---- the tab ------------------------------------------------------------------------------------------------------------------
def test_the_tab_says_providers_and_keeps_its_id(test_client):
    h = test_client.get("/").text
    assert '<div class="tab" id="tabSetup" onclick="showMainTab(\'setup\')">Providers</div>' in h
    assert '>Setup</div>' not in h[h.index('<div class="tabs">'):h.index('<div id="paneOverview">')]
    assert 'id="paneSetup"' in h


def test_nothing_in_the_page_or_the_code_still_says_the_setup_tab(test_client):
    html = test_client.get("/").text
    assert not re.search(r"Setup (tab|card)", html), re.findall(r".{40}Setup (?:tab|card).{20}", html)
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"Setup tab", text), path.name


def test_the_error_messages_a_player_sees_in_skyrimnet_say_providers(test_client):
    import multiproxy
    src = Path(multiproxy.__file__).read_text(encoding="utf-8")
    assert "key found. Add one on the Providers tab." in src and "Add a key on the Providers tab." in src


def test_the_description_and_the_empty_states_point_at_providers(test_client):
    h = test_client.get("/").text
    assert "Pick the AI services you want to use.<" in h or "Pick the AI services you want to use." in h
    assert "No providers are ready yet. Go to the Providers tab to add one." in h
    assert "No providers set up yet. Go to the Providers tab to add one first." in h
    assert "If it did not open, go to the Providers tab and pick one provider." in h


def test_the_help_pages_and_their_button_say_providers():
    import help_ui
    assert "on the Providers tab</button>" in help_ui.HELP_JS and "open its Providers card." in help_ui.HELP_PANE_HTML
    assert "helpGoSetup(id) { showMainTab('setup'); selectProvider(id); }" in help_ui.HELP_JS, "the jump still lands on the same tab"


# ---- the Settings section ------------------------------------------------------------------------------------------------------------
def test_the_connection_card_is_gone_from_the_providers_pane_and_is_a_settings_section(test_client):
    h = test_client.get("/").text
    pane = h[h.index('<div id="paneSetup"'):h.index('<div id="paneLocal"')]
    assert "Connect to SkyrimNet" not in pane and 'id="addrChat"' not in pane and 'id="addrRows"' not in pane
    assert "Start here" in pane and "Choose a provider" in pane and 'id="panel"' in pane
    assert '<h3 class="st-sec" id="st-skyrimnet">SkyrimNet connection</h3>' in settings_ui.JS


def test_the_section_has_the_address_with_copy_the_name_and_the_steps(proxy_module, test_client):
    h = test_client.get("/").text
    t = h[h.index('<template id="stConnTemplate">'):h.index("</template>", h.index('<template id="stConnTemplate">'))]
    chat = f"http://127.0.0.1:{proxy_module.MAIN_PORT}/v1/chat/completions"
    assert f"<span>{chat}</span>" in t and f"copyModel(this, '{chat}')" in t and ">Copy</button>" in t
    assert "Paste into SkyrimNet under AI &gt; Providers, API Endpoint (API Style: Chat Completions)." in t
    assert '<span class="label addr-name">Name</span>' in t and "<span>SkyrimNet MultiProxy</span>" in t and 'id="addrRows"' in t
    assert "Please update both" not in t, "the version warning only shows when the versions differ"


def test_the_version_warning_moved_with_it(proxy_module, test_client):
    from unittest.mock import patch
    with patch.object(proxy_module, "PLUGIN_VERSION", "2.5.0"), patch.object(proxy_module, "PROXY_VERSION", "3.0.0"):
        h = test_client.get("/").text
    t = h[h.index('<template id="stConnTemplate">'):h.index("</template>", h.index('<template id="stConnTemplate">'))]
    assert 'class="version-mismatch"' in t and "MultiProxy and the Skyrim mod versions do not match. Please update both." in t


def test_the_two_steps_are_worded_once_and_both_the_walk_through_and_settings_use_them():
    js = get_started_ui.JS
    assert "const SN_STEPS = [" in js and "  SN_STEPS[0],\n  SN_STEPS[1]," in js.replace("\r\n", "\n")
    assert "In SkyrimNet, go to <b>AI &gt; Providers</b> and press <b>+ Add Custom Provider</b>. Copy the Name and MultiProxy\\'s address, set <b>API Style</b> to Chat Completions, and leave the key blank." in js
    assert "In SkyrimNet: go to <b>AI &gt; Models</b>. For each job, pick the new provider and paste the model name." in js
    assert js.count("go to <b>AI &gt; Providers</b> and press <b>+ Add Custom Provider</b>. Copy the Name") == 1
    assert "SN_STEPS" in settings_ui.JS


@needs_node
def test_settings_draws_the_template_and_the_steps():
    harness = """
const tpl = { innerHTML: '<div class="st-conn"><span>http://127.0.0.1:8000/v1/chat/completions</span><button class="copy">Copy</button></div>' };
const document = { getElementById: id => (id === 'stConnTemplate' ? tpl : null) };
""" + "const SN_STEPS = ['one <b>AI &gt; Providers</b>', 'two <b>AI &gt; Models</b>'];\n"
    fn = settings_ui.JS[settings_ui.JS.index("function stConnHtml()"):settings_ui.JS.index("function stNote(key)")]
    src = harness + fn + "console.log(JSON.stringify(stConnHtml()));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        out = subprocess.run([NODE, f.name], capture_output=True, timeout=30)
    finally:
        os.unlink(f.name)
    html = json.loads(out.stdout.decode("utf-8"))
    assert "/v1/chat/completions" in html and ">Copy</button>" in html and '<ol class="st-steps"><li>one <b>AI &gt; Providers</b></li><li>two <b>AI &gt; Models</b></li></ol>' in html


def test_an_address_selects_whole():
    import selection_ui
    assert ".endpoint > span:first-child" in selection_ui.SELECTION_CSS, "the address in this section is one of those"


# ---- the walk-through ---------------------------------------------------------------------------------------------------------------------------
def test_the_walk_through_keeps_its_steps_and_its_jump_to_providers():
    js = get_started_ui.JS
    assert "Last step: tell SkyrimNet" in js and "1. Add MultiProxy as a provider" in js and "2. Choose it for the jobs you want" in js
    assert "Set one up on the Providers tab, then copy its model name from there." in js and "Open the Providers tab" in js
    assert "gsLeaveToSetup" in js and "showMainTab('setup')" in settings_ui.JS + js or True


# ---- the Overview ---------------------------------------------------------------------------------------------------------------------------------
@needs_node
def test_the_overview_row_says_connected_or_waiting_and_leaves_itself_out_when_not_measured():
    on, waiting, none = draw(base(skyrimnet={"connected": True})), draw(base(skyrimnet={"connected": False})), draw(base())
    assert '<b>SkyrimNet</b> Connected' in on and 'class="ov-dot ok"' in on.split("<b>SkyrimNet</b>")[0][-60:]
    assert '<b>SkyrimNet</b> Waiting for SkyrimNet' in waiting and 'class="ov-dot off"' in waiting.split("<b>SkyrimNet</b>")[0][-60:]
    assert "SkyrimNet</b>" not in none


@needs_node
def test_the_row_opens_the_settings_section_and_a_provider_row_opens_providers():
    d = base(skyrimnet={"connected": False})
    src = (HARNESS + "const calls = []; function openSettings(s) { calls.push(['settings', s]); } function showMainTab(t) { calls.push(['tab', t]); } function selectProvider(p) { calls.push(['select', p]); }\n" + ESC + "\n" +
           overview_ui.JS + "\nconst html = ovHtml(" + json.dumps(d) + ");\nconst m = html.match(/class=\"ov-sn\"(?: title=\"[^\"]*\")? onclick=\"ovGo\\((\\d+)\\)\"/); ovGo(+m[1]);\n"
           "const prov = ovJumps.findIndex(j => j.provider); ovGo(prov);\nconsole.log(JSON.stringify(calls));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
    try:
        out = subprocess.run([NODE, f.name], capture_output=True, timeout=30)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    calls = json.loads(out.stdout.decode("utf-8"))
    assert calls[0] == ["settings", "skyrimnet"] and calls[1:] == [["tab", "setup"], ["select", calls[2][1]]]


def test_the_overview_data_carries_it():
    d = overview.build(**{**BASE_ARGS, "skyrimnet": {"connected": True}})
    assert d["skyrimnet"] == {"connected": True, "line": ""}
    assert overview.build(**BASE_ARGS)["skyrimnet"] is None


BASE_ARGS = dict(tracking=False, figures=None, this_pc={"name": "Device", "host": "pc", "gpu": None, "usedMb": None, "totalMb": None}, local_models=[], local_speeds={}, pcs=[], providers=[],
                 health={}, uses={}, routes=[], decisions=None, voice=None, setup={"done": True, "left": []})


def test_a_request_from_the_dashboard_does_not_count_but_one_from_skyrimnet_does(proxy_module):
    pm = proxy_module
    pm._outside_client.update(at=None, agent="")
    browser = SimpleNamespace(headers={"origin": "http://127.0.0.1:8000", "user-agent": "Mozilla/5.0", "sec-fetch-site": "same-origin"})
    pm._note_outside_client(browser)
    assert pm._outside_client["at"] is None
    skyrimnet = SimpleNamespace(headers={"user-agent": "SkyrimNet/1.0", "content-type": "application/json"})
    pm._note_outside_client(skyrimnet)
    assert pm._outside_client["at"] is not None and pm._outside_client["agent"] == "SkyrimNet/1.0"
    first = pm._outside_client["at"]
    pm._note_outside_client(skyrimnet)
    assert pm._outside_client["at"] >= first      # the latest time is kept, so a second request moves it forward (a float, never compared for equality)


def test_the_route_reports_it(proxy_module, test_client):
    import asyncio
    proxy_module._outside_client.update(at=None, agent="")
    assert asyncio.run(proxy_module._overview_payload())["skyrimnet"] == {"connected": False, "line": "Waiting for SkyrimNet"}
    proxy_module._outside_client["at"] = __import__("time").time()      # now: "connected" means it asked in the last 2 minutes
    assert asyncio.run(proxy_module._overview_payload())["skyrimnet"]["connected"] is True
    assert "skyrimnet" in test_client.get("/stats/overview").json()


def test_the_help_pages_say_providers_and_settings():
    text = (ROOT / "docs" / "help" / "help-content.md").read_text(encoding="utf-8")
    assert "Setup tab" not in text and "Setup card" not in text
    assert "(**MultiProxy's address**, in **Settings** under **SkyrimNet connection**)" in text
    assert "Every model on the Providers tab has a handy" in text and "the provider's panel on the Providers tab" in text
