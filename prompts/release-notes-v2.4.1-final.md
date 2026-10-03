# v2.4.1 — Long conversations, fixed

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
