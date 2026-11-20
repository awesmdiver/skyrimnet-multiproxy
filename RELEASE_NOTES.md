# v4.3.0 — A fresh breeze for every conversation

![A cozy, painterly fantasy tavern lit by warm lamplight, featuring a cheerful bard speaking as glowing sound waves and notes float gently through the air.](https://raw.githubusercontent.com/awesmdiver/skyrimnet-multiproxy/master/assets/release-banner-v4-3-0.jpg)

## ✨ What's New

**A new voice, a guide to pick one, and faster first lines.**

### Voices
* **Breeze voice:** A new voice that copies a short clip and follows directions on how to speak. English only, free for personal use. Pick how it sounds on the **Voice** tab: **Quickest**, **Standard**, **More natural** or **Most natural**.
* **Which voice?:** A new link on the **Voice** tab opens a guide. Hear a made-up man and woman in every voice, with no internet needed, then pick one with a click.
* **Quick first lines:** Characters taking turns no longer wait for their voice to be set up. MultiProxy now gets Breeze and Qwen3-TTS voices ready in the background, and Qwen3-TTS and Chatterbox keep them ready.
* **New characters mid-game:** A voice SkyrimNet adds while you play now gets its words worked out, and its first lines wait a moment instead of failing.

### Decisions
* **Google Decisions:** Google's decision model joins **Who answers decisions** on the **Decisions** tab. It says how sure it is, so the "less than 60% sure" rule works for it. It uses your Gemini key.

### Your PCs
* **Old graphics driver:** If your NVIDIA driver is too old for MultiProxy's engine, **Overview** and the **Voice** tab now say so in two short steps.
* **Waiting for a spare PC:** While a spare PC starts, the **Voice** and **Decisions** tabs keep showing the voice or model you chose, with a line like "Waiting for MSI to come online".
* **Higgs installs again:** New Higgs installs had stopped working, and they work again. Press **Install** on the **Voice** tab once to update an older voice engine.
* **Courier 1.22:** Press **Update Courier** on your spare PCs to bring them all of this.

## 📥 Installation: which files do I need?

You only need the files for what you are setting up right now. The Courier is only for **additional** devices. You do not need the Courier on the PC where MultiProxy is installed.

| I want to... | Download |
| :--- | :--- |
| Run MultiProxy on my Windows gaming PC | `MultiProxy-Setup-4.3.0.exe` |
| Use a spare Windows PC or laptop (the Courier) | `MultiProxy-Courier-Setup-1.22.exe` (or `MultiProxy-Courier-1.22.zip` to skip the installer) |
| Run MultiProxy on Linux | `MultiProxy-4.3.0-linux.tar.gz` |
| Use a spare Linux PC (the Courier) | `MultiProxy-Courier-1.22-linux.tar.gz` |
| Install the Skyrim mod by hand | `SkyrimNetMultiProxy.zip` |
| Check a download is not damaged | `SHA256SUMS.txt` |

### Windows: your gaming PC
1. Download and run `MultiProxy-Setup-4.3.0.exe`, then press Next.
2. Press **Open folder** and drag the included Skyrim mod into Vortex or Mod Organizer 2 (you can also do this later).
3. Finish with **Open the dashboard now** ticked so MultiProxy is ready.

### Windows: a spare PC or laptop (the Courier)
1. On your gaming PC, go to the **Other devices** tab, press **Add a PC**, and copy the address it shows.
2. On the spare PC, open that address, download the Courier, and run it. Keep the ticked boxes, press Install, and press Yes if Windows asks about the firewall.
3. On your gaming PC, press **Add** when it asks to let the new PC join.

If you skip the installer, unzip `MultiProxy-Courier-1.22.zip` on the spare PC and double-click **Set up this PC**. An older Courier needs the new installer once, or the **Update Courier** button.

### Linux
These downloads were tested in Ubuntu 24.04 containers. You need `python3` (version 3.10 or newer).

**MultiProxy:**
1. Download and unpack the file: `tar -xzf MultiProxy-4.3.0-linux.tar.gz`
2. Open the `multiproxy` folder in a terminal. Run `./install.sh` and press Enter to keep each default [bracket] answer.
3. Open the dashboard link it shows at the end (`http://127.0.0.1:8000`).

Skyrim runs under Proton on Linux and cannot start MultiProxy. MultiProxy runs as a background service for SkyrimNet to find. Do not install the Skyrim mod on Linux.

**The Courier on a spare Linux PC:**
1. Download and unpack the file: `tar -xzf MultiProxy-Courier-1.22-linux.tar.gz`
2. Open the `multiproxy-courier` folder in a terminal and run `./install.sh`.
3. Copy the pairing code. On your gaming PC dashboard, go to **Other devices**, find the device, type the code, and press **Pair**.

To check a download is not damaged, run `sha256sum -c SHA256SUMS.txt` in the folder with your files.

## 💬 You told us, we listened

4.3.0 is about voices: you asked for faster first lines, a way to hear a voice before picking it, and a clearer word when something is missing. Keep the ideas coming.

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
