# Polish the v2.3.0 README for SkyrimNet MultiProxy

Give a second pass to the full README below. I drafted it; I want it to read more naturally and to
be easier for a mod player to scan, without losing any real information.

## The project

**SkyrimNet MultiProxy** (v2.3.0, just renamed from "Claude SkyrimNet Proxy Launcher") is a free,
open-source download for Skyrim players. SkyrimNet is a Skyrim mod that gives NPCs AI-driven
dialogue; it needs an OpenAI-style chat endpoint to talk to. This download has two parts:

1. A small SKSE plugin (`SkyrimNetMultiProxy.dll` + `SkyrimNetMultiProxy.ini`, imported through
   Vortex or MO2) that starts the proxy in the background when Skyrim launches, and leaves it alone
   if it's already running.
2. The proxy itself (`proxy.py`, Python), which forwards each SkyrimNet request to the AI the player
   picked, based on the model name. Eight providers: Claude (through the player's Claude CLI login,
   no key), ChatGPT (through OpenAI's Codex CLI login, no key, slower per reply), OpenRouter, GLM
   (z.ai), Nano-GPT, OpenAI, Kilo Gateway (API keys), and Ollama (local models on the player's PC,
   or Ollama Cloud with a key). A local dashboard at http://127.0.0.1:8000 lets players paste keys,
   see which providers are ready, copy model names, list their installed Ollama models (with a
   Refresh button), and run a Quick Test that explains failures in plain words.

Facts that must stay true in anything you write:
- `setup.bat` checks for Python, installs it with winget if it's missing, installs the proxy's
  packages, and prints the paths the player needs for the .ini.
- The .ini needs `PythonExe`, `ProxyScript` (path to proxy.py), `WorkDir` (the unzipped folder), and
  `Port` (default 8000).
- The release zip unzips to one `SkyrimNet MultiProxy` folder.
- Upgrading from the old `ProxyLauncher`: remove the old mod; the new plugin copies an existing
  `ProxyLauncher.ini` to `SkyrimNetMultiProxy.ini` on first run, but those paths still point at the
  old folder and must be updated; `config.json` (saved keys) lives in the proxy folder and must be
  copied over or the keys re-pasted.
- Claude is optional. The Terms of Service caution applies only to using Claude through a
  subscription; keep its substance intact (it's a real risk notice).
- SkyrimNet setting: endpoint `http://localhost:8000/v1/chat/completions`, API key left empty.
- Credits must stay complete and accurate, including rhinos0608/skyrimnet-codex-proxy for the idea
  and code behind OpenAI, Kilo Gateway, Ollama, and ChatGPT/Codex support.

## Readers

Skyrim mod players. Many aren't developers. They want to know fast: what is this, does it work with
the AI I have (or with none), and how do I set it up.

## Voice

- Casual, friendly, plain language, like a helpful fellow modder. A little fun is welcome; marketing
  fluff isn't. Serious and measured only for the Terms of Service caution and real warnings.
- Short sentences, natural contractions, active voice. Bold exact file names, buttons, and settings
  where the draft does.
- Don't claim something is "easy" or "just a click" before the reader has done it.
- Keep every link, file name, path, and setting name exactly as written.
- Don't invent features, numbers, or claims beyond the facts above. If something in my draft looks
  wrong or unclear, point it out instead of guessing a fix.

## My draft (GitHub Markdown)

````markdown
![License](https://img.shields.io/badge/License-MIT-yellow.svg) ![Platform](https://img.shields.io/badge/Skyrim-SE-blue.svg)

# SkyrimNet MultiProxy

> **Connect your AI of choice to SkyrimNet — set it up once, and Skyrim starts it for you every time you play.**

---

## ⚡ Overview

[SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin) gives your NPCs AI-driven conversations,
but it needs something to talk to. SkyrimNet MultiProxy is that something: a small helper that runs on
your PC, takes SkyrimNet's requests, and sends each one to the AI you picked.

You decide where your NPCs get their dialogue:

- **Subscriptions (no API key needed):** Your existing Claude or ChatGPT account.
- **API keys:** OpenRouter, GLM (z.ai), Nano-GPT, OpenAI, or Kilo Gateway.
- **Ollama:** Free models running on your own PC, or Ollama Cloud with a key.

This download bundles everything: a Skyrim plugin that starts and stops the proxy with the game, and a
complete, ready-to-run copy of the proxy itself. No separate download, no patching.

### 📋 At a Glance
| Feature | Details |
| :--- | :--- |
| **Requirements** | [SKSE64](https://skse.silverlock.org/), Python 3.10+ (`setup.bat` can install it for you), and at least one AI: a Claude or ChatGPT subscription, an API key, or Ollama |
| **Performance Impact** | The plugin launches once at game start, then sits idle. Reply speed depends on the AI you pick |
| **Safety** | Never touches saves; if a proxy is already running on its port, it's left alone |
| **Compatibility** | Skyrim SE |

---

## ✨ Key Features

* **One download, everything included:** The Skyrim plugin and a fully working copy of the proxy, in
  one zip.
* **Starts with Skyrim:** When the game launches, the plugin checks whether the proxy is already
  running and starts it in the background if it isn't.
* **Eight AI choices:** Claude and ChatGPT (through your subscriptions), OpenRouter, GLM (z.ai),
  Nano-GPT, OpenAI, Kilo Gateway, and Ollama. Mix and match. Each request goes wherever its model
  name points.
* **No Claude required:** Use any provider on its own. Claude only needs to be set up if you pick a
  Claude model.
* **A clean dashboard:** Open `http://127.0.0.1:8000` to paste keys, see which providers are set up,
  and copy model names. Pick a provider from one dropdown to see its key, its models, and whether it's
  ready.
* **Your Ollama models, listed for you:** The dashboard shows the models you've actually installed,
  with a **Refresh** button for when you pull a new one. Add an Ollama Cloud key and it shows the
  hosted models instead.
* **Quick Test that tells you why:** Send a test line to any provider that's ready. If it fails, you
  get the reason in plain words.
* **Can auto-close with the game:** Turn on `AutoCloseWithSkyrim` in `proxy.ini` and the proxy shuts
  itself down when Skyrim exits.
* **Keys stay out of your logs:** Launch status is always logged, full proxy logging is optional, and
  API keys and login tokens are hidden before anything is written.

---

## 📦 Getting Started

1. **Download the release zip** and unzip it anywhere. You'll get a `SkyrimNet MultiProxy` folder
   (e.g. `C:\Tools\SkyrimNet MultiProxy\`).
2. **Double-click `setup.bat`** inside that folder. It checks for Python (and installs it if it's
   missing), installs what the proxy needs, and prints the exact paths for step 4.
3. **Import `SkyrimNetMultiProxy.zip`** with your mod manager (Vortex or MO2), same as any other mod,
   and enable it. `SkyrimNetMultiProxy.dll` and `SkyrimNetMultiProxy.ini` land in your game's
   `Data/SKSE/Plugins/` folder automatically.
4. **Edit `SkyrimNetMultiProxy.ini`** (in `Data/SKSE/Plugins/`, or open it from the mod's entry in
   Vortex or MO2) and set the paths `setup.bat` printed:
   - `PythonExe` — full path to your Python executable
   - `ProxyScript` — full path to `proxy.py` in your `SkyrimNet MultiProxy` folder
   - `WorkDir` — the `SkyrimNet MultiProxy` folder itself
   - `Port` — the port the proxy listens on (default `8000`)
5. **Launch Skyrim as normal.** The proxy starts itself.
6. **Set up your AI.** Open `http://127.0.0.1:8000`, pick a provider, and paste its key. For Claude,
   have the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and logged in. For
   ChatGPT, install the [Codex CLI](https://www.npmjs.com/package/@openai/codex) and run
   `codex login` once. For Ollama, just have it running.
7. **Point SkyrimNet at the proxy.** In SkyrimNet's settings, set the endpoint to
   `http://localhost:8000/v1/chat/completions`, leave the API key empty, and use a model name from the
   dashboard (each one has a **Copy** button).

> [!TIP]
> Set `PythonExe` to Python's full path rather than just `python`. Windows can quietly send a bare
> `python` command to its own Store-app stub instead of your real install.

> [!TIP]
> Want auto-close with Skyrim or a log file? Copy `proxy.ini.example` to `proxy.ini` in your
> `SkyrimNet MultiProxy` folder and turn on what you want. Skip it and everything else works the same.

---

## 🔄 Upgrading from Claude SkyrimNet Proxy Launcher

This used to be called **Claude SkyrimNet Proxy Launcher**, and the plugin was `ProxyLauncher.dll`.

1. **Remove the old `ProxyLauncher` mod** in Vortex or MO2, then import `SkyrimNetMultiProxy.zip`.
   Your old settings come along: if the plugin finds a `ProxyLauncher.ini` and no
   `SkyrimNetMultiProxy.ini`, it copies your settings over the first time it runs.
2. **Point the paths at the new folder.** Those copied settings still point at your old proxy
   folder. Update `ProxyScript` and `WorkDir` in `SkyrimNetMultiProxy.ini` to the new
   `SkyrimNet MultiProxy` folder, or you'll keep running the old proxy.
3. **Bring your saved keys.** Copy `config.json` from your old proxy folder into the new one, or paste
   your keys into the dashboard again.

---

## ⚠️ Important Notes

> [!WARNING]
> `ProxyScript` and `WorkDir` in `SkyrimNetMultiProxy.ini` must point at your `SkyrimNet MultiProxy`
> folder before you launch Skyrim. The plugin has nothing to start without them.

> [!CAUTION]
> **Using Claude through your subscription operates in a gray area of Anthropic's Terms of
> Service.** The proxy uses the Claude CLI's logged-in session to make direct API calls, rather than
> going through the standard Claude Code interface — a method that may not be explicitly authorized
> under Anthropic's [Terms of Service](https://www.anthropic.com/legal/consumer-terms) or
> [Acceptable Use Policy](https://www.anthropic.com/legal/aup). Read both in full before using
> Claude models. This access could be restricted or blocked at any time, and your account could
> potentially be affected. This software is provided as-is, with no guarantee of continued
> functionality — you take on this risk yourself by using it. The other providers don't use this
> method.

---

## ❓ Frequently Asked Questions

* **Q: Do I need SkyrimNet installed for this to do anything?**
  > **Yes.** This runs the proxy. The in-game conversations come from
  > [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin) itself.

---

* **Q: Do I need a Claude subscription?**
  > **No.** Any one provider is enough. Claude only matters if you pick a Claude model.

---

* **Q: Which provider should I pick?**
  > **Whatever you already have.** A Claude or ChatGPT subscription costs nothing extra. Ollama is
  > free and runs on your own PC, as long as it can handle the model. The key-based providers bill
  > through your own account with them.

---

* **Q: What if I already started the proxy myself before launching Skyrim?**
  > **It's left alone.** The plugin checks whether the port is already in use and skips starting a
  > second copy.

---

* **Q: Does this affect my save game?**
  > **No.** It only starts a background program — it never reads or writes save data.

---

* **Q: Will the proxy keep running after I close Skyrim?**
  > **By default, yes.** Turn on `AutoCloseWithSkyrim` in `proxy.ini` if you'd rather it shut down
  > with the game.

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
* **[rhinos0608/skyrimnet-codex-proxy](https://github.com/rhinos0608/skyrimnet-codex-proxy)** (MIT
  License) — the idea and code behind the OpenAI, Kilo Gateway, Ollama, and ChatGPT/Codex provider
  support, and the template the proxy's test suite was adapted from.
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
issue](https://github.com/awesmdiver/skyrimnet-multiproxy/issues) so nothing gets lost.
````

## What I want back

1. The complete revised README as one Markdown block, ready to paste. Keep the overall section
   order and the GitHub callout syntax (`> [!TIP]`, `> [!WARNING]`, `> [!CAUTION]`). Tighten where
   it helps; don't pad.
2. A short list of what you changed and why, plus anything in the draft you think is inaccurate or
   confusing.
