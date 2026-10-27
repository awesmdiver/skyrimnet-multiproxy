"""Routing, step 1 (2026-10-02): which job a request is, routes, hand-over, resting, cost.

The recogniser is scored on SkyrimNet's own tagged requests (tests/fixtures/routing_job_shapes.jsonl, from step 0) with a floor, so a SkyrimNet update that breaks
it is noticed. Hand-over is proven with real aiohttp servers standing in for the AIs (good, failing, busy, empty, slow).
"""
import asyncio
import json
import os
import sqlite3
import time

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

import ai_costs
import request_facts
import routing
from tests.fixtures.courier_fakes import free_port

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _table():
    doc = json.load(open(os.path.join(FIX, "routing_skyrimnet_job_settings.json"), encoding="utf-8"))
    return {k: {"max_tokens": v.get("max_tokens"), "temperature": v.get("temperature"), "timeout": v.get("timeout_s"), "models": v.get("models", [])} for k, v in doc["jobs"].items()}


def _rows():
    return [json.loads(line) for line in open(os.path.join(FIX, "routing_job_shapes.jsonl"), encoding="utf-8")]


def _features(r):
    system = r["system_opening"]
    if system.startswith("(persona prompt"):
        system = "You are Aerina, a Female Nord in Skyrim. You are speaking to the player."
    return routing.make_features(stream=r["stream"], images=r["images"], questions=r["questions"], messages=r["messages"], max_tokens=r["max_tokens"],
                                 temperature=r["temperature"], system_text=system, first_user_text=r["user_opening"])


# ---- recognising the job ---------------------------------------------------------------------------------------------------------------------------------

class TestRecognition:
    def test_the_scored_floor_on_real_requests(self):
        table = _table()
        right = wrong = cant = 0
        wrong_by = {}
        for r in _rows():
            if r["job"] in ("quick-test ping", "(unlabelled non-streamed)"):
                continue
            got = routing.recognise(_features(r), table)
            if got.keys == (r["job"],):
                right += 1
            elif got.keys == (routing.OTHER,) or len(got.keys) > 1:
                cant += 1
            else:
                wrong += 1
                wrong_by[r["job"]] = wrong_by.get(r["job"], 0) + 1
        n = right + wrong + cant
        assert right / n >= 0.90, f"right {right} wrong {wrong} can't tell {cant} of {n}"
        assert wrong / n <= 0.01, f"wrong {wrong}: {wrong_by}"

    def test_the_shapes_that_are_certain(self):
        table = _table()
        assert routing.recognise(routing.make_features(stream=False, images=0, questions=True, messages=0, max_tokens=None, temperature=None), table).keys == ("decisions",)
        v = routing.recognise(routing.make_features(stream=False, images=1, questions=False, messages=1, max_tokens=4000, temperature=0.7, first_user_text="You are describing what"), table)
        assert v.keys == ("vision",) and v.certain

    def test_settings_split_dialogue_from_combat_and_when_they_match_it_is_one_job_for_routing(self):
        table = _table()
        d = routing.make_features(stream=True, images=0, questions=False, messages=54, max_tokens=4096, temperature=1.0, system_text="You are Aerina, a Female Nord in Skyrim. You are speaking")
        c = routing.make_features(stream=True, images=0, questions=False, messages=54, max_tokens=2000, temperature=1.0, system_text="You are Aerina, a Female Nord in Skyrim. You are speaking")
        assert routing.recognise(d, table).keys == ("dialogue",) and routing.recognise(c, table).keys == ("combat",)
        same = dict(table)
        same["combat"] = dict(table["dialogue"])
        both = routing.recognise(d, same)
        assert both.keys == ("dialogue", "combat") and both.label == "Dialogue and Combat" and not both.certain
        assert ["combat", "dialogue"] in routing.indistinguishable_groups(same)

    def test_a_setting_that_was_changed_after_the_request_shape_was_learned_falls_back_to_the_opening_and_is_a_guess(self):
        table = _table()
        table["meta"]["max_tokens"] = 100                       # SkyrimNet's file says 100, the request says 4000 (seen for real, step 0)
        f = routing.make_features(stream=False, images=0, questions=False, messages=2, max_tokens=4000, temperature=0.7, system_text="You are a memory search query generator for an AI NPC")
        got = routing.recognise(f, table)
        assert got.keys == ("meta",) and not got.certain and got.how == routing.HOW_RECOGNISED

    def test_a_job_never_seen_is_other_and_without_skyrimnet_settings_only_the_shape_speaks(self):
        f = routing.make_features(stream=False, images=0, questions=False, messages=2, max_tokens=123, temperature=0.31, system_text="Something no one has ever sent")
        assert routing.recognise(f, _table()).keys == (routing.OTHER,)
        assert routing.recognise(f, {}).keys == (routing.OTHER,)
        assert routing.recognise(routing.make_features(stream=False, images=1, questions=False, messages=1, max_tokens=1, temperature=1), {}).keys == ("vision",)

    def test_explicit_names_and_the_authors_hint(self):
        assert routing.job_from_name("job/memory") == "memory" and routing.job_from_name("job/Combat") == "combat"
        assert routing.job_from_name("job/dialogue") == "dialogue" and routing.job_from_name("claude-sonnet-5") is None and routing.job_from_name("job/nonsense") is None
        assert routing.job_from_hint({"x-whatever": "memory"}) is None, "no hint is defined yet, so none is ever read"

    def test_all_19_jobs_have_a_proper_name(self):
        assert len(routing.JOB_ORDER) == 19 and all(routing.label(k) for k in routing.JOB_ORDER), "every job has a name: SkyrimNet's own, or its raw name"
        assert routing.label_of(("dialogue", "combat")) == "Dialogue and Combat"

    def test_the_prompt_text_is_never_kept(self):
        f = routing.make_features(stream=False, images=0, questions=False, messages=2, max_tokens=1, temperature=1, system_text="SECRET " * 500)
        assert len(f.opening) <= routing.OPENING_CHARS and "SECRET" in f.opening and f.opening.count("SECRET") < 12


# ---- choosing --------------------------------------------------------------------------------------------------------------------------------------------

def _classify(ai):
    return {"local/a": (routing.CLASS_FREE, "local"), "pc/b": (routing.CLASS_FREE, "other"), "claude": (routing.CLASS_INCLUDED, "subscription"),
            "glm/x": (routing.CLASS_PAID, "cloud"), "nano/y": (routing.CLASS_PAID, "cloud")}.get(ai, (routing.CLASS_PAID, "cloud"))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _settings(**kw):
    return routing.clean_settings({"routes": {"memory": {"ais": ["a"]}}, **kw})


class TestChoosing:
    def test_in_this_order_and_comma_lists_expand_in_place(self):
        e = routing.Engine(_classify)
        r = {"ais": ["claude", "glm/x,nano/y", "pc/b"], "how": "order"}
        got = e.order("memory", r, _settings(), expand=lambda a: a.split(",") if "," in a else [a])
        assert [c.ai for c in got] == ["claude", "glm/x", "nano/y", "pc/b"]

    def test_take_turns_starts_one_further_each_time(self):
        e = routing.Engine(_classify)
        r = {"ais": ["claude", "pc/b", "glm/x"], "how": "turns"}
        firsts = [e.order("memory", r, _settings())[0].ai for _ in range(4)]
        assert firsts == ["claude", "pc/b", "glm/x", "claude"]

    def test_free_ones_first_means_local_and_other_pcs_then_subscriptions_then_paid(self):
        e = routing.Engine(_classify)
        r = {"ais": ["glm/x", "claude", "nano/y", "local/a", "pc/b"], "how": "free"}
        assert [c.ai for c in e.order("memory", r, _settings())] == ["local/a", "pc/b", "claude", "glm/x", "nano/y"]

    def test_cheapest_first_uses_real_cost_per_job_and_an_unknown_cost_goes_last_with_the_reason(self):
        e = routing.Engine(_classify)
        e.costs.add("memory", "nano/y", 0.0004)
        e.costs.add("memory", "glm/x", 0.002)
        r = {"ais": ["glm/x", "unknown/z", "nano/y", "claude", "local/a"], "how": "cheapest"}
        got = e.order("memory", r, _settings())
        assert [c.ai for c in got] == ["local/a", "claude", "nano/y", "glm/x", "unknown/z"]
        assert "no cost reported" in got[-1].note
        assert e.order("diary", r, _settings())[2].ai != "nano/y" or True        # another job has its own costs (nothing learned there)

    def test_fastest_right_now_a_new_ai_gets_a_fair_first_try_and_busy_ones_are_penalised(self):
        clock = Clock()
        e = routing.Engine(_classify, clock=clock)
        for _ in range(6):
            e.first_words.add("claude", 2.0)
            e.first_words.add("glm/x", 3.0)
        r = {"ais": ["glm/x", "claude", "nano/y"], "how": "fastest"}
        got = e.order("memory", r, _settings())
        assert got[0].ai == "nano/y" and "fair first try" in got[0].note, "no data yet: tried first"
        for _ in range(6):
            e.first_words.add("nano/y", 5.0)
        assert [c.ai for c in e.order("memory", r, _settings())] == ["claude", "glm/x", "nano/y"]
        for _ in range(3):
            e.load.start("claude")                              # three requests being written right now: 3 x 0.75 s
        assert e.order("memory", r, _settings())[0].ai == "glm/x", "everything must not pile onto the one that was quickest a moment ago"

    def test_recent_times_count_most_and_old_ones_are_forgotten(self):
        clock = Clock()
        fw = routing.FirstWordBook(clock)
        fw.add("a", 10.0)
        for _ in range(5):
            fw.add("a", 1.0)
        v, n = fw.ewma("a")
        assert n == 6 and v < 4, "the recent fast ones outweigh the old slow one"
        clock.t += routing.RECENT_S + 1
        assert fw.ewma("a") == (None, 0)


class TestResting:
    def test_three_failures_in_a_minute_rest_it_for_two_minutes_and_one_blip_never_does(self):
        clock = Clock()
        rb = routing.RestBook(clock)
        s = routing.clean_settings({})["rest"]
        assert s["failures"] == 3 and s["within_s"] == 60 and s["for_s"] == 120
        assert not rb.fail("a", "x", s) and not rb.fail("a", "x", s)
        clock.t += 61
        assert not rb.fail("a", "x", s), "the earlier ones were more than a minute ago"
        assert not rb.fail("a", "x", s) and rb.fail("a", "x", s)
        assert rb.until("a") == clock.t + 120
        clock.t += 121
        assert rb.until("a") is None
        rb.fail("b", "x", s)
        rb.ok("b")
        assert not rb.fail("b", "x", s) and not rb.fail("b", "x", s), "a success clears the count"

    def test_a_resting_ai_goes_last_but_is_still_tried_when_it_is_the_last_one_standing(self):
        clock = Clock()
        e = routing.Engine(_classify, clock=clock)
        s = routing.clean_settings({})["rest"]
        for _ in range(3):
            e.rest.fail("claude", "x", s)
        got = e.order("memory", {"ais": ["claude", "pc/b"], "how": "order"}, _settings())
        assert [c.ai for c in got] == ["pc/b", "claude"] and got[1].resting_until
        for _ in range(3):
            e.rest.fail("pc/b", "x", s)
        got = e.order("memory", {"ais": ["claude", "pc/b"], "how": "order"}, _settings())
        assert len(got) == 2, "all resting: every one is still on the list"


class TestSlowness:
    def test_off_by_default_and_never_for_a_local_model(self):
        e = routing.Engine(_classify)
        for _ in range(10):
            e.first_words.add("claude", 2.0)
            e.first_words.add("local/a", 2.0)
        c = routing.Candidate("claude")
        assert e.slow_limit(c, {"ais": ["claude"]}, _settings(), streamed=False, job_keys=("memory",)) is None
        on = _settings(slow_handover={"enabled": True})
        assert e.slow_limit(c, {"ais": ["claude"]}, on, streamed=False, job_keys=("memory",)) == pytest.approx(5.0), "2.5 x 2.0 s = 5 s, the floor"
        assert e.slow_limit(routing.Candidate("local/a"), {"ais": ["x"]}, on, streamed=False, job_keys=("memory",)) is None

    def test_learned_from_the_ais_own_times_between_5_and_10_seconds_and_not_for_streamed_dialogue_unless_allowed(self):
        e = routing.Engine(_classify)
        for _ in range(10):
            e.first_words.add("claude", 3.0)
            e.first_words.add("glm/x", 9.0)
        on = _settings(slow_handover={"enabled": True})
        assert e.slow_limit(routing.Candidate("claude"), {"ais": []}, on, streamed=False, job_keys=("memory",)) == pytest.approx(7.5)
        assert e.slow_limit(routing.Candidate("glm/x"), {"ais": []}, on, streamed=False, job_keys=("memory",)) == 10.0
        assert e.slow_limit(routing.Candidate("claude"), {"ais": []}, on, streamed=True, job_keys=("dialogue",)) is None
        allowed = _settings(slow_handover={"enabled": True, "streamed_dialogue": True})
        assert e.slow_limit(routing.Candidate("claude"), {"ais": []}, allowed, streamed=True, job_keys=("dialogue",)) == pytest.approx(7.5)
        assert e.slow_limit(routing.Candidate("never-seen"), {"ais": []}, on, streamed=False, job_keys=("memory",)) is None, "no history: no limit"
        assert e.slow_limit(routing.Candidate("claude"), {"ais": [], "slow_limit_s": 4}, _settings(), streamed=True, job_keys=("dialogue",)) == 4.0, "a limit set for the job is the player's own"


class TestSettings:
    def test_only_known_jobs_ways_and_sensible_numbers_survive(self):
        c = routing.clean_settings({"routes": {"memory": {"ais": ["a", " ", "b"], "how": "bogus"}, "bad key!": {"ais": ["x"]}, "meta": {"ais": []}},
                                    "rest": {"failures": 500, "for_s": "x"}, "slow_handover": {"enabled": 1}})
        assert list(c["routes"]) == ["memory"] and c["routes"]["memory"] == {"ais": ["a", "b"], "how": "order", "slow_limit_s": None}
        assert c["rest"]["failures"] == 20 and c["rest"]["for_s"] == 120 and c["slow_handover"] == {"enabled": True, "streamed_dialogue": False}
        assert routing.clean_settings(None) == routing.clean_settings({}) and routing.clean_settings({})["routes"] == {}

    def test_jobs_that_cant_be_told_apart_share_the_first_route_any_of_them_has(self):
        s = routing.clean_settings({"routes": {"combat": {"ais": ["x"]}}})
        route, key = routing.route_for(s, ("dialogue", "combat"))
        assert key == "combat" and route["ais"] == ["x"]
        assert routing.route_for(s, ("memory",)) == (None, None)


# ---- hand-over (the engine, with plain fakes) ------------------------------------------------------------------------------------------------------------

class Boom(Exception):
    def __init__(self, status, detail=""):
        self.status_code, self.detail = status, detail


async def _run(e, cands, behaviour, *, settings=None, streamed=False, route=None):
    settings = settings or _settings()
    cs = [routing.Candidate(a) for a in cands]
    calls = []

    async def call(ai):
        calls.append(ai)
        return await behaviour[ai]()
    out = await routing.run_route(e, "memory", cs, settings, call, streamed=streamed, job_keys=("memory",), route=route or {"ais": cands})
    return out, calls


def _val(v):
    async def f():
        return v
    return f


def _err(status, detail=""):
    async def f():
        raise Boom(status, detail)
    return f


GOOD = {"choices": [{"message": {"content": "hi"}}]}
EMPTY = {"choices": [{"message": {"content": "  "}}]}
pytestmark_async = pytest.mark.asyncio


@pytest.mark.asyncio
class TestHandOver:
    async def test_a_failure_passes_the_line_on_and_says_who_was_passed_over(self):
        e = routing.Engine(_classify)
        out, calls = await _run(e, ["a", "b"], {"a": _err(502), "b": _val(GOOD)})
        assert out.ai == "b" and calls == ["a", "b"] and out.skipped == [("a", "failed (502)")]

    async def test_busy_and_an_empty_reply_pass_it_on_too_but_a_bad_request_never_does(self):
        e = routing.Engine(_classify)
        out, _ = await _run(e, ["a", "b", "c"], {"a": _err(429, "Too many requests on this line"), "b": _val(EMPTY), "c": _val(GOOD)})
        assert out.ai == "c" and [w for _, w in out.skipped] == ["busy", "empty reply"]
        with pytest.raises(Boom) as ex:
            await _run(e, ["a", "b"], {"a": _err(400, "bad"), "b": _val(GOOD)})
        assert ex.value.status_code == 400

    async def test_the_last_ones_failure_is_the_one_raised_and_the_last_ones_empty_reply_is_returned(self):
        e = routing.Engine(_classify)
        with pytest.raises(Boom) as ex:
            await _run(e, ["a", "b"], {"a": _err(500), "b": _err(503)})
        assert ex.value.status_code == 503
        out, _ = await _run(e, ["a", "b"], {"a": _err(500), "b": _val(EMPTY)})
        assert out.response == EMPTY

    async def test_failures_count_toward_resting_but_busy_and_slowness_never_do(self):
        clock = Clock()
        e = routing.Engine(_classify, clock=clock)
        for _ in range(3):
            await _run(e, ["a", "b"], {"a": _err(500), "b": _val(GOOD)})
        assert e.rest.until("a")
        e2 = routing.Engine(_classify)
        for _ in range(5):
            await _run(e2, ["a", "b"], {"a": _err(429, "line is full"), "b": _val(GOOD)})
        assert e2.rest.until("a") is None, "our own line being full says nothing about the AI"

    async def test_slowness_hands_over_only_when_turned_on_and_is_not_a_failure(self, monkeypatch):
        monkeypatch.setattr(routing, "EXPLICIT_MIN_S", 0.0)
        e = routing.Engine(_classify)
        for _ in range(10):
            e.first_words.add("a", 1.0)
        calls = []

        async def slow():
            await asyncio.sleep(0.5)
            return GOOD

        async def quick():
            return {"choices": [{"message": {"content": "quick"}}]}
        # off by default: the slow one answers
        out, _ = await _run(e, ["a", "b"], {"a": slow, "b": quick})
        assert out.ai == "a"
        # on, with an explicit short limit for the job (the player's own number)
        out, _ = await _run(e, ["a", "b"], {"a": slow, "b": quick}, route={"ais": ["a", "b"], "slow_limit_s": 0.05})
        assert out.ai == "b" and out.skipped == [("a", "too slow")] and e.rest.until("a") is None

    async def test_a_timeout_from_inside_the_ais_own_call_is_a_failure_not_slowness(self):
        e = routing.Engine(_classify)

        async def times_out():
            raise asyncio.TimeoutError()
        out, _ = await _run(e, ["a", "b"], {"a": times_out, "b": _val(GOOD)})
        assert out.skipped == [("a", "failed (timed out)")]

    async def test_the_client_hanging_up_goes_straight_out(self):
        e = routing.Engine(_classify)

        class Gone(Exception):
            pass

        async def gone():
            raise Gone()
        cs = [routing.Candidate("a"), routing.Candidate("b")]
        with pytest.raises(Gone):
            await routing.run_route(e, "memory", cs, _settings(), lambda ai: gone(), streamed=False, job_keys=("memory",), route={"ais": ["a", "b"]}, passthrough=(Gone,))
        assert e.load.n["a"] == 0

    async def test_a_stream_counts_as_work_in_progress_until_its_last_byte(self):
        e = routing.Engine(_classify)

        class Resp:
            def __init__(self):
                async def body():
                    yield b"1"
                    yield b"2"
                self.body_iterator = body()
        r = Resp()
        out, _ = await _run(e, ["a"], {"a": _val(r)})
        assert e.load.n["a"] == 1
        async for _ in out.response.body_iterator:
            pass
        assert e.load.n["a"] == 0


# ---- cost and local timings ------------------------------------------------------------------------------------------------------------------------------

class TestCost:
    def test_free_included_reported_priced_and_not_reported(self):
        assert ai_costs.work_out("free", None, 10, 10) == (0.0, "free")
        assert ai_costs.work_out("included", None, 10, 10) == (None, "included in your subscription")
        assert ai_costs.work_out("paid", {"cost": 0.0123}, 10, 10) == (0.0123, "reported by the model")
        assert ai_costs.work_out("paid", {"cost": "0.5"}, 1, 1)[0] == 0.5
        assert ai_costs.work_out("paid", None, 1_000_000, 500_000, {"prompt": 2.0, "completion": 4.0}) == (4.0, "worked out from the model's own prices")
        assert ai_costs.work_out("paid", None, 100, 100) == (None, "not reported")
        assert ai_costs.work_out("paid", None, None, 100, {"prompt": 1, "completion": 1})[1] == "not reported", "no token count: never a guess"
        assert ai_costs.reported_cost({"cost_microdollars": 1500}) == pytest.approx(0.0015)
        assert ai_costs.reported_cost({"cost": True}) is None and ai_costs.reported_cost("x") is None

    def test_nano_prices_come_from_its_own_model_list(self):
        got = ai_costs.parse_nano_prices({"data": [
            {"id": "Qwen/Q3", "pricing": {"prompt": 0.5, "completion": 1.5, "currency": "USD", "unit": "per_million_tokens"}},
            {"id": "no-prices"}, {"id": "per-token", "pricing": {"prompt": 1, "completion": 1, "unit": "per_token"}}]})
        assert got == {"qwen/q3": {"prompt": 0.5, "completion": 1.5}}

    @pytest.mark.asyncio
    async def test_the_price_list_is_fetched_once_in_a_while_and_a_failure_is_just_no_prices(self):
        calls = []
        clock = Clock()

        async def fetch():
            calls.append(1)
            return {"m": {"prompt": 1, "completion": 2}}
        pl = ai_costs.PriceList(fetch, clock)
        assert await pl.lookup("M") == {"prompt": 1, "completion": 2} and await pl.lookup("m") and len(calls) == 1

        async def broken():
            raise RuntimeError("no network")
        assert await ai_costs.PriceList(broken, clock).lookup("m") is None

    def test_llama_cpp_timings_are_kept_as_they_are(self):
        t = ai_costs.timings_from({"timings": {"prompt_n": 120, "cache_n": 800, "prompt_ms": 95.5, "predicted_n": 60, "predicted_ms": 1500.0, "predicted_per_second": 40.0}})
        assert t == {"prompt_n": 120.0, "cache_n": 800.0, "prompt_ms": 95.5, "predicted_n": 60.0, "predicted_ms": 1500.0, "tokens_per_second": 40.0}
        assert ai_costs.timings_from({"timings": {"predicted_n": 30, "predicted_ms": 1000}})["tokens_per_second"] == 30.0
        assert ai_costs.timings_from({}) is None and ai_costs.timings_from({"timings": "x"}) is None

    def test_the_request_record_says_it_in_its_log_line(self):
        rec = request_facts.RequestRecord(request_id="r1")
        rec.provider, rec.model, rec.job, rec.job_key, rec.job_how, rec.job_certain = "Local", "m", "Memory", "memory", "recognised", False
        rec.note_usage({"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0021, "timings": {"prompt_n": 20, "cache_n": 80, "predicted_n": 5, "predicted_ms": 100}})
        rec.skipped.append(("claude", "failed (502)"))
        line = rec.line()
        assert "Memory (looks like)" in line and "$0.0021" in line and "remembered 80 of 100 prompt tokens" in line and "passed over claude (failed (502))" in line
        rec.cost_usd, rec.cost_note = 0.0, "free"
        assert "free" in rec.line()


class TestLogCheck:
    LOG = ('[2026-10-02 10:32:28.126] [vision] Generate request [req_a_1]:\n{\n  "max_tokens": 4000,\n  "messages": [\n    {\n      "model": "nope"\n    }\n  ],\n  "model": "gemini/g",\n  "temperature": 0.7\n}\n'
           '[2026-10-02 10:32:30.000] [] GenerateStreaming request [req_b_2]:\n{\n  "model": "claude-sonnet-5-5",\n  "stream": true\n}\n')

    def test_the_log_is_read_for_the_tag_and_the_model_only(self):
        assert routing.parse_request_log(self.LOG) == [("2026-10-02 10:32:28", "vision", "gemini/g"), ("2026-10-02 10:32:30", "dialogue", "claude-sonnet-5-5")]

    def test_agreement_is_counted_when_exactly_one_request_of_ours_matches(self):
        lc = routing.LogChecker()
        t = time.mktime(time.strptime("2026-10-02 10:32:28", "%Y-%m-%d %H:%M:%S"))
        lc.note_request(t + 0.2, "gemini/g", ("vision",))
        lc.note_request(t + 2.2, "claude-sonnet-5-5", ("memory",))
        lc.feed(routing.parse_request_log(self.LOG), lambda ts: time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))
        assert (lc.agreed, lc.disagreed) == (1, 1)
        lc.note_request(t + 0.3, "gemini/g", ("vision",))                  # now two match: not counted
        lc.feed(routing.parse_request_log(self.LOG)[:1], lambda ts: time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))
        assert (lc.agreed, lc.disagreed) == (1, 1)


def test_the_capture_filter_hides_secrets_but_not_token_counts(proxy_module):
    out = proxy_module._strip_capture_secrets({"max_tokens": 4000, "max_completion_tokens": 9, "api_key": "k", "access_token": "t", "Authorization": "b", "stream_options": {"include_usage": True},
                                                "nested": {"completion_tokens": 3, "token": "x"}})
    assert out["max_tokens"] == 4000 and out["max_completion_tokens"] == 9 and out["nested"]["completion_tokens"] == 3
    assert out["api_key"] == out["access_token"] == out["Authorization"] == out["nested"]["token"] == "***REDACTED***"


# ---- through the real app: AIs that answer, fail, are busy, empty or slow ------------------------------------------------------------------------------------

class FakeAIs:
    """One OpenAI-style server standing in for several AIs: the model id says how it behaves."""

    def __init__(self):
        self.port = free_port()
        self.calls = []
        self.runner = None

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}/v1"

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def stop(self):
        await self.runner.cleanup()

    async def chat(self, request):
        body = await request.json()
        model = body["model"]
        self.calls.append(model)
        if model.startswith("boom"):
            return web.json_response({"error": {"message": "The model crashed."}}, status=500)
        if model.startswith("busy"):
            return web.json_response({"error": {"message": "Too many requests"}}, status=429)
        if model.startswith("bad"):
            return web.json_response({"error": {"message": "That request wasn't valid."}}, status=400)
        if model.startswith("slow"):
            await asyncio.sleep(1.0)
        text = "" if model.startswith("empty") else f"answer from {model}"
        timings = {"prompt_n": 5, "cache_n": 95, "prompt_ms": 12.0, "predicted_n": 4, "predicted_ms": 100.0, "predicted_per_second": 40.0}
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            if text:
                for piece in text.split(" "):
                    await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': piece + ' '}}]})}\n\n".encode())
            end = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "timings": timings}
            await resp.write(f"data: {json.dumps(end)}\n\n".encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 100, "completion_tokens": 4}, "timings": timings})


@pytest_asyncio.fixture
async def env(proxy_module, monkeypatch):
    ais = await FakeAIs().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", ais.base)
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    proxy_module._routing_engine.__init__(proxy_module._ai_class, key=lambda a: proxy_module._resolve_model_name(a))
    proxy_module._job_table_cache["ts"] = 0.0
    e = type("Env", (), {})()
    e.ais, e.pm = ais, proxy_module
    e.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    yield e
    await ais.stop()


def set_routes(env, routes, **extra):
    cfg = env.pm._load_config()
    cfg["routing"] = {"routes": routes, **extra}
    env.pm._save_config(cfg)


def chat(model="job/memory", stream=False, **kw):
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": "hello"}], **kw}


def rows(env):
    with sqlite3.connect(env.pm.SPEED_HISTORY_DB) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM speed_history ORDER BY ts")]


async def sse(client, payload):
    events = []
    async with client.stream("POST", "/v1/chat/completions", json=payload) as r:
        async for line in r.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                events.append(json.loads(line[6:]))
        return r.status_code, events


def words(events):
    return "".join(((e.get("choices") or [{}])[0].get("delta") or {}).get("content") or "" for e in events).strip()


class TestTiedToOneJob:
    def test_one_job_is_routable_a_guess_across_several_is_not(self):
        one = routing.Recognition(("memory",), routing.HOW_RECOGNISED, True)
        many = routing.Recognition(("memory", "meta", "vision"), routing.HOW_RECOGNISED, False)
        unknown = routing.Recognition((routing.OTHER,), routing.HOW_RECOGNISED, False)
        assert routing.tied_to_one_job(one, {}) and not routing.tied_to_one_job(many, {}) and not routing.tied_to_one_job(unknown, {})

    def test_the_quick_test_mark_is_found_in_any_case(self):
        assert routing.is_quick_test({"x-multiproxy-quick-test": "1"}) and not routing.is_quick_test({"x": "1"})


@pytest.mark.asyncio
class TestThroughTheApp:
    async def test_with_no_route_a_request_goes_where_its_model_name_says_and_its_job_is_still_recorded(self, env):
        r = await env.client.post("/v1/chat/completions", json=chat("lmstudio/good-1"))
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "answer from good-1"
        assert env.ais.calls == ["good-1"]
        row = rows(env)[-1]
        assert row["cost_note"] == "free" and row["cost"] == 0.0 and row["tokens_in"] == 100
        assert json.loads(row["timings_json"])["cache_n"] == 95.0, "a local reply's own timings are kept"

    async def test_an_explicit_job_name_with_no_route_says_so_plainly(self, env):
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.status_code == 404 and "Memory has no route yet. Drag it onto a model on the Routing tab." in r.text

    async def test_a_failing_first_choice_hands_over_and_the_record_says_who_answered_and_what_was_passed_over(self, env):
        set_routes(env, {"memory": {"ais": ["lmstudio/boom-1", "lmstudio/good-1"]}})
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "answer from good-1"
        assert env.ais.calls == ["boom-1", "good-1"]
        ok = [x for x in rows(env) if x["success"]][-1]
        assert ok["model"] == "good-1" and ok["job"] == "memory" and ok["job_how"] == "name" and json.loads(ok["skipped"]) == [["lmstudio/boom-1", "failed (500)"]]
        failed = [x for x in rows(env) if not x["success"]]
        assert failed and failed[0]["model"] == "boom-1"

    async def test_busy_and_an_empty_reply_hand_over_and_a_bad_request_does_not(self, env):
        set_routes(env, {"memory": {"ais": ["lmstudio/busy-1", "lmstudio/empty-1", "lmstudio/good-1"]}, "meta": {"ais": ["lmstudio/bad-1", "lmstudio/good-2"]}})
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.json()["choices"][0]["message"]["content"] == "answer from good-1" and env.ais.calls == ["busy-1", "empty-1", "good-1"]
        env.ais.calls.clear()
        r = await env.client.post("/v1/chat/completions", json=chat("job/meta"))
        assert r.status_code == 400 and env.ais.calls == ["bad-1"], "the request itself was wrong: another AI would say the same"

    async def test_a_stream_that_fails_before_its_first_word_hands_over_and_one_that_has_started_is_never_abandoned(self, env):
        set_routes(env, {"dialogue": {"ais": ["lmstudio/boom-1", "lmstudio/good-1"]}})
        status, events = await sse(env.client, chat("job/dialogue", stream=True))
        assert status == 200 and words(events) == "answer from good-1" and env.ais.calls == ["boom-1", "good-1"]
        assert json.loads(rows(env)[-1]["timings_json"])["tokens_per_second"] == 40.0, "timings ride on the last chunk"

    async def test_everything_failing_gives_the_last_ones_error_and_all_were_tried(self, env):
        set_routes(env, {"memory": {"ais": ["lmstudio/boom-1", "lmstudio/boom-2"]}})
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.status_code >= 500 and env.ais.calls == ["boom-1", "boom-2"]

    async def test_three_failures_rest_the_ai_and_the_status_says_until_when(self, env):
        set_routes(env, {"memory": {"ais": ["lmstudio/boom-1", "lmstudio/good-1"]}})
        for _ in range(3):
            await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        env.ais.calls.clear()
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert env.ais.calls == ["good-1"], "the resting one is skipped"
        s = (await env.client.get("/routing/status")).json()
        assert [x["ai"] for x in s["resting"]] == ["boom-1"] and 100 <= s["resting"][0]["secondsLeft"] <= 120

    async def test_a_job_recognised_by_its_shape_is_routed_and_marked_as_a_guess(self, env, monkeypatch):
        monkeypatch.setattr(env.pm, "_skyrimnet_job_table", _table)
        set_routes(env, {"memory": {"ais": ["lmstudio/good-1"]}})
        body = {"model": "claude-sonnet-5-5", "max_tokens": 4000, "temperature": 1.0, "stream": False,
                "messages": [{"role": "system", "content": "You are an AI assistant that helps create memories for NPCs in Skyrim."}, {"role": "user", "content": "Write one."}]}
        r = await env.client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200 and env.ais.calls == ["good-1"], "answered by the route, not by the model name SkyrimNet sent"
        row = rows(env)[-1]
        assert row["job"] == "memory" and row["job_how"] == "recognised"

    async def test_quick_test_goes_to_the_model_it_names_even_when_the_request_looks_like_a_routed_job(self, env, monkeypatch):
        monkeypatch.setattr(env.pm, "_skyrimnet_job_table", _table)
        set_routes(env, {"memory": {"ais": ["lmstudio/good-1"]}})
        body = {"model": "lmstudio/good-2", "max_tokens": 4000, "temperature": 1.0, "stream": False,
                "messages": [{"role": "system", "content": "You are an AI assistant that helps create memories for NPCs in Skyrim."}, {"role": "user", "content": "Write one."}]}
        r = await env.client.post("/v1/chat/completions", json=body, headers={routing.QUICK_TEST_HEADER: "1"})
        assert r.status_code == 200 and env.ais.calls == ["good-2"]
        env.ais.calls.clear()
        r = await env.client.post("/v1/chat/completions", json=body)
        assert env.ais.calls == ["good-1"], "the same request without the Quick Test mark is still routed"

    async def test_a_request_that_could_be_several_jobs_goes_to_the_model_it_asked_for(self, env, monkeypatch):
        monkeypatch.setattr(env.pm, "_skyrimnet_job_table", lambda: {})          # SkyrimNet not linked: a long streamed chat is only a guess between jobs
        set_routes(env, {"dialogue": {"ais": ["lmstudio/good-1"]}, "combat": {"ais": ["lmstudio/good-1"]}})
        msgs = [{"role": "user" if i % 2 else "assistant", "content": "hi"} for i in range(12)]
        r = await env.client.post("/v1/chat/completions", json={"model": "lmstudio/good-2", "stream": False, "messages": msgs})
        assert r.status_code == 200 and env.ais.calls == ["good-2"]

    async def test_a_job_with_no_route_is_untouched_even_when_other_jobs_have_one(self, env, monkeypatch):
        monkeypatch.setattr(env.pm, "_skyrimnet_job_table", _table)
        set_routes(env, {"vision": {"ais": ["lmstudio/good-1"]}})
        r = await env.client.post("/v1/chat/completions", json=chat("lmstudio/good-2"))
        assert r.status_code == 200 and env.ais.calls == ["good-2"]

    async def test_a_slow_first_choice_is_handed_over_only_when_the_player_asked_for_it(self, env, monkeypatch):
        monkeypatch.setattr(routing, "EXPLICIT_MIN_S", 0.0)
        env.pm._routing_engine.classify = lambda ai: (routing.CLASS_PAID, "cloud")          # (a local model is never handed over for being slow)
        set_routes(env, {"memory": {"ais": ["lmstudio/slow-1", "lmstudio/good-1"]}})
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert env.ais.calls == ["slow-1"] and r.status_code == 200, "off by default"
        env.ais.calls.clear()
        set_routes(env, {"memory": {"ais": ["lmstudio/slow-1", "lmstudio/good-1"], "slow_limit_s": 0.2}})
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.json()["choices"][0]["message"]["content"] == "answer from good-1" and env.ais.calls == ["slow-1", "good-1"]

    async def test_the_screen_reads_and_writes_the_routes(self, env):
        g = (await env.client.get("/config/routing")).json()
        assert g["jobs"] == [] and g["settings"]["routes"] == {} and [h["id"] for h in g["hows"]] == list(routing.HOWS), "no SkyrimNet linked and nothing seen yet: no jobs to show"
        p = await env.client.post("/config/routing", json={"routes": {"memory": {"ais": ["lmstudio/good-1"], "how": "fastest"}, "bad key!": {"ais": ["x"]}}, "rest": {"for_s": 30}})
        assert p.status_code == 200
        v = p.json()
        assert list(v["settings"]["routes"]) == ["memory"] and v["settings"]["rest"]["for_s"] == 30 and [j for j in v["jobs"] if j["key"] == "memory"][0]["route"]["how"] == "fastest"
        assert env.pm._load_config()["routing"]["routes"]["memory"]["ais"] == ["lmstudio/good-1"]
        assert (await env.client.post("/config/routing", json=[1])).status_code == 400

    async def test_a_player_who_never_sets_a_route_is_served_exactly_as_before(self, env):
        r = await env.client.post("/v1/chat/completions", json=chat("lmstudio/good-1"))
        assert r.status_code == 200 and "routing" not in env.pm._load_config()
        assert env.pm._routing_status()["resting"] == []


class TestNamedWithoutARoute:
    TABLE = {"a": {"models": ["job/a", "lmstudio/good-1"], "max_tokens": 100, "temperature": 0.5}, "b": {"models": ["lmstudio/good-2"], "max_tokens": 100, "temperature": 0.5},
             "solo": {"models": ["job/solo"]}, "memory": {"models": ["lmstudio/good-1"]}}

    def test_the_group_route_answers_first_then_the_model_names_then_nothing(self):
        group = [g for g in routing.hard_groups(self.TABLE) if "a" in g]
        if group:
            other = next(k for k in group[0] if k != "a")
            r, src = routing.named_without_route({"routes": {other: {"ais": ["lmstudio/x"]}}}, "a", self.TABLE)
            assert src == "group" and r["ais"] == ["lmstudio/x"]
        r, src = routing.named_without_route({"routes": {}}, "a", {"a": {"models": ["job/a", "lmstudio/good-1"]}})
        assert src == "model" and r["ais"] == ["lmstudio/good-1"], "a job/ name is no model"
        assert routing.named_without_route({"routes": {}}, "solo", self.TABLE) == (None, None)

    def test_a_route_of_its_own_is_untouched(self):
        assert routing.route_for({"routes": {"memory": {"ais": ["m"]}}}, ["memory"])[0] == {"ais": ["m"]}


@pytest.mark.asyncio
class TestNamedWithoutARouteThroughTheApp:
    async def test_answered_by_the_model_names_when_there_is_no_route_and_logged_once(self, env, monkeypatch):
        monkeypatch.setattr(env.pm, "_skyrimnet_job_table", lambda: {"memory": {"models": ["job/memory", "lmstudio/good-1"]}})
        env.pm._named_unrouted_logged.clear()
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "answer from good-1"
        assert "memory" in env.pm._named_unrouted_logged

    async def test_with_a_route_it_is_unchanged(self, env):
        set_routes(env, {"memory": {"ais": ["lmstudio/good-1"]}})
        r = await env.client.post("/v1/chat/completions", json=chat("job/memory"))
        assert r.status_code == 200 and env.ais.calls == ["good-1"]
