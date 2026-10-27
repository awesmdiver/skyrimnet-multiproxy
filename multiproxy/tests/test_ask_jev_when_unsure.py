"""The local model answers first and Jev is asked only when it isn't sure (queue: quick-decisions-ask-jev-when-the-local-model-is-not-sure, 2026-10-05).
Stand-ins only (a sleeping server on 127.0.0.1, a stub local engine); no real key is used or printed."""
import json
from unittest.mock import AsyncMock

import pytest

import decision_test
import decision_ui
from tests.test_decision_order import StubEngine, local      # noqa: F401  (local is a fixture)
from tests.test_decision_time_limit import SleepyServer, as_nano, as_typesafe
from tests.test_quicktest_decisions import StatusStub, run
from tests.test_jev_provider import REAL_JEV_REQUEST

REQ = {"model": "typesafe/jev-latest", "state": "x", "questions": {
    "pick": {"type": "choice", "instructions": "?", "criteria": {"a": None, "b": None}},
    "fear": {"type": "score", "instructions": "?", "criteria": ["1", "2", "3"]},
    "buy": {"type": "noul", "instructions": "?"}}}


def local_says(conf, noul=0.9, score_conf=0.0):
    return {"answers": {"pick": {"type": "choice", "choice": "a", "confidence": conf, "probabilities": {"a": conf, "b": 1 - conf}},
                        "fear": {"type": "score", "score": 1.0, "legend": {}, "probabilities": {}, "confidence": score_conf},
                        "buy": {"type": "noul", "noul": noul}}, "usage": {"input_tokens": 5, "output_tokens": 1}}


JEV_ANSWERS = {"pick": {"type": "choice", "choice": "b", "confidence": 0.95, "probabilities": {"a": 0.05, "b": 0.95}}, "fear": {"type": "score", "score": 2.0, "legend": {}, "probabilities": {}, "confidence": 0.5},
               "buy": {"type": "noul", "noul": 0.1}}


@pytest.fixture
def setup(proxy_module, local, monkeypatch):      # noqa: F811
    server = SleepyServer(delay=0.0)
    server.body = {"model": "m", "answers": JEV_ANSWERS, "usage": {"input_tokens": 3, "output_tokens": 0}}
    stub = local()
    monkeypatch.setattr(proxy_module, "_paid_limit", lambda: 0.3)
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    spied = []
    real = proxy_module._record_speed

    async def spy(*a, **k):
        spied.append((k.get("provider"), k.get("fail_reason")))
        return await real(*a, **k)
    monkeypatch.setattr(proxy_module, "_record_speed", spy)

    class S:
        pass
    s = S()
    s.server, s.stub, s.spied, s.pm, s.mp = server, stub, spied, proxy_module, monkeypatch
    yield s
    server.close()
    h = proxy_module.decision_handles
    h.nano_line = h.typesafe_line = ""
    h.last_answer = None
    proxy_module._save_config({"decisions": {}})


def ask(test_client, body=None):
    return test_client.post("/v1/systemone", json=body or REQ)


def with_nano(s):
    as_nano(s.pm, s.mp, s.server)


# ---- the rule ----------------------------------------------------------------------------------------------------------------------------------------
def test_lowest_confidence_looks_at_choices_and_yes_no_only():
    assert decision_test.lowest_confidence(REQ["questions"], local_says(0.8, noul=0.7, score_conf=0.0)["answers"]) == pytest.approx(0.7)
    assert decision_test.lowest_confidence(REQ["questions"], local_says(0.4, noul=0.1)["answers"]) == pytest.approx(0.4)
    only_score = {"fear": REQ["questions"]["fear"]}
    assert decision_test.lowest_confidence(only_score, {"fear": {"type": "score", "score": 1, "confidence": 0.0}}) is None


def test_a_sure_local_answer_returns_at_once_and_calls_nothing_paid(setup, test_client):
    with_nano(setup)
    setup.stub.answer = AsyncMock(return_value=local_says(0.9))
    r = ask(test_client)
    assert r.status_code == 200 and r.json()["answers"]["pick"]["choice"] == "a" and setup.server.calls == 0
    assert [p for p, _ in setup.spied] == ["Local decision model"]
    assert "unsure" not in (setup.pm.decision_handles.last_answer or {})


def test_an_unsure_choice_asks_jev_and_its_answer_wins(setup, test_client):
    with_nano(setup)
    setup.stub.answer = AsyncMock(return_value=local_says(0.39))
    r = ask(test_client)
    assert r.status_code == 200 and r.json()["answers"]["pick"]["choice"] == "b" and setup.server.calls == 1
    assert [p for p, _ in setup.spied] == ["Local decision model", "Nano-GPT"], "both calls recorded, each under its real provider"
    la = setup.pm.decision_handles.last_answer
    assert la["by"] == "Nano-GPT" and la["line"] == "The local model wasn't sure, so Nano-GPT stepped in." and la["unsure"] == pytest.approx(0.39)


def test_an_unsure_yes_no_triggers_it_too_and_typesafe_is_used_when_it_is_first(setup, test_client):
    as_typesafe(setup.pm, setup.mp, setup.server)
    setup.stub.answer = AsyncMock(return_value=local_says(0.9, noul=0.55))
    r = ask(test_client)
    assert setup.server.calls == 1 and r.json()["answers"]["buy"]["noul"] == 0.1
    assert setup.pm.decision_handles.last_answer["line"] == "The local model wasn't sure, so TypeSafe stepped in."


def test_an_unsure_score_alone_does_not_trigger_it(setup, test_client):
    with_nano(setup)
    setup.stub.answer = AsyncMock(return_value=local_says(0.9, score_conf=0.0))
    ask(test_client)
    assert setup.server.calls == 0


def test_a_refusal_or_a_slow_paid_answer_keeps_the_local_answer_with_the_line(setup, test_client):
    with_nano(setup)
    setup.stub.answer = AsyncMock(return_value=local_says(0.3))
    setup.server.delay = 1.0                                   # slower than the 0.3 s limit
    r = ask(test_client)
    assert r.status_code == 200 and r.json()["answers"]["pick"]["choice"] == "a" and setup.server.calls == 1
    la = setup.pm.decision_handles.last_answer
    assert la["by"] == "local" and la["line"] == "The local model wasn't sure, but the next model took too long, so the local answer stands."
    assert ("Nano-GPT", "timeout") in setup.spied and ("Local decision model", None) in setup.spied


def test_no_key_means_no_step(setup, test_client):
    setup.stub.answer = AsyncMock(return_value=local_says(0.1))
    r = ask(test_client)
    assert r.status_code == 200 and r.json()["answers"]["pick"]["choice"] == "a" and setup.server.calls == 0


def test_the_checkbox_off_means_no_step(setup, test_client):
    with_nano(setup)
    assert test_client.post("/config/decisions/settings", json={"askWhenUnsure": False}).json()["settings"]["ask_when_unsure"] is False
    setup.stub.answer = AsyncMock(return_value=local_says(0.1))
    ask(test_client)
    assert setup.server.calls == 0


def test_the_threshold_is_honoured_and_clamped(setup, test_client):
    with_nano(setup)
    for bad in (5, 99, "x"):
        assert test_client.post("/config/decisions/settings", json={"surePercent": bad}).status_code == 400
    assert test_client.post("/config/decisions/settings", json={"surePercent": 30}).json()["settings"]["sure_percent"] == 30
    setup.stub.answer = AsyncMock(return_value=local_says(0.4))
    ask(test_client)
    assert setup.server.calls == 0, "40% is sure enough when the line is at 30%"
    setup.pm._save_config({"decisions": {"sure_percent": 500}})
    assert setup.pm.decision_handles.settings()["sure_percent"] == 95


def test_the_local_only_name_never_asks(setup, test_client):
    with_nano(setup)
    setup.stub.answer = AsyncMock(return_value=local_says(0.1))
    r = ask(test_client, {**REQ, "model": "local/decisions"})
    assert r.status_code == 200 and setup.server.calls == 0


# ---- the Quick Test ---------------------------------------------------------------------------------------------------------------------------
def test_as_set_up_says_when_jev_overrode_the_local_answer_and_the_other_choices_never_do(setup, test_client):
    with_nano(setup)
    setup.stub.__class__ = StatusStub
    _, q = decision_test.request("combat")
    unsure = {"answers": {"action_choice": {"type": "choice", "choice": "attack", "confidence": 0.39, "probabilities": {}},
                          "fear_score": {"type": "score", "score": 3.0, "legend": {}, "probabilities": {}, "confidence": 0.0}}, "usage": {}}
    setup.server.body = {"model": "m", "answers": {"action_choice": {"type": "choice", "choice": "flee", "confidence": 0.9, "probabilities": {}},
                                                   "fear_score": {"type": "score", "score": 7.0, "legend": {}, "probabilities": {}, "confidence": 0.5}}, "usage": {}}
    setup.stub.answer = AsyncMock(return_value=unsure)
    r = run(test_client, "setup")
    assert r["ok"] and r["line"].startswith("Answered by Nano-GPT (jev-latest) in ") and r["line"].endswith(" s (the local model was only 39% sure)")
    assert r["answers"][0]["text"] == "flee" and setup.server.calls == 1
    r = run(test_client, "local")
    assert r["ok"] and r["line"].startswith("Answered by Kev-4B on this device") and "sure" not in r["line"] and setup.server.calls == 1
    run(test_client, "nanogpt")
    assert setup.server.calls == 2, "Nano-GPT only: one call, no local step"


# ---- any row can hand over to the row below it (2026-10-26) ----
def set_order(test_client, order):
    r = test_client.post("/config/decisions/settings", json={"decisionOrder": order})
    assert r.status_code == 200, r.text


def test_the_last_row_has_nobody_below_so_its_unsure_answer_stands(setup, test_client):
    with_nano(setup)
    set_order(test_client, ["nano", "local", "typesafe", "perplexity", "ollama"])
    setup.server.delay = 1.0                                   # Nano-GPT (first) is slower than the 0.3 s limit: the next model down answers
    setup.stub.answer = AsyncMock(return_value=local_says(0.2))
    r = ask(test_client)
    assert r.status_code == 200 and r.json()["answers"]["pick"]["choice"] == "a", "the local model is the last ready row: nobody below, its answer stands"
    assert setup.server.calls == 1


def test_a_row_that_is_not_the_first_hands_an_unsure_answer_to_the_row_below_it(setup, test_client):
    with_nano(setup)
    ts = SleepyServer(delay=0.0)
    ts.body = {"model": "m", "answers": {**JEV_ANSWERS, "pick": {"type": "choice", "choice": "b", "confidence": 0.99, "probabilities": {"a": 0.01, "b": 0.99}}}, "usage": {"input_tokens": 1, "output_tokens": 0}}
    try:
        as_typesafe(setup.pm, setup.mp, ts)
        set_order(test_client, ["nano", "local", "typesafe", "perplexity", "ollama"])
        setup.server.delay = 1.0                               # Nano-GPT is too slow, then the local model is only 20% sure: TypeSafe, below it, answers
        setup.stub.answer = AsyncMock(return_value=local_says(0.2))
        r = ask(test_client)
        assert r.status_code == 200 and r.json()["answers"]["pick"]["choice"] == "b" and ts.calls == 1
        assert setup.pm.decision_handles.last_answer["by"] == "TypeSafe"
    finally:
        ts.close()


def test_the_seconds_limit_is_there_while_the_box_is_ticked_and_gone_when_it_is_not(proxy_module, test_client):
    try:
        assert test_client.post("/config/decisions/settings", json={"askWhenUnsure": True, "paidLimitSeconds": 4}).status_code == 200
        assert proxy_module._paid_limit() == 4.0
        assert test_client.post("/config/decisions/settings", json={"askWhenUnsure": False}).status_code == 200
        assert proxy_module._paid_limit() is None, "unticked: no hand-over by time"
    finally:
        proxy_module._save_config({"decisions": {}})


def test_the_local_model_is_capped_by_the_seconds_only_when_that_is_shorter_than_its_own_limit(proxy_module):
    h = proxy_module.decision_handles
    s = h.settings()
    assert h._time_cap() == s["paid_limit_seconds"]
    import decision_engine as de
    assert de.GPU_LIMIT_SECONDS < 6.0 and de.CPU_LIMIT_SECONDS <= 6.0, "at the default 6 seconds the local model's own limit is already the shorter one: a healthy local model never hands over more than before"


# ---- the card ------------------------------------------------------------------------------------------------------------------------------------
def test_the_card_has_the_one_sentence_with_both_numbers_inside_its_box():
    js = decision_ui.DECISION_JS
    assert "Try the next model down if the current one takes longer than " in js and " seconds or is less than " in js and " % sure.</label>" in js
    assert "dcSetting({ surePercent: this.value })" in js and "dcSetting({ paidLimitSeconds: this.value })" in js and "dcSetting({ askWhenUnsure: this.checked })" in js
    assert js.count("(on ? '' : ' disabled')") == 2, "both numbers are disabled while the box is unticked"
    assert "if (s.lastAnswer && s.lastAnswer.line)" in js or "dcSt.lastAnswer && dcSt.lastAnswer.line" in js
