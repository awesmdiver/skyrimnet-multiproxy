"""Routing: job names and descriptions come from SkyrimNet itself (2026-10-02), not from a list of ours.

A scratch SkyrimNet folder stands in for the real one: its dashboard text table (under any file name hash), its mods' manifests, a new mod appearing, a missing
dashboard, a manifest with no description, a manifest that isn't YAML, and descriptions with quotes and escapes."""
import json
import os
import shutil
import time

import httpx
import pytest
import pytest_asyncio

import job_names
import routing
import routing_view

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
TABLE_JS = open(os.path.join(FIX, "skyrimnet_dashboard_text_table.js"), encoding="utf-8").read()

MANIFEST = """plugin:
  name: IntelEngine
  version: "3.5.1"
  description: "NPC task & story automation engine"
variants:
  - name: intel_story_dm
    description: "Story Dungeon Master decisions — uses base OpenRouter config unless overridden below"
schema:
  fields: []
"""


def make_sn(tmp_path, *, hash_="94e55244", dashboard=True, manifests=None):
    sn = tmp_path / "SkyrimNet"
    (sn / "assets" / "web-new").mkdir(parents=True)
    if dashboard:
        shutil.copy(os.path.join(FIX, "skyrimnet_dashboard_text_table.js"), sn / "assets" / "web-new" / f"main.{hash_}.js")
    for rel, text in (manifests or {}).items():
        f = sn / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return str(sn)


# ---- reading SkyrimNet's text table ----------------------------------------------------------------------------------------------------------------------

def test_the_text_table_is_read_word_for_word_with_its_escapes():
    t = job_names.parse_text_table(TABLE_JS)
    assert t["dialogue"] == {"name": "Dialogue", "about": "Primary model for NPC conversations and any task that doesn't override a variant."}, "the JS \\' became a plain apostrophe"
    assert t["decisions"]["about"].startswith("Typed choice questions (NPC mood, who speaks next, who you are addressing)") and t["decisions"]["about"].endswith("under Conversations.")
    assert t["AgentDefault"]["name"] == "AI Assistant" and t["gamemaster_evaluation"]["name"] == "GameMaster" and len(t) == 12
    assert "other" not in t and "app.title" not in t


def test_quotes_and_escaped_characters_come_out_right():
    js = r'var t={"llm.variant_x":"Say \"hi\"","llm.variant_x_desc":"It\'s a \\ path\nsecond line, café ☺, \x41, tab\there, 🧩 end"};'
    got = job_names.parse_text_table(js)["x"]
    assert got["name"] == 'Say "hi"'
    assert got["about"] == "It's a \\ path\nsecond line, café ☺, A, tab\there, 🧩 end"


def test_the_built_in_copy_is_exactly_skyrimnets_own_twelve():
    t = job_names.parse_text_table(TABLE_JS)
    assert {k: (v["name"], v["about"]) for k, v in t.items()} == {k: tuple(v) for k, v in job_names.BUILTIN.items()}, "regenerate job_names.BUILTIN from the fixture"


# ---- finding the files -------------------------------------------------------------------------------------------------------------------------------

def test_the_dashboard_file_may_have_any_hash_and_either_folder(tmp_path):
    for sub, h in (("web-new", "deadbeef"), ("web", "c0c96ccd")):
        sn = tmp_path / sub
        (sn / "assets" / sub).mkdir(parents=True)
        shutil.copy(os.path.join(FIX, "skyrimnet_dashboard_text_table.js"), sn / "assets" / sub / f"main.{h}.js")
        n = job_names.JobNames()
        n.refresh(str(sn), force=True)
        assert n.from_files and n.info("memory") == {"name": "Memory", "about": "Processes events into long-term NPC memories with importance scoring.", "mod": None, "source": "skyrimnet", "puzzle": False}


def test_an_old_dashboard_without_the_table_is_skipped_for_one_that_has_it(tmp_path):
    sn = make_sn(tmp_path)
    old = os.path.join(sn, "assets", "web")
    os.makedirs(old)
    open(os.path.join(old, "main.c0c96ccd.js"), "w").write("var t={'other':1};")
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    assert n.from_files and n.name("combat") == "Combat"


def test_a_missing_dashboard_falls_back_to_the_built_in_copy_and_the_status_says_so(tmp_path):
    n = job_names.JobNames()
    n.refresh(make_sn(tmp_path, dashboard=False), force=True)
    assert not n.from_files and n.status()["source"] == "builtin" and "built-in copy" in n.status()["text"]
    assert n.info("memory")["source"] == "builtin" and n.name("decisions") == "Decisions"
    assert n.info("intel_story_dm") == {"name": "intel_story_dm", "about": job_names.NO_DESCRIPTION, "mod": None, "source": "raw", "puzzle": True}
    n.refresh(None, force=True)
    assert n.status()["source"] == "builtin", "SkyrimNet not linked at all"
    n.refresh(os.path.join(str(tmp_path), "nowhere"), force=True)
    assert n.name("meta") == "Meta"


def test_a_dashboard_that_cannot_be_read_is_not_fatal(tmp_path, monkeypatch):
    sn = make_sn(tmp_path)
    real_open = open

    def bad(path, *a, **k):
        if str(path).endswith(".js"):
            raise OSError("locked")
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", bad)
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    monkeypatch.undo()
    assert n.status()["source"] == "builtin"


# ---- jobs other mods add -------------------------------------------------------------------------------------------------------------------------------

def test_a_mods_job_is_shown_by_its_raw_name_with_the_description_it_declared_and_which_mod(tmp_path):
    sn = make_sn(tmp_path, manifests={"config/plugins/IntelEngine/manifest.yaml": MANIFEST})
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    i = n.info("intel_story_dm")
    assert i["name"] == "intel_story_dm" and i["mod"] == "IntelEngine" and i["puzzle"] and i["source"] == "mod"
    assert i["about"] == "Story Dungeon Master decisions — uses base OpenRouter config unless overridden below", "the mod's own words, whole"
    assert n.status()["mods"] == ["IntelEngine"]


def test_the_manifests_in_the_external_folder_count_too(tmp_path):
    sn = make_sn(tmp_path, manifests={"external/cleanestpoison.agencyengine/settings/AgencyEngine.yaml": MANIFEST.replace("IntelEngine", "AgencyEngine").replace("intel_story_dm", "agencyengine_impulse")})
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    assert n.info("agencyengine_impulse")["mod"] == "AgencyEngine"


def test_a_variant_with_no_description_and_a_manifest_that_is_not_yaml(tmp_path):
    sn = make_sn(tmp_path, manifests={"config/plugins/Quiet/manifest.yaml": "plugin:\n  name: Quiet\nvariants:\n  - name: quiet_job\n",
                                      "config/plugins/Broken/manifest.yaml": "plugin: [unclosed\n  - : :\n\t\tbad",
                                      "config/plugins/NotAMap/manifest.yaml": "- just\n- a list\n"})
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    q = n.info("quiet_job")
    assert q["mod"] == "Quiet" and q["about"] == "" and q["puzzle"], "declared, but the mod gave no description: none is invented"
    assert n.from_files and n.name("combat") == "Combat", "the broken ones were ignored"


def test_a_new_mods_job_appears_by_itself_when_its_manifest_arrives(tmp_path):
    sn = make_sn(tmp_path)
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    assert n.info("intel_story_dm")["source"] == "raw"
    f = os.path.join(sn, "config", "plugins", "IntelEngine", "manifest.yaml")
    os.makedirs(os.path.dirname(f))
    open(f, "w", encoding="utf-8").write(MANIFEST)
    n.refresh(sn, force=True)
    assert n.info("intel_story_dm")["mod"] == "IntelEngine"
    open(f, "w", encoding="utf-8").write(MANIFEST.replace("Story Dungeon Master", "Changed words"))
    os.utime(f, (time.time() + 5, time.time() + 5))
    n.refresh(sn, force=True)
    assert n.info("intel_story_dm")["about"].startswith("Changed words"), "re-read when the file changes"


def test_files_are_only_checked_every_few_seconds(tmp_path):
    clock = [1000.0]
    sn = make_sn(tmp_path)
    n = job_names.JobNames(clock=lambda: clock[0])
    n.refresh(sn)
    f = os.path.join(sn, "config", "plugins", "X", "manifest.yaml")
    os.makedirs(os.path.dirname(f))
    open(f, "w", encoding="utf-8").write(MANIFEST)
    n.refresh(sn)
    assert n.info("intel_story_dm")["source"] == "raw"
    clock[0] += job_names.RECHECK_S + 1
    n.refresh(sn)
    assert n.info("intel_story_dm")["source"] == "mod"


def test_a_skyrimnet_job_keeps_skyrimnets_words_even_if_a_mod_declares_the_same_key(tmp_path):
    sn = make_sn(tmp_path, manifests={"config/plugins/Evil/manifest.yaml": "plugin:\n  name: Evil\nvariants:\n  - name: memory\n    description: other\n"})
    n = job_names.JobNames()
    n.refresh(sn, force=True)
    assert n.info("memory")["source"] == "skyrimnet" and n.name("memory") == "Memory"


# ---- the one source the rest of MultiProxy uses -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def names(tmp_path, monkeypatch):
    n = job_names.JobNames()
    n.refresh(make_sn(tmp_path, manifests={"config/plugins/NarrativeEngine/manifest.yaml": "plugin:\n  name: NarrativeEngine\nvariants:\n  - name: narrative_engine_director\n    description: Directs.\n  - name: narrative_engine_composer\n    description: Composes.\n"}), force=True)
    monkeypatch.setattr(job_names, "NAMES", n)
    return n


def test_labels_join_with_and_using_the_mirrored_names(names):
    assert routing.label("dialogue") == "Dialogue" and routing.label("narrative_engine_composer") == "narrative_engine_composer"
    assert routing.label_of(("narrative_engine_composer", "narrative_engine_director")) == "narrative_engine_composer and narrative_engine_director"
    assert routing.label_of(("dialogue", "combat")) == "Dialogue and Combat" and routing.about("combat").startswith("Handles NPC dialogue")


def test_no_made_up_name_is_left_in_the_code():
    assert not hasattr(routing, "JOB_LABELS") and not hasattr(routing, "JOB_ABOUT")
    src = open(os.path.join(os.path.dirname(__file__), "..", "routing.py"), encoding="utf-8").read()
    for made_up in ("Companion Impulses", "Event Sorting", "Social and Story Events", "Relationship Changes", "Companion Relationships"):
        assert made_up not in src.split("LEGACY_LABELS")[0], made_up


def test_explicit_names_work_for_any_job_a_mod_adds(names):
    assert routing.job_from_name("job/narrative_engine_director") == "narrative_engine_director" and routing.job_from_name("job/Dialogue") == "dialogue"
    assert routing.job_from_name("job/brand_new_job") is None and routing.job_from_name("job/brand_new_job", known=["brand_new_job"]) == "brand_new_job"
    c = routing.clean_settings({"routes": {"brand_new_job": {"ais": ["a"]}, "bad key!": {"ais": ["a"]}}})
    assert list(c["routes"]) == ["brand_new_job"], "a route for a job a mod added is kept; a nonsense key is not"


def test_the_view_has_the_mirrored_names_the_mod_and_a_puzzle_mark_for_a_mod_job(names):
    t = {"dialogue": {"models": ["a"], "max_tokens": 4096, "temperature": 1}, "narrative_engine_composer": {"models": ["a"], "max_tokens": 8192, "temperature": 0},
         "narrative_engine_director": {"models": ["a"], "max_tokens": 8192, "temperature": 0}, "shiny_new_job": {"models": ["a"], "max_tokens": 1, "temperature": 1}}
    v = routing_view.build(settings=routing.clean_settings({}), table=t, status={"resting": []}, reachable=[{"id": "a", "owned_by": "lmstudio"}], remotes={}, stats={}, seen={},
                           costs=lambda j, k: None, classify=lambda a: ("free", "local"), key_of=lambda a: a, name_of=lambda a: (a, "x"), now_resting={})
    by = {j["key"]: j for j in v["jobs"]}
    assert by["dialogue"]["label"] == "Dialogue" and not by["dialogue"]["puzzle"] and by["dialogue"]["about"].startswith("Primary model")
    d = by["narrative_engine_director"]
    assert d["label"] == "narrative_engine_director" and d["puzzle"] and d["mod"] == "NarrativeEngine" and d["about"] == "Directs."
    s = by["shiny_new_job"]
    assert s["label"] == "shiny_new_job" and s["puzzle"] and s["mod"] is None and s["about"] == job_names.NO_DESCRIPTION, "a job declared nowhere"
    assert v["status"]["names"]["source"] == "files"


# ---- history ---------------------------------------------------------------------------------------------------------------------------------------------

def test_history_stored_under_the_old_labels_is_shown_under_the_current_name(names):
    import multiproxy as m
    roles = json.dumps(["Companion Impulses", "Intel Story Dm", "Dialogue", "Memory"])
    assert m._roles_of(roles) == ["agencyengine_impulse", "intel_story_dm", "Dialogue", "Memory"]
    assert routing.current_role_label("Something Else") == "Something Else"


@pytest_asyncio.fixture
async def client(proxy_module, monkeypatch, tmp_path):
    n = job_names.JobNames()
    sn = make_sn(tmp_path, manifests={"config/plugins/IntelEngine/manifest.yaml": MANIFEST})
    n.refresh(sn, force=True)
    monkeypatch.setattr(job_names, "NAMES", n)
    monkeypatch.setattr(proxy_module, "_refresh_job_names", lambda: None)
    monkeypatch.setattr(proxy_module, "_skyrimnet_job_table", lambda: {"intel_story_dm": {"models": ["lmstudio/x"], "max_tokens": 4096, "temperature": 1}, "dialogue": {"models": ["lmstudio/x"], "max_tokens": 4096, "temperature": 1}})
    monkeypatch.setattr(proxy_module, "_check_against_skyrimnet_log", lambda: None)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


@pytest.mark.asyncio
async def test_the_endpoint_gives_the_mirrored_names(client):
    g = (await client.get("/config/routing")).json()
    by = {j["key"]: j for j in g["jobs"]}
    assert by["intel_story_dm"]["label"] == "intel_story_dm" and by["intel_story_dm"]["mod"] == "IntelEngine" and by["dialogue"]["label"] == "Dialogue"
    assert g["status"]["names"]["source"] == "files"
    page = (await client.get("/")).text
    # what the panel now shows for a job (the job panel cleanup): one short row with the add-on's name in muted text, and the author's own description behind "What does it do?"
    assert by["intel_story_dm"]["about"] == "Story Dungeon Master decisions \u2014 uses base OpenRouter config unless overridden below", "the mod's own words reach the page, whole"
    assert by["dialogue"]["about"].startswith("Primary model") and by["dialogue"]["mod"] in (None, ""), "a SkyrimNet job has its own description and no add-on name"
    start = page.index("'<div class=\"rt-jobrow\">'")
    row = page[start: page.index("What does it do?", start) + 60]
    assert "rt-puzzle" in page and "esc(j.mod)" in row and '<span class="lm-muted">' in row, "the add-on's name sits on the job row in muted text"
    assert "j.about" in row and "What does it do?" in row and "rtToggleAbout" in row, "its description is behind the toggle"
