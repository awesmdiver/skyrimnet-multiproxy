"""The Claude lane keeps thinking off the way each model allows, and a cut-off answer is retried with more room and named plainly (4.2.1).

Real finding (2026-11-10, the director's log): GameMaster on claude-sonnet-5-5 used exactly its 256-token budget for 0 to 168 characters of answer. Measured on the real lane:
Opus 5.5 and Haiku 5.5 think when `thinking` is left out (Haiku: 256 tokens, 0 characters), Haiku 5.5 takes {"type": "disabled"}, Sonnet 5.5 takes {"type": "between_tools"},
Opus 5.5 refuses both (400), Haiku 4.5 refuses the effort setting. No network here: stand-in sessions only.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import claude_thinking as ct
from tests.test_upstream_retry import _Ctx

TEMPLATE = {"system": [{"type": "text", "text": "billing"}], "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}], "model": "m", "max_tokens": 1,
            "output_config": {"effort": "medium"}}
CAPTURED = {"Authorization": "Bearer sk-FAKE", "anthropic-version": "2023-06-01", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _clean():
    ct.forget_rejected()
    yield
    ct.forget_rejected()


def body_for(model, effort="medium"):
    b = {"output_config": {"effort": effort}} if effort else {}
    return ct.apply(b, model)


def test_the_table_picks_the_right_field_for_each_model():
    assert body_for("claude-sonnet-5-5")["thinking"] == {"type": "between_tools"}
    assert body_for("claude-haiku-5-5")["thinking"] == {"type": "disabled"}
    assert "thinking" not in body_for("claude-opus-5-5")                 # cannot be turned off: nothing is sent
    assert body_for("claude-opus-5")["thinking"] == {"type": "disabled"}
    assert body_for("claude-sonnet-5")["thinking"] == {"type": "disabled"}
    for omit in ("claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-fable-5-2", "claude-mythos-5-2"):
        assert "thinking" not in body_for(omit), omit


def test_an_unknown_or_newer_model_leaves_the_field_out():
    for m in ("claude-sonnet-9", "claude-newthing-1", "", "gpt-5", "claude-sonnet-5-6"):
        assert "thinking" not in body_for(m), m


def test_a_dated_or_prefixed_name_finds_its_model():
    assert body_for("claude-sonnet-5-5-20261101")["thinking"] == {"type": "between_tools"}
    assert body_for("claude/claude-haiku-5-5")["thinking"] == {"type": "disabled"}


def test_the_switch_that_needs_effort_high_or_below_gets_it():
    assert body_for("claude-sonnet-5-5", "xhigh")["output_config"]["effort"] == "high"
    assert body_for("claude-haiku-5-5", "max")["output_config"]["effort"] == "high"
    assert body_for("claude-sonnet-5-5", "low")["output_config"]["effort"] == "low"
    assert body_for("claude-opus-5-5", "xhigh")["output_config"]["effort"] == "xhigh"          # nothing is sent there, so its effort is left as it is


def test_a_model_without_the_effort_setting_does_not_get_it():
    b = body_for("claude-haiku-4-5")
    assert "output_config" not in b and "thinking" not in b


def test_a_leftover_thinking_field_in_the_body_is_replaced_not_kept():
    b = {"thinking": {"type": "enabled", "budget_tokens": 5000}, "output_config": {"effort": "medium"}}
    assert ct.apply(b, "claude-opus-5-5").get("thinking") is None
    assert ct.apply(dict(b), "claude-sonnet-5-5")["thinking"] == {"type": "between_tools"}


def test_the_request_body_the_lane_builds_carries_it(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module.auth, "body_template", json.loads(json.dumps(TEMPLATE)), raising=False)
    b = proxy_module._build_api_body("sys", [{"role": "user", "content": "hi"}], "claude-sonnet-5-5", 256)
    assert b["thinking"] == {"type": "between_tools"} and b["max_tokens"] == 256 and b["output_config"] == {"effort": "medium"}
    b = proxy_module._build_api_body("sys", [{"role": "user", "content": "hi"}], "claude-opus-5-5", 256)
    assert "thinking" not in b


class Session:
    """Answers with the next canned (status, body) each time it is posted to, and records what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sent = []

    def post(self, url, **kw):
        self.sent.append(json.loads(kw["data"]))
        status, body = self.answers.pop(0)
        resp = MagicMock()
        resp.status = status
        resp.headers = {"Content-Type": "application/json"}
        resp.read = AsyncMock(return_value=json.dumps(body).encode())
        return _Ctx(resp=resp)


OK = {"content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 3, "output_tokens": 2}, "stop_reason": "end_turn"}
REFUSED = {"type": "error", "error": {"type": "invalid_request_error", "message": '"thinking.type.between_tools" is not supported for this model.'}}


@pytest.mark.asyncio
async def test_a_400_about_thinking_is_sent_again_once_without_it_and_remembered(proxy_module, monkeypatch):
    s = Session([(400, REFUSED), (200, OK), (200, OK)])
    monkeypatch.setattr(proxy_module.auth, "body_template", json.loads(json.dumps(TEMPLATE)), raising=False)
    monkeypatch.setattr(proxy_module.auth, "headers", dict(CAPTURED), raising=False)
    monkeypatch.setattr(proxy_module.auth, "session", s, raising=False)
    text, usage = await proxy_module.call_api_direct("sys", [{"role": "user", "content": "hi"}], "claude-sonnet-5-5", 256)
    assert text == "OK" and len(s.sent) == 2
    assert s.sent[0]["thinking"] == {"type": "between_tools"} and "thinking" not in s.sent[1]
    await proxy_module.call_api_direct("sys", [{"role": "user", "content": "hi"}], "claude-sonnet-5-5", 256)
    assert len(s.sent) == 3 and "thinking" not in s.sent[2], "remembered: not tried again this session"


@pytest.mark.asyncio
async def test_a_400_that_is_not_about_thinking_is_not_retried(proxy_module, monkeypatch):
    s = Session([(400, {"error": {"message": "max_tokens too large"}}), (200, OK)])
    monkeypatch.setattr(proxy_module.auth, "body_template", json.loads(json.dumps(TEMPLATE)), raising=False)
    monkeypatch.setattr(proxy_module.auth, "headers", dict(CAPTURED), raising=False)
    monkeypatch.setattr(proxy_module.auth, "session", s, raising=False)
    with pytest.raises(HTTPException) as e:
        await proxy_module.call_api_direct("sys", [{"role": "user", "content": "hi"}], "claude-sonnet-5-5", 256)
    assert e.value.status_code == 400 and len(s.sent) == 1


# ---- a cut-off answer -------------------------------------------------------------------------------------------------------------
CUT = {"input_tokens": 3057, "output_tokens": 256, "stop_reason": "max_tokens"}


def test_what_counts_as_cut_off():
    assert ct.is_cut_off("", CUT, 256, wants_json=True)
    assert ct.is_cut_off('{"a": "half of', CUT, 256, wants_json=True)                  # half a JSON object is no answer
    assert ct.is_cut_off("hm", CUT, 256, wants_json=False)                              # almost nothing for 256 tokens: the budget went on thinking
    assert not ct.is_cut_off("x" * 900, CUT, 256, wants_json=False)                     # a long prose line that hit the limit is left as it is
    assert not ct.is_cut_off("{}", {"output_tokens": 4, "stop_reason": "end_turn"}, 256, wants_json=True)
    assert not ct.is_cut_off("{}", {"output_tokens": 256, "stop_reason": "end_turn"}, 256, wants_json=True)       # ended by itself on exactly the limit
    assert not ct.is_cut_off("", None, 256, wants_json=True) and not ct.is_cut_off("", {}, 256, wants_json=True)
    assert ct.bigger_budget(256) == 1024 and ct.bigger_budget(1000) == 2000 and ct.bigger_budget(100000) == ct.MORE_ROOM_CAP


@pytest.mark.asyncio
async def test_a_reply_cut_off_with_no_text_is_asked_again_once_with_more_room_and_then_succeeds(proxy_module):
    asked = []

    async def ask(mt):
        asked.append(mt)
        return '{"ok": true}', {"output_tokens": 40, "stop_reason": "end_turn"}
    text, usage = await proxy_module._recover_cut_off("", dict(CUT), True, ask, 256, "rid", "claude-sonnet-5-5")
    assert asked == [1024] and text == '{"ok": true}' and usage["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_still_cut_off_gives_the_new_message_not_the_broken_json_one(proxy_module):
    async def ask(mt):
        return "", {"output_tokens": mt, "stop_reason": "max_tokens"}
    with pytest.raises(HTTPException) as e:
        await proxy_module._recover_cut_off("", dict(CUT), True, ask, 256, "rid", "claude-sonnet-5-5")
    assert e.value.status_code == 502 and e.value.detail == "The AI ran out of room before it finished its answer."
    assert "broken answer" not in e.value.detail


@pytest.mark.asyncio
async def test_a_normal_reply_is_not_asked_again(proxy_module):
    async def ask(mt):
        raise AssertionError("must not be asked")
    for text, usage in (('{"a": 1}', {"output_tokens": 9, "stop_reason": "end_turn"}), ("hello", None), ("hello", {})):
        assert await proxy_module._recover_cut_off(text, usage, True, ask, 256, "rid", "m") == (text, usage)


@pytest.mark.asyncio
async def test_a_genuinely_broken_json_reply_keeps_the_old_path_and_words(proxy_module):
    """Not cut off (it ended by itself) but invalid JSON: untouched here, so the existing check, retry and wording still apply."""
    async def ask(mt):
        raise AssertionError("must not be asked")
    bad = ('{"a": ', {"output_tokens": 6, "stop_reason": "end_turn"})
    assert await proxy_module._recover_cut_off(bad[0], bad[1], True, ask, 256, "rid", "m") == bad
    src = open(proxy_module.__file__, encoding="utf-8").read()
    assert "The AI gave a broken answer, even after asking again." in src


@pytest.mark.asyncio
async def test_the_log_names_the_whole_budget_being_used(proxy_module, monkeypatch):
    body = {"content": [{"type": "text", "text": "hm"}], "usage": {"input_tokens": 3057, "output_tokens": 256}, "stop_reason": "max_tokens"}
    s = Session([(200, body)])
    log = MagicMock()
    monkeypatch.setattr(proxy_module, "logger", log)
    monkeypatch.setattr(proxy_module.auth, "body_template", json.loads(json.dumps(TEMPLATE)), raising=False)
    monkeypatch.setattr(proxy_module.auth, "headers", dict(CAPTURED), raising=False)
    monkeypatch.setattr(proxy_module.auth, "session", s, raising=False)
    await proxy_module.call_api_direct("sys", [{"role": "user", "content": "hi"}], "claude-sonnet-5-5", 256)
    lines = [c.args[0] for c in log.warning.call_args_list]
    assert any(l.endswith("claude-sonnet-5-5 used its whole 256-token budget (2 characters of answer): the answer was cut off") and l.startswith("[") for l in lines)


def test_the_courier_lane_uses_the_same_table():
    import claude_courier
    src = open(claude_courier.__file__, encoding="utf-8").read()
    assert "claude_thinking.apply(body, model)" in src and 'body.pop("thinking", None)\n        return body' not in src
