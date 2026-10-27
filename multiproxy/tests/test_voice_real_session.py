"""What a real evening of play sent (2026-09-29, the director's laptop; the shape of it is in tests/fixtures/voice/real-session-2026-09-29.json): SkyrimNet's
health check (`ping.` with no voice-sample file), the named values on every line, tags already in the model's own form, a line that ran away, and a
line the engine ran out of room on."""
import io
import json
import wave
from pathlib import Path

import httpx
import pytest

import voice_service as vs
from tests.test_voice_service import env, engine_requests, speak, wav  # noqa: F401  (a running service with the fake engine behind it)

FX = json.loads((Path(__file__).parent / "fixtures" / "voice" / "real-session-2026-09-29.json").read_text(encoding="utf-8"))


async def call(client, data):
    r = await client.post("/gradio_api/call/generate_audio", json={"data": data})
    if r.status_code != 200:
        return r, None, None
    res = await client.get(f"/gradio_api/call/generate_audio/{r.json()['event_id']}")
    if not res.text.startswith("event: complete"):
        return r, None, res.text
    item = json.loads(res.text.split("data: ", 1)[1])[0]
    audio = await client.get(httpx.URL(item["url"]).path)
    return r, audio.content, res.text


def seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---- the named values ------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestNamedValues:
    def test_every_value_of_the_real_request_is_read(self):
        v = FX["named_values"]
        data = ["Cairn's quiet for now.", {"path": "abc/femaleeventoned.wav", "meta": {"_type": "gradio.FileData"}}, v["language"], v["temperature"], v["top_p"], v["seed"]]
        p = vs.parse_generate_data(data)
        assert p == {"text": "Cairn's quiet for now.", "ref": "abc/femaleeventoned.wav", "temperature": 0.800000011920929, "top_p": 0.8, "seed": 4294967295, "language": "en-us"}

    def test_a_language_code_is_not_mistaken_for_the_text_and_a_second_decimal_is_not_the_temperature(self):
        p = vs.parse_generate_data(["Hello.", "en-us", 0.7, 0.9])
        assert p["text"] == "Hello." and p["language"] == "en-us" and p["temperature"] == 0.7 and p["top_p"] == 0.9 and p["seed"] is None

    def test_seed_4294967295_means_random_and_is_never_passed_as_a_fixed_seed(self):
        assert vs._fixed_seed(4294967295) is None and vs._fixed_seed(-1) is None and vs._fixed_seed(None) is None and vs._fixed_seed(True) is None
        assert vs._fixed_seed(12345) == 12345 and vs._fixed_seed(0) == 0

    async def test_the_engine_gets_the_temperature_and_top_p_and_no_seed_when_the_seed_is_random(self, env):
        v = FX["named_values"]
        r, audio, _ = await call(env.client, ["Cairn's quiet for now.", None, v["language"], v["temperature"], v["top_p"], v["seed"]])
        assert audio[:4] == b"RIFF"
        got = engine_requests(env)[-1]
        assert got["temperature"] == pytest.approx(0.8, abs=1e-6) and got["top_p"] == 0.8 and got["seed"] is None

    async def test_a_fixed_seed_is_passed_on(self, env):
        await call(env.client, ["Hello there.", None, "en-us", 0.8, 0.8, 777])
        assert engine_requests(env)[-1]["seed"] == 777


# ---- tags already in the model's own form --------------------------------------------------------------------------------------------------
class TestRealSessionTags:
    def test_the_session_used_25_different_tags_and_all_are_among_the_models_43(self):
        tags = FX["tags_seen"]
        assert len(tags) == 25
        for t in tags:
            assert t[2:-2] in vs.ALLOWED_TAGS, t
        assert max(tags, key=tags.get) == "<|emotion:amusement|>", "emotion tags are by far the most common"

    @pytest.mark.parametrize("tag", sorted(FX["tags_seen"]))
    def test_each_native_tag_passes_through_untouched(self, tag):
        word = {"sfx": vs.SFX}.get(tag[2:-2].split(":")[0])
        extra = word[tag[2:-2].split(":")[1]] if word else "Hello there."
        text = f"{tag}{extra}"
        out, dropped = vs.convert_tags(text)
        assert out == text and dropped == []

    def test_several_native_tags_in_a_row_stay_as_they_are(self):
        text = "<|emotion:relief|><|prosody:speed_fast|><|style:shouting|>Finally!"
        assert vs.convert_tags(text) == (text, [])

    def test_the_bracket_spelling_of_older_skyrimnet_still_works(self):
        assert vs.convert_tags("[emotion-amusement]Hello.")[0] == "<|emotion:amusement|>Hello."


# ---- the health check ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestHealthCheck:
    async def test_ping_with_no_voice_sample_file_gets_a_short_valid_clip_and_the_engine_is_not_called(self, env):
        before = len(engine_requests(env))
        r, audio, sse = await call(env.client, FX["health_check"]["data"])
        assert r.status_code == 200 and sse.startswith("event: complete") and audio[:4] == b"RIFF"
        assert 0.2 < seconds(audio) < 2.0
        assert len(engine_requests(env)) == before, "the engine wasn't called"
        assert not any("isn't here any more" in m for m in env.logs) and env.service.status()["healthChecksAnswered"] == 1

    async def test_the_missing_file_is_not_an_error_on_any_of_its_spellings(self, env):
        for sample in (None, "", {"path": ""}, {"path": "", "url": "http://x:7860/gradio_api/file=", "orig_name": "maleeventoned.wav"}):
            r, audio, _ = await call(env.client, ["ping.", sample, "en-us", 0.8, 0.8, 4294967295])
            assert audio and audio[:4] == b"RIFF", sample

    async def test_a_ping_is_answered_at_once_even_with_a_line_being_made(self, env):
        r, audio, _ = await call(env.client, ["ping", {"path": ""}])
        assert audio

    async def test_a_stopped_higgs_gets_the_clean_not_running_error_so_skyrimnet_shows_the_right_state(self, env):
        await env.engine.stop()
        r = await env.client.post("/gradio_api/call/generate_audio", json={"data": FX["health_check"]["data"]})
        assert r.status_code == 503 and r.json()["detail"] == vs.NOT_RUNNING

    async def test_a_real_line_that_merely_says_ping_with_a_voice_sample_goes_to_the_engine(self, env):
        audio, _ = await speak(env.client, "", "ping.", wav())
        assert audio and engine_requests(env)[-1]["input"] == "ping."

    async def test_a_real_line_with_a_missing_sample_still_gets_the_old_plain_error(self, env):
        r, audio, text = await call(env.client, ["Hello there.", {"path": "gone/x.wav"}, "en-us", 0.8, 0.8, 4294967295])
        assert r.status_code == 400 and audio is None and "voice clip for this line is missing" in r.text


# ---- runaway lines -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestRunawayLines:
    def test_the_rule_and_the_real_runaway(self):
        words17 = " ".join(["word"] * 17)
        assert vs.expected_seconds(words17) == pytest.approx(6.8) and vs.cap_seconds(words17) == pytest.approx(16.6)
        assert vs.cap_seconds("Ahh!") == 4.0, "never below the floor"
        assert vs.expected_seconds("<|prosody:speed_slow|>" + words17) == pytest.approx(6.8 * 1.3)
        assert vs.expected_seconds("<|emotion:fear|>one two") == pytest.approx(0.8), "tags aren't words"

    def test_none_of_the_494_real_lines_would_have_been_cut(self):
        for words, secs in FX["words_and_seconds"]:
            assert secs <= vs.cap_seconds(" ".join(["word"] * words)), (words, secs)
        assert max(s for _, s in FX["words_and_seconds"]) < 15, "the real session's longest normal line"

    def test_cutting_keeps_a_valid_wav_with_a_fade(self):
        import math, struct
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 7))) for i in range(24000 * 10)))
        out = vs.cut_wav(buf.getvalue(), 3.0)
        assert seconds(out) == pytest.approx(3.0, abs=0.01)
        with wave.open(io.BytesIO(out), "rb") as w:
            frames = w.readframes(w.getnframes())
        last = struct.unpack("<h", frames[-2:])[0]
        assert abs(last) < 200, "faded to quiet at the cut"

    async def test_a_line_that_runs_away_is_cut_at_its_cap_and_logged_once(self, env):
        r, audio, _ = await call(env.client, ["RUNAWAY the water is warm and there is room enough for all of us", None, "en-us", 0.8, 0.8, 4294967295])
        cap = vs.cap_seconds("RUNAWAY the water is warm and there is room enough for all of us")
        assert seconds(audio) == pytest.approx(cap, abs=0.05) and seconds(audio) < 20
        assert sum("ran far too long" in m for m in env.logs) == 1 and env.service.status()["runawayLinesCut"] == 1

    async def test_a_normal_line_is_not_touched(self, env):
        r, audio, _ = await call(env.client, ["Hello there, friend.", None, "en-us", 0.8, 0.8, 4294967295])
        assert seconds(audio) == pytest.approx(1.0, abs=0.01) and not any("ran far too long" in m for m in env.logs)


# ---- "ran out of room before finishing" ----------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestOutOfRoomRetries:
    async def test_the_first_retry_uses_a_fresh_random_seed_and_works(self, env):
        n0 = len(engine_requests(env))
        r, audio, _ = await call(env.client, ["TL1 <|emotion:fear|>Stay close to me.", None, "en-us", 0.8, 0.8, 4294967295])
        assert audio
        sent = engine_requests(env)[n0:]
        assert len(sent) == 2 and sent[0]["seed"] is None and isinstance(sent[1]["seed"], int)
        assert sum("ran out of room" in m for m in env.logs) == 1

    async def test_then_once_more_without_the_control_tags(self, env):
        n0 = len(engine_requests(env))
        r, audio, _ = await call(env.client, ["TL2 <|emotion:fear|>Stay close to me.", None, "en-us", 0.8, 0.8, 4294967295])
        assert audio
        sent = engine_requests(env)[n0:]
        assert len(sent) == 3 and "<|" in sent[0]["input"] and "<|" in sent[1]["input"] and "<|" not in sent[2]["input"]
        assert sum("ran out of room" in m for m in env.logs) == 2 and any("without the style tags" in m for m in env.logs)

    async def test_if_that_fails_too_the_error_is_clean_and_there_are_no_more_tries(self, env):
        n0 = len(engine_requests(env))
        r, audio, text = await call(env.client, ["TL3 <|emotion:fear|>Stay close to me.", None, "en-us", 0.8, 0.8, 4294967295])
        assert audio is None and vs.COULD_NOT_FINISH in text
        assert len(engine_requests(env)) - n0 == 3
        assert env.engine.status()["ready"], "the engine itself is fine"

    async def test_a_line_with_no_tags_to_drop_fails_cleanly_after_the_seed_retry(self, env):
        n0 = len(engine_requests(env))
        r, audio, text = await call(env.client, ["TL3 Stay close to me.", None, "en-us", 0.8, 0.8, 4294967295])
        assert audio is None and vs.COULD_NOT_FINISH in text and len(engine_requests(env)) - n0 == 2

    async def test_a_normal_line_makes_one_engine_call(self, env):
        n0 = len(engine_requests(env))
        await call(env.client, ["Stay close to me.", None, "en-us", 0.8, 0.8, 4294967295])
        assert len(engine_requests(env)) - n0 == 1


# ---- the real capture (SkyrimNet 0.26.0 pressing Test Voice, 2026-10-02) ---------------------------------------------------------------------
CAPTURES = Path(__file__).parent / "fixtures" / "voice" / "captures"


def captured_calls():
    return sorted(CAPTURES.glob("*/[0-9][0-9][0-9]-POST-*generate*audio*.json"))


@pytest.mark.skipif(not captured_calls(), reason="no real capture")
@pytest.mark.asyncio
class TestRealCapture:
    def data(self):
        return json.loads(captured_calls()[0].read_text(encoding="utf-8"))["body"]["json"]["data"]

    def test_the_wire_order_is_thirty_slots_with_the_values_where_the_capture_says(self):
        d = self.data()
        assert len(d) == 30 and d[0] is None
        assert d[1] == "This is a test of the text-to-speech system." and d[2] == "en-us" and d[3]["orig_name"] == "femalesultry.wav"
        assert (d[19], d[20], d[23], d[26], d[28]) == (4.5, 0.8, 0.800000011920929, -1, False)

    def test_it_is_read_by_slot_so_the_first_decimal_is_not_mistaken_for_the_temperature(self):
        p = vs.parse_generate_data(self.data())
        assert p["text"] == "This is a test of the text-to-speech system." and p["language"] == "en-us" and p["ref"] == "standin/player.wav"
        assert p["temperature"] == 0.800000011920929 and p["top_p"] == 0.8, "not 4.5"
        assert vs._fixed_seed(p["seed"]) is None, "-1 is random"

    def test_the_real_calls_reach_the_engine_with_the_right_numbers(self):
        # (parsed values are what start_line hands the engine)
        for path in captured_calls():
            p = vs.parse_generate_data(json.loads(path.read_text(encoding="utf-8"))["body"]["json"]["data"])
            assert p["temperature"] == pytest.approx(0.8) and p["top_p"] == 0.8

    def test_the_same_request_with_a_ping_in_the_real_layout_is_a_health_check(self):
        d = self.data()
        d[1] = "ping."
        d[3] = {"meta": {"_type": "gradio.FileData"}, "mime_type": "audio/wav", "orig_name": "maleeventoned.wav", "path": "", "url": "http://x:7860/gradio_api/file="}
        assert vs.is_health_check(vs.parse_generate_data(d))

    async def test_the_real_layout_end_to_end_sends_temperature_and_top_p_and_no_seed(self, env):
        data = self.data()
        data[3] = None                                  # (the sample the stand-in stored isn't kept; speak without one)
        r, audio, _ = await call(env.client, data)
        assert audio[:4] == b"RIFF"
        got = engine_requests(env)[-1]
        assert got["temperature"] == pytest.approx(0.8, abs=1e-6) and got["top_p"] == 0.8 and got["seed"] is None
