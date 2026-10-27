"""gpu_choice.py (Routing step 8): reading the cards, which ones count, and turning a remembered choice into an engine's start settings. Nothing here starts a program:
the readers are given text, and the program runners are fakes."""
import json
from types import SimpleNamespace

import gpu_choice as gc
from gpu_choice import Card

UUID_A = "GPU-50863396-8256-dd23-f5f0-7cc79cc1ba4e"
UUID_B = "GPU-11111111-2222-3333-4444-555555555555"
NV5090 = Card(UUID_A, "NVIDIA GeForce RTX 5090", 32607, "nvidia")
NV3060 = Card(UUID_B, "NVIDIA GeForce RTX 3060", 12288, "nvidia")
INTEL = Card("pnp:PCI\\VEN_8086&DEV_A780\\3&11583659&0&10", "Intel(R) Graphics", 128, "intel")
ARC = Card("pnp:PCI\\VEN_8086&DEV_56A0\\4&1", "Intel(R) Arc(TM) A770 Graphics", 16384, "intel")
RADEON = Card("pnp:PCI\\VEN_1002&DEV_744C\\4&2", "AMD Radeon RX 7900 XTX", 24576, "amd")


# ---- reading the cards ---------------------------------------------------------------------------------------------------------------
def test_parse_nvidia_reads_uuid_name_and_memory_and_skips_junk_lines():
    csv = f"{UUID_A}, NVIDIA GeForce RTX 5090, 32607\n{UUID_B}, NVIDIA GeForce RTX 3060, 12288\nNo devices were found\n\n"
    cards = gc.parse_nvidia(csv)
    assert [(c.id, c.name, c.memory_mb, c.vendor) for c in cards] == [(UUID_A, "NVIDIA GeForce RTX 5090", 32607, "nvidia"), (UUID_B, "NVIDIA GeForce RTX 3060", 12288, "nvidia")]


def test_parse_nvidia_keeps_a_name_with_a_comma_and_an_unreadable_memory_as_none():
    c = gc.parse_nvidia(f"{UUID_A}, Fancy, Card, [N/A]")[0]
    assert c.name == "Fancy, Card" and c.memory_mb is None


def test_parse_windows_adapters_uses_the_pnp_id_and_drops_virtual_adapters():
    text = json.dumps([
        {"Name": "NVIDIA GeForce RTX 5090", "PNPDeviceID": "PCI\\VEN_10DE&DEV_2B85\\4&1", "AdapterRAM": 4294967295},
        {"Name": "Intel(R) Graphics", "PNPDeviceID": "PCI\\VEN_8086&DEV_A780\\3&1", "AdapterRAM": 134217728},
        {"Name": "Microsoft Basic Display Adapter", "PNPDeviceID": "ROOT\\BASICDISPLAY\\0000", "AdapterRAM": 0},
        {"Name": "Virtual Desktop Monitor", "PNPDeviceID": "ROOT\\X\\0", "AdapterRAM": 0},
        {"Name": "", "PNPDeviceID": "PCI\\X", "AdapterRAM": 1},
        {"Name": "No id card", "PNPDeviceID": None}])
    cards = gc.parse_windows_adapters(text)
    assert [c.name for c in cards] == ["NVIDIA GeForce RTX 5090", "Intel(R) Graphics"]
    assert cards[0].id == "pnp:PCI\\VEN_10DE&DEV_2B85\\4&1" and cards[0].vendor == "nvidia" and cards[1].vendor == "intel" and cards[1].memory_mb == 128


def test_parse_windows_adapters_takes_one_adapter_as_a_plain_object_and_survives_bad_text():
    one = gc.parse_windows_adapters(json.dumps({"Name": "AMD Radeon RX 7900 XTX", "PNPDeviceID": "PCI\\A", "AdapterRAM": 0}))
    assert len(one) == 1 and one[0].memory_mb is None and one[0].vendor == "amd"
    assert gc.parse_windows_adapters("not json") == [] and gc.parse_windows_adapters("") == []


def test_parse_lspci_finds_display_adapters_by_pci_address():
    text = ('0000:00:02.0 "VGA compatible controller" "Intel Corporation" "Raptor Lake-S GT1 [UHD Graphics 770]" -r04 "ASUSTeK" "X"\n'
            '0000:01:00.0 "VGA compatible controller" "NVIDIA Corporation" "GB202 [GeForce RTX 5090]" -ra1 "ASUSTeK" "Y"\n'
            '0000:00:1f.3 "Audio device" "Intel Corporation" "Something" -r11 "A" "B"\n')
    cards = gc.parse_lspci(text)
    assert [c.id for c in cards] == ["pci:0000:00:02.0", "pci:0000:01:00.0"]
    assert cards[1].vendor == "nvidia" and cards[0].vendor == "intel" and cards[0].memory_mb is None


def test_parse_list_devices_reads_names_descriptions_and_memory():
    text = ("ggml_vulkan: Found 2 Vulkan devices:\nAvailable devices:\n"
            "  Vulkan0: NVIDIA GeForce RTX 5090 (32,187 MiB, 31,000 MiB free)\n"
            "  Vulkan1: Intel(R) Graphics (37037 MiB, 47826 MiB free)\n"
            "  CUDA0: NVIDIA GeForce RTX 5090\n")
    assert gc.parse_list_devices(text) == [("Vulkan0", "NVIDIA GeForce RTX 5090", 32187), ("Vulkan1", "Intel(R) Graphics", 37037), ("CUDA0", "NVIDIA GeForce RTX 5090", None)]
    assert gc.parse_list_devices("") == []


# ---- list_cards: merging the two readers ---------------------------------------------------------------------------------------------
def fake_run(nvidia=None, windows=None, lspci=None):
    def run(cmd, **kw):
        head = cmd[0]
        out = {"nvidia-smi": nvidia, "powershell": windows, "lspci": lspci}[head]
        if out is None:
            raise FileNotFoundError(head)
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    return run


def test_list_cards_shows_a_card_both_readers_know_once_with_the_nvidia_tool_id_and_adds_the_others():
    win = json.dumps([{"Name": "NVIDIA GeForce RTX 5090", "PNPDeviceID": "PCI\\N", "AdapterRAM": 4294967295},
                      {"Name": "Intel(R) Graphics", "PNPDeviceID": "PCI\\I", "AdapterRAM": 134217728}])
    cards = gc.list_cards(fake_run(nvidia=f"{UUID_A}, NVIDIA GeForce RTX 5090, 32607", windows=win), platform="win32")
    assert [(c.id, c.vendor) for c in cards] == [(UUID_A, "nvidia"), ("pnp:PCI\\I", "intel")]
    assert cards[0].memory_mb == 32607


def test_list_cards_trusts_the_nvidia_tool_over_a_windows_nvidia_adapter_it_could_not_match():
    win = json.dumps([{"Name": "NVIDIA Something Odd", "PNPDeviceID": "PCI\\N", "AdapterRAM": 1048576}])
    cards = gc.list_cards(fake_run(nvidia=f"{UUID_A}, NVIDIA GeForce RTX 5090, 32607", windows=win), platform="win32")
    assert [c.id for c in cards] == [UUID_A]


def test_list_cards_without_the_nvidia_tool_uses_the_windows_list_alone():
    win = json.dumps([{"Name": "AMD Radeon RX 7900 XTX", "PNPDeviceID": "PCI\\A", "AdapterRAM": 0}])
    cards = gc.list_cards(fake_run(windows=win), platform="win32")
    assert [c.id for c in cards] == ["pnp:PCI\\A"]


def test_list_cards_on_linux_uses_lspci_and_gives_an_empty_list_when_nothing_can_be_read():
    lspci = '0000:01:00.0 "VGA compatible controller" "NVIDIA Corporation" "GB202 [GeForce RTX 5090]" -ra1 "A" "B"\n'
    cards = gc.list_cards(fake_run(nvidia=f"{UUID_A}, NVIDIA GeForce RTX 5090, 32607", lspci=lspci), platform="linux")
    assert [c.id for c in cards] == [UUID_A]          # the PCI entry names the same card: shown once
    assert gc.list_cards(fake_run(), platform="linux") == []


def test_cached_cards_remembers_for_a_minute_only_when_not_given_a_reader():
    calls = []
    assert gc.cached_cards(reader=lambda: calls.append(1) or [NV5090]) == [NV5090]
    assert gc.cached_cards(reader=lambda: calls.append(1) or [NV5090]) == [NV5090] and len(calls) == 2


# ---- which cards count -----------------------------------------------------------------------------------------------------------------
def test_built_in_graphics_does_not_count_but_arc_and_nvidia_and_discrete_amd_do():
    assert gc.is_integrated(INTEL) and not gc.is_integrated(ARC) and not gc.is_integrated(NV5090) and not gc.is_integrated(RADEON)
    assert gc.is_integrated(Card("x", "AMD Radeon(TM) Graphics", 512, "amd"))
    assert gc.is_integrated(Card("x", "AMD Radeon Vega 8 Graphics", 512, "amd"))
    assert not gc.is_integrated(Card("x", "AMD Radeon VII", 16384, "amd"))


def test_a_pc_with_one_real_card_and_intel_graphics_is_not_asked_a_question():
    assert gc.choosable([NV5090, INTEL]) == [NV5090]
    assert len(gc.choosable([NV5090, INTEL])) == 1           # the choice shows only when this is more than one
    assert len(gc.choosable([NV5090, NV3060, INTEL])) == 2


def test_choice_of_remembers_id_name_and_memory_to_one_decimal():
    assert gc.choice_of(NV5090) == {"id": UUID_A, "name": "NVIDIA GeForce RTX 5090", "memoryGb": 31.8}
    assert gc.choice_of(Card("p", "Unknown", None, "other"))["memoryGb"] is None
    assert NV3060.to_json() == {"id": UUID_B, "name": "NVIDIA GeForce RTX 3060", "memoryGb": 12.0, "vendor": "nvidia"}


# ---- plan_for ---------------------------------------------------------------------------------------------------------------------------
CARDS = [NV5090, NV3060, INTEL]
DEVS = [("Vulkan0", "NVIDIA GeForce RTX 5090", 32187), ("Vulkan1", "NVIDIA GeForce RTX 3060", 12000), ("Vulkan2", "Intel(R) Graphics", 37037)]


def test_no_choice_is_automatic_and_adds_nothing():
    for choice in (None, {}, {"id": ""}):
        p = gc.plan_for(choice, CARDS, "cuda-13.4")
        assert p.automatic and p.env == {} and p.args == [] and p.card is None and p.note == ""
    assert gc.merge_env(None, gc.Plan()) is None and gc.merge_env({"A": "1"}, gc.Plan()) == {"A": "1"}


def test_a_cuda_engine_is_pointed_at_the_card_by_its_uuid():
    p = gc.plan_for(gc.choice_of(NV3060), CARDS, "cuda-13.4")
    assert p.env == {"CUDA_VISIBLE_DEVICES": UUID_B} and p.args == [] and p.card == NV3060 and not p.automatic and p.note == ""


def test_a_cuda_engine_cannot_use_a_card_that_is_not_nvidia():
    p = gc.plan_for(gc.choice_of(INTEL), CARDS, "cuda-12.4")
    assert p.automatic and p.env == {} and "can't use Intel(R) Graphics" in p.note


def test_a_vulkan_engine_gets_device_with_this_boots_name_found_from_the_engines_own_list():
    seen = []
    p = gc.plan_for(gc.choice_of(NV3060), CARDS, "vulkan", "llama-server.exe", lambda exe: seen.append(exe) or DEVS)
    assert p.args == ["--device", "Vulkan1"] and p.env == {} and p.card == NV3060 and seen == ["llama-server.exe"]
    devs = DEVS + [("Vulkan3", "Intel(R) Arc(TM) A770 Graphics", 16000)]
    assert gc.plan_for(gc.choice_of(ARC), CARDS + [ARC], "rocm", "x", lambda e: devs).args == ["--device", "Vulkan3"]


def test_a_name_that_is_all_filler_words_never_matches_an_engine_device():
    """Known edge: "Intel(R) Graphics" normalises to nothing, so it can't be matched by name. Harmless today because built-in graphics is not choosable."""
    p = gc.plan_for(gc.choice_of(INTEL), CARDS, "vulkan", "x", lambda e: DEVS)
    assert p.automatic and "didn't list" in p.note


def test_two_cards_with_one_name_pick_the_device_whose_memory_is_closest():
    twin1, twin2 = Card("pnp:1", "AMD Radeon RX 7900 XTX", 24576, "amd"), Card("pnp:2", "AMD Radeon RX 7900 XTX", 49152, "amd")
    devs = [("Vulkan0", "AMD Radeon RX 7900 XTX", 24000), ("Vulkan1", "AMD Radeon RX 7900 XTX", 48000)]
    assert gc.plan_for(gc.choice_of(twin2), [twin1, twin2], "vulkan", "x", lambda e: devs).args == ["--device", "Vulkan1"]


def test_an_engine_that_does_not_list_the_card_falls_back_to_automatic_with_a_note():
    p = gc.plan_for(gc.choice_of(NV3060), CARDS, "vulkan", "x", lambda e: [DEVS[0]])
    assert p.automatic and p.args == [] and "didn't list NVIDIA GeForce RTX 3060" in p.note
    assert gc.plan_for(gc.choice_of(NV3060), CARDS, "vulkan", None).note.startswith("The software didn't list")


def test_a_card_that_is_gone_falls_back_to_automatic_and_says_so_by_name():
    p = gc.plan_for({"id": "GPU-gone", "name": "NVIDIA GeForce GTX 1080", "memoryGb": 8.0}, CARDS, "cuda-13.4")
    assert p.automatic and p.env == {} and p.note == "The graphics card you chose (NVIDIA GeForce GTX 1080) isn't in this device anymore, so Automatic is used."


def test_a_processor_only_engine_cannot_use_a_card_so_it_is_automatic_with_a_note():
    p = gc.plan_for(gc.choice_of(NV5090), CARDS, "cpu")
    assert p.automatic and p.args == [] and p.env == {} and "can't use" in p.note
    assert p.to_json() == {"automatic": True, "card": None, "note": p.note}


def test_merge_env_adds_the_plan_to_the_base_or_to_the_real_environment(monkeypatch):
    plan = gc.plan_for(gc.choice_of(NV5090), CARDS, "cuda-13.4")
    assert gc.merge_env({"A": "1"}, plan) == {"A": "1", "CUDA_VISIBLE_DEVICES": UUID_A}
    monkeypatch.setenv("SOME_VAR", "yes")
    merged = gc.merge_env(None, plan)
    assert merged["SOME_VAR"] == "yes" and merged["CUDA_VISIBLE_DEVICES"] == UUID_A
    assert plan.to_json()["card"]["id"] == UUID_A


# ---- apply_card ------------------------------------------------------------------------------------------------------------------------
def hardware():
    from local_models import GpuInfo
    from voice_engine import Hardware
    return Hardware(system="windows", gpu=GpuInfo("NVIDIA GeForce RTX 5090", 32607, "nvidia"), driver=617.14, free_vram_bytes=20 * 1024**3,
                    free_ram_bytes=1, free_disk_bytes=2)


def test_apply_card_without_a_card_leaves_the_hardware_alone():
    hw = hardware()
    assert gc.apply_card(hw, None) is hw and hw.gpu.name == "NVIDIA GeForce RTX 5090" and hw.free_vram_bytes == 20 * 1024**3


def test_apply_card_for_an_nvidia_card_reads_that_cards_free_memory():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="9000\n", stderr="")
    hw = gc.apply_card(hardware(), NV3060, run)
    assert hw.gpu.name == "NVIDIA GeForce RTX 3060" and hw.gpu.memory_mb == 12288 and hw.free_vram_bytes == 9000 * 1024 * 1024
    assert calls[0][:3] == ["nvidia-smi", "-i", UUID_B]


def test_apply_card_when_free_memory_cannot_be_read_says_unknown_never_full():
    def boom(cmd, **kw):
        raise OSError("no tool")
    assert gc.apply_card(hardware(), NV3060, boom).free_vram_bytes is None
    assert gc.apply_card(hardware(), RADEON, boom).free_vram_bytes is None       # not NVIDIA: nothing to ask
