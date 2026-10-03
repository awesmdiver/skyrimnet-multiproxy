# SkyrimNet Plugin Hub listing — ready to paste

Plugin type: **Listing**

## Title
SkyrimNet MultiProxy

## Cover image
`assets/readme-banner.jpg` (1600×893, 264 KB; the rune stone sits in the middle, so Discord's square crop still works)

## Tagline
Connect your AI of choice to SkyrimNet — set it up once, and it starts every time you play.

## Description
SkyrimNet uses AI for a lot more than just conversation: dynamic dialogue, actions, personal diaries, character bios, the GameMaster, and more. **MultiProxy** sits quietly in the background on your PC, grabs those requests, and routes each one to whichever AI model you want handling that specific job.

**Use what you already have:**
- **Subscriptions (no API keys needed):** Your existing Claude, ChatGPT, or Google AI Pro accounts.
- **API keys:** OpenRouter, GLM (z.ai), Nano-GPT, OpenAI, DeepSeek, Gemini, TypeSafe, or Kilo Gateway.
- **Ollama:** Run free models directly on your own hardware, or connect via Ollama Cloud.

**What it does for you:**
- **Starts with Skyrim:** An SKSE plugin launches the proxy automatically whenever your game boots up—nothing extra to click or remember.
- **Easy local dashboard:** Head to `http://127.0.0.1:8000` to drop in keys, grab model names, or test any AI with a quick in-character chat.
- **Live Speeds tab:** Track real response times, check connection reliability, and see which AI is running each SkyrimNet job, with Dialogue prioritized first.
- **Smooths out rough replies:** Automatically retries dropped calls, re-prompts or cleans up garbled output, and fixes broken tags before SkyrimNet ever sees them.
- **Built for Beta 25:** Properly passes real connection failures back to SkyrimNet so backup switching kicks in, instead of an NPC awkwardly reading error code out loud.

**Quick setup:** Download the release from GitHub, run `setup.bat`, install the plugin zip in Vortex or MO2, and set SkyrimNet's endpoint to `http://localhost:8000/v1/chat/completions` (leave the API key field empty). The step-by-step walkthrough is on GitHub.

⚠️ Using a Claude subscription through a proxy sits in a gray area under Anthropic's terms of service. Please read the notice on GitHub before using it. Other providers aren't affected.

## Version
3.1.0

## What changed in this version
- **Ready for SkyrimNet Beta 25:** Beta 25 can swap to a backup AI if your primary stalls, or race a second request against a slow one. MultiProxy reports real failures immediately so handoffs trigger cleanly instead of an NPC reading error code out loud! Canceled race requests are cleared away without cluttering your Speeds tab.
- **Voice cues left alone:** Beta 25 uses distinct tags per voice engine, so cues pass through untouched by default. If you use Higgs, toggle `FixHiggsVoiceTags` to keep them clean.
- **Tougher under pressure:** Courtesy of community member Raydonn, dropped connections retry automatically, long chats trim and retry instead of going silent, and scrambled markup tags get repaired before SkyrimNet sees them.
- **Waiting lines:** Also from Raydonn, cap simultaneous requests per provider (`[Concurrency]` in `proxy.ini`) to dodge rate limits. GLM defaults to 6.
- **Full reply log:** Turn on `LogFullReplies` to print complete AI replies directly in your console.
- **Smarter Speeds tab:** Live health cards for each AI, tracking how fast replies start talking, overall speeds, and plain reliability labels, with Dialogue front and center.

**Fixes**
- **Mod Organizer 2:** The Speeds tab reliably finds SkyrimNet folders on MO2 setups.
- **Faster DeepSeek:** Skips the hidden "thinking" step by default, matching GLM.
- **Privacy:** The Speeds tab no longer enables SkyrimNet stats sharing; manage that directly in SkyrimNet's Privacy settings.

## Tags
proxy, ai providers, openrouter, claude, chatgpt, gemini, deepseek, glm, ollama, local ai, performance, utility

## Languages
English

## Contains adult content
Off

## External URL
https://github.com/awesmdiver/skyrimnet-multiproxy
