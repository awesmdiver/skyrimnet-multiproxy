<!-- Gemini-polished 2026-09-27; two factual fixes (dashboard shows a text line, not meters; "never" overclaimed). Apply to README.md AFTER the v3.1.0 release is published, per RELEASING.md step 8. Re-check Beta 25 facts against that build's handoff first. -->

Here is the polished second pass for the four README additions, formatted and ready to paste:

## 1. At a Glance table — Compatibility row

```markdown
| **Compatibility** | Skyrim SE, SkyrimNet Beta 24 and Beta 25 |
```

## 2. Key Features — two new bullets, placed right after "Vision support"

* **Ready for SkyrimNet Beta 25:** When an AI stalls or fails, Beta 25 can smoothly fall back to your next provider in line, and MultiProxy now reports real errors so that switch actually fires instead of an NPC reading a glitch aloud. Dropped race requests get cleaned up quietly without dinging your stats on the Speeds tab. Voice cues pass straight through to suit whatever voice engine your NPCs use, though Higgs users can turn on `FixHiggsVoiceTags` to tidy them automatically.
* **Tougher under pressure:** Dropped connections retry on their own, chats that run too long get trimmed in the middle and retried instead of going silent, and garbled markup tags get cleaned up before SkyrimNet ever sees them. You can also set waiting lines under `[Concurrency]` in `proxy.ini` so busy providers don't trip their rate limits. The dashboard shows how busy each capped provider is, so you can see who's swamped at a glance.

## 3. Credits — new line, after cleanestpoison

* **Raydonn** (Discord: `r_raydonn`) — The tag fixer, automatic connection retries, chat trimming, per-provider request limits, and full-reply console logging all started in their own customized proxy.

## 4. Getting Started TIP about proxy.ini — extended

> To turn on automatic shutdown, debug logs, full AI replies in the console, or per-provider request limits, copy **`proxy.ini.example`** to **`proxy.ini`** in your `SkyrimNet MultiProxy` folder and adjust the settings. If you skip this, default behavior applies.


## 5. Key Features — replace the existing "Speeds tab" bullet with

* **Speeds tab:** See how your AIs are actually performing live on the dashboard. Dedicated health cards flag whether a service is running smooth, dragging its feet, or throwing errors. Each model tracks when it starts talking, total reply time, reply speed, and reliability—categorized by SkyrimNet job, with Dialogue ranked first to keep conversational pauses short. Your history survives restarts, complete with time filters and head-to-head job leaderboards.
