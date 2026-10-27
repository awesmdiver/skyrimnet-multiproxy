"""Tests for the xAI (API key) provider against a fake xAI endpoint on this device only (127.0.0.1): a real local web server that
answers like api.x.ai's OpenAI-style API (`/v1/models`, `/v1/chat/completions`), so the real request code runs.

No valid xAI key was available when this was built, so the shapes are xAI's published OpenAI-style ones (`data[].id` for the
list; `choices[0].message.content`, `usage`, and `data: {...}` / `data: [DONE]` lines for chat); the wrong-key answer is the
one api.x.ai really gave a wrong key on 2026-10-03: HTTP 400 `{"code":"invalid-argument","error":"Incorrect API key provided..."}`.
"""
import json

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException

GOOD_KEY = "xai-test-key-not-real"
MODELS = {"object": "list", "data": [
    {"id": "grok-4", "object": "model", "owned_by": "xai"},
    {"id": "grok-4-fast-reasoning", "object": "model", "owned_by": "xai"},
    {"id": "grok-imagine-image", "object": "model", "owned_by": "xai"},
    {"id": "grok-imagine-video", "object": "model", "owned_by": "xai"},
    {"id": "not-a-chat-thing", "object": "model", "owned_by": "xai"},
]}
WRONG_KEY = {"code": "invalid-argument", "error": "Incorrect API key provided. You can obtain an API key from https://console.x.ai."}


def _make_app(seen: dict):
    async def models(request):
        if request.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
            return web.json_response(WRONG_KEY, status=400)
        return web.json_response(MODELS)

    async def chat(request):
        if request.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
            return web.json_response(WRONG_KEY, status=400)
        body = await request.json()
        seen["body"] = body
        if not body.get("stream"):
            return web.json_response({"id": "x", "object": "chat.completion", "model": body["model"],
                                      "choices": [{"index": 0, "message": {"role": "assistant", "content": "Dragons are rude."}, "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for piece in ("Dragons ", "are ", "rude."):
            ev = {"id": "x", "object": "chat.completion.chunk", "model": body["model"], "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    return app


@pytest_asyncio.fixture
async def xai_server(proxy_module, monkeypatch):
    seen: dict = {}
    server = TestServer(_make_app(seen), host="127.0.0.1")
    await server.start_server()
    monkeypatch.setattr(proxy_module, "XAI_BASE_URL", f"http://127.0.0.1:{server.port}/v1")
    monkeypatch.setattr(proxy_module, "_xai_models_cache", {})
    try:
        yield seen
    finally:
        await server.close()


MSGS = [{"role": "user", "content": "What do you think of dragons?"}]


# ---- Calls ----

@pytest.mark.asyncio
async def test_direct_with_no_key_asks_for_one(proxy_module):
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_xai_direct(None, MSGS, "grok-4", 100)
    assert exc.value.status_code == 500
    assert "No xAI key" in exc.value.detail


@pytest.mark.asyncio
async def test_direct_returns_the_reply_and_usage_and_sends_only_settings_every_model_takes(proxy_module, xai_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "xai_api_key", GOOD_KEY)
    text, usage = await proxy_module.call_xai_direct("You are Lydia.", MSGS, "grok-4", 100, temperature=0.5, frequency_penalty=0.3, stop=["x"])
    assert text == "Dragons are rude."
    assert usage["prompt_tokens"] == 9 and usage["completion_tokens"] == 4
    body = xai_server["body"]
    assert body["model"] == "grok-4" and body["temperature"] == 0.5
    assert "frequency_penalty" not in body and "stop" not in body, "reasoning models refuse these"
    assert body["messages"][0] == {"role": "system", "content": "You are Lydia."}


@pytest.mark.asyncio
async def test_a_wrong_key_is_a_clear_error(proxy_module, xai_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "xai_api_key", "wrong")
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_xai_direct(None, MSGS, "grok-4", 100)
    assert exc.value.status_code in (400, 401, 502)
    assert "Incorrect API key" in exc.value.detail


@pytest.mark.asyncio
async def test_streaming_passes_each_piece_through_to_the_end(proxy_module, xai_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "xai_api_key", GOOD_KEY)
    chunks = [c async for c in proxy_module.call_xai_streaming(None, MSGS, "grok-4", 100)]
    joined = "".join(chunks)
    for piece in ("Dragons ", "are ", "rude."):
        assert piece in joined
    assert joined.rstrip().endswith("data: [DONE]")
    assert xai_server["body"]["stream"] is True


# ---- Models ----

@pytest.mark.asyncio
async def test_status_with_no_key(proxy_module):
    s = await proxy_module._xai_status()
    assert (s["state"], s["status"], s["testable"]) == ("off", "No key yet", False)
    assert s["any"] == "xai/MODEL"


@pytest.mark.asyncio
async def test_status_lists_only_chat_models_from_the_live_list(proxy_module, xai_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "xai_api_key", GOOD_KEY)
    s = await proxy_module._xai_status(force=True)
    assert s["state"] == "ok" and s["status"] == "Key saved: 2 models available" and s["any"] is None
    assert [r[0] for r in s["models"]] == ["xai/grok-4", "xai/grok-4-fast-reasoning"]
    assert [r[1] for r in s["models"]] == ["Grok 4", "Grok 4 Fast Reasoning"]


@pytest.mark.asyncio
async def test_status_with_a_key_the_service_refuses_says_so_not_couldnt_reach(proxy_module, xai_server, monkeypatch, caplog):
    """The real 2026-10-03 case: xAI answers 400 "Incorrect API key provided" to a key that isn't an xAI key. That is a refused key, not an unreachable service."""
    monkeypatch.setattr(proxy_module, "xai_api_key", "wrong")
    with caplog.at_level("WARNING"):
        s = await proxy_module._xai_status(force=True)
    assert s["state"] == "warn" and s["status"] == "xAI didn't accept this key. Make a new one at console.x.ai and paste it here."
    assert "couldn't reach" not in s["status"]
    assert s["models"] == proxy_module.XAI_STATIC_FALLBACK_MODELS and s["any"] == "xai/MODEL" and s["testable"] is True
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "xAI's model list: the key was refused (HTTP 400): Incorrect API key provided" in log and "wrong" not in log.replace("Incorrect", ""), "one line, the status and xAI's message, never the key"


@pytest.mark.asyncio
async def test_a_refused_key_stays_explained_when_the_answer_comes_from_the_cache(proxy_module, xai_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "xai_api_key", "wrong")
    await proxy_module._xai_status(force=True)
    s = await proxy_module._xai_status()           # cached: no new call, same explanation
    assert "didn't accept this key" in s["status"]


@pytest.mark.asyncio
async def test_a_service_that_cannot_be_reached_keeps_the_old_line(proxy_module, monkeypatch, caplog):
    monkeypatch.setattr(proxy_module, "xai_api_key", GOOD_KEY)
    monkeypatch.setattr(proxy_module, "XAI_BASE_URL", "http://127.0.0.1:9/v1")       # nothing listens there
    monkeypatch.setattr(proxy_module, "_xai_models_cache", {})
    with caplog.at_level("WARNING"):
        s = await proxy_module._xai_status(force=True)
    assert s["state"] == "warn" and s["status"] == "Key saved, but couldn't reach the live model list"
    assert "xAI's model list: couldn't reach it" in " ".join(r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expect", [
    (401, "", "refused"), (403, "{}", "refused"),
    (400, '{"error": "Incorrect API key provided."}', "refused"),
    (400, '{"error": "something else is wrong"}', "other"),
    (500, "", "other"), (429, "slow down", "other"),
])
async def test_the_failure_is_classified(proxy_module, status, body, expect):
    assert proxy_module._classify_model_list_failure(status, body) == expect


@pytest.mark.asyncio
async def test_any_other_error_keeps_the_old_line_and_is_logged(proxy_module, monkeypatch, caplog):
    async def models(request):
        return web.json_response({"error": "overloaded"}, status=503)
    app = web.Application()
    app.router.add_get("/v1/models", models)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        monkeypatch.setattr(proxy_module, "XAI_BASE_URL", f"http://127.0.0.1:{server.port}/v1")
        monkeypatch.setattr(proxy_module, "xai_api_key", GOOD_KEY)
        monkeypatch.setattr(proxy_module, "_xai_models_cache", {})
        with caplog.at_level("WARNING"):
            s = await proxy_module._xai_status(force=True)
    finally:
        await server.close()
    assert s["status"] == "Key saved, but couldn't reach the live model list"
    assert "(HTTP 503): overloaded" in " ".join(r.getMessage() for r in caplog.records)


def test_a_key_that_does_not_start_with_xai_gets_a_gentle_check_before_it_is_saved(test_client):
    html = test_client.get("/").text
    assert '"keyPrefix": "xai-"' in html
    assert "function keyLooksWrong(p, key)" in html and "if (!confirmed && keyLooksWrong(p, key)) { askSaveAnyway(id); return; }" in html
    assert "This doesn't look like ${p.keyKindName} (they start with ${p.keyPrefix}). Save anyway?" in html
    assert "Save anyway</button>" in html and "Cancel</button>" in html and '<div id="keyCheck"></div>' in html
    assert '"keyKindName": "an xAI key"' in html


# ---- It is a provider like the others ----

def test_the_xai_prefix_routes_and_reads_like_the_other_providers(proxy_module):
    assert proxy_module.is_xai_model("xai/grok-4") and proxy_module.is_xai_model("XAI/grok-4")
    assert proxy_module.strip_xai_prefix("xai/grok-4") == "grok-4"
    assert not proxy_module.is_xai_model("grok/grok-4.6") and not proxy_module.is_xai_model("grok-4")
    assert not proxy_module.is_unclaimed_slash_model("xai/grok-4")
    assert proxy_module._resolve_model_name("xai/grok-4") == "grok-4"
    assert proxy_module._provider_label_for("xai/grok-4") == "xAI"
    assert proxy_module._ai_class("xai/grok-4") == (proxy_module.routing.CLASS_PAID, "cloud")
    assert proxy_module._override_kind("xai/grok-4") == "xai"


@pytest.mark.asyncio
async def test_the_dashboard_lists_xai_with_a_key_box_and_refresh(proxy_module, xai_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "xai_api_key", GOOD_KEY)
    p = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "xai")
    assert p["name"] == "xAI (API key)" and p["hasKey"] is True
    assert p["keyEndpoint"] == "/config/xai-key" and p["refreshEndpoint"] == "/config/xai-models"
    assert len(p["models"]) == 2


def test_the_key_is_saved_and_cleared_like_the_other_keys(test_client, proxy_module):
    test_client.post("/config/xai-key", json={"key": "throwaway-value"})
    assert proxy_module.xai_api_key == "throwaway-value"
    test_client.post("/config/xai-key", json={"key": ""})
    assert proxy_module.xai_api_key is None
