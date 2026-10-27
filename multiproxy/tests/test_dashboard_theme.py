"""Tests for the dashboard wearing the player's SkyrimNet theme (2026-09-30): the three colours in
<SkyrimNet>/config/Dashboard.yaml (accent_color / bg_color / text_color) become CSS variables, with
light vs dark decided from the background and a Dragonborn fallback for anything unreadable."""
import pytest

DRAGONBORN = {"accent": "#c9a84c", "bg": "#18181c", "text": "#e8e6e1"}

# SkyrimNet's own six presets: (accent, bg, text, is_light)
PRESETS = {
    "dragonborn": ("#c9a84c", "#18181c", "#e8e6e1", False),
    "parchment": ("#a07218", "#eae6df", "#1c1a16", True),
    "stormcloak": ("#5b9bf5", "#0a0c0f", "#d8dce6", False),
    "silver-blood": ("#46627e", "#d3d8df", "#1a1c20", True),
    "imperial": ("#c44040", "#141112", "#cfb488", False),
    "terminal": ("#4ade80", "#0b0f1a", "#4ade80", False),
}


def _link(tmp_path, dashboard_yaml_text):
    """A fake linked Skyrim root whose SkyrimNet config folder holds the given Dashboard.yaml
    (None = folder exists but the file does not)."""
    root = tmp_path / "Skyrim"
    config = root / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
    config.mkdir(parents=True)
    if dashboard_yaml_text is not None:
        (config / "Dashboard.yaml").write_text(dashboard_yaml_text, encoding="utf-8")
    return str(root)


def _yaml(accent, bg, text, preset="custom"):
    return f"theme_preset: {preset}\naccent_color: '{accent}'\nbg_color: '{bg}'\ntext_color: '{text}'\nother: 1\n"


class TestReadTheme:
    def test_reads_the_three_colours(self, proxy_module, tmp_path):
        root = _link(tmp_path, _yaml("#d4a855", "#18181c", "#e8e6e1", "dragonborn"))
        assert proxy_module._read_skyrimnet_theme(root) == {
            "accent": "#d4a855", "bg": "#18181c", "text": "#e8e6e1"}

    def test_custom_colours_are_used_not_the_preset_name(self, proxy_module, tmp_path):
        root = _link(tmp_path, _yaml("#ff00aa", "#101820", "#f0f0f0", "dragonborn"))
        assert proxy_module._read_skyrimnet_theme(root)["accent"] == "#ff00aa"

    def test_short_and_uppercase_hex_are_normalised(self, proxy_module, tmp_path):
        root = _link(tmp_path, _yaml("#F0A", "#ABCDEF", "#000", "x"))
        assert proxy_module._read_skyrimnet_theme(root) == {
            "accent": "#ff00aa", "bg": "#abcdef", "text": "#000000"}

    @pytest.mark.parametrize("name", list(PRESETS))
    def test_every_builtin_preset_round_trips(self, proxy_module, tmp_path, name):
        accent, bg, text, _ = PRESETS[name]
        root = _link(tmp_path, _yaml(accent, bg, text, name))
        assert proxy_module._read_skyrimnet_theme(root) == {"accent": accent, "bg": bg, "text": text}


class TestFallback:
    def test_nothing_linked(self, proxy_module):
        assert proxy_module._read_skyrimnet_theme("") == DRAGONBORN

    def test_linked_root_without_skyrimnet(self, proxy_module, tmp_path):
        assert proxy_module._read_skyrimnet_theme(str(tmp_path)) == DRAGONBORN

    def test_missing_dashboard_yaml(self, proxy_module, tmp_path):
        assert proxy_module._read_skyrimnet_theme(_link(tmp_path, None)) == DRAGONBORN

    @pytest.mark.parametrize("text", [
        "",                                  # empty file
        ": : not yaml [",                    # unparseable
        "- just\n- a list\n",                # not a mapping
        "accent_color: '#c9a84c'\n",         # two of three colours missing
        _yaml("not-a-colour", "#18181c", "#e8e6e1"),
        _yaml("#c9a84c", "rgb(1,2,3)", "#e8e6e1"),
        _yaml("#c9a84c", "#18181c", "#12345"),
        "accent_color: 5\nbg_color: 6\ntext_color: 7\n",
    ])
    def test_unreadable_or_invalid_file(self, proxy_module, tmp_path, text):
        assert proxy_module._read_skyrimnet_theme(_link(tmp_path, text)) == DRAGONBORN

    def test_never_raises_even_if_the_resolver_blows_up(self, proxy_module, monkeypatch):
        def boom(_):
            raise RuntimeError("nope")
        monkeypatch.setattr(proxy_module, "_resolve_skyrimnet_dir_cached", boom)
        assert proxy_module._read_skyrimnet_theme("C:/anything") == DRAGONBORN


class TestLightOrDark:
    @pytest.mark.parametrize("name", list(PRESETS))
    def test_builtin_presets(self, proxy_module, name):
        _, bg, _, is_light = PRESETS[name]
        assert proxy_module._theme_is_light(bg) is is_light

    def test_pure_extremes(self, proxy_module):
        assert proxy_module._theme_is_light("#ffffff") is True
        assert proxy_module._theme_is_light("#000000") is False


class TestThemeCss:
    def test_uses_the_players_colours_and_dark_status_colours(self, proxy_module, tmp_path):
        root = _link(tmp_path, _yaml("#d4a855", "#18181c", "#e8e6e1"))
        css = proxy_module._dashboard_theme_css(root)
        assert "--accent:#d4a855" in css and "--bg:#18181c" in css and "--text:#e8e6e1" in css
        assert "--ok:#7cefab" in css and "--err:#f26060" in css and "color-scheme:dark" in css

    def test_light_theme_gets_light_status_colours(self, proxy_module, tmp_path):
        accent, bg, text, _ = PRESETS["parchment"]
        css = proxy_module._dashboard_theme_css(_link(tmp_path, _yaml(accent, bg, text)))
        assert "--ok:#136b33" in css and "--err:#ab1d1d" in css and "color-scheme:light" in css

    def test_fallback_css_is_dragonborn(self, proxy_module):
        css = proxy_module._dashboard_theme_css("")
        assert "--accent:#c9a84c" in css and "--bg:#18181c" in css


class TestDashboardPage:
    def test_page_carries_the_theme_and_no_old_slate_palette(self, proxy_module, test_client, tmp_path, monkeypatch):
        accent, bg, text, _ = PRESETS["stormcloak"]
        monkeypatch.setattr(proxy_module, "skyrim_install_path", _link(tmp_path, _yaml(accent, bg, text)))
        proxy_module._skyrimnet_dir_cache["root"] = None
        html = test_client.get("/").text
        assert "--accent:#5b9bf5" in html and "--bg:#0a0c0f" in html
        for old in ("#0f172a", "#1e293b", "#334155", "#3b82f6", "#94a3b8", "#e2e8f0"):
            assert old not in html

    def test_theme_is_re_read_on_every_page_load(self, proxy_module, test_client, tmp_path, monkeypatch):
        root = _link(tmp_path, _yaml("#111111", "#222222", "#eeeeee"))
        monkeypatch.setattr(proxy_module, "skyrim_install_path", root)
        proxy_module._skyrimnet_dir_cache["root"] = None
        assert "--accent:#111111" in test_client.get("/").text
        cfg = proxy_module._resolve_skyrimnet_dir_cached(root)
        import os
        with open(os.path.join(cfg, "config", "Dashboard.yaml"), "w", encoding="utf-8") as f:
            f.write(_yaml("#abcdef", "#222222", "#eeeeee"))
        assert "--accent:#abcdef" in test_client.get("/").text

    def test_quick_test_panel_is_part_of_the_page_so_it_takes_the_same_theme(self, proxy_module, test_client):
        import re
        import quicktest_ui
        assert not re.search(r"window\.open\(\s*(''|\"\")", test_client.get("/").text), "no blank pop-up window (the Claude sign-in link opens its real address in a tab, on purpose)"
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", quicktest_ui.CSS), "only the theme's own variables, never a literal colour"

    def test_nothing_linked_still_renders_with_dragonborn(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "skyrim_install_path", "")
        res = test_client.get("/")
        assert res.status_code == 200 and "--accent:#c9a84c" in res.text
