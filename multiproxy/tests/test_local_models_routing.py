"""Local models (2026-09-30), proxy side: `local/<model>` routing, streaming, images, honest errors,
the dashboard entry and the /config/local-models routes -- with a fake llama-server process behind
it, through the real FastAPI app (no real model, graphics card or download).
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio

import local_models as lm

FAKE_SERVER = str(Path(__file__).parent / "fixtures" / "fake_llama_server.py")


@pytest_asyncio.fixture
async def local(proxy_module, tmp_path, monkeypatch):
    """The proxy's own local_svc, pointed at a fake engine + one fake downloaded model."""
    monkeypatch.setattr(lm, "HEALTH_POLL_SECONDS", 0.05)
    svc = proxy_module.local_svc
    svc._command_builder = lambda exe, model, port, gpu: [sys.executable, FAKE_SERVER, "--port", str(port)]
    folder = svc.set_folder(str(tmp_path / "models"))
    eng = folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-cpu"
    eng.mkdir(parents=True)
    (eng / ("llama-server.exe" if os.name == "nt" else "llama-server")).write_bytes(b"fake")
    svc._update(engine={"kind": "cpu", "build": lm.LLAMA_CPP_BUILD})
    # One downloaded "model" -- a player-added GGUF, so no giant catalog file has to exist.
    gguf = tmp_path / "Tiny Chat.gguf"
    gguf.write_bytes(b"x")
    mm = tmp_path / "mmproj-tiny.gguf"
    mm.write_bytes(b"y")
    svc.add_custom(str(gguf), str(mm))
    yield svc
    await svc.stop_all()


@pytest_asyncio.fixture
async def running(local):
    s = await local.start("tiny-chat")
    await s.ready.wait()
    assert s.state == "running"
    return local


@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


def _chat(model="local/tiny-chat", content="hi", stream=False, **extra):
    return {"model": model, "messages": [{"role": "user", "content": content}], "stream": stream, **extra}


# ---- routing names -------------------------------------------------------------------------
def test_local_prefix_is_its_own_provider(proxy_module):
    p = proxy_module
    assert p.is_local_model("local/gemma-4-12b") and p.is_local_model("LOCAL/Gemma-4-12B")
    assert p.strip_local_prefix("local/gemma-4-12b") == "gemma-4-12b"
    assert p._resolve_model_name("local/gemma-4-12b") == "gemma-4-12b"
    assert not p.is_unclaimed_slash_model("local/gemma-4-12b"), "a claimed prefix is never 'unclaimed'"
    assert not p.is_local_model("ollama/local/x") and not p.is_local_model("gemma-4-12b")
    assert not p.is_local_model("localhost/foo") and p.is_unclaimed_slash_model("localhost/foo")


def test_a_local_default_model_picks_the_local_provider(proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "_default_claude_model", lambda: "local/gemma-4-12b")
    assert proxy_module._default_provider_id() == "local"


# ---- real requests ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_chat_request_reaches_the_local_model(running, client):
    r = await client.post("/v1/chat/completions", json=_chat(content="hello there"))
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "echo:hello there"
    assert body["model"].endswith("tiny-chat")


@pytest.mark.asyncio
async def test_streaming_comes_back_as_a_real_stream(running, client):
    async with client.stream("POST", "/v1/chat/completions", json=_chat(content="stream me", stream=True)) as r:
        assert r.status_code == 200
        text = "".join([line async for line in r.aiter_text()])
    pieces = [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ") and l != "data: [DONE]"]
    said = "".join((p["choices"][0].get("delta") or {}).get("content") or "" for p in pieces if p.get("choices"))
    assert said == "echo:stream me"
    assert text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_a_picture_from_vision_goes_straight_through(running, client):
    content = [{"type": "text", "text": "what is this?"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]
    r = await client.post("/v1/chat/completions", json=_chat(content=content))
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "I see a picture. echo:what is this?"


@pytest.mark.asyncio
async def test_reasoning_settings_llama_server_does_not_know_are_dropped(running, client):
    r = await client.post("/v1/chat/completions", json=_chat(
        content="params?", reasoning_effort="high", thinking={"type": "enabled"}, top_k=40, temperature=0.7))
    sent = r.json()["choices"][0]["message"]["content"].split(",")
    assert "top_k" in sent, "top_k is something llama-server understands, so it is kept"
    assert "reasoning_effort" not in sent and "thinking" not in sent


@pytest.mark.asyncio
async def test_a_request_while_the_model_is_still_loading_waits_for_it(local, client):
    local._command_builder = lambda exe, model, port, gpu: [sys.executable, FAKE_SERVER, "--port", str(port), "--mode", "load-delay:1.0"]
    s = await local.start("tiny-chat")
    assert s.state == "starting"
    r = await client.post("/v1/chat/completions", json=_chat(content="patience"))
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "echo:patience"


# ---- honest failures (real HTTP errors, so SkyrimNet falls back) ---------------------------------
@pytest.mark.asyncio
async def test_a_stopped_model_is_a_503_with_a_plain_reason(running, client):
    await running.stop("tiny-chat")
    r = await client.post("/v1/chat/completions", json=_chat())
    assert r.status_code == 503
    assert "stopped" in json.dumps(r.json())


@pytest.mark.asyncio
async def test_a_stopped_model_fails_a_stream_as_an_http_error_not_a_200(running, client):
    await running.stop("tiny-chat")
    r = await client.post("/v1/chat/completions", json=_chat(stream=True))
    assert r.status_code == 503, "a stream that can't start must be a real HTTP error"


@pytest.mark.asyncio
async def test_out_of_graphics_memory_is_a_503_naming_the_cause(local, client):
    local._command_builder = lambda exe, model, port, gpu: [sys.executable, FAKE_SERVER, "--port", str(port), "--mode", "oom-exit"]
    s = await local.start("tiny-chat")
    await s.ready.wait()
    r = await client.post("/v1/chat/completions", json=_chat())
    assert r.status_code == 503 and "graphics memory" in json.dumps(r.json())


@pytest.mark.asyncio
async def test_an_unknown_local_model_is_a_404(running, client):
    r = await client.post("/v1/chat/completions", json=_chat(model="local/nope"))
    assert r.status_code == 404 and "isn't one of your local models" in json.dumps(r.json())


@pytest.mark.asyncio
async def test_a_missing_model_file_is_reported_not_crashed(running, client, tmp_path):
    (tmp_path / "Tiny Chat.gguf").unlink()
    r = await client.post("/v1/chat/completions", json=_chat())
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_the_engine_dying_mid_game_is_a_503_then_a_clear_reason(running, client):
    s = running.servers["tiny-chat"]
    await running._kill(s)
    r = await client.post("/v1/chat/completions", json=_chat())
    assert r.status_code == 503 and "isn't running" in json.dumps(r.json())


def test_speeds_tab_labels_local_calls_as_local(proxy_module):
    assert proxy_module._PROVIDER_SHORT["local"] == "Local"


# ---- listing ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_v1_models_lists_local_models_by_their_local_name(running, client):
    ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
    assert "local/tiny-chat" in ids


@pytest.mark.asyncio
async def test_dashboard_has_a_local_provider_pill_and_the_card(running, client, proxy_module):
    providers = await proxy_module._dashboard_providers()
    entry = next(p for p in providers if p["id"] == "local")
    assert entry["state"] == "ok" and entry["testable"] is True and entry["keyless"] is True
    assert entry["models"] == [["local/tiny-chat", "Tiny Chat", "Sees pictures"]]
    html = (await client.get("/")).text
    assert 'id="localCard"' in html and "Local models" in html and "function lmRender" in html


@pytest.mark.asyncio
async def test_local_pill_is_grey_until_a_model_is_downloaded(proxy_module):
    entry = next(p for p in await proxy_module._dashboard_providers() if p["id"] == "local")
    assert entry["state"] == "off" and entry["testable"] is False and entry["models"] == []


# ---- the card's routes ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_state_route_describes_everything_the_card_draws(running, client):
    d = (await client.get("/config/local-models")).json()
    assert set(d) >= {"supported", "folder", "gpu", "engine", "catalog", "installed"}
    assert d["installed"][0]["modelName"] == "local/tiny-chat" and d["installed"][0]["state"] == "running"
    assert [m["id"] for m in d["catalog"]] == [m["id"] for m in lm.CATALOG]


@pytest.mark.asyncio
async def test_start_and_stop_routes(running, client):
    assert (await client.post("/config/local-models/stop", json={"id": "tiny-chat"})).json() == {"status": "stopped"}
    assert running.state_of("tiny-chat")[0] == "stopped"
    r = await client.post("/config/local-models/start", json={"id": "tiny-chat"})
    assert r.json()["status"] in ("starting", "running")
    await running.servers["tiny-chat"].ready.wait()
    assert running.state_of("tiny-chat")[0] == "running"


@pytest.mark.asyncio
async def test_folder_route_rejects_a_bad_path_with_a_plain_message(client):
    r = await client.post("/config/local-models/folder", json={"path": "relative/path"})
    assert r.status_code == 400 and "full folder path" in r.json()["detail"]


@pytest.mark.asyncio
async def test_pick_folder_route_asks_for_a_real_dialog_and_returns_the_choice(client):
    with patch.object(lm, "pick_folder_dialog", return_value="D:\\AI Models"):
        assert (await client.post("/config/local-models/pick-folder")).json() == {"path": "D:\\AI Models"}
    with patch.object(lm, "pick_folder_dialog", return_value=None):
        assert (await client.post("/config/local-models/pick-folder")).json() == {"path": None}


@pytest.mark.asyncio
async def test_downloading_without_a_folder_says_to_pick_one(client):
    r = await client.post("/config/local-models/model/download", json={"id": "gemma-4-12b"})
    assert r.status_code == 400 and "folder" in r.json()["detail"]


@pytest.mark.asyncio
async def test_add_and_remove_a_gguf_through_the_routes(local, client, tmp_path):
    f = tmp_path / "Other Model.gguf"
    f.write_bytes(b"x")
    r = await client.post("/config/local-models/add", json={"path": str(f)})
    assert r.json() == {"status": "added", "modelName": "local/other-model"}
    assert (await client.post("/config/local-models/add", json={"path": str(tmp_path / "x.txt")})).status_code == 400
    await client.post("/config/local-models/remove", json={"id": "other-model"})
    assert local.find("local/other-model") is None and f.exists()


@pytest.mark.asyncio
async def test_config_json_only_ever_holds_the_folder_engine_and_custom_models(local, proxy_module):
    cfg = proxy_module._load_config()["local_models"]
    assert set(cfg) == {"folder", "engine", "custom"}


# ---- startup and shutdown ---------------------------------------------------------------------------
class _Runner:
    async def cleanup(self):
        pass


@pytest.mark.asyncio
async def test_models_start_with_the_proxy_and_stop_when_it_closes(local, proxy_module, monkeypatch):
    async def runner():
        return _Runner()
    monkeypatch.setattr(proxy_module, "start_interceptor", runner)
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_module, "OPEN_BROWSER_ON_START", False)
    async with proxy_module.lifespan(proxy_module.app):
        s = local.servers.get("tiny-chat") or await local.start("tiny-chat")
        await s.ready.wait()
        assert s.state == "running"
        pid = s.proc.pid
    assert local.state_of("tiny-chat")[0] == "stopped"
    assert s.proc.returncode is not None, f"engine {pid} must not outlive the proxy"


@pytest.mark.asyncio
async def test_startup_kills_an_engine_left_behind_by_a_crashed_run(local, proxy_module, monkeypatch):
    killed = []
    monkeypatch.setattr(local, "cleanup_leftovers", lambda: killed.append(1) or 0)
    async def runner():
        return _Runner()
    monkeypatch.setattr(proxy_module, "start_interceptor", runner)
    monkeypatch.setattr(proxy_module.auth, "ensure_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_module, "OPEN_BROWSER_ON_START", False)
    async with proxy_module.lifespan(proxy_module.app):
        pass
    assert killed == [1]
