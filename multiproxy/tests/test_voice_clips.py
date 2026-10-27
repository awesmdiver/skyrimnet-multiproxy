"""Your own voice clips (step 4): name matching, the override, the first clip SkyrimNet sent kept, replace and remove keep the old file, a clip the engine can't
use, a folder that is watched, the routes the Voice tab calls, and the same through a Courier device (add, play, speak, moving Higgs along with its clips)."""
import base64
import io
import json
import time
import wave
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import voice_clips as vc
import voice_downloads
import voice_engine as ve
import voice_service as vs
import voice_wiring
from tests.test_voice_courier import PC, install, installed_rig, move_to, rig, speak, until          # noqa: F401  (rig is a fixture)
from tests.test_voice_service import multipart, wav
from tests.voice_helpers import make_engine


def clip(seconds=0.5, rate=24000):
    return wav(rate, 1, 2, seconds)


# ---- which voice a name means -------------------------------------------------------------------------------------------------------------------
class TestNames:
    @pytest.mark.parametrize("name", ["femalenord.wav", "FemaleNord.WAV", "femalenord.mp3", "FEMALENORD", "femalenord.flac", "c:/x/FemaleNord.Wav"])
    def test_case_and_file_type_do_not_matter(self, name):
        assert vc.voice_key(name) == "femalenord"

    def test_odd_characters_turn_the_same_way_the_upload_name_does(self):
        assert vc.voice_key("Female Nord.wav") == "female_nord" == vc.voice_key("female_nord.wav")

    def test_a_name_with_dots_keeps_them(self):
        assert vc.voice_key("a.b.wav") == "a.b"

    def test_nothing_is_nothing(self):
        assert vc.voice_key("") == "" and vc.voice_key(None) == ""


# ---- the store ----------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    messages = []
    s = vc.ClipStore(lambda: tmp_path / "clips", messages.append)
    s.messages = messages
    return s


class TestResolve:
    def test_without_a_clip_the_first_upload_is_kept_and_used_every_time(self, store):
        first, second = clip(0.5), clip(0.8)
        assert store.resolve("femalenord.wav", first) == first
        assert (store.kept_folder / "femalenord.wav").read_bytes() == first
        assert store.resolve("femalenord.wav", second) == first, "the voice stays the same from line to line"
        assert [r["voice"] for r in store.listing()] == ["femalenord"]

    def test_the_players_clip_is_used_instead_of_the_upload(self, store):
        mine = clip(1.0)
        store.add("femalenord", mine)
        got = store.resolve("FemaleNord.wav", clip(0.5))
        assert got == mine                                         # (already in the engine's shape, so it is the same bytes)

    def test_the_upload_is_still_kept_when_a_clip_of_the_players_wins(self, store):
        store.add("femalenord", clip(1.0))
        store.resolve("femalenord.wav", clip(0.5))
        assert (store.kept_folder / "femalenord.wav").exists()      # Remove then goes back to SkyrimNet's

    def test_a_clip_in_another_rate_is_brought_to_what_the_engine_takes(self, store):
        store.add("serana", clip(1.0, 44100))
        out = store.resolve("serana.wav", None)
        with wave.open(io.BytesIO(out)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)

    def test_no_voice_name_means_whatever_came(self, store):
        c = clip()
        assert store.resolve(None, c) == c and store.resolve("", c) == c and store.resolve("x", None) is None

    def test_a_clip_that_cannot_be_used_never_reaches_the_engine(self, store):
        (store.folder).mkdir(parents=True)
        (store.folder / "serana.mp3").write_bytes(b"ID3 not a wav at all")
        (store.folder / "galmar.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        upload = clip()
        assert store.resolve("serana.wav", upload) == upload and store.resolve("galmar.wav", upload) == upload
        rows = {r["voice"]: r for r in store.listing()}
        assert rows["serana"]["source"] == "yours" and rows["serana"]["supported"] is False
        assert rows["galmar"]["supported"] is False
        store.resolve("serana.wav", upload)
        assert sum("serana.mp3" in m for m in store.messages) == 1, "said once, in the log"


class TestChanging:
    def test_adding_over_a_clip_moves_the_old_one_aside_with_a_timestamp(self, store):
        store.add("femalenord", clip(0.5))
        old = (store.folder / "femalenord.wav").read_bytes()
        store.add("femalenord", clip(1.5))
        kept = list(store.replaced_folder.iterdir())
        assert len(kept) == 1 and kept[0].read_bytes() == old
        assert kept[0].name.startswith("femalenord.") and kept[0].suffix == ".wav" and len(kept[0].name) > len("femalenord.wav")
        assert store.listing()[0]["seconds"] == pytest.approx(1.5, abs=0.05)

    def test_replacing_twice_in_a_second_keeps_both(self, store):
        for s in (0.5, 0.7, 0.9):
            store.add("femalenord", clip(s))
        assert len(list(store.replaced_folder.iterdir())) == 2

    def test_remove_goes_back_to_skyrimnets_clip_and_keeps_the_players_file(self, store):
        upload = clip(0.5)
        store.resolve("femalenord.wav", upload)
        mine = clip(1.0)
        store.add("femalenord", mine)
        assert store.remove("femalenord") is True
        assert store.resolve("femalenord.wav", clip(0.9)) == upload
        assert [r["source"] for r in store.listing()] == ["skyrimnet"]
        assert [p.read_bytes() for p in store.replaced_folder.iterdir()] == [mine]
        assert store.remove("femalenord") is False

    def test_a_clip_the_engine_cannot_use_is_refused_and_nothing_changes(self, store):
        store.add("femalenord", clip(0.5))
        before = (store.folder / "femalenord.wav").read_bytes()
        with pytest.raises(vc.ClipFormatError) as e:
            store.add("femalenord", b"this is not sound")
        assert e.value.message == "That voice clip's format isn't supported."
        assert (store.folder / "femalenord.wav").read_bytes() == before and not store.replaced_folder.exists()

    def test_no_voice_name_is_refused(self, store):
        with pytest.raises(ve.VoiceEngineError):
            store.add("  ", clip())

    def test_a_dropped_in_file_is_picked_up_without_a_restart(self, store):
        assert store.listing() == []
        store.folder.mkdir(parents=True)
        (store.folder / "Serana.WAV").write_bytes(clip(2.0))
        rows = store.listing()
        assert [(r["voice"], r["source"], r["supported"]) for r in rows] == [("serana", "yours", True)]
        assert store.resolve("serana.wav", clip(0.5)) == clip(2.0)
        (store.folder / "Serana.WAV").unlink()                       # and a removed file is noticed too: back to the clip SkyrimNet sent
        assert [(r["voice"], r["source"]) for r in store.listing()] == [("serana", "skyrimnet")]

    def test_other_files_in_the_folder_are_left_alone(self, store):
        store.folder.mkdir(parents=True)
        (store.folder / "notes.txt").write_text("x")
        (store.folder / ".hidden.wav").write_bytes(clip())
        assert store.listing() == []

    def test_the_player_wins_a_tie_with_the_newest_file(self, store):
        store.folder.mkdir(parents=True)
        (store.folder / "serana.mp3").write_bytes(b"old")
        time.sleep(0.02)
        (store.folder / "serana.wav").write_bytes(clip(1.0))
        assert store.listing()[0]["file"] == "serana.wav"


# ---- the engine uses them -----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def this_pc(tmp_path, monkeypatch):
    engine, src, _ = make_engine(tmp_path / "g")
    monkeypatch.setattr(ve, "InternetSource", lambda: voice_downloads.FolderSource(src))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "requests.jsonl"))
    cfg: dict = {}
    opened, picked = [], []
    app = FastAPI()
    handles = voice_wiring.setup(app, lambda: json.loads(json.dumps(cfg)), lambda d: (cfg.clear(), cfg.update(d)), lambda: str(tmp_path / "settings"), lambda s: s,
                                 __import__("logging").getLogger("t"), this_pc=lambda: "gaming-pc", engine=engine, open_folder=opened.append,
                                 pick_file=lambda title, types: picked[0] if picked else None)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8123", timeout=60)
    rg = type("R", (), {})()
    rg.client, rg.engine, rg.cfg, rg.opened, rg.picked, rg.tmp, rg.handles = client, engine, cfg, opened, picked, tmp_path, handles
    rg.requests = lambda: [json.loads(x) for x in (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()] if (tmp_path / "requests.jsonl").exists() else []
    return rg


@pytest_asyncio.fixture
async def running(this_pc):
    await this_pc.client.post("/config/voice/license")
    await this_pc.client.post("/config/voice/install")
    assert await until(lambda: this_pc.engine.job and this_pc.engine.job.state == "done")
    yield this_pc
    await this_pc.client.aclose()
    await this_pc.engine.close()


@pytest.mark.asyncio
class TestTheEngineUsesThem:
    async def test_skyrimnets_upload_goes_to_the_engine_when_there_is_no_clip(self, running):
        audio, _ = await speak(running.client, "Hello there.")
        assert audio and running.requests()[-1]["ref_bytes"] == len(wav())

    async def test_a_clip_named_after_the_voice_goes_to_the_engine_instead(self, running):
        mine = clip(2.0)
        running.engine.clips.add("femaleeventoned", mine)           # SkyrimNet's upload in speak() is named femaleeventoned.wav
        audio, _ = await speak(running.client, "Hello there.")
        assert audio and running.requests()[-1]["ref_bytes"] == len(mine) != len(wav())

    async def test_the_first_upload_stays_the_voices_clip_when_a_later_one_differs(self, running):
        await speak(running.client, "One.")
        first = running.requests()[-1]["ref_bytes"]
        body, ctype = multipart("femaleeventoned.wav", clip(0.9))
        path = (await running.client.post("/gradio_api/upload", content=body, headers={"content-type": ctype})).json()[0]
        call = await running.client.post("/gradio_api/call/generate_audio", json={"data": ["Two.", {"path": path, "meta": {"_type": "gradio.FileData"}}, 0.8]})
        await running.client.get(f"/gradio_api/call/generate_audio/{call.json()['event_id']}")
        assert running.requests()[-1]["ref_bytes"] == first

    async def test_removing_the_clip_goes_back_to_skyrimnets(self, running):
        await speak(running.client, "One.")
        running.engine.clips.add("femaleeventoned", clip(2.0))
        await speak(running.client, "Two.")
        assert running.requests()[-1]["ref_bytes"] != len(wav())
        assert (await running.client.post("/config/voice/clips/remove", json={"voice": "femaleeventoned"})).json()["removed"] is True
        await speak(running.client, "Three.")
        assert running.requests()[-1]["ref_bytes"] == len(wav())

    async def test_a_dropped_in_file_is_used_on_the_next_line(self, running):
        await speak(running.client, "One.")
        (running.engine.clips_dir / "FemaleEventOned.wav").write_bytes(clip(3.0))
        await speak(running.client, "Two.")
        assert running.requests()[-1]["ref_bytes"] == len(clip(3.0))

    async def test_a_file_the_engine_cannot_use_is_never_sent(self, running):
        await speak(running.client, "One.")
        (running.engine.clips_dir / "femaleeventoned.mp3").write_bytes(b"not sound")
        await speak(running.client, "Two.")
        assert running.requests()[-1]["ref_bytes"] == len(wav())


# ---- the routes the Voice tab calls -------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestTheRoutes:
    async def test_list_add_play_remove(self, running, tmp_path):
        await speak(running.client, "One.")
        d = (await running.client.get("/config/voice/clips")).json()
        assert d["onThisPc"] and d["formats"] == "WAV files" and [(c["voice"], c["source"]) for c in d["clips"]] == [("femaleeventoned", "skyrimnet")]
        f = tmp_path / "mine.wav"
        f.write_bytes(clip(2.0))
        r = await running.client.post("/config/voice/clips/add", json={"voice": "femaleeventoned", "path": str(f)})
        assert r.status_code == 200 and r.json()["clip"]["source"] == "yours" and r.json()["clip"]["seconds"] == pytest.approx(2.0, abs=0.05)
        a = await running.client.get("/config/voice/clips/audio?voice=femaleeventoned&which=yours")
        assert a.status_code == 200 and a.headers["content-type"] == "audio/wav" and a.content == clip(2.0)
        s = await running.client.get("/config/voice/clips/audio?voice=femaleeventoned&which=skyrimnet")
        assert s.content == wav()
        assert (await running.client.get("/config/voice/clips/audio?voice=nobody")).status_code == 404

    async def test_the_file_window_gives_a_path(self, running, tmp_path):
        assert (await running.client.post("/config/voice/clips/pick")).json() == {"path": None}
        running.picked.append(str(tmp_path / "x.wav"))
        assert (await running.client.post("/config/voice/clips/pick")).json() == {"path": str(tmp_path / "x.wav")}

    async def test_add_from_bytes(self, running):
        r = await running.client.post("/config/voice/clips/add", json={"voice": "serana", "dataB64": base64.b64encode(clip(1.0)).decode()})
        assert r.status_code == 200 and (running.engine.clips_dir / "serana.wav").exists()

    async def test_unsupported_formats_are_refused_plainly(self, running, tmp_path):
        mp3 = tmp_path / "a.mp3"
        mp3.write_bytes(b"ID3")
        bad = tmp_path / "b.wav"
        bad.write_bytes(b"not sound")
        txt = tmp_path / "c.txt"
        txt.write_text("x")
        for path in (mp3, bad, txt):
            r = await running.client.post("/config/voice/clips/add", json={"voice": "serana", "path": str(path)})
            assert r.status_code == 400 and r.json()["detail"] == "That voice clip's format isn't supported."
        assert not (running.engine.clips_dir / "serana.wav").exists()
        assert (await running.client.post("/config/voice/clips/add", json={"voice": "", "path": str(bad)})).status_code == 400
        assert (await running.client.post("/config/voice/clips/add", json={"voice": "serana", "path": str(tmp_path / "nope.wav")})).status_code == 400

    async def test_open_folder(self, running):
        assert (await running.client.post("/config/voice/clips/open-folder")).status_code == 200
        assert running.opened == [str(running.engine.clips_dir)]

    async def test_uninstalling_keeps_the_clips(self, running):
        running.engine.clips.add("serana", clip(1.0))
        await running.client.post("/config/voice/uninstall", json={})
        assert (running.engine.clips_dir / "serana.wav").exists()


# ---- through a Courier device -----------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestThroughTheCourier:
    async def _move_to_laptop(self, rig):
        await installed_rig(rig)
        return await move_to(rig, PC)

    async def test_a_clip_added_here_lands_on_the_pc_that_runs_higgs_and_plays_back(self, rig):
        await self._move_to_laptop(rig)
        mine = clip(2.0)
        r = await rig.client.post("/config/voice/clips/add", json={"voice": "serana", "dataB64": base64.b64encode(mine).decode()})
        assert r.status_code == 200 and r.json()["pc"] == PC
        assert (rig.c_engine.clips_dir / "serana.wav").read_bytes() == mine
        assert not (rig.g_engine.clips_dir / "serana.wav").exists(), "it went to the device that runs Higgs, nowhere else"
        d = (await rig.client.get("/config/voice/clips")).json()
        assert d["pc"] == PC and d["onThisPc"] is False and d["clips"][0]["voice"] == "serana"
        a = await rig.client.get("/config/voice/clips/audio?voice=serana&which=yours")
        assert a.status_code == 200 and a.content == mine
        assert (await rig.client.get("/config/voice/clips/audio?voice=nobody")).status_code == 404

    async def test_a_line_spoken_through_the_courier_uses_the_clip_there(self, rig):
        await self._move_to_laptop(rig)
        mine = clip(2.0)
        await rig.client.post("/config/voice/clips/add", json={"voice": "femaleeventoned", "dataB64": base64.b64encode(mine).decode()})
        audio, _ = await speak(rig.client, "Hello there.")
        assert audio and rig.requests()[-1]["ref_bytes"] == len(mine)
        await rig.client.post("/config/voice/clips/remove", json={"voice": "femaleeventoned"})
        await speak(rig.client, "Again.")
        assert rig.requests()[-1]["ref_bytes"] == len(wav()), "back to the one SkyrimNet sent (kept there the first time)"
        assert list((rig.c_engine.clips_dir / "replaced").iterdir())

    async def test_the_kept_first_clip_lives_on_the_courier_pc(self, rig):
        await self._move_to_laptop(rig)
        await speak(rig.client, "Hello there.")
        assert (rig.c_engine.clips_dir / "from-skyrimnet" / "femaleeventoned.wav").exists()

    async def test_unsupported_clip_through_the_courier_is_refused(self, rig):
        await self._move_to_laptop(rig)
        r = await rig.client.post("/config/voice/clips/add", json={"voice": "serana", "dataB64": base64.b64encode(b"not sound").decode()})
        assert r.status_code == 400 and r.json()["detail"] == "That voice clip's format isn't supported."

    async def test_moving_higgs_to_another_pc_takes_the_clips_along_and_keeps_the_originals(self, rig):
        await installed_rig(rig)
        await move_to(rig, "this")           # (finishing an install sends Higgs to that device by itself: bring it back)
        # Higgs runs on this device with a clip of the player's and a kept SkyrimNet clip
        rig.g_engine.clips.add("serana", clip(2.0))
        rig.g_engine.clips.resolve("galmar.wav", clip(0.5))
        r = (await rig.client.post("/config/voice/location", json={"pc": PC})).json()
        assert r["clipsCarried"] == 2 and r["clipsError"] == ""             # serana's clip of the player's + the kept SkyrimNet clip for galmar
        assert (rig.c_engine.clips_dir / "serana.wav").read_bytes() == clip(2.0)
        assert (rig.c_engine.clips_dir / "from-skyrimnet" / "galmar.wav").exists()
        assert (rig.g_engine.clips_dir / "serana.wav").exists(), "the old device keeps its own"
        again = (await rig.client.post("/config/voice/location", json={"pc": "this"})).json()
        assert again["clipsError"] == ""
        assert (rig.g_engine.clips_dir / "from-skyrimnet" / "galmar.wav").exists()

    async def test_nothing_is_replaced_on_the_pc_it_moves_to(self, rig):
        await installed_rig(rig)
        await move_to(rig, "this")
        rig.g_engine.clips.add("serana", clip(2.0))
        rig.c_engine.clips.add("serana", clip(3.0))
        await rig.client.post("/config/voice/location", json={"pc": PC})
        assert (rig.c_engine.clips_dir / "serana.wav").read_bytes() == clip(3.0)


# ---- the wording on the Voice tab (Gemini's, as written) ----------------------------------------------------------------------------------------
def test_the_voice_clips_section_has_the_agreed_wording():
    import voice_ui
    js = voice_ui.VOICE_JS
    for text in (
        "format not supported", "Add a clip…", "Open folder", "Replace…", "▶ Play",
    ):
        assert text in js, text
    # the list of SkyrimNet's voices and its search box are gone; the Add a clip pop-up still suggests those names
    for gone in ("from SkyrimNet (lower quality)", "Add clip…", "Search voices…", "vcClipSearch", "VC_LOW_HELP"):
        assert gone not in js, gone
    assert "x.source === 'yours'" in js[js.index("function vcClipRows()"):js.index("function vcClipsDraw()")]
    assert 'id="vcClipVoices"' in js and "vcClipDialogGo" in js
    assert "plugin" not in js.lower().replace("plugin-", "")


def test_the_clips_card_explains_itself_in_gemini_words():
    import voice_ui
    js = voice_ui.VOICE_JS
    for text in (
        "Voice clips are short recordings that the voice copies to give characters a unique sound.",
        "Adding your own custom clips replaces the game\\'s flat defaults with clearer, much better voices.",
        "Click \"Add a clip...\" and type the voice\\'s name.",
        "Choose a WAV recording from your computer.",
        'Click "Play" to hear your new custom voice.',
        "Tip: The best clips are 8 to 15 seconds long, feature one voice in a quiet room, have no music, and are saved as a WAV file.",
        "No clips of your own yet.</b> <span class=\"lm-muted\">Add one to make voices sound exactly right!",
    ):
        assert text in js, text
    # the old two lines are gone
    assert "Press <b>Add a clip" not in js and "Supported files:" not in js
    # the how-to list has no hide button, and the order is: heading, lines, steps, Add, rows, tip
    card = js[js.index("function vcClipsCard()"):js.index("function vcPlay(")]
    assert "shBox" not in card and "hide" not in card.lower()
    assert card.index("VC_CLIPS_WHAT") < card.index("VC_CLIPS_STEPS") < card.index("vcClipsHeader()") < card.index("vcClipRows()") < card.index("VC_CLIPS_TIP")


def test_the_start_here_and_help_steps_match_the_agreed_wording():
    import voice_ui, pathlib
    js = voice_ui.VOICE_JS
    for text in (
        "<b>Setup</b>: Choose a voice and which device will run it, tick the license box, and click Next: Download.",
        "<b>Download</b>: Wait for the big voice file to save to your main device (you can leave this page).",
        "<b>Install</b>: Everything sets itself up automatically and gives you an address to copy when ready.",
        "<b>SkyrimNet</b>: When the voice is ready, its card shows the steps for SkyrimNet, written for the voice you chose. They end with <b>Save Changes</b> and <b>Test Voice</b>.",
    ):
        assert text in js, text
    help_md = (pathlib.Path(voice_ui.__file__).parent / "docs" / "help" / "help-content.md").read_text(encoding="utf-8")
    assert "4. **SkyrimNet:** When the voice is ready, its card shows the steps for SkyrimNet, written for the voice you chose. They end with **Save Changes** and **Test Voice**." in help_md
    assert "- **Qwen3-TTS voice** or **Chatterbox voice:** set **TTS Engine** to **OpenAI-Compatible**" in help_md and "Then press **Save Changes**" in help_md


# ---- the Test button: one line, made the way SkyrimNet's are, played in the page (2026-10-06) -----------------------------------------------------------------
@pytest.mark.asyncio
class TestTheTestButton:
    async def test_with_no_clip_at_all_it_explains_and_makes_nothing(self, running):
        before = len(running.requests())
        r = await running.client.post("/config/voice/test", json={})
        assert r.status_code == 200 and r.json() == {"needsClip": True, "detail": "Add a voice clip below to test it."}
        assert len(running.requests()) == before, "nothing was sent to the voice (no faked voice from a tone)"

    async def test_with_a_clip_of_their_own_it_returns_sound_made_with_that_clip(self, running):
        mine = clip(2.0)
        running.engine.clips.add("serana", mine)
        r = await running.client.post("/config/voice/test", json={})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF" and r.headers["x-voice-clip"] == "serana"
        last = running.requests()[-1]
        assert last["ref_bytes"] == len(mine) and last["input"] == "The road north is long, but the inn ahead will have a warm fire."

    async def test_with_two_clips_the_one_asked_for_is_used(self, running):
        a, b = clip(1.0), clip(3.0)
        running.engine.clips.add("serana", a)
        running.engine.clips.add("lydia", b)
        r = await running.client.post("/config/voice/test", json={"voice": "lydia"})
        assert r.status_code == 200 and running.requests()[-1]["ref_bytes"] == len(b)
        r = await running.client.post("/config/voice/test", json={"voice": "nobody"})
        assert r.status_code == 200 and running.requests()[-1]["ref_bytes"] in (len(a), len(b)), "an unknown name falls back to one of their own clips"

    async def test_without_their_own_clip_the_one_skyrimnet_sent_is_used(self, running):
        await speak(running.client, "Hello there.")             # SkyrimNet's first upload is kept
        kept = running.requests()[-1]["ref_bytes"]
        r = await running.client.post("/config/voice/test", json={})
        assert r.status_code == 200 and r.content[:4] == b"RIFF" and running.requests()[-1]["ref_bytes"] == kept

    async def test_a_voice_that_is_not_running_says_so_plainly(self, running):
        await running.client.post("/config/voice/stop")
        await until(lambda: running.engine.state != "ready")
        running.engine.clips.add("serana", clip(1.0))
        r = await running.client.post("/config/voice/test", json={})
        assert r.status_code == 409 and r.json()["detail"] and "{" not in r.json()["detail"]

    async def test_raw_program_text_never_reaches_the_page(self, running, monkeypatch):
        running.engine.clips.add("serana", clip(1.0))

        async def broken(*a, **k):
            raise ve.VoiceEngineError(500, "(500) {\"detail\":\"voice clone ICL mode requires reference text\"}")
        monkeypatch.setattr(running.engine, "synthesize", broken)
        r = await running.client.post("/config/voice/test", json={})
        assert r.status_code == 500 and r.json()["detail"] == "The voice couldn't speak a line. Click **Log** to see why."

    async def test_a_plain_sentence_from_the_voice_passes_through(self):
        assert voice_wiring.plain_test_failure("Type the exact words spoken in the voice clip.") == "Type the exact words spoken in the voice clip."
        assert voice_wiring.plain_test_failure("") == voice_wiring.TEST_FAILED and voice_wiring.plain_test_failure("{x}") == voice_wiring.TEST_FAILED

    async def test_through_a_courier_the_line_is_made_there_with_the_clip_there(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        mine = clip(2.0)
        await rig.client.post("/config/voice/clips/add", json={"voice": "serana", "dataB64": base64.b64encode(mine).decode()})
        before = len(rig.requests())
        r = await rig.client.post("/config/voice/test", json={})
        assert r.status_code == 200 and r.content[:4] == b"RIFF"
        assert len(rig.requests()) == before + 1 and rig.requests()[-1]["ref_bytes"] == len(mine) and rig.requests()[-1]["input"].startswith("The road north")

    async def test_through_a_courier_with_no_clip_nothing_is_sent(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        before = len(rig.requests())
        r = await rig.client.post("/config/voice/test", json={})
        assert r.json().get("needsClip") is True and len(rig.requests()) == before
