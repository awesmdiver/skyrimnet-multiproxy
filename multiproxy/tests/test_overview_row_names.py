"""Every row in Models and services has a name; a nameless row is said in the log once; an empty purpose says what the row is, only when true (2026-11-04). Fake data only."""
import logging

import overview
from tests.test_overview import base


def _all(d):
    return [r for g in d["groups"] for r in g["rows"]] + ((d.get("idle") or {}).get("rows") or [])


def _msi(engines):
    return {"id": "MSI", "name": "MSI", "online": True, "gpu": None, "program": "LM Studio", "kind": "courier", "models": [], "engines": engines,
            "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}


def test_an_engine_with_no_name_gets_its_known_name_or_its_id_and_the_log_says_so(caplog):
    overview._LOGGED_NAMELESS.clear()
    pc = _msi([{"id": "lmstudio", "name": "", "running": False, "can": {}}, {"id": "llamacpp", "name": "", "running": False, "can": {}}])
    with caplog.at_level(logging.WARNING, logger="overview"):
        d = base(pcs=[pc])
        base(pcs=[pc])
    names = {r["key"]: r["name"] for r in _all(d)}
    assert names["engine:MSI/lmstudio"] == "lmstudio" and names["engine:MSI/llamacpp"] == "Model engine (llama.cpp)"
    msgs = [m.getMessage() for m in caplog.records]
    assert msgs.count("overview: row engine:MSI/lmstudio had no name, showing lmstudio") == 1       # once per key per run


def test_a_model_with_no_name_on_this_device_or_another_pc_or_a_provider_still_has_one():
    d = base(local_models=[{"id": "abc", "name": "", "modelName": "", "state": "stopped"}],
             pcs=[{"id": "Laptop", "online": True, "gpu": None, "program": "x", "models": [{"id": "m1", "name": "", "state": "stopped"}], "problem": None, "coveredBy": None, "usedMb": None, "totalMb": None}],
             providers=[{"id": "p", "name": "", "short": "Pee", "state": "ok", "status": "Ready", "cloud": False, "where": "This device"}], uses={"provider:Pee": ["Memories"]}, routes=[])
    rs = _all(d)
    assert rs and all(str(r["name"]).strip() for r in rs)


def test_no_row_of_the_normal_page_has_an_empty_name_and_engines_say_what_they_are():
    d = base(pcs=[_msi([{"id": "ollama", "name": "Ollama", "running": False, "can": {}}])], uses={"local:gemma": ["Dialogue"], "provider:Claude": ["Memories"]}, routes=[])
    rs = _all(d)
    assert all(str(r["name"]).strip() for r in rs)
    eng = next(r for r in rs if r["key"] == "engine:MSI/ollama")
    assert d["cols"]["does"] and eng["does"] == "Engine"
    assert next(r for r in rs if r["key"] == "local:gemma")["does"] == "Dialogue"


def test_a_row_a_job_uses_never_gets_the_not_used_words():
    assert overview._what_it_is({"key": "decisions"}) == ""
    assert overview._what_it_is({"key": "provider:X"}) == "Not used by SkyrimNet"
