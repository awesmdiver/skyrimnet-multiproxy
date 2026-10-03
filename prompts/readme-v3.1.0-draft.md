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
