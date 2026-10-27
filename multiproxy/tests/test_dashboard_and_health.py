"""Smoke tests for /health, /v1/models, and the dashboard (/) -- no existing reference project
equivalent to adapt from (these endpoints are specific to this project's own dashboard design), but
written for the same reason as everything else here: the dashboard has already broken once from an
untested edge case (the Nano-GPT Quick Test dropdown wrongly hiding a real, concrete model id), and
these are cheap to run on every change.
"""
from unittest.mock import patch

import pytest


def test_health_reports_every_provider(test_client, proxy_module):
    resp = test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "glm_configured", "nano_configured", "openai_configured",
        "deepseek_configured", "kilo_configured", "gemini_configured", "xai_configured", "continuum_configured", "cloudflare_configured", "mistral_configured", "groq_configured", "ollama_cloud_configured", "grok_cli_found", "grok_logged_in",
        "codex_cli_found", "codex_logged_in", "antigravity_cli_found", "antigravity_logged_in",
    ):
        assert key in data, key
    # conftest.py resets every key/CLI path to None/False for a clean baseline
    assert data["openai_configured"] is False
    assert data["deepseek_configured"] is False
    assert data["antigravity_cli_found"] is False
    assert data["codex_cli_found"] is False


def test_models_always_includes_claude(test_client):
    resp = test_client.get("/v1/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert "claude-sonnet-5-5" in ids


def test_models_excludes_unconfigured_providers(test_client):
    """With no keys/CLI configured (conftest.py's clean baseline) AND Ollama's daemon unreachable
    (conftest.py's own autouse _mock_ollama_tags default), no glm/nano/openai/deepseek/kilo/codex/
    ollama/geminicli entries should appear at all -- only Claude."""
    resp = test_client.get("/v1/models")
    ids = [m["id"] for m in resp.json()["data"]]
    assert not any(i.startswith("glmzai/") for i in ids)
    assert not any(i.startswith("nano/") for i in ids)
    assert not any(i.startswith("openai/") for i in ids)
    assert not any(i.startswith("deepseek/") for i in ids)
    assert not any(i.startswith("kilo/") for i in ids)
    assert not any(i.startswith("codex/") for i in ids)
    assert not any(i.startswith("ollama/") for i in ids)  # daemon not reachable in tests
    assert not any(i.startswith("geminicli/") for i in ids)  # _agy_path() returns None in tests


def test_models_includes_ollama_when_running_with_models(proxy_module, test_client):
    """Real installed models (2026-09-13) show up in /v1/models as ollama/<name> entries when the
    daemon is reachable and has at least one model -- replacing the old always-present
    ollama/* placeholder."""
    async def _fake_tags(base_url, api_key=None, *, force=False):
        return {"models": [{"name": "qwen3:8b", "size": 5225388164, "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M"}}]}

    with patch.object(proxy_module, "_fetch_ollama_tags", _fake_tags):
        resp = test_client.get("/v1/models")
    ids = [m["id"] for m in resp.json()["data"]]
    assert "ollama/qwen3:8b" in ids


def test_models_includes_configured_providers(proxy_module, test_client):
    with patch.object(proxy_module, "openai_api_key", "sk-test"), \
         patch.object(proxy_module, "kilo_api_key", "kilo-test"):
        resp = test_client.get("/v1/models")
    ids = [m["id"] for m in resp.json()["data"]]
    assert any(i.startswith("openai/") for i in ids)
    assert any(i.startswith("kilo/") for i in ids)


def test_dashboard_renders_with_no_providers_configured(test_client):
    """conftest.py's test_client fixture already hits this once at setup -- a second explicit check
    here documents the intent and gives a readable failure if it ever regresses."""
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "SkyrimNet MultiProxy" in resp.text


def test_dashboard_providers_all_present_no_copilot_in_order(proxy_module):
    """The compact "Providers & Models" card is built from _dashboard_providers() -- assert its
    shape directly rather than scraping server-rendered <option> tags, since the real dropdowns are
    now built client-side from the embedded JSON (see dashboard()'s PROVIDERS constant)."""
    import asyncio

    providers = asyncio.run(proxy_module._dashboard_providers())
    ids = [p["id"] for p in providers]
    assert ids == ["claude", "glm", "nano", "openai", "deepseek", "typesafe", "perplexity", "kilo", "gemini", "xai", "continuum", "cloudflare", "mistral", "groq", "ollama", "lmstudio", "local", "remote", "codex", "geminicli", "grok"]
    assert "copilot" not in ids


def test_dashboard_key_providers_have_real_config_routes(test_client, proxy_module):
    """Every key-based provider's keyEndpoint must be a route this app actually registers."""
    import asyncio

    providers = asyncio.run(proxy_module._dashboard_providers())
    from tests.route_paths import route_paths
    registered = route_paths(proxy_module.app)
    for p in providers:
        if p.get("keyEndpoint"):
            assert p["keyEndpoint"] in registered, p["id"]


def test_dashboard_embeds_providers_json(test_client):
    """The provider list PROVIDERS is data-driven JSON embedded in the page, not a server-rendered
    per-provider card -- this is what replaced the old 7-card dashboard."""
    html = test_client.get("/").text
    assert "const PROVIDERS = " in html
    assert '"id": "kilo"' in html
    assert "Choose a provider" in html


def test_dashboard_has_ollama_refresh_and_readable_error_wiring(test_client):
    """Smoke test for the 2026-09-13 additions: Ollama's own ↻ Refresh button/route and the
    Quick Test readable-error rendering are actually wired into the page's own script.

    `refreshLive` (generalized 2026-09-15 when Gemini added a SECOND live-discovery provider,
    from what used to be a hardcoded `refreshOllama`) is the shared JS function now; the
    `/config/ollama-models` endpoint itself is untouched and still embedded in the page via
    Ollama's own `refreshEndpoint` field in the PROVIDERS JSON.

    The readable-error rendering itself moved (2026-09-16) from the old inline `#response.error`
    box into the Quick Test panel's own `.qt-turn.error` transcript rows -- see
    test_quick_test_popup_scenarios.py for the popup-specific assertions; this test just confirms
    that relocated wiring is actually present, not the retired inline mechanism."""
    html = test_client.get("/").text
    assert "refreshLive" in html
    assert "/config/ollama-models" in html
    assert ".qt-turn.error" in html
    assert "qtAddTurn('error'" in html


def test_provider_pills_are_grouped_by_how_you_pay_not_labelled_per_pill(test_client):
    """The pills (2026-10-03) say how you pay through their group heading, not a word repeated on each pill; see test_provider_picker.py."""
    html = test_client.get("/").text
    for label in ("Claude (Subscription)", "GLM (z.ai) (Key)", "Ollama (This device)", "Other devices (Network)"):
        assert f"'{label}'" not in html, label
    assert "pillLabel(p)" in html and "PICKER_GROUPS" in html


def test_the_model_card_no_longer_shows_the_repair_line(test_client):
    """Simple is the word (2026-10-02): the "N broken replies fixed" line is off the model card."""
    html = test_client.get("/").text
    assert "v2.repair_line" not in html and 'class="repairline"' not in html


def test_model_row_name_and_note_columns_wrap_long_text(test_client):
    """Real bug, real account (2026-09-16): Codex's own live catalog grew from 5 to 382 entries,
    and some real descriptions came back as full paragraphs -- .model .mid already had
    overflow-wrap:anywhere, but .model .name/.model .note didn't, so a long unbroken description
    pushed its own grid column (and the whole row/card) wider than intended instead of wrapping,
    causing horizontal scroll on the dashboard. Confirms the fix is actually present, not just
    fixed once and silently reverted later."""
    html = test_client.get("/").text
    assert ".model .name { font-size:0.85rem; overflow-wrap:anywhere }" in html
    assert ".model .note { color:var(--muted); font-size:0.78rem; overflow-wrap:anywhere }" in html


def test_dashboard_providers_testable_flag_for_no_key_and_key_saved(proxy_module):
    """Quick Test's provider filter (2026-09-13) is driven by this one server-side `testable`
    boolean per provider, not re-derived from `state` strings in JS -- a key-based provider must be
    testable exactly when it has a key."""
    import asyncio

    providers = asyncio.run(proxy_module._dashboard_providers())
    by_id = {p["id"]: p for p in providers}
    assert by_id["kilo"]["testable"] is False  # conftest.py's clean baseline: no key

    with patch.object(proxy_module, "kilo_api_key", "kilo-test"):
        providers = asyncio.run(proxy_module._dashboard_providers())
    by_id = {p["id"]: p for p in providers}
    assert by_id["kilo"]["testable"] is True


def test_dashboard_providers_testable_flag_for_codex_not_installed(proxy_module):
    """Codex not installed (_codex_path() returns None, conftest.py's clean baseline) must not be
    testable -- Quick Test can't get an answer from a CLI that isn't there."""
    import asyncio

    providers = asyncio.run(proxy_module._dashboard_providers())
    by_id = {p["id"]: p for p in providers}
    assert by_id["codex"]["testable"] is False


def test_dashboard_providers_testable_flag_for_claude_warming_up(proxy_module):
    """Deliberate exception to the otherwise blanket 'testable == (state == \"ok\")' rule: Claude
    while still warming up (no headers/body_template yet, but no last_error either -- the very
    first few seconds after startup) stays testable, since it's about to be ready and hiding the
    default model for a few seconds would be confusing rather than honest."""
    import asyncio

    with patch.object(proxy_module.auth, "headers", None), \
         patch.object(proxy_module.auth, "body_template", None), \
         patch.object(proxy_module.auth, "last_error", None):
        providers = asyncio.run(proxy_module._dashboard_providers())
    by_id = {p["id"]: p for p in providers}
    assert by_id["claude"]["state"] == "warn"
    assert by_id["claude"]["status"] == "Starting up…"
    assert by_id["claude"]["testable"] is True

    # Contrast: Claude broken (a real last_error) must NOT be testable, even though it's also `warn`.
    with patch.object(proxy_module.auth, "headers", None), \
         patch.object(proxy_module.auth, "body_template", None), \
         patch.object(proxy_module.auth, "last_error", "Your organization has disabled Claude subscription access"):
        providers = asyncio.run(proxy_module._dashboard_providers())
    by_id = {p["id"]: p for p in providers}
    assert by_id["claude"]["state"] == "warn"
    assert by_id["claude"]["testable"] is False

    # Contrast: no Claude CLI at all -- off, not testable.
    with patch.object(proxy_module, "CLAUDE_PATH", None):
        providers = asyncio.run(proxy_module._dashboard_providers())
    by_id = {p["id"]: p for p in providers}
    assert by_id["claude"]["state"] == "off"
    assert by_id["claude"]["testable"] is False


def test_dashboard_posting_empty_key_clears_each_provider(test_client, proxy_module):
    """Every key-based provider's /config/*-key endpoint must treat an empty key as a clear --
    the dashboard's real Clear button relies on this."""
    endpoints_and_attrs = [
        ("/config/glm-key", "glm_api_key"),
        ("/config/nano-key", "nano_api_key"),
        ("/config/openai-key", "openai_api_key"),
        ("/config/deepseek-key", "deepseek_api_key"),
        ("/config/kilo-key", "kilo_api_key"),
        ("/config/gemini-key", "gemini_api_key"),
        ("/config/xai-key", "xai_api_key"),
        ("/config/continuum-key", "continuum_api_key"),
        ("/config/ollama-key", "ollama_api_key"),
    ]
    for endpoint, attr in endpoints_and_attrs:
        test_client.post(endpoint, json={"key": "throwaway-value"})
        assert getattr(proxy_module, attr) is not None, attr
        resp = test_client.post(endpoint, json={"key": ""})
        assert resp.status_code == 200
        assert getattr(proxy_module, attr) is None, attr


# --- The request line (2026-09-27): /health block + the dashboard's quiet status line ---

def test_health_concurrency_block_is_empty_when_no_limit_is_set(test_client):
    assert test_client.get("/health").json()["concurrency"] == {}


def test_health_concurrency_block_reports_active_queued_and_limit(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "glm": 6, "openai": 2})
    line = proxy_module._get_request_line("glm", 6)
    line.active = 3
    line._waiters.extend([object(), object()])   # two requests waiting
    block = test_client.get("/health").json()["concurrency"]
    assert block == {
        "openai": {"active": 0, "queued": 0, "limit": 2},
        "glm": {"active": 3, "queued": 2, "limit": 6},
    }
    line._waiters.clear()


def test_health_concurrency_block_leaves_out_providers_without_a_limit(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "glm": 6})
    block = test_client.get("/health").json()["concurrency"]
    assert list(block) == ["glm"]


def test_lightweight_request_line_route_matches_health(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, "glm": 6})
    assert test_client.get("/stats/request-line").json() == test_client.get("/health").json()["concurrency"]


def test_the_setup_tab_no_longer_shows_the_live_request_line(test_client, proxy_module, monkeypatch):
    """Simple is the word (2026-10-02): the "GLM: 3 of 6 busy" meter is off the Setup tab, with or without limits.
    The /stats/request-line route stays."""
    for limits in ({}, {"glm": 6}):
        monkeypatch.setattr(proxy_module, "REQUEST_LIMITS", {**proxy_module.REQUEST_LIMITS, **limits})
        html = test_client.get("/").text
        assert 'id="requestLine"' not in html and "REQUEST_LINE_ON" not in html and "pollRequestLine" not in html


# --- SkyrimNet's usage-sharing setting: read-only pointer, never a switch (2026-09-27) ---
# `telemetry.metrics_enabled` in SkyrimNet.yaml is, in Beta 25, "Send Anonymous Usage Metrics"
# (Settings -> General -> Privacy): SkyrimNet asks the player for consent before turning it on. The
# proxy used to flip it silently from a Speeds-tab button; that button, its route and the write code
# are gone, and the proxy never writes to a SkyrimNet config file.

def test_the_speeds_tab_has_no_usage_sharing_button(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    html = test_client.get("/").text
    for gone in ("trackerBtn", "enableSkyrimNetTracker", "enable-skyrimnet-tracker",
                 "built-in speed tracker", "trackerAlready", "tracker_already_on"):
        assert gone not in html, gone


def test_the_speeds_tab_no_longer_carries_the_usage_sharing_callout(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
    html = test_client.get("/").text
    assert 'id="privacyNote"' not in html and "community stats" not in html


def test_the_old_toggle_route_is_gone(test_client):
    for method in ("post", "get", "put"):
        assert getattr(test_client, method)("/config/enable-skyrimnet-tracker").status_code in (404, 405)


def test_stats_speeds_no_longer_reads_or_reports_the_sharing_setting(test_client, proxy_module, tmp_path):
    config = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
    config.mkdir(parents=True)
    (config / "SkyrimNet.yaml").write_text("telemetry:\n  enabled: true\n  metrics_enabled: true\n", encoding="utf-8")
    with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
        body = test_client.get("/stats/speeds").json()
    assert "tracker_already_on" not in body


def test_the_proxy_has_no_code_that_writes_skyrimnet_config():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "multiproxy.py").read_text(encoding="utf-8")
    for gone in ("_flip_skyrimnet_tracker_setting", "_skyrimnet_tracker_already_on",
                 "_read_skyrimnet_tracker_block", "metrics_enabled"):
        assert gone not in src, gone


def test_dashboard_still_renders_with_speed_tracking_off(test_client, proxy_module, monkeypatch):
    monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", False)
    html = test_client.get("/").text
    assert 'id="privacyNote"' not in html
