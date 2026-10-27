"""Tests for logging the actual request + response on any error (2026-09-20), reported live: a
run of real 422s left nothing but uvicorn's own bare "422 Unprocessable Content" access line, no
way to tell what SkyrimNet actually sent or why it was rejected.

Covers: a real FastAPI validation error (422) logs both the request body and the validation
detail; a successful (200) request logs nothing; a credential-shaped substring in the logged
body comes out redacted; an oversized body gets truncated, not dumped in full.
"""
import logging
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
    '{"choices":[{"message":{"content":"hi there"},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":5,"completion_tokens":3}}'
)


def _error_log_records(caplog):
    return [r for r in caplog.records if r.name == "proxy" and r.message.startswith("[error]")]


class TestValidationErrorLogging:
    def test_a_real_422_logs_request_body_and_validation_detail(self, proxy_module, test_client, caplog):
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "MARKER_XYZ_CONTENT"}],
                "max_tokens": "not-an-int",
            })
        assert r.status_code == 422

        errors = _error_log_records(caplog)
        assert len(errors) == 1
        msg = errors[0].message
        assert "422" in msg
        assert "/v1/chat/completions" in msg
        assert "MARKER_XYZ_CONTENT" in msg  # the actual request body, not just a status line
        assert "max_tokens" in msg  # FastAPI's own validation error detail names the bad field

    def test_our_own_httpexception_error_is_also_logged(self, proxy_module, test_client, caplog):
        """Not just FastAPI's built-in validation errors -- our own HTTPException ->
        _openai_style_error_handler path too, proving this is a general mechanism, not
        special-cased to one error shape."""
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/provider/model",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 500  # Kilo key not configured in tests
        errors = _error_log_records(caplog)
        assert len(errors) == 1
        assert "500" in errors[0].message
        assert "No Kilo Gateway key found. Add one on the Providers tab." in errors[0].message


class TestSuccessfulRequestsAreNotLogged:
    def test_a_real_200_produces_no_error_log_line(self, proxy_module, test_client, caplog):
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with caplog.at_level(logging.WARNING, logger="proxy"), \
             patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200
        assert _error_log_records(caplog) == []

    def test_a_successful_streaming_request_produces_no_error_log_line(self, proxy_module, test_client, caplog):
        sse = b'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n\ndata: [DONE]\n\n'
        mock_content = MagicMock()

        async def _iter():
            yield sse

        mock_content.iter_any.return_value = _iter()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.content = mock_content
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        session = _make_mock_session(mock_resp)

        with caplog.at_level(logging.WARNING, logger="proxy"), \
             patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/streamed-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
        assert r.status_code == 200
        assert _error_log_records(caplog) == []


class TestRedaction:
    def test_credential_shaped_content_in_the_request_body_is_redacted(self, proxy_module, test_client, caplog):
        fake_key = "sk-or-v1-abcdefghij1234567890abcdefghij1234567890"
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": f"my key is {fake_key}"}],
                "max_tokens": "not-an-int",  # force a 422 so this actually gets logged
            })
        assert r.status_code == 422
        errors = _error_log_records(caplog)
        assert len(errors) == 1
        assert fake_key not in errors[0].message
        assert "***REDACTED***" in errors[0].message

    def test_redact_text_helper_matches_the_glm_zai_key_shape_too(self, proxy_module):
        glm_key = "0123456789abcdef0123456789abcdef.FakeFakeFakeFake"
        redacted = proxy_module._redact_text(f"key: {glm_key}")
        assert glm_key not in redacted
        assert "***REDACTED***" in redacted


class TestTruncation:
    def test_an_oversized_request_body_is_truncated_not_dumped_in_full(self, proxy_module, test_client, caplog):
        huge_content = "X" * 10000
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": huge_content}],
                "max_tokens": "not-an-int",  # force a 422 so this actually gets logged
            })
        assert r.status_code == 422
        errors = _error_log_records(caplog)
        assert len(errors) == 1
        msg = errors[0].message
        assert huge_content not in msg  # the full 10,000-char string must never appear whole
        assert "truncated" in msg
        assert "bytes total" in msg

    def test_format_logged_body_helper_directly(self, proxy_module):
        small = proxy_module._format_logged_body(b'{"a": 1}')
        assert small == '{"a": 1}'

        big_raw = b"Y" * 5000
        big = proxy_module._format_logged_body(big_raw)
        assert len(big) < 5000
        assert "truncated, 5000 bytes total" in big

        assert proxy_module._format_logged_body(b"") == "(empty)"


class TestVoiceRequestsNeverLogTheirContent:
    """2026-10-06: a refused voice enrolment wrote the whole body (the sound's bytes and the spoken words) into the log. Voice requests log field NAMES and sizes only."""

    def test_a_refused_enrolment_and_a_refused_line_log_names_and_sizes_not_content(self, proxy_module, test_client, caplog):
        boundary = "----b"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nactor:1\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"ref_audio\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\nRIFFWAVEnotreallyaudio\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"ref_text\"\r\n\r\nSECRET_SPOKEN_WORDS_XYZ\r\n--{boundary}--\r\n").encode()
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/v1/audio/some-unknown-route", content=body, headers={"content-type": f"multipart/form-data; boundary={boundary}"})
            test_client.post("/v1/audio/speech", json={"input": "SECRET_LINE_TEXT_XYZ", "voice": "x"})
        assert r.status_code == 404
        logged = "\n".join(rec.getMessage() for rec in _error_log_records(caplog))
        assert "SECRET_SPOKEN_WORDS_XYZ" not in logged and "RIFFWAVE" not in logged and "SECRET_LINE_TEXT_XYZ" not in logged
        assert "voice request, no sound or words written" in logged and "title(field" in logged and "ref_audio(file" in logged


class TestBinaryRequestsLogOnlyTheirTypeAndSize:
    """2026-10-24: SkyrimNet's External Whisper test posted a voice clip to POST / (404) and the start of the recording landed in the log. A body that is not text is
    logged as its type and size; a multipart one lists each part's name, type and size."""

    def _audio_body(self):
        boundary = "----b"
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00SOUNDBYTES_MARKER\x00\x01\x02"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + wav + \
               (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\nSECRET_WORDS_XYZ\r\n--{boundary}--\r\n").encode()
        return body, f"multipart/form-data; boundary={boundary}", wav

    def test_a_failed_audio_upload_logs_type_and_size_and_no_sound(self, proxy_module, test_client, caplog):
        body, ctype, wav = self._audio_body()
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/some/unknown/route", content=body, headers={"content-type": ctype})
        assert r.status_code == 404
        logged = "\n".join(rec.getMessage() for rec in _error_log_records(caplog))
        assert "request body: multipart/form-data, " in logged and "(not shown)" in logged
        assert f"file: audio/wav, {len(wav):,} bytes" in logged and "model: whisper-1" in logged
        assert "SOUNDBYTES_MARKER" not in logged and "RIFF" not in logged and "SECRET_WORDS_XYZ" not in logged and "\x00" not in logged

    def test_an_octet_stream_body_is_type_and_size_only(self, proxy_module, test_client, caplog):
        with caplog.at_level(logging.WARNING, logger="proxy"):
            test_client.post("/some/unknown/route", content=b"RIFF....WAVEdata" * 10, headers={"content-type": "application/octet-stream"})
        logged = "\n".join(rec.getMessage() for rec in _error_log_records(caplog))
        assert "request body: application/octet-stream, 160 bytes (not shown)" in logged and "RIFF" not in logged

    def test_binary_bytes_with_no_content_type_are_still_not_printed(self, proxy_module):
        out = proxy_module._format_logged_body(b"RIFF\x00\x01\x02\xff\xfe" + b"\x80" * 100)
        assert out == "(binary, 109 bytes, not shown)"
        assert proxy_module._format_logged_body("caf\u00e9 ok".encode()) == "caf\u00e9 ok"

    def test_a_json_request_is_logged_as_before(self, proxy_module, test_client, caplog):
        with caplog.at_level(logging.WARNING, logger="proxy"):
            test_client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "MARKER_JSON"}], "max_tokens": "x"})
        assert "MARKER_JSON" in "\n".join(rec.getMessage() for rec in _error_log_records(caplog))

    def test_a_rejected_binary_capture_is_not_written_verbatim(self, proxy_module, tmp_path, monkeypatch):
        monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", True)
        monkeypatch.setattr(proxy_module, "CAPTURE_DIR", str(tmp_path))
        proxy_module.capture_rejected_request_if_enabled(b"RIFF\x00\x00WAVE\xff\xfe\x00")
        text = next(tmp_path.glob("rejected_*.json")).read_text(encoding="utf-8")
        assert "RIFF" not in text and "not shown" in text

    def test_the_bare_address_with_an_audio_file_is_answered_like_the_transcriptions_route(self, proxy_module, test_client, caplog):
        body, ctype, _ = self._audio_body()
        r = test_client.post("/", content=body, headers={"content-type": ctype})
        assert r.status_code == 501 and r.json()["error"]["type"] == "not_implemented"
        with caplog.at_level(logging.WARNING, logger="proxy"):
            r = test_client.post("/", json={"hello": "world"})
        assert r.status_code == 404
