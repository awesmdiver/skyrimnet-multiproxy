"""A saved device whose pairing code changed (2026-10-05): it answers 403, so the row asks for the new code instead of saying "not answering",
the device is not shown as offline-red, and Update code verifies the code on that device before saving it. No code appears in any answer or log line."""
import logging

import pytest

import other_pcs as op
from tests.test_other_pcs_courier import allow_hosts, client, until, paired  # noqa: F401
from tests.test_courier import env  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _pair(env, client, proxy_module):
    r = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{env.courier.settings.port}",
                                                                             "code": env.courier.settings.code, "nickname": "s2"})
    assert r.status_code == 200
    rem = proxy_module.remote_svc.get("s2")
    assert await until(lambda: rem.report is not None)
    return rem


async def _code_changed(env, rem):
    """The Courier makes a new code (as a restarted one would); this device still holds the old one."""
    old = rem.code
    env.courier.new_code()
    assert await until(lambda: rem.online is False and rem.code_changed, 15)
    return old


class TestCodeChanged:
    async def test_a_refused_code_is_reachable_not_offline_and_asks_for_the_new_code(self, env, client, proxy_module, allow_hosts):
        rem = await _pair(env, client, proxy_module)
        await _code_changed(env, rem)
        s = next(x for x in (await client.get("/config/other-pcs")).json()["servers"] if x["id"] == "s2")
        assert s["codeChanged"] is True and s["online"] is False and "isn't answering" not in s["error"]
        await proxy_module.remote_svc.stop_polling()

    async def test_a_wrong_new_code_is_not_saved_and_the_right_one_is(self, env, client, proxy_module, allow_hosts, caplog):
        rem = await _pair(env, client, proxy_module)
        old = await _code_changed(env, rem)
        real = env.courier.settings.code
        with caplog.at_level(logging.DEBUG):
            bad = await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": "ZZZ-ZZZ"})
            assert bad.status_code == 403 and bad.json()["detail"] == "Wrong pairing code."
            assert rem.code == old and "ZZZ" not in bad.text
            ok = await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": real})
        assert ok.status_code == 200 and ok.json() == {"status": "ok"}
        assert real not in ok.text and "ZZZ" not in caplog.text and real not in caplog.text
        assert rem.code == real and rem.id == "s2" and rem.instance == env.courier.settings.id and rem.online is True and rem.code_changed is False
        await proxy_module.remote_svc.stop_polling()

    async def test_change_pairing_code_works_on_a_healthy_pc(self, env, client, proxy_module, allow_hosts):
        rem = await _pair(env, client, proxy_module)
        real = env.courier.settings.code
        r = await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": real.lower()})
        assert r.status_code == 200 and rem.online is True
        assert (await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": ""})).status_code == 400
        assert (await client.post("/config/other-pcs/courier/set-code", json={"id": "nobody", "code": real})).status_code == 404
        await proxy_module.remote_svc.stop_polling()

    async def test_the_row_text_and_button_are_in_the_screen(self):
        src = open(op.__file__.replace("other_pcs.py", "other_pcs_ui.py"), encoding="utf-8").read()
        for s in ("This device\\'s pairing code has changed. Type the new code shown in its Courier window.", "Update code", "Change pairing code…"):
            assert s in src
