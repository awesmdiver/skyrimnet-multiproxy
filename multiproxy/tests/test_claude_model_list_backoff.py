"""Claude's model list (2026-10-02): a FAILED fetch is remembered, not retried on every poll; the failure is logged once when it starts and once when it ends; a
refused sign-in (401) is refreshed once and tried again; Refresh and a changed sign-in try again at once. The real log showed the same "not available (HTTP 401)"
line every 2 seconds for as long as the dashboard (or the tray app's poll) was open."""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


class FakeAnthropic:
    """Stands in for aiohttp.ClientSession: answers from a queue of statuses, and counts the calls."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0
        self.tokens = []

    def __call__(self, *a, **k):
        outer = self

        class Session:
            async def __aenter__(s): return s
            async def __aexit__(s, *x): return False

            def get(s, url, headers=None, params=None):
                outer.calls += 1
                outer.tokens.append(headers.get("Authorization"))
                status = outer.statuses.pop(0) if len(outer.statuses) > 1 else outer.statuses[0]

                class Resp:
                    async def __aenter__(r): return r
                    async def __aexit__(r, *x): return False
                    @property
                    def status(r): return status
                    async def json(r): return {"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "created_at": "2026-09-01T00:00:00Z"}], "has_more": False}
                return Resp()
        return Session()


@pytest.fixture
def claude(proxy_module, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="proxy")
    state = {"token": "Bearer old"}
    auth = MagicMock()
    auth.is_ready = True
    auth.headers = {"Authorization": "Bearer old"}
    auth.invalidate = MagicMock()

    async def refresh(force=False, timeout=60):
        auth.headers = {"Authorization": "Bearer new"}
        auth.is_ready = True
    auth.ensure_ready = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(proxy_module, "auth", auth)
    monkeypatch.setattr(proxy_module, "_claude_models_last_good", None)

    def use(statuses):
        fake = FakeAnthropic(statuses)
        monkeypatch.setattr(proxy_module.aiohttp, "ClientSession", fake)
        return fake
    return proxy_module, auth, use, caplog


def lines(caplog, word):
    return [r.getMessage() for r in caplog.records if word in r.getMessage()]


@pytest.mark.asyncio
async def test_a_failing_list_is_asked_once_then_left_alone_until_the_back_off_ends(claude, monkeypatch):
    pm, auth, use, caplog = claude
    fake = use([500])
    for _ in range(25):                                   # the dashboard and the tray app polling every couple of seconds
        assert await pm._fetch_claude_models() is None
    assert fake.calls == 1
    assert len(lines(caplog, "model list isn't available")) == 1
    t = pm._claude_models_fail["at"]
    monkeypatch.setattr(pm.time, "time", lambda: t + pm.CLAUDE_MODEL_LIST_FAIL_BACKOFF + 1)
    assert await pm._fetch_claude_models() is None
    assert fake.calls == 2, "tried again after the back-off"
    assert len(lines(caplog, "model list isn't available")) == 1, "...and the same spell isn't logged again"


@pytest.mark.asyncio
async def test_the_failure_line_says_why_in_plain_words_and_never_the_token(claude):
    pm, auth, use, caplog = claude
    use([503])
    await pm._fetch_claude_models()
    msg = lines(caplog, "model list isn't available")[0]
    assert msg == "Claude's model list isn't available: Anthropic answered HTTP 503. Showing the saved list; trying again in a few minutes, or when you press Refresh."
    assert "Bearer" not in msg and "old" not in msg


@pytest.mark.asyncio
async def test_a_refused_sign_in_is_refreshed_once_and_the_list_recovers_without_a_chat_request(claude):
    pm, auth, use, caplog = claude
    fake = use([401, 200])
    models = await pm._fetch_claude_models()
    assert models and models[0]["id"] == "claude-sonnet-5-5"
    auth.invalidate.assert_called_once()
    auth.ensure_ready.assert_awaited_once()
    assert fake.tokens == ["Bearer old", "Bearer new"], "the second try used the refreshed sign-in"
    assert lines(caplog, "model list isn't available") == [] and pm._claude_models_fail["failing"] is False


@pytest.mark.asyncio
async def test_a_sign_in_that_stays_refused_is_one_log_line_a_plain_panel_label_and_no_second_refresh_until_the_back_off_ends(claude):
    pm, auth, use, caplog = claude
    fake = use([401])
    for _ in range(10):
        await pm._fetch_claude_models()
    assert fake.calls == 2 and auth.ensure_ready.await_count == 1, "one try, one refresh, one retry; then quiet"
    assert lines(caplog, "model list isn't available") == ["Claude's model list isn't available: Anthropic refused the sign-in (HTTP 401). Showing the saved list; trying again in a few minutes, or when you press Refresh."]
    status = await pm._claude_status()
    assert status["live"] is False and status["label"] == "Models"   # the plain-steps Setup rewrite shortened the label; live is False says it is the saved list


@pytest.mark.asyncio
async def test_a_recovery_is_logged_once(claude, monkeypatch):
    pm, auth, use, caplog = claude
    fake = use([500, 200])
    await pm._fetch_claude_models()
    assert (await pm._fetch_claude_models(force=True)) is not None
    assert lines(caplog, "available again") == ["Claude's model list is available again"]
    for _ in range(5):
        await pm._fetch_claude_models(force=True)
    assert len(lines(caplog, "available again")) == 1, "only a recovery from a failure is announced"


@pytest.mark.asyncio
async def test_refresh_forces_a_new_try_even_inside_the_back_off(claude):
    pm, auth, use, caplog = claude
    fake = use([500])
    await pm._fetch_claude_models()
    await pm._fetch_claude_models()
    assert fake.calls == 1
    await pm._claude_status(force=True)
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_a_changed_sign_in_is_tried_again_at_once(claude):
    pm, auth, use, caplog = claude
    fake = use([500])
    await pm._fetch_claude_models()
    auth.headers = {"Authorization": "Bearer someone-signed-in-again"}
    await pm._fetch_claude_models()
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_not_signed_in_yet_is_neither_a_try_nor_a_log_line(claude):
    pm, auth, use, caplog = claude
    fake = use([200])
    auth.is_ready = False
    assert await pm._fetch_claude_models() is None and fake.calls == 0 and lines(caplog, "model list") == []


@pytest.mark.asyncio
async def test_a_good_list_is_cached_as_before(claude):
    pm, auth, use, caplog = claude
    fake = use([200])
    await pm._fetch_claude_models()
    await pm._fetch_claude_models()
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_a_network_failure_is_remembered_too(claude, monkeypatch):
    pm, auth, use, caplog = claude

    def boom(*a, **k):
        raise pm.aiohttp.ClientConnectionError("no route")
    monkeypatch.setattr(pm.aiohttp, "ClientSession", boom)
    for _ in range(4):
        assert await pm._fetch_claude_models() is None
    assert len(lines(caplog, "model list isn't available")) == 1 and "couldn't reach it" in lines(caplog, "model list isn't available")[0]


@pytest.mark.asyncio
async def test_the_tray_status_keeps_the_provider_answer_for_a_few_seconds(proxy_module, monkeypatch):
    calls = []

    async def providers():
        calls.append(1)
        return [{"state": "ok"}, {"state": "off"}]
    monkeypatch.setattr(proxy_module, "_dashboard_providers", providers)
    for _ in range(10):
        s = await proxy_module._tray_status()
    assert len(calls) == 1 and s["readyCount"] == 1 and s["totalAIs"] == 2
    t = proxy_module._tray_providers_cache["at"]
    monkeypatch.setattr(proxy_module.time, "time", lambda: t + proxy_module.TRAY_PROVIDERS_CACHE_SECONDS + 1)
    await proxy_module._tray_status()
    assert len(calls) == 2
