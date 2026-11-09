"""The Benchmarks and Benchmark test set Help topics (the prompts are read from the test set file), the link on the tab, and Peak VRAM used showing the model's own peak."""
import json
import re
import shutil
import subprocess

import pytest

import benchmarks_results as res
import benchmarks_ui
import help_content


def topic(topics, tid):
    return next(t for t in topics if t["id"] == tid)


def test_both_topics_are_registered_in_tools_with_the_test_set_under_benchmarks():
    ts = help_content.load_topics()
    ids = [t["id"] for t in ts]
    assert ids.index("speeds") < ids.index("benchmarks") < ids.index("benchmark-test-set") < ids.index("network")
    assert topic(ts, "benchmarks")["group"] == topic(ts, "benchmark-test-set")["group"] == topic(ts, "speeds")["group"]
    assert topic(ts, "benchmarks")["tab"] == "benchmarks" and topic(ts, "benchmarks")["parent"] is None and topic(ts, "benchmark-test-set")["parent"] == "benchmarks"


def test_both_topics_are_found_by_search_text_and_no_marker_is_left():
    ts = help_content.load_topics()
    assert "How the test works" in topic(ts, "benchmarks")["text"] and "Dialogue" in topic(ts, "benchmark-test-set")["text"]
    for t in ts:
        assert "{{" not in t["html"] and "{{" not in t["text"]


def test_the_prompts_section_is_six_groups_of_three_taken_from_the_file():
    page = topic(help_content.load_topics(), "benchmark-test-set")["html"]
    ts = json.loads(help_content.TESTSET_PATH.read_text(encoding="utf-8"))
    assert page.count('class="hp-grp2"') == 6 and page.count('class="hp-prow"') == 18 and page.count("3 prompts") == 6
    names = re.findall(r'<details class="hp-grp2"><summary><b>([^<]+)</b>', page)
    assert names == ["Dialogue", "Role-play", "Structured answer", "Follow an instruction", "Other languages", "Make a decision"]
    p = ts["prompts"][0]
    assert f'about {p["approx_tokens"]:,} tokens · reply up to {p["max_tokens"]} tokens' in page
    assert "<details" in page and "<details open" not in page                       # everything is closed until opened
    assert "the reply ended on its own" in page and "as long as the shortest quarter of one player&#x27;s real SkyrimNet requests" in page


def test_changing_a_prompt_in_a_copy_of_the_test_set_changes_the_page(tmp_path):
    ts = json.loads(help_content.TESTSET_PATH.read_text(encoding="utf-8"))
    ts["prompts"][0]["messages"][1]["content"] = "A brand new user message <script>alert(1)</script>"
    f = tmp_path / "ts.json"
    f.write_text(json.dumps(ts), encoding="utf-8")
    page = topic(help_content.load_topics(str(f)), "benchmark-test-set")["html"]
    assert "A brand new user message" in page
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page      # shown as text, never as markup


def test_the_fingerprint_sits_in_a_closed_technical_details_section():
    page = topic(help_content.load_topics(), "benchmark-test-set")["html"]
    ts = json.loads(help_content.TESTSET_PATH.read_text(encoding="utf-8"))
    m = re.search(r'<details class="hp-tech"><summary>Technical details</summary>(.*?)</details>', page, re.S)
    assert m and ts["sha256"] in m.group(1) and "<code>" in m.group(1)
    assert 'hp-tech" open' not in page


def test_an_unreadable_test_set_still_gives_the_page_and_no_marker():
    t = topic(help_content.parse(help_content.CONTENT_PATH.read_text(encoding="utf-8"), {}), "benchmark-test-set")
    assert "{{" not in t["html"] and "couldn't be read" in t["html"]


def test_the_tab_has_the_help_link_and_the_help_list_indents_the_sub_entry():
    assert "openHelp('benchmarks')" in benchmarks_ui.BENCHMARKS_PANE_HTML and "How does this test work?" in benchmarks_ui.BENCHMARKS_PANE_HTML
    import help_ui
    assert "t.parent ? 'sub '" in help_ui.HELP_JS and ".hp-nav a.sub" in help_ui.HELP_CSS and "showMainTab(\\'benchmarks\\')" in help_ui.HELP_JS


# ---- Part 2: Peak VRAM used is the model's own peak ----
def summary(*cards):
    return {"memory": {str(i): {"vram_idle_before_load_mb": b, "vram_highest_overall_mb": h, "model_vram_delta_mb": h - b} for i, (b, h) in enumerate(cards)}}


def test_the_table_figure_is_the_model_delta_not_the_whole_card():
    s = summary((1126, 9830))
    assert res._peak_vram_mb(s) == 8704 and res._card_highest_mb(s) == 9830


def test_with_two_cards_it_is_the_sum_of_each_cards_delta():
    s = summary((1000, 9000), (500, 3500))
    assert res._peak_vram_mb(s) == 8000 + 3000 and res._card_highest_mb(s) == 9000
    assert res._peak_vram_mb({"memory": {}}) is None and res._peak_vram_mb(None) is None


def test_the_header_hover_and_the_detail_words_say_what_it_now_is():
    js = benchmarks_ui.BENCHMARKS_JS
    assert "The most VRAM the model used. This is the card\\'s peak use minus what was used before the model loaded." in js
    assert "Highest on the whole card: " in js and "VRAM in use on the card" in js
    assert "bmChart(BM_WORDS.res.peakVram" not in js


def test_the_checked_lines_match_the_stricter_checks():
    page = topic(help_content.load_topics(), "benchmark-test-set")["html"]
    want = [
        "One &lt;internal_thought&gt; block with something inside it, then spoken text; the reply reads as English; the reply ended on its own.",
        "One &lt;internal_thought&gt; block with something inside it, then spoken text; the exact text [sighs] once; the reply reads as English; the reply ended on its own.",
        "The whole reply is a JSON object with exactly the keys mood, intent and line, each holding some text; the reply ended on its own.",
        "Exactly 3 lines, numbered 1. 2. and 3., with some text after each number; the reply ended on its own.",
        "The reply reads as Spanish (short), French (medium) or German (long); the reply ended on its own.",
        "The whole reply is exactly one of: accept, refuse, haggle; the reply ended on its own.",
    ]
    for line in want:
        assert page.count(line) >= 1, line
    assert page.count("opens and closes in order") == 0 and "numbered 1, 2 and 3" not in page and "the keys mood, intent and line;" not in page
