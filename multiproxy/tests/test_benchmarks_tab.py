"""The Benchmarks tab's server side: the model list, the saved setup, the results and one run's detail (from a real run against the fake engine), saving files, Add to MultiProxy."""
import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import benchmarks_contract as bc
import benchmarks_results as res
import benchmarks_store as bs
import benchmarks_tab as bt
import benchmarks_wiring as bw
import gpu_choice
import local_guard
from benchmarks_fakes import FakeCard, FakeLauncher, make_service, plan_body, write_model

OWN = "http://127.0.0.1:8000"
JSON_H = {"X-MultiProxy": "1", "Content-Type": "application/json"}


class StubLocal:
    HAS_GAME = True

    def __init__(self, tmp):
        self.tmp = tmp
        self.models = [SimpleNamespace(id="gemma-4-12b", name="Gemma 4 12B", path=write_model(tmp, "gemma-4-12b-Q4_K_M.gguf"))]
        self.added = []
        self.dl = None
        self.files = {"gemma-4-12b": self.models[0].path}        # catalog id -> the model file on disk (a vision model's text file can be here without being "installed")
        self.fetched, self.fail, self.free_ok, self.folder_ok = [], {}, True, True
        self.paused, self.cancelled, self.jobs = set(), [], {}

    def model_file(self, mid):
        return self.files.get(mid)

    def _paused_marks(self):
        return {f"model:{m}": True for m in self.paused}

    def _need_folder(self):
        import local_models
        if not self.folder_ok:
            raise local_models.LocalModelError(400, "Pick a folder to save models in first.")

    def _check_space(self, need):
        import local_models
        if not self.free_ok:
            raise local_models.LocalModelError(400, "This needs about 99 GB free, but the drive you chose only has 1 GB left.")

    def download_model_file(self, mid):
        """A fake download: it 'fetches' only the model file, in a task, and ends done / error / paused as told."""
        self.fetched.append(mid)
        job = SimpleNamespace(state="running", error="", task=None)

        async def work():
            await asyncio.sleep(0.05)
            if mid in self.fail:
                job.state, job.error = "error", self.fail[mid]
            elif mid in self.paused:
                job.state = "paused"
            else:
                self.files[mid] = write_model(self.tmp, mid + ".gguf")
                job.state = "done"
        job.task = asyncio.create_task(work())
        self.jobs[mid] = job
        return job

    async def cancel(self, key):
        self.cancelled.append(key)
        job = self.jobs.get(key.split(":", 1)[1])
        if job and job.task and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)

    async def snapshot(self):
        return {"catalog": [{"id": "gemma-4-12b", "sizeBytes": 7_500_000_000, "installed": True, "download": None},
                            {"id": "gemma-4-26b-a4b", "sizeBytes": 18_000_000_000, "installed": False, "download": None},
                            {"id": "gemma-4-e4b", "sizeBytes": 5_500_000_000, "installed": False, "download": self.dl},
                            {"id": "gemma-4-e2b", "sizeBytes": 3_900_000_000, "installed": False, "download": None},
                            {"id": "gemma-4-31b", "sizeBytes": 19_000_000_000, "installed": False, "download": None}],
                "installed": [{"id": "gemma-4-12b", "name": "Gemma 4 12B"}], "engine": {"installed": True}, "gpu": {"memoryGb": 32}}

    def installed(self):
        return self.models

    def add_custom(self, path, mmproj=""):
        self.added.append(path)
        self.models.append(SimpleNamespace(id="mine", name="mine", path=path))
        return SimpleNamespace(model_name="local/mine")


def card(i, name, mb):
    return gpu_choice.Card(f"GPU-{i}", name, mb, "nvidia")


def run_fake(tmp, base, **body):
    model = write_model(tmp, "fake-model.gguf")
    c = FakeCard()
    launcher = FakeLauncher(c)

    async def go():
        svc = make_service(base, launcher, c)
        r = await svc.start(plan_body([model], levels=[1, 4], requests_per_level=6, sustained=True, sustained_s=0.4, **body))
        assert r["ok"], r
        await svc.wait()
    asyncio.run(go())
    for e in launcher.engines:
        e.stop()
    return model


@pytest.fixture
def api(tmp_path, monkeypatch):
    base = tmp_path / "benchmarks"
    monkeypatch.setenv(bs.BENCHMARKS_DIR_ENV, str(base))
    local_guard.ENABLED = True
    local = StubLocal(tmp_path)
    saved_to = {"path": None, "picks": []}

    def save_dialog(title, name, types, ext):
        return saved_to["path"]

    def pick_dialog(title, types):
        return saved_to["picks"].pop(0) if saved_to["picks"] else None
    app = FastAPI()
    app.add_middleware(local_guard.LocalGuard, log=lambda m: None)
    svc = make_service(base, FakeLauncher(FakeCard()), FakeCard())
    cards = [card(1, "RTX 4090", 24576), card(2, "RTX 3060", 12288)]
    bw.setup(app, local, lambda: {}, SimpleNamespace(running=False), {}, str(tmp_path), "test", service=svc,
             tab_options={"save_dialog": save_dialog, "pick_dialog": pick_dialog, "cards_fn": lambda: cards})
    with TestClient(app, base_url=OWN) as client:
        yield {"c": client, "base": base, "tmp": tmp_path, "local": local, "saved": saved_to, "cards": cards, "svc": svc}


# --- the list ---
def test_community_file_is_read_and_unknown_or_broken_entries_are_skipped(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"models": [{"id": "gemma-4-12b", "title": "Gemma 4 12B", "quantisation": "Q4_K_M", "size_bytes": 1, "link": "x", "source": "s"}, {"id": "nope", "title": "No"}, {"id": 3}, "x", {"title": "no id"}]}), encoding="utf-8")
    assert [e["id"] for e in bt.load_community(str(f))] == ["gemma-4-12b", "nope"]
    assert [e["id"] for e in bt.load_community(str(f), {"gemma-4-12b"})] == ["gemma-4-12b"]
    assert bt.load_community(str(tmp_path / "missing.json")) == []
    f.write_text("not json", encoding="utf-8")
    assert bt.load_community(str(f)) == []


def test_the_shipped_list_names_only_models_the_local_tab_knows():
    import local_models
    entries = bt.load_community()
    assert len(entries) >= 5 and {e["id"] for e in entries} <= set(local_models.CATALOG_BY_ID)
    assert all(e["title"] and e["quantisation"] and e["link"].startswith("https://huggingface.co/") and e["source"] for e in entries)


def test_tab_state_rows_skyrim_and_gpus(api, monkeypatch):
    import skyrim_peak
    monkeypatch.setattr(skyrim_peak, "figure", lambda cfg: (28160, "2026-10-02"))
    api["local"].dl = {"state": "running", "percent": 30, "done": 1, "total": 3}
    st = api["c"].get("/config/benchmarks/tab").json()
    rows = {r["key"]: r for r in st["models"]}
    assert rows["c:gemma-4-12b"]["state"] == "downloaded" and rows["c:gemma-4-12b"]["path"].endswith("gemma-4-12b-Q4_K_M.gguf")
    assert rows["c:gemma-4-26b-a4b"]["state"] == "not_downloaded" and rows["c:gemma-4-e4b"]["state"] == "downloading" and rows["c:gemma-4-e4b"]["download"]["percent"] == 30
    assert all(not r["ticked"] for r in rows.values())
    assert st["skyrim"] == {"peakMb": 28160, "peakDay": "2026-10-02", "hasGame": True, "running": False}
    assert st["gpus"]["shown"] is True and [c["name"] for c in st["gpus"]["cards"]] == ["RTX 4090", "RTX 3060"]
    assert st["defaults"]["levels"] == [1, 4, 8] and st["defaults"]["requestsPerLevel"] == 25 and st["engineInstalled"] is True


def test_one_card_shows_no_gpus_control(api):
    api["cards"].pop()
    assert api["c"].get("/config/benchmarks/tab").json()["gpus"]["shown"] is False


def test_ticks_setup_files_and_hiding_are_saved_and_a_model_needs_its_file(api):
    c = api["c"]
    gguf = write_model(api["tmp"], "my-own-model.gguf")
    api["saved"]["picks"].append(gguf)
    r = c.post("/config/benchmarks/add-file", headers=JSON_H, content="{}").json()
    assert r["ok"] and r["key"] == "f:" + os.path.abspath(gguf)
    assert c.post("/config/benchmarks/add-file", headers=JSON_H, content="{}").json() == {"cancelled": True}              # the window was closed
    txt = api["tmp"] / "a.txt"
    txt.write_text("x")
    api["saved"]["picks"].append(str(txt))
    bad = c.post("/config/benchmarks/add-file", headers=JSON_H, content="{}")
    assert bad.status_code == 400 and bad.json()["detail"] == bt.NOT_GGUF
    c.post("/config/benchmarks/setup", headers=JSON_H, content=json.dumps({"ticked": ["c:gemma-4-12b", "c:gemma-4-26b-a4b", "f:" + os.path.abspath(gguf)], "setup": {"rpl": 40}}))
    st = c.get("/config/benchmarks/tab").json()
    rows = {r["key"]: r for r in st["models"]}
    assert rows["c:gemma-4-12b"]["ticked"] and rows["f:" + os.path.abspath(gguf)]["ticked"] and rows["c:gemma-4-26b-a4b"]["ticked"]        # not downloaded: can be ticked (it is downloaded first)
    assert st["pending"]["ids"] == ["gemma-4-26b-a4b"] and st["pending"]["count"] == 1
    assert rows["f:" + os.path.abspath(gguf)]["state"] == "local" and st["setup"] == {"rpl": 40}
    c.post("/config/benchmarks/hide", headers=JSON_H, content=json.dumps({"id": "gemma-4-26b-a4b"}))
    assert "c:gemma-4-26b-a4b" not in {r["key"] for r in c.get("/config/benchmarks/tab").json()["models"]}
    c.post("/config/benchmarks/remove-file", headers=JSON_H, content=json.dumps({"path": os.path.abspath(gguf)}))
    assert "f:" + os.path.abspath(gguf) not in {r["key"] for r in c.get("/config/benchmarks/tab").json()["models"]} and os.path.exists(gguf)       # the file itself is never touched
    os.remove(txt)
    big = c.post("/config/benchmarks/setup", headers=JSON_H, content=json.dumps({"setup": {"x": "y" * 30000}}))
    assert big.status_code == 400


def test_a_local_file_that_is_gone_is_said_so(api):
    gguf = write_model(api["tmp"], "gone-soon.gguf")
    api["saved"]["picks"].append(gguf)
    api["c"].post("/config/benchmarks/add-file", headers=JSON_H, content="{}")
    os.remove(gguf)
    rows = {r["key"]: r for r in api["c"].get("/config/benchmarks/tab").json()["models"]}
    assert rows["f:" + os.path.abspath(gguf)]["state"] == "missing" and not rows["f:" + os.path.abspath(gguf)]["ticked"]


# --- results and detail, from a real run against the fake engine ---
def test_results_and_detail_come_from_the_files(api):
    run_fake(api["tmp"], api["base"])
    rows = api["c"].get("/config/benchmarks/results").json()["runs"]
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "completed" and r["model"] == "fake-model.gguf" and r["size_bytes"] > 0 and r["level"] == 4 and r["requests"] == 6 and r["profile"] == "custom"
    assert r["peak_vram_mb"] == 8000 and r["highest_card_vram_mb"] == 9000 and r["headroom"]["kind"] == "estimate" and r["headroom"]["dot"] in ("green", "amber", "red", "grey") and r["load_time_s"] > 0
    assert r["generation_tok_s"]["n"] == 6 and r["generation_tok_s"]["p95"] == bc.NOT_ENOUGH_REQUESTS and r["ttft_ms"]["mean"] > 0 and r["prompt_tok_s"]["mean"] == 2000.0
    assert r["peak_temp_c"] == 40.0 and r["sustained"]["status"] in ("completed", "slowdown_detected") and r["checks"]["total"] > 0 and r["checks"]["passed"] <= r["checks"]["total"] and r["spill"] == "not_detected"
    d = api["c"].get(f"/config/benchmarks/runs/{r['run_id']}/detail").json()
    h = d["header"]
    assert h["model_id_short"] and "…" in h["model_id_short"] and h["setup_id"] and h["test_set_version"] == "v1" and h["engine_build"] == "b-fake" and h["ended"] == "completed"
    assert [l["level"] for l in d["levels"]] == [1, 4] and d["levels"][1]["requests"] == 6 and d["levels"][1]["together_tok_s"] > 0 and d["level"] == 4
    assert d["prompt_length"] and {p["prompt"] for p in d["prompt_length"]} <= {"short", "medium", "long"} and all(p["tokens_in"] == 200 for p in d["prompt_length"])
    assert d["settings"]["layers_on_card"] == 33 and d["settings"]["slots"] == 4 and d["memory"]["gpus"]["0"]["before_mb"] == 1000 and d["memory"]["gpus"]["0"]["highest_mb"] == 9000
    assert d["memory"]["gpus"]["0"]["delta_mb"] == 8000 and d["memory"]["ram"]["highest_mb"] == 9000
    assert d["telemetry"][-1]["who"] == "machine" and d["telemetry"][0]["who"] == 0                                                 # the cards first, the computer last
    assert d["settings"]["slots"] == 4 and d["settings"]["ctx"] == 4 * (d["settings"]["ctx"] // 4)                                    # the top level's own engine
    reading = {(t["who"], t["reading"]): t for t in d["telemetry"]}
    assert reading[(0, "temp_c")]["lowest"] == reading[(0, "temp_c")]["highest"] == 40.0 and ("machine", "cpu_util_pct") in reading
    assert len(d["charts"]["generation_tok_s"]) == 12 and d["charts"]["temp_c"]["0"] and d["charts"]["vram_used_mb"]["0"] and [c["id"] for c in d["checks"]] == [i for i in ("tags", "format", "language", "length", "choice") if i in {c["id"] for c in d["checks"]}] and {"tags", "language", "length"} <= {c["id"] for c in d["checks"]} and all(c["total"] > 0 for c in d["checks"])
    assert d["thermal_basis"] == {"0": "margin"}


def test_results_say_not_measured_by_leaving_the_number_out(api):
    f = bs.create_run(api["base"], "20261027T000000Z-aa", {"started_epoch_s": 1.0, "model_file": "m.gguf", "profile": "standard", "kind": "chat", "batch_id": "b"})
    bs.finish_run(f, "failed", "The engine stopped. Its last lines: x", 5.0, 1, 7)
    row = api["c"].get("/config/benchmarks/results").json()["runs"][0]
    assert row["status"] == "failed" and row["peak_vram_mb"] is None and row["generation_tok_s"] is None and row["headroom"]["dot"] == "grey" and row["headroom"]["mb"] is None and row["sustained"] is None
    d = api["c"].get("/config/benchmarks/runs/20261027T000000Z-aa/detail").json()
    assert d["levels"] == [] and d["telemetry"] == [] and d["charts"]["generation_tok_s"] == []


def test_a_newer_run_is_listed_as_such(api):
    f = bs.create_run(api["base"], "20261027T000000Z-bb", {"started_epoch_s": 1.0})
    m = json.loads((f / bs.MANIFEST).read_text(encoding="utf-8"))
    m["schema_version"] = 7
    (f / bs.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    assert api["c"].get("/config/benchmarks/results").json()["runs"] == [{"run_id": "20261027T000000Z-bb", "status": "newer_version", "schema_version": 7}]
    assert api["c"].get("/config/benchmarks/runs/20261027T000000Z-bb/detail").json()["status"] == "newer_version"


def test_test_again_plan_and_a_missing_file(api):
    model = run_fake(api["tmp"], api["base"], skyrim="available:20", prompts=["short", "long"], temperature=0.5)
    rid = api["c"].get("/config/benchmarks/results").json()["runs"][0]["run_id"]
    api["c"].post("/config/benchmarks/setup", headers=JSON_H, content=json.dumps({"ticked": []}))
    r = api["c"].get(f"/config/benchmarks/runs/{rid}/plan")
    assert r.status_code == 404 and r.json()["detail"] == bt.NO_FILE_FOR_RUN                                              # the file is not among the known models
    api["local"].models.append(SimpleNamespace(id="fake", name="fake", path=model))
    plan = api["c"].get(f"/config/benchmarks/runs/{rid}/plan").json()
    assert plan["models"] == [model] and plan["levels"] == [1, 4] and plan["requests_per_level"] == 6 and plan["skyrim"] == "available:20.0" and plan["prompts"] == ["short", "long"] and plan["temperature"] == 0.5
    import benchmarks_runner as br
    again = br.parse_plan(dict(plan))                                                                                      # the plan is a valid start body
    assert again.levels == (1, 4) and again.prompts == ("short", "long") and again.skyrim == "available"
    os.remove(model)
    assert api["c"].get(f"/config/benchmarks/runs/{rid}/plan").status_code == 404
    assert api["c"].get("/config/benchmarks/runs/..%2f/plan").status_code in (404, 307)


# --- saving files: the person picks where ---
def test_saving_asks_where_and_writes_only_there(api):
    run_fake(api["tmp"], api["base"])
    rid = api["c"].get("/config/benchmarks/results").json()["runs"][0]["run_id"]
    c = api["c"]
    r = c.post(f"/config/benchmarks/runs/{rid}/save", headers=JSON_H, content=json.dumps({"format": "csv"}))
    assert r.json() == {"cancelled": True}                                                                                # the window was closed: nothing written
    out = api["tmp"] / "chosen" / "requests.csv"
    out.parent.mkdir()
    api["saved"]["path"] = str(out)
    assert c.post(f"/config/benchmarks/runs/{rid}/save", headers=JSON_H, content=json.dumps({"format": "csv"})).json() == {"saved": True}
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("run_id,model_id,setup_id")
    out2 = api["tmp"] / "chosen" / "all.json"
    api["saved"]["path"] = str(out2)
    assert c.post(f"/config/benchmarks/runs/{rid}/save", headers=JSON_H, content=json.dumps({"format": "json"})).json() == {"saved": True}
    full = json.loads(out2.read_text(encoding="utf-8"))
    assert full["schema_version"] == 1 and full["manifest"]["run_id"] == rid and len(full["requests"]) > 6
    assert c.post(f"/config/benchmarks/runs/{rid}/save", headers=JSON_H, content=json.dumps({"format": "xml"})).status_code == 400
    assert c.post("/config/benchmarks/runs/nope/save", headers=JSON_H, content=json.dumps({"format": "csv"})).status_code == 404
    out3 = api["tmp"] / "chosen" / "table.json"
    api["saved"]["path"] = str(out3)
    assert c.post("/config/benchmarks/save-results", headers=JSON_H, content="{}").json() == {"saved": True}
    table = json.loads(out3.read_text(encoding="utf-8"))
    assert table["results"][0]["run_id"] == rid and table["schema_version"] == 1


# --- Add to MultiProxy ---
def test_add_to_multiproxy_uses_the_existing_add_and_never_a_route(api):
    model = run_fake(api["tmp"], api["base"])
    rid = api["c"].get("/config/benchmarks/results").json()["runs"][0]["run_id"]
    c = api["c"]
    assert c.post("/config/benchmarks/add-to-multiproxy", headers=JSON_H, content=json.dumps({"run_id": rid})).json()["detail"] == bt.NO_FILE_FOR_RUN
    api["saved"]["picks"].append(model)
    c.post("/config/benchmarks/add-file", headers=JSON_H, content="{}")
    r = c.post("/config/benchmarks/add-to-multiproxy", headers=JSON_H, content=json.dumps({"run_id": rid})).json()
    assert r == {"ok": True, "modelName": "local/mine"} and api["local"].added == [os.path.abspath(model)]
    again = c.post("/config/benchmarks/add-to-multiproxy", headers=JSON_H, content=json.dumps({"run_id": rid})).json()
    assert again["already"] is True and again["message"] == bt.ALREADY_THERE and len(api["local"].added) == 1


# --- the guard covers the new routes too ---
def test_new_routes_are_behind_the_same_guard(api):
    c = api["c"]
    for method, path in (("GET", "/config/benchmarks/tab"), ("GET", "/config/benchmarks/results"), ("GET", "/config/benchmarks/runs/x/detail"), ("GET", "/config/benchmarks/runs/x/plan")):
        assert c.request(method, path, headers={"Host": "evil.example"}).status_code == 403
        assert c.request(method, path, headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.request(method, path, content=b"x").status_code == 400                                                    # a GET with a body
    for path in ("/config/benchmarks/setup", "/config/benchmarks/add-file", "/config/benchmarks/remove-file", "/config/benchmarks/hide", "/config/benchmarks/runs/x/save", "/config/benchmarks/save-results", "/config/benchmarks/add-to-multiproxy"):
        assert c.post(path, content="{}", headers={"Content-Type": "application/json"}).status_code == 403                  # no X-MultiProxy header
        assert c.post(path, content="{}", headers={"X-MultiProxy": "1", "Content-Type": "text/plain"}).status_code == 415
        assert c.post(path, content="{}", headers={**JSON_H, "Origin": "https://evil.example"}).status_code == 403
    assert api["local"].added == []


# --- the pure parts ---
def test_short_ids():
    assert res.short_model_id("sha256:" + "9f3a" + "0" * 56 + "c1e2:123") == "9f3a…c1e2" and res.short_model_id("") == "" and res.short_setup_id("41b7ffee") == "41B7"


def test_chart_points_are_thinned_and_telemetry_only_counts_the_test_phases():
    samples = [{"kind": "gpu", "gpu_index": 0, "t_s": float(i), "phase": "level_1", "temp_c": 40.0 + i, "vram_used_mb": 1000, "power_w": None} for i in range(500)]
    samples.append({"kind": "gpu", "gpu_index": 0, "t_s": 600.0, "phase": "cooldown", "temp_c": 99.0})
    samples.append({"kind": "gpu", "gpu_index": 0, "t_s": 1.0, "phase": "idle_before_load", "temp_c": 1.0})
    ch = res.charts([], samples)
    assert len(ch["temp_c"]["0"]) <= res.CHART_POINTS + 1 and "power_w" not in ch
    tel = {(t["who"], t["reading"]): t for t in res.telemetry(samples)}
    assert tel[(0, "temp_c")]["lowest"] == 40.0 and tel[(0, "temp_c")]["highest"] == 539.0


# --- already on the PC, several models, one download ---
def tick(api, *keys):
    api["c"].post("/config/benchmarks/setup", headers=JSON_H, content=json.dumps({"ticked": list(keys)}))


def wait_queue(api, secs=10):
    for _ in range(int(secs / 0.05)):
        q = api["c"].get("/config/benchmarks/downloads").json()
        if not q["active"]:
            return q
        time.sleep(0.05)
    raise AssertionError("the download queue did not finish")


def test_a_catalog_model_whose_file_is_here_reads_downloaded_even_when_the_local_tab_does_not_list_it(api):
    """A vision model whose text file is in the models folder but whose vision file is not: the Local device tab does not call it installed; this list does."""
    api["local"].files["gemma-4-e4b"] = write_model(api["tmp"], "gemma-4-E4B-it-Q4_K_M.gguf")
    rows = {r["key"]: r for r in api["c"].get("/config/benchmarks/tab").json()["models"]}
    r = rows["c:gemma-4-e4b"]
    assert r["state"] == "downloaded" and r["path"].endswith("gemma-4-E4B-it-Q4_K_M.gguf") and r["download"] is None


def test_models_the_local_tab_holds_that_the_list_does_not_know_are_listed_once(api):
    mine = write_model(api["tmp"], "from-the-local-tab.gguf")
    api["local"].models.append(SimpleNamespace(id="mine", name="My model", path=mine, custom=True))
    same = write_model(api["tmp"], "also-added-here.gguf")
    api["local"].models.append(SimpleNamespace(id="mine2", name="Added twice", path=same))
    api["saved"]["picks"].append(same)
    api["c"].post("/config/benchmarks/add-file", headers=JSON_H, content="{}")
    rows = api["c"].get("/config/benchmarks/tab").json()["models"]
    keys = [r["key"] for r in rows]
    assert keys.count("f:" + os.path.abspath(mine)) == 1 and keys.count("f:" + os.path.abspath(same)) == 1
    r = next(r for r in rows if r["key"] == "f:" + os.path.abspath(mine))
    assert r["state"] == "local" and r["title"] == "My model" and r["fromLocalTab"] is True
    assert "fromLocalTab" not in next(r for r in rows if r["key"] == "f:" + os.path.abspath(same))
    assert "c:gemma-4-12b" in keys and keys.count("f:" + os.path.abspath(api["local"].models[0].path)) == 0           # the curated model is not listed a second time


def test_download_fetches_nothing_for_a_model_that_is_already_here(api):
    r = api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-12b"]}))
    assert r.status_code == 200 and r.json() == {"ok": True, "ids": []}
    wait_queue(api)
    assert api["local"].fetched == []


def test_download_ticked_goes_one_after_another_and_only_the_missing(api):
    r = api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-e2b", "nope"]}))
    assert r.json() == {"ok": True, "ids": ["gemma-4-26b-a4b", "gemma-4-e2b"]}
    q = wait_queue(api)
    assert api["local"].fetched == ["gemma-4-26b-a4b", "gemma-4-e2b"] and q["done"] == ["gemma-4-26b-a4b", "gemma-4-e2b"] and q["failed"] == {} and q["start_result"] is None
    rows = {r["key"]: r for r in api["c"].get("/config/benchmarks/tab").json()["models"]}
    assert rows["c:gemma-4-26b-a4b"]["state"] == "downloaded" and rows["c:gemma-4-e2b"]["state"] == "downloaded"


def test_no_room_refuses_plainly_and_downloads_nothing(api):
    api["local"].free_ok = False
    r = api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b"]}))
    assert r.status_code == 400 and "free" in r.json()["detail"] and api["local"].fetched == []
    api["local"].free_ok, api["local"].folder_ok = True, False
    assert api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b"]})).status_code == 400
    assert api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": []})).status_code == 400


def test_start_downloads_the_missing_ones_first_then_tests_every_ticked_model(api):
    started = []

    async def fake_start(body, takeover=False):
        started.append(body)
        return {"ok": True, "batch_id": "b"}
    api["svc"].start = fake_start
    body = plan_body([api["local"].models[0].path])
    r = api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b", "gemma-4-e2b"], "then": body}))
    assert r.json()["ok"]
    q = wait_queue(api)
    assert len(started) == 1 and started[0]["models"] == [api["local"].models[0].path, api["local"].files["gemma-4-26b-a4b"], api["local"].files["gemma-4-e2b"]]
    assert q["start_result"]["ok"] and api["local"].fetched == ["gemma-4-26b-a4b", "gemma-4-e2b"]                        # downloaded before the test began


def test_a_failed_download_leaves_that_model_out_and_says_why(api):
    started = []

    async def fake_start(body, takeover=False):
        started.append(body["models"])
        return {"ok": True, "batch_id": "b"}
    api["svc"].start = fake_start
    api["local"].fail["gemma-4-26b-a4b"] = "The download was damaged. Press Try again."
    api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b", "gemma-4-e2b"], "then": plan_body([api["local"].models[0].path])}))
    q = wait_queue(api)
    assert q["failed"] == {"gemma-4-26b-a4b": "The download was damaged. Press Try again."} and q["done"] == ["gemma-4-e2b"]
    assert started == [[api["local"].models[0].path, api["local"].files["gemma-4-e2b"]]]
    rows = {r["key"]: r for r in api["c"].get("/config/benchmarks/tab").json()["models"]}
    assert rows["c:gemma-4-26b-a4b"]["failure"] == "The download was damaged. Press Try again." and rows["c:gemma-4-26b-a4b"]["state"] == "not_downloaded"


def test_when_every_download_fails_and_nothing_is_ready_nothing_starts(api):
    started = []

    async def fake_start(body, takeover=False):
        started.append(body)
        return {"ok": True}
    api["svc"].start = fake_start
    api["local"].fail["gemma-4-26b-a4b"] = "No network."
    api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b"], "then": plan_body([])}))
    q = wait_queue(api)
    assert started == [] and q["start_result"] == {"ok": False, "message": bt.DL_FAILED_NONE}


def test_cancel_stops_everything_and_starts_nothing(api):
    started = []

    async def fake_start(body, takeover=False):
        started.append(body)
        return {"ok": True}
    api["svc"].start = fake_start
    api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b", "gemma-4-e2b"], "then": plan_body([api["local"].models[0].path])}))
    r = api["c"].post("/config/benchmarks/download/cancel", headers=JSON_H, content=json.dumps({"id": "gemma-4-26b-a4b"}))
    assert r.json()["ok"]
    q = wait_queue(api)
    assert started == [] and q["cancelled"] is True and q["start_result"] is None and "model:gemma-4-26b-a4b" in api["local"].cancelled
    assert api["local"].fetched == ["gemma-4-26b-a4b"]                                                                     # the second was never begun
    assert not api["c"].get("/config/benchmarks/tab").json()["queue"]["active"]


def test_a_pause_ends_the_queue_without_starting_the_test(api):
    started = []

    async def fake_start(body, takeover=False):
        started.append(body)
        return {"ok": True}
    api["svc"].start = fake_start
    api["local"].paused.add("gemma-4-26b-a4b")
    api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b", "gemma-4-e2b"], "then": plan_body([api["local"].models[0].path])}))
    q = wait_queue(api)
    assert started == [] and q["paused"] is True and api["local"].fetched == ["gemma-4-26b-a4b"] and q["failed"] == {}


def test_a_second_download_while_one_is_going_is_refused(api):
    api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b"]}))
    again = api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-e2b"]}))
    assert again.status_code == 409
    wait_queue(api)


def test_a_refused_start_after_the_downloads_is_kept_for_the_page(api):
    async def fake_start(body, takeover=False):
        return {"ok": False, "status": 409, "needs_takeover": [{"id": "g", "name": "Gemma"}], "message": "A game model is using the graphics card. Stop it to run the benchmark?"}
    api["svc"].start = fake_start
    api["c"].post("/config/benchmarks/download", headers=JSON_H, content=json.dumps({"ids": ["gemma-4-26b-a4b"], "then": plan_body([])}))
    q = wait_queue(api)
    assert q["start_result"]["needs_takeover"] == [{"id": "g", "name": "Gemma"}] and q["body"]["models"] == [api["local"].files["gemma-4-26b-a4b"]]


def test_download_routes_are_behind_the_same_guard(api):
    r = api["c"].post("/config/benchmarks/download", content=json.dumps({"ids": ["gemma-4-e2b"]}))
    assert r.status_code >= 400 and api["local"].fetched == []


def _sus(status, base, meds, **kw):
    wins = [{"start_s": 60 * (i + 1), "n": 0 if m is None else 3, "p50_tok_s": m, "below_threshold": False} for i, m in enumerate(meds)]
    return {"status": status, "baseline_tok_s": base, "windows": wins, "after_minutes": kw.get("after"), "reason": kw.get("reason", "")}


def test_sustained_cell_evidence():
    row = lambda v: res.result_row({"run_id": "r"}, {"sustained": v})["sustained"]
    done = row(_sus("completed", 15.0, [14.0, 15.0, None]))
    assert done["lowest_pct"] == 93 and done["lowest_tok_s"] == 14.0 and done["baseline_tok_s"] == 15.0
    slow = row(_sus("slowdown_detected", 20.0, [18.0, 10.0], after=2.0))
    assert slow["lowest_pct"] == 50 and slow["after_minutes"] == 2.0
    assert row(_sus("failed", None, [], reason="no request finished in the first 60 s"))["lowest_pct"] is None
    assert row(_sus("not_run", None, []))["status"] == "not_run"
    assert row(_sus("completed", 15.0, [None, None]))["lowest_pct"] is None                       # no usable window: no percent
