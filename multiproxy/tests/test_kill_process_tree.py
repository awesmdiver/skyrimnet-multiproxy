"""Tests for _kill_process_tree, the shared cleanup helper Codex/Antigravity's own timeout paths
both call on a hung CLI subprocess.

Real, live-confirmed bug (2026-09-16): both the real Codex CLI and the real Antigravity CLI resolve
to a multi-level process tree on Windows (an npm-installed `.CMD` shim launching a real worker
process as ITS OWN child). A plain `proc.kill()` only ever reaches the immediate child asyncio
itself spawned -- confirmed live via Get-Process during an actual hung request: the immediate child
was gone, but the real worker process it had spawned was still alive, unkilled, minutes later. This
left the OLD unbounded `await proc.wait()` at every one of these call sites hanging INDEFINITELY --
a real request against a genuinely unreachable custom Codex provider sat for 12+ minutes with no
response, despite CODEX_TIMEOUT correctly firing at its configured 180s deadline. `taskkill /T /F`
is Windows' own built-in recursive tree-kill; this is the fix, tested in isolation here.

Updated 2026-09-16 (second pass): the non-Windows branch used to be a bare `proc.kill()` on the
unverified assumption that POSIX had "no such tree-wide orphan risk" -- that was wrong. Confirmed
live on real Linux (WSL2): a synthetic repro reproduces the exact same orphaned-grandchild bug, AND
the real `@openai/codex` npm package's own `bin/codex.js` (identical on Linux/macOS/Windows) spawns
its native binary via plain `child_process.spawn()`, a genuine separate OS child that `proc.kill()`'s
SIGKILL cannot reach (SIGKILL bypasses the JS shim's own SIGINT/SIGTERM/SIGHUP forwarding, since
SIGKILL can't be caught by anything). The real POSIX fix is a process group: `start_new_session=True`
at spawn time (now passed by every caller of this helper) plus `os.killpg(os.getpgid(pid), SIGKILL)`
at kill time, tested here the same way the Windows taskkill path is -- mocking `os.killpg`/
`os.getpgid` directly rather than relying on the actual host OS, so this suite gives the same
answer whether it runs on Windows or POSIX.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_proc(pid=12345):
    proc = MagicMock()
    proc.pid = pid
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _make_mock_taskkill_proc():
    """A mock for the `taskkill /T /F /PID ...` subprocess itself -- a real, successful tree-kill
    never needs to fall back to the plain proc.kill()."""
    taskkill_proc = MagicMock()
    taskkill_proc.wait = AsyncMock()
    return taskkill_proc


@pytest.mark.asyncio
async def test_windows_uses_taskkill_and_skips_plain_kill(proxy_module):
    proc = _make_mock_proc(pid=12345)
    taskkill_proc = _make_mock_taskkill_proc()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        assert args[0] == "taskkill"
        assert args[1:] == ("/T", "/F", "/PID", "12345")
        return taskkill_proc

    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", side_effect=_fake_create_subprocess_exec):
        await proxy_module._kill_process_tree(proc)

    taskkill_proc.wait.assert_awaited_once()
    proc.kill.assert_not_called()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_windows_falls_back_to_plain_kill_if_taskkill_itself_fails(proxy_module):
    """taskkill isn't guaranteed to exist/succeed in every environment (a locked-down machine, a
    missing PATH entry) -- falling back to the plain kill() this replaced means a real Codex/
    Antigravity request still gets SOME cleanup attempt rather than none at all."""
    proc = _make_mock_proc()

    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("no taskkill"))):
        await proxy_module._kill_process_tree(proc)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_posix_uses_process_group_kill_and_skips_plain_kill(proxy_module):
    """The real fix: os.killpg(os.getpgid(pid), SIGKILL) signals the whole process group (the
    tracked child AND any descendant it spawned, e.g. the real native binary Codex's own JS shim
    launches as its own child) -- confirmed live on real Linux this actually reaches a grandchild a
    plain proc.kill() cannot. No taskkill-equivalent subprocess is spawned on this path."""
    proc = _make_mock_proc(pid=12345)

    # signal.SIGKILL itself doesn't exist on Windows (this suite runs cross-platform) -- patched in
    # with its real POSIX value (always 9) for the duration of this test rather than referenced
    # directly, so the assertion below doesn't fail on this platform for an unrelated reason.
    with patch.object(proxy_module.os, "name", "posix"), \
         patch.object(proxy_module.signal, "SIGKILL", 9, create=True), \
         patch.object(proxy_module.os, "getpgid", MagicMock(return_value=99), create=True) as mock_getpgid, \
         patch.object(proxy_module.os, "killpg", MagicMock(), create=True) as mock_killpg, \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock()) as mock_create:
        await proxy_module._kill_process_tree(proc)

    mock_create.assert_not_called()
    mock_getpgid.assert_called_once_with(12345)
    mock_killpg.assert_called_once_with(99, 9)
    proc.kill.assert_not_called()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_posix_falls_back_to_plain_kill_if_killpg_itself_fails(proxy_module):
    """A process that already exited, or one that was never actually made its own group leader for
    some reason (start_new_session wasn't honored, a platform quirk), raises ProcessLookupError/
    OSError out of os.getpgid()/os.killpg() -- falling back to the plain kill() this replaced means
    a real request still gets SOME cleanup attempt rather than none at all, the same reasoning the
    Windows taskkill-failure fallback already uses."""
    proc = _make_mock_proc()

    with patch.object(proxy_module.os, "name", "posix"), \
         patch.object(proxy_module.os, "getpgid", MagicMock(side_effect=ProcessLookupError("no such process")), create=True):
        await proxy_module._kill_process_tree(proc)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_wait_is_bounded_even_if_it_hangs(proxy_module):
    """The exact real, live-confirmed failure this whole helper exists to fix: even after a
    successful kill, proc.wait() itself can still hang indefinitely for an orphaned grandchild
    Windows never signals completion for. This must never be allowed to block the caller forever."""
    async def _hang_forever():
        await asyncio.sleep(9999)

    proc = _make_mock_proc()
    proc.wait = AsyncMock(side_effect=_hang_forever)
    taskkill_proc = _make_mock_taskkill_proc()

    with patch.object(proxy_module.os, "name", "nt"), \
         patch.object(proxy_module, "_KILL_TREE_WAIT_TIMEOUT", 0.05), \
         patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=taskkill_proc)):
        await proxy_module._kill_process_tree(proc)  # must return promptly, not hang the test itself
