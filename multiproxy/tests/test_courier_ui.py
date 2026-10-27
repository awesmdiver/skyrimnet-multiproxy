"""The words the Courier shows (tray menu, "This device is ready" window) and the tray's wiring. The words live in two pure
functions so they can be reviewed and tested without a screen; the tray itself is exercised when pystray is installed."""
from courier import COURIER_BUILD
import pytest

import courier_ui as ui




def _need_pystray():
    """pystray, and a screen for it: on Linux it refuses to even import without one (a bare container, ssh)."""
    try:
        pytest.importorskip("pystray")
    except Exception as e:      # noqa: BLE001
        pytest.skip(f"pystray needs a display here: {type(e).__name__}")

def texts(items):
    return [i["text"] for i in items if i["text"] is not None]


class TestTrayMenu:
    def test_the_menu_is_exactly_the_one_in_the_mockup(self):
        m = ui.menu_model(True, "GAMING-PC", "KX7-42M", True, True)
        assert texts(m) == ["MultiProxy Courier", f"1.{COURIER_BUILD} · Connected to GAMING-PC", "Pairing code KX7-42M", "Keep this device awake", "Start with Windows",
                            "Show pairing code…", "Make new pairing code", "Open log folder", "Quit Courier"]
        assert [i["key"] for i in m if i["text"] is None] == ["-", "-", "-"], "three separators"

    def test_waiting_for_the_main_pc(self):
        assert ui.menu_model(False, "", "KX7-42M", True, True)[1]["text"] == f"1.{COURIER_BUILD} · Waiting for your main device"

    def test_connected_without_a_name_still_reads_well(self):
        assert ui.menu_model(True, "", "KX7-42M", True, True)[1]["text"] == f"1.{COURIER_BUILD} · Connected to your main device"

    def test_the_two_switches_show_their_state_and_the_status_lines_are_not_clickable(self):
        m = {i["key"]: i for i in ui.menu_model(False, "", "AAA-111", False, True)}
        assert m["keep_awake"]["checked"] is False and m["start_with_windows"]["checked"] is True
        assert m["title"]["enabled"] is False and m["code"]["enabled"] is False

    def test_start_with_windows_is_left_out_where_the_installer_does_that_job(self):
        assert "Start with Windows" not in texts(ui.menu_model(False, "", "AAA-111", True, False, start_supported=False))

    def test_every_clickable_item_has_something_to_do(self):
        for key in ("keep_awake", "start_with_windows", "show_code", "new_code", "open_log", "quit"):
            assert hasattr(ui.Tray, "do_" + key), key


class TestReadyWindow:
    REPORT = {"programs": [
        {"id": "ollama", "name": "Ollama", "version": "0.12.3", "installed": True, "running": True},
        {"id": "lmstudio", "name": "LM Studio", "version": "0.4.1", "installed": True, "running": False},
        {"id": "llamacpp", "name": "llama.cpp", "version": None, "installed": False, "running": False, "engine": {"kind": "none"}}]}

    BUILTIN = "Model engine (llama.cpp): Ready\n(downloads when you first load a model)"

    def test_the_words_are_the_directors_own(self):
        t = ui.ready_window_text("KX7-42M", self.REPORT)
        assert t["title"] == "This device is ready." and t["code"] == "KX7-42M"
        assert t["steps_title"] == "How to connect:"
        assert t["steps"] == [{"text": "On your main device, open MultiProxy > Other devices.", "note": None},
                              {"text": f"On your main PC, select this computer ({ui._this_pc()}) and enter the code below.",
                               "note": "(If Windows Firewall asks, allow access on private networks.)"}]
        assert t["found_title"] == "Found AI programs:"
        assert t["programs"] == ["Ollama 0.12.3: Running", "LM Studio 0.4.1: Installed (not running)", self.BUILTIN]
        assert t["footer"] == "After you have paired this computer, you can safely close this window. Courier will stay active in your system tray by the clock."
        assert t["close"] == "Got it"

    def test_the_step_names_this_pc_as_the_courier_reports_it(self):
        t = ui.ready_window_text("KX7-42M", {"courier": {"name": "laptop"}, "programs": []})
        assert t["steps"][1]["text"] == "On your main PC, select this computer (laptop) and enter the code below."

    def test_a_program_that_is_not_installed_is_not_listed(self):
        t = ui.ready_window_text("X", {"programs": [{"id": "ollama", "name": "Ollama", "version": None, "installed": False, "running": False}]})
        assert t["programs"] == [self.BUILTIN]

    def test_no_version_known_just_the_name(self):
        rep = {"programs": [{"id": "ollama", "name": "Ollama", "version": None, "installed": True, "running": True}]}
        assert ui.ready_window_text("X", rep)["programs"][0] == "Ollama: Running"

    def test_with_no_report_it_still_says_something_useful(self):
        assert ui.ready_window_text("X", None)["programs"] == [self.BUILTIN]

    def test_the_built_in_engine_once_downloaded(self):
        rep = {"programs": [{"id": "llamacpp", "name": "llama.cpp", "version": None, "installed": True, "running": False, "engine": {"kind": "built-in"}}]}
        assert ui.ready_window_text("X", rep)["programs"] == ["Model engine (llama.cpp): Ready"]

    def test_an_existing_llama_server_is_named_as_such(self):
        rep = {"programs": [{"id": "llamacpp", "name": "llama.cpp", "version": "b4000", "installed": True, "running": False, "engine": {"kind": "existing"}}]}
        assert ui.ready_window_text("X", rep)["programs"] == ["llama.cpp b4000: Ready"]


class TestIcon:
    def test_green_when_connected_grey_when_waiting(self):
        pytest.importorskip("PIL")
        green, grey = ui.make_icon(True), ui.make_icon(False)
        assert green.size == (64, 64) and green.getpixel((50, 50))[:3] == (52, 199, 89) and grey.getpixel((50, 50))[:3] == (140, 140, 148)


class TestPairedAndUpdated:
    REPORT = {"courier": {"name": "laptop"}, "programs": [{"id": "ollama", "name": "Ollama", "version": "0.12.3", "installed": True, "running": True}]}

    def test_paired_has_no_steps_and_no_code_but_still_the_engines(self):
        t = ui.ready_window_text("KX7-42M", self.REPORT, "Den PC")
        assert t["title"] == "This device is ready."
        assert t["paired"] == "You're all set! This computer is already paired with Den PC."
        assert t["steps"] == [] and t["steps_title"] == "" and t["code"] == "" and "KX7-42M" not in repr(t)
        assert t["found_title"] == "Found AI programs:" and t["programs"] == ["Ollama 0.12.3: Running"]
        assert t["footer"] == "You can safely close this window. Courier will stay active in your system tray by the clock."
        assert t["updated"] is None and t["close"] == "Got it"

    def test_paired_and_just_updated_adds_the_updated_line(self):
        t = ui.ready_window_text("KX7-42M", self.REPORT, "Den PC", ("1.2", "1.3"))
        assert t["updated"] == "Courier just updated from 1.2 to 1.3." and t["code"] == "" and t["steps"] == []

    def test_updated_but_not_paired_is_the_normal_window_plus_the_line(self):
        t = ui.ready_window_text("KX7-42M", self.REPORT, None, ("1.2", "1.3"))
        assert t["updated"] == "Courier just updated from 1.2 to 1.3."
        assert t["code"] == "KX7-42M" and t["paired"] is None and t["steps_title"] == "How to connect:" and len(t["steps"]) == 2
        assert t["footer"].startswith("After you have paired this computer")

    def test_not_paired_is_as_before(self):
        t = ui.ready_window_text("KX7-42M", self.REPORT)
        assert t["updated"] is None and t["paired"] is None and t["code"] == "KX7-42M"

    def test_the_tray_title_names_the_paired_pc_and_the_code_line_goes(self):
        paired = ui.menu_model(False, "", "AAA-111", True, True, paired_name="Den PC")
        assert paired[1]["text"] == f"1.{COURIER_BUILD} · Waiting for Den PC to connect"
        assert not any(i["key"] == "code" for i in paired)
        assert "Show pairing code…" in [i["text"] for i in paired] and "Make new pairing code" in [i["text"] for i in paired]
        assert ui.menu_model(True, "Den PC", "AAA-111", True, True, paired_name="Den PC")[1]["text"] == f"1.{COURIER_BUILD} · Connected to Den PC"
        plain = ui.menu_model(False, "", "AAA-111", True, True)
        assert plain[0]["text"] == "MultiProxy Courier" and plain[1]["text"] == f"1.{COURIER_BUILD} · Waiting for your main device" and plain[2]["text"] == "Pairing code AAA-111"

    def test_show_pairing_code_asks_for_the_code_view_even_when_paired(self, tmp_path, monkeypatch):
        _need_pystray()
        import courier as cr
        settings = cr.CourierSettings(tmp_path / "courier.json")
        tray = ui.Tray(cr.Courier(settings, bind_ips=["127.0.0.1"]), settings, tmp_path)
        seen = []
        monkeypatch.setattr(ui.hidden_process, "popen", lambda cmd, **kw: seen.append(cmd))
        tray.do_show_code()
        assert "ready-window" in seen[0] and "--show-code" in seen[0]


class TestTrayWiring:
    def test_the_tray_builds_its_menu_and_follows_the_courier(self, tmp_path):
        _need_pystray()
        import courier as cr
        settings = cr.CourierSettings(tmp_path / "courier.json")
        c = cr.Courier(settings, bind_ips=["127.0.0.1"])
        tray = ui.Tray(c, settings, tmp_path)
        assert texts(tray.model())[0] == "MultiProxy Courier" and texts(tray.model())[1].startswith("1.") and texts(tray.model())[2] == f"Pairing code {settings.code}"
        assert tray._menu() is not None
