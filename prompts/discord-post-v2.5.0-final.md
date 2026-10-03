**SkyrimNet MultiProxy v2.5.0** — Codex goes beyond ChatGPT

MultiProxy connects SkyrimNet to the AI of your choice — NPC dialogue, actions, diaries, bios, GameMaster — and starts automatically whenever you launch the game.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
- Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>

**🆕 New in v2.5.0**
- **Codex isn't locked to your ChatGPT subscription anymore.** The dashboard's Codex card now lets you add other AI services: built-in presets (OpenAI API, local Ollama, local LM Studio) or any custom OpenAI-compatible endpoint (OpenRouter, self-hosted gateways, etc.). A plain `codex/MODEL` still routes to your ChatGPT subscription; an added provider gets its own `codex/<provider-id>/MODEL` route. Your real Codex CLI login and config are never touched — this is a completely separate, proxy-owned list.
- **Pin a model that isn't listed.** Know a working model ID that isn't shown — newer, older, or from another provider entirely? Pin it directly or search the account's full catalog right from the Codex card's **Pin a model** tab.
- **Quick Test, completely rebuilt.** Pick from six named scenarios (follower, merchant, tense scene, and more) to load a realistic prompt in one click, or write your own. Hit Send and a real popup opens with a live, multi-turn conversation — keep chatting back and forth instead of getting boxed into a single reply.
- **DeepSeek joins the lineup.** Add your DeepSeek API key in the dashboard and use `deepseek/MODEL` (e.g. `deepseek/deepseek-flash`) to route through their official pay-as-you-go API.

**🐛 Fixed in v2.5.0**
- **Codex & Antigravity 10+ minute hang.** If the app behind either wasn't running, requests could hang in total silence far past the timeout. They now fail cleanly and tell you plainly what happened.
- **Gemini's deprecated 2.5 generation** has been removed from the model list after Google discontinued it for new use.

**✨ Features**
- **11 AIs to choose from:** Claude, ChatGPT/Codex, and Google AI Pro (subscriptions, no keys), plus OpenRouter, GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, Kilo Gateway, and free local models with Ollama
  - Mix and match: a different AI for different parts of SkyrimNet if you want
- **Codex, your way:** stick with your ChatGPT subscription, or point Codex at OpenAI, local Ollama/LM Studio, or your own OpenAI-compatible endpoint
- **Local AI:** Ollama lists the models you've installed
- **Starts with Skyrim** in the background (optional auto-close)
- **Web dashboard:** manage keys, test connections, copy model names
- **Safe logs:** keys and tokens never hit the log

**🛠️ Setup**
1. Extract the zip anywhere and run `setup.bat` (installs Python + packages, prints your paths)
2. Install `SkyrimNetMultiProxy.zip` in Vortex/MO2, paste those paths into `SkyrimNetMultiProxy.ini`
3. Launch Skyrim, open `http://127.0.0.1:8000`, pick a provider and add your key or login
4. In SkyrimNet: endpoint `http://localhost:8000/v1/chat/completions`, API key blank, model from the dashboard

*Already on v2.4.1?* Just swap in the new `proxy.py` — that's every change above. Your `config.json` and SkyrimNet settings carry over untouched.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet, SKSE64
- One AI to talk to: a Claude, ChatGPT or Google login, an API key, or Ollama running locally

**🔀 Overlap:** built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** using a Claude subscription this way is a gray area under Anthropic's ToS. Your risk. Other providers don't use this method.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT)
