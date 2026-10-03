# v2.4.0 — Your Google Subscription, In Skyrim

## What's New

**Already paying for Google AI Pro? Now Skyrim can use it.**

Google AI Pro and Ultra subscriptions now work the same way your Claude and ChatGPT subscriptions
already do here — signed in, no API key, no per-token billing. That brings SkyrimNet MultiProxy to ten
providers, and three of them are subscriptions you may already own.

* **Google AI Pro/Ultra support, no key needed:** Install [Google's Antigravity
  CLI](https://antigravity.google), sign in once with your Google account, and use `geminicli/MODEL`.
  Nothing to paste into the dashboard.
* **More than Gemini under one login:** Your Google subscription isn't Gemini-only. The dashboard
  lists every model the signed-in account can actually run — today that includes Claude and GPT-OSS
  models alongside Google's own — with a **Refresh** button for when Google's lineup changes.
* **Gemini API key support too:** Prefer pay-as-you-go? Add a Google AI Studio key and use
  `gemini/MODEL` to go straight to Google's own Gemini API. The dashboard lists the models your key
  can run, so you're never guessing at a model name.
* **Real streaming on the subscription route:** NPC replies arrive in pieces as they're generated,
  the way SkyrimNet is built to show them — not held back and delivered in one lump at the end.

## Improvements & Polish

* **Provider errors no longer disappear:** When a subscription CLI failed, its real error message
  could be thrown away before anything got to read it, leaving you with a failure and no reason for
  it. The message now always makes it through to the log and the dashboard.

## Good to Know

* **What a Google subscription actually allows:** Google doesn't publish a request count. It gives
  you two rolling windows — a 5-hour limit and a weekly one — and each request spends from them in
  proportion to its cost, so shorter prompts and cheaper models stretch much further. You can check
  where you stand any time by running `agy` and typing `/usage`.
* **Subscription replies take longer:** Like ChatGPT/Codex, the Google subscription route runs
  through a real CLI call per reply rather than a direct API call. The Gemini API key route is the
  fast one.
* **Nothing to reinstall for the plugin:** This release only changes the proxy. If you already have
  v2.3.0's SKSE plugin installed, your existing setup keeps working — just replace `proxy.py` with
  the new one.
* **Your saved keys carry over:** Copy your existing `config.json` into the new folder as usual;
  nothing about its format changed.

## Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on
GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
