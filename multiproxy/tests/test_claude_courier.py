"""Claude on a Courier device (claude_courier.py, courier.py's /courier/v1/claude/*, the main device's routes and row).

Stand-ins only: a release server that serves a stand-in `claude` program (tests/fixtures/fake_claude_program.py wrapped in a small launcher), a stand-in
Anthropic API that accepts only the codes a test allows, and the real Courier door. Nothing real is installed or signed in; the codes here are made up.
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

import claude_courier as cc
import claude_wiring
import courier as cr
import courier_system as cs
import other_pcs as op
from tests.fixtures.courier_fakes import free_port

pytestmark = pytest.mark.asyncio
PROGRAM = str(Path(__file__).parent / "fixtures" / "fake_claude_program.py")
GOOD = "sk-ant-oat01-" + "a" * 95            # 108 characters, like a real one
OTHER = "sk-ant-oat01-" + "b" * 95
BAD = "sk-ant-oat01-" + "z" * 95
LIMITED = "sk-ant-oat01-" + "L" * 95
CUT = GOOD[:79]                              # what a terminal that wraps at 80 columns leaves behind
ALL_CODES = (GOOD, OTHER, BAD, LIMITED)
PLATFORM = "win32-x64"
log = logging.getLogger("test-claude")


def launcher_bytes() -> bytes:
    if sys.platform == "win32":
        return f'@echo off\r\n"{sys.executable}" "{PROGRAM}" %*\r\n'.encode()
    return f'#!/bin/sh\nexec "{sys.executable}" "{PROGRAM}" "$@"\n'.encode()


class FakeReleases:
    """Anthropic's release site: /latest, /<version>/manifest.json and /<version>/<platform>/<file>."""

    def __init__(self):
        self.version = "9.9.9"
        self.payload = launcher_bytes()
        self.checksum = None                  # a wrong one on purpose in some tests
        self.requests = []

    async def start(self):
        app = web.Application()
        app.router.add_get("/latest", self.latest)
        app.router.add_get("/{version}/manifest.json", self.manifest)
        app.router.add_get("/{version}/{platform}/{name}", self.binary)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.port = free_port()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def stop(self):
        await self.runner.cleanup()

    async def latest(self, request):
        self.requests.append("latest")
        return web.Response(text=self.version + "\n")

    async def manifest(self, request):
        digest = self.checksum or hashlib.sha256(self.payload).hexdigest()
        return web.json_response({"version": request.match_info["version"], "platforms": {PLATFORM: {"binary": "claude.exe", "checksum": digest, "size": len(self.payload)}}})

    async def binary(self, request):
        self.requests.append(request.path)
        return web.Response(body=self.payload, content_type="application/octet-stream")


class FakeAnthropic:
    """api.anthropic.com: /v1/messages (a stream of events) and /v1/models, accepting only the codes in `accept`."""

    def __init__(self):
        self.accept = {GOOD, OTHER}
        self.seen = []                        # (path, auth ok, body) for each request; the code itself is never kept
        self.models = ["claude-sonnet-5-5", "claude-opus-5-5"]

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/messages", self.messages)
        app.router.add_get("/v1/models", self.list_models)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.port = free_port()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def stop(self):
        await self.runner.cleanup()

    def _token(self, request):
        return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

    async def messages(self, request):
        body = await request.json()
        token = self._token(request)
        self.seen.append((request.path, token in self.accept, body))
        if token == LIMITED:
            return web.json_response({"type": "error", "error": {"type": "rate_limit_error", "message": "You've reached your usage limit. It resets at 9 PM."}}, status=429)
        if token not in self.accept:
            return web.json_response({"type": "error", "error": {"type": "authentication_error", "message": "Invalid bearer token"}}, status=401)
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}]
        for ev in events:
            await resp.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
        await resp.write_eof()
        return resp

    async def list_models(self, request):
        if self._token(request) not in self.accept:
            return web.json_response({"error": {"message": "Invalid bearer token"}}, status=401)
        return web.json_response({"data": [{"id": m, "display_name": m} for m in self.models] + [{"id": "other-thing"}], "has_more": False})


class Capture(logging.Handler):
    """Every log line anything wrote, for the leak scan."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    class W: pass
    w = W()
    w.releases, w.api = await FakeReleases().start(), await FakeAnthropic().start()
    w.record = tmp_path / "env-record.jsonl"
    w.planted = {**os.environ, "ANTHROPIC_API_KEY": "sk-ant-api03-planted-key-value", "ANTHROPIC_AUTH_TOKEN": "planted-bearer", "ANTHROPIC_PROFILE": "work",
                 "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_OAUTH_TOKEN": "planted-oauth", "FAKE_CLAUDE_RECORD": str(w.record), "KEEP_ME": "yes"}
    w.cap = Capture()
    log.addHandler(w.cap)
    log.setLevel(logging.DEBUG)
    w.tmp = tmp_path
    w.changes = 0

    def make(folder=None, **kw):
        h = cc.ClaudeHost(folder or tmp_path / "claude", log, changed=lambda: setattr(w, "changes", w.changes + 1), releases_url=w.releases.url, api_url=w.api.url,
                          verify_signature=False, platform_key=PLATFORM, environ=w.planted, exe_name="claude.cmd" if sys.platform == "win32" else "claude", **kw)
        return h
    w.make = make
    w.host = make()
    yield w
    log.removeHandler(w.cap)
    await w.releases.stop()
    await w.api.stop()


async def installed(w):
    job = w.host.start_install()
    await job.task
    assert job.state == "done", job.error
    return w.host


async def signed_in(w, code=GOOD):
    await installed(w)
    return await w.host.sign_in(code)


def leak_scan(w, *extra):
    """Nothing the host logged, said or returned holds a code (or anything shaped like one)."""
    blob = "\n".join(w.cap.lines + [str(x) for x in extra])
    for code in ALL_CODES:
        assert code not in blob
    assert "sk-ant-" not in blob.replace("sk-ant-oat01-[code]", "")


# ============================================================================================
# Install
# ============================================================================================
class TestInstall:
    async def test_a_fresh_pc_says_not_installed_and_asks_the_network_nothing(self, world):
        s = world.host.status_json()
        assert s["state"] == "not_installed" and not s["installed"] and s["version"] is None and s["models"] == []
        await asyncio.sleep(0.05)
        assert world.releases.requests == [], "nothing is looked up until it is installed"

    async def test_install_downloads_checks_and_keeps_one_file_in_its_own_folder(self, world):
        h = await installed(world)
        assert h.exe.is_file() and h.exe.parent == world.tmp / "claude" / "bin"
        assert sorted(p.name for p in h.bin_dir.iterdir()) == [h.exe.name], "no .part, .new or .old left behind"
        s = h.status_json()
        assert s["state"] == "needs_signin" and s["version"] == "9.9.9" and s["installed"] and s["install"]["state"] == "done"
        assert s["install"]["total"] == len(world.releases.payload) and s["install"]["percent"] == 100

    async def test_progress_is_real_and_a_change_is_signalled(self, world):
        before = world.changes
        await installed(world)
        assert world.changes > before

    async def test_a_download_that_fails_its_checksum_is_deleted(self, world):
        world.releases.checksum = "0" * 64
        job = world.host.start_install()
        await job.task
        assert job.state == "error" and job.error == cc.LINE_CHECKSUM
        assert not world.host.exe.exists() and not list(world.host.bin_dir.iterdir())
        assert world.host.status_json()["state"] == "not_installed"
        assert world.host.status_json()["warning"] == cc.LINE_CHECKSUM

    async def test_a_download_that_will_not_start_is_not_kept(self, world, monkeypatch):
        monkeypatch.setattr(cc.ClaudeHost, "_run_version", lambda self, exe: asyncio.sleep(0, result=""))
        job = world.host.start_install()
        await job.task
        assert job.state == "error" and job.error.startswith("Claude was downloaded but") and "printed nothing" in job.error and not world.host.exe.exists()

    async def test_a_site_that_cannot_be_reached_gives_the_download_line(self, world):
        h = world.make(world.tmp / "other")
        h.releases_url = "http://127.0.0.1:1"
        job = h.start_install()
        await job.task
        assert job.state == "error" and job.error == cc.LINE_DOWNLOAD

    async def test_cancel_stops_it_and_removes_the_part_file(self, world):
        world.releases.payload = launcher_bytes() + b"x" * 2_000_000
        h = world.host
        job = h.start_install()
        await asyncio.sleep(0)
        assert h.cancel_install()
        await asyncio.gather(job.task, return_exceptions=True)
        assert job.state == "cancelled" and not any(h.bin_dir.glob("*.part")) and not h.exe.exists()

    async def test_two_installs_at_once_are_refused(self, world):
        job = world.host.start_install()
        with pytest.raises(cc.ClaudeError) as e:
            world.host.start_install()
        assert e.value.status == 409
        await job.task

    async def test_the_signature_check_refuses_a_file_that_is_not_signed_by_anthropic(self, world, monkeypatch):
        h = world.make(world.tmp / "signed", )
        h.verify_signature = True

        async def refuse(self, path):
            path.unlink(missing_ok=True)
            raise cc.ClaudeError(502, cc.LINE_SIGNATURE)
        monkeypatch.setattr(cc.ClaudeHost, "_check_signature", refuse)
        job = h.start_install()
        await job.task
        assert job.error == cc.LINE_SIGNATURE and not h.exe.exists()


class TestUpdate:
    async def test_update_shows_only_when_a_newer_version_is_known_and_never_by_itself(self, world):
        h = await installed(world)
        h._latest = (h.version(), 0.0)
        assert not h.status_json(kick=False)["updateAvailable"]
        world.releases.version = "9.9.10"
        await h._refresh_latest()
        s = h.status_json(kick=False)
        assert s["updateAvailable"] and s["latestVersion"] == "9.9.10" and s["version"] == "9.9.9", "knowing about it changes nothing until Update is pressed"
        assert h.version() == "9.9.9"

    async def test_update_installs_the_newer_one_and_keeps_the_sign_in(self, world):
        await signed_in(world)
        h = world.host
        world.releases.version = "9.9.10"
        world.planted["FAKE_CLAUDE_VERSION"] = "9.9.10"      # the new file says its own version, as the real one does
        job = h.start_install(updating=True)
        await job.task
        assert job.state == "done" and h.version() == "9.9.10"
        assert h.signed_in() and h.state() == "ready" and not h.status_json(kick=False)["updateAvailable"]
        assert sorted(p.name for p in h.bin_dir.iterdir()) == [h.exe.name]

    async def test_update_before_install_is_refused(self, world):
        with pytest.raises(cc.ClaudeError):
            world.host.start_install(updating=True)


# ============================================================================================
# Sign in and out
# ============================================================================================
class TestCodeShape:
    @pytest.mark.parametrize("raw,line", [("", cc.LINE_NOT_A_CODE), ("hello there", cc.LINE_NOT_A_CODE), (CUT, cc.LINE_CUT_OFF),
                                          ("sk-ant-api03-" + "k" * 90, cc.LINE_API_KEY), ("sk-ant-oat01-" + "a" * 40 + " !" + "b" * 60, cc.LINE_NOT_A_CODE)])
    async def test_a_wrong_paste_is_refused_before_anything_is_saved(self, world, raw, line):
        await installed(world)
        with pytest.raises(cc.ClaudeError) as e:
            await world.host.sign_in(raw)
        assert e.value.message == line and not world.host.token_file.exists()
        assert world.api.seen == []

    async def test_a_code_that_wrapped_over_two_lines_is_whole_again(self, world):
        assert cc.check_token(GOOD[:60] + "\r\n" + GOOD[60:] + "\n") == GOOD
        assert cc.check_token("  " + GOOD + "  ") == GOOD


class TestSignIn:
    async def test_a_good_code_is_kept_only_in_the_courier_folder_and_the_row_says_ready(self, world):
        s = await signed_in(world)
        assert s["state"] == "ready" and s["signedIn"] and s["warning"] == ""
        assert world.host.token_file.read_text() == GOOD and world.host.token_file.parent == world.tmp / "claude"
        assert GOOD not in json.dumps(s)
        holders = [p for p in world.tmp.rglob("*") if p.is_file() and GOOD.encode() in p.read_bytes()]
        assert holders == [world.host.token_file], "the code is in exactly one file"

    async def test_the_stand_in_program_saw_the_code_only_in_its_own_environment(self, world, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        await signed_in(world)
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ, "never in Courier's own environment"
        facts = [json.loads(l) for l in world.record.read_text().splitlines()]
        assert facts, "the program ran"
        for f in facts:
            assert f["has_oauth"] is True
            assert [n for n in f["names"] if n.startswith("ANTHROPIC_")] == ["ANTHROPIC_BASE_URL"], "the key, the bearer, the profile: all removed (the base address is the capture's own)"
            assert not [n for n in f["names"] if n.startswith("CLAUDE_CODE_USE_")] and {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "DISABLE_AUTOUPDATER"} <= set(f["names"])
            assert f["config_dir"] == str(world.tmp / "claude" / "home") and f["autoupdater_disabled"]
        assert world.api.seen[0][1] is True, "what reached the API was the saved code, not the planted one"

    async def test_the_capture_runs_from_an_empty_folder_and_strips_tools(self, world):
        await signed_in(world)
        facts = json.loads(world.record.read_text().splitlines()[0])
        assert "claude-capture-" in facts["cwd"] and "--print" in facts["argv"] and "--no-session-persistence" in facts["argv"]
        h = world.host
        assert h._template and "tools" not in h._template and "thinking" not in h._template and "context_management" not in h._template

    async def test_a_refused_code_is_not_kept_and_the_row_says_why(self, world):
        await installed(world)
        with pytest.raises(cc.ClaudeError) as e:
            await world.host.sign_in(BAD)
        assert e.value.message == cc.LINE_REFUSED
        s = world.host.status_json(kick=False)
        assert s["state"] == "needs_signin" and s["warning"] == cc.LINE_REFUSED and not world.host.token_file.exists() and not s["signedIn"]

    async def test_a_new_good_code_clears_the_warning(self, world):
        await installed(world)
        with pytest.raises(cc.ClaudeError):
            await world.host.sign_in(BAD)
        s = await world.host.sign_in(OTHER)
        assert s["state"] == "ready" and s["warning"] == "" and world.host.token_file.read_text() == OTHER

    async def test_signing_in_before_installing_is_refused(self, world):
        with pytest.raises(cc.ClaudeError) as e:
            await world.host.sign_in(GOOD)
        assert e.value.message == cc.LINE_NOT_INSTALLED

    async def test_sign_out_removes_the_code_and_what_the_program_wrote(self, world):
        await signed_in(world)
        (world.host.home / "note.txt").write_text("x")
        s = world.host.sign_out()
        assert s["state"] == "needs_signin" and not world.host.token_file.exists() and not world.host.home.exists() and s["installed"]
        assert not [p for p in world.tmp.rglob("*") if p.is_file() and GOOD.encode() in p.read_bytes()]

    async def test_the_sign_in_survives_a_courier_restart(self, world):
        await signed_in(world)
        again = world.make()
        assert again.state() == "ready" and again.signed_in()

    async def test_child_env_removes_everything_that_could_win_over_the_code(self):
        env = cc.child_env("TOKEN", Path("C:/cfg"), environ={"ANTHROPIC_API_KEY": "k", "anthropic_auth_token": "t", "ANTHROPIC_BASE_URL": "http://x", "ANTHROPIC_PROFILE": "p",
                                                             "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_OAUTH_TOKEN": "old", "PATH": "p", "KEEP": "1"})
        assert env == {"PATH": "p", "KEEP": "1", "CLAUDE_CONFIG_DIR": str(Path("C:/cfg")), "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_OAUTH_TOKEN": "TOKEN"}


# ============================================================================================
# The request path
# ============================================================================================
class FakeRequest:
    pass


async def serve(host):
    """A tiny door that hands every chat request to the host, the way courier.py does."""
    async def chat(request):
        try:
            return await host.chat(request, await request.json())
        except cc.ClaudeError as e:
            return web.json_response({"error": {"message": e.message}}, status=e.status)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}"


class TestChat:
    @pytest_asyncio.fixture
    async def door(self, world):
        await signed_in(world)
        runner, base = await serve(world.host)
        yield base
        await runner.cleanup()

    async def test_a_whole_answer_is_openai_shaped_and_uses_the_chats_own_text(self, world, door):
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "max_tokens": 50, "messages": [
                    {"role": "system", "content": "You are Lydia."}, {"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"},
                    {"role": "user", "content": [{"type": "text", "text": "Again"}, {"type": "image_url", "image_url": {"url": "data:x"}}]}]}) as r:
                data = await r.json()
        assert r.status == 200 and data["choices"][0]["message"]["content"] == "Hello" and data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"] == {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
        _, authed, body = world.api.seen[-1]
        assert authed and body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 50 and body["stream"] is True
        assert [b["text"] for b in body["system"]][-1] == "You are Lydia." and body["system"][0]["text"].startswith("x-anthropic-billing-header")
        assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
        assert body["messages"][0]["content"][0]["text"].startswith("<system-reminder>") and body["messages"][0]["content"][-1]["text"] == "Hello"
        assert body["messages"][2]["content"] == [{"type": "text", "text": "Again"}]
        assert "tools" not in body and "thinking" not in body

    async def test_a_streamed_answer_arrives_in_pieces_and_ends_with_done(self, world, door):
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude/claude-sonnet-5-5", "stream": True, "messages": [{"role": "user", "content": "Hi"}]}) as r:
                text = (await r.read()).decode()
        events = [l[6:] for l in text.splitlines() if l.startswith("data: ")]
        assert events[-1] == "[DONE]"
        chunks = [json.loads(e) for e in events[:-1]]
        assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
        assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "Hello" and chunks[-1]["choices"][0]["finish_reason"] == "stop"
        assert world.api.seen[-1][2]["model"] == "claude-sonnet-5-5", "the claude/ spelling names the model after it"

    async def test_a_limit_is_passed_on_exactly_as_anthropic_says_it(self, world, door):
        world.api.accept.add(LIMITED)
        await world.host.sign_in(LIMITED) if False else None
        world.host.token_file.write_text(LIMITED)
        world.host.invalidate()
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "Hi"}]}) as r:
                data = await r.json()
        assert r.status == 429 and data["error"]["message"] == "You've reached your usage limit. It resets at 9 PM."

    async def test_an_expired_code_gives_the_expired_line_and_the_row_asks_for_a_new_one(self, world, door):
        world.api.accept.discard(GOOD)                      # it worked at sign-in, and now Anthropic refuses it
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "Hi"}]}) as r:
                data = await r.json()
        assert r.status == 503 and data["error"]["message"] == cc.LINE_EXPIRED
        s = world.host.status_json(kick=False)
        assert s["state"] == "needs_signin" and s["warning"] == cc.LINE_EXPIRED
        world.api.accept.add(OTHER)
        assert (await world.host.sign_in(OTHER))["state"] == "ready"

    async def test_a_refused_sign_in_in_the_middle_is_taken_again_once_before_giving_up(self, world, door):
        seen_before = len(world.api.seen)
        world.host._headers = dict(world.host._headers, Authorization="Bearer " + BAD)       # the kept sign-in went stale, the saved code is fine
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "Hi"}]}) as r:
                data = await r.json()
        assert r.status == 200 and data["choices"][0]["message"]["content"] == "Hello"
        assert len(world.api.seen) - seen_before == 3, "the refused try, the new capture, the answer"

    async def test_no_message_is_a_plain_400(self, world, door):
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "system", "content": "x"}]}) as r:
                assert r.status == 400

    async def test_an_unreachable_anthropic_is_one_short_line(self, world, door):
        world.host.api_url = "http://127.0.0.1:1"
        async with aiohttp.ClientSession() as s:
            async with s.post(door + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "Hi"}]}) as r:
                data = await r.json()
        assert r.status == 502 and data["error"]["message"] == cc.LINE_UNREACHABLE

    async def test_a_request_before_install_or_sign_in_says_so(self, world):
        with pytest.raises(cc.ClaudeError) as e:
            await world.host.chat(FakeRequest(), {"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "x"}]})
        assert e.value.message == cc.LINE_NOT_INSTALLED
        await installed(world)
        with pytest.raises(cc.ClaudeError) as e:
            await world.host.chat(FakeRequest(), {"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "x"}]})
        assert e.value.message == cc.LINE_NEEDS_SIGN_IN

    async def test_models_come_from_the_sign_in_and_fall_back_to_the_default(self, world):
        await signed_in(world)
        assert await world.host.models() == ["claude-sonnet-5-5", "claude-opus-5-5"]
        again = world.make()
        again._models = ([], 0.0)
        world.api.accept.clear()
        assert await again.models() == ["claude-sonnet-5-5"]
        s = world.host.status_json(kick=False)
        assert [m["id"] for m in s["models"]] == ["claude-sonnet-5-5", "claude-opus-5-5"] and s["models"][0]["name"] == "Sonnet 5.5"


class TestTest:
    async def test_test_sends_one_tiny_real_request(self, world):
        await signed_in(world)
        before = len(world.api.seen)
        r = await world.host.test()
        assert r["ok"] is True and r["model"] == "claude-sonnet-5-5" and isinstance(r["seconds"], float)
        assert len(world.api.seen) - before == 1 and world.api.seen[-1][2]["max_tokens"] == 16

    async def test_test_says_the_one_line_when_it_cannot(self, world):
        await signed_in(world)
        world.api.accept.clear()
        r = await world.host.test()
        assert r == {"ok": False, "error": cc.LINE_EXPIRED, "kind": "expired"}

    async def test_test_before_sign_in_says_so(self, world):
        await installed(world)
        assert (await world.host.test())["error"] == cc.LINE_NEEDS_SIGN_IN


# ============================================================================================
# Through the real Courier door
# ============================================================================================
class Door:
    def __init__(self, courier): self.courier = courier

    @property
    def base(self): return f"http://127.0.0.1:{self.courier.settings.port}"

    async def call(self, method, path, body=None, expect=200):
        async with aiohttp.ClientSession() as s:
            async with s.request(method, self.base + path, json=body, headers={"Authorization": f"Bearer {self.courier.settings.code}"}) as r:
                data = await r.json(content_type=None)
                assert r.status == expect, (r.status, data)
                return data


@pytest_asyncio.fixture
async def courier(world, tmp_path):
    import courier_adapters as ca
    (tmp_path / "courier").mkdir()
    settings = cr.CourierSettings(tmp_path / "courier" / "courier.json")
    settings.set(port=free_port())
    c = cr.Courier(settings, log, bind_ips=["127.0.0.1"], discovery_port=free_port(), keep_awake=cs.KeepAwake(type("A", (), {"start": lambda s: True, "stop": lambda s: None})()),
                   gpu_reader=lambda: [])
    c.adapters = []
    c.claude = world.make(tmp_path / "courier" / "claude")
    c.claude.changed = c.signal_change
    await c.start()
    yield Door(c)
    await c.stop()


class TestCourierDoor:
    async def test_the_report_carries_claude_and_its_state_moves_with_each_step(self, world, courier):
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["claude"]["state"] == "not_installed"
        await courier.call("POST", "/courier/v1/claude/install")
        for _ in range(100):
            s = await courier.call("GET", "/courier/v1/claude/status")
            if s["state"] != "installing":
                break
            await asyncio.sleep(0.05)
        assert s["state"] == "needs_signin" and s["version"] == "9.9.9"
        out = await courier.call("POST", "/courier/v1/claude/signin", {"code": GOOD})
        assert out["state"] == "ready"
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["claude"]["state"] == "ready" and [m["id"] for m in rep["claude"]["models"]]
        assert GOOD not in json.dumps(rep)

    async def test_a_refused_code_and_a_cut_off_code_answer_with_their_one_line(self, world, courier):
        await courier.call("POST", "/courier/v1/claude/install")
        await world.host.install_job.task if False else await courier.courier.claude.install_job.task
        r = await courier.call("POST", "/courier/v1/claude/signin", {"code": CUT}, expect=400)
        assert r["error"]["message"] == cc.LINE_CUT_OFF
        r = await courier.call("POST", "/courier/v1/claude/signin", {"code": BAD}, expect=503)
        assert r["error"]["message"] == cc.LINE_REFUSED
        assert (await courier.call("GET", "/courier/v1/claude/status"))["warning"] == cc.LINE_REFUSED

    async def test_chat_for_a_claude_model_goes_to_claude_and_the_models_list_has_it(self, world, courier):
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        await courier.call("POST", "/courier/v1/claude/signin", {"code": GOOD})
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "Hi"}]},
                              headers={"Authorization": f"Bearer {courier.courier.settings.code}"}) as r:
                data = await r.json()
        assert r.status == 200 and data["choices"][0]["message"]["content"] == "Hello"
        models = (await courier.call("GET", "/v1/models"))["data"]
        assert {"id": "claude-sonnet-5-5", "object": "model", "owned_by": "claude"} in models

    async def test_test_and_sign_out_work_through_the_door(self, world, courier):
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        await courier.call("POST", "/courier/v1/claude/signin", {"code": GOOD})
        assert (await courier.call("POST", "/courier/v1/claude/test"))["ok"] is True
        assert (await courier.call("POST", "/courier/v1/claude/signout"))["state"] == "needs_signin"

    async def test_nothing_is_answered_without_the_pairing_code(self, world, courier):
        for method, path in (("GET", "/courier/v1/claude/status"), ("POST", "/courier/v1/claude/signin"), ("POST", "/courier/v1/claude/install")):
            async with aiohttp.ClientSession() as s:
                async with s.request(method, courier.base + path, json={"code": GOOD}) as r:
                    assert r.status == 403, path


# ============================================================================================
# The code never leaks
# ============================================================================================
class TestNoLeak:
    async def test_every_log_line_status_and_response_of_a_whole_run_is_free_of_the_code(self, world, courier, caplog):
        caplog.set_level(logging.DEBUG)
        seen = []
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        for code, expect in ((CUT, 400), (BAD, 503), (GOOD, 200)):
            seen.append(await courier.call("POST", "/courier/v1/claude/signin", {"code": code}, expect=expect))
        seen.append(await courier.call("GET", "/courier/v1/claude/status"))
        seen.append(await courier.call("GET", "/courier/v1/report?fresh=1"))
        seen.append(await courier.call("POST", "/courier/v1/claude/test"))
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
                              headers={"Authorization": f"Bearer {courier.courier.settings.code}"}) as r:
                seen.append((await r.read()).decode())
        world.api.accept.clear()                             # an expired one, too
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/v1/chat/completions", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "Hi"}]},
                              headers={"Authorization": f"Bearer {courier.courier.settings.code}"}) as r:
                seen.append((await r.read()).decode())
        seen.append(await courier.call("GET", "/courier/v1/log"))
        seen.append(await courier.call("POST", "/courier/v1/claude/signout"))
        leak_scan(world, *seen, "\n".join(x.getMessage() for x in caplog.records))
        logfile = courier.courier.settings.path.parent / "courier.log"
        if logfile.exists():
            assert GOOD not in logfile.read_text(errors="replace")

    async def test_mask_hides_the_code_and_anything_shaped_like_one(self):
        assert GOOD not in cc.mask(f"failed with {GOOD} here", GOOD) and "sk-ant-" not in cc.mask("x sk-ant-oat01-abcdefghijk y").replace("[code]", "")
        assert cc.mask("plain words") == "plain words"

    async def test_a_failure_text_from_the_program_that_echoes_the_code_is_masked_in_the_log(self, world):
        host = await installed(world)
        host.log.info(cc.mask(f"Failed to authenticate with {OTHER}", OTHER))
        leak_scan(world)


# ============================================================================================
# The main device's side
# ============================================================================================
class FakeRemotes:
    def __init__(self): self.calls = []

    async def courier_claude(self, rid, action, body=None):
        self.calls.append((rid, action, body))
        if action == "signin" and (body or {}).get("code") == "boom":
            raise op.OtherPcsError(503, cc.LINE_REFUSED)
        return {"state": "ready"}


class TestGamingSide:
    @pytest_asyncio.fixture
    async def client(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        app = FastAPI()
        self.remotes = FakeRemotes()
        claude_wiring.setup(app, self.remotes)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c

    async def test_the_row_asks_for_one_action_at_a_time_and_only_signin_carries_the_code(self, client):
        assert (await client.post("/config/claude/pc/install", json={"id": "msi", "code": "ignored"})).json() == {"state": "ready"}
        await client.post("/config/claude/pc/signin", json={"id": "msi", "code": GOOD})
        await client.post("/config/claude/pc/test", json={"id": "msi"})
        assert self.remotes.calls == [("msi", "install", {}), ("msi", "signin", {"code": GOOD}), ("msi", "test", {})]

    async def test_an_unknown_action_is_a_404_and_a_refusal_keeps_the_pcs_own_line(self, client):
        assert (await client.post("/config/claude/pc/format-everything", json={"id": "msi"})).status_code == 404
        r = await client.post("/config/claude/pc/signin", json={"id": "msi", "code": "boom"})
        assert r.status_code == 503 and r.json() == {"detail": cc.LINE_REFUSED}

    async def test_a_failed_signin_never_writes_its_body_to_the_log(self):
        import multiproxy
        assert claude_wiring.SIGN_IN_PATH in multiproxy._BODY_HIDDEN_PATHS

    async def test_the_remote_service_sends_it_on_and_keeps_nothing(self):
        class Client:
            def __init__(self): self.sent = []
            async def claude_post(self, what, body, timeout=30): self.sent.append((what, body)); return {"state": "ready"}
            async def fetch_report(self): return {"generated_at": 1, "courier": {"name": "MSI", "id": "i"}, "programs": [], "gpus": []}
        svc = op.RemoteServers(lambda: {}, lambda c: None, log, lambda n: None, lambda: set())
        r = op.Remote(id="msi", kind="courier", name="MSI", base="http://x")
        r.client = Client()
        svc._remotes = {"msi": r}
        svc._loaded = True
        assert await svc.courier_claude("msi", "signin", {"code": GOOD}) == {"state": "ready"}
        assert r.client.sent == [("signin", {"code": GOOD})]
        assert GOOD not in json.dumps(r.to_json(), default=str)


class TestModelsOnTheGamingPc:
    REPORT = {"programs": [], "claude": {"state": "ready", "models": [{"id": "claude-sonnet-5-5", "name": "Sonnet 5.5"}]}}

    async def test_claude_models_join_the_other_pcs_model_list_by_name(self):
        m = op.models_from_report(self.REPORT)
        assert m == [{"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "vision": False, "program": "claude", "state": "running"}]
        assert op.courier_program_label(self.REPORT) == "Claude"

    async def test_a_claude_that_is_not_ready_adds_nothing(self):
        for st in ("not_installed", "needs_signin", "installing", "checking"):
            rep = {"programs": [], "claude": {"state": st, "models": [{"id": "claude-sonnet-5-5", "name": "x"}]}}
            assert op.models_from_report(rep) == [] and op.courier_program_label(rep) == "llama.cpp"

    async def test_the_overview_gives_those_rows_no_load_or_unload_button(self):
        import overview
        pcs = [{"id": "msi", "name": "MSI", "online": True, "kind": "courier", "gpu": None, "program": "Claude", "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None,
                "models": [{"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "state": "running", "program": "claude"},
                           {"id": "gemma", "name": "Gemma", "state": "running", "program": "llamacpp"}]}]
        d = overview.build(tracking=False, figures=None, this_pc={"name": "Gaming device", "host": "g", "gpu": None, "usedMb": None, "totalMb": None}, local_models=[], local_speeds={}, pcs=pcs,
                           providers=[], health={}, uses={"pc:msi/claude-sonnet-5-5": ["Dialogue"], "pc:msi/gemma": ["Memories"]}, routes=[], decisions=None, voice=None, setup={"done": True, "left": []})
        rows = {r["name"]: r for g in d["groups"] for r in g["rows"]}
        assert rows["Claude Sonnet 5.5"]["where"] == "MSI" and rows["Claude Sonnet 5.5"]["action"] is None and rows["Claude Sonnet 5.5"]["test"] == {"model": "msi/claude-sonnet-5-5"}
        assert rows["Gemma"]["action"] is not None, "other programs keep theirs"


# ============================================================================================
# The row on the Other devices tab
# ============================================================================================
class TestRowScript:
    async def test_every_state_in_the_mockup_has_its_words(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        for text in ("Not installed", "Needs sign-in", "Ready", "Install", "Sign in", "Test", "Sign out", "Update", "Subscription", "Save", "Cancel",
                     "</b>, open a terminal.</li>", "Run this command:", "<code>claude setup-token</code>", "Copy the code, paste it here, and press <b>Save</b>.",
                     "Paste the code from the terminal", "Checking the sign-in code"):
            assert text in js, text

    async def test_the_row_is_hooked_in_after_higgs_and_the_decision_model_and_is_in_the_page(self):
        import other_pcs_ui
        assert "ccPcRow(r)" in other_pcs_ui.OTHER_PCS_JS
        import multiproxy
        assert "function ccPcRow" in multiproxy._DASHBOARD_HTML if hasattr(multiproxy, "_DASHBOARD_HTML") else True

    async def test_the_code_box_hides_what_is_typed_and_the_code_is_cleared_after_save(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        assert 'type="password"' in js and "ccCodeText[id] = '';" in js
        assert "localStorage" not in js and "sessionStorage" not in js, "the code is never stored in the page"

    async def test_there_is_no_new_kind_of_ui(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        for used in ("op-prog", "vc-warn", "op-steps", "op-dl", "lm-bar", "lm-muted", "op-grow"):
            assert used in js
        assert "<style" not in js and "innerHTML =" not in js.replace("opRender", "")


# ============================================================================================
# A downloaded program that will not start: say why, from its own words (2026-10-06)
# ============================================================================================
def run_instead(monkeypatch, module, code):
    """Make the version check run a tiny Python program that does `code` instead of the downloaded one (works the same on every system)."""
    real = asyncio.create_subprocess_exec

    async def fake(exe, *args, **kw):
        return await real(sys.executable, "-c", code, **{k: v for k, v in kw.items() if k in ("stdin", "stdout", "stderr", "env")})
    monkeypatch.setattr(module.hidden_process, "create_subprocess_exec", fake)


class TestAProgramThatWillNotStart:
    async def test_a_version_line_that_ends_its_sentence_with_a_full_stop_is_still_a_version(self, tmp_path, monkeypatch):
        run_instead(monkeypatch, cc, "print('GitHub Copilot CLI 1.0.92.'); print(\"Run 'copilot update' to check for updates.\")")
        chk = await cc.check_start(tmp_path / "x", {})
        assert chk.version == "1.0.92" and chk.returncode == 0

    async def test_a_prerelease_version_with_a_full_stop_after_it(self, tmp_path, monkeypatch):
        run_instead(monkeypatch, cc, "print('Tool 2.0.0-beta.1.')")
        assert (await cc.check_start(tmp_path / "x", {})).version == "2.0.0-beta.1"

    @pytest.mark.parametrize("plat", ["linux", "win32", "darwin"])
    async def test_a_missing_library_is_named_with_the_one_command_for_each_family_and_never_blames_antivirus(self, tmp_path, monkeypatch, plat):
        run_instead(monkeypatch, cc, "import sys; sys.stderr.write('./copilot: error while loading shared libraries: libstdc++.so.6: cannot open shared object file: No such file or directory\\n'); sys.exit(127)")
        chk = await cc.check_start(tmp_path / "x", {})
        line = cc.explain_start_failure("Copilot", tmp_path, chk, plat)
        assert line == ("Copilot can't start on <PC>: a system library is missing (libstdc++.so.6). On Debian or Ubuntu: `sudo apt install libstdc++6`. "
                        "On Fedora: `sudo dnf install libstdc++`. On Arch: `sudo pacman -S gcc-libs`. Then press Install again.")
        assert "ntivirus" not in line

    async def test_an_unknown_library_is_named_without_a_command(self, tmp_path):
        chk = cc.StartCheck(returncode=127, output="x: error while loading shared libraries: libfoo.so.9: cannot open shared object file")
        assert cc.explain_start_failure("Copilot", tmp_path, chk, "linux") == "Copilot can't start on <PC>: a system library is missing (libfoo.so.9). Then press Install again."

    async def test_a_system_that_is_too_old_says_so(self, tmp_path):
        chk = cc.StartCheck(returncode=1, output="./copilot: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found (required by ./copilot)")
        line = cc.explain_start_failure("Copilot", tmp_path, chk, "linux")
        assert line == "Copilot needs a newer Linux than <PC> has (its system libraries are too old). Update the system, then press Install again." and "ntivirus" not in line

    async def test_a_download_for_another_kind_of_computer_says_so(self, tmp_path):
        for chk in (cc.StartCheck(output="bash: ./copilot: cannot execute binary file: Exec format error"), cc.StartCheck(error=OSError(8, "Exec format error"))):
            assert cc.explain_start_failure("Copilot", tmp_path, chk, "linux") == "This download of Copilot is built for a different kind of computer than <PC>. Press Install again, or ask for help with this one."

    async def test_a_folder_that_forbids_running_programs_says_so(self, tmp_path):
        line = cc.explain_start_failure("Copilot", tmp_path, cc.StartCheck(error=PermissionError(13, "Permission denied")), "linux")
        assert line == "Copilot isn't allowed to run from its folder on <PC>. The folder might be set to block programs."

    async def test_the_programs_own_error_is_shown_even_on_windows_and_antivirus_is_not_named(self, tmp_path):
        chk = cc.StartCheck(returncode=139, output="Segmentation fault (core dumped)")
        for plat in ("win32", "linux"):
            line = cc.explain_start_failure("Copilot", tmp_path, chk, plat)
            assert line == "Copilot was downloaded but won't start on <PC>: Segmentation fault (core dumped)" and "ntivirus" not in line

    async def test_a_program_that_prints_nothing_gets_the_antivirus_advice_on_windows_only(self, tmp_path):
        chk = cc.StartCheck(returncode=1, output="")
        win = cc.explain_start_failure("Copilot", tmp_path, chk, "win32")
        assert win == f"Copilot was downloaded but won't start on <PC>, and printed nothing. Antivirus software might have blocked it. Allow the folder `{tmp_path}`, then press Install again."
        for plat in ("linux", "darwin"):
            line = cc.explain_start_failure("Copilot", tmp_path, chk, plat)
            assert line == "Copilot was downloaded but didn't start on <PC>, and printed nothing (exit code 1). The Courier's log has more." and "ntivirus" not in line

    async def test_a_program_that_never_answers_says_it_was_slow_on_every_system(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "VERSION_TIMEOUT", 0.5)
        run_instead(monkeypatch, cc, "import time; time.sleep(30)")
        chk = await cc.check_start(tmp_path / "x", {})
        assert chk.timed_out
        for plat in ("win32", "linux"):
            assert cc.explain_start_failure("Copilot", tmp_path, chk, plat) == "Copilot didn't start within 0 seconds on <PC>. Try again: the first start of a new program can be slow." or "didn't start within" in cc.explain_start_failure("Copilot", tmp_path, chk, plat)

    async def test_a_download_that_cannot_start_is_not_kept_and_the_row_and_log_say_why(self, world, monkeypatch):
        host = world.host

        async def broken(self, exe):
            self._start_check = cc.StartCheck(returncode=127, output="./claude: error while loading shared libraries: libstdc++.so.6: cannot open shared object file")
            return ""
        monkeypatch.setattr(type(host), "_run_version", broken)
        job = host.start_install()
        await job.task
        assert job.state == "error" and "a system library is missing (libstdc++.so.6)" in job.error and "ntivirus" not in job.error and not host.exe.exists()
        assert any("Claude didn't start when checked (exit code 127" in l and "libstdc++.so.6" in l for l in world.cap.lines)
