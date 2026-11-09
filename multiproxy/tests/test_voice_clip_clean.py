"""A voice clip the voice program cannot read is rewritten as a plain WAV before it is sent, the log names the voice, and a kept clip that still fails is worked out again
once. Fake clips of every shape SkyrimNet could plausibly send; a strict reader stands in for the engine (tests/fake_audiocpp_server.py FAKE_STRICT_WAV)."""
import asyncio
import math
import struct

import pytest

import voice_clips as vc
import voice_engine as ve
import voice_service as vs
from tests.test_voice_refusal import restart_with, say
from tests.test_voice_service import env, engine_requests  # noqa: F401  (the fixture)


def pcm(rate=24000, ch=1, bits=16, seconds=0.5):
    n = int(rate * seconds)
    if bits == 8:
        return bytes(128 + int(60 * math.sin(i / 7)) for i in range(n * ch))
    if bits == 16:
        return struct.pack(f"<{n * ch}h", *[int(8000 * math.sin(i / 7)) for i in range(n * ch)])
    if bits == 24:
        return b"".join(int(2 ** 20 * math.sin(i / 7)).to_bytes(3, "little", signed=True) for i in range(n * ch))
    if bits == 32:
        return struct.pack(f"<{n * ch}f", *[0.3 * math.sin(i / 7) for i in range(n * ch)])
    raise ValueError(bits)


def chunk(tag: bytes, body: bytes, pad=True) -> bytes:
    return tag + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 and pad else b"")


def fmt_part(kind=1, ch=1, rate=24000, bits=16, extensible=False):
    block = ch * bits // 8
    base = struct.pack("<HHIIHH", 0xFFFE if extensible else kind, ch, rate, rate * block, block, bits)
    if extensible:
        base += struct.pack("<HHI", 22, bits, 4) + struct.pack("<H", kind) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    return chunk(b"fmt ", base)


def wav(rate=24000, ch=1, bits=16, kind=1, extra=b"", extensible=False, data_len=None, seconds=0.5, cut=0, riff_len=None):
    body = pcm(rate, ch, 32 if kind == 3 else bits, seconds)
    d = struct.pack("<I", len(body) if data_len is None else data_len)
    out = b"WAVE" + fmt_part(kind, ch, rate, bits, extensible) + extra + b"data" + d + body
    out = b"RIFF" + struct.pack("<I", len(out) if riff_len is None else riff_len) + out
    return out[: len(out) - cut] if cut else out


def strict_accepts(b: bytes) -> bool:
    """The strict reader the engine is taken to be: 'fmt ' (16 bytes, PCM) straight after WAVE, 'data' straight after it, its length the real length."""
    return (b[:4] == b"RIFF" and b[8:16] == b"WAVEfmt " and b[16:20] == struct.pack("<I", 16) and b[36:40] == b"data" and struct.unpack("<I", b[40:44])[0] == len(b) - 44)


LIST = chunk(b"LIST", b"INFOISFT" + struct.pack("<I", 14) + b"Lavf58.76.100\x00")
SHAPES = {
    "plain": wav(),
    "LIST before data (what ffmpeg writes)": wav(extra=LIST),
    "fact before data": wav(extra=chunk(b"fact", struct.pack("<I", 12000))),
    "id3 before data": wav(extra=chunk(b"id3 ", b"ID3\x03\x00\x00\x00\x00\x00\x00")),
    "WAVE_FORMAT_EXTENSIBLE": wav(extensible=True),
    "24-bit": wav(bits=24),
    "32-bit float": wav(kind=3, bits=32),
    "data length 0": wav(data_len=0),
    "data length 0xFFFFFFFF": wav(data_len=0xFFFFFFFF, riff_len=0xFFFFFFFF),
    "odd chunk without its pad byte": wav(extra=chunk(b"LIST", b"INFOabc", pad=False)),
    "cut short": wav(cut=3001),
    "8 kHz": wav(rate=8000),
    "48 kHz": wav(rate=48000),
    "8-bit": wav(bits=8),
    "six channels": wav(ch=6),
    "stereo": wav(ch=2),
}
NOT_WAV = {"OGG": b"OggS\x00\x02" + b"\x00" * 60, "MP3": b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 20, "FLAC": b"fLaC\x00\x00\x00\x22" + b"\x00" * 40,
           "XWM": b"RIFF\x24\x00\x00\x00XWMAfmt " + b"\x00" * 40}


def test_the_shapes_the_engine_cannot_read_are_the_ones_that_are_not_plain():
    """The shapes a strict reader refuses (documented here so the handoff can name them): all of them except the plain one."""
    refused = sorted(k for k, v in SHAPES.items() if not strict_accepts(v))
    assert "plain" not in refused and "LIST before data (what ffmpeg writes)" in refused and "data length 0" in refused and "WAVE_FORMAT_EXTENSIBLE" in refused


@pytest.mark.parametrize("name", list(SHAPES))
def test_every_shape_comes_out_as_a_plain_wav_the_strict_reader_accepts(name):
    out = vc.prepare_reference(SHAPES[name])
    assert strict_accepts(out) and vc.is_plain_wav(out), name
    assert len(out) > 10000, "the sound is still there"


def test_a_plain_clip_is_not_written_again():
    clip = SHAPES["plain"]
    assert vc.prepare_reference(clip) is clip


def test_a_24k_mono_16_bit_clip_with_extra_parts_is_written_again_not_passed_on():
    """The old rule passed on any clip already 24 kHz mono 16-bit that the standard library could open, extra parts and all."""
    clip = SHAPES["LIST before data (what ffmpeg writes)"]
    assert not strict_accepts(clip)
    assert vc.prepare_reference(clip) != clip


@pytest.mark.parametrize("kind", list(NOT_WAV))
def test_a_sound_that_is_not_a_wav_is_refused_and_named(kind):
    with pytest.raises(vc.ClipFormatError) as e:
        vc.prepare_reference(NOT_WAV[kind])
    assert e.value.kind == kind and kind in vc.clip_facts(NOT_WAV[kind])


def test_the_clip_facts_are_one_short_line_without_the_sound():
    f = vc.clip_facts(SHAPES["LIST before data (what ffmpeg writes)"])
    assert f.startswith("PCM, 1 channel, 24000 Hz, 16-bit, 24000 bytes of sound") and "extra parts before the sound: LIST" in f and "\n" not in f
    assert "the header says 0" in vc.clip_facts(SHAPES["data length 0"]) and "cut short" in vc.clip_facts(SHAPES["cut short"])


# ---- in play: the kept clip, the log, the retry ----------------------------------------------------------------------------------------
def keep(env, name, data):
    folder = env.engine.clips.kept_folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.wav").write_bytes(data)


@pytest.mark.asyncio
async def test_a_bad_kept_clip_is_cleaned_before_it_is_sent_and_the_original_is_left_alone(env, monkeypatch):
    await restart_with(env, monkeypatch, FAKE_STRICT_WAV=1)
    bad = SHAPES["LIST before data (what ffmpeg writes)"]
    keep(env, "skeleton", bad)
    said = []
    env.engine.clips.log = said.append
    r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Rattle.", "voice": "skeleton"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert (env.engine.clips.kept_folder / "skeleton.wav").read_bytes() == bad, "the stored original is not changed"
    assert strict_accepts((env.engine.clips.folder / "cleaned" / "skeleton.wav").read_bytes()), "the clean copy is kept beside it"
    assert any("voice skeleton" in m and "sent as a plain WAV" in m for m in said)


@pytest.mark.asyncio
async def test_a_good_kept_clip_is_sent_as_it_is(env, monkeypatch):
    await restart_with(env, monkeypatch, FAKE_STRICT_WAV=1)
    keep(env, "femalenord", SHAPES["plain"])
    r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Hello.", "voice": "femalenord"})
    assert r.status_code == 200 and not (env.engine.clips.folder / "cleaned").exists()


@pytest.mark.asyncio
async def test_a_refused_line_names_the_voice_and_the_clip(env, monkeypatch):
    monkeypatch.setattr(vs, "FAILURE_GROUP_SECONDS", 0)
    await restart_with(env, monkeypatch, FAKE_REFUSE="500|failed to read WAV data chunk")
    keep(env, "skeleton", SHAPES["LIST before data (what ffmpeg writes)"])
    r = await env.client.post("/v1/audio/speech", json={"model": "higgs", "input": "Rattle.", "voice": "skeleton"})
    assert r.status_code == 503
    line = next(m for m in env.logs if "failed after" in m)
    assert "voice skeleton" in line and "kept from SkyrimNet: PCM, 1 channel, 24000 Hz, 16-bit" in line and "extra parts before the sound: LIST" in line
    assert "Rattle" not in line


@pytest.mark.asyncio
async def test_refusals_are_grouped_per_voice(env, monkeypatch):
    monkeypatch.setattr(vs, "FAILURE_GROUP_SECONDS", 0.3)
    await restart_with(env, monkeypatch, FAKE_REFUSE="500|failed to read WAV data chunk")
    env.logs.clear()
    await asyncio.gather(*[env.client.post("/v1/audio/speech", json={"model": "higgs", "input": f"Line {i}.", "voice": "skeleton"}) for i in range(3)])
    await asyncio.gather(*[env.client.post("/v1/audio/speech", json={"model": "higgs", "input": f"Line {i}.", "voice": "atronach"}) for i in range(2)])
    await asyncio.sleep(0.8)
    grouped = [m for m in env.logs if "voice lines refused in" in m]
    assert any(m.startswith("3 voice lines refused in") and "for voice skeleton" in m for m in grouped)
    assert any(m.startswith("2 voice lines refused in") and "for voice atronach" in m for m in grouped)


@pytest.mark.asyncio
async def test_a_line_the_engine_cannot_read_the_clip_for_is_tried_once_more_after_the_clip_is_worked_out_again(env, monkeypatch):
    keep(env, "skeleton", SHAPES["plain"])
    calls, cleaned = [], []
    real = env.engine._speak

    async def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ve.VoiceEngineError(502, "The voice refused that line (the voice program answered 500; it said: failed to read WAV data chunk).")
        return await real(*a, **k)
    monkeypatch.setattr(env.engine, "_speak", flaky)
    real_reclean = env.engine.clips.reclean
    monkeypatch.setattr(env.engine.clips, "reclean", lambda v: (cleaned.append(v), real_reclean(v))[1])
    logged = []
    monkeypatch.setattr(env.engine, "log", logged.append)
    res = await env.engine.synthesize("Rattle.", None, voice="skeleton")
    assert res.wav[:4] == b"RIFF" and len(calls) == 2 and cleaned == ["skeleton"]
    assert any("worked out again as a plain WAV and the line is being tried once more" in m and "skeleton" in m for m in logged)


@pytest.mark.asyncio
async def test_the_retry_happens_once_only(env, monkeypatch):
    keep(env, "skeleton", SHAPES["plain"])
    calls = []

    async def always(*a, **k):
        calls.append(1)
        raise ve.VoiceEngineError(502, "failed to read WAV data chunk")
    monkeypatch.setattr(env.engine, "_speak", always)
    with pytest.raises(ve.VoiceEngineError):
        await env.engine.synthesize("Rattle.", None, voice="skeleton")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_clip_that_is_no_wav_at_all_is_said_plainly_and_the_line_goes_on_without_it(env, monkeypatch):
    sample = NOT_WAV["OGG"]
    job = await env.service.start_parsed({"text": "Hello.", "ref_bytes": None, "temperature": None, "top_p": None, "seed": None}, ref_bytes=sample, voice="creature")
    await asyncio.wait_for(job.done.wait(), 20)
    assert vs.UNREADABLE_SAMPLE in env.logs
    assert any("The clip for voice creature is in a format MultiProxy cannot read (OGG)" in m for m in env.logs)
