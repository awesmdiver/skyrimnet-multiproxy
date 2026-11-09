"""Benchmarks contract rules: identifiers, states, statistics, records, headroom, sustained verdict (hand-worked examples)."""
import pytest

import benchmarks_contract as bc
from benchmarks_contract import RequestRecord as R


# --- identifiers ---
def test_setup_id_same_for_equal_setups_and_changes_with_any_field():
    a = {"kind": "chat", "levels": [1, 4], "temperature": 0.7, "resolved": {"layers": 33, "flash": True}}
    b = {"resolved": {"flash": True, "layers": 33}, "temperature": 0.7, "levels": [1, 4], "kind": "chat"}
    assert bc.setup_id(a) == bc.setup_id(b)
    assert bc.setup_id(dict(a, temperature=1.0)) != bc.setup_id(a)
    assert bc.setup_id(dict(a, resolved={"layers": 32, "flash": True})) != bc.setup_id(a)
    assert bc.setup_id(dict(a, levels=[1, 4, 8])) != bc.setup_id(a)


def test_setup_id_floats_one_way():
    assert bc.canonical_json({"t": 1.0}) == bc.canonical_json({"t": 1}) == '{"t":1}'
    assert bc.setup_id({"t": 0.1 + 0.2}) == bc.setup_id({"t": 0.3})
    with pytest.raises(ValueError):
        bc.canonical_json({"t": float("nan")})


def test_model_id_and_run_id():
    assert bc.model_id("a" * 64, 123) == "sha256:" + "a" * 64 + ":123"
    with pytest.raises(ValueError):
        bc.model_id("xyz", 1)
    rid = bc.new_run_id(0, unique="ab12")
    assert rid == "19700101T000000Z-ab12" and bc.valid_run_id(rid)
    assert not bc.valid_run_id("../x") and not bc.valid_run_id("..") and not bc.valid_run_id("a/b")


# --- states ---
def test_run_moves():
    assert bc.can_move_run("queued", "running") and bc.can_move_run("running", "completed")
    assert not bc.can_move_run("completed", "running") and not bc.can_move_run("cancelled", "failed")
    with pytest.raises(ValueError):
        bc.move_run("running", "failed")                       # needs a reason
    assert bc.move_run("running", "interrupted", "thermal_stop") == "interrupted"


def test_steps():
    assert bc.step_plan([1, 4, 8]) == ["load", "warmup", "level:1", "level:4", "level:8", "sustained", "unload", "cooldown"]
    assert bc.steps_total([1], sustained=False, cooldown=False) == 4
    assert bc.can_move_step("pending", "running") and not bc.can_move_step("done", "running")
    assert bc.steps_done({"load": "done", "warmup": "done", "level:1": "running", "x": "skipped"}) == 2


# --- percentile and statistics ---
def test_percentile_nearest_rank():
    assert bc.percentile([5], 95) == 5
    assert bc.percentile(range(1, 21), 95) == 19              # ceil(0.95*20) = 19
    assert bc.percentile(range(1, 21), 50) == 10
    assert bc.percentile(range(1, 26), 95) == 24              # ceil(23.75) = 24
    assert bc.percentile(range(1, 26), 50) == 13              # ceil(12.5) = 13
    assert bc.percentile([3, 1, 2], 0) == 1
    assert bc.percentile([1, 1, 1, 9], 75) == 1               # ties
    with pytest.raises(ValueError):
        bc.percentile([], 50)


@pytest.mark.parametrize("n,p95", [(1, False), (19, False), (20, True), (25, True)])
def test_p95_only_from_20(n, p95):
    s = bc.level_statistics(range(1, n + 1))
    assert s["n"] == n
    assert (s["p95"] != bc.NOT_ENOUGH_REQUESTS) is p95


def test_level_statistics_values():
    s = bc.level_statistics([2, 2, 4])
    assert (s["mean"], s["min"], s["max"], s["p50"]) == (8 / 3, 2, 4, 2)
    assert bc.level_statistics([])["n"] == 0


# --- records ---
def rec(i, sub, first, done, tokens=10, status="ok", level=4, **kw):
    return R(request_id=str(i), level=level, t_submit_s=sub, t_first_token_s=first, t_done_s=done, completion_tokens=tokens, status=status, **kw)


def test_record_metrics_from_engine_timings_only():
    r = rec(1, 1.0, 1.25, 3.0, prompt_n=200, prompt_ms=100.0, predicted_n=50, predicted_ms=500.0)
    assert r.ttft_ms().value == 250.0 and r.completion_time_s().value == 2.0
    assert r.prompt_tok_s().value == 2000.0 and r.generation_tok_s().value == 100.0
    bare = rec(2, 0.0, None, 2.0)                              # no stream, no timing report: never derived from the stopwatch
    assert bare.ttft_ms().status == bc.NOT_REPORTED
    assert bare.prompt_tok_s().status == bc.NOT_REPORTED and bare.generation_tok_s().status == bc.NOT_REPORTED
    assert rec(3, 0, 1, 2, predicted_n=5, predicted_ms=0.0).generation_tok_s().status == bc.NOT_REPORTED


def test_row_round_trip_and_unknown_keys():
    r = rec(1, 0.0, 0.5, 2.0, predicted_n=20, predicted_ms=400.0)
    row = r.to_row()
    assert row["generation_tok_s"] == 50.0 and row["prompt_tok_s"] is None
    assert R.from_row(dict(row, future_field=1)) == r


def test_level_summary():
    # four in flight at once, then one error, one timeout, one warm-up
    recs = [rec(i, 0.0, 0.1, 2.0 + i, tokens=10, predicted_n=10, predicted_ms=1000.0) for i in range(4)]
    recs += [rec(9, 0.0, None, 1.0, status="error"), rec(10, 0.0, None, 5.0, status="timeout"), rec(11, 0.0, 0.1, 99.0, warmup=True)]
    s = bc.level_summary(recs, 4, slots=4)
    assert s["completed"] == 4 and s["failed"] == 1 and s["timed_out"] == 1
    assert s["actual_overlap"] == 6                            # 4 ok + error + timeout (the warm-up is left out)
    assert s["aggregate_tok_s"]["value"] == 40 / 5.0           # last t_done 5.0 - first t_submit 0.0
    assert s["requests_per_s"]["value"] == 4 / 5.0
    assert s["generation_tok_s"]["n"] == 4 and s["generation_tok_s"]["p95"] == bc.NOT_ENOUGH_REQUESTS
    assert s["prompt_tok_s"]["n"] == 0


def test_level_summary_empty_and_no_token_counts():
    assert bc.level_summary([], 1)["aggregate_tok_s"]["status"] == bc.NOT_MEASURED
    r = rec(1, 0.0, 0.1, 1.0, tokens=None, level=1)
    assert bc.level_summary([r], 1)["aggregate_tok_s"]["status"] == bc.NOT_REPORTED


def test_overlap_finish_before_start_at_same_time():
    assert bc.max_overlap([rec(1, 0, 0, 1), rec(2, 1, 1, 2)]) == 1


# --- headroom ---
def hr(total, peak, delta, state="running"):
    return bc.headroom_estimate(total, peak, delta, state)


def test_headroom_dots_at_the_4gb_edge():
    assert hr(24576, 10000, 10480)["dot"] == "green" and hr(24576, 10000, 10480)["headroom_mb"] == 4096
    amber = hr(24576, 10000, 10481)
    assert amber["dot"] == "amber" and amber["headroom_gb"] == 3.99          # never shown as 4.0 while amber
    assert hr(24576, 10000, 14576)["dot"] == "amber" and hr(24576, 10000, 14576)["headroom_mb"] == 0
    assert hr(24576, 10000, 14577)["dot"] == "red"
    assert hr(24576, 10000, 10480.4)["dot"] == "green" and hr(24576, 10000, 10480.6)["dot"] == "amber"


def test_headroom_zero_is_never_negative_zero():
    import math
    r = hr(1000, 500, 500.2)
    assert r["headroom_mb"] == 0 and math.copysign(1, r["headroom_gb"]) == 1.0
    assert hr(1000, 500, 501)["headroom_gb"] < 0


def test_headroom_grey_closed_and_kind():
    assert hr(None, 1, 1)["dot"] == "grey" and hr(1000, None, 1)["dot"] == "grey" and hr(1000, 1, None)["dot"] == "grey"
    closed = hr(8000, None, 2000, "closed")
    assert closed["skyrim_peak_mb"] == 0 and closed["dot"] == "green" and closed["kind"] == "estimate"
    assert bc.headroom_estimate(8000, 1000, 1000, "history", usual_skyrim_mb=5000)["headroom_mb"] == 6000     # the usual figure is not used
    with pytest.raises(ValueError):
        hr(1, 1, 1, "maybe")


def test_delta_and_per_gpu():
    assert bc.model_vram_delta_mb(9000, 1000) == 8000
    assert bc.model_vram_delta_mb(900, 1000) == 0 and bc.model_vram_delta_mb(None, 1) is None
    out = bc.headroom_per_gpu([{"gpu_index": 0, "vram_total_mb": 24576, "vram_idle_before_load_mb": 1000, "vram_highest_overall_mb": 9000},
                               {"gpu_index": 1, "vram_total_mb": 8192}], 6000, "running")
    assert out[0]["gpu_index"] == 0 and out[0]["headroom_mb"] == 24576 - 6000 - 8000 and out[0]["dot"] == "green"
    assert out[1]["dot"] == "grey"


# --- sustained ---
def pts(per_second_values, step=10):
    """(time, tok/s) every `step` seconds from a list of (start, end, value) spans."""
    return [(t, v) for a, b, v in per_second_values for t in range(a, b, step)]


def test_sustained_completed():
    v = bc.sustained_verdict(pts([(5, 300, 100.0)]))
    assert v["status"] == "completed" and v["baseline_tok_s"] == 100.0 and len(v["windows"]) == 4 and v["after_minutes"] is None
    assert v["rule"]["slowdown_below_pct_of_baseline"] == 80


def test_sustained_slowdown_after_minutes():
    s = pts([(5, 120, 100.0), (120, 300, 79.0)])
    v = bc.sustained_verdict(s)
    assert v["status"] == "slowdown_detected" and v["after_minutes"] == 2.0
    exact = bc.sustained_verdict(pts([(5, 120, 100.0), (120, 300, 80.0)]))          # exactly 80% is not below
    assert exact["status"] == "completed"


def test_sustained_failure_interruption_not_run_and_empty():
    s = pts([(5, 300, 100.0)])
    assert bc.sustained_verdict(s, failed_reason="out of memory")["status"] == "failed"
    i = bc.sustained_verdict(s[:8], interrupted_reason="thermal_stop")
    assert i["status"] == "interrupted" and i["reason"] == "thermal_stop"
    assert bc.sustained_verdict([], ran=False)["status"] == "not_run"
    assert bc.sustained_verdict([(100, 50.0)])["status"] == "failed"               # nothing finished in the first 60 s
    gap = bc.sustained_verdict(pts([(5, 120, 100.0), (180, 300, 100.0)]))
    assert gap["empty_windows"] == 1 and gap["status"] == "completed"
    assert bc.sustained_verdict([(10, None), (20, 100.0), (130, 70.0)], duration_s=180)["status"] == "slowdown_detected"


def test_check_structure():
    c = bc.new_check("json", "Valid JSON", 24, 25, [{"request_id": "7", "line": "oops"}])
    assert c["failures"][0]["request_id"] == "7"
    with pytest.raises(ValueError):
        bc.new_check("x", "x", 3, 2)


def test_availability_round_trip():
    for a in (bc.measured(3), bc.not_measured("needs NVIDIA"), bc.NOT_REPORTED_AVAILABILITY):
        assert bc.Availability.from_dict(a.to_dict()) == a
