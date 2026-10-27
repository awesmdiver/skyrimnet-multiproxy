"""SkyrimNet's model check, GET /v1/models, answers from lists MultiProxy already holds (2026-10-02).

Asking Ollama, LM Studio, Antigravity and Codex took 0.1 to 1.3 s each, in a row, about once per call. Now each keeps its last list and is refreshed in the
background; a slow or silent one never holds an answer up. These tests call the handler directly, with fake slow sources.
"""
import asyncio
import time

import pytest

pytestmark = pytest.mark.asyncio


def ids(resp):
    return [m["id"] for m in resp["data"]]


async def test_a_warm_call_does_not_wait_for_a_slow_source(proxy_module, monkeypatch):
    calls = []

    async def slow_ollama(*, force=False):
        calls.append(time.monotonic())
        await asyncio.sleep(0.4)
        return {"models": [("llama3", "Llama 3", "")], "state": "ok"}

    monkeypatch.setattr(proxy_module, "_ollama_status", slow_ollama)
    first = await proxy_module.list_models()
    assert "llama3" in ids(first), "the very first call waits for a source that has never answered"
    proxy_module._models_source("ollama")["at"] = 0.0                      # (the list has gone stale)
    t = time.perf_counter()
    second = await proxy_module.list_models()
    assert time.perf_counter() - t < 0.1, "a stale list is handed out at once; the refresh runs behind the answer"
    assert "llama3" in ids(second)
    await asyncio.sleep(0.6)
    assert len(calls) == 2, "the stale list was refreshed once in the background"


async def test_a_source_that_has_never_answered_is_left_out_not_waited_on_for_long(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "MODELS_FIRST_WAIT_S", 0.2)
    release = asyncio.Event()

    async def silent_lmstudio(*, force=False):
        await release.wait()
        return {"models": [("qwen3", "Qwen 3", "")], "state": "ok"}

    monkeypatch.setattr(proxy_module, "_lmstudio_status", silent_lmstudio)
    t = time.perf_counter()
    r = await proxy_module.list_models()
    assert time.perf_counter() - t < 1.0 and "qwen3" not in ids(r)
    release.set()
    await asyncio.sleep(0.2)
    assert "qwen3" in ids(await proxy_module.list_models()), "it shows up once it has answered"


async def test_many_callers_at_once_share_one_refresh(proxy_module, monkeypatch):
    n = []

    async def lm(*, force=False):
        n.append(1)
        await asyncio.sleep(0.2)
        return {"models": [("m1", "M1", "")], "state": "ok"}

    monkeypatch.setattr(proxy_module, "_lmstudio_status", lm)
    out = await asyncio.gather(*[proxy_module.list_models() for _ in range(10)])
    assert len(n) == 1 and all("m1" in ids(o) for o in out)


async def test_a_source_that_fails_keeps_its_last_list(proxy_module, monkeypatch):
    state = {"fail": False}

    async def ollama(*, force=False):
        if state["fail"]:
            raise RuntimeError("daemon went away")
        return {"models": [("gemma", "Gemma", "")], "state": "ok"}

    monkeypatch.setattr(proxy_module, "_ollama_status", ollama)
    assert "gemma" in ids(await proxy_module.list_models())
    state["fail"] = True
    proxy_module._models_source("ollama")["at"] = 0.0
    assert "gemma" in ids(await proxy_module.list_models())
    await asyncio.sleep(0.1)
    assert "gemma" in ids(await proxy_module.list_models()), "after the failed refresh it still shows the last list"


async def test_a_key_removed_takes_its_models_out_at_once(proxy_module, monkeypatch):
    async def gem(*, force=False):
        return {"models": [("gemini-x", "Gemini X", "")], "state": "ok"}

    monkeypatch.setattr(proxy_module, "_gemini_status", gem)
    monkeypatch.setattr(proxy_module, "gemini_api_key", "k")
    assert "gemini-x" in ids(await proxy_module.list_models())
    monkeypatch.setattr(proxy_module, "gemini_api_key", None)
    assert "gemini-x" not in ids(await proxy_module.list_models())
