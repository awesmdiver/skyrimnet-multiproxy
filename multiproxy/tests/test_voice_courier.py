"""Higgs voice on another device through the Courier (step 2): a real Courier on a spare port with a FAKE voice engine behind it, a real gaming-device side
(MultiProxy's voice routes, the pairing, the shared file transfer) in front of it. Install with progress, the license (refused, accepted), the relay with
streaming, the unreachable device, moving Higgs between devices, the stale-line rule across the relay, the disk checks on both ends."""
import asyncio
import json
import os
import logging
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import courier as cr
import courier_system as cs
import other_pcs as op
import voice_downloads
import voice_engine as ve
import voice_service as vs
import voice_wiring
from tests.fixtures.courier_fakes import free_port
from tests.test_courier import GPUS, FakeAwake, FakeRegistry
from tests.test_voice_service import multipart, wav
from tests.voice_helpers import fake_launcher, good_hardware, make_engine
from voice_downloads import DownloadStore

log = logging.getLogger("test-voice-courier")
pytestmark = pytest.mark.asyncio
PC = "laptop"
HOST = os.environ.get("VOICE_RIG_HOST", "127.0.0.1")        # (the measuring script reaches the Courier at this device's home-network address instead)


class Rig:
    pass


def make_gaming_engine(tmp_path, like: ve.VoiceEngine, port_range=(28900, 28949)):
    store = DownloadStore(lambda: tmp_path / "g" / "downloads")
    return ve.VoiceEngine(lambda: tmp_path / "g" / "voice", store, builds=like.builds, model=like.model, launcher=fake_launcher,
                          hardware_fn=good_hardware, port_range=port_range)


@pytest_asyncio.fixture
async def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback=False: None)       # the other device is this device in these tests
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "engine-requests.jsonl"))
    rg = Rig()
    rg.tmp = tmp_path
    # ---- the Courier device: a real Courier, with a fake engine behind its voice manager ----
    (tmp_path / "courier").mkdir()
    settings = cr.CourierSettings(tmp_path / "courier" / "courier.json")
    settings.set(port=free_port())
    courier = cr.Courier(settings, log, bind_ips=[HOST], discovery_port=free_port(), keep_awake=cs.KeepAwake(FakeAwake()),
                         startup=cs.StartWithWindows("cmd", registry=FakeRegistry()), gpu_reader=lambda: GPUS, adapters=[])
    c_engine, src, _ = make_engine(tmp_path / "c")
    import voice_courier
    courier.voice = voice_courier.CourierVoice(lambda: tmp_path / "c" / "voice", lambda: tmp_path / "c" / "downloads", log, engine=c_engine,
                                               changed=courier.signal_change)
    await courier.start()
    # ---- the main device: MultiProxy's voice routes, with a fake engine of its own and the same pinned files ----
    g_engine = make_gaming_engine(tmp_path, c_engine)
    store: dict = {}
    remotes = op.RemoteServers(lambda: store, lambda d: store.update(d), log, lambda n: None, lambda: set(), client_factory=__import__("courier_client").CourierClient)
    app = FastAPI()
    rg.handles = voice_wiring.setup(app, lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), lambda: str(tmp_path / "g"), lambda s: s,
                       log, remotes=remotes, this_pc=lambda: "gaming-pc", engine=g_engine, source_factory=lambda: voice_downloads.FolderSource(src))
    rg.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", timeout=60)
    rg.courier, rg.c_engine, rg.g_engine, rg.remotes, rg.src, rg.store = courier, c_engine, g_engine, remotes, src, store
    r = await remotes.add_multiproxy(f"{HOST}:{settings.port}", settings.code, PC)
    for _ in range(100):
        if r.report:
            break
        await asyncio.sleep(0.05)
    rg.remote = r
    rg.requests = lambda: [json.loads(x) for x in (tmp_path / "engine-requests.jsonl").read_text(encoding="utf-8").splitlines()] if (tmp_path / "engine-requests.jsonl").exists() else []
    yield rg
    await rg.client.aclose()
    await remotes.stop_polling()
    await g_engine.close()
    await courier.stop()


async def until(cond, secs=20.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def install(rig, source="gaming-pc"):
    assert (await rig.client.post("/config/voice/pc/license", json={"id": PC})).status_code == 200
    r = await rig.client.post("/config/voice/pc/install", json={"id": PC, "source": source})
    assert r.status_code == 200, r.text
    seen = []
    for _ in range(600):
        j = (await rig.client.get(f"/config/voice/pc/job?id={PC}")).json()
        seen.append(j)
        if j["state"] != "running":
            return j, seen
        await asyncio.sleep(0.05)
    raise AssertionError("the install never finished")


async def speak(client, text, sample=True):
    """A full SkyrimNet round trip; returns (audio or None, sse text)."""
    ref = None
    if sample:
        body, ctype = multipart("femaleeventoned.wav", wav())
        path = (await client.post("/gradio_api/upload", content=body, headers={"content-type": ctype})).json()[0]
        ref = {"path": path, "meta": {"_type": "gradio.FileData"}}
    call = await client.post("/gradio_api/call/generate_audio", json={"data": [text, ref, 0.8]})
    if call.status_code != 200:
        return None, call.text
    res = await client.get(f"/gradio_api/call/generate_audio/{call.json()['event_id']}")
    if not res.text.startswith("event: complete"):
        return None, res.text
    item = json.loads(res.text.split("data: ", 1)[1])[0]
    audio = await client.get(httpx.URL(item["url"]).path)
    return audio.content, res.text


# ---- the Courier reports Higgs, and the license gates the model ---------------------------------------------------------------------------------
class TestCourierSide:
    async def test_the_courier_report_carries_a_voice_block(self, rig):
        rep = await rig.courier.report(fresh=True)
        v = rep["voice"]
        assert v["state"] == "not_installed" and v["licenseAccepted"] is False and v["installed"] is False and "gpuMemoryMb" in v

    async def test_the_fit_says_what_it_needs_and_what_is_already_there(self, rig):
        info = (await rig.remote.client.voice_get("fit"))
        assert info["fit"]["verdict"] in ("ok", "warn") and info["licenseOk"] is False and info["installed"] is False
        names = {f["name"]: f for f in info["files"]}
        assert rig.c_engine.model.name in names and all(f["have"] is False for f in info["files"]) and info["freeDiskBytes"]

    async def test_install_is_refused_until_the_license_is_accepted_for_that_pc(self, rig):
        with pytest.raises(Exception) as e:
            await rig.remote.client.voice_post("install", {"source": "kept"})
        assert "license" in str(getattr(e.value, "message", e.value)).lower()

    async def test_the_model_is_refused_over_the_transfer_until_the_license_but_the_engine_is_not(self, rig):
        import file_transfer
        from voice_engine import LICENSE_VERSION  # noqa: F401
        model = rig.c_engine.model
        spec = {"kind": "voice", "model": "higgs-voice", "file": model.name, "size": model.size, "sha256": model.sha256}
        with pytest.raises(file_transfer.TransferError) as e:
            await rig.remote.client.transfer_begin(spec)
        assert e.value.status == 403 and "license" in e.value.message.lower()
        engine_file = next(iter(rig.c_engine.builds.values())).files[0]
        ok = await rig.remote.client.transfer_begin({"kind": "voice", "model": "higgs-voice", "file": engine_file.name, "size": engine_file.size, "sha256": engine_file.sha256})
        assert ok["state"] == "receiving"
        await rig.remote.client.transfer_cancel(ok["id"])

    async def test_only_the_pinned_files_are_taken_with_the_right_size_and_checksum(self, rig):
        import file_transfer
        engine_file = next(iter(rig.c_engine.builds.values())).files[0]
        for bad in ({"file": "evil.exe", "size": 10, "sha256": "0" * 64}, {"file": engine_file.name, "size": engine_file.size + 1, "sha256": engine_file.sha256},
                    {"file": engine_file.name, "size": engine_file.size, "sha256": "f" * 64}, {"file": "../../x.zip", "size": 1, "sha256": "0" * 64}):
            with pytest.raises(file_transfer.TransferError) as e:
                await rig.remote.client.transfer_begin({"kind": "voice", "model": "higgs-voice", **bad})
            assert e.value.status == 400, bad

    async def test_the_license_record_is_kept_on_both_sides(self, rig):
        await rig.client.post("/config/voice/pc/license", json={"id": PC})
        mine = json.loads((rig.tmp / "g" / "voice" / "voice-license.json").read_text(encoding="utf-8"))["pcs"][PC]
        theirs = json.loads((rig.tmp / "c" / "voice" / "voice-license.json").read_text(encoding="utf-8"))
        assert mine["pc"] == PC and mine["license_version"] == ve.LICENSE_VERSION and mine["accepted_at"]
        assert theirs["license_version"] == ve.LICENSE_VERSION and theirs["from"] == "gaming-pc" and theirs["accepted"] is True
        assert (await rig.client.get(f"/config/voice/pc?id={PC}")).json()["licenseOk"] is True

    async def test_a_license_version_the_pc_does_not_know_is_refused(self, rig):
        with pytest.raises(Exception):
            await rig.remote.client.voice_post("license", {"licenseVersion": "something-else", "from": "x"})


# ---- install, with progress, from the main device --------------------------------------------------------------------------------------------
class TestInstall:
    async def test_install_gets_the_files_over_from_the_gaming_pc_with_real_progress_and_starts_higgs(self, rig):
        job, seen = await install(rig)
        assert job["state"] == "done", job
        trail = rig.handles.remote.jobs[PC].trail
        assert [s for s, _ in trail][0] == "preparing" and "copying" in [s for s, _ in trail] and trail[-1][0] == "installing", "fetched here once, copied, then set up there"
        msgs = " | ".join(m for _, m in trail)
        assert "Downloading the voice files on this device (once)" in msgs and "Copying the voice model from this device to laptop" in msgs and "Copying the voice engine from this device to laptop" in msgs
        await until(lambda: rig.c_engine.state == "ready")
        assert rig.c_engine.installed() and rig.c_engine.state == "ready"
        for spec in [*next(iter(rig.c_engine.builds.values())).files, rig.c_engine.model]:
            assert rig.c_engine.store.has(spec), spec.name
        assert rig.g_engine.store.has(rig.c_engine.model), "kept on the main device too (download once)"
        rep = await rig.courier.report(fresh=True)
        assert rep["voice"]["ready"] is True and rep["voice"]["licenseAccepted"] is True

    async def test_a_second_install_copies_nothing_that_is_already_there(self, rig):
        await install(rig)
        await rig.c_engine.stop()
        rig.c_engine.state = "stopped"
        sent_before = len(rig.requests())
        job, seen = await install(rig)
        assert job["state"] == "done" and "copying" not in [s for s, _ in rig.handles.remote.jobs[PC].trail]

    async def test_the_progress_comes_through_the_couriers_live_report(self, rig):
        # the receiving side's own numbers (what the Courier pushes over the live connection)
        engine_file = next(iter(rig.c_engine.builds.values())).files[0]
        info = await rig.remote.client.transfer_begin({"kind": "voice", "model": "higgs-voice", "file": engine_file.name, "size": engine_file.size, "sha256": engine_file.sha256})
        rep = await rig.courier.report(fresh=True)
        assert rep["voice"]["receiving"]["state"] == "running"
        await rig.remote.client.transfer_cancel(info["id"])

    async def test_download_it_on_that_pc_is_the_other_choice(self, rig, monkeypatch):
        monkeypatch.setattr(voice_downloads, "InternetSource", lambda: voice_downloads.FolderSource(rig.src))
        job, seen = await install(rig, "that-pc")
        assert job["state"] == "done" and not any(j["stage"] == "copying" for j in seen)
        assert rig.c_engine.installed()

    async def test_a_pc_with_no_room_is_refused_before_anything_moves(self, rig):
        big = good_hardware()
        big.free_disk_bytes = 100 * 1024 ** 2
        rig.c_engine._hardware_fn = lambda folder: big
        job, seen = await install(rig)
        assert job["state"] == "error" and "room" in job["error"].lower() and not any(j["stage"] == "copying" for j in seen)

    async def test_this_pc_short_of_room_says_so_and_offers_the_other_way(self, rig, monkeypatch):
        monkeypatch.setattr("voice_remote.VoiceRemote._gaming_free", lambda self: 1024)
        job, _ = await install(rig)
        assert job["state"] == "error" and "doesn't have room for the voice files" in job["error"] and "download them itself instead" in job["error"]

    async def test_a_courier_with_different_pinned_files_asks_for_an_update(self, rig, monkeypatch):
        rig.g_engine.model = ve.FileSpec(rig.g_engine.model.name, rig.g_engine.model.url, rig.g_engine.model.size, "9" * 64)
        job, _ = await install(rig)
        assert job["state"] == "error" and "different version" in job["error"]

    async def test_cancel_stops_it(self, rig, monkeypatch):
        orig = voice_downloads.FolderSource.fetch

        async def slow(self, spec, dest, on_bytes):
            await asyncio.sleep(2)
            await orig(self, spec, dest, on_bytes)
        monkeypatch.setattr(voice_downloads.FolderSource, "fetch", slow)
        await rig.client.post("/config/voice/pc/license", json={"id": PC})
        await rig.client.post("/config/voice/pc/install", json={"id": PC})
        await asyncio.sleep(0.3)
        assert (await rig.client.post("/config/voice/pc/cancel", json={"id": PC})).json() == {"cancelled": True}
        j = {}
        for _ in range(100):
            j = (await rig.client.get(f"/config/voice/pc/job?id={PC}")).json()
            if j["state"] != "running":
                break
            await asyncio.sleep(0.05)
        assert j["state"] == "cancelled"


# ---- the relay, where Higgs runs, and the unreachable device --------------------------------------------------------------------------------
async def installed_rig(rig):
    job, _ = await install(rig)
    assert job["state"] == "done", job
    await until(lambda: rig.c_engine.state == "ready")
    assert await until(lambda: ((rig.remote.report or {}).get("voice") or {}).get("ready"), 10), "the main device has been told it is ready"


async def move_to(rig, pc):
    r = await rig.client.post("/config/voice/location", json={"pc": pc})
    assert r.status_code == 200, r.text
    return r.json()


class TestRelay:
    async def test_a_line_goes_to_the_other_pc_and_the_audio_comes_back(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        before = len(rig.requests())
        audio, sse = await speak(rig.client, "<|emotion:contentment|>Cheers, sweetie.")
        assert audio[:4] == b"RIFF" and sse.startswith("event: complete")
        got = rig.requests()[before:]
        assert len(got) == 1 and got[0]["input"] == "<|emotion:contentment|>Cheers, sweetie." and got[0]["ref_bytes"] == len(wav()), "the line, and the voice sample, reached that device's engine"
        assert got[0]["temperature"] == 0.8

    async def test_the_audio_is_streamed_to_disk_never_held_whole_in_memory(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        res = await __import__("voice_remote").RemoteVoiceEngine(rig.remotes, lambda: PC, lambda: rig.tmp / "g" / "voice" / "work").synthesize("Hello there.", wav())
        assert res.path is not None and res.path.is_file() and res.wav == b"" and res.path.stat().st_size > 1000
        res.path.unlink()

    async def test_the_relay_reports_what_it_adds(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        await speak(rig.client, "Hello there.")
        st = (await rig.client.get("/voice/status")).json()
        assert st["pc"] == PC and st["location"] == PC and st["relayMs"] is not None and st["relayMs"] < 2000

    async def test_the_move_changes_where_lines_go_and_skyrimnet_never_notices(self, rig):
        await installed_rig(rig)
        # the main device's own engine, installed from the same kept files
        rig.g_engine.start_install(voice_downloads.FolderSource(rig.src))
        assert await until(lambda: rig.g_engine.job.state != "running", 60) and rig.g_engine.job.state == "done"
        n_c = len(rig.requests())
        await move_to(rig, "this")
        a1, _ = await speak(rig.client, "Line on this device.")
        after_local = rig.requests()
        assert a1 and any(r["input"] == "Line on this device." for r in after_local), "this device's own engine answered it"
        await move_to(rig, PC)
        a2, _ = await speak(rig.client, "Line on the laptop.")
        assert a2 and any(r["input"] == "Line on the laptop." for r in rig.requests())
        assert (await rig.client.get("/config/voice/location")).json()["location"] == PC
        await move_to(rig, "this")
        assert (await rig.client.get("/config/voice/location")).json()["location"] == "this"

    async def test_an_unknown_pc_cannot_be_chosen(self, rig):
        r = await rig.client.post("/config/voice/location", json={"pc": "nowhere"})
        assert r.status_code == 404

    async def test_the_health_check_goes_through_the_relay_without_the_engine(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        before = len(rig.requests())
        call = await rig.client.post("/gradio_api/call/generate_audio", json={"data": ["ping.", {"path": ""}, "en-us", 0.8, 0.8, 4294967295]})
        res = await rig.client.get(f"/gradio_api/call/generate_audio/{call.json()['event_id']}")
        assert res.text.startswith("event: complete") and len(rig.requests()) == before

    async def test_a_line_the_engine_cannot_finish_is_retried_through_the_relay_then_fails_cleanly(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        before = len(rig.requests())
        audio, sse = await speak(rig.client, "TL3 <|emotion:fear|>Stay close.")
        assert audio is None and vs.COULD_NOT_FINISH in sse
        assert len(rig.requests()) - before == 3, "the same three tries as on this device"
        audio, _ = await speak(rig.client, "TL1 Stay close.")
        assert audio, "a fresh seed worked, across the relay"

    async def test_a_runaway_line_is_cut_across_the_relay_too(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        audio, _ = await speak(rig.client, "RUNAWAY the water is warm and there is room enough")
        import io, wave
        with wave.open(io.BytesIO(audio), "rb") as w:
            assert w.getnframes() / w.getframerate() < 12


class TestUnreachable:
    async def test_a_stopped_courier_gives_skyrimnet_an_immediate_clean_error_naming_the_pc(self, rig):
        await installed_rig(rig)
        await move_to(rig, PC)
        await rig.courier.stop()
        await until(lambda: rig.remote.online is False, 10)
        t = time.time()
        audio, text = await speak(rig.client, "Hello.", sample=False)
        assert audio is None and time.time() - t < 3 and PC in text and "isn't answering" in text
        st = (await rig.client.get("/voice/status")).json()
        assert st["state"] == "unreachable" and st["pc"] == PC and PC in st["detail"] and st["ready"] is False

    async def test_a_pc_that_is_there_but_has_no_higgs_says_so(self, rig):
        await move_to(rig, PC)
        audio, text = await speak(rig.client, "Hello.", sample=False)
        assert audio is None and f"Higgs voice isn't installed on {PC}." in text

    async def test_a_stopped_higgs_on_that_pc_says_to_start_it(self, rig):
        await installed_rig(rig)
        await rig.client.post("/config/voice/pc/stop", json={"id": PC})
        await move_to(rig, PC)
        await until(lambda: not (rig.remote.report or {}).get("voice", {}).get("ready"), 10)
        audio, text = await speak(rig.client, "Hello.", sample=False)
        assert audio is None and f"Higgs voice isn't running on {PC}. Start it in MultiProxy." in text

    async def test_a_pc_removed_from_the_list_while_chosen_is_said_plainly(self, rig):
        await move_to(rig, PC)
        rig.remotes.remove(PC)
        audio, text = await speak(rig.client, "Hello.", sample=False)
        assert audio is None and "isn't in your list of devices anymore" in text


class TestStaleAcrossTheRelay:
    async def test_the_wait_is_counted_from_when_multiproxy_received_the_line(self, rig, monkeypatch):
        await installed_rig(rig)
        await move_to(rig, PC)
        # slow lines on the other device: the second waits behind the first and goes stale at MultiProxy, before the engine could start it
        c_engine = rig.c_engine
        orig = c_engine.open_speech

        import contextlib

        @contextlib.asynccontextmanager
        async def slow(*a, **k):
            await asyncio.sleep(2.0)
            async with orig(*a, **k) as r:
                yield r
        monkeypatch.setattr(c_engine, "open_speech", slow)
        monkeypatch.setattr(vs, "STALE_AFTER_SECONDS", 1.5)
        rig.handles.service.stale_after = 1.5
        a, b = await asyncio.gather(speak(rig.client, "First line here.", sample=False), speak(rig.client, "Second line here.", sample=False))
        results = [a[0] is not None, b[0] is not None]
        assert results.count(True) == 1 and vs.STALE in (a[1] if a[0] is None else b[1])


# ---- the license is asked once; a file that fails its check can be fetched again (voice setup in three steps) ----------------------------------------
class TestSetupInThreeSteps:
    async def test_the_license_accepted_on_this_pc_covers_the_courier_without_asking_again(self, rig):
        assert (await rig.client.post("/config/voice/license")).status_code == 200
        r = await rig.client.post("/config/voice/pc/install", json={"id": PC})
        assert r.status_code == 200, r.text
        assert rig.handles.remote.license_ok(PC) and rig.courier.voice.license_ok()
        for _ in range(600):
            if (await rig.client.get(f"/config/voice/pc/job?id={PC}")).json()["state"] != "running":
                break
            await asyncio.sleep(0.05)

    async def test_a_device_that_lost_its_license_record_is_told_again_when_next_is_pressed(self, rig):
        """The main device still remembers telling the laptop, but the laptop's own record is gone (reinstalled, wiped): Next must send it again, not stop on the red line."""
        assert (await rig.client.post("/config/voice/license")).status_code == 200
        assert (await rig.client.post("/config/voice/pc/license", json={"id": PC})).status_code == 200
        rig.courier.voice.license_file.unlink()
        rig.courier.voice.invalidate()
        assert rig.handles.remote.license_ok(PC) and not rig.courier.voice.license_ok()
        r = await rig.client.post("/config/voice/pc/install", json={"id": PC})
        assert r.status_code == 200, r.text
        assert rig.courier.voice.license_ok()
        for _ in range(600):
            if (await rig.client.get(f"/config/voice/pc/job?id={PC}")).json()["state"] != "running":
                break
            await asyncio.sleep(0.05)

    async def test_a_device_that_already_holds_the_license_is_not_told_twice(self, rig):
        assert (await rig.client.post("/config/voice/license")).status_code == 200
        assert (await rig.client.post("/config/voice/pc/license", json={"id": PC})).status_code == 200
        assert await rig.handles.remote.ensure_license(PC) is False

    async def test_a_voices_license_is_its_own_for_the_device(self, rig):
        """Accepting one voice on a device never accepts another."""
        assert (await rig.client.post("/config/voice/pc/license", json={"id": PC})).status_code == 200
        assert rig.handles.remote.license_ok(PC, "higgs") and not rig.handles.remote.license_ok(PC, "chatterbox")
        assert rig.courier.voice.license_ok("higgs") and not rig.courier.voice.license_ok("chatterbox")

    async def test_the_red_line_says_what_to_do(self):
        import voice_courier
        assert "Next: Download" in voice_courier.LICENSE_REQUIRED and "main device first" not in voice_courier.LICENSE_REQUIRED

    async def test_no_license_anywhere_is_still_refused(self, rig):
        r = await rig.client.post("/config/voice/pc/install", json={"id": PC})
        assert r.status_code == 403

    async def test_a_newer_license_asks_again(self, rig, monkeypatch):
        await rig.client.post("/config/voice/license")
        monkeypatch.setattr(voice_wiring, "LICENSE_VERSION", "a-newer-one")
        assert (await rig.client.post("/config/voice/pc/install", json={"id": PC})).status_code == 403

    async def test_a_failed_check_names_the_file_and_download_it_again_forgets_it(self, rig, monkeypatch):
        import file_transfer
        await rig.client.post("/config/voice/license")
        bad = rig.g_engine.model.name

        async def refuse(*a, **k):
            raise file_transfer.TransferError(422, f"{bad} didn't pass its safety check, so the copy was thrown away. Try again.")
        monkeypatch.setattr(file_transfer, "send_file", refuse)
        await rig.client.post("/config/voice/pc/install", json={"id": PC})
        j = {}
        for _ in range(600):
            j = (await rig.client.get(f"/config/voice/pc/job?id={PC}")).json()
            if j["state"] != "running":
                break
            await asyncio.sleep(0.05)
        assert j["state"] == "error" and j["badFile"] == bad
        assert rig.g_engine.store.path_of(bad).is_file()
        monkeypatch.undo()
        monkeypatch.setattr("voice_remote.VoiceRemote._get_files_over", _noop_then_stop)
        r = await rig.client.post("/config/voice/redownload", json={"pc": PC, "file": bad})
        assert r.status_code == 200
        assert not rig.g_engine.store.path_of(bad).exists(), "the kept copy was forgotten, so the next run fetches it fresh"

    async def test_download_it_again_only_for_a_voice_file_and_after_the_license(self, rig):
        assert (await rig.client.post("/config/voice/redownload", json={"pc": PC, "file": "notes.txt"})).status_code == 400
        assert (await rig.client.post("/config/voice/redownload", json={"pc": PC, "file": rig.g_engine.model.name})).status_code == 403


async def _noop_then_stop(self, r, job, info):
    raise voice_remote_cancel()


def voice_remote_cancel():
    import voice_remote
    return voice_remote._Cancelled()
