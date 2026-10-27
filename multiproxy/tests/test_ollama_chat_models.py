"""The Overview Test for Ollama (2026-10-05): it picks a model that can chat, not just the first one Ollama lists.

A stand-in Ollama (this device only, a spare port) lists an embedding model first. Checked: a model without the `completion` capability is marked and sorted after the chat
ones, the row stays Ready, the test model is a route-used one, else one that answered in the last day, else the first that can chat; an old Ollama that reports no
capabilities keeps every model but the page retries once with the next one when the test says "does not support chat"; the picker's default is a chat model.
"""
import asyncio
import sqlite3
import time

import pytest
from aiohttp import web

import overview
import speed_queries
from tests.conftest import REAL_FETCH_OLLAMA_TAGS, REAL_OLLAMA_KINDS
from tests.test_overview import base, draw, needs_node, rows

TAGS = {"models": [{"name": "clef-flash:latest", "digest": "a"}, {"name": "clef:latest", "digest": "b"}, {"name": "qwen3:30b", "digest": "c"}, {"name": "qwen3:8b", "digest": "d"}]}
CAPS = {"clef-flash:latest": ["decision"], "clef:latest": ["completion"], "qwen3:30b": ["completion", "tools"], "qwen3:8b": ["completion", "tools"]}


def stand_in(with_caps: bool):
    async def tags(_):
        return web.json_response(TAGS)

    async def show(request):
        body = await request.json()
        return web.json_response({"capabilities": CAPS[body["model"]]} if with_caps else {"details": {}})
    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/show", show)
    return app


async def status_from(proxy_module, monkeypatch, with_caps):
    runner = web.AppRunner(stand_in(with_caps))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setattr(proxy_module, "ollama_api_key", None)
    monkeypatch.setattr(proxy_module, "_ollama_kinds", REAL_OLLAMA_KINDS)
    monkeypatch.setattr(proxy_module, "_fetch_ollama_tags", REAL_FETCH_OLLAMA_TAGS)       # (the suite replaces it with "not reachable")
    proxy_module._ollama_caps_cache.clear()
    proxy_module._ollama_tags_cache.clear()
    try:
        return await proxy_module._ollama_status(force=True)
    finally:
        await runner.cleanup()


def test_a_model_that_cannot_chat_is_marked_and_listed_after_the_chat_ones(proxy_module, monkeypatch):
    st = asyncio.run(status_from(proxy_module, monkeypatch, True))
    assert [r[1] for r in st["models"]] == ["clef:latest", "qwen3:30b", "qwen3:8b", "clef-flash:latest"], "chat models first, each group still by name"
    assert st["models"][-1][2] == "Decisions" and "Decisions" not in st["models"][0][2] and st["models"][0][2] == "Chat"
    assert st["chat"] == ["ollama/clef:latest", "ollama/qwen3:30b", "ollama/qwen3:8b"]
    assert st["state"] == "ok" and st["testable"] is True


def test_an_old_ollama_that_reports_nothing_keeps_every_model_unmarked(proxy_module, monkeypatch):
    st = asyncio.run(status_from(proxy_module, monkeypatch, False))
    assert [r[1] for r in st["models"]] == ["clef-flash:latest", "clef:latest", "qwen3:30b", "qwen3:8b"]
    assert all(r[2] == "" for r in st["models"]) and len(st["chat"]) == 4 and st["kinds"] == {}, "no information: nothing new is shown"


def test_the_provider_card_default_model_is_a_chat_model(proxy_module, monkeypatch):
    st = asyncio.run(status_from(proxy_module, monkeypatch, True))
    assert st["models"][0][1] != "clef-flash:latest", "the picker's default is the first listed"


def test_the_capability_answer_is_kept_not_asked_every_time(proxy_module, monkeypatch):
    asked = []

    async def run():
        st = await status_from(proxy_module, monkeypatch, True)
        proxy_module._ollama_tags_cache.clear()
        return st
    asyncio.run(run())
    assert len(proxy_module._ollama_caps_cache) == 4 and all(v[1] is not None for v in proxy_module._ollama_caps_cache.values())
    assert asked == []


# ---- which model the Test uses ----
CHAT = ["ollama/clef:latest", "ollama/qwen3:30b", "ollama/qwen3:8b"]


def test_a_route_used_model_comes_first_then_a_recent_one_then_the_first_that_can_chat():
    assert overview.pick_test_models(CHAT, ["ollama/qwen3:8b"], ["ollama/qwen3:30b"]) == ["ollama/qwen3:8b", "ollama/qwen3:30b", "ollama/clef:latest"]
    assert overview.pick_test_models(CHAT, [], ["qwen3:30b"])[0] == "ollama/qwen3:30b", "a name without the prefix still matches"
    assert overview.pick_test_models(CHAT, [], []) == CHAT
    assert overview.pick_test_models(CHAT, ["ollama/clef-flash:latest"], []) == CHAT, "a route naming a non-chat model does not bring it in"


def test_recent_models_are_those_that_answered_in_the_last_day_newest_first(tmp_path):
    db = str(tmp_path / "h.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE speed_history (model TEXT, provider TEXT, ts REAL, success INTEGER)")
    now = time.time()
    conn.executemany("INSERT INTO speed_history VALUES (?,?,?,?)", [("ollama/a", "Ollama", now - 100, 1), ("ollama/b", "Ollama", now - 50, 1), ("ollama/a", "Ollama", now - 10, 1),
                                                                    ("ollama/bad", "Ollama", now - 5, 0), ("ollama/old", "Ollama", now - 200000, 1), ("x", "Claude", now - 5, 1)])
    conn.commit()
    conn.close()
    assert speed_queries.recent_models(db, "Ollama", now - 86400) == ["ollama/a", "ollama/b"]
    assert speed_queries.recent_models(str(tmp_path / "missing" / "no.db"), "Ollama", 0) == []


def test_the_row_carries_the_chosen_model_and_the_one_to_retry_with_and_is_ready():
    prov = [{"id": "ollama", "name": "Ollama", "short": "Ollama", "state": "ok", "status": "Local: 4 models installed", "cloud": False, "where": "This device",
             "testModel": "ollama/qwen3:8b", "testNext": ["ollama/qwen3:30b"]}]
    d = base(providers=prov, uses={"provider:Ollama": ["Memories"]}, health={"Ollama": {"level": "ok"}})
    r = rows(d)["Ollama"]
    assert r["test"] == {"model": "ollama/qwen3:8b", "next": ["ollama/qwen3:30b"]} and r["word"] in ("Ready", "In use") and r["level"] == "ok"


@needs_node
def test_with_no_capability_information_the_test_tries_the_next_model_once_only_for_does_not_support_chat():
    prov = [{"id": "ollama", "name": "Ollama", "short": "Ollama", "state": "ok", "status": "Local", "cloud": False, "where": "This device",
             "testModel": "ollama/clef-flash:latest", "testNext": ["ollama/clef:latest"]}]
    d = base(providers=prov, uses={"provider:Ollama": ["Memories"]})
    idx = "ovActs.findIndex(r => r.key === 'provider:Ollama')"
    from tests.test_overview import run_page
    fetch = ("global.fetch = async (url, o) => { const m = JSON.parse(o.body).model; calls.push(m); "
             "if (m === 'ollama/clef-flash:latest') return { ok: false, json: async () => ({ error: { message: 'Ollama: \"clef-flash:latest\" does not support chat' } }) }; "
             "return { ok: true, json: async () => ({ choices: [{}] }) }; };\n")
    out = run_page(d, fetch + "ovHtml(ovLast); await ovTest(" + idx + ");")
    assert out["calls"] == ["ollama/clef-flash:latest", "ollama/clef:latest"], out["calls"]
    assert "Answered in" in out["html"] and "No answer" not in out["html"]
    other = ("global.fetch = async (url, o) => { calls.push(JSON.parse(o.body).model); return { ok: false, json: async () => ({ error: { message: 'Bad key' } }) }; };\n")
    out = run_page(d, other + "ovHtml(ovLast); await ovTest(" + idx + ");")
    assert out["calls"] == ["ollama/clef-flash:latest"], "another failure is not retried"
    assert "No answer: Bad key" in out["html"]
