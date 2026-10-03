# Polish the v3.1.0 release notes for SkyrimNet MultiProxy

Give a second pass to the GitHub release notes below. I drafted them. I want them to read more
naturally and be easy for a mod player to scan, without losing any real information or adding any.

## The project

**SkyrimNet MultiProxy** is a free, open-source download for Skyrim players. SkyrimNet is a Skyrim mod
that gives NPCs AI-driven dialogue (plus actions, diaries, a GameMaster and more), and it needs an
OpenAI-style chat endpoint to talk to. MultiProxy is that bridge: a small background tool on the
player's PC that takes each SkyrimNet request and sends it to whichever AI the player picked (Claude,
ChatGPT, Google, OpenRouter, GLM, DeepSeek, Ollama and more). An SKSE plugin starts it automatically
with the game. Settings live in a `proxy.ini` file next to the proxy.

## What's in v3.1.0 (facts that must stay true)

- **Headline: ready for SkyrimNet Beta 25**, SkyrimNet's biggest update, released 2026-09-26. Beta 25
  added "smart routing": when an AI provider fails or stalls, SkyrimNet switches to the next one in the
  player's rotation. It can also race a second request against a slow one and drop the loser. Beta 25
  also asks for different voice tags depending on each NPC's voice engine (Higgs, Chatterbox, or none).
- MultiProxy changes for Beta 25: proxy errors now reach SkyrimNet as real failures (before, some
  showed up as a normal reply, and an NPC could even read the error aloud), so Beta 25's backup
  switching works. Dropped race requests get cleaned up and don't count as failures on the Speeds tab.
  Voice cues pass through exactly as the AI wrote them by default; Higgs users can turn on
  `FixHiggsVoiceTags` to have them tidied. The Speeds tab labels every model when a job rotates
  between several.
- Also new, from community member **Raydonn**'s own customized proxy: a tag fixer that tidies garbled
  markup tags (on by default, `FixNpcTags`); automatic retry of dropped connections; too-long chats get
  their oldest middle messages trimmed and retried instead of failing; a per-provider "waiting line"
  that caps simultaneous requests (`[Concurrency]` in `proxy.ini`; GLM starts at 6, matching z.ai's
  plan limits; others unlimited), with the dashboard showing how busy each capped provider is; and
  `LogFullReplies` (off by default) to print every AI reply in full in the console.
- Two fixes since v3.0.0: the Speeds tab now finds SkyrimNet's files under Mod Organizer 2 setups
  (and says plainly if it still can't); DeepSeek models skip their hidden "thinking" step by default
  for faster, plainer dialogue, the same way GLM already did.
- Updating means replacing `proxy.py` AND reinstalling the new `SkyrimNetMultiProxy.zip` plugin in
  Vortex/MO2. Saved keys (`config.json`) are untouched. Everything also still works on Beta 24.

## Voice

Warm, casual, a little playful, plain language, written for Skyrim players rather than developers.
Short scannable bullets with a bold lead-in. Natural contractions. No jargon like "SSE", "HTTP
status", "semaphore" or "endpoint errors". Keep every setting name, file name and section header
exactly as written. Don't add features or claims that aren't listed above.

## Format rules

- Keep the section headers and their emojis exactly: `## ✨ What's New`, `## 🔧 Improvements & Polish`,
  `## 📋 Good to Know`, `## 🤝 Special Thanks`, `## 💬 Need Help?`.
- Keep the `# v3.1.0 — Ready for Beta 25` title line and the banner image line exactly.
- The headline sells ONE thing, Beta 25 readiness. Raydonn's upgrades are a clear second section, not
  a co-headline.
- Return the full release notes in Markdown, ready to paste.

## The draft

# v3.1.0 — Ready for Beta 25

![Ready for Beta 25](assets/release-banner-v3-1-0.jpg)

## ✨ What's New

**SkyrimNet Beta 25 is here, and MultiProxy is ready for it.**

Beta 25 rebuilt how SkyrimNet picks and switches between AI providers. When one stalls or fails, it
now jumps to a backup instead of leaving your NPC hanging. v3.1.0 makes sure the proxy plays along,
and it's a whole lot sturdier under pressure too.

### Built for Beta 25
* **Smart routing actually sees trouble now.** Before, some proxy errors showed up to SkyrimNet as a
  normal reply, and an NPC might even read the error out loud. Now SkyrimNet sees a real failure and
  switches to your next provider, just like Beta 25 intended.
* **Races handled cleanly.** When one provider is slow to start talking, Beta 25 can race a second
  request against it and drop the loser. The proxy tidies up after the dropped one, and it won't
  count as a failure on your Speeds tab.
* **Voice cues that match your voice engine.** Beta 25 asks for different voice tags depending on
  which voice engine each NPC uses. The proxy now leaves them exactly as the AI wrote them. If your
  NPCs use Higgs, you can turn on `FixHiggsVoiceTags` in `proxy.ini` to have the proxy tidy their
  voice cues too.
* **Speeds tab keeps up with model rotations.** Beta 25 lets one job rotate between several models.
  The Speeds tab now labels every model in the rotation, not just the first one.

### Tougher under pressure
* **Tag fixer.** AIs sometimes garble the little markup tags SkyrimNet relies on. The proxy tidies
  those up before SkyrimNet sees the reply. On by default, and `FixNpcTags` in `proxy.ini` turns it
  off.
* **Shrugs off hiccups.** A dropped connection gets retried automatically. If a chat grows too long
  for the model, the oldest middle bits get trimmed and it tries again, instead of the NPC going
  silent.
* **A polite waiting line.** Cap how many requests each provider handles at once under
  `[Concurrency]` in `proxy.ini`. Extra requests wait their turn instead of tripping rate limits, and
  the dashboard shows how busy each capped provider is.
* **Watch the full replies.** Turn on `LogFullReplies` in `proxy.ini` to print every AI reply in full
  in the console. Great for debugging prompts.

## 🔧 Improvements & Polish

* **Mod Organizer 2 players: the Speeds tab finds SkyrimNet now.** Linking your Skyrim install used
  to miss SkyrimNet's files under MO2 setups. It now checks the usual MO2 layouts, and tells you
  plainly if it still can't find them.
* **DeepSeek answers faster.** DeepSeek models now skip their hidden "thinking" step by default, the
  same way GLM already does. You get quick, plain NPC dialogue instead of waiting on reasoning nobody
  sees.

## 📋 Good to Know

* **How to update:** replace your old `proxy.py` with the new one, and install the new
  `SkyrimNetMultiProxy.zip` in Vortex or MO2 over the old plugin. Your `config.json` and saved keys
  and logins stay safe and untouched.
* **GLM users:** the waiting line starts GLM at 6 requests at once, which matches z.ai's plan limits.
  If your plan allows more, raise `GLM` under `[Concurrency]` in `proxy.ini`.
* **Still on Beta 24?** Everything here works there too.

## 🤝 Special Thanks

* **Raydonn**, for sharing their own tuned-up proxy. The tag fixer, retries, chat trimming, waiting
  line and full-reply log all started there.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
