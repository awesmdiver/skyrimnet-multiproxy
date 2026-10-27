"""Copilot on a Courier device (copilot_courier.py, courier.py's /courier/v1/copilot/*, the main device's routes and row).

Stand-ins only: a release site that serves a stand-in `copilot` program (tests/fixtures/fake_copilot_program.py wrapped in a small launcher, packed as GitHub packs it),
and the real Courier door. Nothing real is installed or signed in; the token here is made up.
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

import copilot_courier as cp
import copilot_wiring
import courier as cr
import courier_system as cs
import other_pcs as op
from tests.fixtures.courier_fakes import free_port
from tests.fixtures.fake_copilot_program import FAKE_TOKEN

pytestmark = pytest.mark.asyncio
PROGRAM = str(Path(__file__).parent / "fixtures" / "fake_copilot_program.py")
WIN = sys.platform == "win32"
PLATFORM = "win32-x64" if WIN else "linux-x64"
ASSET = cp.asset_name(PLATFORM)
EXE_NAME = "copilot.cmd" if WIN else "copilot"
PLANTED_TOKEN = "ghp_" + "P" * 36                     # a token already in this device's own environment: it must never reach the program
log = logging.getLogger("test-copilot")


def launcher_bytes() -> bytes:
    if WIN:
        return f'@echo off\r\n"{sys.executable}" "{PROGRAM}" %*\r\n'.encode()
    return f'#!/bin/sh\nexec "{sys.executable}" "{PROGRAM}" "$@"\n'.encode()


def pack(members: dict) -> bytes:
    buf = io.BytesIO()
    if WIN:
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in members.items():
                z.writestr(name, data)
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o755
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeGitHub:
    """api.github.com's release list and github.com's download page, for the one release a test serves."""

    def __init__(self):
        self.version = "9.9.9"
        self.members = {EXE_NAME: launcher_bytes(), "resources/notes.txt": b"hello"}
        self.sums_wrong = False
        self.no_sums = False
        self.requests = []

    @property
    def payload(self) -> bytes:
        return pack(self.members)

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/releases/latest", self.latest)
        app.router.add_get("/dl/v{version}/{name}", self.download)
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
        assets = [{"name": ASSET, "size": len(self.payload)}]
        if not self.no_sums:
            assets.append({"name": "SHA256SUMS.txt", "size": 100})
        return web.json_response({"tag_name": "v" + self.version, "assets": assets})

    async def download(self, request):
        name = request.match_info["name"]
        self.requests.append(request.path)
        if name == "SHA256SUMS.txt":
            digest = "0" * 64 if self.sums_wrong else hashlib.sha256(self.payload).hexdigest()
            return web.Response(text=f"{digest}  {ASSET}\n{'1' * 64}  copilot-other.zip\n")
        return web.Response(body=self.payload, content_type="application/octet-stream")


class Capture(logging.Handler):
    """Every log line anything wrote, for the leak scan."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest_asyncio.fixture
async def world(tmp_path):
    class W: pass
    w = W()
    w.gh = await FakeGitHub().start()
    w.record = tmp_path / "env-record.jsonl"
    w.approve = tmp_path / "approve.flag"
    w.planted = {**os.environ, "GH_TOKEN": PLANTED_TOKEN, "COPILOT_GITHUB_TOKEN": PLANTED_TOKEN, "GITHUB_TOKEN": PLANTED_TOKEN, "COPILOT_PROVIDER_BASE_URL": "http://elsewhere",
                 "FAKE_COPILOT_RECORD": str(w.record), "FAKE_COPILOT_APPROVE": str(w.approve), "KEEP_ME": "yes"}
    w.cap = Capture()
    log.addHandler(w.cap)
    log.setLevel(logging.DEBUG)
    w.tmp = tmp_path
    w.changes = 0

    def make(folder=None):
        return cp.CopilotHost(folder or tmp_path / "copilot", log, changed=lambda: setattr(w, "changes", w.changes + 1), api_url=w.gh.url + "/api/releases/latest",
                              download_base=w.gh.url + "/dl", platform_key=PLATFORM, environ=w.planted, exe_names=(EXE_NAME,))
    w.make = make
    w.host = make()
    yield w
    log.removeHandler(w.cap)
    await w.gh.stop()


async def installed(w):
    job = w.host.start_install()
    await job.task
    assert job.state == "done", job.error
    return w.host


async def wait_for(cond, seconds=10.0):
    end = asyncio.get_running_loop().time() + seconds
    while not cond():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.02)


async def signed_in(w):
    await installed(w)
    lg = w.host.start_signin()
    await wait_for(lambda: lg.state == "code")
    w.approve.write_text("yes")
    await lg.task
    assert lg.state == "done", lg.error
    w.approve.unlink()
    return w.host


def records(w) -> list:
    return [json.loads(x) for x in w.record.read_text(encoding="utf-8").splitlines()] if w.record.exists() else []


def leak_scan(w, *extra):
    """Nothing the host logged, said or returned holds a token (or anything shaped like one)."""
    blob = "\n".join(w.cap.lines + [str(x) for x in extra])
    assert FAKE_TOKEN not in blob and PLANTED_TOKEN not in blob
    assert cp.SECRET_RE.search(blob.replace("[token]", "")) is None


class FakeRequest:
    pass


# ============================================================================================
# The small pure parts
# ============================================================================================
class TestParts:
    async def test_the_checksum_list_is_read_by_file_name(self):
        text = ("a" * 64 + "  copilot-win32-x64.zip\n" + "B" * 64 + " *copilot-linux-x64.tar.gz\nnot a line\n")
        assert cp.parse_sums(text) == {"copilot-win32-x64.zip": "a" * 64, "copilot-linux-x64.tar.gz": "b" * 64}

    async def test_the_file_name_follows_the_pc_kind(self):
        assert cp.asset_name("win32-x64") == "copilot-win32-x64.zip" and cp.asset_name("win32-arm64") == "copilot-win32-arm64.zip"
        assert cp.asset_name("linux-x64") == "copilot-linux-x64.tar.gz"

    async def test_the_child_environment_is_cleaned_and_given_its_own_folder(self, tmp_path):
        env = cp.child_env(tmp_path / "home", {"PATH": "p", "GH_TOKEN": "x", "GITHUB_TOKEN": "x", "COPILOT_GITHUB_TOKEN": "x", "COPILOT_MODEL": "x", "KEEP": "1"})
        assert env["KEEP"] == "1" and env["PATH"] == "p"
        assert not {"GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_MODEL"} & set(env)
        assert env["COPILOT_HOME"] == str(tmp_path / "home") and env["GH_CONFIG_DIR"].startswith(str(tmp_path / "home"))
        assert env["COPILOT_AUTO_UPDATE"] == "false"

    async def test_a_lone_message_goes_as_it_is_and_a_conversation_keeps_who_said_what(self):
        assert cp.prompt_from([{"role": "user", "content": "Hi"}]) == "Hi"
        p = cp.prompt_from([{"role": "system", "content": "Be brief."}, {"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}, {"role": "user", "content": "C"}])
        assert "Instructions:\nBe brief." in p and "User: A" in p and "Assistant: B" in p and p.index("User: C") > p.index("Assistant: B")

    async def test_the_model_list_keeps_only_bare_names(self):
        text = "Available models:\n  gpt-5.2\n  - claude-sonnet-5-5 (default)\nUsage: copilot [options]\nsome long sentence about things that are not a model at all ok\n  gemini-3-pro\n"
        assert cp.parse_model_list(text) == ["gpt-5.2", "claude-sonnet-5-5", "gemini-3-pro"]

    async def test_masking_hides_anything_shaped_like_a_github_token(self):
        for t in (FAKE_TOKEN, PLANTED_TOKEN, "github_pat_" + "a" * 40):
            assert t not in cp.mask(f"failed for {t} now")

    async def test_the_archive_cannot_write_outside_its_folder(self, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../escaped.txt", "x")
        with pytest.raises(ValueError):
            cp.safe_extract(evil, tmp_path / "out")
        assert not (tmp_path / "escaped.txt").exists()


# ============================================================================================
# Install
# ============================================================================================
class TestInstall:
    async def test_installs_into_its_own_folder_checks_the_checksum_and_records_the_version(self, world):
        host = await installed(world)
        assert host.installed() and host.version() == "9.9.9" and host.exe.is_file()
        assert host.exe.resolve().is_relative_to((world.tmp / "copilot" / "bin").resolve())
        assert (world.tmp / "copilot" / "bin" / "resources" / "notes.txt").exists(), "the whole release is unpacked, not only the program file"
        assert host.state() == "needs_signin"
        assert sorted(p.name for p in (world.tmp / "copilot").iterdir()) == ["bin", "state.json"], "nothing else is left behind (no download, no staging folder)"
        assert any("SHA256SUMS.txt" in r for r in world.gh.requests)

    async def test_a_download_that_does_not_match_the_published_checksum_is_refused_and_deleted(self, world):
        world.gh.sums_wrong = True
        job = world.host.start_install()
        await job.task
        assert job.state == "error" and job.error == cp.LINE_CHECKSUM
        assert not world.host.installed() and not (world.tmp / "copilot" / "bin").exists()
        assert not list((world.tmp / "copilot").glob("download*")) and not (world.tmp / "copilot" / "bin.new").exists()

    async def test_a_release_with_no_checksum_list_is_refused(self, world):
        world.gh.no_sums = True
        job = world.host.start_install()
        await job.task
        assert job.state == "error" and job.error == cp.LINE_DOWNLOAD and not world.host.installed()

    async def test_an_archive_that_tries_to_leave_its_folder_is_refused(self, world):
        world.gh.members = {"../escaped.txt": b"x", EXE_NAME: launcher_bytes()} if WIN else {"../escaped.txt": b"x"}
        job = world.host.start_install()
        await job.task
        assert job.state == "error" and not world.host.installed()
        assert not (world.tmp / "escaped.txt").exists()

    async def test_an_archive_with_no_program_in_it_says_so(self, world):
        world.gh.members = {"readme.txt": b"nothing here"}
        job = world.host.start_install()
        await job.task
        assert job.state == "error" and job.error == cp.LINE_NO_PROGRAM and not world.host.installed()

    async def test_the_program_runs_in_a_cleaned_environment_with_its_own_folder(self, world):
        await signed_in(world)
        await world.host.test()
        ask = [r for r in records(world) if r["kind"] == "ask"][-1]
        assert ask["names"] == ["COPILOT_AUTO_UPDATE", "COPILOT_HOME", "GH_CONFIG_DIR"], "no token, no provider override: only what Copilot needs"
        assert ask["home"] == str(world.host.home) and ask["auto_update"] == "false"
        assert not ask["override_seen"]

    async def test_update_swaps_in_the_newer_program_and_a_failed_update_keeps_the_old_one(self, world):
        await installed(world)
        world.gh.version = world.planted["FAKE_COPILOT_VERSION"] = "9.9.10"
        job = world.host.start_install(updating=True)
        await job.task
        assert job.state == "done" and world.host.version() == "9.9.10" and not (world.tmp / "copilot" / "bin.old").exists()
        world.gh.version = "9.9.11"
        world.gh.sums_wrong = True
        job = world.host.start_install(updating=True)
        await job.task
        assert job.state == "error" and world.host.version() == "9.9.10" and world.host.installed()

    async def test_an_update_before_install_is_refused_and_the_newest_version_is_remembered(self, world):
        with pytest.raises(cp.CopilotError) as e:
            world.host.start_install(updating=True)
        assert e.value.message == cp.LINE_NOT_INSTALLED
        await installed(world)
        world.gh.version = "9.9.12"
        world.host.refresh_latest_soon(force=True)
        await world.host._latest_task
        assert world.host.update_available() and world.host.status_json(False)["updateAvailable"] is True


# ============================================================================================
# Sign in and out
# ============================================================================================
class TestSignIn:
    async def test_sign_in_shows_the_code_then_turns_ready_when_it_is_approved(self, world):
        await installed(world)
        lg = world.host.start_signin()
        await wait_for(lambda: lg.state == "code")
        st = world.host.status_json(False)
        assert st["state"] == "signing_in" and st["signin"]["code"] == "ABCD-1234" and st["signin"]["url"] == "https://github.com/login/device"
        assert st["signedIn"] is False and st["models"] == []
        world.approve.write_text("yes")
        await lg.task
        st = world.host.status_json(False)
        assert st["state"] == "ready" and st["signedIn"] is True and st["signin"] is None
        assert [m["id"] for m in st["models"]]

    async def test_a_token_in_this_pcs_own_environment_is_not_used_instead(self, world):
        await installed(world)
        # signed in on the books, but the program's own sign-in is gone: it must say so rather than quietly use the planted GH_TOKEN
        world.host._write_state(signedIn=True)
        r = await world.host.test()
        assert r["ok"] is False and r["error"] == cp.LINE_EXPIRED
        assert world.host.state() == "needs_signin" and world.host.status_json(False)["warning"] == cp.LINE_EXPIRED
        assert not any(x.get("override_seen") for x in records(world))

    async def test_cancelling_a_sign_in_stops_the_program_and_leaves_the_row_asking_for_one(self, world):
        await installed(world)
        lg = world.host.start_signin()
        await wait_for(lambda: lg.state == "code")
        assert world.host.cancel_signin() is True
        await asyncio.wait({lg.task}, timeout=5)
        assert lg.state == "cancelled" and world.host.state() == "needs_signin" and not world.host.signed_in()

    async def test_a_code_that_runs_out_ends_with_one_short_line(self, world):
        world.planted["FAKE_COPILOT_WAIT"] = "0.3"
        await installed(world)
        lg = world.host.start_signin()
        await lg.task
        assert lg.state == "error" and lg.error == cp.LINE_LOGIN_FAILED
        assert world.host.status_json(False)["warning"] == cp.LINE_LOGIN_FAILED and world.host.state() == "needs_signin"

    async def test_a_program_that_prints_no_code_says_so(self, world):
        world.planted["FAKE_COPILOT_MODE"] = "login_fail"
        await installed(world)
        lg = world.host.start_signin()
        await lg.task
        assert lg.state == "error" and lg.error == cp.LINE_NO_CODE

    async def test_sign_in_needs_the_program_first(self, world):
        with pytest.raises(cp.CopilotError) as e:
            world.host.start_signin()
        assert e.value.message == cp.LINE_NOT_INSTALLED

    async def test_sign_out_logs_the_program_out_and_clears_what_it_wrote(self, world):
        host = await signed_in(world)
        assert (host.home / "config.json").exists()
        st = await host.sign_out()
        assert st["state"] == "needs_signin" and st["signedIn"] is False
        assert not host.home.exists()
        assert any(r["kind"] == "logout" for r in records(world))
        r = await host.test()
        assert r["ok"] is False and r["error"] == cp.LINE_NEEDS_SIGN_IN


# ============================================================================================
# Chat
# ============================================================================================
async def sse(resp_text: str) -> tuple:
    chunks, text = [], ""
    for line in resp_text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            ev = json.loads(line[6:])
            chunks.append(ev)
            text += ev["choices"][0]["delta"].get("content") or ""
    return chunks, text


async def serve(host):
    """A tiny door that hands every chat request to the host, the way courier.py does."""
    async def chat(request):
        try:
            return await host.chat(request, await request.json())
        except cp.CopilotError as e:
            return web.json_response({"error": {"message": e.message, "kind": e.kind}}, status=e.status)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}"


async def post(url, body):
    async with aiohttp.ClientSession() as s:
        async with s.post(url + "/v1/chat/completions", json=body) as r:
            return r.status, await r.text()


class TestChat:
    @pytest_asyncio.fixture
    async def door(self, world):
        await signed_in(world)
        runner, url = await serve(world.host)
        yield url
        await runner.cleanup()

    async def test_a_whole_answer_is_an_openai_chat_completion(self, world, door):
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        data = json.loads(text)
        assert status == 200 and data["choices"][0]["message"]["content"] == "Hello" and data["choices"][0]["finish_reason"] == "stop"
        assert data["model"] == "copilot/default"
        ask = [r for r in records(world) if r["kind"] == "ask"][-1]
        assert ask["model"] == "" and not ask["via_stdin"], "the default model names no --model; a short prompt rides on the command line"
        assert "-p" in ask["argv"] and "--no-ask-user" in ask["argv"] and "--deny-tool=shell,write" in ask["argv"]
        assert Path(ask["cwd"]).name.startswith("copilot-ask-"), "it runs in an empty folder of its own"
        assert not Path(ask["cwd"]).exists(), "which is gone afterwards"

    async def test_a_streamed_answer_arrives_in_pieces_and_ends_with_done(self, world, door):
        status, text = await post(door, {"model": "copilot/gpt-5.2", "stream": True, "messages": [{"role": "user", "content": "Hi"}]})
        chunks, said = await sse(text)
        assert status == 200 and said == "Hello" and text.rstrip().endswith("data: [DONE]")
        assert [c["choices"][0]["delta"].get("content") for c in chunks][:3] == ["", "Hel", "lo"]
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        assert [r for r in records(world) if r["kind"] == "ask"][-1]["model"] == "gpt-5.2"

    async def test_a_long_conversation_goes_in_through_the_programs_input(self, world, door, monkeypatch):
        monkeypatch.setattr(cp, "ARGV_PROMPT_LIMIT", 10)
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "A"},
                                                                                  {"role": "assistant", "content": "B"}, {"role": "user", "content": "C"}]})
        assert status == 200 and json.loads(text)["choices"][0]["message"]["content"] == "Hello"
        ask = [r for r in records(world) if r["kind"] == "ask"][-1]
        assert ask["via_stdin"] is True and ask["prompt_has_system"] is True and "-p" not in ask["argv"]

    async def test_a_program_that_prints_plain_text_is_still_answered(self, world, door):
        world.planted["FAKE_COPILOT_MODE"] = "plain"
        status, text = await post(door, {"model": "copilot/default", "stream": True, "messages": [{"role": "user", "content": "Hi"}]})
        _, said = await sse(text)
        assert status == 200 and said == "Hello"

    async def test_an_older_program_that_refuses_a_flag_is_asked_again_with_fewer(self, world, door):
        world.planted["FAKE_COPILOT_MODE"] = "unknown_flag"
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        assert status == 200 and json.loads(text)["choices"][0]["message"]["content"] == "Hello"
        assert world.host._short_flags is True
        asks = [r for r in records(world) if r["kind"] == "ask"]
        assert asks[-2]["argv"].count("--output-format") == 1 and "--output-format" not in asks[-1]["argv"]

    async def test_a_limit_is_passed_on_in_githubs_own_words(self, world, door):
        world.planted["FAKE_COPILOT_MODE"] = "limit"
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        err = json.loads(text)["error"]
        assert status == 429 and err["kind"] == "limit" and "reached your monthly limit" in err["message"]

    async def test_an_account_without_copilot_gets_its_one_line(self, world, door):
        world.planted["FAKE_COPILOT_MODE"] = "no_plan"
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        assert status == 403 and json.loads(text)["error"]["message"] == cp.LINE_NO_PLAN

    async def test_a_program_that_says_nothing_gets_the_plain_no_answer_line(self, world, door):
        world.planted["FAKE_COPILOT_MODE"] = "silent_fail"
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        assert status == 502 and json.loads(text)["error"]["message"] == cp.LINE_NO_ANSWER

    async def test_a_sign_in_that_was_lost_sends_the_row_back_to_needs_sign_in(self, world, door):
        for f in world.host.home.glob("config.json"):
            f.unlink()
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        assert status == 503 and json.loads(text)["error"]["message"] == cp.LINE_EXPIRED
        assert world.host.state() == "needs_signin"

    async def test_a_request_with_nothing_to_answer_is_refused(self, world, door):
        status, text = await post(door, {"model": "copilot/default", "messages": [{"role": "system", "content": "x"}]})
        assert status == 400

    async def test_requests_before_install_or_sign_in_are_refused_with_the_plain_line(self, world):
        with pytest.raises(cp.CopilotError) as e:
            await world.host.chat(FakeRequest(), {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        assert e.value.message == cp.LINE_NOT_INSTALLED and e.value.status == 503
        await installed(world)
        with pytest.raises(cp.CopilotError) as e:
            await world.host.chat(FakeRequest(), {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]})
        assert e.value.message == cp.LINE_NEEDS_SIGN_IN

    async def test_it_answers_only_models_with_its_own_prefix(self, world):
        assert world.host.handles("copilot/gpt-5.2") and world.host.handles("Copilot/default")
        assert not world.host.handles("claude-sonnet-5-5") and not world.host.handles("gpt-5.2")


class TestModelsAndTest:
    async def test_the_models_are_the_programs_own_list_with_default_first(self, world):
        host = await signed_in(world)
        ids = await host.models()
        assert ids == ["copilot/default", "copilot/gpt-5.2", "copilot/claude-sonnet-5-5", "copilot/gemini-3-pro"]
        assert host.status_json(False)["models"][0] == {"id": "copilot/default", "name": "Default"}

    async def test_the_test_button_makes_one_tiny_real_request(self, world):
        host = await signed_in(world)
        r = await host.test()
        assert r["ok"] is True and r["model"] == "copilot/default" and r["seconds"] >= 0

    async def test_a_test_that_fails_returns_the_one_line_not_an_exception(self, world):
        host = await signed_in(world)
        world.planted["FAKE_COPILOT_MODE"] = "limit"
        r = await host.test()
        assert r["ok"] is False and "limit" in r["error"] and r["kind"] == "limit"


# ============================================================================================
# Uninstall
# ============================================================================================
class TestUninstall:
    async def test_it_removes_this_programs_folder_and_nothing_next_to_it(self, world):
        host = await signed_in(world)
        neighbour = world.tmp / "keep-me.txt"
        neighbour.write_text("mine")
        other = world.tmp / "claude"
        other.mkdir()
        (other / "state.json").write_text("{}")
        out = await host.uninstall()
        assert out["state"] == "not_installed" and out["freedBytes"] > 0
        assert not (world.tmp / "copilot").exists()
        assert neighbour.read_text() == "mine" and (other / "state.json").exists()
        assert any(r["kind"] == "logout" for r in records(world)), "the program is asked to forget its own sign-in first"

    async def test_busy_answers_a_plain_line_and_changes_nothing(self, world):
        host = await signed_in(world)
        host.inflight = 1
        with pytest.raises(cp.CopilotError) as e:
            await host.uninstall()
        assert e.value.message == cp.LINE_BUSY and host.installed()

    async def test_it_can_be_installed_again_afterwards(self, world):
        host = await signed_in(world)
        await host.uninstall()
        await installed(world)
        assert host.state() == "needs_signin", "the old sign-in is gone with it"


# ============================================================================================
# The token never leaks
# ============================================================================================
class TestNoLeak:
    async def test_every_log_line_status_and_response_of_a_whole_run_is_free_of_any_token(self, world, caplog):
        caplog.set_level(logging.DEBUG)
        seen = []
        host = await signed_in(world)
        seen.append(host.status_json(False))
        runner, url = await serve(host)
        try:
            seen.append(await post(url, {"model": "copilot/default", "stream": True, "messages": [{"role": "user", "content": "Hi"}]}))
            seen.append(await post(url, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]}))
            for mode in ("limit", "no_plan", "silent_fail", "token_in_error"):
                world.planted["FAKE_COPILOT_MODE"] = mode
                seen.append(await post(url, {"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]}))
                seen.append(host.status_json(False))
            world.planted["FAKE_COPILOT_MODE"] = "ok"
            seen.append(await host.test())
            seen.append(await host.models())
            seen.append(await host.sign_out())
        finally:
            await runner.cleanup()
        leak_scan(world, json.dumps(seen, default=str), "\n".join(r.getMessage() for r in caplog.records))
        assert FAKE_TOKEN not in (world.tmp / "copilot" / "state.json").read_text(encoding="utf-8")

    async def test_the_token_that_a_failing_program_prints_is_masked_in_the_one_line(self, world):
        host = await signed_in(world)
        world.planted["FAKE_COPILOT_MODE"] = "token_in_error"
        r = await host.test()
        assert r["ok"] is False and FAKE_TOKEN not in json.dumps(r) and "[token]" in r["error"]

    async def test_the_couriers_log_tail_hides_a_github_token_too(self):
        for token in (FAKE_TOKEN, PLANTED_TOKEN, "github_pat_" + "a" * 40):
            assert token not in cr.mask_log_line(f"Copilot failed for {token} at noon", "ABC-123")

    async def test_the_state_file_holds_no_sign_in_only_whether_there_is_one(self, world):
        host = await signed_in(world)
        assert json.loads((world.tmp / "copilot" / "state.json").read_text()) == {"version": "9.9.9", "exe": EXE_NAME, "signedIn": True}
        assert host.status_json(False)["signedIn"] is True


# ============================================================================================
# The Courier's door
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
    (tmp_path / "courier").mkdir()
    settings = cr.CourierSettings(tmp_path / "courier" / "courier.json")
    settings.set(port=free_port())
    c = cr.Courier(settings, log, bind_ips=["127.0.0.1"], discovery_port=free_port(), keep_awake=cs.KeepAwake(type("A", (), {"start": lambda s: True, "stop": lambda s: None})()),
                   gpu_reader=lambda: [])
    c.adapters = []
    c.copilot = world.make(tmp_path / "courier" / "copilot")
    c.copilot.changed = c.signal_change
    await c.start()
    yield Door(c)
    await c.stop()


class TestCourierDoor:
    async def test_the_report_carries_copilot_and_its_state_moves_with_each_step(self, world, courier):
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["copilot"]["state"] == "not_installed"
        await courier.call("POST", "/courier/v1/copilot/install")
        await courier.courier.copilot.install_job.task
        assert (await courier.call("GET", "/courier/v1/copilot/status"))["state"] == "needs_signin"
        out = await courier.call("POST", "/courier/v1/copilot/signin")
        assert out["state"] in ("signing_in", "needs_signin")
        await wait_for(lambda: courier.courier.copilot.login.state == "code")
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["copilot"]["state"] == "signing_in" and rep["copilot"]["signin"]["code"] == "ABCD-1234"
        world.approve.write_text("yes")
        await courier.courier.copilot.login.task
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["copilot"]["state"] == "ready" and [m["id"] for m in rep["copilot"]["models"]]
        assert FAKE_TOKEN not in json.dumps(rep)

    async def test_cancel_sign_in_and_sign_out_work_through_the_door(self, world, courier):
        await courier.call("POST", "/courier/v1/copilot/install")
        await courier.courier.copilot.install_job.task
        await courier.call("POST", "/courier/v1/copilot/signin")
        await wait_for(lambda: courier.courier.copilot.login.state == "code")
        assert (await courier.call("POST", "/courier/v1/copilot/cancelsignin"))["state"] in ("needs_signin", "signing_in")
        await asyncio.wait({courier.courier.copilot.login.task}, timeout=5)
        assert (await courier.call("GET", "/courier/v1/copilot/status"))["state"] == "needs_signin"
        assert (await courier.call("POST", "/courier/v1/copilot/signout"))["state"] == "needs_signin"

    async def test_a_signin_before_install_answers_its_one_line(self, world, courier):
        r = await courier.call("POST", "/courier/v1/copilot/signin", expect=409)
        assert r["error"]["message"] == cp.LINE_NOT_INSTALLED

    async def test_chat_for_a_copilot_model_goes_to_copilot_and_the_models_list_has_it(self, world, courier):
        await signed_in_through(courier, world)
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/v1/chat/completions", json={"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]},
                              headers={"Authorization": f"Bearer {courier.courier.settings.code}"}) as r:
                data = await r.json()
        assert r.status == 200 and data["choices"][0]["message"]["content"] == "Hello"
        models = (await courier.call("GET", "/v1/models"))["data"]
        assert {"id": "copilot/default", "object": "model", "owned_by": "copilot"} in models

    async def test_a_refused_request_carries_the_kind_in_a_header(self, world, courier):
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/v1/chat/completions", json={"model": "copilot/default", "messages": [{"role": "user", "content": "Hi"}]},
                              headers={"Authorization": f"Bearer {courier.courier.settings.code}"}) as r:
                assert r.status == 503 and r.headers.get("X-Courier-Kind") == "not_installed"

    async def test_test_and_uninstall_work_through_the_door(self, world, courier):
        await signed_in_through(courier, world)
        assert (await courier.call("POST", "/courier/v1/copilot/test"))["ok"] is True
        out = await courier.call("POST", "/courier/v1/copilot/uninstall")
        assert out["state"] == "not_installed" and out["freedBytes"] > 0

    async def test_nothing_is_answered_without_the_pairing_code(self, world, courier):
        for method, path in (("GET", "/courier/v1/copilot/status"), ("POST", "/courier/v1/copilot/signin"), ("POST", "/courier/v1/copilot/install"),
                             ("POST", "/courier/v1/copilot/uninstall"), ("POST", "/courier/v1/copilot/signout")):
            async with aiohttp.ClientSession() as s:
                async with s.request(method, courier.base + path, json={}) as r:
                    assert r.status == 403, path

    async def test_an_unknown_action_is_not_a_route(self, world, courier):
        async with aiohttp.ClientSession() as s:
            async with s.post(courier.base + "/courier/v1/copilot/format-everything", json={}, headers={"Authorization": f"Bearer {courier.courier.settings.code}"}) as r:
                assert r.status == 404


async def signed_in_through(courier, world):
    await courier.call("POST", "/courier/v1/copilot/install")
    await courier.courier.copilot.install_job.task
    await courier.call("POST", "/courier/v1/copilot/signin")
    await wait_for(lambda: courier.courier.copilot.login.state == "code")
    world.approve.write_text("yes")
    await courier.courier.copilot.login.task
    assert courier.courier.copilot.state() == "ready"


# ============================================================================================
# The main device's side
# ============================================================================================
class FakeRemotes:
    def __init__(self): self.calls = []

    async def courier_copilot(self, rid, action):
        self.calls.append((rid, action))
        if action == "signin" and rid == "boom":
            raise op.OtherPcsError(409, cp.LINE_NOT_INSTALLED)
        return {"state": "ready"}


class TestGamingSide:
    @pytest_asyncio.fixture
    async def client(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        app = FastAPI()
        self.remotes = FakeRemotes()
        copilot_wiring.setup(app, self.remotes)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c

    async def test_the_row_asks_for_one_action_at_a_time(self, client):
        for a in copilot_wiring.ACTIONS:
            assert (await client.post(f"/config/copilot/pc/{a}", json={"id": "msi"})).json() == {"state": "ready"}
        assert self.remotes.calls == [("msi", a) for a in copilot_wiring.ACTIONS]

    async def test_an_unknown_action_is_a_404_and_a_refusal_keeps_the_pcs_own_line(self, client):
        assert (await client.post("/config/copilot/pc/format-everything", json={"id": "msi"})).status_code == 404
        r = await client.post("/config/copilot/pc/signin", json={"id": "boom"})
        assert r.status_code == 409 and r.json() == {"detail": cp.LINE_NOT_INSTALLED}

    async def test_the_remote_service_sends_it_on_and_keeps_nothing(self):
        class Client:
            def __init__(self): self.sent = []
            async def copilot_post(self, what, body, timeout=30): self.sent.append((what, body)); return {"state": "signing_in"}
            async def fetch_report(self): return {"generated_at": 1, "courier": {"name": "MSI", "id": "i"}, "programs": [], "gpus": []}
        svc = op.RemoteServers(lambda: {}, lambda c: None, log, lambda n: None, lambda: set())
        r = op.Remote(id="msi", kind="courier", name="MSI", base="http://x")
        r.client = Client()
        svc._remotes = {"msi": r}
        svc._loaded = True
        assert await svc.courier_copilot("msi", "signin") == {"state": "signing_in"}
        assert r.client.sent == [("signin", {})]


class TestModelsOnTheGamingPc:
    REPORT = {"programs": [], "copilot": {"state": "ready", "models": [{"id": "copilot/default", "name": "Default"}, {"id": "copilot/gpt-5.2", "name": "gpt-5.2"}]}}

    async def test_copilot_models_join_the_other_pcs_model_list_by_name(self):
        m = op.models_from_report(self.REPORT)
        assert m == [{"id": "copilot/default", "name": "Copilot Default", "vision": False, "program": "copilot", "state": "running"},
                     {"id": "copilot/gpt-5.2", "name": "Copilot gpt-5.2", "vision": False, "program": "copilot", "state": "running"}]
        assert op.courier_program_label(self.REPORT) == "Copilot"

    async def test_claude_and_copilot_side_by_side_are_both_named(self):
        rep = {"programs": [], "claude": {"state": "ready", "models": []}, "copilot": {"state": "ready", "models": []}}
        assert op.courier_program_label(rep) == "Claude + Copilot"

    async def test_a_copilot_that_is_not_ready_adds_nothing(self):
        for st in ("not_installed", "needs_signin", "installing", "signing_in"):
            rep = {"programs": [], "copilot": {"state": st, "models": [{"id": "copilot/default", "name": "x"}]}}
            assert op.models_from_report(rep) == [] and op.courier_program_label(rep) == "llama.cpp"

    async def test_a_pc_name_in_front_still_finds_the_model_with_its_own_slash(self):
        svc = op.RemoteServers(lambda: {}, lambda c: None, log, lambda n: None, lambda: set())
        r = op.Remote(id="msi", kind="courier", name="MSI", base="http://x")
        svc._remotes = {"msi": r}
        svc._loaded = True
        found = svc.match("msi/copilot/gpt-5.2")
        assert found and found[0].id == "msi" and found[1] == "copilot/gpt-5.2"

    async def test_the_overview_gives_those_rows_no_load_or_unload_button(self):
        import overview
        pcs = [{"id": "msi", "name": "MSI", "online": True, "kind": "courier", "gpu": None, "program": "Copilot", "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None,
                "models": [{"id": "copilot/default", "name": "Copilot Default", "state": "running", "program": "copilot"},
                           {"id": "gemma", "name": "Gemma", "state": "running", "program": "llamacpp"}]}]
        d = overview.build(tracking=False, figures=None, this_pc={"name": "Gaming device", "host": "g", "gpu": None, "usedMb": None, "totalMb": None}, local_models=[], local_speeds={}, pcs=pcs,
                           providers=[], health={}, uses={"pc:msi/copilot/default": ["Dialogue"], "pc:msi/gemma": ["Memories"]}, routes=[], decisions=None, voice=None, setup={"done": True, "left": []})
        rows = {r["name"]: r for g in d["groups"] for r in g["rows"]}
        assert rows["Copilot Default"]["where"] == "MSI" and rows["Copilot Default"]["action"] is None and rows["Copilot Default"]["test"] == {"model": "msi/copilot/default"}
        assert rows["Gemma"]["action"] is not None, "other programs keep theirs"


# ============================================================================================
# The row on the Other devices tab
# ============================================================================================
class TestRowScript:
    async def test_every_state_has_its_words(self):
        import copilot_ui
        js = copilot_ui.COPILOT_JS
        for text in ("Not installed", "Needs sign-in", "Getting a sign-in code", "Waiting for the sign-in", "Ready", "Install", "Sign in", "Test", "Sign out", "Update", "Subscription",
                     "Cancel", "Uninstalling", "On any device, open <code>", "Type this sign-in code: ", "Approve the sign-in. This row turns Ready by itself."):
            assert text in js, text

    async def test_the_row_is_hooked_in_after_claudes_and_is_in_the_page(self):
        import other_pcs_ui
        import multiproxy
        assert "cpPcRow(r)" in other_pcs_ui.OTHER_PCS_JS and other_pcs_ui.OTHER_PCS_JS.index("ccPcRow(r)") < other_pcs_ui.OTHER_PCS_JS.index("cpPcRow(r)")
        assert "{copilot_ui.COPILOT_JS}" in Path(multiproxy.__file__).read_text(encoding="utf-8")

    async def test_the_uninstall_question_names_copilot_and_the_github_account_is_not_touched(self):
        import other_pcs_ui
        js = other_pcs_ui.OTHER_PCS_JS
        assert "'Uninstall Copilot from '" in js and "Your GitHub account is not touched." in js and "cpAct(a.id, 'uninstall')" in js

    async def test_nothing_secret_is_typed_or_stored_in_the_page(self):
        import copilot_ui
        js = copilot_ui.COPILOT_JS
        assert "<input" not in js and "localStorage" not in js and "sessionStorage" not in js

    async def test_there_is_no_new_kind_of_ui(self):
        import copilot_ui
        js = copilot_ui.COPILOT_JS
        for used in ("op-prog", "op-steps", "lm-bar", "lm-muted", "op-grow"):
            assert used in js
        assert "<style" not in js and "innerHTML =" not in js.replace("opRender", "")


# ============================================================================================
# A downloaded program that will not start: say why, from its own words (2026-10-06)
# ============================================================================================
class TestCopilotThatWillNotStart:
    async def test_the_real_version_line_with_its_full_stop_is_accepted(self, tmp_path, monkeypatch):
        import claude_courier
        real = asyncio.create_subprocess_exec

        async def fake(exe, *args, **kw):
            return await real(sys.executable, "-c", "print('GitHub Copilot CLI 1.0.92.'); print(\"Run 'copilot update' to check for updates.\")", **{k: v for k, v in kw.items() if k in ("stdin", "stdout", "stderr", "env")})
        monkeypatch.setattr(claude_courier.hidden_process, "create_subprocess_exec", fake)
        host = cp.CopilotHost(tmp_path / "c", log)
        assert await host._run_version(tmp_path / "copilot") == "1.0.92"

    async def test_a_download_that_cannot_start_is_not_kept_and_the_row_and_log_say_why(self, world, monkeypatch):
        host = world.host

        async def broken(self, exe):
            self._start_check = cp.StartCheck(returncode=127, output="./copilot: error while loading shared libraries: libstdc++.so.6: cannot open shared object file")
            return ""
        monkeypatch.setattr(type(host), "_run_version", broken)
        job = host.start_install()
        await job.task
        assert job.state == "error" and "a system library is missing (libstdc++.so.6)" in job.error and "ntivirus" not in job.error and not host.installed()
        assert any("Copilot didn't start when checked (exit code 127" in l and "libstdc++.so.6" in l for l in world.cap.lines)

    async def test_a_program_that_prints_nothing_on_linux_never_hears_about_antivirus(self, world, monkeypatch):
        host = world.host

        async def silent(self, exe):
            self._start_check = cp.StartCheck(returncode=1, output="")
            return ""
        monkeypatch.setattr(type(host), "_run_version", silent)
        monkeypatch.setattr(sys, "platform", "linux")
        job = host.start_install()
        await job.task
        assert job.state == "error" and job.error == "Copilot was downloaded but didn't start on <PC>, and printed nothing (exit code 1). The Courier's log has more."
