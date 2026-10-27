"""Tests for Ollama model discovery (2026-09-13): the dashboard used to offer a hand-picked
example model (ollama/llama3.1:8b) nobody actually had installed, so Quick Test's own "success
path" always failed live. _fetch_ollama_tags/_ollama_status replace that with Ollama's real
GET /api/tags (local daemon, or Ollama Cloud once a key is saved).

conftest.py's autouse `_mock_ollama_tags` fixture replaces `proxy._fetch_ollama_tags` with a
"daemon not reachable" stub for every test in this suite EXCEPT here, where tests either override
it per-scenario (for _ollama_status/_dashboard_providers/route tests) or call
`REAL_FETCH_OLLAMA_TAGS` directly (for the real fetch/cache/timeout logic itself).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from conftest import REAL_FETCH_OLLAMA_TAGS

_QWEN_30B = {"name": "qwen3:30b", "size": 18556699314, "details": {"parameter_size": "30.5B", "quantization_level": "Q4_K_M"}}
_QWEN_8B = {"name": "qwen3:8b", "size": 5225388164, "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M"}}


def _make_mock_response(status=200, json_data=None):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(resp):
    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestOllamaApiRoot:
    def test_strips_v1_suffix(self, proxy_module):
        assert proxy_module._ollama_api_root("http://localhost:11434/v1") == "http://localhost:11434"
        assert proxy_module._ollama_api_root("https://ollama.com/v1") == "https://ollama.com"

    def test_leaves_non_v1_url_unchanged(self, proxy_module):
        assert proxy_module._ollama_api_root("http://localhost:11434") == "http://localhost:11434"


class TestFetchOllamaTags:
    """Exercises the REAL _fetch_ollama_tags (via conftest's REAL_FETCH_OLLAMA_TAGS reference),
    not the autouse-mocked module attribute -- these prove the actual network/timeout/cache
    handling works, which the mock in every other test deliberately bypasses."""

    @pytest.mark.asyncio
    async def test_returns_parsed_json_on_200(self, proxy_module):
        resp = _make_mock_response(200, {"models": [_QWEN_8B]})
        session = _make_mock_session(resp)
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", return_value=session):
            result = await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
        assert result == {"models": [_QWEN_8B]}
        url = session.get.call_args.args[0]
        assert url == "http://localhost:11434/api/tags"

    @pytest.mark.asyncio
    async def test_returns_none_on_non_200(self, proxy_module):
        resp = _make_mock_response(500, {})
        session = _make_mock_session(resp)
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", return_value=session):
            result = await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self, proxy_module):
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", side_effect=aiohttp.ClientConnectionError("refused")):
            result = await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, proxy_module):
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", side_effect=asyncio.TimeoutError()):
            result = await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
        assert result is None

    @pytest.mark.asyncio
    async def test_sends_bearer_auth_for_cloud(self, proxy_module):
        resp = _make_mock_response(200, {"models": []})
        session = _make_mock_session(resp)
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", return_value=session):
            await REAL_FETCH_OLLAMA_TAGS("https://ollama.com/v1", "cloud-test-key")
        headers = session.get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer cloud-test-key"

    @pytest.mark.asyncio
    async def test_caches_within_ttl(self, proxy_module):
        resp = _make_mock_response(200, {"models": [_QWEN_8B]})
        session = _make_mock_session(resp)
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", return_value=session):
            await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
            await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
        assert session.get.call_count == 1, "second call within the TTL should be served from cache"

    @pytest.mark.asyncio
    async def test_force_bypasses_cache(self, proxy_module):
        resp = _make_mock_response(200, {"models": [_QWEN_8B]})
        session = _make_mock_session(resp)
        proxy_module._ollama_tags_cache.clear()
        with patch("aiohttp.ClientSession", return_value=session):
            await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1")
            await REAL_FETCH_OLLAMA_TAGS("http://localhost:11434/v1", force=True)
        assert session.get.call_count == 2


class TestOllamaModelRow:
    def test_local_full_details(self, proxy_module):
        row = proxy_module._ollama_model_row(_QWEN_8B, cloud=False)
        assert row == ["ollama/qwen3:8b", "qwen3:8b", "8.2B · Q4_K_M · 4.9 GB"]

    def test_local_missing_quantization(self, proxy_module):
        m = {"name": "custom:latest", "size": 1073741824, "details": {"parameter_size": "1B"}}
        row = proxy_module._ollama_model_row(m, cloud=False)
        assert row == ["ollama/custom:latest", "custom:latest", "1B · 1.0 GB"]

    def test_local_missing_all_details(self, proxy_module):
        m = {"name": "bare:latest"}
        row = proxy_module._ollama_model_row(m, cloud=False)
        assert row == ["ollama/bare:latest", "bare:latest", ""]

    def test_cloud_gets_plain_note_regardless_of_size(self, proxy_module):
        m = {"name": "glm-5.3-flash", "size": 999999999999, "details": {}}
        row = proxy_module._ollama_model_row(m, cloud=True)
        assert row == ["ollama/glm-5.3-flash", "glm-5.3-flash", "Cloud model"]


class TestOllamaStatus:
    """The five states from the task spec, each via a mocked _fetch_ollama_tags."""

    @pytest.mark.asyncio
    async def test_local_running_with_models_plural(self, proxy_module):
        async def _fake(base_url, api_key=None, *, force=False):
            return {"models": [_QWEN_30B, _QWEN_8B]}
        with patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            status = await proxy_module._ollama_status()
        assert status["state"] == "ok"
        assert status["status"] == "Local: 2 models installed"
        assert status["testable"] is True
        assert [r[0] for r in status["models"]] == ["ollama/qwen3:30b", "ollama/qwen3:8b"]  # sorted by name

    @pytest.mark.asyncio
    async def test_local_running_with_one_model_singular(self, proxy_module):
        async def _fake(base_url, api_key=None, *, force=False):
            return {"models": [_QWEN_8B]}
        with patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            status = await proxy_module._ollama_status()
        assert status["status"] == "Local: 1 model installed"

    @pytest.mark.asyncio
    async def test_local_running_no_models(self, proxy_module):
        async def _fake(base_url, api_key=None, *, force=False):
            return {"models": []}
        with patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            status = await proxy_module._ollama_status()
        assert status["state"] == "warn"
        assert status["status"] == "Local: no models installed"
        assert status["testable"] is False
        assert "has no models" in status["empty"]

    @pytest.mark.asyncio
    async def test_local_not_running(self, proxy_module):
        async def _fake(base_url, api_key=None, *, force=False):
            return None
        with patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            status = await proxy_module._ollama_status()
        assert status["state"] == "off"
        assert status["status"] == "Not running"
        assert status["testable"] is False
        assert "isn't running" in status["empty"]

    @pytest.mark.asyncio
    async def test_cloud_success(self, proxy_module):
        async def _fake(base_url, api_key=None, *, force=False):
            assert base_url == proxy_module.OLLAMA_CLOUD_BASE_URL
            assert api_key == "cloud-key"
            return {"models": [{"name": "glm-5.3-flash", "size": 1, "details": {}}]}
        with patch.object(proxy_module, "ollama_api_key", "cloud-key"), \
             patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            status = await proxy_module._ollama_status()
        assert status["state"] == "ok"
        assert status["status"] == "Ollama Cloud: 1 model"
        assert status["testable"] is True
        assert status["models"] == [["ollama/glm-5.3-flash", "glm-5.3-flash", "Cloud model"]]

    @pytest.mark.asyncio
    async def test_cloud_fetch_failed_still_testable(self, proxy_module):
        """Task's own rule: 'Quick Test lists Ollama when the Cloud list has >=1 model OR the key
        is saved' -- a failed Cloud fetch must NOT drop Ollama out of Quick Test."""
        async def _fake(base_url, api_key=None, *, force=False):
            return None
        with patch.object(proxy_module, "ollama_api_key", "cloud-key"), \
             patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            status = await proxy_module._ollama_status()
        assert status["state"] == "ok"
        assert status["status"] == "Ollama Cloud (key saved)"
        assert status["testable"] is True
        assert status["models"] == []
        assert status["any"] == "ollama/MODEL"
        assert "Couldn't load the cloud model list" in status["empty"]


class TestDashboardProvidersOllamaWiring:
    @pytest.mark.asyncio
    async def test_ollama_entry_reflects_ollama_status(self, proxy_module):
        async def _fake(base_url, api_key=None, *, force=False):
            return {"models": [_QWEN_8B]}
        with patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            providers = await proxy_module._dashboard_providers()
        ollama = next(p for p in providers if p["id"] == "ollama")
        assert ollama["state"] == "ok"
        assert ollama["status"] == "Local: 1 model installed"
        assert ollama["testable"] is True
        assert ollama["models"] == [["ollama/qwen3:8b", "qwen3:8b", "8.2B · Q4_K_M · 4.9 GB"]]
        assert ollama["live"] is True


class TestRefreshRoute:
    def test_refresh_route_forces_a_fresh_check(self, proxy_module, test_client):
        calls = []

        async def _fake(base_url, api_key=None, *, force=False):
            calls.append(force)
            return {"models": [_QWEN_8B]}

        with patch.object(proxy_module, "_fetch_ollama_tags", _fake):
            resp = test_client.get("/config/ollama-models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "ok"
        assert data["testable"] is True
        assert data["models"] == [["ollama/qwen3:8b", "qwen3:8b", "8.2B · Q4_K_M · 4.9 GB"]]
        assert calls == [True], "the Refresh route must bypass the cache"
