"""Tests for the model-prefix detection functions and their strip_X_prefix counterparts.

Adapted from rhinos0608/skyrimnet-codex-proxy's own tests/test_model_detection.py (colon-delimited
prefixes there, slash-delimited here -- otherwise the same shape: each detector must match its own
prefix, reject every OTHER provider's prefix, and reject empty/partial/malformed input).

This is exactly the class of bug this project already hit twice for real: the Nano-GPT Quick Test
dropdown wrongly excluding a real, concrete `nano/provider/model`-shaped row (a "contains a slash"
heuristic that didn't know about legitimate nested-slash model ids), and `is_openrouter_model`
needing its own exclusion list extended every time a new prefixed provider was added. A suite like
this, run before it needs to say something is broken, is the whole point.
"""

ALL_PREFIXES = {
    "glm": "glmzai/",
    "nano": "nano/",
    "openai": "openai/",
    "deepseek": "deepseek/",
    "kilo": "kilo/",
    "gemini": "gemini/",
    "ollama": "ollama/",
    "local": "local/",
    "codex": "codex/",
    "copilot": "copilot/",
    "geminicli": "geminicli/",
    "grok": "grok/",
    "xai": "xai/",
    "continuum": "continuum/",
}


class TestIsGlmModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_glm_model("glmzai/glm-4.6") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_glm_model("GLMZAI/glm-4.6") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "glm":
                continue
            assert proxy_module.is_glm_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_glm_model("") is False

    def test_rejects_partial_prefix(self, proxy_module):
        assert proxy_module.is_glm_model("glmza/glm-4.6") is False

    def test_rejects_bare_claude_model(self, proxy_module):
        assert proxy_module.is_glm_model("claude-sonnet-5") is False


class TestIsNanoModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_nano_model("nano/gpt-4o-mini") is True

    def test_nested_namespace(self, proxy_module):
        assert proxy_module.is_nano_model("nano/qwen/qwen3.8-27b-uncensored") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_nano_model("NANO/gpt-4o-mini") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "nano":
                continue
            assert proxy_module.is_nano_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_nano_model("") is False

    def test_rejects_plain_openrouter_model(self, proxy_module):
        assert proxy_module.is_nano_model("openai/gpt-4o") is False


class TestIsOpenaiModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_openai_model("openai/gpt-5.5") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_openai_model("OpenAI/gpt-5.5") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "openai":
                continue
            assert proxy_module.is_openai_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_openai_model("") is False


class TestIsDeepseekModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_deepseek_model("deepseek/deepseek-v4-pro") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_deepseek_model("DeepSeek/deepseek-v4-pro") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "deepseek":
                continue
            assert proxy_module.is_deepseek_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_deepseek_model("") is False


class TestIsKiloModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_kilo_model("kilo/kilo-auto/balanced") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_kilo_model("KILO/anthropic/claude-sonnet-5") is True

    def test_rejects_raw_gateway_model_without_prefix(self, proxy_module):
        assert proxy_module.is_kilo_model("anthropic/claude-sonnet-5") is False

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "kilo":
                continue
            assert proxy_module.is_kilo_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_kilo_model("") is False


class TestIsGeminiModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_gemini_model("gemini/gemini-3.8-flash") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_gemini_model("GEMINI/gemini-3.8-flash") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "gemini":
                continue
            assert proxy_module.is_gemini_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_gemini_model("") is False

    def test_rejects_bare_claude_model(self, proxy_module):
        assert proxy_module.is_gemini_model("claude-sonnet-5") is False


class TestIsOllamaModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_ollama_model("ollama/llama3.1:8b") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_ollama_model("OLLAMA/llama3.1:8b") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "ollama":
                continue
            assert proxy_module.is_ollama_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_ollama_model("") is False


class TestIsCodexModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_codex_model("codex/gpt-5.5") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_codex_model("CODEX/gpt-5.5") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "codex":
                continue
            assert proxy_module.is_codex_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_codex_model("") is False


class TestIsCopilotModel:
    """Detection function exists (and stays correct) even though Copilot isn't wired into routing
    yet -- see TECHNICAL.md's own "GitHub Copilot -- NOT implemented, and why" section."""

    def test_simple_model(self, proxy_module):
        assert proxy_module.is_copilot_model("copilot/gpt-4.1") is True

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "copilot":
                continue
            assert proxy_module.is_copilot_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_copilot_model("") is False


class TestIsGeminicliModel:
    def test_simple_model(self, proxy_module):
        assert proxy_module.is_geminicli_model("geminicli/gemini-3.8-flash-high") is True

    def test_case_insensitive(self, proxy_module):
        assert proxy_module.is_geminicli_model("GEMINICLI/gemini-3.8-flash-high") is True

    def test_never_confused_with_the_api_key_route(self, proxy_module):
        """geminicli/ (subscription, Antigravity CLI) and gemini/ (API key) are two separate
        routes on purpose -- confirm neither detector matches the other's prefix."""
        assert proxy_module.is_geminicli_model("gemini/gemini-3.8-flash") is False
        assert proxy_module.is_gemini_model("geminicli/gemini-3.8-flash-high") is False

    def test_rejects_other_providers(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            if name == "geminicli":
                continue
            assert proxy_module.is_geminicli_model(f"{prefix}some-model") is False, name

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_geminicli_model("") is False


class TestIsUnclaimedSlashModel:
    """A '/'-containing name that no prefix-routed provider claims used to go to OpenRouter; MultiProxy
    no longer connects to it, so such names get a clear error instead. This must still reject every
    prefixed provider's own ids, even though every one of them also contains a '/'."""

    def test_standard_model(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("meta-llama/llama-3-70b") is True

    def test_anthropic_model(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("anthropic/claude-3.5-sonnet") is True

    def test_rejects_every_prefixed_provider(self, proxy_module):
        for name, prefix in ALL_PREFIXES.items():
            assert proxy_module.is_unclaimed_slash_model(f"{prefix}some-model") is False, name

    def test_rejects_bare_claude_model_no_slash(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("claude-sonnet-5") is False

    def test_rejects_empty_string(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("") is False

    def test_nano_gpt_nested_slash_is_still_nano(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("nano/qwen/qwen3.8-27b-uncensored") is False

    def test_kilo_nested_slash_is_still_kilo(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("kilo/anthropic/claude-sonnet-5") is False

    def test_gemini_is_not_unclaimed(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("gemini/gemini-3.8-flash") is False

    def test_geminicli_is_not_unclaimed(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("geminicli/gemini-3.8-flash-high") is False

    def test_deepseek_is_not_unclaimed(self, proxy_module):
        assert proxy_module.is_unclaimed_slash_model("deepseek/deepseek-v4-pro") is False


class TestStripPrefixFunctions:
    """strip_X_prefix must remove exactly its own prefix and nothing else, and be a no-op on a
    string that was never prefixed in the first place (defensive -- callers shouldn't rely on that,
    but it should never silently mangle an unrelated string)."""

    def test_strip_glm_prefix(self, proxy_module):
        assert proxy_module.strip_glm_prefix("glmzai/glm-4.6") == "glm-4.6"

    def test_strip_nano_prefix_preserves_nested_slash(self, proxy_module):
        assert proxy_module.strip_nano_prefix("nano/qwen/qwen3.8-27b-uncensored") == "qwen/qwen3.8-27b-uncensored"

    def test_strip_openai_prefix(self, proxy_module):
        assert proxy_module.strip_openai_prefix("openai/gpt-5.5") == "gpt-5.5"

    def test_strip_deepseek_prefix(self, proxy_module):
        assert proxy_module.strip_deepseek_prefix("deepseek/deepseek-v4-pro") == "deepseek-v4-pro"

    def test_strip_kilo_prefix_preserves_nested_slash(self, proxy_module):
        assert proxy_module.strip_kilo_prefix("kilo/anthropic/claude-sonnet-5") == "anthropic/claude-sonnet-5"

    def test_strip_gemini_prefix(self, proxy_module):
        assert proxy_module.strip_gemini_prefix("gemini/gemini-3.8-flash") == "gemini-3.8-flash"

    def test_strip_ollama_prefix(self, proxy_module):
        assert proxy_module.strip_ollama_prefix("ollama/llama3.1:8b") == "llama3.1:8b"

    def test_strip_codex_prefix(self, proxy_module):
        assert proxy_module.strip_codex_prefix("codex/gpt-5.5") == "gpt-5.5"

    def test_strip_copilot_prefix(self, proxy_module):
        assert proxy_module.strip_copilot_prefix("copilot/gpt-4.1") == "gpt-4.1"

    def test_strip_geminicli_prefix(self, proxy_module):
        assert proxy_module.strip_geminicli_prefix("geminicli/gemini-3.8-flash-high") == "gemini-3.8-flash-high"

    def test_strip_is_noop_on_unprefixed_string(self, proxy_module):
        assert proxy_module.strip_glm_prefix("some-other-model") == "some-other-model"
