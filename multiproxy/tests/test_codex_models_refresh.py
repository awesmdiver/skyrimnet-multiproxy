"""Check for models asks Codex itself to refresh its model list when it is old (4.2.1). MultiProxy only starts the exchange and re-reads the file: models_cache.json is Codex's own.

Stand-ins only: a fake `codex app-server` that rewrites a scratch cache the way the real one does. The real Codex is never started (conftest turns the refresh off; these tests turn it on).
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import codex_models_refresh as cmr

FAKE = str(Path(__file__).resolve().parent / "fixtures" / "fake_codex_app_server.py")


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def write_cache(path, days_ago, version="0.167.2", slugs=("gpt-6-luna", "gpt-5.6-luna")):
    models = [{"slug": s, "display_name": s, "visibility": "list", "supported_in_api": True, "priority": i} for i, s in enumerate(slugs)]
    Path(path).write_text(json.dumps({"fetched_at": iso(days_ago), "client_version": version, "models": models}), encoding="utf-8")


# ---- the rules ------------------------------------------------------------------------------------------------------------------
def test_when_a_refresh_is_needed(tmp_path):
    f = tmp_path / "models_cache.json"
    assert cmr.needs_refresh(cmr.read_meta(str(f)), "0.167.2")                      # missing
    write_cache(f, 2)
    assert cmr.needs_refresh(cmr.read_meta(str(f)), "0.167.2")                      # older than 24 hours
    write_cache(f, 0.1, version="0.155.1")
    assert cmr.needs_refresh(cmr.read_meta(str(f)), "0.167.2")                      # another Codex version wrote it
    write_cache(f, 0.1)
    assert not cmr.needs_refresh(cmr.read_meta(str(f)), "0.167.2")                  # fresh and the same version
    assert not cmr.needs_refresh(cmr.read_meta(str(f)), None)                       # version unknown: age alone decides


def test_the_old_list_line_only_after_a_week_with_the_date(tmp_path):
    f = tmp_path / "models_cache.json"
    write_cache(f, 3)
    assert cmr.old_note(cmr.read_meta(str(f))) == ""
    write_cache(f, 10)
    note = cmr.old_note(cmr.read_meta(str(f)))
    d = datetime.now() - timedelta(days=10)
    assert note == f"This list is from {d.strftime('%b')} {d.day}, {d.year}. Sign in to Codex to update it."
    assert cmr.old_note(None) == ""


def test_the_time_in_the_file_is_read_in_every_shape():
    for s in ("2026-11-13T02:14:39.675596900Z", "2026-11-13T02:14:39Z", "2026-11-13T02:14:39+00:00", "2026-11-13T02:14:39.5"):
        assert cmr.parse_time(s) is not None, s
    assert cmr.parse_time("") is None and cmr.parse_time(None) is None and cmr.parse_time("soon") is None


# ---- the exchange with a stand-in Codex -----------------------------------------------------------------------------------------------
def spawner(cache, mode="ok"):
    def spawn(exe, arg, **kw):
        assert arg == "app-server"
        return asyncio.create_subprocess_exec(sys.executable, FAKE, str(cache), mode, **kw)
    return spawn


def test_the_stand_in_codex_rewrites_its_file_when_asked_and_the_file_is_read_again(tmp_path):
    f = tmp_path / "models_cache.json"
    write_cache(f, 30, version="0.155.1")
    assert asyncio.run(cmr.ask(spawner(f), "codex", timeout=15)) is True
    meta = cmr.read_meta(str(f))
    assert meta["version"] == "9.9.9" and time.time() - meta["fetched"] < 60


@pytest.mark.parametrize("mode", ["die", "error"])
def test_a_codex_that_fails_gives_false_and_leaves_the_file_alone(tmp_path, mode):
    f = tmp_path / "models_cache.json"
    write_cache(f, 30)
    before = f.read_text(encoding="utf-8")
    assert asyncio.run(cmr.ask(spawner(f, mode), "codex", timeout=5)) is False
    assert f.read_text(encoding="utf-8") == before


def test_a_codex_that_never_answers_is_given_up_on_at_the_time_limit_and_ended(tmp_path):
    f = tmp_path / "models_cache.json"
    write_cache(f, 30)
    t = time.monotonic()
    assert asyncio.run(cmr.ask(spawner(f, "hang"), "codex", timeout=1.0)) is False
    assert time.monotonic() - t < 10


def test_a_codex_that_cannot_be_started_is_quiet():
    async def spawn(exe, arg, **kw):
        raise FileNotFoundError(exe)
    assert asyncio.run(cmr.ask(spawn, "nope", timeout=2)) is False


# ---- in the program ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def card(proxy_module, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(proxy_module, "CODEX_MODEL_REFRESH", True)
    monkeypatch.setattr(proxy_module, "_codex_path", lambda: "codex")
    monkeypatch.setattr(proxy_module, "_codex_installed_version", AsyncMock(return_value="0.167.2"))
    monkeypatch.setattr(proxy_module, "_codex_logged_in", AsyncMock(return_value=True))
    monkeypatch.setattr(proxy_module, "_codex_refresh_last", 0.0)
    monkeypatch.setattr(proxy_module, "_codex_refresh_task", None)
    proxy_module._codex_models_cache = (0.0, None)
    return tmp_path / "models_cache.json"


def fake_ask(cache, calls, ok=True, delay=0.0):
    async def ask(spawn, exe, **kw):
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        if ok:
            write_cache(cache, 0, version="0.167.2", slugs=("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"))
        return ok
    return ask


@pytest.mark.asyncio
async def test_check_for_models_with_an_old_file_refreshes_once_and_reads_the_new_list(proxy_module, card, monkeypatch):
    write_cache(card, 30)
    calls = []
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", fake_ask(card, calls))
    st = await proxy_module._codex_status(force=True)
    assert len(calls) == 1
    assert [m[0] for m in st["models"]] == ["codex/gpt-6-astra", "codex/gpt-6.1-sol", "codex/gpt-6.1-terra", "codex/gpt-6-luna"]
    assert st["staleNote"] == ""


@pytest.mark.asyncio
async def test_a_different_codex_version_refreshes_a_fresh_file_but_a_fresh_one_does_not(proxy_module, card, monkeypatch):
    calls = []
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", fake_ask(card, calls))
    write_cache(card, 0.1, version="0.155.1")
    await proxy_module._codex_status(force=True)
    assert len(calls) == 1
    write_cache(card, 0.1)
    await proxy_module._codex_status(force=True)
    assert len(calls) == 1, "fresh and the same version: Codex is not asked"


@pytest.mark.asyncio
async def test_a_failed_refresh_keeps_the_old_list_and_says_so_only_after_a_week(proxy_module, card, monkeypatch):
    calls = []
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", fake_ask(card, calls, ok=False))
    write_cache(card, 10)
    st = await proxy_module._codex_status(force=True)
    assert [m[0] for m in st["models"]] == ["codex/gpt-6-luna", "codex/gpt-5.6-luna"]
    assert st["staleNote"].startswith("This list is from ") and st["staleNote"].endswith(". Sign in to Codex to update it.")
    write_cache(card, 3)
    st = await proxy_module._codex_status(force=True)
    assert st["staleNote"] == "" and len(calls) == 2


@pytest.mark.asyncio
async def test_two_checks_at_once_start_one_exchange(proxy_module, card, monkeypatch):
    write_cache(card, 30)
    calls = []
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", fake_ask(card, calls, delay=0.4))
    await asyncio.gather(proxy_module._fetch_codex_models(force=True), proxy_module._fetch_codex_models(force=True))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_opening_the_card_asks_in_the_background_at_most_once_a_day(proxy_module, card, monkeypatch):
    write_cache(card, 30)
    calls = []
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", fake_ask(card, calls))
    rows = await proxy_module._fetch_codex_models()              # the page is not held up: it gets the old list at once
    assert [r[0] for r in rows] == ["gpt-6-luna", "gpt-5.6-luna"]
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    write_cache(card, 30)
    proxy_module._codex_models_cache = (0.0, None)
    await proxy_module._fetch_codex_models()
    await asyncio.sleep(0.2)
    assert len(calls) == 1, "tried within the last day: not again"


@pytest.mark.asyncio
async def test_a_slow_codex_does_not_hold_the_button_beyond_its_limit(proxy_module, card, monkeypatch):
    write_cache(card, 30)
    monkeypatch.setattr(proxy_module, "CODEX_REFRESH_WAIT", 0.5)
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", fake_ask(card, [], delay=5))
    t = time.monotonic()
    rows = await proxy_module._fetch_codex_models(force=True)
    assert time.monotonic() - t < 3 and [r[0] for r in rows] == ["gpt-6-luna", "gpt-5.6-luna"]
    proxy_module._codex_refresh_task.cancel()


@pytest.mark.asyncio
async def test_multiproxy_never_opens_models_cache_json_for_writing(proxy_module, card, monkeypatch):
    import builtins
    modes = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        if str(file).endswith("models_cache.json"):
            modes.append(mode)
        return real_open(file, mode, *a, **k)
    write_cache(card, 30)
    monkeypatch.setattr(proxy_module.codex_models_refresh, "ask", AsyncMock(return_value=False))
    monkeypatch.setattr(builtins, "open", spy)
    await proxy_module._codex_status(force=True)
    assert modes and all(set(m) <= set("rt") for m in modes), modes
    ours = Path(cmr.__file__).read_text(encoding="utf-8")
    assert "'w'" not in ours and '"w"' not in ours
    assert 'open(_codex_models_cache_path(), "w' not in Path(proxy_module.__file__).read_text(encoding="utf-8")


def test_the_card_shows_the_old_list_line_under_the_heading(proxy_module, test_client):
    html = test_client.get("/").text
    assert "codexSelectedId === 'default' && active.staleNote" in html
