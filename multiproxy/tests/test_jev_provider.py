"""Tests for TypeSafe's Jev decision model (queue: let-jev-decisions-through, 2026-09-29).

SkyrimNet's own experimental Decisions job (26-rc1) sends a {model, questions, state} body with no
`messages` at all -- a typed-question request, not a chat completion. The real request shape below
(model "typesafe/jev-1.13", one "intent" choice-question, state {"text": "..."}) is byte-for-byte
what a real SkyrimNet model test sent, captured live 2026-09-29 from SkyrimNet's own
openrouter_input.log (multiproxy.log itself never saw it -- a stale request predating this session's own
restarts; see the handoff for the full story). The matching real answer
({"intent": {"choice": "greeting", "confidence": 0.4, ...}}), also captured live from SkyrimNet's
openrouter_output.log, is reused below as the mocked TypeSafe response so the passthrough tests
exercise a genuinely real shape, not an invented one.

Design note this whole file is built around: is_jev_request/call_typesafe_systemone/
_handle_jev_request deliberately do NOT reuse _extract_upstream_error_message or
_openai_style_error_handler the way every other provider's errors do -- TypeSafe's response
(success or failure) has to reach SkyrimNet completely unchanged, status code included, because
SkyrimNet already speaks this exact protocol and re-wrapping it would break its own parsing.
"""
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _speed_rows(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM speed_history ORDER BY ts")]

REAL_JEV_REQUEST = {
    "model": "typesafe/jev-1.13",
    "questions": {
        "intent": {
            "criteria": {
                "complaint": "Reports a problem or displeasure",
                "greeting": "A hello or a check that someone is there",
                "other": None,
                "question": "Asks for information",
                "request": "Asks for something to be done",
            },
            "instructions": "Classify the intent of `text`.",
            "type": "choice",
        },
    },
    "state": {"text": "Hello there! Are you working?"},
}

REAL_JEV_ANSWER_BODY = json.dumps({
    "intent": {
        "choice": "greeting",
        "confidence": 0.4,
        "probabilities": {"complaint": 0, "greeting": 0.52, "other": 0, "question": 0.48, "request": 0},
        "type": "choice",
    },
}).encode("utf-8")


def _make_mock_response(status=200, body: bytes = b"{}", content_type="application/json"):
    resp = AsyncMock()
    resp.status = status
    resp.read = AsyncMock(return_value=body)
    resp.text = AsyncMock(return_value=body.decode("utf-8"))
    resp.json = AsyncMock(return_value=json.loads(body) if body else {})
    resp.headers = {"Content-Type": content_type}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


class TestIsJevRequest:
    """Detection: a typesafe/ model, or the questions-without-messages shape -- never both wrong
    (a normal chat request must never be misrouted here)."""

    def test_typesafe_prefixed_model_is_jev_even_with_messages(self, proxy_module):
        req = proxy_module.ChatRequest(model="typesafe/jev-latest", messages=[{"role": "user", "content": "hi"}])
        assert proxy_module.is_jev_request(req) is True

    def test_questions_with_no_messages_is_jev_without_the_prefix(self, proxy_module):
        req = proxy_module.ChatRequest(model="some-model", questions={"q": {"type": "noul"}})
        assert proxy_module.is_jev_request(req) is True

    def test_real_captured_request_is_recognized(self, proxy_module):
        req = proxy_module.ChatRequest(**REAL_JEV_REQUEST)
        assert proxy_module.is_jev_request(req) is True

    def test_plain_chat_request_is_not_jev(self, proxy_module):
        req = proxy_module.ChatRequest(model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}])
        assert proxy_module.is_jev_request(req) is False

    def test_questions_field_with_real_messages_present_is_not_jev(self, proxy_module):
        """Narrow on purpose (task's own wording: "questions and no messages") -- a chat request
        that happens to carry an unrelated `questions` extra field must not be misrouted just
        because that key exists."""
        req = proxy_module.ChatRequest(
            model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}], questions={"unrelated": 1},
        )
        assert proxy_module.is_jev_request(req) is False


class TestModelNameMapping:
    def test_short_form_maps_to_the_real_versioned_id(self, proxy_module):
        assert proxy_module._resolve_typesafe_model("typesafe/jev-1.13") == "jev-1.13.0"

    def test_latest_and_preview_pass_through_unchanged(self, proxy_module):
        assert proxy_module._resolve_typesafe_model("typesafe/jev-latest") == "jev-latest"
        assert proxy_module._resolve_typesafe_model("typesafe/jev-preview") == "jev-preview"

    def test_already_bare_versioned_id_passes_through(self, proxy_module):
        assert proxy_module._resolve_typesafe_model("jev-1.13.0") == "jev-1.13.0"

    def test_unrecognized_id_is_not_invented(self, proxy_module):
        """Never guesses -- an id this doesn't know is handed to TypeSafe verbatim, which is the
        right place to decide it's invalid, not a client-side guess."""
        assert proxy_module._resolve_typesafe_model("typesafe/some-future-model") == "some-future-model"


class TestChatRequestStillRequiresMessages:
    """Pydantic can no longer enforce this by itself now that ChatRequest also has to accept
    Jev's messages-less shape -- _chat_completions_impl's own explicit check must land on the
    exact same outcome a genuine chat request always got: rejected, before any provider is ever
    contacted."""

    def test_missing_messages_on_a_plain_chat_request_is_still_a_422(self, proxy_module, test_client):
        r = test_client.post("/v1/chat/completions", json={"model": "claude-sonnet-5"})
        assert r.status_code == 422

    def test_jev_request_is_accepted_with_no_messages_field_at_all(self, proxy_module, test_client):
        """The actual bug this whole feature exists to fix: this used to be a 422
        ("messages: Field required") before any handler or log line ran."""
        resp = _make_mock_response(200, REAL_JEV_ANSWER_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json=REAL_JEV_REQUEST)
        assert r.status_code == 200
        assert "messages" not in r.json()


class TestKeyRouting:
    def test_uses_typesafe_directly_when_its_key_is_saved(self, proxy_module, test_client):
        ts_resp = _make_mock_response(200, REAL_JEV_ANSWER_BODY)
        session = _make_mock_session(ts_resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json=REAL_JEV_REQUEST)
        assert r.status_code == 200
        url = session.post.call_args.args[0]
        assert url == proxy_module.TYPESAFE_BASE_URL

    def test_no_typesafe_key_uses_a_stand_in_from_a_ready_provider_never_openrouter(self, proxy_module, test_client):
        mock_deepseek = AsyncMock(return_value=('{"intent": "greeting"}', {}))
        with patch.object(proxy_module, "deepseek_api_key", "k"), \
             patch.object(proxy_module, "call_deepseek_direct", mock_deepseek):
            r = test_client.post("/v1/chat/completions", json=REAL_JEV_REQUEST)
        assert r.status_code == 200
        assert r.json()["answers"]["intent"]["choice"] == "greeting"
        mock_deepseek.assert_awaited_once()

    def test_neither_key_is_a_clear_error_not_a_crash(self, proxy_module, test_client):
        r = test_client.post("/v1/chat/completions", json=REAL_JEV_REQUEST)
        assert r.status_code == 500
        message = r.json()["error"]["message"]
        assert "TypeSafe" in message
        assert "OpenRouter" not in message


class TestTypeSafeDirectPassthrough:
    """The core promise: TypeSafe's own response goes back exactly as it arrived -- no tag fixer,
    no reshaping, no re-wrapping through the proxy's usual {"error":...} shape."""

    @pytest.mark.asyncio
    async def test_success_body_is_byte_identical(self, proxy_module):
        resp = _make_mock_response(200, REAL_JEV_ANSWER_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            status, body, content_type = await proxy_module.call_typesafe_systemone(
                "typesafe/jev-1.13", {"text": "hi"}, REAL_JEV_REQUEST["questions"], "req1",
            )
        assert status == 200
        assert body == REAL_JEV_ANSWER_BODY
        assert content_type == "application/json"

    @pytest.mark.asyncio
    async def test_sends_the_mapped_model_and_bearer_auth(self, proxy_module):
        resp = _make_mock_response(200, REAL_JEV_ANSWER_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            await proxy_module.call_typesafe_systemone(
                "typesafe/jev-1.13", {"text": "hi"}, {"q": {"type": "noul"}}, "req1",
            )
        payload = session.post.call_args.kwargs["json"]
        headers = session.post.call_args.kwargs["headers"]
        assert payload["model"] == "jev-1.13.0"
        assert payload["state"] == {"text": "hi"}
        assert headers["Authorization"] == "Bearer ts-test-key"

    @pytest.mark.parametrize("status", [401, 422, 429, 529])
    @pytest.mark.asyncio
    async def test_upstream_error_status_is_preserved_exactly(self, proxy_module, status):
        error_body = json.dumps({"error": "something TypeSafe-specific"}).encode("utf-8")
        resp = _make_mock_response(status, error_body)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            returned_status, body, _ = await proxy_module.call_typesafe_systemone(
                "typesafe/jev-latest", "test", {"q": {"type": "noul"}}, "req1",
            )
        assert returned_status == status
        assert body == error_body  # unwrapped -- never re-shaped into {"error":{"message":...}}

    @pytest.mark.asyncio
    async def test_end_to_end_error_status_reaches_the_client_unwrapped(self, proxy_module, test_client):
        """Same check as above, but through the real route -- proves _handle_jev_request returns a
        raw Response rather than raising HTTPException (which _openai_style_error_handler would
        have re-wrapped)."""
        error_body = json.dumps({"error": "something TypeSafe-specific"}).encode("utf-8")
        resp = _make_mock_response(429, error_body)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json=REAL_JEV_REQUEST)
        assert r.status_code == 429
        assert r.json() == {"error": "something TypeSafe-specific"}

    @pytest.mark.asyncio
    async def test_connection_failure_is_a_clean_502_not_a_crash(self, proxy_module):
        import aiohttp
        session = MagicMock()
        session.post = MagicMock(side_effect=aiohttp.ClientConnectionError("boom"))
        session.close = AsyncMock()
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            with pytest.raises(HTTPException) as exc:
                await proxy_module.call_typesafe_systemone("typesafe/jev-latest", "test", {}, "req1")
        assert exc.value.status_code == 502


class TestStandInReshape:
    """A stand-in (some other ready AI) answers in plain chat text; this reshapes that into the same
    envelope TypeSafe's real endpoint would return. Used to be tested through OpenRouter's Jev
    Router, which MultiProxy no longer connects to -- the reshaping itself is unchanged."""

    def _reshape(self, proxy_module, text, questions=None, usage=None):
        return proxy_module._reshape_jev_stand_in_answer(
            "typesafe/jev-1.13", text, questions or REAL_JEV_REQUEST["questions"], usage or {}, "req1")

    def test_reshapes_a_choice_answer_into_the_jev_envelope(self, proxy_module):
        status, body = self._reshape(proxy_module, '{"intent": "greeting"}', usage={"prompt_tokens": 160, "completion_tokens": 81})
        assert status == 200
        assert body["model"] == "typesafe/jev-1.13"
        assert body["answers"]["intent"]["type"] == "choice"
        assert body["answers"]["intent"]["choice"] == "greeting"
        assert body["answers"]["intent"]["probabilities"]["greeting"] == 1.0
        assert body["answers"]["intent"]["probabilities"]["complaint"] == 0.0
        assert body["usage"]["prompt_tokens"] == 160

    def test_a_chosen_option_not_in_criteria_falls_back_without_crashing(self, proxy_module):
        status, body = self._reshape(proxy_module, '{"intent": "not_a_real_option"}')
        assert status == 200
        assert body["answers"]["intent"]["choice"] in REAL_JEV_REQUEST["questions"]["intent"]["criteria"]

    def test_non_json_answer_is_a_clean_502_not_a_crash(self, proxy_module):
        status, body = self._reshape(proxy_module, "not json at all")
        assert status == 502
        assert "error" in body

    def test_strips_a_markdown_fence_around_the_json_answer(self, proxy_module):
        status, body = self._reshape(proxy_module, '```json\n{"intent": "question"}\n```')
        assert status == 200
        assert body["answers"]["intent"]["choice"] == "question"

    def test_noul_and_score_questions_reshape_correctly(self, proxy_module):
        questions = {
            "is_greeting": {"type": "noul", "instructions": "Is this a greeting?"},
            "urgency": {"type": "score", "instructions": "Rate urgency.", "criteria": ["low", "medium", "high"]},
        }
        status, body = self._reshape(proxy_module, '{"is_greeting": 0.9, "urgency": 2}', questions)
        assert status == 200
        assert body["answers"]["is_greeting"] == {"type": "noul", "noul": 0.9}
        assert body["answers"]["urgency"]["type"] == "score"
        assert body["answers"]["urgency"]["score"] == 2
        assert body["answers"]["urgency"]["legend"] == {"0": "low", "1": "medium", "2": "high"}

    def test_the_openrouter_fallback_is_gone(self, proxy_module):
        assert not hasattr(proxy_module, "call_jev_via_openrouter")


class TestSpeedRecording:
    @pytest.mark.asyncio
    async def test_successful_jev_call_records_speed_under_the_raw_model_name(self, proxy_module):
        """Recorded under the RAW SkyrimNet-sent model string ("typesafe/jev-1.13"), not the
        TypeSafe-mapped id ("jev-1.13.0") -- that's the string SkyrimNet's own yaml
        variants.decisions.model_name uses too, which is what makes the Decisions role chip
        actually match (_resolve_model_name has no typesafe/ entry, so it's a no-op passthrough,
        same as every other provider's "stripped name" convention -- see that function's own
        docstring)."""
        resp = _make_mock_response(200, REAL_JEV_ANSWER_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            req = proxy_module.ChatRequest(**REAL_JEV_REQUEST)
            await proxy_module._handle_jev_request(req, MagicMock(), "req1")
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        row = rows[0]
        # Recorded under the RAW SkyrimNet-sent model string ("typesafe/jev-1.13"), not the
        # TypeSafe-mapped id -- see this class's own docstring for why that's what makes the
        # Decisions role chip actually match.
        assert row["model"] == "typesafe/jev-1.13"
        assert row["success"] == 1
        assert row["provider"] == "TypeSafe"
        assert row["first_token"] is None  # no streaming -- no "starts talking" data
        assert row["fail_reason"] is None

    @pytest.mark.asyncio
    async def test_failed_jev_call_records_speed_as_a_failure_with_a_reason(self, proxy_module):
        error_body = json.dumps({"error": "bad request"}).encode("utf-8")
        resp = _make_mock_response(422, error_body)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            req = proxy_module.ChatRequest(**REAL_JEV_REQUEST)
            await proxy_module._handle_jev_request(req, MagicMock(), "req1")
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        assert rows[0]["success"] == 0
        assert rows[0]["fail_reason"] == "TypeSafe returned 422"


class TestDashboardCard:
    @pytest.mark.asyncio
    async def test_typesafe_card_is_not_testable_even_with_a_key(self, proxy_module):
        """Jev isn't a chat model -- Quick Test can never send it a plain message, key or no key."""
        with patch.object(proxy_module, "typesafe_api_key", "ts-test-key"):
            providers = await proxy_module._dashboard_providers()
        card = next(p for p in providers if p["id"] == "typesafe")
        assert card["testable"] is False
        assert card["hasKey"] is True

    @pytest.mark.asyncio
    async def test_typesafe_card_shows_off_with_no_key(self, proxy_module):
        providers = await proxy_module._dashboard_providers()
        card = next(p for p in providers if p["id"] == "typesafe")
        assert card["state"] == "off"
        assert card["testable"] is False


class TestKeyEndpoint:
    def test_saves_and_clears_the_typesafe_key(self, proxy_module, test_client):
        r = test_client.post("/config/typesafe-key", json={"key": "ts-real-key"})
        assert r.status_code == 200
        assert r.json()["status"] == "saved"
        assert proxy_module.typesafe_api_key == "ts-real-key"

        r = test_client.post("/config/typesafe-key", json={"key": ""})
        assert r.status_code == 200
        assert r.json()["status"] == "cleared"
        assert proxy_module.typesafe_api_key is None
