"""Benchmarks protect the PC, step 2: how much graphics memory a model really needs for THIS test, not for the file.

A model's file size says little about a test at 8 requests at once: every request gets its own slot, and each slot reserves its own conversation memory (the "KV cache"). On
2026-10-28 a 18.7 GB model on a 31.8 GB card needed about 22 GB at 1 slot and about 39 GB at 8: the card filled up, the rest spilled into the computer's main memory and the PC
crawled. This module says that BEFORE the test starts. It is pure (no I/O except reading a model file's header on request) so it is tested with made-up numbers.

Where the number comes from, in order of preference:
  1. `measured`: a finished run of the very same configuration (model file and size, engine build, slots, context, cache types, offload, flash attention) that was not capped by the
     card: its own highest memory use at that number of requests minus the card's use before the load. A one-slot peak is never an eight-slot estimate; a run that filled the card is
     never a measurement of what the model NEEDS.
  2. `header`: `local_engine_memory.estimate_from_facts`, the project's measured estimate (design/local-model-settings-engines.md, section 4): the weights are the file, the
     conversation cache follows exactly from the model file's header (it matched what the engine really allocated, to the megabyte, in 18 runs on four model families), the engine's
     working memory and overhead are measured constants. Confidence is `good`.
  3. `fallback`: the file size times 1.05 plus a per-token constant (scaled by the file's size) for the context, when the header cannot be read or lacks the numbers (a new kind of
     model). Confidence is `weak`.
llama.cpp's own dry-run tool (`llama-fit-params`) agrees with the header formula but takes 1 to 25 s a model, is missing from some builds, and under-counts the working memory of a
few models, so it is NOT used here: the dot in the model list and the question at Start must give the same answer, quickly.

The margin is the larger of 1 GiB and 15% of the estimate. It is not a claim about accuracy; it only decides the colour: over the free memory = too big; inside the margin = tight.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import local_engine_memory as lem

MIB = 1024 * 1024
MARGIN_FLOOR_MB = 1024
MARGIN_PCT = 15.0
FALLBACK_FILE_FACTOR = 1.05                    # weights on the card, a little above the file
FALLBACK_MB_PER_TOKEN_PER_GB = 0.025           # conversation memory per reserved token for each GB of model file when nothing better is known: measured 0.115 MB a token for the 18.7 GB Gemma 31B
                                               # (0.006 per GB) and about 0.125 for a plain 8B model of 4.9 GB (0.025 per GB), so this is the larger of the two: it never under-counts either
SATURATED_PCT = 98.0                           # a run that filled the card to this share says nothing about how much the model needs

CONFIDENCE_GOOD, CONFIDENCE_WEAK = "good", "weak"
SOURCE_MEASURED, SOURCE_HEADER, SOURCE_FALLBACK = "measured", "header", "fallback"
FITS, TIGHT, TOO_BIG = "fits", "tight", "too_big"


@dataclass
class Need:
    need_mb: int
    margin_mb: int
    confidence: str
    source: str
    slots: int
    per_slot_tokens: int
    ctx: int
    parts: dict = field(default_factory=dict)
    reason: str = ""

    def to_json(self) -> dict:
        return {"needMb": self.need_mb, "marginMb": self.margin_mb, "confidence": self.confidence, "source": self.source, "slots": self.slots, "perSlotTokens": self.per_slot_tokens,
                "ctx": self.ctx, "parts": self.parts, "reason": self.reason}


def margin_mb(need_mb: float) -> int:
    return int(max(MARGIN_FLOOR_MB, round(need_mb * MARGIN_PCT / 100.0)))


def run_settings(device_settings: dict, overrides: dict, slots: int, per_slot_tokens: int, default_ctx: int = 8192) -> tuple:
    """The settings the test's engine really starts with at `slots` requests at once: the device's own settings, the plan's changes, and a context of at least `per_slot_tokens`
    for each slot. -> (settings, {"ctx", "parallel"}). The launcher uses this very function, so the estimate and the run cannot drift apart."""
    settings = dict(device_settings or {})
    for k in ("layers", "flashAttn", "cacheType"):
        if k in (overrides or {}):
            settings[k] = overrides[k]
    ctx = max(int((overrides or {}).get("ctx") or settings.get("ctx") or default_ctx), int(per_slot_tokens) * int(slots))
    return settings, {"ctx": ctx, "parallel": int(slots)}


def estimate_gpu_need(size_bytes: int, facts, settings: dict, slots: int, per_slot_tokens: int, overrides: Optional[dict] = None, mmproj_size: Optional[int] = None,
                      measured_mb: Optional[float] = None, tensors=None) -> Need:
    """The graphics memory the engine takes for a test at `slots` requests at once, with `per_slot_tokens` reserved for each. `facts` is a `gguf_header.ModelFacts` or None."""
    settings, override = run_settings(settings, overrides or {}, slots, per_slot_tokens)
    ctx = override["ctx"]
    if measured_mb is not None and measured_mb > 0:
        n = int(round(measured_mb))
        return Need(n, margin_mb(n), CONFIDENCE_GOOD, SOURCE_MEASURED, slots, per_slot_tokens, ctx, {"measuredMb": n})
    est = lem.estimate_from_facts(int(size_bytes), mmproj_size, facts, settings, override, None, tensors)
    if est.total_mb is not None and "the conversation cache" in est.exact:
        return Need(est.total_mb, margin_mb(est.total_mb), CONFIDENCE_GOOD, SOURCE_HEADER, slots, per_slot_tokens, ctx,
                    {"weightsMb": est.weights_mb, "visionMb": est.vision_mb, "cacheMb": est.cache_mb, "computeMb": est.compute_mb, "engineMb": est.process_mb})
    weights = 0 if settings.get("layers") == "none" else int(size_bytes * FALLBACK_FILE_FACTOR / MIB)
    cache = int(FALLBACK_MB_PER_TOKEN_PER_GB * (size_bytes / 1e9) * ctx)
    vision = int(mmproj_size / MIB) + lem.VISION_EXTRA_MB if mmproj_size else 0
    total = weights + cache + vision + lem.COMPUTE_MIN_MB + lem.PROCESS_OVERHEAD_MB
    return Need(total, margin_mb(total), CONFIDENCE_WEAK, SOURCE_FALLBACK, slots, per_slot_tokens, ctx,
                {"weightsMb": weights, "visionMb": vision, "cacheMb": cache, "computeMb": lem.COMPUTE_MIN_MB, "engineMb": lem.PROCESS_OVERHEAD_MB}, est.reason or "The model's details were not available.")


def verdict(need: Need, free_mb: Optional[float]) -> Optional[str]:
    """`too_big` when the estimate is over the free memory; `tight` when it fits only inside the margin or the estimate is weak; `fits` otherwise. None when the free memory is not
    known (never a guess)."""
    if free_mb is None:
        return None
    if need.need_mb > free_mb:
        return TOO_BIG
    if need.need_mb + need.margin_mb > free_mb or need.confidence == CONFIDENCE_WEAK:
        return TIGHT
    return FITS


# --- measured runs ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


_scan_cache: dict = {}


def load_measured(base) -> list:
    """One entry per finished level of every completed run in `base`: the configuration it ran under and the memory it added at that level. Cached while the files do not change."""
    try:
        names = sorted(n for n in os.listdir(base) if os.path.isfile(os.path.join(base, n, "summary.json")) and os.path.isfile(os.path.join(base, n, "manifest.json")))
    except OSError:
        return []
    key = (str(base), tuple((n, os.path.getmtime(os.path.join(base, n, "summary.json"))) for n in names))
    if key in _scan_cache:
        return _scan_cache[key]
    out = []
    for n in names:
        m, s = _read(os.path.join(base, n, "manifest.json")), _read(os.path.join(base, n, "summary.json"))
        if not m or not s or m.get("status") != "completed":
            continue
        mem = s.get("memory") or {}
        req = m.get("requested_settings") or {}
        res = m.get("resolved_settings") or {}
        over = req.get("overrides") or {}
        for lv, summ in (s.get("levels") or {}).items():
            eng = (summ or {}).get("engine") or {}
            hi = (summ or {}).get("vram_highest_in_level_mb") or {}
            added, capped = 0.0, False
            for gpu, hv in hi.items():
                g = mem.get(gpu) or {}
                before, total = g.get("vram_idle_before_load_mb"), g.get("vram_total_mb")
                if hv is None or before is None:
                    added = None
                    break
                added += max(0.0, hv - before)
                if total and hv >= total * SATURATED_PCT / 100.0:
                    capped = True
            if not hi or added is None or not eng.get("slots"):
                continue
            out.append({"file": m.get("model_file"), "size": m.get("model_size_bytes"), "build": res.get("engine_build"), "slots": eng.get("slots"), "ctx": eng.get("ctx"),
                        "ctk": eng.get("cache_type_k"), "ctv": eng.get("cache_type_v"), "layers": res.get("layers_requested"), "flash": res.get("flash_attn_requested"),
                        "cache": res.get("cache_type_requested"), "batch": over.get("batch"), "added_mb": added, "capped": capped, "level": int(lv)})
    if len(_scan_cache) > 8:
        _scan_cache.clear()
    _scan_cache[key] = out
    return out


def measured_for(entries: list, file: str, size: int, slots: int, ctx: int, settings: dict, build: Optional[str] = None, batch=None) -> Optional[float]:
    """The memory a finished run of exactly this configuration added, or None. The configuration is the model file and its size, the engine build, the slots, the whole context,
    the cache types, the offload and the flash attention setting, and the batch size; a run that filled the card (`capped`) is never used."""
    best = None
    for e in entries or []:
        if e.get("capped") or e.get("file") != file or e.get("size") != size or e.get("slots") != slots or e.get("ctx") != ctx:
            continue
        if build is not None and e.get("build") != build:
            continue
        if e.get("layers") != settings.get("layers") or e.get("flash") != settings.get("flashAttn") or e.get("cache") != settings.get("cacheType") or e.get("batch") != batch:
            continue
        best = e["added_mb"] if best is None else max(best, e["added_mb"])
    return best


def estimate_for_plan(path: str, levels, prompts, sustained: bool, device_settings: dict, overrides: Optional[dict] = None, size_bytes: Optional[int] = None) -> Optional[dict]:
    """Our header-based guess of the card memory for one run, saved in the run's record BEFORE it starts so the Results can set it beside what the engine really took: `{"total_mb",
    "model_mb", "cache_mb", "slots"}`, or None when the model file's header could not be read (nothing is guessed from the file's size alone here). The same numbers the Start warning
    uses (`benchmarks_tab.apply_fit`). `slots` is the most requests at once the test sends (the 5-minute run uses 4)."""
    import benchmarks_testset as tset
    import gguf_header
    import gguf_tensors
    vals = [int(x) for x in (levels or []) if isinstance(x, int) and not isinstance(x, bool) and x >= 1]
    if sustained:
        vals.append(4)
    slots = max(vals or [1])
    data = tset.load()
    ps = tset.prompts_for(data, [c for c in (prompts or []) if c in tset.CLASSES] or list(tset.CLASSES))
    per_slot = tset.tokens_per_slot(ps + ([tset.sustained_prompt(data)] if sustained else []))
    facts = gguf_header.read_model_facts(path)
    if facts is None:
        return None
    size = size_bytes if size_bytes else os.path.getsize(path)
    need = estimate_gpu_need(int(size), facts, device_settings or {}, slots, per_slot, overrides or {}, None, None, gguf_tensors.read_tensor_sizes(path))
    parts = need.parts or {}
    return {"total_mb": int(need.need_mb), "model_mb": parts.get("weightsMb"), "cache_mb": parts.get("cacheMb"), "slots": slots}
