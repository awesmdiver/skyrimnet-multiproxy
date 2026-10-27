"""Every tab opens with a short description that stays (2026-10-03).

The text lives in the tab's own fixed markup, never inside an area a script rebuilds (Routing's `#rtBody` redraws every two seconds and had its own words in it, so what a
player saw on first paint was replaced), in one shared style, and it is not a Start here box (those can be hidden).
"""
import re

import pytest

import tab_desc_ui

PANES = {"setup": "paneSetup", "local": "paneLocal", "other": "paneOther", "voice": "paneVoice", "models": "paneModels", "routing": "paneRouting", "quick": "paneQuick",
         "speeds": "paneSpeeds", "help": "paneHelp", "settings": "paneSettings"}
# the director's final wording (Gemini pass, 2026-10-03), exactly
FINAL = {
    "setup": "Pick the AI services you want to use.",      # (the Providers tab since 2026-10-04: the addresses moved to Settings)
    "local": "Download and run free AI models on this device, no accounts needed.",
    "other": "Borrow the graphics card of another computer in your house to share the heavy lifting.",
    "voice": "Bring your NPCs to life with real spoken voices, generated right here at home.",
    "models": "View every downloaded model across your PCs, see where each is used, and remove the ones you no longer need.",
    "routing": "Choose which AI handles each conversation or background job, and who takes over when they're busy.",
    "quick": "Chat with any model to make sure it's answering nicely before you load up the game.",
    "speeds": "Check how fast and reliably your models answer, and see how big SkyrimNet's prompts are so you can set each model's context.",
    "help": "Plain, friendly step-by-step guides for every part of MultiProxy.",
    "settings": "Adjust how MultiProxy looks, how it starts with Skyrim, and how it updates.",
}

# areas a script rebuilds (innerHTML / replaceChildren) inside each pane: a description in one of these would be thrown away
REBUILT = ["rtBody", "rtLive", "opBody", "vcBody", "stCard", "stGpuCard", "panel", "gsCard", "helpPage", "healthGrid", "speedRows"]


@pytest.fixture
def page(proxy_module, test_client, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    return test_client.get("/").text


def _pane(html: str, pane_id: str) -> str:
    start = html.index(f'id="{pane_id}"')
    ends = [html.find(f'id="{p}"', start + 10) for p in PANES.values() if p != pane_id] + [html.find('id="paneStart"', start + 10), html.find("<script>", start + 10)]
    return html[start:min(e for e in ends if e > 0)]


def test_the_wording_is_the_final_wording_exactly():
    assert tab_desc_ui.TEXT == FINAL


@pytest.mark.parametrize("key", list(PANES))
def test_every_tab_has_exactly_one_description_with_its_final_wording(page, key):
    pane = _pane(page, PANES[key])
    found = re.findall(r'<p class="tab-desc" data-tab-desc="(\w+)">(.*?)</p>', pane)
    assert [k for k, _ in found] == [key], key
    assert found[0][1].replace("&#x27;", "'") == FINAL[key]


@pytest.mark.parametrize("key", list(PANES))
def test_the_description_is_the_first_thing_in_the_fixed_page_and_outside_every_rebuilt_area(page, key):
    pane = _pane(page, PANES[key])
    body = pane[pane.index(">") + 1:].lstrip()
    # the tab's own first element (a hidden modal holder may sit before it, never an area that is redrawn)
    assert body.startswith('<p class="tab-desc"'), key
    desc_at = pane.index('class="tab-desc"')
    for rebuilt in REBUILT:
        i = pane.find(f'id="{rebuilt}"')
        assert i == -1 or desc_at < i, f"{key}: the description sits after (possibly inside) #{rebuilt}"


def test_there_is_one_shared_style_built_once_with_theme_variables(page):
    assert page.count(".tab-desc {") == 1
    css = tab_desc_ui.CSS
    assert "var(--muted)" in css and not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", css)
    assert "max-width" not in css, "text uses the width its card has"


def test_it_is_not_a_start_here_box(page):
    for key in PANES:
        m = re.search(r'<p class="tab-desc"[^>]*>', _pane(page, PANES[key]))
        assert "data-sh" not in m.group(0) and "sh-box" not in m.group(0), "a Start here box can be hidden; this can't"


def test_routings_own_redraw_no_longer_owns_any_description_and_the_card_it_draws_is_not_one():
    """Routing's body is rebuilt by rtRender every couple of seconds (and shows 'Looking at your models...' until it loads), so the description is outside it."""
    import routing_ui
    pane = routing_ui.ROUTING_PANE_HTML
    assert pane.index("tab-desc") < pane.index('id="rtBody"')
    assert "tab-desc" not in routing_ui.ROUTING_JS


def test_every_pane_markup_is_built_from_the_one_helper():
    import help_ui, other_pcs_ui, routing_ui, settings_ui, voice_ui
    for mod, key in ((help_ui.HELP_PANE_HTML, "help"), (other_pcs_ui.OTHER_PCS_PANE_HTML, "other"), (routing_ui.ROUTING_PANE_HTML, "routing"),
                     (settings_ui.PANE_HTML, "settings"), (voice_ui.VOICE_PANE_HTML, "voice")):
        assert tab_desc_ui.html(key) in mod
