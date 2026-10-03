**SkyrimNet MultiProxy v2.4.1** — bug fixes for Google routes

MultiProxy connects SkyrimNet to the AI of your choice — NPC dialogue, actions, diaries, bios, GameMaster — and starts automatically whenever you launch the game.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
- Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>

**🐛 Fixed in v2.4.1**
- **Google Antigravity went quiet in long conversations.** Once an NPC had a lot to remember, replies got cut off mid-sentence and they went silent. Fixed, and confirmed working in game by the player who reported it.
- **Clearer error messages.** If an AI fails mid-reply, it now tells you what went wrong instead of leaving a half-finished sentence hanging. This applies to every AI, not just Google.
- **ChatGPT lists all your models.** The dashboard now pulls and displays every model your account can actually run, instead of just one, with a **Refresh** button for when OpenAI updates the lineup.
- **Crash logs name the right version.** The SKSE plugin had been reporting itself as 2.0.0.0 since the very first release. It now reports the version you actually installed.

**🆕 New in v2.4.0**
- **Google AI Pro/Ultra support.** Sign in once via Google Antigravity and you're set — no API key needed, just like Claude and ChatGPT.
- **More than Gemini.** Access Claude, GPT-OSS, and other models available under your Google login.
- **API keys work too.** Prefer using a Google AI Studio key? Fully supported, and responds even faster.

**✨ Features**
- **10 AIs to choose from:** Claude, ChatGPT and Google AI Pro (subscriptions, no keys), plus OpenRouter, GLM, Nano-GPT, OpenAI, Gemini, Kilo Gateway, and free local models with Ollama
  - Mix and match: a different AI for different parts of SkyrimNet if you want
- **Local AI:** Ollama lists the models you've installed
- **Starts with Skyrim** in the background (optional auto-close)
- **Web dashboard:** manage keys, test connections, copy model names
- **Safe logs:** keys and tokens never hit the log

**🛠️ Setup**
1. Extract the zip anywhere and run `setup.bat` (installs Python + packages, prints your paths)
2. Install `SkyrimNetMultiProxy.zip` in Vortex/MO2, paste those paths into `SkyrimNetMultiProxy.ini`
3. Launch Skyrim, open `http://127.0.0.1:8000`, pick a provider and add your key or login
4. In SkyrimNet: endpoint `http://localhost:8000/v1/chat/completions`, API key blank, model from the dashboard

*Already on v2.4.0?* Swap in the new `proxy.py` — that's every fix above. Then install the new `SkyrimNetMultiProxy.zip` over your old plugin mod in Vortex/MO2 so the `.dll` gets replaced as well; that part only fixes the version shown in crash logs. Your `config.json` and SkyrimNet settings carry over untouched.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet, SKSE64
- One AI to talk to: a Claude, ChatGPT or Google login, an API key, or Ollama running locally

**🔀 Overlap:** built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** using a Claude subscription this way is a gray area under Anthropic's ToS. Your risk. Other providers don't use this method.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT)
