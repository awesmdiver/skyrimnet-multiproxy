"""Routing, step 6 (2026-10-02): the Live view. Requests as they happen, and the reasoning of models that share it. Nothing written to disk, nothing read out of a reply
while the view is closed, a slow or vanished page never slows a request, and no key or address ever appears."""
import asyncio
import json
import shutil
import subprocess
import time

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

import live_feed
import request_facts
from tests.fixtures.courier_fakes import free_port

NODE = shutil.which("node")


def rec(**kw):
    r = request_facts.RequestRecord(request_id="abc123")
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# ---- what a line says ------------------------------------------------------------------------------------------------------------------------------------

def test_a_line_has_job_ai_where_times_tokens_and_cost():
    ev = live_feed.request_event(rec(job="Dialogue", job_key="dialogue", model="gemma-4-12b", provider="This device", first_word_s=0.4, total_s=1.8, tokens_in=1240, tokens_out=96,
                                     cost_usd=0.0031, cost_note="reported by the AI"))
    assert (ev["job"], ev["ai"], ev["guess"], ev["failed"]) == ("Dialogue", "gemma-4-12b (This device)", False, False)
    assert ev["detail"] == "first word 0.4 s · 1.8 s · 1,240 in, 96 out · $0.0031"
    local = live_feed.request_event(rec(job="Memory", job_key="memory", model="m", provider="m", cost_usd=0.0, cost_note="free", queue_wait_s=1.2, queue_behind=["Dialogue"], total_s=4.0))
    assert local["ai"] == "m" and local["detail"] == "waited 1.2 s behind Dialogue · 4.0 s · free"
    assert "cost" not in live_feed.request_event(rec(job="Memory", model="m", cost_note="not reported", total_s=1.0))["detail"], "a cost nobody reported is left out, not guessed"
    dec = live_feed.request_event(rec(job="Decisions", model="Local decision model", questions=("a", "b", "c"), total_s=0.14))
    assert dec["detail"].startswith("3 questions")


def test_a_guessed_job_reads_looks_like_and_a_handover_names_who_stepped_aside():
    ev = live_feed.request_event(rec(job="Memory", job_key="memory", job_how="recognised", job_certain=False, model="claude-sonnet-5-5", provider="Claude", total_s=1.3,
                                     skipped=[("gemma-4-12b", "too slow")]))
    assert ev["job"] == "Looks like Memory" and ev["handover"] == "gemma-4-12b was too slow → " and ev["answered"] is True
    assert live_feed.request_event(rec(job="Memory", job_key="memory", job_how="recognised", job_certain=True, model="m"))["job"] == "Memory"
    assert live_feed.request_event(rec(job="Memory", job_how="hint", job_certain=False, job_key="memory", model="m"))["job"] == "Memory", "only our own guess is a guess"
    failed = live_feed.request_event(rec(job="Memory", model="m", outcome="timeout", skipped=[("a", "busy"), ("b", "failed (500)")]))
    assert failed["failed"] is True and "a was busy, b failed (500) → " in failed["handover"] and "failed (timeout)" in failed["detail"] and failed["answered"] is False


def test_keys_and_addresses_never_appear():
    dirty = "http://192.168.1.5:1234/v1 sk-abcdefghijklmnopqrstuvwxyz123456 and 10.0.0.2:8000 at www.example.com via nuc.local:9 token AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 ok"
    out = live_feed.scrub(dirty)
    for bad in ("192.168", "sk-abc", "10.0.0.2", "example.com", "nuc.local", "AbCdEfGh"):
        assert bad not in out, (bad, out)
    assert out.endswith("ok") and live_feed.scrub("Dialogue → gemma-4-12b (This device) · 1.8 s") == "Dialogue → gemma-4-12b (This device) · 1.8 s"
    ev = live_feed.request_event(rec(job="Memory", model="http://10.1.1.1:1234/m", provider="nuc.local:8000", skipped=[("sk-abcdefghijklmnopqrstuvwxyz123456", "busy")], total_s=1))
    assert "10.1.1.1" not in json.dumps(ev) and "nuc.local" not in json.dumps(ev) and "sk-abc" not in json.dumps(ev)


def test_reasoning_is_read_from_both_spellings_and_nothing_else():
    f = live_feed.reasoning_from_event
    assert f('data: {"choices":[{"delta":{"reasoning_content":"Let me think"}}]}') == "Let me think"
    assert f('data: {"choices":[{"delta":{"reasoning":"hmm"}}]}') == "hmm"
    assert f('data: {"choices":[{"delta":{"content":"hello"}}]}') == "" and f("data: [DONE]") == "" and f(": comment") == "" and f("data: {broken \"reasoning") == ""


# ---- closed costs nothing; a slow page can never slow a request -------------------------------------------------------------------------------------------

def test_closed_view_keeps_only_a_short_tail_and_reads_no_thinking():
    feed = live_feed.Feed()
    for i in range(500):
        feed.request(rec(job="Memory", model="m", total_s=1.0))
    assert len(feed.snapshot()) == live_feed.TAIL_LINES and feed.watchers == 0
    assert feed.watching_thinking() is False
    feed.thinking("r", "Memory", "secret reasoning")           # nobody watching: dropped on the spot, kept nowhere
    assert "secret" not in json.dumps(feed.snapshot()) and not hasattr(feed, "_think_tail")


@pytest.mark.asyncio
async def test_a_page_that_never_reads_cannot_slow_or_break_anything():
    feed = live_feed.Feed()
    slow = feed.stream(thinking=True)
    await slow.__anext__()                                     # joined (the retry hint), then never read again
    assert feed.watchers == 1 and feed.watching_thinking()
    t0 = time.perf_counter()
    for i in range(20000):
        feed.request(rec(job="Memory", model="m", total_s=1.0))
        feed.thinking("r", "Memory", "x" * 50)
    took = time.perf_counter() - t0
    assert took < 5.0, f"publishing must never wait for a reader ({took:.2f}s)"
    w = next(iter(feed._watchers))
    assert w.queue.qsize() <= live_feed.QUEUE_SIZE and feed.dropped > 0, "the slow page loses its oldest lines, nobody else is affected"
    await slow.aclose()
    assert feed.watchers == 0, "a page that goes away is forgotten"


@pytest.mark.asyncio
async def test_a_page_gets_the_tail_then_new_lines_and_thinking_only_if_it_asked():
    feed = live_feed.Feed()
    feed.request(rec(job="Memory", job_key="memory", model="old", total_s=1.0))
    both, only_req = feed.stream(thinking=True), feed.stream(thinking=False)
    got_both, got_req = [], []
    for s, out in ((both, got_both), (only_req, got_req)):
        assert (await s.__anext__()).startswith("retry:")
        out.append(json.loads((await s.__anext__())[6:]))
    feed.request(rec(job="Dialogue", job_key="dialogue", model="new", total_s=2.0))
    feed.thinking("r1", "Dialogue", "pondering")
    for s, out in ((both, got_both), (only_req, got_req)):
        out.append(json.loads((await s.__anext__())[6:]))
    assert [e["ai"] for e in got_both if e["k"] == "req"] == ["old", "new"] and [e["ai"] for e in got_req if e["k"] == "req"] == ["old", "new"]
    assert json.loads((await both.__anext__())[6:]) == {"k": "think", "id": "r1", "job": "Dialogue", "text": "pondering"}
    nxt = await only_req.__anext__()                           # nothing more for it: the next thing is only the heartbeat
    await both.aclose(), await only_req.aclose()


@pytest.mark.asyncio
async def test_heartbeat_keeps_a_quiet_page_connected():
    feed = live_feed.Feed()
    s = feed.stream(heartbeat_s=0.05)
    await s.__anext__()
    assert (await s.__anext__()).startswith(": still here")
    await s.aclose()


# ---- through the real app ----------------------------------------------------------------------------------------------------------------------------------

class Streamer:
    """A fake LM Studio that answers a streamed chat with reasoning first, then the words."""

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
        body = await request.json()
        if not body.get("stream"):
            return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for delta in ({"reasoning_content": "First I think. "}, {"reasoning_content": "Then I decide."}, {"content": "Hello"}, {"content": " there"}):
            await resp.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta}]})}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp


@pytest_asyncio.fixture
async def env(proxy_module, monkeypatch):
    ai = await Streamer().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", ai.base)
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    monkeypatch.setattr(proxy_module, "_live", live_feed.Feed())
    cfg = proxy_module._load_config()
    cfg["routing"] = {"routes": {"memory": {"ais": ["lmstudio/m"]}}}
    proxy_module._save_config(cfg)
    e = type("Env", (), {})()
    e.ai, e.pm = ai, proxy_module
    e.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    yield e
    await ai.runner.cleanup()


def ask(stream):
    return {"model": "job/memory", "stream": stream, "messages": [{"role": "user", "content": "private words"}], "max_tokens": 200}


@pytest.mark.asyncio
async def test_a_request_becomes_a_line_with_its_job_and_no_prompt_text(env):
    r = await env.client.post("/v1/chat/completions", json=ask(False))
    assert r.status_code == 200
    lines = env.pm._live.snapshot()
    assert lines and lines[-1]["job"] == "Memory" and lines[-1]["ai"].startswith("m") and "private words" not in json.dumps(lines)


@pytest.mark.asyncio
async def test_thinking_reaches_a_watcher_labelled_by_job_and_the_words_still_reach_skyrimnet_intact(env):
    s = env.pm._live.stream(thinking=True)
    await s.__anext__()
    r = await env.client.post("/v1/chat/completions", json=ask(True))
    assert r.status_code == 200 and "Hello" in r.text and " there" in r.text and "[DONE]" in r.text
    think = []
    while env.pm._live.watchers and len(think) < 2:
        raw = await asyncio.wait_for(s.__anext__(), 3)
        if raw.startswith("data:") and json.loads(raw[6:])["k"] == "think":
            think.append(json.loads(raw[6:]))
    assert "".join(t["text"] for t in think) == "First I think. Then I decide." and {t["job"] for t in think} == {"Memory"}
    await s.aclose()


@pytest.mark.asyncio
async def test_with_the_view_closed_a_reply_is_not_even_parsed_for_thinking(env, monkeypatch):
    called = []
    monkeypatch.setattr(live_feed, "reasoning_from_event", lambda e: called.append(e) or "")
    r = await env.client.post("/v1/chat/completions", json=ask(True))
    assert r.status_code == 200 and "Hello" in r.text and called == []


@pytest.mark.asyncio
async def test_an_open_page_that_never_reads_does_not_slow_requests(env):
    async def run(n):
        t0 = time.perf_counter()
        for _ in range(n):
            assert (await env.client.post("/v1/chat/completions", json=ask(True))).status_code == 200
        return time.perf_counter() - t0
    await run(3)
    closed = await run(25)
    stuck = env.pm._live.stream(thinking=True)
    await stuck.__anext__()
    opened = await run(25)
    await stuck.aclose()
    assert opened < closed * 2 + 1.0, f"closed {closed:.2f}s, open-and-stuck {opened:.2f}s"


@pytest.mark.asyncio
async def test_the_route_serves_server_sent_events_and_the_page_has_the_live_card(env):
    from tests.route_paths import route_paths
    assert "/live/stream" in route_paths(env.pm.app)
    page = (await env.client.get("/")).text
    assert "rtLiveRender" in page and 'id="rtLive"' in page


# ---- the page's script -----------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_live_script_filters_pauses_and_stays_closed_until_opened(tmp_path):
    from tests.test_routing_tab import HARNESS
    import routing_ui
    script = HARNESS + routing_ui.ROUTING_JS + r"""
els['rtLive'] = { innerHTML: '' };
els['rtLiveBox'] = null;
global.EventSource = function (url) { opened.push(url); this.close = () => { opened.push('closed'); }; };
global.opened = [];
els['paneRouting'] = { style: { display: '' } };
(async () => {
  rtLiveRender();
  const closedHtml = els['rtLive'].innerHTML;
  rtLiveSync();
  const noStreamWhileClosed = opened.length === 0;
  rtLiveToggle();                                   // open
  const openUrl = opened[0];
  const req = { k: 'req', id: 'a', t: '10:00:01', job: 'Looks like Memory', jobKey: 'memory', handover: 'gemma was too slow → ', ai: 'claude', answered: true, failed: false, detail: '1.3 s · 1,240 in, 96 out · $0.0031' };
  rtLiveTake(req); rtLiveTake({ k: 'think', id: 'b', job: 'Dialogue', text: 'one ' }); rtLiveTake({ k: 'think', id: 'b', job: 'Dialogue', text: 'two' });
  const both = rtLiveHtmlRows();
  rtLiveMode = 'req'; const onlyReq = rtLiveHtmlRows();
  rtLiveMode = 'think'; const onlyThink = rtLiveHtmlRows();
  rtLiveMode = 'both';
  rtLivePause(); rtLiveTake({ k: 'req', id: 'c', t: '10:00:02', job: 'Dialogue', jobKey: 'dialogue', ai: 'm', detail: '' });
  const heldWhilePaused = rtLiveItems.length, pending = rtLivePending.length;
  rtLivePause();
  const afterResume = rtLiveItems.length;
  rtLiveToggle();                                   // close
  console.log(JSON.stringify({ closedHtml, noStreamWhileClosed, openUrl, both, onlyReq, onlyThink, heldWhilePaused, pending, afterResume, closedEvents: opened.slice(1), kept: rtLiveItems.length }));
})();
"""
    f = tmp_path / "h.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert "Nothing is shown or saved until you open this." in o["closedHtml"] and ">Open<" in o["closedHtml"] and o["noStreamWhileClosed"], "nothing streams until it is opened"
    assert o["openUrl"].startswith("/live/stream")
    assert "Looks like Memory" in o["both"] and "gemma was too slow → claude answered" in o["both"] and "$0.0031" in o["both"] and "one two" in o["both"]
    assert "one two" not in o["onlyReq"] and "Looks like Memory" in o["onlyReq"] and "Looks like Memory" not in o["onlyThink"] and "one two" in o["onlyThink"]
    assert (o["heldWhilePaused"], o["pending"], o["afterResume"]) == (2, 1, 3), "Pause holds new lines back, Resume shows them"
    assert o["closedEvents"] == ["closed"] and o["kept"] == 0, "closing stops the stream and forgets what was shown"
