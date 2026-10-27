"""Errors pass on what the provider actually said (2026-10-03).

Continuum answered `503 {"error":"upstream_error","status":426,"detail":"Continuum inference for grok-4.7 is temporarily unavailable. Please try again."}`
and the player only saw "upstream_error": the error code was kept and the detail (the part a person can act on) was dropped. The shared path now takes the
most human field in this order: error.message, detail, message, error (when it is a string), else the text itself; and the provider's name goes in front.
"""
import json

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException

CONTINUUM_REPLY = {"error": "upstream_error", "status": 426,
                   "detail": "Continuum inference for grok-4.7 is temporarily unavailable. Please try again."}
MSGS = [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize("body,expect", [
    (json.dumps(CONTINUUM_REPLY), "Continuum inference for grok-4.7 is temporarily unavailable. Please try again."),
    (json.dumps({"error": {"message": "Invalid API key", "type": "x"}}), "Invalid API key"),                         # OpenAI style
    (json.dumps({"error": {"message": "from error.message"}, "detail": "from detail", "message": "from message"}), "from error.message"),
    (json.dumps({"error": "a code", "detail": "from detail", "message": "from message"}), "from detail"),
    (json.dumps({"error": "a code", "message": "from message"}), "from message"),
    (json.dumps({"error": "only a code"}), "only a code"),
    (json.dumps([{"error": {"message": "Gemini's array shape"}}]), "Gemini's array shape"),
    ("The gateway is having a bad day", "The gateway is having a bad day"),                                         # plain text
    ("", "Unknown error"),
])
def test_the_most_human_field_wins(proxy_module, body, expect):
    assert proxy_module._extract_upstream_error_message(body) == expect


def test_the_provider_name_goes_in_front(proxy_module):
    assert proxy_module._provider_error_message("Continuum", json.dumps(CONTINUUM_REPLY)) == \
        "Continuum: Continuum inference for grok-4.7 is temporarily unavailable. Please try again."
    assert proxy_module._provider_error_message("xAI", "plain text") == "xAI: plain text"


@pytest_asyncio.fixture
async def continuum_503(proxy_module, monkeypatch):
    async def chat(request):
        return web.json_response(CONTINUUM_REPLY, status=503)
    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    monkeypatch.setattr(proxy_module, "CONTINUUM_BASE_URL", f"http://127.0.0.1:{server.port}/v1")
    monkeypatch.setattr(proxy_module, "continuum_api_key", "cont_sk_test_not_real")
    try:
        yield
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_continuums_exact_reply_reads_as_the_real_reason_direct(proxy_module, continuum_503):
    with pytest.raises(HTTPException) as exc:
        await proxy_module.call_continuum_direct(None, MSGS, "grok-4.7", 100)
    assert exc.value.status_code == 503
    assert exc.value.detail == "Continuum: Continuum inference for grok-4.7 is temporarily unavailable. Please try again."


@pytest.mark.asyncio
async def test_and_when_streaming(proxy_module, continuum_503):
    with pytest.raises(HTTPException) as exc:
        async for _ in proxy_module.call_continuum_streaming(None, MSGS, "grok-4.7", 100):
            pass
    assert exc.value.detail == "Continuum: Continuum inference for grok-4.7 is temporarily unavailable. Please try again."


def test_every_key_lane_goes_through_the_one_path(proxy_module):
    """xAI, Gemini, OpenAI, DeepSeek, Kilo and Continuum use the shared OpenAI-style helper; GLM and Nano-GPT have their own copies of it. No lane reads an
    error body on its own any more."""
    import inspect
    src = inspect.getsource(proxy_module)
    assert "detail=_extract_upstream_error_message(error_text))" not in src and "detail=_extract_upstream_error_message(error_body))" not in src
    assert src.count('_provider_error_message("GLM", error_text)') == 2 and src.count('_provider_error_message("Nano-GPT", error_body)') == 2
    assert src.count("_provider_error_message(provider_name, error_body)") == 2
    for call in ("call_xai_direct", "call_gemini_direct", "call_openai_direct", "call_deepseek_direct", "call_kilo_direct", "call_continuum_direct"):
        assert "_call_openai_compatible_direct(" in inspect.getsource(getattr(proxy_module, call)), call


def test_the_quick_test_window_says_what_to_do_only_where_the_help_tab_has_steps(test_client):
    html = test_client.get("/").text
    assert "See the Help tab for steps to fix this." in html and "Try again in a moment, or pick another model." in html
    assert "const helpHasSteps = /key|sign(ed)? in|credit|balance|billing|not installed|didn.t accept/i.test(raw);" in html
    assert "r.status >= 500 || r.status === 429" in html
    assert "const tail = helpHasSteps ? '. See the Help tab for steps to fix this.' : temporary ? '. Try again in a moment, or pick another model.' : '.';" in html
