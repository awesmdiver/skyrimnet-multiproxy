"""Local models, part 2 (other_pcs.py): the pairing code, the private-address rule, the model-server
door, finding servers on the network, and the main device's list of other devices / AI servers.

Real sockets on this machine stand in for the home network (no second device needed): the door binds to
127.0.0.1 and a fake llama-server answers behind it.
"""
import asyncio
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

import local_models as lm
import other_pcs as op

FAKE_SERVER = str(Path(__file__).parent / "fixtures" / "fake_llama_server.py")
log = __import__("logging").getLogger("test-other-pcs")


from tests.fixtures.portpool import free_port  # noqa: E402,F401  (one shared allocator: see portpool.py)


# ------------------------------------------------------------------------------------------
# Pairing code
# ------------------------------------------------------------------------------------------
class TestPairingCode:
    def test_shape_is_three_dash_three_from_an_easy_to_read_alphabet(self):
        for _ in range(200):
            code = op.new_pairing_code()
            assert len(code) == 7 and code[3] == "-"
            assert set(code.replace("-", "")) <= set(op.CODE_ALPHABET)
        assert not set("01OI") & set(op.CODE_ALPHABET)

    def test_codes_are_not_all_the_same(self):
        assert len({op.new_pairing_code() for _ in range(50)}) > 40

    def test_matching_ignores_case_dashes_and_spaces(self):
        assert op.codes_match("kx7-42m", "KX7-42M")
        assert op.codes_match(" KX7 42M ", "KX7-42M")
        assert op.codes_match("KX742M", "KX7-42M")

    def test_wrong_empty_or_missing_codes_never_match(self):
        assert not op.codes_match("KX7-42N", "KX7-42M")
        assert not op.codes_match("", "KX7-42M")
        assert not op.codes_match("---", "---")
        assert not op.codes_match("KX7-42M", "")


# ------------------------------------------------------------------------------------------
# Which addresses are allowed
# ------------------------------------------------------------------------------------------
class TestAddressRules:
    @pytest.mark.parametrize("ip", ["192.168.1.50", "192.168.0.1", "10.0.0.5", "10.255.255.254", "172.16.0.1", "172.31.255.1", "fd12:3456::1"])
    def test_home_network_addresses_are_private(self, ip):
        assert op.ip_class(ip) == "private"

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "172.15.0.1", "172.32.0.1", "192.169.1.1", "11.0.0.1", "2001:4860:4860::8888", "169.254.1.1", "garbage", ""])
    def test_everything_else_is_not(self, ip):
        assert op.ip_class(ip) == "other"

    @pytest.mark.parametrize("ip", ["100.64.0.0", "100.64.0.1", "100.100.100.100", "100.127.255.254", "100.127.255.255", "fd7a:115c:a1e0::1"])
    def test_a_tailscale_style_private_link_is_private(self, ip):
        assert op.ip_class(ip) == "private"

    @pytest.mark.parametrize("ip", ["100.63.255.255", "100.128.0.0", "100.0.0.1", "101.64.0.1", "99.64.0.1", "::ffff:100.128.0.1"])
    def test_the_edges_of_that_range_stay_public(self, ip):
        assert op.ip_class(ip) == "other"

    def test_a_tailscale_address_is_allowed_by_hand_and_a_neighbouring_public_one_is_not(self):
        op.check_host_allowed("100.100.1.2", allow_loopback=False)
        for bad in ("100.128.0.1", "100.63.0.1", "8.8.8.8"):
            with pytest.raises(op.OtherPcsError) as e:
                op.check_host_allowed(bad, allow_loopback=False)
            assert e.value.message == op.PRIVATE_NETWORK_MESSAGE

    def test_the_refusal_says_it_plainly_for_a_tailscale_user_too(self):
        assert op.PRIVATE_NETWORK_MESSAGE == "That address isn't on a private network or Tailscale link. Check the address and try again."

    def test_the_door_listens_on_a_tailscale_address_only_when_the_device_has_one(self, monkeypatch):
        class NoUdp:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def connect(self, *a): raise OSError("no route")
        monkeypatch.setattr(op.socket, "socket", NoUdp)
        monkeypatch.setattr(op.sys, "platform", "win32")             # (no `ip addr` call: the names the device has are all there is)
        has = [(2, 1, 6, "", ("192.168.1.5", 0)), (2, 1, 6, "", ("100.101.102.103", 0)), (2, 1, 6, "", ("8.8.8.8", 0)), (2, 1, 6, "", ("100.128.0.9", 0))]
        monkeypatch.setattr(op.socket, "getaddrinfo", lambda *a, **k: has)
        ts = lambda: [("Tailscale", "100.101.102.103")]
        assert op.local_private_ips(ts) == ["100.101.102.103", "192.168.1.5"]
        monkeypatch.setattr(op.socket, "getaddrinfo", lambda *a, **k: has[:1] + has[2:])
        assert op.local_private_ips(lambda: []) == ["192.168.1.5"], "no Tailscale link on this device: nothing is added, and public addresses never are"

    def test_loopback_is_its_own_kind(self):
        assert op.ip_class("127.0.0.1") == "loopback" and op.ip_class("::1") == "loopback"

    def test_an_ipv4_mapped_ipv6_address_follows_its_ipv4(self):
        assert op.ip_class("::ffff:192.168.1.5") == "private"
        assert op.ip_class("::ffff:8.8.8.8") == "other"

    def test_check_allows_private_and_refuses_public(self):
        op.check_host_allowed("192.168.1.50", allow_loopback=False)
        with pytest.raises(op.OtherPcsError) as e:
            op.check_host_allowed("8.8.8.8", allow_loopback=False)
        assert e.value.message == op.PRIVATE_NETWORK_MESSAGE

    def test_this_pc_is_only_allowed_for_your_own_ai_server(self):
        op.check_host_allowed("127.0.0.1", allow_loopback=True)
        with pytest.raises(op.OtherPcsError) as e:
            op.check_host_allowed("127.0.0.1", allow_loopback=False)
        assert "Add it on the Local device tab instead" in e.value.message

    def test_a_name_must_resolve_ONLY_to_allowed_addresses(self, monkeypatch):
        monkeypatch.setattr(op.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("192.168.1.9", 0)), (0, 0, 0, "", ("8.8.8.8", 0))])
        with pytest.raises(op.OtherPcsError):
            op.check_host_allowed("sneaky.example", allow_loopback=False)
        monkeypatch.setattr(op.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("192.168.1.9", 0))])
        op.check_host_allowed("den-pc", allow_loopback=False)

    def test_an_unknown_name_is_a_plain_message(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no such host")
        monkeypatch.setattr(op.socket, "getaddrinfo", boom)
        with pytest.raises(op.OtherPcsError) as e:
            op.check_host_allowed("nope", allow_loopback=False)
        assert "Couldn't find nope." in e.value.message


class TestParseAddress:
    def test_bare_host_gets_the_default_port(self):
        a = op.parse_address("192.168.1.50", 8100)
        assert (a.scheme, a.host, a.port, a.base) == ("http", "192.168.1.50", 8100, "http://192.168.1.50:8100")

    def test_host_and_port(self):
        assert op.parse_address("den-pc:9000", 8100).base == "http://den-pc:9000"

    def test_a_full_url_keeps_its_path_without_a_trailing_slash(self):
        a = op.parse_address("http://192.168.1.60:8080/v1/", None)
        assert a.base == "http://192.168.1.60:8080/v1" and a.port == 8080

    def test_https_is_kept(self):
        assert op.parse_address("https://192.168.1.60:8443", None).scheme == "https"

    def test_a_port_is_required_when_there_is_no_default(self):
        with pytest.raises(op.OtherPcsError) as e:
            op.parse_address("192.168.1.60", None)
        assert "Add the exact address" in e.value.message

    @pytest.mark.parametrize("bad", ["", "   ", "ftp://x:1", "http://", "http://host:notaport"])
    def test_garbage_is_a_plain_message(self, bad):
        with pytest.raises(op.OtherPcsError):
            op.parse_address(bad, 8100)


def test_this_pcs_own_addresses_are_only_home_network_ones():
    ips = op.local_private_ips()
    assert all(op.ip_class(ip) == "private" for ip in ips)


# ------------------------------------------------------------------------------------------
# Fixtures: a fake downloaded model behind a real door
# ------------------------------------------------------------------------------------------
@pytest.fixture
def store():
    return {}


def make_local(tmp_path, store, mode="ok"):
    svc = lm.LocalModels(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log,
                         str(tmp_path / "pids.json"), command_builder=lambda exe, m, port, gpu: [sys.executable, FAKE_SERVER, "--port", str(port), "--mode", mode],
                         gpu_detector=lambda: lm.GpuInfo("Test GPU", 24576, "nvidia", cuda=(13, 4)))
    folder = svc.set_folder(str(tmp_path / "models"))
    eng = folder / "engine" / f"{lm.LLAMA_CPP_BUILD}-cpu"
    eng.mkdir(parents=True)
    (eng / ("llama-server.exe" if sys.platform == "win32" else "llama-server")).write_bytes(b"fake")
    svc._update(engine={"kind": "cpu", "build": lm.LLAMA_CPP_BUILD})
    gguf, mm = tmp_path / "Den Model.gguf", tmp_path / "mmproj-den.gguf"
    gguf.write_bytes(b"x"); mm.write_bytes(b"y")
    svc.add_custom(str(gguf), str(mm))
    return svc


@pytest_asyncio.fixture
async def door(tmp_path, monkeypatch):
    """The model-server door on 127.0.0.1 with one running fake model, sharing ON."""
    monkeypatch.setattr(lm, "HEALTH_POLL_SECONDS", 0.05)
    monkeypatch.setattr(op, "local_private_ips", lambda: ["127.0.0.1"])
    store = {"share": {"port": free_port()}}
    local = make_local(tmp_path, store)
    mgr = op.ShareManager(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log, local,
                          lambda: local._gpu, version="9.9", discovery_port=free_port())
    local._gpu = lm.GpuInfo("Test GPU", 24576, "nvidia")
    s = await local.start("den-model")
    await s.ready.wait()
    await mgr.set_enabled(True)
    mgr.local_svc = local
    yield mgr
    await mgr.stop()
    await local.stop_all()


def url(mgr, path):
    return f"http://127.0.0.1:{mgr.port}{path}"


async def get(mgr, path, code=None, **kw):
    async with aiohttp.ClientSession() as s:
        async with s.get(url(mgr, path), headers={op.CODE_HEADER: code} if code else {}, **kw) as r:
            return r.status, await r.json(content_type=None)


async def post(mgr, body, code=None):
    async with aiohttp.ClientSession() as s:
        async with s.post(url(mgr, "/share/v1/chat/completions"), json=body, headers={op.CODE_HEADER: code} if code else {}) as r:
            return r.status, await r.read(), r.headers.get("Content-Type", "")


# ------------------------------------------------------------------------------------------
# The door
# ------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestDoor:
    async def test_it_is_off_until_turned_on(self, tmp_path, monkeypatch):
        store = {}
        local = make_local(tmp_path, store)
        mgr = op.ShareManager(lambda: dict(store), lambda d: (store.clear(), store.update(d)), log, local, lambda: None, discovery_port=free_port())
        await mgr.apply()
        assert not mgr.running and not mgr.enabled and mgr.state()["code"] == ""

    async def test_turning_it_on_makes_a_code_and_opens_the_door_only_on_home_addresses(self, door):
        st = door.state()
        assert door.running and st["enabled"] and len(st["code"]) == 7
        assert st["addresses"] == [f"127.0.0.1:{door.port}"]

    async def test_info_needs_the_code(self, door):
        assert (await get(door, "/share/info"))[0] == 403
        assert (await get(door, "/share/info", code="AAA-AAA"))[0] == 403
        status, data = await get(door, "/share/info", code=door.code)
        assert status == 200 and data["product"] == "multiproxy" and data["version"] == "9.9"
        assert data["gpu"]["name"] == "Test GPU"
        assert [m["id"] for m in data["models"]] == ["den-model"] and data["models"][0]["state"] == "running" and data["models"][0]["vision"] is True

    async def test_the_code_works_in_any_case_with_or_without_the_dash(self, door):
        assert (await get(door, "/share/info", code=door.code.lower()))[0] == 200
        assert (await get(door, "/share/info", code=door.code.replace("-", "")))[0] == 200

    async def test_a_chat_request_runs_on_this_pcs_model(self, door):
        status, body, _ = await post(door, {"model": "den-model", "messages": [{"role": "user", "content": "hello"}]}, door.code)
        assert status == 200 and json.loads(body)["choices"][0]["message"]["content"] == "echo:hello"

    async def test_the_local_prefix_is_accepted_too(self, door):
        status, body, _ = await post(door, {"model": "local/den-model", "messages": [{"role": "user", "content": "hi"}]}, door.code)
        assert status == 200

    async def test_streaming_comes_through_as_a_stream(self, door):
        status, body, ctype = await post(door, {"model": "den-model", "stream": True, "messages": [{"role": "user", "content": "stream me"}]}, door.code)
        assert status == 200 and "event-stream" in ctype
        text = body.decode()
        pieces = [json.loads(l[6:]) for l in text.splitlines() if l.startswith("data: ") and l != "data: [DONE]"]
        assert "".join((p["choices"][0].get("delta") or {}).get("content") or "" for p in pieces) == "echo:stream me"
        assert text.rstrip().endswith("data: [DONE]")

    async def test_pictures_go_straight_through(self, door):
        content = [{"type": "text", "text": "what?"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        status, body, _ = await post(door, {"model": "den-model", "messages": [{"role": "user", "content": content}]}, door.code)
        assert json.loads(body)["choices"][0]["message"]["content"] == "I see a picture. echo:what?"

    async def test_chat_without_the_code_never_reaches_the_model(self, door):
        status, body, _ = await post(door, {"model": "den-model", "messages": [{"role": "user", "content": "hi"}]})
        assert status == 403 and "pairing code" in json.loads(body)["error"]["message"]
        assert door.last_chat == 0

    async def test_only_this_pcs_local_models_are_reachable(self, door):
        status, body, _ = await post(door, {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}, door.code)
        assert status == 404 and "doesn't have a model called" in json.loads(body)["error"]["message"]

    async def test_a_stopped_model_is_a_plain_503(self, door):
        await door.local.stop("den-model")
        status, body, _ = await post(door, {"model": "den-model", "messages": [{"role": "user", "content": "hi"}]}, door.code)
        assert status == 503 and "stopped" in json.loads(body)["error"]["message"]

    async def test_reasoning_knobs_are_dropped(self, door):
        status, body, _ = await post(door, {"model": "den-model", "reasoning_effort": "high", "thinking": {"type": "enabled"}, "top_k": 5,
                                            "messages": [{"role": "user", "content": "params?"}]}, door.code)
        sent = json.loads(body)["choices"][0]["message"]["content"].split(",")
        assert "top_k" in sent and "thinking" not in sent and "reasoning_effort" not in sent

    async def test_ten_wrong_codes_lock_that_address_out_for_a_minute(self, door):
        for _ in range(op.MAX_BAD_CODES):
            assert (await get(door, "/share/info", code="WRONG1"))[0] == 403
        assert (await get(door, "/share/info", code="WRONG1"))[0] == 429
        assert (await get(door, "/share/info", code=door.code))[0] == 429, "even the right code waits out the lockout"

    async def test_a_new_code_stops_the_old_one_working(self, door):
        old = door.code
        new = door.new_code()
        assert new != old
        assert (await get(door, "/share/info", code=old))[0] == 403
        assert (await get(door, "/share/info", code=new))[0] == 200

    async def test_a_request_from_a_public_address_is_refused_even_with_the_right_code(self, door):
        fake = SimpleNamespace(remote="8.8.8.8", headers={op.CODE_HEADER: door.code})
        denied = door._check(fake)
        assert denied.status == 403

    async def test_status_shows_connected_after_a_request_and_waiting_before(self, door):
        assert door.state()["connected"] is False
        await get(door, "/share/info", code=door.code)
        st = door.state()
        assert st["connected"] is True and st["connectedFrom"] == "127.0.0.1"

    async def test_turning_it_off_closes_the_door(self, door):
        await door.set_enabled(False)
        assert not door.running and door.state()["code"] == ""
        with pytest.raises(aiohttp.ClientConnectorError):
            await get(door, "/share/info", code=door.code)

    async def test_the_dashboard_is_not_on_the_door(self, door):
        for path in ("/", "/config/other-pcs", "/config/local-models", "/health", "/v1/models"):
            async with aiohttp.ClientSession() as s:
                async with s.get(url(door, path), headers={op.CODE_HEADER: door.code}) as r:
                    assert r.status == 404, path

    async def test_no_home_network_address_is_a_plain_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(op, "local_private_ips", lambda: [])
        store = {}
        local = make_local(tmp_path, store)
        mgr = op.ShareManager(lambda: dict(store), lambda d: (store.clear(), store.update(d)), log, local, lambda: None, discovery_port=free_port())
        await mgr.set_enabled(True)
        assert not mgr.running and "home-network address" in mgr.error

    async def test_a_busy_port_is_a_plain_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(op, "local_private_ips", lambda: ["127.0.0.1"])
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        try:
            store = {"share": {"port": blocker.getsockname()[1]}}
            local = make_local(tmp_path, store)
            mgr = op.ShareManager(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log, local, lambda: None, discovery_port=free_port())
            await mgr.set_enabled(True)
            assert not mgr.running and "Couldn't open the sharing port" in mgr.error
        finally:
            blocker.close()


# ------------------------------------------------------------------------------------------
# Finding servers on the network
# ------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestDiscovery:
    async def test_a_scan_finds_a_sharing_pc_and_nothing_secret(self, door):
        found = await asyncio.to_thread(op.scan_network, 1.0, door.discovery_port, "someone-else", ["127.0.0.1"])
        assert len(found) == 1
        f = found[0]
        assert f["host"] == "127.0.0.1" and f["address"] == f"http://127.0.0.1:{door.port}"
        assert f["gpu"]["name"] == "Test GPU" and f["models"] == ["Den Model"]
        assert door.code not in json.dumps(f), "the pairing code is never broadcast"

    async def test_a_scan_skips_itself(self, door):
        found = await asyncio.to_thread(op.scan_network, 1.0, door.discovery_port, door.instance, ["127.0.0.1"])
        assert found == []

    async def test_a_scan_with_nobody_there_finds_nothing(self):
        assert await asyncio.to_thread(op.scan_network, 0.4, free_port(), "", ["127.0.0.1"]) == []

    async def test_nothing_answers_when_sharing_is_off(self, door):
        await door.set_enabled(False)
        assert await asyncio.to_thread(op.scan_network, 0.5, door.discovery_port, "x", ["127.0.0.1"]) == []


# ------------------------------------------------------------------------------------------
# The main device's list
# ------------------------------------------------------------------------------------------
def make_remotes(store, reserved=None):
    return op.RemoteServers(lambda: json.loads(json.dumps(store)), lambda d: (store.clear(), store.update(d)), log,
                            reserved or (lambda n: None), lambda: {8000, 9999})


@pytest.fixture
def allow_any_host(monkeypatch):
    """The door here is on 127.0.0.1; let a 'paired MultiProxy' live there for these tests."""
    monkeypatch.setattr(op, "check_host_allowed", lambda host, allow_loopback=False: None)


@pytest_asyncio.fixture
async def own_server():
    """A pretend 'AI server somebody already runs' (llama.cpp / LM Studio shape) on a local port."""
    seen = {"auth": [], "bodies": []}

    async def models(request):
        seen["auth"].append(request.headers.get("Authorization"))
        return web.json_response({"object": "list", "data": [{"id": "qwen-32b"}, {"id": "gemma-3-12b"}]})

    async def chat(request):
        body = await request.json()
        seen["bodies"].append(body)
        seen["auth"].append(request.headers.get("Authorization"))
        text = body["messages"][-1]["content"]
        if isinstance(text, list):
            if seen.get("vision_off"):
                return web.json_response({"error": {"message": "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj"}}, status=500)
            text = "pic"
        if body.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(('data: ' + json.dumps({"choices": [{"index": 0, "delta": {"content": "own:" + text}, "finish_reason": None}]}) + "\n\n").encode())
            await resp.write(b"data: [DONE]\n\n")
            return resp
        return web.json_response({"choices": [{"index": 0, "message": {"role": "assistant", "content": "own:" + text}, "finish_reason": "stop"}], "usage": {}})

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    seen["port"] = port
    seen["runner"] = runner
    yield seen
    await runner.cleanup()


@pytest.mark.asyncio
class TestPairedMultiproxy:
    async def test_pairing_lists_its_models_and_names_it_after_the_pc(self, door, allow_any_host):
        store = {}
        remotes = make_remotes(store)
        r = await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "")
        assert r.kind == "multiproxy" and r.online is True and r.id == slugify_host()
        assert [m["id"] for m in r.models] == ["den-model"] and r.gpu["name"] == "Test GPU"
        assert store["remote_servers"][0]["code"] == door.code
        assert remotes.online_model_names() == [(f"{r.id}/den-model", "Den Model", True)]

    async def test_a_nickname_can_be_chosen(self, door, allow_any_host):
        remotes = make_remotes({})
        r = await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "Den PC!")
        assert r.id == "den-pc" and remotes.match("den-pc/den-model")[1] == "den-model"

    async def test_a_wrong_code_is_a_plain_message_and_nothing_is_saved(self, door, allow_any_host):
        store = {}
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes(store).add_multiproxy(f"127.0.0.1:{door.port}", "NOT-RIGHT", "")
        assert e.value.status == 403 and "code doesn't match" in e.value.message
        assert "remote_servers" not in store

    async def test_a_missing_code_is_asked_for(self, door, allow_any_host):
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_multiproxy(f"127.0.0.1:{door.port}", "  ", "")
        assert "pairing code" in e.value.message

    async def test_a_pc_that_isnt_there_is_a_plain_message(self, allow_any_host):
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_multiproxy(f"127.0.0.1:{free_port()}", "ABC-DEF", "")
        assert e.value.status == 502 and "Is MultiProxy running there with sharing turned on" in e.value.message and "firewall" in e.value.message

    async def test_something_that_isnt_a_multiproxy_is_refused(self, own_server, allow_any_host):
        with pytest.raises(op.OtherPcsError):
            await make_remotes({}).add_multiproxy(f"127.0.0.1:{own_server['port']}", "ABC-DEF", "")

    async def test_public_and_this_pc_addresses_are_refused_for_a_paired_multiproxy(self):
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_multiproxy("8.8.8.8", "ABC-DEF", "")
        assert e.value.message == op.PRIVATE_NETWORK_MESSAGE
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_multiproxy("127.0.0.1", "ABC-DEF", "")
        assert "Add it on the Local device tab instead" in e.value.message

    async def test_nicknames_must_be_unique_and_not_a_built_in_prefix(self, door, allow_any_host):
        remotes = make_remotes({}, reserved=lambda n: "taken by built-in" if n in ("ollama", "local") else None)
        await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        for nick in ("den-pc", "Den PC"):
            with pytest.raises(op.OtherPcsError) as e:
                await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, nick)
            assert "already have a device with that nickname" in e.value.message
        with pytest.raises(op.OtherPcsError) as e:
            await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "ollama")
        assert e.value.message == "taken by built-in"

    async def test_it_goes_offline_and_comes_back(self, door, allow_any_host):
        remotes = make_remotes({})
        r = await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        await door.set_enabled(False)
        await remotes.poll_all()
        assert r.online is False and "Couldn't reach that device" in r.error and remotes.online_model_names() == []
        await door.set_enabled(True)
        await remotes.poll_all()
        assert r.online is True and r.error == ""

    async def test_a_changed_code_shows_as_offline_with_the_reason(self, door, allow_any_host):
        remotes = make_remotes({})
        r = await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        door.new_code()
        await remotes.poll_all()
        assert r.online is False and "code doesn't match" in r.error

    async def test_saved_servers_survive_a_restart_and_removal_forgets_them(self, door, allow_any_host):
        store = {}
        await make_remotes(store).add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        again = make_remotes(store)
        assert [r.id for r in again.all()] == ["den-pc"] and again.all()[0].online is None
        again.remove("den-pc")
        assert make_remotes(store).all() == [] and store["remote_servers"] == []

    async def test_match_only_knows_saved_nicknames(self, door, allow_any_host):
        remotes = make_remotes({})
        await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "den-pc")
        assert remotes.match("DEN-PC/gemma")[1] == "gemma"
        assert remotes.match("other/gemma") is None and remotes.match("den-pc") is None and remotes.match("den-pc/") is None


@pytest.mark.asyncio
class TestOwnAiServer:
    async def test_reads_the_model_list_from_v1_models(self, own_server):
        r = await make_remotes({}).add_openai(f"http://127.0.0.1:{own_server['port']}", "den-llama")
        assert r.kind == "openai" and r.online and [m["id"] for m in r.models] == ["qwen-32b", "gemma-3-12b"]
        assert r.api_base.endswith("/v1")

    async def test_a_url_that_already_ends_in_v1_is_fine(self, own_server):
        r = await make_remotes({}).add_openai(f"http://127.0.0.1:{own_server['port']}/v1", "x")
        assert r.api_base == f"http://127.0.0.1:{own_server['port']}/v1" and r.online

    async def test_a_key_is_sent_as_a_bearer_token(self, own_server):
        await make_remotes({}).add_openai(f"127.0.0.1:{own_server['port']}", "litellm", key="sk-test")
        assert "Bearer sk-test" in own_server["auth"]

    async def test_this_pcs_own_ports_are_refused_with_a_plain_explanation(self):
        for port in (8000, 9999):
            with pytest.raises(op.OtherPcsError) as e:
                await make_remotes({}).add_openai(f"http://localhost:{port}", "nim")
            assert "MultiProxy is already using that address" in e.value.message

    async def test_the_local_models_engine_ports_are_refused_too(self, monkeypatch):
        remotes = op.RemoteServers(lambda: {}, lambda d: None, log, lambda n: None, lambda: set(range(lm.SERVER_PORT_START, lm.SERVER_PORT_END)))
        with pytest.raises(op.OtherPcsError):
            await remotes.add_openai("http://127.0.0.1:18080", "x")

    async def test_a_port_is_required(self):
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_openai("192.168.1.60", "x")
        assert "Add the exact address" in e.value.message

    async def test_public_addresses_are_refused(self):
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_openai("http://8.8.8.8:8080", "x")
        assert e.value.message == op.PRIVATE_NETWORK_MESSAGE

    async def test_a_server_that_wants_a_key_says_so(self):
        async def deny(request):
            return web.Response(status=401)
        app = web.Application()
        app.router.add_get("/v1/models", deny)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            with pytest.raises(op.OtherPcsError) as e:
                await make_remotes({}).add_openai(f"127.0.0.1:{port}", "needs-key")
            assert "wants a key" in e.value.message
        finally:
            await runner.cleanup()

    async def test_nothing_listening_is_a_plain_message(self):
        with pytest.raises(op.OtherPcsError) as e:
            await make_remotes({}).add_openai(f"127.0.0.1:{free_port()}", "x")
        assert "Is it running, and is the address right" in e.value.message

    async def test_the_nickname_defaults_from_the_address(self, own_server):
        r = await make_remotes({}).add_openai(f"127.0.0.1:{own_server['port']}", "")
        assert r.id == "127-0-0-1"


def slugify_host():
    return lm.slugify(op.short_hostname())


def test_image_hint_names_the_fix_for_llama_cpps_picture_error():
    msg = "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj"
    assert op.image_hint(500, msg) == ("That provider can't read pictures. Check its settings and try again.")
    assert op.image_hint(500, "something else") == "something else"


# What each popular AI server really returns from GET /v1/models (shapes copied from real responses:
# llama.cpp and Ollama were checked live on the dev device, 2026-09-30; the others from their documentation).
SERVER_SHAPES = {
    "llama.cpp (llama-server)": {"models": [{"name": "gemma-3-270m"}], "object": "list", "data": [{"id": "gemma-3-270m", "aliases": ["gemma-3-270m"], "object": "model", "owned_by": "llamacpp", "meta": {"n_ctx": 16384}}]},
    "Ollama": {"object": "list", "data": [{"id": "gemma4:latest", "object": "model", "created": 1, "owned_by": "library"}, {"id": "gemma4:26b", "object": "model", "created": 1, "owned_by": "library"}]},
    "LM Studio": {"data": [{"id": "google/gemma-3-12b", "object": "model", "owned_by": "organization_owner"}], "object": "list"},
    "LiteLLM": {"data": [{"id": "gpt-4o", "object": "model", "created": 1677610602, "owned_by": "openai"}, {"id": "my-local", "object": "model", "created": 1677610602, "owned_by": "openai"}], "object": "list"},
    "NVIDIA NIM": {"object": "list", "data": [{"id": "meta/llama-3.1-8b-instruct", "object": "model", "created": 735790403, "owned_by": "system", "root": "meta/llama-3.1-8b-instruct", "parent": None, "permission": [{"id": "modelperm-x"}]}]},
    "vLLM": {"object": "list", "data": [{"id": "Qwen/Qwen3-8B", "object": "model", "created": 1, "owned_by": "vllm", "root": "Qwen/Qwen3-8B", "parent": None, "max_model_len": 32768, "permission": []}]},
    "koboldcpp (read live 2026-10-25, v1.123.1)": {"object": "list", "data": [{"id": "koboldcpp/Qwen3-1.7B-Q8_0", "object": "model", "created": 1792957291, "owned_by": "koboldcpp", "status": {"value": "loaded"}, "permission": [], "root": "koboldcpp"}]},
    "Unsloth Studio (shape from its API docs, 2026-10-25: no object field, a loaded flag)": {"data": [{"id": "gemma-4-26B-A4B-it-GGUF", "loaded": True}, {"id": "Qwen3-8B-GGUF", "loaded": False}]},
    "an older server that returns a bare list": [{"id": "bare-model"}],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("server", list(SERVER_SHAPES))
async def test_the_model_list_of_every_popular_ai_server_is_understood(server):
    payload = SERVER_SHAPES[server]

    async def models(request):
        return web.json_response(payload)
    app = web.Application()
    app.router.add_get("/v1/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        r = await make_remotes({}).add_openai(f"127.0.0.1:{port}", "x")
    finally:
        await runner.cleanup()
    expected = [m["id"] for m in (payload["data"] if isinstance(payload, dict) else payload)]
    assert [m["id"] for m in r.models] == expected and r.online


@pytest.mark.asyncio
async def test_litellm_with_a_key_and_a_custom_path_prefix(own_server):
    """LiteLLM is usually on :4000 with a master key, sometimes behind a path like /litellm/v1."""
    seen = []

    async def models(request):
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"object": "list", "data": [{"id": "gpt-4o"}]})
    app = web.Application()
    app.router.add_get("/litellm/v1/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        r = await make_remotes({}).add_openai(f"http://127.0.0.1:{port}/litellm/v1", "lite", key="sk-1234")
    finally:
        await runner.cleanup()
    assert r.online and seen == ["Bearer sk-1234"] and r.api_base == f"http://127.0.0.1:{port}/litellm/v1"


# ---- steadiness under a full run (2026-10-01) ------------------------------------------------------------------------
def test_a_scan_asks_again_when_the_first_datagram_is_lost():
    """One datagram can be lost (Wi-Fi, a busy device). This 'Courier' ignores the first ask and answers the second."""
    import json as _json
    import threading
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    asked = []

    def serve():
        sock.settimeout(5)
        try:
            while len(asked) < 2:
                data, addr = sock.recvfrom(1024)
                asked.append(data)
                if len(asked) == 2:
                    sock.sendto(_json.dumps({"magic": op.DISCOVERY_ANSWER, "name": "den", "instance": "abc", "port": 8110, "models": []}).encode(), addr)
        except OSError:
            pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    found = op.scan_network(3.0, port, "", ["127.0.0.1"])
    t.join(5)
    sock.close()
    assert len(asked) == 2 and [f["instance"] for f in found] == ["abc"]


def test_the_test_port_pool_gives_ports_free_for_tcp_and_udp_and_not_the_same_twice():
    seen = [free_port() for _ in range(40)]
    assert len(set(seen)) == 40
    for p in seen[:5]:
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            s = socket.socket(socket.AF_INET, kind)
            s.bind(("127.0.0.1", p))
            s.close()
    assert not any(8000 == p or 9999 == p or 59595 == p or 18000 <= p < 18200 for p in seen)


def test_engine_ports_start_at_a_random_place_so_two_programs_looking_at_once_dont_collide():
    import local_models as lm_
    firsts = {lm_._free_port() for _ in range(40)}
    assert len(firsts) > 5 and all(lm_.SERVER_PORT_START <= p < lm_.SERVER_PORT_END for p in firsts)
