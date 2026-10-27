"""Which graphics card each local AI uses, a Courier device's side (Routing, step 8): the `gpu_choice` block in its report, GET/POST /courier/v1/gpu, the choice reaching its
own engines, a change being pushed, and a Courier nobody touched staying exactly as it was. A real Courier on a spare port; fake cards."""
import json
import logging

import aiohttp
import pytest
import pytest_asyncio

import courier as cr
import courier_client
import courier_system as cs
import gpu_choice as gc
import other_pcs as op
from tests.fixtures.courier_fakes import free_port
from tests.test_courier import GPUS, FakeAwake, FakeRegistry

pytestmark = pytest.mark.asyncio
log = logging.getLogger("test-gpu-choice-courier")
BIG = gc.Card("GPU-aaaa", "NVIDIA GeForce RTX 5090", 32768, "nvidia")
SMALL = gc.Card("GPU-bbbb", "NVIDIA GeForce RTX 3060", 12288, "nvidia")


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback=False: None)
    e = type("E", (), {})()
    e.cards = [BIG, SMALL]
    settings = cr.CourierSettings(tmp_path / "courier.json")
    settings.set(port=free_port())
    e.settings = settings
    c = cr.Courier(settings, log, bind_ips=["127.0.0.1"], discovery_port=free_port(), keep_awake=cs.KeepAwake(FakeAwake()),
                   startup=cs.StartWithWindows("cmd", registry=FakeRegistry()), gpu_reader=lambda: GPUS, adapters=[], cards_fn=lambda: e.cards)
    gguf = tmp_path / "Den Model.gguf"
    gguf.write_bytes(b"x")
    c.local.set_folder(str(tmp_path / "models"))
    c.local.add_custom(str(gguf))
    e.model_id = c.local.installed()[0].id
    c.decisions.engine.installed = lambda key=None: True               # (an installed decision model, without the files)
    e.courier = c
    await c.start()
    e.client = courier_client.CourierClient(f"http://127.0.0.1:{settings.port}", settings.code, allow_loopback=True)
    yield e
    await c.stop()


async def call(env, method, path, body=None, expect=200):
    async with aiohttp.ClientSession() as s:
        async with s.request(method, f"http://127.0.0.1:{env.settings.port}{path}", json=body, headers={"Authorization": f"Bearer {env.settings.code}"}) as r:
            data = await r.json(content_type=None)
            assert r.status == expect, (r.status, data)
            return data


async def test_the_report_carries_the_cards_and_the_ais(env):
    rep = await call(env, "GET", "/courier/v1/report")
    g = rep["gpu_choice"]
    assert [c["shortName"] for c in g["cards"]] == ["RTX 5090", "RTX 3060"]
    assert [a["key"] for a in g["ais"]] == [f"local:{env.model_id}", "decisions"]
    assert all(a["choice"] is None and a["note"] == "" for a in g["ais"])
    assert await call(env, "GET", "/courier/v1/gpu") == g


async def test_nobody_touched_it_means_no_trace_in_the_settings_file(env):
    await call(env, "GET", "/courier/v1/report")
    assert "gpu_choices" not in json.loads(env.settings.path.read_text(encoding="utf-8"))


async def test_a_choice_is_saved_and_reaches_the_engines(env):
    d = await env.client.gpu_post({"ai": "decisions", "cardId": "GPU-bbbb"})
    assert d["ais"][1]["choice"]["id"] == "GPU-bbbb"
    await env.client.gpu_post({"ai": f"local:{env.model_id}", "cardId": "GPU-aaaa"})
    saved = json.loads(env.settings.path.read_text(encoding="utf-8"))["gpu_choices"]
    assert saved == {"decisions": gc.choice_of(SMALL), f"local:{env.model_id}": gc.choice_of(BIG)}
    assert env.courier.decisions.engine._gpu_choice_fn()["id"] == "GPU-bbbb"           # read fresh from courier.json at each start
    assert env.courier.local._choice_for(env.model_id)["id"] == "GPU-aaaa"
    assert env.courier.voice.engine._gpu_choice_fn() is None
    d = await env.client.gpu_post({"ai": "decisions", "cardId": None})
    assert d["ais"][1]["choice"] is None
    assert "decisions" not in json.loads(env.settings.path.read_text(encoding="utf-8"))["gpu_choices"]


async def test_refusals_are_plain(env):
    async with aiohttp.ClientSession() as s:
        for body, msg in (({"ai": "voice", "cardId": "GPU-aaaa"}, "That AI isn't installed on that device."),
                          ({"ai": "decisions", "cardId": "GPU-nope"}, "That graphics card isn't in that device anymore.")):
            async with s.post(f"http://127.0.0.1:{env.settings.port}/courier/v1/gpu", json=body, headers={"Authorization": f"Bearer {env.settings.code}"}) as r:
                assert r.status == 404 and (await r.json())["error"]["message"] == msg


async def test_a_change_is_pushed_and_a_pairing_code_is_still_needed(env):
    before = env.courier.signature(await env.courier.report(fresh=True))
    await env.client.gpu_post({"ai": "decisions", "cardId": "GPU-aaaa"})
    assert env.courier.signature(await env.courier.report(fresh=True)) != before
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{env.settings.port}/courier/v1/gpu") as r:
            assert r.status in (401, 403)


async def test_a_card_that_vanished_is_reported_with_its_note(env):
    await env.client.gpu_post({"ai": "decisions", "cardId": "GPU-bbbb"})
    env.cards = [BIG, gc.Card("GPU-cccc", "NVIDIA GeForce RTX 4070", 12288, "nvidia")]
    ai = (await call(env, "GET", "/courier/v1/gpu"))["ais"][1]
    assert ai["note"] == "The graphics card you chose (NVIDIA GeForce RTX 3060) isn't in this device anymore, so Automatic is used."
