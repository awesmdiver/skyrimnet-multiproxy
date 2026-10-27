"""Other devices: stop LM Studio or Ollama from the main device (2026-10-01).

A running AI program's line gets **Stop <Program>**: it unloads the loaded models (so the graphics memory is really freed), then
stops the program. Real HTTP servers stand in for Ollama and LM Studio; the "kill" that would end the real Ollama is replaced
by one that shuts the fake down, so no test ever touches a real program.
"""
import asyncio
import json
import time

import pytest

import courier as cr
import other_pcs as op
from tests.test_courier import env  # noqa: F401

pytestmark = pytest.mark.asyncio


def program(env, pid):
    return next(a for a in env.courier.adapters if a.id == pid)


async def report_of(env, pid):
    rep = await env.call("GET", "/courier/v1/report?fresh=1")
    return next(p for p in rep["programs"] if p["id"] == pid)


def wire_kill(env, works=True):
    """Ollama's kill: ends the fake server (like closing the real program), or does nothing (a service that comes straight back)."""
    loop = asyncio.get_running_loop()
    ollama = program(env, "ollama")
    ollama._kill = (lambda: asyncio.run_coroutine_threadsafe(env.ollama.stop(), loop).result(10)) if works else (lambda: None)
    return ollama


class TestOllamaStop:
    async def test_it_unloads_the_models_then_stops_the_program(self, env):
        wire_kill(env)
        await env.call("POST", "/courier/v1/programs/ollama/models/load", {"model": "gemma4:12b", "wait": True})
        assert "gemma4:12b" in env.ollama.loaded
        await env.call("POST", "/courier/v1/programs/ollama/stop")
        assert env.ollama.generate_calls[-1] == {"model": "gemma4:12b", "keep_alive": 0, "stream": False}, "unloaded before the program was closed"
        p = await report_of(env, "ollama")
        assert p["state"] == "stopped" and p["running"] is False and p["models"] == [], "its dot goes red and nothing is loaded"

    async def test_with_nothing_loaded_it_just_stops(self, env):
        wire_kill(env)
        await env.call("POST", "/courier/v1/programs/ollama/stop")
        assert (await report_of(env, "ollama"))["state"] == "stopped"

    async def test_stopping_a_stopped_program_is_fine(self, env):
        wire_kill(env)
        await env.call("POST", "/courier/v1/programs/ollama/stop")
        await env.call("POST", "/courier/v1/programs/ollama/stop")

    async def test_when_it_cant_be_stopped_the_row_says_so_plainly(self, env):
        wire_kill(env, works=False)
        status, msg = await env.err("POST", "/courier/v1/programs/ollama/stop")
        assert status == 409 and msg == "Ollama is running as something Courier isn't allowed to stop (a Windows service, or set to restart itself). Close it on that device."
        p = await report_of(env, "ollama")
        assert p["stop_note"] == msg and p["running"] is True
        wire_kill(env)
        await env.call("POST", "/courier/v1/programs/ollama/stop")
        assert (await report_of(env, "ollama"))["stop_note"] == "", "the note goes once it's really stopped"

    async def test_a_stopped_program_is_not_started_again_by_a_request_or_its_keep_loaded(self, env):
        ollama = wire_kill(env)
        spawned = []
        ollama._spawn = lambda *a, **k: spawned.append(a)
        await env.call("POST", "/courier/v1/programs/ollama/stop")
        for model in ("ollama/gemma4:12b", "gemma4:12b"):
            status, msg = await env.err("POST", "/v1/chat/completions", {"model": model, "messages": [{"role": "user", "content": "hi"}]})
            assert status == 503 and msg == "Ollama isn't answering right now.", (model, status, msg)
        assert spawned == [] and not any(c.get("keep_alive") == -1 for c in env.ollama.generate_calls), "nothing started it, nothing pinned a model"


class TestLmStudioStop:
    async def test_it_unloads_everything_then_stops_the_server(self, env):
        await env.call("POST", "/courier/v1/programs/lmstudio/models/load", {"model": "mistral-small-3.2-24b", "wait": True})
        assert env.lms.loaded
        env.lms.cli_calls.clear()
        await env.call("POST", "/courier/v1/programs/lmstudio/stop")
        stops = [c for c in env.lms.cli_calls if c[0] in ("unload", "server")]
        assert stops == [["unload", "--all"], ["server", "stop"]], "models first, so the graphics memory is really freed"
        p = await report_of(env, "lmstudio")
        assert p["state"] == "stopped" and p["models"] == []

    async def test_a_request_while_it_is_stopped_fails_cleanly_and_does_not_restart_it(self, env):
        await env.call("POST", "/courier/v1/programs/lmstudio/stop")
        env.lms.cli_calls.clear()
        status, msg = await env.err("POST", "/v1/chat/completions", {"model": "lmstudio/mistral-small-3.2-24b", "messages": [{"role": "user", "content": "hi"}]})
        assert status == 503 and msg == "LM Studio isn't answering right now."
        assert not [c for c in env.lms.cli_calls if c[:2] == ["server", "start"]]

    async def test_the_app_is_closed_only_if_this_couriers_start_opened_it(self, env):
        lms = program(env, "lmstudio")
        closed = []
        lms._close_app = lambda: closed.append(1)
        await env.call("POST", "/courier/v1/programs/lmstudio/stop")
        assert closed == [], "the player had it open themselves (or we can't tell): leave the app alone"
        await env.call("POST", "/courier/v1/programs/lmstudio/start")
        lms._opened_app = True
        await env.call("POST", "/courier/v1/programs/lmstudio/stop")
        assert closed == [1] and lms._opened_app is False

    async def test_start_remembers_whether_it_opened_the_app(self, env, monkeypatch):
        lms = program(env, "lmstudio")
        await env.call("POST", "/courier/v1/programs/lmstudio/stop")
        states = iter([False, True])                                     # closed before Start, open after
        monkeypatch.setattr(lms, "_app_open", lambda: next(states))
        await env.call("POST", "/courier/v1/programs/lmstudio/start")
        assert lms._opened_app is True
        monkeypatch.setattr(lms, "_close_app", lambda: None)
        await env.call("POST", "/courier/v1/programs/lmstudio/stop")
        states2 = iter([True, True])                                     # already open before Start
        lms._opened_app = False
        monkeypatch.setattr(lms, "_app_open", lambda: next(states2))
        await env.call("POST", "/courier/v1/programs/lmstudio/start")
        assert lms._opened_app is False


class TestWhoCanStop:
    async def test_the_report_offers_stop_for_lm_studio_and_ollama_only(self, env):
        rep = await env.call("GET", "/courier/v1/report")
        caps = {p["id"]: p["capabilities"] for p in rep["programs"]}
        assert caps["ollama"]["stop"] and caps["lmstudio"]["stop"] and not caps["llamacpp"].get("stop")

    async def test_llama_cpp_has_nothing_to_stop(self, env):
        status, msg = await env.err("POST", "/courier/v1/programs/llamacpp/stop")
        assert status == 400 and "doesn't support" in msg


class TestMainPc:
    async def test_a_program_that_is_stopped_is_the_same_plain_failure_as_a_pc_that_doesnt_answer(self, proxy_module):
        r = op.Remote(id="msi", kind="courier", name="msi", base="http://x", online=True)
        err = proxy_module.HTTPException(status_code=503, detail="Ollama isn't serving models right now.")
        out = proxy_module._remote_failed(r, err)
        assert isinstance(out, proxy_module.RemoteUnavailable) and out.detail == "msi isn't serving models right now."
        assert r.online is True, "the device is up; its row says which program is stopped"
        other = proxy_module._remote_failed(r, proxy_module.HTTPException(status_code=503, detail="Something else"))
        assert not isinstance(other, proxy_module.RemoteUnavailable)

    async def test_the_backup_answers_when_the_program_is_stopped(self, proxy_module, monkeypatch):
        from tests.fixtures.courier_fakes import FakeLmStudio
        lms = await FakeLmStudio().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", lms.base + "/v1")
        cfg = proxy_module._load_config()
        cfg["other_pcs_backup"] = {"enabled": True, "model": "lmstudio/mistral-small-3.2-24b"}
        proxy_module._save_config(cfg)
        r = op.Remote(id="msi", kind="courier", name="msi", base="http://x", online=True)
        monkeypatch.setattr(proxy_module.remote_svc, "match", lambda m: (r, m.split("/", 1)[1]) if m.startswith("msi/") else None)

        real = proxy_module._call_openai_compatible_direct

        async def stopped(provider, *a, **k):
            if provider == "msi":
                raise proxy_module.HTTPException(status_code=503, detail="Ollama isn't serving models right now.")
            return await real(provider, *a, **k)
        monkeypatch.setattr(proxy_module, "_call_openai_compatible_direct", stopped)
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30) as c:
            res = await c.post("/v1/chat/completions", json={"model": "msi/gemma4:12b", "messages": [{"role": "user", "content": "hello"}]})
        await lms.stop()
        assert res.status_code == 200 and res.json()["choices"][0]["message"]["content"] == "lmstudio:hello"

    async def test_the_stop_action_is_accepted_by_the_route(self, proxy_module, test_client, monkeypatch):
        seen = []

        async def fake(rid, program, action, model=""):
            seen.append((rid, program, action))
        monkeypatch.setattr(proxy_module.remote_svc, "courier_action", fake)
        assert test_client.post("/config/other-pcs/courier/action", json={"id": "msi", "program": "ollama", "action": "stop"}).json() == {"status": "ok"}
        assert seen == [("msi", "ollama", "stop")]

    async def test_the_state_no_longer_carries_a_stop_question(self, proxy_module, test_client):
        assert "stopConfirm" not in test_client.get("/config/other-pcs").json()
