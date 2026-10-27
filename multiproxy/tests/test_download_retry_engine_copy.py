"""The runner copied from this device to another one instead of downloaded there (2026-10-23, redone after the first build was lost): the zip of the installed runner
folder, what the receiving Courier accepts (one name shape, Windows only, a build and a kind it knows), the unpacking checks, and the main device's choice to send it
or fall back to the usual download."""
import asyncio
import json
import logging
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import decision_courier as dc
import decision_engine as de
import decision_remote
import file_transfer
import local_models as lm
from voice_downloads import DownloadStore

pytestmark = [pytest.mark.skipif(os.name != "nt", reason="the runner copy is Windows only")]
log = logging.getLogger("test-engine-copy")
BUILD, KIND = "b11461", "cuda-13.4"


def make_local(tmp_path, name, with_engine=True, build=BUILD, kind=KIND):
    store = {}
    s = lm.LocalModels(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log, str(tmp_path / f"{name}-pids.json"))
    s.set_folder(str(tmp_path / name))
    if with_engine:
        d = tmp_path / name / "engine" / f"{build}-{kind}"
        (d / "sub").mkdir(parents=True)
        (d / "llama-server.exe").write_bytes(b"exe")
        (d / "ggml-cuda.dll").write_bytes(b"dll" * 100)
        (d / "sub" / "extra.dll").write_bytes(b"more")
        s._update(engine={"kind": kind, "build": build})
    return s


class TestPackAndUnpack:
    def test_the_runner_folder_goes_into_a_zip_with_relative_names_and_comes_out_the_same(self, tmp_path):
        a, b = make_local(tmp_path, "a"), make_local(tmp_path, "b", with_engine=False)
        z = tmp_path / "x" / "engine-copy.zip"
        assert a.pack_engine(z) == (BUILD, KIND)
        names = zipfile.ZipFile(z).namelist()
        assert sorted(names) == ["ggml-cuda.dll", "llama-server.exe", "sub/extra.dll"] and not any(n.startswith(("/", "..")) or ":" in n for n in names)
        b.install_engine_copy(z, BUILD, KIND)
        assert b.engine_exe_current() == tmp_path / "b" / "engine" / f"{BUILD}-{KIND}" / "llama-server.exe"
        assert (tmp_path / "b" / "engine" / f"{BUILD}-{KIND}" / "sub" / "extra.dll").read_bytes() == b"more" and b.engine_build_number() == 11461

    def test_nothing_to_pack_without_a_current_runner(self, tmp_path):
        with pytest.raises(lm.LocalModelError):
            make_local(tmp_path, "a", with_engine=False).pack_engine(tmp_path / "z.zip")
        with pytest.raises(lm.LocalModelError):                                            # a runner too old for the decision model is never sent
            make_local(tmp_path, "old", build="b11000").pack_engine(tmp_path / "z.zip")

    @pytest.mark.parametrize("bad", ["../escape.dll", "..\\escape.dll", "C:/Windows/evil.dll", "/abs.dll"])
    def test_a_name_that_leaves_the_folder_is_refused_and_nothing_is_changed(self, tmp_path, bad):
        b = make_local(tmp_path, "b", with_engine=False)
        z = tmp_path / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("llama-server.exe", b"x")
            zf.writestr(bad, b"evil")
        with pytest.raises(lm.LocalModelError):
            b.install_engine_copy(z, BUILD, KIND)
        assert not (tmp_path / "b" / "engine").exists() or not any((tmp_path / "b" / "engine").iterdir()), "no half-unpacked folder is left"
        assert not (tmp_path / "escape.dll").exists() and b._cfg().get("engine") is None

    def test_a_zip_without_a_runner_in_it_or_a_damaged_one_or_an_odd_build_is_refused(self, tmp_path):
        b = make_local(tmp_path, "b", with_engine=False)
        empty = tmp_path / "empty.zip"
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("readme.txt", b"x")
        for z, build, kind in ((empty, BUILD, KIND), (tmp_path / "x.zip", BUILD, KIND), (empty, "b100", KIND), (empty, BUILD, "no-such-kind")):
            if z.name == "x.zip":
                z.write_bytes(b"not a zip")
            with pytest.raises(lm.LocalModelError):
                b.install_engine_copy(z, build, kind)
        assert b._cfg().get("engine") is None


@pytest.fixture
def engine_copy_on(monkeypatch):
    """The runner copy is switched off in the product (security review of the 4.0 release, M1); these tests check how it works when it is on."""
    monkeypatch.setattr(dc, "ENGINE_COPY_ENABLED", True)


class TestSwitchedOffByDefault:
    """Security review of the 4.0 release, Must fix M1: an unpinned zip of programs, vouched for only by the pairing code, is not accepted from the main device."""

    def spec(self, **kw):
        return {"kind": "decision", "model": "decision-model", "file": f"engine-copy-{BUILD}-{KIND}.zip", "size": 1000, "sha256": "a" * 64, "role": "engine-copy", **kw}

    def test_the_switch_is_off_in_the_product(self):
        assert dc.ENGINE_COPY_ENABLED is False

    def test_the_courier_refuses_a_runner_copy_with_the_right_name_role_and_size(self, tmp_path):
        c = dc.CourierDecisions(lambda: tmp_path / "dec", SimpleNamespace(data={}, save=lambda: None), log, None)
        with pytest.raises(file_transfer.TransferError):
            c.resolve_decision_dest(self.spec())
        assert dc.parse_engine_copy(f"engine-copy-{BUILD}-{KIND}.zip") is None

    def test_a_refused_begin_over_the_real_transfer_door_leaves_nothing_behind(self, tmp_path):
        c = dc.CourierDecisions(lambda: tmp_path / "dec", SimpleNamespace(data={}, save=lambda: None), log, None)
        rx = file_transfer.TransferReceiver(lambda s: tmp_path / "x", log, resolve_decision=c.resolve_decision_dest)
        with pytest.raises(file_transfer.TransferError):
            rx.begin(self.spec())
        assert not any((tmp_path / "dec").rglob("engine-copy*")) if (tmp_path / "dec").exists() else True

    def test_the_report_says_this_device_does_not_take_one_so_the_main_device_downloads_as_before(self):
        assert dc.CourierDecisions.engine_copy_facts()["engineCopy"] is False


@pytest.mark.usefixtures("engine_copy_on")
class TestWhatTheCourierAccepts:
    def courier(self, tmp_path, local=None):
        settings = SimpleNamespace(data={}, save=lambda: None)
        return dc.CourierDecisions(lambda: tmp_path / "dec", settings, log, local)

    def spec(self, name=f"engine-copy-{BUILD}-{KIND}.zip", **kw):
        return {"kind": "decision", "model": "decision-model", "file": name, "size": 1000, "sha256": "a" * 64, "role": "engine-copy", **kw}

    def test_exactly_one_name_shape_with_the_role_is_taken_and_goes_to_the_kept_downloads(self, tmp_path):
        c = self.courier(tmp_path)
        assert c.resolve_decision_dest(self.spec()) == c.store.path_of(f"engine-copy-{BUILD}-{KIND}.zip")

    @pytest.mark.parametrize("change", [dict(file="engine-copy-b11461-../x.zip"), dict(file="engine-copy-b11461-cuda-13.4.exe"), dict(file="engine-copy-b100-cuda-13.4.zip"),
                                        dict(file="engine-copy-b11461-nonsense.zip"), dict(role="model"), dict(size=7 * 1024 ** 3), dict(size=0), dict(size="x"),
                                        dict(file="somethingelse.zip")])
    def test_anything_else_is_refused(self, tmp_path, change):
        with pytest.raises(file_transfer.TransferError):
            self.courier(tmp_path).resolve_decision_dest(self.spec(**change))

    def test_the_name_shape_without_the_role_is_refused_too(self, tmp_path):
        with pytest.raises(file_transfer.TransferError):
            self.courier(tmp_path).resolve_decision_dest(self.spec(role="engine"))

    def test_the_report_says_the_courier_can_take_a_copy_and_what_system_it_is(self, tmp_path):
        f = dc.CourierDecisions.engine_copy_facts()
        assert f == {"engineCopy": True, "engineSystem": "win"}

    @pytest.mark.asyncio
    async def test_a_copy_that_arrived_is_unpacked_instead_of_downloading(self, tmp_path):
        b = make_local(tmp_path, "b", with_engine=False)
        a = make_local(tmp_path, "a")
        z = tmp_path / "dec" / "downloads" / f"engine-copy-{BUILD}-{KIND}.zip"
        a.pack_engine(z)
        c = self.courier(tmp_path, b)
        c.mode = "kept"
        b.download_engine = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download"))
        c._engine_kind = lambda: KIND
        exe = await c._ensure_engine(lambda d, t: None)
        assert exe == b.engine_exe_current() and exe.is_file() and not z.exists(), "the zip is removed once unpacked"

    @pytest.mark.asyncio
    async def test_a_copy_that_cannot_be_used_falls_back_to_the_usual_way(self, tmp_path):
        b = make_local(tmp_path, "b", with_engine=False)
        z = tmp_path / "dec" / "downloads" / f"engine-copy-{BUILD}-{KIND}.zip"
        z.parent.mkdir(parents=True)
        z.write_bytes(b"not a zip")
        c = self.courier(tmp_path, b)
        c.mode = "kept"
        c._engine_kind = lambda: KIND
        with pytest.raises(de.DecisionError) as e:                    # nothing else was sent either, so the usual way says so
            await c._ensure_engine(lambda d, t: None)
        assert "hasn't been copied" in e.value.message and not z.exists()


class FakeClient:
    pass


def make_remote(tmp_path, local, send):
    store = DownloadStore(lambda: tmp_path / "kept")
    logs = []
    r = decision_remote.DecisionRemote(SimpleNamespace(), SimpleNamespace(models={}), store, lambda: "gaming-pc", logs.append, local=local)
    return r, logs, store


def info(kind=KIND, have=False, copy=True, system="win"):
    return {"files": [{"name": "llama-x.zip", "size": 5, "sha256": "b" * 64, "have": have, "role": "engine"},
                      {"name": "m.gguf", "size": 5, "sha256": "c" * 64, "have": False, "role": "model"}],
            "engineKind": kind, "engineCopy": copy, "engineSystem": system}


class TestTheMainDeviceSendsItOrFallsBack:
    async def run_it(self, tmp_path, monkeypatch, inf, send=None, local=None):
        local = local or make_local(tmp_path, "a")
        r, logs, store = make_remote(tmp_path, local, send)
        sent = []

        async def fake_send(client, spec, path, progress, cancelled, on_pause=None):
            sent.append((spec, path.name, path.exists()))
            if send:
                raise send
        monkeypatch.setattr(file_transfer, "send_file", fake_send)
        job = decision_remote.RemoteInstall("laptop", "gaming-pc")
        dev = SimpleNamespace(label="MSI", client=object())
        ok = await r._try_engine_copy(dev, job, inf)
        return ok, sent, logs, job, store

    @pytest.mark.asyncio
    async def test_a_matching_runner_is_packed_sent_with_its_own_role_and_removed_here(self, tmp_path, monkeypatch):
        ok, sent, logs, job, store = await self.run_it(tmp_path, monkeypatch, info())
        assert ok is True and len(sent) == 1
        spec, name, existed = sent[0]
        assert name == f"engine-copy-{BUILD}-{KIND}.zip" and existed and spec["role"] == "engine-copy" and spec["kind"] == "decision" and spec["size"] > 0
        assert not any(store.folder.glob("*.zip")), "nothing left over on this device"
        assert [t[2] for t in job.trail] == ["Packing the engine software on this device…", "Copying the engine software from this device to MSI…"]
        assert logs == [f"The engine software ({BUILD}, {KIND}) was copied from this device to MSI instead of downloading it there"]

    @pytest.mark.parametrize("inf", [info(kind="vulkan"), info(copy=False), info(system="linux"), info(have=True), {"files": [], "engineKind": None}])
    @pytest.mark.asyncio
    async def test_a_different_kind_an_old_courier_linux_or_a_runner_already_there_sends_nothing(self, tmp_path, monkeypatch, inf):
        ok, sent, _, _, _ = await self.run_it(tmp_path, monkeypatch, inf)
        assert ok is False and sent == []

    @pytest.mark.asyncio
    async def test_no_runner_here_sends_nothing(self, tmp_path, monkeypatch):
        ok, sent, _, _, _ = await self.run_it(tmp_path, monkeypatch, info(), local=make_local(tmp_path, "empty", with_engine=False))
        assert ok is False and sent == []

    @pytest.mark.asyncio
    async def test_a_failed_send_says_so_in_the_log_and_the_usual_download_runs(self, tmp_path, monkeypatch):
        ok, sent, logs, _, store = await self.run_it(tmp_path, monkeypatch, info(), send=file_transfer.TransferError(507, "no room"))
        assert ok is False and len(sent) == 1 and "Using the usual download instead" in logs[0] and not any(store.folder.glob("*.zip"))

    @pytest.mark.asyncio
    async def test_a_cancel_is_not_a_fallback(self, tmp_path, monkeypatch):
        with pytest.raises(file_transfer.SendCancelled):
            await self.run_it(tmp_path, monkeypatch, info(), send=file_transfer.SendCancelled())
