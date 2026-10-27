"""Local models, part 2, through the real proxy app: routing to another device, honest failures, the Other
Devices tab and its routes. The "other device" is a model-server door on 127.0.0.1 with a fake llama-server
behind it (fixtures come from test_other_pcs.py).
"""
import json
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

import other_pcs as op
from test_other_pcs import allow_any_host, door, free_port, get, own_server  # noqa: F401  (fixtures)


@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


def chat(model, content="hi", stream=False, **extra):
    return {"model": model, "messages": [{"role": "user", "content": content}], "stream": stream, **extra}


@pytest_asyncio.fixture
async def paired(door, proxy_module, allow_any_host):  # noqa: F811
    """The proxy's own gaming-device list, paired with the door as 'den-pc'."""
    await proxy_module.remote_svc.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
    return door


@pytest.mark.asyncio
class TestRoutingToAPairedPc:
    async def test_a_chat_request_runs_on_the_other_pcs_model(self, paired, client):
        r = await client.post("/v1/chat/completions", json=chat("den-pc/den-model", "hello there"))
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "echo:hello there"

    async def test_streaming_passes_through(self, paired, client):
        async with client.stream("POST", "/v1/chat/completions", json=chat("den-pc/den-model", "stream me", stream=True)) as r:
            assert r.status_code == 200
            text = "".join([c async for c in r.aiter_text()])
        pieces = [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ") and l != "data: [DONE]"]
        assert "".join((p["choices"][0].get("delta") or {}).get("content") or "" for p in pieces if p.get("choices")) == "echo:stream me"

    async def test_pictures_pass_through(self, paired, client):
        content = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        r = await client.post("/v1/chat/completions", json=chat("den-pc/den-model", content))
        assert r.json()["choices"][0]["message"]["content"] == "I see a picture. echo:look"

    async def test_the_speeds_tab_names_the_pc_that_answered(self, paired, client, proxy_module):
        await client.post("/v1/chat/completions", json=chat("den-pc/den-model"))
        with sqlite3.connect(proxy_module.SPEED_HISTORY_DB) as conn:
            rows = conn.execute("SELECT model, provider, success FROM speed_history").fetchall()
        assert rows == [("den-pc/den-model", "den-pc", 1)]

    async def test_the_pc_being_off_is_a_real_http_error_and_its_pill_turns_offline(self, paired, client, proxy_module):
        await paired.set_enabled(False)
        r = await client.post("/v1/chat/completions", json=chat("den-pc/den-model"))
        assert r.status_code == 502
        assert "isn't answering. Is that device on and running MultiProxy?" in r.json()["error"]["message"]
        assert proxy_module.remote_svc.get("den-pc").online is False
        entry = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "remote")
        assert entry["state"] == "warn" and entry["testable"] is False

    async def test_a_stream_to_an_off_pc_fails_as_an_http_error_not_a_200(self, paired, client):
        await paired.set_enabled(False)
        r = await client.post("/v1/chat/completions", json=chat("den-pc/den-model", stream=True))
        assert r.status_code == 502

    async def test_a_model_stopped_on_the_other_pc_passes_its_plain_reason_through(self, paired, client, proxy_module):
        await paired.local.stop("den-model")
        r = await client.post("/v1/chat/completions", json=chat("den-pc/den-model"))
        assert r.status_code == 503 and "stopped" in r.json()["error"]["message"]
        assert proxy_module.remote_svc.get("den-pc").online is not False, "the device itself is fine; only that model is stopped"

    async def test_a_wrong_model_name_is_a_404_from_that_pc(self, paired, client):
        r = await client.post("/v1/chat/completions", json=chat("den-pc/nope"))
        assert r.status_code == 404

    async def test_it_comes_back_when_the_pc_does(self, paired, client, proxy_module):
        await paired.set_enabled(False)
        await client.post("/v1/chat/completions", json=chat("den-pc/den-model"))
        await paired.set_enabled(True)
        r = await client.post("/v1/chat/completions", json=chat("den-pc/den-model", "back"))
        assert r.status_code == 200 and proxy_module.remote_svc.get("den-pc").online is True


@pytest.mark.asyncio
class TestRoutingToYourOwnAiServer:
    async def _add(self, proxy_module, own, key=""):
        return await proxy_module.remote_svc.add_openai(f"http://127.0.0.1:{own['port']}", "den-llama", key)

    async def test_chat_stream_and_key(self, own_server, client, proxy_module):
        await self._add(proxy_module, own_server, "sk-xyz")
        r = await client.post("/v1/chat/completions", json=chat("den-llama/qwen-32b", "ping"))
        assert r.json()["choices"][0]["message"]["content"] == "own:ping"
        assert own_server["bodies"][-1]["model"] == "qwen-32b", "the other server sees ITS model name, not ours"
        assert own_server["auth"][-1] == "Bearer sk-xyz"
        async with client.stream("POST", "/v1/chat/completions", json=chat("den-llama/qwen-32b", "s", stream=True)) as rr:
            assert "own:s" in "".join([c async for c in rr.aiter_text()])

    async def test_a_model_name_that_itself_has_a_slash_still_works(self, own_server, client, proxy_module):
        await self._add(proxy_module, own_server)
        r = await client.post("/v1/chat/completions", json=chat("den-llama/meta/llama-3-8b", "x"))
        assert r.status_code == 200 and own_server["bodies"][-1]["model"] == "meta/llama-3-8b"

    async def test_pictures_pass_through(self, own_server, client, proxy_module):
        await self._add(proxy_module, own_server)
        content = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        r = await client.post("/v1/chat/completions", json=chat("den-llama/qwen-32b", content))
        assert r.json()["choices"][0]["message"]["content"] == "own:pic"
        assert own_server["bodies"][-1]["messages"][-1]["content"] == content

    async def test_llama_cpps_missing_vision_file_error_becomes_a_plain_hint(self, own_server, client, proxy_module):
        await self._add(proxy_module, own_server)
        own_server["vision_off"] = True
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        r = await client.post("/v1/chat/completions", json=chat("den-llama/qwen-32b", content))
        assert r.status_code == 500
        assert "Check its settings and try again" in r.json()["error"]["message"]

    async def test_a_server_that_is_off_is_a_plain_502_and_offline(self, own_server, client, proxy_module):
        await self._add(proxy_module, own_server)
        await own_server["runner"].cleanup()
        r = await client.post("/v1/chat/completions", json=chat("den-llama/qwen-32b"))
        assert r.status_code == 502 and "Is that AI server running?" in r.json()["error"]["message"]
        assert proxy_module.remote_svc.get("den-llama").online is False

    async def test_it_is_not_misread_as_the_removed_openrouter_route(self, own_server, client, proxy_module):
        await self._add(proxy_module, own_server)
        assert not proxy_module.is_unclaimed_slash_model("den-llama/qwen-32b")
        assert proxy_module.is_unclaimed_slash_model("not-a-nickname/qwen-32b")
        r = await client.post("/v1/chat/completions", json=chat("not-a-nickname/qwen-32b"))
        assert r.status_code == 404 and "OpenRouter" in r.json()["error"]["message"]


class TestWiring:
    @pytest.mark.asyncio
    async def test_models_and_provider_entry_list_online_models(self, paired, client, proxy_module):
        ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
        assert "den-pc/den-model" in ids
        entry = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "remote")
        assert entry["state"] == "ok" and entry["testable"] is True and entry["models"] == [["den-pc/den-model", "Den Model", "Sees pictures"]]

    @pytest.mark.asyncio
    async def test_pill_is_grey_with_no_other_pcs(self, proxy_module):
        entry = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "remote")
        assert entry["state"] == "off" and entry["status"] == "No other devices yet" and entry["models"] == []

    def test_a_default_model_on_another_pc_picks_the_remote_provider(self, proxy_module, monkeypatch):
        proxy_module.remote_svc._remotes["den-pc"] = op.Remote(id="den-pc", kind="openai", name="den-pc", base="http://192.168.1.2:8080")
        proxy_module.remote_svc._loaded = True
        monkeypatch.setattr(proxy_module, "_default_claude_model", lambda: "den-pc/gemma")
        assert proxy_module._default_provider_id() == "remote"

    @pytest.mark.parametrize("nick", ["ollama", "local", "glmzai", "nano", "openai", "deepseek", "kilo", "gemini", "codex", "copilot", "geminicli", "claude", "typesafe", "openrouter"])
    def test_built_in_prefixes_cannot_be_used_as_a_nickname(self, proxy_module, nick):
        assert "used by one of MultiProxy's providers" in proxy_module._nickname_problem(nick)

    def test_ordinary_nicknames_are_fine(self, proxy_module):
        assert proxy_module._nickname_problem("den-pc") is None and proxy_module._nickname_problem("") is not None

    def test_the_tab_the_pane_and_the_script_are_on_the_page(self, test_client):
        html = test_client.get("/").text
        assert 'id="tabOther"' in html and 'id="paneOther"' in html and "function opRender" in html
        assert "MODEL_SERVER" not in html and "Share with my other devices" not in html and "Let my main device use these models" not in html


@pytest.mark.asyncio
class TestRoutes:
    async def test_state_describes_everything_the_tab_draws(self, paired, client):
        d = (await client.get("/config/other-pcs?refresh=true")).json()
        assert set(d) >= {"newCodeConfirm", "servers", "downloadUrl", "linuxCommand", "thisPc", "mainPort"} and "share" not in d and "modelServer" not in d
        assert d["servers"][0]["id"] == "den-pc" and d["servers"][0]["online"] is True
        assert d["servers"][0]["models"][0]["modelName"] == "den-pc/den-model"
        assert d["downloadUrl"].endswith("MultiProxy-Courier.zip") and d["linuxCommand"].startswith("curl -fsSL ")

    async def test_add_multiproxy_and_remove_routes(self, door, client, allow_any_host, proxy_module):  # noqa: F811
        ok = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{door.port}", "code": door.code, "nickname": "den-pc"})
        assert ok.json() == {"status": "added", "id": "den-pc"}
        bad = await client.post("/config/other-pcs/servers/add-multiproxy", json={"address": f"127.0.0.1:{door.port}", "code": "WRONG1", "nickname": "other"})
        assert bad.status_code == 403 and "code doesn't match" in bad.json()["detail"]
        await client.post("/config/other-pcs/servers/remove", json={"id": "den-pc"})
        assert proxy_module.remote_svc.all() == []

    async def test_add_own_route_refuses_public_addresses_plainly(self, client):
        r = await client.post("/config/other-pcs/servers/add-own", json={"address": "http://8.8.8.8:8080", "nickname": "x"})
        assert r.status_code == 400 and r.json()["detail"] == op.PRIVATE_NETWORK_MESSAGE

    async def test_scan_route_marks_pcs_already_added(self, paired, client, proxy_module, monkeypatch):
        monkeypatch.setattr(op, "scan_network", lambda *a, **k: [
            {"name": "DEN-PC", "address": f"http://127.0.0.1:{paired.port}", "instance": "a", "gpu": None, "models": []},
            {"name": "OTHER", "address": "http://192.168.1.9:8100", "instance": "b", "gpu": None, "models": []}])
        found = (await client.post("/config/other-pcs/scan")).json()["found"]
        assert [(f["name"], f["paired"]) for f in found] == [("DEN-PC", True), ("OTHER", False)]

    async def test_the_sharing_routes_are_gone(self, client):
        assert (await client.post("/config/other-pcs/share", json={"enabled": True})).status_code in (404, 405)
        assert (await client.post("/config/other-pcs/share/new-code")).status_code in (404, 405)

    async def test_provider_entry_route(self, client):
        assert (await client.get("/config/other-pcs/provider")).json()["id"] == "remote"


@pytest.mark.asyncio
async def test_the_proxy_opens_no_sharing_door_and_drops_an_old_model_servers_leftover_settings(proxy_module, monkeypatch):
    proxy_module._save_config({"model_server": True, "share": {"enabled": True, "code": "ABC-DEF", "port": free_port()}, "keep": 1})

    class R:
        async def cleanup(self):
            pass

    async def runner():
        return R()
    monkeypatch.setattr(proxy_module, "start_interceptor", runner)
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_module, "OPEN_BROWSER_ON_START", False)
    async with proxy_module.lifespan(proxy_module.app):
        assert not hasattr(proxy_module, "share_mgr")
        cfg = proxy_module._load_config()
        assert "model_server" not in cfg and "share" not in cfg and cfg["keep"] == 1
        assert proxy_module.remote_svc._poll_task is not None
    assert proxy_module.remote_svc._poll_task is None
