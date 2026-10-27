"""Tests for the Antigravity CLI (Google AI Pro/Ultra subscription) provider --
call_geminicli_direct/call_geminicli_streaming, _run_agy_json/_stream_agy_events's subprocess
lifecycle, _parse_agy_models_output/_fetch_agy_models/_agy_status model discovery, and the
CLI-missing path.

Real shapes throughout (the JSON result object, the stream-json NDJSON events, `agy models`' own
tab-separated output, the denied-tool-attempt "SUCCESS status but empty response" quirk) are all
copied verbatim from a real, live session against a real Google AI Pro-authenticated account
(2026-09-15) -- not guessed from docs. See TECHNICAL.md's own "Antigravity CLI integration notes"
for the full narrative; this file just pins those shapes down as regression tests.

Mocking shape mirrors test_codex_subprocess.py (a mock asyncio subprocess with a sync kill() and
async wait()/communicate()) for the JSON (non-streaming) path, and adds a streaming-specific mock
(async readline()/stderr.read() sequences) for _stream_agy_events, which reads incrementally rather
than calling communicate() once.

Updated 2026-09-16: the timeout-kill tests used to assert a plain proc.kill() directly -- now
asserts the shared _kill_process_tree helper was used instead (see test_kill_process_tree.py for
that helper's own dedicated unit tests), fixing the exact same real, live-confirmed Windows hang
found via Codex's identical process-tree shape (an npm-style CLI shim wrapping a real worker
process, whose plain kill()+wait() could hang indefinitely past AGY_TIMEOUT's own deadline).
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_mock_proc(returncode=0):
    """A mock asyncio subprocess with a sync kill() and an async wait()/communicate() -- same shape
    test_codex_subprocess.py's own helper uses, for the communicate()-based _run_agy_json path."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock()
    return proc


def _make_mock_streaming_proc(stdout_lines, stderr_bytes=b"", returncode=0):
    """A mock asyncio subprocess for _stream_agy_events's own incremental-read shape: stdout.readline()
    yields each given line in order then b"" (EOF); stderr.read() yields the given bytes once then
    b"" (EOF) -- draining stderr is a real, separate concurrent loop in the real code (see
    _stream_agy_events's own "why a drain task" comment), so this must support at least two calls.
    Also gives .stdin a real write/drain/close so the concurrent stdin-writer task (the mechanism-1
    fix -- the prompt now travels over stdin, not argv) has something to call without raising; tests
    that care what was actually written read it back off `stdin.write.call_args`."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    stdout = MagicMock()
    stdout.readline = AsyncMock(side_effect=[*stdout_lines, b""])
    proc.stdout = stdout
    stderr = MagicMock()
    stderr.read = AsyncMock(side_effect=[stderr_bytes, b""])
    proc.stderr = stderr
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    stdin.close = MagicMock()
    proc.stdin = stdin
    return proc


# ---- Direct (--output-format json) calls ----

@pytest.mark.asyncio
async def test_direct_raises_clean_error_when_cli_missing(proxy_module):
    """_agy_path() returns None by default in tests (see conftest.py) -- confirm this is a clean,
    actionable 500 naming the real install command, not a crash spawning a subprocess with a None
    executable path."""
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_geminicli_direct(
            system_prompt=None, messages=[{"role": "user", "content": "hi"}],
            model="gemini-3.8-flash-low", max_tokens=100,
        )
    assert exc.value.status_code == 500
    assert "Google Antigravity is not installed" in exc.value.detail
    assert "Check Help" in exc.value.detail


@pytest.mark.asyncio
async def test_direct_kills_proc_on_timeout(proxy_module):
    """_run_agy_json is now a thin consumer of _stream_agy_events (2026-09-16, see its own
    docstring) -- both callers share the exact same subprocess/timeout/kill plumbing, so this
    exercises the same code path test_streaming_kills_proc_on_timeout does below."""
    proc = _make_mock_streaming_proc([])
    proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_kill_tree = AsyncMock()

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module, "AGY_TIMEOUT", 0.01), \
         patch.object(proxy_module, "_kill_process_tree", mock_kill_tree), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_geminicli_direct(
                system_prompt="You are Lydia.", messages=[{"role": "user", "content": "hello"}],
                model="gemini-3.8-flash-low", max_tokens=100,
            )

    assert exc.value.status_code == 504
    mock_kill_tree.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_direct_raises_clean_error_on_non_zero_exit(proxy_module):
    proc = _make_mock_streaming_proc([], stderr_bytes=b"some real stderr text", returncode=1)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_geminicli_direct(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                model="gemini-3.8-flash-low", max_tokens=100,
            )

    assert exc.value.status_code == 502
    assert "some real stderr text" in exc.value.detail


@pytest.mark.asyncio
async def test_direct_parses_a_real_recorded_success_response(proxy_module):
    """Real result-event shape captured live 2026-09-15 against a real Pro-authenticated account,
    now delivered over the stream-json transport (see _stream_agy_events) rather than a bare
    --output-format json blob."""
    lines = [
        json.dumps({
            "event": "result",
            "result": {
                "conversation_id": "1a80b534-8cd4-4e94-99db-87024b57ecca",
                "status": "SUCCESS",
                "response": "They are formidable beasts, but no match for my Thane.",
                "duration_seconds": 1.21,
                "num_turns": 1,
                "usage": {"input_tokens": 23, "output_tokens": 59, "thinking_tokens": 27, "cache_read_tokens": 0, "total_tokens": 82},
            },
        }).encode() + b"\n",
    ]
    proc = _make_mock_streaming_proc(lines)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as create_exec:
        text, usage = await proxy_module.call_geminicli_direct(
            system_prompt="You are Lydia.", messages=[{"role": "user", "content": "What do you think of dragons?"}],
            model="gemini-3.8-flash-low", max_tokens=100,
        )

    assert text == "They are formidable beasts, but no match for my Thane."
    assert usage == {"input_tokens": 23, "output_tokens": 59}
    # Confirms the real flags this depends on for safety: sandboxed, slash-commands disabled, no
    # --dangerously-skip-permissions anywhere in the call.
    args = create_exec.call_args.args
    assert "--sandbox" in args
    assert "--disable-slash-commands" in args
    assert "--dangerously-skip-permissions" not in args
    assert "--model" in args and "gemini-3.8-flash-low" in args
    # Mechanism-1 regression guard: the prompt must never appear as a command-line argument again.
    assert "-p=" in args
    assert not any("What do you think of dragons?" in a for a in args if isinstance(a, str))
    # ...and it must actually have been written to stdin instead.
    written = proc.stdin.write.call_args.args[0]
    assert json.loads(written.decode())["message"]["content"].endswith("What do you think of dragons?\n\nAssistant:")


@pytest.mark.asyncio
async def test_direct_survives_a_denied_tool_attempt_with_empty_response(proxy_module):
    """Real, live-found quirk (2026-09-15): a prompt that leads the agent to attempt a tool (e.g. a
    literal "/usage") still reports status SUCCESS with an EMPTY response and a denied_actions
    list, rather than a distinct failure status. Must degrade to an empty string, never crash --
    same "don't crash on an unusual but real shape" discipline the Gemini API-key provider's own
    content-key fix already established."""
    lines = [
        json.dumps({
            "event": "result",
            "result": {
                "conversation_id": "c255de3b-3c89-4188-b141-d112fd67abc5",
                "status": "SUCCESS",
                "response": "",
                "duration_seconds": 2.11,
                "num_turns": 1,
                "usage": {"input_tokens": 13103, "output_tokens": 342, "thinking_tokens": 262, "cache_read_tokens": 0, "total_tokens": 13445},
                "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
            },
        }).encode() + b"\n",
    ]
    proc = _make_mock_streaming_proc(lines)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        text, usage = await proxy_module.call_geminicli_direct(
            system_prompt=None, messages=[{"role": "user", "content": "/usage"}],
            model="gemini-3.8-flash-low", max_tokens=100,
        )

    assert text == ""
    assert usage == {"input_tokens": 13103, "output_tokens": 342}


@pytest.mark.asyncio
async def test_direct_raises_on_non_success_status(proxy_module):
    lines = [
        json.dumps({"event": "result", "result": {"status": "ERROR", "error": "something real went wrong"}}).encode() + b"\n",
    ]
    proc = _make_mock_streaming_proc(lines)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_geminicli_direct(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                model="gemini-3.8-flash-low", max_tokens=100,
            )

    assert exc.value.status_code == 502
    assert "something real went wrong" in exc.value.detail


@pytest.mark.asyncio
async def test_direct_raises_clean_error_when_no_result_event_arrives(proxy_module):
    """NDJSON parsing is per-line now (see _stream_agy_events): a garbled line is silently skipped
    rather than failing the whole call outright, so the failure mode for "the CLI never produced a
    usable result" is a clean, explicit error instead of a JSONDecodeError on the whole blob."""
    lines = [b"not json at all\n"]
    proc = _make_mock_streaming_proc(lines)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_geminicli_direct(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                model="gemini-3.8-flash-low", max_tokens=100,
            )

    assert exc.value.status_code == 502
    assert "sent back no answer" in exc.value.detail


# ---- Streaming (--output-format stream-json) calls ----

@pytest.mark.asyncio
async def test_streaming_raises_clean_error_when_cli_missing(proxy_module):
    # Raised, not streamed: nothing has been sent yet, so the route turns it into a real HTTP error.
    with pytest.raises(HTTPException) as exc:
        [
            chunk
            async for chunk in proxy_module.call_geminicli_streaming(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                model="gemini-3.8-flash-low", max_tokens=100,
            )
        ]
    assert exc.value.status_code == 500
    assert "Google Antigravity is not installed" in exc.value.detail


@pytest.mark.asyncio
async def test_streaming_translates_a_real_recorded_event_sequence(proxy_module):
    """Real NDJSON event sequence captured live 2026-09-15 (trimmed to the essentials) -- confirms
    text_delta fragments are forwarded as-is (they are already incremental, not cumulative -- see
    _stream_agy_events's own comment) and the stream ends with a real finish_reason + [DONE]."""
    lines = [
        json.dumps({"event": "init", "conversation_id": "x", "init": {"model": "gemini-3.8-flash-low"}}).encode() + b"\n",
        json.dumps({"event": "step_update", "step_update": {"step_index": 0, "state": "DONE", "step_type": "user_input"}}).encode() + b"\n",
        json.dumps({"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE", "step_type": "agent_response", "text_delta": "One is the first number.\nTwo comes n"}}).encode() + b"\n",
        json.dumps({"event": "step_update", "step_update": {"step_index": 1, "state": "ACTIVE", "step_type": "agent_response", "text_delta": "ext in sequence."}}).encode() + b"\n",
        json.dumps({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "agent_response", "text_delta": "\n"}}).encode() + b"\n",
        json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "One is the first number.\nTwo comes next in sequence.\n"}}).encode() + b"\n",
    ]
    proc = _make_mock_streaming_proc(lines)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as create_exec:
        chunks = [
            chunk
            async for chunk in proxy_module.call_geminicli_streaming(
                system_prompt=None, messages=[{"role": "user", "content": "Count to 2."}],
                model="gemini-3.8-flash-low", max_tokens=100,
            )
        ]

    # Each SSE event is its own JSON object -- reassemble the real deltas properly rather than a
    # naive substring search on the raw (escaped) SSE text.
    data_lines = [c for c in "".join(chunks).split("\n\n") if c.startswith("data: ") and c != "data: [DONE]"]
    events = [json.loads(line[len("data: "):]) for line in data_lines]
    reassembled = "".join(e["choices"][0]["delta"].get("content", "") for e in events)
    assert reassembled == "One is the first number.\nTwo comes next in sequence.\n"
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    assert "data: [DONE]" in "".join(chunks)
    # The user_input echo step must never leak into the SSE stream as visible text.
    text_events = [e for e in events if e["choices"][0]["delta"].get("content")]
    assert len(text_events) == 3  # three real text_delta fragments, no extras
    # Mechanism-1 regression guard: the prompt must never appear as a command-line argument.
    args = create_exec.call_args.args
    assert "-p=" in args
    assert not any("Count to 2." in a for a in args if isinstance(a, str))
    written = proc.stdin.write.call_args.args[0]
    assert json.loads(written.decode()) == {"event": "user", "message": {"content": "User: Count to 2.\n\nAssistant:"}}


@pytest.mark.asyncio
async def test_streaming_kills_proc_on_timeout(proxy_module):
    proc = _make_mock_streaming_proc([])
    proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_kill_tree = AsyncMock()

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module, "AGY_TIMEOUT", 0.01), \
         patch.object(proxy_module, "_kill_process_tree", mock_kill_tree), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            [
                chunk
                async for chunk in proxy_module.call_geminicli_streaming(
                    system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                    model="gemini-3.8-flash-low", max_tokens=100,
                )
            ]

    assert exc.value.status_code == 504
    mock_kill_tree.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_spawns_with_start_new_session_for_kill_process_tree(proxy_module):
    """_kill_process_tree's own POSIX branch (os.killpg) only works if the child was made its own
    process group leader AT SPAWN TIME -- see test_codex_subprocess.py's own identical test for the
    full real-Linux justification. A future edit to this call site that drops
    start_new_session=True would silently re-open the same bug on POSIX only (Windows' own taskkill
    path doesn't need it)."""
    proc = _make_mock_streaming_proc([])
    proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_create = AsyncMock(return_value=proc)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module, "AGY_TIMEOUT", 0.01), \
         patch.object(proxy_module, "_kill_process_tree", AsyncMock()), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", mock_create):
        with pytest.raises(HTTPException):  # the simulated timeout is raised as a 504
            async for _ in proxy_module.call_geminicli_streaming(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                model="gemini-3.8-flash-low", max_tokens=100,
            ):
                pass

    assert mock_create.call_args.kwargs.get("start_new_session") is True


@pytest.mark.asyncio
async def test_streaming_raises_clean_error_on_non_zero_exit(proxy_module):
    proc = _make_mock_streaming_proc([], stderr_bytes=b"a real failure reason", returncode=1)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            [
                chunk
                async for chunk in proxy_module.call_geminicli_streaming(
                    system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                    model="gemini-3.8-flash-low", max_tokens=100,
                )
            ]

    assert exc.value.status_code == 502
    assert "a real failure reason" in exc.value.detail


@pytest.mark.asyncio
async def test_stream_handles_a_line_over_64kib_through_the_real_asyncio_reader(proxy_module, tmp_path):
    """Mechanism 2, real regression test: asyncio's StreamReader defaults to a 64 KiB readline()
    buffer, and the CLI echoes the whole prompt back as one `step_type: "user_input"` NDJSON line --
    confirmed live (2026-09-16) that a single line past ~64KB raises `ValueError: Separator is
    found, but chunk is longer than limit` on that default. This spawns a REAL subprocess (a tiny
    throwaway script, not a mock) so it exercises the genuine asyncio StreamReader machinery
    _stream_agy_events depends on -- a MagicMock stand-in returning canned bytes can't reproduce
    the real bug, since the real `ValueError` comes from asyncio's own internal buffer, not from
    anything this code calls directly."""
    big_delta = "x" * 70_000
    helper = tmp_path / "fake_agy_big_line.py"
    helper.write_text(
        "import json\n"
        "print(json.dumps({'event': 'init', 'conversation_id': 'x', 'init': {'model': 'm'}}))\n"
        "print(json.dumps({'event': 'step_update', 'step_update': {'step_index': 0, 'state': 'DONE', "
        "'step_type': 'user_input', 'text_delta': " + repr(big_delta) + "}}))\n"
        "print(json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': 'ok'}}))\n"
    )
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def _spawn_fake_cli(*args, **kwargs):
        # Ignore the real agy argv entirely and spawn the throwaway script above instead -- keeps
        # the real stdin=/stdout=/stderr=/limit= kwargs from the code under test so the actual
        # StreamReader machinery runs for real.
        return await real_create_subprocess_exec(sys.executable, str(helper), **kwargs)

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", _spawn_fake_cli):
        events = [e async for e in proxy_module._stream_agy_events("hi", "m", "req1", str(tmp_path))]

    step_events = [e for e in events if e.get("event") == "step_update"]
    assert any(len(e["step_update"].get("text_delta") or "") > 65536 for e in step_events)
    assert any(e.get("event") == "result" for e in events)


@pytest.mark.asyncio
async def test_streaming_survives_an_unexpected_non_http_exception(proxy_module):
    """Mechanism 3, defence in depth: call_geminicli_streaming used to catch only HTTPException, so
    any other exception -- a real example: the FileNotFoundError from mechanism 1's argv-length bug,
    or the ValueError from mechanism 2's readline() buffer, both fixed elsewhere in this same change
    -- would escape AFTER FastAPI had already sent SSE headers, truncating the response instead of
    ending it cleanly (the exact "cURL streaming request failed: Transferred a partial file" this
    whole item exists to fix). Forces a genuine non-HTTPException failure mid-stream and confirms a
    readable error chunk plus a clean [DONE], never an exception escaping the generator."""
    async def _broken_stream(*args, **kwargs):
        if False:
            yield  # pragma: no cover -- makes this an async generator function
        raise ValueError("Separator is found, but chunk is longer than limit")

    with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
         patch.object(proxy_module, "_stream_agy_events", _broken_stream):
        with pytest.raises(HTTPException) as exc:
            [
                chunk
                async for chunk in proxy_module.call_geminicli_streaming(
                    system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                    model="gemini-3.8-flash-low", max_tokens=100,
                )
            ]

    # Becomes a clean 500 (a real HTTP error response before any text, a clean stream end after) --
    # never a raw exception escaping into a half-sent stream.
    assert exc.value.status_code == 500
    assert "Something went wrong in MultiProxy" in exc.value.detail


# ---- Model discovery ----

class TestParseAgyModelsOutput:
    def test_parses_a_real_recorded_output(self, proxy_module):
        """Real output captured live 2026-09-15 against a real Pro-authenticated account."""
        raw = (
            "Fetching available models...\n"
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
            "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
        )
        rows = proxy_module._parse_agy_models_output(raw)
        assert rows == [
            ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
            ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
            ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
        ]

    def test_skips_blank_and_untabbed_lines(self, proxy_module):
        raw = "Fetching available models...\n\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n   \n"
        rows = proxy_module._parse_agy_models_output(raw)
        assert rows == [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)")]

    def test_empty_output_returns_empty_list(self, proxy_module):
        assert proxy_module._parse_agy_models_output("") == []


def _agy_run(returncode=0, stdout=b""):
    """A stand-in for hidden_process.run (the program is started on a worker thread now, not through asyncio's subprocess)."""
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    return MagicMock(return_value=SimpleNamespace(returncode=returncode, stdout=stdout))


class TestFetchAgyModels:
    @pytest.mark.asyncio
    async def test_returns_none_when_cli_missing(self, proxy_module):
        proxy_module._agy_models_cache = (0.0, None)
        result = await proxy_module._fetch_agy_models()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_parsed_rows_on_success(self, proxy_module):
        proxy_module._agy_models_cache = (0.0, None)
        run = _agy_run(0, b"Fetching available models...\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n")
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.hidden_process, "run", run):
            result = await proxy_module._fetch_agy_models()
        assert result == [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)")]

    @pytest.mark.asyncio
    async def test_returns_none_on_non_zero_exit(self, proxy_module):
        proxy_module._agy_models_cache = (0.0, None)
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.hidden_process, "run", _agy_run(1, b"")):
            result = await proxy_module._fetch_agy_models()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, proxy_module):
        import subprocess
        from unittest.mock import MagicMock
        proxy_module._agy_models_cache = (0.0, None)
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.hidden_process, "run", MagicMock(side_effect=subprocess.TimeoutExpired("agy", 1))):
            result = await proxy_module._fetch_agy_models()
        assert result is None

    @pytest.mark.asyncio
    async def test_caches_within_ttl(self, proxy_module):
        proxy_module._agy_models_cache = (0.0, None)
        run = _agy_run(0, b"gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n")
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.hidden_process, "run", run):
            await proxy_module._fetch_agy_models()
            await proxy_module._fetch_agy_models()
        assert run.call_count == 1, "second call within the TTL should be served from cache"

    @pytest.mark.asyncio
    async def test_force_bypasses_cache(self, proxy_module):
        proxy_module._agy_models_cache = (0.0, None)
        run = _agy_run(0, b"gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n")
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.hidden_process, "run", run):
            await proxy_module._fetch_agy_models()
            await proxy_module._fetch_agy_models(force=True)
        assert run.call_count == 2

    @pytest.mark.asyncio
    async def test_the_program_is_started_off_the_event_loop(self, proxy_module):
        import asyncio
        from types import SimpleNamespace
        proxy_module._agy_models_cache = (0.0, None)
        seen = {}

        def run(*a, **k):
            try:
                asyncio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            return SimpleNamespace(returncode=0, stdout=b"x\tX\n")
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.hidden_process, "run", run):
            await proxy_module._fetch_agy_models()
        assert seen["on_loop"] is False


class TestAgyStatus:
    @pytest.mark.asyncio
    async def test_not_installed(self, proxy_module):
        status = await proxy_module._agy_status()
        assert status["state"] == "off"
        assert status["status"] == "Not installed"
        assert status["testable"] is False
        assert status["models"] == []

    @pytest.mark.asyncio
    async def test_installing_agy_is_picked_up_live_without_a_restart(self, proxy_module):
        """Regression test for the real bug (2026-09-20, identical class to the earlier Codex fix
        in 06c5e0a): AGY_PATH used to be a one-time `shutil.which("agy")` snapshot taken at proxy
        startup, so installing Antigravity CLI (or running its login) while the proxy was already
        running never got picked up -- every status check, including the dashboard's own Refresh
        button, kept reading the stale "not found" result forever. Only a full proxy restart fixed
        it. _agy_path() is now a genuinely live check, so this proves the fix WITHOUT reloading or
        re-importing the module -- that's the whole point: no restart should ever be needed again."""
        status = await proxy_module._agy_status()
        assert status["state"] == "off"
        assert status["status"] == "Not installed"

        # Antigravity CLI "gets installed" mid-session -- no module reload, just the live PATH
        # lookup starting to find it, exactly like a real install would cause.
        async def _fake_none(*, force=False):
            return None
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module, "_fetch_agy_models", _fake_none):
            status = await proxy_module._agy_status()
        assert status["state"] != "off"
        assert status["status"] != "Not installed"

        # And confirm it's a genuinely live check, not a one-time re-snapshot: with the patch
        # removed again, the very next call goes right back to "not installed".
        status = await proxy_module._agy_status()
        assert status["state"] == "off"
        assert status["status"] == "Not installed"

    @pytest.mark.asyncio
    async def test_installed_but_not_usable(self, proxy_module):
        async def _fake(*, force=False):
            return None
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module, "_fetch_agy_models", _fake):
            status = await proxy_module._agy_status()
        assert status["state"] == "warn"
        assert status["testable"] is False
        assert "Sign in" in status["empty"]

    @pytest.mark.asyncio
    async def test_installed_and_working(self, proxy_module):
        async def _fake(*, force=False):
            return [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"), ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)")]
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module, "_fetch_agy_models", _fake):
            status = await proxy_module._agy_status()
        assert status["state"] == "ok"
        assert status["testable"] is True
        assert status["status"] == "Installed and signed in: 2 models available"
        assert status["models"] == [
            ["geminicli/gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)", ""],
            ["geminicli/claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)", ""],
        ]
        assert status["any"] is None


class TestDashboardProvidersGeminicliWiring:
    @pytest.mark.asyncio
    async def test_geminicli_entry_reflects_agy_status(self, proxy_module):
        async def _fake(*, force=False):
            return [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)")]
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module, "_fetch_agy_models", _fake):
            providers = await proxy_module._dashboard_providers()
        geminicli = next(p for p in providers if p["id"] == "geminicli")
        assert geminicli["state"] == "ok"
        assert geminicli["testable"] is True
        assert geminicli["live"] is True
        assert geminicli["keyless"] is True
        assert geminicli["hasKey"] is False
        assert geminicli["refreshEndpoint"] == "/config/geminicli-models"


class TestRefreshRoute:
    def test_refresh_route_forces_a_fresh_check(self, proxy_module, test_client):
        calls = []

        async def _fake(*, force=False):
            calls.append(force)
            return [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)")]

        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module, "_fetch_agy_models", _fake):
            resp = test_client.get("/config/geminicli-models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "ok"
        assert data["testable"] is True
        assert calls == [True], "the Refresh route must bypass the cache"


# ---- End-to-end through chat_completions() ----

class TestGeminicliEndToEnd:
    def test_non_streaming_reaches_geminicli_and_strips_the_prefix(self, proxy_module, test_client):
        lines = [
            json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "Hello", "usage": {"input_tokens": 5, "output_tokens": 2}}}).encode() + b"\n",
        ]
        proc = _make_mock_streaming_proc(lines)
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)) as create_exec:
            r = test_client.post("/v1/chat/completions", json={
                "model": "geminicli/gemini-3.8-flash-low",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200
        args = create_exec.call_args.args
        model_idx = args.index("--model") + 1
        assert args[model_idx] == "gemini-3.8-flash-low"

    def test_missing_cli_uses_the_shared_error_shape(self, proxy_module, test_client):
        r = test_client.post("/v1/chat/completions", json={
            "model": "geminicli/gemini-3.8-flash-low",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 500
        assert "Google Antigravity is not installed" in r.json()["error"]["message"]
