"""Benchmarks readings sampler: parsers and the sampler with recorded fake command output, cadence, highest/lowest, heat check, spill evidence, start/stop."""
import threading
import time

import pytest

import benchmarks_sampler as bsm

ONE_CARD = "0, NVIDIA GeForce RTX 4090, 1024, 24564, 3, 41, 28.50, 450.00, 210, 30\n"
TWO_CARDS = ("0, NVIDIA GeForce RTX 4090, 9000, 24564, 90, 70, 380.10, 450.00, 2520, 55\n"
             "1, NVIDIA GeForce RTX 3060, 4100, 12288, 12, 52, 60.00, 170.00, 1800, 40\n")
NO_POWER = "0, NVIDIA T400, 500, 4096, 5, 38, [N/A], [N/A], 300, [N/A]\n"
ALL_NA = "0, Tesla X, [N/A], [N/A], [N/A], [N/A], [Not Supported], [N/A], [N/A], [N/A]\n"
THERMAL = """
GPU 00000000:01:00.0
    Temperature
        GPU Current Temp                  : 41 C
        GPU T.Limit Temp                  : 43 C
        GPU Shutdown Temp                 : 98 C
        GPU Slowdown Temp                 : 95 C
        GPU Max Operating Temp            : 87 C

GPU 00000000:02:00.0
    Temperature
        GPU Current Temp                  : 52 C
        GPU Shutdown Temp                 : N/A
        GPU Slowdown Temp                 : N/A

GPU 00000000:03:00.0
    Temperature
        GPU Current Temp                  : 50 C
        GPU Shutdown Temp                 : 100 C
        GPU Slowdown Temp                 : N/A
"""


# This PC's RTX 5090 (2026-10-27): no absolute limits, only margins; the live margin is `GPU Current T.Limit Temp`.
THERMAL_5090 = """
Attached GPUs                                          : 1
GPU 00000000:01:00.0
    Temperature
        GPU Current Temp                               : 34.88 C
        GPU Current Max Temp                           : N/A
        GPU Current T.Limit Temp                       : 55 C
        GPU Shutdown T.Limit Temp Specification        : -5 C
        GPU Slowdown T.Limit Temp Specification        : -2 C
        GPU Max Operating T.Limit Temp Specification   : 0 C
        GPU Target Temperature Specification           : N/A
        Memory Current Temp                            : N/A
        Memory Max Operating T.Limit Temp Specification: N/A
"""


def smi_calls(s):
    return [c for c in s._runner.calls if c[0] == "nvidia-smi" and c[1].startswith("--query-gpu")]


class Fake:
    """A runner that answers from a script: `smi` is a list of (status, text) used in turn (the last repeats); `calls` records every command."""

    def __init__(self, smi, thermal=("ok", THERMAL)):
        self.smi, self.thermal, self.calls, self.i = list(smi), thermal, [], 0

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)
        if cmd == bsm.SMI_THERMAL:
            return self.thermal
        ans = self.smi[min(self.i, len(self.smi) - 1)]
        self.i += 1
        return ans


def sampler(smi, rows=None, **kw):
    out = [] if rows is None else rows
    kw.setdefault("machine_reader", lambda: {"cpu_util_pct": 10.0, "ram_used_mb": 8000})
    s = bsm.ReadingsSampler(on_sample=out.append, runner=Fake(smi), **kw)
    return s, out


# --- parsers ---
def test_parse_one_and_two_cards():
    r = bsm.parse_gpu_rows(ONE_CARD)
    assert r == [{"gpu_index": 0, "name": "NVIDIA GeForce RTX 4090", "vram_used_mb": 1024.0, "vram_total_mb": 24564.0, "gpu_util_pct": 3.0, "temp_c": 41.0,
                  "power_w": 28.5, "power_limit_w": 450.0, "clock_mhz": 210.0, "fan_pct": 30.0, "t_limit_margin_c": None}]
    two = bsm.parse_gpu_rows(TWO_CARDS)
    assert [c["gpu_index"] for c in two] == [0, 1] and two[1]["vram_total_mb"] == 12288.0 and two[0]["vram_total_mb"] != two[1]["vram_total_mb"]


def test_parse_na_fields_are_none_never_zero():
    r = bsm.parse_gpu_rows(NO_POWER)[0]
    assert r["power_w"] is None and r["power_limit_w"] is None and r["fan_pct"] is None and r["temp_c"] == 38.0
    z = bsm.parse_gpu_rows(ALL_NA)[0]
    assert all(z[f] is None for f in bsm.GPU_FIELDS)
    assert bsm.parse_gpu_rows("garbage\n0, x\n") == [] and bsm.parse_gpu_rows("") == []


def test_parse_thermal_limits():
    t = bsm.parse_thermal_limits(THERMAL)
    assert t[0]["slowdown_c"] == 95.0 and t[0]["shutdown_c"] == 98.0 and t[0]["thermal_limit_c"] == 95.0 and t[0]["max_operating_margin_c"] is None
    assert t[1]["thermal_limit_c"] is None
    assert t[2]["thermal_limit_c"] == 100.0
    assert bsm.parse_thermal_limits("") == {}


def test_cpu_helpers():
    assert bsm.parse_proc_stat("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 1 1 1\n") == (850, 1000)
    assert bsm.cpu_util_between((850, 1000), (900, 1100)) == 50.0
    assert bsm.cpu_util_between(None, (1, 2)) is None and bsm.cpu_util_between((1, 5), (1, 5)) is None
    assert bsm.read_ram_used_mb() is None or bsm.read_ram_used_mb() > 0


# --- sampling ---
def test_one_call_for_all_cards_and_rows_per_gpu_plus_machine():
    s, rows = sampler([("ok", TWO_CARDS)])
    s._t0 = 100.0
    s.tick(101.0)
    assert [r["kind"] for r in rows] == ["gpu", "gpu", "machine"] and rows[0]["t_s"] == 1.0 and rows[1]["gpu_index"] == 1
    calls = smi_calls(s)
    assert len(calls) == 1 and calls[0][1].count(",") == 10                   # one call, every field (and the margin column)
    assert rows[2]["cpu_util_pct"] == 10.0


def test_cadence_with_injected_clock():
    s, rows = sampler([("ok", ONE_CARD)])
    s._t0 = s._next_due = 0.0
    taken = [s.run_due(t) for t in (0.0, 0.4, 0.99, 1.0, 1.5, 2.0, 2.01)]
    assert taken == [True, False, False, True, False, True, False]
    assert s.run_due(10.0) is True and s.run_due(10.5) is False and s.run_due(11.0) is True       # late: no burst to catch up


def test_not_reported_reason_once_and_no_guess():
    s, rows = sampler([("ok", NO_POWER)])
    s._t0 = 0.0
    for t in (1, 2, 3):
        s.tick(t)
    gpu_rows = [r for r in rows if r["kind"] == "gpu"]
    assert all(r["power_w"] is None for r in gpu_rows)
    reasons = s.reasons()
    assert reasons["gpu0.power_w"] == bsm.REASON_NOT_REPORTED and reasons["gpu0.fan_pct"] == bsm.REASON_NOT_REPORTED and "gpu0.temp_c" not in reasons
    assert "power_w" not in s.highest_lowest()[0]


def test_highest_lowest_and_phases():
    cards = [f"0, A, {v}, 8000, 1, 40, 10, 100, 100, 10\n" for v in (1000, 5000, 3000)]
    s, _ = sampler([("ok", c) for c in cards])
    s._t0 = 0.0
    s.mark("idle_before_load")
    s.tick(1)
    s.mark("level_4")
    s.tick(2)
    s.tick(3)
    hl = s.highest_lowest()[0]["vram_used_mb"]
    assert hl == {"highest_sampled": 5000.0, "lowest_sampled": 1000.0}
    assert s.highest_in_phase("idle_before_load")[0]["vram_used_mb"] == 1000.0
    assert s.highest_in_phase()[0]["vram_used_mb"] == 5000.0                       # current phase
    snap = s.snapshot()
    assert snap["gpus"][0]["vram_used_mb"] == 3000.0 and snap["gpus"][0]["phase"] == "level_4" and snap["machine"]["ram_used_mb"] == 8000
    assert s.highest_lowest()["machine"]["ram_used_mb"]["highest_sampled"] == 8000


def test_two_cards_keep_separate_extremes():
    s, _ = sampler([("ok", TWO_CARDS)])
    s._t0 = 0.0
    s.tick(1)
    hl = s.highest_lowest()
    assert hl[0]["vram_used_mb"]["highest_sampled"] == 9000.0 and hl[1]["vram_used_mb"]["highest_sampled"] == 4100.0
    assert s.gpu_names() == {0: "NVIDIA GeForce RTX 4090", 1: "NVIDIA GeForce RTX 3060"}


def test_nvidia_smi_missing_stops_asking_at_once():
    s, rows = sampler([("missing", "")])
    s._t0 = 0.0
    s.tick(1)
    s.tick(2)
    assert s.reasons()["gpus"] == bsm.REASON_NEEDS_NVIDIA
    assert len(smi_calls(s)) == 1
    assert all(r["kind"] == "machine" for r in rows) and s.snapshot()["not_measured"] == bsm.REASON_NEEDS_NVIDIA


def test_hang_counts_and_five_failures_in_a_row_stop_asking():
    s, rows = sampler([("timeout", "")])
    s._t0 = 0.0
    for t in range(1, 5):
        s.tick(t)
    assert "gpus" not in s.reasons()
    s.tick(5)
    assert s.reasons()["gpus"] == bsm.REASON_GAVE_UP
    s.tick(6)
    assert len(smi_calls(s)) == 5                                                # the sixth second did not ask
    assert not any(r["kind"] == "gpu" for r in rows) and sum(r["kind"] == "machine" for r in rows) == 6


def test_a_success_resets_the_failure_count():
    s, _ = sampler([("timeout", ""), ("timeout", ""), ("ok", ONE_CARD), ("timeout", ""), ("timeout", "")])
    s._t0 = 0.0
    for t in range(1, 6):
        s.tick(t)
    assert "gpus" not in s.reasons()


def test_bad_sink_does_not_stop_readings():
    def boom(row):
        raise RuntimeError("x")
    s = bsm.ReadingsSampler(on_sample=boom, runner=Fake([("ok", ONE_CARD)]), machine_reader=lambda: {})
    s._t0 = 0.0
    s.tick(1)
    assert s.samples_taken == 1 and s.reasons()["machine.cpu_util_pct"] == bsm.REASON_UNREADABLE


# --- heat ---
def test_thermal_stop_with_and_without_a_limit():
    s, _ = sampler([("ok", "0, A, 1, 8000, 1, 94, 1, 1, 1, 1\n1, B, 1, 8000, 1, 99, 1, 1, 1, 1\n")])
    s._t0 = 0.0
    s._read_limits()
    s.tick(1)
    assert s.thermal_limit_c() == {0: 95.0, 1: None}
    assert s.thermal_stop()["stop"] is False                                          # card 0 is below 95; card 1 has no limit, so 99 C proves nothing
    s2, _ = sampler([("ok", "0, A, 1, 8000, 1, 95, 1, 1, 1, 1\n")])
    s2._t0 = 0.0
    s2._read_limits()
    s2.tick(1)
    stop = s2.thermal_stop()
    assert stop["stop"] is True and stop["gpu_index"] == 0 and stop["limit_c"] == 95.0 and stop["limit_source"] == "card"


def test_no_limit_readable_is_false_and_ceiling_is_opt_in():
    hot = "0, A, 1, 8000, 1, 99, 1, 1, 1, 1\n"
    s = bsm.ReadingsSampler(runner=Fake([("ok", hot)], thermal=("error", "")), machine_reader=lambda: {})
    s._t0 = 0.0
    s._read_limits()
    s.tick(1)
    assert s.thermal_stop() == {"stop": False, "limit_known": False} and s.thermal_limit_c() == {0: None}
    c = bsm.ReadingsSampler(runner=Fake([("ok", hot)], thermal=("error", "")), machine_reader=lambda: {}, ceiling_c=90.0)
    c._t0 = 0.0
    c.tick(1)
    r = c.thermal_stop()
    assert r["stop"] is True and r["limit_source"] == "setting" and r["limit_c"] == 90.0
    only1 = bsm.ReadingsSampler(runner=Fake([("ok", hot)], thermal=("error", "")), machine_reader=lambda: {}, ceiling_c=90.0, tested_gpus={1})
    only1._t0 = 0.0
    only1.tick(1)
    assert only1.thermal_stop()["stop"] is False


# --- spill ---
def test_spill_three_outcomes():
    d = bsm.spill_evidence(30, 33)
    assert d["spill"] == "detected" and "30 of 33 layers" in d["evidence"]
    assert bsm.spill_evidence(None, None, [100, 400])["spill"] == "detected" and "rose by 300 MB" in bsm.spill_evidence(None, None, [100, 400])["evidence"]
    assert bsm.spill_evidence(33, 33)["spill"] == "not_detected"
    assert bsm.spill_evidence(None, None, [100, 120])["spill"] == "not_detected"
    assert bsm.spill_evidence()["spill"] == "unknown"


def test_ram_rise_alone_is_never_detected():
    r = bsm.spill_evidence(ram_rose_mb=6000)
    assert r["spill"] == "unknown" and r["observation"] == "System RAM rose by 6000 MB during the run."
    assert bsm.spill_evidence(ram_rose_mb=10)["observation"] is None
    s, _ = sampler([("ok", ONE_CARD)])
    s._t0 = 0.0
    vals = iter([4000, 10000])
    s._machine_reader = lambda: {"cpu_util_pct": 1.0, "ram_used_mb": next(vals)}
    s.tick(1)
    s.tick(2)
    sp = s.spill()
    assert sp["spill"] == "unknown" and "6000 MB" in sp["observation"]


# --- start and stop ---
def test_start_stop_twice_and_no_thread_left():
    before = {t.name for t in threading.enumerate()}
    s, rows = sampler([("ok", ONE_CARD)], interval_s=0.02)
    assert s.start() is True and s.start() is False
    time.sleep(0.15)
    s.stop()
    s.stop()
    assert not s.alive() and rows and s.samples_taken >= 2
    assert not [t for t in threading.enumerate() if t.name == "benchmarks-sampler" and t.is_alive()]
    n = s.samples_taken
    time.sleep(0.06)
    assert s.samples_taken == n                                                       # nothing reads after stop
    assert {t.name for t in threading.enumerate() if t.is_alive()} >= before - {"benchmarks-sampler"}


def test_stop_without_start_and_restart():
    s, _ = sampler([("ok", ONE_CARD)], interval_s=0.02)
    s.stop()
    assert s.start() is True
    s.stop()
    assert not s.alive()


def test_shared_memory_reading_is_windows_only(monkeypatch):
    s, _ = sampler([("ok", ONE_CARD)])
    monkeypatch.setattr(bsm.sys, "platform", "linux")
    assert s.read_engine_shared_gpu_mb(123) is None and s._shared == []


# --- heat on a card that gives a margin, not a limit ---
def test_parse_margin_thermal_text_of_the_5090():
    t = bsm.parse_thermal_limits(THERMAL_5090)[0]
    assert t["thermal_limit_c"] is None and t["slowdown_c"] is None
    assert (t["current_margin_c"], t["max_operating_margin_c"], t["slowdown_margin_c"], t["shutdown_margin_c"]) == (55.0, 0.0, -2.0, -5.0)


def margin_sampler(rows_text, **kw):
    s = bsm.ReadingsSampler(runner=Fake(rows_text, thermal=("ok", THERMAL_5090)), machine_reader=lambda: {}, **kw)
    s._t0 = 0.0
    s._read_limits()
    return s


def test_margin_column_read_and_stop_fires_at_the_cards_own_margin():
    cool = "0, NVIDIA GeForce RTX 5090, 5000, 32607, 7, 35.08, 51.87, 575.00, 1432, 30, 55\n"
    edge = "0, NVIDIA GeForce RTX 5090, 5000, 32607, 99, 88.00, 500.00, 2400, 2400, 90, 0\n"
    s = margin_sampler([("ok", cool), ("ok", edge)])
    s.tick(1)
    assert s.snapshot()["gpus"][0]["t_limit_margin_c"] == 55.0 and s.thermal_stop() == {"stop": False, "limit_known": True}
    assert s.thermal_basis() == {0: "margin"} and s.thermal_limit_c() == {0: None}
    s.tick(2)
    stop = s.thermal_stop()
    assert stop["stop"] is True and stop["limit_source"] == "card margin" and stop["margin_c"] == 0.0 and stop["limit_margin_c"] == 0.0
    assert s.highest_lowest()[0]["t_limit_margin_c"] == {"highest_sampled": 55.0, "lowest_sampled": 0.0}


def test_older_driver_refuses_the_margin_column_and_falls_back():
    old = "0, A, 1, 8000, 1, 60, 1, 1, 1, 1\n"
    s = margin_sampler([("error", ""), ("ok", old), ("ok", old)])
    s.tick(1)
    s.tick(2)
    assert s._failures == 0 and "gpus" not in s.reasons()
    assert [c[1].count(",") for c in smi_calls(s)] == [10, 9, 9]               # tried once, then the plain query
    assert s.snapshot()["gpus"][0]["t_limit_margin_c"] == 55.0                  # from the -q text (the margin the card printed)


def test_margin_from_q_text_refreshed_every_few_passes():
    old = "0, A, 1, 8000, 1, 60, 1, 1, 1, 1\n"
    s = margin_sampler([("error", ""), ("ok", old)])
    for t in range(1, 7):
        s.tick(t)
    q_calls = [c for c in s._runner.calls if c == bsm.SMI_THERMAL]
    assert len(q_calls) == 1 + 1                                               # the start, then once in six passes


def test_no_margin_no_limit_stop_stays_off_and_says_so():
    s = bsm.ReadingsSampler(runner=Fake([("ok", "0, A, 1, 8000, 1, 99, 1, 1, 1, 1, [N/A]\n")], thermal=("ok", "GPU 0000\n    Temperature\n")), machine_reader=lambda: {})
    s._t0 = 0.0
    s._read_limits()
    s.tick(1)
    assert s.thermal_stop() == {"stop": False, "limit_known": False} and s.thermal_basis() == {0: "none"}
    assert s.reasons()["gpu0.t_limit_margin_c"] == bsm.REASON_NOT_REPORTED


def test_absolute_limit_wins_over_margin():
    both = THERMAL + "\n"
    s = bsm.ReadingsSampler(runner=Fake([("ok", "0, A, 1, 8000, 1, 96, 1, 1, 1, 1, 50\n")], thermal=("ok", both)), machine_reader=lambda: {})
    s._t0 = 0.0
    s._read_limits()
    s.tick(1)
    stop = s.thermal_stop()
    assert stop["stop"] is True and stop["limit_source"] == "card" and s.thermal_basis()[0] == "absolute"
