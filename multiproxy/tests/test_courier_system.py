"""courier_system.py: what a Courier reports about its device, and the few things it does to it. A reading it can't take is
None ("not available"), never a made-up number."""
import sys

import pytest

import courier_system as cs
import local_models as lm

NVIDIA_TWO_CARDS = (
    "NVIDIA GeForce RTX 3080, 8600, 10240, 64, 71\n"
    "NVIDIA GeForce GTX 1060, 900, 6144, [N/A], [N/A]\n")


class TestGraphicsCards:
    def test_two_cards_are_two_entries_with_everything_they_report(self):
        cards = cs.parse_nvidia_cards(NVIDIA_TWO_CARDS)
        assert [c["name"] for c in cards] == ["NVIDIA GeForce RTX 3080", "NVIDIA GeForce GTX 1060"]
        assert cards[0] == {"name": "NVIDIA GeForce RTX 3080", "vendor": "nvidia", "memory_used_mb": 8600, "memory_total_mb": 10240,
                            "busy_percent": 64, "temperature_c": 71, "stats_available": True}

    def test_a_value_the_card_doesnt_report_is_none_never_zero(self):
        c = cs.parse_nvidia_cards(NVIDIA_TWO_CARDS)[1]
        assert c["busy_percent"] is None and c["temperature_c"] is None and c["stats_available"] is False
        assert c["memory_total_mb"] == 6144

    @pytest.mark.parametrize("text", ["", "garbage", "name only\n", "N/A, N/A, N/A, N/A, N/A"])
    def test_junk_output_is_no_cards_not_a_crash(self, text):
        cards = cs.parse_nvidia_cards(text)
        assert all(c["memory_total_mb"] is None or isinstance(c["memory_total_mb"], int) for c in cards)

    def test_read_gpus_uses_nvidia_smi_when_it_answers(self):
        cards = cs.read_gpus(run=lambda cmd, timeout=10: NVIDIA_TWO_CARDS)
        assert len(cards) == 2 and cards[0]["busy_percent"] == 64

    def test_a_card_that_doesnt_report_stats_says_so(self):
        """AMD / Intel (no nvidia-smi): name and memory from the card detector; busy % and temperature stay not available."""
        det = lambda: lm.GpuInfo("AMD Radeon RX 7900 XT", 20480, "amd")
        cards = cs.read_gpus(run=lambda cmd, timeout=10: None, detect=det)
        assert cards == [{"name": "AMD Radeon RX 7900 XT", "vendor": "amd", "memory_used_mb": None, "memory_total_mb": 20480,
                          "busy_percent": None, "temperature_c": None, "stats_available": False}]

    def test_no_card_at_all_is_an_empty_list(self):
        assert cs.read_gpus(run=lambda cmd, timeout=10: None, detect=lambda: None) == []


class TestPcFigures:
    def test_meminfo(self):
        text = "MemTotal:       32768000 kB\nMemFree:  100 kB\nMemAvailable:   16384000 kB\n"
        assert cs.parse_meminfo(text) == {"total_mb": 32000, "used_mb": 16000}
        assert cs.parse_meminfo("nothing here") is None

    def test_this_pc_can_report_its_own_memory_disk_and_uptime(self, tmp_path):
        mem = cs.read_pc_memory()
        assert mem and mem["total_mb"] > 0 and 0 <= mem["used_mb"] <= mem["total_mb"]
        disk = cs.read_disk(tmp_path / "not-created-yet" / "models")
        assert disk and disk["free_gb"] > 0
        assert cs.read_uptime_seconds() > 0

    def test_a_desktop_has_no_battery_and_nothing_is_invented(self):
        assert cs.interpret_power_status(1, 128, 255) == {"has_battery": False, "battery_percent": None, "plugged_in": None}

    def test_a_laptop_on_battery_and_plugged_in(self):
        assert cs.interpret_power_status(0, 1, 63) == {"has_battery": True, "battery_percent": 63, "plugged_in": False}
        assert cs.interpret_power_status(1, 8, 80) == {"has_battery": True, "battery_percent": 80, "plugged_in": True}
        assert cs.interpret_power_status(255, 1, 255)["plugged_in"] is None and cs.interpret_power_status(255, 1, 255)["battery_percent"] is None

    def test_power_reads_without_crashing(self):
        p = cs.read_power()
        assert set(p) == {"has_battery", "battery_percent", "plugged_in"}


class FakeAwake:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok
    def start(self):
        self.calls.append("start")
        return self.ok
    def stop(self):
        self.calls.append("stop")


class TestKeepAwake:
    def test_on_then_off_asks_for_and_lets_go(self):
        b = FakeAwake()
        k = cs.KeepAwake(b)
        assert k.set(True) and k.on and k.set(True) and b.calls == ["start"], "asking twice asks once"
        assert k.set(False) and not k.on and b.calls == ["start", "stop"]
        assert k.set(False) and b.calls == ["start", "stop"]

    def test_when_the_request_fails_it_says_so(self):
        k = cs.KeepAwake(FakeAwake(ok=False))
        assert k.set(True) is False and not k.on

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows' own awake request")
    def test_the_real_windows_request_can_be_made_and_released(self):
        k = cs.KeepAwake()
        assert k.set(True) and k.on
        assert k.set(False) and not k.on


class FakeRegistry:
    """A winreg stand-in: nothing here ever touches the real registry."""
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = "HKCU", 1, 2, 1

    def __init__(self):
        self.values = {}
    def OpenKey(self, root, path, res, access):
        class K:
            def __enter__(s): return s
            def __exit__(s, *a): return False
        assert root == "HKCU" and path == cs.RUN_KEY
        return K()
    def SetValueEx(self, k, name, res, typ, value): self.values[name] = value
    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1
    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class TestStartWithWindows:
    def test_on_and_off_in_the_per_user_run_key(self):
        reg = FakeRegistry()
        s = cs.StartWithWindows('"C:\\x\\pythonw.exe" "C:\\x\\courier.py" run', registry=reg)
        assert s.supported and not s.is_on()
        assert s.set(True) and s.is_on()
        assert reg.values == {"MultiProxy Courier": '"C:\\x\\pythonw.exe" "C:\\x\\courier.py" run'}
        assert s.set(False) and not s.is_on() and reg.values == {}
        assert s.set(False), "turning off twice is fine"

    def test_not_supported_means_no_registry(self):
        s = cs.StartWithWindows("x", registry=None) if sys.platform != "win32" else cs.StartWithWindows("x")
        if sys.platform != "win32":
            assert not s.supported and s.set(True) is False and not s.is_on()


class TestFirewallRule:
    def test_private_networks_only_and_named_for_the_courier(self):
        tcp, udp = cs.firewall_commands(8110, 8099)
        assert tcp == ["netsh", "advfirewall", "firewall", "add", "rule", "name=MultiProxy Courier", "dir=in", "action=allow",
                       "profile=private", "remoteip=LocalSubnet", "protocol=TCP", "localport=8110"]
        assert udp[-2:] == ["protocol=UDP", "localport=8099"]
        for cmd in (tcp, udp):
            assert "profile=private" in cmd and not any("public" in c.lower() or "profile=any" in c.lower() or "domain" in c.lower() for c in cmd)
            assert "remoteip=LocalSubnet" in cmd and not any(c.startswith("program=") for c in cmd), "this network only; no program unless one is given"
        tcp, udp = cs.firewall_commands(8110, 8099, r"C:\Courier\python\pythonw.exe")
        for cmd in (tcp, udp):
            assert r"program=C:\Courier\python\pythonw.exe" in cmd and "profile=private" in cmd and "remoteip=LocalSubnet" in cmd
