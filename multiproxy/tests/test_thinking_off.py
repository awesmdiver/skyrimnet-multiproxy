"""SkyrimNet's "reasoning off" reaches the engines that can honour it (2026-10-07).

The real case: `"reasoning": {"enabled": false}` was dropped for LM Studio and Ollama, a Gemma 4 model wrote its reasoning first and ran out of reply budget, and the NPCs
said nothing. Real engines were tried by hand (see thinking_off.py); here a stand-in server mimics a thinking model: it answers only when the request turned thinking off.
"""
import json

import pytest
import pytest_asyncio
from aiohttp import web

import thinking_off
from tests.fixtures.courier_fakes import FakeLmStudio, FakeOllama, _Server


def _asks_off(body: dict) -> bool:
    return body.get("reasoning_effort") == "none"


class ThinkingStandIn(_Server):
    """A model that thinks: without thinking turned off, all of its budget goes on reasoning and the visible answer is empty."""

    def __init__(self):
        super().__init__()
        self.calls: list = []

    def app(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.h_chat)
        return app

    async def h_chat(self, request):
        body = await request.json()
        self.calls.append(body)
        thinks = not _asks_off(body)
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            delta = {"reasoning_content": "Let me think..."} if thinks else {"content": "Hello traveler."}
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta}]})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        message = {"role": "assistant", "content": "", "reasoning_content": "Let me think..."} if thinks else {"role": "assistant", "content": "Hello traveler."}
        return web.json_response({"choices": [{"index": 0, "message": message, "finish_reason": "length" if thinks else "stop"}]})


OFF = {"enabled": False}
SKYRIMNET_EXTRAS = {"reasoning": OFF, "provider": {"order": ["x"]}, "route": "fallback", "cache": True}


class TestTranslate:
    @pytest.mark.parametrize("extra", [{"reasoning": {"enabled": False}}, {"reasoning": {"effort": "none"}}, {"reasoning_effort": "minimal"}, {"reasoning_effort": "none"},
                                       {"thinking": {"type": "disabled"}}])
    def test_each_way_of_asking_for_no_thinking_is_seen(self, extra):
        assert thinking_off.off_requested(extra)

    @pytest.mark.parametrize("extra", [{}, {"reasoning": {"enabled": True}}, {"reasoning_effort": "high"}, {"thinking": {"type": "enabled"}}, {"temperature": 0.7}])
    def test_anything_else_is_left_alone(self, extra):
        assert not thinking_off.off_requested(extra)
        assert thinking_off.translate("ollama", extra) == (extra, False)

    def test_ollama_gets_reasoning_effort_none_and_no_unknown_field(self):
        out, done = thinking_off.translate("ollama", {"reasoning": OFF, "temperature": 0.5})
        assert done and out == {"reasoning_effort": "none", "temperature": 0.5}

    def test_lm_studio_gets_both_switches(self):
        out, done = thinking_off.translate("lmstudio", {"reasoning": OFF})
        assert done and out == {"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}}

    def test_a_players_own_template_setting_is_kept(self):
        out, _ = thinking_off.translate("lmstudio", {"reasoning": OFF, "chat_template_kwargs": {"x": 1}})
        assert out["chat_template_kwargs"] == {"x": 1}

    @pytest.mark.parametrize("engine", [None, "gemini", "multiproxy"])
    def test_an_engine_without_the_switch_is_untouched(self, engine):
        extra = {"reasoning": OFF}
        assert thinking_off.translate(engine, extra) == (extra, False)

    def test_which_program_is_which_engine(self):
        assert thinking_off.engine_of_program("lmstudio") == "lmstudio" == thinking_off.engine_of_program("LM Studio")
        assert thinking_off.engine_of_program("Ollama") == "ollama"
        assert thinking_off.engine_of_program("llamacpp") == "llamacpp" and thinking_off.engine_of_program("") is None   # (llama.cpp: 2026-10-25, see test_thinking_off_llamacpp.py)


@pytest.mark.asyncio
class TestOnTheWire:
    async def test_lm_studio_receives_thinking_off_not_the_dropped_field(self, proxy_module, monkeypatch):
        fake = await FakeLmStudio().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", fake.base + "/v1")
        try:
            await proxy_module.call_lmstudio_direct(None, [{"role": "user", "content": "hi"}], "m", 50, **SKYRIMNET_EXTRAS)
        finally:
            await fake.stop()
        sent = fake.chat_calls[-1]
        assert sent["reasoning_effort"] == "none" and sent["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning" not in sent and "route" not in sent and "cache" not in sent and "provider" not in sent

    async def test_ollama_receives_reasoning_effort_none(self, proxy_module, monkeypatch):
        fake = await FakeOllama().start()
        monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", fake.base + "/v1")
        monkeypatch.setattr(proxy_module, "ollama_api_key", None)
        try:
            await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "gemma4:12b", 50, **SKYRIMNET_EXTRAS)
        finally:
            await fake.stop()
        sent = fake.chat_calls[-1]
        assert sent["reasoning_effort"] == "none" and "reasoning" not in sent and "chat_template_kwargs" not in sent and "think" not in sent

    async def test_a_request_that_did_not_ask_gets_no_thinking_fields(self, proxy_module, monkeypatch):
        fake = await FakeLmStudio().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", fake.base + "/v1")
        try:
            await proxy_module.call_lmstudio_direct(None, [{"role": "user", "content": "hi"}], "m", 50, reasoning={"enabled": True}, temperature=0.3)
        finally:
            await fake.stop()
        sent = fake.chat_calls[-1]
        assert "reasoning_effort" not in sent and "chat_template_kwargs" not in sent and "reasoning" not in sent and sent["temperature"] == 0.3

    async def test_a_reasoning_level_reaches_ollama_as_reasoning_effort(self, proxy_module, monkeypatch):
        fake = await FakeOllama().start()
        monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", fake.base + "/v1")
        monkeypatch.setattr(proxy_module, "ollama_api_key", None)
        try:
            await proxy_module.call_ollama_direct(None, [{"role": "user", "content": "hi"}], "gemma4:12b", 50, reasoning_effort="high")
        finally:
            await fake.stop()
        assert fake.chat_calls[-1]["reasoning_effort"] == "high"          # (2026-10-20: SkyrimNet's low/medium/high levels reach a local Ollama; see test_reasoning_levels.py)


@pytest.mark.asyncio
class TestAThinkingModel:
    async def test_it_is_no_longer_empty_when_thinking_is_turned_off(self, proxy_module, monkeypatch):
        stand_in = await ThinkingStandIn().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", stand_in.base + "/v1")
        try:
            text, _ = await proxy_module.call_lmstudio_direct(None, [{"role": "user", "content": "hi"}], "gemma", 50, **SKYRIMNET_EXTRAS)
            assert text == "Hello traveler."
            # left on (SkyrimNet did not ask), the same model burns its budget: an empty answer that says it only thought
            text, usage = await proxy_module.call_lmstudio_direct(None, [{"role": "user", "content": "hi"}], "gemma", 50)
            assert text == "" and usage.get("reasoned") is True
        finally:
            await stand_in.stop()

    async def test_a_reasoning_only_stream_is_an_empty_reply_not_ok(self, proxy_module, monkeypatch):
        stand_in = await ThinkingStandIn().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", stand_in.base + "/v1")
        try:
            gen = proxy_module.call_lmstudio_streaming(None, [{"role": "user", "content": "hi"}], "gemma", 50)
            with pytest.raises(proxy_module.EmptyReply):
                await proxy_module._prime_stream(gen, "t")
        finally:
            await stand_in.stop()

    async def test_a_model_that_thinks_although_told_not_to_is_remembered_for_its_card(self, proxy_module, monkeypatch):
        class Stubborn(ThinkingStandIn):
            async def h_chat(self, request):                      # ignores the switch
                body = await request.json()
                body.pop("reasoning_effort", None)
                self.calls.append(body)
                return web.json_response({"choices": [{"message": {"content": "Hi.", "reasoning_content": "hmm"}, "finish_reason": "stop"}]})

        stand_in = await Stubborn().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", stand_in.base + "/v1")
        thinking_off.THINKERS._keys.clear()
        try:
            await proxy_module.call_lmstudio_direct(None, [{"role": "user", "content": "hi"}], "stubborn", 50, **SKYRIMNET_EXTRAS)
        finally:
            await stand_in.stop()
        row = proxy_module._lmstudio_row({"id": "stubborn", "loaded": True})
        assert row[2].endswith(thinking_off.THINKS_NOTE) and row[2].startswith("Loaded")
        assert thinking_off.THINKS_NOTE not in proxy_module._lmstudio_row({"id": "other", "loaded": True})[2]
        thinking_off.THINKERS._keys.clear()

    async def test_a_model_that_obeys_gets_no_note(self, proxy_module, monkeypatch):
        stand_in = await ThinkingStandIn().start()
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", stand_in.base + "/v1")
        thinking_off.THINKERS._keys.clear()
        try:
            await proxy_module.call_lmstudio_direct(None, [{"role": "user", "content": "hi"}], "gemma", 50, **SKYRIMNET_EXTRAS)
        finally:
            await stand_in.stop()
        assert thinking_off.THINKS_NOTE not in proxy_module._lmstudio_row({"id": "gemma", "loaded": False})[2]
