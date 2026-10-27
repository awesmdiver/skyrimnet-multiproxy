"""Keep a local Ollama model loaded (ollama_keepwarm.py): the warm-up carries keep_alive -1 to the native API, is not sent per request, is sent when the model is not loaded,
is not sent for a model that does not fit or that just failed, turning it off restores Ollama's default, and a real request is never held up by it."""
import asyncio

import pytest

import ollama_keepwarm as kw
import routing

GB = 1024 ** 3


class Fake:
    """A stand-in Ollama: what is loaded, what is installed, how much room there is, and every native request it was sent."""

    def __init__(self, loaded=(), sizes=None, free=24 * GB, fail=False, delay=0.0):
        self.loaded = {m: {} for m in loaded}
        self.sizes = sizes if sizes is not None else {"gemma4:latest": 9 * GB}
        self.free, self.fail, self.delay = free, fail, delay
        self.sent = []
        self.down = False
        self.now = 1000.0

    async def ps(self):
        return None if self.down else dict(self.loaded)

    async def tags(self):
        return dict(self.sizes)

    async def load(self, model, keep_alive):
        self.sent.append((model, keep_alive))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("model requires more system memory")
        self.loaded[model] = {}

    async def room(self):
        return self.free

    def warm(self):
        return kw.KeepWarm(ps=self.ps, sizes=self.tags, load=self.load, free_bytes=self.room, clock=lambda: self.now)


async def run(k, wanted, **kw_):
    await k.tick(wanted, **kw_)
    await k.wait()


def test_the_warm_up_carries_keep_alive_minus_one_and_no_prompt():
    f = Fake()
    k = f.warm()
    asyncio.run(run(k, ["gemma4:latest"]))
    assert f.sent == [("gemma4:latest", -1)] and k.states["gemma4:latest"] == kw.LOADED


def test_a_loaded_model_is_not_warmed_again_on_every_look():
    f = Fake()
    k = f.warm()

    async def go():
        await run(k, ["gemma4:latest"])
        for _ in range(5):                                   # five more looks (and so five requests' worth of time) while it stays loaded
            await run(k, ["gemma4:latest"])
    asyncio.run(go())
    assert f.sent == [("gemma4:latest", -1)]


def test_it_is_sent_again_when_ollama_has_unloaded_it():
    f = Fake()
    k = f.warm()

    async def go():
        await run(k, ["gemma4:latest"])
        f.loaded.clear()                                     # five idle minutes
        await run(k, ["gemma4:latest"])
    asyncio.run(go())
    assert f.sent == [("gemma4:latest", -1)] * 2


def test_a_model_already_loaded_by_a_real_request_is_asked_once_to_stay():
    f = Fake(loaded=["gemma4:latest"])
    k = f.warm()
    asyncio.run(run(k, ["gemma4:latest"]))
    asyncio.run(run(k, ["gemma4:latest"]))
    assert f.sent == [("gemma4:latest", -1)]


def test_a_model_that_does_not_fit_is_not_warmed():
    f = Fake(free=4 * GB)
    k = f.warm()
    asyncio.run(run(k, ["gemma4:latest"]))
    assert f.sent == [] and k.states["gemma4:latest"] == kw.NO_ROOM
    assert "graphics memory" in k.card("gemma4")["words"]


def test_a_model_that_failed_rests_ten_minutes_then_is_tried_again():
    f = Fake(fail=True)
    k = f.warm()

    async def go():
        await run(k, ["gemma4:latest"])
        assert k.states["gemma4:latest"] == kw.FAILED and "memory" in k.card("gemma4:latest")["problem"]
        f.now += 300
        await run(k, ["gemma4:latest"])                       # five minutes later: still resting
        assert len(f.sent) == 1
        f.now += 400
        f.fail = False
        await run(k, ["gemma4:latest"])                       # past ten minutes: tried again
    asyncio.run(go())
    assert len(f.sent) == 2 and k.states["gemma4:latest"] == kw.LOADED


def test_a_stopped_ollama_sends_nothing():
    f = Fake()
    f.down = True
    k = f.warm()
    asyncio.run(run(k, ["gemma4:latest"]))
    assert f.sent == [] and k.card("gemma4:latest")["state"] == kw.UNKNOWN


def test_turning_it_off_puts_ollamas_default_back_and_stops_warming():
    f = Fake()
    k = f.warm()

    async def go():
        await run(k, ["gemma4:latest"])
        await run(k, ["gemma4:latest"], off={"gemma4:latest"})
        f.loaded.clear()
        await run(k, ["gemma4:latest"], off={"gemma4:latest"})
    asyncio.run(go())
    assert f.sent == [("gemma4:latest", -1), ("gemma4:latest", kw.OLLAMA_DEFAULT)]
    assert k.card("gemma4:latest", {"gemma4:latest"})["on"] is False


def test_the_warm_up_never_holds_up_the_look_that_started_it():
    f = Fake(delay=0.3)
    k = f.warm()

    async def go():
        t = asyncio.get_event_loop().time()
        await k.tick(["gemma4:latest"])                        # returns at once; the load goes on in the background
        quick = asyncio.get_event_loop().time() - t
        assert k.states["gemma4:latest"] == kw.LOADING
        await k.tick(["gemma4:latest"])                        # a second look while loading does not send a second request
        await k.wait()
        return quick
    quick = asyncio.run(go())
    assert quick < 0.1 and len(f.sent) == 1


def test_another_keep_alive_value_is_passed_on():
    f = Fake()
    k = f.warm()
    asyncio.run(run(k, ["gemma4:latest"], keep_alive="30m"))
    assert f.sent == [("gemma4:latest", "30m")]


def test_only_the_first_choice_of_a_route_is_kept():
    routes = {"dialogue": {"ais": ["ollama/gemma4", "ollama/other:7b"]}, "memory": {"ais": ["openai/gpt-x", "ollama/third"]}, "combat": {"ais": ["ollama/gemma4:latest"]}}
    got = kw.first_choices(routes, lambda a: [a], lambda a: a.startswith("ollama/"), lambda a: a[len("ollama/"):])
    assert got == ["gemma4:latest"], "a name with no tag matches Ollama's :latest; backups and non-Ollama first choices are left alone"


def test_the_off_list_is_saved_and_cleaned():
    s = routing.clean_settings({"keep_loaded_off": ["gemma4:latest", "gemma4:latest", "", 5]})
    assert s["keep_loaded_off"] == ["gemma4:latest"] and routing.clean_settings({})["keep_loaded_off"] == []
