"""The ChatGPT (Codex) card installs Codex for you (2026-10-26): stand-in servers and scratch folders only; no real download, no real sign-in, nothing of the player's own Codex."""
import asyncio
import hashlib
import io
import logging
import zipfile

import pytest
from aiohttp import web

import codex_install as ci
from tests.fixtures.courier_fakes import _Server

KEY = "x86_64-pc-windows-msvc"
EXE = ci.exe_name(KEY)


def make_zip(extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(EXE, b"MZ fake codex")
        z.writestr("codex-path/rg.exe", b"rg")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


class ReleasesStandIn(_Server):
    def __init__(self, data: bytes, digest: str = None, listed_size: int = None, tag: str = "rust-v0.164.0", slow: bool = False):
        super().__init__()
        self.data, self.slow = data, slow
        self.digest = digest or hashlib.sha256(data).hexdigest()
        self.size = listed_size if listed_size is not None else len(data)
        self.tag = tag

    def app(self):
        app = web.Application()
        app.router.add_get("/releases", self.h_releases)
        app.router.add_get("/dl/{name}", self.h_dl)
        return app

    async def h_releases(self, request):
        name = ci.asset_name(KEY)
        return web.json_response([
            {"tag_name": "rust-v0.165.0-alpha.1", "prerelease": True, "draft": False, "assets": []},
            {"tag_name": self.tag, "prerelease": False, "draft": False,
             "assets": [{"name": name, "size": self.size, "digest": "sha256:" + self.digest, "browser_download_url": f"{self.base}/dl/{name}"}]},
        ])

    async def h_dl(self, request):
        if self.slow:
            await asyncio.sleep(30)
        return web.Response(body=self.data)


def installer(tmp_path, stand_in, monkeypatch, version="codex-cli 0.164.0"):
    inst = ci.CodexInstaller(tmp_path / "codex", logging.getLogger("t"), releases_url=stand_in.base + "/releases", key=KEY, hosts=(stand_in.base + "/dl/",))

    async def fake_version(exe):
        return version
    monkeypatch.setattr(inst, "_run_version", fake_version)
    return inst


async def finish(inst):
    await inst.job.task


class TestPicking:
    def test_the_newest_real_release_by_its_number(self):
        rel = [{"tag_name": "rust-v0.165.0-alpha.1", "prerelease": True}, {"tag_name": "rust-v0.9.0"}, {"tag_name": "rust-v0.164.0"}, {"tag_name": "rust-v0.164.1", "draft": True},
               {"tag_name": "v0.6.0"}, {"tag_name": "rust-v0.164.0-alpha.12"}]
        assert ci.newest_release(rel)["tag_name"] == "rust-v0.164.0"
        assert ci.newest_release([]) is None and ci.newest_release(None) is None

    def test_a_file_without_a_checksum_or_from_elsewhere_is_refused(self):
        name = ci.asset_name(KEY)
        good = {"name": name, "size": 10, "digest": "sha256:" + "a" * 64, "browser_download_url": "https://github.com/openai/codex/releases/download/rust-v1.2.3/" + name}
        assert ci.asset_for({"assets": [good]}, KEY)[1:] == (10, "a" * 64)
        for bad in ({**good, "digest": None}, {**good, "digest": "md5:abc"}, {**good, "size": 0}, {**good, "browser_download_url": "https://example.com/" + name}):
            with pytest.raises(ci.CodexInstallError) as e:
                ci.asset_for({"assets": [bad]}, KEY)
            assert e.value.message == ci.LINE_CHECK
        with pytest.raises(ci.CodexInstallError) as e:
            ci.asset_for({"assets": []}, KEY)
        assert e.value.message == ci.LINE_NO_BUILD

    def test_which_download_is_for_which_computer(self):
        assert ci.platform_key("win32", "AMD64") == "x86_64-pc-windows-msvc" and ci.platform_key("win32", "ARM64") == "aarch64-pc-windows-msvc"
        assert ci.platform_key("linux", "x86_64") == "x86_64-unknown-linux-musl" and ci.asset_name("x86_64-unknown-linux-musl") == "codex-x86_64-unknown-linux-musl.tar.gz"
        assert ci.platform_key("darwin", "arm64") is None and ci.platform_key("win32", "mips") is None


class TestSignInFile:
    def test_only_asks_whether_the_file_is_there(self, tmp_path):
        env = {"CODEX_HOME": str(tmp_path / "home")}
        assert not ci.signed_in_file_exists(env)
        (tmp_path / "home").mkdir()
        (tmp_path / "home" / "auth.json").write_text("not a real sign-in", encoding="utf-8")
        assert ci.signed_in_file_exists(env)


class TestCard:
    def test_the_two_lines(self, tmp_path):
        inst = ci.CodexInstaller(tmp_path, logging.getLogger("t"), key=KEY)
        assert inst.card(False, True) == {"show": True, "job": None, "line": ci.LINE_SIGNED_IN_NO_PROGRAM, "canInstall": True}
        assert inst.card(False, False)["line"] == ci.LINE_NOT_INSTALLED
        assert ci.LINE_SIGNED_IN_NO_PROGRAM == "You are signed in to Codex, but the program isn't installed on this PC. Press Install."

    def test_an_installed_codex_shows_the_normal_card(self, tmp_path):
        inst = ci.CodexInstaller(tmp_path, logging.getLogger("t"), key=KEY)
        assert inst.card(True, True)["show"] is False

    def test_a_computer_with_no_download_cannot_install(self, tmp_path):
        inst = ci.CodexInstaller(tmp_path, logging.getLogger("t"), key=None)
        assert inst.card(False, False)["canInstall"] is False
        with pytest.raises(ci.CodexInstallError) as e:
            inst.start()
        assert e.value.message == ci.LINE_NO_BUILD


@pytest.mark.asyncio
class TestInstalling:
    async def test_install_puts_it_in_place_and_it_is_found_at_once(self, tmp_path, monkeypatch):
        stand_in = await ReleasesStandIn(make_zip()).start()
        try:
            inst = installer(tmp_path, stand_in, monkeypatch)
            assert inst.exe_if_installed() is None
            inst.start()
            await finish(inst)
        finally:
            await stand_in.stop()
        assert inst.job.state == "done" and inst.job.version == "0.164.0" and inst.job.to_json()["percent"] == 100
        assert inst.exe_if_installed() == str(tmp_path / "codex" / "current" / EXE)
        assert (tmp_path / "codex" / "current" / "codex-path" / "rg.exe").is_file()          # the helper files come with it
        assert not (tmp_path / "codex" / "staging").exists()
        assert inst.card(True, True)["show"] is False                                       # installed: back to the normal card, no restart

    async def test_a_damaged_download_is_refused_and_nothing_is_installed(self, tmp_path, monkeypatch):
        stand_in = await ReleasesStandIn(make_zip(), digest="b" * 64).start()
        try:
            inst = installer(tmp_path, stand_in, monkeypatch)
            inst.start()
            await finish(inst)
        finally:
            await stand_in.stop()
        assert inst.job.state == "error" and inst.job.error == ci.LINE_CHECK
        assert inst.exe_if_installed() is None
        card = inst.card(False, False)
        assert card["show"] and card["job"]["state"] == "error"

    async def test_a_zip_with_a_file_outside_its_folder_is_refused(self, tmp_path, monkeypatch):
        stand_in = await ReleasesStandIn(make_zip({"../evil.txt": "x"})).start()
        try:
            inst = installer(tmp_path, stand_in, monkeypatch)
            inst.start()
            await finish(inst)
        finally:
            await stand_in.stop()
        assert inst.job.state == "error" and inst.exe_if_installed() is None and not (tmp_path / "evil.txt").exists() and not (tmp_path / "codex" / "evil.txt").exists()

    async def test_a_program_that_will_not_start_is_not_installed(self, tmp_path, monkeypatch):
        stand_in = await ReleasesStandIn(make_zip()).start()
        try:
            inst = installer(tmp_path, stand_in, monkeypatch, version="")
            inst.start()
            await finish(inst)
        finally:
            await stand_in.stop()
        assert inst.job.error == ci.LINE_WONT_START and inst.exe_if_installed() is None

    async def test_a_site_that_cannot_be_reached_says_so_in_one_line(self, tmp_path):
        inst = ci.CodexInstaller(tmp_path / "codex", logging.getLogger("t"), releases_url="http://127.0.0.1:1/releases", key=KEY)
        inst.start()
        await finish(inst)
        assert inst.job.state == "error" and inst.job.error == ci.LINE_DOWNLOAD

    async def test_cancel_stops_it_and_leaves_nothing_behind(self, tmp_path, monkeypatch):
        stand_in = await ReleasesStandIn(make_zip(), slow=True).start()
        try:
            inst = installer(tmp_path, stand_in, monkeypatch)
            inst.start()
            for _ in range(50):
                await asyncio.sleep(0.05)
                if inst.job.step == "Downloading":
                    break
            assert inst.running() and inst.job.to_json()["step"] == "Downloading"
            with pytest.raises(ci.CodexInstallError):
                inst.start()                                                                  # a second press while it runs: refused
            assert inst.cancel()
            await asyncio.wait([inst.job.task], timeout=5)
        finally:
            await stand_in.stop()
        assert inst.job.state == "cancelled" and inst.exe_if_installed() is None and not inst.running()
        assert inst.card(False, False)["job"] is None                                         # (a cancelled install leaves the plain "Not installed" card)

    async def test_an_existing_install_is_replaced_whole(self, tmp_path, monkeypatch):
        stand_in = await ReleasesStandIn(make_zip()).start()
        try:
            inst = installer(tmp_path, stand_in, monkeypatch)
            (tmp_path / "codex" / "current").mkdir(parents=True)
            (tmp_path / "codex" / "current" / "stale.txt").write_text("old")
            inst.start()
            await finish(inst)
        finally:
            await stand_in.stop()
        assert inst.job.state == "done" and not (tmp_path / "codex" / "current" / "stale.txt").exists() and not (tmp_path / "codex" / "old").exists()


@pytest.mark.asyncio
class TestOnTheCard:
    async def test_not_installed_shows_install_and_the_signed_in_line(self, proxy_module, tmp_path, monkeypatch):
        monkeypatch.setattr(proxy_module, "_codex_installer", ci.CodexInstaller(tmp_path / "codex", logging.getLogger("t"), key=KEY))
        monkeypatch.setattr(proxy_module.poll_cache, "cached_which", lambda name, ttl=0: None)
        monkeypatch.setattr(ci, "signed_in_file_exists", lambda environ=None: True)
        st = await proxy_module._codex_status_impl()
        assert st["status"] == "Not installed" and st["state"] == "off" and st["installCard"]["show"] and st["installCard"]["canInstall"]
        assert st["empty"] == ci.LINE_SIGNED_IN_NO_PROGRAM
        monkeypatch.setattr(ci, "signed_in_file_exists", lambda environ=None: False)
        assert (await proxy_module._codex_status_impl())["empty"] == ci.LINE_NOT_INSTALLED

    async def test_the_card_finds_a_fresh_install_without_a_restart(self, proxy_module, tmp_path):
        # (the test fixture replaces `_codex_path` with "not found", so the real function is read from the file and run with a scratch installer)
        src = open(proxy_module.__file__, encoding="utf-8").read()
        assert 'return poll_cache.cached_which("codex") or _codex_installer.exe_if_installed()' in src
        inst = ci.CodexInstaller(tmp_path / "codex", logging.getLogger("t"), key=KEY)
        found = lambda which: which or inst.exe_if_installed()                                  # the same `or`
        assert found(None) is None
        (tmp_path / "codex" / "current").mkdir(parents=True)
        (tmp_path / "codex" / "current" / EXE).write_bytes(b"x")
        assert found(None) == str(tmp_path / "codex" / "current" / EXE) and found("C:/on/path/codex.exe") == "C:/on/path/codex.exe"

    async def test_installed_shows_the_normal_card_with_no_install_part(self, proxy_module, tmp_path, monkeypatch):
        monkeypatch.setattr(proxy_module, "_codex_path", lambda: "C:/codex.exe")
        monkeypatch.setattr(proxy_module, "_codex_installer", ci.CodexInstaller(tmp_path / "codex", logging.getLogger("t"), key=KEY))

        async def signed_in():
            return True

        async def no_rows(force=False):
            return None
        monkeypatch.setattr(proxy_module, "_codex_logged_in", signed_in)
        monkeypatch.setattr(proxy_module, "_fetch_codex_models", no_rows)
        st = await proxy_module._codex_status_impl()
        assert st["state"] == "ok" and st["installCard"] is None

    async def test_install_is_refused_when_codex_is_already_there(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "_codex_path", lambda: "C:/codex.exe")
        r = test_client.post("/config/codex/install")
        assert r.status_code == 409


def test_the_install_script_is_in_the_page(proxy_module):
    import codex_install_ui
    assert "codexInstallHtml" in codex_install_ui.CODEX_INSTALL_JS and "Install" in codex_install_ui.CODEX_INSTALL_JS
