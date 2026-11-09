"""A line the voice refuses says why (in the log and to SkyrimNet), lines the voice turns away only because it is still busy are tried again in order, and lines that
fail together are one log line. Stand-in voice program only (tests/fake_audiocpp_server.py: FAKE_REFUSE, FAKE_BUSY_REFUSE)."""
import asyncio
import time

import pytest

import voice_engine as ve
import voice_service as vs
from tests.test_voice_service import env, engine_requests  # noqa: F401  (the fixture)


async def restart_with(env, monkeypatch, **knobs):
    for k, v in knobs.items():
        monkeypatch.setenv(k, str(v))
    env.engine.proc.terminate()
    await env.engine.restart()
    for _ in range(200):
        if env.engine.state == "ready":
            break
        await asyncio.sleep(0.05)
    assert env.engine.state == "ready"


def say(env, text):
    return env.client.post("/v1/audio/speech", json={"model": "higgs", "input": text})


# ---- the sentence ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,txt,kind,words", [
    (500, '{"error":{"message":"CUDA error: out of memory"}}', "memory", "ran out of memory"),
    (409, '{"error":{"message":"engine is busy with another request"}}', "busy", "busy with another line"),
    (429, "slow down", "busy", "busy with another line"),
    (500, '{"error":{"message":"model not loaded"}}', "not_loaded", "isn't loaded"),
    (400, '{"error":{"message":"could not decode voice_ref"}}', "clip", "refused the voice clip"),
    (500, '{"error":{"message":"something odd happened"}}', "other", "refused that line"),
    (502, "", "unreadable", "couldn't be read"),
])
def test_the_sentence_says_what_the_voice_answered_and_which_kind_it_was(status, txt, kind, words):
    k, sentence = ve.explain_refusal(status, txt)
    assert k == kind and words in sentence and str(status) in sentence and "Log" not in sentence


def test_the_line_of_speech_is_never_in_the_sentence():
    k, sentence = ve.explain_refusal(500, '{"error":{"message":"bad input: The dragon is coming for all of us"}}', "The dragon is coming for all of us")
    assert "dragon" not in sentence and "…" in sentence


def test_the_engines_words_are_cut_short_and_stay_on_one_line():
    _, sentence = ve.explain_refusal(500, '{"error":{"message":"' + "word " * 200 + '\\nsecond"}}')
    assert "\n" not in sentence and len(sentence) < 320


# ---- what SkyrimNet and the log are told ---------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_refusing_voice_gives_the_real_reason_to_skyrimnet_and_the_log(env, monkeypatch):
    await restart_with(env, monkeypatch, FAKE_REFUSE="500|CUDA error: out of memory")
    r = await say(env, "Hello there.")
    msg = r.json()["error"]["message"]
    assert r.status_code == 503 and "ran out of memory" in msg and "CUDA error: out of memory" in msg and "Open **Log**" not in msg
    monkeypatch.setattr(vs, "FAILURE_GROUP_SECONDS", 0)
    r = await say(env, "Hello again.")
    assert any("ran out of memory" in m and "failed after" in m for m in env.logs)


@pytest.mark.asyncio
async def test_lines_that_fail_together_are_one_log_line(env, monkeypatch):
    monkeypatch.setattr(vs, "FAILURE_GROUP_SECONDS", 0.3)
    await restart_with(env, monkeypatch, FAKE_REFUSE="500|CUDA error: out of memory")
    env.logs.clear()
    rs = await asyncio.gather(*[say(env, f"Line {i}.") for i in range(6)])
    assert all(r.status_code == 503 for r in rs)
    await asyncio.sleep(0.8)
    grouped = [m for m in env.logs if "voice lines refused in" in m]
    assert len(grouped) == 1 and grouped[0].startswith("6 voice lines refused in ") and "ran out of memory" in grouped[0]
    assert not [m for m in env.logs if "failed after" in m], "no per-line warning for the group"


@pytest.mark.asyncio
async def test_one_failed_line_is_still_its_own_log_line_once_the_group_is_over(env, monkeypatch):
    monkeypatch.setattr(vs, "FAILURE_GROUP_SECONDS", 0.2)
    await restart_with(env, monkeypatch, FAKE_REFUSE="500|CUDA error: out of memory")
    env.logs.clear()
    await say(env, "Just one.")
    await asyncio.sleep(0.5)
    assert len([m for m in env.logs if m.startswith("Voice line (OpenAI-Compatible)") and "failed after" in m]) == 1


# ---- a voice that is only busy -----------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lines_the_voice_turns_away_while_busy_are_spoken_in_order(env, monkeypatch):
    monkeypatch.setattr(vs, "BUSY_RETRY_SECONDS", 0.05)
    await restart_with(env, monkeypatch, FAKE_BUSY_REFUSE=2, FAKE_DELAY=0.05)
    tasks = []
    for t in ("First line.", "Second line.", "Third line."):
        tasks.append(asyncio.ensure_future(say(env, t)))
        await asyncio.sleep(0.03)
    rs = await asyncio.gather(*tasks)
    assert [r.status_code for r in rs] == [200, 200, 200] and all(r.content[:4] == b"RIFF" for r in rs)
    assert [x["input"] for x in engine_requests(env) if x["input"].endswith(" line.")] == ["First line.", "Second line.", "Third line."]


@pytest.mark.asyncio
async def test_a_voice_that_stays_busy_past_the_waiting_time_gets_one_clear_503_with_the_reason(env, monkeypatch):
    monkeypatch.setattr(vs, "BUSY_RETRY_SECONDS", 0.05)
    env.service.stale_after = 1.0
    await restart_with(env, monkeypatch, FAKE_BUSY_REFUSE=100000)
    t = time.time()
    r = await say(env, "Never fits.")
    msg = r.json()["error"]["message"]
    assert r.status_code == 503 and 0.8 < time.time() - t < 4 and msg.startswith("The voice was busy for ") and "busy with another line" in msg and "Open **Log**" not in msg


@pytest.mark.asyncio
async def test_the_busy_retry_is_the_same_through_a_courier_relay(env, monkeypatch):
    """The Courier names a busy refusal `refused_busy`, and the main device raises the retrying error from it."""
    import courier_client
    assert "refused_busy" in open(courier_client.__file__, encoding="utf-8").read()
    e = ve.EngineRefusedBusy("The voice was busy with another line (x).")
    assert isinstance(e, ve.VoiceEngineError) and e.status == 503


@pytest.mark.asyncio
async def test_the_original_burst_one_answered_then_six_together_is_all_spoken_in_order(env, monkeypatch):
    """The 2026-11-05 15:22 shape: a line is answered, then six arrive within about two seconds while the voice is still finishing. The voice turns the first of them
    away as busy; none of the six may fail."""
    monkeypatch.setattr(vs, "BUSY_RETRY_SECONDS", 0.05)
    await restart_with(env, monkeypatch, FAKE_BUSY_REFUSE=3, FAKE_DELAY=0.05)
    assert (await say(env, "Answered first line.")).status_code == 200
    tasks = []
    for i in range(6):
        tasks.append(asyncio.ensure_future(say(env, f"Burst {i} line.")))
        await asyncio.sleep(0.02)
    rs = await asyncio.gather(*tasks)
    assert [r.status_code for r in rs] == [200] * 6
    assert [x["input"] for x in engine_requests(env) if x["input"].startswith("Burst")] == [f"Burst {i} line." for i in range(6)]
