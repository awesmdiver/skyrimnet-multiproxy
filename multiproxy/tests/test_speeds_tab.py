"""Tests for the Speeds tab (2026-09-19) -- per-AI response times, labeled by what SkyrimNet
actually uses each model for.

Covers: speed_tracker capturing real timing through the real chat_completions() dispatch path
(both non-streaming and streaming, success and failure), the /stats/speeds endpoint shape, role
chips built from a SkyrimNet-shaped variants yaml, Steam auto-detection, the linked-path config
endpoints and the speed-thresholds config endpoint. (Until 2026-09-27 this also covered a button
that flipped SkyrimNet's usage-sharing setting; it's gone -- see test_dashboard_and_health.py.)
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_response(status=200, text=""):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    if status == 200:
        import json as _json
        resp.json = AsyncMock(return_value=_json.loads(text))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(resp):
    session = MagicMock()
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


OK_CHAT_BODY = (
    '{"choices":[{"message":{"content":"hi there"},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":5,"completion_tokens":3}}'
)


def _card(pm, raw):
    """The card a model the way SkyrimNet names it (its raw yaml string) lands on: its provider and its model together."""
    return pm.speed_queries.card_key(pm._provider_label_for(raw), pm._resolve_model_name(raw))


def _rec(pm, raw, model, elapsed, ok=True):
    """Record a call the way chat_completions does: the stripped model name and the provider that served it."""
    pm.speed_tracker.record(model, elapsed, ok, pm._provider_label_for(raw))


class TestSpeedTrackerViaRealDispatch:
    """End-to-end through the real /v1/chat/completions route -- proves the instrumentation added
    to chat_completions() actually fires, not just the _SpeedTracker class in isolation."""

    def test_successful_non_streaming_call_is_recorded(self, proxy_module, test_client):
        resp = _make_mock_response(200, OK_CHAT_BODY)
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/anthropic/claude-haiku-4.5",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 200

        snap = proxy_module.speed_tracker.snapshot()
        assert "Kilo Gateway::anthropic/claude-haiku-4.5" in snap
        entry = snap["Kilo Gateway::anthropic/claude-haiku-4.5"]
        assert (entry["model"], entry["provider"]) == ("anthropic/claude-haiku-4.5", "Kilo Gateway")
        assert entry["last_reply"] is not None and entry["last_reply"] >= 0
        assert entry["avg"] == entry["last_reply"]
        assert entry["success_pct"] == 100
        assert entry["calls_last_hour"] == 1
        assert entry["recent"] == [entry["last_reply"]]

    def test_failed_non_streaming_call_is_recorded_as_failure(self, proxy_module, test_client):
        resp = _make_mock_response(500, "upstream exploded")
        session = _make_mock_session(resp)
        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/model",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert r.status_code == 500

        snap = proxy_module.speed_tracker.snapshot()
        assert snap["Kilo Gateway::some/model"]["success_pct"] == 0
        assert snap["Kilo Gateway::some/model"]["calls_last_hour"] == 1

    def test_streaming_call_is_recorded_only_after_the_stream_finishes(self, proxy_module, test_client):
        sse = b'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n\ndata: [DONE]\n\n'
        mock_content = MagicMock()

        async def _iter():
            yield sse

        mock_content.iter_any.return_value = _iter()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.content = mock_content
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        session = _make_mock_session(mock_resp)

        with patch.object(proxy_module, "kilo_api_key", "kilo-test"), \
             patch.object(proxy_module, "auth", MagicMock(session=None)), \
             patch("aiohttp.ClientSession", return_value=session):
            r = test_client.post("/v1/chat/completions", json={
                "model": "kilo/some/streamed-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
        assert r.status_code == 200

        snap = proxy_module.speed_tracker.snapshot()
        assert snap["Kilo Gateway::some/streamed-model"]["success_pct"] == 100
        assert snap["Kilo Gateway::some/streamed-model"]["calls_last_hour"] == 1

    def test_key_not_configured_failure_before_any_upstream_call_is_still_recorded(self, proxy_module, test_client):
        """No provider key at all -> HTTPException raised inside the provider call before any
        real network call. Still a completed request for that model, so still counts as a failure."""
        r = test_client.post("/v1/chat/completions", json={
            "model": "kilo/provider/model",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 500
        snap = proxy_module.speed_tracker.snapshot()
        assert snap["Kilo Gateway::provider/model"]["success_pct"] == 0


class TestSpeedTrackerUnit:
    def test_rolling_history_caps_at_twenty_and_hour_window_prunes_old_calls(self, proxy_module):
        tracker = proxy_module._SpeedTracker()
        for i in range(25):
            tracker.record("m", float(i), True, "P")
        snap = tracker.snapshot()["P::m"]
        assert len(snap["recent"]) == 10  # sparkline: last ~10
        assert snap["last_reply"] == 24.0
        assert snap["calls_last_hour"] == 25  # hour count isn't capped at 20 like the rolling stats are

    def test_mixed_success_and_failure_gives_correct_percentage(self, proxy_module):
        tracker = proxy_module._SpeedTracker()
        tracker.record("m", 1.0, True, "P")
        tracker.record("m", 1.0, True, "P")
        tracker.record("m", 1.0, False, "P")
        tracker.record("m", 1.0, True, "P")
        assert tracker.snapshot()["P::m"]["success_pct"] == 75

    def test_unseen_model_is_absent(self, proxy_module):
        tracker = proxy_module._SpeedTracker()
        assert tracker.snapshot() == {}


class TestResolveModelName:
    """_resolve_model_name is the single source of truth chat_completions() and role-chip
    matching now both share -- shouldn't ever fall out of sync with the routing prefixes
    is_X_model/strip_X_prefix actually implement."""

    def test_strips_a_real_prefixed_provider(self, proxy_module):
        assert proxy_module._resolve_model_name("deepseek/deepseek-v3.2") == "deepseek-v3.2"
        assert proxy_module._resolve_model_name("glmzai/glm-4.6") == "glm-4.6"
        assert proxy_module._resolve_model_name("kilo/anthropic/claude-sonnet-5") == "anthropic/claude-sonnet-5"

    def test_bare_claude_model_is_unchanged(self, proxy_module):
        """No prefix to strip at all -- must not regress the case that already worked."""
        assert proxy_module._resolve_model_name("claude-sonnet-5") == "claude-sonnet-5"

    def test_plain_openrouter_passthrough_is_unchanged(self, proxy_module):
        """OpenRouter's own strip is a no-op (lambda m: m) -- must not regress the other case
        that already worked before this fix."""
        assert proxy_module._resolve_model_name("anthropic/claude-haiku-4.5") == "anthropic/claude-haiku-4.5"
        assert proxy_module._resolve_model_name("meta-llama/llama-3.3-70b-instruct") == "meta-llama/llama-3.3-70b-instruct"


class TestStatsSpeedsEndpoint:
    def test_shape_with_no_calls_and_not_linked(self, proxy_module, test_client):
        r = test_client.get("/stats/speeds")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "linked": False,
            "skyrim_install_path": None,
            "skyrimnet_found": None,
            "thresholds": proxy_module.DEFAULT_SPEED_THRESHOLDS,
            "starts_talking_thresholds": proxy_module.DEFAULT_STARTS_TALKING_THRESHOLDS,
            "pc": "all",
            "models": [],
            "ownTime": {"medianMs": None, "requests": 0},
        }

    def test_role_chips_merge_in_once_linked(self, proxy_module, test_client, tmp_path):
        config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "OpenRouter.yaml").write_text(
            "openrouter:\n"
            "  model_name: deepseek/deepseek-v3.2\n"
            "  api_key: \"\"\n"
            "  variants:\n"
            "    AgentDefault:\n"
            "      model_name: anthropic/claude-haiku-4.5\n"
            "    DiaryGeneration:\n"
            "      model_name: qwen/qwen3-235b-a22b-2507\n"
            "    memory:\n"
            "      model_name: qwen/qwen3-235b-a22b-2507\n"
            "    gamemaster_evaluation:\n"
            "      model_name: meta-llama/llama-3.3-70b-instruct\n",
            encoding="utf-8",
        )
        _rec(proxy_module, "anthropic/claude-haiku-4.5", "anthropic/claude-haiku-4.5", 1.5)
        _rec(proxy_module, "meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-3.3-70b-instruct", 4.0)
        _rec(proxy_module, "qwen/qwen3-235b-a22b-2507", "qwen/qwen3-235b-a22b-2507", 6.0)

        with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
            r = test_client.get("/stats/speeds")
        assert r.status_code == 200
        body = r.json()
        assert body["linked"] is True

        by_model = {m["model"]: m for m in body["models"]}
        agent_default_chips = {c["label"] for c in by_model["anthropic/claude-haiku-4.5"]["roles"]}
        assert agent_default_chips == {"AI Assistant"}  # not "Dialogue" -- root model_name differs

        gm_chip = by_model["meta-llama/llama-3.3-70b-instruct"]["roles"]
        assert gm_chip == [{"label": "GameMaster", "class": "gamemaster", "category": "gamemaster"}]

        # A model backing BOTH Diary Generation and Memory carries both chips.
        diary_model_chips = {c["label"] for c in by_model["qwen/qwen3-235b-a22b-2507"]["roles"]}
        assert diary_model_chips == {"Diary Generation", "Memory"}
        for c in by_model["qwen/qwen3-235b-a22b-2507"]["roles"]:
            assert c["class"] == ("diary" if c["label"] == "Diary Generation" else "other")
            assert c["category"] == "background"

    def test_root_model_name_gets_the_dialogue_chip(self, proxy_module, test_client, tmp_path):
        config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "GLM.yaml").write_text(
            "glm:\n  model_name: glm-4.6\n  variants:\n    vision:\n      model_name: glm-4.6v\n",
            encoding="utf-8",
        )
        _rec(proxy_module, "glm-4.6", "glm-4.6", 1.0)
        with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
            r = test_client.get("/stats/speeds")
        by_model = {m["model"]: m for m in r.json()["models"]}
        assert by_model["glm-4.6"]["roles"] == [{"label": "Dialogue", "class": "dialogue", "category": "dialogue"}]

    def test_prefixed_provider_model_still_gets_its_role_chip(self, proxy_module, test_client, tmp_path):
        """Regression test for the real live bug (2026-09-20): SkyrimNet's own yaml stores the RAW,
        unstripped model string ("deepseek/deepseek-v3.2"), but speed_tracker records the STRIPPED
        name chat_completions() actually sends to the provider ("deepseek-v3.2") -- role chips
        silently failed to match for every prefixed provider (DeepSeek, GLM, Nano-GPT, Kilo,
        Gemini, Ollama, Codex, Antigravity), which is most real setups, not an edge case. This is
        the exact model from the director's own live screenshot."""
        config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "DeepSeek.yaml").write_text(
            "deepseek:\n"
            "  model_name: deepseek/deepseek-v3.2\n"
            "  variants:\n"
            "    combat:\n"
            "      model_name: deepseek/deepseek-flash\n",
            encoding="utf-8",
        )
        # Keyed by the STRIPPED name -- exactly what speed_tracker.record() gets called with from
        # the real chat_completions() dispatch (see stats_model there), never the raw yaml string.
        _rec(proxy_module, "deepseek/deepseek-v3.2", "deepseek-v3.2", 1.2)
        _rec(proxy_module, "deepseek/deepseek-flash", "deepseek-flash", 0.8)

        with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
            r = test_client.get("/stats/speeds")
        assert r.status_code == 200
        by_model = {m["model"]: m for m in r.json()["models"]}

        assert by_model["deepseek-v3.2"]["roles"] == [
            {"label": "Dialogue", "class": "dialogue", "category": "dialogue"}
        ]
        assert by_model["deepseek-flash"]["roles"] == [
            {"label": "Combat", "class": "other", "category": "background"}
        ]
        # The bug's own failure mode: chips silently missing, not an error -- assert they're
        # non-empty rather than just "the request succeeded."
        assert by_model["deepseek-v3.2"]["roles"] != []
        assert by_model["deepseek-flash"]["roles"] != []

    def test_unrecognized_variant_key_shows_its_raw_name_not_a_failure(self, proxy_module, tmp_path):
        doc = {"model_name": "m0", "variants": {"someWeirdNewRoleKey": "m1"}}
        chips = proxy_module._model_role_chips(doc)
        chips = {k.split("::", 1)[1]: v for k, v in chips.items()}                # (card keys are "<provider>::<model>")
        assert chips["m1"][0]["label"] == "someWeirdNewRoleKey"   # a job declared nowhere shows its raw name, as SkyrimNet does (job names from SkyrimNet, 2026-10-02)
        assert chips["m1"][0]["class"] == "other"
        assert chips["m1"][0]["category"] == "background"

    def test_no_skyrimnet_style_yaml_present_returns_none_not_an_error(self, proxy_module, tmp_path):
        config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "Unrelated.yaml").write_text("some_other_shape:\n  foo: bar\n", encoding="utf-8")
        assert proxy_module._find_skyrimnet_variants_doc(str(tmp_path)) is None

    def test_missing_config_dir_returns_none(self, proxy_module, tmp_path):
        assert proxy_module._find_skyrimnet_variants_doc(str(tmp_path)) is None

    def test_finds_the_variants_doc_via_an_mo2_mods_sibling_when_direct_path_is_missing(self, proxy_module, tmp_path):
        """2026-09-22 fix: the linked root is an MO2 instance's "Stock Game" folder with nothing
        under its own Data\\ -- SkyrimNet's real config lives under a sibling mods\\<Name>\\
        instead. Same real doc lookup that already worked for the direct-path case, just found
        via the shared resolver's MO2 fallback."""
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"
        stock_game.mkdir(parents=True)
        config_dir = instance / "mods" / "SkyrimNet Output" / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "provider.yaml").write_text(
            "some_provider:\n  model_name: mo2-model\n  variants:\n    dialogue:\n      model_name: mo2-model\n",
            encoding="utf-8",
        )
        doc = proxy_module._find_skyrimnet_variants_doc(str(stock_game))
        assert doc == {"model_name": "mo2-model", "model_names": ["mo2-model"], "variants": {"dialogue": ["mo2-model"]}}



class TestModelRotations:
    """SkyrimNet Beta 25 (2026-09-26): a role can ROTATE through several models -- `model_name:
    google/gemma-4-31b-it, deepseek/deepseek-v3.2` plus a `model_params:` list of `{name, provider_id,
    ...}` (see model-presets/presets/openrouter-default.yaml in the SkyrimNet repo). The chips used to
    treat `model_name` as ONE model, so a rotation matched nothing and its chips vanished."""

    ROTATION_YAML = (
        "openrouter:\n"
        "  model_name: google/gemma-4-31b-it, deepseek/deepseek-v3.2\n"
        "  model_params:\n"
        "    - { name: google/gemma-4-31b-it, provider_id: openrouter, temperature: 1.0 }\n"
        "    - { name: deepseek/deepseek-v3.2, provider_id: openrouter, temperature: 0.7 }\n"
        "  variants:\n"
        "    combat:\n"
        "      model_name: google/gemma-4-26b-a4b-it, google/gemma-4-31b-it, deepseek/deepseek-v4.1-flash\n"
        "      model_params:\n"
        "        - { name: google/gemma-4-26b-a4b-it, provider_id: openrouter }\n"
        "        - { name: google/gemma-4-31b-it, provider_id: openrouter }\n"
        "        - { name: deepseek/deepseek-v4.1-flash, provider_id: openrouter }\n"
        "    memory:\n"
        "      model_name: qwen/qwen3-235b-a22b-2507\n"
        "    vision:\n"
        "      model_params:\n"
        "        - { name: google/gemma-4-26b-a4b-it, provider_id: openrouter }\n"
    )

    def _write(self, tmp_path, text):
        config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "OpenRouter.yaml").write_text(text, encoding="utf-8")

    def test_model_names_splits_commas_trims_and_reads_model_params(self, proxy_module):
        entry = {
            "model_name": " a/one ,b/two,, a/one ",
            "model_params": [{"name": "b/two"}, {"name": "c/three"}, {"provider_id": "x"}, "junk"],
        }
        assert proxy_module._model_names(entry) == ["a/one", "b/two", "c/three"]

    def test_model_names_takes_names_from_model_params_alone(self, proxy_module):
        assert proxy_module._model_names({"model_params": [{"name": "only/here"}]}) == ["only/here"]

    def test_model_names_accepts_a_bare_string_or_list(self, proxy_module):
        assert proxy_module._model_names("a, b") == ["a", "b"]
        assert proxy_module._model_names(["a", "b, c"]) == ["a", "b", "c"]
        assert proxy_module._model_names(None) == []

    def test_finder_returns_every_model_of_a_rotation(self, proxy_module, tmp_path):
        self._write(tmp_path, self.ROTATION_YAML)
        doc = proxy_module._find_skyrimnet_variants_doc(str(tmp_path))
        assert doc["model_names"] == ["google/gemma-4-31b-it", "deepseek/deepseek-v3.2"]
        assert doc["variants"]["combat"] == [
            "google/gemma-4-26b-a4b-it", "google/gemma-4-31b-it", "deepseek/deepseek-v4.1-flash",
        ]
        assert doc["variants"]["memory"] == ["qwen/qwen3-235b-a22b-2507"]
        assert doc["variants"]["vision"] == ["google/gemma-4-26b-a4b-it"]   # named only in model_params

    def test_every_model_in_a_rotation_gets_the_roles_chip(self, proxy_module, tmp_path):
        self._write(tmp_path, self.ROTATION_YAML)
        doc = proxy_module._find_skyrimnet_variants_doc(str(tmp_path))
        chips = proxy_module._model_role_chips(doc)
        labels = lambda raw: [c["label"] for c in chips[_card(proxy_module, raw)]]
        # Keyed by the RESOLVED name, same as every other chip: "deepseek/..." is DeepSeek's own
        # prefix, so it's stripped (see _resolve_model_name); "google/..." and "qwen/..." aren't.
        assert labels("deepseek/deepseek-v3.2") == ["Dialogue"]
        assert labels("google/gemma-4-31b-it") == ["Dialogue", "Combat"]        # in two roles: both chips
        assert labels("google/gemma-4-26b-a4b-it") == ["Combat", "Vision"]
        assert labels("deepseek/deepseek-v4.1-flash") == ["Combat"]
        assert labels("qwen/qwen3-235b-a22b-2507") == ["Memory"]

    def test_a_single_model_role_is_unchanged(self, proxy_module):
        chips = proxy_module._model_role_chips({"model_name": "m0", "variants": {"combat": "m1"}})
        assert [c["label"] for c in chips[_card(proxy_module, "m0")]] == ["Dialogue"]
        assert [c["label"] for c in chips[_card(proxy_module, "m1")]] == ["Combat"]

    def test_a_legacy_comma_string_doc_is_split_too(self, proxy_module):
        chips = proxy_module._model_role_chips({"model_name": "a, b", "variants": {"memory": "c, d"}})
        assert set(chips) == {_card(proxy_module, m) for m in "abcd"}

    def test_stats_speeds_shows_chips_for_a_rotation_end_to_end(self, proxy_module, test_client, tmp_path):
        self._write(tmp_path, (
            "deepseek:\n"
            "  model_name: deepseek/deepseek-v3.2, deepseek/deepseek-flash\n"
            "  variants:\n"
            "    memory:\n"
            "      model_name: deepseek/deepseek-flash\n"
        ))
        _rec(proxy_module, "deepseek/deepseek-v3.2", "deepseek-v3.2", 1.2)
        _rec(proxy_module, "deepseek/deepseek-flash", "deepseek-flash", 0.8)
        with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
            r = test_client.get("/stats/speeds")
        by_model = {m["model"]: [c["label"] for c in m["roles"]] for m in r.json()["models"]}
        assert by_model == {"deepseek-v3.2": ["Dialogue"], "deepseek-flash": ["Dialogue", "Memory"]}


class TestSkyrimPathLinking:
    def test_detect_finds_nothing_when_no_steam_libraries(self, proxy_module, test_client):
        with patch.object(proxy_module, "_find_steam_libraries", return_value=[]):
            r = test_client.get("/config/skyrim-path/detect")
        assert r.status_code == 200
        assert r.json() == {"linked": False, "path": None}
        assert proxy_module.skyrim_install_path == ""

    def test_detect_finds_and_persists_a_real_looking_install(self, proxy_module, test_client, tmp_path):
        lib = tmp_path / "SteamLibrary"
        game_dir = lib / "steamapps" / "common" / "Skyrim Special Edition"
        # A "config" subfolder is required (2026-09-22, the shared _resolve_skyrimnet_dir
        # validity check) -- a bare empty SkyrimNet folder no longer counts as "really has
        # SkyrimNet installed" for either the manual-link path OR Steam auto-detect now that both
        # share the one resolver.
        (game_dir / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config").mkdir(parents=True)
        with patch.object(proxy_module, "_find_steam_libraries", return_value=[str(lib)]):
            r = test_client.get("/config/skyrim-path/detect")
        assert r.status_code == 200
        body = r.json()
        assert body["linked"] is True
        assert body["path"] == str(game_dir)
        assert body["source"] == "steam"
        assert body["skyrimnet_found"] is True
        assert proxy_module.skyrim_install_path == str(game_dir)

    def test_a_steam_library_without_skyrimnet_installed_is_not_matched(self, proxy_module, test_client, tmp_path):
        lib = tmp_path / "SteamLibrary"
        game_dir = lib / "steamapps" / "common" / "Skyrim Special Edition"
        game_dir.mkdir(parents=True)  # game installed, but no SkyrimNet folder under it
        with patch.object(proxy_module, "_find_steam_libraries", return_value=[str(lib)]):
            r = test_client.get("/config/skyrim-path/detect")
        assert r.json() == {"linked": False, "path": None}

    def test_manual_link_requires_an_existing_folder(self, proxy_module, test_client):
        r = test_client.post("/config/skyrim-path", json={"path": "Z:\\definitely\\not\\real"})
        assert r.status_code == 400

    def test_manual_link_where_the_direct_path_has_skyrimnet_succeeds_with_no_warning(self, proxy_module, test_client, tmp_path):
        skyrim_root = tmp_path / "Skyrim Special Edition"
        (skyrim_root / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config").mkdir(parents=True)
        r = test_client.post("/config/skyrim-path", json={"path": str(skyrim_root)})
        assert r.status_code == 200
        assert r.json() == {"linked": True, "path": str(skyrim_root), "skyrimnet_found": True}

    def test_manual_link_where_only_an_mo2_sibling_has_skyrimnet_still_succeeds_found_via_fallback(self, proxy_module, test_client, tmp_path):
        """2026-09-22 fix: linking an MO2 instance's "Stock Game" folder (nothing under its own
        Data\\) must still report skyrimnet_found: True once the shared resolver's MO2 fallback
        finds the real files under a sibling mods\\<Name>\\ folder."""
        instance = tmp_path / "FakeInstance"
        stock_game = instance / "Stock Game"
        stock_game.mkdir(parents=True)
        (instance / "mods" / "SkyrimNet Output" / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config").mkdir(parents=True)
        r = test_client.post("/config/skyrim-path", json={"path": str(stock_game)})
        assert r.status_code == 200
        assert r.json() == {"linked": True, "path": str(stock_game), "skyrimnet_found": True}

    def test_manual_link_and_unlink_round_trip(self, proxy_module, test_client, tmp_path):
        r = test_client.post("/config/skyrim-path", json={"path": str(tmp_path)})
        assert r.status_code == 200
        # tmp_path has no SkyrimNet folder anywhere under it -- linking still succeeds (a warning,
        # never a hard block), but skyrimnet_found correctly comes back False.
        assert r.json() == {"linked": True, "path": str(tmp_path), "skyrimnet_found": False}
        assert proxy_module.skyrim_install_path == str(tmp_path)

        r = test_client.post("/config/skyrim-path", json={"path": ""})
        assert r.status_code == 200
        assert r.json() == {"linked": False, "path": None}
        assert proxy_module.skyrim_install_path == ""

    def test_find_steam_libraries_returns_empty_without_the_registry_key(self, proxy_module):
        # Real, unmocked call -- on a CI/dev box with no Steam registry key at all, or on a
        # non-Windows machine (no winreg module), this must degrade to [] rather than raising.
        result = proxy_module._find_steam_libraries()
        assert isinstance(result, list)

    @pytest.mark.skipif(sys.platform != "win32", reason="finds Steam through the Windows registry")
    def test_parses_a_real_shaped_libraryfolders_vdf_with_multiple_libraries(self, proxy_module, tmp_path):
        """Real, live-confirmed shape (2026-09-19, this dev machine's own libraryfolders.vdf) --
        Valve's own KeyValues format, not JSON, with numbered sections and tab-separated
        key/value pairs. Also confirms Windows path backslashes (escaped as \\\\ in the file) come
        back as plain single backslashes."""
        steam_root = tmp_path / "Steam"
        (steam_root / "steamapps").mkdir(parents=True)
        (steam_root / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n'
            "{\n"
            '\t"0"\n'
            "\t{\n"
            '\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n'
            '\t\t"label"\t\t""\n'
            "\t}\n"
            '\t"1"\n'
            "\t{\n"
            '\t\t"path"\t\t"D:\\\\Games"\n'
            '\t\t"label"\t\t""\n'
            "\t}\n"
            "}\n",
            encoding="utf-8",
        )

        class _FakeKeyCtx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import winreg
        with patch.object(winreg, "OpenKey", return_value=_FakeKeyCtx()), \
             patch.object(winreg, "QueryValueEx", return_value=(str(steam_root), 1)):
            libs = proxy_module._find_steam_libraries()

        assert libs[0] == str(steam_root)  # the registry's own SteamPath always comes first
        assert "C:\\Program Files (x86)\\Steam" in libs
        assert "D:\\Games" in libs
        assert not any("\\\\" in lib for lib in libs)  # unescaped, not double-backslashed


class TestSpeedThresholdsEndpoint:
    def test_round_trips_through_config_json(self, proxy_module, test_client):
        payload = {
            "dialogue": {"good": 1.5, "slow": 3.0},
            "gamemaster": {"good": 2.5, "slow": 5.0},
            "vision": {"good": 3.5, "slow": 7.0},
            "background": {"good": 6.0, "slow": 12.0},
        }
        r = test_client.post("/config/speed-thresholds", json=payload)
        assert r.status_code == 200
        assert r.json()["thresholds"] == payload
        assert proxy_module.speed_thresholds == payload

        saved = proxy_module._load_config()
        assert saved["speed_thresholds"] == payload

    def test_rejects_a_missing_category(self, proxy_module, test_client):
        payload = {"dialogue": {"good": 1.0, "slow": 2.0}}
        r = test_client.post("/config/speed-thresholds", json=payload)
        assert r.status_code == 400

    def test_rejects_slow_less_than_good(self, proxy_module, test_client):
        payload = dict(proxy_module.DEFAULT_SPEED_THRESHOLDS)
        payload["dialogue"] = {"good": 5.0, "slow": 1.0}
        r = test_client.post("/config/speed-thresholds", json=payload)
        assert r.status_code == 400

    def test_rejects_non_numeric_values(self, proxy_module, test_client):
        payload = dict(proxy_module.DEFAULT_SPEED_THRESHOLDS)
        payload["vision"] = {"good": "fast", "slow": 8.0}
        r = test_client.post("/config/speed-thresholds", json=payload)
        assert r.status_code == 400
