![License](https://img.shields.io/badge/License-MIT-yellow.svg) ![Platform](https://img.shields.io/badge/Skyrim-SE-blue.svg)

# Claude SkyrimNet Proxy Launcher

> **Everything you need to power SkyrimNet's AI NPC conversations with your Claude subscription — download, unzip, configure once, and Skyrim starts it for you from then on.**

---

## ⚡ Overview

[SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin) lets you use your Claude subscription for
in-game NPC conversations, but it needs a small proxy service running in the background to talk to
Claude — something you'd otherwise have to remember to start (and stop) by hand every time you play.

This release bundles everything: an SKSE plugin that starts and stops the proxy automatically with
Skyrim, and a complete, ready-to-run copy of the proxy itself. **No separate download, no patching
— it works out of the box.**

### 📋 At a Glance
| Feature | Details |
| :--- | :--- |
| **Requirements** | [SKSE64](https://skse.silverlock.org/), Python 3.10+, and the Claude CLI (logged into a Claude Max subscription) — the proxy itself is bundled, nothing else to download |
| **Performance Impact** | The plugin launches once at game start, otherwise idle — negligible. The proxy makes direct API calls after the first request (~2s), instead of ~9s per request via a subprocess |
| **Safety** | Never touches saves; if a proxy is already running (port 8000), it's left alone |
| **Compatibility** | Skyrim SE |

---

## ✨ Key Features

* **One download, everything included:** The release zip has the SKSE plugin and a fully working
  copy of the proxy — no separate repo to clone, no patch script to run.
* **Starts the proxy for you:** The moment Skyrim launches, the plugin checks if the proxy is
  already running and, if not, starts it automatically in the background.
* **Respects a proxy you started manually:** If something's already listening on the configured
  port, it's left alone rather than starting a duplicate.
* **Can auto-close with the game:** Turn on `AutoCloseWithSkyrim` in the proxy's own `proxy.ini`
  and it shuts itself down when Skyrim exits — no orphaned console windows left running.
* **Uses your existing Claude subscription:** The bundled proxy authenticates through the Claude
  CLI you're already logged into — no separate API key or per-token billing.
* **GLM (z.ai) and Nano-GPT support:** Want a model outside Anthropic's lineup? Add a z.ai or
  Nano-GPT API key and use `glmzai/MODEL` or `nano/provider/model` — fast NPC dialogue by default,
  with extended "thinking" handled transparently even on GLM models that require it.
* **Everything logged:** A launch-status log (this plugin) and, optionally, a full proxy activity
  log are written for easy troubleshooting.

---

## 📦 Getting Started

1. **Download the release zip** and unzip it anywhere (e.g. `C:\Tools\ClaudeSkyrimNetProxy\`).
2. **Install Python 3.10+** and make sure the **Claude CLI** is installed and logged in (`claude`
   should run without an auth error).
3. **Double-click `setup.bat`** inside the unzipped folder. It checks your Python install, sets
   everything else up, and prints the exact paths to use in the next step.
4. **Import `ProxyLauncher.zip`** with your mod manager (Vortex or MO2), same as any other mod, and
   enable it — `ProxyLauncher.dll` and `ProxyLauncher.ini` land in your game's
   `Data/SKSE/Plugins/` folder automatically.
5. **Edit `ProxyLauncher.ini`** — find it where your mod manager put it (`Data/SKSE/Plugins/`
   in your game folder, or open it straight from the mod's own entry in Vortex/MO2) — and set
   the three paths `setup.bat` printed for you:
   - `PythonExe` — full path to your Python executable
   - `ProxyScript` — full path to the unzipped `proxy.py`
   - `WorkDir` — the unzipped folder itself
   - `Port` — the port the proxy listens on (default `8000`)
6. **Launch Skyrim as normal** — the proxy starts itself.

> [!TIP]
> Prefer doing it by hand instead? `setup.bat` just runs `pip install -r requirements.txt` — feel
> free to run that yourself from a terminal in the unzipped folder instead.

> [!TIP]
> Set `PythonExe` to Python's full path rather than just `python` — Windows can silently redirect a
> bare `python` command to its own Store-app stub instead of your real install.

> [!TIP]
> Want OpenRouter, GLM (z.ai), or Nano-GPT models too, or the auto-close/logging options? Copy
> `config.example.json` to `config.json` and `proxy.ini.example` to `proxy.ini` in the unzipped
> folder — without them, sensible defaults apply and everything still works. You can also paste
> any of these keys straight into the proxy's own dashboard (`http://127.0.0.1:8000`) instead of
> editing the file by hand.

---

## ⚠️ Important Notes

> [!WARNING]
> `ProxyScript` and `WorkDir` in `ProxyLauncher.ini` must point at the unzipped proxy files before
> launching Skyrim — the plugin has nothing to launch without them.

> [!CAUTION]
> **The bundled proxy operates in a gray area of Anthropic's Terms of Service.** It uses the Claude
> CLI's authenticated session to make direct API calls, rather than going through the standard
> Claude Code interface — a method that may not be explicitly authorized under Anthropic's
> [Terms of Service](https://www.anthropic.com/legal/consumer-terms) or
> [Acceptable Use Policy](https://www.anthropic.com/legal/aup). Read both in full before using
> this. This access method could be restricted or blocked at any time, and your account could
> potentially be affected. This software is provided as-is, with no guarantee of continued
> functionality — you take on this risk yourself by using it.

---

## ❓ Frequently Asked Questions

* **Q: Do I need SkyrimNet installed for this to do anything?**
  > **Yes.** This launches/manages the proxy process — the actual in-game conversation feature
  > comes from [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin) itself.

---

* **Q: What if I already started the proxy myself before launching Skyrim?**
  > **It's left alone.** The plugin checks whether the configured port is already in use and
  > skips launching a second copy if so.

---

* **Q: Does this affect my save game?**
  > **No.** It only starts and stops a background process and makes API calls — it never reads or
  > writes save data.

---

* **Q: Will the proxy keep running after I close Skyrim?**
  > **By default, yes** — closing Skyrim doesn't close the proxy automatically. Turn on
  > `AutoCloseWithSkyrim` in `proxy.ini` if you'd rather it shut down with the game.

---

* **Q: Do I need to patch anything myself?**
  > **No.** Earlier releases required downloading `proxy.py` separately and running a patch
  > script against it — that's no longer necessary. The bundled `proxy.py` is already complete.

---

## 🛠️ Technical Details & Contributions

Build instructions, the SKSE lifecycle hooks used, and project structure all live in
[`TECHNICAL.md`](TECHNICAL.md).

---

## 🤝 Credits

* **[Galanx](https://github.com/galanx/Claude-SkyrimNet-Proxy)** — creator of the original
  Claude-SkyrimNet-Proxy (MIT License) the bundled proxy is based on. All of the core proxy/auth
  design is their work; several fixes from this project have been submitted upstream as
  [PR #5](https://github.com/galanx/Claude-SkyrimNet-Proxy/pull/5).
* **cleanestpoison** (Discord: `cleanestpoison_73104`) — original idea and merged `proxy.py` for
  GLM (z.ai) and Nano-GPT provider support.
* **[MinLL/SkyrimNet-GamePlugin](https://github.com/MinLL/SkyrimNet-GamePlugin)** — the in-game mod
  this whole setup exists to support.
* **[CommonLibSSE-NG](https://github.com/CharmedBaryon/CommonLibSSE-NG)** — the SKSE plugin
  framework this is built on.
* **Community:** Join the [SkyrimNet Discord](https://discord.gg/X7y5D7gFj) for support and
  discussion.

---

## 💬 Feedback & Issues

Bug reports, suggestions, and questions are tracked on GitHub — please [open an
issue](https://github.com/awesmdiver/claude-skyrimnet-proxy-launcher/issues) so nothing gets lost.
