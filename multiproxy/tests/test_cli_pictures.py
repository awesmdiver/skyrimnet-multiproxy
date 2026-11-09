"""Pictures on the Codex and Grok routes (2026-11-08): Codex gets them as files (`-i`), Grok refuses them in plain words, Copilot refuses them; none drops one silently.

Fake processes only: no real Codex, Grok or Copilot call, no sign-in.
"""
import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import claude_courier as cc

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake picture bytes").decode()
WITH_PICTURE = [{"role": "user", "content": [{"type": "text", "text": "What is this?"}, {"type": "image_url", "image_url": {"url": PNG}}]}]
TEXT_ONLY = [{"role": "user", "content": "hello"}]


def _fake_codex(seen: dict):
    """A stand-in `create_subprocess_exec` that records the arguments and whether each `-i` file existed, and writes the answer file the way Codex does."""
    async def make(*args, **kwargs):
        seen["args"] = list(args)
        seen["files"] = {}
        a = list(args)
        for i, word in enumerate(a):
            if word == "-i":
                path = a[i + 1]
                with open(path, "rb") as f:
                    seen["files"][path] = f.read()
            if word == "-o":
                out = a[i + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write("a red square")
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.wait = AsyncMock()
        return proc
    return make


@pytest.mark.asyncio
async def test_a_picture_reaches_codex_as_a_file_and_is_removed_after(proxy_module):
    seen: dict = {}
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/codex"), patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
            patch.object(proxy_module.hidden_process, "create_subprocess_exec", _fake_codex(seen)):
        text, _ = await proxy_module.call_codex_direct("sys", WITH_PICTURE, "gpt-5.5", 100)
    assert text == "a red square"
    args = seen["args"]
    assert args[1] == "exec" and args[2] == "-i" and args[4].startswith("--")          # `-i FILE` first, then another option (so nothing later is read as a picture)
    (path, data), = seen["files"].items()
    assert path.endswith(".png") and data == b"\x89PNG fake picture bytes"
    assert not os.path.exists(path)                                                    # the throwaway folder is gone the moment the call ends


@pytest.mark.asyncio
async def test_a_text_request_to_codex_is_unchanged(proxy_module):
    seen: dict = {}
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/codex"), patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)), \
            patch.object(proxy_module.hidden_process, "create_subprocess_exec", _fake_codex(seen)):
        await proxy_module.call_codex_direct("sys", TEXT_ONLY, "gpt-5.5", 100)
    assert "-i" not in seen["args"] and seen["args"][1:3] == ["exec", "--skip-git-repo-check"]


@pytest.mark.asyncio
async def test_a_picture_codex_cannot_take_is_refused_in_plain_words(proxy_module):
    bad = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]
    with patch.object(proxy_module, "_codex_path", lambda: "/fake/codex"), patch.object(proxy_module, "_codex_logged_in", AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as e:
            await proxy_module.call_codex_direct("sys", bad, "gpt-5.5", 100)
    assert e.value.status_code == 400 and e.value.detail == "The Codex subscription can't look at pictures. Pick another AI for Vision on the Routing page."


@pytest.mark.asyncio
async def test_grok_refuses_a_picture_and_never_starts_the_program(proxy_module):
    started = AsyncMock()
    with patch.object(proxy_module, "_grok_path", lambda: "/fake/grok"), patch.object(proxy_module.hidden_process, "create_subprocess_exec", started):
        with pytest.raises(HTTPException) as e:
            await proxy_module.call_grok_direct("sys", WITH_PICTURE, "grok-4", 100)
        with pytest.raises(HTTPException) as e2:
            [c async for c in proxy_module.call_grok_streaming("sys", WITH_PICTURE, "grok-4", 100)]
    started.assert_not_called()
    for err in (e, e2):
        assert err.value.status_code == 400 and err.value.detail == "The Grok subscription can't look at pictures. Pick another AI for Vision on the Routing page."


@pytest.mark.asyncio
async def test_a_text_request_to_grok_still_starts_the_program(proxy_module):
    started = AsyncMock(side_effect=RuntimeError("started"))
    with patch.object(proxy_module, "_grok_path", lambda: "/fake/grok"), patch.object(proxy_module.hidden_process, "create_subprocess_exec", started):
        with pytest.raises(Exception) as e:
            await proxy_module.call_grok_direct("sys", TEXT_ONLY, "grok-4", 100)
    started.assert_called_once()
    assert not (isinstance(e.value, HTTPException) and e.value.status_code == 400)


def test_the_sentence_is_one_shape_for_every_route():
    assert cc.no_pictures_line("Claude") == cc.LINE_NO_PICTURES
    assert cc.has_picture(WITH_PICTURE) and not cc.has_picture(TEXT_ONLY)


@pytest.mark.asyncio
async def test_copilot_on_a_courier_refuses_a_picture(tmp_path):
    import logging
    import copilot_courier as cp
    host = cp.CopilotHost(tmp_path / "copilot", logging.getLogger("t")) if hasattr(cp, "CopilotHost") else None
    if host is None:
        pytest.skip("no host class")
    with pytest.raises(cp.CopilotError) as e:
        await host.chat(None, {"model": "copilot/default", "messages": WITH_PICTURE})
    assert e.value.status == 400 and e.value.message == "The Copilot subscription can't look at pictures. Pick another AI for Vision on the Routing page."
