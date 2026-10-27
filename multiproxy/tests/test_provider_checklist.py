"""Every provider is listed in every place that lists providers (2026-10-24).

The single source: the Providers tab's own list, `_dashboard_providers()` in multiproxy.py (one entry per provider the proxy supports), with each provider's picker group from
`_PICKER_INFO` ("subscription", "key" or "pc"). Each test below is one place that must know every provider; a miss fails with ONE line naming the provider and the place, and
pointing at docs/ADDING-A-PROVIDER.md. Where a place legitimately does not list a provider, it is in that test's ALLOW dict, with the reason -- never a skip.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

import help_content

ROOT = Path(__file__).resolve().parent.parent
GUIDE = "docs/ADDING-A-PROVIDER.md"



def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _miss(pid: str, place: str) -> str:
    return f"provider '{pid}' is missing from {place} -- see {GUIDE}"


@pytest.fixture()
def providers(proxy_module):
    """{id: dashboard entry} for every provider the Providers tab lists."""
    return {p["id"]: p for p in asyncio.run(proxy_module._dashboard_providers())}


@pytest.fixture()
def kinds(proxy_module, providers):
    """{id: "subscription" | "key" | "pc"} from the picker table; a provider with no picker entry is reported by test_picker_knows_every_provider."""
    return {pid: proxy_module._PICKER_INFO.get(pid, ("key",))[0] for pid in providers}


def _check(providers, place, ok, allow=None):
    allow = allow or {}
    bad = [pid for pid in providers if pid not in allow and not ok(pid)]
    assert not bad, "\n".join(_miss(pid, place) for pid in bad)
    stale = [pid for pid in allow if pid not in providers or ok(pid)]
    assert not stale, f"'{place}' allow-list entry {stale} is stale (the provider is gone, or the place now lists it): remove it from the allow-list"


def test_picker_knows_every_provider(proxy_module, providers):
    _check(providers, "_PICKER_INFO in multiproxy.py (the Providers picker's group, pill and tag)", lambda pid: pid in proxy_module._PICKER_INFO)


def test_help_has_a_page_for_every_provider(providers):
    """help_content.PAGES names the page (and the provider it belongs to); docs/help/help-content.md holds the page itself."""
    page_ids = {meta[1]: title for title, meta in help_content.PAGES.items() if meta[1]}
    topics = {t["id"] for t in help_content.load_topics()}
    _check(providers, "the Help tab (help_content.PAGES and a matching page in docs/help/help-content.md)",
           lambda pid: pid in page_ids and help_content.PAGES[page_ids[pid]][0] in topics)


def test_which_ai_table_lists_every_provider(providers):
    md = _text("docs/help/help-content.md")
    section = md.split("### Which AI should I use?", 1)[1].split("\n---", 1)[0]
    page_ids = {meta[1]: title for title, meta in help_content.PAGES.items() if meta[1]}
    _check(providers, 'the "Which AI should I use?" table in docs/help/help-content.md',
           lambda pid: pid in page_ids and page_ids[pid].split(" (")[0] in section)


def test_get_started_lists_every_provider(test_client, providers):
    html = test_client.get("/").text
    groups = re.search(r"const GS_GROUPS = \[(.*?)\n\];", html, re.S).group(1)
    listed = set(re.findall(r"'([a-z0-9]+)'", " ".join(re.findall(r"ids: \[(.*?)\]", groups))))
    _check(providers, "the Get Started provider groups (GS_GROUPS in get_started_ui.py)", lambda pid: pid in listed)


def test_key_providers_get_the_start_here_steps(test_client, providers, kinds):
    html = test_client.get("/").text
    keyed = set(re.findall(r"'([a-z0-9]+)'", re.search(r"const SU_KEYED = \[(.*?)\];", html).group(1)))
    _check(providers, "the page's SU_KEYED list in multiproxy.py (the 'Start here' steps for key providers)", lambda pid: pid in keyed,
           allow={pid: "not a key provider" for pid in providers if kinds[pid] != "key"})


def test_example_config_has_every_key(providers, kinds):
    cfg = json.loads(_text("config.example.json"))
    _check(providers, "config.example.json (a `<id>_api_key` line)", lambda pid: f"{pid}_api_key" in cfg,
           allow={pid: "no saved key: a subscription or a program on this device or another one" for pid in providers
                  if kinds[pid] != "key" and pid not in ("ollama",)})


def test_example_ini_has_every_request_line(proxy_module, providers):
    """Providers reached over HTTP have a [Concurrency] line (multiproxy.ini.example); the ones that run as a program here or on another device do not."""
    ini = _text("multiproxy.ini.example")
    ini_keys = {pid: key for key, pid, _label, _d in proxy_module._REQUEST_LINE_PROVIDERS}
    _check(providers, "_REQUEST_LINE_PROVIDERS in multiproxy.py and its [Concurrency] line in multiproxy.ini.example",
           lambda pid: pid in ini_keys and re.search(rf"^{re.escape(ini_keys[pid])}\s*=", ini, re.M),
           allow={"typesafe": "TypeSafe is one slow lane with its own wait handling, not an HTTP pool (no request line)",
                  "local": "runs on this device", "remote": "other devices set their own limits",
                  "codex": "a program run once per request, not an HTTP pool", "geminicli": "a program run once per request, not an HTTP pool",
                  "grok": "a program run once per request, not an HTTP pool",
                  "perplexity": "decisions only: one quick call per decision under the player's own 'move on after' limit, never a chat pool"})


def test_short_names_cover_every_provider(proxy_module, providers):
    _check(providers, "_PROVIDER_SHORT in multiproxy.py (the name on Speeds, Routing and the Overview)", lambda pid: pid in proxy_module._PROVIDER_SHORT,
           allow={"typesafe": "named 'TypeSafe' by its own branch where the name is looked up", "remote": "shown by each device's own nickname"})


def test_every_service_has_a_cost_class(proxy_module, providers):
    """Routing's Free / Included / Paid chip reads _COST_CLASS_BY_PROVIDER by the short name."""
    names = {pid: proxy_module._PROVIDER_SHORT.get(pid) or ("TypeSafe" if pid == "typesafe" else None) for pid in providers}
    _check(providers, "_COST_CLASS_BY_PROVIDER in multiproxy.py (Routing's cost chip)", lambda pid: names[pid] in proxy_module._COST_CLASS_BY_PROVIDER,
           allow={"remote": "another device's nickname is the name; its cost class comes from where it runs"})


def test_model_prefixes_are_checked_in_the_detection_test(providers):
    src = _text("tests/test_model_detection.py")
    _check(providers, "ALL_PREFIXES in tests/test_model_detection.py", lambda pid: re.search(rf'"{pid}":\s*"{pid}/"', src) is not None,
           allow=_PREFIX_ALLOW)


_PREFIX_ALLOW = {   # providers whose prefix is not simply `<id>/`, or that another test file covers
    "claude": "Claude has no prefix (a plain model name)", "glm": "its prefix is glmzai/",
            "typesafe": "tests/test_typesafe*.py covers it", "remote": "the prefix is the device's nickname", "lmstudio": "its prefix is lmstudio/ and has its own test class",
            "cloudflare": "key sources are covered by tests/test_key_sources.py", "mistral": "key sources are covered by tests/test_key_sources.py",
            "groq": "key sources are covered by tests/test_key_sources.py",
            "perplexity": "decisions only: it has no chat prefix and is never offered for chat (tests/test_perplexity_decisions.py covers it)"}


def test_fixtures_clear_every_saved_key(providers, kinds):
    """conftest.py's isolation fixture must blank each key, or a developer's real key leaks into the tests."""
    src = _text("tests/conftest.py")
    keyed = {pid for pid in providers if kinds[pid] == "key" or pid == "ollama"}
    _check(providers, "tests/conftest.py (the fixture that blanks `<id>_api_key`, or the key-source loop)",
           lambda pid: f'"{pid}_api_key"' in src or pid in ("cloudflare", "mistral", "groq"),
           allow={pid: "no saved key" for pid in providers if pid not in keyed})


def test_readme_lists_every_provider(providers, kinds, proxy_module):
    readme = _text("README.md")
    _check(providers, "README.md (the provider list under Choose your AI)", lambda pid: proxy_module._PICKER_INFO[pid][1].lower() in readme.lower())


def test_readme_credits_the_services(providers, kinds, proxy_module):
    """A service MultiProxy talks to is credited in README's Credits section (original author, original URL). Older providers are credited through the project the idea came from;
    every service that has a key or an account has its line (the GAP entries were closed on 2026-10-24)."""
    credits = _text("README.md").split("## 🤝 Credits", 1)[1].lower()
    _check(providers, "README.md's Credits section", lambda pid: proxy_module._PICKER_INFO[pid][1].lower() in credits or pid in ("geminicli", "codex"),
           allow={"remote": "own code"})


def test_technical_md_mentions_every_provider(providers, proxy_module):
    tech = _text("TECHNICAL.md").lower()
    _check(providers, "TECHNICAL.md (a section or line for the provider)", lambda pid: proxy_module._PICKER_INFO[pid][1].lower() in tech or pid in tech,
           allow={})


@pytest.mark.parametrize("pid", ["continuum", "xai", "groq", "perplexity"])
def test_the_log_redaction_knows_the_key_shape(proxy_module, pid):
    """A provider whose key starts with a recognisable word must have that shape in _CREDENTIAL_TOKEN_RE. (Providers with a plain random key have no shape to match.)"""
    sample = {"continuum": "cont_sk_Ab1234Cd5678Ef9012Gh3456", "xai": "xai-Ab1234Cd5678Ef9012Gh3456", "groq": "gsk_Ab1234Cd5678Ef9012Gh3456", "perplexity": "pplx-Ab1234Cd5678Ef9012Gh3456"}[pid]
    assert "REDACTED" in proxy_module._redact_text(f"key {sample} end"), f"provider '{pid}': its key shape is not masked in the log -- see {GUIDE}"
