"""Tests for local models, part 1 (local_models.py): graphics card detection, engine choice, the
fit check, resumable/verified downloads, and starting/stopping llama-server (with a fake one).

No real llama-server, graphics card or internet is used here -- the real thing was tried by hand on
the dev device (see TECHNICAL.md, "Local models, part 1").
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web

import local_models as lm

FAKE_SERVER = str(Path(__file__).parent / "fixtures" / "fake_llama_server.py")
log = logging.getLogger("test-local-models")


# ------------------------------------------------------------------------------------------
# Graphics card detection
# ------------------------------------------------------------------------------------------
NVSMI_PLAIN_NEW = "| NVIDIA-SMI 617.14    KMD Version: 617.14    CUDA UMD Version: 13.4 |"
NVSMI_PLAIN_OLD = "| NVIDIA-SMI 551.61    Driver Version: 551.61    CUDA Version: 12.4 |"


class TestParseNvidiaSmi:
    def test_one_card(self):
        cards = lm.parse_nvidia_smi("NVIDIA GeForce RTX 5090, 32607\n", NVSMI_PLAIN_NEW)
        assert len(cards) == 1
        c = cards[0]
        assert (c.name, c.memory_mb, c.vendor, c.cuda, c.source) == ("NVIDIA GeForce RTX 5090", 32607, "nvidia", (13, 4), "nvidia-smi")

    def test_old_driver_header_format(self):
        assert lm.parse_nvidia_smi("NVIDIA GeForce RTX 3060, 12288", NVSMI_PLAIN_OLD)[0].cuda == (12, 4)

    def test_two_cards_and_a_blank_line(self):
        cards = lm.parse_nvidia_smi("NVIDIA GeForce RTX 4090, 24564\n\nNVIDIA GeForce GTX 1050, 2048\n")
        assert [c.memory_mb for c in cards] == [24564, 2048]
        assert cards[0].cuda is None

    def test_a_name_that_contains_a_comma(self):
        assert lm.parse_nvidia_smi("NVIDIA RTX, Something, 8192")[0].name == "NVIDIA RTX, Something"

    def test_unreadable_memory_keeps_the_card_with_no_memory(self):
        c = lm.parse_nvidia_smi("NVIDIA GeForce RTX 5090, [N/A]")[0]
        assert c.memory_mb is None

    def test_garbage_gives_no_cards(self):
        assert lm.parse_nvidia_smi("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.") == []
        assert lm.parse_nvidia_smi("") == []


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(lm, "_platform", lambda: "win32")


@pytest.fixture
def as_linux(monkeypatch):
    monkeypatch.setattr(lm, "_platform", lambda: "linux")
    monkeypatch.setattr(lm.platform, "machine", lambda: "x86_64")


class TestDetectGpu:
    def _run(self, query_out, plain_out=NVSMI_PLAIN_NEW, rc=0):
        def run(cmd, **kw):
            if "--query-gpu=name,memory.total" in " ".join(cmd):
                return SimpleNamespace(returncode=rc, stdout=query_out)
            return SimpleNamespace(returncode=0, stdout=plain_out)
        return run

    def test_uses_nvidia_smi_when_it_answers(self):
        g = lm.detect_gpu(self._run("NVIDIA GeForce RTX 5090, 32607"))
        assert g.memory_mb == 32607 and g.cuda == (13, 4)

    def test_picks_the_card_with_the_most_memory(self):
        g = lm.detect_gpu(self._run("NVIDIA GeForce GTX 1050, 2048\nNVIDIA GeForce RTX 4090, 24564"))
        assert g.name.endswith("4090")

    def test_falls_back_to_windows_registry_when_nvidia_smi_is_missing(self, monkeypatch, as_windows):
        def missing(cmd, **kw):
            raise FileNotFoundError("nvidia-smi")
        monkeypatch.setattr(lm, "_registry_gpus", lambda: [
            lm.GpuInfo("Intel(R) UHD Graphics", 128, "intel", source="registry"),
            lm.GpuInfo("AMD Radeon RX 7900 XTX", 24560, "amd", source="registry"),
        ])
        g = lm.detect_gpu(missing)
        assert (g.name, g.memory_mb, g.vendor, g.source) == ("AMD Radeon RX 7900 XTX", 24560, "amd", "registry")

    def test_nothing_anywhere_gives_none(self, monkeypatch, as_windows):
        def missing(cmd, **kw):
            raise FileNotFoundError
        monkeypatch.setattr(lm, "_registry_gpus", lambda: [])
        assert lm.detect_gpu(missing) is None

    def test_nvidia_smi_failing_with_an_error_code_falls_back(self, monkeypatch, as_windows):
        monkeypatch.setattr(lm, "_registry_gpus", lambda: [lm.GpuInfo("NVIDIA GeForce RTX 3060", 12288, "nvidia", source="registry")])
        g = lm.detect_gpu(self._run("", rc=9))
        assert g.memory_mb == 12288 and g.source == "registry"


def test_vendor_of():
    assert lm.vendor_of("NVIDIA GeForce RTX 5090") == "nvidia"
    assert lm.vendor_of("AMD Radeon RX 7900") == "amd"
    assert lm.vendor_of("Intel(R) Arc(TM) A770") == "intel"
    assert lm.vendor_of("Some Mystery Adapter") == "other"


# ------------------------------------------------------------------------------------------
# Which engine build
# ------------------------------------------------------------------------------------------
def _kinds(options):
    return [o["kind"] for o in options]


@pytest.mark.usefixtures("as_windows")
class TestEngineOptions:
    def test_new_nvidia_driver_recommends_cuda_13(self):
        opts = lm.engine_options(lm.GpuInfo("NVIDIA RTX 5090", 32607, "nvidia", cuda=(13, 4)))
        assert _kinds(opts) == ["cuda-13.4", "vulkan", "cpu"]
        assert opts[0]["recommended"] and not opts[1]["recommended"]

    def test_older_nvidia_driver_gets_cuda_12(self):
        opts = lm.engine_options(lm.GpuInfo("NVIDIA RTX 3060", 12288, "nvidia", cuda=(12, 6)))
        assert _kinds(opts)[0] == "cuda-12.4" and opts[0]["recommended"]

    def test_nvidia_driver_too_old_for_any_cuda_build_gets_vulkan(self):
        opts = lm.engine_options(lm.GpuInfo("NVIDIA GTX 970", 4096, "nvidia", cuda=(11, 8)))
        assert _kinds(opts) == ["vulkan", "cpu"]
        assert opts[0]["recommended"]

    def test_nvidia_with_unknown_cuda_version_gets_vulkan(self):
        opts = lm.engine_options(lm.GpuInfo("NVIDIA RTX", 8192, "nvidia", cuda=None))
        assert _kinds(opts) == ["vulkan", "cpu"] and opts[0]["recommended"]

    def test_amd_and_intel_get_vulkan(self):
        for vendor in ("amd", "intel"):
            opts = lm.engine_options(lm.GpuInfo("x", 8192, vendor))
            assert opts[0]["kind"] == "vulkan" and opts[0]["recommended"]

    def test_no_graphics_card_recommends_cpu(self):
        opts = lm.engine_options(None)
        assert _kinds(opts) == ["cpu", "vulkan"] and opts[0]["recommended"]

    def test_sizes_are_the_sum_of_the_files(self):
        opts = {o["kind"]: o for o in lm.engine_options(lm.GpuInfo("NVIDIA", 8192, "nvidia", cuda=(13, 4)))}
        assert opts["cuda-13.4"]["sizeBytes"] == 152832067 + 423535356
        assert opts["vulkan"]["sizeBytes"] == 33262554


@pytest.mark.usefixtures("as_windows")
class TestPinnedBuild:
    def test_every_engine_file_is_named_for_the_pinned_build_and_has_a_real_checksum(self):
        for kind, files in lm.ENGINE_FILES.items():
            for name, size, sha in files:
                assert size > 1_000_000
                assert len(sha) == 64 and int(sha, 16) >= 0
                if name.startswith("llama-"):
                    assert lm.LLAMA_CPP_BUILD in name

    def test_download_url_points_at_the_pinned_release(self):
        url = lm.engine_url(lm.LLAMA_CPP_BUILD, "llama-b11361-bin-win-vulkan-x64.zip")
        assert lm.LLAMA_CPP_BUILD == "b11361"
        assert url == "https://github.com/ggml-org/llama.cpp/releases/download/b11361/llama-b11361-bin-win-vulkan-x64.zip"

    def test_nvidia_builds_come_with_their_runtime_zip(self):
        for kind in ("cuda-13.4", "cuda-12.4"):
            names = [n for n, _, _ in lm.ENGINE_FILES[kind]]
            assert any(n.startswith("cudart-llama-bin-win-cuda-") for n in names)


# ------------------------------------------------------------------------------------------
# The model list and the fit check
# ------------------------------------------------------------------------------------------
class TestCatalog:
    def test_the_three_gemma_4_models_are_there_with_vision_files(self):
        assert [m["id"] for m in lm.CATALOG] == ["gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-31b"]
        for m in lm.CATALOG:
            roles = {f["role"] for f in m["files"]}
            assert roles == {"model", "mmproj"}, "a vision model must always download its vision file too"
            assert m["vision"] is True

    def test_each_file_has_a_size_and_a_64_char_checksum_and_a_real_url(self):
        for m in lm.CATALOG:
            for f in m["files"]:
                assert f["size"] > 100_000_000 and len(f["sha256"]) == 64
                url = lm.HF_URL.format(repo=m["repo"], file=f["file"])
                assert url.startswith("https://huggingface.co/lmstudio-community/") and url.endswith(f["file"])

    def test_model_ids_are_url_safe_and_unique(self):
        ids = [m["id"] for m in lm.CATALOG]
        assert len(set(ids)) == len(ids)
        assert all(i == lm.slugify(i) for i in ids)

    def test_total_size_includes_the_vision_file(self):
        m = lm.CATALOG_BY_ID["gemma-4-12b"]
        assert lm.model_total_bytes(m["files"]) == 7381382944 + 175115296
        assert lm.model_total_bytes(lm.CATALOG_BY_ID["gemma-4-e4b"]["files"]) == 5335291936 + 991551840
        assert lm.model_total_bytes(lm.CATALOG_BY_ID["gemma-4-e2b"]["files"]) == 3427880384 + 986833248


class TestFitCheck:
    GB = 1024 ** 3

    def _gpu(self, gb):
        return lm.GpuInfo("x", int(gb * 1024), "nvidia")

    def test_small_model_on_a_16gb_card_fits_with_skyrim(self):
        assert lm.fit_check(int(7.6e9), self._gpu(16)) == "fits"

    def test_model_that_fits_the_card_but_not_beside_skyrim_is_tight(self):
        # 16 GB card: Skyrim keeps 4 GB, so 12 GB is left; 12.5 GB + room needs more than that but under 16.
        assert lm.fit_check(12 * self.GB, self._gpu(16)) == "tight"

    def test_model_bigger_than_the_card_is_too_big(self):
        assert lm.fit_check(18 * self.GB, self._gpu(16)) == "too_big"

    def test_a_model_server_has_no_skyrim_so_the_whole_card_counts(self):
        # 16 GB card, 12 GB model + room: tight beside Skyrim, but simply fits on a model server.
        assert lm.fit_check(12 * self.GB, self._gpu(16)) == "tight"
        assert lm.fit_check(12 * self.GB, self._gpu(16), reserve_skyrim=False) == "fits"
        assert lm.fit_check(18 * self.GB, self._gpu(16), reserve_skyrim=False) == "too_big"
        # the three Gemmas on a 24 GB card (a typical spare device): all but the biggest are fine
        gpu = self._gpu(24)
        assert [lm.fit_check(lm.model_total_bytes(m["files"]), gpu, reserve_skyrim=False) for m in lm.CATALOG] == ["fits"] * 5

    def test_the_real_dev_card_fits_all_three_gemmas(self):
        gpu = lm.GpuInfo("RTX 5090", 32607, "nvidia")
        assert [lm.fit_check(lm.model_total_bytes(m["files"]), gpu) for m in lm.CATALOG] == ["fits"] * 5

    def test_an_8gb_card_cannot_take_the_12b(self):
        assert lm.fit_check(lm.model_total_bytes(lm.CATALOG_BY_ID["gemma-4-12b"]["files"]), self._gpu(8)) == "too_big"

    def test_skyrim_keeps_at_least_3gb_even_on_a_small_card(self):
        # 8 GB card: a quarter is 2 GB, but we never assume under 3 GB -> 5 GB left for the model + room.
        assert lm.fit_check(int(3.4 * self.GB), self._gpu(8)) == "fits"      # 3.4 + 1.5 = 4.9 <= 5
        assert lm.fit_check(int(3.6 * self.GB), self._gpu(8)) == "tight"     # 3.6 + 1.5 = 5.1 > 5

    def test_boundary_just_fits_and_just_misses(self):
        card = 16 * self.GB
        skyrim = 4 * self.GB
        room = lm.WORKING_ROOM_BYTES
        assert lm.fit_check(card - skyrim - room, self._gpu(16)) == "fits"
        assert lm.fit_check(card - skyrim - room + 1, self._gpu(16)) == "tight"
        assert lm.fit_check(card - room, self._gpu(16)) == "tight"
        assert lm.fit_check(card - room + 1, self._gpu(16)) == "too_big"

    def test_a_huge_card_still_leaves_skyrim_ten_gb_at_most(self):
        gpu = self._gpu(48)
        assert lm.fit_check(48 * self.GB - 10 * self.GB - lm.WORKING_ROOM_BYTES, gpu) == "fits"
        assert lm.fit_check(48 * self.GB - 10 * self.GB - lm.WORKING_ROOM_BYTES + 1, gpu) == "tight"

    def test_unknown_memory_skips_the_check(self):
        assert lm.fit_check(10 * self.GB, lm.GpuInfo("x", None, "nvidia")) is None
        assert lm.fit_check(10 * self.GB, None) is None


# ------------------------------------------------------------------------------------------
# Downloads (against a tiny local web server)
# ------------------------------------------------------------------------------------------
@asynccontextmanager
async def serve(files: dict, ignore_range=False, corrupt=False, status=None):
    """A minimal file server that honours `Range: bytes=N-` (or pretends not to)."""
    requests = []

    async def handler(request):
        requests.append(dict(request.headers))
        if status:
            return web.Response(status=status)
        data = files.get(request.path.lstrip("/"))
        if data is None:
            return web.Response(status=404)
        if corrupt:
            data = b"X" + data[1:]
        rng = request.headers.get("Range")
        if rng and not ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            return web.Response(status=206, body=data[start:], headers={"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
        return web.Response(body=data)

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", requests
    finally:
        await runner.cleanup()


def _sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.mark.asyncio
class TestDownloadFile:
    DATA = bytes(range(256)) * 400      # ~100 KB

    async def _dl(self, base, dest, data=None, sha=None, size=None):
        import aiohttp
        data = self.DATA if data is None else data
        seen = []
        async with aiohttp.ClientSession() as s:
            await lm.download_file(s, f"{base}/f.bin", dest, size or len(data), sha or _sha(data), seen.append)
        return seen

    async def test_downloads_and_reports_real_progress(self, tmp_path):
        async with serve({"f.bin": self.DATA}) as (base, _):
            seen = await self._dl(base, tmp_path / "out" / "f.bin")
        assert (tmp_path / "out" / "f.bin").read_bytes() == self.DATA
        assert sum(seen) == len(self.DATA)
        assert not (tmp_path / "out" / "f.bin.part").exists()

    async def test_resumes_a_half_finished_download(self, tmp_path):
        dest = tmp_path / "f.bin"
        (tmp_path / "f.bin.part").write_bytes(self.DATA[:30000])
        async with serve({"f.bin": self.DATA}) as (base, reqs):
            seen = await self._dl(base, dest)
        assert dest.read_bytes() == self.DATA
        assert reqs[0]["Range"] == "bytes=30000-"
        assert sum(seen) == len(self.DATA), "progress must count the resumed part once, not twice"

    async def test_a_server_that_ignores_resume_still_ends_up_right(self, tmp_path):
        dest = tmp_path / "f.bin"
        (tmp_path / "f.bin.part").write_bytes(self.DATA[:30000])
        async with serve({"f.bin": self.DATA}, ignore_range=True) as (base, _):
            seen = await self._dl(base, dest)
        assert dest.read_bytes() == self.DATA
        assert sum(seen) == len(self.DATA)

    async def test_a_damaged_download_is_rejected_and_not_kept(self, tmp_path):
        dest = tmp_path / "f.bin"
        async with serve({"f.bin": self.DATA}, corrupt=True) as (base, _):
            with pytest.raises(lm.LocalModelError) as e:
                await self._dl(base, dest)
        assert "safety check" in e.value.message
        assert not dest.exists() and not (tmp_path / "f.bin.part").exists()

    async def test_wrong_size_is_rejected(self, tmp_path):
        async with serve({"f.bin": self.DATA}) as (base, _):
            with pytest.raises(lm.LocalModelError) as e:
                await self._dl(base, tmp_path / "f.bin", size=len(self.DATA) + 5)
        assert "wrong size" in e.value.message
        assert not (tmp_path / "f.bin").exists()

    async def test_an_http_error_is_a_plain_message(self, tmp_path):
        async with serve({}, status=503) as (base, _):
            with pytest.raises(lm.LocalModelError) as e:
                await self._dl(base, tmp_path / "f.bin")
        assert "503" in e.value.message

    async def test_a_finished_file_is_not_downloaded_again(self, tmp_path):
        dest = tmp_path / "f.bin"
        dest.write_bytes(self.DATA)
        async with serve({}, status=500) as (base, reqs):
            seen = await self._dl(base, dest)
        assert reqs == [] and sum(seen) == len(self.DATA)

    async def test_an_oversized_leftover_part_file_starts_over(self, tmp_path):
        (tmp_path / "f.bin.part").write_bytes(self.DATA + b"junk")
        async with serve({"f.bin": self.DATA}) as (base, _):
            await self._dl(base, tmp_path / "f.bin")
        assert (tmp_path / "f.bin").read_bytes() == self.DATA


# ------------------------------------------------------------------------------------------
# The service: folder, custom files, jobs
# ------------------------------------------------------------------------------------------
MODEL_BYTES = b"GGUF-model-weights" * 500
MMPROJ_BYTES = b"GGUF-vision-file" * 100


def _tiny_catalog():
    return [{
        "id": "tiny-model", "name": "Tiny Model", "blurb": "for tests", "vision": True, "repo": "acme/tiny",
        "files": [
            {"file": "tiny.gguf", "size": len(MODEL_BYTES), "sha256": _sha(MODEL_BYTES), "role": "model"},
            {"file": "mmproj-tiny.gguf", "size": len(MMPROJ_BYTES), "sha256": _sha(MMPROJ_BYTES), "role": "mmproj"},
        ],
    }]


@pytest.fixture
def store():
    return {}


@pytest.fixture
def svc(tmp_path, store, monkeypatch):
    catalog = _tiny_catalog()
    monkeypatch.setattr(lm, "CATALOG", catalog)
    monkeypatch.setattr(lm, "CATALOG_BY_ID", {m["id"]: m for m in catalog})
    monkeypatch.setattr(lm, "HEALTH_POLL_SECONDS", 0.05)
    mode = {"value": "ok"}

    def builder(exe, model, port, gpu_engine):
        return [sys.executable, FAKE_SERVER, "--port", str(port), "--mode", mode["value"]]

    s = lm.LocalModels(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log,
                       str(tmp_path / "pids.json"), command_builder=builder,
                       gpu_detector=lambda: lm.GpuInfo("Test GPU", 16384, "nvidia", cuda=(13, 4)))
    s.mode = mode
    return s


def _install_fake_engine(svc, tmp_path, kind="cpu"):
    folder = tmp_path / "models"
    svc.set_folder(str(folder))
    d = folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-{kind}"
    d.mkdir(parents=True)
    (d / ("llama-server.exe" if os.name == "nt" else "llama-server")).write_bytes(b"fake")
    svc._update(engine={"kind": kind, "build": lm.LLAMA_CPP_BUILD})
    return d


def _install_fake_model(svc, vouched=False):
    d = svc.folder / "models" / "tiny-model"
    d.mkdir(parents=True)
    (d / "tiny.gguf").write_bytes(MODEL_BYTES)
    (d / "mmproj-tiny.gguf").write_bytes(MMPROJ_BYTES)
    if vouched:                                  # what a finished download notes about each file
        lm.remember_verified(d / "tiny.gguf", _sha(MODEL_BYTES))
        lm.remember_verified(d / "mmproj-tiny.gguf", _sha(MMPROJ_BYTES))


class TestFolderAndCustomFiles:
    def test_no_folder_until_the_player_picks_one(self, svc):
        assert svc.folder is None
        assert svc.engine_dir() is None and svc.installed() == []

    def test_set_folder_creates_it_and_remembers_it(self, svc, tmp_path, store):
        p = svc.set_folder(str(tmp_path / "AI Models"))
        assert p.is_dir() and store["local_models"]["folder"] == str(p)

    def test_a_relative_folder_is_refused(self, svc):
        with pytest.raises(lm.LocalModelError) as e:
            svc.set_folder("models")
        assert e.value.status == 400

    def test_a_folder_that_cant_be_written_is_refused_plainly(self, svc, tmp_path):
        blocker = tmp_path / "a-file"
        blocker.write_text("x")
        with pytest.raises(lm.LocalModelError) as e:
            svc.set_folder(str(blocker / "sub"))
        assert "Couldn't save" in e.value.message

    def test_installed_needs_every_file_at_its_exact_size(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        d = svc.folder / "models" / "tiny-model"
        d.mkdir(parents=True)
        (d / "tiny.gguf").write_bytes(MODEL_BYTES)
        assert svc.installed() == [], "the vision file is missing"
        (d / "mmproj-tiny.gguf").write_bytes(MMPROJ_BYTES[:-1])
        assert svc.installed() == [], "the vision file is the wrong size"
        (d / "mmproj-tiny.gguf").write_bytes(MMPROJ_BYTES)
        [m] = svc.installed()
        assert (m.id, m.vision, m.model_name) == ("tiny-model", True, "local/tiny-model")

    def test_add_custom_gguf_references_the_file_in_place(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        f = tmp_path / "My Cool Model-Q4.gguf"
        f.write_bytes(b"x" * 10)
        m = svc.add_custom(str(f))
        assert m.model_name == "local/my-cool-model-q4" and m.custom and m.mmproj is None and not m.vision
        assert svc.find("local/MY-COOL-MODEL-Q4").path == f
        assert f.exists()

    def test_custom_with_a_vision_file_is_a_vision_model(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        f, mm = tmp_path / "v.gguf", tmp_path / "mmproj-v.gguf"
        f.write_bytes(b"x"); mm.write_bytes(b"y")
        assert svc.add_custom(str(f), str(mm)).vision is True

    def test_custom_rejects_non_gguf_and_missing_files(self, svc, tmp_path):
        notgguf = tmp_path / "x.txt"
        notgguf.write_text("hi")
        for bad in (str(notgguf), str(tmp_path / "nope.gguf")):
            with pytest.raises(lm.LocalModelError):
                svc.add_custom(bad)

    def test_two_customs_with_the_same_name_get_different_ids(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        a, b = tmp_path / "a" / "same.gguf", tmp_path / "b" / "same.gguf"
        for f in (a, b):
            f.parent.mkdir(); f.write_bytes(b"x")
        assert svc.add_custom(str(a)).id == "same"
        assert svc.add_custom(str(b)).id == "same-2"

    @pytest.mark.asyncio
    async def test_removing_a_custom_model_leaves_the_players_file(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        f = tmp_path / "keep.gguf"
        f.write_bytes(b"x")
        svc.add_custom(str(f))
        await svc.remove("keep")
        assert svc.installed() == [] and f.exists()

    @pytest.mark.asyncio
    async def test_removing_a_downloaded_model_keeps_its_files_and_drops_it_from_the_list(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc, vouched=True)
        await svc.remove("tiny-model")
        assert svc.installed() == [] and (svc.folder / "models" / "tiny-model" / "tiny.gguf").exists()
        assert [k["id"] for k in svc.kept()] == ["tiny-model"]

    @pytest.mark.asyncio
    async def test_remove_and_delete_files_removes_the_folder(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc, vouched=True)
        await svc.remove("tiny-model", delete_files=True)
        assert svc.installed() == [] and svc.kept() == [] and not (svc.folder / "models" / "tiny-model").exists()

    @pytest.mark.asyncio
    async def test_a_kept_complete_model_is_added_back_without_a_download(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc, vouched=True)
        await svc.remove("tiny-model")
        snap = await svc.snapshot()
        row = next(c for c in snap["catalog"] if c["id"] == "tiny-model")
        assert row["kept"] is True and row["installed"] is False and snap["installed"] == []
        await svc.add_kept("tiny-model")
        assert [m.id for m in svc.installed()] == ["tiny-model"] and svc.kept() == [] and not svc.jobs

    @pytest.mark.asyncio
    async def test_a_half_file_or_an_unchecked_copy_is_not_offered_as_kept(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc, vouched=True)
        await svc.remove("tiny-model")
        (svc.folder / "models" / "tiny-model" / "tiny.gguf").write_bytes(MODEL_BYTES[:100])      # cut short
        assert svc.kept() == []
        with pytest.raises(lm.LocalModelError):
            await svc.add_kept("tiny-model")
        assert svc.installed() == []
        await svc.delete_kept("tiny-model")
        assert not (svc.folder / "models" / "tiny-model").exists()

    @pytest.mark.asyncio
    async def test_files_nothing_vouches_for_are_not_kept(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc)                        # full size, but no checksum was ever noted for them
        await svc.remove("tiny-model")
        assert svc.kept() == []

    @pytest.mark.asyncio
    async def test_a_download_of_a_kept_model_needs_no_new_room_for_its_files(self, svc, tmp_path, monkeypatch):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc, vouched=True)
        await svc.remove("tiny-model")
        asked = []
        monkeypatch.setattr(svc, "_check_space", lambda need: asked.append(need))
        monkeypatch.setattr(svc, "_start_job", lambda job: job)
        svc.download_model("tiny-model")
        assert asked == [256 * 1024 * 1024] and svc.kept() == [], "the kept files are already here, so only the margin is asked for; it is no longer 'kept'"


@pytest.mark.asyncio
class TestDownloadJobs:
    async def _finish(self, job):
        await asyncio.wait_for(job.task, timeout=30)

    async def test_model_download_writes_both_files_and_shows_up_as_installed(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "m"))
        async with serve({"acme/tiny/tiny.gguf": MODEL_BYTES, "acme/tiny/mmproj-tiny.gguf": MMPROJ_BYTES}) as (base, _):
            monkeypatch.setattr(lm, "HF_URL", base + "/{repo}/{file}")
            job = svc.download_model("tiny-model")
            await self._finish(job)
        assert job.state == "done" and job.done == job.total == len(MODEL_BYTES) + len(MMPROJ_BYTES)
        assert [m.id for m in svc.installed()] == ["tiny-model"]

    async def test_a_failed_download_says_why_and_can_be_retried(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "m"))
        async with serve({}, status=503) as (base, _):
            monkeypatch.setattr(lm, "HF_URL", base + "/{repo}/{file}")
            job = svc.download_model("tiny-model")
            await self._finish(job)
        assert job.state == "error" and "503" in job.error
        async with serve({"acme/tiny/tiny.gguf": MODEL_BYTES, "acme/tiny/mmproj-tiny.gguf": MMPROJ_BYTES}) as (base, _):
            monkeypatch.setattr(lm, "HF_URL", base + "/{repo}/{file}")
            job2 = svc.download_model("tiny-model")
            assert job2 is not job
            await self._finish(job2)
        assert job2.state == "done"

    async def test_asking_twice_while_running_shares_one_job(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "m"))
        async with serve({"acme/tiny/tiny.gguf": MODEL_BYTES, "acme/tiny/mmproj-tiny.gguf": MMPROJ_BYTES}) as (base, _):
            monkeypatch.setattr(lm, "HF_URL", base + "/{repo}/{file}")
            a = svc.download_model("tiny-model")
            b = svc.download_model("tiny-model")
            assert a is b
            await self._finish(a)

    async def test_no_folder_is_a_plain_message(self, svc):
        with pytest.raises(lm.LocalModelError) as e:
            svc.download_model("tiny-model")
        assert "folder" in e.value.message

    async def test_unknown_model_is_a_404(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        with pytest.raises(lm.LocalModelError) as e:
            svc.download_model("nope")
        assert e.value.status == 404

    async def test_not_enough_disk_space_is_caught_before_downloading(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "m"))
        monkeypatch.setattr(lm.shutil, "disk_usage", lambda p: SimpleNamespace(free=10))
        with pytest.raises(lm.LocalModelError) as e:
            svc.download_model("tiny-model")
        assert "free" in e.value.message

    async def test_cancel_stops_the_job_and_forgets_it(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "m"))
        gate = asyncio.Event()

        async def slow(session, url, dest, size, sha, on_bytes):
            await gate.wait()
        monkeypatch.setattr(lm, "download_file", slow)
        job = svc.download_model("tiny-model")
        await asyncio.sleep(0.05)
        await svc.cancel("model:tiny-model")
        assert job.task.cancelled() or job.task.done()
        assert "model:tiny-model" not in svc.jobs

    @pytest.mark.skipif(sys.platform != "win32", reason="the Windows engine is a .zip with llama-server.exe in it")
    async def test_engine_download_unpacks_both_zips_together_and_remembers_the_choice(self, svc, tmp_path, monkeypatch, as_windows):
        def zip_of(files):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for n, b in files.items():
                    z.writestr(n, b)
            return buf.getvalue()
        prog, runtime = zip_of({"llama-server.exe": b"exe", "ggml.dll": b"dll"}), zip_of({"cudart64_13.dll": b"rt"})
        monkeypatch.setitem(lm.ENGINE_FILES, "cuda-13.4", [("prog.zip", len(prog), _sha(prog)), ("rt.zip", len(runtime), _sha(runtime))])
        svc.set_folder(str(tmp_path / "m"))
        async with serve({"prog.zip": prog, "rt.zip": runtime}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            job = svc.download_engine("cuda-13.4")
            await self._finish(job)
        assert job.state == "done", job.error
        d = svc.engine_dir()
        assert sorted(p.name for p in d.iterdir()) == ["cudart64_13.dll", "ggml.dll", "llama-server.exe"]
        assert svc.engine_exe() == d / "llama-server.exe"
        assert not (svc.folder / "engine" / "_downloads").exists(), "the zips are cleaned up"

    async def test_unknown_engine_kind_is_refused(self, svc, tmp_path, as_windows):
        svc.set_folder(str(tmp_path / "m"))
        with pytest.raises(lm.LocalModelError):
            svc.download_engine("cuda-99")


# ------------------------------------------------------------------------------------------
# Running llama-server
# ------------------------------------------------------------------------------------------
async def _wait_state(svc, model_id, wanted, timeout=20):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if svc.state_of(model_id)[0] in wanted:
            return svc.state_of(model_id)
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached {wanted}; state is {svc.state_of(model_id)}")


def _alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.mark.asyncio
class TestServerLifecycle:
    @pytest_asyncio.fixture(autouse=True)
    async def _cleanup(self, svc):
        yield
        await svc.stop_all()

    async def _ready_svc(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        _install_fake_model(svc)

    async def test_start_waits_until_ready_then_serves_on_its_own_port(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        s = await svc.start("tiny-model")
        assert s.state == "starting"
        await asyncio.wait_for(s.ready.wait(), 20)
        assert s.state == "running"
        assert lm.SERVER_PORT_START <= s.port < lm.SERVER_PORT_END and s.port not in (8000, 9999)
        base = await svc.ensure_ready("local/tiny-model")
        assert base == f"http://127.0.0.1:{s.port}/v1"

    async def test_its_pid_is_recorded_while_running_and_forgotten_after_stop(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        pid = s.proc.pid
        assert json.loads(Path(svc.pids_file).read_text()) == {"tiny-model": pid}
        await svc.stop("tiny-model")
        assert svc.state_of("tiny-model") == ("stopped", "")
        assert not _alive(pid)
        assert not Path(svc.pids_file).exists()

    async def test_stop_frees_it_and_a_request_then_gets_a_plain_503(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        await svc.stop("tiny-model")
        with pytest.raises(lm.LocalModelError) as e:
            await svc.ensure_ready("local/tiny-model")
        assert e.value.status == 503 and "stopped" in e.value.message

    async def test_it_can_be_started_again_after_a_stop(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        await svc.stop("tiny-model")
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        assert s.state == "running"

    async def test_a_request_during_loading_waits_for_the_model(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        svc.mode["value"] = "load-delay:1.0"
        s = await svc.start("tiny-model")
        assert s.state == "starting"
        base = await svc.ensure_ready("local/tiny-model")
        assert s.state == "running" and base.endswith(f":{s.port}/v1")

    async def test_a_request_that_waits_too_long_gets_a_plain_503(self, svc, tmp_path, monkeypatch):
        await self._ready_svc(svc, tmp_path)
        monkeypatch.setattr(lm, "REQUEST_WAIT_TIMEOUT", 0.3)
        svc.mode["value"] = "load-delay:5"
        await svc.start("tiny-model")
        with pytest.raises(lm.LocalModelError) as e:
            await svc.ensure_ready("local/tiny-model")
        assert e.value.status == 503 and "still loading" in e.value.message

    async def test_running_out_of_graphics_memory_is_named_plainly(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        svc.mode["value"] = "oom-exit"
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        state, message = svc.state_of("tiny-model")
        assert state == "failed" and "graphics memory" in message
        with pytest.raises(lm.LocalModelError) as e:
            await svc.ensure_ready("local/tiny-model")
        assert e.value.status == 503 and "graphics memory" in e.value.message

    async def test_an_engine_that_dies_later_is_noticed(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        svc.mode["value"] = "die-after-ready"
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        assert s.state == "running"
        state, message = await _wait_state(svc, "tiny-model", {"failed"})
        assert "stopped unexpectedly" in message and "code 1" in message
        with pytest.raises(lm.LocalModelError):
            await svc.ensure_ready("local/tiny-model")

    async def test_a_model_that_never_gets_ready_is_killed_and_reported(self, svc, tmp_path, monkeypatch):
        await self._ready_svc(svc, tmp_path)
        monkeypatch.setattr(lm, "SERVER_LOAD_TIMEOUT", 0.5)
        svc.mode["value"] = "load-delay:30"
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        assert s.state == "failed" and "too long" in s.message
        assert s.proc.returncode is not None

    async def test_missing_model_files_fail_honestly_and_a_missing_engine_is_fetched_first(self, svc, tmp_path, monkeypatch):
        svc.set_folder(str(tmp_path / "m"))
        s = await svc.start("tiny-model")
        assert s.state == "failed" and "missing" in s.message
        _install_fake_model(svc)
        gate = asyncio.Event()

        async def stuck(session, url, dest, size, sha, on_bytes):      # (never the real GitHub)
            await gate.wait()
        monkeypatch.setattr(lm, "download_file", stuck)
        s = await svc.start("tiny-model")
        assert s.state == "starting" and s.engine_wait is True, "loading a model with no engine gets the engine first"
        await svc.stop("tiny-model")
        assert "engine" not in svc.jobs

    async def test_unknown_model_name_is_a_404(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        with pytest.raises(lm.LocalModelError) as e:
            await svc.ensure_ready("local/not-a-model")
        assert e.value.status == 404

    async def test_models_load_one_at_a_time_in_start_all(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        f = tmp_path / "second.gguf"
        f.write_bytes(b"x")
        svc.add_custom(str(f))
        await svc.start_all()
        assert {m.id: svc.state_of(m.id)[0] for m in svc.installed()} == {"tiny-model": "running", "second": "running"}
        ports = {svc.servers[i].port for i in ("tiny-model", "second")}
        assert len(ports) == 2

    async def test_start_all_without_an_engine_does_nothing(self, svc, tmp_path):
        svc.set_folder(str(tmp_path / "m"))
        _install_fake_model(svc)
        await svc.start_all()
        assert svc.servers == {}

    async def test_snapshot_reports_state_for_the_dashboard(self, svc, tmp_path):
        await self._ready_svc(svc, tmp_path)
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 20)
        snap = await svc.snapshot()
        assert snap["gpu"] == {"name": "Test GPU", "memoryGb": 16.0, "vendor": "nvidia"}
        assert snap["engine"]["installed"] is True and snap["engine"]["kind"] == "cpu"
        assert snap["catalog"][0]["installed"] is True and snap["catalog"][0]["fit"] == "fits"
        assert snap["installed"][0]["modelName"] == "local/tiny-model" and snap["installed"][0]["state"] == "running"
        assert svc.provider_status()["state"] == "ok"
        await svc.stop("tiny-model")
        assert svc.provider_status() == {"state": "warn", "status": "Downloaded, not running",
                                          "models": [["local/tiny-model", "Tiny Model", "Sees pictures"]], "testable": False}


class TestCleanupOfLeftovers:
    def _sleeper(self):
        # Like the real engines, in its OWN process group (on Linux a group-kill of the engine must
        # never reach pytest itself).
        kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kw)

    def test_a_leftover_engine_from_a_crashed_run_is_killed_and_the_file_removed(self, svc, monkeypatch):
        p = self._sleeper()
        try:
            Path(svc.pids_file).write_text(json.dumps({"tiny-model": p.pid}))
            monkeypatch.setattr(lm, "_pid_is_llama_server", lambda pid: True)
            assert svc.cleanup_leftovers() == 1
            p.wait(timeout=15)
            assert p.returncode is not None
            assert not Path(svc.pids_file).exists()
        finally:
            p.kill()

    def test_a_reused_pid_that_is_not_llama_server_is_left_alone(self, svc, monkeypatch):
        p = self._sleeper()
        try:
            Path(svc.pids_file).write_text(json.dumps({"tiny-model": p.pid}))
            monkeypatch.setattr(lm, "_pid_is_llama_server", lambda pid: False)
            assert svc.cleanup_leftovers() == 0
            assert p.poll() is None, "an unrelated program must never be killed"
            assert not Path(svc.pids_file).exists()
        finally:
            p.kill()

    def test_no_pid_file_or_a_broken_one_is_fine(self, svc):
        assert svc.cleanup_leftovers() == 0
        Path(svc.pids_file).write_text("not json")
        assert svc.cleanup_leftovers() == 0

    def test_the_real_check_only_matches_a_process_called_llama_server(self):
        p = self._sleeper()
        try:
            assert lm._pid_is_llama_server(p.pid) is False
            assert lm._pid_is_llama_server(999999) is False
        finally:
            p.kill()


class TestExplainFailure:
    def test_out_of_memory_wordings(self):
        for text in ("CUDA error: out of memory", "cudaMalloc failed: out of memory", "ErrorOutOfDeviceMemory", "failed to allocate CUDA0 buffer"):
            assert "graphics memory" in lm.explain_failure(text, 1)

    def test_bad_model_file(self):
        assert "couldn't be loaded" in lm.explain_failure("error: failed to load model 'x.gguf'", 1)

    def test_too_new_a_model_for_the_engine(self):
        assert "doesn't know this kind of model" in lm.explain_failure("unknown model architecture: 'gemma9'", 1)

    def test_anything_else_mentions_the_exit_code(self):
        assert "(code 3)" in lm.explain_failure("???", 3)
        assert "(code" not in lm.explain_failure("", None)


@pytest.mark.asyncio
async def test_a_model_server_says_so_and_stops_reserving_room_for_skyrim(svc, store):
    assert (await svc.snapshot())["modelServer"] is False
    store["model_server"] = True
    snap = await svc.snapshot()
    assert snap["modelServer"] is True and svc.model_server is True
    gpu_small = lm.GpuInfo("Test GPU", 16384, "nvidia")
    svc._gpu, svc._gpu_read = gpu_small, True
    # The tiny test catalog: nothing changes for a 1 KB model, but the flag is what the card reads.
    assert [m["fit"] for m in snap["catalog"]] == ["fits"]


def test_free_port_skips_busy_ones_and_stays_clear_of_the_proxy_ports():
    import socket
    first = lm._free_port()
    s = socket.socket()
    s.bind(("127.0.0.1", first))
    try:
        second = lm._free_port()
        assert second != first, "a port that's taken is never handed out"
    finally:
        s.close()
    assert first not in (8000, 9999) and second not in (8000, 9999)


# ------------------------------------------------------------------------------------------
# Linux (the model-server device may run Linux)
# ------------------------------------------------------------------------------------------
class TestLinuxEngine:
    def test_the_linux_builds_are_the_pinned_ubuntu_tar_gz_ones_with_real_checksums(self):
        for table in (lm.ENGINE_FILES_LINUX, lm.ENGINE_FILES_LINUX_ARM64):
            for kind, files in table.items():
                for name, size, sha in files:
                    assert name.endswith(".tar.gz") and "ubuntu" in name and size > 1_000_000 and len(sha) == 64
                    if name.startswith("llama-"):
                        assert lm.LLAMA_CPP_BUILD in name
        assert set(lm.ENGINE_FILES_LINUX) == {"cuda-13.4", "cuda-12.8", "rocm", "vulkan", "cpu"}

    def test_linux_nvidia_builds_come_with_their_runtime_archive(self):
        for kind in ("cuda-13.4", "cuda-12.8"):
            assert any(n.startswith("cudart-llama-") for n, _, _ in lm.ENGINE_FILES_LINUX[kind])

    def test_the_table_follows_the_operating_system_and_processor(self, monkeypatch):
        monkeypatch.setattr(lm, "_platform", lambda: "win32")
        assert lm.engine_files_table() is lm.ENGINE_FILES
        monkeypatch.setattr(lm, "_platform", lambda: "linux")
        monkeypatch.setattr(lm.platform, "machine", lambda: "x86_64")
        assert lm.engine_files_table() is lm.ENGINE_FILES_LINUX
        monkeypatch.setattr(lm.platform, "machine", lambda: "aarch64")
        assert lm.engine_files_table() is lm.ENGINE_FILES_LINUX_ARM64

    def test_supported_on_windows_and_linux_not_macos(self, monkeypatch):
        for plat, ok in (("win32", True), ("linux", True), ("darwin", False)):
            monkeypatch.setattr(lm, "_platform", lambda p=plat: p)
            assert lm.platform_supported() is ok

    def test_nvidia_on_linux_gets_the_newest_cuda_the_driver_runs(self, as_linux):
        assert [o["kind"] for o in lm.engine_options(lm.GpuInfo("RTX", 24576, "nvidia", cuda=(13, 4)))] == ["cuda-13.4", "vulkan", "cpu"]
        o = lm.engine_options(lm.GpuInfo("RTX", 12288, "nvidia", cuda=(12, 9)))
        assert o[0]["kind"] == "cuda-12.8" and o[0]["recommended"]
        o = lm.engine_options(lm.GpuInfo("GTX", 4096, "nvidia", cuda=(12, 4)))
        assert o[0]["kind"] == "vulkan", "Linux has no CUDA 12.4 build, so an older driver gets the works-on-anything one"

    def test_amd_gets_vulkan_recommended_and_rocm_offered_only_when_rocm_is_installed(self, as_linux):
        with_rocm = lm.engine_options(lm.GpuInfo("Radeon", 24576, "amd", source="rocm-smi"))
        assert [o["kind"] for o in with_rocm] == ["vulkan", "rocm", "cpu"] and with_rocm[0]["recommended"] and not with_rocm[1]["recommended"]
        without = lm.engine_options(lm.GpuInfo("Radeon", None, "amd", source="lspci"))
        assert [o["kind"] for o in without] == ["vulkan", "cpu"]

    def test_arm_linux_has_only_vulkan_and_cpu(self, monkeypatch):
        monkeypatch.setattr(lm, "_platform", lambda: "linux")
        monkeypatch.setattr(lm.platform, "machine", lambda: "aarch64")
        assert [o["kind"] for o in lm.engine_options(lm.GpuInfo("RTX", 24576, "nvidia", cuda=(13, 4)))] == ["vulkan", "cpu"]
        assert lm.engine_options(None)[0]["kind"] == "cpu"

    def test_no_card_on_linux_recommends_cpu(self, as_linux):
        assert lm.engine_options(None)[0] == {**lm.engine_options(None)[0], "kind": "cpu", "recommended": True}


class TestLinuxGpuDetection:
    ROCM = json.dumps({"card0": {"Card Series": "Radeon RX 7900 XTX", "VRAM Total Memory (B)": "25753026560", "VRAM Total Used Memory (B)": "123"}})

    def test_parses_rocm_smi(self):
        [c] = lm.parse_rocm_smi(self.ROCM)
        assert (c.name, c.memory_mb, c.vendor, c.source) == ("Radeon RX 7900 XTX", 24560, "amd", "rocm-smi")

    def test_other_key_spellings_and_garbage(self):
        [c] = lm.parse_rocm_smi(json.dumps({"card1": {"Card series": "Radeon Pro", "vram total memory (b)": "8589934592"}}))
        assert c.memory_mb == 8192
        assert lm.parse_rocm_smi("not json") == [] and lm.parse_rocm_smi("[]") == [] and lm.parse_rocm_smi('{"system": {}}') == []

    def test_amd_card_found_through_rocm_smi_when_there_is_no_nvidia(self, as_linux):
        def run(cmd, **kw):
            if cmd[0] == "nvidia-smi":
                raise FileNotFoundError
            if cmd[0] == "rocm-smi":
                return SimpleNamespace(returncode=0, stdout=self.ROCM)
            raise FileNotFoundError
        g = lm.detect_gpu(run)
        assert g.vendor == "amd" and g.memory_mb == 24560

    def test_last_resort_is_the_cards_name_from_lspci_with_no_fit_check(self, as_linux):
        lspci = '00:02.0 "VGA compatible controller" "Intel Corporation" "UHD Graphics 770" -r0c\n'
        def run(cmd, **kw):
            if cmd[0] == "lspci":
                return SimpleNamespace(returncode=0, stdout=lspci)
            raise FileNotFoundError
        g = lm.detect_gpu(run)
        assert g.vendor == "intel" and g.memory_mb is None and "UHD Graphics 770" in g.name
        assert lm.fit_check(10 ** 9, g) is None

    def test_nothing_at_all_on_linux_is_none(self, as_linux):
        def run(cmd, **kw):
            raise FileNotFoundError
        assert lm.detect_gpu(run) is None


class TestUnpackTarGz:
    def _tar(self, path, members):
        import tarfile as tf
        with tf.open(path, "w:gz") as t:
            for name, data in members.items():
                info = tf.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755
                t.addfile(info, io.BytesIO(data))

    def test_unpacks_and_keeps_the_executable_bit(self, tmp_path):
        a = tmp_path / "e.tar.gz"
        self._tar(a, {"llama-b1/llama-server": b"bin", "llama-b1/libggml.so": b"lib"})
        lm.unpack_archive(a, tmp_path / "out")
        exe = tmp_path / "out" / "llama-b1" / "llama-server"
        assert exe.read_bytes() == b"bin"
        if os.name != "nt":
            assert os.access(exe, os.X_OK)

    def test_two_archives_unpack_into_the_same_folder(self, tmp_path):
        self._tar(tmp_path / "a.tar.gz", {"llama-b1/llama-server": b"bin"})
        self._tar(tmp_path / "b.tar.gz", {"llama-b1/libcudart.so.13": b"rt"})
        lm.unpack_archive(tmp_path / "a.tar.gz", tmp_path / "out")
        lm.unpack_archive(tmp_path / "b.tar.gz", tmp_path / "out")
        assert sorted(p.name for p in (tmp_path / "out" / "llama-b1").iterdir()) == ["libcudart.so.13", "llama-server"]

    def test_a_file_that_would_escape_the_folder_is_refused(self, tmp_path):
        self._tar(tmp_path / "bad.tar.gz", {"../evil": b"x"})
        with pytest.raises(lm.LocalModelError) as e:
            lm.unpack_archive(tmp_path / "bad.tar.gz", tmp_path / "out")
        assert "unsafe" in e.value.message and not (tmp_path / "evil").exists()

    def test_a_zip_that_would_escape_is_refused_too(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "bad.zip", "w") as z:
            z.writestr("../evil", "x")
        with pytest.raises(lm.LocalModelError):
            lm.unpack_archive(tmp_path / "bad.zip", tmp_path / "out")
        assert not (tmp_path / "evil").exists()

    @pytest.mark.skipif(os.name == "nt", reason="needs real symlinks")
    def test_a_symlink_pointing_outside_is_refused(self, tmp_path):
        import tarfile as tf
        with tf.open(tmp_path / "s.tar.gz", "w:gz") as t:
            info = tf.TarInfo("link")
            info.type = tf.SYMTYPE
            info.linkname = "../../etc/passwd"
            t.addfile(info)
        with pytest.raises(lm.LocalModelError):
            lm.unpack_archive(tmp_path / "s.tar.gz", tmp_path / "out")


@pytest.mark.asyncio
class TestLinuxEngineDownload:
    async def test_downloads_unpacks_and_marks_the_program_runnable(self, svc, tmp_path, monkeypatch, as_linux):
        import tarfile as tf

        def tgz(members):
            buf = io.BytesIO()
            with tf.open(fileobj=buf, mode="w:gz") as t:
                for n, b in members.items():
                    info = tf.TarInfo(n)
                    info.size = len(b)
                    info.mode = 0o644        # as if the archive forgot the executable bit
                    t.addfile(info, io.BytesIO(b))
            return buf.getvalue()
        prog, rt = tgz({"llama-b1/llama-server": b"exe", "llama-b1/libggml.so": b"so"}), tgz({"llama-b1/libcudart.so": b"rt"})
        monkeypatch.setitem(lm.ENGINE_FILES_LINUX, "cuda-13.4", [("p.tar.gz", len(prog), _sha(prog)), ("r.tar.gz", len(rt), _sha(rt))])
        svc.set_folder(str(tmp_path / "m"))
        async with serve({"p.tar.gz": prog, "r.tar.gz": rt}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            job = svc.download_engine("cuda-13.4")
            await asyncio.wait_for(job.task, timeout=30)
        assert job.state == "done", job.error
        exe = svc.engine_dir() / "llama-b1" / "llama-server"
        assert sorted(p.name for p in exe.parent.iterdir()) == ["libcudart.so", "libggml.so", "llama-server"]
        if os.name != "nt":
            assert svc.engine_exe() == exe and os.access(exe, os.X_OK)


class TestEngineNewEnoughForTheDecisionModel:
    """llama.cpp's own decision API (POST /v1/systemone) needs build 11361 or newer; an engine downloaded before that is fetched again, once, for the decision model."""

    def test_the_pinned_build_is_new_enough_and_the_pin_never_goes_below_it(self, svc, tmp_path):
        _install_fake_engine(svc, tmp_path)
        assert int(lm.LLAMA_CPP_BUILD[1:]) >= lm.NATIVE_DECISION_MIN_BUILD == 11361
        assert svc.engine_build_number() == int(lm.LLAMA_CPP_BUILD[1:]) and svc.engine_exe_current() == svc.engine_exe() and svc.engine_exe_current() is not None

    def test_an_older_engine_counts_as_none_for_the_decision_model_but_is_still_there_for_chat_models(self, svc, tmp_path):
        folder = tmp_path / "models"
        svc.set_folder(str(folder))
        d = folder / "engine" / "b11303-cpu"
        d.mkdir(parents=True)
        (d / ("llama-server.exe" if os.name == "nt" else "llama-server")).write_bytes(b"fake")
        svc._update(engine={"kind": "cpu", "build": "b11303"})
        assert svc.engine_build_number() == 11303 and svc.engine_exe() is not None and svc.engine_exe_current() is None

    def test_no_engine_or_a_build_that_cant_be_read_counts_as_none(self, svc, tmp_path):
        assert svc.engine_build_number() == 0 and svc.engine_exe_current() is None
        _install_fake_engine(svc, tmp_path)
        svc._update(engine={"kind": "cpu", "build": "latest"})
        assert svc.engine_build_number() == 0 and svc.engine_exe_current() is None


# ---- the Models tab's runner rows (2026-10-26): every build on disk is listed; the one in use is never deleted ----
def test_two_runner_builds_are_listed_and_only_the_old_one_can_be_deleted(svc, tmp_path):
    cur = _install_fake_engine(svc, tmp_path)
    old = cur.parent / "b100-cpu"
    old.mkdir()
    (old / "llama-server.exe").write_bytes(b"x" * 20)
    builds = {b["dir"]: b for b in svc.engine_builds()}
    assert builds[cur.name]["current"] is True and builds["b100-cpu"]["current"] is False and builds["b100-cpu"]["bytes"] == 20
    with pytest.raises(lm.LocalModelError):
        svc.delete_engine_build(cur.name)
    assert cur.is_dir(), "the build in use stays"
    assert svc.delete_engine_build("b100-cpu") == 20
    assert not old.exists() and cur.is_dir()
    with pytest.raises(lm.LocalModelError):
        svc.delete_engine_build("..")
