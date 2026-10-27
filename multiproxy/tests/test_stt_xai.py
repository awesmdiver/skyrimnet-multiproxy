"""Speech to text through xAI (4.0): off by default, then SkyrimNet's clip is passed to xAI and the text comes back. Stand-in xAI server on 127.0.0.1; the real one is never called."""
import asyncio
import json
import struct
import time

import aiohttp
import httpx
import pytest
import pytest_asyncio
from aiohttp import web
from fastapi import FastAPI

import stt_xai
import voice_ui
import voice_wiring
from tests.test_stt_capture import form
from tests.test_voice_service import wav
from tests.voice_helpers import make_engine

pytestmark = pytest.mark.asyncio

KEY = "xai-this-key-must-never-be-logged"
WORDS = "the secret words said aloud"


class FakeXai:
    """Answers like xAI's /v1/stt: `mode` picks the answer. `seen` keeps what it received."""

    def __init__(self):
        self.mode, self.seen, self.calls = "ok", None, 0

    async def handle(self, request: web.Request):
        self.calls += 1
        raw = await request.read()
        self.seen = {"auth": request.headers.get("Authorization"), "ctype": request.headers.get("Content-Type", ""), "raw": raw}
        mode = self.mode
        if mode == "slow":
            await asyncio.sleep(5)
        if mode in ("401", "403", "429", "500", "503", "400"):
            return web.json_response({"error": "raw body of xAI that must never reach the player"}, status=int(mode))
        if mode == "nested":
            return web.Response(body=b"[" * 200000, content_type="application/json")
        if mode == "garbage":
            return web.Response(text="<html>no</html>")
        if mode == "notext":
            return web.json_response({"words": []})
        return web.json_response({"text": WORDS, "language": "en", "duration": 2.5, "words": []})


@pytest_asyncio.fixture
async def rig(tmp_path):
    fake = FakeXai()
    xapp = web.Application(client_max_size=64 * 1024 * 1024)
    xapp.router.add_post("/v1/stt", fake.handle)
    runner = web.AppRunner(xapp)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    cfg, logs, state = {}, [], {"key": KEY, "now": 1000.0}
    stt = stt_xai.XaiStt(lambda: dict(cfg), lambda: state["key"], logs.append, url=f"http://127.0.0.1:{port}/v1/stt", clock=lambda: state["now"])
    import stt_capture
    app = FastAPI()
    app.include_router(stt_capture.build_router(logs.append, stt))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000")
    yield type("Rig", (), dict(fake=fake, cfg=cfg, logs=logs, state=state, stt=stt, client=client, tmp=tmp_path))
    await client.aclose()
    await runner.cleanup()


def clip(seconds=2.5, prompt="names of the people nearby", language="en"):
    parts = [("file", wav(44100, 1, 2, seconds=seconds), "audio.wav", "audio/wav"), ("model", "openai/whisper-1", None, None), ("prompt", prompt, None, None)]
    if language:
        parts.insert(2, ("language", language, None, None))
    return form(parts)


async def say(rig, **kw):
    body, ctype = clip(**kw)
    return await rig.client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})


async def test_with_the_switch_off_the_new_sentence_is_the_answer_and_nothing_goes_to_xai(rig):
    r = await say(rig)
    assert r.status_code == 501
    assert r.json()["error"]["message"] == "Speech to text is off. Turn it on in MultiProxy on the Voice tab."
    assert rig.fake.calls == 0


async def test_with_the_switch_on_the_clip_goes_to_xai_with_the_file_last_and_the_text_comes_back(rig):
    rig.cfg["stt_xai"] = True
    body, ctype = clip(seconds=2.5)
    r = await rig.client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})
    assert r.status_code == 200 and r.json() == {"text": WORDS}
    seen = rig.fake.seen
    assert seen["auth"] == f"Bearer {KEY}"
    parts = stt_xai.parse_form(seen["ctype"], seen["raw"])
    names = [n for n in (p.split(b'name="')[1].split(b'"')[0].decode() for p in seen["raw"].split(b"Content-Disposition")[1:])]
    assert names == ["language", "format", "file"]                      # the file part is last; model and prompt are not forwarded
    assert parts["language"][2] == b"en" and parts["format"][2] == b"true"
    assert parts["file"][2] == wav(44100, 1, 2, seconds=2.5)           # the sound arrives byte for byte


async def test_a_language_that_is_not_a_short_code_is_not_passed_on(rig):
    rig.cfg["stt_xai"] = True
    r = await say(rig, language="en; drop table")
    assert r.status_code == 200
    names = [p.split(b'name="')[1].split(b'"')[0].decode() for p in rig.fake.seen["raw"].split(b"Content-Disposition")[1:]]
    assert names == ["file"]


@pytest.mark.parametrize("mode,status,sentence", [
    ("401", 401, "xAI didn't accept the key. Check it on the Providers tab."),
    ("403", 401, "xAI didn't accept the key. Check it on the Providers tab."),
    ("429", 503, "xAI is busy right now. Try again in a moment."),
    ("500", 503, "xAI is busy right now. Try again in a moment."),
    ("400", 502, "xAI couldn't read that recording."),
    ("garbage", 502, "xAI sent back something MultiProxy couldn't read."),
    ("notext", 502, "xAI sent back something MultiProxy couldn't read."),
])
async def test_each_failure_is_one_plain_sentence_in_the_openai_style(rig, mode, status, sentence):
    rig.cfg["stt_xai"] = True
    rig.fake.mode = mode
    r = await say(rig)
    assert r.status_code == status and r.json()["error"]["message"] == sentence
    assert "raw body" not in r.text and KEY not in r.text
    assert rig.fake.calls == 1                                         # (no automatic retry)


async def test_a_slow_xai_gives_up_with_a_plain_sentence_and_no_second_try(rig, monkeypatch):
    monkeypatch.setattr(stt_xai, "TIMEOUT_S", 0.3)
    rig.cfg["stt_xai"] = True
    rig.fake.mode = "slow"
    r = await say(rig)
    assert r.status_code == 504 and r.json()["error"]["message"] == "xAI didn't answer in time. Try again."
    assert rig.fake.calls == 1


async def test_no_key_is_said_plainly_and_nothing_is_sent(rig):
    rig.cfg["stt_xai"] = True
    rig.state["key"] = None
    r = await say(rig)
    assert r.status_code == 401 and r.json()["error"]["message"] == "There is no xAI key saved. Add it on the Providers tab."
    assert rig.fake.calls == 0


async def test_a_nothing_at_all_request_and_an_unreachable_xai_are_plain_too(rig):
    rig.cfg["stt_xai"] = True
    r = await rig.client.post("/v1/audio/transcriptions", content=b"x", headers={"content-type": "text/plain"})
    assert r.status_code == 400 and r.json()["error"]["message"] == "No recording came with that request."
    rig.stt.url = "http://127.0.0.1:1/v1/stt"
    r = await say(rig)
    assert r.status_code == 502 and r.json()["error"]["message"] == "MultiProxy couldn't reach xAI. Check your internet connection."


async def test_a_clip_that_is_too_long_or_too_big_is_refused_before_xai_hears_it(rig, monkeypatch):
    rig.cfg["stt_xai"] = True
    r = await say(rig, seconds=61)
    assert r.status_code == 413 and r.json()["error"]["message"] == "That recording is longer than 60 seconds."
    assert (await say(rig, seconds=59)).status_code == 200
    monkeypatch.setattr(stt_xai, "MAX_BYTES", 1000)
    r = await say(rig, seconds=1)
    assert r.status_code == 413 and r.json()["error"]["message"] == "That recording is too big."
    assert rig.fake.calls == 1


async def test_thirty_clips_a_minute_then_a_plain_error_and_the_minute_passing_frees_it(rig):
    rig.cfg["stt_xai"] = True
    for _ in range(stt_xai.PER_MINUTE):
        assert (await say(rig, seconds=0.1)).status_code == 200
    r = await say(rig, seconds=0.1)
    assert r.status_code == 429 and r.json()["error"]["message"] == "Too many recordings in a minute. Wait a moment and try again."
    assert rig.fake.calls == stt_xai.PER_MINUTE
    rig.state["now"] += 61
    assert (await say(rig, seconds=0.1)).status_code == 200


async def test_no_log_line_holds_the_sound_the_text_or_the_key(rig):
    rig.cfg["stt_xai"] = True
    await say(rig, prompt=WORDS)
    for mode in ("401", "garbage", "429"):
        rig.fake.mode = mode
        await say(rig)
    assert rig.logs[0] == "Speech to text via xAI: 2.5 s of sound, answered in " + rig.logs[0].split("answered in ")[1]
    assert rig.logs[1:] == ["Speech to text via xAI failed: key_refused_401.", "Speech to text via xAI failed: bad_reply.", "Speech to text via xAI failed: busy_429."]
    blob = " ".join(rig.logs)
    assert KEY not in blob and WORDS not in blob and "RIFF" not in blob and "raw body" not in blob


async def test_the_last_request_is_real_data_only(rig):
    assert rig.stt.status()["last"] is None
    rig.cfg["stt_xai"] = True
    await say(rig)
    rig.state["now"] += 4
    last = rig.stt.status()["last"]
    assert last["ok"] is True and last["ago"] == 4 and last["took"] >= 0
    rig.fake.mode = "401"
    await say(rig)
    assert rig.stt.status()["last"]["ok"] is False


async def test_the_sound_is_never_written_to_disk(rig, tmp_path, monkeypatch):
    rig.cfg["stt_xai"] = True
    monkeypatch.chdir(tmp_path)
    sound = wav(44100, 1, 2, seconds=2.5)[:4000]
    await say(rig)
    assert not [p for p in tmp_path.rglob("*") if p.is_file() and sound in p.read_bytes()]          # (no file anywhere under the working folder holds the sound)


# ---- the Voice tab's switch -----------------------------------------------------------------------------------------------------------------------
@pytest_asyncio.fixture
async def tab(tmp_path):
    engine, _src, _ = make_engine(tmp_path / "g")
    cfg, key = {}, {"v": None}
    app = FastAPI()
    voice_wiring.setup(app, lambda: json.loads(json.dumps(cfg)), lambda d: (cfg.clear(), cfg.update(d)), lambda: str(tmp_path / "settings"), lambda s: s,
                       __import__("logging").getLogger("t"), this_pc=lambda: "gaming-pc", engine=engine, main_port=lambda: 8123, xai_key=lambda: key["v"])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8123", timeout=30)
    yield type("Tab", (), dict(client=client, cfg=cfg, key=key))
    await client.aclose()
    await engine.close()


async def test_it_is_off_by_default_and_the_card_data_has_the_address_and_no_invented_numbers(tab):
    st = (await tab.client.get("/config/voice/overview")).json()["stt"]
    assert st == {"on": False, "hasKey": False, "last": None, "address": "http://127.0.0.1:8123/v1/audio/transcriptions"}


async def test_turning_it_on_needs_the_saved_xai_key_and_turning_it_off_forgets_the_setting(tab):
    r = await tab.client.post("/config/voice/stt", json={"on": True})
    assert r.status_code == 400 and "stt_xai" not in tab.cfg
    tab.key["v"] = KEY
    r = await tab.client.post("/config/voice/stt", json={"on": True})
    assert r.status_code == 200 and r.json()["on"] is True and tab.cfg["stt_xai"] is True
    r = await tab.client.post("/config/voice/stt", json={"on": False})
    assert r.json()["on"] is False and "stt_xai" not in tab.cfg


def test_the_card_has_the_confirm_wording_the_steps_and_a_greyed_switch_without_a_key():
    js = voice_ui.VOICE_JS
    for text in ["Turn my voice into text with xAI",
                 "Your voice recordings will be sent to xAI to be turned into text. xAI\\'s terms apply. Turn this on?",
                 "Turn on</button>", "Add your xAI key", "No requests yet",
                 "In SkyrimNet, open the speech settings and choose <b>External Whisper</b>.", "Paste this address:", "Press the push-to-talk key and speak."]:
        assert text in js, text
    assert "(s.hasKey ? '' : ' disabled')" in js
    assert "el.checked = false; vcModal = { type: 'stt' }" in js             # turning on opens the dialog first


# --- the four safety fixes (security review 2026-10-24, SF1 to SF4) ---

def raw_clip(data: bytes):
    return form([("file", data, "audio.wav", "audio/wav"), ("model", "openai/whisper-1", None, None)])


async def post_raw(rig, data: bytes):
    body, ctype = raw_clip(data)
    return await rig.client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})


async def test_a_third_clip_at_the_same_moment_is_refused_with_a_plain_sentence(rig, monkeypatch):
    monkeypatch.setattr(stt_xai, "TIMEOUT_S", 1.0)
    rig.cfg["stt_xai"] = True
    rig.fake.mode = "slow"
    results = await asyncio.gather(say(rig), say(rig), say(rig))
    assert sorted(r.status_code for r in results) == [429, 504, 504]
    refused = next(r for r in results if r.status_code == 429)
    assert refused.json()["error"]["message"] == "Too many recordings at once. Wait a moment and try again."
    assert rig.fake.calls == 2 and rig.stt._active == 0
    rig.fake.mode = "ok"
    assert (await say(rig)).status_code == 200                          # (the slots come back)


async def test_an_upload_that_arrives_too_slowly_is_cut_off(rig, monkeypatch):
    monkeypatch.setattr(stt_xai, "UPLOAD_S", 0.3)
    rig.cfg["stt_xai"] = True
    body, ctype = clip()

    async def drip():
        yield body[:100]
        await asyncio.sleep(2)
        yield body[100:]
    r = await rig.client.post("/v1/audio/transcriptions", content=drip(), headers={"content-type": ctype})
    assert r.status_code == 408 and r.json()["error"]["message"] == "That recording took too long to arrive. Try again."
    assert rig.fake.calls == 0 and rig.stt._active == 0


async def test_a_13_mb_clip_is_too_big_and_a_real_one_is_far_below_the_cap(rig):
    rig.cfg["stt_xai"] = True
    assert stt_xai.MAX_BYTES == 12 * 1024 * 1024
    r = await post_raw(rig, wav(44100, 1, 2, seconds=1) + b"\x00" * (13 * 1024 * 1024))
    assert r.status_code == 413 and r.json()["error"]["message"] == "That recording is too big."
    assert rig.fake.calls == 0


def forged(declared: int, real: int, byte_rate: int = 88200) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, byte_rate, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + declared) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", declared) + b"\x00" * real


async def test_a_header_that_says_one_thing_and_a_size_that_says_another_is_refused(rig):
    rig.cfg["stt_xai"] = True
    for data in (forged(100, 8 * 1024 * 1024), forged(1000, 1000, byte_rate=5_000_000)):
        r = await post_raw(rig, data)
        assert r.status_code == 400 and r.json()["error"]["message"] == "That recording isn't a valid sound file."
    assert rig.fake.calls == 0
    assert (await post_raw(rig, forged(88200, 88200))).status_code == 200      # an honest one still goes
    assert stt_xai.wav_seconds(forged(0, 88200)) == 1.0                         # (a streamed header: the real size counts)


async def test_other_requests_are_answered_quickly_while_a_big_clip_is_handled(rig):
    rig.cfg["stt_xai"] = True
    big = forged(10 * 1024 * 1024, 10 * 1024 * 1024, byte_rate=192000)
    gaps, stop = [], asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.005)
            now = time.monotonic()
            gaps.append(now - last - 0.005)
            last = now
    t = asyncio.create_task(ticker())
    r = await post_raw(rig, big)
    stop.set()
    await t
    assert r.status_code == 200
    gaps.sort()
    assert len(gaps) > 5 and gaps[-2] < 0.05, gaps[-3:]          # (the single worst gap is allowed: the stand-in xAI and the test client share this loop)


async def test_a_deeply_nested_reply_from_xai_is_the_plain_sentence_and_no_trace(rig):
    rig.cfg["stt_xai"] = True
    rig.fake.mode = "nested"
    r = await say(rig)
    assert r.status_code == 502 and r.json()["error"]["message"] == "xAI sent back something MultiProxy couldn't read."
    assert "Traceback" not in r.text and not any("[[[[" in line for line in rig.logs)


def test_no_screen_or_help_page_says_multiproxy_does_not_save_the_recordings():
    """The director, 2026-10-24: the confirm says only what is needed. The behaviour (nothing is saved or logged) is unchanged and is tested above; it is just not said on screen."""
    import help_content
    page = next(t for t in help_content.load_topics() if t["title"] == "Speech to text")
    for text in (voice_ui.VOICE_JS, page["html"], page["text"]):
        assert "does not save" not in text and "doesn't save" not in text


def test_the_voice_tab_card_links_to_its_help_page_and_the_page_is_under_voice_after_higgs():
    import help_content
    assert r"openHelp(\'speech-to-text\')" in voice_ui.VOICE_JS
    topics = help_content.load_topics()
    ids = [t["id"] for t in topics]
    assert "speech-to-text" in ids and ids.index("speech-to-text") == ids.index("higgs") + 1
    assert next(t for t in topics if t["id"] == "speech-to-text")["group"] == "Voice"
