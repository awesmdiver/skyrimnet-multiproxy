"""Tests for the Codex CLI subprocess lifecycle (call_codex_direct/call_codex_streaming).

Adapted from rhinos0608/skyrimnet-codex-proxy's own tests/test_cli_subprocess_cleanup.py --
confirms a timed-out Codex CLI subprocess is actually cleaned up via _kill_process_tree (see
test_kill_process_tree.py for that helper's own dedicated unit tests), not left orphaned until the
whole proxy process exits. Our own _run_codex_exec already does this, so this is a confirming
regression test, not a bug fix -- worth having anyway, since a future edit to _run_codex_exec could
silently drop the cleanup call without any other test noticing.

Updated 2026-09-16: a plain proc.kill() used to sit directly in _run_codex_exec, and these tests
asserted it was called once. Real, live-confirmed Windows bug found the same day: for the real
multi-level Codex CLI process tree (an npm .CMD shim -> node.exe -> a native Rust binary), that
plain kill()+wait() could hang the whole request for 12+ minutes past CODEX_TIMEOUT's own 180s
deadline. _run_codex_exec now delegates to the shared _kill_process_tree helper (taskkill /T /F on
Windows), so these tests assert THAT was used instead of asserting the old direct proc.kill() call.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_mock_proc():
    """A mock asyncio subprocess with a sync kill() and an async wait()/communicate()."""
    proc = MagicMock()
    proc.returncode = None
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_direct_kills_proc_on_timeout(proxy_module):
    proc = _make_mock_proc()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_kill_tree = AsyncMock()

    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
         patch.object(proxy_module, "_kill_process_tree", mock_kill_tree), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_codex_direct(
                system_prompt="You are Lydia.",
                messages=[{"role": "user", "content": "hello"}],
                model="gpt-5.5",
                max_tokens=512,
            )

    assert exc.value.status_code == 504
    mock_kill_tree.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_streaming_kills_proc_on_timeout(proxy_module):
    proc = _make_mock_proc()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_kill_tree = AsyncMock()

    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
         patch.object(proxy_module, "_kill_process_tree", mock_kill_tree), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        # Nothing is sent before the CLI finishes, so a timeout is raised as a real 504 (the route
        # turns it into an HTTP error response) -- never text inside a "200" stream.
        with pytest.raises(HTTPException) as exc:
            [
                chunk
                async for chunk in proxy_module.call_codex_streaming(
                    system_prompt="You are Lydia.",
                    messages=[{"role": "user", "content": "hello"}],
                    model="gpt-5.5",
                    max_tokens=512,
                )
            ]

    assert exc.value.status_code == 504
    mock_kill_tree.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_spawns_with_start_new_session_for_kill_process_tree(proxy_module):
    """_kill_process_tree's own POSIX branch (os.killpg) only works if the child was made its own
    process group leader AT SPAWN TIME -- confirmed live on real Linux this is required for
    os.getpgid()/os.killpg() to reach a spawned grandchild at all. A future edit to this call site
    that drops start_new_session=True would silently re-open the exact bug _kill_process_tree fixes,
    on POSIX only (Windows' own taskkill path doesn't need it). Reuses the timeout setup (rather
    than a full successful-call setup) purely to keep this test focused on the one spawn-time kwarg
    -- what happens after spawn is already covered by the other tests in this file."""
    proc = _make_mock_proc()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_create = AsyncMock(return_value=proc)

    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
         patch.object(proxy_module, "_kill_process_tree", AsyncMock()), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", mock_create):
        with pytest.raises(HTTPException):
            await proxy_module.call_codex_direct(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}],
                model="gpt-5.5", max_tokens=100,
            )

    assert mock_create.call_args.kwargs.get("start_new_session") is True


@pytest.mark.asyncio
async def test_direct_raises_clean_error_when_cli_missing(proxy_module):
    """_codex_path() returns None by default in tests (see conftest.py) -- confirm this is a clean,
    actionable 500, not a crash trying to spawn a subprocess with a None executable path."""
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_codex_direct(
            system_prompt=None, messages=[{"role": "user", "content": "hi"}], model="gpt-5.5", max_tokens=100,
        )
    assert exc.value.status_code == 500
    assert "Codex isn't installed. Check Help to see how to install it." in exc.value.detail


@pytest.mark.asyncio
async def test_direct_raises_clean_error_when_not_logged_in(proxy_module):
    """codex exec itself doesn't fail with a clean "not logged in" message when unauthenticated
    (confirmed live -- see TECHNICAL.md's own Codex integration notes), so this precondition check
    is the only source of a clean error here; confirm it actually fires before any subprocess spawn
    is attempted."""
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_codex_direct(
                system_prompt=None, messages=[{"role": "user", "content": "hi"}], model="gpt-5.5", max_tokens=100,
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_streaming_survives_an_unexpected_non_http_exception(proxy_module):
    """Real production incident (2026-09-16): call_codex_streaming caught only HTTPException from
    _run_codex_exec -- any other exception (a subprocess/decode surprise) escaped AFTER FastAPI had
    already sent SSE headers, truncating the response instead of ending it cleanly. Same defense-
    in-depth shape the Antigravity CLI streaming fix already established."""
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
         patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
         patch.object(proxy_module, "_run_codex_exec", AsyncMock(side_effect=ValueError("a genuinely unexpected bug"))):
        with pytest.raises(HTTPException) as exc:
            [
                chunk
                async for chunk in proxy_module.call_codex_streaming(
                    system_prompt=None, messages=[{"role": "user", "content": "hi"}], model="gpt-5.5", max_tokens=100,
                )
            ]

    # Raised as a clean 500 (becomes a real HTTP error response), never a truncated stream.
    assert exc.value.status_code == 500
    assert "Something went wrong in MultiProxy" in exc.value.detail
