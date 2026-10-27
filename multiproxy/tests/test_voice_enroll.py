"""SkyrimNet's OpenAI-Compatible engine works with MultiProxy's voice address (2026-10-06): it registers a voice first (`POST /v1/audio/voices`: `title`, `ref_audio`,
`ref_text`), then asks for speech with it (`POST /v1/audio/speech`: `model`, `input`, `voice`). Stand-in engine only; the relayed case goes through a stand-in Courier."""
import base64
import json

import httpx
import pytest

import voice_service as vs
from tests.test_voice_courier import PC, installed_rig, move_to, rig  # noqa: F401  (the Courier rig)
from tests.test_voice_service import engine_requests, env, wav  # noqa: F401  (the fixture and the helpers)

pytestmark = pytest.mark.asyncio

TITLE = "actor:1242669203022171874"
KEY = "actor_1242669203022171874"
WORDS = "Right, let's get this straight. I don't have time for games."


def form(title=TITLE, audio=None, words=WORDS, extra=None):
    boundary = "----enrolboundary"
    parts = []
    if title is not None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\n{title}\r\n".encode())
    if audio is not None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"ref_audio\"; filename=\"snv1-abc.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n")
    if words is not None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"ref_text\"\r\n\r\n{words}\r\n".encode())
    for k, v in (extra or {}).items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


async def enroll(client, **kw):
    body, ctype = form(**kw)
    return await client.post("/v1/audio/voices", content=body, headers={"content-type": ctype})


class TestEnrolment:
    async def test_the_voice_is_registered_and_its_id_comes_back(self, env):
        r = await enroll(env.client, audio=wav(16000, seconds=1.0))
        assert r.status_code == 200
        j = r.json()
        assert j["id"] == KEY and j["voice_id"] == KEY and j["title"] == TITLE
        assert (await env.client.get("/v1/audio/voices")).json() == {"voices": [KEY]}
        assert (await env.client.post("/voice/v1/audio/voices", content=form(audio=wav(16000))[0], headers={"content-type": form()[1]})).status_code == 200

    async def test_the_clip_is_kept_with_its_words_beside_it_as_typed_words(self, env):
        await enroll(env.client, audio=wav(16000, seconds=1.0))
        kept = env.engine.clips.kept_folder
        assert (kept / f"{KEY}.wav").is_file() and (kept / f"{KEY}.txt").read_text(encoding="utf-8").strip() == WORDS
        assert env.engine.clips.words_info(KEY) == (WORDS, "typed")      # so no Qwen3-ASR step is needed for it

    async def test_the_same_title_and_sound_again_changes_nothing_and_a_different_sound_replaces_the_old(self, env):
        a, b = wav(16000, seconds=1.0), wav(16000, seconds=2.0)
        await enroll(env.client, audio=a)
        f = env.engine.clips.kept_folder / f"{KEY}.wav"
        first = f.stat().st_mtime_ns
        assert (await enroll(env.client, audio=a)).status_code == 200
        assert f.stat().st_mtime_ns == first and f.read_bytes() == a
        await enroll(env.client, audio=b, words="New words.")
        assert f.read_bytes() == b and env.engine.clips.words_for(KEY) == "New words."
        assert (await env.client.get("/v1/audio/voices")).json() == {"voices": [KEY]}

    async def test_a_voice_can_be_deleted(self, env):
        await enroll(env.client, audio=wav(16000))
        r = await env.client.delete(f"/v1/audio/voices/{KEY}")
        assert r.json() == {"id": KEY, "deleted": True}
        assert not (env.engine.clips.kept_folder / f"{KEY}.wav").exists() and (await env.client.get("/v1/audio/voices")).json() == {"voices": []}

    async def test_refusals_are_plain_sentences(self, env):
        assert (await enroll(env.client, audio=None)).json()["error"]["message"] == vs.ENROLL_NO_CLIP
        r = await enroll(env.client, audio=b"this is not a wav file at all")
        assert r.status_code == 400 and r.json()["error"]["message"] == vs.BAD_CLIP
        assert (await enroll(env.client, title=None, audio=wav(16000))).status_code == 400
        big = b"RIFF" + b"\0" * (vs.ENROLL_MAX_BYTES + 200000)
        r = await enroll(env.client, audio=big)
        assert r.status_code == 413 and r.json()["error"]["message"] == vs.ENROLL_TOO_BIG

    async def test_nothing_in_the_log_holds_the_sound_or_the_spoken_words(self, env, monkeypatch):
        monkeypatch.setenv(vs.FIELDS_SWITCH, "1")
        await enroll(env.client, audio=wav(16000))
        assert env.logs and all(WORDS not in m and "RIFF" not in m for m in env.logs)
        assert any("title(field" in m and "ref_audio(file" in m for m in env.logs), "names and sizes only"


class TestSpeech:
    async def test_speech_with_an_enrolled_voice_uses_its_clip_and_its_words(self, env):
        await enroll(env.client, audio=wav(16000, seconds=1.0))
        r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Hello there.", "voice": KEY, "response_format": "wav"})
        assert r.status_code == 200 and r.content[:4] == b"RIFF"
        got = engine_requests(env)[-1]
        assert got["input"] == "Hello there." and got["ref_bytes"] and got["reference_text"] == WORDS
        r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Again.", "voice": TITLE})       # the title works as well as the id
        assert engine_requests(env)[-1]["reference_text"] == WORDS

    async def test_a_json_answer_is_given_when_asked_for(self, env):
        r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Hello there.", "response_format": "json"})
        assert base64.b64decode(r.json()["data"])[:4] == b"RIFF"

    async def test_model_accepts_the_running_voice_and_the_old_alias_but_names_another_voice_plainly(self, env):
        for model in ("higgs", "Higgs voice", "HIGGS", "", None, "tts-1", "something-else"):
            body = {"input": "Hello there."} | ({"model": model} if model is not None else {})
            assert (await env.client.post("/v1/audio/speech", json=body)).status_code == 200, model
        r = await env.client.post("/v1/audio/speech", json={"model": "qwen3tts", "input": "Hello there."})
        assert r.status_code == 400 and "Qwen3-TTS voice isn't the voice MultiProxy is running (Higgs voice)" in r.json()["error"]["message"]
        assert "Set SkyrimNet's Model to higgs" in r.json()["error"]["message"]

    async def test_a_foreign_host_or_origin_is_refused_by_the_same_guard_as_every_local_route(self, env, monkeypatch):
        import local_guard
        from fastapi import FastAPI
        monkeypatch.setattr(local_guard, "ENABLED", True)
        app = FastAPI()
        app.add_middleware(local_guard.LocalGuard, log=lambda m: None)
        vs.mount(app, env.service)
        body, ctype = form(audio=wav(16000))
        for base, headers, ok in (("http://evil.example", {}, False), ("http://127.0.0.1:8000", {"origin": "http://evil.example"}, False), ("http://127.0.0.1:8000", {}, True)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5), ), base_url=base) as c:
                r = await c.post("/v1/audio/voices", content=body, headers={"content-type": ctype, **headers})
                assert (r.status_code == 200) == ok, (base, headers, r.status_code)
                assert "access-control-allow-origin" not in r.headers


class TestRelayed:
    async def test_enrol_then_speak_while_the_voice_runs_on_another_device(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        r = await enroll(rig.client, audio=wav(16000, seconds=1.0))
        assert r.status_code == 200 and r.json()["id"] == KEY, r.text
        assert (rig.tmp / "c" / "voice" / "clips" / "from-skyrimnet" / f"{KEY}.wav").is_file(), "the clip was kept on the device the voice runs on"
        before = len(rig.requests())
        r = await rig.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Hello there.", "voice": KEY})
        assert r.status_code == 200 and r.content[:4] == b"RIFF"
        got = rig.requests()[before:]
        assert len(got) == 1 and got[0]["ref_bytes"] and got[0]["reference_text"] == WORDS
        assert (await enroll(rig.client, audio=wav(16000, seconds=1.0))).status_code == 200     # again: nothing sent twice
        assert len(json.loads((rig.tmp / "g" / "voice" / "work" / "enrolled.json").read_text(encoding="utf-8"))) == 1


# ---- the log says which voice made each line, and how long it took (2026-10-07) -----------------------------------------------------------------------
def line_logs(env):
    return [m for m in env.logs if m.startswith("Voice line ")]


class TestTheLineThatSaysWhoSpoke:
    async def test_a_good_openai_line_names_the_voice_the_model_asked_for_the_clip_the_words_and_the_time(self, env):
        await enroll(env.client, audio=wav(16000, seconds=1.0))
        r = await env.client.post("/v1/audio/speech", json={"model": "qwen3tts", "input": "A very private line of dialogue.", "voice": KEY})
        assert r.status_code == 400                                          # (Higgs runs here: another voice's name is refused, and that is logged too)
        r = await env.client.post("/v1/audio/speech", json={"model": "tts-1", "input": "A very private line of dialogue.", "voice": KEY})
        assert r.status_code == 200
        lines = line_logs(env)
        assert lines[0].startswith("Voice line (OpenAI-Compatible) refused; SkyrimNet asked for model qwen3tts: Qwen3-TTS voice isn't the voice MultiProxy is running")
        good = lines[-1]
        assert good.startswith("Voice line (OpenAI-Compatible): Higgs voice answered; SkyrimNet asked for model tts-1 (accepted as the running voice); ")
        assert f"clip: SkyrimNet's clip for {KEY}; words: typed or given by SkyrimNet; " in good and good.endswith(" s of speech.") and " s, " in good

    async def test_the_higgs_route_and_the_test_button_write_their_own_line(self, env):
        body, ctype = __import__("tests.test_voice_service", fromlist=["multipart"]).multipart("femalenord.wav", wav(16000))
        path = (await env.client.post("/gradio_api/upload", content=body, headers={"content-type": ctype})).json()[0]
        call = await env.client.post("/gradio_api/call/generate_audio", json={"data": ["A very private line of dialogue.", {"path": path, "meta": {"_type": "gradio.FileData"}}, 0.8]})
        await env.client.get(f"/gradio_api/call/generate_audio/{call.json()['event_id']}")
        await env.service.speak_test("A very private line of dialogue.", "femalenord")
        higgs, test = line_logs(env)[-2:]
        assert higgs.startswith("Voice line (Higgs): Higgs voice answered; clip: SkyrimNet's clip for femalenord; words: not needed; ")
        assert test.startswith("Voice line (Test button): Higgs voice answered; clip: SkyrimNet's clip for femalenord; ")

    async def test_a_failed_line_gives_the_plain_reason_and_nothing_else(self, env):
        await env.engine.stop()
        r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "A very private line of dialogue."})
        assert r.status_code == 503
        assert line_logs(env)[-1] == f"Voice line (OpenAI-Compatible) refused; SkyrimNet asked for model higgs: {vs.NOT_RUNNING.rstrip('.')}."
        with pytest.raises(Exception):
            await env.service.speak_test("A very private line of dialogue.", "femalenord")
        assert line_logs(env)[-1].startswith("Voice line (Test button): Higgs voice was asked; ") and " failed after " in line_logs(env)[-1]

    async def test_no_line_ever_holds_the_text_the_words_or_the_sound(self, env):
        await enroll(env.client, audio=wav(16000, seconds=1.0))
        await env.client.post("/v1/audio/speech", json={"model": "tts-1", "input": "A very private line of dialogue.", "voice": KEY, "api_key": "sk-must-never-be-logged"})
        await env.service.speak_test("A very private line of dialogue.", KEY)
        text = "\n".join(line_logs(env))
        assert text and "private line" not in text and "get this straight" not in text and "RIFF" not in text and "sk-must" not in text

    async def test_the_voice_row_says_once_when_skyrimnet_asked_for_a_name_that_is_not_the_running_voice(self, env):
        assert env.service.status()["askedModel"] is None
        await env.client.post("/v1/audio/speech", json={"model": "tts-1", "input": "Hello there."})
        assert env.service.status()["askedModel"] == {"asked": "tts-1", "answered": "Higgs voice"}
        await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Hello there."})
        assert env.service.status()["askedModel"] is None


class TestTheLineWhenRelayed:
    async def test_the_line_names_the_other_device(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        logs = []
        rig.handles.service.log = logs.append
        await enroll(rig.client, audio=wav(16000, seconds=1.0))
        await rig.client.post("/v1/audio/speech", json={"model": "tts-1", "input": "A very private line of dialogue.", "voice": KEY})
        line = next((m for m in logs if m.startswith("Voice line (OpenAI-Compatible)")), "")
        assert f"on {PC} answered; SkyrimNet asked for model tts-1 (accepted as the running voice); clip and words: chosen on {PC};" in line, logs
        assert "private line" not in line
