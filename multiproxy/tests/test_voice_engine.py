"""The voice engine manager and the kept downloads: fit check, install (resume, cancel, bad checksum, no room), start/stop, an engine that died at
once, the Vulkan speed check, uninstall, status. A fake engine program stands in for the real one (tests/fake_audiocpp_server.py)."""
import asyncio
import os
import time
from pathlib import Path

import pytest

import voice_engine as ve
from local_models import GpuInfo, LocalModelError
from voice_downloads import DownloadStore, FileSpec, FolderSource
from tests.voice_helpers import good_hardware, make_engine, sha



async def wait_job(engine, seconds=60):
    t = time.time()
    while engine.job and engine.job.state == "running" and time.time() - t < seconds:
        await asyncio.sleep(0.05)
    return engine.job


async def installed_engine(tmp_path, monkeypatch=None, **kw):
    engine, src, store = make_engine(tmp_path, **kw)
    engine.start_install(FolderSource(src))
    job = await wait_job(engine)
    assert job.state == "done", job.error
    return engine, src, store


# ---- the fit check ------------------------------------------------------------------------------------------------------------------
def hw(**kw):
    base = dict(system="windows", gpu=GpuInfo("NVIDIA GeForce RTX 5090", 32768, "nvidia"), driver=617.14, free_vram_bytes=20 * 1024**3,
                free_ram_bytes=32 * 1024**3, free_disk_bytes=500 * 1024**3)
    base.update(kw)
    return ve.Hardware(**base)


def test_a_recent_nvidia_card_gets_the_newest_build_and_passes():
    f = ve.assess(hw())
    assert f.verdict == "ok" and f.build == "win-cuda13.3" and not f.speed_check


@pytest.mark.parametrize("driver,build", [(580.0, "win-cuda13.3"), (579.99, "win-cuda12.4"), (528.33, "win-cuda12.4")])
def test_the_nvidia_build_is_chosen_by_driver_version(driver, build):
    assert ve.assess(hw(driver=driver)).build == build


def test_a_driver_older_than_either_build_is_refused_with_a_plain_reason():
    f = ve.assess(hw(driver=511.0))
    assert f.verdict == "refuse" and "too old" in f.reasons[0] and "528.33" in f.reasons[0]


def test_no_graphics_card_is_refused_and_cpu_is_never_offered():
    f = ve.assess(hw(gpu=None))
    assert f.verdict == "refuse" and f.build is None and "graphics card" in f.reasons[0]


def test_other_cards_get_vulkan_with_a_speed_check_warning():
    f = ve.assess(hw(gpu=GpuInfo("AMD Radeon RX 7800", 16384, "amd"), driver=None))
    assert f.build == "win-vulkan" and f.speed_check and f.verdict == "warn" and any("test one spoken line" in w for w in f.warnings)


def test_not_enough_disk_is_refused_with_the_numbers():
    f = ve.assess(hw(free_disk_bytes=3 * 1024**3))
    assert f.verdict == "refuse" and "isn't enough room" in f.reasons[0] and "3.0 GB is free" in f.reasons[0]


def test_tight_graphics_or_ordinary_memory_only_warns_it_never_refuses():
    f = ve.assess(hw(free_vram_bytes=2 * 1024**3, free_ram_bytes=2 * 1024**3))
    assert f.verdict == "warn" and len(f.warnings) == 2 and "graphics memory" in f.warnings[0] and "system memory" in f.warnings[1]


def test_other_systems_get_a_plain_not_yet():
    f = ve.assess(hw(system="mac"))
    assert f.verdict == "refuse" and "Windows and Linux devices" in f.reasons[0]


# ---- Linux (2026-10-05) -------------------------------------------------------------------------------------------------------------
def lhw(**kw):
    return hw(**{**dict(system="linux", driver=575.0, glibc=(2, 39), cuda_missing=[]), **kw})


def test_linux_nvidia_with_the_cuda_libraries_gets_the_cuda_build():
    f = ve.assess(lhw())
    assert f.verdict == "ok" and f.build == "linux-cuda12.8" and not f.speed_check


def test_linux_nvidia_without_the_cuda_libraries_is_told_which_to_install():
    f = ve.assess(lhw(cuda_missing=["libcublas.so.12", "libnccl.so.2"]))
    assert f.verdict == "refuse" and f.build is None
    assert "libcublas.so.12, libnccl.so.2" in f.reasons[0] and "cuBLAS, cuFFT and NCCL" in f.reasons[0]


def test_linux_nvidia_when_the_libraries_cannot_be_checked_still_tries_cuda():
    assert ve.assess(lhw(cuda_missing=None)).build == "linux-cuda12.8"


def test_linux_other_cards_are_not_offered_until_vulkan_has_been_measured_on_one():
    amd = lhw(gpu=GpuInfo("AMD Radeon RX 7800", 16384, "amd"), driver=None)
    f = ve.assess(amd)
    assert f.verdict == "refuse" and "NVIDIA graphics cards" in f.reasons[0] and "AMD and Intel" in f.reasons[0]
    assert ve.LINUX_VULKAN_MEASURED is False


def test_when_vulkan_is_switched_on_linux_other_cards_get_it_with_the_speed_check(monkeypatch):
    monkeypatch.setattr(ve, "LINUX_VULKAN_MEASURED", True)
    f = ve.assess(lhw(gpu=GpuInfo("AMD Radeon RX 7800", 16384, "amd"), driver=None))
    assert f.build == "linux-vulkan" and f.speed_check and any("test one spoken line" in w for w in f.warnings)
    g = ve.assess(lhw(cuda_missing=["libcublas.so.12"]))
    assert g.build == "linux-vulkan" and any("libcublas.so.12" in w and "Vulkan" in w for w in g.warnings) and "cuda-libs" in g.flags


def test_linux_nvidia_driver_older_than_the_cuda_12_floor_is_refused_with_a_plain_reason():
    f = ve.assess(ve.Hardware(system="linux", gpu=GpuInfo("NVIDIA GeForce GTX 1080", 8192, "nvidia"), driver=510.0, glibc=(2, 39), cuda_missing=[],
                              free_vram_bytes=20 * 1024**3, free_ram_bytes=32 * 1024**3, free_disk_bytes=500 * 1024**3))
    assert f.verdict == "refuse" and "too old" in f.reasons[0] and "525.6" in f.reasons[0]


def test_a_linux_older_than_the_build_needs_is_refused_and_ubuntu_is_named():
    f = ve.assess(lhw(glibc=(2, 31)))
    assert f.verdict == "refuse" and "too old" in f.reasons[0] and "2.31" in f.reasons[0] and "2.35" in f.reasons[0] and "Ubuntu 24.04" in f.reasons[0]


def test_the_linux_pinned_files_are_the_publishers_and_the_unpacked_program_gets_marked_executable_later():
    cuda, vk = ve.BUILDS["linux-cuda12.8"], ve.BUILDS["linux-vulkan"]
    assert cuda.files[0].name == "audio-v0.9.0-bin-ubuntu-x64-cuda12.8-colab.tar.gz" and cuda.files[0].sha256 == "c9ed906f918246669c324f0d31f1b7dd80cbe003c35cf8a54932f333b57ca3f6"
    assert vk.files[0].name == "audio-v0.9.0-bin-ubuntu-x64-vulkan-portable.tar.gz" and vk.files[0].sha256 == "ce661a7b39add12e91f1c7cc590d55854a39ec0a2fd04c004b3fed29cb978398"
    assert cuda.system == vk.system == "linux" and cuda.backend == "cuda" and vk.backend == "vulkan" and vk.needs_speed_check and not cuda.needs_speed_check
    assert all(f.url.startswith("https://github.com/0xShug0/audio.cpp/releases/download/v0.9.0/") for b in (cuda, vk) for f in b.files)


def test_cuda_libraries_are_found_in_the_system_list_and_the_folder_the_program_itself_searches(tmp_path, monkeypatch):
    listing = chr(10).join(f"{chr(9)}{n} (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/{n}" for n in ("libcudart.so.12", "libcufft.so.11"))

    class R:
        returncode, stdout = 0, listing

    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path))
    (tmp_path / "libcublas.so.12").write_bytes(b"")
    assert ve.linux_cuda_missing(run=lambda *a, **k: R()) == ["libnccl.so.2"]


def test_cuda_libraries_unknown_when_there_is_no_way_to_look(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ldconfig")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.setattr(ve.Path, "is_dir", lambda self: False)
    assert ve.linux_cuda_missing(run=boom) is None


@pytest.mark.parametrize("tail,code,alive,words", [
    ("error while loading shared libraries: libcublas.so.12: cannot open shared object file", 127, 0.2, "libcublas.so.12"),
    ("ggml_vulkan: no devices found libvulkan.so.1", 1, 5.0, "Vulkan"),
    ("ggml_vulkan: No devices found. audiocpp_server failed: Vulkan backend requested but registry 'Vulkan' has no device 0", 1, 35.0, "Vulkan support"),
])
def test_linux_engine_deaths_are_explained_without_windows_words(tail, code, alive, words, monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", False)
    text = ve.explain_death(tail, code, alive)
    assert words in text and "Windows" not in text and "antivirus" not in text.lower()


def test_a_linux_engine_that_dies_at_once_never_says_the_phrase_the_voice_tab_turns_into_windows_advice(monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", False)
    text = ve.explain_death("", 1, 0.5)
    assert "right after it started" not in text and "driver" in text


def test_the_pinned_files_are_the_measured_ones():
    b = ve.BUILDS["win-cuda13.3"]
    assert [f.sha256 for f in b.files][0] == "46eb928af284babb3526fc5198da12a4fe4e4445812d60e90a04e82cb5a87786"
    assert ve.MODEL.size == 5_095_354_048 and ve.MODEL.sha256.startswith("79746822")
    assert all(f.url.startswith("https://github.com/0xShug0/audio.cpp/releases/download/v0.9.0/") for f in b.files)


# ---- install ------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_install_keeps_the_downloads_links_the_model_and_ends_ready(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        s = engine.status()
        assert s["state"] == "ready" and s["ready"] and s["installed"] and s["install"]["state"] == "done" and s["install"]["percent"] == 100
        kept = {f["name"] for f in s["downloads"]["files"]}
        assert kept == {"engine-fake.zip", "model-fake.gguf"} and s["downloads"]["totalBytes"] > 0
        model = engine.models_dir / "model-fake.gguf"
        assert model.is_file() and (model.stat().st_nlink > 1 or model.stat().st_size == (src / "model-fake.gguf").stat().st_size)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_clearing_the_kept_downloads_never_breaks_what_is_installed(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        freed = store.clear()
        assert freed > 0 and store.listing()["files"] == []
        assert engine.installed()
        await engine.restart()
        assert engine.status()["ready"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_a_second_install_uses_the_kept_files_and_needs_no_source(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        await engine.uninstall()
        assert not engine.installed() and store.listing()["files"]
        engine.start_install(FolderSource(tmp_path / "nowhere"))          # the source has nothing: the kept files must be enough
        job = await wait_job(engine)
        assert job.state == "done", job.error
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_a_bad_checksum_is_refused_and_nothing_is_kept(tmp_path):
    engine, src, store = make_engine(tmp_path)
    (src / "model-fake.gguf").write_bytes(b"tampered " * 1000)                 # the same size is not enough
    bad = (src / "model-fake.gguf")
    spec = engine.model
    object.__setattr__(spec, "size", bad.stat().st_size)                        # make the size match so only the checksum can catch it
    engine.start_install(FolderSource(src))
    job = await wait_job(engine)
    assert job.state == "error" and "safety check" in job.error
    assert "model-fake.gguf" not in {f["name"] for f in store.listing()["files"]} and not engine.installed()
    assert engine.status()["state"] == "not_installed"


@pytest.mark.asyncio
async def test_a_missing_file_says_which(tmp_path):
    engine, src, store = make_engine(tmp_path)
    (src / "engine-fake.zip").unlink()
    engine.start_install(FolderSource(src))
    job = await wait_job(engine)
    assert job.state == "error" and "engine-fake.zip" in job.error


@pytest.mark.asyncio
async def test_no_room_on_disk_refuses_before_any_download(tmp_path):
    engine, src, store = make_engine(tmp_path, hardware=lambda f: hw(free_disk_bytes=1024))
    with pytest.raises(ve.VoiceEngineError) as e:
        engine.start_install(FolderSource(src))
    assert e.value.status == 400 and "isn't enough room" in e.value.message
    assert not (tmp_path / "downloads").exists() or not list((tmp_path / "downloads").glob("*.gguf"))


@pytest.mark.asyncio
async def test_cancelling_keeps_the_partial_work_and_a_second_try_carries_on(tmp_path):
    engine, src, store = make_engine(tmp_path, model_bytes=b"m" * 40_000_000)

    class Slow(FolderSource):
        async def fetch(self, spec, dest, on_bytes):
            if spec.name.endswith(".gguf"):
                await asyncio.sleep(5)
            await super().fetch(spec, dest, on_bytes)

    engine.start_install(Slow(src))
    await asyncio.sleep(0.8)
    assert engine.cancel_install()
    for _ in range(100):
        if engine.job.state != "running":
            break
        await asyncio.sleep(0.05)
    assert engine.job.state == "cancelled" and engine.status()["state"] == "not_installed"
    assert store.has(next(iter(engine.builds.values())).files[0]), "the engine file finished before the cancel and is kept"
    engine.start_install(FolderSource(src))
    job = await wait_job(engine)
    assert job.state == "done", job.error
    await engine.close()


@pytest.mark.asyncio
async def test_progress_reports_bytes_done_and_total_for_a_download_in_flight(tmp_path):
    engine, src, store = make_engine(tmp_path, model_bytes=b"m" * 20_000_000)
    job = engine.start_install(FolderSource(src))
    assert job.total == engine.model.size + engine.builds["fake"].files[0].size
    j = await wait_job(engine)
    assert j.done >= j.total and j.to_json()["percent"] == 100
    await engine.close()


@pytest.mark.asyncio
async def test_a_second_install_while_one_runs_is_refused(tmp_path):
    engine, src, store = make_engine(tmp_path)
    engine.start_install(FolderSource(src))
    with pytest.raises(ve.VoiceEngineError) as e:
        engine.start_install(FolderSource(src))
    assert e.value.status == 409
    await wait_job(engine)
    await engine.close()


# ---- run ----------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_stop_and_restart(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        assert engine.status()["state"] == "ready"
        port1, pid1 = engine.port, engine.proc.pid
        await engine.stop()
        assert engine.status()["state"] == "stopped" and engine.proc is None
        await engine.start()
        assert engine.status()["ready"]
        await engine.restart()
        assert engine.status()["ready"] and engine.proc.pid != pid1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_ready_waits_for_a_real_warm_up_line_and_the_first_run_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LOAD_DELAY", "0.5")
    engine, src, store = make_engine(tmp_path)
    states = []

    async def watch():
        while engine.job is None or engine.job.state == "running":
            states.append((engine.state, engine.detail))
            await asyncio.sleep(0.05)
    engine.start_install(FolderSource(src))
    await asyncio.gather(watch(), wait_job(engine))
    assert ("getting_ready", "Getting ready for the first time…") in states or ("starting", "Loading the voice…") in states
    assert engine.status()["ready"] and engine._read_state()["first_run_done"] is True
    await engine.close()


@pytest.mark.asyncio
async def test_a_line_through_the_engine_reports_its_time_and_the_status_remembers_it(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        res = await engine.synthesize("Hello there.", ve.make_silence_wav())
        assert res.wav[:4] == b"RIFF" and res.audio_ms == 1000
        s = engine.status()
        assert s["lastLineMs"] and s["linesDone"] >= 2 and s["lastLineRtf"] == 0.1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_asking_a_stopped_engine_is_an_immediate_clean_error(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    await engine.stop()
    t = time.time()
    with pytest.raises(ve.VoiceEngineError) as e:
        await engine.synthesize("Hello.", None)
    assert e.value.status == 503 and "isn't running" in e.value.message and time.time() - t < 0.5
    await engine.close()


@pytest.mark.asyncio
async def test_an_engine_that_dies_at_once_says_so_with_the_antivirus_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", True)          # (the antivirus advice is the Windows sentence, whatever this test runs on)
    monkeypatch.setenv("FAKE_DIE_AT_ONCE", "1")
    engine, src, store = make_engine(tmp_path)
    engine.start_install(FolderSource(src))
    job = await wait_job(engine)
    s = engine.status()
    assert s["state"] == "died" and job.state == "error"
    for words in ("the engine said nothing", "antivirus", "Smart App Control", "Allow this folder", f"`{engine.engine_dir}`"):
        assert words in s["detail"], words
    assert not engine.status()["ready"]


@pytest.mark.asyncio
async def test_an_engine_that_prints_its_own_error_shows_that_cause_and_never_the_antivirus_line(tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", True)
    monkeypatch.setenv("FAKE_DIE_PRINTING", "audiocpp_server failed: The model file has the wrong shape")
    engine, src, store = make_engine(tmp_path)
    engine.start_install(FolderSource(src))
    await wait_job(engine)
    d = engine.status()["detail"]
    assert engine.status()["state"] == "died"
    assert "couldn't start with this setup" in d and "The model file has the wrong shape." in d
    assert "ntivirus" not in d and "Smart App Control" not in d


@pytest.mark.asyncio
async def test_an_engine_that_printed_something_unplain_points_to_the_log_not_the_antivirus(tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", True)
    monkeypatch.setenv("FAKE_DIE_PRINTING", "something odd happened at C:\\x\\y.dll")
    engine, src, store = make_engine(tmp_path)
    engine.start_install(FolderSource(src))
    await wait_job(engine)
    d = engine.status()["detail"]
    assert d == "The voice engine stopped as soon as it started. Open **Log** to see why." and "ntivirus" not in d


def test_explain_death_reads_the_engines_own_words_first(monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", True)
    own = "warning: legacy model spec\naudiocpp_server failed: Chatterbox supports VoiceCloning and VoiceConversion\n"
    d = ve.explain_death("", 1, 1.5, "Chatterbox voice", own, "C:\\engine")
    assert d == "Chatterbox voice couldn't start with this setup. The engine said: Chatterbox supports VoiceCloning and VoiceConversion."
    assert ve.explain_death("", 1, 1.5, "X", "audiocpp_server failed: see C:\\a\\b", "C:\\engine") == "X couldn't start with this setup. Open **Log** to see why."
    assert "ntivirus" not in ve.explain_death("", 1, 1.5, "X", "loading...\n", "C:\\engine")
    assert "`C:\\engine`" in ve.explain_death("", 1, 1.5, "X", "", "C:\\engine")


def test_explain_death_gives_a_short_plain_cause_for_known_engine_errors():
    own = "audiocpp_server failed: unsupported task: clone (expected vad, asr, diar, sep, gen, tts, clon, vc, s2s, align, vdes, spk, svc, or midi)\n"
    assert ve.explain_death("", 1, 1.5, "Chatterbox voice", own, "C:\\e") == "Chatterbox voice couldn't start: the voice engine doesn't know this setup's task name. Open **Log** to see why."
    assert ve.explain_death("", 1, 1.5, "X", "audiocpp_server failed: reference clip not found", "C:\\e") == "X couldn't start: its reference clip is missing. Open **Log** to see why."
    assert ve.explain_death("", 1, 1.5, "X", "audiocpp_server failed: model file not found", "C:\\e") == "X couldn't start: a file it needs is missing. Open **Log** to see why."


def test_the_stand_in_engine_refuses_task_names_the_real_one_refuses(tmp_path):
    import json, subprocess, sys
    fake = Path(__file__).parent / "fake_audiocpp_server.py"
    for task, family, ok in (("clone", "chatterbox", False), ("tts", "chatterbox", False), ("bogus", "higgs_audio_tts", False)):
        cfg = tmp_path / f"{task}-{family}.json"
        cfg.write_text(json.dumps({"port": 1, "models": [{"id": "m", "family": family, "task": task}]}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(fake), str(cfg)], capture_output=True, text=True, timeout=30)
        assert r.returncode == 1
        if task != "tts":
            assert f"unsupported task: {task} (expected vad, asr, diar, sep, gen, tts, clon, vc, s2s, align, vdes, spk, svc, or midi)" in r.stdout


@pytest.mark.asyncio
async def test_an_engine_that_dies_later_is_noticed_by_the_status_and_the_next_line(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        ve.kill_tree_sync(engine.proc.pid)
        for _ in range(60):
            if engine.proc.poll() is not None:
                break
            await asyncio.sleep(0.1)
        assert engine.status()["state"] == "died"
        with pytest.raises(ve.VoiceEngineError):
            await engine.synthesize("Hi.", None)
    finally:
        await engine.close()


def test_the_death_explanations_are_plain(monkeypatch):
    monkeypatch.setattr(ve, "IS_WINDOWS", True)
    assert "graphics memory" in ve.explain_death("CUDA error: out of memory", 1, 60)
    assert "processor" in ve.explain_death("", 0xC000001D, 1)
    assert "stopped as soon as it started" in ve.explain_death("", 1, 3)
    assert "stopped unexpectedly" in ve.explain_death("", 7, 300)


@pytest.mark.asyncio
async def test_a_warm_up_failure_is_reported_not_called_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WARMUP_FAIL", "1")
    engine, src, store = make_engine(tmp_path)
    engine.start_install(FolderSource(src))
    job = await wait_job(engine)
    assert job.state == "error" and engine.status()["state"] == "error" and "started, but its test line failed" in engine.status()["detail"]
    await engine.close()


@pytest.mark.asyncio
async def test_a_vulkan_card_that_is_too_slow_is_told_so_after_a_timed_line(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_RTF", "0.9")
    engine, src, store = await installed_engine(tmp_path, backend="vulkan")
    try:
        s = engine.status()
        assert s["state"] == "too_slow" and s["speedCheck"] == {"rtf": 0.9, "limit": ve.SPEED_LIMIT_RTF, "ok": False}
        assert "too slow" in s["detail"] and not s["ready"]
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_a_vulkan_card_that_is_fast_enough_stays_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_RTF", "0.15")
    engine, src, store = await installed_engine(tmp_path, backend="vulkan")
    try:
        assert engine.status()["ready"] and engine.speed_check["ok"] is True
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_nvidia_is_not_speed_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_RTF", "0.9")
    engine, src, store = await installed_engine(tmp_path)
    try:
        assert engine.status()["ready"] and engine.speed_check is None
    finally:
        await engine.close()


# ---- the engine's own refusal of a stale line ----------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_engines_busy_refusal_becomes_engine_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "1.0")
    engine, src, store = await installed_engine(tmp_path)
    try:
        first = asyncio.ensure_future(engine.synthesize("one", None))
        await asyncio.sleep(0.2)
        with pytest.raises(ve.EngineBusy):
            await engine.synthesize("two", None, busy_timeout_ms=300)
        await first
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_a_clip_over_5_mib_never_reaches_the_engine(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        with pytest.raises(ve.VoiceEngineError) as e:
            await engine.synthesize("x", b"0" * (ve.REFERENCE_MAX_BYTES + 1))
        assert e.value.status == 400
    finally:
        await engine.close()


# ---- uninstall, log, status ------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_uninstall_frees_the_install_keeps_the_players_clips_and_the_kept_downloads(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    (engine.clips_dir / "mine.wav").write_bytes(b"clip")
    freed = await engine.uninstall()
    assert not engine.installed() and engine.status()["state"] == "not_installed" and freed > 0
    assert not engine.engine_dir.exists() and not engine.models_dir.exists()
    assert (engine.clips_dir / "mine.wav").read_bytes() == b"clip" and store.listing()["files"]
    await engine.uninstall(keep_clips=False)
    assert not engine.clips_dir.exists()


@pytest.mark.asyncio
async def test_the_log_is_masked(tmp_path):
    engine, src, store = make_engine(tmp_path)
    engine.redact = lambda s: s.replace("sk-secret", "***")
    engine.log("key sk-secret used")
    t = engine.log_tail()
    assert "***" in t and "sk-secret" not in t


@pytest.mark.asyncio
async def test_status_of_a_fresh_install_is_plain(tmp_path):
    engine, src, store = make_engine(tmp_path)
    s = engine.status()
    assert s["state"] == "not_installed" and s["label"] == "Not installed" and s["install"] is None and s["linesWaiting"] == 0
    engine.waiting_fn = lambda: 3
    engine.state = "ready"
    engine.proc = type("P", (), {"poll": lambda self: None})()
    assert engine.status()["label"] == "Busy (3 lines waiting)"
    engine.proc = None
    engine.state = "not_installed"


@pytest.mark.asyncio
async def test_the_kept_downloads_index_notices_a_changed_file(tmp_path):
    engine, src, store = await installed_engine(tmp_path)
    try:
        spec = engine.builds["fake"].files[0]
        assert store.has(spec)
        (store.folder / spec.name).write_bytes(b"changed")
        assert not store.has(spec)
    finally:
        await engine.close()


def test_kept_downloads_count_as_already_there_for_the_voice_disk_check():
    full = ve.assess(hw())
    kept = ve.assess(hw(), kept_fn=lambda specs: sum(s.size for s in specs))
    assert full.need_disk_bytes > 0 and kept.need_disk_bytes == full.need_disk_bytes - kept_total(full)


def kept_total(fit):
    b = ve.BUILDS[fit.build]
    vm = ve.VOICE_MODELS[ve.DEFAULT_VOICE_MODEL]
    return vm.file.size + (vm.companion.size if vm.companion else 0) + sum(f.size for f in b.files)
