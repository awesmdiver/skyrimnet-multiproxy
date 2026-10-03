# v2.4.1 — Long conversations, fixed

## ✨ What's New

**A fix for anyone using Google Antigravity with SkyrimNet.**

Antigravity worked fine in testing and then failed for real players, because SkyrimNet's prompts are far bigger than a test prompt. Once a conversation carried enough context — a character's bio, their memories of you, what just happened — the reply stopped mid-flight, SkyrimNet showed a transfer error, and the NPC simply went quiet. That's fixed, and a real player has confirmed it in game.

* **Google Antigravity handles full-size conversations now.** The prompt is handed over a different way, one with no length limit, so a long memory or a detailed character sheet no longer cuts the reply off. Tested at 150,000 characters — far more than SkyrimNet sends.
* **A failed reply explains itself instead of vanishing.** Before, an unexpected error part-way through a response could leave you with a half-finished sentence and no reason for it. Every provider now ends the reply with something readable — Claude, ChatGPT, Antigravity, Gemini, OpenRouter, GLM, Nano-GPT, OpenAI, Kilo Gateway and Ollama.
* **ChatGPT offers every model your account can run.** The dashboard used to list a single hard-coded model. It now reads the real list from your own Codex login, with a **Refresh** button for when OpenAI changes the lineup.
* **The plugin reports its real version.** `SkyrimNetMultiProxy.dll` had been telling SKSE it was version 2.0.0.0 since the very first release, which made crash logs misleading. It now reports the version you actually installed.
* **The settings template lists every key.** `config.example.json` was missing its `gemini_api_key` line, so anyone setting up the Gemini route from the template had nowhere obvious to put the key.

## 📋 Good to Know

* **Upgrading from v2.4.0:** replace `proxy.py` with the new one and reinstall the plugin to pick up the version fix. Your `config.json` and your settings carry over untouched.
* **Nothing to redo in SkyrimNet.** The endpoint, the blank API key, and your model names all stay exactly as they are.
* **Using a ChatGPT model already?** It still works. The new list adds models, it doesn't replace the one you have.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
