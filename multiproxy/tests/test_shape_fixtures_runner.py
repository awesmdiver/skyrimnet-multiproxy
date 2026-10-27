"""Tests for tests/manual/run_shape_fixtures.py's own scoring logic -- the harness itself, not the
proxy. Real bug, 2026-09-16: the harness scored a row `ok` whenever the stream ended with a clean
`[DONE]`, even when zero characters came back. Seen for real during verification: a misrouted run
produced `ok`, `clean_end YES`, `chars 0`. To a player, an NPC that says nothing is exactly as
broken as one cut off mid-sentence -- this must fail, with its own distinct message.

The runner module itself is never collected by pytest (its own filename doesn't match
test_*.py/*_test.py), but its scoring functions are plain, importable functions -- this file imports
and drives them directly against the real proxy_module fixture, same as any other test here. Fully
offline: the runner's own local stub provider (patched Ollama) is reused, no network/subprocess.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "manual"))
import run_shape_fixtures as runner  # noqa: E402
from run_shape_fixtures import (  # noqa: E402
    _run_direct, _run_streaming, _stub_direct, _stub_streaming, build_client,
)
from tests.fixtures.skyrimnet_shapes import FIXTURES  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_stub_flags():
    runner.STUB_BREAK_STREAM = False
    runner.STUB_EMPTY_REPLY = False
    yield
    runner.STUB_BREAK_STREAM = False
    runner.STUB_EMPTY_REPLY = False


@pytest.fixture()
def stub_client(proxy_module):
    monkeypatch_targets = (proxy_module.call_ollama_direct, proxy_module.call_ollama_streaming)
    proxy_module.call_ollama_direct = _stub_direct
    proxy_module.call_ollama_streaming = _stub_streaming
    try:
        yield build_client()
    finally:
        proxy_module.call_ollama_direct, proxy_module.call_ollama_streaming = monkeypatch_targets


class TestDirectScoring:
    def test_normal_reply_passes(self, stub_client):
        result = _run_direct(stub_client, FIXTURES[0], "ollama/stub-test")
        assert result.ok is True
        assert result.chars_returned > 0
        assert result.error == ""

    def test_empty_reply_fails(self, stub_client):
        """Real finding while writing this test: chat_completions() itself already guards against
        an empty non-streaming reply for every provider (`if not response: raise HTTPException(500,
        "Empty response")`), so this surfaces as an HTTP 500 the harness's existing status-code
        check already catches -- not the harness's own dedicated zero-chars message, which stays as
        defense-in-depth for a response shape that reaches this function with HTTP 200 and empty
        content some other way. Either path must fail; this only asserts the outcome, not which
        message produced it."""
        runner.STUB_EMPTY_REPLY = True
        result = _run_direct(stub_client, FIXTURES[0], "ollama/stub-test")
        assert result.ok is False
        assert result.chars_returned == 0
        assert result.error


class TestStreamingScoring:
    def test_normal_reply_passes(self, stub_client):
        result = _run_streaming(stub_client, FIXTURES[0], "ollama/stub-test")
        assert result.ok is True
        assert result.clean_end is True
        assert result.chars_returned > 0
        assert result.error == ""

    def test_truncated_stream_fails(self, stub_client):
        runner.STUB_BREAK_STREAM = True
        result = _run_streaming(stub_client, FIXTURES[0], "ollama/stub-test")
        assert result.ok is False
        assert result.clean_end is False
        assert "truncated" in result.error

    def test_empty_reply_fails_with_its_own_message_even_though_stream_ended_cleanly(self, stub_client):
        """The exact real bug: a clean [DONE] with zero characters is silence dressed up as success, and needs its own
        distinct failure so it isn't confused with the truncation case above. Since 2026-10-07 (ceefe7e, "does not leave
        NPCs silent") the program itself refuses an empty reply with a 502 ("It answered with nothing.") before any
        stream starts, so the runner sees an HTTP error, never a clean empty stream."""
        runner.STUB_EMPTY_REPLY = True
        result = _run_streaming(stub_client, FIXTURES[0], "ollama/stub-test")
        assert result.ok is False, "an empty reply must fail overall"
        assert result.chars_returned == 0
        assert "HTTP 502" in result.error
        assert "truncated" not in result.error
