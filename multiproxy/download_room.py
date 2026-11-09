"""Is there graphics memory to spare before a local engine or model is downloaded? (2026-10-27)

The director pressed Download for the llama.cpp engine while Skyrim was running on a card with about 4 GB to spare, started it, and the game and the whole system crashed.
The Local device page now asks this module first. It only ever warns: "Download anyway" always goes ahead, and nothing is remembered.

Memory left for models = the card's total, minus Skyrim's highest use (the 14-day tracker, or what the game is using right now if that is more; a quarter-of-the-card guess when
neither is known, the fit check's own rule), minus what the other programs use now, minus the models already running. A model needs its size plus the fit check's working room.
The engine alone is small: it warns when less than ENGINE_FLOOR_MB is left, or when Skyrim is running and the free memory right now is under that floor.
A device with no reading, or whose card is not an NVIDIA one (the readings come from NVIDIA's tools), says nothing at all. Pure functions: the route passes in the readings.
"""
from typing import Optional

import local_models

ENGINE_FLOOR_MB = 2048            # the engine alone needs about this much to start and hold a little; below it a warning shows

# Every word this feature shows. One place for the design side's wording pass.
WORDS = {
    "model": "Only about {gb} GB of graphics memory is free{next_to}, which is too little for {name}. Use a cloud provider or another device, or download anyway.",
    "engine": "Only about {gb} GB of graphics memory is free{next_to}, which is too little to run a local model. Use a cloud provider or another device, or download anyway.",
    "nextTo": " next to Skyrim",
}                                  # (the two buttons, "Cancel" and "Download anyway", are LM_ROOM_WORDS in local_models_ui.py)


def _gb(mb: float) -> str:
    return f"{max(0.0, mb) / 1024:.1f}"


def left_for_models_mb(sample, peak_mb: Optional[int], model_server: bool, has_game: bool = True) -> Optional[float]:
    """The graphics memory a new model could use, in MB; None when this device gives no reading."""
    if sample is None or not sample.total_mb:
        return None
    skyrim = 0.0
    if has_game and not model_server:
        known = max(peak_mb or 0, sample.skyrim_mb or 0)
        if known:
            skyrim = known
        else:
            card = sample.total_mb * 1024 * 1024
            skyrim = min(max(int(card * local_models.SKYRIM_SHARE_FRACTION), local_models.SKYRIM_SHARE_MIN), local_models.SKYRIM_SHARE_MAX) / (1024 * 1024)
    return sample.total_mb - skyrim - sample.other_mb - sum(sample.models.values())


def check(what: str, sample, gpu, peak_mb: Optional[int], model_server: bool, has_game: bool = True,
          model_bytes: int = 0, model_name: str = "") -> dict:
    """{"warn": False} or {"warn": True, "line": the warning, "freeMb", "needMb"}. `what` is "engine" or "model"."""
    if gpu is None or getattr(gpu, "vendor", "") != "nvidia" or sample is None:
        return {"warn": False}
    left = left_for_models_mb(sample, peak_mb, model_server, has_game)
    if left is None:
        return {"warn": False}
    game_on = bool(sample.skyrim_running) and not model_server
    if what == "engine":
        need = ENGINE_FLOOR_MB
        short = left < need or (game_on and sample.free_mb < need)
        shown = min(left, sample.free_mb) if game_on else left
        key = "engine"
    else:
        need = (model_bytes + local_models.WORKING_ROOM_BYTES) / (1024 * 1024)
        short = left < need
        shown = left
        key = "model"
    if not short:
        return {"warn": False}
    line = WORDS[key].format(gb=_gb(shown), next_to=WORDS["nextTo"] if (has_game and not model_server) else "", name=model_name or "this model")
    return {"warn": True, "line": line, "freeMb": int(max(0, shown)), "needMb": int(need)}
