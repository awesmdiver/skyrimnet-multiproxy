"""MultiProxy finds the SkyrimNet folder for Mod Organizer 2 players and asks when it cannot (2026-10-25). Stand-in folders in a scratch area; no real MO2 or game folder."""
import os
import socket
import time

import pytest

import skyrimnet_finder as sf

PLUGIN = ("SKSE", "Plugins", "SkyrimNet")


def make(base, *parts, config=True, yaml_age=0):
    d = base.joinpath(*parts)
    (d / "config").mkdir(parents=True, exist_ok=True) if config else d.mkdir(parents=True, exist_ok=True)
    if config:
        y = d / "config" / "SkyrimNet.yaml"
        y.write_text("x", encoding="utf-8")
        t = time.time() - yaml_age
        os.utime(y, (t, t))
    return d


class TestLayouts:
    def test_a_mod_folder_with_skse_directly_in_it(self, tmp_path):
        """The normal MO2 layout (mods store the CONTENTS of Data): the case that failed for a Steam-folder player."""
        game = tmp_path / "SteamLibrary" / "common" / "Skyrim Special Edition"
        game.mkdir(parents=True)
        real = make(tmp_path, "Modlists", "Keizaal", "mods", "skyrimnet-beta28", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "Modlists" / "Keizaal")) == str(real)
        assert sf.from_game_root(str(game)) is None                      # a game folder with no instance beside it: nothing to find without the player's help

    def test_with_a_data_folder_in_the_mod_too(self, tmp_path):
        real = make(tmp_path, "inst", "mods", "SkyrimNet Output", "Data", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "inst")) == str(real)

    def test_in_overwrite_with_and_without_data(self, tmp_path):
        a = make(tmp_path, "a", "overwrite", *PLUGIN)
        b = make(tmp_path, "b", "overwrite", "Data", *PLUGIN)
        (tmp_path / "a" / "mods").mkdir()
        (tmp_path / "b" / "mods").mkdir()
        assert sf.from_pick(str(tmp_path / "a")) == str(a) and sf.from_pick(str(tmp_path / "b")) == str(b)

    def test_mods_beat_overwrite(self, tmp_path):
        in_mods = make(tmp_path, "i", "mods", "m", *PLUGIN)
        make(tmp_path, "i", "overwrite", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "i")) == str(in_mods)

    def test_several_candidates_the_newest_yaml_wins(self, tmp_path):
        make(tmp_path, "i", "mods", "old", *PLUGIN, yaml_age=5000)
        new = make(tmp_path, "i", "mods", "new", *PLUGIN, yaml_age=10)
        make(tmp_path, "i", "mods", "older", "Data", *PLUGIN, yaml_age=9000)
        assert sf.from_pick(str(tmp_path / "i")) == str(new)

    def test_a_folder_named_skyrimnet_without_config_is_not_it(self, tmp_path):
        make(tmp_path, "i", "mods", "m", *PLUGIN, config=False)
        assert sf.from_pick(str(tmp_path / "i")) is None
        assert sf.from_pick(str(tmp_path / "i" / "mods" / "m" / "SKSE" / "Plugins" / "SkyrimNet")) is None

    def test_the_old_layouts_still_work(self, tmp_path):
        game = tmp_path / "Skyrim"
        direct = make(game, "Data", *PLUGIN)
        assert sf.from_game_root(str(game)) == str(direct)
        inst = tmp_path / "inst"
        stock = inst / "Stock Game"
        stock.mkdir(parents=True)
        mod = make(inst, "mods", "x", "Data", *PLUGIN)
        assert sf.from_game_root(str(stock)) == str(mod)


class TestProfileOrder:
    def test_the_active_profile_decides_and_disabled_mods_do_not_count(self, tmp_path):
        inst = tmp_path / "inst"
        newer_but_disabled = make(inst, "mods", "Disabled", *PLUGIN, yaml_age=1)
        high = make(inst, "mods", "High", *PLUGIN, yaml_age=900)
        low = make(inst, "mods", "Low", *PLUGIN, yaml_age=800)
        (inst / "profiles" / "Main").mkdir(parents=True)
        (inst / "profiles" / "Main" / "modlist.txt").write_text("# This file was automatically generated\n+High\n-Disabled\n+Low\n", encoding="utf-8")
        (inst / "ModOrganizer.ini").write_text("[General]\nselected_profile=@ByteArray(Main)\n", encoding="utf-8")
        assert sf.from_pick(str(inst)) == str(high)                       # MO2 writes the highest priority first
        assert str(newer_but_disabled) != sf.from_pick(str(inst)) and str(low) != sf.from_pick(str(inst))

    def test_an_unreadable_profile_falls_back_to_the_newest_yaml(self, tmp_path):
        inst = tmp_path / "inst"
        make(inst, "mods", "Old", *PLUGIN, yaml_age=900)
        new = make(inst, "mods", "New", *PLUGIN, yaml_age=5)
        (inst / "ModOrganizer.ini").write_text("[General]\nselected_profile=@ByteArray(Missing)\n", encoding="utf-8")
        assert sf.from_pick(str(inst)) == str(new)

    def test_a_profile_name_cannot_walk_out(self, tmp_path):
        inst = tmp_path / "inst"
        new = make(inst, "mods", "New", *PLUGIN)
        (inst / "ModOrganizer.ini").write_text("[General]\nselected_profile=..\\..\\evil\n", encoding="utf-8")
        assert sf.from_pick(str(inst)) == str(new)


class TestEachKindOfPick:
    def test_the_skyrimnet_folder_itself(self, tmp_path):
        real = make(tmp_path, "mods", "m", *PLUGIN)
        assert sf.from_pick(str(real)) == str(real)

    def test_the_mods_own_folder(self, tmp_path):
        real = make(tmp_path, "mods", "m", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "mods" / "m")) == str(real)

    def test_an_instance_folder(self, tmp_path):
        real = make(tmp_path, "inst", "mods", "m", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "inst")) == str(real)

    def test_a_mods_folder(self, tmp_path):
        real = make(tmp_path, "inst", "mods", "m", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "inst" / "mods")) == str(real)

    def test_a_game_folder(self, tmp_path):
        real = make(tmp_path, "Skyrim", "Data", *PLUGIN)
        assert sf.from_pick(str(tmp_path / "Skyrim")) == str(real)

    def test_a_folder_with_nothing_and_a_missing_one(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert sf.from_pick(str(tmp_path / "empty")) is None and sf.from_pick(str(tmp_path / "nope")) is None and sf.from_pick("") is None

    def test_a_pick_wins_and_a_vanished_pick_falls_back(self, tmp_path):
        game = tmp_path / "Skyrim"
        direct = make(game, "Data", *PLUGIN)
        picked = make(tmp_path, "inst", "mods", "m", *PLUGIN)
        assert sf.resolve(str(game), str(tmp_path / "inst")) == str(picked)
        assert sf.resolve(str(game), str(tmp_path / "gone")) == str(direct)
        assert sf.resolve("", "") is None

    def test_nothing_is_written_into_the_players_folders(self, tmp_path):
        make(tmp_path, "inst", "mods", "m", *PLUGIN)
        before = sorted(str(p) for p in tmp_path.rglob("*"))
        sf.from_pick(str(tmp_path / "inst"))
        sf.resolve(str(tmp_path / "Skyrim"), str(tmp_path / "inst"))
        assert before == sorted(str(p) for p in tmp_path.rglob("*"))


class TestTheQuestionOnTheServer:
    def test_a_pick_is_saved_in_its_own_setting_and_found(self, proxy_module, test_client, tmp_path, monkeypatch):
        game = tmp_path / "Skyrim Special Edition"
        game.mkdir()
        real = make(tmp_path, "Modlists", "K", "mods", "skyrimnet-beta28", *PLUGIN)
        monkeypatch.setattr(proxy_module, "skyrim_install_path", str(game))
        monkeypatch.setattr(proxy_module, "skyrimnet_pick", "")
        proxy_module._skyrimnet_dir_cache["root"] = None
        assert proxy_module._resolve_skyrimnet_dir_cached(str(game)) is None
        bad = test_client.post("/config/skyrimnet-folder", json={"path": str(tmp_path / "Modlists")})
        assert bad.json() == {"ok": False, "line": "That folder does not hold SkyrimNet. Pick another.", "folder": None}
        assert proxy_module.skyrimnet_pick == ""
        ok = test_client.post("/config/skyrimnet-folder", json={"path": str(tmp_path / "Modlists" / "K")})
        assert ok.json() == {"ok": True, "line": f"Found SkyrimNet in {real}", "folder": str(real)}
        assert proxy_module.skyrim_install_path == str(game), "Your Skyrim folder is not touched"
        assert proxy_module._resolve_skyrimnet_dir_cached(str(game)) == str(real)
        assert proxy_module._load_config().get("skyrimnet_folder_pick") == str(tmp_path / "Modlists" / "K")
        assert test_client.post("/config/skyrimnet-folder", json={"path": ""}).json()["ok"] is True and proxy_module.skyrimnet_pick == ""

    def test_the_state_says_when_it_was_found(self, proxy_module, test_client, tmp_path, monkeypatch):
        game = tmp_path / "Skyrim"
        game.mkdir()
        make(tmp_path, "inst", "mods", "m", *PLUGIN)
        monkeypatch.setattr(proxy_module, "skyrim_install_path", str(game))
        monkeypatch.setattr(proxy_module, "skyrimnet_pick", "")
        proxy_module._skyrimnet_dir_cache["root"] = None
        assert test_client.get("/config/settings").json()["folders"]["skyrimnetFound"] is False
        test_client.post("/config/skyrimnet-folder", json={"path": str(tmp_path / "inst")})
        f = test_client.get("/config/settings").json()["folders"]
        assert f["skyrimnetFound"] is True and f["skyrimnetPick"] == str(tmp_path / "inst")

    def test_the_picker_dialog_has_its_title(self, proxy_module, test_client, monkeypatch):
        seen = []
        monkeypatch.setattr(proxy_module.local_models, "pick_folder_dialog", lambda title="": seen.append(title) or None)
        assert test_client.post("/config/settings/pick-folder", json={"what": "skyrimnet"}).json() == {"path": None}
        assert seen == ["Where is your SkyrimNet folder?"]

    def test_the_words_and_the_script_are_in_both_pages(self):
        import get_started_ui
        import settings_ui
        assert "__SN_WORDS__" not in settings_ui.JS and "Where is your SkyrimNet folder?" in settings_ui.JS and "Found SkyrimNet in {folder}" in settings_ui.JS
        assert "skyrimnet-folder" in settings_ui.JS and "skyrimnet-folder" in get_started_ui.JS and "Where is your SkyrimNet folder?" in get_started_ui.JS
        assert "stAskSkyrimNet()" in settings_ui.JS and "gsBrowseSkyrimNet()" in get_started_ui.JS


class TestThePortLine:
    def test_a_free_port_is_not_blocked(self, proxy_module):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        assert proxy_module._port_problem(free) is None

    def test_a_port_in_use_names_the_program(self, proxy_module, monkeypatch):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            monkeypatch.setattr(proxy_module, "_fix_it_listener_pids", lambda p: [4242])
            monkeypatch.setattr(proxy_module.fix_it, "process_name_of", lambda pid: "nginx.exe")
            assert proxy_module._port_problem(port) == f"Port {port} is in use by nginx.exe. MultiProxy needs its own port. The default is 8000."
            monkeypatch.setattr(proxy_module.fix_it, "process_name_of", lambda pid: None)
            assert proxy_module._port_problem(port) == f"Port {port} is used by another program. Pick a different one."
            monkeypatch.setattr(proxy_module, "_fix_it_listener_pids", lambda p: [])
            assert proxy_module._port_problem(port) == f"Port {port} is used by another program. Pick a different one."
        finally:
            s.close()

    def test_skyrimnets_own_web_page_is_named(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "_fix_it_listener_pids", lambda p: [4242])
        monkeypatch.setattr(proxy_module.fix_it, "process_name_of", lambda pid: "SkyrimSE.exe")
        assert proxy_module._port_owner_name(8080) == "SkyrimNet's web page"
        assert proxy_module._port_owner_name(9000) == "SkyrimSE.exe"
        monkeypatch.setattr(proxy_module.fix_it, "process_name_of", lambda pid: "nginx.exe")
        assert proxy_module._port_owner_name(8080) == "nginx.exe"
