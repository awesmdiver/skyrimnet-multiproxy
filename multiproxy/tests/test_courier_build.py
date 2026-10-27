"""The Courier's build number and an older Courier (2026-10-06): the main device can tell which device's Courier is out of date, and says so plainly, for the Other devices
tab, the Overview, and any voice that device cannot run (a Courier built before Qwen3-TTS and Chatterbox knows Higgs only).
"""
import re

import pytest

import courier as cr
import other_pcs as op
import overview
import voice_courier
from tests.test_overview import base
from tests.test_voice_courier import PC, rig  # noqa: F401  (rig is a fixture)

pytestmark = pytest.mark.asyncio


class TestTheFacts:
    async def test_a_courier_with_no_number_or_a_lower_one_is_older(self):
        assert op.courier_is_older({"courier": {"version": "1.0.0"}}) is True
        assert op.courier_is_older({"courier": {"build": 0}}) is True
        assert op.courier_is_older({"courier": {"build": op.MIN_COURIER_BUILD}}) is False
        assert op.courier_is_older({"courier": {"build": op.MIN_COURIER_BUILD + 5}}) is False
        assert op.courier_is_older(None) is False and op.courier_is_older({}) is False, "nothing reported yet: not called old"

    async def test_this_courier_is_not_older_than_the_main_device_that_ships_with_it(self):
        assert cr.COURIER_BUILD >= op.MIN_COURIER_BUILD
        assert cr.COURIER_BUILD == 11 and op.MIN_COURIER_BUILD == 1, "build 11 is the final 4.1 Courier (the Courier start program, engine wording, Brotli); build 10 is the 1.10 test build (download_meter.py joined the Courier files); build 9 carries the moved-service start and the Linux process-group fix (1.9); build 8 the security round three fixes; build 7 was the first with one number (1.7); build 6 was the first with the updater; the oldest the main device supports is unchanged"

    async def test_the_voices_a_courier_can_run(self):
        assert op.courier_voice_models({"voice": {"supportedModels": ["higgs", "chatterbox"]}}) == ["higgs", "chatterbox"]
        assert op.courier_voice_models({"voice": {"licenses": {"higgs": True, "qwen3tts": False}}}) == ["higgs", "qwen3tts"]
        assert op.courier_voice_models({"voice": {"state": "stopped"}}) == ["higgs"], "an old Courier that says nothing runs Higgs only"
        assert op.courier_voice_models({"courier": {}}) == [], "no voice block: no voice"

    async def test_the_license_answer_points_at_the_device_that_is_older(self):
        assert voice_courier.license_mismatch_text("2", "1") == "The Courier on this device needs an update."
        assert "MultiProxy on your main device needs to be updated." in voice_courier.license_mismatch_text("1", "2")
        assert voice_courier.license_mismatch_text("x", "1").endswith("The Courier on this device needs an update.")


class TestTheCourierReportsItsBuild:
    async def test_the_report_carries_the_build_and_the_voices(self, rig):
        rep = await rig.courier.report(fresh=True)
        assert rep["courier"]["build"] == cr.COURIER_BUILD and "built" in rep["courier"]
        assert rep["voice"]["supportedModels"] == ["higgs", "qwen3tts", "chatterbox"]
        j = [s for s in rig.remotes.all() if s.id == PC][0].to_json()
        assert j["courierBuild"] == cr.COURIER_BUILD and j["courierOlder"] is False

    async def test_the_stamp_is_read_from_the_file_next_to_the_courier(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, "__file__", str(tmp_path / "courier.py"))
        assert cr.read_built() == ""
        (tmp_path / "courier-build.txt").write_text("2026-10-06-abc1234\n", encoding="utf-8")
        assert cr.read_built() == "2026-10-06-abc1234"


def make_old(rig):
    """What a Courier built before the build number and the new voices reports: no build, and a voice block without the list of voices or the licenses by voice."""
    rep = rig.remote.report
    rep["courier"].pop("build", None)
    rep["courier"].pop("built", None)
    rep["voice"].pop("supportedModels", None)
    rep["voice"].pop("licenses", None)


class TestAnOldCourierAskedForAVoiceItCannotRun:
    async def test_chatterbox_and_qwen_are_refused_with_the_right_device_named(self, rig):
        make_old(rig)
        for key, name in (("chatterbox", "Chatterbox voice"), ("qwen3tts", "Qwen3-TTS voice")):
            r = await rig.client.post("/config/voice/pc/license", json={"id": PC, "model": key})
            assert r.status_code == 409, r.text
            assert r.json()["detail"] == f"Courier on {PC} needs an update."
            r = await rig.client.post("/config/voice/pc/install", json={"id": PC, "source": "gaming-pc", "model": key})
            assert r.status_code == 409 and "needs an update" in r.json()["detail"] and "gaming" not in r.json()["detail"].lower()

    async def test_higgs_still_works_on_it(self, rig):
        make_old(rig)
        r = await rig.client.post("/config/voice/pc/license", json={"id": PC, "model": "higgs"})
        assert r.status_code == 200, r.text

    async def test_a_current_courier_takes_every_voice(self, rig):
        for key in ("higgs", "qwen3tts", "chatterbox"):
            assert (await rig.client.post("/config/voice/pc/license", json={"id": PC, "model": key})).status_code == 200

    async def test_the_old_couriers_own_answer_is_replaced_by_the_line_that_names_the_device(self, rig, monkeypatch):
        async def old_answer(*a, **k):
            e = Exception("old")
            e.status, e.message = 409, "That isn't the license version this PC knows. Check for a MultiProxy update on your gaming PC."
            raise e
        monkeypatch.setattr(rig.remote.client, "voice_post", old_answer)
        r = await rig.client.post("/config/voice/pc/license", json={"id": PC, "model": "chatterbox"})
        assert r.status_code == 409 and r.json()["detail"] == f"Courier on {PC} needs an update."

    async def test_the_overview_tells_the_page_which_voices_each_device_can_run(self, rig):
        make_old(rig)
        d = (await rig.client.get("/config/voice/overview")).json()
        p = [x for x in d["pcs"] if x["id"] == PC][0]
        assert p["supportedModels"] == ["higgs"] and p["courierOlder"] is True and p["hasVoice"] is True


class TestTheOverviewRow:
    async def test_an_older_courier_gives_an_amber_row(self):
        pcs = [dict(base()["machines"][1] and {"id": "Laptop", "online": True, "gpu": "RTX 4060", "program": "LM Studio", "models": [], "problem": None, "coveredBy": None,
                                              "usedMb": None, "totalMb": None, "courierOlder": True})]
        d = base(pcs=pcs)
        row = [m for m in d["machines"] if m["name"] == "Laptop"][0]
        assert row["level"] == "warn" and row["word"] == "Courier needs an update"
        pcs[0]["courierOlder"] = False
        assert [m for m in base(pcs=pcs)["machines"] if m["name"] == "Laptop"][0]["word"] == "Online"
