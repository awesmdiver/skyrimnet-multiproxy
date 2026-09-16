![License](https://img.shields.io/badge/License-MIT-yellow.svg) ![Platform](https://img.shields.io/badge/Skyrim-SE-blue.svg)

# SkyrimNet MultiProxy

![SkyrimNet MultiProxy](assets/readme-banner.jpg)

> **Connect your AI of choice to SkyrimNet — set it up once, and Skyrim starts it for you every time you play.**

[![Donate PayPal](https://img.shields.io/badge/Donate-PayPal-00457C?logo=paypal&logoColor=white)](https://paypal.me/awesmdiver)
[![Donate Venmo](https://img.shields.io/badge/Donate-Venmo-3D95CE?logo=venmo&logoColor=white)](https://www.venmo.com/u/awesmdiver)

---

## ⚡ Overview

[SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin) uses AI for a lot more than NPC dialogue: conversations, NPC actions, diaries, bios, the GameMaster, and more. All of that needs an OpenAI-compatible endpoint to talk to. SkyrimNet MultiProxy is that bridge: a lightweight background tool running on your PC that receives SkyrimNet's AI requests and routes each one to the AI model you choose.

Pick whichever AI fits your setup:

- **Subscriptions (no API keys):** Use your existing Claude, ChatGPT, or Google AI Pro account.
- **API keys:** OpenRouter, GLM (z.ai), Nano-GPT, OpenAI, Gemini, or Kilo Gateway.
- **Ollama:** Run free models locally on your PC, or connect to Ollama Cloud with an API key.

Everything is included in one package: an SKSE plugin that manages the proxy lifecycle alongside the game, and a complete, ready-to-run copy of the proxy.

### 📋 At a Glance
| Feature | Details |
| :--- | :--- |
| **Requirements** | [SKSE64](https://skse.silverlock.org/), Python 3.10+ (`setup.bat` can install it for you), and any supported AI option (Claude/ChatGPT login, an API key, or Ollama) |
| **Performance Impact** | The SKSE plugin runs once at startup, then goes idle. In-game reply speed depends on your chosen AI provider |
| **Safety** | Never touches save files. If the proxy is already running on its port, the plugin leaves it alone |
| **Compatibility** | Skyrim SE |

---

## ✨ Key Features

* **Complete package:** Includes both the SKSE plugin and the full proxy script in a single download.
* **Starts automatically:** When Skyrim boots, the plugin checks if the proxy is already active. If not, it launches it silently in the background.
* **Ten AI providers:** Claude, ChatGPT, and Google Antigravity (via CLI logins), OpenRouter, GLM (z.ai), Nano-GPT, OpenAI, Gemini, Kilo Gateway, and Ollama. Mix and match — SkyrimNet lets you give different jobs different models, and each request is routed automatically based on its model name.
* **Your Google subscription counts:** Already paying for Google AI Pro or Ultra? Install [Google's Antigravity CLI](https://antigravity.google), sign in once, and use `geminicli/MODEL` — no API key required. That login isn't limited to Gemini either: the dashboard lists whichever models your account can actually run, Claude and GPT-OSS models included, with a **Refresh** button for when Google changes the lineup.
* **Gemini API key support:** Prefer going straight to Google's API? Add a Google AI Studio key and use `gemini/MODEL` — nothing to install, and the dashboard lists every model your key can run.
* **No Claude required:** Use whichever provider you prefer. Claude configuration is only needed if you choose a Claude model.
* **Local web dashboard:** Visit `http://127.0.0.1:8000` to save keys, check provider status, and copy model strings. Selecting a provider displays its key, model list, and readiness status.
* **Automatic Ollama model list:** The dashboard detects and lists your locally installed Ollama models, complete with a **Refresh** button for newly pulled models. Enter an Ollama Cloud key to view hosted models instead.
* **Built-in Quick Test:** Test your connection to any configured provider directly from the dashboard and receive clear, plain-language error explanations if something goes wrong.
* **Optional auto-close:** Enable `AutoCloseWithSkyrim` in `proxy.ini` to shut down the proxy automatically when Skyrim closes.
* **Safe logging:** Launch status is always recorded, optional detailed proxy logs are available, and all API keys and session tokens are stripped before writing to disk.

---

## 📦 Getting Started

1. **Download and extract the release:** Extract the release zip anywhere on your system to create your `SkyrimNet MultiProxy` folder (e.g., `C:\Tools\SkyrimNet MultiProxy\`).
2. **Run the setup script:** Double-click **`setup.bat`** inside that folder. It checks for Python (and installs it via winget if it's missing), installs the required dependencies, and prints the exact path values you need for step 4.
3. **Install the SKSE plugin:** Import **`SkyrimNetMultiProxy.zip`** (it's inside your `SkyrimNet MultiProxy` folder) into your mod manager (Vortex or Mod Organizer 2) and enable it. This places `SkyrimNetMultiProxy.dll` and `SkyrimNetMultiProxy.ini` into your game's `Data/SKSE/Plugins/` directory.
4. **Configure `SkyrimNetMultiProxy.ini`:** Open the file (in `Data/SKSE/Plugins/` or via your mod manager) and fill in the values printed by `setup.bat`:
   - `PythonExe` — Full path to your `python.exe`
   - `ProxyScript` — Full path to `proxy.py` in your `SkyrimNet MultiProxy` folder
   - `WorkDir` — Full path to your `SkyrimNet MultiProxy` folder
   - `Port` — Port the proxy listens on (default: `8000`)
5. **Launch Skyrim:** Start Skyrim normally through SKSE. The proxy will launch in the background.
6. **Configure your AI provider:** Open `http://127.0.0.1:8000` in your browser, choose your provider, and enter your credentials:
   - **Claude:** Install the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) and complete the login.
   - **ChatGPT:** Install the [Codex CLI](https://www.npmjs.com/package/@openai/codex) and run `codex login` once in a terminal (note: responses are generally slower per reply).
   - **Google Antigravity:** Install the [Antigravity CLI](https://antigravity.google) — on Windows, run `irm https://antigravity.google/cli/install.ps1 | iex` in PowerShell — then run `agy` once and sign in with the Google account your AI Pro or Ultra subscription is on (note: responses are generally slower per reply, same as ChatGPT above).
   - **API Key providers:** Paste your key into the dashboard. For Gemini, that's a key from [Google AI Studio](https://aistudio.google.com/apikey).
   - **Ollama:** Ensure the Ollama service is running on your machine.
7. **Configure SkyrimNet:** In SkyrimNet's in-game or configuration settings:
   - Endpoint: `http://localhost:8000/v1/chat/completions`
   - API Key: Leave blank
   - Model: Copy your preferred model name directly from the dashboard using its **Copy** button.

> [!TIP]
> Always set `PythonExe` to the absolute path of `python.exe` instead of just writing `python`. On Windows, running bare `python` can accidentally invoke the Windows Store alias rather than your actual Python installation.

> [!TIP]
> To enable automatic shutdown or proxy debug logs, copy **`proxy.ini.example`** to **`proxy.ini`** in your `SkyrimNet MultiProxy` folder and adjust the settings. If you skip this, default behavior applies.

---

## 🔄 Upgrading from Claude SkyrimNet Proxy Launcher

If you are upgrading from **Claude SkyrimNet Proxy Launcher** (plugin previously named `ProxyLauncher.dll`):

1. **Replace the mod:** Remove the old `ProxyLauncher` mod in Vortex or MO2, then install and enable `SkyrimNetMultiProxy.zip`. On first run, if the plugin detects `ProxyLauncher.ini` and no `SkyrimNetMultiProxy.ini`, it automatically copies your old settings over.
2. **Update your paths:** Because the migrated settings still point to your old folder, open `SkyrimNetMultiProxy.ini` and update `ProxyScript` and `WorkDir` to your new `SkyrimNet MultiProxy` directory.
3. **Migrate your saved keys:** Copy `config.json` from your old proxy folder into the new `SkyrimNet MultiProxy` folder, or re-enter your keys via the dashboard.

---

## ⚠️ Important Notes

> [!WARNING]
> `ProxyScript` and `WorkDir` in `SkyrimNetMultiProxy.ini` must be configured with valid paths before launching Skyrim, or the SKSE plugin will be unable to start the proxy.

> [!CAUTION]
> **Using Claude through a subscription operates in a gray area of Anthropic's Terms of Service.** The proxy uses the Claude CLI's active session to make direct API requests rather than routing through the standard Claude Code interface. This workflow may not be explicitly authorized under Anthropic's [Terms of Service](https://www.anthropic.com/legal/consumer-terms) or [Acceptable Use Policy](https://www.anthropic.com/legal/aup). Please review both documents before using Claude models. Access could be throttled or revoked at any time, and your Anthropic account could potentially be affected. This software is provided as-is with no guarantee of continued compatibility; you assume all responsibility by using it. The other providers don't use this method.

---

## ❓ Frequently Asked Questions

* **Q: Do I need SkyrimNet installed for this to work?**
  > **Yes.** This tool is a proxy server built to handle the AI requests from [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin).

---

* **Q: Do I need a Claude subscription?**
  > **No.** You only need credentials for the specific provider you want to use. If you use Ollama, OpenRouter, or OpenAI, Claude is not required at all.

---

* **Q: Which AI provider should I choose?**
  > **Use what you already have.** If you already subscribe to Claude, ChatGPT, or Google AI Pro, that login works here through its own CLI. Ollama is free and runs locally if your hardware can handle the model. Everything else (OpenRouter, GLM, Nano-GPT, OpenAI, Gemini, Kilo Gateway) takes an API key from that service, on whatever terms you have with them.

---

* **Q: What happens if the proxy is already running when Skyrim starts?**
  > **Nothing breaks.** The plugin checks the port first; if it detects an active proxy, it leaves it alone and does not launch a duplicate instance.

---

* **Q: Does this mod alter save games?**
  > **No.** It is purely a process launcher and network proxy. It does not read, modify, or inject data into your `.ess` save files.

---

* **Q: Will the proxy keep running after I quit Skyrim?**
  > **By default, yes.** If you want it to shut down when you exit the game, set `AutoCloseWithSkyrim = true` in `proxy.ini`.

---

## 🛠️ Technical Details & Contributions

Build instructions, project architecture, and SKSE lifecycle hook details can be found in [`TECHNICAL.md`](TECHNICAL.md).

---

## 🤝 Credits

* **[Galanx](https://github.com/galanx/Claude-SkyrimNet-Proxy)** — Creator of the original Claude-SkyrimNet-Proxy (MIT License) upon which this proxy is built. Core proxy/auth architecture is their original work; upstream fixes from this project were submitted via [PR #5](https://github.com/galanx/Claude-SkyrimNet-Proxy/pull/5).
* **[rhinos0608/skyrimnet-codex-proxy](https://github.com/rhinos0608/skyrimnet-codex-proxy)** (MIT License) — The idea and code behind the OpenAI, Kilo Gateway, Ollama, and ChatGPT/Codex support, as well as the proxy's test suite framework.
* **cleanestpoison** (Discord: `cleanestpoison_73104`) — Original concept and merged implementation for GLM (z.ai) and Nano-GPT provider support.
* **[MinLL/SkyrimNet-GamePlugin](https://github.com/MinLL/SkyrimNet-GamePlugin)** — The Skyrim mod whose AI-driven NPCs, GameMaster, and more this proxy exists to power.
* **[CommonLibSSE-NG](https://github.com/CharmedBaryon/CommonLibSSE-NG)** — The SKSE plugin framework used to build the launcher DLL.
* **Community:** Join the [SkyrimNet Discord](https://discord.gg/X7y5D7gFj) for community support and modding discussion.

---

## 💬 Feedback & Issues

Bug reports, suggestions, and questions are tracked on GitHub — please [open an issue](https://github.com/awesmdiver/skyrimnet-multiproxy/issues) so your feedback can be addressed.
