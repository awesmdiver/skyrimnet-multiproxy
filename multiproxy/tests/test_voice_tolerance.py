"""The local voice routes never trip SkyrimNet over (2026-10-06): no key needed (and one that is sent is ignored), any reasonable sample accepted and brought to the
engine's 24 kHz mono, extra fields ignored, an unreadable sample tolerated, and a way to log a request's field NAMES (never values) so SkyrimNet's real ones can be captured.
Stand-ins only: the fake engine from tests/voice_helpers.py."""
import array
import io
import json
import struct
import wave

import httpx
import pytest
import pytest_asyncio

import voice_clips as vc
import voice_service as vs
from tests.test_voice_service import Env, engine_requests, env, multipart, speak, wav  # noqa: F401  (the fixture and the helpers)

pytestmark = pytest.mark.asyncio


def tone(rate, channels=1, width=2, seconds=1.0, float_samples=False, extensible=False, streamed_size=False) -> bytes:
    """A WAV made by hand (so the shapes the standard library refuses can be made too)."""
    n = int(rate * seconds)
    if float_samples:
        frames = array.array("f", [((i % 100) - 50) / 100 for i in range(n * channels)]).tobytes()
        bits, tag = 32, 3
    elif width == 1:
        frames, bits, tag = bytes(128 + (i % 50) for i in range(n * channels)), 8, 1
    elif width == 3:
        frames, bits, tag = b"".join(((i % 200) * 5000).to_bytes(3, "little", signed=True) for i in range(n * channels)), 24, 1
    else:
        frames, bits, tag = struct.pack(f"<{n * channels}h", *[(i % 200) * 50 for i in range(n * channels)]), 16, 1
    align = channels * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHHHHIH14s", 0xFFFE, channels, rate, rate * align, align, bits, 22, bits, 3, tag, b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71")
    else:
        fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, bits)
    size = 0xFFFFFFFF if streamed_size else len(frames)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", size) + frames
    return b"RIFF" + struct.pack("<I", len(body)) + body


def info(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()


# ---- the sample: every rate and channel count ----------------------------------------------------------------------------------------------
class TestSamples:
    @pytest.mark.parametrize("rate", [8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000])
    @pytest.mark.parametrize("channels", [1, 2])
    async def test_every_reasonable_rate_and_channel_count_comes_out_24k_mono_16_bit_of_the_same_length(self, rate, channels):
        out = vc.prepare_reference(wav(rate, channels, 2, seconds=1.0))
        r, ch, width, n = info(out)
        assert (r, ch, width) == (24000, 1, 2) and abs(n - 24000) <= 2

    @pytest.mark.parametrize("width", [1, 2, 3])
    async def test_8_16_and_24_bit_samples_are_taken(self, width):
        data = tone(16000, 1, width)
        assert info(vc.prepare_reference(data))[:3] == (24000, 1, 2)

    async def test_floating_point_samples_which_the_standard_library_refuses_are_taken(self):
        data = tone(16000, 2, float_samples=True)
        with pytest.raises(wave.Error):
            wave.open(io.BytesIO(data))
        r, ch, width, n = info(vc.prepare_reference(data))
        assert (r, ch, width) == (24000, 1, 2) and abs(n - 24000) <= 2

    async def test_the_extensible_header_and_a_size_left_unset_by_a_program_that_streamed_it_are_taken(self):
        out = vc.prepare_reference(tone(16000, 1, 2, extensible=True, streamed_size=True))
        assert info(out)[:3] == (24000, 1, 2)

    async def test_a_down_sampled_clip_keeps_its_loudness_and_its_sound_not_noise(self):
        # a slow wave at 44.1 kHz: after averaging down it is still the same slow wave (no aliasing hash)
        n = 44100
        data = struct.pack(f"<{n}h", *[int(10000 * ((i // 100) % 2 * 2 - 1)) for i in range(n)])
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(data)
        out = vc.prepare_reference(buf.getvalue())
        with wave.open(io.BytesIO(out)) as w:
            s = struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes()))
        assert max(s) > 9000 and min(s) < -9000

    async def test_a_very_long_one_is_cut_before_it_is_worked_on(self):
        out = vc.prepare_reference(wav(48000, 2, 2, seconds=40))
        assert info(out)[3] == 24000 * vc.REFERENCE_MAX_SECONDS

    async def test_the_same_sample_is_worked_out_once(self, monkeypatch):
        calls = []
        real = vc._prepare
        monkeypatch.setattr(vc, "_prepare", lambda d: calls.append(1) or real(d))
        w = wav(16000, 1, 2, seconds=0.7)
        a, b = vc.prepare_reference(w), vc.prepare_reference(w)
        assert a == b and len(calls) == 1

    @pytest.mark.parametrize("junk", [b"", b"not audio at all", b"ID3\x03\x00mp3-ish", b"RIFF" + b"\x00" * 40, tone(2000)])
    async def test_what_cannot_be_read_still_raises_for_the_callers_that_must_know(self, junk):
        with pytest.raises(vc.ClipFormatError):
            vc.prepare_reference(junk)


# ---- the whole round trip, at each rate ----------------------------------------------------------------------------------------------------
class TestRoundTrip:
    @pytest.mark.parametrize("rate,channels", [(16000, 1), (22050, 1), (24000, 1), (44100, 1), (44100, 2), (48000, 2)])
    async def test_each_sample_gets_audio_and_the_engine_is_given_24k_mono(self, env, rate, channels):
        audio, _ = await speak(env.client, "", "Hello there.", wav(rate, channels, 2, seconds=1.0))
        assert audio and audio[:4] == b"RIFF"
        assert engine_requests(env)[-1]["input"] == "Hello there." and engine_requests(env)[-1]["ref_bytes"] > 24000 * 2 * 0.9

    async def test_a_float_sample_from_skyrimnet_gets_audio(self, env):
        audio, _ = await speak(env.client, "", "Hello there.", tone(16000, 1, float_samples=True))
        assert audio
        assert engine_requests(env)[-1]["ref_bytes"] is not None

    async def test_a_sample_that_cannot_be_read_no_longer_stops_the_line(self, env):
        audio, _ = await speak(env.client, "", "Hello again.", b"this is not a wav file at all", name="bad.wav")
        assert audio and audio[:4] == b"RIFF"
        assert vs.UNREADABLE_SAMPLE in env.logs
        assert engine_requests(env)[-1]["input"] == "Hello again." and engine_requests(env)[-1]["ref_bytes"] is None

    async def test_an_upload_without_a_file_name_is_still_a_sample(self, env):
        boundary = "----b"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + wav(16000) + f"\r\n--{boundary}--\r\n".encode()
        up = await env.client.post("/gradio_api/upload", content=body, headers={"content-type": f"multipart/form-data; boundary={boundary}"})
        assert up.status_code == 200 and up.json()[0].endswith("voice.wav")

    async def test_extra_form_fields_in_the_upload_are_ignored(self, env):
        boundary = "----b"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_hash\"\r\n\r\nabc\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.wav\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n").encode() + wav(16000) + f"\r\n--{boundary}--\r\n".encode()
        up = await env.client.post("/gradio_api/upload?upload_id=xyz", content=body, headers={"content-type": f"multipart/form-data; boundary={boundary}"})
        assert up.status_code == 200 and len(up.json()) == 1


# ---- keys and extra fields -----------------------------------------------------------------------------------------------------------------
class TestNoKeyAndExtras:
    HEADERS = [{}, {"Authorization": "Bearer not-a-real-key"}, {"Authorization": "Bearer "}, {"X-API-Key": "wrong"}, {"api-key": "wrong", "Authorization": "Basic Zm9v"}]

    @pytest.mark.parametrize("headers", HEADERS)
    async def test_the_whole_flow_works_with_no_key_or_any_wrong_one(self, env, headers):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.client._transport.app), base_url="http://127.0.0.1:8000", headers=headers)
        try:
            audio, _ = await speak(client, "", "Hello there.", wav(16000))
            assert audio
            assert (await client.post("/v1/audio/speech", json={"input": "Hello there."})).status_code == 200
        finally:
            await client.aclose()

    async def test_extra_fields_in_the_request_are_ignored(self, env):
        r = await env.client.post("/gradio_api/call/generate_audio", json={"data": ["Hello there.", None, 0.8], "event_data": None, "fn_index": 3, "trigger_id": 9, "session_hash": "abc", "surprise": {"a": 1}})
        assert r.status_code == 200
        res = await env.client.get(f"/gradio_api/call/generate_audio/{r.json()['event_id']}")
        assert res.text.startswith("event: complete")

    async def test_a_bare_list_instead_of_an_object_is_taken_too(self, env):
        r = await env.client.post("/gradio_api/call/generate_audio", json=["Hello there.", None, 0.8])
        assert r.status_code == 200

    async def test_the_thirty_slot_layout_is_still_read_with_extra_slots_after_it(self, env):
        data = [None] * 33
        data[1], data[2], data[20], data[23], data[26] = "Hello there.", "en-us", 0.8, 0.8, -1
        data[30] = {"something": "new"}
        r = await env.client.post("/gradio_api/call/generate_audio", json={"data": data})
        assert r.status_code == 200
        await env.client.get(f"/gradio_api/call/generate_audio/{r.json()['event_id']}")
        assert engine_requests(env)[-1]["input"] == "Hello there." and engine_requests(env)[-1]["temperature"] == pytest.approx(0.8)


# ---- the OpenAI-style speech route ----------------------------------------------------------------------------------------------------------
class TestSpeechRoute:
    async def test_a_plain_request_is_answered_with_a_wav(self, env):
        r = await env.client.post("/v1/audio/speech", json={"model": "tts-1", "input": "Hello there.", "voice": "alloy", "response_format": "mp3", "speed": 1.0, "extra": [1, 2]})
        assert r.status_code == 200 and r.content[:4] == b"RIFF" and r.headers["content-type"] == "audio/wav"
        assert engine_requests(env)[-1]["input"] == "Hello there."

    async def test_a_reference_sample_in_the_request_is_brought_to_24k_mono_first(self, env):
        import base64
        ref = base64.b64encode(wav(16000, 1, 2, seconds=2.0)).decode()
        for field, value in (("voice_ref", {"type": "base64", "data": ref}), ("reference_audio", ref), ("ref_audio", "data:audio/wav;base64," + ref)):
            r = await env.client.post("/v1/audio/speech", json={"input": "Hello there.", field: value, "reference_text": "whatever"})
            assert r.status_code == 200, field
            assert engine_requests(env)[-1]["ref_bytes"] > 24000 * 2 * 1.8, field

    async def test_it_works_under_voice_too_and_names_the_text_field_in_other_ways(self, env):
        assert (await env.client.post("/voice/v1/audio/speech", json={"text": "Hello there."})).status_code == 200
        assert (await env.client.post("/v1/audio/speech", json={"prompt": "Hello there."})).status_code == 200

    async def test_a_request_with_no_text_or_not_json_gets_the_plain_line(self, env):
        for kwargs in ({"json": {"voice": "alloy"}}, {"content": b"not json"}, {"json": ["x"]}):
            r = await env.client.post("/v1/audio/speech", **kwargs)
            assert r.status_code == 400 and r.json()["error"]["message"] == vs.UNRECOGNISED

    async def test_when_higgs_is_not_running_it_says_so_at_once(self, env):
        await env.engine.stop()
        r = await env.client.post("/v1/audio/speech", json={"input": "Hello there."})
        assert r.status_code == 503 and r.json()["error"]["message"] == vs.NOT_RUNNING


# ---- the field-name log --------------------------------------------------------------------------------------------------------------------------
class TestFieldNames:
    async def test_with_the_switch_on_a_request_is_logged_by_names_only(self, env, monkeypatch):
        monkeypatch.setenv(vs.FIELDS_SWITCH, "1")
        secret_text, secret_key = "a very private line of dialogue", "sk-this-key-must-never-be-logged"
        body, ctype = multipart("femalenord.wav", wav(16000))
        await env.client.post("/gradio_api/upload", content=body, headers={"content-type": ctype, "Authorization": f"Bearer {secret_key}", "X-Surprise": "1"})
        await env.client.post("/gradio_api/call/generate_audio", json={"data": [secret_text, None, 0.8], "fn_index": 3}, headers={"Authorization": f"Bearer {secret_key}"})
        await env.client.post("/v1/audio/speech", json={"input": secret_text, "voice_ref": "A" * 200, "speed": 1.2})
        lines = [m for m in env.logs if m.startswith("Voice request ")]
        assert len(lines) == 3
        text = "\n".join(lines)
        assert secret_text not in text and secret_key not in text and "AAAA" not in text and "RIFF" not in text
        assert "key sent yes" in lines[0] and "x-surprise" in lines[0] and "files(file," in lines[0]
        assert "data=list" in lines[1] and "fn_index=int" in lines[1] and "data slots: 0=text, 2=number" in lines[1]
        assert "input=text" in lines[2] and "voice_ref=text" in lines[2] and "speed=number" in lines[2] and "key sent no" in lines[2]

    async def test_with_the_switch_off_nothing_extra_is_logged(self, env, monkeypatch):
        monkeypatch.delenv(vs.FIELDS_SWITCH, raising=False)
        await env.client.post("/v1/audio/speech", json={"input": "Hello there."})
        assert not any(m.startswith("Voice request ") for m in env.logs)


# ---- the guard is untouched --------------------------------------------------------------------------------------------------------------------------
class TestGuardStillHolds:
    async def test_a_foreign_host_and_a_foreign_origin_are_still_refused_on_the_voice_routes(self):
        import local_guard
        assert local_guard.refusal("POST", "/gradio_api/call/generate_audio", {b"host": "evil.example:8000"}, 8000)
        assert local_guard.refusal("POST", "/v1/audio/speech", {b"host": "127.0.0.1:8000", b"origin": "https://evil.example"}, 8000)
        assert local_guard.refusal("POST", "/v1/audio/speech", {b"host": "127.0.0.1:8000"}, 8000) is None, "a program on this device (no Origin) is let through, with or without a key"
        assert local_guard.refusal("POST", "/v1/audio/speech", {b"host": "127.0.0.1:8000", b"authorization": "Bearer x"}, 8000) is None
