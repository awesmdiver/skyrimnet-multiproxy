![Ready for Beta 25](https://raw.githubusercontent.com/awesmdiver/skyrimnet-multiproxy/master/assets/release-banner-v3-1-0.jpg)

## ✨ What's New

**SkyrimNet Beta 25 arrived on September 26, and MultiProxy is fully tuned up for it.**

Beta 25 is SkyrimNet’s biggest update yet, bringing "smart routing" that swaps to a backup provider whenever an AI stalls or fails, and the ability to race a second request against a sluggish model. MultiProxy v3.1.0 makes sure those features work seamlessly without a hitch. 

This release also folds in a fantastic suite of stability upgrades adapted straight from community member **Raydonn's** personal proxy setup.

### Built for Beta 25
* **Smart routing actually catches failures.** Previously, some proxy errors were handed back to SkyrimNet as regular text replies—meaning an NPC might literally read out a raw error message. Now, errors reach SkyrimNet as real failures, allowing Beta 25 to immediately pivot to your backup AI.
* **Clean racing between models.** When Beta 25 races a second model against a slow one and drops the loser, MultiProxy quietly cleans up the canceled request so it doesn't clutter your Speeds tab with false errors.
* **Voice tags tailored to your engine.** Beta 25 requests different voice cues depending on whether an NPC uses Higgs, Chatterbox, or no voice engine at all. MultiProxy now passes these cues through untouched by default. If your NPCs use Higgs, you can turn on `FixHiggsVoiceTags` in `proxy.ini` to have the proxy tidy them up automatically.
* **Full rotation tracking on the Speeds tab.** If you configure a SkyrimNet task to cycle between several models, the Speeds tab now tracks and labels every model in the rotation instead of only showing the first.

### A smarter Speeds tab
* **Health at a glance.** A card for each active AI tells you whether it's "Running smooth", "Slow to start talking", or "Hitting errors", with a quick one-line reason why. Use the **Show** toggle to check services "Used in the last hour" (default) or "Every ready service".
* **"Starts talking in."** That initial pause before an NPC speaks is the wait you actually feel. Each model now highlights how fast it starts talking, alongside **Talks at** (speed) and **Full reply in** times.
* **Plain reliability ratings.** Models earn clear **Reliable**, **Some errors**, or **Unreliable** tags, paired with a fastest / typical / slowest spread bar and a mini strip of the last 40 replies. Failures show in red along with why they stumbled.
* **By role, Dialogue first.** Every SkyrimNet job gets its own dedicated tab and a quick note on which metric matters most, showing each model's share of that workload. Dialogue ranks models by how quickly they start talking, while all other jobs rank by full reply time.
* **Words or tokens.** Reply speeds display in words per second by default, with a one-click toggle to tokens per second if you prefer raw numbers.
* **Fills in as you play.** Fresh stats gather live while you explore. Previous runs show "Not measured yet" until new replies roll in.

### Tougher Under Pressure (Upgrades via Raydonn)
* **Markup tag fixer (`FixNpcTags`).** AIs sometimes mangle the little formatting tags SkyrimNet needs. The proxy now repairs these before SkyrimNet sees them. This is enabled by default; set `FixNpcTags` to `false` in `proxy.ini` if you prefer raw output.
* **Shrugs off hiccups automatically.** Dropped network connections are retried right away. If a conversation grows too long for a model's context window, the proxy trims out the oldest middle messages and retries rather than letting your NPC fall silent.
* **Polite waiting lines (`[Concurrency]`).** Avoid tripping rate limits by capping how many requests each provider handles at once under `[Concurrency]` in `proxy.ini`. GLM starts capped at 6 (matching z.ai plan limits) while other providers remain unlimited. The dashboard also displays how busy each capped provider is in real time.
* **Full reply logging (`LogFullReplies`).** Want to inspect everything under the hood? Turn on `LogFullReplies` in `proxy.ini` to print every AI response in full directly to the console.

## 🔧 Improvements & Polish

* **Mod Organizer 2 support on the Speeds tab.** Linking your game directory used to lose track of SkyrimNet files inside MO2's virtual file tree. MultiProxy now checks standard MO2 folder layouts, and it will plainly let you know if it still can't locate them.
* **Faster DeepSeek responses.** DeepSeek models now skip their hidden "thinking" step by default (just like GLM already does), giving you snappy NPC dialogue instead of making you wait on internal reasoning you never see.
* **The Speeds tab no longer changes SkyrimNet's privacy settings.** Its old **Turn it on** button also switched on SkyrimNet's "Send Anonymous Usage Metrics" setting without saying so. That choice is yours to make, so the button is gone. The Speeds tab now points you to SkyrimNet's own **Settings → General → Privacy** instead. If you used the button before and would rather not share, you can turn it off there.

## 📋 Good to Know

* **How to update:** Replace your existing `proxy.py` with the new file, and reinstall the new `SkyrimNetMultiProxy.zip` mod archive in Vortex or MO2 over your old plugin. Your `config.json`, saved keys, and logins remain safe and untouched.
* **GLM users:** The concurrency waiting line defaults GLM to 6 simultaneous requests to match z.ai's base plan tiers. If your plan allows more, feel free to raise `GLM` under `[Concurrency]` in `proxy.ini`.
* **Still on Beta 24?** No rush—everything in this update is backward-compatible and runs smoothly on Beta 24, too.

## 🤝 Special Thanks

* **Raydonn**, for sharing their personal tuned-up proxy. The tag fixer, automatic retries, context trimming, waiting lines, and full-reply logging all originated from their work.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
