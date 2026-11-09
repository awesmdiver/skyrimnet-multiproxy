"""The automated checks: each one with answers that pass and answers that fail (hand-made), and the summary that says "passed 11 of 12" and keeps the failing line."""
import pytest

import benchmarks_checks as bk
import benchmarks_testset as ts


# --- tags ---
@pytest.mark.parametrize("answer,ok", [
    ("<internal_thought>She is careful.</internal_thought> Well, hello there.", True),
    ("<internal_thought>a</internal_thought> text <internal_thought>b</internal_thought>", False),          # exactly one block
    ("<internal_thought>   </internal_thought> Well, hello there.", False),                                  # something inside it
    ("<internal_thought>She is careful.</internal_thought>", False),                                         # and something said after it
    ("<internal_thought>She is careful.</internal_thought>   \n ", False),
    ("Well, hello. <internal_thought>She is careful.</internal_thought>", False),
    ("Hello there. <internal_thought>no close tag", False),
    ("<internal_thought>a</internal_thought> <internal_thought>b", False),
    ("</internal_thought> backwards <internal_thought>", False),
    ("< internal_thought >a</ internal_thought >", False),
    ("[internal_thought]a[/internal_thought]", False),
    ("Just talking, no thought at all.", False),
])
def test_tags_xml_style(answer, ok):
    assert bk.check_tags(["<internal_thought>"], answer)[0] is ok


def test_tags_voice_tag_has_to_be_there_exactly():
    req = ["<internal_thought>", "[sighs]"]
    assert bk.check_tags(req, "<internal_thought>x</internal_thought> Oh. [sighs] Fine.")[0] is True
    assert bk.check_tags(req, "<internal_thought>x</internal_thought> Oh. [Sighs] Fine.")[0] is False
    assert bk.check_tags(req, "<internal_thought>x</internal_thought> Oh. [sighs] Fine. [sighs]")[0] is False
    assert "exactly once" in bk.check_tags(req, "<internal_thought>x</internal_thought> [sighs] [sighs]")[1]
    assert bk.check_tags(req, "<internal_thought>x</internal_thought> Oh. *sighs* Fine.")[0] is False
    assert "[sighs] is missing" in bk.check_tags(req, "<internal_thought>x</internal_thought> Oh.")[1]


# --- format ---
JSON_SPEC = {"kind": "json", "keys": ["mood", "intent", "line"]}


@pytest.mark.parametrize("answer,ok", [
    ('{"mood": "calm", "intent": "greet", "line": "Hello."}', True),
    ('  \n{"mood": "calm", "intent": "greet", "line": "Hello."}\n', True),
    ('{"mood": "calm", "intent": "greet", "line": "Hello.", "extra": 1}', False),                          # exactly these keys
    ('{"mood": "calm", "intent": "greet", "line": ""}', False),                                           # each a non-empty string
    ('{"mood": "calm", "intent": "  ", "line": "Hello."}', False),
    ('{"mood": 3, "intent": "greet", "line": "Hello."}', False),
    ('{"mood": "calm", "intent": null, "line": "Hello."}', False),
    ('```json\n{"mood": "calm", "intent": "greet", "line": "Hello."}\n```', False),
    ('Here you go: {"mood": "calm", "intent": "greet", "line": "Hello."}', False),
    ('{"mood": "calm", "line": "Hello."}', False),
    ('["calm", "greet", "Hello."]', False),
    ("not json at all", False),
    ("", False),
])
def test_format_json(answer, ok):
    assert bk.check_format(JSON_SPEC, answer)[0] is ok


@pytest.mark.parametrize("answer,ok", [
    ("1. Open the gate.\n2. Walk the road.\n3. Rest at the inn.", True),
    ("1) Open the gate.\n2) Walk the road.\n3) Rest at the inn.", False),                                   # the asked "1." not "1)"
    ("1.\n2. Walk the road.\n3. Rest at the inn.", False),                                                   # text after each number
    ("1. Open the gate.\n2. Walk the road.\n3.", False),
    ("\n1. One.\n\n2. Two.\n3. Three.\n", True),
    ("1. One.\n2. Two.", False),
    ("1. One.\n2. Two.\n3. Three.\n4. Four.", False),
    ("Sure!\n1. One.\n2. Two.\n3. Three.", False),
    ("1. One.\n3. Two.\n2. Three.", False),
    ("- One\n- Two\n- Three", False),
])
def test_format_numbered(answer, ok):
    assert bk.check_format({"kind": "numbered", "count": 3}, answer)[0] is ok


def test_format_labelled():
    spec = {"kind": "labelled", "labels": ["Name", "Mood"]}
    assert bk.check_format(spec, "Name: Orla\nMood: calm")[0] is True
    assert bk.check_format(spec, "Mood: calm\nName: Orla")[0] is False
    assert bk.check_format(spec, "Name: Orla")[0] is False
    assert bk.check_format({"kind": "mystery"}, "x")[0] is False


# --- language (the disclosed word-list rule) ---
EN = "I would not worry about the road, because the river is low and we can cross it before the night falls."
ES = "No me preocuparía por el camino, porque el río está bajo y podemos cruzarlo antes de que caiga la noche."
FR = "Je ne m'inquiéterais pas pour la route, parce que la rivière est basse et que nous pouvons la traverser avant la nuit."
DE = "Ich würde mir um den Weg keine Sorgen machen, denn der Fluss ist niedrig und wir können ihn vor der Nacht überqueren."


@pytest.mark.parametrize("expected,answer,ok", [
    ("en", EN, True), ("es", ES, True), ("fr", FR, True), ("de", DE, True),
    ("es", EN, False), ("en", ES, False), ("fr", DE, False), ("de", FR, False),
    ("en", "Yes, sure.", False),                     # too short to tell
    ("en", "", False),
    ("en", "Orla Venn, Tobren Hask and Maelis Quill, Corvin Ashby, Lisbet Vane, Garrick Moll.", False),     # names: no common words, not English by the rule
])
def test_language_rule(expected, answer, ok):
    assert bk.check_language(expected, answer)[0] is ok


def test_language_without_a_list_is_not_judged():
    assert bk.check_language("ja", "こんにちは") == (True, "")
    assert bk.language_scores("") == {}


# --- length ---
def test_length_is_the_finish_reason():
    assert bk.check_length("stop")[0] is True
    assert bk.check_length("length") == (False, "it ran into the output limit")
    assert bk.check_length("")[0] is False and bk.check_length(None)[0] is False


# --- choice ---
OPTS = ["accept", "refuse", "haggle"]


@pytest.mark.parametrize("answer,ok", [
    ("accept", True), ("Accept.", True), ("  refuse \n", True), ("HAGGLE", True), ("**haggle**", False), ('"accept"', False), ("haggle\nBecause the price is high.", False),
    ("accept..", False), ("accept!", False), ("accept\n", True), ("\naccept\nrefuse", False),
    ("I would accept", False), ("accept or refuse", False), ("maybe", False), ("", False), ("1", False),
])
def test_choice(answer, ok):
    assert bk.check_choice(OPTS, answer)[0] is ok


# --- which checks a prompt asks for ---
def test_evaluate_asks_only_the_checks_a_prompt_names():
    p = {"checks": {"tags": ["<internal_thought>"], "language": "en"}}
    out = bk.evaluate(p, "<internal_thought>x</internal_thought> " + EN, "stop")
    assert set(out) == {"tags", "language", "length"} and all(v[0] for v in out.values())
    assert set(bk.evaluate({"checks": {}}, "anything", "length")) == {"length"}
    assert bk.evaluate({"checks": {"choice": OPTS}}, "accept", "stop")["choice"][0] is True


# --- the summary: "passed 11 of 12", the failing request and the line the model wrote ---
def test_summary_counts_and_keeps_the_failing_line():
    prompts = {"a": {"checks": {"format": JSON_SPEC}}, "b": {"checks": {"tags": ["<internal_thought>"]}}, "c": {"checks": {}}}
    rows = [{"request_id": "r1", "prompt_id": "a", "status": "ok", "finish_reason": "stop", "answer": '{"mood": "x", "intent": "y", "line": "z"}'},
            {"request_id": "r2", "prompt_id": "a", "status": "ok", "finish_reason": "length", "answer": "Here is a very long answer " + "x" * 400},
            {"request_id": "r3", "prompt_id": "b", "status": "ok", "finish_reason": "stop", "answer": "no tag here"},
            {"request_id": "r4", "prompt_id": "c", "status": "ok", "finish_reason": "stop", "answer": "fine"},
            {"request_id": "w1", "prompt_id": "a", "status": "ok", "warmup": True, "finish_reason": "length", "answer": "warm-up: not counted"},
            {"request_id": "s1", "prompt_id": "a", "status": "ok", "sustained": True, "finish_reason": "length", "answer": "sustained: not counted"},
            {"request_id": "e1", "prompt_id": "a", "status": "error", "answer": ""},
            {"request_id": "u1", "prompt_id": "zzz", "status": "ok", "finish_reason": "stop", "answer": "unknown prompt"}]
    checks = {c["id"]: c for c in bk.summarize(rows, prompts)}
    assert list(checks) == ["tags", "format", "length"]                                                                    # fixed order, only checks that applied
    assert (checks["format"]["passed"], checks["format"]["total"]) == (1, 2) and checks["format"]["title"] == "Answers in the format asked"
    assert (checks["length"]["passed"], checks["length"]["total"]) == (3, 4)
    assert (checks["tags"]["passed"], checks["tags"]["total"]) == (0, 1)
    fail = checks["format"]["failures"][0]
    assert fail["request_id"] == "r2" and fail["prompt_id"] == "a" and fail["line"].startswith("Here is a very long answer") and len(fail["line"]) == bk.LINE_CHARS
    assert checks["length"]["failures"][0]["why"] == "it ran into the output limit"
    assert bk.summarize([], prompts) == []


def test_the_real_set_works_with_the_checks_end_to_end():
    data = ts.load()
    by = {p["id"]: p for p in data["prompts"]}
    good = {"dialogue": "<internal_thought>Hmm.</internal_thought> " + EN, "roleplay": "<internal_thought>Hmm.</internal_thought> Oh. [sighs] " + EN,
            "structured": '{"mood": "calm", "intent": "greet", "line": "Hello there."}', "instruction": "1. Go to the gate before dark.\n2. Wait for the ferry.\n3. Rest at the inn.", "choice": "accept"}
    for pid, p in by.items():
        if p["category"] in good:
            out = bk.evaluate(p, good[p["category"]], "stop")
            assert all(ok for ok, _ in out.values()), (pid, out)
    assert all(ok for ok, _ in bk.evaluate(by["edge-short"], ES, "stop").values()) and all(ok for ok, _ in bk.evaluate(by["edge-medium"], FR, "stop").values())
    assert all(ok for ok, _ in bk.evaluate(by["edge-long"], DE, "stop").values())
    assert not bk.evaluate(by["edge-short"], EN, "stop")["language"][0]


def test_the_language_rule_publishes_its_word_lists_and_thresholds_as_constants():
    assert bk.LANGUAGE_MIN_WORDS == 5 and bk.LANGUAGE_MIN_SHARE == 0.08
    assert set(bk.STOPWORDS) == {"en", "es", "fr", "de"} and all(len(w) >= 25 for w in bk.STOPWORDS.values())
    assert bk.check_language("en", "the dog is in the house and it is not for you")[0] is True                          # the thresholds are the ones in use
    assert bk.check_language("en", "the dog is")[0] is False                                                            # fewer than LANGUAGE_MIN_WORDS words
