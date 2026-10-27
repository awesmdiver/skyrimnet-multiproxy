"""Removing a device that refuses the pairing code always works (queue: removing-a-pc-that-refuses-the-pairing-code-always-works, 2026-10-05).
It used to fail with a 500 ("Extra data: line 1 column 4") when the other end answered with a body that was not JSON, and the device stayed in the list.
Stand-in servers on 127.0.0.1 only; the codes used are made up."""
import asyncio
import time

import pytest
from aiohttp import web

import courier_client as cc
import other_pcs as op
from tests.test_courier import env  # noqa: F401  (the running Courier fixture)
from tests.test_other_pcs_courier import allow_hosts, client, paired, until  # noqa: F401


async def serve(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


async def plain_403(request):
    return web.Response(status=403, text="403 Forbidden", content_type="text/plain")


async def never(request):
    await asyncio.sleep(30)
    return web.Response(text="late")


async def odd_200(request):
    return web.Response(status=200, text="not json at all {", content_type="text/plain")


def courier_client(port):
    return cc.CourierClient(f"127.0.0.1:{port}", "AAA-111", allow_loopback=True)


@pytest.mark.asyncio
class TestTheClient:
    async def test_a_plain_text_refusal_becomes_the_pairing_code_message(self):
        runner, port = await serve(plain_403)
        try:
            with pytest.raises(op.OtherPcsError) as e:
                await courier_client(port).unpair()
            assert e.value.status == 403 and "Wrong pairing code." in str(e.value.message)
        finally:
            await runner.cleanup()

    async def test_a_body_that_is_not_json_never_crashes_any_caller(self):
        runner, port = await serve(odd_200)
        try:
            for call in (lambda c: c.fetch_report(), lambda c: c.unpair(), lambda c: c.log(), lambda c: c.action("llama", "start")):
                with pytest.raises(op.OtherPcsError):
                    await call(courier_client(port))
        finally:
            await runner.cleanup()

    async def test_a_plain_text_error_that_is_not_about_the_code_says_the_status(self):
        async def boom(request):
            return web.Response(status=500, text="oops", content_type="text/plain")
        runner, port = await serve(boom)
        try:
            with pytest.raises(op.OtherPcsError) as e:
                await courier_client(port).fetch_report()
            assert e.value.status == 500 and "(500)" in str(e.value.message)
        finally:
            await runner.cleanup()


@pytest.mark.asyncio
class TestTheCourierItself:
    async def test_a_refused_code_is_its_normal_json_error(self, env):  # noqa: F811
        status, msg = await env.err("POST", "/courier/v1/pairing/unpair", {}, headers={})
        assert status == 403 and "pairing code" in msg
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{env.courier.settings.port}/courier/v1/pairing/unpair", headers={"Authorization": "Bearer WRONG"}) as r:
                body = await r.json()
        assert r.status == 403 and body["error"]["message"].startswith("Wrong pairing code")


@pytest.mark.asyncio
class TestRemoveAlwaysRemoves:
    async def _saved(self, proxy_module, client, port):
        r = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{port}", "code": "x", "nickname": "s2"})
        return r

    async def _with_client(self, proxy_module, port):
        svc = proxy_module.remote_svc
        svc._load()
        svc._remotes["s2"] = op.Remote(id="s2", kind="courier", name="s2", base=f"http://127.0.0.1:{port}", code="AAA-111")
        svc._remotes["s2"].client = courier_client(port)
        return svc

    async def test_a_courier_that_refuses_with_plain_text_still_removes_and_says_not_reached(self, proxy_module, client, allow_hosts):  # noqa: F811
        runner, port = await serve(plain_403)
        try:
            await self._with_client(proxy_module, port)
            res = await client.post("/config/other-pcs/servers/remove", json={"id": "s2"})
            assert res.status_code == 200 and res.json() == {"status": "removed", "note": op.REMOVED_NOT_REACHED}
            assert proxy_module.remote_svc.get("s2") is None
        finally:
            await runner.cleanup()

    async def test_any_other_trouble_in_the_unpair_attempt_is_still_just_not_reached(self, proxy_module, client, allow_hosts):  # noqa: F811
        svc = await self._with_client(proxy_module, 1)

        async def explode():
            raise ValueError("Extra data: line 1 column 4 (char 3)")
        svc._remotes["s2"].client.unpair = explode
        res = await client.post("/config/other-pcs/servers/remove", json={"id": "s2"})
        assert res.status_code == 200 and res.json()["note"] == op.REMOVED_NOT_REACHED and proxy_module.remote_svc.get("s2") is None

    async def test_a_courier_that_never_answers_does_not_hold_the_click_up(self, proxy_module, client, allow_hosts, monkeypatch):  # noqa: F811
        monkeypatch.setattr(op, "UNPAIR_LIMIT", 0.5)
        runner, port = await serve(never)
        try:
            svc = await self._with_client(proxy_module, port)

            async def hang():
                await asyncio.sleep(30)
            svc._remotes["s2"].client.unpair = hang
            t0 = time.monotonic()
            res = await client.post("/config/other-pcs/servers/remove", json={"id": "s2"})
            assert time.monotonic() - t0 < 3 and res.json()["note"] == op.REMOVED_NOT_REACHED and proxy_module.remote_svc.get("s2") is None
        finally:
            await runner.cleanup()


@pytest.mark.asyncio
class TestTheOtherRoutes:
    async def test_new_code_and_set_code_answer_with_a_clear_message_when_the_other_end_misbehaves(self, proxy_module, client, allow_hosts):  # noqa: F811
        runner, port = await serve(odd_200)
        try:
            svc = proxy_module.remote_svc
            svc._load()
            svc._remotes["s2"] = op.Remote(id="s2", kind="courier", name="s2", base=f"http://127.0.0.1:{port}", code="AAA-111")
            svc._remotes["s2"].client = courier_client(port)
            r = await client.post("/config/other-pcs/courier/new-code", json={"id": "s2"})
            assert r.status_code in (400, 403, 502) and r.json()["detail"], "a message, never a bare 500"
            r = await client.post("/config/other-pcs/courier/set-code", json={"id": "s2", "code": "BBB-222"})
            assert r.status_code != 500
            assert proxy_module.remote_svc.get("s2") is not None, "nothing was changed"
        finally:
            await runner.cleanup()
