"""Speeds: filter by device, and show where each answer ran (2026-10-01).

Where = "Cloud", "This device", or another device's nickname. Local models are saved under their bare name with the
provider "Local"; another device's models keep their `<nickname>/<model>` name and carry the nickname as provider.
"""
import asyncio
import json
import re
import sqlite3
import time

import pytest

import other_pcs as op


def seed(db, model, ts, elapsed=1.0, success=True, *, provider=None, roles=None, first_token=0.5):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO speed_history (ts, model, elapsed, success, roles_json, first_token, out_tokens, out_chars, fail_reason, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, model, elapsed, 1 if success else 0, json.dumps(roles) if roles else None, first_token, 40, 160, None, provider))
        conn.commit()


def add_pc(proxy_module, nick="den-pc"):
    proxy_module.remote_svc._remotes[nick] = op.Remote(id=nick, kind="openai", name=nick, base="http://192.168.1.50:8080", online=True)
    proxy_module.remote_svc._loaded = True


def add_local_model(proxy_module, tmp_path):
    folder = proxy_module.local_svc.set_folder(str(tmp_path / "m"))
    f = tmp_path / "x.gguf"
    f.write_bytes(b"x")
    proxy_module.local_svc.add_custom(str(f))


@pytest.fixture
def history(proxy_module):
    """One day of calls from every kind of place, saved the way the proxy really saves them."""
    db, now = proxy_module.SPEED_HISTORY_DB, time.time()
    seed(db, "claude-sonnet-5-5", now - 100, provider="Claude", roles=["Dialogue"])
    seed(db, "glm-4.6", now - 90, provider="GLM", roles=["Dialogue"])
    seed(db, "gemma-4-12b", now - 80, provider="Local", roles=["Dialogue"])          # local: bare name + provider Local
    seed(db, "den-pc/gemma-4-31b", now - 70, provider="den-pc", roles=["Dialogue"])
    seed(db, "gemma-4-12b", now - 60, success=False, provider="Local")
    seed(db, "old-model", now - 50, provider=None)                                   # from before providers were saved
    return db


# ---- the Where rule ------------------------------------------------------------------------------------------------
class TestWhereRule:
    def test_cloud_services_are_cloud(self, proxy_module):
        for model, provider in (("claude-sonnet-5-5", "Claude"), ("glm-4.6", "GLM"), ("gpt-4o-mini", "OpenAI"), ("gemini-3.8-flash", "Gemini"),
                                ("gpt-5.5", "Codex"), ("gemini-3.8-flash-high", "Antigravity"), ("deepseek-flash", "DeepSeek"),
                                ("anthropic/claude-sonnet-5", "Kilo Gateway"), ("x", "Nano-GPT"), ("jev-1.13", "TypeSafe"), ("some/model", "OpenRouter"),
                                ("llama3", "Ollama")):
            assert proxy_module._speed_where(model, provider) == "Cloud", (model, provider)

    def test_local_models_are_this_pc_by_provider_or_prefix(self, proxy_module):
        assert proxy_module._speed_where("gemma-4-12b", "Local") == "This device"
        assert proxy_module._speed_where("local/gemma-4-12b", None) == "This device"
        assert proxy_module._speed_where("LOCAL/gemma", "Claude") == "This device"

    def test_another_pcs_models_carry_its_nickname(self, proxy_module):
        add_pc(proxy_module)
        assert proxy_module._speed_where("den-pc/gemma-4-31b", "den-pc") == "den-pc"
        assert proxy_module._speed_where("den-pc/meta/llama", None) == "den-pc"

    def test_a_removed_pcs_history_keeps_its_name_and_is_never_cloud(self, proxy_module):
        assert proxy_module._speed_where("old-pc/gemma", "old-pc") == "old-pc"

    def test_old_history_with_no_provider_is_cloud(self, proxy_module):
        assert proxy_module._speed_where("old-model", None) == "Cloud"
        assert proxy_module._speed_where(None, None) == "Cloud"

    def test_the_filter_values(self, proxy_module):
        m = proxy_module._where_matches
        assert m("Cloud", "all") and m("This device", "all") and m("den-pc", "all") and m("Cloud", "") and m("Cloud", None)
        assert m("This device", "this") and not m("Cloud", "this") and not m("den-pc", "this")
        assert m("Cloud", "cloud") and not m("This device", "cloud") and not m("den-pc", "cloud")
        assert m("den-pc", "den-pc") and m("den-pc", "DEN-PC") and not m("Cloud", "den-pc") and not m("other-pc", "den-pc")

    @pytest.mark.parametrize("word", ["all", "this", "cloud"])
    def test_a_nickname_cant_be_one_of_the_filters_own_words(self, proxy_module, word):
        assert proxy_module._nickname_problem(word)


# ---- the choices -----------------------------------------------------------------------------------------------------
class TestFilterChoices:
    def test_hidden_when_there_is_nothing_but_cloud(self, test_client):
        d = test_client.get("/stats/speeds/pcs").json()
        assert d["show"] is False
        assert [o["id"] for o in d["options"]] == ["all", "this", "cloud"]

    def test_shown_with_a_local_model(self, proxy_module, test_client, tmp_path):
        add_local_model(proxy_module, tmp_path)
        assert test_client.get("/stats/speeds/pcs").json()["show"] is True

    def test_shown_with_another_pc_and_lists_it_in_the_agreed_order(self, proxy_module, test_client):
        add_pc(proxy_module, "den-pc")
        add_pc(proxy_module, "attic")
        d = test_client.get("/stats/speeds/pcs").json()
        assert d["show"] is True
        assert [(o["id"], o["label"]) for o in d["options"]] == [
            ("all", "All devices and services"), ("this", "This device"), ("den-pc", "den-pc"), ("attic", "attic"), ("cloud", "Cloud services")]

    def test_off_when_speed_tracking_is_off(self, proxy_module, test_client):
        from unittest.mock import patch
        with patch.object(proxy_module, "ENABLE_SPEED_TRACKING", False):
            assert test_client.get("/stats/speeds/pcs").status_code == 404


# ---- every view ------------------------------------------------------------------------------------------------------
def models(resp):
    return sorted(m["model"] for m in resp.json()["models"])


class TestByModel:
    def test_every_row_says_where_it_ran(self, history, proxy_module, test_client):
        add_pc(proxy_module)
        body = test_client.get("/stats/speeds?range=all").json()
        assert {m["model"]: m["where"] for m in body["models"]} == {
            "claude-sonnet-5-5": "Cloud", "glm-4.6": "Cloud", "gemma-4-12b": "This device", "den-pc/gemma-4-31b": "den-pc", "old-model": "Cloud"}

    def test_each_filter_value_on_each_range(self, history, proxy_module, test_client):
        add_pc(proxy_module)
        for rng in ("7d", "30d", "all"):
            assert models(test_client.get(f"/stats/speeds?range={rng}&pc=this")) == ["gemma-4-12b"]
            assert models(test_client.get(f"/stats/speeds?range={rng}&pc=den-pc")) == ["den-pc/gemma-4-31b"]
            assert models(test_client.get(f"/stats/speeds?range={rng}&pc=cloud")) == ["claude-sonnet-5-5", "glm-4.6", "old-model"]
            assert len(models(test_client.get(f"/stats/speeds?range={rng}&pc=all"))) == 5

    def test_today_view_filters_the_live_numbers_too(self, history, proxy_module, test_client):
        add_pc(proxy_module)
        for model, provider in (("claude-sonnet-5-5", "Claude"), ("gemma-4-12b", "Local"), ("den-pc/gemma-4-31b", "den-pc")):
            proxy_module.speed_tracker.record(model, 1.0, True, provider)
        assert models(test_client.get("/stats/speeds?range=today&pc=this")) == ["gemma-4-12b"]
        assert models(test_client.get("/stats/speeds?range=today&pc=cloud")) == ["claude-sonnet-5-5"]
        assert len(models(test_client.get("/stats/speeds?range=today"))) == 3

    def test_no_filter_means_everything_exactly_as_before(self, history, proxy_module, test_client):
        assert models(test_client.get("/stats/speeds?range=all")) == models(test_client.get("/stats/speeds?range=all&pc=all"))

    def test_the_calls_numbers_only_count_the_filtered_rows(self, history, test_client):
        m = [x for x in test_client.get("/stats/speeds?range=all&pc=this").json()["models"]][0]
        assert m["calls_total"] == 2 and m["success_pct"] == 50

    def test_an_unknown_nickname_just_shows_nothing(self, history, test_client):
        assert test_client.get("/stats/speeds?range=all&pc=nobody").json()["models"] == []


class TestByRole:
    def test_rows_say_where_and_the_filter_applies(self, history, proxy_module, test_client):
        add_pc(proxy_module)
        everyone = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all").json()["models"]
        assert {m["model"]: m["where"] for m in everyone} == {"claude-sonnet-5-5": "Cloud", "glm-4.6": "Cloud", "gemma-4-12b": "This device", "den-pc/gemma-4-31b": "den-pc"}
        this = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all&pc=this").json()["models"]
        assert [m["model"] for m in this] == ["gemma-4-12b"]
        assert [m["model"] for m in test_client.get("/stats/speeds/by-role?role=Dialogue&range=all&pc=den-pc").json()["models"]] == ["den-pc/gemma-4-31b"]
        cloud = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all&pc=cloud").json()["models"]
        assert sorted(m["model"] for m in cloud) == ["claude-sonnet-5-5", "glm-4.6"]

    def test_share_is_of_what_is_shown(self, history, test_client):
        cloud = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all&pc=cloud").json()["models"]
        assert sorted(m["share"] for m in cloud) == [50, 50]


class TestHealthCards:
    def test_cards_filter_by_where_and_name_it(self, history, proxy_module, test_client):
        add_pc(proxy_module)
        body = test_client.get("/stats/speeds/health").json()
        assert {c["provider"]: c["where"] for c in body["cards"]} == {"Claude": "Cloud", "GLM": "Cloud", "Local": "This device", "den-pc": "den-pc"}
        assert [c["provider"] for c in test_client.get("/stats/speeds/health?pc=this").json()["cards"]] == ["Local"]
        assert [c["provider"] for c in test_client.get("/stats/speeds/health?pc=den-pc").json()["cards"]] == ["den-pc"]
        assert sorted(c["provider"] for c in test_client.get("/stats/speeds/health?pc=cloud").json()["cards"]) == ["Claude", "GLM"]

    def test_every_ready_service_includes_other_pcs_that_are_up(self, proxy_module, test_client):
        add_pc(proxy_module)
        cards = test_client.get("/stats/speeds/health?show=ready").json()["cards"]
        assert "den-pc" in [c["provider"] for c in cards]
        assert [c["provider"] for c in test_client.get("/stats/speeds/health?show=ready&pc=den-pc").json()["cards"]] == ["den-pc"]


class TestOldHistory:
    def test_rows_saved_before_any_of_this_work_unchanged(self, proxy_module, test_client):
        """History written by an older proxy: no provider on some rows, the bare local name on others."""
        db, now = proxy_module.SPEED_HISTORY_DB, time.time()
        seed(db, "claude-sonnet-5-5", now - 500, provider=None)
        seed(db, "local/gemma-4-12b", now - 400, provider=None)           # a prefix that was kept
        seed(db, "den-pc/gemma-4-31b", now - 300, provider=None)
        add_pc(proxy_module)
        body = test_client.get("/stats/speeds?range=all").json()
        assert {m["model"]: m["where"] for m in body["models"]} == {
            "claude-sonnet-5-5": "Cloud", "local/gemma-4-12b": "This device", "den-pc/gemma-4-31b": "den-pc"}


# ---- the page ----------------------------------------------------------------------------------------------------------
class TestPage:
    def test_the_filter_sits_in_the_main_cards_header_and_starts_hidden(self, test_client):
        html = test_client.get("/").text
        head = html[html.index('<div class="health-head">'):html.index('<div class="health-grid"')]
        assert 'id="speedPcBox" style="display:none">Where' in head and 'id="speedPc"' in head and 'id="healthShow"' in head
        assert head.index('id="speedPc"') < head.index('id="healthShow"')

    def test_every_speeds_fetch_carries_the_filter(self, test_client):
        html = test_client.get("/").text
        for url in ("'/stats/speeds/health?show='", "'/stats/speeds/by-role?role='", "'/stats/speeds?range='"):
            line = [l for l in html.splitlines() if url in l and "fetch(" in l]
            assert line and all("pcParam()" in l for l in line), url

    def test_the_tag_is_on_both_model_views_and_the_choice_is_kept_while_the_page_is_open(self, test_client):
        html = test_client.get("/").text
        assert html.count("${whereTag(m)}") == 2 and "let speedPc = 'all';" in html
        assert "if (!data.show) { speedPc = 'all'; box.style.display = 'none'; return; }" in html
        assert ".where-tag {" in html

    def test_the_filter_is_not_remembered_across_restarts(self, test_client):
        html = test_client.get("/").text
        assert "prefSet('speeds.pc'" not in html and "prefGet('speeds.pc'" not in html
