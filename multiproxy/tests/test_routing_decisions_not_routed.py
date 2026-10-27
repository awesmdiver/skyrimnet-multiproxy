"""2026-10-26: Decisions are answered by the order on the Decisions tab, so Routing lists no Decisions chip, no card for a model only Decisions names, and nothing for it in Needs attention."""
import json
import subprocess

import pytest

import routing
import routing_ui
from tests.test_routing_elsewhere import NODE
from tests.test_routing_gone_and_decisions import HARNESS
from tests.test_routing_tab import _build

JEV = "typesafe/jev-1.13"


def _table(extra_models=()):
    return {"decisions": {"max_tokens": 50, "temperature": 0.1, "timeout": 10, "models": [JEV], "providers": ["skyrimnet-multiproxy"]},
            "dialogue": {"max_tokens": 600, "temperature": 0.9, "timeout": 60, "models": list(extra_models) or ["lmstudio/q"], "providers": ["skyrimnet-multiproxy"]}}


REACH = [{"id": "lmstudio/q", "owned_by": "lmstudio"}]


def test_a_model_only_decisions_uses_has_no_card_and_is_not_in_needs_attention():
    v = _build(table=_table(), reachable=REACH, seen={"decisions": "x", "dialogue": "x"})
    assert not any(a["id"] == JEV for a in v["ais"])
    assert not any(p["id"] == "lost" for p in v["places"])
    d = next(j for j in v["jobs"] if j["key"] == "decisions")
    assert d["onAis"] == [] and d["routable"] is False and next(j for j in v["jobs"] if j["key"] == "dialogue")["routable"] is True


def test_a_model_another_job_also_uses_still_follows_the_normal_rules_for_that_job():
    t = _table(extra_models=[JEV])
    v = _build(table=t, reachable=REACH, seen={"decisions": "x", "dialogue": "x"})
    a = next(a for a in v["ais"] if a["id"] == JEV)
    assert a["place"] == "lost" and any(p["id"] == "lost" for p in v["places"])


def test_decisions_stays_in_priority_when_busy_and_a_decisions_route_puts_no_chip_anywhere():
    v = _build(table=_table(), reachable=REACH, seen={"decisions": "x"},
               settings=routing.clean_settings({"routes": {"decisions": {"ais": ["lmstudio/q"], "how": "order"}}}))
    assert "decisions" in v["priority"]
    assert next(j for j in v["jobs"] if j["key"] == "decisions")["onAis"] == []
    assert next(a for a in v["ais"] if a["id"] == "lmstudio/q")["id"], "the real model keeps its card"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_draws_no_decisions_chip_keeps_the_priority_chip_and_leaves_decisions_out_of_the_job_names(tmp_path):
    script = HARNESS + routing_ui.ROUTING_JS + r"""
rtView = {
  places: [{ id: 'this', name: 'This device', caption: 'c' }],
  ais: [{ id: 'lmstudio/q', name: 'q', kind: 'LM Studio', place: 'this', state: 'on' }],
  jobs: [{ key: 'decisions', label: 'Decisions', about: '', route: null, onAis: [], routable: false, sharesWith: [], how: null, models: ['typesafe/jev-1.13'] },
         { key: 'dialogue', label: 'Dialogue', about: '', route: null, onAis: ['lmstudio/q'], routable: true, sharesWith: [], how: null, models: ['lmstudio/q'] }],
  priority: ['dialogue', 'decisions'], settings: { aging_s: 5 }, hows: [], groups: [], status: {}, skyrimNetLinked: true,
};
rtPrioOpen = true;
console.log(JSON.stringify({ picture: rtPicture(), prio: rtPriorityCard(), use: rtUseCard() }));
"""
    f = tmp_path / "d.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert "Decisions" not in o["picture"] and "Dialogue" in o["picture"]
    assert "2 Decisions" in o["prio"]
    assert "job/decisions" not in o["use"] and "job/dialogue" in o["use"]
