# v2.4.0 — Your Google Subscription, in Skyrim

## What's New

**Already paying for Google AI Pro? Now Skyrim can use it.**

Google AI Pro and Ultra subscriptions now work the same way your Claude and ChatGPT subscriptions already do: signed in, no API key, and no per-token billing. That brings SkyrimNet MultiProxy to ten supported providers, including three subscription routes you might already be paying for.

* **Google AI Pro/Ultra support (no key needed):** Install [Google's Antigravity CLI](https://antigravity.google), sign in once with your Google account, and use the `geminicli/MODEL` prefix. You do not need to paste any API key into the dashboard.
* **Access non-Gemini models through your Google account:** Your Google subscription is not limited to Gemini. The dashboard automatically lists whichever models your signed-in account can run (including Claude and GPT-OSS models alongside Google's own), with a **Refresh** button for when Google updates the lineup.
* **Gemini API key support:** If you prefer pay-as-you-go, add a Google AI Studio key and use `gemini/MODEL` for direct API access. The dashboard lists all models available for your key so you don't have to guess exact model names.
* **Real streaming on the subscription route:** NPC dialogue streams in chunk by chunk as it generates, matching how SkyrimNet expects to display text, rather than waiting for the entire response to finish before sending.

## Good to Know

* **How Google subscription limits work:** Google does not use a fixed daily request count. Instead, your plan uses two rolling windows (a 5-hour limit and a weekly limit). Each request draws from these pools based on its cost, meaning shorter prompts and cheaper models will stretch your quota further. You can check your current usage at any time by opening your terminal, running `agy`, and typing `/usage`.
* **Subscription latency vs. API keys:** Because the subscription route invokes Google's CLI tool behind the scenes for each prompt, responses take a bit longer to start than direct API calls. If response speed is your main priority, use a Google AI Studio API key instead.
* **Plugin updates not required:** This update only modifies the proxy script. If you already have the SKSE plugin installed from the previous release, it will work as-is. Just replace your `proxy.py` with the new file.
* **Your configuration carries over:** You can safely keep or copy over your existing `config.json` file; the configuration format has not changed.

## Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!

# v2.3.0 — SkyrimNet MultiProxy: Your AI, Your Choice

![Four more ways to play — ChatGPT, OpenAI, Kilo Gateway and Ollama](assets/release-banner-v2-3-0.jpg)

## What's New

**A new name, because it's not just Claude anymore!**

Claude SkyrimNet Proxy Launcher is now **SkyrimNet MultiProxy**. Everything SkyrimNet uses AI for — NPC conversations and actions, diaries, bios, the GameMaster, and more — can now run on eight different AI providers, and you don't need a Claude subscription to use any of them.

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
