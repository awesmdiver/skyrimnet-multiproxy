"""Security review 4.1.0 fixes (M1, S1, S5, S7). Stand-in keys only; nothing here touches a real settings folder."""
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import local_guard
import setup_file

ROOT = Path(__file__).resolve().parent.parent
STAND_IN = "Zz-stand-in-key-for-the-review-12345"


# ---- M1: a key typed into "Add your own server" never reaches the log ----------------------------------------------------------------------------------

def test_a_failing_add_own_server_leaves_the_key_out_of_the_log(proxy_module, test_client, caplog):
    with caplog.at_level(logging.DEBUG):
        r = test_client.post("/config/other-pcs/servers/add-own", json={"address": "not an address", "nickname": "x", "key": STAND_IN})
    assert STAND_IN not in r.text
    assert STAND_IN not in caplog.text and STAND_IN not in "\n".join(rec.getMessage() for rec in caplog.records)


def test_a_json_field_called_key_is_masked_in_any_logged_text(proxy_module):
    assert STAND_IN not in proxy_module._redact_text(json.dumps({"address": "a", "key": STAND_IN}))


@pytest.mark.parametrize("path", [
    "/config/other-pcs/servers/add-own", "/config/other-pcs/servers/add-multiproxy", "/config/other-pcs/servers/remove",
    "/config/other-pcs/courier/set-code", "/config/glm-key", "/config/nano-key", "/config/perplexity-key", "/config/openai-key", "/config/deepseek-key",
    "/config/codex-providers", "/config/codex-providers/anything",
])
def test_every_route_that_carries_a_key_or_a_pairing_code_has_its_body_hidden(proxy_module, path):
    assert proxy_module._body_is_hidden(path)


def test_the_claude_sign_in_routes_have_their_body_hidden(proxy_module):
    import claude_wiring
    assert proxy_module._body_is_hidden(claude_wiring.SIGN_IN_PATH) and proxy_module._body_is_hidden(claude_wiring.SIGN_IN_CODE_PATH)


def test_every_provider_key_route_registered_has_its_body_hidden(proxy_module):
    posts = [r.path for r in proxy_module.app.routes if "POST" in getattr(r, "methods", ()) and r.path.startswith("/config/") and r.path.endswith("-key")]
    assert posts and all(proxy_module._body_is_hidden(p) for p in posts)


# ---- S1: Load a setup keeps only the routing block aside -------------------------------------------------------------------------------------------------

def test_the_backup_holds_the_routing_block_and_no_key(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"routing": {"routes": {"dialogue": {"ais": ["a"]}}}, "glm_api_key": STAND_IN}), encoding="utf-8")
    assert setup_file.backup_file(str(cfg), str(tmp_path))
    saved = Path(setup_file.backup_path(str(tmp_path))).read_text(encoding="utf-8")
    assert STAND_IN not in saved and json.loads(saved) == {"routing": {"routes": {"dialogue": {"ais": ["a"]}}}}


def test_undo_puts_back_only_the_routing_block_and_leaves_later_changes_alone(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"routing": {"routes": {"old": 1}}, "keep": 1}), encoding="utf-8")
    setup_file.backup_file(str(cfg), str(tmp_path))
    cfg.write_text(json.dumps({"routing": {"routes": {"new": 2}}, "keep": 1, "added_later": True}), encoding="utf-8")
    assert setup_file.restore_backup(str(cfg), str(tmp_path))
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"routing": {"routes": {"old": 1}}, "keep": 1, "added_later": True}
    assert not setup_file.can_undo(str(tmp_path))


def test_undo_of_a_pc_that_had_no_routing_removes_the_loaded_one(tmp_path):
    cfg = tmp_path / "config.json"
    setup_file.backup_file(str(cfg), str(tmp_path))
    cfg.write_text(json.dumps({"routing": {"routes": {"x": 1}}, "other": 1}), encoding="utf-8")
    assert setup_file.restore_backup(str(cfg), str(tmp_path))
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"other": 1}


def test_an_older_backup_that_holds_the_whole_file_restores_its_routing_only(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"routing": {"routes": {"new": 2}}, "glm_api_key": "current"}), encoding="utf-8")
    old = Path(setup_file.backup_path(str(tmp_path)))
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"routing": {"routes": {"old": 1}}, "glm_api_key": "stale"}), encoding="utf-8")
    assert setup_file.restore_backup(str(cfg), str(tmp_path))
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"routing": {"routes": {"old": 1}}, "glm_api_key": "current"}


def test_backups_are_ignored_by_git_and_never_staged_in_an_installer():
    assert "backups/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    spec = importlib.util.spec_from_file_location("installer_build", ROOT / "installer" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("installer_build", mod)
    spec.loader.exec_module(mod)
    assert setup_file.BACKUP_NAME in mod.NEVER


# ---- S5: the public-tree scrub ----------------------------------------------------------------------------------------------------------------------------

def _scrub_module():
    path = ROOT / "scripts" / "build_public_tree.py"
    if not path.exists():
        pytest.skip("the public tree does not carry the build script")
    spec = importlib.util.spec_from_file_location("build_public_tree_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_scrub_lets_the_perplexity_model_name_through_and_still_stops_a_key():
    m = _scrub_module()
    cred = m.credential_regex()
    ok = {"ai_costs.py": b'    "pplx-decider-flash-latest": {"prompt": 0.01},\n'}
    assert m.scrub(ok, cred) == []
    bad = {"x.py": b'KEY = "pplx-' + b"Qq7Vb2Lm9Xe4Rt8Yu3Ko6Pz" + b'"\n'}
    assert m.scrub(bad, cred), "a real-looking pplx- key still stops the script"
    assert m.scrub({"x.py": b'KEY = "sk-' + b"Qq7Vb2Lm9Xe4Rt8Yu3Ko6Pz1Aa5" + b'"\n'}, cred)


def test_the_real_tree_passes_the_key_rule_on_ai_costs():
    m = _scrub_module()
    cred = m.credential_regex()
    assert m.scrub({"ai_costs.py": (ROOT / "ai_costs.py").read_bytes()}, cred) == []


# ---- S7: check-everything needs the header ----------------------------------------------------------------------------------------------------------------

def test_check_everything_is_a_guarded_dashboard_route(proxy_module):
    local_guard.ENABLED = True
    c = TestClient(proxy_module.app, base_url="http://127.0.0.1:8000")
    path = "/config/check-everything"
    from tests.route_paths import route_paths
    assert path in route_paths(proxy_module.app)                         # (the POST-only answers just below)
    assert c.post(path).status_code == 403
    assert c.post(path, headers={"X-MultiProxy": "0"}).status_code == 403
    assert c.post(path, headers={"X-MultiProxy": "1"}).status_code == 200
    assert "/stats/check-everything" not in route_paths(proxy_module.app)


def test_the_page_calls_the_new_address():
    ui = (ROOT / "overview_ui.py").read_text(encoding="utf-8")
    assert "/config/check-everything" in ui and "/stats/check-everything" not in ui


def test_the_scrub_does_not_read_a_menu_item_called_key_as_a_secret_but_still_reads_api_key_fields():
    m = _scrub_module()
    cred = m.credential_regex()
    assert m.scrub({"courier_ui.py": b'items = [{"key": "version", "text": "x"}]\n'}, cred) == []
    assert m.scrub({"x.py": b'cfg = {"api_key": "' + b"Qq7Vb2Lm9Xe4Rt8Yu3Ko6Pz" + b'"}\n'}, cred)
