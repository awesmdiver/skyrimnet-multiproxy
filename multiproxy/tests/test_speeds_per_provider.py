"""The Speeds tab keeps one card per provider and model (2026-10-03): the same model name under two providers is two cards with separate numbers, in the cards,
the role chips, the device filter, the live "today" tracker, the By-role list and the time breakdown; the engine's own memory (first words, costs, resting) is
keyed the same way."""
import sqlite3
import time
from unittest.mock import patch

import speed_queries
import speeds_time

SAME = "grok-4.7"


def _seed(db, provider, ok_count, fail_count=0, first_token=1.0, roles=None, job=None):
    now = time.time()
    with sqlite3.connect(db) as conn:
        for i in range(ok_count + fail_count):
            ok = 1 if i < ok_count else 0
            conn.execute("INSERT INTO speed_history (ts, model, elapsed, success, first_token, provider, roles_json, job) VALUES (?, ?, 2.0, ?, ?, ?, ?, ?)",
                         (now - 60 + i, SAME, ok, first_token if ok else None, provider, roles, job))


def _cards(client, rng="all", pc="all"):
    return {m["provider"]: m for m in client.get(f"/stats/speeds?range={rng}&pc={pc}").json()["models"] if m["model"] == SAME}


def test_the_same_model_under_two_providers_is_two_cards_with_their_own_numbers(proxy_module, test_client):
    db = proxy_module.SPEED_HISTORY_DB
    _seed(db, "Grok", 5)
    _seed(db, "Continuum", 3, 2, first_token=4.0)
    for rng in ("7d", "30d", "all"):
        cards = _cards(test_client, rng)
        assert set(cards) == {"Grok", "Continuum"}, rng
        assert cards["Grok"]["calls_total"] == 5 and cards["Grok"]["success_pct"] == 100 and cards["Grok"]["v2"]["first_token"] == 1.0
        assert cards["Continuum"]["calls_total"] == 5 and cards["Continuum"]["success_pct"] == 60 and cards["Continuum"]["v2"]["first_token"] == 4.0
        assert cards["Grok"]["key"] != cards["Continuum"]["key"]


def test_calls_saved_with_no_provider_stay_on_a_card_of_their_own(proxy_module, test_client):
    db = proxy_module.SPEED_HISTORY_DB
    _seed(db, "Grok", 2)
    _seed(db, None, 7)
    cards = _cards(test_client)
    assert cards["Grok"]["calls_total"] == 2 and cards[None]["calls_total"] == 7, "old rows are never added to a provider's numbers"


def test_the_live_today_tracker_keeps_two_providers_apart(proxy_module, test_client):
    proxy_module.speed_tracker.record(SAME, 1.0, True, "Grok")
    proxy_module.speed_tracker.record(SAME, 9.0, False, "Continuum")
    proxy_module.speed_tracker.record(SAME, 9.0, False, "Continuum")
    cards = _cards(test_client, "today")
    assert cards["Grok"]["success_pct"] == 100 and cards["Grok"]["calls_last_hour"] == 1
    assert cards["Continuum"]["success_pct"] == 0 and cards["Continuum"]["calls_last_hour"] == 2


def test_role_chips_land_on_the_right_provider(proxy_module, test_client, tmp_path):
    config_dir = tmp_path / "Data" / "SKSE" / "Plugins" / "SkyrimNet" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "X.yaml").write_text(f"x:\n  model_name: continuum/{SAME}\n  variants:\n    memory:\n      model_name: grok/{SAME}\n", encoding="utf-8")
    db = proxy_module.SPEED_HISTORY_DB
    _seed(db, "Grok", 1)
    _seed(db, "Continuum", 1)
    with patch.object(proxy_module, "skyrim_install_path", str(tmp_path)):
        cards = _cards(test_client)
    assert [c["label"] for c in cards["Continuum"]["roles"]] == ["Dialogue"]
    assert [c["label"] for c in cards["Grok"]["roles"]] == ["Memory"]


def test_the_pc_filter_follows_each_cards_own_provider(proxy_module, test_client):
    db = proxy_module.SPEED_HISTORY_DB
    _seed(db, "Local", 2)
    _seed(db, "Continuum", 3)
    assert set(_cards(test_client, "all", "this")) == {"Local"}
    assert set(_cards(test_client, "all", "cloud")) == {"Continuum"}
    assert set(_cards(test_client, "all", "all")) == {"Local", "Continuum"}


def test_by_role_lists_each_provider_separately(proxy_module, test_client):
    db = proxy_module.SPEED_HISTORY_DB
    _seed(db, "Grok", 4, roles='["Dialogue"]')
    _seed(db, "Continuum", 1, 1, roles='["Dialogue"]')
    models = test_client.get("/stats/speeds/by-role?role=Dialogue&range=all").json()["models"]
    by = {m["provider"]: m for m in models}
    assert set(by) == {"Grok", "Continuum"} and by["Grok"]["calls"] == 4 and by["Continuum"]["calls"] == 2
    assert by["Continuum"]["success_pct"] == 50 and by["Grok"]["success_pct"] == 100


def test_the_time_breakdown_keeps_a_job_and_model_under_two_providers_apart(proxy_module):
    db = proxy_module.SPEED_HISTORY_DB
    _seed(db, "Grok", 3, job="dialogue")
    _seed(db, "Continuum", 2, job="dialogue")
    out = speed_queries.time_answer(db, None, label_of=lambda j: j, where_of=lambda m, p: "Cloud", keep=lambda w: True, order_of=lambda j: 0)
    by = {g["provider"]: g for g in out["groups"]}
    assert by["Grok"]["calls"] == 3 and by["Continuum"]["calls"] == 2


def test_the_engines_memory_is_keyed_by_provider_and_model(proxy_module):
    a, b = f"continuum/{SAME}", f"grok/{SAME}"
    assert proxy_module._engine_key(a) != proxy_module._engine_key(b)
    assert proxy_module._engine_key(a) == speed_queries.card_key("Continuum", SAME)
    for _ in range(10):
        proxy_module._routing_engine.first_words.add(proxy_module._engine_key(a), 5.0)
    assert proxy_module._routing_engine.first_words.median(proxy_module._engine_key(b)) is None
    assert proxy_module._routing_engine.first_words.median(proxy_module._engine_key(a)) == 5.0
