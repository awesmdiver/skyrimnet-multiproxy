"""Quick Test for Quick decisions (Jev) (queue: quick-test-can-test-quick-decisions, 2026-10-05). Stand-ins on 127.0.0.1 or in memory only: no real key is used or printed.
Each choice goes through the real function SkyrimNet's request goes through, says who answered and how long it took, an unavailable choice says why, and nothing is saved."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import decision_test
import decision_test_ui
from tests.test_decision_order import StubEngine, deepseek, local      # noqa: F401  (local is a fixture)
from tests.test_nano_decisions import FAKE_KEY, StandIn, nano, not_installed      # noqa: F401  (nano is a fixture)

COMBAT_ANSWERS = {
    "action_choice": {"type": "choice", "choice": "flee", "confidence": 0.8, "probabilities": {"attack": 0.1, "flee": 0.8, "surrender": 0.1}},
    "fear_score": {"type": "score", "score": 7.2, "legend": {}, "probabilities": {}, "confidence": 0.6},
}


def run(test_client, who, scenario="combat"):
    r = test_client.post("/quicktest/decision", json={"who": who, "scenario": scenario})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def no_keys(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    monkeypatch.setattr(proxy_module, "auth", MagicMock(session=None, is_ready=False))


class StatusStub(StubEngine):
    def status(self):
        return {"ready": True, "installed": True}


@pytest.fixture
def local_ready(proxy_module, local, monkeypatch):      # noqa: F811
    stub = local()
    stub.__class__ = StatusStub
    return stub


# ---- the scenarios and how they become real questions -------------------------------------------------------------------------------------------------
def test_there_are_four_scenarios_in_the_directors_words_in_order():
    assert [s[1] for s in decision_test.SCENARIOS] == ["Combat Survival Decision", "Stealth and Detection Routing", "Dynamic Dialogue and Bounty Check", "Stolen Goods Merchant Check"]
    state, questions = decision_test.request("combat")
    assert state.startswith("The NPC, a Whiterun Guard, currently has 15% health remaining.")
    assert questions["action_choice"] == {"type": "choice", "instructions": "Based on the state, does the guard 'attack', 'flee', or 'surrender'?",
                                          "criteria": {"attack": None, "flee": None, "surrender": None}}
    assert questions["fear_score"] == {"type": "score", "instructions": "Rate the guard's fear level from 1 to 10.", "criteria": [str(i) for i in range(1, 11)]}


def test_the_one_to_a_hundred_scale_is_sent_as_ten_bands_and_yes_no_is_a_noul():
    _, q = decision_test.request("stealth")
    assert q["alertness_score"]["criteria"] == ["1-10", "11-20", "21-30", "31-40", "41-50", "51-60", "61-70", "71-80", "81-90", "91-100"]
    assert list(q["reaction_choice"]["criteria"]) == ["ignore", "search_area", "warn_others"]
    _, m = decision_test.request("merchant")
    assert m == {"will_buy_stolen_goods": {"type": "noul", "instructions": "Answer strictly 'yes' or 'no': Will the merchant purchase this item from the player?"}}
    assert decision_test.request("nope") is None


def test_answers_read_the_way_a_person_reads_them():
    _, q = decision_test.request("combat")
    got = decision_test.read_answers(q, COMBAT_ANSWERS)
    assert got == [{"name": "action_choice", "text": "flee", "confidence": 0.8}, {"name": "fear_score", "text": "8", "confidence": 0.6}]
    _, m = decision_test.request("merchant")
    assert decision_test.read_answers(m, {"will_buy_stolen_goods": {"type": "noul", "noul": 0.9}}) == [{"name": "will_buy_stolen_goods", "text": "Yes", "confidence": 0.9}]
    assert decision_test.read_answers(m, {"will_buy_stolen_goods": {"type": "noul", "noul": 0.2}})[0]["text"] == "No"
    assert decision_test.read_answers(m, {})[0]["text"] == "No answer"


# ---- who can answer ---------------------------------------------------------------------------------------------------------------------------
def test_unavailable_choices_say_why_and_a_press_does_nothing(proxy_module, test_client, no_keys):
    o = test_client.get("/quicktest/decision/options").json()
    by = {c["id"]: c for c in o["choices"]}
    assert [c["id"] for c in o["choices"]] == ["local", "typesafe", "nanogpt", "ollama", "perplexity", "setup"] and len(o["scenarios"]) == 4
    assert by["typesafe"]["reason"] == "You haven't added a TypeSafe key, so this choice is turned off."
    assert by["nanogpt"]["reason"] == "You haven't added a Nano-GPT key, so this choice is turned off."
    assert by["local"]["reason"] in ("The local model isn't installed, so that choice is off.", "The local model isn't running, so that choice is off.")
    assert by["setup"]["reason"] == "" and by["typesafe"]["paid"] and by["nanogpt"]["paid"] and not by["local"]["paid"]
    with patch.object(proxy_module, "call_typesafe_systemone", AsyncMock()) as ts:
        r = run(test_client, "typesafe")
    assert r == {"ok": False, "error": "You haven't added a TypeSafe key, so this choice is turned off."} and ts.await_count == 0


def test_a_bad_request_is_refused(test_client):
    assert test_client.post("/quicktest/decision", json={"who": "magic", "scenario": "combat"}).status_code == 400
    assert test_client.post("/quicktest/decision", json={"who": "local", "scenario": "x"}).status_code == 400


# ---- each way of answering, through its real function ------------------------------------------------------------------------------------------
def test_nano_gpt_gets_the_typed_questions_and_the_line_names_it(proxy_module, test_client, nano, monkeypatch):
    not_installed(proxy_module, monkeypatch)
    nano.body = {"model": "typesafe/jev-latest", "answers": COMBAT_ANSWERS, "usage": {"input_tokens": 5, "output_tokens": 0}}
    seen = []
    real = proxy_module._record_speed

    async def spy(*a, **k):
        seen.append(k.get("provider"))
        return await real(*a, **k)
    monkeypatch.setattr(proxy_module, "_record_speed", spy)
    proxy_module.decision_handles.nano_line = "kept"
    r = run(test_client, "nanogpt")
    assert r["ok"] and len(nano.calls) == 1 and nano.calls[0]["body"]["questions"] == decision_test.request("combat")[1]
    assert r["line"].startswith("Answered by Nano-GPT (jev-latest) in ") and r["line"].endswith(" s")
    assert [a["text"] for a in r["answers"]] == ["flee", "8"] and r["asked"][0]["name"] == "action_choice"
    assert seen == ["Nano-GPT"], "recorded in Speeds under the real provider"
    assert proxy_module.decision_handles.nano_line == "kept", "the card's own line is put back"


def test_a_nano_gpt_refusal_says_so_plainly_and_names_the_next_step(proxy_module, test_client, nano, monkeypatch):
    not_installed(proxy_module, monkeypatch)
    nano.status, nano.body = 401, {"error": {"message": "bad key"}}
    r = run(test_client, "nanogpt")
    assert r["ok"] is False and r["error"] == "Nano-GPT said no (your key may not be right)." and r["next"] == "Next in line: another AI standing in."
    nano.status = 402
    assert run(test_client, "nanogpt")["error"] == "Nano-GPT said no (your balance may be empty)."


def test_typesafe_goes_through_its_real_call(proxy_module, test_client, no_keys, monkeypatch):
    monkeypatch.setattr(proxy_module, "typesafe_api_key", "ts-FAKE")
    not_installed(proxy_module, monkeypatch)
    ok = AsyncMock(return_value=(200, json.dumps({"answers": COMBAT_ANSWERS, "usage": {}}).encode(), "application/json"))
    with patch.object(proxy_module, "call_typesafe_systemone", ok):
        r = run(test_client, "typesafe")
    assert r["ok"] and r["line"].startswith("Answered by TypeSafe in ")
    assert ok.await_args.args[2] == decision_test.request("combat")[1]
    bad = AsyncMock(return_value=(401, json.dumps({"error": {"message": "Invalid API key"}}).encode(), "application/json"))
    with patch.object(proxy_module, "call_typesafe_systemone", bad):
        r = run(test_client, "typesafe")
    assert r["ok"] is False and r["error"] == "TypeSafe said no (Invalid API key)." and r["next"] == "Nothing else is set up to answer.", "with a TypeSafe key and no local model nothing follows it, as for SkyrimNet"


def test_the_local_model_answers_through_the_real_handler(proxy_module, test_client, no_keys, local_ready):
    local_ready.answer = AsyncMock(return_value={"answers": COMBAT_ANSWERS, "usage": {"input_tokens": 9, "output_tokens": 1}})
    r = run(test_client, "local")
    assert r["ok"] and r["line"].startswith("Answered by Kev-4B on this device in ") and local_ready.answer.await_count == 1
    local_ready.answer = AsyncMock(side_effect=proxy_module.decision_wiring.de.DecisionNotReady())
    r = run(test_client, "local")
    assert r["ok"] is False and "The local model couldn't answer" in r["error"]


def test_as_set_up_follows_the_real_order_and_a_stand_in_is_never_called_jev(proxy_module, test_client, no_keys, monkeypatch):
    not_installed(proxy_module, monkeypatch)
    dseek = AsyncMock(return_value=(json.dumps({"action_choice": "flee", "fear_score": 7}), {"prompt_tokens": 30, "completion_tokens": 4}))
    monkeypatch.setattr(proxy_module, "deepseek_api_key", "k")
    with patch.object(proxy_module, "call_deepseek_direct", dseek):
        r = run(test_client, "setup")
    assert r["ok"] and r["line"].startswith("A stand-in AI answered: DeepSeek in ") and "Jev" not in r["line"] and dseek.await_count == 1


def test_as_set_up_through_nano_gpt_names_it(proxy_module, test_client, nano, monkeypatch):
    not_installed(proxy_module, monkeypatch)
    nano.body = {"model": "x", "answers": COMBAT_ANSWERS, "usage": {}}
    r = run(test_client, "setup")
    assert r["ok"] and r["line"].startswith("Answered by Nano-GPT (jev-latest) in ")


def test_it_changes_no_setting_and_saves_nothing(proxy_module, test_client, nano, monkeypatch):
    not_installed(proxy_module, monkeypatch)
    nano.body = {"model": "x", "answers": COMBAT_ANSWERS, "usage": {}}
    before = (json.dumps(proxy_module._load_config(), sort_keys=True), proxy_module.decision_handles.settings())
    proxy_module.decision_handles.last_answer = {"by": "x"}
    run(test_client, "nanogpt")
    assert (json.dumps(proxy_module._load_config(), sort_keys=True), proxy_module.decision_handles.settings()) == before
    assert proxy_module.decision_handles.last_answer == {"by": "x"}


# ---- the page ---------------------------------------------------------------------------------------------------------------------------------------
def test_the_card_is_on_the_quick_test_tab_with_its_words_and_the_cost_line(test_client):
    h = test_client.get("/").text
    pane = h[h.index('<div id="paneQuick"'):h.index('<div id="paneSpeeds"')]
    assert "Try a provider before you play." in pane and 'id="dqCard"' in pane and "Try Quick decisions" not in pane, "the chat test is still there; the section title replaces the card's heading"
    for w in ("Pick who should answer.", "Keep the sample scenario or pick another.", "Press Send.", "Read the answers below.", ">Who answers<", ">Scenario<"):
        assert w in decision_test_ui.PANE_HTML.replace("<li>", ">").replace("</li>", "<"), w
    assert "const DQ_COST = 'This sends one real request.';" in decision_test_ui.JS
    assert "'What was asked'" in decision_test_ui.JS and "'Answers'" in decision_test_ui.JS and "if (which === 'quick') dqOpened();" in h
    assert "innerHTML" not in decision_test_ui.JS, "what the server sent goes in as text"
