"""The piece that speaks SkyrimNet's voice language: tags, reference clips, the upload/call/wait/download flow (replayed from SkyrimNet's observed
requests, and from a real capture when one is dropped into tests/fixtures/voice/captures/), the queue, the stale-line rule, the not-running error."""
import asyncio
import io
import json
import struct
import time
import wave
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import voice_engine as ve
import voice_service as vs
from voice_downloads import FolderSource
from tests.voice_helpers import make_engine

FIXTURES = Path(__file__).parent / "fixtures" / "voice"


def wav(rate=24000, channels=1, width=2, seconds=0.5):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(rate * seconds)
        if width == 1:
            w.writeframes(bytes((128 + (i % 50)) for i in range(n * channels)))
        else:
            w.writeframes(struct.pack(f"<{n * channels}h", *[(i % 200) * 50 for i in range(n * channels)]))
    return buf.getvalue()


def multipart(filename, data):
    boundary = "----testboundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


# ---- tags ---------------------------------------------------------------------------------------------------------------------------
def test_there_are_exactly_43_tags_and_they_match_skyrimnets_own_allowed_list():
    assert len(vs.ALLOWED_TAGS) == 43
    skyrimnet_list = ["emotion-affection", "emotion-fear", "prosody-speed_very_slow", "prosody-long_pause", "style-whispering", "sfx-sneeze", "sfx-cough"]
    assert all(t.replace("-", ":", 1) in vs.ALLOWED_TAGS for t in skyrimnet_list)


@pytest.mark.parametrize("raw,expected", [
    ("<|emotion:contentment|>Cheers, sweetie.", "<|emotion:contentment|>Cheers, sweetie."),
    ("[emotion-fear]Stay close.", "<|emotion:fear|>Stay close."),
    ("[Emotion-Fear] Stay close.", "<|emotion:fear|> Stay close."),
    ("Wait <|prosody:pause|> here.", "Wait <|prosody:pause|> here."),
    ("<|sfx:sigh|> Fine.", "<|sfx:sigh|>Uh Fine."),
    ("<|sfx:laughter|>Haha, good one.", "<|sfx:laughter|>Haha, good one."),
    ("Well. [sfx-cough]", "Well. <|sfx:cough|>Ahem"),
    ("plain words with [brackets] and <angles>", "plain words with [brackets] and <angles>"),
])
def test_tags_are_converted_or_kept(raw, expected):
    assert vs.convert_tags(raw)[0] == expected


def test_a_tag_the_model_does_not_know_is_removed_and_reported():
    text, dropped = vs.convert_tags("<|emotion:grumpy|>Hmph. [sfx-explosion] <|nonsense|>Done.")
    assert text == "Hmph. Done." and len(dropped) == 3
    assert "<|" not in text and "[" not in text


# ---- reference clips ----------------------------------------------------------------------------------------------------------------
def test_a_clip_that_is_already_right_goes_through_untouched():
    w = wav()
    assert vs.prepare_reference(w) == w      # (equal bytes, not the same object: the same clip prepared by an earlier test is remembered by checksum)


@pytest.mark.parametrize("rate,channels,width", [(44100, 2, 2), (16000, 1, 1), (48000, 1, 2)])
def test_other_wavs_are_brought_to_24k_mono_16_bit(rate, channels, width):
    out = vs.prepare_reference(wav(rate, channels, width))
    with wave.open(io.BytesIO(out)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
        assert 0.45 < w.getnframes() / 24000 < 0.55


def test_a_long_clip_is_cut_and_stays_under_the_engines_limit():
    out = vs.prepare_reference(wav(24000, 1, 2, seconds=45))
    with wave.open(io.BytesIO(out)) as w:
        assert w.getnframes() == 24000 * vs.REFERENCE_MAX_SECONDS
    assert len(out) <= ve.REFERENCE_MAX_BYTES


@pytest.mark.parametrize("junk", [b"", b"not audio at all", b"ID3\x03\x00mp3-ish", b"RIFF" + b"\x00" * 40])
def test_a_clip_in_a_format_it_cannot_use_says_so_plainly(junk):
    with pytest.raises(vs.ClipFormatError) as e:
        vs.prepare_reference(junk)
    assert e.value.message == "That voice clip's format isn't supported."


# ---- reading SkyrimNet's request -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("data", [
    ["Hello there.", "abc/femaleeventoned.wav", 0.8],
    [{"path": "abc/femaleeventoned.wav", "meta": {"_type": "gradio.FileData"}}, "Hello there.", 0.8],
    [0.8, "Hello there.", {"path": "abc/x.wav"}, True],
])
def test_the_values_are_recognised_by_kind_not_by_position(data):
    p = vs.parse_generate_data(data)
    assert p["text"] == "Hello there." and p["ref"].endswith(".wav") and p["temperature"] == 0.8


@pytest.mark.parametrize("data", [None, [], [1, 2], ["   "], {"data": 1}, ["abc.wav"]])
def test_a_request_with_nothing_to_speak_is_not_recognised(data):
    assert vs.parse_generate_data(data) == {}


# ---- the whole flow, replayed -----------------------------------------------------------------------------------------------------------
class Env:
    pass


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "engine-requests.jsonl"))
    engine, src, store = make_engine(tmp_path)
    engine.start_install(FolderSource(src))
    for _ in range(600):
        if engine.job.state != "running":
            break
        await asyncio.sleep(0.05)
    assert engine.job.state == "done", engine.job.error
    logs = []
    service = vs.VoiceService(engine, lambda: tmp_path / "work", log=logs.append)
    app = FastAPI()
    vs.mount(app, service)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000")
    e = Env()
    e.engine, e.service, e.client, e.logs, e.log_file = engine, service, client, logs, tmp_path / "engine-requests.jsonl"
    yield e
    await client.aclose()
    await engine.close()


def engine_requests(e):
    try:
        return [json.loads(x) for x in e.log_file.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []


async def speak(client, prefix, text, sample=None, name="femaleeventoned.wav"):
    """One full SkyrimNet round trip. Returns (answer WAV bytes or None, the SSE text)."""
    data_ref = None
    if sample is not None:
        body, ctype = multipart(name, sample)
        up = await client.post(f"{prefix}/gradio_api/upload", content=body, headers={"content-type": ctype})
        assert up.status_code == 200
        path = up.json()[0]
        assert (await client.head(f"{prefix}/gradio_api/file={path}")).status_code == 200
        data_ref = {"path": path, "meta": {"_type": "gradio.FileData"}}
    call = await client.post(f"{prefix}/gradio_api/call/generate_audio", json={"data": [text, data_ref, 0.8]})
    if call.status_code != 200:
        return None, call.text
    ev = call.json()["event_id"]
    res = await client.get(f"{prefix}/gradio_api/call/generate_audio/{ev}")
    assert res.headers["content-type"].startswith("text/event-stream")
    if not res.text.startswith("event: complete"):
        return None, res.text
    item = json.loads(res.text.split("data: ", 1)[1])[0]
    url = httpx.URL(item["url"])
    audio = await client.get(url.path)
    assert audio.status_code == 200
    return audio.content, res.text


@pytest.mark.parametrize("prefix", ["", "/voice"])
@pytest.mark.asyncio
async def test_the_observed_flow_works_at_the_main_address_and_under_voice(env, prefix):
    audio, sse = await speak(env.client, prefix, "<|emotion:contentment|>Cheers, sweetie.", wav())
    assert audio[:4] == b"RIFF" and sse.startswith("event: complete\ndata: [")
    item = json.loads(sse.split("data: ", 1)[1])[0]
    assert item["url"].startswith(f"http://127.0.0.1:8000{prefix}/gradio_api/file=") and item["meta"] == {"_type": "gradio.FileData"} and item["orig_name"].endswith(".wav")
    got = engine_requests(env)[-1]
    assert got["input"] == "<|emotion:contentment|>Cheers, sweetie." and got["ref_bytes"] == len(wav()) and got["temperature"] == 0.8


@pytest.mark.asyncio
async def test_every_observed_line_in_the_fixture_gets_audio_back(env):
    fx = json.loads((FIXTURES / "skyrimnet-0.26.0-observed-flow.json").read_text(encoding="utf-8"))
    assert "0.26.0" in fx["source"]
    for line in fx["lines"]:
        audio, sse = await speak(env.client, "", line["text"], wav(), name=f"{line['voice']}.wav")
        assert audio and audio[:4] == b"RIFF", (line, sse)
    sent = [r["input"] for r in engine_requests(env)]
    assert "<|emotion:fear|>Stay close to me." in sent, "bracket spelling reached the engine in the engine's own"
    assert not any("[" in s for s in sent)


@pytest.mark.asyncio
async def test_the_sample_is_checked_with_head_and_a_removed_one_is_gone(env):
    body, ctype = multipart("a.wav", wav())
    path = (await env.client.post("/gradio_api/upload", content=body, headers={"content-type": ctype})).json()[0]
    assert (await env.client.head(f"/gradio_api/file={path}")).status_code == 200
    assert (await env.client.head("/gradio_api/file=nothing/here.wav")).status_code == 404


@pytest.mark.asyncio
async def test_a_path_that_climbs_out_of_the_voice_folders_is_never_served(env, tmp_path):
    (tmp_path / "secret.txt").write_text("private")
    for p in ("../../secret.txt", "..%2F..%2Fsecret.txt", str(tmp_path / "secret.txt"), "C:/Windows/win.ini"):
        r = await env.client.get(f"/gradio_api/file={p}")
        assert r.status_code == 404 and b"private" not in r.content


@pytest.mark.asyncio
async def test_a_sample_in_a_format_the_engine_cannot_use_never_reaches_it_and_the_line_is_spoken_without_it(env):
    audio, text = await speak(env.client, "", "Hello.", b"this is not a wav file at all", name="bad.wav")
    assert audio and vs.UNREADABLE_SAMPLE in env.logs                       # (2026-10-06) tolerated: no sample, but the line is not lost
    assert all(r["ref_bytes"] is None for r in engine_requests(env) if r["input"] == "Hello.")


@pytest.mark.asyncio
async def test_other_wav_shapes_are_converted_on_the_way(env):
    audio, _ = await speak(env.client, "", "Hello.", wav(44100, 2, 2))
    assert audio
    assert engine_requests(env)[-1]["ref_bytes"] != len(wav(44100, 2, 2))


@pytest.mark.asyncio
async def test_an_upload_with_no_file_in_it_gets_the_plain_log_line_and_a_clean_error(env):
    r = await env.client.post("/gradio_api/upload", content=b"nothing", headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 400 and r.json()["detail"] == vs.UNRECOGNISED
    assert vs.UNRECOGNISED in env.logs


@pytest.mark.asyncio
async def test_a_request_it_does_not_recognise_gets_the_plain_log_line_and_a_clean_error(env):
    r = await env.client.post("/gradio_api/call/generate_audio", json={"data": []})
    assert r.status_code == 400 and r.json()["detail"] == vs.UNRECOGNISED == "SkyrimNet sent a voice request MultiProxy doesn't recognize. Check for a MultiProxy update."
    r = await env.client.post("/gradio_api/call/some_new_function", json={"data": ["x"]})
    assert r.status_code == 404 and any(vs.UNRECOGNISED in m for m in env.logs)
    assert env.service.status()["unrecognisedRequests"] == 2
    r = await env.client.post("/gradio_api/call/generate_audio", content=b"not json")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_when_higgs_is_not_running_skyrimnet_gets_an_immediate_clean_error(env):
    await env.engine.stop()
    t = time.time()
    audio, text = await speak(env.client, "", "Hello.", wav())
    assert audio is None and vs.NOT_RUNNING in text and time.time() - t < 1.0
    r = await env.client.post("/gradio_api/call/generate_audio", json={"data": ["Hello."]})
    assert r.status_code == 503 and r.json()["detail"] == "Higgs voice isn't running. Press **Start** in MultiProxy."


@pytest.mark.asyncio
async def test_the_bare_status_route_says_what_the_engine_says(env):
    s = (await env.client.get("/voice/status")).json()
    assert s["state"] == "ready" and s["ready"] and s["linesWaiting"] == 0 and "downloads" in s


# ---- many lines at once ---------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_many_lines_at_once_are_served_in_arrival_order_and_none_is_dropped(env, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "0.15")
    env.engine.proc.terminate()                                    # restart so the engine picks up the new delay
    await env.engine.restart()
    texts = [f"Line number {i}." for i in range(8)]
    tasks = []
    for t in texts:
        tasks.append(asyncio.ensure_future(speak(env.client, "", t)))
        await asyncio.sleep(0.02)                                  # arrival order
    await asyncio.sleep(0.2)
    assert env.service.queue.waiting > 0 and env.engine.status()["label"].startswith("Busy (")
    results = await asyncio.gather(*tasks)
    assert all(a and a[:4] == b"RIFF" for a, _ in results), "nothing dropped"
    order = [r["input"] for r in engine_requests(env) if r["input"].startswith("Line number")]
    assert order == texts


@pytest.mark.asyncio
async def test_a_line_that_waited_too_long_gets_an_immediate_clean_error_not_a_late_voice(env, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "0.5")
    env.engine.proc.terminate()
    await env.engine.restart()
    env.service.stale_after = 0.9
    t0 = time.time()
    results = await asyncio.gather(*[speak(env.client, "", f"Line {i}.") for i in range(5)])
    ok = [r for r in results if r[0]]
    stale = [r for r in results if not r[0]]
    assert len(ok) >= 1 and len(stale) >= 2 and len(ok) + len(stale) == 5
    assert all(vs.STALE in r[1] and r[1].startswith("event: error") for r in stale)
    assert time.time() - t0 < 4, "the stale lines were refused as soon as their time ran out, not after the whole queue"


def test_the_constants_say_why():
    src = Path(vs.__file__).read_text(encoding="utf-8")
    assert vs.ENGINE_PARALLELISM == 1 and vs.STALE_AFTER_SECONDS == 20.0
    assert "timeout: 30" in src and "N lines at once take N times one line" in src


# ---- the real capture, when there is one ---------------------------------------------------------------------------------------------------
def real_captures():
    return sorted((FIXTURES / "captures").glob("*/[0-9][0-9][0-9]-POST-*generate*audio*.json")) if (FIXTURES / "captures").is_dir() else []


@pytest.mark.skipif(not real_captures(), reason="no real capture has been dropped into tests/fixtures/voice/captures/ yet")
@pytest.mark.parametrize("path", real_captures() or [None])
@pytest.mark.asyncio
async def test_every_real_captured_generate_audio_request_is_recognised(path):
    entry = json.loads(Path(path).read_text(encoding="utf-8"))
    data = entry["body"]["json"]["data"]
    assert vs.parse_generate_data(data), f"SkyrimNet sent {data!r}, which MultiProxy can't read"
