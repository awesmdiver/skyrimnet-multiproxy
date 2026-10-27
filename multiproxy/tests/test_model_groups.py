"""Long model lists grouped by family (model_groups_ui.py), 2026-10-01.

The grouping runs in the browser, so these tests run the page's own script under Node with REAL model
names (copied from the live dashboard of the dev device for Claude, Gemini, Google Antigravity and Codex;
Ollama / Local models / the rest from real naming conventions) and check the families, the newest pick,
the ordering, the 5-model threshold and the rendered HTML.
"""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

import model_groups_ui as mg

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

# ---- real lists: [id, display name, note] --------------------------------------------------------------
CLAUDE = [
    ["claude-sonnet-5-5", "Sonnet 5.5", "The best mix of speed and smarts."],
    ["claude-opus-5-5", "Opus 5.5", "For complex tasks and hard work."],
    ["claude-fable-5-1", "Fable 5.1", "For deep reasoning and multi-step tasks."],
    ["claude-opus-5", "Opus 5", ""], ["claude-sonnet-5", "Sonnet 5", ""], ["claude-fable-5", "Fable 5", ""],
    ["claude-opus-4-8", "Opus 4.8", ""], ["claude-opus-4-7", "Opus 4.7", ""], ["claude-sonnet-4-6", "Sonnet 4.6", ""],
    ["claude-opus-4-6", "Opus 4.6", ""], ["claude-opus-4-5-20251101", "Opus 4.5", ""],
    ["claude-haiku-4-5-20251001", "Haiku 4.5", "The fastest model with near-frontier intelligence"],
    ["claude-sonnet-4-5-20250929", "Sonnet 4.5", ""],
]
GEMINI = [
    ["gemini/gemini-3-flash-preview", "Gemini 3 Flash Preview", "Preview"],
    ["gemini/gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", ""],
    ["gemini/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite Preview", "Preview"],
    ["gemini/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "Preview"],
    ["gemini/gemini-3.1-pro-preview-customtools", "Gemini 3.1 Pro Preview Custom Tools", "Preview"],
    ["gemini/gemini-3.5-flash", "Gemini 3.5 Flash", ""], ["gemini/gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", ""],
    ["gemini/gemini-3.6-flash", "Gemini 3.6 Flash", ""], ["gemini/gemini-3.7-flash", "Gemini 3.7 Flash", ""],
    ["gemini/gemini-3.8-flash", "Gemini 3.8 Flash", ""],
]
ANTIGRAVITY = [
    ["geminicli/gemini-3.8-flash-high", "Gemini 3.8 Flash (High)", ""], ["geminicli/gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)", ""],
    ["geminicli/gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)", ""], ["geminicli/gemini-3.7-flash-high", "Gemini 3.7 Flash (High)", ""],
    ["geminicli/gemini-3.7-flash-medium", "Gemini 3.7 Flash (Medium)", ""], ["geminicli/gemini-3.7-flash-low", "Gemini 3.7 Flash (Low)", ""],
    ["geminicli/gemini-3.6-flash-high", "Gemini 3.6 Flash (High)", ""], ["geminicli/gemini-3.6-flash-medium", "Gemini 3.6 Flash (Medium)", ""],
    ["geminicli/gemini-3.6-flash-low", "Gemini 3.6 Flash (Low)", ""], ["geminicli/gemini-3.1-pro-high", "Gemini 3.1 Pro (High)", ""],
    ["geminicli/gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)", ""], ["geminicli/claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)", ""],
    ["geminicli/claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)", ""], ["geminicli/gpt-oss-120b-medium", "GPT-OSS 120B (Medium)", ""],
]
CODEX = [   # the four recommended + a couple the player pinned (4th element = pinned)
    ["codex/gpt-5.6-terra", "GPT-5.6-Terra", "Balanced agentic coding model for everyday work."],
    ["codex/gpt-5.6-luna", "GPT-5.6-Luna", "Fast and affordable agentic coding model."],
    ["codex/gpt-5.5", "GPT-5.5", "Proven previous-generation model for coding and general work."],
    ["codex/gpt-5.6-astra", "GPT-5.6-Astra", "Most capable."], ["codex/gpt-5.6-sol", "GPT-5.6-Sol", "Strongest reasoning."],
    ["codex/my-model", "my-model", "", True], ["codex/anthropic/claude-x", "claude-x", "", "anthropic/claude-x"],
]
OLLAMA = [
    ["ollama/gemma4:latest", "gemma4:latest", "~17 GB"], ["ollama/gemma4:26b", "gemma4:26b", "~17 GB"],
    ["ollama/llama3.1:8b", "llama3.1:8b", ""], ["ollama/llama3.1:70b", "llama3.1:70b", ""], ["ollama/qwen3:8b", "qwen3:8b", ""],
    ["ollama/qwen3-coder:30b", "qwen3-coder:30b", ""], ["ollama/deepseek-r1:7b", "deepseek-r1:7b", ""],
    ["ollama/gpt-oss:120b-cloud", "gpt-oss:120b-cloud", "Cloud model"], ["ollama/hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M", "x", ""],
]
LOCAL = [
    ["local/gemma-4-12b", "Gemma 4 12B", "Sees pictures"], ["local/gemma-4-26b-a4b", "Gemma 4 26B A4B", "Sees pictures"],
    ["local/gemma-4-31b", "Gemma 4 31B", "Sees pictures"], ["local/gemma-3-270m-it-q8-0", "gemma-3-270m-it-Q8_0", ""],
    ["local/tiny-chat", "Tiny Chat", ""], ["local/my-cool-model-q4", "My Cool Model Q4", ""],
]
REMOTE = [
    ["den-pc/gemma-4-31b", "Gemma 4 31B", ""], ["den-pc/gemma-4-26b-a4b", "Gemma 4 26B A4B", ""],
    ["den-llama/qwen-32b", "qwen-32b", ""], ["den-llama/meta/llama-3-8b", "meta/llama-3-8b", ""],
    ["den-llama/gemma-3-12b", "gemma-3-12b", ""], ["den-llama/qwen-14b", "qwen-14b", ""],
]
OTHERS = {   # providers whose real lists are short today; the rule must still read sensibly when they grow
    "nano": [["nano/gpt-4o-mini", "GPT-4o mini", ""], ["nano/qwen/qwen3.8-27b-uncensored", "Qwen3.8 27B (uncensored)", ""],
             ["nano/qwen/qwen3.8-27b", "Qwen3.8 27B", ""], ["nano/gpt-5.5", "GPT-5.5", ""], ["nano/glm-4.6", "GLM 4.6", ""], ["nano/glm-5.3", "GLM 5.3", ""]],
    "openai": [["openai/gpt-4o-mini", "GPT-4o mini", ""], ["openai/gpt-5.5", "GPT-5.5", ""], ["openai/gpt-4.1", "GPT-4.1", ""],
               ["openai/gpt-4o", "GPT-4o", ""], ["openai/o3", "o3", ""], ["openai/gpt-5.4", "GPT-5.4", ""]],
    "deepseek": [["deepseek/deepseek-flash", "DeepSeek Flash", ""], ["deepseek/deepseek-v4-pro", "DeepSeek V4 Pro", ""],
                 ["deepseek/deepseek-v3.2", "DeepSeek V3.2", ""], ["deepseek/deepseek-v4-flash", "DeepSeek V4 Flash", ""],
                 ["deepseek/deepseek-r1", "DeepSeek R1", ""], ["deepseek/deepseek-v3", "DeepSeek V3", ""]],
    "glm": [["glmzai/glm-4.6", "GLM 4.6", ""], ["glmzai/glm-5.3", "GLM 5.3", ""], ["glmzai/glm-4.7", "GLM 4.7", ""],
            ["glmzai/glm-5", "GLM 5", ""], ["glmzai/glm-5.3-flash", "GLM 5.3 Flash", ""], ["glmzai/glm-4.5-air", "GLM 4.5 Air", ""]],
    "typesafe": [["typesafe/jev-latest", "Jev (latest)", ""], ["typesafe/jev-1.13", "Jev 1.13", ""], ["typesafe/jev-preview", "Jev (preview)", ""],
                 ["typesafe/jev-1.12", "Jev 1.12", ""], ["typesafe/jev-1.11", "Jev 1.11", ""], ["typesafe/jev-1.10", "Jev 1.10", ""]],
    "kilo": [["kilo/anthropic/claude-sonnet-5", "Claude Sonnet 5", ""], ["kilo/anthropic/claude-opus-5", "Claude Opus 5", ""],
             ["kilo/openai/gpt-5", "GPT-5", ""], ["kilo/google/gemini-3-flash", "Gemini 3 Flash", ""], ["kilo/x-ai/grok-4", "Grok 4", ""], ["kilo/kilo-auto/balanced", "Kilo Auto", ""]],
}


def _run(providers_rows: dict, extra: str = "") -> dict:
    """Runs the page's grouping script under Node and returns what it prints as JSON."""
    prelude = "global._escapeHtml = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n"
    prelude += "global.jsq = s => String(s == null ? '' : s).replace(/[\\\\']/g, '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n"   # (the page's own jsq, from local_models_ui: the Copy newest button passes an id through it)
    script = (prelude + mg.MODEL_GROUPS_LOGIC_JS + mg.MODEL_GROUPS_RENDER_JS +
              f"const DATA = {json.dumps(providers_rows)};\n" + extra)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return json.loads(out.stdout.decode("utf-8"))


def groups(pid, rows):
    res = _run({}, f"console.log(JSON.stringify(mgGroupRows({json.dumps(pid)}, {json.dumps(rows)}).map(g => ({{family: g.family, count: g.rows.length, newest: g.newest && g.newest[0], ids: g.rows.map(r => r[0])}}))));")
    return res


def fam(pid, rows):
    return [(g["family"], g["count"], g["newest"]) for g in groups(pid, rows)]


# ---- per provider ------------------------------------------------------------------------------------------
class TestClaude:
    def test_families_without_the_maker_ordered_by_their_newest(self):
        assert fam("claude", CLAUDE) == [
            ("Sonnet", 4, "claude-sonnet-5-5"), ("Opus", 6, "claude-opus-5-5"),
            ("Fable", 2, "claude-fable-5-1"), ("Haiku", 1, "claude-haiku-4-5-20251001")]

    def test_inside_a_group_newest_first(self):
        g = {x["family"]: x["ids"] for x in groups("claude", CLAUDE)}
        assert g["Sonnet"] == ["claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929"]
        assert g["Opus"] == ["claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5-20251101"]

    def test_a_dated_id_is_not_mistaken_for_a_version(self):
        assert fam("claude", [["claude-haiku-4-5-20251001", "Haiku 4.5", ""]]) == [("Haiku", 1, "claude-haiku-4-5-20251001")]


class TestGemini:
    def test_flash_flash_lite_and_pro(self):
        assert fam("gemini", GEMINI) == [
            ("Flash", 5, "gemini/gemini-3.8-flash"), ("Flash Lite", 3, "gemini/gemini-3.5-flash-lite"), ("Pro", 2, "gemini/gemini-3.1-pro-preview")]

    def test_a_finished_model_beats_its_preview_when_the_version_ties(self):
        g = {x["family"]: x["ids"] for x in groups("gemini", GEMINI)}
        assert g["Flash Lite"] == ["gemini/gemini-3.5-flash-lite", "gemini/gemini-3.1-flash-lite", "gemini/gemini-3.1-flash-lite-preview"]
        assert g["Flash"][-1] == "gemini/gemini-3-flash-preview"


class TestAntigravity:
    def test_effort_levels_stay_with_their_family_and_other_makers_get_their_own(self):
        res = fam("geminicli", ANTIGRAVITY)
        assert [(f, c) for f, c, _ in res] == [("Flash", 9), ("Pro", 2), ("Sonnet", 1), ("Opus", 1), ("GPT-OSS", 1)], "the provider's own order: versions of different makers can't be compared"
        assert res[0][2] == "geminicli/gemini-3.8-flash-high"        # the provider's own first (High) wins the tie


class TestCodex:
    def test_codenames_are_families_and_a_bare_version_is_gpt(self):
        res = {f: (c, n) for f, c, n in fam("codex", CODEX)}
        assert res["Terra"] == (1, "codex/gpt-5.6-terra") and res["Luna"][0] == 1 and res["Astra"][0] == 1 and res["Sol"][0] == 1
        assert res["GPT"] == (1, "codex/gpt-5.5")

    def test_pinned_models_are_their_own_group_shown_first_with_no_newest(self):
        gs = groups("codex", CODEX)
        assert gs[0]["family"] == "Pinned" and gs[0]["count"] == 2 and gs[0]["newest"] is None
        assert gs[0]["ids"] == ["codex/my-model", "codex/anthropic/claude-x"]
        assert all(g["family"] != "Pinned" for g in gs[1:])


class TestOllamaAndLocalModels:
    def test_base_model_name_before_the_size_or_tag(self):
        res = {f: c for f, c, _ in fam("ollama", OLLAMA)}
        assert res["gemma4"] == 2 and res["llama3.1"] == 2 and res["qwen3"] == 1 and res["qwen3-coder"] == 1
        assert res["deepseek-r1"] == 1 and res["gpt-oss"] == 1
        assert "Other" not in res and "Llama" not in res

    def test_a_hugging_face_style_id_uses_its_last_segment(self):
        assert fam("ollama", [["ollama/hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF:Q4_K_M", "x", ""]])[0][0] == "Llama-3.2"

    def test_latest_tag_is_the_newest_pick_inside_a_family(self):
        g = {x["family"]: x for x in groups("ollama", OLLAMA)}
        assert g["gemma4"]["newest"] == "ollama/gemma4:latest" and g["gemma4"]["ids"][0] == "ollama/gemma4:latest"

    def test_local_models_share_a_family_across_sizes_and_custom_files_get_their_own(self):
        res = {f: (c, n) for f, c, n in fam("local", LOCAL)}
        assert res["gemma-4"][0] == 3 and res["gemma-3"][0] == 1 and res["tiny"][0] == 1 and res["my-cool-model"][0] == 1
        assert res["gemma-4"][1] == "local/gemma-4-12b", "same version: the provider's own first one stays on top"
        assert fam("local", LOCAL)[0][0] == "gemma-4", "newest family first"

    def test_other_pcs_group_by_the_model_not_the_nickname(self):
        res = {f: c for f, c, _ in fam("remote", REMOTE)}
        assert res["gemma-4"] == 2 and res["qwen"] == 2 and res["gemma-3"] == 1 and res["llama-3"] == 1


class TestTheRestOfTheProviders:
    def test_nano_gpt(self):
        res = {f: c for f, c, _ in fam("nano", OTHERS["nano"])}
        assert res == {"Mini": 1, "Qwen": 2, "GPT": 1, "GLM": 2}

    def test_openai(self):
        res = {f: c for f, c, _ in fam("openai", OTHERS["openai"])}
        assert res == {"Mini": 1, "GPT": 4, "O3": 1}

    def test_deepseek(self):
        res = fam("deepseek", OTHERS["deepseek"])
        assert {f: c for f, c, _ in res} == {"Flash": 2, "Pro": 1, "DeepSeek": 2, "R1": 1}

    def test_glm(self):
        res = fam("glm", OTHERS["glm"])
        assert {f: c for f, c, _ in res} == {"GLM": 4, "Flash": 1, "Air": 1}

    def test_typesafe_jev(self):
        res = fam("typesafe", OTHERS["typesafe"])
        assert {f: c for f, c, _ in res} == {"Jev": 6}
        g = groups("typesafe", OTHERS["typesafe"])[0]["ids"]
        assert g[0] == "typesafe/jev-1.13" and g[-1] in ("typesafe/jev-latest", "typesafe/jev-preview")

    def test_kilo_keeps_the_family_not_the_maker(self):
        res = {f: c for f, c, _ in fam("kilo", OTHERS["kilo"])}
        assert res == {"Sonnet": 1, "Opus": 1, "GPT": 1, "Flash": 1, "Grok": 1, "Kilo Auto": 1}

    def test_a_name_that_leaves_nothing_is_other_and_goes_last(self):
        res = fam("kilo", [["a/x", "5.5", ""], ["a/y", "Sonnet 5", ""], ["a/z", "V2", ""]])
        assert res[-1][0] == "Other" and res[-1][1] == 2 and res[0][0] == "Sonnet"


# ---- the 5-model rule and the rendered rows --------------------------------------------------------------------
def render(pid, key, rows, extra=""):
    row_html = "(r, inGroup) => '<div class=\"model\">' + r[0] + (inGroup ? '' : '|' + r[2]) + '</div>'"
    res = _run({}, f"{extra}\nconsole.log(JSON.stringify(renderModelListHTML({json.dumps(pid)}, {json.dumps(key)}, {json.dumps(rows)}, {row_html})));")
    return res


class TestThresholdAndRendering:
    def test_five_or_fewer_stay_a_flat_list(self):
        html = render("claude", "claude", CLAUDE[:5])
        assert "mgroup" not in html and html.count('class="model"') == 5 and "|The best mix of speed and smarts." in html

    def test_six_or_more_are_grouped(self):
        html = render("claude", "claude", CLAUDE[:6])
        assert html.count('class="mgroup') >= 2 and "mg-row" in html

    def test_exactly_the_boundary(self):
        assert "mgroup" not in render("ollama", "o", OLLAMA[:5]) and "mgroup" in render("ollama", "o", OLLAMA[:6])

    def test_every_group_starts_closed(self):
        assert "mgroup open" not in render("claude", "claude", CLAUDE)

    def test_the_group_row_has_the_family_count_description_and_a_copy_newest_with_its_id_in_the_tooltip(self):
        html = render("claude", "claude", CLAUDE)
        assert '<span class="mg-fam">Sonnet<span class="mg-count">4 models</span></span>' in html
        assert '<span class="mg-desc" title="The best mix of speed and smarts.">The best mix of speed and smarts.</span>' in html
        assert 'title="Copies claude-sonnet-5-5"' in html and ">Copy newest</button>" in html
        assert "newest claude" not in html.lower(), "no 'newest <id>' text on the row"

    def test_a_single_model_says_1_model(self):
        assert '<span class="mg-count">1 model</span>' in render("claude", "claude", CLAUDE)

    def test_clicking_copy_newest_does_not_open_the_group(self):
        assert "event.stopPropagation(); copyModel(this, 'claude-sonnet-5-5')" in render("claude", "claude", CLAUDE)

    def test_rows_inside_a_group_leave_the_description_off(self):
        html = render("claude", "claude", CLAUDE)
        assert '<div class="model">claude-sonnet-5-5</div>' in html and "claude-sonnet-5-5|" not in html

    def test_a_newest_model_with_no_description_shows_nothing_there(self):
        rows = [[f"x/m-{i}", f"Alpha {i}", ""] for i in range(1, 7)]
        html = render("kilo", "k", rows)
        assert '<span class="mg-desc"></span>' in html

    def test_the_pinned_group_has_no_copy_newest(self):
        html = render("codex", "codex|default", CODEX)
        pinned = html.split('class="mgroup')[1]
        assert "Pinned" in pinned and "Copy newest" not in pinned and "mg-desc\"></span>" in pinned

    def test_what_the_player_opened_is_remembered_and_lists_dont_mix(self):
        html = render("claude", "claude", CLAUDE, "openModelGroups.add('claude|Opus');")
        opus = [c for c in html.split('<div class="mgroup') if "Opus<span" in c][0]
        sonnet = [c for c in html.split('<div class="mgroup') if "Sonnet<span" in c][0]
        assert opus.startswith(" open") and not sonnet.startswith(" open")
        other_list = render("codex", "codex|default", CODEX, "openModelGroups.add('claude|Opus');")
        assert "mgroup open" not in other_list

    def test_toggling_a_row_updates_the_remembered_set(self):
        out = _run({}, """
          const group = { classList: { _o: false, toggle() { this._o = !this._o; return this._o; } } };
          const row = { parentElement: group };
          toggleModelGroup(row, 'a|b'); const afterOpen = [...openModelGroups];
          toggleModelGroup(row, 'a|b'); console.log(JSON.stringify([afterOpen, [...openModelGroups]]));""")
        assert out == [["a|b"], []]

    def test_hostile_text_cannot_break_the_row(self):
        rows = [["x/a", "<b>Evil</b> 1", "<img src=x onerror=alert(1)>"]] + [[f"x/{i}", f"Evil {i}", ""] for i in range(2, 7)]
        html = render("kilo", "k", rows)
        assert "<img" not in html and "&lt;img" in html


# ---- wired into the real page ------------------------------------------------------------------------------------
def test_the_page_uses_the_shared_grouping_for_providers_and_codex(proxy_module, test_client):
    html = test_client.get("/").text
    assert "function mgGroupRows" in html and ".mgroup" in html
    assert html.count("renderModelListHTML(") >= 3          # the generic panel + both Codex lists
    # the logic is defined before the first place the page renders a provider panel
    assert html.index("const openModelGroups") < html.index("selectProvider(setupHashProvider() || DEFAULT_PROVIDER")
    assert 'codexModelRowHTML(m, active.id, inGroup)' in html


def test_the_grouping_script_is_plain_strings_with_no_python_braces_to_escape():
    for part in (mg.MODEL_GROUPS_LOGIC_JS, mg.MODEL_GROUPS_RENDER_JS, mg.MODEL_GROUPS_CSS):
        assert isinstance(part, str) and len(part) > 100
