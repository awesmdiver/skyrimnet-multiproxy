"""The graphics-memory warning before a local engine or model download (2026-10-27): plenty of room, tight, Skyrim running, no card, and that the warning never blocks."""
from types import SimpleNamespace

import pytest

import download_room as dr
import gpu_monitor as gm
import local_models as lm

GIB = 1024


def sample(total=32768, skyrim_mb=None, running=False, other=1500, models=None, used=None):
    s = gm.build_sample(total, used if used is not None else (skyrim_mb or 0) + other, {111: skyrim_mb} if skyrim_mb else None, [111] if running else [], {}, source="t")
    if models:
        s.models = models
    return s


NV = SimpleNamespace(vendor="nvidia", memory_mb=32768)


def test_plenty_of_room_says_nothing_for_engine_and_model():
    s = sample(skyrim_mb=9000, running=True)
    assert dr.check("engine", s, NV, 9000, False) == {"warn": False}
    assert dr.check("model", s, NV, 9000, False, model_bytes=int(6e9), model_name="X") == {"warn": False}


def test_tight_next_to_skyrim_warns_for_a_model_and_quotes_the_numbers():
    s = sample(skyrim_mb=27.4 * GIB, running=True, other=900)           # the director's case: about 4 GB left
    r = dr.check("model", s, NV, int(27.4 * GIB), False, model_bytes=int(6e9), model_name="Gemma 4 12B")
    assert r["warn"] and "next to Skyrim" in r["line"] and "Gemma 4 12B" in r["line"] and "Download anyway" not in r["line"][:0]
    assert "3.7 GB" in r["line"] or "3.8 GB" in r["line"]


def test_engine_alone_only_warns_below_the_floor():
    ok = sample(skyrim_mb=27.4 * GIB, running=True, other=900)           # about 3.7 GB left: the engine alone is small
    assert dr.check("engine", ok, NV, int(27.4 * GIB), False) == {"warn": False}
    tight = sample(skyrim_mb=30 * GIB, running=True, other=900)         # under 2 GB left
    r = dr.check("engine", tight, NV, 30 * GIB, False)
    assert r["warn"] and "local model" in r["line"] and r["needMb"] == dr.ENGINE_FLOOR_MB


def test_skyrim_running_and_free_memory_under_the_floor_warns_even_when_the_tracker_figure_is_low():
    s = sample(skyrim_mb=30 * GIB, running=True, other=900)
    assert dr.check("engine", s, NV, 12 * GIB, False)["warn"], "the game is using more than the tracker says: free right now is what counts"


def test_the_trackers_figure_counts_when_skyrim_is_closed():
    s = sample(running=False, other=1500)
    assert dr.check("model", s, NV, 30 * GIB, False, model_bytes=int(6e9), model_name="M")["warn"]
    assert not dr.check("model", s, NV, 12 * GIB, False, model_bytes=int(6e9), model_name="M")["warn"]


def test_no_figure_at_all_uses_the_quarter_of_the_card_guess():
    s = sample(running=False, other=500)
    assert not dr.check("model", s, NV, None, False, model_bytes=int(6e9), model_name="M")["warn"]
    small = sample(total=8192, running=False, other=500)
    assert dr.check("model", small, NV, None, False, model_bytes=int(6e9), model_name="M")["warn"]


def test_a_model_server_has_no_skyrim_to_leave_room_for():
    s = sample(total=24576, other=500)
    assert not dr.check("model", s, NV, 20 * GIB, True, model_bytes=int(6e9), model_name="M")["warn"]
    big = dr.check("model", s, NV, None, True, model_bytes=int(40e9), model_name="Big")
    assert big["warn"] and "next to Skyrim" not in big["line"]


def test_models_already_running_take_their_space():
    s = sample(skyrim_mb=14 * GIB, running=True, other=1000, models={"a": 12 * GIB})
    assert dr.check("model", s, NV, 14 * GIB, False, model_bytes=int(6e9), model_name="M")["warn"]


@pytest.mark.parametrize("gpu", [None, SimpleNamespace(vendor="amd", memory_mb=16384), SimpleNamespace(vendor="intel", memory_mb=8192)])
def test_no_card_or_a_card_we_cannot_read_never_warns(gpu):
    s = sample(skyrim_mb=30 * GIB, running=True)
    assert dr.check("engine", s, gpu, 30 * GIB, False) == {"warn": False}
    assert dr.check("model", s, gpu, 30 * GIB, False, model_bytes=int(6e9)) == {"warn": False}


def test_a_reading_that_cannot_be_taken_never_warns():
    assert dr.check("engine", None, NV, 30 * GIB, False) == {"warn": False}


# ---- the route, and the page's override ----------------------------------------------------------------------
@pytest.fixture
def route(test_client, monkeypatch):
    import multiproxy

    def run(s, gpu=NV, peak=30 * GIB):
        async def fake_gpu(*a, **k):
            return gpu
        async def fake_sample(force=False):
            return s
        monkeypatch.setattr(multiproxy.local_svc, "gpu", fake_gpu)
        monkeypatch.setattr(multiproxy.gpu_monitor_svc, "sample", fake_sample)
        monkeypatch.setattr(multiproxy.gpu_monitor_svc, "_load_cfg", lambda: {"gpu_monitor": {"days": {__import__("datetime").date.today().isoformat(): peak}}})
        return test_client
    return run


def test_the_route_warns_and_never_blocks_the_download_itself(route):
    c = route(sample(skyrim_mb=30 * GIB, running=True))
    r = c.get("/config/local-models/room?what=engine").json()
    assert r["warn"] and r["line"]
    mid = lm.CATALOG[0]["id"]
    assert c.get(f"/config/local-models/room?what=model&id={mid}").json()["warn"]
    assert c.get("/config/local-models/room?what=model&id=nope").json() == {"warn": False}
    # the download route has no memory check of its own: "Download anyway" is just this call (its answer here is only about the folder)
    assert c.post("/config/local-models/engine/download", json={"kind": ""}).status_code != 507


def test_the_page_has_the_override_and_nothing_is_stored():
    import local_models_ui as ui
    js = ui.LOCAL_MODELS_JS
    assert "Download anyway" in js and "lmRoomGo" in js and "lmRoomCancel" in js
    assert "localStorage" not in js[js.index("function lmGuarded") - 600: js.index("function lmDownloadModel(id)") + 200]
