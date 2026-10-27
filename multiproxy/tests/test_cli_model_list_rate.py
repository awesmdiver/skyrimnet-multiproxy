"""The Antigravity and Grok model lists start a program only when a person needs the answer (2026-10-25).

Watched live: a page poll re-ran `agy models` about every 65 s, and each run could make Antigravity start an updater that flashed a console window.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

AGY_OUT = b"Fetching available models...\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
GROK_OUT = b"Default model: grok-4.6\n  * grok-4.6 (default)\n  - grok-4.5\n"


@pytest.fixture
def counting(proxy_module, monkeypatch):
    calls = []

    def run(cmd, *a, **k):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=AGY_OUT if "agy" in str(cmd[0]) else GROK_OUT)
    monkeypatch.setattr(proxy_module.hidden_process, "run", run)
    monkeypatch.setattr(proxy_module, "_agy_path", lambda: "/fake/agy")
    monkeypatch.setattr(proxy_module, "_grok_path", lambda: "/fake/grok")
    monkeypatch.setattr(proxy_module, "_agy_models_cache", (0.0, None))
    monkeypatch.setattr(proxy_module, "_grok_models_cache", (0.0, None))
    return calls


def test_the_lists_are_kept_for_minutes_not_a_minute(proxy_module):
    assert proxy_module.AGY_MODELS_CACHE_TTL >= 600 and proxy_module.GROK_MODELS_CACHE_TTL >= 600
    assert proxy_module.AGY_MODELS_FAILED_TTL >= 600 and proxy_module.GROK_MODELS_FAILED_TTL >= 600


@pytest.mark.asyncio
async def test_a_page_polling_every_few_seconds_starts_each_program_once(proxy_module, counting):
    for _ in range(40):
        await proxy_module._agy_status()
        await proxy_module._grok_status()
    assert len([c for c in counting if "agy" in c[0]]) == 1
    assert len([c for c in counting if "grok" in c[0]]) == 1


@pytest.mark.asyncio
async def test_everyone_asking_at_once_shares_one_start(proxy_module, counting):
    await asyncio.gather(*[proxy_module._fetch_agy_models() for _ in range(10)])
    assert len(counting) == 1


@pytest.mark.asyncio
async def test_refresh_looks_at_once_every_time(proxy_module, counting):
    await proxy_module._agy_status(force=True)
    await proxy_module._agy_status(force=True)
    assert len(counting) == 2


@pytest.mark.asyncio
async def test_an_old_list_is_handed_out_at_once_and_one_background_look_follows(proxy_module, counting):
    old = time.time() - proxy_module.AGY_MODELS_CACHE_TTL - 5
    proxy_module._agy_models_cache = (old, [("old-model", "Old")])
    for _ in range(5):
        assert await proxy_module._fetch_agy_models() == [("old-model", "Old")]
    for _ in range(20):
        await asyncio.sleep(0.05)
        if proxy_module._agy_models_cache[1] != [("old-model", "Old")]:
            break
    assert len(counting) == 1
    assert proxy_module._agy_models_cache[1] == [("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)")]


@pytest.mark.asyncio
async def test_a_failed_look_is_not_retried_by_a_poll_within_ten_minutes(proxy_module, monkeypatch):
    calls = []
    monkeypatch.setattr(proxy_module.hidden_process, "run", lambda *a, **k: calls.append(1) or SimpleNamespace(returncode=1, stdout=b""))
    monkeypatch.setattr(proxy_module, "_agy_path", lambda: "/fake/agy")
    monkeypatch.setattr(proxy_module, "_agy_models_cache", (0.0, None))
    for _ in range(10):
        assert await proxy_module._fetch_agy_models() is None
        await asyncio.sleep(0)
    assert len(calls) == 1
