"""Courier says when a main device tried to connect with the wrong pairing code: in its log, its tray menu and its window, instead of only waiting.
The door is real (127.0.0.1); the pairing codes in these tests are the Courier's own, and none of them may ever show up in the log, the status or an answer."""
from courier import COURIER_BUILD
import logging
import time

import pytest

import courier as cr
import courier_ui as ui
from tests.test_courier import env      # noqa: F401  (a running Courier, with fakes behind it)

pytestmark = pytest.mark.asyncio
WRONG = "ZZZ-999-WRONG"


def said(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def everything(caplog, env, *extra):
    """All the text a pairing code could leak into."""
    c = env.courier
    return "\n".join([r.getMessage() for r in caplog.records] + [ui.WRONG_CODE_STATUS, ui.WRONG_CODE_MENU, *extra,
                      *[i["text"] or "" for i in ui.menu_model(False, "", "CODE", True, True, True, None, c.pairing_trouble())
                        if i["key"] == "title"], *ui.trouble_lines(c.pairing_trouble())])


async def wrong(env, code=WRONG):
    return await env.err("GET", "/courier/v1/report", headers={"Authorization": f"Bearer {code}"})


class TestTheLog:
    async def test_a_wrong_code_is_one_line_with_the_address_and_never_the_code(self, env, caplog):
        caplog.set_level(logging.INFO)
        status, msg = await wrong(env)
        assert status == 403
        lines = said(caplog, "tried to connect")
        assert lines == ["A device at 127.0.0.1 tried to connect with a pairing code that doesn't match."]
        real = env.courier.settings.code
        text = everything(caplog, env, msg)
        assert WRONG not in text and real not in text and "WRONG" not in text.replace(ui.WRONG_CODE_MENU, "")

    async def test_ten_more_tries_write_nothing_then_the_count_comes_with_the_next_line_after_quiet(self, env, caplog):
        caplog.set_level(logging.INFO)
        now = [1000.0]
        env.courier._clock = lambda: now[0]
        for _ in range(11):
            await wrong(env)
            now[0] += 1
        assert len(said(caplog, "tried to connect")) == 1            # the other ten are only counted
        now[0] += cr.REFUSAL_QUIET_SECONDS + 1                        # ten minutes of quiet
        await wrong(env)
        lines = said(caplog, "tried to connect")
        assert len(lines) == 2 and lines[1].endswith("(tried 11 times)")
        await wrong(env)                                              # and quiet again afterwards
        assert len(said(caplog, "tried to connect")) == 2

    async def test_a_new_pairing_code_is_a_new_situation_so_the_next_try_is_written_again(self, env, caplog):
        caplog.set_level(logging.INFO)
        await wrong(env)
        env.courier.new_code()
        await wrong(env)
        assert len(said(caplog, "tried to connect")) == 2

    async def test_a_pc_that_connects_with_the_right_code_is_written_once(self, env, caplog):
        caplog.set_level(logging.INFO)
        env.courier._note_right_code("192.168.1.50")
        env.courier._note_right_code("192.168.1.50")
        env.courier._note_right_code("192.168.1.51")
        assert said(caplog, "connected.") == ["A device at 192.168.1.50 connected.", "A device at 192.168.1.51 connected."]

    async def test_a_request_with_no_code_is_one_line_an_hour_and_not_a_gaming_pc(self, env, caplog):
        caplog.set_level(logging.INFO)
        now = [1000.0]
        env.courier._clock = lambda: now[0]
        for _ in range(3):
            assert (await env.err("GET", "/courier/v1/report", headers={}))[0] == 403
        assert said(caplog, "asked for Courier without a pairing code.") == ["Something at 127.0.0.1 asked for Courier without a pairing code."]
        assert not said(caplog, "tried to connect")
        now[0] += cr.NO_CODE_LOG_SECONDS + 1
        await env.err("GET", "/courier/v1/report", headers={})
        assert len(said(caplog, "without a pairing code")) == 2


class TestTheStatus:
    async def test_it_says_waiting_until_a_wrong_code_is_seen_then_names_the_address(self, env):
        c = env.courier
        assert c.pairing_trouble() is None
        assert ui.menu_model(False, "", "CODE", True, True, True, None, c.pairing_trouble())[1]["text"] == f"1.{COURIER_BUILD} · Waiting for your main device"
        await wrong(env)
        t = c.pairing_trouble()
        assert t == {"address": "127.0.0.1", "new_code": False}
        assert ui.menu_model(False, "", "CODE", True, True, True, None, t)[1]["text"] == f"1.{COURIER_BUILD} · Wrong pairing code from 127.0.0.1"
        assert ui.trouble_lines(t) == ["A device at 127.0.0.1 tried to connect with an old pairing code. Update the code on your main PC."]

    async def test_it_goes_back_to_normal_after_ten_quiet_minutes_and_after_a_right_code(self, env):
        c = env.courier
        now = [1000.0]
        c._clock = lambda: now[0]
        await wrong(env)
        now[0] += cr.REFUSAL_QUIET_SECONDS + 1
        assert c.pairing_trouble() is None
        await wrong(env)
        assert c.pairing_trouble() is not None
        c._note_right_code("127.0.0.1")                              # that device connects with the right code
        assert c.pairing_trouble() is None

    async def test_a_gaming_pc_that_is_connected_wins(self, env):
        c = env.courier
        await wrong(env)
        c.last_seen, c.last_seen_ip = time.time(), "192.168.1.50"
        assert c.connected()[0] and c.pairing_trouble() is None

    async def test_no_code_at_all_does_not_change_the_status(self, env):
        await env.err("GET", "/courier/v1/report", headers={})
        assert env.courier.pairing_trouble() is None

    async def test_the_report_the_window_reads_carries_it(self, env):
        await wrong(env)
        rep = await env.call("GET", "/courier/v1/report")
        assert rep["courier"]["pairing_trouble"]["address"] == "127.0.0.1"
        text = ui.ready_window_text("KX7-42M", rep, "My main device")             # even a paired Courier shows its code then
        assert text["code"] == "KX7-42M" and text["trouble"][0].startswith("A device at 127.0.0.1 tried to connect with an old pairing code.")

    async def test_the_window_is_unchanged_without_trouble(self, env):
        rep = await env.call("GET", "/courier/v1/report")
        text = ui.ready_window_text("KX7-42M", rep, None)
        assert text["trouble"] == [] and text["code"] == "KX7-42M"


class TestTheNewCodeHint:
    async def test_after_make_new_pairing_code_the_window_says_to_enter_it(self, env):
        c = env.courier
        c.new_code()
        await wrong(env)
        t = c.pairing_trouble()
        assert t["new_code"] is True
        assert ui.trouble_lines(t)[1] == "You made a new pairing code on this device. Enter it on your main device."

    async def test_the_hint_is_only_for_an_hour(self, env):
        c = env.courier
        c.new_code()
        c.settings.set(code_made_at=time.time() - cr.NEW_CODE_HINT_SECONDS - 5)
        await wrong(env)
        assert c.pairing_trouble()["new_code"] is False and len(ui.trouble_lines(c.pairing_trouble())) == 1

    async def test_the_new_code_is_never_in_the_hint_or_the_log(self, env, caplog):
        caplog.set_level(logging.INFO)
        code = env.courier.new_code()
        await wrong(env)
        assert code not in everything(caplog, env, *ui.trouble_lines(env.courier.pairing_trouble()))
