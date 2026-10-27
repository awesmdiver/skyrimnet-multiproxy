"""Tests for the NPC tag fixer -- ported from Raydonn's fork (2026-09-27), split in two for SkyrimNet
Beta 25 the same day:

  * FixNpcTags (ON by default) repairs broken markup like `<internal thought>`.
  * FixHiggsVoiceTags (OFF by default) rewrites voice cues (`[laughs]`, `*sighs*`) into Beta 25's
    Higgs tags (`[sfx-laughter]`, `[sfx-sigh]`). Only for players whose NPCs use the Higgs voice
    engine: Chatterbox NPCs are asked for plain `[sigh]` / `[angry]`, and rewriting those would make
    the voice read the new tag aloud.

Two layers: the sanitizer itself (sanitize_response / sanitize_sse_stream, including a tag split across
two chunks), and the ONE choke point in chat_completions that applies it to every provider, tested end
to end through the real route with a fake provider function so the same test holds for any of them.

sanitize_response returns (fixed_text, repairs) since queue: catch-broken-replies (2026-09-29) -- every
call below indexes [0] for the text; test_repairs_are_returned_alongside_the_text below covers the
second element specifically.
"""
import json
import os

import pytest


def _sse(content=None, finish=None, extra=None):
    """One OpenAI-shaped SSE event, the way every provider here emits them."""
    choice = {"index": 0, "delta": {}, "finish_reason": finish}
    if content is not None:
        choice["delta"]["content"] = content
    obj = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [choice]}
    obj.update(extra or {})
    return f"data: {json.dumps(obj)}\n\n"


async def _agen(events):
    for e in events:
        yield e


async def _run_sanitizer(proxy_module, events):
    return [e async for e in proxy_module.sanitize_sse_stream(_agen(events), "rid", "m")]


def _joined_content(events):
    out = []
    for e in events:
        stripped = e.strip()
        if stripped.startswith("data: ") and stripped != "data: [DONE]":
            for ch in json.loads(stripped[6:]).get("choices", []):
                out.append((ch.get("delta") or {}).get("content") or "")
    return "".join(out)


def _chunked(text, size):
    return [_sse(text[i:i + size]) for i in range(0, len(text), size)] + ["data: [DONE]\n\n"]


@pytest.fixture()
def voice_on(monkeypatch, proxy_module):
    monkeypatch.setattr(proxy_module, "FIX_HIGGS_VOICE_TAGS", True)


# Every kind of voice-looking text: Chatterbox plain tags, Higgs tags, actions, parentheses.
VOICE_LOOKING = (
    "*sighs* Well. [sigh] [angry] [clear throat] (angry) [emotion-anger] [SFX-LAUGHTER] "
    "*laughs softly* [laughs] [whispering] <sad> Done."
)

HIGGS_CATALOG = (
    "emotion-affection emotion-amusement emotion-anger emotion-arousal emotion-awe emotion-bitterness "
    "emotion-confusion emotion-contemplation emotion-contentment emotion-determination emotion-disgust "
    "emotion-elation emotion-enthusiasm emotion-fear emotion-helplessness emotion-longing emotion-pride "
    "emotion-relief emotion-sadness emotion-shame emotion-surprise style-singing style-shouting "
    "style-whispering prosody-speed_very_slow prosody-speed_slow prosody-speed_fast prosody-speed_very_fast "
    "prosody-pitch_low prosody-pitch_high prosody-expressive_high prosody-expressive_low sfx-cough "
    "sfx-laughter sfx-crying sfx-screaming sfx-burping sfx-humming sfx-sigh sfx-sniff sfx-sneeze"
).split()


# --- broken-markup repair (FixNpcTags) ---

def test_repairs_broken_thought_tag(proxy_module):
    text = "<internal thought>She hesitates.</internal thought> Well then."
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == (
        "<internal_thought>She hesitates.</internal_thought> Well then."
    )


def test_plain_text_is_returned_unchanged(proxy_module):
    text = "The Jarl nods. \"Welcome to Whiterun, traveler.\" Nothing to fix here."
    assert proxy_module.sanitize_response(text, "rid", "m")[0] is text


def test_already_correct_markup_is_unchanged(proxy_module):
    text = "<internal_thought>Fine.</internal_thought> Please."
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == text


def test_repairs_are_logged(proxy_module, caplog):
    with caplog.at_level("WARNING", logger="proxy"):
        proxy_module.sanitize_response("<internal thought>x</internal thought>", "abc123", "some-model")
    assert any("abc123" in r.message and "markup repaired" in r.message for r in caplog.records)


def test_repairs_are_returned_alongside_the_text(proxy_module):
    """The list of what changed comes back too, not just logged -- the choke point in
    chat_completions needs it to record "repaired" on the Speeds tab (queue: catch-broken-replies,
    2026-09-29)."""
    fixed, repairs = proxy_module.sanitize_response("<internal thought>x</internal thought>", "rid", "m")
    assert fixed == "<internal_thought>x</internal_thought>"
    assert repairs


def test_plain_text_reports_no_repairs(proxy_module):
    fixed, repairs = proxy_module.sanitize_response("Just a normal line.", "rid", "m")
    assert repairs == []


def test_vocab_seeded_from_config_known_tags(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "_tag_vocab", {})
    monkeypatch.setattr(proxy_module, "_load_config", lambda: {"known_tags": ["scene_note"]})
    proxy_module._seed_tag_vocab()
    assert proxy_module.sanitize_response("<scene note>x</scene note>", "rid", "m")[0] == "<scene_note>x</scene_note>"


# --- voice cues OFF (the default): voice-looking text is left completely alone ---

def test_voice_setting_is_off_by_default(proxy_module):
    assert proxy_module.FIX_HIGGS_VOICE_TAGS is False


def test_voice_looking_text_is_byte_identical_with_the_setting_off(proxy_module):
    assert proxy_module.sanitize_response(VOICE_LOOKING, "rid", "m")[0] == VOICE_LOOKING


def test_chatterbox_style_text_is_untouched_with_the_setting_off(proxy_module):
    text = "[whispering] Keep your voice down - [shush] - they'll hear us. [sigh] [happy] [clear throat] So? [chuckle]"
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == text


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, 64])
async def test_voice_looking_text_streams_through_unchanged_with_the_setting_off(proxy_module, size):
    out = await _run_sanitizer(proxy_module, _chunked(VOICE_LOOKING, size))
    assert _joined_content(out) == VOICE_LOOKING


@pytest.mark.asyncio
async def test_off_setting_does_not_hold_back_a_half_written_action(proxy_module):
    """`*sighs` mid-stream is only ever held back so it can be rewritten -- with rewriting off it
    must come straight through, not sit waiting for a closing star."""
    out = await _run_sanitizer(proxy_module, [_sse("Well *sighs"), _sse(" and goes.")])
    assert _joined_content(out[:1]) == "Well *sighs"


# --- voice cues ON: Beta 25's Higgs catalog ---

def test_on_rewrites_a_loose_sound_cue_to_the_lowercase_higgs_tag(proxy_module, voice_on):
    assert proxy_module.sanitize_response("Ha [laughs] that's good.", "rid", "m")[0] == "Ha [sfx-laughter] that's good."


def test_on_rewrites_a_star_action_to_a_sound_tag(proxy_module, voice_on):
    assert proxy_module.sanitize_response("Well *sighs* fine.", "rid", "m")[0] == "Well [sfx-sigh] fine."


def test_on_output_is_lowercase_whatever_case_came_in(proxy_module, voice_on):
    assert proxy_module.sanitize_response("[EMOTION-ANGER] Get out! [Sfx-Laughter]", "rid", "m")[0] == (
        "[emotion-anger] Get out! [sfx-laughter]"
    )


def test_on_every_catalog_tag_survives_unchanged(proxy_module, voice_on):
    """Including the three names Raydonn's table lacked (amusement, elation, burping) and the whole
    prosody category -- none of them may be rewritten or deleted."""
    text = " ".join(f"[{t}]Word" for t in HIGGS_CATALOG)
    assert len(HIGGS_CATALOG) == 41
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == text


def test_on_stacked_tags_at_a_sentence_start_survive(proxy_module, voice_on):
    text = "[emotion-arousal][prosody-pitch_low]Come closer... [emotion-elation][sfx-laughter]Haha, welcome!"
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == text


def test_on_recognised_category_with_an_unknown_name_is_left_alone_never_deleted(proxy_module, voice_on):
    text = "[emotion-zzz] a [prosody-fast_forward] b [sfx-boom] c [style-mumbling] d [emotion] e"
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == text


def test_on_a_tag_in_the_wrong_category_is_left_alone(proxy_module, voice_on):
    assert proxy_module.sanitize_response("[sfx-anger] hm", "rid", "m")[0] == "[sfx-anger] hm"


def test_on_loose_emotion_words_become_real_emotion_tags(proxy_module, voice_on):
    out, _repairs = proxy_module.sanitize_response("[angry] Out! [happy] Ha. [emotion-sad] Oh.", "rid", "m")
    assert out == "[emotion-anger] Out! [emotion-elation] Ha. [emotion-sadness] Oh."


def test_on_words_that_are_not_voice_cues_are_untouched(proxy_module, voice_on):
    text = "[dramatic] [clear throat] (whatever) [Lydia] <sad_song> [1, 2]"
    assert proxy_module.sanitize_response(text, "rid", "m")[0] == text


def test_on_a_closing_voice_tag_is_dropped(proxy_module, voice_on):
    assert proxy_module.sanitize_response("Ha [/sfx-sigh] ok", "rid", "m")[0] == "Ha ok"


def test_on_tag_repair_and_voice_repair_work_together(proxy_module, voice_on):
    out, _repairs = proxy_module.sanitize_response("<internal thought>Hm [sighs]</internal thought>", "rid", "m")
    assert out == "<internal_thought>Hm [sfx-sigh]</internal_thought>"


@pytest.mark.asyncio
async def test_on_stream_repairs_a_voice_tag_split_across_chunks(proxy_module, voice_on):
    out = await _run_sanitizer(proxy_module, [_sse("Ha [lau"), _sse("ghs] good"), "data: [DONE]\n\n"])
    assert _joined_content(out) == "Ha [sfx-laughter] good"


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 2, 3, 7, 16])
async def test_on_stream_keeps_catalog_tags_intact_however_they_are_split(proxy_module, voice_on, size):
    text = "[emotion-elation][prosody-pitch_low]Come here. [sfx-burping]Urp. [emotion-amusement]Ha."
    out = await _run_sanitizer(proxy_module, _chunked(text, size))
    assert _joined_content(out) == text


@pytest.mark.asyncio
async def test_on_stream_rewrites_an_action_split_across_chunks(proxy_module, voice_on):
    out = await _run_sanitizer(proxy_module, [_sse("Well *si"), _sse("ghs* fine."), "data: [DONE]\n\n"])
    assert _joined_content(out) == "Well [sfx-sigh] fine."


# --- voice cues ON: the vocabulary comes from the player's own Higgs.yaml when it can be read ---

def _link_install(tmp_path, monkeypatch, proxy_module, higgs_yaml):
    config = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
    config.mkdir(parents=True)
    if higgs_yaml is not None:
        (config / "Higgs.yaml").write_text(higgs_yaml, encoding="utf-8")
    monkeypatch.setattr(proxy_module, "skyrim_install_path", str(tmp_path))
    return config / "Higgs.yaml"


def test_vocab_uses_the_players_own_allowed_list(proxy_module, voice_on, tmp_path, monkeypatch):
    _link_install(tmp_path, monkeypatch, proxy_module,
                  "enable_audio_tags: true\nallowed_audio_tags:\n  - sfx-sigh\n  - emotion-anger\n")
    # sigh and anger are allowed; laughter isn't, so "[laughs]" is left exactly as written.
    out, _repairs = proxy_module.sanitize_response("[sighs] [angry] [laughs] [emotion-elation]", "rid", "m")
    assert out == "[sfx-sigh] [emotion-anger] [laughs] [emotion-elation]"


def test_vocab_is_empty_when_the_player_turned_audio_tags_off(proxy_module, voice_on, tmp_path, monkeypatch):
    _link_install(tmp_path, monkeypatch, proxy_module,
                  "enable_audio_tags: false\nallowed_audio_tags:\n  - sfx-sigh\n")
    assert proxy_module.sanitize_response("Well *sighs* [laughs]", "rid", "m")[0] == "Well *sighs* [laughs]"


def test_vocab_falls_back_to_the_beta25_catalog_when_higgs_yaml_is_missing(proxy_module, voice_on, tmp_path, monkeypatch):
    _link_install(tmp_path, monkeypatch, proxy_module, None)
    assert proxy_module.sanitize_response("[laughs] [emotion-elation]", "rid", "m")[0] == "[sfx-laughter] [emotion-elation]"


def test_vocab_falls_back_when_nothing_is_linked(proxy_module, voice_on):
    assert proxy_module.skyrim_install_path == ""
    assert proxy_module.sanitize_response("[laughs]", "rid", "m")[0] == "[sfx-laughter]"


def test_vocab_falls_back_when_higgs_yaml_is_garbage(proxy_module, voice_on, tmp_path, monkeypatch):
    _link_install(tmp_path, monkeypatch, proxy_module, "::: not [valid yaml")
    assert proxy_module.sanitize_response("[laughs]", "rid", "m")[0] == "[sfx-laughter]"


def test_vocab_picks_up_an_edited_higgs_yaml(proxy_module, voice_on, tmp_path, monkeypatch):
    path = _link_install(tmp_path, monkeypatch, proxy_module, "allowed_audio_tags:\n  - sfx-sigh\n")
    assert proxy_module.sanitize_response("[laughs]", "rid", "m")[0] == "[laughs]"
    path.write_text("allowed_audio_tags:\n  - sfx-laughter\n", encoding="utf-8")
    later = os.stat(path).st_mtime + 5
    os.utime(path, (later, later))
    assert proxy_module.sanitize_response("[laughs]", "rid", "m")[0] == "[sfx-laughter]"


def test_the_higgs_file_is_not_read_at_all_when_the_setting_is_off(proxy_module, tmp_path, monkeypatch):
    _link_install(tmp_path, monkeypatch, proxy_module, "allowed_audio_tags:\n  - sfx-sigh\n")
    monkeypatch.setattr(proxy_module, "_read_higgs_yaml", lambda p: pytest.fail("Higgs.yaml read with the setting off"))
    assert proxy_module.sanitize_response("[laughs] <internal thought>x</internal thought>", "rid", "m")[0] == (
        "[laughs] <internal_thought>x</internal_thought>"
    )


def test_the_live_higgs_catalog_matches_beta25s_list(proxy_module):
    """The built-in fallback vocabulary must be exactly Beta 25's 41-tag Higgs catalog."""
    assert sorted(proxy_module._HIGGS_CATALOG) == sorted(HIGGS_CATALOG)


# --- sanitize_sse_stream ---

@pytest.mark.asyncio
async def test_stream_repairs_a_tag_split_across_two_chunks(proxy_module):
    events = [
        _sse("Well <internal "), _sse("thought>hmm</internal thou"), _sse("ght> ok", finish="stop"),
        "data: [DONE]\n\n",
    ]
    out = await _run_sanitizer(proxy_module, events)
    assert _joined_content(out) == "Well <internal_thought>hmm</internal_thought> ok"
    assert out[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_plain_text_passes_through_byte_identical(proxy_module):
    events = [_sse("Hello, "), _sse("traveler. "), _sse("Stay a while.", finish="stop"), "data: [DONE]\n\n"]
    out = await _run_sanitizer(proxy_module, events)
    assert out == events


@pytest.mark.asyncio
async def test_stream_flushes_held_text_before_the_finish_chunk(proxy_module):
    # The reply ends with an unclosed "<" -- held back until the stream says it's over.
    out = await _run_sanitizer(proxy_module, [_sse("a < b"), _sse(None, finish="stop"), "data: [DONE]\n\n"])
    assert _joined_content(out) == "a < b"
    assert json.loads(out[-2].strip()[6:])["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_stream_text_and_finish_in_one_chunk_loses_nothing(proxy_module):
    out = await _run_sanitizer(proxy_module, [_sse("done <", finish="stop"), "data: [DONE]\n\n"])
    assert _joined_content(out) == "done <"
    assert any(json.loads(e.strip()[6:])["choices"][0]["finish_reason"] == "stop" for e in out if e.startswith("data: {"))


@pytest.mark.asyncio
async def test_stream_passes_non_reply_events_through(proxy_module):
    events = ['data: {"error": "GLM API key not configured"}\n\n', ": keep-alive\n\n", "data: [DONE]\n\n"]
    assert await _run_sanitizer(proxy_module, events) == events


# --- the choke point in chat_completions, provider-agnostic ---

def _patch_glm(monkeypatch, proxy_module, reply=None, chunks=None):
    async def direct(system_prompt, messages, model, max_tokens, **extra):
        return reply, {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}

    async def streaming(system_prompt, messages, model, max_tokens, **extra):
        for c in chunks:
            yield c

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    monkeypatch.setattr(proxy_module, "call_glm_streaming", streaming)


def _post(test_client, proxy_module, stream):
    return test_client.post("/v1/chat/completions", json={
        "model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": stream,
        "messages": [{"role": "user", "content": "hi"}],
    })


def test_route_repairs_non_streaming_reply(test_client, proxy_module, monkeypatch):
    _patch_glm(monkeypatch, proxy_module, reply="<internal thought>Hm.</internal thought> Ha [laughs]")
    body = _post(test_client, proxy_module, False).json()
    # voice cues are off by default, so only the tag is repaired
    assert body["choices"][0]["message"]["content"] == "<internal_thought>Hm.</internal_thought> Ha [laughs]"


def test_route_repairs_voice_cues_too_when_that_setting_is_on(test_client, proxy_module, monkeypatch, voice_on):
    _patch_glm(monkeypatch, proxy_module, reply="<internal thought>Hm.</internal thought> Ha [laughs]")
    body = _post(test_client, proxy_module, False).json()
    assert body["choices"][0]["message"]["content"] == "<internal_thought>Hm.</internal_thought> Ha [sfx-laughter]"


def test_route_repairs_streaming_reply_split_across_chunks(test_client, proxy_module, monkeypatch):
    _patch_glm(monkeypatch, proxy_module, chunks=[
        _sse("<internal "), _sse("thought>Hm.</internal thought>", finish="stop"), "data: [DONE]\n\n",
    ])
    resp = _post(test_client, proxy_module, True)
    events = [e + "\n\n" for e in resp.text.split("\n\n") if e]
    assert _joined_content(events) == "<internal_thought>Hm.</internal_thought>"


def test_route_plain_text_unchanged(test_client, proxy_module, monkeypatch):
    _patch_glm(monkeypatch, proxy_module, reply="Just a normal line of dialogue.")
    assert _post(test_client, proxy_module, False).json()["choices"][0]["message"]["content"] == "Just a normal line of dialogue."


def test_route_leaves_voice_looking_text_alone_by_default(test_client, proxy_module, monkeypatch):
    _patch_glm(monkeypatch, proxy_module, reply=VOICE_LOOKING)
    assert _post(test_client, proxy_module, False).json()["choices"][0]["message"]["content"] == VOICE_LOOKING


def test_setting_off_changes_nothing_non_streaming(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "FIX_NPC_TAGS", False)
    _patch_glm(monkeypatch, proxy_module, reply="<internal thought>Hm.</internal thought> Ha [laughs]")
    body = _post(test_client, proxy_module, False).json()
    assert body["choices"][0]["message"]["content"] == "<internal thought>Hm.</internal thought> Ha [laughs]"


def test_setting_off_changes_nothing_streaming(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "FIX_NPC_TAGS", False)
    chunks = [_sse("<internal "), _sse("thought>Hm.", finish="stop"), "data: [DONE]\n\n"]
    _patch_glm(monkeypatch, proxy_module, chunks=chunks)
    assert _post(test_client, proxy_module, True).text == "".join(chunks)
