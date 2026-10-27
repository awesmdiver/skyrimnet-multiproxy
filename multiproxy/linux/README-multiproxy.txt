SkyrimNet MultiProxy for Linux
==============================

START HERE
  1. Unpack this package and open a terminal in its folder.
  2. Run  ./install.sh
  3. Press Enter to keep each answer shown in [brackets], or type your own.
  4. Open the dashboard it shows at the end (http://127.0.0.1:8000) and follow Get started.

After that, the  multiproxy  command handles everything.
  multiproxy status      is it running, and how many AIs are ready
  multiproxy start       start it
  multiproxy stop        stop it
  multiproxy restart     stop it, then start it again
  multiproxy logs        follow the log (Ctrl+C to leave)
  multiproxy open        open the dashboard in your browser
  multiproxy tray        show a tray icon (only on a desktop that has a system tray)
  multiproxy uninstall   remove it (keeps your settings unless you add --remove-settings)
  multiproxy help        this list

  If "multiproxy" isn't found, add ~/.local/bin to your PATH, or run ~/.local/share/multiproxy/multiproxy directly.

Skyrim on Linux (Proton)
  Skyrim runs in Proton, so its mods can't start Linux programs. MultiProxy runs by itself as a
  background service on this computer, and Skyrim just connects to it. You don't start or stop it with the game.
  1. Run the installer above and say yes to "Start it when you log in?".
  2. In SkyrimNet, point the provider to MultiProxy's address (shown on the dashboard's Setup tab).
  3. Just play. MultiProxy is already running.

Missing the tray icon?
  Some desktops (like plain GNOME) hide tray icons. You aren't missing out: the command above and the
  dashboard do everything the tray would.

Where things live
  Program:           ~/.local/share/multiproxy          (updates overwrite this)
  Settings and logs: ~/.config/multiproxy               (kept safe during updates; your keys live here)
  You can choose different folders when the installer asks.

UPDATING
  Unpack the new package and run ./install.sh again. Press Enter to keep your current folders. It replaces the
  program and keeps your settings.

Start when the computer boots (optional)
  The service starts when you log in. To start it at boot, before anyone logs in, run this once:
      loginctl enable-linger $USER

SAFETY
  MultiProxy only listens on this computer (127.0.0.1). Nothing is exposed to your network or the internet.
