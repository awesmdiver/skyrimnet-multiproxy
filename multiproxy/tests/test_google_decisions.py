"""Quick decisions answered by Google's Decisions model (`gemini-decision-1`), with the Google (Gemini) key the player already has (queue: add-google-s-decisions-model).
A stand-in server on 127.0.0.1 speaks the Decisions API as Google's public page describes it (nothing real is called and no real key is used or printed): the request that is
sent, yes/no, choice and score questions mapped both ways, the confidence reaching the "less than N% sure" rule, every decision Google cannot take, every error passing the
decision on, and that the key never reaches a log."""
import asyncio
import json
import logging
import threading
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from tests.test_decision_order import deepseek, lines, local                  # noqa: F401  (local is a fixture)

FAKE_KEY = "AIzaSyFAKE-0000000000000000000000000"
ALL_KINDS = {
    "state": {"scene": "Two guards argue at a gate", "speaker": "Aela"},
    "questions": {
        "angry": {"type": "noul", "instructions": "Is anyone angry?", "criteria": {"true": "Someone is shouting", "false": "Everyone is calm"}},
        "topic": {"type": "choice", "instructions": "What is it about?", "criteria": {"gate": "A gate", "money": "Money", "other": None}},
        "tension": {"type": "score", "instructions": "How tense is it?", "criteria": ["calm", "uneasy", "heated", "violent"]},
    },
}


def answer_for(questions: dict, conf: float = 0.8) -> dict:
    """What the service answers in each kind (Google's public page: the field names and what they mean)."""
    out = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            out[name] = {"type": "noul", "noul": 0.9, "confidence": 0.9}
        elif q["type"] == "choice":
            keys = list(q["criteria"])
            out[name] = {"type": "choice", "choice": keys[0], "confidence": conf, "probabilities": {k: (conf if i == 0 else (1 - conf) / (len(keys) - 1)) for i, k in enumerate(keys)}}
        else:
            n = len(q["criteria"])
            out[name] = {"type": "score", "score": 1.4, "confidence": 0.7, "legend": {str(i): c for i, c in enumerate(q["criteria"])}, "probabilities": {str(i): 1 / n for i in range(n)}}
    return out


class StandIn:
    """A Decisions API look-alike on 127.0.0.1: answers each Google-shaped question in its own kind unless told `status`/`body`; remembers what it was sent."""

    def __init__(self):
        self.status, self.body, self.delay, self.conf, self.calls = 200, None, 0.0, 0.8, []
        self.usage = {"total_input_tokens": 123, "total_output_tokens": 0, "total_tokens": 123}
        self.loop, self.runner, self.url = asyncio.new_event_loop(), None, ""
        ready = threading.Event()

        async def handle(request):
            body = await request.json()
            self.calls.append({"headers": dict(request.headers), "body": body, "path": request.path, "query": request.query_string})
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.body is not None or self.status != 200:
                return web.json_response(self.body if self.body is not None else {"error": {"code": "safety", "message": "blocked"}}, status=self.status)
            return web.json_response({"answers": answer_for(body["questions"], self.conf), "usage": self.usage})

        async def start():
            app = web.Application()
            app.router.add_post("/v1beta/decisions", handle)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1beta/decisions"
            ready.set()

        self.thread = threading.Thread(target=lambda: (asyncio.set_event_loop(self.loop), self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True)
        self.thread.start()
        ready.wait(5)

    def close(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def goog(proxy_module, monkeypatch):
    server = StandIn()
    monkeypatch.setattr(proxy_module, "GOOGLE_DECISIONS_URL", server.url)
    monkeypatch.setattr(proxy_module, "auth", type("A", (), {"session": None, "is_ready": False})())
    monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    monkeypatch.setattr(proxy_module, "perplexity_api_key", None)
    yield server
    server.close()
    proxy_module.decision_handles.provider_lines.clear()
    proxy_module.decision_handles.last_answer = None


def ask(test_client, body=None):
    return test_client.post("/v1/systemone", json=body or {"model": "typesafe/jev-latest", **ALL_KINDS})


def not_installed(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)


def with_stand_in(proxy_module):
    return patch.multiple(proxy_module, deepseek_api_key="k", call_deepseek_direct=deepseek())


def call(proxy_module, state, questions):
    return asyncio.run(proxy_module.call_google_decisions("gemini-decision-1", state, questions, "t"))


class TestTheCallAndTheMapping:
    def test_it_answers_and_nothing_else_is_asked(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        assert ask(test_client).status_code == 200 and len(goog.calls) == 1

    def test_the_address_the_header_and_the_body(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        ask(test_client, {"model": "typesafe/jev-latest", "model_index": 0, **ALL_KINDS})
        c = goog.calls[0]
        low = {k.lower(): v for k, v in c["headers"].items()}
        assert low["x-goog-api-key"] == FAKE_KEY and "authorization" not in low and FAKE_KEY not in c["query"] and c["path"] == "/v1beta/decisions"
        assert set(c["body"]) == {"model", "input", "questions"} and c["body"]["model"] == "gemini-decision-1", "model_index and the typesafe/ name stay behind"

    def test_the_real_address_is_google_over_https(self, proxy_module):
        assert proxy_module.GOOGLE_DECISIONS_URL == "https://generativelanguage.googleapis.com/v1beta/decisions"
        assert proxy_module.GOOGLE_DECISION_MODELS == ("gemini-decision-1",)

    def test_state_becomes_the_input_text(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        ask(test_client)
        assert json.loads(goog.calls[0]["body"]["input"]) == ALL_KINDS["state"]
        ask(test_client, {"model": "x", "state": "Just some words with ünïcode", "questions": ALL_KINDS["questions"]})
        assert goog.calls[1]["body"]["input"] == "Just some words with ünïcode"

    def test_each_question_kind_is_written_the_google_way(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        ask(test_client)
        q = goog.calls[0]["body"]["questions"]
        assert q["angry"] == {"type": "noul", "instructions": "Is anyone angry?", "criteria": {"true_description": "Someone is shouting", "false_description": "Everyone is calm"}}
        assert q["topic"]["criteria"] == {"gate": "A gate", "money": "Money", "other": ""}, "an option with no text becomes an empty one"
        assert q["tension"] == {"type": "score", "instructions": "How tense is it?", "criteria": ["calm", "uneasy", "heated", "violent"]}

    def test_a_yes_no_without_criteria_is_fine(self, proxy_module, goog):
        status, body, _ = call(proxy_module, "s", {"q": {"type": "noul", "instructions": "Is it raining?"}})
        assert status == 200 and "criteria" not in goog.calls[0]["body"]["questions"]["q"]

    def test_answers_come_back_in_the_shape_skyrimnet_reads(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        body = ask(test_client).json()
        assert set(body) == {"model", "answers", "usage"} and body["model"] == "gemini-decision-1"
        a = body["answers"]
        assert a["angry"] == {"type": "noul", "noul": 0.9, "confidence": 0.9}
        assert a["topic"]["choice"] == "gate" and a["topic"]["confidence"] == 0.8 and set(a["topic"]["probabilities"]) == {"gate", "money", "other"}
        assert a["tension"]["score"] == 1.4 and a["tension"]["legend"]["2"] == "heated" and a["tension"]["confidence"] == 0.7
        assert body["usage"] == {"input_tokens": 123, "output_tokens": 0}

    def test_the_real_request_shape_skyrimnet_sends_works_too(self, proxy_module, test_client, goog, monkeypatch):
        from tests.test_jev_provider import REAL_JEV_REQUEST
        not_installed(proxy_module, monkeypatch)
        r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200 and r.json()["answers"]["intent"]["choice"] == "complaint"


class TestTheConfidenceReachesTheRule:
    def test_a_sure_answer_stands(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        nano = AsyncMock(return_value=(200, b"{}", "application/json"))
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        proxy_module._save_config({"decisions": {"decision_order": ["google", "nano"]}})
        with patch.object(proxy_module, "call_nano_systemone", nano):
            r = ask(test_client)
        assert r.status_code == 200 and nano.await_count == 0 and r.json()["answers"]["topic"]["choice"] == "gate"

    def test_an_unsure_answer_hands_over_to_the_next_one(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        goog.conf = 0.4
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        proxy_module._save_config({"decisions": {"decision_order": ["google", "nano"]}})
        body = json.dumps({"model": "jev-latest", "answers": answer_for(ALL_KINDS["questions"], 0.95), "usage": {"input_tokens": 1, "output_tokens": 0}}).encode()
        nano = AsyncMock(return_value=(200, body, "application/json"))
        with patch.object(proxy_module, "call_nano_systemone", nano):
            r = ask(test_client)
        assert r.status_code == 200 and nano.await_count == 1 and r.json()["answers"]["topic"]["confidence"] == 0.95
        assert proxy_module.decision_handles.last_answer["by"] == "Nano-GPT" and proxy_module.decision_handles.last_answer["unsure"] == pytest.approx(0.4)

    def test_a_yes_no_near_fifty_fifty_is_unsure_too(self, proxy_module, test_client, goog, monkeypatch):
        import decision_test
        answers = {"angry": {"type": "noul", "noul": 0.52, "confidence": 0.52}}
        assert decision_test.lowest_confidence({"angry": ALL_KINDS["questions"]["angry"]}, answers) == pytest.approx(0.52)

    def test_the_rule_can_be_turned_off(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        goog.conf = 0.4
        monkeypatch.setattr(proxy_module, "nano_api_key", "sk-nano-FAKE")
        proxy_module._save_config({"decisions": {"decision_order": ["google", "nano"], "ask_when_unsure": False}})
        nano = AsyncMock(return_value=(200, b"{}", "application/json"))
        with patch.object(proxy_module, "call_nano_systemone", nano):
            assert ask(test_client).status_code == 200
        assert nano.await_count == 0


class TestWhatItCannotTakeIsPassedOnNotGuessed:
    PIC = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}

    @pytest.mark.parametrize("why,body", [
        ("a picture", {"state": {"p": PIC}, "questions": ALL_KINDS["questions"]}),
        ("no state", {"state": {}, "questions": ALL_KINDS["questions"]}),
        ("a field Google does not have", {"state": "s", "questions": {"q": {"type": "noul", "instructions": "x", "threshold": 0.5}}}),
        ("a kind Google does not have", {"state": "s", "questions": {"q": {"type": "number", "instructions": "x"}}}),
        ("a question with no instructions", {"state": "s", "questions": {"q": {"type": "noul"}}}),
        ("a name Google does not take", {"state": "s", "questions": {"has space": {"type": "noul", "instructions": "x"}}}),
        ("a name that is too long", {"state": "s", "questions": {"q" * 65: {"type": "noul", "instructions": "x"}}}),
        ("a choice with one option", {"state": "s", "questions": {"q": {"type": "choice", "instructions": "x", "criteria": {"a": "only"}}}}),
        ("a choice written as a list", {"state": "s", "questions": {"q": {"type": "choice", "instructions": "x", "criteria": ["a", "b"]}}}),
        ("an option name with a space at the end", {"state": "s", "questions": {"q": {"type": "choice", "instructions": "x", "criteria": {"a ": "", "b": ""}}}}),
        ("a score with one level", {"state": "s", "questions": {"q": {"type": "score", "instructions": "x", "criteria": ["only"]}}}),
        ("a score with an empty level", {"state": "s", "questions": {"q": {"type": "score", "instructions": "x", "criteria": ["a", ""]}}}),
        ("a yes/no with odd criteria", {"state": "s", "questions": {"q": {"type": "noul", "instructions": "x", "criteria": {"maybe": "x"}}}}),
    ])
    def test_it_is_not_sent_and_another_ai_answers(self, proxy_module, test_client, goog, monkeypatch, caplog, why, body):
        caplog.set_level(logging.INFO)
        not_installed(proxy_module, monkeypatch)
        with with_stand_in(proxy_module):
            r = test_client.post("/v1/systemone", json={"model": "typesafe/jev-latest", **body})
        assert goog.calls == [], why
        assert lines(caplog, "Google can't take this decision"), why
        assert r.status_code == 200

    def test_a_missing_state_is_passed_on(self, proxy_module, goog):
        status, body, _ = call(proxy_module, None, ALL_KINDS["questions"])
        assert status == proxy_module.GOOGLE_NOT_MAPPED and goog.calls == []

    def test_the_card_says_it_in_plain_words(self, proxy_module):
        assert proxy_module.google_decision_line(proxy_module.GOOGLE_NOT_MAPPED) == "Google couldn't make this decision."


class TestAnErrorPassesTheDecisionOn:
    @pytest.mark.parametrize("status,line", [
        (400, "Google didn't answer."), (401, "Google didn't accept your key."), (403, "Google didn't accept your key."), (429, "Google is too busy."),
        (500, "Google didn't answer."), (503, "Google didn't answer.")])
    def test_another_ai_answers_and_the_line_says_so(self, proxy_module, test_client, goog, monkeypatch, status, line):
        not_installed(proxy_module, monkeypatch)
        goog.status = status
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            r = ask(test_client)
        assert r.status_code == 200 and ds.await_count == 1
        assert proxy_module.decision_handles.provider_lines["google"] == f"{line} Another AI answered instead."

    def test_a_safety_block_is_a_400_and_passes_on_quietly(self, proxy_module, goog):
        goog.status, goog.body = 400, {"error": {"code": "prohibited_content", "message": "no"}}
        status, body, _ = call(proxy_module, "s", ALL_KINDS["questions"])
        assert status == 400 and body == b""

    def test_a_200_that_leaves_a_question_unanswered_is_a_miss(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        full = answer_for(ALL_KINDS["questions"])
        del full["tension"]
        goog.body = {"answers": full, "usage": {}}
        with with_stand_in(proxy_module):
            assert ask(test_client).status_code == 200
        assert proxy_module.decision_handles.provider_lines["google"].startswith("Google didn't answer.")

    @pytest.mark.parametrize("answers", [
        {"angry": {"type": "choice", "choice": "x", "confidence": 1, "probabilities": {}}},          # the wrong kind
        {"angry": {"type": "noul", "noul": 1.5, "confidence": 1}},                                   # not a probability
        {"angry": {"type": "noul", "noul": 0.5}},                                                    # no confidence
        {"topic": {"type": "choice", "choice": "not-an-option", "confidence": 1, "probabilities": {}}},
        {"topic": {"type": "choice", "choice": "gate", "confidence": 1, "probabilities": {"gate": "most"}}},
        {"tension": {"type": "score", "score": 9, "confidence": 1, "legend": {}, "probabilities": {}}},
        {"tension": "yes"},
    ])
    def test_a_hostile_or_broken_answer_is_never_handed_to_skyrimnet(self, proxy_module, goog, answers):
        full = answer_for(ALL_KINDS["questions"])
        full.update(answers)
        goog.body = {"answers": full, "usage": {}}
        status, body, _ = call(proxy_module, "s", ALL_KINDS["questions"])
        assert status == 502 and body == b""

    @pytest.mark.parametrize("body", [[], "text", {"answers": []}, {"nothing": 1}])
    def test_an_answer_of_the_wrong_shape_is_a_miss(self, proxy_module, goog, body):
        goog.body = body
        assert call(proxy_module, "s", ALL_KINDS["questions"])[0] == 502

    def test_an_answer_that_is_not_json_is_a_miss(self, proxy_module, goog):
        goog.body = None
        with patch.object(proxy_module, "_google_read_answers", side_effect=ValueError("x")):
            assert call(proxy_module, "s", ALL_KINDS["questions"])[0] == 502

    def test_an_oversized_answer_is_not_read(self, proxy_module, goog, monkeypatch):
        monkeypatch.setattr(proxy_module, "GOOGLE_ANSWER_LIMIT_BYTES", 200)
        assert call(proxy_module, "s", ALL_KINDS["questions"])[0] == 502

    def test_extra_fields_in_an_answer_are_dropped(self, proxy_module, goog):
        full = answer_for(ALL_KINDS["questions"])
        full["angry"]["evil"] = "<script>"
        goog.body = {"answers": full, "usage": {}, "extra": 1}
        status, body, _ = call(proxy_module, "s", ALL_KINDS["questions"])
        assert status == 200 and "evil" not in body.decode() and "extra" not in body.decode()

    def test_too_slow_passes_on_within_the_players_limit(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "_paid_limit", lambda: 0.3)
        goog.delay = 1.5
        ds = deepseek()
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", ds):
            r = ask(test_client)
        assert r.status_code == 200 and ds.await_count == 1
        assert proxy_module.decision_handles.provider_lines["google"] == "Google was too slow. Another AI answered instead."

    def test_no_connection_passes_on(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "GOOGLE_DECISIONS_URL", "http://127.0.0.1:1/v1beta/decisions")
        with with_stand_in(proxy_module):
            assert ask(test_client).status_code == 200

    def test_a_later_answer_clears_the_line_and_a_new_key_does_too(self, proxy_module, test_client, goog, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        proxy_module.decision_handles.provider_lines["google"] = "Google didn't accept your key. Another AI answered instead."
        assert ask(test_client).status_code == 200 and "google" not in proxy_module.decision_handles.provider_lines
        proxy_module.decision_handles.provider_lines["google"] = "Google was too slow. Another AI answered instead."
        assert test_client.post("/config/gemini-key", json={"key": FAKE_KEY}).json() == {"status": "saved"}
        assert "google" not in proxy_module.decision_handles.provider_lines


class TestTheOrder:
    def test_google_sits_after_perplexity_and_before_nano_gpt(self, proxy_module, local, monkeypatch):
        monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
        monkeypatch.setattr(proxy_module, "perplexity_api_key", FAKE_KEY)
        local()
        assert proxy_module.decision_handles.order("x", True, True) == ["local", "typesafe", "perplexity", "google", "nano", "standin"]

    def test_without_a_key_it_is_left_out(self, proxy_module, local, monkeypatch):
        monkeypatch.setattr(proxy_module, "gemini_api_key", None)
        local()
        assert "google" not in proxy_module.decision_handles.order("x", True, True)

    def test_it_alone_answers_when_nothing_is_installed(self, proxy_module, monkeypatch):
        not_installed(proxy_module, monkeypatch)
        monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
        assert proxy_module.decision_handles.order("x", False, False) == ["google", "standin"]

    def test_the_drag_order_saves_and_loads(self, proxy_module, test_client, monkeypatch):
        order = ["google", "local", "typesafe", "perplexity", "nano", "ollama"]
        r = test_client.post("/config/decisions/settings", json={"decisionOrder": order})
        assert r.status_code == 200 and r.json()["settings"]["decision_order"] == order
        assert test_client.get("/config/decisions/status").json()["settings"]["decision_order"] == order
        assert proxy_module._load_config()["decisions"]["decision_order"] == order

    def test_google_can_be_the_players_first_choice(self):
        import decision_wiring
        assert "google" in decision_wiring.answer_first_choices() and decision_wiring.clean_order(None, "google")[0] == "google"

    def test_the_first_cloud_choice_can_be_google(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
        assert proxy_module.decision_handles.cloud_choice(False, False) == "google"


class TestSpeedsAndCost:
    def test_the_answer_is_recorded_as_google_with_the_decisions_role(self, proxy_module, test_client, goog, monkeypatch, tmp_path):
        import sqlite3
        not_installed(proxy_module, monkeypatch)
        db = tmp_path / "speeds.db"
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        monkeypatch.setattr(proxy_module, "SPEED_HISTORY_DB", str(db))
        proxy_module._init_speed_history_db()
        ask(test_client)
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM speed_history")]
        assert len(rows) == 1 and rows[0]["provider"] == "Google Decisions" and rows[0]["model"] == "gemini-decision-1" and rows[0]["success"] == 1 and "Decisions" in rows[0]["roles_json"]

    def test_it_is_a_paid_provider_for_routing(self, proxy_module):
        import routing
        assert proxy_module._COST_CLASS_BY_PROVIDER["Google Decisions"] == routing.CLASS_PAID


class TestTheKey:
    def test_the_key_never_reaches_the_log_even_on_a_refusal(self, proxy_module, test_client, goog, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        not_installed(proxy_module, monkeypatch)
        goog.status, goog.body = 400, {"error": {"code": "safety", "message": f"bad key {FAKE_KEY}"}}
        with with_stand_in(proxy_module):
            ask(test_client)
        goog.status, goog.body = 429, {"error": {"message": f"slow down {FAKE_KEY}"}}
        with with_stand_in(proxy_module):
            ask(test_client)
        goog.status, goog.body = 200, None
        ask(test_client)
        assert not any(FAKE_KEY in r.getMessage() for r in caplog.records)

    def test_the_shape_of_the_key_is_masked(self, proxy_module):
        assert FAKE_KEY not in proxy_module._redact_text(f"x {FAKE_KEY} y")


class TestWhereItShowsUp:
    def test_it_is_not_a_new_provider_on_the_providers_tab(self, proxy_module):
        assert not any(p["id"] == "google" for p in asyncio.run(proxy_module._dashboard_providers())), "it uses the Gemini entry's key"

    def test_the_decisions_card_lists_it(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "gemini_api_key", None)
        off = next(p for p in test_client.get("/config/decisions/status").json()["providers"] if p["id"] == "google")
        assert off["ready"] is False and off["models"] == [] and off["choice"] == "google" and off["offReason"] == "You haven't added a Google (Gemini) key, so this choice is turned off."
        monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
        on = next(p for p in test_client.get("/config/decisions/status").json()["providers"] if p["id"] == "google")
        assert on["ready"] is True and on["models"] == ["gemini-decision-1"] and on["model"] == "gemini-decision-1" and on["label"] == "My Google key"

    def test_it_is_never_offered_for_chat(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
        listing = json.dumps(test_client.get("/v1/models").json()).lower()
        assert "gemini-decision" not in listing

    def test_the_quick_test_can_ask_it(self, proxy_module, test_client, goog):
        import decision_test
        sc = decision_test.listing()[0]["id"]
        choices = {c["id"]: c for c in test_client.get("/quicktest/decision/options").json()["choices"]}
        assert choices["google"]["reason"] == "" and choices["google"]["paid"] is True
        r = test_client.post("/quicktest/decision", json={"who": "google", "scenario": sc}).json()
        assert r["ok"] is True and r["line"].startswith("Answered by gemini-decision-1 in ")
