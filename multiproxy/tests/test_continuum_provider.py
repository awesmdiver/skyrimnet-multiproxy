"""Tests for the Continuum (plan key) provider against a fake Continuum endpoint on this device only (127.0.0.1): a real local web
server that answers like api.continuumcode.ai's OpenAI-style API (`/v1/models`, `/v1/chat/completions`), so the real request code
runs. The shapes are the OpenAI ones Continuum's own Settings page describes; no real Continuum key was used here.
"""
import json

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException

GOOD_KEY = "cont_sk_test_key_not_real"
MODELS = {"object": "list", "data": [
    {"id": "claude-opus-5-5", "object": "model"},
    {"id": "gpt-5.5", "object": "model"},
    {"id": "text-embedding-3-small", "object": "model"},
]}
REFUSED = {"error": {"message": "Invalid API key", "type": "invalid_request_error"}}


def _make_app(seen: dict):
    async def models(request):
        if request.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
            return web.json_response(REFUSED, status=401)
        return web.json_response(MODELS)

    async def chat(request):
        if request.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
            return web.json_response(REFUSED, status=401)
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
async def continuum_server(proxy_module, monkeypatch):
    seen: dict = {}
    server = TestServer(_make_app(seen), host="127.0.0.1")
    await server.start_server()
    monkeypatch.setattr(proxy_module, "CONTINUUM_BASE_URL", f"http://127.0.0.1:{server.port}/v1")
    monkeypatch.setattr(proxy_module, "_continuum_models_cache", {})
    try:
        yield seen
    finally:
        await server.close()


MSGS = [{"role": "user", "content": "What do you think of dragons?"}]


# ---- Calls ----

@pytest.mark.asyncio
async def test_direct_with_no_key_asks_for_one(proxy_module):
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_continuum_direct(None, MSGS, "claude-opus-5-5", 100)
    assert exc.value.status_code == 500
    assert "No Continuum key" in exc.value.detail


@pytest.mark.asyncio
async def test_direct_returns_the_reply_and_usage_and_sends_only_settings_every_model_takes(proxy_module, continuum_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)
    text, usage = await proxy_module.call_continuum_direct("You are Lydia.", MSGS, "claude-opus-5-5", 100, temperature=0.5, frequency_penalty=0.3, stop=["x"])
    assert text == "Dragons are rude."
    assert usage["prompt_tokens"] == 9 and usage["completion_tokens"] == 4
    body = continuum_server["body"]
    assert body["model"] == "claude-opus-5-5" and body["temperature"] == 0.5
    assert "frequency_penalty" not in body and "stop" not in body
    assert body["messages"][0] == {"role": "system", "content": "You are Lydia."}


@pytest.mark.asyncio
async def test_a_refused_key_is_a_clear_error(proxy_module, continuum_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", "wrong")
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_continuum_direct(None, MSGS, "claude-opus-5-5", 100)
    assert exc.value.status_code in (400, 401, 502)
    assert "Invalid API key" in exc.value.detail


@pytest.mark.asyncio
async def test_streaming_passes_each_piece_through_to_the_end(proxy_module, continuum_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)
    chunks = [c async for c in proxy_module.call_continuum_streaming(None, MSGS, "claude-opus-5-5", 100)]
    joined = "".join(chunks)
    for piece in ("Dragons ", "are ", "rude."):
        assert piece in joined
    assert joined.rstrip().endswith("data: [DONE]")
    assert continuum_server["body"]["stream"] is True


# ---- Models ----

@pytest.mark.asyncio
async def test_status_with_no_key(proxy_module):
    s = await proxy_module._continuum_status()
    assert (s["state"], s["status"], s["testable"]) == ("off", "No key yet", False)
    assert s["any"] == "continuum/MODEL"


@pytest.mark.asyncio
async def test_status_lists_only_chat_models_from_the_live_list(proxy_module, continuum_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)
    s = await proxy_module._continuum_status(force=True)
    assert s["state"] == "ok" and s["status"] == "Key saved: 2 models available" and s["any"] is None
    assert [r[0] for r in s["models"]] == ["continuum/claude-opus-5-5", "continuum/gpt-5.5"]


@pytest.mark.asyncio
async def test_status_says_when_continuum_does_not_accept_the_key(proxy_module, continuum_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", "wrong")
    s = await proxy_module._continuum_status(force=True)
    assert s["state"] == "warn" and s["status"] == "Continuum didn't accept this key"
    assert s["models"] == proxy_module.CONTINUUM_STATIC_FALLBACK_MODELS and s["any"] == "continuum/MODEL"


@pytest.mark.asyncio
async def test_status_says_when_continuum_cannot_be_reached(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)
    monkeypatch.setattr(proxy_module, "CONTINUUM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(proxy_module, "_continuum_models_cache", {})
    s = await proxy_module._continuum_status(force=True)
    assert s["state"] == "warn" and s["status"] == "Key saved, but couldn't reach Continuum"


# ---- It is a provider like the others ----

def test_the_continuum_prefix_routes_and_reads_like_the_other_providers(proxy_module):
    assert proxy_module.is_continuum_model("continuum/claude-opus-5-5") and proxy_module.is_continuum_model("Continuum/x")
    assert proxy_module.strip_continuum_prefix("continuum/claude-opus-5-5") == "claude-opus-5-5"
    assert not proxy_module.is_continuum_model("claude-opus-5-5")
    assert not proxy_module.is_unclaimed_slash_model("continuum/claude-opus-5-5")
    assert proxy_module._resolve_model_name("continuum/claude-opus-5-5") == "claude-opus-5-5"
    assert proxy_module._provider_label_for("continuum/claude-opus-5-5") == "Continuum"
    assert proxy_module._ai_class("continuum/claude-opus-5-5") == (proxy_module.routing.CLASS_PAID, "cloud")
    assert proxy_module._override_kind("continuum/claude-opus-5-5") == "continuum"


@pytest.mark.asyncio
async def test_the_dashboard_lists_continuum_with_a_key_box_and_refresh(proxy_module, continuum_server, monkeypatch):
    monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)
    p = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "continuum")
    assert p["name"] == "Continuum (plan key)" and p["hasKey"] is True
    assert p["keyEndpoint"] == "/config/continuum-key" and p["refreshEndpoint"] == "/config/continuum-models"
    assert p["keyPrefix"] == "cont_sk_" and p["keyKindName"] == "a Continuum key" and len(p["models"]) == 2


def test_the_key_is_saved_and_cleared_like_the_other_keys(test_client, proxy_module):
    test_client.post("/config/continuum-key", json={"key": "throwaway-value"})
    assert proxy_module.continuum_api_key == "throwaway-value"
    test_client.post("/config/continuum-key", json={"key": ""})
    assert proxy_module.continuum_api_key is None


def test_health_and_models_list_show_continuum(test_client, proxy_module, monkeypatch):
    assert test_client.get("/health").json()["continuum_configured"] is False
    monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)

    async def fake_status(*, force=False):
        return {"models": [["continuum/claude-opus-5-5", "claude-opus-5-5", ""]]}
    monkeypatch.setattr(proxy_module, "_continuum_status", fake_status)
    proxy_module._models_forget()
    assert test_client.get("/health").json()["continuum_configured"] is True
    ids = {m["id"]: m["owned_by"] for m in test_client.get("/v1/models").json()["data"]}
    assert ids["continuum/claude-opus-5-5"] == "continuum"


def test_the_dashboard_asks_before_saving_a_key_that_does_not_start_right(test_client):
    html = test_client.get("/").text
    assert "This doesn't look like ${{p.keyKindName}} (they start with ${{p.keyPrefix}}). Save anyway?".replace("{{", "{").replace("}}", "}") in html and "Save anyway" in html
    # The provider list is grouped by how you pay now ("Choose a provider"); Continuum is a plan key and says so.
    assert "'continuum'" in html and "Continuum (plan key)" in html and "a Continuum key" in html


# ---- A reply that streams although nobody asked it to ----

def _odd_app(kind: str):
    async def chat(request):
        if kind == "sse":
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for piece in ("Dragons ", "are ", "rude."):
                ev = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                await resp.write(f"data: {json.dumps(ev)}\n\n".encode())
            last = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}
            await resp.write(f"data: {json.dumps(last)}\n\ndata: [DONE]\n\n".encode())
            return resp
        if kind == "html":
            return web.Response(text="<html>hi</html>", content_type="text/html")
        return web.json_response({"choices": [{"message": {"content": "Plain JSON."}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}})
    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    return app


async def _ask(proxy_module, monkeypatch, kind):
    server = TestServer(_odd_app(kind), host="127.0.0.1")
    await server.start_server()
    try:
        monkeypatch.setattr(proxy_module, "CONTINUUM_BASE_URL", f"http://127.0.0.1:{server.port}/v1")
        monkeypatch.setattr(proxy_module, "continuum_api_key", GOOD_KEY)
        return await proxy_module.call_continuum_direct(None, MSGS, "claude-opus-5-5", 100)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_stream_sent_to_a_not_streamed_request_is_put_together(proxy_module, monkeypatch):
    text, usage = await _ask(proxy_module, monkeypatch, "sse")
    assert text == "Dragons are rude."
    assert usage["prompt_tokens"] == 9 and usage["completion_tokens"] == 4 and usage["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_a_json_reply_still_works(proxy_module, monkeypatch):
    text, usage = await _ask(proxy_module, monkeypatch, "json")
    assert text == "Plain JSON." and usage["completion_tokens"] == 2


@pytest.mark.asyncio
async def test_an_odd_reply_type_is_a_named_error_not_could_not_reach(proxy_module, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await _ask(proxy_module, monkeypatch, "html")
    assert "text/html" in exc.value.detail and "Could not reach" not in exc.value.detail
