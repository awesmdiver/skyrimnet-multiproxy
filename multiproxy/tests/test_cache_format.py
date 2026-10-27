"""Claude through a subscription, with SkyrimNet's prompt-caching message format (2026-10-21). Stand-ins only.

The report: since SkyrimNet Beta 27's "Cache Enabled" prompts, every request to a Claude model through a subscription failed with
`messages.14.content.0.text.text: Input should be a valid string`. The cause: the Claude lane wrapped a message's content, when it was a LIST of parts, straight into
`{"type": "text", "text": <the list>}`. The stand-in Anthropic below checks a request the way the real one does (every text a string, no empty text block, at most four
cache markers, roles alternating, first message from the user) and answers 400 with the same words when it is not.
"""
import copy
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import claude_courier as cc

MARK = {"type": "ephemeral"}


# --- a stand-in Anthropic that validates like the real one --------------------------------------------------------------------------------------------------------
def problems_in(body: dict):
    """The first thing the real API would refuse in this body, said the way it says it, or None."""
    markers = 0
    for i, block in enumerate(body.get("system") or []):
        if not isinstance(block.get("text"), str):
            return f"system.{i}.text: Input should be a valid string"
        if not block["text"].strip():
            return f"system.{i}.text: text content blocks must be non-empty"
        markers += "cache_control" in block
    msgs = body.get("messages") or []
    if not msgs:
        return "messages: at least one message is required"
    if msgs[0]["role"] != "user":
        return "messages.0.role: the first message must use the user role"
    for i, m in enumerate(msgs):
        if i and m["role"] == msgs[i - 1]["role"]:
            return f"messages.{i}.role: roles must alternate between user and assistant"
        content = m.get("content")
        if not isinstance(content, list) or not content:
            return f"messages.{i}.content: must be a non-empty list"
        for j, b in enumerate(content):
            if b.get("type") != "text":
                return f"messages.{i}.content.{j}.type: only text blocks here"
            if not isinstance(b.get("text"), str):
                return f"messages.{i}.content.{j}.text.text: Input should be a valid string"
            if not b["text"].strip():
                return f"messages.{i}.content.{j}.text: text content blocks must contain non-whitespace text"
            if "cache_control" in b:
                markers += 1
                if b["cache_control"].get("type") != "ephemeral":
                    return f"messages.{i}.content.{j}.cache_control.type: must be ephemeral"
    if markers > 4:
        return f"A maximum of 4 blocks with cache_control may be provided. Found {markers}."
    return None


SSE = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n\n'
       b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello there"}}\n\n'
       b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n')


class FakeAnthropic:
    """Stands in for aiohttp.ClientSession: records every body sent and answers like Anthropic would."""

    def __init__(self):
        self.bodies = []

    def post(self, url, data=None, headers=None, **kw):
        body = json.loads(data if data is not None else json.dumps(kw.get("json")))
        self.bodies.append(body)
        problem = problems_in(body)
        resp = MagicMock()
        if problem:
            payload = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": problem}}).encode()
            resp.status = 400
            resp.headers = {"Content-Type": "application/json"}
        else:
            payload = SSE
            resp.status = 200
            resp.headers = {"Content-Type": "text/event-stream"}
        resp.read = AsyncMock(return_value=payload)

        async def iter_any():
            yield payload
        resp.content = MagicMock()
        resp.content.iter_any = iter_any
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def close(self):
        pass


@pytest.fixture()
def claude(proxy_module, test_client, monkeypatch):
    monkeypatch.setattr(proxy_module.auth, "headers", {"Authorization": "Bearer t"})
    monkeypatch.setattr(proxy_module.auth, "body_template", {"system": [{"type": "text", "text": "billing block"}], "messages": []})
    monkeypatch.setattr(proxy_module.auth, "session", None)

    async def _ready(*, force=False, timeout=60.0):
        return

    monkeypatch.setattr(proxy_module.auth, "ensure_ready", _ready)
    api = FakeAnthropic()

    def ask(messages, stream=False, model="claude-sonnet-5"):
        with patch("aiohttp.ClientSession", return_value=api):
            return test_client.post("/v1/chat/completions", json={"model": model, "messages": messages, "stream": stream})
    return api, ask


def part(text, marker=False):
    p = {"type": "text", "text": text}
    if marker:
        p["cache_control"] = dict(MARK)
    return p


# Every odd content shape the caching format might use, as the content of one user message.
SHAPES = {
    "plain string": "plain words",
    "list of text parts": [part("one"), part("two")],
    "marker on a text part": [part("marked", True)],
    "marker on the last of several": [part("a"), part("b"), part("c", True)],
    "a part given as a dict": {"type": "text", "text": "a dict, not a list"},
    "a dict part with a marker": {"type": "text", "text": "marked dict", "cache_control": dict(MARK)},
    "a bare string in the list": ["bare", part("then a part")],
    "a null text": [{"type": "text", "text": None}, part("kept")],
    "a missing text": [{"type": "text"}, part("kept")],
    "an empty text": [part(""), part("kept")],
    "a text that is a list of parts": [{"type": "text", "text": [part("inner one"), part("inner two")], "cache_control": dict(MARK)}],
    "a text that is a dict": [{"type": "text", "text": {"type": "text", "text": "inner dict"}}],
    "a part with no type": [{"text": "no type given"}],
    "a number as the text": [{"type": "text", "text": 42}],
    "a marker with a one hour life": [{"type": "text", "text": "long", "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
}
# These shapes are SkyrimNet's caching format as the report describes it; the rest are every other way the same field can go wrong.
BROKEN_BEFORE = {"list of text parts", "marker on a text part", "marker on the last of several", "a bare string in the list", "a null text", "a missing text",
                 "an empty text", "a text that is a list of parts", "a text that is a dict", "a part with no type", "a number as the text", "a marker with a one hour life"}


def conversation(shape, position, length=24, consecutive=False):
    """A chat of `length` alternating messages with `shape` as the content of the message at `position`; `consecutive` repeats the user message right after it."""
    msgs = []
    for i in range(length):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"line {i}"})
    msgs[position]["content"] = copy.deepcopy(SHAPES[shape])
    if consecutive:
        msgs.insert(position + 1, {"role": msgs[position]["role"], "content": copy.deepcopy(SHAPES[shape])})
    return [{"role": "system", "content": "You are Lydia."}] + msgs


@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_shape_is_accepted_by_a_validating_anthropic(claude, shape, stream):
    api, ask = claude
    for position in (0, 3, 14, 22):
        for consecutive in (False, True):
            api.bodies.clear()
            r = ask(conversation(shape, position, consecutive=consecutive), stream=stream)
            assert r.status_code == 200, (shape, position, consecutive, r.text[:300])
            assert problems_in(api.bodies[-1]) is None


def test_the_exact_request_of_the_report_is_accepted(claude):
    """Message 14, content 0: a user message sent as a list of parts with a marker, in a long chat (the report's error named exactly that place)."""
    api, ask = claude
    msgs = [{"role": "system", "content": [part("You are Lydia.", True)]}]
    for i in range(20):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": [part(f"line {i}", i in (13, 14, 15))]})
    r = ask(msgs, stream=True)
    assert r.status_code == 200, r.text[:300]
    body = api.bodies[-1]
    assert body["messages"][14]["content"][0]["text"] == "line 14" and body["messages"][14]["content"][0]["cache_control"] == MARK


def test_the_plain_string_front_of_the_prompt_is_exactly_as_before(claude):
    api, ask = claude
    ask([{"role": "system", "content": "SYS"}, {"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}, {"role": "user", "content": "Again"}])
    body = api.bodies[-1]
    assert body["system"] == [{"type": "text", "text": "billing block"}, {"type": "text", "text": "SYS"}]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}, {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]},
                                {"role": "user", "content": [{"type": "text", "text": "Again"}]}]


def test_markers_are_kept_where_they_were(claude):
    api, ask = claude
    ask([{"role": "system", "content": [part("SYS", True)]}, {"role": "user", "content": [part("a"), part("b", True)]}, {"role": "assistant", "content": "ok"},
         {"role": "user", "content": [part("c", True)]}])
    body = api.bodies[-1]
    assert body["system"][-1].get("cache_control") == MARK, "the system text's marker is kept"
    assert [b.get("cache_control") for b in body["messages"][0]["content"]] == [None, MARK]
    assert body["messages"][2]["content"][0].get("cache_control") == MARK


def test_markers_survive_two_messages_being_merged(claude):
    api, ask = claude
    ask([{"role": "user", "content": [part("first", True)]}, {"role": "user", "content": [part("second", True)]}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "q"}])
    body = api.bodies[-1]
    assert [b["text"] for b in body["messages"][0]["content"]] == ["first", "second"]
    assert all(b.get("cache_control") == MARK for b in body["messages"][0]["content"])


def test_at_most_four_markers_go_and_they_are_the_last_four(claude, caplog):
    api, ask = claude
    msgs = [{"role": "system", "content": [part("SYS", True)]}]
    for i in range(10):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": [part(f"line {i}", True)]})
    with caplog.at_level(logging.INFO, logger="proxy"):
        r = ask(msgs)
    assert r.status_code == 200, r.text[:300]
    body = api.bodies[-1]
    marked = [b["text"] for b in (body["system"] + [b for m in body["messages"] for b in m["content"]]) if "cache_control" in b]
    assert marked == ["line 6", "line 7", "line 8", "line 9"]
    assert any("[cache]" in rec.getMessage() and "kept 4 of 11 cache markers" in rec.getMessage() for rec in caplog.records)


def test_the_templates_own_markers_count_towards_the_four(claude, proxy_module, monkeypatch):
    api, ask = claude
    monkeypatch.setattr(proxy_module.auth, "body_template", {"system": [{"type": "text", "text": "billing block", "cache_control": dict(MARK)}],
                                                             "messages": [{"role": "user", "content": [{"type": "text", "text": "<system-reminder>r</system-reminder>", "cache_control": dict(MARK)}]}]})
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": [part(f"line {i}", True)]} for i in range(6)]
    r = ask(msgs)
    assert r.status_code == 200, r.text[:300]
    body = api.bodies[-1]
    count = sum("cache_control" in b for b in body["system"]) + sum("cache_control" in b for m in body["messages"] for b in m["content"])
    assert count == 4


def test_a_log_line_says_what_was_repaired_and_never_the_prompt(claude, caplog):
    api, ask = claude
    secret = "THE-PROMPT-TEXT-NOBODY-SHOULD-SEE-IN-A-LOG"
    with caplog.at_level(logging.INFO, logger="proxy"):
        ask([{"role": "user", "content": [{"type": "text", "text": [part(secret)]}]}])
    lines = [rec.getMessage() for rec in caplog.records if "[cache]" in rec.getMessage()]
    assert lines and "fixed" in lines[0]
    assert not any(secret in rec.getMessage() for rec in caplog.records)


def test_the_front_of_the_prompt_is_the_same_bytes_call_to_call(claude):
    api, ask = claude

    def chat(tail):
        return [{"role": "system", "content": [part("SYS fixed", True)]}, {"role": "user", "content": [part("scene", True), {"type": "text", "text": [part("more")]}]},
                {"role": "assistant", "content": [part("ok")]}, {"role": "user", "content": [part(tail)]}]
    ask(chat("one"))
    ask(chat("two"))
    a, b = api.bodies[-2], api.bodies[-1]
    assert json.dumps(a["system"]) == json.dumps(b["system"])
    assert json.dumps(a["messages"][:-1]) == json.dumps(b["messages"][:-1])


def test_an_empty_message_is_left_out_and_its_neighbours_join(claude):
    api, ask = claude
    r = ask([{"role": "user", "content": "a"}, {"role": "assistant", "content": [{"type": "text", "text": ""}]}, {"role": "user", "content": "b"}])
    assert r.status_code == 200
    assert [m["role"] for m in api.bodies[-1]["messages"]] == ["user"]
    assert [b["text"] for b in api.bodies[-1]["messages"][0]["content"]] == ["a\n\nb"] or [b["text"] for b in api.bodies[-1]["messages"][0]["content"]] == ["a", "b"]


# --- the other lanes ----------------------------------------------------------------------------------------------------------------------------------------------
def _mock_response(text='{"choices":[{"message":{"content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'):
    resp = AsyncMock()
    resp.status = 200
    resp.text = AsyncMock(return_value=text)
    resp.read = AsyncMock(return_value=text.encode())
    resp.json = AsyncMock(return_value=json.loads(text))
    resp.headers = {"Content-Type": "application/json"}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _sent_by(proxy_module, test_client, model, key_attr, messages):
    session = MagicMock()
    session.post = MagicMock(return_value=_mock_response())
    session.close = AsyncMock()
    with patch.object(proxy_module, key_attr, "test-key"), patch.object(proxy_module, "auth", MagicMock(session=None)), patch("aiohttp.ClientSession", return_value=session):
        r = test_client.post("/v1/chat/completions", json={"model": model, "messages": messages})
    assert r.status_code == 200, r.text[:300]
    return session.post.call_args.kwargs["json"]["messages"]


def _all_parts(messages):
    return [p for m in messages if isinstance(m["content"], list) for p in m["content"]]


CACHING_CHAT = [{"role": "system", "content": "SYS"}] + [
    {"role": "user" if i % 2 == 0 else "assistant", "content": [part(f"line {i}", True)] if i != 4 else [{"type": "text", "text": [part("nested")], "cache_control": dict(MARK)}]}
    for i in range(8)]


@pytest.mark.parametrize("model,key_attr", [("glmzai/glm-4.6", "glm_api_key"), ("openai/gpt-4o-mini", "openai_api_key"), ("deepseek/deepseek-flash", "deepseek_api_key"),
                                            ("gemini/gemini-3.8-flash", "gemini_api_key")], ids=["glm", "openai", "deepseek", "gemini"])
def test_providers_that_do_not_use_markers_get_plain_text_parts_only(proxy_module, test_client, model, key_attr):
    sent = _sent_by(proxy_module, test_client, model, key_attr, copy.deepcopy(CACHING_CHAT))
    parts = _all_parts(sent)
    assert parts and all(isinstance(p["text"], str) and "cache_control" not in p for p in parts)


@pytest.mark.parametrize("model,key_attr", [("kilo/anthropic/claude-sonnet-5", "kilo_api_key"), ("nano/claude-sonnet-5", "nano_api_key")], ids=["kilo", "nano"])
def test_gateways_that_pass_markers_on_keep_the_last_four(proxy_module, test_client, model, key_attr):
    sent = _sent_by(proxy_module, test_client, model, key_attr, copy.deepcopy(CACHING_CHAT))
    parts = _all_parts(sent)
    assert all(isinstance(p["text"], str) for p in parts)
    assert [p["text"] for p in parts if "cache_control" in p] == ["nested", "line 5", "line 6", "line 7"]


def test_the_cli_lanes_get_text_from_every_shape(proxy_module):
    for shape, content in SHAPES.items():
        assert isinstance(proxy_module._content_as_text(copy.deepcopy(content)), str), shape


def test_the_chat_can_be_trimmed_when_its_content_is_a_list(proxy_module):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": [part("x" * 400, True)]} for i in range(10)]
    new, removed = proxy_module._trim_messages_for_context(msgs, 100)
    assert removed > 0 and len(new) < len(msgs) and new[0] == msgs[0] and new[-1] == msgs[-1]


# --- the merge itself ---------------------------------------------------------------------------------------------------------------------------------------------
class TestMerge:
    def test_strings_still_join_with_a_blank_line(self, proxy_module):
        assert proxy_module._merge_message_content("a", "b") == "a\n\nb"

    @pytest.mark.parametrize("a", list(SHAPES))
    @pytest.mark.parametrize("b", ["plain", [part("p", True)], {"type": "text", "text": "d"}, None])
    def test_no_pairing_leaves_a_text_that_is_not_a_string(self, proxy_module, a, b):
        merged = proxy_module._merge_message_content(copy.deepcopy(SHAPES[a]) if not isinstance(SHAPES[a], str) else SHAPES[a], copy.deepcopy(b))
        if isinstance(merged, list):
            assert all(isinstance(p.get("text"), str) for p in merged if p.get("type") == "text")


# --- the Courier's own Claude (it builds its own body) -----------------------------------------------------------------------------------------------------------------
def test_the_couriers_claude_builds_the_same_clean_body():
    host = cc.ClaudeHost.__new__(cc.ClaudeHost)
    host._template = {"system": [{"type": "text", "text": "billing block"}], "messages": []}
    host.log = logging.getLogger("test-cache-courier")
    for shape in SHAPES:
        body = host.build_body(conversation(shape, 3, consecutive=True), "claude-sonnet-5", 100)
        assert problems_in(body) is None, shape
    msgs = [{"role": "system", "content": [part("SYS", True)]}] + [{"role": "user" if i % 2 == 0 else "assistant", "content": [part(f"l{i}", True)]} for i in range(8)]
    body = host.build_body(msgs, "claude-sonnet-5", 100)
    assert problems_in(body) is None
    assert sum("cache_control" in b for b in body["system"]) + sum("cache_control" in b for m in body["messages"] for b in m["content"]) == 4
