# v2.4.0 — Your Google Subscription, in Skyrim

## ✨ What's New

**Already paying for Google AI Pro? Now Skyrim can use it.**

Google AI Pro and Ultra subscriptions now work the same way your Claude and ChatGPT subscriptions already do: signed in, with no API key to manage. That brings SkyrimNet MultiProxy to ten supported providers, including three subscription routes you might already be paying for.

* **Google AI Pro/Ultra support (no key needed):** Install [Google's Antigravity CLI](https://antigravity.google), sign in once with your Google account, and use the `geminicli/MODEL` prefix. You do not need to paste any API key into the dashboard.
* **Access non-Gemini models through your Google account:** Your Google subscription is not limited to Gemini. The dashboard automatically lists whichever models your signed-in account can run (including Claude and GPT-OSS models alongside Google's own), with a **Refresh** button for when Google updates the lineup.
* **Gemini API key support:** Add a Google AI Studio key and use `gemini/MODEL` to go straight to Google's Gemini API — nothing to install, no CLI in the picture. The dashboard lists all models available for your key so you don't have to guess exact model names.
* **Real streaming on the subscription route:** NPC dialogue streams in chunk by chunk as it generates, matching how SkyrimNet expects to display text, rather than waiting for the entire response to finish before sending.

## 📋 Good to Know

* **Which of the two to pick:** It comes down to what you already have. The Gemini API key route needs nothing installed and answers faster, since it calls Google's API directly. The Antigravity route needs the Antigravity CLI installed and signed in, and each reply is a real CLI call, so it takes a little longer to start — the one to use if you already have Antigravity, or you'd rather not manage an API key at all.
* **Plugin updates not required:** This update only modifies the proxy script. If you already have the SKSE plugin installed from the previous release, it will work as-is. Just replace your `proxy.py` with the new file.
* **Your configuration carries over:** You can safely keep or copy over your existing `config.json` file; the configuration format has not changed.

## 💬 Need Help?

Ran into a glitch or have a suggestion? Feel free to [open an issue on GitHub](https://github.com/awesmdiver/skyrimnet-multiproxy/issues)!
