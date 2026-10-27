"""Signing Claude in on another device from the main device, with no terminal and no browser on that device (claude_courier.py `start_signin` / `submit_code`, terminal_process.py,
courier.py's /courier/v1/claude/signin/*, claude_wiring.py's /config/claude/pc/signin-*, claude_ui.py's row).

A stand-in for `claude setup-token` (tests/fixtures/fake_claude_signin.py) runs on a REAL hidden pseudo-terminal (ConPTY on Windows, `pty` on Linux): it prints a link, waits for a
pasted code and prints a made-up long code over two wrapped lines. Nothing real is installed or signed in; every code, link and token here is made up, and no browser is opened.
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

import claude_courier as cc
import claude_wiring
import other_pcs as op
import terminal_process as tp
from tests.test_claude_courier import (BAD, GOOD, Capture, Door, FakeAnthropic, FakeReleases, courier, installed, leak_scan, log, world)    # noqa: F401  (fixtures)

pytestmark = pytest.mark.asyncio
SIGNIN_PROGRAM = str(Path(__file__).parent / "fixtures" / "fake_claude_signin.py")
ONE_TIME = "fakecode123#STATE456"                     # what the stand-in's sign-in page would show
OTHER_LINK_CODE = "fakecode123#SOMETHINGELSE"
FAKE_TOKEN = "sk-ant-oat01-" + "fakeTOKEN_" * 9 + "abcde"      # 108 characters, the stand-in's long code


def program_argv(self):
    return [sys.executable, SIGNIN_PROGRAM]


@pytest_asyncio.fixture
async def sworld(world, monkeypatch):
    """The `world` of the Claude tests with the sign-in program swapped for the stand-in and its long code accepted by the stand-in API."""
    monkeypatch.setattr(cc.ClaudeHost, "signin_argv", program_argv)
    world.api.accept.add(FAKE_TOKEN)
    world.signin_record = world.tmp / "signin-record.jsonl"
    world.planted["FAKE_SIGNIN_RECORD"] = str(world.signin_record)
    await installed(world)
    yield world
    await world.host.shutdown()


def secrets_in(w, *extra):
    """The one-time code, the long code and the link's own values must be in no log line and no extra text (responses, status, report)."""
    blob = "\n".join(w.cap.lines + [str(x) for x in extra])
    for secret in (FAKE_TOKEN, "fakecode123", "FAKECHALLENGE0123456789", "state=STATE456", "00000000-fake-0000"):
        assert secret not in blob, secret


# ============================================================================================
# What the program prints, and what the Courier makes of it
# ============================================================================================
class TestReadingTheScreen:
    LINK = "https://claude.com/cai/oauth/authorize?code=true&client_id=abc&state=S1"

    async def test_the_programs_own_hyperlink_is_the_exact_link_even_when_the_text_is_broken_over_lines(self):
        raw = "\x1b[2J\x1b[H\x1b]8;;" + self.LINK + "\x1b\\" + self.LINK[:40] + "\r\n" + self.LINK[40:] + "\x1b]8;;\x1b\\\r\nPaste code here if prompted > "
        assert cc.find_link(raw) == self.LINK

    async def test_a_link_with_no_hyperlink_is_joined_over_its_lines_and_stops_at_the_next_sentence(self):
        raw = "Use the url below:\r\n\r\n" + self.LINK[:30] + "\r\n" + self.LINK[30:] + "\r\n\r\nPaste code here if prompted > "
        assert cc.find_link(raw) == self.LINK

    async def test_no_link_yet_is_an_empty_answer(self):
        assert cc.find_link("Opening browser to sign in...\r\n") == ""

    async def test_cursor_jumps_count_as_line_breaks_and_colour_codes_vanish(self):
        assert cc.strip_terminal("\x1b[31mred\x1b[0m\x1b[5;1Hnext\r\nlast") == "red\nnext\nlast"

    async def test_the_long_code_is_joined_over_two_lines_and_is_whole_only_once_more_text_follows(self):
        wrapped = "Your OAuth token (valid for 1 year):\r\n\r\n" + FAKE_TOKEN[:79] + "\r\n" + FAKE_TOKEN[79:]
        assert cc.find_token(wrapped) == (FAKE_TOKEN, False)          # it could still be growing
        assert cc.find_token(wrapped + "\r\n\r\nStore this token securely.") == (FAKE_TOKEN, True)
        assert cc.find_token("nothing yet") == ("", False)

    @pytest.mark.parametrize("url,ok", [
        ("https://claude.com/cai/oauth/authorize?x=1", True), ("https://claude.ai/login", True), ("https://platform.claude.com/oauth/authorize", True),
        ("https://console.anthropic.com/oauth", True), ("http://claude.com/cai/oauth/authorize", False), ("https://claude.com.evil.test/x", False),
        ("https://evilclaude.com/x", False), ("https://claude.com@evil.test/x", False), ("https://user:pw@claude.com/x", False), ("https://claude.com:8443/x", False),
        ("javascript:alert(1)", False), ("", False), ("https://127.0.0.1/x", False)])
    async def test_only_anthropics_own_addresses_are_ever_sent_on(self, url, ok):
        assert cc.link_allowed(url) is ok

    async def test_the_link_is_logged_without_its_query(self):
        assert cc.link_for_log(self.LINK + "&code_challenge=SECRET") == "https://claude.com/cai/oauth/authorize"
        assert cc.link_state(self.LINK) == "S1"


# ============================================================================================
# A real hidden terminal (ConPTY on Windows, pty on Linux), with a stand-in program
# ============================================================================================
class TestTheTerminal:
    async def _wait(self, term, pred, seconds=15):
        raw = ""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            raw += term.read().decode("utf-8", "replace")
            if pred(raw):
                return raw
            await asyncio.sleep(0.05)
        return raw

    async def test_the_program_sees_a_real_terminal_a_very_wide_one_and_gets_what_is_typed(self, tmp_path):
        code = "import sys, os; print('tty', sys.stdin.isatty() and sys.stdout.isatty(), os.environ.get('COLUMNS')); sys.stdout.flush(); l = sys.stdin.readline(); print('got', l.strip()); sys.stdout.flush()"
        term = tp.open_terminal([sys.executable, "-c", code], dict(os.environ), str(tmp_path))
        try:
            raw = await self._wait(term, lambda t: "tty" in t)
            assert "tty True 500" in raw
            term.write("hello\r")
            raw += await self._wait(term, lambda t: "got hello" in t)
            assert "got hello" in raw
        finally:
            term.kill()
        assert not term.alive()

    async def test_kill_ends_a_program_that_would_wait_for_ever(self, tmp_path):
        term = tp.open_terminal([sys.executable, "-c", "import time; time.sleep(3600)"], dict(os.environ), str(tmp_path))
        assert term.alive()
        term.kill()
        assert not term.alive()

    async def test_killing_twice_is_harmless_and_the_second_call_touches_nothing(self, tmp_path):
        """A program's number can belong to the NEXT program by the time a second kill arrives (found on Linux: cancel, then start again, and the new one was ended)."""
        term = tp.open_terminal([sys.executable, "-c", "import time; time.sleep(3600)"], dict(os.environ), str(tmp_path))
        term.kill()
        other = tp.open_terminal([sys.executable, "-c", "import time; time.sleep(3600)"], dict(os.environ), str(tmp_path))
        try:
            term.kill()
            assert other.alive()
        finally:
            other.kill()

    async def test_a_program_that_cannot_be_started_is_an_oserror_not_a_hang(self, tmp_path):
        with pytest.raises(OSError):
            tp.open_terminal([str(tmp_path / "no-such-program.exe")], dict(os.environ), str(tmp_path))


# ============================================================================================
# The whole sign-in on the Courier device
# ============================================================================================
class TestSignInFromTheGamingPc:
    async def test_the_link_comes_back_and_a_code_pasted_much_later_finishes_it_and_the_long_code_stays_on_this_pc(self, sworld):
        h = sworld.host
        st = await h.start_signin()
        assert st["state"] == "signing_in" and st["signin"]["state"] == "link" and cc.link_allowed(st["signin"]["url"]), st
        assert st["signedIn"] is False and not h.token_file.exists()
        await asyncio.sleep(2.0)                                   # the person is on their phone: the program keeps waiting
        assert h.status_json(False)["signin"]["state"] == "link"
        done = await h.submit_code("  " + ONE_TIME[:8] + "\r\n" + ONE_TIME[8:] + "  ")       # pasted over two lines: whole again
        assert done["state"] == "ready" and done["signedIn"] and done["signin"] is None
        assert h.token_file.read_text() == FAKE_TOKEN, "the long code was captured whole, from the two wrapped lines"
        assert FAKE_TOKEN not in json.dumps(done) and "fakecode123" not in json.dumps(done)
        assert h.login.term is None, "the program was stopped"
        holders = [p for p in sworld.tmp.rglob("*") if p.is_file() and FAKE_TOKEN.encode() in p.read_bytes()]
        assert holders == [h.token_file], "the long code is in exactly one file"
        secrets_in(sworld, done)

    async def test_the_program_ran_with_the_courier_environment_a_wide_terminal_and_no_browser_help(self, sworld):
        await sworld.host.start_signin()
        await sworld.host.submit_code(ONE_TIME)
        facts = [json.loads(l) for l in sworld.signin_record.read_text().splitlines()]
        assert len(facts) == 1
        f = facts[0]
        assert f["argv"] == [] and f["tty"] is True and f["columns"] == "500"
        assert not [n for n in f["names"] if n.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_OAUTH"))], "the planted key, bearer, profile and code were all removed"
        assert f["config_dir"] == str(sworld.tmp / "claude" / "home") and "DISABLE_AUTOUPDATER" in f["names"]
        if sys.platform != "win32":
            assert "BROWSER" in f["names"]

    async def test_a_wrong_code_is_answered_with_silence_so_it_is_given_up_on_and_nothing_is_kept(self, sworld, monkeypatch):
        monkeypatch.setattr(cc, "SIGNIN_CODE_WAIT", 1.5)
        h = sworld.host
        await h.start_signin()
        term = h.login.term
        with pytest.raises(cc.ClaudeError) as e:
            await h.submit_code("wrongcode999#STATE456")
        assert e.value.message == cc.LINE_CODE_NOT_ACCEPTED and e.value.kind == "refused"
        assert not h.token_file.exists() and not term.alive() and h.state() == "needs_signin"
        assert h.status_json(False)["signin"] is None
        secrets_in(sworld, e.value.message)

    async def test_a_code_from_a_different_link_is_refused_at_once_and_the_right_one_still_works(self, sworld):
        h = sworld.host
        await h.start_signin()
        with pytest.raises(cc.ClaudeError) as e:
            await h.submit_code(OTHER_LINK_CODE)
        assert e.value.message == cc.LINE_CODE_OTHER_LINK
        assert h.status_json(False)["signin"]["state"] == "link", "still waiting"
        assert (await h.submit_code(ONE_TIME))["state"] == "ready"

    @pytest.mark.parametrize("raw", ["", "   ", "short", "has\x00control" + "x" * 20])
    async def test_a_code_that_is_not_shaped_like_one_is_refused_before_anything_is_typed(self, sworld, raw):
        h = sworld.host
        await h.start_signin()
        with pytest.raises(cc.ClaudeError) as e:
            await h.submit_code(raw)
        assert e.value.status == 400 and h.login.state == "link"

    async def test_a_code_with_no_sign_in_waiting_is_refused(self, sworld):
        with pytest.raises(cc.ClaudeError) as e:
            await sworld.host.submit_code(ONE_TIME)
        assert e.value.message == cc.LINE_NO_CODE_YET and e.value.status == 409

    async def test_cancel_stops_the_program_and_a_new_sign_in_can_start(self, sworld):
        h = sworld.host
        await h.start_signin()
        term = h.login.term
        assert h.cancel_signin() and not term.alive()
        assert h.status_json(False)["signin"] is None and h.state() == "needs_signin"
        assert h.cancel_signin() is False
        assert (await h.start_signin())["signin"]["state"] == "link"

    async def test_one_sign_in_at_a_time_a_second_start_gets_the_same_one(self, sworld):
        h = sworld.host
        a = await h.start_signin()
        b = await h.start_signin()
        assert a["signin"]["url"] == b["signin"]["url"]
        assert len(sworld.signin_record.read_text().splitlines()) == 1, "only one program was started"

    async def test_a_program_that_never_prints_a_link_is_given_up_on_and_stopped(self, sworld, monkeypatch):
        monkeypatch.setattr(cc, "SIGNIN_LINK_WAIT", 1.0)
        sworld.planted["FAKE_SIGNIN_MODE"] = "no_link"
        h = sworld.host
        with pytest.raises(cc.ClaudeError) as e:
            await h.start_signin()
        assert e.value.message == cc.LINE_NO_LINK and e.value.kind == "no_link"
        assert h.login.term is None and h.state() == "needs_signin"

    async def test_a_program_that_needs_a_terminal_but_gets_none_is_the_same_clean_failure(self, sworld, monkeypatch):
        def refuse(*a, **k):
            raise tp.TerminalUnavailable("no pty here")
        monkeypatch.setattr(cc.terminal_process, "open_terminal", refuse)
        with pytest.raises(cc.ClaudeError) as e:
            await sworld.host.start_signin()
        assert e.value.message == cc.LINE_NO_TERMINAL and e.value.kind == "no_terminal" and e.value.status == 501

    async def test_a_program_that_dies_after_the_link_ends_the_sign_in_with_one_plain_line(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"] = "die"
        h = sworld.host
        await h.start_signin()
        for _ in range(100):
            if h.login.state == "error":
                break
            await asyncio.sleep(0.05)
        assert h.login.state == "error" and h.login.error == cc.LINE_SIGNIN_ENDED
        with pytest.raises(cc.ClaudeError) as e:
            await h.submit_code(ONE_TIME)
        assert e.value.message == cc.LINE_SIGNIN_ENDED

    async def test_a_link_that_is_not_anthropics_is_never_sent_on(self, sworld):
        sworld.planted["FAKE_SIGNIN_HOST"] = "evil.test"
        h = sworld.host
        with pytest.raises(cc.ClaudeError) as e:
            await h.start_signin()
        assert e.value.message == cc.LINE_BAD_LINK
        assert h.status_json(False)["signin"] is None and h.login.url == ""
        assert not any("evil.test" in line for line in sworld.cap.lines)

    @pytest.mark.parametrize("mode", ["no_osc8", "unwrapped", "slow"])
    async def test_other_ways_the_program_may_print_are_read_the_same(self, sworld, mode):
        sworld.planted["FAKE_SIGNIN_MODE"] = mode
        h = sworld.host
        st = await h.start_signin()
        assert cc.link_allowed(st["signin"]["url"]) and cc.link_state(st["signin"]["url"]) == "STATE456", "a text link joined over its lines is the same link"
        assert (await h.submit_code(ONE_TIME))["state"] == "ready" and h.token_file.read_text() == FAKE_TOKEN

    async def test_the_courier_quitting_stops_a_waiting_sign_in(self, sworld):
        h = sworld.host
        await h.start_signin()
        term = h.login.term
        await h.shutdown()
        assert not term.alive() and h.status_json(False)["signin"] is None

    async def test_not_installed_is_refused(self, world):
        with pytest.raises(cc.ClaudeError) as e:
            await world.host.start_signin()
        assert e.value.status == 409

    async def test_the_long_code_the_stand_in_api_refuses_is_not_kept(self, sworld):
        sworld.api.accept.discard(FAKE_TOKEN)
        h = sworld.host
        await h.start_signin()
        with pytest.raises(cc.ClaudeError) as e:
            await h.submit_code(ONE_TIME)
        assert e.value.kind in ("refused", "expired") and not h.token_file.exists()

    async def test_signing_out_while_waiting_stops_it_too(self, sworld):
        h = sworld.host
        await h.start_signin()
        term = h.login.term
        h.sign_out()
        assert not term.alive()


# ============================================================================================
# Through the real Courier door, and the leak scan
# ============================================================================================
class TestCourierDoor:
    async def test_start_code_and_cancel_through_the_door_and_the_report_follows(self, sworld, courier):
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        started = await courier.call("POST", "/courier/v1/claude/signin/start")
        assert started["state"] == "signing_in" and cc.link_allowed(started["signin"]["url"])
        rep = await courier.call("GET", "/courier/v1/report?fresh=1")
        assert rep["claude"]["signin"]["state"] == "link"
        await courier.call("POST", "/courier/v1/claude/signin/cancel")
        assert (await courier.call("GET", "/courier/v1/claude/status"))["signin"] is None
        await courier.call("POST", "/courier/v1/claude/signin/start")
        done = await courier.call("POST", "/courier/v1/claude/signin/code", {"code": ONE_TIME})
        assert done["state"] == "ready"

    async def test_a_wrong_shape_a_missing_signin_and_a_refusal_answer_with_their_one_line(self, sworld, courier, monkeypatch):
        monkeypatch.setattr(cc, "SIGNIN_CODE_WAIT", 1.5)
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        r = await courier.call("POST", "/courier/v1/claude/signin/code", {"code": ONE_TIME}, expect=409)
        assert r["error"]["message"] == cc.LINE_NO_CODE_YET
        await courier.call("POST", "/courier/v1/claude/signin/start")
        await courier.call("POST", "/courier/v1/claude/signin/code", {"code": "x"}, expect=400)
        r = await courier.call("POST", "/courier/v1/claude/signin/code", {"code": "wrong999#STATE456"}, expect=502)
        assert r["error"]["message"] == cc.LINE_CODE_NOT_ACCEPTED

    async def test_nothing_is_answered_without_the_pairing_code(self, sworld, courier):
        for step in ("start", "code", "cancel"):
            async with aiohttp.ClientSession() as s:
                async with s.post(courier.base + f"/courier/v1/claude/signin/{step}", json={"code": ONE_TIME}) as r:
                    assert r.status == 403, step
        assert courier.courier.claude.login is None, "nothing was started"

    async def test_a_whole_sign_in_leaves_no_code_link_or_long_code_in_any_log_status_response_or_report(self, sworld, courier, caplog):
        caplog.set_level(logging.DEBUG)
        seen = []
        await courier.call("POST", "/courier/v1/claude/install")
        await courier.courier.claude.install_job.task
        started = await courier.call("POST", "/courier/v1/claude/signin/start")
        seen.append({k: v for k, v in started.items() if k != "signin"})          # (the link itself is meant to be in `signin`, for the dashboard to open)
        seen.append(await courier.call("GET", "/courier/v1/claude/status") and "")
        seen.append(await courier.call("GET", "/courier/v1/log"))
        seen.append(await courier.call("POST", "/courier/v1/claude/signin/code", {"code": ONE_TIME}))
        seen.append(await courier.call("GET", "/courier/v1/claude/status"))
        seen.append(await courier.call("GET", "/courier/v1/report?fresh=1"))
        seen.append(await courier.call("GET", "/courier/v1/log"))
        seen.append(await courier.call("POST", "/courier/v1/claude/test"))
        seen.append(await courier.call("POST", "/courier/v1/claude/signout"))
        secrets_in(sworld, *seen, "\n".join(x.getMessage() for x in caplog.records))
        logfile = courier.courier.settings.path.parent / "courier.log"
        if logfile.exists():
            text = logfile.read_text(errors="replace")
            assert FAKE_TOKEN not in text and "fakecode123" not in text and "FAKECHALLENGE" not in text


# ============================================================================================
# The main device's side
# ============================================================================================
class FakeRemotes:
    def __init__(self):
        self.calls = []

    async def courier_claude(self, rid, action, body=None):
        self.calls.append((rid, action, body))
        return {"state": "signing_in"}


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

    async def test_the_three_new_actions_pass_on_and_only_the_code_carries_a_body(self, client):
        await client.post("/config/claude/pc/signin-start", json={"id": "msi", "code": "ignored"})
        await client.post("/config/claude/pc/signin-code", json={"id": "msi", "code": ONE_TIME})
        await client.post("/config/claude/pc/signin-cancel", json={"id": "msi", "code": "ignored"})
        assert self.remotes.calls == [("msi", "signin-start", {}), ("msi", "signin-code", {"code": ONE_TIME}), ("msi", "signin-cancel", {})]

    async def test_a_failed_one_time_code_request_never_writes_its_body_to_the_log(self):
        import multiproxy
        assert claude_wiring.SIGN_IN_CODE_PATH in multiproxy._BODY_HIDDEN_PATHS and claude_wiring.SIGN_IN_PATH in multiproxy._BODY_HIDDEN_PATHS

    async def test_the_remote_service_turns_them_into_the_couriers_routes_with_room_to_wait(self):
        class Client:
            def __init__(self):
                self.sent = []

            async def claude_post(self, what, body, timeout=30):
                self.sent.append((what, body, timeout))
                return {"state": "signing_in"}

            async def fetch_report(self):
                return {"generated_at": 1, "courier": {"name": "MSI", "id": "i"}, "programs": [], "gpus": []}
        svc = op.RemoteServers(lambda: {}, lambda c: None, log, lambda n: None, lambda: set())
        r = op.Remote(id="msi", kind="courier", name="MSI", base="http://x")
        r.client = Client()
        svc._remotes = {"msi": r}
        svc._loaded = True
        await svc.courier_claude("msi", "signin-start", {})
        await svc.courier_claude("msi", "signin-code", {"code": ONE_TIME})
        await svc.courier_claude("msi", "signin-cancel", {})
        await svc.courier_claude("msi", "signin", {"code": GOOD})
        assert [(w, t) for w, _, t in r.client.sent] == [("signin/start", 60), ("signin/code", 170), ("signin/cancel", 30), ("signin", 120)]
        assert ONE_TIME not in json.dumps(r.to_json(), default=str)


# ============================================================================================
# The row on the Other devices tab
# ============================================================================================
class TestRow:
    async def test_the_new_words_and_steps_are_there_and_the_older_way_is_kept(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        for text in ("Open the sign-in page", "Paste the code from the page", "Open the sign-in page on any device with a browser, sign in to Claude and authorize access.", "Once authorized, Claude will provide a code.",
                     "Copy the code, paste it here, and press <b>Save</b>.", "Waiting for the sign-in", "Getting a sign-in link", "Checking the sign-in code", "Starting…",
                     "'signin-start'", "'signin-code'", "'signin-cancel'",
                     "A sign-in page opened on <b>", "Sign in to Claude there and authorize access.", "No browser on ", "Click here", "Something went wrong? ", "Get a sign-in code using a terminal instead",
                     "open a terminal.</li>", "Run this command:", "claude setup-token", "Claude will provide a link. Paste the link into a browser on any device, sign in to Claude and authorize access.",
                     "Paste the code from the terminal", "Sign in with a terminal", "Opening the sign-in page on "):
            assert text in js, text

    async def test_the_page_holds_no_how_it_works_text_and_at_most_three_numbered_steps(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        block = js.split("function ccPageSignIn", 1)[1].split("function ccWay2Small", 1)[0]
        assert block.count("<li>") == 3 and "pseudo" not in block.lower() and "terminal" not in block.lower() and "token" not in block.lower()

    async def test_the_link_only_opens_when_it_is_anthropics_and_the_codes_are_never_stored(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        assert "function ccLinkOk" in js and "ccLinkOk(u)) window.open(u, '_blank', 'noopener')" in js
        assert 'type="password"' in js and "ccPageCode[id] = '';" in js
        assert "localStorage" not in js and "sessionStorage" not in js

    async def test_the_link_hosts_in_the_page_and_in_the_courier_are_the_same_list(self):
        import re
        import claude_ui
        m = re.search(r"CC_LINK_HOSTS = \[(.*?)\]", claude_ui.CLAUDE_JS)
        assert set(re.findall(r"'([^']+)'", m.group(1))) == set(cc.LINK_HOSTS)

    async def test_the_dashboard_script_still_parses_with_the_new_row(self):
        import shutil
        import subprocess
        import claude_ui
        node = shutil.which("node")
        if not node:
            pytest.skip("needs node")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.js"
            f.write_text(claude_ui.CLAUDE_JS, encoding="utf-8")
            r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr


# ============================================================================================
# However the sign-in finishes, the Courier's state is the truth and the row follows it (2026-10-06)
# ============================================================================================
async def settle(h, secs=8.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs and h.login and h.login.active():
        await asyncio.sleep(0.1)


class TestItFinishesByItself:
    async def test_a_sign_in_approved_on_that_device_finishes_with_no_code_pasted(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = "auto", "0.6"
        h = sworld.host
        st = await h.start_signin()
        assert st["state"] == "signing_in" and st["signedIn"] is False
        await settle(h)
        s = h.status_json(False)
        assert s["state"] == "ready" and s["signedIn"] is True and s["signin"] is None and s["warning"] == ""
        assert h.token_file.read_text() == FAKE_TOKEN
        secrets_in(sworld, s)

    async def test_a_code_pasted_after_it_already_finished_says_nothing_wrong(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = "auto", "0.3"
        h = sworld.host
        await h.start_signin()
        await settle(h)
        done = await h.submit_code(ONE_TIME)
        assert done["state"] == "ready" and done["warning"] == ""

    async def test_a_code_that_finishes_it_still_works(self, sworld):
        h = sworld.host
        await h.start_signin()
        assert (await h.submit_code(ONE_TIME))["state"] == "ready"

    async def test_a_program_that_ends_without_signing_in_leaves_one_plain_line_not_a_silent_needs_sign_in(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = "end", "0.4"
        h = sworld.host
        await h.start_signin()
        await settle(h)
        s = h.status_json(False)
        assert s["state"] == "needs_signin" and s["signin"] is None and not h.token_file.exists()
        assert s["warning"] == "The sign-in didn't finish. Press Sign in to try again." == cc.LINE_SIGNIN_ENDED
        await h.start_signin()
        assert h.status_json(False)["warning"] == "", "a new try clears the line"

    async def test_cancelling_says_nothing_about_it_not_finishing(self, sworld):
        h = sworld.host
        await h.start_signin()
        assert h.cancel_signin() is True
        await asyncio.sleep(0.3)
        assert h.status_json(False)["warning"] == ""


class TestThePageFollowsTheDevice:
    """The row's own script, run under Node with a stand-in page: the watch starts, asks every 2 seconds, and stops."""

    def run(self, body):
        import shutil
        import subprocess
        import tempfile
        import claude_ui
        node = shutil.which("node")
        if not node:
            pytest.skip("needs node")
        src = ("const asked = []; global.document = { hidden: false }; global.esc = s => String(s); global.jsq = s => String(s); global.opDot = () => '';\n"
               "global.opRefresh = async (f) => { asked.push(!!f); };\n" + claude_ui.CLAUDE_JS + "\n(async () => { " + body + " process.exit(0); })();")      # (the watch's real timer would keep Node alive)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.js"
            f.write_text(src, encoding="utf-8")
            r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr[:1200]
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_while_signing_in_and_for_sixty_seconds_after_it_asks_the_device_every_two_seconds(self):
        out = self.run("""
          ccWatchNote('msi', { state: 'signing_in' });
          const a = Object.keys(ccWatch);
          await ccWatchTick();
          ccWatchNote('msi', { state: 'needs_signin' });      // the steps went away
          const w = ccWatch.msi;
          await ccWatchTick();
          console.log(JSON.stringify({ a, activeAfter: w.active, left: Math.round((w.until - Date.now()) / 1000), asked: asked.length, every: CC_WATCH_EVERY_MS }));
        """)
        assert out["a"] == ["msi"] and out["activeAfter"] is False and 58 <= out["left"] <= 60 and out["asked"] == 2 and out["every"] == 2000

    def test_it_stops_at_ready_on_cancel_and_after_the_time_is_up(self):
        out = self.run("""
          ccWatchNote('msi', { state: 'signing_in' }); ccWatchNote('msi', { state: 'ready' });
          const ready = Object.keys(ccWatch).length;
          ccWatchNote('den', { state: 'signing_in' }); await ccCancelSignin_stub('den');
          const cancelled = Object.keys(ccWatch).length;
          ccWatchNote('lap', { state: 'signing_in' }); ccWatchNote('lap', { state: 'needs_signin' }); ccWatch.lap.until = Date.now() - 1;
          const before = asked.length; await ccWatchTick();
          console.log(JSON.stringify({ ready, cancelled, left: Object.keys(ccWatch).length, askedMore: asked.length - before, timer: ccWatchTimer === null }));
          async function ccCancelSignin_stub(id) { delete ccWatch[id]; }
        """)
        assert out == {"ready": 0, "cancelled": 0, "left": 0, "askedMore": 0, "timer": True}

    def test_the_real_cancel_and_start_paths_use_the_watch(self):
        import claude_ui
        js = claude_ui.CLAUDE_JS
        assert "ccWatchOn(id);\n  await ccAct(id, 'signin-start')" in js.replace("\r\n", "\n") and "delete ccWatch[id];" in js.split("async function ccCancelSignin")[1].split("}")[0]
        assert "ccWatchNote(r.id, c);" in js.split("function ccPcRow")[1][:200]


# ============================================================================================
# The three ways to sign in (design/mockup-claude-signin-row.html, 2026-10-06)
# ============================================================================================
class TestTheThreeWays:
    async def test_the_device_says_whether_the_program_opens_its_page_there(self, sworld, monkeypatch):
        h = sworld.host
        monkeypatch.setattr(cc.ClaudeHost, "_signin_env", lambda self: {**os.environ, "BROWSER": "true"})
        st = await h.start_signin()
        assert st["signin"]["browser"] == "none", "a headless device: nothing opens there, the row starts on the link and the code"
        h.cancel_signin()
        await asyncio.sleep(0.3)
        monkeypatch.setattr(cc.ClaudeHost, "_signin_env", lambda self: {k: v for k, v in os.environ.items() if k != "BROWSER"})
        st = await h.start_signin()
        assert st["signin"]["browser"] == "opened"

    async def test_a_headless_device_finishes_by_the_link_and_a_code(self, sworld, monkeypatch):
        monkeypatch.setattr(cc.ClaudeHost, "_signin_env", lambda self: {**os.environ, "BROWSER": "true"})
        h = sworld.host
        st = await h.start_signin()
        assert st["signin"]["browser"] == "none" and cc.link_allowed(st["signin"]["url"])
        done = await h.submit_code(ONE_TIME)
        assert done["state"] == "ready" and h.token_file.read_text() == FAKE_TOKEN

    async def test_a_headless_device_finishes_by_the_terminal_with_the_long_code_pasted(self, sworld, monkeypatch):
        monkeypatch.setattr(cc.ClaudeHost, "_signin_env", lambda self: {**os.environ, "BROWSER": "true"})
        h = sworld.host
        await h.start_signin()
        assert h.cancel_signin() is True                       # the person moves to the terminal way: the device's own try is stopped first
        await asyncio.sleep(0.3)
        assert h.status_json(False)["state"] == "needs_signin"
        done = await h.sign_in(FAKE_TOKEN)                     # the existing path for a pasted `claude setup-token` code
        assert done["state"] == "ready" and h.token_file.read_text() == FAKE_TOKEN
        secrets_in(sworld, done)


class TestTheRowInEachWay:
    """The row's script under Node with a stand-in page: what each way shows, and the small lines that move between them."""

    def draw(self, body):
        import shutil
        import subprocess
        import tempfile
        import claude_ui
        node = shutil.which("node")
        if not node:
            pytest.skip("needs node")
        src = ("global.document = { hidden: false }; global.esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); global.jsq = s => String(s);\n"
               "global.opDot = (c, t) => '<i class=\"dot ' + c + '\"></i>'; global.opName = r => r.name || r.id; global.opCopy = () => {}; global.opAskUninstall = () => {};\n"
               "global.opRender = () => {}; global.opRefresh = async () => {}; const posts = []; global.lmPost = async (u, b) => { posts.push([u, b || null]); return {}; };\n"
               "global.opState = { servers: [] };\n" + claude_ui.CLAUDE_JS +
               "\nfunction dev(c) { const r = { id: 'msi', name: 'MSI', report: { claude: Object.assign({ installed: true, platform: 'win32-x64' }, c) } }; opState.servers = [r]; return r; }\n"
               "(async () => { " + body + " process.exit(0); })();")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.js"
            f.write_text(src, encoding="utf-8")
            r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30, encoding="utf-8")
        assert r.returncode == 0, r.stderr[:1200]
        return json.loads(r.stdout.strip().splitlines()[-1])

    @staticmethod
    def plain(h):
        import html
        import re
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).replace(" .", ".").replace(" ,", ",").replace(" ?", "?").strip()

    def test_way_one_has_no_link_and_no_code_box_and_one_small_line(self):
        out = self.draw("""const r = dev({ state: 'signing_in', signin: { state: 'link', url: 'https://claude.com/x', browser: 'opened' } });
          console.log(JSON.stringify({ h: ccPcRow(r) }));""")
        h = out["h"]
        t = self.plain(h)
        assert "A sign-in page opened on MSI. Sign in to Claude there and authorize access." in t and "No browser on MSI? Click here" in t and "Waiting for the sign-in" in t
        assert "<input" not in h and "Open the sign-in page" not in h and "claude.com" not in h and ">Cancel<" in h and "Uninstall" not in h

    def test_way_two_has_the_link_with_copy_the_box_save_and_the_two_small_lines(self):
        out = self.draw("""const r = dev({ state: 'signing_in', signin: { state: 'link', url: 'https://claude.com/x', browser: 'opened' } });
          ccSetWay('msi', 2); console.log(JSON.stringify({ h: ccPcRow(r) }));""")
        h = out["h"]
        t = self.plain(h)
        assert "Open the sign-in page on any device with a browser, sign in to Claude and authorize access. Once authorized, Claude will provide a code. Copy the code, paste it here, and press Save." in t
        assert "Open the sign-in page" in t and "Copy" in t and 'placeholder="Paste the code from the page"' in h and ">Save<" in h and 'value="https://claude.com/x"' in h
        assert "Something went wrong? Get a sign-in code using a terminal instead · Back" in t.replace(" ", " ") and h.count(">Cancel<") == 1

    def test_a_device_with_no_browser_starts_on_way_two_and_has_no_back_to_a_page_that_never_opened(self):
        out = self.draw("""const r = dev({ state: 'signing_in', signin: { state: 'link', url: 'https://claude.com/x', browser: 'none' } });
          console.log(JSON.stringify({ h: ccPcRow(r) }));""")
        t = self.plain(out["h"])
        assert "Once authorized, Claude will provide a code." in t and "Back" not in t and "A sign-in page opened on" not in t

    def test_way_three_has_the_command_with_copy_the_box_save_cancel_and_back(self):
        out = self.draw("""ccOpen.msi = true; const r = dev({ state: 'needs_signin' });
          console.log(JSON.stringify({ h: ccPcRow(r) }));""")
        h = out["h"]
        t = self.plain(h)
        assert "Sign in with a terminal" in t and "open a terminal." in t and "Run this command: claude setup-token Copy" in t
        assert "Claude will provide a link. Paste the link into a browser on any device, sign in to Claude and authorize access. Copy the code, paste it here, and press Save." in t
        assert 'placeholder="Paste the code from the terminal"' in h and ">Save<" in h and ">Cancel<" in h and t.rstrip().endswith("Back") and "Uninstall" not in h

    def test_the_small_lines_move_between_the_ways_and_cancel_ends_each(self):
        out = self.draw("""const r = dev({ state: 'signing_in', signin: { state: 'link', url: 'https://claude.com/x', browser: 'opened' } });
          const a = ccWayOf('msi', r.report.claude.signin); ccSetWay('msi', 2); const b = ccWayOf('msi', r.report.claude.signin); ccSetWay('msi', 1); const c = ccWayOf('msi', r.report.claude.signin);
          await ccToTerminal('msi');
          const afterTerminal = { open: ccOpen.msi, posts: posts.slice() };
          dev({ state: 'needs_signin' });
          await ccFromTerminal('msi');
          const afterBack = { open: ccOpen.msi, way: ccWay.msi, posts: posts.map(p => p[0]) };
          ccOpen.msi = true; ccToggle('msi');
          await ccCancelSignin('msi');
          console.log(JSON.stringify({ a, b, c, afterTerminal, afterBack, closed: ccOpen.msi, last: posts[posts.length - 1] }));""")
        assert (out["a"], out["b"], out["c"]) == (1, 2, 1)
        assert out["afterTerminal"]["open"] is True and out["afterTerminal"]["posts"][0][0].endswith("/signin-cancel")
        assert out["afterBack"]["open"] is False and out["afterBack"]["way"] == 2 and any(p.endswith("/signin-start") for p in out["afterBack"]["posts"])
        assert out["closed"] is False and out["last"][0].endswith("/signin-cancel")

    def test_a_pasted_code_in_way_two_and_a_pasted_long_code_in_way_three_go_to_the_right_routes(self):
        out = self.draw("""ccPageCode.msi = 'abc#def'; await ccSavePageCode('msi');
          ccCodeText.msi = 'sk-ant-oat01-longcode'; await ccSave('msi');
          console.log(JSON.stringify({ posts, left: [ccPageCode.msi, ccCodeText.msi] }));""")
        urls = [p[0] for p in out["posts"]]
        assert urls[0].endswith("/signin-code") and urls[1].endswith("/signin") and out["left"] == ["", ""], "neither code is kept on the page"


# ============================================================================================
# Why a sign-in did not finish, and what the program printed (shapes only), 2026-10-06
# ============================================================================================
def log_text(w):
    return "\n".join(w.cap.lines)


class TestWhatTheProgramPrintedShapes:
    async def test_a_code_like_string_leaves_no_part_of_itself_in_the_shape(self):
        raw = "\r\nYour OAuth token (valid for 1 year):\r\n\r\n" + FAKE_TOKEN + "\r\nSee https://claude.com/cai/oauth/authorize?code=true&state=STATE456\r\n"
        shape = "\n".join(cc.shape_of(raw))
        for part in ("sk-ant", "fakeTOKEN", "abcde", "oat01", "claude.com", "STATE456", "authorize", "Your OAuth"):
            assert part not in shape, part
        assert f"len={len(FAKE_TOKEN)} shape=" in shape and "a-a-a9-a_a" in shape, "length and the classes are there"

    async def test_only_the_last_twenty_lines_with_text_and_each_cut_at_eighty(self):
        raw = "\r\n".join(f"line number {i}" for i in range(50)) + "\r\n" + "x1" * 200
        got = cc.shape_of(raw)
        assert len(got) == 20 and all(len(s.split("shape=", 1)[1]) <= 80 for s in got)


class TestTheEndOfASignInIsAlwaysLogged:
    async def test_a_program_that_ends_with_no_code_says_so_with_its_exit_code_and_the_shapes(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = "end", "0.4"
        h = sworld.host
        await h.start_signin()
        await settle(h)
        t = log_text(sworld)
        assert "The sign-in program ended before giving a code. (exit code 1, ran " in t
        assert "What the program printed, as shapes only (letters a, digits 9):" in t and "  len=" in t
        assert "a long code was in the output: no; the program was still running: no; a line looked like a question or a prompt: no" in t.replace("yes; a line", "no; a line") or "a line looked like a question" in t
        assert "terminal size: 500x" in t
        secrets_in(sworld, t)

    async def test_a_code_like_string_that_is_too_short_is_not_kept_and_never_appears_in_the_log(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = "short_end", "0.4"
        h = sworld.host
        await h.start_signin()
        await settle(h)
        t = log_text(sworld)
        assert not h.token_file.exists() and h.status_json(False)["state"] == "needs_signin"
        assert "The sign-in program ended before giving a code." in t and "a long code was in the output: yes" in t
        for part in ("SHORTSECRET", "VALUE123", "sk-ant-oat01"):
            assert part not in t, part

    async def test_no_code_in_time_is_logged(self, sworld, monkeypatch):
        monkeypatch.setattr(cc, "SIGNIN_TOTAL", 1.0)
        h = sworld.host
        await h.start_signin()
        await settle(h, 10)
        t = log_text(sworld)
        assert "The sign-in program gave no code in time. (exit code none: still running, ran " in t and "the program was still running: yes" in t

    async def test_a_code_that_is_not_accepted_is_logged(self, sworld, monkeypatch):
        monkeypatch.setattr(cc, "SIGNIN_CODE_WAIT", 1.5)
        h = sworld.host
        await h.start_signin()
        with pytest.raises(cc.ClaudeError):
            await h.submit_code("wrongcode999#STATE456")
        assert "The code was not accepted. (exit code " in log_text(sworld)

    async def test_a_kept_code_is_logged_without_it(self, sworld):
        h = sworld.host
        await h.start_signin()
        await h.submit_code(ONE_TIME)
        t = log_text(sworld)
        assert "The sign-in program gave a code and it was kept (ran " in t and "Claude was signed in on this device." in t
        secrets_in(sworld, t)


class TestEveryShapeOfTheCode:
    LONG = FAKE_TOKEN

    @pytest.mark.parametrize("raw,expect", [
        ("Your OAuth token (valid for 1 year):\r\n" + FAKE_TOKEN + "\r\n", (FAKE_TOKEN, False)),
        ("Your OAuth token:\r\n\r\n" + FAKE_TOKEN + "\r\n\r\nDone.", (FAKE_TOKEN, True)),
        ("\x1b[32m" + FAKE_TOKEN + "\x1b[0m\r\r\n\x1b[2K\r\nmore", (FAKE_TOKEN, True)),
        ("token: " + FAKE_TOKEN + " Store this token securely.", (FAKE_TOKEN, True)),
        ("\r\n".join(FAKE_TOKEN[i:i + 40] for i in range(0, len(FAKE_TOKEN), 40)) + "\r\n\r\nStore it.", (FAKE_TOKEN, True)),
    ])
    async def test_the_code_is_found(self, raw, expect):
        assert cc.find_token(raw) == expect

    async def test_something_that_only_starts_like_a_code_is_not_kept(self, sworld):
        assert len(cc.find_token("see sk-ant-oat01-ABC for the format")[0]) < cc.TOKEN_MIN_CHARS, "found, but too short: the loop never keeps it"

    @pytest.mark.parametrize("mode", ["inline", "noisy", "wrapped3"])
    async def test_each_shape_finishes_the_sign_in(self, sworld, mode):
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = mode, "0.4"
        h = sworld.host
        await h.start_signin()
        await settle(h)
        s = h.status_json(False)
        assert s["state"] == "ready" and h.token_file.read_text() == FAKE_TOKEN
        secrets_in(sworld, s)

    async def test_a_program_that_prints_the_code_and_then_waits_for_a_key_is_stopped_after_two_quiet_seconds(self, sworld, monkeypatch):
        monkeypatch.setattr(cc, "SIGNIN_QUIET_KEEP", 1.0)
        monkeypatch.setattr(cc, "SIGNIN_DIE_GRACE", 0.3)
        sworld.planted["FAKE_SIGNIN_MODE"], sworld.planted["FAKE_SIGNIN_DELAY"] = "alive", "0.4"
        h = sworld.host
        await h.start_signin()
        term = h.login.term
        await settle(h)
        assert h.status_json(False)["state"] == "ready" and h.token_file.read_text() == FAKE_TOKEN
        await asyncio.sleep(0.8)
        assert term is not None and not term.alive(), "the program that never ends was stopped"

    async def test_press_enter_to_continue_is_answered_once_and_a_question_never_is(self, sworld):
        sworld.planted["FAKE_SIGNIN_MODE"] = "enter"
        h = sworld.host
        await h.start_signin()
        await settle(h)
        assert h.status_json(False)["state"] == "ready"
        assert log_text(sworld).count("asked to press Enter, so Enter was pressed once") == 1
        for line in ("Continue? [y/N]", "Do you want to press Enter?", "Open the browser (y/n)?"):
            assert not cc.ENTER_PROMPT_RE.search(line), line
        for line in ("Press Enter to continue", "Approved. Press Enter to continue", "press any key"):
            assert cc.ENTER_PROMPT_RE.search(line), line
