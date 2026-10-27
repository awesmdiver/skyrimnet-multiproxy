"""Tests for _resolve_skyrimnet_dir/_resolve_skyrimnet_dir_cached (2026-09-22) -- finding
SkyrimNet's own Data/SKSE/Plugins/SkyrimNet folder even under a Mod Organizer 2 (MO2) instance,
where SkyrimNet's real output can physically live under a sibling mods\\<Name>\\Data\\... or
overwrite\\Data\\... folder instead of directly under the linked root's own Data\\ (MO2's virtual
filesystem merge only exists inside the real Skyrim process, not this separate proxy).

Real report this fixes (2026-09-22, Discord/WabbaJack user): "Linked to your Skyrim install"
looked healthy while role chips and the tracker toggle silently failed, because every consumer
independently joined paths under the linked root with no MO2 awareness at all.
"""
import os

import pytest


def _make_skyrimnet_dir(base) -> None:
    """A "real-looking" SkyrimNet folder -- has a config subfolder, matching the one validity
    check every existing consumer already used (_find_skyrimnet_variants_doc's own os.listdir on
    config/) -- never invents a stricter/looser check than what was already there."""
    (base / "config").mkdir(parents=True)


class TestResolveSkyrimnetDir:
    def test_direct_path_is_found_first_no_mo2_search_needed(self, proxy_module, tmp_path):
        skyrim_root = tmp_path / "Skyrim Special Edition"
        direct = skyrim_root / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(direct)
        # A sibling "mods\" folder existing too (as it would for a real MO2 instance) must never
        # get preferred over an already-valid direct path -- confirmed by making it invalid/wrong
        # on purpose: if the resolver fell through to it anyway, this would return the wrong path.
        wrong = tmp_path / "mods" / "SomeOtherMod" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(wrong)
        assert proxy_module._resolve_skyrimnet_dir(str(skyrim_root)) == str(direct)

    def test_mo2_mods_sibling_is_found_when_direct_path_is_missing(self, proxy_module, tmp_path):
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"  # linked root -- no Data\SKSE\... under it at all
        stock_game.mkdir(parents=True)
        real = instance / "mods" / "SkyrimNet Output" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(real)
        assert proxy_module._resolve_skyrimnet_dir(str(stock_game)) == str(real)

    def test_overwrite_sibling_is_found_when_nothing_is_in_mods(self, proxy_module, tmp_path):
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"
        stock_game.mkdir(parents=True)
        real = instance / "overwrite" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(real)
        assert proxy_module._resolve_skyrimnet_dir(str(stock_game)) == str(real)

    def test_mods_sibling_wins_over_overwrite_when_both_exist(self, proxy_module, tmp_path):
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"
        stock_game.mkdir(parents=True)
        in_mods = instance / "mods" / "SkyrimNet Output" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        in_overwrite = instance / "overwrite" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(in_mods)
        _make_skyrimnet_dir(in_overwrite)
        assert proxy_module._resolve_skyrimnet_dir(str(stock_game)) == str(in_mods)

    def test_nothing_anywhere_returns_none(self, proxy_module, tmp_path):
        stock_game = tmp_path / "FakeInstance" / "Stock Game"
        stock_game.mkdir(parents=True)
        assert proxy_module._resolve_skyrimnet_dir(str(stock_game)) is None

    def test_a_bare_folder_with_no_config_subfolder_does_not_count(self, proxy_module, tmp_path):
        """A folder literally named "SkyrimNet" with nothing real inside it must not be accepted
        -- matches the task's own "never just a folder happens to be named SkyrimNet" rule."""
        skyrim_root = tmp_path / "Skyrim Special Edition"
        empty_skyrimnet_dir = skyrim_root / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        empty_skyrimnet_dir.mkdir(parents=True)  # no config/ subfolder inside
        assert proxy_module._resolve_skyrimnet_dir(str(skyrim_root)) is None

    def test_multiple_mods_candidates_deterministic_tiebreak_by_newest_yaml(self, proxy_module, tmp_path):
        """Two mods both wrote a real-looking SkyrimNet folder -- the one with the most recently
        modified SkyrimNet.yaml wins, not whichever os.listdir() happened to return first.
        Deliberately names the OLDER one first alphabetically, so an accidental
        "alphabetically/insertion-order first" implementation would pick the wrong one and fail
        this test."""
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"
        stock_game.mkdir(parents=True)

        older = instance / "mods" / "AAA Old SkyrimNet Mod" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        newer = instance / "mods" / "ZZZ New SkyrimNet Mod" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(older)
        _make_skyrimnet_dir(newer)
        older_yaml = older / "config" / "SkyrimNet.yaml"
        newer_yaml = newer / "config" / "SkyrimNet.yaml"
        older_yaml.write_text("telemetry:\n  metrics_enabled: false\n", encoding="utf-8")
        newer_yaml.write_text("telemetry:\n  metrics_enabled: false\n", encoding="utf-8")
        old_time = 1_700_000_000
        new_time = 1_800_000_000
        os.utime(older_yaml, (old_time, old_time))
        os.utime(newer_yaml, (new_time, new_time))

        assert proxy_module._resolve_skyrimnet_dir(str(stock_game)) == str(newer)

    def test_returns_none_when_the_linked_root_itself_does_not_exist(self, proxy_module, tmp_path):
        assert proxy_module._resolve_skyrimnet_dir(str(tmp_path / "does-not-exist")) is None


class TestResolveSkyrimnetDirCached:
    def test_caches_by_root_and_reflects_a_changed_root(self, proxy_module, tmp_path):
        root_a = tmp_path / "RootA"
        real_a = root_a / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(real_a)
        root_b = tmp_path / "RootB"  # nothing real under this one
        root_b.mkdir(parents=True)

        assert proxy_module._resolve_skyrimnet_dir_cached(str(root_a)) == str(real_a)
        # A different root is re-resolved, not served from root_a's own cached result.
        assert proxy_module._resolve_skyrimnet_dir_cached(str(root_b)) is None
        # Re-resolving the SAME root again still gives the same, correct answer (cache hit).
        assert proxy_module._resolve_skyrimnet_dir_cached(str(root_a)) == str(real_a)

    def test_does_not_re_scan_disk_for_the_same_root_twice(self, proxy_module, tmp_path, monkeypatch):
        """Confirms the caching decision actually caches -- not just that the returned value is
        correct, but that a second call for the SAME root never touches the filesystem again.
        Uses the MO2-fallback shape deliberately (not the direct path) -- that's the one branch
        that actually calls os.listdir(mods\\) at all, so this is a real, meaningful check, not
        one that would pass even without any caching."""
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"
        stock_game.mkdir(parents=True)
        real = instance / "mods" / "SkyrimNet Output" / "Data" / "SKSE" / "Plugins" / "SkyrimNet"
        _make_skyrimnet_dir(real)

        first = proxy_module._resolve_skyrimnet_dir_cached(str(stock_game))
        assert first == str(real)

        real_listdir = os.listdir
        def _fail_if_called(path):
            raise AssertionError(f"disk was re-scanned for an already-cached root: {path}")
        monkeypatch.setattr(os, "listdir", _fail_if_called)
        try:
            second = proxy_module._resolve_skyrimnet_dir_cached(str(stock_game))
        finally:
            monkeypatch.setattr(os, "listdir", real_listdir)
        assert second == str(real)
