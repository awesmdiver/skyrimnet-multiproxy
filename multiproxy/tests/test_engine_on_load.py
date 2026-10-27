"""Loading a model fetches the llama.cpp engine by itself when it isn't there yet (2026-10-01). The director added models that were
already on his laptop, pressed Load, and was told to "install your first model": loading IS installing. The same code serves the
Courier and the main device's own Local models; a fake release (a tiny zip on a local web server) stands in for GitHub.
"""
import asyncio
import io
import json
import os
import zipfile
from pathlib import Path

import aiohttp
import pytest

import courier_adapters as ca
import local_models as lm
from tests.test_courier import env  # noqa: F401  (a running Courier with fake Ollama / LM Studio behind it)
from tests.test_local_models import _sha, serve, store, svc  # noqa: F401

pytestmark = pytest.mark.asyncio
MB = 1024 * 1024
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"
ZIP = io.BytesIO()
with zipfile.ZipFile(ZIP, "w") as _z:
    _z.writestr(EXE, b"fake engine")
RELEASE = ZIP.getvalue()
ERR_DEAD_END = "It will download automatically whenever you install your first model"


def gguf(path: Path, mb: int = 60) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(mb * MB)
    return path


@pytest.fixture
def release(monkeypatch):
    """A fake engine release: the table says 'cpu' is this zip, and the download URL is filled in by `up()`."""
    monkeypatch.setattr(lm, "engine_files_table", lambda: {"cpu": [("engine.zip", len(RELEASE), _sha(RELEASE))]})
    monkeypatch.setattr(lm, "platform_supported", lambda: True)
    return RELEASE


async def until(cond, secs=15.0):
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < secs:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def no_engine(courier):
    courier.local._update(engine=None)
    assert courier.local.engine_exe() is None


def llama(rep):
    return next(p for p in rep["programs"] if p["id"] == "llamacpp")


async def add_found(env, name="found-model"):
    path = gguf(env.lms_models / "x" / f"{name}.gguf")
    r = await env.call("POST", "/courier/v1/models/add", {"paths": [str(path)]})
    return r["added"][0]["id"]


class TestCourierLoadFetchesTheEngine:
    async def test_load_on_a_found_model_with_no_engine_gets_the_engine_then_loads_the_model(self, env, release, monkeypatch):
        mid = await add_found(env)
        no_engine(env.courier)
        async with serve({"engine.zip": release}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid, "wait": True})
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        m = next(m for m in llama(rep)["models"] if m["id"] == mid)
        assert m["loaded"] is True and "error" not in m
        assert env.courier.local.engine_exe() is not None and llama(rep)["installed"] is True

    async def test_the_row_shows_real_engine_progress_while_it_downloads_and_then_the_normal_loading_state(self, env, release, monkeypatch):
        mid = await add_found(env)
        no_engine(env.courier)
        gate = asyncio.Event()
        orig = lm.download_file

        async def slow(session, url, dest, size, sha, on_bytes):
            on_bytes(7)
            await gate.wait()
            on_bytes(-7)
            await orig(session, url, dest, size, sha, on_bytes)
        monkeypatch.setattr(lm, "download_file", slow)
        async with serve({"engine.zip": release}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid})
            rep = await env.call("GET", "/courier/v1/report?fresh=1")
            m = next(m for m in llama(rep)["models"] if m["id"] == mid)
            assert m["state"] == "loading" and m["engine_download"]["total"] == len(RELEASE) and m["engine_download"]["done"] >= 7
            gate.set()
            assert await until(lambda: env.courier.local.state_of(mid)[0] == "running")
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        m = next(m for m in llama(rep)["models"] if m["id"] == mid)
        assert m["loaded"] is True and "engine_download" not in m

    async def test_a_failed_engine_download_shows_on_that_models_row_and_can_be_retried(self, env, release, monkeypatch):
        mid = await add_found(env)
        no_engine(env.courier)
        async with serve({}) as (base, _):                                         # the release isn't there: a 404
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            status, _ = await env.err("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid, "wait": True})
        assert status == 502
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        m = next(m for m in llama(rep)["models"] if m["id"] == mid)
        assert m["state"] == "error" and "answered 404" in m["error"] and m["loaded"] is False
        async with serve({"engine.zip": release}) as (base, _):                    # Try again, now that it is there
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid, "wait": True})
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        assert next(m for m in llama(rep)["models"] if m["id"] == mid)["loaded"] is True

    async def test_no_internet_says_so_on_the_row(self, env, release, monkeypatch):
        mid = await add_found(env)
        no_engine(env.courier)

        async def offline(*a, **k):
            raise aiohttp.ClientConnectionError("offline")
        monkeypatch.setattr(lm, "download_file", offline)
        await env.err("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid, "wait": True})
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        err = next(m for m in llama(rep)["models"] if m["id"] == mid)["error"]
        # the download fix (2026-10-23): a connection that cannot be made says what could not be reached, and only then says to check the internet
        assert err.startswith("Couldn't download the engine software on this device.") and "Couldn't connect to github.com." in err and "Check the internet connection" in err, err

    async def test_cancel_stops_the_engine_download_and_leaves_the_row_ready_to_load_again(self, env, release, monkeypatch):
        mid = await add_found(env)
        no_engine(env.courier)
        gate = asyncio.Event()

        async def stuck(session, url, dest, size, sha, on_bytes):
            on_bytes(5)
            await gate.wait()
        monkeypatch.setattr(lm, "download_file", stuck)
        await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid})
        assert await until(lambda: env.courier.local.servers[mid].engine_wait)
        await env.call("POST", f"/courier/v1/programs/llamacpp/downloads/cancel", {"model": mid})
        rep = await env.call("GET", "/courier/v1/report?fresh=1")
        m = next(m for m in llama(rep)["models"] if m["id"] == mid)
        assert m["state"] == "available" and "error" not in m and "engine_download" not in m
        assert "engine" not in env.courier.local.jobs and env.courier.local.engine_exe() is None

    async def test_the_dead_end_message_is_gone_from_every_path(self, env, release):
        mid = await add_found(env)
        no_engine(env.courier)
        for path, body in (("/courier/v1/programs/llamacpp/models/load", {"model": mid}),):
            status, msg = await env.err("POST", path, body)
            assert ERR_DEAD_END not in msg
        assert ERR_DEAD_END not in Path(ca.__file__).read_text(encoding="utf-8")

    async def test_a_chat_request_for_an_added_model_with_no_engine_starts_the_download_and_answers_still_loading(self, env, release, monkeypatch):
        mid = await add_found(env)
        no_engine(env.courier)
        monkeypatch.setattr(lm, "REQUEST_WAIT_TIMEOUT", 0.3)
        gate = asyncio.Event()

        async def stuck(session, url, dest, size, sha, on_bytes):
            on_bytes(5)
            await gate.wait()
        monkeypatch.setattr(lm, "download_file", stuck)
        status, msg = await env.err("POST", "/v1/chat/completions", {"model": f"local/{mid}", "messages": [{"role": "user", "content": "hi"}]})
        assert status == 503 and "still loading" in msg and ERR_DEAD_END not in msg
        assert env.courier.local.jobs["engine"].state == "running", "the engine is being fetched, not a dead end"
        gate.set()
        await env.courier.local.cancel("engine")

    async def test_the_engine_being_there_does_not_change_a_normal_load(self, env):
        mid = await add_found(env)
        await env.call("POST", "/courier/v1/programs/llamacpp/models/load", {"model": mid, "wait": True})
        assert "engine" not in env.courier.local.jobs and env.courier.local.state_of(mid)[0] == "running"


class TestGamingPcsOwnLocalModelsDoTheSame:
    async def _svc(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "models"))
        gguf(tmp_path / "x" / "mine.gguf")
        m = svc.add_custom(str(tmp_path / "x" / "mine.gguf"))
        assert svc.engine_exe() is None
        return m

    async def test_start_with_no_engine_gets_it_first_then_loads(self, svc, tmp_path, release, monkeypatch):
        m = await self._svc(svc, tmp_path, monkeypatch)
        async with serve({"engine.zip": release}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start(m.id)
            assert s.state == "starting" and s.engine_wait is True
            await s.ready.wait()
        assert s.state == "running" and svc.engine_exe() is not None
        snap = await svc.snapshot()
        assert snap["engine"]["installed"] is True and next(r for r in snap["installed"] if r["id"] == m.id)["engineWait"] is False
        await svc.stop_all()

    async def test_the_snapshot_says_which_rows_are_waiting_for_the_engine(self, svc, tmp_path, release, monkeypatch):
        m = await self._svc(svc, tmp_path, monkeypatch)
        gate = asyncio.Event()

        async def stuck(session, url, dest, size, sha, on_bytes):
            on_bytes(3)
            await gate.wait()
        monkeypatch.setattr(lm, "download_file", stuck)
        await svc.start(m.id)
        snap = await svc.snapshot()
        row = next(r for r in snap["installed"] if r["id"] == m.id)
        assert row["engineWait"] is True and row["state"] == "starting" and snap["engine"]["download"]["state"] == "running"
        await svc.stop(m.id)                                                      # Cancel
        assert "engine" not in svc.jobs and svc.state_of(m.id)[0] == "stopped"

    async def test_a_failure_lands_on_the_models_row_and_start_tries_again(self, svc, tmp_path, release, monkeypatch):
        m = await self._svc(svc, tmp_path, monkeypatch)
        async with serve({}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start(m.id)
            await s.ready.wait()
        assert s.state == "failed" and "answered 404" in s.message
        async with serve({"engine.zip": release}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start(m.id)
            await s.ready.wait()
        assert s.state == "running"
        await svc.stop_all()

    async def test_a_request_with_no_engine_starts_the_fetch_and_answers_still_loading(self, svc, tmp_path, release, monkeypatch):
        m = await self._svc(svc, tmp_path, monkeypatch)
        monkeypatch.setattr(lm, "REQUEST_WAIT_TIMEOUT", 0.3)
        gate = asyncio.Event()

        async def stuck(session, url, dest, size, sha, on_bytes):
            await gate.wait()
        monkeypatch.setattr(lm, "download_file", stuck)
        with pytest.raises(lm.LocalModelError) as e:
            await svc.ensure_ready(m.model_name)
        assert e.value.status == 503 and "still loading" in e.value.message
        assert svc.jobs["engine"].state == "running"
        await svc.stop(m.id)

    async def test_no_room_for_the_engine_is_said_on_the_row(self, svc, tmp_path, release, monkeypatch):
        m = await self._svc(svc, tmp_path, monkeypatch)
        monkeypatch.setattr(lm.shutil, "disk_usage", lambda p: type("U", (), {"free": 1, "total": 10, "used": 9})())
        s = await svc.start(m.id)
        assert s.state == "failed" and "free" in s.message


class TestEngineFailureWordingMatchesTheButton:
    async def test_a_damaged_engine_download_says_press_try_again_on_the_model_row(self, svc, tmp_path, release, monkeypatch):
        svc.set_folder(str(tmp_path / "models"))
        gguf(tmp_path / "x" / "mine.gguf")
        m = svc.add_custom(str(tmp_path / "x" / "mine.gguf"))
        async with serve({"engine.zip": b"X" + release[1:]}) as (base, _):          # the right size, damaged
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start(m.id)
            await s.ready.wait()
        assert s.state == "failed" and "didn't pass its safety check" in s.message and s.message.endswith("Press Try again to start it again.")
        assert "Hit Download" not in s.message

    def test_a_catalog_download_keeps_the_hit_download_wording(self):
        assert "Press Download to start it again." in Path(lm.__file__).read_text(encoding="utf-8")
