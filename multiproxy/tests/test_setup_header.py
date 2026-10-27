"""The Providers tab's top card, and the SkyrimNet connection section of Settings that took its address over (2026-10-01, reworded 2026-10-02 "Simple is the word"): Start here steps, the address to paste into
SkyrimNet, and a line only when the Skyrim mod is missing. The Version row is gone (Settings shows both versions).
"""
import asyncio
import re
from unittest.mock import patch


def _card(html: str) -> str:
    """The Providers pane's top, plus the markup of the Settings > SkyrimNet connection section (the page's own template, which the Settings page draws)."""
    start = html.index('<div id="paneSetup" style="display:none">')
    top = html[start:html.index("Choose a provider", start)]
    t = html.index('<template id="stConnTemplate">')
    return top + "<!-- SKYRIMNET-CONNECTION -->" + html[t + len('<template id="stConnTemplate">'):html.index("</template>", t)]


def test_the_claude_only_details_are_gone(test_client):
    card = _card(test_client.get("/").text)
    for gone in ("Template", "Default model", "Claude CLI", "bytes", "grid4", "Endpoint"):
        card = card.replace("API Endpoint", "")      # (SkyrimNet's own field name, quoted in the hint under each address)
        assert gone not in card, gone


def test_start_here_steps_come_first_then_the_address(proxy_module, test_client):
    card = _card(test_client.get("/").text)
    assert card.index("Start here") < card.index("SKYRIMNET-CONNECTION")
    steps = re.findall(r"<li>(.*?)</li>", card)
    assert [s.replace("&rsquo;", "\u2019") for s in steps] == [
        "Pick a provider below that you already pay for or have a key for.",
        "Paste your key and click Save (or Sign in).",
        "Wait for the dot next to its name to turn green.",
        "Copy a model name from the list.",
        "Paste the model name and MultiProxy\u2019s address into SkyrimNet.",
    ]
    assert "/v1/chat/completions" in card and ">Copy</button>" in card


def test_there_is_no_version_row(proxy_module, test_client):
    with patch.object(proxy_module, "PLUGIN_VERSION", proxy_module.PROXY_VERSION):
        card = _card(test_client.get("/").text)
    assert 'class="version-row"' not in card and "Version" not in card and f"Mod {proxy_module.PROXY_VERSION}" not in card
    assert 'class="help-q"' not in card


def test_mod_not_detected_stays_with_a_question_mark_that_explains_it_and_opens_settings(proxy_module, test_client):
    with patch.object(proxy_module, "PLUGIN_VERSION", None):
        card = _card(test_client.get("/").text)
    # The "Mod not detected" row (with its ?) is now the Settings page's own "Skyrim mod" section, a few lines above the SkyrimNet connection section.
    import settings_ui
    assert "Mod not detected" not in card and "Mod not detected" in settings_ui.JS and 'id="st-mod"' in settings_ui.JS and "Plugin" not in card


def test_the_mismatch_warning_says_what_to_do(proxy_module, test_client):
    with patch.object(proxy_module, "PLUGIN_VERSION", "2.5.0"), patch.object(proxy_module, "PROXY_VERSION", "3.0.0"):
        card = _card(test_client.get("/").text)
    assert 'class="version-mismatch"' in card and "MultiProxy and the Skyrim mod versions do not match. Please update both." in card


def test_the_default_model_now_lives_on_claudes_panel(proxy_module):
    providers = {p["id"]: p for p in asyncio.run(proxy_module._dashboard_providers())}
    label = proxy_module._claude_model_label(proxy_module._default_claude_model())
    assert providers["claude"]["help"] == f"No key needed. A job with no model named uses {label}. (More in Help)"


def test_claudes_own_state_is_still_on_its_panel_with_and_without_the_program(proxy_module):
    with patch.object(proxy_module, "CLAUDE_PATH", None):
        p = {x["id"]: x for x in asyncio.run(proxy_module._dashboard_providers())}["claude"]
    assert p["status"] == "Program not installed" and p["state"] == "off"
    with patch.object(proxy_module, "CLAUDE_PATH", "/x/claude"), patch.object(proxy_module.auth, "last_error", None), \
            patch.object(type(proxy_module.auth), "is_ready", property(lambda self: True)):
        p = {x["id"]: x for x in asyncio.run(proxy_module._dashboard_providers())}["claude"]
    assert p["state"] == "ok" and p["status"] == "Ready"
    with patch.object(proxy_module, "CLAUDE_PATH", "/x/claude"), patch.object(proxy_module.auth, "last_error", "Not logged in"):
        p = {x["id"]: x for x in asyncio.run(proxy_module._dashboard_providers())}["claude"]
    assert p["status"] == "Not available" and "Not logged in" not in p["help"]


def test_a_request_with_no_model_still_goes_where_it_did(proxy_module):
    """Behaviour unchanged: no model named -> the default (newest Sonnet), routed to Claude."""
    assert proxy_module._default_provider_id() == "claude"
    assert proxy_module._default_claude_model() == proxy_module._default_claude_model()
    assert not proxy_module.is_unclaimed_slash_model(proxy_module._default_claude_model())
