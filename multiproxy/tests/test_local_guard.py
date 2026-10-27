"""Other websites are kept out of MultiProxy's local servers (security review before 4.0, 2026-10-06).

Stand-ins only: the in-memory client, plus one real server on 127.0.0.1 and a spare port.
"""
import asyncio
import http.client
import threading
import time

import pytest
from fastapi.testclient import TestClient

import local_guard
from fixtures.portpool import free_port

OWN = "http://127.0.0.1:8000"
JSON = {"X-MultiProxy": "1", "Content-Type": "application/json"}


@pytest.fixture()
def guarded(proxy_module):
    local_guard.ENABLED = True
    return TestClient(proxy_module.app, base_url=OWN)


def test_no_cors_header_is_ever_sent(guarded):
    r = guarded.get("/health", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "access-control-allow-origin" not in r.headers
    pre = guarded.options("/config/settings", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "x-multiproxy,content-type"})
    assert pre.status_code == 403 and "access-control-allow-origin" not in pre.headers
    ok = guarded.get("/health")
    assert ok.status_code == 200 and "access-control-allow-origin" not in ok.headers


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example:8000", "127.0.0.1:9", "10.0.0.5:8000", ""])
def test_a_foreign_host_is_refused(guarded, host):
    assert guarded.get("/health", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST:8000", "[::1]:8000"])
def test_the_real_hosts_are_let_in(guarded, host):
    assert guarded.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "http://evil.example:8000", "http://127.0.0.1:8001", "null", "http://localhost:3000"])
def test_a_foreign_origin_is_refused_on_every_kind_of_request(guarded, origin):
    assert guarded.get("/live/stream", headers={"Origin": origin}).status_code == 403
    assert guarded.post("/config/help-seen", headers={**JSON, "Origin": origin}).status_code == 403
    assert guarded.post("/v1/chat/completions", json={}, headers={"Origin": origin}).status_code == 403


def test_the_dashboards_own_origin_works(guarded):
    assert guarded.post("/config/help-seen", headers={**JSON, "Origin": OWN}).status_code == 200
    assert guarded.get("/health", headers={"Origin": "http://localhost:8000", "Host": "localhost:8000"}).status_code == 200


def test_cross_site_fetch_metadata_is_refused(guarded):
    assert guarded.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert guarded.get("/health", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_dashboard_change_needs_the_header_and_json(guarded):
    assert guarded.post("/config/help-seen", headers={"Origin": OWN}).status_code == 403            # no X-MultiProxy
    assert guarded.post("/config/help-seen", headers={"X-MultiProxy": "0"}).status_code == 403
    assert guarded.post("/config/help-seen").status_code == 403                                      # a program without the header is not the page either
    bad = guarded.post("/config/glm-key", content='{"key":"x"}', headers={"X-MultiProxy": "1", "Content-Type": "text/plain"})
    assert bad.status_code == 415
    assert guarded.post("/config/help-seen", headers={"X-MultiProxy": "1"}).status_code == 200      # no body: the header alone is enough
    assert guarded.post("/start-here/hide", json={}, headers={"Content-Type": "text/plain", "X-MultiProxy": "1"}).status_code in (415, 200, 422)


def test_the_form_post_a_web_page_can_make_is_refused(guarded):
    """A plain HTML form: text/plain body, no custom header, but the browser always adds Origin."""
    r = guarded.post("/config/glm-key", content='{"key":"stolen"}', headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_skyrimnets_own_calls_still_work(guarded, proxy_module):
    """A program sends no Origin, Referer or Sec-Fetch-*: the OpenAI-style routes keep answering it (only the Host is checked)."""
    assert guarded.get("/v1/models").status_code == 200
    assert guarded.get("/health").status_code == 200
    r = guarded.post("/v1/chat/completions", json={"model": "x", "messages": []})
    assert r.status_code != 403
    r = guarded.post("/jev/whatever", json={"questions": ["a"]})
    assert r.status_code != 403


def test_the_tray_channel_is_not_blocked_but_still_needs_its_token(guarded):
    assert guarded.get("/local/ping").status_code in (401, 404)


def test_a_refusal_is_logged_in_one_line_without_the_body(proxy_module, caplog):
    local_guard.ENABLED = True
    c = TestClient(proxy_module.app, base_url=OWN)
    with caplog.at_level("WARNING"):
        c.post("/config/glm-key", content='{"key":"sk-secret-secret-secret"}', headers={"Origin": "https://evil.example", "Content-Type": "text/plain"})
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Refused")]
    assert lines == ["Refused POST /config/glm-key: foreign Origin"]
    assert "sk-secret" not in caplog.text


def test_the_page_adds_the_header_to_its_own_requests(proxy_module):
    html = TestClient(proxy_module.app).get("/").text
    assert "'X-MultiProxy', '1'" in html and "url.origin === location.origin" in html


def test_a_real_server_refuses_another_site_and_a_rebinding_name(proxy_module):
    """The real thing on a spare 127.0.0.1 port: raw sockets, so Host and Origin are exactly what we send."""
    import uvicorn
    local_guard.ENABLED = True
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(proxy_module.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started

        def ask(method, path, headers, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in headers.items():
                c.putheader(k, v)
            c.endheaders(body)
            r = c.getresponse()
            r.read()
            out = (r.status, r.getheader("Access-Control-Allow-Origin"))
            c.close()
            return out

        mine = {"Host": f"127.0.0.1:{port}"}
        assert ask("GET", "/health", mine) == (200, None)
        assert ask("GET", "/health", {"Host": f"rebind.example:{port}"})[0] == 403
        assert ask("GET", "/health", {**mine, "Origin": "https://evil.example"})[0] == 403
        form = {**mine, "Origin": "https://evil.example", "Content-Type": "text/plain", "Content-Length": "5"}
        assert ask("POST", "/config/glm-key", form, b'{"a"}')[0] == 403
        ours = {**mine, "Origin": f"http://127.0.0.1:{port}", "X-MultiProxy": "1"}
        assert ask("POST", "/config/help-seen", ours)[0] == 200
    finally:
        server.should_exit = True
        t.join(timeout=10)


def test_the_interceptor_refuses_other_sites_too(proxy_module, monkeypatch):
    """The port the `claude` capture run talks to: it must not take a web page's request (it could plant a stranger's login as the captured one)."""
    import aiohttp
    local_guard.ENABLED = True
    port = free_port()
    monkeypatch.setattr(proxy_module, "INTERCEPTOR_PORT", port)
    sent = []

    async def go():
        async def fake_handler(request):
            sent.append(request.path)
            return proxy_module.web.json_response({"ok": True})
        # the real guard, in front of a stand-in handler (the real one would forward to api.anthropic.com)
        app = proxy_module.web.Application(middlewares=[proxy_module._interceptor_guard])
        app.router.add_route("*", "/{p:.*}", fake_handler)
        runner = proxy_module.web.AppRunner(app)
        await runner.setup()
        await proxy_module.web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            async with aiohttp.ClientSession() as s:
                base = f"http://127.0.0.1:{port}/v1/messages"
                r = await s.post(base, data="{}", headers={"Origin": "https://evil.example"})
                assert r.status == 403
                r = await s.post(base, data="{}", headers={"Host": f"rebind.example:{port}"})
                assert r.status == 403
                r = await s.post(base, data="{}")
                assert r.status == 200
        finally:
            await runner.cleanup()

    asyncio.run(go())
    assert sent == ["/v1/messages"]
