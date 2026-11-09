"""The vendor-neutral list of graphics cards (gpu_adapters.py) and where it is used. Fake readings only: this PC's real adapter list is never read."""
import types

import fake_gpu_counters as fgc
import pytest

import benchmarks_cardusage as bcu
import courier_system as cs
import gpu_adapters as ga
import gpu_choice
import gpu_counters as gc
import gpu_monitor as gm
import local_models as lm

MIB = 1024 * 1024
GB = 1024 * MIB


def row(name, vendor_id, gb, luid, software=False, device=1, subsys=0):
    return {"name": name, "vendor_id": vendor_id, "device_id": device, "subsys_id": subsys, "dedicated": int(gb * GB), "luid": luid, "software": software}


NVIDIA = row("NVIDIA GeForce RTX 5090", 0x10DE, 32, 0xAA, device=0x2B85)
AMD = row("AMD Radeon RX 7900 XT", 0x1002, 20, 0xBB, device=0x744C)
AMD_IGPU = row("AMD Radeon(TM) Graphics", 0x1002, 0.5, 0xCC, device=0x164E)
INTEL = row("Intel(R) Arc(TM) A770 Graphics", 0x8086, 16, 0xDD, device=0x56A0)
BASIC = row("Microsoft Basic Render Driver", 0x1414, 0, 0xEE, software=True)


def cards(rows):
    return ga.list_adapters(platform="win32", dxgi=lambda: rows)


# ---- the list ----
def test_every_maker_is_listed_with_name_vendor_memory_and_adapter_id():
    got = cards([NVIDIA, AMD, INTEL])
    assert [(a.name, a.vendor, a.memory_mb, a.luid) for a in got] == [("NVIDIA GeForce RTX 5090", "nvidia", 32768, 0xAA), ("AMD Radeon RX 7900 XT", "amd", 20480, 0xBB),
                                                                    ("Intel(R) Arc(TM) A770 Graphics", "intel", 16384, 0xDD)]


def test_the_software_adapter_is_skipped_and_none_is_an_empty_list():
    assert [a.name for a in cards([BASIC, AMD])] == ["AMD Radeon RX 7900 XT"]
    assert cards([]) == [] and cards([BASIC]) == []


def test_two_identical_cards_get_different_stable_ids():
    a, b = cards([row("AMD Radeon RX 7900 XT", 0x1002, 20, 1), row("AMD Radeon RX 7900 XT", 0x1002, 20, 2)])
    assert a.stable_id != b.stable_id and a.stable_id.startswith("dxgi:1002:")


def test_an_unreadable_list_is_empty_never_invented():
    def boom():
        raise OSError("no dxgi")
    assert ga.list_adapters(platform="win32", dxgi=boom) == []


def test_linux_reads_amd_from_sysfs_name_and_memory_only(tmp_path):
    for n, vendor, mem, prod in (("card0", "0x1002", "21458001920", "AMD Radeon RX 7900 XT"), ("card1", "0x10de", None, None), ("card2", "0x1002", None, None)):
        d = tmp_path / n / "device"
        d.mkdir(parents=True)
        (d / "vendor").write_text(vendor + "\n")
        if mem:
            (d / "mem_info_vram_total").write_text(mem + "\n")
        if prod:
            (d / "product_name").write_text(prod + "\n")
    (tmp_path / "card0-DP-1").mkdir()                                              # a connector, not a card
    got = ga.amd_from_sysfs(str(tmp_path))
    assert [(a.name, a.memory_mb) for a in got] == [("AMD Radeon RX 7900 XT", 20463), ("AMD graphics card", None)]        # NVIDIA is not read here; no memory -> None, not 0


# ---- the card the app uses ----
def fake_run_factory(smi=None):
    def run(cmd, **kw):
        out = smi if cmd[0] == "nvidia-smi" and smi is not None else None
        return types.SimpleNamespace(returncode=0 if out is not None else 1, stdout=out or "")
    return run


def test_an_amd_only_pc_gets_its_card_with_name_and_memory(monkeypatch):
    monkeypatch.setattr(lm, "_platform", lambda: "win32")
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [AMD, AMD_IGPU, BASIC])
    g = lm.detect_gpu(fake_run_factory())
    assert (g.name, g.vendor, g.memory_mb, g.luid) == ("AMD Radeon RX 7900 XT", "amd", 20480, 0xBB)


def test_an_nvidia_only_pc_is_exactly_as_before(monkeypatch):
    monkeypatch.setattr(lm, "_platform", lambda: "win32")
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA])
    g = lm.detect_gpu(fake_run_factory("NVIDIA GeForce RTX 5090, 32607\n"))
    assert (g.name, g.vendor, g.memory_mb, g.source) == ("NVIDIA GeForce RTX 5090", "nvidia", 32607, "nvidia-smi") and g.luid == 0xAA      # only the adapter id is new


def test_nvidia_plus_an_amd_built_in_card_keeps_the_nvidia_card(monkeypatch):
    monkeypatch.setattr(lm, "_platform", lambda: "win32")
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA, AMD_IGPU])
    g = lm.detect_gpu(fake_run_factory("NVIDIA GeForce RTX 5090, 32607\n"))
    assert g.vendor == "nvidia" and g.memory_mb == 32607


def test_no_card_at_all_says_so_the_way_it_does_now(monkeypatch):
    monkeypatch.setattr(lm, "_platform", lambda: "win32")
    monkeypatch.setattr(lm, "_registry_gpus", lambda: [])
    assert lm.detect_gpu(fake_run_factory()) is None


def test_the_card_list_for_the_picker_shows_an_amd_card_with_its_full_memory(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA, AMD, INTEL])

    def run(cmd, **kw):
        if cmd[0] == "nvidia-smi":
            return types.SimpleNamespace(returncode=0, stdout="GPU-abc, NVIDIA GeForce RTX 5090, 32607\n")
        ps = '[{"Name":"NVIDIA GeForce RTX 5090","PNPDeviceID":"PCI\\\\A","AdapterRAM":4294967295},{"Name":"AMD Radeon RX 7900 XT","PNPDeviceID":"PCI\\\\B","AdapterRAM":4293918720}]'
        return types.SimpleNamespace(returncode=0, stdout=ps)
    got = gpu_choice.list_cards(run, platform="win32")
    by = {c.name: c for c in got}
    assert by["AMD Radeon RX 7900 XT"].memory_mb == 20480 and by["AMD Radeon RX 7900 XT"].id == "pnp:PCI\\B"       # the old 4 GB figure is replaced; the remembered id is unchanged
    assert by["Intel(R) Arc(TM) A770 Graphics"].memory_mb == 16384                                                # a card the other list missed is added
    assert by["NVIDIA GeForce RTX 5090"].id == "GPU-abc" and len(got) == 3


def test_the_card_list_still_works_when_the_other_list_cannot_be_read(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [AMD])
    got = gpu_choice.list_cards(lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout=""), platform="win32")
    assert [(c.name, c.memory_mb, c.vendor) for c in got] == [("AMD Radeon RX 7900 XT", 20480, "amd")]


def test_the_couriers_report_lists_the_other_cards_without_made_up_readings(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA, AMD])
    smi = "NVIDIA GeForce RTX 5090, 3000, 32607, 10, 50\n"
    got = cs.read_gpus(run=lambda cmd, **kw: smi)
    assert [c["name"] for c in got] == ["NVIDIA GeForce RTX 5090", "AMD Radeon RX 7900 XT"]
    amd = got[1]
    assert amd["memory_total_mb"] == 20480 and amd["busy_percent"] is None and amd["temperature_c"] is None and amd["stats_available"] is False and amd["memory_used_mb"] is None
    assert got[0]["busy_percent"] == 10                                                                              # NVIDIA unchanged


def test_the_couriers_report_for_nvidia_only_is_unchanged(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA])
    got = cs.read_gpus(run=lambda cmd, **kw: "NVIDIA GeForce RTX 5090, 3000, 32607, 10, 50\n")
    assert len(got) == 1 and got[0]["name"] == "NVIDIA GeForce RTX 5090"


INTEL_IGPU = row("Intel(R) Graphics", 0x8086, 0.125, 0xFF, device=0x46A6)
NO_SMI = lambda cmd, **kw: None


def test_the_couriers_report_leaves_out_built_in_graphics_beside_a_real_card(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [INTEL_IGPU, AMD])
    assert [c["name"] for c in cs.read_gpus(run=NO_SMI)] == ["AMD Radeon RX 7900 XT"]


def test_the_couriers_report_for_nvidia_plus_built_in_graphics_lists_only_nvidia(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [NVIDIA, INTEL_IGPU, AMD_IGPU])
    got = cs.read_gpus(run=lambda cmd, **kw: "NVIDIA GeForce RTX 5090, 3000, 32607, 10, 50\n")
    assert [c["name"] for c in got] == ["NVIDIA GeForce RTX 5090"]


def test_the_couriers_report_keeps_built_in_graphics_when_it_is_the_only_card(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [INTEL_IGPU])
    assert [c["name"] for c in cs.read_gpus(run=NO_SMI)] == ["Intel(R) Graphics"]


def test_the_couriers_report_lists_two_real_cards_and_never_a_software_adapter(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [AMD, INTEL, BASIC])
    assert [c["name"] for c in cs.read_gpus(run=NO_SMI)] == ["AMD Radeon RX 7900 XT", "Intel(R) Arc(TM) A770 Graphics"]


def test_the_couriers_report_for_a_software_adapter_alone_is_empty(monkeypatch):
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [BASIC])
    assert cs.read_gpus(run=NO_SMI, detect=lambda: None) == []


def test_the_couriers_report_with_no_card_is_empty(monkeypatch):
    assert cs.read_gpus(run=lambda cmd, **kw: None, detect=lambda: None) == []


# ---- the numbers the counters give work per adapter ----
def amd_counters(monkeypatch):
    t = fgc.table([(100, 6100, 10)], adapters={0xBB: 8000}, luid=0xBB)
    t[gc.COUNTERS["a_dedicated"][0]] = [(fgc.adapter_inst(0xBB), float(8000 * MIB))]
    fgc.install(monkeypatch, fgc.FakeBackend(t))


def test_the_benchmarks_block_works_for_an_amd_only_pc(monkeypatch):
    amd_counters(monkeypatch)
    monkeypatch.setattr(ga, "_dxgi_raw", lambda: [AMD])
    monkeypatch.setattr(gc, "process_names", lambda: {100: "SkyrimSE.exe"})
    use = bcu.read_usage(lambda cmd, t: ("error", ""), platform="win32")                     # nvidia-smi is not there
    assert use["available"] and use["totalMb"] == 20480 and use["usedMb"] == 8000 and use["freeMb"] == 12480
    assert [(p["name"], p["mb"]) for p in use["programs"]] == [("Skyrim", 6100)]


def test_the_graphics_memory_panel_works_for_an_amd_only_pc(monkeypatch):
    amd_counters(monkeypatch)
    monkeypatch.setattr(gc, "process_names", lambda: {100: "SkyrimSE.exe"})
    monkeypatch.setattr(gm, "_run", lambda cmd, timeout=15: None)                              # no nvidia-smi
    mon = gm.GpuMonitor.__new__(gm.GpuMonitor)
    s = mon._read_windows(lm.GpuInfo("AMD Radeon RX 7900 XT", 20480, "amd", luid=0xBB), {}, True)
    assert (s.total_mb, s.used_mb, s.skyrim_mb) == (20480, 8000, 6100) and s.free_mb == 12480


def test_the_adapter_id_picks_the_card_when_two_are_listed(monkeypatch):
    t = fgc.table([(100, 6100, 0)])
    t[gc.COUNTERS["dedicated"][0]] += [(fgc.inst(7, 0xBB), float(900 * MIB))]
    fgc.install(monkeypatch, fgc.FakeBackend(t))
    assert set(gc.read_programs(None, 0xBB)) == {7} and set(gc.read_programs(None, 0xF123)) == {100}
