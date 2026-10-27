"""Speeds tab, "Time breakdown" (Routing step 5, 2026-10-02): per job and model, the typical line split into waiting in line, reading the prompt up to the first word
and writing; writing speed and how much of the prompt a local model remembered; tokens and cost with one total. Nothing is made up: a missing number says "not reported",
and older rows without the new facts still count for what they have."""
import asyncio
import json
import shutil
import subprocess
import time

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

import routing
import speeds_time
import speeds_time_ui
from tests.fixtures.courier_fakes import free_port

NODE = shutil.which("node")


def row(**kw):
    base = dict(model="m", provider="Local", job="dialogue", roles_json=None, success=1, elapsed=3.0, first_token=1.0, out_tokens=40, out_chars=None, tokens_in=200, cost=None,
                cost_note=None, timings_json=None, queue_wait=0.5)
    base.update(kw)
    return base


def agg(rows, keep=lambda w: True):
    return speeds_time.aggregate(rows, label_of=lambda k: {"dialogue": "Dialogue", "memory": "Memory"}.get(k, k), where_of=lambda r: "This device" if r["provider"] == "Local" else "Cloud",
                                 keep=keep, order_of=lambda k: ["dialogue", "memory"].index(k) if k in ("dialogue", "memory") else 99)


class TestTheThreeParts:
    def test_wait_read_and_write_are_each_the_typical_value_of_the_rows_that_have_them(self):
        g = agg([row(queue_wait=0.2, first_token=1.0, elapsed=3.0), row(queue_wait=0.4, first_token=2.0, elapsed=5.0), row(queue_wait=0.6, first_token=3.0, elapsed=7.0)])["groups"][0]
        assert (g["wait"], g["read"], g["write"]) == (0.4, 2.0, 3.0) and g["total"] == 5.4, "total = wait + the model's own time, typical of each line"
        assert g["calls"] == 3 and g["waitKnown"]

    def test_a_reply_that_was_not_streamed_has_no_start_to_split_at(self):
        g = agg([row(first_token=None, elapsed=4.0, queue_wait=0.0)])["groups"][0]
        assert g["read"] is None and g["write"] is None and g["whole"] == 4.0 and g["total"] == 4.0
        assert g["wordsPerSecond"] is None, "no speed can be told without a start"

    def test_older_rows_without_the_new_facts_still_show_what_they_have(self):
        g = agg([row(queue_wait=None, job=None, roles_json='["Memory"]', tokens_in=None, out_tokens=None, out_chars=None, first_token=None, elapsed=2.5)])["groups"][0]
        assert g["label"] == "Memory" and not g["waitKnown"] and g["wait"] is None and g["whole"] == 2.5 and g["total"] == 2.5
        assert g["tokensIn"] is None and g["tokensOut"] is None and g["costKind"] == "not reported"
        assert agg([row(job=None, roles_json=None)])["groups"][0]["label"] == "Unknown"

    def test_failed_calls_count_as_lines_but_not_toward_the_times(self):
        g = agg([row(elapsed=3.0), row(success=0, elapsed=0.1, first_token=None, queue_wait=9.0)])["groups"][0]
        assert g["calls"] == 2 and g["ok"] == 1 and g["wait"] == 0.5 and g["total"] == 3.5


class TestSpeedAndMemory:
    def test_writing_speed_from_the_models_own_token_count_and_an_estimate_only_when_it_gave_none(self):
        g = agg([row(first_token=1.0, elapsed=3.0, out_tokens=60)])["groups"][0]
        assert g["tokensPerSecond"] == 30.0 and g["wordsPerSecond"] == 22.5 and not g["speedEstimated"]
        e = agg([row(first_token=1.0, elapsed=3.0, out_tokens=None, out_chars=240)])["groups"][0]
        assert e["tokensPerSecond"] == 30.0 and e["speedEstimated"], "240 characters is about 60 tokens, marked as an estimate"
        assert agg([row(first_token=1.0, elapsed=1.1, out_tokens=50)])["groups"][0]["tokensPerSecond"] is None, "one burst: no honest speed"

    def test_a_local_models_prompt_remembered_is_cache_over_cache_plus_new(self):
        t = json.dumps({"prompt_n": 20, "cache_n": 80})
        g = agg([row(timings_json=t), row(timings_json=json.dumps({"prompt_n": 50, "cache_n": 50}))])["groups"][0]
        assert g["remembered"] == 65
        assert agg([row()])["groups"][0]["remembered"] is None, "a model that doesn't report it shows nothing"
        assert agg([row(timings_json="not json")])["groups"][0]["remembered"] is None


class TestCostAndTokens:
    def test_free_subscription_a_real_number_and_not_reported(self):
        g = {x["model"]: x for x in agg([
            row(model="local-a", cost=0.0, cost_note="free"), row(model="claude", provider="Claude", cost=None, cost_note="included in your subscription"),
            row(model="glm", provider="GLM", cost=0.002, cost_note="reported by the model"), row(model="glm", provider="GLM", cost=0.004, cost_note="reported by the model"),
            row(model="openai", provider="OpenAI", cost=None, cost_note="not reported")])["groups"]}
        assert g["local-a"]["costKind"] == "free" and g["local-a"]["costTypical"] is None
        assert g["claude"]["costKind"] == "subscription"
        assert g["glm"]["costTypical"] == 0.003 and g["glm"]["costTotal"] == 0.006
        assert g["openai"]["costKind"] == "not reported" and g["openai"]["costTotal"] is None

    def test_one_total_across_the_pay_as_you_go_models_that_report_and_it_names_the_ones_not_counted(self):
        out = agg([row(model="glm", provider="GLM", job="dialogue", cost=0.002, cost_note="reported by the model"), row(model="glm", provider="GLM", job="memory", cost=0.5, cost_note="worked out from the model's own prices"),
                   row(model="openai", provider="OpenAI", cost=None, cost_note="not reported"), row(model="local-a", cost=0.0, cost_note="free")])
        assert out["total"]["cost"] == 0.502 and out["total"]["countedModels"] == ["glm"] and out["total"]["notCounted"] == ["openai"]
        none = agg([row(model="local-a", cost=0.0, cost_note="free")])["total"]
        assert none == {"cost": None, "countedModels": [], "notCounted": []}

    def test_tokens_are_summed_where_reported_and_none_where_not(self):
        g = agg([row(tokens_in=100, out_tokens=10), row(tokens_in=50, out_tokens=5)])["groups"][0]
        assert (g["tokensIn"], g["tokensOut"]) == (150, 15)
        n = agg([row(tokens_in=None, out_tokens=None, out_chars=None)])["groups"][0]
        assert n["tokensIn"] is None and n["tokensOut"] is None


class TestGroupingAndFilters:
    def test_grouped_by_job_in_the_jobs_order_then_busiest_model_first(self):
        out = agg([row(job="memory", model="b"), row(job="dialogue", model="x"), row(job="dialogue", model="y"), row(job="dialogue", model="y"), row(job="zzz_mod_job", model="q")])["groups"]
        assert [(g["label"], g["model"]) for g in out] == [("Dialogue", "y"), ("Dialogue", "x"), ("Memory", "b"), ("zzz_mod_job", "q")]

    def test_the_pc_filter_keeps_only_what_ran_there(self):
        rows = [row(model="a", provider="Local"), row(model="b", provider="GLM")]
        assert [g["model"] for g in agg(rows, keep=lambda w: w == "This device")["groups"]] == ["a"]
        assert [g["model"] for g in agg(rows, keep=lambda w: w == "Cloud")["groups"]] == ["b"]


# ---- through the app --------------------------------------------------------------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(proxy_module):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    c.pm = proxy_module
    return c


def put(pm, **kw):
    r = row(**kw)
    extra = {k: r[k] for k in ("job", "tokens_in", "cost", "cost_note", "timings_json", "queue_wait") if r.get(k) is not None}
    pm._insert_speed_history_row(r["model"], r["elapsed"], bool(r["success"]), r["roles_json"], r["first_token"], r["out_tokens"], r["out_chars"], None, r["provider"], None, extra)


@pytest.mark.asyncio
async def test_the_endpoint_reads_the_history_filters_by_range_and_where_and_validates(client):
    pm = client.pm
    put(pm, model="gemma", provider="Local", job="dialogue", queue_wait=0.3, cost=0.0, cost_note="free", timings_json=json.dumps({"prompt_n": 10, "cache_n": 90}))
    put(pm, model="glm-4.6", provider="GLM", job="memory", cost=0.01, cost_note="reported by the model", queue_wait=0.0)
    r = (await client.get("/stats/speeds/time?range=all")).json()
    by = {g["model"]: g for g in r["groups"]}
    assert by["gemma"]["label"] == "Dialogue" and by["gemma"]["remembered"] == 90 and by["gemma"]["costKind"] == "free" and by["gemma"]["where"] == "This device"
    assert by["glm-4.6"]["costTotal"] == 0.01 and r["total"]["cost"] == 0.01
    only = (await client.get("/stats/speeds/time?range=all&pc=this")).json()
    assert [g["model"] for g in only["groups"]] == ["gemma"]
    assert (await client.get("/stats/speeds/time?range=never")).status_code == 400
    pm.ENABLE_SPEED_TRACKING = False
    try:
        assert (await client.get("/stats/speeds/time")).status_code == 404
    finally:
        pm.ENABLE_SPEED_TRACKING = True


class OneAtATime:
    def __init__(self):
        self.port = free_port()

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

    async def chat(self, request):
        await asyncio.sleep(0.4)
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                                  "timings": {"prompt_n": 10, "cache_n": 30, "prompt_ms": 5.0, "predicted_n": 3, "predicted_ms": 50.0}})


@pytest.mark.asyncio
async def test_a_request_that_waited_in_line_has_its_wait_saved_and_shown(proxy_module, monkeypatch):
    ai = await OneAtATime().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", ai.base)
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "lmstudio": 1})
    monkeypatch.setattr(proxy_module, "QUEUE_TIMEOUT_SECONDS", 20.0)
    proxy_module._request_lines.clear()
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    body = {"model": "lmstudio/m", "messages": [{"role": "user", "content": "hi"}]}
    a = asyncio.create_task(c.post("/v1/chat/completions", json=body))
    await asyncio.sleep(0.15)
    b = asyncio.create_task(c.post("/v1/chat/completions", json=body))
    await asyncio.gather(a, b)
    r = (await c.get("/stats/speeds/time?range=all")).json()
    g = r["groups"][0]
    assert g["calls"] == 2 and g["model"] == "m" and g["waitKnown"]
    rows = proxy_module._query_time_rows(None)
    waits = sorted(x["queue_wait"] for x in rows)
    assert waits[0] < 0.1 and waits[1] > 0.2, "the second request stood in line while the first was being answered"
    assert g["remembered"] == 75 and g["costKind"] == "free" and g["tokensIn"] == 14 and g["tokensOut"] == 6
    await ai.runner.cleanup()
    proxy_module._request_lines.clear()


# ---- the page --------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_speeds_tab_has_the_new_view_with_the_legend_exactly(client):
    page = (await client.get("/")).text
    assert "sttInit" in page and "optSpeedsTime" in page and "speedsTimeView" in page and "which === 'time'" in page
    for w in ("waiting in line", "reading the prompt, up to the first word", "writing", "Local models also show how much of the prompt they remembered from last time.", "Time breakdown"):
        assert w in speeds_time_ui.SPEEDS_TIME_JS, w


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_script_is_valid_and_draws_the_bar_the_speed_and_the_cost(tmp_path):
    js = r"""
global.speedUnit = 'words'; global.document = { getElementById: () => null }; global._escapeHtml = s => String(s); global.$ = () => null; global.setTimeout = () => 0;
""" + speeds_time_ui.SPEEDS_TIME_JS + r"""
const g = { waitKnown: true, wait: 1, read: 1, write: 2, whole: null, wordsPerSecond: 22.5, tokensPerSecond: 30, speedEstimated: true, remembered: 65, costTypical: 0.0031, costTotal: 0.03, costKind: 'not reported', tokensIn: 1200, tokensOut: 90 };
const out = { bar: sttBar(g), speed: sttSpeed(g), cost: sttCost(g), tokens: sttTokens(g), none: sttBar({ waitKnown: false, read: null, whole: null }), free: sttCost({ costTypical: null, costKind: 'free' }),
              whole: sttBar({ waitKnown: false, read: null, whole: 4 }) };
console.log(JSON.stringify(out));
"""
    f = tmp_path / "t.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["bar"].count("<i ") == 3 and "width:25.0%" in o["bar"] and "width:50.0%" in o["bar"]
    assert "≈ 23 words/s" in o["speed"] and "remembered 65% of the prompt" in o["speed"]
    assert "$0.0031 a line" in o["cost"] and "$0.030 in this range" in o["cost"] and "in 1,200" in o["tokens"] or "in 1200" in o["tokens"]
    assert "not reported" in o["none"] and o["free"] == "free"
    assert "whole reply (not streamed)" in o["whole"] and "stt-whole" in o["whole"]
