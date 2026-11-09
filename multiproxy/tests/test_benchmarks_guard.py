"""Benchmarks protect the PC, step 1: the memory guard rules (fake readings, a fake monotonic clock), the owned process (a fake operating system, and one real throw-away child), and the
runner: a model that runs the PC out of memory is stopped on its own, the next one waits until memory has recovered."""
import asyncio
import json
import sys

import pytest

import benchmarks_contract as bc
import benchmarks_guard as g
import benchmarks_owned as ow
import benchmarks_runner as br
import benchmarks_sampler as bsm
import benchmarks_store as bs
from benchmarks_fakes import FakeCard, FakeLauncher, plan_body, read_rows, write_model

MB = 1
TOTAL = 64000                                             # a 64 GB machine: warning 7,680 MB, stop 5,120 MB, emergency 1,280 MB
LV = g.GuardConfig().levels(TOTAL)


def reading(avail=40000, total=TOTAL, **kw):
    return dict({"available_mb": avail, "total_mb": total}, **kw)


def run_guard(guard, steps):
    """steps: (seconds from the start, reading or None). Returns the answer after each one."""
    return [guard.feed(float(t), r) for t, r in steps]


# ---- the levels ----
def test_levels_scale_with_the_machine():
    cfg = g.GuardConfig()
    small = cfg.levels(8192)                                                # 8 GB: the floors win
    assert (small["warn"], small["stop"], small["emergency"]) == (2048, 1536, 512)
    big = cfg.levels(64000)                                                 # 64 GB: the shares win
    assert (big["warn"], big["stop"], big["emergency"]) == (7680, 5120, 1280)
    assert cfg.levels(0)["warn"] == 2048


# ---- warning: hold, recovery, a second event ----
def test_warning_needs_five_seconds_just_below_the_level_and_not_four():
    gd = g.MemoryGuard()
    low = reading(LV["warn"] - 1)
    assert run_guard(gd, [(0, low), (4.9, low)]) == ["ok", "ok"]
    assert gd.feed(5.0, low) == "hold" and not gd.admitting()
    ok = g.MemoryGuard()
    assert run_guard(ok, [(0, reading(LV["warn"])), (10, reading(LV["warn"]))]) == ["ok", "ok"]            # exactly at the level is not below it


def test_a_brief_dip_resets_the_clock():
    gd = g.MemoryGuard()
    low, fine = reading(LV["warn"] - 1), reading(LV["warn"] + 100)
    assert run_guard(gd, [(0, low), (3, low), (3.5, fine), (4, low), (8.9, low)]) == ["ok"] * 5          # 4.9 s since it dipped again
    assert gd.feed(9.0, low) == "hold"


def test_recovery_needs_the_level_plus_one_gib_for_ten_seconds_and_only_once_per_model():
    gd = g.MemoryGuard()
    low, better = reading(LV["warn"] - 1), reading(LV["warn"] + 1024)
    gd.feed(0, low); gd.feed(5, low)
    assert gd.state == "hold"
    assert gd.feed(6, reading(LV["warn"] + 1023)) == "hold"                                               # one MiB short
    gd.feed(7, better); gd.feed(16.9, better)
    assert gd.state == "hold"
    assert gd.feed(17.0, better) == "ok" and gd.recoveries == 1 and [e["event"] for e in gd.events] == ["memory_warning", "memory_recovered"]
    gd.feed(20, low)
    assert gd.feed(25, low) == "stop" and gd.stopped["reason"] == g.R_REPEATED                           # a second pressure event stops the model


def test_a_hold_that_never_clears_stops_the_model_after_the_grace_and_the_give_up_time():
    cfg = g.GuardConfig()
    gd = g.MemoryGuard(cfg)
    low = reading(LV["warn"] - 1)
    gd.feed(0, low); gd.feed(5, low)
    assert gd.feed(5 + cfg.inflight_grace_s + cfg.hold_give_up_s - 1, low) == "hold"
    assert gd.feed(5 + cfg.inflight_grace_s + cfg.hold_give_up_s, low) == "stop" and gd.stopped["reason"] == g.R_HELD


# ---- stop and emergency ----
def test_stop_level_five_seconds_and_emergency_two_seconds():
    gd = g.MemoryGuard()
    low = reading(LV["stop"] - 1)
    assert run_guard(gd, [(0, low), (4.9, low)])[-1] != "stop"
    assert gd.feed(5.0, low) == "stop" and gd.stopped["reason"] == g.R_LOW and gd.stopped["threshold"] == LV["stop"] and gd.stopped["duration_s"] == 5.0
    em = g.MemoryGuard()
    crit = reading(LV["emergency"] - 1)
    assert run_guard(em, [(0, crit), (1.9, crit)])[-1] != "stop"
    assert em.feed(2.0, crit) == "stop" and em.stopped["reason"] == g.R_EMERGENCY
    just = g.MemoryGuard()
    assert run_guard(just, [(0, reading(LV["stop"])), (30, reading(LV["stop"]))])[-1] == "hold"            # at the stop level exactly: not below, but well below the warning level


def test_with_the_tick_off_only_the_emergency_floor_still_stops():
    off = g.MemoryGuard(enabled=False)
    assert run_guard(off, [(0, reading(LV["stop"] - 1)), (60, reading(LV["stop"] - 1))]) == ["ok", "ok"]    # no warning, no stop level
    assert run_guard(off, [(61, reading(LV["emergency"] - 1)), (63, reading(LV["emergency"] - 1))])[-1] == "stop"
    assert off.stopped["reason"] == g.R_EMERGENCY


# ---- commit charge, memory pressure, spill ----
def test_commit_charge_stops_only_with_ram_already_low_for_ten_seconds():
    low = LV["warn"] - 1
    gd = g.MemoryGuard()
    hot = reading(low, commit_used_mb=96000, commit_limit_mb=100000)
    assert run_guard(gd, [(0, hot), (9.9, hot)])[-1] != "stop"
    assert gd.feed(10.0, hot) == "stop" and gd.stopped["reason"] == g.R_COMMIT
    fine_ram = g.MemoryGuard()
    assert run_guard(fine_ram, [(0, reading(40000, commit_used_mb=99000, commit_limit_mb=100000)), (60, reading(40000, commit_used_mb=99000, commit_limit_mb=100000))]) == ["ok", "ok"]
    under = g.MemoryGuard()
    ok = reading(low, commit_used_mb=94000, commit_limit_mb=100000)                                       # 94%: under the line
    assert run_guard(under, [(0, ok), (11, ok)])[-1] != "stop"


def test_linux_memory_pressure_only_corroborates_low_ram():
    alone = g.MemoryGuard()
    assert run_guard(alone, [(0, reading(40000, psi_full_avg10=50.0)), (60, reading(40000, psi_full_avg10=50.0))]) == ["ok", "ok"]
    both = g.MemoryGuard()
    r = reading(LV["warn"] - 1, psi_full_avg10=11.0)
    assert run_guard(both, [(0, r), (9.9, r)])[-1] != "stop" and both.feed(10.0, r) == "stop"
    calm = g.MemoryGuard()
    r2 = reading(LV["warn"] - 1, psi_full_avg10=9.0)
    assert run_guard(calm, [(0, r2), (11, r2)])[-1] != "stop"


SPILL = dict(vram_used_mb=31500, vram_total_mb=32000, progress_age_s=31.0)


def test_spill_needs_the_full_card_the_engines_ram_growing_and_no_progress():
    gd = g.MemoryGuard()
    steps = [(0, reading(engine_ws_mb=10000, **SPILL)), (5, reading(engine_ws_mb=10300, **SPILL)), (9, reading(engine_ws_mb=10600, **SPILL)), (10, reading(engine_ws_mb=10600, **SPILL))]
    assert run_guard(gd, steps)[-1] == "stop" and gd.stopped["reason"] == g.R_SPILL
    for name, kw in (("card not full", dict(SPILL, vram_used_mb=30000)), ("progress continues", dict(SPILL, progress_age_s=5.0)), ("deliberate offload with progress", dict(SPILL, progress_age_s=0.0))):
        ok = g.MemoryGuard()
        out = run_guard(ok, [(0, reading(engine_ws_mb=10000, **kw)), (5, reading(engine_ws_mb=10300, **kw)), (9, reading(engine_ws_mb=10700, **kw)), (10, reading(engine_ws_mb=11000, **kw))])
        assert out[-1] == "ok", name
    flat = g.MemoryGuard()                                                                                 # the card is full, nothing moves, but the engine's RAM does not grow: shared memory alone never stops
    assert run_guard(flat, [(i, reading(engine_ws_mb=10000, **SPILL)) for i in range(0, 40, 5)])[-1] == "ok"
    slow = g.MemoryGuard()                                                                                 # growth spread over more than 10 s is not a rise of 512 MiB in 10 s
    out = run_guard(slow, [(i, reading(engine_ws_mb=10000 + i * 20, **SPILL)) for i in range(0, 60, 5)])
    assert out[-1] == "ok"


# ---- bad readings ----
@pytest.mark.parametrize("bad", [None, {}, {"available_mb": None, "total_mb": TOTAL}, {"available_mb": "lots", "total_mb": TOTAL}, {"available_mb": float("nan"), "total_mb": TOTAL},
                                 {"available_mb": True, "total_mb": TOTAL}, {"available_mb": 5000, "total_mb": 0}])
def test_a_missing_stale_or_invalid_reading_is_never_healthy_and_never_a_stop(bad):
    gd = g.MemoryGuard()
    assert run_guard(gd, [(0, bad), (30, bad)]) == ["ok", "ok"]
    gd.feed(40, reading(LV["warn"] - 1))
    gd.feed(45, reading(LV["warn"] - 1))
    assert gd.state == "hold"
    gd.feed(46, reading(LV["warn"] + 5000))
    gd.feed(50, bad)                                                                                       # a gap breaks the recovery run: it starts again
    gd.feed(56.5, reading(LV["warn"] + 5000))
    assert gd.feed(60, reading(LV["warn"] + 5000)) == "hold"
    assert gd.feed(66.6, reading(LV["warn"] + 5000)) == "ok"


def test_a_pressure_already_running_is_not_cleared_by_a_missing_reading():
    gd = g.MemoryGuard()
    low = reading(LV["stop"] - 1)
    gd.feed(0, low)
    gd.feed(3, None)
    assert gd.feed(5.0, low) == "stop"


def test_a_huge_healthy_machine_and_big_working_sets_never_stop():
    gd = g.MemoryGuard()
    for t in range(0, 600, 5):
        assert gd.feed(float(t), reading(300000, total=512000, engine_ws_mb=200000, commit_used_mb=80, commit_limit_mb=100, vram_used_mb=31900, vram_total_mb=32000, progress_age_s=1.0)) == "ok"


def test_the_samples_before_a_stop_are_kept_for_thirty_seconds():
    gd = g.MemoryGuard()
    for t in range(0, 41):
        gd.feed(float(t), reading(40000 - t))
    gd.feed(41.0, reading(100)); gd.feed(43.0, reading(100))
    s = gd.stopped
    ts = [x["t"] for x in s["samples_before"]]
    assert max(ts) == 43.0 and min(ts) >= 13.0 and len(ts) >= 30 and s["reading"]["available_mb"] == 100


# ---- the gate between models, and the record ----
def test_the_next_model_waits_for_memory_commit_and_room_on_the_card():
    cfg = g.GuardConfig()
    ready = reading(LV["warn"] + 1024)
    assert g.recovered_for_next_model(ready, cfg) is True
    assert g.recovered_for_next_model(reading(LV["warn"] + 1023), cfg) is False
    assert g.recovered_for_next_model(None, cfg) is False and g.recovered_for_next_model({"available_mb": None, "total_mb": TOTAL}, cfg) is False
    assert g.recovered_for_next_model(dict(ready, commit_used_mb=96, commit_limit_mb=100), cfg) is False
    assert g.recovered_for_next_model(dict(ready, vram_used_mb=30000, vram_total_mb=32000), cfg, need_vram_mb=5000) is False
    assert g.recovered_for_next_model(dict(ready, vram_used_mb=20000, vram_total_mb=32000), cfg, need_vram_mb=5000) is True       # it need not return to the earlier figure, only have room


def test_memory_that_does_not_come_back_after_unload_is_flagged():
    cfg = g.GuardConfig()
    assert g.memory_not_released({"used_mb": 30000, "total_mb": TOTAL}, {"used_mb": 33000, "total_mb": TOTAL}, cfg) is None             # 3 GB is under the larger of 1 GB and 5% (3.2 GB)
    out = g.memory_not_released({"used_mb": 30000, "total_mb": TOTAL}, {"used_mb": 34000, "total_mb": TOTAL}, cfg)
    assert out == {"before_mb": 30000, "after_mb": 34000, "extra_mb": 4000, "allowed_mb": 3200}
    assert g.memory_not_released({"used_mb": 1000, "total_mb": 8000}, {"used_mb": 2500, "total_mb": 8000}, cfg)["allowed_mb"] == 1024
    assert g.memory_not_released(None, {"used_mb": 5, "total_mb": 8}, cfg) is None


# ======================================================================================================================================================================
# the owned process
# ======================================================================================================================================================================

class FakeOps(ow.SystemOps):
    """A fake operating system: records every call so a test can say what was (not) ended."""

    def __init__(self, pid=4242, started=100.0, exe="llama-server.exe", survives=False, child_survives=False, deny_ask=False, deny_kill=False):
        self.calls = []
        self.procs = {pid: ow.Identity(pid, started, exe)}
        self.pid, self.survives, self.child_survives, self.deny_ask, self.deny_kill = pid, survives, child_survives, deny_ask, deny_kill
        self.alive_members = {"main", "child"}

    def identity(self, pid):
        return self.procs.get(pid)

    def make_container(self, pid):
        return {"job": pid}

    def ask_exit(self, pid, container):
        self.calls.append(("ask_exit", pid))
        if self.deny_ask:
            raise PermissionError("denied")
        if not self.survives:
            self.procs.pop(pid, None)
            self.alive_members.discard("main")
            if not self.child_survives:
                self.alive_members.discard("child")

    def kill_container(self, container):
        self.calls.append(("kill_container", container["job"]))
        if self.deny_kill:
            raise PermissionError("denied")
        self.procs.pop(self.pid, None)
        self.alive_members.clear()

    def container_alive(self, container):
        return bool(self.alive_members)

    def close_container(self, container):
        self.calls.append(("close_container", container["job"]))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


def stop_with(ops, **kw):
    clock = Clock()
    p = ow.OwnedProcess(ops.pid, ops)
    if "reused_by" in kw:
        ops.procs[ops.pid] = kw.pop("reused_by")
    return asyncio.run(p.stop(sleep=clock.sleep, clock=clock, **kw))


def test_a_polite_exit_is_enough_when_everything_leaves():
    ops = FakeOps()
    out = stop_with(ops)
    assert out == {"graceful": True, "forced": False, "owned_gone": True, "identity_mismatch": False, "error": ""} and ("kill_container", 4242) not in ops.calls


def test_the_whole_owned_tree_is_ended_by_force_when_the_engine_does_not_leave():
    ops = FakeOps(survives=True)
    out = stop_with(ops)
    assert out["forced"] is True and out["owned_gone"] is True and ("kill_container", 4242) in ops.calls


def test_a_child_that_outlives_its_parent_is_ended_too():
    ops = FakeOps(child_survives=True)
    out = stop_with(ops)
    assert out["graceful"] and out["forced"] and out["owned_gone"] and ops.alive_members == set()


def test_a_reused_process_id_is_never_touched():
    ops = FakeOps()
    out = stop_with(ops, reused_by=ow.Identity(4242, 999.0, "chrome.exe"))
    assert out["identity_mismatch"] is True and not any(c[0] in ("ask_exit", "kill_container") for c in ops.calls)         # no termination call of any kind
    again = FakeOps()
    out = stop_with(again, reused_by=ow.Identity(4242, 100.0, "notepad.exe"))                                              # same id and time, another program
    assert out["identity_mismatch"] is True and not any(c[0] in ("ask_exit", "kill_container") for c in again.calls)


def test_a_permission_failure_is_reported_and_nothing_else_is_ended():
    ops = FakeOps(survives=True, deny_ask=True, deny_kill=True)
    out = stop_with(ops, gone_s=3.0)
    assert "could not end the engine" in out["error"] and out["owned_gone"] is False
    assert {c[0] for c in ops.calls} <= {"ask_exit", "kill_container", "close_container"} and all(c[1] == 4242 for c in ops.calls if c[0] != "close_container" or True)


def test_no_program_name_or_port_is_ever_used_to_end_anything():
    text = open(ow.__file__, encoding="utf-8").read().lower()
    for bad in ("taskkill", "pkill", "killall", "/im ", "netstat", "psutil"):
        assert bad not in text, bad


@pytest.mark.skipif(sys.platform not in ("win32", "linux"), reason="needs a job object or a process group")
def test_a_real_throw_away_engine_and_its_child_are_both_gone_after_stop(tmp_path):
    """Starts a harmless python that starts another harmless python (both only sleep), puts them in the owned container, stops them, and checks both ids are gone."""
    import subprocess
    script = tmp_path / "sleeper.py"
    script.write_text("import subprocess, sys, time\nsys.stdin.readline()\nc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\nprint(c.pid, flush=True)\ntime.sleep(120)\n", encoding="utf-8")
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {"start_new_session": True}
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stdin=subprocess.PIPE, text=True, **kw)
    try:
        owned = ow.OwnedProcess(proc.pid)                                                                            # the container first, so what the engine starts later is inside it
        if owned.container is None:
            pytest.skip("this system would not make a container")
        proc.stdin.write("go\n")
        proc.stdin.flush()
        child_pid = int(proc.stdout.readline().strip())
        assert owned.identity is not None and owned.ops.container_alive(owned.container) and owned.ops.identity(child_pid) is not None
        out = asyncio.run(owned.stop(process_gone=lambda: proc.poll() is not None))
        assert out["owned_gone"] is True and not out["identity_mismatch"]
        proc.wait(timeout=10)
        assert owned.ops.identity(child_pid) is None or sys.platform != "win32"                                          # (a Linux zombie child may linger until reaped by init)
        assert proc.poll() is not None
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()                                                                                              # this test's own child, by the handle it started


# ======================================================================================================================================================================
# the runner
# ======================================================================================================================================================================

FAST = g.GuardConfig(warn_hold_s=0.15, stop_hold_s=0.15, emergency_hold_s=0.06, recover_hold_s=0.15, inflight_grace_s=0.1, hold_give_up_s=0.4, settle_s=0.0, gate_hold_s=0.05,
                     gate_timeout_s=0.6, stale_after_s=1.0, model_time_limit_s=600.0)


class Mem:
    """What the computer's memory is doing, set by the test; read by the sampler (during a run) and by the gate (between runs)."""

    def __init__(self, avail=40000, total=TOTAL):
        self.avail, self.total = avail, total
        self.used_extra = 0
        self.launcher = None
        self.by_start = None                                   # a function of the number of engines started: the available memory then

    def available(self):
        return self.by_start(self.launcher.starts) if self.by_start and self.launcher else self.avail

    def machine(self):
        a = self.available()
        return {"cpu_util_pct": 5.0, "ram_used_mb": self.total - a if a is not None else None, "ram_total_mb": self.total, "ram_available_mb": a, "commit_used_mb": None, "commit_limit_mb": None}

    def now(self):
        a = self.available()
        return {"available_mb": a, "total_mb": self.total, "used_mb": self.total - a if a is not None else None, "vram_used_mb": None, "vram_total_mb": None}


def make(env, mem, with_gate=True, **kw):
    mem.launcher = env["launcher"]
    hooks = br.Hooks()
    card = env["card"]
    hooks.sampler_factory = lambda on_sample, plan: bsm.ReadingsSampler(on_sample=on_sample, runner=card, interval_s=0.03, machine_reader=mem.machine, ceiling_c=plan.ceiling_c)
    hooks.version = "test"
    if with_gate:
        hooks.memory_now = mem.now
    kw.setdefault("heartbeat_s", 0.05)
    kw.setdefault("thermal_poll_s", 0.03)
    kw.setdefault("guard", FAST)
    kw.setdefault("guard_poll_s", 0.02)
    return br.BenchmarkService(env["base"], env["launcher"], hooks, **kw)


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "benchmarks"
    card = FakeCard()
    launcher = FakeLauncher(card, engine_kwargs=lambda n: {"token_s": 0.01, "tokens": 12})
    yield {"base": base, "tmp": tmp_path, "card": card, "launcher": launcher, "models": [write_model(tmp_path, f"m{i}.gguf") for i in range(3)]}
    for e in launcher.engines:
        e.stop()


def runs_of(base):
    out = []
    for p in sorted(base.iterdir()):
        if p.is_dir() and (p / bs.MANIFEST).exists():
            out.append(bs.read_manifest(p))
    return sorted(out, key=lambda m: m["model_index"])


async def finish(svc, body, timeout=40):
    res = await svc.start(body)
    assert res["ok"], res
    await asyncio.wait_for(svc.wait(), timeout)


def test_a_model_running_the_pc_out_of_memory_is_stopped_on_its_own_and_the_next_one_goes_ahead(env):
    mem = Mem()
    mem.by_start = lambda starts: 900 if env["launcher"].stops == 0 else 40000                   # the first model's engine runs the PC out of memory (900 MB of 64,000 is under the emergency floor); stopping it frees it

    async def go():
        svc = make(env, mem)
        await finish(svc, plan_body(env["models"][:2], levels=[1, 4], requests_per_level=20, sustained=False, cooldown=False))
        return svc
    svc = asyncio.run(go())
    first, second = runs_of(env["base"])
    assert first["status"] == bc.RUN_STOPPED_MEMORY and first["reason"] == g.R_EMERGENCY
    assert second["status"] == bc.RUN_COMPLETED
    stop = first["safety_stop"]
    assert stop["kind"] == "memory" and stop["reason"] == g.R_EMERGENCY and stop["duration_s"] >= FAST.emergency_hold_s and stop["threshold"] == round(LV_FAST_EMERGENCY())
    assert stop["samples_before"] and stop["reading"]["available_mb"] == 900 and stop["cleanup"] == {} and stop["requests"]["finished"] >= 0
    assert stop["concurrency"] in (None, 1, 4) and stop["engine"]["slots"] in (None, 1, 4) and "observed" in stop              # (None: it was stopped while the model was still loading)
    assert first["protections"]["memory_guard"] is True and first["protections"]["emergency_floor"] is True and first["memory_guard"]["constants"]["emergency_floor_mb"] == 512
    assert svc.status()["stops"] == 1 and env["launcher"].stops >= 2 and env["launcher"].starts >= 2


def LV_FAST_EMERGENCY():
    return FAST.levels(TOTAL)["emergency"]


def test_no_request_is_sent_and_no_engine_restarted_after_a_safety_stop(env):
    mem = Mem(avail=500)

    async def go():
        svc = make(env, mem)
        await finish(svc, plan_body(env["models"][:1], levels=[1, 4, 8], requests_per_level=30, sustained=False, cooldown=False))
    asyncio.run(go())
    (m,) = runs_of(env["base"])
    folder = next(p for p in env["base"].iterdir() if p.is_dir())
    rows = read_rows(folder, bs.REQUESTS)
    assert m["status"] == bc.RUN_STOPPED_MEMORY
    n = len(rows)
    assert env["launcher"].starts == 1 and n < 30 * 3                                           # one engine; the test did not go on to the later levels
    assert m["steps_done"] < m["steps_total"]


def test_when_memory_does_not_recover_there_is_no_next_model_and_the_batch_says_so(env):
    mem = Mem()
    mem.by_start = lambda starts: 700 if starts == 1 else 700                                    # never recovers

    async def go():
        svc = make(env, mem)
        await finish(svc, plan_body(env["models"], levels=[1], requests_per_level=20, sustained=False, cooldown=False))
        return svc
    svc = asyncio.run(go())
    runs = runs_of(env["base"])
    st = svc.status()
    assert len(runs) == 1 and runs[0]["status"] == bc.RUN_STOPPED_MEMORY and env["launcher"].starts == 1
    assert st["batch_stopped"] == "memory_not_recovered" and st["message"] == br.BATCH_STOP_MEMORY and st["running"] is False and "waiting_memory" not in st


def test_the_live_status_says_it_is_waiting_while_memory_frees_up_and_then_goes_on(env):
    mem = Mem()
    calls = {"n": 0}

    def now():
        calls["n"] += 1
        a = 700 if calls["n"] < 8 else 40000                                                       # a few looks later the memory is back
        return {"available_mb": a, "total_mb": TOTAL, "used_mb": TOTAL - a, "vram_used_mb": None, "vram_total_mb": None}
    mem.by_start = lambda starts: 700 if starts == 1 else 40000
    seen = []

    async def go():
        svc = make(env, mem)
        svc.hooks.memory_now = now
        await svc.start(plan_body(env["models"][:2], levels=[1], requests_per_level=20, sustained=False, cooldown=False))
        while svc.running():
            seen.append(svc.status().get("waiting_memory"))
            await asyncio.sleep(0.01)
        return svc
    svc = asyncio.run(go())
    assert True in seen                                                                              # "Waiting for the PC's memory to free up" was on screen
    assert [m["status"] for m in runs_of(env["base"])] == [bc.RUN_STOPPED_MEMORY, bc.RUN_COMPLETED] and "batch_stopped" not in svc.status()


def test_the_engine_that_cannot_be_fully_stopped_ends_the_batch(env):
    mem = Mem(avail=400)
    real_stop = None

    async def go():
        nonlocal real_stop
        svc = make(env, mem)
        orig = env["launcher"].start

        async def start(*a, **k):
            h = await orig(*a, **k)
            inner = h.stop

            async def stop():
                await inner()
                return {"graceful": True, "forced": True, "owned_gone": False, "identity_mismatch": False, "error": "still running"}
            h.stop = stop
            return h
        env["launcher"].start = start
        await finish(svc, plan_body(env["models"], levels=[1], requests_per_level=20, sustained=False, cooldown=False))
        return svc
    svc = asyncio.run(go())
    assert svc.status()["batch_stopped"] == "cleanup" and svc.status()["message"] == br.BATCH_STOP_CLEANUP and env["launcher"].starts == 1


def test_with_the_tick_off_low_memory_does_not_stop_the_test_but_the_emergency_floor_still_does(env):
    mem = Mem(avail=3000)                                                                          # below the stop level (5,120), above the emergency floor (1,280)

    async def go(avail, **plan):
        mem.avail = avail
        svc = make(env, mem)
        await finish(svc, plan_body(env["models"][:1], levels=[1], requests_per_level=15, sustained=False, cooldown=False, **plan))
    asyncio.run(go(3000, memory_guard=False))
    assert runs_of(env["base"])[0]["status"] == bc.RUN_COMPLETED and runs_of(env["base"])[0]["protections"]["memory_guard"] is False
    import shutil
    for p in env["base"].iterdir():
        if p.is_dir():
            shutil.rmtree(p)
    asyncio.run(go(400, memory_guard=False))
    m = runs_of(env["base"])[0]
    assert m["status"] == bc.RUN_STOPPED_MEMORY and m["reason"] == g.R_EMERGENCY and m["protections"]["memory_guard"] is False and m["protections"]["heat_stop"] is True


def test_a_short_dip_holds_new_requests_then_the_test_goes_on(env):
    mem = Mem()
    t0 = {}

    def avail(starts):
        import time
        t0.setdefault("t", time.monotonic())
        dt = time.monotonic() - t0["t"]
        return 6000 if 0.3 < dt < 0.6 else 40000                                                   # below the warning level (7,680) for 0.3 s, then fine
    mem.by_start = avail

    async def go():
        svc = make(env, mem)
        await finish(svc, plan_body(env["models"][:1], levels=[1], requests_per_level=40, sustained=False, cooldown=False))
    asyncio.run(go())
    (m,) = runs_of(env["base"])
    assert m["status"] == bc.RUN_COMPLETED
    assert [e["event"] for e in m["memory_guard"]["events"]] == ["memory_warning", "memory_recovered"] and m["memory_guard"]["recoveries"] == 1


def test_missing_memory_readings_never_stop_a_test(env):
    mem = Mem()
    mem.by_start = lambda starts: None

    async def go():
        svc = make(env, mem, with_gate=False)
        await finish(svc, plan_body(env["models"][:1], levels=[1], requests_per_level=10, sustained=False, cooldown=False))
    asyncio.run(go())
    assert runs_of(env["base"])[0]["status"] == bc.RUN_COMPLETED


def test_the_time_limit_stops_a_model_that_takes_too_long_and_is_not_switched_off_by_the_tick(env):
    mem = Mem()
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.05, "tokens": 40}                     # slow engine

    async def go():
        svc = make(env, mem, guard=g.GuardConfig(**dict(FAST.as_dict(), model_time_limit_s=0.6)))
        await finish(svc, plan_body(env["models"][:1], levels=[1], requests_per_level=60, sustained=False, cooldown=False, memory_guard=False))
    asyncio.run(go())
    (m,) = runs_of(env["base"])
    assert m["status"] == bc.RUN_STOPPED_TIME and m["reason"] == g.R_TOO_LONG and m["safety_stop"]["kind"] == "time" and m["safety_stop"]["limit_s"] == 0.6


def test_only_one_stop_result_is_stored_when_several_things_arrive_together(env):
    svc = make(env, Mem())
    assert svc.claim("memory", {"reason": g.R_LOW}) is True
    assert svc.claim("time", {}) is False and svc.claim("thermal", {}) is False and svc.claim("cancel") is False
    assert svc._claim["kind"] == "memory"
    mem = Mem(avail=500)
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.05, "tokens": 40}

    async def go():
        svc2 = make(env, mem, guard=g.GuardConfig(**dict(FAST.as_dict(), model_time_limit_s=0.12)))
        res = await svc2.start(plan_body(env["models"][:1], levels=[1], requests_per_level=60, sustained=False, cooldown=False))
        assert res["ok"]
        await asyncio.sleep(0.08)
        await svc2.cancel()                                                                         # a person's Stop lands in the same moment
        await svc2.wait()
    asyncio.run(go())
    (m,) = runs_of(env["base"])
    assert m["status"] in (bc.RUN_STOPPED_MEMORY, bc.RUN_STOPPED_TIME, bc.RUN_CANCELLED)
    kinds = {bc.RUN_STOPPED_MEMORY: "memory", bc.RUN_STOPPED_TIME: "time"}
    if m["status"] in kinds:
        assert m["safety_stop"]["kind"] == kinds[m["status"]]                                       # the stored result is the one that came first, and only that one


def test_all_eight_requests_outstanding_do_not_block_the_stop_or_the_cleanup(env):
    mem = Mem()
    mem.by_start = lambda starts: 450 if env["launcher"].engines and env["launcher"].engines[0].inflight >= 4 else 40000        # low once several requests are outstanding
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.2, "tokens": 50, "first_s": 1.0}      # every request is slow

    async def go():
        svc = make(env, mem)
        await asyncio.wait_for(finish(svc, plan_body(env["models"][:1], levels=[8], requests_per_level=16, sustained=False, cooldown=False, warmup=0)), 20)
    asyncio.run(go())
    (m,) = runs_of(env["base"])
    assert m["status"] == bc.RUN_STOPPED_MEMORY and m["safety_stop"]["requests_in_flight_cancelled"] >= 1 and env["launcher"].stops >= 1


def test_memory_before_the_load_and_after_the_unload_is_recorded_and_a_leak_is_flagged(env):
    mem = Mem()
    mem.by_start = lambda starts: 40000

    class Leaky(Mem):
        pass
    calls = {"n": 0}

    def now():
        calls["n"] += 1
        used = 30000 if calls["n"] == 1 else 36000                                                  # the first look is before the load, a later one after the unload: 6 GB more
        return {"available_mb": TOTAL - used, "total_mb": TOTAL, "used_mb": used, "vram_used_mb": None, "vram_total_mb": None}

    async def go():
        svc = make(env, mem)
        svc.hooks.memory_now = now
        logs = []
        svc.log = logs.append
        await finish(svc, plan_body(env["models"][:1], levels=[1], requests_per_level=10, sustained=False, cooldown=False))
        return logs
    logs = asyncio.run(go())
    (m,) = runs_of(env["base"])
    mm = m["machine_memory"]
    assert mm["before_load"]["used_mb"] == 30000 and mm["after_unload"]["used_mb"] == 36000 and mm["in_use_highest_mb"] is not None and mm["available_lowest_mb"] is not None
    assert m["memory_not_released"] == {"before_mb": 30000, "after_mb": 36000, "extra_mb": 6000, "allowed_mb": 3200}
    assert any("memory did not return after unload: before 30000 MB, after 36000 MB" in x for x in logs)


def test_the_plan_records_the_tick(tmp_path):
    model = write_model(tmp_path, "p.gguf")
    plan = br.parse_plan({"models": [model], "memory_guard": False})
    assert plan.memory_guard is False and plan.requested()["memory_guard"] is False
    assert br.parse_plan({"models": [model]}).memory_guard is True and br.parse_plan({"models": [model]}).requested()["memory_guard"] is True
