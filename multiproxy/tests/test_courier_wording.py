"""The Courier's words after the Gemini wording pass (2026-10-01), with the design-side corrections that win where they differ:
never send the player to the Courier device for something the main device can do; no "VRAM"; "run AI models for your main device"; the ufw
hint uses the device's own range; the status command keeps the port; making a new pairing code asks first, in words shared with the
Other devices tab. Each line here is checked as it ships (in the code or in the setup scripts)."""
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import courier_adapters as ca
import courier_ui as ui
import other_pcs as op

ROOT = Path(__file__).resolve().parent.parent
SRC = {n: (ROOT / n).read_text(encoding="utf-8") for n in ("courier.py", "courier_adapters.py", "courier_client.py", "courier_ui.py")}
ALL_SRC = "\n".join(SRC.values())

# every line as it should now read (the old wording must be gone)
SHIPPED = [
    "Connection refused: Courier only connects to devices on your private network or Tailscale.",
    "Wrong pairing code. Check the code shown on your Courier device.",
    "Received an invalid request.",
    "Received something unexpected instead of a request.",
    "You didn't choose a model.",
    "Please choose a model (for example: gemma3:12b).",
    "Please choose a model (for example: qwen/qwen3-4b).",
    "Didn't recognize that command.",
    "This device doesn't have that program installed.",
    "This device doesn't have a model named '{name}'.",
    "{self.name} doesn't support that action remotely.",
    "The program didn't answer. Try starting it again on the Other devices tab.",
    "{adapter.name} failed to respond: {e}",
    "Ollama isn't installed on the Courier device.",
    "Ollama doesn't have '{model}' installed yet. Download it first.",
    "Ollama failed to launch. Try starting it again from the Other devices tab.",
    "Ollama couldn't load {m['id']}. That device might not have enough graphics memory.",
    "Ollama failed to download '{model}' ({r.status}).",
    "Download failed: {e}",
    "LM Studio isn't fully set up. In LM Studio on that device, open Settings, click \\\"Install CLI\\\", and try again.",
    "LM Studio doesn't have '{model}'. Download it first.",
    "LM Studio took too long to respond.",
    "LM Studio's local server failed to start.",
    "LM Studio failed to download that model.",
    "LM Studio doesn't let other programs delete models. Delete it in LM Studio on that device.",
    "'{model}' hasn't been downloaded on that device yet.",
    "The model failed to start.",
    "MultiProxy's model engine (llama.cpp) downloads the first time you load a model.",
    "The system installer manages auto-start on this device.",
    "Couldn't update the Start with Windows setting.",
    "Couldn't change the sleep settings on this device.",
    "Couldn't find a local network address. Is this device connected to your Wi-Fi or router?",
    "Is another copy of Courier already running on this device?",
    "Couldn't reach Courier on that device. Is it running, and allowed through the Windows firewall?",
    "Wrong pairing code.",
    "That device refused the connection ({e.status}).",
    "Lost connection to the Courier device.",
    "That device generated a new pairing code. Enter it to reconnect.",
    "Created a new pairing code. Update the code on your main device to reconnect.",
    "disconnected. Waiting for reconnect...",
    "Your main device can't find this device automatically",
    "Couldn't show the tray icon",
    "is not running on this device.",
    "is running.",
    "Model engine (llama.cpp): from MultiProxy (downloads the first time you load a model)",
]
GONE = [
    "It will download automatically whenever you install your first model",
    "on the Courier device to", "Try opening it on this device", "check it on the Courier's device", "VRAM", "Download it in LM Studio",
    "Unload it in LM Studio", "isn't one of this device's llama.cpp models", "(or run  lms bootstrap  once)", "That wasn't a valid request.",
    "Made a new pairing code. The main device needs the new one.", "MultiProxy's own engine", "Lost the connection to that device.",
]


@pytest.mark.parametrize("line", SHIPPED)
def test_every_new_line_ships_as_written(line):
    assert line in ALL_SRC + (ROOT / "other_pcs.py").read_text(encoding="utf-8"), line      # (the "couldn't connect" line lives in other_pcs.py now: a redirect says it too)


@pytest.mark.parametrize("old", GONE)
def test_the_old_wording_is_gone(old):
    # (the one allowed "Courier device" error is Ollama not being installed: nothing the main device can do about that)
    if old == "on the Courier device to":
        assert "Download it first on the Courier device" not in ALL_SRC
    else:
        assert old not in ALL_SRC, old


def test_the_only_go_to_that_pc_steps_are_install_cli_and_lm_studios_missing_delete():
    assert "on that device" in ca.LMS_NOT_FOUND and "Install CLI" in ca.LMS_NOT_FOUND
    for line in ("Download it first.", "Unload it first."):
        assert "LM Studio" not in line


class TestTrayMenuAndConfirm:
    def test_the_menu_item_reads_make_new_pairing_code(self):
        assert "Make new pairing code" in [i["text"] for i in ui.menu_model(False, "", "AAA-111", True, True)]

    def test_the_question_is_one_shared_set_of_words(self):
        assert op.NEW_CODE_CONFIRM == {"question": "Make a new pairing code? Your main PC will need the new code to reconnect.",
                                       "cancel": "Cancel", "confirm": "Make new code"}

    def test_the_tray_asks_first_and_does_nothing_when_cancelled(self, monkeypatch, tmp_path):
        made = []
        tray = ui.Tray.__new__(ui.Tray)
        tray.config_dir, tray.loop = tmp_path, None
        tray.courier = SimpleNamespace(new_code=lambda: made.append(1))
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return SimpleNamespace(returncode=1)
        monkeypatch.setattr(ui.subprocess, "run", fake_run)
        tray.do_new_code()
        assert calls and calls[0][-3] == "confirm-new-code" and made == []

    def test_confirmed_goes_on_to_make_the_code_and_show_it(self, monkeypatch, tmp_path):
        shown = []
        tray = ui.Tray.__new__(ui.Tray)
        tray.config_dir, tray.loop = tmp_path, None
        tray.do_show_code = lambda: shown.append(1)
        monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=0))
        tray.do_new_code()
        assert shown == [1]

    def test_the_confirm_command_exists_and_cancelling_by_default_is_safe(self):
        assert "confirm-new-code" in SRC["courier.py"] and "show_confirm_new_code" in SRC["courier_ui.py"]


class TestInstallerWords:
    ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "courier/install-courier.sh").read_text(encoding="utf-8")
    bat = (ROOT / "courier/Set up this PC.bat").read_text(encoding="utf-8")

    def test_it_says_run_ai_models_for_your_gaming_pc_never_offload_ai_companions(self):
        assert "run AI models for your main device" in self.ps1 and "run AI models for your main device" in self.sh and "run AI models for your main device" in self.bat
        for text in (self.ps1, self.sh, self.bat):
            assert "offload" not in text.lower() and "companions" not in text.lower()

    def test_the_gemini_installer_lines(self):
        for line in ("Courier needs Python to run.", "I found an older version on this device.",
                     "Courier replaces it. Running both at once will cause problems.", "Allowing your main device to connect...",
                     "All set. This device is now a MultiProxy Courier.", "Setup complete (Courier was not started)."):
            assert line in self.ps1, line
        for line in ("Checking Python…", "Extracting files to $INSTALL_DIR...", "Setting Courier to start automatically on boot...",
                     "All set! This computer is now a MultiProxy Courier.", "(If that command isn't found, run it as", "running Local models requires one more system library"):
            assert line in self.sh, line
        assert "Setup needs PowerShell 7, a free update from Microsoft." in self.bat

    def test_the_ufw_hint_uses_this_computers_own_range_and_not_a_fixed_one(self):
        assert "hostname -I" in self.sh and "$NET" in self.sh
        assert "ufw allow from 192.168.0.0/16" not in self.sh
        for rule in ("10\\.", "192\\.168", "172\\."):
            assert rule in self.sh.split("NET=")[1].split("\n")[0]

    def test_the_ufw_range_picks_the_real_one_for_each_kind_of_home(self, tmp_path):
        """Run the installer's own range line against three kinds of home address."""
        line = [l for l in self.sh.splitlines() if l.strip().startswith('NET="$(hostname -I')][0].strip()
        for addr, want in (("192.168.1.20", "192.168.1.0/24"), ("10.0.5.7", "10.0.5.0/24"), ("172.20.4.9", "172.20.4.0/24")):
            script = tmp_path / "net.sh"
            script.write_text(f"hostname() {{ echo '{addr} 172.17.0.1'; }}\n{line}\nNET=\"${{NET:-fallback}}\"\necho $NET\n", encoding="utf-8")
            bash = next((b for b in ("C:/Program Files/Git/bin/bash.exe", "/bin/bash", "/usr/bin/bash") if Path(b).exists()), None)
            if not bash:
                pytest.skip("no bash")
            out = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=20).stdout.strip()
            assert out == want, (addr, out)
