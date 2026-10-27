"""2026-10-26: a job SkyrimNet named job/<name> that nobody has routed yet is not a model that failed. It is not under "Not reachable now"; its card is grey and says
"No model chosen yet. Answered as before."  A real model that nothing answers for says what is missing."""
import json
import subprocess

import pytest

import routing
import routing_ui
import routing_view
from tests.test_routing_elsewhere import NODE
from tests.test_routing_tab import _build
from tests.test_routing_named_exactly import JOBS, table

WORDS = "No model chosen yet. Answered as before."
NO_ANSWER = "Drag it onto a model."
DIRECTORS_SENTENCE = "These jobs have no assigned model. Drag them onto a model to get an answer."


def card(v, ai_id):
    return next(a for a in v["ais"] if a["id"] == ai_id)


def test_an_unrouted_job_name_is_not_under_not_reachable_now_and_is_grey_with_the_new_words():
    v = _build(table=table(named=["alpha_bg"]), seen={}, settings=routing.clean_settings({}))
    a = card(v, "job/alpha_bg")
    assert a["place"] == "unchosen" and a["state"] == "idle" and a["unchosen"] is True and a["words"] == NO_ANSWER and a["kind"] == "" and a["unchosenCase"] == "none"
    assert not any(x["id"] == "job/alpha_bg" for x in v["ais"] if x["place"] == "lost"), "it is not under Not reachable now: nothing is wrong"
    assert {"id": "unchosen", "name": "Jobs with no model yet", "caption": DIRECTORS_SENTENCE} in v["places"]
    assert "job/alpha_bg" not in next(j for j in v["jobs"] if j["key"] == "alpha_bg")["onAis"], "a job's own name is never a row in its Who answers list"


def test_the_two_cases_are_decided_by_the_same_code_that_decides_the_404():
    """Nothing else answers the job (its only model is its own name, no group-mate has a route): the 404 case, "Drag it onto a model."
    Something else does (a group-mate has a route, or SkyrimNet sends real model names too): "No model chosen yet. Answered as before." """
    t = table(named=["alpha_bg"])
    none = card(_build(table=t, seen={}, settings=routing.clean_settings({})), "job/alpha_bg")
    assert none["unchosenCase"] == "none" and none["words"] == NO_ANSWER
    assert routing.named_without_route(routing.clean_settings({}), "alpha_bg", t)[0] is None, "the request path says 404 for exactly this job"
    mate = routing.clean_settings({"routes": {"bravo_bg": {"ais": ["deepseek-chat"], "how": "order"}}})
    v = _build(table=t, seen={}, settings=mate)
    assert card(v, "job/alpha_bg")["unchosenCase"] == "answered" and card(v, "job/alpha_bg")["words"] == WORDS
    assert routing.named_without_route(mate, "alpha_bg", t)[0] is not None
    assert {"id": "unchosen", "name": "Jobs with no model yet", "caption": "answered as before"} in v["places"], "no job here is unanswered: the column carries no sentence about dragging"
    t2 = table(named=["alpha_bg"])
    t2["alpha_bg"]["models"] = ["job/alpha_bg", "deepseek-chat"]
    both = card(_build(table=t2, seen={}, settings=routing.clean_settings({})), "job/alpha_bg")
    assert both["unchosenCase"] == "answered" and routing.named_without_route(routing.clean_settings({}), "alpha_bg", t2)[0] is not None


def test_a_real_model_nothing_answers_for_still_sits_under_not_reachable_now_and_says_what_is_missing():
    t = table()
    t["alpha_bg"]["models"] = ["deepseek-chat"]
    v = _build(table=t, seen={}, settings=routing.clean_settings({"routes": {"alpha_bg": {"ais": ["deepseek-chat"], "how": "order"}}}))
    a = card(v, "deepseek-chat")
    assert a["place"] == "lost" and a["state"] == "warn" and not a.get("unchosen")
    assert a["missing"] and "not set up" not in a["missing"].lower()
    assert any(p["id"] == "lost" for p in v["places"])


def test_the_missing_thing_is_named_by_kind_of_provider():
    f = routing_view._missing
    assert f("x", "DeepSeek", "cloud", "lost", None, None) == "Needs your DeepSeek key."
    assert f("x", "Codex", "subscription", "lost", None, None) == "Sign in to Codex."
    assert f("x", "Ollama", "other", "lost", None, None) == "Ollama isn't running, or doesn't have this model."
    assert f("x", "Local", "local", "lost", None, None) == "Not downloaded on this device."
    assert f("a/b", "not set up", "cloud", "lost", None, None).startswith("No provider here uses this name.")
    assert f("x", "DeepSeek", "cloud", "this", None, None) is None and f("x", "DeepSeek", "cloud", "lost", "OpenRouter", None) is None and f("x", "Claude", "subscription", "lost", None, {"words": "x"}) is None


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_prints_the_new_words_the_missing_thing_and_never_the_bare_not_set_up(tmp_path):
    from tests.test_routing_gone_and_decisions import HARNESS
    script = HARNESS + routing_ui.ROUTING_JS + r"""
const a = { unchosen: true, words: %s, kind: '' };
const b = { kind: 'DeepSeek', missing: 'Needs your DeepSeek key.', state: 'none' };
const c = { kind: 'Claude', state: 'none' };
console.log(JSON.stringify({ a: rtKindLine(a), b: rtKindLine(b), c: rtKindLine(c) }));
""" % json.dumps(WORDS)
    f = tmp_path / "u.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o == {"a": WORDS, "b": "Needs your DeepSeek key.", "c": "Claude"}
    assert "not set up" not in routing_ui.ROUTING_JS


# ---- step 3: is dragging first really needed for job/<name>? (the Option 2 box) ----
def test_a_job_name_needs_a_drag_only_when_nothing_else_can_answer_it():
    t = table(named=["alpha_bg"])
    # the jobs it could not be told apart from have a route: it is answered like them
    route, how = routing.named_without_route(routing.clean_settings({"routes": {"bravo_bg": {"ais": ["deepseek-chat"], "how": "order"}}}), "alpha_bg", t)
    assert how == "group" and route["ais"] == ["deepseek-chat"]
    # SkyrimNet gives the job a real model name of its own: that answers it
    t2 = table()
    t2["alpha_bg"]["models"] = ["gemma-4-12b"]
    t2["alpha_bg"]["max_tokens"] = 9
    route, how = routing.named_without_route(routing.clean_settings({}), "alpha_bg", t2)
    assert how == "model" and route["ais"] == ["gemma-4-12b"]
    # nothing at all (its only model IS the job name and no group-mate has a route): this is the one case that answers "no route yet"
    assert routing.named_without_route(routing.clean_settings({}), "alpha_bg", t) == (None, None)


def test_the_job_is_listed_on_the_tab_by_itself_without_any_drag_so_its_name_can_be_copied():
    v = _build(table=table(named=["alpha_bg"]), seen={}, settings=routing.clean_settings({}))
    j = next(j for j in v["jobs"] if j["key"] == "alpha_bg")
    assert j["route"] is None and j["name"] == "job/alpha_bg" and j["namedExactly"] is True
    unknown = _build(table=table(), seen={}, settings=routing.clean_settings({}))
    assert not any(j["key"] == "never_heard_of_it" for j in unknown["jobs"])
    assert routing.job_from_name("job/never_heard_of_it", table()) is None, "a name MultiProxy has never heard of is not recognised as a job at all"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_picture_draws_the_card_in_its_own_column_with_a_grey_dot_and_no_not_reachable_framing(tmp_path):
    from tests.test_routing_gone_and_decisions import HARNESS
    v = _build(table=table(named=["alpha_bg"]), seen={}, settings=routing.clean_settings({}))
    v["ais"] = [a for a in v["ais"] if a["id"] == "job/alpha_bg"]
    v["places"] = [p for p in v["places"] if p["id"] in ("this", "unchosen")]
    script = HARNESS + routing_ui.ROUTING_JS + "\nrtView = " + json.dumps(v) + ";\nconsole.log(JSON.stringify({ html: rtPicture() }));\n"
    f = tmp_path / "p.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout.strip().splitlines()[-1])["html"]
    assert "Jobs with no model yet" in html and NO_ANSWER in html and DIRECTORS_SENTENCE in html and "op-dot idle" in html
    assert "Not reachable now" not in html and "nothing is answering for these" not in html and "not set up" not in html
