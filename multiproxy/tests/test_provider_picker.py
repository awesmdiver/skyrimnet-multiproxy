"""The Setup tab's "Choose a provider" (2026-10-03): three groups by how you pay, short pill names, status-only dots, no dropdown."""
import asyncio
import json
import re
import shutil
import subprocess

import pytest


def _page(test_client):
    return test_client.get("/").text


def _providers(proxy_module):
    return asyncio.run(proxy_module._dashboard_providers())


def test_every_provider_declares_a_known_group(proxy_module):
    groups = {g[0] for g in proxy_module.PICKER_GROUPS}
    got = _providers(proxy_module)
    assert len(got) == 21      # Perplexity joined the key providers
    assert all(p["group"] in groups and p["pill"] for p in got)
    by = {g: sorted(p["id"] for p in got if p["group"] == g) for g in groups}
    assert by["subscription"] == ["claude", "codex", "geminicli", "grok"]
    assert by["pc"] == ["lmstudio", "local", "ollama", "remote"]
    assert len(by["key"]) == 13 and "perplexity" in by["key"]


def test_pill_names_lose_the_repeated_words_and_keep_a_quiet_second_name(proxy_module):
    got = {p["id"]: (p["pill"], p["tag"]) for p in _providers(proxy_module)}
    assert got["codex"] == ("ChatGPT", "Codex") and got["geminicli"] == ("Google", "Antigravity") and got["grok"] == ("Grok", "SuperGrok")
    assert got["glm"] == ("GLM", "z.ai") and got["typesafe"] == ("TypeSafe", "Jev") and got["claude"] == ("Claude", "")
    for pill, tag in got.values():
        assert not re.search(r"\((Key|Subscription|This device|Network)\)", pill)


def test_the_group_headings_and_notes(proxy_module):
    assert [g[1:] for g in proxy_module.PICKER_GROUPS] == [
        ("Your subscriptions", "No key needed."), ("With a key", "Pay as you go, or use your plan's key."), ("On your devices", "Free, runs at home.")]


def test_there_is_no_dropdown_and_the_pulse_is_only_on_issue_dots(test_client):
    html = _page(test_client)
    assert "providerPick" not in html and 'id="chips"' in html
    assert "animation:issue 2.4s" in html
    pulse = re.findall(r"([^{}\n]*)\{\{?\s*animation:issue", html)
    assert pulse and all(".warn" in s and ".bad" in s and ".ok" not in s and ".off" not in s for s in pulse)
    assert "prefers-reduced-motion:reduce" in html and "Choose a provider" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_pills_are_buttons_grouped_and_ordered_set_up_first(test_client, tmp_path):
    html = _page(test_client)
    start = html.index("function pillLabel(p)")
    end = html.index("function renderPanel(p)")
    js = html[start:end].replace("{{", "{").replace("}}", "}")
    harness = tmp_path / "h.js"
    harness.write_text(
        "let selectedProvider = null; const els = {};\n"
        "const $ = id => els[id] || (els[id] = {style:{}, querySelectorAll: () => [], textContent:''});\n"
        "const PICKER_GROUPS = [['a','Group A','note a'],['b','Group B','note b']];\n"
        "let PROVIDERS = [\n"
        " {id:'z',group:'a',pill:'Zed',tag:'',state:'ok'},{id:'m',group:'a',pill:'Mid',tag:'Quiet',state:'off'},\n"
        " {id:'c',group:'a',pill:'Cee',tag:'',state:'warn'},{id:'y',group:'b',pill:'Why',tag:'',state:'off'},{id:'x',group:'b',pill:'Ex',tag:'',state:'bad'}];\n"
        + js + "\n"
        "renderChips('c'); const out = $('chips').innerHTML;\n"
        "PROVIDERS[3].state = 'ok'; renderChips('c'); const out2 = $('chips').innerHTML;\n"
        "console.log(JSON.stringify({out, out2, count: $('readyCount').textContent}));\n",
        encoding="utf-8")
    got = json.loads(subprocess.run(["node", str(harness)], capture_output=True, text=True, check=True).stdout)
    out, out2 = got["out"], got["out2"]
    names = lambda s: re.findall(r'</span>([A-Za-z]+)(?: <span class="tag">|</button>)', s)
    assert names(out) == ["Cee", "Zed", "Mid", "Ex", "Why"], "set up first (alphabetical), then not set up, inside each group"
    assert names(out2) == ["Cee", "Zed", "Mid", "Ex", "Why"] or names(out2)[3:] == ["Ex", "Why"], "Why became set up: only then does it sort ahead of nothing else in its group"
    assert out.count("<button") == 5 and 'aria-pressed="true"' in out and out.count('aria-pressed="true"') == 1
    assert out.index("Group A") < out.index("Group B") and '<span class="tag">Quiet</span>' in out
    assert 'class="dot off"' in out and 'class="chip off ' in out and 'class="dot warn"' in out and 'class="dot bad"' in out
    assert got["count"] == "2 of 5 ready"
