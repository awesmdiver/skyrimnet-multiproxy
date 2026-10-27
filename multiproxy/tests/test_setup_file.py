"""Share a setup as a file (2026-10-25): Save my setup / Load a setup. The file never holds a key, an address or a path; a newer or invalid file is refused; a line this PC cannot use is
skipped (never downloaded); the settings are backed up before a load and Undo puts them back."""
import json
import os

import httpx
import pytest
import pytest_asyncio

import routing
import setup_file

FAKE_KEY = "sk-ant-api03-ABCDEF1234567890abcdef"
FAKE_URL = "https://evil.example.com/v1"
FAKE_PATH = "C:\\Users\\someone\\models\\secret.gguf"


def _block():
    return routing.clean_settings({
        "routes": {
            "dialogue": {"ais": ["local/gemma-4-12b", "deepseek/deepseek-flash"], "how": "order", "slow_limit_s": 8},
            "memory": {"ais": ["den-pc/gemma-4-31b"], "how": "order"},
            "decisions": {"ais": ["claude-sonnet-5-5", "den-pc/qwen", FAKE_URL], "how": "turns"},
            "meta": {"ais": [FAKE_PATH, "https://10.0.0.5:8000/v1"], "how": "order"},
        },
        "overrides": {"dialogue": {"temperature": 0.7, "thinking": "off"}},
        "tested": {"memory": {"thinking_off": True, "answer_format": False}},
        "keep_loaded_off": ["local/x"], "split_copied": ["dialogue"],
    })


def _portable(ai):
    return not any(p.strip().startswith("den-pc/") for p in ai.split(","))


def _doc():
    return setup_file.build(_block(), _portable)


# ---- saving ---------------------------------------------------------------------------------------------------------------------------------------------

def test_the_file_holds_the_jobs_the_backups_and_the_speed_and_thinking_settings():
    d = _doc()
    assert d["kind"] == "multiproxy-setup" and d["format"] == setup_file.FORMAT_VERSION
    assert d["jobs"]["dialogue"]["route"] == {"how": "order", "ais": ["local/gemma-4-12b", "deepseek/deepseek-flash"], "slow_limit_s": 8}
    assert d["jobs"]["dialogue"]["fine_tune"] == {"temperature": 0.7, "thinking": "off"}
    assert d["jobs"]["memory"]["tested"] == {"thinking_off": True, "answer_format": False}
    assert d["jobs"]["decisions"]["route"]["ais"] == ["claude-sonnet-5-5"] and d["jobs"]["decisions"]["route"]["how"] == "turns"


def test_another_pc_or_a_custom_address_is_the_job_name_only_marked_needs_setting_up():
    d = _doc()
    assert d["jobs"]["memory"]["route"] == {"how": "order", "needs_setting_up_here": True}
    assert d["jobs"]["decisions"]["route"]["needs_setting_up_here"] is True
    assert d["jobs"]["meta"]["route"] == {"how": "order", "needs_setting_up_here": True}


def test_no_key_address_or_path_ever_reaches_the_written_file():
    block = _block()
    block["routes"]["dialogue"]["ais"] += [FAKE_KEY, "http://192.168.1.9:8000", "localhost:8000/x", "/home/me/model.gguf", "~/m", "..\\x", "my.server.local/m"]
    block["x_secret"] = FAKE_KEY                                    # something that is not a known field is simply never read
    block["keys"] = {"openai": FAKE_KEY}
    text = setup_file.dumps(setup_file.build(block, _portable))
    for bad in (FAKE_KEY, "evil.example.com", "https", "://", "192.168", "localhost", "someone", "secret.gguf", "C:", "\\\\", "/home", "~", "10.0.0.5", ".local", "den-pc"):
        assert bad not in text, bad
    assert "local/gemma-4-12b" in text, "a normal model name still goes in"


def test_the_last_scan_refuses_a_file_with_an_address_in_it():
    with pytest.raises(setup_file.SetupError):
        setup_file.dumps({"kind": "multiproxy-setup", "format": 1, "jobs": {"dialogue": {"route": {"ais": [FAKE_URL]}}}})
    with pytest.raises(setup_file.SetupError):
        setup_file.assert_clean(json.dumps({"a": {"b": [FAKE_KEY]}}))


def test_the_suggested_name_is_multiproxy_date_mpsetup():
    import time
    assert setup_file.suggested_name(time.strptime("2026-10-25", "%Y-%m-%d")) == "multiproxy_2026-10-25.mpsetup"


# ---- loading --------------------------------------------------------------------------------------------------------------------------------------------

def _plan(doc, current=None, have=lambda ai: "", names=lambda ai: ai.split("/")[-1]):
    return setup_file.plan(doc, current or routing.clean_settings({}), have, names, lambda k: k.capitalize())


def test_save_then_load_round_trip_on_a_blank_pc():
    doc = setup_file.parse(setup_file.dumps(_doc()))
    p = _plan(doc)
    texts = [ln["text"] for ln in p["lines"] if not ln["skip"]]
    assert "Dialogue will use gemma-4-12b, then deepseek-flash" in texts
    assert "Dialogue: Temperature 0.7, Thinking off" in texts
    assert 'Memory: "Thinking off" is on, "Ask for a fixed answer format" is off' in texts
    out = setup_file.apply(p, routing.clean_settings({}))
    assert out["routes"]["dialogue"] == {"ais": ["local/gemma-4-12b", "deepseek/deepseek-flash"], "how": "order", "slow_limit_s": 8.0}
    assert out["overrides"] == {"dialogue": {"temperature": 0.7, "thinking": "off"}}
    assert out["tested"]["memory"] == {"thinking_off": True, "answer_format": False}
    assert "memory" not in out["routes"], "a route that was set up on another PC is not invented here"


def test_a_newer_file_is_refused_with_one_plain_line():
    with pytest.raises(setup_file.SetupError) as e:
        setup_file.parse(json.dumps({"kind": "multiproxy-setup", "format": setup_file.FORMAT_VERSION + 1, "jobs": {}}))
    assert str(e.value) == "This file is from a newer version of MultiProxy. Update MultiProxy to load it."


@pytest.mark.parametrize("text", ["", "not json", "[]", "{}", '{"kind": "other", "format": 1, "jobs": {}}', '{"kind": "multiproxy-setup", "jobs": {}}',
                                  '{"kind": "multiproxy-setup", "format": "1", "jobs": {}}', '{"kind": "multiproxy-setup", "format": 1, "jobs": []}', pytest.param("x" * (setup_file.MAX_BYTES + 1), id="too-big")])
def test_anything_that_is_not_a_setup_is_refused(text):
    with pytest.raises(setup_file.SetupError) as e:
        setup_file.parse(text)
    assert str(e.value) == setup_file.NOT_A_SETUP


def test_a_hand_edited_file_cannot_bring_an_address_or_a_key_in():
    text = json.dumps({"kind": "multiproxy-setup", "format": 1, "jobs": {"dialogue": {"route": {"ais": ["claude-sonnet-5-5", FAKE_URL, FAKE_KEY, FAKE_PATH]}}, "bad key!": {"route": {"ais": ["x"]}}}})
    doc = setup_file.parse(text)
    assert doc["jobs"]["dialogue"]["route"]["ais"] == ["claude-sonnet-5-5"] and doc["jobs"]["dialogue"]["needs_setup"] is True and list(doc["jobs"]) == ["dialogue"]


def test_a_model_not_installed_is_skipped_by_itself_and_nothing_is_downloaded():
    doc = setup_file.parse(setup_file.dumps(_doc()))
    have = lambda ai: "You do not have this model." if ai.startswith("local/") else ""
    p = _plan(doc, have=have)
    by = {ln["text"]: ln for ln in p["lines"]}
    line = by["Dialogue will use deepseek-flash"]
    assert not line["skip"] and "gemma-4-12b: You do not have this model." in line["warn"], "the other model still goes in, and the missing one is named"
    out = setup_file.apply(p, routing.clean_settings({}))
    assert out["routes"]["dialogue"]["ais"] == ["deepseek/deepseek-flash"]

    p2 = _plan(doc, have=lambda ai: "You do not have this model.")
    skipped = [ln for ln in p2["lines"] if ln["skip"]]
    assert skipped and all("You do not have this model." in ln["skip"] for ln in skipped if ln["job"] == "dialogue")
    out2 = setup_file.apply(p2, routing.clean_settings({}))
    assert "dialogue" not in out2["routes"], "a skipped line changes nothing"
    assert p2["count"] == sum(1 for ln in p2["lines"] if not ln["skip"])


def test_a_line_that_changes_nothing_is_left_out_and_other_jobs_are_untouched():
    doc = setup_file.parse(setup_file.dumps(_doc()))
    first = setup_file.apply(_plan(doc), routing.clean_settings({"routes": {"vision": {"ais": ["claude-sonnet-5-5"]}}}))
    again = _plan(doc, current=first)
    assert again["lines"] == [] or all(ln["part"] != "route" or ln["job"] != "dialogue" for ln in again["lines"])
    assert first["routes"]["vision"]["ais"] == ["claude-sonnet-5-5"]


# ---- the backup and the undo ------------------------------------------------------------------------------------------------------------------------------

def test_backup_then_undo_puts_the_old_settings_back(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"routing": {"routes": {}}, "a": 1}', encoding="utf-8")
    assert not setup_file.can_undo(str(tmp_path))
    assert setup_file.backup_file(str(cfg), str(tmp_path)) and setup_file.can_undo(str(tmp_path))
    cfg.write_text('{"changed": true}', encoding="utf-8")
    assert setup_file.restore_backup(str(cfg), str(tmp_path))
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"routing": {"routes": {}}, "changed": True}      # (only the routing block goes back)
    assert not setup_file.can_undo(str(tmp_path)) and not setup_file.restore_backup(str(cfg), str(tmp_path))


def test_a_pc_with_no_settings_file_yet_can_still_undo_back_to_empty(tmp_path):
    cfg = tmp_path / "config.json"
    setup_file.backup_file(str(cfg), str(tmp_path))
    cfg.write_text('{"x": 1}', encoding="utf-8")
    assert setup_file.restore_backup(str(cfg), str(tmp_path)) and json.loads(cfg.read_text(encoding="utf-8")) == {"x": 1}


# ---- the routes -----------------------------------------------------------------------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(proxy_module, monkeypatch):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)
    c.pm = proxy_module

    async def models():
        return {"data": [{"id": "local/gemma-4-12b"}, {"id": "deepseek/deepseek-flash"}]}
    monkeypatch.setattr(proxy_module, "list_models", models)
    monkeypatch.setattr(proxy_module, "_refresh_job_names", lambda: None)
    monkeypatch.setattr(type(proxy_module.auth), "is_ready", property(lambda self: False))
    return c


@pytest.mark.asyncio
async def test_save_asks_where_and_the_file_on_disk_has_no_key_address_or_path(client, tmp_path, monkeypatch):
    pm = client.pm
    cfg = pm._load_config()
    cfg["routing"] = {"routes": {"dialogue": {"ais": ["local/gemma-4-12b", FAKE_URL]}}, "overrides": {"dialogue": {"temperature": 0.5}}}
    cfg["openai_api_key"] = FAKE_KEY
    cfg["skyrim_path"] = FAKE_PATH
    pm._save_config(cfg)
    asked = {}
    out = tmp_path / "mine.mpsetup"

    def dialog(title, name, types, ext):
        asked.update(name=name, ext=ext)
        return str(out)
    monkeypatch.setattr(pm.local_models, "save_file_dialog", dialog)
    r = await client.post("/config/setup/save")
    assert r.json() == {"saved": True}, "no 'saved here' echo"
    assert asked["name"].startswith("multiproxy_") and asked["name"].endswith(".mpsetup") and asked["ext"] == ".mpsetup"
    text = out.read_text(encoding="utf-8")
    assert json.loads(text)["jobs"]["dialogue"]["route"]["ais"] == ["local/gemma-4-12b"]
    for bad in (FAKE_KEY, "evil.example", "https", "someone", "secret.gguf", "C:\\"):
        assert bad not in text
    monkeypatch.setattr(pm.local_models, "save_file_dialog", lambda *a: None)
    assert (await client.post("/config/setup/save")).json() == {"saved": False}


@pytest.mark.asyncio
async def test_load_shows_the_list_first_then_backs_up_applies_and_undo_restores(client, tmp_path, monkeypatch):
    pm = client.pm
    before = {"routing": {"routes": {"dialogue": {"ais": ["deepseek/deepseek-flash"], "how": "order"}}}, "keep": 1}
    pm._save_config(before)
    doc = {"kind": "multiproxy-setup", "format": 1, "jobs": {
        "dialogue": {"route": {"ais": ["local/gemma-4-12b"], "how": "order"}},
        "memory": {"route": {"ais": ["local/not-here"], "how": "order"}, "fine_tune": {"temperature": 0.3}},
        "decisions": {"route": {"ais": ["claude-sonnet-5-5"], "how": "order"}}}}
    f = tmp_path / "theirs.mpsetup"
    f.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(pm.local_models, "pick_file_dialog", lambda *a: str(f))

    r = (await client.post("/config/setup/open")).json()
    assert r["file"] == "theirs.mpsetup"
    skipped = {ln["text"]: ln["skip"] for ln in r["lines"] if ln["skip"]}
    assert skipped and any("do not have" in why for why in skipped.values()), "a model not installed is skipped, and says so"
    assert any("not signed in to Claude" in why for why in skipped.values())
    assert r["count"] == 2 and pm._load_config()["routing"]["routes"]["dialogue"]["ais"] == ["deepseek/deepseek-flash"], "nothing changes until the button"
    assert not (await client.get("/config/setup")).json()["canUndo"]

    assert (await client.post("/config/setup/apply", json={"token": "wrong"})).status_code == 400
    assert (await client.post("/config/setup/apply", json={"token": r["token"]})).json() == {"canUndo": True}
    cur = pm._load_config()
    assert cur["routing"]["routes"]["dialogue"]["ais"] == ["local/gemma-4-12b"] and cur["routing"]["overrides"]["memory"] == {"temperature": 0.3}
    assert "memory" not in cur["routing"]["routes"] and cur["keep"] == 1
    assert (await client.get("/config/setup")).json()["canUndo"]

    assert (await client.post("/config/setup/undo")).json() == {"canUndo": False}
    assert pm._load_config() == before
    assert (await client.post("/config/setup/undo")).status_code == 400


@pytest.mark.asyncio
async def test_load_refuses_newer_and_invalid_files_in_plain_words(client, tmp_path, monkeypatch):
    pm = client.pm
    for body, words in ((json.dumps({"kind": "multiproxy-setup", "format": 99, "jobs": {}}), "This file is from a newer version of MultiProxy. Update MultiProxy to load it."),
                        ("hello", "This is not a MultiProxy setup file.")):
        f = tmp_path / "x.mpsetup"
        f.write_text(body, encoding="utf-8")
        monkeypatch.setattr(pm.local_models, "pick_file_dialog", lambda *a, p=str(f): p)
        r = await client.post("/config/setup/open")
        assert r.status_code == 400 and r.json()["error"]["message"] == words
    monkeypatch.setattr(pm.local_models, "pick_file_dialog", lambda *a: None)
    assert (await client.post("/config/setup/open")).json() == {"cancelled": True}


def test_the_settings_page_has_the_two_buttons_and_the_words_exactly():
    import settings_ui
    for words in ("Share your setup", "Save my setup", "Load a setup", "Undo the last load", "Nothing changes until you press Load.",
                  "Saved: which model does which job, backups, speed and thinking settings. Not saved: keys, sign-ins, pairing codes and addresses."):
        assert words in settings_ui.JS, words
    assert 'id="stModalBox"' in settings_ui.PANE_HTML
