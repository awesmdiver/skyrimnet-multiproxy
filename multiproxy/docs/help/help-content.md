# MultiProxy Help

<!-- Final wording: Gemini Pro pass (2026-10-01) on the design-side draft, with a few fact corrections.
     Source facts: docs/help/provider-facts.md. The dashboard's Help tab is built from this file. -->

## START HERE

### How MultiProxy works

SkyrimNet only ever talks to MultiProxy, using a single address (**MultiProxy's address**, in **Settings** under **SkyrimNet connection**). MultiProxy acts like a switchboard, passing each request to whichever AI you've picked. 

You tell it which AI to use simply by typing a **model name** into SkyrimNet. The first part of that name tells MultiProxy where to send it.

- `claude-sonnet-5-5` (no first part) goes to Claude.
- `gemini/gemini-3.8-flash` goes to Gemini.
- `local/gemma-4-12b` goes to a model running on this device.
- `laptop/gemma-4-12b` goes to a model on your device nicknamed "laptop".

**Want to choose by job instead?** On the **Routing** tab you can pick which model answers Dialogue, Combat, Memory and the other jobs, with backups if one fails. Nothing changes until you do. [Routing](#help/routing) explains how.

**Setting up SkyrimNet (just once):**
In SkyrimNet's own settings page, go to **AI > Providers** and press **+ Add Custom Provider**. Name it **SkyrimNet MultiProxy**, paste MultiProxy's address into **API Endpoint** (and set **API Style** to **Chat Completions**), and leave the **API Key** box completely empty (MultiProxy keeps your keys safe, so SkyrimNet does not need them). Then go to **AI > Models**: for each job you want MultiProxy to handle (like Dialogue or Combat), pick **SkyrimNet MultiProxy** in the **Provider** list and paste a model name from this dashboard into the **Model** box. Every model on the Providers tab has a handy **Copy model name** button to get the exact name!

**Installing it:**
1. Double-click the MultiProxy installer.
2. Page 1: press Next (the two folders are fine as they are).
3. Old-settings page (only appears if you used the zip before): pick the old folder, or press Next.
4. Page 2: press "Open folder" and drag the mod file into Vortex or MO2. You can also do this later.
5. Page 3: leave the ticks as they are, optionally choose where models are saved, and press Install.
6. Last page: leave "Open the dashboard now" ticked, press Finish. MultiProxy is ready by your clock.
7. To remove it: Windows Settings > Apps > SkyrimNet MultiProxy > Uninstall; tick what to delete.

**The icon by your clock:**
1. Look for the small M icon by the clock (it is started by the installer, the Start menu, or the Skyrim mod).
2. A green dot means it is working; yellow means starting or no models ready yet; red means stopped.
3. Double-click the icon (or pick "Open dashboard") to open MultiProxy.
4. If the dot is red, pick "Start MultiProxy". If it stays red, hover the icon to read why, then pick "View recent activity".
5. Tick "Start with Windows" if you want it ready every time.
6. Tick "Start and stop with Skyrim" to have it open and close with the game (install the mod first).
7. "Quit MultiProxy" closes everything.

**Tip:** Use **+ Add model to rotation** in SkyrimNet to let a job take turns between different models. You can also type several models separated by commas in one **Model** box: MultiProxy will nicely take turns between them.

### Which AI should I use?

There are four main kinds, and you are completely free to mix and match them!

| Kind | What it costs | Providers |
| :--- | :--- | :--- |
| **A subscription you already pay for** | Nothing extra | Claude, Codex (ChatGPT), Google Antigravity, Grok (SuperGrok) |
| **Pay-as-you-go with a key** | A little per reply | GLM (z.ai), Nano-GPT, OpenAI, DeepSeek, Gemini, xAI, Continuum, Kilo Gateway, TypeSafe (Jev), Perplexity |
| **A small free allowance with a key** | Free, but small | Cloudflare Workers AI, Mistral, Groq |
| **Your own hardware** | Free | Local device, Ollama, LM Studio, Other devices |

A green dot next to a provider means it is ready to go. A grey dot just means it isn't set up yet. Don't worry about the grey dots—you only need one green one to play!

---

## SUBSCRIPTIONS (NO KEY)

### Claude

**What it is:** Anthropic's Claude, using the Claude subscription you already pay for.

**What you need:** A Claude plan that includes Claude Code (Pro, Max, Team, or Enterprise; the free plan doesn't include this), and Anthropic's free **Claude Code** app installed on this device.

**Set it up:**
1. Install Claude Code: open PowerShell and run `irm https://claude.ai/install.ps1 | iex` (full instructions are at code.claude.com/docs/en/setup).
2. Run `claude` once and sign in when your web browser opens.
3. Restart MultiProxy. The Claude dot will turn green.

**Use it in SkyrimNet:** Just the plain model name with nothing in front, like `claude-sonnet-5-5`.

**Good to know:**
- There is no key and no per-reply charge. It just uses your normal subscription.
- ⚠️ Using a subscription this way sits in a grey area of Anthropic's terms. It could stop working at any time, and your account could be affected. Use it knowing that.
- If Claude suddenly stops answering during a game, just restart MultiProxy.
- **If the Claude dot is orange**, read the line under it, then:
  1. **Update Claude Code.** In a terminal, run `claude update`. Check it worked with `claude --version`.
  2. **Sign in again.** Run `claude` and sign in when asked. Test it with `claude --print "say ok"`. It should answer.
  3. **Restart MultiProxy.** The Claude dot will turn green.
- **Claude on another device.** On a device that runs Courier, MultiProxy can install Claude and sign it in for you. The sign-in code comes from Claude's own `claude setup-token` command, which prints it for you to paste in. MultiProxy sends the code to the Courier on that device over your home network, and it is kept only on that device, in Courier's own folder. Signing out or uninstalling Claude there deletes it.

### Codex (ChatGPT)

**What it is:** OpenAI's models, using your ChatGPT subscription and OpenAI's **Codex** app.

**What you need:** A ChatGPT plan that includes Codex (check OpenAI's site to see which plans do), and the Codex app itself.

**Set it up:**
1. Install it: open a terminal and run `npm install -g @openai/codex`. This needs Node.js on your device first; it's free from nodejs.org.
2. Run `codex login` once and sign in with your ChatGPT account.
3. On the Codex panel here in the dashboard, press **Check for models**.

**Use it in SkyrimNet:** `codex/MODEL`, like `codex/gpt-5.5`.

**Good to know:**
- This is a bit slower than the others. Each reply runs the Codex app from scratch, and the whole reply arrives all at once instead of streaming in.
- It is text only. It can't look at pictures of your game.
- **Add an unlisted model:** The list only shows a few recommended models. If you want to use one that isn't listed, type its name in the **Model name** box and press **Add model** (or use the **Add an unlisted model** tab) so it stays in your list. A name that is not valid fails with Codex's own error, just as it would if you typed it by hand.
- **Extra providers:** The Codex panel keeps its own separate list of extra providers (**Add built-in provider** for OpenAI, Ollama or LM Studio, **Add custom provider** for any other service). MultiProxy never reads or changes your real `~/.codex/config.toml`. A custom provider has to speak Codex's own Responses format (most modern OpenAI-compatible services do; an older Chat Completions-only service will not work here).
- This is completely different from the **OpenAI** provider below, which uses a paid key.

### Google Antigravity

**What it is:** Google's **Antigravity** app, linked to your Google account so your Google AI Pro or Ultra plan covers the chat.

**What you need:** A Google AI Pro or Ultra subscription and the Antigravity app (called `agy`).

**Set it up:**
1. Install it: open PowerShell and run `irm https://antigravity.google/cli/install.ps1 | iex` (more details at antigravity.google).
2. Run `agy` once and sign in with your Google account.
3. On the Antigravity panel here, press **Check for models**.

**Use it in SkyrimNet:** `geminicli/MODEL`, like `geminicli/gemini-3.8-flash-high`.

**Good to know:**
- It isn't just Gemini! Depending on your plan, you might also see Claude and other models listed here.
- It runs a bit slower than the key-based providers, and is the slowest option if you are using pictures.
- Your allowance resets on a rolling basis. To check what you have left, run `agy` in your terminal and type `/usage`.
- This is different from the **Gemini** provider below, which uses a key. (And yes, the name to type here is `geminicli`, not `gemini`.)

### Grok (SuperGrok)

**What it is:** xAI's **Grok** app (called `grok`), signed in with your SuperGrok or X Premium+ account, so your plan covers the chat.

**What you need:** A SuperGrok or X Premium+ subscription and the Grok app.

**Set it up:**
1. Install it: open PowerShell and run `irm https://x.ai/cli/install.ps1 | iex` (more details at x.ai).
2. Run `grok login` once and sign in. (No browser on this device? Run `grok login --device-code` instead.)
3. On the Grok panel here, press **Check for models**.

**Use it in SkyrimNet:** `grok/MODEL`, like `grok/grok-4.6`.

**Good to know:**
- It runs a bit slower than the key-based providers, because the Grok app starts fresh for each reply.
- Pictures aren't sent to Grok here.
- Your plan has its own usage limits. Check xAI's site for what yours allows.
- This is different from **xAI (API key)** below, which is paid per use. (The name to type here is `grok/`, not `xai/`.)

---

## PAY-AS-YOU-GO (A KEY)

All of these work exactly the same way: you make an account on their website, create a secret "key", paste that key into the provider's panel on the Providers tab, and press **Save**. Your keys are stored on this device only.

### GLM (z.ai)

**What it is:** Z.ai's GLM models.

**What you need:** A z.ai account with a little bit of money added to the balance.

**Set it up:** Create a key at z.ai/manage-apikey/apikey-list, paste it into the panel, and press Save.

**Use it in SkyrimNet:** `glmzai/MODEL`, like `glmzai/glm-5.3`. (Make sure to use `glmzai`, not just `glm`!)

**Good to know:** Z.ai limits how many requests you can run at the exact same time. MultiProxy handles this by sending 6 at a time and politely queueing the rest. If your z.ai plan allows more (or fewer), change the number in Settings, under **Advanced: Concurrent jobs**.

### Nano-GPT

**What it is:** A fantastic all-in-one key that unlocks hundreds of models from different makers, including uncensored roleplay models.

**What you need:** A nano-gpt.com account with a little bit of balance.

**Set it up:** Create a key at nano-gpt.com/api, paste it in, and press Save.

**Use it in SkyrimNet:** `nano/MODEL`, like `nano/qwen/qwen3.8-27b-uncensored`.

**Good to know:** The panel here only lists a couple of models to get you started, but absolutely any model name from Nano-GPT's site will work as long as you put `nano/` in front of it.

### OpenAI

**What it is:** OpenAI's own paid developer service. (Remember, this is not your ChatGPT subscription—that is **Codex**).

**What you need:** An OpenAI developer account with your billing information set up.

**Set it up:** Create a key at platform.openai.com/api-keys, paste it in, and press Save.

**Use it in SkyrimNet:** `openai/MODEL`, like `openai/gpt-5.5`.

**Good to know:** You never need an OpenAI key just to use MultiProxy itself! 

### DeepSeek

**What it is:** DeepSeek's own paid service. It is known for being fast and cheap.

**What you need:** A DeepSeek account with a little bit of balance.

**Set it up:** Create a key at platform.deepseek.com/api_keys, paste it in, and press Save.

**Use it in SkyrimNet:** `deepseek/MODEL`, like `deepseek/deepseek-flash`.

**Good to know:** Its models think before they answer, and that thinking counts toward the length of the reply.

### Gemini

**What it is:** Google's Gemini models using a Google AI Studio key. (This is different from your Google subscription, which is **Antigravity**).

**What you need:** A standard Google account.

**Set it up:** Create a key at aistudio.google.com/apikey, paste it in, and press Save. The panel will then light up and show you the models your key can use.

**Use it in SkyrimNet:** `gemini/MODEL`, like `gemini/gemini-3.8-flash`.

**Good to know:** There is a free tier available, but it only allows a few requests per minute. A busy Skyrim scene with lots of NPCs talking can easily exceed this limit. On the plus side, Gemini is fantastic at reading pictures!

### xAI (API key)

**What it is:** xAI's Grok models using a key from xAI's console. (This is different from a SuperGrok subscription, which is **Grok (SuperGrok)** above.)

**What you need:** An xAI account with some credit on it.

**Set it up:** Create a key at console.x.ai, paste it in, and press Save. The panel will then light up and show you the models your key can use.

**Use it in SkyrimNet:** `xai/MODEL`, like `xai/grok-4`.

**Good to know:** You pay xAI for what you use, so a busy scene costs more than a quiet one.

### Continuum (plan key)

**What it is:** Continuum's AI service, using a personal key from your Continuum plan, so replies come out of your plan instead of a separate bill.

**What you need:** A Continuum plan, and a key from your account.

**Set it up:**
1. Open your Continuum account, go to Settings, then Account, and make a key in the Inference API part. It starts with `cont_sk_`.
2. Paste it in and press **Save**. (If it doesn't start with `cont_sk_`, you will be asked once before it saves.)
3. Press **Check for models**. The panel shows the models your key can use.

**Use it in SkyrimNet:** `continuum/MODEL`, like `continuum/claude-opus-5-5`.

**Good to know:**
- Every reply counts against your Continuum plan's weekly budget. A busy scene uses it up faster than a quiet one.
- The dot turns amber if Continuum says it doesn't accept the key, and says so on the panel.

### Kilo Gateway

**What it is:** Kilo's gateway service. Like Nano-GPT, it gives you one key to access hundreds of models from many different makers.

**What you need:** A Kilo account.

**Set it up:** Get a key at kilo.ai, paste it in, and press Save.

**Use it in SkyrimNet:** `kilo/MAKER/MODEL`, like `kilo/anthropic/claude-sonnet-5`.

**Good to know:** Pay close attention to the model names on their site, as you need to include the maker's name.

### TypeSafe (Jev)

**What it is:** Jev is a tiny, lightning-fast AI dedicated purely to making SkyrimNet's quick background decisions (like figuring out an NPC's mood, who should speak next, or who you are talking to). It does not write actual dialogue.

**What you need:** A TypeSafe key from typesafe.ai.

**Set it up:** Paste your key in and press Save.

**Use it in SkyrimNet:** `typesafe/jev-latest`.

**Good to know:**
- Want this for free? MultiProxy can run its own small decision model right on your hardware. Check out **Quick decisions** to see how!
- Don't have a key? No problem! One of your other AIs will just answer those decisions instead.
- There is no Quick Test button for Jev, because it isn't meant for chatting.
- ⚠️ Needs SkyrimNet Beta 26 rc2 or newer. In SkyrimNet, add a second custom provider just for decisions: set its **API Style** to **System One**, give it MultiProxy's address ending in `/v1/systemone` (like `http://127.0.0.1:8000/v1/systemone`), and type any word into its **API Key** box. SkyrimNet won't send decisions to a provider with an empty key; MultiProxy ignores whatever you type there. Then pick that provider on the **Decisions** card under AI > Models.

### Perplexity

**What it is:** A fast cloud AI from Perplexity. It answers the quick background questions SkyrimNet asks while you play, like an NPC's mood, who speaks next, or who you are talking to. It never writes dialogue.

**What you need:** A Perplexity API key. You can get one at console.perplexity.ai. It starts with `pplx-`.

**Set it up:**
1. Sign up at console.perplexity.ai and create an API key.
2. On the **Providers** tab, open **Perplexity**, paste your key and press **Save**.
3. Optional: On the **Decisions** tab, drag Perplexity to the top of **Who answers decisions**.

**Use it in SkyrimNet:** There is nothing new to type. Perplexity answers the decisions SkyrimNet already sends to MultiProxy. See **Quick decisions** to learn how to add it.

**Good to know:**
- The standard model costs $0.02 per million input tokens, and nothing for the answers. The **Cheaper and faster** choice on the **Decisions** tab costs $0.01 per million input tokens.
- Decisions run in this order: your own local decision model, TypeSafe, Perplexity, then Nano-GPT. If one fails, the next one tries.
- It only answers decisions. It never appears in your chat model lists, in **Routing**, or in **Quick Test**.
- If Perplexity rejects your key, the card will tell you. Paste the key again or make a new one.
- You need SkyrimNet Beta 26 rc2 or newer for decisions.

### Cloudflare Workers AI

**What it is:** Cloudflare's AI service. You get a small free allowance every day, and it starts over at midnight (UTC). It is handy for trying MultiProxy out, or as a spare.

**What you need:** A Cloudflare account. It's free.

**Set it up:**
1. Open dash.cloudflare.com, go to **Workers AI**, and press **Use REST API**.
2. Press **Create a Workers AI API Token**, then **Create API Token**, and copy the token. Your **Account ID** is on the same page.
3. Paste your Account ID, a colon (`:`) and the token into the box, with no spaces, and press **Save**.

**Use it in SkyrimNet:** `cloudflare/@cf/MAKER/MODEL`, like `cloudflare/@cf/qwen/qwen3-30b-a3b-fp8`.

**Good to know:**
- The allowance is roughly 70 to 400 replies a day, depending on the model. The note beside each model says about how many. It will not last a long, busy session.
- When it runs out, Cloudflare says no until the next day, and another AI can answer instead.
- Some of Cloudflare's biggest models need its paid plan, and say no on the free one.

### Mistral

**What it is:** Mistral's AI service. The free plan has a small allowance, so it is handy for trying MultiProxy out, or as a spare.

**What you need:** A Mistral account. The free plan may ask you for a phone number.

**Set it up:**
1. Open console.mistral.ai, go to **API Keys**, and make a key.
2. Paste it in and press **Save**.
3. Press **Check for models**. The panel shows the models your key can use today.

**Use it in SkyrimNet:** `mistral/MODEL`, like `mistral/mistral-small-2603`.

**Good to know:**
- ⚠️ On the free plan, Mistral may use what you send to train its AI. Your NPCs' conversations are part of that. You can switch it off in Mistral's settings, under Privacy.
- The free plan is slow to take many requests at once. MultiProxy sends two at a time and the rest wait in line (you can change that in `multiproxy.ini`).

### Groq

**What it is:** Groq's very fast AI service. The free plan is very small, so it is handy for trying MultiProxy out, but not as your main AI.

**What you need:** A Groq account.

**Set it up:**
1. Open console.groq.com/keys and make a key.
2. Paste it in and press **Save**.
3. Press **Check for models**. The panel shows the models your key can use today.

**Use it in SkyrimNet:** `groq/MODEL`, like `groq/openai/gpt-oss-20b`.

**Good to know:**
- The free plan only allows a few replies a minute, so a busy scene runs out within minutes.
- Groq retires models often. **Check for models** shows what works today.

---

## YOUR OWN HARDWARE (FREE)

### Local device

**What it is:** AI models running entirely on your own device's graphics card. MultiProxy handles everything: it downloads the open-source engine (llama.cpp) and the models for you. It's totally free, private, and doesn't even need the internet once everything is downloaded!

**What you need:** A graphics card with enough memory. Skyrim also needs a lot of graphics memory to run smoothly, which is why we included a fit check.

**Set it up** (on the **Local device** tab):
1. **Set up local models.** MultiProxy looks at your graphics card and picks the best version of the **Model engine (llama.cpp)** for it (the open-source program that runs the models). Just press Download. When a newer version is out, a line says so, with an **Update llama.cpp** button.
2. **Pick where models are saved.** These files are huge (up to 20 GB each!), so make sure you pick a hard drive with plenty of space.
3. **Pick a model** and press Download. A yellow dot means the model is a tight squeeze or too big to run alongside Skyrim; hover your mouse over the dot to see why.
4. That's it! Models automatically wake up when MultiProxy starts and go to sleep when it closes.

**Use it in SkyrimNet:** `local/MODEL`, like `local/gemma-4-12b`. Each row has a handy **Copy** button.

**Good to know:**
- **Time left and Pause:** Every download shows a **Pause** button and tells you how much time is left once it calculates the speed. A paused download keeps its progress, even if you close MultiProxy. Press **Resume** to carry on.
- **Graphics memory:** At the top of the tab, it shows exactly what Skyrim, your models, and your PC are using right now. Below the model list, **Skyrim's peak memory** shows the highest amount of graphics memory the game has used on this PC. It forgets readings older than two weeks. Press **Reset tracker** to forget it immediately and measure again while you play. MultiProxy uses this peak to ensure the game always has enough room.
- If a model is too big for your main device, it will still download perfectly fine. This is great if you want to move it to a spare computer (see **Other devices** below).
- Pressing **Stop** immediately frees up your memory until you press Start again.
- **If graphics memory gets nearly full while Skyrim runs,** MultiProxy stops its own chat model to protect the game. See **The memory guard**.
- **How models run on this device** has two columns, with related settings side by side. **Memory to keep free for the game** tells llama.cpp how much graphics memory to leave for Skyrim when it fits a model onto the card: bigger is safer for the game, smaller lets the model use more. It is worked out from the most memory Skyrim has used on this device, a device with no game keeps 1 GB, and it only applies while **Graphics card layers** is on Automatic (it greys out when you pick a layer count). **Memory per conversation** is shared by **Requests at the same time**, so each request gets its share of the pages shown under it. **Smaller cache** (q8_0) halves the cache memory with almost no loss in quality, and **Speed boost for long prompts** stays on while it is used, because turning it off with the smaller cache would stop the model from starting.
- Already downloaded your own model file from the internet? Click **Add it here…** under the model list to use it exactly where it sits. Model files like this end in `.gguf`; if the model sees pictures, it also needs its matching vision file (the `mmproj` file).

### Set me up

**What it is:** The fastest way to get local models running on a new install. **Set me up** (found on **Overview**, the **Get started** list, and the **Local device** tab) checks your graphics card and your game. It shows what fits and installs the items you tick.

**How to use it:**
1. Press **Set me up**. It asks: **Do you play Skyrim on this PC?** Press **Yes** (it will suggest smaller models to leave memory for the game) or **No, another PC**.
2. If you pressed Yes, start Skyrim and press **Measure**. MultiProxy will check how much graphics memory the game uses. If it cannot see the game, press **Continue with an estimate**.
3. Read the table showing **Model**, **Size**, **Fits this PC**, and **Status**. Tick what you want. The voice model (Higgs) is optional and requires a card with tensor cores.
4. Press **Install**. Each download shows the time remaining and can be paused.

**If there isn't enough room next to Skyrim:** An amber line will warn you and offer next steps. If you have a paired PC, press **Open Other devices** to set models up there. If you don't, press **Add a PC**, or **Open Providers** to use a cloud model instead.

**Good to know:**
- Toggling **Don't run models on this PC** (in **Settings**) turns off local model suggestions for this computer. Models you already installed remain on your disk. If any are running, MultiProxy asks before stopping them.
- If you have a cloud key saved, the setup might ask: **You have a key. Run quick decisions in the cloud only?** (using the **Use cloud AI only** button). This does the exact same thing as the **Cloud only** tick box on the **Decisions** tab.

### The memory guard

**What it is:** Skyrim uses more graphics memory when you enter busy areas. If your card fills up while MultiProxy's local chat model is loaded, the game can crash. The memory guard watches for this while you play and stops the chat model first to free up memory.

**What you will see:**
- On the **Local device** tab, the model's row shows: "Stopped <model>: Skyrim's graphics memory was nearly full." This line also appears on **Overview** with a **Start** button.
- Characters keep talking if their job has a backup on the **Routing** tab, like a cloud model. A job with no backup shows the message "<model> is stopped" until you start the model again.

**What it stops, and what it leaves alone:**
- It stops local chat models on the graphics card running low on memory. If you have two cards, it only affects models on the full card.
- It never touches the quick-decisions model, the voice, Ollama, LM Studio, or models on another PC.

**When it steps in:**
1. It only watches while Skyrim is running. It reads nothing when the game is closed.
2. It checks free graphics memory every 2 seconds. It acts if free memory stays below 5% (or below 1.5 GB if that is larger) for 5 seconds in a row. Short dips while loading new areas do not count.
3. In practice, it steps in 5 to 7 seconds after memory first runs low.

**What to do next:**
1. Close Skyrim. Then press **Start** on the model, or let it start with MultiProxy.
2. Do not press **Start** while the game is running and memory is tight. The guard will just stop it again after 5 seconds.
3. If it keeps stopping, pick a smaller model or run the model on another PC. Notes on **Set me up** and the **Local device** tab explain which models leave room for the game.
4. Start Skyrim before you click **Set me up**. This lets MultiProxy measure how much memory the game actually uses.

**Turn it off:** On the **Settings** page, under **Starting and stopping**, switch off **Protect Skyrim's graphics memory**. This is on by default. When turned off, MultiProxy never reads your graphics memory and never stops a model on its own.

**Good to know:**
- When the guard acts, MultiProxy logs a line in `graphics-guard-events.jsonl` (found next to `config.json`). This records the time, the model, your free memory, and your card's total memory.
- It catches memory that fills up gradually. A sudden spike that fills the card in under 2 seconds can still bypass the guard and crash the game.
- If graphics memory fills up, Windows sometimes moves data to your regular system memory. This makes Skyrim slow down instead of crashing. The guard still stops the model to be safe.
- It only reads NVIDIA cards. It does nothing on an AMD or Intel card.

### Quick decisions

**What it is:** A small, incredibly fast AI that answers the little questions SkyrimNet asks constantly while you play: what mood is this NPC in, who speaks next, or are they talking to you? It does the same job as TypeSafe's Jev without needing a key. It runs entirely on your own hardware for free, keeps everything private, and never writes dialogue. You set it up on the **Decisions** tab.

**What you need:** About 2.8 GB of disk space and roughly 4 GB of graphics memory to spare. On a graphics card, it gives a yes/no answer in about 40 thousandths of a second! No graphics card to spare? It can run on ordinary system memory instead. That takes about a second per question, which is perfectly fine for background decisions.

**Set it up** (on the **Decisions** tab):
1. Find the device you want in **Where decisions run**: this device, or any device running the MultiProxy Courier.
2. Press **Set it up** on its row and wait for the download. This only happens once: your other devices will just copy the files from this one.
3. When the dot turns green, it is ready to go! Decisions switch to that device by themselves once it is ready.

**Use it in SkyrimNet:** You will need SkyrimNet Beta 26 rc2 or newer. Under **AI > Providers**, add a custom provider named `MultiProxy Decisions`. Paste in MultiProxy's address ending in `/v1/systemone`, set the **API Style** to **System One**, and type the word `proxy` in the **API Key** box (SkyrimNet just needs something there; MultiProxy ignores it). Then under **AI > Models**, go to the **Decisions** card, pick your new provider, and paste in `local/decisions`. The MultiProxy card has a handy **Copy** button for each of these!

**Good to know:**
- By default, **Run on** is set to Automatic. This means it uses your graphics card only when there is room to spare, switching to ordinary memory so it never crowds your game. You can also pin it directly to your Graphics card or System memory.
- **Who answers decisions:** A numbered list at the bottom of the tab shows the local model, TypeSafe, Perplexity, Nano-GPT, and any other provider that answers decisions. **Drag a row by its handle** to change the order. The first one ready answers. A service missing a key stays in the list as **No key**, with an **Add a key** link so you know why it was skipped.
- **Fallback limits:** Under the list, a tick box says: **Try the next model down if the current one takes longer than 6 seconds or is less than 60 % sure.** You can change both numbers. Only local decision models can report how sure they are; cloud models only use the timer. If a model is down or refuses to answer, it always hands over, regardless of this box.
- **Cloud only:** Tick this if your PC lacks the graphics memory. It ensures no PC starts a local decision model, routing all decisions to your cloud key. You must save a key first (it says **Add a key first.** if you have none). Untick it to return to normal.
- **Name warnings:** If SkyrimNet's **Decisions** setting uses a different model name than the one MultiProxy expects, an amber line warns you and shows the correct SkyrimNet steps. This only appears when decisions are set up on this PC.
- **Stopping old models:** If a decision model is still running on a PC after you route decisions elsewhere, its row says **Still running, not in use** and gives you a **Shut down** button. Moving decisions or the voice to a new PC automatically stops the copy on the old PC.
- You can choose between three models: **Kev-4B** is the recommended one, accurate and fast. **Lev** is an alternative to Kev-4B that is a little lighter on the graphics card. **Laya (small)** is for devices with little or no graphics memory: much smaller and quicker, but it gets noticeably more questions wrong, most of all with long scenes, and it is English only.
- Want to move it to another device later? Press **Set it up** on that device's row, or **Use it** if it is already installed there. SkyrimNet's address stays exactly the same wherever the model runs, and nothing is uninstalled by moving.
- **Waiting limit** (in the ⋯ menu): the local model gets a set time to answer each decision. The usual is 0.8 seconds on a graphics card, or 6 seconds on system memory. A slower device might need more time. If it runs out, MultiProxy skips the local model and your next available provider answers instead. **Use the usual** puts it back.
- The **API Key** box in SkyrimNet needs a word, but MultiProxy never checks it as a password. The address you paste is always this device; MultiProxy passes each question on to whichever device runs the model, so moving it never touches your SkyrimNet settings.
- A few very large questions (more than 255 options) can't be answered by the local model, so MultiProxy quietly passes those on to your next provider.
- **Uninstall** (in the ⋯ menu) removes the model but keeps the downloaded files, so installing it again later is quick. Your other providers are not affected.
- Built on open decision models: **Kev-4B** and **Lev** (built on Qwen) and **Laya** (built on ModernBERT).

### Ollama

**What it is:** A popular, free app that runs AI models on your device. MultiProxy will happily talk to it if you have it running.

**What you need:** The Ollama app, available at ollama.com/download.

**Set it up:** Install Ollama, open your terminal and download a model (for example, type `ollama pull gemma4:12b`), and then press **Check for models** on the Ollama panel in our dashboard.

**Use it in SkyrimNet:** `ollama/MODEL`, like `ollama/gemma4:12b`.

**Good to know:**
- **Ollama Cloud:** If you paste an Ollama Cloud key in, this provider will use Ollama's cloud models instead of your local ones. You can only use one or the other here, not both at the same time.
- Running Ollama on a different computer? Use our **Other devices** tab instead!

### LM Studio

**What it is:** A great free app that lets you easily browse, download, and run models on your device.

**What you need:** LM Studio, from lmstudio.ai/download.

**Set it up:** Install it, use it to download a model, and start its local server. (MultiProxy can even start the server for you!)

**Technical details:** LM Studio's server is at `localhost:1234` unless you change it. The address box on the Providers tab only needs touching if LM Studio runs on another device; **Use the default** puts it back. The **Start LM Studio's server** button needs LM Studio's command-line tool: in LM Studio, open Settings and click **Install CLI** once.

**Use it in SkyrimNet:** `lmstudio/MODEL`, like `lmstudio/qwen/qwen3-4b`.

**Good to know:** Running LM Studio on a different computer? Use our **Other devices** tab instead!

### Other devices

**What it is:** This lets you run the AI on a totally different computer in your house (like a spare desktop or a laptop). This is brilliant because it lets your main device keep all its graphics power purely for playing Skyrim! You just put a tiny helper program called the **MultiProxy Courier** on the second computer, and then you can manage everything right from your main device.

**What you need:** Another device connected to your home network. It can already have Ollama or LM Studio installed, or it can be completely blank—the Courier brings its own engine.

**Installing the Courier with its installer:**
1. Double-click the Courier installer.
2. Page 1: press Next (choose where models go only if your main drive is small).
3. Page 2: leave all three ticks on and press Install.
4. When Windows asks for permission to let the Courier through the firewall, press Yes.
5. Read the pairing code in the window that opens.
6. On your main device: MultiProxy > Other devices > find this device > type the code > Pair.

**Add a PC (the quick way):** Press **Add a PC** on the **Other devices** tab. It gives you one web address. Open that address on your spare PC and download the Courier from that page. The Courier pairs itself, and your main PC will ask: **<NAME> wants to join.** Press **Add** to accept, or **No**. The address works once, lasts 15 minutes, and only works on your home network. To update the Courier later, press **Update Courier**.

**Set it up:**
1. On the **Other devices** tab, press **Windows** under **Add a device**, which copies the download link. Go to your second device, open a web browser, and paste that link in. (If the second device runs Linux, press **Linux** instead, which copies the install command).
2. On the second device, double-click **Set up this PC**. If Windows asks to allow it on your home network, say yes!
3. The second device will show a **pairing code**. Come back to your main device, look under **Found on your network**, type that code in, and press **Pair**.

**Use it in SkyrimNet:** `NICKNAME/MODEL`, like `laptop/gemma-4-12b`.

**What you can do from your main device:**
- Watch the other device's graphics card, memory, temperature, and hard drive space in real-time.
- Load, unload, and download new models. You can even start Ollama or LM Studio remotely if they are closed!
- Delete models to free up space (Note: you can delete Ollama and MultiProxy models here, but LM Studio makes you delete its models from inside the LM Studio app).
- Keep the other device awake, or generate a new pairing code.
- **Remote model engine:** Press **Show** on a device's **Model engine (llama.cpp)** row to open screens just like the Local device tab, but for that specific spare PC. You can update the engine, pick and download models (the fit dots look at *that* device's graphics card), start, stop, and remove models. You can also **Copy** a model from your main PC if it is faster, or press **Add it here…** to load a model file already on that spare PC.

**Good to know:**
- **Status dots:** A green dot means the model is loaded. A yellow dot means the model did not fit entirely on the other device's graphics card and is using slower system memory. A grey dot means it is not loaded or running (nothing is wrong). Red is only used for real problems.
- **A program you uninstalled** (Ollama or LM Studio) reads **Not installed** with a **Hide** button. **Show hidden programs** (next to **Log**) brings it back, and it comes back by itself once it is installed again. A Courier older than 1.11 cannot say; update the Courier.
- 🔋 **Important:** A laptop running on battery power is drastically slower. Always plug it into the wall!
- ⚠️ Closing a laptop's lid usually puts it to sleep, which kills the AI. You need to go into Windows' power settings on the laptop and set "When I close the lid" to "Do nothing".
- For your security, only devices on your own private home network can connect, and only if they have the pairing code. See [**Network and ports**](#help/network) for the details.
- **Technical details:**
  - Addresses must be on your home network (192.168.x.x, 10.x.x.x or 172.16 to 172.31.x.x) or on a Tailscale link (100.64.x.x to 100.127.x.x). A device added by hand can use its address and port, like 192.168.1.50 or 192.168.1.60:8080 (the Courier listens on port 8110).
  - If an AI program runs on your main device, give it a port other than the one MultiProxy uses itself.
  - The **Add a model running elsewhere (Advanced)** option works with LM Studio, Ollama and llama.cpp (llama-server), and with LiteLLM, NVIDIA NIM and vLLM. It only lists their models here. To let a llama.cpp server see pictures, start it with its vision file (the `--mmproj` option).
  - The Courier finds LM Studio, Ollama or llama.cpp by itself, or uses MultiProxy's own model engine if there is none. If LM Studio's command-line tool is missing, open LM Studio once on that device and turn on its command-line tool in LM Studio's settings.
  - If a program won't stop, close it on that device. If nothing helps, restart that device.
  - The backup covers every device on this tab and checks again every 30 seconds.
  - Setting up without questions (scripts, or several devices). Windows: `"Set up this PC.bat" -Yes` keeps the suggested folder and answers yes to everything; `"Set up this PC.bat" -InstallDir "D:\Courier"` installs there without asking; `-NoAutoStart`, `-NoFirewall` and `-NoStart` are there too. Linux: `bash install-courier.sh --yes` keeps the suggested folder, `--dir /opt/courier` installs there without asking, `--no-service` skips starting with the computer.
  - On Linux, to keep the Courier running after you log out, run `sudo loginctl enable-linger YOURNAME` once (the installer tries this for you).
  - The Courier's pairing code window shows the address if you need to add the device by hand. Windows asks the first time the Courier starts: allow it on private networks.
- **No Courier?** If you don't want to install the Courier, you can still link an external Ollama, LM Studio, or llama.cpp setup by typing in its network address. MultiProxy will list its models, but you won't be able to manage the device remotely.

---

### Share a setup and Check everything

**Share a setup:** Pressing **Save my setup** writes your routes, backups, and fine-tune choices to a small `.mpsetup` file (named like `multiproxy_2026-10-26.mpsetup`). **Load a setup** reads it back. This file contains no keys, tokens, web addresses, or file paths. A route pointing to another PC is saved only by its job name and marked "needs setting up here". Before loading, MultiProxy shows you what will change and skips any lines your PC cannot use. It automatically backs up your settings first, so you can undo the last load.

**Check everything** (a button on **Overview**): Runs a short list of checks and only shows what is wrong, along with instructions to fix it. It might warn you if another program is using MultiProxy's port, or if a paired PC cannot be reached. It stays quiet when everything is fine, and it never changes Windows settings for you.

### Models

**What it is:** A list of every model downloaded on your PCs. It shows file size, where each model is used, and if it is running. You can start, stop, remove, or delete models here.

**What you need:** Nothing. The tab is always available. A list appears for each PC you have paired on **Other devices**. Pick a PC at the top.

**How to use it:**
1. Open the **Models** tab. Select a PC at the top if you have more than one.
2. Find your model. **Used by** shows which jobs rely on it, such as Dialogue or Memory. "Not used" means no jobs need it.
3. Use the buttons on its row.

**The buttons:**
- **Start** and **Stop** load or unload the model.
- **Remove** takes the model out of MultiProxy. A pop-up asks whether to keep or delete its files. Keeping the files lets you add the model back or copy it to another PC without downloading it again.
- **Add** puts a kept model marked **On this PC** back into MultiProxy immediately. The program checks the files first.
- **Delete** deletes the files of a model marked **On this PC** from your computer.

**Good to know:**
- If jobs use a model, the pop-up shows **In use**. Hover over it to see which jobs.
- If you added a model file yourself, removing it only takes it off the list. Your file stays where it is.
- **Kept files and leftovers:** Quick decisions and voice models appear here too, alongside the model engines they need. Removing one works like any other model: you choose whether to keep its files. A kept model says **On this PC**, and pressing **Add** restores it without downloading. A leftover file with no owner says **Leftover download (file name)** and **Kept on this PC**, with a **Delete** button. Unused **Model engine (llama.cpp)** rows show **Kept on this PC** and can be deleted.
- Another PC's list is read-only unless that PC can load and unload models.
- Below the list, you can open your models folder and check your used and free drive space.

## VOICE

### Higgs voice

**What it is:** A voice engine that speaks your NPCs' lines out loud, running on your own hardware. It copies each voice from a short sample clip, so Lydia still sounds exactly like Lydia! MultiProxy handles downloading and running it, then hands SkyrimNet a single address to use. It is totally free, and nothing ever leaves your devices.

**What you need:** An NVIDIA graphics card (RTX 20 series or newer) with about 5.5 GB of graphics memory to spare, about 6 GB of ordinary memory, and about 6.2 GB of disk space. That is a lot to ask of a card that is already running Skyrim, which is why you can easily put it on another device instead.

**Set it up** (on the **Voice** tab, in three steps: **Setup**, **Download**, **Install**):
1. **Setup:** Choose a voice and which device will run it, tick the license box, and click **Next: Download**. You'll see options for this device or a remote device, and the free Boson AI license box to tick. (You are asked once: it covers every device you install Higgs on.)
2. **Download:** Wait for the big voice file to save to your main device (you can leave this page!). You'll see a progress bar tracking the local download that moves to the next step all by itself.
3. **Install:** Everything sets itself up automatically and gives you an address to copy when ready. You'll see a message saying the device is set up to run Higgs, plus the address and a copy icon.
4. **SkyrimNet:** When the voice is ready, its card shows the steps for SkyrimNet, written for the voice you chose. They end with **Save Changes** and **Test Voice**.

**Use it in SkyrimNet:** The card shows numbered steps for the voice you chose, in SkyrimNet's own words. In SkyrimNet, open **Voice** > **Text-to-Speech**, then:
- **Higgs voice:** set **TTS Engine** to **Higgs**, open the **Higgs** tab, paste the address into **Endpoint**, check that **tts.model** says `higgs`, and turn **Enable Audio Tags** on.
- **Qwen3-TTS voice** or **Chatterbox voice:** set **TTS Engine** to **OpenAI-Compatible**, open the **OpenAI-Compatible** tab, set **Service** to **Self-hosted**, paste the address into **Endpoint**, type `qwen3tts` (or `chatterbox`) into **Model**, and leave **API Key** empty.

Then press **Save Changes** (the **Test Voice** button stays grey until you do), and press **Test Voice**. The address always points at this main device, even when the voice is running on another one, so you can move it later without ever touching SkyrimNet.

**Your own voice clips:** SkyrimNet sends Higgs a low-quality sample for each voice. (The first sample sent is kept and reused so the character's voice stays exactly the same). For a much better result, give any voice a clear clip of your own: press **Add a clip…** on the **Voice** tab, or name a WAV file after the voice (like `femalenord.wav`) and drop it into the clips folder. A clear, clean recording of 8 to 15 seconds works best.

**Good to know:**
- Pressing **Stop** frees your graphics memory at once. Turning on **Start when MultiProxy starts** in the ⋯ menu brings Higgs up whenever MultiProxy starts.
- **Move to another device** in the ⋯ menu seamlessly takes Higgs, and all your custom voice clips, to another device. SkyrimNet's address stays exactly the same.
- Removing your custom clip just goes back to using SkyrimNet's original sample. A replaced or removed clip is always kept, never deleted.
- Only WAV files work as clips for now.
- The license allows personal, non-commercial use, and it does not allow copying a real person's voice without their consent.
- Higgs needs about 5.5 GB of graphics memory (6.1 GB on the Vulkan build), 6 GB of ordinary memory and 6.2 GB of disk space. While it runs, the card's memory use shows in the **Voice** tab's technical details.
- SkyrimNet's address is always this device. MultiProxy passes each line to wherever Higgs is running, so you can move it later without touching your SkyrimNet settings.
- If Higgs stops unexpectedly, open **Log** on the Voice tab: the technical details show the exit code. A replaced clip is kept, and removing your clip goes back to SkyrimNet's.
- If a device's Courier is out of date, download the newest MultiProxy Courier installer on that device and run it (**Set up this PC**), then come back.
- If Higgs stops right after starting, your antivirus or Windows' Smart App Control may have blocked it. Add its folder to the allowed list and try again.

### Speech to text

**What it is:** Lets you talk to your NPCs with SkyrimNet's push-to-talk key. MultiProxy sends a short recording of your voice to xAI, which turns it into text, and hands the text back to SkyrimNet. Your graphics card stays free for Skyrim and the voices.

**What you need:** An xAI key with some credit on the account, saved on the **Providers** tab (see **xAI (API key)**), and SkyrimNet's speech settings.

**Your voice leaves your PC.** That is why this is off until you turn it on. xAI's terms apply.

**Set it up** (on the **Voice** tab, in the **Speech to text** card):
1. If you haven't yet, add your xAI key on the **Providers** tab and press Save.
2. On the **Voice** tab, turn on **Turn my voice into text with xAI**, and press **Turn on** when it asks.
3. Press the copy icon next to the address on the card.

**Use it in SkyrimNet:** open the speech settings, set the speech engine to **External Whisper**, paste the address into the address box (it ends in `/v1/audio/transcriptions`: keep that ending), then press your push-to-talk key and speak.

**Good to know:**
- It is off until you turn it on, and it needs your xAI key. Without a key the switch stays grey.
- You pay xAI for what you use. xAI's own figure is about ten cents for an hour of speech, so a few spoken seconds cost a tiny fraction of a cent. Check xAI's site for the current price.
- The card shows when the last request happened and how long it took. If it says "No requests yet", SkyrimNet has not sent anything: check the address.
- A recording can be up to 60 seconds, and up to 30 are accepted a minute, so a stuck program cannot run up your bill.
- If SkyrimNet says **"xAI didn't accept the key"**, check the key on the **Providers** tab. If it says **"xAI didn't answer in time"**, just try again.
- If SkyrimNet says **"Speech to text is off"**, turn the switch on in MultiProxy.
- To stop sending your voice to xAI, turn the switch off. SkyrimNet then gets the "Speech to text is off" answer.
- SkyrimNet has to be pointed at the address exactly as shown. Without the ending (`/v1/audio/transcriptions`) it can't find MultiProxy.

---

## TOOLS

### Quick Test

Want to try an AI before you trust it with your Skyrim playthrough? Pick any ready provider and model, choose one of our ready-made Skyrim test scenes (like chatting with Lydia, Belethor, or Paarthurnax), or just write your own prompt, and press **Send**. A chat window will pop open so you can have a full conversation to test its speed and personality.

**The Background box** holds the character's instructions (the sample scenario fills it in). **Additional context** is optional: it is added to those instructions before sending, so you can test with memories or recent events, the same shape a real SkyrimNet request carries.

**If the chat window shows an error:**
- "Not installed" or "not signed in" for Claude, Codex or Google Antigravity: open that provider's page in Help and follow its install steps.
- "No ... key found": add the key on the provider's card on the Providers tab.
- "Couldn't reach ...": the program or device is not running, or its address is wrong. Start it and try again.
- Anything else: the message comes straight from the provider. Look the exact words up on the provider's own site, or ask on the issue page (see **Trouble?**).

### Routing

**What it is:** MultiProxy's way of directing traffic. SkyrimNet sends many AI requests at the exact same time, asking for different kinds of work like Dialogue, Combat, or Memory. Routing lets you choose exactly which model answers each job, so you can mix and match your cloud subscriptions with free local models.

**What you need:** Just SkyrimNet and MultiProxy, plus a few models ready to go on your providers (like a cloud service, this device, or another device using the MultiProxy Courier).

**Set it up:**
1. Open the **Routing** tab. You will see the models you have set up, grouped by where they run: This device, each other device, Cloud, or **Set in SkyrimNet**. A box called **Needs attention** appears only when something is broken, and **Jobs with no model yet** lists jobs SkyrimNet asked for by name that have no model of their own.
2. Find the job you want to route. Jobs look like little chips.
3. Drag the chip onto the model you want to answer it.
4. To add a backup, click the chip and press **+ Add a model**. The chips are numbered: **1** answers first, and **2** steps in if the first one fails or is busy.
5. A chip that says **7 background jobs** (or any number) is a group of jobs that SkyrimNet add-ons bring, like companion impulses or relationship tracking. MultiProxy can't tell them apart, so they move together. Hover the chip to see each job and which add-on it comes from. Each one is set on SkyrimNet's own **Models** page.

**Use it in SkyrimNet:**
There are two ways MultiProxy knows which job is which:
- **Option 1 (Automatic):** Do nothing at all! MultiProxy recognises the job just from looking at the request. This is nearly always right. If it has to guess, it marks the job with "Looks like".
- **Option 2 (Exact names):** Use this to stop MultiProxy from guessing, or to tell apart jobs that look the same.
  1. Open SkyrimNet and go to **AI > Models**.
  2. Choose MultiProxy as the provider.
  3. Type the exact name (like `job/dialogue`) into the model box.
  4. If this job does not share a route with anything else, it won't get an answer yet. Open MultiProxy's Routing tab and drag the job onto a model to connect it.

**Good to know:**
- **Click a job** chip to open it. You will see its backups in order and can change them: **Make first choice**, **Move up**, **Move down**, **Remove**, or **+ Add a model**.
- You can choose how MultiProxy picks among your backups. **In this order** is the default. **Take turns** swaps between them. **Fastest right now** spreads the load based on recent speed. **Free ones first** tries local models and other devices, then subscriptions, then pay-as-you-go. **Cheapest first** uses the cost the provider reports, putting unknown costs last.
- **When one doesn't answer:** A failure, a "busy" signal, or an empty reply passes the line to the next model at once. A reply that has already started is never switched.
- Passing a job on because a model is slow is off by default. Big cloud models often need 5 seconds or more, and SkyrimNet already tries again if it waits too long. You can turn this on for each job if you need it.
- A model that fails 3 times in a row within a minute rests for 2 minutes (you can change both numbers). A slow local model is never rested for being slow, and the last model in a list is always tried.
- **Fine-tune this job:** You can override settings like temperature, longest reply, or thinking for this job only. Each setting shows the value SkyrimNet last sent for that job, and one click puts it back to following SkyrimNet.
- **Priority when busy:** Because SkyrimNet sends many requests at once, the game's own jobs go first when a model is busy. Press **Change the order** to see the chips and drag to reorder them. A waiting job moves up one place every 5 seconds so nothing waits forever, and a job currently being written is never cut off.
- **Needs attention:** This section only shows real problems: a model its provider retired (with a button to switch to the new one), a missing key or sign-in, or a provider that is down. **Set in SkyrimNet** models appear grey: SkyrimNet sends these to a provider MultiProxy does not carry, so MultiProxy cannot test or count them. **Decisions** do not appear here; they follow the separate list on the **Decisions** tab.
- **Live:** This panel stays closed until you open it. It shows each request as it happens: the job, which model answered, where it is, any hand-over, wait times, the first word, total time, tokens, and cost. For models that share it, you can even read their reasoning as it is written. Nothing here is saved.
- Click **Go back to SkyrimNet's model name** to undo a job's route. The job will just go wherever its model name in SkyrimNet points.
- Some jobs come from add-ons. SkyrimNet marks them with a puzzle piece on its Models page, and they show up here by their raw names, like `intel_story_dm`.
- The automatic option cannot tell `narrative_engine_composer` and `narrative_engine_director` apart, so they move together on the screen unless you use exact names in SkyrimNet.
- **Which graphics card:** If you have a device with two real graphics cards, look in Settings. You can tell each local model, the decision model, and Higgs voice to stay on a chosen card. MultiProxy remembers which card each one uses, so a restart or a driver update does not move it. Automatic is the default.
- On the Speeds tab, check **Time breakdown** to see the breakdown for each job and model: waiting in line, reading the prompt, writing, speed, tokens and cost.

### Speeds

This tab shows you exactly how fast and reliable each AI has been while you were actually playing the game. You can see how many seconds it takes an NPC to start talking, how long the whole reply takes, and exactly what went wrong if an AI failed to answer. You can filter the data by model, by job (dialogue, vision, memory, etc.), by time, or by where the AI ran (your device, a spare device, or the cloud).

Each job's card shows a short plain line for how fast it needs to be. If you want to compare with other players, SkyrimNet has its own community stats page at fateless.ai/stats. SkyrimNet can also share anonymous speed stats with that page; that is your choice, and it lives in SkyrimNet's settings under **Settings > General > Privacy**.

The **Time breakdown** view shows, for each job and model, a typical line split into waiting in line, reading the prompt and writing, plus the writing speed, tokens and cost where the model reports them. [Routing](#help/routing) explains the jobs.

When a model card says some replies failed, the card lists why (too busy, took too long, server errors, login problems, broken replies). MultiProxy also repairs or retries broken replies on its own (Settings, under **Fixing provider replies**).

### Network and ports

For the curious and for anyone setting up a firewall. Most people never need this page: on a single device, nothing has to be opened at all.

**On your main device**

Nothing on your main device listens to your home network, so its firewall needs no changes. Everything here stays strictly on this one machine.

| Port | Type | Who can reach it | What it's for |
| :--- | :--- | :--- | :--- |
| 8000 | TCP (HTTP) | This device only | The address SkyrimNet talks to, and this dashboard. |
| 9999 | TCP (HTTP) | This device only | Used internally by the Claude subscription. |
| 18080 to 18180 | TCP (HTTP) | This device only | Local device: each loaded model gets one port from this range. |
| 1234 | TCP (HTTP) | This device only | LM Studio's own server, if you use it. |
| 11434 | TCP (HTTP) | This device only | Ollama's own server, if you use it. |
| 18900 to 18949 | TCP (HTTP) | This device only | The local decision model: it takes one port from this range when it runs on this device. |
| 18950 to 18999 | TCP (HTTP) | This device only | Higgs voice: the engine takes one port from this range when it runs on this device. |

**On another device running the Courier**

| Port | Type | Who can reach it | What it's for |
| :--- | :--- | :--- | :--- |
| 8110 | TCP (HTTP and WebSocket) | Your home network | Everything your main device asks the Courier to do, plus the live status connection. Secured by your pairing code. |
| 8099 | UDP | Your home network | Lets your main device find the Courier automatically. |
| 18080 to 18180 | TCP (HTTP) | That device only | The Courier's own local models (the same range as on your main device). |
| 18900 to 18949 | TCP (HTTP) | That device only | The local decision model, if you run it there (the same range as on your main device). |
| 18950 to 18999 | TCP (HTTP) | That device only | Higgs voice, if you run it there (the same range as on your main device). |

- The Courier's setup offers to add the first two firewall rules for you for **private networks only**. If you declined, you must open TCP 8110 by hand. If you also leave UDP 8099 blocked, automatic discovery fails, but you can bypass it by typing the other device's address on the **Other devices** tab.
- Higgs voice opens nothing new on the other device: your main device reaches it through the Courier's one door (8110), and the engine itself stays private to that device. SkyrimNet always talks to the same address on your main device (8000), wherever Higgs runs.
- The local decision model opens nothing new on the other device either: your main device reaches it through the Courier's one door (8110), and the model itself stays private to that device. SkyrimNet still talks to the same address on your main device, wherever the model runs.
- LM Studio and Ollama on that device stay private to it. The Courier passes requests along internally, so their ports do not need opening.

**What goes out**

- To cloud AI providers: HTTPS (TCP 443).
- To your other devices: TCP 8110, and a UDP 8099 broadcast on your home network to find them.

**Good to know**

- You never need to forward a port on your router because everything stays inside your home network.
- ⚠️ Don't open any of these ports to the internet.
- **No Courier?** If you link another device's Ollama, LM Studio or llama.cpp directly by address, that program's own port has to be open on that device (11434 for Ollama, 1234 for LM Studio). That program also has to be set to accept connections from your network.
- **Changed a port?** The tables show the default port numbers.

### Trouble?

- **A provider's dot is grey:** This just means it isn't completely set up yet, or its companion app isn't currently running. Click on its panel to see exactly what is missing!
- **Jobs waiting in line:** Each provider can only handle so many jobs at the same moment (see Settings, under **Advanced: Concurrent jobs**). When a scene is very busy, extra jobs wait their turn. This is completely normal.
- **Skyrim crashed, or the chat model stopped by itself:** See **The memory guard** under **Local device**. If Skyrim crashed with the guard switched off, turn it on.
- **NPCs went quiet:** Hop over to the Speeds tab to see which request failed, and then try running that exact same model in the Quick Test tab to see what the error is.
- **Voice or speech to text isn't working and someone asks what SkyrimNet sends:** 1. Open **Settings** (the ⚙ at the top right). 2. Turn on **Log what SkyrimNet sends**. 3. Do the thing that fails once more in the game. 4. Send the file `multiproxy.log` from MultiProxy's settings folder (the one with your `config.json`; don't send `config.json` itself, it holds your keys). It writes only the names and sizes of what SkyrimNet sent, never your words or the sound, and it turns itself off after an hour.
- **Mod not detected:** This means MultiProxy was started manually. If you install the SkyrimNet MultiProxy mod, Skyrim will start MultiProxy for you. Open **Settings** (the ⚙ at the top right) to see where to get it.
- **Found a bug, or have an idea?** We would love to hear it! Open an issue at github.com/awesmdiver/skyrimnet-multiproxy/issues and tell us what you were doing, what happened, and your MultiProxy version (Settings, under About).

### Glossary

A quick guide to the words you will see around MultiProxy. If a term on your screen is new to you, you will find its simple meaning right here!

**API key (pay-as-you-go key)**: A secret code from a service that charges per use. MultiProxy safely keeps it, and SkyrimNet never sees it.

**API Style**: A setting in SkyrimNet that says what kind of requests a provider takes. It uses Chat Completions for normal models and System One for decisions.

**Backup**: A second model for a job. It jumps in to answer when your first choice fails or is busy.

**Decision model (Quick decisions)**: A tiny, fast model that answers SkyrimNet's quick questions like NPC moods or who speaks next instead of writing dialogue.

**First word**: How long it takes until a reply starts appearing. A fast first word is what makes an NPC feel quick.

**Graphics memory (VRAM)**: The memory on your graphics card. The game, local models, and the voice engine all share it.

**Higgs voice**: The voice engine that speaks NPC lines. It copies each voice from a short voice clip.

**Jev**: TypeSafe's paid decision model. MultiProxy can use it with an API key, or you can run your own free local decision model instead.

**Job**: One of SkyrimNet's kinds of work, like Dialogue, Combat, Memory, Diary Generation, Vision, or jobs added by other mods. Each job goes to a model.

**Local model**: A model running on your own graphics card, downloaded and started by MultiProxy. It is totally free and private.

**Model**: The specific one that answers, identified by its name like claude-sonnet-5-5 or gemma-4-12b. A job goes to a model.

**MultiProxy**: This tool! It is the one address SkyrimNet sends everything to, which passes each request to the model you chose.

**MultiProxy Courier**: The small helper app you install on another device so MultiProxy can use it.

**MultiProxy's address (endpoint)**: The web address SkyrimNet sends requests to, like http://127.0.0.1:8000. You paste it once into SkyrimNet.

**Other devices**: Extra computers in your home that can run models for you.

**Port**: The number after the colon in an address, like 8000. Change it only if something else on your device is using it.

**Provider**: Where models come from. This can be a subscription (Claude, ChatGPT), a pay-as-you-go service (Gemini, DeepSeek, Nano-GPT), a local program (Ollama, LM Studio), this device, or another device.

**Routing**: Choosing which model answers which job. You set this up on the Routing tab; [Routing](#help/routing) in Help explains it.

**Skyrim mod (SkyrimNet MultiProxy mod)**: MultiProxy's small mod that lets it start and stop with the game.

**SkyrimNet**: The Skyrim mod that gives NPCs AI conversations, memories, and behaviour. MultiProxy works for it.

**Subscription**: A monthly plan you already pay for, like Claude, ChatGPT/Codex, or Google. MultiProxy can log in and use it without a key.

**Tokens**: The small pieces text is counted in. Pay-as-you-go services charge by them.

**Voice clip**: A short recording a voice is copied from.
