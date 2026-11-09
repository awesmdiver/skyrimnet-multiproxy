"""What the model file says about itself, and what its conversation cache would cost (Benchmarks: the facts line and the cache line under a model's name).

Everything comes from the GGUF header we already read for the memory estimate (`gguf_header`), and every cache number comes from OUR estimate (`local_engine_memory.estimate_from_facts`: it
knows sliding-window, hybrid, recurrent and shared-cache models). A fact the file does not give is left out (never 0, never a dash); no estimate, no cache line.

The idea of a facts line (architecture, size, quantisation, layers, the model's own longest context) was seen in gputaskman's model panel (MIT, Scott Hanselman); the quantisation names
below are llama.cpp's public `LLAMA_FTYPE` list, and no gputaskman code is used here.
"""
import re
from typing import Optional

import gguf_header
import local_engine_memory as lem

CACHE_BIG_CTX = 32768

# llama.cpp's file types (general.file_type). A number not in the list is left out rather than guessed.
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M",
              16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M",
              28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}


def _params_text(kv: dict) -> Optional[str]:
    """The model's size as the file words it: `general.size_label` ("12B"), else `general.parameter_count` shown as billions or millions. None when neither is there."""
    label = kv.get("general.size_label")
    if isinstance(label, str) and re.fullmatch(r"[0-9.]+[KMBT]|\d+x[0-9.]+[KMBT]", label.strip(), re.I):
        return label.strip()
    n = kv.get("general.parameter_count")
    if isinstance(n, int) and n > 0:
        return f"{n / 1e9:.1f}B".replace(".0B", "B") if n >= 1e9 else f"{round(n / 1e6)}M"
    return None


def model_facts(path) -> dict:
    """`{"arch", "params", "quant", "layers", "max_ctx"}` with only the facts the file gives (maybe `{}`)."""
    facts = gguf_header.read_model_facts(path)
    if facts is None:
        return {}
    out: dict = {}
    if facts.arch:
        out["arch"] = facts.arch
    p = _params_text(facts.kv)
    if p:
        out["params"] = p
    ft = facts.kv.get("general.file_type")
    if isinstance(ft, int) and ft in FILE_TYPES:
        out["quant"] = FILE_TYPES[ft]
    if facts.layers:
        out["layers"] = int(facts.layers)
    if facts.trained_ctx:
        out["max_ctx"] = int(facts.trained_ctx)
    return out


def cache_cost(path, device_settings: dict, test_ctx: int) -> dict:
    """The cache one conversation costs at the context the test uses and at 32k (or the model's own longest context when that is smaller): `{"a_ctx", "a_mb", "b_ctx", "b_mb"}`.
    Our estimate only (`estimate_from_facts`, one request at a time, the engine's own cache type from the device settings). `{}` when the estimate cannot be made; the second size is
    left out when it would be the same as the first or smaller."""
    facts = gguf_header.read_model_facts(path)
    if facts is None or not test_ctx or test_ctx <= 0:
        return {}
    settings = dict(device_settings or {})
    settings["layers"] = "all"

    def mb(ctx: int) -> Optional[int]:
        est = lem.estimate_from_facts(int(facts.file_size), None, facts, settings, {"ctx": int(ctx), "parallel": 1}, None)
        return est.cache_mb if "the conversation cache" in (est.exact or []) and est.cache_mb is not None else None

    a = mb(test_ctx)
    if a is None:
        return {}
    out = {"a_ctx": int(test_ctx), "a_mb": int(a)}
    big = min(CACHE_BIG_CTX, facts.trained_ctx) if facts.trained_ctx else CACHE_BIG_CTX
    if big > test_ctx:
        b = mb(big)
        if b is not None:
            out["b_ctx"], out["b_mb"] = int(big), int(b)
    return out


def test_ctx(prompts, sustained: bool = True) -> int:
    """The context one request of the test reserves (the room for the biggest ticked prompt and its reply: the same figure the runner starts its engine with)."""
    import benchmarks_testset as tset
    data = tset.load()
    ps = tset.prompts_for(data, [c for c in (prompts or []) if c in tset.CLASSES] or list(tset.CLASSES))
    return int(tset.tokens_per_slot(ps + ([tset.sustained_prompt(data)] if sustained else [])))


def for_model(path, device_settings: dict, test_ctx: int) -> dict:
    """What the page draws under a model's name: `{"facts": {...}, "cache": {...}}`, each part present only when it has something. `{}` for a file with nothing."""
    out = {}
    f = model_facts(path)
    if f:
        out["facts"] = f
    c = cache_cost(path, device_settings, test_ctx)
    if c:
        out["cache"] = c
    return out
