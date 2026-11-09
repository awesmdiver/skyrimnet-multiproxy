"""SkyrimNet Beta30 audit (2026-11-05): new jobs read well and still route, a voice line in a language the running voice cannot speak is refused in plain words,
and a voice with no clip is mentioned once. Stand-ins only."""
import pytest

import job_names
import routing
import voice_clips as vc
import voice_service as vs
from tests.test_voice_service import env, wav  # noqa: F401

pytestmark = pytest.mark.asyncio


class TestNewJobs:
    async def test_a_new_job_in_skyrimnets_table_gets_its_name_and_description(self):
        text = '"llm.variant_default":"Dialogue","llm.variant_player_option_translation":"Player Option Translation","llm.variant_player_option_translation_desc":"Translates the dialogue menu."'
        got = job_names.parse_text_table(text)
        assert got["player_option_translation"] == {"name": "Player Option Translation", "about": "Translates the dialogue menu."}

    async def test_a_job_nobody_described_shows_its_raw_name_and_a_plain_line_not_an_error(self):
        j = job_names.JobNames()
        info = j.info("bard_play_song")
        assert info["name"] == "bard_play_song" and info["about"] == job_names.NO_DESCRIPTION

    async def test_an_unknown_job_still_routes_to_other_and_other_has_a_label(self):
        table = {"dialogue": {"max_tokens": 100, "temperature": 0.7}, "UniversalTranslator": {"max_tokens": 50, "temperature": 0.2}}
        f = routing.make_features(stream=False, images=0, questions=False, messages=2, max_tokens=999, temperature=0.9)
        r = routing.recognise(f, table)
        assert r.keys == (routing.OTHER,) and r.label == "Other"

    async def test_a_new_job_asked_for_by_name_routes(self):
        assert routing.job_from_name("job/player_option_translation", ("player_option_translation",)) == "player_option_translation"


class TestLanguages:
    async def test_codes(self):
        assert vs.language_code("en-us") == "en" and vs.language_code("zh_CN") == "zh" and vs.language_code("???") is None and vs.language_code(None) is None

    async def test_qwen_and_chatterbox_lists(self):
        assert vs.language_problem("qwen3tts", "ja-jp") is None
        assert "Arabic" not in (vs.language_problem("qwen3tts", "ar") or "") or "can't speak Arabic" in vs.language_problem("qwen3tts", "ar")
        assert "can't speak Japanese" in vs.language_problem("chatterbox", "ja", "Chatterbox voice")
        assert vs.language_problem("chatterbox", "fr") is None
        assert vs.language_problem("higgs", "ja") is None and vs.language_problem("chatterbox", "xx-unknown-thing!") is None

    async def test_the_speech_route_reads_the_language_field(self):
        parsed, _, _ = vs.parse_speech_body({"input": "Bonjour", "language": "fr-FR"})
        assert parsed["language"] == "fr"

    async def test_an_unsupported_language_is_refused_in_plain_words_and_a_supported_one_is_spoken(self, env, monkeypatch):
        real = env.engine.status
        monkeypatch.setattr(env.engine, "status", lambda: {**real(), "model": "chatterbox", "modelName": "Chatterbox voice"})
        r = await env.client.post("/v1/audio/speech", json={"input": "Konnichiwa", "language": "ja"})
        assert r.status_code == 400 and "can't speak Japanese" in r.json()["error"]["message"]
        monkeypatch.setattr(env.engine, "status", real)
        ok = await env.client.post("/v1/audio/speech", json={"input": "Hello there", "language": "ja"})      # Higgs works the language out itself
        assert ok.status_code == 200


class TestUnknownVoice:
    async def test_a_voice_with_no_clip_is_logged_once_not_per_line(self, tmp_path):
        logs = []
        store = vc.ClipStore(lambda: tmp_path / "clips", log=logs.append)
        for _ in range(3):
            assert store.resolve("skeleton_creature", None) is None
        assert len(logs) == 1 and "skeleton_creature" in logs[0]
        assert store.resolve("skeleton_creature", b"abc") == b"abc"          # a clip SkyrimNet does send is still used
