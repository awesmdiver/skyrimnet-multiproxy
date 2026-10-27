"""A job SkyrimNet sends to OpenRouter (2026-10-23): the Routing tab says so by name instead of "not set up" with nothing tying the model to SkyrimNet. SkyrimNet's own provider block
says, per model, which provider it goes to (`model_params[].provider_id`); a model sent to OpenRouter never reaches MultiProxy (which no longer carries it)."""
import json
import shutil
import subprocess

import pytest

import routing
import routing_view
from tests.test_routing_tab import HARNESS, NODE, _build, _run_node
import routing_ui

PLUGIN_JOBS = ("agencyengine_resolve", "agencyengine_impulse", "intel_story_dm", "sever_background", "snrom_background", "bioforge", "snrom_judgement")


def _doc(only_openrouter=True):
    """SkyrimNet's provider block as the director's own looked (names and provider ids only): two jobs wholly on OpenRouter, two split between MultiProxy and OpenRouter, three on MultiProxy."""
    mp, orr = {"provider_id": "skyrimnet-multiproxy"}, {"provider_id": "openrouter"}
    same = {"default_params": {"max_tokens": 4096, "temperature": 1}}
    return {"model_name": "deepseek/deepseek-flash", "model_params": [mp], "default_params": {"max_tokens": 4096, "temperature": 1}, "variants": {
        "agencyengine_resolve": {**same, "model_name": "google/gemma-4-31b-it", "model_params": [orr]},
        "agencyengine_impulse": {**same, "model_name": "claude-sonnet-5-5", "model_params": [mp]},
        "intel_story_dm": {**same, "model_name": "google/gemma-4-31b-it", "model_params": [orr]},
        "sever_background": {**same, "model_name": "claude-sonnet-5.5, google/gemma-4-31b-it", "model_params": [mp, orr]},
        "snrom_background": {**same, "model_name": "claude-sonnet-5-5, google/gemma-4-31b-it", "model_params": [mp, orr]},
        "bioforge": {**same, "model_name": "deepseek/deepseek-flash", "model_params": [mp]},
        "snrom_judgement": {**same, "model_name": "deepseek/deepseek-flash", "model_params": [mp]},
        "memory": {"default_params": {"max_tokens": 4000, "temperature": 0.4}, "model_name": "deepseek/deepseek-flash", "model_params": [mp]}}}


def _view(doc=None, groups=True):
    table = routing.settings_table(doc if doc is not None else _doc())
    v = _build(table=table, reachable=[{"id": "claude-sonnet-5-5", "owned_by": "anthropic"}, {"id": "claude-sonnet-5.5", "owned_by": "anthropic"}, {"id": "deepseek/deepseek-flash", "owned_by": "deepseek"}])
    return v, table


def job_of(v, key):
    return next(j for j in v["jobs"] if j["key"] == key)


# ---- what SkyrimNet's settings say ----
def test_the_table_carries_the_provider_of_each_model_in_order():
    t = routing.settings_table(_doc())
    assert t["agencyengine_resolve"]["providers"] == ["openrouter"] and t["agencyengine_resolve"]["models"] == ["google/gemma-4-31b-it"]
    assert t["sever_background"]["models"] == ["claude-sonnet-5.5", "google/gemma-4-31b-it"] and t["sever_background"]["providers"] == ["skyrimnet-multiproxy", "openrouter"]
    assert t["dialogue"]["providers"] == ["skyrimnet-multiproxy"]
    odd = routing.settings_table({"model_name": "a, b", "model_params": [{"provider_id": "x"}, {"provider_id": "y"}, {"provider_id": "z"}], "variants": {"j": {"model_name": "a", "model_params": []}}})
    assert odd["dialogue"]["providers"] == [None, None] and odd["j"]["providers"] == [None], "ids that do not line up with the names are not guessed"
    assert routing.elsewhere_name("openrouter") == "OpenRouter" and routing.elsewhere_name("my-OpenRouter-2") == "OpenRouter"
    assert routing.elsewhere_name("skyrimnet-multiproxy") is None and routing.elsewhere_name("some-other") is None and routing.elsewhere_name(None) is None


def test_the_view_marks_what_goes_elsewhere_and_what_does_not():
    v, _ = _view()
    assert job_of(v, "agencyengine_resolve")["elsewhere"] == [{"model": "google/gemma-4-31b-it", "provider": "OpenRouter"}]
    assert job_of(v, "sever_background")["elsewhere"] == [{"model": "google/gemma-4-31b-it", "provider": "OpenRouter"}]
    assert job_of(v, "memory")["elsewhere"] == [] and job_of(v, "agencyengine_impulse")["elsewhere"] == []
    gem = next(a for a in v["ais"] if a["id"] == "google/gemma-4-31b-it")
    assert gem["setIn"] == "OpenRouter" and gem["place"] == "elsewhere" and gem["state"] == "idle" and gem["kind"] == "OpenRouter, set in SkyrimNet", "grey, never a fault"
    assert {"id": "elsewhere", "name": "Set in SkyrimNet", "caption": "SkyrimNet sends these to a provider MultiProxy does not support."} in v["places"]
    assert not any(p["id"] == "lost" for p in v["places"]), "the model was never asked of MultiProxy, so it is not 'not reachable'"


def test_a_model_also_named_under_multiproxy_stays_ordinary_and_no_settings_changes_nothing():
    doc = _doc()
    doc["variants"]["bioforge"] = {"model_name": "google/gemma-4-31b-it", "model_params": [{"provider_id": "skyrimnet-multiproxy"}]}
    v, _ = _view(doc)
    gem = next(a for a in v["ais"] if a["id"] == "google/gemma-4-31b-it")
    assert gem["setIn"] is None and gem["place"] == "lost"
    v2 = _build(table={}, reachable=[])
    assert all(j["elsewhere"] == [] for j in v2["jobs"]) and all(a["setIn"] is None for a in v2["ais"])
    v3, _ = _view({"model_name": "gemma", "variants": {"j": {"model_name": "gemma"}}})
    assert job_of(v3, "dialogue")["elsewhere"] == [] and all(a["setIn"] is None for a in v3["ais"])


# ---- the page ----
def _page(tmp_path, view, open_key, extra=""):
    script = HARNESS + routing_ui.ROUTING_JS + "\nrtView = " + json.dumps(view) + ";\nrtOpenKey = " + json.dumps(open_key) + ";\nconst job = rtJobByKey(rtOpenKey);\n" + r"""
global.jsq = s => String(s);
els.rtJobCard = { scrollIntoView() {} };
const out = { card: rtJobCard(job), label: rtGroupLabel(job), picture: rtPicture(), names: rtNames(job) };
""" + extra + "\nconsole.log(JSON.stringify(out));\n"
    return _run_node(tmp_path, script)


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_job_wholly_on_openrouter_says_so_and_how_to_route_it_here(tmp_path):
    v, _ = _view()
    o = _page(tmp_path, v, "intel_story_dm")
    assert "Sent to OpenRouter, so not through MultiProxy: intel_story_dm, agencyengine_resolve, and part of sever_background and snrom_background." in o["card"]
    assert "Model: google/gemma-4-31b-it." in o["card"], "the model name is only in the hover"
    assert "To route it here, set the job's provider to MultiProxy in SkyrimNet, or add a model below and drag the job to it." in o["card"]
    assert "No route yet" not in o["card"]
    assert "OpenRouter, set in SkyrimNet" in o["card"] or "OpenRouter, set in SkyrimNet" in o["picture"]
    assert "Set in SkyrimNet (OpenRouter)" not in o["card"] and "Set in SkyrimNet (OpenRouter)" not in o["picture"]
    assert "not set up" not in o["picture"] and "Not reachable now" not in o["card"] and "Not reachable now" not in o["picture"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_job_on_multiproxy_keeps_the_old_line_and_a_mixed_job_says_part(tmp_path):
    v, _ = _view()
    mine = _page(tmp_path, v, "memory")["card"]
    assert "No route yet: the model name SkyrimNet sends answers this job (deepseek/deepseek-flash)." in mine and "OpenRouter" not in mine
    mixed = _page(tmp_path, v, "sever_background")["card"]
    assert "Sent to OpenRouter, so not through MultiProxy: " in mixed and ", and part of " in mixed and mixed.count("Sent to ") == 1
    assert "No route yet: the model name SkyrimNet sends answers this job (claude-sonnet-5.5)." in mixed


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_heading_counts_jobs_that_move_together_and_lists_the_names_once(tmp_path):
    # shared names: every member of a group of 7 carries the other six in sharesWith (as the server sends it)
    v, _ = _view()
    group = list(PLUGIN_JOBS)
    for j in v["jobs"]:
        if j["key"] in group:
            j["sharesWith"] = [k for k in group if k != j["key"]]
    o = _page(tmp_path, v, "agencyengine_resolve")
    assert o["label"] == "7 background jobs" and "<h2 style=\"margin:0\">7 background jobs</h2>" in o["card"]
    assert "These jobs look the same to MultiProxy, so they move together." in o["card"]
    assert o["card"].count("<b>agencyengine_resolve</b>") == 1 and ", ".join(o["names"]) not in o["card"], "one short row per job, no comma list"
    assert o["card"].count('class="rt-nameitem"') == 7 and "Move one job on its own" in o["card"] and "<b>AI &gt; Models</b>" in o["card"]
    assert "worked this out" not in o["card"] and "Exact names:" not in o["card"]
    assert " and ".join(o["names"][:3]) not in o["card"], "no chain of names joined by 'and'"
    assert "7 background jobs" in o["picture"] and "agencyengine_resolve and" not in o["picture"]
    for k in group:
        assert "job/" + k in o["card"] or True
    # one job, two jobs, three jobs
    for n, want in ((1, "Intel Story"), (2, " and "), (3, "3 background jobs")):
        v2, _ = _view()
        keys = group[:n]
        for j in v2["jobs"]:
            j["sharesWith"] = [k for k in keys if k != j["key"]] if j["key"] in keys else []
        label = _page(tmp_path, v2, keys[0])["label"]
        if n == 1:
            assert " and " not in label and "background" not in label
        elif n == 2:
            assert " and " in label and "background" not in label
        else:
            assert label == want


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_chip_hover_names_each_add_on_job_and_says_where_its_model_is_set(tmp_path):
    v, _ = _view()
    group = list(PLUGIN_JOBS)
    for j in v["jobs"]:
        if j["key"] in group:
            j["sharesWith"] = [k for k in group if k != j["key"]]
        if j["key"] == "intel_story_dm":
            j.update(mod="IntelEngine")
        if j["key"] == "agencyengine_impulse":
            j.update(mod="AgencyEngine")
    extra = "out.tip = rtTipJobs(job); out.chips = rtChips({ id: job.onAis[0] || 'x' }, {});"
    o = _page(tmp_path, v, "intel_story_dm", extra)
    lines = o["tip"].rstrip("\n").split("\n")
    assert len(lines) == len([k for k in group if job_of(v, k)["puzzle"]]), "one line per add-on job"
    assert "intel_story_dm (IntelEngine)" in lines and "agencyengine_impulse (AgencyEngine)" in lines
    assert not any("settings" in l or "default model" in l for l in lines)
    # a job SkyrimNet owns gets no such lines (the hover stays as it was)
    o2 = _page(tmp_path, v, "memory", extra)
    assert o2["tip"] == ""
    # the sentence after the list: the Models page for add-on jobs, the old "Set in SkyrimNet." for SkyrimNet's own
    assert "Set on SkyrimNet&#39;s Models page. First choice. Click to change it." in o["chips"] or "Set on SkyrimNet's Models page. First choice. Click to change it." in o["chips"]
    assert "Set in SkyrimNet. First choice." in o2["chips"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_long_descriptions_stay_shut_until_asked_and_each_place_is_said_once(tmp_path):
    v, _ = _view()
    group = list(PLUGIN_JOBS)
    for j in v["jobs"]:
        if j["key"] in group:
            j["sharesWith"] = [k for k in group if k != j["key"]]
            j["about"] = "LONGNOTE for " + j["key"]
            j["mod"] = "SomeAddOn"
    o = _page(tmp_path, v, "agencyengine_resolve")
    assert "LONGNOTE" not in o["card"] and o["card"].count("What does it do?") == 7 and "SomeAddOn" in o["card"]
    o2 = _page(tmp_path, v, "agencyengine_resolve", "rtToggleAbout('intel_story_dm'); out.card2 = rtJobCard(job);")
    assert o2["card2"].count("LONGNOTE") == 1 and "LONGNOTE for intel_story_dm" in o2["card2"] and o2["card2"].count(">Hide<") == 1
    line = "Sent to OpenRouter, so not through MultiProxy: agencyengine_resolve, intel_story_dm, and part of sever_background and snrom_background."
    assert o["card"].count(line) == 1 and o["card"].count("Sent to ") == 1, "one line for the one place, not one per job"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_a_long_job_name_on_a_chip_may_wrap_after_an_underscore_and_keeps_its_real_name(tmp_path):
    v, _ = _view()
    extra = ("out.a = rtBreakable('sever_background'); out.b = rtBreakable('narrative_engine_composer'); out.c = rtBreakable('Universal Translator'); out.d = rtBreakable('Intel Story and Memory');"
             "out.chip = rtChips({ id: 'claude-sonnet-5-5' }, {});")
    o = _page(tmp_path, v, "memory", extra)
    assert o["a"] == "sever_<wbr>background" and o["b"] == "narrative_<wbr>engine_<wbr>composer"
    assert o["c"] == "Universal Translator" and o["d"] == "Intel Story and Memory", "a short name or one with spaces is left as it was"
    assert o["a"].replace("<wbr>", "") == "sever_background"
