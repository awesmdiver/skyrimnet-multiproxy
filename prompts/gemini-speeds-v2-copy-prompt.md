# Polish the Speeds v2 copy for the v3.1.0 release

Give a second pass to four pieces of copy I drafted. They describe a new feature going into a release
whose notes, README, and Discord post you've already polished. Match that voice: warm, casual, a
little playful, plain language for Skyrim players, no developer jargon.

## Context

**SkyrimNet MultiProxy** connects SkyrimNet (a Skyrim mod that gives NPCs AI-driven dialogue and more)
to whichever AI the player picks. v3.1.0's headline is "Ready for SkyrimNet Beta 25". Its dashboard has a
**Speeds tab** showing how fast each AI answers. v3.1.0 upgrades it, borrowing ideas from SkyrimNet's
new community stats page, for the player's own setup.

Facts that must stay true:
- Health cards, one per AI service: "Running smooth", "Slow to start talking", or "Hitting errors",
  with a one-line reason. A **Show** picker: "Used in the last hour" (default) or "Every ready service".
- Each model shows **Starts talking in** (how fast a reply begins), **Full reply in**, and **Talks at**
  (speed), plus a reliability label (**Reliable**, **Some errors**, **Unreliable**), a fastest / typical
  / slowest bar, and a strip of the last 40 replies with failures in red and why they failed.
- **By role** view: a tab per SkyrimNet job, **Dialogue first**, each with a one-line note on which
  number matters, and each model's share of that job. Dialogue ranks by how fast replies start; every
  other job ranks by full reply time.
- A **words/s · tokens/s** switch, words by default.
- The new numbers fill in as you play; older history shows "Not measured yet".

## What to return

1. **A.** The release-notes sub-section, in Markdown, keeping the `### A smarter Speeds tab` heading.
2. **B.** The new README bullet, one paragraph starting with `**Speeds tab:**`.
3. **C.** The **complete Discord post** below with the new "Smarter Speeds tab" line added to
   "🆕 New in v3.1.0". **The whole post must be 4,000 characters or fewer.** It's already at
   3992, so trim wording elsewhere to make room, without dropping any fact, link,
   setting name, or section. Keep the three links exactly as written, with the angle brackets and no
   spaces inside them. Discord Markdown only (**bold**, *italic*, `code`, "-" bullets).
4. **D.** The eight role explainers, one line each, same format ("Job: sentence"). Each is one or two
   short sentences shown above that job's table. Keep **full reply time** in bold where it's used.

## The drafts

# Speeds v2 copy for v3.1.0 (draft)

## A. Release notes — new sub-section under "✨ What's New", after "Built for Beta 25"

### A smarter Speeds tab
* **Health at a glance.** A card for each AI you're using tells you whether it's running smooth, slow
  to start talking, or hitting errors, and why. Pick **Show** to see just the last hour or every ready
  service.
* **"Starts talking in."** How fast a reply begins, which is the wait you actually feel in a
  conversation. Dialogue leads with it.
* **Plain reliability labels.** Each model reads **Reliable**, **Some errors**, or **Unreliable**, with a
  strip of its last 40 replies. Failures show in red, along with why they failed.
* **By role, Dialogue first.** Every SkyrimNet job gets its own tab, a one-line note on which number
  matters for it, and how much of that job each model handles.
* **Words or tokens.** Reply speed shows in words per second by default. Switch to tokens if that's
  your thing.
* The new "starts talking" and speed numbers fill in as you play. Older history shows "Not measured
  yet".

## B. README — replace the existing "Speeds tab" Key Features bullet

Current:

* **Speeds tab:** See how fast every AI you've connected is actually answering, live, right on the
  dashboard — labeled with what SkyrimNet is using it for (Dialogue, Diary, Vision, Game Master, and
  more), with a verdict that knows a slow diary entry is fine but slow dialogue isn't. History survives
  a restart and is kept forever, with a Today/7 days/30 days/All time picker, plus a head-to-head
  leaderboard comparing every AI that's ever handled a given job.

New:

* **Speeds tab:** See how your AIs are really doing, live, right on the dashboard. A health card for
  each one flags anything slow or failing, and every model shows how fast replies start, how long they
  take, and how reliable they are, labeled with what SkyrimNet uses it for. Dialogue comes first,
  since that's the wait you feel. History survives a restart, with a Today/7 days/30 days/All time
  picker and a head-to-head leaderboard for every job.

## C. Discord post — one new line in "🆕 New in v3.1.0", after "Full reply log"

- **Smarter Speeds tab:** Health at a glance for each AI, how fast replies start talking, and plain
  reliability labels, with Dialogue front and center.

(The post is at 3,992 of 4,000 characters, so this needs trimming elsewhere.)

## D. Role explainers on the Speeds tab (By role), new wording from the build

- Diary Generation: Writes an NPC's diary entries from what happened. Nobody's waiting on it, so the
  **full reply time** is what counts.
- Character Profiles: Builds an NPC's background and personality. It runs off to the side, so the
  **full reply time** is what counts.
- Universal Translator: Translates speech as it happens. The **full reply time** is what counts.
- Action Evaluation: Picks what an NPC does next. The **full reply time** is what counts.
- Combat: Handles quick lines during a fight. The **full reply time** is what counts.
- Meta: Small housekeeping calls behind the scenes. The **full reply time** is what counts.
- AI Assistant: Answers when you ask SkyrimNet's assistant something. The **full reply time** is what
  counts.
- Any other job: One of SkyrimNet's jobs for an AI. The **full reply time** is what counts.


## The current Discord post (for C)

**SkyrimNet MultiProxy v3.1.0** — Ready for Beta 25

MultiProxy connects SkyrimNet to whatever AI you prefer to run — dialogue, actions, diaries, bios, GameMaster — and boots up automatically whenever you launch the game.

**🔗 Links**
- GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
- Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
- Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>

**🆕 New in v3.1.0**
- **Ready for SkyrimNet Beta 25:** Beta 25 can switch to a backup AI when your main one stalls or fails, or race a second request against a slow one. MultiProxy now reports real failures immediately so that handoff actually triggers, instead of an NPC reading an error out loud! Abandoned race requests are tidied up without cluttering your Speeds tab.
- **Voice cues left alone:** Beta 25 needs different voice tags depending on your voice engine, so voice cues now pass straight through untouched by default. If you use Higgs, just turn on `FixHiggsVoiceTags` to keep them tidied up.
- **Tougher under pressure:** Courtesy of community member Raydonn, dropped connections now retry automatically, chats that run too long get trimmed and retried instead of falling silent, and scrambled markup tags get fixed before SkyrimNet sees them.
- **Waiting lines:** Also from Raydonn, cap how many requests each provider handles at once (`[Concurrency]` in `proxy.ini`) so busy services don't hit rate limits. GLM starts at 6.
- **Full reply log:** Turn on `LogFullReplies` to see every AI reply in full in your console.

**🐛 Fixed in v3.1.0**
- **Mod Organizer 2:** The Speeds tab now reliably finds SkyrimNet's folders on MO2 setups.
- **Faster DeepSeek:** DeepSeek skips its hidden "thinking" step by default, just like GLM.
- **Privacy:** The Speeds tab no longer switches on SkyrimNet's stats sharing. That's your call, in SkyrimNet's own Privacy settings.

**✨ Features**
- **11 supported AI options:** Claude, ChatGPT/Codex, and Google AI Pro (using your regular subscription login, no API keys needed), plus OpenRouter, GLM, Nano-GPT, OpenAI, DeepSeek, Gemini, Kilo Gateway, and local Ollama — mix and match per SkyrimNet feature.
- **Speeds dashboard:** Real-time timing, live task tracking, and benchmark history.
- **Flexible Codex routing:** Use your ChatGPT subscription, or route through OpenAI, Ollama/LM Studio, or custom endpoints.
- **Local AI ready:** Automatically detects models installed in Ollama.
- **Zero-hassle workflow:** Starts with Skyrim in the background (optional auto-close & auto-dashboard).
- **Web dashboard & secure logs:** Manage keys and test connections easily; your API keys and tokens are never logged.

**🛠️ Setup**
1. Extract the release anywhere and run `setup.bat` (installs Python packages and shows your paths).
2. Install `SkyrimNetMultiProxy.zip` in Vortex or MO2, then paste those paths into `SkyrimNetMultiProxy.ini`.
3. Launch Skyrim, open `http://127.0.0.1:8000`, pick your provider, and log in or add your key.
4. In SkyrimNet: set your endpoint to `http://localhost:8000/v1/chat/completions`, leave the API key blank, and pick your model from the dashboard.

*Upgrading?* Replace `proxy.py` AND reinstall `SkyrimNetMultiProxy.zip` in Vortex/MO2 so you get the new plugin `.dll` too. Your `config.json` and SkyrimNet settings stay untouched.

**⚙️ Requirements**
- Skyrim SE, SkyrimNet (Beta 24 or 25), and SKSE64
- An AI provider: a Claude/ChatGPT/Google login, an API key, or local Ollama

**🔀 Overlap:** Built on Galanx's Claude-SkyrimNet-Proxy and rhinos0608's skyrimnet-codex-proxy. Run only one proxy on port 8000.

⚠️ **Notice:** Using a Claude web subscription via proxy is a gray area under Anthropic's ToS — use at your own discretion. Other providers are unaffected.

🙏 **Credits:** Galanx (original Claude proxy), rhinos0608 (ChatGPT, OpenAI, Kilo, Ollama), cleanestpoison (GLM & Nano-GPT), and Raydonn (stability upgrades).
