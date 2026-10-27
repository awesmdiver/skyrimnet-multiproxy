"""Quick decisions answered by Jev through Nano-GPT, with the player's own Nano-GPT key (queue: jev-decisions-can-go-through-nano-gpt, 2026-10-05).
A stand-in server on 127.0.0.1 speaks Nano-GPT's System One shape (nothing real is called, and no real key is ever used or printed): the order with every combination
of keys, the call that is made, the extra fields that stay out of what SkyrimNet gets, a refusal passing the decision on with its plain line, Speeds, the setting."""
import asyncio
import json
import logging
import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

import decision_ui
from tests.test_decision_order import LOCAL_ANSWER, deepseek, lines, local, ts_session      # noqa: F401  (local is a fixture)
from tests.test_jev_provider import REAL_JEV_REQUEST

FAKE_KEY = "sk-nano-FAKE-0000"
ANSWERS = {"intent": {"type": "choice", "choice": "greeting", "confidence": 0.9, "probabilities": {"complaint": 0.0, "greeting": 0.9, "other": 0.0, "question": 0.1, "request": 0.0}}}
NANO_BODY = {"id": "req-123", "model": "typesafe/jev-latest", "provider": "TypeSafe", "answers": ANSWERS, "usage": {"input_tokens": 77, "output_tokens": 0}}


class StandIn:
    """A Nano-GPT System One look-alike on 127.0.0.1: answers with `status`/`body`, remembers what it was sent."""

    def __init__(self):
        self.status, self.body, self.calls = 200, NANO_BODY, []
        self.loop, self.runner, self.url = asyncio.new_event_loop(), None, ""
        ready = threading.Event()

        async def handle(request):
            self.calls.append({"headers": dict(request.headers), "body": await request.json(), "path": request.path})
            return web.json_response(self.body, status=self.status) if not isinstance(self.body, bytes) else web.Response(body=self.body, status=self.status)

        async def start():
            app = web.Application()
            app.router.add_post("/api/v1/systemone", handle)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/v1/systemone"
            ready.set()

        self.thread = threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True)
        self.thread.start()
        ready.wait(5)

    def close(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def nano(proxy_module, monkeypatch):
    server = StandIn()
    monkeypatch.setattr(proxy_module, "NANO_DECISIONS_URL", server.url)
    monkeypatch.setattr(proxy_module, "auth", MagicMock(session=None, is_ready=False))
    monkeypatch.setattr(proxy_module, "nano_api_key", FAKE_KEY)
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    yield server
    server.close()
    proxy_module.decision_handles.nano_line, proxy_module.decision_handles.last_answer = "", None


def ask(test_client, body=None, path="/v1/systemone"):
    return test_client.post(path, json=body or REAL_JEV_REQUEST)


def not_installed(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)


class TestOrderWithoutTheLocalModel:
    def test_no_typesafe_key_but_a_nano_key_jev_goes_through_nano_gpt(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        r = ask(test_client)
        assert r.status_code == 200 and r.json()["answers"] == ANSWERS and len(nano.calls) == 1

    def test_the_call_has_the_right_model_bearer_key_and_body(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        ask(test_client)
        c = nano.calls[0]
        assert c["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" and c["path"] == "/api/v1/systemone"
        assert c["body"] == {"model": "jev-latest", "state": REAL_JEV_REQUEST["state"], "questions": REAL_JEV_REQUEST["questions"]}

    def test_the_address_is_the_documented_one(self, proxy_module):
        assert proxy_module.NANO_DECISIONS_URL == "https://api.nano-gpt.com/api/v1/systemone" and proxy_module.NANO_DECISIONS_MODEL == "jev-latest"

    def test_nano_gpts_extra_fields_do_not_reach_skyrimnet(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        body = ask(test_client).json()
        assert "id" not in body and "provider" not in body and body["usage"] == NANO_BODY["usage"] and set(body) == {"model", "answers", "usage"}

    def test_a_typesafe_key_wins_and_nano_gpt_is_not_asked(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        session = ts_session()
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), patch("aiohttp.ClientSession", return_value=session):
            r = ask(test_client)
        assert r.status_code == 200 and session.post.call_count == 1 and nano.calls == []

    def test_neither_key_a_stand_in_answers_and_is_recorded_under_its_own_name(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "nano_api_key", None)
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            r = ask(test_client)
        assert r.status_code == 200 and nano.calls == [] and ds.await_count == 1

    def test_nothing_ready_is_the_existing_clear_error(self, proxy_module, test_client, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "nano_api_key", None)
        monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
        for name in ("deepseek", "glm", "gemini", "openai", "kilo"):
            monkeypatch.setattr(proxy_module, f"{name}_api_key", None)
        monkeypatch.setattr(proxy_module, "auth", MagicMock(session=None, is_ready=False))
        r = ask(test_client)
        assert r.status_code == 500 and "No TypeSafe key found" in r.json()["error"]["message"]


class TestARefusalPassesTheDecisionOn:
    @pytest.mark.parametrize("status,why", [(401, "your key may not be right"), (402, "your balance may be empty"), (404, "it can't find the decision model"),
                                            (429, "it is too busy right now")])
    def test_another_ai_answers_and_the_line_says_so(self, proxy_module, test_client, nano, monkeypatch, status, why):
        not_installed(proxy_module, monkeypatch)
        nano.status, nano.body = status, {"error": "nope"}
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            r = ask(test_client)
        assert r.status_code == 200 and ds.await_count == 1
        line = f"Nano-GPT said no ({why}). Another AI answered instead."
        assert proxy_module.decision_handles.nano_line == line
        assert test_client.get("/config/decisions/status").json()["nanoLine"] == line

    def test_an_unexpected_failure_has_a_plain_line_too(self):
        import multiproxy as m
        assert m.nano_decision_line(500, True) == "Nano-GPT didn't answer. Another AI answered instead."
        assert m.nano_decision_line(402, False) == "Nano-GPT said no (your balance may be empty). No other AI was ready to answer."

    def test_the_stand_in_is_never_nano_gpts_own_chat_model_after_nano_gpt_said_no(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        nano.status, nano.body = 402, {"error": "no balance"}
        nano_chat = AsyncMock(return_value=("{}", {}))
        for name in ("deepseek", "glm", "gemini", "openai", "kilo"):
            monkeypatch.setattr(proxy_module, f"{name}_api_key", None)
        with patch.object(proxy_module, "call_nano_direct", nano_chat):
            r = ask(test_client)
        nano_chat.assert_not_called()
        assert r.status_code == 402 and r.json()["error"]["message"] == "Nano-GPT said no (your balance may be empty). No other AI was ready to answer."

    def test_an_answer_without_answers_counts_as_a_miss(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        nano.body = {"id": "x", "provider": "TypeSafe"}
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            assert ask(test_client).status_code == 200
        assert ds.await_count == 1 and proxy_module.decision_handles.nano_line.startswith("Nano-GPT didn't answer.")

    def test_a_later_answer_clears_the_line(self, proxy_module, test_client, nano, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        proxy_module.decision_handles.nano_line = "Nano-GPT said no (your balance may be empty). Another AI answered instead."
        assert ask(test_client).status_code == 200
        assert proxy_module.decision_handles.nano_line == ""

    def test_saving_a_new_key_clears_the_line(self, proxy_module, test_client):
        proxy_module.decision_handles.nano_line = "Nano-GPT said no (your key may not be right). Another AI answered instead."
        test_client.post("/config/nano-key", json={"key": FAKE_KEY})
        assert proxy_module.decision_handles.nano_line == ""


class TestSpeeds:
    def _rows(self, proxy_module, monkeypatch, tmp_path):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        db = tmp_path / "speeds.db"
        monkeypatch.setattr(proxy_module, "SPEED_HISTORY_DB", str(db))
        proxy_module._init_speed_history_db()
        return db

    @staticmethod
    def read(db):
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM speed_history")]

    def test_the_answer_is_recorded_as_nano_gpt_with_the_decisions_role(self, proxy_module, test_client, nano, monkeypatch, tmp_path):
        not_installed(proxy_module, monkeypatch)
        db = self._rows(proxy_module, monkeypatch, tmp_path)
        ask(test_client)
        rows = self.read(db)
        assert len(rows) == 1 and rows[0]["provider"] == "Nano-GPT" and rows[0]["model"] == "jev-latest" and rows[0]["success"] == 1 and "Decisions" in rows[0]["roles_json"]

    def test_a_refusal_is_recorded_as_a_failed_nano_gpt_row_and_the_stand_in_under_its_own_name(self, proxy_module, test_client, nano, monkeypatch, tmp_path):
        not_installed(proxy_module, monkeypatch)
        db = self._rows(proxy_module, monkeypatch, tmp_path)
        nano.status, nano.body = 429, {"error": "slow"}
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", deepseek()):
            ask(test_client)
        rows = sorted(self.read(db), key=lambda r: r["provider"])
        assert [(r["provider"], r["success"]) for r in rows] == [("DeepSeek", 1), ("Nano-GPT", 0)] and rows[1]["model"] == "jev-latest"
        assert all("Decisions" in r["roles_json"] for r in rows)


class TestNoKeyIsLogged:
    def test_the_key_never_reaches_the_log_even_on_a_refusal(self, proxy_module, test_client, nano, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        not_installed(proxy_module, monkeypatch)
        nano.status, nano.body = 401, {"error": f"bad key {FAKE_KEY}"}
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", deepseek()):
            ask(test_client)
            nano.status, nano.body = 200, NANO_BODY
            ask(test_client)
        assert not any(FAKE_KEY in r.getMessage() for r in caplog.records)


class TestWithTheLocalModel:
    def test_the_order_puts_the_choice_first_then_local_then_the_other_paid_one(self, proxy_module, local):
        h = proxy_module.decision_handles
        local()
        assert h.order("x", True, True) == ["local", "typesafe", "nano", "standin"]
        assert h.order("x", False, True) == ["local", "nano", "standin"] and h.order("x", False, False) == ["local", "standin"]
        local(answer_first="typesafe")
        assert h.order("x", True, True) == ["typesafe", "local", "nano", "standin"]
        local(answer_first="nanogpt")
        assert h.order("x", True, True) == ["nano", "local", "typesafe", "standin"] and h.order("x", False, True) == ["nano", "local", "standin"]
        assert h.order("x", True, False) == ["local", "typesafe", "standin"]

    def test_nano_gpt_first_answers_and_the_local_model_is_not_asked(self, proxy_module, test_client, local, nano):
        stub = local(answer_first="nanogpt")
        r = ask(test_client)
        assert r.status_code == 200 and r.json()["answers"] == ANSWERS and stub.calls == 0 and len(nano.calls) == 1
        assert proxy_module.decision_handles.last_answer["by"] == "Nano-GPT"

    def test_when_the_local_model_is_too_slow_nano_gpt_takes_over(self, proxy_module, test_client, local, nano):
        local("too_slow")
        r = ask(test_client)
        assert r.status_code == 200 and r.json()["answers"] == ANSWERS and len(nano.calls) == 1
        la = proxy_module.decision_handles.last_answer
        assert la["by"] == "Nano-GPT" and la["first"] == "local" and la["miss"] == "too_slow"

    def test_nano_gpt_first_and_it_says_no_so_the_local_model_answers_and_the_card_says_why(self, proxy_module, test_client, local, nano):
        stub = local(answer_first="nanogpt")
        nano.status, nano.body = 402, {"error": "x"}
        r = ask(test_client)
        assert r.status_code == 200 and r.json()["answers"] == LOCAL_ANSWER["answers"] and stub.calls == 1
        assert proxy_module.decision_handles.nano_line == "Nano-GPT said no (your balance may be empty). Another AI answered instead."
        assert proxy_module.decision_handles.last_answer["by"] == "local" and proxy_module.decision_handles.last_answer["miss"] == "nano"

    def test_when_everything_before_it_fails_and_it_says_no_too_the_stand_in_answers(self, proxy_module, test_client, local, nano):
        local("stopped")
        nano.status, nano.body = 429, {"error": "x"}
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", deepseek()):
            r = ask(test_client)
        assert r.status_code == 200 and proxy_module.decision_handles.nano_line == "Nano-GPT said no (it is too busy right now). Another AI answered instead."


class TestTheSetting:
    def test_nanogpt_is_a_saved_choice(self, test_client):
        assert test_client.post("/config/decisions/settings", json={"answerFirst": "nanogpt"}).json()["settings"]["answer_first"] == "nanogpt"
        assert test_client.post("/config/decisions/settings", json={"answerFirst": "other"}).status_code == 400

    def test_the_card_has_the_third_choice_and_its_disabled_reason(self):
        js = decision_ui.DECISION_JS
        assert "const keyed = id === 'nano' ? !!s.nanoKey : p.keyOk !== false;" in js and "word: ready ? 'Ready' : (keyed ? 'Stopped' : 'No key')" in js, "Nano-GPT is a row of the list: Ready, or No key with an Add a key link"
        assert "so that choice is off" not in js and "so the local model answers" not in js
        assert "charges a small amount" not in js

    def test_the_status_knows_the_key_the_line_and_when_nano_gpt_is_the_one_answering(self, proxy_module, test_client, monkeypatch, local):
        d = test_client.get("/config/decisions/status").json()
        assert d["nanoKey"] is False and d["nanoLine"] == "" and d["nanoActive"] is False
        monkeypatch.setattr(proxy_module, "nano_api_key", FAKE_KEY)
        d = test_client.get("/config/decisions/status").json()
        assert d["nanoKey"] is True and d["nanoActive"] is True          # not installed, no TypeSafe key: Nano-GPT answers
        local()
        assert test_client.get("/config/decisions/status").json()["nanoActive"] is False          # the local model answers first
        local(answer_first="nanogpt")
        assert test_client.get("/config/decisions/status").json()["nanoActive"] is True
        monkeypatch.setattr(proxy_module, "typesafe_api_key", "ts")
        local(answer_first="typesafe")
        assert test_client.get("/config/decisions/status").json()["nanoActive"] is False


def test_the_overview_names_nano_gpt_as_the_decision_backup(proxy_module, monkeypatch, local):
    import overview
    local(answer_first="local")
    h = proxy_module.decision_handles
    order = h.order(None, False, True)
    after = [x for x in order if x != "local"]
    assert after[0] == "nano"
    assert overview._service_line("decisions", {"fallback": "Nano-GPT"}) == "Nano-GPT answers decisions until it comes back."
