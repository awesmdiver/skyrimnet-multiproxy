"""Every download shows the time left (from the speed really measured) and the model downloads can be paused and resumed (2026-10-26).

A stand-in web server serves a small file slowly (it stops halfway until the test lets it go); a fake clock stands in for time. No real download is made."""
import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

import download_meter as dm
import local_models as lm
from download_meter import Meter, left_text

MB = 1024 * 1024
MODEL = bytes((i * 7) % 251 for i in range(3 * MB))
VISION = b"v" * 1000


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---- the time left: the maths and the words ----
def test_nothing_is_said_before_ten_seconds_of_data():
    c = Clock()
    m = Meter(c)
    for _ in range(9):
        c.t += 1
        m.note(MB)
    assert m.text(9 * MB, 100 * MB) == ""
    c.t += 1
    m.note(MB)
    assert m.text(10 * MB, 100 * MB) == "about 1 min left" or m.text(10 * MB, 100 * MB).startswith("about ")


def test_the_time_follows_the_speed_of_the_last_thirty_seconds():
    c = Clock()
    m = Meter(c)
    done = 0
    for _ in range(40):                       # 1 MB a second
        c.t += 1
        m.note(MB)
        done += MB
    slow = m.text(done, 4000 * MB)
    for _ in range(30):                       # then 4 MB a second, for the whole window
        c.t += 1
        for _ in range(4):
            m.note(MB)
        done += 4 * MB
    fast = m.text(done, 4000 * MB)
    assert slow == "about 1 h 5 min left"
    assert fast == "about 15 min left"
    assert m.speed() == pytest.approx(4 * MB, rel=0.1)


def test_no_data_for_fifteen_seconds_says_waiting_for_the_network():
    c = Clock()
    m = Meter(c)
    for _ in range(12):
        c.t += 1
        m.note(MB)
    assert m.text(12 * MB, 100 * MB).startswith("about")
    c.t += 15
    assert m.text(12 * MB, 100 * MB) == "waiting for network"
    c.t += 1
    m.note(MB)
    assert m.text(13 * MB, 100 * MB).startswith("about")


def test_bytes_already_on_disk_are_not_speed():
    c = Clock()
    m = Meter(c)
    m.note(2_000_000_000)                     # a resumed file's credit, or a file that was already complete
    for _ in range(12):
        c.t += 1
        m.note(MB)
    assert m.speed() == pytest.approx(MB, rel=0.1)


def test_the_figure_is_calm_and_rounded():
    assert left_text(20) == "less than a minute left"
    assert left_text(100) == "about 2 min left"
    assert left_text(40 * 60 + 70) == "about 40 min left"
    assert left_text(3600) == "about 1 h left"
    assert left_text(3600 + 17 * 60) == "about 1 h 15 min left"


def test_a_finished_or_unsized_download_says_nothing():
    c = Clock()
    m = Meter(c)
    for _ in range(12):
        c.t += 1
        m.note(MB)
    assert m.text(100 * MB, 100 * MB) == "" and m.text(5 * MB, 0) == ""


def test_the_meter_observing_a_running_total_skips_what_was_there_at_the_start():
    c = Clock()
    m = Meter(c)
    m.observe(5 * MB)
    for i in range(1, 13):
        c.t += 1
        m.observe(5 * MB + i * MB)
    assert m.speed() == pytest.approx(MB, rel=0.1)


# ---- Pause and Resume of a model download ----
@asynccontextmanager
async def slow_server():
    """Serves the model file, stopping after the first 1 MiB until `gate` is set. Honours Range."""
    gate = asyncio.Event()
    seen = []

    async def handler(request):
        name = request.path.rsplit("/", 1)[-1]
        data = {"tiny.gguf": MODEL, "mmproj-tiny.gguf": VISION}[name]
        rng = request.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        seen.append((name, start))
        resp = web.StreamResponse(status=206 if rng else 200, headers={"Content-Length": str(len(data) - start),
                                                                      **({"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"} if rng else {})})
        await resp.prepare(request)
        pos = start
        while pos < len(data):
            end = min(len(data), pos + MB)
            await resp.write(data[pos:end])
            pos = end
            if name == "tiny.gguf" and pos == start + MB and not gate.is_set():
                await gate.wait()
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", gate, seen
    finally:
        gate.set()
        await runner.cleanup()


def _make(tmp_path, store, monkeypatch, base):
    catalog = [{"id": "tiny-model", "name": "Tiny Model", "blurb": "for tests", "vision": True, "repo": "acme/tiny",
                "files": [{"file": "tiny.gguf", "size": len(MODEL), "sha256": _sha(MODEL), "role": "model"},
                          {"file": "mmproj-tiny.gguf", "size": len(VISION), "sha256": _sha(VISION), "role": "mmproj"}]}]
    monkeypatch.setattr(lm, "CATALOG", catalog)
    monkeypatch.setattr(lm, "CATALOG_BY_ID", {m["id"]: m for m in catalog})
    monkeypatch.setattr(lm, "HF_URL", base + "/{repo}/{file}")
    monkeypatch.setattr(lm, "_free_space_ok", lambda *a, **k: True, raising=False)
    s = lm.LocalModels(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), logging.getLogger("t"),
                       str(tmp_path / "pids.json"), session_factory=__import__("aiohttp").ClientSession)
    s.set_folder(str(tmp_path / "m"))
    return s


async def _until(cond, seconds=10):
    for _ in range(int(seconds / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def _part(svc):
    return svc.folder / "models" / "tiny-model" / "tiny.gguf.part"


@pytest.mark.asyncio
async def test_pause_keeps_the_partial_file_and_resume_carries_on_from_the_same_byte(tmp_path, monkeypatch):
    store = {}
    async with slow_server() as (base, gate, seen):
        svc = _make(tmp_path, store, monkeypatch, base)
        job = svc.download_model("tiny-model")
        await _until(lambda: job.done >= MB)
        await svc.pause("model:tiny-model")
        kept = _part(svc).stat().st_size
        assert kept >= MB and "model:tiny-model" not in svc.jobs
        snap = (await svc.snapshot())["catalog"][0]["download"]
        assert snap["state"] == "paused" and snap["done"] >= kept and snap["left"] == ""
        gate.set()
        job2 = svc.resume("model:tiny-model")
        await _until(lambda: job2.state != "running")
        assert job2.state == "done", job2.error
        assert (svc.folder / "models" / "tiny-model" / "tiny.gguf").read_bytes() == MODEL          # size and checksum passed, so the file is whole
        assert ("tiny.gguf", kept) in seen, "it asked for the rest from the byte it had, not from zero"
        assert [s for n, s in seen if n == "tiny.gguf"].count(0) == 1
        assert "paused" not in store.get("local_models", {})


@pytest.mark.asyncio
async def test_a_paused_download_is_still_paused_after_a_restart(tmp_path, monkeypatch):
    store = {}
    async with slow_server() as (base, gate, seen):
        svc = _make(tmp_path, store, monkeypatch, base)
        job = svc.download_model("tiny-model")
        await _until(lambda: job.done >= MB)
        await svc.pause("model:tiny-model")
        again = _make(tmp_path, store, monkeypatch, base)             # a new program run: the same settings and folder, no memory of the job
        d = (await again.snapshot())["catalog"][0]["download"]
        assert d["state"] == "paused" and d["done"] >= MB and d["total"] == len(MODEL) + len(VISION)
        gate.set()
        j = again.resume("model:tiny-model")
        await _until(lambda: j.state != "running")
        assert j.state == "done"


@pytest.mark.asyncio
async def test_a_paused_download_whose_partial_file_is_gone_is_just_a_download_again(tmp_path, monkeypatch):
    store = {}
    async with slow_server() as (base, gate, seen):
        svc = _make(tmp_path, store, monkeypatch, base)
        job = svc.download_model("tiny-model")
        await _until(lambda: job.done >= MB)
        await svc.pause("model:tiny-model")
        _part(svc).unlink()
        assert (await svc.snapshot())["catalog"][0]["download"] is None


@pytest.mark.asyncio
async def test_two_clicks_never_start_two_fetches(tmp_path, monkeypatch):
    store = {}
    async with slow_server() as (base, gate, seen):
        svc = _make(tmp_path, store, monkeypatch, base)
        a = svc.download_model("tiny-model")
        b = svc.download_model("tiny-model")
        assert a is b
        await _until(lambda: a.done >= MB)
        await svc.pause("model:tiny-model")
        gate.set()
        r1 = svc.resume("model:tiny-model")
        r2 = svc.resume("model:tiny-model")
        assert r1 is r2
        await _until(lambda: r1.state != "running")
        assert r1.state == "done"
        assert [s for n, s in seen if n == "tiny.gguf"].count(0) == 1, "the file was never started again from zero"


@pytest.mark.asyncio
async def test_cancel_after_a_pause_clears_the_pause_and_keeps_the_bytes(tmp_path, monkeypatch):
    store = {}
    async with slow_server() as (base, gate, seen):
        svc = _make(tmp_path, store, monkeypatch, base)
        job = svc.download_model("tiny-model")
        await _until(lambda: job.done >= MB)
        await svc.pause("model:tiny-model")
        await svc.cancel("model:tiny-model")
        assert (await svc.snapshot())["catalog"][0]["download"] is None
        assert _part(svc).exists()


@pytest.mark.asyncio
async def test_a_running_job_reports_its_time_left_from_its_meter(tmp_path, monkeypatch):
    store = {}
    async with slow_server() as (base, gate, seen):
        svc = _make(tmp_path, store, monkeypatch, base)
        job = svc.download_model("tiny-model")
        c = Clock()
        job.meter = Meter(c)
        assert job.to_json()["left"] == ""
        for _ in range(12):
            c.t += 1
            job.meter.note(MB)
        job.total = 100 * MB
        job.done = 12 * MB
        assert job.to_json()["left"].startswith("about")
        c.t += 20
        assert job.to_json()["left"] == "waiting for network"
        await svc.cancel("model:tiny-model")


def test_the_page_scripts_carry_the_buttons_and_the_words():
    import local_models_ui
    import decision_ui
    import set_me_up_ui
    js = local_models_ui.LOCAL_MODELS_JS
    assert "Pause" in js and "Resume" in js and "Paused · " in js and "GB downloaded" in js and "/config/local-models/pause" in js
    assert "dcPause" in decision_ui.DECISION_JS and "Paused" in decision_ui.DECISION_JS
    assert "smuPause" in set_me_up_ui.JS and "Resume" in set_me_up_ui.JS
