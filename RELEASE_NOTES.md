# v4.1.0 — Every model in one place

![MultiProxy 4.1](https://raw.githubusercontent.com/awesmdiver/skyrimnet-multiproxy/master/assets/release-banner-v4-1-0.jpg)

## ✨ What's New

**Setting up is easier, and every model is in one place.**

MultiProxy 4.1 takes you from a fresh install to working local models in a few clicks. It lets you manage every model across all your PCs and gives you more control over who answers what.

### Getting set up
* **Set me up:** Checks your graphics card and your game, shows which models fit alongside Skyrim, and installs the ones you tick. If nothing fits on your gaming PC, it points you to a spare PC or a cloud model.
* **Add a PC:** Press **Add a PC** and open the provided address on a spare PC. The Courier pairs itself when you say yes. No pairing codes to type.
* **Check everything:** One button on **Overview** runs a short list of checks. It only shows what is wrong and tells you how to fix it.
* **Share a setup:** Save your routes and choices to a small file to load on another MultiProxy. It saves no keys or file paths.

### Your models
* **One list of every model:** The new **Models** tab shows every model on all your PCs and where it is used. You can start, stop, remove, or delete models from here.
* **Remove, but keep the files:** When you remove a model, you can keep its files. Adding it back or moving it to another PC needs no new download.
* **Downloads you can see and pause:** Every download shows the time remaining. You can pause and resume them, even after restarting MultiProxy.
* **Keep the engine current:** When a newer **Model engine (llama.cpp)** is ready, an **Update llama.cpp** button appears for this PC and your spare PCs.
* **A guard for your game:** If your graphics memory gets too full while Skyrim is running, MultiProxy stops its local chat model to prevent a game crash.

### Quick decisions
* **Choose who answers:** **Who answers decisions** is a numbered list you drag into your preferred order. If the top choice is slow or unsure, it hands the question to the next one down.
* **Cloud only:** Low on graphics memory? Tick **Cloud only**. No PC will start a local decision model.
* **Cleaner moves:** When you move decisions or the voice to another PC, the copy on the first PC stops by itself.

### Routing and your other PCs
* **A clearer Routing tab:** **Needs attention** only shows real problems. **Jobs with no model yet** tells you what to do. **Priority when busy** ensures the game's own jobs always go first.
* **The engine on other PCs:** Open **Model engine (llama.cpp)** on any paired PC to see the same screens as your gaming PC. Pick, download, start, stop, and remove models remotely.
* **Start and Shut down:** Remote Ollama and LM Studio programs now have **Start**, **Restart**, and **Shut down** buttons on **Overview**. Uninstalled programs no longer show up.
* **Courier 1.11:** The latest Courier update brings all these features to your spare PCs.

### Skyrim mod
* **Works on Skyrim VR:** One mod file now covers Skyrim SE, AE, and VR.
* **No Address Library needed:** The mod works without it, so the missing library pop-up is gone.
* **One file for every version:** We removed the separate Skyrim 1.7 download. The standard mod file in the installer is all you need.

## 🔧 Improvements & Polish

* **Clearer wording:** We polished the text on the Local device, Models, Decisions, Routing, and Other devices tabs.
* **Names in Windows' Startup list:** MultiProxy and the Courier now show their real names there, rather than "Python".
* **Quits with Skyrim, even on a crash:** If **Start and stop with Skyrim** is on, MultiProxy notices if the game closes unexpectedly.
* **Steadier Claude:** Claude answers that arrive compressed no longer fail.
* **Calmer colours:** An unloaded model simply shows a grey dot. Red is saved for real problems.

## 📥 Installation: which files do I need?

You only need the files for what you are setting up right now. The Courier is only for **additional** devices. You do not need the Courier on the PC where MultiProxy is installed.

| I want to... | Download |
| :--- | :--- |
| Run MultiProxy on my Windows gaming PC | `MultiProxy-Setup-4.1.0.exe` |
| Use a spare Windows PC or laptop (the Courier) | `MultiProxy-Courier-Setup-1.11.exe` (or `MultiProxy-Courier-1.11.zip` to skip the installer) |
| Run MultiProxy on Linux | `MultiProxy-4.1.0-linux.tar.gz` |
| Use a spare Linux PC (the Courier) | `MultiProxy-Courier-1.11-linux.tar.gz` |
| Install the Skyrim mod by hand | `SkyrimNetMultiProxy.zip` |
| Check a download is not damaged | `SHA256SUMS.txt` |

### Windows: your gaming PC
1. Download and run `MultiProxy-Setup-4.1.0.exe`, then press Next.
2. Press **Open folder** and drag the included Skyrim mod into Vortex or Mod Organizer 2 (you can also do this later).
3. Finish with **Open the dashboard now** ticked so MultiProxy is ready.

### Windows: a spare PC or laptop (the Courier)
1. On your gaming PC, go to the **Other devices** tab, press **Add a PC**, and copy the address it shows.
2. On the spare PC, open that address, download the Courier, and run it. Keep the ticked boxes, press Install, and press Yes if Windows asks about the firewall.
3. On your gaming PC, press **Add** when it asks to let the new PC join.

If you skip the installer, unzip `MultiProxy-Courier-1.11.zip` on the spare PC and double-click **Set up this PC**. An older Courier needs the new installer once, or the **Update Courier** button.

### Linux
These downloads were tested in Ubuntu 24.04 containers. You need `python3` (version 3.10 or newer).

**MultiProxy:**
1. Download and unpack the file: `tar -xzf MultiProxy-4.1.0-linux.tar.gz`
2. Open the `multiproxy` folder in a terminal. Run `./install.sh` and press Enter to keep each default [bracket] answer.
3. Open the dashboard link it shows at the end (`http://127.0.0.1:8000`).

Skyrim runs under Proton on Linux and cannot start MultiProxy. MultiProxy runs as a background service for SkyrimNet to find. Do not install the Skyrim mod on Linux.

**The Courier on a spare Linux PC:**
1. Download and unpack the file: `tar -xzf MultiProxy-Courier-1.11-linux.tar.gz`
2. Open the `multiproxy-courier` folder in a terminal and run `./install.sh`.
3. Copy the pairing code. On your gaming PC dashboard, go to **Other devices**, find the device, type the code, and press **Pair**.

To check a download is not damaged, run `sha256sum -c SHA256SUMS.txt` in the folder with your files.

## 💬 You told us, we listened

Setting up local AI for SkyrimNet is harder than it should be. We know the main pain points: connecting models to the game, using a second PC, managing graphics memory, and knowing which models actually work well.

* **Done in 4.1:** **Set me up** picks models that fit alongside Skyrim, **Add a PC** replaces typing pairing codes, the **Models** tab shows everything across all your PCs, and a memory guard stops local chat models before Skyrim crashes.
* **Next:** A tool to test models with many requests at once (just like SkyrimNet does), a view to show how large SkyrimNet prompts really are, tested settings for specific jobs, and simpler starting points for common PCs.

Is something still hard to use? [Tell us on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues).

## 📋 Good to Know

* **How to update:** Run the new installer. Pick your old MultiProxy folder when asked so you keep your settings, then install the new Skyrim mod over the old one in your mod manager.
* **Second computers:** Older Courier devices need the new installer once, or the **Update Courier** button. After that, updates take one click.
* **Your home network only:** Your PCs talk over your home network. Messages are not encrypted, so only use MultiProxy on a network you trust.
* **Linux:** We included an `install.sh` script, but it is only tested on Ubuntu 24.04 containers so far. Reports from a real Linux PC are welcome!
* **SkyrimNet version:** Quick decisions require SkyrimNet Beta 26 rc2 or newer.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
