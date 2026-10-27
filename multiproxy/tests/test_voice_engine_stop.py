"""Uninstalling, changing or reinstalling a voice stops its engine first (by the id the manager wrote down), a locked file ends as one plain sentence, an engine left
over with no voice installed is stopped, and the engine's repeated log line is trimmed. The stand-in engine (tests/fake_audiocpp_server.py) is the program that runs."""
import io
import os
import subprocess
import sys
import time

import pytest

import voice_engine as ve
from tests.test_voice_engine import installed_engine
from tests.voice_helpers import fake_launcher, good_hardware


def alive(pid: int) -> bool:
    return bool(ve.process_image(pid))


def wait_gone(pid: int, seconds: float = 8) -> bool:
    t = time.time()
    while time.time() - t < seconds:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


def second_manager(engine, tmp_path, store):
    """A new manager on the same folder: what MultiProxy is after a restart (it did not start the running engine itself)."""
    return ve.VoiceEngine(lambda: tmp_path / "voice", store, builds=engine.builds, model=engine.model, launcher=fake_launcher, hardware_fn=good_hardware,
                          port_range=(28950, 28999))


@pytest.mark.asyncio
async def test_uninstall_of_a_running_voice_stops_the_engine_first_then_removes_everything(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    pid = engine.proc.pid
    assert engine.pid_file.exists()
    await engine.uninstall()
    assert wait_gone(pid)
    assert not engine.engine_dir.exists() and not engine.models_dir.exists() and not engine.pid_file.exists()
    await engine.close()


@pytest.mark.asyncio
async def test_an_engine_an_earlier_run_left_behind_is_stopped_by_its_recorded_id(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    pid = engine.proc.pid
    other = second_manager(engine, tmp_path, store)
    assert other.proc is None and other.pid_file.exists()
    await other.uninstall()
    assert wait_gone(pid) and not other.engine_dir.exists()
    await other.close()
    engine.proc = None
    await engine.close()


@pytest.mark.asyncio
async def test_an_engine_that_will_not_exit_leaves_every_file_and_says_so_plainly(tmp_path, monkeypatch):
    engine, src, store = await installed_engine(tmp_path)
    pid = engine.proc.pid
    other = second_manager(engine, tmp_path, store)
    real_kill = ve.kill_tree_sync
    monkeypatch.setattr(ve, "kill_tree_sync", lambda p: None)
    monkeypatch.setattr(ve, "STOP_WAIT_SECONDS", 0.6)
    try:
        with pytest.raises(ve.VoiceEngineError) as e:
            await other.uninstall()
        assert e.value.message == ve.STILL_RUNNING and e.value.status == 409
        assert other.engine_dir.is_dir() and other.models_dir.is_dir()
        assert ve.STILL_RUNNING.replace("**", "") in other.log_tail()
        assert alive(pid)
    finally:
        monkeypatch.setattr(ve, "kill_tree_sync", real_kill)
        real_kill(pid)
        await other.close()
        await engine.close()


@pytest.mark.asyncio
async def test_a_reinstall_stops_the_old_engine_before_touching_its_files(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    pid = engine.proc.pid
    other = second_manager(engine, tmp_path, store)
    from voice_downloads import FolderSource
    other.start_install(FolderSource(src))
    t = time.time()
    while other.job.state == "running" and time.time() - t < 60:
        time.sleep(0.05)
        await __import__("asyncio").sleep(0)
    assert other.job.state == "done", other.job.error
    assert wait_gone(pid) and other.proc.pid != pid
    await other.close()
    await engine.close()


@pytest.mark.asyncio
async def test_a_recorded_id_that_now_belongs_to_another_program_is_never_touched(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    await engine.stop()
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        engine._record_pid(bystander.pid, "notepad.exe")           # the id was reused by something else after a reboot
        assert await engine.stop_recorded() is True
        assert not engine.pid_file.exists() and bystander.poll() is None
    finally:
        bystander.kill()
        await engine.close()


@pytest.mark.asyncio
async def test_an_engine_still_alive_with_no_voice_installed_is_stopped(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    pid = engine.proc.pid
    other = second_manager(engine, tmp_path, store)
    (other.models_dir / other.model.name).unlink()                # nothing is installed any more, but the engine runs on
    assert not other.installed()
    await other.reap_if_not_installed()
    assert wait_gone(pid) and not other.pid_file.exists()
    await other.close()
    engine.proc = None
    await engine.close()


def test_the_engines_repeated_line_is_written_once_with_a_count(tmp_path):
    noisy = b"ggml_backend_cuda_graph_compute: CUDA graph warmup complete\n"
    pipe = io.BytesIO(b"starting\n" + noisy * 300 + b"a real warning\n")
    out = tmp_path / "engine-stdout.log"
    ve.pump_engine_output(pipe, out)
    text = out.read_text(encoding="utf-8")
    assert text.count("CUDA graph warmup complete") == 1 and "300 times" in text
    assert text.startswith("starting\n") and "a real warning" in text


@pytest.mark.skipif(os.name == "nt", reason="process groups are a Linux idea; Windows ends the program with taskkill")
@pytest.mark.asyncio
async def test_the_engine_runs_in_its_own_group_so_stopping_it_never_signals_this_program(tmp_path):
    engine, _, _ = await installed_engine(tmp_path)
    try:
        assert os.getpgid(engine.proc.pid) != os.getpgrp()
    finally:
        await engine.close()
