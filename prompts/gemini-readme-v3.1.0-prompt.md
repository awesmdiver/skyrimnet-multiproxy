# Polish the v3.1.0 README changes for SkyrimNet MultiProxy

Give a second pass to the README additions below. I drafted them. They get dropped into an existing
README, so they must match its style: each Key Features bullet is one paragraph starting with a
**bold lead-in:** phrase, then two or three plain sentences. Improve the flow and scannability without
losing or adding information.

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

## Two existing Key Features bullets, for tone reference

* **Speeds tab:** See how fast every AI you've connected is actually answering, live, right on the
  dashboard — labeled with what SkyrimNet is using it for (Dialogue, Diary, Vision, Game Master, and
  more), with a verdict that knows a slow diary entry is fine but slow dialogue isn't.
* **Starts automatically:** When Skyrim boots, the plugin checks if the proxy is already active. If
  not, it launches it silently in the background.

## The draft changes

# README changes for v3.1.0 (draft)

Only the parts that change. Everything else in the README stays as is.

## 1. At a Glance table — Compatibility row

Current: `| **Compatibility** | Skyrim SE |`

New: `| **Compatibility** | Skyrim SE, SkyrimNet Beta 24 and Beta 25 |`

## 2. Key Features — two new bullets, placed right after "Vision support"

* **Ready for SkyrimNet Beta 25:** Beta 25 can switch to a backup AI when one stalls or fails, and
  MultiProxy tells it plainly when a provider is in trouble so the switch actually happens. Voice
  cues come through exactly as the AI wrote them, so they stay right for whichever voice engine your
  NPCs use. Higgs users can have them tidied up too.
* **Tougher under pressure:** Dropped connections retry on their own, chats that grow too long get
  trimmed and retried instead of going silent, and garbled markup tags get tidied before SkyrimNet
  sees them. You can also cap how many requests each provider handles at once, so busy services
  don't trip their rate limits. The dashboard shows how full each line is.

## 3. Credits — new line, after cleanestpoison

* **Raydonn** (Discord: `r_raydonn`) — The tag fixer, connection retries, too-long-chat trimming,
  per-provider request limits, and full-reply logging all started in their own customized proxy.

## 4. Getting Started TIP about proxy.ini — extended

Current:

> To enable automatic shutdown or proxy debug logs, copy **`proxy.ini.example`** to **`proxy.ini`**
> in your `SkyrimNet MultiProxy` folder and adjust the settings. If you skip this, default behavior
> applies.

New:

> To turn on automatic shutdown, debug logs, full AI replies in the console, or per-provider request
> limits, copy **`proxy.ini.example`** to **`proxy.ini`** in your `SkyrimNet MultiProxy` folder and
> adjust the settings. If you skip this, default behavior applies.


Return the same four numbered changes, each with its final text, ready to paste.