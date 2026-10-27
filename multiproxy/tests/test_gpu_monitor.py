"""Graphics memory monitor, the quieter model rows' fit rules, and the picker for every device (2026-10-01).

Parsing uses output captured from the dev device (Windows `typeperf`, `tasklist`, `nvidia-smi`); the rest runs
against fake readers so no test touches a real graphics card or starts a real engine.
"""
import asyncio
import datetime
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

import gpu_monitor as gm
import local_models as lm
import other_pcs as op
from test_other_pcs import allow_any_host, door, free_port, own_server  # noqa: F401  (fixtures)

GIB = 1024 ** 3
log = logging.getLogger("test-gpu")

# Captured with Skyrim running on the dev device (trimmed): SkyrimSE.exe (pid 4242) 20.0 GB, a Gemma engine
# (pid 7777) 9.1 GB, one process reporting nonsense (200 GB), the built-in graphics adapter (a second luid).
TYPEPERF = (
    '"(PDH-CSV 4.0)",'
    '"\\\\AREA-51\\GPU Process Memory(pid_4242_luid_0x00000000_0x00015379_phys_0)\\Dedicated Usage",'
    '"\\\\AREA-51\\GPU Process Memory(pid_7777_luid_0x00000000_0x00015379_phys_0)\\Dedicated Usage",'
    '"\\\\AREA-51\\GPU Process Memory(pid_9999_luid_0x00000000_0x00015379_phys_0)\\Dedicated Usage",'
    '"\\\\AREA-51\\GPU Process Memory(pid_1111_luid_0x00000000_0x00015379_phys_0)\\Dedicated Usage",'
    '"\\\\AREA-51\\GPU Process Memory(pid_1111_luid_0x00000000_0x00017384_phys_0)\\Dedicated Usage",'
    '"\\\\AREA-51\\GPU Process Memory(pid_2222_luid_0x00000000_0x00017384_phys_0)\\Dedicated Usage"\n'
    '"09/30/2026 20:10:22.818","21474836480.000000","9771507712.000000","214748364800.000000","6152192.000000","1048576.000000","2097152.000000"\n'
)
TASKLIST = '"System","4","Services","0","1,000 K"\n"SkyrimSE.exe","4242","Console","1","3,000,000 K"\n"chrome.exe","555","Console","1","100,000 K"\n'


# ---- parsing ---------------------------------------------------------------------------------------------
class TestParsing:
    def test_per_process_memory_for_the_main_card_in_mb(self):
        got = gm.parse_typeperf_gpu_process_memory(TYPEPERF)
        assert got[4242] == 20480 and got[7777] == 9318 and got[1111] == 5, "the second adapter's bytes aren't added"
        assert 2222 not in got, "the built-in adapter's own processes are not 'the card'"

    def test_garbage_or_empty_gives_nothing(self):
        assert gm.parse_typeperf_gpu_process_memory("") == {}
        assert gm.parse_typeperf_gpu_process_memory("ERROR: nothing") == {}
        assert gm.parse_typeperf_gpu_process_memory('"(PDH-CSV 4.0)","\\\\X\\Other(thing)\\Counter"\n"09/30/2026 1:00:00.000","5"\n') == {}

    def test_finds_skyrim_by_program_name_and_ignores_the_rest(self):
        assert gm.parse_tasklist_pids(TASKLIST) == [4242]
        assert gm.parse_tasklist_pids('"SkyrimVR.exe","99","Console","1","1 K"\n') == [99]
        assert gm.parse_tasklist_pids("") == []

    def test_nvidia_totals_pick_the_biggest_card(self):
        assert gm.parse_nvidia_memory("6299, 32607\n") == (6299, 32607)
        assert gm.parse_nvidia_memory("100, 2048\n9000, 24564\n") == (9000, 24564)
        assert gm.parse_nvidia_memory("") is None and gm.parse_nvidia_memory("N/A, N/A") is None

    def test_nvidia_compute_apps(self):
        assert gm.parse_nvidia_compute_apps("7777, 9318\n1234, 500\n") == {7777: 9318, 1234: 500}
        assert gm.parse_nvidia_compute_apps("") == {}


# ---- putting the readings together -----------------------------------------------------------------------------
class TestBuildSample:
    def test_the_directors_numbers(self):
        # 32 GB card: Skyrim 20.0 GB, Gemma 12B 9.1 GB, 2 GB of everything else, 0.7 GB free
        s = gm.build_sample(32607, 32607 - 717, {4242: 20480, 7777: 9318, 1: 2000}, [4242], {"gemma-4-12b": 7777})
        assert s.skyrim_mb == 20480 and s.models == {"gemma-4-12b": 9318} and s.other_mb == 32607 - 717 - 20480 - 9318 and s.free_mb == 717
        assert s.skyrim_running

    def test_a_nonsense_reading_is_ignored_and_lands_in_everything_else(self):
        s = gm.build_sample(32607, 20000, {4242: 9000, 9999: 204800}, [4242], {})
        assert s.skyrim_mb == 9000 and s.other_mb == 11000 and s.used_mb == 20000

    def test_nonsense_for_skyrim_itself_means_unknown_not_a_giant_bar(self):
        s = gm.build_sample(32607, 10000, {4242: 204800}, [4242], {})
        assert s.skyrim_running and s.skyrim_mb is None and s.other_mb == 10000

    def test_nothing_is_ever_bigger_than_the_card(self):
        s = gm.build_sample(8192, 99999, {4242: 8000, 7777: 8000}, [4242], {"m": 7777})
        assert s.used_mb == 8192 and s.free_mb == 0 and s.used_mb <= s.total_mb

    def test_skyrim_not_running(self):
        s = gm.build_sample(32607, 3000, {}, [], {})
        assert not s.skyrim_running and s.skyrim_mb is None and s.other_mb == 3000 and s.free_mb == 29607

    def test_without_per_process_figures_there_is_only_a_total(self):
        s = gm.build_sample(24564, 9000, None, [], {"gemma": 1234})
        assert s.models == {} and s.other_mb == 9000 and s.free_mb == 15564

    def test_two_running_models_get_a_segment_each(self):
        s = gm.build_sample(32607, 20000, {1: 8000, 2: 9000}, [], {"a": 1, "b": 2})
        assert s.models == {"a": 8000, "b": 9000} and s.other_mb == 3000

    def test_when_text(self):
        today = datetime.date(2026, 10, 1)
        assert gm.when_text("2026-10-01", today) == "today" and gm.when_text("2026-09-30", today) == "yesterday"
        assert gm.when_text("2026-09-12", today) == "on 12 Sep" and gm.when_text("junk", today) == "" and gm.when_text(None, today) == ""


# ---- the fit rules -------------------------------------------------------------------------------------------------
THE_BIG_THREE = [m for m in lm.CATALOG if m["id"] in ("gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-31b")]       # the two small Gemmas fit everywhere these checks look
BIG_IDS = ("gemma-4-12b", "gemma-4-26b-a4b", "gemma-4-31b")
GPU32 = lm.GpuInfo("NVIDIA GeForce RTX 5090", 32607, "nvidia")


class TestFitWithTheRealPeak:
    def _fits(self, peak_gb):
        return [lm.fit_check(lm.model_total_bytes(m["files"]), GPU32, True, int(peak_gb * 1024)) for m in THE_BIG_THREE]

    def test_the_directors_numbers_12b_fits_and_the_two_big_ones_do_not(self):
        assert self._fits(20) == ["fits", "too_big", "too_big"]

    def test_with_no_peak_known_the_guess_says_everything_fits_on_this_card(self):
        assert [lm.fit_check(lm.model_total_bytes(m["files"]), GPU32, True, None) for m in THE_BIG_THREE] == ["fits"] * 3

    def test_a_quiet_game_leaves_room_for_more(self):
        assert self._fits(8) == ["fits", "fits", "fits"]

    def test_the_rule_itself_free_while_playing_and_a_gb_of_margin(self):
        gpu = lm.GpuInfo("x", 16 * 1024, "nvidia")
        peak_mb = 6 * 1024                      # free = 16 - 6 - 2 = 8 GiB
        room = lm.WORKING_ROOM_BYTES
        assert lm.fit_check(7 * GIB - room, gpu, True, peak_mb) == "fits"            # exactly free - 1 GiB
        assert lm.fit_check(7 * GIB - room + 1, gpu, True, peak_mb) == "tight"
        assert lm.fit_check(8 * GIB - room, gpu, True, peak_mb) == "tight"           # exactly free
        assert lm.fit_check(8 * GIB - room + 1, gpu, True, peak_mb) == "too_big"

    def test_a_peak_that_leaves_nothing_is_too_big_even_for_a_tiny_model(self):
        assert lm.fit_check(10 ** 8, lm.GpuInfo("x", 8192, "nvidia"), True, 8000) == "too_big"

    def test_a_model_server_ignores_any_peak(self):
        assert lm.fit_check(18 * GIB, lm.GpuInfo("x", 24576, "nvidia"), reserve_skyrim=False, skyrim_peak_mb=20000) == "fits"

    def test_the_guess_is_a_quarter_of_the_card_capped_at_ten_gb(self):
        assert lm.SKYRIM_SHARE_MAX == 10 * GIB and lm.SKYRIM_SHARE_MIN == 3 * GIB and lm.SKYRIM_SHARE_FRACTION == 0.25
        gpu = lm.GpuInfo("x", 64 * 1024, "nvidia")
        assert lm.fit_check(64 * GIB - 10 * GIB - lm.WORKING_ROOM_BYTES, gpu) == "fits"
        assert lm.fit_check(64 * GIB - 10 * GIB - lm.WORKING_ROOM_BYTES + 1, gpu) == "tight"

    @pytest.mark.asyncio
    async def test_the_snapshot_uses_the_saved_peak_and_a_model_server_does_not(self, tmp_path):
        store = {"gpu_monitor": {"skyrimPeakMb": 20480, "skyrimPeakDate": datetime.date.today().isoformat()}}
        svc = lm.LocalModels(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log,
                             str(tmp_path / "p.json"), gpu_detector=lambda: GPU32)
        snap = await svc.snapshot()
        assert snap["skyrimPeakMb"] == 20480 and [m["fit"] for m in snap["catalog"] if m["id"] in BIG_IDS] == ["fits", "too_big", "too_big"]
        store["model_server"] = True
        snap = await svc.snapshot()
        assert snap["skyrimPeakMb"] is None and [m["fit"] for m in snap["catalog"] if m["id"] in BIG_IDS] == ["fits"] * 3


# ---- the monitor: peak saving, reset, payload ------------------------------------------------------------------------
def make_monitor(store, sample, models=None):
    local = SimpleNamespace(servers={}, _gpu=GPU32, installed=lambda: models or [])
    calls = {"n": 0}

    def reader():
        calls["n"] += 1
        return sample() if callable(sample) else sample
    mon = gm.GpuMonitor(local, lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log, reader=reader)
    mon.calls = calls
    return mon


RUNNING = gm.build_sample(32607, 31890, {4242: 20480, 7777: 9318}, [4242], {"gemma-4-12b": 7777})


@pytest.mark.asyncio
class TestMonitor:
    async def test_the_peak_is_learned_saved_with_the_date_and_only_ever_goes_up(self):
        store = {}
        mon = make_monitor(store, RUNNING)
        await mon.sample_for_peak()
        assert mon.skyrim_peak_mb == 20480 and mon.skyrim_peak_date == datetime.date.today().isoformat()
        assert store["gpu_monitor"]["skyrimPeakMb"] == 20480
        mon._reader = lambda: gm.build_sample(32607, 9000, {4242: 9000}, [4242], {})
        await mon.sample_for_peak()
        assert mon.skyrim_peak_mb == 20480, "a quieter moment never lowers it"
        mon._reader = lambda: gm.build_sample(32607, 25000, {4242: 22000}, [4242], {})
        await mon.sample_for_peak()
        assert mon.skyrim_peak_mb == 22000

    async def test_nothing_is_learned_while_skyrim_isnt_running(self):
        store = {}
        mon = make_monitor(store, gm.build_sample(32607, 3000, {}, [], {}))
        await mon.sample_for_peak()
        assert mon.skyrim_peak_mb is None and "gpu_monitor" not in store

    async def test_reset_forgets_it(self):
        store = {"gpu_monitor": {"skyrimPeakMb": 20480, "skyrimPeakDate": datetime.date.today().isoformat()}, "other": 1}
        mon = make_monitor(store, RUNNING)
        mon.reset_peak()
        assert mon.skyrim_peak_mb is None and store == {"other": 1}

    async def test_a_model_server_never_looks_for_skyrim(self):
        store = {"model_server": True}
        mon = make_monitor(store, RUNNING)
        await mon.sample_for_peak()
        assert mon.calls["n"] == 0 and mon.skyrim_peak_mb is None

    async def test_readings_are_cached_a_moment_so_polling_cant_pile_up(self):
        mon = make_monitor({}, RUNNING)
        await mon.sample(); await mon.sample(); await mon.sample()
        assert mon.calls["n"] == 1
        await mon.sample(force=True)
        assert mon.calls["n"] == 2

    async def test_the_payload_for_the_card(self):
        names = [SimpleNamespace(id="gemma-4-12b", name="Gemma 4 12B")]
        store = {"gpu_monitor": {"skyrimPeakMb": 21914, "skyrimPeakDate": datetime.date.today().isoformat()}}
        p = await make_monitor(store, RUNNING, names).payload()
        assert p["available"] and p["card"] == "NVIDIA GeForce RTX 5090" and p["totalMb"] == 32607
        assert [(g["key"], g["label"], g["mb"]) for g in p["segments"]] == [("skyrim", "Skyrim", 20480), ("model:gemma-4-12b", "Gemma 4 12B", 9318)]
        assert p["skyrimShown"] and p["skyrimRunning"] and p["peakMb"] == 21914 and p["peakWhen"] == "today"
        assert p["otherMb"] + p["freeMb"] + 20480 + 9318 == 32607

    async def test_the_payload_a_model_server_door_sends_has_no_skyrim(self):
        p = await make_monitor({}, RUNNING).payload(include_skyrim=False)
        assert not p["skyrimShown"] and p["peakMb"] is None and all(g["key"] != "skyrim" for g in p["segments"])
        assert p["otherMb"] >= 20480, "Skyrim's memory, if any, is just 'everything else' to a model server"

    async def test_no_reading_means_the_card_hides(self):
        assert await make_monitor({}, None).payload() == {"available": False}

    async def test_a_reader_that_crashes_hides_the_card_instead_of_breaking_the_page(self):
        def boom():
            raise RuntimeError("counters broke")
        assert await make_monitor({}, boom).payload() == {"available": False}

    async def test_the_background_loop_starts_and_stops_cleanly(self):
        mon = make_monitor({}, RUNNING)
        mon.start()
        await asyncio.sleep(0.05)
        await mon.stop()
        assert mon._task is None and mon.calls["n"] >= 1


# ---- the real operating-system readers (smoke: never crash, whatever this device is) ----------------------------------
def test_the_real_reader_returns_a_sample_or_none_never_an_error():
    local = SimpleNamespace(servers={}, _gpu=GPU32, installed=lambda: [])
    mon = gm.GpuMonitor(local, lambda: {}, lambda d: None, log)
    s = mon._read_sync()
    assert s is None or (s.total_mb > 0 and 0 <= s.used_mb <= s.total_mb and s.free_mb >= 0)


# ---- the routes and the picker -----------------------------------------------------------------------------------------
@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


@pytest.fixture
def fake_card(proxy_module, monkeypatch):
    proxy_module.local_svc._gpu, proxy_module.local_svc._gpu_read = GPU32, True
    mon = proxy_module.gpu_monitor_svc
    mon._reader = lambda: RUNNING
    return mon


@pytest.mark.asyncio
class TestMemoryRoute:
    async def test_this_pc(self, client, fake_card):
        d = (await client.get("/config/local-models/memory")).json()
        assert d["pc"] == "this" and d["available"] and d["skyrimShown"] and d["segments"][0]["label"] == "Skyrim"
        assert d["pcs"] == [{"id": "this", "label": "This device · NVIDIA GeForce RTX 5090", "kind": "this", "online": True}]

    async def test_no_reading_hides_the_card(self, client, proxy_module):
        proxy_module.local_svc._gpu, proxy_module.local_svc._gpu_read = GPU32, True
        assert (await client.get("/config/local-models/memory")).json()["available"] is False

    async def test_reset_clears_the_peak_and_the_fit_icons_fall_back(self, client, fake_card, proxy_module):
        proxy_module._save_config({"gpu_monitor": {"skyrimPeakMb": 20480, "skyrimPeakDate": datetime.date.today().isoformat()}})
        assert (await client.get("/config/local-models")).json()["skyrimPeakMb"] == 20480
        assert (await client.post("/config/local-models/memory/reset")).json() == {"status": "reset"}
        assert (await client.get("/config/local-models")).json()["skyrimPeakMb"] is None

    async def test_the_picker_lists_every_pc_but_only_when_there_is_more_than_this_one(self, client, fake_card, door, allow_any_host, own_server, proxy_module):  # noqa: F811
        await proxy_module.remote_svc.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        await proxy_module.remote_svc.add_openai(f"http://127.0.0.1:{own_server['port']}", "den-llama")
        pcs = (await client.get("/config/local-models/memory")).json()["pcs"]
        assert [p["id"] for p in pcs] == ["this", "den-pc", "den-llama"]
        assert pcs[1]["label"] == "den-pc · Test GPU" and pcs[2]["label"] == "den-llama (AI server)"
        assert [p["kind"] for p in pcs] == ["this", "multiproxy", "own"] and all(p["online"] for p in pcs)


@pytest.mark.asyncio
class TestOtherPcsInThePicker:
    async def test_a_model_server_reports_its_own_numbers_with_no_skyrim(self, client, door, allow_any_host, proxy_module):  # noqa: F811
        door._memory_fn = lambda: make_monitor({}, RUNNING).payload(include_skyrim=False)
        await proxy_module.remote_svc.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        d = (await client.get("/config/local-models/memory?pc=den-pc")).json()
        assert d["pc"] == "den-pc" and d["state"] == "ok" and d["available"] and not d["skyrimShown"]
        assert d["totalMb"] == 32607 and all(g["key"] != "skyrim" for g in d["segments"])

    async def test_your_own_ai_server_has_nothing_to_show(self, client, own_server, proxy_module):
        await proxy_module.remote_svc.add_openai(f"http://127.0.0.1:{own_server['port']}", "den-llama")
        assert (await client.get("/config/local-models/memory?pc=den-llama")).json() == {
            **(await client.get("/config/local-models/memory?pc=den-llama")).json(), "state": "own", "name": "den-llama"}

    async def test_an_offline_pc(self, client, door, allow_any_host, proxy_module):  # noqa: F811
        await proxy_module.remote_svc.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        await door.set_enabled(False)
        await proxy_module.remote_svc.poll_all()
        d = (await client.get("/config/local-models/memory?pc=den-pc")).json()
        assert d["state"] == "offline" and d["name"] == "den-pc"
        assert [p for p in d["pcs"] if p["id"] == "den-pc"][0]["online"] is False

    async def test_a_model_server_running_an_older_multiproxy(self, client, door, allow_any_host, proxy_module):  # noqa: F811
        door._memory_fn = None                     # an older MultiProxy: the route answers 404
        await proxy_module.remote_svc.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        assert (await client.get("/config/local-models/memory?pc=den-pc")).json()["state"] == "older"

    async def test_an_unknown_name_falls_back_to_this_pc(self, client, fake_card):
        assert (await client.get("/config/local-models/memory?pc=nobody")).json()["pc"] == "this"


@pytest.mark.asyncio
class TestTheDoorsMemoryRoute:
    async def _get(self, door, code):
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{door.port}/share/memory", headers={op.CODE_HEADER: code} if code else {}) as r:
                return r.status, await r.json(content_type=None)

    async def test_needs_the_pairing_code(self, door):  # noqa: F811
        door._memory_fn = lambda: make_monitor({}, RUNNING).payload(include_skyrim=False)
        assert (await self._get(door, None))[0] == 403
        assert (await self._get(door, "WRONG1"))[0] == 403
        status, data = await self._get(door, door.code)
        assert status == 200 and data["available"] and data["totalMb"] == 32607

    async def test_exposes_only_the_memory_figures(self, door):  # noqa: F811
        door._memory_fn = lambda: make_monitor({}, RUNNING).payload(include_skyrim=False)
        _, data = await self._get(door, door.code)
        assert set(data) == {"available", "card", "totalMb", "usedMb", "segments", "otherMb", "freeMb", "skyrimShown", "skyrimRunning", "peakMb", "peakWhen"}
        assert door.code not in json.dumps(data)

    async def test_a_pc_that_cant_be_read_says_so(self, door):  # noqa: F811
        door._memory_fn = lambda: make_monitor({}, None).payload()
        assert (await self._get(door, door.code))[1] == {"available": False}


# ---- the page ---------------------------------------------------------------------------------------------------------
def test_the_page_has_the_memory_card_the_quieter_rows_and_no_fit_pills(test_client):
    html = test_client.get("/").text
    pane = html[html.index('<div id="paneLocal"'):html.index('<div id="paneQuick"')]
    assert 'id="memCard"' in pane and pane.index('id="memCard"') < pane.index('id="localCard"')
    for needle in ("function memRender", "function lmIcons", "function lmLegend", "Sees pictures", "Tight fit alongside Skyrim",
                   "Too big to run alongside Skyrim. Fine on a spare PC.", "Too big for this device", "Skyrim\\'s highest memory use", "Reset",
                   "Models will only use the free space left over.", "t run Skyrim, so models can use the whole graphics card.",
                   "was connected directly (without the MultiProxy Courier). We can\\'t see its graphics memory here, but its models will still work.",
                   "is offline.", "needs the newer MultiProxy to show this."):
        assert needle in html, needle
    assert "Fits with Skyrim" not in html and "lm-fit-" not in html and "lm-tag" not in html.split("</style>")[1].replace("lm-tag{", "")


def test_only_dots_carry_color_one_yellow_dot_replaces_the_fit_icons(test_client):
    html = test_client.get("/").text
    for gone in ("⛔", "lm-ic.no", "lm-ic.tight", "lm-ic.vis"):
        assert gone not in html, gone
    assert ".lm-dot { margin-left:7px; font-size:0.85rem; cursor:help; color:var(--warn) }" in html
    assert ".lm-ic { margin-left:7px; font-size:0.85rem; cursor:help; color:var(--muted) }" in html, "the eye is plain and muted"
    assert """title="' + esc('Tight fit alongside Skyrim' + why) + '">●</span>""" in html, "the dot says why, with the numbers the verdict used"
    assert "Too big or a tight fit (hover to see why)" in html


def test_the_legend_has_exactly_the_eye_and_the_yellow_dot(test_client):
    html = test_client.get("/").text
    legend = html[html.index("function lmLegend"):]
    legend = legend[:legend.index("\n}\n")]
    assert "Sees pictures" in legend and "Too big or a tight fit (hover to see why)" in legend
    assert legend.count("<span>") == 2


def test_the_download_size_is_right_aligned_with_its_button(test_client):
    html = test_client.get("/").text
    assert ".lm-act { display:flex; flex-direction:column; align-items:flex-end; gap:4px }" in html


def test_polling_only_happens_while_the_local_models_tab_is_open(test_client):
    html = test_client.get("/").text
    fn = html[html.index("async function memRefresh"):]
    fn = fn[:fn.index("\n}\n") + 3]
    assert "if (!memTabOpen()) return;" in fn and "setTimeout(memRefresh" in fn
    assert "memStart()" in html[html.index("function showMainTab"):html.index("function showMainTab") + 4000]          # (the function has grown with the tabs)


def test_download_is_never_disabled_by_the_fit_icon(test_client):
    html = test_client.get("/").text
    assert "m.fit === 'too_big' ? 'disabled'" not in html


# ---- Skyrim's busiest moment expires, goes down, resets, and is shown (2026-10-26) ----
import skyrim_peak as sp


def _day(n):
    return datetime.date(2026, 10, 26) - datetime.timedelta(days=n)


def test_an_old_spike_falls_out_of_the_14_day_window_and_the_figure_can_go_down():
    cfg = {}
    assert sp.record(cfg, 27496, _day(24)) is True                      # the director's old spike
    assert sp.figure(cfg, _day(0)) == (None, None), "24 days old: not known yet, never a stale number"
    sp.record(cfg, 14000, _day(10))
    sp.record(cfg, 9000, _day(1))
    assert sp.figure(cfg, _day(0)) == (14000, _day(10).isoformat())
    assert sp.figure(cfg, _day(-5)) == (9000, _day(1).isoformat()), "once the 14000 day is out, only the lower days remain: the figure comes down"


def test_a_reading_over_the_card_is_ignored_and_an_older_single_figure_counts_as_one_day():
    cfg = {}
    assert sp.record(cfg, 40000, _day(0), card_mb=32768) is False and sp.figure(cfg, _day(0)) == (None, None)
    old = {"gpu_monitor": {"skyrimPeakMb": 20480, "skyrimPeakDate": _day(3).isoformat()}, "set_me_up_measure": {"mb": 9000, "date": _day(1).isoformat()}}
    assert sp.figure(old, _day(0)) == (20480, _day(3).isoformat())
    assert sp.figure(old, _day(-20)) == (None, None), "twenty days on, both are out"


def test_the_monitor_uses_the_window_and_reset_clears_it(monkeypatch):
    store = {"gpu_monitor": {"skyrimPeakMb": 27496, "skyrimPeakDate": "2026-10-02"}}
    mon = gm.GpuMonitor(None, lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), logging.getLogger("t"))
    assert mon.skyrim_peak_mb is None, "an old spike is gone"
    assert mon.note_skyrim(9000) and mon.skyrim_peak_mb == 9000
    mon.reset_peak()
    assert mon.skyrim_peak_mb is None and "gpu_monitor" not in store


def test_the_dot_explains_itself_with_the_numbers_it_used():
    gpu = lm.GpuInfo("x", 32768, "nvidia")
    why = lm.fit_why(5 * lm.GIB, gpu, 27496)
    assert why.startswith("Skyrim peak ") and ", free about " in why and ", needs " in why
    assert lm.fit_why(5 * GIB, gpu, None) == ""
    import local_models_ui
    js = local_models_ui.LOCAL_MODELS_JS
    assert "Skyrim's peak memory: not measured yet" in js and "lmPeakReset" in js and "/config/local-models/memory/reset" in js
