"""Set me up (2026-10-26): one button looks at this PC, asks one question, shows what it will install, and installs it with real progress.

Built to design/mockup-easier-installing.html section 1, where design/easier-installing-decisions.md wins on every difference:
  * The chat model is picked by the program's OWN fit check (`local_engine_memory`, the same one the Local models tab uses) at the context size the Local models tab already
    suggests for this card (`local_engine_settings.presets_for`), with named room left for the system and, when the game is on this PC, for Skyrim. No size table.
  * The catalog models' header facts (the numbers the cache formula needs) are kept in CACHE_FACTS, read from the real files' headers, so the check can run before anything
    is downloaded. A model that is not in CACHE_FACTS is never picked. The 31B is listed with its honest verdict but never preselected; no model is added to the catalog here.
  * Nothing here downloads on its own: engine, chat model, decision model and voice are the existing installs (`LocalModels.download_engine` / `download_model`,
    `DecisionEngine.start_install`, `VoiceEngine.start_install`), started one after the other and watched.
  * The words live in WORDS so the design side's Gemini pass is a one-spot swap.
"""
import asyncio
import datetime
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import decision_engine as de
import skyrim_peak
import gguf_header
from gpu_monitor import when_text
import local_engine_memory
import local_engine_settings
import local_models as lm

# ---- named estimates: change these two numbers to change how much room the picks leave --------------------------------------------------------------------------------
# Both are ESTIMATES of graphics memory the rest of the PC wants, not measurements.
SYSTEM_HEADROOM_MB = 2048          # Windows, the browser and overlays (the Local models tab's own "everything else" figure is the same 2 GB)
SKYRIM_HEADROOM_MB = 12288         # Skyrim itself, when the answer to "Do you play Skyrim on this PC?" is Yes: an ESTIMATE for Skyrim at 4K with heavy textures (a lighter game needs less; change it here)
# The least the rest of the PC can live on. A model that fits the budget above has "room to spare"; one that only fits with these smaller figures "leaves little" (it may slow or crash the game); anything bigger will not fit.
SYSTEM_LEAST_MB = 1024
SKYRIM_LEAST_MB = 6144
# When the game is running, its graphics memory is MEASURED (the highest of about ten readings over about ten seconds) and the buffer below is added. Both numbers are ESTIMATES of
# the margin one moment of play cannot show (a bigger area, other weather or more mods can use more); change them here.
MEASURE_BUFFER_MIN_MB = 2048       # the buffer is the larger of this ...
MEASURE_BUFFER_FRACTION = 0.15     # ... and this share of the measured figure
MEASURE_READINGS = 10
MEASURE_SECONDS = 10.0
DECISION_SMALL_BELOW_MB = 4096     # under this much graphics memory (or no card) the quick-decisions model is Laya (small), the same rule as the Decisions tab's one click
DISK_EXTRA_BYTES = 256 * 1024 * 1024       # what the existing installs ask for beyond the files themselves
ENGINE_UNPACK_FACTOR = 2           # the engine's zips plus what they unpack to (`LocalModels.download_engine` asks for the same)
RETRY_WAITS = (5.0, 20.0)          # seconds before the 2nd and 3rd try of a step that did not finish
POLL_SECONDS = 0.5

# Biggest first. Every one is listed for the player; none is picked unless it has room to spare (or nothing does and it is the smallest that fits).
CHAT_CANDIDATES = ("gemma-4-31b", "gemma-4-26b-a4b", "gemma-4-12b", "gemma-4-e4b", "gemma-4-e2b")
ONLY_WITH_ROOM = ("gemma-4-e4b", "gemma-4-e2b")      # the small ones are preselected only with room to spare; "leaves little" is never a start for them (the player can still choose them)
NEVER_PRESELECTED = ("gemma-4-31b",)        # listed with its honest verdict, but the player has to choose it
ROUTED_JOBS = ("dialogue", "gamemaster_evaluation", "memory")      # the talking jobs: dialogue, actions, memories (the keys Routing uses)

WORDS = {
    "question": "Do you play Skyrim on this PC?",
    "question_hint": "Yes keeps models smaller, saving graphics memory for the game.",
    "chat_name": "Dialogue, actions and memories: {model}",
    "chat_detail_game": "One model does all the talking jobs. Leaves room for the game on this PC.",
    "chat_detail_alone": "One model does all the talking jobs. Uses this PC's graphics card.",
    "decision_name": "Quick decisions: {model}",
    "decision_detail_kev": "Answers yes/no questions fast",
    "decision_detail_laya": "Small and quick; gets more questions wrong on long scenes",
    "engine_name": "Model engine (llama.cpp)",
    "engine_detail": "Installed for you, the right version for your card",
    "voice_name": "Voice: Higgs",
    "voice_optional": "(optional)",
    "voice_detail": "Needs a card with tensor cores",
    "voice_licence": "Accept its licence on the Voice tab first.",
    "installed": "Already installed",
    "verdict_room": "Fits, with room to spare",
    "verdict_little_game": "Fits, but leaves little for the game",
    "verdict_little_alone": "Fits, but leaves little to spare",
    "verdict_no_game": "Too big to run alongside Skyrim. Fine on a spare PC.",     # the Local models tab's own words
    "verdict_no_alone": "Too big for this device",                               # the Local models tab's own words
    "warn_game": "This may slow or crash the game.",
    "warn_alone": "This may run slowly.",
    "no_chat": "Cloud models handle dialogue better on this PC.",
    "no_card": "No graphics card found. Local models will run slowly here.",
    "card_chosen": "Using {name} (the card you chose).",
    "card_biggest": "Using {name} (the biggest of {n} graphics cards).",
    "disk_short": "{need} needed, {free} free on {drive}. Make some room, or choose another folder.",
    "folder_needed": "Choose where to keep the models.",
    "retry": "{name} did not finish downloading. It will try again by itself.",
    "done": "Installed. The talking jobs use {chat} and quick decisions use {decision}.",
    "done_decision_only": "Installed. Quick decisions use {decision}.",
    "done_cloud_only": "Installed. Dialogue uses {chat}.",
    "failed": "Could not install {name}: {why}",
    "col_model": "Model",
    "col_size": "Size",
    "col_fits": "Fits this PC",
    "col_status": "Status",
    "status_running": "Running",
    "status_installed": "Installed",
    "status_none": "–",
    "use": "Use these models",
    "game_measured": "Skyrim is using",
    "game_estimate": "Estimate",
    "game_buffer": "Buffer",
    "measured_when": "measured {when}",
    "measure": "Measure",
    "measuring": "Measuring…",
    "measure_step1": "Start Skyrim and load a save in a busy place, such as Whiterun.",
    "measure_step2": "Come back here.",
    "measure_step3": "Press Measure.",
    "no_room": "There isn't enough VRAM (graphics memory) to run models alongside Skyrim on this PC.",
    "no_room_paired": "{name} is paired. Set up models there.",
    "no_room_fits": "It has room for {model}.",
    "no_room_add": "Use another PC for the models. Add one here.",
    "no_room_cloud": "Or use a cloud model.",
    "btn_other_devices": "Open Other devices",
    "btn_add_pc": "Add a PC",
    "btn_providers": "Open Providers",
}

# ---- the catalog chat models' header facts (the cache formula's inputs), read from the real files' headers on 2026-10-26 ------------------------------------------------
# Both are Gemma 4: five sliding-window layers (1024 tokens) for every full-attention layer; the full layer keeps few heads.
def _gemma_facts(blocks: int, full_kv_heads: int, embedding: int, heads: int = 16, swa_kv_heads: int = 8) -> dict:
    groups = blocks // 6
    return {
        "gemma4.attention.head_count": heads,
        "gemma4.attention.head_count_kv": ([swa_kv_heads] * 5 + [full_kv_heads]) * groups,
        "gemma4.attention.key_length": 512, "gemma4.attention.key_length_swa": 256,
        "gemma4.attention.value_length": 512, "gemma4.attention.value_length_swa": 256,
        "gemma4.attention.sliding_window": 1024,
        "gemma4.attention.sliding_window_pattern": ([True] * 5 + [False]) * groups,
        "gemma4.block_count": blocks, "gemma4.context_length": 262144, "gemma4.embedding_length": embedding,
        "general.architecture": "gemma4",
    }


def _gemma_e_facts(blocks: int, kv_heads: int, embedding: int, period: int, shared: int) -> dict:
    """Gemma 4 E2B / E4B: one KV head count for every layer, a 512-token window, `period - 1` window layers per full layer, and the last `shared` layers reuse the cache of earlier ones (they keep none)."""
    return {
        "gemma4.attention.head_count": 8, "gemma4.attention.head_count_kv": kv_heads,
        "gemma4.attention.key_length": 512, "gemma4.attention.key_length_swa": 256,
        "gemma4.attention.value_length": 512, "gemma4.attention.value_length_swa": 256,
        "gemma4.attention.sliding_window": 512, "gemma4.attention.shared_kv_layers": shared,
        "gemma4.attention.sliding_window_pattern": ([True] * (period - 1) + [False]) * (blocks // period),
        "gemma4.block_count": blocks, "gemma4.context_length": 131072, "gemma4.embedding_length": embedding,
        "general.architecture": "gemma4",
    }


CACHE_FACTS = {
    "gemma-4-e2b": _gemma_e_facts(35, 1, 1536, 5, 20),       # read from the real file's header on 2026-10-26
    "gemma-4-e4b": _gemma_e_facts(42, 2, 2560, 6, 18),       # read from the real file's header on 2026-10-26
    "gemma-4-12b": _gemma_facts(48, 1, 3840),
    "gemma-4-26b-a4b": _gemma_facts(30, 2, 2816),
    "gemma-4-31b": _gemma_facts(60, 4, 5376, heads=32, swa_kv_heads=16),       # read from the real file's header on 2026-10-26
}


def facts_of(model_id: str, file_size: int) -> Optional[gguf_header.ModelFacts]:
    kv = CACHE_FACTS.get(model_id)
    if not kv:
        return None
    return gguf_header.ModelFacts("gemma4", kv["gemma4.block_count"], kv["gemma4.context_length"], file_size, dict(kv))


# ---- the pick ---------------------------------------------------------------------------------------------------------------------------------------------------------
@dataclass
class Card:
    name: str = ""
    memory_mb: Optional[int] = None
    vendor: str = ""
    count: int = 0                     # graphics cards a player could keep an AI on (built-in graphics is not counted)
    chosen: bool = False               # the player picked this one (the existing GPU choice)


def hardware_facts(card: Card) -> local_engine_settings.HardwareFacts:
    return local_engine_settings.HardwareFacts(card.name, card.vendor, card.count or (1 if card.name else 0), card.memory_mb)


def buffer_mb(measured_mb: int) -> int:
    return int(max(MEASURE_BUFFER_MIN_MB, MEASURE_BUFFER_FRACTION * measured_mb))


def game_room_mb(measured_mb: Optional[int]) -> int:
    """What is kept for Skyrim: what was measured plus the buffer, else the estimate."""
    return measured_mb + buffer_mb(measured_mb) if measured_mb else SKYRIM_HEADROOM_MB


def budget_mb(card: Card, has_game: bool, game_mb: Optional[int] = None) -> Optional[int]:
    """Graphics memory a chat model may use: the card, less the room kept for the system and (when the game is here) for Skyrim."""
    if not card.memory_mb:
        return None
    return card.memory_mb - SYSTEM_HEADROOM_MB - (game_room_mb(game_mb) if has_game else 0)


def estimate_for(model_id: str, card: Card, has_game: bool) -> Optional[local_engine_memory.MemoryEstimate]:
    """The program's own memory estimate for a catalog model at the settings the Local models tab suggests for this card, before the file exists."""
    spec = lm.CATALOG_BY_ID.get(model_id)
    if not spec:
        return None
    main = next(f for f in spec["files"] if f["role"] == "model")
    mm = next((f for f in spec["files"] if f["role"] == "mmproj"), None)
    facts = facts_of(model_id, main["size"])
    if facts is None:
        return None
    settings = local_engine_settings.presets_for(hardware_facts(card), has_game, None)[0]["settings"]
    est = local_engine_memory.estimate_from_facts(main["size"], mm["size"] if mm else None, facts, settings, None, card.memory_mb)
    return est if est.total_mb is not None else None


def verdict_of(model_id: str, card: Card, has_game: bool, game_mb: Optional[int] = None) -> Optional[str]:
    """"room" (fits with room to spare), "little" (fits but leaves little for the game) or "no" (will not fit); None when it cannot be told (no card, no header facts)."""
    room = budget_mb(card, has_game, game_mb)
    est = estimate_for(model_id, card, has_game)
    if room is None or est is None:
        return None
    if est.total_mb <= room:
        return "room"
    if est.total_mb <= card.memory_mb - SYSTEM_LEAST_MB - ((game_mb or SKYRIM_LEAST_MB) if has_game else 0):          # measured: the game's own figure, without the buffer
        return "little"
    return "no"


def chat_options(card: Card, has_game: bool, game_mb: Optional[int] = None) -> list:
    """Every catalog chat model, biggest first, with its verdict for this PC and this answer (the list the player picks from)."""
    out = []
    for mid in CHAT_CANDIDATES:
        v = verdict_of(mid, card, has_game, game_mb)
        if v is None:
            continue
        text = WORDS["verdict_room"] if v == "room" else (WORDS["verdict_little_game" if has_game else "verdict_little_alone"] if v == "little"
                                                          else WORDS["verdict_no_game" if has_game else "verdict_no_alone"])
        out.append({"id": mid, "name": lm.CATALOG_BY_ID[mid]["name"], "bytes": lm.model_total_bytes(lm.CATALOG_BY_ID[mid]["files"]),
                    "verdict": v, "text": text, "selectable": v != "no",
                    "warning": WORDS["warn_game" if has_game else "warn_alone"] if v == "little" else ""})
    return out


def default_chat(options: list) -> Optional[str]:
    """The safe one: the largest with room to spare, else the smallest that fits, else None."""
    options = [o for o in options if o["id"] not in NEVER_PRESELECTED]
    for o in options:
        if o["verdict"] == "room":
            return o["id"]
    for o in reversed(options):
        if o["verdict"] == "little" and o["id"] not in ONLY_WITH_ROOM:
            return o["id"]
    return None


def pick_chat(card: Card, has_game: bool, choice: Optional[str] = None, game_mb: Optional[int] = None) -> Optional[str]:
    """The player's pick when it can run at all, else the safe default (or None: no card, an unreadable card, or nothing fits)."""
    options = chat_options(card, has_game, game_mb)
    if choice and any(o["id"] == choice and o["selectable"] for o in options):
        return choice
    return default_chat(options)


def pick_decision(card: Card, installed_key: Optional[str] = None) -> str:
    """The quick-decisions model: the one already installed, else Kev-4B, or Laya (small) when there is almost no graphics memory."""
    if installed_key:
        return installed_key
    if not card.memory_mb or card.memory_mb < DECISION_SMALL_BELOW_MB:
        return "laya"
    return de.DEFAULT_MODEL


# ---- the plan ---------------------------------------------------------------------------------------------------------------------------------------------------------
def gb_text(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def drive_of(path: Optional[Path]) -> str:
    if not path:
        return ""
    return path.drive or str(path.anchor or path)


@dataclass
class Facts:
    """Everything the plan needs from this PC. MultiProxy fills it in; a test fills it with stand-ins."""
    card: Card = field(default_factory=Card)
    folder: Optional[Path] = None
    free_disk: Optional[int] = None
    engine_kind: str = "cpu"
    engine_bytes: int = 0
    engine_here: bool = False
    chat_here: set = field(default_factory=set)             # catalog chat model ids already installed
    chat_running: set = field(default_factory=set)          # the ones loaded in the built-in engine right now
    decision_running: bool = False
    cloud_only: bool = False                                # Decisions > Who answers first > "Cloud only": no decision model is offered or installed
    skyrim_mb: Optional[int] = None                         # the game's measured graphics memory (the last measurement), None = use the estimate
    skyrim_when: str = ""                                   # "today", "yesterday", "on 24 Oct": when that was measured
    decision_key: Optional[str] = None                      # a decision model already installed
    voice_available: bool = False                           # the voice model can be installed here (fit not refused)
    voice_licence_ok: bool = False
    voice_bytes: int = 0
    voice_here: bool = False
    ram_bytes: Optional[int] = None
    kept_files: set = field(default_factory=set)             # file names in the kept downloads: a kept file is already there, so it needs no room
    chat_kept: set = field(default_factory=set)             # catalog chat model ids removed but with their files still in the models folder
    other_pcs: list = field(default_factory=list)           # paired Courier PCs: {"id", "name", "card": Card or None} (the card is None when that PC's graphics memory is not known)


def no_room_panel(facts: Facts, options: list) -> Optional[dict]:
    """When the game is here and not one chat model can run next to it: the amber line and the next steps (a paired PC, else add one or use the cloud). None when something fits."""
    if not options or any(o["selectable"] for o in options):
        return None
    steps = []
    for pc in facts.other_pcs:
        text = WORDS["no_room_paired"].format(name=pc["name"])
        card = pc.get("card")
        if card is not None and card.memory_mb:
            best = default_chat(chat_options(card, False))          # that PC has no game on it: the same fit check, the whole card
            if best:
                text += " " + WORDS["no_room_fits"].format(model=lm.CATALOG_BY_ID[best]["name"])
        steps.append({"text": text, "button": WORDS["btn_other_devices"], "action": "pc", "id": pc["id"]})
    if not steps:
        steps.append({"text": WORDS["no_room_add"], "button": WORDS["btn_add_pc"], "action": "add", "id": ""})
        steps.append({"text": WORDS["no_room_cloud"], "button": WORDS["btn_providers"], "action": "providers", "id": ""})
    return {"warning": WORDS["no_room"], "steps": steps}


def _status(installed: bool, running: bool) -> str:
    return WORDS["status_running"] if running else (WORDS["status_installed"] if installed else WORDS["status_none"])


def make_plan(facts: Facts, has_game: bool, voice: bool = False, chat: Optional[str] = None) -> dict:
    """What would be installed on this PC, for the screen and for the install itself."""
    card = facts.card
    items: list = []
    notes: list = []
    game_mb = facts.skyrim_mb if has_game else None
    options = chat_options(card, has_game, game_mb)
    chat_id = pick_chat(card, has_game, chat, game_mb)
    for o in options:
        o["selected"] = o["id"] == chat_id
        o["status"] = _status(o["id"] in facts.chat_here, o["id"] in facts.chat_running)
    if chat_id is None and not card.memory_mb:
        notes.append(WORDS["no_card"])
    if chat_id:
        spec = lm.CATALOG_BY_ID[chat_id]
        total = lm.model_total_bytes(spec["files"])
        here = chat_id in facts.chat_here
        items.append({"key": "chat", "id": chat_id, "name": WORDS["chat_name"].format(model=spec["name"]), "model": spec["name"],
                      "detail": WORDS["installed"] if here else WORDS["chat_detail_game" if has_game else "chat_detail_alone"], "fits": "", "size": total,
                      "status": _status(here, chat_id in facts.chat_running),
                      "bytes": 0 if here or chat_id in facts.chat_kept else total, "here": here, "ticked": True, "optional": False, "disabled": False})
    else:
        notes.append(WORDS["no_chat"])
    no_room = no_room_panel(facts, options) if has_game else None
    if no_room:
        notes = [n for n in notes if n != WORDS["no_chat"]]          # the panel says it instead
    dkey = None if facts.cloud_only else pick_decision(card, facts.decision_key)
    if dkey:                                                # (with "Cloud only" on, the table does not offer the decision model at all)
        dspec = de.MODELS[dkey]
        d_here = facts.decision_key == dkey
        items.append({"key": "decision", "id": dkey, "name": WORDS["decision_name"].format(model=dspec.label), "model": dspec.label,
                      "detail": WORDS["installed"] if d_here else WORDS["decision_detail_laya" if dkey == "laya" else "decision_detail_kev"],
                      "fits": "", "size": dspec.total_bytes(), "status": _status(d_here, d_here and facts.decision_running),
                      "bytes": 0 if d_here else sum(f.size for f in dspec.files() if f.name not in facts.kept_files), "here": d_here, "ticked": True, "optional": False, "disabled": False})
    items.append({"key": "engine", "id": facts.engine_kind, "name": WORDS["engine_name"], "model": "llama.cpp",
                  "detail": WORDS["installed"] if facts.engine_here else WORDS["engine_detail"],
                  "fits": "", "size": facts.engine_bytes, "status": _status(facts.engine_here, facts.engine_here and bool(facts.chat_running or facts.decision_running)),
                  "bytes": 0 if facts.engine_here else facts.engine_bytes, "here": facts.engine_here, "ticked": True, "optional": False, "disabled": False})
    if facts.voice_available or facts.voice_here:
        ok = facts.voice_here or facts.voice_licence_ok
        items.append({"key": "voice", "id": "higgs", "name": WORDS["voice_name"], "optionalNote": WORDS["voice_optional"], "model": "Higgs",
                      "detail": WORDS["installed"] if facts.voice_here else (WORDS["voice_detail"] if ok else WORDS["voice_licence"]),
                      "fits": "" if facts.voice_here else (WORDS["voice_detail"] if ok else WORDS["voice_licence"]), "size": facts.voice_bytes, "status": _status(facts.voice_here, False),
                      "bytes": 0 if facts.voice_here else facts.voice_bytes, "here": facts.voice_here,
                      "ticked": bool(voice) and ok and not facts.voice_here, "optional": True, "disabled": not ok or facts.voice_here})
    need = sum(i["bytes"] for i in items if i["ticked"])
    if not facts.engine_here:
        need += facts.engine_bytes * (ENGINE_UNPACK_FACTOR - 1)         # the unpacked copy
    if need:
        need += DISK_EXTRA_BYTES
    warn = ""
    if facts.folder is None:
        warn = WORDS["folder_needed"]
    elif facts.free_disk is not None and need > facts.free_disk:
        warn = WORDS["disk_short"].format(need=gb_text(need), free=gb_text(facts.free_disk), drive=drive_of(facts.folder))
    card_note = ""
    if card.name and card.count > 1:
        card_note = WORDS["card_chosen"].format(name=card.name) if card.chosen else WORDS["card_biggest"].format(name=card.name, n=card.count)
    work = [i for i in items if i["ticked"] and not i["here"]]
    everything_here = bool(chat_id) and not work
    game = None
    if has_game:
        if facts.skyrim_mb:
            game = {"measured": True, "label": WORDS["game_measured"], "mb": facts.skyrim_mb, "bufferLabel": WORDS["game_buffer"], "bufferMb": buffer_mb(facts.skyrim_mb),
                    "when": WORDS["measured_when"].format(when=facts.skyrim_when) if facts.skyrim_when else ""}
        else:
            game = {"measured": False, "label": WORDS["game_estimate"], "mb": SKYRIM_HEADROOM_MB, "bufferLabel": "", "bufferMb": 0, "when": "",
                    "steps": [WORDS["measure_step1"], WORDS["measure_step2"], WORDS["measure_step3"]]}
        game["button"] = WORDS["measure"]
    return {"game": game, "columns": [WORDS["col_model"], WORDS["col_size"], WORDS["col_fits"], WORDS["col_status"]], "useWord": WORDS["use"], "allHere": everything_here,"hasGame": has_game, "items": items, "notes": notes, "needBytes": need, "freeBytes": facts.free_disk, "drive": drive_of(facts.folder),
            "folder": str(facts.folder) if facts.folder else None, "warning": warn, "cardNote": card_note,
            "canInstall": (bool(work) or everything_here) and not warn, "installCount": len(work),
            "card": {"name": card.name, "memoryGb": round(card.memory_mb / 1024, 1) if card.memory_mb else None, "count": card.count},
            "chat": chat_id, "decision": dkey, "models": [] if no_room else options, "noRoom": no_room,
            "chatWarning": next((o["warning"] for o in options if o["selected"]), "")}


# ---- the install ------------------------------------------------------------------------------------------------------------------------------------------------------
@dataclass
class Step:
    key: str
    label: str
    total: int = 0
    done: int = 0
    state: str = "waiting"             # waiting | running | done | error
    error: str = ""
    left: str = ""                     # "about 40 min left" / "waiting for network", copied from the download this step watches (worded by download_meter.py)

    def to_json(self) -> dict:
        return {"key": self.key, "label": self.label, "total": self.total, "done": self.done, "state": self.state,
                "percent": int(self.done * 100 / self.total) if self.total else 0, "left": self.left if self.state == "running" else ""}


@dataclass
class SetJob:
    steps: list
    plan: dict
    state: str = "running"             # running | paused | done | error | cancelled
    warning: str = ""                  # "<name> did not finish downloading. It will try again by itself."
    error: str = ""
    message: str = ""                  # the one line shown when done
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    options: tuple = ()                # (has_game, voice, chat) it was started with: Resume starts it again the same way

    def to_json(self) -> dict:
        return {"state": self.state, "steps": [s.to_json() for s in self.steps], "warning": self.warning, "error": self.error, "message": self.message,
                "pausedBytes": sum(s.total if s.state == "done" else s.done for s in self.steps) if self.state == "paused" else 0}


class SetMeUp:
    """The orchestrator. `local` is LocalModels, `decisions` a DecisionEngine, `voice` a VoiceEngine (or None), `facts_fn` builds Facts from this PC."""

    def __init__(self, *, local, decisions, voice=None, voice_licence_ok: Callable[[], bool] = lambda: False, facts_fn: Callable[[], Any],
                 load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None], log, retry_waits=RETRY_WAITS, poll: float = POLL_SECONDS, measure_fn=None):
        self.measure_fn = measure_fn           # async (readings, seconds) -> {"running": bool, "mb": int | None}: the running game's graphics memory
        self.local, self.decisions, self.voice = local, decisions, voice
        self.voice_licence_ok = voice_licence_ok
        self.facts_fn = facts_fn
        self._load_cfg, self._save_cfg, self.log = load_cfg, save_cfg, log
        self.retry_waits, self.poll = retry_waits, poll
        self.job: Optional[SetJob] = None
        self._current: Optional[tuple] = None          # (kind, key) of the install being watched, so Cancel can stop it

    # ---- state kept in the settings file
    def used(self) -> bool:
        return bool((self._load_cfg().get("set_me_up") or {}).get("used"))

    def show_card(self, already_set_up: bool) -> bool:
        """Whether Set me up is offered (Overview's card, Get started's block): only on a brand-new install. Once anything was set up, or Set me up has finished once, it is gone for
        good: that is remembered (`set_up_before`), so a player who later deletes their models is not nagged."""
        cfg = self._load_cfg()
        if cfg.get("set_up_before") or (cfg.get("set_me_up") or {}).get("used"):
            return False
        if already_set_up:
            cfg["set_up_before"] = True
            self._save_cfg(cfg)
            return False
        return True

    def _mark_used(self, plan: dict) -> None:
        cfg = self._load_cfg()
        cfg["set_me_up"] = {"used": True, "chat": plan.get("chat"), "decision": plan.get("decision")}
        self._save_cfg(cfg)

    # ---- what the screen asks for
    async def _facts(self) -> Facts:
        facts = await self.facts_fn()
        facts.cloud_only = bool((self._load_cfg().get("decisions") or {}).get("cloud_only"))
        mb, day = skyrim_peak.figure(self._load_cfg())          # the one shared figure (skyrim_peak.py): the Local device dots read the same one
        if mb and facts.skyrim_mb is None:
            facts.skyrim_mb = int(mb)
            facts.skyrim_when = when_text(day)
        return facts

    async def measure(self) -> dict:
        """Read the running game's graphics memory (the highest of several readings) and remember it with today's date. {"running", "measured"}: not running, or unreadable, changes nothing."""
        if self.measure_fn is None:
            return {"running": False, "measured": False}
        try:
            res = await self.measure_fn(MEASURE_READINGS, MEASURE_SECONDS)
        except Exception:
            res = None
        res = res or {}
        mb = res.get("mb")
        if mb and mb > 0:
            cfg = self._load_cfg()
            skyrim_peak.record(cfg, int(mb), card_mb=(await self.facts_fn()).card.memory_mb)          # into the same figure the Local device dots use
            self._save_cfg(cfg)
        return {"running": bool(res.get("running")), "measured": bool(mb and mb > 0)}

    async def look(self) -> dict:
        facts = await self._facts()
        return {"used": self.used(), "job": self.job.to_json() if self.job else None, "question": WORDS["question"], "questionHint": WORDS["question_hint"],
                "card": {"name": facts.card.name, "memoryGb": round(facts.card.memory_mb / 1024, 1) if facts.card.memory_mb else None, "count": facts.card.count},
                "folder": str(facts.folder) if facts.folder else None,
                "freeGb": round(facts.free_disk / 1e9, 1) if facts.free_disk is not None else None, "drive": drive_of(facts.folder),
                "cardNote": make_plan(facts, True)["cardNote"]}

    async def plan(self, has_game: bool, voice: bool = False, chat: Optional[str] = None) -> dict:
        return make_plan(await self._facts(), has_game, voice, chat)

    # ---- running it
    async def start(self, has_game: bool, voice: bool = False, chat: Optional[str] = None) -> SetJob:
        if self.job and self.job.state == "running":
            return self.job
        plan = await self.plan(has_game, voice, chat)
        if not plan["canInstall"]:
            raise lm.LocalModelError(400, plan["warning"] or "There is nothing to install.")
        steps = [Step(i["key"], i["model"] if i["key"] != "engine" else "llama.cpp engine", total=i["bytes"]) for i in plan["items"] if i["ticked"] and not i["here"]]
        # the engine goes first: the models need it
        steps.sort(key=lambda s: 0 if s.key == "engine" else 1)
        self.job = SetJob(steps, plan, options=(has_game, voice, chat))
        self.job.task = asyncio.ensure_future(self._run(self.job, plan))
        return self.job

    async def pause(self) -> None:
        """Stop fetching now and keep the partial files. Each download is paused by its own owner (so it also reads Paused on its own tab, even after a restart);
        Resume starts the same plan again and every download carries on from the byte it reached."""
        job = self.job
        if not job or job.state != "running":
            return
        job.state = "paused"
        cur = self._current
        try:
            if cur and cur[0] == "local":
                await self.local.pause(cur[1])
            elif cur and cur[0] == "decision":
                self.decisions.pause_install()
            elif cur and cur[0] == "voice" and self.voice is not None:
                self.voice.cancel_install()
        except Exception:
            pass
        if job.task:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)

    async def resume(self) -> SetJob:
        job = self.job
        if not job or job.state != "paused":
            raise lm.LocalModelError(404, "There are no paused downloads here.")
        return await self.start(*job.options)

    async def cancel(self) -> None:
        job = self.job
        if not job or job.state not in ("running", "paused"):
            return
        was_paused = job.state == "paused"
        job.state = "cancelled"
        cur = self._current
        try:
            if cur and cur[0] == "local":
                await self.local.cancel(cur[1])
            elif cur and cur[0] == "decision":
                self.decisions.cancel_install()
            elif cur and cur[0] == "voice" and self.voice is not None:
                self.voice.cancel_install()
        except Exception:
            pass
        if job.task and not was_paused:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)

    def dismiss(self) -> None:
        if self.job and self.job.state not in ("running", "paused"):
            self.job = None

    async def _watch(self, step: Step, handle: Any, kind: str, key: str) -> None:
        """Wait for one of the existing install jobs to end, copying its byte counts to the step."""
        self._current = (kind, key)
        while True:
            state = getattr(handle, "state", "done")
            if getattr(handle, "total", 0):
                step.total = handle.total
            step.done = min(getattr(handle, "done", 0), step.total) if step.total else getattr(handle, "done", 0)
            try:
                step.left = handle.to_json().get("left", "")
            except Exception:
                step.left = ""
            if state != "running":
                break
            await asyncio.sleep(self.poll)
        if state == "done":
            return
        raise lm.LocalModelError(502, getattr(handle, "error", "") or "The download did not finish.")

    async def _do(self, step: Step, plan: dict) -> None:
        if step.key == "engine":
            handle = self.local.download_engine(plan["items"][[i["key"] for i in plan["items"]].index("engine")]["id"])
            await self._watch(step, handle, "local", "engine")
        elif step.key == "chat":
            mid = plan["chat"]
            await self._watch(step, self.local.download_model(mid), "local", f"model:{mid}")
        elif step.key == "decision":
            cfg = self._load_cfg()
            cur = dict(cfg.get("decisions") or {})
            if cur.get("model") != plan["decision"]:
                cur["model"] = plan["decision"]
                cfg["decisions"] = cur
                self._save_cfg(cfg)
            await self._watch(step, self.decisions.start_install(), "decision", "decision")
        elif step.key == "voice" and self.voice is not None:
            await self._watch(step, self.voice.start_install(), "voice", "voice")

    async def _run(self, job: SetJob, plan: dict) -> None:
        try:
            for step in job.steps:
                step.state = "running"
                tries = len(self.retry_waits) + 1
                for attempt in range(tries):
                    try:
                        await self._do(step, plan)
                        step.state, step.done, job.warning = "done", step.total, ""
                        break
                    except Exception as e:           # (a cancel is not an Exception: it passes through to the handler below)
                        step.done = 0
                        why = getattr(e, "message", "") or str(e)
                        if attempt + 1 >= tries or getattr(e, "status", None) in (400, 403, 404, 409):      # asking again cannot fix a full disk, a refused card or a missing folder
                            step.state, step.error = "error", why
                            job.state, job.error = "error", WORDS["failed"].format(name=step.label, why=why)
                            job.warning = ""
                            self.log.warning(f"Set me up: {step.label} failed: {why}")
                            return
                        job.warning = WORDS["retry"].format(name=step.label)
                        await asyncio.sleep(self.retry_waits[attempt])
            self._wire_routing(plan)
            job.state = "done"
            job.warning = ""
            chat = plan.get("chat") and lm.CATALOG_BY_ID[plan["chat"]]["name"]
            if plan.get("decision"):
                decision = de.MODELS[plan["decision"]].label
                job.message = WORDS["done"].format(chat=chat, decision=decision) if chat else WORDS["done_decision_only"].format(decision=decision)
            else:
                job.message = WORDS["done_cloud_only"].format(chat=chat)
            self._mark_used(plan)
        except asyncio.CancelledError:
            if job.state != "paused":
                job.state = "cancelled"
            raise
        except Exception as e:
            job.state, job.error = "error", WORDS["failed"].format(name="Set me up", why=str(e))
            self.log.exception("Set me up failed")

    def _wire_routing(self, plan: dict) -> None:
        """The talking jobs use the chat model: a job that has no route yet gets one (a route the player made is left alone). The tested settings need nothing here: the built-in
        engine starts with them ON for the models MultiProxy installs (tested_settings.BUILT_IN_ON_BY_DEFAULT)."""
        chat = plan.get("chat")
        if not chat:
            return
        import routing
        cfg = self._load_cfg()
        block = routing.clean_settings(cfg.get("routing"))
        routes = dict(block.get("routes") or {})
        for job in ROUTED_JOBS:
            if job not in routes:
                routes[job] = {"ais": [f"{lm.LOCAL_PREFIX}{chat}"], "how": "order", "slow_limit_s": None}
        block["routes"] = routes
        cfg["routing"] = routing.clean_settings(block)
        self._save_cfg(cfg)


# ---- looking at this PC (the real facts; tests hand in their own) -----------------------------------------------------------------------------------------------
def nearest_existing(path: Path) -> Path:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


async def read_facts(local, decisions, voice=None, voice_licence_ok: Callable[[], bool] = lambda: False, cards_fn: Optional[Callable[[], list]] = None,
                     choice_fn: Optional[Callable[[str], Optional[dict]]] = None, pcs_fn: Optional[Callable[[], list]] = None) -> Facts:
    """The real facts: the card (the player's own choice when they made one), the models folder and its free room, and what is already installed."""
    import gpu_choice
    cards = await asyncio.to_thread(cards_fn or gpu_choice.cached_cards)
    real = [c for c in cards if c.memory_mb and not gpu_choice.is_integrated(c)] or list(cards)
    gpu = await local.gpu_for(CHAT_CANDIDATES[-1])
    card = Card()
    chosen = None
    if choice_fn is not None:
        for mid in CHAT_CANDIDATES:
            c = choice_fn(mid)
            if c and c.get("id"):
                chosen = c
                break
    if gpu is not None:
        card = Card(gpu.name, gpu.memory_mb, gpu.vendor, count=max(1, len(gpu_choice.choosable(cards))), chosen=bool(chosen))
    elif real:
        best = max(real, key=lambda c: c.memory_mb or 0)
        card = Card(best.name, best.memory_mb, best.vendor, count=len(real))
    folder = local.folder
    free = None
    if folder:
        try:
            free = shutil.disk_usage(nearest_existing(folder)).free
        except OSError:
            free = None
    options = lm.engine_options(gpu) if gpu is not None else lm.engine_options(None)
    kind = options[0]["kind"]
    facts = Facts(card=card, folder=folder, free_disk=free, engine_kind=kind, engine_bytes=lm.engine_total_size(kind), engine_here=local.engine_exe_current() is not None)
    facts.chat_here = {m.id for m in local.installed() if not m.custom}
    try:
        facts.chat_kept = {k["id"] for k in local.kept()}
        facts.kept_files = {f["name"] for f in decisions.store.listing()["files"]}
    except Exception:
        pass
    facts.chat_running = {i for i in facts.chat_here if local.state_of(i)[0] == "running"}
    facts.decision_running = getattr(decisions, "state", "") == "ready"
    try:
        if decisions.installed(decisions.model_key()):
            facts.decision_key = decisions.model_key()
    except Exception:
        pass
    if pcs_fn is not None:
        try:
            for r in pcs_fn():
                if getattr(r, "kind", "") != "courier":
                    continue
                g = getattr(r, "gpu", None) or {}
                gb = g.get("memoryGb")
                pc_card = Card(g.get("name") or "", int(gb * 1024), g.get("vendor") or "", count=1) if gb else None
                facts.other_pcs.append({"id": r.id, "name": r.label, "card": pc_card})
        except Exception:
            pass
    if voice is not None:
        try:
            fit = await asyncio.to_thread(voice.fit_check)
            facts.voice_available = fit.verdict != "refuse"
            facts.voice_bytes = fit.need_disk_bytes
            facts.voice_here = bool(voice.installed())
            facts.voice_licence_ok = bool(voice_licence_ok())
        except Exception:
            facts.voice_available = False
    return facts
