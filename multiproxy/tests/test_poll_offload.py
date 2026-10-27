"""The perf check's fixes for the dashboard's polls and the request path (2026-10-02): config.json is read from disk only when it changed, a
program start (Codex's sign-in check, the graphics card read) never runs on the event loop and its answer is kept, the log file is written by
its own thread without losing a line, and the Speeds page's timer only runs while the Speeds tab can be seen (plus the "own time" line)."""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import offloop_log
import poll_cache

NODE = shutil.which("node")


def on_the_loop() -> bool:
    """True when called from a thread that runs an asyncio loop (a worker thread has none)."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


# ---- config.json ---------------------------------------------------------------------------------------------------------------------------
def age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


class TestConfigReader:
    def test_an_unchanged_old_file_is_read_from_disk_once(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        age(p, 60)
        r = poll_cache.ConfigReader()
        for _ in range(50):
            assert r.load(str(p)) == {"a": 1}
        assert r.disk_reads == 1

    def test_a_change_is_seen_by_the_very_next_read_even_with_the_same_size_and_time(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        r = poll_cache.ConfigReader()
        assert r.load(str(p)) == {"a": 1}
        p.write_text('{"a": 2}', encoding="utf-8")          # same size, written within the same instant
        assert r.load(str(p)) == {"a": 2}
        p.write_text('{"a": 3}', encoding="utf-8")
        assert r.load(str(p)) == {"a": 3}

    def test_a_change_to_an_old_file_is_seen_too(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        age(p, 60)
        r = poll_cache.ConfigReader()
        assert r.load(str(p)) == {"a": 1}
        p.write_text('{"a": 22}', encoding="utf-8")          # a new modified time and size
        assert r.load(str(p)) == {"a": 22}

    def test_missing_and_broken_files_read_as_empty(self, tmp_path):
        r = poll_cache.ConfigReader()
        assert r.load(str(tmp_path / "nope.json")) == {}
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert r.load(str(p)) == {}

    def test_every_caller_gets_its_own_copy_to_change(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text('{"a": {"b": 1}}', encoding="utf-8")
        age(p, 60)
        r = poll_cache.ConfigReader()
        first = r.load(str(p))
        first["a"]["b"] = 99
        first["new"] = True
        assert r.load(str(p)) == {"a": {"b": 1}}

    def test_the_app_sees_what_it_just_saved_and_what_another_program_wrote(self, proxy_module, tmp_path):
        cfg_path = proxy_module.CONFIG_FILE
        proxy_module._save_config({"x": 1})
        assert proxy_module._load_config() == {"x": 1}
        proxy_module._save_config({"x": 2, "y": [1, 2]})
        assert proxy_module._load_config() == {"x": 2, "y": [1, 2]}
        with open(cfg_path, "w", encoding="utf-8") as f:                  # (the tray app or a hand edit)
            json.dump({"x": 3, "y": [1, 2]}, f)
        assert proxy_module._load_config()["x"] == 3

    def test_a_burst_of_loads_reads_the_disk_once(self, proxy_module):
        proxy_module._save_config({"routing": {"routes": {}}})
        age(proxy_module.CONFIG_FILE, 60)
        proxy_module._config_reader.forget()
        before = proxy_module._config_reader.disk_reads
        for _ in range(300):
            proxy_module._load_config()
        assert proxy_module._config_reader.disk_reads - before == 1


# ---- PATH lookups ----------------------------------------------------------------------------------------------------------------------------
class TestCachedWhich:
    def test_remembered_and_forgettable(self, monkeypatch):
        monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: "")          # (the saved-PATH second look has its own tests)
        calls = []
        monkeypatch.setattr(poll_cache.shutil, "which", lambda n: calls.append(n) or "/x/" + n)
        assert poll_cache.cached_which("tool") == "/x/tool"
        assert poll_cache.cached_which("tool") == "/x/tool"
        assert calls == ["tool"]
        poll_cache.forget_which("tool")
        poll_cache.cached_which("tool")
        assert calls == ["tool", "tool"]

    def test_a_stale_answer_comes_back_at_once_and_a_thread_looks_again(self, monkeypatch):
        monkeypatch.setattr(poll_cache, "_saved_path_reader", lambda: "")
        answers = {"v": None}
        seen = []
        gate = threading.Event()

        def which(name):
            seen.append(threading.current_thread())
            if len(seen) > 1:
                gate.wait(5)
            return answers["v"]
        monkeypatch.setattr(poll_cache.shutil, "which", which)
        assert poll_cache.cached_which("tool", ttl=0.05) is None
        answers["v"] = "/now/installed"
        time.sleep(0.1)
        assert poll_cache.cached_which("tool", ttl=0.05) is None             # the old answer, immediately (the lookup is stuck on `gate`)
        gate.set()
        for _ in range(100):
            if poll_cache.cached_which("tool", ttl=0.05) == "/now/installed":
                break
            time.sleep(0.02)
        assert poll_cache.cached_which("tool", ttl=0.05) == "/now/installed"
        assert seen[1] is not threading.main_thread()


# ---- one shared answer ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestShared:
    async def test_everyone_asking_at_once_shares_one_computation_and_it_is_kept(self):
        runs = []

        async def work():
            runs.append(1)
            await asyncio.sleep(0.05)
            return len(runs)
        got = await asyncio.gather(*[poll_cache.shared("k", lambda v: 5.0, work) for _ in range(20)])
        assert set(got) == {1} and len(runs) == 1
        assert await poll_cache.shared("k", lambda v: 5.0, work) == 1
        poll_cache.forget("k")
        assert await poll_cache.shared("k", lambda v: 5.0, work) == 2

    async def test_the_time_kept_can_depend_on_the_answer(self):
        n = []

        async def work():
            n.append(1)
            return len(n) > 1
        ttl = lambda ok: 60.0 if ok else 0.0
        assert await poll_cache.shared("t", ttl, work) is False
        assert await poll_cache.shared("t", ttl, work) is True              # a "no" is asked again at once
        assert await poll_cache.shared("t", ttl, work) is True and len(n) == 2

    async def test_a_failure_reaches_everyone_waiting_and_is_not_kept(self):
        async def boom():
            await asyncio.sleep(0.02)
            raise RuntimeError("x")
        res = await asyncio.gather(*[poll_cache.shared("f", lambda v: 5.0, boom) for _ in range(3)], return_exceptions=True)
        assert all(isinstance(r, RuntimeError) for r in res)

        async def fine():
            return 7
        assert await poll_cache.shared("f", lambda v: 5.0, fine) == 7


# ---- Codex's sign-in check ------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestCodexLogin:
    async def test_the_program_starts_on_a_worker_thread_once_and_the_answer_is_kept(self, proxy_module, monkeypatch):
        runs = []

        def fake_run(cmd, **kw):
            runs.append((cmd, on_the_loop()))
            return SimpleNamespace(returncode=0)
        monkeypatch.setattr(proxy_module, "_codex_path", lambda: "/x/codex")
        monkeypatch.setattr(proxy_module.hidden_process, "run", fake_run)
        got = await asyncio.gather(*[proxy_module._codex_logged_in() for _ in range(10)])
        assert got == [True] * 10
        assert await proxy_module._codex_logged_in() is True
        assert runs == [(["/x/codex", "login", "status"], False)]

    async def test_a_no_is_asked_again_soon_and_refresh_forgets_at_once(self, proxy_module, monkeypatch):
        state = {"rc": 1}
        runs = []

        def fake_run(cmd, **kw):
            runs.append(1)
            return SimpleNamespace(returncode=state["rc"])
        monkeypatch.setattr(proxy_module, "_codex_path", lambda: "/x/codex")
        monkeypatch.setattr(proxy_module.hidden_process, "run", fake_run)
        assert await proxy_module._codex_logged_in() is False
        state["rc"] = 0
        assert await proxy_module._codex_logged_in() is False             # kept a few seconds
        status = await proxy_module._codex_status(force=True)              # the Refresh button
        assert status["testable"] is True and len(runs) == 2

    async def test_a_program_that_fails_or_hangs_reads_as_not_signed_in(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "_codex_path", lambda: "/x/codex")

        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 10)
        monkeypatch.setattr(proxy_module.hidden_process, "run", hang)
        assert await proxy_module._codex_logged_in() is False


# ---- graphics card read behind the Decisions and Voice polls ---------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestHardwareReadsLeaveTheLoop:
    async def test_the_voice_overview_reads_the_card_on_a_worker_thread_and_keeps_it(self, tmp_path, monkeypatch):
        import voice_wiring
        from tests.voice_helpers import good_hardware, make_engine
        engine, _src, _ = make_engine(tmp_path / "g")
        reads = []

        def hw(folder):
            reads.append(on_the_loop())
            return good_hardware()
        engine._hardware_fn = hw
        app = FastAPI()
        voice_wiring.setup(app, lambda: {}, lambda d: None, lambda: str(tmp_path / "settings"), lambda s: s, logging.getLogger("t"), this_pc=lambda: "gaming-pc",
                           engine=engine, main_port=lambda: 8123)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8123", timeout=30)
        try:
            for _ in range(3):
                d = (await client.get("/config/voice/overview")).json()
            assert d["thisPc"]["gpu"]["name"] == "NVIDIA GeForce RTX 5090"
            assert reads == [False]
        finally:
            await client.aclose()
            await engine.close()

    async def test_the_decisions_status_reads_the_card_on_a_worker_thread_and_keeps_it(self, tmp_path):
        import decision_wiring
        from tests.decision_helpers import hardware, make_engine
        engine, settings, _src = make_engine(tmp_path)
        reads = []

        def hw(folder):
            reads.append(on_the_loop())
            return hardware()
        engine._hardware_fn = hw
        app = FastAPI()
        decision_wiring.setup(app, lambda: {}, lambda d: None, lambda: str(tmp_path / "s"), lambda s: s, logging.getLogger("t"), None, engine.store, engine=engine,
                              remotes=None, this_pc=lambda: "gaming-pc", has_typesafe_key=lambda: False)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", timeout=30)
        try:
            for _ in range(3):
                d = (await client.get("/config/decisions/status")).json()
            assert d["thisPc"]["gpu"] is not None
            assert reads == [False]
        finally:
            await client.aclose()


# ---- the log file ----------------------------------------------------------------------------------------------------------------------------
class RecordingFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(levelname)s %(message)s")
        self.threads = []

    def format(self, record):
        self.threads.append(threading.current_thread())
        return super().format(record)


class TestOffLoopLog:
    def test_lines_reach_the_file_formatted_on_the_log_thread(self, tmp_path):
        path = tmp_path / "a.log"
        h = offloop_log.OffLoopFileHandler(str(path), mode="w", encoding="utf-8")
        fmt = RecordingFormatter()
        h.setFormatter(fmt)
        lg = logging.getLogger("offloop-test-1")
        lg.propagate = False
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        try:
            for i in range(500):
                lg.info("line %d with a dash — and %s", i, "args")
            h.flush()
            lines = path.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 500 and lines[0] == "INFO line 0 with a dash — and args" and lines[-1].startswith("INFO line 499")
            assert fmt.threads and all(t is not threading.main_thread() for t in fmt.threads)
        finally:
            lg.removeHandler(h)
            h.close()

    def test_nothing_is_lost_when_it_is_closed_straight_after_a_burst(self, tmp_path):
        path = tmp_path / "b.log"
        h = offloop_log.OffLoopFileHandler(str(path), mode="w", encoding="utf-8")
        lg = logging.getLogger("offloop-test-2")
        lg.propagate = False
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        for i in range(5000):
            lg.info("n%d", i)
        lg.removeHandler(h)
        h.close()
        assert len(path.read_text(encoding="utf-8").splitlines()) == 5000

    def test_drain_all_puts_everything_in_the_file_and_logging_goes_on_after(self, tmp_path):
        path = tmp_path / "c.log"
        h = offloop_log.OffLoopFileHandler(str(path), mode="w", encoding="utf-8")
        lg = logging.getLogger("offloop-test-3")
        lg.propagate = False
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        try:
            for i in range(300):
                lg.info("a%d", i)
            offloop_log.drain_all()
            assert len(path.read_text(encoding="utf-8").splitlines()) == 300
            lg.info("after")
            h.flush()
            assert path.read_text(encoding="utf-8").splitlines()[-1] == "after"
        finally:
            lg.removeHandler(h)
            h.close()

    def test_the_same_rotation_as_before(self, tmp_path):
        (tmp_path / "rot").mkdir()
        tmp_path = tmp_path / "rot"
        path = tmp_path / "d.log"
        h = offloop_log.OffLoopFileHandler(str(path), maxBytes=2000, backupCount=2, encoding="utf-8")
        lg = logging.getLogger("offloop-test-4")
        lg.propagate = False
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        for i in range(200):
            lg.info("%s", "x" * 50)
        lg.removeHandler(h)
        h.close()
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["d.log", "d.log.1", "d.log.2"]
        assert all(p.stat().st_size <= 2100 for p in tmp_path.iterdir())

    def test_the_handler_declared_in_the_servers_log_config_is_this_one_and_masks_keys(self, proxy_module, tmp_path):
        import logging.config
        path = tmp_path / "e.log"
        cfg = {"version": 1, "disable_existing_loggers": False,
               "formatters": {"f": {"()": proxy_module._RedactingFormatter, "fmt": "%(asctime)s %(levelname)-8s %(message)s"}},
               "handlers": {"file": {"()": offloop_log.OffLoopFileHandler, "filename": str(path), "mode": "w", "encoding": "utf-8", "formatter": "f"}},
               "loggers": {"offloop-test-5": {"handlers": ["file"], "level": "INFO", "propagate": False}}}
        logging.config.dictConfig(cfg)
        lg = logging.getLogger("offloop-test-5")
        h = lg.handlers[0]
        try:
            lg.info("key sk-ant-api03-abcdefghijklmnopqrstuvwx1234 here")
            h.flush()
            text = path.read_text(encoding="utf-8")
            assert "here" in text and "abcdefghijklmnopqrstuvwx1234" not in text
        finally:
            lg.removeHandler(h)
            h.close()

    def test_the_server_config_uses_it_for_both_kinds_of_log_file(self):
        src = (os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        text = open(os.path.join(src, "multiproxy.py"), encoding="utf-8").read()
        assert text.count('"()": offloop_log.OffLoopFileHandler') == 2


# ---- the page: Speeds timer and the "own time" line ---------------------------------------------------------------------------------------------
def run_node(proxy_module, body: str) -> str:
    if NODE is None:
        pytest.skip("Node isn't installed")
    import tempfile
    prelude = """
      const store = {};
      global.window = { localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } } };
      const els = {};
      global.$ = id => els[id] || (els[id] = { style: {}, className: '', classList: { toggle() {} } });
      global._escapeHtml = s => String(s);
      const listeners = {};
      global.document = { hidden: false, querySelectorAll: () => [], activeElement: null, addEventListener: (n, f) => { (listeners[n] = listeners[n] || []).push(f); } };
      global.setInterval = (f, ms) => { global.__intervals.push({ f, ms, alive: true }); return global.__intervals.length; };
      global.clearInterval = id => { if (global.__intervals[id - 1]) global.__intervals[id - 1].alive = false; };
      global.__intervals = [];
      global.fetchTime = async () => {};      // (lives in the Time view's own script, not in this one)
      global.fetchLocalSpeeds = async () => {};      // (the same, for the Local view)
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(prelude + proxy_module._SPEEDS_V2_JS + "\n" + body)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return out.stdout.decode("utf-8").strip()


@pytest.mark.skipif(NODE is None, reason="Node isn't installed")
class TestSpeedsPageTimer:
    def test_the_line_says_the_median_with_one_decimal_below_ten_and_whole_numbers_above(self, proxy_module):
        out = run_node(proxy_module, """
          console.log(JSON.stringify([ownTimeText({medianMs: 3.24, requests: 40}), ownTimeText({medianMs: 0.96, requests: 3}), ownTimeText({medianMs: 9.96, requests: 3}),
            ownTimeText({medianMs: 10, requests: 2}), ownTimeText({medianMs: 12.6, requests: 9}), ownTimeText({medianMs: 250.4, requests: 9})]));""")
        assert json.loads(out) == ["MultiProxy's own time: median 3.2 ms", "MultiProxy's own time: median 1.0 ms", "MultiProxy's own time: median 10 ms",
                                   "MultiProxy's own time: median 10 ms", "MultiProxy's own time: median 13 ms", "MultiProxy's own time: median 250 ms"]

    def test_the_line_is_hidden_when_there_is_nothing_to_say_and_an_older_server_is_tolerated(self, proxy_module):
        out = run_node(proxy_module, """
          console.log(JSON.stringify([ownTimeText(null), ownTimeText(undefined), ownTimeText({}), ownTimeText({medianMs: null, requests: 0}), ownTimeText({medianMs: 'x'}),
            ownTimeText({medianMs: NaN})]));
          renderOwnTime(undefined); const a = $('ownTimeLine').style.display + '|' + $('ownTimeLine').textContent;
          renderOwnTime({medianMs: 4.04, requests: 5}); const b = $('ownTimeLine').style.display + '|' + $('ownTimeLine').textContent;
          renderOwnTime({medianMs: null, requests: 0}); const c = $('ownTimeLine').style.display + '|' + $('ownTimeLine').textContent;
          console.log(JSON.stringify([a, b, c]));""")
        first, second = out.splitlines()
        assert json.loads(first) == [""] * 6
        assert json.loads(second) == ["none|", "block|MultiProxy's own time: median 4.0 ms", "none|"]

    def test_the_page_has_the_line_under_the_range_chooser_and_fetchSpeeds_draws_it(self, test_client, proxy_module):
        html = test_client.get("/").text
        assert 'id="ownTimeLine"' in html and html.index('id="rangePicker"') < html.index('id="ownTimeLine"') < html.index('id="speedsByModelView"')
        assert "renderOwnTime(data.ownTime);" in proxy_module._SPEEDS_V2_JS

    def test_the_timer_runs_only_while_the_speeds_tab_is_visible(self, proxy_module):
        out = run_node(proxy_module, """
          speedsTabLoaded = true;
          let rounds = 0;
          fetchSpeedPcs = async () => { rounds++; }; fetchSpeeds = async () => {}; fetchHealth = async () => {};
          const log = [];
          const pause = () => new Promise(r => setTimeout(r, 5));
          (async () => {
            $('paneSpeeds').style.display = 'none';
            speedsTimerSync(); log.push(['other tab', __intervals.length]);
            $('paneSpeeds').style.display = 'block';
            speedsTimerSync(); log.push(['speeds tab', __intervals.length, __intervals[0].alive, __intervals[0].ms]); await pause();
            document.hidden = true; speedsTimerSync(); log.push(['browser tab hidden', __intervals[0].alive]);
            document.hidden = false; speedsTimerSync(); log.push(['visible again', __intervals.length, __intervals[1].alive]); await pause();
            $('paneSpeeds').style.display = 'none'; speedsTimerSync(); log.push(['left the tab', __intervals[1].alive]);
            log.push(['rounds', rounds]); console.log(JSON.stringify(log));
          })();""")
        log = json.loads(out)
        assert log[0] == ["other tab", 0]
        assert log[1] == ["speeds tab", 1, True, 5000]
        assert log[2] == ["browser tab hidden", False]
        assert log[3] == ["visible again", 2, True]
        assert log[4] == ["left the tab", False]
        assert log[5] == ["rounds", 2], "one immediate round when the tab is first shown and one when it comes back"

    def test_a_round_never_starts_while_the_last_one_is_still_running(self, proxy_module):
        out = run_node(proxy_module, """
          speedsTabLoaded = true; $('paneSpeeds').style.display = 'block';
          let started = 0, release;
          const gate = new Promise(r => { release = r; });
          fetchSpeedPcs = async () => { started++; await gate; }; fetchSpeeds = async () => {}; fetchHealth = async () => {};
          (async () => {
            const a = speedsRound(); speedsRound(); speedsRound();
            const during = started;
            release(); await a;
            await speedsRound();
            console.log(JSON.stringify([during, started]));
          })();""")
        assert json.loads(out) == [1, 2]

    def test_the_by_job_and_time_views_join_the_round_only_when_shown(self, proxy_module):
        out = run_node(proxy_module, """
          speedsTabLoaded = true; $('paneSpeeds').style.display = 'block';
          const calls = [];
          fetchSpeedPcs = async () => {}; fetchSpeeds = async () => {}; fetchHealth = async () => {};
          fetchByRole = async () => { calls.push('role'); }; fetchTime = async () => { calls.push('time'); };
          byRoleLoaded = true; $('speedsByRoleView').style.display = 'block'; $('speedsTimeView').style.display = 'none';
          (async () => { await speedsRound(); $('speedsByRoleView').style.display = 'none'; $('speedsTimeView').style.display = 'block'; await speedsRound();
            console.log(JSON.stringify(calls)); })();""")
        assert json.loads(out) == ["role", "time"]


def js_of(module_name: str, const: str) -> str:
    return getattr(__import__(module_name), const)


class TestOtherPageTimersRespectAHiddenBrowserTab:
    @pytest.mark.parametrize("module,const,needle", [
        ("routing_ui", "ROUTING_JS", "if (rtLoading || rtBusy || document.hidden) return;"),
        ("voice_ui", "VOICE_JS", "if (vcLoading || document.hidden) return;"),
        ("decision_ui", "DECISION_JS", "if (dcLoading || document.hidden) return;"),
        ("other_pcs_ui", "OTHER_PCS_JS", "if (!document.hidden) await opRefresh(false);"),
        ("local_models_ui", "LOCAL_MODELS_JS", "if (document.hidden) { lmSchedule(); return; }"),
        ("local_models_ui", "LOCAL_MODELS_JS", "if (document.hidden) { memTimer = setTimeout(memRefresh, 3500); return; }"),
        ("get_started_ui", "GET_STARTED_JS", "if (document.hidden) return;"),
    ])
    def test_each_poll_skips_its_request_while_the_browser_tab_is_hidden(self, module, const, needle):
        import importlib
        mod = importlib.import_module(module)
        names = [n for n in dir(mod) if n.endswith("_JS") or n.isupper() and isinstance(getattr(mod, n), str)]
        assert any(needle in getattr(mod, n) for n in names if isinstance(getattr(mod, n), str)), needle

    @pytest.mark.parametrize("module,needle", [
        ("routing_ui", "if (!document.hidden && rtTimer) rtLoad();"),
        ("voice_ui", "if (!document.hidden && vcTimer) vcLoad();"),
        ("decision_ui", "if (!document.hidden && dcTimer) dcLoad();"),
    ])
    def test_a_poll_that_was_skipped_refreshes_the_moment_the_tab_is_shown(self, module, needle):
        import importlib
        mod = importlib.import_module(module)
        assert any(needle in getattr(mod, n) for n in dir(mod) if isinstance(getattr(mod, n), str))


@pytest.mark.asyncio
class TestSlowValue:
    async def test_the_first_ask_waits_on_a_worker_thread_then_polls_never_wait(self):
        reads = []
        gate = threading.Event()

        def compute():
            reads.append(on_the_loop())
            if len(reads) > 1:
                gate.wait(5)                      # (the second read is slow: no poll may wait for it)
            return len(reads)
        v = poll_cache.SlowValue(compute, ttl=0.05)
        await v.ensure()
        assert v.get() == 1 and reads == [False]
        await asyncio.sleep(0.1)                  # now stale
        t = time.perf_counter()
        await v.ensure()
        await v.ensure()
        assert time.perf_counter() - t < 0.5 and v.get() == 1, "the stale value is handed out at once while a thread reads again"
        gate.set()
        for _ in range(100):
            if v.get() == 2:
                break
            await asyncio.sleep(0.02)
        assert v.get() == 2 and reads == [False, False], "only one background read ran, on a worker thread"

    async def test_a_failing_read_does_not_wedge_it(self):
        n = []

        def compute():
            n.append(1)
            if len(n) == 2:
                raise RuntimeError("driver")
            return len(n)
        v = poll_cache.SlowValue(compute, ttl=0.0)
        await v.ensure()
        await v.ensure()                          # starts the failing background read
        await asyncio.sleep(0.2)
        await v.ensure()                          # asks again: the flag was cleared
        await asyncio.sleep(0.2)
        assert v.get() == 3


@pytest.mark.asyncio
class TestSharedStaleOk:
    async def test_the_first_ask_waits_then_an_expired_answer_is_handed_out_while_one_task_makes_the_next(self):
        gate = asyncio.Event()
        runs = []

        async def work():
            runs.append(1)
            if len(runs) > 1:
                await gate.wait()
            return len(runs)
        ttl = lambda v: 0.0
        assert await poll_cache.shared("s", ttl, work, stale_ok=True) == 1
        t = time.perf_counter()
        got = [await poll_cache.shared("s", ttl, work, stale_ok=True) for _ in range(5)]
        await asyncio.sleep(0)
        assert got == [1] * 5 and time.perf_counter() - t < 0.5 and len(runs) == 2, "one refresh started, nobody waited for it"
        gate.set()
        await asyncio.sleep(0.05)
        assert await poll_cache.shared("s", lambda v: 60.0, work, stale_ok=True) == 2

    async def test_a_failed_refresh_keeps_the_old_answer(self):
        n = []

        async def work():
            n.append(1)
            if len(n) == 2:
                raise RuntimeError("down")
            return len(n)
        ttl = lambda v: 0.0
        assert await poll_cache.shared("e", ttl, work, stale_ok=True) == 1
        assert await poll_cache.shared("e", ttl, work, stale_ok=True) == 1      # starts the failing refresh
        await asyncio.sleep(0.05)
        assert await poll_cache.shared("e", ttl, work, stale_ok=True) == 1
        await asyncio.sleep(0.05)
        assert await poll_cache.shared("e", lambda v: 60.0, work, stale_ok=True) == 3
