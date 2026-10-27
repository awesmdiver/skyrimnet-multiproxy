"""The local decision model starts with MultiProxy unless the player turned that off (queue: the-local-decision-model-starts-with-multiproxy-unless-the-player-turned-that-off,
2026-10-05). A stub engine stands in for the real one: nothing is installed or started."""
import asyncio

import pytest

import decision_engine as de
import decision_wiring as dw


class Engine:
    """Looks like the engine manager: installed, a state, and counts of start and stop."""

    def __init__(self, state="stopped", installed=True):
        self.state, self._installed, self.starts, self.stops, self.limit_hits = state, installed, 0, 0, 0

    def installed(self, key=None):
        return self._installed

    async def start(self):
        self.starts += 1
        self.state = "ready"

    async def stop(self, why="", by_button=False):
        self.stops += 1
        self.state = "stopped"

    async def restart(self):
        await self.stop()
        await self.start()

    async def answer(self, state, questions):
        raise de.DecisionNotReady()

    def status(self):
        return {"state": self.state, "ready": self.state == "ready", "installed": self._installed}

    def model_label(self):
        return "Kev-4B"


@pytest.fixture
def handles(proxy_module, monkeypatch):
    h = proxy_module.decision_handles
    eng = Engine()
    monkeypatch.setattr(h, "engine", eng)
    monkeypatch.setattr(h, "stopped_by_player", False)
    monkeypatch.setattr(h, "last_auto_start", 0.0)
    yield h
    proxy_module._save_config({"decisions": {}})


async def decide(h):
    res = await h.try_local({"x": 1}, {"q": {"type": "noul"}}, "t1")
    await asyncio.sleep(0.05)                   # let the background start run
    return res


# ---- the default and the saved choice ------------------------------------------------------------------------------------------------
def test_a_config_that_never_had_the_key_reads_as_on_and_a_saved_off_stays_off(proxy_module, handles):
    proxy_module._save_config({"decisions": {}})
    assert dw.DEFAULT_SETTINGS["autostart"] is True and handles.settings()["autostart"] is True
    proxy_module._save_config({"decisions": {"autostart": False}})
    assert handles.settings()["autostart"] is False


def test_the_settings_route_only_writes_what_the_player_changed(proxy_module, test_client, handles):
    proxy_module._save_config({"decisions": {}})
    assert test_client.post("/config/decisions/settings", json={"surePercent": 40}).status_code == 200
    assert proxy_module._load_config()["decisions"] == {"sure_percent": 40}, "autostart is not written until the player changes it"
    test_client.post("/config/decisions/settings", json={"autostart": False})
    assert proxy_module._load_config()["decisions"] == {"sure_percent": 40, "autostart": False}


@pytest.mark.asyncio
async def test_at_launch_it_starts_with_no_saved_key_but_not_when_turned_off(proxy_module, handles):
    proxy_module._save_config({"decisions": {}})
    await handles.autostart()
    assert handles.engine.starts == 1
    handles.engine.state = "stopped"
    proxy_module._save_config({"decisions": {"autostart": False}})
    await handles.autostart()
    assert handles.engine.starts == 1


# ---- a decision while it is stopped ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_decision_while_stopped_starts_it_in_the_background_and_goes_to_the_next_answerer(proxy_module, handles):
    proxy_module._save_config({"decisions": {}})
    res = await handles.try_local({"x": 1}, {"q": {"type": "noul"}}, "t1")
    assert res is None and handles.last_reason == "not_running", "this decision is not held up: the next answerer takes it"
    await asyncio.sleep(0.05)
    assert handles.engine.starts == 1


@pytest.mark.asyncio
async def test_a_restart_after_a_crash_is_one_per_five_minutes(proxy_module, handles):
    proxy_module._save_config({"decisions": {}})
    handles.engine.state = "died"
    await decide(handles)
    assert handles.engine.starts == 1
    handles.engine.state = "died"
    await decide(handles)
    await decide(handles)
    assert handles.engine.starts == 1, "not again within the five minutes"
    handles.last_auto_start -= dw.RESTART_EVERY + 1
    handles.engine.state = "died"
    await decide(handles)
    assert handles.engine.starts == 2 and dw.RESTART_EVERY == 300.0


@pytest.mark.asyncio
async def test_with_autostart_off_nothing_starts_by_itself(proxy_module, handles):
    proxy_module._save_config({"decisions": {"autostart": False}})
    await decide(handles)
    assert handles.engine.starts == 0


@pytest.mark.asyncio
async def test_not_installed_or_running_elsewhere_or_already_starting_does_nothing(proxy_module, handles):
    proxy_module._save_config({"decisions": {}})
    handles.engine._installed = False
    await decide(handles)
    handles.engine._installed, handles.engine.state = True, "starting"
    await decide(handles)
    assert handles.engine.starts == 0


# ---- Stop ---------------------------------------------------------------------------------------------------------------------------------------
def test_stop_is_respected_until_start(proxy_module, test_client, handles):
    proxy_module._save_config({"decisions": {}})
    assert test_client.post("/config/decisions/stop", json={}).status_code == 200
    assert handles.stopped_by_player is True and handles.engine.stops == 1
    asyncio.run(decide(handles))
    assert handles.engine.starts == 0, "a Stop stays stopped"
    assert test_client.post("/config/decisions/start", json={}).status_code == 200
    assert handles.stopped_by_player is False and handles.engine.starts == 1
    handles.engine.state = "stopped"
    handles.last_auto_start = 0.0
    asyncio.run(decide(handles))
    assert handles.engine.starts == 2, "after Start the rule applies again"
