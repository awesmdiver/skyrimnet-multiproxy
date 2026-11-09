"""Pictures on the Claude subscription lane, and the Vision test that checks the picture was seen (2026-11-08, public issue #9).

Fake data only: a made-up data URL, a stand-in chat function. No real Claude call, no real sign-in.
"""
import base64
import json
import struct

import pytest

import claude_courier as cc
import vision_test

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake picture bytes").decode()
MESSAGE = [{"role": "user", "content": [{"type": "text", "text": "Describe what you see in this image."}, {"type": "image_url", "image_url": {"url": PNG}}]}]


# --- the lane: a picture goes to Claude as a picture ----------------------------------------------------------------------------------------------------

def test_a_picture_reaches_claude_as_an_image_block_in_order():
    out = cc.anthropic_messages(MESSAGE)
    blocks = out[0]["content"]
    assert [b["type"] for b in blocks] == ["text", "image"]
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": PNG.split(",", 1)[1]}


def test_text_only_requests_are_unchanged():
    out = cc.anthropic_messages([{"role": "user", "content": "Hello"}, {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}])
    assert out == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}, {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}]
    assert cc.check_pictures(MESSAGE) == 1 and cc.check_pictures([{"role": "user", "content": "Hello"}]) == 0


@pytest.mark.parametrize("url", [
    pytest.param("https://example.com/cat.png", id="web-address"),                                  # (Claude is never sent an address to fetch)
    pytest.param("data:image/bmp;base64,AAAA", id="type-claude-does-not-take"),
    pytest.param("data:image/png;base64,@@@not base64@@@", id="not-base64"),
    pytest.param("data:image/png;base64," + base64.b64encode(b"x" * (cc.MAX_PICTURE_BYTES + 10)).decode(), id="over-5-MB"),
])
def test_a_picture_claude_cannot_be_given_is_refused_not_dropped(url):
    messages = [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": url}}]}]
    with pytest.raises(cc.PictureNotPassed):
        cc.check_pictures(messages)
    with pytest.raises(cc.PictureNotPassed):
        cc.anthropic_messages(messages)


def test_too_many_pictures_are_refused():
    part = {"type": "image_url", "image_url": {"url": PNG}}
    with pytest.raises(cc.PictureNotPassed):
        cc.check_pictures([{"role": "user", "content": [part] * (cc.MAX_PICTURES + 1)}])


def test_the_refusal_sentence_is_exact():
    assert cc.LINE_NO_PICTURES == "The Claude subscription can't look at pictures. Pick another AI for Vision on the Routing page."


@pytest.mark.asyncio
async def test_the_courier_lane_refuses_a_picture_it_cannot_pass_and_says_so(tmp_path):
    import logging
    host = cc.ClaudeHost(tmp_path / "claude", logging.getLogger("t"))
    bad = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]
    with pytest.raises(cc.ClaudeError) as e:
        await host.chat(None, {"model": "claude-haiku-5-5", "messages": bad})
    assert e.value.status == 400 and e.value.message == cc.LINE_NO_PICTURES


def test_the_main_device_refuses_it_in_plain_words(test_client):
    r = test_client.post("/v1/chat/completions", json={"model": "claude-haiku-5-5", "max_tokens": 20, "messages": [
        {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]})
    assert r.status_code == 400 and cc.LINE_NO_PICTURES in r.text


# --- the picture the Vision test sends -------------------------------------------------------------------------------------------------------------------

def test_the_test_picture_is_a_real_red_png_with_white_letters():
    png = vision_test.test_picture_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (160, 160)
    content = vision_test.message_content()
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert cc.picture_block(content[1])["source"]["media_type"] == "image/png"          # Claude's lane takes it as it is


@pytest.mark.parametrize("answer,seen", [
    ("I don't see an image in your message. It may not have uploaded successfully.", False),
    ("Sorry, I can't view images.", False),
    ("A solid red square with the word STAR in white letters.", True),
    ("It is a red picture.", True),
    ("The word STAR.", True),
    ("", False),
])
def test_the_answer_counts_only_when_it_names_the_colour_or_the_word(answer, seen):
    assert vision_test.saw_picture(answer) is seen


# --- the Routing page's Test for a Vision job ----------------------------------------------------------------------------------------------------------

def _run_test(test_client, proxy_module, monkeypatch, reply, job="vision"):
    sent = []

    async def fake_impl(req, request, rid):
        sent.append(req)
        return {"choices": [{"message": {"content": reply}}]}
    monkeypatch.setattr(proxy_module, "_chat_completions_impl", fake_impl)
    assert test_client.post("/config/routing", json={"routes": {job: {"ais": ["ollama/stand-in"], "how": "order"}}}).status_code == 200
    r = test_client.post("/config/routing/test", json={"job": job})
    assert r.status_code == 200
    return r.json(), sent


def test_the_vision_test_fails_when_the_ai_says_it_sees_no_picture(test_client, proxy_module, monkeypatch):
    out, sent = _run_test(test_client, proxy_module, monkeypatch, "I don't see an image in your message. It may not have uploaded successfully...")
    assert out["ok"] is False and out["line"].startswith("The AI did not see the picture.")
    parts = sent[0].messages[0].content
    assert parts[1]["type"] == "image_url"                                  # a picture really went with the question


def test_the_vision_test_passes_when_the_ai_names_the_colour(test_client, proxy_module, monkeypatch):
    out, _ = _run_test(test_client, proxy_module, monkeypatch, "A red square with the word STAR.")
    assert out["ok"] is True and "did not see" not in out["line"]


def test_other_jobs_are_still_tested_with_text(test_client, proxy_module, monkeypatch):
    out, sent = _run_test(test_client, proxy_module, monkeypatch, "Hello!", job="dialogue")
    assert out["ok"] is True and sent[0].messages[0].content == proxy_module.ROUTE_TEST_MESSAGE
