"""The gold drop border on Routing follows the card under the pointer (2026-10-26). The cards sit in columns by place, so a model's number (its place in the view's list) is not
its place among the cards on the page: the highlight used to land on "the i-th card in the page", a different card than the one under the pointer. The drop always used the
right number (`rtView.ais[i]`). Run in node against a stand-in page."""
import json
import subprocess

import pytest

import routing_ui
from tests.test_routing_gone_and_decisions import HARNESS, NODE

pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")

SCRIPT = r"""
const mk = () => { const s = new Set(); const o = { s, classList: { add: c => s.add(c), remove: (...c) => c.forEach(x => s.delete(x)) } }; o.contains = x => x === o; return o; };
// the page order of the cards is NOT the order of the view's list: This device holds gem-lite (view number 3) and Cloud holds gem-flash (view number 1)
const cards = { flash: mk(), lite: mk(), lost: mk(), elsewhere: mk() };
const all = Object.values(cards);
global.document = { getElementById: id => null, activeElement: null, querySelectorAll: sel => all.filter(e => (sel.includes('.rt-over') && e.s.has('rt-over')) || (sel.includes('.rt-dragging') && e.s.has('rt-dragging'))) };
rtView = { settings: {}, places: [], hows: [], groups: [], status: {}, jobs: [{ key: 'decisions', label: 'Decisions', route: null, onAis: [], models: [] }],
  ais: [{ id: 'x0', place: 'cloud' }, { id: 'gem-flash', place: 'cloud' }, { id: 'gone', place: 'lost' }, { id: 'gem-lite', place: 'this' }, { id: 'or', place: 'elsewhere' }, { id: 'job/j', place: 'unchosen' }] };
rtJobAt = i => rtView.jobs[i];
const moved = []; rtMoveJob = (job, id) => moved.push(id);
rtRender = () => {};
const ev = (el, related) => ({ currentTarget: el, relatedTarget: related || null, prevented: false, preventDefault() { this.prevented = true; } });
const out = {};
rtDrag = 0;
let e1 = ev(cards.lite); rtOverAi(e1, 3);                       // the pointer is over gem-lite (view number 3)
out.liteLit = cards.lite.s.has('rt-over'); out.flashLit = cards.flash.s.has('rt-over'); out.prevented = e1.prevented;
let e2 = ev(cards.flash); rtOverAi(e2, 1);                      // it moves on to gem-flash (view number 1): the first one goes out
out.afterMove = { lite: cards.lite.s.has('rt-over'), flash: cards.flash.s.has('rt-over') };
let e3 = ev(cards.lost); rtOverAi(e3, 2);                       // a model under Needs attention takes nothing
out.lostLit = cards.lost.s.has('rt-over'); out.lostPrevented = e3.prevented;
let e4 = ev(cards.elsewhere); rtOverAi(e4, 4);                  // nor does one SkyrimNet sends elsewhere
out.elseLit = cards.elsewhere.s.has('rt-over');
rtLeaveAi(ev(cards.flash, cards.flash), 1);                       // onto something inside the same card: still lit
out.stillLit = cards.flash.s.has('rt-over');
rtLeaveAi(ev(cards.flash, null), 1);
out.leftOut = cards.flash.s.has('rt-over');
rtOverAi(ev(cards.lite), 3);
rtDropAi(ev(cards.lite), 3);                                    // the drop goes to the card the player sees lit
out.moved = moved.slice(); out.clearedAfterDrop = all.every(e => !e.s.has('rt-over'));
rtDrag = 0; cards.flash.s.add('rt-over'); cards.lite.s.add('rt-dragging');
rtDragEnd();
out.clearedAtEnd = all.every(e => !e.s.has('rt-over') && !e.s.has('rt-dragging'));
rtDrag = 0; rtDropAi(ev(cards.lost), 2);                        // a drop on one that takes nothing moves nothing
out.movedAfterBad = moved.slice();
out.canTake = rtView.ais.map(a => rtCanTake(a));
console.log(JSON.stringify(out));
"""


def _run(tmp_path):
    f = tmp_path / "drop.js"
    f.write_text(HARNESS + routing_ui.ROUTING_JS + SCRIPT, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[:1500]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_highlight_is_on_the_card_under_the_pointer_and_the_drop_goes_there(tmp_path):
    o = _run(tmp_path)
    assert o["liteLit"] is True and o["flashLit"] is False and o["prevented"] is True, "the lit card is the one the event came from, not the one at that number in the page"
    assert o["afterMove"] == {"lite": False, "flash": True}, "moving on takes the border off the card it left"
    assert o["stillLit"] is True and o["leftOut"] is False, "leaving for a child of the same card keeps it; really leaving clears it"
    assert o["moved"] == ["gem-lite"], "the drop goes to the card the player saw lit"
    assert o["clearedAfterDrop"] is True and o["clearedAtEnd"] is True, "no border is left behind"


def test_a_card_that_cannot_answer_a_job_does_not_light_up_and_takes_no_drop(tmp_path):
    o = _run(tmp_path)
    assert o["lostLit"] is False and o["lostPrevented"] is False and o["elseLit"] is False
    assert o["movedAfterBad"] == ["gem-lite"], "a drop on a Needs attention card moved nothing"
    assert o["canTake"] == [True, True, False, True, False, False]


def test_the_cards_that_cannot_take_a_job_have_no_drop_handlers():
    js = routing_ui.ROUTING_JS
    assert "(rtCanTake(a) ? ' ondragover=\"rtOverAi(event, ' + i + ')\" ondragleave=\"rtLeaveAi(event, ' + i + ')\" ondrop=\"rtDropAi(event, ' + i + ')\"' : '')" in js
    assert "document.querySelectorAll('.rt-ai')[i]" not in js, "no highlight by position in the page any more"
