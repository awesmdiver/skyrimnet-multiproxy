"""llama.cpp engine updates (llama_builds.py, local_models.py, the Courier's engine route), 2026-10-06.

Stand-ins only: a made-up release list (with GitHub's stub "latest" release in it, exactly as the real list has it), tiny zips served from a local
test server, and a fake llama-server. No real graphics card, no real download of a multi-gigabyte package.
"""
import asyncio
import hashlib
import io
import json
import sys
import zipfile

import pytest

import llama_builds as lb
import local_models as lm
from tests.test_local_models import _install_fake_engine, _install_fake_model, _sha, as_linux, as_windows, serve, store, svc    # noqa: F401  (fixtures)

WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="the Windows engine is a .zip with llama-server.exe in it")


def _asset(name, data=b"x" * 10, digest="auto"):
    d = {"name": name, "size": len(data)}
    if digest == "auto":
        d["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    elif digest:
        d["digest"] = digest
    return d


def _release(tag, assets, prerelease=True, draft=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets}


STUB = _release("v0.6.0", [_asset("nightly-tag.txt", b"b11433")], prerelease=False)        # what GitHub calls "latest"


def _win_assets(tag, **kw):
    return [_asset(f"llama-{tag}-bin-win-vulkan-x64.zip", **kw), _asset(f"llama-{tag}-bin-win-cpu-x64.zip"),
            _asset(f"llama-{tag}-bin-win-rocm-10.0-x64.zip"), _asset(f"llama-{tag}-bin-win-sycl-x64.zip"),
            _asset(f"llama-{tag}-bin-win-cuda-12.4-x64.zip"), _asset("cudart-llama-bin-win-cuda-12.4-x64.zip")]


# ------------------------------------------------------------------------------------------
# Choosing the build
# ------------------------------------------------------------------------------------------
class TestNewestNumbered:
    def test_the_stub_latest_release_is_never_chosen_even_when_it_is_listed_first(self):
        rels = [STUB, _release("b11430", []), _release("b11433", []), _release("b11429", [])]
        assert lb.newest_numbered(rels)["tag_name"] == "b11433"

    def test_all_numbered_builds_are_pre_releases_so_the_flag_is_not_used(self):
        assert lb.newest_numbered([STUB, _release("b100", [], prerelease=True)])["tag_name"] == "b100"

    def test_it_is_the_highest_number_not_the_newest_in_the_list_and_not_a_text_sort(self):
        rels = [_release("b999", []), _release("b11433", []), _release("b2000", [])]
        assert lb.newest_numbered(rels)["tag_name"] == "b11433"

    def test_only_the_stub_means_nothing_to_offer(self):
        assert lb.newest_numbered([STUB]) is None and lb.newest_numbered([]) is None and lb.newest_numbered(None) is None

    def test_drafts_and_odd_tags_are_ignored(self):
        rels = [_release("b12000", [], draft=True), _release("b12x", []), _release("v1.0", []), _release("b11433", [])]
        assert lb.newest_numbered(rels)["tag_name"] == "b11433"

    def test_a_given_build_is_found_by_its_number(self):
        rels = [STUB, _release("b11361", []), _release("b11433", [])]
        assert lb.release_for(rels, "b11361")["tag_name"] == "b11361" and lb.release_for(rels, "b1") is None and lb.release_for(rels, "v0.6.0") is None


class TestFilesForAKind:
    def test_windows_vulkan_gives_the_name_the_size_and_the_checksum(self):
        data = b"zipbytes"
        [(name, size, sha)] = lb.files_for(_release("b11433", _win_assets("b11433", data=data)), "vulkan", "win")
        assert (name, size, sha) == ("llama-b11433-bin-win-vulkan-x64.zip", len(data), hashlib.sha256(data).hexdigest())

    def test_the_nvidia_runtime_is_the_second_file_and_has_no_build_number_on_windows(self):
        files = lb.files_for(_release("b11433", _win_assets("b11433")), "cuda-12.4", "win")
        assert [f[0] for f in files] == ["llama-b11433-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"]

    def test_rocm_is_matched_whatever_its_own_number_is(self):
        rel = _release("b11500", [_asset("llama-b11500-bin-win-rocm-11.1-x64.zip")])
        assert lb.files_for(rel, "rocm", "win")[0][0] == "llama-b11500-bin-win-rocm-11.1-x64.zip"

    def test_a_file_with_no_published_checksum_is_refused_never_guessed(self):
        with pytest.raises(lb.BuildError) as e:
            lb.files_for(_release("b11433", _win_assets("b11433", digest=None)), "vulkan", "win")
        assert "safety check" in str(e.value)

    def test_a_malformed_checksum_is_refused(self):
        with pytest.raises(lb.BuildError):
            lb.files_for(_release("b11433", _win_assets("b11433", digest="sha256:abc")), "vulkan", "win")

    def test_a_build_that_lacks_the_kind_is_refused_plainly(self):
        with pytest.raises(lb.BuildError) as e:
            lb.files_for(_release("b11433", [_asset("llama-b11433-bin-win-cpu-x64.zip")]), "vulkan", "win")
        assert "b11433" in str(e.value) and "vulkan" in str(e.value)

    def test_linux_sycl_is_refused_because_its_runtime_is_not_in_the_package(self):
        rel = _release("b11433", [_asset("llama-b11433-bin-ubuntu-sycl-fp16-x64.tar.gz")])
        with pytest.raises(lb.BuildError):
            lb.files_for(rel, "sycl", "linux")

    def test_the_stub_is_not_a_build(self):
        with pytest.raises(lb.BuildError):
            lb.files_for(STUB, "vulkan", "win")

    def test_two_assets_that_both_match_is_refused_not_a_coin_flip(self):
        rel = _release("b11433", [_asset("llama-b11433-bin-win-rocm-10.0-x64.zip"), _asset("llama-b11433-bin-win-rocm-11.0-x64.zip")])
        with pytest.raises(lb.BuildError):
            lb.files_for(rel, "rocm", "win")


@pytest.mark.asyncio
async def test_an_unreadable_release_list_is_just_empty():
    class Resp:
        status = 403
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class Session:
        def get(self, *a, **k): return Resp()
    assert await lb.fetch_releases(Session()) == []

    class Boom:
        def get(self, *a, **k): raise OSError("offline")
    assert await lb.fetch_releases(Boom()) == []


# ------------------------------------------------------------------------------------------
# The faster versions are offered only for a card that is really there
# ------------------------------------------------------------------------------------------
def _kinds(gpu):
    return [o["kind"] for o in lm.engine_options(gpu)]


class TestFasterVersionsOffer:
    def test_an_amd_card_on_windows_is_offered_the_amd_version_as_advanced_never_recommended(self, as_windows):
        opts = lm.engine_options(lm.GpuInfo("AMD Radeon RX 7900 XTX", 24576, "amd", source="registry"))
        assert [o["kind"] for o in opts] == ["vulkan", "rocm", "cpu"]
        rocm = next(o for o in opts if o["kind"] == "rocm")
        assert rocm["advanced"] and not rocm["recommended"] and opts[0]["kind"] == "vulkan" and opts[0]["recommended"]
        assert "recent" in rocm["blurb"] and "driver" in rocm["blurb"]

    def test_an_intel_card_on_windows_is_offered_the_intel_version(self, as_windows):
        opts = lm.engine_options(lm.GpuInfo("Intel Arc B580", 12288, "intel", source="registry"))
        assert [o["kind"] for o in opts] == ["vulkan", "sycl", "cpu"] and next(o for o in opts if o["kind"] == "sycl")["advanced"]

    def test_nvidia_and_unknown_cards_and_no_card_get_no_faster_version(self, as_windows):
        for gpu in (lm.GpuInfo("RTX 4090", 24576, "nvidia", cuda=(13, 4)), lm.GpuInfo("Something", 4096, "other"), None):
            assert not set(_kinds(gpu)) & set(lm.FAST_KINDS)

    def test_an_intel_card_gets_no_amd_version_and_an_amd_card_no_intel_one(self, as_windows):
        assert "rocm" not in _kinds(lm.GpuInfo("Intel Arc", 8192, "intel")) and "sycl" not in _kinds(lm.GpuInfo("Radeon", 8192, "amd"))

    def test_on_linux_intel_gets_nothing_extra_and_amd_only_when_rocm_is_there(self, as_linux):
        assert "sycl" not in _kinds(lm.GpuInfo("Intel Arc", 8192, "intel"))
        assert "rocm" not in _kinds(lm.GpuInfo("Radeon", 8192, "amd", source="registry")) and "rocm" in _kinds(lm.GpuInfo("Radeon", 8192, "amd", source="rocm-smi"))

    def test_the_pinned_default_stays_the_pinned_default(self):
        assert lm.LLAMA_CPP_BUILD == "b11361"
        for kind in lm.FAST_KINDS:
            for n, size, sha in lm.ENGINE_FILES[kind]:
                assert lm.LLAMA_CPP_BUILD in n and size > 0 and len(sha) == 64


# ------------------------------------------------------------------------------------------
# Updating: only when asked, always by number, always checked
# ------------------------------------------------------------------------------------------
def _zip_with_server(extra=b"new"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("llama-server.exe", extra)
    return buf.getvalue()


@pytest.mark.asyncio
class TestUpdate:
    def _releases(self, svc, monkeypatch, rels):
        async def fake(force=False):
            return rels
        monkeypatch.setattr(svc, "_releases", fake)

    async def test_a_newer_numbered_build_is_offered_for_the_installed_kind_and_nothing_is_installed_by_asking(self, svc, tmp_path, monkeypatch, as_windows):
        d = _install_fake_engine(svc, tmp_path, "vulkan")
        self._releases(svc, monkeypatch, [STUB, _release("b11500", _win_assets("b11500")), _release("b11361", _win_assets("b11361"))])
        info = await svc.engine_update_info()
        assert info["available"] and info["build"] == "b11500" and info["kind"] == "vulkan" and info["sizeBytes"] == 10
        assert svc._cfg()["engine"] == {"kind": "vulkan", "build": lm.LLAMA_CPP_BUILD} and d.is_dir(), "asking changes nothing"
        assert not svc.jobs

    async def test_only_the_stub_or_only_older_builds_means_no_update(self, svc, tmp_path, monkeypatch, as_windows):
        _install_fake_engine(svc, tmp_path, "vulkan")
        self._releases(svc, monkeypatch, [STUB])
        assert (await svc.engine_update_info()) == {"available": False}
        self._releases(svc, monkeypatch, [STUB, _release("b11361", _win_assets("b11361")), _release("b11000", _win_assets("b11000"))])
        assert (await svc.engine_update_info()) == {"available": False}
        with pytest.raises(lm.LocalModelError):
            await svc.update_engine()

    async def test_no_installed_engine_means_nothing_to_update(self, svc, tmp_path, monkeypatch, as_windows):
        svc.set_folder(str(tmp_path / "m"))
        self._releases(svc, monkeypatch, [_release("b11500", _win_assets("b11500"))])
        assert (await svc.engine_update_info()) == {"available": False}

    async def test_a_newer_build_without_a_listed_checksum_is_not_offered_and_says_why(self, svc, tmp_path, monkeypatch, as_windows):
        _install_fake_engine(svc, tmp_path, "vulkan")
        self._releases(svc, monkeypatch, [_release("b11500", _win_assets("b11500", digest=None))])
        info = await svc.engine_update_info()
        assert not info["available"] and "safety check" in info["reason"]
        with pytest.raises(lm.LocalModelError) as e:
            await svc.update_engine()
        assert "safety check" in e.value.message

    async def test_the_default_download_is_still_the_pinned_build_never_a_newer_one(self, svc, tmp_path, monkeypatch, as_windows):
        svc.set_folder(str(tmp_path / "m"))
        self._releases(svc, monkeypatch, [_release("b11500", _win_assets("b11500"))])
        seen = []
        async def fake_start(job):
            seen.append(job)
            return job
        monkeypatch.setattr(svc, "_start_job", lambda job: seen.append(job) or job)
        svc.download_engine("vulkan")
        assert seen[0].files[0]["url"].endswith(f"/{lm.LLAMA_CPP_BUILD}/llama-{lm.LLAMA_CPP_BUILD}-bin-win-vulkan-x64.zip")

    @WIN_ONLY
    async def test_update_downloads_the_newer_build_checks_it_switches_to_it_and_removes_the_old_one(self, svc, tmp_path, monkeypatch, as_windows):
        old = _install_fake_engine(svc, tmp_path, "vulkan")
        z = _zip_with_server(b"newer")
        rel = _release("b11500", [_asset("llama-b11500-bin-win-vulkan-x64.zip", z)])
        self._releases(svc, monkeypatch, [STUB, rel])
        async with serve({"llama-b11500-bin-win-vulkan-x64.zip": z}) as (base, reqs):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            job = await svc.update_engine()
            await asyncio.wait_for(job.task, 30)
        assert job.state == "done", job.error
        assert svc._cfg()["engine"] == {"kind": "vulkan", "build": "b11500"} and svc.engine_build_number() == 11500
        assert svc.engine_dir().name == "b11500-vulkan" and svc.engine_exe().read_bytes() == b"newer"
        assert not old.exists(), "the build it replaced is removed"

    @WIN_ONLY
    async def test_a_download_that_does_not_match_its_published_checksum_is_refused_and_the_working_engine_is_kept(self, svc, tmp_path, monkeypatch, as_windows):
        old = _install_fake_engine(svc, tmp_path, "vulkan")
        z = _zip_with_server(b"newer")
        rel = _release("b11500", [_asset("llama-b11500-bin-win-vulkan-x64.zip", z)])
        self._releases(svc, monkeypatch, [rel])
        async with serve({"llama-b11500-bin-win-vulkan-x64.zip": b"Q" + z[1:]}) as (base, _):       # same size, other bytes
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            job = await svc.update_engine()
            await asyncio.wait_for(job.task, 30)
        assert job.state == "error" and "safety check" in job.error
        assert svc._cfg()["engine"] == {"kind": "vulkan", "build": lm.LLAMA_CPP_BUILD} and old.is_dir() and svc.engine_exe().read_bytes() == b"fake"

    @WIN_ONLY
    async def test_the_old_build_is_kept_while_a_model_is_running_from_it(self, svc, tmp_path, monkeypatch, as_windows):
        old = _install_fake_engine(svc, tmp_path, "vulkan")
        svc.servers["tiny-model"] = lm.Server("tiny-model")
        svc.servers["tiny-model"].state = "running"
        new = old.parent / "b11500-vulkan"
        new.mkdir()
        svc._prune_old_engines(new)
        assert old.is_dir()
        svc.servers["tiny-model"].state = "stopped"
        svc._prune_old_engines(new)
        assert not old.exists() and new.is_dir()


# ------------------------------------------------------------------------------------------
# A faster build that will not start is replaced by the works-on-anything one
# ------------------------------------------------------------------------------------------
@pytest.mark.asyncio
@WIN_ONLY
class TestFallbackToVulkan:
    def _builder(self, svc, started):
        from tests.test_local_models import FAKE_SERVER

        def builder(exe, model, port, gpu_engine):
            started.append(str(exe))
            if "rocm" in str(exe) or "sycl" in str(exe):
                return [sys.executable, "-c", "import sys; print('HIP error: no kernel image is available'); sys.exit(1)"]
            return [sys.executable, FAKE_SERVER, "--port", str(port), "--mode", "ok"]
        svc._command_builder = builder

    async def _setup(self, svc, tmp_path, monkeypatch, kind):
        _install_fake_engine(svc, tmp_path, kind)
        _install_fake_model(svc)
        z = _zip_with_server(b"vk")
        monkeypatch.setitem(lm.ENGINE_FILES, "vulkan", [("vk.zip", len(z), _sha(z))])
        return z

    async def test_the_model_ends_up_running_on_vulkan_with_a_one_line_note(self, svc, tmp_path, monkeypatch, as_windows):
        started = []
        self._builder(svc, started)
        z = await self._setup(svc, tmp_path, monkeypatch, "rocm")
        async with serve({"vk.zip": z}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start("tiny-model")
            await asyncio.wait_for(s.ready.wait(), 60)
        assert s.state == "running", s.message
        assert svc._cfg()["engine"]["kind"] == "vulkan" and "rocm" in started[0] and "vulkan" in started[-1]
        assert svc.engine_notes["tiny-model"] == lm.FALLBACK_NOTE
        row = next(r for r in (await svc.snapshot())["installed"] if r["id"] == "tiny-model")
        assert row["engineNote"] == lm.FALLBACK_NOTE
        await svc.stop("tiny-model")

    async def test_the_intel_version_falls_back_the_same_way(self, svc, tmp_path, monkeypatch, as_windows):
        started = []
        self._builder(svc, started)
        z = await self._setup(svc, tmp_path, monkeypatch, "sycl")
        async with serve({"vk.zip": z}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start("tiny-model")
            await asyncio.wait_for(s.ready.wait(), 60)
        assert s.state == "running" and svc._cfg()["engine"]["kind"] == "vulkan"
        await svc.stop("tiny-model")

    async def test_a_newer_build_falls_back_to_the_vulkan_file_of_the_same_release(self, svc, tmp_path, monkeypatch, as_windows):
        started = []
        self._builder(svc, started)
        z = await self._setup(svc, tmp_path, monkeypatch, "rocm")
        svc._update(engine={"kind": "rocm", "build": "b11500"})
        (svc.folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-rocm").rename(svc.folder / "engine" / "b11500-rocm")
        rel = _release("b11500", [_asset("llama-b11500-bin-win-vulkan-x64.zip", z)])

        async def fake(force=False):
            return [STUB, rel]
        monkeypatch.setattr(svc, "_releases", fake)
        async with serve({"llama-b11500-bin-win-vulkan-x64.zip": z}) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start("tiny-model")
            await asyncio.wait_for(s.ready.wait(), 60)
        assert s.state == "running" and svc._cfg()["engine"] == {"kind": "vulkan", "build": "b11500"}
        await svc.stop("tiny-model")

    async def test_when_vulkan_cannot_be_fetched_the_model_fails_with_the_engines_own_reason(self, svc, tmp_path, monkeypatch, as_windows):
        started = []
        self._builder(svc, started)
        await self._setup(svc, tmp_path, monkeypatch, "rocm")
        async with serve({}, status=500) as (base, _):
            monkeypatch.setattr(lm, "_RELEASE_URL", base + "/{name}")
            s = await svc.start("tiny-model")
            await asyncio.wait_for(s.ready.wait(), 60)
        assert s.state == "failed" and s.message and not svc.engine_notes.get("tiny-model")
        assert svc._cfg()["engine"]["kind"] == "rocm"

    async def test_a_works_on_anything_engine_that_fails_is_not_replaced(self, svc, tmp_path, monkeypatch, as_windows):
        started = []
        svc._command_builder = lambda exe, model, port, gpu: (started.append(str(exe)), [sys.executable, "-c", "import sys; sys.exit(1)"])[1]
        _install_fake_engine(svc, tmp_path, "vulkan")
        _install_fake_model(svc)
        s = await svc.start("tiny-model")
        await asyncio.wait_for(s.ready.wait(), 60)
        assert s.state == "failed" and len(started) == 1 and not svc.jobs and svc._cfg()["engine"]["kind"] == "vulkan"


# ------------------------------------------------------------------------------------------
# Another device (Courier): the same update, the same faster versions
# ------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestCourier:
    def _adapter(self, svc):
        import courier_adapters as ca
        import logging
        a = ca.LlamaCppAdapter(logging.getLogger("t"), svc)
        a.changed = lambda: None
        return a

    async def test_the_engine_info_names_the_build_the_update_and_only_the_faster_versions_the_card_is_offered(self, svc, tmp_path, monkeypatch, as_windows):
        svc._gpu_detector = lambda: lm.GpuInfo("AMD Radeon RX 7800", 16384, "amd", source="registry")
        _install_fake_engine(svc, tmp_path, "vulkan")

        async def fake(force=False):
            return [STUB, _release("b11500", _win_assets("b11500"))]
        monkeypatch.setattr(svc, "_releases", fake)
        info = await self._adapter(svc).engine_info()
        assert info["kind"] == "vulkan" and info["build"] == lm.LLAMA_CPP_BUILD
        assert info["update"] == {"available": True, "build": "b11500", "sizeBytes": 10}
        assert [o["kind"] for o in info["options"]] == ["rocm"]

    async def test_use_refuses_a_version_the_card_is_not_offered(self, svc, tmp_path, monkeypatch, as_windows):
        import courier_adapters as ca
        svc._gpu_detector = lambda: lm.GpuInfo("RTX 4090", 24576, "nvidia", cuda=(13, 4))
        _install_fake_engine(svc, tmp_path, "vulkan")
        with pytest.raises(ca.AdapterError) as e:
            await self._adapter(svc).engine_use("rocm")
        assert e.value.status == 400

    async def test_update_with_nothing_newer_is_a_plain_refusal_and_busy_is_a_plain_409(self, svc, tmp_path, monkeypatch, as_windows):
        import courier_adapters as ca
        _install_fake_engine(svc, tmp_path, "vulkan")

        async def fake(force=False):
            return [STUB]
        monkeypatch.setattr(svc, "_releases", fake)
        a = self._adapter(svc)
        with pytest.raises(ca.AdapterError) as e:
            await a.engine_update()
        assert e.value.status == 400
        a.loading.add("x")
        with pytest.raises(ca.AdapterError) as e:
            await a.engine_update()
        assert e.value.status == 409

    async def test_the_route_asks_for_an_action_and_never_updates_by_itself(self, svc, tmp_path, monkeypatch, as_windows):
        """Reading the engine page never changes it: only the POST with an action does."""
        import courier
        src = open(courier.__file__, encoding="utf-8").read()
        assert 'add_get("/courier/v1/programs/{program}/engine"' in src and 'add_post("/courier/v1/programs/{program}/engine"' in src
        assert "Didn't recognize that command." in src.split("async def _h_engine_post")[1].split("async def _h_settings")[0]


def test_the_courier_package_carries_the_new_module():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    import json
    # (the Courier's files are listed once, in courier/courier-files.json: the zip, the installer, the Linux package and the set-up script all read it)
    assert "llama_builds.py" in json.loads((root / "courier" / "courier-files.json").read_text(encoding="utf-8"))["program"]
