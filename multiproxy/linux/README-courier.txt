MultiProxy Courier for Linux
============================

A helper for your other computers. It lets your main PC run AI models here, so it can keep its
graphics card power for Skyrim. Set it up once, then manage everything from the "Other devices" tab on your main device.

START HERE
  1. Unpack this package and open a terminal in its folder.
  2. Run  ./install.sh
  3. Press Enter to keep each answer shown in [brackets], or type your own.
  4. Copy the pairing code shown at the end.
  5. On your main device: MultiProxy > Other devices > find this device > type the code > Pair.

After that, the  multiproxy-courier  command handles everything.
  multiproxy-courier status      is it running, and is your main device connected
  multiproxy-courier code        show the pairing code again
  multiproxy-courier start       start it
  multiproxy-courier stop        stop it
  multiproxy-courier restart     stop it, then start it again
  multiproxy-courier vpn-networks  other VPN or Tailscale networks: list, add 10.8.0.0/16, remove 10.8.0.0/16
  multiproxy-courier logs        follow the log (Ctrl+C to leave)
  multiproxy-courier uninstall   remove it (keeps your settings and pairing code unless you add --remove-settings)
  multiproxy-courier help        this list

  If "multiproxy-courier" isn't found, add ~/.local/bin to your PATH, or run ~/.local/share/multiproxy-courier/multiproxy-courier directly.

Voice (Higgs) on this computer
  Your main PC can install and run the Higgs voice here too, right from the Other devices tab. On Linux, this works with NVIDIA
  graphics cards today (AMD and Intel cards haven't been tested yet). It also needs the CUDA 12 libraries from your
  distribution (cuBLAS, cuFFT and NCCL), and a Linux as new as Ubuntu 22.04.

Where things live
  Program:                         ~/.local/share/multiproxy-courier   (updates overwrite this)
  Settings, pairing code, and logs: ~/.config/multiproxy-courier       (kept safe during updates)

UPDATING
  Unpack the new package and run ./install.sh again. Press Enter to keep your current folders.

Keep it running without a login
  The installer asks your system to keep the service running even when no one is logged in. If it fails, run this once:
      loginctl enable-linger $USER

FIREWALL
  If your firewall (ufw) is on, the installer prints two commands to let your main PC connect here.

SAFETY
  Courier only answers computers on your home network, and every request requires the pairing code. Nothing is exposed to the internet.
