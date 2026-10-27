"""Overview gives Ollama, LM Studio and the model engine Start and Shut down buttons, and leaves out what is not installed (2026-10-26)."""
import json

import other_pcs
from tests.test_overview import base, draw, needs_node, rows

CAPS = {"load": True, "unload": True, "start": True, "stop": True, "restart": True}


def _report(ollama_running=False, installed=True, caps=CAPS, llama_installed=True):
    return {"programs": [
        {"id": "ollama", "name": "Ollama", "installed": installed, "running": ollama_running, "state": "running" if ollama_running else "stopped", "capabilities": caps,
         "models": [{"id": "gemma3", "name": "gemma3", "loaded": False, "state": "available"}] if installed else []},
        {"id": "lmstudio", "name": "LM Studio", "installed": True, "running": False, "state": "stopped", "capabilities": caps, "models": [{"id": "q", "name": "q", "loaded": False, "state": "available"}]},
        {"id": "llamacpp", "name": "llama.cpp", "installed": llama_installed, "running": True, "state": "running", "capabilities": {"load": True, "start": False, "restart": False},
         "models": [{"id": "g", "name": "g", "loaded": True, "state": "loaded"}]}],
        "claude": {"state": "off"}, "copilot": {"state": "off"}}


def _pc(report):
    return {"id": "MSI", "name": "MSI", "online": True, "gpu": "RTX 4060", "program": other_pcs.courier_program_label(report), "kind": "courier",
            "models": other_pcs.models_from_report(report), "engines": other_pcs.engines_of(report), "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}


def test_a_stopped_program_gets_start_that_reaches_that_devices_courier_route():
    r = rows(base(pcs=[_pc(_report())], uses={}, routes=[]))["Ollama"]
    assert r["word"] == "Stopped" and r["state"] == "stopped"
    assert r["action"]["kind"] == "start" and r["action"]["label"] == "Start"
    assert r["action"]["url"] == "/config/other-pcs/courier/action"
    assert r["action"]["body"] == {"id": "MSI", "program": "ollama", "model": "", "action": "start"}


def test_a_running_program_gets_shut_down_and_restart_with_the_same_calls_other_devices_makes():
    r = rows(base(pcs=[_pc(_report(ollama_running=True))], uses={}, routes=[]))["Ollama"]
    a = r["action"]
    assert a["kind"] == "stop" and a["label"] == "Shut down" and a["body"]["action"] == "stop" and a["body"]["program"] == "ollama"
    assert a["restart"]["label"] == "Restart" and [c["body"]["action"] for c in a["restart"]["calls"]] == ["stop", "start"]


def test_an_older_courier_that_lists_no_abilities_shows_no_button_and_says_nothing():
    r = rows(base(pcs=[_pc(_report(caps={}))], uses={}, routes=[]))["Ollama"]
    assert r["action"] is None and r["word"] == "Stopped" and not r.get("line")


def test_the_model_engine_has_its_name_and_no_button_when_its_courier_cannot_start_it():
    shown = rows(base(pcs=[_pc(_report())], uses={}, routes=[]))
    assert "llama.cpp" not in shown
    e = shown["Model engine (llama.cpp)"]
    assert e["word"] == "Running" and e["action"] is None


def test_a_program_the_device_reports_as_not_installed_is_not_listed_but_an_unknown_one_keeps_its_row():
    gone = rows(base(pcs=[_pc(_report(installed=False))], uses={}, routes=[]))
    assert "Ollama" not in gone and "LM Studio" in gone
    rep = _report()
    for p in rep["programs"]:
        p.pop("installed", None)                       # a Courier that cannot say (1.9): keep the row, do not guess
    unknown = rows(base(pcs=[_pc(rep)], uses={}, routes=[]))
    assert "Ollama" in unknown


HIDE = "global.opHiddenGet = () => ({ 'MSI|ollama': true }); global.opShowHidden = {};\n"


@needs_node
def test_the_page_draws_the_buttons_and_leaves_out_a_hidden_program_until_show_hidden_brings_it_back():
    d = base(pcs=[_pc(_report())], uses={}, routes=[])
    shown = draw(d)
    assert ">Start</button>" in shown and "Ollama" in shown
    running = draw(base(pcs=[_pc(_report(ollama_running=True))], uses={}, routes=[]))
    assert ">Shut down</button>" in running and ">Restart</button>" in running
    hidden = draw(d, prelude=HIDE)
    assert "<b>Ollama</b>" not in hidden and "<b>LM Studio</b>" in hidden
    back = draw(d, prelude="global.opHiddenGet = () => ({ 'MSI|ollama': true }); global.opShowHidden = { MSI: true };\n")
    assert "<b>Ollama</b>" in back
    still = draw(base(pcs=[_pc(_report(ollama_running=True))], uses={}, routes=[]), prelude=HIDE)
    assert "<b>Ollama</b>" in still, "a running program always shows"
