"""A Claude model typed with a dot (claude-sonnet-5.5) is sent as the dash form Anthropic knows (claude-sonnet-5-5) (2026-10-24)."""
import logging


def test_a_dotted_claude_version_becomes_the_dash_form(proxy_module):
    for typed, want in (("claude-sonnet-5.5", "claude-sonnet-5-5"), ("claude-haiku-4.5", "claude-haiku-4-5"), ("claude-opus-4.1", "claude-opus-4-1")):
        assert proxy_module._resolve_model_name(typed) == want


def test_other_providers_and_right_names_keep_what_they_have(proxy_module):
    for name in ("claude-sonnet-5-5", "claude-haiku-4-5-20251001", "gemini/gemini-3.8-flash", "openai/gpt-5.5", "codex/gpt-5.5", "deepseek/deepseek-v3.2", "kilo/anthropic/claude-haiku-4.5"):
        want = proxy_module._resolve_model_name(name)
        assert (want == name) or "/" in name, name
    assert proxy_module._resolve_model_name("openai/gpt-5.5") == "gpt-5.5"
    assert proxy_module._resolve_model_name("kilo/anthropic/claude-haiku-4.5") == "anthropic/claude-haiku-4.5"
    assert proxy_module._resolve_model_name("claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert proxy_module._resolve_model_name("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"


def test_the_log_line_comes_once_per_name(proxy_module, caplog):
    proxy_module._claude_dots_told.clear()
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            proxy_module._resolve_model_name("claude-sonnet-5.5")
    lines = [r.getMessage() for r in caplog.records if "written with a dot" in r.getMessage()]
    assert lines == ["Claude model claude-sonnet-5.5 was written with a dot; used claude-sonnet-5-5."]
