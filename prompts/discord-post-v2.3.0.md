SkyrimNet MultiProxy v2.3.0 — it's not just Claude anymore!

Connect SkyrimNet to the AI of your choice. MultiProxy powers everything SkyrimNet uses AI for (NPC dialogue, actions, diaries, bios, GameMaster), and its SKSE plugin starts it whenever you play.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>

**📌 Status:** Released (v2.3.0)

**✨ Features**
- **8 AI providers:** Claude and ChatGPT (subscriptions, no API keys), OpenRouter, GLM (z.ai), Nano-GPT, OpenAI, Kilo Gateway, Ollama
  - Mix and match: different models for different SkyrimNet tasks
- **Local AI:** Ollama lists the models you've installed
- **Starts with Skyrim** in the background (optional auto-close)
- **Web dashboard:** manage keys, test connections, copy model names
- **Safe logs:** keys and tokens never hit the log

**🛠️ Setup**
1. Extract the zip anywhere and run `setup.bat` (installs Python + packages, prints your paths)
2. Install `SkyrimNetMultiProxy.zip` in Vortex/MO2, paste those paths into `SkyrimNetMultiProxy.ini`
3. Launch Skyrim, open `http://127.0.0.1:8000`, pick a provider and add your key or login
4. In SkyrimNet: endpoint `http://localhost:8000/v1/chat/completions`, API key blank, model from the dashboard

*Coming from Claude SkyrimNet Proxy Launcher?* Remove the old `ProxyLauncher` mod first, then follow the README's upgrade steps.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet, SKSE64
- A Claude/ChatGPT login, an API key, or Ollama

**🔀 Overlap:** Built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** Using a Claude subscription this way is a gray area under Anthropic's ToS. Your risk. Other providers don't use this method.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama support), cleanestpoison (GLM & Nano-GPT)
