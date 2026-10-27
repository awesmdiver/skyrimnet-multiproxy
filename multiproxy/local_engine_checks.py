"""The checks on a set of local-model settings (2026-10-04): one module, one list of `{level, code, text}` items the screens can show.

Built to design/local-model-settings-engines.md (section 6 lists what crashes or quietly costs a lot in the pinned build) and the Gemini review.

Levels: `error` (the engine would refuse it or crash: saving is refused), `warning` (it runs but costs something real: saving is allowed and the screen says
why), `info` (a fact the screen can show or use, for example to grey a box out). Extra fields tell the screen what to do: `field` (which box it is about),
`strong` (a warning that should be loud), `disabled` (grey the box out), `experimental` (show the Experimental label).

`run_checks` is pure: everything it needs (the model file's facts, the graphics card, the estimate) is passed in, so tests need no hardware.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import gguf_header
import local_engine_flags as flags
import local_engine_memory as memory
import local_engine_settings as eset

PER_REQUEST_FLOOR = 4096             # below this a request cannot even hold SkyrimNet's shortest scene: refused
PER_REQUEST_WARN = 16384             # below this long scenes may not fit (until the proxy has measured what SkyrimNet really sends: see PromptPeaks)
BATCH_FLOOR = 2                      # a batch of 0 crashes the engine and a batch of 1 crashes it at warm-up (both run); anything smaller is refused
BATCH_SLOW = 128                     # below this the engine runs but chews prompts slowly
SLOW_PAIRS_OK = ("f16", "q8_0", "q4_0", "bf16")


@dataclass
class Check:
    level: str
    code: str
    text: str
    field: str = ""
    strong: bool = False
    disabled: bool = False
    experimental: bool = False

    def to_json(self) -> dict:
        out = {"level": self.level, "code": self.code, "text": self.text}
        for k in ("field", "strong", "disabled", "experimental"):
            if getattr(self, k):
                out[k] = getattr(self, k)
        return out


def _batch_values(extras) -> list:
    out = []
    for names in (("-b", "--batch-size"), ("-ub", "--ubatch-size")):
        v = flags.extra_value(extras, *names)
        if v is not None:
            try:
                out.append((names[1], int(v)))
            except ValueError:
                pass
    return out


def run_checks(settings: dict, *, override: Optional[dict] = None, facts: Optional[gguf_header.ModelFacts] = None, hardware: Optional[eset.HardwareFacts] = None,
               estimate: Optional[memory.MemoryEstimate] = None, model_name: str = "", gpu_engine: bool = True) -> list:
    """Every check that applies to a device's `settings` (and, with `override`, `facts` and `model_name`, to one model on it). `estimate` is the memory
    estimate for the same setup; `hardware` says what card the device has."""
    ov = override or {}
    out: list = []
    ctx = int(ov.get("ctx") or settings.get("ctx") or eset.DEFAULT_CTX)
    parallel = int(ov.get("parallel") or settings.get("parallel") or eset.DEFAULT_PARALLEL)
    layers, cache, flash = settings.get("layers", "all"), settings.get("cacheType", "f16"), settings.get("flashAttn", "auto")
    extras = settings.get("extraArgs") or []
    who = f"{model_name}: " if model_name else ""

    # --- crashes and refusals -------------------------------------------------------------
    if cache != "f16" and flash == "off":
        out.append(Check("error", "cache-needs-flash-attention", f"{who}The smaller cache needs flash attention. Set it to Auto or On, or switch the cache back to full size. Otherwise, the model won't start.",
                         field="flashAttn"))
    for name, value in _batch_values(extras):
        if value < BATCH_FLOOR:
            out.append(Check("error", "batch-too-small", f"{who}A batch size of {value} crashes the engine ({name}). Remove it from your flags, or use 128 or more.", field="extraArgs"))
        elif value < BATCH_SLOW:
            out.append(Check("warning", "batch-slow", f"{who}A batch size of {value} ({name}) makes long prompts slow. The engine's own numbers are right for most devices.", field="extraArgs"))
    if parallel < 1:
        out.append(Check("error", "parallel-too-small", f"{who}Requests at the same time needs to be 1 or more.", field="parallel"))
    if ctx < 1:
        out.append(Check("error", "context-too-small", f"{who}Memory per conversation needs to be more than 0.", field="ctx"))

    # --- memory per request ---------------------------------------------------------------
    per_request = ctx // max(1, parallel)
    if per_request < PER_REQUEST_FLOOR:
        out.append(Check("error", "per-request-floor", f"{who}Each request gets only {per_request:,} tokens (about {max(1, round(per_request / flags.PAGE_TOKENS))} pages). "
                         f"Below {PER_REQUEST_FLOOR:,}, even a short scene won't fit. Raise the memory or lower the requests at the same time.", field="ctx"))
    elif per_request < PER_REQUEST_WARN:
        out.append(Check("warning", "per-request-low", f"{who}Each request gets {per_request:,} tokens (about {round(per_request / flags.PAGE_TOKENS)} pages). "
                         f"Below {PER_REQUEST_WARN:,}, SkyrimNet's long scenes may not fit.", field="ctx"))
    lane = -(-(ctx // max(1, parallel)) // 256) * 256 if parallel >= 1 else 0
    if parallel >= 1 and lane * parallel != ctx and ctx >= 256:
        out.append(Check("info", "total-grows", f"{who}{parallel} requests don't divide {ctx:,} evenly, so the engine rounds each one up and the total becomes {lane * parallel:,}.", field="ctx"))
    if facts is not None and facts.trained_ctx and per_request > facts.trained_ctx:
        out.append(Check("warning", "ctx-above-trained", f"{who}Each request gets {per_request:,} tokens but this model was only trained on {facts.trained_ctx:,}. "
                         f"MultiProxy would still set aside memory for all of it, and the model can't use the extra.", field="ctx"))

    # --- layers ---------------------------------------------------------------------------
    if gpu_engine and layers != "auto":
        out.append(Check("info", "fit-target-ignored", "Memory to keep free only works when graphics card layers is on Automatic.", field="fitTargetMb", disabled=True))
    if gpu_engine and isinstance(layers, int):
        total = facts.offload_layers if facts else None
        if total is None or layers < total:
            out.append(Check("warning", "partial-offload", f"{who}Part of the model would run on the processor, which is about 7 times slower. "
                             f"Use Automatic to let the engine fit as much as it can on the card.", field="layers", strong=True))
    elif gpu_engine and layers == "none" and hardware is not None and hardware.card_count > 0:
        out.append(Check("warning", "no-offload", f"{who}None puts the whole model on the processor, which is many times slower than your graphics card.", field="layers", strong=True))

    # --- the hardware ---------------------------------------------------------------------
    if hardware is not None and hardware.experimental:
        why = "more than one graphics card" if hardware.card_count > 1 else f"an {hardware.vendor.upper() if hardware.vendor == 'amd' else hardware.vendor.title()} graphics card"
        out.append(Check("info", "experimental-hardware", f"This device has {why}. MultiProxy's memory suggestions were only tested on one NVIDIA card, so it starts with the safe settings.",
                         experimental=True))

    # --- what is in the player's own flags ------------------------------------------------
    if flags.has_extra_flag(extras, "--swa-full"):
        out.append(Check("warning", "swa-full", f"{who}--swa-full can use about 7 times the cache memory on some models (Gemma 4) and shows no error.", field="extraArgs", strong=True))
    k, v = flags.extra_value(extras, "-ctk", "--cache-type-k"), flags.extra_value(extras, "-ctv", "--cache-type-v")
    if k or v:
        kk, vv = k or cache, v or cache
        if "iq4_nl" in (kk, vv):
            out.append(Check("warning", "cache-very-slow", f"{who}The iq4_nl cache type is about 7 times slower on graphics cards.", field="extraArgs", strong=True))
        elif kk != vv or kk not in SLOW_PAIRS_OK or vv not in SLOW_PAIRS_OK:
            out.append(Check("warning", "cache-pair-slow", f"{who}This pair of cache types ({kk} and {vv}) has no fast path on many graphics cards, so the engine falls back to a slow one.", field="extraArgs"))
        if vv not in ("f16", "f32", "bf16") and flash == "off":
            out.append(Check("error", "cache-needs-flash-attention", f"{who}A smaller cache needs flash attention. Set it to Auto or On.", field="flashAttn"))
    unknown = [it for it in flags.parse_command_line(extras).extra_items if not it["known"]]
    if unknown:
        names = ", ".join(it["flag"] for it in unknown)
        out.append(Check("info", "extra-flags-untested", f"MultiProxy hasn't tested these flags: {names}. They're passed to the engine exactly as typed.", field="extraArgs"))

    # --- does it fit ----------------------------------------------------------------------
    if estimate is not None and estimate.total_mb is not None and estimate.card_mb and estimate.total_mb > estimate.card_mb:
        out.append(Check("warning", "memory-too-big", f"{who}These settings need {estimate.summary} of graphics memory. They probably won't fit. The model may run much slower, or Skyrim may stutter. "
                         f"Try a smaller memory size or the smaller cache.", field="ctx", strong=True))
    out.sort(key=lambda c: {"error": 0, "warning": 1, "info": 2}[c.level])
    seen, deduped = set(), []
    for c in out:
        if (c.code, c.text) not in seen:
            seen.add((c.code, c.text))
            deduped.append(c)
    return deduped


def has_errors(checks: list) -> bool:
    return any(c.level == "error" for c in checks)
