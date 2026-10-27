"""The Quick decisions card (Local decisions, step 2): it is in the page on the Local models tab with its exact wording, the status it draws from carries what it
needs (the address to copy, the models, who answered last), the plain download-failure message, and what the SkyrimNet steps say."""
import logging

import pytest

import decision_engine as de
import decision_ui
from tests.test_decision_order import LOCAL_ANSWER, StubEngine, deepseek, lines, local, post, ts_session      # noqa: F401  (local is a fixture)

JS = decision_ui.DECISION_JS

# Gemini's wording and the item's own lines, exactly as they must appear on the page
WORDING = [
    "SkyrimNet asks dozens of tiny background questions while you play, such as \"who is speaking next?\". MultiProxy answers them with a small model on your own device. It never writes dialogue; it only directs the flow of the game.",
    "<h3>Start here</h3>",
    "In SkyrimNet, go to <b>AI &gt; Providers</b>, press <b>+ Add Custom Provider</b>, and fill in the <b>Name</b>, web address, <b>API Style</b> \"System One\", and <b>API Key</b>.",
    "<li><b>API Style:</b> System One</li>",
    "In SkyrimNet, go to <b>AI &gt; Models &gt; Decisions</b>, pick <b>MultiProxy Decisions</b>, and paste the model name.",
    "field('Model name:', s.localName)",
    "(Optional) Open the <b>⋯</b> menu to uninstall it, or change when it starts.",
    "You can move the model to another device later without changing these SkyrimNet settings.",
    "openHelp(\\'decisions\\')",
    "Ready. Using system memory because your graphics card is full. Answers will be slightly slower.",
    "Ready. Using system memory.",
    "Who answers decisions", "Drag a row", "Order", "Add a key",
    "Try the next model down if the current one takes longer than ", " seconds or is less than ", " % sure.",
    "Skip the local model if it hasn\\'t answered in",
    "Start when MultiProxy starts", "Waiting limit…", "Uninstall (frees about ",
    "Getting the engine software (one time)…", "Getting ready: doing a quick test.", "Installing, step ' + b.step + ' of ' + b.of",
    "Needs a newer MultiProxy Courier",
    "The usual is 0.8 seconds on a graphics card, or 6 seconds on system memory. A slower device might need more time. If skipped, your next available provider will answer.",
    "This removes the model from this device. The downloaded files are kept",
    "Your other providers are not affected.",
]


class TestThePage:
    def test_the_tab_sits_right_after_voice_and_before_routing(self, test_client):
        html = test_client.get("/").text
        assert html.index('id="tabVoice"') < html.index('id="tabDecisions"') < html.index('id="tabRouting"')
        assert 'id="paneDecisions" style="display:none"' in html and html.index('id="paneVoice"') < html.index('id="paneDecisions"') < html.index('id="paneRouting"')
        assert "function dcRender()" in html and "dcOpened()" in html and "dcClosed()" in html
        assert ">Decisions</div>" in html

    def test_local_models_has_no_card_only_a_line_pointing_at_the_tab(self, test_client):
        html = test_client.get("/").text
        pane = html[html.index('<div id="paneLocal"'):html.index('<div id="paneQuick"')]
        assert 'id="dcCard"' not in pane and 'id="dcBody"' not in pane and "Decision engine" not in pane
        assert "Quick decisions have their own tab. <a class=\"lm-link\" onclick=\"dcGoRow()\">Open Decisions</a>" in pane
        assert 'id="dcBody"' in html[html.index('<div id="paneDecisions"'):html.index('<div id="paneRouting"')]

    def test_a_link_standing_in_a_row_uses_the_button_size(self, test_client):
        html = test_client.get("/").text
        assert ".op-prog .lm-link { font-size:0.85rem }" in html
        assert "button {" in html and "font-size:0.85rem" in html[html.index("  button {"):][:200]

    @pytest.mark.parametrize("text", WORDING)
    def test_the_wording_is_exact(self, text):
        assert text in JS, text

    def test_no_jargon_and_no_jev_on_screen_except_where_it_says_what_this_replaces(self):
        visible = JS.lower().replace("ask jev when the local model isn" + chr(92) + "'t sure", "")        # the one place the director's own wording says Jev (queue: ask-jev-when-the-local-model-is-not-sure)
        for bad in ("plugin", "jev ", "typesafe's product", "llama-server", "gguf", "lora"):
            assert bad not in visible, bad

    def test_the_hint_names_the_provider_and_the_pinned_model_name(self):
        import decision_wiring as dw
        assert dw.PROVIDER_NAME == "MultiProxy Decisions" and dw.API_KEY_WORD == "proxy" and dw.LOCAL_NAME == "local/decisions"


class TestStatusForTheCard:
    def test_it_carries_the_address_to_copy_the_models_and_the_words(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module.decision_handles, "last_answer", None)      # (an earlier test in a full run leaves a last answer behind)
        d = test_client.get("/config/decisions/status").json()
        assert d["address"] == f"http://127.0.0.1:{proxy_module.MAIN_PORT}/v1/systemone"
        assert d["providerName"] == "MultiProxy Decisions" and d["apiKeyWord"] == "proxy" and d["localName"] == "local/decisions"
        assert [m["key"] for m in d["models"]] == ["kev-4b", "laya", "lev"] and d["models"][0]["label"] == "Kev-4B" and d["models"][0]["blurb"] == "Recommended: accurate and fast."
        assert d["models"][1]["label"] == "Laya (small)" and d["models"][1]["blurb"].startswith("For devices with little or no graphics memory")
        assert d["models"][0]["graphicsBytes"] > 3 * 1024 ** 3 and 2.8e9 < d["models"][0]["diskBytes"] < 3.3e9
        assert d["models"][1]["graphicsBytes"] < 2 * 1024 ** 3 and d["models"][1]["diskBytes"] < 0.5e9
        assert d["installedModels"] == [] and d["typesafeKey"] is False and d["lastAnswer"] is None and d["answerOrder"] is None

    def test_it_knows_whether_a_typesafe_key_is_saved(self, test_client, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "typesafe_api_key", "ts-fake")
        assert test_client.get("/config/decisions/status").json()["typesafeKey"] is True


class TestInstallFailureIsPlain:
    @pytest.mark.parametrize("raw", ["Couldn't reach the download site for Kev-4B-Q4_K_M.gguf (ClientConnectorError). Check your internet and try again; what was already downloaded is kept.",
                                     "The download site answered 404 for Kev-4B-Q4_K_M.gguf.",
                                     "Kev-4B-Q4_K_M.gguf isn't in D:\\somewhere.",
                                     "The download was interrupted. Press Download to pick up where it left off."])
    def test_a_download_that_could_not_be_made_says_so_without_an_address_or_a_folder(self, raw):
        assert de.plain_install_error(raw) == "Couldn't download the decision model's files. Check your internet connection and try again."

    def test_other_reasons_keep_their_words(self):
        assert "safety check" in de.plain_install_error("x.gguf didn't pass its safety check (the download was damaged). Press Download to start it again.")
        assert de.plain_install_error("Press Download to start") == "Press Install to start"

    @pytest.mark.asyncio
    async def test_an_install_that_cannot_download_ends_with_that_message(self, tmp_path):
        from tests.decision_helpers import make_engine
        engine, _, src = make_engine(tmp_path)
        (src / "model-kev-4b.gguf").unlink()
        job = engine.start_install()
        await job.task
        assert job.state == "error" and job.error == de.DOWNLOAD_FAILED and str(src) not in job.error
        await engine.close()


class TestWhoAnsweredLast:
    def test_a_local_answer_is_noted_as_the_first_choice(self, proxy_module, test_client, local):
        local()
        post(proxy_module, test_client)
        la = proxy_module.decision_handles.last_answer
        assert la["by"] == "local" and la["first"] == "local" and la["miss"] == ""

    def test_when_the_local_model_was_too_slow_and_typesafe_answered_the_card_can_say_why(self, proxy_module, test_client, local):
        local("too_slow")
        post(proxy_module, test_client, key=True, session=ts_session())
        la = proxy_module.decision_handles.last_answer
        assert la["by"] == "TypeSafe" and la["first"] == "local" and la["miss"] == "too_slow"
        assert test_client.get("/config/decisions/status").json()["lastAnswer"]["by"] == "TypeSafe"

    def test_a_stopped_model_and_a_stand_in(self, proxy_module, test_client, local):
        local("stopped")
        post(proxy_module, test_client, deepseek=deepseek())
        la = proxy_module.decision_handles.last_answer
        assert la["by"] == "DeepSeek" and la["miss"] == "not_running"

    def test_typesafe_first_and_it_failed_so_the_local_model_answered(self, proxy_module, test_client, local):
        local(answer_first="typesafe")
        post(proxy_module, test_client, key=True, session=ts_session(429, b'{"error":"x"}'))
        la = proxy_module.decision_handles.last_answer
        assert la["by"] == "local" and la["first"] == "typesafe" and la["miss"] == "typesafe"


def test_the_old_intro_and_first_step_are_gone_from_every_script():
    import local_models_ui, voice_ui, other_pcs_ui
    for mod in (decision_ui, local_models_ui, voice_ui, other_pcs_ui):
        text = "".join(v for v in vars(mod).values() if isinstance(v, str))
        for old in ("save you money", "answers them instantly on your device", "check the \"needs about", "Leave the defaults exactly as they are"):
            assert old not in text, (mod.__name__, old)
    assert "Select the recommended Kev-4B model" not in decision_ui.DECISION_JS and "Press <b>Install</b>" not in decision_ui.DECISION_JS


class TestTheNumberedList:
    """The "Who answers decisions" card: the numbered list, the sentence under it, and the lines about whoever could not answer. The card's functions run in Node against a
    stand-in page (every name the card calls but does not define is a stub that answers an empty string; esc, jsq and opDot are real here)."""

    HARNESS = r"""
const vm = require('vm');
const ghost = new Proxy({}, { has: () => true, get: (t, k) => (k in t ? t[k] : (k === Symbol.unscopables ? undefined : (k in globalThis ? globalThis[k] : () => ''))), set: (t, k, v) => { t[k] = v; return true; } });
vm.createContext(ghost);
const noop = () => new Proxy(function () {}, { get: () => noop(), apply: () => noop() });
for (const n of ['document', 'window', 'localStorage', 'navigator', 'history', 'location']) ghost[n] = noop();
ghost.esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
ghost.jsq = s => String(s).replace(/'/g, "\\'");
ghost.opDot = (c, t) => '<span class="op-dot ' + c + '"></span>';
ghost.lmPost = async (u, b) => { ghost.__posts.push([u, b]); return {}; };
ghost.__posts = [];
vm.runInContext(process.env.DC_JS, ghost);
const S = JSON.parse(process.env.DC_STATUS);
vm.runInContext("dcSt = " + JSON.stringify(S), ghost);
const out = {};
out.card = vm.runInContext("dcWhoCard({ installed: true, state: 'stopped', settings: dcSt.settings })", ghost);
out.cardReady = vm.runInContext("dcWhoCard({ installed: true, state: 'ready', settings: dcSt.settings })", ghost);
out.cardNone = vm.runInContext("dcWhoCard({ installed: false, state: 'not_installed', settings: dcSt.settings })", ghost);
vm.runInContext("dcDo = async fn => { await fn(); }; dcLoad = async () => {}; dcRender = () => {}", ghost);
vm.runInContext("dcOrdMove(0, 2)", ghost);
out.afterDrag = vm.runInContext("dcSt.settings.decision_order", ghost);
vm.runInContext("dcOrdKey({ key: 'ArrowUp', preventDefault() {} }, 2)", ghost);
out.afterKey = vm.runInContext("dcSt.settings.decision_order", ghost);
out.focus = vm.runInContext("dcOrdFocus", ghost);
setTimeout(() => { out.posts = ghost.__posts; console.log(JSON.stringify(out)); }, 20);
"""

    STATUS = {
        "settings": {"model": "kev-4b", "decision_order": ["perplexity", "local", "typesafe", "nano"], "ask_when_unsure": True, "sure_percent": 60, "paid_limit_seconds": 6},
        "location": "this", "thisPc": {"name": "Area-51"}, "pcs": [], "models": [{"key": "kev-4b", "label": "Kev-4B"}], "typesafeKey": False, "nanoKey": True,
        "providers": [{"id": "perplexity", "label": "My Perplexity key", "ready": True, "keyOk": True, "models": ["a", "b"], "model": "a", "labels": {"a": "Cheaper and faster"}},
                      {"id": "nano", "label": "My Nano-GPT key", "ready": True, "keyOk": True, "models": ["m1", "m2"], "model": "m1"},
                      {"id": "ollama", "label": "My Ollama decision model", "ready": False, "keyOk": True, "models": []}],
        "answerLines": {"nano": "Nano-GPT said no (your balance may be empty). Another AI answered instead."}, "lastAnswer": None,
    }

    def run(self, status=None):
        import json, os, shutil, subprocess
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node isn't installed")
        r = subprocess.run([node, "-e", self.HARNESS], capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, DC_JS=JS, DC_STATUS=json.dumps(status or self.STATUS)), timeout=60)
        assert r.returncode == 0, r.stderr[:1500]
        return json.loads(r.stdout)

    @staticmethod
    def rows(html):
        return [r for r in html.split('<tr class="dc-ord-row"')[1:]]

    def test_one_numbered_row_per_service_in_the_saved_order_with_the_right_dot_and_word(self):
        h = self.run()["card"]
        rows = self.rows(h)
        assert len(rows) == 4
        names = [r.split("<b>")[1].split("</b>")[0] for r in rows]
        assert names == ["Perplexity", "Local model", "TypeSafe", "Nano-GPT"], names
        assert [r.split('class="dc-n">')[1].split("<")[0] for r in rows] == ["1", "2", "3", "4"]
        assert "op-dot on" in rows[0] and "Ready" in rows[0] and "Cheaper and faster" in rows[0] and "<select" in rows[0]
        assert "op-dot idle" in rows[1] and "Stopped" in rows[1] and "Kev-4B, on Area-51" in rows[1]
        assert "op-dot idle" in rows[2] and "No key" in rows[2] and "dcGoKey('typesafe')" in rows[2] and ">Add a key<" in rows[2], "a service with no key stays in the list, greyed, with a link"
        assert "op-dot on" in rows[3] and "Ready" in rows[3] and "<select" in rows[3]
        assert "<th>Order</th><th>Model</th><th>Status</th>" in h

    def test_the_local_model_row_is_ready_stopped_or_not_installed(self):
        r = self.run()
        assert "op-dot on" in self.rows(r["cardReady"])[1] and "Ready" in self.rows(r["cardReady"])[1]
        none = self.rows(r["cardNone"])[1]
        assert "op-dot idle" in none and "Not installed" in none

    def test_a_service_that_failed_on_the_last_decision_has_one_amber_line_under_its_row(self):
        h = self.run()["card"]
        assert h.count('<tr class="dc-ord-note">') == 1
        assert "Nano-GPT said no (your balance may be empty). Another AI answered instead." in h.split('<tr class="dc-ord-note">')[1]

    def test_every_row_can_be_dragged_and_its_handle_takes_the_arrow_keys(self):
        h = self.run()["card"]
        assert h.count('draggable="true"') == 4 and h.count('role="button"') == 4 and h.count("onkeydown=\"dcOrdKey(event,") == 4
        assert "ondrop=\"dcOrdDrop(event, 3)\"" in h and "Up and Down arrow keys" in h

    def test_dragging_and_the_arrow_keys_reorder_and_save_the_list(self):
        r = self.run()
        assert r["afterDrag"] == ["local", "typesafe", "perplexity", "nano"], "row 1 dragged to place 3: the numbers follow"
        assert r["afterKey"] == ["local", "perplexity", "typesafe", "nano"] and r["focus"] == "perplexity", "Up on row 3 moves it up one and keeps the handle focused"
        assert [p for p in r["posts"] if p[0] == "/config/decisions/settings"][-1][1] == {"decisionOrder": ["local", "perplexity", "typesafe", "nano"]}

    def test_the_one_sentence_is_the_directors_and_its_numbers_are_disabled_while_unticked(self):
        r = self.run()
        h = r["card"]
        sentence = "Try the next model down if the current one takes longer than <input type=\"text\" class=\"dc-num\" id=\"dcPaidLimit\""
        assert sentence in h and "seconds or is less than <input" in h and "% sure.</label>" in h
        assert 'id="dcAskUnsure" checked' in h and 'value="60"' in h and 'value="6"' in h and " disabled" not in h.split('<label class="dc-sentence">')[1].split('</label>')[0]
        off = self.run({**self.STATUS, "settings": {**self.STATUS["settings"], "ask_when_unsure": False}})["card"].split('<label class="dc-sentence">')[1].split('</label>')[0]
        assert 'id="dcAskUnsure" checked' not in off and off.count(" disabled") == 2

    def test_none_of_the_old_texts_is_left_on_the_page(self):
        for old in ("The other one takes over", "Answer decisions with", "Ask a stronger AI when", "Your local model must be at least", "Move on if a paid answer takes longer than",
                    "so that choice is off", "so those choices are off", "Already have a TypeSafe key?", "Don't have one?", "dcAnswerWith", "dcSelFirst"):
            assert old not in JS, old
        assert "answer_first" not in JS


def test_the_card_warns_when_the_player_chose_the_local_model_first_and_it_is_off():
    src = JS.replace("\r\n", "\n")
    assert "(dcSt.settings.decision_order || [])[0] || 'local') === 'local' && canStart" in src
    assert "The local decision model is off, so decisions are going to another AI. Press <b>Start</b>." in src
