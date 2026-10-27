"""Check it like SkyrimNet (queue: check-it-like-skyrimnet, 2026-10-25). A stand-in model answers in memory (no network, no key): the three parts are judged green or
amber, broken action text counts as not usable, the time in line is counted, a key problem stops the check, the decision samples are asked of a stand-in decision
engine, and the own-play card is read from a scratch history. Nothing here touches the real history, settings or models."""
import asyncio
import json
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

import skyrimnet_check
import skyrimnet_check_samples as samples

MODEL_PREFIX = "glm-"


def good_reply(system_prompt):
    if "JSON object" in system_prompt:
        return '{"action": "Wait", "target": "player", "reason": "nothing to do"}'
    return "Aye, the road is quiet tonight."


def fake_model(monkeypatch, proxy_module, reply=good_reply, delay=0.0):
    seen = {"n": 0, "active": 0, "peak": 0}

    async def direct(system_prompt, messages, model, max_tokens, **extra):
        seen["n"] += 1
        seen["active"] += 1
        seen["peak"] = max(seen["peak"], seen["active"])
        try:
            if delay:
                await asyncio.sleep(delay)
            return reply(system_prompt or ""), {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}
        finally:
            seen["active"] -= 1

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    return seen


def check(test_client, proxy_module, part, model=None):
    r = test_client.post("/quicktest/check", json={"model": model or proxy_module.GLM_PREFIX + "glm-4.6", "part": part}, headers={"X-MultiProxy-Quick-Test": "1"})
    assert r.status_code == 200, r.text
    return r.json()


# ---- the samples ----------------------------------------------------------------------------------------------------------------------------------
def test_the_sample_sets_have_the_numbers_the_card_promises():
    assert len(samples.CHAT_SAMPLES) == skyrimnet_check.ONE_AT_A_TIME == 5
    assert len(samples.ACTION_SAMPLES) == skyrimnet_check.ACTION_COUNT == 10
    assert len(samples.DECISION_SAMPLES) == 3
    assert {s["job"] for s in samples.CHAT_SAMPLES} == {"Dialogue", "Memory", "Action", "Vision (text only)"}
    assert all(s["good"] for s in samples.CHAT_SAMPLES + samples.ACTION_SAMPLES)
    assert all(s["expected"] and set(s["expected"]) <= set(s["options"]) for s in samples.DECISION_SAMPLES)


def test_every_json_sample_is_one_the_program_treats_as_wanting_json(proxy_module):
    for s in samples.ACTION_SAMPLES + [c for c in samples.CHAT_SAMPLES if c["id"] in ("memory", "action")]:
        assert proxy_module._expects_json_reply(s["system"], [{"content": s["user"]}]), s["id"]
    for s in samples.CHAT_SAMPLES:
        if s["id"] not in ("memory", "action"):
            assert not proxy_module._expects_json_reply(s["system"], [{"content": s["user"]}]), s["id"]


# ---- the judging ------------------------------------------------------------------------------------------------------------------------------------
def test_the_judging_numbers():
    assert skyrimnet_check.actions([{"ok": True, "label": "fine"}] * 9 + [{"ok": True, "label": "broken"}])["state"] == "green"
    assert skyrimnet_check.actions([{"ok": True, "label": "fine"}] * 6 + [{"ok": False, "label": "broken"}] * 4)["state"] == "amber"
    assert skyrimnet_check.actions([{"ok": True, "label": "fine"}] * 5 + [{"ok": False, "label": "broken"}] * 5)["state"] == "red"
    a = skyrimnet_check.actions([{"ok": True, "label": "fine"}] * 8 + [{"ok": True, "label": "repaired"}] * 2)
    assert (a["usable"], a["repaired"], a["state"]) == (10, 2, "green")
    assert skyrimnet_check.BUSY_SLOW_SECONDS == 20.0
    assert skyrimnet_check.busy_room([{"ok": True, "seconds": 20.5, "waited": 0.0}])["state"] == "amber"
    assert skyrimnet_check.busy_room([{"ok": True, "seconds": 19.9, "waited": 0.0}])["state"] == "green"
    assert skyrimnet_check.reply_label("asked_again", "valid") == "repaired"
    assert skyrimnet_check.reply_label(None, "broken") == "broken"


def test_a_key_or_payment_refusal_is_told_from_a_slow_model():
    assert skyrimnet_check.is_account_problem(401, "") and skyrimnet_check.is_account_problem(402, "")
    assert skyrimnet_check.is_account_problem(500, "Your API key was not accepted.")
    assert not skyrimnet_check.is_account_problem(502, "The AI gave a broken answer.")


# ---- the three parts, end to end --------------------------------------------------------------------------------------------------------------------
def test_a_model_that_copes_is_green_on_every_part(test_client, proxy_module, monkeypatch):
    fake_model(monkeypatch, proxy_module)
    one, busy, act = (check(test_client, proxy_module, p) for p in ("one", "busy", "actions"))
    assert (one["state"], one["answered"], one["total"]) == ("green", 5, 5) and one["slowest"] is not None
    assert (busy["state"], busy["answered"], busy["total"]) == ("green", 6, 6)
    assert (act["state"], act["usable"], act["repaired"], act["total"]) == ("green", 10, 0, 10)
    assert not one["stopped"] and not busy["stopped"] and not act["stopped"]


def test_the_busy_room_sends_all_six_together_and_a_slow_one_turns_amber(test_client, proxy_module, monkeypatch):
    seen = fake_model(monkeypatch, proxy_module, delay=0.15)
    monkeypatch.setattr(skyrimnet_check, "BUSY_SLOW_SECONDS", 0.1)
    busy = check(test_client, proxy_module, "busy")
    assert seen["peak"] == 6                       # six were in the model at once
    assert busy["state"] == "amber" and busy["slowest"] > 0.1


def test_the_time_in_line_is_counted(test_client, proxy_module, monkeypatch):
    seen = fake_model(monkeypatch, proxy_module, delay=0.4)
    monkeypatch.setitem(proxy_module.REQUEST_LIMITS, "glm", 2)       # only two at a time: four of the six stand in line
    busy = check(test_client, proxy_module, "busy")
    assert seen["peak"] == 2
    assert busy["waited"] == 4 and busy["longest_wait"] >= 0.3


def test_broken_action_text_is_not_usable_and_a_fenced_one_is_repaired(test_client, proxy_module, monkeypatch):
    n = {"i": 0}

    def reply(system_prompt):
        n["i"] += 1
        if n["i"] <= 2:
            return '```json\n{"action": "Wait", "target": "x", "reason": "y"}\n```'           # wrapper noise: the program's own repair fixes it
        if n["i"] <= 6:
            return "I think the guard should probably attack, but I cannot say for sure."       # broken, even when asked again
        return '{"action": "Wait", "target": "x", "reason": "y"}'

    fake_model(monkeypatch, proxy_module, reply)
    act = check(test_client, proxy_module, "actions")
    assert act["repaired"] == 2 and act["usable"] < 10 and act["usable"] >= 2
    assert act["state"] in ("amber", "red")


def test_a_key_problem_stops_the_check_at_once(test_client, proxy_module, monkeypatch):
    calls = {"n": 0}

    async def direct(system_prompt, messages, model, max_tokens, **extra):
        calls["n"] += 1
        raise proxy_module.HTTPException(status_code=401, detail="Your GLM key was not accepted.")

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    one = check(test_client, proxy_module, "one")
    assert calls["n"] == 1                                            # no retry loop
    assert one["stopped"] == "Your GLM key was not accepted."


def test_a_bad_request_is_refused(test_client):
    assert test_client.post("/quicktest/check", json={"model": "", "part": "one"}).status_code == 400
    assert test_client.post("/quicktest/check", json={"model": "x", "part": "nope"}).status_code == 400


# ---- the decision model -----------------------------------------------------------------------------------------------------------------------------
def test_the_decision_samples_are_asked_and_judged_against_the_expected_answer(proxy_module, test_client, monkeypatch):
    given = {"kind": "greeting", "mood": "gentle", "next": "the guard"}
    ask = iter(samples.DECISION_SAMPLES)
    monkeypatch.setattr(proxy_module, "_decision_test_choices", lambda: [{"id": "local", "reason": ""}])

    async def jev(req, request, rid):
        s = next(ask)
        return proxy_module.JSONResponse({"answers": {"answer": {"type": "choice", "choice": given[s["id"]], "confidence": 0.7}}})

    monkeypatch.setattr(proxy_module, "_handle_jev_request", jev)
    r = test_client.post("/quicktest/check/decision", json={}, headers={"X-MultiProxy-Quick-Test": "1"}).json()
    assert r["ok"] and [d["answer"] for d in r["rows"]] == ["greeting", "gentle", "the guard"]
    assert [d["as_expected"] for d in r["rows"]] == [True, False, True]
    assert all(d["confidence"] == 0.7 and d["seconds"] >= 0 for d in r["rows"])


def test_the_decision_check_is_offered_only_when_there_is_a_decision_model(proxy_module, test_client, monkeypatch):
    monkeypatch.setattr(proxy_module, "_decision_test_choices", lambda: [{"id": "local", "reason": "The local model isn't installed, so that choice is off."}])
    assert test_client.get("/quicktest/check/options").json()["decision"] is False
    r = test_client.post("/quicktest/check/decision", json={}).json()
    assert r == {"ok": False, "error": "The local model isn't installed, so that choice is off."}


# ---- from your own play ------------------------------------------------------------------------------------------------------------------------------
def test_own_play_reads_the_scratch_history_and_lists_only_jobs_with_data(proxy_module, test_client):
    now = time.time()
    rows = [("dialogue", 1, "fine", 0.2), ("dialogue", 1, "repaired", 4.1), ("dialogue", 1, "asked_again", None), ("dialogue", 0, None, 9.0),
            ("action_evaluation", 1, "fine", 18.9), ("other", 1, None, None), ("memory", 1, None, None)]
    with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
        for i, (job, ok, repair, wait) in enumerate(rows):
            conn.execute("INSERT INTO speed_history (ts, model, elapsed, success, job, reply_repair, queue_wait) VALUES (?, 'm', 1.0, ?, ?, ?, ?)", (now - 60 - i, ok, job, repair, wait))
        conn.execute("INSERT INTO speed_history (ts, model, elapsed, success, job, reply_repair) VALUES (?, 'm', 1.0, 1, 'vision', 'fine')", (now - 9 * 86400,))      # older than 7 days
    own = test_client.get("/quicktest/check/options").json()["own"]
    by = {a["job"]: a for a in own}
    assert set(by) == {"Dialogue", "Action Evaluation", "Memory"}                       # vision is too old, "other" is not a job
    d = by["Dialogue"]
    assert (d["replies"], d["fine"], d["repaired"], d["asked_again"], d["longest_wait"]) == (3, 1, 1, 1, 4.1)          # the failed one is not a reply
    assert by["Action Evaluation"]["longest_wait"] == 18.9 and by["Memory"]["longest_wait"] is None


def test_with_no_data_there_is_nothing_to_list(proxy_module, test_client):
    assert test_client.get("/quicktest/check/options").json()["own"] == []


# ---- the card on the page ----------------------------------------------------------------------------------------------------------------------------
def test_the_card_is_on_the_quick_test_tab_with_its_words(test_client):
    h = test_client.get("/").text
    pane = h[h.index('<div id="paneQuick"'):h.index('<div id="paneSpeeds"')]
    for needle in ('id="ckCard"', 'onclick="ckRun()"', 'onclick="ckRunDecision()"', "Check it</button>", "Check my decision model</button>",
                   "The sample prompts are built in. They are shaped like the game's, but they are not your game.", "From your own play (last 7 days)"):
        assert needle in pane, needle
    assert "title: 'Check it like SkyrimNet', open: false" in h and "if (which === 'quick') ckOpened();" in h
    assert "textContent" in h and "innerHTML = d." not in h
