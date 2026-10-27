"""Routing, step 4 (2026-10-02): fine-tune a job. What SkyrimNet last sent for a job is remembered (settings only, never message text); per-job overrides are applied as the
request goes to whichever model answers it, translated for that kind of model, and a setting a model can't take is skipped and said so, never an error in game."""
import json
import os
import shutil
import subprocess

import httpx
import pytest
import pytest_asyncio
from aiohttp import web

import fine_tune
import routing
from tests.fixtures.courier_fakes import free_port

NODE = shutil.which("node")


# ---- the settings ----------------------------------------------------------------------------------------------------------------------------------------

def test_overrides_keep_only_sensible_values_for_known_settings():
    c = fine_tune.clean_overrides({"memory": {"temperature": "0.65", "max_tokens": "400", "thinking": "OFF", "top_p": 7, "bogus": 1, "top_k": "x", "frequency_penalty": -2},
                                   "bad key!": {"temperature": 1}, "meta": {"temperature": 99}, "vision": "x"})
    assert c == {"memory": {"temperature": 0.65, "max_tokens": 400, "thinking": "off", "frequency_penalty": -2.0}}, "out-of-range or unknown things are dropped, a job left with none disappears"
    assert fine_tune.clean_value("max_tokens", 12.6) == 13 and fine_tune.clean_value("temperature", True) is None and fine_tune.clean_value("thinking", "maybe") is None
    s = routing.clean_settings({"overrides": {"memory": {"temperature": 0.5}}})
    assert s["overrides"] == {"memory": {"temperature": 0.5}} and routing.clean_settings({})["overrides"] == {}


def test_jobs_that_cant_be_told_apart_share_the_first_overrides_any_of_them_has():
    s = routing.clean_settings({"overrides": {"combat": {"temperature": 0.4}}})
    assert fine_tune.overrides_for(s, ("dialogue", "combat")) == {"temperature": 0.4} and fine_tune.overrides_for(s, ("memory",)) == {}


# ---- what SkyrimNet sent -----------------------------------------------------------------------------------------------------------------------------------

def test_only_plain_settings_are_remembered_never_message_text():
    got = fine_tune.sent_settings(max_tokens=4096, temperature=1.0, extra={
        "top_p": 0.9, "reasoning": {"enabled": False}, "seed": 42, "frequency_penalty": 0.0, "stream_options": {"include_usage": True}, "messages": [{"content": "SECRET WORDS"}],
        "stop": ["a long stop sentence"], "tools": [{"x": 1}], "user": "player name", "reasoning_effort": "low", "note": "a sentence with spaces that is text", "do_sample": True})
    assert got == {"max_tokens": 4096, "temperature": 1.0, "top_p": 0.9, "thinking": "off", "seed": 42, "frequency_penalty": 0, "reasoning_effort": "low", "do_sample": True}
    assert "SECRET" not in json.dumps(got) and "stop" not in got and "note" not in got and "user" not in got
    assert fine_tune.sent_settings(extra={"thinking": {"type": "enabled"}}) == {"thinking": "on"}
    assert fine_tune.sent_settings(max_tokens=None, temperature=None, extra={}) == {}


def test_the_last_request_per_job_is_kept_saved_only_when_it_changes_and_comes_back_after_a_restart(tmp_path):
    f = str(tmp_path / "last.json")
    ls = fine_tune.LastSent(f)
    ls.note(("memory",), {"temperature": 0.8})
    t1 = os.path.getmtime(f)
    ls.note(("memory",), {"temperature": 0.8})                 # same: nothing written
    assert os.path.getmtime(f) == t1
    ls.note(("memory", "meta"), {"temperature": 0.7, "max_tokens": 100})
    again = fine_tune.LastSent(f)
    assert again.get("memory") == {"temperature": 0.7, "max_tokens": 100} and again.get("meta") == {"temperature": 0.7, "max_tokens": 100} and again.get("combat") == {}
    ls.note(("x",), {})                                        # nothing to remember
    assert "x" not in json.load(open(f))
    assert fine_tune.LastSent(str(tmp_path / "missing.json")).get("a") == {} and fine_tune.LastSent(None).get("a") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert fine_tune.LastSent(str(bad)).get("a") == {}, "a damaged file is just forgotten"


# ---- applying them -----------------------------------------------------------------------------------------------------------------------------------------

def test_apply_puts_in_what_the_model_takes_and_skips_the_rest_and_changes_nothing_without_overrides():
    over = {"temperature": 0.65, "top_k": 40, "max_tokens": 300, "thinking": "on"}
    mt, extra, applied, skipped = fine_tune.apply(over, lambda n: n in ("temperature", "max_tokens"), 4096, {"top_p": 0.9}, lambda v: None)
    assert (mt, extra, applied, skipped) == (300, {"top_p": 0.9, "temperature": 0.65}, ["temperature", "max_tokens"], ["thinking", "top_k"])
    mt, extra, applied, skipped = fine_tune.apply({"thinking": "off"}, lambda n: True, 100, {}, lambda v: {"thinking": {"type": "disabled" if v == "off" else "enabled"}})
    assert extra == {"thinking": {"type": "disabled"}} and applied == ["thinking"]
    assert fine_tune.apply({}, lambda n: True, 4096, {"a": 1}, lambda v: None) == (4096, {"a": 1}, [], [])


@pytest.mark.parametrize("kind,takes,skips", [
    ("claude", {"max_tokens"}, {"temperature", "top_p", "top_k", "thinking"}),
    ("codex", set(), {"max_tokens", "temperature"}),
    ("geminicli", set(), {"max_tokens"}),
    ("gemini", {"max_tokens", "temperature", "top_p"}, {"top_k", "frequency_penalty", "thinking"}),
    ("glm", {"max_tokens", "temperature", "top_p", "thinking"}, {"top_k", "frequency_penalty"}),
    ("deepseek", {"max_tokens", "temperature", "thinking", "presence_penalty"}, {"top_k"}),
    ("ollama", {"temperature", "frequency_penalty", "max_tokens"}, {"top_k", "thinking"}),
    ("local", {"temperature", "top_k", "max_tokens"}, {"thinking"}),
])
def test_each_kind_of_model_takes_only_what_it_is_known_to_take(proxy_module, kind, takes, skips):
    import request_facts
    allv = {"temperature": 0.5, "top_p": 0.5, "top_k": 10, "frequency_penalty": 0.1, "presence_penalty": 0.1, "max_tokens": 50, "thinking": "off"}
    rec = request_facts.RequestRecord(request_id="r")
    rec.overrides = allv
    mt, extra = proxy_module._apply_overrides(rec, kind, 4096, {})
    for n in takes:
        assert n in rec.override_applied, (kind, n)
    for n in skips:
        assert fine_tune.plain_name(n) in rec.override_skipped, (kind, n)
    if "thinking" in takes:
        assert extra["thinking"] == {"type": "disabled"}, "GLM and DeepSeek switch thinking with their own field"
    assert ("max_tokens" in takes) == (mt == 50)
    assert not (skips & set(extra)), "a skipped setting is never sent"


# ---- through the real app -----------------------------------------------------------------------------------------------------------------------------------

class Echo:
    """A fake LM Studio that records the fields each request arrived with."""

    def __init__(self):
        self.port = free_port()
        self.seen: list = []

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
        self.seen.append({k: v for k, v in body.items() if k not in ("messages", "model")})
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@pytest_asyncio.fixture
async def env(proxy_module, monkeypatch):
    ai = await Echo().start()
    monkeypatch.setattr(proxy_module, "lmstudio_base_url", ai.base)
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    e = type("Env", (), {})()
    e.ai, e.pm = ai, proxy_module
    e.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    yield e
    await ai.runner.cleanup()


def configure(env, overrides=None, routes=None):
    cfg = env.pm._load_config()
    cfg["routing"] = {"routes": routes or {"memory": {"ais": ["lmstudio/m"]}}, "overrides": overrides or {}}
    env.pm._save_config(cfg)


def ask(**extra):
    return {"model": "job/memory", "stream": False, "messages": [{"role": "user", "content": "private words"}], "max_tokens": 4000, "temperature": 1.0, "top_p": 0.9,
            "reasoning": {"enabled": False}, **extra}


@pytest.mark.asyncio
async def test_without_overrides_the_model_gets_exactly_what_it_always_got_and_the_numbers_are_remembered(env):
    configure(env)
    r = await env.client.post("/v1/chat/completions", json=ask())
    assert r.status_code == 200
    # The request asks for no thinking (`reasoning: {enabled: false}`), so a local model is also told so in the two ways Ollama and LM Studio understand (thinking_off.py, Beta 28): those two
    # fields are the ONLY addition; the fine-tune numbers are untouched.
    assert env.ai.seen[-1] == {"max_tokens": 4000, "top_p": 0.9, "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}}
    assert "temperature" not in env.ai.seen[-1], "SkyrimNet's own temperature isn't forwarded today (unchanged); only an override sends one"
    assert env.pm._last_sent.get("memory") == {"max_tokens": 4000, "temperature": 1.0, "top_p": 0.9, "thinking": "off"}
    assert "private words" not in json.dumps(env.pm._last_sent._by)


@pytest.mark.asyncio
async def test_an_override_is_applied_before_the_request_goes_and_what_the_model_cant_take_is_skipped_quietly(env, caplog):
    import logging
    caplog.set_level(logging.INFO)
    configure(env, {"memory": {"temperature": 0.65, "max_tokens": 321, "top_k": 40, "thinking": "off"}})
    r = await env.client.post("/v1/chat/completions", json=ask())
    assert r.status_code == 200, r.text
    got = env.ai.seen[-1]
    assert got["temperature"] == 0.65 and got["max_tokens"] == 321 and got["top_k"] == 40 and got["top_p"] == 0.9
    assert "thinking" not in got and "reasoning" not in got, "LM Studio takes no thinking switch: skipped, never sent"
    line = [m for m in caplog.messages if "answered:" in m][-1]
    assert "fine-tuned: temperature 0.65, max_tokens 321, top_k 40" in line and "not used here: Thinking" in line


@pytest.mark.asyncio
async def test_the_view_shows_what_was_sent_the_overrides_and_which_models_cant_take_them(env, monkeypatch):
    monkeypatch.setattr(env.pm, "_skyrimnet_job_table", lambda: {"memory": {"models": ["lmstudio/m", "gemini/g"], "max_tokens": 4000, "temperature": 1.0}, "decisions": {"models": ["x"]}})
    configure(env, {"memory": {"top_k": 40, "temperature": 0.5}}, routes={"memory": {"ais": ["lmstudio/m", "gemini/g"]}})
    await env.client.post("/v1/chat/completions", json=ask())
    g = (await env.client.get("/config/routing")).json()
    j = {x["key"]: x for x in g["jobs"]}
    rows = {r["name"]: r for r in j["memory"]["settingsSent"]}
    assert rows["temperature"]["sent"] == 1.0 and rows["temperature"]["override"] == 0.5 and rows["temperature"]["skippedOn"] == []
    assert rows["top_k"]["override"] == 40 and rows["top_k"]["skippedOn"] == ["g"], "Gemini takes no word-choices setting"
    assert rows["max_tokens"]["sent"] == 4000 and j["memory"]["hasSent"] and j["decisions"]["fineTune"] is False
    assert [f["plain"] for f in g["fineTuneFields"]][:3] == ["Temperature", "Longest reply", "Thinking"]
    p = await env.client.post("/config/routing", json={**g["settings"], "overrides": {"memory": {"temperature": "9", "top_p": "0.3"}}})
    assert p.json()["settings"]["overrides"] == {"memory": {"top_p": 0.3}}, "a number out of range is dropped on save"


@pytest.mark.asyncio
async def test_a_job_without_overrides_is_untouched_even_when_another_job_has_some(env):
    configure(env, {"meta": {"temperature": 0.1}})
    await env.client.post("/v1/chat/completions", json=ask())
    assert "temperature" not in env.ai.seen[-1]


# ---- the page and its script ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_page_has_the_fine_tune_block_with_the_help_line_exactly(env):
    import routing_ui
    page = (await env.client.get("/")).text
    assert "rtFineTune" in page and "Fine-tune this job" in page
    assert '"SkyrimNet sends" shows the exact numbers from the last real request for this job. You can type a new number to override it, or clear it to go back to normal.' in routing_ui.ROUTING_JS


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_typing_a_number_sends_it_and_clear_removes_it_and_a_bad_one_says_so(tmp_path):
    from tests.test_routing_tab import HARNESS
    import routing_ui
    script = HARNESS + routing_ui.ROUTING_JS + r"""
view.fineTuneFields = [{ name: 'temperature', plain: 'Temperature', kind: 'number', min: 0, max: 2 }];
view.settings.overrides = { memory: { temperature: 0.5 } };
rtView = view; rtOpenKey = 'memory';
global.lmPost = async (url, body) => { posts.push(JSON.parse(JSON.stringify(body))); const kept = JSON.parse(JSON.stringify(body)); if (kept.overrides && kept.overrides.memory && kept.overrides.memory.temperature === '9') delete kept.overrides.memory.temperature; return { ...view, settings: kept }; };
(async () => {
  await rtSetOverride('temperature', ' 0.65 ');
  rtView.settings.overrides = { memory: { temperature: 0.65, top_p: 0.3 } };
  await rtSetOverride('temperature', '');
  await rtClearOverrides();
  await rtSetOverride('temperature', '9');
  console.log(JSON.stringify({ posts: posts.map(p => p.overrides), flash: rtFlash && rtFlash.text }));
})();
"""
    f = tmp_path / "h.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["posts"][0] == {"memory": {"temperature": "0.65"}}
    assert o["posts"][1] == {"memory": {"top_p": 0.3}}, "Clear removes just that setting"
    assert "overrides" not in o["posts"][2] or o["posts"][2]["overrides"] == {}, "Clear all"
    assert o["flash"] == "Temperature needs a number from 0 to 2."
