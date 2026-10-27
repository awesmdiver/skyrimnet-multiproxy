"""The saved settings of local models: what is saved, where, and what it starts out as (2026-10-04).

Built to design/local-model-settings-mockup.html (revision 2 and its "Gemini review applied" block) and design/local-model-settings-engines.md.

WHAT IS SAVED: settings belong to the DEVICE (this device is `local`; a device running models through the Courier has its own id), saved on the MultiProxy server
in `config.json` under `local_engine_settings`, one record per device:

    {"devices": {"local": {"build": "b11361", "settings": {...}, "models": {"<model id>": {"ctx": 16384, "parallel": 1}}, "reset": [...], "hash": "..."}}}

  * `settings`: `layers` (auto | all | none | a number), `fitTargetMb` (auto | a number: the graphics memory to keep free for the game), `ctx` (the total
    memory for conversations, in tokens), `parallel` (requests at the same time), `flashAttn` (auto | on | off), `cacheType` (f16 | q8_0 | q4_0, the same for
    both halves of the cache), `extraArgs` (the player's own flags, as separate arguments; this is also where batch sizes and cache reuse live, because
    the screens do not show them), `preset` (which preset the values came from; `custom` once they differ from every preset).
  * `models`: a model only overrides `ctx` and `parallel`. They are keyed by model id and live here, never in the model's folder (replacing the model file
    must not lose them).
  * `build`: the engine build the settings were last checked against (`LLAMA_CPP_BUILD`). When the pinned build changes, `revalidate` checks everything
    again, resets whatever is no longer valid to its default and keeps the list of what it reset in `reset` for the screen to show.
  * `hash`: a short fingerprint of the record, so a device that is sent its settings can say whether it has the current ones.

A device that never saved anything gets `DEFAULT_SETTINGS`, which are exactly what MultiProxy has always started a chat model with (32,768 tokens in total,
two requests at a time, every layer on the card): nothing changes for anyone until they choose a preset or change a value. MultiProxy 4.0 is not released,
so there is nothing older to migrate.

Also here: the presets by graphics card size (`presets_for`), the default for "memory to keep free" (`resolve_fit_target_mb`), and the recorder of the
largest prompt each model has been sent (`PromptPeaks`), saved as data under `local_prompt_peaks`.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Optional

import local_engine_flags as flags

DEVICE_LOCAL = "local"
CONFIG_KEY = "local_engine_settings"
PEAKS_KEY = "local_prompt_peaks"

DEFAULT_CTX = 32768
DEFAULT_PARALLEL = 2
DEFAULT_SETTINGS = {
    "layers": "all", "fitTargetMb": "auto", "ctx": DEFAULT_CTX, "parallel": DEFAULT_PARALLEL,
    "flashAttn": "auto", "cacheType": "f16", "extraArgs": [], "preset": "default",
}
SETTING_KEYS = tuple(DEFAULT_SETTINGS)
PRESET_IDS = ("default", "suggested", "fastest", "share", "custom")
OVERRIDE_KEYS = ("ctx", "parallel")
MAX_CTX = 4 * 1024 * 1024
MAX_PARALLEL = 64

FIT_MARGIN_MB = 1024                 # kept free beyond Skyrim's own busiest point (windows, the browser, an overlay)
NO_GAME_FIT_MB = 1024                # a device with no game on it: llama.cpp's own default margin
UNKNOWN_CARD_FIT_MB = 4096           # no busiest point learned yet and no card size: the smallest guess of Skyrim's share (3 GB) plus the margin
SHARE_EXTRA_MB = 2048                # "Share the card": this much more kept free than the suggestion


# --------------------------------------------------------------------------------------------
# Checking and cleaning values
# --------------------------------------------------------------------------------------------
def _int(v) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == int(v):
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _layers(v):
    if isinstance(v, str) and v.strip().lower() in flags.LAYER_WORDS:
        return v.strip().lower()
    n = _int(v)
    if n is None or n < 0:
        return None
    return "none" if n == 0 else n


def normalise_settings(raw, base: Optional[dict] = None) -> tuple:
    """-> (settings, problems). `raw` is what a screen or a saved file holds; anything missing comes from `base` (default: the defaults), anything that is
    not a valid value is replaced by `base`'s and named in `problems` (a list of `{setting, was, reason}`)."""
    base = copy.deepcopy(base or DEFAULT_SETTINGS)
    raw = raw if isinstance(raw, dict) else {}
    out, problems = dict(base), []

    def bad(key, reason):
        problems.append({"setting": key, "was": raw.get(key), "reason": reason})

    for key in SETTING_KEYS:
        if key not in raw:
            continue
        v = raw[key]
        if key == "layers":
            n = _layers(v)
            out[key] = n if n is not None else base[key]
            if n is None:
                bad(key, "not auto, all, none or a number of layers")
        elif key == "fitTargetMb":
            n = _int(v)
            if isinstance(v, str) and v.strip().lower() == "auto":
                out[key] = "auto"
            elif n is not None and n >= 0:
                out[key] = n
            else:
                bad(key, "not auto or a number of megabytes")
        elif key == "ctx":
            n = _int(v)
            if n is not None and 1 <= n <= MAX_CTX:
                out[key] = n
            else:
                bad(key, "not a memory size")
        elif key == "parallel":
            n = _int(v)
            if n is not None and 1 <= n <= MAX_PARALLEL:
                out[key] = n
            else:
                bad(key, "needs a whole number from 1 up")
        elif key == "flashAttn":
            if isinstance(v, str) and v.strip().lower() in ("auto", "on", "off"):
                out[key] = v.strip().lower()
            else:
                bad(key, "not auto, on or off")
        elif key == "cacheType":
            if isinstance(v, str) and v.strip().lower() in flags.SIMPLE_CACHE_TYPES:
                out[key] = v.strip().lower()
            else:
                bad(key, "not f16, q8_0 or q4_0")
        elif key == "extraArgs":
            if isinstance(v, str):
                out[key] = flags.tokenize(v)
            elif isinstance(v, (list, tuple)) and all(isinstance(t, (str, int, float)) and not isinstance(t, bool) for t in v):
                out[key] = [str(t) for t in v]
            else:
                bad(key, "not a list of flags")
        elif key == "preset":
            if isinstance(v, str) and v in PRESET_IDS:
                out[key] = v
            else:
                bad(key, "not a known preset")
    return out, problems


def normalise_override(raw) -> dict:
    """A model's own `{ctx, parallel}`: only valid whole numbers survive; an empty result means "follows the device"."""
    out = {}
    if isinstance(raw, dict):
        n = _int(raw.get("ctx"))
        if n is not None and 1 <= n <= MAX_CTX:
            out["ctx"] = n
        n = _int(raw.get("parallel"))
        if n is not None and 1 <= n <= MAX_PARALLEL:
            out["parallel"] = n
    return out


def validate_for_build(settings: dict, known_flags: Optional[set] = None) -> tuple:
    """Check saved settings against the engine they will run on -> (settings, reset). Anything no longer valid goes back to its default; every change is
    listed as `{setting, was, now, reason}`. The extra flags are the part that can go stale between builds: each is run through the parser (so a flag the
    build removed, or one it now refuses, is dropped), and, when the engine's own `--help` flag list is known, a flag the help does not list is dropped too."""
    s, problems = normalise_settings(settings)
    reset = [{"setting": p["setting"], "was": str(p["was"]), "now": str(DEFAULT_SETTINGS[p["setting"]]), "reason": p["reason"]} for p in problems]
    parsed = flags.parse_command_line(s.get("extraArgs") or [])
    keep = []
    for it in parsed.extra_items:
        if known_flags is not None and not it["known"] and it["flag"].lower() not in known_flags:
            reset.append({"setting": "extraArgs", "was": " ".join(it["tokens"]), "now": "", "reason": "This version of the engine does not list that flag."})
            continue
        keep += it["tokens"]
    for group in (parsed.refused, parsed.unavailable, parsed.invalid):
        for d in group:
            reset.append({"setting": "extraArgs", "was": f"{d['flag']} {d['value']}".strip(), "now": "", "reason": d.get("reason") or "No longer allowed."})
    s["extraArgs"] = keep
    return s, reset


def fingerprint(record: dict) -> str:
    """A short fingerprint of the settings and per-model overrides in a device record."""
    blob = json.dumps({"settings": record.get("settings"), "models": record.get("models"), "build": record.get("build")}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------------------------
@dataclass
class DeviceRecord:
    device: str
    settings: dict
    models: dict = field(default_factory=dict)
    build: str = ""
    reset: list = field(default_factory=list)
    hash: str = ""
    saved: bool = False                    # False: nothing was ever saved for this device (the defaults are in use)

    def to_json(self) -> dict:
        return {"device": self.device, "settings": self.settings, "models": self.models, "build": self.build, "reset": self.reset, "hash": self.hash,
                "saved": self.saved}


class EngineSettingsStore:
    """Reads and writes the `local_engine_settings` key of config.json through the same two callbacks `LocalModels` uses."""

    def __init__(self, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None]):
        self._load_cfg, self._save_cfg = load_cfg, save_cfg

    def _all(self) -> dict:
        d = self._load_cfg().get(CONFIG_KEY)
        return d if isinstance(d, dict) else {}

    def get(self, device: str = DEVICE_LOCAL) -> DeviceRecord:
        rec = (self._all().get("devices") or {}).get(device)
        if not isinstance(rec, dict):
            return DeviceRecord(device, copy.deepcopy(DEFAULT_SETTINGS))
        settings, _ = normalise_settings(rec.get("settings"))
        models = {mid: o for mid, v in (rec.get("models") or {}).items() if (o := normalise_override(v))} if isinstance(rec.get("models"), dict) else {}
        return DeviceRecord(device, settings, models, str(rec.get("build") or ""), list(rec.get("reset") or []), str(rec.get("hash") or ""), saved=True)

    def _put(self, rec: DeviceRecord) -> DeviceRecord:
        cfg = self._load_cfg()
        block = dict(cfg.get(CONFIG_KEY) or {})
        devices = dict(block.get("devices") or {})
        data = {"build": rec.build, "settings": rec.settings, "models": rec.models, "reset": rec.reset}
        data["hash"] = rec.hash = fingerprint(data)
        devices[rec.device] = data
        block["devices"] = devices
        cfg[CONFIG_KEY] = block
        self._save_cfg(cfg)
        rec.saved = True
        return rec

    def save(self, device: str, settings: Optional[dict], models: Optional[dict], build: str) -> DeviceRecord:
        """Save a device's settings and/or its per-model overrides (`None` leaves that part as it was). A model whose override is `{}` or None goes back
        to following the device. Saving counts as the player having seen what was reset, so the list is cleared."""
        rec = self.get(device)
        if settings is not None:
            rec.settings, _ = normalise_settings(settings, base=rec.settings)
        if models is not None:
            for mid, ov in models.items():
                clean = normalise_override(ov)
                if clean:
                    rec.models[str(mid)] = clean
                else:
                    rec.models.pop(str(mid), None)
        rec.build, rec.reset = build, []
        return self._put(rec)

    def reset_to_defaults(self, device: str, build: str, model_id: Optional[str] = None) -> DeviceRecord:
        """The whole device back to the defaults, or (with `model_id`) one model back to following the device."""
        rec = self.get(device)
        if model_id:
            rec.models.pop(model_id, None)
        else:
            rec.settings, rec.models = copy.deepcopy(DEFAULT_SETTINGS), {}
        rec.build, rec.reset = build, []
        return self._put(rec)

    def replace_all(self, device: str, settings: dict, models: dict, build: str) -> DeviceRecord:
        """Make a device's record EXACTLY this (settings, per-model overrides, build): what a device that is sent its settings does. Nothing of the old record is
        kept (a model the new record no longer overrides follows the device again)."""
        clean, _ = normalise_settings(settings)
        ovs = {str(mid): o for mid, v in (models or {}).items() if (o := normalise_override(v))} if isinstance(models, dict) else {}
        return self._put(DeviceRecord(device, clean, ovs, str(build or ""), []))

    def forget_model(self, device: str, model_id: str) -> None:
        rec = self.get(device)
        if rec.saved and model_id in rec.models:
            rec.models.pop(model_id)
            self._put(rec)

    def revalidate(self, device: str, build: str, known_flags: Optional[set] = None) -> DeviceRecord:
        """Called when a model starts. A device that saved settings against another engine build is checked again: everything no longer valid goes back to
        its default and is listed in `reset` (kept until the player saves again). Nothing saved, or the same build: nothing happens."""
        rec = self.get(device)
        if not rec.saved or rec.build == build:
            return rec
        rec.settings, reset = validate_for_build(rec.settings, known_flags)
        rec.reset, rec.build = reset, build
        return self._put(rec)

    def export(self, device: str) -> dict:
        """What is sent to a device that runs models for this one: its settings, its per-model overrides and the fingerprint."""
        rec = self.get(device)
        return {"device": device, "settings": rec.settings, "models": rec.models, "build": rec.build, "hash": rec.hash or fingerprint(rec.to_json())}

    def effective(self, device: str, model_id: str) -> tuple:
        """-> (device settings, that model's own {ctx, parallel})."""
        rec = self.get(device)
        return rec.settings, rec.models.get(model_id, {})


# --------------------------------------------------------------------------------------------
# Graphics card facts, the default for "memory to keep free", the presets
# --------------------------------------------------------------------------------------------
@dataclass
class HardwareFacts:
    name: str = ""
    vendor: str = ""                       # nvidia | amd | intel | other | "" (no card found)
    card_count: int = 0
    memory_mb: Optional[int] = None

    @property
    def one_nvidia_card(self) -> bool:
        return self.vendor == "nvidia" and self.card_count == 1

    @property
    def experimental(self) -> bool:
        """Anything but one NVIDIA card: the memory suggestions were only measured there (design/local-model-settings-engines.md, "Open risks")."""
        return self.card_count > 1 or self.vendor in ("amd", "intel", "other")

    def to_json(self) -> dict:
        return {"name": self.name, "vendor": self.vendor, "cardCount": self.card_count, "memoryMb": self.memory_mb,
                "memoryGb": round(self.memory_mb / 1024, 1) if self.memory_mb else None, "experimental": self.experimental}


def resolve_fit_target_mb(settings: dict, *, has_game: bool, skyrim_peak_mb: Optional[int], card_mb: Optional[int]) -> tuple:
    """-> (megabytes to keep free, where the number came from). A number the player set is used as it is. Automatic is worked out: on a device with the game, Skyrim's
    learned busiest point plus a margin (before it is learned: a quarter of the card, 3 to 10 GB, plus the margin); on a device with no game, 1 GB."""
    v = settings.get("fitTargetMb", "auto")
    if v != "auto" and _int(v) is not None:
        return int(v), "yours"
    if not has_game:
        return NO_GAME_FIT_MB, "no game on this device"
    if skyrim_peak_mb:
        return int(skyrim_peak_mb) + FIT_MARGIN_MB, "your game's busiest point"
    if card_mb:
        import local_models as lm
        card = card_mb * 1024 * 1024
        share = min(max(int(card * lm.SKYRIM_SHARE_FRACTION), lm.SKYRIM_SHARE_MIN), lm.SKYRIM_SHARE_MAX)
        return share // (1024 * 1024) + FIT_MARGIN_MB, "a guess (the game has not been measured yet)"
    return UNKNOWN_CARD_FIT_MB, "a guess (the card size is unknown)"


def _suggested(hw: HardwareFacts, has_game: bool) -> dict:
    """The suggested settings for ONE NVIDIA card (design/local-model-settings-engines.md, section 8). Layers are Automatic everywhere: llama.cpp fits the
    model to what is free and keeps the "memory to keep free" number free."""
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update({"layers": "auto", "preset": "suggested"})
    gb = (hw.memory_mb or 0) / 1024
    if gb < 10:                                        # 8 GB class: the smallest model does not fit beside the game; the cheapest cache, one request
        s.update({"parallel": 1, "ctx": 8192, "cacheType": "q8_0"})
    elif gb < 14:                                      # 12 GB class
        s.update({"parallel": 1, "ctx": 16384, "cacheType": "q8_0"} if has_game else {"parallel": 2, "ctx": 32768, "cacheType": "q8_0"})
    elif gb < 20:                                      # 16 GB class
        s.update({"parallel": 1, "ctx": 16384, "cacheType": "q8_0"} if has_game else {"parallel": 2, "ctx": 32768, "cacheType": "f16"})
    else:                                              # 24 GB and up
        s.update({"parallel": 2, "ctx": 32768, "cacheType": "f16"})
    return s


def presets_for(hw: HardwareFacts, has_game: bool, skyrim_peak_mb: Optional[int]) -> list:
    """The presets the screen offers, each `{id, label, blurb, settings, enabled, experimental, reason}`. Suggestions are built only for one NVIDIA card; any
    other graphics setup gets the safe defaults (MultiProxy's own, which are known to start) with the Experimental label and no memory suggestion."""
    labels = {
        "suggested": ("Suggested for this device", "Chosen for this graphics card. Fast, and leaves room for the game." if has_game else "Chosen for this graphics card."),
        "fastest": ("Fastest replies", "One request at a time, as fast as it can go."),
        "share": ("Share the card", "Leaves more graphics memory free for the game." if has_game else "Leaves more graphics memory free."),
    }
    if hw.experimental or not hw.memory_mb or hw.card_count == 0:
        why = ("This graphics setup is new to MultiProxy, so it starts with the safe settings." if hw.experimental
               else "MultiProxy can't read this device's graphics card, so it starts with the safe settings.")
        safe = copy.deepcopy(DEFAULT_SETTINGS)
        safe["preset"] = "suggested"
        out = [{"id": "suggested", "label": labels["suggested"][0], "blurb": why, "settings": safe, "enabled": True, "experimental": hw.experimental, "reason": ""}]
        for pid in ("fastest", "share"):
            out.append({"id": pid, "label": labels[pid][0], "blurb": labels[pid][1], "settings": None, "enabled": False, "experimental": hw.experimental,
                        "reason": "Memory suggestions are only built for one NVIDIA card so far."})
    else:
        sug = _suggested(hw, has_game)
        fast = dict(sug, preset="fastest", parallel=1, ctx=max(8192, sug["ctx"] // max(1, sug["parallel"])))
        target, _ = resolve_fit_target_mb(sug, has_game=has_game, skyrim_peak_mb=skyrim_peak_mb, card_mb=hw.memory_mb)
        share = dict(sug, preset="share", fitTargetMb=target + SHARE_EXTRA_MB, cacheType="q8_0" if sug["cacheType"] == "f16" else sug["cacheType"])
        out = [{"id": pid, "label": labels[pid][0], "blurb": labels[pid][1], "settings": st, "enabled": True, "experimental": False, "reason": ""}
               for pid, st in (("suggested", sug), ("fastest", fast), ("share", share))]
    out.append({"id": "custom", "label": "Custom", "blurb": "You changed something below.", "settings": None, "enabled": True,
                "experimental": hw.experimental, "reason": ""})
    return out


def detect_preset(settings: dict, presets: list) -> str:
    """Which preset these settings are (`default` when they are MultiProxy's own defaults, `custom` when they match nothing)."""
    def core(s):
        return {k: s.get(k) for k in SETTING_KEYS if k != "preset"}
    if core(settings) == core(DEFAULT_SETTINGS):
        return "default"
    for p in presets:
        if p["settings"] and core(p["settings"]) == core(settings):
            return p["id"]
    return "custom"


# --------------------------------------------------------------------------------------------
# The largest prompt each model has been sent
# --------------------------------------------------------------------------------------------
class PromptPeaks:
    """The most tokens one request to each local model has carried, saved as data (`local_prompt_peaks`: `{model id: {"tokens", "date"}}`). Nothing uses it yet:
    today's memory per request (16,384) is a guess at "big enough for SkyrimNet", and a later item replaces the guess with what the proxy has really seen.
    Written only when a model's number goes up, so it costs next to nothing once the game's longest scenes have been through."""

    def __init__(self, load_cfg: Callable[[], dict], save_cfg: Callable[[dict], None]):
        self._load_cfg, self._save_cfg = load_cfg, save_cfg
        self._seen: Optional[dict] = None

    def _load(self) -> dict:
        if self._seen is None:
            raw = self._load_cfg().get(PEAKS_KEY)
            self._seen = {str(k): int(v["tokens"]) for k, v in (raw or {}).items() if isinstance(v, dict) and _int(v.get("tokens"))} if isinstance(raw, dict) else {}
        return self._seen

    def note(self, model_id: str, tokens, today: Optional[datetime.date] = None) -> bool:
        """True when `tokens` is a new largest prompt for the model (and was saved)."""
        n = _int(tokens)
        if not model_id or n is None or n <= 0:
            return False
        seen = self._load()
        if n <= seen.get(model_id, 0):
            return False
        seen[model_id] = n
        cfg = self._load_cfg()
        block = dict(cfg.get(PEAKS_KEY) or {})
        block[model_id] = {"tokens": n, "date": (today or datetime.date.today()).isoformat()}
        cfg[PEAKS_KEY] = block
        self._save_cfg(cfg)
        return True

    def peak(self, model_id: str) -> Optional[int]:
        return self._load().get(model_id)

    def all(self) -> dict:
        return dict(self._load())


# --------------------------------------------------------------------------------------------
# The setup a model's speed was measured under (the Speeds tab)
# --------------------------------------------------------------------------------------------
PRESET_WORDS = {"default": "Default settings", "suggested": "Suggested", "fastest": "Fastest replies", "share": "Share the card", "custom": "Custom"}


def setup_fingerprint(settings: dict, override: Optional[dict], build: str, skip_extras: bool = False) -> str:
    """A short fingerprint of everything that changes how fast a model runs: the device settings and the model's own overrides in effect when its engine STARTED
    (a save only counts once the model restarts), plus the engine build. The preset name is a label, not a setting, so it is left out. Saved with every local
    request's speed record so numbers measured under different setups are never mixed."""
    keys = ("layers", "fitTargetMb", "ctx", "parallel", "flashAttn", "cacheType", "extraArgs")
    eff = {k: settings.get(k) for k in keys}
    eff.update(normalise_override(override))
    blob = json.dumps({"s": eff, "build": build, "noExtras": bool(skip_extras)}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def setup_words(settings: dict, override: Optional[dict]) -> str:
    """The setup in plain words for the Speeds table: "Fastest replies · 32,768 memory · 1 at a time"."""
    ov = normalise_override(override)
    ctx, par = ov.get("ctx") or settings.get("ctx") or DEFAULT_CTX, ov.get("parallel") or settings.get("parallel") or DEFAULT_PARALLEL
    return f"{PRESET_WORDS.get(settings.get('preset'), 'Custom')} · {int(ctx):,} memory · {int(par)} at a time"
