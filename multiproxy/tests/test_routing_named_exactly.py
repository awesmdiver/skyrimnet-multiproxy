"""A job SkyrimNet names exactly (job/<name>) leaves its group on the Routing tab and gets its own chip and route (2026-10-24)."""
import json

import pytest

import multiproxy
import routing
import routing_ui
import routing_view
from tests.test_routing_elsewhere import NODE, _page
from tests.test_routing_tab import _build

JOBS = ["alpha_bg", "bravo_bg", "charlie_bg", "delta_bg", "echo_bg", "foxtrot_bg", "golf_bg"]


def table(named=()):
    """Seven add-on jobs with identical settings (so they form one group); the ones in `named` have the model job/<key> in SkyrimNet."""
    return {k: {"max_tokens": 2000, "temperature": 0.5, "timeout": 30, "models": ["job/" + k] if k in named else ["some-model"], "providers": ["skyrimnet-multiproxy"]} for k in JOBS}


def view(named=(), seen=None, settings=None, **kw):
    return _build(table=table(named), seen=seen or {}, settings=routing.clean_settings(settings or {}), **kw)


def job(v, key):
    return next(j for j in v["jobs"] if j["key"] == key)


def test_nobody_named_leaves_the_group_of_seven_as_it_was():
    v = view()
    assert v["groups"] == [sorted(JOBS, key=routing.order_of)] and all(not job(v, k)["namedExactly"] for k in JOBS)
    assert sorted(job(v, "alpha_bg")["sharesWith"]) == sorted(set(JOBS) - {"alpha_bg"})


def test_one_job_named_in_skyrimnet_is_on_its_own_and_the_group_has_six():
    v = view(named=["alpha_bg"])
    assert job(v, "alpha_bg")["sharesWith"] == [] and job(v, "alpha_bg")["namedExactly"] is True
    assert len(v["groups"]) == 1 and "alpha_bg" not in v["groups"][0] and len(v["groups"][0]) == 6
    assert len(job(v, "bravo_bg")["sharesWith"]) == 5 and "alpha_bg" not in job(v, "bravo_bg")["sharesWith"]


def test_two_named_leave_a_group_of_five_and_two_chips_of_their_own():
    v = view(named=["alpha_bg", "golf_bg"])
    assert len(v["groups"][0]) == 5
    assert job(v, "alpha_bg")["sharesWith"] == [] and job(v, "golf_bg")["sharesWith"] == []


def test_one_left_alone_is_no_group_at_all():
    v = view(named=JOBS[:6])
    assert v["groups"] == [] and all(job(v, k)["sharesWith"] == [] for k in JOBS)


@pytest.mark.parametrize("how", ["name", "hint"])
def test_a_job_seen_asked_for_by_name_leaves_its_group_even_without_the_settings_entry(how):
    v = view(seen={"charlie_bg": how})
    assert job(v, "charlie_bg")["sharesWith"] == [] and job(v, "charlie_bg")["namedExactly"] and len(v["groups"][0]) == 6
    assert not job(view(seen={"charlie_bg": "recognised"}), "charlie_bg")["namedExactly"], "only a name or a hint counts"


def test_a_named_job_uses_its_own_route_and_the_group_keeps_its_own():
    own = {"ais": ["x-model"], "how": "order"}
    grp = {"ais": ["y-model"], "how": "order"}
    routes = {"alpha_bg": own, **{k: grp for k in JOBS[1:]}}
    v = view(named=["alpha_bg"], settings={"routes": routes})
    assert job(v, "alpha_bg")["route"]["ais"] == ["x-model"] and job(v, "bravo_bg")["route"]["ais"] == ["y-model"]
    assert routing.route_for(routing.clean_settings({"routes": routes}), ["alpha_bg"])[0]["ais"] == ["x-model"]


def test_hard_groups_and_recognising_leave_the_named_ones_out():
    t = table()
    assert len(routing.hard_groups(t, openings={})[0]) == 7
    assert len(routing.hard_groups(t, openings={}, named={"alpha_bg"})[0]) == 6
    assert routing.exactly_named(table(["alpha_bg"])) == {"alpha_bg"} and routing.exactly_named(t, {"echo_bg": "name", "x": "recognised"}) == {"echo_bg"}
    assert "alpha_bg" not in routing.without_named(t, {"alpha_bg"})


def test_the_split_copies_the_groups_route_once(monkeypatch):
    cfg = {"routing": {"routes": {k: {"ais": ["y-model"], "how": "order"} for k in JOBS[1:]}}}
    monkeypatch.setattr(multiproxy, "_skyrimnet_job_table", lambda: table(["alpha_bg"]))
    monkeypatch.setattr(multiproxy, "_load_config", lambda: cfg)
    monkeypatch.setattr(multiproxy, "_save_config", lambda c: cfg.update(c))
    monkeypatch.setattr(multiproxy, "_seen_how", {}); monkeypatch.setattr(multiproxy, "_named_seen", set())
    multiproxy._copy_split_routes()
    r = cfg["routing"]
    assert r["routes"]["alpha_bg"]["ais"] == ["y-model"] and r["split_copied"] == ["alpha_bg"]
    del r["routes"]["alpha_bg"]                                   # the player clears it: it is not copied again
    multiproxy._copy_split_routes()
    assert "alpha_bg" not in cfg["routing"]["routes"]
    assert routing.clean_settings(cfg["routing"])["split_copied"] == ["alpha_bg"]


def test_a_job_with_a_route_of_its_own_is_not_overwritten_by_the_copy(monkeypatch):
    cfg = {"routing": {"routes": {"alpha_bg": {"ais": ["mine"], "how": "order"}, "bravo_bg": {"ais": ["y-model"], "how": "order"}}}}
    monkeypatch.setattr(multiproxy, "_skyrimnet_job_table", lambda: table(["alpha_bg"]))
    monkeypatch.setattr(multiproxy, "_load_config", lambda: cfg)
    monkeypatch.setattr(multiproxy, "_save_config", lambda c: cfg.update(c))
    monkeypatch.setattr(multiproxy, "_seen_how", {}); monkeypatch.setattr(multiproxy, "_named_seen", set())
    multiproxy._copy_split_routes()
    assert cfg["routing"]["routes"]["alpha_bg"]["ais"] == ["mine"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_gives_the_job_its_own_chip_with_the_note_and_the_group_a_smaller_label(tmp_path):
    v = view(named=["alpha_bg"])
    o = _page(tmp_path, v, "alpha_bg")
    assert o["label"] == "alpha_bg" or "alpha" in o["label"].lower()
    assert "Named exactly in SkyrimNet, so it has its own route." in o["card"] and "Pin this job by name" not in o["card"]
    g = _page(tmp_path, v, "bravo_bg")
    assert g["label"].startswith("6 ") and "Named exactly" not in g["card"] and "Move one job on its own" in g["card"]
    assert "These jobs look the same to MultiProxy, so they move together." in g["card"]
