"""Tests for the dashboard's version display (2026-09-20) -- PROXY_VERSION alongside the
PLUGIN_VERSION the SKSE plugin passes via --plugin-version=X.Y.Z, and a visible warning when the
two are both known and don't match. Real motivation: the plugin shipped v3.0.0 while its own log
still said v2.5.0, because only one of two places that needed a version bump got one -- this is
meant to make that class of mistake impossible to miss on the next release.
"""
from unittest.mock import patch


class TestProxyVersionAlwaysShown:
    def test_dashboard_shows_the_proxy_version(self, proxy_module, test_client):
        # Since "Simple is the word" the version lives on the Settings page ("SkyrimNet MultiProxy <version>"),
        # not in the dashboard header: its state carries the number and settings_ui renders it.
        import settings_ui
        assert test_client.get("/config/settings").json()["about"]["version"] == proxy_module.PROXY_VERSION
        assert "SkyrimNet MultiProxy ' + esc(a.version" in settings_ui.JS


class TestPluginVersionDisplay:
    def test_shows_not_detected_when_plugin_version_is_absent(self, proxy_module, test_client):
        with patch.object(proxy_module, "PLUGIN_VERSION", None):
            html = test_client.get("/").text
        assert "Mod not detected" in html

    def test_shows_the_plugin_version_when_present(self, proxy_module, test_client):
        with patch.object(proxy_module, "PLUGIN_VERSION", "3.0.0"):
            mod = test_client.get("/config/settings").json()["mod"]
        # Settings shows it as "Detected (version 3.0.0)".
        import settings_ui
        assert mod["detected"] is True and mod["version"] == "3.0.0"
        assert "'Detected' + (mod.version ? ' (version ' + esc(mod.version)" in settings_ui.JS


class TestVersionMismatchNote:
    def test_no_mismatch_note_when_plugin_version_is_unknown(self, proxy_module, test_client):
        # Note: the CSS rule ".version-mismatch { ... }" is always present in <style> -- checking
        # for the class="version-mismatch" DIV itself is what actually distinguishes "rendered"
        # from "just defined in the stylesheet".
        with patch.object(proxy_module, "PLUGIN_VERSION", None):
            html = test_client.get("/").text
        assert 'class="version-mismatch"' not in html

    def test_no_mismatch_note_when_versions_match(self, proxy_module, test_client):
        with patch.object(proxy_module, "PLUGIN_VERSION", proxy_module.PROXY_VERSION):
            html = test_client.get("/").text
        assert 'class="version-mismatch"' not in html

    def test_mismatch_note_appears_when_both_known_and_different(self, proxy_module, test_client):
        with patch.object(proxy_module, "PLUGIN_VERSION", "2.5.0"), \
             patch.object(proxy_module, "PROXY_VERSION", "3.0.0"):
            html = test_client.get("/").text
        assert 'class="version-mismatch"' in html
        assert "don" in html.lower() and "match" in html.lower()  # the plain-language note itself


class TestPluginVersionArgParsing:
    def test_a_beta_tail_does_not_count_as_a_mismatch_with_the_mod(self, proxy_module, test_client):
        """The mod's version is a plain number (4.0.0); MultiProxy's is 4.0.0-beta.1: the same release, so no warning."""
        with patch.object(proxy_module, "PLUGIN_VERSION", "4.0.0"), patch.object(proxy_module, "PROXY_VERSION", "4.0.0-beta.1"):
            html = test_client.get("/").text
        assert 'class="version-mismatch"' not in html

    def test_plugin_version_arg_is_parsed_from_argv(self, monkeypatch):
        """Exercises the real argparse call at the top of multiproxy.py, not just the resulting
        module attribute -- confirms --plugin-version=X.Y.Z is actually recognized."""
        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--plugin-version", default=None)
        parsed, _unknown = parser.parse_known_args(["--plugin-version=3.0.0", "--some-other-flag"])
        assert parsed.plugin_version == "3.0.0"

    def test_unknown_arguments_are_ignored_not_fatal(self):
        """This parsing runs at multiproxy.py's own import time, against the REAL process argv --
        which, under pytest, is pytest's own CLI flags (test paths, -q, coverage flags, ...).
        parse_known_args must never raise on those, or importing multiproxy.py under pytest would
        itself be broken."""
        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--plugin-version", default=None)
        parsed, unknown = parser.parse_known_args(["tests/", "-q", "--cov=proxy"])
        assert parsed.plugin_version is None
        assert unknown == ["tests/", "-q", "--cov=proxy"]
