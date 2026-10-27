MultiProxy Courier
==================

START HERE
  1. Unzip Courier, then double-click "Set up this PC" (Windows) or run `bash install-courier.sh` (Linux).
  2. Say yes if asked to install PowerShell 7 or Python.
  3. Press Enter to keep the suggested folder.
  4. Say yes to the firewall question, then press Yes in the Windows pop-up.
  5. Copy the pairing code shown at the end.
  6. On your main device: MultiProxy > Other devices > find this device > type the code > Pair.
  7. To start it later: open MultiProxy Courier from the Start menu, or run `multiproxy-courier start` on Linux.

A helper app for your other computers. It runs models here to save your main device's graphics power for Skyrim. Manage it from the Other devices tab on your main device.

On Linux: run 'bash install-courier.sh'. It installs, starts up, and gives your pairing code. Check on it anytime with:
    multiproxy-courier status

STARTING AND UPDATING
  Start it: open MultiProxy Courier from the Start menu (Windows) or run 'multiproxy-courier start' (Linux). You don't need to run setup again.
  Update it: double-click "Set up this PC" from the new download and press Enter to keep your folder. It updates and keeps your settings.

WHERE MODELS ARE SAVED
  The first time you download a model to this device from the Other devices tab, it asks which
  drive and folder to use (you can change it later from that device's settings there). Models on this device, like ones LM Studio downloaded, can be used where they are: click Find models on ...

SAFETY
  It only answers computers on your home network, and every request needs the pairing code. Nothing is open to the internet.

TO REMOVE IT (Windows)
  Go to Windows Settings > Apps > MultiProxy Courier > Uninstall. Tick what you want to delete.
  (If you used "Set up this PC" instead of the installer, there is no Uninstall. Right-click the tray icon, untick "Start with Windows", then click Quit Courier. Delete its folder and the "MultiProxy Courier" shortcut in the Start menu. Its two "MultiProxy Courier" firewall rules are harmless; delete them in Windows Defender Firewall > Advanced settings > Inbound Rules if you like.)
TO REMOVE IT (Linux)
  Run 'systemctl --user disable --now multiproxy-courier' and delete its folder.
