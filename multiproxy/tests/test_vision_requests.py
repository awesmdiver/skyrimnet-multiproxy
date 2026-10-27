"""Tests for Vision (multimodal) requests (2026-09-20).

Root cause: a real Vision request (SkyrimNet's OmniSight screenshot analysis) sends a standard
OpenAI-shaped multimodal message -- content: [{"type": "text", ...}, {"type": "image_url", ...}]
-- but ChatMessage.content used to be typed as plain `str`, so FastAPI's own request validation
rejected the whole request with a 422 before it ever reached chat_completions() at all.

No real captured SkyrimNet Vision request was available to verify against (checked
prompt-captures/ and multiproxy.log for a real 422 carrying this shape -- neither had one). These
tests are built against the standard OpenAI vision content-parts shape instead, per SkyrimNet's
own docs describing itself as targeting "an OpenAI-compatible endpoint" -- flagged in the handoff
rather than blocked on.
"""
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_response(status=200, text=""):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    if status == 200:
        import json as _json
        resp.json = AsyncMock(return_value=_json.loads(text))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


OK_CHAT_BODY = (
    '{"choices":[{"message":{"content":"I see a dragon"},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":50,"completion_tokens":4}}'
)

VISION_CONTENT = [
    {"type": "text", "text": "What's in this screenshot?"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZmFrZWltYWdlZGF0YQ=="}},
]


class TestVisionRequestIsAccepted:
    def test_multimodal_content_no_longer_422s(self, proxy_module, test_client):
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/qwen/qwen3-vl-235b-a22b-instruct",
                "messages": [{"role": "user", "content": VISION_CONTENT}],
            })
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "I see a dragon"

    def test_multimodal_content_is_forwarded_to_the_provider_intact(self, proxy_module, test_client):
        """The whole point: the image_url part must reach OpenRouter unchanged, not get
        stringified or dropped."""
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/qwen/qwen3-vl-235b-a22b-instruct",
                "messages": [{"role": "user", "content": VISION_CONTENT}],
            })
        assert r.status_code == 200
        sent_payload = session.post.call_args.kwargs["json"]
        sent_messages = sent_payload["messages"]
        user_msg = next(m for m in sent_messages if m["role"] == "user")
        assert user_msg["content"] == VISION_CONTENT

    def test_plain_string_content_still_works_unchanged(self, proxy_module, test_client):
        """Regression guard: the overwhelmingly common case (normal text dialogue) must not
        change shape or behavior now that content also accepts a list."""
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some-model",
                "messages": [{"role": "user", "content": "hello there"}],
            })
        assert r.status_code == 200
        sent_payload = session.post.call_args.kwargs["json"]
        user_msg = next(m for m in sent_payload["messages"] if m["role"] == "user")
        assert user_msg["content"] == "hello there"


class TestMergeConsecutiveSameRoleContent:
    def test_string_plus_string_merges_exactly_as_before(self, proxy_module):
        merged = proxy_module._merge_message_content("first", "second")
        assert merged == "first\n\nsecond"

    def test_list_plus_string_normalizes_and_concatenates(self, proxy_module):
        merged = proxy_module._merge_message_content(VISION_CONTENT, "one more thing")
        assert merged == VISION_CONTENT + [{"type": "text", "text": "one more thing"}]

    def test_string_plus_list_normalizes_and_concatenates(self, proxy_module):
        merged = proxy_module._merge_message_content("earlier note", VISION_CONTENT)
        assert merged == [{"type": "text", "text": "earlier note"}] + VISION_CONTENT

    def test_list_plus_list_concatenates_both_lists(self, proxy_module):
        other = [{"type": "text", "text": "second image coming up"}]
        merged = proxy_module._merge_message_content(VISION_CONTENT, other)
        assert merged == VISION_CONTENT + other

    def test_two_consecutive_user_messages_one_with_vision_merge_without_crashing(self, proxy_module, test_client):
        """End-to-end: SkyrimNet could plausibly send a vision message immediately followed by
        another user message (no assistant turn in between) -- the merge step must not crash."""
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/qwen/qwen3-vl-235b-a22b-instruct",
                "messages": [
                    {"role": "user", "content": VISION_CONTENT},
                    {"role": "user", "content": "also, is it hostile?"},
                ],
            })
        assert r.status_code == 200
        sent_payload = session.post.call_args.kwargs["json"]
        user_msgs = [m for m in sent_payload["messages"] if m["role"] == "user"]
        assert len(user_msgs) == 1  # merged into one
        assert user_msgs[0]["content"] == VISION_CONTENT + [{"type": "text", "text": "also, is it hostile?"}]


class TestContentAsTextHelper:
    def test_plain_string_passes_through(self, proxy_module):
        assert proxy_module._content_as_text("hi there") == "hi there"

    def test_multimodal_list_extracts_only_the_text_parts(self, proxy_module):
        assert proxy_module._content_as_text(VISION_CONTENT) == "What's in this screenshot?"

    def test_none_becomes_empty_string(self, proxy_module):
        assert proxy_module._content_as_text(None) == ""


class TestSystemPromptStaysAStringEvenIfSomehowMultimodal(object):
    def test_multimodal_system_message_is_flattened_to_text(self, proxy_module, test_client):
        """A multimodal system message is not realistic in practice, but system_prompt must
        never silently become a list -- everything downstream (the bare-Claude body template,
        the token-estimate fallback) assumes it's a string or None."""
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some-model",
                "messages": [
                    {"role": "system", "content": [{"type": "text", "text": "be concise"}]},
                    {"role": "user", "content": "hi"},
                ],
            })
        assert r.status_code == 200
        sent_payload = session.post.call_args.kwargs["json"]
        system_msg = next(m for m in sent_payload["messages"] if m["role"] == "system")
        assert system_msg["content"] == "be concise"
