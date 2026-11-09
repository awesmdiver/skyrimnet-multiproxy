"""Benchmarks result folder: atomic files, listing, interrupted marking, deletion safety, exports, model hash cache."""
import hashlib
import json
import os

import pytest

import benchmarks_contract as bc
import benchmarks_store as bs


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "benchmarks"
    b.mkdir()
    return b


def make(base, rid="20261027T100000Z-aa", **extra):
    m = dict({"model_id": "m", "setup_id": "s", "profile": "standard", "kind": "chat", "started_epoch_s": 1000.0, "steps_total": 8}, **extra)
    return bs.create_run(base, rid, m)


def test_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(bs.BENCHMARKS_DIR_ENV, str(tmp_path / "x"))
    assert bs.benchmark_dir(tmp_path) == tmp_path / "x"
    monkeypatch.delenv(bs.BENCHMARKS_DIR_ENV)
    monkeypatch.setenv("MULTIPROXY_SETTINGS_DIR", str(tmp_path / "s"))
    assert bs.benchmark_dir(tmp_path) == tmp_path / "s" / "benchmarks"


def test_create_and_round_trip(base):
    f = make(base)
    m = bs.read_manifest(f)
    assert m["schema_version"] == 1 and m["status"] == "running" and m["run_id"] == f.name
    with pytest.raises(FileExistsError):
        make(base)
    assert not list(f.glob("*.tmp"))


def test_jsonl_append_flush_one_writer_and_cut_line(base):
    f = make(base)
    p = f / bs.REQUESTS
    with bs.JsonlWriter(p) as w:
        w.append({"a": 1})
        assert bs.read_jsonl(p) == [{"a": 1}]                   # flushed before close
        with pytest.raises(RuntimeError):
            bs.JsonlWriter(p)
        w.append({"a": 2, "t": "é"})
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"a": 3')                                     # a crash mid-line
    assert [r["a"] for r in bs.read_jsonl(p)] == [1, 2]
    with bs.JsonlWriter(p):                                     # the writer is free again after close
        pass


def test_interrupted_is_marked_without_changing_data(base):
    f = make(base)
    bs.write_progress(f, "level:4", 3, 8, heartbeat_epoch_s=1100.0)
    before = (f / bs.MANIFEST).read_bytes()
    assert bs.load_run(base, f.name, now_epoch_s=1150.0)["manifest"]["status"] == "running"      # heartbeat fresh
    run = bs.load_run(base, f.name, now_epoch_s=1100.0 + bs.STALE_HEARTBEAT_S + 1)
    assert run["manifest"]["status"] == "interrupted" and run["manifest"]["reason"] == "program_stopped"
    assert (f / bs.MANIFEST).read_bytes() == before
    assert bs.list_runs(base, 99999.0)[0]["status"] == "interrupted" and bs.list_runs(base, 99999.0)[0]["steps_done"] == 3


def test_finished_run_stays_completed(base):
    f = make(base)
    bs.finish_run(f, "completed", "", 1200.0, 8, 8)
    run = bs.load_run(base, f.name, now_epoch_s=10 ** 9)
    assert run["manifest"]["status"] == "completed" and run["manifest"]["steps_done"] == 8
    f2 = make(base, "r2")
    with pytest.raises(ValueError):
        bs.finish_run(f2, "failed", "", 1.0, 1, 8)               # a failed run needs a reason
    bs.finish_run(f2, "interrupted", "thermal_stop", 1.0, 5, 8)
    assert bs.read_manifest(f2)["reason"] == "thermal_stop"


def test_newer_schema_says_so(base):
    f = make(base)
    m = json.loads((f / bs.MANIFEST).read_text(encoding="utf-8"))
    m["schema_version"] = 2
    (f / bs.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(bs.NewerSchemaError) as e:
        bs.load_run(base, f.name, 0)
    assert e.value.found == 2
    entry = bs.list_runs(base, 0)[0]
    assert entry["status"] == "newer_version" and entry["message"] == bs.NEWER_VERSION_LINE


def test_list_runs_order_and_unreadable(base):
    make(base, "a1", started_epoch_s=1.0)
    make(base, "b2", started_epoch_s=5.0)
    (base / "junk").mkdir()
    (base / "not a run id").mkdir()
    out = bs.list_runs(base, 2.0)
    assert [e["run_id"] for e in out if e["status"] != "unreadable"][:1] == ["b2"]
    assert any(e["run_id"] == "junk" and e["status"] == "unreadable" for e in out)
    assert all(e["run_id"] != "not a run id" for e in out)


def test_delete_refuses_anything_else(base, tmp_path):
    f = make(base)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}")
    for bad in ("..", "../outside", str(outside), "x/y", ""):
        with pytest.raises(ValueError):
            bs.delete_run(base, bad)
    assert outside.exists()
    (base / "plain").mkdir()
    with pytest.raises(ValueError):                             # a folder with no manifest is not a run
        bs.delete_run(base, "plain")
    bs.delete_run(base, f.name)
    assert not f.exists() and (base / "plain").exists()


def test_csv_and_full_export(base):
    f = make(base)
    with bs.JsonlWriter(f / bs.REQUESTS) as w:
        w.append(bc.RequestRecord(request_id="1", level=1, t_submit_s=0, t_done_s=1, error="=cmd()", predicted_n=10, predicted_ms=100.0).to_row())
        w.append(dict(bc.RequestRecord(request_id="2", level=1).to_row(), future="x"))
    text = bs.requests_csv(base, f.name)
    lines = text.splitlines()
    assert lines[0].startswith("run_id,model_id,setup_id,profile,kind,request_id") and lines[0].endswith("future")
    assert lines[1].startswith(f"{f.name},m,s,standard,chat,1,") and "'=cmd()" in lines[1] and ",100.0," in lines[1]
    assert len(lines) == 3
    full = bs.full_export(base, f.name, 0)
    assert full["schema_version"] == 1 and len(full["requests"]) == 2 and full["manifest"]["run_id"] == f.name


def test_hash_cache_progress_cancel(tmp_path, base):
    p = tmp_path / "m.gguf"
    data = os.urandom(20000)
    p.write_bytes(data)
    seen = []
    mid = bs.model_id_for(p, base, progress=lambda d, t: seen.append((d, t)))
    assert mid == f"sha256:{hashlib.sha256(data).hexdigest()}:20000" and seen[-1] == (20000, 20000)
    seen.clear()
    assert bs.model_id_for(p, base, progress=lambda d, t: seen.append(d)) == mid and not seen      # cached: not read again
    p.write_bytes(data + b"x")
    assert bs.model_id_for(p, base).endswith(":20001") and bs.model_id_for(p, base) != mid
    with pytest.raises(bs.HashCancelled):
        bs.hash_file(p, cancelled=lambda: True)
