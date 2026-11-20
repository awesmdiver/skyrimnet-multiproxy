"""Security review 4.3.0, S4 and S5: Google's error text never puts the scene in the log, and the Google call never follows a redirect (the key header would go along).

Stand-ins only (a server on 127.0.0.1)."""
import asyncio
import logging

import pytest
from aiohttp import web

from tests.test_google_decisions import ALL_KINDS, FAKE_KEY, goog  # noqa: F401  (the stand-in server and its fixture)

SCENE = "SECRETSCENE Lydia whispered the plan to hide the dragonstone under the floor"


def call(proxy_module, state=SCENE):
    return asyncio.run(proxy_module.call_google_decisions("gemini-decision-1", state, ALL_KINDS["questions"], "t"))


def logged(caplog):
    return " ".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("status,body", [
    (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Input blocked because: " + SCENE}}),
    (400, {"error": {"code": "safety", "message": SCENE}}),
    (429, {"error": {"message": "slow down: " + SCENE}}),
    (500, {"detail": SCENE}),
    (403, {"error": SCENE}),
])
def test_an_error_answer_that_repeats_the_scene_never_puts_it_in_the_log(proxy_module, goog, caplog, status, body):
    caplog.set_level(logging.DEBUG)
    goog.status, goog.body = status, body
    got, payload, _ = call(proxy_module)
    assert got == status and payload == b""                       # the decision is still handed over
    text = logged(caplog)
    assert "SECRETSCENE" not in text and "dragonstone" not in text
    assert f"Google {status}" in text                             # the status is what the log says


def test_the_short_code_word_is_kept_and_is_only_letters_and_digits(proxy_module, goog, caplog):
    caplog.set_level(logging.DEBUG)
    goog.status, goog.body = 400, {"error": {"status": "INVALID_ARGUMENT\nforged line", "message": SCENE}}
    call(proxy_module)
    text = logged(caplog)
    assert "INVALID_ARGUMENTforgedline" in text and "\n" not in text.split("Google 400")[1][:80] and "SECRETSCENE" not in text


def test_a_broken_error_body_is_a_plain_miss(proxy_module, goog, caplog):
    caplog.set_level(logging.DEBUG)
    goog.status, goog.body = 502, ["not", "a", "dict"]
    got, _, _ = call(proxy_module)
    assert got == 502 and "SECRETSCENE" not in logged(caplog)


def test_the_key_still_never_reaches_the_log(proxy_module, goog, caplog):
    caplog.set_level(logging.DEBUG)
    goog.status, goog.body = 400, {"error": {"status": "X", "message": f"bad key {FAKE_KEY}"}}
    call(proxy_module)
    assert FAKE_KEY not in logged(caplog)


def test_a_redirect_is_not_followed_and_the_key_goes_nowhere_else(proxy_module, monkeypatch, caplog):
    """A server that answers the Google address with a redirect to another host: nothing reaches the other host, the decision is handed over."""
    caplog.set_level(logging.DEBUG)
    seen = {"second": 0, "first": 0}

    async def run():
        async def final(request):
            seen["second"] += 1
            seen["headers"] = dict(request.headers)
            return web.json_response({"answers": {}}, status=200)
        app2 = web.Application()
        app2.router.add_post("/x", final)
        r2 = web.AppRunner(app2)
        await r2.setup()
        s2 = web.TCPSite(r2, "127.0.0.1", 0)
        await s2.start()
        p2 = s2._server.sockets[0].getsockname()[1]

        async def first(request):
            seen["first"] += 1
            raise web.HTTPTemporaryRedirect(f"http://localhost:{p2}/x")          # another origin
        app1 = web.Application()
        app1.router.add_post("/v1beta/decisions", first)
        r1 = web.AppRunner(app1)
        await r1.setup()
        s1 = web.TCPSite(r1, "127.0.0.1", 0)
        await s1.start()
        p1 = s1._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(proxy_module, "GOOGLE_DECISIONS_URL", f"http://127.0.0.1:{p1}/v1beta/decisions")
        monkeypatch.setattr(proxy_module, "auth", type("A", (), {"session": None, "is_ready": False})())
        monkeypatch.setattr(proxy_module, "gemini_api_key", FAKE_KEY)
        out = await proxy_module.call_google_decisions("gemini-decision-1", SCENE, ALL_KINDS["questions"], "t")
        await r1.cleanup()
        await r2.cleanup()
        return out
    status, body, _ = asyncio.new_event_loop().run_until_complete(run())
    assert seen["first"] == 1 and seen["second"] == 0, seen                  # the redirect target was never asked
    assert status == 307 and body == b""                                      # a miss: the decision is passed on
    text = logged(caplog)
    assert "redirect not followed" in text and FAKE_KEY not in text and "SECRETSCENE" not in text


def test_the_card_line_for_a_redirect_is_the_plain_one(proxy_module):
    assert proxy_module.google_decision_line(307) == "Google didn't answer."
