"""AMD card readings (gpu_amd.py): new and unverified, so the tests use a stand-in for AMD's driver library (the same `ADL2_*` calls, filling the same ctypes structures) and fake card
lists. Nothing here touches a real AMD library or this PC's cards."""
import ctypes
import json
import logging
import re
import shutil
import subprocess

import pytest

import benchmarks_sampler as bsm
import fake_gpu_counters as fgc
import gpu_adapters as ga
import gpu_amd as amd
import gpu_counters as gc

GB = 1024 * 1024 * 1024


class FakeAdl:
    """The calls of AMD's library the module makes. `adapters`: [(index, bus, device, function)]; `sensors`: {adapter index: {sensor number: value}}; `fail`: calls that answer an error code."""

    def __init__(self, adapters=((0, 3, 0, 0),), sensors=None, fail=(), version=b"23.40.27.06", raise_in=()):
        self.adapters_, self.sensors_, self.fail, self.version_, self.raise_in = adapters, sensors or {}, set(fail), version, set(raise_in)
        self.calls = []

    def ADL2_Main_Control_Create(self, malloc, mode, ctx):
        self.calls.append("create")
        if "create" in self.fail:
            return -1
        ctx._obj.value = 1234
        return 0

    def ADL2_Adapter_NumberOfAdapters_Get(self, ctx, count):
        count._obj.value = len(self.adapters_)
        return -1 if "count" in self.fail else 0

    def ADL2_Adapter_AdapterInfo_Get(self, ctx, entries, size):
        if "info" in self.fail:
            return -1
        for e, (idx, bus, dev, fn) in zip(entries, self.adapters_):
            e.index, e.bus, e.device, e.function = idx, bus, dev, fn
            e.name = b"AMD Radeon RX 7900 XT"
        return 0

    def ADL2_New_QueryPMLogData_Get(self, ctx, index, metrics):
        self.calls.append(("query", index))
        if "query" in self.raise_in:
            raise OSError("access violation")
        if "query" in self.fail:
            return -5
        m = metrics._obj
        for n, v in (self.sensors_.get(index) or {}).items():
            m.sensors[n].supported, m.sensors[n].value = 1, v
        return 0

    def ADL2_Graphics_Versions_Get(self, ctx, buf):
        if "version" in self.fail:
            return -1
        ctypes.memmove(buf, self.version_ + b"\0", len(self.version_) + 1)
        return 0


FULL = {1: 2400, 2: 1250, 8: 61, 19: 37, 23: 90, 73: 142}
AMD_ROW = {"name": "AMD Radeon RX 7900 XT", "vendor_id": 0x1002, "device_id": 0x744C, "subsys_id": 1, "dedicated": 20 * GB, "luid": 0xBB, "software": False, "pci": (3, 0, 0)}
IGPU_ROW = {"name": "AMD Radeon(TM) Graphics", "vendor_id": 0x1002, "device_id": 0x164E, "subsys_id": 2, "dedicated": GB // 2, "luid": 0xCC, "software": False, "pci": (14, 0, 0)}
NVIDIA_ROW = {"name": "NVIDIA GeForce RTX 5090", "vendor_id": 0x10DE, "device_id": 0x2B85, "subsys_id": 3, "dedicated": 32 * GB, "luid": 0xAA, "software": False, "pci": (1, 0, 0)}


@pytest.fixture
def world(monkeypatch):
    """A Windows with one AMD card and a stand-in library. Returns the stand-in so a test can change it."""
    amd.reset()
    ga.reset()
    monkeypatch.setattr(amd, "_force_windows", True)
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [AMD_ROW, IGPU_ROW])
    fake = FakeAdl(adapters=((0, 3, 0, 0), (1, 14, 0, 0)), sensors={0: dict(FULL)})
    monkeypatch.setattr(amd, "_opener", lambda: amd.AdlLibrary(fake, None))
    yield fake
    amd.reset()
    ga.reset()


# ---- the library ----
def test_every_reading_the_driver_gives_is_read():
    lib = amd.AdlLibrary(FakeAdl(sensors={0: dict(FULL)}), None)
    assert amd.readings_from_sensors(lib.sensors(0)) == {"gpu_util_pct": 37, "temp_c": 61, "power_w": 142, "clock_mhz": 2400, "mem_clock_mhz": 1250}            # board power wins


def test_chip_power_is_used_when_there_is_no_board_power_and_missing_readings_are_none():
    lib = amd.AdlLibrary(FakeAdl(sensors={0: {1: 2400, 23: 88}}), None)
    r = amd.readings_from_sensors(lib.sensors(0))
    assert r == {"gpu_util_pct": None, "temp_c": None, "power_w": 88, "clock_mhz": 2400, "mem_clock_mhz": None}
    assert amd.readings_from_sensors({}) == {k: None for k in ("gpu_util_pct", "temp_c", "power_w", "clock_mhz", "mem_clock_mhz")}


def test_a_failing_call_gives_nothing_never_an_exception():
    for fail in ("query", "count", "info"):
        lib = amd.AdlLibrary(FakeAdl(sensors={0: dict(FULL)}, fail=(fail,)), None)
        assert (lib.sensors(0) if fail == "query" else lib.adapters()) in ({}, [])
    assert amd.AdlLibrary(FakeAdl(sensors={0: dict(FULL)}, raise_in=("query",)), None).sensors(0) == {}          # a fault inside the call
    with pytest.raises(amd.AdlError):
        amd.AdlLibrary(FakeAdl(fail=("create",)), None)


def test_the_driver_version_is_read_when_the_library_has_it():
    assert amd.AdlLibrary(FakeAdl(), None).version() == "23.40.27.06"
    assert amd.AdlLibrary(FakeAdl(fail=("version",)), None).version() is None


def test_the_adapter_is_tied_to_the_card_by_pci_bus_device_and_function():
    infos = amd.AdlLibrary(FakeAdl(adapters=((4, 3, 0, 0), (2, 3, 0, 0), (7, 9, 0, 0))), None).adapters()
    assert amd.match_index(infos, (3, 0, 0)) == 2                      # several logical handles for one card: the lowest
    assert amd.match_index(infos, (9, 0, 0)) == 7 and amd.match_index(infos, (5, 0, 0)) is None and amd.match_index(infos, None) is None


# ---- the rows for the sampler ----
def test_the_row_has_the_readings_and_the_card_memory(world, monkeypatch):
    t = fgc.table([(1, 10, 0)], adapters={0xBB: 8000}, luid=0xBB)
    t[gc.COUNTERS["a_dedicated"][0]] = [(fgc.adapter_inst(0xBB), float(8000 * 1024 * 1024))]
    fgc.install(monkeypatch, fgc.FakeBackend(t))
    rows = amd.read_rows()
    assert len(rows) == 1 and rows[0]["name"] == "AMD Radeon RX 7900 XT"                       # the processor's built-in graphics is not a card to test on
    assert (rows[0]["vram_total_mb"], rows[0]["vram_used_mb"], rows[0]["gpu_util_pct"], rows[0]["temp_c"], rows[0]["power_w"], rows[0]["clock_mhz"]) == (20480, 8000, 37, 61, 142, 2400)
    assert rows[0]["fan_pct"] is None and rows[0]["power_limit_w"] is None and rows[0]["t_limit_margin_c"] is None


def test_some_readings_missing_means_none_for_those(world):
    world.sensors_ = {0: {19: 12}}
    r = amd.read_rows()[0]
    assert r["gpu_util_pct"] == 12 and r["temp_c"] is None and r["power_w"] is None and r["clock_mhz"] is None


def test_no_library_is_no_readings_and_no_error(world, monkeypatch):
    def nolib():
        raise amd.AdlError("no AMD library")
    monkeypatch.setattr(amd, "_opener", nolib)
    amd.reset()
    assert amd.read_rows() == [] and amd.read_rows() == []


def test_a_call_that_errors_still_gives_the_card_with_nothing_to_draw(world):
    world.fail.add("query")
    r = amd.read_rows()[0]
    assert r["name"] == "AMD Radeon RX 7900 XT" and r["temp_c"] is None and r["gpu_util_pct"] is None


def test_no_amd_card_no_rows(monkeypatch):
    amd.reset()
    ga.reset()
    monkeypatch.setattr(amd, "_force_windows", True)
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA_ROW])
    assert amd.read_rows() == []


def test_two_cards_with_the_same_pci_numbers_are_not_guessed(world, monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [AMD_ROW, dict(AMD_ROW, luid=0xDD, subsys_id=9)])
    ga.reset()
    assert amd.read_rows() == []


def test_one_log_line_names_the_card_the_driver_version_and_which_readings_came(world, caplog):
    world.sensors_ = {0: {19: 12, 8: 55}}
    caplog.set_level(logging.INFO, logger="multiproxy.gpu_amd")
    amd.read_rows()
    amd.read_rows()
    lines = [r.getMessage() for r in caplog.records if "AMD card in use" in r.getMessage()]
    assert lines == ["AMD card in use (new, not fully tested): AMD Radeon RX 7900 XT; driver library 23.40.27.06; readings: utilisation yes, clocks no, power no, temperature yes"]


# ---- the sampler ----
SMI = "0, NVIDIA GeForce RTX 5090, 3000, 32607, 10, 50, 100.0, 575.0, 2400, 30\n"


class Runner:
    def __init__(self, text=SMI, status="ok"):
        self.text, self.status = text, status

    def __call__(self, cmd, timeout):
        return (self.status, self.text) if cmd[0] == "nvidia-smi" and "--query-gpu" in " ".join(cmd) else ("ok", "")


def amd_rows(temp=None, util=40):
    return lambda: [{"name": "AMD Radeon RX 7900 XT", "vram_used_mb": 8000, "vram_total_mb": 20480, "gpu_util_pct": util, "temp_c": temp, "power_w": 140, "clock_mhz": 2400,
                     "power_limit_w": None, "fan_pct": None, "t_limit_margin_c": None, "mem_clock_mhz": 1250}]


def sampler(runner, reader, **kw):
    return bsm.ReadingsSampler(runner=runner, machine_reader=lambda: {"cpu_util_pct": 1.0, "ram_used_mb": 1000}, amd_reader=reader, **kw)


def test_the_amd_card_follows_the_nvidia_cards_in_the_same_rows():
    s = sampler(Runner(), amd_rows(temp=60))
    s.tick(0.0)
    gpus = s.snapshot()["gpus"]
    assert [(g["gpu_index"], g["name"]) for g in gpus] == [(0, "NVIDIA GeForce RTX 5090"), (1, "AMD Radeon RX 7900 XT")]
    assert gpus[1]["temp_c"] == 60 and gpus[1]["gpu_util_pct"] == 40 and gpus[0]["gpu_util_pct"] == 10          # NVIDIA unchanged


def test_an_amd_only_pc_does_not_give_up_for_lack_of_nvidia_smi():
    s = sampler(Runner("", "missing"), amd_rows())
    for i in range(10):
        s.tick(float(i))
    snap = s.snapshot()
    assert snap["not_measured"] == "" and [g["name"] for g in snap["gpus"]] == ["AMD Radeon RX 7900 XT"]
    plain = sampler(Runner("", "missing"), lambda: [])
    plain.tick(0.0)
    assert plain.snapshot()["not_measured"] == bsm.REASON_NEEDS_NVIDIA                              # without an AMD card it is exactly as before


def test_a_reader_that_fails_never_reaches_the_sampler():
    def boom():
        raise RuntimeError("driver exploded")
    s = sampler(Runner(), boom)
    s.tick(0.0)
    assert [g["gpu_index"] for g in s.snapshot()["gpus"]] == [0]


def test_a_missing_amd_reading_is_a_not_reported_reason_not_a_zero():
    s = sampler(Runner("", "missing"), amd_rows(temp=None))
    s.tick(0.0)
    assert s.snapshot()["gpus"][0]["temp_c"] is None
    assert s.reasons().get("gpu0.temp_c") == bsm.REASON_NOT_REPORTED


def test_the_heat_stop_ignores_a_missing_temperature_and_uses_a_real_one():
    quiet = sampler(Runner("", "missing"), amd_rows(temp=None), ceiling_c=80.0)
    quiet.tick(0.0)
    assert quiet.thermal_stop()["stop"] is False
    hot = sampler(Runner("", "missing"), amd_rows(temp=95), ceiling_c=80.0)
    hot.tick(0.0)
    stop = hot.thermal_stop()
    assert stop["stop"] is True and stop["temp_c"] == 95 and stop["limit_source"] == "setting"
    no_limit = sampler(Runner("", "missing"), amd_rows(temp=95))
    no_limit.tick(0.0)
    assert no_limit.thermal_stop()["stop"] is False                                                # no limit known for an AMD card and none set: nothing is invented


# ---- the "new and not fully tested" line ----
def run_js(snippet):
    import benchmarks_ui
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed")
    import tempfile
    from pathlib import Path
    prelude = ("function esc(s) { return String(s == null ? '' : s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c])); }\n"
               "const document = { getElementById: () => null, addEventListener() {}, createElement: () => ({ style: {} }), body: { appendChild() {} } }; const window = {};\n"
               "function ovDot(l) { return ''; } async function fetch() { return { ok: true, json: async () => ({}) }; } function setTimeout() { return 1; } function clearTimeout() {}\n")
    f = Path(tempfile.mkdtemp()) / "t.js"
    f.write_text(prelude + benchmarks_ui.BENCHMARKS_JS + "\n(() => { const OUT = {};\n" + snippet + "\nconsole.log(JSON.stringify(OUT)); })();\n", encoding="utf-8")
    r = subprocess.run([node, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_benchmarks_line_is_for_an_amd_card_only_and_links_the_issues_page():
    out = run_js("""
      const nv = { id: 'GPU-1', name: 'RTX 5090', memoryGb: 32, vendor: 'nvidia' }, ad = { id: 'pnp:x', name: 'RX 7900 XT', memoryGb: 20, vendor: 'amd' };
      OUT.nvidia = bmAmdNoteHtml({ cards: [nv] });
      OUT.amd = bmAmdNoteHtml({ cards: [ad] });
      bmSet.gpu = '';
      OUT.mixedAuto = bmAmdNoteHtml({ cards: [nv, ad] });            // Automatic: the biggest real card is NVIDIA
      bmSet.gpu = 'pnp:x';
      OUT.mixedPicked = bmAmdNoteHtml({ cards: [nv, ad] });          // the AMD card was picked
      OUT.none = bmAmdNoteHtml({ cards: [] });
    """)
    assert out["nvidia"] == "" and out["mixedAuto"] == "" and out["none"] == ""
    for key in ("amd", "mixedPicked"):
        h = out[key]
        text = re.sub(r"<[^>]+>", "", h).replace("&#39;", "'")
        assert text == "AMD cards are new in MultiProxy and not fully tested. If something looks wrong, please tell us on the project's Issues page."
        assert 'href="https://github.com/awesmdiver/skyrimnet-multiproxy/issues"' in h and ">Issues page</a>" in h


def test_the_graphics_memory_panel_line_is_for_an_amd_card_only():
    import local_models_ui
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed")
    js = local_models_ui.LOCAL_MODELS_JS
    start = js.index("const MEM_AMD_NEW")
    end = js.index("\n}\n", js.index("function memAmdNote")) + 3
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "m.js"
    f.write_text("function esc(s) { return String(s == null ? '' : s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c])); }\n" + js[start:end] +
                 "\nconsole.log(JSON.stringify([memAmdNote({ vendor: 'nvidia' }), memAmdNote({ vendor: '' }), memAmdNote(null), memAmdNote({ vendor: 'amd' })]));\n", encoding="utf-8")
    r = subprocess.run([node, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    nvidia, blank, none, amd_html = json.loads(r.stdout.strip().splitlines()[-1])
    assert nvidia == "" and blank == "" and none == ""
    assert re.sub(r"<[^>]+>", "", amd_html).replace("&#39;", "'") == "AMD cards are new in MultiProxy and not fully tested. If something looks wrong, please tell us on the project's Issues page."
    assert 'href="https://github.com/awesmdiver/skyrimnet-multiproxy/issues"' in amd_html and "lm-muted" in amd_html


def test_the_memory_panel_data_says_which_maker_the_card_is():
    import asyncio
    from types import SimpleNamespace
    import gpu_monitor as gm
    import local_models as lm
    sample = gm.build_sample(20480, 8000, {}, [], {})
    for vendor in ("amd", "nvidia"):
        local = SimpleNamespace(_gpu=lm.GpuInfo("Some Card", 20480, vendor), installed=lambda: [])
        mon = gm.GpuMonitor(local, lambda: {}, lambda d: None, logging.getLogger("t"), reader=lambda: sample)
        assert asyncio.run(mon.payload())["vendor"] == vendor
