"""Benchmarks: the background (contract sections 12 and 12b), as pure code.

The background is what the test device already runs next to the model being tested: models and voice engines that are loaded now, and (on the "start normal" answer) the services
MultiProxy has assigned to the device that are stopped. Nothing here starts or stops anything; the runner does that through its hooks. An entry is a plain dict:

    {"id", "kind" ("chat" | "voice" | "decision" | "stt"), "name", "state" ("loaded" | "assigned_stopped"),
     "vram_mb": {"<gpu_index>": MB} or None   (what it holds now; None: MultiProxy has no figure, and nothing is guessed),
     "size_mb": MB or None   (a stopped service's last known or catalogue size, always shown as "when running"),
     "started_by_multiproxy": bool   (only these are ever stopped by a run)}
"""
from typing import Iterable, Optional

import benchmarks_contract as bc

BACKGROUND_STOP, BACKGROUND_KEEP, BACKGROUND_START_NORMAL = "stop", "keep", "start_normal"
BACKGROUND_CHOICES = (BACKGROUND_STOP, BACKGROUND_KEEP, BACKGROUND_START_NORMAL)
STATE_LOADED, STATE_ASSIGNED_STOPPED = "loaded", "assigned_stopped"
SIZE_NOTE_WHEN_RUNNING = "when running"
KINDS = ("chat", "voice", "decision", "stt")

# Said to the person (the screen shows them as they are; the design side runs them through a Gemini pass).
REFUSE_BACKGROUND_FIT = "{name} won't fit on the graphics card next to what is already running ({others}). Stop some of them, or pick another model."
REFUSE_BACKGROUND_START = "Couldn't start {name}, so the test didn't begin."


def _num(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def normalize_item(item: dict) -> dict:
    """One clean entry from whatever a source gave: unknown kinds become `chat`, a figure that is not a number is dropped (None), the state is one of the two."""
    vram = item.get("vram_mb")
    if isinstance(vram, dict):
        vram = {str(k): _num(v) for k, v in vram.items() if _num(v) is not None} or None
    else:
        vram = None
    kind = item.get("kind") if item.get("kind") in KINDS else "chat"
    state = STATE_ASSIGNED_STOPPED if item.get("state") == STATE_ASSIGNED_STOPPED else STATE_LOADED
    out = {"id": str(item.get("id", "")), "kind": kind, "name": str(item.get("name") or item.get("id") or ""), "state": state, "vram_mb": vram,
           "size_mb": _num(item.get("size_mb")), "started_by_multiproxy": bool(item.get("started_by_multiproxy", False))}
    if state == STATE_ASSIGNED_STOPPED:
        out["size_note"] = SIZE_NOTE_WHEN_RUNNING
    return out


def normalize(items: Optional[Iterable[dict]]) -> list:
    return [normalize_item(i) for i in (items or []) if isinstance(i, dict) and i.get("id")]


def loaded_only(items: Iterable[dict]) -> list:
    return [i for i in items if i.get("state") == STATE_LOADED]


def stoppable(loaded: Iterable[dict]) -> list:
    """The loaded entries a run may stop on "Stop others": only the ones MultiProxy started."""
    return [i for i in loaded if i.get("started_by_multiproxy")]


def total_by_gpu(items: Iterable[dict], use_size_for_unknown: bool = False) -> dict:
    """What the entries hold, per card (`{"0": MB}`). With `use_size_for_unknown`, an entry with no figure for any card counts its `size_mb` (a stopped service "when running") on
    card `"any"`, which the caller applies to the card it tests against. Unknown stays unknown: an entry with no figure and no size adds nothing."""
    out: dict = {}
    for i in items:
        v = i.get("vram_mb")
        if v:
            for k, mb in v.items():
                out[k] = out.get(k, 0) + mb
        elif use_size_for_unknown and i.get("size_mb"):
            out["any"] = out.get("any", 0) + i["size_mb"]
    return out


def biggest_on_one_card_mb(items: Iterable[dict], use_size_for_unknown: bool = False) -> float:
    """The most the entries hold on any single card, with the "any" amount added to it: the number the "will it fit next to them" check uses (the safe side when cards differ)."""
    t = total_by_gpu(items, use_size_for_unknown)
    anywhere = t.pop("any", 0)
    return (max(t.values()) if t else 0) + anywhere


def setup_part(loaded: Iterable[dict]) -> list:
    """What goes into the setup id: who is loaded (kind, id, name), sorted. Not the memory figures: they wobble from run to run, and the same background must give the same id."""
    return sorted(({"kind": i["kind"], "id": i["id"], "name": i["name"]} for i in loaded), key=lambda d: (d["kind"], d["id"]))


def names(items: Iterable[dict]) -> str:
    return ", ".join(i["name"] for i in items)


def normalize_choice(value) -> str:
    """`stop` | `keep` | `start_normal`; anything else is rejected by the caller (None here)."""
    return value if value in BACKGROUND_CHOICES else ""


# --- pairs: the same test, with and without the background ------------------------------------------------------------------------------------------------------------------

def sharing_setup(runs: Iterable[dict], run_id: str) -> list:
    """From manifests (as `benchmarks_store.load_manifest_only` gives them), the runs that are the same test as `run_id` apart from the background: the same model and the same
    `setup_core_id` (the setup id without its background), but another background. Newest first, each with its `background_names` so a screen can say "alone" / "with Higgs"."""
    runs = list(runs)
    mine = next((r for r in runs if r.get("run_id") == run_id), None)
    if not mine or not mine.get("setup_core_id"):
        return []
    mine_bg = mine.get("background_key", "")
    out = [r for r in runs if r.get("run_id") != run_id and r.get("model_id") == mine.get("model_id") and r.get("setup_core_id") == mine["setup_core_id"]
           and r.get("background_key", "") != mine_bg]
    out.sort(key=lambda r: r.get("started_epoch_s") or 0, reverse=True)
    return [{"run_id": r.get("run_id"), "status": r.get("status"), "setup_id": r.get("setup_id"), "background_names": r.get("background_names", []),
             "pair_of": r.get("pair_of"), "started_epoch_s": r.get("started_epoch_s")} for r in out]


def background_key(loaded: Iterable[dict]) -> str:
    """A short, stable text for 'which background' (ids sorted): equal backgrounds give equal keys."""
    return bc.canonical_json(setup_part(loaded))
