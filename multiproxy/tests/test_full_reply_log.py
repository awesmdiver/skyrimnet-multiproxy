"""Tests for LogFullReplies (2026-09-27) -- prints each successful reply in full when turned on,
nothing extra when off, and never dresses a proxy error message up as a reply."""
import json

import pytest


def _sse(content):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}) + "\n\n"


def _patch(monkeypatch, proxy_module, reply=None, chunks=None):
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


def _reply_records(caplog):
    return [r.getMessage() for r in caplog.records if "REPLY" in r.getMessage()]


def test_off_by_default_logs_no_reply_text(test_client, proxy_module, monkeypatch, caplog):
    _patch(monkeypatch, proxy_module, reply="The full secret dialogue line.")
    with caplog.at_level("INFO", logger="proxy"):
        _post(test_client, proxy_module, False)
    assert not _reply_records(caplog)
    assert "secret dialogue" not in caplog.text


def test_on_prints_a_non_streaming_reply_in_full(test_client, proxy_module, monkeypatch, caplog):
    monkeypatch.setattr(proxy_module, "LOG_FULL_REPLIES", True)
    _patch(monkeypatch, proxy_module, reply="The full dialogue line, all of it.")
    with caplog.at_level("INFO", logger="proxy"):
        _post(test_client, proxy_module, False)
    records = _reply_records(caplog)
    assert len(records) == 1 and "The full dialogue line, all of it." in records[0]


def test_on_prints_a_streamed_reply_assembled_in_full(test_client, proxy_module, monkeypatch, caplog):
    monkeypatch.setattr(proxy_module, "LOG_FULL_REPLIES", True)
    _patch(monkeypatch, proxy_module, chunks=[_sse("Hello, "), _sse("dear "), _sse("traveler."), "data: [DONE]\n\n"])
    with caplog.at_level("INFO", logger="proxy"):
        _post(test_client, proxy_module, True)
    records = _reply_records(caplog)
    assert len(records) == 1 and "Hello, dear traveler." in records[0]


def test_logged_reply_is_the_repaired_one(test_client, proxy_module, monkeypatch, caplog):
    monkeypatch.setattr(proxy_module, "LOG_FULL_REPLIES", True)
    _patch(monkeypatch, proxy_module, reply="<internal thought>Ha</internal thought>")
    with caplog.at_level("INFO", logger="proxy"):
        _post(test_client, proxy_module, False)
    assert "<internal_thought>Ha</internal_thought>" in _reply_records(caplog)[0]


def test_a_stream_that_dies_partway_is_not_logged_as_a_reply(test_client, proxy_module, monkeypatch, caplog):
    monkeypatch.setattr(proxy_module, "LOG_FULL_REPLIES", True)

    async def broken(system_prompt, messages, model, max_tokens, **extra):
        yield _sse("Half a sentence")
        raise RuntimeError("upstream fell over")

    monkeypatch.setattr(proxy_module, "call_glm_streaming", broken)
    with caplog.at_level("INFO", logger="proxy"):
        r = _post(test_client, proxy_module, True)
    assert "Half a sentence" in r.text and r.text.rstrip().endswith("data: [DONE]")
    assert not _reply_records(caplog)


def test_a_failed_request_logs_no_reply(test_client, proxy_module, monkeypatch, caplog):
    from fastapi import HTTPException
    monkeypatch.setattr(proxy_module, "LOG_FULL_REPLIES", True)

    async def direct(*a, **k):
        raise HTTPException(status_code=502, detail="nope")

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    with caplog.at_level("INFO", logger="proxy"):
        assert _post(test_client, proxy_module, False).status_code == 502
    assert not _reply_records(caplog)


def test_reply_log_goes_through_the_redacting_formatter(proxy_module):
    """The log line is one ordinary logger call, so the same redaction that masks credentials in
    every other line applies -- prove it on the formatter itself."""
    fmt = proxy_module._RedactingFormatter("%(message)s")
    import logging
    record = logging.LogRecord("proxy", logging.INFO, __file__, 1, "REPLY leaked sk-ant-api03-abcdefghijklmnopqrstuvwx1234", None, None)
    assert "abcdefghijklmnopqrstuvwx1234" not in fmt.format(record)
