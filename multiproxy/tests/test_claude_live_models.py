"""Tests for the Claude card's live model list (2026-09-30): Anthropic's GET /v1/models called with the
captured Claude CLI login, cached, refreshable, with a built-in fallback and a live-derived default.

The real endpoint was confirmed to accept a subscription login the same day; these tests use a fake one.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

LIVE = [  # the shape the real endpoint returns (order as returned: newest first, plus older models)
    {"type": "model", "id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "created_at": "2026-09-28T00:00:00Z"},
    {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "created_at": "2026-09-21T16:24:00Z"},
    {"type": "model", "id": "claude-fable-5-1", "display_name": "Claude Fable 5.1", "created_at": "2026-08-28T00:00:00Z"},
    {"type": "model", "id": "claude-sonnet-5", "display_name": "Claude Sonnet 5", "created_at": "2026-06-29T00:00:00Z"},
    {"type": "model", "id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-15T00:00:00Z"},
    {"type": "model", "id": "claude-opus-4-6", "display_name": "Claude Opus 4.6", "created_at": "2026-02-04T00:00:00Z"},
]


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """Stands in for aiohttp.ClientSession: serves `pages` (list of responses) in order and records calls."""
    calls = []

    def __init__(self, pages):
        self.pages = pages  # shared, so successive sessions consume successive responses

    def get(self, url, headers=None, params=None):
        FakeSession.calls.append({"url": url, "headers": headers, "params": params})
        return self.pages.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def signed_in(proxy_module):
    auth = MagicMock(is_ready=True, body_template={"model": "x"}, last_error=None, headers={
        "Authorization": "Bearer x", "anthropic-beta": "oauth-2025-04-20", "anthropic-version": "2023-06-01",
        "Content-Length": "5", "Content-Type": "application/json", "Accept-Encoding": "gzip"})
    with patch.object(proxy_module, "auth", auth):
        yield auth


def _serve(proxy_module, pages):
    FakeSession.calls = []
    return patch.object(proxy_module.aiohttp, "ClientSession", lambda *a, **k: FakeSession(pages))


@pytest.mark.asyncio
class TestFetch:
    async def test_list_is_parsed_and_uses_the_captured_login(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE, "has_more": False})]):
            raw = await proxy_module._fetch_claude_models()
        assert [m["id"] for m in raw] == [m["id"] for m in LIVE]
        sent = FakeSession.calls[0]["headers"]
        assert sent["Authorization"] == "Bearer x" and sent["anthropic-beta"] == "oauth-2025-04-20"
        assert not {"Content-Length", "Content-Type", "Accept-Encoding"} & set(sent)

    async def test_follows_pages(self, proxy_module, signed_in):
        pages = [FakeResp(200, {"data": LIVE[:2], "has_more": True, "last_id": "claude-opus-5-5"}),
                 FakeResp(200, {"data": LIVE[2:], "has_more": False})]
        with _serve(proxy_module, pages):
            raw = await proxy_module._fetch_claude_models()
        assert len(raw) == len(LIVE)
        assert FakeSession.calls[1]["params"]["after_id"] == "claude-opus-5-5"

    async def test_cached_until_refresh_forces_a_new_call(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE}), FakeResp(200, {"data": LIVE[:1]})]):
            await proxy_module._fetch_claude_models()
            again = await proxy_module._fetch_claude_models()
            assert len(again) == len(LIVE) and len(FakeSession.calls) == 1
            forced = await proxy_module._fetch_claude_models(force=True)
        assert len(forced) == 1 and len(FakeSession.calls) == 2

    async def test_not_signed_in_makes_no_call(self, proxy_module):
        with patch.object(proxy_module, "auth", MagicMock(is_ready=False, headers=None)), _serve(proxy_module, []):
            assert await proxy_module._fetch_claude_models() is None
        assert FakeSession.calls == []

    @pytest.mark.parametrize("status", [404, 500])
    async def test_refusal_returns_none_and_keeps_the_login(self, proxy_module, signed_in, status):
        with _serve(proxy_module, [FakeResp(status)]):
            assert await proxy_module._fetch_claude_models() is None
        signed_in.invalidate.assert_not_called()

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_sign_in_is_refreshed_once_then_given_up_on_quietly(self, proxy_module, signed_in, status):
        # (changed 2026-10-02: a revoked login used to keep the list failing until a chat request refreshed it; the list now does that itself, once)
        signed_in.ensure_ready = AsyncMock()
        with _serve(proxy_module, [FakeResp(status), FakeResp(status)]):
            assert await proxy_module._fetch_claude_models() is None
        signed_in.invalidate.assert_called_once()
        signed_in.ensure_ready.assert_awaited_once()
        assert len(FakeSession.calls) == 2

    async def test_network_error_returns_none(self, proxy_module, signed_in):
        class Boom(FakeSession):
            def get(self, *a, **k):
                raise proxy_module.aiohttp.ClientError("down")
        with patch.object(proxy_module.aiohttp, "ClientSession", lambda *a, **k: Boom([])):
            assert await proxy_module._fetch_claude_models() is None

    async def test_non_claude_and_empty_results_are_ignored(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(200, {"data": [{"id": "gpt-x"}, "junk"]})]):
            assert await proxy_module._fetch_claude_models() is None


@pytest.mark.asyncio
class TestStatusRows:
    async def test_live_rows_are_newest_first_with_display_names_and_known_notes_only(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE})]):
            st = await proxy_module._claude_status()
        assert st["live"] is True and st["label"] == "Models"
        ids = [r[0] for r in st["models"]]
        assert ids[:3] == ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]
        rows = {r[0]: r for r in st["models"]}
        assert rows["claude-sonnet-5-5"][1:] == ["Sonnet 5.5", "The best mix of speed and smarts."]
        assert rows["claude-haiku-4-5-20251001"][2] == "The fastest model with near-frontier intelligence"
        # models we have no wording for show the name alone -- nothing invented
        assert rows["claude-sonnet-5"][2] == "" and rows["claude-opus-4-6"][2] == ""

    async def test_fallback_when_live_list_fails(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(403)]):
            st = await proxy_module._claude_status()
        assert st["live"] is False and st["label"] == "Models"
        assert [r[0] for r in st["models"]] == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-5-5"]
        assert st["models"][0][2] == "For complex tasks and hard work."

    async def test_v1_models_carries_the_live_claude_models(self, proxy_module, signed_in, test_client):
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE})]):
            ids = [m["id"] for m in test_client.get("/v1/models").json()["data"]]
        assert "claude-opus-5-5" in ids and "claude-opus-4-6" in ids

    async def test_refresh_route(self, proxy_module, signed_in, test_client):
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE[:1]})]):
            body = test_client.get("/config/claude-models").json()
        assert body["label"] == "Models" and [r[0] for r in body["models"]] == ["claude-sonnet-5-5"]

    async def test_dashboard_entry_is_refreshable_and_live(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE})]):
            entry = [p for p in await proxy_module._dashboard_providers() if p["id"] == "claude"][0]
        assert entry["live"] is True and entry["refreshEndpoint"] == "/config/claude-models"
        assert entry["models"][0][0] == "claude-sonnet-5-5"


@pytest.mark.asyncio
class TestDefaultModel:
    async def test_builtin_default_before_any_list(self, proxy_module):
        assert proxy_module._default_claude_model() == proxy_module.DEFAULT_MODEL == "claude-sonnet-5-5"

    async def test_newest_sonnet_from_live_list_wins(self, proxy_module, signed_in):
        newer = dict(LIVE[0], id="claude-sonnet-5-6", created_at="2026-12-01T00:00:00Z")
        with _serve(proxy_module, [FakeResp(200, {"data": LIVE + [newer]})]):
            await proxy_module._fetch_claude_models()
        assert proxy_module._default_claude_model() == "claude-sonnet-5-6"

    async def test_default_survives_a_failed_refresh(self, proxy_module, signed_in):
        newer = dict(LIVE[0], id="claude-sonnet-5-6", created_at="2026-12-01T00:00:00Z")
        with _serve(proxy_module, [FakeResp(200, {"data": [newer]}), FakeResp(500)]):
            await proxy_module._fetch_claude_models()
            await proxy_module._fetch_claude_models(force=True)
        assert proxy_module._default_claude_model() == "claude-sonnet-5-6"

    async def test_list_without_a_sonnet_falls_back(self, proxy_module, signed_in):
        with _serve(proxy_module, [FakeResp(200, {"data": [LIVE[1]]})]):
            await proxy_module._fetch_claude_models()
        assert proxy_module._default_claude_model() == proxy_module.DEFAULT_MODEL

    async def test_quick_test_default_and_no_model_requests_follow_it(self, proxy_module, signed_in, test_client):
        newer = dict(LIVE[0], id="claude-sonnet-5-6", created_at="2026-12-01T00:00:00Z")
        with _serve(proxy_module, [FakeResp(200, {"data": [newer]})]):
            await proxy_module._fetch_claude_models()
        assert 'const DEFAULT_MODEL_ID = "claude-sonnet-5-6"' in test_client.get("/").text

    async def test_label_from_id_when_no_live_name(self, proxy_module):
        f = proxy_module._claude_model_label
        assert f("claude-sonnet-5-5") == "Sonnet 5.5"
        assert f("claude-haiku-4-5-20251001") == "Haiku 4.5"
        assert f("claude-fable-5") == "Fable 5"
