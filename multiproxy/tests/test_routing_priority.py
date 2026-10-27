"""Routing, step 3 (2026-10-02): what goes first when an AI is busy.

The line (`_RequestLine`) serves waiting requests by their job's place in the player's order, first come first served among equals; a waiting request moves up one
place per `aging_s` seconds so nothing waits forever; a running request is never touched. Proven on the line itself, and through the real app with a fake AI that
can only do one thing at a time."""
import asyncio
import json
import os
import shutil
import subprocess
import time

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

import routing
from tests.fixtures.courier_fakes import free_port

NODE = shutil.which("node")


# ---- the settings and the order ---------------------------------------------------------------------------------------------------------------------------

def test_the_default_order_puts_what_you_notice_first_and_the_aging_is_five_seconds():
    s = routing.clean_settings({})
    assert s["priority"] == [] and s["aging_s"] == 5.0
    o = routing.priority_order(s)
    assert o[:4] == ["dialogue", "combat", "decisions", "vision"] and set(o) == set(routing.JOB_ORDER) and len(o) == len(set(o))
    assert routing.priority_rank(s, "dialogue") == 0 and routing.priority_rank(s, "vision") == 3


def test_a_saved_order_is_used_cleaned_and_a_job_not_in_it_ranks_last_for_every_such_job():
    s = routing.clean_settings({"priority": ["memory", "dialogue", "memory", "bad key!", 7], "aging_s": 500})
    assert s["priority"] == ["memory", "dialogue"] and s["aging_s"] == 120.0
    assert routing.priority_rank(s, "memory") == 0 and routing.priority_rank(s, "dialogue") == 1
    assert routing.priority_rank(s, "combat") == routing.priority_rank(s, "meta") == routing.priority_rank(s, None) == 2, "unlisted jobs are equal: first come first served"
    assert routing.priority_rank(s, "dialogue/combat") == 1, "jobs that can't be told apart rank as the best of them"
    assert routing.clean_settings({"aging_s": 0})["aging_s"] == 1.0
    assert routing.priority_order(s, shown=["brand_new_job", "memory"])[-1] == "brand_new_job", "a job a mod added goes at the end"


# ---- the line --------------------------------------------------------------------------------------------------------------------------------------------

async def _drain(line, jobs, order_log):
    """Start one task per (name, rank, label) waiting on a full line (in this order), return the tasks."""
    tasks = []
    for name, rank, label in jobs:
        async def go(name=name, rank=rank, label=label):
            behind = await line.acquire(10, rank, label, 5.0)
            order_log.append((name, behind))
        tasks.append(asyncio.create_task(go()))
        await asyncio.sleep(0.01)
    return tasks


@pytest.mark.asyncio
async def test_a_higher_job_goes_ahead_equal_jobs_stay_first_come_first_served_and_who_was_passed_over_is_told(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)                                      # one request is already running
    log: list = []
    tasks = await _drain(line, [("diary-1", 12, "Diary Generation"), ("memory-1", 13, "Memory"), ("dialogue-1", 0, "Dialogue"), ("diary-2", 12, "Diary Generation"), ("dialogue-2", 0, "Dialogue")], log)
    for _ in range(5):
        line.release()
        await asyncio.sleep(0.02)
    await asyncio.gather(*tasks)
    assert [n for n, _ in log] == ["dialogue-1", "dialogue-2", "diary-1", "diary-2", "memory-1"], "dialogue first, then diary (first come first served), then memory"
    assert dict(log) == {"dialogue-1": [], "dialogue-2": [], "diary-1": ["Dialogue"], "diary-2": ["Dialogue"], "memory-1": ["Dialogue", "Diary Generation"]},         "each one passed over is told which jobs went ahead of it (never a job that was simply in front of it)"


@pytest.mark.asyncio
async def test_with_no_job_set_the_line_is_exactly_first_come_first_served_as_before(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)
    log: list = []
    tasks = await _drain(line, [(f"r{i}", 1_000_000, "") for i in range(5)], log)
    for _ in range(5):
        line.release()
        await asyncio.sleep(0.02)
    await asyncio.gather(*tasks)
    assert [n for n, _ in log] == ["r0", "r1", "r2", "r3", "r4"] and all(b == [] for _, b in log)


@pytest.mark.asyncio
async def test_nothing_waits_forever_a_waiting_request_moves_up_one_place_per_aging_step(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)
    got: list = []

    async def go(name, rank, aging):
        await line.acquire(10, rank, name, aging)
        got.append(name)
    old = asyncio.create_task(go("old-background", 5, 0.05))              # rank 5, moves up a place every 0.05 s
    await asyncio.sleep(0.4)                                               # 0.4 s = 8 steps: its effective place is 5 - 8 = -3, ahead of any new dialogue (0)
    fresh = asyncio.create_task(go("fresh-dialogue", 0, 5.0))
    await asyncio.sleep(0.02)
    line.release()
    await asyncio.sleep(0.02)
    assert got == ["old-background"], "it waited long enough to outrank a dialogue that arrived after it"
    line.release()
    await asyncio.gather(old, fresh)
    assert got == ["old-background", "fresh-dialogue"]


@pytest.mark.asyncio
async def test_the_bound_a_request_that_waited_aging_times_rank_plus_one_outranks_every_newcomer(proxy_module):
    rank, aging = 3, 0.04
    line = proxy_module._RequestLine(1)
    await line.acquire(1)
    got: list = []

    async def go(name, r):
        await line.acquire(10, r, name, aging)
        got.append(name)
    t = asyncio.create_task(go("bg", rank))
    await asyncio.sleep(aging * (rank + 1) + 0.05)
    newcomers = [asyncio.create_task(go(f"d{i}", 0)) for i in range(3)]
    await asyncio.sleep(0.02)
    line.release()
    await asyncio.sleep(0.02)
    assert got[0] == "bg"
    for _ in range(3):
        line.release()
        await asyncio.sleep(0.01)
    await asyncio.gather(t, *newcomers)


@pytest.mark.asyncio
async def test_a_running_request_is_never_interrupted_and_timeouts_and_cancels_still_work(proxy_module):
    line = proxy_module._RequestLine(1)
    await line.acquire(1)                                      # running: nothing below can touch it
    with pytest.raises(proxy_module._RequestLineTimeout):
        await line.acquire(0.05, 0, "Dialogue", 5.0)
    assert line.active == 1 and line.queued == 0
    t = asyncio.create_task(line.acquire(5, 0, "Dialogue", 5.0))
    await asyncio.sleep(0.02)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert line.queued == 0 and line.active == 1
    line.release()
    assert line.active == 0


# ---- through the real app, with a fake AI that does one thing at a time ------------------------------------------------------------------------------------

class OneAtATime:
    def __init__(self):
        self.port = free_port()
        self.started: list = []
        self.runner = None
        self.busy = 0
        self.max_busy = 0

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
        body = await request.json()
        self.started.append(body["messages"][-1]["content"])
        self.busy += 1
        self.max_busy = max(self.max_busy, self.busy)
        await asyncio.sleep(0.35)
        self.busy -= 1
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@pytest_asyncio.fixture
async def env(proxy_module, monkeypatch):
    ai = await OneAtATime().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", ai.base)
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "lmstudio": 1})
    monkeypatch.setattr(proxy_module, "QUEUE_TIMEOUT_SECONDS", 20.0)
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    proxy_module._request_lines.clear()
    cfg = proxy_module._load_config()
    cfg["routing"] = {"routes": {"memory": {"ais": ["lmstudio/m"]}, "dialogue": {"ais": ["lmstudio/m"]}, "DiaryGeneration": {"ais": ["lmstudio/m"]}}}
    proxy_module._save_config(cfg)
    e = type("Env", (), {})()
    e.ai, e.pm = ai, proxy_module
    e.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    yield e
    await ai.runner.cleanup()
    proxy_module._request_lines.clear()


def chat(job, text):
    return {"model": f"job/{job}", "stream": False, "messages": [{"role": "user", "content": text}]}


@pytest.mark.asyncio
async def test_with_an_ai_that_does_one_at_a_time_dialogue_goes_ahead_of_a_waiting_diary_and_the_record_says_so(env, caplog):
    import logging
    caplog.set_level(logging.INFO)
    first = asyncio.create_task(env.client.post("/v1/chat/completions", json=chat("memory", "blocker")))
    await asyncio.sleep(0.12)                                      # it is running now; the AI is full
    diary = asyncio.create_task(env.client.post("/v1/chat/completions", json=chat("DiaryGeneration", "diary entry")))
    await asyncio.sleep(0.08)
    dialogue = asyncio.create_task(env.client.post("/v1/chat/completions", json=chat("dialogue", "spoken line")))
    r = await asyncio.gather(first, diary, dialogue)
    assert [x.status_code for x in r] == [200, 200, 200]
    assert env.ai.started == ["blocker", "spoken line", "diary entry"], "the dialogue that came later started before the waiting diary"
    assert env.ai.max_busy == 1, "the AI was never asked for two at once"
    behind = [m for m in caplog.messages if "waited" in m and "behind Dialogue" in m]
    assert behind, "the diary's record says it waited behind Dialogue: " + "\n".join(m for m in caplog.messages if "answered:" in m)


@pytest.mark.asyncio
async def test_the_player_can_put_the_diary_first_and_the_line_follows(env):
    cfg = env.pm._load_config()
    cfg["routing"]["priority"] = ["DiaryGeneration", "dialogue"]
    env.pm._save_config(cfg)
    first = asyncio.create_task(env.client.post("/v1/chat/completions", json=chat("memory", "blocker")))
    await asyncio.sleep(0.12)
    dialogue = asyncio.create_task(env.client.post("/v1/chat/completions", json=chat("dialogue", "spoken line")))
    await asyncio.sleep(0.05)
    diary = asyncio.create_task(env.client.post("/v1/chat/completions", json=chat("DiaryGeneration", "diary entry")))
    await asyncio.gather(first, diary, dialogue)
    assert env.ai.started == ["blocker", "diary entry", "spoken line"]


@pytest.mark.asyncio
async def test_the_endpoint_and_the_tab_carry_the_order_and_save_it(env):
    g = (await env.client.get("/config/routing")).json()
    assert g["settings"]["aging_s"] == 5.0 and g["priority"][:4] == ["dialogue", "combat", "decisions", "vision"]
    p = await env.client.post("/config/routing", json={**g["settings"], "priority": ["meta", "dialogue"], "aging_s": 10})
    v = p.json()
    assert v["settings"]["priority"] == ["meta", "dialogue"] and v["settings"]["aging_s"] == 10.0 and v["priority"][:2] == ["meta", "dialogue"]
    page = (await env.client.get("/")).text
    assert "When a model is busy, the game" in page and "Priority when busy" in page and "What goes first" not in page and "rtPriorityCard" in page


# ---- the card's script -----------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_card_reorders_by_arrow_and_by_drop_and_keeps_jobs_that_are_not_drawn(tmp_path):
    from tests.test_routing_tab import HARNESS
    import routing_ui
    script = HARNESS.replace("hows: [{ id: 'order'", "priority: ['dialogue', 'hidden_job', 'memory', 'meta', 'narrative_engine_composer'], hows: [{ id: 'order'") + routing_ui.ROUTING_JS + r"""
rtView = view;
view.settings.aging_s = 5; view.settings.priority = [];
(async () => {
  console.log(JSON.stringify(rtPrioItems()));
  await rtPrioMove(1, -1);                                          // memory earlier than dialogue? (index 1 is memory) -> memory first
  await rtPrioMove(0, -1);                                          // the first can't go earlier: nothing sent
  rtPDrag = 2; await rtPDrop({ preventDefault() {} }, 0);           // the 3rd dropped on the 1st goes to the front
  await rtSetAging('10');
  console.log(JSON.stringify(posts.map(p => ({ priority: p.priority, aging: p.aging_s }))));
})();
"""
    f = tmp_path / "h.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert json.loads(lines[0]) == ["memory", "meta", "narrative_engine_composer"], "only the jobs the tab draws are reordered"
    posts = json.loads(lines[-1])
    assert len(posts) == 3, "the move that can't happen sent nothing"
    assert posts[0]["priority"][-1] == "hidden_job", "a job that isn't drawn keeps its place, after the ones that are"
    assert posts[2]["aging"] == 10
