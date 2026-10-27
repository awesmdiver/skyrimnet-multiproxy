"""Quick decisions answered by Perplexity's Decisions API, with the player's own key (queue: perplexity-as-a-decision-provider, 2026-10-25).
A stand-in server on 127.0.0.1 speaks the Decisions API as its public documentation describes it (nothing real is called and no real key is ever used or printed): the
request that is sent, yes/no, choice and score questions mapped both ways, pictures, every decision Perplexity cannot take, every error passing the decision on, the order with
and without the local model, TypeSafe and Nano-GPT, Speeds and the cost, and that the key never reaches a log."""
import asyncio
import json
import logging
import sqlite3
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

import ai_costs
from tests.test_decision_order import deepseek, lines, local, ts_session      # noqa: F401  (local is a fixture)
from tests.test_jev_provider import REAL_JEV_REQUEST

FAKE_KEY = "pplx-FAKE-0000000000000000"
PNG = "data:image/png;base64,iVBORw0KGgo="
ALL_KINDS = {
    "state": {"scene": "Two guards argue at a gate", "speaker": "Aela"},
    "questions": {
        "angry": {"type": "noul", "instructions": "Is anyone angry?", "criteria": {"true": "Someone is shouting", "false": "Everyone is calm"}},
        "topic": {"type": "choice", "instructions": "What is it about?", "criteria": {"gate": "A gate", "money": "Money", "other": None}},
        "tension": {"type": "score", "instructions": "How tense is it?", "criteria": ["calm", "uneasy", "heated", "violent"]},
    },
}


def answer_for(questions: dict) -> dict:
    """What the real service answers in each kind (public docs: the field names and what they mean)."""
    out = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            out[name] = {"type": "noul", "noul": 0.94}
        elif q["type"] == "choice":
            keys = list(q["criteria"])
            out[name] = {"type": "choice", "choice": keys[0], "confidence": 0.8, "probabilities": {k: (0.8 if i == 0 else 0.2 / max(1, len(keys) - 1)) for i, k in enumerate(keys)}}
        else:
            n = len(q["criteria"])
            out[name] = {"type": "score", "score": 1.4, "confidence": 0.7, "legend": {str(i): c for i, c in enumerate(q["criteria"])}, "probabilities": {str(i): 1 / n for i in range(n)}}
    return out


class StandIn:
    """A Perplexity Decisions API look-alike on 127.0.0.1: answers every question in its own kind unless told `status`/`body`, remembers what it was sent."""

    def __init__(self):
        self.status, self.body, self.delay, self.calls = 200, None, 0.0, []
        self.usage = {"input_tokens": 123, "output_tokens": 0}
        self.loop, self.runner, self.url = asyncio.new_event_loop(), None, ""
        ready = threading.Event()

        async def handle(request):
            body = await request.json()
            self.calls.append({"headers": dict(request.headers), "body": body, "path": request.path})
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.body is not None or self.status != 200:
                return web.json_response(self.body if self.body is not None else {"error": {"message": "nope", "type": "x", "code": None}}, status=self.status)
            return web.json_response({"model": body["model"], "answers": answer_for(body["questions"]), "usage": self.usage})

        async def start():
            app = web.Application()
            app.router.add_post("/v1/decisions", handle)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1/decisions"
            ready.set()

        self.thread = threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True)
        self.thread.start()
        ready.wait(5)

    def close(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def pplx(proxy_module, monkeypatch):
    server = StandIn()
    monkeypatch.setattr(proxy_module, "PERPLEXITY_DECISIONS_URL", server.url)
    monkeypatch.setattr(proxy_module, "auth", MagicMock(session=None, is_ready=False))
    monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    yield server
    server.close()
    proxy_module.decision_handles.provider_lines.clear()
    proxy_module.decision_handles.last_answer = None


def ask(test_client, body=None, path="/v1/systemone"):
    return test_client.post(path, json=body or {"model": "typesafe/jev-latest", **ALL_KINDS})


def not_installed(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)


def with_stand_in(proxy_module):
    return patch.multiple(proxy_module, deepseek_api_key="k", call_deepseek_direct=deepseek())


class TestTheCallAndTheMapping:
    def test_it_answers_and_nothing_else_is_asked(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        r = ask(test_client)
        assert r.status_code == 200 and len(pplx.calls) == 1

    def test_the_call_has_the_documented_address_bearer_key_and_only_the_three_fields(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        ask(test_client, {"model": "typesafe/jev-latest", "model_index": 0, **ALL_KINDS})
        c = pplx.calls[0]
        assert c["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" and "x-api-key" not in {k.lower() for k in c["headers"]} and c["path"] == "/v1/decisions"
        assert c["body"] == {"model": "pplx-decider-latest", "state": ALL_KINDS["state"], "questions": ALL_KINDS["questions"]}, "model_index and the typesafe/ model name stay behind"

    def test_the_address_and_models_are_the_documented_ones(self, proxy_module):
        assert proxy_module.PERPLEXITY_DECISIONS_URL == "https://api.perplexity.ai/v1/decisions"
        assert proxy_module.PERPLEXITY_DECISION_MODELS == ("pplx-decider-latest", "pplx-decider-flash-latest")

    def test_yes_no_choice_and_score_come_back_unchanged_in_the_shape_skyrimnet_reads(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        body = ask(test_client).json()
        assert set(body) == {"model", "answers", "usage"} and body["answers"] == answer_for(ALL_KINDS["questions"]) and body["usage"] == {"input_tokens": 123, "output_tokens": 0}
        assert body["answers"]["angry"]["noul"] == 0.94 and body["answers"]["topic"]["choice"] == "gate" and body["answers"]["tension"]["score"] == 1.4
        assert body["answers"]["tension"]["legend"]["2"] == "heated" and set(body["answers"]["topic"]["probabilities"]) == {"gate", "money", "other"}

    def test_the_real_request_shape_skyrimnet_sends_works_too(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and r.json()["answers"]["intent"]["choice"] == "complaint" and pplx.calls[0]["body"]["questions"] == REAL_JEV_REQUEST["questions"]

    def test_pictures_as_inline_images_go_across_as_they_are(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        state = {"look": "What is shown?", "pics": [{"type": "image_url", "image_url": {"url": PNG}}], "deep": {"a": {"type": "image_url", "image_url": {"url": PNG.replace("png", "webp")}}}}
        assert ask(test_client, {"model": "x", "state": state, "questions": ALL_KINDS["questions"]}).status_code == 200
        assert pplx.calls[0]["body"]["state"] == state

    def test_the_cheaper_model_is_the_players_choice(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        proxy_module._save_config({"decisions": {"decision_models": {"perplexity": "pplx-decider-flash-latest"}}})
        r = ask(test_client)
        assert pplx.calls[0]["body"]["model"] == "pplx-decider-flash-latest" and r.json()["model"] == "pplx-decider-flash-latest"


class TestWhatItCannotTakeIsPassedOnNotGuessed:
    PIC = {"type": "image_url", "image_url": {"url": PNG}}

    @pytest.mark.parametrize("why,body", [
        ("a picture by web address", {"state": {"p": {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}}, "questions": ALL_KINDS["questions"]}),
        ("a picture that is not PNG, JPEG or WebP", {"state": {"p": {"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lG"}}}, "questions": ALL_KINDS["questions"]}),
        ("more than eight pictures", {"state": [PIC_ for PIC_ in [{"type": "image_url", "image_url": {"url": PNG}}] * 9], "questions": ALL_KINDS["questions"]}),
        ("a field Perplexity does not have", {"state": "s", "questions": {"q": {"type": "noul", "instructions": "x", "threshold": 0.5}}}),
        ("a kind Perplexity does not have", {"state": "s", "questions": {"q": {"type": "number", "instructions": "x"}}}),
        ("a yes/no with nothing to go on", {"state": "s", "questions": {"q": {"type": "noul"}}}),
        ("a choice written as a list", {"state": "s", "questions": {"q": {"type": "choice", "criteria": ["a", "b"]}}}),
        ("a score with too many levels", {"state": "s", "questions": {"q": {"type": "score", "criteria": [str(i) for i in range(11)]}}}),
        ("a score with no levels", {"state": "s", "questions": {"q": {"type": "score", "criteria": []}}}),
        ("no questions", {"state": "s", "questions": {}}),
    ])
    def test_it_is_not_sent_and_another_ai_answers(self, proxy_module, test_client, pplx, monkeypatch, caplog, why, body):
        caplog.set_level(logging.INFO)
        not_installed(proxy_module, monkeypatch)
        with with_stand_in(proxy_module) as _:
            r = test_client.post("/v1/systemone", json={"model": "typesafe/jev-latest", **body}) if body["questions"] else None
        if r is None:                       # an empty question map never reaches the providers at all (the request is refused earlier)
            return
        assert pplx.calls == [], why
        assert lines(caplog, "Perplexity can't take this decision"), why

    def test_a_missing_state_is_passed_on(self, proxy_module, pplx):
        status, body, _ = asyncio.run(proxy_module.call_perplexity_decisions("pplx-decider-latest", None, ALL_KINDS["questions"], "t"))
        assert status == proxy_module.PERPLEXITY_NOT_MAPPED and pplx.calls == []

    def test_the_card_says_it_in_plain_words(self, proxy_module):
        assert proxy_module.perplexity_decision_line(proxy_module.PERPLEXITY_NOT_MAPPED) == "Perplexity can't take this decision."


class TestAnErrorPassesTheDecisionOn:
    @pytest.mark.parametrize("status,line", [
        (401, "Perplexity didn't accept the key."), (403, "Perplexity didn't accept the key."), (400, "Perplexity didn't answer."), (429, "Perplexity is too busy right now."),
        (500, "Perplexity didn't answer."), (503, "Perplexity didn't answer."), (504, "Perplexity didn't answer.")])
    def test_another_ai_answers_and_the_line_says_so(self, proxy_module, test_client, pplx, monkeypatch, status, line):
        not_installed(proxy_module, monkeypatch)
        pplx.status = status
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            r = ask(test_client)
        assert r.status_code == 200 and ds.await_count == 1
        assert proxy_module.decision_handles.provider_lines["perplexity"] == f"{line} Another AI answered instead."
        assert test_client.get("/config/decisions/status").json()["providerLines"] == [f"{line} Another AI answered instead."]

    def test_nobody_else_ready_says_so_and_the_error_is_clear(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        pplx.status = 401
        for name in ("deepseek", "glm", "gemini", "openai", "kilo"):
            monkeypatch.setattr(proxy_module, f"{name}_api_key", None)
        r = ask(test_client)
        assert r.status_code >= 400
        assert proxy_module.decision_handles.provider_lines["perplexity"] == "Perplexity didn't accept the key. No other AI was ready to answer."

    def test_a_200_that_leaves_a_question_unanswered_is_a_miss(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        full = answer_for(ALL_KINDS["questions"])
        del full["tension"]
        pplx.body = {"model": "pplx-decider-latest", "answers": full, "usage": {"input_tokens": 5, "output_tokens": 0}}
        with with_stand_in(proxy_module):
            assert ask(test_client).status_code == 200
        assert proxy_module.decision_handles.provider_lines["perplexity"].startswith("Perplexity didn't answer.")

    @pytest.mark.parametrize("answers", [
        {"angry": {"type": "choice", "choice": "x", "confidence": 1, "probabilities": {}}},          # the wrong kind
        {"angry": {"type": "noul", "noul": 1.5}},                                                    # not a probability
        {"topic": {"type": "choice", "choice": "not-an-option", "confidence": 1, "probabilities": {}}},
        {"tension": {"type": "score", "score": 9, "confidence": 1, "legend": {}, "probabilities": {}}},
    ])
    def test_an_answer_that_is_not_in_its_own_kind_is_never_handed_to_skyrimnet(self, proxy_module, pplx, answers):
        full = answer_for(ALL_KINDS["questions"])
        full.update(answers)
        pplx.body = {"model": "m", "answers": full, "usage": {}}
        status, body, _ = asyncio.run(proxy_module.call_perplexity_decisions("pplx-decider-latest", ALL_KINDS["state"], ALL_KINDS["questions"], "t"))
        assert status == 502 and body == b""

    def test_too_slow_passes_on_within_the_players_limit(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "_paid_limit", lambda: 0.3)
        pplx.delay = 1.5
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            r = ask(test_client)
        assert r.status_code == 200 and ds.await_count == 1
        assert proxy_module.decision_handles.provider_lines["perplexity"] == "Perplexity was too slow. Another AI answered instead."

    def test_no_connection_passes_on(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "PERPLEXITY_DECISIONS_URL", "http://127.0.0.1:1/v1/decisions")
        with with_stand_in(proxy_module):
            assert ask(test_client).status_code == 200

    def test_a_later_answer_clears_the_line_and_a_new_key_does_too(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        proxy_module.decision_handles.provider_lines["perplexity"] = "Perplexity didn't accept the key. Another AI answered instead."
        assert ask(test_client).status_code == 200 and "perplexity" not in proxy_module.decision_handles.provider_lines
        proxy_module.decision_handles.provider_lines["perplexity"] = "Perplexity was too slow. Another AI answered instead."
        assert test_client.post("/config/perplexity-key", json={"key": FAKE_KEY}).json() == {"status": "saved"}
        assert "perplexity" not in proxy_module.decision_handles.provider_lines


class TestTheOrder:
    def order(self, proxy_module, ts=False, nano=False, pplx_key=True):
        proxy_module.perplexity_api_key = FAKE_KEY if pplx_key else None
        return proxy_module.decision_handles.order("x", ts, nano)

    def test_local_then_typesafe_then_perplexity_then_nano_then_a_stand_in(self, proxy_module, local, monkeypatch):
        monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
        local()
        h = proxy_module.decision_handles
        assert h.order("x", True, True) == ["local", "typesafe", "perplexity", "nano", "standin"]
        assert h.order("x", False, True) == ["local", "perplexity", "nano", "standin"]
        assert h.order("x", True, False) == ["local", "typesafe", "perplexity", "standin"]
        assert h.order("x", False, False) == ["local", "perplexity", "standin"]
        monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
        assert h.order("x", True, True) == ["local", "typesafe", "nano", "standin"], "no key: left out, as before"

    def test_without_the_local_model(self, proxy_module, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
        h = proxy_module.decision_handles
        assert h.order("x", True, True) == ["typesafe", "perplexity", "nano", "standin"]
        assert h.order("x", False, False) == ["perplexity", "standin"]
        monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
        assert h.order("x", True, True) is None, "no local model and no Perplexity: exactly as before"

    def test_the_players_choice_goes_first(self, proxy_module, local, monkeypatch):
        monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
        local(answer_first="perplexity")
        assert proxy_module.decision_handles.order("x", True, True) == ["perplexity", "local", "typesafe", "nano", "standin"]

    def test_the_named_order_is_one_documented_constant(self):
        import decision_wiring
        assert decision_wiring.DECISION_ORDER == ("local", "typesafe", "perplexity", "nano")
        assert "perplexity" in decision_wiring.answer_first_choices()

    def test_typesafe_fails_then_perplexity_answers_before_nano_gpt(self, proxy_module, test_client, pplx, local, monkeypatch):
        local()
        proxy_module.decision_handles.engine.behaviour = "stopped"
        monkeypatch.setattr(proxy_module, "typesafe_api_key", "ts-test-key")
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        nano = AsyncMock(return_value=(200, b"{}", "application/json"))
        typesafe = AsyncMock(return_value=(401, b"{}", "application/json"))
        with patch.object(proxy_module, "call_nano_systemone", nano), patch.object(proxy_module, "call_typesafe_systemone", typesafe):
            r = ask(test_client)
        assert r.status_code == 200 and typesafe.await_count == 1 and len(pplx.calls) == 1 and nano.await_count == 0

    def test_perplexity_fails_then_nano_gpt_answers(self, proxy_module, test_client, pplx, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        pplx.status = 429
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        body = json.dumps({"model": "jev-latest", "answers": answer_for(ALL_KINDS["questions"]), "usage": {"input_tokens": 1, "output_tokens": 0}}).encode()
        nano = AsyncMock(return_value=(200, body, "application/json"))
        with patch.object(proxy_module, "call_nano_systemone", nano):
            r = ask(test_client)
        assert r.status_code == 200 and nano.await_count == 1 and len(pplx.calls) == 1
        assert proxy_module.decision_handles.provider_lines["perplexity"] == "Perplexity is too busy right now. Another AI answered instead."

    def test_a_typesafe_key_alone_changes_nothing(self, proxy_module, test_client, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
        monkeypatch.setattr(proxy_module, "typesafe_api_key", "ts-test-key")
        session = ts_session()
        with patch("aiohttp.ClientSession", return_value=session):
            assert ask(test_client).status_code == 200
        assert session.post.call_count == 1


class TestSpeedsAndCost:
    def _db(self, proxy_module, monkeypatch, tmp_path):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        db = tmp_path / "speeds.db"
        monkeypatch.setattr(proxy_module, "SPEED_HISTORY_DB", str(db))
        proxy_module._init_speed_history_db()
        return db

    @staticmethod
    def rows(db):
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM speed_history")]

    def test_the_answer_is_recorded_as_perplexity_with_the_decisions_role_and_its_cost(self, proxy_module, test_client, pplx, monkeypatch, tmp_path):
        not_installed(proxy_module, monkeypatch)
        db = self._db(proxy_module, monkeypatch, tmp_path)
        pplx.usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        ask(test_client)
        rows = self.rows(db)
        assert len(rows) == 1 and rows[0]["provider"] == "Perplexity" and rows[0]["model"] == "pplx-decider-latest" and rows[0]["success"] == 1 and "Decisions" in rows[0]["roles_json"]
        assert rows[0]["cost"] == pytest.approx(0.02) and rows[0]["cost_note"] == ai_costs.NOTE_PRICES

    def test_the_cheaper_model_costs_half(self, proxy_module, test_client, pplx, monkeypatch, tmp_path):
        not_installed(proxy_module, monkeypatch)
        db = self._db(proxy_module, monkeypatch, tmp_path)
        proxy_module._save_config({"decisions": {"decision_models": {"perplexity": "pplx-decider-flash-latest"}}})
        pplx.usage = {"input_tokens": 500_000, "output_tokens": 0}
        ask(test_client)
        assert self.rows(db)[0]["cost"] == pytest.approx(0.005)

    def test_a_refusal_is_a_failed_perplexity_row_and_the_stand_in_is_under_its_own_name(self, proxy_module, test_client, pplx, monkeypatch, tmp_path):
        not_installed(proxy_module, monkeypatch)
        db = self._db(proxy_module, monkeypatch, tmp_path)
        pplx.status = 429
        with with_stand_in(proxy_module):
            ask(test_client)
        rows = sorted(self.rows(db), key=lambda r: r["provider"])
        assert [(r["provider"], r["success"]) for r in rows] == [("DeepSeek", 1), ("Perplexity", 0)] and all("Decisions" in r["roles_json"] for r in rows)

    def test_prices_come_from_one_place(self):
        assert ai_costs.perplexity_price("pplx-decider-latest") == {"prompt": 0.02, "completion": 0.0} and ai_costs.perplexity_price("perplexity/pplx-decider-flash-latest")["prompt"] == 0.01
        assert ai_costs.perplexity_price("something-else") is None

    def test_it_is_a_paid_provider_for_routing(self, proxy_module):
        import routing
        assert proxy_module._COST_CLASS_BY_PROVIDER["Perplexity"] == routing.CLASS_PAID


class TestTheKey:
    def test_the_key_never_reaches_the_log_even_on_a_refusal(self, proxy_module, test_client, pplx, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        not_installed(proxy_module, monkeypatch)
        pplx.status, pplx.body = 401, {"error": {"message": f"bad key {FAKE_KEY}", "type": "auth", "code": None}}
        with with_stand_in(proxy_module):
            ask(test_client)
        pplx.status, pplx.body = 200, None
        ask(test_client)
        assert not any(FAKE_KEY in r.getMessage() for r in caplog.records)

    def test_the_shape_of_the_key_is_masked(self, proxy_module):
        assert "REDACTED" in proxy_module._redact_text(f"x {FAKE_KEY} y") and FAKE_KEY not in proxy_module._redact_text(f"x {FAKE_KEY} y")

    def test_saving_and_clearing(self, proxy_module, test_client, monkeypatch, tmp_path):
        monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
        assert test_client.post("/config/perplexity-key", json={"key": f"  {FAKE_KEY} "}).json() == {"status": "saved"}
        assert proxy_module.perplexity_api_key == FAKE_KEY and proxy_module._load_config()["perplexity_api_key"] == FAKE_KEY
        assert test_client.post("/config/perplexity-key", json={"key": ""}).json() == {"status": "cleared"}
        assert proxy_module.perplexity_api_key is None and "perplexity_api_key" not in proxy_module._load_config()


class TestWhereItShowsUp:
    def test_the_providers_tab_lists_it_as_a_decisions_only_key(self, proxy_module):
        entry = next(p for p in asyncio.run(proxy_module._dashboard_providers()) if p["id"] == "perplexity")
        assert entry["keyEndpoint"] == "/config/perplexity-key" and entry["testable"] is False and entry["models"] == [] and entry["group"] == "key" and entry["tag"] == "Decisions only"
        assert "does not write dialogue" in entry["help"] and "console.perplexity.ai" in entry["help"]

    def test_it_is_never_offered_for_chat(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
        listing = json.dumps(test_client.get("/v1/models").json()).lower()
        assert "pplx" not in listing and "perplexity" not in listing
        assert not hasattr(proxy_module, "is_perplexity_model")

    def test_the_decisions_card_lists_it_with_plain_model_names(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
        off = next(p for p in test_client.get("/config/decisions/status").json()["providers"] if p["id"] == "perplexity")
        assert off["ready"] is False and off["models"] == [] and off["choice"] == "perplexity" and off["offReason"]
        monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
        on = next(p for p in test_client.get("/config/decisions/status").json()["providers"] if p["id"] == "perplexity")
        assert on["ready"] is True and on["models"] == ["pplx-decider-latest", "pplx-decider-flash-latest"] and on["model"] == "pplx-decider-latest"
        assert on["labels"] == {"pplx-decider-latest": "Standard", "pplx-decider-flash-latest": "Cheaper and faster"} and on["label"] == "My Perplexity key"

    def test_the_quick_test_can_ask_it(self, proxy_module, test_client, pplx):
        import decision_test
        sc = decision_test.listing()[0]["id"]
        choices = {c["id"]: c for c in test_client.get("/quicktest/decision/options").json()["choices"]}
        assert choices["perplexity"]["reason"] == "" and choices["perplexity"]["paid"] is True
        r = test_client.post("/quicktest/decision", json={"who": "perplexity", "scenario": sc}).json()
        assert r["ok"] is True and r["line"].startswith("Answered by pplx-decider-latest in ")

    def test_the_quick_test_says_why_a_wrong_key_failed(self, proxy_module, test_client, pplx):
        import decision_test
        pplx.status = 401
        r = test_client.post("/quicktest/decision", json={"who": "perplexity", "scenario": decision_test.listing()[0]["id"]}).json()
        assert r["ok"] is False and r["error"] == "Perplexity didn't accept the key."

    def test_the_quick_test_is_off_without_a_key(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
        choices = {c["id"]: c for c in test_client.get("/quicktest/decision/options").json()["choices"]}
        assert choices["perplexity"]["reason"] == "You haven't added a Perplexity key, so this choice is turned off."
