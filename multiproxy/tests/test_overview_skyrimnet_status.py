"""Overview says "SkyrimNet Connected" only while SkyrimNet is really talking to MultiProxy (2026-10-26), and "Check everything" sits on that same line."""
import overview
import overview_ui

NOW = 1_000_000.0


def status(ago_s, running=False):
    return overview.skyrimnet_status(None if ago_s is None else NOW - ago_s, running, NOW)


def test_a_request_half_a_minute_ago_is_connected():
    assert status(30) == {"connected": True, "line": "Connected"}


def test_five_minutes_with_no_mod_notice_is_grey_and_says_when():
    assert status(5 * 60) == {"connected": False, "line": "last seen 5 min ago"}


def test_with_the_game_running_five_minutes_is_still_connected_but_eleven_is_not():
    assert status(5 * 60, running=True)["connected"] is True
    assert status(11 * 60, running=True) == {"connected": False, "line": "last seen 11 min ago"}


def test_never_seen_waits():
    assert status(None) == {"connected": False, "line": "Waiting for SkyrimNet"}
    assert status(None, running=True)["line"] == "Waiting for SkyrimNet"


def test_the_closed_notice_greys_it_after_the_short_window():
    assert status(5 * 60, running=False)["connected"] is False       # the mod said "closed": running is false again
    assert status(90, running=False)["connected"] is True            # still inside the two minutes


def test_older_sightings_are_told_in_hours_then_earlier_today():
    assert status(2 * 3600)["line"] == "last seen 2 h ago"
    assert status(13 * 3600)["line"] == "last seen earlier today"


def test_the_windows_are_named_constants():
    assert overview.SKYRIMNET_TALKING_S == 120 and overview.SKYRIMNET_GAME_RUNNING_S == 600


def test_the_check_button_is_on_the_status_line_before_the_speeds_link():
    js = overview_ui.JS
    fn = js[js.index("function ovSkyrimNet"):js.index("// Check everything:")]
    assert fn.index("ovCheckButton()") < fn.index("ovSpeedsLink()"), "the button sits just before the link"
    assert "if (!s && !link) return ''" not in fn, "the line is always drawn, since the button lives on it"
    assert "ovCheckButton()" not in js[js.index("function ovCheckBox"):js.index("async function ovCheckRun")], "no button on its own row any more"
    css = overview_ui.CSS
    assert "flex-wrap:wrap" in css.split(".ov-top {")[1].split("}")[0], "on a narrow window the button wraps under the status"
