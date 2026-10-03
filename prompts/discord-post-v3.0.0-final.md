**SkyrimNet MultiProxy v3.0.0** — A speedometer for your AI

MultiProxy connects SkyrimNet to the AI of your choice — dialogue, actions, diaries, bios, GameMaster — and launches automatically whenever you start the game.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
- Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>

**🆕 New in v3.0.0**
- **Speeds tab:** Live response timing for every connected model, automatically tagged with its SkyrimNet task (Dialogue, Diary, Vision, GameMaster). Context-aware verdicts know when speed actually matters (instant dialogue vs. background diaries).
- **Persistent history:** Performance tracking now survives restarts — view trends by Today, 7 days, 30 days, or All time.
- **Head-to-head comparisons:** A ranked leaderboard showing how different models stack up at the same task, fastest first.
- **Vision (OmniSight) support:** SkyrimNet screenshot analysis was previously getting blocked before hitting the AI. Fixed and fully working across all providers.

**🐛 Fixed in v3.0.0**
- **Silent launch failure on Windows:** Windows has a default `python` shortcut that opens the Microsoft Store instead of running scripts, yet falsely reports success. MultiProxy now defaults to `py` to prevent this. (If you installed Python via the MS Store app, stick to `python`).
- **Instant login detection:** Logging into Codex or Google Antigravity while the proxy is running no longer leaves them stuck on "Not installed" until a restart.
- **Cleaner console:** Suppressed routine request spam — now only surfaces real errors with useful detail.

**✨ Features**
- **11 supported AI options:** Claude, ChatGPT/Codex, and Google AI Pro (via web login, no keys needed), plus OpenRouter, GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, Kilo Gateway, and local Ollama — mix and match per SkyrimNet feature.
- **Speeds dashboard:** Real-time timing, task tracking, and benchmark history.
- **Flexible Codex routing:** Use your ChatGPT subscription, or route through OpenAI, Ollama/LM Studio, or custom endpoints.
- **Local AI ready:** Automatically detects models installed in Ollama.
- **Automated workflow:** Starts with Skyrim in the background (optional auto-close & auto-dashboard).
- **Web dashboard & secure logs:** Manage keys and test connections easily; API keys and tokens are never logged.

**🛠️ Setup**
1. Extract the release anywhere and run `setup.bat` (installs Python packages, displays your paths).
2. Install `SkyrimNetMultiProxy.zip` in Vortex/MO2, then paste those paths into `SkyrimNetMultiProxy.ini`.
3. Launch Skyrim, open `http://127.0.0.1:8000`, pick a provider, and log in or add your key.
4. In SkyrimNet: set endpoint to `http://localhost:8000/v1/chat/completions`, leave API key blank, and select your model from the dashboard.

*Upgrading from v2.5.0?* Two files this time: replace `proxy.py`, and reinstall `SkyrimNetMultiProxy.zip` in Vortex/MO2 to get the new plugin `.dll` too. Your `config.json` and SkyrimNet settings remain untouched either way.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet, and SKSE64
- An AI provider: a Claude/ChatGPT/Google login, an API key, or local Ollama

**🔀 Overlap:** Built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** Using a Claude web subscription via proxy is a gray area under Anthropic's ToS — use at your own discretion. Other providers are unaffected.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT)
