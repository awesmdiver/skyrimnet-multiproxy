"""The ready window and the commands tell the same story while a main device pairs: "type this code" turns into "Connected to <name>."
the moment it pairs, and goes back to the steps when the device is unpaired. One decision (`pairing_state`) feeds the window and `status`."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import courier as cr
import courier_ui as ui

ROOT = Path(__file__).resolve().parent.parent
FOOTER = "You can safely close this window. Courier will stay active in your system tray by the clock."


def rep(connected_to=None, trouble=None):
    return {"courier": {"name": "laptop", "pairing_trouble": trouble, "connected_to": connected_to}, "programs": []}


class TestWords:
    def test_not_paired_shows_the_steps_and_the_code(self):
        t = ui.ready_window_text("KX7-42M", rep())
        assert t["code"] == "KX7-42M" and t["steps"] and t["paired"] is None

    def test_paired_but_not_connected_keeps_the_existing_text(self):
        t = ui.ready_window_text("KX7-42M", rep(), "Den PC")
        assert t["paired"] == "You're all set! This computer is already paired with Den PC." and t["code"] == "" and t["steps"] == []

    def test_connected_now_says_who_and_hides_the_steps_and_the_code(self):
        t = ui.ready_window_text("KX7-42M", rep("Gaming-Rig"), "Gaming-Rig")
        assert t["paired"] == "Connected to Gaming-Rig." and t["footer"] == FOOTER
        assert t["code"] == "" and t["steps"] == [] and t["steps_title"] == ""

    def test_trouble_shows_the_code_and_the_lines_even_when_paired(self):
        t = ui.ready_window_text("KX7-42M", rep(trouble={"address": "192.168.1.9", "new_code": False}), "Den PC")
        assert t["code"] == "KX7-42M" and t["trouble"][0].startswith("A device at 192.168.1.9 tried to connect with an old pairing code.")

    def test_updated_line_still_shows_in_the_connected_view(self):
        t = ui.ready_window_text("KX7-42M", rep("Rig"), "Rig", ("1.2", "1.3"))
        assert t["updated"] == "Courier just updated from 1.2 to 1.3." and t["paired"] == "Connected to Rig."


class TestWatch:
    def test_pairing_while_open_redraws_to_connected(self):
        w = ui.WindowWatch()
        first = w.view("AAA-111", None, rep())
        assert first["code"] == "AAA-111"
        assert w.view("AAA-111", None, rep()) is None                                  # nothing changed: no redraw
        t = w.view("AAA-111", "Rig", rep("Rig"))
        assert t["paired"] == "Connected to Rig." and t["code"] == ""

    def test_unpairing_goes_back_to_the_steps_and_the_new_code(self):
        w = ui.WindowWatch()
        w.view("AAA-111", "Rig", rep("Rig"))
        t = w.view("BBB-222", None, rep())
        assert t["code"] == "BBB-222" and t["steps"] and t["paired"] is None

    def test_a_new_code_alone_redraws(self):
        w = ui.WindowWatch()
        w.view("AAA-111", None, rep())
        assert w.view("BBB-222", None, rep())["code"] == "BBB-222"

    def test_an_unreachable_courier_keeps_the_last_view(self):
        w = ui.WindowWatch()
        w.view("AAA-111", "Rig", rep("Rig"))
        assert w.view("AAA-111", "Rig", None) is None
        assert w.shown["paired"] == "Connected to Rig."
        assert w.view("AAA-111", "Rig", rep("Rig")) is None                           # and back: still nothing to redraw

    def test_the_very_first_view_is_drawn_even_without_an_answer(self):
        assert ui.WindowWatch().view("AAA-111", None, None)["code"] == "AAA-111"

    def test_trouble_lines_appear_and_disappear_on_their_own(self):
        w = ui.WindowWatch()
        w.view("AAA-111", None, rep())
        bad = w.view("AAA-111", None, rep(trouble={"address": "10.0.0.5", "new_code": False}))
        assert bad["trouble"]
        assert w.view("AAA-111", None, rep())["trouble"] == []

    def test_show_pairing_code_holds_until_the_pairing_changes(self):
        w = ui.WindowWatch(force_code=True)
        assert w.view("AAA-111", "Rig", rep())["code"] == "AAA-111"                  # asked for the code on a paired device
        assert w.view("AAA-111", "Rig", rep("Rig")) ["paired"] == "Connected to Rig."  # then it connects: the truth wins


class TestPairingFile:
    def test_reads_code_and_name(self, tmp_path):
        p = tmp_path / "courier.json"
        p.write_text(json.dumps({"code": "AAA-111", "paired": True, "paired_name": "Rig"}), encoding="utf-8")
        assert ui.read_pairing(p) == ("AAA-111", "Rig")
        p.write_text(json.dumps({"code": "BBB-222", "paired": False, "paired_name": ""}), encoding="utf-8")
        assert ui.read_pairing(p) == ("BBB-222", None)

    def test_a_file_being_written_is_skipped_and_never_replaced(self, tmp_path):
        p = tmp_path / "courier.json"
        p.write_text("{ half", encoding="utf-8")
        assert ui.read_pairing(p) is None and p.read_text(encoding="utf-8") == "{ half"
        assert ui.read_pairing(tmp_path / "missing.json") is None


class TestTrayRightAfterPairing:
    def test_the_menu_drops_the_code_and_says_connected(self):
        m = ui.menu_model(True, "Rig", "AAA-111", True, True, paired_name="Rig")
        texts = [i["text"] for i in m if i["text"]]
        assert texts[0] == "MultiProxy Courier" and texts[1].startswith("1.") and texts[1].endswith(" · Connected to Rig") and not any(t.startswith("Pairing code") for t in texts)

    def test_the_menu_lists_the_code_before_pairing(self):
        assert any(i["text"] == "Pairing code AAA-111" for i in ui.menu_model(False, "", "AAA-111", True, True))


class TestCommands:
    def make(self, tmp_path, paired):
        s = cr.CourierSettings(tmp_path / "courier.json")
        if paired:
            s.note_pairing("Rig")
        return s

    def test_status_lines_for_each_state(self, tmp_path, monkeypatch):
        s = self.make(tmp_path, False)
        monkeypatch.setattr(cr, "local_call", lambda *a, **k: {"courier": {"version": "9", "pairing_trouble": None, "connected_to": None}, "programs": []})
        assert f"Pairing code: {s.code}" in cr.status_text(s)
        s.note_pairing("Rig")
        out = cr.status_text(s)
        assert "Paired with Rig, waiting for it to connect" in out and "Pairing code" not in out
        monkeypatch.setattr(cr, "local_call", lambda *a, **k: {"courier": {"version": "9", "pairing_trouble": None, "connected_to": "Rig"}, "programs": []})
        out = cr.status_text(s)
        assert "Connected to Rig" in out and "Pairing code" not in out
        monkeypatch.setattr(cr, "local_call", lambda *a, **k: {"courier": {"version": "9", "connected_to": None,
                                                                          "pairing_trouble": {"address": "10.0.0.5", "new_code": False}}, "programs": []})
        out = cr.status_text(s)
        assert f"Pairing code: {s.code}" in out and "A device at 10.0.0.5 tried to connect with an old pairing code." in out

    def test_status_when_the_courier_is_not_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, "local_call", lambda *a, **k: None)
        s = self.make(tmp_path, False)
        assert f"Pairing code: {s.code}" in cr.status_text(s)
        s.note_pairing("Rig")
        out = cr.status_text(s)
        assert "Paired with Rig." in out and "Pairing code" not in out

    def test_code_says_already_paired_instead_of_the_used_code(self, tmp_path):
        s = self.make(tmp_path, False)
        assert cr.code_text(s) == s.code
        s.note_pairing("Rig")
        assert cr.code_text(s, "linux") == "This device is already paired with Rig. To make a new pairing code, run multiproxy-courier new-code."
        assert s.code not in cr.code_text(s, "linux")
        assert cr.code_text(s, "win32").endswith("choose Make new pairing code in the tray menu.")

    def test_the_real_commands(self, tmp_path):
        s = self.make(tmp_path, True)
        run = lambda cmd: subprocess.run([sys.executable, str(ROOT / "courier.py"), cmd, "--config-dir", str(tmp_path), "--port", "1"], capture_output=True, text=True, timeout=60)
        assert "already paired with Rig" in run("code").stdout and run("paired").stdout.strip() == "Rig"
        assert s.code not in run("code").stdout


class TestInstallerPrintout:
    def test_the_linux_install_ends_by_checking_the_pairing(self):
        sh = (ROOT / "linux" / "courier-install.sh").read_text(encoding="utf-8")
        assert 'courier.py paired --config-dir "$SETTINGS_DIR"' in sh
        assert 'Already paired with $PAIRED_WITH. Nothing more to do.' in sh
        assert sh.index('if [ -n "$PAIRED_WITH" ]') < sh.index("Pairing code:  $CODE")
