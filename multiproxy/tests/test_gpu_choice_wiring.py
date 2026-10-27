"""Which graphics card each local AI uses, the main device's side (Routing, step 8): the saved choices, /config/gpu-choice, a Courier device through its client, and the
Settings card's words. Fakes stand in for the engines and the cards (no real graphics card, no real engine)."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import gpu_choice as gc
import gpu_choice_ui
import gpu_choice_wiring as gw
import settings_ui

ROOT = Path(__file__).resolve().parent.parent

BIG = gc.Card("GPU-aaaa", "NVIDIA GeForce RTX 5090", 32768, "nvidia")
SMALL = gc.Card("GPU-bbbb", "NVIDIA GeForce RTX 3060", 12288, "nvidia")
IGPU = gc.Card("pnp:INTEL", "Intel(R) UHD Graphics", 1024, "intel")


class FakeLocal:
    def __init__(self, running=()):
        self.running = set(running)
        self.gpu_cards, self.gpu_notes = {}, {}

    def installed(self):
        return [SimpleNamespace(id="gemma", name="gemma-4-12b"), SimpleNamespace(id="qwen", name="qwen-3.5-9b")]

    def state_of(self, mid):
        return ("running" if mid in self.running else "stopped", "")


class FakeEngine:
    def __init__(self, installed=True, state="stopped"):
        self._installed, self.state, self.gpu_card, self.gpu_note = installed, state, None, ""

    def installed(self):
        return self._installed


class FakeClient:
    def __init__(self, block):
        self.block, self.posts = block, []

    async def gpu_post(self, body):
        self.posts.append(body)
        return self.block


class FakeRemotes:
    def __init__(self, *pcs):
        self.pcs = list(pcs)

    def all(self):
        return self.pcs


@pytest_asyncio.fixture
async def rig():
    r = SimpleNamespace(cfg={}, cards=[BIG, SMALL, IGPU], local=FakeLocal(), dec=FakeEngine(), voice=FakeEngine(installed=False), remotes=FakeRemotes())
    app = FastAPI()
    r.handles = gw.setup(app, lambda: r.cfg, lambda c: r.cfg.update(c) if c is not r.cfg else None, r.local, r.dec, r.voice, remotes=r.remotes, cards_fn=lambda: r.cards)
    r.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    yield r
    await r.http.aclose()


class TestPure:
    def test_choice_reads_fresh_and_ignores_junk(self):
        cfg = {}
        one = gw.choice_fn(lambda: cfg, "decisions")
        local = gw.local_choice_fn(lambda: cfg)
        assert one() is None and local("gemma") is None
        cfg["gpu_choices"] = {"decisions": {"id": "GPU-aaaa"}, "local:gemma": {"id": "GPU-bbbb"}, "voice": "nope"}
        assert one() == {"id": "GPU-aaaa"} and local("gemma") == {"id": "GPU-bbbb"} and gw.choice_fn(lambda: cfg, "voice")() is None

    def test_set_and_clear_leave_no_trace_when_automatic(self):
        cfg = {"x": 1}
        block = gw.build_block(FakeLocal(), FakeEngine(), FakeEngine(installed=False), {}, [BIG, SMALL, IGPU])
        assert gw.set_choice(cfg, block, [BIG, SMALL, IGPU], "decisions", "GPU-bbbb") is True
        assert cfg["gpu_choices"]["decisions"] == gc.choice_of(SMALL)
        assert gw.set_choice(cfg, block, [BIG, SMALL, IGPU], "decisions", None) is True
        assert cfg == {"x": 1}                                   # the key is gone again: the file looks as it did before

    def test_refuses_an_unknown_ai_an_unknown_card_and_a_built_in_card(self):
        block = gw.build_block(FakeLocal(), FakeEngine(), FakeEngine(installed=False), {}, [BIG, SMALL, IGPU])
        assert [a["key"] for a in block["ais"]] == ["local:gemma", "local:qwen", "decisions"]       # voice isn't installed: not listed
        with pytest.raises(gw.ChoiceError) as e:
            gw.set_choice({}, block, [BIG, SMALL, IGPU], "voice", "GPU-aaaa")
        assert e.value.message == gw.NO_AI
        for bad in ("GPU-zzzz", IGPU.id):
            with pytest.raises(gw.ChoiceError) as e:
                gw.set_choice({}, block, [BIG, SMALL, IGPU], "decisions", bad)
            assert e.value.message == gw.NO_CARD

    def test_a_running_ai_needs_a_restart_until_it_is_on_the_chosen_card(self):
        local = FakeLocal(running={"gemma"})
        ch = {"local:gemma": gc.choice_of(SMALL)}
        row = lambda: next(a for a in gw.build_block(local, FakeEngine(), FakeEngine(installed=False), ch, [BIG, SMALL])["ais"] if a["key"] == "local:gemma")
        assert row()["restartNeeded"] is True
        local.gpu_cards["gemma"] = SMALL.name
        assert row()["restartNeeded"] is False
        local.running = set()
        local.gpu_cards["gemma"] = None
        assert row()["restartNeeded"] is False                   # stopped: it just starts on the card next time

    def test_a_card_that_is_gone_says_so_in_plain_words(self):
        ch = {"decisions": {"id": "GPU-old", "name": "RTX 2070", "memoryGb": 8}}
        a = gw.build_block(FakeLocal(), FakeEngine(), FakeEngine(installed=False), ch, [BIG, SMALL])["ais"][-1]
        assert a["note"] == "The graphics card you chose (RTX 2070) isn't in this device anymore, so Automatic is used."


@pytest.mark.asyncio
class TestThisPcRoutes:
    async def test_one_card_means_nothing_is_shown(self, rig):
        rig.cards = [BIG, IGPU]
        d = (await rig.http.get("/config/gpu-choice")).json()
        assert d == {"pcs": [], "shown": False}

    async def test_two_cards_list_every_installed_ai_with_short_names(self, rig):
        d = (await rig.http.get("/config/gpu-choice")).json()
        assert d["shown"] is True and [p["id"] for p in d["pcs"]] == ["this"]
        pc = d["pcs"][0]
        assert [c["shortName"] for c in pc["cards"]] == ["RTX 5090", "RTX 3060"] and pc["cards"][0]["memoryGb"] == 32.0
        assert [a["label"] for a in pc["ais"]] == ["gemma-4-12b", "qwen-3.5-9b", "Local decision model"]
        assert all(a["choice"] is None and a["note"] == "" and not a["restartNeeded"] for a in pc["ais"])
        assert "gpu_choices" not in rig.cfg

    async def test_choosing_saves_and_a_running_ai_is_told_to_restart(self, rig):
        rig.local.running = {"gemma"}
        d = (await rig.http.post("/config/gpu-choice", json={"pc": "this", "ai": "local:gemma", "cardId": "GPU-bbbb"})).json()
        assert d["restartNeeded"] is True and d["ai"] == "local:gemma"
        assert rig.cfg["gpu_choices"] == {"local:gemma": gc.choice_of(SMALL)}
        d2 = (await rig.http.post("/config/gpu-choice", json={"pc": "this", "ai": "decisions", "cardId": "GPU-aaaa"})).json()
        assert d2["restartNeeded"] is False                      # the decision model isn't running
        again = (await rig.http.get("/config/gpu-choice")).json()["pcs"][0]["ais"]
        assert again[0]["choice"]["id"] == "GPU-bbbb" and again[0]["restartNeeded"] is True
        back = (await rig.http.post("/config/gpu-choice", json={"pc": "this", "ai": "local:gemma", "cardId": None})).json()
        assert back["pcs"][0]["ais"][0]["choice"] is None

    async def test_refusals_are_plain(self, rig):
        r = await rig.http.post("/config/gpu-choice", json={"pc": "this", "ai": "voice", "cardId": "GPU-aaaa"})
        assert r.status_code == 404 and r.json()["detail"] == "That AI isn't installed on that device."
        r = await rig.http.post("/config/gpu-choice", json={"pc": "this", "ai": "decisions", "cardId": "GPU-nope"})
        assert r.status_code == 404 and r.json()["detail"] == "That graphics card isn't in that device anymore."
        r = await rig.http.post("/config/gpu-choice", json={"pc": "nobody", "ai": "decisions", "cardId": None})
        assert r.status_code == 404 and r.json()["detail"] == "That device isn't set up for this."


def courier_pc(client, online=True, cards=(BIG, SMALL), ais=None):
    block = {"cards": [gw.card_json(c) for c in cards], "ais": ais if ais is not None else [{"key": "decisions", "label": "Local decision model", "choice": None, "note": "", "running": False, "restartNeeded": False}]}
    return SimpleNamespace(id="den-pc", name="DEN-PC", kind="courier", online=online, client=client, report={"gpu_choice": block})


@pytest.mark.asyncio
class TestCourierPcs:
    async def test_a_courier_with_two_cards_is_grouped_by_its_name(self, rig):
        rig.remotes.pcs = [courier_pc(FakeClient({}))]
        d = (await rig.http.get("/config/gpu-choice")).json()
        assert [(p["id"], p["name"]) for p in d["pcs"]] == [("this", "This device"), ("den-pc", "DEN-PC")]

    async def test_a_courier_with_one_card_or_an_old_courier_is_left_out(self, rig):
        old = courier_pc(FakeClient({}))
        old.report = {}
        rig.remotes.pcs = [courier_pc(FakeClient({}), cards=(BIG,)), old]
        d = (await rig.http.get("/config/gpu-choice")).json()
        assert [p["id"] for p in d["pcs"]] == ["this"]

    async def test_choosing_on_a_courier_goes_through_its_client(self, rig):
        new = {"cards": [gw.card_json(BIG), gw.card_json(SMALL)], "ais": [{"key": "decisions", "label": "Local decision model", "choice": gc.choice_of(BIG), "note": "", "running": True, "restartNeeded": True}]}
        client = FakeClient(new)
        rig.remotes.pcs = [courier_pc(client)]
        d = (await rig.http.post("/config/gpu-choice", json={"pc": "den-pc", "ai": "decisions", "cardId": "GPU-aaaa"})).json()
        assert client.posts == [{"ai": "decisions", "cardId": "GPU-aaaa"}] and d["restartNeeded"] is True
        assert next(p for p in d["pcs"] if p["id"] == "den-pc")["ais"][0]["choice"]["id"] == "GPU-aaaa"
        assert "gpu_choices" not in rig.cfg                      # the choice lives on that device, not here

    async def test_an_offline_courier_cannot_be_changed(self, rig):
        client = FakeClient({})
        rig.remotes.pcs = [courier_pc(client, online=False)]
        r = await rig.http.post("/config/gpu-choice", json={"pc": "den-pc", "ai": "decisions", "cardId": None})
        assert r.status_code == 409 and r.json()["detail"] == "That device is offline right now, so you can't change its graphics card." and client.posts == []


class TestCardWords:
    def test_the_card_says_what_the_mockup_says(self):
        assert gpu_choice_ui.HEADING == "Which graphics card"
        assert gpu_choice_ui.HINT == "With two graphics cards, MultiProxy remembers which card each local AI uses, so a restart or a driver update does not move it."
        for s in (gpu_choice_ui.HEADING, gpu_choice_ui.HINT, gpu_choice_ui.AUTOMATIC, gpu_choice_ui.RESTART_HINT, gpu_choice_ui.RESTART_BUTTON):
            assert s in gpu_choice_ui.JS
        assert "Restart it to use this." in gpu_choice_ui.JS and "Restart now" in gpu_choice_ui.JS

    def test_it_is_in_the_page_and_hidden_until_a_pc_has_two_cards(self):
        assert 'id="stGpuCard" style="display:none"' in settings_ui.PANE_HTML
        assert "gcLoad" in settings_ui.JS
        page = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
        assert "{gpu_choice_ui.CSS}" in page and "{gpu_choice_ui.JS}" in page
        assert "!gcState.shown" in gpu_choice_ui.JS

    def test_every_restart_goes_to_that_ais_existing_route(self):
        for route in ("/config/decisions/restart", "/config/voice/restart", "/config/voice/pc/restart", "/config/local-models/stop", "/config/local-models/start",
                      "/config/other-pcs/courier/action"):
            assert route in gpu_choice_ui.JS
        page = (ROOT / "multiproxy.py").read_text(encoding="utf-8") + (ROOT / "voice_wiring.py").read_text(encoding="utf-8") + (ROOT / "decision_wiring.py").read_text(encoding="utf-8")
        for route in ("/config/decisions/restart", "/config/voice/restart", "/config/voice/pc/{action}", "/config/local-models/stop", "/config/local-models/start", "/config/other-pcs/courier/action"):
            assert route in page
