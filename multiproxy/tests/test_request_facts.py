"""Logs: what arrived, what SkyrimNet sent along, and what we answered (2026-10-02).

request_facts on its own (masking, once per distinct hint, the record and its line), then the real routes: a decision on each address, a chat reply, a retry, a
stand-in, a failure, and that no response header is added yet."""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import request_facts as rf
from tests.test_jev_provider import REAL_JEV_ANSWER_BODY, REAL_JEV_REQUEST, _make_mock_response, _make_mock_session

FAKE_KEY = "sk-abcdefghij1234567890SECRET"


def redact(text):
    import re
    return re.sub(r"\bsk-[A-Za-z0-9_-]{10,}", "***REDACTED***", text)


# ---- what SkyrimNet sent along ------------------------------------------------------------------------------------------------------------------
class TestHints:
    def test_plumbing_is_not_a_hint(self):
        h = {"Content-Type": "application/json", "Content-Length": "5", "Host": "x", "Connection": "keep-alive", "Accept": "*/*", "User-Agent": "u"}
        assert rf.hint_headers(h) == []

    def test_anything_else_is_shown_by_name_and_value(self):
        assert rf.hint_headers({"X-Job": "dialogue", "X-Priority": "high", "Host": "x"}) == [("x-job", "dialogue"), ("x-priority", "high")]

    def test_a_key_in_a_header_never_reaches_a_line(self):
        h = {"Authorization": f"Bearer {FAKE_KEY}", "X-Api-Key": FAKE_KEY, "X-Note": f"see {FAKE_KEY}", "X-Token": "plain-looking-secret"}
        shown = dict(rf.hint_headers(h, redact))
        assert shown["authorization"] == rf.MASKED and shown["x-api-key"] == rf.MASKED and shown["x-token"] == rf.MASKED
        assert FAKE_KEY not in shown["x-note"] and "REDACTED" in shown["x-note"]
        lines = rf.Intake(redact).observe("r1", "/v1/chat/completions", "chat", h, [FAKE_KEY])
        assert "SECRET" not in " ".join(lines) and "plain-looking-secret" not in " ".join(lines)

    def test_a_long_value_is_cut(self):
        v = dict(rf.hint_headers({"X-Big": "a" * 500}))["x-big"]
        assert len(v) < 200 and v.endswith("(cut)")


class TestIntake:
    H = {"User-Agent": "SkyrimNet/0.26.0 (rc2)", "X-Route-Hint": "fast", "Content-Type": "application/json"}

    def test_first_contact_names_the_user_agent_once(self):
        i = rf.Intake(redact)
        first = i.observe("a1", "/v1/chat/completions", "chat", self.H)
        again = i.observe("a2", "/v1/chat/completions", "chat", self.H)
        assert first[0] == '[a1] First contact from user agent "SkyrimNet/0.26.0 (rc2)"'
        assert not any("First contact" in x for x in again)
        assert any("First contact" in x for x in i.observe("a3", "/x", "chat", {"User-Agent": "SkyrimNet/0.27.0"}))      # a new version is seen too

    def test_a_hint_is_logged_once_per_distinct_set_then_quiet(self):
        i = rf.Intake(redact)
        l1 = i.observe("a1", "/v1/chat/completions", "chat", self.H)
        assert "[a1] SkyrimNet sent along (chat via /v1/chat/completions): x-route-hint=fast" in l1
        assert [x for x in i.observe("a2", "/v1/chat/completions", "chat", self.H) if "sent along" in x] == []
        l3 = i.observe("a3", "/v1/chat/completions", "chat", {**self.H, "X-Route-Hint": "slow"})        # a new value shows up
        assert any("x-route-hint=slow" in x for x in l3)
        l4 = i.observe("a4", "/v1/chat/completions", "chat", {**self.H, "X-New": "1"})                    # a new hint shows up with the others
        assert any("x-new=1" in x and "x-route-hint=fast" in x for x in l4)

    def test_a_header_that_changes_every_time_stops_being_logged(self):
        i = rf.Intake(redact)
        out = []
        for n in range(30):
            out += i.observe(f"r{n}", "/p", "chat", {"X-Request-Id": f"id-{n}"})
        assert sum("sent along" in x for x in out) == rf.VALUES_PER_NAME_MAX
        assert sum("keeps changing" in x for x in out) == 1

    def test_full_capture_logs_every_request(self):
        i = rf.Intake(redact)
        i.observe("a1", "/p", "chat", self.H)
        assert any("sent along" in x for x in i.observe("a2", "/p", "chat", self.H, capture_all=True))

    def test_extra_body_settings_are_named_once_per_distinct_set(self):
        i = rf.Intake(redact)
        first = i.observe("a1", "/p", "chat", {}, ["reasoning_effort", "top_k"])
        assert first == ["[a1] SkyrimNet's chat request also carried these settings (/p): reasoning_effort, top_k"]
        assert i.observe("a2", "/p", "chat", {}, ["top_k", "reasoning_effort"]) == []
        assert i.observe("a3", "/p", "chat", {}, ["top_k"])
        assert i.observe("a4", "/p", "decision", {}, ["reasoning_effort", "top_k"])                   # the same names on another kind of request are new

    def test_nothing_to_say_is_no_lines(self):
        assert rf.Intake(redact).observe("a1", "/p", "chat", {"Host": "x"}) == []


# ---- the record ---------------------------------------------------------------------------------------------------------------------------------
class TestRecord:
    def test_usage_in_either_style(self):
        r = rf.RequestRecord("r")
        r.note_usage({"prompt_tokens": 10, "completion_tokens": 4})
        assert (r.tokens_in, r.tokens_out) == (10, 4)
        r.note_usage({"input_tokens": 7, "output_tokens": 2})
        assert (r.tokens_in, r.tokens_out) == (7, 2)
        r.note_usage(None)
        r.note_usage({"prompt_tokens": True})
        assert (r.tokens_in, r.tokens_out) == (7, 2)

    def test_a_normal_reply_line(self):
        r = rf.RequestRecord("ab12", path="/v1/chat/completions", job="Dialogue", provider="GLM", model="glm-4.6", first_word_s=0.4, total_s=1.4,
                             queue_wait_s=0.2, tokens_in=812, tokens_out=64, outcome="ok")
        assert r.line() == "[ab12] answered: chat via /v1/chat/completions | Dialogue | GLM glm-4.6 | 1.4s (first word 0.4s, waited 0.2s in line) | in=812 out=64 tok | ok"

    def test_a_decision_names_its_questions_and_a_stand_in_says_so(self):
        r = rf.RequestRecord("c3", path="/v1/systemone", kind="decision", questions=("mood", "speaker"), provider="DeepSeek", model="deepseek-flash",
                             stand_in="TypeSafe", total_s=0.9, outcome="ok", status=200)
        assert r.line() == "[c3] answered: decision (mood, speaker) via /v1/systemone | DeepSeek deepseek-flash | stand-in for TypeSafe | 0.9s | ok"

    def test_a_retry_and_a_failure(self):
        r = rf.RequestRecord("d4", provider="GLM", model="m", retried=True, total_s=3.0, outcome="timeout", status=502)
        assert "asked again" in r.line() and r.line().endswith("failed (timeout), status 502")

    def test_unknown_facts_are_left_out(self):
        assert rf.RequestRecord("e5").line() == "[e5] answered: chat | ok"

    def test_no_response_headers_yet(self):
        assert rf.response_headers(rf.RequestRecord("x", provider="GLM")) == {} and rf.response_headers(None) == {}


# ---- through the real routes --------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def facts(proxy_module, caplog, monkeypatch):
    monkeypatch.setattr(proxy_module, "_intake", rf.Intake(proxy_module._redact_text))
    monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", False)
    caplog.set_level(logging.INFO)
    return caplog


def lines(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def typesafe(proxy_module, test_client, path, extra_headers=None, body=None):
    resp = _make_mock_response(200, REAL_JEV_ANSWER_BODY)
    session = _make_mock_session(resp)
    with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), patch.object(proxy_module, "auth", MagicMock(session=None)), \
         patch("aiohttp.ClientSession", return_value=session):
        return test_client.post(path, json=body or REAL_JEV_REQUEST, headers=extra_headers or {})


class TestDecisionsThroughTheRoutes:
    @pytest.mark.parametrize("path", ["/v1/systemone", "/v1/decisions", "/v1/chat/completions", "/some/other/path"])
    def test_the_address_and_question_come_up_on_each_route(self, proxy_module, test_client, facts, path):
        r = typesafe(proxy_module, test_client, path)
        assert r.status_code == 200
        assert any(f"-> TypeSafe jev-1.13.0 (intent) via {path}" in m for m in lines(facts, "-> TypeSafe"))
        answered = lines(facts, "answered:")
        assert len(answered) == 1 and f"decision (intent) via {path}" in answered[0] and "TypeSafe jev-1.13.0" in answered[0] and "Decisions" in answered[0]

    def test_a_stand_in_says_so_and_which_ai(self, proxy_module, test_client, facts):
        deepseek = AsyncMock(return_value=('{"intent": "greeting"}', {"prompt_tokens": 40, "completion_tokens": 5}))
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", deepseek):
            r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 200
        assert lines(facts, "Jev stand-in, answered by DeepSeek deepseek-flash instead of TypeSafe (intent) via /v1/systemone")
        a = lines(facts, "answered:")[0]
        assert "stand-in for TypeSafe" in a and "DeepSeek deepseek-flash" in a and "in=40 out=5 tok" in a

    def test_typesafe_usage_is_kept(self, proxy_module, test_client, facts):
        body = json.dumps({"intent": {"choice": "greeting"}, "usage": {"input_tokens": 90, "output_tokens": 3}}).encode()
        resp = _make_mock_response(200, body)
        with patch.object(proxy_module, "typesafe_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=_make_mock_session(resp)):
            test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert "in=90 out=3 tok" in lines(facts, "answered:")[0]

    def test_a_failure_from_typesafe_is_a_failed_line_with_its_status(self, proxy_module, test_client, facts):
        resp = _make_mock_response(429, b'{"error":"slow down"}')
        with patch.object(proxy_module, "typesafe_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=_make_mock_session(resp)):
            r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
        assert r.status_code == 429
        a = lines(facts, "answered:")[0]
        assert "failed (TypeSafe returned 429)" in a and a.endswith("status 429")

    def test_no_response_header_is_added_yet(self, proxy_module, test_client, facts):
        r = typesafe(proxy_module, test_client, "/v1/systemone")
        assert not [h for h in r.headers if h.lower().startswith("x-")]


class TestWhatSkyrimNetSentAlong:
    def test_hints_show_once_and_a_key_is_masked(self, proxy_module, test_client, facts):
        hdr = {"X-Route-Hint": "fast", "Authorization": f"Bearer {FAKE_KEY}", "User-Agent": "SkyrimNet/0.26.0 rc2"}
        typesafe(proxy_module, test_client, "/v1/systemone", hdr)
        typesafe(proxy_module, test_client, "/v1/systemone", hdr)
        sent = lines(facts, "SkyrimNet sent along")
        assert len(sent) == 1 and "x-route-hint=fast" in sent[0] and "authorization=***" in sent[0]
        assert len(lines(facts, "First contact")) == 1
        assert "SECRET" not in facts.text

    def test_extra_body_settings_are_named(self, proxy_module, test_client, facts):
        typesafe(proxy_module, test_client, "/v1/systemone", body={**REAL_JEV_REQUEST, "reasoning_effort": "low"})
        assert lines(facts, "also carried these settings (/v1/systemone): reasoning_effort")

    def test_full_capture_shows_them_on_every_request(self, proxy_module, test_client, facts, monkeypatch):
        monkeypatch.setattr(proxy_module, "CAPTURE_PROMPTS", True)
        monkeypatch.setattr(proxy_module, "capture_prompt_if_enabled", lambda req: None)
        for _ in range(2):
            typesafe(proxy_module, test_client, "/v1/systemone", {"X-Route-Hint": "fast"})
        assert len(lines(facts, "SkyrimNet sent along")) == 2


class TestChatThroughTheRoute:
    BODY = {"model": "deepseek/deepseek-flash", "messages": [{"role": "user", "content": "hello there"}]}

    def _post(self, proxy_module, test_client, direct, body=None):
        with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", direct), \
             patch.object(proxy_module, "_get_role_chips_cached", lambda: {"DeepSeek::deepseek-flash": [{"label": "Dialogue"}]}):
            return test_client.post("/v1/chat/completions", json=body or self.BODY)

    def test_a_normal_reply_has_one_summary_line_with_the_job(self, proxy_module, test_client, facts):
        r = self._post(proxy_module, test_client, AsyncMock(return_value=("hi", {"prompt_tokens": 12, "completion_tokens": 3})))
        assert r.status_code == 200 and not [h for h in r.headers if h.lower().startswith("x-")]
        a = lines(facts, "answered:")
        assert len(a) == 1
        assert "chat via /v1/chat/completions | Dialogue | " in a[0] and "deepseek-flash" in a[0] and "in=12 out=3 tok" in a[0] and a[0].endswith("| ok")
        assert "waited" in a[0]                                             # it went through the request line

    def test_a_failure_is_a_failed_line(self, proxy_module, test_client, facts):
        from fastapi import HTTPException
        r = self._post(proxy_module, test_client, AsyncMock(side_effect=HTTPException(status_code=502, detail="upstream down")))
        assert r.status_code >= 500
        assert lines(facts, "failed (")

    def test_a_reply_asked_for_twice_says_so(self, proxy_module, test_client, facts):
        rec = rf.RequestRecord("r9")
        token = rf.CURRENT.set(rec)
        try:
            proxy_module._note_record("deepseek-flash", 1.0, True, None, 5, None, "DeepSeek", "asked_again", None)
        finally:
            rf.CURRENT.reset(token)
        assert rec.retried and "asked again" in rec.line() and lines(facts, "[r9] answered:")

    def test_the_backup_is_a_stand_in_for_the_pc_that_did_not_answer(self, proxy_module, facts):
        rec = rf.RequestRecord("r8", provider="DeepSeek", model="m", stand_in="laptop", total_s=1.0, outcome="ok")
        assert "stand-in for laptop" in rec.line()
