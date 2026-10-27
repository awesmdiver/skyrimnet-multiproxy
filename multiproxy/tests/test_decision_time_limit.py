"""Quick decisions give up on a slow TypeSafe or Nano-GPT answer (queue: quick-decisions-give-up-on-a-slow-paid-answer, 2026-10-05). A stand-in server on 127.0.0.1 that
sleeps plays the paid answerer (nothing real is called, no real key is used or printed); the limit is made short for the test, and its setting is checked on its own."""
import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

import decision_ui
from tests.test_decision_order import StubEngine, local      # noqa: F401  (local is a fixture)
from tests.test_jev_provider import REAL_JEV_REQUEST
from tests.test_quicktest_decisions import COMBAT_ANSWERS, run

ANSWER = {"model": "m", "answers": {"intent": {"type": "choice", "choice": "greeting", "confidence": 0.9, "probabilities": {"greeting": 0.9}}}, "usage": {"input_tokens": 3, "output_tokens": 0}}


class SleepyServer:
    """Answers every POST on any path after `delay` seconds; counts the calls it received."""

    def __init__(self, delay=0.0):
        self.delay, self.calls = delay, 0
        self.loop, self.url = asyncio.new_event_loop(), ""
        ready = threading.Event()

        async def handle(request):
            self.calls += 1
            await request.read()
            await asyncio.sleep(self.delay)
            return web.json_response(self.body)

        async def start():
            app = web.Application()
            app.router.add_post("/{tail:.*}", handle)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/systemone"
            ready.set()
        self.body = ANSWER
        threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True).start()
        ready.wait(5)

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def slow(proxy_module, monkeypatch):
    server = SleepyServer(delay=1.0)
    monkeypatch.setattr(proxy_module, "auth", MagicMock(session=None, is_ready=False))
    monkeypatch.setattr(proxy_module, "_paid_limit", lambda: 0.2)
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)
    yield server
    server.close()
    proxy_module.decision_handles.nano_line = proxy_module.decision_handles.typesafe_line = ""
    proxy_module.decision_handles.last_answer = None


def as_nano(proxy_module, monkeypatch, server):
    monkeypatch.setattr(proxy_module, "NANO_DECISIONS_URL", server.url)
    monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")


def as_typesafe(proxy_module, monkeypatch, server):
    monkeypatch.setattr(proxy_module, "TYPESAFE_BASE_URL", server.url)
    monkeypatch.setattr(proxy_module, "typesafe_api_key", "ts-FAKE")


def stand_in(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    return patch.object(proxy_module, "call_deepseek_direct", AsyncMock(return_value=(json.dumps({"intent": "greeting"}), {"prompt_tokens": 3, "completion_tokens": 1})))


# ---- Nano-GPT --------------------------------------------------------------------------------------------------------------------------------------
def test_a_slow_nano_gpt_answer_is_cancelled_and_the_stand_in_answers_with_the_line(proxy_module, test_client, slow, monkeypatch):
    as_nano(proxy_module, monkeypatch, slow)
    seen = []
    real = proxy_module._record_speed

    async def spy(*a, **k):
        seen.append((k.get("provider"), k.get("fail_reason")))
        return await real(*a, **k)
    monkeypatch.setattr(proxy_module, "_record_speed", spy)
    with stand_in(proxy_module, monkeypatch) as ds:
        r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
    assert r.status_code == 200 and ds.await_count == 1
    assert slow.calls == 1, "tried once: no retry inside a decision"
    assert proxy_module.decision_handles.nano_line == "Nano-GPT was too slow. Another AI answered instead."
    assert ("Nano-GPT", "timeout") in seen, "Speeds gets a failed row for the slow provider"


def test_if_nobody_else_is_ready_the_existing_no_other_ai_wording_follows(proxy_module, test_client, slow, monkeypatch):
    as_nano(proxy_module, monkeypatch, slow)
    r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
    assert r.status_code == 502 and proxy_module.decision_handles.nano_line == "Nano-GPT was too slow. No other AI was ready to answer."


def test_a_fast_answer_is_untouched(proxy_module, test_client, slow, monkeypatch):
    slow.delay = 0.0
    as_nano(proxy_module, monkeypatch, slow)
    r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
    assert r.status_code == 200 and r.json()["answers"] == ANSWER["answers"] and proxy_module.decision_handles.nano_line == ""


# ---- TypeSafe ---------------------------------------------------------------------------------------------------------------------------------------
def test_a_slow_typesafe_answer_moves_on_to_the_stand_in(proxy_module, test_client, slow, monkeypatch):
    as_typesafe(proxy_module, monkeypatch, slow)
    with stand_in(proxy_module, monkeypatch):
        r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
    assert r.status_code == 200 and slow.calls == 1
    assert proxy_module.decision_handles.typesafe_line == "TypeSafe was too slow. Another AI answered instead."


def test_in_the_order_with_the_local_model_a_slow_typesafe_passes_to_it(proxy_module, test_client, slow, local, monkeypatch):      # noqa: F811
    stub = local(answer_first="typesafe")
    as_typesafe(proxy_module, monkeypatch, slow)
    r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
    assert r.status_code == 200 and stub.calls == 1 and slow.calls == 1
    assert proxy_module.decision_handles.typesafe_line == "TypeSafe was too slow. Another AI answered instead."


def test_the_local_model_is_not_given_a_paid_limit(proxy_module):
    import inspect
    src = inspect.getsource(proxy_module.decision_handles.try_local)
    assert "paid" not in src


# ---- the setting ------------------------------------------------------------------------------------------------------------------------------------
def test_the_limit_helpers_and_the_default(proxy_module):
    assert proxy_module._decision_limit_kwargs(None) == {}
    k = proxy_module._decision_limit_kwargs(6)
    assert k["attempts"] == 1 and k["timeout"].total == 6
    assert proxy_module.DECISION_PAID_LIMIT == 6.0 and proxy_module.decision_handles.settings()["paid_limit_seconds"] == 6.0


def test_the_setting_is_one_number_from_two_to_nine_and_is_saved_like_the_others(proxy_module, test_client):
    for bad in (1, 9.5, "x", None):
        assert test_client.post("/config/decisions/settings", json={"paidLimitSeconds": bad}).status_code == 400
    r = test_client.post("/config/decisions/settings", json={"paidLimitSeconds": 4})
    assert r.status_code == 200 and r.json()["settings"]["paid_limit_seconds"] == 4.0
    assert proxy_module._paid_limit() == 4.0
    proxy_module._save_config({"decisions": {"paid_limit_seconds": 50}})
    assert proxy_module._paid_limit() == 9.0, "a hand-edited value is kept inside the range"
    proxy_module._save_config({"decisions": {}})


# ---- Quick Test for Quick decisions ---------------------------------------------------------------------------------------------------
def test_the_quick_test_says_it_and_names_the_next_step(proxy_module, test_client, slow, monkeypatch):
    as_nano(proxy_module, monkeypatch, slow)
    r = run(test_client, "nanogpt")
    assert r["ok"] is False and r["error"] == "Nano-GPT was too slow." and r["next"] == "Next in line: another AI standing in."
    as_typesafe(proxy_module, monkeypatch, slow)
    r = run(test_client, "typesafe")
    assert r["ok"] is False and r["error"] == "TypeSafe was too slow."
    assert proxy_module.decision_handles.nano_line == "" and proxy_module.decision_handles.typesafe_line == "", "a test leaves the card's lines as they were"


# ---- the card ---------------------------------------------------------------------------------------------------------------------------------------
def test_the_card_has_the_number_box_and_shows_the_typesafe_line():
    js = decision_ui.DECISION_JS
    assert "if the current one takes longer than " in js and "dcSetting({ paidLimitSeconds: this.value })" in js and "% sure.</label>" in js
    assert "answerLines" in js and "typesafeLine: st.typesafeLine" in js          # a service's own failure line sits under its row
