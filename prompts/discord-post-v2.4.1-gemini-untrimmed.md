**SkyrimNet MultiProxy v2.4.1** — bug fixes for Google routes

MultiProxy connects SkyrimNet to the AI of your choice — NPC dialogue, actions, diaries, bios, GameMaster — and starts automatically whenever you launch the game.

**🔗 Guide + download:** <https://github.com/awesmdiver/skyrimnet-multiproxy>

**🐛 Fixed in v2.4.1**
- **Google Antigravity went quiet in long conversations.** Once an NPC had a lot to remember, replies got cut off mid-sentence and they went silent. Fixed, and confirmed working in game.
- **Clearer error messages.** If an AI fails mid-reply, it now tells you what went wrong instead of leaving a half-finished sentence hanging. This applies to every AI, not just Google.
- **ChatGPT lists all your models.** The dashboard now pulls and displays every model your account can actually run, instead of just one.

**🆕 Still new in v2.4.0**
- **Google AI Pro/Ultra support.** Sign in once via Google Antigravity and you're set — no API key needed, just like Claude and ChatGPT.
- **More than Gemini.** Access Claude, GPT-OSS, and other models available under your Google login.
- **API keys work too.** Prefer using a Google AI Studio key? Fully supported, and responds even faster.

**✨ 10 AIs to choose from:** Claude, ChatGPT, and Google AI Pro (subscriptions, no keys), plus OpenRouter, GLM, Nano-GPT, OpenAI, Gemini, Kilo Gateway, and free local models with Ollama.

*Already on v2.4.0?* Swap in the new `proxy.py` and reinstall the plugin — your settings carry over. New here? The README walks you through setup.

**⚙️ You'll need:** Skyrim SE, SkyrimNet, SKSE64, and one AI to talk to — a login, an API key, or Ollama.

**🔀 Overlap:** built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** using a Claude subscription this way is a gray area under Anthropic's ToS. Your risk. Other providers don't use this method.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT)
