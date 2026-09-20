# v3.0.0 — A speedometer for your AI

![A speedometer for your AI — the new Speeds tab](assets/release-banner-v3-0-0.jpg)

## ✨ What's New

**You can finally see how fast each of your connected AIs is actually answering — and what SkyrimNet is using them for.**

Until now, the only way to tell if your Dialogue model was running slow was if it felt slow in-game. The new **Speeds tab** gives you real numbers: live response times for every connected AI, labeled by the specific job SkyrimNet assigned to it, with built-in verdicts so you know at a glance whether a delay is normal or needs tuning.

### The Speeds tab
* **Live response times for every AI.** Right next to the Setup tab, see exactly how long replies take. Every request is timed automatically with zero configuration.
* **Labeled by job, not just model name.** Models are tagged with what SkyrimNet actually uses them for — Dialogue, Diary, Vision, Game Master, Memory, and more — pulled directly from your SkyrimNet settings so you never have to guess.
* **Smart verdicts that know context.** A 3-second reply is sluggish for live Dialogue, but perfectly fine for a background Diary entry. You can even customize the speed thresholds for each type of job right from the dashboard.
* **History that persists across restarts.** Response times are saved across sessions. Filter by Today, 7 days, 30 days, or All time to spot long-term performance trends.
* **Head-to-head model comparisons.** Switch your Dialogue model next week, and you can still compare it to your old one. You get a ranked leaderboard for every AI that has handled a specific job — fastest first — with an active badge on your current choice.
* **Optional auto-open dashboard.** A new setting in `proxy.ini` can automatically launch the dashboard in your browser when the proxy starts.

### Vision, actually working now
* **Vision requests no longer fail.** SkyrimNet's screenshot feature (OmniSight) sends image data alongside text, which the proxy was previously rejecting before it ever reached your model. That's now fixed — Vision works properly through OpenRouter and every other supported service.

## 🔧 Improvements & Polish

* **Fixed silent startup failures on Windows.** On some PCs, the standard `python` command triggers a Microsoft Store shortcut instead of running the program, falsely reporting success while the proxy never actually starts. Default settings now point to `py` (installed by python.org). *(If you intentionally installed Python from the Microsoft Store, stick with `python`, as that version doesn't include `py`.)*
* **A general safety net for any other silent startup failure.** The fix above catches one specific cause; this covers every other one too. If the proxy process starts but never actually finishes coming up, you'll now see a clear message about it in `SkyrimNetMultiProxy.log` — instead of a false "launched successfully" with nothing else to go on. A normal, working launch is completely unaffected — this only ever speaks up when something's actually wrong.
* **Instant status updates for Codex and Google Antigravity.** Logging in or installing these while the proxy was running previously left them stuck showing "Not installed" until a full proxy restart. Both now update their status live.
* **A much quieter, cleaner console window.** Routine background check-ins no longer flood the console. Only actual errors are printed, now including full request and response details instead of a single vague error code.
* **Copy button for the Endpoint field**, matching the buttons already on every model row.
* **Better crash logging.** If the proxy ever closes unexpectedly while Skyrim is still running, it now writes the crash details to `proxy.log` before closing, instead of disappearing without a trace.

## 📋 Good to Know

* **How to update:** replace your old `proxy.py` with the new one. Your `config.json` and saved API keys/logins are safe and untouched. If your proxy has previously failed to launch quietly, check the `PythonExe` line in `SkyrimNetMultiProxy.ini` against the fix above — just make sure not to change it to `py` if you installed Python via the Microsoft Store.
* **The SKSE plugin (`.dll`) has a small update this release**, for the safety-net fix above — everything else about it is unchanged from v2.5.0.
* **We're still investigating a separate, rare crash.** In some cases, the proxy starts successfully but silently stops a short time later while Skyrim is still running. This is separate from the `python`/`py` launch issue above (which is confirmed fixed). The new crash logging is our first step toward tracking this down — if you run into this, sharing your `proxy.log` will help us fix it.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!

---

# v2.5.0 — Codex goes beyond ChatGPT

![Codex goes beyond ChatGPT — a new stone rises on the hill](assets/release-banner-v2-5-0.jpg)

## ✨ What's New

**Codex isn't tied down to just your personal ChatGPT subscription anymore—and testing any provider just got a massive quality-of-life upgrade.**

Until now, entering `codex/MODEL` in SkyrimNet always routed through your own ChatGPT login. That was great for an easy, no-fuss setup, but pretty limiting if you wanted Codex's snappy speed with other models. Starting in v2.5.0, your Codex setup acts as a launchpad for other AI services, too—letting you bounce between local models, official developer keys, and multi-model hubs without giving up the Codex workflow. Right alongside that, Quick Test has been rebuilt from scratch: instead of filling out a dry form to see if things work, trying out a provider now feels like actually chatting with an NPC.

### Codex, multi-provider
* **Codex can now talk to other AI providers.** Head over to the Codex card in the dashboard to add and manage extra services. We've included handy presets for the official OpenAI API, local Ollama setups, and local LM Studio rigs, plus plenty of room for any custom OpenAI-compatible endpoint you like (such as OpenRouter or a private gateway). Give each setup a nickname, then route right to it in SkyrimNet using `codex/<provider-id>/MODEL` (for example, `codex/sn-my-openrouter/anthropic/claude-sonnet-4.5`).
* **Your default ChatGPT setup stays right where it is.** Typing a plain `codex/MODEL` without a provider ID still connects straight to your personal subscription, just like it always has. MultiProxy handles this list of extra providers entirely on its own side, so your actual Codex CLI login and config files stay completely untouched. One quick tip: custom endpoints just need to support the newer "Responses API" format expected by Codex's CLI rather than the older "Chat Completions" style (most modern services, OpenRouter included, support this automatically).
* **Pin a model that isn't in the list.** Found a great model ID that isn't in the dropdown—whether it's brand new, a trusty older version, or hosted on another provider entirely? You can pin it directly, or search through your account's full model catalog to find it, right inside the Codex card's **Pin a model** tab.

### Quick Test, reworked
* **Named scenarios, ready to go.** Pick from six ready-made scenarios—like a loyal follower wandering the roads, a sly merchant hawking wares, or a tense standoff—and Quick Test fills in a fitting system prompt and starter line in one click. Prefer full control over the prompt? You can still write your own from scratch anytime.
* **A real conversation, not a single reply.** When you click Send, a proper popup window opens with an ongoing back-and-forth chat. You can keep talking to the same "NPC" across as many turns as you want to see how the model holds up over time, instead of being limited to a single one-off reply.

### DeepSeek
* **DeepSeek joins the lineup.** DeepSeek now gets its own dedicated card in the dashboard. Just paste in your API key, then use `deepseek/MODEL` (such as `deepseek/deepseek-flash`) in SkyrimNet to route directly through their official pay-as-you-go service.

## 🔧 Improvements & Polish

* **Fixed: a Codex or Antigravity request could hang for 10+ minutes with zero response.** If the background helper app behind Codex or Antigravity wasn't running, requests could hang indefinitely in total silence instead of throwing a clean error. It now times out properly and lets you know exactly what happened.
* **Removed Gemini's 2.5 generation from the model list.** Google has discontinued this tier for new setups, so we've cleared it out of the dropdown to avoid any dead ends.

## 📋 Good to Know

* **Upgrading from v2.4.1:** Simply drop the new `proxy.py` file in place of the old one. Your existing `config.json` stays completely safe and untouched, keeping all your saved keys and Codex logins ready to go.
* **Nothing to redo in SkyrimNet:** Your local endpoint URL, blank API key, and existing model names in SkyrimNet remain exactly the same. You only need to change settings in SkyrimNet if you're trying out a new DeepSeek model or testing a new Codex provider route.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!

# v2.4.1 — Long conversations, fixed

![Your Google subscription, in Skyrim — Google Antigravity and Gemini](assets/release-banner-v2-4-0.jpg)

## ✨ What's New

**A fix for anyone using Google Antigravity with SkyrimNet.**

Antigravity worked fine in testing and then failed for real players, because SkyrimNet's prompts are far bigger than a test prompt. Once a conversation carried enough context — a character's biography, their memories of you, recent events — the reply stopped mid-sentence, SkyrimNet threw a transfer error, and the NPC went quiet. That is fixed, and confirmed working in game by the player who reported it.

* **Google Antigravity handles full-size conversations now.** The prompt is handed over a different way, one with no length limit, so a long memory or a detailed character sheet won't cut the reply off. Tested up to 150,000 characters — far more than SkyrimNet ever sends.
* **A failed reply explains itself instead of vanishing.** Previously, an unexpected error partway through a response could leave you with a half-finished sentence and no reason why. Every provider now ends the reply with a readable error message — Claude, ChatGPT, Antigravity, Gemini, OpenRouter, GLM, Nano-GPT, OpenAI, Kilo Gateway, and Ollama.
* **ChatGPT offers every model your account can run.** The dashboard used to list a single hard-coded model. It now fetches the actual list from your Codex login, with a **Refresh** button for when OpenAI updates the lineup.
* **The plugin reports its real version.** `SkyrimNetMultiProxy.dll` had been telling SKSE it was version 2.0.0.0 since the first release, making crash logs misleading. It now reports the real version you installed.

## 📋 Good to Know

* **Upgrading from v2.4.0:** Two files to swap. Replace `proxy.py` with the new one — that's every fix above. Then install the new `SkyrimNetMultiProxy.zip` in Vortex or MO2 over the old plugin mod, so `SkyrimNetMultiProxy.dll` gets replaced too; that one only affects the version your crash logs report, so skip it if you'd rather. Your `config.json` carries over untouched either way.
* **Nothing to redo in SkyrimNet:** Your endpoint, blank API key, and model names stay exactly as they are.
* **Already using a ChatGPT model?** It will keep working. The new list adds the rest of your available models without replacing the one you're currently using.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!

# v2.4.0 — Your Google Subscription, in Skyrim

![Your Google subscription, in Skyrim — Google Antigravity and Gemini](assets/release-banner-v2-4-0.jpg)

## ✨ What's New

**Already paying for Google AI Pro? Now Skyrim can use it.**

Google AI Pro and Ultra subscriptions now work the same way your Claude and ChatGPT subscriptions already do: signed in, with no API key to manage. That brings SkyrimNet MultiProxy to ten supported providers, including three subscription routes you might already be paying for.

* **Google AI Pro/Ultra support (no key needed):** Install [Google's Antigravity CLI](https://antigravity.google), sign in once with your Google account, and use the `geminicli/MODEL` prefix. You do not need to paste any API key into the dashboard.
* **Access non-Gemini models through your Google account:** Your Google subscription is not limited to Gemini. The dashboard automatically lists whichever models your signed-in account can run (including Claude and GPT-OSS models alongside Google's own), with a **Refresh** button for when Google updates the lineup.
* **Gemini API key support:** Add a Google AI Studio key and use `gemini/MODEL` to go straight to Google's Gemini API — nothing to install, no CLI in the picture. The dashboard lists all models available for your key so you don't have to guess exact model names.
* **Real streaming on the subscription route:** NPC dialogue streams in chunk by chunk as it generates, matching how SkyrimNet expects to display text, rather than waiting for the entire response to finish before sending.

## 📋 Good to Know

* **Which of the two to pick:** It comes down to what you already have. The Gemini API key route needs nothing installed and answers faster, since it calls Google's API directly. The Antigravity route needs the Antigravity CLI installed and signed in, and each reply is a real CLI call, so it takes a little longer to start — the one to use if you already have Antigravity, or you'd rather not manage an API key at all.
* **Plugin updates not required:** This update only modifies the proxy script. If you already have the SKSE plugin installed from the previous release, it will work as-is. Just replace your `proxy.py` with the new file.
* **Your configuration carries over:** You can safely keep or copy over your existing `config.json` file; the configuration format has not changed.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!

# v2.3.0 — SkyrimNet MultiProxy: Your AI, Your Choice

![Four more ways to play — ChatGPT, OpenAI, Kilo Gateway and Ollama](assets/release-banner-v2-3-0.jpg)

## ✨ What's New

**A new name, because it's not just Claude anymore!**

Claude SkyrimNet Proxy Launcher is now **SkyrimNet MultiProxy**. Everything SkyrimNet uses AI for — NPC conversations and actions, diaries, bios, the GameMaster, and more — can now run on eight different AI providers, and you don't need a Claude subscription to use any of them.

* **Use the subscription you already have:** Claude still works through your Claude CLI login, and ChatGPT is now supported through OpenAI's Codex CLI. No API keys required for either.
* **More providers:** OpenAI, Kilo Gateway, and Ollama join OpenRouter, GLM (z.ai), and Nano-GPT.
* **Free local models with Ollama:** Run AI models locally on your own PC. The dashboard lists the models you have installed, with a **Refresh** button when you pull a new one. If you add an Ollama Cloud key, the dashboard lists Ollama's hosted models instead.
* **No Claude required:** The proxy starts and runs without Claude installed. Claude is only needed if you choose a Claude model.

**A Cleaner Dashboard**

No more endless scrolling through key cards and model tables. Open `http://127.0.0.1:8000` to pick a provider from a single dropdown, view its status, paste your key, and copy model names with one click. The Quick Test feature only enables providers ready to respond, and explains any failures in plain English.

## 🔧 Improvements & Polish

* **Clean zip structure:** The release archive now unzips into a single `SkyrimNet MultiProxy` folder instead of scattering loose files.
* **Safer logging:** The proxy no longer prints your Claude session token to the log file.
* **Clearer error handling:** If an AI provider rejects a prompt, SkyrimNet receives a clean, readable error instead of garbled text.

## 📋 Good to Know

* **Plugin renamed:** `ProxyLauncher.dll` is now `SkyrimNetMultiProxy.dll`. Remove the old ProxyLauncher mod from your mod manager before installing the new version.
* **Upgrading your settings:** When it first runs, the new plugin automatically copies your old `ProxyLauncher.ini` values into `SkyrimNetMultiProxy.ini`. Update `ProxyScript` and `WorkDir` so they point to your new `SkyrimNet MultiProxy` folder. Copy your existing `config.json` into the new folder to bring over your saved API keys.
* **ChatGPT response time:** Because ChatGPT runs through the Codex CLI, replies will take longer to generate than with direct API providers.
* **Using Claude?** The Terms of Service notice in the README still applies if you use Claude subscription models.

## 🤝 Special Thanks

A huge thank you to **[rhinos0608](https://github.com/rhinos0608/skyrimnet-codex-proxy)**! Their skyrimnet-codex-proxy project provided the core idea and code for our OpenAI, Kilo Gateway, Ollama, and ChatGPT/Codex integration.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
