"""Benchmarks say up front when the llama.cpp engine is not installed (or local models are skipped): the tab route's engine state, and a start that is refused before any run folder exists."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import benchmarks_runner as br
import benchmarks_store as bs
import benchmarks_wiring as bw
import local_guard
from benchmarks_fakes import FakeCard, FakeLauncher, make_service, plan_body, write_model
from test_benchmarks_tab import OWN, JSON_H, StubLocal


def build(tmp_path, monkeypatch, installed=True, skipped=False):
    base = tmp_path / "benchmarks"
    monkeypatch.setenv(bs.BENCHMARKS_DIR_ENV, str(base))
    local_guard.ENABLED = True
    local = StubLocal(tmp_path)
    snap = local.snapshot

    async def snapshot():
        s = await snap()
        s["engine"] = {"installed": installed}
        return s
    local.snapshot = snapshot
    state = {"installed": installed, "skipped": skipped}
    hooks = br.Hooks(engine_state=lambda: br.engine_state_of(state["installed"], {"local_models_skipped": state["skipped"]}))
    svc = make_service(base, FakeLauncher(FakeCard()), FakeCard(), hooks=hooks)
    app = FastAPI()
    app.add_middleware(local_guard.LocalGuard, log=lambda m: None)
    bw.setup(app, local, lambda: {"local_models_skipped": skipped}, type("S", (), {"running": False})(), {}, str(tmp_path), "test", service=svc, tab_options={"cards_fn": lambda: []})
    return app, base, local, svc


@pytest.mark.parametrize("installed,skipped,want", [(True, False, "installed"), (False, False, "not_installed"), (True, True, "local_models_off"), (False, True, "local_models_off")])
def test_the_tab_route_says_the_engine_state(tmp_path, monkeypatch, installed, skipped, want):
    app, *_ = build(tmp_path, monkeypatch, installed, skipped)
    with TestClient(app, base_url=OWN) as c:
        st = c.get("/config/benchmarks/tab").json()
    assert st["engine"] == want and st["engineInstalled"] is installed


@pytest.mark.parametrize("installed,skipped,line", [(False, False, br.REFUSE_NO_ENGINE), (True, True, br.REFUSE_MODELS_OFF)])
def test_a_start_with_no_engine_is_refused_and_creates_no_run(tmp_path, monkeypatch, installed, skipped, line):
    app, base, local, svc = build(tmp_path, monkeypatch, installed, skipped)
    models = [write_model(tmp_path, f"m{i}.gguf") for i in range(5)]
    with TestClient(app, base_url=OWN) as c:
        single = c.post("/config/benchmarks/start", headers=JSON_H, content=json.dumps(plan_body(models[:1])))
        batch = c.post("/config/benchmarks/start", headers=JSON_H, content=json.dumps(plan_body(models)))
    for r in (single, batch):
        assert r.status_code == 409 and r.json()["detail"] == line
    assert not base.exists() or [p for p in base.iterdir() if p.is_dir()] == []


def test_the_refusal_words_match_the_warning_lines():
    import benchmarks_ui
    assert br.REFUSE_NO_ENGINE in benchmarks_ui.BENCHMARKS_JS
    assert br.REFUSE_MODELS_OFF in benchmarks_ui.BENCHMARKS_JS


def test_with_the_engine_present_a_start_still_runs(tmp_path, monkeypatch):
    app, base, local, svc = build(tmp_path, monkeypatch)

    async def go():
        r = await svc.start(plan_body([write_model(tmp_path, "ok.gguf")], levels=[1], requests_per_level=2, sustained=False))
        assert r["ok"], r
        await svc.wait()
    asyncio.run(go())
    assert any(p.is_dir() for p in base.iterdir())
