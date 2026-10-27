"""SkyrimNet Beta 26 rc2 (2026-10-02): "delivery tags now repeat on every later sentence of the same reply (each sentence is sent to the voice engine on its own).
Sounds such as laughs, coughs and sighs are left out, so they play once." A tag at the start of every sentence must pass, a sound must play once, and the
runaway-length estimate must not count repeated tags as words."""
import json

import pytest

import voice_service as vs
from tests.test_voice_service import env, speak, engine_requests, wav  # noqa: F401  (a running fake engine and SkyrimNet's round trip)

SENTENCES = ["<|style:whispering|>Come closer, traveller.", "<|style:whispering|>I have something to tell you.", "<|style:whispering|>Not a word to the guards."]


@pytest.mark.parametrize("sentence", SENTENCES)
def test_each_sentence_of_a_whispered_reply_passes_with_its_own_tag(sentence):
    text, dropped = vs.convert_tags(sentence)
    assert text == sentence and dropped == []


def test_a_whole_reply_with_the_tag_repeated_on_every_sentence_passes_untouched():
    reply = " ".join(SENTENCES)
    text, dropped = vs.convert_tags(reply)
    assert text == reply and dropped == []
    stacked = "<|style:whispering|><|emotion:fear|>Run. <|style:whispering|><|emotion:fear|>Now."
    assert vs.convert_tags(stacked) == (stacked, [])


def test_the_bracket_spelling_repeated_on_every_sentence_is_converted_each_time():
    text, dropped = vs.convert_tags("[style-whispering]Come closer. [style-whispering]Closer still. [emotion-anger][style-shouting]Get out!")
    assert text == "<|style:whispering|>Come closer. <|style:whispering|>Closer still. <|emotion:anger|><|style:shouting|>Get out!" and dropped == []


def test_a_sound_that_plays_once_gets_its_written_sound_once_and_the_later_sentences_have_none():
    first = vs.convert_tags("<|style:whispering|><|sfx:sigh|> It is late.")[0]
    later = vs.convert_tags("<|style:whispering|>The road is long.")[0]
    assert first == "<|style:whispering|><|sfx:sigh|>Uh It is late." and "sfx" not in later and later == "<|style:whispering|>The road is long."


def test_repeated_tags_are_not_words_so_the_runaway_cap_does_not_move():
    plain = "Come closer, traveller. I have something to tell you."
    tagged = "<|style:whispering|>Come closer, traveller. <|style:whispering|>I have something to tell you."
    many = "".join("<|style:whispering|><|emotion:fear|>" for _ in range(8)) + plain
    assert vs.expected_seconds(tagged) == vs.expected_seconds(plain) == vs.expected_seconds(many)
    assert vs.cap_seconds(tagged) == vs.cap_seconds(plain) == vs.cap_seconds(many)


def test_only_the_models_tags_are_ever_counted_as_non_words_and_a_pause_still_costs_its_time():
    assert vs.expected_seconds("<|style:whispering|>One two three.") == pytest.approx(3 * vs.SECONDS_PER_WORD)
    assert vs.expected_seconds("<|style:whispering|>One<|prosody:pause|> two.") == pytest.approx(2 * vs.SECONDS_PER_WORD + 0.7)
    assert vs.expected_seconds("<|style:whispering|><|sfx:laughter|>Haha.") == pytest.approx(vs.SECONDS_PER_WORD + 0.6)


@pytest.mark.asyncio
async def test_a_multi_sentence_reply_sent_sentence_by_sentence_reaches_the_engine_with_every_tag(env):
    for s in SENTENCES:
        audio, sse = await speak(env.client, "", s, wav())
        assert audio and audio[:4] == b"RIFF", sse
    audio, sse = await speak(env.client, "", "<|style:whispering|><|sfx:laughter|>Heh. It is nothing.", wav())
    assert audio
    sent = [r["input"] for r in engine_requests(env)]
    assert sent[-4:-1] == SENTENCES
    assert sent[-1] == "<|style:whispering|><|sfx:laughter|>Heh. It is nothing.", "a written sound already follows the tag, so none is added"
    assert sum(x.count("<|sfx:") for x in sent) == 1, "the sound played once"
    assert env.service.status()["runawayLinesCut"] == 0
