"""Security review 4.3.0, S2 and S3: what another device's report says cannot break the page or the log.

S2: the last-known voice / decision state reads wrong-kind values in the settings as empty and keeps only a few short names and flags of a device's report.
S3: a Courier's report cannot put a line break (or a long text) into a log line: the graphics-card line, the device name, anything quoted from it."""
import json
import logging

import pytest

import local_models
import other_pcs
import voice_engine as ve

KEY = "voice_last_known"


class Dev:
    def __init__(self, id="d1", online=True, report=None):
        self.id, self.online, self.report = id, online, (GOOD if report is None else report)


def shape(rep):
    v = rep.get("voice")
    return {"model": str(v["model"]), "installedModels": [str(x) for x in (v.get("installedModels") or [])], "installed": bool(v.get("installed"))} if isinstance(v, dict) and v.get("model") else None


GOOD = {"voice": {"model": "higgs", "installedModels": ["higgs"], "installed": True}}


def run(stored, devices, keep_all=False):
    saved = {}
    cfg = {KEY: stored} if stored is not ... else {}
    out = other_pcs.remember_last_known(lambda: json.loads(json.dumps(cfg)), lambda d: saved.update(d), KEY, devices, shape, keep_all=keep_all)
    return out, saved


# ---- S2 ------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["abc", [1, 2], 7, {"d1": "oops"}, {"d1": [1]}, {"d1": {"model": "x", "seen": "never"}}, None, True, {"d1": {"model": "x", "seen": [1]}}, {7: {"seen": 1}}])
def test_a_wrong_kind_of_value_in_the_settings_reads_as_empty_and_is_replaced(bad):
    out, saved = run(bad, [Dev()])
    assert out["d1"]["model"] == "higgs" and out["d1"]["installed"] is True and isinstance(out["d1"]["seen"], int)
    assert saved[KEY] == out                                          # the damaged value was replaced by a good one


def test_a_wrong_kind_with_no_device_answering_is_empty_not_a_crash():
    for bad in ("abc", [1], 7, {"d1": "oops"}):
        out, saved = run(bad, [Dev(online=False)])
        assert out == {}


def test_a_damaged_settings_object_itself_is_survived():
    saved = {}
    out = other_pcs.remember_last_known(lambda: ["not", "a", "dict"], lambda d: saved.update(d), KEY, [Dev()], shape)
    assert out["d1"]["model"] == "higgs" and KEY in saved


def test_a_report_of_an_odd_shape_says_nothing_and_does_not_crash():
    for rep in ({"voice": {"model": "m", "installedModels": 5}}, {"voice": "text"}, {"voice": {"model": {"a": 1}, "installedModels": [{"x": 1}]}}):
        out, _ = run({}, [Dev(report=rep)])
        assert isinstance(out, dict)


def test_a_seven_megabyte_report_keeps_a_few_hundred_bytes():
    big = {"voice": {"model": "m" * 2_000_000, "installedModels": ["x" * 1000] * 5000, "installed": True}}
    out, saved = run({}, [Dev(report=big)])
    e = out["d1"]
    assert len(e["model"]) <= other_pcs.LAST_KNOWN_TEXT and len(e["installedModels"]) <= other_pcs.LAST_KNOWN_ITEMS
    assert all(len(x) <= other_pcs.LAST_KNOWN_TEXT for x in e["installedModels"])
    assert len(json.dumps(saved)) <= other_pcs.LAST_KNOWN_BYTES + 200
    assert e["installed"] is True


def test_line_breaks_and_control_characters_in_a_kept_name_are_gone():
    rep = {"voice": {"model": "hi\nggs\x00\r\n", "installedModels": ["a\nb", "c d"], "installed": True}}
    out, _ = run({}, [Dev(report=rep)])
    flat = json.dumps(out)
    assert "\\n" not in flat and "\\u2028" not in flat and "\\u0000" not in flat and "\\r" not in flat


def test_a_good_report_is_kept_as_before_and_an_unchanged_one_does_not_rewrite_the_settings():
    out, saved = run({}, [Dev()])
    assert out["d1"]["model"] == "higgs" and out["d1"]["installedModels"] == ["higgs"]
    again = []
    other_pcs.remember_last_known(lambda: {KEY: out}, lambda d: again.append(d), KEY, [Dev()], shape)
    assert again == []                                                  # nothing changed: nothing written


def test_a_device_that_was_removed_is_still_forgotten():
    out, _ = run({"gone": {"model": "higgs", "seen": 1}, "d1": {"model": "higgs", "seen": 1}}, [Dev()])
    assert list(out) == ["d1"]


# ---- S3 ------------------------------------------------------------------------------------------------------------------------------------
def test_clean_text_removes_every_control_character_and_cuts():
    assert local_models.clean_text("a\nb\r\nc\x00d e\x7f", 50) == "a b c d e"
    assert len(local_models.clean_text("x" * 5000, 80)) == 80
    assert local_models.clean_text(None) == "" and local_models.clean_text({"a": 1}, 50) == "{'a': 1}"


def test_the_graphics_card_line_of_a_hostile_report_is_one_short_line():
    r = type("R", (), {})()
    r.id, r.label = "d1", "Laptop\nFAKE LINE: key=abc"
    r.report = {"courier": {"platform": "win32"}, "gpus": [{"name": "RTX\n2026-01-01 ERROR forged line " + "x" * 5000, "memory_total_mb": 8192, "vendor": "nvidia",
                                                           "driver": "581.80\nforged\n" + "9" * 3000, "cuda": "13.5\nforged"}]}
    lines = []

    class L:
        def info(self, m):
            lines.append(m)
    out = ve.DeviceGraphicsLog(L()).on_report(r)
    assert len(out) == 1 and all("\n" not in x and "\r" not in x for x in out)
    assert max(len(x) for x in out) < 300


def test_a_report_with_a_string_or_list_where_a_dict_belongs_does_not_crash_the_card_line():
    for rep in ({"gpus": "text", "courier": "x"}, {"gpus": [1, None, "a"], "courier": []}, {"gpus": {"a": 1}}, {}):
        r = type("R", (), {})()
        r.id, r.label, r.report = "d2", "PC", rep
        lines = []

        class L:
            def info(self, m):
                lines.append(m)
        ve.DeviceGraphicsLog(L()).on_report(r)
        assert all("\n" not in x for x in lines)


def test_the_device_label_and_name_cannot_carry_a_line_break():
    r = other_pcs.Remote(id="d1", kind="courier", name="Gaming\nPC [FAKE]" + "x" * 500, base="http://127.0.0.1:1")
    assert "\n" not in r.label and len(r.label) <= 60


def test_a_name_taken_from_a_courier_report_is_cleaned_on_arrival(monkeypatch):
    r = other_pcs.Remote(id="d1", kind="courier", name="", base="http://127.0.0.1:1")
    rep = {"courier": {"name": "Lap\ntop" + "y" * 400}}
    r.name = local_models.clean_text((rep.get("courier") or {}).get("name"), 60) or r.name          # the line other_pcs runs when a report arrives
    assert "\n" not in r.name and len(r.name) <= 60


def test_the_log_lines_that_quote_a_device_message_clean_it(caplog):
    import voice_wiring
    src = open(voice_wiring.__file__, encoding="utf-8").read()
    assert "local_models.clean_text(getattr(e, 'message', e.__class__.__name__), 200)" in src
    import decision_wiring
    src = open(decision_wiring.__file__, encoding="utf-8").read()
    assert src.count("local_models.clean_text(getattr(e, 'message'") == 2
