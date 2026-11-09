"""Benchmarks routes: behind the same guard as every dashboard route, the path rules, and the run list / export / delete."""
import asyncio
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import benchmarks_contract as bc
import benchmarks_runner as br
import benchmarks_store as bs
import benchmarks_wiring as bw
import local_guard
from benchmarks_fakes import FakeCard, FakeLauncher, make_service, plan_body, write_model

OWN = "http://127.0.0.1:8000"
JSON_H = {"X-MultiProxy": "1", "Content-Type": "application/json"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    base = tmp_path / "benchmarks"
    monkeypatch.setenv(bs.BENCHMARKS_DIR_ENV, str(base))
    local_guard.ENABLED = True
    card = FakeCard()
    launcher = FakeLauncher(card)
    svc = make_service(base, launcher, card)
    app = FastAPI()
    app.add_middleware(local_guard.LocalGuard, log=lambda m: None)
    handles = bw.setup(app, None, None, None, {}, str(tmp_path), "test", service=svc)
    with TestClient(app, base_url=OWN) as client:                  # one event loop for every request, like the real server (a background run must outlive its request)
        yield {"c": client, "svc": svc, "base": base, "model": write_model(tmp_path), "launcher": launcher, "handles": handles}
    for e in launcher.engines:
        e.stop()


def finish_run(api):
    for _ in range(300):
        s = api["c"].get("/config/benchmarks/status").json()
        if not s["running"]:
            return s
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


def start(api, **kw):
    return api["c"].post("/config/benchmarks/start", headers=JSON_H, content=json.dumps(plan_body([api["model"]], **kw)))


def test_guards_foreign_host_origin_header_and_body_rules(api):
    c = api["c"]
    body = json.dumps(plan_body([api["model"]]))
    assert c.get("/config/benchmarks/status", headers={"Host": "evil.example"}).status_code == 403
    assert c.get("/config/benchmarks/runs", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/config/benchmarks/start", headers={**JSON_H, "Origin": "https://evil.example"}, content=body).status_code == 403
    assert c.post("/config/benchmarks/start", headers={"Content-Type": "application/json"}, content=body).status_code == 403          # no X-MultiProxy header
    assert c.post("/config/benchmarks/start", headers={"X-MultiProxy": "1", "Content-Type": "text/plain"}, content=body).status_code == 415   # JSON bodies only
    assert c.delete("/config/benchmarks/runs/x").status_code == 403
    assert c.post("/config/benchmarks/cancel").status_code == 403
    assert c.get("/config/benchmarks/status", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    for h in ("/config/benchmarks/status", "/config/benchmarks/runs"):
        r = c.get(h, headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers
    assert api["launcher"].starts == 0


def test_get_with_a_body_is_refused(api):
    c = api["c"]
    assert c.request("GET", "/config/benchmarks/status", content=b'{"x":1}', headers={"Content-Type": "application/json"}).status_code == 400
    assert c.request("GET", "/config/benchmarks/runs", content=b"x").status_code == 400
    assert c.get("/config/benchmarks/status").status_code == 200


def test_start_status_runs_run_export_delete(api):
    c = api["c"]
    r = start(api)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["batch_id"]
    end = finish_run(api)
    assert end["runs"][0]["status"] == "completed"
    runs = c.get("/config/benchmarks/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["status"] == "completed" and runs[0]["kind"] == "chat"
    rid = runs[0]["run_id"]
    one = c.get(f"/config/benchmarks/runs/{rid}").json()
    assert one["manifest"]["run_id"] == rid and one["summary"]["levels"]["1"]["completed"] == 4 and one["request_count"] >= 4
    csv_text = c.get(f"/config/benchmarks/runs/{rid}/export?format=csv")
    assert csv_text.status_code == 200 and csv_text.headers["content-type"].startswith("text/csv") and csv_text.text.splitlines()[0].startswith("run_id,model_id,setup_id")
    full = c.get(f"/config/benchmarks/runs/{rid}/export?format=json").json()
    assert full["schema_version"] == 1 and len(full["requests"]) >= 4 and full["manifest"]["run_id"] == rid
    assert c.get(f"/config/benchmarks/runs/{rid}/export?format=xml").status_code == 400
    assert c.delete(f"/config/benchmarks/runs/{rid}", headers=JSON_H).json() == {"ok": True}
    assert c.get("/config/benchmarks/runs").json()["runs"] == [] and c.get(f"/config/benchmarks/runs/{rid}").status_code == 404


def test_status_has_live_readings_and_the_run_id_while_running(api):
    api["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 10}
    assert start(api, requests_per_level=30).status_code == 200
    seen = {}
    for _ in range(200):
        s = api["c"].get("/config/benchmarks/status").json()
        if s.get("step") == "level:1" and s.get("readings", {}).get("gpus"):
            seen = s
            break
        time.sleep(0.05)
    assert seen["running"] and seen["run_id"] and seen["model_index"] == 1 and seen["models_total"] == 1 and seen["readings"]["gpus"][0]["name"] == "Fake GPU"
    assert api["c"].delete(f"/config/benchmarks/runs/{seen['run_id']}", headers=JSON_H).status_code == 409           # still going
    assert api["c"].post("/config/benchmarks/cancel", headers=JSON_H).json() == {"ok": True, "cancelled": True}
    finish_run(api)
    assert api["c"].post("/config/benchmarks/cancel", headers=JSON_H).json()["cancelled"] is False


def test_refusals_come_back_as_plain_lines(api):
    c = api["c"]
    r = c.post("/config/benchmarks/start", headers=JSON_H, content=json.dumps({"models": []}))
    assert r.status_code == 400 and r.json()["detail"] == br.PLAN_NO_MODELS
    r = c.post("/config/benchmarks/start", headers=JSON_H, content="[1]")
    assert r.status_code == 400 and r.json()["detail"] == bw.BAD_BODY
    api["svc"].hooks.loaded_by_multiproxy = lambda: [{"id": "g", "name": "G"}]
    r = start(api)
    assert r.status_code == 409 and r.json()["needs_takeover"] == [{"id": "g", "name": "G"}] and r.json()["ok"] is False
    r = c.post("/config/benchmarks/start", headers=JSON_H, content=json.dumps(dict(plan_body([api["model"]]), takeover=True)))
    assert r.status_code == 200
    finish_run(api)


@pytest.mark.parametrize("path", ["/etc/passwd", "C:\\Windows\\system32\\config\\SAM", "model.gguf", "x.gguf\x00.txt", "../../escape.gguf", "\\\\server\\share\\m.gguf"])
def test_a_plan_path_must_be_an_existing_gguf_file(api, path):
    r = api["c"].post("/config/benchmarks/start", headers=JSON_H, content=json.dumps({"models": [path]}))
    assert r.status_code == 400 and r.json()["detail"] == br.PLAN_NOT_GGUF and api["launcher"].starts == 0


@pytest.mark.parametrize("run_id", ["..", "..%2f..", "a%2fb", "%2e%2e", "x" * 200, "not a run id"])
def test_run_ids_cannot_climb_out(api, tmp_path, run_id):
    (tmp_path / "keep.txt").write_text("x")
    assert api["c"].get(f"/config/benchmarks/runs/{run_id}").status_code in (404, 307, 405)
    assert api["c"].delete(f"/config/benchmarks/runs/{run_id}", headers=JSON_H).status_code in (404, 307, 405)
    assert (tmp_path / "keep.txt").exists()


def test_a_newer_schema_run_says_so(api):
    f = bs.create_run(api["base"], "20261027T000000Z-ff", {"started_epoch_s": 1.0})
    m = json.loads((f / bs.MANIFEST).read_text(encoding="utf-8"))
    m["schema_version"] = 9
    (f / bs.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    r = api["c"].get("/config/benchmarks/runs/20261027T000000Z-ff").json()
    assert r["status"] == "newer_version" and r["detail"] == bs.NEWER_VERSION_LINE
    assert api["c"].get("/config/benchmarks/runs").json()["runs"][0]["status"] == "newer_version"


def test_recover_on_start_marks_runs_and_stops_only_the_recorded_engine(api, monkeypatch):
    f = bs.create_run(api["base"], "20261027T000000Z-aa", {"started_epoch_s": 1.0, "steps_total": 6})
    killed = []
    import benchmarks_engine as be
    monkeypatch.setattr(be.local_models, "_pid_is_llama_server", lambda pid: True)
    monkeypatch.setattr(be.local_models, "kill_tree_sync", lambda pid: killed.append(pid))
    (api["base"] / be.PID_FILE).write_text(json.dumps({"pid": 31337}), encoding="utf-8")
    assert api["handles"].recover_on_start() == {"engine_stopped": True, "runs_marked": 1} and killed == [31337]
    assert bs.read_manifest(f)["status"] == "interrupted"


def test_the_real_app_has_the_routes_and_no_request_goes_through_the_proxy_path(proxy_module):
    paths = {getattr(r, "path", "") for r in proxy_module.app.routes}
    assert {"/config/benchmarks/start", "/config/benchmarks/status", "/config/benchmarks/cancel", "/config/benchmarks/runs", "/config/benchmarks/runs/{run_id}",
            "/config/benchmarks/runs/{run_id}/export"} <= paths
    assert proxy_module.benchmarks_handles.service.hooks.version == proxy_module.PROXY_VERSION
