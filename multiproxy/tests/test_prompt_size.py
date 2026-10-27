"""Prompt size (Speeds tab, 2026-10-25): per job, the typical and biggest prompt SkyrimNet sent, against the context of the model that answered it. Numbers come only from the
Speeds history's `tokens_in` (a request without one is left out, never counted as zero); the verdict is green up to 70% of the context, amber up to the context, red above it, and
nothing at all when the context is not known. The Local models card shows one warning line only when something is wrong.
"""
import json
import shutil
import sqlite3
import subprocess
import time

import pytest

import prompt_size as ps
import prompt_size_ui

NODE = shutil.which("node")
K = 1024


class Db:
    def __init__(self, path):
        self.path = path

    def add(self, job="dialogue", tokens=5000, *, model="gemma", provider="Local", ago=60.0, ok=1):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO speed_history (ts, model, elapsed, success, provider, job, tokens_in) VALUES (?,?,?,?,?,?,?)",
                      (time.time() - ago, model, 1.0, ok, provider, job, tokens))


@pytest.fixture
def db(proxy_module):
    proxy_module._speed_cache.clear()
    return Db(proxy_module.SPEED_HISTORY_DB)


def _read(db, range_="today"):
    return {g["job"]: g for g in ps.read(db.path, time.time(), range_)}


class TestRead:
    def test_typical_is_the_median_and_biggest_the_maximum(self, db):
        for t in (1000, 2000, 9000, 4000):
            db.add(tokens=t)
        g = _read(db)["dialogue"]
        assert (g["typical"], g["biggest"], g["requests"]) == (3000, 9000, 4)

    def test_a_request_with_no_token_count_is_left_out_never_counted_as_zero(self, db):
        db.add(tokens=None)
        db.add(tokens=0)
        db.add(tokens=4000)
        g = _read(db)["dialogue"]
        assert (g["typical"], g["biggest"], g["requests"]) == (4000, 4000, 1), "a missing count must not drag the middle value down"
        db.add(job="memory", tokens=None)
        assert "memory" not in _read(db)

    def test_the_range_decides_which_requests_count(self, db):
        db.add(tokens=30000, ago=2 * 3600)             # two hours ago
        db.add(tokens=8000, ago=60)
        assert _read(db, "hour")["dialogue"]["biggest"] == 8000
        assert _read(db, "today")["dialogue"]["biggest"] == 30000
        db.add(job="diary", tokens=7000, ago=3 * 86400)
        assert "diary" not in _read(db, "today") and _read(db, "week")["diary"]["biggest"] == 7000

    def test_the_model_that_answered_most_is_the_one_named(self, db):
        db.add(tokens=100, model="a")
        db.add(tokens=100, model="b")
        db.add(tokens=100, model="b")
        g = _read(db)["dialogue"]
        assert (g["model"], g["provider"]) == ("b", "Local")

    def test_the_hourly_strip_is_the_largest_prompt_per_hour_oldest_first(self, db):
        db.add(tokens=3000, ago=60)                    # the last hour
        db.add(tokens=9000, ago=90)
        db.add(tokens=5000, ago=3 * 3600 + 60)         # 3 to 4 hours ago
        hours = _read(db)["dialogue"]["hours"]
        assert len(hours) == 24 and hours[23] == 9000 and hours[20] == 5000 and sum(1 for h in hours if h) == 2

    def test_the_strip_covers_24_hours_even_for_the_last_hour_range(self, db):
        db.add(tokens=5000, ago=10 * 3600)
        db.add(tokens=2000, ago=60)
        g = _read(db, "hour")["dialogue"]
        assert g["biggest"] == 2000 and g["hours"][13] == 5000

    def test_a_row_with_no_job_is_left_out(self, db):
        db.add(job=None, tokens=4000)
        assert _read(db) == {}

    def test_an_empty_history_gives_nothing(self, db):
        assert ps.read(db.path, time.time(), "today") == []


class TestVerdict:
    def test_up_to_70_percent_fits_with_room(self):
        v = ps.verdict(int(0.7 * 32 * K), 32 * K)
        assert v == {"level": "ok", "text": "Fits easily"}

    def test_above_70_percent_is_amber_with_a_too_small_and_a_safe_size(self):
        v = ps.verdict(int(29.6 * K), 32 * K)
        assert v == {"level": "warn", "text": "Close: 24K is too small, 40K is safe"}

    def test_exactly_the_context_is_still_amber(self):
        assert ps.verdict(32 * K, 32 * K)["level"] == "warn"

    def test_above_the_context_is_red(self):
        v = ps.verdict(40 * K, 32 * K)
        assert v == {"level": "bad", "text": "Too small: the biggest prompt (40K) is larger than the context (32K)"}

    def test_the_safe_size_is_a_quarter_more_rounded_up_to_the_next_4k(self):
        assert "20K is safe" in ps.verdict(int(14.5 * K), 16 * K)["text"]       # 14.5K + a quarter = 18.1K -> 20K

    def test_a_model_with_no_known_context_gets_numbers_only(self):
        assert ps.verdict(9000, None, "Cloud") == {"level": None, "text": "Cloud model: no limit to compare"}
        assert ps.verdict(9000, None, "This device") == {"level": None, "text": "Unknown context"}

    def test_sizes_are_written_with_k_of_1024(self):
        assert [ps.fmt(n) for n in (900, 16384, 32768, 30310, 131072)] == ["900", "16K", "32K", "29.6K", "128K"]


class TestRoute:
    @pytest.fixture(autouse=True)
    def _on(self, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)

    def test_each_job_comes_with_its_name_place_context_and_verdict(self, db, test_client, proxy_module):
        # a built-in model: the default settings give each request 16K (32K shared by 2)
        for t in (4000, 6000, 15000):
            db.add(job="dialogue", tokens=t, model="gemma", provider="Local")
        db.add(job="diary", tokens=9000, model="claude-sonnet-5-5", provider="Claude")
        body = test_client.get("/config/prompt-size?range=today").json()
        jobs = {j["job"]: j for j in body["jobs"]}
        d = jobs["dialogue"]
        assert (d["where"], d["context"], d["contextText"], d["biggest"], d["biggestText"]) == ("This device", 16 * K, "16K", 15000, "14.6K")
        assert d["verdict"]["level"] == "warn", "15000 of 16384 is above 70%"
        c = jobs["diary"]
        assert (c["where"], c["context"], c["verdict"]) == ("Cloud", None, {"level": None, "text": "Cloud model: no limit to compare"})

    def test_a_bad_range_is_refused_and_tracking_off_says_so(self, test_client, proxy_module, monkeypatch):
        assert test_client.get("/config/prompt-size?range=month").status_code == 400
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", False)
        assert test_client.get("/config/prompt-size").status_code == 404

    def test_nothing_is_drawn_without_a_counted_request(self, db, test_client):
        db.add(tokens=None)
        assert test_client.get("/config/prompt-size?range=week").json()["jobs"] == []

    def test_the_context_follows_the_saved_settings_per_model(self, proxy_module):
        svc = proxy_module.local_svc
        svc.engine_settings.save("local", {"ctx": 65536, "parallel": 2}, {"big": {"ctx": 98304, "parallel": 3}}, "b1")
        assert proxy_module._prompt_context("plain", "Local") == 32 * K
        assert proxy_module._prompt_context("big", "Local") == 32 * K
        assert proxy_module._prompt_context("claude-sonnet-5-5", "Claude") is None
        assert proxy_module._prompt_context("some-model", "Ollama") is None


class TestPage:
    def test_the_page_has_the_view_the_route_and_still_parses(self, test_client):
        html = test_client.get("/").text
        assert "optSpeedsSize" in html and "/config/prompt-size" in html and "pszLocalWarn" in html

    @pytest.mark.skipif(NODE is None, reason="Node isn't installed")
    def test_the_local_models_line_shows_only_when_close(self, tmp_path):
        jobs = [
            {"provider": "Local", "model": "close", "biggest": 29900, "biggestText": "29.2K", "context": 32768, "contextText": "32K", "verdict": {"level": "warn"}},
            {"provider": "Local", "model": "fine", "biggest": 9000, "biggestText": "8.8K", "context": 32768, "contextText": "32K", "verdict": {"level": "ok"}},
            {"provider": "Local", "model": "over", "biggest": 40000, "biggestText": "39K", "context": 32768, "contextText": "32K", "verdict": {"level": "bad"}},
            {"provider": "Claude", "model": "close", "biggest": 1, "biggestText": "1", "context": None, "contextText": None, "verdict": {"level": None}},
        ]
        script = ("const _escapeHtml = s => String(s); const setTimeout = () => 0;\n" + prompt_size_ui.PROMPT_SIZE_JS
                  + "\npszToday = " + json.dumps(jobs) + ";\nconsole.log(JSON.stringify(['close', 'fine', 'over', 'none'].map(pszLocalWarn)));\n")
        f = tmp_path / "warn.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, r.stderr[:600]
        close, fine, over, none = json.loads(r.stdout)
        assert "biggest prompt today was 29.2K" in close and "close to 32K" in close and "See Prompt size" in close
        assert fine == "" and none == "", "all is well: nothing is drawn"
        assert "larger than 32K" in over
