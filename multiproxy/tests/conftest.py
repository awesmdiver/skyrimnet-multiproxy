"""Shared test fixtures for proxy tests.

Adapted from rhinos0608/skyrimnet-codex-proxy's own tests/conftest.py (MIT-licensed reference
project this proxy's OpenAI/Kilo/Ollama/Codex support was researched against) -- same strategy,
using a fake `shutil.which` at import time.

CLAUDE_PATH used to be a HARD requirement (`raise RuntimeError("claude CLI not found on PATH")` at
import time if missing) -- fixed 2026-09-13 (a real bug, reported by a user: someone with Codex set
up but no working Claude subscription couldn't use the proxy AT ALL). CLAUDE_PATH is optional now,
exactly like CODEX_PATH already was, so `_fake_which` below could return None for everything and
still import cleanly -- it still fakes a non-None "claude" path by default purely so every test that
doesn't care about Claude's own availability (the large majority of this suite) keeps exercising
the "Claude CLI present" branch it was already written against. `test_claude_availability.py`
specifically exercises the "no Claude CLI at all" and "Claude present but broken" paths, including
a real (not faked) fresh import with `shutil.which` returning None for everything.
"""
import copy
import hashlib
import os
import sys
from unittest.mock import patch

import pytest

# Importing multiproxy must never write into the real folder (it copies an old proxy.ini to multiproxy.ini once, for real players).
os.environ["MULTIPROXY_NO_INI_MIGRATION"] = "1"

# Ensure proxy is importable from the project root regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import speed_store  # noqa: E402


def _fake_which(name, mode=None, path=None):
    return "/fake/path/claude" if name == "claude" else None


# Import proxy at COLLECTION time (module level), before any fixture runs -- mirrors the reference
# project's own comment: some test modules could import proxy indirectly before a fixture ever
# executes, so the mock has to be in place before the very first `import proxy` anywhere.
with patch("shutil.which", side_effect=_fake_which):
    import multiproxy as proxy  # noqa: F401,E402

# Captured once, before the autouse _mock_ollama_tags fixture below ever replaces
# proxy._fetch_ollama_tags -- a test that wants to exercise the REAL fetch/cache/timeout logic
# (test_ollama_discovery.py) calls this reference directly instead of the module attribute, which
# stays mocked for every other test.
REAL_FETCH_OLLAMA_TAGS = proxy._fetch_ollama_tags
REAL_OLLAMA_KINDS = proxy._ollama_kinds
REAL_FETCH_LMSTUDIO_MODELS = proxy._fetch_lmstudio_models      # (same idea for LM Studio's model list)


def _hash_file(path):
    """sha256 of a file's bytes, or None if it doesn't exist -- used to prove the real config.json
    is untouched, without ever printing its (real, secret) contents on a mismatch."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


# proxy.CONFIG_FILE is only ever the REAL path at collection time / between tests -- the
# _isolate_config_file fixture below monkeypatches it away for the duration of each test and
# monkeypatch reverts it afterward. Captured here, once, before any test can run.
_REAL_CONFIG_FILE = proxy.CONFIG_FILE
_REAL_CONFIG_HASH_AT_START = _hash_file(_REAL_CONFIG_FILE)

# Same story for speed_history.db (2026-09-20) -- confirmed real, not just theoretical, the same
# day this was added: a full suite run before this isolation fixture existed wrote 12KB into the
# real file next to multiproxy.py in a single pass (every chat_completions()-dispatching test in the
# whole suite calls _record_speed, which now persists too).
_REAL_SPEED_HISTORY_DB = proxy.SPEED_HISTORY_DB
_REAL_SPEED_HISTORY_HASH_AT_START = _hash_file(_REAL_SPEED_HISTORY_DB)


def pytest_sessionfinish(session, exitstatus):
    """Belt-and-braces: fail loudly if the real config.json or speed_history.db changed at all
    during this run, on top of the autouse isolation fixtures below that are supposed to prevent
    that in the first place. Exists because a fixture existing is exactly what a future change
    could accidentally break (e.g. a test that builds its own TestClient without going through the
    proxy_module/test_client fixtures) -- this check doesn't rely on any single test remembering to
    ask for isolation."""
    after_config = _hash_file(_REAL_CONFIG_FILE)
    if after_config != _REAL_CONFIG_HASH_AT_START:
        session.warn(pytest.PytestWarning(
            f"\n\n*** REAL config.json CHANGED DURING THIS TEST RUN: {_REAL_CONFIG_FILE} ***\n"
            "A test wrote to the live config file instead of an isolated one -- this must never "
            "happen. Restore it from a backup immediately and do not run pytest again until the "
            "cause is fixed.\n"
        ))
        session.exitstatus = 1
    after_speed_db = _hash_file(_REAL_SPEED_HISTORY_DB)
    if after_speed_db != _REAL_SPEED_HISTORY_HASH_AT_START:
        session.warn(pytest.PytestWarning(
            f"\n\n*** REAL speed_history.db CHANGED DURING THIS TEST RUN: {_REAL_SPEED_HISTORY_DB} ***\n"
            "A test wrote to the live speed history file instead of an isolated one -- this must "
            "never happen. Do not run pytest again until the cause is fixed.\n"
        ))
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _downloads_do_not_wait_between_tries(monkeypatch):
    """A download that fails tries again after 3, 10 and 30 seconds (download_retry.py). No test waits that long: tests that check the waits pass their own."""
    import download_retry
    monkeypatch.setattr(download_retry, "WAITS", (0.01, 0.01, 0.01))


@pytest.fixture(autouse=True)
def _local_guard_off_by_default():
    """The suite drives the app through an in-memory client whose Host is "t" and which sends no X-MultiProxy header, so the website guard (local_guard.py) is off for it;
    tests/test_local_guard.py turns it on for its own checks."""
    import local_guard
    local_guard.ENABLED = False
    yield
    local_guard.ENABLED = True


@pytest.fixture(autouse=True)
def _no_engine_probes(monkeypatch):
    """After a request to Ollama or LM Studio is given up on, MultiProxy asks that engine for one word to learn why (a real request to a real engine, and a real model load). Never from a
    test: the director's own Ollama is on this PC. A test of the probe itself turns it back on with a stand-in engine. What was known about a model never carries into the next test."""
    monkeypatch.setattr(proxy, "ENGINE_PROBES", False)
    proxy._probing.clear()
    proxy._trouble._by.clear()
    yield
    proxy._trouble._by.clear()


@pytest.fixture(autouse=True)
def _isolate_config_file(monkeypatch, tmp_path):
    """Redirect every /config/*-key write to a throwaway file under pytest's own tmp_path, instead
    of the real config.json next to multiproxy.py.

    Real bug, real consequence: before this fixture existed,
    test_dashboard_posting_empty_key_clears_each_provider (2d1de03) posted through the real
    /config/*-key routes, which call _save_config() -> writes proxy.CONFIG_FILE directly. With no
    redirect, that IS the director's live config.json (the one Skyrim is pointed at) --
    running pytest silently overwrote it to {}, erasing real OpenRouter/GLM/Nano-GPT/OpenAI keys
    with no way to recover them. autouse=True so a future test can't forget this the same way this
    one did -- isolation shouldn't depend on every test author remembering to ask for it.
    """
    monkeypatch.setattr(proxy, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(proxy._last_sent, "path", str(tmp_path / "routing-last-sent.json"))      # what SkyrimNet last sent for a job is saved to a file: never the real one
    monkeypatch.setattr(proxy._last_sent, "_by", {})


@pytest.fixture(autouse=True)
def _forget_poll_caches():
    """The short-lived answers poll_cache keeps (the Routing view's AI list and history, Codex's sign-in, PATH lookups, config.json's text)
    are module-wide: never from one test into the next."""
    import poll_cache
    poll_cache.forget()
    poll_cache.forget_which()
    proxy._config_reader.forget()
    yield
    poll_cache.forget()
    poll_cache.forget_which()
    proxy._config_reader.forget()


@pytest.fixture(autouse=True)
def _isolate_settings_page(monkeypatch, tmp_path):
    """The Settings page writes multiproxy.ini (Start and stop with Skyrim) and reads GitHub for a new version: never the real ini, never
    the real network, and the watcher / restart that a setting would start never really starts."""
    ini = tmp_path / "multiproxy.ini"
    ini.write_text("[General]\n; comment kept\nAutoCloseWithSkyrim = false\n", encoding="utf-8")
    monkeypatch.setattr(proxy, "_PROXY_INI", str(ini))
    monkeypatch.setattr(proxy, "AUTO_CLOSE_WITH_SKYRIM", False)

    async def _no_release():
        return None
    monkeypatch.setattr(proxy, "_fetch_latest_release", _no_release)
    monkeypatch.setattr(proxy, "_ensure_skyrim_watcher", lambda: None)
    monkeypatch.setattr(proxy, "_restart_self", lambda port: None)
    monkeypatch.setattr(proxy, "_update_state", {"state": "unknown", "latest": None, "url": proxy.PLUGIN_DOWNLOAD_URL, "checkedAt": None})


@pytest.fixture(autouse=True)
def _forget_learned_extra_fields():
    """Fields a provider's API refused are remembered for the run (see proxy._learn_unknown_fields): never from one test into the next."""
    proxy._learned_drops.clear()
    proxy._logged_drops.clear()
    yield
    proxy._learned_drops.clear()
    proxy._logged_drops.clear()


@pytest.fixture(autouse=True)
def _forget_named_jobs():
    """Jobs seen asked for by their exact name are remembered for the run (proxy._named_seen): never from one test into the next."""
    proxy._named_seen.clear()
    proxy._seen_how.clear()
    yield
    proxy._named_seen.clear()
    proxy._seen_how.clear()


@pytest.fixture(autouse=True)
def _isolate_speed_history_db(monkeypatch, tmp_path):
    """Redirect every persisted speed-history write to a throwaway file under pytest's own
    tmp_path, instead of the real speed_history.db next to multiproxy.py -- same reasoning, same
    autouse=True, as _isolate_config_file above. Confirmed real (2026-09-20): a full suite run
    before this fixture existed wrote real rows into the live file. Re-runs _init_speed_history_db
    against the new path so the table actually exists there -- the real one only ever gets created
    once, at multiproxy.py's own import time, against the real path."""
    db_path = str(tmp_path / "speed_history_test.db")
    monkeypatch.setattr(proxy, "SPEED_HISTORY_DB", db_path)
    proxy._init_speed_history_db()
    # Rows are saved by a background writer in production (speed_store.py). Tests read the file right after a call, so every queued row is written before
    # the call returns here, and Speeds answers are never reused between two reads. tests/test_speed_store.py tests the real, asynchronous behaviour.
    speed_store.set_sync(True)
    yield
    speed_store.stop()
    speed_store.set_sync(False)


@pytest.fixture(autouse=True)
def _mock_ollama_tags(monkeypatch):
    """Ollama's own GET /api/tags is a real network call -- autouse-mock it to a deterministic
    "daemon not reachable" default for every test, exactly like _isolate_config_file does for
    config.json, so the suite never depends on (or is slowed/flaked by) a real local daemon. A real
    one happens to run on the dev machine this was built on, which would otherwise make
    dashboard/`/v1/models` tests silently pass or fail depending on what's actually installed there.

    Individual tests override per scenario with their own
    `monkeypatch.setattr(proxy_module, "_fetch_ollama_tags", ...)`; a test that wants the REAL
    fetch/cache/timeout logic (test_ollama_discovery.py) calls `REAL_FETCH_OLLAMA_TAGS` above
    directly instead, since that reference was captured before this fixture ever ran.
    """
    async def _not_reachable(base_url, api_key=None, *, force=False):
        return None
    monkeypatch.setattr(proxy, "_fetch_ollama_tags", _not_reachable)

    async def _no_kinds(base_url, tag_models):
        return {}                   # (Ollama's POST /api/show is a real call to a real daemon too: "it did not say what the models are")
    monkeypatch.setattr(proxy, "_ollama_kinds", _no_kinds)
    proxy._models_forget()          # (/v1/models remembers each source's list for a few seconds: every test starts with none remembered)


    async def _lmstudio_not_reachable(base_url, *, force=False):
        return None
    monkeypatch.setattr(proxy, "_fetch_lmstudio_models", _lmstudio_not_reachable)
    monkeypatch.setattr(proxy, "lmstudio_base_url", proxy.LMSTUDIO_DEFAULT_BASE_URL)      # (a route can change this global: every test starts at the default)
    # ...and no real `lms` / LM Studio install on the dev device leaks into the LM Studio pill either: by default there is none
    from types import SimpleNamespace
    monkeypatch.setattr(proxy, "_lmstudio_adapter", lambda base: SimpleNamespace(cli=None, _app_installed=lambda: False))


@pytest.fixture()
def proxy_module(monkeypatch, tmp_path):
    """Return the already-imported proxy module, with every provider-level API key reset to None
    (module-level globals can leak real values from config.json, or from a previous test, otherwise)
    and _codex_path()/_agy_path() forced to return None so no test accidentally depends on whether
    THIS machine happens to have a real Codex/Antigravity CLI on PATH -- unlike CLAUDE_PATH (a
    one-time snapshot taken at import time), both are genuinely live `shutil.which(...)` checks
    (2026-09-19/2026-09-20 fixes -- see their own docstrings in multiproxy.py for why), so without this
    override every test that doesn't explicitly patch them would do a real, machine-dependent PATH
    lookup.

    CLAUDE_PATH is deliberately NOT reset to None here, unlike every other provider -- it stays at
    whatever `_fake_which` gave it at collection time (a fake, non-None path), so the large majority
    of this suite keeps exercising the "Claude CLI present" branch it was already written against
    without every single test needing to care about Claude's own availability. multiproxy.py itself
    handles CLAUDE_PATH being None fine now (dashboard()/health()/capture_auth() all check for it) --
    test_claude_availability.py exercises that path specifically, via a real fresh import rather
    than mutating this shared module's CLAUDE_PATH (which would leak into every OTHER test that
    runs afterward in the same session, since monkeypatch reverts per-test but this fixture would
    otherwise need to reset it every time).
    """
    monkeypatch.setattr(proxy, "glm_api_key", None)
    monkeypatch.setattr(proxy, "nano_api_key", None)
    monkeypatch.setattr(proxy, "openai_api_key", None)
    monkeypatch.setattr(proxy, "deepseek_api_key", None)
    monkeypatch.setattr(proxy, "typesafe_api_key", None)
    monkeypatch.setattr(proxy, "perplexity_api_key", None)
    monkeypatch.setattr(proxy, "kilo_api_key", None)
    monkeypatch.setattr(proxy, "gemini_api_key", None)
    monkeypatch.setattr(proxy, "xai_api_key", None)
    monkeypatch.setattr(proxy, "continuum_api_key", None)
    monkeypatch.setattr(proxy, "_ks_keys", {s.id: None for s in proxy.key_sources.SOURCES})        # Cloudflare, Mistral, Groq
    monkeypatch.setattr(proxy, "ollama_api_key", None)
    # Local models (2026-09-30): a fresh service per test, with its pid file under tmp_path, so no
    # test can see the dev machine's real models folder, start a real llama-server, or write a pid
    # file next to multiproxy.py. (It reads config.json through proxy._load_config, already isolated.)
    monkeypatch.setattr(proxy, "local_svc", proxy.local_models.LocalModels(
        proxy._load_config, proxy._save_config, proxy.logger, str(tmp_path / "local_models_pids.json"),
        gpu_detector=lambda: None))
    # Other devices (part 2): fresh per test too, tied to that test's local_svc, never opening a real
    # network door or touching the real config.
    monkeypatch.setattr(proxy, "remote_svc", proxy.other_pcs.RemoteServers(
        proxy._load_config, proxy._save_config, proxy.logger, lambda n: proxy._nickname_problem(n),
        lambda: proxy._forbidden_loopback_ports(), client_factory=proxy.courier_client.CourierClient))
    monkeypatch.setattr(proxy, "gpu_monitor_svc", proxy.gpu_monitor.GpuMonitor(
        proxy.local_svc, proxy._load_config, proxy._save_config, proxy.logger, reader=lambda: None))
    monkeypatch.setattr(proxy, "codex_providers", [])
    monkeypatch.setattr(proxy, "codex_pinned_models", {})
    monkeypatch.setattr(proxy, "_codex_path", lambda: None)
    # Same reasoning as _codex_path above -- this dev machine has a real, signed-in Antigravity CLI
    # installed (confirmed live 2026-09-15), so leaving _agy_path() un-reset would make every test
    # in this suite implicitly depend on that real, machine-specific install/login state.
    monkeypatch.setattr(proxy, "_agy_path", lambda: None)
    # Same again for Grok: this dev machine has xAI's `grok` program installed (2026-10-03), so neither the PATH lookup nor its own
    # default install folder may leak into a test.
    monkeypatch.setattr(proxy, "_grok_path", lambda: None)
    monkeypatch.setattr(proxy, "_grok_default_exe", lambda: None)
    monkeypatch.setattr(proxy, "_grok_models_cache", (0.0, None))
    # Speeds tab (2026-09-19) globals -- same leak risk as every provider key above: a test that
    # links a Skyrim path, changes thresholds, or records a speed_tracker entry would otherwise
    # bleed into every test that runs after it in the same session (speed_tracker especially, since
    # every /v1/chat/completions call in the WHOLE suite writes into that one module-level singleton).
    monkeypatch.setattr(proxy, "skyrim_install_path", "")
    monkeypatch.setattr(proxy, "speed_thresholds", copy.deepcopy(proxy.DEFAULT_SPEED_THRESHOLDS))
    monkeypatch.setattr(proxy, "starts_talking_thresholds", copy.deepcopy(proxy.DEFAULT_STARTS_TALKING_THRESHOLDS))
    monkeypatch.setattr(proxy, "speed_tracker", proxy._SpeedTracker())
    monkeypatch.setattr(proxy, "_role_chip_cache", {"ts": 0.0, "chips": {}})
    # _skyrimnet_dir_cache (2026-09-22): keyed by the linked root string, so a genuinely different
    # tmp_path per test wouldn't normally collide -- reset anyway, same defensive reasoning as
    # _role_chip_cache above, in case a test ever reuses a literal path string.
    monkeypatch.setattr(proxy, "_skyrimnet_dir_cache", {"root": None, "resolved": None})
    # Claude's live model list (2026-09-30): same leak risk -- a test that fetches a list must not change the default model for later tests.
    monkeypatch.setattr(proxy, "_claude_models_cache", {"at": 0.0, "value": None})
    monkeypatch.setattr(proxy, "_claude_models_fail", {"failing": False, "at": 0.0, "login": "", "why": "", "sign_in": False})     # (a failed list is remembered: start every test clean)
    monkeypatch.setattr(proxy, "_tray_providers_cache", {"at": 0.0, "value": []})
    monkeypatch.setattr(proxy, "_claude_models_last_good", None)
    # Raydonn's fork upgrades (2026-09-27): the tag fixer's learned tag vocabulary, the request-line
    # state and the three multiproxy.ini-driven settings are all module-level, so a test that changes one
    # would bleed into every later test -- and a real multiproxy.ini on the dev machine (e.g. a GLM limit
    # of 6) would otherwise change what every test in the suite sees. Pin them to the built-in
    # defaults, except that no provider has a limit (tests that want a line set their own).
    monkeypatch.setattr(proxy, "_tag_vocab", {"internalthought": "internal_thought"})
    monkeypatch.setattr(proxy, "_request_lines", {})
    monkeypatch.setattr(proxy, "REQUEST_LIMITS", {pid: 0 for _, pid, _, _ in proxy._REQUEST_LINE_PROVIDERS})
    monkeypatch.setattr(proxy, "QUEUE_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr(proxy, "FIX_NPC_TAGS", True)
    # Voice-cue rewriting (2026-09-27, Beta 25): off by default; its cache of the linked install's
    # Higgs.yaml tag list is keyed by path + mtime, but reset anyway for the same defensive reason.
    monkeypatch.setattr(proxy, "FIX_HIGGS_VOICE_TAGS", False)
    monkeypatch.setattr(proxy, "_higgs_tags_cache", {"path": None, "mtime": None, "value": None})
    monkeypatch.setattr(proxy, "LOG_FULL_REPLIES", False)
    # "When the port is busy, offer to stop the old proxy and start fresh" (2026-09-30): pin to the
    # real default so a real multiproxy.ini on the dev machine (e.g. ReplaceRunningProxy=always) can't
    # change what any test not explicitly about this setting sees.
    monkeypatch.setattr(proxy, "REPLACE_RUNNING_PROXY_POLICY", "ask")
    return proxy


@pytest.fixture()
def test_client(proxy_module):
    """FastAPI TestClient that does NOT run the lifespan (no subprocess spawning or real network
    calls on "startup") -- instantiating TestClient without using it as a context manager skips
    FastAPI's lifespan startup/shutdown hooks entirely."""
    from fastapi.testclient import TestClient
    client = TestClient(proxy_module.app, raise_server_exceptions=True)
    client.get("/")
    proxy_module._claude_models_fail.update(failing=False, at=0.0, login="", why="", sign_in=False)      # (that first page load may have noted a failed Claude list: a test starts without it)
    return client


REAL_READ_NANO_DECISION_MODELS = proxy._read_nano_decision_models

# A few tests import a FRESH copy of multiproxy (test_claude_availability: "a real fresh import"). That import registers its own decision providers over the shared
# decision_providers.REGISTRY, and the fresh copy's Ollama reader is NOT the mocked one: later tests then asked the dev machine's real Ollama (found 2026-10-06: the full
# suite failed ~28 decision tests only when run whole). The real set is kept here and put back around every test.
import decision_providers as _dp_registry_owner  # noqa: E402
_REAL_DECISION_PROVIDERS = dict(_dp_registry_owner.REGISTRY)


@pytest.fixture(autouse=True)
def _no_decision_model_lists(monkeypatch):
    """Nano-GPT's model list is a real network call (a real key may sit in the dev machine's config): every test starts with no decision models read and with that call mocked
    to "none". test_decision_providers.py restores REAL_READ_NANO_DECISION_MODELS and points it at a stand-in server."""
    import decision_providers
    decision_providers.REGISTRY.clear()
    decision_providers.REGISTRY.update(_REAL_DECISION_PROVIDERS)
    decision_providers.CACHE.clear()

    async def _none():
        return []
    monkeypatch.setattr(proxy, "_read_nano_decision_models", _none)
    yield
    decision_providers.REGISTRY.clear()
    decision_providers.REGISTRY.update(_REAL_DECISION_PROVIDERS)
    decision_providers.CACHE.clear()


@pytest.fixture
def real_read_nano_decision_models():
    return REAL_READ_NANO_DECISION_MODELS
