# Polish the v2.3.0 release notes and START HERE file for SkyrimNet MultiProxy

Give a second pass to two short documents I drafted for the v2.3.0 release.

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

## Document 1: RELEASE_NOTES.md (shown as the GitHub release description)

The previous release's notes used this same structure and tone (What's New, Improvements & Polish,
Good to Know, Special Thanks, Need Help?), so keep that shape.

````markdown
# v2.3.0 — SkyrimNet MultiProxy: Your AI, Your Choice

## What's New

**A new name, because it's not just Claude anymore!**

Claude SkyrimNet Proxy Launcher is now **SkyrimNet MultiProxy**. Your NPCs can get their dialogue
from eight different AIs, and you don't need a Claude subscription to use any of them.

* **Use the subscription you already have:** Claude still works through your Claude login, and now
  ChatGPT does too, through OpenAI's Codex CLI. No API keys needed for either.
* **More providers:** OpenAI, Kilo Gateway, and Ollama join OpenRouter, GLM, and Nano-GPT.
* **Free local models with Ollama:** Run models on your own PC. The dashboard lists the ones you've
  actually installed, with a **Refresh** button for when you pull a new one. Have an Ollama Cloud key?
  The dashboard shows the hosted models instead.
* **No Claude required:** The proxy starts fine without Claude set up. Claude only needs to work if
  you pick a Claude model.

**A Cleaner Dashboard**

The long scroll of key cards and model tables is gone. Open `http://127.0.0.1:8000`, pick a provider
from one dropdown, and see its key, its models (each with a **Copy** button), and whether it's ready.
Quick Test only offers providers that can answer right now, and if a test fails, it tells you why in
plain words.

## Improvements & Polish

* **Tidier download:** Unzipping now gives you one `SkyrimNet MultiProxy` folder instead of loose files.
* **Safer logs:** The proxy no longer writes your Claude login token into its log.
* **Clearer errors:** When a provider turns a request down, SkyrimNet gets a clean error message
  instead of garbled text.

## Good to Know

* **The plugin has a new name:** `ProxyLauncher.dll` is now `SkyrimNetMultiProxy.dll`. Remove the old
  ProxyLauncher mod before importing the new one.
* **Your settings come along:** The first time it runs, the new plugin copies your old
  `ProxyLauncher.ini` settings into `SkyrimNetMultiProxy.ini`. Update `ProxyScript` and `WorkDir` to
  point at the new `SkyrimNet MultiProxy` folder, and copy over your `config.json` to keep your saved
  keys.
* **ChatGPT replies take longer:** Each one runs through the Codex CLI, so expect a slower response
  than the other providers.
* **Using Claude?** The Terms of Service note in the README still applies to Claude models.

## Special Thanks

A huge thank you to **[rhinos0608](https://github.com/rhinos0608/skyrimnet-codex-proxy)**, whose
skyrimnet-codex-proxy provided the idea and code behind the OpenAI, Kilo Gateway, Ollama, and
ChatGPT/Codex support in this release!

## Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
````

## Document 2: START HERE.txt (plain text file inside the download, read in Notepad)

Plain text only: no Markdown, no bold, no emoji. Stay within ~90 characters per line. Use `--`
instead of em-dashes so it displays correctly everywhere.

````text
SkyrimNet MultiProxy
====================

This connects SkyrimNet's AI NPCs to the AI of your choice -- a Claude or ChatGPT
subscription, an API key, or free local models with Ollama -- and starts it
automatically whenever you launch Skyrim.

Before you start
-----------------
- SKSE64 installed
- At least one AI to talk to. You can set this up after the steps below.

Setup (one time only)
----------------------
1. Double-click setup.bat. It checks for Python (and installs it if it's missing),
   installs what the proxy needs, and prints the exact paths you'll need for step 3.

2. Import SkyrimNetMultiProxy.zip with your mod manager (Vortex or MO2), same as any
   other mod, and enable it. SkyrimNetMultiProxy.dll and SkyrimNetMultiProxy.ini land
   in your game's Data\SKSE\Plugins\ folder on their own.

3. Open SkyrimNetMultiProxy.ini and set the paths setup.bat printed for you. It's in
   Data\SKSE\Plugins\ in your game folder, or open it from the mod's entry in Vortex/MO2:
     PythonExe   -> the full path to your Python program
     ProxyScript -> the full path to proxy.py (the one in this folder)
     WorkDir     -> the full path to this folder

4. Launch Skyrim like you normally would. The proxy starts itself.

5. Open http://127.0.0.1:8000 in your browser, pick a provider, and set it up:
     Claude  -> have the Claude CLI installed and logged in
     ChatGPT -> install the Codex CLI and run "codex login" once
     Ollama  -> just have Ollama running
     Others  -> paste your API key and click Save

6. In SkyrimNet's settings, set the endpoint to
   http://localhost:8000/v1/chat/completions, leave the API key empty, and use a
   model name from the dashboard (each one has a Copy button).

Upgrading from Claude SkyrimNet Proxy Launcher (ProxyLauncher)?
-----------------------------------------------------------------
- Remove the old ProxyLauncher mod, then import SkyrimNetMultiProxy.zip.
- Your old ProxyLauncher.ini settings get copied over the first time the plugin runs,
  but they still point at your old folder. Change ProxyScript and WorkDir to this folder.
- Copy config.json from your old folder into this one to keep your saved keys, or
  paste them into the dashboard again.

Want auto-close or a log file?
-------------------------------
Optional. Copy proxy.ini.example to proxy.ini in this folder and turn on what you want.
Skip it and everything else works the same.

Something not working?
-----------------------
Full setup help, frequently asked questions, and troubleshooting tips are on the project
page: https://github.com/awesmdiver/skyrimnet-multiproxy

If you use Claude models, please read the Terms of Service note in that README first --
it's important.
````

## What I want back

1. The revised RELEASE_NOTES.md as one Markdown block.
2. The revised START HERE.txt as one plain-text block.
3. A short list of what you changed in each and why, plus anything you think is inaccurate or
   confusing.
