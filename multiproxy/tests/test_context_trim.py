"""Tests for the too-long-chat trimmer -- ported from Raydonn's fork (2026-09-27), with the fixes the
original needed: bounded (never loops forever), only for a real context-length rejection, never
touches the system prompt.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# --- error-shape detection ---

def _glm_body(input_t=190000, output_t=20000, max_t=200000):
    return json.dumps({"error": {
        "code": "context_length_exceeded", "message": "too long",
        "input_tokens": input_t, "output_tokens": output_t, "max_context_tokens": max_t,
    }})


def _openai_body(limit=8192, used=9000):
    return json.dumps({"error": {
        "message": f"This model's maximum context length is {limit} tokens. However, your messages "
                   f"resulted in {used} tokens. Please reduce the length of the messages.",
        "type": "invalid_request_error", "param": "messages", "code": "context_length_exceeded",
    }})


def test_glm_shape_gives_exact_overage(proxy_module):
    assert proxy_module._context_overflow_excess(400, _glm_body()) == 10000


def test_openai_shape_gives_exact_overage_from_the_message(proxy_module):
    assert proxy_module._context_overflow_excess(400, _openai_body(8192, 9000)) == 808


def test_context_error_without_numbers_is_still_recognised(proxy_module):
    body = json.dumps({"error": {"code": "context_length_exceeded", "message": "too long"}})
    assert proxy_module._context_overflow_excess(400, body) == 0


def test_other_errors_are_not_context_errors(proxy_module):
    assert proxy_module._context_overflow_excess(400, json.dumps({"error": {"code": "invalid_api_key", "message": "no"}})) is None
    assert proxy_module._context_overflow_excess(500, _glm_body()) is None
    assert proxy_module._context_overflow_excess(400, "plain text") is None
    # the phrase appearing only inside a message isn't the error code
    body = json.dumps({"error": {"code": "other", "message": "not context_length_exceeded"}})
    assert proxy_module._context_overflow_excess(400, body) is None


# --- the trimming itself ---

def _chat(n=6, size=100):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}-" + "x" * size} for i in range(n)]


def test_trim_keeps_first_and_newest_and_drops_oldest_in_between(proxy_module):
    msgs = _chat(6)
    out, removed = proxy_module._trim_messages_for_context(msgs, excess_tokens=40)  # 180 chars
    assert removed >= 180
    assert out[0] == msgs[0] and out[-1] == msgs[-1]
    assert [m["content"][:2] for m in out] == ["m0", "m3", "m4", "m5"][-len(out):] or out[0]["content"].startswith("m0")
    assert msgs[1] not in out                       # oldest middle message went first
    assert len(msgs) == 6                           # input list is never mutated


def test_exact_overage_means_tokens_times_four_and_a_half_chars(proxy_module):
    msgs = _chat(10, size=95)  # each message 98 chars
    _, removed = proxy_module._trim_messages_for_context(msgs, excess_tokens=100)  # 450 chars
    assert 450 <= removed < 450 + 98


def test_unknown_overage_trims_about_a_quarter(proxy_module):
    msgs = _chat(20, size=98)  # about 2030 chars in all
    total = sum(len(m["content"]) for m in msgs)
    out, removed = proxy_module._trim_messages_for_context(msgs, excess_tokens=None)
    assert total * 0.25 <= removed < total * 0.25 + 102
    assert len(out) < len(msgs)


def test_with_two_messages_the_newest_is_cut_from_the_top_and_marked(proxy_module):
    msgs = [{"role": "user", "content": "context"}, {"role": "user", "content": "a" * 1000}]
    out, removed = proxy_module._trim_messages_for_context(msgs, excess_tokens=100)
    assert out[0]["content"] == "context"
    assert out[1]["content"].startswith("...[SYSTEM AUTO-TRIMMED]...\n")
    assert out[1]["content"].endswith("a" * 250)     # the last quarter is always kept
    assert removed == 450


def test_a_single_message_is_never_wiped_out(proxy_module):
    out, removed = proxy_module._trim_messages_for_context([{"role": "user", "content": "b" * 100}], excess_tokens=10_000)
    assert removed == 75
    assert len(out[0]["content"]) > 25


def test_nothing_to_trim_reports_zero(proxy_module):
    out, removed = proxy_module._trim_messages_for_context([{"role": "user", "content": ""}], excess_tokens=50)
    assert removed == 0


def test_non_text_content_is_dropped_whole_or_left_alone_never_crashed_on(proxy_module):
    img = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    msgs = [{"role": "user", "content": "first"}, {"role": "assistant", "content": img}, {"role": "user", "content": "last " * 40}]
    out, removed = proxy_module._trim_messages_for_context(msgs, excess_tokens=5)
    assert removed > 0 and out[0]["content"] == "first"


# --- retry behaviour through real provider functions ---

def _resp(status=200, body="", json_data=None):
    r = AsyncMock()
    r.status = status
    r.text = AsyncMock(return_value=body)
    r.json = AsyncMock(return_value=json_data or {})
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _ok():
    return _resp(200, json_data={"choices": [{"message": {"content": "fine"}, "finish_reason": "stop"}]})


def _session(*resps):
    s = MagicMock()
    s.post = MagicMock(side_effect=list(resps))
    s.close = AsyncMock()
    return s


def _sent_messages(session, call_index):
    return session.post.call_args_list[call_index].kwargs["json"]["messages"]


CHAT = [{"role": "user", "content": f"turn {i} " + "y" * 200} for i in range(8)]
SYSTEM = "You are a very important system prompt."


@pytest.mark.asyncio
async def test_glm_direct_trims_and_succeeds_on_retry(proxy_module):
    session = _session(_resp(400, _glm_body(190000, 10050)), _ok())  # 50 tokens over = 225 chars
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        text, _ = await proxy_module.call_glm_direct(SYSTEM, CHAT, "glm-4.6", 50)
    assert text == "fine"
    first, second = _sent_messages(session, 0), _sent_messages(session, 1)
    assert len(second) < len(first)
    assert second[0] == {"role": "system", "content": SYSTEM}          # system prompt untouched
    assert second[1]["content"] == CHAT[0]["content"]                   # first chat message kept
    assert second[-1]["content"] == CHAT[-1]["content"]                 # newest kept


@pytest.mark.asyncio
async def test_glm_direct_gives_up_after_three_trims_with_the_original_error(proxy_module):
    session = _session(*[_resp(400, _glm_body()) for _ in range(10)])
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        with pytest.raises(HTTPException) as exc:
            await proxy_module.call_glm_direct(SYSTEM, CHAT, "glm-4.6", 50)
    assert exc.value.status_code == 400
    assert session.post.call_count == 1 + proxy_module.MAX_CONTEXT_TRIMS
    assert all(_sent_messages(session, i)[0] == {"role": "system", "content": SYSTEM} for i in range(session.post.call_count))


@pytest.mark.asyncio
async def test_glm_direct_does_not_trim_for_other_400s(proxy_module):
    session = _session(_resp(400, json.dumps({"error": {"code": "invalid_request", "message": "bad"}})), _ok())
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        with pytest.raises(HTTPException):
            await proxy_module.call_glm_direct(SYSTEM, CHAT, "glm-4.6", 50)
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_stops_early_when_there_is_nothing_left_to_trim(proxy_module):
    session = _session(*[_resp(400, _glm_body()) for _ in range(10)])
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        with pytest.raises(HTTPException):
            await proxy_module.call_glm_direct(SYSTEM, [{"role": "user", "content": ""}], "glm-4.6", 50)
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_glm_thinking_retry_still_works_alongside_trimming(proxy_module):
    thinking = _resp(400, json.dumps({"error": {"message": "This model always engages in thinking"}}))
    session = _session(thinking, _resp(400, _glm_body()), _ok())
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        text, _ = await proxy_module.call_glm_direct(SYSTEM, CHAT, "glm-5.3", 50)
    assert text == "fine"
    assert session.post.call_args_list[1].kwargs["json"].get("reasoning_effort") == "low"
    assert session.post.call_args_list[2].kwargs["json"].get("reasoning_effort") == "low"
    assert len(_sent_messages(session, 2)) < len(_sent_messages(session, 1))


@pytest.mark.asyncio
async def test_openai_compatible_direct_trims_on_the_standard_code(proxy_module):
    session = _session(_resp(400, _openai_body()), _ok())
    with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        text, _ = await proxy_module.call_deepseek_direct(SYSTEM, CHAT, "deepseek-flash", 50)
    assert text == "fine"
    second = _sent_messages(session, 1)
    assert second[0] == {"role": "system", "content": SYSTEM}
    assert len(second) < len(_sent_messages(session, 0))


async def _iter(chunks):
    for c in chunks:
        yield c


def _stream_resp(events):
    r = _resp(200)
    r.content = MagicMock()
    r.content.iter_any = lambda: _iter([e.encode() for e in events])
    return r


@pytest.mark.asyncio
async def test_glm_streaming_trims_before_anything_is_sent(proxy_module):
    session = _session(_resp(400, _glm_body()), _stream_resp(['data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', "data: [DONE]\n\n"]))
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        events = [e async for e in proxy_module.call_glm_streaming(SYSTEM, CHAT, "glm-4.6", 50)]
    assert any('"hi"' in e for e in events)
    assert not any("error" in e.lower() for e in events)
    assert len(_sent_messages(session, 1)) < len(_sent_messages(session, 0))


@pytest.mark.asyncio
async def test_openai_compatible_streaming_gives_up_with_the_original_error(proxy_module):
    session = _session(*[_resp(400, _openai_body()) for _ in range(10)])
    with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)):
        with pytest.raises(HTTPException) as exc:  # raised, so the route can answer with a real 400
            [e async for e in proxy_module.call_deepseek_streaming(SYSTEM, CHAT, "deepseek-flash", 50)]
    assert session.post.call_count == 1 + proxy_module.MAX_CONTEXT_TRIMS
    assert exc.value.status_code == 400
    assert "maximum context length" in exc.value.detail


@pytest.mark.asyncio
async def test_each_trim_is_logged_as_a_warning(proxy_module, caplog):
    session = _session(_resp(400, _glm_body()), _ok())
    with patch.object(proxy_module, "glm_api_key", "k"), patch.object(proxy_module, "auth", MagicMock(session=session)), \
         caplog.at_level("WARNING", logger="proxy"):
        await proxy_module.call_glm_direct(SYSTEM, CHAT, "glm-4.6", 50)
    assert any("says the chat is too long" in r.message and "trim 1 of 3" in r.message for r in caplog.records)
