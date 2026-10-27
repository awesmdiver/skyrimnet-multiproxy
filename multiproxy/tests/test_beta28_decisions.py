"""Two things SkyrimNet Beta 28 changes for the decisions job and for prompt caching (2026-10-20). Stand-ins only.

1. The Decision Gate (off by default) sends ONE EXTRA decisions request per player line, started early and taking 0.2 to 0.4 s. Two decisions back to back must both be answered
   by the local decision model inside its waiting limit and both must be on the Speeds tab.
2. Prompt caching (Beta 27 final) lives on the front of the prompt: providers cache the system prompt and the first turns. Every place MultiProxy changes a request body is
   listed in the handoff; the ones that touch messages are proved here to send the front of the prompt byte for byte the same from one call to the next.
"""
import asyncio
import json
import sqlite3
import time

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

from tests.decision_helpers import installed_engine
from tests.fixtures.courier_fakes import free_port

STATE = "Lydia drew her sword and fought two wolves outside Whiterun. The last wolf has just died and the road is quiet."
GATE = {"memory": {"type": "noul", "instructions": "Does this line need a memory of the past to be answered?"}}
MAIN = {"intent": {"type": "choice", "instructions": "What does the player want?", "criteria": {"greeting": "says hello", "question": "asks", "request": "asks for help"}},
        "combat": {"type": "noul", "instructions": "Is combat still going on?"}}


@pytest_asyncio.fixture
async def local_decisions(proxy_module, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "0.3")                    # the Decision Gate's own figure: 0.2 to 0.4 s
    engine, settings, _ = await installed_engine(tmp_path, slots=4)
    monkeypatch.setattr(proxy_module.decision_handles, "engine", engine)
    monkeypatch.setattr(proxy_module.decision_handles, "_told", "")
    monkeypatch.setattr(proxy_module, "typesafe_api_key", None)
    monkeypatch.setattr(proxy_module, "nano_api_key", None)
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    yield type("L", (), {"engine": engine, "client": client, "settings": settings})()
    await client.aclose()
    await engine.close()


def decision(questions):
    return {"model": "typesafe/jev-latest", "state": STATE, "questions": questions}


async def timed(client, body, path="/v1/systemone"):
    t0 = time.monotonic()
    r = await client.post(path, json=body)
    return r, time.monotonic() - t0


def decision_rows(pm):
    import speed_store
    speed_store.flush()
    with sqlite3.connect(pm.SPEED_HISTORY_DB) as conn:
        return conn.execute("SELECT model, success, provider, roles_json FROM speed_history WHERE model = 'local/decisions'").fetchall()


@pytest.mark.asyncio
async def test_a_decision_and_its_gate_request_back_to_back_are_both_answered_inside_the_limit_and_both_are_on_speeds(proxy_module, local_decisions):
    limit = proxy_module.decision_handles.engine  # the engine decides its own limit from where it runs
    (gate, t_gate), (main, t_main) = await asyncio.gather(timed(local_decisions.client, decision(GATE)), timed(local_decisions.client, decision(MAIN)))
    assert gate.status_code == 200 and set(gate.json()["answers"]) == {"memory"}
    assert main.status_code == 200 and set(main.json()["answers"]) == {"intent", "combat"}
    from decision_engine import limit_for
    budget = limit_for("gpu", local_decisions.settings.get("limit_seconds") or 0)
    assert t_gate < budget and t_main < budget, f"gate {t_gate:.2f} s, decision {t_main:.2f} s, limit {budget} s"
    st = local_decisions.engine.status()
    assert st["answered"] == 2 and st["passedOn"] == 0 and st["waiting"] == 0, "neither was passed on to a paid AI, nothing is left waiting"
    rows = decision_rows(proxy_module)
    assert len(rows) == 2 and all(r[1] == 1 and r[2] == "Local decision model" and "Decisions" in (r[3] or "") for r in rows), rows


@pytest.mark.asyncio
async def test_six_player_lines_in_a_row_each_with_its_gate_request_are_all_answered_by_the_local_model(proxy_module, local_decisions):
    local_decisions.settings["limit_seconds"] = 1.5          # (twelve answers in a row on a busy test machine: the default 0.8 s is proved by the test above)
    results = []
    for _ in range(6):
        results.append(await asyncio.gather(timed(local_decisions.client, decision(GATE)), timed(local_decisions.client, decision(MAIN))))
    from decision_engine import limit_for
    budget = limit_for("gpu", local_decisions.settings.get("limit_seconds") or 0)
    assert all(r.status_code == 200 for pair in results for r, _t in pair)
    assert max(t for pair in results for _r, t in pair) < budget
    assert local_decisions.engine.status()["answered"] == 12 and local_decisions.engine.status()["passedOn"] == 0
    assert len(decision_rows(proxy_module)) == 12


# ---- the front of the prompt ----

class Recorder:
    """An AI on 127.0.0.1 that remembers the messages it was sent, as the exact text that arrived."""

    def __init__(self):
        self.port = free_port()
        self.sent: list = []
        self.runner = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        app.router.add_post("/chat/completions", self.chat)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        await self.runner.cleanup()

    async def chat(self, request):
        body = await request.json()
        self.sent.append(json.dumps(body["messages"], sort_keys=True, ensure_ascii=False))
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'ok there'}}]})}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok there"}, "finish_reason": "stop"}], "usage": {}})


SYSTEM = "You are Lydia, a housecarl in Whiterun. <|emotion:fear|> Speak plainly.\nSecond line of the persona, with an accent: é."
# a spelling the tag fixer would change if it were looking at requests (it only fixes replies)
HISTORY_ASSISTANT = "[emotion-fear] Stay close to me."


def conversation(n: int) -> list:
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Hello Lydia."}, {"role": "assistant", "content": HISTORY_ASSISTANT}]
    for i in range(n):
        msgs += [{"role": "user", "content": f"Turn {i}: where are we going?"}, {"role": "assistant", "content": f"Turn {i} answer."}]
    return msgs + [{"role": "user", "content": "And now?"}]


ROUTES = {                                   # model name -> (what to point at the recorder)
    "lmstudio/m": ("lmstudio_base_url", None),
    "ollama/m": ("OLLAMA_LOCAL_BASE_URL", None),
    "openai/m": ("OPENAI_BASE_URL", "openai_api_key"),
    "deepseek/m": ("DEEPSEEK_BASE_URL", "deepseek_api_key"),
    "gemini/m": ("GEMINI_BASE_URL", "gemini_api_key"),
    "xai/m": ("XAI_BASE_URL", "xai_api_key"),
    "kilo/m": ("KILO_BASE_URL", "kilo_api_key"),
    "glmzai/m": ("GLM_BASE_URL", "glm_api_key"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("model", list(ROUTES))
@pytest.mark.parametrize("stream", [False, True])
async def test_the_front_of_the_prompt_is_the_same_bytes_on_every_call(proxy_module, monkeypatch, model, stream):
    rec = await Recorder().start()
    base_attr, key_attr = ROUTES[model]
    monkeypatch.setattr(proxy_module, base_attr, rec.base)
    if key_attr:
        monkeypatch.setattr(proxy_module, key_attr, "FAKE-KEY")
    if model.startswith("ollama"):
        monkeypatch.setattr(proxy_module, "ollama_api_key", None)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    try:
        varying = [{}, {"temperature": 0.3, "multiproxy": {"x": 1}}, {"reasoning": {"enabled": False}, "cache": True, "provider": {"order": ["x"]}}, {"temperature": 1.1, "max_tokens": 50}]
        for i, extra in enumerate(varying):
            body = {"model": model, "stream": stream, "messages": conversation(i), **extra}
            r = await client.post("/v1/chat/completions", json=body)
            assert r.status_code == 200, (model, r.text)
    finally:
        await client.aclose()
        await rec.stop()
    assert len(rec.sent) == 4
    first = [json.loads(s) for s in rec.sent]
    for sent in first:
        assert sent[0] == {"role": "system", "content": SYSTEM}, "the system prompt arrived exactly as SkyrimNet wrote it"
        assert sent[1] == {"role": "user", "content": "Hello Lydia."}
        assert sent[2] == {"role": "assistant", "content": HISTORY_ASSISTANT}, "history is never reworded (the tag fixer only fixes replies)"
    prefixes = {json.dumps(sent[:3], sort_keys=True, ensure_ascii=False) for sent in first}
    assert len(prefixes) == 1, "the first three messages are the same text on every call"
    for earlier, later in zip(first, first[1:]):
        assert later[:len(earlier) - 1] == earlier[:-1], "each call starts with all of the previous call's messages, up to its last one"


@pytest.mark.asyncio
async def test_a_chat_that_starts_with_the_npc_and_two_user_turns_in_a_row_are_changed_the_same_way_every_time(proxy_module, monkeypatch):
    """The two places the message list is reshaped: an assistant message first gets `Continue.` put before it, and two messages from the same side in a row are joined with a
    blank line. Both depend only on the messages themselves, so a repeated start gives repeated bytes."""
    rec = await Recorder().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", rec.base)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    try:
        for tail in ("One.", "Two.", "Three."):
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "assistant", "content": "Hail."}, {"role": "user", "content": "A"}, {"role": "user", "content": "B"},
                    {"role": "assistant", "content": tail}, {"role": "user", "content": "C"}]
            assert (await client.post("/v1/chat/completions", json={"model": "lmstudio/m", "messages": msgs})).status_code == 200
    finally:
        await client.aclose()
        await rec.stop()
    sent = [json.loads(s) for s in rec.sent]
    assert sent[0][:4] == [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Continue."}, {"role": "assistant", "content": "Hail."},
                           {"role": "user", "content": "A\n\nB"}]
    assert len({json.dumps(s[:4], sort_keys=True) for s in sent}) == 1


@pytest.mark.asyncio
async def test_two_system_messages_are_both_kept_in_order_and_the_first_is_still_the_front(proxy_module, monkeypatch):
    """Found while listing the changes (2026-10-20): a request with two system messages used to keep only the last. The caching plugin of Beta 27 may split the prompt that way."""
    rec = await Recorder().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", rec.base)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    try:
        msgs = [{"role": "system", "content": "FIXED PART."}, {"role": "system", "content": "Changing part 7."}, {"role": "user", "content": "Hi"}]
        assert (await client.post("/v1/chat/completions", json={"model": "lmstudio/m", "messages": msgs})).status_code == 200
    finally:
        await client.aclose()
        await rec.stop()
    assert json.loads(rec.sent[0])[0] == {"role": "system", "content": "FIXED PART.\n\nChanging part 7."}
