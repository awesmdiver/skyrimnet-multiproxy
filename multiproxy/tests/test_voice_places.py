"""Where voice lives (2026-10-03): inside the Local models folder, split into sub-folders; the kept downloads follow it, a chosen folder wins, and an
offer moves old kept files safely."""
import hashlib
import json
import logging

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import voice_downloads as vd
import voice_ui
import voice_wiring


def keep(folder, name="kept.bin", data=b"k" * 1000):
    """A kept download, indexed the way the store does."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    idx = folder / "index.json"
    d = json.loads(idx.read_text()) if idx.is_file() else {}
    d[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": "", "kept": 1}
    idx.write_text(json.dumps(d))


def test_with_a_models_folder_voice_and_downloads_live_inside_it(tmp_path):
    sett, models = tmp_path / "s", tmp_path / "Models"
    assert vd.default_voice_folder(sett, models) == models / "voice"
    p = vd.downloads_plan(sett, models)
    assert p["folder"] == models / "voice" / "downloads" and p["offer"] is False


def test_no_models_folder_keeps_the_old_places(tmp_path):
    sett = tmp_path / "s"
    assert vd.default_voice_folder(sett, None) == sett / "voice"
    assert vd.downloads_plan(sett, None)["folder"] == sett / "voice-downloads"


def test_an_install_in_the_old_place_stays_there(tmp_path):
    sett, models = tmp_path / "s", tmp_path / "Models"
    (sett / "voice").mkdir(parents=True)
    (sett / "voice" / "voice-state.json").write_text("{}")
    assert vd.default_voice_folder(sett, models) == sett / "voice"


def test_a_chosen_downloads_folder_always_wins(tmp_path):
    sett, models, mine = tmp_path / "s", tmp_path / "Models", tmp_path / "mine"
    keep(sett / "voice-downloads")
    assert vd.downloads_plan(sett, models, str(mine))["folder"] == mine


def test_old_kept_files_keep_working_until_moved_and_the_move_is_offered(tmp_path):
    sett, models = tmp_path / "s", tmp_path / "Models"
    keep(sett / "voice-downloads")
    p = vd.downloads_plan(sett, models)
    assert p["folder"] == sett / "voice-downloads" and p["target"] == models / "voice" / "downloads" and p["offer"] is True


def test_moving_checks_files_and_keeps_both_indexes_right(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    keep(old, "good.bin", b"g" * 5000)
    keep(old, "bad.bin", b"b" * 100)
    (old / "bad.bin").write_bytes(b"x" * 100)                    # changed since it was checked: must not move
    (old / "big.gguf.part").write_bytes(b"p" * 300)
    out = vd.DownloadStore(lambda: old).move_kept(new)
    assert out["moved"] == 1 and out["left"] == 1
    assert (new / "good.bin").read_bytes() == b"g" * 5000 and not (old / "good.bin").exists()
    assert (new / "big.gguf.part").stat().st_size == 300 and not (old / "big.gguf.part").exists()
    assert list(json.loads((new / "index.json").read_text())) == ["good.bin"]
    assert list(json.loads((old / "index.json").read_text())) == ["bad.bin"] and (old / "bad.bin").exists()
    assert not list(new.glob("*.moving"))


def test_the_tab_shows_the_folder_with_open_and_change_buttons_and_says_where_it_saves():
    js = voice_ui.VOICE_JS
    for text in ("Downloads on this PC: ", "Open folder", "Change folder…", "Move your kept voice downloads to <code>", "Move", "Not now"):
        assert text in js, text


@pytest_asyncio.fixture
async def rig(tmp_path):
    cfg: dict = {}
    opened = []
    app = FastAPI()
    voice_wiring.setup(app, lambda: json.loads(json.dumps(cfg)), lambda d: (cfg.clear(), cfg.update(d)), lambda: str(tmp_path / "settings"), lambda s: s,
                       logging.getLogger("t"), open_folder=opened.append)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8123", timeout=30)
    yield type("Rig", (), dict(client=client, cfg=cfg, opened=opened, tmp=tmp_path))
    await client.aclose()


@pytest.mark.asyncio
class TestRoutes:
    async def test_overview_follows_the_models_folder_and_open_folder_opens_it(self, rig):
        rig.cfg["local_models"] = {"folder": str(rig.tmp / "Models")}
        d = (await rig.client.get("/config/voice/overview")).json()
        want = rig.tmp / "Models" / "voice" / "downloads"
        assert d["downloadsFolder"] == str(want) and d["voiceFolder"] == str(rig.tmp / "Models" / "voice") and d["moveOffer"] is None
        assert (await rig.client.post("/config/voice/downloads/open-folder")).status_code == 200
        assert rig.opened == [str(want)]

    async def test_no_models_folder_keeps_the_old_default(self, rig):
        d = (await rig.client.get("/config/voice/overview")).json()
        assert d["downloadsFolder"] == str(rig.tmp / "settings" / "voice-downloads")

    async def test_a_chosen_folder_wins_over_the_models_folder(self, rig):
        rig.cfg["local_models"] = {"folder": str(rig.tmp / "Models")}
        await rig.client.post("/config/voice/folders", json={"downloadsFolder": str(rig.tmp / "mine")})
        assert (await rig.client.get("/config/voice/overview")).json()["downloadsFolder"] == str(rig.tmp / "mine")

    async def test_the_move_is_offered_then_done_then_gone(self, rig):
        rig.cfg["local_models"] = {"folder": str(rig.tmp / "Models")}
        keep(rig.tmp / "settings" / "voice-downloads")
        d = (await rig.client.get("/config/voice/overview")).json()
        assert d["downloadsFolder"] == str(rig.tmp / "settings" / "voice-downloads")          # the old place keeps working until the player says
        assert d["moveOffer"] == {"to": str(rig.tmp / "Models" / "voice" / "downloads")}
        out = (await rig.client.post("/config/voice/downloads/move", json={"move": True})).json()
        assert out["moved"] == 1 and out["left"] == 0
        d = (await rig.client.get("/config/voice/overview")).json()
        assert d["moveOffer"] is None and d["downloadsFolder"] == str(rig.tmp / "Models" / "voice" / "downloads") and d["downloads"]["totalBytes"] == 1000

    async def test_not_now_is_asked_only_once(self, rig):
        rig.cfg["local_models"] = {"folder": str(rig.tmp / "Models")}
        keep(rig.tmp / "settings" / "voice-downloads")
        await rig.client.post("/config/voice/downloads/move", json={"move": False})
        d = (await rig.client.get("/config/voice/overview")).json()
        assert d["moveOffer"] is None and d["downloadsFolder"] == str(rig.tmp / "settings" / "voice-downloads")
