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
