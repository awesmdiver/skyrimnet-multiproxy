"""Unpairing from both sides (2026-10-03): Remove on the main device tells a reachable Courier to unpair first, the Courier's tray
can unpair itself, and both land in the same state (not paired, a new code). Only the paired main device (the right code) can ask."""
from types import SimpleNamespace

import pytest

import courier as cr
import courier_ui as ui
import other_pcs as op
from tests.test_courier import env  # noqa: F401  (the running Courier fixture)
from tests.test_other_pcs_courier import allow_hosts, client, paired, until  # noqa: F401


@pytest.mark.asyncio
class TestCourierSide:
    async def test_an_unpair_call_without_the_code_is_refused_and_changes_nothing(self, env):
        env.courier.settings.note_pairing("Den PC")
        code = env.courier.settings.code
        status, msg = await env.err("POST", "/courier/v1/pairing/unpair", {}, headers={})
        assert status == 403 and "pairing code" in msg
        assert env.courier.settings.paired_name() == "Den PC" and env.courier.settings.code == code

    async def test_unpair_clears_the_record_and_makes_a_new_code(self, env):
        env.courier.settings.note_pairing("Den PC")
        old = env.courier.settings.code
        assert await env.call("POST", "/courier/v1/pairing/unpair") == {"status": "unpaired"}
        assert env.courier.settings.paired_name() is None and env.courier.settings.code != old

    async def test_make_new_pairing_code_still_unpairs_too(self, env):
        env.courier.settings.note_pairing("Den PC")
        await env.call("POST", "/courier/v1/pairing/new")
        assert env.courier.settings.paired_name() is None


class TestTray:
    def test_the_unpair_entry_shows_only_while_paired_and_sits_above_make_new_code(self):
        texts = lambda m: [i["text"] for i in m]  # noqa: E731
        paired = texts(ui.menu_model(False, "", "AAA-111", True, True, paired_name="Den PC"))
        assert "Unpair from Den PC" in paired and paired.index("Unpair from Den PC") < paired.index("Make new pairing code")
        assert not any(t and t.startswith("Unpair") for t in texts(ui.menu_model(False, "", "AAA-111", True, True)))

    def test_the_confirm_words(self):
        assert op.UNPAIR_CONFIRM == {"title": "Unpair from {name}?",
                                     "question": "Your main PC will stop using this computer. You can pair it again later with a new code.",
                                     "cancel": "Cancel", "confirm": "Unpair"}
        assert "confirm-unpair" in open(cr.__file__, encoding="utf-8").read()

    def test_cancelling_does_nothing_and_confirming_unpairs(self, monkeypatch, tmp_path):
        made, shown = [], []
        tray = ui.Tray.__new__(ui.Tray)
        tray.config_dir, tray.loop = tmp_path, None
        tray.courier = SimpleNamespace(new_code=lambda: made.append(1))
        tray.do_show_code = lambda: shown.append(1)
        calls = []
        monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), SimpleNamespace(returncode=1))[1])
        tray.do_unpair()
        assert calls[0][-3] == "confirm-unpair" and shown == []
        monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=0))
        tray.do_unpair()
        assert shown == [1]


@pytest.mark.asyncio
class TestGamingPcSide:
    async def test_remove_with_a_reachable_courier_unpairs_it_first(self, env, client, proxy_module, allow_hosts):  # noqa: F811
        r = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{env.courier.settings.port}",
                                                                                 "code": env.courier.settings.code, "nickname": "s2"})
        assert r.status_code == 200
        assert await until(lambda: proxy_module.remote_svc.get("s2").report is not None)
        env.courier.settings.note_pairing("Den PC")
        old = env.courier.settings.code
        res = await client.post("/config/other-pcs/servers/remove", json={"id": "s2"})
        assert res.json() == {"status": "removed"} and proxy_module.remote_svc.all() == []
        assert env.courier.settings.paired_name() is None and env.courier.settings.code != old
        await proxy_module.remote_svc.stop_polling()

    async def test_remove_with_an_unreachable_courier_still_removes_and_says_so(self, env, client, proxy_module, allow_hosts):  # noqa: F811
        r = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{env.courier.settings.port}",
                                                                                 "code": env.courier.settings.code, "nickname": "s2"})
        assert r.status_code == 200
        assert await until(lambda: proxy_module.remote_svc.get("s2").report is not None)
        await env.courier.stop()                                                     # off / asleep
        res = await client.post("/config/other-pcs/servers/remove", json={"id": "s2"})
        assert res.json() == {"status": "removed", "note": op.REMOVED_NOT_REACHED} and proxy_module.remote_svc.all() == []
        assert op.REMOVED_NOT_REACHED == ("Removed. That device wasn't reachable, so it still thinks it's paired. "
                                          "Choose Unpair from its tray menu, or pair it again.")
        await proxy_module.remote_svc.stop_polling()

    async def test_removing_an_unknown_or_plain_pc_just_removes(self, client):  # noqa: F811
        assert (await client.post("/config/other-pcs/servers/remove", json={"id": "nobody"})).json() == {"status": "removed"}
