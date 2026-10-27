"""The built-in list of Claude models (used when Anthropic's own list cannot be read) names the current ones (2026-10-26; source: platform.claude.com/docs/en/about-claude/models/overview)."""
import routing
import routing_view

CURRENT = {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}


def test_the_fallback_list_holds_only_the_current_models_with_a_line_each(proxy_module):
    rows = proxy_module.CLAUDE_STATIC_FALLBACK_MODELS
    assert {r[0] for r in rows} == CURRENT and len(rows) == 4
    assert all(r[2] for r in rows), "every one has its one-line description"
    assert not any("4-5" in r[0] or "4.5" in r[1] for r in rows)


def test_a_dated_id_still_finds_its_line_and_an_old_name_keeps_its_line_when_the_live_list_returns_it(proxy_module):
    note = proxy_module._claude_model_note
    assert note("claude-haiku-5-5-20260101") == note("claude-haiku-5-5") != ""
    assert note("claude-haiku-4-5-20251001") == "The fastest model with near-frontier intelligence"
    assert note("claude-haiku-5-5") != note("claude-haiku-4-5")


def test_a_route_naming_the_old_haiku_is_gone_when_only_the_fallback_list_is_offered(proxy_module):
    reachable = [{"id": r[0], "owned_by": "anthropic"} for r in proxy_module.CLAUDE_STATIC_FALLBACK_MODELS]
    settings = routing.clean_settings({"routes": {"decisions": {"ais": ["claude-haiku-4-5"], "how": "order"}}})
    view = routing_view.build(settings=settings, table={}, status={"resting": [], "checks": {}, "working": {}}, reachable=reachable, remotes={}, stats={}, seen={}, costs=lambda j, k: None,
                              classify=lambda a: (routing.CLASS_INCLUDED, "subscription"), key_of=lambda a: a, name_of=lambda a: (a, "Claude"), now_resting={})
    # A model only Decisions names no longer gets a card of its own on Routing (removed on purpose, 4.1), so the old Haiku draws nothing.
    assert not any(x["id"] == "claude-haiku-4-5" for x in view["ais"])
