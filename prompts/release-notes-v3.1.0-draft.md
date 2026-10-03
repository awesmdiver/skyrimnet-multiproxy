# v3.1.0 — Ready for Beta 25

![Ready for Beta 25](assets/release-banner-v3-1-0.jpg)

<!-- DRAFT — Beta 25 section written from the build spec; confirm against its handoff before the Gemini pass. -->

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
