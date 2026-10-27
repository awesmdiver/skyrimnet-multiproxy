"""How much graphics memory a local model will take with a given set of settings (2026-10-04).

Built to design/local-model-settings-engines.md, section 4, where every number was measured against the pinned llama.cpp build:

  * THE CACHE IS EXACT. The memory for the conversations (the "KV cache", plus the recurrent state of the hybrid models) follows from the numbers in the
    model file's header alone. The formula below matched what the server really allocated, to the megabyte, in all 18 test runs across four model
    families (plain, sliding-window, hybrid; with and without a smaller cache). So the screens may say "the cache is exact".
  * The weights are the file's size (within 0.3 %). The rest is an ESTIMATE: the engine's own working memory ("compute buffer", it grows with the batch
    size: about 0.25 MB per batch token on most models, 1 MB on the one with a huge word list), the engine's own overhead (about 550 MB), and, for a
    vision model, the vision file plus the working memory it asks for.
  * `check_with_engine` runs llama.cpp's own dry-run tool (`llama-fit-params`, in the Windows engine zips, about one second) for a second opinion. It is
    slower and can be missing, so it runs in the background when a custom or pasted setup is saved, and its absence falls back to the estimate and says so.
    (It is exact for most models and under-counts the working memory of a few, so its number is shown as "checked", still never as a promise.)

A card that is too full does NOT stop a model starting on Windows (the driver spills into ordinary memory and everything gets slower), so nothing here can
be learned by "just starting it": the estimate has to warn before saving.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import gguf_header
import hidden_process
import local_engine_flags as flags

MIB = 1024 * 1024
BYTES_PER_VALUE = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32, "q4_1": 20 / 32, "q5_0": 22 / 32, "q5_1": 24 / 32, "iq4_nl": 18 / 32}
WEIGHTS_FACTOR = 0.9975                 # the share of the file that lands on the card (Gemma 12B: 7,024.4 MiB on the card for a 7,039.5 MiB file)
PROCESS_OVERHEAD_MB = 550               # the engine's own memory on the card (measured 500 to 550)
VISION_EXTRA_MB = 250                   # what a vision file asks for beyond its own size (Gemma's: a 167 MiB file asks for 414 MiB)
COMPUTE_MB_PER_BATCH_TOKEN = 0.25       # measured 0.1 to 0.27 on normal models
COMPUTE_MIN_MB = 128
DEFAULT_UBATCH = 512
FIT_PARAMS_TIMEOUT = 25


def _pad(n: float, m: int = 256) -> int:
    return int(math.ceil(n / m) * m)


def _per_layer(v, n: int, default=None) -> list:
    """A value that is either one number for every layer or one per layer -> one per layer."""
    if isinstance(v, list):
        if not v:
            return [default] * n
        return (v + [v[-1]] * n)[:n]
    return [v if v is not None else default] * n


def cache_bytes(facts: gguf_header.ModelFacts, ctx: int, parallel: int, ctk: str = "f16", ctv: str = "f16", ubatch: int = DEFAULT_UBATCH,
                swa_full: bool = False) -> Optional[int]:
    """The conversation memory (cache plus any recurrent state), exactly as llama.cpp allocates it, or None when the model file lacks the numbers.

    Each of the `parallel` requests gets `ctx / parallel` tokens rounded UP to a multiple of 256 (so the total can be a little more than `ctx`); a
    sliding-window layer keeps only its window (plus the physical batch) per request; a hybrid model's recurrent layers hold a fixed state, not a cache."""
    n = facts.layers
    if not n or ctk not in BYTES_PER_VALUE or ctv not in BYTES_PER_VALUE:
        return None
    g = facts.g
    heads = g("attention.head_count")
    heads_kv = _per_layer(g("attention.head_count_kv"), n, heads)
    emb = g("embedding_length")
    klen = g("attention.key_length", (emb // heads) if isinstance(emb, int) and isinstance(heads, int) and heads else None)
    vlen = g("attention.value_length", klen)
    if klen is None or vlen is None or any(h is None for h in heads_kv):
        return None
    klen_swa, vlen_swa = g("attention.key_length_swa", klen), g("attention.value_length_swa", vlen)
    window = g("attention.sliding_window")
    pattern = g("attention.sliding_window_pattern")
    if not window:
        is_swa = [False] * n
    elif isinstance(pattern, int) and pattern > 0:
        is_swa = [((i + 1) % pattern) != 0 for i in range(n)]
    elif isinstance(pattern, list):
        is_swa = [bool(x) for x in _per_layer(pattern, n, 0)]
    else:
        is_swa = [False] * n
    interval, recurrent = g("full_attention_interval"), g("attention.recurrent_layers")
    if isinstance(recurrent, list):
        is_rec = [bool(x) for x in _per_layer(recurrent, n, 0)]
    elif interval:
        is_rec = [((i + 1) % interval) != 0 for i in range(n)]
    else:
        is_rec = [False] * n
    shared = g("attention.shared_kv_layers")
    keeps_cache = n - shared if isinstance(shared, int) and 0 < shared < n else n         # Gemma 4 E2B/E4B: the last layers reuse earlier layers' cache and keep none
    parallel = max(1, int(parallel))
    lane = _pad(ctx / parallel)
    cells_all = lane * parallel
    cells_swa = parallel * _pad(min(lane, (window or 0) + ubatch))
    total = 0.0
    for i in range(n):
        if is_rec[i] or i >= keeps_cache:
            continue
        if is_swa[i]:
            cells, kl, vl = (cells_all if swa_full else cells_swa), klen_swa, vlen_swa
        else:
            cells, kl, vl = cells_all, klen, vlen
        total += cells * heads_kv[i] * (kl * BYTES_PER_VALUE[ctk] + vl * BYTES_PER_VALUE[ctv])
    if any(is_rec):
        conv, inner, state, groups = g("ssm.conv_kernel"), g("ssm.inner_size"), g("ssm.state_size"), g("ssm.group_count")
        if None in (conv, inner, state, groups):
            return None
        total += parallel * sum(is_rec) * 4 * ((conv - 1) * (inner + 2 * groups * state) + state * inner)
    return int(total)


@dataclass
class MemoryEstimate:
    weights_mb: int = 0
    vision_mb: int = 0
    cache_mb: Optional[int] = None
    compute_mb: int = 0
    process_mb: int = PROCESS_OVERHEAD_MB
    total_mb: Optional[int] = None
    card_mb: Optional[int] = None
    exact: list = field(default_factory=list)            # the parts that are exact ("the conversation cache")
    checked: bool = False                                # confirmed by the engine's own dry run
    check_note: str = ""                                 # why it was not (the tool is missing, it took too long...)
    layers_on_card: Optional[int] = None                 # from the dry run, when layers are Automatic
    per_request_tokens: int = 0
    reason: str = ""                                     # why there is no number (the model file could not be read)

    @property
    def fits(self) -> Optional[bool]:
        return None if self.total_mb is None or not self.card_mb else self.total_mb <= self.card_mb

    @property
    def summary(self) -> str:
        """"about 8.1 of 24 GB": what the line under the settings says."""
        if self.total_mb is None:
            return ""
        used = f"about {self.total_mb / 1024:.1f}"
        return f"{used} of {self.card_mb / 1024:.0f} GB" if self.card_mb else f"{used} GB"

    @property
    def exact_note(self) -> str:
        if self.checked:
            return "Checked with the engine's own memory tool. Still an estimate: Windows can move memory around while the game runs."
        if "the conversation cache" in self.exact:
            return "The conversation cache is exact; the rest is an estimate."
        return "An estimate."

    def to_json(self) -> dict:
        return {"totalMb": self.total_mb, "cardMb": self.card_mb, "summary": self.summary, "fits": self.fits, "exact": self.exact, "exactNote": self.exact_note,
                "checked": self.checked, "checkNote": self.check_note, "layersOnCard": self.layers_on_card, "perRequestTokens": self.per_request_tokens,
                "parts": {"weightsMb": self.weights_mb, "visionMb": self.vision_mb, "cacheMb": self.cache_mb, "computeMb": self.compute_mb, "engineMb": self.process_mb},
                "reason": self.reason}


def estimate_memory(model_path, mmproj_path, settings: dict, override: Optional[dict] = None, card_mb: Optional[int] = None) -> MemoryEstimate:
    """The graphics memory `model_path` takes with `settings` (the device's) and `override` (that model's own `{ctx, parallel}`)."""
    ov = override or {}
    ctx = int(ov.get("ctx") or settings.get("ctx") or 32768)
    parallel = int(ov.get("parallel") or settings.get("parallel") or 2)
    try:
        size = os.stat(str(model_path)).st_size
    except OSError:
        return MemoryEstimate(card_mb=card_mb, per_request_tokens=ctx // max(1, parallel), reason="Couldn't read the model file.")
    mm_size = None
    if mmproj_path:
        try:
            mm_size = os.stat(str(mmproj_path)).st_size
        except OSError:
            pass
    return estimate_from_facts(size, mm_size, gguf_header.read_model_facts(model_path), settings, override, card_mb)


def estimate_from_facts(size: int, mmproj_size: Optional[int], facts: Optional[gguf_header.ModelFacts], settings: dict, override: Optional[dict] = None,
                        card_mb: Optional[int] = None) -> MemoryEstimate:
    """The same estimate from numbers instead of files: the model file's size, the vision file's size and the facts of the header. Set me up asks it about a catalog model
    that is not downloaded yet (its facts are kept in set_me_up.py)."""
    ov = override or {}
    ctx = int(ov.get("ctx") or settings.get("ctx") or 32768)
    parallel = int(ov.get("parallel") or settings.get("parallel") or 2)
    est = MemoryEstimate(card_mb=card_mb, per_request_tokens=ctx // max(1, parallel))
    extras = settings.get("extraArgs") or []
    ub_raw = flags.extra_value(extras, "-ub", "--ubatch-size")
    ub = int(ub_raw) if ub_raw and ub_raw.isdigit() and int(ub_raw) > 0 else DEFAULT_UBATCH
    cache = settings.get("cacheType", "f16")
    ctk = flags.extra_value(extras, "-ctk", "--cache-type-k") or cache
    ctv = flags.extra_value(extras, "-ctv", "--cache-type-v") or cache
    layers = settings.get("layers", "all")
    total_layers = facts.offload_layers if facts else None
    weights = size * WEIGHTS_FACTOR / MIB
    if layers == "none":
        weights = 0.0
    elif isinstance(layers, int) and total_layers:
        weights = weights * min(layers, total_layers) / total_layers      # layers are uneven in size: only the engine's own tool gets this exactly
    est.weights_mb = int(weights)
    if mmproj_size is not None:
        est.vision_mb = int(mmproj_size / MIB) + VISION_EXTRA_MB
    est.compute_mb = int(max(COMPUTE_MIN_MB, ub * COMPUTE_MB_PER_BATCH_TOKEN))
    if facts is None:
        est.reason = "Couldn't read the model file's header, so the cache size couldn't be calculated."
        return est
    cb = cache_bytes(facts, ctx, parallel, ctk, ctv, ub, flags.has_extra_flag(extras, "--swa-full"))
    if cb is None:
        est.reason = "This model file doesn't have enough details to calculate its cache."
        return est
    est.cache_mb = int(cb / MIB)
    est.exact = ["the conversation cache"]
    est.total_mb = est.weights_mb + est.vision_mb + est.cache_mb + est.compute_mb + est.process_mb
    return est


# --------------------------------------------------------------------------------------------
# The engine's own dry run
# --------------------------------------------------------------------------------------------
_PROJECTED = re.compile(r"projected to use (\d+) MiB of device memory vs\. (\d+) MiB of free device memory")


def find_fit_params(exe) -> Optional[Path]:
    """`llama-fit-params` next to the engine (it ships in the Windows zips; not checked in the Linux ones)."""
    if not exe:
        return None
    d = Path(str(exe)).parent
    for name in ("llama-fit-params.exe", "llama-fit-params"):
        p = d / name
        if p.is_file():
            return p
    return None


def fit_params_args(model_path, settings: dict, override: Optional[dict] = None, fit_target_mb: Optional[int] = None) -> list:
    """The part of the launch command that decides memory, in the order the dry-run tool takes it."""
    ov = override or {}
    extras = settings.get("extraArgs") or []
    args = ["-m", str(model_path), "-c", str(int(ov.get("ctx") or settings.get("ctx") or 32768)), "-np", str(int(ov.get("parallel") or settings.get("parallel") or 2))]
    layers = settings.get("layers", "all")
    if layers == "all":
        args += ["-ngl", "999"]
    elif layers == "none":
        args += ["-ngl", "0"]
    elif isinstance(layers, int):
        args += ["-ngl", str(layers)]
    elif fit_target_mb is not None:
        args += ["--fit-target", str(int(fit_target_mb))]
    cache, flash = settings.get("cacheType", "f16"), settings.get("flashAttn", "auto")
    if cache != "f16" and flash == "auto":
        flash = "on"
    if flash in ("on", "off"):
        args += ["-fa", flash]
    if cache != "f16":
        args += ["-ctk", cache, "-ctv", cache]
    for names, out in ((("-ub", "--ubatch-size"), "-ub"), (("-b", "--batch-size"), "-b"), (("-ctk", "--cache-type-k"), "-ctk"), (("-ctv", "--cache-type-v"), "-ctv")):
        v = flags.extra_value(extras, *names)
        if v:
            args += [out, v]
    if flags.has_extra_flag(extras, "--swa-full"):
        args.append("--swa-full")
    return args


def check_with_engine(exe, model_path, settings: dict, override: Optional[dict] = None, fit_target_mb: Optional[int] = None,
                      run=hidden_process.run) -> Optional[dict]:
    """Run the engine's dry-run tool for this setup -> `{projectedMb, freeMb, layersOnCard}`, or None when the tool is missing, slow or says nothing. Blocking
    (about a second): callers run it in a thread."""
    tool = find_fit_params(exe)
    if tool is None:
        return None
    cmd = [str(tool), *fit_params_args(model_path, settings, override, fit_target_mb), "-lv", "4"]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=FIT_PARAMS_TIMEOUT, cwd=str(tool.parent), env=flags.clean_environment(
            extra={"LD_LIBRARY_PATH": f"{tool.parent}:{os.environ.get('LD_LIBRARY_PATH', '')}".rstrip(":")} if os.name != "nt" else None))
    except Exception:
        return None
    text = (r.stderr or "") + "\n" + (r.stdout or "")
    m = None
    for m in _PROJECTED.finditer(text):
        pass
    if not m:
        return None
    layers = None
    for line in reversed((r.stdout or "").splitlines()):
        lm = re.search(r"-ngl (-?\d+)", line)
        if lm:
            layers = int(lm.group(1)) if int(lm.group(1)) >= 0 else None       # -1 means "nothing had to change": the engine's own choice, not a count
            break
    return {"projectedMb": int(m.group(1)), "freeMb": int(m.group(2)), "layersOnCard": layers}


def apply_engine_check(est: MemoryEstimate, check: Optional[dict], vision: bool) -> MemoryEstimate:
    """Fold a dry run into an estimate: its projection already counts the weights, the cache and the working memory, so the engine's own overhead (and the
    vision file) are added back. A missing check is said plainly and the estimate stands."""
    if not check:
        est.check_note = "The engine's memory tool was not available, so this is the estimate."
        return est
    est.total_mb = check["projectedMb"] + est.process_mb + (est.vision_mb if vision else 0)
    est.checked = True
    est.layers_on_card = check.get("layersOnCard")
    return est
