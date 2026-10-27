"""Tests for the OpenAI-style error shape (2026-09-13). Real bug, live-tested by the director: an
Ollama "model not found" error came back as a doubly-JSON-encoded string blob
(`{"detail": "{\\"error\\":{\\"message\\":...}}\\n"}`) because the upstream error body was passed
straight through as FastAPI's `detail`. Every error this proxy returns now comes back as
`{"error": {"message": ..., "type": "api_error", "code": null}}`, with `message` pulled out of the
upstream JSON when it has one, or the raw text when it doesn't.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestExtractUpstreamErrorMessage:
    def test_pulls_message_from_nested_error_object(self, proxy_module):
        raw = '{"error":{"message":"model \'llama3.1:8b\' not found","type":"api_error","code":null}}\n'
        assert proxy_module._extract_upstream_error_message(raw) == "model 'llama3.1:8b' not found"

    def test_pulls_message_from_string_error_field(self, proxy_module):
        raw = '{"error": "OpenRouter API key not configured"}'
        assert proxy_module._extract_upstream_error_message(raw) == "OpenRouter API key not configured"

    def test_pulls_top_level_message_when_no_error_key(self, proxy_module):
        raw = '{"message": "rate limited"}'
        assert proxy_module._extract_upstream_error_message(raw) == "rate limited"

    def test_falls_back_to_raw_text_when_not_json(self, proxy_module):
        raw = "upstream is on fire"
        assert proxy_module._extract_upstream_error_message(raw) == "upstream is on fire"

    def test_falls_back_to_raw_text_when_json_has_no_recognizable_shape(self, proxy_module):
        raw = '{"unrelated": "field"}'
        assert proxy_module._extract_upstream_error_message(raw) == raw

    def test_empty_body_uses_fallback(self, proxy_module):
        assert proxy_module._extract_upstream_error_message("") == "Unknown error"
        assert proxy_module._extract_upstream_error_message("   ") == "Unknown error"
        assert proxy_module._extract_upstream_error_message("", fallback="custom") == "custom"


def _make_mock_response(status=200, text=""):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


class TestNonStreamingErrorShape:
    """End-to-end via chat_completions()'s real HTTPException -> app-level exception handler path,
    not just the extraction helper in isolation."""

    def test_upstream_json_error_becomes_clean_error_object(self, proxy_module, test_client):
        error_body = '{"error":{"message":"model \'llama3.1:8b\' not found","type":"api_error","code":null}}\n'
        resp = _make_mock_response(404, error_body)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-sonnet-5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 404
        body = r.json()
        assert "detail" not in body
        assert body["error"]["message"] == "Kilo Gateway: model 'llama3.1:8b' not found"
        assert body["error"]["type"] == "api_error"
        assert body["error"]["code"] is None

    def test_upstream_non_json_error_uses_raw_text(self, proxy_module, test_client):
        resp = _make_mock_response(502, "upstream gateway is down")
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-sonnet-5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 502
        assert r.json()["error"]["message"] == "Kilo Gateway: upstream gateway is down"

    def test_key_not_configured_error_also_uses_the_shared_shape(self, proxy_module, test_client):
        r = test_client.post("/v1/chat/completions", json={
            "model": "kilo/provider/model",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 500
        assert r.json()["error"]["message"] == "No Kilo Gateway key found. Add one on the Providers tab."


class TestStreamingErrorShape:
    def test_streaming_error_chunk_carries_the_clean_message_then_done(self, proxy_module, test_client):
        error_body = '{"error":{"message":"model \'llama3.1:8b\' not found"}}\n'
        resp = _make_mock_response(404, error_body)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-sonnet-5",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
        # 2026-09-27 (SkyrimNet Beta 25): a failure before any text is a real HTTP error with the
        # upstream's own status, not a "200" stream carrying error text -- SkyrimNet's smart routing
        # can only route around a request it can tell failed.
        assert r.status_code == 404
        assert r.json()["error"]["message"] == "Kilo Gateway: model 'llama3.1:8b' not found"
        assert "[Kilo Gateway" not in r.text
        assert "data:" not in r.text
