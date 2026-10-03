**SkyrimNet MultiProxy v2.4.1** — bug fixes for the Google routes

MultiProxy connects SkyrimNet to the AI of your choice — NPC dialogue, actions, diaries, bios, GameMaster — and starts itself whenever you launch the game.

**🔗 Guide + download:** <https://github.com/awesmdiver/skyrimnet-multiproxy>

**🐛 Fixed in v2.4.1**
- **Google Antigravity went quiet in long conversations.** Once an NPC had enough to remember, the reply got cut off and they said nothing. Fixed, and confirmed in game.
- **A failed reply now tells you what happened** instead of leaving a half-finished sentence hanging. Every AI, not just the Google ones.
- **ChatGPT shows all your models** now, not just one — the dashboard reads your real account.

**🆕 Still new in v2.4.0**
- **Got a Google AI Pro or Ultra account? Skyrim can use it.** Install Google Antigravity, sign in once, and you're done — no API key, just like Claude and ChatGPT already work here.
- **More than just Gemini.** The dashboard lists whatever models your login can run, Claude and GPT-OSS included.
- **No CLI? Use a Gemini key instead.** A Google AI Studio key works with the same account and answers faster.

**✨ 10 AIs to choose from:** Claude, ChatGPT and Google AI Pro (subscriptions, no keys), plus OpenRouter, GLM, Nano-GPT, OpenAI, Gemini, Kilo Gateway, and free local models with Ollama.

*Already on v2.4.0?* Swap in the new `proxy.py` and reinstall the plugin — your settings carry over. New here? The README walks you through setup.

**⚙️ You'll need:** Skyrim SE, SkyrimNet, SKSE64, and one AI to talk to — a login, an API key, or Ollama.

**🔀 Overlap:** built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** using a Claude subscription this way is a gray area under Anthropic's ToS. Your risk. Other providers don't use this method.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT)
