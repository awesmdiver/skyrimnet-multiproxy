# v4.2.0 — Put every model to the test

![MultiProxy 4.2](https://raw.githubusercontent.com/awesmdiver/skyrimnet-multiproxy/master/assets/release-banner-v4-2-0.jpg)

## ✨ What's New

**Put every model to the test, on your PC or another one.**

MultiProxy 4.2 adds **Benchmarks**: a new tab that tests your AI models to show exactly how fast each one answers and how much graphics memory it uses, helping you pick the one that fits alongside Skyrim.

### Benchmarks
* **Run anywhere:** Tick your models, press **Start benchmark**, and watch live speed, memory, and temperature on your gaming PC or a spare one running the Courier.
* **Check your fit:** **On this card right now** shows what is using your graphics card, while the fit dots and start warnings calculate based on the memory that is truly free.
* **Get real numbers:** Each result shows exactly how much memory the model, its cache, and its workspace actually used, next to our estimate from before the run.
* **Protect your PC:** If a test starts running your PC out of memory, MultiProxy safely stops that model by itself and moves to the next one.
* **Know your model:** See your model's size, layers, and max conversation length before and during a test, along with which engine is running it and how much memory short and long chats will need.
* **AMD cards (new):** Benchmarks now reads AMD graphics cards, but this is new and not fully tested. If anything looks wrong, please let us know on the [Issues page](https://github.com/awesmdiver/skyrimnet-multiproxy/issues).
* **Save your results:** Keep your test scores to easily compare runs and find out which models are fast enough for smooth play.

### Your other PCs
* **Update from afar:** If **Model engine (llama.cpp)** on a spare PC is older than yours, simply click **Update engine** on its card, and watch for update warnings even when cards are closed.
* **Upgrade the Courier:** Version 1.18 brings all these new benchmark and update features to your spare PCs.

### Voice and pictures
* **Understand voice errors:** The log now explains exactly why a voice refused a line, and automatically fixes unreadable clips before sending them.
* **Show pictures to Claude:** Pictures now reach Claude and Codex, the Vision **Test** on **Routing** proves the AI saw them, and Grok and Copilot clearly tell you they cannot see pictures.
* **Spot language limits:** MultiProxy now warns you if SkyrimNet asks a voice to speak a language it does not know.

### On your desktop
* **Check memory users:** Hover over the colours on the **Graphics memory** bar under **Local device** to see exactly which programs are using your graphics card.

## 🔧 Improvements & Polish

* **Smaller fixes and polish:** We added clearer names on **Overview**, a helpful colour key at the top, made the **Watch activity live** window stay open, and more.

## 📥 Installation: which files do I need?

You only need the files for what you are setting up right now. The Courier is only for **additional** devices. You do not need the Courier on the PC where MultiProxy is installed.

| I want to... | Download |
| :--- | :--- |
| Run MultiProxy on my Windows gaming PC | `MultiProxy-Setup-4.2.0.exe` |
| Use a spare Windows PC or laptop (the Courier) | `MultiProxy-Courier-Setup-1.18.exe` (or `MultiProxy-Courier-1.18.zip` to skip the installer) |
| Run MultiProxy on Linux | `MultiProxy-4.2.0-linux.tar.gz` |
| Use a spare Linux PC (the Courier) | `MultiProxy-Courier-1.18-linux.tar.gz` |
| Install the Skyrim mod by hand | `SkyrimNetMultiProxy.zip` |
| Check a download is not damaged | `SHA256SUMS.txt` |

### Windows: your gaming PC
1. Download and run `MultiProxy-Setup-4.2.0.exe`, then press Next.
2. Press **Open folder** and drag the included Skyrim mod into Vortex or Mod Organizer 2 (you can also do this later).
3. Finish with **Open the dashboard now** ticked so MultiProxy is ready.

### Windows: a spare PC or laptop (the Courier)
1. On your gaming PC, go to the **Other devices** tab, press **Add a PC**, and copy the address it shows.
2. On the spare PC, open that address, download the Courier, and run it. Keep the ticked boxes, press Install, and press Yes if Windows asks about the firewall.
3. On your gaming PC, press **Add** when it asks to let the new PC join.

If you skip the installer, unzip `MultiProxy-Courier-1.18.zip` on the spare PC and double-click **Set up this PC**. An older Courier needs the new installer once, or the **Update Courier** button.

### Linux
These downloads were tested in Ubuntu 24.04 containers. You need `python3` (version 3.10 or newer).

**MultiProxy:**
1. Download and unpack the file: `tar -xzf MultiProxy-4.2.0-linux.tar.gz`
2. Open the `multiproxy` folder in a terminal. Run `./install.sh` and press Enter to keep each default [bracket] answer.
3. Open the dashboard link it shows at the end (`http://127.0.0.1:8000`).

Skyrim runs under Proton on Linux and cannot start MultiProxy. MultiProxy runs as a background service for SkyrimNet to find. Do not install the Skyrim mod on Linux.

**The Courier on a spare Linux PC:**
1. Download and unpack the file: `tar -xzf MultiProxy-Courier-1.18-linux.tar.gz`
2. Open the `multiproxy-courier` folder in a terminal and run `./install.sh`.
3. Copy the pairing code. On your gaming PC dashboard, go to **Other devices**, find the device, type the code, and press **Pair**.

To check a download is not damaged, run `sha256sum -c SHA256SUMS.txt` in the folder with your files.

## 💬 You told us, we listened

Setting up local AI for SkyrimNet is harder than it should be. We know the main pain points: connecting models to the game, using a second PC, managing graphics memory, and knowing which models actually work well.

* **Done in 4.2:** **Benchmarks** tests your models the way SkyrimNet uses them, with many requests at once, showing you how fast each one answers and how much graphics memory it needs.
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
