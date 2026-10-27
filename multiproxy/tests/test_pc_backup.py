"""A backup when one of your other devices doesn't answer (2026-10-01).

A request for `<nickname>/<model>` is answered by the backup ONLY if nothing has gone back to SkyrimNet yet: the device refused, didn't
answer in time, is already marked offline, or dropped before its first word. A device that drops after words have streamed back is NOT swapped
(a stream can't be rewound): that stream ends with a real error, the device is marked offline, and the NEXT request goes to the backup. A real
error from the model is never a reason to switch. Real aiohttp servers stand in for the other device (every way of going quiet) and for the
backup (a fake LM Studio).
"""
import asyncio
import json
import time

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

from tests.fixtures.courier_fakes import FakeLmStudio, free_port

BACKUP = "lmstudio/google/gemma-4-e4b"


class FakePc:
    """Another device's AI server (an OpenAI-style one added by address) that can go quiet in every way."""

    def __init__(self):
        self.port = free_port()
        self.mode = "ok"
        self.calls = 0
        self.runner = None

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/models", self.models)
        app.router.add_post("/v1/chat/completions", self.chat)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def models(self, request):
        return web.json_response({"data": [{"id": "m1"}]})

    async def chat(self, request):
        self.calls += 1
        body = await request.json()
        mode = self.mode
        if mode == "hang":
            await asyncio.sleep(30)
        if mode == "drop-first":
            request.transport.abort()
            return web.Response(status=500)
        if mode == "error400":
            return web.json_response({"error": {"message": "That request wasn't valid for this model."}}, status=400)
        if mode == "error500":
            return web.json_response({"error": {"message": "The model crashed."}}, status=500)
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for piece in ("Hello ", "from ", "the device"):
                await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': piece}}]})}\n\n".encode())
                await asyncio.sleep(0.05)
                if mode == "drop-mid" and piece == "from ":
                    await asyncio.sleep(0.1)
                    request.transport.abort()
                    return resp
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello from the device"}, "finish_reason": "stop"}]})


@pytest_asyncio.fixture
async def env(proxy_module, monkeypatch):
    pc = await FakePc().start()
    lms = await FakeLmStudio().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", lms.base + "/v1")
    monkeypatch.setattr(proxy_module, "BACKUP_FIRST_WORD_SECONDS", 0.4)
    monkeypatch.setattr(proxy_module, "BACKUP_REPLY_SECONDS", 0.4)
    monkeypatch.setattr(proxy_module, "BACKUP_CONNECT_SECONDS", 1.0)
    await proxy_module.remote_svc.add_openai(pc.base, "den-pc")
    e = type("Env", (), {})()
    e.pc, e.lms, e.pm = pc, lms, proxy_module
    e.remote = proxy_module.remote_svc.get("den-pc")
    e.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    yield e
    await pc.stop()
    await lms.stop()


def set_backup(env, enabled=True, model=BACKUP):
    cfg = env.pm._load_config()
    cfg["other_pcs_backup"] = {"enabled": enabled, "model": model}
    env.pm._save_config(cfg)


def body(model="den-pc/m1", stream=False, text="hello"):
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": text}]}


async def sse(client, payload):
    """(status, [events as text])"""
    events = []
    async with client.stream("POST", "/v1/chat/completions", json=payload) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                events.append(line[6:])
        return r.status_code, events


def words(events):
    out = ""
    for e in events:
        if e != "[DONE]":
            d = json.loads(e)
            out += (d.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
    return out


pytestmark = pytest.mark.asyncio


class TestWhenTheBackupAnswers:
    async def test_a_pc_that_is_off_hands_the_request_to_the_backup_in_the_same_shape(self, env):
        set_backup(env)
        await env.pc.stop()
        r = await env.client.post("/v1/chat/completions", json=body())
        d = r.json()
        assert r.status_code == 200 and d["choices"][0]["message"]["content"] == "lmstudio:hello" and d["object"] == "chat.completion"
        assert d["choices"][0]["finish_reason"] and "usage" in d
        assert env.lms.chat_calls[-1]["model"] == "google/gemma-4-e4b", "the backup got the request, with its own model id"
        assert env.remote.online is False

    async def test_streaming_is_answered_by_the_backup_the_same_way(self, env):
        set_backup(env)
        await env.pc.stop()
        status, events = await sse(env.client, body(stream=True))
        assert status == 200 and words(events) == "lmstudio:hello" and events[-1] == "[DONE]"

    async def test_a_pc_that_hangs_is_a_timeout_before_answering_and_the_backup_takes_over(self, env):
        set_backup(env)
        env.pc.mode = "hang"
        t0 = time.monotonic()
        r = await env.client.post("/v1/chat/completions", json=body())
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "lmstudio:hello" and time.monotonic() - t0 < 5
        env.pc.mode = "ok"
        env.remote.retry_at = 0
        env.remote.online = True
        env.pc.mode = "hang"
        status, events = await sse(env.client, body(stream=True))
        assert status == 200 and words(events) == "lmstudio:hello"

    async def test_a_pc_that_drops_before_the_first_word_is_covered_too(self, env):
        set_backup(env)
        env.pc.mode = "drop-first"
        assert (await env.client.post("/v1/chat/completions", json=body())).json()["choices"][0]["message"]["content"] == "lmstudio:hello"
        env.remote.retry_at, env.remote.online = 0, True
        status, events = await sse(env.client, body(stream=True))
        assert status == 200 and words(events) == "lmstudio:hello"

    async def test_an_offline_pc_is_not_even_contacted_until_its_wait_is_over(self, env):
        set_backup(env)
        env.remote.online = False
        env.remote.retry_at = time.time() + 20
        r = await env.client.post("/v1/chat/completions", json=body())
        assert r.json()["choices"][0]["message"]["content"] == "lmstudio:hello" and env.pc.calls == 0
        status, events = await sse(env.client, body(stream=True))
        assert words(events) == "lmstudio:hello" and env.pc.calls == 0

    async def test_the_backup_is_what_speeds_and_health_record_not_the_silent_pc(self, env):
        set_backup(env)
        env.remote.online = False
        env.remote.retry_at = time.time() + 20
        await env.client.post("/v1/chat/completions", json=body())
        models = (await env.client.get("/stats/speeds?range=today")).json()["models"]
        assert [(m["model"], m["where"]) for m in models] == [("google/gemma-4-e4b", "This device")], "no failed call was recorded against the device"
        cards = (await env.client.get("/stats/speeds/health")).json()["cards"]
        assert [c["provider"] for c in cards] == ["LM Studio"]

    async def test_the_switch_is_logged_plainly(self, env, caplog):
        set_backup(env)
        env.remote.online, env.remote.retry_at = False, time.time() + 20
        with caplog.at_level("WARNING", logger="proxy"):
            await env.client.post("/v1/chat/completions", json=body())
        assert any("den-pc didn't answer -- answering this one with the backup, lmstudio/google/gemma-4-e4b" in r.message for r in caplog.records)


class TestComingBack:
    async def test_after_its_wait_the_pc_is_tried_again_and_used_the_moment_it_answers(self, env):
        set_backup(env)
        env.remote.online, env.remote.retry_at = False, time.time() + 20
        await env.client.post("/v1/chat/completions", json=body())
        assert env.pc.calls == 0
        env.remote.retry_at = time.time() - 1                        # the 30 seconds are up
        r = await env.client.post("/v1/chat/completions", json=body())
        assert r.json()["choices"][0]["message"]["content"] == "Hello from the device" and env.pc.calls == 1
        assert env.remote.online is True
        assert (await env.client.post("/v1/chat/completions", json=body())).json()["choices"][0]["message"]["content"] == "Hello from the device"

    async def test_a_failed_retry_restarts_the_30_second_wait(self, env):
        set_backup(env)
        await env.pc.stop()
        env.remote.online, env.remote.retry_at = False, time.time() - 1
        before = time.time()
        r = await env.client.post("/v1/chat/completions", json=body())
        assert r.json()["choices"][0]["message"]["content"] == "lmstudio:hello"
        assert env.remote.retry_at >= before + 29, "tried once, failed, waits another 30 s"

    async def test_going_offline_starts_the_wait_and_coming_back_clears_it(self, env):
        env.pm.remote_svc.mark(env.remote, False, "gone")
        assert env.remote.retry_at > time.time() + 25
        env.pm.remote_svc.mark(env.remote, True)
        assert env.remote.retry_at == 0.0


class TestWhenItDropsMidStream:
    async def test_no_switch_a_real_error_ends_the_reply_the_pc_goes_offline_and_the_next_one_uses_the_backup(self, env):
        set_backup(env)
        env.pc.mode = "drop-mid"
        status, events = await sse(env.client, body(stream=True))
        assert status == 200
        assert words(events).startswith("Hello"), "the words that already went back stand"
        assert events[-1] != "[DONE]" and json.loads(events[-1])["error"]["message"] == "den-pc stopped answering partway."
        assert env.lms.chat_calls == [], "the backup never saw this one: two models in one line would be garbage"
        assert env.remote.online is False
        env.pc.mode = "ok"
        status, events = await sse(env.client, body(stream=True))
        assert words(events) == "lmstudio:hello" and env.pc.calls == 1, "the next request goes to the backup without trying the device"

    async def test_with_no_backup_a_mid_stream_drop_ends_cleanly_exactly_as_before(self, env):
        env.pc.mode = "drop-mid"
        status, events = await sse(env.client, body(stream=True))
        assert events[-1] == "[DONE]" and not any('"error"' in e for e in events)


class TestRealModelErrorsAreNotAReasonToSwitch:
    @pytest.mark.parametrize("mode,code", [("error400", 400), ("error500", 500)])
    async def test_the_pcs_own_error_comes_back_as_it_is(self, env, mode, code):
        set_backup(env)
        env.pc.mode = mode
        r = await env.client.post("/v1/chat/completions", json=body())
        assert r.status_code == code and env.lms.chat_calls == []
        assert env.remote.online is not False, "the device answered: it isn't offline"
        status, events = await sse(env.client, body(stream=True))
        assert status == code and env.lms.chat_calls == []


class TestSwitchOff:
    async def test_off_is_todays_behaviour_a_plain_error_so_skyrimnets_own_fallback_can_act(self, env):
        set_backup(env, enabled=False)
        await env.pc.stop()
        r = await env.client.post("/v1/chat/completions", json=body())
        assert r.status_code == 502 and env.lms.chat_calls == []
        assert "den-pc isn't answering" in json.dumps(r.json())

    async def test_nothing_picked_is_the_same(self, env):
        set_backup(env, enabled=True, model="")
        await env.pc.stop()
        assert (await env.client.post("/v1/chat/completions", json=body())).status_code == 502

    async def test_another_pcs_model_can_never_be_the_backup(self, env):
        set_backup(env, model="den-pc/m1")
        assert env.pm._backup_model() is None
        r = await env.client.post("/config/other-pcs/backup", json={"enabled": True, "model": "den-pc/m1"})
        assert r.status_code == 400 and r.json()["detail"] == "Pick a model on this device or in the cloud, not one on another device."

    async def test_models_that_are_not_on_the_pc_are_unaffected_by_the_backup(self, env):
        set_backup(env)
        r = await env.client.post("/v1/chat/completions", json=body(model=BACKUP))
        assert r.json()["choices"][0]["message"]["content"] == "lmstudio:hello" and env.pc.calls == 0


class TestTheCardsRoutes:
    async def test_saving_and_reading_the_choice(self, env):
        r = await env.client.post("/config/other-pcs/backup", json={"enabled": True, "model": BACKUP})
        assert r.json() == {"enabled": True, "model": BACKUP, "active": True}
        state = (await env.client.get("/config/other-pcs")).json()
        assert state["backup"] == {"enabled": True, "model": BACKUP, "active": True}
        off = await env.client.post("/config/other-pcs/backup", json={"enabled": False, "model": BACKUP})
        assert off.json()["active"] is False and env.pm._load_config()["other_pcs_backup"]["model"] == BACKUP, "the pick is kept while off"

    async def test_the_options_are_every_ready_model_on_this_pc_and_the_cloud_never_another_pc(self, env, monkeypatch):
        async def providers():
            return [
                {"id": "claude", "name": "Claude (Anthropic)", "testable": True, "models": [["claude-sonnet-5-5", "Sonnet", ""]]},
                {"id": "local", "name": "Local device", "testable": True, "models": [["local/gemma-4-12b", "Gemma", ""]]},
                {"id": "lmstudio", "name": "LM Studio", "testable": True, "models": [["lmstudio/google/gemma-4-e4b", "g", ""]]},
                {"id": "remote", "name": "Other devices", "testable": True, "models": [["den-pc/m1", "m1", ""]]},
                {"id": "gemini", "name": "Gemini (API key)", "testable": False, "models": [["gemini/x", "x", ""]]},
            ]
        monkeypatch.setattr(env.pm, "_dashboard_providers", providers)
        opts = (await env.client.get("/config/other-pcs/backup-options")).json()["options"]
        assert opts == [{"id": "claude-sonnet-5-5", "label": "claude-sonnet-5-5 (Claude)"}, {"id": "local/gemma-4-12b", "label": "local/gemma-4-12b (this device)"},
                        {"id": "lmstudio/google/gemma-4-e4b", "label": "lmstudio/google/gemma-4-e4b (LM Studio)"}]

    async def test_a_pc_being_covered_says_so_in_the_state(self, env):
        set_backup(env)
        env.remote.online = False
        row = next(s for s in (await env.client.get("/config/other-pcs")).json()["servers"] if s["id"] == "den-pc")
        assert row["coveredBy"] == BACKUP
        env.remote.online = True
        row = next(s for s in (await env.client.get("/config/other-pcs")).json()["servers"] if s["id"] == "den-pc")
        assert row["coveredBy"] is None
        set_backup(env, enabled=False)
        env.remote.online = False
        row = next(s for s in (await env.client.get("/config/other-pcs")).json()["servers"] if s["id"] == "den-pc")
        assert row["coveredBy"] is None
