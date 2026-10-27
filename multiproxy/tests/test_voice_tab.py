"""The Voice tab (step 3): the page carries the tab and its exact wording, and the routes it calls behave: the license on this device gates the install, a finished
install switches on "My NPCs use the Higgs voice engine" and points lines at that device, the overview has what the screen needs."""
import json

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import voice_downloads
import voice_engine as ve
import voice_ui
import voice_wiring
from tests.test_voice_courier import until
from tests.voice_helpers import good_hardware, make_engine

# Gemini's wording, used as written (spec: "From the Gemini design review"). Every string must be in the page exactly like this.
WORDING = [
    "Give your NPCs a voice that runs on your PC. Choose Higgs, Qwen3-TTS or Chatterbox.",
    "Start here",
    "<b>Setup</b>: Choose a voice and which device will run it, tick the license box, and click Next: Download.",
    "<b>Download</b>: Wait for the big voice file to save to your main device (you can leave this page).",
    "<b>Install</b>: Everything sets itself up automatically and gives you an address to copy when ready.",
    "Press <b>Save</b>, then <b>Test Voice</b>.",
    "<b>' + esc(vcMN(i.activeModel)) + '</b>",
    "Run voice on",
    "Setup",
    "The voice, where it runs, and the license",
    "Saved on this device",
    "Send it to the PC where it runs",
    "On this PC, it shares your graphics card with Skyrim and any local AI models. Using another PC keeps all that memory free for the game.",
    "I have read and agree to the <a class=\"lm-link\"", "' license</a>.",
    "Read it",
    "You only need to agree once per voice, and it covers every PC you install ' + esc(mrow.label) + ' on.",
    "Next: Download",
    "You can leave this page. It keeps going and moves on to Install by itself.",
    "Copying from this device to ",
    "Installing on ",
    "MultiProxy keeps downloads on this PC so it can send them to your other devices.",
    "Checksum verification failed for ",
    "The downloaded file doesn\\'t match the official release and might be damaged.",
    "Download it again",
    "is set up to run ' + esc(i.modelName) + '.",
    "esc(i.modelName) + ' is set up on this device.",
    "'Set <b>TTS Engine</b> to <b>Higgs</b>, then open the <b>Higgs</b> tab.',",
    "'Set <b>TTS Engine</b> to <b>OpenAI-Compatible</b>, then open the <b>OpenAI-Compatible</b> tab.',",
    "Ready.",
    "In SkyrimNet, open <b>Voice</b> &gt; <b>Text-to-Speech</b>.",
    "was switched on for you in Settings.",
    " graphics card may be too small. Voice lines might be slow and the game may stutter.",
    "text = vcRich(i.detail || (i.modelName + ' stopped.'));",
    "Open folder",
    "has an older MultiProxy Courier. Update it (use the newest MultiProxy Courier download) and come back.",
    "Voice log on ",
    "Reading details…",
    "Nothing to show yet.",
    "This frees up \' + vcGb(kept) + \' GB. Your working voices won\\'t break, but installing on another device later will mean downloading the files again.",
    "Start when MultiProxy starts",
    "Check for an update",
    "SkyrimNet\\'s settings won\\'t need to change.",
]


class TestThePage:
    def test_the_tab_sits_between_other_pcs_and_quick_test(self, test_client):
        html = test_client.get("/").text
        assert html.index('id="tabOther"') < html.index('id="tabVoice"') < html.index('id="tabQuick"')
        assert 'id="paneVoice" style="display:none"' in html
        assert "showMainTab('voice')" in html and "vcOpened()" in html

    @pytest.mark.parametrize("text", WORDING)
    def test_the_wording_is_exact(self, text, test_client):
        assert text in voice_ui.VOICE_JS, text

    def test_the_page_script_carries_the_voice_script(self, test_client):
        html = test_client.get("/").text
        assert "function vcRender()" in html and "function vcPcRow(r)" in html and "#voice" in html

    def test_the_other_pcs_tab_draws_higgs_as_one_more_program(self):
        from other_pcs_ui import OTHER_PCS_JS
        assert "vcPcRow(r)" in OTHER_PCS_JS

    def test_no_jargon_and_the_word_mod_is_not_misused(self):
        for bad in ("plugin", "TTS 3 is", "gguf", "audio.cpp", "CUDA", "Vulkan"):
            assert bad not in voice_ui.VOICE_JS.replace("Read the full Boson Higgs TTS 3 license", ""), bad


@pytest_asyncio.fixture
async def rig(tmp_path, monkeypatch):
    engine, src, _ = make_engine(tmp_path / "g")
    monkeypatch.setattr(ve, "InternetSource", lambda: voice_downloads.FolderSource(src))
    cfg: dict = {}
    switched = []

    def enable():
        switched.append(1)
        return len(switched) == 1

    opened = []
    app = FastAPI()
    handles = voice_wiring.setup(app, lambda: json.loads(json.dumps(cfg)), lambda d: (cfg.clear(), cfg.update(d)), lambda: str(tmp_path / "settings"), lambda s: s,
                                 __import__("logging").getLogger("t"), this_pc=lambda: "gaming-pc", engine=engine, main_port=lambda: 8123,
                                 open_folder=opened.append, enable_tags=enable)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8123", timeout=30)
    yield type("Rig", (), dict(client=client, engine=engine, cfg=cfg, switched=switched, opened=opened, handles=handles, tmp=tmp_path))
    await client.aclose()
    await engine.close()


@pytest.mark.asyncio
class TestRoutes:
    async def test_the_overview_has_what_the_screen_needs(self, rig):
        d = (await rig.client.get("/config/voice/overview")).json()
        assert d["address"] == "http://127.0.0.1:8123"           # MultiProxy's own address: no separate voice port
        assert d["thisPc"]["licenseOk"] is False and d["thisEngine"]["state"] == "not_installed"
        assert d["licenseUrl"].startswith("https://") and d["modelBytes"] == rig.engine.model.size
        assert d["pcs"] == [] and d["location"] == "this" and d["tagsSwitchedOn"] is False

    async def test_install_needs_the_license_first(self, rig):
        r = await rig.client.post("/config/voice/install")
        assert r.status_code == 403 and "license" in r.json()["detail"]
        assert rig.engine.job is None

    async def test_the_license_is_recorded_with_pc_time_and_version(self, rig):
        d = (await rig.client.post("/config/voice/license")).json()
        assert d["licenseOk"] and d["license"]["pc"] == "gaming-pc" and d["license"]["license_version"] == ve.LICENSE_VERSION and d["license"]["accepted_at"] > 0
        assert (await rig.client.get("/config/voice/overview")).json()["thisPc"]["licenseOk"] is True
        saved = json.loads((rig.tmp / "settings" / "voice" / "voice-license-this-pc.json").read_text(encoding="utf-8"))
        assert saved["license_version"] == ve.LICENSE_VERSION

    async def test_a_license_for_an_older_version_is_not_enough(self, rig):
        await rig.client.post("/config/voice/license")
        f = rig.tmp / "settings" / "voice" / "voice-license-this-pc.json"
        rec = json.loads(f.read_text(encoding="utf-8"))
        rec["license_version"] = "older"
        f.write_text(json.dumps(rec), encoding="utf-8")
        assert (await rig.client.post("/config/voice/install")).status_code == 403

    async def test_installing_switches_the_tag_setting_on_once_it_is_done_and_says_so(self, rig):
        await rig.client.post("/config/voice/license")
        assert (await rig.client.post("/config/voice/install")).status_code == 200
        assert rig.switched == []                                   # not before it is installed
        assert await until(lambda: rig.engine.job and rig.engine.job.state == "done")
        assert await until(lambda: rig.switched)
        d = (await rig.client.get("/config/voice/overview")).json()
        assert d["tagsSwitchedOn"] is True and d["thisEngine"]["installed"] is True and d["location"] == "this"

    async def test_uninstalling_leaves_the_setting_alone(self, rig):
        await rig.client.post("/config/voice/license")
        await rig.client.post("/config/voice/install")
        assert await until(lambda: rig.switched)
        await rig.client.post("/config/voice/uninstall", json={})
        assert len(rig.switched) == 1                               # nothing switched it back
        assert (await rig.client.get("/config/voice/overview")).json()["tagsSwitchedOn"] is False      # only the "we switched it on for you" note goes

    async def test_a_failed_install_does_not_touch_the_setting(self, rig, monkeypatch):
        await rig.client.post("/config/voice/license")
        monkeypatch.setattr(ve, "InternetSource", lambda: voice_downloads.FolderSource(rig.tmp / "nowhere"))
        await rig.client.post("/config/voice/install")
        assert await until(lambda: rig.engine.job and rig.engine.job.state == "error")
        assert rig.switched == []

    async def test_start_with_this_pc_is_remembered_and_used(self, rig):
        assert (await rig.client.post("/config/voice/autostart", json={"on": True})).json() == {"autostart": True}
        assert (await rig.client.get("/config/voice/overview")).json()["thisPc"]["autostart"] is True
        await rig.client.post("/config/voice/license")
        await rig.client.post("/config/voice/install")
        assert await until(lambda: rig.engine.job and rig.engine.job.state == "done")
        await rig.engine.stop()
        await rig.handles.autostart()
        assert rig.engine.status()["state"] == "ready"
        assert (await rig.client.post("/config/voice/autostart", json={"on": False})).json() == {"autostart": False}

    async def test_the_engine_check_compares_with_the_tested_version(self, rig):
        d = (await rig.client.get("/config/voice/engine-check")).json()
        assert d["upToDate"] is False and d["newest"] == ve.ENGINE_VERSION
        await rig.client.post("/config/voice/license")
        await rig.client.post("/config/voice/install")
        assert await until(lambda: rig.engine.job and rig.engine.job.state == "done")
        rig.engine._write_state(engine=ve.ENGINE_VERSION)
        assert (await rig.client.get("/config/voice/engine-check")).json()["upToDate"] is True
        rig.engine._write_state(engine="v0.1.0")
        assert (await rig.client.get("/config/voice/engine-check")).json()["upToDate"] is False

    async def test_open_folder_opens_the_engine_folder(self, rig):
        assert (await rig.client.post("/config/voice/open-folder")).status_code == 404       # nothing there yet
        rig.engine.folder.mkdir(parents=True)
        assert (await rig.client.post("/config/voice/open-folder")).status_code == 200
        assert rig.opened == [str(rig.engine.folder)]

    async def test_the_fit_names_what_its_warnings_are_about(self, rig):
        from voice_engine import Hardware
        from local_models import GpuInfo
        hw = Hardware(system="windows", gpu=GpuInfo("NVIDIA GeForce GTX 1650", 4096, "nvidia"), driver=617.14, free_vram_bytes=3 * 1024**3,
                      free_ram_bytes=32 * 1024**3, free_disk_bytes=500 * 1024**3)
        fit = ve.assess(hw)
        assert fit.verdict == "warn" and fit.flags == ["vram"] and fit.to_json()["flags"] == ["vram"]
        assert ve.assess(good_hardware()).flags == []
