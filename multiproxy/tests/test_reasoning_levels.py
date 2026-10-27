"""SkyrimNet's six reasoning levels reach every kind of AI in a form it takes (2026-10-20, Beta 27 final / Beta 28).

SkyrimNet's "Allow Reasoning" is off, model default, minimal, low, medium or high, sent per model, and it can say that in several spellings (`reasoning: false`,
`reasoning: {"enabled": false}`, `reasoning: {"effort": ...}`, `reasoning_effort`). Each kind of AI MultiProxy talks to is stood in for by a real server on its own socket that
REFUSES a field it doesn't know the way a real one does (HTTP 400 "Unrecognized request argument supplied: ..."), and a value it doesn't know. For every level in every
spelling the request must go through (no error), and what the stand-in received is exactly what the table in `EXPECT` says. See thinking_off.py for the choices made:
`minimal` is off for Ollama and LM Studio (neither has a minimal level), low/medium/high are sent as `reasoning_effort`, a local llama.cpp is started with reasoning off
and is sent nothing, GLM and DeepSeek get their own thinking switch, and the strict clouds (OpenAI, Gemini, xAI) get only SkyrimNet's own `reasoning_effort`.
"""
import json

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

import thinking_off
from tests.fixtures.courier_fakes import free_port

KNOWN = {"model", "messages", "max_tokens", "max_completion_tokens", "stream", "temperature"}
REASONING_FIELDS = ("reasoning", "reasoning_effort", "thinking", "chat_template_kwargs")
VALUES = {"none", "minimal", "low", "medium", "high"}          # what a stand-in's reasoning_effort takes


class StrictEngine:
    """An AI that answers only requests made of fields it knows. `accepts`: the reasoning fields it knows. A 400 for the rest, worded as the real ones do."""

    def __init__(self, accepts, mandatory=False):
        self.port = free_port()
        self.accepts = set(accepts)
        self.mandatory = mandatory
        self.bodies: list = []
        self.runner = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        await self.runner.cleanup()

    async def chat(self, request):
        body = await request.json()
        self.bodies.append(body)
        if self.mandatory and any(k in body for k in REASONING_FIELDS):
            return web.json_response({"error": {"message": "Reasoning is mandatory for this model and cannot be disabled.", "type": "invalid_request_error"}}, status=400)
        for k in body:
            if k not in KNOWN and k not in self.accepts:
                return web.json_response({"error": {"message": f"Unrecognized request argument supplied: {k}", "type": "invalid_request_error"}}, status=400)
        if "reasoning_effort" in body and body["reasoning_effort"] not in VALUES:
            return web.json_response({"error": {"message": f"Invalid value: '{body['reasoning_effort']}'", "type": "invalid_request_error"}}, status=400)
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'hello there'}}]})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "hello there"}, "finish_reason": "stop"}], "usage": {}})

    def reasoning_seen(self) -> dict:
        return {k: v for k, v in self.bodies[-1].items() if k in REASONING_FIELDS}


# The spellings of each level SkyrimNet can use.
def spellings(level):
    if level == "default":
        return [{}, {"reasoning": True}, {"reasoning": {"enabled": True}}]
    if level == "off":
        return [{"reasoning": False}, {"reasoning": {"enabled": False}}, {"reasoning_effort": "none"}, {"reasoning": {"effort": "none"}}, {"thinking": {"type": "disabled"}}]
    return [{"reasoning_effort": level}, {"reasoning": {"effort": level}}]


LEVELS = ["default", "off", "minimal", "low", "medium", "high"]
OFF = {"off", "minimal"}
DISABLED = {"thinking": {"type": "disabled"}}


def ollama_expect(level, spelling):
    return {"reasoning_effort": "none"} if level in OFF else {"reasoning_effort": level} if level in ("low", "medium", "high") else {}


def lmstudio_expect(level, spelling):
    return {"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}} if level in OFF else ollama_expect(level, spelling)


def nothing(level, spelling):
    return {}


def own_switch_expect(level, spelling):         # GLM, DeepSeek: thinking is off by default for NPC dialogue; a level turns it on at that effort
    return {"reasoning_effort": level} if level in ("low", "medium", "high") else DISABLED


def effort_only_expect(level, spelling):        # strict clouds: SkyrimNet's own `reasoning_effort` is passed as it is; a `reasoning` object is not a field they take
    return {"reasoning_effort": spelling["reasoning_effort"]} if "reasoning_effort" in spelling else {}


def call_of(proxy_module, monkeypatch, kind, engine):
    """(the function that asks this kind of AI, with its address and key pointed at `engine`)."""
    pm = proxy_module
    if kind == "ollama":
        monkeypatch.setattr(pm, "OLLAMA_LOCAL_BASE_URL", engine.base)
        monkeypatch.setattr(pm, "ollama_api_key", None)
        return pm.call_ollama_direct, pm.call_ollama_streaming
    if kind == "lmstudio":
        monkeypatch.setattr(pm, "lmstudio_base_url", engine.base)
        return pm.call_lmstudio_direct, pm.call_lmstudio_streaming
    if kind == "llamacpp":
        async def base(model):
            return engine.base
        monkeypatch.setattr(pm, "_local_base_url", base)
        return pm.call_local_direct, pm.call_local_streaming
    keyed = {"glm": ("GLM_BASE_URL", "glm_api_key", "call_glm_direct", "call_glm_streaming"),
             "deepseek": ("DEEPSEEK_BASE_URL", "deepseek_api_key", "call_deepseek_direct", "call_deepseek_streaming"),
             "openai": ("OPENAI_BASE_URL", "openai_api_key", "call_openai_direct", "call_openai_streaming"),
             "gemini": ("GEMINI_BASE_URL", "gemini_api_key", "call_gemini_direct", "call_gemini_streaming"),
             "xai": ("XAI_BASE_URL", "xai_api_key", "call_xai_direct", "call_xai_streaming"),
             "kilo": ("KILO_BASE_URL", "kilo_api_key", "call_kilo_direct", "call_kilo_streaming")}[kind]
    monkeypatch.setattr(pm, keyed[0], engine.base)
    monkeypatch.setattr(pm, keyed[1], "FAKE-KEY")
    return getattr(pm, keyed[2]), getattr(pm, keyed[3])


# kind -> (the reasoning fields the stand-in knows, what it must have been sent)
KINDS = {
    "ollama": ({"reasoning_effort"}, ollama_expect),
    "lmstudio": ({"reasoning_effort", "chat_template_kwargs"}, lmstudio_expect),
    "llamacpp": (set(), nothing),
    "glm": ({"thinking", "reasoning_effort"}, own_switch_expect),
    "deepseek": ({"thinking", "reasoning_effort"}, own_switch_expect),
    "openai": ({"reasoning_effort"}, effort_only_expect),
    "gemini": ({"reasoning_effort"}, effort_only_expect),
    "xai": ({"reasoning_effort"}, effort_only_expect),
    "kilo": ({"reasoning_effort"}, effort_only_expect),
}


async def drain(gen):
    return [x async for x in gen]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("level", LEVELS)
async def test_every_level_in_every_spelling_goes_through_and_arrives_in_the_form_the_ai_takes(proxy_module, monkeypatch, kind, level):
    accepts, expect = KINDS[kind]
    engine = await StrictEngine(accepts).start()
    try:
        direct, streaming = call_of(proxy_module, monkeypatch, kind, engine)
        for spelling in spellings(level):
            msgs = [{"role": "user", "content": "hi"}]
            text, _usage = await direct(None, msgs, "some-model", 50, temperature=0.5, **spelling)
            assert text == "hello there", (kind, level, spelling)
            assert engine.reasoning_seen() == expect(level, spelling), (kind, level, spelling, engine.bodies[-1])
            events = await drain(streaming(None, msgs, "some-model", 50, temperature=0.5, **spelling))
            assert any("hello there" in e for e in events), (kind, level, spelling)
            assert engine.reasoning_seen() == expect(level, spelling), ("streamed", kind, level, spelling, engine.bodies[-1])
    finally:
        await engine.stop()


# ---- the old way: SkyrimNet's `reasoning: {"enabled": false}` with all its other fields, and a request that asked for nothing ----

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ollama", "lmstudio", "llamacpp", "glm", "openai", "gemini"])
async def test_the_fields_only_openrouter_knows_never_cause_an_error(proxy_module, monkeypatch, kind):
    accepts, _ = KINDS[kind]
    engine = await StrictEngine(accepts).start()
    try:
        direct, _s = call_of(proxy_module, monkeypatch, kind, engine)
        text, _u = await direct(None, [{"role": "user", "content": "hi"}], "m", 50, reasoning={"enabled": False}, provider={"order": ["x"]}, route="fallback", cache=True,
                                include_reasoning=False)
        assert text == "hello there"
        assert not {"provider", "route", "cache", "include_reasoning"} & set(engine.bodies[-1])
    finally:
        await engine.stop()


# ---- "reasoning is mandatory": SkyrimNet retries a model that refuses `off` once at minimal, so it has to be able to read the refusal ----

@pytest_asyncio.fixture
async def mandatory(proxy_module, monkeypatch):
    engine = await StrictEngine({"reasoning_effort", "chat_template_kwargs", "thinking"}, mandatory=True).start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", engine.base)
    monkeypatch.setattr(proxy_module, "OPENAI_BASE_URL", engine.base)
    monkeypatch.setattr(proxy_module, "openai_api_key", "FAKE-KEY")
    monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", engine.base)
    monkeypatch.setattr(proxy_module, "ollama_api_key", None)
    yield engine
    await engine.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["lmstudio/some-model", "openai/some-model", "ollama/some-model"])
@pytest.mark.parametrize("stream", [False, True])
async def test_a_model_that_says_reasoning_is_mandatory_is_heard_by_skyrimnet_with_its_own_words_and_status(proxy_module, mandatory, model, stream):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    try:
        r = await client.post("/v1/chat/completions", json={"model": model, "stream": stream, "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "none"})
    finally:
        await client.aclose()
    assert r.status_code == 400, r.text
    assert "reasoning is mandatory" in r.text.lower(), r.text


@pytest.mark.asyncio
async def test_asked_again_at_minimal_the_same_model_gets_the_least_it_can_take(proxy_module, monkeypatch):
    """What SkyrimNet does next: the retry says `minimal`. For an engine with no minimal level that is off again (so it is refused again, and said so); for a cloud it is passed
    as the word SkyrimNet chose."""
    engine = await StrictEngine({"reasoning_effort"}).start()
    try:
        direct, _s = call_of(proxy_module, monkeypatch, "openai", engine)
        await direct(None, [{"role": "user", "content": "hi"}], "m", 50, reasoning_effort="minimal")
        assert engine.reasoning_seen() == {"reasoning_effort": "minimal"}
    finally:
        await engine.stop()


# ---- the words themselves ----

@pytest.mark.parametrize("extra,level", [
    ({}, None), ({"reasoning": True}, None), ({"reasoning": {"enabled": True}}, None), ({"reasoning_effort": "default"}, None), ({"reasoning_effort": "xhigh"}, None),
    ({"reasoning": False}, "off"), ({"reasoning": {"enabled": False}}, "off"), ({"reasoning_effort": "NONE"}, "off"), ({"reasoning": {"effort": "off"}}, "off"),
    ({"thinking": {"type": "disabled"}}, "off"), ({"thinking": {"type": "enabled"}}, None),
    ({"reasoning_effort": "minimal"}, "minimal"), ({"reasoning": {"effort": "Minimal"}}, "minimal"),
    ({"reasoning_effort": "low"}, "low"), ({"reasoning": {"effort": "medium"}}, "medium"), ({"reasoning": "high"}, "high"),
    ({"reasoning": {"enabled": True, "effort": "high"}}, "high"),
])
def test_each_spelling_is_read_as_its_level(extra, level):
    assert thinking_off.level_of(extra) == level


def test_minimal_is_off_for_a_local_engine_and_a_level_is_a_level():
    assert thinking_off.off_requested({"reasoning_effort": "minimal"}) and thinking_off.off_requested({"reasoning": False})
    assert not thinking_off.off_requested({"reasoning_effort": "low"}) and not thinking_off.off_requested({"reasoning": True})
    out, done = thinking_off.translate_level("ollama", {"reasoning": {"effort": "medium"}, "temperature": 0.4})
    assert done and out == {"reasoning_effort": "medium", "temperature": 0.4}
    assert thinking_off.translate_level("ollama", {"reasoning_effort": "minimal"}) == ({"reasoning_effort": "minimal"}, False)
    assert thinking_off.translate_level("gemini", {"reasoning_effort": "high"}) == ({"reasoning_effort": "high"}, False)      # (llamacpp joined the engines 2026-10-25)


def test_an_ai_with_its_own_thinking_field_gets_one_spelling():
    assert thinking_off.normalised({"reasoning": False, "reasoning_effort": "none", "temperature": 1}) == {"temperature": 1}
    assert thinking_off.normalised({"reasoning": {"effort": "high"}}) == {"reasoning_effort": "high"}
    extra = {"thinking": {"type": "enabled"}, "temperature": 1}
    assert thinking_off.normalised(extra) is extra


def test_a_request_in_the_ais_own_dialect_is_left_as_it_is():
    both = {"reasoning_effort": "low", "thinking": {"type": "disabled"}}
    assert thinking_off.normalised(both) is both
