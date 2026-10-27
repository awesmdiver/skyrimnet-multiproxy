"""The dashboard's tab bar (2026-10-01): Setup · Local models · Quick Test · Other devices · Speeds.
The Local models card and the Quick Test card each moved out of the long Setup scroll onto their own tab.
"""
import re
from unittest.mock import patch


def _page(test_client):
    return test_client.get("/").text


def _pane(html: str, pane_id: str) -> str:
    """The markup of one tab pane: from its opening div up to the next pane's opening div."""
    ids = [m.group(1) for m in re.finditer(r'<div id="(pane(?:Setup|Local|Quick|Speeds|Other|Voice|Routing))"', html)]
    start = html.index(f'<div id="{pane_id}"')
    later = [html.index(f'<div id="{i}"') for i in ids if html.index(f'<div id="{i}"') > start]
    return html[start:min(later)] if later else html[start:html.index("<script>", start)]


def _tabs(html: str) -> list:
    bar = html[html.index('<div class="tabs">'):html.index('<div id="paneOverview">')]
    return re.findall(r"""<div class="tab[^"]*" id="tab(\w+)" onclick="showMainTab\('(\w+)'\)">([^<]+)</div>""", bar)


def test_tab_bar_order_and_names(test_client):
    assert _tabs(_page(test_client)) == [
        ("Overview", "overview", "Overview"), ("Setup", "setup", "Providers"), ("Local", "local", "Local device"), ("Other", "other", "Other devices"),
        ("Voice", "voice", "Voice"), ("Decisions", "decisions", "Decisions"), ("Models", "models", "Models"), ("Routing", "routing", "Routing"), ("Quick", "quick", "Quick Test"), ("Speeds", "speeds", "Speeds"), ("Help", "help", "Help")]


def test_speeds_stays_optional_and_the_rest_remain(proxy_module, test_client):
    with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
        names = [t[2] for t in _tabs(_page(test_client))]
    assert names == ["Overview", "Providers", "Local device", "Other devices", "Voice", "Decisions", "Models", "Routing", "Quick Test", "Help"]


def test_overview_is_active_first_and_the_others_start_hidden(test_client):
    html = _page(test_client)
    assert 'class="tab active" id="tabOverview"' in html and 'class="tab" id="tabSetup"' in html
    for pane in ("paneSetup", "paneLocal", "paneQuick", "paneOther", "paneVoice", "paneSpeeds"):
        assert re.search(rf'<div id="{pane}" style="display:none">', html), pane
    assert '<div id="paneOverview">' in html


def test_the_local_models_card_is_alone_on_its_own_tab(test_client):
    html = _page(test_client)
    local, setup = _pane(html, "paneLocal"), _pane(html, "paneSetup")
    assert 'id="localCard"' in local and 'id="lmMain"' in local
    assert 'id="localCard"' not in setup and "Quick Test" not in local


def test_the_quick_test_card_is_alone_on_its_own_tab_with_all_its_controls(test_client):
    html = _page(test_client)
    quick, setup = _pane(html, "paneQuick"), _pane(html, "paneSetup")
    for needle in ("<h3>Try a provider before you play.</h3>", 'id="testProvider"', 'id="testModel"', 'id="testScenario"', 'id="sysPrompt"',
                   'id="userMsg"', 'id="extraContext"', 'id="testBtn"', "openQuickTestPanel()"):
        assert needle in quick, needle
        assert needle not in setup, needle


def test_setup_keeps_everything_else_in_its_order(test_client):
    setup = _pane(_page(test_client), "paneSetup")
    assert setup.index("Start here") < setup.index("Choose a provider") < setup.index('id="panel"')
    assert "Connect to SkyrimNet" not in setup and 'id="addrChat"' not in setup and "api-style" not in setup, "the SkyrimNet connection details moved to Settings"
    assert 'providerPick' not in setup and 'id="chips"' in setup


def test_other_pcs_and_speeds_panes_are_still_there(test_client):
    html = _page(test_client)
    assert 'id="paneOther"' in html and 'id="opBody"' in html and 'id="paneSpeeds"' in html


def test_the_old_slot_that_moved_the_local_card_into_other_pcs_is_gone(test_client):
    html = _page(test_client)
    assert "opLocalSlot" not in html and "opMovedLocal" not in html


def test_switching_tabs_knows_every_pane_and_refreshes_the_local_card(test_client):
    html = _page(test_client)
    fn = html[html.index("function showMainTab"):]
    fn = fn[:fn.index("\n}\n") + 3]
    for key in ("setup", "local", "quick", "other", "speeds"):
        assert f"'{key}'" in fn, key
    assert "lmOpened()" in fn and "opOpened()" in fn      # (lmOpened() refreshes the local card: commit 602b3d8 replaced the direct lmRefresh() call)


def test_the_model_server_mode_is_retired(proxy_module, test_client):
    """2026-10-01: the Courier replaces the Model Server. No model-server page mode, and the Speeds tab is always there."""
    html = _page(test_client)
    assert "MODEL_SERVER" not in html and "This device is a model server" not in html
    assert [t[2] for t in _tabs(html)] == ["Overview", "Providers", "Local device", "Other devices", "Voice", "Decisions", "Models", "Routing", "Quick Test", "Speeds", "Help"]
    assert 'id="paneSpeeds"' in html


def test_an_old_model_servers_leftover_setting_changes_nothing_on_the_page(proxy_module, test_client):
    proxy_module._save_config({"model_server": True, "share": {"enabled": True, "code": "ABC-DEF"}})
    html = _page(test_client)
    assert "MODEL_SERVER" not in html and 'id="paneSpeeds"' in html


def test_help_text_names_the_right_tab(proxy_module):
    import asyncio
    providers = {p["id"]: p for p in asyncio.run(proxy_module._dashboard_providers())}
    assert providers["local"]["help"] == "Runs on this device. No subscription needed. Download and manage models on the Local device tab. (More in Help)"
    assert providers["local"]["empty"] == "Nothing downloaded yet. Go to the Local device tab to get a model."
    assert providers["remote"]["help"] == "These are models running on your other devices. (More in Help)"


def test_error_messages_point_at_the_tab_not_a_card():
    import local_models
    src = open(local_models.__file__, encoding="utf-8").read()
    assert "Local models card." not in src
    assert "Add it on the Local device tab." in src and "Start it from the dashboard's Local device tab." in src


def test_the_always_on_ready_pill_is_gone_and_the_tabs_follow_the_tagline(proxy_module, test_client):
    """The page only loads while the proxy runs, so a green "Ready" could never say anything else; each
    provider's own dot is the real status."""
    for _ in (0,):
        html = _page(test_client)
        assert 'class="status"' not in html and ">Ready<" not in html and ".status {" not in html
        head = html[html.index('<div class="subtitle">'):html.index('<div id="paneOverview">')]
        assert head.index("</div>") < head.index('<div class="tabs">')
        assert "<span" not in head.split('<div class="tabs">')[0], "nothing between the tagline and the tab bar"


def test_there_is_no_other_test_shortcut_that_would_scroll_to_nothing(test_client):
    """Nothing besides the Quick Test tab itself launches a test, so no link needs rerouting."""
    html = _page(test_client)
    launchers = re.findall(r'onclick="[^"]*(?:openQuickTestPanel|testChat)\(', html)
    assert len(launchers) == 1
    assert "openQuickTestPanel()" in _pane(html, "paneQuick")
