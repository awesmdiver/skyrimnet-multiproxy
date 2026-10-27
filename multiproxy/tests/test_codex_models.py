"""Tests for Codex model discovery (2026-09-16) -- _parse_codex_models_cache/_fetch_codex_models/
_codex_status, reading the CLI's own real, account-specific model list from its on-disk cache
(CODEX_HOME or ~/.codex)/models_cache.json -- there is no `codex models` subcommand.

Real shapes throughout: the recorded models_cache.json entries below are copied verbatim from a
real file (client_version 0.154.0, captured live 2026-09-16) -- not guessed. See TECHNICAL.md's own
"Codex model discovery" section for the full narrative.

Mocking shape mirrors test_geminicli_provider.py's TestParseAgyModelsOutput/TestFetchAgyModels/
TestAgyStatus (a local-resource discovery mechanism, not a network call) -- _fetch_codex_models
reads a file instead of running a subprocess, so tests patch `_codex_models_cache_path` to point at
a tmp_path file instead of mocking `create_subprocess_exec`.
"""
import json
from unittest.mock import patch

import pytest


def _make_async(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


REAL_MODELS_CACHE = {
    "fetched_at": "2026-09-16T06:54:01.051468300Z",
    "etag": 'W/"2145b3c13c2342cc29fb6c67b2394fc6"',
    "client_version": "0.154.0",
    "models": [
        {
            "slug": "gpt-reserve", "display_name": "GPT-Reserve",
            "description": "Fast and affordable agentic coding model.",
            "visibility": "hide", "supported_in_api": True, "priority": 3,
        },
        {
            "slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra",
            "description": "Balanced agentic coding model for everyday work.",
            "visibility": "list", "supported_in_api": True, "priority": 7,
        },
        {
            "slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna",
            "description": "Fast and affordable agentic coding model.",
            "visibility": "list", "supported_in_api": True, "priority": 8,
        },
        {
            "slug": "gpt-5.5", "display_name": "GPT-5.5",
            "description": "Proven previous-generation model for coding and general work",
            "visibility": "list", "supported_in_api": True, "priority": 12,
        },
        {
            "slug": "codex-auto-review", "display_name": "Codex Auto Review",
            "description": "Automatic approval review model for Codex.",
            "visibility": "hide", "supported_in_api": True, "priority": 43,
        },
    ],
}


# ---- _parse_codex_models_cache ----

class TestParseCodexModelsCache:
    def test_real_recorded_cache_filters_and_orders_by_priority(self, proxy_module):
        rows = proxy_module._parse_codex_models_cache(REAL_MODELS_CACHE)
        # gpt-reserve (priority 3, hidden) and codex-auto-review (priority 43, hidden) excluded --
        # visibility is the real gate, ordering is by priority among what's left.
        assert rows == [
            ("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work."),
            ("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model."),
            ("gpt-5.5", "GPT-5.5", "Proven previous-generation model for coding and general work"),
        ]

    def test_visibility_hide_excluded_even_when_supported_in_api(self, proxy_module):
        raw = {"models": [{"slug": "x", "visibility": "hide", "supported_in_api": True, "priority": 1}]}
        assert proxy_module._parse_codex_models_cache(raw) == []

    def test_supported_in_api_false_excluded_even_when_visibility_list(self, proxy_module):
        """The task's own explicit instruction: honour supported_in_api as a second gate, even
        though no entry in the real recorded cache currently needs it -- a future model could set
        this False while still marked 'list'."""
        raw = {"models": [{"slug": "x", "display_name": "X", "visibility": "list", "supported_in_api": False, "priority": 1}]}
        assert proxy_module._parse_codex_models_cache(raw) == []

    def test_model_missing_slug_is_skipped(self, proxy_module):
        raw = {"models": [{"display_name": "No Slug", "visibility": "list", "supported_in_api": True}]}
        assert proxy_module._parse_codex_models_cache(raw) == []

    def test_missing_display_name_falls_back_to_slug(self, proxy_module):
        raw = {"models": [{"slug": "bare-model", "visibility": "list", "supported_in_api": True, "priority": 1}]}
        assert proxy_module._parse_codex_models_cache(raw) == [("bare-model", "bare-model", "")]

    def test_empty_models_list_returns_empty(self, proxy_module):
        assert proxy_module._parse_codex_models_cache({"models": []}) == []

    def test_missing_models_key_raises_value_error(self, proxy_module):
        with pytest.raises(ValueError):
            proxy_module._parse_codex_models_cache({"fetched_at": "..."})

    def test_models_not_a_list_raises_value_error(self, proxy_module):
        with pytest.raises(ValueError):
            proxy_module._parse_codex_models_cache({"models": "not-a-list"})


# ---- _fetch_codex_models ----

class TestFetchCodexModels:
    @pytest.mark.asyncio
    async def test_returns_none_when_file_missing(self, proxy_module, tmp_path, monkeypatch):
        """A brand-new Codex install that has never run won't have this file -- an expected,
        disclosed case, not an error."""
        missing = tmp_path / "does-not-exist.json"
        monkeypatch.setattr(proxy_module, "_codex_models_cache_path", lambda: str(missing))
        proxy_module._codex_models_cache = (0.0, None)
        assert await proxy_module._fetch_codex_models() is None

    @pytest.mark.asyncio
    async def test_returns_none_on_malformed_json(self, proxy_module, tmp_path, monkeypatch):
        bad = tmp_path / "models_cache.json"
        bad.write_text("not json at all", encoding="utf-8")
        monkeypatch.setattr(proxy_module, "_codex_models_cache_path", lambda: str(bad))
        proxy_module._codex_models_cache = (0.0, None)
        assert await proxy_module._fetch_codex_models() is None

    @pytest.mark.asyncio
    async def test_returns_none_on_unexpected_shape(self, proxy_module, tmp_path, monkeypatch):
        bad = tmp_path / "models_cache.json"
        bad.write_text(json.dumps({"totally": "different shape"}), encoding="utf-8")
        monkeypatch.setattr(proxy_module, "_codex_models_cache_path", lambda: str(bad))
        proxy_module._codex_models_cache = (0.0, None)
        assert await proxy_module._fetch_codex_models() is None

    @pytest.mark.asyncio
    async def test_returns_parsed_rows_on_success(self, proxy_module, tmp_path, monkeypatch):
        good = tmp_path / "models_cache.json"
        good.write_text(json.dumps(REAL_MODELS_CACHE), encoding="utf-8")
        monkeypatch.setattr(proxy_module, "_codex_models_cache_path", lambda: str(good))
        proxy_module._codex_models_cache = (0.0, None)
        rows = await proxy_module._fetch_codex_models()
        assert rows == [
            ("gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work."),
            ("gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model."),
            ("gpt-5.5", "GPT-5.5", "Proven previous-generation model for coding and general work"),
        ]

    @pytest.mark.asyncio
    async def test_caches_within_ttl(self, proxy_module, tmp_path, monkeypatch):
        good = tmp_path / "models_cache.json"
        good.write_text(json.dumps(REAL_MODELS_CACHE), encoding="utf-8")
        read_count = {"n": 0}
        real_open = open

        def _counting_path():
            read_count["n"] += 1
            return str(good)
        monkeypatch.setattr(proxy_module, "_codex_models_cache_path", _counting_path)
        proxy_module._codex_models_cache = (0.0, None)
        await proxy_module._fetch_codex_models()
        await proxy_module._fetch_codex_models()
        assert read_count["n"] == 1, "second call within the TTL should be served from cache"

    @pytest.mark.asyncio
    async def test_force_bypasses_cache(self, proxy_module, tmp_path, monkeypatch):
        good = tmp_path / "models_cache.json"
        good.write_text(json.dumps(REAL_MODELS_CACHE), encoding="utf-8")
        read_count = {"n": 0}

        def _counting_path():
            read_count["n"] += 1
            return str(good)
        monkeypatch.setattr(proxy_module, "_codex_models_cache_path", _counting_path)
        proxy_module._codex_models_cache = (0.0, None)
        await proxy_module._fetch_codex_models()
        await proxy_module._fetch_codex_models(force=True)
        assert read_count["n"] == 2


class TestCodexModelsCachePath:
    def test_uses_codex_home_when_set(self, proxy_module, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", "/some/custom/codex/home")
        path = proxy_module._codex_models_cache_path()
        assert path in ("/some/custom/codex/home/models_cache.json", "/some/custom/codex/home\\models_cache.json") or path.replace("\\", "/") == "/some/custom/codex/home/models_cache.json"

    def test_falls_back_to_home_dot_codex(self, proxy_module, monkeypatch):
        monkeypatch.delenv("CODEX_HOME", raising=False)
        path = proxy_module._codex_models_cache_path().replace("\\", "/")
        assert path.endswith("/.codex/models_cache.json")


# ---- _codex_status ----

class TestCodexStatus:
    @pytest.mark.asyncio
    async def test_not_installed(self, proxy_module):
        status = await proxy_module._codex_status()
        assert status["state"] == "off"
        assert status["status"] == "Not installed"
        assert status["testable"] is False
        assert status["models"] == []

    @pytest.mark.asyncio
    async def test_installing_codex_is_picked_up_live_without_a_restart(self, proxy_module, monkeypatch):
        """Regression test for the real bug (2026-09-19): CODEX_PATH used to be a one-time
        `shutil.which("codex")` snapshot taken at proxy startup, so installing Codex (or running
        `codex login`) while the proxy was already running never got picked up -- every status
        check, including the dashboard's own Refresh button, kept reading the stale "not found"
        result forever. Only a full proxy restart fixed it. _codex_path() is now a genuinely live
        check, so this proves the fix WITHOUT reloading or re-importing the module -- that's the
        whole point: no restart should ever be needed again."""
        status = await proxy_module._codex_status()
        assert status["state"] == "off"
        assert status["status"] == "Not installed"

        # Codex "gets installed" mid-session -- no module reload, just the live PATH lookup
        # starting to find it, exactly like a real `npm install -g @openai/codex` would cause.
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(False)), \
             patch.object(proxy_module, "_fetch_codex_models", _make_async(None)):
            status = await proxy_module._codex_status()
        assert status["state"] != "off"
        assert status["status"] != "Not installed"

        # And confirm it's a genuinely live check, not a one-time re-snapshot: with the patch
        # removed again, the very next call goes right back to "not installed".
        status = await proxy_module._codex_status()
        assert status["state"] == "off"
        assert status["status"] == "Not installed"

    @pytest.mark.asyncio
    async def test_installed_and_logged_in_with_real_models(self, proxy_module, monkeypatch):
        async def _fake_rows(*, force=False):
            return [("gpt-5.6-terra", "GPT-5.6-Terra", "desc")]
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(True)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_rows):
            status = await proxy_module._codex_status()
        assert status["state"] == "ok"
        assert status["testable"] is True
        assert status["status"] == "Ready: 1 model available"
        assert status["models"] == [["codex/gpt-5.6-terra", "GPT-5.6-Terra", "desc"]]
        assert status["any"] is None

    @pytest.mark.asyncio
    async def test_installed_not_logged_in_but_real_models_still_shown(self, proxy_module, monkeypatch):
        """A stale cache from a previous login can still exist while logged out -- the real list is
        informational either way, but testable must track login state, not file availability."""
        async def _fake_rows(*, force=False):
            return [("gpt-5.5", "GPT-5.5", "desc")]
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(False)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_rows):
            status = await proxy_module._codex_status()
        assert status["state"] == "warn"
        assert status["testable"] is False
        assert status["models"] == [["codex/gpt-5.5", "GPT-5.5", "desc"]]
        assert "Sign in to Codex" in status["empty"]

    @pytest.mark.asyncio
    async def test_installed_logged_in_but_cache_unreadable_falls_back(self, proxy_module):
        async def _fake_none(*, force=False):
            return None
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(True)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_none):
            status = await proxy_module._codex_status()
        assert status["state"] == "ok"  # logged in, so still testable with the one known-good model
        assert status["testable"] is True
        assert status["models"] == proxy_module.CODEX_FALLBACK_MODELS
        # Approved copy (2026-09-16): count included, correctly singular for the one real fallback
        # entry -- CODEX_FALLBACK_MODELS' own real length drives the count, never hardcoded, so a
        # future second fallback model pluralises automatically.
        assert status["status"] == "Ready: 1 default model"
        assert status["label"] == "Models"
        assert status["any"] == "codex/MODEL"

    @pytest.mark.asyncio
    async def test_brand_new_install_not_logged_in_and_no_cache_falls_back(self, proxy_module):
        """The exact 'never run' case the item's own instructions call out."""
        async def _fake_none(*, force=False):
            return None
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(False)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_none):
            status = await proxy_module._codex_status()
        assert status["state"] == "warn"
        assert status["testable"] is False
        assert status["models"] == proxy_module.CODEX_FALLBACK_MODELS
        assert status["status"] == "Not signed in"


class TestDashboardProvidersCodexWiring:
    @pytest.mark.asyncio
    async def test_codex_entry_reflects_codex_status(self, proxy_module):
        async def _fake_rows(*, force=False):
            return [("gpt-5.6-terra", "GPT-5.6-Terra", "desc")]
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(True)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_rows):
            providers = await proxy_module._dashboard_providers()
        codex = next(p for p in providers if p["id"] == "codex")
        assert codex["state"] == "ok"
        assert codex["testable"] is True
        assert codex["models"] == [["codex/gpt-5.6-terra", "GPT-5.6-Terra", "desc"]]
        assert codex["refreshEndpoint"] == "/config/codex-models"


class TestRefreshRoute:
    def test_refresh_route_forces_a_fresh_check(self, proxy_module, test_client):
        calls = []

        async def _fake_rows(*, force=False):
            calls.append(force)
            return [("gpt-5.5", "GPT-5.5", "desc")]
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(True)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_rows):
            resp = test_client.get("/config/codex-models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "ok"
        assert data["models"] == [["codex/gpt-5.5", "GPT-5.5", "desc"]]
        assert calls == [True], "the Refresh route must bypass the cache"


class TestListModelsIncludesRealCodexCatalog:
    def test_v1_models_uses_real_codex_status(self, proxy_module, test_client):
        async def _fake_rows(*, force=False):
            return [("gpt-5.6-terra", "GPT-5.6-Terra", "desc")]
        with patch.object(proxy_module, "_codex_path", lambda: "/fake/path/codex"), \
             patch.object(proxy_module, "_codex_logged_in", _make_async(True)), \
             patch.object(proxy_module, "_fetch_codex_models", _fake_rows):
            resp = test_client.get("/v1/models")
        ids = [m["id"] for m in resp.json()["data"]]
        assert "codex/gpt-5.6-terra" in ids
        assert "codex/gpt-5.2-codex" not in ids  # the old hardcoded, never-real entry must be gone
