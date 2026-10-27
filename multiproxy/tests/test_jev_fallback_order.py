"""Tests for the Jev fallback order (queue: jev-fallback-order, 2026-09-29) -- follow-up to
"Let Jev decisions through the proxy" (commit 726ca17).

Director's decision, four routes in priority order:
  1. TypeSafe key saved -> TypeSafe directly (unchanged; see tests/test_jev_provider.py).
  2. No TypeSafe key, OpenRouter key saved -> stand-in via OpenRouter's own jev-router model
     (unchanged; see tests/test_jev_provider.py) -- re-checked before this item was built: sending
     SkyrimNet's own EXACT real captured body (no messages, no reshaping) straight to
     https://openrouter.ai/api/v1/chat/completions gets a real, live 400:
     {"error":{"message":"Input required: specify \"prompt\" or \"messages\"","code":400}} --
     rejected before any model ever saw it. So OpenRouter's jev-router still can't speak TypeSafe's
     real protocol even now; the existing stand-in behavior stays exactly as it was.
  3. Neither of those, but some OTHER real AI is ready -> a stand-in via THAT provider directly
     (call_jev_stand_in, this file's own focus).
  4. Nothing ready at all -> a clear error.

This file covers route 3 and 4 specifically: the priority table itself, each entry correctly
skipped when not ready, Claude's own real-signed-in-CLI check (auth.is_ready, not just
"installed"), and that a stand-in's Speeds-tab entry is recorded under its own real model name
with the Decisions role forced on -- never mistaken for the real Jev.
"""
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

REAL_QUESTIONS = {
    "intent": {
        "criteria": {"greeting": "A hello", "question": "Asks for information"},
        "instructions": "Classify the intent of `text`.",
        "type": "choice",
    },
}


def _speed_rows(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM speed_history ORDER BY ts")]


class TestStandInPriorityOrder:
    @pytest.mark.asyncio
    async def test_deepseek_is_tried_first_when_everything_is_ready(self, proxy_module):
        """Table order is DeepSeek, GLM, Gemini, OpenAI, Nano-GPT, Kilo, Claude -- DeepSeek wins
        when every single one of them is ready at once."""
        mock_deepseek = AsyncMock(return_value=('{"intent": "greeting"}', {"prompt_tokens": 1}))
        with patch.object(proxy_module, "deepseek_api_key", "k"), \
             patch.object(proxy_module, "glm_api_key", "k"), \
             patch.object(proxy_module, "gemini_api_key", "k"), \
             patch.object(proxy_module, "openai_api_key", "k"), \
             patch.object(proxy_module, "nano_api_key", "k"), \
             patch.object(proxy_module, "kilo_api_key", "k"), \
             patch.object(proxy_module, "auth", MagicMock(is_ready=True)), \
             patch.object(proxy_module, "call_deepseek_direct", mock_deepseek):
            result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
        assert result is not None
        status, body, real_model, display_name = result
        assert display_name == "DeepSeek"
        assert real_model == "deepseek-flash"
        assert status == 200
        mock_deepseek.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_through_to_glm_when_deepseek_not_ready(self, proxy_module):
        mock_glm = AsyncMock(return_value=('{"intent": "greeting"}', {}))
        with patch.object(proxy_module, "deepseek_api_key", None), \
             patch.object(proxy_module, "glm_api_key", "k"), \
             patch.object(proxy_module, "call_glm_direct", mock_glm):
            result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
        assert result[3] == "GLM"
        assert result[2] == "glm-4.6"
        mock_glm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_through_every_keyed_provider_to_claude_last(self, proxy_module):
        """Every keyed provider unready, but Claude's CLI is genuinely signed in -- Claude is the
        real last resort in the table, matching the director's own ordering."""
        mock_claude = AsyncMock(return_value=('{"intent": "greeting"}', {}))
        with patch.object(proxy_module, "deepseek_api_key", None), \
             patch.object(proxy_module, "glm_api_key", None), \
             patch.object(proxy_module, "gemini_api_key", None), \
             patch.object(proxy_module, "openai_api_key", None), \
             patch.object(proxy_module, "nano_api_key", None), \
             patch.object(proxy_module, "kilo_api_key", None), \
             patch.object(proxy_module, "auth", MagicMock(is_ready=True)), \
             patch.object(proxy_module, "call_api_direct", mock_claude):
            result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
        assert result[3] == "Claude"
        assert result[2] == "claude-sonnet-5-5"
        mock_claude.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_claude_needs_auth_is_ready_not_just_being_installed(self, proxy_module):
        """The real bar is auth.is_ready (genuinely signed in and confirmed working), not just
        CLAUDE_PATH being non-None (which conftest.py's own fixture always leaves as a fake, non-
        None path for every other test in this suite) -- a CLI that's installed but not actually
        ready yet must be skipped, same as any other unready provider."""
        with patch.object(proxy_module, "deepseek_api_key", None), \
             patch.object(proxy_module, "glm_api_key", None), \
             patch.object(proxy_module, "gemini_api_key", None), \
             patch.object(proxy_module, "openai_api_key", None), \
             patch.object(proxy_module, "nano_api_key", None), \
             patch.object(proxy_module, "kilo_api_key", None), \
             patch.object(proxy_module, "auth", MagicMock(is_ready=False)):
            result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
        assert result is None

    @pytest.mark.asyncio
    async def test_nothing_ready_returns_none(self, proxy_module):
        with patch.object(proxy_module, "auth", MagicMock(is_ready=False)):
            result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
        assert result is None

    @pytest.mark.asyncio
    async def test_gemini_openai_nano_kilo_each_chosen_when_only_that_one_is_ready(self, proxy_module):
        """Every remaining table entry, one at a time -- proves the table is wired to the right
        real call_X_direct function and the right real model id, not just DeepSeek/GLM/Claude."""
        cases = [
            ("gemini_api_key", "call_gemini_direct", "Gemini", "gemini-3.8-flash"),
            ("openai_api_key", "call_openai_direct", "OpenAI", "gpt-4o-mini"),
            ("nano_api_key", "call_nano_direct", "Nano-GPT", "gpt-4o-mini"),
            ("kilo_api_key", "call_kilo_direct", "Kilo Gateway", "kilo-auto/balanced"),
        ]
        for key_attr, fn_name, expected_display, expected_model in cases:
            mock_fn = AsyncMock(return_value=('{"intent": "greeting"}', {}))
            with patch.object(proxy_module, "deepseek_api_key", None), \
                 patch.object(proxy_module, "glm_api_key", None), \
                 patch.object(proxy_module, key_attr, "k"), \
                 patch.object(proxy_module, "auth", MagicMock(is_ready=False)), \
                 patch.object(proxy_module, fn_name, mock_fn):
                # Every OTHER keyed candidate ahead of this one in the table must also be off.
                other_keys = ["gemini_api_key", "openai_api_key", "nano_api_key", "kilo_api_key"]
                other_keys.remove(key_attr)
                with patch.multiple(proxy_module, **{k: None for k in other_keys}):
                    result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
            assert result is not None, f"expected a result when only {key_attr} is set"
            assert result[3] == expected_display, f"{key_attr}: expected {expected_display}, got {result[3]}"
            assert result[2] == expected_model
            mock_fn.assert_awaited_once()


class TestStandInPrompt:
    @pytest.mark.asyncio
    async def test_sends_the_flattened_question_prompt_not_the_raw_jev_body(self, proxy_module):
        mock_deepseek = AsyncMock(return_value=('{"intent": "greeting"}', {}))
        with patch.object(proxy_module, "deepseek_api_key", "k"), \
             patch.object(proxy_module, "call_deepseek_direct", mock_deepseek):
            await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "Hello!"}, REAL_QUESTIONS, "req1")
        _, messages, model, max_tokens = mock_deepseek.call_args.args
        assert model == "deepseek-flash"
        assert "Hello!" in messages[0]["content"]
        assert "intent" in messages[0]["content"]
        # A real flattened prompt (_build_jev_fallback_prompt's own natural-language framing),
        # never the raw Jev request body dumped in as JSON.
        assert '"questions":' not in messages[0]["content"]
        assert '"model":' not in messages[0]["content"]


class TestStandInErrorHandling:
    @pytest.mark.asyncio
    async def test_a_provider_error_is_reported_cleanly_not_raised(self, proxy_module):
        """A stand-in provider that raises HTTPException (a real, live failure -- an expired key,
        say) must not crash the whole Jev request; it comes back as a clean error tuple instead,
        same "always returns, never raises past this point" contract every other stand-in path
        already has."""
        mock_deepseek = AsyncMock(side_effect=HTTPException(status_code=401, detail="invalid key"))
        with patch.object(proxy_module, "deepseek_api_key", "k"), \
             patch.object(proxy_module, "call_deepseek_direct", mock_deepseek):
            result = await proxy_module.call_jev_stand_in("typesafe/jev-1.13", {"text": "hi"}, REAL_QUESTIONS, "req1")
        assert result is not None
        status, body, real_model, display_name = result
        assert status == 401
        assert "DeepSeek" in body["error"]["message"]
        assert real_model == "deepseek-flash"


class TestEndToEndRecordingAndRole:
    @pytest.mark.asyncio
    async def test_stand_in_records_speed_under_its_own_real_model_with_decisions_forced_on(self, proxy_module, test_client):
        """The real point of this whole item: a stand-in's Speeds-tab row must be attributed to
        ITS OWN real model, not "typesafe/jev-1.13", and must still show up as a Decisions call --
        both true at once, via extra_roles, never by hoping the model name coincidentally matches
        something in SkyrimNet's own yaml."""
        mock_deepseek = AsyncMock(return_value=('{"intent": "greeting"}', {}))
        with patch.object(proxy_module, "typesafe_api_key", None), \
             patch.object(proxy_module, "deepseek_api_key", "k"), \
             patch.object(proxy_module, "call_deepseek_direct", mock_deepseek):
            r = test_client.post("/v1/chat/completions", json={
                "model": "typesafe/jev-1.13", "questions": REAL_QUESTIONS, "state": {"text": "hi"},
            })
        assert r.status_code == 200
        body = r.json()
        assert body["model"] == "typesafe/jev-1.13"  # the envelope still names the model SkyrimNet asked for
        assert body["answers"]["intent"]["choice"] == "greeting"

        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        row = rows[0]
        assert row["model"] == "deepseek-flash"  # recorded under the REAL model, never "typesafe/..."
        assert row["provider"] == "DeepSeek"
        assert json.loads(row["roles_json"]) == ["Decisions"] or "Decisions" in json.loads(row["roles_json"])

    @pytest.mark.asyncio
    async def test_typesafe_direct_path_also_forces_the_decisions_role(self, proxy_module, test_client):
        """Robustness improvement made alongside this item (not strictly asked for, but the same
        principle): TypeSafe-direct is recorded under req.model itself
        (already matches SkyrimNet's own yaml in the real, live-verified case), but extra_roles now
        forces "Decisions" on regardless, so it no longer silently depends on that coincidence."""
        resp = AsyncMock()
        resp.status = 200
        resp.read = AsyncMock(return_value=b'{"answers":{}}')
        resp.headers = {"Content-Type": "application/json"}
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        session.close = AsyncMock()
        with patch.object(proxy_module, "typesafe_api_key", "ts-key"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "typesafe/jev-1.13", "questions": REAL_QUESTIONS, "state": {"text": "hi"},
            })
        assert r.status_code == 200
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        assert "Decisions" in json.loads(rows[0]["roles_json"])


class TestRecordSpeedExtraRoles:
    @pytest.mark.asyncio
    async def test_extra_roles_are_added_even_with_no_yaml_based_roles(self, proxy_module):
        await proxy_module._record_speed("some-model-nobody-configured", 1.0, True, extra_roles=["Decisions"])
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert json.loads(rows[0]["roles_json"]) == ["Decisions"]

    @pytest.mark.asyncio
    async def test_extra_roles_dont_duplicate_an_existing_role(self, proxy_module):
        with patch.object(proxy_module, "_get_role_chips_cached", lambda: {"m": [{"label": "Decisions", "class": "other", "category": "background"}]}):
            await proxy_module._record_speed("m", 1.0, True, extra_roles=["Decisions"])
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert json.loads(rows[0]["roles_json"]) == ["Decisions"]

    @pytest.mark.asyncio
    async def test_no_extra_roles_is_the_same_as_before(self, proxy_module):
        await proxy_module._record_speed("m", 1.0, True)
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert rows[0]["roles_json"] is None
