# v4.2.1 — Put every model to the test (polish)

![MultiProxy 4.2](https://raw.githubusercontent.com/awesmdiver/skyrimnet-multiproxy/master/assets/release-banner-v4-2-0.jpg)

## ✨ What's New

**A quicker close, fewer empty squares, and fixes for the things you reported.**

MultiProxy 4.2.1 is a small polish update: no new tabs, just a few wrinkles smoothed out.

### Closing and starting
* **Quits in seconds:** MultiProxy now frees its port first and switches off your local models all at once, so you can start it again right away. The log notes how long closing took.

### Your AI services
* **Claude answers:** Newer Claude models no longer spend the small answer budget on thinking. If an answer still runs out of room, MultiProxy asks once more, or tells you plainly: "The AI ran out of room before it finished its answer."
* **ChatGPT card:** The list shows every model Codex offers your account, and **Check for models** asks Codex to refresh an old list. It spends nothing.

### NPC lines
* **No more empty squares:** Skyrim's subtitle font cannot draw a long dash, so MultiProxy swaps it for a plain hyphen. You can switch this off in **Settings**.

### On your desktop
* **Track with ModPacer:** A link on **Overview** shows how to have ModPacer 1.5 or newer watch MultiProxy for updates. Hide it with "Don't show this link again", and bring it back in **Settings**.

### Your other PCs
* **Courier 1.19:** Your spare PCs get the quicker stop and the Claude fix.

## 📥 Installation: which files do I need?

You only need the files for what you are setting up right now. The Courier is only for **additional** devices. You do not need the Courier on the PC where MultiProxy is installed.

| I want to... | Download |
| :--- | :--- |
| Run MultiProxy on my Windows gaming PC | `MultiProxy-Setup-4.2.1.exe` |
| Use a spare Windows PC or laptop (the Courier) | `MultiProxy-Courier-Setup-1.19.exe` (or `MultiProxy-Courier-1.19.zip` to skip the installer) |
| Run MultiProxy on Linux | `MultiProxy-4.2.1-linux.tar.gz` |
| Use a spare Linux PC (the Courier) | `MultiProxy-Courier-1.19-linux.tar.gz` |
| Install the Skyrim mod by hand | `SkyrimNetMultiProxy.zip` |
| Check a download is not damaged | `SHA256SUMS.txt` |

### Windows: your gaming PC
1. Download and run `MultiProxy-Setup-4.2.1.exe`, then press Next.
2. Press **Open folder** and drag the included Skyrim mod into Vortex or Mod Organizer 2 (you can also do this later).
3. Finish with **Open the dashboard now** ticked so MultiProxy is ready.

### Windows: a spare PC or laptop (the Courier)
1. On your gaming PC, go to the **Other devices** tab, press **Add a PC**, and copy the address it shows.
2. On the spare PC, open that address, download the Courier, and run it. Keep the ticked boxes, press Install, and press Yes if Windows asks about the firewall.
3. On your gaming PC, press **Add** when it asks to let the new PC join.

If you skip the installer, unzip `MultiProxy-Courier-1.19.zip` on the spare PC and double-click **Set up this PC**. An older Courier needs the new installer once, or the **Update Courier** button.

### Linux
These downloads were tested in Ubuntu 24.04 containers. You need `python3` (version 3.10 or newer).

**MultiProxy:**
1. Download and unpack the file: `tar -xzf MultiProxy-4.2.1-linux.tar.gz`
2. Open the `multiproxy` folder in a terminal. Run `./install.sh` and press Enter to keep each default [bracket] answer.
3. Open the dashboard link it shows at the end (`http://127.0.0.1:8000`).

Skyrim runs under Proton on Linux and cannot start MultiProxy. MultiProxy runs as a background service for SkyrimNet to find. Do not install the Skyrim mod on Linux.

**The Courier on a spare Linux PC:**
1. Download and unpack the file: `tar -xzf MultiProxy-Courier-1.19-linux.tar.gz`
2. Open the `multiproxy-courier` folder in a terminal and run `./install.sh`.
3. Copy the pairing code. On your gaming PC dashboard, go to **Other devices**, find the device, type the code, and press **Pair**.

To check a download is not damaged, run `sha256sum -c SHA256SUMS.txt` in the folder with your files.

## 💬 You told us, we listened

4.2.1 is mostly your reports: the slow quit, empty squares in subtitles, answers cut short, and the ChatGPT list. Keep them coming.

* **Next:** A view to show how large SkyrimNet prompts really are, tested settings for specific jobs, and simpler starting points for common PCs.

Is something still hard to use? [Tell us on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues).

## 📋 Good to Know

* **How to update:** Run the new installer. Pick your old MultiProxy folder when asked so you keep your settings, then install the new Skyrim mod over the old one in your mod manager.
* **Second computers:** Older Courier devices need the new installer once, or the **Update Courier** button. After that, updates take one click.
* **Your home network only:** Your PCs talk over your home network. Messages are not encrypted, so only use MultiProxy on a network you trust.
* **Linux:** We included an `install.sh` script, but it is only tested on Ubuntu 24.04 containers so far. Reports from a real Linux PC are welcome!
* **SkyrimNet version:** Quick decisions require SkyrimNet Beta 26 rc2 or newer.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
