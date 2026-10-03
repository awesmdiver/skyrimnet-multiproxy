**SkyrimNet MultiProxy v3.1.0** — Ready for Beta 25

MultiProxy connects SkyrimNet to whatever AI you prefer to run — dialogue, actions, diaries, bios, GameMaster — and boots up automatically whenever you launch the game.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
- Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>

**🆕 New in v3.1.0**
- **Ready for SkyrimNet Beta 25:** Beta 25 can swap to a backup AI if your primary stalls, or race a second request against a slow one. MultiProxy reports real failures immediately so handoffs trigger cleanly instead of an NPC reading error code out loud! Canceled race requests are cleared away without cluttering your Speeds tab.
- **Voice cues left alone:** Beta 25 uses distinct tags per voice engine, so cues pass through untouched by default. If you use Higgs, toggle `FixHiggsVoiceTags` to keep them clean.
- **Tougher under pressure:** Courtesy of community member Raydonn, dropped connections retry automatically, long chats trim and retry instead of going silent, and scrambled markup tags get repaired before SkyrimNet sees them.
- **Waiting lines:** Also from Raydonn, cap simultaneous requests per provider (`[Concurrency]` in `proxy.ini`) to dodge rate limits. GLM defaults to 6.
- **Full reply log:** Turn on `LogFullReplies` to print complete AI replies directly in your console.
- **Smarter Speeds tab:** Live health cards for each AI, tracking how fast replies start talking, overall speeds, and plain reliability labels, with Dialogue front and center.

**🐛 Fixed in v3.1.0**
- **Mod Organizer 2:** The Speeds tab reliably finds SkyrimNet folders on MO2 setups.
- **Faster DeepSeek:** Skips the hidden "thinking" step by default, matching GLM.
- **Privacy:** The Speeds tab no longer enables SkyrimNet stats sharing; manage that directly in SkyrimNet's Privacy settings.

**✨ Features**
- **11 supported AI options:** Claude, ChatGPT/Codex, and Google AI Pro (with your subscription login, no API keys needed), plus OpenRouter, GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, Kilo Gateway, and local Ollama — mix and match per SkyrimNet feature.
- **Speeds dashboard:** Real-time response timing, live task tracking, and benchmark history.
- **Flexible Codex routing:** Use your ChatGPT sub, OpenAI, Ollama/LM Studio, or custom endpoints.
- **Local AI ready:** Automatically detects models installed in Ollama.
- **Zero-hassle workflow:** Starts with Skyrim in the background (optional auto-close & auto-dashboard).
- **Web dashboard & secure logs:** Manage keys and test connections easily; your API keys and tokens are never logged.

**🛠️ Setup**
1. Extract the release anywhere and run `setup.bat` (installs Python packages and shows your paths).
2. Install `SkyrimNetMultiProxy.zip` in Vortex or MO2, then paste those paths into `SkyrimNetMultiProxy.ini`.
3. Launch Skyrim, open `http://127.0.0.1:8000`, pick your provider, and log in or add your key.
4. In SkyrimNet: set your endpoint to `http://localhost:8000/v1/chat/completions`, leave the API key blank, and pick your model from the dashboard.

*Upgrading?* Replace `proxy.py` AND reinstall `SkyrimNetMultiProxy.zip` in Vortex/MO2 for the new plugin `.dll`. Your `config.json` and SkyrimNet settings stay untouched.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet (Beta 24 or 25), and SKSE64
- An AI provider: a Claude/ChatGPT/Google login, an API key, or local Ollama

**🔀 Overlap:** Built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** Using a Claude web subscription via proxy is a gray area under Anthropic's ToS — use at your own discretion. Other providers are unaffected.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT), and Raydonn (stability upgrades).
