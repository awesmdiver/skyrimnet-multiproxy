"""Benchmarks Chat test runner against a fake engine and a fake card (no real engine, game, card or director settings)."""
import asyncio
import json
import time

import pytest

import benchmarks_contract as bc
import benchmarks_engine as be
import benchmarks_runner as br
import benchmarks_sampler as bsm
import benchmarks_store as bs
from benchmarks_fakes import FakeCard, FakeLauncher, make_service, plan_body, read_rows, wait_for, write_model


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "benchmarks"
    model = write_model(tmp_path)
    card = FakeCard()
    launcher = FakeLauncher(card)
    yield {"base": base, "model": model, "card": card, "launcher": launcher, "tmp": tmp_path}
    for e in launcher.engines:
        e.stop()


def only_run(base):
    runs = [p for p in base.iterdir() if p.is_dir()]
    assert len(runs) == 1
    return runs[0]


def run(coro):
    return asyncio.run(coro)


async def go(env, body, **kw):
    svc = make_service(env["base"], env["launcher"], env["card"], **kw)
    res = await svc.start(body)
    assert res["ok"], res
    await svc.wait()
    return svc


# --- the whole run ---
def test_whole_run_two_levels_with_restart_warmup_sustained_and_phases(env):
    body = plan_body([env["model"]], levels=[1, 4], requests_per_level=6, sustained=True, sustained_s=0.5)
    svc = run(go(env, body))
    folder = only_run(env["base"])
    m = bs.read_manifest(folder)
    assert m["status"] == "completed" and m["steps_done"] == m["steps_total"] == 6 and m["profile"] == "custom" and m["test_set_version"] == "v1"
    assert m["kind"] == "chat" and m["model_id"].startswith("sha256:") and m["setup_id_final"] is True and m["load_time_s"] > 0
    assert m["resolved_settings"]["layers_on_card"] == 33 and m["engine"]["build"] == "b-fake"
    rows = read_rows(folder, bs.REQUESTS)
    warm = [r for r in rows if r["warmup"]]
    assert len(warm) == 4                                                       # 2 after the first load, 2 after the restart to 4 slots
    real = [r for r in rows if not r["warmup"] and not r.get("sustained")]
    assert len([r for r in real if r["level"] == 1]) == 6 and len([r for r in real if r["level"] == 4]) == 6
    assert all(r["status"] == "ok" and r["ttft_ms"] > 0 and r["generation_tok_s"] > 0 and r["prompt_tok_s"] == 2000.0 for r in real)
    summary = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))
    l1, l4 = summary["levels"]["1"], summary["levels"]["4"]
    assert l1["completed"] == 6 and l1["slots"] == 1 and l1["actual_overlap"] == 1
    assert l1["engine"]["slots"] == 1 and l1["engine"]["layers_on_card"] == 33 and summary["levels"]["4"]["engine"]["slots"] == 4         # what each level really ran on is kept
    assert l4["completed"] == 6 and l4["slots"] == 4 and l4["actual_overlap"] == 4
    assert l1["generation_tok_s"]["n"] == 6 and l1["generation_tok_s"]["p95"] == bc.NOT_ENOUGH_REQUESTS       # warm-ups are not in any statistic
    assert [e["step"] for e in summary["engine_steps"]] == ["engine_restart"] and summary["engine_steps"][0]["slots_after"] == 4
    assert env["launcher"].slots_seen == [1, 4] and env["launcher"].starts == 2
    assert summary["sustained"]["status"] in ("completed", "slowdown_detected") and summary["sustained"]["rule"]["duration_s"] == 0.5
    mem = summary["memory"]["0"]
    assert mem["vram_idle_before_load_mb"] == 1000 and mem["vram_highest_overall_mb"] == 9000 and mem["model_vram_delta_mb"] == 8000 and mem["scope"].startswith("whole card")
    assert summary["headroom"][0]["kind"] == "estimate" and summary["headroom"][0]["dot"] in ("green", "amber", "red", "grey")
    samples = read_rows(folder, bs.SAMPLES)
    phases = {s["phase"] for s in samples}
    assert {"idle_before_load", "after_load", "level_1", "level_4", "sustained", "unloaded"} <= phases
    assert any(s["kind"] == "gpu" and s["gpu_index"] == 0 and s["t_limit_margin_c"] == 55.0 for s in samples)
    assert bs.read_manifest(folder)["measurement_availability"]["ttft_ms"]["status"] == "measured"
    assert m["start_temp_c"] == {"0": 40.0} or bs.read_manifest(folder)["start_temp_c"] == {"0": 40.0}
    assert svc.status()["running"] is False and svc.status()["runs"][0]["status"] == "completed"


def test_n_measured_completions_with_errors_counted_apart(env):
    env["launcher"].engine_kwargs = lambda n: {"fail_every": 3}
    run(go(env, plan_body([env["model"]], requests_per_level=5)))
    folder = only_run(env["base"])
    summary = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))["levels"]["1"]
    assert summary["completed"] == 5 and summary["failed"] >= 1 and summary["timed_out"] == 0
    rows = [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"]]
    assert sum(1 for r in rows if r["status"] == "ok") == 5 and sum(1 for r in rows if r["status"] == "error") == summary["failed"]
    assert bs.read_manifest(folder)["status"] == "completed"


def test_a_level_keeps_its_concurrency_and_drops_leftovers(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.01, "tokens": 6}
    run(go(env, plan_body([env["model"]], levels=[4], requests_per_level=9)))
    folder = only_run(env["base"])
    rows = [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"]]
    assert len(rows) == 9                                                         # the requests still in flight at the 9th completion are dropped, not recorded
    assert env["launcher"].engines[0].peak == 4


def test_timeout_is_counted_apart(env):
    env["launcher"].engine_kwargs = lambda n: {"hang": True}
    body = plan_body([env["model"]], requests_per_level=2, request_timeout_s=0.3, warmup=0)
    svc = run(go(env, body))
    folder = only_run(env["base"])
    m = bs.read_manifest(folder)
    assert m["status"] == "failed" and "too many requests failed" in m["reason"]
    rows = read_rows(folder, bs.REQUESTS)
    assert rows and all(r["status"] == "timeout" for r in rows)
    summary = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))["levels"]["1"]
    assert summary["timed_out"] == len(rows) and summary["completed"] == 0


def test_missing_stream_and_timings_are_not_reported_never_derived(env):
    def kw(n):
        return {"first_s": 0.01}
    env["launcher"].engine_kwargs = kw
    orig = FakeLauncher.start

    async def start(self, *a, **k):
        h = await orig(self, *a, **k)
        h.engine.timings = False
        return h
    FakeLauncher.start = start
    try:
        run(go(env, plan_body([env["model"]], requests_per_level=3)))
    finally:
        FakeLauncher.start = orig
    folder = only_run(env["base"])
    rows = [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"]]
    assert all(r["generation_tok_s"] is None and r["prompt_tok_s"] is None and r["ttft_ms"] is not None for r in rows)
    av = bs.read_manifest(folder)["measurement_availability"]
    assert av["generation_tok_s"]["status"] == "not_reported" and av["prompt_tok_s"]["status"] == "not_reported"


# --- stop, cancel, heat, a dead engine ---
def test_cancel_mid_level_keeps_what_was_measured_and_unloads(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 10}

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"])
        assert (await svc.start(plan_body([env["model"]], levels=[4], requests_per_level=60)))["ok"]
        assert await wait_for(lambda: svc.status().get("step") == "level:4")
        await asyncio.sleep(0.5)
        assert await svc.cancel()
        await svc.wait()
        return svc
    svc = run(main())
    folder = only_run(env["base"])
    m = bs.read_manifest(folder)
    assert m["status"] == "cancelled" and m["steps_done"] < m["steps_total"]
    assert env["launcher"].stops == env["launcher"].starts == 1 and not env["launcher"].engines[0].running
    assert [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"]]
    assert svc.status()["running"] is False


def test_thermal_stop_on_a_margin_card_interrupts_the_run(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 10}

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"])
        assert (await svc.start(plan_body([env["model"], env["model"]], levels=[4], requests_per_level=80)))["ok"]
        assert await wait_for(lambda: svc.status().get("step") == "level:4")
        env["card"].margin_c = 0                                                  # the card says it has reached its maximum operating temperature
        await svc.wait()
        return svc
    svc = run(main())
    folder = [p for p in env["base"].iterdir() if p.is_dir()]
    assert len(folder) == 1                                                       # the batch stops: the second model never starts
    m = bs.read_manifest(folder[0])
    assert m["status"] == "interrupted" and m["reason"] == "thermal_stop"
    assert m["thermal_stop_reading"]["limit_source"] == "card margin" and m["thermal_stop_reading"]["margin_c"] == 0.0
    assert m["thermal_basis"] == {"0": "margin"} and env["launcher"].stops == 1


def test_a_card_with_no_limit_never_stops_the_run(env):
    env["card"].thermal = "GPU 0000\n    Temperature\n"
    env["card"].temp_c, env["card"].margin_c = 99.0, 0
    run(go(env, plan_body([env["model"]])))
    m = bs.read_manifest(only_run(env["base"]))
    assert m["status"] == "completed" and m["thermal_basis"] == {"0": "none"}
    assert m["measurement_availability"]["thermal_stop"]["status"] == "not_measured"


def test_an_engine_that_dies_fails_the_step_and_the_run_carries_on(env):
    first = {"done": False}

    def kw(n):
        return {"die_after": 3} if n == 1 else {}
    env["launcher"].engine_kwargs = kw
    svc = run(go(env, plan_body([env["model"], env["model"]], requests_per_level=5)))
    runs = sorted((bs.read_manifest(p) for p in env["base"].iterdir() if p.is_dir()), key=lambda m: m["model_index"])
    assert [m["status"] for m in runs] == ["failed", "completed"]
    assert "The engine stopped" in runs[0]["reason"] and "out of memory" in runs[0]["reason"]
    assert [r["status"] for r in svc.status()["runs"]] == ["failed", "completed"]


def test_an_engine_that_will_not_start_fails_that_model(env):
    env["launcher"].fail_start = "The model wouldn't start: no room."
    run(go(env, plan_body([env["model"]])))
    m = bs.read_manifest(only_run(env["base"]))
    assert m["status"] == "failed" and "no room" in m["reason"]


# --- restart recovery ---
def test_program_closing_mid_run_leaves_it_interrupted_and_no_engine(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 10}

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"])
        assert (await svc.start(plan_body([env["model"]], levels=[4], requests_per_level=60)))["ok"]
        assert await wait_for(lambda: svc.status().get("step") == "level:4")
        await svc.shutdown()
    run(main())
    m = bs.read_manifest(only_run(env["base"]))
    assert m["status"] == "interrupted" and m["reason"] == "program_stopped" and not env["launcher"].engines[0].running


def test_a_crash_is_found_at_the_next_start(env, monkeypatch):
    folder = bs.create_run(env["base"], "20261027T000000Z-aa", {"started_epoch_s": 1.0, "profile": "standard", "kind": "chat", "steps_total": 8})
    bs.write_progress(folder, "level:4", 3, 8, 5.0)
    bs.write_json(folder, bs.SUMMARY, {"kept": True})
    assert br.mark_unfinished_interrupted(env["base"]) == 1
    m = bs.read_manifest(folder)
    assert m["status"] == "interrupted" and m["reason"] == "program_stopped" and m["steps_done"] == 3
    assert json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8")) == {"kept": True}
    assert br.mark_unfinished_interrupted(env["base"]) == 0
    # the leftover engine: only the recorded one, only if it is still a llama-server
    killed = []
    monkeypatch.setattr(be.local_models, "_pid_is_llama_server", lambda pid: pid == 4242)
    monkeypatch.setattr(be.local_models, "kill_tree_sync", lambda pid: killed.append(pid))
    (env["base"] / be.PID_FILE).write_text(json.dumps({"pid": 4242, "port": 1, "run_id": "x"}), encoding="utf-8")
    assert be.stop_leftover_engine(env["base"]) is True and killed == [4242] and not (env["base"] / be.PID_FILE).exists()
    (env["base"] / be.PID_FILE).write_text(json.dumps({"pid": 777}), encoding="utf-8")
    assert be.stop_leftover_engine(env["base"]) is False and killed == [4242]          # a reused process id is left alone
    assert be.stop_leftover_engine(env["base"]) is False


# --- refusals ---
def test_refusal_reasons(env):
    wall = {"now": 1000.0}
    hooks = br.Hooks()
    svc = make_service(env["base"], env["launcher"], env["card"], hooks=hooks, wall=lambda: wall["now"])
    body = plan_body([env["model"]])

    async def main():
        hooks.last_game_request_at = lambda: 950.0
        r = await svc.start(body)
        assert not r["ok"] and r["message"] == br.REFUSE_RECENT
        hooks.last_game_request_at = lambda: 800.0
        hooks.game_running = lambda: True
        r = await svc.start(dict(body, skyrim="closed"))
        assert r["message"] == br.REFUSE_GAME
        assert (await svc.start(plan_body([env["tmp"].as_posix() + "/nope.gguf"])))["message"] == br.PLAN_NOT_GGUF
        txt = env["tmp"] / "a.txt"
        txt.write_text("x")
        assert (await svc.start(plan_body([str(txt)])))["message"] == br.PLAN_NOT_GGUF
        assert (await svc.start({"models": []}))["message"] == br.PLAN_NO_MODELS
        hooks.fit_check = lambda p: "too_big"
        assert "too big" in (await svc.start(body))["message"]
        hooks.fit_check = lambda p: None
        hooks.free_disk_mb = lambda p: 5
        assert (await svc.start(body))["message"] == br.REFUSE_DISK
        hooks.free_disk_mb = lambda p: 10 ** 6
        hooks.game_running = lambda: False
        assert (await svc.start(dict(body, skyrim="closed")))["ok"]
        r = await svc.start(body)                                                  # one run at a time
        assert not r["ok"] and r["message"] == br.REFUSE_BUSY and r["status"] == 409
        await svc.wait()
    run(main())


def test_missing_file_is_refused_at_start_not_only_in_the_plan(env):
    svc = make_service(env["base"], env["launcher"], env["card"])
    import os

    async def main():
        real = br.parse_plan
        r = await svc.start(plan_body([env["model"]]))
        assert r["ok"]
        await svc.wait()
    run(main())
    gone = write_model(env["tmp"], "gone.gguf")
    os.remove(gone)
    assert br.REFUSE_MISSING.format(name="gone.gguf") == "A model file is missing: gone.gguf."


def test_takeover_handshake_unloads_only_what_multiproxy_loaded(env):
    unloaded = []
    hooks = br.Hooks()
    hooks.loaded_by_multiproxy = lambda: [{"id": "gemma", "name": "Gemma"}]

    async def unload(ids):
        unloaded.append(list(ids))
    hooks.unload = unload
    svc = make_service(env["base"], env["launcher"], env["card"], hooks=hooks)

    async def main():
        first = await svc.start(plan_body([env["model"]]))
        assert not first["ok"] and first["needs_takeover"] == [{"id": "gemma", "name": "Gemma"}] and unloaded == [] and env["launcher"].starts == 0
        second = await svc.start(plan_body([env["model"]]), takeover=True)
        assert second["ok"] and unloaded == [["gemma"]]
        await svc.wait()
    run(main())
    assert bs.read_manifest(only_run(env["base"]))["status"] == "completed"


# --- cool-down with an injected clock ---
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


class StubSampler:
    def __init__(self, temps):
        self.temps = list(temps)

    def snapshot(self):
        t = self.temps.pop(0) if len(self.temps) > 1 else self.temps[0]
        return {"gpus": [] if t is None else [{"gpu_index": 0, "temp_c": t}]}


def cooldown(temps, **plan_kw):
    clock = Clock()
    svc = br.BenchmarkService("unused", None, clock=clock, sleep=clock.sleep)
    plan = br.Plan(models=["x"], **plan_kw)
    ctx = br._RunCtx(svc, plan, "b", 0, "x", False)
    ctx.sampler = StubSampler(temps)
    return run(ctx._cooldown()), clock


def test_cooldown_waits_until_every_gpu_is_below_the_temperature():
    out, clock = cooldown([80, 70, 60, 56, 54])
    assert out["measured"] and out["waited_s"] == 4.0 and out["final_temp_c"] == 54 and not out["timed_out"] and out["wanted_below_c"] == 55.0


def test_cooldown_gives_up_after_the_most_it_may_wait():
    out, clock = cooldown([90], cooldown_max_s=300)
    assert out["timed_out"] is True and out["waited_s"] == 300.0 and out["final_temp_c"] == 90


def test_cooldown_without_a_reading_does_not_wait():
    out, _ = cooldown([None])
    assert out == {"wanted_below_c": 55.0, "waited_s": 0.0, "final_temp_c": None, "timed_out": False, "measured": False}


def test_a_two_model_run_cools_down_only_between_models(env):
    env["card"].temp_c = 40.0
    svc = run(go(env, plan_body([env["model"], env["model"]], cooldown=True)))
    runs = sorted((bs.read_manifest(p) for p in env["base"].iterdir() if p.is_dir()), key=lambda m: m["model_index"])
    assert runs[0]["steps_total"] == 5 and "cooldown" in runs[0] and runs[0]["cooldown"]["timed_out"] is False
    assert runs[1]["steps_total"] == 4 and "cooldown" not in runs[1]
    assert [m["status"] for m in runs] == ["completed", "completed"] and runs[0]["batch_id"] == runs[1]["batch_id"]


# --- the plan ---
def test_plan_parsing_profile_and_rules(env):
    p = br.parse_plan({"models": [env["model"]]})
    assert p.profile == "standard" and p.levels == (1, 4, 8) and p.requests_per_level == 25 and p.skyrim == "history"
    assert br.parse_plan({"models": [env["model"]], "requests_per_level": 30}).profile == "custom"
    assert br.parse_plan({"models": [env["model"]], "skyrim": "typed:12.5"}).skyrim_gb == 12.5
    for bad in ({"models": [env["model"]], "kind": "decisions"}, {"models": [env["model"]], "requests_per_level": 101}, {"models": [env["model"]], "skyrim": "maybe"},
                {"models": [env["model"]], "settings": {"fan": 3}}, {"models": [env["model"]], "levels": [0]}, {"models": [env["model"]], "settings": {"cacheType": "weird"}},
                {"models": [env["model"]], "skyrim": "typed:abc"}, {"models": [env["model"]], "request_timeout_s": True}, [], {"models": [3]}, {"models": ["a\x00.gguf"]}):
        with pytest.raises(br.PlanError):
            br.parse_plan(bad)


def test_requests_go_straight_to_the_engine_not_the_proxy():
    import inspect
    src = inspect.getsource(br)
    assert "127.0.0.1:{self.handle.port}" in src and "import multiproxy" not in src and "MAIN_PORT" not in src and "speed_store" not in src


def test_one_slot_count_per_level_matches_the_engine(env):
    run(go(env, plan_body([env["model"]], levels=[1, 4, 8], requests_per_level=3)))
    assert env["launcher"].slots_seen == [1, 4, 8]


def test_engine_log_reading_with_the_real_startup_lines():
    text = """0.00.733.973 I load_tensors: offloaded 29/29 layers to GPU
0.01.424.839 I llama_context: n_batch               = 2048
0.01.424.840 I llama_context: n_ubatch              = 512
0.01.424.840 I llama_context: flash_attn            = auto
0.01.430.428 I llama_kv_cache: size =  448.00 MiB (  4096 cells,  28 layers,  1/1 seqs), K (f16):  224.00 MiB, V (q8_0):  224.00 MiB
0.01.431.949 I resolve_fused_ops: Flash Attention enabled
0.02.985.701 I cmn          init: llama threadpool init, n_threads = 24
"""
    got = be.parse_engine_log(text)
    assert got == {"layers_on_card": 29, "layers_total": 29, "n_batch": "2048", "n_ubatch": "512", "flash_attn": "auto", "n_threads": "24", "flash_attn_active": True,
                   "cache_type_k": "f16", "cache_type_v": "q8_0"}
    assert be.parse_engine_log("") == {} and be.parse_engine_log("0.03 I srv load_model: initializing, n_slots = 1") == {}
    assert be.parse_engine_log("offloaded 20/33 layers to GPU")["layers_on_card"] == 20


def test_spill_uses_the_engines_layers_and_two_shared_memory_readings(env):
    run(go(env, plan_body([env["model"]])))
    m = bs.read_manifest(only_run(env["base"]))
    assert m["spill"]["spill"] == "not_detected" and "All requested layers are on the card" in m["spill"]["evidence"]


# --- what the screen's setup adds ---
def test_prompt_lengths_temperature_and_batch_are_part_of_the_plan(env):
    p = br.parse_plan({"models": [env["model"]], "prompts": ["short"], "temperature": 0.7, "settings": {"batch": 512, "threads": 4, "gpu": "GPU-abc"}})
    assert p.prompts == ("short",) and p.temperature == 0.7 and p.profile == "custom" and p.overrides == {"batch": 512, "threads": 4, "gpu": "GPU-abc"}
    assert br.parse_plan({"models": [env["model"]], "prompts": ["long", "short", "medium"]}).profile == "standard"
    for bad in ({"prompts": []}, {"prompts": ["huge"]}, {"temperature": 5}, {"settings": {"batch": 1}}, {"settings": {"threads": 0}}, {"settings": {"gpu": ""}}):
        with pytest.raises(br.PlanError):
            br.parse_plan(dict(bad, models=[env["model"]]))
    r = p.requested()
    assert r["prompts"] == ["short"] and r["temperature"] == 0.7 and r["heat_stop"] is True


def test_only_the_ticked_prompt_lengths_are_sent(env):
    run(go(env, plan_body([env["model"]], prompts=["long"], requests_per_level=3)))
    rows = [r for r in read_rows(only_run(env["base"]), bs.REQUESTS)]
    assert {r["prompt_length_class"] for r in rows} == {"long"}


def test_vram_available_is_turned_into_headroom_per_card(env):
    run(go(env, plan_body([env["model"]], skyrim="available:20")))
    summary = json.loads((only_run(env["base"]) / bs.SUMMARY).read_text(encoding="utf-8"))
    head = summary["headroom"][0]
    # the card has 32000 MB; 20 GB (20480 MB) is left for models, so the stand-in for Skyrim is 11520 MB; the model adds 8000 MB
    assert head["skyrim_peak_mb"] == 32000 - 20480 and head["headroom_mb"] == 20480 - 8000 and head["skyrim_state"] == "typed"


def test_the_heat_stop_can_be_switched_off(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.01, "tokens": 6}
    env["card"].margin_c = 0
    run(go(env, plan_body([env["model"]], heat_stop=False)))
    assert bs.read_manifest(only_run(env["base"]))["status"] == "completed"


def test_the_live_readings_carry_the_sampled_peaks(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 10}

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"])
        assert (await svc.start(plan_body([env["model"]], requests_per_level=30)))["ok"]
        assert await wait_for(lambda: svc.status().get("readings", {}).get("peaks", {}).get("0", {}).get("vram_used_mb"))
        st = svc.status()
        await svc.cancel()
        await svc.wait()
        return st
    st = run(main())
    assert st["profile"] == "custom" and st["levels"] == [1] and st["readings"]["peaks"]["0"]["vram_used_mb"]["highest_sampled"] >= 1000


# --- the fixed test set, the checks and the sustained run ---
EN = "I would not worry about the road, because the river is low and we can cross it before the night falls."
ES = "No me preocuparía por el camino, porque el río está bajo y podemos cruzarlo antes de que caiga la noche."
FR = "Je ne m'inquiéterais pas pour la route, parce que la rivière est basse et que nous pouvons la traverser avant la nuit."
DE = "Ich würde mir um den Weg keine Sorgen machen, denn der Fluss ist niedrig und wir können ihn vor der Nacht überqueren."


def good_answer(messages):
    user = messages[-1]["content"]
    if "JSON object" in user:
        return '{"mood": "calm", "intent": "greet", "line": "Hello there."}'
    if "three numbered lines" in user:
        return "1. Go to the gate before dark.\n2. Wait for the ferry.\n3. Rest at the inn."
    if "The options are" in user:
        return "accept"
    if "Le voyageur" in user:
        return FR
    if "El viajero" in user:
        return ES
    if "Der Reisende" in user:
        return DE
    tags = "<internal_thought>Hmm.</internal_thought> " + ("Oh. [sighs] " if "[sighs]" in user else "")
    return tags + EN


def test_the_fixed_set_is_what_is_sent_and_the_checks_are_kept(env):
    env["launcher"].engine_kwargs = lambda n: {"answer": good_answer}
    run(go(env, plan_body([env["model"]], requests_per_level=18)))
    folder = only_run(env["base"])
    m = bs.read_manifest(folder)
    import benchmarks_testset as tset
    ts = tset.load()
    assert m["test_set_version"] == "v1" and m["test_set_hash"] == ts["sha256"] and m["requested_settings"]["prompts"] == ["short", "medium", "long"]
    rows = [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"]]
    assert [r["prompt_id"] for r in rows] == [pick_in_order(ts["prompts"], ts["sha256"], 18)[k] for k in range(18)]       # the seeded cycle
    assert sorted(r["prompt_id"] for r in rows) == sorted(p["id"] for p in ts["prompts"])           # every prompt once in the first full cycle
    assert all(r["answer"] and r["finish_reason"] == "stop" for r in rows)
    sent = env["launcher"].engines[0].seen
    by_id = {p["id"]: p for p in ts["prompts"]}
    first = [b for b in sent if b["messages"] == by_id["dialogue-short"]["messages"]]
    assert first and first[0]["max_tokens"] == by_id["dialogue-short"]["max_tokens"] == 200 and first[0]["stream"] is True and first[0]["cache_prompt"] is False
    summary = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))
    checks = {c["id"]: c for c in summary["checks"]}
    assert {k: (v["passed"], v["total"]) for k, v in checks.items()} == {"tags": (6, 6), "format": (6, 6), "language": (9, 9), "length": (18, 18), "choice": (3, 3)}
    assert all(c["failures"] == [] for c in checks.values()) and summary["test_set_version"] == "v1" and summary["test_set_hash"] == ts["sha256"]
    # the engine is asked for a context big enough for the biggest prompt of the run in every slot
    assert env["launcher"].overrides_seen[0]["per_slot_tokens"] == tset.tokens_per_slot(ts["prompts"]) >= 16100


def test_failed_checks_keep_the_request_and_the_line_the_model_wrote(env):
    env["launcher"].engine_kwargs = lambda n: {"answer": "Sure thing, traveller. Here is a plain answer with no tags and no shape at all.", "finish": "length"}
    run(go(env, plan_body([env["model"]], requests_per_level=18)))
    folder = only_run(env["base"])
    summary = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))
    checks = {c["id"]: c for c in summary["checks"]}
    assert checks["tags"]["passed"] == 0 and checks["format"]["passed"] == 0 and checks["length"]["passed"] == 0 and checks["choice"]["passed"] == 0
    f = checks["tags"]["failures"][0]
    assert f["request_id"].startswith("r1-") and f["line"].startswith("Sure thing, traveller.") and f["prompt_id"].startswith(("dialogue-", "roleplay-")) and "<internal_thought>" in f["why"]
    assert checks["length"]["failures"][0]["why"] == "it ran into the output limit" and len(checks["length"]["failures"]) == 18
    row = [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"]][0]
    assert row["answer"].startswith("Sure thing")                                                  # kept in every request row, and in the CSV export
    assert "answer" in bs.requests_csv(env["base"], folder.name).splitlines()[0]


def test_only_the_ticked_lengths_set_the_context(env):
    run(go(env, plan_body([env["model"]], prompts=["short"], requests_per_level=3)))
    import benchmarks_testset as tset
    assert env["launcher"].overrides_seen[0]["per_slot_tokens"] == tset.tokens_per_slot(tset.prompts_for(tset.load(), ["short"]))
    assert env["launcher"].overrides_seen[0]["per_slot_tokens"] < 8192                             # (no sustained run in this plan: only the short prompts count)


def test_the_sustained_run_loops_with_four_slots_the_medium_prompt_and_saves_its_rule(env):
    env["launcher"].engine_kwargs = lambda n: {"answer": good_answer, "token_s": 0.01, "tokens": 6}
    run(go(env, plan_body([env["model"]], levels=[4], requests_per_level=4, sustained=True, sustained_s=1.5)))
    folder = only_run(env["base"])
    summary = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))
    s = summary["sustained"]
    assert s["status"] == "completed" and s["rule"] == dict(bc.SUSTAINED_RULE, duration_s=1.5, baseline_window_s=60.0, window_s=60.0, slowdown_below_pct_of_baseline=80) and s["baseline_tok_s"] > 0
    rows = [r for r in read_rows(folder, bs.REQUESTS) if r.get("sustained")]
    assert len(rows) >= 8 and {r["prompt_id"] for r in rows} == {"dialogue-medium"} and {r["level"] for r in rows} == {4}
    assert env["launcher"].slots_seen == [4]                                                       # 4 slots all the way (the level was 4 too: no restart)
    assert not [c for c in summary["checks"] if any(f["request_id"] in {r["request_id"] for r in rows} for f in c["failures"])]       # sustained requests are not checked


def test_an_error_in_the_sustained_run_makes_it_failed_with_the_reason(env):
    env["launcher"].engine_kwargs = lambda n: {"fail_every": 6, "token_s": 0.01, "tokens": 6}
    run(go(env, plan_body([env["model"]], levels=[4], requests_per_level=3, sustained=True, sustained_s=3)))
    m = bs.read_manifest(only_run(env["base"]))
    s = json.loads((only_run(env["base"]) / bs.SUMMARY).read_text(encoding="utf-8"))["sustained"]
    assert s["status"] == "failed" and "a request error" in s["reason"] and s["rule"]["duration_s"] == 3
    assert m["status"] == "completed"                                                              # the run goes on to unload; the sustained result says what happened


def test_a_heat_stop_in_the_sustained_run_ends_it_interrupted(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 8}

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"])
        assert (await svc.start(plan_body([env["model"]], levels=[4], requests_per_level=3, sustained=True, sustained_s=30)))["ok"]
        assert await wait_for(lambda: svc.status().get("step") == "sustained")
        await asyncio.sleep(0.6)
        env["card"].margin_c = 0
        await svc.wait()
    run(main())
    folder = only_run(env["base"])
    m = bs.read_manifest(folder)
    s = json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))["sustained"]
    assert m["status"] == "interrupted" and m["reason"] == "thermal_stop"
    assert s["status"] == "interrupted" and s["reason"] == "thermal_stop" and s["rule"]["duration_s"] == 30            # what it did is kept, with the rule's numbers


def test_cancelling_the_sustained_run_is_interrupted_not_a_made_up_result(env):
    env["launcher"].engine_kwargs = lambda n: {"token_s": 0.02, "tokens": 8}

    async def main():
        svc = make_service(env["base"], env["launcher"], env["card"])
        assert (await svc.start(plan_body([env["model"]], levels=[4], requests_per_level=3, sustained=True, sustained_s=30)))["ok"]
        assert await wait_for(lambda: svc.status().get("step") == "sustained")
        await svc.cancel()
        await svc.wait()
    run(main())
    folder = only_run(env["base"])
    assert bs.read_manifest(folder)["status"] == "cancelled"
    assert json.loads((folder / bs.SUMMARY).read_text(encoding="utf-8"))["sustained"]["status"] == "interrupted"


# --- the sustained verdict on fake runs (the rule of contract section 7, 300 s) ---
def curve(spans):
    """(time, tok/s) every 10 s from (start, end, value) spans."""
    return [(t, v) for a, b, v in spans for t in range(a, b, 10)]


def test_sustained_verdict_flat_slowing_error_and_heat_stop():
    flat = bc.sustained_verdict(curve([(5, 300, 50.0)]))
    assert flat["status"] == "completed" and flat["baseline_tok_s"] == 50.0 and flat["after_minutes"] is None and len(flat["windows"]) == 4 and flat["rule"]["duration_s"] == 300
    slow = bc.sustained_verdict(curve([(5, 180, 50.0), (180, 300, 39.0)]))                        # slows down at minute 3: 39 is under 80% of 50
    assert slow["status"] == "slowdown_detected" and slow["after_minutes"] == 3.0 and slow["windows"][2]["below_threshold"] is True
    err = bc.sustained_verdict(curve([(5, 240, 50.0)]), failed_reason="a request error: HTTP 500")                      # an error at minute 4
    assert err["status"] == "failed" and err["reason"] == "a request error: HTTP 500" and err["rule"]["slowdown_below_pct_of_baseline"] == 80
    heat = bc.sustained_verdict(curve([(5, 150, 50.0)]), interrupted_reason="thermal_stop")
    assert heat["status"] == "interrupted" and heat["reason"] == "thermal_stop" and heat["baseline_tok_s"] == 50.0
    assert bc.sustained_verdict([], ran=False)["status"] == "not_run"
    assert bc.sustained_verdict(curve([(5, 300, 50.0)]), duration_s=120)["rule"]["duration_s"] == 120 and len(bc.sustained_verdict(curve([(5, 120, 50.0)]), duration_s=120)["windows"]) == 1


def test_n_per_level_up_to_a_hundred_and_p95_only_from_twenty(env):
    run(go(env, plan_body([env["model"]], requests_per_level=100)))
    summary = json.loads((only_run(env["base"]) / bs.SUMMARY).read_text(encoding="utf-8"))["levels"]["1"]
    assert summary["completed"] == 100 and summary["ttft_ms"]["n"] == 100 and isinstance(summary["ttft_ms"]["p95"], float)
    with pytest.raises(br.PlanError):
        br.parse_plan({"models": [env["model"]], "requests_per_level": 101})
    import shutil
    shutil.rmtree(env["base"])
    run(go(env, plan_body([env["model"]], requests_per_level=19)))
    assert json.loads((only_run(env["base"]) / bs.SUMMARY).read_text(encoding="utf-8"))["levels"]["1"]["ttft_ms"]["p95"] == bc.NOT_ENOUGH_REQUESTS


def test_the_engines_context_is_per_slot_in_props():
    """Seen on a real engine (b11361, 2026-10-27): 8 slots started with a 155,648 context report n_ctx 19,456, one slot's share."""
    assert be.ctx_from_props({"total_slots": 8, "default_generation_settings": {"n_ctx": 19456}}) == {"total_slots": 8, "ctx_per_slot": 19456, "ctx": 155648}
    assert be.ctx_from_props({"total_slots": 1, "default_generation_settings": {"n_ctx": 32768}})["ctx"] == 32768
    assert be.ctx_from_props({}) == {"total_slots": None} and be.ctx_from_props({"default_generation_settings": {"n_ctx": 4096}}) == {"total_slots": None, "ctx_per_slot": 4096}


def test_the_prompt_cycle_goes_on_across_the_levels_so_every_prompt_is_used_about_equally(env):
    env["launcher"].engine_kwargs = lambda n: {"answer": good_answer}
    run(go(env, plan_body([env["model"]], levels=[1, 4, 8], requests_per_level=25, sustained=False)))
    folder = only_run(env["base"])
    rows = [r for r in read_rows(folder, bs.REQUESTS) if not r["warmup"] and not r.get("sustained")]
    import benchmarks_testset as tset
    ids = [p["id"] for p in tset.load()["prompts"]]
    counts = {i: sum(1 for r in rows if r["prompt_id"] == i) for i in ids}
    assert set(counts) == set(ids) and sum(counts.values()) == 75
    # 75 requests over 18 prompts is 4 or 5 each. The requests still running when a level's last one finishes are dropped (by design), and the slow prompts are the ones left running, so
    # in the last level a prompt can end one short of that: the whole run stays within 3 to 5, and levels 1 and 4 (whose dropped prompts go back into the cycle) leave nothing short.
    assert min(counts.values()) >= 3 and max(counts.values()) <= 5 and sum(1 for c in counts.values() if c in (4, 5)) >= 16
    first = [r["prompt_id"] for r in rows if r["level"] == 1]
    second = [r["prompt_id"] for r in rows if r["level"] == 4]
    assert len(set(first)) == 18 and len(set(second)) >= 17                                           # level 4 does not start over at the first prompt: it goes on through the cycle


def test_dropped_requests_give_their_prompt_back_so_the_counts_stay_within_one():
    import benchmarks_testset as tset
    prompts, seed = tset.load()["prompts"], tset.load()["sha256"]
    uses, recorded = {}, {p["id"]: 0 for p in prompts}
    for level, n in ((1, 1), (4, 4), (8, 8)):                                                         # a level starts n requests, keeps 25, and drops the n-1 still running
        started = []
        for _ in range(25 + n - 1):
            p = br.pick_prompt(prompts, uses, seed)
            uses[p["id"]] = uses.get(p["id"], 0) + 1
            started.append(p["id"])
        for pid in started[25:]:
            uses[pid] -= 1                                                                            # dropped
        for pid in started[:25]:
            recorded[pid] += 1
        if level < 8:
            assert max(recorded.values()) - min(recorded.values()) <= 1
    assert sum(recorded.values()) == 75 and max(recorded.values()) - min(recorded.values()) <= 1 and min(recorded.values()) >= 4


def pick_in_order(prompts, seed, count):
    uses, out = {}, []
    for _ in range(count):
        p = br.pick_prompt(prompts, uses, seed)
        uses[p["id"]] = uses.get(p["id"], 0) + 1
        out.append(p["id"])
    return out


def test_the_schedule_is_seeded_complete_cycles_in_their_own_order():
    import benchmarks_testset as tset
    prompts = tset.load()["prompts"]
    seed = tset.load()["sha256"]
    a = pick_in_order(prompts, seed, 54)
    assert a == pick_in_order(prompts, seed, 54)                                                      # the same every time: the order can be rebuilt from the test set's hash
    for c in range(3):
        assert sorted(a[c * 18:(c + 1) * 18]) == sorted(p["id"] for p in prompts)                     # a complete cycle uses each prompt once
    assert a[:18] != a[18:36] and a[:18] != [p["id"] for p in prompts]                                # each cycle has its own order
    assert pick_in_order(prompts, "other", 18) != a[:18]
    uses = {p["id"]: 1 for p in prompts}
    uses[prompts[5]["id"]] = 0                                                                        # a dropped request's prompt is the one used least, so it comes next
    assert br.pick_prompt(prompts, uses, seed)["id"] == prompts[5]["id"]


def test_the_status_says_when_part_of_the_model_runs_on_the_processor_and_forgets_it_for_the_next_model(env):
    seen = []
    orig = env["launcher"].start

    async def start(*a, **k):
        h = await orig(*a, **k)
        if env["launcher"].starts == 1:
            h.resolved = dict(h.resolved, layers_on_card=20, layers_total=33)           # the engine put only 20 of 33 layers on the card
        return h
    env["launcher"].start = start

    async def go_two():
        svc = make_service(env["base"], env["launcher"], env["card"])
        env["model2"] = write_model(env["tmp"], "second.gguf")
        res = await svc.start(plan_body([env["model"], env["model2"]], levels=[1], requests_per_level=4, sustained=False, cooldown=False))
        assert res["ok"]
        while svc.running():
            seen.append(dict(svc.status().get("layers") or {}) or None)
            await asyncio.sleep(0.01)
        return svc
    svc = run(go_two())
    assert {"on_card": 20, "total": 33} in seen                                            # the live panel could say it
    assert svc.status().get("layers") == {"on_card": 33, "total": 33}                       # the second model put every layer on the card: nothing carried over
