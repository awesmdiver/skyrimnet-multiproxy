"""Tests for "Catch broken AI replies: repair them, retry once, and count them" (queue:
catch-broken-replies, 2026-09-29).

Step 1 (required before building this) found, from SkyrimNet's own real captured logs
(openrouter_input*.log, 1656 real requests across six files): zero occurrences anywhere of
response_format/json_schema/a use_structured_outputs flag -- the only real signal a JSON-expecting
job gives is its own prompt text explicitly asking for JSON ("Respond ONLY with a JSON object",
"Return raw JSON only", "JSON only:", etc.). Every real JSON-expecting job sampled said so this way;
no confirmed prose job ever mentioned "json" at all. Real examples below are drawn from that same
log set (see TECHNICAL.md for the full counts) -- the fenced-reply example is the single biggest
real repairable case found (memory: 262 of 263 real replies sampled came back wrapped in a ```json
fence), and the "broken" example is the one genuinely unparseable real reply found (a literal,
unescaped newline inside a JSON string value -- not any of the three safe repairs this feature
performs, so it's correctly reported broken rather than guessed at).
"""
import json
import sqlite3

import pytest


def _speed_rows(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM speed_history ORDER BY ts")]


# The real "memory" job's own prompt tail (trimmed) -- a genuine JSON-expecting signal.
JSON_SYSTEM_PROMPT = (
    "You are an AI assistant that helps create memories for NPCs in Skyrim. "
    "Respond ONLY with a JSON object:\n```json\n"
    '{"content": "...", "location": "...", "emotion": "...", "importance_score": 0.0, '
    '"tags": [], "type": "EXPERIENCE"}\n```'
)

# Real (memory: req_dba9ef3d2fd36661_1a0cb52d173) reply shape, trimmed -- fenced, otherwise valid.
REAL_FENCED_REPLY = (
    '```json\n{\n  "content": "Wanda has been busy tonight at the Bannered Mare.",\n'
    '  "location": "Bannered Mare",\n  "emotion": "neutral",\n  "importance_score": 0.2,\n'
    '  "tags": ["wanda"],\n  "type": "EXPERIENCE"\n}\n```'
)

# The one real genuinely-broken reply found (memory: req_b587db076d898a90_1a0ec022614) -- a fenced
# JSON object whose own "content" string contains a literal, unescaped newline. Even after the fence
# is stripped, this is not valid JSON (json.loads correctly rejects a raw control character inside a
# string) -- and none of the three safe repairs (fence/trim/trailing-comma) can fix that without
# guessing at what the model meant, so it stays "broken".
REAL_BROKEN_REPLY = (
    '```json\n{\n"content": "I sit on a bench in the Bannered Mare while Mikael drones on.\n'
    '",\n"location": "The Bannered Mare, Hold: Whiterun",\n"emotion": "neutral",\n'
    '"importance_score": 0.15,\n"tags": ["bannered-mare"],\n"type": "EXPERIENCE"\n}\n```'
)

VALID_JSON_REPLY = '{"content": "A quiet evening.", "location": "Whiterun", "emotion": "neutral", "importance_score": 0.1, "tags": [], "type": "EXPERIENCE"}'


class TestExpectsJsonReply:
    def test_real_memory_prompt_is_detected(self, proxy_module):
        assert proxy_module._expects_json_reply(JSON_SYSTEM_PROMPT, []) is True

    def test_real_agencyengine_phrasing_is_detected(self, proxy_module):
        assert proxy_module._expects_json_reply(
            "Return **only** a JSON object -- no prose, no markdown, no fence.", []
        ) is True

    def test_real_intel_story_dm_phrasing_is_detected(self, proxy_module):
        assert proxy_module._expects_json_reply(
            "Your ENTIRE response must be a single JSON object -- nothing before it, nothing after.", []
        ) is True

    def test_json_only_colon_phrasing_is_detected(self, proxy_module):
        assert proxy_module._expects_json_reply("Return each entry once. JSON only:", []) is True

    def test_plain_prose_prompt_is_not_detected(self, proxy_module):
        text = "You are Lydia, a stalwart Nord housecarl. Stay in character."
        assert proxy_module._expects_json_reply(text, []) is False

    def test_real_negative_no_json_warning_does_not_false_positive(self, proxy_module):
        """Real sever_background variant: explicitly tells the model NOT to use JSON, in a
        sentence that also contains the word "ONLY" a few words earlier -- a naive proximity check
        without the sentence-boundary rule would wrongly flag this."""
        text = (
            "Write 1-2 sentences from a narrator's perspective. Output ONLY the narrative text. "
            "No JSON, no explanation, no quotes around the whole thing."
        )
        assert proxy_module._expects_json_reply(text, []) is False

    def test_checks_message_content_too_not_just_system_prompt(self, proxy_module):
        messages = [{"role": "user", "content": "Respond ONLY with a JSON object: {...}"}]
        assert proxy_module._expects_json_reply(None, messages) is True

    def test_multimodal_message_content_is_flattened_first(self, proxy_module):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Return raw JSON only. No markdown."},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]}]
        assert proxy_module._expects_json_reply(None, messages) is True


class TestRepairJsonReply:
    def test_valid_json_is_reported_valid_unchanged(self, proxy_module):
        status, text, applied = proxy_module._repair_json_reply(VALID_JSON_REPLY)
        assert status == "valid"
        assert text == VALID_JSON_REPLY
        assert applied == []

    def test_real_fenced_reply_is_repaired_by_stripping_the_fence(self, proxy_module):
        status, text, applied = proxy_module._repair_json_reply(REAL_FENCED_REPLY)
        assert status == "repaired"
        assert applied == ["removed code fences"]
        assert json.loads(text)["location"] == "Bannered Mare"

    def test_text_before_and_after_the_json_is_trimmed(self, proxy_module):
        reply = 'Sure, here you go:\n{"a": 1}\nHope that helps!'
        status, text, applied = proxy_module._repair_json_reply(reply)
        assert status == "repaired"
        assert applied == ["removed text before/after the JSON"]
        assert json.loads(text) == {"a": 1}

    def test_trailing_commas_are_stripped(self, proxy_module):
        reply = '{"a": 1, "b": [1, 2,], }'
        status, text, applied = proxy_module._repair_json_reply(reply)
        assert status == "repaired"
        assert "removed trailing commas" in applied
        assert json.loads(text) == {"a": 1, "b": [1, 2]}

    def test_fence_and_trailing_comma_together_both_get_applied(self, proxy_module):
        reply = '```json\n{"a": 1,}\n```'
        status, text, applied = proxy_module._repair_json_reply(reply)
        assert status == "repaired"
        assert applied == ["removed code fences", "removed trailing commas"]
        assert json.loads(text) == {"a": 1}

    def test_real_broken_reply_is_reported_broken_not_guessed_at(self, proxy_module):
        """The one real unrepairable reply found in step 1 -- a raw, unescaped newline inside a
        JSON string value. None of the three safe repairs can fix this without inventing content,
        so it must come back "broken", never silently mangled into something that merely parses."""
        status, text, applied = proxy_module._repair_json_reply(REAL_BROKEN_REPLY)
        assert status == "broken"
        assert text == REAL_BROKEN_REPLY  # returned completely unmodified

    def test_empty_reply_is_broken(self, proxy_module):
        status, _text, _applied = proxy_module._repair_json_reply("")
        assert status == "broken"

    def test_never_touches_a_valid_reply_even_if_it_looks_fixable(self, proxy_module):
        """A reply that's already valid JSON, even one containing a literal "```json" substring
        inside a string value, must be returned byte-for-byte -- the "valid" check always runs
        first and short-circuits everything else."""
        reply = '{"note": "the example used ```json fences"}'
        status, text, applied = proxy_module._repair_json_reply(reply)
        assert status == "valid"
        assert text == reply
        assert applied == []


def _post_json(test_client, proxy_module, stream=False, model=None):
    return test_client.post("/v1/chat/completions", json={
        "model": model or (proxy_module.GLM_PREFIX + "glm-4.6"), "stream": stream,
        "messages": [
            {"role": "system", "content": JSON_SYSTEM_PROMPT},
            {"role": "user", "content": "Generate a memory for what just happened."},
        ],
    })


def _patch_glm_direct_sequence(monkeypatch, proxy_module, replies):
    """Each successive call returns the next reply in the list (stays on the last one after)."""
    calls = {"n": 0}

    async def direct(system_prompt, messages, model, max_tokens, **extra):
        idx = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return replies[idx], {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}

    monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
    return calls


class TestNonStreamingEndToEnd:
    def test_valid_json_passes_through_unchanged_and_is_recorded_fine(self, test_client, proxy_module, monkeypatch):
        _patch_glm_direct_sequence(monkeypatch, proxy_module, [VALID_JSON_REPLY])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == VALID_JSON_REPLY
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        assert rows[0]["success"] == 1
        assert rows[0]["reply_repair"] == "fine"

    def test_fenced_reply_is_fixed_and_recorded_repaired(self, test_client, proxy_module, monkeypatch):
        calls = _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_FENCED_REPLY])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert json.loads(content)["location"] == "Bannered Mare"  # fence is gone, still valid JSON
        assert calls["n"] == 1  # a repairable reply never needs a retry
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        assert rows[0]["reply_repair"] == "repaired"

    def test_a_tidied_reply_prints_nothing_at_info_but_is_still_counted(self, test_client, proxy_module, monkeypatch, caplog):
        import logging
        _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_FENCED_REPLY])
        with caplog.at_level(logging.DEBUG, logger=proxy_module.logger.name):
            assert _post_json(test_client, proxy_module).status_code == 200
        fixed = [r for r in caplog.records if "fixed it" in r.getMessage()]
        assert fixed and all(r.levelno == logging.DEBUG for r in fixed), "normal behaviour: kept for debugging, never shown at INFO"
        assert _speed_rows(proxy_module.SPEED_HISTORY_DB)[0]["reply_repair"] == "repaired"

    def test_real_problems_still_print(self, test_client, proxy_module, monkeypatch, caplog):
        import logging
        _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_BROKEN_REPLY, REAL_BROKEN_REPLY])
        with caplog.at_level(logging.DEBUG, logger=proxy_module.logger.name):
            assert _post_json(test_client, proxy_module).status_code == 502
        said = {r.getMessage().split("-- ", 1)[-1][:30]: r.levelno for r in caplog.records if "reply" in r.getMessage() and "JSON" in r.getMessage()}
        assert any(lvl == logging.INFO and "asked again" in k for k, lvl in said.items())
        assert any(lvl == logging.WARNING and "still wasn't valid JSON after asking again" in r.getMessage() for r in caplog.records for lvl in [r.levelno])

    def test_broken_reply_is_retried_and_the_retry_succeeding_is_used(self, test_client, proxy_module, monkeypatch):
        calls = _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_BROKEN_REPLY, VALID_JSON_REPLY])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == VALID_JSON_REPLY
        assert calls["n"] == 2  # the original attempt, plus exactly one retry
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1
        assert rows[0]["success"] == 1
        assert rows[0]["reply_repair"] == "asked_again"

    def test_broken_reply_whose_retry_also_fails_becomes_a_real_error(self, test_client, proxy_module, monkeypatch):
        calls = _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_BROKEN_REPLY, REAL_BROKEN_REPLY])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 502  # SkyrimNet's own fallback/smart-routing must see a real error
        assert calls["n"] == 2
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1  # one row for the whole request, not two
        assert rows[0]["success"] == 0
        assert rows[0]["fail_reason"] == "broken reply"

    def test_setting_off_passes_a_broken_reply_through_untouched_but_still_counts_it(
        self, test_client, proxy_module, monkeypatch,
    ):
        """The setting's own contract: no repair, no retry, no error -- SkyrimNet gets the exact
        200 and text it always would have. What "still counted" means concretely: one extra,
        accounting-only failed row alongside the real (untouched) success."""
        monkeypatch.setattr(proxy_module, "FIX_BROKEN_REPLIES", False)
        calls = _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_FENCED_REPLY, "should never be called"])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 200
        # Untouched -- still fenced, not the repaired version.
        assert resp.json()["choices"][0]["message"]["content"] == REAL_FENCED_REPLY
        assert calls["n"] == 1  # never retried
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 2
        by_success = {r["success"]: r for r in rows}
        assert by_success[1]["reply_repair"] is None
        assert by_success[0]["fail_reason"] == "broken reply"

    def test_setting_off_still_passes_through_a_genuinely_broken_reply(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "FIX_BROKEN_REPLIES", False)
        _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_BROKEN_REPLY])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 200  # never a 502 with the setting off
        assert resp.json()["choices"][0]["message"]["content"] == REAL_BROKEN_REPLY

    def test_setting_off_leaves_a_valid_reply_completely_alone(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "FIX_BROKEN_REPLIES", False)
        _patch_glm_direct_sequence(monkeypatch, proxy_module, [VALID_JSON_REPLY])
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == VALID_JSON_REPLY
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert len(rows) == 1  # a genuinely valid reply never gets the extra accounting row
        assert rows[0]["reply_repair"] == "fine"

    def test_prose_reply_is_never_touched_by_the_json_repair_path(self, test_client, proxy_module, monkeypatch):
        """An ordinary dialogue request (no JSON directive anywhere in its prompt) must never enter
        the JSON-repair path at all -- it keeps going through the existing tag fixer exactly as
        before, and is untouched here since there's no markup to fix."""

        async def direct(system_prompt, messages, model, max_tokens, **extra):
            return "Just a normal line of dialogue.", {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}

        monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
        resp = test_client.post("/v1/chat/completions", json={
            "model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "Just a normal line of dialogue."
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert rows[0]["reply_repair"] == "fine"

    def test_prose_reply_with_a_real_markup_bug_is_counted_repaired_too(self, test_client, proxy_module, monkeypatch):
        """"The tag fixer's markup repairs count as repaired too" -- same reply_repair value the
        JSON path uses, on the same speed_history row, for the SAME reason (something needed fixing
        before SkyrimNet could use it)."""

        async def direct(system_prompt, messages, model, max_tokens, **extra):
            return "<internal thought>Hm.</internal thought> Ha.", {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"}

        monkeypatch.setattr(proxy_module, "call_glm_direct", direct)
        resp = test_client.post("/v1/chat/completions", json={
            "model": proxy_module.GLM_PREFIX + "glm-4.6", "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "<internal_thought>Hm.</internal_thought> Ha."
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert rows[0]["reply_repair"] == "repaired"

    def test_no_time_left_skips_the_retry_and_fails_immediately(self, test_client, proxy_module, monkeypatch):
        """"as long as there's clearly time left" -- once the budget is already used up, a broken
        reply goes straight to the real error without spending more time asking again."""
        calls = _patch_glm_direct_sequence(monkeypatch, proxy_module, [REAL_BROKEN_REPLY, VALID_JSON_REPLY])
        monkeypatch.setattr(proxy_module, "_JSON_RETRY_BUDGET_SECONDS", 0.0)
        resp = _post_json(test_client, proxy_module)
        assert resp.status_code == 502
        assert calls["n"] == 1  # never even tried the retry


class TestStreamingJsonHold:
    """Confirmed live (step 1): 0 of 345 real captured streaming requests ever asked for JSON --
    built to spec anyway, since a future job could. These prove the hold-and-check-at-the-end
    mechanics work, not that this path sees real traffic today."""

    def _patch_glm_streaming_sequence(self, monkeypatch, proxy_module, chunk_lists):
        calls = {"n": 0}

        async def streaming(system_prompt, messages, model, max_tokens, **extra):
            idx = min(calls["n"], len(chunk_lists) - 1)
            calls["n"] += 1
            for c in chunk_lists[idx]:
                yield c

        monkeypatch.setattr(proxy_module, "call_glm_streaming", streaming)
        return calls

    @staticmethod
    def _sse(content):
        obj = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}
        return f"data: {json.dumps(obj)}\n\n"

    def test_fenced_reply_split_across_chunks_is_held_and_repaired_into_one_reply(
        self, test_client, proxy_module, monkeypatch,
    ):
        chunks = [self._sse(REAL_FENCED_REPLY[:20]), self._sse(REAL_FENCED_REPLY[20:]), "data: [DONE]\n\n"]
        calls = self._patch_glm_streaming_sequence(monkeypatch, proxy_module, [chunks])
        resp = _post_json(test_client, proxy_module, stream=True)
        assert resp.status_code == 200
        events = [e + "\n\n" for e in resp.text.split("\n\n") if e]
        text_events = [e for e in events if e.startswith("data: {")]
        payload = json.loads(text_events[0].strip()[6:])
        content = payload["choices"][0]["delta"]["content"]
        assert json.loads(content)["location"] == "Bannered Mare"
        assert events[-1].strip() == "data: [DONE]"
        assert calls["n"] == 1
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert rows[0]["reply_repair"] == "repaired"

    def test_broken_stream_is_retried_and_the_retry_succeeding_is_used(self, test_client, proxy_module, monkeypatch):
        broken_chunks = [self._sse(REAL_BROKEN_REPLY), "data: [DONE]\n\n"]
        ok_chunks = [self._sse(VALID_JSON_REPLY), "data: [DONE]\n\n"]
        calls = self._patch_glm_streaming_sequence(monkeypatch, proxy_module, [broken_chunks, ok_chunks])
        resp = _post_json(test_client, proxy_module, stream=True)
        assert resp.status_code == 200
        assert calls["n"] == 2
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert rows[0]["reply_repair"] == "asked_again"

    def test_broken_stream_whose_retry_also_fails_is_a_real_error_with_nothing_sent_yet(
        self, test_client, proxy_module, monkeypatch,
    ):
        broken_chunks = [self._sse(REAL_BROKEN_REPLY), "data: [DONE]\n\n"]
        self._patch_glm_streaming_sequence(monkeypatch, proxy_module, [broken_chunks, broken_chunks])
        resp = _post_json(test_client, proxy_module, stream=True)
        assert resp.status_code == 502
        rows = _speed_rows(proxy_module.SPEED_HISTORY_DB)
        assert rows[0]["success"] == 0
        assert rows[0]["fail_reason"] == "broken reply"


class TestRepairLineDashboard:
    def test_none_when_nothing_to_report(self, proxy_module):
        rows = [(1.0, 0.5, 1, None, None, None, None, "GLM", "fine")]
        assert proxy_module._repair_line(rows) is None

    def test_fixed_asked_again_and_still_broken_all_shown_together(self, proxy_module):
        rows = [
            (1.0, 0.5, 1, None, None, None, None, "GLM", "repaired"),
            (2.0, 0.5, 1, None, None, None, None, "GLM", "repaired"),
            (3.0, 0.5, 1, None, None, None, None, "GLM", "repaired"),
            (4.0, 0.5, 1, None, None, None, None, "GLM", "asked_again"),
            (5.0, 0.5, 0, None, None, None, "broken reply", "GLM", None),
        ]
        assert proxy_module._repair_line(rows) == "3 broken replies fixed · 1 asked again · 1 still broken"

    def test_uses_the_whole_window_not_just_the_recent_strip(self, proxy_module):
        """Unlike _failure_line (last40 only), this answers "does this ever happen for this
        model", so it must see rows outside the recent-activity strip too."""
        rows = [(float(i), 0.5, 1, None, None, None, None, "GLM", "repaired") for i in range(50)]
        assert proxy_module._repair_line(rows) == "50 broken replies fixed"

    def test_singular_wording_for_exactly_one(self, proxy_module):
        rows = [(1.0, 0.5, 1, None, None, None, None, "GLM", "repaired")]
        assert proxy_module._repair_line(rows) == "1 broken reply fixed"


def _seed(db, model, ts, elapsed, success=True, *, fail_reason=None, provider=None, reply_repair=None):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO speed_history (ts, model, elapsed, success, fail_reason, provider, reply_repair) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, model, elapsed, 1 if success else 0, fail_reason, provider, reply_repair),
        )
        conn.commit()


class TestRepairLineThroughTheEndpoint:
    def test_the_by_model_card_carries_the_repair_line(self, proxy_module, test_client):
        import time
        db = proxy_module.SPEED_HISTORY_DB
        now = time.time()
        _seed(db, "glm-4.6", now - 100, 1.0, provider="GLM", reply_repair="repaired")
        _seed(db, "glm-4.6", now - 90, 1.0, provider="GLM", reply_repair="repaired")
        _seed(db, "glm-4.6", now - 80, 1.0, success=False, provider="GLM", fail_reason="broken reply")
        models = test_client.get("/stats/speeds?range=all").json()["models"]
        by = {m["model"]: m for m in models}
        assert by["glm-4.6"]["v2"]["repair_line"] == "2 broken replies fixed · 1 still broken"

    def test_no_repair_line_when_nothing_ever_needed_fixing(self, proxy_module, test_client):
        import time
        db = proxy_module.SPEED_HISTORY_DB
        _seed(db, "claude-sonnet-5", time.time() - 10, 1.0, provider="Claude", reply_repair="fine")
        models = test_client.get("/stats/speeds?range=all").json()["models"]
        by = {m["model"]: m for m in models}
        assert by["claude-sonnet-5"]["v2"]["repair_line"] is None
