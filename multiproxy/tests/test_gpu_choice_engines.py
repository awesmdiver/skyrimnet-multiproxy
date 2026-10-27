"""Routing step 8: each local AI starts with its remembered graphics card (local models, the decision model, Higgs voice). Fake engines stand in for the real ones; a recorder
in front of the process starter shows exactly which arguments and environment each start used. No choice stored = started exactly as before."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

import decision_engine as de
import gpu_choice as gc
import local_models as lm
import voice_engine as ve
from gpu_choice import Card
from tests import decision_helpers as dh
from tests import voice_helpers as vh
from tests.test_local_models import _install_fake_engine, _install_fake_model, _tiny_catalog, FAKE_SERVER, log
from voice_downloads import FolderSource

UUID_A = "GPU-50863396-8256-dd23-f5f0-7cc79cc1ba4e"
UUID_B = "GPU-11111111-2222-3333-4444-555555555555"
NV5090 = Card(UUID_A, "NVIDIA GeForce RTX 5090", 32607, "nvidia")
NV3060 = Card(UUID_B, "NVIDIA GeForce RTX 3060", 12288, "nvidia")
INTEL = Card("pnp:INTEL", "Intel(R) Graphics", 128, "intel")
CARDS = [NV5090, NV3060, INTEL]
VK_LLAMA = [("Vulkan0", "NVIDIA GeForce RTX 5090", 32187), ("Vulkan1", "NVIDIA GeForce RTX 3060", 12000), ("Vulkan2", "Intel(R) Graphics", 37037)]
CHOOSE_3060 = gc.choice_of(NV3060)
GONE = {"id": "GPU-gone", "name": "NVIDIA GeForce GTX 1080", "memoryGb": 8.0}


class Recorder:
    """Stands in front of the real process starters: notes the arguments and environment, then runs the fake engine (without the `--device X` the fakes don't know)."""
    def __init__(self):
        self.starts = []

    @staticmethod
    def _clean(cmd):
        out, skip = [], False
        for a in cmd:
            if skip:
                skip = False
            elif a == "--device":
                skip = True
            else:
                out.append(a)
        return out

    def install(self, monkeypatch, module):
        real_async, real_popen = module.hidden_process.create_subprocess_exec, module.hidden_process.popen
        rec = self

        async def fake_async(*cmd, **kw):
            rec.starts.append((list(cmd), kw))
            return await real_async(*rec._clean(cmd), **kw)

        def fake_popen(cmd, **kw):
            rec.starts.append((list(cmd), kw))
            return real_popen(rec._clean(cmd), **kw)
        monkeypatch.setattr(module.hidden_process, "create_subprocess_exec", fake_async)
        monkeypatch.setattr(module.hidden_process, "popen", fake_popen)
        return self

    @property
    def cmd(self):
        return self.starts[-1][0]

    @property
    def env(self):
        return self.starts[-1][1].get("env")


# ---------------------------------------------------------------------------------------------------------------------------------
# Local models
# ---------------------------------------------------------------------------------------------------------------------------------
@pytest_asyncio.fixture
async def make_svc(tmp_path, monkeypatch):
    catalog = _tiny_catalog()
    monkeypatch.setattr(lm, "CATALOG", catalog)
    monkeypatch.setattr(lm, "CATALOG_BY_ID", {m["id"]: m for m in catalog})
    monkeypatch.setattr(lm, "HEALTH_POLL_SECONDS", 0.05)
    rec = Recorder().install(monkeypatch, lm)
    made = []

    def make(choice=None, kind="cuda-13.4", cards=CARDS, devices=VK_LLAMA, calls=None):
        store: dict = {}

        def builder(exe, model, port, gpu_engine):
            return [sys.executable, FAKE_SERVER, "--port", str(port), "--mode", "ok"]

        def cards_fn():
            if calls is not None:
                calls.append("cards")
            return cards
        s = lm.LocalModels(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log, str(tmp_path / "pids.json"), command_builder=builder,
                           gpu_detector=lambda: lm.GpuInfo("Test GPU", 16384, "nvidia", cuda=(13, 4)),
                           gpu_choice_fn=lambda model_id: choice, cards_fn=cards_fn, devices_fn=lambda exe: devices)
        _install_fake_engine(s, tmp_path, kind)
        _install_fake_model(s)
        made.append(s)
        return s
    yield make, rec
    for s in made:
        await s.stop_all()


async def _run(svc):
    s = await svc.start("tiny-model")
    await asyncio.wait_for(s.ready.wait(), 20)
    assert s.state == "running", s.message
    return s


@pytest.mark.asyncio
class TestLocalModelsStart:
    async def test_no_choice_starts_exactly_as_before_and_reads_no_cards(self, make_svc):
        make, rec = make_svc
        calls: list = []
        svc = make(calls=calls)
        await _run(svc)
        assert "--device" not in rec.cmd
        env = rec.starts[-1][1].get("env")          # the player's own environment, minus every LLAMA_ARG_* variable (local_engine_flags.clean_environment)
        assert env is not None and not [k for k in env if k.upper().startswith("LLAMA_ARG_")]
        if os.name != "nt":
            assert "CUDA_VISIBLE_DEVICES" not in rec.env
        assert calls == [] and svc.gpu_note == "" and svc.gpu_cards["tiny-model"] is None

    async def test_a_cuda_choice_sets_the_card_in_the_environment_and_keeps_the_rest_of_it(self, make_svc, monkeypatch):
        make, rec = make_svc
        monkeypatch.setenv("KEEP_ME", "yes")
        svc = make(CHOOSE_3060)
        await _run(svc)
        assert rec.env["CUDA_VISIBLE_DEVICES"] == UUID_B and rec.env["KEEP_ME"] == "yes" and "--device" not in rec.cmd
        assert svc.gpu_cards["tiny-model"] == "NVIDIA GeForce RTX 3060" and svc.gpu_note == ""

    async def test_a_vulkan_choice_adds_device_to_the_command(self, make_svc):
        make, rec = make_svc
        svc = make(CHOOSE_3060, kind="vulkan")
        await _run(svc)
        assert rec.cmd[-2:] == ["--device", "Vulkan1"] and (rec.env is None or "CUDA_VISIBLE_DEVICES" not in rec.env)

    async def test_a_card_that_is_gone_starts_automatic_and_remembers_the_plain_note(self, make_svc):
        make, rec = make_svc
        svc = make(GONE)
        s = await _run(svc)
        assert s.state == "running" and "CUDA_VISIBLE_DEVICES" not in (rec.env or {}) and "--device" not in rec.cmd
        assert svc.gpu_note == "The graphics card you chose (NVIDIA GeForce GTX 1080) isn't in this device anymore, so Automatic is used."
        assert svc.gpu_notes["tiny-model"] == svc.gpu_note
        snap = await svc.snapshot()
        assert snap["gpuNote"] == svc.gpu_note and snap["installed"][0]["gpuNote"] == svc.gpu_note and snap["installed"][0]["gpuCard"] is None

    async def test_an_engine_that_cannot_use_the_card_starts_automatic_with_a_note(self, make_svc):
        make, rec = make_svc
        svc = make(CHOOSE_3060, kind="cpu")
        await _run(svc)
        assert "--device" not in rec.cmd and "can't use NVIDIA GeForce RTX 3060" in svc.gpu_note

    async def test_a_later_start_with_no_choice_clears_the_note(self, make_svc):
        make, rec = make_svc
        svc = make(GONE)
        await _run(svc)
        assert svc.gpu_note
        await svc.stop("tiny-model")
        svc._gpu_choice_fn = lambda model_id: None
        await _run(svc)
        assert svc.gpu_note == ""

    async def test_the_snapshot_carries_the_card_name_of_a_followed_choice(self, make_svc):
        make, rec = make_svc
        svc = make(CHOOSE_3060)
        await _run(svc)
        snap = await svc.snapshot()
        assert snap["installed"][0]["gpuCard"] == "NVIDIA GeForce RTX 3060" and snap["gpuNote"] == ""

    async def test_the_fit_check_uses_the_chosen_cards_memory_and_the_usual_card_otherwise(self, make_svc, monkeypatch):
        make, rec = make_svc
        seen = []
        real = lm.fit_check
        monkeypatch.setattr(lm, "fit_check", lambda total, gpu, *a, **k: seen.append(gpu) or real(total, gpu, *a, **k))
        svc = make(CHOOSE_3060)
        await svc.snapshot()
        assert seen[-1].name == "NVIDIA GeForce RTX 3060" and seen[-1].memory_mb == 12288
        svc._gpu_choice_fn = lambda model_id: None
        await svc.snapshot()
        assert seen[-1].name == "Test GPU" and seen[-1].memory_mb == 16384
        svc._gpu_choice_fn = lambda model_id: GONE                           # a chosen card that is gone: the usual card
        await svc.snapshot()
        assert seen[-1].name == "Test GPU"

    async def test_gpu_for_asks_the_choice_function_with_the_model_id(self, make_svc):
        make, rec = make_svc
        asked = []
        svc = make()
        svc._gpu_choice_fn = lambda model_id: asked.append(model_id) or CHOOSE_3060
        g = await svc.gpu_for("tiny-model")
        assert set(asked) == {"tiny-model"} and g.name == "NVIDIA GeForce RTX 3060"

    @pytest.mark.skipif(os.name == "nt", reason="the Linux branch of the start code only runs on Linux")
    async def test_linux_keeps_its_library_path_and_adds_the_card(self, make_svc):
        make, rec = make_svc
        svc = make(CHOOSE_3060)
        await _run(svc)
        assert rec.env["CUDA_VISIBLE_DEVICES"] == UUID_B and "LD_LIBRARY_PATH" in rec.env


# ---------------------------------------------------------------------------------------------------------------------------------
# The decision model
# ---------------------------------------------------------------------------------------------------------------------------------
async def _decision(tmp_path, monkeypatch, choice=None, kind="cuda-13.4", cards=CARDS, devices=VK_LLAMA, settings=None, hw=None, calls=None):
    rec = Recorder().install(monkeypatch, de)
    monkeypatch.setattr(gc, "apply_card", lambda hw, card, run=None: _fake_apply(hw, card))     # no real nvidia-smi in a test

    def cards_fn():
        if calls is not None:
            calls.append("cards")
        return cards
    engine, st, src = await dh.installed_engine(tmp_path, kind=kind, settings=settings, hw=hw, gpu_choice_fn=lambda: choice, cards_fn=cards_fn, devices_fn=lambda exe: devices)
    return engine, rec


@pytest.mark.asyncio
class TestDecisionEngineStart:
    async def test_no_choice_is_started_exactly_as_before_and_reads_no_cards(self, tmp_path, monkeypatch):
        calls: list = []
        engine, rec = await _decision(tmp_path, monkeypatch, calls=calls)
        try:
            await engine.start()
            assert engine.state == "ready" and "--device" not in rec.cmd
            env = rec.starts[-1][1].get("env")
            assert env is not None and not [k for k in env if k.upper().startswith("LLAMA_ARG_")]
            assert calls == [] and engine.gpu_note == "" and engine.status()["gpuNote"] == "" and engine.status()["gpuCard"] is None
        finally:
            await engine.stop()

    async def test_a_cuda_choice_sets_the_card_and_the_status_names_it(self, tmp_path, monkeypatch):
        engine, rec = await _decision(tmp_path, monkeypatch, CHOOSE_3060)
        try:
            await engine.start()
            assert engine.state == "ready" and rec.env["CUDA_VISIBLE_DEVICES"] == UUID_B
            assert engine.status()["gpuCard"] == "NVIDIA GeForce RTX 3060" and engine.status()["gpuNote"] == ""
        finally:
            await engine.stop()

    async def test_a_vulkan_choice_adds_the_device_argument(self, tmp_path, monkeypatch):
        engine, rec = await _decision(tmp_path, monkeypatch, CHOOSE_3060, kind="vulkan")
        try:
            await engine.start()
            assert rec.cmd[-2:] == ["--device", "Vulkan1"]
        finally:
            await engine.stop()

    async def test_a_gone_card_starts_automatic_with_the_plain_note_in_the_status(self, tmp_path, monkeypatch):
        engine, rec = await _decision(tmp_path, monkeypatch, GONE)
        try:
            await engine.start()
            assert engine.state == "ready" and "CUDA_VISIBLE_DEVICES" not in (rec.env or {})
            assert engine.status()["gpuNote"].startswith("The graphics card you chose (NVIDIA GeForce GTX 1080) isn't in this device anymore")
            assert engine.status()["gpuCard"] is None
        finally:
            await engine.stop()

    async def test_running_on_the_processor_leaves_the_choice_out_of_the_start(self, tmp_path, monkeypatch):
        engine, rec = await _decision(tmp_path, monkeypatch, CHOOSE_3060, settings={"run_on": "cpu"})
        try:
            await engine.start()
            assert engine.run_on == "cpu" and "CUDA_VISIBLE_DEVICES" not in (rec.env or {}) and "--device" not in rec.cmd and engine.gpu_note == ""
        finally:
            await engine.stop()

    async def test_the_fit_check_talks_about_the_chosen_card(self, tmp_path, monkeypatch):
        engine, rec = await _decision(tmp_path, monkeypatch, CHOOSE_3060, hw=dh.hardware(free_vram_gb=20.0))
        fit = engine.fit_check()
        assert fit["graphics"]["name"] == "NVIDIA GeForce RTX 3060" and fit["graphics"]["totalMb"] == 12288 and fit["graphics"]["freeMb"] == 9000

    async def test_the_fit_check_with_no_choice_is_the_usual_card(self, tmp_path, monkeypatch):
        engine, rec = await _decision(tmp_path, monkeypatch)
        assert engine.fit_check()["graphics"]["name"] == "NVIDIA GeForce RTX 5090"


def _fake_apply(hw, card):
    """What apply_card does, without asking nvidia-smi."""
    hw.gpu = lm.GpuInfo(card.name, card.memory_mb, card.vendor)
    hw.free_vram_bytes = 9000 * 1024 * 1024
    return hw


# ---------------------------------------------------------------------------------------------------------------------------------
# Higgs voice
# ---------------------------------------------------------------------------------------------------------------------------------
VK_VOICE = [(0, "NVIDIA GeForce RTX 5090"), (1, "NVIDIA GeForce RTX 3060"), (2, "Intel(R) Graphics")]


async def _voice(tmp_path, monkeypatch, choice=None, backend="cuda", cards=CARDS, devices=VK_VOICE, calls=None):
    rec = Recorder().install(monkeypatch, ve)
    engine, src, store = vh.make_engine(tmp_path, backend=backend)

    def cards_fn():
        if calls is not None:
            calls.append("cards")
        return cards
    engine._gpu_choice_fn = lambda: choice
    engine._cards_fn = cards_fn
    engine._devices_fn = lambda exe, kind: devices
    engine.start_install(FolderSource(src))
    t = 0
    while engine.job and engine.job.state == "running" and t < 600:
        await asyncio.sleep(0.05)
        t += 1
    assert engine.job.state == "done", engine.job.error
    return engine, rec


def _config(engine):
    return json.loads((engine.folder / "server.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
class TestVoiceStart:
    async def test_no_choice_is_started_exactly_as_before(self, tmp_path, monkeypatch):
        calls: list = []
        engine, rec = await _voice(tmp_path, monkeypatch, calls=calls)
        try:
            await engine.start()
            assert engine.state == "ready" and rec.env is None and _config(engine)["device"] == 0
            assert calls == [] and engine.status()["gpuNote"] == "" and engine.status()["gpuCard"] is None
        finally:
            await engine.stop()

    async def test_the_cuda_build_gets_the_card_in_the_environment_and_keeps_device_zero(self, tmp_path, monkeypatch):
        engine, rec = await _voice(tmp_path, monkeypatch, CHOOSE_3060)
        try:
            await engine.start()
            assert rec.env["CUDA_VISIBLE_DEVICES"] == UUID_B and _config(engine)["device"] == 0
            assert engine.status()["gpuCard"] == "NVIDIA GeForce RTX 3060"
        finally:
            await engine.stop()

    async def test_the_vulkan_build_gets_the_engines_own_device_number_found_by_name(self, tmp_path, monkeypatch):
        engine, rec = await _voice(tmp_path, monkeypatch, CHOOSE_3060, backend="vulkan")
        try:
            await engine.start()
            assert _config(engine)["device"] == 1 and _config(engine)["backend"] == "vulkan"
            assert rec.env is None and engine.status()["gpuCard"] == "NVIDIA GeForce RTX 3060" and engine.status()["gpuNote"] == ""
        finally:
            await engine.stop()

    async def test_the_vulkan_build_stays_automatic_when_the_engine_does_not_list_the_card(self, tmp_path, monkeypatch):
        engine, rec = await _voice(tmp_path, monkeypatch, CHOOSE_3060, backend="vulkan", devices=[VK_VOICE[0]])
        try:
            await engine.start()
            assert _config(engine)["device"] == 0 and engine.status()["gpuCard"] is None
            assert engine.status()["gpuNote"] == "The software didn't list NVIDIA GeForce RTX 3060, so Automatic is used."
        finally:
            await engine.stop()

    async def test_two_cards_with_one_name_on_vulkan_stay_automatic_with_a_plain_note(self, tmp_path, monkeypatch):
        twin = Card("pnp:TWIN", "AMD Radeon RX 7900 XTX", 24576, "amd")
        twin2 = Card("pnp:TWIN2", "AMD Radeon RX 7900 XTX", 24576, "amd")
        devs = [(0, "AMD Radeon RX 7900 XTX"), (1, "AMD Radeon RX 7900 XTX")]
        engine, rec = await _voice(tmp_path, monkeypatch, gc.choice_of(twin), backend="vulkan", cards=[twin, twin2], devices=devs)
        try:
            await engine.start()
            assert _config(engine)["device"] == 0 and engine.status()["gpuNote"] == ve.VOICE_SAME_NAME.format(name="AMD Radeon RX 7900 XTX")
        finally:
            await engine.stop()

    async def test_a_gone_card_starts_automatic_with_the_plain_note(self, tmp_path, monkeypatch):
        engine, rec = await _voice(tmp_path, monkeypatch, GONE)
        try:
            await engine.start()
            assert engine.state == "ready" and rec.env is None and engine.status()["gpuNote"].startswith("The graphics card you chose (NVIDIA GeForce GTX 1080)")
        finally:
            await engine.stop()

    async def test_the_fit_check_talks_about_the_chosen_card(self, tmp_path, monkeypatch):
        engine, rec = await _voice(tmp_path, monkeypatch, CHOOSE_3060)
        monkeypatch.setattr(gc, "apply_card", lambda hw, card, run=None: _fake_apply(hw, card))
        assert engine.hardware().gpu.name == "NVIDIA GeForce RTX 3060" and engine.hardware().free_vram_bytes == 9000 * 1024 * 1024


def test_voice_device_list_is_read_for_its_own_backend_only():
    text = ('ggml_vulkan: Found 2 Vulkan devices:\navailable_devices=3\nVulkan:0 "NVIDIA GeForce RTX 5090" [GPU]\nVulkan:1 "Intel(R) Graphics" [IGPU]\n'
            'CPU:0 "Intel(R) Core(TM) Ultra 9 285K" [CPU]\nselect with: --backend <cuda|hip|vulkan|metal|cpu> --device <index>\n')
    assert ve.parse_voice_devices(text, "vulkan") == [(0, "NVIDIA GeForce RTX 5090"), (1, "Intel(R) Graphics")]
    assert ve.parse_voice_devices(text, "cuda") == [] and ve.parse_voice_devices("", "vulkan") == []


def test_a_processor_only_voice_build_ignores_the_choice():
    plan, device = ve.voice_plan(CHOOSE_3060, CARDS, "cpu", "x")
    assert plan.automatic and plan.note == "" and device == 0
