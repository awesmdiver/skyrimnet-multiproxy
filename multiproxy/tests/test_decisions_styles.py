"""SkyrimNet Beta 26 rc2's API Style (2026-10-02): the Decisions and System One styles send the same {model, questions, state} body to the Endpoint the player
typed, which is no longer necessarily /v1/chat/completions. Every likely path, and any other path for a decisions-shaped body, reaches the same Jev handling
(TypeSafe with a saved key, a stand-in AI without one), and the answer is byte-for-byte what it was on the rc1 route. A fake TypeSafe stands in for the real one:
no real key is ever used or stored."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient as AioClient, TestServer

from tests.test_jev_provider import REAL_JEV_ANSWER_BODY, REAL_JEV_REQUEST, _make_mock_response, _make_mock_session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import decisions_capture_standin as cap  # noqa: E402

FAKE_KEY = "ts-test-key"
PATHS = ["/v1/chat/completions", "/v1/systemone", "/v1/decisions", "/systemone", "/decisions", "/alpha/decisions", "/api/alpha/decisions"]
UNLISTED = ["/v1/some/new/path", "/api/v2/decide", "/anything"]


def with_typesafe(proxy_module, resp=None):
    resp = resp or _make_mock_response(200, REAL_JEV_ANSWER_BODY)
    session = _make_mock_session(resp)
    return session, [patch.object(proxy_module, "typesafe_api_key", FAKE_KEY), patch.object(proxy_module, "auth", MagicMock(session=None)),
                     patch("aiohttp.ClientSession", return_value=session)]


@pytest.mark.parametrize("path", PATHS + UNLISTED)
def test_every_decisions_path_reaches_typesafe_with_a_saved_key_and_answers_byte_for_byte(proxy_module, test_client, path):
    session, patches = with_typesafe(proxy_module)
    for p in patches:
        p.start()
    try:
        r = test_client.post(path, json=REAL_JEV_REQUEST)
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200 and r.content == REAL_JEV_ANSWER_BODY, "TypeSafe's answer reaches SkyrimNet completely unchanged"
    assert session.post.call_args.args[0] == proxy_module.TYPESAFE_BASE_URL
    assert session.post.call_args.kwargs["json"]["questions"] == REAL_JEV_REQUEST["questions"]


@pytest.mark.parametrize("path", PATHS + UNLISTED)
def test_without_a_typesafe_key_another_ai_stands_in_on_every_path(proxy_module, test_client, path):
    mock_deepseek = AsyncMock(return_value=('{"intent": "greeting"}', {}))
    with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", mock_deepseek):
        r = test_client.post(path, json=REAL_JEV_REQUEST)
    assert r.status_code == 200 and r.json()["answers"]["intent"]["choice"] == "greeting" and set(r.json()) == {"model", "answers", "usage"}
    mock_deepseek.assert_awaited_once()


@pytest.mark.parametrize("path", ["/v1/systemone", "/v1/decisions", "/api/alpha/decisions", "/v1/some/new/path"])
def test_a_typesafe_error_keeps_its_status_on_the_new_paths_too(proxy_module, test_client, path):
    session, patches = with_typesafe(proxy_module, _make_mock_response(429, b'{"error": "slow down"}'))
    for p in patches:
        p.start()
    try:
        r = test_client.post(path, json=REAL_JEV_REQUEST)
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 429 and r.json() == {"error": "slow down"}


def test_nothing_ready_is_the_same_clear_error_on_the_new_paths(proxy_module, test_client):
    r = test_client.post("/v1/systemone", json=REAL_JEV_REQUEST)
    assert r.status_code == 500 and "TypeSafe" in r.json()["error"]["message"]


def test_the_state_may_be_text_or_an_object_and_a_bare_body_without_a_model_still_works(proxy_module, test_client):
    body = {"questions": REAL_JEV_REQUEST["questions"], "state": "Hello there"}
    mock = AsyncMock(return_value=('{"intent": "greeting"}', {}))
    with patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", mock):
        r = test_client.post("/v1/systemone", json=body)
    assert r.status_code == 200 and r.json()["model"].startswith("typesafe/")


@pytest.mark.parametrize("path", PATHS[1:])
def test_a_chat_request_on_a_decisions_path_is_refused_plainly_never_guessed_at(proxy_module, test_client, path):
    r = test_client.post(path, json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400 and "isn't a decisions request" in r.json()["error"]["message"]
    assert test_client.post(path, content=b"not json").status_code == 400


def test_other_unknown_posts_are_still_a_plain_404_and_chat_still_goes_to_chat(proxy_module, test_client):
    assert test_client.post("/v1/unknown", json={"hello": 1}).status_code == 404
    assert test_client.post("/v1/unknown", json={"messages": [{"role": "user", "content": "x"}], "questions": {"a": {}}}).status_code == 404
    assert test_client.post("/v1/chat/completions", json={"model": "claude-sonnet-5-5"}).status_code == 422, "a chat request with no messages is still refused as before"
    assert test_client.get("/v1/unknown").status_code in (404, 405)


def test_the_rc1_request_through_the_old_route_is_unchanged(proxy_module, test_client):
    session, patches = with_typesafe(proxy_module)
    for p in patches:
        p.start()
    try:
        r = test_client.post("/v1/chat/completions", json=REAL_JEV_REQUEST)
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200 and r.content == REAL_JEV_ANSWER_BODY
    assert session.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"


# ---- the recording stand-in ------------------------------------------------------------------------------------------------------------
@pytest_asyncio.fixture
async def recorder(tmp_path):
    rec = cap.Recorder(tmp_path / "rec", "0.26.0 (test)")
    client = AioClient(TestServer(cap.make_app(rec)))
    await client.start_server()
    yield rec, client
    await client.close()


@pytest.mark.asyncio
async def test_the_stand_in_records_each_style_masks_the_key_and_keeps_text_short(recorder):
    rec, c = recorder
    secret = "sk-or-v1-THIS-IS-A-FAKE-TEST-KEY-0123456789abcdef"
    long_text = "x" * 600
    await c.post("/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": long_text}], "stream": False}, headers={"Authorization": f"Bearer {secret}"})
    r = await c.post("/decisions", json=REAL_JEV_REQUEST, headers={"Authorization": f"Bearer {secret}"})
    answer = await r.json()
    assert answer["answers"]["intent"]["choice"] in REAL_JEV_REQUEST["questions"]["intent"]["criteria"] and set(answer) == {"model", "answers", "usage"}
    await c.post("/systemone", json={**REAL_JEV_REQUEST, "api_key": secret})
    files = sorted(rec.out.glob("[0-9][0-9][0-9]-*.json"))
    assert len(files) == 3 and "SKYRIMNET-VERSION.txt" in {p.name for p in rec.out.iterdir()}
    everything = " ".join(p.read_text(encoding="utf-8") for p in files)
    assert secret not in everything and "THIS-IS-A-FAKE" not in everything, "a key is never saved"
    first = json.loads(files[0].read_text(encoding="utf-8"))
    assert first["path"] == "/chat/completions" and first["headers"]["Authorization"].startswith("<Bearer, ") and "not saved" in first["headers"]["Authorization"]
    assert "+380 characters" in first["body"]["json"]["messages"][0]["content"], "long text is cut short"
    third = json.loads(files[2].read_text(encoding="utf-8"))
    assert third["path"] == "/systemone" and third["body"]["json"]["api_key"] == "<not saved>"


@pytest.mark.asyncio
async def test_the_stand_in_answers_chat_in_both_shapes_and_models_and_notes_unknown_requests(recorder):
    rec, c = recorder
    r = await c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert (await r.json())["choices"][0]["message"]["content"] == "Understood."
    r = await c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "stream": True})
    text = await r.text()
    assert text.startswith("data: ") and text.rstrip().endswith("data: [DONE]")
    r = await c.get("/v1/models")
    assert (await r.json())["data"][0]["id"] == "capture-model"
    r = await c.post("/somewhere", json={"other": 1})
    assert r.status == 404 and len(list(rec.out.glob("[0-9][0-9][0-9]-*.json"))) == 4
