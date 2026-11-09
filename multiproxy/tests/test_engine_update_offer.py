"""An older model engine on another device is offered this PC's build (2026-11-04). Fake data only."""
import asyncio

import llama_builds
import local_models
import other_pcs


def test_an_older_engine_is_offered_the_build_this_pc_uses_even_when_the_device_saw_nothing():
    info = {"installed": True, "kind": "cuda", "build": "b11890", "update": {"available": False, "build": None, "sizeBytes": None}}
    assert other_pcs.engine_update_offer(info, "b11946")["build"] == "b11946" and other_pcs.engine_update_offer(info, "b11946")["available"] is True


def test_the_same_or_a_newer_engine_shows_nothing_new():
    for b in ("b11946", "b12000"):
        info = {"installed": True, "build": b, "update": {"available": False}}
        assert other_pcs.engine_update_offer(info, "b11946") == {"available": False}


def test_a_device_with_no_engine_or_no_build_is_not_offered_one():
    assert other_pcs.engine_update_offer({"installed": False, "build": None}, "b11946") == {"available": False}
    assert other_pcs.engine_update_offer({"installed": True, "build": None}, "b11946") == {"available": False}


def test_the_devices_own_offer_is_kept_when_it_is_not_behind_this_pc():
    own = {"available": True, "build": "b12400", "sizeBytes": 5}
    assert other_pcs.engine_update_offer({"installed": True, "build": "b12000", "update": own}, "b11946") == own


def test_update_engine_with_a_build_downloads_exactly_that_build_with_the_release_checksums(monkeypatch, tmp_path):
    svc = local_models.LocalModels.__new__(local_models.LocalModels)
    calls = []
    monkeypatch.setattr(svc, "_cfg", lambda: {"engine": {"kind": "cuda"}}, raising=False)
    monkeypatch.setattr(svc, "engine_exe", lambda: tmp_path / "x", raising=False)

    async def rel(force=False):
        return [{"tag_name": "b11946"}, {"tag_name": "b11890"}]
    monkeypatch.setattr(svc, "_releases", rel, raising=False)
    monkeypatch.setattr(local_models.LocalModels, "_system_key", staticmethod(lambda: "win"))
    monkeypatch.setattr(llama_builds, "files_for", lambda r, k, s: [("a.zip", 1, "h")])
    monkeypatch.setattr(svc, "download_engine", lambda kind, build="", files=None: calls.append((kind, build, files)) or "job", raising=False)
    assert asyncio.run(svc.update_engine("b11946")) == "job"
    assert calls == [("cuda", "b11946", [("a.zip", 1, "h")])]


# ---- Courier build 13: the capability, the build number, and what the page offers ----
import json
import shutil
import subprocess

import courier
import courier_adapters
import local_models_ui
import pytest

BEHIND = {"installed": True, "build": "b11890", "update": {"available": False}}


def test_a_device_with_the_capability_gets_the_button():
    assert other_pcs.engine_update_offer({**BEHIND, "capabilities": ["engine-exact-update.v2"]}, "b11946", False)["how"] == "button"


def test_a_device_without_it_gets_update_the_courier_first_or_the_plain_line_never_a_button():
    assert other_pcs.engine_update_offer(BEHIND, "b11946", True)["how"] == "courierFirst"
    assert other_pcs.engine_update_offer(BEHIND, "b11946", False)["how"] == "plain"


def test_courier_build_13_reports_the_capability_and_the_build_is_13():
    assert courier.COURIER_BUILD >= 13 and courier.COURIER_VERSION.endswith(f".{courier.COURIER_BUILD}")          # (the build moves on; the capability below must stay)
    assert other_pcs.CAP_ENGINE_EXACT_UPDATE == "engine-exact-update.v2" == courier_adapters.CAP_ENGINE_EXACT_UPDATE
    assert "capabilities\": [CAP_ENGINE_EXACT_UPDATE]" in open(courier_adapters.__file__, encoding="utf-8").read()


def test_the_mismatch_message_names_the_build_and_where_to_press_the_button():
    import local_engine_sync
    m = local_engine_sync.mismatch_message("b11361", "b11946")
    assert m == ("This device runs engine b11361, but your main device sent settings made for b11946. "
                 "On the main device, open Other devices and press Update engine for this device, then try again.")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node isn't installed")
def test_the_engine_row_line_for_each_kind_of_device_and_the_failure_line():
    js = local_models_ui.LOCAL_MODELS_JS
    fn = js[js.index("function lmBuildNumber("):js.index("function lmInstalledEngineExtras(")]
    fn = fn[:fn.index("function lmUseEngine(")] + fn[fn.index("function lmEngineUpdateInline("):]
    src = ("const esc = s => String(s), jsq = s => String(s), lmFmt = b => b + ' B', lmCheckEngineUpdate = () => {};\n" + fn +
           "\nconst base = { scope: 'MSI', engine: { build: 'b11890' } };\n"
           "const up = how => Object.assign({}, base, { engineUpdate: { available: true, build: 'b11946', how } });\n"
           "console.log(JSON.stringify([lmEngineUpdateInline(up('button')), lmEngineUpdateInline(up('courierFirst')), lmEngineUpdateInline(up('plain')),\n"
           "  lmEngineUpdateInline(Object.assign(up('button'), { engineFail: { build: 'b11946', why: 'old' } })), lmEngineUpdateInline(Object.assign({}, base, { engineUpdate: { available: false } }))]));")
    out = subprocess.run([shutil.which("node"), "-e", src], capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    button, courier_first, plain, failed, nothing = json.loads(out.stdout.decode("utf-8"))
    assert "Newer engine available: b11946" in button and ">Update engine<" in button and "updateEngine" in button
    assert ">Update the Courier first<" in courier_first and "ucStart('MSI')" in courier_first and "updateEngine" not in courier_first
    assert "Update the Courier on that device first." in plain and "<button" not in plain
    assert "That device could not update to b11946. Update the Courier on it first." in failed
    got = json.loads(subprocess.run([shutil.which("node"), "-e", src.replace("engineFail: { build: 'b11946', why: 'old' }", "engineFail: { build: 'b11946', why: 'get' }")], capture_output=True, timeout=30).stdout.decode("utf-8"))
    assert "That device could not get b11946. Try again, or check its internet." in got[3] and ">Update engine<" in got[3]
    assert nothing == ""


# ---- a build that has scrolled off the newest-30 list (2026-11-04) ----
def _svc(monkeypatch, tmp_path, listed, by_tag, kind="cuda-13.4"):
    svc = local_models.LocalModels.__new__(local_models.LocalModels)
    calls = {"download": [], "tag": []}
    monkeypatch.setattr(svc, "_cfg", lambda: {"engine": {"kind": kind}}, raising=False)
    monkeypatch.setattr(svc, "engine_exe", lambda: tmp_path / "x", raising=False)

    async def rel(force=False):
        return listed
    monkeypatch.setattr(svc, "_releases", rel, raising=False)

    async def tag(build):
        calls["tag"].append(build)
        return by_tag
    monkeypatch.setattr(svc, "_release_by_tag", tag, raising=False)
    monkeypatch.setattr(local_models.LocalModels, "_system_key", staticmethod(lambda: "win"))
    monkeypatch.setattr(llama_builds, "files_for", lambda r, k, s: [("a.zip", 1, "h")])
    monkeypatch.setattr(svc, "download_engine", lambda kind, build="", files=None: calls["download"].append((kind, build, files)) or "job", raising=False)
    return svc, calls


def test_the_pinned_build_uses_the_listed_files_and_never_asks_github(monkeypatch, tmp_path):
    kind = next(iter(local_models.engine_files_table()))
    svc, calls = _svc(monkeypatch, tmp_path, [], None, kind=kind)
    assert asyncio.run(svc.update_engine(local_models.LLAMA_CPP_BUILD)) == "job"
    assert calls["download"] == [(kind, "", None)] and calls["tag"] == [], "the pinned download: its own checksums, no release list, no lookup"


def test_another_old_build_is_looked_up_by_its_tag(monkeypatch, tmp_path):
    svc, calls = _svc(monkeypatch, tmp_path, [{"tag_name": "b12500"}], {"tag_name": "b11800"}, kind="not-in-the-table")
    assert asyncio.run(svc.update_engine("b11800")) == "job"
    assert calls["tag"] == ["b11800"] and calls["download"] == [("not-in-the-table", "b11800", [("a.zip", 1, "h")])]


def test_a_build_in_the_list_needs_no_lookup(monkeypatch, tmp_path):
    svc, calls = _svc(monkeypatch, tmp_path, [{"tag_name": "b11800"}], None, kind="not-in-the-table")
    asyncio.run(svc.update_engine("b11800"))
    assert calls["tag"] == []


def test_a_tag_that_does_not_exist_or_an_offline_lookup_gives_the_plain_failure_line(monkeypatch, tmp_path):
    svc, calls = _svc(monkeypatch, tmp_path, [], None, kind="not-in-the-table")
    with pytest.raises(local_models.LocalModelError) as e:
        asyncio.run(svc.update_engine("b11800"))
    assert e.value.message == "That device could not get b11800. Try again, or check its internet." and calls["download"] == []


def test_the_tag_lookup_reads_one_release_and_never_a_bad_tag():
    class Resp:
        def __init__(self, status, data): self.status, self._d = status, data
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self, content_type=None): return self._d

    class Sess:
        def __init__(self, resp): self.resp, self.urls = resp, []
        def get(self, url, headers=None): self.urls.append(url); return self.resp
    ok = Sess(Resp(200, {"tag_name": "b11800"}))
    assert asyncio.run(llama_builds.fetch_release_by_tag(ok, "b11800")) == {"tag_name": "b11800"} and ok.urls == ["https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11800"]
    assert asyncio.run(llama_builds.fetch_release_by_tag(Sess(Resp(404, {})), "b11800")) is None
    assert asyncio.run(llama_builds.fetch_release_by_tag(Sess(Resp(403, {})), "b11800")) is None
    never = Sess(Resp(200, {}))
    assert asyncio.run(llama_builds.fetch_release_by_tag(never, "../x")) is None and never.urls == []


def test_a_build_13_courier_with_only_the_first_capability_is_not_given_the_button():
    assert other_pcs.engine_update_offer({**BEHIND, "capabilities": ["engine-exact-update.v1"]}, "b11946", True)["how"] == "courierFirst"
