"""Benchmarks: the numbers the Benchmarks tab shows, taken from the result files and from nothing else (contract section 9; DESIGN: real data or it is not drawn).

Pure functions over what `benchmarks_store` reads: a manifest, a summary, the request rows and the sample rows. They return plain numbers and short codes (never words): the page
turns them into text, and a number it cannot be given reads "Not measured" there. Nothing here scores, ranks or picks a best.
"""
from typing import Optional

import benchmarks_contract as bc

TEST_PHASES = ("after_load", "warmup", "engine_restart", "sustained")       # plus every `level_<n>`: the samples taken while the model was loaded and being tested
CHART_POINTS = 120
TELEMETRY = (("temp_c", "gpu"), ("gpu_util_pct", "gpu"), ("clock_mhz", "gpu"), ("power_w", "gpu"), ("fan_pct", "gpu"), ("cpu_util_pct", "machine"))


def short_model_id(model_id: str) -> str:
    """`sha256:9f3a...c1e2:7381382944` -> `9f3a…c1e2` (the fingerprint, short)."""
    parts = (model_id or "").split(":")
    h = parts[1] if len(parts) >= 2 else ""
    return f"{h[:4]}…{h[-4:]}" if len(h) >= 8 else h


def short_setup_id(setup_id: str) -> str:
    return (setup_id or "")[:4].upper()


def _stat(level: Optional[dict], key: str) -> Optional[dict]:
    s = (level or {}).get(key)
    return s if isinstance(s, dict) and s.get("n") else None


def _stop_of(manifest: dict) -> Optional[dict]:
    """Why a model was stopped to protect the PC, in the few figures the table shows (the whole record stays in the run's files)."""
    s = manifest.get("safety_stop")
    if manifest.get("status") not in bc.RUN_STATES_STOPPED or not isinstance(s, dict):
        return None
    return {"kind": s.get("kind"), "reason": s.get("reason"), "concurrency": s.get("concurrency"), "step": s.get("step"), "limit_s": s.get("limit_s"), "elapsed_s": s.get("elapsed_s"),
            "threshold": s.get("threshold"), "duration_s": s.get("duration_s")}


def _top_level(summary: Optional[dict]) -> tuple:
    """(level number, its summary) for the highest concurrency that was tested, or (None, None)."""
    levels = (summary or {}).get("levels") or {}
    if not levels:
        return None, None
    n = max(int(k) for k in levels)
    return n, levels[str(n)]


def _headroom(summary: Optional[dict]) -> dict:
    """The tightest card's estimate: `{mb, dot, kind}`. Grey and no number when no card gave enough data."""
    heads = [h for h in ((summary or {}).get("headroom") or []) if h.get("headroom_mb") is not None]
    if not heads:
        return {"mb": None, "dot": bc.DOT_GREY, "kind": "estimate", "gpus": len((summary or {}).get("headroom") or [])}
    worst = min(heads, key=lambda h: h["headroom_mb"])
    return {"mb": worst["headroom_mb"], "dot": worst["dot"], "kind": "estimate", "gpu_index": worst.get("gpu_index"), "skyrim_state": worst.get("skyrim_state"), "gpus": len(heads)}


def _peak_vram_mb(summary: Optional[dict]) -> Optional[float]:
    """The model's own peak: each card's highest use minus its use before the load (`model_vram_delta_mb`), summed over the cards. None when no card has one."""
    vals = [m.get("model_vram_delta_mb") for m in ((summary or {}).get("memory") or {}).values() if m.get("model_vram_delta_mb") is not None]
    return sum(vals) if vals else None


def _card_highest_mb(summary: Optional[dict]) -> Optional[float]:
    """The card's highest total use during the test (Windows and other programs included): the largest across the cards."""
    vals = [m.get("vram_highest_overall_mb") for m in ((summary or {}).get("memory") or {}).values() if m.get("vram_highest_overall_mb") is not None]
    return max(vals) if vals else None


def _peak_temp_c(summary: Optional[dict]) -> Optional[float]:
    vals = [v["temp_c"]["highest_sampled"] for k, v in ((summary or {}).get("highest_lowest") or {}).items() if k != "machine" and "temp_c" in v]
    return max(vals) if vals else None


def _checks(summary: Optional[dict]) -> Optional[dict]:
    checks = (summary or {}).get("checks") or []
    return {"passed": sum(c["passed"] for c in checks), "total": sum(c["total"] for c in checks)} if checks else None


def _sustained(v: dict) -> dict:
    """The sustained cell: the verdict plus the evidence the run recorded. `lowest_pct` = the lowest later window's p50 as a whole percent of the baseline; None when no window had requests."""
    base = v.get("baseline_tok_s")
    meds = [w["p50_tok_s"] for w in v.get("windows") or [] if w.get("n") and w.get("p50_tok_s") is not None]
    low = min(meds) if meds else None
    pct = round(low * 100.0 / base) if (low is not None and base) else None
    return {"status": v.get("status"), "after_minutes": v.get("after_minutes"), "reason": v.get("reason"), "baseline_tok_s": base, "lowest_tok_s": low, "lowest_pct": pct}


def _services(manifest: dict) -> Optional[dict]:
    """The services that were loaded on the test device, from the manifest (a remote run): `{"requested": choice, "seen": [names]}`; None for a run that recorded none."""
    s = manifest.get("services")
    return {"requested": s.get("requested"), "seen": [str(x)[:80] for x in (s.get("seen") or []) if x][:12]} if isinstance(s, dict) else None


def _on_battery(manifest: dict) -> Optional[bool]:
    """True when the device said it ran on battery; None when it did not say (never guessed)."""
    p = manifest.get("power")
    return True if isinstance(p, dict) and p.get("has_battery") and p.get("ac") is False else None


def _hot(manifest: dict) -> bool:
    """True when the manifest says the device ran hot: the card reported throttling, or the test was stopped at the card's own heat limit."""
    t = manifest.get("thermal")
    return bool((isinstance(t, dict) and t.get("throttled") is True) or manifest.get("thermal_stop_reading"))


def _memory_breakdown(manifest: dict, summary: Optional[dict], level, top: Optional[dict]) -> Optional[dict]:
    """The model, cache and working space the engine itself printed when it loaded at the highest concurrency (`measured`, MB) beside our header-based guess made before the run
    (`estimated_mb`). Either may be missing; `None` when the run has neither. Numbers only: no program names are ever in here (they live in their own local file)."""
    engine = (top or {}).get("engine") or {}
    buffers = engine.get("buffers_mb") or (manifest.get("resolved_settings") or {}).get("buffers_mb") or ((summary or {}).get("resolved_settings") or {}).get("buffers_mb")
    measured = {k: buffers[k] for k in ("model", "cache", "compute") if isinstance(buffers, dict) and buffers.get(k) is not None}
    est = manifest.get("memory_estimate_mb") if isinstance(manifest.get("memory_estimate_mb"), dict) else {}
    if len(measured) < 3 and not est.get("total_mb"):
        return None
    return {"measured": measured if len(measured) == 3 else None, "estimated_mb": est.get("total_mb"), "concurrency": level}


def result_row(manifest: dict, summary: Optional[dict]) -> dict:
    """One row of the results table (one run = one model). Timings are those of the highest concurrency tested, as the table says ("at 8 concurrent")."""
    level, top = _top_level(summary)
    av = manifest.get("measurement_availability") or {}
    spill = manifest.get("spill") or {}
    sustained = (summary or {}).get("sustained") or {}
    return {
        "run_id": manifest.get("run_id"), "batch_id": manifest.get("batch_id"), "device": bc.device_of(manifest), "model": manifest.get("model_file") or "", "size_bytes": manifest.get("model_size_bytes"),
        "status": manifest.get("status"), "reason": manifest.get("reason") or "", "steps_done": manifest.get("steps_done"), "steps_total": manifest.get("steps_total"),
        "profile": manifest.get("profile"), "started_epoch_s": manifest.get("started_epoch_s"), "ended_epoch_s": manifest.get("ended_epoch_s"), "skyrim_state": manifest.get("skyrim_state"), "kind": manifest.get("kind"),
        "level": level, "requests": (top or {}).get("completed"),
        "partial": manifest.get("status") in bc.RUN_STATES_STOPPED, "stop": _stop_of(manifest),
        "peak_vram_mb": _peak_vram_mb(summary), "highest_card_vram_mb": _card_highest_mb(summary), "headroom": _headroom(summary), "load_time_s": manifest.get("load_time_s"),
        "prompt_tok_s": _stat(top, "prompt_tok_s"), "generation_tok_s": _stat(top, "generation_tok_s"), "ttft_ms": _stat(top, "ttft_ms"),
        "availability": {k: (av.get(k) or {}).get("status") for k in ("prompt_tok_s", "generation_tok_s", "ttft_ms")},
        "peak_temp_c": _peak_temp_c(summary), "sustained": _sustained(sustained) if sustained else None,
        "checks": _checks(summary), "spill": spill.get("spill"), "spill_observation": spill.get("observation"), "spill_evidence": spill.get("evidence"),
        "heat_limit": ((av.get("thermal_stop") or {}).get("status") if av else None),
        "services": _services(manifest), "on_battery": _on_battery(manifest), "hot": _hot(manifest),
        "memory_breakdown": _memory_breakdown(manifest, summary, level, top),
    }


def _avg(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _prompt_rows(requests: list, level: Optional[int]) -> list:
    """By prompt length at the highest concurrency: tokens in, prompt speed, time to read, TTFT, generation speed, tokens out (averages of the measured, finished requests)."""
    out = []
    for cls in bc.PROMPT_CLASSES:
        rows = [r for r in requests if not r.get("warmup") and not r.get("sustained") and r.get("status") == "ok" and r.get("level") == level and r.get("prompt_length_class") == cls]
        if not rows:
            continue
        out.append({"prompt": cls, "n": len(rows), "tokens_in": _avg([r.get("prompt_tokens") for r in rows]), "prompt_tok_s": _avg([r.get("prompt_tok_s") for r in rows]),
                    "time_to_read_s": _avg([(r["prompt_ms"] / 1000.0) if r.get("prompt_ms") else None for r in rows]), "ttft_ms": _avg([r.get("ttft_ms") for r in rows]),
                    "generation_tok_s": _avg([r.get("generation_tok_s") for r in rows]), "tokens_out": _avg([r.get("completion_tokens") for r in rows])})
    return out


def _in_test(phase: str) -> bool:
    return phase in TEST_PHASES or str(phase).startswith("level_")


def telemetry(samples: list) -> list:
    """Lowest, average and highest of each reading while the model was loaded and being tested, one row per card (and one for the computer)."""
    rows = []
    acc: dict = {}
    for s in samples:
        if not _in_test(s.get("phase", "")):
            continue
        who = s.get("gpu_index") if s.get("kind") == "gpu" else "machine"
        for field, scope in TELEMETRY:
            if (scope == "gpu") != (s.get("kind") == "gpu"):
                continue
            v = s.get(field)
            if v is not None:
                acc.setdefault((who, field), []).append(v)
    for (who, field), vals in acc.items():
        rows.append({"who": who, "reading": field, "lowest": min(vals), "average": sum(vals) / len(vals), "highest": max(vals)})
    order = {f: i for i, (f, _) in enumerate(TELEMETRY)}
    rows.sort(key=lambda r: (str(r["who"]) == "machine", str(r["who"]), order[r["reading"]]))              # the cards first, then the computer
    return rows


def _downsample(points: list, n: int = CHART_POINTS) -> list:
    if len(points) <= n:
        return points
    step = len(points) / n
    return [points[int(i * step)] for i in range(n)] + [points[-1]]


def charts(requests: list, samples: list) -> dict:
    """Small line charts: generation speed request by request, and temperature, VRAM used and power over the test (one line per card). Drawn from the files only."""
    gen = [r["generation_tok_s"] for r in requests if not r.get("warmup") and not r.get("sustained") and r.get("status") == "ok" and r.get("generation_tok_s") is not None]
    out: dict = {"generation_tok_s": gen, "requests": len(gen)}
    t0 = None
    series: dict = {}
    for s in samples:
        if s.get("kind") != "gpu" or not _in_test(s.get("phase", "")):
            continue
        t0 = s["t_s"] if t0 is None else t0
        for field in ("temp_c", "vram_used_mb", "power_w"):
            if s.get(field) is not None:
                series.setdefault(field, {}).setdefault(s["gpu_index"], []).append([round(s["t_s"] - t0, 1), s[field]])
    for field, per_gpu in series.items():
        out[field] = {str(i): _downsample(pts) for i, pts in per_gpu.items()}
    out["duration_s"] = round(max((s["t_s"] for s in samples if s.get("kind") == "machine" and _in_test(s.get("phase", ""))), default=0) - (t0 or 0), 1) if t0 is not None else None
    return out


def _ram(samples: list) -> dict:
    idle = [s.get("ram_used_mb") for s in samples if s.get("kind") == "machine" and s.get("phase") == "idle_before_load" and s.get("ram_used_mb") is not None]
    test = [s.get("ram_used_mb") for s in samples if s.get("kind") == "machine" and _in_test(s.get("phase", "")) and s.get("ram_used_mb") is not None]
    return {"before_mb": max(idle) if idle else None, "highest_mb": max(test) if test else None}


def detail(manifest: dict, summary: Optional[dict], requests: list, samples: list) -> dict:
    """Everything the detail page shows about one run."""
    summary = summary or {}
    level, top = _top_level(summary)
    resolved = dict(summary.get("resolved_settings") or manifest.get("resolved_settings") or {})
    resolved.update({k: v for k, v in ((top or {}).get("engine") or {}).items() if v is not None})                  # the engine the top level ran on (a restart replaces the last one)
    hw = manifest.get("hardware") or {}
    levels = []
    for key in sorted((summary.get("levels") or {}), key=int):
        lv = summary["levels"][key]
        tog = (lv.get("aggregate_tok_s") or {})
        levels.append({"level": int(key), "slots": lv.get("slots"), "requests": lv.get("completed"), "failed": lv.get("failed"), "timed_out": lv.get("timed_out"), "overlap": lv.get("actual_overlap"),
                       "ttft_ms": _stat(lv, "ttft_ms"), "generation_tok_s": _stat(lv, "generation_tok_s"), "prompt_tok_s": _stat(lv, "prompt_tok_s"),
                       "together_tok_s": tog.get("value") if tog.get("status") == "measured" else None})
    mem = summary.get("memory") or {}
    return {
        "row": result_row(manifest, summary),
        "header": {"model_id_short": short_model_id(manifest.get("model_id", "")), "setup_id": short_setup_id(manifest.get("setup_id", "")), "setup_id_full": manifest.get("setup_id"),
                   "run_id": manifest.get("run_id"), "device": bc.device_of(manifest), "profile": manifest.get("profile"), "test_set_version": manifest.get("test_set_version"),
                   "engine": (manifest.get("engine") or {}).get("name"), "engine_build": (manifest.get("engine") or {}).get("build"), "gpus": hw.get("gpus") or [],
                   "driver": hw.get("gpu_driver"), "os": hw.get("os"), "cpu": hw.get("cpu"), "ended": manifest.get("status"), "reason": manifest.get("reason") or "",
                   "started_epoch_s": manifest.get("started_epoch_s"), "model_file": manifest.get("model_file"), "size_bytes": manifest.get("model_size_bytes"),
                   "schema_version": manifest.get("schema_version")},
        "levels": levels, "level": level,
        "prompt_length": _prompt_rows(requests, level),
        "settings": {"layers_on_card": resolved.get("layers_on_card"), "layers_total": resolved.get("layers_total"), "ctx": resolved.get("ctx"), "slots": resolved.get("slots"),
                     "cache_type_k": resolved.get("cache_type_k"), "cache_type_v": resolved.get("cache_type_v"), "flash_attn_active": resolved.get("flash_attn_active"),
                     "flash_attn": resolved.get("flash_attn"), "n_batch": resolved.get("n_batch"), "n_ubatch": resolved.get("n_ubatch"), "threads": resolved.get("n_threads"),
                     "load_time_s": manifest.get("load_time_s"), "gpu_choice": manifest.get("gpu_choice"), "requested": manifest.get("requested_settings")},
        "memory": {"skyrim_state": (_headroom(summary).get("skyrim_state") or manifest.get("skyrim_state")),
                   "skyrim_peak_mb": next((h.get("skyrim_peak_mb") for h in (summary.get("headroom") or []) if h.get("skyrim_peak_mb") is not None), None),
                   "gpus": {k: {"before_mb": m.get("vram_idle_before_load_mb"), "loaded_mb": m.get("vram_after_load_mb"), "highest_mb": m.get("vram_highest_overall_mb"), "delta_mb": m.get("model_vram_delta_mb"),
                                "total_mb": m.get("vram_total_mb"), "at_top_level_mb": ((top or {}).get("vram_highest_in_level_mb") or {}).get(k)} for k, m in mem.items()},
                   "headroom": _headroom(summary), "ram": _ram(samples)},
        "telemetry": telemetry(samples), "charts": charts(requests, samples), "checks": summary.get("checks") or [], "sustained": summary.get("sustained"),
        "spill": manifest.get("spill"), "thermal_basis": manifest.get("thermal_basis"), "heat_stop_reading": manifest.get("thermal_stop_reading"),
        "availability": manifest.get("measurement_availability") or {},
    }
