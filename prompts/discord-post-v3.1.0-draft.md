**SkyrimNet MultiProxy v3.1.0** — Ready for Beta 25

MultiProxy connects SkyrimNet to the AI of your choice — dialogue, actions, diaries, bios, GameMaster — and launches automatically whenever you start the game.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
- Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>

**🆕 New in v3.1.0**
- **Ready for SkyrimNet Beta 25:** Beta 25 switches to a backup AI when one stalls or fails. MultiProxy now reports real failures so that switch actually happens, instead of an NPC reading an error out loud. Dropped race requests get cleaned up without counting against your Speeds tab.
- **Voice cues left alone:** Beta 25 wants different voice tags per voice engine, so they now pass through exactly as the AI wrote them. Higgs users can turn on `FixHiggsVoiceTags` to have them tidied.
- **Tougher under pressure:** Dropped connections retry on their own, too-long chats get trimmed and retried instead of going silent, and garbled markup tags get fixed before SkyrimNet sees them.
- **Waiting lines:** Cap how many requests each provider handles at once (`[Concurrency]` in `proxy.ini`) so busy services don't trip rate limits. GLM starts at 6.
- **Full reply log:** Turn on `LogFullReplies` to see every AI reply in the console.

**🐛 Fixed in v3.1.0**
- **Mod Organizer 2:** The Speeds tab now finds SkyrimNet's files under MO2 setups.
- **Faster DeepSeek:** DeepSeek skips its hidden "thinking" step by default, same as GLM.

**✨ Features**
- **11 supported AI options:** Claude, ChatGPT/Codex, and Google AI Pro (with your existing subscription login, no keys needed), plus OpenRouter, GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, Kilo Gateway, and local Ollama — mix and match per SkyrimNet feature.
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

*Upgrading from v3.0.0?* Replace `proxy.py`, and reinstall `SkyrimNetMultiProxy.zip` in Vortex/MO2 to get the new plugin `.dll` too. Your `config.json` and SkyrimNet settings remain untouched.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet (Beta 24 or 25), and SKSE64
- An AI provider: a Claude/ChatGPT/Google login, an API key, or local Ollama

**🔀 Overlap:** Built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** Using a Claude web subscription via proxy is a gray area under Anthropic's ToS — use at your own discretion. Other providers are unaffected.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT), Raydonn (tag fixer, retries, waiting lines)
