"""Speech-to-text, step one (2026-10-06): POST /v1/audio/transcriptions records the shape of a request and answers 501. Stand-ins only."""
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import local_guard
import stt_capture as stt
from tests.test_voice_service import env, wav  # noqa: F401  (the fixture and the helper)

pytestmark = pytest.mark.asyncio

SECRET_KEY = "sk-this-key-must-never-be-logged"


def form(parts, boundary="----sttboundary"):
    """parts: [(name, value, filename|None, content-type|None)] -> (body, content-type)."""
    body = b""
    for name, value, fn, ctype in parts:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"' + (f'; filename="{fn}"' if fn else "") + "\r\n"
        if ctype:
            head += f"Content-Type: {ctype}\r\n"
        body += head.encode() + b"\r\n" + (value if isinstance(value, bytes) else str(value).encode()) + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


@pytest_asyncio.fixture
async def rig():
    logs = []
    app = FastAPI()
    app.include_router(stt.build_router(logs.append))
    app.include_router(stt.build_router(logs.append), prefix="/voice")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000")
    yield client, logs
    await client.aclose()


async def test_a_wav_request_is_answered_501_with_the_plain_openai_style_error(rig, monkeypatch):
    monkeypatch.delenv(stt.FIELDS_SWITCH, raising=False)
    client, logs = rig
    body, ctype = form([("file", wav(16000, 1, 2, seconds=2.5), "speech.wav", "audio/wav"), ("model", "whisper-1", None, None)])
    r = await client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})
    assert r.status_code == 501
    assert r.json() == {"error": {"message": "Speech to text is off. Turn it on in MultiProxy on the Voice tab.", "type": "not_implemented"}}
    assert logs == ["A speech-to-text request arrived (POST /v1/audio/transcriptions); speech to text is off, so it was answered with 501."]


async def test_it_is_answered_under_voice_too(rig, env):
    client, _ = rig
    assert (await client.post("/voice/v1/audio/transcriptions", content=b"x")).status_code == 501
    r = await env.client.post("/v1/audio/transcriptions", content=b"x")        # (mounted with the voice routes in the real app)
    assert r.status_code == 501 and any(m.startswith("A speech-to-text request arrived") for m in env.logs)


async def test_with_the_switch_on_the_shape_is_logged_and_never_the_sound_or_the_key(rig, monkeypatch):
    monkeypatch.setenv(stt.FIELDS_SWITCH, "1")
    client, logs = rig
    body, ctype = form([("file", wav(16000, 1, 2, seconds=2.5), "speech.WAV", "audio/wav"), ("model", "whisper-1", None, None), ("language", "en", None, None),
                        ("temperature", "0.2", None, None), ("response_format", "json", None, None), ("prompt", "a private thing the player said", None, None),
                        ("odd_field", "secret value", None, None)])
    await client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype, "Authorization": f"Bearer {SECRET_KEY}", "X-Surprise": "1"})
    assert len(logs) == 2
    line = logs[1]
    assert line.startswith("Speech-to-text request POST /v1/audio/transcriptions: content type multipart/form-data; key sent yes;")
    assert "boundary" not in line and "x-surprise" in line
    assert "file(file, extension .wav, audio/wav," in line and "model(text = whisper-1)" in line and "language(text = en)" in line and "temperature(number = 0.2)" in line
    assert "prompt (text, 31 characters)" in line and "odd_field(text, 12 bytes)" in line
    assert "audio: wav; wav, 16000 Hz, 1 channel, 16-bit, 2.50 s" in line
    text = "\n".join(logs)
    assert SECRET_KEY not in text and "a private thing" not in text and "secret value" not in text and "RIFF" not in text


async def test_a_request_with_no_key_says_no(rig, monkeypatch):
    monkeypatch.setenv(stt.FIELDS_SWITCH, "1")
    client, logs = rig
    body, ctype = form([("file", wav(), "a.wav", "audio/wav")])
    await client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})
    assert "key sent no" in logs[1]


async def test_a_wrong_content_type_and_a_missing_audio_part_are_described_not_refused(rig, monkeypatch):
    monkeypatch.setenv(stt.FIELDS_SWITCH, "1")
    client, logs = rig
    r = await client.post("/v1/audio/transcriptions", json={"model": "whisper-1", "file": "abc", "language": "en"})
    assert r.status_code == 501 and "json fields: model=text, file=text, language=text" in logs[1] and "audio: none found" in logs[1]
    body, ctype = form([("model", "whisper-1", None, None)])
    r = await client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})
    assert r.status_code == 501 and "audio: none found" in logs[3]
    r = await client.post("/v1/audio/transcriptions", content=b"junk", headers={"content-type": "text/plain"})
    assert r.status_code == 501 and "content type text/plain" in logs[5] and "no body fields" in logs[5]


async def test_other_containers_are_named(rig, monkeypatch):
    monkeypatch.setenv(stt.FIELDS_SWITCH, "1")
    client, logs = rig
    for data, name in ((b"OggS" + b"\0" * 30, "ogg"), (b"fLaC" + b"\0" * 30, "flac"), (b"ID3\x03" + b"\0" * 30, "mp3"), (b"\0\0\0\x18ftypM4A " + b"\0" * 30, "mp4/m4a")):
        logs.clear()
        body, ctype = form([("file", data, "a.bin", "application/octet-stream")])
        await client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})
        assert f"audio: {name}" in logs[1]


async def test_a_body_over_25_mb_is_refused_with_413_before_it_is_read(rig):
    client, logs = rig
    big = b"\0" * (stt.MAX_BYTES + 1)
    r = await client.post("/v1/audio/transcriptions", content=big, headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413 and r.json()["error"]["type"] == "request_too_large"
    assert logs == []


async def test_a_body_with_no_length_is_stopped_at_the_cap_too(rig):
    client, _ = rig

    async def chunks():
        for _ in range(26):
            yield b"\0" * (1024 * 1024)
    r = await client.post("/v1/audio/transcriptions", content=chunks(), headers={"content-type": "application/octet-stream"})
    assert r.status_code == 413


async def test_the_switch_off_logs_only_the_one_short_line(rig, monkeypatch):
    monkeypatch.delenv(stt.FIELDS_SWITCH, raising=False)
    client, logs = rig
    body, ctype = form([("file", wav(), "a.wav", "audio/wav")])
    await client.post("/v1/audio/transcriptions", content=body, headers={"content-type": ctype})
    assert len(logs) == 1 and not any(m.startswith("Speech-to-text request ") for m in logs)


async def test_the_guard_rules_hold_for_the_new_route():
    p = "/v1/audio/transcriptions"
    assert local_guard.refusal("POST", p, {b"host": "evil.example:8000"}, 8000)
    assert local_guard.refusal("POST", p, {b"host": "127.0.0.1:8000", b"origin": "https://evil.example"}, 8000)
    assert local_guard.refusal("POST", p, {b"host": "127.0.0.1:8000", b"sec-fetch-site": "cross-site"}, 8000)
    assert local_guard.refusal("POST", p, {b"host": "127.0.0.1:8000"}, 8000) is None, "a program on this device (no Origin) is let through"
    assert local_guard.refusal("POST", p, {b"host": "127.0.0.1:8000", b"authorization": "Bearer x"}, 8000) is None
    assert local_guard.refusal("POST", p, {b"host": "127.0.0.1:8000", b"content-type": "multipart/form-data; boundary=x", b"content-length": "10"}, 8000) is None
