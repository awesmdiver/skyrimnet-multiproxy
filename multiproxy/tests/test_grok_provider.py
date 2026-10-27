"""Tests for the Grok (SuperGrok subscription) provider: the `grok` command-line program run once per request.

A real, fake `grok` program (a tiny launcher around a Python script) is started for each call, so the real process
handling runs: finding it, not signed in, a streamed reply, a whole-message reply, a failure, a hang, and what it is
and isn't handed. Its `models` output and its sign-in error are the real text xAI's v1.0.46 prints when signed out
(read 2026-10-03). The reply events follow xAI's published shapes (one ACP session update per line, or the Anthropic
style); no signed-in account was available, so those shapes are pinned here as what the reader accepts.
"""
import json
import os
import stat
import sys
import time

import pytest
from fastapi import HTTPException

# What `grok models` printed on 2026-10-03 (v1.0.46) with nobody signed in.
REAL_MODELS_SIGNED_OUT = "You are not authenticated.\n\nDefault model: grok-4.6\n\nAvailable models:\n  * grok-4.6 (default)\n  - grok-4.5\n"
REAL_MODELS_SIGNED_IN = "Default model: grok-4.6\n\nAvailable models:\n  * grok-4.6 (default)\n  - grok-4.5\n"
# What `grok -p ... --output-format streaming-json` printed when nobody is signed in.
REAL_SIGNED_OUT_EVENT = {"type": "error", "message": "Not signed in. To authenticate without a browser, run:\n  grok login --device-code\n\nAlternatively, set the XAI_API_KEY environment variable or run `grok login` on a machine with a browser."}

FAKE_SCRIPT = r'''
import json, os, sys, time
args = sys.argv[1:]
mode = os.environ.get("GROK_FAKE_MODE", "stream")
log = os.environ.get("GROK_FAKE_LOG")
if "models" in args:
    sys.stdout.write(os.environ.get("GROK_FAKE_MODELS", ""))
    sys.exit(0)
prompt = ""
if "--prompt-file" in args:
    with open(args[args.index("--prompt-file") + 1], encoding="utf-8") as f:
        prompt = f.read()
if log:
    with open(log, "w", encoding="utf-8") as f:
        json.dump({"args": args, "cwd_files": sorted(os.listdir(os.getcwd())), "prompt": prompt}, f)
def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n"); sys.stdout.flush()
if mode == "stream":
    for piece in ("Dragons ", "are ", "rude."):
        emit({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": piece}}}})
    emit({"type": "result", "usage": {"input_tokens": 11, "output_tokens": 3}})
elif mode == "whole":
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Whole answer."}]}})
    emit({"type": "result", "result": "Whole answer.", "usage": {"inputTokens": 5, "outputTokens": 2}})
elif mode == "replay":
    with open(os.environ["GROK_FAKE_REPLAY"], encoding="utf-8") as f:
        for line in f:
            sys.stdout.write(line); sys.stdout.flush()
elif mode == "signedout":
    emit(json.loads(os.environ["GROK_FAKE_ERROR"]))
    sys.stderr.write("Error: Not signed in.\n")
    sys.exit(1)
elif mode == "fail":
    sys.stderr.write("the model melted\n")
    sys.exit(2)
elif mode == "hang":
    time.sleep(60)
'''


@pytest.fixture()
def fake_grok(tmp_path, monkeypatch, proxy_module):
    """A real program called `grok` that behaves as GROK_FAKE_MODE says; points the proxy at it."""
    script = tmp_path / "fake_grok.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        launcher = tmp_path / "grok.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "grok"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("GROK_FAKE_LOG", str(log))
    monkeypatch.setenv("GROK_FAKE_ERROR", json.dumps(REAL_SIGNED_OUT_EVENT))
    monkeypatch.setattr(proxy_module, "_grok_path", lambda: str(launcher))
    return log


MSGS = [{"role": "user", "content": "What do you think of dragons?"}]


# ---- Not installed ----

@pytest.mark.asyncio
async def test_direct_and_streaming_say_clearly_when_grok_is_not_installed(proxy_module):
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_grok_direct(None, MSGS, "grok-4.6", 100)
    assert exc.value.status_code == 500
    assert "Grok isn't installed" in exc.value.detail and "Check Help" in exc.value.detail
    with pytest.raises(HTTPException) as exc2:
        async for _ in proxy_module.call_grok_streaming(None, MSGS, "grok-4.6", 100):
            pass
    assert exc2.value.status_code == 500


def test_the_program_is_found_in_its_own_install_folder_when_it_is_not_on_the_path(proxy_module, monkeypatch, tmp_path):
    monkeypatch.setattr(proxy_module.poll_cache, "cached_which", lambda name: None)
    monkeypatch.setattr(proxy_module, "_grok_default_exe", lambda: str(tmp_path / "grok.exe"))
    assert proxy_module._find_grok() == str(tmp_path / "grok.exe")
    monkeypatch.setattr(proxy_module, "_grok_default_exe", lambda: None)
    assert proxy_module._find_grok() is None
    monkeypatch.setattr(proxy_module.poll_cache, "cached_which", lambda name: "/usr/bin/grok")
    assert proxy_module._find_grok() == "/usr/bin/grok", "on the path wins"


# ---- A reply ----

@pytest.mark.asyncio
async def test_direct_joins_the_streamed_pieces_and_reads_the_usage(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "stream")
    text, usage = await proxy_module.call_grok_direct("You are Lydia.", MSGS, "grok-4.6", 100)
    assert text == "Dragons are rude."
    assert usage == {"input_tokens": 11, "output_tokens": 3}


@pytest.mark.asyncio
async def test_a_reply_that_only_comes_whole_is_still_read(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "whole")
    text, usage = await proxy_module.call_grok_direct(None, MSGS, "grok-4.6", 100)
    assert text == "Whole answer."
    assert usage == {"input_tokens": 5, "output_tokens": 2}


@pytest.mark.asyncio
async def test_streaming_sends_each_piece_then_a_clean_end(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "stream")
    chunks = [c async for c in proxy_module.call_grok_streaming(None, MSGS, "grok-4.6", 100)]
    deltas = [json.loads(c[len("data: "):])["choices"][0]["delta"].get("content") for c in chunks[:-2]]
    assert deltas == ["Dragons ", "are ", "rude."]
    assert json.loads(chunks[-2][len("data: "):])["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_streaming_a_whole_message_reply_is_sent_as_one_piece(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "whole")
    chunks = [c async for c in proxy_module.call_grok_streaming(None, MSGS, "grok-4.6", 100)]
    assert [json.loads(c[len("data: "):])["choices"][0]["delta"].get("content") for c in chunks[:-2]] == ["Whole answer."]


# ---- What it is and isn't handed ----

@pytest.mark.asyncio
async def test_it_gets_the_prompt_in_a_file_an_empty_folder_and_no_way_to_change_anything(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "stream")
    await proxy_module.call_grok_direct("You are Lydia.", [{"role": "user", "content": [
        {"type": "text", "text": "Look at this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}], "grok-4.5", 100)
    seen = json.loads(fake_grok.read_text(encoding="utf-8"))
    args = seen["args"]
    assert args[args.index("--model") + 1] == "grok-4.5"
    assert args[args.index("--output-format") + 1] == "streaming-json"
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("--disable-web-search", "--no-subagents", "--prompt-file"):
        assert flag in args, flag
    assert args[args.index("--max-turns") + 1] == "1"
    assert "--always-approve" not in args and "-p" not in args
    assert "System: You are Lydia." in seen["prompt"] and "Look at this" in seen["prompt"]
    assert "AAAA" not in seen["prompt"] and "image" not in seen["prompt"].lower().replace("this", ""), "a picture is never handed over"
    assert seen["cwd_files"] == ["prompt.txt"], "it runs in an empty scratch folder holding only the prompt"


# ---- The real app's reply format (recorded 2026-10-03, signed in, grok 1.0.x) ----

REAL_PONG = os.path.join(os.path.dirname(__file__), "fixtures", "grok_real_pong.ndjson")


def _replay(monkeypatch, lines):
    path = os.path.join(os.environ["GROK_FAKE_LOG"] + ".replay")
    with open(path, "w", encoding="utf-8") as f:
        f.write(chr(10).join(json.dumps(x) if isinstance(x, dict) else x for x in lines) + chr(10))
    monkeypatch.setenv("GROK_FAKE_MODE", "replay")
    monkeypatch.setenv("GROK_FAKE_REPLAY", path)


@pytest.mark.asyncio
async def test_the_real_recording_gives_pong_without_the_thoughts_and_reads_the_usage(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "replay")
    monkeypatch.setenv("GROK_FAKE_REPLAY", REAL_PONG)
    text, usage = await proxy_module.call_grok_direct(None, MSGS, "grok-4.7", 100)
    assert text == "pong"
    assert usage == {"input_tokens": 14471, "output_tokens": 31}
    chunks = [c async for c in proxy_module.call_grok_streaming(None, MSGS, "grok-4.7", 100)]
    assert [json.loads(c[6:])["choices"][0]["delta"].get("content") for c in chunks[:-2]] == ["pong"]


@pytest.mark.asyncio
async def test_a_longer_answer_arrives_piece_by_piece(proxy_module, fake_grok, monkeypatch):
    _replay(monkeypatch, [{"type": "thought", "data": "hmm"}, {"type": "text", "data": "Dragons "}, {"type": "text", "data": "are "}, {"type": "text", "data": "rude."},
                          {"type": "end", "stopReason": "end_turn", "usage": {"input_tokens": 7, "output_tokens": 3}}])
    chunks = [c async for c in proxy_module.call_grok_streaming(None, MSGS, "grok-4.7", 100)]
    assert [json.loads(c[6:])["choices"][0]["delta"].get("content") for c in chunks[:-2]] == ["Dragons ", "are ", "rude."]
    text, usage = await proxy_module.call_grok_direct(None, MSGS, "grok-4.7", 100)
    assert text == "Dragons are rude." and usage == {"input_tokens": 7, "output_tokens": 3}


@pytest.mark.asyncio
async def test_an_error_line_and_an_early_stop_become_plain_errors(proxy_module, fake_grok, monkeypatch):
    _replay(monkeypatch, [{"type": "error", "message": "The model melted"}])
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_grok_direct(None, MSGS, "grok-4.7", 100)
    assert exc.value.status_code == 502 and "melted" in exc.value.detail
    _replay(monkeypatch, [{"type": "text", "data": "half"}, {"type": "end", "stopReason": "max_tokens"}])
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_grok_direct(None, MSGS, "grok-4.7", 100)
    assert exc.value.status_code == 502 and "max_tokens" in exc.value.detail


@pytest.mark.asyncio
async def test_only_the_smallest_tool_list_is_asked_for(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "stream")
    await proxy_module.call_grok_direct(None, MSGS, "grok-4.7", 100)
    args = json.loads(fake_grok.read_text(encoding="utf-8"))["args"]
    assert args[args.index("--tools") + 1] == "read_file" and args[args.index("--model") + 1] == "grok-4.7"


def test_the_signed_in_models_list_is_read(proxy_module):
    raw = chr(10).join(["You are logged in with grok.com.", "", "Default model: grok-4.7", "", "Available models:", "  * grok-4.7 (default)", ""])
    got = proxy_module._parse_grok_models_output(raw)
    assert got["signed_in"] and [r[0] for r in got["rows"]] == ["grok-4.7"]


# ---- Errors ----

@pytest.mark.asyncio
async def test_not_signed_in_is_a_plain_answer_with_the_real_sign_in_event(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "signedout")
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_grok_direct(None, MSGS, "grok-4.6", 100)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Grok isn't signed in. Check Help for the sign-in steps."


@pytest.mark.asyncio
async def test_not_signed_in_while_streaming_is_the_same_plain_answer(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "signedout")
    with pytest.raises(HTTPException) as exc:
        async for _ in proxy_module.call_grok_streaming(None, MSGS, "grok-4.6", 100):
            pass
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_a_failed_run_says_what_the_program_printed(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "fail")
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_grok_direct(None, MSGS, "grok-4.6", 100)
    assert exc.value.status_code == 502
    assert "the model melted" in exc.value.detail


@pytest.mark.asyncio
async def test_a_hung_program_is_stopped_and_reported(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODE", "hang")
    monkeypatch.setattr(proxy_module, "GROK_TIMEOUT", 1.0)
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_grok_direct(None, MSGS, "grok-4.6", 100)
    assert exc.value.status_code == 504
    assert "took too long" in exc.value.detail


# ---- Reading events ----

def test_the_event_reader_understands_both_published_shapes(proxy_module):
    f = proxy_module._grok_event_text
    acp = {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hi"}}}}
    flat = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Yo"}}
    assert f(acp) == ("Hi", "")
    assert f(flat) == ("Yo", "")
    assert f({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hey"}}}) == ("Hey", "")
    assert f({"type": "assistant", "message": {"content": [{"type": "text", "text": "All"}]}}) == ("", "All")
    assert f({"type": "result", "result": "Done"}) == ("", "Done")
    assert f({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "hmm"}}) == ("", ""), "thinking is not the answer"
    assert f({"type": "something new"}) == ("", "")


# ---- Models and the status line ----

def test_the_models_list_is_read_from_the_programs_own_output(proxy_module):
    out = proxy_module._parse_grok_models_output(REAL_MODELS_SIGNED_OUT)
    assert out == {"signed_in": False, "rows": [("grok-4.6", "Grok 4.6"), ("grok-4.5", "Grok 4.5")]}
    assert proxy_module._parse_grok_models_output(REAL_MODELS_SIGNED_IN)["signed_in"] is True


@pytest.mark.asyncio
async def test_status_not_installed(proxy_module):
    s = await proxy_module._grok_status()
    assert (s["state"], s["status"], s["testable"], s["models"]) == ("off", "Not installed", False, [])
    assert "Check Help" in s["empty"]


@pytest.mark.asyncio
async def test_status_installed_and_signed_in_lists_the_models(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODELS", REAL_MODELS_SIGNED_IN)
    s = await proxy_module._grok_status(force=True)
    assert s["state"] == "ok" and s["testable"] is True
    assert s["status"] == "Installed and signed in: 2 models available"
    assert [r[0] for r in s["models"]] == ["grok/grok-4.6", "grok/grok-4.5"]


@pytest.mark.asyncio
async def test_status_installed_but_not_signed_in_says_so(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODELS", REAL_MODELS_SIGNED_OUT)
    s = await proxy_module._grok_status(force=True)
    assert (s["state"], s["status"], s["testable"], s["models"]) == ("warn", "Installed, but not signed in", False, [])
    assert s["empty"] == "Sign in to Grok on your device, then press Check for models."


@pytest.mark.asyncio
async def test_one_failed_look_is_retried_once_and_a_lasting_failure_is_remembered_only_briefly(proxy_module, fake_grok, monkeypatch):
    good = type("P", (), {"returncode": 0, "stdout": REAL_MODELS_SIGNED_IN.encode()})()
    bad = type("P", (), {"returncode": 1, "stdout": b""})()
    answers = [bad, good]
    monkeypatch.setattr(proxy_module.hidden_process, "run", lambda *a, **k: answers.pop(0))
    info = await proxy_module._fetch_grok_models(force=True)
    assert info is not None and info["signed_in"] is True and answers == []
    answers[:] = [bad, bad]
    monkeypatch.setattr(proxy_module.hidden_process, "run", lambda *a, **k: answers.pop(0))
    assert await proxy_module._fetch_grok_models(force=True) is None
    cached_at, _ = proxy_module._grok_models_cache
    assert time.time() - cached_at >= proxy_module.GROK_MODELS_CACHE_TTL - proxy_module.GROK_MODELS_FAILED_TTL - 1, "the failure expires in seconds, not a minute"


# ---- It is a provider like the others ----

def test_the_grok_prefix_routes_and_reads_like_the_other_providers(proxy_module):
    assert proxy_module.is_grok_model("grok/grok-4.6") and proxy_module.is_grok_model("GROK/grok-4.6")
    assert proxy_module.strip_grok_prefix("grok/grok-4.6") == "grok-4.6"
    assert not proxy_module.is_grok_model("xai/grok-4") and not proxy_module.is_grok_model("grok-4.6")
    assert not proxy_module.is_unclaimed_slash_model("grok/grok-4.6")
    assert proxy_module._resolve_model_name("grok/grok-4.6") == "grok-4.6"
    assert proxy_module._provider_label_for("grok/grok-4.6") == "Grok"
    assert proxy_module._ai_class("grok/grok-4.6") == (proxy_module.routing.CLASS_INCLUDED, "subscription")


@pytest.mark.asyncio
async def test_the_dashboard_lists_grok_with_its_own_setup_state(proxy_module, fake_grok, monkeypatch):
    monkeypatch.setenv("GROK_FAKE_MODELS", REAL_MODELS_SIGNED_IN)
    p = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "grok")
    assert p["name"] == "Grok (SuperGrok)" and p["keyless"] is True and p["refreshEndpoint"] == "/config/grok-models"
    assert "SuperGrok" in p["help"]
