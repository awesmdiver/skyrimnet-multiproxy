"""The Overview lists every engine and model each paired device really reports, and Voice gets a typical time (2026-10-22)."""
import sqlite3
import time

import pytest

import other_pcs
import overview
import speed_queries
from tests.test_overview import base, rows


def _report():
    """What a paired Courier reports: Ollama and LM Studio models (one loaded each, the rest idle) and Claude signed in."""
    return {"programs": [
        {"id": "ollama", "name": "Ollama", "installed": True, "running": True, "state": "running",
         "models": [{"id": "gemma3", "name": "gemma3", "loaded": True, "state": "loaded"}, {"id": "llama3", "name": "llama3", "loaded": False, "state": "available"},
                    {"id": "phi4", "name": "phi4", "loaded": False, "state": "available"}]},
        {"id": "lmstudio", "name": "LM Studio", "installed": True, "running": True, "state": "running",
         "models": [{"id": "qwen", "name": "qwen3", "loaded": False, "state": "available"}, {"id": "mistral", "name": "mistral", "loaded": True, "state": "loaded"}]},
        {"id": "llamacpp", "name": "llama.cpp", "installed": True, "running": False, "state": "stopped", "models": []}],
        "claude": {"state": "ready", "models": [{"id": "sonnet", "name": "Sonnet 4.6"}, {"id": "opus", "name": "Opus 4.7"}]}, "copilot": {"state": "off"}}


def _pc(online=True, report="x"):
    rep = _report() if report == "x" else report
    return {"id": "MSI", "name": "MSI", "online": online, "gpu": "RTX 4060", "program": other_pcs.courier_program_label(rep), "kind": "courier",
            "models": other_pcs.models_from_report(rep) if rep else [], "engines": other_pcs.engines_of(rep), "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}


def _all(d):
    return rows(d), {r["name"]: r for r in (d["idle"] or {"rows": []})["rows"]}


def test_engines_and_loaded_models_are_rows_with_the_device_name_and_idle_ones_go_in_the_idle_group():
    d = base(pcs=[_pc()], uses={}, routes=[])
    shown, idle = _all(d)
    for engine in ("Ollama", "LM Studio", "Claude"):
        assert shown[engine]["where"] == "MSI" and shown[engine]["word"] in ("Running", "Ready") and shown[engine]["level"] == "ok"
    assert "llama.cpp" not in shown, "an engine with no models and nothing running is not drawn"
    assert shown["gemma3"]["where"] == "MSI" and shown["gemma3"]["word"] == "Running"
    assert shown["mistral"]["word"] == "Running"
    assert set(idle) >= {"llama3", "phi4", "qwen3", "Claude Sonnet 4.6", "Claude Opus 4.7"}
    assert all(idle[n]["where"] == "MSI" for n in ("llama3", "phi4", "qwen3")) and idle["llama3"]["word"] == "Stopped"
    assert "Claude Sonnet 4.6" not in shown, "Claude is always ready: it only gets its own row when it is used"


def test_a_model_that_answered_in_the_last_hour_has_its_own_row_with_its_figures():
    fig = {"rows": {(None, "MSI/llama3"): {"n": 7, "ok": 7, "typical": 3.0, "tokensPerSecond": None, "avg": 3.2, "ago": 400.0}},
           "perMinute": 1.0, "typical": 3.0, "failuresToday": 0, "calls": 7}
    d = base(pcs=[_pc()], figures=fig, uses={}, routes=[])
    shown, idle = _all(d)
    assert shown["llama3"]["requests"] == "7" and shown["llama3"]["avg"] == "3.2 s" and "llama3" not in idle
    assert shown["Ollama"]["requests"] == "7"
    assert idle["phi4"]["requests"] == "0"                      # (answered nothing: zero, not left out)


def test_an_offline_device_lists_its_engines_and_models_as_not_ready():
    d = base(pcs=[_pc(online=False)], uses={}, routes=[])
    shown, idle = _all(d)
    assert shown["Ollama"]["word"] == "Not ready" and shown["Ollama"]["where"] == "MSI"
    assert all(r["word"] == "Not ready" for r in idle.values() if r["where"] == "MSI")
    assert "llama3" in idle and "gemma3" in idle or "gemma3" in shown


def test_a_device_with_no_report_adds_nothing_and_the_card_still_agrees_with_the_rows():
    d = base(pcs=[_pc(report=None)], uses={}, routes=[])
    shown, idle = _all(d)
    assert not [r for r in list(shown.values()) + list(idle.values()) if r["where"] == "MSI"]
    d = base(pcs=[_pc()], uses={}, routes=[])
    card = next(m for m in d["machines"] if m["name"] == "MSI")
    shown, idle = _all(d)
    n_rows = len([r for r in list(shown.values()) + list(idle.values()) if r["where"] == "MSI" and r["key"].startswith("pc:")])
    assert n_rows == 7 and "4 models" in card["runs"]            # (4 = loaded Ollama, loaded LM Studio, and the two Claude ones that are always ready); all 7 models are in the table


def test_this_devices_own_rows_stay():
    d = base(pcs=[_pc()], uses={}, routes=[])
    shown, _ = _all(d)
    assert shown["gemma-4-12b"]["where"] == "This device"


# ---- Voice's typical time ----
VOICE = {"state": "ready", "label": "Running", "pc": "", "installed": True}


def _fig(voice):
    return {"rows": {}, "perMinute": 0.0, "typical": None, "failuresToday": 0, "calls": 0, "voice": voice}


def test_voice_shows_a_typical_time_from_the_hours_lines_and_none_under_five():
    d = base(figures=_fig({"n": 57, "ok": 57, "avg": 8.1, "typical": 6.4}), voice=VOICE)
    assert rows(d)["Voice"]["speed"] == "6.4 s typical" and rows(d)["Voice"]["avg"] == "8.1 s"
    d = base(figures=_fig({"n": 3, "ok": 3, "avg": 8.1, "typical": None}), voice=VOICE)
    assert rows(d)["Voice"]["speed"] is None


from tests.test_overview import draw, needs_node


@needs_node
def test_the_page_draws_the_new_rows_with_the_existing_look():
    h = draw(base(pcs=[_pc()], uses={}, routes=[], figures=_fig({"n": 57, "ok": 57, "avg": 8.1, "typical": 6.4}), voice=VOICE))
    for s in ("Ollama", "LM Studio", "gemma3", "MSI", "Not in use (", "llama3"):
        assert s in h, s
