"""The main device's side of every device run through its Courier (2026-10-01): pairing makes a Courier device with a live connection, its
report becomes the device's models / program label / graphics card, actions go through the Courier, a dropped connection marks it
offline at once, and the routes the Other devices tab calls. A real Courier (with fake Ollama / LM Studio behind it) answers on 127.0.0.1.
"""
import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
import pytest
from aiohttp import web

import courier_client as cc
import other_pcs as op
from tests.fixtures.courier_fakes import FakeOllama, free_port
from tests.test_courier import env  # noqa: F401  (the running Courier fixture)

pytestmark = pytest.mark.asyncio
log = logging.getLogger("test-other-pcs-courier")


@pytest.fixture
def allow_hosts(monkeypatch):
    """The other device is this device in these tests: let loopback through the home-network rule."""
    monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback=False: None)


def make_remotes(store):
    return op.RemoteServers(lambda: store, lambda d: store.update(d), log, lambda n: None, lambda: set(), client_factory=cc.CourierClient)


async def paired(env, store=None, nick="s2"):
    store = {} if store is None else store
    remotes = make_remotes(store)
    r = await remotes.add_multiproxy(f"127.0.0.1:{env.courier.settings.port}", env.courier.settings.code, nick)
    for _ in range(100):
        if r.report:
            break
        await asyncio.sleep(0.05)
    return remotes, r, store


async def until(cond, secs=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


class TestPairing:
    async def test_pairing_with_a_courier_makes_a_courier_pc_with_a_live_connection(self, env, allow_hosts):
        remotes, r, store = await paired(env)
        assert r.kind == "courier" and r.client is not None and r.online is True
        assert r.instance == env.courier.settings.id
        assert store["remote_servers"][0]["kind"] == "courier" and store["remote_servers"][0]["instance"] == env.courier.settings.id
        await remotes.stop_polling()

    async def test_its_report_becomes_the_pcs_models_program_label_and_card(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        assert r.program == "Ollama + LM Studio"
        assert r.gpu == {"name": "RTX 3080", "vendor": "nvidia", "memoryGb": 10.0}
        by = {m["id"]: m for m in r.models}
        assert {"gemma4:12b", "den-model"} <= set(by) and by["den-model"]["program"] == "llamacpp"
        assert [m["id"] for m in r.ready_models()] and "gemma4:12b" in [m["id"] for m in r.ready_models()], "Ollama loads a model when asked"
        j = r.to_json()
        assert j["kind"] == "courier" and j["program"] == "Ollama + LM Studio" and j["report"]["courier"]["id"] == env.courier.settings.id
        assert "s2/gemma4:12b" in [m["modelName"] for m in j["models"]]
        assert remotes.online_model_names() and ("s2/gemma4:12b", "gemma4:12b", False) in remotes.online_model_names()
        await remotes.stop_polling()

    async def test_the_program_label_is_just_llama_cpp_when_that_is_all_there_is(self, env, allow_hosts):
        assert op.courier_program_label({"programs": [{"id": "llamacpp", "name": "llama.cpp", "installed": True}]}) == "llama.cpp"
        assert op.courier_program_label({"programs": []}) == "llama.cpp" and op.courier_program_label(None) == "llama.cpp"
        assert op.courier_program_label({"programs": [{"id": "ollama", "name": "Ollama", "installed": True},
                                                      {"id": "lmstudio", "name": "LM Studio", "installed": False}]}) == "Ollama"

    async def test_a_chat_goes_through_the_courier(self, env, allow_hosts):
        import aiohttp
        remotes, r, _ = await paired(env)
        async with aiohttp.ClientSession() as s:
            async with s.post(r.api_base + "/chat/completions", headers=r.headers(),
                              json={"model": "gemma4:12b", "messages": [{"role": "user", "content": "via the list"}]}) as resp:
                assert (await resp.json())["choices"][0]["message"]["content"] == "ollama:via the list"
        await remotes.stop_polling()

    async def test_a_wrong_code_is_still_refused_in_plain_words(self, env, allow_hosts):
        remotes = make_remotes({})
        with pytest.raises(op.OtherPcsError) as e:
            await remotes.add_multiproxy(f"127.0.0.1:{env.courier.settings.port}", "BAD-000", "x")
        assert e.value.status == 403

    async def test_a_pc_paired_before_the_courier_screens_existed_becomes_a_courier_pc_by_itself(self, env, allow_hosts):
        store = {"remote_servers": [{"id": "old", "kind": "multiproxy", "name": "old", "base": f"http://127.0.0.1:{env.courier.settings.port}",
                                     "code": env.courier.settings.code}]}
        remotes = make_remotes(store)
        r = remotes.all()[0]
        assert r.kind == "multiproxy"
        await remotes._poll(r)
        assert r.kind == "courier" and r.client is not None and store["remote_servers"][0]["kind"] == "courier"
        assert await until(lambda: r.report is not None)
        await remotes.stop_polling()

    async def test_start_polling_opens_the_live_connection_of_every_saved_courier(self, env, allow_hosts):
        store = {"remote_servers": [{"id": "s2", "kind": "courier", "name": "s2", "base": f"http://127.0.0.1:{env.courier.settings.port}",
                                     "code": env.courier.settings.code, "instance": env.courier.settings.id}]}
        remotes = make_remotes(store)
        remotes.start_polling()
        r = remotes.all()[0]
        assert await until(lambda: r.report is not None and r.online is True)
        await remotes.stop_polling()
        assert r.client is None


class TestLiveState:
    async def test_a_dropped_connection_marks_the_pc_offline_at_once_and_it_comes_back_by_itself(self, env, allow_hosts, monkeypatch):
        monkeypatch.setattr(cc, "RECONNECT_START", 0.1)
        remotes, r, _ = await paired(env)
        t0 = time.monotonic()
        await env.courier.stop()
        assert await until(lambda: r.online is False, 3), "offline at once, not at the next 15 s check"
        assert time.monotonic() - t0 < 3
        await env.courier.start()
        assert await until(lambda: r.online is True, 10)
        await remotes.stop_polling()

    async def test_a_program_stopping_shows_up_in_the_pcs_report_without_asking(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        await env.ollama.stop()
        assert await until(lambda: next(p for p in r.report["programs"] if p["id"] == "ollama")["state"] == "stopped", 16)     # (the cheap "is it answering?" check runs every 10 s when idle)
        await remotes.stop_polling()

    async def test_polling_does_not_second_guess_a_live_connection(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        calls = []
        orig = remotes._session_factory
        remotes._session_factory = lambda: (calls.append(1), orig())[1]
        await remotes._poll(r)
        assert calls == [] and r.online is True
        await remotes.stop_polling()


class TestActions:
    async def test_load_unload_show_up_in_the_report_at_once(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        await remotes.courier_action("s2", "ollama", "load", "gemma4:12b")
        assert await until(lambda: any(m["loaded"] for p in r.report["programs"] if p["id"] == "ollama" for m in p["models"]))
        await remotes.courier_action("s2", "ollama", "unload", "gemma4:12b")
        assert not any(m["loaded"] for p in r.report["programs"] if p["id"] == "ollama" for m in p["models"])
        await remotes.stop_polling()

    async def test_delete_and_download_go_through(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        await remotes.courier_action("s2", "ollama", "delete", "gemma4:12b")
        assert env.ollama.deleted == ["gemma4:12b"]
        await remotes.courier_action("s2", "ollama", "download", "qwen3:14b")
        assert await until(lambda: "qwen3:14b" in env.ollama.models)
        await remotes.stop_polling()

    async def test_an_error_from_the_other_pc_comes_back_in_its_own_plain_words(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        with pytest.raises(op.OtherPcsError) as e:
            await remotes.courier_action("s2", "ollama", "load", "nope:1b")
        assert e.value.status == 404 and "Download it first" in e.value.message
        await remotes.stop_polling()

    async def test_settings_change_the_other_pc_and_its_report(self, env, allow_hosts):
        remotes, r, _ = await paired(env)
        await remotes.courier_settings("s2", keep_awake=False)
        assert r.report["settings"]["keep_awake"] is False and env.awake.calls[-1] == "stop"
        await remotes.courier_settings("s2", start_with_windows=False)
        assert env.registry.values == {}
        await remotes.stop_polling()

    async def test_a_new_pairing_code_is_kept_here_and_the_connection_follows_it(self, env, allow_hosts, monkeypatch):
        monkeypatch.setattr(cc, "RECONNECT_START", 0.1)
        remotes, r, store = await paired(env)
        old = r.code
        await remotes.courier_new_code("s2")
        assert r.code == env.courier.settings.code != old
        assert store["remote_servers"][0]["code"] == r.code
        assert await until(lambda: r.online is True and r.client.code == r.code, 8)
        assert (await env.err("GET", "/courier/v1/report", headers={"Authorization": f"Bearer {old}"}))[0] == 403
        await remotes.stop_polling()

    async def test_only_a_courier_pc_can_be_managed(self, env, allow_hosts):
        remotes = make_remotes({"remote_servers": [{"id": "den", "kind": "openai", "name": "den", "base": "http://127.0.0.1:1"}]})
        with pytest.raises(op.OtherPcsError) as e:
            await remotes.courier_action("den", "ollama", "load", "x")
        assert e.value.status == 400 and "doesn't have MultiProxy Courier installed" in e.value.message
        with pytest.raises(op.OtherPcsError) as e:
            await remotes.courier_action("nobody", "ollama", "load", "x")
        assert e.value.status == 404

    async def test_removing_a_pc_closes_its_connection(self, env, allow_hosts):
        remotes, r, store = await paired(env)
        c = r.client
        remotes.remove("s2")
        await asyncio.sleep(0.3)
        assert c._task is None or c._task.done() and not env.courier.clients
        assert store["remote_servers"] == []


class TestAddressChange:
    async def test_a_courier_pc_that_moved_is_followed_and_its_connection_re_pointed(self, env, allow_hosts, monkeypatch):
        monkeypatch.setattr(cc, "RECONNECT_START", 0.1)
        remotes, r, store = await paired(env)
        good = r.base
        c = r.client
        r.base = f"http://127.0.0.1:{free_port()}"          # the router handed out a new address (nothing answers there)
        remotes._ensure_client(r)
        assert r.client is not c, "the connection follows the address"
        assert await until(lambda: r.online is False, 5)
        found = await asyncio.to_thread(op.scan_network, 3.0, env.courier.discovery_port, "", ["127.0.0.1"])
        assert await remotes.follow_moved([r], scan=lambda: found) == 1
        assert r.base == good and await until(lambda: r.online is True, 15)
        await remotes.stop_polling()


class TestMemoryPicker:
    async def test_a_courier_pcs_card_numbers_are_listed_with_their_real_figures(self, env, allow_hosts, monkeypatch):
        async def nothing(include_skyrim=True): return {"available": False}
        monkeypatch.setattr(env.courier.monitor, "payload", nothing)
        remotes, r, _ = await paired(env)
        m = await remotes.memory(r)
        assert m["state"] == "ok" and m["card"] == "RTX 3080" and m["totalMb"] == 10240 and m["usedMb"] == 8600 and m["freeMb"] == 1640
        assert m["skyrimShown"] is False
        await remotes.stop_polling()

    async def test_the_couriers_own_monitor_is_used_when_it_has_one(self, env, allow_hosts, monkeypatch):
        async def has(include_skyrim=True):
            return {"available": True, "card": "RTX 3080", "totalMb": 10240, "usedMb": 4000, "segments": [{"key": "model:x", "label": "X", "mb": 3000}],
                    "otherMb": 1000, "freeMb": 6240}
        monkeypatch.setattr(env.courier.monitor, "payload", has)
        remotes, r, _ = await paired(env)
        m = await remotes.memory(r)
        assert m["state"] == "ok" and m["segments"][0]["label"] == "X"
        await remotes.stop_polling()

    async def test_a_pc_with_no_card_numbers_says_so(self, env, allow_hosts):
        r = op.Remote(id="x", kind="courier", name="x", base="http://127.0.0.1:1", report={"gpus": [{"name": "AMD", "memory_total_mb": 8192, "memory_used_mb": None}]})
        assert op.RemoteServers._memory_from_report(r) == {"available": False}


class TestProgramDetection:
    async def test_an_ai_server_added_by_address_is_recognised_by_what_it_answers(self, env, allow_hosts):
        remotes = make_remotes({})
        r = await remotes.add_openai(env.ollama.base, "den-ollama")
        assert r.program == "Ollama" and r.kind == "openai"
        await env.lms.start()
        r2 = await remotes.add_openai(env.lms.base, "den-lms")
        assert r2.program == "LM Studio"

    async def test_llama_server_is_recognised_by_its_props_route(self, env, allow_hosts):
        app = web.Application()

        async def models(request): return web.json_response({"data": [{"id": "qwen", "owned_by": "llamacpp"}]})
        async def props(request): return web.json_response({"default_generation_settings": {}})
        app.router.add_get("/v1/models", models)
        app.router.add_get("/props", props)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        port = free_port()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            r = await make_remotes({}).add_openai(f"http://127.0.0.1:{port}", "den-llama")
            assert r.program == "llama.cpp"
        finally:
            await runner.cleanup()

    async def test_anything_else_is_just_an_ai_server(self, env, allow_hosts):
        app = web.Application()

        async def models(request): return web.json_response({"data": [{"id": "x"}]})
        app.router.add_get("/v1/models", models)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        port = free_port()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            r = await make_remotes({}).add_openai(f"http://127.0.0.1:{port}", "den-x")
            assert r.program == "AI server"
        finally:
            await runner.cleanup()

    async def test_an_older_model_server_is_labelled_multiproxy(self, env, allow_hosts):
        r = op.Remote(id="old", kind="multiproxy", name="old", base="http://127.0.0.1:1")
        assert r.to_json()["program"] == "" and r.kind == "multiproxy"      # the poll sets "MultiProxy" (covered by the door tests)


# ---- the routes the tab calls ---------------------------------------------------------------------------------------
@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


class TestRoutes:
    async def _pair(self, env, client, proxy_module):
        r = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{env.courier.settings.port}",
                                                                                 "code": env.courier.settings.code, "nickname": "s2"})
        assert r.status_code == 200 and r.json() == {"status": "added", "id": "s2"}
        rem = proxy_module.remote_svc.get("s2")
        assert await until(lambda: rem.report is not None)
        return rem

    async def test_the_state_carries_the_courier_pc_and_its_report(self, env, client, proxy_module, allow_hosts):
        await self._pair(env, client, proxy_module)
        d = (await client.get("/config/other-pcs")).json()
        s = d["servers"][0]
        assert s["kind"] == "courier" and s["program"] == "Ollama + LM Studio" and s["report"]["programs"][0]["id"] == "ollama"
        assert d["downloadUrl"].endswith("MultiProxy-Courier.zip") and d["linuxCommand"].endswith("install-courier.sh | bash")
        await proxy_module.remote_svc.stop_polling()

    async def test_actions_settings_and_a_new_code(self, env, client, proxy_module, allow_hosts):
        rem = await self._pair(env, client, proxy_module)
        ok = await client.post("/config/other-pcs/courier/action", json={"id": "s2", "program": "ollama", "action": "load", "model": "gemma4:12b"})
        assert ok.status_code == 200 and ok.json() == {"status": "ok"}
        assert await until(lambda: "gemma4:12b" in env.ollama.loaded)
        assert (await client.post("/config/other-pcs/courier/action", json={"id": "s2", "program": "ollama", "action": "unload", "model": "gemma4:12b"})).status_code == 200
        assert (await client.post("/config/other-pcs/courier/settings", json={"id": "s2", "keep_awake": False})).json() == {"status": "ok"}
        assert rem.report["settings"]["keep_awake"] is False
        old = rem.code
        assert (await client.post("/config/other-pcs/courier/new-code", json={"id": "s2"})).json() == {"status": "ok"}
        assert rem.code != old and rem.code == env.courier.settings.code
        await proxy_module.remote_svc.stop_polling()

    async def test_errors_come_back_as_plain_words(self, env, client, proxy_module, allow_hosts):
        await self._pair(env, client, proxy_module)
        r = await client.post("/config/other-pcs/courier/action", json={"id": "s2", "program": "ollama", "action": "load", "model": "nope:1b"})
        assert r.status_code == 404 and "Download it first" in r.json()["detail"]
        assert (await client.post("/config/other-pcs/courier/action", json={"id": "s2", "program": "ollama", "action": "explode"})).status_code == 400
        assert (await client.post("/config/other-pcs/courier/action", json={"id": "nobody", "program": "ollama", "action": "load", "model": "x"})).status_code == 404
        await proxy_module.remote_svc.stop_polling()

    async def test_the_memory_picker_lists_the_courier_pc_with_its_card(self, env, client, proxy_module, allow_hosts, monkeypatch):
        async def nothing(include_skyrim=True): return {"available": False}
        monkeypatch.setattr(env.courier.monitor, "payload", nothing)
        await self._pair(env, client, proxy_module)
        d = (await client.get("/config/local-models/memory?pc=s2")).json()
        assert [p["id"] for p in d["pcs"]] == ["this", "s2"] and d["pcs"][1]["label"] == "s2 · RTX 3080" and d["pcs"][1]["kind"] == "multiproxy"
        assert d["state"] == "ok" and d["card"] == "RTX 3080" and d["totalMb"] == 10240
        await proxy_module.remote_svc.stop_polling()

    async def test_the_pill_lists_the_courier_pcs_models_and_a_chat_through_the_proxy_reaches_it(self, env, client, proxy_module, allow_hosts):
        await self._pair(env, client, proxy_module)
        pill = (await client.get("/config/other-pcs/provider")).json()
        assert "s2/gemma4:12b" in [row[0] for row in pill["models"]]
        r = await client.post("/v1/chat/completions", json={"model": "s2/gemma4:12b", "messages": [{"role": "user", "content": "through the proxy"}]})
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "ollama:through the proxy"
        await proxy_module.remote_svc.stop_polling()

    async def test_an_unreachable_courier_pc_is_a_plain_502_that_names_the_courier(self, proxy_module):
        r = op.Remote(id="s2", kind="courier", name="s2", base="http://127.0.0.1:1", code="X")
        assert "running the Courier" in proxy_module._remote_unreachable(r).detail

    async def test_the_download_and_install_links_point_at_the_courier(self):
        assert op.MODEL_SERVER_DOWNLOAD_URL.endswith("/MultiProxy-Courier.zip") and op.LINUX_INSTALL_COMMAND.endswith("install-courier.sh | bash")


# ---- models already on that device, and where new ones are saved (2026-10-01) ------------------------------------------------
class TestModelsOnThatPc:
    @staticmethod
    def _model(path, mb=60):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.truncate(mb * 1024 * 1024)

    async def test_find_add_and_remove_through_the_gaming_pcs_routes(self, env, client, proxy_module, allow_hosts):
        self._model(env.lms_models / "google" / "gemma" / "gemma-4-12b-Q4.gguf")
        rem = await TestRoutes()._pair(env, client, proxy_module)
        found = (await client.post("/config/other-pcs/courier/models/find", json={"id": "s2"})).json()
        item = next(i for i in found["found"] if i["name"] == "gemma-4-12b-Q4")
        assert item["source"] == "lmstudio" and item["added"] is False
        added = (await client.post("/config/other-pcs/courier/models/add", json={"id": "s2", "paths": [item["path"]]})).json()
        assert [a["name"] for a in added["added"]] == ["gemma-4-12b-Q4"]
        assert any(m["id"] == "gemma-4-12b-q4" and m["program"] == "llamacpp" for m in rem.models), "it shows in that device's list at once"
        ok = await client.post("/config/other-pcs/courier/action", json={"id": "s2", "program": "llamacpp", "action": "delete", "model": "gemma-4-12b-q4"})
        assert ok.status_code == 200 and Path(item["path"]).is_file(), "removing it from the list never deletes the file"
        await proxy_module.remote_svc.stop_polling()

    async def test_the_scan_routes_start_report_and_stop(self, env, client, proxy_module, allow_hosts, tmp_path, monkeypatch):
        self._model(tmp_path / "drive" / "found.gguf")
        monkeypatch.setattr(env.courier.scanner, "_roots", lambda: [tmp_path / "drive"])
        await TestRoutes()._pair(env, client, proxy_module)
        assert (await client.post("/config/other-pcs/courier/models/scan", json={"id": "s2", "action": "start"})).status_code == 200
        for _ in range(100):
            s = (await client.post("/config/other-pcs/courier/models/scan", json={"id": "s2", "action": "status"})).json()
            if s["state"] == "done":
                break
            await asyncio.sleep(0.05)
        assert [i["name"] for i in s["found"]] == ["found"]
        assert (await client.post("/config/other-pcs/courier/models/scan", json={"id": "s2", "action": "explode"})).status_code == 400
        await proxy_module.remote_svc.stop_polling()

    async def test_the_drive_list_and_the_folder_choice(self, env, client, proxy_module, allow_hosts, tmp_path):
        rem = await TestRoutes()._pair(env, client, proxy_module)
        d = (await client.get("/config/other-pcs/courier/drives?id=s2")).json()
        assert d["drives"] and d["chosen"] is False and rem.report["settings"]["models_folder_chosen"] is False
        target = tmp_path / "Big" / "Models"
        r = await client.post("/config/other-pcs/courier/models-folder", json={"id": "s2", "path": str(target)})
        assert r.status_code == 200 and target.is_dir()
        assert rem.report["settings"]["models_folder"] == str(target) and rem.report["settings"]["models_folder_chosen"] is True, "the report shows it at once"
        bad = await client.post("/config/other-pcs/courier/models-folder", json={"id": "s2", "path": "relative"})
        assert bad.status_code == 400 and bad.json()["detail"].startswith("Couldn't use that folder on ")
        await proxy_module.remote_svc.stop_polling()

    async def test_a_pc_without_a_courier_or_not_in_the_list_is_refused(self, env, client, proxy_module, allow_hosts):
        assert (await client.post("/config/other-pcs/courier/models/find", json={"id": "nobody"})).status_code == 404
        assert (await client.get("/config/other-pcs/courier/drives?id=nobody")).status_code == 404
        assert (await client.post("/config/other-pcs/courier/models/add", json={"id": "nobody", "paths": "x"})).status_code == 400
