"""Who answers a decision (Local decisions, step 1): the local model, a TypeSafe key, another AI standing in, in the order the player chose, through the real routes.
The local model itself is a stand-in object here (its engine is tested in test_decision_engine.py); what is checked is the order, the passing on, who the log says
answered, and that nothing changes for a player who hasn't installed it."""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import decision_engine as de
from tests.test_jev_provider import REAL_JEV_ANSWER_BODY, REAL_JEV_REQUEST, _make_mock_response, _make_mock_session

LOCAL_ANSWER = {"answers": {"intent": {"type": "choice", "choice": "greeting", "confidence": 0.9, "probabilities": {"complaint": 0.0, "greeting": 0.95, "other": 0.01, "question": 0.02, "request": 0.02}}},
                "usage": {"input_tokens": 321, "output_tokens": 1}}


class StubEngine:
    """Looks like the engine manager to the decision handles: installed, with a chosen behaviour for answer()."""

    def __init__(self, behaviour="answers"):
        self.behaviour, self.calls, self.state, self.limit_hits = behaviour, 0, "ready", 0

    def installed(self, key=None):
        return True

    def model_label(self):
        return "Kev-4B"

    async def answer(self, state, questions):
        self.calls += 1
        if self.behaviour == "answers":
            return LOCAL_ANSWER
        if self.behaviour == "too_slow":
            self.limit_hits += 1
            raise de.DecisionTooSlow(0.8)
        if self.behaviour == "stopped":
            self.state = "stopped"
            raise de.DecisionNotReady()
        if self.behaviour == "unanswerable":
            raise de.Unanswerable("The decision model couldn't take this question.")
        raise de.DecisionError(502, "The decision engine returned an error (500).")


@pytest.fixture
def local(proxy_module, monkeypatch, caplog):
    caplog.set_level(logging.INFO)

    def install(behaviour="answers", answer_first=None):
        stub = StubEngine(behaviour)
        monkeypatch.setattr(proxy_module.decision_handles, "engine", stub)
        monkeypatch.setattr(proxy_module.decision_handles, "_told", "")
        if answer_first:
            proxy_module._save_config({"decisions": {"answer_first": answer_first}})
        return stub
    return install


def ts_session(status=200, body=REAL_JEV_ANSWER_BODY):
    return _make_mock_session(_make_mock_response(status, body))


def post(proxy_module, test_client, *, key=False, session=None, deepseek=None, body=None, path="/v1/systemone"):
    patches = [patch.object(proxy_module, "typesafe_api_key", "ts-test-key" if key else None)]
    if key or session is not None:
        patches += [patch.object(proxy_module, "auth", MagicMock(session=None, is_ready=False)), patch("aiohttp.ClientSession", return_value=session or ts_session())]
    if deepseek is not None:
        patches += [patch.object(proxy_module, "deepseek_api_key", "k"), patch.object(proxy_module, "call_deepseek_direct", deepseek)]
    for p in patches:
        p.start()
    try:
        return test_client.post(path, json=body or REAL_JEV_REQUEST)
    finally:
        for p in reversed(patches):
            p.stop()


def lines(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


DEEPSEEK_OK = {"intent": "greeting"}


def deepseek():
    return AsyncMock(return_value=(json.dumps(DEEPSEEK_OK), {"prompt_tokens": 30, "completion_tokens": 4}))


class TestOrder:
    def test_the_order_without_the_local_model_is_as_before(self, proxy_module, monkeypatch):
        h = proxy_module.decision_handles
        monkeypatch.setattr(h.engine, "installed", lambda key=None: False)
        assert h.order("typesafe/jev-1.13", True) is None and h.order("typesafe/jev-1.13", False) is None

    def test_installed_it_comes_first_then_typesafe_then_a_stand_in(self, proxy_module, local):
        local()
        h = proxy_module.decision_handles
        assert h.order("typesafe/jev-1.13", True) == ["local", "typesafe", "standin"]
        assert h.order("typesafe/jev-1.13", False) == ["local", "standin"]

    def test_the_player_can_put_typesafe_first(self, proxy_module, local):
        local(answer_first="typesafe")
        h = proxy_module.decision_handles
        assert h.order("anything", True) == ["typesafe", "local", "standin"] and h.order("anything", False) == ["local", "standin"]

    def test_the_name_skyrimnet_sends_does_not_matter_but_local_decisions_pins_it(self, proxy_module, local):
        local()
        h = proxy_module.decision_handles
        assert h.order("typesafe/jev-latest", True) == h.order("openrouter/whatever", True) == h.order(None, True)
        assert h.order("local/decisions", True) == ["local"] and h.order("LOCAL/Decisions", False) == ["local"]

    def test_the_local_name_is_pinned_even_when_it_is_not_installed(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)
        assert proxy_module.decision_handles.order("local/decisions", True) == ["local"]


class TestLocalAnswers:
    def test_with_no_key_it_answers_in_typesafes_shape_and_the_log_says_who(self, proxy_module, test_client, local, caplog):
        stub = local()
        r = post(proxy_module, test_client)
        assert r.status_code == 200
        body = r.json()
        assert body["answers"] == LOCAL_ANSWER["answers"] and body["usage"] == LOCAL_ANSWER["usage"] and body["model"] == "typesafe/jev-1.13"
        a = lines(caplog, "answered:")
        assert len(a) == 1 and "decision (intent) via /v1/systemone" in a[0] and "Local decision model Kev-4B" in a[0]
        assert "in=321 out=1 tok" in a[0] and a[0].endswith("| ok") and "stand-in" not in a[0]
        assert stub.calls == 1

    def test_with_a_key_the_local_model_is_asked_first_and_typesafe_is_not_touched(self, proxy_module, test_client, local):
        local()
        session = ts_session()
        r = post(proxy_module, test_client, key=True, session=session)
        assert r.status_code == 200 and r.json()["answers"] == LOCAL_ANSWER["answers"]
        session.post.assert_not_called()

    def test_it_works_on_the_chat_address_and_every_decisions_address(self, proxy_module, test_client, local):
        local()
        for path in ("/v1/chat/completions", "/v1/systemone", "/v1/decisions", "/some/other/path"):
            assert post(proxy_module, test_client, path=path).json()["answers"] == LOCAL_ANSWER["answers"], path

    def test_the_speeds_tab_gets_a_row_for_it_under_decisions(self, proxy_module, test_client, local, monkeypatch, tmp_path):
        import sqlite3
        local()
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        db = tmp_path / "speeds.db"
        monkeypatch.setattr(proxy_module, "SPEED_HISTORY_DB", str(db))
        proxy_module._init_speed_history_db()
        post(proxy_module, test_client)
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM speed_history")]
        assert len(rows) == 1 and rows[0]["model"] == "local/decisions" and rows[0]["provider"] == "Local decision model" and rows[0]["success"] == 1
        assert "Decisions" in rows[0]["roles_json"]


class TestPassingOn:
    @pytest.mark.parametrize("behaviour,why", [("too_slow", "didn't answer within 0.8 s, passing the decision on"), ("stopped", "The local decision model is stopped"),
                                               ("unanswerable", "can't answer this one honestly"), ("broken", "failed (The decision engine returned an error (500).)")])
    def test_when_the_local_model_cannot_answer_a_stand_in_does(self, proxy_module, test_client, local, caplog, behaviour, why):
        local(behaviour)
        ds = deepseek()
        r = post(proxy_module, test_client, deepseek=ds)
        assert r.status_code == 200 and r.json()["answers"]["intent"]["choice"] == "greeting"
        ds.assert_awaited_once()
        assert lines(caplog, why)
        a = lines(caplog, "answered:")
        assert "stand-in for the local decision model" in a[-1] and "DeepSeek" in a[-1]

    def test_a_decision_the_limit_cut_is_recorded_as_too_busy(self, proxy_module, test_client, local, caplog):
        local("too_slow")
        post(proxy_module, test_client, deepseek=deepseek())
        assert any("failed (busy)" in m for m in lines(caplog, "answered:"))

    def test_a_stopped_model_is_mentioned_once_not_on_every_decision(self, proxy_module, test_client, local, caplog):
        local("stopped")
        for _ in range(3):
            post(proxy_module, test_client, deepseek=deepseek())
        assert len(lines(caplog, "The local decision model is stopped")) == 1

    def test_when_local_fails_the_typesafe_key_answers(self, proxy_module, test_client, local, caplog):
        local("too_slow")
        session = ts_session()
        r = post(proxy_module, test_client, key=True, session=session)
        assert r.status_code == 200 and session.post.call_count == 1
        a = lines(caplog, "answered:")
        assert "TypeSafe" in a[-1] and a[-1].endswith("| ok")

    def test_typesafe_first_and_it_fails_so_the_local_model_answers(self, proxy_module, test_client, local, caplog):
        stub = local(answer_first="typesafe")
        session = ts_session(429, b'{"error":"slow down"}')
        r = post(proxy_module, test_client, key=True, session=session)
        assert r.status_code == 200 and r.json()["answers"] == LOCAL_ANSWER["answers"] and stub.calls == 1
        assert lines(caplog, "TypeSafe didn't give an answer (429), passing the decision on")

    def test_typesafe_first_and_it_answers_so_the_local_model_is_not_asked(self, proxy_module, test_client, local):
        stub = local(answer_first="typesafe")
        r = post(proxy_module, test_client, key=True)
        assert r.status_code == 200 and stub.calls == 0

    def test_when_nobody_can_answer_typesafes_own_error_reaches_skyrimnet_unchanged(self, proxy_module, test_client, local):
        local("stopped")
        session = ts_session(429, b'{"error":"slow down"}')
        r = post(proxy_module, test_client, key=True, session=session)
        assert r.status_code == 429 and r.json() == {"error": "slow down"}

    def test_when_nobody_can_answer_and_there_is_no_key_it_is_a_plain_error(self, proxy_module, test_client, local):
        local("stopped")
        r = post(proxy_module, test_client)
        assert r.status_code == 500 and "The local model couldn't answer, and no other AI is ready" in r.json()["error"]["message"]

    def test_a_stand_in_that_fails_after_a_typesafe_error_gives_typesafes_error(self, proxy_module, test_client, local):
        from fastapi import HTTPException
        local("stopped")
        bad = AsyncMock(side_effect=HTTPException(status_code=502, detail="down"))
        r = post(proxy_module, test_client, key=True, session=ts_session(529, b'{"error":"overloaded"}'), deepseek=bad)
        assert r.status_code == 529 and r.json() == {"error": "overloaded"}


class TestPinned:
    def test_the_local_name_asks_the_local_model_and_nothing_else(self, proxy_module, test_client, local):
        stub = local()
        session = ts_session()
        r = post(proxy_module, test_client, key=True, session=session, body={**REAL_JEV_REQUEST, "model": "local/decisions"})
        assert r.status_code == 200 and r.json()["model"] == "local/decisions" and stub.calls == 1
        session.post.assert_not_called()

    def test_pinned_and_it_cannot_answer_is_a_clean_503_not_another_ai(self, proxy_module, test_client, local):
        local("too_slow")
        session = ts_session()
        ds = deepseek()
        r = post(proxy_module, test_client, key=True, session=session, deepseek=ds, body={**REAL_JEV_REQUEST, "model": "local/decisions"})
        assert r.status_code == 503 and "The local model couldn't answer" in r.json()["error"]["message"]
        session.post.assert_not_called()
        ds.assert_not_called()


class TestNothingChangesWithoutIt:
    def test_not_installed_a_key_still_means_typesafe_alone_and_its_errors_pass_through(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)
        monkeypatch.setattr(proxy_module.decision_handles.engine, "answer", AsyncMock(side_effect=AssertionError("must not be asked")))
        r = post(proxy_module, test_client, key=True, session=ts_session(422, b'{"error":"bad"}'))
        assert r.status_code == 422 and r.json() == {"error": "bad"}

    def test_not_installed_and_no_key_a_stand_in_answers(self, proxy_module, test_client, monkeypatch):
        monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: False)
        r = post(proxy_module, test_client, deepseek=deepseek())
        assert r.status_code == 200


# ---- the routes ---------------------------------------------------------------------------------------------------------------------------------
class TestRoutes:
    def test_status_has_the_settings_with_their_defaults_and_the_local_name(self, test_client):
        d = test_client.get("/config/decisions/status").json()
        assert d["settings"] == {"model": "kev-4b", "run_on": "automatic", "limit_seconds": 0, "paid_limit_seconds": 6.0, "ask_when_unsure": True, "sure_percent": 60, "answer_first": "local", "decision_order": ["local", "typesafe", "perplexity", "nano", "ollama"], "autostart": True, "location": "this", "decision_models": {}, "cloud_only": False, "cloud_prev_answer": "", "cloud_switched_to": ""}
        assert d["localName"] == "local/decisions" and d["state"] in ("not_installed", "stopped") and "runOn" in d and "limitHits" in d

    def test_settings_are_saved_and_checked(self, proxy_module, test_client):
        r = test_client.post("/config/decisions/settings", json={"model": "laya", "runOn": "cpu", "limitSeconds": 2.5, "answerFirst": "typesafe", "autostart": True})
        assert r.status_code == 200 and r.json()["settings"] == {"model": "laya", "run_on": "cpu", "limit_seconds": 2.5, "paid_limit_seconds": 6.0, "ask_when_unsure": True, "sure_percent": 60, "answer_first": "typesafe", "decision_order": ["typesafe", "local", "perplexity", "nano", "ollama"], "autostart": True, "location": "this", "decision_models": {}, "cloud_only": False, "cloud_prev_answer": "", "cloud_switched_to": ""}
        assert test_client.get("/config/decisions/status").json()["settings"]["answer_first"] == "typesafe"
        for bad in ({"model": "9b"}, {"runOn": "tpu"}, {"answerFirst": "never"}, {"limitSeconds": "soon"}, {"limitSeconds": -1}, {"limitSeconds": 0.05}):
            assert test_client.post("/config/decisions/settings", json=bad).status_code == 400, bad
        assert test_client.post("/config/decisions/settings", json={"limitSeconds": 0}).status_code == 200

    def test_the_fit_route_says_where_it_would_run(self, proxy_module, test_client, monkeypatch):
        from tests.decision_helpers import hardware
        monkeypatch.setattr(proxy_module.decision_handles.engine, "_hardware_fn", lambda folder: hardware(free_vram_gb=2.0))
        d = test_client.get("/config/decisions/fit").json()
        assert d["runOn"] == "cpu" and d["runOnReason"] == "Running on system memory (the graphics card is too full)." and d["graphics"]["freeMb"] == 2048 and d["verdict"] in ("ok", "warn", "refuse")

    def test_start_without_the_files_is_a_plain_409(self, test_client):
        r = test_client.post("/config/decisions/start")
        assert r.status_code == 409 and "isn't installed" in r.json()["detail"]

    def test_the_log_route_answers(self, test_client):
        assert "log" in test_client.get("/config/decisions/log").json()


class TestTheLineNamesTheRealAnswerer:
    def test_after_a_local_miss_the_stand_in_line_names_the_stand_in_and_its_model_not_the_local_one(self, proxy_module, test_client, local, caplog):
        local("too_slow")
        post(proxy_module, test_client, deepseek=deepseek())
        last = lines(caplog, "answered:")[-1]
        assert "DeepSeek deepseek-flash" in last and "local/decisions" not in last and "stand-in for the local decision model" in last


# ---- the order is the player's own list (2026-10-26) ----
class TestTheSavedOrder:
    def test_an_old_answer_first_pick_becomes_the_order_and_none_gives_the_default(self):
        import decision_wiring as dw
        default = dw.service_ids()
        assert default[:4] == ["local", "typesafe", "perplexity", "nano"]
        assert dw.clean_order(None, "local") == default
        assert dw.clean_order(None, "typesafe")[:2] == ["typesafe", "local"]
        assert dw.clean_order(None, "nanogpt")[:2] == ["nano", "local"]
        assert dw.clean_order(None, "perplexity")[:2] == ["perplexity", "local"]

    def test_a_saved_order_is_used_as_it_is_with_new_services_at_the_end_and_junk_dropped(self):
        import decision_wiring as dw
        assert dw.clean_order(["nano", "nope", "local", "nano"], "typesafe") == ["nano", "local", "typesafe", "perplexity", "ollama"], "the old pick is not read once an order is saved"

    def test_dragging_saves_the_order_and_it_decides_who_is_asked_first(self, proxy_module, test_client, monkeypatch):
        r = test_client.post("/config/decisions/settings", json={"decisionOrder": ["nano", "typesafe", "local", "perplexity", "ollama"]})
        assert r.status_code == 200 and r.json()["settings"]["decision_order"][:3] == ["nano", "typesafe", "local"]
        monkeypatch.setattr(proxy_module.decision_handles.engine, "installed", lambda key=None: True)
        assert proxy_module.decision_handles.order(None, True, True) == ["nano", "typesafe", "local", "standin"], "ready ones only, in the player's order, a stand-in last"
        assert proxy_module.decision_handles.order(None, True, False) == ["typesafe", "local", "standin"], "no key: skipped"
        assert proxy_module.decision_handles.order("local/decisions", True, True) == ["local"], "SkyrimNet's local/decisions still means the local model only"
        assert test_client.post("/config/decisions/settings", json={"decisionOrder": ["bogus"]}).status_code == 400
        assert test_client.post("/config/decisions/settings", json={"decisionOrder": []}).status_code == 400
