"""The built-in sample prompts for "Check it like SkyrimNet" (2026-10-25).

Written to look like the game's own jobs (the same kind of system prompt, the same "reply with a JSON object only" ask that MultiProxy already recognises as a job
that wants JSON), but they are made up: they are not SkyrimNet's prompts and they say nothing about the player's game. Plain data, no code, so a sample can be
added or reworded without touching the check. `good` says what a good answer looks like; the check itself only applies the program's existing JSON test to a reply.
"""

# The five requests of "One at a time" (and, repeated, the first of "A busy room"): a plain line of dialogue, a memory (JSON), an action (JSON) and a vision-off
# text. (id, job, system prompt, user message, what a good answer looks like)
CHAT_SAMPLES = [
    {"id": "dialogue-1", "job": "Dialogue",
     "system": "You are Lydia, a housecarl in Whiterun. Stay in character. Reply with one or two short spoken sentences and nothing else.",
     "user": "The player says: Lydia, do you think the road to Riverwood is safe at night?",
     "good": "One or two spoken sentences, in character."},
    {"id": "dialogue-2", "job": "Dialogue",
     "system": "You are Belethor, the shopkeeper of Belethor's General Goods. Stay in character. Reply with one or two short spoken sentences and nothing else.",
     "user": "The player says: I would like to sell you this iron dagger. What will you give me for it?",
     "good": "One or two spoken sentences, in character."},
    {"id": "memory", "job": "Memory",
     "system": "You help an NPC remember what happened. Write one memory of the scene below. Respond ONLY with a JSON object: "
               '{"content": "...", "emotion": "...", "importance_score": 0.0, "tags": []}',
     "user": "Scene: In the Bannered Mare, a bard played until late. Wanda poured ale for the player and a Nord guard. The guard laughed at the player's story about a dragon.",
     "good": "A JSON object with content, emotion, importance_score and tags."},
    {"id": "action", "job": "Action",
     "system": "You choose what an NPC does next. Respond ONLY with a JSON object: "
               '{"action": "Follow | Wait | Attack | Trade | Sit", "target": "...", "reason": "..."}',
     "user": "Scene: Lydia stands beside the player outside Dragonsreach. A traveller walks up and offers to sell a map. Lydia is a follower and the player has just walked away.",
     "good": "A JSON object with an action, a target and a reason."},
    {"id": "vision-off", "job": "Vision (text only)",
     "system": "You describe what an NPC sees in a short sentence. Reply with one plain sentence and nothing else.",
     "user": "Visible: a snowy courtyard, two guards by the gate, a cart of firewood, and a Nord woman carrying a basket.",
     "good": "One plain sentence."},
]

# The ten action prompts of "Actions". Each one is a different scene; every one asks for a JSON object only. (scene, the actions the NPC may pick from)
_ACTION_SCENES = [
    ("Lydia is a follower. The player draws a sword and a bandit rushes out of the trees ten steps away.", "Attack | Defend | Flee | Wait"),
    ("A merchant in Riften waves at the player, who has stopped in front of the stall.", "Trade | Greet | Ignore | Wait"),
    ("The player walks away from a tavern while Lydia is still sitting at a table.", "Follow | Wait | Sit | Stand"),
    ("A guard in Solitude sees the player holding a lockpick beside a locked door at night.", "Warn | Arrest | Ignore | Follow"),
    ("A child in Whiterun asks the player for a coin and holds out both hands.", "Give | Refuse | Talk | Wait"),
    ("The player pulls a lever in a dwarven ruin and a wall slides open behind Lydia.", "Investigate | Wait | Warn | Follow"),
    ("An innkeeper sees two customers arguing loudly over a spilled drink.", "Intervene | Warn | Ignore | Call guard"),
    ("A blacksmith finishes a sword and sees the player standing at the counter.", "Sell | Talk | Wait | Work"),
    ("The player sleeps in a bed at an inn and Lydia is awake in the next room.", "Wait | Guard | Sleep | Follow"),
    ("A dragon lands on the road ahead of the player and Lydia, wings spread.", "Attack | Flee | Take cover | Wait"),
]

ACTION_SAMPLES = [
    {"id": f"action-{i + 1}", "job": "Action",
     "system": "You choose what an NPC does next. Respond ONLY with a JSON object: "
               '{"action": "' + choices + '", "target": "...", "reason": "..."}',
     "user": "Scene: " + scene,
     "good": "A JSON object with an action from the list, a target and a reason."}
    for i, (scene, choices) in enumerate(_ACTION_SCENES)
]

# The three questions of "Check my decision model". Each is a quick decision (a choice among named options). `expected` lists the answers that count as right.
# (id, what the row is called, the scene, the question, the options)
DECISION_SAMPLES = [
    {"id": "kind", "name": "\"Hello there! Are you working?\" (what kind of message?)",
     "state": "A traveller walks up to a blacksmith and says: \"Hello there! Are you working?\"",
     "question": "What kind of message is this: 'greeting', 'question', 'order' or 'threat'?",
     "options": ["greeting", "question", "order", "threat"], "expected": ["greeting"]},
    {"id": "mood", "name": "A heated three-way argument (what mood?)",
     "state": "A guard, an innkeeper and a merchant are shouting over each other in the middle of the tavern. One slams a fist on the table and another points at the door.",
     "question": "What is the mood of this scene: 'gentle', 'tense', 'angry' or 'cheerful'?",
     "options": ["gentle", "tense", "angry", "cheerful"], "expected": ["tense", "angry"]},
    {"id": "next", "name": "Who speaks next?",
     "state": "The player asks a question aloud in the tavern. The guard has just been accused of lying and has his hand on his sword. The innkeeper is polishing a cup and the merchant is counting coins.",
     "question": "Who speaks next: 'the guard', 'the innkeeper' or 'the merchant'?",
     "options": ["the guard", "the innkeeper", "the merchant"], "expected": ["the guard"]},
]
