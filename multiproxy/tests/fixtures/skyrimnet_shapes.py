"""Request-shaped fixtures modeled on the kinds of jobs SkyrimNet actually asks the proxy to do --
built because the Antigravity streaming crash (2026-09-16) was live-verified before release with a
short test prompt, and broke on exactly the difference between that and a real one. Every existing
test in this repo (test_geminicli_provider.py included, before that fix) used small, hand-written
payloads; nothing in the suite could have caught a bug that only shows up past ~32KB.

**All five fixtures below are SYNTHETIC** -- built from what SkyrimNet's own README/dashboard copy
and this proxy's TECHNICAL.md say about what it sends (a system prompt combining character bio +
memories + recent events + world state, plus a short user turn), not captured from a real running
SkyrimNet session. The director doesn't have SkyrimNet running right now. See "Capturing real
fixtures" in TECHNICAL.md for how to replace these with genuine captures once he does -- the shape
(a `Fixture` with the same field names) is designed to be a drop-in swap.

Two fixtures are deliberately oversized past the exact thresholds that broke the Antigravity route:
- `bio_generation`: past 32,767 characters (Windows' CreateProcessW argv-length cap).
- `location_awareness`: past 65,536 bytes (asyncio's default StreamReader readline() buffer).
The other three are realistic-but-more-modest sizes, since not every real SkyrimNet job carries the
full memory/history stack a live dialogue turn does.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fixture:
    name: str
    task: str  # human-readable job label, shown in the runner's table
    system_prompt: str
    user_content: str
    is_synthetic: bool = True
    notes: str = ""

    @property
    def system_chars(self) -> int:
        return len(self.system_prompt)

    @property
    def total_chars(self) -> int:
        return len(self.system_prompt) + len(self.user_content)


def _cycle_to_length(lines: list, target_chars: int, *, numbered: bool = True) -> str:
    """Repeats `lines` (cycling, numbering each repeat) until the joined text is at or past
    target_chars -- the realistic way a real memory/event log actually gets this big: many
    individually-plausible short entries accumulated over play time, not one giant paragraph."""
    out = []
    total = 0
    i = 0
    while total < target_chars:
        line = lines[i % len(lines)]
        cycle = i // len(lines) + 1
        entry = f"{i + 1}. {line}" + (f" (session {cycle})" if numbered and cycle > 1 else "")
        out.append(entry)
        total += len(entry) + 1
        i += 1
    return "\n".join(out)


# ---- Shared content pools (realistic-sounding, reused across fixtures) ----

_LYDIA_MEMORIES = [
    "The player freed me from my post at Dragonsreach and named me their housecarl.",
    "We cleared Bleak Falls Barrow together; I carried the dragonstone back myself.",
    "The player gave me an enchanted steel sword after my old one broke fighting a troll.",
    "I watched the player absorb a dragon's soul for the first time at the western watchtower.",
    "The player asked me to wait outside while they spoke with the Jarl alone.",
    "We were ambushed by bandits on the road to Whiterun; I took an arrow meant for them.",
    "The player joined the Companions, and I told them I was proud, though I worry about the Circle.",
    "I mentioned my old commander from the Great War once, and the player asked me to tell the story.",
    "The player left me behind at Breezehome for three days without word; I was worried sick.",
    "We fought a frost troll near the river; the player nearly died before I got there.",
]

_RECENT_EVENTS = [
    "A dragon was sighted near the Western Watchtower an hour ago.",
    "The court wizard was overheard arguing with the Jarl about Talos worship.",
    "A courier delivered a sealed letter to the player at the city gates.",
    "Bandits raided a farm outside the city walls last night.",
    "The player sold a large haul of dragon bones to the local blacksmith.",
    "A civil war skirmish was reported on the road to Rorikstead.",
    "The player was seen leaving the Bannered Mare late at night, looking uneasy.",
    "A merchant caravan arrived from Riften with unusually expensive goods.",
    "The player's Companion's wolf howled outside the gates at midnight.",
    "Rumors are spreading that a Thalmor patrol was seen near the stables.",
]

_LOCATION_DETAILS = [
    "A guard in city-watch armor stands near the gate, scanning the road.",
    "A merchant's stall sells iron ingots and leather, tarp flapping in the wind.",
    "Smoke rises from the blacksmith's forge two buildings down.",
    "A stray dog sniffs at a barrel of fish outside the general store.",
    "Two children are playing a chasing game near the well.",
    "A beggar sits against the tavern wall, asking passersby for coin.",
    "A cart loaded with hay creaks past, pulled by a tired horse.",
    "Torches gutter in their sconces along the palisade wall.",
    "A raven watches from the eaves of the longhouse roof.",
    "The smell of fresh bread drifts from the bakery's open shutters.",
]

_GAMEMASTER_BEATS = [
    "Tension rises as the dragon's roar echoes across the valley, sending birds scattering.",
    "The negotiations in the Jarl's hall grow tense as the Stormcloak envoy raises his voice.",
    "A sudden storm rolls in from the Sea of Ghosts, forcing travelers to seek shelter.",
    "Word spreads through the market that the High King's murder is now blamed on the player's own faction.",
    "The old ruins begin to hum faintly as the party approaches the sealed door.",
    "A pack of wolves is heard howling closer than usual, unsettling the camp.",
    "The innkeeper's expression darkens as the topic of the missing caravan comes up.",
    "Ash begins to fall gently from Red Mountain in the distance, a bad omen to the locals.",
    "The player's companion hesitates at the crossroads, clearly torn about which path to take.",
    "A courier arrives breathless, having run the whole way from Windhelm with urgent news.",
]


# ---- Fixture 1: NPC dialogue turn (moderate-to-large -- the exact real shape that broke) ----

_lydia_bio = (
    "You are Lydia, housecarl to the Dragonborn, sworn to carry their burdens. You are steady, "
    "dry-humored, fiercely loyal, and quietly weary of being treated as a pack mule. Speak in short, "
    "grounded sentences. Never break character or mention that you are an AI."
)
_lydia_memories = _cycle_to_length(_LYDIA_MEMORIES, 13_000)
_lydia_events = _cycle_to_length(_RECENT_EVENTS, 10_000)
NPC_DIALOGUE_TURN = Fixture(
    name="npc_dialogue_turn",
    task="NPC dialogue turn",
    system_prompt=(
        f"{_lydia_bio}\n\n"
        f"## Character bio\nLydia, Nord housecarl of Whiterun, age 29, former guard.\n\n"
        f"## Memories of the player\n{_lydia_memories}\n\n"
        f"## Recent events\n{_lydia_events}\n\n"
        "## Current scene\nYou are standing in Breezehome. The player has just walked in, "
        "muddy and tired, and set a heavy pack down by the door."
    ),
    user_content="Long day. You hungry?",
    notes="Sized to comfortably exceed 32,767 characters -- this is the real shape (bio + memories "
          "+ recent events + a short user turn) that broke on the geminicli/ route in production.",
)


# ---- Fixture 2: Bio / character generation (deliberately past 32,767 characters) ----

_worldbuilding_notes = _cycle_to_length(
    [
        "The character should fit naturally into a Nord hold's social hierarchy.",
        "Avoid modern phrasing; keep the voice period-appropriate for a fantasy North.",
        "The backstory should connect to at least one existing named NPC in Whiterun.",
        "Include one flaw or fear that could drive future quest hooks.",
        "The character's speech pattern should be distinct from Lydia's and from Farengar's.",
    ],
    40_000,
)
BIO_GENERATION = Fixture(
    name="bio_generation",
    task="Bio / character generation",
    system_prompt=(
        "You write character bios for new SkyrimNet NPCs. Given a name, role, and hold, produce a "
        "concise bio (appearance, personality, one memorable quirk, one secret) in third person.\n\n"
        f"## Worldbuilding constraints\n{_worldbuilding_notes}"
    ),
    user_content=(
        "Generate a bio for a new NPC: Hrolfdir, a retired sellsword now working as a stablehand "
        "in Whiterun, hold: Whiterun."
    ),
    notes="Deliberately past the 32,767-character Windows CreateProcessW argv-length cap that broke "
          "the Antigravity route when the prompt was still passed as a -p command-line argument.",
)


# ---- Fixture 3: Diary entry (moderate size -- a single NPC's own reflective voice) ----

_diary_recent_events = _cycle_to_length(_RECENT_EVENTS, 6_000)
DIARY_ENTRY = Fixture(
    name="diary_entry",
    task="NPC diary entry",
    system_prompt=(
        "You write a single first-person diary entry for the NPC described below, reflecting on "
        "recent events from their own perspective. Keep it to one or two short paragraphs, in their "
        "own voice, dated in-world.\n\n"
        "## Character\nUthgerd the Unbroken, Nord warrior, blunt, proud, quick to anger but loyal "
        "once respect is earned.\n\n"
        f"## Recent events she witnessed or heard about\n{_diary_recent_events}"
    ),
    user_content="Write tonight's diary entry.",
)


# ---- Fixture 4: Location and scene awareness (deliberately past 65,536 bytes) ----

_location_details = _cycle_to_length(_LOCATION_DETAILS, 115_000)
_nearby_npcs = _cycle_to_length(
    [
        "Adrianne Avenicci is hammering at the forge, sparks catching the evening light.",
        "Carlotta Valentia is arranging apples on her stall, glancing warily at a nearby guard.",
        "Belethor is sweeping the steps of the General Goods store.",
        "A Whiterun guard paces the wall walk above, spear resting on one shoulder.",
        "Ysolda haggles with a traveling merchant over a crate of spices.",
    ],
    30_000,
)
LOCATION_AWARENESS = Fixture(
    name="location_awareness",
    task="Location and scene awareness",
    system_prompt=(
        "You describe what the player character currently sees and hears, in second person, "
        "grounded in the concrete details provided. Do not invent named characters not listed "
        "below. Keep the final answer to 2-4 sentences even though the context below is long.\n\n"
        f"## Surroundings\nLocation: Whiterun, the Plains District, late afternoon.\n{_location_details}\n\n"
        f"## Nearby NPCs\n{_nearby_npcs}"
    ),
    user_content="What do you see here?",
    notes="Deliberately past 65,536 bytes (64 KiB) -- the asyncio StreamReader readline() default "
          "that raised ValueError on the Antigravity route when a single echoed line exceeded it. "
          "This fixture's total size, not any single internal line the proxy itself controls, is "
          "what's oversized here -- it exercises the full HTTP request/response path at that scale, "
          "not the geminicli-internal NDJSON-echo mechanism specifically (that's covered by "
          "test_geminicli_provider.py's own real-subprocess test).",
)


# ---- Fixture 5: GameMaster narration beat (moderate-to-large) ----

_gm_context = _cycle_to_length(_GAMEMASTER_BEATS, 20_000)
GAMEMASTER_NARRATION = Fixture(
    name="gamemaster_narration",
    task="GameMaster narration",
    system_prompt=(
        "You are the GameMaster, narrating the world reactively around the player without taking "
        "control of their character. Write in second person, present tense, 2-3 sentences, "
        "atmospheric but not purple. Do not resolve the situation for the player -- narrate the "
        "moment, then stop.\n\n"
        f"## Recent narrative beats\n{_gm_context}"
    ),
    user_content=(
        "The player has just stepped out of Jorrvaskr into the evening air after a heated argument "
        "with a Companion. Narrate the moment."
    ),
)


FIXTURES = [
    NPC_DIALOGUE_TURN,
    BIO_GENERATION,
    DIARY_ENTRY,
    LOCATION_AWARENESS,
    GAMEMASTER_NARRATION,
]

# Loud, load-time assertions -- a future edit that shrinks a template below the thresholds this
# whole file exists to guarantee should fail immediately, not silently stop catching the regression
# it was built for.
assert BIO_GENERATION.total_chars > 32_767, (
    f"bio_generation must exceed 32,767 characters (Windows argv-length cap) -- got {BIO_GENERATION.total_chars}"
)
assert LOCATION_AWARENESS.total_chars > 65_536, (
    f"location_awareness must exceed 65,536 bytes (64 KiB) -- got {LOCATION_AWARENESS.total_chars}"
)
