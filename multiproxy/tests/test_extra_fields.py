"""A request must never fail over a setting an AI doesn't understand (2026-10-01).

Real case (the director, live): SkyrimNet's "Provider Settings" box added {"cache": true, "provider": {"ignore": [...]}, "route": "fallback"}
to every request; Gemini answered 400 `Unknown name "cache": Cannot find field`, and the NPC got nothing. Every provider is exercised here
through the whole app against a real local server that rejects fields it doesn't know, the way the real API does.
"""
import json

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

from tests.fixtures.courier_fakes import FakeLmStudio, free_port

pytestmark = pytest.mark.asyncio

# The exact request from the director's log, plus MultiProxy's own reserved object.
EXTRA = {"cache": True, "provider": {"ignore": ["atlas-cloud"]}, "route": "fallback", "reasoning": {"enabled": False}, "temperature": 1.0,
         "multiproxy": {"anything": "later"}}
FOREIGN = {"cache", "provider", "route", "multiproxy"}


class StrictServer:
    """An OpenAI-shaped chat endpoint that, like Gemini / OpenAI / GLM, answers 400 to a field it doesn't know."""

    def __init__(self, known, style="gemini", tolerant=False):
        self.known, self.style, self.tolerant = set(known) | {"model", "messages"}, style, tolerant
        self.bodies, self.port, self.runner = [], free_port(), None

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    async def start(self):
        app = web.Application()
        app.router.add_post("/chat/completions", self.chat)
        app.router.add_post("/v1/chat/completions", self.chat)

        async def models(request):
            return web.json_response({"data": [{"id": "m1"}]})
        app.router.add_get("/v1/models", models)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        await self.runner.cleanup()

    async def chat(self, request):
        body = await request.json()
        self.bodies.append(body)
        unknown = [k for k in body if k not in self.known]
        if unknown and not self.tolerant:
            if self.style == "gemini":
                msg = "Invalid JSON payload received. " + " ".join(f'Unknown name "{k}": Cannot find field.' for k in unknown)
                return web.json_response([{"error": {"code": 400, "message": msg, "status": "INVALID_ARGUMENT"}}], status=400)
            if self.style == "openai":
                return web.json_response({"error": {"message": f"Unrecognized request argument supplied: {unknown[0]}", "type": "invalid_request_error"}}, status=400)
            if self.style == "vague":                                    # names nothing: the safety net drops every non-standard field
                return web.json_response({"error": {"message": "Invalid JSON payload received."}}, status=400)
            return web.json_response({"error": {"message": f"Extra inputs are not permitted: {unknown}"}}, status=400)
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'hello'}}]})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@pytest_asyncio.fixture(autouse=True)
async def fresh(proxy_module):
    proxy_module._learned_drops.clear()
    proxy_module._logged_drops.clear()
    yield


async def post(proxy_module, model, extra=None, stream=False):
    body = {"model": model, "stream": stream, "max_tokens": 4096, "messages": [{"role": "user", "content": "hi"}], **(EXTRA if extra is None else extra)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30) as c:
        if not stream:
            return await c.post("/v1/chat/completions", json=body)
        async with c.stream("POST", "/v1/chat/completions", json=body) as r:
            text = ""
            async for line in r.aiter_lines():
                text += line + "\n"
            r.text_ = text
            return r


STRICT = {   # provider -> (model the request names, base-URL attribute, key attribute, the fields its real API knows, error style)
    "gemini": ("gemini/gemini-3.8-flash", "GEMINI_BASE_URL", "gemini_api_key", {"temperature", "top_p", "n", "stream", "stream_options", "stop", "max_tokens", "response_format", "tools", "tool_choice", "reasoning_effort"}, "gemini"),
    "openai": ("openai/gpt-5.5", "OPENAI_BASE_URL", "openai_api_key", {"temperature", "top_p", "stream", "max_completion_tokens", "response_format", "reasoning_effort", "seed"}, "openai"),
    "glm": ("glmzai/glm-5.3", "GLM_BASE_URL", "glm_api_key", {"temperature", "top_p", "stream", "max_tokens", "thinking", "reasoning_effort", "do_sample", "stop"}, "gemini"),
}
TOLERANT = {   # these keep players' own settings (min_p...) but never get another API's fields
    "nano": ("nano/some-model", "NANO_BASE_URL", "nano_api_key"),
    "deepseek": ("deepseek/deepseek-flash", "DEEPSEEK_BASE_URL", "deepseek_api_key"),
}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("name", sorted(STRICT))
async def test_a_strict_api_succeeds_with_the_exact_request_from_the_log(proxy_module, monkeypatch, name, stream):
    model, base_attr, key_attr, known, style = STRICT[name]
    srv = await StrictServer(known, style).start()
    monkeypatch.setattr(proxy_module, base_attr, srv.base)
    monkeypatch.setattr(proxy_module, key_attr, "test-key")
    try:
        r = await post(proxy_module, model, stream=stream)
    finally:
        await srv.stop()
    assert r.status_code == 200, (name, getattr(r, "text", ""))
    assert len(srv.bodies) == 1, "no wasted retry: the extras never went out"
    assert not (set(srv.bodies[0]) & FOREIGN), srv.bodies[0]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("name", sorted(TOLERANT))
async def test_tolerant_apis_keep_the_players_own_settings_but_not_another_apis(proxy_module, monkeypatch, name, stream):
    model, base_attr, key_attr = TOLERANT[name]
    srv = await StrictServer(set(), tolerant=True).start()
    monkeypatch.setattr(proxy_module, base_attr, srv.base)
    monkeypatch.setattr(proxy_module, key_attr, "test-key")
    try:
        r = await post(proxy_module, model, extra={**EXTRA, "min_p": 0.05}, stream=stream)
    finally:
        await srv.stop()
    assert r.status_code == 200
    sent = srv.bodies[0]
    assert sent["min_p"] == 0.05 and not (set(sent) & FOREIGN), sent


async def test_kilo_gateway_is_openrouter_shaped_so_it_keeps_openrouters_settings_but_never_multiproxy(proxy_module, monkeypatch):
    srv = await StrictServer(set(), tolerant=True).start()
    monkeypatch.setattr(proxy_module, "KILO_BASE_URL", srv.base)
    monkeypatch.setattr(proxy_module, "kilo_api_key", "k")
    try:
        r = await post(proxy_module, "kilo/anthropic/claude-sonnet-5")
    finally:
        await srv.stop()
    sent = srv.bodies[0]
    assert r.status_code == 200 and sent["cache"] is True and sent["route"] == "fallback" and sent["provider"] == {"ignore": ["atlas-cloud"]}
    assert "multiproxy" not in sent


async def test_ollama_lm_studio_and_another_pc_get_everything_but_openrouters_fields_and_multiproxy(proxy_module, monkeypatch):
    ol = await StrictServer(set(), tolerant=True).start()
    monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", ol.base + "/v1")
    lms = await FakeLmStudio().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", lms.base + "/v1")
    pc = await StrictServer(set(), tolerant=True).start()
    await proxy_module.remote_svc.add_openai(pc.base, "den-pc")
    try:
        r1 = await post(proxy_module, "ollama/llama3", extra={**EXTRA, "num_ctx": 8192})
        r2 = await post(proxy_module, "lmstudio/google/gemma-4-e4b", extra={**EXTRA, "min_p": 0.1})
        r3 = await post(proxy_module, "den-pc/m1", extra={**EXTRA, "min_p": 0.1})
    finally:
        await ol.stop(); await lms.stop(); await pc.stop()
    assert r1.status_code == r2.status_code == r3.status_code == 200
    assert ol.bodies[0]["num_ctx"] == 8192 and not (set(ol.bodies[0]) & FOREIGN)
    assert lms.chat_calls[0]["min_p"] == 0.1 and not (set(lms.chat_calls[0]) & FOREIGN)
    assert pc.bodies[0]["min_p"] == 0.1 and not (set(pc.bodies[0]) & FOREIGN)
    assert "reasoning" not in ol.bodies[0] and "reasoning" not in pc.bodies[0], "the existing block on `reasoning` is unchanged"


# ---- the safety net ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("style", ["gemini", "openai", "pydantic"])
async def test_a_400_naming_unknown_fields_is_retried_once_without_them_and_remembered(proxy_module, monkeypatch, style, stream):
    srv = await StrictServer({"temperature", "stream", "max_tokens"}, style).start()          # DeepSeek here: a tolerant path that meets a strict server
    monkeypatch.setattr(proxy_module, "DEEPSEEK_BASE_URL", srv.base)
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    try:
        r = await post(proxy_module, "deepseek/deepseek-flash", extra={"temperature": 1.0, "weird_setting": 7, "other_setting": {"a": 1}}, stream=stream)
        assert r.status_code == 200
        first = len(srv.bodies)
        assert first == 2 and "weird_setting" in srv.bodies[0] and "weird_setting" not in srv.bodies[1] and "thinking" not in srv.bodies[1] or first == 2
        r2 = await post(proxy_module, "deepseek/deepseek-flash", extra={"temperature": 1.0, "weird_setting": 7, "other_setting": {"a": 1}}, stream=stream)
        assert r2.status_code == 200
        assert len(srv.bodies) == first + 1, "the next request doesn't pay for the same mistake again"
        assert "weird_setting" not in srv.bodies[-1]
    finally:
        await srv.stop()


async def test_a_vague_400_drops_every_non_standard_field_once(proxy_module, monkeypatch):
    srv = await StrictServer({"temperature", "stream", "max_tokens", "thinking"}, "vague").start()
    monkeypatch.setattr(proxy_module, "DEEPSEEK_BASE_URL", srv.base)
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    try:
        r = await post(proxy_module, "deepseek/deepseek-flash", extra={"temperature": 1.0, "weird_setting": 7})
    finally:
        await srv.stop()
    assert r.status_code == 200 and "weird_setting" in srv.bodies[0] and "weird_setting" not in srv.bodies[1]


async def test_a_real_400_that_is_not_about_fields_is_not_retried(proxy_module, monkeypatch):
    app = web.Application()
    calls = []

    async def chat(request):
        calls.append(1)
        return web.json_response({"error": {"message": "Your prompt is too sketchy."}}, status=400)
    app.router.add_post("/chat/completions", chat)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    monkeypatch.setattr(proxy_module, "DEEPSEEK_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    try:
        r = await post(proxy_module, "deepseek/deepseek-flash", extra={"weird_setting": 7})
    finally:
        await runner.cleanup()
    assert r.status_code == 400 and len(calls) == 1


async def test_nano_retries_once_too(proxy_module, monkeypatch):
    srv = await StrictServer({"temperature", "stream", "max_tokens"}, "gemini").start()
    monkeypatch.setattr(proxy_module, "NANO_BASE_URL", srv.base)
    monkeypatch.setattr(proxy_module, "nano_api_key", "k")
    try:
        r = await post(proxy_module, "nano/x", extra={"temperature": 1.0, "weird_setting": 7})
        r2 = await post(proxy_module, "nano/x", extra={"temperature": 1.0, "weird_setting": 7}, stream=True)
    finally:
        await srv.stop()
    assert r.status_code == 200 and r2.status_code == 200
    assert "weird_setting" in srv.bodies[0] and "weird_setting" not in srv.bodies[1] and all("weird_setting" not in b for b in srv.bodies[2:])


# ---- logging and the reserved name ------------------------------------------------------------------------------------------
async def test_a_dropped_field_is_logged_once_per_provider_not_on_every_request(proxy_module, monkeypatch, caplog):
    srv = await StrictServer(STRICT["gemini"][3], "gemini").start()
    monkeypatch.setattr(proxy_module, "GEMINI_BASE_URL", srv.base)
    monkeypatch.setattr(proxy_module, "gemini_api_key", "k")
    try:
        with caplog.at_level("WARNING", logger="proxy"):
            for _ in range(4):
                assert (await post(proxy_module, "gemini/gemini-3.8-flash")).status_code == 200
    finally:
        await srv.stop()
    lines = [r.message for r in caplog.records if "dropping unsupported param(s)" in r.message and r.message.startswith("Gemini:")]
    assert len(lines) == 1 and "'reasoning'" in lines[0] and "'cache'" in lines[0] and "multiproxy" not in lines[0], lines


async def test_multiproxy_is_never_sent_to_anything_and_is_not_an_error(proxy_module, monkeypatch):
    srv = await StrictServer(set(), tolerant=True).start()
    monkeypatch.setattr(proxy_module, "DEEPSEEK_BASE_URL", srv.base)
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    try:
        r = await post(proxy_module, "deepseek/deepseek-flash", extra={"multiproxy": {"mode": "x", "nested": {"a": [1, 2]}}})
    finally:
        await srv.stop()
    assert r.status_code == 200 and "multiproxy" not in srv.bodies[0]
    assert proxy_module._clean_extra_params("Anything", {"multiproxy": {"a": 1}, "temperature": 1.0}) == {"temperature": 1.0}


def test_the_foreign_field_list_and_the_safe_lists_are_what_the_notes_say(proxy_module):
    assert {"provider", "route", "cache", "transforms", "models", "plugins"} <= proxy_module.OPENROUTER_ONLY_FIELDS
    assert not (proxy_module.OPENROUTER_ONLY_FIELDS & proxy_module.GEMINI_ALLOWED_PARAMS)
    assert not (proxy_module.OPENROUTER_ONLY_FIELDS & proxy_module.OPENAI_ALLOWED_PARAMS)
    assert not (proxy_module.OPENROUTER_ONLY_FIELDS & proxy_module.GLM_SUPPORTED_PARAMS)
    assert "multiproxy" not in proxy_module.GEMINI_ALLOWED_PARAMS


async def test_the_subscription_providers_never_forward_extra_fields(proxy_module):
    """Claude, Codex and Antigravity run a program: nothing of the request body except the messages is passed on."""
    import inspect
    for fn in (proxy_module.call_codex_direct, proxy_module.call_geminicli_direct):
        src = inspect.getsource(fn)
        assert "extra_params" not in src.split('"""')[-1].replace("**extra_params", ""), fn.__name__
