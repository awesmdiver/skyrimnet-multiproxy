"""Tests for Vision (screenshots) through Google Antigravity (queue: agy-vision, 2026-09-29).

Real finding this whole feature is built on: `agy`'s own stream-json input has no inline image
content block at all -- confirmed live, a real Anthropic-style
{"type":"image","source":{...}} block gets `stream input content block type "image" is not
supported (only "text")` straight back. What DOES work, also confirmed live against a real
signed-in account: `agy` is a full agentic CLI with its own `view_file` tool -- telling it, in
plain text, to look at a real image FILE by path makes it call that tool itself and it genuinely
sees the image (a real test picture with a crimson background and the word "BANANA" on it came
back correctly as "COLOR=red|TEXT=BANANA" -- not something a text-only guess could produce).
`--add-dir` turned out unnecessary; the sandbox already permits `view_file` on the process's own
cwd, which is exactly the tempdir `_agy_flatten_prompt` writes the decoded image into.

Mocking shape follows test_geminicli_provider.py's own conventions (a mock asyncio subprocess with
the same stdin/stdout/stderr shape).
"""
import asyncio
import base64
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# A real, tiny valid PNG (a 1x1 red pixel) -- small enough to inline here, but genuinely a real
# decodable image, not an arbitrary byte string, since _extract_image_parts's own job is decoding.
_REAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)
_REAL_PNG_BYTES = base64.b64decode(_REAL_PNG_B64)


def _image_url_part(text="What do you see?", b64=_REAL_PNG_B64, mime="image/png"):
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


def _make_mock_streaming_proc(stdout_lines, stderr_bytes=b"", returncode=0):
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


def _result_line(response="a real answer"):
    return json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": response, "usage": {"input_tokens": 10, "output_tokens": 5}}}).encode() + b"\n"


class TestExtractImageParts:
    def test_no_images_in_a_plain_string(self, proxy_module):
        assert proxy_module._extract_image_parts("just text") == []

    def test_no_images_in_a_text_only_part_list(self, proxy_module):
        assert proxy_module._extract_image_parts([{"type": "text", "text": "hi"}]) == []

    def test_decodes_a_real_image_url_part(self, proxy_module):
        images = proxy_module._extract_image_parts(_image_url_part())
        assert len(images) == 1
        ext, data = images[0]
        assert ext == "png"
        assert data == _REAL_PNG_BYTES

    def test_multiple_image_parts_all_decoded(self, proxy_module):
        content = _image_url_part() + [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_REAL_PNG_B64}"}}]
        images = proxy_module._extract_image_parts(content)
        assert [ext for ext, _ in images] == ["png", "jpeg"]

    def test_malformed_data_url_is_skipped_not_raised(self, proxy_module):
        content = [{"type": "image_url", "image_url": {"url": "not-a-data-url"}}]
        assert proxy_module._extract_image_parts(content) == []

    def test_bad_base64_is_skipped_not_raised(self, proxy_module):
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,***not-valid***"}}]
        assert proxy_module._extract_image_parts(content) == []

    def test_missing_image_url_key_is_skipped(self, proxy_module):
        assert proxy_module._extract_image_parts([{"type": "image_url"}]) == []


class TestFlattenPromptWritesImages:
    def test_no_image_dir_leaves_prompt_exactly_as_before(self, proxy_module, tmp_path):
        """image_dir=None (the default) must produce byte-identical output to the pre-Vision
        behavior, even when the message DOES carry an image part -- a caller that hasn't been
        updated for this (there are none left, but the contract matters) must never crash or
        silently write files nobody asked for."""
        prompt = proxy_module._agy_flatten_prompt(None, [{"role": "user", "content": _image_url_part("hello")}])
        assert "hello" in prompt
        assert "screenshot" not in prompt.lower()
        assert "view_file" not in prompt

    def test_text_only_message_is_unaffected_by_a_real_image_dir(self, proxy_module, tmp_path):
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        prompt = proxy_module._agy_flatten_prompt(None, [{"role": "user", "content": "just text"}], image_dir=str(image_dir))
        assert prompt == "User: just text\n\nAssistant:"
        assert os.listdir(image_dir) == []

    def test_one_image_writes_a_real_file_and_notes_it_singular(self, proxy_module, tmp_path):
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        prompt = proxy_module._agy_flatten_prompt(
            None, [{"role": "user", "content": _image_url_part("what color?")}], image_dir=str(image_dir),
        )
        files = os.listdir(image_dir)
        assert len(files) == 1
        assert files[0].endswith(".png")
        with open(os.path.join(image_dir, files[0]), "rb") as f:
            assert f.read() == _REAL_PNG_BYTES
        assert "what color?" in prompt
        assert "a screenshot" in prompt
        assert "view_file" in prompt
        assert str(image_dir) in prompt
        assert files[0] in prompt

    def test_two_images_in_one_message_note_it_plural(self, proxy_module, tmp_path):
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        content = _image_url_part() + [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_REAL_PNG_B64}"}}]
        prompt = proxy_module._agy_flatten_prompt(None, [{"role": "user", "content": content}], image_dir=str(image_dir))
        assert "2 screenshots" in prompt
        assert "them" in prompt
        assert len(os.listdir(image_dir)) == 2

    def test_images_in_different_messages_each_get_their_own_note(self, proxy_module, tmp_path):
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        messages = [
            {"role": "user", "content": _image_url_part("first")},
            {"role": "assistant", "content": "ok, looking"},
            {"role": "user", "content": _image_url_part("second")},
        ]
        prompt = proxy_module._agy_flatten_prompt(None, messages, image_dir=str(image_dir))
        assert prompt.count("a screenshot") == 2
        assert len(os.listdir(image_dir)) == 2

    def test_empty_subtype_data_url_is_not_treated_as_an_image(self, proxy_module, tmp_path):
        """`data:image/;base64,...` has no subtype at all -- _AGY_IMAGE_DATA_URL_RE requires one or
        more characters there, so this is correctly skipped entirely (no file written, no crash),
        not "written with some guessed default extension"."""
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        content = [{"type": "image_url", "image_url": {"url": "data:image/;base64," + _REAL_PNG_B64}}]
        assert proxy_module._extract_image_parts(content) == []
        prompt = proxy_module._agy_flatten_prompt(None, [{"role": "user", "content": content}], image_dir=str(image_dir))
        assert os.listdir(image_dir) == []
        assert "view_file" not in prompt


class TestCallGeminicliDirectWithVision:
    @pytest.mark.asyncio
    async def test_image_request_writes_a_file_the_subprocess_can_reach_and_cleans_up_after(self, proxy_module):
        proc = _make_mock_streaming_proc([_result_line("COLOR=red")])
        captured_cwd = {}

        async def _spawn(*args, **kwargs):
            captured_cwd["cwd"] = kwargs.get("cwd")
            # The image file must already exist, in this exact cwd, THE MOMENT the process would
            # start -- real agy would look for it right away.
            files = os.listdir(kwargs["cwd"])
            captured_cwd["files_at_spawn"] = files
            return proc

        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", _spawn):
            text, usage = await proxy_module.call_geminicli_direct(
                system_prompt=None,
                messages=[{"role": "user", "content": _image_url_part("what color is this?")}],
                model="gemini-3.8-flash-medium", max_tokens=100,
            )

        assert text == "COLOR=red"
        assert len(captured_cwd["files_at_spawn"]) == 1
        stdin_payload = json.loads(proc.stdin.write.call_args.args[0])
        assert "view_file" in stdin_payload["message"]["content"]
        assert captured_cwd["files_at_spawn"][0] in stdin_payload["message"]["content"]
        # Cleanup: the same tempdir must be gone once the call has returned.
        assert not os.path.isdir(captured_cwd["cwd"])

    @pytest.mark.asyncio
    async def test_text_only_request_sends_no_view_file_instruction(self, proxy_module):
        proc = _make_mock_streaming_proc([_result_line("pong")])
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
            text, _ = await proxy_module.call_geminicli_direct(
                system_prompt=None, messages=[{"role": "user", "content": "say pong"}],
                model="gemini-3.8-flash-medium", max_tokens=100,
            )
        assert text == "pong"
        stdin_payload = json.loads(proc.stdin.write.call_args.args[0])
        assert "view_file" not in stdin_payload["message"]["content"]

    @pytest.mark.asyncio
    async def test_image_tempdir_is_still_cleaned_up_on_a_non_zero_exit(self, proxy_module):
        proc = _make_mock_streaming_proc([], stderr_bytes=b"boom", returncode=1)
        captured = {}

        async def _spawn(*args, **kwargs):
            captured["cwd"] = kwargs["cwd"]
            return proc

        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", _spawn):
            with pytest.raises(HTTPException) as exc:
                await proxy_module.call_geminicli_direct(
                    system_prompt=None, messages=[{"role": "user", "content": _image_url_part()}],
                    model="gemini-3.8-flash-medium", max_tokens=100,
                )
        assert exc.value.status_code == 502
        assert not os.path.isdir(captured["cwd"])

    @pytest.mark.asyncio
    async def test_image_tempdir_is_still_cleaned_up_on_timeout(self, proxy_module):
        proc = _make_mock_streaming_proc([])
        proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
        captured = {}

        async def _spawn(*args, **kwargs):
            captured["cwd"] = kwargs["cwd"]
            return proc

        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module, "AGY_TIMEOUT", 0.01), \
             patch.object(proxy_module, "_kill_process_tree", AsyncMock()), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", _spawn):
            with pytest.raises(HTTPException) as exc:
                await proxy_module.call_geminicli_direct(
                    system_prompt=None, messages=[{"role": "user", "content": _image_url_part()}],
                    model="gemini-3.8-flash-medium", max_tokens=100,
                )
        assert exc.value.status_code == 504
        assert not os.path.isdir(captured["cwd"])


class TestCallGeminicliStreamingWithVision:
    @pytest.mark.asyncio
    async def test_image_request_streams_and_cleans_up_after(self, proxy_module):
        lines = [
            json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "red"}}).encode() + b"\n",
            _result_line("red"),
        ]
        proc = _make_mock_streaming_proc(lines)
        captured = {}

        async def _spawn(*args, **kwargs):
            captured["cwd"] = kwargs["cwd"]
            captured["files_at_spawn"] = os.listdir(kwargs["cwd"])
            return proc

        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", _spawn):
            chunks = [
                c async for c in proxy_module.call_geminicli_streaming(
                    system_prompt=None, messages=[{"role": "user", "content": _image_url_part()}],
                    model="gemini-3.8-flash-medium", max_tokens=100,
                )
            ]

        combined = "".join(chunks)
        assert "red" in combined
        assert "data: [DONE]" in combined
        assert len(captured["files_at_spawn"]) == 1
        # The generator has fully finished (exhausted the async for above) -- the tempdir it used
        # must be gone by now, same guarantee the non-streaming path has.
        assert not os.path.isdir(captured["cwd"])

    @pytest.mark.asyncio
    async def test_text_only_streaming_unaffected(self, proxy_module):
        lines = [
            json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "pong"}}).encode() + b"\n",
            _result_line("pong"),
        ]
        proc = _make_mock_streaming_proc(lines)
        with patch.object(proxy_module, "_agy_path", lambda: "/fake/path/agy"), \
             patch.object(proxy_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
            chunks = [
                c async for c in proxy_module.call_geminicli_streaming(
                    system_prompt=None, messages=[{"role": "user", "content": "say pong"}],
                    model="gemini-3.8-flash-medium", max_tokens=100,
                )
            ]
        combined = "".join(chunks)
        assert "pong" in combined
        stdin_payload = json.loads(proc.stdin.write.call_args.args[0])
        assert "view_file" not in stdin_payload["message"]["content"]


class TestImageCountLogging:
    def test_counts_images_across_all_messages(self, proxy_module):
        messages = [
            {"role": "user", "content": _image_url_part()},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": _image_url_part() + [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_REAL_PNG_B64}"}}]},
        ]
        assert proxy_module._agy_image_count(messages) == 3

    def test_zero_for_text_only(self, proxy_module):
        assert proxy_module._agy_image_count([{"role": "user", "content": "hi"}]) == 0
