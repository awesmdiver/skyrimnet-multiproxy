"""A local model that cannot load tells you why, and does not leave NPCs silent (2026-10-07).

The real case: a Dialogue model on Ollama could not load (the engine ran out of memory starting it). Ollama said so only after nearly three minutes, SkyrimNet had hung up
long before, and no page said why. The second case: a model answered with nothing, MultiProxy logged "ok", and the NPCs stayed silent.

Every engine here is a stand-in server on this PC's loopback (never the real Ollama that may be running on it).
"""
import asyncio
import json
import re
import socket
import sqlite3
import threading
import time

import pytest
from aiohttp import web

import engine_trouble
import overview
import speed_queries
import speed_store

MEMORY_BODY = json.dumps({"error": "llama runner process has terminated: Gemma4Assistant requires ctx_other to be set ... failed to allocate buffer of size 285212672 "
                                    "for C:\\Users\\someone\\.ollama\\models\\blobs\\sha256-abc: unable to allocate CUDA_Host buffer"})
FRIENDLY_MEMORY = "Ollama couldn't load this model because it ran out of memory."
CLAUSE_MEMORY = "couldn't load (ran out of memory while starting)"
SAYS_NOTHING = "It answered with nothing."


# ---------------------------------------------------------------------------------------------------------------------------------
# a stand-in engine
# ---------------------------------------------------------------------------------------------------------------------------------

class StandIn:
    """A tiny OpenAI-shaped engine on the loopback. `modes` maps a model name to how it behaves: ok, slow, never, memory (a 500 after `delay`), empty, thinks, missing."""

    def __init__(self, modes: dict, delay: float = 0.0):
        self.modes, self.delay = modes, delay
        self.calls: list = []
        self.cancelled: list = []
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self._ready = threading.Event()
        self._loop = None
        self._stop = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        assert self._ready.wait(10)
        return self

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(10)

    def _run(self):
        async def main():
            self._stop = asyncio.Event()
            self._loop = asyncio.get_running_loop()
            app = web.Application()
            app.router.add_post("/v1/chat/completions", self._chat)
            runner = web.AppRunner(app, handler_cancellation=True)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", self.port).start()
            self._ready.set()
            await self._stop.wait()
            await runner.cleanup()
        asyncio.run(main())

    async def _chat(self, request: web.Request):
        body = await request.json()
        model, stream = body.get("model"), bool(body.get("stream"))
        mode = self.modes.get(model, "ok")
        self.calls.append((model, mode, stream, body.get("max_tokens")))
        try:
            if mode == "never":
                await asyncio.sleep(60)
            if mode == "slow":
                await asyncio.sleep(self.delay)
            if mode == "memory":
                await asyncio.sleep(self.delay)
                return web.Response(status=500, text=MEMORY_BODY, content_type="application/json")
            if mode == "missing":
                return web.Response(status=404, text=json.dumps({"error": f"model '{model}' not found, try pulling it first"}), content_type="application/json")
        except asyncio.CancelledError:
            self.cancelled.append(model)
            raise
        words = {"ok": "Well met, traveller.", "slow": "Well met, traveller."}.get(mode, "")
        if not stream:
            msg = {"role": "assistant", "content": words}
            if mode == "thinks":
                msg["reasoning_content"] = "I should greet them."
            return web.json_response({"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 4}})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)

        async def send(delta, finish=None):
            await resp.write(("data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode())
        await send({"role": "assistant", "content": ""})
        if mode == "thinks":
            await send({"reasoning_content": "I should greet them."})
        if words:
            await send({"content": words})
        await send({}, "stop")
        await resp.write(b"data: [DONE]\n\n")
        return resp


@pytest.fixture()
def engine(proxy_module, monkeypatch):
    made = []
    rest = proxy_module._routing_engine.rest                    # (three failures in a row rest a model: that memory is module-wide, so each test starts without it)
    for book in (rest._fails, rest._until, rest._why):
        book.clear()

    def make(modes, delay=0.0):
        e = StandIn(modes, delay).start()
        made.append(e)
        monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", e.url)
        monkeypatch.setattr(proxy_module, "ollama_api_key", None)
        return e
    yield make
    for e in made:
        e.stop()


@pytest.fixture()
def tracking(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    return proxy_module


def rows(proxy_module):
    speed_store.flush()
    with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
        return conn.execute("SELECT model, success, fail_reason, fail_note, provider FROM speed_history ORDER BY ts").fetchall()


def chat(client, model, stream, **extra):
    return client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "Hello there"}], "stream": stream, "max_tokens": 50, **extra})


def route(client, ais, job="dialogue"):
    r = client.post("/config/routing", json={"routes": {job: {"ais": ais, "how": "order"}}})
    assert r.status_code == 200


# ---------------------------------------------------------------------------------------------------------------------------------
# reading an engine's own failure
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,body,clause", [
    (500, MEMORY_BODY, CLAUSE_MEMORY),
    (500, '{"error":"cudaMalloc failed: out of memory"}', CLAUSE_MEMORY),
    (500, '{"error":"model requires more system memory (24.5 GiB) than is available (9.1 GiB)"}', CLAUSE_MEMORY),
    (500, '{"error":"model failed to load, this may be due to resource limitations or an incompatible model"}', "couldn't load (it may have run out of memory)"),
    (500, '{"error":"model does not fit in VRAM"}', "couldn't load (too big for the graphics card)"),
    (500, '{"error":"llama runner process has terminated: exit status 2"}', "couldn't load (it stopped while starting)"),
    (404, '{"error":"model \'gemma4:26b\' not found, try pulling it first"}', "doesn't have this model"),
    (400, '{"error":"No models loaded. Please load a model in the developer page"}', "has no model loaded"),
    (503, '{"error":"model is still loading"}', "is still loading this model"),
    (500, '{"error":"something nobody has seen before"}', "couldn't answer (its own log may say why)"),
])
def test_an_engines_failure_becomes_one_plain_clause(status, body, clause):
    t = engine_trouble.explain("Ollama", status, body)
    assert t is not None and t.clause == clause
    assert t.sentence.startswith("Ollama ")


def test_a_plain_sentence_never_carries_a_path_a_size_a_code_or_the_engines_words():
    for status, body in [(500, MEMORY_BODY), (404, '{"error":"model \'x\' not found"}'), (500, "weird C:\\Users\\a\\b.gguf 12345 0xdeadbeef")]:
        t = engine_trouble.explain("Ollama", status, body)
        text = t.sentence + " " + t.clause
        assert not re.search(r"\d|[\\/]|0x|sha256|blob|runner|buffer|CUDA|ctx_other|Gemma", text, re.I), text


def test_a_request_error_that_is_not_the_engines_fault_gets_no_sentence():
    assert engine_trouble.explain("Ollama", 400, '{"error":"bad field"}') is None
    assert engine_trouble.explain("Ollama", 422, "") is None


@pytest.mark.parametrize("text,empty", [("", True), ("   \n\t", True), ("<internal_thought></internal_thought>", True), ("[laughs]", True), ("<think>", True),
                                          ("Hello.", False), ("<b>Hi</b>", False), ("*", True), (None, True)])
def test_what_counts_as_an_empty_reply(text, empty):
    assert engine_trouble.says_nothing(text) is empty


def test_a_json_reply_is_empty_only_when_blank():
    assert engine_trouble.says_nothing("{}", structured=True) is False
    assert engine_trouble.says_nothing("  ", structured=True) is True


# ---------------------------------------------------------------------------------------------------------------------------------
# a model that cannot load
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [True, False])
def test_a_model_that_cannot_load_says_why_and_keeps_the_reason(tracking, test_client, engine, stream):
    e = engine({"gemma4:26b": "memory"}, delay=0.3)
    r = chat(test_client, "ollama/gemma4:26b", stream)
    assert r.status_code == 500
    message = r.json()["error"]["message"]
    assert message == FRIENDLY_MEMORY
    assert not re.search(r"\d|[\\/]|buffer|CUDA|ctx_other", message), "no path, size or raw error text"
    assert rows(tracking) == [("gemma4:26b", 0, "server", CLAUSE_MEMORY, "Ollama")]
    assert tracking._trouble.clause("Ollama::gemma4:26b") == CLAUSE_MEMORY
    assert [c[1] for c in e.calls] == ["memory"]


def test_a_model_that_answers_clears_what_was_wrong_with_it(tracking, test_client, engine):
    engine({"gemma4:26b": "ok"})
    tracking._trouble.set("Ollama::gemma4:26b", CLAUSE_MEMORY)
    assert chat(test_client, "ollama/gemma4:26b", True).status_code == 200
    assert tracking._trouble.clause("Ollama::gemma4:26b") is None


def test_a_model_the_engine_does_not_have_says_so(tracking, test_client, engine):
    engine({"nope": "missing"})
    r = chat(test_client, "ollama/nope", False)
    assert r.status_code == 404 and r.json()["error"]["message"] == "Ollama doesn't have this model."
    assert rows(tracking)[0][3] == "doesn't have this model"


def test_an_engine_that_is_not_running_says_so(tracking, test_client, proxy_module, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(proxy_module, "OLLAMA_LOCAL_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setattr(proxy_module, "ollama_api_key", None)
    r = chat(test_client, "ollama/anything", False)
    assert r.status_code == 502
    assert rows(proxy_module)[0][3] == "isn't running"


# ---------------------------------------------------------------------------------------------------------------------------------
# a request that never starts is given up on
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [True, False])
def test_a_request_that_never_starts_is_abandoned_at_the_engine_and_recorded(tracking, test_client, engine, monkeypatch, stream):
    e = engine({"slowpoke": "never"})
    monkeypatch.setattr(engine_trouble, "FIRST_WORD_S", 0.6)
    monkeypatch.setattr(engine_trouble, "WHOLE_REPLY_S", 0.6)
    t0 = time.time()
    r = chat(test_client, "ollama/slowpoke", stream)
    assert time.time() - t0 < 5
    assert r.status_code == 504 and "took too long to answer" in r.json()["error"]["message"]
    time.sleep(0.4)
    assert e.cancelled == ["slowpoke"], "the engine's request was cancelled, so its card is not kept busy"
    assert rows(tracking) == [("slowpoke", 0, "timeout", "took too long to start answering", "Ollama")]


def test_a_slow_start_inside_the_time_is_fine(tracking, test_client, engine, monkeypatch):
    engine({"m": "slow"}, delay=0.3)
    monkeypatch.setattr(engine_trouble, "FIRST_WORD_S", 3.0)
    assert chat(test_client, "ollama/m", True).status_code == 200
    assert rows(tracking)[0][1] == 1


def test_a_cloud_model_keeps_its_own_limits(proxy_module, monkeypatch):
    assert proxy_module._first_word_budget("Gemini") is None and proxy_module._first_word_budget("Claude") is None
    monkeypatch.setattr(proxy_module, "ollama_api_key", "k")
    assert proxy_module._first_word_budget("Ollama") is None, "Ollama Cloud is not a local engine"
    monkeypatch.setattr(proxy_module, "ollama_api_key", None)
    assert proxy_module._first_word_budget("Ollama") == engine_trouble.FIRST_WORD_S
    assert proxy_module._first_word_budget("LM Studio", whole_reply=True) == engine_trouble.WHOLE_REPLY_S
    assert engine_trouble.FIRST_WORD_S <= engine_trouble.SKYRIMNET_PATIENCE_S and engine_trouble.WHOLE_REPLY_S <= engine_trouble.SKYRIMNET_PATIENCE_S


def test_the_routes_own_limit_is_how_long_an_engine_gets(tracking, test_client, engine, monkeypatch):
    e = engine({"slowpoke": "never", "good": "ok"})
    monkeypatch.setattr(engine_trouble, "FIRST_WORD_S", 30.0)            # (the default would wait far longer than the route's 3 seconds)
    r = test_client.post("/config/routing", json={"routes": {"dialogue": {"ais": ["ollama/slowpoke", "ollama/good"], "how": "order", "slow_limit_s": 3}}})
    assert r.status_code == 200
    t0 = time.time()
    got = chat(test_client, "job/dialogue", True)
    assert got.status_code == 200 and "Well met, traveller." in got.text and time.time() - t0 < 6
    time.sleep(0.4)
    assert e.cancelled == ["slowpoke"]
    assert rows(tracking)[0] == ("slowpoke", 0, "timeout", "took too long to start answering", "Ollama"), "a failure with its reason, not plain slowness"


# ---------------------------------------------------------------------------------------------------------------------------------
# the backup answers
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("bad", ["memory", "empty", "missing"])
def test_the_backup_answers_when_the_first_choice_fails(tracking, test_client, engine, stream, bad):
    e = engine({"bad": bad, "good": "ok"})
    route(test_client, ["ollama/bad", "ollama/good"])
    r = chat(test_client, "job/dialogue", stream)
    assert r.status_code == 200
    assert "Well met, traveller." in r.text
    assert [c[0] for c in e.calls] == ["bad", "good"]
    failed = [x for x in rows(tracking) if x[0] == "bad"]
    assert failed and failed[0][1] == 0


def test_a_streamed_hang_in_the_first_choice_hands_over_inside_the_time(tracking, test_client, engine, monkeypatch):
    e = engine({"bad": "never", "good": "ok"})
    monkeypatch.setattr(engine_trouble, "FIRST_WORD_S", 0.6)
    route(test_client, ["ollama/bad", "ollama/good"])
    t0 = time.time()
    r = chat(test_client, "job/dialogue", True)
    assert r.status_code == 200 and "Well met, traveller." in r.text and time.time() - t0 < 5
    assert [c[0] for c in e.calls] == ["bad", "good"]


# ---------------------------------------------------------------------------------------------------------------------------------
# an empty reply is a failure, not "ok"
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("stream", [True, False])
def test_an_empty_reply_is_a_failure_and_the_last_choice_returns_a_real_error(tracking, test_client, engine, stream):
    engine({"mute": "empty"})
    r = chat(test_client, "ollama/mute", stream)
    assert r.status_code == 502, "an error the game can log, never an empty line"
    assert r.json()["error"]["message"] == SAYS_NOTHING
    assert rows(tracking) == [("mute", 0, "empty", "answered with nothing", "Ollama")]
    assert tracking._trouble.clause("Ollama::mute") == "answered with nothing"


@pytest.mark.parametrize("stream", [True, False])
def test_a_model_that_only_thought_says_so(tracking, test_client, engine, stream):
    engine({"thinker": "thinks"})                # wrote its thoughts and no answer (its reply ran out while it was thinking)
    r = chat(test_client, "ollama/thinker", stream)
    assert r.status_code == 502 and r.json()["error"]["message"] == engine_trouble.THOUGHT_SENTENCE == "It spent the whole time thinking and wrote nothing."
    assert rows(tracking) == [("thinker", 0, "empty", engine_trouble.THOUGHT_CLAUSE, "Ollama")]


def test_an_empty_stream_is_never_handed_to_the_game(tracking, test_client, engine):
    engine({"mute": "empty"})
    r = chat(test_client, "ollama/mute", True)
    assert "data:" not in r.text or "error" in r.text
    assert r.status_code != 200


def test_the_log_says_it_answered_with_nothing(tracking, test_client, engine, caplog):
    engine({"mute": "empty"})
    with caplog.at_level("INFO"):
        chat(test_client, "ollama/mute", False)
        chat(test_client, "ollama/mute", True)
    assert [m for m in caplog.messages if "answered with nothing" in m]


def test_a_reply_of_only_whitespace_is_empty_for_the_login_branch_too(tracking, proxy_module, monkeypatch, test_client):
    async def blank(*a, **k):
        return "  ", {}
    monkeypatch.setattr(proxy_module, "call_api_direct", blank)

    async def ready(*a, **k):
        return None
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", ready)
    r = test_client.post("/v1/chat/completions", json={"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502 and r.json()["error"]["message"] == SAYS_NOTHING
    assert [x[2] for x in rows(proxy_module)] == ["empty"]


# ---------------------------------------------------------------------------------------------------------------------------------
# an engine that says why only after the game gave up
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_given_up_request_gets_its_reason_from_one_patient_check(tracking, engine, monkeypatch):
    e = engine({"gemma4:26b": "memory"}, delay=0.2)
    monkeypatch.setattr(tracking, "ENGINE_PROBES", True)
    tracking._probing.clear()
    await tracking._record_speed("gemma4:26b", 15.0, False, fail_reason="gave up", provider="Ollama")
    tracking._after_silence("Ollama", "gemma4:26b")
    tracking._after_silence("Ollama", "gemma4:26b")        # a second one inside the same minutes does nothing
    await asyncio.gather(*list(tracking._background))
    assert len(e.calls) == 1 and e.calls[0][3] == 1, "one check, one word"
    assert tracking._trouble.clause("Ollama::gemma4:26b") == CLAUSE_MEMORY
    assert rows(tracking)[0][3] == CLAUSE_MEMORY, "kept with the given-up request"


@pytest.mark.asyncio
async def test_a_check_that_loads_clears_the_trouble(tracking, engine, monkeypatch):
    engine({"m": "ok"})
    monkeypatch.setattr(tracking, "ENGINE_PROBES", True)
    tracking._probing.clear()
    tracking._trouble.set("Ollama::m", "took too long to start answering")
    tracking._after_silence("Ollama", "m")
    await asyncio.gather(*list(tracking._background))
    assert tracking._trouble.clause("Ollama::m") is None


def test_tests_never_probe_a_real_engine(proxy_module):
    assert proxy_module.ENGINE_PROBES is False


# ---------------------------------------------------------------------------------------------------------------------------------
# where the person looks
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_routing_page_data_carries_the_note_and_the_page_draws_it(tracking, test_client, engine):
    engine({"gemma4:26b": "memory"}, delay=0.1)
    route(test_client, ["ollama/gemma4:26b"])
    chat(test_client, "job/dialogue", True)
    view = test_client.get("/config/routing").json()
    ai = next(a for a in view["ais"] if a["id"] == "ollama/gemma4:26b")
    assert ai["trouble"] == CLAUSE_MEMORY and ai["state"] == "warn"
    page = test_client.get("/").text
    assert "function rtTrouble(a, margin)" in page and "a.kind + ' ' + a.name + ': ' + a.trouble" in page
    assert "(only.trouble || (only.calls >= 4" in page


def test_the_speeds_card_names_the_model_and_why(tracking, test_client, engine):
    engine({"gemma4:26b": "memory"}, delay=0.1)
    for _ in range(3):
        chat(test_client, "ollama/gemma4:26b", True)
    speed_store.flush()
    cards = test_client.get("/stats/speeds/health").json()["cards"]
    card = next(c for c in cards if c["provider"] == "Ollama")
    assert card["title"] == "Not answering"
    assert f"Ollama gemma4:26b: {CLAUSE_MEMORY}" in card["detail"]


def test_the_speeds_failure_line_has_the_new_reason(tracking):
    fn = tracking._REASON_PHRASES[tracking.EMPTY]
    assert fn(2) == "2 answered with nothing"
    assert speed_queries._REASON_PHRASES["empty"](1) == "1 answered with nothing"


def _overview(ais_by_route, trouble):
    routes = [{"job": job, "ais": [{"key": f"provider:{n}", "pc": None, "name": n, **({"trouble": trouble} if trouble and i == 0 else {})} for i, n in enumerate(ais)]} for job, ais in ais_by_route]
    inp = dict(tracking=True, figures=None, this_pc={"name": "msi", "host": "msi", "gpu": None, "usedMb": None, "totalMb": None},
               local_models=[], local_speeds={}, pcs=[], providers=[{"id": "ollama", "name": "Ollama", "short": "Ollama", "state": "ok", "status": "Ready", "cloud": False}],
               health={}, uses={}, routes=routes, decisions=None, voice=None, setup={"done": True, "left": []})
    return overview.build(**inp)


def test_the_overview_says_a_route_with_nothing_behind_it_has_a_failing_model():
    note = "Ollama gemma4:26b: " + CLAUSE_MEMORY
    d = _overview([("Dialogue", ["Ollama"])], note)
    line = next(a for a in d["attention"] if a["name"] == "Ollama")
    assert line["level"] == "bad" and line["tab"] == {"to": "routing"}
    assert line["line"] == "Dialogue has no backup and its model is failing. Ollama gemma4:26b: couldn't load (ran out of memory while starting)."


def test_the_overview_with_a_backup_is_amber_and_names_the_backup_use():
    note = "Ollama gemma4:26b: " + CLAUSE_MEMORY
    d = _overview([("Dialogue", ["Ollama", "Claude"])], note)
    line = next(a for a in d["attention"] if a["name"] == "Ollama")
    assert line["level"] == "warn" and line["line"] == "Ollama gemma4:26b: couldn't load (ran out of memory while starting). Dialogue uses its backup."


def test_the_overview_adds_nothing_for_a_healthy_route():
    assert not [a for a in _overview([("Dialogue", ["Ollama"])], None)["attention"] if a["name"] == "Ollama"]


# ---------------------------------------------------------------------------------------------------------------------------------
# the Test button
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_test_says_how_long_it_took(tracking, test_client, engine):
    engine({"good": "ok"})
    route(test_client, ["ollama/good"])
    r = test_client.post("/config/routing/test", json={"job": "dialogue"}).json()
    assert r["ok"] is True
    assert re.fullmatch(r"Answered by Ollama good in \d+(\.\d)? s \(first word after \d+(\.\d)? s\)\.", r["line"]), r["line"]


def test_the_test_says_why_it_failed_in_plain_words(tracking, test_client, engine):
    engine({"gemma4:26b": "memory"}, delay=0.1)
    route(test_client, ["ollama/gemma4:26b"])
    r = test_client.post("/config/routing/test", json={"job": "dialogue"}).json()
    assert r["ok"] is False
    assert r["line"] == f"Nothing answered. Ollama gemma4:26b: {CLAUSE_MEMORY}."


def test_the_test_names_the_backup_that_answered(tracking, test_client, engine):
    engine({"bad": "memory", "good": "ok"})
    route(test_client, ["ollama/bad", "ollama/good"])
    r = test_client.post("/config/routing/test", json={"job": "dialogue"}).json()
    assert r["ok"] is True and r["line"].startswith("Answered by Ollama good in ") and f"Ollama bad: {CLAUSE_MEMORY}." in r["line"]


def test_a_cold_model_shows_its_loading_apart_from_its_reply_time(tracking, test_client, engine, monkeypatch):
    e = engine({"big": "slow"}, delay=0.4)

    async def cold(ai):
        return False
    monkeypatch.setattr(tracking, "_engine_is_loaded", cold)
    route(test_client, ["ollama/big"])
    r = test_client.post("/config/routing/test", json={"job": "dialogue"}).json()
    assert r["ok"] is True
    assert re.fullmatch(r"Ollama big loaded in \d+(\.\d)? s\. Its next reply took \d+(\.\d)? s \(first word after \d+(\.\d)? s\)\.", r["line"]), r["line"]
    assert len(e.calls) == 2


def test_a_cold_model_gets_the_long_patience_a_game_never_gives(tracking, test_client, engine, monkeypatch):
    engine({"big": "slow"}, delay=1.2)
    monkeypatch.setattr(engine_trouble, "FIRST_WORD_S", 0.3)
    route(test_client, ["ollama/big"])
    assert chat(test_client, "job/dialogue", True).status_code == 504, "the game's request is given up on"
    r = test_client.post("/config/routing/test", json={"job": "dialogue"}).json()
    assert r["ok"] is True, "the Test waits for it"


def test_the_test_without_a_route_says_so(tracking, test_client):
    r = test_client.post("/config/routing/test", json={"job": "dialogue"}).json()
    assert r == {"ok": False, "line": "Set a route for this job first.", "seconds": 0}


def test_the_test_button_is_on_the_page_with_its_words(tracking, test_client):
    page = test_client.get("/").text
    assert "Test this route" in page and "/config/routing/test" in page
    assert "Testing… a model that isn\\'t loaded yet can take a few minutes." in page
    assert "A model on this device (Ollama or LM Studio) that has not said its first word after about 20 seconds is given up on, so the next model can answer." in page
    assert "can\\'t be told from a slow start" not in page


# ---------------------------------------------------------------------------------------------------------------------------------
# a key, a path or a spoken line is never in a note
# ---------------------------------------------------------------------------------------------------------------------------------

def test_nothing_secret_reaches_a_note(tracking, test_client, engine):
    engine({"gemma4:26b": "memory"}, delay=0.1)
    chat(test_client, "ollama/gemma4:26b", False)
    speed_store.flush()
    with sqlite3.connect(tracking.SPEED_HISTORY_DB) as conn:
        notes = [r[0] for r in conn.execute("SELECT fail_note FROM speed_history WHERE fail_note IS NOT NULL")]
    assert notes == [CLAUSE_MEMORY]
    assert not re.search(r"\d|[\\/]|Users|sha256|Hello there", notes[0])
