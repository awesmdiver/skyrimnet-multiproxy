"""Benchmarks on another PC, step 2: the Courier runs one test with a model that is already there.

A real Courier on 127.0.0.1 with its own scratch folder (nothing of the real settings, models or firewall is touched), a fake engine and a fake graphics card behind the runner, and the
benchmark client from this PC's side. Unit-level checks use `CourierBenchmarks` directly where a test needs to control the clock.
"""
import asyncio
import json
import sys
import time
import uuid
from types import SimpleNamespace

import aiohttp
import pytest
import pytest_asyncio

import benchmarks_contract as bc
import benchmarks_courier as bcr
import benchmarks_hooks
import benchmarks_remote_client as client_mod
import benchmarks_remote_plan as rp
import benchmarks_runner as br
import benchmarks_sampler as bsm
import courier as cr
import courier_system as cs
import local_models as lm
from benchmarks_fakes import FakeCard, FakeLauncher, make_service, write_model
from tests.fixtures.courier_fakes import free_port

pytestmark = pytest.mark.asyncio
OWNER = "main-pc-aaaaaaaa1111"
OTHER = "main-pc-bbbbbbbb2222"
BATTERY_OK = {"has_battery": False, "battery_percent": None, "plugged_in": None}


class FakeAwake:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")
        return True

    def stop(self):
        self.calls.append("stop")


def plan_body(model_id, owner=OWNER, **kw):
    body = {"protocol_version": 1, "request_id": uuid.uuid4().hex, "owner_id": owner, "models": [{"id": model_id}], "levels": [1], "requests_per_level": 4, "sustained": False,
            "cooldown": False, "while_testing": "stop"}
    body.update(kw)
    return body


class Env:
    def __init__(self):
        self.power = dict(BATTERY_OK)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.courier.settings.port}"

    def headers(self, owner=OWNER, token=None, **extra):
        h = {"Authorization": f"Bearer {self.courier.settings.code}", bcr.OWNER_HEADER: owner, **extra}
        if token:
            h[bcr.TOKEN_HEADER] = token
        return h

    async def call(self, method, path, body=None, headers=None, raw=None, expect=None):
        async with aiohttp.ClientSession() as s:
            kw = {"data": raw} if raw is not None else {"json": body}
            async with s.request(method, self.base + path, headers=headers if headers is not None else self.headers(), **kw) as r:
                data = await r.json(content_type=None)
                if expect is not None:
                    assert r.status == expect, (r.status, data)
                return r.status, data

    async def start(self, body, owner=OWNER, expect=None):
        return await self.call("POST", "/courier/v1/benchmarks", body, self.headers(owner), expect=expect)

    async def status(self, job, owner=OWNER, token=None, expect=None):
        return await self.call("GET", f"/courier/v1/benchmarks/status?job={job['job_id']}", None, self.headers(owner, token or job["job_token"]), expect=expect)

    async def wait_state(self, job, states, timeout=30.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            _, st = await self.status(job)
            if st["state"] in states:
                return st
            await asyncio.sleep(0.05)
        raise AssertionError(f"job stayed {st['state']}")


def _fast_gate():
    """The wait for the PC's memory between models is 10 s in real life; the tests do not wait that long."""
    import benchmarks_guard as bg
    return bg.GuardConfig(gate_hold_s=0.05, settle_s=0.0)


def make_env(tmp_path, monkeypatch, engine_kwargs=None, lease_s=60.0, tick_s=0.05, machine=None, service_extra=None):
    monkeypatch.setitem(benchmarks_hooks._driver_cache, "v", None)
    e = Env()
    e.card = FakeCard()
    e.launcher = FakeLauncher(e.card, engine_kwargs=engine_kwargs)
    e.awake = FakeAwake()
    settings = cr.CourierSettings(tmp_path / "courier.json")
    settings.set(port=free_port(), keep_awake=False)                                                       # (the person's own keep-awake setting is off: the job holds it alone)
    sampler = lambda on_sample, plan: bsm.ReadingsSampler(on_sample=on_sample, runner=e.card, interval_s=0.03, machine_reader=machine or (lambda: {"cpu_util_pct": 5.0, "ram_used_mb": 9000}), ceiling_c=plan.ceiling_c)
    opts = {"launcher": e.launcher, "sampler_factory": sampler, "power_fn": lambda: dict(e.power), "service": {"heartbeat_s": 0.05, "thermal_poll_s": 0.03, "guard": _fast_gate(), "guard_poll_s": 0.05, **(service_extra or {})},
            "job": {"lease_s": lease_s, "tick_s": tick_s, "free_mb_fn": lambda p: 100000}}
    e.courier = cr.Courier(settings, logging_stub, adapters=[], bind_ips=["127.0.0.1"], discovery_port=free_port(), keep_awake=cs.KeepAwake(e.awake), gpu_reader=lambda: [], bench_options=opts)
    folder = e.courier.local.set_folder(str(tmp_path / "models"))
    eng = folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-cpu"
    eng.mkdir(parents=True)
    (eng / ("llama-server.exe" if sys.platform == "win32" else "llama-server")).write_bytes(b"fake")
    e.courier.local._update(engine={"kind": "cpu", "build": lm.LLAMA_CPP_BUILD})
    e.courier.local._gpu_detector = lambda: lm.GpuInfo("Test GPU", 24576, "nvidia", cuda=(13, 4))
    gguf = write_model(tmp_path, "Den Model.gguf")
    e.model_id = e.courier.local.add_custom(gguf).id
    return e


class _Log:
    def info(self, *a, **k): pass
    warning = error = debug = exception = info


logging_stub = _Log()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    await e.courier.start()
    yield e
    await e.courier.stop()
    for eng in e.launcher.engines:
        eng.stop()


@pytest_asyncio.fixture
async def slow(tmp_path, monkeypatch):
    """A Courier whose test takes a good while (a slow engine, many requests), with a short lease, for the tests that end it early."""
    e = make_env(tmp_path, monkeypatch, engine_kwargs=lambda n: {"token_s": 0.05, "tokens": 20}, lease_s=1.5)
    await e.courier.start()
    yield e
    await e.courier.stop()
    for eng in e.launcher.engines:
        eng.stop()


LONG = {"requests_per_level": 100}


# --- the happy path ---------------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_report_says_what_this_courier_can_do(env):
    _, rep = await env.call("GET", "/courier/v1/report", expect=200)
    b = rep["courier"]["benchmarks"]
    assert b["protocol_version"] == 1 and rp.missing_capabilities(b, rp.required_capabilities({"while_testing": "start_normal"}, collect=False)) == []
    assert rep["courier"]["build"] == cr.COURIER_BUILD >= 12


async def test_start_runs_the_whole_test_and_finishes_and_the_files_stay_on_the_courier(env):
    status, job = await env.start(plan_body(env.model_id))
    assert status == 201 and job["state"] == "queued" and job["replayed"] is False and job["job_token"]
    st = await env.wait_state(job, rp.ENDED_STATES)
    assert st["state"] == "finished", st
    assert len(st["runs"]) == 1 and st["runs"][0]["state"] == "completed" and st["models"][0]["state"] == "done"
    run = env.courier.benchmarks.runs_dir / st["runs"][0]["run_id"]
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skyrim_state"] == "not_applicable" and manifest["status"] == "completed"
    assert manifest["device"]["id"] == env.courier.settings.id and manifest["device"]["courier_build"] == cr.COURIER_BUILD
    assert manifest["job"]["job_id"] == job["job_id"] and manifest["job"]["owner_id"] == OWNER and manifest["job"]["plan_hash"] == st["plan_hash"]
    assert manifest["builds"]["protocol_version"] == 1 and manifest["power"]["has_battery"] is False and manifest["services"]["requested"] == "stop"
    assert "thermal" in manifest and "power_end" in manifest and manifest["test_set_version"]
    assert (run / "requests.jsonl").stat().st_size > 0 and (run / "samples.jsonl").stat().st_size > 0
    assert not env.courier.benchmarks.lock_path.exists()
    assert env.awake.calls == ["start", "stop"]                                                            # kept awake for the job, released at the end


async def test_a_keep_awake_the_person_turned_on_stays_on_after_the_job(tmp_path, monkeypatch):
    e = make_env(tmp_path, monkeypatch)
    e.courier.settings.set(keep_awake=True)
    await e.courier.start()
    try:
        _, job = await e.start(plan_body(e.model_id))
        assert (await e.wait_state(job, rp.ENDED_STATES))["state"] == "finished"
        await asyncio.sleep(0.2)
        assert e.awake.calls == ["start"] and e.courier.keep_awake.on
    finally:
        await e.courier.stop()


async def test_a_plan_the_validator_rejects_never_starts(env):
    for bad, field in ((plan_body(env.model_id, shell="calc"), "shell"), (plan_body("C:\\evil.exe"), "models[0].id"), (plan_body("https://x.example/m.gguf"), "models[0].id"),
                       (plan_body(env.model_id, settings={"extraArgs": "--host 0.0.0.0"}), "settings.extraArgs"), (plan_body("not-a-model"), "models[0].id")):
        status, data = await env.start(bad)
        assert status == 400 and data["error"]["code"] == "bad_plan" and data["error"]["field"] == field, data
    assert env.launcher.starts == 0 and not env.courier.benchmarks.jobs and not env.courier.benchmarks.lock_path.exists()
    status, data = await env.start(plan_body(env.model_id, protocol_version=2))
    assert status == 400 and data["error"]["code"] == "bad_protocol"
    status, data = await env.start(plan_body(env.model_id, owner=OTHER), owner=OWNER)
    assert status == 403 and data["error"]["code"] == "owner_mismatch"                                      # the plan's owner must be the caller
    mid = f"sha256:{'a' * 64}:100"
    status, data = await env.start(plan_body(env.model_id, models=[{"id": mid, "file": "m.gguf", "sha256": "a" * 64, "size": 100}]))
    assert status == 201                                                                                    # a model the device lacks is waited for (step 4 sends it); nothing runs until it arrives
    await env.call("POST", "/courier/v1/benchmarks/cancel", {"job_id": data["job_id"]}, env.headers(OWNER, data["job_token"]))
    await env.wait_state(data, rp.ENDED_STATES)
    assert env.launcher.starts == 0


# --- retry-safe start, two controllers, cancel ------------------------------------------------------------------------------------------------------------------------------

async def test_the_same_request_id_twice_is_one_job(env):
    body = plan_body(env.model_id)
    s1, a = await env.start(body)
    s2, b = await env.start(dict(body))
    assert (s1, s2) == (201, 200) and b["replayed"] is True and b["job_id"] == a["job_id"] and b["job_token"] == a["job_token"]
    assert len(env.courier.benchmarks.jobs) == 1
    s3, c = await env.start(plan_body(env.model_id, request_id=body["request_id"], levels=[1, 4]))
    assert s3 == 409 and c["error"]["code"] == "request_id_reused"                                           # same id, a different plan
    s4, d = await env.start(dict(body, owner_id=OTHER), owner=OTHER)
    assert s4 == 409 and d["error"]["code"] == "request_id_reused"                                          # another PC cannot take over the replay
    await env.wait_state(a, rp.ENDED_STATES)
    assert env.launcher.starts == 1                                                                         # one job, one run


async def test_two_controllers_asking_together_get_one_job_and_one_busy(slow):
    results = await asyncio.gather(slow.start(plan_body(slow.model_id, owner=OWNER, **LONG), owner=OWNER), slow.start(plan_body(slow.model_id, owner=OTHER, **LONG), owner=OTHER))
    codes = sorted(s for s, _ in results)
    assert codes == [201, 409]
    busy = next(d for s, d in results if s == 409)
    assert busy["error"]["code"] == "busy" and busy["error"]["busy_reason"] == "benchmark"
    assert len(slow.courier.benchmarks.jobs) == 1


async def test_cancel_stops_the_test_and_keeps_what_was_measured(slow):
    _, job = await slow.start(plan_body(slow.model_id, **LONG))
    end = time.monotonic() + 20
    while time.monotonic() < end and not (slow.launcher.starts >= 1 and (await slow.status(job))[1]["state"] == "running"):
        await asyncio.sleep(0.05)
    status, view = await slow.call("POST", "/courier/v1/benchmarks/cancel", {"job_id": job["job_id"]}, slow.headers(OWNER, job["job_token"]))
    assert status == 202 and view["cancel_requested"] is True
    st = await slow.wait_state(job, rp.ENDED_STATES)
    assert st["state"] == "cancelled" and st["reason"] == "user_cancel"
    assert not slow.courier.benchmarks.lock_path.exists() and slow.launcher.stops >= 1
    status, data = await slow.call("POST", "/courier/v1/benchmarks/cancel", {"job_id": job["job_id"]}, slow.headers(OWNER, job["job_token"]))
    assert status == 409 and data["error"]["code"] == "illegal_state"                                         # nothing to cancel any more


async def test_a_busy_game_refuses_unless_the_plan_says_take_over(env):
    env.courier._outside["at"] = time.time()                                                              # the game asked for a chat answer a moment ago
    status, data = await env.start(plan_body(env.model_id))
    assert status == 409 and data["error"]["busy_reason"] == "chat"
    status, job = await env.start(plan_body(env.model_id, take_over=True))
    assert status == 201
    assert (await env.wait_state(job, rp.ENDED_STATES))["state"] == "finished"


# --- the owner, the lease ---------------------------------------------------------------------------------------------------------------------------------------------------------

async def test_only_the_owner_with_the_key_can_look_and_only_it_keeps_the_job_alive(slow):
    _, job = await slow.start(plan_body(slow.model_id, **LONG))
    deadline = time.monotonic() + 6                                                                           # the lease is 1.5 s: another PC polling the whole time must not save it
    while time.monotonic() < deadline:
        s1, d1 = await slow.status(job, owner=OTHER)
        assert s1 == 403 and d1["error"]["code"] == "owner_mismatch"
        s2, d2 = await slow.status(job, token="wrong-key-wrong-key")
        assert s2 == 403 and d2["error"]["code"] == "bad_job_token"
        job_now = slow.courier.benchmarks.jobs[job["job_id"]]
        if job_now["state"] in rp.ENDED_STATES:
            break
        await asyncio.sleep(0.1)
    _, st = await slow.status(job)
    assert st["state"] == "interrupted" and st["reason"] == "lost_contact"
    status, data = await slow.call("GET", "/courier/v1/benchmarks/status?job=job-nope-nope-nope", None, slow.headers(OWNER, "x"))
    assert status == 404 and data["error"]["code"] == "unknown_job"


async def test_the_owners_polling_keeps_the_lease_and_silence_ends_the_run_and_stops_the_engine(slow):
    _, job = await slow.start(plan_body(slow.model_id, **LONG))
    for _ in range(25):                                                                                       # 2.5 s of polling, longer than the 1.5 s lease
        _, st = await slow.status(job)
        assert st["state"] in rp.ACTIVE_STATES and st["lease_expires_in_s"] is not None
        await asyncio.sleep(0.1)
    end = time.monotonic() + 20                                                                              # now go quiet (looking without asking the Courier, which would renew the lease)
    while time.monotonic() < end and slow.courier.benchmarks.jobs[job["job_id"]]["state"] not in rp.ENDED_STATES:
        await asyncio.sleep(0.05)
    st = slow.courier.benchmarks.view(slow.courier.benchmarks.jobs[job["job_id"]])
    assert st["state"] == "interrupted" and st["reason"] == "lost_contact"
    assert slow.launcher.stops >= 1 and not any(e.running for e in slow.launcher.engines)
    run = slow.courier.benchmarks.runs_dir / st["runs"][0]["run_id"]
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "interrupted" and m["reason"] == "lost_contact" and m["interruptions"][0]["kind"] == "lost_contact"
    assert not slow.courier.benchmarks.lock_path.exists() and slow.awake.calls[-1] == "stop"


async def test_low_battery_ends_the_run_cleanly(slow):
    _, job = await slow.start(plan_body(slow.model_id, **LONG))
    slow.power.update(has_battery=True, battery_percent=9, plugged_in=False)
    st = None
    for _ in range(100):
        _, st = await slow.status(job)
        if st["state"] in rp.ENDED_STATES:
            break
        await asyncio.sleep(0.1)
    assert st["state"] == "interrupted" and st["reason"] == "low_battery" and "battery" in st["message"]


async def test_a_battery_that_is_charging_or_not_low_is_left_alone(env):
    env.power.update(has_battery=True, battery_percent=9, plugged_in=True)
    _, job = await env.start(plan_body(env.model_id))
    assert (await env.wait_state(job, rp.ENDED_STATES))["state"] == "finished"


# --- browser-shaped requests, the body, the id (security review SF1 and SF4) --------------------------------------------------------------------------------------------------

async def test_browser_shaped_requests_are_refused(env):
    body = plan_body(env.model_id)
    for extra, code in (({"Origin": "https://evil.example"}, "browser_request"), ({"Origin": "null"}, "browser_request"), ({"Sec-Fetch-Site": "cross-site"}, "browser_request"),
                        ({"Host": "evil.example"}, "bad_host"), ({"Host": "rebind.attacker.test:8110"}, "bad_host")):
        status, data = await env.call("POST", "/courier/v1/benchmarks", body, {**env.headers(), **extra}, expect=None)
        assert status == 403 and data["error"]["code"] == code, (extra, data)
    status, data = await env.call("GET", "/courier/v1/benchmarks/status", None, {**env.headers(), "Origin": "https://evil.example"})
    assert status == 403
    status, data = await env.call("POST", "/courier/v1/benchmarks", None, env.headers(), raw=json.dumps(body), expect=None)          # (aiohttp sends raw data as octet-stream)
    assert status == 415 and data["error"]["code"] == "bad_content_type"
    status, data = await env.call("POST", "/courier/v1/benchmarks", body, {"Authorization": f"Bearer {env.courier.settings.code}"})
    assert status == 400 and data["error"]["code"] == "bad_owner"                                            # no PC id: not a request from this program
    status, data = await env.call("POST", "/courier/v1/benchmarks", body, {bcr.OWNER_HEADER: OWNER})
    assert status == 403                                                                                      # no pairing code
    assert env.launcher.starts == 0 and not env.courier.benchmarks.jobs


async def test_the_start_body_has_its_own_small_limit(env):
    huge = plan_body(env.model_id, request_id="x" * (rp.MAX_BODY_BYTES + 10))
    status, data = await env.call("POST", "/courier/v1/benchmarks", huge, env.headers())
    assert status == 413 and data["error"]["code"] == "too_big"
    status, data = await env.call("POST", "/courier/v1/benchmarks/cancel", {"job_id": "x" * 5000}, env.headers(OWNER, "k"))
    assert status == 413
    assert not env.courier.benchmarks.jobs


async def test_the_main_pcs_permanent_id_is_recorded_for_the_courier(env):
    await env.start(plan_body(env.model_id))
    data = json.loads(env.courier.settings.path.read_text(encoding="utf-8"))
    assert data["paired_instance"] == OWNER and OWNER in data["main_instances"]
    await env.call("GET", "/courier/v1/benchmarks/status", None, env.headers(OTHER))                              # a request that is not an accepted plan records nothing (security review T11)
    assert json.loads(env.courier.settings.path.read_text(encoding="utf-8"))["main_instances"] == [OWNER]
    env.courier.settings.note_instance(OTHER)
    assert json.loads(env.courier.settings.path.read_text(encoding="utf-8"))["main_instances"] == [OWNER, OTHER]
    env.courier.settings.clear_pairing()
    data = json.loads(env.courier.settings.path.read_text(encoding="utf-8"))
    assert data["main_instances"] == [] and data["paired_instance"] == ""


# --- the client (this PC's side) ----------------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_client_runs_a_test_end_to_end_and_its_polling_is_the_heartbeat(env):
    c = client_mod.BenchmarkClient("127.0.0.1:" + str(env.courier.settings.port), env.courier.settings.code, OWNER, allow_loopback=True)
    seen = []
    started = await c.start(client_mod.build_plan([env.model_id], OWNER, levels=[1], requests_per_level=4, sustained=False, cooldown=False, while_testing="stop"))
    assert started["state"] == "queued"
    final = await c.watch(lambda st: seen.append(st["state"]), interval=0.05)
    assert final["state"] == "finished" and "running" in seen
    again = await c.start(client_mod.build_plan([env.model_id], OWNER, request_id="req-fixed-0001", levels=[1], requests_per_level=4, sustained=False, cooldown=False, while_testing="stop"))
    assert not again["replayed"]
    await c.watch(interval=0.05)


async def test_the_client_refuses_an_old_courier_before_sending_anything():
    plan = client_mod.build_plan(["m"], OWNER)
    for report in ({"courier": {"build": 11}}, {"courier": {"benchmarks": {"protocol_version": 1, "capabilities": ["start.v1"]}}}, {"courier": {"benchmarks": {"protocol_version": 9, "capabilities": list(rp.BASE_CAPABILITIES)}}}, None):
        with pytest.raises(client_mod.OldCourier) as e:
            client_mod.check_report(report, plan, "LAPTOP")
        assert "LAPTOP" in e.value.message and e.value.missing
    client_mod.check_report({"courier": {"benchmarks": bcr.capabilities_block()}}, plan)


async def test_client_start_stops_at_an_old_courier_without_posting(monkeypatch):
    c = client_mod.BenchmarkClient("127.0.0.1:9", "ABCD-2345", OWNER, allow_loopback=True)
    calls = []

    async def fake_call(method, path, body=None, timeout=15, with_job=True):
        calls.append((method, path))
        return {"courier": {"build": 11}}
    monkeypatch.setattr(c, "_call", fake_call)
    with pytest.raises(client_mod.OldCourier):
        await c.start(client_mod.build_plan(["m"], OWNER))
    assert calls == [("GET", "/courier/v1/report")]


async def test_the_client_says_when_contact_is_lost_and_keeps_asking(monkeypatch):
    c = client_mod.BenchmarkClient("127.0.0.1:9", "ABCD-2345", OWNER, allow_loopback=True)
    now = [0.0]
    c.clock = lambda: now[0]
    c.last_contact = 0.0
    answers = [client_mod.NoContact(), client_mod.NoContact(), client_mod.NoContact(), client_mod.NoContact(), {"state": "running"}, {"state": "interrupted", "reason": "lost_contact"}]

    async def status():
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(c, "status", status)
    lost, got = [], []

    async def sleep(s):
        now[0] += 10
    final = await c.watch(lambda st: got.append(st["state"]), lost.append, interval=5, lost_after=20, sleep=sleep)
    assert lost == [20.0, 30.0] and got == ["running", "interrupted"] and final["state"] == "interrupted"


async def test_the_client_retries_a_lost_start_answer_with_the_same_request_id(monkeypatch):
    c = client_mod.BenchmarkClient("127.0.0.1:9", "ABCD-2345", OWNER, allow_loopback=True)
    sent = []

    async def fake_call(method, path, body=None, timeout=15, with_job=True):
        if path.endswith("/report"):
            return {"courier": {"benchmarks": bcr.capabilities_block()}}
        sent.append(body["request_id"])
        if len(sent) < 3:
            raise client_mod.NoContact()
        return {"job_id": "job-1", "job_token": "tok", "state": "queued", "replayed": True}
    monkeypatch.setattr(c, "_call", fake_call)
    assert (await c.start(client_mod.build_plan(["m"], OWNER, request_id="req-same-0001")))["job_id"] == "job-1"
    assert sent == ["req-same-0001"] * 3 and c.job_token == "tok"


# --- the job on its own: clock, sleep, services, restart ---------------------------------------------------------------------------------------------------------------------------

def unit(tmp_path, clock=None, hooks=None, engine_kwargs=None, restore=None, power=None, lease_s=60.0, models_root=None):
    card = FakeCard()
    launcher = FakeLauncher(card, engine_kwargs=engine_kwargs)
    model = write_model(tmp_path, "unit.gguf")
    svc = make_service(tmp_path / "benchmarks", launcher, card, hooks=hooks)
    awake = FakeAwake()
    jobs = bcr.CourierBenchmarks(tmp_path, svc, lambda: {"unit-model": model}, device_fn=lambda: {"id": "dev", "name": "UNIT", "courier_build": 12}, keep_awake=cs.KeepAwake(awake),
                                 power_fn=power or (lambda: dict(BATTERY_OK)), restore_fn=restore, clock=clock or time.monotonic, lease_s=lease_s, tick_s=0.03, sleep_gap_s=20.0,
                                 free_mb_fn=lambda p: 100000, models_root=models_root)
    return SimpleNamespace(jobs=jobs, svc=svc, launcher=launcher, card=card, awake=awake, model=model)


def body_for(**kw):
    return json.dumps(plan_body("unit-model", **kw)).encode()


async def end_of(u, job_id, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if u.jobs.jobs[job_id]["state"] in rp.ENDED_STATES:
            await u.svc.wait()
            return u.jobs.jobs[job_id]
        await asyncio.sleep(0.03)
    raise AssertionError("job did not end")


async def test_a_sleeping_pc_marks_the_run_interrupted(tmp_path):
    t = [1000.0]
    u = unit(tmp_path, clock=lambda: t[0], engine_kwargs=lambda n: {"token_s": 0.05, "tokens": 20})
    status, job = await u.jobs.start(OWNER, body_for(requests_per_level=100))
    assert status == 201
    for _ in range(100):
        if u.svc.running() and u.svc.status().get("step") not in (None, "starting"):
            break
        await asyncio.sleep(0.03)
    t[0] += 3600                                                                                          # the PC was suspended for an hour
    done = await end_of(u, job["job_id"])
    assert done["state"] == "interrupted" and done["reason"] == "slept" and "sleep" in done["message"]


async def test_the_models_the_test_stopped_are_put_back_unless_the_person_started_them_since(tmp_path):
    held = [{"id": "m1", "name": "Model One"}, {"id": "m2", "name": "Model Two"}]
    unloaded, restored = [], []
    hooks = br.Hooks(loaded_by_multiproxy=lambda: list(held))

    async def unload(ids):
        unloaded.extend(ids)
        held[:] = [h for h in held if h["id"] not in ids]
    hooks.unload = unload
    state = {"loaded": []}
    hooks.background_loaded = lambda: [{"id": i, "kind": "chat", "name": i, "state": "loaded", "vram_mb": None, "size_mb": None, "started_by_multiproxy": True} for i in state["loaded"]]

    async def restore(sid):
        restored.append(sid)
        return None
    u = unit(tmp_path, hooks=hooks, restore=restore)
    with pytest.raises(bcr.BenchError) as e:
        await u.jobs.start(OWNER, body_for())
    assert e.value.status == 409 and e.value.extra["busy_reason"] == "chat"                                # models are loaded and the plan does not say take over
    status, job = await u.jobs.start(OWNER, body_for(take_over=True))
    assert status == 201
    state["loaded"] = ["m2"]                                                                              # the person started Model Two again meanwhile
    done = await end_of(u, job["job_id"])
    assert done["state"] == "finished" and unloaded == ["m1", "m2"]
    await asyncio.sleep(0.2)
    assert restored == ["m1"] and done["services_restored"] == ["m1"]


async def test_services_the_test_started_are_stopped_afterwards(tmp_path):
    started, stopped = [], []
    hooks = br.Hooks()
    hooks.background_assigned = lambda: [{"id": "service:voice", "kind": "voice", "name": "Voice", "state": "assigned_stopped", "vram_mb": None, "size_mb": 2000, "started_by_multiproxy": False}]

    async def start_service(sid):
        started.append(sid)
        return None

    async def unload(ids):
        stopped.extend(ids)
    hooks.start_service, hooks.unload = start_service, unload
    u = unit(tmp_path, hooks=hooks)
    status, job = await u.jobs.start(OWNER, body_for(while_testing="start_normal"))
    assert status == 201
    done = await end_of(u, job["job_id"])
    assert done["state"] == "finished" and started == ["service:voice"] and stopped == ["service:voice"] and done["services_started"] == ["service:voice"]


async def test_a_refusal_by_the_runner_fails_the_job_with_its_plain_line(tmp_path):
    hooks = br.Hooks(engine_state=lambda: br.ENGINE_NOT_INSTALLED)
    u = unit(tmp_path, hooks=hooks)
    status, job = await u.jobs.start(OWNER, body_for())
    done = await end_of(u, job["job_id"])
    assert status == 201 and done["state"] == "failed" and done["reason"] == "refused" and "engine" in done["message"].lower()
    assert not u.jobs.lock_path.exists() and u.launcher.starts == 0


async def test_a_restarted_courier_reports_the_unfinished_job_interrupted_and_cleans_up(tmp_path, monkeypatch):
    import local_models
    import benchmarks_engine as be
    u = unit(tmp_path, models_root=tmp_path / "models")
    plan = rp.validate_plan(json.loads(body_for()), {"unit-model": {"file": "unit.gguf"}})
    job = {"job_id": "job-left-behind-0001", "request_id": plan["request_id"], "owner_id": OWNER, "job_token": "tok", "plan": plan, "state": "running", "reason": "", "message": "",
           "cancel_requested": False, "interrupt_reason": "", "created_at": "2026-10-28T10:00:00+00:00", "state_changed_at": "2026-10-28T10:00:00+00:00", "ended_at": None,
           "ended_epoch_s": None, "model_paths": [u.model], "runs": [], "services_started": ["service:voice"], "services_stopped_for_test": [], "services_restored": [], "batch_id": "b"}
    part = tmp_path / "models" / rp.JOBS_SUBDIR / job["job_id"]
    part.mkdir(parents=True)
    (part / "m.gguf.partial").write_bytes(b"half")
    (part / "m.gguf.partial.json").write_text("{}", encoding="utf-8")
    (part / "whole.gguf").write_bytes(b"keep")
    unrelated = tmp_path / "models" / "old-model.gguf"
    unrelated.write_bytes(b"mine")
    run_folder = u.jobs.runs_dir / "run-left-behind"
    run_folder.mkdir(parents=True)
    import benchmarks_store as bs
    bs.create_run(u.jobs.runs_dir, "run-2026-left", {"schema_version": 1, "status": "running", "steps_total": 5, "steps_done": 1})
    u.jobs.jobs[job["job_id"]] = job
    u.jobs._save(job)
    u.jobs.lock_path.write_text(json.dumps({"job_id": job["job_id"]}), encoding="utf-8")
    (u.jobs.jobs_dir / be.PID_FILE).write_text(json.dumps({"pid": 424242, "port": 1, "run_id": "x"}), encoding="utf-8")
    killed, stopped_services = [], []
    monkeypatch.setattr(local_models, "_pid_is_llama_server", lambda pid: pid == 424242)
    monkeypatch.setattr(local_models, "kill_tree_sync", lambda pid: killed.append(pid))

    async def stop_services(ids):
        stopped_services.extend(ids)
    fresh = bcr.CourierBenchmarks(tmp_path, u.svc, lambda: {"unit-model": u.model}, device_fn=lambda: {}, models_root=tmp_path / "models", free_mb_fn=lambda p: 1e6)      # (a new process reads the job file)
    assert fresh.jobs[job["job_id"]]["state"] == "running"
    out = await fresh.reconcile(stop_services)
    assert out["engine_stopped"] and killed == [424242] and out["jobs_interrupted"] == 1 and out["partial_removed"] == 2 and out["runs_marked"] == 1
    j = fresh.jobs[job["job_id"]]
    assert j["state"] == "interrupted" and j["reason"] == "courier_restarted" and "restarted" in j["message"] and stopped_services == ["service:voice"]
    assert not fresh.lock_path.exists() and not (fresh.jobs_dir / be.PID_FILE).exists()
    assert not (part / "m.gguf.partial").exists() and not (part / "m.gguf.partial.json").exists() and not (part / "whole.gguf").exists() and unrelated.exists()
    assert bs.read_manifest(u.jobs.runs_dir / "run-2026-left")["status"] == "interrupted"
    # a new job is accepted now, and the old one is still reported (never resumed)
    status, new = await fresh.start(OWNER, body_for())
    assert status == 201
    await asyncio.gather(fresh._rt[new["job_id"]]["task"], return_exceptions=True)
    assert fresh.jobs[job["job_id"]]["state"] == "interrupted"


async def test_a_stale_lock_with_no_active_job_does_not_block_a_start(tmp_path):
    u = unit(tmp_path)
    u.jobs.lock_path.write_text(json.dumps({"job_id": "job-ghost"}), encoding="utf-8")
    status, job = await u.jobs.start(OWNER, body_for())
    assert status == 201
    assert (await end_of(u, job["job_id"]))["state"] == "finished"


async def test_ended_jobs_are_kept_only_for_a_bounded_time_and_number(tmp_path):
    t = [time.time()]
    u = unit(tmp_path)
    u.jobs.wall = lambda: t[0]
    ids = []
    for i in range(7):
        status, job = await u.jobs.start(OWNER, body_for())
        assert status == 201
        await end_of(u, job["job_id"])
        await asyncio.sleep(0.1)
        ids.append(job["job_id"])
        t[0] += 60
    kept = [j for j in ids if j in u.jobs.jobs]
    assert len(kept) == bcr.MAX_UNCOLLECTED and kept == ids[-5:]
    t[0] += 15 * 86400
    u.jobs.prune()
    assert not [j for j in ids if j in u.jobs.jobs]
    assert not list(u.jobs.runs_dir.glob("run-*"))                                                        # the run folders of dropped jobs go with them


# --- protect the PC: the device acts alone ---------------------------------------------------------------------------------------------------------------------------------------

def _low_memory_env(tmp_path, monkeypatch, **kw):
    import benchmarks_guard as bg
    fast = bg.GuardConfig(warn_hold_s=0.15, stop_hold_s=0.15, emergency_hold_s=0.06, settle_s=0.0, gate_hold_s=0.05, gate_timeout_s=0.5)
    low = lambda: {"cpu_util_pct": 5.0, "ram_used_mb": 63000, "ram_total_mb": 64000, "ram_available_mb": 1000}                   # 1,000 MB of 64,000 is under the emergency floor
    return make_env(tmp_path, monkeypatch, engine_kwargs=lambda n: {"token_s": 0.02, "tokens": 30}, machine=low, service_extra={"guard": fast, "guard_poll_s": 0.02}, **kw)


@pytest_asyncio.fixture
async def low(tmp_path, monkeypatch):
    e = _low_memory_env(tmp_path, monkeypatch)
    await e.courier.start()
    yield e
    await e.courier.stop()
    for eng in e.launcher.engines:
        eng.stop()


async def test_the_device_stops_a_model_that_runs_it_out_of_memory_by_itself_and_keeps_the_result(low):
    _, job = await low.start(plan_body(low.model_id, **LONG))
    st = await low.wait_state(job, rp.ENDED_STATES)
    assert st["runs"][0]["state"] == "stopped_for_memory" and st["runs"][0]["reason"] == "memory_emergency"
    assert st["state"] == "finished"                                                                         # the batch went through: a stopped model is not a failed job
    run = low.courier.benchmarks.runs_dir / st["runs"][0]["run_id"]
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "stopped_for_memory" and m["safety_stop"]["kind"] == "memory" and m["safety_stop"]["samples_before"] and m["protections"]["memory_guard"] is True
    assert low.launcher.stops >= 1 and not any(e.running for e in low.launcher.engines) and not low.courier.benchmarks.lock_path.exists()


async def test_a_main_pc_that_goes_quiet_during_the_pressure_does_not_change_what_the_device_stored(tmp_path, monkeypatch):
    e = _low_memory_env(tmp_path, monkeypatch, lease_s=1.5)
    await e.courier.start()
    try:
        _, job = await e.start(plan_body(e.model_id, **LONG))                                                 # then nobody asks again: the lease runs out while the device is protecting itself
        end = time.monotonic() + 20
        while time.monotonic() < end and e.courier.benchmarks.jobs[job["job_id"]]["state"] not in rp.ENDED_STATES:
            await asyncio.sleep(0.05)
        view = e.courier.benchmarks.view(e.courier.benchmarks.jobs[job["job_id"]])
        run = e.courier.benchmarks.runs_dir / view["runs"][0]["run_id"]
        m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        assert m["status"] == "stopped_for_memory" and m["safety_stop"]["reason"] == "memory_emergency" and m["status"] in bc.RUN_STATES_FINAL        # one stored result, the device's own
        assert e.launcher.stops >= 1 and not any(x.running for x in e.launcher.engines)
    finally:
        await e.courier.stop()
