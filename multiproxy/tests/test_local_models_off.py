"""The Settings switch "Don't run models on this PC" (2026-10-26): saved, read live, and the screens stop suggesting local models. Quick decisions and Voice are not affected."""
import json
import os
import subprocess
import tempfile
from unittest.mock import AsyncMock

import pytest

import local_models_ui
import models_off
import models_ui
import set_me_up_ui
import settings_ui
from tests.test_overview import ESC, HARNESS, NODE, base, needs_node, rows


# ---- the switch is saved and read ----
def test_the_switch_is_off_by_default_saved_when_turned_on_and_gone_when_turned_off(test_client, proxy_module):
    s = test_client.get("/config/settings").json()
    assert s["localModelsOff"] is False and s["localModelsOffWords"]["title"] == "Don't run models on this PC"
    assert test_client.post("/config/settings", json={"localModelsOff": True}).status_code == 200
    assert test_client.get("/config/settings").json()["localModelsOff"] is True
    assert proxy_module._load_config()["local_models_off"] is True
    test_client.post("/config/settings", json={"localModelsOff": False})
    assert test_client.get("/config/settings").json()["localModelsOff"] is False and "local_models_off" not in proxy_module._load_config()


def test_models_already_running_are_stopped_only_when_the_player_said_yes(test_client, proxy_module, monkeypatch):
    stop = AsyncMock()
    monkeypatch.setattr(proxy_module.local_svc, "stop_all", stop)
    test_client.post("/config/settings", json={"localModelsOff": True})
    stop.assert_not_called()                                    # turned on without a yes: nothing is stopped by surprise
    test_client.post("/config/settings", json={"localModelsOff": False})
    test_client.post("/config/settings", json={"localModelsOff": True, "stopRunning": True})
    stop.assert_awaited_once()
    test_client.post("/config/settings", json={"localModelsOff": False, "stopRunning": True})
    stop.assert_awaited_once()                                  # turning it off again stops nothing and restores everything


def test_the_screens_read_the_flag_from_the_server(test_client):
    for path in ("/config/local-models", "/config/set-me-up", "/config/models-tab"):
        assert test_client.get(path).json()["modelsOff"] is False, path
    test_client.post("/config/settings", json={"localModelsOff": True})
    assert test_client.get("/config/local-models").json()["modelsOff"] is True
    assert test_client.get("/config/local-models").json()["runsOn"] is None, "no paired PC runs a model"
    assert test_client.get("/config/set-me-up").json()["modelsOff"] is True
    assert test_client.get("/config/models-tab").json()["modelsOff"] is True
    test_client.post("/config/settings", json={"localModelsOff": False})


# ---- Overview ----
def test_the_overview_says_models_off_here_in_the_muted_machines_cell_and_nothing_turns_amber_or_red():
    on = base(models_off=True)
    this = next(m for m in on["machines"] if m["name"] == "This device")
    assert "Models off here" in this["runs"] and this["level"] == "ok"
    assert not any(a["level"] in ("warn", "bad") and "off" in a["line"].lower() for a in on["attention"])
    assert "Models off here" not in next(m for m in base()["machines"] if m["name"] == "This device")["runs"]


# ---- the Local device tab, Set me up, Settings, Models tab ----
def _node(src: str):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


@needs_node
def test_the_fit_dots_and_legend_are_hidden_when_on_and_back_when_off():
    lm = local_models_ui.LOCAL_MODELS_JS
    part = lm[lm.index("function lmIcons"):lm.index("// Skyrim's busiest moment")]
    src = (HARNESS + ESC + "\nfunction lmSkyrimPeakLine() { return '<p>peak</p>'; }\nlet lmState = null;\n" + part +
           "const m = { fit: 'too_big', vision: false };\nconst a = [lmIcons(m, false, { scope: 'this' }), lmLegend(false, {})];\n"
           "const b = [lmIcons(m, false, { modelsOff: true, scope: 'this' }), lmLegend(false, { modelsOff: true, scope: 'this' })];\n"
           "const c = [lmIcons(m, true, { modelsOff: true, scope: 'S2' }), lmLegend(true, { modelsOff: true, scope: 'S2' })];\nconsole.log(JSON.stringify({a, b, c}));\n")
    o = _node(src)
    assert "lm-dot" in o["a"][0] and "lm-legend" in o["a"][1]
    assert "lm-dot" not in o["b"][0] and o["b"][1] == ""
    assert "lm-dot" in o["c"][0] and "lm-legend" in o["c"][1], "this PC's switch is about this PC: another PC's screens keep their dots"


def test_start_is_disabled_with_the_reason_and_a_muted_line_says_where_models_run():
    js = local_models_ui.LOCAL_MODELS_JS
    assert "(s.modelsOff && !remote ? ' disabled title=\"' + esc(LM_OFF.hover) + '\"' : '')" in js
    assert "s.runsOn ? LM_OFF.runsOn.replace('{pc}', s.runsOn) : LM_OFF.off" in js
    assert "Download" in js and "lmDownloadModel" in js, "no button is removed"
    w = models_off.WORDS
    assert w["hover"] == "Models are switched off on this PC: Settings." and w["off"] == "Models are switched off on this PC." and w["runsOn"] == "Models run on {pc}."
    assert "const LM_OFF = " in js


def test_set_me_up_skips_to_a_panel_with_the_next_steps_and_a_turn_on_link():
    js = set_me_up_ui.JS
    assert "smu.look && smu.look.modelsOff" in js and "smuOffHtml()" in js
    panel = js[js.index("function smuOffHtml"):js.index("function smuDraw")]
    assert "SMU_OFF.setMeUpNext" in panel and "SMU_OFF.turnOn" in panel and "openSettings('folders')" in js
    assert models_off.WORDS["setMeUpNext"] == ["Open Other devices", "Add a PC", "Open Providers"] and models_off.WORDS["turnOn"] == "Turn on"
    assert "smuAsk()" not in panel and "smuMeasure" not in panel, "no question and no measuring there"


def test_the_settings_switch_asks_before_stopping_running_models_with_cancel_on_the_left():
    js = settings_ui.PANE_HTML if hasattr(settings_ui, "PANE_HTML") else ""
    src = open(settings_ui.__file__, encoding="utf-8").read()
    ask = src[src.index("async function stLocalOffAsk"):src.index("async function stSetToggle")]
    assert "ownAsk(w.confirm.replace('{names}', names.join(', ')), w.confirmGo)" in ask and "if (!names.length) return {};" in ask, "nobody running: nobody is asked"
    assert "{ stopRunning: true }" in ask and "return ok ? { stopRunning: true } : null;" in ask
    assert models_off.WORDS["confirmGo"] == "Turn off and stop them"
    assert "stToggle('localModelsOff'" in src and js is not None


def test_quick_decisions_and_voice_rows_are_not_touched_by_the_switch():
    js = models_ui.MODELS_JS
    line = next(l for l in js.splitlines() if "mdBtn(r.status === 'running' ? MD_WORDS.stop" in l and "mdRun(" in l)
    assert "(r.kind === 'model' || r.kind === 'own')" in line, "only a model started on this PC is disabled"
