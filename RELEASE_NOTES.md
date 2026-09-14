# v2.3.0 — SkyrimNet MultiProxy: Your AI, Your Choice

## What's New

**A new name, because it's not just Claude anymore!**

Claude SkyrimNet Proxy Launcher is now **SkyrimNet MultiProxy**. Your NPCs can now get their dialogue from eight different AI providers, and you don't need a Claude subscription to run any of them.

* **Use the subscription you already have:** Claude still works through your Claude CLI login, and ChatGPT is now supported through OpenAI's Codex CLI. No API keys required for either.
* **More providers:** OpenAI, Kilo Gateway, and Ollama join OpenRouter, GLM (z.ai), and Nano-GPT.
* **Free local models with Ollama:** Run AI models locally on your own PC. The dashboard lists the models you have installed, with a **Refresh** button when you pull a new one. If you add an Ollama Cloud key, the dashboard lists Ollama's hosted models instead.
* **No Claude required:** The proxy starts and runs without Claude installed. Claude is only needed if you choose a Claude model.

**A Cleaner Dashboard**

No more endless scrolling through key cards and model tables. Open `http://127.0.0.1:8000` to pick a provider from a single dropdown, view its status, paste your key, and copy model names with one click. The Quick Test feature only enables providers ready to respond, and explains any failures in plain English.

## Improvements & Polish

* **Clean zip structure:** The release archive now unzips into a single `SkyrimNet MultiProxy` folder instead of scattering loose files.
* **Safer logging:** The proxy no longer prints your Claude session token to the log file.
* **Clearer error handling:** If an AI provider rejects a prompt, SkyrimNet receives a clean, readable error instead of garbled text.

## Good to Know

* **Plugin renamed:** `ProxyLauncher.dll` is now `SkyrimNetMultiProxy.dll`. Remove the old ProxyLauncher mod from your mod manager before installing the new version.
* **Upgrading your settings:** When it first runs, the new plugin automatically copies your old `ProxyLauncher.ini` values into `SkyrimNetMultiProxy.ini`. Update `ProxyScript` and `WorkDir` so they point to your new `SkyrimNet MultiProxy` folder. Copy your existing `config.json` into the new folder to bring over your saved API keys.
* **ChatGPT response time:** Because ChatGPT runs through the Codex CLI, replies will take longer to generate than with direct API providers.
* **Using Claude?** The Terms of Service notice in the README still applies if you use Claude subscription models.

## Special Thanks

A huge thank you to **[rhinos0608](https://github.com/rhinos0608/skyrimnet-codex-proxy)**! Their skyrimnet-codex-proxy project provided the core idea and code for our OpenAI, Kilo Gateway, Ollama, and ChatGPT/Codex integration.

## Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
