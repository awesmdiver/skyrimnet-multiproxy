"""The Overview (and the Routing view's list of reachable AIs) keep a short shared answer. A device that was just paired again must not stay
"Needs the new pairing code" there (2026-10-22): a change to a device drops those answers, and an old answer is never handed out stale."""
import asyncio

import pytest

import poll_cache
from tests.test_other_pcs_courier import allow_hosts, client, until  # noqa: F401
from tests.test_other_pcs_set_code import _code_changed, _pair
from tests.test_courier import env  # noqa: F401

pytestmark = pytest.mark.asyncio


def _row(overview: dict) -> str:
    """The word the Overview shows for the paired device 's2'."""
    text = repr(overview)
    return "Needs the new pairing code" if "Needs the new pairing code" in text else ("Offline" if "'Offline'" in text else "other")


class TestOverviewAfterANewCode:
    async def test_the_row_is_right_at_once_after_the_code_is_accepted(self, env, client, proxy_module, allow_hosts):
        rem = await _pair(env, client, proxy_module)
        await _code_changed(env, rem)
        first = (await client.get("/stats/overview")).json()
        assert _row(first) == "Needs the new pairing code"            # (this also fills the shared answer)
        ok = await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": env.courier.settings.code})
        assert ok.status_code == 200
        after = (await client.get("/stats/overview")).json()
        assert _row(after) == "other" and "Needs the new pairing code" not in repr(after)
        await proxy_module.remote_svc.stop_polling()

    async def test_a_wrong_code_changes_nothing_and_unpairing_is_seen_at_once(self, env, client, proxy_module, allow_hosts):
        rem = await _pair(env, client, proxy_module)
        await _code_changed(env, rem)
        assert _row((await client.get("/stats/overview")).json()) == "Needs the new pairing code"
        assert (await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": "ZZZ-ZZZ"})).status_code == 403
        assert _row((await client.get("/stats/overview")).json()) == "Needs the new pairing code"
        await client.post("/config/other-pcs/servers/remove", json={"id": "s2"})
        assert "s2" not in repr((await client.get("/stats/overview")).json().get("devices", ""))
        assert "Needs the new pairing code" not in repr((await client.get("/stats/overview")).json())
        await proxy_module.remote_svc.stop_polling()

    async def test_the_other_devices_tab_reads_the_same_state(self, env, client, proxy_module, allow_hosts):
        rem = await _pair(env, client, proxy_module)
        await _code_changed(env, rem)
        await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": env.courier.settings.code})
        s = next(x for x in (await client.get("/config/other-pcs")).json()["servers"] if x["id"] == "s2")
        assert s["online"] is True and s["codeChanged"] is False
        await proxy_module.remote_svc.stop_polling()


class _Clock:
    """poll_cache's own clock only (the event loop keeps the real one)."""
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def __getattr__(self, name):
        import time
        return getattr(time, name)


class TestSharedAnswerAge:
    async def test_an_old_answer_is_not_handed_out_stale(self, monkeypatch):
        poll_cache.forget()
        clock = _Clock()
        monkeypatch.setattr(poll_cache, "time", clock)
        n = {"v": 0}

        async def make():
            n["v"] += 1
            return n["v"]

        kw = dict(stale_ok=True, max_stale=20.0)
        assert await poll_cache.shared("k-age", lambda v: 3.0, make, **kw) == 1
        clock.t += 10                                              # run out, but young: handed out at once, one background refresh
        assert await poll_cache.shared("k-age", lambda v: 3.0, make, **kw) == 1
        await asyncio.sleep(0.05)
        clock.t += 60                                              # a page opened again after a minute: it waits for a fresh picture
        assert await poll_cache.shared("k-age", lambda v: 3.0, make, **kw) == 3
        poll_cache.forget()

    async def test_without_a_limit_the_old_behaviour_is_kept(self, monkeypatch):
        poll_cache.forget()
        clock = _Clock()
        monkeypatch.setattr(poll_cache, "time", clock)
        n = {"v": 0}

        async def make():
            n["v"] += 1
            return n["v"]

        assert await poll_cache.shared("k-old", lambda v: 3.0, make, stale_ok=True) == 1
        clock.t += 600
        assert await poll_cache.shared("k-old", lambda v: 3.0, make, stale_ok=True) == 1
        poll_cache.forget()


class TestPageScript:
    async def test_opening_the_overview_paints_what_the_server_says_now(self):
        import overview_ui
        src = open(overview_ui.__file__, encoding="utf-8").read()
        body = src.split("function ovOpened() {", 1)[1].split("}", 1)[0]
        assert "ovLoad(true)" in body
