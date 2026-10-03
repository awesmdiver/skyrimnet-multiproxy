# Polish the SkyrimNet Plugin Hub listing for MultiProxy

Give a second pass to the plugin listing below. I drafted it. Make it read naturally and scan easily for a
Skyrim player browsing a plugin catalog, without losing or adding any facts.

## Context

SkyrimNet is a Skyrim mod that gives NPCs AI-driven dialogue, actions, diaries and more. It now has an
official **Plugin Hub**: a catalog inside SkyrimNet's own dashboard and on fateless.ai, where each plugin
gets a title, a one-line **tagline**, a markdown **description**, and comma-separated **tags**.
SkyrimNet's team asked the author to list **SkyrimNet MultiProxy** there, even though it isn't an
in-game plugin, so everything lives in one place.

MultiProxy is a free, open-source background tool on the player's PC. It takes SkyrimNet's AI requests
and routes each to the AI the player picked (Claude, ChatGPT or Google subscriptions without keys;
OpenRouter, GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, TypeSafe, Kilo Gateway with keys; or Ollama). An SKSE
plugin starts it with the game. It has a local dashboard, a Speeds tab (reply start time, reliability, per
SkyrimNet job, Dialogue first), and cleans up broken replies. v3.1.0 is its current release, built for
SkyrimNet Beta 25.

## Rules

- Voice: warm, casual, a little playful, plain language for players, no developer jargon.
- **Tagline:** one line, under about 100 characters. It currently reuses the README's own tagline; keep it
  close unless you have a clearly better one-liner.
- **Description:** markdown, short sections with bold lead-ins and bullets. Keep every provider name, the
  URL, the address `http://localhost:8000/v1/chat/completions`, `setup.bat`, and the Claude terms
  notice (keep its ⚠️ and its measured tone). Keep it roughly the same length or shorter.
- **Tags:** 8 to 12 short, lowercase-friendly tags a player would actually search for. Comma-separated.
- Return three clearly labeled sections: **Tagline**, **Description**, **Tags**, ready to paste.

## The draft

# SkyrimNet Plugin Hub listing (draft)

**Title:** SkyrimNet MultiProxy
**Version:** 3.1.0

## Tagline

Connect your AI of choice to SkyrimNet — set it up once, and Skyrim starts it for you every time you play.

## Description (markdown)

SkyrimNet uses AI for a lot more than NPC dialogue: conversations, actions, diaries, bios, the GameMaster
and more. **MultiProxy** is a small tool that runs in the background on your PC, takes each of those
requests, and sends it to whichever AI you pick for that job.

**Use what you already have:**
- **Subscriptions, no API key:** your Claude, ChatGPT, or Google AI Pro account.
- **API keys:** OpenRouter, GLM (z.ai), Nano-GPT, OpenAI, DeepSeek, Gemini, TypeSafe, or Kilo Gateway.
- **Ollama:** free models on your own PC, or Ollama Cloud.

**What you get:**
- **Starts with Skyrim.** A small SKSE plugin launches the proxy when the game starts, so there's nothing
  to remember.
- **A dashboard** at `http://127.0.0.1:8000` to add keys, copy model names, and test any AI with a
  quick in-character chat.
- **A Speeds tab** that shows how each AI is really doing: how fast replies start, how reliable it is,
  and which SkyrimNet job it's handling, with Dialogue first.
- **Tougher under pressure.** Dropped connections retry, garbled replies get cleaned up or asked again,
  and broken markup tags get fixed before SkyrimNet sees them.
- **Ready for Beta 25.** Real failures are reported properly, so SkyrimNet's backup switching kicks in
  instead of an NPC reading an error out loud.

**Setup:** download from GitHub, run `setup.bat`, install the plugin zip in Vortex or MO2, then point
SkyrimNet at `http://localhost:8000/v1/chat/completions` with the API key left blank. The full guide is
on GitHub.

⚠️ Using a Claude subscription through a proxy is a gray area under Anthropic's terms. Read the notice
on GitHub first. Other providers aren't affected.

**Download and guide:** https://github.com/awesmdiver/skyrimnet-multiproxy

## Tags (comma-separated)

proxy, AI providers, OpenRouter, Claude, ChatGPT, Gemini, DeepSeek, GLM, Ollama, local AI, speed, utility

## Other fields

- **Languages:** English
- **Contains adult content:** off
