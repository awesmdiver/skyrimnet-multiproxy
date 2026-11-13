"""The long-dash fixer on JSON replies, after the 4.2.1 safety review: S1 (the text `\\u2014` stays text) and S2 (one linear pass, no repeated scanning)."""
import json
import re
import time

import fancy_dashes as fd


def test_a_reply_that_holds_the_text_backslash_u2014_stays_valid_json():
    s = r'{"a": "write \\u2014 to get a dash ' + "—" + r'"}'              # JSON for: write — to get a dash (a real dash)
    out, n = fd.fix_json(s)
    assert json.loads(out) == {"a": "write \\u2014 to get a dash -"} and n == 1
    odd = r'{"a": "x \\— y"}'                                              # an escaped backslash, then a real — escape: that one is replaced
    out, n = fd.fix_json(odd)
    assert json.loads(out) == {"a": "x \\- y"} and n == 1


def test_a_normal_escaped_dash_is_still_replaced():
    out, n = fd.fix_json(r'{"a": "x — y – z − ―"}')
    assert json.loads(out) == {"a": "x - y - z - -"} and n == 4


def test_an_unterminated_string_of_escaped_quotes_is_fast():
    text = "—\"" + 'a\\"' * 15000                                          # 45 KB, never closed
    t = time.perf_counter()
    assert fd.fix_json(text) == (text, 0)
    assert time.perf_counter() - t < 0.5


def test_the_one_pass_reader_finds_the_same_strings_as_the_old_pattern_on_ordinary_input():
    old = re.compile(r'"(?:[^"\\\n]|\\.)*"')
    for text in ('{"a": "x — \\" y", "b": ["p", "q\\\\"], "c": 1}', '"a" "b"', 'say "hi" then "bye', '"one\ntwo" "three"', '{"k": "v—"}\n{"k2": "w"}'):
        assert [text[a:b] for a, b in fd._strings(text)] == [m.group(0) for m in old.finditer(text)], text
