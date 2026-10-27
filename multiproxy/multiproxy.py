"""
SkyrimNet MultiProxy -- OpenAI-compatible local proxy for SkyrimNet, routing across 11 AI backends.

Origin: galanx/Claude-SkyrimNet-Proxy (MIT), which rode a Claude Max subscription via Claude CLI.
See TECHNICAL.md for provider-specific quirks and architectural history; this header maps the file.

Routing (by model-ID prefix, evaluated in chat_completions via _ROUTES):
  Prefix               Provider       Auth / Transport
  -------------------  -------------  --------------------------------------------------
  (no prefix)          Claude         Claude CLI login (captured auth -> direct aiohttp)
  glmzai/MODEL         GLM (z.ai)     API key
  nano/provider/model  Nano-GPT       API key
  openai/MODEL         OpenAI         API key
  deepseek/MODEL       DeepSeek       API key
  kilo/provider/model  Kilo Gateway   API key
  gemini/MODEL         Gemini         API key (Google's own OpenAI-compatible endpoint)
  ollama/MODEL         Ollama         Local daemon (no key) or Ollama Cloud (API key)
  lmstudio/MODEL       LM Studio      LM Studio's local server on this device (no key)
  codex/MODEL          Codex          ChatGPT login via Codex CLI subprocess (default provider)
  codex/id/MODEL       Codex          Saved provider `id` (own key/local daemon), same CLI subprocess
  geminicli/MODEL      Antigravity    Google AI Pro/Ultra login via Antigravity CLI (`agy`) subprocess
  grok/MODEL           Grok           SuperGrok subscription via xAI's `grok` CLI subprocess
  xai/MODEL            xAI            API key (api.x.ai, OpenAI-style)
  continuum/MODEL      Continuum      Continuum plan key (api.continuumcode.ai, OpenAI-style)

Adding a provider:
  1. Define constants, is_X_model(), and strip_X_prefix().
  2. Implement call functions (or reuse _call_openai_compatible_*).
  3. Register in _ROUTES and _dashboard_providers().

Architecture & Request Flow:
  - Claude: at startup, an interceptor on port 9999 (INTERCEPTOR_PORT) runs ONE 'claude --print'
    from a clean temp dir and captures its auth headers plus a minimal body template (tools
    stripped, extended thinking off; the clean dir keeps the system-reminder ~350 chars vs ~16K).
    Per-request: direct aiohttp call to api.anthropic.com; Anthropic SSE is translated into OpenAI
    chat.completion.chunk events.
  - OpenAI-compatible: OpenAI, DeepSeek, Kilo, Ollama, and Gemini share _call_openai_compatible_direct
    and _call_openai_compatible_streaming. GLM and Nano-GPT use dedicated handlers.
    Param filtering varies by provider: Kilo/Ollama/Gemini each block a few unsupported fields
    (three different sets -- checked per provider, not assumed identical), GLM whitelists its own
    supported set, OpenAI and DeepSeek pass extra params through unfiltered.
  - Codex: invokes the Codex CLI as a subprocess per request (no public direct API).
  - Antigravity CLI (geminicli/MODEL): invokes the real `agy` CLI as a subprocess per request, same
    shape as Codex -- but with genuine incremental streaming (--output-format stream-json), unlike
    Codex's own honest single-chunk fake. Google's successor to Gemini CLI's now-discontinued
    personal-account login.
  - Dynamic models: Ollama lists models live from /api/tags (local) or Ollama Cloud; Gemini lists
    live from its own OpenAI-compat /models; Antigravity CLI lists live from `agy models`. None of
    these three is a static list.

Concurrency, Auth & Resilience:
  - Persistent aiohttp session reuses TCP+TLS across requests (~200-500ms saved per call).
  - Soft startup: a missing Claude CLI or failed capture doesn't stop startup; other routes work.
  - Single-flight refresh: if Claude auth expires, exactly ONE capture_auth() runs while concurrent
    requests wait. Re-captures are throttled by RECAPTURE_COOLDOWN to prevent subprocess storms.
  - Error normalization: upstream errors map to {"error": {...}} with the upstream's own status.
    Streaming works the same way (SkyrimNet Beta 25's smart routing needs a real failure status):
    a failure before the first event is a real HTTP error, a failure after it just ends the
    stream with [DONE] -- error wording is never written into a reply. See _prime_stream.
  - Raydonn's fork upgrades (2026-09-27, see TECHNICAL.md): NPC tag fixer (FixNpcTags), retry of
    dropped upstream connections, bounded too-long-chat trimming, per-provider request line
    ([Concurrency] in multiproxy.ini) and an optional full-reply console log (LogFullReplies).

Dashboard & Config (GET /):
  - Web UI built from _dashboard_providers(). Keys saved to config.json (plaintext, gitignored).
  - Quick Test runs against ready providers only. /health and /v1/models expose provider state.

Safety & Auxiliaries:
  - Redaction: credentials are masked in logs via _redact_credential and _RedactingFormatter.
  - Test isolation: an autouse fixture in tests/conftest.py keeps tests off the real config.json.
  - multiproxy.ini toggles optional file logging (multiproxy.log) and SkyrimWatcher (auto-exit with game).
"""

import argparse
import asyncio
import contextvars
import hashlib
import atexit
import json
import sys
import time
from urllib.parse import urlsplit
import uuid
import logging
import logging.handlers
import shutil
import os
import signal
import errno
import copy
import tempfile
import re
import base64
import configparser
import socket
import subprocess
import app_icon  # the app icon files (the dashboard's favicon, the tray's picture)
import hidden_process  # every program started from here goes through it: no console window flashing up
import sqlite3
import threading
import weakref
import webbrowser
from html import escape as html_escape
import datetime
import urllib.request
import urllib.error
from collections import deque
from contextlib import asynccontextmanager, suppress
from typing import Callable, Optional, Union

import uvicorn
import model_groups_ui  # long model lists grouped by family (strings spliced into the page)
import model_transfer  # sending local models from this device to a Courier device ("download once")
import courier_update_sender  # sending the signed Courier update from this device to a Courier device (the Update Courier buttons)
import local_models_ui  # the dashboard's Local models card (strings spliced into the page)
import local_settings_ui  # the Local models tab's settings screens (local_settings_ui.py)
import gpu_choice  # which cards this device has (the memory guard finds the card a model uses)
import gpu_guard  # stops the built-in chat model before Skyrim runs out of graphics memory (a Settings switch, on by default)
import gpu_monitor  # the Local models tab's graphics memory monitor (and Skyrim's learned busiest point)
import add_a_pc  # Add a PC: one address opened on the other PC pairs it (a small door, open only while an address is live)
import other_pcs  # local models, part 2: other devices on your home network -- see that file's docstring
import safe_paths
import courier_adapters  # LM Studio's server start for the LM Studio provider (the same code the Courier uses)
import courier_client  # the Other devices tab's live connection to each Courier (the small program on those devices)
import help_content  # the Help tab's pages, built from docs/help/help-content.md
import notices  # credits and license notices (Settings, About)
import help_ui  # the dashboard's Help tab (strings spliced into the page)
import settings_ui  # the dashboard's Settings page behind the cog
import setup_file  # Settings > Share your setup: Save my setup / Load a setup
import gpu_choice_ui  # Settings card: which graphics card each local AI uses
import gpu_choice_wiring  # its routes and saved choices (/config/gpu-choice)
import get_started_ui  # the five-step first-run flow (also reopened from Settings)
import set_me_up  # Set me up: one button installs the engine and a small set of models that fit this PC (set_me_up.py)
import set_me_up_ui
import start_here_ui  # the hide button every Start here box shares (and Settings' Show all again)
import live_feed  # the Live view: request lines and reasoning as they happen, in memory only (live_feed.py)
import tested_settings  # tested settings per job and model family (settings_presets.json)
import fine_tune  # what SkyrimNet last sent for a job and the per-job overrides (fine_tune.py)
import job_names  # the names and descriptions of SkyrimNet's jobs, read from SkyrimNet itself (job_names.py)
import speeds_time  # the time breakdown, per job and model (speeds_time.py)
import speeds_time_ui  # the Speeds tab's Time breakdown view (speeds_time_ui.py)
import speeds_local_ui  # the Speeds tab's Local models side by side view (speeds_local_ui.py)
import prompt_size  # how big SkyrimNet's prompts are for each job, against the model's context (prompt_size.py)
import prompt_size_ui  # the Speeds tab's Prompt size view and the Local models warning (prompt_size_ui.py)
import selection_ui  # text can be selected and copied: a redraw and a click leave a selection alone (selection_ui.py)
import speed_store  # the Speeds history file: background writer, WAL, indexes, shared answer cache (speed_store.py)
import speed_queries  # the Speeds tab's reads, done inside SQLite (speed_queries.py)
import model_kinds  # what kind each model is (chat, decisions, vision, embeddings), from each provider's own list (model_kinds.py)
import quick_stats  # the Overview's quick stats of the last hour, and the count of requests in flight (quick_stats.py)
atexit.register(speed_store.stop)   # a normal shutdown writes the rows still queued
import routing_view  # what the Routing tab shows (routing_view.py)
import routing_ui  # the dashboard's Routing tab (routing_ui.py)
import jump_ui  # the "Jump to" row every long tab shares (jump_ui.py)
import routing  # which job a request is, which AIs may answer it, hand-over (routing.py)
import ai_costs  # what a request cost, and local timings (ai_costs.py)
import request_facts  # what SkyrimNet sent along, and one record of what happened to each request (log lines; later response headers)
import claude_courier as _cc  # the clean text blocks Anthropic takes and the cap on cache markers (shared with the Courier's Claude)
import thinking_off  # SkyrimNet's "reasoning off" said the way Ollama and LM Studio understand it (thinking_off.py)
import engine_trouble  # why a local engine (Ollama, LM Studio) did not answer, in one plain sentence; what an empty reply is (engine_trouble.py)
import loop_lag  # the opt-in probe that says where anything blocks the server (MULTIPROXY_LOOP_LAG_MS)
import poll_cache  # keeps config.json, PATH lookups and slow program starts off the event loop (2026-10-02)
import offloop_log  # the log file is written by its own thread, not on the event loop (2026-10-02)
import gc_tuning  # frees the collector from re-walking the start-up objects (2026-10-02)
import address_rows_ui  # Settings > SkyrimNet connection: the voice and quick decisions addresses (strings spliced into the page)
import tab_desc_ui  # the description every tab opens with
import quicktest_ui  # Quick Test's chat, as a panel on the dashboard (strings spliced into the page)
import quick_sections_ui  # Quick Test as a list of collapsible sections
import skyrimnet_check  # Check it like SkyrimNet: how the three parts are judged (skyrimnet_check.py)
import skyrimnet_check_samples as check_samples  # its built-in sample prompts and expected answers
import skyrimnet_check_ui  # its card on the Quick Test tab (strings spliced into the page)
import decision_test_ui  # ... and its card on the Quick Test tab
import decision_test  # Quick Test for Quick decisions (Jev): the scenarios and how an answer reads
import overview  # the Overview page's data: every machine, model and service with its status and numbers (overview.py)
import overview_ui  # the dashboard's Overview tab, the first page (strings spliced into the page)
import models_off  # the Settings switch "Don't run models on this PC": its words and its saved flag (models_off.py)
import models_tab  # the Models tab's data: every downloaded model on each PC, where it is used (models_tab.py)
import models_ui  # the dashboard's Models tab (strings spliced into the page)
import skyrimnet_finder  # finding SkyrimNet's folder, also for Mod Organizer 2 (skyrimnet_finder.py)
import fix_it  # Overview's "Check everything" button: a growing list of checks that name what is wrong (fix_it.py)
import decision_ui  # the dashboard's Decisions tab (strings spliced into the page)
import decision_providers  # providers that answer a quick decision with a decision model of their own (Ollama, Nano-GPT, ...): one registry, see decision_providers.py
import decision_wiring  # the local decision model: engine manager + who answers a decision (see decision_engine.py)
import voice_ui  # the dashboard's Voice tab (strings spliced into the page)
import claude_ui  # Claude's row on the Other devices tab (a string spliced into the page)
import claude_wiring  # Claude on another device: the dashboard routes its row calls
import copilot_ui  # Copilot's row on the Other devices tab (a string spliced into the page)
import copilot_wiring  # Copilot on another device: the dashboard routes its row calls
import codex_install  # the ChatGPT (Codex) card's Install button: fetches Codex for this PC (codex_install.py)
import codex_install_ui  # that card's install part (a string spliced into the page)
import voice_wiring  # Higgs voice on this device: engine manager + SkyrimNet's voice endpoint (see voice_service.py)
import field_log  # (the Settings switch "Log what SkyrimNet sends": voice_service and stt_capture ask it before writing a request's field line)
import stt_capture  # (body_summary: a non-text request body is logged as its type and size only)
import voice_service  # (its describe_request writes a voice request into the log by field names only)
import local_channel  # the local channel the Skyrim mod and the tray app use (see that file's docstring)
import settings_files  # multiproxy.ini / config.json edits and the Start with Windows entry, shared with the tray app
import lmstudio_ui  # the LM Studio panel's address box and Start button
import claude_trouble  # the line under an orange Claude card
import other_pcs_ui  # the dashboard's Other devices tab (strings spliced into the page)
import local_engine_api
import local_engine_settings
import local_engine_sync
import ollama_keepwarm  # keeps a route's first-choice local Ollama model loaded (ollama_keepwarm.py)
import model_matches  # is a catalog model already in Ollama or LM Studio (model_matches.py)
import courier_models  # the finder for model files already on a device (also used by the Other devices page)
import local_models  # local models, part 1 (2026-09-30): llama.cpp on this device -- see that file's docstring
from fastapi import FastAPI, HTTPException, Request
import key_sources
import local_guard
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict
from aiohttp import web
import aiohttp

# Shared by _RedactingFormatter below AND _redact_text (2026-09-20, for the error-response
# logging middleware) -- one credential pattern, not two independently-maintained copies. sk-...
# covers Nano-GPT (and other sk-... keys); the second alternative covers z.ai/GLM's own key shape (a 32-char
# hex id, a literal dot, then a random suffix -- e.g. 0123...cdef.FakeFake), which
# doesn't start with "sk-" so the first pattern alone would miss it.
# Widened 2026-10-06 (security review): Gemini (AIza...), xAI, Groq, Continuum (cont_sk_..., added 2026-10-24), Hugging Face, GitHub keys, Bearer values, and a JSON "api_key"/"token"/"authorization" field holding anything.
_CREDENTIAL_TOKEN_RE = re.compile(r'\bsk-[A-Za-z0-9_-]{10,}|\b[0-9a-f]{32}\.[A-Za-z0-9]{8,}\b|\bAIza[0-9A-Za-z_-]{20,}|\b(?:xai|gsk|hf|ghp|github_pat|cont_sk|pplx)[-_][A-Za-z0-9_-]{16,}'
                                  r'|(?i:\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}|(?i:"(?:key|api[_-]?key|token|authorization|access[_-]?token)"\s*:\s*)"[^"]{6,}"')


def _script_json(value) -> str:
    """JSON that is safe inside an inline <script>: the characters < > & and the two line separators become escapes, so text from a provider can never close the script
    (security review 2026-10-06)."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _redact_text(text: str) -> str:
    """Masks any credential-shaped substring in arbitrary free-form text (a request/response
    body, say) -- the same pattern _RedactingFormatter already applies to file-log lines, reused
    here rather than reinvented, so a token can never leak through either path."""
    return _CREDENTIAL_TOKEN_RE.sub('***REDACTED***', text)


class _RedactingFormatter(logging.Formatter):
    """File-log formatter that masks API key tokens so they never reach disk."""
    def format(self, record: logging.LogRecord) -> str:
        return _redact_text(super().format(record))


class _SuppressSuccessfulAccessLogs(logging.Filter):
    """Uvicorn prints one raw access-log line per request. Signal when something's wrong, pure
    noise for routine 200s -- the dashboard alone polls /stats/speeds every few seconds, drowning
    out anything actually worth seeing. Drop plain-200 records; let everything else (errors,
    redirects, whatever) through unchanged. (2026-09-20, reported live.)"""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            status_code = record.args[-1]  # uvicorn's access record args end with the status code
        except (TypeError, IndexError):
            return True
        return str(status_code) != "200"


# Bumped by hand each release (2026-09-20) -- same manual pattern the SKSE plugin's own
# skse-project.json "Version" field already follows on its side; no attempt at deriving this
# automatically from anywhere.
PROXY_VERSION = "4.1.0"
import build_info as _build_info          # (the build stamp the package carries, and the one plain line that says which version and build this is)
BUILD_STAMP = _build_info.read_stamp(os.path.dirname(os.path.abspath(__file__)))

# The SKSE plugin passes its own build version via --plugin-version=X.Y.Z when IT launches this
# script (see proxy_launcher.cpp's own LaunchProxy) -- shown on the dashboard next to
# PROXY_VERSION so the two ever silently drifting apart (a real bug that happened: the plugin
# shipped v3.0.0 while still reporting v2.5.0 in its own log, because only one of two places that
# needed a version bump got one) is visible at a glance instead of discovered live, later, in a
# support thread. None is the normal, expected case for any launch that doesn't go through the
# plugin -- start-proxy.bat, a bare `py multiproxy.py`, this test suite -- never treated as an error.
# parse_known_args (not parse_args) so an unrecognized argument in argv -- pytest's own CLI flags
# included, since this runs at import time -- is ignored rather than crashing on import.
_arg_parser = argparse.ArgumentParser(add_help=False)
_arg_parser.add_argument("--plugin-version", default=None)
PLUGIN_VERSION: Optional[str] = _arg_parser.parse_known_args()[0].plugin_version

# Read multiproxy.ini before logging so EnableLogging can control the file handler
SETTINGS_DIR = settings_files.settings_dir(os.path.dirname(os.path.abspath(__file__)))    # config.json, multiproxy.ini, the logs: next to the program unless the installer says otherwise
_PROXY_INI = os.path.join(SETTINGS_DIR, "multiproxy.ini")


def _migrate_legacy_ini(ini_path: str, legacy_path: str) -> Optional[str]:
    """This program used to be proxy.py with proxy.ini beside it (renamed 2026-10-01 so it can't be confused with the original proxy or
    forks). Nobody's setup breaks: when multiproxy.ini isn't there but an old proxy.ini is, its choices are copied into multiproxy.ini
    once (comments and all). The old file is never touched or deleted. Returns the plain line to log, or None when nothing was done."""
    if os.path.exists(ini_path) or not os.path.exists(legacy_path):
        return None
    try:
        with open(legacy_path, "rb") as f:
            data = f.read()
        with open(ini_path, "wb") as f:
            f.write(data)
    except OSError:
        return None
    return f"Your settings moved: {os.path.basename(legacy_path)} was copied to {os.path.basename(ini_path)} (the old file is still there, untouched)."


_INI_MIGRATION_NOTE: Optional[str] = (None if os.environ.get("MULTIPROXY_NO_INI_MIGRATION") else
                                      _migrate_legacy_ini(_PROXY_INI, os.path.join(os.path.dirname(_PROXY_INI), "proxy.ini")))
_proxy_ini_cfg = configparser.ConfigParser()
_proxy_ini_cfg.read(_PROXY_INI)
AUTO_CLOSE_WITH_SKYRIM: bool = _proxy_ini_cfg.getboolean("General", "AutoCloseWithSkyrim", fallback=False)
ENABLE_LOGGING: bool = _proxy_ini_cfg.getboolean("General", "EnableLogging", fallback=False)
# Off by default, like every other multiproxy.ini flag -- see lifespan()'s own use of this for why it's
# read once at import time rather than live: same pattern AUTO_CLOSE_WITH_SKYRIM already uses, and
# the same reason tests must never see this true against a real, unmocked webbrowser.open().
OPEN_BROWSER_ON_START: bool = _proxy_ini_cfg.getboolean("General", "OpenBrowserOnStart", fallback=False)
# Off by default -- see capture_prompt_if_enabled()'s own docstring for what gets written and what
# gets stripped before it does. Added 2026-09-16 so real SkyrimNet request shapes can be captured
# and turned into test fixtures (tests/fixtures/skyrimnet_shapes.py), instead of every fixture in
# this repo being hand-written and small enough to never have caught the Antigravity streaming bug.
CAPTURE_PROMPTS: bool = _proxy_ini_cfg.getboolean("General", "CapturePrompts", fallback=False)
# fallback=True -- the ONE deliberate exception to every other flag on this page defaulting to
# False. Those were all opt-IN from day one; speed tracking (and the Speeds tab) was already
# shipped and already on for every existing user, with no flag at all, before this was added
# (2026-09-20). Defaulting this to False would silently turn OFF a feature already live the
# moment someone updates -- a real regression, not a neutral opt-in default.
ENABLE_SPEED_TRACKING: bool = _proxy_ini_cfg.getboolean("General", "EnableSpeedTracking", fallback=True)
# Raydonn's fork upgrades (2026-09-27). FixNpcTags is ON by default: it only ever touches text that
# already looks like broken markup (a tag with a stray space, a voice tag written as "[laughs]"),
# and text with none of that comes back byte-for-byte unchanged. LogFullReplies is OFF by default:
# it prints every reply in full, which is noisy and puts NPC dialogue in the log file.
FIX_NPC_TAGS: bool = _proxy_ini_cfg.getboolean("General", "FixNpcTags", fallback=True)
# On by default, same reasoning as FixNpcTags above (queue: catch-broken-replies, 2026-09-29): it
# only ever strips wrapper noise (code fences, stray text around the JSON, trailing commas) around
# a reply a job's own prompt already asked for as JSON -- see _expects_json_reply/_repair_json_reply.
# Off still counts what WOULD have happened on the Speeds tab; it just stops changing what SkyrimNet
# actually receives.
FIX_BROKEN_REPLIES: bool = _proxy_ini_cfg.getboolean("General", "FixBrokenReplies", fallback=True)
# Voice-cue rewriting is separate and OFF by default (2026-09-27, SkyrimNet Beta 25): the tags an NPC
# should use depend on that NPC's voice engine, which the proxy can't see -- see the voice-cue block.
FIX_HIGGS_VOICE_TAGS: bool = _proxy_ini_cfg.getboolean("General", "FixHiggsVoiceTags", fallback=False)
LOG_FULL_REPLIES: bool = _proxy_ini_cfg.getboolean("General", "LogFullReplies", fallback=False)
# "ask" (default), "always", or "never" -- what to do when a port this proxy needs is already held
# by ANOTHER MultiProxy (never anything else -- see _identify_port_holder). Any other/misspelled
# value falls back to "ask" rather than silently behaving like "never", the same "never crash on a
# typo" reasoning every other multiproxy.ini read here already follows.
REPLACE_RUNNING_PROXY_POLICY: str = _proxy_ini_cfg.get("General", "ReplaceRunningProxy", fallback="ask").strip().lower()
if REPLACE_RUNNING_PROXY_POLICY not in ("ask", "always", "never"):
    REPLACE_RUNNING_PROXY_POLICY = "ask"
CAPTURE_DIR = os.path.join(SETTINGS_DIR, "prompt-captures")
_SKYRIM_PROCESSES: list[str] = [
    p.strip()
    for p in _proxy_ini_cfg.get("General", "SkyrimProcess", fallback="SkyrimSE.exe,SkyrimVR.exe").split(",")
    if p.strip()
]

# Enable ANSI VT sequences on the Windows console so uvicorn's colour codes render correctly.
# Consoles created by CreateProcess (e.g. via the SKSE plugin) have VT processing off by default.
try:
    import ctypes as _ctypes
    _k32 = _ctypes.windll.kernel32
    for _std in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
        _h = _k32.GetStdHandle(_std)
        _mode = _ctypes.c_ulong()
        if _h and _k32.GetConsoleMode(_h, _ctypes.byref(_mode)):
            _k32.SetConsoleMode(_h, _mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
except Exception:
    pass

# Temporary console for pre-startup messages; uvicorn's log_config replaces this at run time.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger("proxy")
if _INI_MIGRATION_NOTE:
    logger.info(_INI_MIGRATION_NOTE)


# Crash diagnostics (2026-09-20) -- real, confirmed-live incident: this proxy is normally launched
# by the SkyrimNetMultiProxy SKSE plugin via CreateProcess(CREATE_NEW_CONSOLE), which gives it its
# own console window rather than a hidden one -- but that console is tied to the process's own
# lifetime, so it closes the INSTANT the process exits, whether that's a clean exit or a crash. A
# genuine uncaught exception normally prints a traceback straight to that console right before
# exiting -- which means the traceback flashes and vanishes with the window, leaving multiproxy.log
# (and SkyrimNetMultiProxy.log) with no trace at all that anything went wrong, just silence. Both
# hooks below route an uncaught exception through `logger` FIRST, so it lands in multiproxy.log (which
# survives after the console is gone) before the process actually dies -- diagnosing a future
# silent death without needing to already know the cause today.
def _log_uncaught_exception(exc_type, exc_value, exc_tb):
    logger.critical("UNCAUGHT EXCEPTION in main thread -- proxy is about to exit", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _log_uncaught_thread_exception(args) -> None:
    # An uncaught exception in a background thread (e.g. SkyrimWatcher) never kills the whole
    # process by default -- Python just prints to stderr and lets that one thread die -- but it's
    # exactly as invisible once the console is gone, so it gets the same treatment.
    logger.critical(f"UNCAUGHT EXCEPTION in thread {args.thread.name!r}", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


sys.excepthook = _log_uncaught_exception
threading.excepthook = _log_uncaught_thread_exception

# Build a custom uvicorn log_config that keeps uvicorn's pretty console output while also
# tee-ing every message to the log file (plain text, no ANSI colour codes) when EnableLogging is
# on. ALWAYS built now (2026-09-20) -- never left as bare None the way it used to be when
# EnableLogging was off -- specifically so the 200-suppression filter below can be declared INSIDE
# the dict itself (dictConfig's own "filters" section), not added imperatively via
# some_logger.addFilter(...) after the fact. Real trap checked directly rather than assumed: a
# filter added imperatively to logging.getLogger("uvicorn.access") BEFORE uvicorn.run() calls
# logging.config.dictConfig(...) was verified (2026-09-20) to actually survive dictConfig in this
# exact Python/uvicorn combination -- but that's an observed behavior, not something dictConfig's
# own docs guarantee, so declaring the filter IN the config dict (correct by construction,
# independent of that behavior) is what ships.
from uvicorn.config import LOGGING_CONFIG as _ULC
_UVICORN_LOG_CFG = copy.deepcopy(_ULC)
_UVICORN_LOG_CFG.setdefault("filters", {})["suppress_200"] = {"()": _SuppressSuccessfulAccessLogs}
_UVICORN_LOG_CFG["loggers"]["uvicorn.access"]["filters"] = ["suppress_200"]

_LOG_FILE = os.path.join(SETTINGS_DIR, "multiproxy.log")
# Started by the tray app (it sets MULTIPROXY_TRAY=1): there is no console to read, so the log is ALWAYS written, to a rotating file (1 MB, 3 old
# copies) that the tray app's "View logs" and "Open live log window" read. A run from a terminal is unchanged (the EnableLogging setting decides).
_TRAY_MODE = os.environ.get("MULTIPROXY_TRAY", "").strip() == "1"
if ENABLE_LOGGING or _TRAY_MODE:
    # Redacting formatter for the file — timestamps, no ANSI codes, tokens masked
    _UVICORN_LOG_CFG["formatters"]["file_fmt"] = {
        "()": _RedactingFormatter,
        "fmt": "%(asctime)s %(levelname)-8s %(message)s",
    }
    # Fresh file each session (mode='w') so each start is easy to read independently
    # Both kinds are written by offloop_log's own thread (same file, same format, same rotation), never on the event loop.
    _UVICORN_LOG_CFG["handlers"]["file"] = {
        "()": offloop_log.OffLoopFileHandler,
        "filename": _LOG_FILE,
        "mode": "w",
        "encoding": "utf-8",
        "formatter": "file_fmt",
    } if not _TRAY_MODE else {
        "()": offloop_log.OffLoopFileHandler,
        "filename": _LOG_FILE,
        "maxBytes": 1_000_000,
        "backupCount": 3,
        "encoding": "utf-8",
        "formatter": "file_fmt",
    }
    # Separate console handler for proxy/app messages — uses datetime format,
    # distinct from uvicorn's "default" handler (which has no datetime).
    _UVICORN_LOG_CFG["formatters"]["proxy_fmt"] = {
        "format": "%(asctime)s %(levelname)-8s %(message)s",
    }
    _UVICORN_LOG_CFG["handlers"]["proxy_console"] = {
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
        "formatter": "proxy_fmt",
    }
    # Add file handler alongside each uvicorn logger's existing pretty console handler
    for _lname in ("uvicorn", "uvicorn.access"):
        _UVICORN_LOG_CFG["loggers"][_lname]["handlers"] = (
            list(_UVICORN_LOG_CFG["loggers"][_lname].get("handlers", [])) + ["file"]
        )
    # Root logger: datetime-stamped console + file for proxy/app messages.
    # uvicorn loggers keep propagate=False so there is no double console output.
    _UVICORN_LOG_CFG["root"] = {"handlers": ["proxy_console", "file"], "level": "INFO"}

# Built-in fallback for "which Claude model when a request names none". The real default is the newest
# Sonnet in Anthropic's live model list (_default_claude_model()); this is used until that list is known
# and whenever it can't be fetched.
DEFAULT_MODEL = "claude-sonnet-5-5"
def _port_from_env(name: str, default: int) -> int:
    """A port number from an environment variable, or `default` if it isn't set or isn't a valid port.
    Only ever set on purpose: a model-server device where something else already uses 8000, or a test run
    of two proxies on one device. A normal Skyrim install never sets these."""
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and 0 < int(raw) < 65536 else default


# Where to get the SkyrimNet MultiProxy plugin (the public release), and what the dashboard's "?" next to
# "Mod not detected" says (2026-10-01: a "mod" to every player, never a "plugin"). Casual voice; the last sentence points at Settings.
PLUGIN_DOWNLOAD_URL = "https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest"
PLUGIN_HELP_TEXT = ("Open Settings to install the Skyrim mod. MultiProxy will then start and stop "
                    "with the game.")
SKYRIMNET_PROVIDER_NAME = "SkyrimNet MultiProxy"        # the Name SkyrimNet's custom provider is given (Settings > SkyrimNet connection, and the Get started walk-through)
INTERCEPTOR_PORT = _port_from_env("MULTIPROXY_INTERCEPTOR_PORT", 9999)
def _saved_main_port() -> int:
    """The port chosen on the Settings page (config.json "main_port"), or 8000."""
    try:
        with open(os.path.join(SETTINGS_DIR, "config.json"), "r", encoding="utf-8") as f:
            v = json.load(f).get("main_port")
        return int(v) if isinstance(v, int) and 1024 <= v <= 65535 else 8000
    except (OSError, ValueError, AttributeError):
        return 8000


MAIN_PORT = _port_from_env("MULTIPROXY_PORT", _saved_main_port())  # the dashboard/API port uvicorn.run() binds at the very bottom of this file

# Optional, like the Codex CLI's own live _codex_path() check below -- a missing Claude CLI no
# longer stops the proxy from starting.
# Real bug, reported by a user (2026-09-13): someone with Codex set up but no working Claude
# subscription (their org had disabled Claude Code access) couldn't use the proxy AT ALL, because
# this used to `raise RuntimeError` at import time. Every place that reads CLAUDE_PATH now handles
# None, and Claude auth is only ever required for a request that actually routes to Claude --
# every other provider works normally regardless of whether Claude is available.
CLAUDE_PATH: Optional[str] = poll_cache.cached_which("claude")   # (also looks in Windows' saved PATH; _claude_path_loop keeps it current, so a Claude installed or updated later is found)
if CLAUDE_PATH:
    logger.info(f"Using Claude CLI: {CLAUDE_PATH}")
else:
    logger.warning(
        "Claude CLI not found on PATH -- Claude-routed requests will return a clean error until "
        "it's installed and logged in. Every other configured provider still works normally."
    )

CONFIG_FILE = os.path.join(SETTINGS_DIR, "config.json")
_config_reader = poll_cache.ConfigReader()


def _load_config() -> dict:
    """Load persisted config from disk, return empty dict on missing/corrupt file. The file's text is kept between reads while its modified
    time and size are unchanged (poll_cache.ConfigReader): every request reads it several times, and a read on the event loop stalled it."""
    return _config_reader.load(CONFIG_FILE)


def _save_config(data: dict) -> None:
    """Persist config dict to disk: all-or-nothing, and readable by its owner only (safe_paths.write_private_json)."""
    safe_paths.write_private_json(CONFIG_FILE, data)
    _config_reader.forget()


# --- Speed tracking (Speeds tab) ---
# Every reply already gets timed internally (each call_X_direct/call_X_streaming measures its own
# `elapsed`) but that number was only ever printed to the log and then thrown away. This keeps a
# short in-memory rolling history per resolved model string instead, so the dashboard's Speeds tab
# can show it for "Today" -- the live, zero-latency view. See the persistent speed_history.db
# section below (2026-09-20) for the longer-range (7d/30d/all time) history that survives a
# restart -- these two stores are deliberately separate; this in-memory one is untouched by that
# addition and still resets on restart, same as the console log already does.
SPEED_HISTORY_LEN = 20  # how many recent calls feed "last reply" / "avg" / "success %" / sparkline
SPEED_HOUR_WINDOW = 3600.0  # seconds, for "N calls in the last hour"


class _SpeedTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_model: dict[str, dict] = {}

    def record(self, model: str, elapsed: float, success: bool, provider: Optional[str] = None) -> None:
        now = time.time()
        with self._lock:
            entry = self._by_model.setdefault((provider or None, model), {
                "recent": deque(maxlen=SPEED_HISTORY_LEN),  # dicts: elapsed, success, ts
                "hour_calls": deque(),  # timestamps only, pruned to the last hour on read
            })
            entry["recent"].append({"elapsed": elapsed, "success": success, "ts": now})
            entry["hour_calls"].append(now)

    def snapshot(self) -> dict:
        """card key (one provider's model, see speed_queries.card_key) -> {model, provider, last_reply, avg, success_pct, calls_last_hour, recent}
        (each stat None/empty when nothing's been seen yet for that provider's model)."""
        now = time.time()
        out = {}
        with self._lock:
            for (provider, model), entry in self._by_model.items():
                hour_calls = entry["hour_calls"]
                while hour_calls and now - hour_calls[0] > SPEED_HOUR_WINDOW:
                    hour_calls.popleft()
                recent = list(entry["recent"])
                elapsed_values = [r["elapsed"] for r in recent]
                success_count = sum(1 for r in recent if r["success"])
                out[speed_queries.card_key(provider, model)] = {
                    "model": model,
                    "provider": provider,
                    "last_reply": elapsed_values[-1] if elapsed_values else None,
                    "avg": (sum(elapsed_values) / len(elapsed_values)) if elapsed_values else None,
                    "success_pct": round(100 * success_count / len(recent)) if recent else None,
                    "calls_last_hour": len(hour_calls),
                    "recent": elapsed_values[-10:],
                }
        return out


speed_tracker = _SpeedTracker()


# --- Persistent speed history (survives a restart) ---
# The in-memory _SpeedTracker above resets every restart -- fine for "Today," useless for "has
# this gotten slower over the last week." Every completed call also gets a row here: a plain local
# SQLite file (stdlib sqlite3, no new dependency), never rolled off (a few numbers per call is
# tiny -- years of real usage is still a small file). Personal runtime data, gitignored alongside
# config.json/multiproxy.log.
SPEED_HISTORY_DB = os.path.join(SETTINGS_DIR, "speed_history.db")


# New in Speeds v2: seconds to the first piece of reply text (streamed replies only), the reply's real
# output-token count (when the service reports it) and its length in characters (always), why a call
# failed (a short category, NULL on success), and which service handled it.
_SPEED_V2_COLUMNS = (
    ("first_token", "REAL"),
    ("out_tokens", "INTEGER"),
    ("out_chars", "INTEGER"),
    ("fail_reason", "TEXT"),
    ("provider", "TEXT"),
)

# Catch-broken-replies (2026-09-29): what happened to THIS reply's own shape, one value --
# "fine" (nothing needed), "repaired" (JSON wrapper noise stripped, or a markup/tag-fixer repair --
# both count as the same "repaired" story on the Speeds tab), "asked_again" (broken, retried, the
# retry came out usable), or NULL (not tracked for this call: a Jev/Decisions request, or a call made
# before this column existed). A reply that was retried and STILL came out broken is not one of
# these values -- it's recorded as its own separate failed row instead (fail_reason="broken reply"),
# the same way any other failure already is, so it counts against reliability for real rather than
# living only in this one column.
_REPAIR_COLUMNS = (
    ("reply_repair", "TEXT"),
)

# Routing (2026-10-02): the job this request was (SkyrimNet's key), how it was known (hint / name / recognised), tokens in, what it cost (dollars, when the AI
# reported it or its own prices give it; 0 for a free AI) and why (cost_note), a local llama.cpp's own timings (JSON), and which AIs were passed over first.
_ROUTING_COLUMNS = (
    ("job", "TEXT"),
    ("job_how", "TEXT"),
    ("tokens_in", "INTEGER"),
    ("cost", "REAL"),
    ("cost_note", "TEXT"),
    ("timings_json", "TEXT"),
    ("skipped", "TEXT"),
    ("queue_wait", "REAL"),                      # seconds the request stood in line before the model started on it (None when it never went through a line)
    ("own_ms", "REAL"),                          # milliseconds MultiProxy itself added to this chat request (request_facts.RequestRecord.own_ms; None for decisions and older rows)
    ("setup_fp", "TEXT"),                        # a local model's settings fingerprint when its engine started (local_engine_settings.setup_fingerprint); None for everything else
    ("cold", "INTEGER"),                         # 1 = the first reply since that engine started (slow for reasons unrelated to the settings: never averaged), else 0
    ("in_flight", "INTEGER"),                    # how many requests were being answered when this one started, itself included (the Overview's "Most at once")
    ("stand_in", "TEXT"),                        # who this request's answer stood in for when a backup answered ("TypeSafe", a device's name); None when the first choice answered
    ("fail_note", "TEXT"),                       # a failed request's plain reason from a local engine ("couldn't load (ran out of memory while starting)", engine_trouble.py); None otherwise
)

_trouble = engine_trouble.TroubleBook()         # what is wrong with each model right now (Routing, Speeds and the Overview read it); cleared when the model answers

# Short service names stored with each call (and shown on the health cards), by provider id.
_PROVIDER_SHORT = {
    "claude": "Claude", "glm": "GLM", "nano": "Nano-GPT", "openai": "OpenAI",
    "deepseek": "DeepSeek", "kilo": "Kilo Gateway", "gemini": "Gemini", "ollama": "Ollama", "lmstudio": "LM Studio", "local": "Local",
    "codex": "Codex", "geminicli": "Antigravity", "grok": "Grok", "xai": "xAI", "continuum": "Continuum", "perplexity": "Perplexity",
}
_PROVIDER_SHORT.update({_s.id: _s.pill for _s in key_sources.SOURCES})


class EngineFailure(HTTPException):
    """A local engine's (Ollama's, LM Studio's) failure with its plain meaning attached: `detail` is the sentence, `note` the short clause cards show after the model's name
    ("couldn't load (ran out of memory while starting)"). Nothing in either carries a path, a size, a code or the engine's own words (engine_trouble.py)."""

    def __init__(self, status_code: int, trouble: "engine_trouble.Trouble"):
        super().__init__(status_code=status_code, detail=trouble.sentence)
        self.note = trouble.clause


class EmptyReply(EngineFailure):
    """The AI answered with nothing (no words, only whitespace or tags). A failure like any other: recorded as one, and the route's backup is tried."""

    empty = True

    def __init__(self, thought: bool = False):
        super().__init__(502, engine_trouble.empty(thought))


EMPTY = "empty"          # the saved reason for a reply with no words in it


def _fail_note(exc: BaseException) -> Optional[str]:
    """The plain clause a failure carries (EngineFailure), else None."""
    note = getattr(exc, "note", None)
    return note if isinstance(note, str) and note else None


def _fail_reason(exc: BaseException) -> str:
    """Why a call failed, as one of: busy, timeout, server, auth, empty, other -- from what the proxy already
    knows since the Beta 25 work (a real HTTP status on every failure). 429 (the provider's or our own
    request line) is `busy`; 401/403 and "Claude isn't available" (its login) are `auth`; 408/504 or a
    timeout is `timeout`; any other 5xx is `server`; a reply with no words in it is `empty`."""
    if isinstance(exc, EmptyReply):
        return EMPTY
    if isinstance(exc, HTTPException):
        code = exc.status_code
        detail = str(exc.detail or "")
        if code == 429:
            return "busy"
        if code in (401, 403) or detail.startswith("Claude isn't available"):
            return "auth"
        if code in (408, 504) or "in time" in detail or "timed out" in detail:
            return "timeout"
        if code >= 500:
            return "server"
        return "other"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    return "other"


def _init_speed_history_db() -> None:
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS speed_history ("
            "ts REAL NOT NULL, model TEXT NOT NULL, elapsed REAL NOT NULL, success INTEGER NOT NULL)"
        )
        # Upgrade an existing speed_history.db from before roles_json existed (2026-09-20,
        # speeds-compare-by-role) -- a player's real file can already have rows from before this
        # column existed, so this checks first (PRAGMA table_info) and ALTER TABLE ADD COLUMN
        # only if it's actually missing, rather than folding it into CREATE TABLE IF NOT EXISTS
        # (which does nothing on a file where the table already exists with the old schema).
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(speed_history)")}
        if "roles_json" not in existing_cols:
            conn.execute("ALTER TABLE speed_history ADD COLUMN roles_json TEXT")
        # Speeds v2 (2026-09-27): same careful pattern, one column at a time. Old rows keep NULLs,
        # which every reader treats as "not measured" (never as zero).
        for column, sql_type in _SPEED_V2_COLUMNS:
            if column not in existing_cols:
                conn.execute(f"ALTER TABLE speed_history ADD COLUMN {column} {sql_type}")
        # Catch-broken-replies: same one-column-at-a-time pattern, one more time.
        for column, sql_type in _REPAIR_COLUMNS + _ROUTING_COLUMNS:
            if column not in existing_cols:
                conn.execute(f"ALTER TABLE speed_history ADD COLUMN {column} {sql_type}")
        # Every real query below filters by model and orders by ts -- this is the one index that
        # matters, and it covers both.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_speed_history_model_ts ON speed_history (model, ts)")
        conn.commit()
        speed_store.prepare(conn)       # WAL journal + the ts / job indexes: an old file is upgraded in place, here, on first start


_init_speed_history_db()


def _insert_speed_history_row(
    model: str, elapsed: float, success: bool, roles_json: Optional[str],
    first_token: Optional[float] = None, out_tokens: Optional[int] = None, out_chars: Optional[int] = None,
    fail_reason: Optional[str] = None, provider: Optional[str] = None, reply_repair: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Blocking (real disk I/O) -- always called through asyncio.to_thread, never directly from
    async code. See _record_speed's own docstring for why the caller awaits this rather than
    firing it and forgetting it.

    roles_json is a JSON array of the exact role label string(s) this model was serving at the
    moment of THIS call (e.g. '["Dialogue"]', or None if nothing was linked) -- a snapshot, not a
    live lookup, so a model's older history stays correctly labeled even after SkyrimNet's config
    later moves that role onto a different model (2026-09-20, speeds-compare-by-role).

    reply_repair (queue: catch-broken-replies, 2026-09-29) is one of "fine"/"repaired"/"asked_again",
    or None when this call was never checked (a Jev/Decisions request, or a row from before this
    column existed) -- see _REPAIR_COLUMNS' own comment for why "still broken" isn't a value here."""
    extra = {k: v for k, v in (extra or {}).items() if k in {c for c, _t in _ROUTING_COLUMNS}}
    columns = ["ts", "model", "elapsed", "success", "roles_json", "first_token", "out_tokens", "out_chars", "fail_reason", "provider", "reply_repair"] + list(extra)
    values = [time.time(), model, elapsed, 1 if success else 0, roles_json, first_token, out_tokens, out_chars, fail_reason, provider, reply_repair] + list(extra.values())
    speed_store.enqueue_row(SPEED_HISTORY_DB, columns, values)      # queued for the one background writer (speed_store.py): returns at once, never blocks a request


def _record_decision(confidence: Optional[float], asked: bool, answered_by: Optional[str]) -> None:
    """One small row per decision the local model answered FIRST (2026-10-05, for the Overview's Quick decisions line): how sure it was (None when it gave no
    confidence), whether a stronger AI was asked for a second opinion, and who gave the answer. Queued for the background writer: never slows the reply."""
    if not ENABLE_SPEED_TRACKING:
        return
    try:
        speed_store.enqueue_row(SPEED_HISTORY_DB, ("ts", "confidence", "asked", "answered_by"), (time.time(), confidence, 1 if asked else 0, answered_by), table="decision_log")
    except Exception as e:
        logger.warning(f"Decision record write failed (non-fatal): {e}")


def _record_voice_line(seconds: float, ok: bool, waited: Optional[float] = None, spoke: Optional[float] = None, audio: Optional[float] = None, chars: Optional[int] = None) -> None:
    """One small row per spoken line (2026-10-05, for the Overview's Voice row): seconds from SkyrimNet asking to the finished line, and whether it worked; since 2026-10-23
    also how long it stood in the voice queue (`waited`) and how long the engine spent on it (`spoke`), None when it never got that far. Queued for the background writer:
    never slows a line."""
    if not ENABLE_SPEED_TRACKING:
        return
    try:
        speed_store.enqueue_row(SPEED_HISTORY_DB, ("ts", "seconds", "ok", "waited", "spoke", "audio", "chars"),
                                (time.time(), float(seconds), 1 if ok else 0, None if waited is None else float(waited), None if spoke is None else float(spoke),
                                 None if audio is None else float(audio), None if chars is None else int(chars)), table="voice_log")
    except Exception as e:
        logger.warning(f"Voice line record write failed (non-fatal): {e}")


_intake = request_facts.Intake(_redact_text)    # what SkyrimNet sent along, shown once per new hint (request_facts.py)


# Has SkyrimNet (or any program that is not this dashboard) talked to MultiProxy since it started? The dashboard's own pages (Quick Test) call the same routes from the browser, and a
# browser always says where it comes from (Origin, Referer or Sec-Fetch-Site); a program like SkyrimNet sends none of them. The time of the latest one is kept, so the Overview says Connected only while SkyrimNet is really talking.
_outside_client = {"at": None, "agent": ""}


def _note_outside_client(request) -> None:
    try:
        h = {str(k).lower() for k in request.headers.keys()}
        if not ({"origin", "referer", "sec-fetch-site"} & h):
            _outside_client["at"] = time.time()
            _outside_client["agent"] = str(request.headers.get("user-agent") or "")[:80]
    except Exception:
        pass


_in_flight = quick_stats.InFlight()       # requests being answered right now: the count each one sees when it starts is saved with its row ("Most at once")


def _leave_when_sent(result, request_id: str) -> bool:
    """A streamed answer is still being sent when the handler returns: it stops counting as in flight when its last chunk goes out (or the client leaves). True when it
    took that over; False for a plain answer (the caller stops counting at once)."""
    body = getattr(result, "body_iterator", None)
    if body is None:
        return False

    async def counted():
        try:
            async for chunk in body:
                yield chunk
        finally:
            _in_flight.leave(request_id)
    result.body_iterator = counted()
    return True


def _begin_request(request, request_id: str, kind: str = "chat", extra_settings=(), count: bool = True) -> "request_facts.RequestRecord":
    """Start the one record for this request and log what SkyrimNet sent along with it (never raises: a log problem must not break a request). `count`: it counts as
    in flight until its handler calls `_in_flight.leave` (a dashboard test is not SkyrimNet's load, so it does not count)."""
    _note_outside_client(request)
    path = ""
    try:
        path = request.url.path
    except Exception:
        pass
    rec = request_facts.begin(request_id, path, kind)
    if count:
        rec.in_flight = _in_flight.enter(request_id)
    try:
        for line in _intake.observe(request_id, path, kind, request.headers, extra_settings, capture_all=CAPTURE_PROMPTS):
            logger.info(line)
    except Exception as e:
        logger.warning(f"Couldn't read what SkyrimNet sent along with this request ({e.__class__.__name__})")
    return rec


_live = live_feed.Feed()


def _note_record(model: str, elapsed: float, success: bool, first_token, out_tokens, fail_reason, provider, reply_repair, extra_roles) -> None:
    """Fill this request's record from a finished call and write the reply's log line from it (the one line that says who answered, how fast, how many
    tokens). Runs before the Speeds gate, so it works with speed tracking off too."""
    rec = request_facts.current()
    if rec is None:
        return
    rec.attempt_ended()                                   # the AI's share of this attempt is over (own_ms is worked out from the rest)
    try:
        rec.model, rec.provider = rec.model or model, _pc_shown(provider) or rec.provider
        rec.total_s = (time.time() - rec.started) if rec.started else elapsed
        rec.first_word_s = first_token
        if out_tokens is not None:
            rec.tokens_out = out_tokens
        rec.outcome = "ok" if success else (fail_reason or "failed")
        rec.reply_repair = reply_repair
        if reply_repair == "asked_again":
            rec.retried = True
        if rec.job is None:                          # a job we didn't recognise (or routing never looked): the Speeds tab's own reading, by model name, as it always was
            labels = [chip["label"] for chip in _get_role_chips_cached().get(speed_queries.card_key(provider, model), [])]
            for extra in (extra_roles or []):
                if extra not in labels:
                    labels.append(extra)
            if labels:
                rec.job = "/".join(labels)
        if success and first_token:
            _routing_engine.first_words.add(speed_queries.card_key(provider, model), first_token)
        logger.info(rec.line())
        _live.request(rec)
    except Exception as e:
        logger.warning(f"Couldn't write this request's summary line ({e.__class__.__name__})")


def _speed_extra(model: str, provider: Optional[str], success: bool) -> dict:
    """The routing facts of this request plus, for a local model, the setup it ran under and whether it was the first reply since its engine started."""
    extra = _routing_columns()
    if provider == "Local":
        try:
            extra.update(local_svc.note_request(strip_local_prefix(model), success))
        except Exception:
            pass
    return extra


async def _record_speed(
    model: str, elapsed: float, success: bool, *,
    first_token: Optional[float] = None, out_tokens: Optional[int] = None, out_chars: Optional[int] = None,
    fail_reason: Optional[str] = None, provider: Optional[str] = None, extra_roles: Optional[list] = None,
    reply_repair: Optional[str] = None, fail_note: Optional[str] = None,
) -> None:
    """The one place a completed call's timing gets recorded -- both into the in-memory
    _SpeedTracker (unchanged, still exactly right for "Today") and as a row in speed_history.db
    (2026-09-20) so longer ranges survive a restart. Called from chat_completions()'s own
    non-streaming/streaming/failure paths and from _timed_stream_impl below -- see queue item
    speeds-history for why this replaced calling speed_tracker.record(...) directly at each site.

    The actual insert runs off the event loop via asyncio.to_thread, so a slow disk can never
    block any OTHER concurrent request -- awaited here rather than fired-and-forgotten via
    asyncio.create_task, though: a local sqlite insert is a fraction of a millisecond, genuinely
    not noticeable added latency on the response it came from, and awaiting it makes the write
    deterministic (done by the time this request's response goes out, not a background task that
    might never get scheduled before the process exits) -- which also happens to be exactly what
    makes this reliably testable. A failed write is caught and logged, never raised: it must never
    break the actual chat response this timing came from.

    Gated on ENABLE_SPEED_TRACKING (2026-09-20) -- checked first and cheaply, before touching
    _SpeedTracker OR the database, so "tracking off" costs one boolean check per call, not a
    record-then-discard.

    `extra_roles` (queue: jev-fallback-order, 2026-09-29) -- role label(s) to attach to this ONE
    recording regardless of what SkyrimNet's own yaml says `model` is normally used for. Needed for
    a Jev stand-in specifically: it's recorded under the STAND-IN's real model id (e.g.
    "deepseek-flash"), not "typesafe/jev-1.13", precisely so it's never mistaken for the real
    thing on the Speeds tab -- but that real model id is very likely already serving some OTHER
    role in the player's own yaml (dialogue, most likely), and the generic yaml-based lookup below
    has no way to know THIS PARTICULAR call was standing in for Decisions. Forcing the label
    directly is what actually guarantees "still shows up under Decisions" without depending on
    yaml naming lining up by coincidence.

    `reply_repair` (queue: catch-broken-replies, 2026-09-29) -- "fine"/"repaired"/"asked_again" for
    a reply this request's own prompt asked for as JSON (or a prose reply the tag fixer looked at),
    None when this call was never checked at all. A reply that was retried and STILL came out
    broken is never passed here -- it gets its own separate failed _record_speed call instead
    (fail_reason="broken reply"), so it counts against reliability like any other real failure.
    """
    await _finish_cost(model, provider, success, out_tokens)
    _note_record(model, elapsed, success, first_token, out_tokens, fail_reason, provider, reply_repair, extra_roles)
    if success:
        _trouble.clear(speed_queries.card_key(provider, model))        # it answered: whatever was wrong with it is over
    elif fail_note:
        _trouble.set(speed_queries.card_key(provider, model), fail_note)
    if not ENABLE_SPEED_TRACKING:
        return
    speed_tracker.record(model, elapsed, success, provider)
    # Snapshot whatever _get_role_chips_cached() currently says this model is serving, at the
    # moment of THIS call -- reuses the exact same lookup the live "By model" view already uses,
    # rather than inventing a second way to read SkyrimNet's role config (2026-09-20,
    # speeds-compare-by-role). None (not "[]") when nothing's linked, so a plain substring/LIKE
    # query elsewhere can never accidentally match an empty array against a real role name.
    role_labels = [chip["label"] for chip in _get_role_chips_cached().get(speed_queries.card_key(provider, model), [])]
    for extra in (extra_roles or []):
        if extra not in role_labels:
            role_labels.append(extra)
    roles_json = json.dumps(role_labels) if role_labels else None
    try:
        _insert_speed_history_row(      # only queues the row (the background writer saves it): a slow disk can't delay this reply
            model, elapsed, success, roles_json,
            first_token, out_tokens, out_chars, None if success else fail_reason, provider, reply_repair,
            {**_speed_extra(model, provider, success), **({"fail_note": fail_note} if fail_note and not success else {})},
        )
    except Exception as e:
        logger.warning(f"Speed history write failed (non-fatal, Today's view is unaffected): {e}")


# Providers that send the whole reply in one piece (so "seconds to the first word" would just be the
# full reply time): Codex runs a CLI to completion. Antigravity's CLI really does stream, so it is timed.
_WHOLE_REPLY_PROVIDERS = {"Codex"}


def _stream_event_facts(event: str) -> tuple:
    """(characters of reply text in this SSE event, output-token count if the event reports one)."""
    stripped = event.strip()
    if not stripped.startswith("data: {"):
        return 0, None
    try:
        obj = json.loads(stripped[6:])
    except (json.JSONDecodeError, ValueError):
        return 0, None
    if not isinstance(obj, dict):
        return 0, None
    chars = 0
    choices = obj.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        content = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(content, str):
            chars = len(content)
    usage = obj.get("usage")
    tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    return chars, tokens if isinstance(tokens, int) else None


def _timed_stream(source, model: str, start: float, *, provider: Optional[str] = None, state: Optional[dict] = None):
    """Wraps a provider's streaming generator so speed is recorded for the FULL time until the
    stream actually finishes (last chunk / generator exhaustion), not when the StreamingResponse
    object was constructed -- a streaming reply isn't "done" until the last chunk lands. Also
    measures (Speeds v2) how long the first piece of reply text took, and how much text came.

    Plain (non-generator) dispatcher, not itself using `yield` -- when ENABLE_SPEED_TRACKING is
    off, returns `source` completely unwrapped: no try/except, no timing, not even a per-chunk
    pass-through loop. That's the point of splitting this out from _timed_stream_impl: "tracking
    off" means the streaming path runs with literally zero instrumentation overhead, not a wrapper
    that immediately discards what it measured. `state` is shared with _finish_stream: it says
    whether the stream failed partway (which ends it cleanly, so it isn't an exception here)."""
    if not ENABLE_SPEED_TRACKING:
        return source
    return _timed_stream_impl(source, model, start, provider, state if state is not None else {})


def _with_record_headers(result):
    """Put request_facts.response_headers(...) on a chat answer. It returns no headers today, so the answer goes out exactly as it was; once SkyrimNet's
    author and we agree the names, changing that one function is all it takes (a plain dict answer is wrapped, a stream's headers are added to)."""
    headers = request_facts.response_headers(request_facts.current())
    if not headers:
        return result
    if isinstance(result, dict):
        return JSONResponse(content=result, headers=headers)
    try:
        result.headers.update(headers)
    except Exception:
        pass
    return result


def _usage_to_record(usage) -> None:
    rec = request_facts.current()
    if rec is not None:
        rec.note_usage(usage)


def _note_stream_usage(event: str) -> None:
    """Hand the request's record the token counts a streamed reply's usage event carries (when it carries any)."""
    rec = request_facts.current()
    stripped = event.strip()
    if rec is None or not stripped.startswith("data: {"):
        return
    try:
        obj = json.loads(stripped[6:])
    except (json.JSONDecodeError, ValueError):
        return
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if not isinstance(usage, dict) and isinstance(obj.get("message"), dict):
            usage = obj["message"].get("usage")
        rec.note_usage(usage)
        rec.note_timings(obj)


async def _timed_stream_impl(source, model: str, start: float, provider: Optional[str], state: dict):
    success = True
    finished = False  # False if SkyrimNet hung up mid-stream: that says nothing about the provider
    fail_reason = None
    first_token = None
    out_chars = 0
    out_tokens = None
    try:
        async for chunk in source:
            chars, tokens = _stream_event_facts(chunk)
            if chars:
                if first_token is None and provider not in _WHOLE_REPLY_PROVIDERS:
                    first_token = time.time() - start
                out_chars += chars
            if tokens is not None:
                out_tokens = tokens
            if '"prompt_tokens"' in chunk or '"input_tokens"' in chunk or '"timings"' in chunk:     # the usage event (tokens, cost) or llama.cpp's own timings
                _note_stream_usage(chunk)
            yield chunk
        finished = True
        if state.get("ended_early"):  # failed partway: the stream was closed cleanly, but it did fail
            success = False
            fail_reason = state.get("reason") or "other"
    except Exception as e:
        success = False
        finished = True
        fail_reason = _fail_reason(e)
        raise
    finally:
        if finished:
            await _record_speed(
                model, time.time() - start, success, first_token=first_token, out_tokens=out_tokens,
                out_chars=out_chars, fail_reason=fail_reason, provider=provider,
                reply_repair=state.get("reply_repair") if success else None,
            )
        aclose = getattr(source, "aclose", None)
        if aclose:
            with suppress(Exception):
                await aclose()


# --- Raydonn's fork upgrades (2026-09-27) ---
# Ported from community member Raydonn's own fork of this proxy (reference copy kept in
# prompts/reference/raydonn-proxy-custom.py). Six features, each adapted to work across every
# provider here instead of the two (GLM, and the since-removed OpenRouter) that fork wired them into:
#   1. NPC tag fixer   -- sanitize_response / sanitize_sse_stream (below), applied at ONE choke
#                         point in chat_completions() so no provider needs its own copy.
#   2. Connection retry -- _post_with_retry, a drop-in for `session.post(...)` in `async with`.
#   3. Too-long chats  -- _ContextTrimmer / _trim_messages_for_context (bounded, unlike theirs).
#   4/5. Request line  -- _RequestLine / _acquire_request_slot, plus the /health telemetry.
#   6. Full replies    -- _log_reply (LogFullReplies in multiproxy.ini).

# --- 1. NPC tag fixer ---
# The regexes, vocabulary, voice-tag alias table and hold-back rules below are Raydonn's, unchanged
# -- this code is intricate and was tuned against real model output, so only the plumbing around it
# (where it's called from, how proxy-emitted error chunks are skipped) is this project's own.
_TAG_JUNK = "*_`~.,:;!?\"'|+=#\\/ \t-"
_EMPHASIS = "*_`~"
_TAG_BRACKETS = {"<": ">", "[": "]", "(": ")", "{": "}"}
_TAG_CLOSERS = {closer: opener for opener, closer in _TAG_BRACKETS.items()}
_TAG_PREFIX_RE = re.compile(r"\[?/?(?:[A-Za-z][A-Za-z0-9]*(?:[ _.\-]+[A-Za-z0-9]*){0,2})?[\]>)}]{0,2}\Z")
_BRACKET_SCAN = {o: re.compile(f"[{re.escape(o)}{re.escape(c)}]") for o, c in _TAG_BRACKETS.items()}

_TAG_CANDIDATE_RE = re.compile(
    r"(?P<pre>[*_`~]{0,3})(?P<open>[<\[])(?P<inner>[^<>\[\]\n]{1,64})(?P<close>[>\]])(?P<post>[*_`~]{0,3})"
)
_WELL_FORMED_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9_]*)/?>")
_CLEAN_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_LOOSE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:[ _.\-][A-Za-z0-9]+)*$")

# The tags this session has seen written correctly (e.g. <internal_thought>), keyed by a
# case/punctuation-insensitive form of the name so "Internal Thought" repairs to "internal_thought".
# Seeded from config.json's optional "known_tags" list (default: just internal_thought).
_tag_vocab: dict[str, str] = {}


def _tag_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _remember_tag(name: str) -> None:
    key = _tag_key(name)
    if key and key not in _tag_vocab and len(_tag_vocab) < 64:
        _tag_vocab[key] = name


def _seed_tag_vocab() -> None:
    for seed in _load_config().get("known_tags", ["internal_thought"]):
        _remember_tag(str(seed))


_seed_tag_vocab()


def _repair_tags(text: str) -> tuple[str, list[str], bool]:
    for m in _WELL_FORMED_RE.finditer(text):
        _remember_tag(m.group(1))
    repairs: list[str] = []

    def _fix(m: re.Match) -> str:
        raw, inner = m.group(0), m.group("inner")
        core = inner.strip(_TAG_JUNK)
        closing = inner.strip(_EMPHASIS + " \t").startswith("/")
        self_closing = inner.strip(_EMPHASIS + " \t").endswith("/")
        if not core or not _LOOSE_NAME_RE.match(core):
            return raw
        name = _tag_vocab.get(_tag_key(core))
        square = m.group("open") != "<" or m.group("close") != ">"
        if name is None:
            if square or not _CLEAN_NAME_RE.match(core):
                return raw
            name = core
        slash_l, slash_r = "/" if closing else "", "/" if self_closing else ""
        fixed = f"<{slash_l}{name}{slash_r}>"
        if fixed == raw:
            return raw
        repairs.append(f"{raw}  ->  {fixed}")
        return fixed

    out = _TAG_CANDIDATE_RE.sub(_fix, text)
    return out, repairs, bool(repairs) and out.endswith(">")


# --- Voice cues (FixHiggsVoiceTags, OFF by default) ---
# Raydonn's voice-cue rewriting, retargeted at SkyrimNet Beta 25's real tag list. Beta 25 asks the
# AI for DIFFERENT voice tags depending on each NPC's voice engine (0650_audio_tags.prompt):
#   Higgs / Higgs v3 -> lowercase `[category-tag]` (`[emotion-anger]`, `[sfx-sigh]`, `[prosody-pitch_low]`)
#   Chatterbox       -> plain words (`[angry]`, `[sigh]`, `[laugh]`, `[clear throat]`)
#   everything else  -> no tags
# The proxy can't know which engine a given NPC uses, so this only runs when the player says their
# NPCs use Higgs. Anything outside the active vocabulary is left exactly as the AI wrote it -- the
# prompt warns an unknown tag "gets read aloud literally", and deleting one is worse than keeping it.
_HIGGS_CATALOG = (
    "emotion-affection", "emotion-amusement", "emotion-anger", "emotion-arousal", "emotion-awe",
    "emotion-bitterness", "emotion-confusion", "emotion-contemplation", "emotion-contentment",
    "emotion-determination", "emotion-disgust", "emotion-elation", "emotion-enthusiasm", "emotion-fear",
    "emotion-helplessness", "emotion-longing", "emotion-pride", "emotion-relief", "emotion-sadness",
    "emotion-shame", "emotion-surprise",
    "style-singing", "style-shouting", "style-whispering",
    "prosody-speed_very_slow", "prosody-speed_slow", "prosody-speed_fast", "prosody-speed_very_fast",
    "prosody-pitch_low", "prosody-pitch_high", "prosody-expressive_high", "prosody-expressive_low",
    "sfx-cough", "sfx-laughter", "sfx-crying", "sfx-screaming", "sfx-burping", "sfx-humming",
    "sfx-sigh", "sfx-sniff", "sfx-sneeze",
)
# Categories that count as "a voice tag" even when the name after the dash isn't recognised -- such
# a tag is left alone rather than rewritten or dropped.
_VOICE_CATEGORIES = ("emotion", "style", "prosody", "sfx")


def _voice_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _build_voice_vocab(tags) -> dict[str, str]:
    """{name-key: "category-name"} from a list of full tags like "sfx-laughter". The key is the
    name with case and punctuation removed (`speed_very_slow` -> SPEEDVERYSLOW)."""
    vocab: dict[str, str] = {}
    for tag in tags or ():
        cat, _, name = str(tag).strip().lower().partition("-")
        if cat in _VOICE_CATEGORIES and name:
            vocab[_voice_key(name)] = f"{cat}-{name}"
    return vocab


_DEFAULT_VOICE_VOCAB = _build_voice_vocab(_HIGGS_CATALOG)

# Loose wording an AI might use -> the name-key of the real tag (a key of the vocabulary above).
# Raydonn's table, plus the three names Beta 25 has that his didn't (amusement, elation, burping).
_VOICE_ALIAS = {
    "LAUGH": "LAUGHTER", "LAUGHS": "LAUGHTER", "LAUGHING": "LAUGHTER",
    "CHUCKLE": "LAUGHTER", "CHUCKLES": "LAUGHTER", "CHUCKLING": "LAUGHTER",
    "GIGGLE": "LAUGHTER", "GIGGLES": "LAUGHTER", "SNORT": "LAUGHTER",
    "SIGHS": "SIGH", "SIGHING": "SIGH",
    "COUGHS": "COUGH", "COUGHING": "COUGH", "CLEARSTHROAT": "COUGH",
    "CRY": "CRYING", "CRIES": "CRYING", "SOB": "CRYING", "SOBS": "CRYING",
    "SOBBING": "CRYING", "WEEP": "CRYING", "WEEPING": "CRYING",
    "SCREAM": "SCREAMING", "SCREAMS": "SCREAMING", "SHRIEK": "SCREAMING",
    "SHRIEKS": "SCREAMING", "SHRIEKING": "SCREAMING",
    "SNIFFS": "SNIFF", "SNIFFLE": "SNIFF", "SNIFFLES": "SNIFF", "SNIFFING": "SNIFF",
    "SNEEZES": "SNEEZE", "SNEEZING": "SNEEZE",
    "HUM": "HUMMING", "HUMS": "HUMMING",
    "BURP": "BURPING", "BURPS": "BURPING",
    "WHISPER": "WHISPERING", "WHISPERS": "WHISPERING", "WHISPERED": "WHISPERING",
    "SHOUT": "SHOUTING", "SHOUTS": "SHOUTING", "YELL": "SHOUTING",
    "YELLS": "SHOUTING", "YELLING": "SHOUTING",
    "SING": "SINGING", "SINGS": "SINGING", "SUNG": "SINGING",
    "AFFECTIONATE": "AFFECTION", "ANGRY": "ANGER", "RAGE": "ANGER", "FURY": "ANGER",
    "FURIOUS": "ANGER", "AROUSED": "AROUSAL", "AWED": "AWE", "BITTER": "BITTERNESS",
    "CONFUSED": "CONFUSION", "CONTEMPLATIVE": "CONTEMPLATION",
    "THOUGHTFUL": "CONTEMPLATION", "CONTENT": "CONTENTMENT",
    "DETERMINED": "DETERMINATION", "DISGUSTED": "DISGUST",
    "ENTHUSIASTIC": "ENTHUSIASM", "EXCITED": "ENTHUSIASM", "EXCITEMENT": "ENTHUSIASM",
    "AFRAID": "FEAR", "SCARED": "FEAR", "FEARFUL": "FEAR", "TERRIFIED": "FEAR",
    "HELPLESS": "HELPLESSNESS", "LONGS": "LONGING", "YEARNING": "LONGING",
    "PROUD": "PRIDE", "RELIEVED": "RELIEF", "SAD": "SADNESS", "SORROW": "SADNESS",
    "ASHAMED": "SHAME", "SURPRISED": "SURPRISE", "SHOCK": "SURPRISE",
    "SHOCKED": "SURPRISE",
    "AMUSED": "AMUSEMENT", "AMUSING": "AMUSEMENT",
    "ELATED": "ELATION", "JOY": "ELATION", "JOYFUL": "ELATION", "HAPPY": "ELATION",
}
_VOICE_WRAPPED_RE = re.compile(
    r"(?P<pre>[*_`~]{0,3})"
    r"(?P<open>\[{1,2}|<|\(|\{)"
    r"(?P<slash>/?)"
    r"(?P<inner>[A-Za-z][A-Za-z0-9 _.\-]{0,39})"
    r"(?:\]{1,2}|>|\)|\})"
    r"(?P<post>[*_`~]{0,3})"
)
_VOICE_ACTION_RE = re.compile(r"(?<![*\w])\*{1,2}(?P<word>[A-Za-z]{3,20})\*{1,2}(?![*\w])")

# Which tags SkyrimNet actually allows, read from the linked install's own Higgs.yaml. Cached by
# file modification time so a request costs one stat() and only when FixHiggsVoiceTags is on.
_higgs_tags_cache: dict = {"path": None, "mtime": None, "value": None}


def _read_higgs_yaml(skyrim_path: str) -> Optional[dict]:
    """{"enabled": bool, "tags": [...]} from <SkyrimNet>/config/Higgs.yaml (`enable_audio_tags` and
    `allowed_audio_tags`), or None when there's no linked install or the file can't be read."""
    if not skyrim_path:
        return None
    skyrimnet_dir = _resolve_skyrimnet_dir_cached(skyrim_path)
    if not skyrimnet_dir:
        _note_skyrimnet_missing(skyrim_path, "SkyrimNet's list of voice tags")
        return None
    path = os.path.join(skyrimnet_dir, "config", "Higgs.yaml")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    if _higgs_tags_cache["path"] == path and _higgs_tags_cache["mtime"] == mtime:
        return _higgs_tags_cache["value"]
    value = None
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        tags = doc.get("allowed_audio_tags") if isinstance(doc, dict) else None
        if isinstance(tags, list):  # no such list at all means "not a Higgs file we understand"
            value = {
                "enabled": doc.get("enable_audio_tags") is not False,
                "tags": [str(t) for t in tags],
            }
    except Exception:
        value = None
    _higgs_tags_cache.update({"path": path, "mtime": mtime, "value": value})
    return value


def _active_voice_vocab() -> dict[str, str]:
    """The vocabulary voice-cue rewriting uses right now: the linked install's own Higgs.yaml list
    when it can be read (empty when the player has turned audio tags off there, so nothing gets
    rewritten), otherwise Beta 25's built-in Higgs catalog."""
    higgs = _read_higgs_yaml(skyrim_install_path)
    if higgs is None:
        return _DEFAULT_VOICE_VOCAB
    return _build_voice_vocab(higgs["tags"]) if higgs["enabled"] else {}


def _resolve_voice(inner: str, *, allow_bare: bool, vocab: dict[str, str]) -> Optional[str]:
    """The real tag ("sfx-laughter") that `inner` means, or None to leave the text as it is."""
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", inner) if t]
    if not tokens or len(tokens) > 5:
        return None
    cat = tokens[0].lower() if tokens[0].lower() in _VOICE_CATEGORIES else None
    if not cat and len(tokens) > 3:
        return None
    key = _voice_key("".join(tokens[1:] if cat else tokens))
    if not key:
        return None
    full = vocab.get(key)
    if full is None:
        target = _VOICE_ALIAS.get(key)
        full = vocab.get(target) if target else None
    if full is None:
        return None  # a recognised category with an unknown name is left as written, never deleted
    if cat and not full.startswith(cat + "-"):
        return None
    if not cat and not allow_bare:
        return None
    return full


def _repair_voice_tags(text: str, prev: str = "", vocab: Optional[dict] = None) -> tuple[str, list[str], bool]:
    vocab = _DEFAULT_VOICE_VOCAB if vocab is None else vocab
    if not vocab:
        return text, [], False
    repairs: list[str] = []
    dropped = False
    prev = prev if prev and prev not in _EMPHASIS + " \t\n" + "".join(_TAG_BRACKETS) else ""
    text = prev + text

    def _fix_wrapped(m: re.Match) -> str:
        nonlocal dropped
        raw, inner = m.group(0), m.group("inner")
        canon = _resolve_voice(inner, allow_bare=m.group("open")[0] in "[<" or inner.isupper(), vocab=vocab)
        if canon is None:
            return raw
        if m.group("slash"):
            canon = ""  # a closing "[/emotion-anger]" means nothing to a voice engine
        # No spacing is added around the tag: Beta 25's Higgs format wants `[category-tag]` "glued to
        # text with no space" (`[sfx-sigh]Haah,`), so the AI's own spacing is kept exactly.
        fixed = f"[{canon}]" if canon else ""
        if fixed == raw:
            return raw
        dropped = dropped or not canon
        repairs.append(f"{raw}  ->  {fixed or '(dropped)'}")
        return fixed

    def _fix_action(m: re.Match) -> str:
        raw = m.group(0)
        canon = _resolve_voice(m.group("word"), allow_bare=True, vocab=vocab)
        if not canon or not canon.startswith("sfx-"):
            return raw
        fixed = f"[{canon}]"
        repairs.append(f"{raw}  ->  {fixed}")
        return fixed

    out = _VOICE_WRAPPED_RE.sub(_fix_wrapped, text)
    if "*" in out:
        out = _VOICE_ACTION_RE.sub(_fix_action, out)
    if dropped:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"[ \t]+([\n.,!?;:])", r"\1", out)
        out = re.sub(r"(?m)^[ \t]+", "", out)
    return out[len(prev):], repairs, bool(repairs) and out.endswith("]")


def _tag_counts(text: str) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for m in _WELL_FORMED_RE.finditer(text):
        tag, name = m.group(0), m.group(1)
        if tag.endswith("/>"):
            continue
        slot = counts.setdefault(name, [0, 0])
        slot[1 if tag.startswith("</") else 0] += 1
    return counts


def _balance_warnings(counts: dict[str, list[int]]) -> list[str]:
    out = []
    for name, (opens, closes) in counts.items():
        if opens != closes and _tag_key(name) in _tag_vocab:
            out.append(f"unbalanced <{name}>: {opens} open / {closes} close")
    return out


def _log_repairs(request_id: str, model: str, repairs: list[str], warnings: list[str]) -> None:
    if repairs:
        logger.warning(f"[{request_id}] {model} markup repaired x{len(repairs)}: " + "  |  ".join(repairs))
    for w in warnings:
        logger.warning(f"[{request_id}] {model} {w}")


def sanitize_response(text: str, request_id: str, model: str) -> tuple:
    """(fixed_text, repairs) -- repairs is the list of what the tag fixer changed (possibly empty),
    returned (not just logged) so the caller can count "repaired" toward the Speeds tab the same
    way a JSON repair does (queue: catch-broken-replies, 2026-09-29: "the tag fixer's markup repairs
    count as repaired too")."""
    if not text or not any(c in text for c in "<[({*"):
        return text, []
    fixed, repairs = text, []
    if FIX_HIGGS_VOICE_TAGS:
        fixed, repairs, _ = _repair_voice_tags(text, vocab=_active_voice_vocab())
    if "<" in fixed or "[" in fixed:
        fixed, tag_repairs, _ = _repair_tags(fixed)
        repairs += tag_repairs
    _log_repairs(request_id, model, repairs, _balance_warnings(_tag_counts(fixed)))
    return fixed, repairs


class StreamSanitizer:
    MAX_HOLD = 96
    ACTION_HOLD = 32

    def __init__(self, request_id: str, model: str):
        self.request_id = request_id
        self.model = model
        self.pending = ""
        self.counts: dict[str, list[int]] = {}
        self.repairs: list[str] = []
        # Survives flush()'s own reset of self.repairs, so a caller can ask "did this WHOLE reply
        # ever need a repair" after the stream ends (queue: catch-broken-replies, 2026-09-29) rather
        # than only "did the last flush() need one".
        self.total_repairs = 0
        self.drop_lead_emphasis = False
        self.last_char = ""
        # Voice-cue rewriting is its own setting (FixHiggsVoiceTags, off by default). Captured once
        # per reply so a setting change never lands halfway through one.
        self.vocab: Optional[dict] = _active_voice_vocab() if FIX_HIGGS_VOICE_TAGS else None

    def feed(self, text: str) -> str:
        self.pending += text
        cut = self._safe_cut(self.pending)
        head, self.pending = self.pending[:cut], self.pending[cut:]
        return self._emit(head)

    def flush(self) -> str:
        out = self._emit(self.pending)
        self.pending = ""
        _log_repairs(self.request_id, self.model, self.repairs, _balance_warnings(self.counts))
        self.total_repairs += len(self.repairs)
        self.repairs = []
        return out

    def _safe_cut(self, buf: str) -> int:
        hold = len(buf)
        for opener, closer in _TAG_BRACKETS.items():
            if opener not in buf:
                continue
            idx = self._unclosed(buf, opener, closer)
            if (idx is not None and len(buf) - idx <= self.MAX_HOLD and self._tag_shaped(buf, idx) and not _TAG_CANDIDATE_RE.match(buf, idx)):
                hold = min(hold, idx)
        if buf and buf[-1] in _TAG_CLOSERS:
            idx = self._matching_open(buf)
            if idx is not None and len(buf) - idx <= self.MAX_HOLD and self._tag_shaped(buf, idx):
                hold = min(hold, idx)
        if self.vocab is not None:
            # Only voice-cue rewriting needs to see a whole `*action*`, so text that has no such
            # rewriting applied is never held back for it.
            idx = self._open_emphasis(buf)
            if idx is not None and len(buf) - idx <= self.ACTION_HOLD:
                hold = min(hold, idx)
        while hold > 0 and buf[hold - 1] in _EMPHASIS and len(buf) - hold < 3:
            hold -= 1
        return hold

    @staticmethod
    def _tag_shaped(buf: str, idx: int) -> bool:
        return buf[idx] == "<" or bool(_TAG_PREFIX_RE.match(buf, idx + 1))

    @classmethod
    def _unclosed(cls, buf: str, opener: str, closer: str) -> Optional[int]:
        base = max(0, len(buf) - cls.MAX_HOLD - 1)
        depth, start = 0, None
        for m in _BRACKET_SCAN[opener].finditer(buf, base):
            if m.group() == opener:
                if depth == 0:
                    start = m.start()
                depth += 1
            elif depth:
                depth -= 1
        return start if depth else None

    @staticmethod
    def _matching_open(buf: str) -> Optional[int]:
        closer, opener, depth = buf[-1], _TAG_CLOSERS[buf[-1]], 0
        for i in range(len(buf) - 1, -1, -1):
            depth += (buf[i] == closer) - (buf[i] == opener)
            if depth == 0:
                return i
        return None

    @staticmethod
    def _open_emphasis(buf: str) -> Optional[int]:
        if "*" not in buf:
            return None
        runs = [m.span() for m in re.finditer(r"\*+", buf)]
        if not runs:
            return None
        if runs[-1][1] == len(buf):
            return runs[-2][0] if len(runs) % 2 == 0 else runs[-1][0]
        return runs[-1][0] if len(runs) % 2 else None

    def _emit(self, chunk: str) -> str:
        if not chunk:
            return chunk
        if self.drop_lead_emphasis:
            self.drop_lead_emphasis = False
            stripped = chunk.lstrip(_EMPHASIS)
            if 0 < len(chunk) - len(stripped) <= 3:
                chunk = stripped
            if not chunk:
                return chunk
        ends_on_voice_tag = False
        if self.vocab is not None and any(c in chunk for c in "<[({*"):
            chunk, voice_repairs, ends_on_voice_tag = _repair_voice_tags(chunk, self.last_char, self.vocab)
            self.repairs.extend(voice_repairs)
            if not chunk:
                self.drop_lead_emphasis = ends_on_voice_tag
                return chunk
        fixed, repairs, ends_on_tag = (
            _repair_tags(chunk) if "<" in chunk or "[" in chunk else (chunk, [], False)
        )
        self.drop_lead_emphasis = ends_on_tag or ends_on_voice_tag
        self.repairs.extend(repairs)
        if "<" in fixed:
            for name, (o, c) in _tag_counts(fixed).items():
                slot = self.counts.setdefault(name, [0, 0])
                slot[0] += o
                slot[1] += c
        if fixed:
            self.last_char = fixed[-1]
        return fixed


def _sse_delta_content(event: str) -> Optional[str]:
    """The reply text carried by one `data: {...}` SSE event, or None if it carries none."""
    stripped = event.strip()
    if not stripped.startswith("data: ") or "\n" in stripped:
        return None
    try:
        obj = json.loads(stripped[6:])
    except (json.JSONDecodeError, ValueError):
        return None
    choices = obj.get("choices") if isinstance(obj, dict) else None
    delta = choices[0].get("delta") if choices and isinstance(choices[0], dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) and content else None


def _text_chunk(request_id: str, model: str, text: str, template: Optional[dict] = None) -> str:
    base = {k: v for k, v in (template or {}).items() if k != "choices"}
    base.setdefault("id", f"chatcmpl-{request_id}")
    base.setdefault("object", "chat.completion.chunk")
    base.setdefault("created", int(time.time()))
    base.setdefault("model", model)
    base["choices"] = [{"index": 0, "delta": {"content": text}, "finish_reason": None}]
    return f"data: {json.dumps(base)}\n\n"


async def sanitize_sse_stream(gen, request_id: str, model: str, state: Optional[dict] = None):
    """`state` (queue: catch-broken-replies, 2026-09-29), when given, gets `state["reply_repair"]`
    set to "repaired"/"fine" once the stream ends -- the streamed counterpart to sanitize_response's
    own (fixed, repairs) return, read back by _finish_stream_impl/_timed_stream_impl so a streamed
    prose reply's tag-fixer repairs count toward the Speeds tab the same way a non-streaming one's
    already do."""
    san = StreamSanitizer(request_id, model)
    try:
        async for event in gen:
            stripped = event.strip()
            if not stripped.startswith("data: ") or "\n" in stripped:
                yield event
                continue
            payload = stripped[6:].strip()
            if payload == "[DONE]":
                tail = san.flush()
                if tail:
                    yield _text_chunk(request_id, model, tail)
                yield event
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                yield event
                continue
            choices = obj.get("choices") if isinstance(obj, dict) else None
            choices = choices or []
            delta = choices[0].get("delta") if choices and isinstance(choices[0], dict) else None
            content = delta.get("content") if isinstance(delta, dict) else None
            finish = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
            if not content:
                if finish:
                    tail = san.flush()
                    if tail:
                        yield _text_chunk(request_id, model, tail, template=obj)
                yield event
                continue
            out = san.feed(content)
            if finish:
                # A chunk carrying both text and the finish marker: nothing may stay held back
                # after it, or the held text (and the marker) would be lost or reordered.
                out += san.flush()
            elif not out:
                continue
            if out == content:
                yield event
                continue
            delta["content"] = out
            yield f"data: {json.dumps(obj)}\n\n"
    finally:
        san.pending = ""
        if state is not None:
            state["reply_repair"] = "repaired" if san.total_repairs else "fine"


# --- 6. Full-reply console log ---
def _log_reply(model: str, request_id: str, text: str) -> None:
    """Print one whole successful reply. Goes through `logger`, so the same credential-redacting
    filter as every other log line applies, and it lands in multiproxy.log too when logging is on."""
    logger.info(f"\n{'-' * 60}\n  REPLY  [{request_id}] ({model})\n{'-' * 60}\n{text}\n{'-' * 60}")


# --- 2. Retry dropped connections ---
# Long-idle keep-alive connections get dropped by the server side now and then; the very next POST
# on that pooled connection fails instantly with a disconnect even though nothing is wrong. A fresh
# attempt succeeds. Only ever applies while OPENING the request (before any response byte) -- once a
# response has started, a retry could duplicate work, so a failure after that is never retried.
POST_OPEN_ATTEMPTS = 3
_RETRYABLE_OPEN_ERRORS = (aiohttp.ServerDisconnectedError, aiohttp.ClientOSError, aiohttp.ClientConnectionError)

# --- Answers compressed in a way MultiProxy cannot read (2026-10-26) ---
# The headers captured from the Claude CLI carry its own Accept-Encoding, which lists `br` (Brotli) and maybe `zstd`. Forwarded upstream, they let Anthropic compress an answer with
# Brotli, which aiohttp can only read with the `Brotli` package (not part of the bundled Python): one request failed with "Can not decode content-encoding: brotli" and the caller got
# a 500. So a request that forwards captured headers asks only for what aiohttp reads on its own, and an answer that still cannot be decoded is asked for once more without compression.
READABLE_ENCODINGS = "gzip, deflate"


def _with_readable_encodings(headers: dict) -> dict:
    """`headers` with Accept-Encoding replaced (whatever its spelling) by the encodings MultiProxy can read."""
    out = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}
    out["Accept-Encoding"] = READABLE_ENCODINGS
    return out


def _is_undecodable_answer(exc: BaseException) -> bool:
    return isinstance(exc, aiohttp.ClientResponseError) and exc.status == 400 and "content-encoding" in str(exc.message or "").lower()


class _post_with_retry:
    """Drop-in for `session.post(url, ...)` inside `async with ... as resp:` that re-tries the
    connection when it drops while opening. On the last attempt the ORIGINAL aiohttp error is
    re-raised (not wrapped), so every provider's existing `except aiohttp.ClientError` handler --
    and the messages it produces -- behaves exactly as before."""

    def __init__(self, session, url: str, *, provider: str, request_id: Optional[str] = None, attempts: Optional[int] = None, **kwargs):
        self._session = session
        self._url = url
        self._provider = provider
        self._request_id = request_id
        self._attempts = attempts or POST_OPEN_ATTEMPTS         # a decision passes 1: a retry would eat the time it has
        self._kwargs = kwargs
        self._ctx = None

    async def __aenter__(self):
        last_exc: Optional[BaseException] = None
        tag = f"[{self._request_id}] " if self._request_id else ""
        identity_tried = False
        attempt = -1
        while attempt + 1 < self._attempts:
            attempt += 1
            ctx = self._session.post(self._url, **self._kwargs)
            try:
                resp = await ctx.__aenter__()
            except aiohttp.ServerTimeoutError:
                # A timeout isn't a dropped connection -- retrying would just triple the wait.
                raise
            except aiohttp.ClientResponseError as e:
                if not _is_undecodable_answer(e) or identity_tried:
                    raise
                # The answer came back compressed in a way this program cannot read (Brotli): ask once more, this time for no compression at all, instead of failing the caller.
                identity_tried = True
                with suppress(Exception):
                    await ctx.__aexit__(type(e), e, e.__traceback__)
                logger.warning(f"{tag}{self._provider} answered in a compression MultiProxy can't read, asking again without compression")
                self._kwargs["headers"] = {**{k: v for k, v in (self._kwargs.get("headers") or {}).items() if k.lower() != "accept-encoding"}, "Accept-Encoding": "identity"}
                attempt -= 1                                    # the extra open does not use up one of the connection-drop attempts
                continue
            except _RETRYABLE_OPEN_ERRORS as e:
                last_exc = e
                with suppress(Exception):
                    await ctx.__aexit__(type(e), e, e.__traceback__)
                if attempt < self._attempts - 1:
                    logger.warning(
                        f"{tag}{self._provider} connection dropped ({type(e).__name__}), "
                        f"retrying (attempt {attempt + 2}/{self._attempts})"
                    )
                    await asyncio.sleep(0.05 * (attempt + 1))
                continue
            self._ctx = ctx
            return resp
        logger.error(
            f"{tag}{self._provider} connection failed after {self._attempts} attempts: "
            f"{type(last_exc).__name__}: {last_exc}"
        )
        raise last_exc

    async def __aexit__(self, exc_type, exc, tb):
        ctx, self._ctx = self._ctx, None
        if ctx is not None:
            return await ctx.__aexit__(exc_type, exc, tb)
        return False


def _connect_timeout_kwargs(session, connect_timeout: Optional[float]) -> dict:
    """`timeout=` for one POST: the session's own timeouts, plus a short wait just for the connection (a device that doesn't pick up)."""
    if not connect_timeout:
        return {}
    t = session.timeout
    return {"timeout": aiohttp.ClientTimeout(total=t.total, connect=t.connect, sock_read=t.sock_read, sock_connect=connect_timeout)}


# --- 3. Trim oversized chats ---
# When a provider rejects a request because the chat is longer than the model can take, drop the
# oldest history and try again -- but only for that specific rejection, only a few times, and never
# touching the system prompt. (Raydonn's version looped until it worked, which could spin forever.)
MAX_CONTEXT_TRIMS = 3
_TRIM_MARKER = "...[SYSTEM AUTO-TRIMMED]...\n"
_CONTEXT_LIMIT_MSG_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?(?:resulted in|requested) (\d+) tokens", re.S | re.I
)


def _context_overflow_excess(status: int, error_text: str) -> Optional[int]:
    """None when this isn't a "chat too long" rejection. Otherwise the exact number of tokens over
    the limit when the provider said so, or 0 when it only said "too long" without numbers."""
    if status not in (400, 413) or "context_length_exceeded" not in (error_text or ""):
        return None
    try:
        parsed = json.loads(error_text)
    except (json.JSONDecodeError, TypeError):
        return 0
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(err, dict):
        return None
    if "context_length_exceeded" not in (err.get("code"), err.get("type")):
        return None
    # GLM's shape: the token counts come as separate fields.
    ints = [err.get(k) for k in ("input_tokens", "output_tokens", "max_context_tokens")]
    if all(isinstance(v, int) for v in ints):
        return max(0, ints[0] + ints[1] - ints[2])
    # Standard OpenAI-compatible shape: the counts are only in the message text.
    m = _CONTEXT_LIMIT_MSG_RE.search(str(err.get("message") or ""))
    if m:
        return max(0, int(m.group(2)) - int(m.group(1)))
    return 0


def _msg_chars(message: dict) -> int:
    content = message.get("content")
    return len(content) if isinstance(content, str) else len(_content_as_text(content))


def _trim_messages_for_context(messages: list, excess_tokens: Optional[int] = None) -> tuple[list, int]:
    """Drop oldest chat history first, keeping the FIRST message (it often holds the diary/scenario
    context) and the NEWEST one. `messages` is the chat only -- the system prompt is passed
    separately everywhere and is never part of this list. Returns (new_messages, chars_removed);
    chars_removed == 0 means there was nothing left that could safely be cut.

    Exact overage -> excess_tokens * 4.5 characters. Unknown overage -> about a quarter of the chat.
    """
    new_messages = [dict(m) for m in messages]
    total = sum(_msg_chars(m) for m in new_messages)
    if excess_tokens and excess_tokens > 0:
        chars_to_remove = int(excess_tokens * 4.5)
    else:
        chars_to_remove = int(total * 0.25)
    chars_to_remove = max(chars_to_remove, 1)
    removed = 0

    while new_messages and removed < chars_to_remove:
        if len(new_messages) > 2:
            # Protect index 0 (context) and index -1 (latest prompt); drop the oldest in between.
            removed += _msg_chars(new_messages[1])
            new_messages.pop(1)
            continue
        # Down to one or two messages: cut text off the top of the newest one that is plain text.
        for target in ((1, 0) if len(new_messages) == 2 else (0,)):
            content = new_messages[target].get("content")
            if isinstance(content, str) and content:
                if content.startswith(_TRIM_MARKER):
                    content = content[len(_TRIM_MARKER):]
                # Always keep the last quarter, so a trim can never wipe out the message entirely.
                cut = min(chars_to_remove - removed, int(len(content) * 0.75))
                if cut > 0:
                    new_messages[target]["content"] = _TRIM_MARKER + content[cut:]
                    removed += cut
                    return new_messages, removed
        break
    return new_messages, removed


class _ContextTrimmer:
    """One per upstream call. Decides whether a rejected request is worth re-sending shorter."""

    def __init__(self, provider: str, request_id: str):
        self.provider = provider
        self.request_id = request_id
        self.attempts = 0

    def retry_messages(self, status: int, error_text: str, messages: list) -> Optional[list]:
        """The shorter chat to retry with, or None to stop and surface the original error."""
        excess = _context_overflow_excess(status, error_text)
        if excess is None:
            return None
        if self.attempts >= MAX_CONTEXT_TRIMS:
            logger.error(
                f"[{self.request_id}] {self.provider} still says the chat is too long after "
                f"{MAX_CONTEXT_TRIMS} trims -- giving up"
            )
            return None
        trimmed, removed = _trim_messages_for_context(messages, excess)
        if removed <= 0:
            logger.warning(f"[{self.request_id}] {self.provider} says the chat is too long, but there's nothing left to trim")
            return None
        self.attempts += 1
        logger.warning(
            f"[{self.request_id}] {self.provider} says the chat is too long -- cut about {removed:,} "
            f"characters of older history and trying again (trim {self.attempts} of {MAX_CONTEXT_TRIMS})"
        )
        return trimmed


# --- 4/5. The request line (per-provider concurrency limits) ---
# (ini key, internal provider id, label shown to the player, default limit) -- 0 means no limit.
# Every provider reached through an HTTP API; the CLI-subprocess ones (Codex, Antigravity) aren't here.
_REQUEST_LINE_PROVIDERS = (
    ("Claude", "claude", "Claude", 0),
    ("GLM", "glm", "GLM", 6),
    ("NanoGPT", "nano", "Nano-GPT", 0),
    ("OpenAI", "openai", "OpenAI", 0),
    ("DeepSeek", "deepseek", "DeepSeek", 0),
    ("Kilo", "kilo", "Kilo Gateway", 0),
    ("Gemini", "gemini", "Gemini", 0),
    ("XAI", "xai", "xAI", 0),
    ("Continuum", "continuum", "Continuum", 0),
    ("Ollama", "ollama", "Ollama", 0),
    ("LMStudio", "lmstudio", "LM Studio", 0),
) + tuple((_s.ini_key, _s.id, _s.pill, _s.limit_default) for _s in key_sources.SOURCES)
_REQUEST_LINE_LABELS = {pid: label for _, pid, label, _ in _REQUEST_LINE_PROVIDERS}


def _read_concurrency_ini(cfg: configparser.ConfigParser) -> tuple[dict[str, int], float]:
    limits: dict[str, int] = {}
    for ini_key, pid, _label, default in _REQUEST_LINE_PROVIDERS:
        try:
            value = cfg.getint("Concurrency", ini_key, fallback=default)
        except ValueError:
            logger.warning(f"multiproxy.ini [Concurrency] {ini_key} isn't a whole number -- using {default}")
            value = default
        limits[pid] = max(0, value)
    try:
        timeout = cfg.getfloat("Concurrency", "QueueTimeoutSeconds", fallback=10.0)
    except ValueError:
        logger.warning("multiproxy.ini [Concurrency] QueueTimeoutSeconds isn't a number -- using 10")
        timeout = 10.0
    return limits, timeout


REQUEST_LIMITS, QUEUE_TIMEOUT_SECONDS = _read_concurrency_ini(_proxy_ini_cfg)
RETRY_AFTER_SECONDS = 5  # what a 429 from a full line tells SkyrimNet to wait


class _RequestLineTimeout(Exception):
    pass


class _Waiter:
    """One request standing in a line: who it is (its job's place in the order), since when, and the jobs that went ahead of it though they came later."""
    __slots__ = ("fut", "rank", "at", "seq", "label", "behind", "aging")

    def __init__(self, fut, rank: int, at: float, seq: int, label: str, aging: float):
        self.fut, self.rank, self.at, self.seq, self.label, self.behind, self.aging = fut, rank, at, seq, label, [], max(0.001, aging)


class _RequestLine:
    """Counts how many requests to one provider are running (`active`) and how many are waiting
    (`queued`). Own counters, not `asyncio.Semaphore._value` -- that private field never goes
    negative, so it can't say how many are waiting. A finished request hands its slot straight to
    the next one, so `active` never dips below `limit` while anyone is waiting.

    Who is next (Routing, what goes first): the waiting request whose job stands highest in the player's order, first come first served among equals. A request
    that has waited moves up one place in that order for every `aging_s` seconds (so its effective place is `rank - waited // aging_s`): any request, whatever its
    job, outranks every request that arrives after it once it has waited `aging_s * (rank + 1)` seconds, so nothing waits forever (the line's own QueueTimeoutSeconds
    still ends a wait with a 429 first when that is shorter). Requests with no job (rank equal for all) are served exactly in the order they came, as before.
    A request that is already running is never touched."""

    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0
        self._waiters: list = []
        self._seq = 0

    @property
    def queued(self) -> int:
        return len(self._waiters)

    @property
    def is_full(self) -> bool:
        return self.active >= self.limit or bool(self._waiters)

    async def acquire(self, timeout: float, rank: int = 0, label: str = "", aging_s: float = 5.0) -> list:
        """Wait for a place. Returns the labels of the jobs that were served ahead of this request although they arrived after it (empty when it never waited
        or was served in its turn)."""
        if not self.is_full:
            self.active += 1
            return []
        fut = asyncio.get_running_loop().create_future()
        self._seq += 1
        w = _Waiter(fut, rank, time.monotonic(), self._seq, label, aging_s)
        self._waiters.append(w)
        try:
            await asyncio.wait_for(fut, timeout if timeout and timeout > 0 else None)
        except asyncio.TimeoutError:
            if fut.done() and not fut.cancelled():
                return list(w.behind)  # the slot was handed to us at the very last moment -- take it
            with suppress(ValueError):
                self._waiters.remove(w)
            raise _RequestLineTimeout()
        except BaseException:
            if fut.done() and not fut.cancelled():
                self.release()  # we were handed a slot but are being cancelled -- pass it on
            else:
                with suppress(ValueError):
                    self._waiters.remove(w)
            raise
        return list(w.behind)

    def _next(self):
        now = time.monotonic()
        live = [w for w in self._waiters if isinstance(w, _Waiter) and not w.fut.done()]
        if not live:
            return None
        return min(live, key=lambda w: (w.rank - int((now - w.at) // w.aging), w.seq))

    def release(self) -> None:
        w = self._next()
        if w is not None:
            self._waiters = [x for x in self._waiters if x is not w and not (isinstance(x, _Waiter) and x.fut.done())]
            for other in self._waiters:                    # everyone who came before it was passed over: they learn who went first
                if isinstance(other, _Waiter) and other.seq < w.seq and w.label and w.label not in other.behind:
                    other.behind.append(w.label)
            w.fut.set_result(None)  # slot handed to the next in line; `active` stays as it was
            return
        self._waiters = [x for x in self._waiters if not (isinstance(x, _Waiter) and x.fut.done())]
        self.active = max(0, self.active - 1)


class _Slot:
    """One held place in a request line. release() is safe to call any number of times."""

    def __init__(self, line: _RequestLine):
        self._line = line
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._line.release()


_request_lines: dict[str, _RequestLine] = {}


def _get_request_line(provider_id: str, limit: int) -> _RequestLine:
    line = _request_lines.get(provider_id)
    if line is None or line.limit != limit:
        line = _request_lines[provider_id] = _RequestLine(limit)
    return line


async def _acquire_request_slot(provider_id: str, request_id: str, rank: Optional[int] = None, job_label: str = "", aging_s: Optional[float] = None) -> Optional[_Slot]:
    """Wait for a place in this provider's line. None when the provider has no limit set. Raises a
    429 (with Retry-After) if the wait outlasts QueueTimeoutSeconds. 429, not 503 and not a long
    wait: SkyrimNet's smart routing treats 429 as a rate limit and moves on to another provider,
    whereas a request that just sits there for a minute only looks slow to it -- it races a second
    request against it, which lengthens the line further."""
    limit = REQUEST_LIMITS.get(provider_id, 0)
    if limit <= 0:
        return None
    line = _get_request_line(provider_id, limit)
    label = _REQUEST_LINE_LABELS.get(provider_id, provider_id)
    waited_from = time.time()
    if line.is_full:
        logger.info(f"[{request_id}] {label} is busy ({line.active} of {limit}) -- waiting for a free spot")
    try:
        if rank is None:                                      # the order of jobs the player set (Routing tab); a request with no job is equal to all the rest
            rec = request_facts.current()
            rank = rec.queue_rank if rec is not None and rec.queue_rank is not None else 1_000_000
            job_label = job_label or (rec.job if rec is not None and rec.queue_rank is not None else "")
            aging_s = aging_s or (rec.queue_aging_s if rec is not None else None)
        behind = await line.acquire(QUEUE_TIMEOUT_SECONDS, rank, job_label, aging_s or routing.DEFAULT_AGING_S)
    except _RequestLineTimeout:
        logger.error(f"[{request_id}] {label} line timed out after {QUEUE_TIMEOUT_SECONDS:g}s ({limit} spots, all busy)")
        spots = "its only spot" if limit == 1 else f"all {limit} of its spots"
        raise HTTPException(
            status_code=429,
            detail=(
                f"{label} is busy right now. {spots} stayed full for "
                f"{QUEUE_TIMEOUT_SECONDS:g} seconds, so this request stepped aside. Try again in a "
                f"few seconds, or raise the {label} limit under Advanced in Settings."
            ),
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    waited = time.time() - waited_from
    if waited > 0.05:
        logger.info(f"[{request_id}] {label} spot opened up after {waited:.1f}s" + (f" (behind {', '.join(behind)})" if behind else ""))
    slot = _Slot(line)
    slot.behind = list(behind)                                 # the jobs that went ahead of this one while it waited
    return slot


def _request_line_snapshot() -> dict:
    """{provider id: {active, queued, limit}} for every provider that has a limit set."""
    out = {}
    for _ini, pid, _label, _default in _REQUEST_LINE_PROVIDERS:
        limit = REQUEST_LIMITS.get(pid, 0)
        if limit <= 0:
            continue
        line = _request_lines.get(pid)
        out[pid] = {
            "active": line.active if line and line.limit == limit else 0,
            "queued": line.queued if line and line.limit == limit else 0,
            "limit": limit,
        }
    return out


# --- Streaming: real HTTP errors before the first byte, a clean end after it ---
# SkyrimNet's smart routing (Beta 25) can only route around a provider it can TELL failed. A stream
# that answers "200 OK" and then carries the error inside it looks like a success -- and error text
# sent as a reply can even be spoken by an NPC. So provider streaming generators just RAISE when
# something goes wrong, and this is what decides what that means:
#   - before the first event: the request fails with a real HTTP status and the normal
#     {"error": {...}} body (_prime_stream, called before the StreamingResponse is created);
#   - after the first event: the status can't change any more, so the stream is simply ended with
#     `data: [DONE]` -- no error text is ever added to the reply (_guarded_events).
_DONE_EVENT = "data: [DONE]\n\n"
DISCONNECT_POLL_SECONDS = 0.5


GAVE_UP = "gave up"          # the saved reason for a request SkyrimNet stopped waiting for before the AI answered


class _ClientGone(Exception):
    """SkyrimNet hung up before there was anything to send back. Routine in Beta 25: its
    first-token guard starts a second request when one is slow and drops whichever loses."""


async def _aclose_quietly(gen) -> None:
    aclose = getattr(gen, "aclose", None)
    if aclose:
        with suppress(Exception):
            await aclose()


def _release_slot(slot: Optional[_Slot]) -> None:
    if slot:
        slot.release()


async def _await_unless_disconnected(request, awaitable, cleanup: Optional[Callable] = None):
    """Await `awaitable`, but stop early (cancelling it, so upstream connections and CLI
    subprocesses are cleaned up) if the client disconnects meanwhile. Raises _ClientGone.
    `cleanup(result)` is called if the awaitable happened to finish at the very moment the client
    left, so something it acquired (a request-line slot) isn't stranded."""
    if request is None:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    hung_up = False
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=DISCONNECT_POLL_SECONDS)
            if done:
                return task.result()
            try:
                gone = await request.is_disconnected()
            except Exception:
                gone = False
            if gone:
                hung_up = True
                raise _ClientGone()
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if hung_up and cleanup and task.done() and not task.cancelled() and task.exception() is None:
            cleanup(task.result())


PRIME_MAX_EVENTS = 5000      # a stream that sends this many events without one word is let through as it is (thinking models can send a lot before they write)


async def _prime_stream(gen, request_id: str) -> list:
    """The events of a provider's stream up to its first real word. A provider that fails before sending anything raises here, while it can still become a proper HTTP
    error response; a stream that ends without one word in it (nothing, only whitespace or only tags) is an EmptyReply, also raised here, so the route's backup can answer
    and SkyrimNet is never sent an empty stream as if it were a line. The events are held back only until the first word arrives (they are the lead-in to it)."""
    held: list = []
    said = ""
    thought = False
    try:
        while True:
            try:
                event = await gen.__anext__()
            except StopAsyncIteration:
                if not held:
                    raise HTTPException(status_code=502, detail="The AI sent back an empty reply.")
                break
            held.append(event)
            text, reasoned, tool, err = engine_trouble.event_facts(event)
            if err and not said.strip():
                raise HTTPException(status_code=502, detail=_clean_stream_error(err))
            thought = thought or reasoned
            said += text
            if tool or not engine_trouble.says_nothing(said) or len(held) >= PRIME_MAX_EVENTS:
                return held
            if event.strip() == "data: [DONE]":
                break
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"[{request_id}] Unexpected error starting a stream")
        raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")
    logger.warning(f"[{request_id}] the AI answered with nothing" + (" (it only wrote its thoughts)" if thought else ""))
    raise EmptyReply(thought)


def _clean_stream_error(err: str) -> str:
    """An error an AI put inside its stream, as a plain one-line message."""
    return _extract_upstream_error_message(err) if err.lstrip().startswith("{") else str(err)[:300]


async def _guarded_events(source, first: Optional[str], request_id: str, model: str, state: dict):
    """`first`, then the rest of `source`. A failure after the first event ends the stream cleanly
    with just `data: [DONE]` -- the reply text so far stands, and nothing about the failure is ever
    written into it."""
    saw_done = False
    for lead in ([] if first is None else [first] if isinstance(first, str) else first):
        saw_done = saw_done or lead.strip() == "data: [DONE]"
        yield lead
    try:
        async for event in source:
            if event.strip() == "data: [DONE]":
                saw_done = True
            yield event
    except Exception as e:
        state["ended_early"] = True
        state["reason"] = _fail_reason(e)
        detail = e.detail if isinstance(e, HTTPException) else f"{type(e).__name__}: {e}"
        if state.get("pc_error") and not saw_done:
            # Another device (with a backup set) stopped partway: no switch (words are already out), a real error instead, so
            # SkyrimNet's own retry acts. The device is marked offline already, so the next request goes to the backup.
            logger.warning(f"[{request_id}] {state['pc_error']} stopped answering partway ({detail}) -- ended the reply with an error; "
                           "the next request goes to the backup")
            yield "data: " + json.dumps({"error": {"message": f"{state['pc_error']} stopped answering partway.", "type": "api_error", "code": None}}) + "\n\n"
            return
        logger.warning(f"[{request_id}] {model} stream stopped partway ({detail}) -- ended it cleanly")
        if not saw_done:
            yield _DONE_EVENT


def _finish_stream(source, request_id: str, model: str, slot: Optional[_Slot], first: Optional[str] = None,
                   state: Optional[dict] = None):
    """Wrap a provider's SSE generator (whose first event, if already read by _prime_stream, is
    `first`) with the clean-ending guard, the tag fixer (FixNpcTags), the full-reply log
    (LogFullReplies) and release of the request-line slot."""
    gen = _finish_stream_impl(source, request_id, model, FIX_NPC_TAGS, LOG_FULL_REPLIES, slot, first, state)
    if slot:
        # Backstop for a stream that's dropped before it ever starts (a generator that never
        # began can't run its own `finally`): garbage collection still gives the slot back.
        weakref.finalize(gen, slot.release)
    return gen


async def _finish_stream_impl(
    source, request_id: str, model: str, fix: bool, log_replies: bool, slot: Optional[_Slot], first: Optional[str],
    state: Optional[dict] = None,
):
    state = state if state is not None else {}
    state.setdefault("ended_early", False)
    events = _guarded_events(source, first, request_id, model, state)
    stream = sanitize_sse_stream(events, request_id, model, state=state) if fix else events
    parts: list[str] = []
    hung_up = False
    try:
        async for event in stream:
            if log_replies:
                content = _sse_delta_content(event)
                if content:
                    parts.append(content)
            yield event
        if log_replies and parts and not state["ended_early"]:
            _log_reply(model, request_id, "".join(parts))
    except (GeneratorExit, asyncio.CancelledError):
        hung_up = True
        raise
    finally:
        # Slot first: this must happen even if the client hung up mid-stream and cleanup is cut short.
        if slot:
            slot.release()
        if hung_up:
            logger.info(f"[{request_id}] SkyrimNet hung up before the reply was finished -- gave up on {model} and stopped it")
        # SkyrimNet hanging up lands here too: closing each layer in turn closes the upstream HTTP
        # response and, for the CLI providers, kills the child process (see their own `finally`).
        for gen in (stream, events, source):
            await _aclose_quietly(gen)


def _real_output_tokens(usage: Optional[dict], openai_shaped: bool) -> Optional[int]:
    """The service's own output-token count for a reply, when it reported one."""
    if not isinstance(usage, dict):
        return None
    value = usage.get("completion_tokens" if openai_shaped else "output_tokens")
    return value if isinstance(value, int) else None


async def _acquire_slot_recorded(request, line_key, request_id, stats_model, provider):
    """Wait for a place in the request line. A refusal because the line stayed full is saved as a
    failed call with the reason `busy`, so the Speeds tab can say "too busy" instead of just "failed"."""
    started = time.time()
    _pc0 = time.perf_counter()
    try:
        slot = await _await_unless_disconnected(
            request, _acquire_request_slot(line_key, request_id), cleanup=_release_slot,
        )
        rec = request_facts.current()
        if rec is not None:
            rec.queue_wait_s = time.time() - started          # how long this request stood in line (the record's "waited")
            rec.queue_behind = list(getattr(slot, "behind", []) or [])
            rec.attempt_started(time.perf_counter() - _pc0)   # the AI is handed the request from here (MultiProxy's own time stops, the AI's starts)
        return slot
    except HTTPException as e:
        _rec_w = request_facts.current()
        if _rec_w is not None:
            _rec_w.waited_s += time.perf_counter() - _pc0
        if e.status_code == 429:
            await _record_speed(stats_model, time.time() - started, False, fail_reason="busy", provider=provider)
        raise


# How long a local engine (Ollama, LM Studio) has to say its first word before MultiProxy gives up on the request and cancels it at the engine (engine_trouble.py says how
# the number was chosen). The route's own "no first word after" setting wins when it has one, and the route Test button asks for much more (a cold model has to load).
_wait_budget: contextvars.ContextVar = contextvars.ContextVar("wait_budget", default=None)


def _first_word_budget(provider: Optional[str], whole_reply: bool = False) -> Optional[float]:
    """Seconds this request's engine has to start answering (a stream's first word) or to finish (a reply that isn't streamed); None for anything but a local engine."""
    if provider not in engine_trouble.LOCAL_ENGINES or (provider == "Ollama" and ollama_api_key):
        return None
    return _wait_budget.get() or (engine_trouble.WHOLE_REPLY_S if whole_reply else engine_trouble.FIRST_WORD_S)


async def _within(awaitable, budget: Optional[float], provider: Optional[str]):
    """Await `awaitable`; past `budget` seconds it is cancelled (which closes the request at the engine, so the engine isn't kept busy with it) and a plain 504 is raised."""
    if not budget:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, budget)
    except asyncio.TimeoutError:
        raise EngineFailure(504, engine_trouble.too_slow(provider or "The AI"))


ENGINE_PROBES = True                  # tests turn this off: a probe is a real request to a real engine
_probing: dict = {}                   # model card key -> when its last load check started (time.monotonic)
_background: set = set()              # the load checks under way (held so they aren't collected while they run)


def _after_silence(provider: Optional[str], model: str) -> None:
    """A request to a local engine was given up on before its first word (SkyrimNet hung up, or the time ran out). The engine's own answer arrives long after the game stopped waiting
    (the real case: a model that could not load said so after nearly three minutes), so the reason is found by one small, patient check of its own: it asks the model for a single
    word and waits. The answer is kept as the model's note (or, if the model loads, its trouble is cleared). At most one check per model every few minutes."""
    if not ENGINE_PROBES or provider not in engine_trouble.LOCAL_ENGINES or (provider == "Ollama" and ollama_api_key):
        return
    key = speed_queries.card_key(provider, model)
    now = time.monotonic()
    if now - _probing.get(key, -1e9) < engine_trouble.PROBE_EVERY_S:
        return
    _probing[key] = now
    task = asyncio.ensure_future(_probe_engine(provider, model, key))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _probe_engine(provider: str, model: str, key: str) -> None:
    call = call_ollama_direct if provider == "Ollama" else call_lmstudio_direct
    t0 = time.time()
    try:
        await asyncio.wait_for(call(None, [{"role": "user", "content": "Hi"}], model, 1), engine_trouble.PROBE_PATIENCE_S)
    except asyncio.TimeoutError:
        note = engine_trouble.too_slow(provider).clause
    except HTTPException as e:
        note = _fail_note(e)
        if note is None:
            t = engine_trouble.explain(provider, e.status_code, str(e.detail))
            note = t.clause if t else None
    except Exception:
        return
    else:
        _trouble.clear(key)
        logger.info(f"{provider} loaded {model} for a check in {time.time() - t0:.0f} s")
        return
    if note:
        _trouble.set(key, note)
        logger.warning(f"{provider} {model}: {note}")
        await asyncio.to_thread(_attach_fail_note, provider, model, note)


def _attach_fail_note(provider: str, model: str, note: str) -> None:
    """Put a reason found late on the recent given-up requests of that model that have none (a note written for the history, never a new request)."""
    try:
        speed_store.flush(5.0)
        with sqlite3.connect(SPEED_HISTORY_DB) as conn:
            conn.execute("UPDATE speed_history SET fail_note = ? WHERE rowid IN (SELECT rowid FROM speed_history WHERE provider = ? AND model = ? AND success = 0 AND fail_note IS NULL "
                         "AND ts > ? ORDER BY ts DESC LIMIT 20)", (note, provider, model, time.time() - engine_trouble.TROUBLE_KEEP_S))
    except Exception as e:
        logger.warning(f"Couldn't save a model's note ({e.__class__.__name__}); the pages still show it until MultiProxy restarts")


async def _open_stream(
    request, gen, request_id: str, model: str, stats_model: str, slot: Optional[_Slot],
    provider: Optional[str] = None, pc_error: Optional[str] = None, first_word_budget: Optional[float] = None,
) -> StreamingResponse:
    """Read the provider stream's events up to the first word (failing here means a real HTTP error: an empty reply is one too), then hand the
    rest to a StreamingResponse. The slot is released on every path that doesn't reach the stream. `first_word_budget`: a local engine that has not
    said a word by then is given up on and its request cancelled (a failure, so the route's backup is tried)."""
    start = time.time()
    try:
        first = await _await_unless_disconnected(request, _within(_prime_stream(gen, request_id), first_word_budget, provider))
    except BaseException as e:
        if slot:
            slot.release()
        await _aclose_quietly(gen)
        if isinstance(e, _ClientGone):
            await _record_speed(stats_model, time.time() - start, False, fail_reason=GAVE_UP, provider=provider)     # asked, and SkyrimNet gave up before it answered
            _after_silence(provider, stats_model)
        elif isinstance(e, Exception):
            await _record_speed(stats_model, time.time() - start, False, fail_reason=_fail_reason(e), provider=provider, fail_note=_fail_note(e))
            if first_word_budget and _fail_note(e) == engine_trouble.SLOW_CLAUSE:
                _after_silence(provider, stats_model)
        raise
    state: dict = {"pc_error": pc_error} if pc_error else {}  # shared: lets the speed timer see a stream that failed partway
    return StreamingResponse(
        _timed_stream(_finish_stream(gen, request_id, model, slot, first, state), stats_model, start,
                      provider=provider, state=state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _drain_sse_stream(source) -> tuple:
    """Fully drains a provider's raw SSE async generator into (full_text, usage_dict) -- the
    JSON-hold path's own equivalent of just reading a non-streaming call's return value. Used only
    for a request whose own prompt asked for JSON back (see _expects_json_reply): that reply has to
    be checked whole before anything reaches SkyrimNet, same as a non-streaming reply already is,
    so there is no live per-chunk pass-through to build here."""
    parts: list[str] = []
    usage: dict = {}
    async for event in source:
        content = _sse_delta_content(event)
        if content:
            parts.append(content)
        stripped = event.strip()
        if stripped.startswith("data: {"):
            try:
                obj = json.loads(stripped[6:])
            except (json.JSONDecodeError, ValueError):
                obj = None
            if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                usage = obj["usage"]
    return "".join(parts), usage


async def _open_json_stream(
    request, gen, request_id: str, model: str, stats_model: str, slot: Optional[_Slot],
    provider: Optional[str], retry_gen_fn, request_start: float,
) -> StreamingResponse:
    """The JSON-expecting counterpart to _open_stream (queue: catch-broken-replies, 2026-09-29) --
    holds the WHOLE stream until it ends, then checks/repairs/retries it exactly like the
    non-streaming path (_check_and_repair_json_reply), and only then answers SkyrimNet: either one
    single, already-valid SSE reply, or a real HTTP error if it's still broken after asking again.
    Nothing is ever forwarded chunk-by-chunk -- SkyrimNet cannot usefully consume half of a JSON
    object, and checking anything less than the complete reply would defeat the point of this
    feature. `retry_gen_fn` (optional) returns a FRESH streaming generator for the one retry.

    Not exercised by any real traffic yet -- confirmed live (step 1 of this feature): 0 of 345 real
    captured streaming requests ever asked for JSON. Built to spec anyway, since a future job could
    stream and still expect JSON, and "hold the stream, check it at the end" is exactly what the
    task asked for regardless of how often it currently happens.
    """
    start = time.time()
    try:
        text, usage = await _await_unless_disconnected(request, _drain_sse_stream(gen))
    except BaseException as e:
        if slot:
            slot.release()
        await _aclose_quietly(gen)
        if isinstance(e, _ClientGone):
            await _record_speed(stats_model, time.time() - start, False, fail_reason=GAVE_UP, provider=provider)     # asked, and SkyrimNet gave up before it answered
        elif isinstance(e, Exception):
            await _record_speed(stats_model, time.time() - start, False, fail_reason=_fail_reason(e), provider=provider, fail_note=_fail_note(e))
        raise
    if slot:
        slot.release()
    if engine_trouble.says_nothing(text, structured=True):
        await _record_speed(stats_model, time.time() - start, False, fail_reason=EMPTY, fail_note=engine_trouble.EMPTY_CLAUSE, provider=provider)
        raise EmptyReply()

    async def _retry():
        return await _drain_sse_stream(retry_gen_fn())

    try:
        final_text, reply_repair, extra_failure = await _check_and_repair_json_reply(
            text, True, _retry if retry_gen_fn else None, request, request_id, model, request_start,
        )
    except _StillBrokenJsonReply:
        await _record_speed(stats_model, time.time() - start, False, fail_reason="broken reply", provider=provider)
        raise HTTPException(status_code=502, detail="The AI gave a broken answer, even after asking again.")

    await _record_speed(
        stats_model, time.time() - start, True, provider=provider, out_chars=len(final_text),
        out_tokens=_real_output_tokens(usage, True), reply_repair=reply_repair,
    )
    if extra_failure:
        # FixBrokenReplies is off -- the reply above is untouched, but this still counts (see
        # _check_and_repair_json_reply's own docstring).
        await _record_speed(stats_model, 0.0, False, fail_reason="broken reply", provider=provider)

    async def _single_shot():
        yield _text_chunk(request_id, model, final_text)
        finish_obj = {
            "id": f"chatcmpl-{request_id}", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(finish_obj)}\n\n"
        yield _DONE_EVENT

    return StreamingResponse(
        _single_shot(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Auth cache + persistent session (with single-flight refresh) ---

class AuthCache:
    # Claude is optional now (see capture_auth/CLAUDE_PATH) -- a broken/unavailable Claude setup
    # must not spawn a fresh `claude --print` on every single Claude-routed request. Once an attempt
    # has failed, later requests reuse that same failure for this long before trying again.
    RECAPTURE_COOLDOWN = 30.0  # seconds

    def __init__(self):
        self.headers: Optional[dict] = None
        self.body_template: Optional[dict] = None
        self.session: Optional[aiohttp.ClientSession] = None

        # Why the last capture attempt failed (a short, human-readable reason -- e.g. the Claude
        # CLI's own one-line explanation), or None if it never failed / hasn't been tried yet.
        # Read by the dashboard and by chat_completions' Claude-unavailable error path.
        self.last_error: Optional[str] = None
        self._last_attempt: float = 0.0

        # Single-flight refresh controls
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

    @property
    def is_ready(self) -> bool:
        return self.headers is not None and self.body_template is not None

    def invalidate(self) -> None:
        self.headers = None
        self.body_template = None

    async def ensure_ready(self, *, force: bool = False, timeout: float = 60.0) -> None:
        """
        If auth is ready -> return.
        If a refresh is already running -> wait for it.
        Else -> start ONE capture_auth() and everyone waits for it, UNLESS the last attempt failed
        recently (within RECAPTURE_COOLDOWN) and this isn't a forced retry -- in that case, raise
        the same failure reason immediately instead of spawning another `claude --print`.
        """
        async with self._refresh_lock:
            if self.is_ready and not force:
                return

            # If a refresh is already running, reuse it.
            if self._refresh_task and not self._refresh_task.done():
                task = self._refresh_task
            else:
                now = time.time()
                if not force and self.last_error and (now - self._last_attempt) < self.RECAPTURE_COOLDOWN:
                    raise RuntimeError(self.last_error)
                # Start a new refresh
                self.invalidate()
                self._last_attempt = now
                self._refresh_task = asyncio.create_task(capture_auth())
                task = self._refresh_task

        # Await outside the lock so other requests can queue behind the same task.
        try:
            await asyncio.wait_for(task, timeout=timeout)
        finally:
            if task.done():
                async with self._refresh_lock:
                    if self._refresh_task is task:
                        self._refresh_task = None

        if not self.is_ready:
            raise RuntimeError(self.last_error or "The sign-in refresh finished, but MultiProxy couldn't save your details.")

auth = AuthCache()


# --- GLM (z.ai) / Nano-GPT + Round-Robin state ---
_cfg = _load_config()
glm_api_key: Optional[str] = _cfg.get("glm_api_key") or None
if glm_api_key:
    logger.info("GLM (z.ai) API key loaded from config.json")
nano_api_key: Optional[str] = _cfg.get("nano_api_key") or None
if nano_api_key:
    logger.info("Nano-GPT API key loaded from config.json")
perplexity_api_key: Optional[str] = _cfg.get("perplexity_api_key") or None
if perplexity_api_key:
    logger.info("Perplexity API key loaded from config.json")
openai_api_key: Optional[str] = _cfg.get("openai_api_key") or None
if openai_api_key:
    logger.info("OpenAI API key loaded from config.json")
kilo_api_key: Optional[str] = _cfg.get("kilo_api_key") or None
if kilo_api_key:
    logger.info("Kilo Gateway API key loaded from config.json")
LMSTUDIO_DEFAULT_BASE_URL = "http://localhost:1234/v1"      # (Codex's LM Studio preset shares this)
LMSTUDIO_PREFIX = "lmstudio/"
lmstudio_base_url: str = _cfg.get("lmstudio_base_url") or LMSTUDIO_DEFAULT_BASE_URL
ollama_api_key: Optional[str] = _cfg.get("ollama_api_key") or None  # only needed for Ollama Cloud; local Ollama needs no key
if ollama_api_key:
    logger.info("Ollama Cloud API key loaded from config.json")
gemini_api_key: Optional[str] = _cfg.get("gemini_api_key") or None
if gemini_api_key:
    logger.info("Gemini API key loaded from config.json")
xai_api_key: Optional[str] = _cfg.get("xai_api_key") or None
if xai_api_key:
    logger.info("xAI API key loaded from config.json")
continuum_api_key: Optional[str] = _cfg.get("continuum_api_key") or None
if continuum_api_key:
    logger.info("Continuum API key loaded from config.json")
deepseek_api_key: Optional[str] = _cfg.get("deepseek_api_key") or None
if deepseek_api_key:
    logger.info("DeepSeek API key loaded from config.json")
typesafe_api_key: Optional[str] = _cfg.get("typesafe_api_key") or None
if typesafe_api_key:
    logger.info("TypeSafe (Jev) API key loaded from config.json")
_ks_keys: dict = {_s.id: (_cfg.get(_s.config_key) or None) for _s in key_sources.SOURCES}       # Cloudflare, Mistral, Groq (key_sources.py)
for _s in key_sources.SOURCES:
    if _ks_keys[_s.id]:
        logger.info(f"{_s.pill} API key loaded from config.json")
# Codex multi-provider support (2026-09-16) -- proxy-owned list of ADDITIONAL Codex providers,
# entirely separate from the implicit, always-present default ChatGPT/Codex-subscription entry
# below (which is never stored here -- see is_codex_model/_codex_resolve_provider). Each entry:
# {id, type: "builtin"|"custom", preset?: "openai"|"ollama"|"lmstudio", name, base_url, api_key?}.
# See _run_codex_exec/_codex_provider_cli_args for how each entry gets injected into a real `codex
# exec` call with no config.toml ever written and the player's own ~/.codex/config.toml never read.
codex_providers: list = _cfg.get("codex_providers") or []
if codex_providers:
    logger.info(f"{len(codex_providers)} saved Codex provider(s) loaded from config.json")
# Codex "pin a model" (2026-09-16) -- neither the default provider's own curated model_cache.json
# list nor an added provider's total lack of a listing API can ever be a genuinely complete model
# list (confirmed live: `codex -m gpt-6-astra` works even though the account's own cache never
# listed it). Keyed by provider id ("default" for the ChatGPT subscription, else a saved provider's
# own id) -> list of {id, name}. See _codex_pinned_rows for how these get merged into a provider's
# own "models" list for display -- no live validation an id is real, same as today's free-typed
# model IDs; a bad one just fails at request time.
codex_pinned_models: dict = _cfg.get("codex_pinned_models") or {}
_round_robin_counter: int = 0

# Speeds tab: the linked Skyrim install (for reading SkyrimNet's own role/variants config) and the
# per-role speed thresholds that drive each speed card's verdict line. Both persisted the same way
# as everything else in config.json -- module globals seeded at import, mutated + re-saved by their
# own /config/* endpoints below.
skyrim_install_path: str = _cfg.get("skyrim_install_path") or ""
if skyrim_install_path:
    logger.info(f"Skyrim install linked for the Speeds tab: {skyrim_install_path}")
skyrimnet_pick: str = _cfg.get("skyrimnet_folder_pick") or ""      # the folder the player picked when MultiProxy could not find SkyrimNet's (its own setting; "Your Skyrim folder" is untouched)

# These four categories are fixed -- every role chip maps to exactly one of them (see
# _ROLE_CATEGORY below). Values are seconds: under "good" is green, over "slow" is red, between is
# yellow. Matches design/dashboard-speeds-tab-mockup.html's own thresholds editor.
DEFAULT_SPEED_THRESHOLDS = {
    "dialogue": {"good": 2.0, "slow": 4.0},
    "gamemaster": {"good": 3.0, "slow": 6.0},
    "vision": {"good": 4.0, "slow": 8.0},
    "background": {"good": 8.0, "slow": 15.0},
}
speed_thresholds: dict = _cfg.get("speed_thresholds") or copy.deepcopy(DEFAULT_SPEED_THRESHOLDS)

# "Starts talking" (Speeds v2): the same four categories, but for how long a streamed reply takes to
# begin -- the wait a player actually feels. Under "good" is quick, over "slow" is slow. Kept apart from
# speed_thresholds (the full-reply bounds) so a config saved before v2 still loads as it always did.
DEFAULT_STARTS_TALKING_THRESHOLDS = {
    "dialogue": {"good": 1.5, "slow": 3.0},
    "gamemaster": {"good": 2.5, "slow": 5.0},
    "vision": {"good": 3.0, "slow": 6.0},
    "background": {"good": 6.0, "slow": 12.0},
}
starts_talking_thresholds: dict = _cfg.get("starts_talking_thresholds") or copy.deepcopy(DEFAULT_STARTS_TALKING_THRESHOLDS)

# GLM (z.ai) -- the general OpenAI-compatible endpoint (api/paas/v4), not the narrower
# api/coding/paas/v4 "Coding Plan" endpoint some other forks use -- that one is scoped to
# Claude-Code-shaped coding-agent traffic on a specific z.ai subscription tier and isn't what a
# plain z.ai API key talks to. Confirmed live 2026-09-13 against docs.z.ai and a real key: this is
# the endpoint docs.z.ai's own quick-start calls out as OpenAI SDK-compatible, and GET .../models
# on it returns the real, current model catalog (glm-4.5 through glm-5.3-flash).
GLM_BASE_URL = "https://api.z.ai/api/paas/v4"
GLM_PREFIX = "glmzai/"
# Per https://docs.z.ai/api-reference/llm/chat-completion (model/messages handled separately).
GLM_SUPPORTED_PARAMS = {
    "temperature", "top_p", "max_tokens", "stream", "do_sample", "stop",
    "tools", "tool_choice", "response_format", "thinking", "reasoning_effort",
    "request_id", "user_id", "tool_stream",
}

NANO_BASE_URL = "https://nano-gpt.com/api/v1"
NANO_PREFIX = "nano/"

# OpenAI's own official API -- a plain, billed API key, genuinely OpenAI-compatible (it IS the
# reference implementation everyone else's "OpenAI-compatible" claim is measured against).
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_PREFIX = "openai/"

# DeepSeek's own official API -- plain Bearer API key, genuinely OpenAI-compatible, confirmed live
# 2026-09-16 against a real key: no param blocklist needed (reasoning_effort and
# thinking={"type": "disabled"} both accepted and honored), plain max_tokens works (unlike OpenAI,
# no max_completion_tokens rename here).
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_PREFIX = "deepseek/"
# Real, live-confirmed model catalog (2026-09-16) -- NOT deepseek-chat/deepseek-reasoner, which is
# this project's own now-stale prior knowledge. Confirmed via GET /models with a real key AND a real
# chat completion against each: deepseek-flash (fast/cheap) and deepseek-v4-pro (flagship,
# reasoning-heavy). Both models emit `reasoning_content` by default alongside `content` (and count
# reasoning tokens against max_tokens/completion_tokens) -- see the "DeepSeek reasoning_content"
# note in TECHNICAL.md for the full live-verified behavior and why nothing is stripped/disabled
# here on this first pass.

# TypeSafe's Jev (queue: let-jev-decisions-through, 2026-09-29) -- SkyrimNet's own experimental
# Decisions job (26-rc1: NPC mood, who speaks next, who the player is addressing) uses Jev as a
# separate, non-chat "typed question" model, not a chat completion. See is_jev_request/
# _handle_jev_request's own header comment for the full routing story; these are just its
# constants, grouped with every other provider's own BASE_URL/PREFIX pair for consistency even
# though Jev never goes through the normal call_X_direct/_ROUTES machinery those other providers do.
TYPESAFE_BASE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_PREFIX = "typesafe/"
# SkyrimNet's own real Decisions-job request (captured live 2026-09-29 from a real model test, via
# SkyrimNet's own openrouter_input.log -- multiproxy.log itself never saw it; see the handoff for why)
# sends "typesafe/jev-1.13", the short form its own dashboard/docs use -- not a real TypeSafe model
# id (confirmed against https://docs.typesafe.ai/models: the real ids are jev-1.13.0, jev-latest,
# jev-preview). Only that one alias needs mapping; jev-latest/jev-preview/jev-1.13.0 already match
# TypeSafe's own API and pass through untouched.
_TYPESAFE_MODEL_MAP = {"jev-1.13": "jev-1.13.0"}
# Jev through Nano-GPT (queue: jev-decisions-can-go-through-nano-gpt, 2026-10-05): Nano-GPT's own System One route takes TypeSafe's body unchanged, with the
# player's Nano-GPT key. Its chat address (NANO_BASE_URL) is on nano-gpt.com; this route is documented on api.nano-gpt.com (docs.nano-gpt.com, Decisions page).
NANO_DECISIONS_URL = "https://api.nano-gpt.com/api/v1/systemone"
NANO_DECISIONS_MODEL = "jev-latest"
# Perplexity's Decisions API (2026-10-25): a cloud classifier that only answers decisions (never writes text), $0.02 (flash $0.01) per million input tokens, output free. The default
# is the standard model, not flash: flash is built for one question per request, and SkyrimNet asks several at once (each would bill the whole state again); at these prices
# the difference is a few thousandths of a cent per decision, so the better model is the default and flash is the player's "cheaper and faster" choice.
PERPLEXITY_DECISIONS_URL = "https://api.perplexity.ai/v1/decisions"
PERPLEXITY_DECISION_MODELS = ("pplx-decider-latest", "pplx-decider-flash-latest")
# How long a decision may wait on a paid answerer (TypeSafe, Nano-GPT) before it is given up on and the next answerer in the order takes it (2026-10-05). SkyrimNet gives
# decisions about 10 seconds, and a stand-in needs time of its own after a miss. The player's setting is `paid_limit_seconds` on the Quick decisions card (2 to 9).
DECISION_PAID_LIMIT = 6.0
DECISION_PAID_LIMIT_RANGE = (2.0, 9.0)
DECISION_SLOW = 598                  # the status the two paid calls give back when the limit ran out (never a real HTTP status)


def _resolve_typesafe_model(raw_model: str) -> str:
    """Strips a leading typesafe/ (SkyrimNet always sends one; a caller that already passed the
    bare TypeSafe id is left alone), then maps the one known short-form alias. Never raises --
    an id this doesn't recognize is passed straight to TypeSafe itself, which is the right place
    for "is this actually a valid Jev model" to be decided, not a guess made here."""
    bare = raw_model[len(TYPESAFE_PREFIX):] if raw_model.startswith(TYPESAFE_PREFIX) else raw_model
    return _TYPESAFE_MODEL_MAP.get(bare, bare)


# Codex (ChatGPT/Codex subscription, via the real Codex CLI as a subprocess) -- see
# call_codex_direct's own header comment for the full "why a subprocess, not direct HTTP"
# reasoning. Optional: unlike CLAUDE_PATH above, a missing Codex CLI does NOT stop the proxy from
# starting -- this is an opt-in extra provider, same as GLM/Nano-GPT/OpenAI needing their own key.
_codex_installer = codex_install.CodexInstaller(os.path.join(SETTINGS_DIR, "codex"), logger)


def _codex_path() -> Optional[str]:
    """Live PATH lookup for the Codex CLI, re-run every time this is called -- deliberately NOT
    cached as a module-level snapshot the way CLAUDE_PATH above is. A snapshot taken once at import
    time goes stale the moment a player installs Codex (or runs `codex login`) while the proxy is
    already running: every status check kept reading the old "not found" result forever, and even
    hitting the dashboard's own Refresh button did nothing, since Refresh only re-checks the MODEL
    list, not whether the CLI itself exists (real bug, confirmed 2026-09-19 -- only a full proxy
    restart fixed it). shutil.which is a cheap PATH directory scan, fine to call on every
    request/status check. (2026-10-02: remembered for 20 s through poll_cache.cached_which, with a thread looking again once it is
    stale and the Refresh buttons forgetting it -- a PATH scan on the event loop was a 16-34 ms stall on every Routing poll.)"""
    return poll_cache.cached_which("codex") or _codex_installer.exe_if_installed()      # (a Codex installed by the card's Install button is found at once, no restart)


if _codex_path():
    logger.info(f"Using Codex CLI: {_codex_path()}")
else:
    logger.info("Codex CLI not found on PATH -- codex/* models will report a clear error if used (optional, npm install -g @openai/codex)")
CODEX_PREFIX = "codex/"
# codex exec runs a real agentic turn under the hood even for a plain dialogue reply -- genuinely
# slower than a normal API call. Matches skyrimnet-codex-proxy's own empirically-chosen budget.
CODEX_TIMEOUT = 180
# A local file read, not a network/subprocess round trip like AGY_MODELS_CACHE_TTL/
# GEMINI_MODEL_LIST_CACHE_TTL -- shorter than either since re-reading it costs almost nothing.
CODEX_MODELS_CACHE_TTL = 10.0
_codex_models_cache: tuple[float, Optional[list]] = (0.0, None)
# Shown when the real model cache can't be read (missing/unreadable/unexpected shape -- see
# _fetch_codex_models's own docstring) -- the one model this route has always been proven to work
# with, kept as a disclosed fallback rather than a silent empty list, same convention
# GEMINI_STATIC_FALLBACK_MODELS already establishes.
CODEX_FALLBACK_MODELS = [
    [f"{CODEX_PREFIX}gpt-5.5", "GPT-5.5 via Codex", "Uses your ChatGPT subscription"],
]

# Codex multi-provider built-in presets (2026-09-16). Fixed base URLs for the two local presets --
# confirmed live against a real Ollama daemon: Codex's own model_providers entries speak the
# Responses API to these, at exactly the same /v1 base a plain OpenAI-compatible client would use.
# Bedrock is deliberately not a preset here -- its AWS SigV4 credential shape doesn't fit this
# dashboard's plain-key/keyless pattern; a custom entry can still point at a Bedrock-compatible
# gateway if someone wants that.
CODEX_OLLAMA_BASE_URL = "http://localhost:11434/v1"
CODEX_LMSTUDIO_BASE_URL = LMSTUDIO_DEFAULT_BASE_URL
CODEX_OPENAI_BASE_URL = "https://api.openai.com/v1"
# Codex's own built-in provider ids (openai/amazon-bedrock/amazon-bedrock-runtime/ollama/lmstudio,
# confirmed live 2026-09-16 by reading codex-rs/model-provider-info/src/lib.rs) plus "default", the
# sentinel this proxy uses for the implicit ChatGPT/Codex-subscription entry that's never stored in
# codex_providers. Every saved provider's own id is generated with an "sn-" prefix (see
# _codex_provider_slug) specifically so it can never collide with any of these -- Codex's own
# merge_configured_model_providers() silently keeps ITS built-in entry over a same-id override, so
# a collision would mean a saved provider silently routing through the wrong backend.
CODEX_RESERVED_PROVIDER_IDS = {"default", "openai", "amazon-bedrock", "amazon-bedrock-runtime", "ollama", "lmstudio"}

# Antigravity CLI (Google AI Pro/Ultra subscription, via the real `agy` CLI as a subprocess) --
# same transport shape as Codex above (a real, officially-maintained agentic CLI shelled out to per
# request), for the same reason: Google's own real backend protocol here isn't ours to reverse-
# engineer and keep in sync with. This is deliberately NOT Gemini CLI (`gemini`, GEMINI_PREFIX's own
# API-key route above is unrelated) -- Google discontinued Gemini CLI's own personal-account login
# in favor of Antigravity CLI, confirmed live 2026-09-15: signing into Gemini CLI with a personal
# Google account fails outright with "This client is no longer supported for Gemini Code Assist
# for individuals... migrate to the Antigravity suite of products." Antigravity CLI's own separate
# "Sign in with Google" flow is the real, current replacement, and it DOES recognize a Google AI Pro
# subscription -- confirmed live with a real Pro account (`agy`'s own banner prints "you@gmail.com
# (Google AI Pro)"). The binary is genuinely named `agy`, not `gemini` or `antigravity` -- constants
# below are named AGY_* to avoid any confusion with the unrelated GEMINI_* API-key constants above;
# GEMINICLI_PREFIX is the router-facing name, kept exactly as specified rather than renamed to match,
# since the prefix names a ROUTE ("your Google subscription channel"), not a model brand -- the same
# reasoning KILO_PREFIX already applies for a gateway that serves many non-Kilo-branded models.
#
# Optional: unlike CLAUDE_PATH, a missing Antigravity CLI does NOT stop the proxy from starting --
# an opt-in extra provider, same as Codex/Kilo/Gemini needing their own install/login.
def _agy_path() -> Optional[str]:
    """Live PATH lookup for the Antigravity CLI, re-run every time this is called -- same fix,
    same reasoning, as _codex_path() (see its own docstring): a one-time snapshot taken at import
    time went stale the moment a player installed the CLI (or ran its login) while the proxy was
    already running, and even the dashboard's own Refresh button didn't help, since Refresh only
    re-checks the model list, not whether the CLI itself exists (real bug, confirmed 2026-09-20,
    identical class to the earlier Codex one). shutil.which is a cheap PATH scan, fine to call on
    every request/status check. (2026-10-02: remembered for 20 s, see _codex_path.)"""
    return poll_cache.cached_which("agy")


if _agy_path():
    logger.info(f"Using Antigravity CLI: {_agy_path()}")
else:
    logger.info("Antigravity CLI not found on PATH -- geminicli/* models will report a clear error if used (optional, Windows: irm https://antigravity.google/cli/install.ps1 | iex)")
GEMINICLI_PREFIX = "geminicli/"
# Real reply latency measured live (2026-09-15, a real Pro-authenticated account): ~1-2s for a
# plain dialogue turn -- but Antigravity is a genuine agentic harness (tool-calling, multi-step
# reasoning) that can in principle run longer than a single completion call, so this keeps
# CODEX_TIMEOUT's own generous-budget reasoning rather than sizing tightly to the happy path alone.
AGY_TIMEOUT = 120
# asyncio's StreamReader defaults to a 64 KiB readline() buffer -- the CLI echoes the whole prompt
# back as a single `step_type: "user_input"` NDJSON line, so a large SkyrimNet prompt (system
# prompt + bio + memories + history) produces a single line well past that default, raising
# `ValueError: Separator is found, but chunk is longer than limit` (confirmed live 2026-09-16, a
# real user hit this on v2.4.0). 10 MiB is comfortably above any prompt size this proxy will
# realistically see echoed back as one line.
AGY_STDOUT_LIMIT = 10 * 1024 * 1024
# A real network call through the CLI (not a local daemon check like Ollama's own tags fetch), so
# cached longer than Ollama's 10s/Gemini's 30s -- "agy models" measurably takes longer than an HTTP
# call, confirmed live.
AGY_MODELS_TIMEOUT = 15.0
# Each look starts the program, and `agy models` makes Antigravity start its own updater child, which flashes a console window
# (watched 2026-10-25: a look every ~65 s while any page was open). So a good list is kept 15 minutes and a failed one 10: an old
# list is handed out at once while one background look makes the next. Refresh, the first start and start-up still look right away.
AGY_MODELS_CACHE_TTL = 900.0
AGY_MODELS_FAILED_TTL = 600.0
_agy_models_cache: tuple[float, Optional[list]] = (0.0, None)

# Grok (SuperGrok subscription, via xAI's own `grok` CLI as a subprocess) -- the same transport shape as Antigravity above: a real,
# officially maintained agentic CLI shelled out to once per request, because xAI's own sign-in and backend aren't ours to re-implement.
# `grok -p` runs one prompt and prints the answer; `--output-format streaming-json` prints one event per line as the answer is written.
# (The CLI's own sign-in is `grok login`; `grok models` lists what the account can use; it was installed and read on 2026-10-03, v1.0.46,
# signed in on 2026-10-03: the real reply is {"type":"text","data":...} lines (answer), {"type":"thought",...} (reasoning, dropped), "usage" and "end"; older shapes are still read.)
# GROK_PREFIX is the route name (`grok/MODEL`); the key lane further down is `xai/MODEL` (api.x.ai).
GROK_PREFIX = "grok/"
GROK_TIMEOUT = 120
GROK_STDOUT_LIMIT = 10 * 1024 * 1024        # one event can carry a lot of text; asyncio's own 64 KiB line limit is far too small
GROK_MODELS_TIMEOUT = 15.0
GROK_MODELS_CACHE_TTL = 900.0          # same reasoning as AGY_MODELS_CACHE_TTL above
GROK_MODELS_FAILED_TTL = 600.0
_grok_models_cache: tuple[float, Optional[dict]] = (0.0, None)


def _grok_default_exe() -> Optional[str]:
    """Where xAI's installer puts the program (~/.grok/bin). It also adds that folder to the player's PATH, but a program that was
    already running (this one) never sees that until it restarts, so the folder is looked at too."""
    exe = os.path.join(os.path.expanduser("~"), ".grok", "bin", "grok.exe" if os.name == "nt" else "grok")
    return exe if os.path.isfile(exe) else None


def _find_grok() -> Optional[str]:
    """The `grok` program: on PATH (remembered for 20 s like the other CLIs), else where its installer puts it."""
    return poll_cache.cached_which("grok") or _grok_default_exe()


def _grok_path() -> Optional[str]:
    return _find_grok()


if _grok_path():
    logger.info(f"Using Grok CLI: {_grok_path()}")
else:
    logger.info("Grok CLI not found -- grok/* models will report a clear error if used (optional, Windows: irm https://x.ai/cli/install.ps1 | iex)")

# xAI API key -- xAI's own OpenAI-style API, a plain billed bearer key (the same shape as Gemini's and DeepSeek's lanes).
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_PREFIX = "xai/"

# Continuum (continuumcode.ai) -- a plan key (cont_sk_...) on its OpenAI-style API; every request counts against the plan's weekly budget.
CONTINUUM_BASE_URL = "https://api.continuumcode.ai/v1"
CONTINUUM_PREFIX = "continuum/"

# Kilo Gateway -- Kilo Code's own hosted OpenAI-compatible API gateway. A plain, billed API key,
# structurally identical to GLM/Nano-GPT (bearer token, OpenAI-shaped passthrough).
KILO_BASE_URL = "https://api.kilo.ai/api/gateway"
KILO_PREFIX = "kilo/"

# Ollama -- local daemon (no auth) by default; Ollama Cloud (a real hosted key) if configured.
# Both are genuinely OpenAI-compatible. 32768 is Ollama Cloud's own lowest-common-denominator
# max_tokens cap across its hosted models -- confirmed a real, already-found bug in
# skyrimnet-codex-proxy (sending an uncapped value can get silently clamped or rejected
# model-by-model); applied here proactively rather than waiting to rediscover it ourselves.
OLLAMA_LOCAL_BASE_URL = "http://localhost:11434/v1"
OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"
OLLAMA_PREFIX = "ollama/"
OLLAMA_CLOUD_MAX_TOKENS_CAP = 32768

# Local models (2026-09-30) -- llama.cpp's own llama-server, started and stopped by this proxy on
# this device, one per downloaded model, each on its own local port. See local_models.py.
LOCAL_PREFIX = local_models.LOCAL_PREFIX
LOCAL_PIDS_FILE = os.path.join(SETTINGS_DIR, "local_models_pids.json")
local_svc = local_models.LocalModels(_load_config, _save_config, logger, LOCAL_PIDS_FILE, gpu_choice_fn=gpu_choice_wiring.local_choice_fn(lambda: _load_config()))
_LOCAL_ATEXIT_SET: list = []  # one atexit hook per process, however many times lifespan() runs

# Local models, part 2 (2026-09-30): other devices. `remote_svc` is the main device's list of model servers
# (Courier devices, older Model Servers, and LM Studio / Ollama / llama.cpp added by address). There is no
# "share this device's models" door any more (retired 2026-10-01: the Courier is what goes on the other devices).
_RESERVED_NICKNAMES = {"claude", "anthropic", "typesafe", "openrouter", "local", "remote", "all", "this", "cloud", "lmstudio"}


def _nickname_problem(nick: str) -> Optional[str]:
    """Why a nickname can't be used as a model prefix, or None. It can't collide with a built-in
    provider's own prefix (glmzai/, nano/, openai/, ollama/, local/...) or the proxy would route
    those requests to the wrong place."""
    if not nick or nick in _RESERVED_NICKNAMES or any(check(f"{nick}/x") for check in _BUILTIN_PREFIX_CHECKS):
        return "That name is used by one of MultiProxy's providers. Pick a different nickname."
    return None


def _forbidden_loopback_ports() -> set:
    """Ports on THIS device that are MultiProxy's own, so an 'AI server' can't be one of them."""
    return {MAIN_PORT, INTERCEPTOR_PORT, *range(local_models.SERVER_PORT_START, local_models.SERVER_PORT_END)}


remote_svc = other_pcs.RemoteServers(_load_config, _save_config, logger, lambda n: _nickname_problem(n), lambda: _forbidden_loopback_ports(),
                                     client_factory=courier_client.CourierClient)
gpu_monitor_svc = gpu_monitor.GpuMonitor(local_svc, _load_config, _save_config, logger)
# Wakes on the Skyrim mod's "started" notice, reads nothing while the game is closed, and only stops the built-in chat models (local_svc), never the decision model, the voice or a Courier.
graphics_guard = gpu_guard.GraphicsGuard(local_svc, gpu_monitor_svc, game_running=lambda: bool(_skyrim_state.running),
                                         enabled=lambda: _load_config().get("graphics_guard") is not False,
                                         events_path=os.path.join(os.path.dirname(os.path.abspath(CONFIG_FILE)), gpu_guard.EVENTS_FILE), log=logger,
                                         cards_fn=lambda: gpu_choice.cached_cards())
other_pcs.set_extra_networks(_load_config().get("private_networks"))        # the VPN ranges the player listed: what this device will connect to

# Gemini (API key) -- Google's own OpenAI-compatible endpoint (docs/research-google-gemini-
# support.md, 2026-09-15). A plain, billed-or-free bearer-token API key, structurally identical to
# Kilo Gateway/Ollama above -- reuses the same _call_openai_compatible_* pair. NO trailing slash,
# unlike how Google's own docs write this URL: _call_openai_compatible_*'s f"{base_url}{path}"
# concatenation (the same convention every base URL here follows) would otherwise produce a double
# slash before /chat/completions.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_PREFIX = "gemini/"

# GitHub Copilot -- no API key of our own to store. A real GitHub OAuth token (discovered from an
# env var or the `gh` CLI you're likely already using -- see get_copilot_github_token) is exchanged
# for a short-lived Copilot session token, which is what actually calls the chat endpoint. See
# call_copilot_direct's own header comment for the full flow and the exact headers Copilot's real
# API requires beyond a bearer token.
COPILOT_TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_DEFAULT_CHAT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_PREFIX = "copilot/"
# Session tokens are ~25min-lived; refresh a bit early rather than racing real expiry mid-request.
COPILOT_TOKEN_REFRESH_MARGIN_SEC = 120


def parse_model_list(model_field: str) -> list[str]:
    """Parse comma-separated model list from request, trimming whitespace."""
    return [m.strip() for m in model_field.split(",") if m.strip()]


def pick_model_round_robin(models: list[str]) -> str:
    """Pick next model from list using round-robin."""
    global _round_robin_counter
    model = models[_round_robin_counter % len(models)]
    _round_robin_counter += 1
    return model


def is_glm_model(model: str) -> bool:
    """GLM (z.ai) models use the 'glmzai/' prefix."""
    return model.lower().startswith(GLM_PREFIX)


def strip_glm_prefix(model: str) -> str:
    """Strip the 'glmzai/' prefix to get the real GLM model name (e.g. 'glm-4.6')."""
    return model[len(GLM_PREFIX):] if model.lower().startswith(GLM_PREFIX) else model


def is_nano_model(model: str) -> bool:
    """Nano-GPT models use the 'nano/' prefix."""
    return model.lower().startswith(NANO_PREFIX)


def strip_nano_prefix(model: str) -> str:
    """Strip the 'nano/' prefix to get the real Nano-GPT model name (which may itself
    contain a '/', e.g. 'nano/qwen/qwen3.8-27b-uncensored' -> 'qwen/qwen3.8-27b-uncensored')."""
    return model[len(NANO_PREFIX):] if model.lower().startswith(NANO_PREFIX) else model


def is_openai_model(model: str) -> bool:
    """OpenAI's own official API uses the 'openai/' prefix."""
    return model.lower().startswith(OPENAI_PREFIX)


def strip_openai_prefix(model: str) -> str:
    """Strip the 'openai/' prefix to get the real model name (e.g. 'gpt-5.5')."""
    return model[len(OPENAI_PREFIX):] if model.lower().startswith(OPENAI_PREFIX) else model


def is_deepseek_model(model: str) -> bool:
    """DeepSeek's own official API uses the 'deepseek/' prefix."""
    return model.lower().startswith(DEEPSEEK_PREFIX)


def strip_deepseek_prefix(model: str) -> str:
    """Strip the 'deepseek/' prefix to get the real model name (e.g. 'deepseek-v4-pro')."""
    return model[len(DEEPSEEK_PREFIX):] if model.lower().startswith(DEEPSEEK_PREFIX) else model


def is_codex_model(model: str) -> bool:
    """Codex (ChatGPT/Codex subscription) models use the 'codex/' prefix."""
    return model.lower().startswith(CODEX_PREFIX)


def strip_codex_prefix(model: str) -> str:
    """Strip the 'codex/' prefix to get the real model name (e.g. 'gpt-5.5')."""
    return model[len(CODEX_PREFIX):] if model.lower().startswith(CODEX_PREFIX) else model


def is_kilo_model(model: str) -> bool:
    """Kilo Gateway models use the 'kilo/' prefix."""
    return model.lower().startswith(KILO_PREFIX)


def strip_kilo_prefix(model: str) -> str:
    """Strip the 'kilo/' prefix to get the real Kilo Gateway model name (which may itself contain
    a '/', e.g. 'kilo/anthropic/claude-sonnet-5')."""
    return model[len(KILO_PREFIX):] if model.lower().startswith(KILO_PREFIX) else model


def is_ollama_model(model: str) -> bool:
    """Ollama (local daemon or Ollama Cloud) models use the 'ollama/' prefix."""
    return model.lower().startswith(OLLAMA_PREFIX)


def strip_ollama_prefix(model: str) -> str:
    """Strip the 'ollama/' prefix to get the real Ollama model name (e.g. 'llama3.1:8b')."""
    return model[len(OLLAMA_PREFIX):] if model.lower().startswith(OLLAMA_PREFIX) else model


def is_lmstudio_model(model: str) -> bool:
    """LM Studio models use the 'lmstudio/' prefix (e.g. 'lmstudio/google/gemma-4-e4b')."""
    return model.lower().startswith(LMSTUDIO_PREFIX)


def strip_lmstudio_prefix(model: str) -> str:
    """Strip the 'lmstudio/' prefix to get LM Studio's own model id (e.g. 'google/gemma-4-e4b')."""
    return model[len(LMSTUDIO_PREFIX):] if model.lower().startswith(LMSTUDIO_PREFIX) else model


def is_local_model(model: str) -> bool:
    """Local models (llama.cpp on this device) use the 'local/' prefix."""
    return model.lower().startswith(LOCAL_PREFIX)


def strip_local_prefix(model: str) -> str:
    """Strip the 'local/' prefix to get the local model's id (e.g. 'gemma-4-12b')."""
    return model[len(LOCAL_PREFIX):] if model.lower().startswith(LOCAL_PREFIX) else model


def is_copilot_model(model: str) -> bool:
    """GitHub Copilot models use the 'copilot/' prefix."""
    return model.lower().startswith(COPILOT_PREFIX)


def strip_copilot_prefix(model: str) -> str:
    """Strip the 'copilot/' prefix to get the real Copilot model name (e.g. 'gpt-4.1')."""
    return model[len(COPILOT_PREFIX):] if model.lower().startswith(COPILOT_PREFIX) else model


def is_gemini_model(model: str) -> bool:
    """Gemini (API key, via Google's OpenAI-compatible endpoint) models use the 'gemini/' prefix."""
    return model.lower().startswith(GEMINI_PREFIX)


def strip_gemini_prefix(model: str) -> str:
    """Strip the 'gemini/' prefix to get the real Gemini model name (e.g. 'gemini-3.8-flash')."""
    return model[len(GEMINI_PREFIX):] if model.lower().startswith(GEMINI_PREFIX) else model


def is_geminicli_model(model: str) -> bool:
    """Antigravity CLI (Google AI Pro/Ultra subscription) models use the 'geminicli/' prefix --
    distinct from GEMINI_PREFIX ('gemini/', the API-key route) since the proxy routes purely on
    prefix and the two auth paths can't share one."""
    return model.lower().startswith(GEMINICLI_PREFIX)


def strip_geminicli_prefix(model: str) -> str:
    """Strip the 'geminicli/' prefix to get the real Antigravity CLI model slug (e.g.
    'gemini-3.8-flash-high')."""
    return model[len(GEMINICLI_PREFIX):] if model.lower().startswith(GEMINICLI_PREFIX) else model


def is_grok_model(model: str) -> bool:
    """Grok (SuperGrok subscription, through xAI's `grok` CLI) models use the 'grok/' prefix."""
    return model.lower().startswith(GROK_PREFIX)


def strip_grok_prefix(model: str) -> str:
    """Strip the 'grok/' prefix to get the real Grok CLI model name (e.g. 'grok-4.6')."""
    return model[len(GROK_PREFIX):] if model.lower().startswith(GROK_PREFIX) else model


def is_xai_model(model: str) -> bool:
    """xAI (API key, api.x.ai) models use the 'xai/' prefix."""
    return model.lower().startswith(XAI_PREFIX)


def strip_xai_prefix(model: str) -> str:
    """Strip the 'xai/' prefix to get the real xAI model name."""
    return model[len(XAI_PREFIX):] if model.lower().startswith(XAI_PREFIX) else model


def is_continuum_model(model: str) -> bool:
    """Continuum (plan key, api.continuumcode.ai) models use the 'continuum/' prefix."""
    return model.lower().startswith(CONTINUUM_PREFIX)


def strip_continuum_prefix(model: str) -> str:
    """Strip the 'continuum/' prefix to get the real Continuum model name."""
    return model[len(CONTINUUM_PREFIX):] if model.lower().startswith(CONTINUUM_PREFIX) else model


def is_key_source_model(model: str) -> bool:
    """Mistral, Groq and Cloudflare Workers AI models use their own prefix (key_sources.py)."""
    return key_sources.match(model) is not None


def strip_key_source_prefix(model: str) -> str:
    return key_sources.strip_prefix(model)


_BUILTIN_PREFIX_CHECKS = (is_glm_model, is_nano_model, is_openai_model, is_deepseek_model, is_codex_model, is_kilo_model, is_ollama_model, is_lmstudio_model, is_local_model, is_copilot_model, is_gemini_model, is_geminicli_model, is_grok_model, is_xai_model, is_continuum_model, is_key_source_model)


def is_remote_model(model: str) -> bool:
    """`<nickname>/<model>` where the nickname is one of the player's other devices / AI servers."""
    return remote_svc.match(model) is not None


_PREFIXED_PROVIDER_CHECKS = _BUILTIN_PREFIX_CHECKS + (is_remote_model,)


OPENROUTER_GONE_MESSAGE = (
    "MultiProxy doesn't connect to OpenRouter anymore. Point SkyrimNet at OpenRouter directly for this "
    "model, or pick one of MultiProxy's providers."
)


def is_unclaimed_slash_model(model: str) -> bool:
    """A 'something/model' name that none of the prefix-routed providers above claims (e.g.
    'google/gemma-4-31b-it'). These used to go to OpenRouter; MultiProxy no longer connects to it,
    so they get a clear error instead of a guess."""
    return "/" in model and not any(check(model) for check in _PREFIXED_PROVIDER_CHECKS)


def map_anthropic_stop_reason(stop_reason: Optional[str]) -> str:
    """Map Anthropic's stop_reason to an OpenAI-style finish_reason.

    Only max_tokens (truncation) has a distinct OpenAI meaning ("length"); everything else
    (end_turn, stop_sequence, an unrecognized future value, or None) maps to "stop" -- this
    proxy strips tool definitions, so tool_use never occurs here.
    """
    return "length" if stop_reason == "max_tokens" else "stop"


# --- MITM Interceptor (startup only) ---

@web.middleware
async def _interceptor_guard(request, handler):
    """The interceptor is only for the one `claude` run MultiProxy starts itself: a web page (or a rebinding name) is refused like on the dashboard (local_guard)."""
    if local_guard.ENABLED:
        sock = request.transport.get_extra_info("sockname") if request.transport else None
        heads = {k.lower().encode(): v for k, v in request.headers.items()}
        bad = local_guard.refusal(request.method, request.path, heads, sock[1] if sock else None)
        if bad:
            logger.warning(f"Refused {request.method} {request.path} on the interceptor: {bad[2]}")
            return web.json_response({"detail": bad[1]}, status=bad[0])
    return await handler(request)


async def interceptor_handler(request):
    body = await request.read()
    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("host", None)

    try:
        parsed = json.loads(body)
    except Exception:
        parsed = {}

    model = parsed.get("model", "")
    real_url = f"https://api.anthropic.com{request.path_qs}"

    # Skip haiku warmup and token counting
    if "haiku" in model or "count_tokens" in request.path:
        async with aiohttp.ClientSession() as session:
            async with session.post(real_url, data=body, headers=headers) as resp:
                resp_body = await resp.read()
                return web.Response(
                    body=resp_body,
                    status=resp.status,
                    headers={"Content-Type": resp.headers.get("Content-Type", "application/json")},
                )

    # Capture auth headers and body template
    # Only capture from real Claude messages (must have "messages" key in body)
    if not auth.is_ready and "messages" in parsed:
        # Build template locally, then assign both fields "atomically"
        captured_headers = dict(headers)

        # Strip tool definitions (60KB dead weight) and extended thinking
        parsed.pop("tools", None)
        parsed.pop("thinking", None)
        parsed.pop("context_management", None)

        captured_template = parsed

        auth.headers = captured_headers
        auth.body_template = captured_template

        template_size = len(json.dumps(captured_template))
        logger.info(f"Captured {len(auth.headers)} headers + template ({template_size:,} bytes, tools stripped)")
        logger.info(f"Auth captured: {_redact_credential(auth.headers.get('Authorization', auth.headers.get('x-api-key')))}")

    # Forward to real API
    async with aiohttp.ClientSession() as session:
        async with session.post(real_url, data=body, headers=headers) as resp:
            resp_body = await resp.read()
            return web.Response(
                body=resp_body,
                status=resp.status,
                headers={"Content-Type": resp.headers.get("Content-Type", "text/event-stream")},
            )


def _redact_credential(value: Optional[str]) -> str:
    """Scheme + last 4 characters only -- enough to confirm auth was captured (and roughly which
    credential shape) without ever writing a usable token to console or disk.

    Real incident (2026-09-13): this line used to log the FULL 'Bearer sk-ant-...' token, and a
    user pasted their own console output into Discord asking for help -- handing over a live
    credential along with the bug report. Never log a token in full, here or anywhere else."""
    if not value:
        return "(none)"
    scheme, sep, token = value.partition(" ")
    if not sep:
        scheme, token = "", value
    tail = token[-4:] if len(token) >= 4 else "***"
    return f"{scheme} ...{tail}".strip()


def _extract_claude_failure_reason(stdout_text: str, stderr_text: str) -> str:
    """First non-empty line of a failed `claude --print`'s own stdout/stderr -- usually the CLI's
    own plain-English explanation (e.g. "Your organization has disabled Claude subscription
    access for Claude Code"), which is far more useful to a user than a generic failure message."""
    for text in (stdout_text, stderr_text):
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line
    return "claude --print failed (see the proxy's own log for details)"


async def capture_auth():
    """Capture Claude auth via a real `claude --print` call, routed through the local interceptor.

    Claude is optional (see CLAUDE_PATH above) -- a failure here is recorded on `auth.last_error`
    and raised, never crashes the app. `ensure_ready` is what rate-limits repeat attempts.
    """
    if not CLAUDE_PATH:
        auth.last_error = "Claude CLI is not installed (or not on PATH)"
        raise RuntimeError(auth.last_error)

    # Use clean temp dir to minimize system-reminder bloat (no CLAUDE.md, no skills)
    with tempfile.TemporaryDirectory() as tmpdir:
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{INTERCEPTOR_PORT}"

        logger.info("Capturing auth from claude --print...")
        proc = await hidden_process.create_subprocess_exec(
            CLAUDE_PATH,
            "--print",
            "--output-format",
            "text",
            "--model",
            DEFAULT_MODEL,
            "--no-session-persistence",
            "--system-prompt",
            "Say ok",
            "ok",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=tmpdir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            with suppress(Exception):
                await proc.wait()
            auth.last_error = "claude --print timed out after 60s"
            raise
        finally:
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW("SkyrimNet MultiProxy")
            except Exception:
                pass

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace")[:800]
            out = (stdout or b"").decode("utf-8", errors="replace")[:800]
            logger.error(f"claude --print failed rc={proc.returncode}\nSTDERR:\n{err}\nSTDOUT:\n{out}")
            auth.last_error = _extract_claude_failure_reason(out, err)
            raise RuntimeError(auth.last_error)

    auth.last_error = None


def _print_generic_port_in_use(port: int) -> None:
    """The original "port's busy" explanation, unchanged -- shown whenever nothing more specific is
    known, or stopping anything isn't appropriate (not another MultiProxy, no console to ask in,
    ReplaceRunningProxy=never, or the player said no)."""
    logger.critical(
        f"MultiProxy can't start: port {port} is already in use.\n"
        f"  It is most likely already running. Look for its icon in the tray, or another\n"
        f"  MultiProxy window.\n"
        f"  Only one copy can run at a time. Close the other one first if you want to start this one."
    )


def _port_listener_pid_linux(port: int) -> Optional[int]:
    """Linux: the PID listening on `port`, from /proc (no netstat/ss needed). The kernel lists every
    listening socket with an inode number in /proc/net/tcp(6); the process holding that inode shows
    it as `socket:[inode]` under /proc/<pid>/fd. Returns None when nothing listens, or when the
    process belongs to someone else and can't be read."""
    if not sys.platform.startswith("linux"):
        return None
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, "r", encoding="ascii", errors="replace") as f:
                next(f, None)
                for line in f:
                    cols = line.split()
                    if len(cols) > 9 and cols[3] == "0A" and int(cols[1].rsplit(":", 1)[1], 16) == port:
                        inodes.add(cols[9])
        except (OSError, ValueError, IndexError):
            continue
    if not inodes:
        return None
    wanted = {f"socket:[{i}]" for i in inodes}
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return None
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(f"{fd_dir}/{fd}") in wanted:
                        return int(pid)
                except OSError:
                    continue
        except OSError:
            continue
    return None


def _process_info_linux(pid: int) -> Optional[dict]:
    """Linux: {"command_line", "started_at"} for `pid` from /proc, like the Windows CIM lookup."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
            stat = f.read()
        # Field 22 (starttime, in clock ticks since boot); the command name in field 2 may hold spaces.
        ticks = int(stat.rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", "r", encoding="ascii") as f:
            boot = next(int(l.split()[1]) for l in f if l.startswith("btime"))
        started_at = datetime.datetime.fromtimestamp(boot + ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    if not cmdline:
        return None
    return {"command_line": cmdline, "started_at": started_at}


def _port_listener_pid(port: int) -> Optional[int]:
    """PID of whatever process is LISTENING on 127.0.0.1:`port` right now, via `netstat -ano` --
    Windows only. Returns None if nothing is listening, this isn't Windows, or netstat itself can't
    be run -- every caller already treats "couldn't identify the holder" as a real, handled
    outcome (the original generic message), never a crash.
    """
    if os.name != "nt":
        return _port_listener_pid_linux(port)
    try:
        result = hidden_process.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    needle = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING" and parts[1].endswith(needle):
            try:
                return int(parts[-1])
            except ValueError:
                continue
    return None


def _parse_cim_datetime(raw: str) -> Optional[datetime.datetime]:
    """A CIM/WMI `CreationDate`, in EITHER of its two real, live-confirmed `ConvertTo-Json` shapes,
    which real-world differ by WHICH powershell.exe answers "powershell" on a given machine:
    Windows PowerShell 5.1 (the one every Windows install ships with, and what plain "powershell"
    resolves to on this dev machine too) serializes a [datetime] as the legacy JSON.NET wrapper
    `"/Date(1790808812774)/"` (milliseconds since the Unix epoch, confirmed live); PowerShell 7+
    ("pwsh", a separate, optional install -- also present on this dev machine, which is what an
    earlier manual check against ONLY pwsh wrongly assumed was the only real shape) serializes the
    same value as a clean ISO-8601 string instead, e.g. `"2026-09-30T15:40:51.389442-07:00"`.
    Real, live, first-try failure caught in this session's own end-to-end test: code written
    against only the pwsh shape silently returned None for every real process on this machine,
    because "powershell" here IS 5.1 -- this must handle both, not assume either.
    """
    m = re.match(r"^/Date\((-?\d+)\)/$", raw)
    if m:
        return datetime.datetime.fromtimestamp(int(m.group(1)) / 1000.0)
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def _process_info(pid: int) -> Optional[dict]:
    """{"command_line": str, "started_at": datetime} for `pid`, via PowerShell's CIM cmdlets.

    Not `wmic` -- confirmed missing entirely on this dev machine's own Windows 11 24H2 install
    (`wmic` was dropped from a default Windows install), so CIM is the only thing to reach for here,
    not a fallback from something more familiar. See _parse_cim_datetime's own docstring for why
    `CreationDate` needs its own real parser rather than a single `datetime.fromisoformat` call.

    None if the process is gone, PowerShell can't be reached, or the output doesn't parse as
    expected -- every caller treats that exactly like "couldn't identify the holder at all".
    """
    if os.name != "nt":
        return _process_info_linux(pid)
    try:
        result = hidden_process.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | '
                "Select-Object CommandLine,CreationDate | ConvertTo-Json -Compress",
            ],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        cmdline = data.get("CommandLine")
        created_raw = data.get("CreationDate")
        if not cmdline or not created_raw:
            return None
        started_at = _parse_cim_datetime(created_raw)
        if started_at is None:
            return None
        return {"command_line": cmdline, "started_at": started_at}
    except Exception:
        return None


def _health_check_says_multiproxy() -> Optional[dict]:
    """Hits THIS proxy's own /health route on MAIN_PORT (always there, regardless of which port is
    actually the one in conflict -- the interceptor on INTERCEPTOR_PORT doesn't serve this) and
    returns the parsed body if it genuinely looks like a real MultiProxy's health response, else
    None. A plain stdlib urllib call, not aiohttp -- this needs to work both before any event loop
    exists (the MAIN_PORT preflight) and from inside one (the interceptor's own conflict path), and
    one short blocking call at startup, only when a conflict is already being investigated, is a
    fine tradeoff against maintaining two implementations.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{MAIN_PORT}/health", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("status") == "healthy" and "claude_cli_found" in data:
        return data
    return None


def _identify_port_holder(port: int) -> tuple[Optional[int], Optional[dict], bool]:
    """(pid, process_info, is_multiproxy) for whoever's listening on `port` right now.

    "is_multiproxy" requires BOTH real signals the build task asked for, not just one: the holding
    process's own command line has to actually run `multiproxy.py` (rules out some unrelated script or
    program that merely happens to hold this port), AND a real /health request against MAIN_PORT has
    to answer with this proxy's own real shape (rules out a coincidental "multiproxy.py" match that isn't
    genuinely this project, and confirms it's actually alive/serving, not just present in a process
    list mid-crash). process_info/is_multiproxy are None/False if the PID itself can't even be
    identified.
    """
    pid = _port_listener_pid(port)
    if pid is None:
        return None, None, False
    info = _process_info(pid)
    if info is None:
        return pid, None, False
    # ("proxy.py" matches both multiproxy.py and the old proxy.py shim an existing player's plugin still launches)
    is_multiproxy = "proxy.py" in info["command_line"].lower() and _health_check_says_multiproxy() is not None
    return pid, info, is_multiproxy


def _friendly_time(dt: datetime.datetime) -> str:
    """'2:41 PM' -- Windows and POSIX spell "no leading zero" with different strftime flags
    (%#I vs %-I); stripping a leading zero ourselves works the same on both."""
    s = dt.strftime("%I:%M %p")
    return s[1:] if s.startswith("0") else s


def _proxy_path_for_message(info: dict) -> str:
    """Best-effort "where it's running from" for the console question. Prefers a live /health
    request's own `proxy_path` field (the exact real path that OTHER instance reports about
    itself) over guessing anything out of its raw command line, which may only show a bare
    relative "multiproxy.py" with no folder at all -- e.g. started via start-proxy.bat, which `cd`s into
    the folder first, so the command line alone never contains it.
    """
    health = _health_check_says_multiproxy()
    if health and health.get("proxy_path"):
        return health["proxy_path"]
    return info.get("command_line") or "another running proxy"


def _ask_yes_no(prompt: str) -> bool:
    """A plain console [Y/n] prompt -- bare Enter (or "y"/"yes", case-insensitively) means yes,
    matching the capital-Y default the prompt text itself shows. Returns False (never stop
    anything) on EOF or any input error, rather than ever assuming yes silently when there was
    really no one there to answer."""
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, OSError):
        return False
    return answer in ("", "y", "yes")


def _stop_process_tree_by_pid(pid: int) -> None:
    """`taskkill /T /F` against an ARBITRARY external PID -- not a process this proxy spawned
    itself, so there's no `asyncio.subprocess.Process` handle to reuse `_kill_process_tree` (below)
    with directly, but the same real, documented Windows recursive tree-kill that helper already
    relies on is exactly as correct here. Synchronous (this whole port-conflict investigation runs
    synchronously either way -- see _health_check_says_multiproxy's own docstring) and never
    raises: a failed kill just means _wait_for_port_free below still finds the port busy and the
    caller falls through to the same explanatory exit as if nothing had been attempted.
    """
    if os.name != "nt":
        # Linux: ask politely first (SIGTERM lets it close its sockets and stop its own model
        # engines), then insist after a couple of seconds.
        try:
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                os.kill(pid, 0)          # raises once the process is gone
                time.sleep(0.1)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        return
    try:
        hidden_process.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, timeout=_KILL_TREE_WAIT_TIMEOUT,
        )
    except Exception:
        pass


def _wait_for_port_free(port: int, timeout: float = 5.0) -> bool:
    """Polls until `port` is free or `timeout` elapses. A just-killed process's socket isn't always
    released by the OS instantly -- retrying a bind immediately after taskkill returns can still
    see EADDRINUSE for a brief window, so this gives that window a moment to close."""
    deadline = time.monotonic() + timeout
    while True:
        if _port_listener_pid(port) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _resolve_port_conflict(port: int) -> None:
    """Called once `port` is confirmed already bound at startup. Investigates who holds it
    (_identify_port_holder) and either stops them and returns normally (the caller should retry
    binding `port`), or exits the whole process outright via os._exit(1) -- every "don't proceed"
    branch below does the latter, so a caller never has to check a return value to know which
    happened; returning at all means it's safe to retry. See this feature's own build task ("When
    the port is busy, offer to stop the old proxy and start fresh") for the full spec this follows.
    """
    pid, info, is_multiproxy = _identify_port_holder(port)

    if pid is None or info is None:
        _print_generic_port_in_use(port)
        os._exit(1)
        return  # pragma: no cover -- os._exit never actually returns; only reached if mocked

    if not is_multiproxy:
        _print_generic_port_in_use(port)
        logger.critical(f"  Whatever's using it right now: process ID {pid}, {info['command_line']}")
        os._exit(1)
        return  # pragma: no cover

    if not sys.stdin.isatty() and not _TRAY_MODE:
        # (A tray-started copy has no console either, but it is the one that is meant to take over a MultiProxy left running without its
        # tray: it falls through to the policy below, with "ask" meaning "yes" because there is nobody to ask.)
        # No console to ask in (the SKSE plugin's own launch, or any other non-interactive start) --
        # keep the original behavior. The plugin already avoids double-launching a proxy on
        # purpose, so this should be rare in practice, and there's genuinely no one here to answer
        # a question even if it weren't.
        _print_generic_port_in_use(port)
        os._exit(1)
        return  # pragma: no cover

    if REPLACE_RUNNING_PROXY_POLICY == "never":
        _print_generic_port_in_use(port)
        os._exit(1)
        return  # pragma: no cover

    if REPLACE_RUNNING_PROXY_POLICY == "ask" and not _TRAY_MODE:
        started = _friendly_time(info["started_at"])
        where = _proxy_path_for_message(info)
        proceed = _ask_yes_no(
            f"Another MultiProxy is already running (started {started}, from {where}). "
            f"Stop it and start this one? [Y/n] "
        )
        if not proceed:
            _print_generic_port_in_use(port)
            os._exit(1)
            return  # pragma: no cover

    # flush=True: real, live-confirmed finding from this session's own end-to-end test -- Python's
    # stdout is fully (not line-)buffered once it isn't a real interactive console, so a plain
    # print() here can sit invisible for the whole lifetime of a long-running process (confirmed:
    # it never appeared at all, even after a forced taskkill, since that skips normal interpreter
    # shutdown/flush). The player needs to see this immediately, not eventually or never.
    print(f"Stopping the other MultiProxy (process ID {pid})...", flush=True)
    _stop_process_tree_by_pid(pid)
    if not _wait_for_port_free(port):
        logger.critical(
            f"Tried to stop the other MultiProxy, but port {port} is still in use -- exiting."
        )
        os._exit(1)
        return  # pragma: no cover


def _exit_if_port_in_use(e: OSError, port: int) -> bool:
    """Given a bind failure, decide what to do. Re-raises anything that isn't actually
    "address already in use" unchanged -- this is only meant to intercept that one specific,
    common, user-confusing case, not to hide some other startup failure.

    Returns True if the port was freed by stopping another MultiProxy and the caller should retry
    binding; never returns otherwise (_resolve_port_conflict always exits directly on every path
    where the caller should not proceed).
    """
    if e.errno != errno.EADDRINUSE:
        raise e
    _resolve_port_conflict(port)
    return True


def _preflight_main_port() -> None:
    """Checked before uvicorn ever tries to bind MAIN_PORT, at the very bottom of this file --
    uvicorn's own bind failure doesn't give this file a clean, version-stable hook to intercept (it
    can raise, log-and-exit, or behave differently across uvicorn releases), so this checks the
    port itself, synchronously, first, the same real primitive INTERCEPTOR_PORT's own bind-failure
    path already uses."""
    if _port_listener_pid(MAIN_PORT) is not None:
        _resolve_port_conflict(MAIN_PORT)


async def start_interceptor():
    """Start MITM interceptor and capture auth from a clean temp dir."""
    iapp = web.Application(middlewares=[_interceptor_guard])
    iapp.router.add_route("*", "/{path_info:.*}", interceptor_handler)

    runner = web.AppRunner(iapp)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", INTERCEPTOR_PORT)
    try:
        await site.start()
    except OSError as e:
        if _exit_if_port_in_use(e, INTERCEPTOR_PORT):
            # _resolve_port_conflict (inside _exit_if_port_in_use) just stopped the other
            # MultiProxy and confirmed the port free -- retry once, the one case this can ever
            # need to.
            site = web.TCPSite(runner, "127.0.0.1", INTERCEPTOR_PORT)
            await site.start()
    logger.info(f"Interceptor on port {INTERCEPTOR_PORT}")

    return runner


# --- Direct API call ---

def _build_api_body(system_prompt: Optional[str], messages: list, model: str, max_tokens: int) -> dict:
    """Build Anthropic API request body from template."""
    body = copy.deepcopy(auth.body_template)

    # 1. Replace system prompt (keep billing block 0)
    billing = body["system"][0]
    body["system"] = [billing]
    if system_prompt:
        system_block = {"type": "text", "text": str(system_prompt)}
        if isinstance(system_prompt, _cc.MarkedText):
            system_block["cache_control"] = dict(system_prompt.marker)
        body["system"].append(system_block)

    # 2. Build full conversation, preserving template auth blocks in first user msg
    auth_blocks = []
    template_first = body["messages"][0] if body["messages"] else {}
    if isinstance(template_first.get("content"), list):
        for block in template_first["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                if "<system-reminder>" in block.get("text", ""):
                    auth_blocks.append(block)

    # (2026-10-21) Every text is a plain string: a caching-format message sends its content as a list of parts, and a list in "text" is what Anthropic refused with
    # "Input should be a valid string". Empty blocks are left out, cache markers stay, and at most four go (the last four).
    new_messages = _cc.anthropic_messages(messages)
    if new_messages and new_messages[0]["role"] == "user":
        new_messages[0]["content"] = auth_blocks + new_messages[0]["content"]
    body["messages"] = new_messages
    found, kept = _cc.cap_cache_markers(body)
    if found > kept:
        logger.info(f"[cache] kept {kept} of {found} cache markers (Anthropic takes at most {_cc.MAX_CACHE_MARKERS})")

    # 3. Model, max_tokens, streaming, and disable extended thinking
    body["model"] = model
    body["max_tokens"] = max_tokens
    body["stream"] = True
    body.pop("thinking", None)
    return body


async def call_api_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int) -> tuple[str, dict]:
    """Direct API call, collects full response (non-streaming to caller). Returns (text, usage)."""
    body = _build_api_body(system_prompt, messages, model, max_tokens)
    headers = _with_readable_encodings(auth.headers)
    body_bytes = json.dumps(body).encode("utf-8")
    headers["Content-Length"] = str(len(body_bytes))

    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> {model} ({len(messages)} msgs)")
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        async with _post_with_retry(
            session,
            "https://api.anthropic.com/v1/messages?beta=true",
            provider="Claude", request_id=request_id,
            data=body_bytes,
            headers=headers,
        ) as resp:
            elapsed = time.time() - start

            if resp.status != 200:
                error_body = await resp.read()
                error_text = error_body.decode("utf-8", errors="replace")
                logger.error(f"[{request_id}] API {resp.status}: {error_text[:300]}")
                if resp.status in (401, 403) or "credential" in error_text.lower():
                    logger.warning("Auth expired/invalid (direct)")
                    auth.invalidate()
                raise HTTPException(status_code=resp.status, detail=error_text[:200])

            resp_body = await resp.read()
            text_parts = []
            usage = None
            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                in_tok = 0
                out_tok = 0
                stop_reason = None
                for line in resp_body.decode("utf-8", errors="replace").split("\n"):
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            continue
                        try:
                            event = json.loads(data_str)
                            if event.get("type") == "message_start":
                                in_tok = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                            elif event.get("type") == "message_delta":
                                out_tok = event.get("usage", {}).get("output_tokens", 0)
                                stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
                            elif event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text_parts.append(delta.get("text", ""))
                        except json.JSONDecodeError:
                            pass
                if in_tok or out_tok:
                    usage = {"input_tokens": in_tok, "output_tokens": out_tok, "stop_reason": stop_reason}
            else:
                try:
                    data = json.loads(resp_body)
                    for block in data.get("content", []):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    usage = data.get("usage", {})
                    if usage:
                        usage["stop_reason"] = data.get("stop_reason")
                except json.JSONDecodeError:
                    usage = {}

            response_text = "".join(text_parts)
            tok_str = f" | in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)} tok" if usage else ""
            logger.info(f"[{request_id}] <- {len(response_text)} chars ({elapsed:.1f}s){tok_str}")
            return response_text, usage
    finally:
        if owns_session:
            await session.close()


async def call_api_streaming_with_retry(system_prompt: Optional[str], messages: list, model: str, max_tokens: int):
    """
    Direct API call, yields OpenAI-format SSE chunks as they arrive.
    Retries ONCE on 401/403 by forcing a single-flight auth refresh before yielding any chunks.

    Real bug, real production incident (2026-09-16): `auth.ensure_ready()` used to be called with
    nothing catching it -- if Claude's login had quietly expired mid-session, that call raised
    `RuntimeError` straight out of this generator with zero SSE events sent, not even `[DONE]`.
    FastAPI had already committed to a StreamingResponse by then, so the player got a truncated
    body and a dead NPC -- the exact `Exception in ASGI application` + curl error 18 pairing the
    Antigravity CLI streaming bug produced, on Claude, the flagship provider, this time. Two layers
    fix it: the two `auth.ensure_ready()` call sites are each wrapped individually so a login
    failure specifically gets `_claude_unavailable_message()`'s own actionable wording (the same
    "pick a different provider" text `chat_completions()`'s own pre-check already gives when auth
    is broken before a stream even starts) -- a login that needs renewing is a thing the player can
    act on, unlike a bare internal error. A broader `except Exception` around the whole function is
    the same defense-in-depth shape the Antigravity CLI and shared-OpenAI-compatible-helper fixes
    already settled on, for anything else that could still escape (a network failure mid-request, a
    parsing surprise) that isn't specifically an auth problem.
    """
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())

    # Every failure below RAISES (see "Streaming: real HTTP errors before the first byte"): before
    # any event has been sent it becomes a real HTTP error response, after that a clean stream end.
    # Nothing here ever writes error wording into the reply text.
    try:
        for attempt in range(2):
            try:
                await auth.ensure_ready(timeout=60)
            except Exception as e:
                logger.warning(f"Claude auth not ready during streaming ({e})")
                raise HTTPException(status_code=503, detail=_claude_unavailable_message(str(e)))

            body = _build_api_body(system_prompt, messages, model, max_tokens)
            headers = _with_readable_encodings(auth.headers)
            body_bytes = json.dumps(body).encode("utf-8")
            headers["Content-Length"] = str(len(body_bytes))

            request_id = uuid.uuid4().hex[:8]
            logger.info(f"[{request_id}] -> {model} ({len(messages)} msgs, stream, attempt={attempt+1})")
            start = time.time()
            total_chars = 0
            input_tokens = 0
            output_tokens = 0
            stop_reason = None

            session = auth.session or aiohttp.ClientSession()
            owns_session = auth.session is None
            try:
                async with _post_with_retry(
                    session,
                    "https://api.anthropic.com/v1/messages?beta=true",
                    provider="Claude", request_id=request_id,
                    data=body_bytes,
                    headers=headers,
                ) as resp:
                    # If auth invalid, refresh and retry BEFORE yielding anything.
                    if resp.status in (401, 403) and attempt == 0:
                        error_text = (await resp.read()).decode("utf-8", errors="replace")[:300]
                        logger.warning(f"[{request_id}] Auth invalid during stream start: {error_text}")
                        auth.invalidate()
                        try:
                            await auth.ensure_ready(force=True, timeout=60)
                        except Exception as e:
                            logger.warning(f"Claude auth refresh failed after invalidation ({e})")
                            raise HTTPException(status_code=503, detail=_claude_unavailable_message(str(e)))
                        continue

                    if resp.status != 200:
                        error_body = await resp.read()
                        error_text = error_body.decode("utf-8", errors="replace")
                        logger.error(f"[{request_id}] API {resp.status}: {error_text[:300]}")
                        if resp.status in (401, 403) or "credential" in error_text.lower():
                            logger.warning("Auth expired/invalid (stream)")
                            auth.invalidate()

                        raise HTTPException(status_code=resp.status, detail=_extract_upstream_error_message(error_text)[:300])

                    # Stream Claude SSE -> OpenAI SSE
                    role_chunk = {
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(role_chunk)}\n\n"

                    buffer = ""
                    async for raw_chunk in resp.content.iter_any():
                        buffer += raw_chunk.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            if event.get("type") == "message_start":
                                input_tokens = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                            elif event.get("type") == "message_delta":
                                output_tokens = event.get("usage", {}).get("output_tokens", 0)
                                stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
                            elif event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    if text:
                                        total_chars += len(text)
                                        oai_chunk = {
                                            "id": cmpl_id,
                                            "object": "chat.completion.chunk",
                                            "created": created,
                                            "model": model,
                                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                                        }
                                        yield f"data: {json.dumps(oai_chunk)}\n\n"

                    stop_chunk = {
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": map_anthropic_stop_reason(stop_reason)}],
                    }
                    yield f"data: {json.dumps(stop_chunk)}\n\n"
                    yield "data: [DONE]\n\n"

                    elapsed = time.time() - start
                    logger.info(f"[{request_id}] <- {total_chars} chars ({elapsed:.1f}s, streamed) | in={input_tokens} out={output_tokens} tok")
                    return
            finally:
                if owns_session:
                    await session.close()

        # If we somehow got here, retry didn't help
        raise HTTPException(status_code=503, detail=_claude_unavailable_message("its login couldn't be refreshed"))
    except HTTPException:
        raise
    except aiohttp.ClientError as e:
        logger.error(f"Claude connection failed: {e}")
        raise HTTPException(status_code=502, detail=f"Couldn't reach Claude: {e}")
    except Exception:
        logger.exception("Unexpected error in Claude streaming")
        raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")


# --- GLM (z.ai) API calls ---

def _wants_own_thinking_config(extra_params: dict) -> bool:
    """True if the caller already asked for thinking/reasoning explicitly -- if so, don't
    override it with our own default-disabled below. Shared by GLM (_build_glm_payload) and
    DeepSeek (call_deepseek_direct/call_deepseek_streaming) -- not GLM-specific despite living
    in this section; DeepSeek needs the exact same check for the exact same reason."""
    return extra_params.get("thinking") is not None or extra_params.get("reasoning_effort") is not None


def _build_glm_payload(oai_messages: list, model: str, max_tokens: int, extra_params: dict, *, force_reasoning_effort: bool) -> dict:
    """Build the GLM request body: OpenAI-shaped messages, GLM's own supported-param
    whitelist, plus a fast-by-default thinking mode.

    NPC dialogue wants a quick, plain reply, not a wall of hidden reasoning -- confirmed live
    2026-09-13, a trivial "Say OK" reply burned 430 reasoning tokens with thinking left on its
    default. So this defaults to `thinking: disabled` UNLESS the caller already supplied their
    own `thinking`/`reasoning_effort` (respected as-is), or `force_reasoning_effort` is set --
    that's for the retry path below, since glm-5.3+ can't disable thinking at all and rejects the
    `thinking` field outright, needing `reasoning_effort` instead.
    """
    extra_params = thinking_off.normalised(extra_params)      # SkyrimNet's six reasoning levels, in any of its spellings, said the way GLM takes them
    glm_params = _clean_extra_params("GLM", extra_params, whitelist=GLM_SUPPORTED_PARAMS)

    payload = {"model": model, "messages": oai_messages, "max_tokens": max_tokens}
    payload.update(glm_params)
    if not _wants_own_thinking_config(extra_params):
        if force_reasoning_effort:
            payload["reasoning_effort"] = "low"
        else:
            payload["thinking"] = {"type": "disabled"}
    return payload


def _glm_requires_thinking_error(status: int, error_text: str) -> bool:
    """True for GLM's specific 'this model always thinks' rejection (glm-5.3 and up, confirmed
    live 2026-09-13) -- the one case worth a same-request retry, since it's not a real failure,
    just the wrong knob for this particular model."""
    return status == 400 and "always engages in thinking" in error_text.lower()


def _glm_oai_messages(system_prompt: Optional[str], messages: list) -> list:
    oai_messages = []
    if system_prompt:
        oai_messages.append({"role": "system", "content": system_prompt})
    for m in messages:
        oai_messages.append({"role": m["role"], "content": m["content"]})
    return oai_messages


async def call_glm_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    """Forward request to GLM/z.ai (OpenAI-compatible), collect full response."""
    if not glm_api_key:
        raise HTTPException(status_code=500, detail="No GLM key found. Add one on the Providers tab.")

    headers = {"Authorization": f"Bearer {glm_api_key}", "Content-Type": "application/json"}
    request_id = uuid.uuid4().hex[:8]
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        # Two independent, each-bounded retries share this loop: one for the thinking-mode
        # rejection, and up to MAX_CONTEXT_TRIMS for a chat that's too long for the model.
        force_effort = False
        trimmer = _ContextTrimmer("GLM", request_id)
        current_messages = messages
        while True:
            oai_messages = _glm_oai_messages(system_prompt, current_messages)
            payload = _build_glm_payload(oai_messages, model, max_tokens, extra_params, force_reasoning_effort=force_effort)
            logger.info(f"[{request_id}] -> GLM {model} ({len(current_messages)} msgs)")
            async with _post_with_retry(session, f"{GLM_BASE_URL}/chat/completions", provider="GLM", request_id=request_id, json=payload, headers=headers) as resp:
                elapsed = time.time() - start
                if resp.status != 200:
                    error_text = await resp.text()
                    if not force_effort and _glm_requires_thinking_error(resp.status, error_text):
                        logger.info(f"[{request_id}] GLM {model} can't disable thinking -- retrying with reasoning_effort=low")
                        force_effort = True
                        continue
                    shorter = trimmer.retry_messages(resp.status, error_text, current_messages)
                    if shorter is not None:
                        current_messages = shorter
                        continue
                    logger.error(f"[{request_id}] GLM {resp.status}: {error_text[:300]}")
                    raise HTTPException(status_code=resp.status, detail=_provider_error_message("GLM", error_text))
                data = await resp.json()
                choice = data["choices"][0]
                text = choice["message"]["content"]
                usage = dict(data.get("usage") or {})
                usage["finish_reason"] = choice.get("finish_reason")
                tok_str = f" | in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)} tok" if usage.get("prompt_tokens") is not None else ""
                logger.info(f"[{request_id}] <- {len(text)} chars ({elapsed:.1f}s, GLM){tok_str}")
                return text, usage
    finally:
        if owns_session:
            await session.close()


async def call_glm_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    """Forward request to GLM/z.ai with streaming, passthrough SSE directly (already
    OpenAI-chunk-shaped). Retries once, before yielding anything, if this model can't disable
    thinking -- same reasoning as call_glm_direct."""
    if not glm_api_key:
        raise HTTPException(status_code=500, detail="No GLM key found. Add one on the Providers tab.")

    headers = {"Authorization": f"Bearer {glm_api_key}", "Content-Type": "application/json"}
    request_id = uuid.uuid4().hex[:8]
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        try:
            force_effort = False
            trimmer = _ContextTrimmer("GLM", request_id)
            current_messages = messages
            while True:
                oai_messages = _glm_oai_messages(system_prompt, current_messages)
                payload = _build_glm_payload(oai_messages, model, max_tokens, extra_params, force_reasoning_effort=force_effort)
                payload["stream"] = True
                logger.info(f"[{request_id}] -> GLM {model} ({len(current_messages)} msgs, stream)")
                async with _post_with_retry(session, f"{GLM_BASE_URL}/chat/completions", provider="GLM", request_id=request_id, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        if not force_effort and _glm_requires_thinking_error(resp.status, error_text):
                            logger.info(f"[{request_id}] GLM {model} can't disable thinking -- retrying with reasoning_effort=low")
                            force_effort = True
                            continue
                        shorter = trimmer.retry_messages(resp.status, error_text, current_messages)
                        if shorter is not None:
                            current_messages = shorter
                            continue
                        logger.error(f"[{request_id}] GLM {resp.status}: {error_text[:300]}")
                        message = _provider_error_message("GLM", error_text)
                        raise HTTPException(status_code=resp.status, detail=message)

                    buffer = ""
                    async for raw_chunk in resp.content.iter_any():
                        buffer += raw_chunk.decode("utf-8", errors="replace")
                        while "\n\n" in buffer:
                            event, buffer = buffer.split("\n\n", 1)
                            event = event.strip()
                            if event:
                                yield event + "\n\n"

                    if buffer.strip():
                        yield buffer.strip() + "\n\n"

                    elapsed = time.time() - start
                    logger.info(f"[{request_id}] <- stream done ({elapsed:.1f}s, GLM)")
                    return
        except HTTPException:
            raise
        except aiohttp.ClientError as e:
            # This handler had NO exception handling at all before 2026-09-16 -- not even a
            # connection-failure branch. Any failure here (including mid-retry) used to escape
            # after SSE headers were already sent, truncating the response instead of ending it
            # cleanly.
            logger.error(f"[{request_id}] GLM connection failed: {e}")
            raise HTTPException(status_code=502, detail=f"Couldn't reach GLM: {e}")
        except Exception:
            logger.exception(f"[{request_id}] Unexpected error in GLM streaming")
            raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")
    finally:
        if owns_session:
            await session.close()


# --- Nano-GPT API calls ---

async def call_nano_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    """Forward request to Nano-GPT (OpenAI-compatible), collect full response."""
    nano_retried = extra_params.pop("_nano_retried", False)
    if not nano_api_key:
        raise HTTPException(status_code=500, detail="No Nano-GPT key found. Add one on the Providers tab.")

    oai_messages = []
    if system_prompt:
        oai_messages.append({"role": "system", "content": system_prompt})
    for m in messages:
        oai_messages.append({"role": m["role"], "content": m["content"]})

    payload = {"model": model, "messages": oai_messages, "max_tokens": max_tokens}
    payload.update(_clean_extra_params("Nano-GPT", extra_params))
    headers = {"Authorization": f"Bearer {nano_api_key}", "Content-Type": "application/json"}

    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> Nano-GPT {model} ({len(messages)} msgs)")
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        async with _post_with_retry(
            session,
            f"{NANO_BASE_URL}/chat/completions",
            provider="Nano-GPT", request_id=request_id,
            json=payload,
            headers=headers,
        ) as resp:
            elapsed = time.time() - start
            if resp.status != 200:
                error_body = await resp.text()
                unknown = None if nano_retried else _unknown_fields_from_error(
                    resp.status, error_body, {k: v for k, v in payload.items() if k not in ("model", "messages", "max_tokens")})
                if unknown:
                    _learn_unknown_fields("Nano-GPT", unknown, request_id)
                    return await call_nano_direct(system_prompt, messages, model, max_tokens, _nano_retried=True, **extra_params)
                logger.error(f"[{request_id}] Nano-GPT {resp.status}: {error_body[:300]}")
                raise HTTPException(status_code=resp.status, detail=_provider_error_message("Nano-GPT", error_body))
            data = await resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            usage = dict(data.get("usage") or {})
            usage["finish_reason"] = choice.get("finish_reason")
            tok_str = f" | in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)} tok" if usage.get("prompt_tokens") is not None else ""
            logger.info(f"[{request_id}] <- {len(text)} chars ({elapsed:.1f}s, Nano-GPT){tok_str}")
            return text, usage
    finally:
        if owns_session:
            await session.close()


async def call_nano_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    nano_retried = extra_params.pop("_nano_retried", False)
    """Forward request to Nano-GPT with streaming, passthrough SSE directly (already
    OpenAI-chunk-shaped -- confirmed live 2026-09-13)."""
    if not nano_api_key:
        raise HTTPException(status_code=500, detail="No Nano-GPT key found. Add one on the Providers tab.")

    oai_messages = []
    if system_prompt:
        oai_messages.append({"role": "system", "content": system_prompt})
    for m in messages:
        oai_messages.append({"role": m["role"], "content": m["content"]})

    payload = {"model": model, "messages": oai_messages, "max_tokens": max_tokens, "stream": True}
    payload.update(_clean_extra_params("Nano-GPT", extra_params))
    headers = {"Authorization": f"Bearer {nano_api_key}", "Content-Type": "application/json"}

    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> Nano-GPT {model} ({len(messages)} msgs, stream)")
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        try:
            async with _post_with_retry(
                session,
                f"{NANO_BASE_URL}/chat/completions",
                provider="Nano-GPT", request_id=request_id,
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    error_body = await resp.text()
                    unknown = None if nano_retried else _unknown_fields_from_error(
                        resp.status, error_body, {k: v for k, v in payload.items() if k not in ("model", "messages", "max_tokens", "stream")})
                    if unknown:
                        _learn_unknown_fields("Nano-GPT", unknown, request_id)
                        async for chunk in call_nano_streaming(system_prompt, messages, model, max_tokens, _nano_retried=True, **extra_params):
                            yield chunk
                        return
                    logger.error(f"[{request_id}] Nano-GPT {resp.status}: {error_body[:300]}")
                    message = _provider_error_message("Nano-GPT", error_body)
                    raise HTTPException(status_code=resp.status, detail=message)

                buffer = ""
                async for raw_chunk in resp.content.iter_any():
                    buffer += raw_chunk.decode("utf-8", errors="replace")
                    while "\n\n" in buffer:
                        event, buffer = buffer.split("\n\n", 1)
                        event = event.strip()
                        if event:
                            yield event + "\n\n"

                if buffer.strip():
                    yield buffer.strip() + "\n\n"

                elapsed = time.time() - start
                logger.info(f"[{request_id}] <- stream done ({elapsed:.1f}s, Nano-GPT)")
        except HTTPException:
            raise
        except aiohttp.ClientError as e:
            # This handler had NO exception handling at all before 2026-09-16 -- not even a
            # connection-failure branch. Any failure here used to escape after SSE headers were
            # already sent, truncating the response instead of ending it cleanly.
            logger.error(f"[{request_id}] Nano-GPT connection failed: {e}")
            raise HTTPException(status_code=502, detail=f"Couldn't reach Nano-GPT: {e}")
        except Exception:
            logger.exception(f"[{request_id}] Unexpected error in Nano-GPT streaming")
            raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")
    finally:
        if owns_session:
            await session.close()


# --- Shared "plain bearer-token, OpenAI-Chat-Completions-shaped" implementation ---
#
# GLM/Nano-GPT above predate this and stay as their own standalone functions rather
# than being retrofitted onto it -- lower risk to leave already-shipped, already-tested code alone.
# This exists because OpenAI/Kilo Gateway/Ollama (three more providers of the exact same real
# shape -- bearer token, POST messages, get back an OpenAI chat.completion) were all being added in
# the same pass; three more copy-pasted call_X_direct/call_X_streaming pairs would just be the same
# ~60 lines duplicated three more times for no real difference beyond base URL, the token-count
# param's name, and which extra params each service accepts.

def _extract_upstream_error_message(raw_text: str, fallback: str = "Unknown error") -> str:
    """Pull a human-readable message out of an upstream error response.

    Real bug, reported live 2026-09-13: Ollama's own error body
    (`{"error":{"message":"model 'llama3.1:8b' not found",...}}`) was passed straight through as
    FastAPI's `detail` string, which the default `{"detail": "..."}` wrapper then JSON-encoded a
    SECOND time -- a double-escaped blob no client should ever have to parse. Every OpenAI-shaped
    provider here (GLM, Nano-GPT, OpenAI, Kilo, Ollama, Gemini) uses the same
    `{"error": {"message": "..."}}` shape, so pulling `message` out here once and letting
    `_openai_style_error_handler` do the final wrapping keeps every call site a one-liner. Falls
    back to the raw text (trimmed) when the body isn't JSON or doesn't have that shape, so a
    real, human-readable upstream message is never dropped just because it came back as plain text.

    Gemini is the one real exception to that shared dict shape -- confirmed live 2026-09-15: its
    OpenAI-compat layer's own error body is a JSON ARRAY containing one object
    (`[{"error": {...}}]`), not a bare object. Unwrapped to that first element before the same
    dict-shape checks below run, so Gemini's real errors get the same clean treatment as every
    other provider instead of falling through to the raw-JSON-text fallback.
    """
    text = (raw_text or "").strip()
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if isinstance(parsed, dict):
        # The most human field the service sent, in this order (2026-10-03): error.message, detail, message, error (when it is a string), else the whole text.
        # Continuum answers {"error":"upstream_error","status":426,"detail":"... temporarily unavailable. Please try again."}: the code is not
        # something a person can act on, the detail is, and it used to be dropped.
        err = parsed.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(parsed.get("detail"), str) and parsed["detail"].strip():
            return parsed["detail"].strip()
        if parsed.get("message"):
            return str(parsed["message"])
        if isinstance(err, str) and err:
            return err
    return text


def _provider_error_message(label: str, raw_text: str) -> str:
    """`Continuum: Continuum inference for grok-4.7 is temporarily unavailable. Please try again.` -- the provider's own words, with its name in front
    so the player sees who said it. Every key lane's error goes through here."""
    return f"{label}: {_extract_upstream_error_message(raw_text)}"


# --- Extra request fields (2026-10-01): a request must never fail over a setting an AI doesn't understand ---
# SkyrimNet's custom provider has a "Provider Settings" JSON box whose contents it adds to EVERY request. A player who pasted
# OpenRouter's settings there ({"cache": true, "provider": {...}, "route": "fallback"}) got a 400 from Gemini ("Unknown name
# "cache": Cannot find field"), because anything MultiProxy didn't recognise was forwarded. Now:
#   * `multiproxy` is MultiProxy's own settings object: read by nobody yet, NEVER forwarded to any provider, never an error.
#   * the fields only OpenRouter understands are never forwarded to anything that isn't OpenRouter-shaped (Kilo Gateway is).
#   * strict APIs (Gemini, OpenAI, GLM) only receive fields from their own allow-list; tolerant ones (Ollama, LM Studio, local
#     engines, other devices, DeepSeek, Nano-GPT) keep every other setting, because players use theirs (min_p, num_ctx...).
#   * safety net: a 400 that names fields it doesn't know is retried ONCE without them, and they are remembered for the run.
MULTIPROXY_FIELD = "multiproxy"
OPENROUTER_ONLY_FIELDS = frozenset({"provider", "route", "cache", "transforms", "models", "plugins", "include_reasoning", "usage"})
OPENROUTER_SHAPED_PROVIDERS = frozenset({"Kilo Gateway"})
# What every OpenAI-shaped chat API is known to accept (fields beyond these are provider-specific).
STANDARD_CHAT_FIELDS = frozenset({
    "temperature", "top_p", "n", "stream", "stream_options", "stop", "max_tokens", "max_completion_tokens", "presence_penalty",
    "frequency_penalty", "logit_bias", "logprobs", "top_logprobs", "seed", "response_format", "tools", "tool_choice",
    "parallel_tool_calls", "user", "reasoning_effort", "metadata", "store", "modalities", "service_tier", "prediction",
    "verbosity", "functions", "function_call",
})
_learned_drops: dict[str, set] = {}          # provider -> fields its API said it doesn't know (this run)
_logged_drops: set = set()                   # (provider, fields) already logged: said once, not on every request
_UNKNOWN_FIELD_RE = re.compile(
    r"unknown name|unrecogni[sz]ed (request )?(argument|parameter|field)|unknown (parameter|field|argument)|cannot find field|"
    r"extra inputs are not permitted|unexpected (field|keyword|parameter)|additional propert|invalid json payload|"
    r"not (a )?(valid|allowed|permitted|supported) (parameter|field|argument)", re.I)


def _log_dropped_once(provider: str, dropped: set) -> None:
    key = (provider, frozenset(dropped))
    if dropped and key not in _logged_drops:
        _logged_drops.add(key)
        logger.warning(f"{provider}: dropping unsupported param(s): {dropped}")


def _clean_extra_params(provider: str, extra: dict, whitelist: Optional[set] = None, blocklist: Optional[set] = None) -> dict:
    """The request's extra fields as `provider` should get them. `multiproxy` never goes anywhere (and isn't worth a log line)."""
    out, dropped = {}, set()
    learned = _learned_drops.get(provider, ())
    for k, v in extra.items():
        if v is None or k == MULTIPROXY_FIELD:
            continue
        if whitelist is not None:
            bad = k not in whitelist
        else:
            bad = bool(blocklist and k in blocklist) or (k in OPENROUTER_ONLY_FIELDS and provider not in OPENROUTER_SHAPED_PROVIDERS)
        if bad or k in learned:
            dropped.add(k)
        else:
            out[k] = v
    _log_dropped_once(provider, dropped)
    return out


def _unknown_fields_from_error(status: int, body: str, sent: dict) -> Optional[set]:
    """Which of the extra fields we sent a 400 says it doesn't know, or None when it isn't that kind of complaint. If the message
    doesn't name them, every non-standard field we sent."""
    if status != 400 or not sent or not _UNKNOWN_FIELD_RE.search(body or ""):
        return None
    named = {k for k in sent if re.search(rf"""["'`]{re.escape(k)}["'`]|supplied: {re.escape(k)}\b|parameter: {re.escape(k)}\b""", body)}
    # (an API often names only the first one it trips on, so every other non-standard field goes too: one retry, not one per field)
    out = named | {k for k in sent if k not in STANDARD_CHAT_FIELDS}
    if out & thinking_off.SWITCH_FIELDS:
        out |= thinking_off.SWITCH_FIELDS & set(sent)       # the two thinking switches are one wish: a server that refuses one is not sent the other either
    return out or None


def _learn_unknown_fields(provider: str, names: set, request_id: str) -> None:
    _learned_drops.setdefault(provider, set()).update(names)
    key = (provider, "learned", frozenset(names))
    if key not in _logged_drops:
        _logged_drops.add(key)
        logger.warning(f"[{request_id}] {provider} didn't accept {sorted(names)}: sent the request again without them, and won't send them again this run")


OPENAI_ALLOWED_PARAMS = STANDARD_CHAT_FIELDS - {"functions", "function_call"} | {"max_completion_tokens"}
GEMINI_ALLOWED_PARAMS = frozenset({"temperature", "top_p", "n", "stream", "stream_options", "stop", "max_tokens", "max_completion_tokens",
                                   "response_format", "tools", "tool_choice", "reasoning_effort", "extra_body", "user"})


def _oai_compat_payload(
    messages: list, model: str, max_tokens: int, max_tokens_param: str, max_tokens_cap: Optional[int],
    param_whitelist: Optional[set], param_blocklist: Optional[set], provider_name: str, extra_params: dict,
) -> dict:
    effective_max_tokens = min(max_tokens, max_tokens_cap) if max_tokens_cap else max_tokens
    payload = {"model": model, "messages": messages, max_tokens_param: effective_max_tokens}
    payload.update(_clean_extra_params(provider_name, extra_params, param_whitelist, param_blocklist))
    return payload


def _oai_compat_headers(api_key: Optional[str], extra_headers: Optional[dict]) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _oai_compat_messages(system_prompt: Optional[str], messages: list) -> list:
    oai_messages = []
    if system_prompt:
        oai_messages.append({"role": "system", "content": system_prompt})
    for m in messages:
        oai_messages.append({"role": m["role"], "content": m["content"]})
    return oai_messages


def _assemble_sse_reply(body: str) -> dict:
    """Put a server-sent-events reply (`data: {...}` lines) together into the one-answer shape a non-streamed reply has:
    the text, the finish reason and the usage. Some services stream even when the request said not to."""
    text_parts: list = []
    finish = None
    usage: dict = {}
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        for ch in ev.get("choices") or []:
            if not isinstance(ch, dict):
                continue
            delta = ch.get("delta") or ch.get("message") or {}
            piece = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(piece, str):
                text_parts.append(piece)
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    return {"choices": [{"message": {"content": "".join(text_parts)}, "finish_reason": finish}], "usage": usage}


async def _read_oai_compat_reply(resp, provider_name: str, request_id: str) -> dict:
    """The parsed answer of a 200 reply. A stream is put together; any other kind of body that isn't JSON is a plain error naming
    its type (never "could not reach": the service did answer)."""
    raw_type = resp.headers.get("Content-Type")
    ctype = raw_type.split(";")[0].strip().lower() if isinstance(raw_type, str) else ""
    if ctype == "text/event-stream":
        logger.info(f"[{request_id}] {provider_name} streamed a reply that wasn't asked to stream -- putting it together")
        return _assemble_sse_reply(await resp.text())
    if ctype and ctype != "application/json" and not ctype.endswith("+json"):
        logger.error(f"[{request_id}] {provider_name} answered 200 with an unexpected type: {ctype}")
        raise HTTPException(status_code=502, detail=f"{provider_name} answered with a reply type MultiProxy doesn't understand ({ctype}).")
    return await resp.json(content_type=None)


async def _call_openai_compatible_direct(
    provider_name: str, base_url: str, path: str, api_key: Optional[str], require_key: bool,
    system_prompt: Optional[str], messages: list, model: str, max_tokens: int,
    max_tokens_param: str = "max_tokens", max_tokens_cap: Optional[int] = None,
    param_whitelist: Optional[set] = None, param_blocklist: Optional[set] = None,
    extra_headers: Optional[dict] = None, error_hint_fn: Optional[Callable[[int, str], str]] = None,
    connect_timeout: Optional[float] = None, engine_errors: bool = False, on_thinks: Optional[Callable[[], None]] = None, **extra_params,
) -> tuple[str, dict]:
    """`engine_errors`: this is a local engine (Ollama, LM Studio) whose own failure is read for its plain meaning (engine_trouble.explain) and raised as an EngineFailure."""
    if require_key and not api_key:
        raise HTTPException(status_code=500, detail=f"No {provider_name} key found. Add one on the Providers tab.")

    headers = _oai_compat_headers(api_key, extra_headers)

    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> {provider_name} {model} ({len(messages)} msgs)")
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    trimmer = _ContextTrimmer(provider_name, request_id)
    current_messages = messages
    retried_unknown = False
    post_kw = _connect_timeout_kwargs(session, connect_timeout)
    try:
        try:
            while True:
                payload = _oai_compat_payload(
                    _oai_compat_messages(system_prompt, current_messages), model, max_tokens, max_tokens_param,
                    max_tokens_cap, param_whitelist, param_blocklist, provider_name, extra_params,
                )
                async with _post_with_retry(session, f"{base_url}{path}", provider=provider_name, request_id=request_id, json=payload, headers=headers, **post_kw) as resp:
                    elapsed = time.time() - start
                    if resp.status != 200:
                        error_body = await resp.text()
                        shorter = trimmer.retry_messages(resp.status, error_body, current_messages)
                        if shorter is not None:
                            current_messages = shorter
                            continue
                        unknown = None if retried_unknown else _unknown_fields_from_error(
                            resp.status, error_body, {k: v for k, v in payload.items() if k not in ("model", "messages", max_tokens_param, "stream")})
                        if unknown:
                            retried_unknown = True
                            _learn_unknown_fields(provider_name, unknown, request_id)
                            continue
                        logger.error(f"[{request_id}] {provider_name} {resp.status}: {error_body[:300]}")
                        trouble = engine_trouble.explain(provider_name, resp.status, error_body) if engine_errors else None
                        if trouble is not None:
                            raise EngineFailure(resp.status, trouble)
                        message = _provider_error_message(provider_name, error_body)
                        if error_hint_fn:
                            message = error_hint_fn(resp.status, message)
                        raise HTTPException(status_code=resp.status, detail=message)
                    data = await _read_oai_compat_reply(resp, provider_name, request_id)
                    choice = data["choices"][0]
                    # Real, live-found crash (2026-09-15, Gemini): a low max_tokens can be entirely
                    # consumed by the model's own internal "thinking" before it produces any visible
                    # reply -- confirmed live, `gemini-3.8-flash` with max_tokens=100 came back
                    # `finish_reason: "length"` with NO "content" key in "message" at all (not even an
                    # empty string), which crashed this line with an unhandled KeyError/500 instead of
                    # a clean, catchable response. `.get("content") or ""` reports that honestly (an
                    # empty reply with finish_reason "length" still tells the caller what happened)
                    # instead of crashing on a shape every OpenAI-compatible provider CAN return.
                    text = choice["message"].get("content") or ""
                    usage = dict(data.get("usage") or {})
                    usage["finish_reason"] = choice.get("finish_reason")
                    thought = bool(choice["message"].get("reasoning_content") or choice["message"].get("reasoning"))
                    if thought and on_thinks:
                        on_thinks()                              # it thought although thinking was turned off: its card says so
                    if not text.strip() and thought:
                        usage["reasoned"] = True              # it wrote its thoughts and no answer: said plainly if the reply turns out empty
                    if isinstance(data.get("timings"), dict):
                        usage["timings"] = data["timings"]            # a local llama.cpp's own numbers (routing: kept in the request record)
                    tok_str = f" | in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)} tok" if usage.get("prompt_tokens") is not None else ""
                    logger.info(f"[{request_id}] <- {len(text)} chars ({elapsed:.1f}s, {provider_name}){tok_str}")
                    return text, usage
        except HTTPException:
            raise
        except aiohttp.ClientError as e:
            # Most likely case in practice: local Ollama simply isn't running. Without this, a
            # plain connection refusal surfaces as an unhandled 500 instead of a clear message.
            logger.error(f"[{request_id}] {provider_name} connection failed: {e}")
            raise _unreachable(provider_name, e, engine_errors)
    finally:
        if owns_session:
            await session.close()


def _unreachable(provider_name: str, e: BaseException, engine_errors: bool) -> HTTPException:
    """The error for a service that could not be reached. A local engine's carries the plain clause for its cards ("isn't running"); the message is the same as ever."""
    message = f"Couldn't reach {provider_name}: {e}"
    if not engine_errors:
        return HTTPException(status_code=502, detail=message)
    failure = EngineFailure(502, engine_trouble.not_running(provider_name) if isinstance(e, aiohttp.ClientConnectorError) else engine_trouble.dropped(provider_name))
    failure.detail = message
    return failure


async def _call_openai_compatible_streaming(
    provider_name: str, base_url: str, path: str, api_key: Optional[str], require_key: bool,
    system_prompt: Optional[str], messages: list, model: str, max_tokens: int,
    max_tokens_param: str = "max_tokens", max_tokens_cap: Optional[int] = None,
    param_whitelist: Optional[set] = None, param_blocklist: Optional[set] = None,
    extra_headers: Optional[dict] = None, synthesize_done: bool = False,
    error_hint_fn: Optional[Callable[[int, str], str]] = None, connect_timeout: Optional[float] = None, engine_errors: bool = False,
    on_thinks: Optional[Callable[[], None]] = None, **extra_params,
):
    """`synthesize_done` covers a real, already-found Ollama Cloud bug: it can drop the stream
    mid-generation without ever sending `data: [DONE]`, which would otherwise hang SkyrimNet
    waiting for a terminator that's never coming. `engine_errors`: see _call_openai_compatible_direct."""
    if require_key and not api_key:
        raise HTTPException(status_code=500, detail=f"No {provider_name} key found. Add one on the Providers tab.")

    headers = _oai_compat_headers(api_key, extra_headers)

    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> {provider_name} {model} ({len(messages)} msgs, stream)")
    start = time.time()

    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    saw_done = False
    trimmer = _ContextTrimmer(provider_name, request_id)
    current_messages = messages
    retried_unknown = False
    post_kw = _connect_timeout_kwargs(session, connect_timeout)
    try:
        try:
            while True:
                payload = _oai_compat_payload(
                    _oai_compat_messages(system_prompt, current_messages), model, max_tokens, max_tokens_param,
                    max_tokens_cap, param_whitelist, param_blocklist, provider_name, extra_params,
                )
                payload["stream"] = True
                async with _post_with_retry(session, f"{base_url}{path}", provider=provider_name, request_id=request_id, json=payload, headers=headers, **post_kw) as resp:
                    if resp.status != 200:
                        error_body = await resp.text()
                        # Nothing has been yielded yet, so a too-long chat can still be re-sent shorter.
                        shorter = trimmer.retry_messages(resp.status, error_body, current_messages)
                        if shorter is not None:
                            current_messages = shorter
                            continue
                        unknown = None if retried_unknown else _unknown_fields_from_error(
                            resp.status, error_body, {k: v for k, v in payload.items() if k not in ("model", "messages", max_tokens_param, "stream")})
                        if unknown:
                            retried_unknown = True
                            _learn_unknown_fields(provider_name, unknown, request_id)
                            continue
                        logger.error(f"[{request_id}] {provider_name} {resp.status}: {error_body[:300]}")
                        trouble = engine_trouble.explain(provider_name, resp.status, error_body) if engine_errors else None
                        if trouble is not None:
                            raise EngineFailure(resp.status, trouble)
                        message = _provider_error_message(provider_name, error_body)
                        if error_hint_fn:
                            message = error_hint_fn(resp.status, message)
                        raise HTTPException(status_code=resp.status, detail=message)

                    buffer = ""
                    async for raw_chunk in resp.content.iter_any():
                        buffer += raw_chunk.decode("utf-8", errors="replace")
                        while "\n\n" in buffer:
                            event, buffer = buffer.split("\n\n", 1)
                            event = event.strip()
                            if event:
                                if on_thinks and engine_trouble.event_facts(event)[1]:
                                    on_thinks()
                                    on_thinks = None
                                if event == "data: [DONE]":
                                    saw_done = True
                                elif _live.watching_thinking():          # the Live view's Thinking: only read out of a reply while somebody is watching it
                                    _live.thinking(request_id, getattr(request_facts.current(), "job", None) or "", live_feed.reasoning_from_event(event))
                                yield event + "\n\n"

                    if buffer.strip():
                        event = buffer.strip()
                        if event == "data: [DONE]":
                            saw_done = True
                        yield event + "\n\n"

                    if synthesize_done and not saw_done:
                        logger.warning(f"[{request_id}] {provider_name} stream ended without [DONE] -- synthesizing one")
                        yield "data: [DONE]\n\n"

                    elapsed = time.time() - start
                    logger.info(f"[{request_id}] <- stream done ({elapsed:.1f}s, {provider_name})")
                    break
        except HTTPException:
            raise
        except aiohttp.ClientError as e:
            # Same "local Ollama simply isn't running" safety net as the non-streaming path --
            # surfaced as an SSE error chunk here since headers/status are already committed once
            # StreamingResponse starts.
            logger.error(f"[{request_id}] {provider_name} connection failed: {e}")
            raise _unreachable(provider_name, e, engine_errors)
        except Exception:
            # Defense in depth, same shape as the Antigravity CLI streaming fix (2026-09-16): only
            # aiohttp.ClientError was ever caught here, so asyncio.TimeoutError, a UnicodeDecodeError
            # on a malformed chunk, or any other surprise mid-stream escaped AFTER FastAPI had
            # already sent SSE headers -- a truncated body (curl "Transferred a partial file"), not
            # a clean error. This helper is shared by Gemini/OpenAI/Kilo Gateway/Ollama Cloud, so
            # one fix covers all four call sites at once.
            logger.exception(f"[{request_id}] Unexpected error in {provider_name} streaming")
            raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")
    finally:
        if owns_session:
            await session.close()


# --- OpenAI (plain, billed API key) ---
# max_completion_tokens, not max_tokens -- OpenAI's Chat Completions API renamed this param and
# recommends the new name universally; o-series reasoning models reject the old name outright.
# NOTE: not independently live-verified end-to-end (the provided test key had no billing credits
# left -- confirmed via GET /v1/models, which doesn't need credits, but a real chat completion call
# returned 429 insufficient_quota before any param validation could be observed). Flagged rather
# than silently assumed correct -- worth a real smoke test once a funded key is available.

async def call_openai_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    return await _call_openai_compatible_direct(
        "OpenAI", OPENAI_BASE_URL, "/chat/completions", openai_api_key, True,
        system_prompt, messages, model, max_tokens, max_tokens_param="max_completion_tokens",
        param_whitelist=OPENAI_ALLOWED_PARAMS, **extra_params,
    )


async def call_openai_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    stream = _call_openai_compatible_streaming(
        "OpenAI", OPENAI_BASE_URL, "/chat/completions", openai_api_key, True,
        system_prompt, messages, model, max_tokens, max_tokens_param="max_completion_tokens",
        param_whitelist=OPENAI_ALLOWED_PARAMS, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        # Close the shared helper right away if SkyrimNet hangs up (its `finally` closes the
        # upstream response) rather than leaving that to garbage collection.
        await stream.aclose()


# --- DeepSeek (plain, billed API key) ---
# Live-verified 2026-09-16 with a real key: no param blocklist (reasoning_effort and
# thinking={"type": "disabled"} both accepted), plain max_tokens (unlike OpenAI). See
# DEEPSEEK_BASE_URL's own comment above and TECHNICAL.md for the full reasoning_content finding.

def _deepseek_extra_params(extra_params: dict) -> dict:
    """Defaults thinking OFF, same reasoning and same disable syntax as GLM's own
    _build_glm_payload -- confirmed live (TECHNICAL.md): reasoning tokens count against
    max_tokens/completion_tokens on both DeepSeek models, so a small max_tokens (several of
    SkyrimNet's own variants use ones as low as 100) can be entirely consumed by hidden thinking,
    leaving content: "" and a clean-but-unhelpful 500 "Empty response" -- confirmed live 2026-09-22
    against the director's own multiproxy.log. Unlike GLM 5.3+, DeepSeek never rejects `thinking:
    {"type": "disabled"}` (TECHNICAL.md, live-verified), so this needs no retry/reasoning_effort
    fallback -- a plain default is the whole fix. Respects an explicit caller-supplied
    thinking/reasoning_effort as-is, same contract _build_glm_payload already has. SkyrimNet's
    "off" and its levels (thinking_off.normalised) are turned into exactly those first, so a
    `reasoning_effort: "none"` is never sent to DeepSeek as a level it may not know."""
    extra_params = {k: v for k, v in thinking_off.normalised(extra_params).items() if k != "reasoning"}      # (`reasoning` is OpenRouter's field, never DeepSeek's: sent on, one refusal would also make this run drop `thinking`)
    if _wants_own_thinking_config(extra_params):
        return extra_params
    return {**extra_params, "thinking": {"type": "disabled"}}


async def call_deepseek_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    return await _call_openai_compatible_direct(
        "DeepSeek", DEEPSEEK_BASE_URL, "/chat/completions", deepseek_api_key, True,
        system_prompt, messages, model, max_tokens, **_deepseek_extra_params(extra_params),
    )


async def call_deepseek_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    stream = _call_openai_compatible_streaming(
        "DeepSeek", DEEPSEEK_BASE_URL, "/chat/completions", deepseek_api_key, True,
        system_prompt, messages, model, max_tokens, **_deepseek_extra_params(extra_params),
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        # Close the shared helper right away if SkyrimNet hangs up (its `finally` closes the
        # upstream response) rather than leaving that to garbage collection.
        await stream.aclose()


# --- Kilo Gateway ---
# Strips thinking/reasoning/top_k -- a real, already-found incompatibility (skyrimnet-codex-proxy's
# own test_kilo_provider.py), not a guess. Deliberately does NOT strip reasoning_effort -- that same
# reference test explicitly asserts it survives (Kilo Gateway can proxy to reasoning-capable models
# that accept it, unlike Ollama below, which strips all four).
KILO_BLOCKED_PARAMS = {"thinking", "reasoning", "top_k"}


async def call_kilo_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    return await _call_openai_compatible_direct(
        "Kilo Gateway", KILO_BASE_URL, "/chat/completions", kilo_api_key, True,
        system_prompt, messages, model, max_tokens, param_blocklist=KILO_BLOCKED_PARAMS,
        **extra_params,
    )


async def call_kilo_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    stream = _call_openai_compatible_streaming(
        "Kilo Gateway", KILO_BASE_URL, "/chat/completions", kilo_api_key, True,
        system_prompt, messages, model, max_tokens, param_blocklist=KILO_BLOCKED_PARAMS,
        **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        # Close the shared helper right away if SkyrimNet hangs up (its `finally` closes the
        # upstream response) rather than leaving that to garbage collection.
        await stream.aclose()


# --- Ollama (local daemon, no auth -- or Ollama Cloud, a real hosted key) ---
# Unlike Kilo Gateway above, Ollama's real API doesn't support reasoning_effort either -- confirmed
# via skyrimnet-codex-proxy's own test_ollama_reasoning_strip.py, which asserts all four of these
# are stripped for Ollama specifically (not the same set as Kilo -- checked, not assumed identical).
OLLAMA_BLOCKED_PARAMS = {"thinking", "reasoning", "reasoning_effort", "top_k"}


def _thinking_for(engine: Optional[str], key: str, extra_params: dict, blocked: set) -> tuple[dict, set, Optional[Callable[[], None]]]:
    """SkyrimNet's "reasoning off" said the way this engine understands it (thinking_off.py), instead of being dropped: (the extra fields, the blocklist to use, and what
    to call when the model thinks anyway, so its card can say so). Untouched when the engine has no switch or the request did not ask."""
    extra, done = thinking_off.translate(engine, extra_params)
    if done:
        return extra, blocked - {"reasoning_effort"}, (lambda: thinking_off.THINKERS.note(key))
    extra, sent = thinking_off.translate_level(engine, extra_params)          # low / medium / high reach the engine as it reads them (Beta 27's six levels)
    if sent:
        return extra, blocked - {"reasoning_effort"}, None
    return extra_params, blocked, None


def _ollama_target(model: str) -> tuple[str, Optional[str], bool]:
    """Local Ollama (no key) unless an Ollama Cloud key is configured, in which case Cloud is
    used instead -- same "local by default, cloud if you've opted in" reasoning as this project's
    own Claude-CLI-first default. Returns (base_url, api_key, require_key)."""
    if ollama_api_key:
        return OLLAMA_CLOUD_BASE_URL, ollama_api_key, True
    return OLLAMA_LOCAL_BASE_URL, None, False


async def call_ollama_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    base_url, api_key, require_key = _ollama_target(model)
    cap = OLLAMA_CLOUD_MAX_TOKENS_CAP if api_key else None
    extra_params, blocked, on_thinks = _thinking_for(None if api_key else "ollama", OLLAMA_PREFIX + model, extra_params, OLLAMA_BLOCKED_PARAMS)
    return await _call_openai_compatible_direct(
        "Ollama", base_url, "/chat/completions", api_key, require_key,
        system_prompt, messages, model, max_tokens, max_tokens_cap=cap,
        param_blocklist=blocked, engine_errors=not api_key, on_thinks=on_thinks, **extra_params,
    )


async def call_ollama_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    base_url, api_key, require_key = _ollama_target(model)
    cap = OLLAMA_CLOUD_MAX_TOKENS_CAP if api_key else None
    extra_params, blocked, on_thinks = _thinking_for(None if api_key else "ollama", OLLAMA_PREFIX + model, extra_params, OLLAMA_BLOCKED_PARAMS)
    stream = _call_openai_compatible_streaming(
        "Ollama", base_url, "/chat/completions", api_key, require_key,
        system_prompt, messages, model, max_tokens, max_tokens_cap=cap,
        param_blocklist=blocked, synthesize_done=bool(api_key), engine_errors=not api_key, on_thinks=on_thinks, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        # Close the shared helper right away if SkyrimNet hangs up (its `finally` closes the
        # upstream response) rather than leaving that to garbage collection.
        await stream.aclose()


# --- Local models (2026-09-30): llama.cpp's llama-server on this device ---
# llama-server speaks the same OpenAI chat shape Ollama does, so this reuses the shared helpers.
# `local_svc.ensure_ready` waits for a model that's still loading and raises a plain reason (stopped,
# crashed, out of graphics memory...) which becomes a real HTTP error so SkyrimNet falls back.
# It has no `top_k` blocked (llama-server supports it); the reasoning knobs aren't part of its API.
LOCAL_BLOCKED_PARAMS = {"thinking", "reasoning", "reasoning_effort"}


async def _local_base_url(model: str) -> str:
    try:
        return await local_svc.ensure_ready(model)
    except local_models.LocalModelError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


async def call_local_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    base_url = await _local_base_url(model)
    return await _call_openai_compatible_direct(
        "Local model", base_url, "/chat/completions", None, False,
        system_prompt, messages, model, max_tokens, param_blocklist=LOCAL_BLOCKED_PARAMS, **extra_params,
    )


async def call_local_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    base_url = await _local_base_url(model)
    stream = _call_openai_compatible_streaming(
        "Local model", base_url, "/chat/completions", None, False,
        system_prompt, messages, model, max_tokens, param_blocklist=LOCAL_BLOCKED_PARAMS, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()


# --- Other devices (2026-09-30): a paired MultiProxy, or an AI server somebody already runs ---
# Both speak the OpenAI chat shape, so this reuses the shared helpers like Ollama does. A paired
# MultiProxy is reached through its model-server door (its own port, with the pairing code header);
# an AI server by its own /v1. Either way an unreachable device is a plain 502 (so SkyrimNet switches to
# a backup) and its pill on the Other devices tab turns Offline.
REMOTE_BLOCKED_PARAMS = {"thinking", "reasoning"}


def _remote_for(model: str):
    m = remote_svc.match(model)
    if not m:
        raise HTTPException(status_code=404, detail=f"'{model}' isn't one of your other devices' models.")
    return m


class RemoteUnavailable(HTTPException):
    """Another device didn't answer (refused, timed out, dropped, already marked offline). Still the same plain 502 as ever; with a backup
    set (below) a request that has not sent a word back yet is answered by the backup instead."""

    def __init__(self, remote, detail: str):
        super().__init__(status_code=502, detail=detail)
        self.remote = remote


def _remote_unreachable(r) -> "RemoteUnavailable":
    what = ("Is that device on and running the Courier?" if r.kind == "courier" else
            "Is that device on and running MultiProxy?" if r.kind == "multiproxy" else "Is that AI server running?")
    return RemoteUnavailable(r, f"{r.label} isn't answering. {what}")


def _remote_failed(r, e: HTTPException, *, streaming: bool = False) -> HTTPException:
    """A connection failure marks that device Offline and becomes a plain message; anything else (the
    other side answered with a real error) passes through untouched. Mid-stream, the shared helper
    turns every surprise into a 500 "Unexpected internal error": for another device that means it stopped
    answering, so that counts as the device dropping too."""
    if e.status_code == 503 and ("isn't answering right now" in str(e.detail) or "isn't serving models right now" in str(e.detail)):
        # The device is up but that program is stopped: the same plain 502 as a device that doesn't answer, so a backup (if one is set)
        # takes the request. The device isn't marked offline: its row says which program is stopped.
        return RemoteUnavailable(r, f"{r.label} isn't serving models right now.")
    gone = (e.status_code == 502 and ("Could not reach" in str(e.detail) or "Couldn't reach" in str(e.detail))) or \
           (streaming and e.status_code == 500 and ("Something went wrong in MultiProxy" in str(e.detail) or "Something went wrong inside MultiProxy" in str(e.detail) or "Unexpected internal error" in str(e.detail)))   # (the new wording, and an older device's))
    if gone:
        err = _remote_unreachable(r)
        remote_svc.mark(r, False, err.detail)
        r.retry_at = time.time() + other_pcs.RETRY_AFTER_SECONDS          # a failed try restarts the 30 s wait
        return err
    return e


def _remote_kwargs(r) -> dict:
    return {"api_key": r.key or None, "extra_headers": None} if r.kind == "openai" else {"api_key": None, "extra_headers": r.headers()}


# --- A backup for another device that doesn't answer (2026-10-01) ---
# `other_pcs_backup` in config.json: {"enabled": bool, "model": "<any ready model on this device or in the cloud>"}. A request for
# `<nickname>/<model>` is answered by the backup ONLY while nothing has gone back to SkyrimNet yet: the device refused, didn't answer in
# time, is already marked offline, or dropped before its first word. A device that drops after words have streamed back is NOT swapped
# for the backup (a stream can't be rewound, and two models in one line reads as garbage): that stream ends with a real error so
# SkyrimNet's own retry acts, the device is marked offline, and the NEXT request goes to the backup. A real error from the model
# (a bad request...) is never a reason to switch.
BACKUP_CONNECT_SECONDS = 5.0       # a device that doesn't even pick up
BACKUP_FIRST_WORD_SECONDS = 30.0   # a streamed reply that never starts
BACKUP_REPLY_SECONDS = 90.0        # a whole (non-streamed) reply


def _backup_model() -> Optional[str]:
    """The backup model when the switch is on and one is picked (never another device's model), else None."""
    c = _load_config().get("other_pcs_backup") or {}
    m = str(c.get("model") or "").strip()
    return m if c.get("enabled") and m and not is_remote_model(m) else None


def _remote_may_be_tried(r) -> bool:
    """Offline devices get no requests until their 30 s wait is over (the poll or a live connection may bring them back sooner)."""
    return r.online is not False or time.time() >= r.retry_at


async def _remote_first_word(gen, r, seconds: float):
    """`gen`, but a stream that sends nothing for `seconds` is the device not answering."""
    it = gen.__aiter__()
    try:
        first = await asyncio.wait_for(it.__anext__(), seconds)
    except asyncio.TimeoutError:
        err = _remote_unreachable(r)
        remote_svc.mark(r, False, f"{r.label} didn't answer in time.")
        r.retry_at = time.time() + other_pcs.RETRY_AFTER_SECONDS
        await _aclose_quietly(gen)
        raise err
    except StopAsyncIteration:
        return
    yield first
    async for chunk in it:
        yield chunk


async def _remote_reply_in_time(coro, r, seconds: float):
    try:
        return await asyncio.wait_for(coro, seconds)
    except asyncio.TimeoutError:
        err = _remote_unreachable(r)
        remote_svc.mark(r, False, f"{r.label} didn't answer in time.")
        r.retry_at = time.time() + other_pcs.RETRY_AFTER_SECONDS
        raise err


def _remote_thinking(r, rid: str, extra_params: dict) -> tuple[dict, set, Optional[Callable[[], None]]]:
    """Thinking turned off for a model on another device, said the way the program running it understands (a Courier tells which program has each model; an AI server
    added by address is Ollama, LM Studio or llama.cpp by its own label; a llama.cpp MultiProxy did not start gets both switches, 2026-10-25). The Courier passes the request through as it is, so nothing changes there."""
    program = next((m.get("program") for m in (r.models or []) if m.get("id") == rid and m.get("program")), None) or r.program
    return _thinking_for(thinking_off.engine_of_program(program), f"{r.id}/{rid}", extra_params, REMOTE_BLOCKED_PARAMS)


async def call_remote_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    r, rid = _remote_for(model)
    kw = _remote_kwargs(r)
    covered = _backup_model() is not None
    if covered and not _remote_may_be_tried(r):
        raise _remote_unreachable(r)
    extra_params, blocked, on_thinks = _remote_thinking(r, rid, extra_params)
    try:
        call = _call_openai_compatible_direct(
            r.id, r.api_base, "/chat/completions", kw["api_key"], False,
            system_prompt, messages, rid, max_tokens, extra_headers=kw["extra_headers"],
            error_hint_fn=other_pcs.image_hint, param_blocklist=blocked, on_thinks=on_thinks,
            connect_timeout=BACKUP_CONNECT_SECONDS if covered else None, **extra_params,
        )
        out = await (_remote_reply_in_time(call, r, BACKUP_REPLY_SECONDS) if covered else call)
    except HTTPException as e:
        raise _remote_failed(r, e)
    remote_svc.mark(r, True)
    return out


async def call_remote_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    r, rid = _remote_for(model)
    kw = _remote_kwargs(r)
    covered = _backup_model() is not None
    if covered and not _remote_may_be_tried(r):
        raise _remote_unreachable(r)
    extra_params, blocked, on_thinks = _remote_thinking(r, rid, extra_params)
    stream = _call_openai_compatible_streaming(
        r.id, r.api_base, "/chat/completions", kw["api_key"], False,
        system_prompt, messages, rid, max_tokens, extra_headers=kw["extra_headers"],
        error_hint_fn=other_pcs.image_hint, param_blocklist=blocked, on_thinks=on_thinks,
        connect_timeout=BACKUP_CONNECT_SECONDS if covered else None, **extra_params,
    )
    source = _remote_first_word(stream, r, BACKUP_FIRST_WORD_SECONDS) if covered else stream
    try:
        async for chunk in source:
            yield chunk
        remote_svc.mark(r, True)
    except RemoteUnavailable:
        raise
    except HTTPException as e:
        raise _remote_failed(r, e, streaming=covered)
    finally:
        await _aclose_quietly(source)
        await stream.aclose()


# --- Ollama model discovery (2026-09-13) ---
# Real bug, live-tested by the director: the dashboard offered a hand-picked example model
# (ollama/llama3.1:8b) that wasn't actually installed, so Quick Test's own "success path" always
# failed. Ollama's real GET /api/tags -- at the server ROOT, not under the OpenAI-compat /v1 path
# OLLAMA_LOCAL_BASE_URL/OLLAMA_CLOUD_BASE_URL point at -- lists what's genuinely there.

OLLAMA_TAGS_TIMEOUT = 1.5  # seconds -- a stopped daemon must never slow the dashboard down
OLLAMA_TAGS_CACHE_TTL = 10.0  # seconds -- avoid hitting the daemon/Cloud on every dashboard render

_ollama_tags_cache: dict[str, tuple[float, Optional[dict]]] = {}


def _ollama_api_root(base_url: str) -> str:
    """/api/tags lives at the server root -- derive it from the existing OLLAMA_*_BASE_URL
    constants (which point at the OpenAI-compat /v1 path) rather than hardcoding a second URL that
    could silently drift out of sync with them."""
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url


async def _fetch_ollama_tags(base_url: str, api_key: Optional[str] = None, *, force: bool = False) -> Optional[dict]:
    """GET /api/tags against the given target (local daemon or Ollama Cloud). Returns the parsed
    `{"models": [...]}` dict, or None if the daemon isn't reachable, times out, or errors -- callers
    treat None as "can't tell you what's installed right now," never as an empty install.

    Cached briefly per `base_url` so a dashboard render (which calls this via the ollama entry in
    _dashboard_providers()) doesn't hit the daemon/Cloud again immediately after a /v1/models call
    in the same few seconds. `force=True` (the dashboard's own Refresh button) bypasses the cache.
    """
    now = time.time()
    if not force and base_url in _ollama_tags_cache:
        cached_at, cached_value = _ollama_tags_cache[base_url]
        if now - cached_at < OLLAMA_TAGS_CACHE_TTL:
            return cached_value

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    result: Optional[dict] = None
    try:
        timeout = aiohttp.ClientTimeout(total=OLLAMA_TAGS_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{_ollama_api_root(base_url)}/api/tags", headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        result = None

    _ollama_tags_cache[base_url] = (now, result)
    return result


# Keep a local Ollama model loaded (2026-10-07, ollama_keepwarm.py): the first choice of each route is loaded through Ollama's own API with keep_alive -1, because the
# OpenAI-style endpoint real requests use ignores it and Ollama drops an idle model after ~5 minutes (a cold load is longer than SkyrimNet waits).
async def _keepwarm_ps() -> Optional[dict]:
    """{loaded model name: its entry} from Ollama's /api/ps, or None when Ollama is not answering."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=OLLAMA_TAGS_TIMEOUT)) as session:
            async with session.get(f"{_ollama_api_root(OLLAMA_LOCAL_BASE_URL)}/api/ps") as resp:
                if resp.status != 200:
                    return None
                return {(m.get("name") or m.get("model")): m for m in ((await resp.json()).get("models") or [])}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


async def _keepwarm_sizes() -> Optional[dict]:
    tags = await _fetch_ollama_tags(OLLAMA_LOCAL_BASE_URL, None)
    return None if tags is None else {(m.get("name") or m.get("model")): int(m.get("size") or 0) for m in (tags.get("models") or [])}


async def _keepwarm_load(model: str, keep_alive) -> None:
    """An empty /api/generate loads the model and sets how long it stays; no prompt, so nothing is generated."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=ollama_keepwarm.LOAD_TIMEOUT_S)) as session:
        async with session.post(f"{_ollama_api_root(OLLAMA_LOCAL_BASE_URL)}/api/generate", json={"model": model, "keep_alive": keep_alive, "stream": False}) as resp:
            if resp.status != 200:
                detail = ""
                try:
                    detail = ((await resp.json()).get("error") or "")
                except Exception:
                    pass
                raise RuntimeError(detail or f"Ollama answered {resp.status}")


async def _keepwarm_free_bytes() -> Optional[int]:
    s = await gpu_monitor_svc.sample()
    return int(s.free_mb) * 1024 * 1024 if s and s.free_mb else None


_keepwarm = ollama_keepwarm.KeepWarm(ps=_keepwarm_ps, sizes=_keepwarm_sizes, load=_keepwarm_load, free_bytes=_keepwarm_free_bytes, log=lambda m: logger.info(m))
_keepwarm_kick = asyncio.Event()


def _keepwarm_wanted() -> list:
    if ollama_api_key:                                         # Ollama Cloud: nothing to keep loaded
        return []
    return ollama_keepwarm.first_choices(_routing_settings().get("routes"), _expand_route_entry, is_ollama_model, strip_ollama_prefix)


async def _keepwarm_tick() -> None:
    cfg = _load_config()
    ka = cfg.get("ollama_keep_alive", ollama_keepwarm.KEEP_FOREVER)
    ka = ka if isinstance(ka, (int, str)) and not isinstance(ka, bool) else ollama_keepwarm.KEEP_FOREVER
    off = {ollama_keepwarm.full_name(strip_ollama_prefix(x)) for x in _routing_settings().get("keep_loaded_off", [])}
    await _keepwarm.tick(_keepwarm_wanted(), off, ka)


async def _keepwarm_loop() -> None:
    global _keepwarm_kick
    _keepwarm_kick = asyncio.Event()                       # made here so it belongs to the running loop
    while True:
        try:
            await _keepwarm_tick()
        except Exception as e:
            logger.debug(f"Keep-loaded check failed ({e.__class__.__name__})")
        try:
            await asyncio.wait_for(_keepwarm_kick.wait(), ollama_keepwarm.CHECK_EVERY_S)     # a changed route wakes it at once
        except asyncio.TimeoutError:
            pass
        _keepwarm_kick.clear()


# Which of Ollama's models can chat (2026-10-05). `/api/tags` does not say; `POST /api/show` gives a `capabilities` list (Ollama 0.6.4 and newer): a model without
# `completion` is an embedding (or other) model that answers a chat request with "does not support chat". An older Ollama gives no list: that is "unknown" (None), and
# an unknown model is treated as chat-capable, exactly as before. Asked once per model (a model's digest changes when it is updated) and kept, so the dashboard's
# frequent reads cost nothing.
OLLAMA_SHOW_TIMEOUT = 1.5
OLLAMA_CAPS_KEEP = 600.0          # seconds a lookup is kept; an unknown answer (daemon busy, old Ollama) is asked again after OLLAMA_CAPS_RETRY
OLLAMA_CAPS_RETRY = 60.0
_ollama_caps_cache: dict = {}


async def _ollama_kinds(base_url: str, tag_models: list) -> dict:
    """{model name: its kinds (model_kinds: Chat, Decisions, Vision, Embeddings, Other), or None when Ollama did not say} for the models `/api/tags` listed. Never raises."""
    now = time.time()
    out: dict = {}
    todo = []
    for m in tag_models:
        name = m.get("name") or m.get("model") or ""
        key = (base_url, name, m.get("digest") or "")
        hit = _ollama_caps_cache.get(key)
        if hit and now - hit[0] < (OLLAMA_CAPS_KEEP if hit[1] is not None else OLLAMA_CAPS_RETRY):
            out[name] = hit[1]
        else:
            todo.append((name, key))

    async def ask(session, name, key):
        verdict = None
        try:
            async with session.post(f"{_ollama_api_root(base_url)}/api/show", json={"model": name, "name": name}) as resp:
                if resp.status == 200:
                    verdict = model_kinds.from_ollama((await resp.json()).get("capabilities"))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            verdict = None
        _ollama_caps_cache[key] = (time.time(), verdict)
        out[name] = verdict

    if todo:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=OLLAMA_SHOW_TIMEOUT)) as session:
            await asyncio.gather(*(ask(session, n, k) for n, k in todo))
    return out


def _ollama_model_size_gb(size_bytes) -> Optional[str]:
    if not size_bytes:
        return None
    return f"{size_bytes / (1024 ** 3):.1f} GB"


def _ollama_model_row(m: dict, *, cloud: bool) -> list:
    """[id, name, note] for one installed/available model -- skips any detail that's missing
    rather than printing a blank. Cloud entries get a plain "Cloud model" note instead of
    size/quantization: Cloud's own `details` come back empty and `size` is the full model weight
    (hundreds of GB), neither of which means anything to a Cloud user."""
    name = m.get("name") or m.get("model") or ""
    if cloud:
        note = "Cloud model"
    else:
        details = m.get("details") or {}
        parts = [p for p in (details.get("parameter_size"), details.get("quantization_level")) if p]
        size = _ollama_model_size_gb(m.get("size"))
        if size:
            parts.append(size)
        note = " · ".join(parts)
    return [f"{OLLAMA_PREFIX}{name}", name, thinking_off.THINKERS.add_to(f"{OLLAMA_PREFIX}{name}", note)]


async def _ollama_status(*, force: bool = False) -> dict:
    """The one place that decides Ollama's REAL current state -- local daemon reachability +
    installed models, or Ollama Cloud's own catalog once a key is saved -- shared by
    _dashboard_providers(), /v1/models, and the dashboard's own Refresh route
    (/config/ollama-models) so there is exactly one source of truth for "is Ollama usable right
    now, and with which models," never three separately-maintained copies of that logic.

    Returns state/status/models/testable/live/empty/any/label -- see _dashboard_providers() for how
    these map onto the shared provider-card shape every other provider also uses.
    """
    if ollama_api_key:
        tags = await _fetch_ollama_tags(OLLAMA_CLOUD_BASE_URL, ollama_api_key, force=force)
        if tags is None:
            return {
                "state": "ok", "status": "Ollama Cloud (key saved)", "models": [],
                # Testable anyway (task's own rule: "Quick Test lists Ollama when the Cloud list has
                # >=1 model OR the key is saved") -- `any` lets Quick Test's "Type your own..." cover
                # the empty dropdown this branch would otherwise leave with nothing selectable.
                "testable": True, "any": f"{OLLAMA_PREFIX}MODEL", "live": True, "label": "Cloud models",
                "empty": "Couldn't load the cloud model list. You can still type a model name into SkyrimNet.",
            }
        rows = sorted((tags.get("models") or []), key=lambda m: (m.get("name") or "").lower())
        rows = [_ollama_model_row(m, cloud=True) for m in rows]
        n = len(rows)
        return {
            "state": "ok", "status": f"Ollama Cloud: {n} {'model' if n == 1 else 'models'}",
            "models": rows, "testable": True, "any": None, "live": True, "label": "Cloud models", "empty": "",
        }

    tags = await _fetch_ollama_tags(OLLAMA_LOCAL_BASE_URL, None, force=force)
    if tags is None:
        return {
            "state": "off", "status": "Not running", "models": [], "testable": False,
            "any": None, "live": True, "label": "Installed models",
            "empty": "Ollama isn't running here. Start it, then press Check for models.",
        }
    raw = sorted((tags.get("models") or []), key=lambda m: (m.get("name") or "").lower())
    kinds = await _ollama_kinds(OLLAMA_LOCAL_BASE_URL, raw) if raw else {}
    rows = []
    model_kinds.BOOK.forget_prefix(OLLAMA_PREFIX)
    for m in raw:
        row = _ollama_model_row(m, cloud=False)
        k = kinds.get(row[1])
        model_kinds.BOOK.note(row[0], k)
        row[2] = " · ".join(x for x in (row[2], model_kinds.words(k)) if x)
        rows.append(row)
    rows.sort(key=lambda r: not model_kinds.can_chat(kinds.get(r[1])))         # models that can chat first (stable: still by name), so the first one listed is one that can answer
    chat_ids = [r[0] for r in rows if model_kinds.can_chat(kinds.get(r[1]))]
    if not rows:
        return {
            "state": "warn", "status": "Local: no models installed", "models": [], "testable": False,
            "any": None, "live": True, "label": "Installed models",
            "empty": "Ollama is running, but has no models. Add one in Ollama, then press Check for models.",
        }
    n = len(rows)
    return {
        "state": "ok", "status": f"Local: {n} {'model' if n == 1 else 'models'} installed",
        "models": rows, "testable": True, "any": None, "live": True, "label": "Installed models", "empty": "", "chat": chat_ids,
        "kinds": {r[0]: kinds[r[1]] for r in rows if kinds.get(r[1])},
    }


# --- LM Studio (2026-10-01): its local OpenAI-compatible server, a provider like Ollama ---
# Model IDs are `lmstudio/<LM Studio's own model id>` (e.g. lmstudio/google/gemma-4-e4b). No key. The list comes from LM
# Studio's own `/api/v0/models` (checked against a real LM Studio 0.4.25: it says which models are loaded and which see
# pictures, and lists embedding models too, which are left out); `/v1/models` is the fallback. LM Studio loads a model on
# the first request that names it, so every downloaded model is usable. "Start LM Studio's server" reuses the Courier's
# LM Studio adapter (courier_adapters.py: the `lms` command-line tool).
LMSTUDIO_BLOCKED_PARAMS = {"thinking", "reasoning"}
LMSTUDIO_MODELS_TIMEOUT = 0.8   # seconds: a closed port can take Windows ~2 s to refuse, and the dashboard mustn't wait on it
LMSTUDIO_MODELS_CACHE_TTL = 10.0
_lmstudio_cache: dict[str, tuple[float, Optional[list]]] = {}
_lmstudio_adapters: dict[str, "courier_adapters.LmStudioAdapter"] = {}


def _lmstudio_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def normalize_lmstudio_address(text: str) -> str:
    """What the player types for LM Studio's address (`localhost:1234`, `192.168.1.5:1234`, `http://host:1234/v1`) as a base URL
    ending in /v1. Raises ValueError with a plain reason when it doesn't look like an address."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Type LM Studio's address first, like localhost:1234.")
    if "://" not in t:
        t = "http://" + t
    parts = urlsplit(t)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("That address doesn't look right. Try localhost:1234.")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("That address doesn't look right. LM Studio is usually at localhost:1234.")
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    return f"{parts.scheme}://{host}{':' + str(port) if port else ''}/v1"


def _lmstudio_adapter(base_url: str):
    a = _lmstudio_adapters.get(base_url)
    if a is None:
        a = _lmstudio_adapters[base_url] = courier_adapters.LmStudioAdapter(logger, base=_lmstudio_root(base_url))
    return a


async def _fetch_lmstudio_models(base_url: str, *, force: bool = False) -> Optional[list]:
    """LM Studio's downloaded chat models as [{"id", "loaded", "vision"}], or None when its server doesn't answer. Cached briefly
    per address (a stopped server must never slow every dashboard render)."""
    now = time.monotonic()
    if not force and base_url in _lmstudio_cache and now - _lmstudio_cache[base_url][0] < LMSTUDIO_MODELS_CACHE_TTL:
        return _lmstudio_cache[base_url][1]
    result: Optional[list] = None
    root = _lmstudio_root(base_url)
    try:
        timeout = aiohttp.ClientTimeout(total=LMSTUDIO_MODELS_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{root}/api/v0/models") as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    result = [{"id": str(m["id"]), "loaded": m.get("state") == "loaded", "vision": m.get("type") == "vlm", "kinds": model_kinds.from_lmstudio(m.get("type")),
                               "context": m.get("loaded_context_length") if m.get("state") == "loaded" else None}          # (the context it was loaded with: the limit a prompt must fit)
                              for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")
                              and str(m.get("type", "llm")).lower() not in ("embedding", "embeddings")
                              and not courier_models.is_helper_model(m.get("id"), m.get("path"), m.get("arch") or m.get("architecture"))]
            if result is None:                      # an older LM Studio without the richer route
                async with session.get(f"{root}/v1/models") as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        result = [{"id": str(m["id"]), "loaded": False, "vision": False} for m in (data.get("data") or [])
                                  if isinstance(m, dict) and m.get("id") and "embed" not in str(m["id"]).lower()
                                  and not courier_models.is_helper_model(m.get("id"))]
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        result = None
    _lmstudio_cache[base_url] = (now, result)
    return result


def _lmstudio_row(m: dict) -> list:
    kinds = m.get("kinds") or ([model_kinds.CHAT, model_kinds.VISION] if m.get("vision") else None)          # (`vision` comes from LM Studio's own type `vlm`)
    notes = [n for n, on in (("Loaded", m["loaded"]), (model_kinds.words(kinds), bool(kinds))) if on]
    model_kinds.BOOK.note(f"{LMSTUDIO_PREFIX}{m['id']}", kinds)
    return [f"{LMSTUDIO_PREFIX}{m['id']}", m["id"], thinking_off.THINKERS.add_to(f"{LMSTUDIO_PREFIX}{m['id']}", " · ".join(notes))]


async def _lmstudio_status(*, force: bool = False) -> dict:
    """The one place that decides LM Studio's real state (like _ollama_status): shared by the Providers card, /v1/models and the
    Refresh route. Extra fields: `address`, `defaultAddress`, `canStart` (its `lms` tool is on this device and the server is off)."""
    base = lmstudio_base_url
    custom = base != LMSTUDIO_DEFAULT_BASE_URL
    extras = {"address": _lmstudio_root(base) if custom else "", "defaultAddress": _lmstudio_root(LMSTUDIO_DEFAULT_BASE_URL),
              "addressEndpoint": "/config/lmstudio-address", "startEndpoint": "/config/lmstudio-start", "canStart": False}
    base_entry = {"any": None, "live": True, "label": "Models in LM Studio", **extras}
    models = await _fetch_lmstudio_models(base, force=force)
    if models is not None:
        rows = [_lmstudio_row(m) for m in sorted(models, key=lambda m: m["id"].lower())]
        if not rows:
            return {**base_entry, "state": "warn", "status": "No models downloaded", "models": [], "testable": False,
                    "empty": "LM Studio is running, but has no models. Download one in LM Studio, then press Check for models."}
        loaded = sum(1 for m in models if m["loaded"])
        n = len(rows)
        status = f"{n} {'model' if n == 1 else 'models'}" + (f", {loaded} loaded" if loaded else "")
        return {**base_entry, "state": "ok", "status": status, "models": rows, "testable": True, "empty": "",
                "kinds": {f"{LMSTUDIO_PREFIX}{m['id']}": m["kinds"] for m in models if m.get("kinds")}}
    adapter = _lmstudio_adapter(base)
    has_cli = bool(adapter.cli)
    installed = has_cli or (not custom and adapter._app_installed())
    if custom:
        return {**base_entry, "state": "off", "status": "Can't reach it", "models": [], "testable": False,
                "empty": "Nothing answered. Make sure LM Studio's server is on, then press Check for models."}
    if not installed:
        return {**base_entry, "state": "off", "status": "Not installed", "models": [], "testable": False, "empty": ""}
    if has_cli:
        return {**base_entry, "state": "off", "status": "LM Studio isn't running", "models": [], "testable": False, "canStart": True,
                "empty": "LM Studio's server is off. Start it here or in LM Studio, then press Check for models."}
    return {**base_entry, "state": "off", "status": "LM Studio isn't running", "models": [], "testable": False,
            "empty": "LM Studio's server is off. Turn it on in LM Studio, then press Check for models."}


def _lmstudio_target() -> tuple[str, Optional[str], bool]:
    return lmstudio_base_url, None, False


async def call_lmstudio_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    base_url, api_key, require_key = _lmstudio_target()
    extra_params, blocked, on_thinks = _thinking_for("lmstudio", LMSTUDIO_PREFIX + model, extra_params, LMSTUDIO_BLOCKED_PARAMS)
    return await _call_openai_compatible_direct(
        "LM Studio", base_url, "/chat/completions", api_key, require_key,
        system_prompt, messages, model, max_tokens, param_blocklist=blocked, engine_errors=True, on_thinks=on_thinks, **extra_params,
    )


async def call_lmstudio_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    base_url, api_key, require_key = _lmstudio_target()
    extra_params, blocked, on_thinks = _thinking_for("lmstudio", LMSTUDIO_PREFIX + model, extra_params, LMSTUDIO_BLOCKED_PARAMS)
    stream = _call_openai_compatible_streaming(
        "LM Studio", base_url, "/chat/completions", api_key, require_key,
        system_prompt, messages, model, max_tokens, param_blocklist=blocked, engine_errors=True, on_thinks=on_thinks, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()


# --- Gemini (API key, via Google's own OpenAI-compatible endpoint) ---
# Confirmed via a real, live call with a real key (2026-09-15, docs/research-google-gemini-
# support.md): Gemini's OpenAI-compat layer rejects these outright with a 400 "Unknown name
# '<field>': Cannot find field" (presence_penalty specifically fails with "Penalty is not enabled
# for this model" instead, but is still a hard 400 either way) -- a real probe, not the docs'
# "still in beta while we extend feature support" disclaimer taken on faith. Three genuinely
# different sets across the three OpenAI-compatible providers here: Kilo blocks
# {thinking, reasoning, top_k}; Ollama blocks those three plus reasoning_effort; Gemini blocks
# thinking/reasoning/top_k plus frequency_penalty/presence_penalty/seed/logprobs, but -- like
# Kilo -- lets reasoning_effort through untouched (confirmed accepted, HTTP 200, in the same probe).
GEMINI_BLOCKED_PARAMS = {"thinking", "reasoning", "top_k", "frequency_penalty", "presence_penalty", "seed", "logprobs"}


def _gemini_error_hint(status: int, message: str) -> str:
    """Gemini's free tier has a real per-minute rate limit (docs/research-google-gemini-
    support.md) -- Google's own raw 429 message doesn't say that, so a plain "resource exhausted"
    reads like something's broken rather than "you've hit a limit that resets in under a minute."
    Appended rather than replacing the upstream text, so the real reason is never lost."""
    if status == 429:
        return f"{message} (Gemini's free tier has a per-minute rate limit -- wait a moment and try again.)"
    return message


async def call_gemini_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    return await _call_openai_compatible_direct(
        "Gemini", GEMINI_BASE_URL, "/chat/completions", gemini_api_key, True,
        system_prompt, messages, model, max_tokens, param_whitelist=GEMINI_ALLOWED_PARAMS,
        error_hint_fn=_gemini_error_hint, **extra_params,
    )


async def call_gemini_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    stream = _call_openai_compatible_streaming(
        "Gemini", GEMINI_BASE_URL, "/chat/completions", gemini_api_key, True,
        system_prompt, messages, model, max_tokens, param_whitelist=GEMINI_ALLOWED_PARAMS,
        error_hint_fn=_gemini_error_hint, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        # Close the shared helper right away if SkyrimNet hangs up (its `finally` closes the
        # upstream response) rather than leaving that to garbage collection.
        await stream.aclose()


# --- Gemini model discovery (2026-09-15) ---
# Same "don't offer a model nobody can actually use" motivation as Ollama's own discovery above,
# but a different shape: Gemini's account isn't user-curated the way a local Ollama install is --
# Google's own GET /models returns EVERY model on the account, including embeddings, TTS,
# transcription, live/bidi audio, and image-only models this proxy's plain chat-completions path
# can't use at all. Unlike the native (non-OpenAI-compat) Gemini API, this endpoint's response
# carries no per-model capability field to filter on (confirmed live 2026-09-15: just id/object/
# owned_by/display_name) -- so the filter here is purely name-based, on the same excluded-token
# idea, extended with a few more tokens genuinely irrelevant to a TEXT chat proxy specifically
# (image/omni/robotics/computer) that a general-purpose Gemini integration might reasonably keep.

# --- A key provider's live model list: "couldn't reach" is not "the key was refused" (2026-10-03) ---
# xAI answered 400 "Incorrect API key provided" for a key that wasn't an xAI key, and the dashboard said "couldn't reach" with nothing in the log. One shared
# helper now tells three things apart: unreachable (no answer / too slow), refused (401, 403, or a 400 that talks about the API key), and anything else.
# The last problem seen for each (provider, key) is kept here so a cached answer still knows why it was empty. Never holds or logs the key.
_model_list_problems: dict = {}


def _key_refused_status(label: str, console: str) -> str:
    return f"{label} didn't accept this key. Make a new one at {console} and paste it here."


def _classify_model_list_failure(status: int, body: str) -> str:
    """'refused' for 401/403 and for a 400 whose message is about the API key, else 'other'."""
    if status in (401, 403):
        return "refused"
    if status == 400 and re.search(r"api[ _-]?key", body or "", re.I):
        return "refused"
    return "other"


async def _get_key_model_list(provider: str, label: str, url: str, api_key: str, timeout_seconds: float) -> Optional[list]:
    """GET `url` with the key as a bearer token: the raw `data` list, or None when it can't be read. Records WHY in _model_list_problems
    ('unreachable', 'refused', 'other', or None when it worked) and logs one line for each failure (status and the service's own message, never the key)."""
    result: Optional[list] = None
    problem: Optional[str] = None
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"Authorization": f"Bearer {api_key}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("data") or []
                else:
                    try:
                        body = await resp.text()
                    except Exception:
                        body = ""
                    message = body
                    try:
                        parsed = json.loads(body)
                        err = parsed.get("error") if isinstance(parsed, dict) else None
                        message = (err.get("message") if isinstance(err, dict) else err) or body
                    except (ValueError, AttributeError, TypeError):
                        pass
                    message = str(message).replace(api_key, "***").strip()[:200]
                    problem = _classify_model_list_failure(resp.status, body)
                    logger.warning(f"{label}'s model list: {'the key was refused' if problem == 'refused' else 'unexpected answer'} (HTTP {resp.status}): {message}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        problem = "unreachable"
        logger.warning(f"{label}'s model list: couldn't reach it ({e.__class__.__name__})")
    except ValueError:
        problem = "other"
        logger.warning(f"{label}'s model list: the answer wasn't readable")
    _model_list_problems[(provider, api_key)] = problem
    return result


GEMINI_MODEL_LIST_TIMEOUT = 3.0  # seconds -- a real call to Google, not a local daemon; must still never hang the dashboard
GEMINI_MODEL_LIST_CACHE_TTL = 30.0  # seconds -- longer than Ollama's 10s: this is a real network round trip, not a local daemon poll

_gemini_models_cache: dict[str, tuple[float, Optional[list]]] = {}  # keyed by API key, so a key change can't serve a stale catalog

GEMINI_EXCLUDED_MODEL_TOKENS = {
    "tts", "transcribe", "live", "latest", "audio", "embedding", "image", "omni", "robotics", "computer",
    # Deprecated generation (2026-09-16): Google's own /models list still returns these, but a real
    # chat completion to gemini-2.5-flash now fails with "This model models/gemini-2.5-flash is no
    # longer available to new users. Please update your code to use models/gemini-3.6-flash..." --
    # confirmed live via this dashboard's own Quick Test, real key, real request. `name.split("-")`
    # gives "2.5" as its own whole token for every real 2.5-generation id (gemini-2.5-flash,
    # gemini-2.5-pro, gemini-2.5-flash-lite, etc. -- confirmed against the real, current 58-model
    # catalog), and no OTHER real model in that catalog carries "2.5" as a substring of a different
    # token, so a whole-token match is safe here without needing a version-string prefix check.
    "2.5",
}

# A short, hand-picked fallback (task's own "fall back to a short static list" instruction) --
# shown with no key at all, or when a saved key's live /models call fails. Kept intentionally
# small and current rather than mirroring the full live catalog; re-verify against
# docs/research-google-gemini-support.md or a fresh live call if these ever look stale.
# The 2.5 generation (gemini-2.5-flash/-pro) was dropped 2026-09-16 -- deprecated by Google, see
# GEMINI_EXCLUDED_MODEL_TOKENS' own comment -- so a no-key/failed-call visitor can't be steered at
# a model that would fail the moment they actually tried it.
GEMINI_STATIC_FALLBACK_MODELS = [
    [f"{GEMINI_PREFIX}gemini-3.8-flash", "Gemini 3.8 Flash", "Newest, most capable Flash"],
    [f"{GEMINI_PREFIX}gemini-3.7-flash", "Gemini 3.7 Flash", "One generation behind 3.8"],
]


async def _fetch_gemini_models(api_key: str, *, force: bool = False) -> Optional[list]:
    """GET {GEMINI_BASE_URL}/models with the real key. Returns the raw `data` list from the
    response, or None if the call fails/times out/errors -- callers treat None as "can't tell you
    what's available right now," same convention _fetch_ollama_tags uses, never as an empty catalog.
    Cached per key (`force=True`, the dashboard's own Refresh button, bypasses it) for the same
    reason Ollama's own tags are cached: a dashboard render and a `/v1/models` call landing within
    the same few seconds shouldn't each cost Google a separate round trip."""
    now = time.time()
    if not force and api_key in _gemini_models_cache:
        cached_at, cached_value = _gemini_models_cache[api_key]
        if now - cached_at < GEMINI_MODEL_LIST_CACHE_TTL:
            return cached_value

    result = await _get_key_model_list("gemini", "Gemini", f"{GEMINI_BASE_URL}/models", api_key, GEMINI_MODEL_LIST_TIMEOUT)
    _gemini_models_cache[api_key] = (now, result)
    return result


def _gemini_model_row(m: dict) -> Optional[list]:
    """[gemini/<name>, <display name>, <note>] for one model Google's own /models listed, or None
    if it's excluded (not a `gemini-` id, or its name contains an excluded token -- see
    GEMINI_EXCLUDED_MODEL_TOKENS' own comment). `id` comes back as `models/gemini-3.8-flash`
    (confirmed live 2026-09-15) -- the `models/` prefix is stripped before the `gemini/` prefix
    this proxy uses is applied, and a real chat completion accepts the bare name with no
    `models/` (also confirmed live)."""
    raw_id = m.get("id") or ""
    name = raw_id[len("models/"):] if raw_id.startswith("models/") else raw_id
    if not name.startswith("gemini-"):
        return None
    tokens = name.split("-")
    if any(t in GEMINI_EXCLUDED_MODEL_TOKENS for t in tokens):
        return None
    note = "Preview" if "preview" in tokens else ""
    return [f"{GEMINI_PREFIX}{name}", m.get("display_name") or name, note]


async def _gemini_status(*, force: bool = False) -> dict:
    """The one place deciding Gemini's real current state -- shared by _dashboard_providers(),
    /v1/models, and GET /config/gemini-models (the Refresh route), same pattern _ollama_status()
    already establishes. `any` is set to f"{GEMINI_PREFIX}MODEL" whenever the shown list might be
    incomplete (no key, or a live call that failed/came back empty) so Quick Test's "Type your
    own..." still works; cleared to None once a real live list is actually showing."""
    if not gemini_api_key:
        return {
            "state": "off", "status": "No key yet", "models": GEMINI_STATIC_FALLBACK_MODELS,
            "testable": False, "any": f"{GEMINI_PREFIX}MODEL", "live": True,
            "label": "Models", "empty": "",
        }

    raw = await _fetch_gemini_models(gemini_api_key, force=force)
    if raw is None and _model_list_problems.get(("gemini", gemini_api_key)) == "refused":
        return {
            "state": "warn", "status": _key_refused_status("Gemini", "aistudio.google.com"), "models": GEMINI_STATIC_FALLBACK_MODELS,
            "testable": True, "any": f"{GEMINI_PREFIX}MODEL", "live": True, "label": "Models", "empty": "",
        }
    if raw is None:
        return {
            "state": "warn", "status": "Key saved, but couldn't reach the live model list", "models": GEMINI_STATIC_FALLBACK_MODELS,
            "testable": True, "any": f"{GEMINI_PREFIX}MODEL", "live": True,
            "label": "Models", "empty": "",
        }

    rows = sorted((row for row in (_gemini_model_row(m) for m in raw) if row), key=lambda r: r[0])
    if not rows:
        return {
            "state": "warn", "status": "Key saved, but the live list came back empty", "models": GEMINI_STATIC_FALLBACK_MODELS,
            "testable": True, "any": f"{GEMINI_PREFIX}MODEL", "live": True,
            "label": "Models", "empty": "",
        }

    n = len(rows)
    return {
        "state": "ok", "status": f"Key saved: {n} model{'s' if n != 1 else ''} available",
        "models": rows, "testable": True, "any": None, "live": True, "label": "Models", "empty": "",
    }


# --- xAI (API key): calls and model discovery ---
# xAI's chat API takes the OpenAI shape. Its reasoning models refuse the penalty and `stop` settings, so only settings every model takes go through.
XAI_ALLOWED_PARAMS = frozenset({"temperature", "top_p", "n", "stream", "stream_options", "max_tokens", "max_completion_tokens", "seed",
                                "response_format", "tools", "tool_choice", "reasoning_effort", "user"})


async def call_xai_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    return await _call_openai_compatible_direct(
        "xAI", XAI_BASE_URL, "/chat/completions", xai_api_key, True,
        system_prompt, messages, model, max_tokens, param_whitelist=XAI_ALLOWED_PARAMS, **extra_params,
    )


async def call_xai_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    stream = _call_openai_compatible_streaming(
        "xAI", XAI_BASE_URL, "/chat/completions", xai_api_key, True,
        system_prompt, messages, model, max_tokens, param_whitelist=XAI_ALLOWED_PARAMS, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()          # (close the shared helper right away if SkyrimNet hangs up)


XAI_MODEL_LIST_TIMEOUT = 3.0
XAI_MODEL_LIST_CACHE_TTL = 30.0
_xai_models_cache: dict[str, tuple[float, Optional[list]]] = {}     # keyed by key, so a changed key can't show an old list
# xAI's /models also lists models that make pictures, video and embeddings; only chat models can answer SkyrimNet.
XAI_EXCLUDED_MODEL_TOKENS = {"imagine", "image", "video", "embedding", "embeddings", "vision-only", "aurora", "tts", "stt", "speech"}
XAI_STATIC_FALLBACK_MODELS = [
    [f"{XAI_PREFIX}grok-4", "Grok 4", ""],
]


async def _fetch_xai_models(api_key: str, *, force: bool = False) -> Optional[list]:
    """GET {XAI_BASE_URL}/models with the key: the raw `data` list, or None when it can't be read (None means "can't tell",
    never "no models"). Remembered per key for a few seconds; Refresh (`force`) skips that."""
    now = time.time()
    if not force and api_key in _xai_models_cache:
        cached_at, cached_value = _xai_models_cache[api_key]
        if now - cached_at < XAI_MODEL_LIST_CACHE_TTL:
            return cached_value
    result = await _get_key_model_list("xai", "xAI", f"{XAI_BASE_URL}/models", api_key, XAI_MODEL_LIST_TIMEOUT)
    _xai_models_cache[api_key] = (now, result)
    return result


def _xai_display_name(model_id: str) -> str:
    """`grok-4-fast-reasoning` -> `Grok 4 Fast Reasoning`."""
    return " ".join(t.capitalize() if t.isalpha() else t for t in model_id.split("-"))


def _xai_model_row(m: dict) -> Optional[list]:
    """[xai/<id>, <name>, <note>] for one listed chat model, or None for one that isn't a chat model."""
    name = (m.get("id") or "").strip()
    if not name.startswith("grok"):
        return None
    tokens = name.lower().split("-")
    if any(t in XAI_EXCLUDED_MODEL_TOKENS for t in tokens):
        return None
    return [f"{XAI_PREFIX}{name}", _xai_display_name(name), ""]


async def _xai_status(*, force: bool = False) -> dict:
    """xAI's real current state, shared by the dashboard, /v1/models and the Refresh route (the same pattern as _gemini_status)."""
    if not xai_api_key:
        return {
            "state": "off", "status": "No key yet", "models": XAI_STATIC_FALLBACK_MODELS,
            "testable": False, "any": f"{XAI_PREFIX}MODEL", "live": True, "label": "Models", "empty": "",
        }
    raw = await _fetch_xai_models(xai_api_key, force=force)
    if raw is None and _model_list_problems.get(("xai", xai_api_key)) == "refused":
        return {
            "state": "warn", "status": _key_refused_status("xAI", "console.x.ai"), "models": XAI_STATIC_FALLBACK_MODELS,
            "testable": True, "any": f"{XAI_PREFIX}MODEL", "live": True, "label": "Models", "empty": "",
        }
    if raw is None:
        return {
            "state": "warn", "status": "Key saved, but couldn't reach the live model list", "models": XAI_STATIC_FALLBACK_MODELS,
            "testable": True, "any": f"{XAI_PREFIX}MODEL", "live": True, "label": "Models", "empty": "",
        }
    rows = sorted((row for row in (_xai_model_row(m) for m in raw) if row), key=lambda r: r[0])
    if not rows:
        return {
            "state": "warn", "status": "Key saved, but the live list came back empty", "models": XAI_STATIC_FALLBACK_MODELS,
            "testable": True, "any": f"{XAI_PREFIX}MODEL", "live": True, "label": "Models", "empty": "",
        }
    n = len(rows)
    return {
        "state": "ok", "status": f"Key saved: {n} model{'s' if n != 1 else ''} available",
        "models": rows, "testable": True, "any": None, "live": True, "label": "Models", "empty": "",
    }


# --- Ready-made "paste a key" sources with a small free allowance: Mistral, Groq, Cloudflare Workers AI (key_sources.py) ---
# One block serves all of them: they take the OpenAI shape, so the shared call code does the work and key_sources.py holds only what differs. The keys live in _ks_keys.
KS_MODEL_LIST_TIMEOUT = 3.0
KS_MODEL_LIST_CACHE_TTL = 30.0
_ks_models_cache: dict = {}          # (source id, key) -> (when, raw list or None): a changed key can't show an old list


def _ks_call_pair(src):
    # (direct, streaming) calls for one source, or (None, None) for no source. The key is read when a call is made, so a saved or cleared key counts at once.
    if src is None:
        return None, None

    async def direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
        base, token = key_sources.split_key(src, _ks_keys.get(src.id))
        return await _call_openai_compatible_direct(
            src.pill, base, "/chat/completions", token, True, system_prompt, messages, model, max_tokens,
            max_tokens_param=src.max_tokens_param, param_whitelist=set(src.allowed), **extra_params,
        )

    async def streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
        base, token = key_sources.split_key(src, _ks_keys.get(src.id))
        stream = _call_openai_compatible_streaming(
            src.pill, base, "/chat/completions", token, True, system_prompt, messages, model, max_tokens,
            max_tokens_param=src.max_tokens_param, param_whitelist=set(src.allowed), **extra_params,
        )
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()          # (close the shared helper right away if SkyrimNet hangs up)

    return direct, streaming


async def _ks_status(src, *, force: bool = False) -> dict:
    # A source's real current state, shared by the dashboard, /v1/models and the Refresh route (the same shape as _xai_status).
    key = _ks_keys.get(src.id)
    rows = [list(r) for r in src.rows]
    live = src.models_path is not None
    base = {"models": rows, "any": src.any, "live": live, "label": "Models", "empty": ""}
    if not key:
        return {**base, "state": "off", "status": "No key yet", "testable": False}
    if not live:
        return {**base, "state": "ok", "status": "Key saved", "testable": True}
    address, token = key_sources.split_key(src, key)
    now = time.time()
    cached = _ks_models_cache.get((src.id, key))
    if not force and cached and now - cached[0] < KS_MODEL_LIST_CACHE_TTL:
        raw = cached[1]
    else:
        raw = await _get_key_model_list(src.id, src.pill, address + src.models_path, token, KS_MODEL_LIST_TIMEOUT)
        _ks_models_cache[(src.id, key)] = (now, raw)
    if raw is None and _model_list_problems.get((src.id, token)) == "refused":
        return {**base, "state": "warn", "status": _key_refused_status(src.pill, src.console), "testable": True}
    if raw is None:
        return {**base, "state": "warn", "status": "Key saved, but couldn't reach the live model list", "testable": True}
    found = sorted((row for row in (key_sources.model_row(src, m) for m in raw if isinstance(m, dict)) if row), key=lambda r: r[0])
    if not found:
        return {**base, "state": "warn", "status": "Key saved, but the live list came back empty", "testable": True}
    n = len(found)
    return {**base, "state": "ok", "status": f"Key saved: {n} model{'s' if n != 1 else ''} available", "models": found, "any": None, "testable": True}


def _ks_entry(src, st: dict) -> dict:
    # One provider's entry for the dashboard (the Providers tab and Get started), from its status.
    entry = {
        "id": src.id, "name": src.name, "state": st["state"], "status": st["status"], "hasKey": bool(_ks_keys.get(src.id)), "testable": st["testable"],
        "placeholder": src.placeholder, "keyEndpoint": f"/config/{src.id}-key", "help": src.help_html, "any": st["any"], "live": st["live"],
        "label": st["label"], "empty": st["empty"], "models": st["models"],
    }
    if st["live"]:
        entry["refreshEndpoint"] = f"/config/{src.id}-models"
    if src.account_in_key:
        entry["keyShape"] = r"^[0-9A-Fa-f]{32}:\S{8,}$"            # checked in the page before it is saved: the plain reason is shown under the box
        entry["keyShapeHelp"] = key_sources.ACCOUNT_KEY_WRONG
    return entry


# --- Continuum (plan key): calls and model discovery ---
# Continuum's API takes the OpenAI shape and serves several makers' models, so only settings every model takes go through.
CONTINUUM_ALLOWED_PARAMS = frozenset({"temperature", "top_p", "n", "stream", "stream_options", "max_tokens", "max_completion_tokens", "seed",
                                      "response_format", "tools", "tool_choice", "user"})


async def call_continuum_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    return await _call_openai_compatible_direct(
        "Continuum", CONTINUUM_BASE_URL, "/chat/completions", continuum_api_key, True,
        system_prompt, messages, model, max_tokens, param_whitelist=CONTINUUM_ALLOWED_PARAMS, **extra_params,
    )


async def call_continuum_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    stream = _call_openai_compatible_streaming(
        "Continuum", CONTINUUM_BASE_URL, "/chat/completions", continuum_api_key, True,
        system_prompt, messages, model, max_tokens, param_whitelist=CONTINUUM_ALLOWED_PARAMS, **extra_params,
    )
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await stream.aclose()          # (close the shared helper right away if SkyrimNet hangs up)


CONTINUUM_MODEL_LIST_TIMEOUT = 3.0
CONTINUUM_MODEL_LIST_CACHE_TTL = 30.0
_continuum_models_cache: dict[str, tuple[float, Optional[list], Optional[str]]] = {}     # keyed by key, so a changed key can't show an old list
# /v1/models can list more than chat models; only chat models can answer SkyrimNet.
CONTINUUM_EXCLUDED_MODEL_TOKENS = {"embedding", "embeddings", "image", "images", "tts", "stt", "speech", "whisper", "moderation", "rerank"}
CONTINUUM_STATIC_FALLBACK_MODELS = [
    [f"{CONTINUUM_PREFIX}claude-opus-5-5", "claude-opus-5-5", ""],
]
CONTINUUM_KEY_START = "cont_sk_"


async def _fetch_continuum_models(api_key: str, *, force: bool = False) -> tuple[Optional[list], Optional[str]]:
    """GET {CONTINUUM_BASE_URL}/models with the key: (the raw `data` list, None), or (None, why it can't be read):
    "refused" when Continuum answered but doesn't accept the key, "unreachable" when no usable answer came back. (None, ...) means
    "can't tell", never "no models". Remembered per key for a few seconds; Refresh (`force`) skips that."""
    now = time.time()
    if not force and api_key in _continuum_models_cache:
        cached_at, cached_value, cached_why = _continuum_models_cache[api_key]
        if now - cached_at < CONTINUUM_MODEL_LIST_CACHE_TTL:
            return cached_value, cached_why
    result: Optional[list] = None
    why: Optional[str] = "unreachable"
    try:
        timeout = aiohttp.ClientTimeout(total=CONTINUUM_MODEL_LIST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{CONTINUUM_BASE_URL}/models", headers={"Authorization": f"Bearer {api_key}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("data") or []
                    why = None
                elif resp.status in (400, 401, 403):
                    why = "refused"
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError):
        result, why = None, "unreachable"
    _continuum_models_cache[api_key] = (now, result, why)
    return result, why


def _continuum_model_row(m: dict) -> Optional[list]:
    """[continuum/<id>, <name>, <note>] for one listed chat model, or None for one that isn't a chat model."""
    name = (m.get("id") or "").strip() if isinstance(m, dict) else ""
    if not name:
        return None
    if any(t in CONTINUUM_EXCLUDED_MODEL_TOKENS for t in name.lower().replace("_", "-").split("-")):
        return None
    return [f"{CONTINUUM_PREFIX}{name}", name, ""]


async def _continuum_status(*, force: bool = False) -> dict:
    """Continuum's real current state, shared by the dashboard, /v1/models and the Refresh route (the same pattern as _xai_status)."""
    base = {"testable": True, "any": f"{CONTINUUM_PREFIX}MODEL", "live": True, "label": "Models", "empty": ""}
    if not continuum_api_key:
        return {**base, "state": "off", "status": "No key yet", "models": CONTINUUM_STATIC_FALLBACK_MODELS, "testable": False}
    raw, why = await _fetch_continuum_models(continuum_api_key, force=force)
    if raw is None:
        if why == "refused":
            return {**base, "state": "warn", "status": "Continuum didn't accept this key", "models": CONTINUUM_STATIC_FALLBACK_MODELS}
        return {**base, "state": "warn", "status": "Key saved, but couldn't reach Continuum", "models": CONTINUUM_STATIC_FALLBACK_MODELS}
    rows = sorted((row for row in (_continuum_model_row(m) for m in raw) if row), key=lambda r: r[0])
    if not rows:
        return {**base, "state": "warn", "status": "Key saved, but the live list came back empty", "models": CONTINUUM_STATIC_FALLBACK_MODELS}
    n = len(rows)
    return {**base, "state": "ok", "status": f"Key saved: {n} model{'s' if n != 1 else ''} available", "models": rows, "any": None}


_KILL_TREE_WAIT_TIMEOUT = 5.0  # seconds -- bounds both the taskkill subprocess and the final reap


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate `proc` AND every descendant it spawned, then wait (bounded) for it to actually be
    gone. Shared by every CLI-subprocess call site that can time out (Codex, Antigravity) -- a real,
    live-confirmed Windows hazard (2026-09-16), not a defensive guess. The caller must pass
    `start_new_session=True` to `create_subprocess_exec` when spawning `proc` -- that's what makes
    the POSIX branch below able to find and kill the whole group; a plain `proc.kill()` (used
    before this helper existed, and still literally identical to what the non-Windows branch used
    to do here) never reaches a spawned grandchild on ANY OS, Windows or POSIX alike.

    Both the real Codex CLI and the real Antigravity CLI resolve to a multi-level process tree on
    Windows: an npm-installed `.CMD` shim that launches a real worker process (a native binary, in
    Codex's own case) as ITS OWN child. `proc.kill()` (`TerminateProcess`) only ever reaches the
    single, immediate child asyncio itself spawned -- confirmed live via Get-Process during an
    actual hung request: the immediate child was gone, but the real worker process it had spawned
    was still alive, unkilled, minutes later. This has two real consequences, both confirmed live:
    (1) the plain, unbounded `await proc.wait()` that used to sit at every one of these call sites
    hung INDEFINITELY waiting for an exit that already effectively wasn't coming from the tracked
    PID -- a real request against a genuinely unreachable custom Codex provider sat for 12+ minutes
    with no response at all, despite CODEX_TIMEOUT correctly firing at its configured 180s deadline;
    (2) the still-running worker process kept its own working directory pinned inside `_run_codex_
    exec`'s own ephemeral `tempfile.TemporaryDirectory()`, so even after bounding proc.wait() with a
    timeout, Python's own cleanup on that directory failed with `PermissionError: [WinError 32] The
    process cannot access the file because it is being used by another process` -- turning what
    should have been a clean 504 "Codex CLI timed out" into an unhandled 500 instead.

    `taskkill /T /F` is Windows' own built-in RECURSIVE tree-kill (`/T` = kill this PID and every
    descendant, `/F` = force) -- the standard, documented fix for exactly this class of orphaned-
    grandchild problem, and simpler/more robust here than hand-rolling a Job Object.

    **The non-Windows branch used to be a bare `proc.kill()` on the theory that "POSIX process
    groups behave differently" and this risk was Windows-only -- that was an unverified assumption
    sitting right next to a hard-won counterexample, and it was WRONG. Confirmed live on real Linux
    (WSL2, 2026-09-16), both generically and against the real CLI's own source: (a) a synthetic
    "shim spawns a background worker" repro reproduces the EXACT same orphan -- killing only the
    immediate child leaves the worker alive and unkilled; (b) the real `@openai/codex` npm package's
    own `bin/codex.js` (IDENTICAL on Linux/macOS/Windows, confirmed by installing and reading it)
    spawns the real native `codex` binary via plain `child_process.spawn()` -- a genuine, separate
    OS child, not an exec-replacement -- and while it DOES forward SIGINT/SIGTERM/SIGHUP to that
    child so a graceful shutdown propagates, `proc.kill()`'s SIGKILL cannot be caught or forwarded
    by ANY signal handler, so that graceful path is completely bypassed and the native binary is
    orphaned exactly like on Windows. (Antigravity's own CLI, unlike Codex's, ships as a single flat
    native Go binary per its own installer script -- no JS wrapper layer confirmed there, so its own
    orphan risk is unconfirmed either way; the fix below is applied to it anyway since a process
    group of one process behaves identically to a single kill, so this is never worse, only safer.)

    The real, correct POSIX fix is a **process group**, not a repeat of the plain kill this
    replaced: `os.killpg(os.getpgid(pid), SIGKILL)` signals every process in the group at once,
    including any descendant -- but only works when the child was made its own group leader AT
    SPAWN TIME (`start_new_session=True`, which every caller of this helper now passes). Falls back
    to the plain `proc.kill()` this replaced if the process already exited, was never actually made
    its own group leader, or `taskkill`/`os.killpg` themselves raise for any other reason -- some
    cleanup attempt is always better than crashing this already-in-a-timeout-handler code path.
    """
    if os.name == "nt" and proc.pid:
        try:
            kill_proc = await hidden_process.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(kill_proc.wait(), timeout=_KILL_TREE_WAIT_TIMEOUT)
        except Exception:
            proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    with suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=_KILL_TREE_WAIT_TIMEOUT)


# --- Codex (ChatGPT/Codex subscription, via the real Codex CLI as a subprocess) ---

def _codex_flatten_prompt(system_prompt: Optional[str], messages: list) -> str:
    """codex exec takes one plain-text prompt, not a messages array -- flatten the conversation
    the same way skyrimnet-codex-proxy does (a real, already-proven shape for this exact CLI)."""
    parts = []
    if system_prompt:
        parts.append(f"System: {system_prompt}")
    for m in messages:
        role = "User" if m["role"] == "user" else "Assistant"
        # _content_as_text: `codex exec` takes one plain-text prompt with no way to attach an
        # image at all -- a raw f-string here would dump a multimodal content LIST's Python repr
        # straight into the prompt (image_url's own base64 data URL included), instead of just
        # the text part actually usable by a text-only CLI.
        parts.append(f"{role}: {_content_as_text(m['content'])}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


CODEX_LOGIN_TTL = 900.0
CODEX_LOGIN_FAILED_TTL = 600.0


async def _codex_logged_in() -> bool:
    """Fast (~150ms, confirmed live 2026-09-13), local-only precondition check -- avoids running
    a slow, real `codex exec` call just to discover the CLI isn't logged in yet. `codex exec`
    itself doesn't fail with a clean "not logged in" message when unauthenticated (confirmed live:
    it fails with a raw 401 from the private backend after several silent internal reconnect
    attempts), so checking this UP FRONT is the only way to give a clear error instead of a
    confusing one."""
    codex_path = _codex_path()
    if not codex_path:
        return False

    async def ask() -> bool:
        # Starting a program is a synchronous call on Windows (30-135 ms): done on a worker thread, never on the event loop.
        try:
            r = await asyncio.to_thread(hidden_process.run, [codex_path, "login", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        stdin=subprocess.DEVNULL, timeout=10)
            return r.returncode == 0
        except Exception:
            return False
    # The answer is kept 15 minutes when signed in, 10 when not; an old one is handed out while a background look makes the next, and
    # the Refresh button forgets it. Everyone asking at once shares one program start.
    return await poll_cache.shared("codex-login", lambda ok: CODEX_LOGIN_TTL if ok else CODEX_LOGIN_FAILED_TTL, ask, stale_ok=True)


def _codex_provider_slug(name: str, existing_ids: set) -> str:
    """Turn a saved provider's display name into a URL-safe, dot-free id for both the
    `codex/<id>/MODEL` routing shape and the `-c model_providers.<id>.*` CLI overrides.

    Always prefixed 'sn-' (SkyrimNet) so it can never collide with one of Codex's own built-in
    provider ids (CODEX_RESERVED_PROVIDER_IDS) -- a collision would mean Codex's own
    merge_configured_model_providers() silently keeping ITS built-in entry instead of ours.
    Restricted to [a-z0-9-] with no dots -- `-c key=value` treats a dot as a nested-path
    separator, so a dot in the id would split it into extra config levels instead of naming one
    provider.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "provider"
    base = f"sn-{slug}"
    candidate = base
    n = 2
    while candidate in existing_ids or candidate in CODEX_RESERVED_PROVIDER_IDS:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _codex_find_provider(provider_id: str) -> Optional[dict]:
    """Look up a saved Codex provider by id, or None (including for 'default', which is never
    stored here -- see _codex_resolve_provider)."""
    return next((p for p in codex_providers if p["id"] == provider_id), None)


def _codex_resolve_provider(remainder: str) -> tuple:
    """A plain `codex/MODEL` request must keep meaning the default ChatGPT subscription forever
    (every existing test and every player not using this feature depends on that) -- so a saved
    provider only ever gets picked when the FIRST path segment after `codex/` matches one of its
    own ids exactly. Anything else -- including a model id that itself happens to contain '/' --
    falls back to "the whole remainder is the model, use the default provider". This is the same
    specific-match-first, generic-catch-all-last precedence is_unclaimed_slash_model uses to
    decide whether a '/'-containing model belongs to a named provider or to none.

    Returns (provider_dict_or_None, real_model). provider is None for the default provider.
    """
    if "/" in remainder:
        maybe_id, rest = remainder.split("/", 1)
        provider = _codex_find_provider(maybe_id)
        if provider is not None:
            return provider, rest
    return None, remainder


def _codex_provider_cli_args(provider: dict) -> tuple:
    """Build the `-c` overrides that define a saved provider entirely on the `codex exec` command
    line -- confirmed live 2026-09-16 against a real installed Codex CLI (0.154.0) that this needs
    no config.toml ever written and no CODEX_HOME override: a real custom entry (OpenRouter) and a
    real local-daemon preset (Ollama) both worked end-to-end with nothing but repeated `-c` flags.

    wire_api is always "responses" -- confirmed live (and in codex-rs/model-provider-info/src/
    lib.rs) that `wire_api = "chat"` is a hard, permanently-removed error in this CLI version
    (github.com/openai/codex/discussions/7782); "responses" is the only wire format Codex's CLI
    will speak to ANY externally-defined provider, built-in preset or custom alike. OpenRouter's
    own endpoint already answers this shape correctly (live-verified); an older Chat-Completions-
    only gateway will not work through Codex specifically, though it still works fine through this
    dashboard's other providers.

    Returns (extra_argv, env_overrides) -- env_overrides is only ever meant to be layered onto the
    SUBPROCESS's own env (never the proxy's own global environment), so the real key never has to
    live anywhere but a single throwaway env var for the one call that needs it.
    """
    slug = provider["id"]
    args = [
        "-c", f"model_providers.{slug}.name={provider['name']}",
        "-c", f"model_providers.{slug}.base_url={provider['base_url']}",
        "-c", f"model_providers.{slug}.wire_api=responses",
        "-c", f"model_provider={slug}",
    ]
    env_overrides = {}
    api_key = provider.get("api_key")
    if api_key:
        env_var = f"SKYRIMNET_CODEX_{slug.upper().replace('-', '_')}_KEY"
        args += ["-c", f"model_providers.{slug}.env_key={env_var}"]
        env_overrides[env_var] = api_key
    return args, env_overrides


def _codex_models_cache_path() -> str:
    """CODEX_HOME if set (the CLI's own override, confirmed via `codex --help`), else ~/.codex --
    never a hardcoded Windows-only path. Not documented as a public interface by OpenAI; this is an
    internal cache file the CLI happens to keep for its own use (see _fetch_codex_models's own
    docstring for what happens if its shape ever changes)."""
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(codex_home, "models_cache.json")


def _parse_codex_models_cache(raw: dict) -> list:
    """[(slug, display_name, description), ...] for models this account can actually be offered --
    `visibility: "list"` AND `supported_in_api` only (a hidden or non-API model is hidden for a
    reason and shouldn't be put in front of a player), ordered by the cache's own `priority` field.

    Real shape confirmed live 2026-09-16 against a real ~/.codex/models_cache.json
    (client_version 0.154.0): five entries -- `gpt-5.6-terra`, `gpt-5.6-luna`, and `gpt-5.5` with
    visibility "list" (all three also supported_in_api, all offered), plus `gpt-reserve` and
    `codex-auto-review` with visibility "hide" (correctly excluded despite also being
    supported_in_api -- visibility is the real gate here, supported_in_api is a second,
    currently-redundant safety net for a future entry that might set it False while still marked
    "list").

    Raises ValueError if `raw` isn't shaped as expected (no "models" list) -- the caller treats
    that exactly like a missing/unreadable file: fall back to CODEX_FALLBACK_MODELS rather than
    crash the dashboard over an internal, undocumented cache file OpenAI could restructure at any
    point without warning."""
    models = raw.get("models")
    if not isinstance(models, list):
        raise ValueError("models_cache.json has no 'models' list")
    rows = []
    for m in models:
        if not isinstance(m, dict):
            continue
        if m.get("visibility") != "list" or not m.get("supported_in_api"):
            continue
        slug = m.get("slug")
        if not slug:
            continue
        rows.append((m.get("priority") if isinstance(m.get("priority"), (int, float)) else 0, slug, m.get("display_name") or slug, m.get("description") or ""))
    rows.sort(key=lambda r: r[0])
    return [(slug, name, desc) for _, slug, name, desc in rows]


async def _fetch_codex_models(*, force: bool = False) -> Optional[list]:
    """Reads the Codex CLI's own real, account-specific model list from its on-disk cache. There is
    **no** `codex models` subcommand -- confirmed live 2026-09-16 against both `codex --help` and
    `codex exec --help`: `-m`/`--model` takes a free-form string with nothing to validate it
    against. The CLI keeps its real answer at CODEX_HOME (or ~/.codex)/models_cache.json instead,
    refreshed by the CLI itself on its own schedule -- this only ever reads it, never writes or
    refreshes it.

    Returns None if the file is missing, unreadable, or its shape isn't what's expected -- callers
    fall back to CODEX_FALLBACK_MODELS and say so, the same "never silently show an empty/wrong
    list" convention _fetch_ollama_tags/_fetch_agy_models/_fetch_gemini_models already use. A
    brand-new Codex install that has never run won't have this file at all -- an expected, disclosed
    case, not an error to log loudly about.

    Cached for CODEX_MODELS_CACHE_TTL (`force=True`, the dashboard's own Refresh button, bypasses
    it) purely to avoid re-reading disk on every dashboard render within the same few seconds --
    this file changes only when the CLI itself refreshes it, not on any timescale a cache could ever
    serve stale for in a way that matters."""
    global _codex_models_cache
    now = time.time()
    cached_at, cached_value = _codex_models_cache
    if not force and now - cached_at < CODEX_MODELS_CACHE_TTL:
        return cached_value

    result = None
    try:
        with open(_codex_models_cache_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = _parse_codex_models_cache(raw) or None
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        result = None

    _codex_models_cache = (now, result)
    return result


def _codex_pinned_rows(provider_id: str) -> list:
    """Pinned models (2026-09-16) render as ordinary rows appended after this provider's own real/
    curated list -- purple text + an Unpin button (the 4th tuple element, checked client-side) is
    the only "this is pinned" signal, no separate badge/pill (an earlier draft had one; trimmed
    since it'd be redundant with the color and the Unpin button already only appearing here).

    The full model id embeds the provider so a pinned row is always directly copy/testable: plain
    `codex/MODEL` for the default provider (unchanged shape), `codex/<provider-id>/MODEL` for a
    saved one -- exactly what _codex_resolve_provider already expects.
    """
    prefix = CODEX_PREFIX if provider_id == "default" else f"{CODEX_PREFIX}{provider_id}/"
    return [
        # The 4th element carries the RAW (un-prefixed) pinned id -- truthy, so it doubles as the
        # "this row is pinned" flag, and lets the dashboard's Unpin button send back exactly what
        # was pinned without having to re-parse it out of the full, prefixed `mid` string.
        [f"{prefix}{entry['id']}", entry.get("name") or entry["id"], "", entry["id"]]
        for entry in codex_pinned_models.get(provider_id, [])
    ]


async def _codex_status(*, force: bool = False) -> dict:
    """Thin wrapper around _codex_status_impl that merges the default provider's own pinned models
    into the real/fallback list -- kept separate so every existing return statement in the real
    status logic below stays untouched."""
    status = await _codex_status_impl(force=force)
    status["models"] = status["models"] + _codex_pinned_rows("default")
    return status


def _codex_install_card() -> dict:
    """The install part of the ChatGPT card (codex_install.py): shown while Codex is not installed, is installing, or the last install failed."""
    return _codex_installer.card(bool(_codex_path()), codex_install.signed_in_file_exists())


async def _codex_status_impl(*, force: bool = False) -> dict:
    """The one place deciding Codex's real current state -- shared by _dashboard_providers(),
    /v1/models, and GET /config/codex-models (the Refresh route), same pattern
    _ollama_status()/_gemini_status()/_agy_status() already establish.

    Login state and model-list availability are independent axes here (unlike Antigravity, where a
    failed models call already implies "not usable"): a real discovered list can exist from a
    previous login even if the CLI isn't currently logged in (informational), so `testable` tracks
    login state specifically while `models`/`state` track whether the real list could be read --
    the fallback applies whenever the file can't be read, not only when logged out (a brand-new,
    never-run install has neither yet, which is exactly why both checks matter independently)."""
    if force:                         # the dashboard's own Refresh button: look again at the program and the sign-in
        poll_cache.forget("codex-login")
        poll_cache.forget_which("codex")
    install_card = _codex_install_card()
    if not _codex_path():
        return {
            "state": "off", "status": "Installing" if _codex_installer.running() else "Not installed", "models": [], "testable": False,
            "any": None, "live": True, "label": "Models",
            "empty": install_card["line"], "installCard": install_card,
        }

    logged_in = await _codex_logged_in()
    login_empty = "" if logged_in else "Sign in to Codex on your device, then press Check for models."
    install_card = install_card if install_card["show"] else None

    rows = await _fetch_codex_models(force=force)
    if rows is None:
        fallback_n = len(CODEX_FALLBACK_MODELS)
        return {
            "state": "ok" if logged_in else "warn",
            "status": f"Ready: {fallback_n} default model{'s' if fallback_n != 1 else ''}" if logged_in else "Not signed in",
            "models": CODEX_FALLBACK_MODELS, "testable": logged_in,
            "any": f"{CODEX_PREFIX}MODEL", "live": True,
            "label": "Models", "empty": login_empty, "installCard": install_card,
        }

    model_rows = [[f"{CODEX_PREFIX}{slug}", name, desc] for slug, name, desc in rows]
    n = len(model_rows)
    return {
        "state": "ok" if logged_in else "warn",
        "status": f"Ready: {n} model{'s' if n != 1 else ''} available" if logged_in else "Not signed in",
        "models": model_rows, "testable": logged_in, "any": None, "live": True,
        "label": "Models", "empty": login_empty, "installCard": install_card,
    }


async def _run_codex_exec(prompt: str, model: str, request_id: str, provider: Optional[dict] = None) -> str:
    """Spawns the real Codex CLI non-interactively and returns its final plain-text reply.

    `provider=None` (the default ChatGPT/Codex subscription) is byte-for-byte the same invocation
    as before this feature existed -- no extra argv, no extra env -- so a plain `codex/MODEL`
    request can never regress. A saved provider adds its own `-c model_providers.*` overrides (see
    _codex_provider_cli_args) and, only if it has a key, that key as a single throwaway env var on
    THIS subprocess's own env -- never the proxy's global environment, never a file on disk.

    Deliberately a subprocess per call, not a direct HTTP integration -- confirmed live 2026-09-13
    (both by reading OpenAI's own open-source `codex-rs` client code AND by a live test run
    against a real installed Codex CLI) that `codex exec` talks to a private, stateful,
    Responses-API-shaped backend with several Codex-specific custom headers (turn-state,
    installation ID, routing hints) this project can't reliably reproduce or keep in sync with as
    OpenAI evolves it. Shelling out to the real, officially-maintained CLI means OpenAI changing
    that private protocol is Codex's own maintainers' problem, not ours -- the same tradeoff
    skyrimnet-codex-proxy already made, and confirmed the more robust of the two real options
    (the alternative, direct-HTTP reverse-engineering, was tried first this session and abandoned
    for exactly this reason).

    Runs in a fresh, throwaway temp dir (--ephemeral, --skip-git-repo-check, --ignore-user-config)
    so this never touches the user's own Codex session history or picks up unrelated project
    context (e.g. an AGENTS.md in whatever directory happened to be cwd) -- the same "clean
    environment" precaution capture_auth() already takes for the Claude CLI. --sandbox read-only
    is the most restrictive built-in sandbox, since this is only ever used for plain dialogue
    generation, never a coding task that should be allowed to touch the filesystem.

    The prompt is fed over stdin (`-` as the positional arg), not as a CLI argument -- a long
    SkyrimNet system prompt could plausibly exceed a safe command-line length, and stdin has no
    such limit. `-o` writes ONLY the final agent message to a plain text file -- confirmed live
    this is far more robust than scraping the JSONL event stream for a specific event/role name
    that could change between Codex CLI versions.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = os.path.join(tmpdir, "last_message.txt")
        extra_args: list = []
        subprocess_env = None  # None -> inherits the current process env exactly as before
        if provider is not None:
            extra_args, env_overrides = _codex_provider_cli_args(provider)
            if env_overrides:
                subprocess_env = {**os.environ, **env_overrides}
        proc = await hidden_process.create_subprocess_exec(
            _codex_path(), "exec",
            "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
            "--sandbox", "read-only",
            *extra_args,
            "-C", tmpdir,
            "-m", model,
            "-o", out_file,
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmpdir,
            env=subprocess_env,
            start_new_session=True,  # required by _kill_process_tree's own POSIX branch -- see its docstring
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=CODEX_TIMEOUT)
        except asyncio.CancelledError:
            # SkyrimNet hung up (its first-token guard drops whichever request loses the race), so
            # nobody is waiting for this answer any more -- don't leave the CLI running for it.
            await _kill_process_tree(proc)
            raise
        except asyncio.TimeoutError:
            # See _kill_process_tree's own docstring -- a plain proc.kill() only reaches the
            # immediate child of a multi-level CLI process tree, real-world-confirmed to leave this
            # request hanging 12+ minutes past CODEX_TIMEOUT's own 180s deadline.
            await _kill_process_tree(proc)
            raise HTTPException(status_code=504, detail="Codex took too long to answer. Make sure it is running and try again.")

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            combined = (err_text or out_text)[:500]
            logger.error(f"[{request_id}] Codex CLI failed rc={proc.returncode}: {combined}")
            raise HTTPException(status_code=502, detail=f"Codex failed: {combined}")

        if not os.path.exists(out_file):
            raise HTTPException(status_code=502, detail="Codex sent back no answer.")

        with open(out_file, "r", encoding="utf-8") as f:
            return f.read().strip()


async def call_codex_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    """Real Codex CLI call, collects the full response (non-streaming to caller). No real token
    usage available from CLI text output -- the dispatcher's own char/4 estimate fallback covers
    this the same way it already does for any provider that doesn't supply real counts.

    `model` here is whatever's left after strip_codex_prefix strips only the leading `codex/` --
    _codex_resolve_provider decides whether its first path segment names a saved provider
    (codex/<provider-id>/MODEL) or the whole thing is a model for the default ChatGPT subscription
    (plain codex/MODEL, unchanged). A saved provider uses its OWN key/local daemon, never the
    ChatGPT login, so the _codex_logged_in() precondition only applies to the default provider.
    """
    if not _codex_path():
        raise HTTPException(status_code=500, detail="Codex isn't installed. Check Help to see how to install it.")
    provider, real_model = _codex_resolve_provider(model)
    if provider is None and not await _codex_logged_in():
        raise HTTPException(status_code=401, detail="You are not signed in to Codex. Sign in, then try again.")

    prompt = _codex_flatten_prompt(system_prompt, messages)
    request_id = uuid.uuid4().hex[:8]
    provider_label = f", provider={provider['id']}" if provider else ""
    logger.info(f"[{request_id}] -> Codex {real_model} ({len(messages)} msgs{provider_label})")
    start = time.time()
    text = await _run_codex_exec(prompt, real_model, request_id, provider)
    elapsed = time.time() - start
    logger.info(f"[{request_id}] <- {len(text)} chars ({elapsed:.1f}s, Codex)")
    return text, {}


async def call_codex_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    """Codex has no cheap way to stream real incremental tokens from a subprocess CLI call -- runs
    the same real call to completion, then emits the whole reply as one SSE chunk. Honest about
    this rather than faking token-by-token streaming this architecture can't actually back (same
    principle as never drawing a progress bar with no real data behind it)."""
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    if not _codex_path():
        raise HTTPException(status_code=500, detail="Codex isn't installed. Check Help to see how to install it.")
    provider, real_model = _codex_resolve_provider(model)
    if provider is None and not await _codex_logged_in():
        raise HTTPException(status_code=401, detail="You are not signed in to Codex. Sign in, then try again.")

    prompt = _codex_flatten_prompt(system_prompt, messages)
    request_id = uuid.uuid4().hex[:8]
    provider_label = f", provider={provider['id']}" if provider else ""
    logger.info(f"[{request_id}] -> Codex {real_model} ({len(messages)} msgs, stream{provider_label})")
    start = time.time()
    try:
        text = await _run_codex_exec(prompt, real_model, request_id, provider)
    except HTTPException:
        raise  # nothing has been sent yet, so this becomes a real HTTP error (see _prime_stream)
    except Exception:
        # Defense in depth: any other subprocess/decode surprise from _run_codex_exec still
        # becomes a proper error response rather than a truncated stream.
        logger.exception(f"[{request_id}] Unexpected error in Codex streaming")
        raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")

    role_chunk = {
        "id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(role_chunk)}\n\n"
    stop_chunk = {
        "id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"
    elapsed = time.time() - start
    logger.info(f"[{request_id}] <- {len(text)} chars ({elapsed:.1f}s, Codex, streamed as one chunk)")


# --- Antigravity CLI (Google AI Pro/Ultra subscription, via the real `agy` CLI as a subprocess) ---

_AGY_IMAGE_DATA_URL_RE = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", re.S)


def _extract_image_parts(content) -> list[tuple[str, bytes]]:
    """Pulls every image_url part's real bytes + file extension out of a message's content (the
    OpenAI-style multimodal shape ChatMessage.content documents: [{"type": "text", ...},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}] -- confirmed live
    2026-09-29 this is exactly what SkyrimNet's own Vision/OmniSight screenshot requests send).
    [] for a plain string or a content list with no image parts. A malformed data URL (no comma,
    bad base64) is skipped rather than raised -- one unparsable image must never break an
    otherwise-fine request."""
    if not isinstance(content, list):
        return []
    images = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            continue
        url = (part.get("image_url") or {}).get("url", "")
        m = _AGY_IMAGE_DATA_URL_RE.match(url)
        if not m:
            continue
        ext, b64 = m.group(1), m.group(2)
        try:
            images.append((ext, base64.b64decode(b64, validate=True)))
        except (ValueError, base64.binascii.Error):
            continue
    return images


def _agy_flatten_prompt(system_prompt: Optional[str], messages: list, image_dir: Optional[str] = None) -> str:
    """`agy -p` takes one plain-text prompt, not a messages array, and (confirmed against its own
    docs) has no dedicated system-prompt flag -- same shape as _codex_flatten_prompt above, kept as
    its own function rather than shared since the two CLIs' real interfaces could diverge later and
    each provider here already keeps its own copy of conceptually-similar per-provider glue (see
    e.g. KILO_BLOCKED_PARAMS/OLLAMA_BLOCKED_PARAMS/GEMINI_BLOCKED_PARAMS, three separate constants
    for a similar idea).

    Vision (queue: agy-vision, 2026-09-29) -- `agy`'s own stream-json input has no inline image
    content block at all (confirmed live: "stream input content block type \"image\" is not
    supported (only \"text\")"), so an image_url part can't travel the way it does to the OpenAI-
    compatible providers. What DOES work, confirmed live against a real signed-in account: `agy` is
    a full agentic CLI with its own `view_file` tool (visible in its init event's tool list) --
    telling it, in plain text, to look at a real image FILE by path makes it call that tool itself
    and it genuinely sees the image (proven with a real test picture: asked for the background
    color and any text in it, got both right, including a word that couldn't have been guessed).
    `--add-dir` turned out to be unnecessary in that same live test -- the sandbox already allows
    `view_file` on anything inside the process's own cwd, which is exactly `image_dir` here (the
    caller's own per-call tempdir, already guaranteed cleaned up after -- see call_geminicli_direct/
    call_geminicli_streaming's own `with tempfile.TemporaryDirectory()` block). `image_dir` is
    `None` for callers that haven't been updated for this yet, or when there is genuinely nothing to
    write -- both leave every existing text-only call byte-for-byte unchanged."""
    parts = []
    if system_prompt:
        parts.append(f"System: {system_prompt}")
    # A single counter for the whole prompt, not reset per message -- a real bug caught by this
    # feature's own test suite: resetting it per-message named every message's own first image
    # "screenshot_0.<ext>", so a second message's screenshot silently overwrote the first one's
    # file on disk (both messages' notes still pointed at "screenshot_0", but only the last write
    # survived) the instant more than one message in the same request carried an image.
    image_index = 0
    for m in messages:
        role = "User" if m["role"] == "user" else "Assistant"
        text = _content_as_text(m["content"])
        images = _extract_image_parts(m["content"]) if image_dir else []
        if images:
            paths = []
            for ext, data in images:
                path = os.path.join(image_dir, f"screenshot_{image_index}.{ext or 'png'}")
                image_index += 1
                with open(path, "wb") as f:
                    f.write(data)
                paths.append(path)
            noun = "a screenshot" if len(paths) == 1 else f"{len(paths)} screenshots"
            pronoun = "it" if len(paths) == 1 else "them"
            text = f"{text}\n[This message included {noun}, saved at: {', '.join(paths)}. Use your view_file tool to look at {pronoun} before replying.]"
        parts.append(f"{role}: {text}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


async def _run_agy_json(prompt: str, model: str, request_id: str, tmpdir: str) -> tuple[str, dict]:
    """Runs Antigravity CLI to completion and returns (reply text, usage dict) from its final
    `result` event -- confirmed live 2026-09-15 against a real Pro-authenticated account, e.g.:
    `{"status":"SUCCESS","response":"...","usage":{"input_tokens":23,"output_tokens":59,...}}`.

    A thin consumer of _stream_agy_events (see its own docstring for the full subprocess/argv/
    stdin/isolation reasoning, which now applies here unchanged): this used to spawn its own
    separate `--output-format json` subprocess, but that transport has no stdin-input mode, so it
    could only take the prompt as a `-p` argument -- the exact command-line-length exposure fixed
    2026-09-16 (see _stream_agy_events). Consuming the shared stream-json generator instead means
    this and the real streaming path can never drift back out of sync on that fix. The intermediate
    step_update events are ignored here; only the final `result` event matters for a non-streaming
    caller.

    `tmpdir` (queue: agy-vision, 2026-09-29) -- the caller's own per-call tempdir (see
    call_geminicli_direct's own `with tempfile.TemporaryDirectory()`), passed through unchanged so
    the subprocess's cwd is the SAME directory _agy_flatten_prompt may have already written a
    decoded screenshot into. Ownership of creating/cleaning it up moved to the caller precisely so
    that write can happen before the prompt is even built -- see _stream_agy_events's own docstring
    for the rest of this story."""
    result = None
    async for event in _stream_agy_events(prompt, model, request_id, tmpdir):
        if event.get("event") == "result":
            result = event.get("result") or {}

    if result is None:
        raise HTTPException(status_code=502, detail="Antigravity sent back no answer.")

    # Real, live-found quirk (2026-09-15): a denied tool attempt still reports status
    # "SUCCESS" with an EMPTY response, rather than a distinct failure status -- so an empty
    # reply is possible even on the "success" path. Not treated as an error here (SkyrimNet
    # just gets an empty line rather than a crash, the same "degrade, don't crash" choice the
    # Gemini API-key provider's own content-key fix already made) -- only a genuinely
    # non-SUCCESS status is raised as a real error.
    if result.get("status") != "SUCCESS":
        raise HTTPException(
            status_code=502,
            detail=f"Antigravity CLI: {result.get('error') or result.get('status') or 'unknown failure'}",
        )

    text = result.get("response") or ""
    real_usage = result.get("usage") or {}
    usage = {"input_tokens": real_usage.get("input_tokens"), "output_tokens": real_usage.get("output_tokens")}
    return text, usage


async def _stream_agy_events(prompt: str, model: str, request_id: str, tmpdir: str):
    """Spawns Antigravity CLI with --input-format/--output-format stream-json and yields each
    parsed NDJSON event as it arrives -- real shape confirmed live 2026-09-15: one `init` event,
    then a `step_update` per step (a `step_type: "user_input"` echo, then one or more
    `step_type: "agent_response"` events carrying incremental `text_delta` fragments -- NOT
    cumulative, each is a new fragment to append, confirmed by concatenating a real captured
    sequence and matching it byte-for-byte against the final `result.response`), and exactly one
    final `result` event.

    The prompt travels over STDIN, never as a `-p` command-line argument (fixed 2026-09-16, real
    bug hit in production on v2.4.0). Windows' CreateProcessW caps a whole command line at 32,767
    characters, confirmed live: a 33,000-char prompt as a `-p` argument raised
    `FileNotFoundError: [WinError 206] The filename or extension is too long` -- not an
    HTTPException, so it escaped straight through call_geminicli_streaming's `except HTTPException`
    and crashed the ASGI response mid-stream. A routine SkyrimNet prompt (system prompt + character
    bio + memories + history) routinely exceeds 32 KB, which is exactly why short test prompts never
    caught this.

    `--input-format text` (the default) does NOT read the prompt from stdin, even with `-p` given
    an explicit empty value (`-p=`) -- confirmed live, it reports "Error: empty prompt." instead.
    Stdin input requires `--input-format stream-json`, which the CLI's own `--help` says requires
    `--output-format stream-json` too (also confirmed live: pairing stream-json input with
    `--output-format json` is flatly rejected). `-p=` (empty value attached, not bare `-p`) is still
    required to enter print mode at all -- confirmed live, `-p` bare or omitted never starts a
    non-interactive run.

    The stdin message shape was reverse-engineered live against the real CLI (2026-09-16) by
    sending progressively more-complete guesses and reading its own validation errors:
      `{}`                                        -> `stream input message is missing the "event" field`
      `{"event": "user_input", ...}`               -> `warning: ignoring unsupported stream input message event "user_input"`
      `{"event": "user", "message": "..."}`        -> `json: cannot unmarshal string into ... printmode.streamInputUserMessage`
      `{"event": "user", "message": {}}`           -> `stream input "user" message has no content`
      `{"event": "user", "message": {"content": "hello"}}` -> a real, successful streamed turn
    So the confirmed, working shape sent to stdin -- one line, newline-terminated, then stdin
    closed -- is:
      {"event": "user", "message": {"content": "<prompt>"}}

    Also passes `limit=AGY_STDOUT_LIMIT` to create_subprocess_exec (forwarded to the underlying
    StreamReader) -- asyncio's own default readline() buffer is 64 KiB, and the CLI echoes the
    whole prompt back as a single `step_type: "user_input"` NDJSON line, so a large prompt produces
    a single large line even with the stdin fix above. Confirmed live: a ~70,000-byte single line
    raises `ValueError: Separator is found, but chunk is longer than limit` on the default buffer.

    Same isolation/safety reasoning as before (fresh tmpdir cwd, --sandbox,
    --disable-slash-commands, no --dangerously-skip-permissions). _run_agy_json used to spawn its
    own separate `--output-format json` subprocess rather than share this function's plumbing,
    since the two needed different reading strategies (communicate() once vs. incremental
    readline()) -- now that the stdin fix above forces BOTH callers onto the same stream-json
    transport, _run_agy_json is a thin consumer of this generator instead (see its own docstring).

    Raises HTTPException on a missing/timed-out/non-zero-exit process; a caller that wants any
    partial output already yielded before a failure (call_geminicli_streaming) must catch this
    itself, same convention _run_codex_exec's own callers already follow for Codex.

    `tmpdir` (queue: agy-vision, 2026-09-29) -- used to create its own tempdir every call; now
    takes the caller's, so a screenshot _agy_flatten_prompt already decoded into it (before this
    function ever runs, since the prompt has to name the real path) sits in the same place this
    subprocess's cwd points at, letting the CLI's own `view_file` tool reach it with no `--add-dir`
    needed (confirmed live: the sandbox already permits reading anything under its own cwd). The
    caller owns the `with tempfile.TemporaryDirectory()` block now, so cleanup -- including any
    screenshot file inside it -- is still automatic and still runs on every exit path (a normal
    finish, a timeout, or a crash), exactly as before; only who calls TemporaryDirectory() moved.
    """
    proc = await hidden_process.create_subprocess_exec(
        _agy_path(),
        "-p=",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", model,
        "--sandbox",
        "--disable-slash-commands",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=tmpdir,
        limit=AGY_STDOUT_LIMIT,
        start_new_session=True,  # required by _kill_process_tree's own POSIX branch -- see its docstring
    )
    stdin_payload = json.dumps({"event": "user", "message": {"content": prompt}}).encode("utf-8") + b"\n"

    async def _write_stdin():
        # Fire-and-forget, launched concurrently with the stdout read loop below rather than
        # awaited up front -- a prompt large enough to exceed the OS pipe buffer would otherwise
        # risk this write blocking before the CLI has even started reading its own stdin, the
        # same deadlock class _drain_stderr below exists to avoid on the stderr side. Wrapped in
        # suppress since a write racing a process that already died (or exited early on bad
        # args) is not this function's problem to report -- the exit-code/timeout checks below
        # already cover a genuinely failed run.
        with suppress(Exception):
            proc.stdin.write(stdin_payload)
            await proc.stdin.drain()
            proc.stdin.close()

    stdin_task = asyncio.create_task(_write_stdin())
    stderr_buf = bytearray()

    async def _drain_stderr():
        # Reads stderr concurrently with the stdout loop below -- without this, a subprocess
        # whose stderr pipe fills up while nothing is reading it can deadlock waiting for a
        # reader that only shows up after stdout is exhausted. communicate() (used by
        # _run_agy_json above) handles this internally; a manual incremental-read loop like
        # this one has to do it explicitly.
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            stderr_buf.extend(chunk)

    drain_task = asyncio.create_task(_drain_stderr())
    deadline = time.time() + AGY_TIMEOUT
    timed_out = False
    abandoned = False  # SkyrimNet hung up: the reader was cancelled or the stream closed early
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                continue
    except (asyncio.CancelledError, GeneratorExit):
        abandoned = True
        raise
    finally:
        if timed_out or abandoned:
            # See _kill_process_tree's own docstring -- a plain proc.kill() only reaches the
            # immediate child of a multi-level CLI process tree (agy's own npm-style launcher
            # wrapping its real worker binary), real-world-confirmed via Codex's own identical
            # pattern to leave this cleanup step hanging 12+ minutes past AGY_TIMEOUT's own
            # deadline even though the timeout itself fires correctly on schedule.
            await _kill_process_tree(proc)
            drain_task.cancel()
            stdin_task.cancel()
            # CancelledError is a BaseException (since Python 3.8), not an Exception -- a bare
            # suppress(Exception) here let it propagate and fail the whole call even on THIS,
            # deliberately-cancelled path (caught live while writing this test suite).
            with suppress(Exception, asyncio.CancelledError):
                await drain_task
            with suppress(Exception, asyncio.CancelledError):
                await stdin_task
        else:
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            # The process has already exited normally, so its stderr pipe will reach EOF on its
            # own almost immediately -- await the drain task rather than cancelling it, so a
            # real non-zero-exit error message actually gets captured instead of being lost to
            # a race between "stdout hit EOF" and "the drain task's next read call happened to
            # run yet" (a real bug here, caught live: cancelling unconditionally intermittently
            # produced an empty error message on a real CLI failure).
            with suppress(Exception):
                await asyncio.wait_for(drain_task, timeout=5)
            with suppress(Exception):
                await asyncio.wait_for(stdin_task, timeout=5)

    if timed_out:
        raise HTTPException(status_code=504, detail="Google Antigravity took too long to answer. Make sure it is running and try again.")

    if proc.returncode != 0:
        err_text = bytes(stderr_buf).decode("utf-8", errors="replace").strip()
        logger.error(f"[{request_id}] Antigravity CLI failed rc={proc.returncode}: {err_text[:500]}")
        raise HTTPException(status_code=502, detail=f"Antigravity failed: {err_text[:500] or 'no error output'}")


def _agy_image_count(messages: list) -> int:
    """How many image_url parts are anywhere in this message list -- just for the log line (queue:
    agy-vision, 2026-09-29). The real per-message extraction/decoding happens inside
    _agy_flatten_prompt itself; this is a cheap, separate count so the two call_geminicli_* log
    lines can say "N screenshots" without flattening the prompt twice."""
    return sum(len(_extract_image_parts(m["content"])) for m in messages)


async def call_geminicli_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    if not _agy_path():
        raise HTTPException(
            status_code=500,
            detail="Google Antigravity is not installed. Check Help for how to install it.",
        )
    request_id = uuid.uuid4().hex[:8]
    n_images = _agy_image_count(messages)
    image_note = f", {n_images} screenshot{'s' if n_images != 1 else ''}" if n_images else ""
    logger.info(f"[{request_id}] -> Antigravity CLI {model} ({len(messages)} msgs{image_note})")
    start = time.time()
    # Vision (queue: agy-vision, 2026-09-29) -- owns the tempdir now (used to be _stream_agy_events'
    # own), created before the prompt is flattened so a real screenshot can be decoded into it and
    # its real path named in the prompt text itself. See _agy_flatten_prompt's own header comment
    # for the full "why a file, not inline content" story. Always cleaned up on the way out --
    # normal return, an exception, or a timeout -- same guarantee `with TemporaryDirectory()`
    # already gave when it lived inside _stream_agy_events, just owned here instead.
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = _agy_flatten_prompt(system_prompt, messages, image_dir=tmpdir)
        text, usage = await _run_agy_json(prompt, model, request_id, tmpdir)
    elapsed = time.time() - start
    logger.info(f"[{request_id}] <- {len(text)} chars ({elapsed:.1f}s, Antigravity CLI)")
    return text, usage


async def call_geminicli_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    """Real, live-verified incremental streaming (2026-09-15) -- unlike call_codex_streaming above
    (which fakes streaming by running the whole call then emitting one chunk, honestly disclosed
    since Codex has no cheap way to do better), Antigravity's own --output-format stream-json
    genuinely emits token-by-token text_delta fragments as the model generates them, translated
    here into real OpenAI-shaped chat.completion.chunk SSE events as each one arrives."""
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    if not _agy_path():
        raise HTTPException(status_code=500, detail="Google Antigravity is not installed. Check Help for how to install it.")

    request_id = uuid.uuid4().hex[:8]
    n_images = _agy_image_count(messages)
    image_note = f", {n_images} screenshot{'s' if n_images != 1 else ''}" if n_images else ""
    logger.info(f"[{request_id}] -> Antigravity CLI {model} ({len(messages)} msgs, stream{image_note})")
    start = time.time()
    chars = 0
    # Vision (queue: agy-vision, 2026-09-29) -- same tempdir-ownership move as call_geminicli_direct
    # above; see its own comment. Has to stay open for the whole generator (across every `yield`
    # below), not just this setup -- `with` blocks inside a generator function already do exactly
    # that (this file's own _stream_agy_events proved the pattern safe before this change moved the
    # TemporaryDirectory() call up a level).
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt = _agy_flatten_prompt(system_prompt, messages, image_dir=tmpdir)
        events = _stream_agy_events(prompt, model, request_id, tmpdir)
        try:
            async for event in events:
                if event.get("event") != "step_update":
                    continue
                step = event.get("step_update") or {}
                if step.get("step_type") != "agent_response":
                    continue
                delta = step.get("text_delta")
                if not delta:
                    continue
                chars += len(delta)
                chunk = {
                    "id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
        except HTTPException:
            raise  # before any text: a real HTTP error; after some: a clean end (see _guarded_events)
        except Exception:
            # Defense in depth: an exception here escapes AFTER FastAPI has already sent SSE headers,
            # so the client sees a truncated body (curl "Transferred a partial file"), not a clean
            # error -- the worst possible failure for the player, since SkyrimNet just shows a raw curl
            # error and the NPC stops talking mid-sentence with no explanation. Two real, unhandled
            # examples hit in production before this existed: a `FileNotFoundError` from Windows'
            # command-line length limit, and a `ValueError` from asyncio's default readline() buffer --
            # neither is an HTTPException, so both used to sail straight past the branch above and crash
            # the ASGI response. Logged with its traceback since "some unexpected exception happened" is
            # not actionable without one; the player still gets a readable error instead of a crash.
            logger.exception(f"[{request_id}] Unexpected error in Antigravity CLI streaming")
            raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")
        finally:
            # Closing the CLI reader explicitly is what kills the child process when SkyrimNet hangs up
            # mid-reply (its own `finally` does the killing); otherwise it'd wait for garbage collection.
            await _aclose_quietly(events)

    stop_chunk = {
        "id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"
    elapsed = time.time() - start
    logger.info(f"[{request_id}] <- {chars} chars ({elapsed:.1f}s, Antigravity CLI, streamed live)")


# --- Antigravity CLI model discovery ---
# Unlike Codex's own static, hand-typed model list (no live account was available to check against
# when that was built), a real Pro-authenticated account WAS available here -- `agy models` is a
# real command that lists exactly what the signed-in account can run, confirmed live 2026-09-15:
# 14 real model slugs spanning three brands under one subscription (Gemini Flash/Pro, Claude
# Sonnet/Opus, and GPT-OSS -- Antigravity is a multi-model harness, not Gemini-only, even though
# this route's own prefix says "geminicli"). Live discovery is used instead of hardcoding that list,
# since it's a newly-launched product Google could still be actively changing.

_cli_models_inflight: dict = {}


async def _cli_models_once(name: str, do):
    """One look at a model-list program at a time per `name`: everyone who asks while it runs waits for the same answer."""
    loop = asyncio.get_running_loop()
    task = _cli_models_inflight.get(name)
    if task is None or task.done() or task.get_loop() is not loop:
        task = asyncio.ensure_future(do())
        _cli_models_inflight[name] = task
        task.add_done_callback(lambda t, n=name: _cli_models_inflight.pop(n, None) if _cli_models_inflight.get(n) is t else None)
    return await asyncio.shield(task)


def _cli_models_look_later(name: str, do) -> None:
    """A stale list was handed out: make the next one in the background (never on the way to an answer)."""
    task = _cli_models_inflight.get(name)
    if task is not None and not task.done():
        return

    async def go():
        with suppress(Exception):
            await _cli_models_once(name, do)
    asyncio.ensure_future(go())


def _parse_agy_models_output(raw: str) -> list:
    """Parses `agy models`' own real output shape: a first progress line ("Fetching available
    models...", no tab) followed by one tab-separated `slug\\tdisplay name` line per model --
    confirmed live 2026-09-15. Returns a list of (slug, display_name) tuples, skipping any line
    without a tab (the progress line, or a stray blank)."""
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        slug, _, name = line.partition("\t")
        slug, name = slug.strip(), name.strip()
        if slug:
            rows.append((slug, name or slug))
    return rows


async def _fetch_agy_models(*, force: bool = False) -> Optional[list]:
    """Real `agy models` call -- returns a list of (slug, display_name) tuples, or None if the CLI
    is missing, not signed in, or the call fails/times out (callers treat None as "can't tell you
    what's runnable right now," same convention _fetch_ollama_tags/_fetch_gemini_models use).
    Cached module-globally (one CLI session/account, unlike per-key Gemini caching) for
    AGY_MODELS_CACHE_TTL; a list past that is still handed out while a background look makes the next one.
    `force=True` (the dashboard's own Refresh button) always looks right away."""
    if not _agy_path():
        return None
    cached_at, cached_value = _agy_models_cache
    if not force and cached_at:
        if time.time() - cached_at >= AGY_MODELS_CACHE_TTL:
            _cli_models_look_later("agy", _look_agy_models)
        return cached_value
    return await _cli_models_once("agy", _look_agy_models)


async def _look_agy_models() -> Optional[list]:
    global _agy_models_cache
    now = time.time()
    result = None
    try:
        # A worker thread, not the event loop: starting a program is a synchronous call on Windows (28-343 ms freezes, perf check 2026-10-02).
        proc = await asyncio.to_thread(hidden_process.run, [_agy_path(), "models"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       stdin=subprocess.DEVNULL, timeout=AGY_MODELS_TIMEOUT,
                                       # Asks Antigravity not to start its background updater for this look (from an unofficial guide; harmless if unknown).
                                       env={**os.environ, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"})
        if proc.returncode == 0:
            rows = _parse_agy_models_output((proc.stdout or b"").decode("utf-8", errors="replace"))
            result = rows or None
    except (asyncio.TimeoutError, subprocess.TimeoutExpired, OSError):
        result = None

    # A failed look is remembered for less time than a good one (but still minutes, so a page poll never restarts the program).
    _agy_models_cache = (now if result is not None else now - (AGY_MODELS_CACHE_TTL - AGY_MODELS_FAILED_TTL), result)
    return result


async def _agy_status(*, force: bool = False) -> dict:
    """The one place deciding Antigravity CLI's real current state -- shared by
    _dashboard_providers(), /v1/models, and GET /config/geminicli-models (the Refresh route), same
    pattern _ollama_status()/_gemini_status() already establish. No key-based states here (this is
    a keyless, subscription-CLI provider like Claude/Codex) -- just installed-and-working vs.
    not-installed vs. installed-but-unusable-right-now (not signed in, or a transient failure;
    `_fetch_agy_models` can't distinguish those two without a separate real call, so neither can
    this, and the message below says so honestly rather than guessing which one it is)."""
    if force:
        poll_cache.forget_which("agy")      # (Refresh: look again at whether the program is installed)
    if not _agy_path():
        return {
            "state": "off", "status": "Not installed", "models": [], "testable": False,
            "any": None, "live": True, "label": "Models",
            "empty": "Google Antigravity is not installed. Check Help for how to install it.",
        }

    rows = await _fetch_agy_models(force=force)
    if rows is None:
        return {
            "state": "warn", "status": "Installed, but not usable right now", "models": [], "testable": False,
            "any": None, "live": True, "label": "Models",
            "empty": "Sign in to Google Antigravity on your device, then press Check for models.",
        }

    model_rows = [[f"{GEMINICLI_PREFIX}{slug}", name, ""] for slug, name in rows]
    n = len(model_rows)
    return {
        # `any: None` once a full live list is showing -- "type your own" would be redundant, same
        # rule Ollama's/Gemini's own "ok" states already follow; only the incomplete states above
        # (not installed, not usable right now) keep the escape hatch.
        "state": "ok", "status": f"Installed and signed in: {n} model{'s' if n != 1 else ''} available",
        "models": model_rows, "testable": True, "any": None, "live": True,
        "label": "Models", "empty": "",
    }


# --- Grok CLI (SuperGrok subscription, via xAI's own `grok` CLI as a subprocess) ---

def _grok_flatten_prompt(system_prompt: Optional[str], messages: list) -> str:
    """`grok -p` takes one plain-text prompt, not a messages array (the same shape as _agy_flatten_prompt). Text only: a picture in a
    message is left out, because nothing of the player's (files, screenshots) is handed to the agent."""
    parts = []
    if system_prompt:
        parts.append(f"System: {system_prompt}")
    for m in messages:
        role = "User" if m["role"] == "user" else "Assistant"
        parts.append(f"{role}: {_content_as_text(m['content'])}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _grok_text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content["text"] if isinstance(content.get("text"), str) else ""
    if isinstance(content, list):
        return "".join(_grok_text_of(c) for c in content)
    return ""


def _grok_event_text(ev: dict) -> tuple[str, str]:
    """(a piece of the answer as it is written, a whole answer) carried by one event; either may be "". Read tolerantly: xAI's own
    `streaming-json` is one ACP session update per line (`agent_message_chunk` carries the pieces); the Anthropic-style shapes
    (`stream_event` deltas, a whole `assistant` message, a final `result`) are understood too, so a change of format doesn't silence it."""
    if ev.get("type") == "text":                         # the real app (read 2026-10-03): {"type":"text","data":"pong"}; its "thought" lines are the reasoning and are never the answer
        return (ev["data"] if isinstance(ev.get("data"), str) else ""), ""
    upd = ev
    params = ev.get("params")
    if isinstance(params, dict) and isinstance(params.get("update"), dict):
        upd = params["update"]
    elif isinstance(ev.get("update"), dict):
        upd = ev["update"]
    if upd.get("sessionUpdate") == "agent_message_chunk":
        return _grok_text_of(upd.get("content")), ""
    kind = ev.get("type")
    delta = None
    if kind == "stream_event":
        delta = (ev.get("event") or {}).get("delta")
    elif kind == "content_block_delta":
        delta = ev.get("delta")
    if isinstance(delta, dict):
        return (delta.get("text") or "") if delta.get("type") == "text_delta" else "", ""
    if kind == "assistant":
        return "", _grok_text_of((ev.get("message") or {}).get("content"))
    if kind == "result" and isinstance(ev.get("result"), str):
        return "", ev["result"]
    return "", ""


def _grok_usage_of(ev: dict) -> Optional[dict]:
    for holder in (ev, ev.get("result"), ev.get("message"), ev.get("params")):
        if isinstance(holder, dict) and isinstance(holder.get("usage"), dict):
            u = holder["usage"]
            i = u.get("input_tokens", u.get("inputTokens", u.get("prompt_tokens")))
            o = u.get("output_tokens", u.get("outputTokens", u.get("completion_tokens")))
            if i is not None or o is not None:
                return {"input_tokens": i, "output_tokens": o}
    return None


def _grok_error(message: str) -> HTTPException:
    """What the player is told when the CLI reports a problem: sign-in gets its own plain answer."""
    low = (message or "").lower()
    if "not signed in" in low or "not authenticated" in low:
        return HTTPException(status_code=401, detail="Grok isn't signed in. Check Help for the sign-in steps.")
    return HTTPException(status_code=502, detail=f"Grok failed: {(message or '').strip()[:500] or 'no error output'}")


async def _stream_grok_events(prompt: str, model: str, request_id: str, tmpdir: str):
    """Runs `grok` for one prompt and yields each parsed event as its line arrives. The prompt goes in a file (`--prompt-file`), never on
    the command line (Windows caps a command line at 32,767 characters and a real SkyrimNet prompt is often longer). It runs in the
    caller's empty scratch folder with nothing handed over: tools that change anything are refused (`--permission-mode dontAsk`),
    no web search, no helper agents, one turn. Raises HTTPException when the program can't start, times out or fails."""
    prompt_file = os.path.join(tmpdir, "prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)
    proc = await hidden_process.create_subprocess_exec(
        _grok_path(),
        "--no-auto-update",
        "--prompt-file", prompt_file,
        "--output-format", "streaming-json",
        "--model", model,
        "--permission-mode", "dontAsk",
        "--disable-web-search",
        "--no-subagents",
        "--tools", "read_file",                        # the app sends every tool's description with each request; one is the smallest list it takes (14,471 -> 5,871 input tokens, measured)
        "--max-turns", "1",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=tmpdir,
        limit=GROK_STDOUT_LIMIT,
        start_new_session=True,  # required by _kill_process_tree's POSIX branch
    )
    stderr_buf = bytearray()

    async def _drain_stderr():
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            stderr_buf.extend(chunk)

    drain_task = asyncio.create_task(_drain_stderr())
    deadline = time.time() + GROK_TIMEOUT
    timed_out = False
    abandoned = False           # SkyrimNet hung up: the reader was cancelled or the stream closed early
    error_message: Optional[str] = None
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ev = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "error":
                error_message = str(ev.get("message") or ev.get("data") or ev.get("error") or "")
                continue
            if ev.get("type") == "end":
                stop = ev.get("stopReason")
                if stop not in (None, "end_turn"):
                    error_message = f"it stopped early ({stop})"
                yield ev
                break                                  # "end" finishes the reply
            yield ev
    except (asyncio.CancelledError, GeneratorExit):
        abandoned = True
        raise
    finally:
        if timed_out or abandoned:
            await _kill_process_tree(proc)
            drain_task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await drain_task
        else:
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            with suppress(Exception):
                await asyncio.wait_for(drain_task, timeout=5)

    if timed_out:
        raise HTTPException(status_code=504, detail="Grok took too long to answer. Make sure it is signed in and try again.")
    if error_message is not None or proc.returncode != 0:
        err_text = error_message if error_message is not None else bytes(stderr_buf).decode("utf-8", errors="replace").strip()
        logger.error(f"[{request_id}] Grok CLI failed rc={proc.returncode}: {err_text[:500]}")
        raise _grok_error(err_text)


async def _run_grok_json(prompt: str, model: str, request_id: str, tmpdir: str) -> tuple[str, dict]:
    """Runs the CLI to the end; returns (the answer, usage)."""
    pieces: list[str] = []
    whole = ""
    usage: dict = {}
    async for ev in _stream_grok_events(prompt, model, request_id, tmpdir):
        piece, full = _grok_event_text(ev)
        if piece:
            pieces.append(piece)
        if full:
            whole = full
        usage = _grok_usage_of(ev) or usage
    text = "".join(pieces) or whole
    if not text:
        raise HTTPException(status_code=502, detail="Grok sent back no answer.")
    return text, {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}


_GROK_NOT_INSTALLED = "Grok isn't installed. Check Help for how to install it."


async def call_grok_direct(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params) -> tuple[str, dict]:
    if not _grok_path():
        raise HTTPException(status_code=500, detail=_GROK_NOT_INSTALLED)
    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> Grok CLI {model} ({len(messages)} msgs)")
    start = time.time()
    with tempfile.TemporaryDirectory() as tmpdir:
        text, usage = await _run_grok_json(_grok_flatten_prompt(system_prompt, messages), model, request_id, tmpdir)
    logger.info(f"[{request_id}] <- {len(text)} chars ({time.time() - start:.1f}s, Grok CLI)")
    return text, usage


async def call_grok_streaming(system_prompt: Optional[str], messages: list, model: str, max_tokens: int, **extra_params):
    """The answer as OpenAI-shaped SSE chunks, as the CLI writes it (a whole-message-only reply is sent as one chunk)."""
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    if not _grok_path():
        raise HTTPException(status_code=500, detail=_GROK_NOT_INSTALLED)
    request_id = uuid.uuid4().hex[:8]
    logger.info(f"[{request_id}] -> Grok CLI {model} ({len(messages)} msgs, stream)")
    start = time.time()
    chars = 0
    whole = ""

    def _chunk(delta_text: str) -> str:
        c = {"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
             "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}]}
        return f"data: {json.dumps(c)}\n\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        events = _stream_grok_events(_grok_flatten_prompt(system_prompt, messages), model, request_id, tmpdir)
        try:
            async for ev in events:
                piece, full = _grok_event_text(ev)
                if full:
                    whole = full
                if piece:
                    chars += len(piece)
                    yield _chunk(piece)
            if not chars and whole:
                chars = len(whole)
                yield _chunk(whole)
        except HTTPException:
            raise
        except Exception:
            logger.exception(f"[{request_id}] Unexpected error in Grok CLI streaming")
            raise HTTPException(status_code=500, detail="Something went wrong in MultiProxy. Please try again.")
        finally:
            await _aclose_quietly(events)          # (closing the reader is what stops the program when SkyrimNet hangs up)

    stop_chunk = {"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": model,
                  "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(stop_chunk)}\n\n"
    yield "data: [DONE]\n\n"
    logger.info(f"[{request_id}] <- {chars} chars ({time.time() - start:.1f}s, Grok CLI, streamed)")


# --- Grok CLI model discovery ---
# `grok models` prints "You are not authenticated." first when nobody is signed in, then "Default model: ..." and the list
# ("  * grok-4.6 (default)", "  - grok-4.5"). Read on 2026-10-03 (v1.0.46, signed out).

def _parse_grok_models_output(raw: str) -> dict:
    """{"signed_in": bool, "rows": [(slug, display name), ...]} from `grok models`' own output."""
    signed_in = "not authenticated" not in raw.lower()
    rows = []
    for line in raw.splitlines():
        m = re.match(r"^\s*[*-]\s+(\S+)", line)
        if m:
            rows.append((m.group(1), _xai_display_name(m.group(1))))
    return {"signed_in": signed_in, "rows": rows}


async def _fetch_grok_models(*, force: bool = False) -> Optional[dict]:
    """Runs `grok models`: {"signed_in", "rows"}, or None when the program is missing or the call fails (None = "can't tell").
    Kept GROK_MODELS_CACHE_TTL like the Antigravity list: a stale answer is handed out while a background look makes the next one."""
    if not _grok_path():
        return None
    cached_at, cached_value = _grok_models_cache
    if not force and cached_at:
        if time.time() - cached_at >= GROK_MODELS_CACHE_TTL:
            _cli_models_look_later("grok", _look_grok_models)
        return cached_value
    return await _cli_models_once("grok", _look_grok_models)


async def _look_grok_models() -> Optional[dict]:
    global _grok_models_cache
    now = time.time()
    exe = _grok_path()
    if not exe:
        return None
    result = None
    for attempt in range(2):        # a one-off quick failure (the program still starting up) gets one more try; a timeout does not (it would double the wait)
        try:
            proc = await asyncio.to_thread(hidden_process.run, [exe, "--no-auto-update", "models"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           stdin=subprocess.DEVNULL, timeout=GROK_MODELS_TIMEOUT)
            if proc.returncode == 0:
                parsed = _parse_grok_models_output((proc.stdout or b"").decode("utf-8", errors="replace"))
                result = parsed if parsed["rows"] or not parsed["signed_in"] else None
        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            break
        except OSError:
            result = None
        if result is not None:
            break
    # A failed look is remembered for less time than a good one (but still minutes, so a page poll never restarts the program).
    _grok_models_cache = (now if result is not None else now - (GROK_MODELS_CACHE_TTL - GROK_MODELS_FAILED_TTL), result)
    return result


async def _grok_status(*, force: bool = False) -> dict:
    """Grok CLI's real current state, shared by the dashboard, /v1/models and the Refresh route (the same pattern as _agy_status)."""
    if force:
        poll_cache.forget_which("grok")
    if not _grok_path():
        return {
            "state": "off", "status": "Not installed", "models": [], "testable": False,
            "any": None, "live": True, "label": "Models", "empty": _GROK_NOT_INSTALLED,
        }
    info = await _fetch_grok_models(force=force)
    if info is None:
        return {
            "state": "warn", "status": "Installed, but not usable right now", "models": [], "testable": False,
            "any": None, "live": True, "label": "Models",
            "empty": "Sign in to Grok on your device, then press Check for models.",
        }
    if not info["signed_in"]:
        return {
            "state": "warn", "status": "Installed, but not signed in", "models": [], "testable": False,
            "any": None, "live": True, "label": "Models",
            "empty": "Sign in to Grok on your device, then press Check for models.",
        }
    model_rows = [[f"{GROK_PREFIX}{slug}", name, ""] for slug, name in info["rows"]]
    n = len(model_rows)
    return {
        "state": "ok", "status": f"Installed and signed in: {n} model{'s' if n != 1 else ''} available",
        "models": model_rows, "testable": True, "any": None, "live": True, "label": "Models", "empty": "",
    }


def _claude_unavailable_message(reason: Optional[str]) -> str:
    """Shared wording for both the non-streaming and streaming Claude-unavailable error paths --
    says what's wrong and points at the obvious fix (pick a different provider's model), rather
    than a bare 'Auth not ready' that doesn't tell a user anything actionable."""
    reason = reason or "Claude auth could not be captured"
    return (
        f"Claude isn't available right now: {reason}. Pick a model from a different provider."
    )


# --- OpenAI-compatible API ---

class ChatMessage(BaseModel):
    role: str
    # Plain string is the overwhelmingly common case (normal dialogue). A list of OpenAI-style
    # content parts (e.g. [{"type": "text", "text": ...}, {"type": "image_url", "image_url":
    # {"url": "data:image/..."}}]) is what a real Vision request (SkyrimNet's OmniSight
    # screenshot analysis) actually sends -- FastAPI's own request validation used to reject the
    # whole request with a 422 before it ever reached chat_completions() at all (2026-09-20).
    # Kept as `list[dict]` rather than a strictly-typed part model: this proxy forwards parts
    # unchanged to whichever OpenAI-compatible provider is picked (see _oai_compat_messages /
    # _call_openai_compatible_direct's own message building), so it never needs to validate their
    # internal shape -- only the openai_shaped providers are guaranteed to actually understand
    # them (see chat_completions' own `openai_shaped` flag and _content_as_text below).
    content: Union[str, list, dict, None] = None     # (a caching-format message can send a part as a dict, a bare string in the list, or null)


class ChatRequest(BaseModel):
    model: Optional[str] = None
    # Optional, not required (queue: let-jev-decisions-through, 2026-09-29) -- a genuine chat
    # request must still have messages, but Pydantic can no longer be the one enforcing that: a
    # Jev-shaped Decisions request has no messages field at all (see is_jev_request below), and
    # this one model covers both request shapes on the same route. The check that used to be
    # automatic is now explicit, right at the top of _chat_completions_impl -- same outcome (a 422
    # for a chat request missing messages), just no longer FastAPI's own auto-generated one.
    messages: Optional[list[ChatMessage]] = None
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = None
    stream: Optional[bool] = False
    # Jev's own two fields (docs.typesafe.ai/api) -- typed here rather than left to fall through
    # `extra="allow"` so is_jev_request/the Jev handlers can read req.questions/req.state directly
    # instead of reaching into model_extra. `state` really is string | object | array per TypeSafe's
    # own docs (confirmed live 2026-09-29: SkyrimNet's real Decisions request sends an object,
    # {"text": "..."}, not the bare string a first guess at this shape assumed).
    questions: Optional[dict] = None
    state: Optional[Union[str, dict, list]] = None
    model_config = ConfigDict(extra="allow")


def is_jev_request(req: "ChatRequest") -> bool:
    """True for a Jev/Decisions request -- SkyrimNet's own experimental "pick one of N options"
    job (see this file's TYPESAFE_BASE_URL comment for the full story), never a chat completion.
    Two ways in, either is enough on its own: the model name says so directly (a typesafe/ prefix,
    however this particular model gets routed downstream), or the body itself has the one shape a
    real chat request never has -- a `questions` map and no `messages` at all. The second check
    exists for a caller that skips the prefix (or for a future TypeSafe model id that doesn't
    happen to start with typesafe/) -- it's deliberately narrow (questions AND no messages, not
    just "has questions") so a normal chat request that happened to carry an unrelated "questions"
    field in its own extra data is never misrouted here."""
    model = req.model or ""
    if model.startswith(TYPESAFE_PREFIX):
        return True
    return req.questions is not None and not req.messages


def _question_names(questions) -> str:
    """The names of a decision's questions for a log line ("mood, speaker"), at most six; "no questions" when there are none."""
    names = [str(k) for k in questions] if isinstance(questions, dict) else []
    if not names:
        return "no questions"
    return ", ".join(names[:6]) + (", ..." if len(names) > 6 else "")


def _via_text(via: str) -> str:
    return f" via {via}" if via else ""


async def call_typesafe_systemone(model: str, state, questions: dict, request_id: str, via: str = "", limit: Optional[float] = None) -> tuple[int, bytes, str]:
    """Calls TypeSafe's real systemone endpoint directly and hands back whatever it said --
    (status_code, raw_response_bytes, content_type), completely unexamined. That's deliberate, not
    a shortcut: SkyrimNet already speaks this exact wire protocol (its own Decisions job builds a
    {model, state, questions} body expecting an {answers, usage} one back -- see docs.typesafe.ai/
    api), so there is nothing here for a tag fixer or a chat-completion wrapper to improve on, and
    passing a real 401/422/429/529 straight through is what lets SkyrimNet's own fallback-to-chat
    logic recognize a genuine failure instead of a proxy-shaped one.

    Only raises for a genuine connection failure (no response at all) -- the same
    aiohttp.ClientError class _post_with_retry already retries once before giving up, same as
    every other provider's own call_X_direct. A real HTTP error response FROM TypeSafe is not an
    exception here; it's returned as data, because the caller's whole job is to relay it exactly.

    `limit` (2026-10-05, a decision): seconds to wait in all, with no retry; when it runs out the call is cancelled and DECISION_SLOW comes back so the order moves on.
    """
    resolved_model = _resolve_typesafe_model(model)
    payload = {"model": resolved_model, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {typesafe_api_key}", "Content-Type": "application/json"}
    logger.info(f"[{request_id}] -> TypeSafe {resolved_model} ({_question_names(questions)}){_via_text(via)}")
    start = time.time()
    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        async with _post_with_retry(
            session, TYPESAFE_BASE_URL, provider="TypeSafe", request_id=request_id,
            json=payload, headers=headers, **_decision_limit_kwargs(limit),
        ) as resp:
            elapsed = time.time() - start
            body = await resp.read()
            content_type = resp.headers.get("Content-Type", "application/json")
            if resp.status == 200:
                logger.info(f"[{request_id}] <- {elapsed:.1f}s, TypeSafe")
            else:
                logger.error(f"[{request_id}] TypeSafe {resp.status} ({elapsed:.1f}s): {body[:300]!r}")
            return resp.status, body, content_type
    except asyncio.TimeoutError:
        logger.warning(f"[{request_id}] TypeSafe didn't answer within {limit:g} s, giving up on it for this decision")
        return DECISION_SLOW, b"", "application/json"
    except aiohttp.ClientError as e:
        # No response at all -- unlike the branch above, there is genuinely no upstream JSON to
        # relay, so this is the one case where wrapping through the proxy's own error shape (via
        # HTTPException) is the right call rather than a shortcut.
        logger.error(f"[{request_id}] Couldn't reach TypeSafe: {e}")
        raise HTTPException(status_code=502, detail=f"Couldn't reach TypeSafe: {e}")
    finally:
        if owns_session:
            await session.close()


def _decision_limit_kwargs(limit: Optional[float]) -> dict:
    """The extra arguments of a decision's paid call: the whole call may take `limit` seconds, and it is tried once. None: as every other call."""
    if not limit:
        return {}
    return {"attempts": 1, "timeout": aiohttp.ClientTimeout(total=limit)}


def _paid_limit() -> Optional[float]:
    """The seconds in the sentence "...or the answer is taking longer than N seconds, try the next model down", kept inside its range. None while that box is unticked: no
    hand-over by time, the call only has the ordinary timeouts."""
    if not decision_handles.settings().get("ask_when_unsure", True):
        return None
    try:
        v = float(decision_handles.settings().get("paid_limit_seconds") or DECISION_PAID_LIMIT)
    except (TypeError, ValueError):
        v = DECISION_PAID_LIMIT
    return min(max(v, DECISION_PAID_LIMIT_RANGE[0]), DECISION_PAID_LIMIT_RANGE[1])


def typesafe_slow_line(another_answered: bool) -> str:
    return "TypeSafe was too slow." + (" Another AI answered instead." if another_answered else " No other AI was ready to answer.")


def _note_typesafe_slow(another_answered: bool, request_id: str) -> None:
    line = typesafe_slow_line(another_answered)
    decision_handles.typesafe_line = line
    logger.info(f"[{request_id}] {line}")


def nano_decision_line(status: int, another_answered: bool) -> str:
    """The one plain line the player sees when Nano-GPT could not answer a decision (the Quick decisions card shows it)."""
    why = {401: "your key may not be right", 403: "your key may not be right", 402: "your balance may be empty", 404: "it can't find the decision model",
           429: "it is too busy right now"}.get(status)
    head = "Nano-GPT was too slow." if status == DECISION_SLOW else f"Nano-GPT said no ({why})." if why else "Nano-GPT didn't answer."
    return head + (" Another AI answered instead." if another_answered else " No other AI was ready to answer.")


async def call_nano_systemone(state, questions: dict, request_id: str, via: str = "", limit: Optional[float] = None) -> tuple[int, bytes, str]:
    """Asks Jev through Nano-GPT's System One route (same body as TypeSafe's own, the player's Nano-GPT key as the bearer). Returns (status, body, content_type); a 200 is
    only returned when the answer really has `answers`, and its body is TypeSafe's shape: Nano-GPT's extra `id` and `provider` fields are dropped so SkyrimNet never sees
    them. Anything else (a refusal, no connection, an answer without answers) comes back as a non-200 status, never raised: the caller passes the decision on."""
    model = _nano_decision_model()
    payload = {"model": model, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {nano_api_key}", "Content-Type": "application/json"}
    logger.info(f"[{request_id}] -> Nano-GPT {model} ({_question_names(questions)}){_via_text(via)}")
    start = time.time()
    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        async with _post_with_retry(session, NANO_DECISIONS_URL, provider="Nano-GPT", request_id=request_id, json=payload, headers=headers, **_decision_limit_kwargs(limit)) as resp:
            elapsed = time.time() - start
            body = await resp.read()
            content_type = resp.headers.get("Content-Type", "application/json")
            if resp.status != 200:
                logger.error(f"[{request_id}] Nano-GPT {resp.status} ({elapsed:.1f}s): {_redact_text(body[:300].decode('utf-8', 'replace'))!r}")
                return resp.status, body, content_type
            try:
                data = json.loads(body)
                if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                    raise ValueError("no answers")
            except (ValueError, TypeError):
                logger.error(f"[{request_id}] Nano-GPT answered 200 but not with answers ({elapsed:.1f}s)")
                return 502, body, content_type
            data.pop("id", None)
            data.pop("provider", None)
            logger.info(f"[{request_id}] <- {elapsed:.1f}s, Nano-GPT")
            return 200, json.dumps(data).encode("utf-8"), "application/json"
    except asyncio.TimeoutError:
        logger.warning(f"[{request_id}] Nano-GPT didn't answer within {limit:g} s, giving up on it for this decision")
        return DECISION_SLOW, b"", "application/json"
    except aiohttp.ClientError as e:
        logger.error(f"[{request_id}] Couldn't reach Nano-GPT: {type(e).__name__}")
        return 502, b"", "application/json"
    except HTTPException as e:
        return e.status_code, b"", "application/json"
    finally:
        if owns_session:
            await session.close()


def _extract_json_object(text: str) -> str:
    """Strips a markdown code fence around a JSON object if one snuck in despite being told not
    to (common LLM habit) -- used only by the Jev stand-in's own response parsing below."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"```\s*$", "", stripped).strip()
    return stripped


def _build_jev_fallback_prompt(state, questions: dict) -> str:
    """Turns a Jev {state, questions} body into a single plain chat prompt asking the same
    questions in natural language -- what a Jev stand-in sends when there's no way to
    reach TypeSafe's own typed-question endpoint."""
    state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    lines = [
        "You are answering structured questions about the input below, using only the input and "
        "each question's own instructions/criteria.",
        "", f"Input:\n{state_text}", "",
    ]
    for qid, q in questions.items():
        qtype = q.get("type") if isinstance(q, dict) else None
        instructions = q.get("instructions", "") if isinstance(q, dict) else ""
        lines.append(f'Question "{qid}" ({qtype}): {instructions}')
        criteria = q.get("criteria") if isinstance(q, dict) else None
        if qtype == "choice" and isinstance(criteria, dict):
            for key, desc in criteria.items():
                lines.append(f"  - {key}: {desc}")
        elif qtype == "score" and isinstance(criteria, list):
            for i, desc in enumerate(criteria):
                lines.append(f"  Level {i}: {desc}")
        elif qtype == "noul" and isinstance(criteria, dict):
            for key, desc in criteria.items():
                lines.append(f"  {key}: {desc}")
        lines.append("")
    lines.append(
        'Respond with ONLY one JSON object, no other text, mapping each question id to its raw '
        'answer -- a noul question\'s answer is a number from 0 to 1, a choice question\'s answer '
        'is the exact option key you chose, a score question\'s answer is the 0-based level number '
        'you chose. Example shape: {"some_id": 0.8, "other_id": "some_key", "third_id": 2}'
    )
    return "\n".join(lines)


def _reshape_jev_stand_in_answer(model: str, raw_text: str, questions: dict, usage: dict, request_id: str) -> tuple[int, dict]:
    """Turns an ordinary chat reply (whatever plain-text answer a stand-in model gave to
    _build_jev_fallback_prompt's own question list) into the same {model, answers, usage} envelope
    TypeSafe's real systemone endpoint would have returned. Shared by every stand-in path (queue:
    jev-fallback-order, 2026-09-29). Every answer here carries fabricated (not real, calibrated)
    probabilities/confidence -- an honest, disclosed step down from the real thing, never a
    substitute for it."""
    try:
        raw_answers = json.loads(_extract_json_object(raw_text))
        if not isinstance(raw_answers, dict):
            raise ValueError("not a JSON object")
    except (json.JSONDecodeError, ValueError):
        logger.error(f"[{request_id}] Jev stand-in didn't return parseable JSON: {raw_text[:300]!r}")
        return 502, {"error": {"message": "The backup model gave a broken answer.", "type": "api_error", "code": None}}

    answers = {}
    for qid, q in (questions or {}).items():
        qtype = q.get("type") if isinstance(q, dict) else None
        raw = raw_answers.get(qid)
        if qtype == "noul":
            try:
                answers[qid] = {"type": "noul", "noul": max(0.0, min(1.0, float(raw)))}
            except (TypeError, ValueError):
                answers[qid] = {"type": "noul", "noul": 0.5}
        elif qtype == "choice":
            criteria = q.get("criteria") if isinstance(q.get("criteria"), dict) else {}
            chosen = raw if raw in criteria else next(iter(criteria), None)
            probabilities = {k: (1.0 if k == chosen else 0.0) for k in criteria}
            answers[qid] = {"type": "choice", "choice": chosen, "probabilities": probabilities, "confidence": 1.0}
        elif qtype == "score":
            criteria = q.get("criteria") if isinstance(q.get("criteria"), list) else []
            try:
                idx = max(0, min(len(criteria) - 1, int(raw))) if criteria else 0
            except (TypeError, ValueError):
                idx = 0
            legend = {str(i): level for i, level in enumerate(criteria)}
            probabilities = {str(i): (1.0 if i == idx else 0.0) for i in range(len(criteria))}
            answers[qid] = {"type": "score", "score": idx, "legend": legend, "probabilities": probabilities, "confidence": 1.0}
        else:
            answers[qid] = {"type": qtype, "value": raw}

    return 200, {"model": model, "answers": answers, "usage": usage}


# Jev decisions order (2026-09-30, after OpenRouter was removed):
#   1. TypeSafe key saved -> TypeSafe directly (call_typesafe_systemone).
#   2. No TypeSafe key, but a Nano-GPT key -> Jev through Nano-GPT (call_nano_systemone), recorded as Nano-GPT. A refusal passes the decision on, with a line for the card.
#   3. Neither, but some other provider is ready -> a stand-in answered by THAT provider
#      (call_jev_stand_in below), clearly recorded under the real provider, never as Jev.
#   4. Nothing ready at all -> a clear error.
# (With the local decision model installed the order is decision_wiring's `order`; Nano-GPT joins it after TypeSafe, or first if the player chose it.)
# (It used to have a step between 1 and 2 -- OpenRouter's own "Jev Router" model -- before MultiProxy
# stopped connecting to OpenRouter. Git history has it.)
#
async def call_jev_stand_in(model: str, state, questions: dict, request_id: str, via: str = "", skip: tuple = ()) -> Optional[tuple[int, dict, str, str]]:
    """Step 2: no TypeSafe key -- has an AI the player already has ready answer
    the questions instead, reshaping a plain chat reply (_reshape_jev_stand_in_answer). Picks the first ready
    entry in the table below (a saved key, or for Claude a genuinely signed-in CLI --
    `auth.is_ready`, not just CLAUDE_PATH being non-None, so a CLI that's installed but not
    actually working yet is correctly skipped). Returns None when nothing in the table is ready
    (the caller raises the real "nothing configured at all" error); otherwise
    (status, body, real_model_id, display_name) -- the real model id and display name matter to
    the caller, which records the Speeds-tab entry under THAT real name/provider, never under
    "typesafe/..." (see the item's own "never mistaken for Jev" instruction).

    Stand-in order (queue: jev-fallback-order, 2026-09-29) -- fast, cheap TEXT models only, a
    saved key or (for Claude) a genuinely signed-in CLI counts as "ready". Codex and Antigravity
    are deliberately excluded: both run a real CLI subprocess per call (several real seconds just
    to start), too slow for a job SkyrimNet gives a 10-second budget. Ollama is ALSO deliberately
    left out, not forgotten -- unlike every other entry here, there's no single model id that's a
    safe default: what's actually available depends entirely on what the player has pulled locally
    or configured on Ollama Cloud, so a hardcoded guess here could confidently pick a model that
    doesn't exist.

    Built FRESH on every call (a local, not a module-level constant) -- same reason
    chat_completions()'s own _ROUTES table is local, not module-level: a plain module-level tuple
    would capture whichever call_X_direct function object existed at IMPORT time, permanently,
    which both a test's `patch.object(proxy_module, "call_deepseek_direct", ...)` and a genuine
    future reassignment would then silently miss (caught live while writing this feature's own
    tests -- a mocked call_deepseek_direct was never actually invoked until this moved local)."""
    n_q = len(questions) if isinstance(questions, dict) else 0
    prompt = _build_jev_fallback_prompt(state, questions or {})
    # (name shown in logs/Speeds, is-ready check, real model id to send, that provider's own call_X_direct)
    stand_in_providers = (
        ("DeepSeek", lambda: bool(deepseek_api_key), "deepseek-flash", call_deepseek_direct),
        ("GLM", lambda: bool(glm_api_key), "glm-4.6", call_glm_direct),
        ("Gemini", lambda: bool(gemini_api_key), "gemini-3.8-flash", call_gemini_direct),
        ("OpenAI", lambda: bool(openai_api_key), "gpt-4o-mini", call_openai_direct),
        ("Nano-GPT", lambda: bool(nano_api_key), "gpt-4o-mini", call_nano_direct),
        ("Kilo Gateway", lambda: bool(kilo_api_key), "kilo-auto/balanced", call_kilo_direct),
        ("Claude", lambda: auth.is_ready, _default_claude_model(), call_api_direct),
    )
    for display_name, is_ready, real_model, call_direct in stand_in_providers:
        if display_name in skip or not is_ready():            # `skip`: a provider that was just asked for this decision and said no (Nano-GPT, after its own Jev call failed)
            continue
        logger.info(f"[{request_id}] -> Jev stand-in, answered by {display_name} {real_model} instead of TypeSafe ({_question_names(questions)}){_via_text(via)}")
        rec = request_facts.current()
        if rec is not None:
            rec.stand_in = "TypeSafe"
        start = time.time()
        try:
            raw_text, usage = await call_direct(None, [{"role": "user", "content": prompt}], real_model, 500)
        except HTTPException as e:
            logger.error(f"[{request_id}] The Jev backup via {display_name} failed: {e.detail}")
            return e.status_code, {"error": {"message": f"The Jev backup via {display_name} failed: {e.detail}", "type": "api_error", "code": None}}, real_model, display_name
        elapsed = time.time() - start
        logger.info(f"[{request_id}] <- {elapsed:.1f}s, Jev stand-in via {display_name}")
        if rec is not None:
            rec.note_usage(usage)
        status, body = _reshape_jev_stand_in_answer(model, raw_text, questions, usage, request_id)
        return status, body, real_model, display_name
    return None


async def _ask_nano_decision(state, questions: dict, request_id: str, via: str, rec, first: str, miss: str = "") -> tuple[int, bytes, str]:
    """One decision asked of Jev through Nano-GPT, with everything that goes with it: the Speeds row (the real provider Nano-GPT, model jev-latest, role Decisions,
    never recorded as TypeSafe), the request line, and the card's note (cleared on an answer; on a refusal set later by `_note_nano_miss`, once it is known whether
    another AI answered)."""
    t0 = time.time()
    model = _nano_decision_model()
    status, body, content_type = await call_nano_systemone(state, questions, request_id, via=via, limit=_paid_limit())
    await _record_speed(model, time.time() - t0, status == 200, fail_reason=None if status == 200 else "timeout" if status == DECISION_SLOW else f"Nano-GPT returned {status}",
                        provider="Nano-GPT", extra_roles=["Decisions"])
    if rec is not None:
        rec.status = status
        if status == 200:
            rec.model, rec.provider = model, "Nano-GPT"
            try:
                rec.note_usage(json.loads(body).get("usage"))
            except Exception:
                pass
        else:
            rec.model = rec.provider = None            # whoever answers next names itself
    if status == 200:
        decision_handles.nano_line = ""
        decision_handles.note_answer("Nano-GPT", first, miss)
    return status, body, content_type


def _note_nano_miss(status: int, another_answered: bool, request_id: str) -> None:
    line = nano_decision_line(status, another_answered)
    decision_handles.nano_line = line
    logger.info(f"[{request_id}] {line}")


# ---- Decision models from any provider (2026-10-05; decision_providers.py is the one registry) ------------------------------------------------------------
# Every provider whose own model list says a model is for Decisions can answer a quick decision. Researched per provider (see the item's handoff): Ollama's decision
# models answer POST /v1/systemone on Ollama's own address (TypeSafe's shape plus `model`), Nano-GPT's answer the System One call it already had, with the chosen model.
# To add a provider: write its `read` (its model list, filtered with decision_providers.decision_models) and its `ask`, and register it below. Nothing else changes.
async def _ollama_version() -> str:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=OLLAMA_SHOW_TIMEOUT)) as session:
            async with session.get(f"{_ollama_api_root(OLLAMA_LOCAL_BASE_URL)}/api/version") as resp:
                if resp.status == 200:
                    return str((await resp.json()).get("version") or "")
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        pass
    return ""


async def _read_ollama_decision_models() -> list:
    """Ollama on this device: the installed models its own `capabilities` call Decisions (never guessed from a name), minus any known not to work on this Ollama version."""
    tags = await _fetch_ollama_tags(OLLAMA_LOCAL_BASE_URL, None)
    raw = sorted((tags or {}).get("models") or [], key=lambda m: (m.get("name") or m.get("model") or "").lower())
    if not raw:
        return []
    kinds = await _ollama_kinds(OLLAMA_LOCAL_BASE_URL, raw)
    names = decision_providers.decision_models([((m.get("name") or m.get("model") or ""), kinds.get(m.get("name") or m.get("model") or "")) for m in raw])
    if not names:
        return []
    version = await _ollama_version()
    return [n for n in names if decision_providers.ollama_model_works(n, version)]


async def _read_nano_decision_models() -> list:
    """Nano-GPT's detailed list (needs its key): the models that answer `decisions`. The one MultiProxy always used goes first."""
    if not nano_api_key:
        return []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        async with session.get(f"{NANO_BASE_URL}/models?detailed=true", headers={"Authorization": f"Bearer {nano_api_key}"}) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    rows = [m for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")] if isinstance(data, dict) else []
    names = decision_providers.decision_models([(str(m["id"]), model_kinds.from_nano(m)) for m in rows])
    names.sort(key=lambda n: n != f"typesafe/{NANO_DECISIONS_MODEL}")
    return names


# ---- Perplexity's Decisions API (2026-10-25): a cheap cloud answer for the decision role, never for chat -------------------------------------------------------
# docs.perplexity.ai ("Decisions API", read 2026-10-25): POST /v1/decisions with `Authorization: Bearer <key>` and {model, state, questions}; the answer is {model, answers, usage}.
# SkyrimNet's questions have the same three kinds (yes/no `noul`, `choice`, `score`) written the same way, so the request goes across as it is; what differs is checked
# BEFORE the call (Perplexity answers 400 to a field it does not know, takes only inline PNG, JPEG or WebP pictures, at most 8 of them, and limits how many questions, options and
# levels there are) and AFTER it (every question must come back answered in its own kind). Whatever cannot be mapped honestly is not guessed: the decision is passed on to the
# next answerer and the reason is logged. The full field-by-field comparison is in TECHNICAL.md ("Perplexity: a decisions-only key").
PERPLEXITY_LABELS = {"pplx-decider-latest": "Standard", "pplx-decider-flash-latest": "Cheaper and faster"}
PERPLEXITY_QUESTION_FIELDS = {"type", "instructions", "criteria"}
PERPLEXITY_MAX_QUESTIONS, PERPLEXITY_MAX_OPTIONS, PERPLEXITY_MAX_LEVELS, PERPLEXITY_MAX_IMAGES = 128, 255, 10, 8
PERPLEXITY_NOT_MAPPED = 422              # the status of "this decision cannot be sent as it is": never a real answer from Perplexity, so the decision is passed on


class _PerplexityCantMap(Exception):
    """A decision Perplexity cannot take as SkyrimNet wrote it; `.args[0]` is the reason, for the log."""


def _perplexity_images(node, found: Optional[list] = None) -> list:
    """Every picture part (`{"type": "image_url", "image_url": {"url": ...}}`) anywhere in `state`, at any depth."""
    found = [] if found is None else found
    if isinstance(node, dict):
        if node.get("type") == "image_url":
            found.append(node)
        else:
            for v in node.values():
                _perplexity_images(v, found)
    elif isinstance(node, list):
        for v in node:
            _perplexity_images(v, found)
    return found


def _perplexity_check_state(state) -> None:
    if state is None:
        raise _PerplexityCantMap("the decision has no state")
    images = _perplexity_images(state)
    if len(images) > PERPLEXITY_MAX_IMAGES:
        raise _PerplexityCantMap(f"it has {len(images)} pictures and Perplexity takes {PERPLEXITY_MAX_IMAGES}")
    for part in images:
        url = (part.get("image_url") or {}).get("url") if isinstance(part.get("image_url"), dict) else None
        if not (isinstance(url, str) and re.match(r"data:image/(png|jpeg|webp);base64,", url)):
            raise _PerplexityCantMap("a picture is not an inline PNG, JPEG or WebP (Perplexity does not fetch web addresses)")


def _perplexity_check_questions(questions) -> None:
    if not isinstance(questions, dict) or not 1 <= len(questions) <= PERPLEXITY_MAX_QUESTIONS:
        raise _PerplexityCantMap(f"it needs 1 to {PERPLEXITY_MAX_QUESTIONS} questions")
    for name, q in questions.items():
        if not name or not isinstance(q, dict):
            raise _PerplexityCantMap("a question is not written as an object with a name")
        extra = sorted(set(q) - PERPLEXITY_QUESTION_FIELDS)
        if extra:
            raise _PerplexityCantMap(f"question '{name}' has a field Perplexity does not have ({', '.join(map(str, extra))})")
        kind, instructions, criteria = q.get("type"), q.get("instructions"), q.get("criteria")
        if instructions is not None and not isinstance(instructions, str):
            raise _PerplexityCantMap(f"question '{name}': the instructions are not text")
        if kind == "noul":
            if not instructions and not criteria:
                raise _PerplexityCantMap(f"question '{name}' has neither instructions nor criteria")
            if criteria is not None and not (isinstance(criteria, dict) and set(criteria) <= {"true", "false"} and all(isinstance(v, str) for v in criteria.values())):
                raise _PerplexityCantMap(f"question '{name}': a yes/no question's criteria are only 'true' and 'false' texts")
        elif kind == "choice":
            if not (isinstance(criteria, dict) and 1 <= len(criteria) <= PERPLEXITY_MAX_OPTIONS and all(v is None or isinstance(v, str) for v in criteria.values())):
                raise _PerplexityCantMap(f"question '{name}': a choice needs 1 to {PERPLEXITY_MAX_OPTIONS} options, each with a text or nothing")
        elif kind == "score":
            if not (isinstance(criteria, list) and 1 <= len(criteria) <= PERPLEXITY_MAX_LEVELS and all(isinstance(v, str) for v in criteria)):
                raise _PerplexityCantMap(f"question '{name}': a score needs 1 to {PERPLEXITY_MAX_LEVELS} levels, each a text")
        else:
            raise _PerplexityCantMap(f"question '{name}' is of a kind Perplexity does not have ({kind!r})")


def _perplexity_number(v, low: float = 0.0, high: float = 1.0) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high


def _perplexity_read_answers(data, questions: dict) -> dict:
    """Perplexity's reply -> what SkyrimNet reads ({model, answers, usage}). Every question asked must come back answered in its own kind with the fields that kind has;
    anything else raises ValueError (the decision is passed on, nothing is made up). The answers themselves are handed on unchanged."""
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise ValueError("no answers")
    out = {}
    for name, q in questions.items():
        a = answers.get(name)
        if not isinstance(a, dict) or a.get("type") != q.get("type"):
            raise ValueError(f"question '{name}' came back unanswered or in another kind")
        kind, criteria = q["type"], q.get("criteria")
        if kind == "noul":
            ok = _perplexity_number(a.get("noul"))
        elif kind == "choice":
            ok = a.get("choice") in criteria and isinstance(a.get("probabilities"), dict) and _perplexity_number(a.get("confidence"))
        else:
            ok = (_perplexity_number(a.get("score"), 0, len(criteria) - 1) and isinstance(a.get("probabilities"), dict) and isinstance(a.get("legend"), dict)
                  and _perplexity_number(a.get("confidence")))
        if not ok:
            raise ValueError(f"question '{name}' came back with a {kind} answer that is missing a part")
        out[name] = a
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    counts = {k: int(usage[k]) for k in ("input_tokens", "output_tokens") if isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)}
    return {"model": data.get("model") if isinstance(data.get("model"), str) else "", "answers": out, "usage": {"input_tokens": counts.get("input_tokens", 0), "output_tokens": counts.get("output_tokens", 0)}}


def perplexity_decision_line(status: int) -> str:
    """The first half of the card's line when Perplexity could not answer a decision (the Quick decisions card adds whether another AI answered instead)."""
    if status == DECISION_SLOW:
        return "Perplexity was too slow."
    if status in (401, 403):
        return "Perplexity didn't accept the key."
    if status == 429:
        return "Perplexity is too busy right now."
    if status == PERPLEXITY_NOT_MAPPED:
        return "Perplexity can't take this decision."
    return "Perplexity didn't answer."


async def call_perplexity_decisions(model: str, state, questions: dict, request_id: str, via: str = "", limit: Optional[float] = None) -> tuple[int, bytes, str]:
    """Asks Perplexity's Decisions API one decision: the same {model, state, questions} body SkyrimNet sends (nothing else: Perplexity refuses a field it does not know, so
    SkyrimNet's `model_index` and the like stay behind), the player's key as the bearer. Returns (status, body, content_type) like every decision provider; a 200 is returned
    only when every question came back answered in its own kind, as {model, answers, usage}. Anything else (a decision that cannot be mapped, a refusal, a timeout, no
    connection, a broken answer) comes back as a non-200 status and is never raised: the caller passes the decision on. The key is never logged."""
    try:
        _perplexity_check_state(state)
        _perplexity_check_questions(questions)
    except _PerplexityCantMap as e:
        logger.info(f"[{request_id}] Perplexity can't take this decision ({e.args[0]}), passing it on")
        return PERPLEXITY_NOT_MAPPED, b"", "application/json"
    payload = {"model": model, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {perplexity_api_key}", "Content-Type": "application/json"}
    logger.info(f"[{request_id}] -> Perplexity {model} ({_question_names(questions)}){_via_text(via)}")
    start = time.time()
    session = auth.session or aiohttp.ClientSession()
    owns_session = auth.session is None
    try:
        async with _post_with_retry(session, PERPLEXITY_DECISIONS_URL, provider="Perplexity", request_id=request_id, json=payload, headers=headers, **_decision_limit_kwargs(limit)) as resp:
            elapsed = time.time() - start
            body = await resp.read()
            if resp.status != 200:
                logger.error(f"[{request_id}] Perplexity {resp.status} ({elapsed:.1f}s): {_redact_text(body[:300].decode('utf-8', 'replace'))!r}")
                return resp.status, body, resp.headers.get("Content-Type", "application/json")
            try:
                read = _perplexity_read_answers(json.loads(body), questions)
            except (ValueError, TypeError) as e:
                logger.error(f"[{request_id}] Perplexity answered 200 but not with usable answers ({elapsed:.1f}s): {e}")
                return 502, b"", "application/json"
            logger.info(f"[{request_id}] <- {elapsed:.1f}s, Perplexity")
            return 200, json.dumps({**read, "model": read["model"] or model}).encode("utf-8"), "application/json"
    except asyncio.TimeoutError:
        logger.warning(f"[{request_id}] Perplexity didn't answer within {limit:g} s, giving up on it for this decision")
        return DECISION_SLOW, b"", "application/json"
    except aiohttp.ClientError as e:
        logger.error(f"[{request_id}] Couldn't reach Perplexity: {type(e).__name__}")
        return 502, b"", "application/json"
    except HTTPException as e:
        return e.status_code, b"", "application/json"
    finally:
        if owns_session:
            await session.close()


async def _read_perplexity_decision_models() -> list:
    return list(PERPLEXITY_DECISION_MODELS) if perplexity_api_key else []


def _note_provider_miss(prov, status: int, another_answered: bool, request_id: str) -> None:
    """The card's one plain line when a paid decision provider (Perplexity) could not answer the last decision it was asked."""
    line = (prov.miss_line(status) if prov.miss_line else f"{prov.name} didn't answer.") + (" Another AI answered instead." if another_answered else " No other AI was ready to answer.")
    decision_handles.provider_lines[prov.id] = line
    logger.info(f"[{request_id}] {line}")


async def call_ollama_decision(model: str, state, questions: dict, request_id: str, via: str = "", limit: Optional[float] = None) -> tuple[int, bytes, str]:
    """Asks one of Ollama's decision models: POST /v1/systemone with a `model` added. Returns (status, body, content_type); a 200 body is {model, answers, usage} read with
    the same reading the local engine uses (decision_engine.read_answer). A question Ollama refuses (400: choice questions take 2 to 26 options) comes back as 400 and is passed
    on. When the model is slower than `limit` (it may still be loading) the decision is passed on and the request is left running, so the model finishes loading."""
    payload = {"model": model, "state": state, "questions": questions}
    logger.info(f"[{request_id}] -> Ollama {model} ({_question_names(questions)}){_via_text(via)}")
    start = time.time()

    async def post():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(f"{_ollama_api_root(OLLAMA_LOCAL_BASE_URL)}/v1/systemone", json=payload) as resp:
                return resp.status, await resp.read()
    task = asyncio.ensure_future(post())
    try:
        done, _ = await asyncio.wait({task}, timeout=limit or None)
    except asyncio.CancelledError:          # SkyrimNet hung up: the request to Ollama stops too (only a decision that is merely too slow is left running, below)
        task.cancel()
        raise
    if not done:
        task.add_done_callback(lambda t: t.cancelled() or t.exception())            # nobody waits for it, but it keeps going: a model still loading finishes loading
        logger.warning(f"[{request_id}] Ollama didn't answer within {limit:g} s, giving up on it for this decision")
        return DECISION_SLOW, b"", "application/json"
    try:
        status, body = task.result()
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
        logger.error(f"[{request_id}] Couldn't reach Ollama: {type(e).__name__}")
        return 502, b"", "application/json"
    if status != 200:
        logger.error(f"[{request_id}] Ollama {status} ({time.time() - start:.1f}s): {body[:200]!r}")
        return status, body, "application/json"
    try:
        data = json.loads(body)
        read = decision_wiring.de.read_answer(data)
    except (ValueError, TypeError, decision_wiring.de.DecisionError):
        logger.error(f"[{request_id}] Ollama answered 200 but not with answers")
        return 502, body, "application/json"
    logger.info(f"[{request_id}] <- {time.time() - start:.1f}s, Ollama")
    return 200, json.dumps({"model": data.get("model") or model, **read}).encode("utf-8"), "application/json"


decision_providers.register(decision_providers.DecisionProvider(
    id="ollama", name="Ollama", choice="ollama", label="My Ollama decision model", read=lambda: _read_ollama_decision_models(), ask=call_ollama_decision, local=True,
    off_reason="Ollama isn't running, or it has no decision model, so that choice is off."))
decision_providers.register(decision_providers.DecisionProvider(
    id="nano", name="Nano-GPT", choice="nanogpt", label="My Nano-GPT key", read=lambda: _read_nano_decision_models(), ask=None, key_ok=lambda: bool(nano_api_key)))
decision_providers.register(decision_providers.DecisionProvider(
    id="perplexity", name="Perplexity", choice="perplexity", label="My Perplexity key", read=lambda: _read_perplexity_decision_models(), ask=call_perplexity_decisions,
    key_ok=lambda: bool(perplexity_api_key), static=lambda: list(PERPLEXITY_DECISION_MODELS) if perplexity_api_key else [], model_labels=PERPLEXITY_LABELS,
    miss_line=perplexity_decision_line, off_reason="You haven't added a Perplexity key, so this choice is turned off."))


def _nano_decision_model() -> str:
    """The Nano-GPT decision model in use: the player's pick from its own list, else the one MultiProxy always used."""
    return decision_providers.chosen_model(decision_providers.REGISTRY["nano"], decision_handles.settings().get("decision_models")) or NANO_DECISIONS_MODEL


async def _ask_provider_decision(prov, state, questions: dict, request_id: str, via: str, rec, first: str, miss: str = "") -> tuple[int, bytes, str]:
    """One decision asked of a registered provider's decision model, with everything that goes with it: the Speeds row (the real provider and model, role Decisions),
    the request record and the card's "answered last". A local provider (Ollama) is held to the local waiting limit, a paid one to the paid limit."""
    model = decision_providers.chosen_model(prov, decision_handles.settings().get("decision_models"))
    try:
        local_setting = float(decision_handles.settings().get("limit_seconds") or 0)
    except (TypeError, ValueError):
        local_setting = 0.0
    limit = decision_wiring.de.limit_for("gpu", local_setting) if prov.local else _paid_limit()
    t0 = time.time()
    status, body, content_type = await prov.ask(model, state, questions, request_id, via, limit)
    if status == 200 and rec is not None:
        try:
            rec.note_usage(json.loads(body).get("usage"))           # before the Speeds row: its cost is worked out from these token counts
        except Exception:
            pass
    await _record_speed(model, time.time() - t0, status == 200, fail_reason=None if status == 200 else "timeout" if status == DECISION_SLOW else f"{prov.name} returned {status}",
                        provider=prov.name, extra_roles=["Decisions"])
    if rec is not None:
        rec.status = status
        if status == 200:
            rec.model, rec.provider = model, prov.name
            try:
                rec.note_usage(json.loads(body).get("usage"))
            except Exception:
                pass
        else:
            rec.model = rec.provider = None            # whoever answers next names itself
    if status == 200:
        decision_handles.note_answer(prov.name, first, miss)
    return status, body, content_type


async def _handle_jev_request(req: "ChatRequest", request: Request, request_id: str):
    """Routes a Jev/Decisions request (see is_jev_request): TypeSafe directly when that key is
    saved; else a stand-in via whatever OTHER real AI the player already has ready
    (call_jev_stand_in); else a clear error. See the "Jev decisions order" comment above.

    TypeSafe's own response returns as a raw Response, deliberately bypassing
    _openai_style_error_handler's usual re-wrapping (see call_typesafe_systemone's own docstring
    for why a real upstream error has to reach SkyrimNet completely unchanged, status included) --
    every stand-in path, by contrast, always returns a genuine 200 JSONResponse-shaped
    {model, answers, usage} envelope OR a clean already-proxy-shaped error, since there's no real
    upstream Jev-shaped response to preserve for any of them. Never touches the chat pipeline below
    this function at all: no tag fixer, no full-reply logging, no OpenAI-shaped usage translation
    -- none of that means anything for an answer that isn't dialogue.

    Every path records the Speeds-tab entry with extra_roles=["Decisions"] -- not just relying on
    SkyrimNet's own yaml naming the recorded model as its "decisions" variant, which only happens
    to be true for TypeSafe (recorded under req.model, "typesafe/jev-1.13" or whatever
    SkyrimNet actually sent, matching its own yaml) and is NEVER true for a stand-in recorded under
    some OTHER provider's real model id (see the item's own "never mistaken for Jev" instruction --
    forcing the role directly is what actually guarantees that, rather than hoping the model name
    happens to line up)."""
    model = req.model or f"{TYPESAFE_PREFIX}jev-latest"
    questions = req.questions or {}
    start = time.time()
    rec = request_facts.current()
    via = ""
    try:
        via = request.url.path
    except Exception:
        pass
    if rec is not None:
        rec.kind, rec.path, rec.questions = "decision", rec.path or via, tuple(str(k) for k in questions)
        rec.model = _resolve_typesafe_model(model) if typesafe_api_key else None      # the model really used (TypeSafe's own id)
    await decision_providers.refresh(wait=False)             # Ollama / Nano-GPT decision models: read once (a moment at most), then re-read in the background; a decision never waits for it
    order = decision_handles.order(req.model, bool(typesafe_api_key), bool(nano_api_key))         # the local decision model installed: it joins the order (None: exactly as before)
    if order is not None:
        return await _handle_jev_in_order(req, request_id, order, model, questions, start, rec, via)
    ts_slow = False
    if typesafe_api_key:
        status, body, content_type = await call_typesafe_systemone(model, req.state, questions, request_id, via=via, limit=_paid_limit())
        if rec is not None:
            rec.status = status
            try:
                rec.note_usage(json.loads(body).get("usage"))          # TypeSafe's own answer carries {answers, usage}
            except Exception:
                pass
        await _record_speed(
            model, time.time() - start, status == 200,
            fail_reason=None if status == 200 else "timeout" if status == DECISION_SLOW else f"TypeSafe returned {status}", provider="TypeSafe",
            extra_roles=["Decisions"],
        )
        if status != DECISION_SLOW:
            if status == 200:
                decision_handles.typesafe_line = ""
            return Response(content=body, status_code=status, media_type=content_type, headers=request_facts.response_headers(rec))
        ts_slow = True                  # too slow: the next answerer takes the decision
        if rec is not None:
            rec.model = rec.provider = None
    nano_miss = None
    if nano_api_key:
        status, body, content_type = await _ask_nano_decision(req.state, questions, request_id, via, rec, "nano")
        if status == 200:
            return Response(content=body, status_code=status, media_type=content_type, headers=request_facts.response_headers(rec))
        nano_miss = status
    stand_in = await call_jev_stand_in(model, req.state, questions, request_id, via=via, skip=("Nano-GPT",) if nano_miss else ())
    if stand_in is None:
        if ts_slow:
            _note_typesafe_slow(False, request_id)
        if nano_miss:
            _note_nano_miss(nano_miss, False, request_id)
            raise HTTPException(status_code=nano_miss if 400 <= nano_miss < 598 else 502, detail=decision_handles.nano_line)
        if ts_slow:
            raise HTTPException(status_code=502, detail=decision_handles.typesafe_line)
        raise HTTPException(
            status_code=500,
            detail="No TypeSafe key found, and no other AI is ready. Add a key on the dashboard.",
        )
    status, body, real_model, display_name = stand_in
    if ts_slow:
        _note_typesafe_slow(status == 200, request_id)
    if nano_miss:
        _note_nano_miss(nano_miss, status == 200, request_id)
        if rec is not None:
            rec.stand_in = "Nano-GPT"
    if rec is not None:
        rec.status = status
    await _record_speed(
        real_model, time.time() - start, status == 200,
        fail_reason=None if status == 200 else f"{display_name} returned {status}", provider=display_name,
        extra_roles=["Decisions"],
    )
    return JSONResponse(content=body, status_code=status, headers=request_facts.response_headers(rec))


async def _second_opinion(state, questions: dict, model: str, request_id: str, via: str, rec, order: list, res: dict, label: str, first: str = "local", first_name: str = "local model",
                          step: str = "local"):
    """The local model answered; was it sure? (2026-10-05) When its least sure choice or yes/no answer is under the player's "sure enough" percentage, the saved TypeSafe or
    Nano-GPT key is asked too (in the order's own order, each within the paid time limit) and its answer is used instead. Returns (the Response to send, None) when a paid
    answerer answered; (None, the percentage) when it was unsure but nobody paid answered (the local answer stands, and SkyrimNet never waits longer than the limit
    for this extra step); (None, None) when it was sure enough, the setting is off, or there is no key to ask. Both calls are recorded in Speeds, each under its own provider."""
    s = decision_handles.settings()
    below = order[order.index(step) + 1:] if step in order else []
    paid = [x for x in below if x in ("typesafe", "nano") or (x in decision_providers.REGISTRY and decision_providers.REGISTRY[x].ask is not None)]          # the models below the one that answered
    low = decision_test.lowest_confidence(questions, res["answers"])
    if not s.get("ask_when_unsure", True) or not paid or low is None or low >= s["sure_percent"] / 100:
        return None, None
    pct = round(low * 100)
    saved = (rec.model, rec.provider, rec.status) if rec is not None else None
    for step2 in paid:
        t0 = time.time()
        if step2 == "typesafe":
            status, body, content_type = await call_typesafe_systemone(model, state, questions, request_id, via=via, limit=_paid_limit())
            await _record_speed(model, time.time() - t0, status == 200, fail_reason=None if status == 200 else "timeout" if status == DECISION_SLOW else f"TypeSafe returned {status}",
                                provider="TypeSafe", extra_roles=["Decisions"])
            if status == 200:
                if rec is not None:
                    rec.status, rec.model = status, _resolve_typesafe_model(model)
                    rec.provider = "TypeSafe"
                    try:
                        rec.note_usage(json.loads(body).get("usage"))
                    except Exception:
                        pass
                decision_handles.typesafe_line = ""
                who = "TypeSafe"
            else:
                if status == DECISION_SLOW:
                    _note_typesafe_slow(True, request_id)
                continue
        elif step2 != "nano" and step2 in decision_providers.REGISTRY:
            prov = decision_providers.REGISTRY[step2]
            status, body, content_type = await _ask_provider_decision(prov, state, questions, request_id, via, rec, first, "unsure")
            if status != 200:
                if prov.miss_line is not None:
                    _note_provider_miss(prov, status, True, request_id)
                continue
            decision_handles.provider_lines.pop(prov.id, None)
            who = prov.name
        else:
            status, body, content_type = await _ask_nano_decision(state, questions, request_id, via, rec, first)
            if status != 200:
                _note_nano_miss(status, True, request_id)
                continue
            who = "Nano-GPT"
        logger.info(f"[{request_id}] The {first_name} was only {pct}% sure, so {who} answered")
        decision_handles.note_answer(who, first, "unsure")
        decision_handles.last_answer.update(unsure=low, line=f"The {first_name} wasn't sure, so {who} stepped in.")
        return Response(content=body, status_code=200, media_type=content_type, headers=request_facts.response_headers(rec)), None
    if rec is not None and saved is not None:
        rec.model, rec.provider, rec.status = saved          # a paid step that failed cleared them: the local answer stands
    logger.info(f"[{request_id}] The {first_name} was only {pct}% sure and no paid answer came back, so its answer stands")
    return None, pct


async def _handle_jev_in_order(req: "ChatRequest", request_id: str, order: list, model: str, questions: dict, start: float, rec, via: str):
    """A decision when the local decision model is installed (2026-10-02, Local decisions step 1): the answerers are tried in `order` (local model, TypeSafe key,
    stand-in AI; the player can put TypeSafe first). The first to give a real answer wins; one that is stopped, too slow (the waiting limit) or fails passes the
    decision on, and the log says who answered. `local/decisions` as the model name means the local model and nothing else. A TypeSafe answer that was an error is
    kept: if nobody after it answers, SkyrimNet gets TypeSafe's own error unchanged, as it always did."""
    typesafe_reply = None
    tried_local = False
    nano_miss = None
    ts_slow = False
    prov_miss: dict = {}                # a paid registered provider (Perplexity) that did not answer this decision: provider id -> status

    def note_prov_misses(another_answered: bool) -> None:
        for pid, st in prov_miss.items():
            _note_provider_miss(decision_providers.REGISTRY[pid], st, another_answered, request_id)

    def answered_by_other() -> None:
        if nano_miss:
            _note_nano_miss(nano_miss, True, request_id)
        if ts_slow:
            _note_typesafe_slow(True, request_id)
        note_prov_misses(True)
    for step in order:
        if step == "local":
            tried_local = True
            t0 = time.time()
            res = await decision_handles.try_local(req.state, questions, request_id, via, received=start if step == order[0] else t0)      # (on another device the limit counts from receipt)
            if res is not None:
                label = decision_handles.active().model_label()
                if rec is not None:
                    rec.model, rec.status = label, 200
                    rec.note_usage(res["usage"])
                await _record_speed("local/decisions", time.time() - t0, True, provider="Local decision model", extra_roles=["Decisions"])
                unsure_answer, unsure_pct = await _second_opinion(req.state, questions, model, request_id, via, rec, order, res, label, first=order[0], step="local")
                _record_decision(decision_test.lowest_confidence(questions, res["answers"]), unsure_answer is not None or unsure_pct is not None,
                                 ((decision_handles.last_answer or {}).get("by") if unsure_answer is not None else "local"))
                if unsure_answer is not None:
                    return unsure_answer
                decision_handles.note_answer("local", order[0], order[0] if order[0] != "local" else "")
                if unsure_pct is not None:
                    decision_handles.last_answer.update(unsure=unsure_pct / 100, line="The local model wasn't sure, but the next model took too long, so the local answer stands.")
                answered_by_other()
                return JSONResponse(content={"model": req.model or "local/decisions", "answers": res["answers"], "usage": res["usage"]},
                                    headers=request_facts.response_headers(rec))
            if decision_handles.last_reason == "too_slow":
                await _record_speed("local/decisions", time.time() - t0, False, fail_reason="busy", provider="Local decision model", extra_roles=["Decisions"])
                if rec is not None:
                    rec.model = rec.provider = None            # the next answerer's line names itself
            if order == ["local"]:
                raise HTTPException(status_code=503, detail="The local model couldn't answer. Is it running? (Local device tab)")
        elif step == "typesafe":
            t0 = time.time()
            status, body, content_type = await call_typesafe_systemone(model, req.state, questions, request_id, via=via, limit=_paid_limit())
            if rec is not None:
                rec.status, rec.model = status, _resolve_typesafe_model(model)
                try:
                    rec.note_usage(json.loads(body).get("usage"))
                except Exception:
                    pass
            await _record_speed(model, time.time() - t0, status == 200, fail_reason=None if status == 200 else "timeout" if status == DECISION_SLOW else f"TypeSafe returned {status}",
                                provider="TypeSafe", extra_roles=["Decisions"])
            if status == 200:
                decision_handles.typesafe_line = ""
                decision_handles.note_answer("TypeSafe", order[0], decision_handles.last_reason if tried_local else ("nano" if nano_miss else ""))
                answered_by_other()
                return Response(content=body, status_code=status, media_type=content_type, headers=request_facts.response_headers(rec))
            if status == DECISION_SLOW:
                ts_slow = True
                if rec is not None:
                    rec.model = rec.provider = None
                logger.info(f"[{request_id}] TypeSafe was too slow, passing the decision on")
                continue
            typesafe_reply = (status, body, content_type)
            logger.info(f"[{request_id}] TypeSafe didn't give an answer ({status}), passing the decision on")
        elif step in decision_providers.REGISTRY and decision_providers.REGISTRY[step].ask is not None:
            prov = decision_providers.REGISTRY[step]
            status, body, content_type = await _ask_provider_decision(prov, req.state, questions, request_id, via, rec, order[0],
                                                                       decision_handles.last_reason if tried_local else "nano" if nano_miss else next(iter(prov_miss), ""))
            if status == 200:
                decision_handles.provider_lines.pop(prov.id, None)
                answer = json.loads(body)             # the same "try the next model down" step the local model has (only if this one reports how sure it is)
                unsure_answer, unsure_pct = await _second_opinion(req.state, questions, model, request_id, via, rec, order, answer, prov.name, first=order[0], first_name=f"{prov.name} model", step=step)
                if unsure_answer is not None:
                    return unsure_answer
                if unsure_pct is not None:
                    decision_handles.last_answer.update(unsure=unsure_pct / 100, line=f"The {prov.name} model wasn't sure, but the next model took too long, so its answer stands.")
                answered_by_other()
                return Response(content=body, status_code=200, media_type=content_type, headers=request_facts.response_headers(rec))
            logger.info(f"[{request_id}] {prov.name} didn't give an answer ({status}), passing the decision on")
            if prov.miss_line is not None:
                prov_miss[prov.id] = status
        elif step == "nano":
            status, body, content_type = await _ask_nano_decision(req.state, questions, request_id, via, rec, order[0], decision_handles.last_reason if tried_local else "")
            if status == 200:
                note_prov_misses(True)          # Perplexity (ahead of Nano-GPT) said no: the card says Nano-GPT answered instead
                return Response(content=body, status_code=status, media_type=content_type, headers=request_facts.response_headers(rec))
            nano_miss = status
            logger.info(f"[{request_id}] Nano-GPT didn't give an answer ({status}), passing the decision on")
        else:
            t0 = time.time()
            stand_in = await call_jev_stand_in(model, req.state, questions, request_id, via=via, skip=("Nano-GPT",) if nano_miss else ())
            if stand_in is None:
                continue
            status, body, real_model, display_name = stand_in
            if rec is not None:
                rec.status, rec.model = status, real_model
                rec.stand_in = "the local decision model" if tried_local and not typesafe_reply and not nano_miss else ("Nano-GPT" if nano_miss else "TypeSafe")
            await _record_speed(real_model, time.time() - t0, status == 200, fail_reason=None if status == 200 else f"{display_name} returned {status}",
                                provider=display_name, extra_roles=["Decisions"])
            if status != 200 and typesafe_reply:
                break
            if status != 200 and ts_slow:
                _note_typesafe_slow(False, request_id)
            if status == 200:
                decision_handles.note_answer(display_name, order[0], "nano" if nano_miss else "typesafe" if typesafe_reply else decision_handles.last_reason)
                answered_by_other()
            elif nano_miss:
                _note_nano_miss(nano_miss, False, request_id)
            if status != 200:
                note_prov_misses(False)
            return JSONResponse(content=body, status_code=status, headers=request_facts.response_headers(rec))
    if ts_slow:
        _note_typesafe_slow(False, request_id)
    if nano_miss:
        _note_nano_miss(nano_miss, False, request_id)
    note_prov_misses(False)
    if typesafe_reply:
        status, body, content_type = typesafe_reply
        return Response(content=body, status_code=status, media_type=content_type, headers=request_facts.response_headers(rec))
    raise HTTPException(status_code=500, detail="The local model couldn't answer, and no other AI is ready. Add a key on the Providers tab.")


# SkyrimNet Beta 26 rc2 (2026-10-02) gave every provider an "API Style": Chat Completions, Decisions or System One. The Decisions and System One styles send
# the same {model, questions, state} body the Jev job always did, but to the Endpoint the player typed (SkyrimNet's own built-in decisions provider is
# `https://openrouter.ai/api/alpha/decisions`; TypeSafe's own is `https://api.typesafe.ai/v1/systemone`), not to /v1/chat/completions. So these paths reach the
# same Jev handling as the rc1 route does, and a player can point either style at MultiProxy's address plus any of them. (The recording stand-in,
# scripts/decisions_capture_standin.py, settles which path rc2 really uses; the list is the likely ones, and the catch-all at the bottom of this file takes a
# decisions-shaped body on any other path rather than refuse it.)
JEV_PATHS = ("/v1/systemone", "/v1/decisions", "/systemone", "/decisions", "/alpha/decisions", "/api/alpha/decisions")


async def _jev_from_body(request: Request):
    request_id = uuid.uuid4().hex[:8]
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or body.get("questions") is None or body.get("messages"):
        raise HTTPException(status_code=400, detail="That isn't a decisions request (it has no questions).")
    try:
        req = ChatRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Couldn't read the decisions request ({e.__class__.__name__}).")
    _begin_request(request, request_id, "decision", list((req.model_extra or {}).keys()))
    try:
        # (Beta 28 cancels lines it no longer needs: a decision SkyrimNet hung up on stops where it is, so the AI is not left working on an answer nobody reads)
        return await _await_unless_disconnected(request, _handle_jev_request(req, request, request_id))
    except _ClientGone:
        logger.info(f"[{request_id}] SkyrimNet hung up before the decision was ready -- gave up on it and stopped the AI")
        return Response(status_code=499)
    finally:
        _in_flight.leave(request_id)


def _content_as_text(content: Union[str, list, None]) -> str:
    """Best-effort plain-text rendering of a ChatMessage's content, for the places that only ever
    needed an approximate string -- a system prompt handed to the bare-Claude body template, the
    char/4 token-count estimate fallback, and the CLI providers (Codex/Antigravity) that flatten
    the whole conversation into one plain-text prompt. Never used for what's actually forwarded to
    an OpenAI-compatible provider (that keeps the original str-or-parts shape untouched, unaltered
    by this function, so a real image_url part still reaches GLM/etc. intact) -- only
    for call sites that were already assuming a plain string long before Vision existed."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, list):
        return " ".join(
            _cc.flat_text(part.get("text")) for part in content
            if isinstance(part, dict) and part.get("type") in ("text", None)
        )
    return "" if content is None else str(content)


# Prompt-caching format (2026-10-21, SkyrimNet Beta 27's "Cache Enabled" prompts): a message's content is a list of parts and some carry a `cache_control` marker.
# Only the Claude lane and the two gateways that pass Anthropic's markers on (Kilo, Nano-GPT) get them; every other provider is sent plain parts, and the CLI lanes plain text.
CACHE_MARKER_LANES = ("claude", "kilo", "nano")


def _clean_content(content, keep_markers: bool):
    """A message's content with every text part a plain string and no empty text part. A string stays exactly as it was; a list stays a list (pictures and other parts untouched);
    a dict part, a bare string in the list or null are put right. Cache markers are kept or taken off. Returns (content, number of parts fixed)."""
    if isinstance(content, str):
        return content, 0
    if content is None:
        return "", 1
    fixed = 0
    parts = [content] if isinstance(content, dict) else content
    if not isinstance(parts, (list, tuple)):
        return str(content), 1
    if parts is not content:
        fixed += 1
    out = []
    for p in parts:
        if p is None:
            fixed += 1
            continue
        if isinstance(p, str):
            fixed += 1
            p = {"type": "text", "text": p}
        elif not isinstance(p, dict):
            fixed += 1
            p = {"type": "text", "text": str(p)}
        if p.get("type") in ("text", None) and ("text" in p or p.get("type") == "text"):
            q = dict(p)
            q.setdefault("type", "text")
            if not isinstance(q.get("text"), str):
                fixed += 1
                q["text"] = _cc.flat_text(q.get("text"))
            if not q["text"].strip():
                fixed += 1
                continue
            p = q
        if "cache_control" in p:
            marker = _cc.clean_marker(p["cache_control"])
            if not keep_markers or marker is None:
                p = {k: v for k, v in p.items() if k != "cache_control"}
            elif p["cache_control"] != marker:
                p = {**p, "cache_control": marker}
        out.append(p)
    return (out if out else ""), fixed


def _cap_message_markers(messages: list, limit: int = _cc.MAX_CACHE_MARKERS) -> tuple:
    """At most `limit` cache markers across the chat, the last ones kept. Returns (found, kept)."""
    parts = [p for m in messages if isinstance(m.get("content"), list) for p in m["content"] if isinstance(p, dict) and "cache_control" in p]
    for p in parts[:max(0, len(parts) - limit)]:
        del p["cache_control"]
    return len(parts), min(len(parts), limit)


def _merge_message_content(a, b):
    """Merges two consecutive same-role messages' content (see chat_completions' own "Merge
    consecutive same-role messages" step). Plain string + string keeps the exact prior behavior
    (joined with a blank line). If either side is multimodal (a list of OpenAI-style content
    parts -- e.g. Vision's text + image_url), both sides are normalized to a list of parts and
    concatenated instead of string-concatenated, so an image_url part is never coerced into text
    or lost."""
    if isinstance(a, str) and isinstance(b, str):
        return a + "\n\n" + b
    a_parts = _clean_content([a] if isinstance(a, str) else a, True)[0]
    b_parts = _clean_content([b] if isinstance(b, str) else b, True)[0]
    return (a_parts if isinstance(a_parts, list) else []) + (b_parts if isinstance(b_parts, list) else [])


# --- Catch broken AI replies (queue: catch-broken-replies, 2026-09-29) ---
#
# Step 1 (required before building this, per the task) found no reliable machine-readable signal
# anywhere: zero occurrences of response_format, json_schema, or a use_structured_outputs flag across
# 40+MB of SkyrimNet's own real captured request logs (openrouter_input*.log, six files, 1656 unique
# real requests). The only real signal that exists is the request's OWN prompt text explicitly asking
# for JSON -- confirmed against every real JSON-expecting job found (memory, sever_background,
# intel_story_dm, agencyengine_impulse/resolve, one CharacterProfileGeneration variant, one
# gamemaster_evaluation variant): every one of them says so directly ("Respond ONLY with a JSON
# object", "Return raw JSON only", "JSON only:", "must be a single JSON object", etc.), and not one of
# the confirmed PROSE jobs in that same real log set (meta, vision, combat, action_evaluation, the
# other gamemaster_evaluation/sever_background/CharacterProfileGeneration variants, plain dialogue)
# ever mentions "json" at all -- including one that explicitly warns "No JSON, no explanation" a few
# words after its own "Output ONLY the..." (see the same-sentence proximity check below for why that
# one doesn't false-positive). See TECHNICAL.md for the full counts.
_JSON_EXPECTATION_RE = re.compile(
    r"json\s+object|json\s+only|json\s+response|```json"
    r"|only\b[^.\n]{0,40}\bjson\b|\bjson\b[^.\n]{0,40}\bonly\b",
    re.I,
)


def _expects_json_reply(system_prompt: Optional[str], messages: list) -> bool:
    """True when THIS request's own prompt explicitly asks for a JSON reply (see the block comment
    above for why prompt text is the only real signal, and why the match is deliberately narrow --
    "json" and a directive word must land in the same sentence, no "." in between, which is exactly
    what keeps "Output ONLY the impression text. No JSON, no explanation" from matching: there's a
    sentence boundary between "ONLY" and "JSON". Verified against 1656 real captured requests with
    zero false positives in either direction."""
    text = system_prompt or ""
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        text += " " + _content_as_text(content)
    return bool(_JSON_EXPECTATION_RE.search(text))


def _strip_code_fence(text: str) -> str:
    """The single biggest real repairable case (queue: catch-broken-replies) -- 297 of ~410 real
    JSON-expecting replies sampled came back wrapped in a ```json fence (memory alone: 262 of 263,
    virtually every one, regardless of model -- almost certainly because the job's own prompt shows
    a ```json example block and models echo the framing literally). Searches rather than anchors to
    the start/end, so a fence anywhere in the reply is found even if surrounding prose exists too."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.S | re.I)
    return m.group(1).strip() if m else text


def _trim_to_json_span(text: str) -> str:
    """Trims to the first {/[ through the LAST matching }/] -- removes preamble/postamble text
    around an otherwise-valid JSON body without touching anything inside it."""
    first = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if first is None:
        return text
    closer = "}" if text[first] == "{" else "]"
    last = text.rfind(closer)
    return text[first:last + 1] if last > first else text


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _repair_json_reply(text: str) -> tuple:
    """(status, text_to_use, [what was fixed]). status is "valid" (unchanged), "repaired" (safe to
    use text_to_use), or "broken" (give up, text_to_use is the original, unrepaired text). Each step
    only strips wrapper noise around the model's own JSON -- code fences, then text before/after the
    JSON span, then trailing commas -- and stops the moment the result actually parses. Never invents
    or changes a field/value; a reply broken any other way (a raw control character inside a string,
    a genuinely truncated object) is correctly reported "broken", not guessed at."""
    original = text or ""
    candidate = original.strip()
    try:
        json.loads(candidate)
        return "valid", original, []
    except (json.JSONDecodeError, ValueError):
        pass

    applied = []
    for step_name, step_fn in (
        ("removed code fences", _strip_code_fence),
        ("removed text before/after the JSON", _trim_to_json_span),
        ("removed trailing commas", _strip_trailing_commas),
    ):
        fixed = step_fn(candidate)
        if fixed == candidate:
            continue
        candidate = fixed
        applied.append(step_name)
        try:
            json.loads(candidate)
            return "repaired", candidate, applied
        except (json.JSONDecodeError, ValueError):
            continue

    return "broken", original, applied


# How long, in total, this request may spend on the original call PLUS one retry before giving up on
# a broken JSON reply -- SkyrimNet's own real requests never send a timeout of their own (confirmed:
# zero occurrences of a "timeout" field across every real captured request), so this is the one and
# only budget, matching the task's own "keep the whole thing under ~10s" instruction.
_JSON_RETRY_BUDGET_SECONDS = 10.0


class _StillBrokenJsonReply(Exception):
    """Raised by _check_and_repair_json_reply when a JSON-expecting reply is still unusable even
    after one retry (or there was no time left to try) -- caught right where it's raised, in each of
    _chat_completions_impl's two provider branches, to record a real failure (fail_reason="broken
    reply") and turn it into the real HTTP error SkyrimNet's own fallback/smart-routing needs to see,
    rather than handing back broken JSON dressed up as a 200."""


async def _check_and_repair_json_reply(
    response: str, wants_json: bool, retry_fn, request: Request, request_id: str, model: str,
    request_start: float,
) -> tuple:
    """For a request whose own prompt asked for JSON (see _expects_json_reply), returns
    (text_to_hand_SkyrimNet, reply_repair_label, extra_failure) after checking/repairing/retrying
    `response` per FIX_BROKEN_REPLIES's rules. Anything else (wants_json False) is returned
    completely untouched with reply_repair=None, extra_failure=False -- a no-op for ordinary
    prose/dialogue replies.

    reply_repair (for the caller's own, real, successful _record_speed call) is "fine" (valid as
    sent), "repaired" (safe wrapper-noise removed), "asked_again" (broken, retried once, the retry
    came out usable), or None (nothing to report -- either the setting is off, or wants_json was
    False to begin with).

    extra_failure is True only with the setting OFF and a reply that wasn't already valid: the text
    is returned completely UNTOUCHED (no repair, no retry -- SkyrimNet gets the exact 200 it always
    would have, unaffected by this feature existing at all) and the caller must ALSO fire one extra,
    accounting-only _record_speed(success=False, fail_reason="broken reply") alongside its own real
    success recording, so FixBrokenReplies=false still answers "how often does this happen" without
    changing anything the player would notice.

    Raises _StillBrokenJsonReply -- never returns -- only with the setting ON, when even a retry (or
    no time left for one) didn't produce something usable: the one real case where SkyrimNet must
    receive an actual error instead of a 200, per the task's own explicit rule."""
    if not wants_json:
        return response, None, False

    status, text, applied = _repair_json_reply(response)
    if status == "valid":
        return response, "fine", False

    if not FIX_BROKEN_REPLIES:
        # Classify only -- never touch the text, never retry, never fail the real HTTP response.
        return response, None, True

    if status == "repaired":
        logger.debug(f"[{request_id}] {model} reply wasn't valid JSON -- fixed it ({', '.join(applied)})")
        return text, "repaired", False

    # status == "broken": ask the same AI once more, but only if there's genuinely time left.
    if retry_fn is None or time.time() - request_start >= _JSON_RETRY_BUDGET_SECONDS:
        logger.warning(f"[{request_id}] {model} reply wasn't valid JSON and couldn't be fixed -- no time left to ask again")
        raise _StillBrokenJsonReply()

    logger.info(f"[{request_id}] {model} reply wasn't valid JSON -- asked again")
    try:
        retry_response, _retry_usage = await _await_unless_disconnected(request, retry_fn())
    except _ClientGone:
        raise
    except Exception:
        raise _StillBrokenJsonReply()

    retry_status, retry_text, _retry_applied = _repair_json_reply(retry_response)
    if retry_status in ("valid", "repaired"):
        logger.info(f"[{request_id}] {model} reply was valid JSON after asking again")
        return retry_text, "asked_again", False

    logger.warning(f"[{request_id}] {model} reply still wasn't valid JSON after asking again -- passing the error on")
    raise _StillBrokenJsonReply()


# Field names stripped (recursively, case-insensitive substring match) from a captured request
# before it's written to disk -- defensive, not because SkyrimNet's own request body is expected to
# carry a credential (API keys live in config.json / outbound headers, never in this inbound body),
# but because "expected to be safe" is exactly the reasoning that let a full bearer token get logged
# in full in this same file once before (see _redact_credential's own docstring, 2026-09-13).
_CAPTURE_SECRET_NAME_FRAGMENTS = ("key", "token", "secret", "password", "authorization")


def _is_secret_field_name(name: str) -> bool:
    """A field whose name says it holds a secret. "token" is one, but "tokens" (max_tokens, completion_tokens, ...) is a count: the very number the job
    recognition needs, which the filter used to hide (routing step 0 found it)."""
    n = str(name).lower()
    if "token" in n.replace("tokens", ""):
        return True
    return any(f in n for f in _CAPTURE_SECRET_NAME_FRAGMENTS if f != "token")


def _strip_capture_secrets(value):
    """Recursively walks a JSON-shaped value (dict/list/primitive) and replaces any dict value
    whose key name is a secret field name (see _is_secret_field_name) with "***REDACTED***"."""
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if _is_secret_field_name(k) else _strip_capture_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_capture_secrets(v) for v in value]
    return value


def capture_prompt_if_enabled(req: "ChatRequest") -> None:
    """Writes a stripped copy of the real request body to CAPTURE_DIR when CapturePrompts=true in
    multiproxy.ini (off by default) -- how tests/fixtures/skyrimnet_shapes.py's synthetic fixtures are
    meant to be replaced with genuine ones once SkyrimNet is actually running (see TECHNICAL.md's
    "Capturing real fixtures" section). Best-effort: a capture failure must never break a real
    request, so this only ever logs a warning, never raises."""
    if not CAPTURE_PROMPTS:
        return
    try:
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        body = _strip_capture_secrets(req.model_dump(mode="json"))
        filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
        with open(os.path.join(CAPTURE_DIR, filename), "w", encoding="utf-8") as f:
            json.dump(body, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"CapturePrompts: failed to write a captured request ({e}) -- request itself was not affected")


def capture_rejected_request_if_enabled(raw_body: bytes) -> None:
    """Sibling to capture_prompt_if_enabled, for the one shape it structurally cannot cover: a
    request SkyrimNet sends that our own ChatRequest model rejects outright (a 422 -- unknown
    fields, or missing ones ChatRequest requires) before the route body, so req.model_dump() never
    exists to capture. Used by _ErrorLogMiddleware below for exactly that case (queue: let-jev-
    decisions-through, 2026-09-29 -- discovering SkyrimNet's real Decisions-job body needed this,
    but it's general: any future request shape our model doesn't recognize yet lands here the same
    way). Same CapturePrompts flag, same CAPTURE_DIR, same secret-stripping, same best-effort
    contract as capture_prompt_if_enabled -- just parses the raw bytes as JSON itself since there's
    no validated model to dump. A body that isn't even valid JSON is written verbatim as text
    instead of silently dropped, so a truly malformed request still leaves a trace."""
    if not CAPTURE_PROMPTS:
        return
    try:
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        filename = f"rejected_{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
        try:
            body = _strip_capture_secrets(json.loads(raw_body.decode("utf-8")))
            text = json.dumps(body, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            text = f"(not JSON, {len(raw_body):,} bytes, not shown)" if _looks_binary(raw_body) else raw_body.decode("utf-8", errors="replace")
        with open(os.path.join(CAPTURE_DIR, filename), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        logger.warning(f"CapturePrompts: failed to write a captured rejected request ({e}) -- request itself was not affected")


def _skyrim_watcher(process_names: list[str], poll_interval: int = 10) -> None:
    """Background thread: shuts down proxy when Skyrim exits."""
    names_lower = [n.lower() for n in process_names]

    def any_running() -> bool:
        try:
            for name in names_lower:
                result = hidden_process.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                if name in result.stdout.lower():
                    return True
        except Exception:
            return True  # fail safe: don't shut down on a check error
        return False

    # Skyrim is usually already running when the proxy starts, but allow a 2-minute
    # grace period in case the proxy was pre-launched before the game.
    logger.info("SkyrimWatcher: waiting for Skyrim process...")
    detected = False
    for _ in range(12):  # 12 × 10 s = 2 minutes
        if not AUTO_CLOSE_WITH_SKYRIM:
            logger.info("SkyrimWatcher: turned off in Settings -- watcher exiting")
            return
        if any_running():
            logger.info("SkyrimWatcher: Skyrim detected, monitoring for exit")
            detected = True
            break
        time.sleep(10)

    if not detected:
        logger.warning("SkyrimWatcher: Skyrim not detected within 2 minutes — watcher exiting")
        return

    while True:
        time.sleep(poll_interval)
        if not AUTO_CLOSE_WITH_SKYRIM:
            logger.info("SkyrimWatcher: turned off in Settings -- watcher exiting")
            return
        if not any_running():
            logger.info("SkyrimWatcher: Skyrim process ended — shutting down proxy")
            offloop_log.drain_all()
            speed_store.stop()      # write the Speeds rows still queued before the process ends
            os._exit(0)


async def _claude_path_loop() -> None:
    """Looks for the `claude` program again every 20 s (the shared lookup, off the event loop), so one installed or updated after MultiProxy started needs no restart."""
    global CLAUDE_PATH
    while True:
        await asyncio.sleep(poll_cache.WHICH_TTL)
        try:
            poll_cache.forget_which("claude")          # (so this is a fresh look, not the remembered answer)
            found = await asyncio.to_thread(poll_cache.cached_which, "claude")
        except Exception:
            continue
        if found != CLAUDE_PATH:
            logger.info(f"Claude CLI: {found or 'no longer found'}")
            CLAUDE_PATH = found


@asynccontextmanager
async def lifespan(app):
    logger.info(_build_info.version_line(PROXY_VERSION, BUILD_STAMP))          # the first line of the log: which version and build wrote the rest
    runner = await start_interceptor()

    # Persistent outgoing HTTP session
    auth.session = aiohttp.ClientSession()

    # Warm up Claude auth at startup if possible (single-flight safe) -- but Claude is optional, so
    # a failure here must never take the whole proxy down. Real bug, reported by a user
    # (2026-09-13): this used to `await` unguarded, so a broken Claude subscription (e.g. "Your
    # organization has disabled Claude subscription access") raised out of `lifespan` before
    # reaching `try: yield`, which crashed startup entirely AND skipped the `finally` below,
    # leaking `auth.session`. Every other configured provider works regardless of Claude's state.
    try:
        await auth.ensure_ready(force=True, timeout=60)
    except Exception as e:
        logger.warning(
            f"Claude auth not available at startup ({e}) -- every other configured provider still "
            "works normally. Claude-routed requests will return a clean error until this is fixed."
        )
    try:
        await _fetch_claude_models(force=True)  # so the default-model pick knows the newest Sonnet
    except Exception as e:
        logger.info(f"Couldn't fetch Claude's model list at startup ({e}) -- using the built-in default")

    # Start-up looks at the two subscription programs' model lists once, in the background; after that Refresh or the 15-minute cache does.
    if _agy_path():
        asyncio.ensure_future(_fetch_agy_models())
    if _grok_path():
        asyncio.ensure_future(_fetch_grok_models())

    _ensure_skyrim_watcher()
    _write_endpoint_pointer()
    update_task = asyncio.create_task(_update_check_loop())
    keepwarm_task = asyncio.create_task(_keepwarm_loop())
    claude_path_task = asyncio.create_task(_claude_path_loop())
    lag_probe = loop_lag.start_from_env(logger.warning)       # opt-in (MULTIPROXY_LOOP_LAG_MS): says where anything that blocks the server is

    if OPEN_BROWSER_ON_START:
        webbrowser.open(f"http://127.0.0.1:{MAIN_PORT}")
        logger.info("Opened the dashboard in your default browser (OpenBrowserOnStart=true)")

    # Local models (2026-09-30): kill any engine a crashed earlier run left behind, then start every
    # downloaded model in the background (loading can take a minute, so startup doesn't wait for it)
    # -- they're warm by the time the game asks, and stopped again in the `finally` below.
    await asyncio.to_thread(local_svc.cleanup_leftovers)
    local_start_task = asyncio.create_task(local_svc.start_all())
    asyncio.ensure_future(voice_handles.autostart())      # Higgs voice's "Start with this device" (the Voice tab's menu)
    asyncio.ensure_future(decision_handles.autostart())   # the local decision model's "Start with this device"
    # Other devices: read this device's graphics card in the background and keep every Courier device's live connection and
    # the Online/Offline dots fresh. An older "model server" install is a normal MultiProxy now: its leftover
    # model-server / sharing settings are dropped (the Courier is what goes on a spare device).
    _retire_model_server_settings()
    asyncio.create_task(local_svc.gpu())
    gpu_monitor_svc.start()
    graphics_guard.start()
    remote_svc.start_polling()
    if not _LOCAL_ATEXIT_SET:
        _LOCAL_ATEXIT_SET.append(True)
        atexit.register(lambda: local_svc.stop_all_sync())

    try:
        yield
    finally:
        update_task.cancel()
        keepwarm_task.cancel()
        claude_path_task.cancel()
        if lag_probe is not None:
            await lag_probe.stop()
        local_start_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await local_start_task
        await graphics_guard.stop()
        await gpu_monitor_svc.stop()
        await remote_svc.stop_polling()
        await local_svc.stop_all()
        await voice_handles.engine.close()
        await decision_handles.engine.close()
        if auth.session:
            await auth.session.close()
            auth.session = None
        await runner.cleanup()
        await asyncio.to_thread(speed_store.stop)       # the Speeds rows still queued are written, then the file is closed



# --- Settings page (2026-10-01): the cog's page. Routes the tray app (a later item) reuses for the same two settings. ---
# Settings and where they live:
#   Start and stop with Skyrim -> multiproxy.ini [General] AutoCloseWithSkyrim (today's setting; the watcher below follows it LIVE now)
#   Start with Windows         -> config.json "start_with_windows" (stored for the tray app, which does the real work; shown disabled until it exists)
#   Port SkyrimNet connects to -> config.json "main_port" (MULTIPROXY_PORT in the environment still wins, as it always did)
# `MULTIPROXY_TRAY=1` in the environment is how the tray app will say "I started this copy" (a run from a terminal doesn't set it).
UPDATE_REPO = "awesmdiver/skyrimnet-multiproxy"
UPDATE_CHECK_EVERY_SECONDS = 24 * 3600
RESERVED_PORT_MESSAGE = "MultiProxy is using port {port}. Pick a different one."
_update_state: dict = {"state": "unknown", "latest": None, "url": PLUGIN_DOWNLOAD_URL, "checkedAt": None}
_skyrim_watcher_thread: Optional[threading.Thread] = None


def _pid_alive(pid: int) -> bool:
    """Is a program with this id still running? (a restarted copy waits for the old one to finish)"""
    try:
        if os.name == "nt":
            out = hidden_process.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
            return f'"{pid}"' in out
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def _ini_set(section: str, key: str, value: str) -> None:
    """Change one `Key = value` line under [section] in multiproxy.ini, keeping every comment (the tray app edits the same file the same way)."""
    settings_files.ini_set(_PROXY_INI, section, key, value)
    _proxy_ini_cfg.read(_PROXY_INI)


def _ini_set_general(key: str, value: str) -> None:
    _ini_set("General", key, value)


# --- Every option from the settings file as a switch (2026-10-01) -------------------------------------------------------------
# multiproxy.ini stays the store (so a hand edit still works and nothing is lost): the Settings page writes it, comments kept, and applies the
# change to the running program at once. Every one of these is read at the moment it's used, so none needs a restart. Not on the page, and still
# read from the file: SkyrimProcess, ReplaceRunningProxy, CapturePrompts (AutoCloseWithSkyrim is the "Start and stop with Skyrim" switch).
SETTINGS_OPTIONS = {          # page key -> (module variable, ini key, section)
    "fixNpcTags": ("FIX_NPC_TAGS", "FixNpcTags"),
    "fixBrokenReplies": ("FIX_BROKEN_REPLIES", "FixBrokenReplies"),
    "fixHiggsVoiceTags": ("FIX_HIGGS_VOICE_TAGS", "FixHiggsVoiceTags"),
    "openBrowserOnStart": ("OPEN_BROWSER_ON_START", "OpenBrowserOnStart"),
    "speedTracking": ("ENABLE_SPEED_TRACKING", "EnableSpeedTracking"),
    "keepLog": ("ENABLE_LOGGING", "EnableLogging"),
    "fullReplies": ("LOG_FULL_REPLIES", "LogFullReplies"),
}
WHOLE_NUMBER_MESSAGE = "Please type a whole number, 0 or more."
SECONDS_MESSAGE = "Please type a whole number of seconds, 1 or more."


def _enable_higgs_voice_tags() -> bool:
    """Installing Higgs voice switches on "My NPCs use the Higgs voice engine" (the Voice tab says so). True when it was off and is on now."""
    global FIX_HIGGS_VOICE_TAGS
    if FIX_HIGGS_VOICE_TAGS:
        return False
    FIX_HIGGS_VOICE_TAGS = True
    _ini_set_general("FixHiggsVoiceTags", "true")
    logger.info("FixHiggsVoiceTags: on (Higgs voice was installed)")
    return True


def _apply_file_logging(on: bool) -> None:
    """Start or stop writing multiproxy.log right now (a fresh file each time it's switched on, like a fresh start)."""
    if _TRAY_MODE:
        return                       # the tray app's server always keeps its (rotating) log: the tray's View logs reads it
    targets = [logging.getLogger(n) for n in ("", "uvicorn", "uvicorn.access")]
    path = os.path.abspath(_LOG_FILE)
    for lg in targets:
        for h in list(lg.handlers):
            if isinstance(h, (logging.FileHandler, offloop_log.OffLoopFileHandler)) and os.path.abspath(getattr(h, "baseFilename", "")) == path:
                lg.removeHandler(h)
                h.close()
    if on:
        h = offloop_log.OffLoopFileHandler(_LOG_FILE, mode="w", encoding="utf-8")
        h.setFormatter(_RedactingFormatter(fmt="%(asctime)s %(levelname)-8s %(message)s"))
        for lg in targets:
            lg.addHandler(h)


PAGE_WIDTHS = ("comfortable", "wide", "full")
DEFAULT_PAGE_WIDTH = "wide"


def _page_width() -> str:
    """How much of the window the dashboard uses (config.json "page_width"); anything unknown reads as the default."""
    w = _load_config().get("page_width")
    return w if w in PAGE_WIDTHS else DEFAULT_PAGE_WIDTH


def _hidden_start_here() -> list:
    """The Start here boxes the player hid (config.json "hidden_start_here"); a fresh install hides nothing."""
    return start_here_ui.clean_names(_load_config().get("hidden_start_here"))


def _limits_state() -> dict:
    return {"providers": [{"id": pid, "label": label, "value": REQUEST_LIMITS.get(pid, 0)} for _ini, pid, label, _d in _REQUEST_LINE_PROVIDERS],
            "queueSeconds": int(QUEUE_TIMEOUT_SECONDS) if float(QUEUE_TIMEOUT_SECONDS).is_integer() else QUEUE_TIMEOUT_SECONDS}


def _get_started_available() -> bool:
    """The "Run Get started again" button only shows once the Get started flow exists (its module is get_started_ui)."""
    import importlib.util
    return importlib.util.find_spec("get_started_ui") is not None


# Keys MultiProxy writes into config.json by itself (nobody chose them). A config with nothing else in it is a first install: Get started opens on its own.
_AUTO_CONFIG_KEYS = {"gpu_monitor", "hidden_start_here", "set_me_up_measure"}


def _get_started_first_run(cfg: dict) -> bool:
    """A first install only: no Get started record, Help never shown, and nothing in the settings file but what MultiProxy wrote itself.
    An update, or anyone who has set anything up, never gets it unasked."""
    if cfg.get("get_started") or cfg.get("help_seen"):
        return False
    return not (set(cfg) - _AUTO_CONFIG_KEYS)


def _mod_ini_lines() -> str:
    """The four lines for the Skyrim mod's own settings file (SkyrimNetMultiProxy.ini), filled in with THIS install's real values."""
    py = sys.executable
    if sys.platform == "win32":
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(pyw):
            py = pyw
    return "\n".join([f"PythonExe={py}", f"ProxyScript={os.path.abspath(__file__)}", f"WorkDir={_settings_dir()}", f"Port={MAIN_PORT}"])


async def _set_me_up_show() -> bool:
    """Set me up (Overview's card, Get started's block) is for a brand-new install: not once a local model, a provider, a paired device or a route exists. Uses what the program already
    knows (the providers list is the Overview's own cached one)."""
    if set_me_up_svc.show_card(False) is False or models_off.is_off(_load_config()):
        return False
    try:
        prov = await poll_cache.shared("overview-providers", lambda v: 5.0, _dashboard_providers, stale_ok=True)
    except Exception:
        prov = []
    already = bool(
        await asyncio.to_thread(local_svc.installed)
        or any(p["state"] == "ok" and p["id"] not in ("local", "remote", "ollama", "lmstudio") for p in prov or [])
        or remote_svc.all()
        or (_routing_settings().get("routes") or {})
        or (_overview_service(decision_handles.active()) or {}).get("installed"))
    return set_me_up_svc.show_card(already)

def _get_started_local_models(cfg: dict) -> tuple:
    """For the last step: the installed local chat models (newest first, as `local/<id>` names), the chat model Set me up installed (`local/<id>`, or "" if none or it is gone),
    and whether Set me up installed the quick-decisions model."""
    try:
        inst = sorted(local_svc.installed(), key=lambda m: m.path.stat().st_mtime if m.path.exists() else 0, reverse=True)
    except Exception:
        inst = []
    names = [{"name": m.model_name, "label": m.name} for m in inst]
    used = cfg.get("set_me_up") if isinstance(cfg.get("set_me_up"), dict) else {}
    chat = f"{LOCAL_PREFIX}{used['chat']}" if used.get("chat") and any(m.id == used["chat"] for m in inst) else ""
    decision = False
    try:
        decision = bool(used.get("decision")) and bool(set_me_up_svc.decisions.installed(used["decision"]))
    except Exception:
        pass
    return names, chat, decision


def _get_started_state() -> dict:
    cfg = _load_config()
    rec = cfg.get("get_started") if isinstance(cfg.get("get_started"), dict) else {}
    try:
        step = max(0, min(4, int(rec.get("step", 0))))
    except (TypeError, ValueError):
        step = 0
    found = bool(_resolve_skyrimnet_dir_cached(skyrim_install_path)) if skyrim_install_path else None
    local_names, smu_chat, smu_decision = _get_started_local_models(cfg)
    return {"done": bool(rec.get("done")), "step": step, "ai": str(rec.get("ai") or ""), "firstRun": _get_started_first_run(cfg),
            "localModels": local_names, "setMeUpChat": smu_chat, "setMeUpDecision": smu_decision,
            "mod": {"detected": bool(PLUGIN_VERSION), "package": _mod_package_path(), "releaseUrl": PLUGIN_DOWNLOAD_URL},
            "skyrimFolder": skyrim_install_path or "", "skyrimnetFound": found, "skyrimnetPick": skyrimnet_pick,
            "iniLines": _mod_ini_lines(), "providerName": SKYRIMNET_PROVIDER_NAME, "port": MAIN_PORT,
            "endpoint": f"http://127.0.0.1:{MAIN_PORT}/v1/chat/completions"}


_skyrimnet_missing_logged: set = set()


def _note_skyrimnet_missing(skyrim_path: str, what: str) -> None:
    """A reader that needs SkyrimNet's files found none under the linked Skyrim folder: say so once in the log, never an empty result with no word."""
    key = (skyrim_path, what)
    if skyrim_path and key not in _skyrimnet_missing_logged:
        _skyrimnet_missing_logged.add(key)
        logger.warning(f"Couldn't read {what}: no SkyrimNet folder was found under {skyrim_path} (checked the usual spot and common Mod Organizer 2 layouts). "
                       "Using Mod Organizer 2? Pick the SkyrimNet folder in Settings.")




def _ensure_skyrim_watcher() -> None:
    """Start the watcher that closes MultiProxy with Skyrim, if the setting is on and it isn't already running."""
    global _skyrim_watcher_thread
    if not AUTO_CLOSE_WITH_SKYRIM or _TRAY_MODE:
        return                       # (in a tray-started copy the tray app decides, from the Skyrim mod's "closed" message: see local_channel.py)
    if _skyrim_watcher_thread is not None and _skyrim_watcher_thread.is_alive():
        return
    _skyrim_watcher_thread = threading.Thread(target=_skyrim_watcher, args=(_SKYRIM_PROCESSES,), daemon=True, name="SkyrimWatcher")
    _skyrim_watcher_thread.start()
    logger.info(f"SkyrimWatcher started (processes: {', '.join(_SKYRIM_PROCESSES)})")


def _started_by_tray() -> bool:
    return os.environ.get("MULTIPROXY_TRAY", "").strip() == "1"


def _tray_app_present() -> bool:
    return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "multiproxy_tray.py"))


def _mod_package_path(folder: Optional[str] = None) -> Optional[str]:
    """The Skyrim mod package the installer ships: `mod/SkyrimNetMultiProxy.zip` in the program folder (the same name every release,
    so a mod manager sees an update). An older install may have only a versioned `SkyrimNetMultiProxy-Mod-<version>.zip`: that is used
    when the fixed name is not there (the newest one if several). None when there isn't one (a zip install, or a mod-manager-only install): the Settings page then
    offers the release page instead. The Open folder / Copy location buttons appear by themselves once the file is there."""
    folder = folder or os.path.join(os.path.dirname(os.path.abspath(__file__)), "mod")
    try:
        listing = os.listdir(folder)
    except OSError:
        return None
    if "SkyrimNetMultiProxy.zip" in listing:
        return os.path.join(folder, "SkyrimNetMultiProxy.zip")
    names = sorted(n for n in listing if re.fullmatch(r"SkyrimNetMultiProxy-Mod-.+\.zip", n))
    return os.path.join(folder, names[-1]) if names else None


def _settings_dir() -> str:
    return os.path.dirname(os.path.abspath(CONFIG_FILE))


def _write_endpoint_pointer() -> None:
    """`endpoint.json` next to config.json: where this MultiProxy answers right now. The Skyrim mod and the tray app read it, so
    a changed port is followed without anyone typing it again."""
    try:
        with open(os.path.join(_settings_dir(), "endpoint.json"), "w", encoding="utf-8") as f:
            json.dump({"url": f"http://127.0.0.1:{MAIN_PORT}", "port": MAIN_PORT, "version": PROXY_VERSION}, f)
    except OSError as e:
        logger.warning(f"Couldn't write endpoint.json ({e})")


def _version_core(v: str) -> tuple:
    """The release number without any beta tail: "4.0.0-beta.1" and "v4.0.0" are both (4, 0, 0)."""
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", v or "")
    nums = [int(p) for p in m.group(1).split(".")] if m else [0]
    return tuple((nums + [0, 0, 0])[:max(3, len(nums))])


def _version_tuple(v: str) -> tuple:
    """Sortable: a beta tail sorts before its own final release and after the release before it (3.1.0 < 4.0.0-beta.1 < 4.0.0-beta.2 < 4.0.0). So 4.0.0 is newer than 3.1.0 and is a final release; a leftover 4.0.0 beta is older than 4.0.0."""
    core = _version_core(v)
    tail = re.sub(r"^\s*[vV]?\d+(?:\.\d+)*", "", v or "").lstrip("-+. ")
    if not tail:
        return core + (1,)
    return core + (0,) + tuple(int(p) for p in re.findall(r"\d+", tail)) + (0,)


async def _fetch_latest_release() -> Optional[dict]:
    """The newest release on GitHub: {"tag", "url"}, or None when it can't be read (offline, rate limited...: always silent)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                             headers={"Accept": "application/vnd.github+json", "User-Agent": "SkyrimNet-MultiProxy"}) as r:
                if r.status != 200:
                    return None
                d = await r.json()
                return {"tag": str(d.get("tag_name") or ""), "url": str(d.get("html_url") or PLUGIN_DOWNLOAD_URL)}
    except Exception:
        return None


async def _check_for_update() -> dict:
    """One update check (at start, daily, and on "Check now"): compares this version with the newest release."""
    rel = await _fetch_latest_release()
    if rel and rel["tag"]:
        _update_state.update(latest=rel["tag"].lstrip("vV"), url=rel["url"], checkedAt=time.time(),
                             state="available" if _version_tuple(rel["tag"]) > _version_tuple(PROXY_VERSION) else "current")
    return dict(_update_state)


async def _update_check_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await _check_for_update()
        except Exception:
            pass
        await asyncio.sleep(UPDATE_CHECK_EVERY_SECONDS)


def _start_with_windows_now(cfg: dict) -> bool:
    """What Windows will actually do at sign-in (the Run entry), when this copy can know; else the saved choice."""
    if _started_by_tray():
        entry = settings_files.run_entry(os.path.dirname(os.path.abspath(__file__)))
        if entry.supported:
            return entry.is_on()
    return bool(cfg.get("start_with_windows"))


def _windows_startup_supported() -> bool:
    """False off Windows (Linux): starting at login is the installer's question and the systemd user service's job there, so the Settings page has no such choice."""
    try:
        return bool(settings_files.run_entry(os.path.dirname(os.path.abspath(__file__))).supported)
    except Exception:
        return sys.platform == "win32"


def _settings_state() -> dict:
    cfg = _load_config()
    tray_here, from_tray = _tray_app_present(), _started_by_tray()
    windows_ok = tray_here and from_tray
    if not tray_here:
        win_note = "This feature is not in this version yet."
    elif not from_tray:
        win_note = "You cannot change this setting because MultiProxy is not running in the background."
    else:
        win_note = ""
    return {
        "startStopWithSkyrim": AUTO_CLOSE_WITH_SKYRIM,
        "skyrimToggleEnabled": bool(PLUGIN_VERSION),   # nothing starts MultiProxy with the game unless the Skyrim mod does
        "skyrimToggleNote": "" if PLUGIN_VERSION else "Needs the Skyrim mod (above).",
        "startWithWindows": _start_with_windows_now(cfg),
        "localModelsOff": models_off.is_off(cfg), "localModelsOffWords": {"title": models_off.WORDS["title"], "hint": models_off.WORDS["hint"], "confirm": models_off.WORDS["confirm"], "confirmGo": models_off.WORDS["confirmGo"]},
        "graphicsGuard": cfg.get("graphics_guard") is not False, "graphicsGuardWords": {"title": gpu_guard.WORDS["switchTitle"], "hint": gpu_guard.WORDS["switchHint"]},
        "windowsToggleEnabled": windows_ok,
        "windowsSupported": _windows_startup_supported(),
        "windowsToggleNote": win_note,
        "startedByTray": from_tray, "trayAppPresent": tray_here,
        "mod": {"detected": bool(PLUGIN_VERSION), "version": PLUGIN_VERSION, "package": _mod_package_path(), "releaseUrl": PLUGIN_DOWNLOAD_URL},
        "folders": {"skyrim": skyrim_install_path or "", "settings": _settings_dir(), "models": str(local_svc.folder) if local_svc.folder else "",
                    "skyrimnetFound": (bool(_resolve_skyrimnet_dir_cached(skyrim_install_path)) if skyrim_install_path else None),
                    "skyrimnetPick": skyrimnet_pick},
        "connection": {"port": MAIN_PORT, "default": 8000, "privateNetworks": other_pcs.extra_networks()},
        "about": {"version": PROXY_VERSION, "build": BUILD_STAMP, "update": dict(_update_state), "notices": notices.NOTICES},
        "options": {k: bool(globals()[v[0]]) for k, v in SETTINGS_OPTIONS.items()},
        "limits": _limits_state(),
        "pageWidth": _page_width(),
        "moveJob": _routing_settings()["on_move"],
        "logFields": field_log.state(),
        "getStarted": _get_started_available(),
    }


# --- Speeds tab: linking the Skyrim install + reading SkyrimNet's own role/variants config ---
# SkyrimNet keeps a settings file at Data/SKSE/Plugins/SkyrimNet/config/<Provider>.yaml (which
# provider preset varies by player, e.g. OpenRouter.yaml, GLM.yaml -- never hardcode a filename)
# shaped like `{provider_key: {model_name: ..., variants: {RoleKey: {model_name: ...}, ...}}}`.
# This section turns that file into "which model is running dialogue/diary/vision/etc" role chips.

# Friendly labels for SkyrimNet's own variant keys -- an unrecognized key still gets a reasonable
# label (title-cased, spaced out) rather than failing, per the spec. Matched verbatim to SkyrimNet's
# own "Quick Test all models" screen (confirmed live 2026-09-20) so a chip here reads as the exact
# same name the player already sees inside SkyrimNet itself, not a second, slightly different name
# for the same thing.
# (The names themselves are SkyrimNet's own: job_names.py reads them from its files, with a built-in copy of its twelve as the fallback; nothing is listed here.)


# Every chip maps to exactly one of the four fixed threshold categories from DEFAULT_SPEED_THRESHOLDS
# -- anything not listed here (Diary Generation, Memory, Character Profiles, Universal Translator,
# Action Evaluation, Combat, Meta, AI Assistant) falls into "background".
_ROLE_CATEGORY = {"Dialogue": "dialogue", "GameMaster": "gamemaster", "Vision": "vision"}

# Only these four labels get their own chip color (matching the mockup's .role.dialogue/.diary/
# .vision/.gamemaster CSS classes) -- everything else renders as the generic "other" chip.
_ROLE_CHIP_CLASS = {"Dialogue": "dialogue", "Diary Generation": "diary", "Vision": "vision", "GameMaster": "gamemaster"}


def _role_label(key: str) -> str:
    """What SkyrimNet calls this job (a job another mod adds: its raw name, as SkyrimNet shows it)."""
    _refresh_job_names()
    return routing.label(key)


def _role_category(label: str) -> str:
    return _ROLE_CATEGORY.get(label, "background")


def _role_chip_class(label: str) -> str:
    return _ROLE_CHIP_CLASS.get(label, "other")


def _resolve_skyrimnet_dir(skyrim_path: str, pick: str = "") -> Optional[str]:
    r"""SkyrimNet's own `SKSE\Plugins\SkyrimNet` folder: the one the player picked when asked (`pick`), else the one found from the linked Skyrim folder: the normal place, then a
    Mod Organizer 2 instance next to it. MO2 keeps a mod as the CONTENTS of the Data folder (`mods\<mod>\SKSE\...`, no `Data` level; also `overwrite`), and MultiProxy only sees what is
    really on disk, never MO2's merged view. See skyrimnet_finder.py for the layouts, the profile order and why a candidate needs a `config` folder."""
    return skyrimnet_finder.resolve(skyrim_path, pick)


# Re-resolves only when the linked root actually changes (2026-09-22) -- re-resolving on every
# call would mean a fresh os.listdir(mods\) on every single dashboard poll (every 5s, via
# _find_skyrimnet_variants_doc, independent of _get_role_chips_cached's own 5s TTL) even
# though the real on-disk layout essentially never changes between polls. A mods\ folder with a
# few hundred entries makes that a real, if small, repeated cost worth skipping. Keyed on the root
# string itself, not a TTL -- correctness here only ever depends on which root is linked, and that
# changes at most a few times per session (linking/relinking), never every few seconds.
_skyrimnet_dir_cache: dict = {"root": None, "resolved": None}


def _resolve_skyrimnet_dir_cached(skyrim_path: str) -> Optional[str]:
    key = (skyrim_path, skyrimnet_pick)                       # the linked folder and the folder the player picked: either changing looks again
    if _skyrimnet_dir_cache["root"] != key:
        _skyrimnet_dir_cache["root"] = key
        _skyrimnet_dir_cache["resolved"] = _resolve_skyrimnet_dir(skyrim_path, skyrimnet_pick)
    return _skyrimnet_dir_cache["resolved"]


def _model_names(entry) -> list:
    """Every model a role uses, in order, from a SkyrimNet provider/variant block. Beta 25 lets one
    role rotate through SEVERAL models: `model_name: google/gemma-4-31b-it, deepseek/deepseek-v3.2`
    (comma-separated), with a matching `model_params: [{name: ..., provider_id: ...}, ...]` list --
    so a role can carry more than one model, and the names may live in either place. Also accepts a
    bare string or list, for callers that already have just the names."""
    names: list = []

    def add(value) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
        elif isinstance(value, str):
            for part in value.split(","):
                part = part.strip()
                if part and part not in names:
                    names.append(part)

    if isinstance(entry, dict):
        add(entry.get("model_name"))
        for param in entry.get("model_params") or []:
            if isinstance(param, dict):
                add(param.get("name"))
    else:
        add(entry)
    return names


def _find_skyrimnet_variants_doc(skyrim_path: str) -> Optional[dict]:
    """Scan Data/SKSE/Plugins/SkyrimNet/config/*.yaml for the active provider's own settings file
    -- identified by SHAPE (a top-level key holding sibling `model_name` and `variants` fields),
    never by filename, since the filename depends on whichever provider preset the player picked.
    Returns {"model_name": str, "model_names": [str], "variants": {role_key: [model names]}} or None
    if nothing matches. Each role's value is a LIST since Beta 25: a role can rotate through several
    models (see _model_names)."""
    skyrimnet_dir = _resolve_skyrimnet_dir_cached(skyrim_path)
    if not skyrimnet_dir:
        return None
    config_dir = os.path.join(skyrimnet_dir, "config")
    try:
        entries = os.listdir(config_dir)
    except OSError:
        return None
    import yaml
    for name in entries:
        if not name.lower().endswith((".yaml", ".yml")):
            continue
        try:
            with open(os.path.join(config_dir, name), "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(doc, dict):
            continue
        for value in doc.values():
            if isinstance(value, dict) and value.get("model_name") and isinstance(value.get("variants"), dict):
                variants = {
                    role_key: _model_names(role_val)
                    for role_key, role_val in value["variants"].items()
                    if isinstance(role_val, dict) and _model_names(role_val)
                }
                return {
                    "model_name": value["model_name"],
                    "model_names": _model_names(value),
                    "variants": variants,
                }
    return None


# The single source of truth for "which prefix maps to which provider" -- an ordered list of
# (is_X_model, strip_X_prefix) pairs, shared by chat_completions()'s own dispatch (via
# _resolve_model_name below) AND role-chip matching (_model_role_chips). Order matters only in
# that a model can't match more than one (each is_X_model check is already mutually exclusive by
# construction -- see is_unclaimed_slash_model's own exclusion list), same rule chat_completions()'s
# _ROUTES table follows.
_PREFIX_STRIP_TABLE = (
    (is_glm_model, strip_glm_prefix),
    (is_nano_model, strip_nano_prefix),
    (is_openai_model, strip_openai_prefix),
    (is_deepseek_model, strip_deepseek_prefix),
    (is_kilo_model, strip_kilo_prefix),
    (is_gemini_model, strip_gemini_prefix),
    (is_ollama_model, strip_ollama_prefix),
    (is_lmstudio_model, strip_lmstudio_prefix),
    (is_local_model, strip_local_prefix),
    # Other devices keep their full `nickname/model` name: it is what SkyrimNet's own config says (so role
    # chips match) and it keeps the same model on two devices apart on the Speeds tab.
    (is_remote_model, lambda m: m),
    (is_codex_model, strip_codex_prefix),
    (is_geminicli_model, strip_geminicli_prefix),
    (is_grok_model, strip_grok_prefix),
    (is_xai_model, strip_xai_prefix),
    (is_continuum_model, strip_continuum_prefix),
    (is_key_source_model, strip_key_source_prefix),
)


_CLAUDE_DOT_VERSION = re.compile(r"(?<=\d)\.(?=\d)")
_claude_dots_told: set = set()


def _claude_dash_name(model: str) -> str:
    """A bare Claude name written with a dot in its version ('claude-sonnet-5.5') becomes the dash form Anthropic knows ('claude-sonnet-5-5'). Only names that start with
    'claude-' are touched, so every other provider keeps its dots. Logged once per name (2026-10-24: Anthropic answered 404 'Did you mean claude-sonnet-5-5?')."""
    if not model.startswith("claude-") or not _CLAUDE_DOT_VERSION.search(model):
        return model
    fixed = _CLAUDE_DOT_VERSION.sub("-", model)
    if model not in _claude_dots_told:
        _claude_dots_told.add(model)
        logger.info(f"Claude model {model} was written with a dot; used {fixed}.")
    return fixed


def _resolve_model_name(model: str) -> str:
    """The same prefix-detection-and-strip chat_completions() applies before dispatching a
    request, exposed standalone so role-chip matching can key by the same resolved name
    speed_tracker uses. Real bug this fixes (2026-09-20): SkyrimNet's own yaml stores the RAW,
    unstripped model string (e.g. "deepseek/deepseek-v3.2"), but speed_tracker records the
    STRIPPED name actually sent to the provider ("deepseek-v3.2") -- role chips silently failed
    to match for every provider that strips a prefix (everything except bare Claude), which is most real setups, not an edge case."""
    for is_model, strip_prefix in _PREFIX_STRIP_TABLE:
        if is_model(model):
            return strip_prefix(model)
    return _claude_dash_name(model)


def _model_role_chips(variants_doc: dict) -> dict:
    """{"model_name": ..., "variants": {role_key: model name(s)}} -> {card key: [chip, ...]}, where
    a chip is {"label", "class", "category"}. A model can carry more than one chip when several
    roles share the same model_name -- and a role that rotates through several models (Beta 25) puts
    its chip on EACH of them. Keyed by the RESOLVED (prefix-stripped) name via
    _resolve_model_name, matching how speed_tracker itself keys its data -- see that function's
    own docstring for the bug this avoids."""
    chips: dict[str, list] = {}

    def add(names, label: str) -> None:
        for raw_name in _model_names(names):
            card = speed_queries.card_key(_provider_label_for(raw_name), _resolve_model_name(raw_name))      # the same model under two providers is two cards
            chip = {"label": label, "class": _role_chip_class(label), "category": _role_category(label)}
            bucket = chips.setdefault(card, [])
            if chip not in bucket:
                bucket.append(chip)

    add(variants_doc.get("model_names") or variants_doc.get("model_name"), "Dialogue")
    for role_key, model_names in variants_doc.get("variants", {}).items():
        add(model_names, _role_label(role_key))
    return chips


# Re-reading the yaml file on every single /stats/speeds poll would mean disk I/O on every
# dashboard refresh -- cheap here, but pointless when the file only ever changes if the player
# edits it by hand while the proxy is running. A few seconds of staleness is a non-issue for a
# monitor-and-report tab.
_ROLE_CACHE_TTL = 5.0
_role_chip_cache: dict = {"ts": 0.0, "chips": {}}


def _get_role_chips_cached() -> dict:
    now = time.time()
    if now - _role_chip_cache["ts"] < _ROLE_CACHE_TTL:
        return _role_chip_cache["chips"]
    chips: dict = {}
    if skyrim_install_path:
        doc = _find_skyrimnet_variants_doc(skyrim_install_path)
        if doc:
            chips = _model_role_chips(doc)
    _role_chip_cache["ts"] = now
    _role_chip_cache["chips"] = chips
    return chips


# --- Routing (2026-10-02, step 1 of the routing work) ---
# routing.py has the logic (which job a request is, routes, hand-over, resting); ai_costs.py has cost. This is where they meet a real request:
# `_chat_completions_impl` asks `_routing_plan` (which also writes the job into the request's record) and, only when the player has set a route for
# that job, `_answer_by_route` tries the route's AIs in turn. No route = nothing changes: the request is answered by its model name, as always.
def _refresh_job_names() -> None:
    """Re-read SkyrimNet's job names when its files changed (cheap: at most every few seconds); a problem is never fatal."""
    try:
        job_names.NAMES.refresh(_resolve_skyrimnet_dir_cached(skyrim_install_path) if skyrim_install_path else None)
    except Exception as e:
        logger.debug(f"Couldn't read SkyrimNet's job names ({e.__class__.__name__}); the built-in copy is used")


def _roles_of(roles_json) -> list:
    """The role labels stored with a Speeds row, a label from before names came from SkyrimNet shown under the job's current name (stored rows are never rewritten)."""
    return [routing.current_role_label(r) for r in json.loads(roles_json)]


def _find_skyrimnet_provider_block(skyrim_path: str) -> Optional[dict]:
    """The provider block of SkyrimNet's settings file (found by its shape, like _find_skyrimnet_variants_doc), for the per-job numbers only: routing.settings_table
    picks out max tokens, temperature and timeout and nothing else; no key is ever read from it."""
    skyrimnet_dir = _resolve_skyrimnet_dir_cached(skyrim_path)
    if not skyrimnet_dir:
        return None
    config_dir = os.path.join(skyrimnet_dir, "config")
    try:
        entries = os.listdir(config_dir)
    except OSError:
        return None
    import yaml
    for name in entries:
        if not name.lower().endswith((".yaml", ".yml")):
            continue
        try:
            with open(os.path.join(config_dir, name), "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(doc, dict):
            for value in doc.values():
                if isinstance(value, dict) and value.get("model_name") and isinstance(value.get("variants"), dict):
                    return value
    return None


_job_table_cache: dict = {"ts": 0.0, "table": {}}


def _skyrimnet_job_table() -> dict:
    """{job key: {max_tokens, temperature, timeout, models}} from SkyrimNet's own settings; {} when it isn't linked. Re-read every few seconds, like the role chips."""
    _refresh_job_names()
    now = time.time()
    if now - _job_table_cache["ts"] < _ROLE_CACHE_TTL:
        return _job_table_cache["table"]
    table: dict = {}
    if skyrim_install_path:
        try:
            table = routing.settings_table(_find_skyrimnet_provider_block(skyrim_install_path))
        except Exception as e:
            logger.warning(f"Couldn't read SkyrimNet's per-job settings ({e.__class__.__name__}); jobs are recognised by their shape only")
    _job_table_cache["ts"], _job_table_cache["table"] = now, table
    return table


_named_seen: set = set()          # jobs asked for by their exact name (job/<name>) or by the hint header since MultiProxy started
_seen_how: dict = {}              # {job key: how it was last known} from the Speeds history, as the Routing view last read it


def _named_jobs(table: Optional[dict] = None) -> set:
    """The jobs SkyrimNet names exactly (its model is `job/<name>`, or they were asked for that way): each has its own chip and route and is in no group."""
    return routing.exactly_named(_skyrimnet_job_table() if table is None else table, _seen_how, _named_seen)


def _hard_groups() -> list:
    return routing.hard_groups(_skyrimnet_job_table(), named=_named_jobs())


def _copy_split_routes() -> None:
    """A job that has just left its group starts with the group's route, once (saved under its own key, the list `split_copied` remembers it), so splitting never silently changes
    where it goes. After that its route is its own: changing or removing it never copies again."""
    table = _skyrimnet_job_table()
    named = _named_jobs(table)
    if not table or not named:
        return
    cfg = _load_config()
    block = routing.clean_settings(cfg.get("routing"))
    todo = [k for k in sorted(named) if k not in block["split_copied"]]
    if not todo:
        return
    groups = routing.hard_groups(table)                    # the groups as they were before anything left
    for k in todo:
        block["split_copied"].append(k)
        if k in block["routes"]:
            continue
        grp = next((g for g in groups if k in g), None)
        route = routing.route_for(block, [m for m in grp if m != k and m not in named])[0] if grp else None
        if route:
            block["routes"][k] = dict(route)
    cfg["routing"] = block
    _save_config(cfg)


def _routing_settings() -> dict:
    return routing.clean_settings(_load_config().get("routing"))


def _engine_key(ai: str) -> str:
    """What the Speeds data and the engine's own memory (first words, costs, resting) call this AI: its provider and its model together."""
    return speed_queries.card_key(_provider_label_for(ai), _resolve_model_name(ai))


def _ai_class(ai: str) -> tuple:
    """(cost class, kind) of an AI by its model name: this device's own models and other devices are free, a login (Claude, Codex, Antigravity) is a subscription, a key is paid."""
    if is_local_model(ai):
        return routing.CLASS_FREE, "local"
    if is_ollama_model(ai) or is_lmstudio_model(ai) or is_remote_model(ai):
        return routing.CLASS_FREE, "other"
    if is_codex_model(ai) or is_geminicli_model(ai) or is_grok_model(ai):
        return routing.CLASS_INCLUDED, "subscription"
    if is_key_source_model(ai):
        return routing.CLASS_FREE, "cloud"          # a small free allowance (the key itself is the player's own: nothing here is billed by MultiProxy)
    if is_glm_model(ai) or is_nano_model(ai) or is_openai_model(ai) or is_deepseek_model(ai) or is_kilo_model(ai) or is_gemini_model(ai) or is_xai_model(ai) or is_continuum_model(ai) or is_unclaimed_slash_model(ai):
        return routing.CLASS_PAID, "cloud"
    return routing.CLASS_INCLUDED, "subscription"          # a bare model name is Claude, through the player's own login


_routing_engine = routing.Engine(_ai_class, key=lambda a: _engine_key(a))
_log_checker = routing.LogChecker()
_first_words_seeded = False


def _seed_first_words() -> None:
    """After a restart the Speeds history already knows how fast each AI started recently: use the last half hour of it (once)."""
    global _first_words_seeded
    if _first_words_seeded:
        return
    _first_words_seeded = True
    try:
        with sqlite3.connect(SPEED_HISTORY_DB, timeout=2.0) as conn:
            rows = conn.execute("SELECT provider, model, ts, first_token FROM speed_history WHERE success = 1 AND first_token IS NOT NULL AND provider IS NOT NULL AND ts > ? ORDER BY ts",
                                (time.time() - routing.RECENT_S,)).fetchall()
        for provider, model, ts, first_token in rows:
            _routing_engine.first_words.add(speed_queries.card_key(provider, model), first_token, ts)
    except Exception as e:
        logger.warning(f"Couldn't read the recent first-word times from the Speeds history ({e.__class__.__name__})")


_COST_CLASS_BY_PROVIDER = {
    "Claude": routing.CLASS_INCLUDED, "Codex": routing.CLASS_INCLUDED, "Antigravity": routing.CLASS_INCLUDED, "Grok": routing.CLASS_INCLUDED,
    "GLM": routing.CLASS_PAID, "Nano-GPT": routing.CLASS_PAID, "OpenAI": routing.CLASS_PAID, "DeepSeek": routing.CLASS_PAID, "Kilo Gateway": routing.CLASS_PAID,
    "Gemini": routing.CLASS_PAID, "xAI": routing.CLASS_PAID, "Continuum": routing.CLASS_PAID, "TypeSafe": routing.CLASS_PAID, "Perplexity": routing.CLASS_PAID,
    "Ollama": routing.CLASS_FREE, "LM Studio": routing.CLASS_FREE, "Local": routing.CLASS_FREE,
}
_COST_CLASS_BY_PROVIDER.update({_s.pill: routing.CLASS_FREE for _s in key_sources.SOURCES})


async def _fetch_nano_prices() -> dict:
    """Nano-GPT's own model list with prices (needs its key; nothing is fetched without one)."""
    if not nano_api_key:
        return {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
        async with session.get(f"{NANO_BASE_URL}/models?detailed=true", headers={"Authorization": f"Bearer {nano_api_key}"}) as resp:
            if resp.status != 200:
                return {}
            return ai_costs.parse_nano_prices(await resp.json())


_nano_prices = ai_costs.PriceList(_fetch_nano_prices)


async def _finish_cost(model: str, provider: Optional[str], success: bool, out_tokens: Optional[int]) -> None:
    """Put what this request cost in its record: free for this device's models and other devices, "included" for a subscription, the AI's own reported cost (or its own
    prices worked out from the tokens) for a key, otherwise "not reported". Never a guess. Never raises."""
    rec = request_facts.current()
    if rec is None or not success or not provider:
        return
    if provider == "Local" and rec.tokens_in:       # the largest prompt each local model has been sent (kept as data for the settings); a local model is saved under its bare name
        try:
            local_svc.prompt_peaks.note(strip_local_prefix(model), rec.tokens_in)
        except Exception:
            pass
    try:
        cls = _COST_CLASS_BY_PROVIDER.get(provider, routing.CLASS_FREE)       # a provider we don't know by name is another device's nickname
        if not (cls == routing.CLASS_PAID and rec.cost_note == ai_costs.NOTE_REPORTED):
            price = await _nano_prices.lookup(model) if provider == "Nano-GPT" else ai_costs.perplexity_price(model) if provider == "Perplexity" else None
            tokens_out = rec.tokens_out if rec.tokens_out is not None else out_tokens
            if provider == "Perplexity" and tokens_out is None:
                tokens_out = 0                  # its output is free and never counted: the input tokens alone make the cost
            rec.cost_usd, rec.cost_note = ai_costs.work_out(cls, None, rec.tokens_in, tokens_out, price)
        if cls == routing.CLASS_PAID and rec.cost_usd is not None and rec.job_key:
            _routing_engine.costs.add(rec.job_key, speed_queries.card_key(provider, model), rec.cost_usd)
    except Exception as e:
        logger.warning(f"Couldn't work out what this request cost ({e.__class__.__name__})")


def _routing_columns() -> dict:
    """The routing facts of this request for its Speeds row."""
    rec = request_facts.current()
    if rec is None:
        return {}
    return {"job": rec.job_key, "job_how": rec.job_how, "tokens_in": rec.tokens_in, "cost": rec.cost_usd, "cost_note": rec.cost_note,
            "timings_json": json.dumps(rec.timings) if rec.timings else None, "skipped": json.dumps(rec.skipped) if rec.skipped else None,
            "queue_wait": rec.queue_wait_s, "own_ms": rec.own_ms(), "in_flight": rec.in_flight, "stand_in": rec.stand_in}


def _request_features(req: "ChatRequest") -> routing.Features:
    msgs = req.messages or []
    images = sum(1 for m in msgs if isinstance(m.content, list) for part in m.content if isinstance(part, dict) and str(part.get("type", "")).startswith("image"))
    system = next((_content_as_text(m.content) for m in msgs if m.role == "system"), "")
    first_user = next((_content_as_text(m.content) for m in msgs if m.role == "user"), "")
    return routing.make_features(
        stream=bool(req.stream), images=images, questions=bool(req.questions) and not msgs, messages=len(msgs),
        max_tokens=req.max_tokens if "max_tokens" in req.model_fields_set else None, temperature=req.temperature,
        system_text=system, first_user_text=first_user)


_named_unrouted_logged: set = set()           # jobs already logged as "named exactly, no route yet" (one line each, not one per request)


class _RoutePlan:
    def __init__(self, recognition, route, job, settings):
        self.recognition, self.route, self.job, self.settings = recognition, route, job, settings


def _routing_plan(req: "ChatRequest", request: Request) -> Optional[_RoutePlan]:
    """Work out this request's job (and write it into its record); return the plan to answer it by a route, or None (no route set: answer it as always)."""
    rec = request_facts.current()
    if is_jev_request(req):
        if rec is not None:
            rec.job_key, rec.job, rec.job_how, rec.job_certain = "decisions", routing.label("decisions"), routing.HOW_RECOGNISED, True
            settings = _routing_settings()
            rec.queue_rank, rec.queue_aging_s = routing.priority_rank(settings, "decisions"), settings["aging_s"]
        return None
    if routing.is_quick_test(request.headers):
        logger.info("routing: Quick Test, sent to the model asked for")
        return None
    name = routing.job_from_name(req.model or "", _skyrimnet_job_table())
    try:
        hint = routing.job_from_hint(request.headers)
        if hint:
            found = routing.Recognition((hint,), routing.HOW_HINT, True)
            _named_seen.add(hint)
        elif name:
            found = routing.Recognition((name,), routing.HOW_NAME, True)
            _named_seen.add(name)
        else:
            found = routing.recognise(_request_features(req), routing.without_named(_skyrimnet_job_table(), _named_jobs()))     # (a job SkyrimNet names exactly is never guessed from its shape)
        _copy_split_routes()
        if rec is not None:
            rec.job_key, rec.job_how, rec.job_certain = "/".join(found.keys), found.how, found.certain
            if found.known:
                rec.job = found.label                       # a job we can't name keeps the Speeds tab's own label (by model name), as before
        _log_checker.note_request(time.time(), req.model or "", found.keys)
        _check_against_skyrimnet_log()
        settings = _routing_settings()
        if rec is not None:
            rec.queue_rank, rec.queue_aging_s = routing.priority_rank(settings, "/".join(found.keys) if found.known else None), settings["aging_s"]
        route, job = routing.route_for(settings, found.keys) if found.known else (None, None)
        if route is not None and found.how == routing.HOW_RECOGNISED and not routing.tied_to_one_job(found, _skyrimnet_job_table(), named=_named_jobs()):
            logger.info("routing: job unknown, sent to the model asked for")
            route, job = None, None
        if found.known:
            _last_sent.note(found.keys, fine_tune.sent_settings(max_tokens=req.max_tokens if "max_tokens" in req.model_fields_set else None, temperature=req.temperature,
                                                                  extra=req.model_extra))
            if rec is not None:
                rec.overrides = fine_tune.overrides_for(settings, found.keys)
    except Exception as e:                                  # recognition must never break a request
        logger.warning(f"Couldn't work out this request's job ({e.__class__.__name__}); answering it by its model name")
        route, found, settings, job = None, None, None, None
    if route is None and name:
        try:
            route, source = routing.named_without_route(settings, name, _skyrimnet_job_table())
        except Exception:
            route, source = None, None
        if route is None:
            raise HTTPException(status_code=404, detail=f"{routing.label(name)} has no route yet. Drag it onto a model on the Routing tab.")
        job = name
        if name not in _named_unrouted_logged:
            _named_unrouted_logged.add(name)
            logger.info(f"routing: {routing.label(name)} is named exactly but has no route yet; answered like its {'group' if source == 'group' else 'model name'} ({route['ais'][0]}).")
    if route is None:
        return None
    return _RoutePlan(found, route, job, settings)


# --- Fine-tune a job (Routing step 4): what SkyrimNet last sent, and the player's per-job overrides ---
_last_sent = fine_tune.LastSent(os.path.join(SETTINGS_DIR, "routing-last-sent.json"))

# Which settings each kind of model can take. Conservative: only what that kind is known to accept (the same lists the request handling above already uses); anything else is
# skipped, never sent. Claude (its own login) and the two command-line subscriptions take no sampler settings here: Claude only a longest reply.
_OVERRIDE_ACCEPTS = {
    "claude": {"max_tokens"}, "codex": set(), "geminicli": set(), "grok": set(),
    "glm": {"max_tokens", "temperature", "top_p", "thinking"},
    "deepseek": {"max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty", "thinking"},
    "nano": {"max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty"},
    "openai": {"max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty"},
    "kilo": {"max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty"},
    "gemini": {"max_tokens", "temperature", "top_p"},
    "xai": {"max_tokens", "temperature", "top_p"},
    "continuum": {"max_tokens", "temperature", "top_p"},
    "ollama": {"max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty"},
    "lmstudio": {"max_tokens", "temperature", "top_p", "top_k", "frequency_penalty", "presence_penalty"},
    "local": {"max_tokens", "temperature", "top_p", "top_k", "frequency_penalty", "presence_penalty"},
    "remote": {"max_tokens", "temperature", "top_p", "top_k", "frequency_penalty", "presence_penalty"},
}
_OVERRIDE_ACCEPTS.update({_s.id: {"max_tokens", "temperature", "top_p"} for _s in key_sources.SOURCES})


def _override_kind(ai: str) -> str:
    """The kind of model a model name is, for what settings it takes."""
    src = key_sources.match(ai)
    if src:
        return src.id
    for check, key in ((is_glm_model, "glm"), (is_nano_model, "nano"), (is_openai_model, "openai"), (is_deepseek_model, "deepseek"), (is_kilo_model, "kilo"), (is_gemini_model, "gemini"),
                       (is_ollama_model, "ollama"), (is_lmstudio_model, "lmstudio"), (is_local_model, "local"), (is_remote_model, "remote"), (is_codex_model, "codex"),
                       (is_geminicli_model, "geminicli"), (is_grok_model, "grok"), (is_xai_model, "xai"), (is_continuum_model, "continuum")):
        if check(ai):
            return key
    return "claude"


def _override_thinking(kind: str):
    """The field a kind of model uses for the thinking switch (GLM and DeepSeek, which MultiProxy already switches by `thinking`), else None."""
    if kind in ("glm", "deepseek"):
        return lambda v: {"thinking": {"type": "enabled" if v == "on" else "disabled"}}
    return lambda v: None


def _tested_engine(line_key: Optional[str], model: str) -> tuple:
    """(engine for tested_settings, whether it is a model MultiProxy installed itself)."""
    program = None
    if line_key == "remote":
        m = remote_svc.match(model)
        if m:
            program = next((x.get("program") for x in (m[0].models or []) if x.get("id") == m[1] and x.get("program")), None) or m[0].program
    engine = tested_settings.engine_of(line_key, program)
    return engine, engine == "builtin"


def _apply_tested(rec, line_key: Optional[str], model: str, system_prompt, merged: list, extra: dict, streamed: bool) -> tuple:
    """The tested settings for this job (Routing > Settings for this model) put into the request for the model that answers it: (extra fields, the same without the fixed
    answer format or None, the job kind or None). Never over what the person set for the job, and a failure here is never a reason to fail a request."""
    try:
        keys = (rec.job_key or "").split("/")
        kind = tested_settings.job_kind(keys)
        engine, built_in = _tested_engine(line_key, model)
        if kind is None or engine is None:
            return extra, None, None
        text = "\n".join([str(system_prompt or "")] + [_content_as_text(m.get("content")) if isinstance(m, dict) else "" for m in merged])
        switches = tested_settings.switches_for(_routing_settings(), keys)
        out, plain, applied = tested_settings.apply(kind, tested_settings.family_of(model), engine, switches, built_in, extra, prompt_text=text, streamed=streamed,
                                                    own=getattr(rec, "overrides", None) or {})
        return out, plain, (kind if plain is not None else None)
    except Exception as e:
        logger.warning(f"Couldn't apply the tested settings ({e.__class__.__name__}); sent the request as it was")
        return extra, None, None


async def _ask_with_safety_net(direct_fn, args: tuple, extra: dict, plain: Optional[dict], kind: Optional[str], request_id: str):
    """Ask the model; when a fixed answer format was added and the reply is empty or not what the job needs (or the engine refused the format), ask once more without it."""
    if plain is None:
        return await direct_fn(*args, **extra)
    try:
        out = await direct_fn(*args, **extra)
        if tested_settings.reply_ok(kind, out[0]):
            return out
        why = "an empty or broken reply"
    except HTTPException as e:
        if e.status_code not in (400, 422):
            raise
        why = f"a refusal ({e.status_code})"
    logger.info(f"[{request_id}] tested settings: {why}; asked again without the fixed answer format")
    return await direct_fn(*args, **plain)


def _apply_overrides(rec, kind: str, max_tokens: int, extra: dict) -> tuple:
    """Put a request's fine-tune settings in as it goes to the model that answers it; remember (in its record) which the model took and which it can't."""
    accepted = _OVERRIDE_ACCEPTS.get(kind, set())
    try:
        max_tokens, extra, applied, skipped = fine_tune.apply(rec.overrides, lambda n: n in accepted, max_tokens, extra, _override_thinking(kind))
    except Exception as e:                                  # fine-tuning is optional: never a reason to fail a request
        logger.warning(f"Couldn't apply the fine-tune settings ({e.__class__.__name__}); sent the request as SkyrimNet made it")
        return max_tokens, extra
    rec.override_applied, rec.override_skipped = applied, [fine_tune.plain_name(n) for n in skipped]
    return max_tokens, extra


def _expand_route_entry(entry: str) -> list:
    """A route entry may be a model name or a comma list of them (the model groups SkyrimNet has always been able to send)."""
    return parse_model_list(entry) if "," in entry else [entry]


def _reset_attempt(rec) -> None:
    """Each AI tried gets a clean slate in the record, so the one that answers is the one that is named."""
    if rec is None:
        return
    rec.model = rec.provider = rec.outcome = rec.first_word_s = rec.total_s = rec.tokens_in = rec.tokens_out = None
    rec.timings = rec.cost_usd = rec.cost_note = None


async def _answer_by_route(plan: _RoutePlan, req: "ChatRequest", request: Request, request_id: str):
    _seed_first_words()
    rec = request_facts.current()
    cands = _routing_engine.order(plan.job, plan.route, plan.settings, expand=_expand_route_entry)
    if not cands:
        return await _answer_by_model(req, request, request_id)
    if rec is not None:
        rec.routed = True

    async def call(ai: str):
        _reset_attempt(rec)
        return await _answer_by_model(req.model_copy(update={"model": ai}), request, request_id)

    explicit = plan.route.get("slow_limit_s")
    # The route's own "no first word after" is how long a local engine gets. MultiProxy's own wait ends a hair before the route's (the route's would call it plain slowness, which
    # says nothing and records nothing; a local engine that does not start is a failure with a reason).
    token = _wait_budget.set(0.9 * max(routing.EXPLICIT_MIN_S, float(explicit))) if explicit and _wait_budget.get() is None else None
    try:
        outcome = await routing.run_route(
            _routing_engine, plan.job, cands, plan.settings, call, streamed=bool(req.stream), job_keys=plan.recognition.keys, route=plan.route,
            skipped=rec.skipped if rec is not None else None, passthrough=(_ClientGone,),
        )
    finally:
        if token is not None:
            _wait_budget.reset(token)
    return outcome.response


_log_state: dict = {"pos": None, "last": 0.0}


def _check_against_skyrimnet_log() -> None:
    """When SkyrimNet's own request log is readable, count how often our recognition agrees with its tag (status only: it never routes anything).
    Looks at most every 10 s, only at entries written after MultiProxy started, and keeps nothing of the prompts."""
    now = time.time()
    if now - _log_state["last"] < 10 or not skyrim_install_path:
        return
    _log_state["last"] = now
    try:
        skyrimnet_dir = _resolve_skyrimnet_dir_cached(skyrim_install_path)
        path = os.path.join(skyrimnet_dir, "logs", "openrouter_input.log") if skyrimnet_dir else ""
        if not path or not os.path.isfile(path):
            return
        size = os.path.getsize(path)
        pos = _log_state["pos"]
        if pos is None or size < pos:
            _log_state["pos"] = size if pos is None else 0
            if pos is None:
                return
            pos = 0
        if size == pos:
            return
        with open(path, "rb") as f:
            f.seek(pos)
            raw = f.read(2_000_000)
        text = raw.decode("utf-8", errors="replace")
        entries = routing.parse_request_log(text)
        last_head = max((m.start() for m in re.finditer(r"^\[\d{4}-\d\d-\d\d ", text, re.M)), default=0)
        if entries and not entries[-1][2]:                   # the newest entry isn't fully written yet: read it again next time
            entries = entries[:-1]
            raw = raw[:len(text[:last_head].encode("utf-8", errors="replace"))]
        _log_state["pos"] = pos + len(raw)
        _log_checker.feed(entries, lambda ts: time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        logger.debug(f"Couldn't check the job recognition against SkyrimNet's log ({e.__class__.__name__})")


def _routing_status() -> dict:
    now = time.time()
    return {
        "resting": [{"ai": r["ai"], "why": r["why"], "secondsLeft": max(0, int(r["until"] - now))} for r in _routing_engine.rest.resting()],
        "checks": {"agreed": _log_checker.agreed, "disagreed": _log_checker.disagreed},
        "working": {k: n for k, n in _routing_engine.load.n.items() if n},
    }


def _find_steam_libraries() -> list:
    """Every Steam library folder, via the registry + libraryfolders.vdf. Returns [] if Steam isn't
    installed, the registry key is missing (e.g. a non-Steam install), or this isn't Windows."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
    except (ImportError, OSError):
        return []
    libraries = [steam_path]
    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return libraries
    # libraryfolders.vdf is Valve's own KeyValues format, not JSON -- pulling just the "path" value
    # per numbered library with a targeted regex avoids pulling in a full VDF parser for one field.
    for m in re.finditer(r'"path"\s*"((?:[^"\\]|\\.)*)"', content):
        lib = m.group(1).replace("\\\\", "\\")
        if lib not in libraries:
            libraries.append(lib)
    return libraries


def _detect_skyrim_install() -> Optional[str]:
    """The first Skyrim Special Edition install, across every Steam library, that actually has
    SkyrimNet installed. Returns the game folder, or None if nothing matched.

    Runs candidates through the shared _resolve_skyrimnet_dir_cached resolver (2026-09-22) rather
    than its own hardcoded os.path.isdir check, so Steam auto-detect and the manual-link path
    share one "does this actually have SkyrimNet" logic instead of two near-copies -- a Steam
    library is never itself an MO2 instance, but there's no reason for this to be a second,
    weaker check than the one the manual path gets."""
    for lib in _find_steam_libraries():
        candidate = os.path.join(lib, "steamapps", "common", "Skyrim Special Edition")
        if _resolve_skyrimnet_dir_cached(candidate):
            return candidate
    return None


app = FastAPI(title="SkyrimNet MultiProxy", lifespan=lifespan)
# No CORS at all (security review 2026-10-06: it used to allow every website). Other sites are refused by local_guard, which checks Host, Origin and the X-MultiProxy header.
app.add_middleware(local_guard.LocalGuard, log=lambda m: logger.warning(m))
_skyrim_state = local_channel.SkyrimState()
_BOOT_ID = uuid.uuid4().hex          # changes every time the server starts: the tray app starts its "Skyrim closed" counting again when it does


TRAY_PROVIDERS_CACHE_SECONDS = 5.0
_tray_providers_cache: dict = {"at": 0.0, "value": []}


async def _tray_status() -> dict:
    """What the tray app needs for its dot, its menu and the quit-with-Skyrim rule (one call, this device only, token required)."""
    now = time.time()
    if now - _tray_providers_cache["at"] > TRAY_PROVIDERS_CACHE_SECONDS:      # the tray polls every 1.5 s; the provider states don't change that fast
        _tray_providers_cache.update(at=now, value=await _dashboard_providers())
    providers = _tray_providers_cache["value"]
    return {"boot": _BOOT_ID, "version": PROXY_VERSION, "port": MAIN_PORT, "autoClose": AUTO_CLOSE_WITH_SKYRIM, "modDetected": bool(PLUGIN_VERSION),
            "update": dict(_update_state), "readyCount": sum(1 for p in providers if p["state"] == "ok"), "totalAIs": len(providers)}


def _on_skyrim_started(folder: str, mod_version: str) -> None:
    """The Skyrim mod says the game started: remember the mod's version (so "Mod detected" is true) and, if no Skyrim folder is linked yet, its folder."""
    global PLUGIN_VERSION, skyrim_install_path
    graphics_guard.notify_started()         # the graphics memory guard sleeps until the game is up
    if mod_version:
        PLUGIN_VERSION = mod_version
    if folder and not skyrim_install_path and os.path.isdir(folder):
        skyrim_install_path = folder
        cfg = _load_config()
        cfg["skyrim_install_path"] = folder
        _save_config(cfg)


def _tray_shutdown() -> None:
    """A clean stop for the tray app: stop the local AI engines, then exit (after this answer has gone back)."""
    async def go():
        with suppress(Exception):
            await local_svc.stop_all()
        with suppress(Exception):
            await voice_handles.engine.close()
        with suppress(Exception):
            await decision_handles.engine.close()
        offloop_log.drain_all()
        speed_store.stop()      # write the Speeds rows still queued before the process ends
        os._exit(0)
    asyncio.get_running_loop().call_later(0.3, lambda: asyncio.ensure_future(go()))


app.include_router(local_channel.build_router(_skyrim_state, lambda: PROXY_VERSION, lambda: MAIN_PORT, _on_skyrim_started, _tray_status, _tray_shutdown,
                                              log=lambda m: logger.info(m)))
voice_handles = voice_wiring.setup(app, _load_config, _save_config, _settings_dir, _redact_text, logger, remotes=remote_svc, this_pc=other_pcs.short_hostname,
                                    main_port=lambda: MAIN_PORT, open_folder=lambda f: _open_in_file_manager(f), enable_tags=lambda: _enable_higgs_voice_tags(),
                                    pick_file=lambda title, types: local_models.pick_file_dialog(title, types),
                                    gpu_choice_fn=gpu_choice_wiring.choice_fn(lambda: _load_config(), gpu_choice_wiring.VOICE),
                                    xai_key=lambda: xai_api_key)   # Higgs voice (routes: /voice/*, /gradio_api/*, /config/voice/*)
voice_handles.service.on_line = _record_voice_line        # every spoken line is saved (seconds, worked or not) for the Overview's Voice row
claude_wiring.setup(app, remote_svc)         # Claude on another device (routes: /config/claude/pc/*)
copilot_wiring.setup(app, remote_svc)        # Copilot on another device (routes: /config/copilot/pc/*)
decision_handles = decision_wiring.setup(app, _load_config, _save_config, _settings_dir, _redact_text, logger, local_svc, voice_handles.store,
                                          main_port=lambda: MAIN_PORT, has_typesafe_key=lambda: bool(typesafe_api_key), has_nano_key=lambda: bool(nano_api_key), remotes=remote_svc,
                                          this_pc=other_pcs.short_hostname, skyrimnet_table=_skyrimnet_job_table,
                                          gpu_choice_fn=gpu_choice_wiring.choice_fn(lambda: _load_config(), gpu_choice_wiring.DECISIONS))   # the local decision model, here or on another device (/config/decisions/*)
set_me_up_svc = set_me_up.SetMeUp(
    local=local_svc, decisions=decision_handles.engine, voice=voice_handles.engine, voice_licence_ok=lambda: voice_handles.license_ok(),
    facts_fn=lambda: set_me_up.read_facts(local_svc, decision_handles.engine, voice_handles.engine, lambda: voice_handles.license_ok(),
                                         choice_fn=gpu_choice_wiring.local_choice_fn(lambda: _load_config()), pcs_fn=lambda: remote_svc.all()),
    load_cfg=_load_config, save_cfg=_save_config, log=logger, measure_fn=lambda n, secs: gpu_monitor_svc.measure_skyrim(n, secs))
gpu_choice_handles = gpu_choice_wiring.setup(app, _load_config, _save_config, local_svc, decision_handles.engine, voice_handles.engine, remotes=remote_svc)   # which graphics card each local AI uses (/config/gpu-choice)

# Log the actual request + response on any error (2026-09-20) -- reported live: a run of real 422s
# on /v1/chat/completions left nothing but uvicorn's own bare "422 Unprocessable Content" access
# line, no way to tell what SkyrimNet sent or why it was rejected. General mechanism, not
# special-cased to one route or error type -- fires on ANY response >= 400, from ANY endpoint,
# whether it's our own HTTPException -> _openai_style_error_handler, FastAPI's own built-in
# RequestValidationError handling (the actual source of that 422 -- a SEPARATE default handler
# from _openai_style_error_handler, which only registers for HTTPException), or an unhandled
# exception that escapes all the way to Starlette's own 500 handler.
_ERROR_LOG_BODY_MAX_CHARS = 2000


def _looks_binary(raw: bytes) -> bool:
    """Bytes that are not text (a sound, an image): a zero byte, or not valid UTF-8 (judged on the start, as a cut multi-byte character at the cut is no sign of binary)."""
    head = raw[:4096]
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        return e.start < len(head) - 4
    return False


def _format_logged_body(raw: bytes) -> str:
    """Decode + redact + truncate a request/response body for the error-logging middleware below.
    Redaction reuses _redact_text (the same pattern _RedactingFormatter already applies to file
    logs) rather than a second copy. Truncation matters for real reason, not a guess: a real
    /v1/chat/completions body can be tens of KB (a full SkyrimNet system prompt/character bio) --
    logging it whole on every error would just trade one kind of console noise for another,
    defeating the point of the companion quiet-access-logs fix."""
    if not raw:
        return "(empty)"
    if _looks_binary(raw):
        return f"(binary, {len(raw):,} bytes, not shown)"
    text = _redact_text(raw.decode("utf-8", errors="replace"))
    if len(text) > _ERROR_LOG_BODY_MAX_CHARS:
        text = text[:_ERROR_LOG_BODY_MAX_CHARS] + f"...(truncated, {len(raw)} bytes total)"
    return text


# Routes whose request body carries a pairing code or an API key: a failed one never logs the body.
_BODY_HIDDEN_PATHS = frozenset({"/config/other-pcs/courier/set-code", "/config/other-pcs/servers/add-multiproxy", claude_wiring.SIGN_IN_PATH, claude_wiring.SIGN_IN_CODE_PATH})
_BODY_HIDDEN_PREFIXES = ("/config/other-pcs/servers/", "/config/codex-providers")      # (add-own sends a "key"; security review 4.1.0, M1)


def _body_is_hidden(path) -> bool:
    path = str(path or "")
    return path in _BODY_HIDDEN_PATHS or (path.startswith("/config/") and path.endswith("-key")) or path.startswith(_BODY_HIDDEN_PREFIXES)


# Pages that show other devices' state keep a short shared answer (poll_cache). Any change to a device (a new pairing code, a pairing, an unpair, a rename...)
# drops those answers, and an answer older than this many seconds is never handed out stale: a page opened again after a while reads the device as it is now.
_DEVICE_VIEW_KEYS = ("overview", "routing-reachable")
_DEVICE_VIEW_MAX_STALE = 20.0


def _forget_device_views(scope) -> None:
    if scope.get("method") == "POST" and str(scope.get("path") or "").startswith("/config/other-pcs"):
        for key in _DEVICE_VIEW_KEYS:
            poll_cache.forget(key)


_VOICE_BODY_PREFIXES = ("/v1/audio/", "/voice/v1/audio/", "/gradio_api/", "/voice/gradio_api/")


def _request_body_for_log(scope, chunks) -> str:
    """What a failed request's body becomes in the log. A voice request (an enrolled clip, a line to speak) holds sound bytes and the spoken words, so only the route's
    field NAMES and sizes are written, never a value (voice_service.describe_request); a request that holds a key or a pairing code is hidden; the rest is as it was."""
    path = str(scope.get("path") or "")
    if _body_is_hidden(path):
        return "(hidden: it may hold a key or a pairing code)"
    if path.startswith(_VOICE_BODY_PREFIXES):
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        body = b"".join(chunks)
        return "(voice request, no sound or words written) " + voice_service.describe_request(scope.get("method", "?"), path, headers, headers.get("content-type", ""), body)
    body = b"".join(chunks)
    content_type = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"content-type"), "")
    summary = stt_capture.body_summary(content_type, body) if body else None    # a sound or any non-text body: its type and size only
    return _format_logged_body(body) if summary is None else summary


class _ErrorLogMiddleware:
    """Logs the request body and response body of every failed (>= 400) request -- see
    _format_logged_body for what gets written and what gets redacted.

    A plain ASGI middleware, not Starlette's `@app.middleware("http")` (2026-09-27, SkyrimNet Beta
    25). That decorator wraps the app in BaseHTTPMiddleware, which hands the route a synthetic
    `receive` that can never report "the client hung up" -- so `request.is_disconnected()` always
    said False, and a request SkyrimNet had already abandoned kept waiting in line and running
    upstream. This version passes the real `receive` straight through (only watching the request
    body go by), so a client disconnect is visible to the route; it also no longer buffers and
    replays the response, it just watches an error's body as it's sent.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_chunks: list[bytes] = []
        response_chunks: list[bytes] = []
        status = {"code": None}

        async def watching_receive():
            message = await receive()
            if message["type"] == "http.request":
                request_chunks.append(message.get("body", b""))
            return message

        async def watching_send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            elif message["type"] == "http.response.body" and (status["code"] or 0) >= 400:
                response_chunks.append(message.get("body", b""))
            await send(message)

        where = f"{scope.get('method', '?')} {scope.get('path', '?')}"
        try:
            await self.app(scope, watching_receive, watching_send)
        except Exception as exc:
            # An unhandled exception escapes the app entirely -- Starlette's own ServerErrorMiddleware,
            # further out than this one, is what actually turns it into the real 500 sent to the
            # client. Log what we know here, then re-raise unchanged so that still happens.
            logger.warning(
                f"[error] {where} -> 500 (unhandled exception: {exc})\n"
                f"  request body: {_request_body_for_log(scope, request_chunks)}"
            )
            raise

        _forget_device_views(scope)
        code = status["code"]
        if code is not None and code >= 400:
            sent = _request_body_for_log(scope, request_chunks)
            logger.warning(
                f"[error] {where} -> {code}\n"
                f"  request body: {sent}\n"
                f"  response body: {_format_logged_body(b''.join(response_chunks))}"
            )
            # A 422 means our own request model didn't recognize this body -- the untruncated,
            # full-fidelity capture capture_prompt_if_enabled can't provide here (see
            # capture_rejected_request_if_enabled's own docstring). The warning above is enough for
            # a quick look, but its 2000-char cap can cut off exactly the tail (a real `questions`
            # object, say) that matters for a genuinely new shape.
            if code == 422:
                capture_rejected_request_if_enabled(b"".join(request_chunks))


app.add_middleware(_ErrorLogMiddleware)


# ---- Quick Test for Quick decisions (Jev) (2026-10-05) ------------------------------------------------------------------------------------------------------
# A test, not a setting: it saves nothing and changes no setting (the card's "answered last" and Nano-GPT lines are put back as they were). Its request goes through the
# same functions SkyrimNet's does and is recorded in Speeds under the real provider like any decision; Speeds has no way to mark a row as a test, so it is not marked.
_DT_WHY_OFF = "You haven't added a {} key, so this choice is turned off."


def _decision_test_order() -> list:
    """The real order decisions are tried in right now (what "As set up" follows)."""
    order = decision_handles.order(None, bool(typesafe_api_key), bool(nano_api_key))
    if order is not None:
        return order
    return ["typesafe"] if typesafe_api_key else (["nano"] if nano_api_key else []) + ["standin"]


def _decision_test_choices() -> list:
    st = decision_handles.active().status()
    local_off = "" if st.get("ready") else ("The local model isn't installed, so that choice is off." if not st.get("installed") else "The local model isn't running, so that choice is off.")
    first = _decision_test_order()[0]
    return [
        {"id": "local", "label": "The local model", "reason": local_off, "paid": False},
        {"id": "typesafe", "label": "My TypeSafe key", "reason": "" if typesafe_api_key else _DT_WHY_OFF.format("TypeSafe"), "paid": True},
        {"id": "nanogpt", "label": "My Nano-GPT key", "reason": "" if nano_api_key else _DT_WHY_OFF.format("Nano-GPT"), "paid": True},
        *[{"id": p.choice, "label": p.label, "reason": "" if p.ready() else p.off_reason, "paid": not p.local} for p in decision_providers.REGISTRY.values() if p.ask is not None],
        {"id": "setup", "label": "As set up", "reason": "", "paid": first in ("typesafe", "nano") or (first in decision_providers.REGISTRY and not decision_providers.REGISTRY[first].local)},
    ]


@app.get("/quicktest/decision/options")
async def quicktest_decision_options():
    await decision_providers.refresh(wait=False)
    return {"scenarios": decision_test.listing(), "choices": _decision_test_choices()}


def _decision_test_failure(status: int, body, who: str) -> str:
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    if status == DECISION_SLOW:
        return "TypeSafe was too slow."
    msg = _extract_upstream_error_message(text, "").strip().rstrip(".")
    if who == "typesafe":
        return f"TypeSafe said no ({msg})." if msg else f"TypeSafe didn't answer ({status})."
    return msg + "." if msg else f"The answer didn't come back ({status})."


@app.post("/quicktest/decision")
async def quicktest_decision(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    who, built = str(body.get("who") or ""), decision_test.request(str(body.get("scenario") or ""))
    prov = decision_providers.by_choice(who)
    prov = prov if prov is not None and prov.ask is not None else None
    if built is None or who not in ("local", "typesafe", "nanogpt", "setup") + ((prov.choice,) if prov else ()):
        raise HTTPException(status_code=400, detail="That isn't a Quick decisions test. Pick a scenario and who answers.")
    state, questions = built
    await decision_providers.refresh(wait=False)
    reasons = {c["id"]: c["reason"] for c in _decision_test_choices()}
    if reasons.get(who):
        return {"ok": False, "error": reasons[who]}
    rid = uuid.uuid4().hex[:8]
    rec = _begin_request(request, rid, "decision", count=False)
    rec.questions = tuple(questions)
    path = request.url.path
    order = _decision_test_order()
    saved = (decision_handles.nano_line, decision_handles.last_answer, decision_handles.typesafe_line)
    t0 = time.time()
    la = None
    answers, error, tried = None, None, {"local": "local", "typesafe": "typesafe", "nanogpt": "nano", "setup": order[0]}.get(who) or (prov.id if prov else "")
    try:
        if who in ("local", "setup"):
            req = ChatRequest(model="local/decisions" if who == "local" else f"{TYPESAFE_PREFIX}jev-latest", state=state, questions=questions)
            try:
                resp = await _handle_jev_request(req, request, rid)
                la = dict(decision_handles.last_answer or {})
                data = json.loads(resp.body)
                if resp.status_code == 200:
                    answers = data.get("answers")
                else:
                    error = _decision_test_failure(resp.status_code, resp.body, "typesafe" if who == "setup" and order[0] == "typesafe" else "")
            except HTTPException as e:
                error = str(e.detail)
        elif who == "typesafe":
            model = f"{TYPESAFE_PREFIX}jev-latest"
            status, raw, _ct = await call_typesafe_systemone(model, state, questions, rid, via=path, limit=_paid_limit())
            await _record_speed(model, time.time() - t0, status == 200, fail_reason=None if status == 200 else "timeout" if status == DECISION_SLOW else f"TypeSafe returned {status}",
                                provider="TypeSafe", extra_roles=["Decisions"])
            if status == 200:
                answers = json.loads(raw).get("answers")
            else:
                error = _decision_test_failure(status, raw, "typesafe")
        elif prov is not None:
            status, raw, _ct = await _ask_provider_decision(prov, state, questions, rid, path, rec, prov.id)
            if status == 200:
                answers = json.loads(raw).get("answers")
            else:
                error = prov.miss_line(status) if prov.miss_line else f"{prov.name} was too slow." if status == DECISION_SLOW else f"{prov.name} didn't answer ({status})."
        else:
            status, raw, _ct = await _ask_nano_decision(state, questions, rid, path, rec, "nano")
            if status == 200:
                answers = json.loads(raw).get("answers")
            else:
                error = nano_decision_line(status, False).split(". ")[0] + "."
    except Exception as e:
        logger.warning(f"[{rid}] Quick decisions test failed ({e.__class__.__name__})")
        error = error or "The test couldn't finish. Try again."
    finally:
        decision_handles.nano_line, decision_handles.last_answer, decision_handles.typesafe_line = saved
    seconds = round(time.time() - t0, 1)
    asked = [{"name": q["name"], "text": q["text"]} for q in next(sc for sc in decision_test.listing() if sc["id"] == str(body["scenario"]))["questions"]]
    if error or not isinstance(answers, dict):
        nxt = decision_test.next_step(order, tried)
        return {"ok": False, "error": error or "No answer came back.", "seconds": seconds, "asked": asked,
                "next": f"Next in line: {nxt}." if nxt else "Nothing else is set up to answer.", "paid": False}
    provider, model = rec.provider or "", rec.model or ""
    unsure = (la or {}).get("unsure") if who == "setup" else None
    note = f" (the local model was only {round(unsure * 100)}% sure)" if unsure is not None and (la or {}).get("miss") == "unsure" else ""
    if provider == "Local decision model":
        e = decision_handles.active()
        line = f"Answered by {model or e.model_label()} on {'this device' if e is decision_handles.engine else getattr(e, 'pc', 'another device')} in {seconds} s"
    elif rec.stand_in:
        line = f"A stand-in AI answered: {provider} in {seconds} s"
    elif provider and provider in {p.name for p in decision_providers.REGISTRY.values() if p.ask is not None}:
        line = f"Answered by {model} in {seconds} s{note}"
    elif provider == "Nano-GPT":
        line = f"Answered by Nano-GPT ({model or NANO_DECISIONS_MODEL}) in {seconds} s{note}"
    else:
        line = f"Answered by {provider or 'TypeSafe'} in {seconds} s{note}"
    return {"ok": True, "line": line, "seconds": seconds, "asked": asked, "answers": decision_test.read_answers(questions, answers)}


# ---- Check it like SkyrimNet (2026-10-25): sample requests sent to the model the person picked, only when they press the button ------------------------------------
CHECK_REQUEST_LIMIT_S = 120.0       # one sample request may take this long before the check calls it failed


def _check_plain_error(status: Optional[int], message: str) -> str:
    """The plain reason a check request failed, for the card: the provider's own message without a trailing full stop."""
    return (message or "The model didn't answer").strip().rstrip(".") + "."


async def _check_send(request: Request, model: str, sample: dict) -> dict:
    """One sample request, through the same path a game request takes for that model. -> {"ok", "seconds", "waited", "label" ("fine"/"repaired"/"broken", JSON samples only), "stop" (a key or payment problem), "error"}."""
    rid = uuid.uuid4().hex[:8]
    _begin_request(request, rid, "chat", (), count=False)
    messages = [ChatMessage(role="system", content=sample["system"]), ChatMessage(role="user", content=sample["user"])]
    req = ChatRequest(model=model, messages=messages, stream=False, max_tokens=300)
    wants_json = _expects_json_reply(sample["system"], [{"content": sample["user"]}])
    t0 = time.time()
    try:
        result = await asyncio.wait_for(_chat_completions_impl(req, request, rid), CHECK_REQUEST_LIMIT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "seconds": time.time() - t0, "waited": None, "label": "broken" if wants_json else None, "stop": False, "error": "The model took too long to answer."}
    except HTTPException as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        return {"ok": False, "seconds": time.time() - t0, "waited": None, "label": "broken" if wants_json else None,
                "stop": skyrimnet_check.is_account_problem(e.status_code, msg), "error": _check_plain_error(e.status_code, msg)}
    except _ClientGone:
        return {"ok": False, "seconds": time.time() - t0, "waited": None, "label": None, "stop": False, "error": ""}
    except Exception as e:
        logger.warning(f"[{rid}] Check it like SkyrimNet: a sample request failed ({e.__class__.__name__})")
        return {"ok": False, "seconds": time.time() - t0, "waited": None, "label": None, "stop": False, "error": "The model couldn't answer."}
    seconds = time.time() - t0
    rec = request_facts.current()
    try:
        data = result if isinstance(result, dict) else json.loads(result.body)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception:
        text = ""
    if not text.strip():
        return {"ok": False, "seconds": seconds, "waited": getattr(rec, "queue_wait_s", None), "label": "broken" if wants_json else None, "stop": False, "error": "The model sent back nothing."}
    label = skyrimnet_check.reply_label(getattr(rec, "reply_repair", None), _repair_json_reply(text)[0]) if wants_json else None
    return {"ok": True, "seconds": seconds, "waited": getattr(rec, "queue_wait_s", None), "label": label, "stop": False, "error": ""}


def _check_stopped(results: list) -> Optional[str]:
    """The plain reason when a key or payment problem stopped the check, else None."""
    bad = next((r for r in results if r.get("stop")), None)
    return bad["error"] if bad else None


@app.get("/quicktest/check/options")
async def quicktest_check_options():
    """What the card needs: whether a decision model can be checked, how many requests a check sends, and the player's own last seven days."""
    local_ready = not _decision_test_choices()[0]["reason"]
    rows = await asyncio.to_thread(speed_queries.own_play_rows, SPEED_HISTORY_DB, time.time(), 7 * 86400.0)
    own = [dict(a, job=routing.label(a["job"])) for a in skyrimnet_check.own_play(rows) if a["job"] != routing.OTHER]
    return {"requests": skyrimnet_check.REQUESTS, "decision": local_ready, "slow": skyrimnet_check.BUSY_SLOW_SECONDS, "own": own}


@app.post("/quicktest/check")
async def quicktest_check(request: Request):
    """One part of the check ("one", "busy" or "actions") for the model the person picked. The page asks for the parts in turn and fills each line as it lands."""
    try:
        body = await request.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    model, part = str(body.get("model") or "").strip(), str(body.get("part") or "")
    if not model or part not in ("one", "busy", "actions"):
        raise HTTPException(status_code=400, detail="Pick a model to check.")
    token = _wait_budget.set(engine_trouble.TEST_PATIENCE_S)          # a model that is not loaded yet gets time to load, as the Routing test gives it
    try:
        if part == "busy":
            samples = (check_samples.CHAT_SAMPLES * 2)[:skyrimnet_check.BUSY_ROOM]
            results = list(await asyncio.gather(*[_check_send(request, model, s) for s in samples]))
        else:
            samples = check_samples.CHAT_SAMPLES if part == "one" else check_samples.ACTION_SAMPLES
            results = []
            for s in samples:
                results.append(await _check_send(request, model, s))
                if results[-1]["stop"]:
                    break                                              # a key or payment problem: the same refusal 20 times helps nobody
    finally:
        _wait_budget.reset(token)
    out = {"part": part, "stopped": _check_stopped(results)}
    if part == "one":
        out.update(skyrimnet_check.one_at_a_time(results))
    elif part == "busy":
        out.update(skyrimnet_check.busy_room(results))
    else:
        out.update(skyrimnet_check.actions(results))
    errors = [r["error"] for r in results if r.get("error")]
    out["error"] = errors[0] if errors and not out["stopped"] and not any(r["ok"] for r in results) else None
    return out


@app.post("/quicktest/check/decision")
async def quicktest_check_decision(request: Request):
    """"Check my decision model": the three sample questions sent to the local decision model, each answer with how sure it was and how long it took."""
    if _decision_test_choices()[0]["reason"]:
        return {"ok": False, "error": _decision_test_choices()[0]["reason"]}
    rows = []
    saved = (decision_handles.nano_line, decision_handles.last_answer, decision_handles.typesafe_line)
    try:
        for sample in check_samples.DECISION_SAMPLES:
            rid = uuid.uuid4().hex[:8]
            rec = _begin_request(request, rid, "decision", count=False)
            questions = {"answer": decision_test.typed_question("answer", sample["question"], "choice", sample["options"])}
            rec.questions = tuple(questions)
            t0 = time.time()
            answer, conf = None, None
            try:
                resp = await _handle_jev_request(ChatRequest(model="local/decisions", state=sample["state"], questions=questions), request, rid)
                if resp.status_code == 200:
                    got = decision_test.read_answers(questions, json.loads(resp.body).get("answers"))[0]
                    answer, conf = (got["text"] if got["text"] != "No answer" else None), got["confidence"]
            except (HTTPException, Exception) as e:
                logger.warning(f"[{rid}] Check it like SkyrimNet: a decision sample failed ({e.__class__.__name__})")
            rows.append(skyrimnet_check.decision_row(sample, answer, conf, time.time() - t0))
    finally:
        decision_handles.nano_line, decision_handles.last_answer, decision_handles.typesafe_line = saved
    return {"ok": True, "rows": rows}


@app.exception_handler(HTTPException)
async def _openai_style_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Every error this proxy returns -- an upstream provider failure, a missing key, Claude being
    unavailable -- comes back in the same `{"error": {"message", "type", "code"}}` shape, instead of
    FastAPI's default `{"detail": "..."}`. Every `raise HTTPException(...)` site already reduces
    `detail` to a single, clean, human-readable string (see `_extract_upstream_error_message` for
    the OpenAI-shaped providers, or `_claude_unavailable_message` for Claude) -- this handler's only
    job is the final, uniform wrapping, so every client (the dashboard's own Quick Test included)
    can rely on one shape everywhere rather than special-casing each provider's own error format.
    """
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": message, "type": "api_error", "code": None}},
        headers=getattr(exc, "headers", None),
    )


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    request_id = uuid.uuid4().hex[:8]
    _begin_request(request, request_id, "chat", list((req.model_extra or {}).keys()))
    handed_on = False
    try:
        result = _with_record_headers(await _chat_completions_impl(req, request, request_id))
        handed_on = _leave_when_sent(result, request_id)
        return result
    except _ClientGone:
        # SkyrimNet hung up before there was an answer to send (routine with Beta 25's first-token
        # guard, which drops whichever of two racing requests loses). Everything held for this
        # request has already been let go by now; nobody is left to read a response, so there's
        # nothing to report and nothing to log as a problem.
        logger.info(f"[{request_id}] SkyrimNet hung up before the reply was ready -- dropped this request")
        return Response(status_code=499)
    finally:
        if not handed_on:
            _in_flight.leave(request_id)


async def _chat_completions_impl(req: ChatRequest, request: Request, request_id: str):
    """One chat request. The job is worked out first (it goes into the request's record); when the player has set a route for that job, the route's AIs answer it
    in turn (routing.py), otherwise it goes where its model name says, exactly as it always did."""
    plan = _routing_plan(req, request)
    if plan is None:
        return await _answer_by_model(req, request, request_id)
    return await _answer_by_route(plan, req, request, request_id)


async def _answer_by_model(req: ChatRequest, request: Request, request_id: str):
    """One chat request. If it was for another device that didn't answer (before anything went back), and a backup is set, the backup
    answers instead: the same request, the same response shape, streaming included."""
    try:
        return await _chat_completions_core(req, request, request_id)
    except RemoteUnavailable as e:
        backup = _backup_model()
        if not backup:
            raise
        logger.warning(f"[{request_id}] {e.remote.label} didn't answer -- answering this one with the backup, {backup}")
        rec = request_facts.current()
        if rec is not None:
            rec.stand_in = e.remote.label                     # the backup answers in that device's place
        return await _chat_completions_core(req.model_copy(update={"model": backup}), request, request_id)


async def _chat_completions_core(req: ChatRequest, request: Request, request_id: str):
    capture_prompt_if_enabled(req)
    request_start = time.time()  # catch-broken-replies' own retry time budget starts here

    if is_jev_request(req):
        return await _await_unless_disconnected(request, _handle_jev_request(req, request, request_id))      # (a hang-up stops the AI; chat_completions answers it quietly)
    if not req.messages:
        # Pydantic used to enforce this by itself (messages was a required field, full stop) --
        # ChatRequest can no longer say that in the type system alone now that it also has to
        # accept Jev's messages-less shape (see is_jev_request above), so a genuine chat request
        # missing messages needs this explicit check to land on the same outcome it always did: a
        # 422, before any provider is ever contacted.
        raise HTTPException(status_code=422, detail="messages: Field required")

    # Parse model list and pick via round-robin
    model_field = req.model or _default_claude_model()
    models = parse_model_list(model_field)
    model = pick_model_round_robin(models) if len(models) > 1 else models[0]
    if is_unclaimed_slash_model(model):
        # A real HTTP error (not a guess at some other provider), so SkyrimNet falls back.
        raise HTTPException(status_code=404, detail=OPENROUTER_GONE_MESSAGE)
    use_glm = is_glm_model(model)
    use_nano = is_nano_model(model)
    use_openai = is_openai_model(model)
    use_deepseek = is_deepseek_model(model)
    use_kilo = is_kilo_model(model)
    use_gemini = is_gemini_model(model)
    use_ollama = is_ollama_model(model)
    use_lmstudio = is_lmstudio_model(model)
    use_local = is_local_model(model)
    use_remote = is_remote_model(model)
    use_codex = is_codex_model(model)
    use_geminicli = is_geminicli_model(model)
    use_grok = is_grok_model(model)
    use_xai = is_xai_model(model)
    use_continuum = is_continuum_model(model)
    use_ks = key_sources.match(model)          # Mistral, Groq or Cloudflare Workers AI (key_sources.py), else None
    ks_direct, ks_streaming = _ks_call_pair(use_ks)
    # NOTE: Copilot routing intentionally not wired in yet -- is_copilot_model/strip_copilot_prefix
    # and the COPILOT_* constants exist, but call_copilot_direct/call_copilot_streaming are not yet
    # built. Live-tested 2026-09-13: the "simple" token discovery this was meant to use (gh CLI's
    # own `gh auth token`) gets a flat 403 from GitHub's Copilot token-exchange endpoint even with
    # Copilot-client-shaped headers -- almost certainly because that token was issued to the `gh`
    # CLI's own OAuth app, not VS Code Copilot's recognized client ID. A real integration likely
    # needs the full OAuth device-flow login skyrimnet-codex-proxy also implements, which is a
    # genuinely bigger feature -- flagged back rather than shipping a path already proven not to
    # work.
    # OpenAI-shaped usage/finish_reason (prompt_tokens/completion_tokens, a real finish_reason
    # string) -- every provider except Anthropic (its own input_tokens/output_tokens/stop_reason
    # shape), Codex (no real usage data at all from CLI text output), and Antigravity CLI (real
    # usage, but under Anthropic-style input_tokens/output_tokens keys -- that's the shape its own
    # JSON output actually uses, so returning it as-is here needs no translation) returns this shape.
    openai_shaped = use_glm or use_nano or use_openai or use_deepseek or use_kilo or use_gemini or use_xai or use_continuum or use_ks is not None or use_ollama or use_lmstudio or use_local or use_remote

    # Table-driven: every provider below shares the exact call_X_direct(system_prompt, messages,
    # model, max_tokens, **extra_params) / call_X_streaming(...) signature, so routing is "find the
    # matching flag, call its pair" rather than one more copy-pasted if/elif block per provider.
    # Order matters only in that a model can't match more than one flag (each is_X_model check is
    # already mutually exclusive by construction -- see is_unclaimed_slash_model's own exclusion list).
    # The prefix-STRIP step itself is NOT duplicated here -- see _resolve_model_name/
    # _PREFIX_STRIP_TABLE (shared with role-chip matching) for that single source of truth.
    _ROUTES = (
        (use_glm, call_glm_direct, call_glm_streaming),
        (use_nano, call_nano_direct, call_nano_streaming),
        (use_openai, call_openai_direct, call_openai_streaming),
        (use_deepseek, call_deepseek_direct, call_deepseek_streaming),
        (use_kilo, call_kilo_direct, call_kilo_streaming),
        (use_gemini, call_gemini_direct, call_gemini_streaming),
        (use_ollama, call_ollama_direct, call_ollama_streaming),
        (use_lmstudio, call_lmstudio_direct, call_lmstudio_streaming),
        (use_local, call_local_direct, call_local_streaming),
        (use_remote, call_remote_direct, call_remote_streaming),
        (use_codex, call_codex_direct, call_codex_streaming),
        (use_geminicli, call_geminicli_direct, call_geminicli_streaming),
        (use_grok, call_grok_direct, call_grok_streaming),
        (use_xai, call_xai_direct, call_xai_streaming),
        (use_continuum, call_continuum_direct, call_continuum_streaming),
        (use_ks is not None, ks_direct, ks_streaming),
    )
    route = next(((direct_fn, streaming_fn) for flag, direct_fn, streaming_fn in _ROUTES if flag), None)
    # Which "request line" (see [Concurrency] in multiproxy.ini) this request stands in. The two CLI
    # subprocess providers (Codex, Antigravity) have none. No route at all means plain Claude.
    line_key = (
        "glm" if use_glm else "nano" if use_nano else "openai" if use_openai
        else "deepseek" if use_deepseek else "kilo" if use_kilo else "gemini" if use_gemini else "xai" if use_xai else "continuum" if use_continuum
        else use_ks.id if use_ks is not None else "ollama" if use_ollama else "lmstudio" if use_lmstudio else "local" if use_local else "remote" if use_remote
        else None if (use_codex or use_geminicli or use_grok) else "claude"
    )
    provider_label = _PROVIDER_SHORT.get(line_key or ("codex" if use_codex else "grok" if use_grok else "geminicli"))
    if use_remote:
        # The Speeds tab and health cards name the device that answered, by its nickname.
        pc = remote_svc.match(model)[0]
        provider_label = pc.id
        if _backup_model() and not _remote_may_be_tried(pc):
            raise _remote_unreachable(pc)        # already offline and its 30 s wait isn't over: the backup answers, nothing is sent to it
    model = _resolve_model_name(model)
    # Speeds tab: the resolved model string actually sent to the provider -- this is what
    # SkyrimNet's own variants config names too, so it's the right key for the Speeds tab's
    # per-model history and role chips (see speed_tracker / _get_role_chips_cached below).
    stats_model = model

    system_prompt = None
    system_marker = None
    cache_fixed = 0               # parts of the chat put right (a text that was not a plain string, an empty text part, a bare string or null where a part belongs)
    cache_markers_in = 0
    anthropic_messages = []
    for msg in req.messages:
        if msg.role == "system":
            # A system message is realistically always plain text in practice, but
            # _content_as_text keeps that true even for a hypothetical multimodal one -- nothing
            # downstream (the bare-Claude body template, the token estimate below) expects
            # system_prompt to ever be anything but a string or None.
            # (2026-10-20, Beta 27's prompt-caching plugin can send the fixed part of the prompt and the changing part as two system messages: both are kept, in order, so the
            # front of the prompt is still the first one. This used to keep only the last.)
            text = _content_as_text(msg.content)
            system_prompt = text if system_prompt is None else f"{system_prompt}\n\n{text}"
            if line_key == "claude" and system_marker is None:
                system_marker = next((mk for p in _cc._content_parts(msg.content) for mk in [_cc.clean_marker(p.get("cache_control"))] if mk), None)
        elif msg.role in ("user", "assistant"):
            content, n_fixed = _clean_content(msg.content, line_key in CACHE_MARKER_LANES)
            cache_fixed += n_fixed
            cache_markers_in += sum(1 for p in _cc._content_parts(msg.content) if "cache_control" in p) if not isinstance(msg.content, str) else 0
            anthropic_messages.append({"role": msg.role, "content": content})
    if system_marker and system_prompt:
        system_prompt = _cc.MarkedText(system_prompt, system_marker)

    if not anthropic_messages:
        raise HTTPException(status_code=400, detail="You didn't type a message.")

    if anthropic_messages[0]["role"] != "user":
        anthropic_messages.insert(0, {"role": "user", "content": "Continue."})

    # Merge consecutive same-role messages
    merged = []
    for msg in anthropic_messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = _merge_message_content(merged[-1]["content"], msg["content"])
        else:
            merged.append(msg)

    cache_found, cache_kept = (0, 0) if line_key == "claude" else _cap_message_markers(merged)       # (the Claude body is capped as a whole, with its system text, where it is built)
    if cache_fixed or cache_found > cache_kept or (cache_markers_in and line_key not in CACHE_MARKER_LANES):
        # Never the prompt text: only what was put right (2026-10-21).
        notes = []
        if cache_fixed:
            notes.append(f"{cache_fixed} message part(s) fixed (text that was not a string, was empty, or was sent as a bare value)")
        if cache_found > cache_kept:
            notes.append(f"kept {cache_kept} of {cache_found} cache markers")
        if cache_markers_in and line_key not in CACHE_MARKER_LANES:
            notes.append(f"{cache_markers_in} cache marker(s) removed (this provider does not use them)")
        logger.info(f"[{request_id}] [cache] " + "; ".join(notes))

    max_tokens = req.max_tokens or 4096
    # `multiproxy` is MultiProxy's own settings object (nothing reads it yet): it never goes to any provider.
    extra_params = {k: v for k, v in (req.model_extra or {}).items() if v is not None and k != MULTIPROXY_FIELD}
    _rec_ft = request_facts.current()
    if _rec_ft is not None and _rec_ft.overrides:           # the player's fine-tune settings for this job, translated for the model that answers (a setting it can't take is skipped)
        max_tokens, extra_params = _apply_overrides(_rec_ft, line_key or ("codex" if use_codex else "geminicli" if use_geminicli else "grok" if use_grok else "claude"), max_tokens, extra_params)
    extra_plain, tested_kind = None, None                     # tested settings (tested_settings.py): the request without the fixed answer format, for the one retry
    if _rec_ft is not None and _rec_ft.job_key:
        extra_params, extra_plain, tested_kind = _apply_tested(_rec_ft, line_key, model, system_prompt, merged, extra_params, bool(req.stream))
    # catch-broken-replies (2026-09-29): does THIS request's own prompt ask for JSON back? See
    # _expects_json_reply's own docstring -- computed once here since both the streaming and
    # non-streaming paths below, for both provider branches, need the same answer.
    wants_json = _expects_json_reply(system_prompt, merged)

    # Route to correct provider
    response = None
    real_usage: dict = {}
    if route:
        direct_fn, streaming_fn = route
        # May wait in line (or get a 429) BEFORE anything else starts -- and before the speed clock,
        # so Speeds measures the provider, not the queue. Released in the `finally` below, or, for a
        # stream, by _finish_stream when the stream ends (including a mid-stream disconnect). A
        # client that hangs up while still waiting leaves the line immediately.
        slot = await _acquire_slot_recorded(request, line_key, request_id, stats_model, provider_label)
        if req.stream:
            if wants_json:
                return await _open_json_stream(
                    request, streaming_fn(system_prompt, merged, model, max_tokens, **extra_params),
                    request_id, model, stats_model, slot, provider_label,
                    lambda: streaming_fn(system_prompt, merged, model, max_tokens, **extra_params),
                    request_start,
                )
            return await _open_stream(
                request, streaming_fn(system_prompt, merged, model, max_tokens, **extra_params),
                request_id, model, stats_model, slot, provider_label,
                pc_error=provider_label if (use_remote and _backup_model()) else None,
                first_word_budget=_first_word_budget(provider_label),
            )
        _speed_t0 = time.time()
        _budget = _first_word_budget(provider_label, whole_reply=True)
        try:
            response, real_usage = await _await_unless_disconnected(
                request, _within(_ask_with_safety_net(direct_fn, (system_prompt, merged, model, max_tokens), extra_params, extra_plain, tested_kind, request_id), _budget, provider_label),
            )
            if not wants_json and _rec_ft is not None:
                _rec_ft.ai_returned()                      # (a JSON check may ask the AI again, so only a plain reply is marked here)
        except _ClientGone:
            # SkyrimNet hung up before this AI answered: saved as a failed request that waited this long, so a model that is asked and never replies still shows on Speeds
            await _record_speed(stats_model, time.time() - _speed_t0, False, fail_reason=GAVE_UP, provider=provider_label)
            _after_silence(provider_label, stats_model)
            raise
        except Exception as e:
            await _record_speed(stats_model, time.time() - _speed_t0, False, fail_reason=_fail_reason(e), provider=provider_label, fail_note=_fail_note(e))
            if _budget and _fail_note(e) == engine_trouble.SLOW_CLAUSE:
                _after_silence(provider_label, stats_model)
            raise
        finally:
            if slot:
                slot.release()
        if engine_trouble.says_nothing(response, structured=wants_json):
            # An AI that answers with nothing has not answered: a failure like any other (the route's backup is tried), never a success that leaves the NPC silent
            empty = EmptyReply(bool((real_usage or {}).get("reasoned")))
            logger.warning(f"[{request_id}] {stats_model} answered with nothing")
            await _record_speed(stats_model, time.time() - _speed_t0, False, fail_reason=EMPTY, fail_note=_fail_note(empty), provider=provider_label)
            raise empty
        _usage_to_record(real_usage)
        extra_failure = False
        if wants_json:
            try:
                response, reply_repair, extra_failure = await _check_and_repair_json_reply(
                    response, wants_json,
                    lambda: direct_fn(system_prompt, merged, model, max_tokens, **extra_params),
                    request, request_id, model, request_start,
                )
            except _StillBrokenJsonReply:
                await _record_speed(stats_model, time.time() - _speed_t0, False, fail_reason="broken reply", provider=provider_label)
                raise HTTPException(status_code=502, detail="The AI gave a broken answer, even after asking again.")
        elif FIX_NPC_TAGS:
            # A JSON-expecting reply skips the tag fixer entirely (see the block above) -- running
            # markup repair on top of a JSON body risks mangling the structure itself (a stray "<"
            # or "*" inside a quoted string field is real content, not broken NPC markup).
            response, tag_repairs = sanitize_response(response, request_id, model)
            reply_repair = "repaired" if tag_repairs else "fine"
        else:
            reply_repair = None
        await _record_speed(
            stats_model, time.time() - _speed_t0, True, provider=provider_label,
            out_chars=len(response or ""), out_tokens=_real_output_tokens(real_usage, openai_shaped),
            reply_repair=reply_repair,
        )
        if extra_failure:
            # FixBrokenReplies is off -- `response` above is untouched, but this still counts.
            await _record_speed(stats_model, 0.0, False, fail_reason="broken reply", provider=provider_label)
    else:
        # Ensure auth is available (queues behind refresh if one is in progress). Claude is
        # optional -- a failure here (no CLI, capture failed, org disabled access, etc.) must
        # return a clean, actionable error instead of raising an unhandled 500 or hanging a stream.
        # See AuthCache.ensure_ready / capture_auth for the rate-limited retry + last_error tracking
        # that keeps this from spawning `claude --print` on every single request while it's broken.
        # A real 503 for streams too (it used to be a "200" carrying an error message -- SkyrimNet's
        # smart routing needs a real failure status to route around).
        try:
            await auth.ensure_ready(timeout=60)
        except Exception as e:
            raise HTTPException(status_code=503, detail=_claude_unavailable_message(str(e)))

        # Claude's line is joined only once its login is confirmed ready, so a wait for the login
        # never counts as time spent in the line.
        slot = await _acquire_slot_recorded(request, line_key, request_id, stats_model, provider_label)

        if req.stream:
            # Streaming must handle retry inside the generator (can't be caught outside)
            if wants_json:
                return await _open_json_stream(
                    request, call_api_streaming_with_retry(system_prompt, merged, model, max_tokens),
                    request_id, model, stats_model, slot, provider_label,
                    lambda: call_api_streaming_with_retry(system_prompt, merged, model, max_tokens),
                    request_start,
                )
            return await _open_stream(
                request, call_api_streaming_with_retry(system_prompt, merged, model, max_tokens),
                request_id, model, stats_model, slot, provider_label,
            )

        # Non-stream: retry once after a forced refresh on 401/403
        _speed_t0 = time.time()
        _speed_ok = False
        _abandoned = False
        _speed_reason = None
        _speed_note = None
        reply_repair = None
        extra_failure = False
        try:
            for attempt in range(2):
                try:
                    response, real_usage = await _await_unless_disconnected(
                        request, call_api_direct(system_prompt, merged, model, max_tokens),
                    )
                    if not wants_json and _rec_ft is not None:
                        _rec_ft.ai_returned()
                    _speed_ok = True
                    _usage_to_record(real_usage)
                    break
                except HTTPException as e:
                    if e.status_code in (401, 403) and attempt == 0:
                        logger.warning("Auth invalid -> forcing refresh once and retrying request")
                        auth.invalidate()
                        try:
                            await auth.ensure_ready(force=True, timeout=60)
                        except Exception as e2:
                            raise HTTPException(status_code=503, detail=_claude_unavailable_message(str(e2)))
                        continue
                    raise
            if engine_trouble.says_nothing(response, structured=wants_json):
                _speed_ok = False
                _speed_reason = EMPTY
                _speed_note = engine_trouble.EMPTY_CLAUSE
                raise EmptyReply()
            if wants_json:
                try:
                    response, reply_repair, extra_failure = await _check_and_repair_json_reply(
                        response, wants_json,
                        lambda: call_api_direct(system_prompt, merged, model, max_tokens),
                        request, request_id, model, request_start,
                    )
                except _StillBrokenJsonReply:
                    _speed_ok = False
                    _speed_reason = "broken reply"
                    raise HTTPException(status_code=502, detail="The AI gave a broken answer, even after asking again.")
            elif FIX_NPC_TAGS:
                # See the route branch's own comment above: JSON replies never reach the tag fixer.
                response, tag_repairs = sanitize_response(response, request_id, model)
                reply_repair = "repaired" if tag_repairs else "fine"
        except _ClientGone:
            _speed_reason = GAVE_UP
            raise
        except asyncio.CancelledError:
            _abandoned = True
            raise
        except Exception as e:
            # `or` (not plain assignment): the JSON-repair branch above may have already set this to
            # "broken reply" before raising -- _fail_reason(HTTPException(502, ...)) would otherwise
            # overwrite that specific reason with the generic "server" bucket.
            _speed_reason = _speed_reason or _fail_reason(e)
            _speed_note = _speed_note or _fail_note(e)
            raise
        finally:
            if slot:
                slot.release()
            if not _abandoned:
                await _record_speed(
                    stats_model, time.time() - _speed_t0, _speed_ok, provider=provider_label, fail_reason=_speed_reason, fail_note=_speed_note,
                    out_chars=len(response or "") if _speed_ok else None,
                    out_tokens=_real_output_tokens(real_usage, openai_shaped) if _speed_ok else None,
                    reply_repair=reply_repair if _speed_ok else None,
                )
                if extra_failure:
                    # FixBrokenReplies is off -- `response` above is untouched, but this still counts.
                    await _record_speed(stats_model, 0.0, False, fail_reason="broken reply", provider=provider_label)

    if not response:
        raise HTTPException(status_code=500, detail="Empty response")

    # The tag fixer (prose) and the JSON repair-or-retry check both already ran, per-branch, right
    # where `response` was obtained -- see wants_json above -- since each needs to happen BEFORE that
    # branch's own success _record_speed call, not after. This is just the full-reply log, the one
    # thing that still only needs a single shared spot regardless of which branch ran. (Errors never
    # get here: they raise above, so proxy-written error text is never "repaired" or logged as a reply.)
    if LOG_FULL_REPLIES:
        _log_reply(model, request_id, response)

    # Prefer the provider's own real token counts; fall back to a char/4 estimate only when
    # the provider didn't give us any (e.g. a malformed response body).
    real_usage = real_usage or {}
    if openai_shaped:
        prompt_tokens = real_usage.get("prompt_tokens")
        completion_tokens = real_usage.get("completion_tokens")
    else:
        prompt_tokens = real_usage.get("input_tokens")
        completion_tokens = real_usage.get("output_tokens")

    if prompt_tokens is None or completion_tokens is None:
        prompt_text = (system_prompt or "") + " ".join(_content_as_text(m["content"]) for m in merged)
        prompt_tokens = prompt_tokens if prompt_tokens is not None else len(prompt_text) // 4
        completion_tokens = completion_tokens if completion_tokens is not None else len(response) // 4

    if openai_shaped:
        # GLM/Nano-GPT are already OpenAI-format -- use their own finish_reason as-is.
        finish_reason = real_usage.get("finish_reason") or "stop"
    else:
        finish_reason = map_anthropic_stop_reason(real_usage.get("stop_reason"))

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response, "name": None},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": None,
    }


@app.post("/config/glm-key")
async def set_glm_key(request: Request):
    global glm_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        glm_api_key = None
        cfg.pop("glm_api_key", None)
        _save_config(cfg)
        logger.info("GLM (z.ai) API key cleared")
        return {"status": "cleared"}
    glm_api_key = key
    cfg["glm_api_key"] = key
    _save_config(cfg)
    logger.info("GLM (z.ai) API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/nano-key")
async def set_nano_key(request: Request):
    global nano_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        nano_api_key = None
        cfg.pop("nano_api_key", None)
        _save_config(cfg)
        logger.info("Nano-GPT API key cleared")
        return {"status": "cleared"}
    nano_api_key = key
    cfg["nano_api_key"] = key
    _save_config(cfg)
    decision_handles.nano_line = ""            # a new key: the old "Nano-GPT said no" no longer applies
    logger.info("Nano-GPT API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/perplexity-key")
async def set_perplexity_key(request: Request):
    global perplexity_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    decision_handles.provider_lines.pop("perplexity", None)            # a new (or removed) key: the old "Perplexity said no" no longer applies
    if not key:
        perplexity_api_key = None
        cfg.pop("perplexity_api_key", None)
        _save_config(cfg)
        logger.info("Perplexity API key cleared")
        return {"status": "cleared"}
    perplexity_api_key = key
    cfg["perplexity_api_key"] = key
    _save_config(cfg)
    logger.info("Perplexity API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/openai-key")
async def set_openai_key(request: Request):
    global openai_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        openai_api_key = None
        cfg.pop("openai_api_key", None)
        _save_config(cfg)
        logger.info("OpenAI API key cleared")
        return {"status": "cleared"}
    openai_api_key = key
    cfg["openai_api_key"] = key
    _save_config(cfg)
    logger.info("OpenAI API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/deepseek-key")
async def set_deepseek_key(request: Request):
    global deepseek_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        deepseek_api_key = None
        cfg.pop("deepseek_api_key", None)
        _save_config(cfg)
        logger.info("DeepSeek API key cleared")
        return {"status": "cleared"}
    deepseek_api_key = key
    cfg["deepseek_api_key"] = key
    _save_config(cfg)
    logger.info("DeepSeek API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/typesafe-key")
async def set_typesafe_key(request: Request):
    global typesafe_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        typesafe_api_key = None
        cfg.pop("typesafe_api_key", None)
        _save_config(cfg)
        logger.info("TypeSafe (Jev) API key cleared")
        return {"status": "cleared"}
    typesafe_api_key = key
    cfg["typesafe_api_key"] = key
    _save_config(cfg)
    logger.info("TypeSafe (Jev) API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/kilo-key")
async def set_kilo_key(request: Request):
    global kilo_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        kilo_api_key = None
        cfg.pop("kilo_api_key", None)
        _save_config(cfg)
        logger.info("Kilo Gateway API key cleared")
        return {"status": "cleared"}
    kilo_api_key = key
    cfg["kilo_api_key"] = key
    _save_config(cfg)
    logger.info("Kilo Gateway API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/gemini-key")
async def set_gemini_key(request: Request):
    global gemini_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        gemini_api_key = None
        cfg.pop("gemini_api_key", None)
        _save_config(cfg)
        _gemini_models_cache.clear()
        logger.info("Gemini API key cleared")
        return {"status": "cleared"}
    gemini_api_key = key
    cfg["gemini_api_key"] = key
    _save_config(cfg)
    _gemini_models_cache.clear()  # a new/changed key may see a different live catalog
    logger.info("Gemini API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/xai-key")
async def set_xai_key(request: Request):
    global xai_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        xai_api_key = None
        cfg.pop("xai_api_key", None)
        _save_config(cfg)
        _xai_models_cache.clear()
        logger.info("xAI API key cleared")
        return {"status": "cleared"}
    xai_api_key = key
    cfg["xai_api_key"] = key
    _save_config(cfg)
    _xai_models_cache.clear()  # a changed key may see a different live list
    logger.info("xAI API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/continuum-key")
async def set_continuum_key(request: Request):
    global continuum_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        continuum_api_key = None
        cfg.pop("continuum_api_key", None)
        _save_config(cfg)
        _continuum_models_cache.clear()
        logger.info("Continuum API key cleared")
        return {"status": "cleared"}
    continuum_api_key = key
    cfg["continuum_api_key"] = key
    _save_config(cfg)
    _continuum_models_cache.clear()  # a changed key may see a different live list
    logger.info("Continuum API key configured and saved to config.json")
    return {"status": "saved"}


@app.post("/config/ollama-key")
async def set_ollama_key(request: Request):
    global ollama_api_key
    data = await request.json()
    key = data.get("key", "").strip()
    cfg = _load_config()
    if not key:
        ollama_api_key = None
        cfg.pop("ollama_api_key", None)
        _save_config(cfg)
        _ollama_tags_cache.clear()  # about to target the local daemon instead -- don't serve stale Cloud rows
        logger.info("Ollama Cloud API key cleared -- ollama/* now targets the local daemon")
        return {"status": "cleared"}
    ollama_api_key = key
    cfg["ollama_api_key"] = key
    _save_config(cfg)
    _ollama_tags_cache.clear()  # a new/changed key may see a different Cloud catalog
    logger.info("Ollama Cloud API key configured and saved to config.json -- ollama/* now targets Ollama Cloud")
    return {"status": "saved"}


# --- Local models (2026-09-30): the dashboard's "Local models" card talks to these ---
def _local_error(e: "local_models.LocalModelError") -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"detail": e.message})


@app.get("/config/local-models")
async def local_models_state(refresh: bool = False):
    """Everything the Local models card draws: this device's graphics card, the engine, the model list
    with fit pills and download progress, and what's installed/running."""
    if refresh:
        await local_svc.gpu(refresh=True)
    snap = await local_svc.snapshot()
    snap["modelsOff"] = models_off.is_off(_load_config())
    snap["runsOn"] = next((r.label for r in remote_svc.all() if r.kind == "courier" and any(m.get("state") == "running" for m in r.models)), None) if snap["modelsOff"] else None
    try:
        elsewhere = await _models_elsewhere([m for m in local_models.CATALOG if not any(c["id"] == m["id"] and c["installed"] for c in snap["catalog"])])
    except Exception as e:                                  # a finder problem must never take the page down: the rows simply keep Download
        logger.debug(f"Local models: couldn't look in other programs ({e.__class__.__name__})")
        elsewhere = {}
    for c in snap["catalog"]:
        c["elsewhere"] = elsewhere.get(c["id"])
    return snap


_lmstudio_files_cache: dict = {"at": 0.0, "files": []}


async def _models_elsewhere(specs: list) -> dict:
    """{catalog id: {"program": "Ollama" or "LM Studio", "name": the name it has there, "useName": what to paste into SkyrimNet or None}} for the catalog models another program on THIS
    device already holds. Ollama is asked at its own address (the same list the Ollama provider shows); LM Studio's models folder is read with the Other devices page's own finder
    (courier_models.find, cached for a minute). Matching is by identity and size (model_matches.py); a program that cannot be read says nothing."""
    if not specs:
        return {}
    out: dict = {}
    tags = await _fetch_ollama_tags(OLLAMA_LOCAL_BASE_URL, None)
    for spec in specs:
        name = model_matches.match_ollama(spec, (tags or {}).get("models"))
        if name:
            out[spec["id"]] = {"program": "Ollama", "name": name, "useName": f"{OLLAMA_PREFIX}{name}"}
    rest = [s for s in specs if s["id"] not in out]
    if rest:
        now = time.monotonic()
        if now - _lmstudio_files_cache["at"] > 60:
            folder = courier_adapters.lm_models_dir()
            _lmstudio_files_cache["files"] = await asyncio.to_thread(courier_models.find, [("lmstudio", folder)]) if folder else []
            _lmstudio_files_cache["at"] = now
        files = _lmstudio_files_cache["files"]
        ids = None
        for spec in rest:
            f = model_matches.match_lmstudio(spec, files)
            if f:
                if ids is None:
                    listed = await _fetch_lmstudio_models(lmstudio_base_url)
                    ids = [m["id"] for m in listed or []]
                lm_id = model_matches.lmstudio_id(spec, ids)
                out[spec["id"]] = {"program": "LM Studio", "name": f["name"], "useName": f"{LMSTUDIO_PREFIX}{lm_id}" if lm_id else None}
    return out


@app.get("/config/local-models/speeds")
async def local_models_speeds():
    """How fast each local model really runs (speed_queries.local_model_speeds): the average of its last 20 warm replies under the setup it is running with now, the
    number from before the last settings change while fewer than 20 have been measured since, and the setup in plain words. Each model's `state` is `ok`,
    `not_enough` (fewer than 5 replies under this setup) or `not_reported` (its server sends no timing block). The Local models card and the Speeds tab both read this."""
    installed = await asyncio.to_thread(local_svc.installed)
    setups = {m.id: local_svc.current_setup(m.id) for m in installed}
    speeds = await _speed_cache.get(("local-speeds", SPEED_HISTORY_DB, tuple(sorted((k, v[0]) for k, v in setups.items()))),
                                    speed_queries.local_model_speeds, SPEED_HISTORY_DB, {k: v[0] for k, v in setups.items()})
    return {"models": [{"id": m.id, "name": m.name, "modelName": m.model_name, "setupWords": setups[m.id][1], **speeds.get(m.id, {"state": "not_enough", "requests": 0, "previous": None})}
                       for m in installed]}


def _per_request_context(settings: dict, override: dict) -> Optional[int]:
    """What one prompt can hold under a device's settings: the memory for conversations shared by the requests at the same time (the same sum the Local models card shows)."""
    try:
        ctx = int(override.get("ctx") or settings["ctx"])
        par = int(override.get("parallel") or settings["parallel"])
    except (KeyError, TypeError, ValueError):
        return None
    return ctx // max(1, par) if ctx > 0 else None


def _prompt_context(model: str, provider: Optional[str]) -> Optional[int]:
    """The most tokens one prompt can hold for a model, only when MultiProxy knows it for sure: this device's own models (its saved settings), a model on another device
    MultiProxy sent settings to (its saved record for that device), and an LM Studio model that is loaded (what it was loaded with). Cloud models, Ollama and anything else: None."""
    if provider == "Local":
        settings, override = local_svc.engine_settings.effective(local_engine_settings.DEVICE_LOCAL, strip_local_prefix(model))
        return _per_request_context(settings, override)
    if provider == "LM Studio":
        wanted = model[len(LMSTUDIO_PREFIX):] if model.startswith(LMSTUDIO_PREFIX) else model
        for m in (_lmstudio_cache.get(lmstudio_base_url, (0.0, None))[1] or []):
            if m.get("id") == wanted:
                return int(m["context"]) if m.get("context") else None
        return None
    match = remote_svc.match(model)
    if match is None:
        return None
    remote, model_id = match
    program = next((m.get("program") for m in remote.models if m.get("id") == model_id), None)
    if remote.kind not in ("courier", "multiproxy") or program not in (None, "llamacpp", "multiproxy"):
        return None                                 # an AI server of its own, or Ollama / LM Studio on that device: MultiProxy does not set their context
    rec = local_svc.engine_settings.get(remote.id)
    if not rec.saved:
        return None                                 # nothing was ever sent to that device: what it runs with is not MultiProxy's to say
    return _per_request_context(rec.settings, rec.models.get(model_id, {}))


@app.get("/config/prompt-size")
async def prompt_size_route(range: str = "today"):
    """Prompt size: per job in the range (hour | today | week), the model that answered it most and where it runs, the typical and the biggest prompt (tokens the AI reported;
    a request with no count is left out), the model's context when known and a verdict, plus the largest prompt in each of the last 24 hours. Read when the card is opened
    (prompt_size.py)."""
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    if range not in prompt_size.RANGES:
        raise HTTPException(status_code=400, detail="range must be one of: hour, today, week")
    groups = await _speed_cache.get(("prompt-size", SPEED_HISTORY_DB, range), prompt_size.read, SPEED_HISTORY_DB, time.time(), range)
    _refresh_job_names()
    jobs = prompt_size.finish(groups, context_of=_prompt_context, where_of=_speed_where, shown=_pc_shown, label_of=routing.label_of, order_of=routing.order_of)
    return {"range": range, "jobs": jobs}


@app.get("/config/local-models/memory")
async def local_models_memory(pc: str = "this"):
    """The Graphics memory card (Revision 5/6): this device's own picture, or another device's. `pcs` always lists
    the choices for the picker (it only shows when there's more than this device)."""
    gpu = await local_svc.gpu()
    pcs = [{"id": "this", "label": f"This device · {gpu.name}" if gpu else "This device", "kind": "this", "online": True}]
    for r in remote_svc.all():
        if r.kind == "openai":
            pcs.append({"id": r.id, "label": f"{r.label} ({r.program or 'AI server'})", "kind": "own", "online": r.online is not False})
        else:
            card = f" · {r.gpu['name']}" if r.gpu and r.gpu.get("name") else ""
            pcs.append({"id": r.id, "label": f"{r.label}{card}", "kind": "multiproxy", "online": r.online is not False})
    if pc == "this" or not remote_svc.get(pc):
        return {"pc": "this", "pcs": pcs, **(await gpu_monitor_svc.payload())}
    remote = remote_svc.get(pc)
    return {"pc": pc, "pcs": pcs, **(await remote_svc.memory(remote))}


@app.post("/config/local-models/memory/reset")
async def local_models_memory_reset():
    """Forget Skyrim's busiest point (after the mod list changed); the fit icons fall back to the guess."""
    gpu_monitor_svc.reset_peak()
    return {"status": "reset"}


@app.post("/config/local-models/folder")
async def local_models_set_folder(request: Request):
    data = await request.json()
    try:
        folder = local_svc.set_folder(str(data.get("path", "")).strip())
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "saved", "folder": str(folder)}


@app.post("/config/local-models/pick-folder")
async def local_models_pick_folder():
    """A real folder picker on this device (the dashboard is local). Returns the chosen folder, or null
    if the player closed the dialog."""
    return {"path": await asyncio.to_thread(local_models.pick_folder_dialog)}


@app.post("/config/local-models/pick-file")
async def local_models_pick_file():
    return {"path": await asyncio.to_thread(local_models.pick_file_dialog)}


@app.post("/config/local-models/engine/download")
async def local_models_download_engine(request: Request):
    data = await request.json()
    try:
        local_svc.download_engine(str(data.get("kind", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "started"}


@app.get("/config/local-models/engine/update")
async def local_models_engine_update_info():
    """Is a newer numbered llama.cpp build available for the installed engine? Nothing is installed by asking."""
    info = await local_svc.engine_update_info()
    return {"available": bool(info.get("available")), "build": info.get("build"), "sizeBytes": info.get("sizeBytes")}


@app.post("/config/local-models/engine/update")
async def local_models_engine_update():
    """The Update button: download the newest numbered build of the same kind of engine and switch to it."""
    try:
        await local_svc.update_engine()
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "started"}


@app.post("/config/local-models/model/download")
async def local_models_download_model(request: Request):
    data = await request.json()
    try:
        local_svc.download_model(str(data.get("id", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "started"}


@app.post("/config/local-models/cancel")
async def local_models_cancel(request: Request):
    data = await request.json()
    await local_svc.cancel(str(data.get("key", "")))
    return {"status": "cancelled"}


@app.post("/config/local-models/pause")
async def local_models_pause(request: Request):
    """Pause a model or runner download: it stops now and keeps what it fetched."""
    data = await request.json()
    await local_svc.pause(str(data.get("key", "")))
    return {"status": "paused"}


@app.post("/config/local-models/resume")
async def local_models_resume(request: Request):
    """Carry on a paused model or runner download from the byte it reached (a second click while it runs starts nothing new)."""
    data = await request.json()
    try:
        local_svc.resume(str(data.get("key", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "started"}


@app.post("/config/local-models/add")
async def local_models_add_custom(request: Request):
    data = await request.json()
    try:
        m = local_svc.add_custom(str(data.get("path", "")), str(data.get("mmproj", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "added", "modelName": m.model_name}


@app.post("/config/local-models/remove")
async def local_models_remove(request: Request):
    data = await request.json()
    await local_svc.remove(str(data.get("id", "")), delete_files=data.get("deleteFiles") is True)      # the files stay unless the player chose "delete files"
    return {"status": "removed"}


@app.post("/config/local-models/add-kept")
async def local_models_add_kept(request: Request):
    data = await request.json()
    try:
        await local_svc.add_kept(str(data.get("id", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "added"}


@app.post("/config/local-models/delete-kept")
async def local_models_delete_kept(request: Request):
    data = await request.json()
    try:
        await local_svc.delete_kept(str(data.get("id", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "deleted"}


@app.post("/config/local-models/start")
async def local_models_start(request: Request):
    data = await request.json()
    s = await local_svc.start(str(data.get("id", "")))
    return {"status": s.state, "message": s.message}


@app.post("/config/local-models/stop")
async def local_models_stop(request: Request):
    data = await request.json()
    await local_svc.stop(str(data.get("id", "")))
    return {"status": "stopped"}


# --- Local model settings (2026-10-04): the saved settings, the checks and the memory estimate (local_engine_api.py) ---
# The settings belong to a DEVICE (`device`: `local` is this device; another device uses its own id). Every route returns plain JSON; a refused save is a 400 whose
# `detail` is the first problem and whose `checks` lists all of them.
#   GET  /config/local-models/settings?device=local        the device's settings, per-model overrides, presets, checks, memory estimate and what was reset
#   POST /config/local-models/settings                      {device, settings?, models?: {id: {ctx, parallel} | null}, apply: save | restart-model | restart-all, modelId?}
#   POST /config/local-models/settings/reset                {device, modelId?, suggested?}: back to the defaults (or the suggested preset), or one model back to the device
#   POST /config/local-models/settings/parse                {text}: a pasted command line sorted into understood / extra / refused / not available / invalid
#   POST /config/local-models/settings/estimate             {device, settings?, models?, modelId?, confirm?}: "about X of Y GB"; confirm also runs the engine's own memory tool
#   GET  /config/local-models/settings/failure?id=          the "engine did not start" message with the engine's own last lines
#   POST /config/local-models/settings/start-without-extras {id}: start that model once without the player's extra flags
def _remote_for_device(device: str):
    """The Courier device a settings key means: its permanent id (what its settings are kept under), or its nickname."""
    r = remote_svc.get(device) if device else None
    if r is None:
        r = next((x for x in remote_svc.all() if x.kind == "courier" and x.instance and x.instance == device), None)
    return r if r is not None and r.kind == "courier" else None


async def _engine_hardware(device: str) -> "local_engine_settings.HardwareFacts":
    """Another device's graphics card as its own Courier report says: what the presets and the Experimental label need (name, memory, vendor, how many cards)."""
    r = _remote_for_device(device)
    g = (r.gpu if r else None) or {}
    mem = g.get("memoryGb")
    cards = [x for x in ((r.report or {}).get("gpus") or []) if isinstance(x, dict)] if r else []
    return local_engine_settings.HardwareFacts(g.get("name") or "", g.get("vendor") or "", max(len(cards), 1) if g.get("name") else 0, int(mem * 1024) if mem else None)


def _engine_models(device: str) -> list:
    """The models on a Courier device, from its own report: no file here to read, so no estimate; each carries what its last start really did."""
    from types import SimpleNamespace
    if device == local_engine_settings.DEVICE_LOCAL:
        return list(local_svc.installed())
    r = _remote_for_device(device)
    out = []
    for p in ((r.report or {}).get("programs") or []) if r else []:
        if p.get("id") == "llamacpp":
            out += [SimpleNamespace(id=m["id"], name=m.get("name") or m["id"], path=None, mmproj=None, started=m.get("started")) for m in p.get("models") or []]
    return out


engine_settings_sender = local_engine_sync.SettingsSender(local_svc.engine_settings, _remote_for_device, local_models.LLAMA_CPP_BUILD, logger)
remote_svc.report_hooks.append(lambda r: engine_settings_sender.on_report(r) if r.kind == "courier" else None)       # a Courier that does not hold its current settings is sent them


def _engine_device_key(device: str) -> str:
    r = _remote_for_device(device)
    return engine_settings_sender.device_key(r) if r is not None else device


engine_settings_api = local_engine_api.EngineSettingsApi(
    local_svc, hardware_fn=_engine_hardware, models_fn=_engine_models, online_fn=lambda device: (lambda r: None if r is None else r.online is not False)(_remote_for_device(device)),
    sender=engine_settings_sender, resolve_fn=_engine_device_key, name_fn=lambda device: (lambda r: r.name if r is not None else "")(_remote_for_device(device)))


def _engine_settings_error(e: "local_engine_api.ApiError") -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"detail": e.message, "checks": e.checks})


@app.get("/config/local-models/settings")
async def engine_settings_state(device: str = "local"):
    return await engine_settings_api.state(device)


@app.post("/config/local-models/settings")
async def engine_settings_save(request: Request):
    data = await request.json()
    try:
        return await engine_settings_api.save(str((data or {}).get("device") or "local"), data)
    except local_engine_api.ApiError as e:
        return _engine_settings_error(e)


@app.post("/config/local-models/settings/reset")
async def engine_settings_reset(request: Request):
    data = await request.json()
    return await engine_settings_api.reset(str(data.get("device") or "local"), str(data["modelId"]) if data.get("modelId") else None, bool(data.get("suggested")))


@app.post("/config/local-models/settings/parse")
async def engine_settings_parse(request: Request):
    data = await request.json()
    return engine_settings_api.parse(str(data.get("text", "")))


@app.post("/config/local-models/settings/estimate")
async def engine_settings_estimate(request: Request):
    data = await request.json()
    try:
        return await engine_settings_api.estimate(str((data or {}).get("device") or "local"), data)
    except local_engine_api.ApiError as e:
        return _engine_settings_error(e)


@app.get("/config/local-models/settings/failure")
async def engine_settings_failure(id: str = ""):
    return engine_settings_api.failure(id)


@app.post("/config/local-models/settings/start-without-extras")
async def engine_settings_start_without_extras(request: Request):
    data = await request.json()
    return await engine_settings_api.start_without_extras(str(data.get("id", "")))


# --- Other devices (2026-09-30): the dashboard's Other devices tab talks to these ---
def _other_error(e: "other_pcs.OtherPcsError") -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"detail": e.message})


def _retire_model_server_settings() -> None:
    """The "MultiProxy Model Server" mode and the "Share with my other devices" door are gone (2026-10-01). A leftover setting from
    an older install would only make this device think it has no Skyrim, so it's removed (once, with one plain log line)."""
    cfg = _load_config()
    gone = [k for k in ("model_server", "share") if k in cfg]
    if gone:
        for k in gone:
            cfg.pop(k, None)
        _save_config(cfg)
        logger.info("This device used to run as a MultiProxy Model Server. That is replaced by the MultiProxy Courier now: "
                    "put the Courier on the spare device instead.")


# --- Routing: what the Routing screen reads and writes (2026-10-02) ---
def _provider_label_for(ai: str) -> str:
    src = key_sources.match(ai)
    if src:
        return _PROVIDER_SHORT[src.id]
    for check, key in ((is_glm_model, "glm"), (is_nano_model, "nano"), (is_openai_model, "openai"), (is_deepseek_model, "deepseek"), (is_kilo_model, "kilo"), (is_gemini_model, "gemini"),
                       (is_ollama_model, "ollama"), (is_lmstudio_model, "lmstudio"), (is_local_model, "local"), (is_codex_model, "codex"), (is_geminicli_model, "geminicli"), (is_grok_model, "grok"), (is_xai_model, "xai"), (is_continuum_model, "continuum")):
        if check(ai):
            return _PROVIDER_SHORT[key]
    if is_remote_model(ai):
        return ai.split("/", 1)[0]
    if is_unclaimed_slash_model(ai):
        return "not set up"                                  # a name no provider here claims (it used to go to OpenRouter)
    return _PROVIDER_SHORT["claude"]


def _ai_name(ai: str) -> tuple:
    """(the model as a player reads it, who serves it): 'gemma-4-12b', 'LM Studio'."""
    if is_remote_model(ai):
        nick = ai.split("/", 1)[0]
        pc = remote_svc.get(nick)
        return ai.split("/", 1)[1], (pc.label if pc else nick)           # the device's name as typed or found, not its lower-case id
    return _resolve_model_name(ai), _provider_label_for(ai)


def _routing_history() -> tuple:
    """(per model, last 24 hours: {calls, ok, first_word median}; {job key: how it was last known}) from the Speeds history. Empty when it can't be read."""
    try:
        return speed_queries.routing_history(SPEED_HISTORY_DB, time.time())      # two indexed reads (the day's rows, the last 600 jobs): cheap at any size
    except Exception as e:
        logger.debug(f"Routing view: couldn't read the Speeds history ({e.__class__.__name__})")
    return {}, {}


async def _routing_view() -> dict:
    _refresh_job_names()

    async def list_ais() -> list:
        try:
            return [{"id": m["id"], "owned_by": m.get("owned_by", ""), "kinds": model_kinds.BOOK.get(m["id"])} for m in (await list_models())["data"]]
        except Exception as e:
            logger.warning(f"Routing view: couldn't list the AIs ({e.__class__.__name__})")
            return []
    # The page asks every 2 s (and so may a second tab): the AI list is rebuilt at most every 5 s and the last-24-hours numbers at most
    # every 10 s, one shared computation for everyone asking at once, and a poll never waits for the rebuild (it gets the last list while one is
    # made; perf check 2026-10-02: 117 ms median per poll before).
    reachable = await poll_cache.shared("routing-reachable", lambda v: 5.0, list_ais, stale_ok=True, max_stale=_DEVICE_VIEW_MAX_STALE)
    stats, seen = await poll_cache.shared("routing-history", lambda v: 10.0, lambda: asyncio.to_thread(_routing_history), stale_ok=True)
    status = _routing_status()
    _seen_how.clear()
    _seen_how.update(seen or {})
    _copy_split_routes()
    view = routing_view.build(
        named=_named_seen, settings=_routing_settings(), table=_skyrimnet_job_table(), status=status, reachable=reachable, remotes={r.id: r.kind for r in remote_svc.all()}, remote_names={r.id: r.label for r in remote_svc.all()},
        stats=stats, seen=seen, costs=lambda job, k: _routing_engine.costs.average(job, k), classify=_ai_class, key_of=_engine_key, name_of=_ai_name,
        now_resting={_engine_key(r["ai"]): r["secondsLeft"] for r in status["resting"]},
        last_sent=_last_sent.get, accepts=lambda ai, name: name in _OVERRIDE_ACCEPTS.get(_override_kind(ai), set()), trouble=_trouble.clause,
    )
    view["keepLoaded"] = _keepwarm_cards(view["settings"])
    view["tested"] = _tested_cards(view)
    view["decisions"] = _routing_decisions_card()
    return view


def _routing_decisions_card() -> Optional[dict]:
    """Where the local decision model really runs, for the read-only card on the Routing page: {"place": "this" | "pc:<device>", "name": the model, "state": "on" | "off", "word"}.
    None when it is not installed anywhere (nothing new is shown)."""
    try:
        loc = decision_handles.location()
        st = decision_handles.active().status()
        if not st.get("installed"):
            return None
        spec = decision_wiring.de.MODELS.get(st.get("model") or "")
        running = st.get("state") in ("ready", "starting", "getting_ready")
        return {"place": "this" if loc == "this" else "pc:" + loc, "name": spec.label if spec else "Decisions", "state": "on" if st.get("state") == "ready" else ("warn" if running else "off"),
                "word": "Running" if st.get("state") == "ready" else ("Starting" if running else "Stopped")}
    except Exception as e:
        logger.debug(f"Routing view: no decisions card ({e.__class__.__name__})")
        return None


def _tested_cards(view: dict) -> dict:
    """{job key: the "Settings for this model" section} for each job whose first choice is a local model that has tested settings (tested_settings.card)."""
    out = {}
    for j in view.get("jobs", []):
        try:
            ai = (j.get("onAis") or [""])[0]
            if not ai or tested_settings.job_kind((j["key"],)) is None:
                continue
            kind = _override_kind(ai)
            engine, _ = _tested_engine(kind, ai)
            c = tested_settings.card(j["key"], ai, kind, None if engine is None else _tested_program(kind, ai), (view["settings"].get("tested") or {}).get(j["key"], {}), _last_sent.get(j["key"]))
            if c:
                c["answeredBy"] = _ai_name(ai)[0]
                out[j["key"]] = c
        except Exception as e:
            logger.debug(f"Routing view: no tested settings for {j.get('key')} ({e.__class__.__name__})")
    return out


def _tested_program(kind: str, ai: str) -> Optional[str]:
    if kind != "remote":
        return None
    m = remote_svc.match(ai)
    return (next((x.get("program") for x in (m[0].models or []) if x.get("id") == m[1] and x.get("program")), None) or m[0].program) if m else None


def _keepwarm_cards(settings: dict) -> dict:
    """{route entry (ollama/model): what its card shows} for each local Ollama model that is the first choice of a route (ollama_keepwarm.card)."""
    if ollama_api_key:
        return {}
    off = {ollama_keepwarm.full_name(strip_ollama_prefix(x)) for x in settings.get("keep_loaded_off", [])}
    out = {}
    for r in (settings.get("routes") or {}).values():
        ai = (r.get("ais") or [""])[0]
        if ai and "," not in ai and is_ollama_model(ai):
            c = _keepwarm.card(strip_ollama_prefix(ai), off)
            c.update(name=ollama_keepwarm.full_name(strip_ollama_prefix(ai)), label=ollama_keepwarm.LABEL, why=ollama_keepwarm.WHY)
            out[ai] = c
    return out


@app.get("/live/stream")
async def live_stream(thinking: int = 1):
    """The Live view, as server-sent events: the last few request lines, then each new one (and the reasoning of models that share it, when asked for). Nothing is
    written anywhere; a page that is closed, gone or slow costs the requests nothing."""
    return StreamingResponse(_live.stream(thinking=bool(thinking)), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/config/routing")
async def get_routing():
    return await _routing_view()


@app.post("/config/routing/replace-model")
async def routing_replace_model(request: Request):
    """The "Use <model> instead" button on a model its provider no longer offers: every route that names `old` names `new` instead (other jobs are left alone)."""
    try:
        body = await request.json()
    except Exception:
        body = None
    old, new = (str(body.get("old") or "").strip(), str(body.get("new") or "").strip()) if isinstance(body, dict) else ("", "")
    if not old or not new:
        raise HTTPException(status_code=400, detail="Send the model to replace and the one to use.")
    cfg = _load_config()
    cfg["routing"] = routing.clean_settings(routing_view.swap_model(routing.clean_settings(cfg.get("routing")), old, new))
    _save_config(cfg)
    _keepwarm_kick.set()
    return await _routing_view()


@app.post("/config/routing")
async def set_routing(request: Request):
    """Save the routes (and the resting and slowness settings). The whole `routing` block is sent and replaced; anything that isn't a known job, a known way of
    choosing or a sensible number is dropped. A job with no route goes back to being answered by its model name."""
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Send the routing settings as a JSON object.")
    cfg = _load_config()
    cfg["routing"] = routing.clean_settings(body)
    _save_config(cfg)
    _keepwarm_kick.set()                                  # a route may now start with an Ollama model (or the switch changed): look now, not in 30 s
    return await _routing_view()


# --- Share a setup as a file (2026-10-25): Settings > Share your setup. The file logic is setup_file.py; these are the dialogs and the wiring. ---
_SETUP_TYPES = [("MultiProxy setup", "*" + setup_file.EXTENSION), ("All files", "*.*")]
_setup_pending: dict = {}                       # the one list a player is looking at: {"token", "plan"}


def _setup_unavailable_one(part: str, listed: set, wild: tuple) -> str:
    """"" when this PC can use that model, else why not, as a plain sentence."""
    if is_remote_model(part):
        return "It runs on another PC."
    provider = _provider_label_for(part)
    if provider == _PROVIDER_SHORT["claude"] and not is_unclaimed_slash_model(part):
        return "" if auth.is_ready else "You are not signed in to Claude."
    if part in listed or any(part.startswith(w) for w in wild):
        return ""
    if is_local_model(part) or is_ollama_model(part) or is_lmstudio_model(part):
        return "You do not have this model."
    if is_unclaimed_slash_model(part):
        return "This PC cannot use it."
    return f"{provider} is not set up on this PC."


async def _setup_have():
    ids = [m["id"] for m in (await list_models()).get("data", [])]
    listed, wild = set(ids), tuple(i[:-1] for i in ids if i.endswith("*"))

    def have(ai: str) -> str:
        for part in _expand_route_entry(ai):
            why = _setup_unavailable_one(part.strip(), listed, wild)
            if why:
                return why
        return ""
    return have


@app.get("/config/setup")
async def setup_state():
    return {"canUndo": setup_file.can_undo(os.path.dirname(CONFIG_FILE))}


@app.post("/config/setup/save")
async def setup_save():
    """Save my setup: a Save dialog (the player always picks the place), then the file. Nothing is said back about where it went."""
    path = await asyncio.to_thread(local_models.save_file_dialog, "Save my setup", setup_file.suggested_name(), _SETUP_TYPES, setup_file.EXTENSION)
    if not path:
        return {"saved": False}
    block = _routing_settings()
    try:
        text = setup_file.dumps(setup_file.build(block, lambda ai: not any(is_remote_model(p.strip()) for p in _expand_route_entry(ai))))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except setup_file.SetupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError:
        raise HTTPException(status_code=400, detail="Could not save the file there. Pick another place.")
    return {"saved": True}


@app.post("/config/setup/open")
async def setup_open():
    """Load a setup, step 1: an Open dialog, then what would change. Nothing changes yet."""
    path = await asyncio.to_thread(local_models.pick_file_dialog, "Load a setup", _SETUP_TYPES)
    if not path:
        return {"cancelled": True}
    try:
        if os.path.getsize(path) > setup_file.MAX_BYTES:
            raise setup_file.SetupError(setup_file.NOT_A_SETUP)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        doc = setup_file.parse(text)
    except setup_file.SetupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (OSError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail=setup_file.NOT_A_SETUP)
    _refresh_job_names()
    plan = setup_file.plan(doc, _routing_settings(), await _setup_have(), lambda ai: _ai_name(ai)[0], routing.label)
    if not plan["lines"]:
        raise HTTPException(status_code=400, detail=setup_file.NOTHING_NEW)
    token = uuid.uuid4().hex
    _setup_pending.clear()
    _setup_pending.update({"token": token, "plan": plan})
    return {"token": token, "file": os.path.basename(path), "count": plan["count"], "lines": [{"text": ln["text"], "skip": ln["skip"], "warn": ln["warn"]} for ln in plan["lines"]]}


@app.post("/config/setup/apply")
async def setup_apply(request: Request):
    """Load a setup, step 2: every usable line at once, after the settings file is backed up."""
    try:
        body = await request.json()
    except Exception:
        body = None
    if not _setup_pending or not isinstance(body, dict) or body.get("token") != _setup_pending.get("token"):
        raise HTTPException(status_code=400, detail="Choose the file again.")
    plan = _setup_pending["plan"]
    _setup_pending.clear()
    try:
        setup_file.backup_file(CONFIG_FILE, os.path.dirname(CONFIG_FILE))
    except OSError:
        raise HTTPException(status_code=400, detail="Could not back up your settings, so nothing was loaded.")
    cfg = _load_config()
    cfg["routing"] = setup_file.apply(plan, routing.clean_settings(cfg.get("routing")))
    _save_config(cfg)
    _keepwarm_kick.set()
    return {"canUndo": True}


@app.post("/config/setup/undo")
async def setup_undo():
    if not setup_file.restore_backup(CONFIG_FILE, os.path.dirname(CONFIG_FILE)):
        raise HTTPException(status_code=400, detail="There is nothing to undo.")
    _config_reader.forget()
    _keepwarm_kick.set()
    return {"canUndo": False}


@app.get("/routing/status")
async def get_routing_status():
    return _routing_status()


ROUTE_TEST_MESSAGE = "Say hello in one short sentence."


async def _engine_is_loaded(ai: str) -> Optional[bool]:
    """Whether Ollama or LM Studio has this model in memory right now (None: not one of those, or it could not be asked)."""
    try:
        if is_ollama_model(ai) and not ollama_api_key:
            want = strip_ollama_prefix(ai)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(f"{_ollama_api_root(OLLAMA_LOCAL_BASE_URL)}/api/ps") as resp:
                    if resp.status != 200:
                        return None
                    names = {str(m.get("name") or m.get("model") or "") for m in ((await resp.json(content_type=None)) or {}).get("models") or [] if isinstance(m, dict)}
            return want in names or f"{want}:latest" in names
        if is_lmstudio_model(ai):
            models = await _fetch_lmstudio_models(lmstudio_base_url, force=True)
            want = strip_lmstudio_prefix(ai)
            return next((bool(m["loaded"]) for m in models or [] if m["id"] == want), None)
    except Exception:
        return None
    return None


async def _route_test_once(request: Request, job_key: str) -> dict:
    """One short streamed request for a job, sent through its route exactly as SkyrimNet's would be. -> {"ok", "firstWord", "total", "who" (provider and model that answered), "skipped", "error", "note"}."""
    rid = uuid.uuid4().hex[:8]
    _begin_request(request, rid, "chat", (), count=False)
    req = ChatRequest(model="job/" + job_key, messages=[ChatMessage(role="user", content=ROUTE_TEST_MESSAGE)], stream=True, max_tokens=60)
    t0 = time.time()
    first = None
    words = ""
    try:
        result = await _chat_completions_impl(req, request, rid)
        body = getattr(result, "body_iterator", None)
        if body is None:
            words = (((result or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        else:
            async for chunk in body:
                piece = _sse_delta_content(chunk)
                if piece:
                    first = first if first is not None else time.time() - t0
                    words += piece
    except (HTTPException, _ClientGone) as e:
        rec = request_facts.current()
        return {"ok": False, "total": time.time() - t0, "error": str(e.detail) if isinstance(e, HTTPException) else "", "note": _fail_note(e),
                "skipped": list(rec.skipped) if rec is not None else []}
    finally:
        _in_flight.leave(rid)
    rec = request_facts.current()
    who = " ".join(x for x in (getattr(rec, "provider", None), getattr(rec, "model", None)) if x)
    return {"ok": bool(words.strip()), "firstWord": first, "total": time.time() - t0, "who": who, "skipped": list(rec.skipped) if rec is not None else [], "error": "", "note": None}


def _secs_text(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds >= 10 else f"{seconds:.1f} s"


@app.post("/config/routing/test")
async def test_route(request: Request):
    """The Routing page's Test: one short request through a job's route, the way SkyrimNet would send it, and one plain line saying how long it took or why it failed. A first
    request to a model that is not loaded yet (Ollama, LM Studio) counts as its loading: it is timed as that, and a second request then shows the reply time. Waits much longer
    than a game would for a local engine (a cold model needs to load)."""
    try:
        body = await request.json()
    except Exception:
        body = None
    key = str((body or {}).get("job") or "") if isinstance(body, dict) else ""
    settings = _routing_settings()
    group = next((g for g in _hard_groups() if key in g), None) or (key,)          # jobs that can't be told apart share the first route any of them has
    route, key = routing.route_for(settings, group) if key else (None, None)
    if route is None:
        return {"ok": False, "line": "Set a route for this job first.", "seconds": 0}
    cands = _routing_engine.order(key, route, settings, expand=_expand_route_entry)
    first_ai = cands[0].ai if cands else None
    cold = (await _engine_is_loaded(first_ai) is False) if first_ai else False
    token = _wait_budget.set(engine_trouble.TEST_PATIENCE_S)
    try:
        one = await _route_test_once(request, key)
        loaded_in = None
        if one["ok"] and cold and not one["skipped"]:
            loaded_in = one["total"]
            one = await _route_test_once(request, key)
    finally:
        _wait_budget.reset(token)
    return {"ok": one["ok"], "line": _route_test_line(one, loaded_in, first_ai), "seconds": round(one["total"], 1)}


def _route_test_line(one: dict, loaded_in: Optional[float], first_ai: Optional[str]) -> str:
    """The one line the Test button shows (every part is a plain sentence: no path, number from an error or raw error text)."""
    passed = []
    for ai, _why in one.get("skipped") or []:
        clause = _trouble.clause(_engine_key(ai))
        passed.append(f"{_provider_label_for(ai)} {_resolve_model_name(ai)}" + (f": {clause}" if clause else " didn't answer") + ".")
    if one["ok"]:
        line = f"Answered by {one['who']} in {_secs_text(one['total'])}"
        if one.get("firstWord") is not None:
            line += f" (first word after {_secs_text(one['firstWord'])})"
        line += "."
        if loaded_in is not None:
            line = f"{one['who']} loaded in {_secs_text(loaded_in)}. Its next reply took {_secs_text(one['total'])}" + (
                f" (first word after {_secs_text(one['firstWord'])})." if one.get("firstWord") is not None else ".")
        return " ".join([line] + passed)
    if passed:
        return "Nothing answered. " + " ".join(passed)
    clause = _trouble.clause(_engine_key(first_ai)) if first_ai else None
    if clause:
        return f"Nothing answered. {_provider_label_for(first_ai)} {_resolve_model_name(first_ai)}: {clause}."
    if one.get("error"):
        return "Nothing answered: " + str(one["error"])[:200].rstrip(". ") + "."
    return "Nothing answered, and no reason came back."


@app.get("/config/other-pcs")
async def other_pcs_state(refresh: bool = False):
    if refresh:
        await remote_svc.poll_all()
    return {
        "newCodeConfirm": other_pcs.NEW_CODE_CONFIRM,
        "backup": _backup_state(),
        "gamingModels": model_sender.gaming_models(),
        "ollamaCatalog": model_matches.ollama_names(local_models.CATALOG),
        "servers": [{**r.to_json(), "coveredBy": _backup_model() if r.online is False else None,
                     "problem": other_pcs.problem_of(r) if r.online is not False else None,
                     "sends": model_sender.jobs_for(r.id), "update": update_sender.job_json(r.id)} for r in remote_svc.all()],
        "courierUpdate": update_sender.status(),
        "downloadUrl": other_pcs.MODEL_SERVER_DOWNLOAD_URL,
        "linuxCommand": other_pcs.LINUX_INSTALL_COMMAND,
        "thisPc": other_pcs.short_hostname(),
        "mainPort": MAIN_PORT,
        "addAPc": add_a_pc_svc.state(),
    }


model_sender = model_transfer.ModelSender(local_svc, remote_svc, logger)
add_a_pc_svc = add_a_pc.AddAPc(remote_svc, logger, program_dir=os.path.dirname(os.path.abspath(__file__)))


@app.get("/config/other-pcs/add-a-pc")
async def add_a_pc_state():
    return add_a_pc_svc.state()


@app.post("/config/other-pcs/add-a-pc/new")
async def add_a_pc_new(request: Request):
    """Make a one-time address (works for 15 minutes); an older one stops at once. {"ip": ...} picks one of this PC's own home addresses instead of the best one."""
    body = await request.json()
    try:
        return await add_a_pc_svc.new_address(str(body.get("ip", "")) if isinstance(body, dict) else "")
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.post("/config/other-pcs/add-a-pc/answer")
async def add_a_pc_answer(request: Request):
    """The click on "<NAME> wants to join.": {"allow": true} adds the waiting device, anything else turns it away."""
    body = await request.json()
    return add_a_pc_svc.answer(isinstance(body, dict) and body.get("allow") is True)


@app.post("/config/other-pcs/add-a-pc/done")
async def add_a_pc_done(request: Request):
    await request.json()
    return await add_a_pc_svc.done()
update_sender = courier_update_sender.UpdateSender(remote_svc, logger)


@app.get("/config/other-pcs/courier/update")
async def other_pcs_courier_update_state():
    """The carried Courier update file (checked) and, per device, its build, whether it can be updated from here and how its update is going: the Decisions and Voice rows ask this for their Update Courier button."""
    return update_sender.status()


@app.post("/config/other-pcs/courier/update")
async def other_pcs_courier_update(request: Request):
    """Send the carried, signed Courier update to one device. `anyway` is the person's Update anyway on a device that said it was busy."""
    data = await request.json()
    try:
        job = update_sender.start(str(data.get("id", "")), data.get("anyway") is True)
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "started", **job.to_json()}


@app.post("/config/other-pcs/courier/update/all")
async def other_pcs_courier_update_all(request: Request):
    """Update every device that is behind, one after the other."""
    await request.json()
    return {"status": "started", "count": update_sender.start_all()}


@app.post("/config/other-pcs/courier/update/cancel")
async def other_pcs_courier_update_cancel(request: Request):
    data = await request.json()
    try:
        update_sender.cancel(str(data.get("id", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "ok"}


def _backup_state() -> dict:
    c = _load_config().get("other_pcs_backup") or {}
    return {"enabled": bool(c.get("enabled")), "model": str(c.get("model") or ""), "active": _backup_model() is not None}


@app.get("/config/other-pcs/backup-options")
async def other_pcs_backup_options():
    """Every ready model on this device and in the cloud (never another device's) a player can pick as the backup."""
    options = []
    for p in await _dashboard_providers():
        if p["id"] == "remote" or not p.get("testable"):
            continue
        short = "this device" if p["id"] == "local" else re.sub(r" \(.*\)", "", p["name"])
        for row in p.get("models") or []:
            options.append({"id": row[0], "label": f"{row[0]} ({short})"})
    return {"options": options}


@app.post("/config/other-pcs/backup")
async def other_pcs_set_backup(request: Request):
    data = await request.json()
    model = str(data.get("model", "")).strip()
    if model and is_remote_model(model):
        return JSONResponse(status_code=400, content={"detail": "Pick a model on this device or in the cloud, not one on another device."})
    cfg = _load_config()
    cfg["other_pcs_backup"] = {"enabled": bool(data.get("enabled")), "model": model}
    _save_config(cfg)
    logger.info(f"Backup for other devices: {'on, ' + model if cfg['other_pcs_backup']['enabled'] and model else 'off'}")
    return _backup_state()


@app.post("/config/other-pcs/scan")
async def other_pcs_scan():
    """Ask the home network which devices are MultiProxy model servers (a second or two)."""
    found = await asyncio.to_thread(other_pcs.scan_network, 1.5, other_pcs.DISCOVERY_PORT, "")
    have = {r.base for r in remote_svc.all()}
    for f in found:
        f["paired"] = f["address"] in have
    return {"found": found}


@app.post("/config/other-pcs/servers/add-multiproxy")
async def other_pcs_add_multiproxy(request: Request):
    data = await request.json()
    try:
        r = await remote_svc.add_multiproxy(str(data.get("address", "")), str(data.get("code", "")), str(data.get("nickname", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "added", "id": r.id}


@app.post("/config/other-pcs/servers/add-own")
async def other_pcs_add_own(request: Request):
    data = await request.json()
    try:
        r = await remote_svc.add_openai(str(data.get("address", "")), str(data.get("nickname", "")), str(data.get("key", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "added", "id": r.id}


@app.post("/config/other-pcs/courier/action")
async def other_pcs_courier_action(request: Request):
    """Run one of a Courier device's actions from here: load / unload / delete / download / cancel a model, start / restart a program, or uninstall the llama.cpp program (never a model)."""
    data = await request.json()
    action = str(data.get("action", ""))
    if action not in ("load", "unload", "delete", "download", "cancel", "start", "restart", "stop", "uninstall"):
        return JSONResponse(status_code=400, content={"detail": "Unknown action."})
    try:
        await remote_svc.courier_action(str(data.get("id", "")), str(data.get("program", "")), action, str(data.get("model", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "ok"}


@app.get("/config/other-pcs/courier/engine")
async def other_pcs_courier_engine(id: str = ""):
    """The model engine (llama.cpp) on a Courier device: the same facts the Local device tab shows for this one (kind with its label, build, a newer build, faster versions)."""
    try:
        d = await remote_svc.courier_engine(id)
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    d["label"] = (local_models.ENGINE_LABELS.get(d.get("kind") or "") or [""])[0]
    return d


@app.post("/config/other-pcs/courier/engine")
async def other_pcs_courier_engine_post(request: Request):
    """Update the engine on a Courier device, or switch its version: action is update or use (with kind)."""
    data = await request.json()
    action = str(data.get("action", ""))
    if action not in ("update", "use"):
        return JSONResponse(status_code=400, content={"detail": "Unknown action."})
    try:
        await remote_svc.courier_engine_post(str(data.get("id", "")), {"action": action, **({"kind": str(data.get("kind", ""))} if action == "use" else {})})
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "ok"}


@app.post("/config/other-pcs/courier/models/find")
async def other_pcs_courier_find(request: Request):
    """Model files (.gguf) already on a Courier device: in LM Studio's folder, the Courier's own, and one the player pointed at."""
    data = await request.json()
    try:
        return await remote_svc.courier_find_models(str(data.get("id", "")), str(data.get("folder", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.post("/config/other-pcs/courier/models/scan")
async def other_pcs_courier_scan(request: Request):
    """The full scan of a Courier device's drives for model files: action is start, stop or status."""
    data = await request.json()
    action = str(data.get("action", "status"))
    if action not in ("start", "stop", "status"):
        return JSONResponse(status_code=400, content={"detail": "Unknown action."})
    try:
        return await remote_svc.courier_scan(str(data.get("id", "")), action)
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.post("/config/other-pcs/courier/models/add")
async def other_pcs_courier_add_models(request: Request):
    """Use model files where they already sit on a Courier device (nothing is copied)."""
    data = await request.json()
    paths = data.get("paths")
    if not isinstance(paths, list):
        return JSONResponse(status_code=400, content={"detail": "You didn't choose any model files."})
    try:
        return await remote_svc.courier_add_models(str(data.get("id", "")), [str(p) for p in paths])
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.get("/config/other-pcs/courier/drives")
async def other_pcs_courier_drives(id: str = ""):
    """A Courier device's drives with free space, for choosing where its models are saved."""
    try:
        return await remote_svc.courier_drives(id)
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.post("/config/other-pcs/courier/models-folder")
async def other_pcs_courier_models_folder(request: Request):
    """Choose the folder a Courier device saves downloaded models in (that device checks it can really use it)."""
    data = await request.json()
    try:
        return await remote_svc.courier_set_models_folder(str(data.get("id", "")), str(data.get("path", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.post("/config/other-pcs/courier/send")
async def other_pcs_courier_send(request: Request):
    """Copy one of this device's local models to a Courier device (instead of that device downloading it again): "download once"."""
    data = await request.json()
    try:
        job = model_sender.start(str(data.get("id", "")), str(data.get("model", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "started", **job.to_json()}


@app.post("/config/other-pcs/courier/send/cancel")
async def other_pcs_courier_send_cancel(request: Request):
    data = await request.json()
    try:
        model_sender.cancel(str(data.get("id", "")), str(data.get("model", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "ok"}


@app.get("/config/other-pcs/courier/log")
async def other_pcs_courier_log(id: str = ""):
    """A Courier device's recent log (the last ~200 lines, secrets masked on that device), for the Log button."""
    try:
        return {"lines": await remote_svc.courier_log(id)}
    except other_pcs.OtherPcsError as e:
        return _other_error(e)


@app.post("/config/other-pcs/courier/settings")
async def other_pcs_courier_settings(request: Request):
    data = await request.json()
    changes = {k: bool(data[k]) for k in ("keep_awake", "start_with_windows") if k in data}
    try:
        if "private_networks" in data:                 # a VPN list for that device: checked here too, so a bad one never travels
            changes["private_networks"] = other_pcs.clean_private_networks(data["private_networks"])
        await remote_svc.courier_settings(str(data.get("id", "")), **changes)
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    return {"status": "ok"}


@app.post("/config/other-pcs/courier/new-code")
async def other_pcs_courier_new_code(request: Request):
    data = await request.json()
    try:
        await remote_svc.courier_new_code(str(data.get("id", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    except Exception as e:
        logger.warning(f"A new pairing code couldn't be made ({e.__class__.__name__})")
        return _other_error(other_pcs.OtherPcsError(502, "Couldn't reach Courier on that device. Is it running, and allowed through the Windows firewall?"))
    return {"status": "ok"}


@app.post("/config/other-pcs/courier/set-code")
async def other_pcs_courier_set_code(request: Request):
    """Give a saved device the new pairing code it shows now (tried on that device first; never echoed or logged)."""
    data = await request.json()
    try:
        await remote_svc.set_code(str(data.get("id", "")), str(data.get("code", "")))
    except other_pcs.OtherPcsError as e:
        return _other_error(e)
    except Exception as e:
        logger.warning(f"A pairing code couldn't be checked ({e.__class__.__name__})")
        return _other_error(other_pcs.OtherPcsError(502, "Couldn't reach Courier on that device. Is it running, and allowed through the Windows firewall?"))
    return {"status": "ok"}


@app.post("/config/other-pcs/servers/remove")
async def other_pcs_remove(request: Request):
    data = await request.json()
    reached = await remote_svc.unpair_and_remove(str(data.get("id", "")))
    if reached:
        return {"status": "removed"}
    return {"status": "removed", "note": other_pcs.REMOVED_NOT_REACHED}


@app.get("/config/other-pcs/provider")
async def other_pcs_provider_entry():
    return _remote_provider_entry()


@app.get("/config/local-models/provider")
async def local_models_provider_entry():
    """The 'Local' provider pill's own entry, for refreshing it in place without a page reload."""
    return _local_provider_entry()


@app.get("/config/ollama-models")
async def refresh_ollama_models():
    """The dashboard's own ↻ Refresh button -- re-checks Ollama (bypassing the short cache) and
    returns the same shape _dashboard_providers() builds for it, so the page can just replace its
    PROVIDERS entry and re-render in place, no reload."""
    status = await _ollama_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"],
    }


@app.get("/config/lmstudio-models")
async def refresh_lmstudio_models():
    """The LM Studio panel's own Refresh: same shape the Providers card builds for it, no page reload."""
    return {k: v for k, v in (await _lmstudio_status(force=True)).items()}


@app.post("/config/lmstudio-address")
async def set_lmstudio_address(request: Request):
    """Change where LM Studio's server is (an empty address goes back to the default, http://localhost:1234)."""
    global lmstudio_base_url
    data = await request.json()
    text = str((data or {}).get("address", "")).strip()
    cfg = _load_config()
    if not text:
        lmstudio_base_url = LMSTUDIO_DEFAULT_BASE_URL
        cfg.pop("lmstudio_base_url", None)
    else:
        try:
            lmstudio_base_url = normalize_lmstudio_address(text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        cfg["lmstudio_base_url"] = lmstudio_base_url
    _save_config(cfg)
    _lmstudio_cache.clear()
    logger.info(f"LM Studio's address is now {lmstudio_base_url}")
    return await _lmstudio_status(force=True)


# What the player sees on THIS device (the Courier's own sentence says "on that device", which is wrong here).
LMSTUDIO_SETUP_NEEDED = "LM Studio needs a quick setup step. Open Settings in LM Studio, click \"Install CLI\", then try again here."


@app.post("/config/lmstudio-start")
async def start_lmstudio_server():
    """Start LM Studio's server through its own `lms` tool (the Courier's LM Studio adapter)."""
    adapter = _lmstudio_adapter(lmstudio_base_url)
    if not adapter.cli:
        raise HTTPException(status_code=404, detail=LMSTUDIO_SETUP_NEEDED)
    try:
        await adapter.start()
    except courier_adapters.AdapterError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    _lmstudio_cache.clear()
    return await _lmstudio_status(force=True)


@app.get("/config/gemini-models")
async def refresh_gemini_models():
    """The dashboard's own ↻ Refresh button for Gemini -- same shape/purpose as
    refresh_ollama_models() above, re-checking (bypassing the cache) via _gemini_status()."""
    status = await _gemini_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"],
    }


def _register_key_source_routes(src) -> None:
    # POST /config/<id>-key (save or clear) and GET /config/<id>-models (the panel's Refresh), the same two routes every key provider has.
    async def set_key(request: Request):
        data = await request.json()
        key = str(data.get("key", "") or "").strip()
        cfg = _load_config()
        if not key:
            _ks_keys[src.id] = None
            cfg.pop(src.config_key, None)
            _save_config(cfg)
            _ks_models_cache.clear()
            logger.info(f"{src.pill} API key cleared")
            return {"status": "cleared"}
        problem = key_sources.key_problem(src, key)
        if problem:
            return JSONResponse({"detail": problem}, status_code=400)
        _ks_keys[src.id] = key
        cfg[src.config_key] = key
        _save_config(cfg)
        _ks_models_cache.clear()          # a changed key may see a different live list
        logger.info(f"{src.pill} API key configured and saved to config.json")
        return {"status": "saved"}

    async def refresh_models():
        st = await _ks_status(src, force=True)
        return {"state": st["state"], "status": st["status"], "models": st["models"], "testable": st["testable"], "any": st["any"], "empty": st["empty"], "label": st["label"]}

    app.add_api_route(f"/config/{src.id}-key", set_key, methods=["POST"])
    app.add_api_route(f"/config/{src.id}-models", refresh_models, methods=["GET"])


for _src in key_sources.SOURCES:
    _register_key_source_routes(_src)


@app.get("/config/xai-models")
async def refresh_xai_models():
    """The dashboard's own Refresh button for xAI (same shape as refresh_gemini_models)."""
    status = await _xai_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"],
    }


@app.get("/config/continuum-models")
async def refresh_continuum_models():
    """The dashboard's own Refresh button for Continuum (same shape as refresh_xai_models)."""
    status = await _continuum_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"],
    }


@app.get("/config/grok-models")
async def refresh_grok_models():
    """The dashboard's own Refresh button for Grok (same shape as refresh_geminicli_models)."""
    status = await _grok_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"],
    }


@app.get("/config/claude-models")
async def refresh_claude_models():
    """The Claude card's own ↻ Refresh button -- re-asks Anthropic for the live model list (bypassing
    the cache). Returns only the list and its label: the card's state/status pill has its own source."""
    status = await _claude_status(force=True)
    return {"models": status["models"], "label": status["label"]}


@app.get("/config/geminicli-models")
async def refresh_geminicli_models():
    """The dashboard's own ↻ Refresh button for Antigravity CLI -- same shape/purpose as
    refresh_ollama_models()/refresh_gemini_models() above, re-checking (bypassing the cache) via
    _agy_status(). No key route needed alongside this one -- Antigravity is keyless, like Claude
    and Codex."""
    status = await _agy_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"],
    }


@app.get("/config/codex-models")
async def refresh_codex_models():
    """The dashboard's own ↻ Refresh button for Codex -- same shape/purpose as
    refresh_geminicli_models() above, re-checking (bypassing the cache) via _codex_status(). No key
    route needed alongside this one -- Codex is keyless, like Claude and Antigravity."""
    status = await _codex_status(force=True)
    return {
        "state": status["state"], "status": status["status"], "models": status["models"],
        "testable": status["testable"], "any": status["any"], "empty": status["empty"],
        "label": status["label"], "installCard": status.get("installCard"),
    }


@app.get("/config/codex/install")
async def codex_install_state():
    """Where the ChatGPT card's Install is (the card polls this while it runs)."""
    return _codex_installer.card(bool(_codex_path()), codex_install.signed_in_file_exists())


@app.post("/config/codex/install")
async def codex_install_start():
    """Fetch Codex for this PC (codex_install.py). Never touches an existing Codex or its sign-in."""
    if _codex_path():
        raise HTTPException(status_code=409, detail="Codex is already installed on this PC.")
    try:
        _codex_installer.start()
    except codex_install.CodexInstallError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    return _codex_installer.card(False, codex_install.signed_in_file_exists())


@app.post("/config/codex/install/cancel")
async def codex_install_cancel():
    _codex_installer.cancel()
    return _codex_installer.card(bool(_codex_path()), codex_install.signed_in_file_exists())


_CODEX_PRESET_DEFAULT_NAMES = {"openai": "OpenAI", "ollama": "Local Ollama", "lmstudio": "LM Studio"}


def _codex_provider_public(p: dict) -> dict:
    """Never echo a raw saved key back to the browser -- same hygiene the rest of this dashboard
    already has for keyed providers (they only ever report hasKey/state, never the key itself)."""
    return {k: v for k, v in p.items() if k != "api_key"} | {"hasKey": bool(p.get("api_key"))}


@app.get("/config/codex-providers")
async def list_codex_providers():
    """List every SAVED Codex provider (the implicit default entry is never stored here -- see
    _codex_resolve_provider)."""
    return {"providers": [_codex_provider_public(p) for p in codex_providers]}


@app.post("/config/codex-providers")
async def add_codex_provider(request: Request):
    """Add a new Codex provider -- either a built-in preset (OpenAI/Ollama/LM Studio) or a custom
    OpenAI-compatible endpoint. The default ChatGPT entry is implicit and can't be created here."""
    data = await request.json()
    provider_type = (data.get("type") or "").strip()
    if provider_type not in ("builtin", "custom"):
        raise HTTPException(status_code=400, detail="Please pick a built-in or custom service.")

    entry: dict = {"type": provider_type}
    if provider_type == "builtin":
        preset = (data.get("preset") or "").strip()
        if preset not in _CODEX_PRESET_DEFAULT_NAMES:
            raise HTTPException(status_code=400, detail="Pick OpenAI, Ollama, or LM Studio.")
        entry["preset"] = preset
        entry["base_url"] = _codex_preset_base_url(preset)
        entry["name"] = (data.get("name") or "").strip() or _CODEX_PRESET_DEFAULT_NAMES[preset]
        # Ollama/LM Studio are local and keyless (see the mockup's own read-only base-URL field
        # with no key row) -- only the OpenAI preset actually saves a key.
        if preset == "openai":
            key = (data.get("api_key") or "").strip()
            if key:
                entry["api_key"] = key
    else:
        name = (data.get("name") or "").strip()
        base_url = (data.get("base_url") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Please type a name.")
        if not base_url:
            raise HTTPException(status_code=400, detail="Please type an address.")
        entry["name"] = name
        entry["base_url"] = base_url
        key = (data.get("api_key") or "").strip()
        if key:
            entry["api_key"] = key

    entry["id"] = _codex_provider_slug(entry["name"], {p["id"] for p in codex_providers})

    cfg = _load_config()
    codex_providers.append(entry)
    cfg["codex_providers"] = codex_providers
    _save_config(cfg)
    logger.info(f"Codex provider added: {entry['id']} ({entry['name']}, {provider_type})")
    return _codex_provider_public(entry)


@app.post("/config/codex-providers/{provider_id}")
async def edit_codex_provider(provider_id: str, request: Request):
    """Edit a saved Codex provider's name/base_url/key. Type and preset are fixed at creation --
    remove and re-add to change those. The default entry isn't stored here, so it can't be edited
    even if someone tries."""
    if provider_id == "default":
        raise HTTPException(status_code=400, detail="You cannot edit the default ChatGPT provider.")
    entry = _codex_find_provider(provider_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Couldn't find provider '{provider_id}'.")

    data = await request.json()
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Please type a name.")
        entry["name"] = name
    if "base_url" in data and entry["type"] == "custom":
        base_url = (data["base_url"] or "").strip()
        if not base_url:
            raise HTTPException(status_code=400, detail="Please type an address.")
        entry["base_url"] = base_url
    if "api_key" in data:
        key = (data["api_key"] or "").strip()
        if key:
            entry["api_key"] = key
        else:
            entry.pop("api_key", None)

    cfg = _load_config()
    cfg["codex_providers"] = codex_providers
    _save_config(cfg)
    logger.info(f"Codex provider edited: {entry['id']}")
    return _codex_provider_public(entry)


@app.post("/config/codex-providers/{provider_id}/remove")
async def remove_codex_provider(provider_id: str):
    """Remove a saved Codex provider. The default entry isn't stored here, so it can't be removed
    even if someone tries."""
    if provider_id == "default":
        raise HTTPException(status_code=400, detail="You cannot remove the default ChatGPT provider.")
    entry = _codex_find_provider(provider_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Couldn't find provider '{provider_id}'.")

    codex_providers.remove(entry)
    cfg = _load_config()
    cfg["codex_providers"] = codex_providers
    _save_config(cfg)
    logger.info(f"Codex provider removed: {provider_id}")
    return {"status": "removed"}


@app.get("/config/codex-provider-panel")
async def codex_provider_panel():
    """Re-fetches the exact `codexProviders` array _dashboard_providers() embeds in the Codex
    entry -- the dashboard's own add/edit/remove flow calls this to refresh the inner picker and
    management list in place, without recomputing this server-side help/models/any logic in JS."""
    status = await _codex_status()
    return _codex_provider_dashboard_entries(status)


def _codex_pin_target_valid(provider: str) -> bool:
    return provider == "default" or _codex_find_provider(provider) is not None


@app.post("/config/codex-pin-model")
async def pin_codex_model(request: Request):
    """Pin an unlisted-but-working model id onto a provider's own Models list -- confirmed live
    2026-09-16 that Codex's own real, working model set is bigger than any provider's own list can
    show (a newly-rolled-out or legacy model, e.g. gpt-6-astra, works via -m even when the
    account's own models_cache.json never listed it; an added provider has no listing API at all).
    No validation the id is real -- a bad one just fails the same way a bad typed-in model id
    already does today, at request time."""
    data = await request.json()
    provider = (data.get("provider") or "").strip()
    model_id = (data.get("model_id") or "").strip()
    name = (data.get("name") or "").strip()
    if not _codex_pin_target_valid(provider):
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'.")
    if not model_id:
        raise HTTPException(status_code=400, detail="Please type a model name.")

    pins = codex_pinned_models.setdefault(provider, [])
    existing = next((p for p in pins if p["id"] == model_id), None)
    if existing is not None:
        if name:
            existing["name"] = name  # re-pinning the same id just updates its display name, no duplicate row
    else:
        pins.append({"id": model_id, "name": name} if name else {"id": model_id})

    cfg = _load_config()
    cfg["codex_pinned_models"] = codex_pinned_models
    _save_config(cfg)
    logger.info(f"Codex model pinned: {model_id} (provider={provider})")
    return {"status": "pinned"}


@app.post("/config/codex-unpin-model")
async def unpin_codex_model(request: Request):
    data = await request.json()
    provider = (data.get("provider") or "").strip()
    model_id = (data.get("model_id") or "").strip()

    pins = codex_pinned_models.get(provider, [])
    match = next((p for p in pins if p["id"] == model_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"The model '{model_id}' is not set up for '{provider}'.")
    pins.remove(match)
    if not pins:
        codex_pinned_models.pop(provider, None)

    cfg = _load_config()
    cfg["codex_pinned_models"] = codex_pinned_models
    _save_config(cfg)
    logger.info(f"Codex model unpinned: {model_id} (provider={provider})")
    return {"status": "unpinned"}


# --- Speeds tab endpoints ---

# Longer-range /stats/speeds queries (2026-09-20) -- everything except "today" is answered from
# speed_history.db instead of the in-memory _SpeedTracker. "today" is deliberately NOT one of
# these windows: it keeps using speed_tracker.snapshot() completely unchanged (see stats_speeds
# below), so its response shape is byte-for-byte identical to before this feature existed.
SPEED_RANGE_WINDOW_SECONDS = {"today": 86400.0, "7d": 7 * 86400.0, "30d": 30 * 86400.0}  # "all" has no window (None)
# "today" here is a rolling 24h window (not a calendar-midnight boundary) -- only reachable via
# the by-role endpoints below, since stats_speeds' own "today" branch short-circuits through
# speed_tracker.snapshot() before ever consulting this dict (see that branch's own comment).
SPEED_WEEKLY_BUCKET_CUTOFF_DAYS = 60  # beyond this much history, bucket by week instead of by day


def _distinct_speed_history_models(since_ts: Optional[float]) -> list:
    """Blocking -- always called through asyncio.to_thread. Every model with at least one row in
    the window, regardless of role-chip linkage (same "only show what's actually been seen" rule
    speed_tracker.snapshot() already follows for Today)."""
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        if since_ts is None:
            cur = conn.execute("SELECT DISTINCT model FROM speed_history")
        else:
            cur = conn.execute("SELECT DISTINCT model FROM speed_history WHERE ts >= ?", (since_ts,))
        return [row[0] for row in cur.fetchall()]


def _query_speed_history_rows(model: str, since_ts: Optional[float]) -> list:
    """Blocking -- always called through asyncio.to_thread. (ts, elapsed, success) rows for one
    model, oldest first, since_ts=None meaning no lower bound (the "all" range)."""
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        if since_ts is None:
            cur = conn.execute("SELECT ts, elapsed, success FROM speed_history WHERE model = ? ORDER BY ts", (model,))
        else:
            cur = conn.execute(
                "SELECT ts, elapsed, success FROM speed_history WHERE model = ? AND ts >= ? ORDER BY ts",
                (model, since_ts),
            )
        return cur.fetchall()


def _aggregate_speed_history(rows: list) -> Optional[dict]:
    """rows -> {last_reply, avg, success_pct, calls_total, recent, bucket_unit}, or None for an
    empty window (caller skips the model entirely, same as speed_tracker never surfacing a model
    it has zero calls for).

    "last_reply" is the most recent row's own elapsed -- since every range's window always
    includes "now", the most recent row within the window IS the model's overall most recent
    call, satisfying the spec's "regardless of range" requirement without a second, unwindowed
    query. The sparkline buckets by day, except once this window's own span exceeds
    SPEED_WEEKLY_BUCKET_CUTOFF_DAYS (in practice only reachable via "all", since 7d/30d can never
    span that far), where it buckets by week instead so the bar count stays readable."""
    if not rows:
        return None
    elapsed_values = [r[1] for r in rows]
    success_count = sum(1 for r in rows if r[2])
    span_days = (rows[-1][0] - rows[0][0]) / 86400.0
    bucket_unit = "week" if span_days > SPEED_WEEKLY_BUCKET_CUTOFF_DAYS else "day"
    bucket_seconds = 7 * 86400.0 if bucket_unit == "week" else 86400.0
    buckets: dict = {}
    for ts, elapsed, _success in rows:
        buckets.setdefault(int(ts // bucket_seconds), []).append(elapsed)
    bucketed_series = [sum(vals) / len(vals) for _key, vals in sorted(buckets.items())]
    return {
        "last_reply": elapsed_values[-1],
        "avg": sum(elapsed_values) / len(elapsed_values),
        "success_pct": round(100 * success_count / len(rows)),
        "calls_total": len(rows),
        "recent": bucketed_series,
        "bucket_unit": bucket_unit,
    }


async def _speed_history_models(range_: str, role_chips: dict) -> list:
    """The 7d/30d/all branch of stats_speeds -- queries speed_history.db instead of the in-memory
    tracker. Runs the (blocking) sqlite reads via asyncio.to_thread, same reasoning as the write
    side: never block the event loop for other concurrent requests."""
    since_ts = None if range_ == "all" else time.time() - SPEED_RANGE_WINDOW_SECONDS[range_]
    model_names = await asyncio.to_thread(_distinct_speed_history_models, since_ts)
    models = []
    for model in model_names:
        rows = await asyncio.to_thread(_query_speed_history_rows, model, since_ts)
        agg = _aggregate_speed_history(rows)
        if agg is None:
            continue
        models.append({"model": model, "roles": role_chips.get(model, []), **agg})
    return models


# --- Speeds v2 (2026-09-27): health at a glance, "starts talking" time, reliability labels ---
# Everything below is computed from speed_history.db rows (never invented): a number is only shown when
# the proxy really measured it. Row tuples are (ts, elapsed, success, first_token, out_tokens, out_chars,
# fail_reason, provider, reply_repair) -- see _V2_ROW_COLUMNS. reply_repair (queue:
# catch-broken-replies, 2026-09-29) is appended last so every existing fixed-index r[N] read below
# keeps working unchanged.
_V2_ROW_COLUMNS = "ts, elapsed, success, first_token, out_tokens, out_chars, fail_reason, provider, reply_repair"
SPEED_STRIP_LEN = 40                # "Last 40 replies"
SPEED_MIN_GENERATION_SECONDS = 0.25  # a reply that finished faster than this after starting gives no honest speed
CHARS_PER_TOKEN = 4.0               # only used to ESTIMATE tokens from reply length (shown with "≈")
WORDS_PER_TOKEN = 0.75
RELIABLE_PCT = 98
SOME_ERRORS_PCT = 90
# Health cards
HEALTH_WINDOW_SECONDS = 3600.0
HEALTH_RECENT_CALLS = 20
HEALTH_ERROR_RATE = 0.10
HEALTH_SLOW_FACTOR = 2.0            # "well above usual": at least this many times the provider's own usual...
HEALTH_SLOW_MIN_EXTRA_SECONDS = 0.5  # ...and at least this much slower in real seconds
HEALTH_BASELINE_MIN_SAMPLES = 5     # need this many earlier replies to say what "usual" is

_REASON_PHRASES = {  # (count -> text) for the failure line under a model card
    "busy": lambda n: f'{n} "too busy"',
    "timeout": lambda n: f"{n} took too long",
    GAVE_UP: lambda n: f"{n} gave up waiting",
    EMPTY: lambda n: f"{n} answered with nothing",
    "server": lambda n: f"{n} server error{'s' if n != 1 else ''}",
    "auth": lambda n: f"{n} login problem{'s' if n != 1 else ''}",
    "broken reply": lambda n: f"{n} broken repl{'y' if n == 1 else 'ies'}",
    "other": lambda n: f"{n} other",
}
_HEALTH_REASON_PHRASES = {  # the same, phrased for "3 were ..." on a health card
    "busy": lambda n: f'{n} {"was" if n == 1 else "were"} "too busy"',
    "timeout": lambda n: f"{n} took too long",
    GAVE_UP: lambda n: f"{n} {'was' if n == 1 else 'were'} given up on",
    EMPTY: lambda n: f"{n} {'was' if n == 1 else 'were'} empty",
    "server": lambda n: f"{n} {'was a server error' if n == 1 else 'were server errors'}",
    "auth": lambda n: f"{n} {'was a login problem' if n == 1 else 'were login problems'}",
    "broken reply": lambda n: f"{n} {'was a broken reply' if n == 1 else 'were broken replies'}",
}


def _median(values: list) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _reliability_level(pct: Optional[int]) -> Optional[str]:
    """Reliable (ok) at 98% or better, Some errors (warn) at 90% or better, Unreliable (bad) below."""
    if pct is None:
        return None
    return "ok" if pct >= RELIABLE_PCT else "warn" if pct >= SOME_ERRORS_PCT else "bad"


def _row_speed(row: tuple) -> Optional[tuple]:
    """(words per second, tokens per second, estimated?) for one saved call, or None when it can't be
    told honestly: only a successful STREAMED reply has both a start and an end to measure between, and
    a reply that ended right after it began (one burst) has no speed to speak of. Real token counts
    are used when the service reported them, otherwise tokens are estimated from the reply's length."""
    _ts, elapsed, success, first_token, out_tokens, out_chars, _reason, _provider, _repair = row
    if not success or first_token is None:
        return None
    generating = elapsed - first_token
    if generating < SPEED_MIN_GENERATION_SECONDS:
        return None
    if out_tokens:
        tokens, estimated = float(out_tokens), False
    elif out_chars:
        tokens, estimated = out_chars / CHARS_PER_TOKEN, True
    else:
        return None
    tps = tokens / generating
    return tps * WORDS_PER_TOKEN, tps, estimated


def _failure_line(rows: list) -> Optional[str]:
    """'3 failed: 2 "too busy", 1 took too long' for the given calls, or None when nothing failed or
    no failure has a saved reason (older history just says "failed" and the card shows no line)."""
    failed = [r for r in rows if not r[2]]
    reasons: dict = {}
    for r in failed:
        if r[6]:
            reasons[r[6]] = reasons.get(r[6], 0) + 1
    if not failed or not reasons:
        return None
    order = ("busy", "timeout", GAVE_UP, EMPTY, "server", "auth", "broken reply", "other")
    parts = [_REASON_PHRASES.get(k, _REASON_PHRASES["other"])(reasons[k]) for k in order if k in reasons]
    unrecorded = len(failed) - sum(reasons.values())
    if unrecorded:
        parts.append(f"{unrecorded} with no reason saved")
    return f"{len(failed)} failed: " + ", ".join(parts)


def _repair_line(rows: list) -> Optional[str]:
    """'3 broken replies fixed · 1 asked again · 1 still broken' (queue: catch-broken-replies,
    2026-09-29) -- over the WHOLE window this card is showing (not just the recent strip
    _failure_line uses), since this is meant to answer "does this ever happen for this model",
    not "what just happened". None when nothing to report, same as _failure_line.

    "still broken" is the exact same fail_reason="broken reply" failure _failure_line already
    counts -- shown again here so the whole repair story (fixed/asked again/still broken) reads as
    one line, without inventing a second bucket of truth for the same failed calls."""
    repaired = sum(1 for r in rows if r[8] == "repaired")
    asked_again = sum(1 for r in rows if r[8] == "asked_again")
    still_broken = sum(1 for r in rows if not r[2] and r[6] == "broken reply")
    parts = []
    if repaired:
        parts.append(f"{repaired} broken repl{'y' if repaired == 1 else 'ies'} fixed")
    if asked_again:
        parts.append(f"{asked_again} asked again")
    if still_broken:
        parts.append(f"{still_broken} still broken")
    return " · ".join(parts) if parts else None


def _measured_notes(rows: list, ok_rows: list) -> dict:
    """Why a "Starts talking" / "Talks at" figure is missing, so the card never claims more than the proxy knows.

    A call saved by Speeds v2 or later always names its service (`provider`); an older call never does. So
    "no start time" means two different things:
      - on a NEW-format successful call it's real: the reply came in one piece (Codex always, or a
        non-streamed reply) -> `whole_reply`;
      - on OLD rows it just wasn't measured yet -> `not_measured` (the numbers fill in as you play);
      - nothing succeeded -> `no_data`.
    `first_token_note` is None when a start time exists at all; `speed_note` is `not_measured` when no
    new-format success exists to work a speed out from, else None (a plain "—")."""
    new_ok = [r for r in ok_rows if r[7]]
    if any(r[3] is not None for r in ok_rows):
        first_note = None
    elif not ok_rows:
        first_note = "no_data"
    elif new_ok:
        first_note = "whole_reply"
    else:
        first_note = "not_measured"
    return {"first_token_note": first_note, "speed_note": None if new_ok else "not_measured"}


def _v2_model_stats(rows: list) -> dict:
    """Everything a By-model card needs beyond today's numbers, from one model's saved calls (any order)."""
    rows = sorted(rows, key=lambda r: r[0])
    ok_rows = [r for r in rows if r[2]]
    success_pct = round(100 * len(ok_rows) / len(rows)) if rows else None
    elapsed_ok = [r[1] for r in ok_rows]
    speeds = [sp for sp in (_row_speed(r) for r in rows) if sp]
    last40 = rows[-SPEED_STRIP_LEN:]
    providers = [r[7] for r in rows if r[7]]
    return {
        "calls": len(rows),
        "provider": providers[-1] if providers else None,
        "reliability": {"pct": success_pct, "level": _reliability_level(success_pct)},
        "first_token": _median([r[3] for r in ok_rows]),
        "first_token_samples": sum(1 for r in ok_rows if r[3] is not None),
        **_measured_notes(rows, ok_rows),
        "full": {
            "fastest": min(elapsed_ok) if elapsed_ok else None,
            "typical": _median(elapsed_ok),
            "slowest": max(elapsed_ok) if elapsed_ok else None,
        },
        "speed": {
            "words": _median([s[0] for s in speeds]),
            "tokens": _median([s[1] for s in speeds]),
            "estimated": any(s[2] for s in speeds),
        } if speeds else None,
        "strip": [{"e": r[1], "ok": bool(r[2])} for r in last40],
        "failure_line": _failure_line(last40),
        "repair_line": _repair_line(rows),
    }


def _query_v2_rows(since_ts: Optional[float]) -> dict:
    """Blocking -- always called through asyncio.to_thread. {(provider or None, model): [row tuples]} in the window (a card is one provider's model)."""
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        if since_ts is None:
            cur = conn.execute(f"SELECT model, {_V2_ROW_COLUMNS} FROM speed_history ORDER BY ts")
        else:
            cur = conn.execute(f"SELECT model, {_V2_ROW_COLUMNS} FROM speed_history WHERE ts >= ? ORDER BY ts", (since_ts,))
        out: dict = {}
        for model, *row in cur.fetchall():
            out.setdefault((row[7] or None, model), []).append(tuple(row))
        return out


def _range_since(range_: str) -> Optional[float]:
    return None if range_ == "all" else time.time() - SPEED_RANGE_WINDOW_SECONDS[range_]


# --- Cheap, shared Speeds reads (2026-10-02, speed_store.py / speed_queries.py) ---
# Every Speeds read runs in a worker thread, mostly inside SQLite, and its answer is shared: a poll that arrives while the same read runs waits for that run, and a
# recent answer is handed back instead of reading again (until a new row is saved, or a few seconds pass), so two open tabs cost one read.
_speed_cache = speed_store.AnswerCache(ttl_s=3.0, still_s=20.0)
_AGG_KEYS = ("last_reply", "avg", "success_pct", "calls_total", "recent", "bucket_unit")


def _models_summary_legacy(since_ts: Optional[float]) -> list:
    """The same answer as speed_queries.models_summary, the slow way (every row into memory, then _aggregate_speed_history and _v2_model_stats). Kept as the
    reference the tests hold the fast read equal to."""
    out = []
    for (provider, model), rows in sorted(_query_v2_rows(since_ts).items(), key=lambda kv: (kv[0][1], kv[0][0] or "")):
        agg = _aggregate_speed_history([(r[0], r[1], r[2]) for r in rows])
        if agg is not None:
            out.append({"model": model, "provider": provider, "key": speed_queries.card_key(provider, model), **agg, "v2": _v2_model_stats(rows)})
    return out


def _models_summary(db: str, since_ts: Optional[float]) -> list:
    return speed_queries.models_summary(db, since_ts, _reliability_level)


def _polled_json(request: Request, payload) -> Response:
    """A polled answer as JSON with an ETag: a page that already has exactly this body gets a bodiless 304."""
    status, body, headers = speed_store.answer(request.headers.get("if-none-match"), payload)
    return Response(content=body, status_code=status, media_type="application/json", headers=headers)


# ---- Where each answer ran (2026-10-01): Cloud, This device, or another device's nickname ----

# Names the Speeds tab has stored as the `provider` of a cloud/subscription service, ever. A provider name
# that is NOT one of these (and isn't "Local") is a device the player once added and has since removed.
_CLOUD_PROVIDER_NAMES = {name.lower() for name in _PROVIDER_SHORT.values() if name not in ("Local", "LM Studio")} | {"typesafe", "openrouter"}


def _speed_where(model: Optional[str], provider: Optional[str]) -> str:
    """"This device", another device's nickname, or "Cloud" for one model's calls.

    Local models are saved under their bare name (the `local/` prefix is stripped before the call), so
    that's recognised by the provider "Local" (or by a `local/` prefix, in case it ever appears); another
    Device's models keep their full `<nickname>/<model>` name. Everything else, including every old row from
    before any of this existed, is Cloud."""
    if provider in ("Local", "LM Studio") or (model or "").lower().startswith(LOCAL_PREFIX):      # (LM Studio runs on this device)
        return "This device"
    match = remote_svc.match(model or "")
    if match:
        return match[0].id
    if provider and provider.lower() not in _CLOUD_PROVIDER_NAMES:
        return provider                      # a device that was removed since: its history keeps its name
    return "Cloud"


def _pc_shown(where):
    """A device's name as the player typed it or the device calls itself (MSI) for a Speeds label; history is saved under the lower-case id (msi), so only the label shown changes."""
    r = remote_svc.get(where) if where else None
    return r.label if r else where


def _where_matches(where: str, pc: str) -> bool:
    """Does a row labelled `where` belong under the Speeds filter value `pc` (all / this / cloud / a nickname)?"""
    pc = (pc or "all").lower()
    if pc == "all":
        return True
    if pc == "this":
        return where == "This device"
    if pc == "cloud":
        return where == "Cloud"
    return where.lower() == pc


@app.get("/stats/speeds/pcs")
async def stats_speeds_pcs():
    """The Speeds tab's filter choices. `show` is False when there's nothing but cloud services (no local
    models and no other devices), so the dashboard hides the filter."""
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    remotes = remote_svc.all()
    options = [{"id": "all", "label": "All devices and services"}, {"id": "this", "label": "This device"}]
    options += [{"id": r.id, "label": r.label} for r in remotes]
    options.append({"id": "cloud", "label": "Cloud services"})
    lmstudio_models = await _lmstudio_status()
    return {"show": bool(local_svc.any_installed() or remotes or lmstudio_models["models"]), "options": options}


# ---- Health cards ----

def _query_health_rows(now: float) -> tuple:
    """Blocking. (rows in the last hour that name a provider, {provider: [first-token times from
    before that hour]} for working out what is "usual" for each service)."""
    since = now - HEALTH_WINDOW_SECONDS
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        cur = conn.execute(
            f"SELECT {_V2_ROW_COLUMNS} FROM speed_history WHERE provider IS NOT NULL AND ts >= ? ORDER BY ts", (since,),
        )
        recent = [tuple(r) for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT provider, first_token FROM speed_history WHERE provider IS NOT NULL AND success = 1 "
            "AND first_token IS NOT NULL AND ts < ? ORDER BY ts DESC LIMIT 5000", (since,),
        )
        baseline: dict = {}
        for provider, first_token in cur.fetchall():
            baseline.setdefault(provider, []).append(first_token)
    return recent, baseline


def _health_card(provider: str, rows: list, baseline: list) -> dict:
    """One provider's verdict from its calls in the last hour. `rows` are that hour's calls (any
    order); `baseline` its first-token times from earlier. Errors beat slow beats smooth."""
    rows = sorted(rows, key=lambda r: r[0])
    if not rows:
        return {"provider": provider, "verdict": "none", "level": "none", "title": "No replies yet",
                "why": "Nothing from this service in the last hour", "calls": 0}
    recent = rows[-HEALTH_RECENT_CALLS:]
    failed = [r for r in recent if not r[2]]
    n = len(recent)
    card = {"provider": provider, "calls": len(rows)}
    if failed and len(failed) / n > HEALTH_ERROR_RATE:
        reasons: dict = {}
        for r in failed:
            if r[6]:
                reasons[r[6]] = reasons.get(r[6], 0) + 1
        why = f"{len(failed)} of the last {n} failed"
        if reasons:
            top = max(reasons, key=lambda k: reasons[k])
            phrase = _HEALTH_REASON_PHRASES.get(top)
            if phrase:
                why += " · " + phrase(reasons[top])
        return {**card, "verdict": "errors", "level": "bad", "title": "Hitting errors", "why": why}
    recent_ft = _median([r[3] for r in recent if r[2] and r[3] is not None])
    usual = _median(baseline) if len(baseline) >= HEALTH_BASELINE_MIN_SAMPLES else None
    if (recent_ft is not None and usual is not None
            and recent_ft > usual * HEALTH_SLOW_FACTOR and recent_ft - usual > HEALTH_SLOW_MIN_EXTRA_SECONDS):
        return {**card, "verdict": "slow", "level": "warn", "title": "Slow to start talking",
                "why": f"Replies starting in {recent_ft:.1f}s, usually {usual:.1f}s"}
    replies = f"{len(rows)} repl{'y' if len(rows) == 1 else 'ies'}"
    bad = sum(1 for r in rows if not r[2])
    return {**card, "verdict": "smooth", "level": "ok", "title": "Running smooth",
            "why": f"{replies}, {'none failed' if not bad else f'{bad} failed'}"}


_SILENT_PHRASES = {  # (count -> text) for "0 replies, 6 gave up waiting" on a card whose AI was asked and never answered
    GAVE_UP: lambda n: f"{n} gave up waiting",
    EMPTY: lambda n: f"{n} answered with nothing",
    "timeout": lambda n: f"{n} timed out",
    "server": lambda n: f"{n} got an error",
    "busy": lambda n: f'{n} "too busy"',
    "auth": lambda n: f"{n} login problem{'s' if n != 1 else ''}",
    "broken reply": lambda n: f"{n} broken repl{'y' if n == 1 else 'ies'}",
    "other": lambda n: f"{n} failed",
}


def _silent_text(failures: list) -> str:
    """'0 replies, 6 gave up waiting' for models that were asked and never answered (reasons added together, most common first)."""
    reasons: dict = {}
    for f in failures:
        for k, n in f["reasons"].items():
            reasons[k] = reasons.get(k, 0) + n
    parts = [_SILENT_PHRASES.get(k, _SILENT_PHRASES["other"])(reasons[k]) for k in sorted(reasons, key=lambda k: -reasons[k])]
    return "0 replies, " + ", ".join(parts)


def _wait_text(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds >= 10 else f"{seconds:.1f} s"


def _silent_models(card: dict, fails: list, route_sizes: dict) -> None:
    """Add to a service's card the models of it that were asked in the last hour and never answered. When none of the service's requests worked, the card says so
    ("Not answering"), coloured by what covers it: amber when the job's route has another AI to try, red when nothing does. A service that also has answers keeps its verdict
    and gets one detail line per silent model."""
    mine = [f for f in fails if f["provider"] == card["provider"]]
    silent = [f for f in mine if not f["ok"]]
    if not silent:
        return

    def job_text(f: dict) -> str:
        job = routing.label(f["job"]) if f["job"] else None
        return " · ".join(x for x in (job, f"longest wait {_wait_text(f['longest'])}") if x)

    def why_text(f: dict) -> Optional[str]:
        """What the local engine itself said was wrong ("Ollama gemma4:26b: couldn't load (ran out of memory while starting)"): what MultiProxy knows now, else what was saved."""
        note = _trouble.clause(speed_queries.card_key(f["provider"], f["model"])) or f.get("note")
        return f"{f['provider']} {f['model']}: {note}" if note else None

    covered = all(route_sizes.get(f["job"], 0) > 1 for f in silent)
    if all(not f["ok"] for f in mine) and sum(f["failed"] for f in mine) >= card.get("calls", 0):
        card.update({"verdict": "silent", "level": "warn" if covered else "bad", "title": "Not answering", "why": _silent_text(silent), "covered": covered})
        if len(silent) == 1:
            card["model"] = silent[0]["model"]
            card["detail"] = [x for x in (why_text(silent[0]), job_text(silent[0])) if x]
            return
    card["detail"] = [f"{why_text(f)} · {_silent_text([f])} · {job_text(f)}" if why_text(f) else f"{f['model']}: {_silent_text([f])} · {job_text(f)}" for f in silent]


def _health_cards(recent_rows: list, baseline: dict, ready_providers: Optional[list], fails: Optional[list] = None, route_sizes: Optional[dict] = None) -> list:
    """Cards for every service with calls in the last hour -- plus, when `ready_providers` is given
    (the "Every ready service" view), every ready service that hasn't been used, as "No replies yet"."""
    by_provider: dict = {}
    for r in recent_rows:
        by_provider.setdefault(r[7], []).append(r)
    names = list(by_provider)
    for name in ready_providers or []:
        if name not in by_provider:
            names.append(name)
    cards = [_health_card(name, by_provider.get(name, []), baseline.get(name, [])) for name in names]
    for c in cards:
        _silent_models(c, fails or [], route_sizes or {})
    rank = {"bad": 0, "warn": 1, "ok": 2, "none": 3}
    return sorted(cards, key=lambda c: (rank[c["level"]], c["provider"].lower()))


@app.get("/stats/speeds/health")
async def stats_speeds_health(request: Request, show: str = "hour", pc: str = "all"):
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    if show not in ("hour", "ready"):
        raise HTTPException(status_code=400, detail="show must be one of: hour, ready")
    recent, baseline = await _speed_cache.get(("health", SPEED_HISTORY_DB), speed_queries.health_rows, SPEED_HISTORY_DB, time.time(), HEALTH_WINDOW_SECONDS, _V2_ROW_COLUMNS)
    fails = await _speed_cache.get(("hour-failures", SPEED_HISTORY_DB), speed_queries.hour_failures, SPEED_HISTORY_DB, time.time(), HEALTH_WINDOW_SECONDS)
    route_sizes = {k: len(r.get("ais") or []) for k, r in (_routing_settings().get("routes") or {}).items()}
    ready = None
    if show == "ready":
        by_id = {p["id"]: p for p in await _dashboard_providers()}
        ready = [_PROVIDER_SHORT[pid] for pid, p in by_id.items() if p.get("state") == "ok" and pid in _PROVIDER_SHORT]
        ready += [r.id for r in remote_svc.all() if r.online]       # other devices that are up
    cards = _health_cards(recent, baseline, ready, fails, route_sizes)
    for c in cards:
        c["where"] = _speed_where(None, c["provider"])
    shown_cards = [c for c in cards if _where_matches(c["where"], pc)]
    for c in shown_cards:
        c["where"], c["provider"] = _pc_shown(c["where"]), _pc_shown(c["provider"])
    return _polled_json(request, {"show": show, "pc": pc, "cards": shown_cards})


# ---- By role, ranked by the number that matters for that job ----

def _rank_by_role(role: str, models: list) -> list:
    """Dialogue is ranked by how fast it STARTS talking (the wait a player feels); every other job by
    the full reply time. A missing number sorts last; ties fall back to the full reply time."""
    def full_time(m):  # the by-role "Full reply" column is the typical successful reply, same as the cards
        return m["full_typical"] if "full_typical" in m else m.get("avg")
    def sort_key(m):
        primary = m.get("first_token") if role == "Dialogue" else full_time(m)
        return (primary is None, primary if primary is not None else 0.0, full_time(m) or 0.0)
    return sorted(models, key=sort_key)


def _query_v2_rows_by_role(role: str, since_ts: Optional[float]) -> dict:
    """Blocking. {(provider, model): [row tuples]} for every call that carried this exact role label."""
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        sql = f"SELECT model, roles_json, {_V2_ROW_COLUMNS} FROM speed_history WHERE roles_json IS NOT NULL"
        cur = conn.execute(sql + " ORDER BY ts") if since_ts is None else conn.execute(sql + " AND ts >= ? ORDER BY ts", (since_ts,))
        out: dict = {}
        for model, roles_json, *row in cur.fetchall():
            try:
                if role in _roles_of(roles_json):
                    out.setdefault((row[7] or None, model), []).append(tuple(row))
            except (TypeError, ValueError):
                continue
        return out


@app.get("/stats/speeds")
async def stats_speeds(request: Request, range: str = "today", pc: str = "all"):
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    if range not in ("today", "7d", "30d", "all"):
        raise HTTPException(status_code=400, detail="range must be one of: today, 7d, 30d, all")

    role_chips = _get_role_chips_cached()

    # "today" is untouched, byte-for-byte, from before this feature existed -- see
    # SPEED_RANGE_WINDOW_SECONDS's own comment above for why it's not just another window.
    # Speeds v2: the extra per-model numbers (starts talking, speed, reliability, strip...) always come
    # from the saved history for the same window, so they survive a restart and agree with the range.
    # One shared read (speed_queries.models_summary) gives both those and the 7d/30d/all cards' own numbers.
    since = _range_since(range)
    summaries = await _speed_cache.get(("models", SPEED_HISTORY_DB, range), _models_summary, SPEED_HISTORY_DB, since)
    by_card = {sm["key"]: sm for sm in summaries}                     # one card per provider + model
    if range == "today":
        speeds = speed_tracker.snapshot()
        models = [
            {"key": card, "roles": role_chips.get(card, []), **stats}
            for card, stats in speeds.items()
        ]
    else:
        models = [{"key": sm["key"], "model": sm["model"], "provider": sm["provider"], "roles": role_chips.get(sm["key"], []), **{k: sm[k] for k in _AGG_KEYS}} for sm in summaries]
    for m in models:
        sm = by_card.get(m["key"])
        m["v2"] = sm["v2"] if sm else _v2_model_stats([])
        m["where"] = _speed_where(m["model"], m["v2"].get("provider"))
    models = [m for m in models if _where_matches(m["where"], pc)]
    for m in models:
        m["where"], m["provider"] = _pc_shown(m["where"]), _pc_shown(m.get("provider"))

    # MultiProxy's own added time per request (the median of the saved own_ms over this range, for the device filter in use).
    own_time = {"medianMs": None, "requests": 0}
    try:
        pairs_ok = None if (pc or "all").lower() == "all" else (lambda model, provider: _where_matches(_speed_where(model, provider), pc))
        own_time = await _speed_cache.get(("own", SPEED_HISTORY_DB, range, pc), speed_queries.own_time, SPEED_HISTORY_DB, since, pairs_ok)
    except Exception as e:
        logger.debug(f"Speeds: couldn't read MultiProxy's own time ({e.__class__.__name__})")

    # skyrimnet_found (2026-09-22): None when nothing's linked at all (nothing to check); once
    # linked, whether the SkyrimNet folder itself was actually found -- via the SAME cached
    # resolver every other consumer here uses, so this can never disagree with them. Lets the
    # dashboard show a third "linked, but couldn't find it" state instead of a false-green
    # "Linked" that then silently fails on role chips (the real MO2 report
    # this fixes).
    skyrimnet_found = bool(_resolve_skyrimnet_dir_cached(skyrim_install_path)) if skyrim_install_path else None
    return _polled_json(request, {
        "linked": bool(skyrim_install_path),
        "skyrim_install_path": skyrim_install_path or None,
        "skyrimnet_found": skyrimnet_found,
        "thresholds": speed_thresholds,
        "starts_talking_thresholds": starts_talking_thresholds,
        "pc": pc,
        "models": models,
        "ownTime": own_time,
    })


# --- Speeds tab: compare by role (2026-09-20) ---
# Role chips on the "By model" view above are read LIVE from SkyrimNet's current config -- the
# moment Dialogue moves from model A to model B, model A's chip vanishes and its older history
# can no longer be labeled. This section answers a different question instead: "every model
# that's ever filled role X, side by side" -- built entirely from the roles_json snapshot
# _record_speed writes on every call, never from the live config.

def _distinct_speed_history_roles() -> list:
    """Blocking -- always called through asyncio.to_thread. The union of every individual role
    label string that has ever appeared in roles_json, sorted -- not a fixed list, so the picker
    naturally grows if SkyrimNet adds a new role and never offers one nobody's ever used."""
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        cur = conn.execute("SELECT DISTINCT roles_json FROM speed_history WHERE roles_json IS NOT NULL")
        role_set: set = set()
        for (roles_json,) in cur.fetchall():
            try:
                role_set.update(_roles_of(roles_json))
            except (TypeError, ValueError):
                continue
        return sorted(role_set)


def _query_speed_history_rows_by_role(role: str, since_ts: Optional[float]) -> list:
    """Blocking -- always called through asyncio.to_thread. ((provider, model), ts, elapsed, success) rows,
    oldest first, for every call that carried this EXACT role label at the time.

    The exact-match check happens in Python, on the parsed JSON array, deliberately NOT as a SQL
    substring/LIKE query -- a label that happens to contain another label's text (e.g. "Vision"
    vs. a hypothetical "Vision2") must never cross-contaminate. This table stays small in
    practice (a few numbers per call, never rolled off), so reading every row in the window and
    filtering in Python is simpler and more portable than depending on SQLite's JSON1 extension
    being compiled in."""
    with sqlite3.connect(SPEED_HISTORY_DB) as conn:
        if since_ts is None:
            cur = conn.execute(
                "SELECT model, ts, elapsed, success, roles_json, provider FROM speed_history "
                "WHERE roles_json IS NOT NULL ORDER BY ts"
            )
        else:
            cur = conn.execute(
                "SELECT model, ts, elapsed, success, roles_json, provider FROM speed_history "
                "WHERE roles_json IS NOT NULL AND ts >= ? ORDER BY ts",
                (since_ts,),
            )
        rows = []
        for model, ts, elapsed, success, roles_json, provider in cur.fetchall():
            try:
                roles = _roles_of(roles_json)
            except (TypeError, ValueError):
                continue
            if role in roles:
                rows.append(((provider or None, model), ts, elapsed, success))
        return rows


def _aggregate_by_role_rows(rows: list) -> list:
    """((provider, model), ts, elapsed, success) tuples, any order -> one entry per provider's model: avg/success_pct/
    calls/first_seen/last_seen, sorted fastest-avg first (the mockup's ranked leaderboard)."""
    by_model: dict = {}
    for pair, ts, elapsed, success in rows:
        by_model.setdefault(pair, []).append((ts, elapsed, success))
    out = []
    for (provider, model), model_rows in by_model.items():
        model_rows.sort(key=lambda r: r[0])
        elapsed_values = [r[1] for r in model_rows]
        success_count = sum(1 for r in model_rows if r[2])
        out.append({
            "model": model,
            "key": speed_queries.card_key(provider, model),
            "avg": sum(elapsed_values) / len(elapsed_values),
            "success_pct": round(100 * success_count / len(model_rows)),
            "calls": len(model_rows),
            "first_seen": model_rows[0][0],
            "last_seen": model_rows[-1][0],
        })
    out.sort(key=lambda m: m["avg"])
    return out


_TIME_COLUMNS = ("model", "provider", "job", "roles_json", "success", "elapsed", "first_token", "out_tokens", "out_chars", "tokens_in", "cost", "cost_note", "timings_json", "queue_wait")


def _query_time_rows(since_ts: Optional[float]) -> list:
    """Blocking. Every saved call in the range as a dict (older rows simply have None for the facts that weren't recorded yet)."""
    sql = f"SELECT {', '.join(_TIME_COLUMNS)} FROM speed_history"
    with sqlite3.connect(SPEED_HISTORY_DB, timeout=5.0) as conn:
        cur = conn.execute(sql + " WHERE ts >= ?", (since_ts,)) if since_ts is not None else conn.execute(sql)
        return [dict(zip(_TIME_COLUMNS, row)) for row in cur.fetchall()]


@app.get("/stats/speeds/time")
async def stats_speeds_time(request: Request, range: str = "today", pc: str = "all"):
    """Time breakdown: per job and model, the typical line split into waiting in line, reading the prompt up to the first word and writing; the writing speed,
    tokens and cost; and one total of what the pay-as-you-go models that report a cost cost in this range (speeds_time.py)."""
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    if range not in ("today", "7d", "30d", "all"):
        raise HTTPException(status_code=400, detail="range must be one of: today, 7d, 30d, all")
    _refresh_job_names()
    since = _range_since(range)
    answer = await _speed_cache.get(
        ("time", SPEED_HISTORY_DB, range, pc),
        lambda: speed_queries.time_answer(SPEED_HISTORY_DB, since, label_of=routing.label, where_of=_speed_where, keep=lambda w: _where_matches(w, pc), order_of=routing.order_of))
    return _polled_json(request, answer)


@app.get("/stats/speeds/roles")
async def stats_speeds_roles():
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    roles = await _speed_cache.get(("roles", SPEED_HISTORY_DB), speed_queries.distinct_roles, SPEED_HISTORY_DB, _roles_of, ttl_s=10.0)
    return {"roles": roles}


def _pair_of_key(card: str) -> tuple:
    """A card key back to (provider or None, model)."""
    provider, model = card.split("::", 1)
    return (provider or None, model)


def _by_role_models(db: str, role: str, since_ts: Optional[float]) -> list:
    """Blocking (worker thread). Every model that carried this role in the window: its average, success %, calls, first/last seen, and (Speeds v2) its
    reliability, starts-talking, typical full reply and service. One read; the per-model arithmetic is the old functions' own."""
    rows, v2_rows = speed_queries.by_role_rows(db, role, since_ts, _V2_ROW_COLUMNS, _roles_of)
    models = _aggregate_by_role_rows(rows)
    for m in models:
        stats = _v2_model_stats(v2_rows.get(_pair_of_key(m["key"]), []))
        m["reliability"] = stats["reliability"]
        m["first_token"] = stats["first_token"]
        m["full_typical"] = stats["full"]["typical"]
        m["provider"] = stats["provider"]
    return models


@app.get("/stats/speeds/by-role")
async def stats_speeds_by_role(request: Request, role: str, range: str = "all", pc: str = "all"):
    if not ENABLE_SPEED_TRACKING:
        raise HTTPException(status_code=404, detail="Speed tracking is off. Turn it on in Settings.")
    if range not in ("today", "7d", "30d", "all"):
        raise HTTPException(status_code=400, detail="range must be one of: today, 7d, 30d, all")

    since_ts = None if range == "all" else time.time() - SPEED_RANGE_WINDOW_SECONDS[range]
    # (the read AND the per-model arithmetic run in the worker thread, once, shared: see _by_role_models)
    cached = await _speed_cache.get(("byrole", SPEED_HISTORY_DB, role, range), _by_role_models, SPEED_HISTORY_DB, role, since_ts)
    models = [dict(m) for m in cached]
    total_calls = sum(m["calls"] for m in models) or 1
    for m in models:
        m["share"] = round(100 * m["calls"] / total_calls)
        m["where"] = _speed_where(m["model"], m["provider"])
    total_after = sum(m["calls"] for m in models if _where_matches(m["where"], pc)) or 1
    models = [m for m in models if _where_matches(m["where"], pc)]
    for m in models:
        m["share"] = round(100 * m["calls"] / total_after)      # share of this job within what's shown
        m["where"], m["provider"] = _pc_shown(m["where"]), _pc_shown(m.get("provider"))
    models = _rank_by_role(role, models)

    # "Currently serving" is a live cross-check against today's config, deliberately NOT "most
    # recent row" -- a model could have the most recent row in this window and still not be the
    # one the config currently points at (e.g. it was swapped out seconds ago).
    role_chips = _get_role_chips_cached()
    for m in models:
        current_labels = [chip["label"] for chip in role_chips.get(m["key"], [])]
        m["currently_serving"] = role in current_labels

    return _polled_json(request, {"role": role, "models": models})


@app.get("/config/settings")
async def settings_get():
    return _settings_state()


@app.post("/config/settings")
async def settings_set(request: Request):
    """The two on/off settings the tray app shares: `startStopWithSkyrim`, `startWithWindows`."""
    data = await request.json()
    if "startStopWithSkyrim" in data:
        on = bool(data["startStopWithSkyrim"])
        global AUTO_CLOSE_WITH_SKYRIM
        AUTO_CLOSE_WITH_SKYRIM = on
        _ini_set_general("AutoCloseWithSkyrim", "true" if on else "false")
        logger.info(f"Start and stop with Skyrim: {'on' if on else 'off'}")
        if on:
            _ensure_skyrim_watcher()
    if "pageWidth" in data:
        if data["pageWidth"] not in PAGE_WIDTHS:
            raise HTTPException(status_code=400, detail="Pick Comfortable, Wide or Full.")
        cfg = _load_config()
        cfg["page_width"] = data["pageWidth"]
        _save_config(cfg)
    if "logFields" in data:                       # in memory only: off at every start and after an hour (field_log.py); the routes ask it per request, so no restart
        if data["logFields"]:
            field_log.enable()
        else:
            field_log.disable()
        logger.info(f"Log what SkyrimNet sends: {'on for one hour' if data['logFields'] else 'off'}")
    if "moveJob" in data:                         # read live by the Routing tab from the same saved routing block: no restart
        if data["moveJob"] not in routing.ON_MOVE:
            raise HTTPException(status_code=400, detail="Pick Ask me each time, Keep the old model as a backup, or Just move it.")
        cfg = _load_config()
        block = routing.clean_settings(cfg.get("routing"))
        block["on_move"] = data["moveJob"]
        cfg["routing"] = block
        _save_config(cfg)
        logger.info(f"When a job is moved to another model: {data['moveJob']}")
    for key, (var, ini_key) in SETTINGS_OPTIONS.items():
        if key in data:
            on = bool(data[key])
            globals()[var] = on
            _ini_set_general(ini_key, "true" if on else "false")
            if key == "keepLog":
                _apply_file_logging(on)
            logger.info(f"{ini_key}: {'on' if on else 'off'}")
    if "localModelsOff" in data:                  # read live by every screen that suggests local models: no restart
        cfg = _load_config()
        on = bool(data["localModelsOff"])
        if on:
            cfg["local_models_off"] = True
        else:
            cfg.pop("local_models_off", None)
        _save_config(cfg)
        if on and data.get("stopRunning") is True:        # only after the player said yes to the list of running models; nothing is stopped by surprise
            await local_svc.stop_all()
        logger.info(f"Don't run models on this PC: {'on' if on else 'off'}")
    if "graphicsGuard" in data:                   # read live by the guard on every look: no restart
        cfg = _load_config()
        cfg["graphics_guard"] = bool(data["graphicsGuard"])
        _save_config(cfg)
        logger.info(f"Graphics memory guard: {'on' if cfg['graphics_guard'] else 'off'}")
    if "startWithWindows" in data:
        on = bool(data["startWithWindows"])
        if _started_by_tray():                   # a tray-started copy: the real per-user Run entry (nothing real is touched from a terminal run)
            entry = settings_files.run_entry(os.path.dirname(os.path.abspath(__file__)))
            if entry.supported and not entry.set(on):
                raise HTTPException(status_code=500, detail="Could not change this. Turn it off in Windows Settings under Startup apps.")
        cfg = _load_config()
        cfg["start_with_windows"] = on
        _save_config(cfg)
        logger.info(f"Start with Windows: {'on' if cfg['start_with_windows'] else 'off'}")
    return _settings_state()


@app.post("/start-here/hide")
async def start_here_hide(request: Request):
    data = await request.json()
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not start_here_ui.NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Give the Start here box a valid name.")
    cfg = _load_config()
    cfg["hidden_start_here"] = start_here_ui.clean_names(list(cfg.get("hidden_start_here") or []) + [name])
    _save_config(cfg)
    return {"hidden": cfg["hidden_start_here"]}


@app.post("/start-here/show-all")
async def start_here_show_all():
    cfg = _load_config()
    cfg["hidden_start_here"] = []
    _save_config(cfg)
    return {"hidden": []}


def _port_problem(port: int) -> Optional[str]:
    if port == MAIN_PORT:
        return None
    if port in (INTERCEPTOR_PORT, 8099, 8100, 8110) or local_models.SERVER_PORT_START <= port < local_models.SERVER_PORT_END:
        return RESERVED_PORT_MESSAGE.format(port=port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            who = _port_owner_name(port)
            if who:
                return f"Port {port} is in use by {who}. MultiProxy needs its own port. The default is 8000."
            return f"Port {port} is used by another program. Pick a different one."
    return None


def _port_owner_name(port: int) -> Optional[str]:
    """The program using a port (fix_it.py's lookup), or None when the system won't say. Port 8080 is SkyrimNet's own web page when the game holds it."""
    try:
        pids = [p for p in _fix_it_listener_pids(port) if p and p != os.getpid()]
        name = fix_it.process_name_of(pids[0]) if pids else None
    except Exception:
        return None
    if port == 8080 and (not name or name.lower().startswith("skyrim")):
        return "SkyrimNet's web page" if name else None
    return name


def _restart_self(port: int) -> None:
    """Start a new copy of MultiProxy on `port` and close this one. The new copy waits for this one to finish (it shares
    port 9999 with it). The tray app (a later item) will do this itself; until then this keeps the setting working."""
    if _TRAY_MODE:                  # the tray app owns this process: exit with its "start me again" code and it does
        local_svc.stop_all_sync()
        offloop_log.drain_all()
        speed_store.stop()      # write the Speeds rows still queued before the process ends
        os._exit(75)
    env = {**os.environ, "MULTIPROXY_PORT": str(port), "MULTIPROXY_WAIT_FOR_PID": str(os.getpid())}
    hidden_process.popen([sys.executable] + sys.argv, env=env, cwd=os.path.dirname(os.path.abspath(__file__)),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    local_svc.stop_all_sync()
    offloop_log.drain_all()
    speed_store.stop()      # write the Speeds rows still queued before the process ends
    os._exit(0)


@app.post("/config/settings/private-networks")
async def settings_private_networks(request: Request):
    """The VPN or Tailscale ranges this device may connect to besides the usual home ones: {"networks": "10.8.0.0/16, ..."} (or a list). A public range, a wildcard or a malformed entry is refused."""
    data = await request.json()
    try:
        nets = other_pcs.clean_private_networks(data.get("networks", ""))
    except other_pcs.OtherPcsError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    cfg = _load_config()
    cfg["private_networks"] = nets
    _save_config(cfg)
    other_pcs.set_extra_networks(nets)
    logger.info(f"VPN networks this device may connect to: {', '.join(nets) or 'none besides the usual ones'}")
    return {"privateNetworks": nets}


@app.post("/config/settings/port")
async def settings_port(request: Request):
    data = await request.json()
    raw = str(data.get("port", "")).strip()
    if not raw.isdigit() or not (1024 <= int(raw) <= 65535):
        raise HTTPException(status_code=400, detail="Pick a whole number between 1024 and 65535.")
    port = int(raw)
    if port == MAIN_PORT:
        return {"port": port, "restarting": False}
    problem = await asyncio.to_thread(_port_problem, port)       # (looking up the program may take a moment)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    cfg = _load_config()
    cfg["main_port"] = port
    _save_config(cfg)
    try:
        with open(os.path.join(_settings_dir(), "endpoint.json"), "w", encoding="utf-8") as f:
            json.dump({"url": f"http://127.0.0.1:{port}", "port": port, "version": PROXY_VERSION}, f)
    except OSError:
        pass
    logger.info(f"Port SkyrimNet connects to changed from {MAIN_PORT} to {port}: restarting")
    loop = asyncio.get_running_loop()
    loop.call_later(1.0, _restart_self, port)         # after this answer has gone back to the page
    return {"port": port, "restarting": True}


@app.post("/config/settings/limits")
async def settings_limits(request: Request):
    """How many requests each AI handles at once (0 = unlimited), and the seconds a request waits in line. Any subset may be sent;
    anything that isn't a whole number is refused with a plain line for that box, and nothing at all is changed then."""
    data = await request.json()
    errors, parsed = {}, {}
    ini_keys = {pid: ini for ini, pid, _l, _d in _REQUEST_LINE_PROVIDERS}
    for k, v in data.items():
        text = str(v).strip()
        if k == "queueSeconds":
            if text.isdigit() and int(text) >= 1:
                parsed[k] = int(text)
            else:
                errors[k] = SECONDS_MESSAGE
        elif k in ini_keys:
            if text.isdigit():
                parsed[k] = int(text)
            else:
                errors[k] = WHOLE_NUMBER_MESSAGE
        else:
            errors[k] = "Setting not found."
    if errors:
        return JSONResponse(status_code=400, content={"detail": next(iter(errors.values())), "errors": errors})
    global QUEUE_TIMEOUT_SECONDS
    for k, n in parsed.items():
        if k == "queueSeconds":
            QUEUE_TIMEOUT_SECONDS = float(n)
            _ini_set("Concurrency", "QueueTimeoutSeconds", str(n))
        else:
            REQUEST_LIMITS[k] = n
            _ini_set("Concurrency", ini_keys[k], str(n))
    if parsed:
        logger.info(f"Request limits changed: {parsed}")
    return _limits_state()


@app.post("/config/settings/check-update")
async def settings_check_update():
    return await _check_for_update()


# --- Set me up (2026-10-26): the screens are set_me_up_ui.py, the logic is set_me_up.py. Every install here is an existing one (engine, model, decision model, voice). ---
@app.get("/config/set-me-up")
async def set_me_up_state():
    """This PC as Set me up sees it (card, folder, free room), the question to ask, and the install in progress or just finished."""
    d = await set_me_up_svc.look()
    d["modelsOff"] = models_off.is_off(_load_config())          # the switch in Settings: Set me up then says models are off here and points to another PC
    d["cloudOffer"] = bool(d["modelsOff"] and not decision_handles.cloud_only() and decision_handles.cloud_choice(bool(typesafe_api_key), bool(nano_api_key)) is not None)       # a key is saved: offer Cloud only (a button; never ticked for them)
    return d


@app.post("/config/set-me-up/measure")
async def set_me_up_measure():
    """Read the running game's graphics memory (about 10 seconds) so the verdicts use it instead of the estimate."""
    return await set_me_up_svc.measure()


@app.get("/config/set-me-up/job")
async def set_me_up_job():
    return {"job": set_me_up_svc.job.to_json() if set_me_up_svc.job else None}


@app.post("/config/set-me-up/plan")
async def set_me_up_plan(request: Request):
    """{"game": bool, "voice": bool, "chat": model id or absent} -> what would be installed (the picks come from the program's own fit check)."""
    data = await request.json()
    return await set_me_up_svc.plan(bool(data.get("game")), bool(data.get("voice")), str(data.get("chat") or "") or None)


@app.post("/config/set-me-up/install")
async def set_me_up_install(request: Request):
    data = await request.json()
    try:
        job = await set_me_up_svc.start(bool(data.get("game")), bool(data.get("voice")), str(data.get("chat") or "") or None)
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"job": job.to_json()}


@app.post("/config/set-me-up/pause")
async def set_me_up_pause():
    await set_me_up_svc.pause()
    return {"job": set_me_up_svc.job.to_json() if set_me_up_svc.job else None}


@app.post("/config/set-me-up/resume")
async def set_me_up_resume():
    try:
        job = await set_me_up_svc.resume()
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"job": job.to_json()}


@app.post("/config/set-me-up/cancel")
async def set_me_up_cancel():
    await set_me_up_svc.cancel()
    return {"job": set_me_up_svc.job.to_json() if set_me_up_svc.job else None}


@app.post("/config/set-me-up/dismiss")
async def set_me_up_dismiss():
    set_me_up_svc.dismiss()
    return {"job": None}


@app.get("/config/get-started")
async def get_started_get():
    """What the Get started flow shows: where it is, and this install's real values (the mod package, the lines for the mod's own file, the Endpoint)."""
    return {**_get_started_state(), "setMeUp": {"show": await _set_me_up_show()}}


@app.post("/config/get-started")
async def get_started_set(request: Request):
    """Remember the flow: {"step": 0-4} where they are, {"done": true} after Skip setup or Finish, {"ai": "<provider id>"} the AI they set up,
    {"restart": true} (Run Get started again) goes back to the first step. Writes only MultiProxy's own settings file."""
    data = await request.json()
    cfg = _load_config()
    rec = dict(cfg.get("get_started") or {})
    if data.get("restart"):
        rec["step"] = 0
    if "step" in data:
        try:
            rec["step"] = max(0, min(4, int(data["step"])))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Which step?")
    if "done" in data:
        rec["done"] = bool(data["done"])
    if "ai" in data:
        rec["ai"] = str(data["ai"] or "")[:40]
    rec.setdefault("step", 0)
    cfg["get_started"] = rec
    cfg["help_seen"] = True            # Get started has been shown: the Help tab no longer opens by itself either
    _save_config(cfg)
    return _get_started_state()


@app.post("/config/settings/pick-folder")
async def settings_pick_folder(request: Request):
    what = str((await request.json()).get("what", ""))
    title = {"skyrim": "Choose your Skyrim folder", "skyrimnet": skyrimnet_finder.WORDS["ask"], "models": "Choose where to save local models",
             "voicedownloads": "Choose where to keep voice downloads"}.get(what)
    if not title:
        raise HTTPException(status_code=400, detail="Unknown folder.")
    return {"path": await asyncio.to_thread(local_models.pick_folder_dialog, title)}


@app.post("/config/settings/open-folder")
async def settings_open_folder(request: Request):
    what = str((await request.json()).get("what", ""))
    if what == "settings":
        target, select = _settings_dir(), None
    elif what == "models":
        if not local_svc.folder:
            raise HTTPException(status_code=404, detail="No models folder has been chosen yet.")
        target, select = str(local_svc.folder), None
    elif what == "mod":
        select = _mod_package_path()
        if not select:
            raise HTTPException(status_code=404, detail="The mod package is missing.")
        target = os.path.dirname(select)
    else:
        raise HTTPException(status_code=400, detail="Unknown folder.")
    _open_in_file_manager(target, select)
    return {"status": "opened"}


def _open_in_file_manager(folder: str, select: Optional[str] = None) -> None:
    """Show a folder in the device's file manager (with `select` highlighted on Windows, for drag and drop into a mod manager)."""
    try:
        if sys.platform == "win32":
            if select:
                hidden_process.popen(["explorer", f"/select,{select}"])
            else:
                os.startfile(folder)  # noqa: S606 (opens a window on purpose)
        elif sys.platform == "darwin":
            hidden_process.popen(["open", folder])
        else:
            hidden_process.popen(["xdg-open", folder])
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not open that folder. {e.strerror or e}")


@app.get("/config/skyrim-path/detect")
async def detect_skyrim_path_route():
    global skyrim_install_path
    if skyrim_install_path and os.path.isdir(skyrim_install_path):
        return {"linked": True, "path": skyrim_install_path, "source": "saved", "skyrimnet_found": bool(_resolve_skyrimnet_dir_cached(skyrim_install_path))}
    detected = _detect_skyrim_install()
    if detected:
        skyrim_install_path = detected
        cfg = _load_config()
        cfg["skyrim_install_path"] = detected
        _save_config(cfg)
        logger.info(f"Skyrim install auto-detected via Steam for the Speeds tab: {detected}")
        # _detect_skyrim_install() only ever returns a root the resolver already validated, so
        # this is always True -- included anyway so every one of these three responses carries
        # the same field, rather than the frontend having to know which endpoints omit it.
        return {"linked": True, "path": detected, "source": "steam", "skyrimnet_found": True}
    return {"linked": False, "path": None}


@app.post("/config/skyrimnet-folder")
async def save_skyrimnet_folder(request: Request):
    """The answer to "Where is your SkyrimNet folder?": any of the SkyrimNet folder, the mod's own folder, an MO2 instance, a mods folder or a game folder. Saved in its own setting
    (never over "Your Skyrim folder"); nothing is written into the player's folders. {"path": ""} forgets the answer."""
    global skyrimnet_pick
    data = await request.json()
    path = str((data or {}).get("path") or "").strip() if isinstance(data, dict) else ""
    if not path:
        skyrimnet_pick = ""
        cfg = _load_config()
        cfg.pop("skyrimnet_folder_pick", None)
        _save_config(cfg)
        return {"ok": True, "line": "", "folder": _resolve_skyrimnet_dir_cached(skyrim_install_path) if skyrim_install_path else None}
    found = await asyncio.to_thread(skyrimnet_finder.from_pick, path)
    if not found:
        return {"ok": False, "line": skyrimnet_finder.WORDS["none"], "folder": None}
    skyrimnet_pick = path
    cfg = _load_config()
    cfg["skyrimnet_folder_pick"] = path
    _save_config(cfg)
    logger.info("The player picked the folder to look for SkyrimNet in; found it.")
    return {"ok": True, "line": skyrimnet_finder.WORDS["found"].format(folder=found), "folder": found}


@app.post("/config/skyrim-path")
async def save_skyrim_path(request: Request):
    global skyrim_install_path
    data = await request.json()
    path = (data.get("path") or "").strip()
    if not path:
        skyrim_install_path = ""
        cfg = _load_config()
        cfg.pop("skyrim_install_path", None)
        _save_config(cfg)
        logger.info("Skyrim install unlinked from the Speeds tab")
        return {"linked": False, "path": None}
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="That folder does not exist.")
    skyrim_install_path = path
    cfg = _load_config()
    cfg["skyrim_install_path"] = path
    _save_config(cfg)
    # Verify the real SkyrimNet folder is actually findable under this root (2026-09-22) --
    # os.path.isdir(path) above only proves the ROOT exists, never that SkyrimNet's own files are
    # anywhere under it. Still saves the path either way (the player may fix it via mods later, or
    # the folder may genuinely not exist yet before SkyrimNet has ever run) -- this is a warning,
    # never a hard block on linking.
    skyrimnet_found = bool(_resolve_skyrimnet_dir_cached(path))
    if skyrimnet_found:
        logger.info(f"Skyrim install linked manually for the Speeds tab: {path}")
    else:
        logger.warning(f"Skyrim install linked manually for the Speeds tab, but no SkyrimNet folder found under it (checked direct + MO2 layouts): {path}")
    return {"linked": True, "path": path, "skyrimnet_found": skyrimnet_found}


def _clean_threshold_set(data, label: str) -> dict:
    """{category: {good, slow}} -> the same, as floats, or a 400 saying what's wrong."""
    if not isinstance(data, dict) or set(data.keys()) != set(DEFAULT_SPEED_THRESHOLDS.keys()):
        raise HTTPException(
            status_code=400,
            detail="You need a threshold for exactly these: dialogue, gamemaster, vision, background.",
        )
    cleaned = {}
    for category, bounds in data.items():
        try:
            good = float(bounds["good"])
            slow = float(bounds["slow"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Check the {label}threshold values for '{category}'.")
        if good < 0 or slow < 0 or slow < good:
            raise HTTPException(
                status_code=400,
                detail=f"In '{category}': {label}slow must be at least as high as good, and both must be 0 or higher.",
            )
        cleaned[category] = {"good": good, "slow": slow}
    return cleaned


@app.post("/config/speed-thresholds")
async def save_speed_thresholds(request: Request):
    """Saves the four full-reply bounds, plus (Speeds v2) an optional `starts_talking` set of the same
    shape. Leaving `starts_talking` out keeps whatever was saved before."""
    global speed_thresholds, starts_talking_thresholds
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="You need a threshold for exactly these: dialogue, gamemaster, vision, background.")
    data = dict(data)
    starts = data.pop("starts_talking", None)
    cleaned = _clean_threshold_set(data, "")
    cleaned_starts = _clean_threshold_set(starts, "'starts talking' ") if starts is not None else None
    speed_thresholds = cleaned
    cfg = _load_config()
    cfg["speed_thresholds"] = cleaned
    if cleaned_starts is not None:
        starts_talking_thresholds = cleaned_starts
        cfg["starts_talking_thresholds"] = cleaned_starts
    _save_config(cfg)
    logger.info("Speed thresholds updated and saved to config.json")
    return {"status": "saved", "thresholds": cleaned, "starts_talking_thresholds": starts_talking_thresholds}


# --- Claude's live model list (2026-09-30) -------------------------------------------------------
# Anthropic's GET /v1/models, called with the same captured Claude CLI login every Claude request
# already uses (auth.headers) -- confirmed live 2026-09-30 that a subscription login is accepted (no API
# key needed). Cached like the Gemini/Codex lists; the ↻ Refresh button bypasses the cache. If it can't be
# fetched (not signed in, endpoint refuses the login, network) a built-in list is shown instead, with a
# quiet note in the card's heading.
CLAUDE_MODELS_URL = "https://api.anthropic.com/v1/models"
CLAUDE_MODEL_LIST_TIMEOUT = 5.0
CLAUDE_MODEL_LIST_CACHE_TTL = 600.0  # seconds -- the lineup changes a few times a year, not a few times an hour
# A FAILED fetch is remembered too (2026-10-02). It used to be retried on every poll: with a revoked sign-in the same refusal came back and was logged every 2 seconds
# for as long as anything polled. Every other provider's list already caches its failure for the same time as a success; this one now does too. The list is tried
# again after this long, at once when the player presses Refresh, and at once when the sign-in itself changes.
CLAUDE_MODEL_LIST_FAIL_BACKOFF = 300.0
CLAUDE_MODEL_LIST_MAX_PAGES = 5

# One-line descriptions, only for models we actually have wording for (Anthropic's own models overview,
# platform.claude.com/docs/en/about-claude/models/overview, checked 2026-10-26). Matched by id prefix so a dated id
# (claude-haiku-4-5-20251001) still finds its line. Anything else shows its name alone -- never an invented description.
# An older model that is no longer current (Haiku 4.5) keeps its line here so it still shows one when the live list
# returns it, but it is not in CLAUDE_STATIC_FALLBACK_MODELS below.
_CLAUDE_MODEL_NOTES = (
    ("claude-opus-5-5", "For complex tasks and hard work."),
    ("claude-sonnet-5-5", "The best mix of speed and smarts."),
    ("claude-fable-5-1", "For deep reasoning and multi-step tasks."),
    ("claude-haiku-5-5", "The fastest model for frequent, quick tasks."),
    ("claude-haiku-4-5", "The fastest model with near-frontier intelligence"),
)

CLAUDE_STATIC_FALLBACK_MODELS = [
    ["claude-opus-5-5", "Opus 5.5", _CLAUDE_MODEL_NOTES[0][1]],
    ["claude-sonnet-5-5", "Sonnet 5.5", _CLAUDE_MODEL_NOTES[1][1]],
    ["claude-fable-5-1", "Fable 5.1", _CLAUDE_MODEL_NOTES[2][1]],
    ["claude-haiku-5-5", "Haiku 5.5", _CLAUDE_MODEL_NOTES[3][1]],
]

# (fetched_at, raw model dicts). `_claude_models_last_good` survives expiry and failed refreshes so the
# default-model pick never flips back to the built-in one just because a refresh hiccuped.
_claude_models_cache: dict = {"at": 0.0, "value": None}
_claude_models_last_good: Optional[list] = None


def _claude_model_note(model_id: str) -> str:
    for prefix, note in _CLAUDE_MODEL_NOTES:
        if model_id.startswith(prefix):
            return note
    return ""


def _claude_model_label(model_id: str) -> str:
    """'Sonnet 5.5' for a model id: the live display name when the last-good list has it (minus its
    'Claude ' prefix), else worked out from the id itself ('claude-sonnet-5-5' -> 'Sonnet 5.5')."""
    for m in (_claude_models_last_good or []):
        if m.get("id") == model_id and m.get("display_name"):
            name = str(m["display_name"])
            return name[len("Claude "):] if name.startswith("Claude ") else name
    parts = [p for p in model_id.split("-") if not (len(p) == 8 and p.isdigit())]
    if len(parts) >= 3 and parts[0] == "claude":
        return f"{parts[1].title()} {'.'.join(parts[2:])}"
    return model_id


def _default_claude_model() -> str:
    """The model used when a request names none, and Quick Test's first pick: the newest Sonnet in
    Anthropic's live list when we have one, else the built-in DEFAULT_MODEL. Never does a network call
    (it runs on every request) -- reads whatever list was last fetched successfully."""
    sonnets = [m for m in (_claude_models_last_good or [])
               if str(m.get("id", "")).startswith("claude-sonnet-") and m.get("created_at")]
    if not sonnets:
        return DEFAULT_MODEL
    return max(sonnets, key=lambda m: m["created_at"])["id"]


_claude_models_fail: dict = {"failing": False, "at": 0.0, "login": "", "why": "", "sign_in": False}
_claude_models_lock = asyncio.Lock()


def _claude_login_fingerprint() -> str:
    """A short fingerprint of the current Claude sign-in (never the token itself): when it changes, a failed model list is worth trying again at once."""
    value = ""
    for k, v in (auth.headers or {}).items():
        if k.lower() == "authorization":
            value = str(v)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""


async def _claude_models_once(headers: dict) -> tuple:
    """One try at Anthropic's list: (models, None) or (None, the reason: an HTTP status number, or a short word for a network problem)."""
    models: list = []
    try:
        timeout = aiohttp.ClientTimeout(total=CLAUDE_MODEL_LIST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            after_id = None
            for _ in range(CLAUDE_MODEL_LIST_MAX_PAGES):
                params = {"limit": "100"}
                if after_id:
                    params["after_id"] = after_id
                async with session.get(CLAUDE_MODELS_URL, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        return None, resp.status
                    page = await resp.json()
                models.extend(page.get("data") or [])
                if not page.get("has_more") or not page.get("last_id"):
                    break
                after_id = page["last_id"]
    except asyncio.TimeoutError:
        return None, "it took too long to answer"
    except (aiohttp.ClientError, ValueError) as e:
        return None, f"couldn't reach it ({e.__class__.__name__})"
    models = [m for m in models if isinstance(m, dict) and str(m.get("id", "")).startswith("claude-")]
    return (models or None), (None if models else "it sent no models")


async def _fetch_claude_models(*, force: bool = False) -> Optional[list]:
    """Anthropic's model list as raw dicts (id, display_name, created_at, ...), or None when it can't
    be fetched right now -- Claude not signed in yet, the endpoint refusing this login, a timeout.
    Never triggers a first login capture. A refused sign-in (401/403) is refreshed ONCE, the same way a refused chat request is
    (the captured login can be revoked while Claude itself works fine: the list call used to keep failing until a chat request
    happened to refresh it), and tried again. A failure is remembered for CLAUDE_MODEL_LIST_FAIL_BACKOFF and logged once when it
    starts and once when it ends; `force` (the Refresh button) and a changed sign-in try again at once."""
    global _claude_models_last_good
    if not force and _claude_models_cache["value"] is not None and time.time() - _claude_models_cache["at"] < CLAUDE_MODEL_LIST_CACHE_TTL:
        return _claude_models_cache["value"]
    if not auth.is_ready or not auth.headers:
        return None
    fail = _claude_models_fail
    async with _claude_models_lock:                     # one try at a time: the poll that waits behind it gets its answer from the cache or the back-off
        now = time.time()
        if not force and _claude_models_cache["value"] is not None and now - _claude_models_cache["at"] < CLAUDE_MODEL_LIST_CACHE_TTL:
            return _claude_models_cache["value"]
        if not auth.is_ready or not auth.headers:
            return None
        if not force and fail["failing"] and fail["login"] == _claude_login_fingerprint() and now - fail["at"] < CLAUDE_MODEL_LIST_FAIL_BACKOFF:
            return None
        headers = {k: v for k, v in auth.headers.items()
                   if k.lower() not in {"content-length", "content-type", "accept-encoding", "host", "connection"}}
        models, why = await _claude_models_once(headers)
        if why in (401, 403):
            try:
                auth.invalidate()
                await auth.ensure_ready(force=True, timeout=60)
                headers = {k: v for k, v in auth.headers.items()
                           if k.lower() not in {"content-length", "content-type", "accept-encoding", "host", "connection"}}
                models, why = await _claude_models_once(headers)
            except Exception:
                pass                                        # the refresh itself failed: the failure below says so; chat requests have their own refresh
        if models:
            if fail["failing"]:
                logger.info("Claude's model list is available again")
            fail.update(failing=False, why="", sign_in=False)
            _claude_models_cache["at"] = time.time()
            _claude_models_cache["value"] = models
            _claude_models_last_good = models
            return models
        sign_in = why in (401, 403) or not auth.is_ready
        if not fail["failing"]:
            reason = f"Anthropic refused the sign-in (HTTP {why})" if why in (401, 403) else (f"Anthropic answered HTTP {why}" if isinstance(why, int) else str(why))
            logger.info(f"Claude's model list isn't available: {reason}. Showing the saved list; trying again in a few minutes, or when you press Refresh.")
        fail.update(failing=True, at=time.time(), login=_claude_login_fingerprint(), why=str(why), sign_in=sign_in)
        return None


async def _claude_status(*, force: bool = False) -> dict:
    """{"models": [[id, display name, one-line note], ...], "label": ..., "live": bool} for the Claude
    card and /v1/models: the live list (newest first) or the built-in one."""
    raw = await _fetch_claude_models(force=force)
    if not raw:
        return {"models": CLAUDE_STATIC_FALLBACK_MODELS, "live": False,
                "label": "Models"}
    rows = []
    for m in sorted(raw, key=lambda m: m.get("created_at") or "", reverse=True):
        name = str(m.get("display_name") or m["id"])
        rows.append([m["id"], name[len("Claude "):] if name.startswith("Claude ") else name, _claude_model_note(m["id"])])
    return {"models": rows, "live": True, "label": "Models"}


# --- /v1/models answers from what MultiProxy already knows (2026-10-02) -----------------------------------------------------------
# Asking Ollama, LM Studio, Antigravity and Codex for their lists takes 0.1 to 1.3 s each (the last two start a program) and their own
# caches last only 10 to 60 s, so SkyrimNet's spaced-out model checks paid for all of them, one after the other, about once a call.
# Now each of those sources keeps its last list here and is refreshed in the background (one refresh at a time per source, never on
# the way to an answer); a slow or offline source just shows its last known list. Only the very first call after start waits for a
# source that has never answered, at most MODELS_FIRST_WAIT_S together, and a source still silent after that is left out until it answers.
MODELS_REFRESH_S = 5.0            # a source's list is older than this -> refresh it in the background (their own caches still apply)
MODELS_FIRST_WAIT_S = 2.5         # the most the very first call waits for sources that have never answered
_models_sources: dict = {}        # name -> {"rows": [(id, owned_by), ...] or None, "at": monotonic or 0.0, "task": asyncio.Task or None}


async def _models_fetch(name: str) -> list:
    """The rows (id, owned_by) one source offers right now, asked the slow way."""
    if name == "claude":
        return [(row[0], "anthropic") for row in (await _claude_status())["models"]]
    if name == "gemini":
        return [(row[0], "google") for row in (await _gemini_status())["models"]]
    if name == "xai":
        return [(row[0], "xai") for row in (await _xai_status())["models"]]
    if name == "continuum":
        return [(row[0], "continuum") for row in (await _continuum_status())["models"]]
    if name in key_sources.BY_ID:
        return [(row[0], name) for row in (await _ks_status(key_sources.BY_ID[name]))["models"]]
    if name == "grok":
        # "owned_by" means "reached through xAI's Grok CLI".
        return [(row[0], "xai-grok-cli") for row in (await _grok_status())["models"]]
    if name == "ollama":
        return [(row[0], "ollama") for row in (await _ollama_status())["models"]]
    if name == "lmstudio":
        return [(row[0], "lmstudio") for row in (await _lmstudio_status())["models"]]
    if name == "codex":
        # row[0], not a 3-tuple unpack: _codex_status() appends _codex_pinned_rows()'s own rows, which have a 4th element.
        return [(row[0], "openai") for row in (await _codex_status())["models"]]
    if name == "agy":
        # "owned_by" means "reached through Google's Antigravity CLI", not "made by Google" (its lineup can include Claude/GPT-OSS models).
        return [(row[0], "google-antigravity") for row in (await _agy_status())["models"]]
    return []


def _models_source(name: str) -> dict:
    return _models_sources.setdefault(name, {"rows": None, "at": 0.0, "task": None})


async def _models_refresh(name: str) -> None:
    src = _models_source(name)
    try:
        src["rows"] = await _models_fetch(name)
        src["at"] = time.monotonic()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        src["at"] = time.monotonic()          # (keep the last list; try again after MODELS_REFRESH_S)
        logger.debug(f"/v1/models: couldn't refresh the {name} list: {e}")
    finally:
        src["task"] = None


def _models_kick(name: str) -> "Optional[asyncio.Task]":
    """Start one background refresh of a source if its list is old and none is running. Returns the running task, if any."""
    src = _models_source(name)
    task = src["task"]
    if task is not None and not task.done() and task.get_loop() is asyncio.get_running_loop():
        return task
    if src["rows"] is not None and time.monotonic() - src["at"] < MODELS_REFRESH_S:
        return None
    src["task"] = task = asyncio.ensure_future(_models_refresh(name))
    return task


def _models_forget() -> None:
    """Drop every remembered list (a test, or a change that must show at once)."""
    for src in _models_sources.values():
        t = src.get("task")
        if t is not None and not t.done():
            t.cancel()
    _models_sources.clear()


@app.get("/v1/models")
async def list_models():
    names = ["claude"]
    if gemini_api_key:
        names.append("gemini")
    if xai_api_key:
        names.append("xai")
    if continuum_api_key:
        names.append("continuum")
    names += [s.id for s in key_sources.SOURCES if _ks_keys.get(s.id)]
    names += ["ollama", "lmstudio"]
    if _codex_path():
        names.append("codex")
    if _agy_path():
        names.append("agy")
    if _grok_path():
        names.append("grok")
    tasks = {n: _models_kick(n) for n in names}
    first = [t for n, t in tasks.items() if t is not None and _models_source(n)["rows"] is None]
    if first:
        await asyncio.wait(first, timeout=MODELS_FIRST_WAIT_S)          # (only ever true for sources that have never answered)

    def rows(name):
        return _models_source(name)["rows"] or []

    data = [{"id": mid, "object": "model", "owned_by": by} for mid, by in rows("claude")]
    if glm_api_key:
        # Real, live catalog confirmed 2026-09-13 via GET https://api.z.ai/api/paas/v4/models --
        # update this list if z.ai retires/adds a model rather than letting it go stale silently.
        data.extend(
            {"id": f"{GLM_PREFIX}{m}", "object": "model", "owned_by": "zhipu"}
            for m in ("glm-4.5", "glm-4.5-air", "glm-4.6", "glm-4.7", "glm-5", "glm-5-turbo", "glm-5.1", "glm-5.2", "glm-5.3", "glm-5.3-flash")
        )
    if nano_api_key:
        data.append({"id": f"{NANO_PREFIX}*", "object": "model", "owned_by": "nano-gpt"})
    if openai_api_key:
        # Real, live catalog confirmed 2026-09-13 via GET https://api.openai.com/v1/models with a
        # real key -- update this list if OpenAI retires/adds a model rather than letting it go stale.
        data.extend(
            {"id": f"{OPENAI_PREFIX}{m}", "object": "model", "owned_by": "openai"}
            for m in ("gpt-5.5", "gpt-5.4", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "o3")
        )
    if deepseek_api_key:
        # Real, live catalog confirmed 2026-09-16 via GET https://api.deepseek.com/models with a
        # real key -- deepseek-flash/deepseek-v4-pro, not deepseek-chat/deepseek-reasoner (this
        # project's own prior, now-stale knowledge). Update this list if DeepSeek retires/adds a
        # model rather than letting it go stale.
        data.extend(
            {"id": f"{DEEPSEEK_PREFIX}{m}", "object": "model", "owned_by": "deepseek"}
            for m in ("deepseek-flash", "deepseek-v4-pro")
        )
    if kilo_api_key:
        data.append({"id": f"{KILO_PREFIX}*", "object": "model", "owned_by": "kilo"})
    if gemini_api_key:
        data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("gemini"))
    if xai_api_key:
        data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("xai"))
    if continuum_api_key:
        data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("continuum"))
    for s in key_sources.SOURCES:
        if _ks_keys.get(s.id):
            data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows(s.id))
    data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("ollama"))
    data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("lmstudio"))
    data.extend({"id": mid, "object": "model", "owned_by": "local"} for mid, _, _ in local_svc.provider_status()["models"])
    data.extend({"id": mid, "object": "model", "owned_by": "other-pc"} for mid, _, _ in remote_svc.online_model_names())
    if "codex" in names:
        data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("codex"))
    if "agy" in names:
        data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("agy"))
    if "grok" in names:
        data.extend({"id": mid, "object": "model", "owned_by": by} for mid, by in rows("grok"))
    return {"object": "list", "data": data}


@app.get("/health")
async def health():
    # "status" reports the PROXY itself, not Claude specifically -- Claude is now optional (see
    # AuthCache/capture_auth), so this endpoint answering at all already means the proxy is up and
    # every other configured provider works, regardless of Claude's own state. Claude's own
    # availability lives in the claude_* fields below, same shape as codex_cli_found/codex_logged_in.
    return {
        "status": "healthy",
        # This instance's own real multiproxy.py path -- added so a SECOND proxy that finds this one
        # holding a port it needs (see _identify_port_holder) can show exactly where it's running
        # from without having to guess one out of a possibly-relative command line.
        "proxy_path": os.path.abspath(__file__),
        "claude_path": CLAUDE_PATH,
        "claude_cli_found": CLAUDE_PATH is not None,
        "auth_cached": auth.is_ready,
        "claude_unavailable_reason": None if auth.is_ready else auth.last_error,
        "glm_configured": glm_api_key is not None,
        "nano_configured": nano_api_key is not None,
        "openai_configured": openai_api_key is not None,
        "deepseek_configured": deepseek_api_key is not None,
        "kilo_configured": kilo_api_key is not None,
        "gemini_configured": gemini_api_key is not None,
        "xai_configured": xai_api_key is not None,
        "continuum_configured": continuum_api_key is not None,
        **{f"{s.id}_configured": _ks_keys.get(s.id) is not None for s in key_sources.SOURCES},
        "grok_cli_found": _grok_path() is not None,
        "grok_logged_in": (await _grok_status())["state"] == "ok" if _grok_path() else False,
        "ollama_cloud_configured": ollama_api_key is not None,
        "codex_cli_found": _codex_path() is not None,
        "codex_logged_in": await _codex_logged_in() if _codex_path() else False,
        "antigravity_cli_found": _agy_path() is not None,
        # Derived from the same cached _agy_status() check every other surface uses (there's no
        # cheap, local-only "is a session active" signal the way Codex's own `login status`
        # subcommand gives -- confirmed live: Antigravity's credentials live in the OS keyring, not
        # a readable local file, and no dedicated auth-status subcommand exists), so this can be up
        # to AGY_MODELS_CACHE_TTL seconds stale rather than always perfectly live.
        "antigravity_logged_in": (await _agy_status())["state"] == "ok" if _agy_path() else False,
        # The request line (see [Concurrency] in multiproxy.ini): one entry per provider that has a limit
        # set, live counts. Empty when no limit is set anywhere.
        "concurrency": _request_line_snapshot(),
    }


@app.get("/stats/request-line")
async def request_line_stats():
    """Just the request-line counts /health also carries -- a separate, cheap route because /health
    does real work on every call (it can run the Codex CLI's login check), which is too heavy for
    the dashboard to poll every few seconds."""
    return _request_line_snapshot()


def _default_provider_id() -> str:
    """Which provider owns DEFAULT_MODEL right now -- computed from the same real is_X_model
    checks the request dispatcher uses (never a separate, driftable copy of that logic), so the
    dashboard's own initial selection always matches what a plain, un-prefixed DEFAULT_MODEL
    request would actually route to."""
    m = _default_claude_model()
    if is_glm_model(m):
        return "glm"
    if is_nano_model(m):
        return "nano"
    if is_openai_model(m):
        return "openai"
    if is_kilo_model(m):
        return "kilo"
    if is_gemini_model(m):
        return "gemini"
    if is_ollama_model(m):
        return "ollama"
    if is_lmstudio_model(m):
        return "lmstudio"
    if is_local_model(m):
        return "local"
    if is_remote_model(m):
        return "remote"
    if is_codex_model(m):
        return "codex"
    if is_geminicli_model(m):
        return "geminicli"
    if is_grok_model(m):
        return "grok"
    if is_xai_model(m):
        return "xai"
    if is_continuum_model(m):
        return "continuum"
    src = key_sources.match(m)
    if src:
        return src.id
    return "claude"


def _codex_preset_base_url(preset: str) -> Optional[str]:
    return {
        "openai": CODEX_OPENAI_BASE_URL,
        "ollama": CODEX_OLLAMA_BASE_URL,
        "lmstudio": CODEX_LMSTUDIO_BASE_URL,
    }.get(preset)


def _codex_provider_dashboard_entries(codex_status: dict) -> list:
    """The 'Codex providers' management list + the inner provider picker's own option data --
    the implicit, always-present default entry first (matching design/dashboard-codex-multi-
    provider-mockup.html's own row order), then every saved provider in codex_providers.

    Each entry carries everything the dashboard needs to render itself without another round
    trip: `models`/`any` for the models-section-vs-any-model-text switch (only the default
    provider ever gets a real live models list -- see the mockup's own settled design note #5 --
    but EVERY provider's own pinned models, if any, are folded into `models` too, via
    _codex_pinned_rows), `removable`/`editable`, and a `typeLabel` matching the mockup's row copy.
    """
    entries = [{
        "id": "default", "name": "ChatGPT (Your subscription)",
        "typeLabel": "Subscription", "note": "Default provider",
        "removable": False, "editable": False,
        "help": 'No key needed. This uses your ChatGPT subscription. (More in Help)',
        "models": codex_status["models"], "any": codex_status["any"],
    }]
    for p in codex_providers:
        type_label = "Custom" if p["type"] == "custom" else "Built-in"
        entries.append({
            "id": p["id"], "name": p["name"],
            "typeLabel": type_label, "note": None,
            "removable": True, "editable": True,
            "type": p["type"], "preset": p.get("preset"),
            "baseUrl": p["base_url"], "hasKey": bool(p.get("api_key")),
            "help": f'You can use any model from this provider. Address: <code>{p["base_url"]}</code>.',
            "models": _codex_pinned_rows(p["id"]), "any": f"{CODEX_PREFIX}{p['id']}/MODEL",
        })
    return entries


def _remote_provider_entry() -> dict:
    """The 'Other devices' provider pill/panel (2026-09-30): the models that are ready right now on the
    player's other devices and AI servers (the real management UI is the Other devices tab)."""
    servers = remote_svc.all()
    rows = [[mid, name, "Sees pictures" if vision else ""] for mid, name, vision in remote_svc.online_model_names()]
    if rows:
        state, status = "ok", f"{sum(1 for r in servers if r.online)} online"
    elif servers:
        state, status = "warn", "Offline"
    else:
        state, status = "off", "No other devices yet"
    return {
        "id": "remote", "name": "Other devices", "state": state, "status": status,
        "keyless": True, "hasKey": False, "testable": bool(rows),
        "help": 'These are models running on your other devices. (More in Help)',
        "any": None, "live": True, "label": "Models on your other devices",
        "empty": "Nothing online yet. Add a device on the Other devices tab.",
        "models": rows,
    }


def _local_provider_entry() -> dict:
    """The 'Local' provider pill/panel (2026-09-30). Keyless; the real management UI is the separate
    Local models card, so this just lists what's downloaded and whether it's running."""
    st = local_svc.provider_status()
    return {
        "id": "local", "name": "Local device", "state": st["state"], "status": st["status"],
        "keyless": True, "hasKey": False, "testable": st["testable"],
        "help": 'Runs on this device. No subscription needed. Download and manage models on the Local device tab. (More in Help)',
        "any": None, "live": True, "label": "Your local models",
        "empty": "Nothing downloaded yet. Go to the Local device tab to get a model.",
        "models": st["models"],
    }


# The Providers tab's "Choose a provider" (2026-10-03): which group each provider sits in (by how you pay), its short pill name, and an optional quiet second name.
# A provider declares its place here; the page's drawing code reads only the `group` / `pill` / `tag` fields it is given, never a provider's name.
PICKER_GROUPS = (
    ("subscription", "Your subscriptions", "No key needed."),
    ("key", "With a key", "Pay as you go, or use your plan's key."),
    ("pc", "On your devices", "Free, runs at home."),
)
_PICKER_INFO = {
    "claude": ("subscription", "Claude", ""), "codex": ("subscription", "ChatGPT", "Codex"),
    "geminicli": ("subscription", "Google", "Antigravity"), "grok": ("subscription", "Grok", "SuperGrok"),
    "continuum": ("key", "Continuum", ""), "deepseek": ("key", "DeepSeek", ""), "gemini": ("key", "Gemini", ""), "glm": ("key", "GLM", "z.ai"),
    "nano": ("key", "Nano-GPT", ""), "openai": ("key", "OpenAI", ""), "typesafe": ("key", "TypeSafe", "Jev"), "xai": ("key", "xAI", ""), "perplexity": ("key", "Perplexity", "Decisions only"),
    "kilo": ("key", "Kilo Gateway", ""),
    "ollama": ("pc", "Ollama", ""), "remote": ("pc", "Other devices", ""), "lmstudio": ("pc", "LM Studio", ""), "local": ("pc", "Local device", ""),
}
_PICKER_INFO.update({_s.id: ("key", _s.pill, "free allowance") for _s in key_sources.SOURCES})


def _with_picker_info(entries: list) -> list:
    """Adds `group`, `pill` and `tag` to each provider (an unlisted one lands under "With a key" with its own name, so it still shows)."""
    for e in entries:
        group, pill, tag = _PICKER_INFO.get(e["id"], ("key", e.get("name", e["id"]), ""))
        e.update(group=group, pill=pill, tag=tag)
    return entries


_claude_version_cache: tuple = (None, None)   # (cache key, version text); see claude_trouble.version_cache_key


async def _claude_version_if_orange(state: str, reason: Optional[str]) -> Optional[str]:
    """`claude --version`, asked only while the Claude dot is orange with a reason, and remembered until the program file changes (never per request)."""
    global _claude_version_cache
    if state != "warn" or not reason or not CLAUDE_PATH:
        return None
    key = claude_trouble.version_cache_key(CLAUDE_PATH)
    if _claude_version_cache[0] != key:
        _claude_version_cache = (key, await asyncio.to_thread(claude_trouble.read_version, CLAUDE_PATH))
    return _claude_version_cache[1]


def _claude_version_now() -> Optional[str]:
    """The same remembered `claude --version` (blocking: Check everything runs it off the page's thread), asked whatever colour the dot is."""
    global _claude_version_cache
    key = claude_trouble.version_cache_key(CLAUDE_PATH)
    if _claude_version_cache[0] != key:
        _claude_version_cache = (key, claude_trouble.read_version(CLAUDE_PATH))
    return _claude_version_cache[1]


async def _dashboard_providers() -> list[dict]:
    """One entry per model provider this proxy supports, describing its CURRENT REAL state (never
    hardcoded ok/off) for the compact "Providers & Models" dashboard card -- design/dashboard-
    provider-dropdown-mockup.html is the approved source of truth for shape, copy, and behavior;
    this is that same PROVIDERS array, computed from live state instead of fake demo data.

    GitHub Copilot is deliberately absent -- see TECHNICAL.md's own "GitHub Copilot -- NOT
    implemented, and why" section; there's nothing real to report a status for.

    Every entry also carries `testable` (2026-09-13) -- whether Quick Test should offer this
    provider right now. See the long comment just below for the one exception (Claude while
    warming up) to the otherwise blanket `state == "ok"` rule.
    """
    # Claude is optional now -- mirror Codex's own off/warn/ok distinction (not installed vs.
    # installed-but-not-working vs. ready) instead of just ready/warming-up.
    #
    # `testable` (2026-09-13, Quick Test filtering) is the one server-side source of truth for
    # "can Quick Test actually get an answer from this provider right now" -- deciding it here,
    # once, instead of re-deriving it from `state` strings in JS, means a future provider/state
    # doesn't need matching logic duplicated in two languages. It's `state == "ok"` for every
    # provider EXCEPT Claude while it's still warming up (the very first few seconds after
    # startup): that's a `warn` state, but it's about to become ready, and hiding the default
    # model from Quick Test for those few seconds would be confusing rather than honest.
    if CLAUDE_PATH is None:
        claude_state, claude_status = "off", "Program not installed"
        claude_testable = False
        claude_help = ""
    elif auth.is_ready:
        claude_state, claude_status = "ok", "Ready"
        claude_testable = True
        claude_help = ""
    elif auth.last_error:
        claude_state, claude_status = "warn", "Not available"
        claude_testable = False
        claude_help = ""
    else:
        claude_state, claude_status = "warn", "Starting up…"
        claude_testable = True  # about to be ready -- see note above
        claude_help = ""

    ollama_status = await _ollama_status()
    lmstudio_status = await _lmstudio_status()
    gemini_status = await _gemini_status()
    agy_status = await _agy_status()
    grok_status = await _grok_status()
    xai_status = await _xai_status()
    continuum_status = await _continuum_status()
    ks_statuses = {s.id: await _ks_status(s) for s in key_sources.SOURCES}
    codex_status = await _codex_status()
    claude_models_status = await _claude_status()
    # What a request that names no model uses (this used to be a "Default model" box in the Setup header).
    claude_help = f"No key needed. A job with no model named uses {_claude_model_label(_default_claude_model())}. (More in Help)"

    def _key_provider(id_, name, key, endpoint, placeholder, help_html, models, any_=None, testable=None):
        # testable=None (default) keeps the normal "a key is saved" rule every other provider
        # uses. Jev passes testable=False explicitly (queue: let-jev-decisions-through, 2026-09-29)
        # -- it's a typed-question model, not a chat one, so Quick Test's own "send a chat message"
        # flow can never work with it regardless of whether a key is saved.
        return {
            "id": id_, "name": name,
            "state": "ok" if key else "off",
            "status": "Key saved" if key else "No key yet",
            "hasKey": bool(key),
            "testable": bool(key) if testable is None else testable,
            "placeholder": placeholder,
            "keyEndpoint": endpoint,
            "help": help_html,
            "any": any_,
            "models": models,
        }

    return _with_picker_info([
        {
            "id": "claude", "name": "Claude (Anthropic)", "state": claude_state, "status": claude_status,
            "keyless": True, "hasKey": False, "testable": claude_testable, "help": claude_help,
            "trouble": claude_trouble.trouble_line(claude_state, auth.last_error, await _claude_version_if_orange(claude_state, auth.last_error)),
            "any": None,
            "refreshEndpoint": "/config/claude-models",
            "live": True, "label": claude_models_status["label"],
            "models": claude_models_status["models"],
        },
        _key_provider(
            "glm", "GLM (z.ai)", glm_api_key, "/config/glm-key",
            "Paste your z.ai API key",
            'Get a key at <a href="https://z.ai/manage-apikey/apikey-list" target="_blank">z.ai</a>.',
            [[f"{GLM_PREFIX}glm-4.6", "GLM 4.6", "Standard GLM model"],
             [f"{GLM_PREFIX}glm-5.3", "GLM 5.3", "Top GLM model"]],
        ),
        _key_provider(
            "nano", "Nano-GPT", nano_api_key, "/config/nano-key",
            "Paste your Nano-GPT key (sk-nano-...)",
            'Get a key at <a href="https://nano-gpt.com/api" target="_blank">nano-gpt.com</a>.',
            [[f"{NANO_PREFIX}gpt-4o-mini", "GPT-4o mini", "Fast, cheap baseline"],
             [f"{NANO_PREFIX}qwen/qwen3.8-27b-uncensored", "Qwen3.8 27B (uncensored)", "Good for immersive NPC dialogue"]],
        ),
        _key_provider(
            "openai", "OpenAI", openai_api_key, "/config/openai-key",
            "Paste your OpenAI key (sk-proj-...)",
            'Get a key at <a href="https://platform.openai.com/api-keys" target="_blank">platform.openai.com</a>.',
            [[f"{OPENAI_PREFIX}gpt-4o-mini", "GPT-4o mini", "Fast, cheap baseline"],
             [f"{OPENAI_PREFIX}gpt-5.5", "GPT-5.5", "Current flagship"]],
        ),
        _key_provider(
            "deepseek", "DeepSeek", deepseek_api_key, "/config/deepseek-key",
            "Paste your DeepSeek key (sk-...)",
            'Get a key at <a href="https://platform.deepseek.com/api_keys" target="_blank">platform.deepseek.com</a>.',
            [[f"{DEEPSEEK_PREFIX}deepseek-flash", "DeepSeek Flash", "Fast, cheap baseline"],
             [f"{DEEPSEEK_PREFIX}deepseek-v4-pro", "DeepSeek V4 Pro", "Flagship, reasoning-heavy"]],
        ),
        _key_provider(
            "typesafe", "TypeSafe (Jev)", typesafe_api_key, "/config/typesafe-key",
            "Paste your TypeSafe API key",
            'TypeSafe answers quick decisions like an NPC\'s mood or who speaks next. It does not write dialogue. '
            'Get a key at <a href="https://typesafe.ai" target="_blank">typesafe.ai</a>.',
            [[f"{TYPESAFE_PREFIX}jev-latest", "Jev (latest)", "Newest version"],
             [f"{TYPESAFE_PREFIX}jev-1.13", "Jev 1.13", "Specific version"],
             [f"{TYPESAFE_PREFIX}jev-preview", "Jev (preview)", "Early access version"]],
            testable=False,
        ),
        _key_provider(
            "perplexity", "Perplexity (Decisions)", perplexity_api_key, "/config/perplexity-key",
            "Paste your Perplexity API key (pplx-...)",
            'Perplexity answers quick decisions like an NPC\'s mood or who speaks next, for a tiny fraction of a cent each. It does not write dialogue. '
            'Get a key at <a href="https://console.perplexity.ai" target="_blank">console.perplexity.ai</a>.',
            [], testable=False,
        ),
        _key_provider(
            "kilo", "Kilo Gateway", kilo_api_key, "/config/kilo-key",
            "Paste your Kilo Gateway key",
            'Get a key at <a href="https://kilo.ai" target="_blank">kilo.ai</a>.',
            [], any_=f"{KILO_PREFIX}provider/model",
        ),
        {
            "id": "gemini", "name": "Gemini (API key)",
            # State/status/models/testable/any/empty all come from _gemini_status() -- a live
            # /models call once a key's saved (2026-09-15), same "real state, never hardcoded"
            # rule Ollama's own entry below already follows.
            "state": gemini_status["state"], "status": gemini_status["status"],
            "hasKey": bool(gemini_api_key),
            "testable": gemini_status["testable"],
            "placeholder": "Paste your Gemini API key",
            "keyEndpoint": "/config/gemini-key",
            "refreshEndpoint": "/config/gemini-models",
            "help": 'Get a key at <a href="https://aistudio.google.com/apikey" target="_blank">aistudio.google.com</a>. '
                    'The free tier has strict limits on how fast it can reply.',
            "any": gemini_status["any"],
            "live": True,
            "label": gemini_status["label"],
            "empty": gemini_status["empty"],
            "models": gemini_status["models"],
        },
        {
            "id": "xai", "name": "xAI (API key)",
            # Everything below the identity comes from _xai_status(): a live /models call once a key is saved (same rule as Gemini's entry).
            "state": xai_status["state"], "status": xai_status["status"],
            "hasKey": bool(xai_api_key),
            "testable": xai_status["testable"],
            "placeholder": "Paste your xAI API key",
            "keyPrefix": "xai-", "keyKindName": "an xAI key",       # a key that doesn't start like this gets a gentle "Save anyway?" before it is saved
            "keyEndpoint": "/config/xai-key",
            "refreshEndpoint": "/config/xai-models",
            "help": 'Get a key at <a href="https://console.x.ai" target="_blank">console.x.ai</a>. (More in Help)',
            "any": xai_status["any"],
            "live": True,
            "label": xai_status["label"],
            "empty": xai_status["empty"],
            "models": xai_status["models"],
        },
        {
            "id": "continuum", "name": "Continuum (plan key)",
            # Everything below the identity comes from _continuum_status(): a live /v1/models call once a key is saved.
            "state": continuum_status["state"], "status": continuum_status["status"],
            "hasKey": bool(continuum_api_key),
            "testable": continuum_status["testable"],
            "placeholder": "Paste your Continuum key (cont_sk_…)",
            "keyPrefix": CONTINUUM_KEY_START, "keyKindName": "a Continuum key",
            "keyEndpoint": "/config/continuum-key",
            "refreshEndpoint": "/config/continuum-models",
            "help": 'Make a key in your <a href="https://continuumcode.ai" target="_blank">Continuum</a> account, under Settings. '
                    'Replies count against your plan\'s weekly budget. (More in Help)',
            "any": continuum_status["any"],
            "live": True,
            "label": continuum_status["label"],
            "empty": continuum_status["empty"],
            "models": continuum_status["models"],
        },
        *[_ks_entry(s, ks_statuses[s.id]) for s in key_sources.SOURCES],
        {
            "id": "ollama", "name": "Ollama",
            # State/status/models/testable/any/empty all come from _ollama_status() -- the real
            # daemon/Cloud check (2026-09-13), replacing an always-"ok" placeholder that offered an
            # example model (llama3.1:8b) nobody actually had installed.
            "state": ollama_status["state"], "status": ollama_status["status"],
            "hasKey": bool(ollama_api_key),
            "testable": ollama_status["testable"],
            "placeholder": "Ollama Cloud key (leave blank to use this device)",
            "keyEndpoint": "/config/ollama-key",
            "refreshEndpoint": "/config/ollama-models",
            "help": 'MultiProxy talks to the Ollama app on this device. You do not need a key unless you want to use '
                    '<a href="https://ollama.com/cloud" target="_blank">Ollama Cloud</a>.',
            "any": ollama_status["any"],
            "live": True,
            "label": ollama_status["label"],
            "empty": ollama_status["empty"],
            "models": ollama_status["models"], "chatModels": ollama_status.get("chat"), "kinds": ollama_status.get("kinds") or {},
        },
        {
            "id": "lmstudio", "name": "LM Studio",
            # Everything below the identity comes from _lmstudio_status() (like Ollama's entry): the server's real state and
            # its real model list; `address` / `canStart` drive the two small extras on its panel (lmstudio_ui.py).
            "state": lmstudio_status["state"], "status": lmstudio_status["status"],
            "keyless": True, "hasKey": False,
            "testable": lmstudio_status["testable"],
            "refreshEndpoint": "/config/lmstudio-models",
            "help": 'Connects to LM Studio on this device. No subscription needed.',
            "any": lmstudio_status["any"],
            "live": True,
            "label": lmstudio_status["label"],
            "empty": lmstudio_status["empty"],
            "models": lmstudio_status["models"], "kinds": lmstudio_status.get("kinds") or {},
            "address": lmstudio_status["address"], "defaultAddress": lmstudio_status["defaultAddress"],
            "addressEndpoint": lmstudio_status["addressEndpoint"], "startEndpoint": lmstudio_status["startEndpoint"],
            "canStart": lmstudio_status["canStart"],
        },
        _local_provider_entry(),
        _remote_provider_entry(),
        {
            "id": "codex", "name": "Codex (ChatGPT subscription)",
            # State/status/models/testable/any/empty all come from _codex_status() -- the account's
            # real, current model list read from the CLI's own on-disk cache (2026-09-16), same
            # "real state, never hardcoded" rule Ollama's/Gemini's/Antigravity's own entries follow.
            "state": codex_status["state"], "status": codex_status["status"],
            "keyless": True, "hasKey": False, "testable": codex_status["testable"],
            "refreshEndpoint": "/config/codex-models",
            "help": 'No key needed. This uses your ChatGPT subscription. (More in Help)',
            "any": codex_status["any"],
            "live": True,
            "label": codex_status["label"],
            "empty": codex_status["empty"],
            "installCard": codex_status.get("installCard"),
            "models": codex_status["models"],
            "codexProviders": _codex_provider_dashboard_entries(codex_status),
        },
        {
            "id": "geminicli", "name": "Google Antigravity (subscription)",
            # State/status/models/testable/any/empty all come from _agy_status() -- a live
            # `agy models` call (2026-09-15), same "real state, never hardcoded" rule Ollama's own
            # entry above already follows. Keyless like Claude/Codex above -- no key row.
            "state": agy_status["state"], "status": agy_status["status"],
            "keyless": True, "hasKey": False, "testable": agy_status["testable"],
            "refreshEndpoint": "/config/geminicli-models",
            "help": 'No key needed. This uses your Google subscription. (More in Help)',
            "any": agy_status["any"],
            "live": True,
            "label": agy_status["label"],
            "empty": agy_status["empty"],
            "models": agy_status["models"],
        },
        {
            "id": "grok", "name": "Grok (SuperGrok)",
            # Everything below the identity comes from _grok_status(): a real `grok models` call. Keyless like Claude/Codex/Antigravity.
            "state": grok_status["state"], "status": grok_status["status"],
            "keyless": True, "hasKey": False, "testable": grok_status["testable"],
            "refreshEndpoint": "/config/grok-models",
            "help": 'No key needed. This uses your SuperGrok subscription. (More in Help)',
            "any": grok_status["any"],
            "live": True,
            "label": grok_status["label"],
            "empty": grok_status["empty"],
            "models": grok_status["models"],
        },
    ])


_SPEEDS_V2_JS = r"""// ---------------- Speeds tab ----------------
// Speeds v2 (2026-09-27), built to design/dashboard-speeds-v2-mockup.html (Revision 2): health cards,
// "starts talking" time, reliability labels, By role as tabs. Plain JS spliced in as one string, so
// none of it needs Python brace-escaping.

const SPEED_CATEGORY_PRIORITY = ['dialogue', 'gamemaster', 'vision', 'background'];

// ---- two remembered choices (browser localStorage; a blocked store just falls back to the defaults) ----
function prefGet(key, fallback) {
  try {
    const v = window.localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (e) { return fallback; }
}
function prefSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch (e) {}
}
let speedUnit = prefGet('speeds.unit', 'words') === 'tokens' ? 'tokens' : 'words';       // words/s by default
let healthShow = prefGet('speeds.healthShow', 'hour') === 'ready' ? 'ready' : 'hour';     // last hour by default

let speedsTabLoaded = false;
let currentThresholds = null;
let currentStartsThresholds = null;
let lastSpeedModels = [];
let currentSpeedRange = 'today';
let currentRole = null;        // the By-role tab; null until roles load, then Dialogue if it exists
let byRoleLoaded = false;

const VIA_NAME = { 'Codex': 'ChatGPT / Codex' };
const RANGE_CALLS_TEXT = { '7d': 'the last 7 days', '30d': 'the last 30 days' };

const ROLE_EXPLAIN = {
  'Dialogue': "The wait you feel before an NPC speaks.",
  'GameMaster': "Runs in the background.",
  'Memory': "Runs in the background.",
  'Vision': "Runs in the background.",
  'Diary Generation': "Runs in the background.",
  'Character Profiles': "Runs in the background.",
  'Universal Translator': "Runs in the background.",
  'Action Evaluation': "Runs in the background.",
  'Combat': "Needs to be instant.",
  'Meta': "Runs in the background.",
  'AI Assistant': "The wait for your answer.",
  'Decisions': "Needs to be instant.",
};
const START_NOTE = {
  whole_reply: 'Sends the whole reply at once',
  not_measured: 'Not measured yet',
  no_data: 'No data yet',
};
const ROLE_EXPLAIN_FALLBACK = "Handles miscellaneous background tasks for SkyrimNet. For non-dialogue jobs like this, the <b>full reply time</b> is what counts.";
const ROLE_TAB_TEXT = { 'GameMaster': 'Game Master' };

function fmtSecs(v) { return v == null ? '—' : (v.toFixed(1) + 's'); }
function fmtPct(v) { return v == null ? '—' : (v + '%'); }
function levelWord(level) { return { ok: 'ok', warn: 'warn', bad: 'bad' }[level] || 'none'; }

function primaryCategory(roles) {
  if (!roles || !roles.length) return 'background';
  const cats = new Set(roles.map(r => r.category));
  for (const cat of SPEED_CATEGORY_PRIORITY) {
    if (cats.has(cat)) return cat;
  }
  return 'background';
}
// The name of the job a model mainly does, for lines like "Quick for Dialogue".
function primaryRoleLabel(roles, cat) {
  const hit = (roles || []).find(r => r.category === cat);
  if (hit) return ROLE_TAB_TEXT[hit.label] || hit.label;   // "GameMaster" reads as "Game Master" in a sentence
  return cat === 'background' ? 'background work' : cat;
}
function verdictLevel(value, bounds) {
  if (value == null || !bounds) return null;
  if (value <= bounds.good) return 'ok';
  if (value >= bounds.slow) return 'bad';
  return 'warn';
}
function shortVerdict(level, cat, label) {
  if (level === 'ok') return (cat === 'dialogue' ? 'Quick for ' : 'Fine for ') + label;
  if (level === 'warn') return 'A bit slow for ' + label;
  return 'Slow for ' + label;
}
function reliabilityText(rel) {
  if (!rel || rel.pct == null) return '';
  const word = rel.level === 'ok' ? 'Reliable' : rel.level === 'warn' ? 'Some errors' : 'Unreliable';
  return word + ' · ' + rel.pct + '%';
}

// ---- the "where" filter (2026-10-01): All devices and services / This device / each other device / Cloud services ----
let speedPc = 'all';          // remembered while the page stays open
function pcParam() { return '&pc=' + encodeURIComponent(speedPc); }
async function fetchSpeedPcs() {
  let data;
  try { data = await (await fetch('/stats/speeds/pcs')).json(); } catch (e) { return; }
  const box = $('speedPcBox');
  if (!data.show) { speedPc = 'all'; box.style.display = 'none'; return; }       // nothing but cloud: no filter
  if (!data.options.some(o => o.id === speedPc)) speedPc = 'all';
  const sel = $('speedPc');
  // Rebuilt only when the list of devices really changes, so an open dropdown (or one that kept focus after a pick) is never disturbed.
  const sig = data.options.map(o => o.id + '|' + o.label).join(';');
  if (sel.dataset.sig !== sig) {
    sel.innerHTML = data.options.map(o => `<option value="${_escapeHtml(o.id)}">${_escapeHtml(o.label)}</option>`).join('');
    sel.dataset.sig = sig;
  }
  sel.value = speedPc;
  box.style.display = '';
}
function setSpeedPc(v) {
  speedPc = v;
  fetchHealth();
  fetchSpeeds();
  if (byRoleLoaded) fetchByRole();
  if ($('speedsTimeView') && $('speedsTimeView').style.display !== 'none') fetchTime();
}
function whereTag(m) { return m.where ? `<span class="where-tag">${_escapeHtml(m.where)}</span>` : ''; }

// ---- health cards ----
function setHealthShow(v) {
  healthShow = v === 'ready' ? 'ready' : 'hour';
  prefSet('speeds.healthShow', healthShow);
  fetchHealth();
}
async function fetchHealth() {
  let data;
  try {
    const res = await fetch('/stats/speeds/health?show=' + encodeURIComponent(healthShow) + pcParam());
    data = await res.json();
  } catch (e) { return; }
  renderHealth(data.cards || []);
}
function renderHealth(cards) {
  $('healthGrid').innerHTML = cards.map(c => `
    <div class="hcard">
      <div class="name">${_escapeHtml(c.provider)}${c.model ? ' ' + _escapeHtml(modelDisplayName(c.model)) : ''}</div>
      <div class="hverdict lv-${levelWord(c.level)}"><span class="hdot"></span>${_escapeHtml(c.title)}</div>
      <div class="why">${_escapeHtml(c.why)}</div>
      ${(c.detail || []).map(d => `<div class="why">${_escapeHtml(d)}</div>`).join('')}
    </div>`).join('');
  $('healthEmpty').style.display = cards.length ? 'none' : 'block';
  $('healthEmpty').textContent = healthShow === 'ready'
    ? 'No providers are ready yet. Go to the Providers tab to add one.'
    : 'No AI replies in the last hour. Talk to an NPC, then check back.';
}

// ---- unit switch ----
function setSpeedUnit(u) {
  speedUnit = u === 'tokens' ? 'tokens' : 'words';
  prefSet('speeds.unit', speedUnit);
  applyUnitToggle();
  renderSpeedRows(lastSpeedModels);
  if ($('speedsTimeView') && $('speedsTimeView').style.display !== 'none') fetchTime();
}
function applyUnitToggle() {
  $('unitWords').className = 'view-opt' + (speedUnit === 'words' ? ' active' : '');
  $('unitTokens').className = 'view-opt' + (speedUnit === 'tokens' ? ' active' : '');
}
function applyHealthShowSelect() { $('healthShow').value = healthShow; }

function selectSpeedRange(r) {
  currentSpeedRange = r;
  document.querySelectorAll('#rangePicker .range-opt').forEach(o => o.classList.toggle('active', o.dataset.r === r));
  fetchSpeeds();
  if (byRoleLoaded) fetchByRole();
  if ($('speedsTimeView') && $('speedsTimeView').style.display !== 'none') fetchTime();
}

function showMainTab(which) {
  for (const [id, key] of [['tabOverview', 'overview'], ['tabSetup', 'setup'], ['tabLocal', 'local'], ['tabQuick', 'quick'], ['tabOther', 'other'], ['tabVoice', 'voice'], ['tabDecisions', 'decisions'], ['tabModels', 'models'], ['tabRouting', 'routing'], ['tabSpeeds', 'speeds'], ['tabHelp', 'help']]) {
    if ($(id)) $(id).className = 'tab' + (which === key ? ' active' : '');
  }
  for (const [id, key] of [['paneOverview', 'overview'], ['paneSetup', 'setup'], ['paneLocal', 'local'], ['paneQuick', 'quick'], ['paneOther', 'other'], ['paneVoice', 'voice'], ['paneDecisions', 'decisions'], ['paneModels', 'models'], ['paneRouting', 'routing'], ['paneSpeeds', 'speeds'], ['paneHelp', 'help'], ['paneSettings', 'settings'], ['paneStart', 'start']]) {
    if ($(id)) $(id).style.display = which === key ? 'block' : 'none';
  }
  if ($('tabCog')) $('tabCog').className = 'tab-cog' + (which === 'settings' ? ' active' : '');
  if (which === 'settings') stLoad();
  if (which === 'overview') ovOpened(); else ovClosed();
  if (which === 'other') opOpened();
  if (which === 'voice') vcOpened();
  if (which === 'routing') rtOpened();
  if (which === 'quick') dqOpened();
  if (which === 'quick') ckOpened();
  if (which !== 'routing' && typeof rtClosed === 'function') rtClosed();
  if (which === 'models' && typeof mdOpened === 'function') mdOpened();
  if (which !== 'models' && typeof mdClosed === 'function') mdClosed();
  if (which === 'decisions' && typeof dcOpened === 'function') dcOpened();
  if (which !== 'decisions' && typeof dcClosed === 'function') dcClosed();
  if (which !== 'voice' && typeof vcClosed === 'function') vcClosed();
  if (which === 'help') helpOpened();
  if (which === 'local' && typeof lmRefresh === 'function') {
    lmOpened();
    if (typeof memStart === 'function') memStart();
  }
  if (which === 'speeds' && !speedsTabLoaded) {
    speedsTabLoaded = true;
    applyUnitToggle();
    applyHealthShowSelect();
    detectSkyrimPath();
  }
  speedsTimerSync();       // the first round (and every later one) comes from here: only while this tab is the one on screen
}

// The Speeds page's 5 s refresh (2026-10-02). It used to start once and never stop, ask again before the last round had come back and keep
// asking on a hidden tab; with a long history that drowned the server. Now: a round only runs while the Speeds tab is the visible tab of
// a visible browser tab, never two at once, and coming back to the tab refreshes at once.
let speedsTimer = null;
let speedsRoundRunning = false;
function speedsShown() {
  const pane = $('paneSpeeds');
  return speedsTabLoaded && !document.hidden && !!pane && pane.style.display === 'block';
}
async function speedsRound() {
  if (speedsRoundRunning || !speedsShown()) return;
  speedsRoundRunning = true;
  try {
    const jobs = [fetchSpeedPcs(), fetchSpeeds(), fetchHealth()];
    if (byRoleLoaded && $('speedsByRoleView').style.display !== 'none') jobs.push(fetchByRole());
    if ($('speedsTimeView') && $('speedsTimeView').style.display !== 'none') jobs.push(fetchTime());
    if ($('speedsLocalView') && $('speedsLocalView').style.display !== 'none') jobs.push(fetchLocalSpeeds());
    await Promise.allSettled(jobs);
  } finally { speedsRoundRunning = false; }
}
function speedsTimerSync() {
  if (speedsShown()) {
    if (!speedsTimer) { speedsTimer = setInterval(speedsRound, 5000); speedsRound(); }
  } else if (speedsTimer) {
    clearInterval(speedsTimer); speedsTimer = null;
  }
}
try { document.addEventListener('visibilitychange', speedsTimerSync); } catch (e) {}

function showSpeedsView(which) {
  $('optSpeedsByModel').className = 'view-opt' + (which === 'model' ? ' active' : '');
  $('optSpeedsByRole').className = 'view-opt' + (which === 'role' ? ' active' : '');
  if ($('optSpeedsTime')) $('optSpeedsTime').className = 'view-opt' + (which === 'time' ? ' active' : '');
  if ($('optSpeedsLocal')) $('optSpeedsLocal').className = 'view-opt' + (which === 'local' ? ' active' : '');
  $('speedsByModelView').style.display = which === 'model' ? 'block' : 'none';
  $('speedsByRoleView').style.display = which === 'role' ? 'block' : 'none';
  if ($('speedsTimeView')) $('speedsTimeView').style.display = which === 'time' ? 'block' : 'none';
  if ($('speedsLocalView')) $('speedsLocalView').style.display = which === 'local' ? 'block' : 'none';
  for (const id of ['rangePicker', 'unitToggle']) if ($(id)) $(id).style.display = which === 'size' ? 'none' : '';       // Prompt size has its own range
  if ($('optSpeedsSize')) $('optSpeedsSize').className = 'view-opt' + (which === 'size' ? ' active' : '');
  if ($('speedsSizeView')) $('speedsSizeView').style.display = which === 'size' ? 'block' : 'none';
  if (which === 'time') sttOpened();
  if (which === 'local') slOpened();
  if (which === 'size') pszOpened();
  if (which === 'role' && !byRoleLoaded) {
    byRoleLoaded = true;
    fetchSpeedRoles().then(fetchByRole);
  }
}

// ---- By role ----
// Dialogue first (director: "it's the one front and center"), the rest as the server lists them.
function orderRoles(roles) {
  const rest = roles.filter(r => r !== 'Dialogue');
  return roles.includes('Dialogue') ? ['Dialogue', ...rest] : rest;
}
async function fetchSpeedRoles() {
  let data = { roles: [] };
  try {
    const res = await fetch('/stats/speeds/roles');
    data = await res.json();
  } catch (e) {}
  const roles = orderRoles(data.roles || []);
  if (!currentRole || !roles.includes(currentRole)) currentRole = roles.length ? roles[0] : null;
  $('roleTabs').innerHTML = roles.map(r =>
    `<span class="rtab${r === currentRole ? ' active' : ''}" data-role="${_escapeHtml(r)}" onclick="selectRole(this.dataset.role)">${_escapeHtml(ROLE_TAB_TEXT[r] || r)}</span>`
  ).join('');
}
function selectRole(role) {
  currentRole = role;
  document.querySelectorAll('#roleTabs .rtab').forEach(t => t.classList.toggle('active', t.dataset.role === role));
  fetchByRole();
}
function renderByRoleRow(role, m) {
  const first = role === 'Dialogue';
  const nowBadge = m.currently_serving ? '<span class="now-badge">Current</span>' : '';
  const rel = m.reliability && m.reliability.pct != null
    ? `<span class="hverdict lv-${levelWord(m.reliability.level)}"><span class="hdot"></span>${_escapeHtml(reliabilityText(m.reliability).replace(' · ', ' '))}</span>` : '—';
  const startsCell = m.first_token != null ? fmtSecs(m.first_token) : '—';
  return `<tr class="${m.currently_serving ? 'current' : ''}">
    <td class="mono">${_escapeHtml(m.model)}${whereTag(m)}${nowBadge}</td>
    <td><div class="share"><div class="sbar" style="width:${Math.max(4, (m.share || 0) * 0.8)}px"></div>${m.share == null ? '—' : m.share + '%'}</div></td>
    <td>${rel}</td>
    <td class="${first ? 'hotcell' : ''}">${startsCell}</td>
    <td class="${first ? '' : 'hotcell'}">${fmtSecs(m.full_typical)}</td>
  </tr>`;
}
async function fetchByRole() {
  if (!currentRole) {
    $('roleRows').innerHTML = '';
    $('roleExplain').style.display = 'none';
    $('roleTable').style.display = 'none';
    $('byRoleEmpty').textContent = 'No roles seen yet -- link your Skyrim install and let a few AI calls happen first.';
    $('byRoleEmpty').style.display = 'block';
    return;
  }
  const role = currentRole;
  let data = { models: [] };
  try {
    const res = await fetch('/stats/speeds/by-role?role=' + encodeURIComponent(role) + '&range=' + encodeURIComponent(currentSpeedRange) + pcParam());
    data = await res.json();
  } catch (e) {}
  if (role !== currentRole) return;   // the player switched tabs while this was loading
  const models = data.models || [];
  const first = role === 'Dialogue';
  $('roleExplain').innerHTML = ROLE_EXPLAIN[role] || ROLE_EXPLAIN_FALLBACK;
  $('roleExplain').style.display = 'block';
  $('thFirst').className = first ? 'hotcell' : '';
  $('thFull').className = first ? '' : 'hotcell';
  $('roleTable').style.display = models.length ? 'table' : 'none';
  $('roleRows').innerHTML = models.map(m => renderByRoleRow(role, m)).join('');
  $('byRoleEmpty').textContent = `No history yet for ${ROLE_TAB_TEXT[role] || role} in this range.`;
  $('byRoleEmpty').style.display = models.length ? 'none' : 'block';
}

// ---- By model ----
async function fetchSpeeds() {
  let data;
  try {
    const res = await fetch('/stats/speeds?range=' + encodeURIComponent(currentSpeedRange) + pcParam());
    data = await res.json();
  } catch (e) {
    return;
  }
  currentThresholds = data.thresholds;
  currentStartsThresholds = data.starts_talking_thresholds;
  fillThreshInputs(data.thresholds, data.starts_talking_thresholds);
  renderLinkState(data.linked, data.skyrim_install_path, data.skyrimnet_found);
  renderSpeedRows(data.models);
  renderOwnTime(data.ownTime);
}

// "MultiProxy's own time: median 3.2 ms": how much time MultiProxy itself adds to a reply (2026-10-02). `ownTime` is {medianMs, requests}
// from /stats/speeds for the chosen range and device; an older server sends none, and a range with no measured request sends medianMs null:
// either way the line stays hidden. One decimal below 10 ms, whole numbers from there up.
function ownTimeText(ownTime) {
  if (!ownTime || typeof ownTime.medianMs !== 'number' || !isFinite(ownTime.medianMs)) return '';
  const ms = ownTime.medianMs;
  const tenth = Math.round(ms * 10) / 10;
  return "MultiProxy's own time: median " + (tenth < 10 ? tenth.toFixed(1) : String(Math.round(ms))) + ' ms';
}
function renderOwnTime(ownTime) {
  const el = $('ownTimeLine');
  if (!el) return;
  const text = ownTimeText(ownTime);
  el.textContent = text;
  el.style.display = text ? 'block' : 'none';
}

// skyrimnetFound (2026-09-22): undefined/null when not linked (nothing to warn about) or true --
// only false shows the warning line. A real Mod Organizer 2 report found "Linked" could still be
// green even though SkyrimNet's own files live somewhere the direct path join can never reach --
// this third state makes that visible right on the card instead of only showing up later as
// empty role chips.
function renderLinkState(linked, path, skyrimnetFound) {
  // Read-only note about SkyrimNet's own usage-sharing setting -- shown whenever an install is linked.
  // There is deliberately NO switch here: SkyrimNet asks the player for consent itself.
  $('linkCard').style.display = linked ? 'none' : 'block';
  // Found = silence: the card only exists while the folder is linked but SkyrimNet cannot be found in it.
  $('linkedCard').style.display = (linked && skyrimnetFound === false) ? 'block' : 'none';
}

async function detectSkyrimPath() {
  $('detectSearching').style.display = 'flex';
  $('detectSearching').innerHTML = '<span>&#128269; Looking for your Skyrim install via Steam&hellip;</span>';
  $('manualRow').style.display = 'none';
  let data = { linked: false };
  try {
    const res = await fetch('/config/skyrim-path/detect');
    data = await res.json();
  } catch (e) {}
  if (data.linked) {
    renderLinkState(true, data.path, data.skyrimnet_found);
  } else {
    $('detectSearching').innerHTML = "<span>Couldn't find it via Steam — type the path yourself:</span>";
    $('manualRow').style.display = 'flex';
  }
}

async function linkSkyrimPath() {
  const path = $('pathInput').value.trim();
  if (!path) return;
  let data = { linked: false };
  try {
    const res = await fetch('/config/skyrim-path', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: path }),
    });
    data = await res.json();
  } catch (e) {}
  if (data.linked) {
    renderLinkState(true, data.path, data.skyrimnet_found);
    fetchSpeeds();
  }
}

async function unlinkSkyrimPath() {
  try {
    await fetch('/config/skyrim-path', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: '' }),
    });
  } catch (e) {}
  renderLinkState(false, null, null);
  $('pathInput').value = '';
  $('detectSearching').style.display = 'none';
  $('manualRow').style.display = 'flex';
}

function toggleThresh() {
  const p = $('threshPanel');
  const opening = p.style.display !== 'block';
  p.style.display = opening ? 'block' : 'none';
  $('threshSaved').style.display = 'none';
  $('threshError').style.display = 'none';
  if (opening && currentThresholds) fillThreshInputs(currentThresholds, currentStartsThresholds);
}

function fillThreshInputs(thresholds, starts) {
  if (!thresholds) return;
  for (const cat of SPEED_CATEGORY_PRIORITY) {
    const pairs = [['th-', thresholds[cat]], ['st-', starts ? starts[cat] : null]];
    for (const [prefix, bounds] of pairs) {
      if (!bounds) continue;
      const goodEl = $(prefix + cat + '-good');
      const slowEl = $(prefix + cat + '-slow');
      if (goodEl && document.activeElement !== goodEl) goodEl.value = bounds.good;
      if (slowEl && document.activeElement !== slowEl) slowEl.value = bounds.slow;
    }
  }
}

async function saveThresh() {
  const payload = { starts_talking: {} };
  for (const cat of SPEED_CATEGORY_PRIORITY) {
    payload[cat] = { good: parseFloat($('th-' + cat + '-good').value), slow: parseFloat($('th-' + cat + '-slow').value) };
    payload.starts_talking[cat] = { good: parseFloat($('st-' + cat + '-good').value), slow: parseFloat($('st-' + cat + '-slow').value) };
  }
  $('threshError').style.display = 'none';
  try {
    const res = await fetch('/config/speed-thresholds', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      $('threshError').textContent = (data.error && data.error.message) || 'Could not save.';
      $('threshError').style.display = 'inline-flex';
      return;
    }
    currentThresholds = data.thresholds;
    currentStartsThresholds = data.starts_talking_thresholds;
    $('threshSaved').style.display = 'inline-flex';
    renderSpeedRows(lastSpeedModels);
  } catch (e) {
    $('threshError').textContent = 'Could not save.';
    $('threshError').style.display = 'inline-flex';
  }
}

function renderSpeedRows(models) {
  lastSpeedModels = models || [];
  const hasAny = lastSpeedModels.length > 0;
  $('trueEmptyCard').style.display = hasAny ? 'none' : 'block';
  $('speedRows').style.display = hasAny ? 'block' : 'none';
  if (!hasAny) { $('speedRows').innerHTML = ''; return; }
  $('speedRows').innerHTML = lastSpeedModels.map(renderSpeedRow).join('');
}

function renderRangeBar(full) {
  if (!full || full.typical == null) return '';
  const axis = Math.max(full.slowest * 1.1, 0.1);
  const left = (full.fastest / axis) * 100;
  const width = Math.max(((full.slowest - full.fastest) / axis) * 100, 2);
  const mid = (full.typical / axis) * 100;
  return `
    <div class="label sv-gap">Full reply, fastest to slowest</div>
    <div class="range"><div class="span" style="left:${left.toFixed(1)}%;width:${width.toFixed(1)}%"></div><div class="mid" style="left:${mid.toFixed(1)}%"></div></div>
    <div class="range-l"><span>${fmtSecs(full.fastest)} fastest</span><span>typical ${fmtSecs(full.typical)}</span><span>${fmtSecs(full.slowest)} slowest</span></div>`;
}

function renderStrip(strip) {
  if (!strip || !strip.length) return '';
  const maxE = Math.max(...strip.filter(s => s.ok).map(s => s.e), 0.1);
  const bars = strip.map(s => s.ok
    ? `<i style="height:${Math.max(12, Math.round((s.e / maxE) * 100))}%" title="${s.e.toFixed(1)}s"></i>`
    : `<i class="err" style="height:100%" title="Failed"></i>`).join('');
  return `<div class="label sv-gap">Last ${strip.length === 40 ? 40 : strip.length} repl${strip.length === 1 ? 'y' : 'ies'}</div><div class="strip">${bars}</div>`;
}

// The model card's head shows the model's plain name (from the provider lists) when there is one, else the id it was called by.
function modelDisplayName(id) {
  if (typeof PROVIDERS === 'undefined') return id;
  for (const p of PROVIDERS) for (const row of (p.models || [])) if (row[0] === id || String(row[0]).endsWith('/' + id)) return row[1] || id;
  return id;
}

function renderSpeedRow(m) {
  const v2 = m.v2 || {};
  const roles = m.roles || [];
  const failing = v2.reliability && v2.reliability.pct != null && v2.reliability.level && v2.reliability.level !== 'ok';      // some of its requests failed: its job tags carry a dot
  const chipsHtml = roles.map(r => `<span class="role ${r.class}">${_escapeHtml(r.label)}${failing ? ` <span class="hdot lv-${levelWord(v2.reliability.level)}" title="Some of its requests failed"></span>` : ''}</span>`).join('');
  const cat = primaryCategory(roles);
  const label = primaryRoleLabel(roles, cat);
  const fullBounds = currentThresholds ? currentThresholds[cat] : null;
  const startBounds = currentStartsThresholds ? currentStartsThresholds[cat] : null;

  // Starts talking in
  let startsN = '—', startsV = '';
  if (v2.first_token != null) {
    startsN = fmtSecs(v2.first_token);
    const lvl = verdictLevel(v2.first_token, startBounds);
    if (lvl) startsV = `<div class="v lv-${lvl}">${shortVerdict(lvl, cat, label)}</div>`;
  } else {
    // Only claim "whole reply at once" when the proxy KNOWS it (a call saved with the new fields that
    // came in one piece). Older history simply predates the measurement.
    const note = v2.first_token_note || 'not_measured';
    startsV = `<div class="v muted">${START_NOTE[note] || START_NOTE.not_measured}</div>`;
  }
  // Full reply in: the typical (median) SUCCESSFUL reply -- the same number as "typical" on the bar
  // below, so the card never shows two different "full reply" times. No successes -> no number.
  const typicalFull = v2.full ? v2.full.typical : null;
  const fullLvl = verdictLevel(typicalFull, fullBounds);
  const fullV = fullLvl ? `<div class="v lv-${fullLvl}">${shortVerdict(fullLvl, cat, label)}</div>` : '';
  // Talks at
  let talksN = '—', talksV = '';
  if (v2.speed) {
    const val = speedUnit === 'words' ? v2.speed.words : v2.speed.tokens;
    if (val != null) talksN = (v2.speed.estimated ? '≈ ' : '') + Math.round(val) + ` <small>${speedUnit === 'words' ? 'words/s' : 'tokens/s'}</small>`;
  } else if (v2.speed_note === 'not_measured' && (v2.reliability && v2.reliability.pct)) {
    talksV = `<div class="v muted">${START_NOTE.not_measured}</div>`;
  }
  const via = v2.provider ? `<div class="via">via ${_escapeHtml(VIA_NAME[v2.provider] || v2.provider)}</div>` : '';
  const rel = v2.reliability && v2.reliability.pct != null
    ? `<span class="rel lv-${levelWord(v2.reliability.level)}"><span class="hdot"></span>${_escapeHtml(reliabilityText(v2.reliability))}</span>` : '';
  const errLine = v2.failure_line ? `<div class="errline">${_escapeHtml(v2.failure_line)}</div>` : '';

  let callsLine;
  if (currentSpeedRange === 'today') {
    callsLine = m.calls_last_hour
      ? `${m.calls_last_hour} call${m.calls_last_hour === 1 ? '' : 's'} in the last hour`
      : 'No calls seen yet this session.';
  } else {
    const n = (m.calls_total || 0).toLocaleString();
    callsLine = currentSpeedRange === 'all'
      ? `${n} call${m.calls_total === 1 ? '' : 's'} all time`
      : `${n} call${m.calls_total === 1 ? '' : 's'} over ${RANGE_CALLS_TEXT[currentSpeedRange]}`;
  }
  return `
    <div class="mcard">
      <div class="mhead">
        <div>
          <div class="model-name">${_escapeHtml(modelDisplayName(m.model))}${whereTag(m)}</div>
          ${via}
          <div class="roles">${chipsHtml}</div>
        </div>
        ${rel}
      </div>
      <div class="sv-stats">
        <div class="sv-stat"><span class="l">Starts talking in</span><span class="n">${startsN}</span>${startsV}</div>
        <div class="sv-stat"><span class="l">Full reply in</span><span class="n">${fmtSecs(typicalFull)}</span>${fullV}</div>
        <div class="sv-stat"><span class="l">Talks at</span><span class="n">${talksN}</span>${talksV}</div>
      </div>
      ${renderRangeBar(v2.full)}
      ${renderStrip(v2.strip)}
      ${errLine}
      <div class="calls">${callsLine}</div>
    </div>`;
}

// Restore the two remembered choices as soon as the page loads (the Speeds tab may not be opened yet).
if ($('unitWords')) { applyUnitToggle(); applyHealthShowSelect(); }
"""


# --- Dashboard colours (2026-09-30) ---------------------------------------------------------
# The dashboard wears whatever theme the player picked in SkyrimNet's own dashboard: three colours in
# <SkyrimNet>/config/Dashboard.yaml (accent_color, bg_color, text_color). No picker here. The three
# colours are read -- not the preset name -- so a custom theme works too. Everything else (panels,
# lines, muted text, tints) is mixed from them in CSS with color-mix, so any combination stays
# readable. Status colours are SkyrimNet's own, chosen by whether the background is light or dark.
_DRAGONBORN_THEME = {"accent": "#c9a84c", "bg": "#18181c", "text": "#e8e6e1"}
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_STATUS_COLORS_DARK = {"ok": "#7cefab", "warn": "#e8941a", "err": "#f26060", "info": "#60a5fa"}
_STATUS_COLORS_LIGHT = {"ok": "#136b33", "warn": "#8a4a06", "err": "#ab1d1d", "info": "#1d50b0"}
# Speeds role chips need four colours that stay distinct from each other AND readable on the page.
_ROLE_COLORS_DARK = {"dialogue": "#93c5fd", "diary": "#c4b5fd", "vision": "#5eead4", "gm": "#fca5a5"}
_ROLE_COLORS_LIGHT = {"dialogue": "#1d4ed8", "diary": "#6d28d9", "vision": "#0f766e", "gm": "#b91c1c"}


def _normalize_hex_color(value) -> Optional[str]:
    """'#abc' / '#AABBCC' -> '#aabbcc'; anything that isn't a plain hex colour -> None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not _HEX_COLOR_RE.match(value):
        return None
    value = value.lower()
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value


def _read_skyrimnet_theme(skyrim_path: str) -> dict:
    """{"accent", "bg", "text"} from <SkyrimNet>/config/Dashboard.yaml, or the Dragonborn default when
    nothing is linked, the file is missing/unreadable, or any of the three colours isn't a valid hex
    colour (mixing a real colour with a default one could produce an unreadable pair). Never raises.
    Deliberately not cached: re-read on every page load so a theme change in SkyrimNet shows up on
    the next refresh without restarting the proxy."""
    try:
        skyrimnet_dir = _resolve_skyrimnet_dir_cached(skyrim_path) if skyrim_path else None
        if not skyrimnet_dir:
            if skyrim_path:
                _note_skyrimnet_missing(skyrim_path, "SkyrimNet's dashboard colours")
            return dict(_DRAGONBORN_THEME)
        import yaml
        with open(os.path.join(skyrimnet_dir, "config", "Dashboard.yaml"), "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if not isinstance(doc, dict):
            return dict(_DRAGONBORN_THEME)
        theme = {
            "accent": _normalize_hex_color(doc.get("accent_color")),
            "bg": _normalize_hex_color(doc.get("bg_color")),
            "text": _normalize_hex_color(doc.get("text_color")),
        }
        if not all(theme.values()):
            return dict(_DRAGONBORN_THEME)
        return theme
    except Exception:
        return dict(_DRAGONBORN_THEME)


def _theme_is_light(bg_hex: str) -> bool:
    """Light when black text would contrast more with the background than white text would (WCAG
    relative luminance > 0.179) -- the same call a person makes by eye."""
    bg_hex = _normalize_hex_color(bg_hex) or _DRAGONBORN_THEME["bg"]
    def channel(i):
        c = int(bg_hex[1 + 2 * i:3 + 2 * i], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    luminance = 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
    return luminance > 0.179


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    def lum(h):
        def ch(i):
            c = int(h[1 + 2 * i:3 + 2 * i], 16) / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(0) + 0.7152 * ch(1) + 0.0722 * ch(2)
    hi, lo = sorted((lum(hex_a), lum(hex_b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _on_accent_color(theme: dict) -> str:
    """Text colour for a filled accent button: the theme's background colour (the SkyrimNet look), else its text colour, whichever reads at 4.5:1 on the accent.
    A mid-tone accent (Parchment's, Silver-Blood's, Imperial's) that neither reaches gets white or black, whichever is clearer (theme check, 2026-10-05)."""
    for pick in (theme["bg"], theme["text"]):
        if _contrast_ratio(pick, theme["accent"]) >= 4.5:
            return pick
    return max((theme["bg"], theme["text"], "#ffffff", "#000000"), key=lambda c: _contrast_ratio(c, theme["accent"]))


def _mix_hex(a: str, pct: int, b: str) -> str:
    """color-mix(in srgb, a pct%, b) as a hex colour (the same channel-by-channel mix the browser does)."""
    return "#" + "".join(f"{round(int(a[1 + 2 * i:3 + 2 * i], 16) * pct / 100 + int(b[1 + 2 * i:3 + 2 * i], 16) * (100 - pct) / 100):02x}" for i in range(3))


def _text_share(color: str, theme: dict, start: int = 72) -> int:
    """How much of `color` (the accent, or a status colour) TEXT in that colour keeps (the rest is the theme's text colour): as much as it can while still reading at 4.6:1 on the page
    and on a panel. 100 when the colour already reads as it is."""
    panel = _mix_hex(theme["text"], 6, theme["bg"])
    for pct in range(start, -1, -4):
        c = _mix_hex(color, pct, theme["text"]) if pct < 100 else color
        if _contrast_ratio(c, theme["bg"]) >= 4.6 and _contrast_ratio(c, panel) >= 4.6:
            return pct
    return 0


def _dashboard_theme_css(skyrim_path: str) -> str:
    """A `:root { ... }` block defining every colour the dashboard (and the Quick Test popup) uses."""
    theme = _read_skyrimnet_theme(skyrim_path)
    light = _theme_is_light(theme["bg"])
    status = _STATUS_COLORS_LIGHT if light else _STATUS_COLORS_DARK
    roles = _ROLE_COLORS_LIGHT if light else _ROLE_COLORS_DARK
    mixes = {
        "panel": ("text", 6, "bg"), "line": ("text", 16, "bg"), "line2": ("text", 28, "bg"),
        "muted": ("text", 72, "bg"), "soft": ("text", 78, "bg"), "body": ("text", 88, "bg"),
    }
    lines = [
        f"color-scheme:{'light' if light else 'dark'}",
        f"--accent:{theme['accent']}", f"--bg:{theme['bg']}", f"--text:{theme['text']}",
        f"--on-accent:{_on_accent_color(theme)}",
        f"--accent-text:color-mix(in srgb, var(--accent) {_text_share(theme['accent'], theme)}%, var(--text))",
        "--accent-hover:color-mix(in srgb, var(--accent) 82%, var(--text))",
    ]
    lines += [f"--{name}:color-mix(in srgb, var(--{a}) {pct}%, var(--{b}))" for name, (a, pct, b) in mixes.items()]
    lines += [f"--{name}:{color}" for name, color in status.items()]
    lines += [f"--{name}-text:color-mix(in srgb, var(--{name}) {_text_share(status[name], theme, 100)}%, var(--text))" for name in ("ok", "warn", "err")]
    lines += [f"--role-{name}:{color}" for name, color in roles.items()]
    return "  :root { " + "; ".join(lines) + " }"


@app.get("/help/content")
async def help_content_route():
    """Every Help page, built from docs/help/help-content.md (so a wording change never means editing code)."""
    return {"topics": help_content.load_topics()}


@app.get("/help/status")
async def help_status_route():
    """The live state of every provider, for the Help tab's dots and top line (same source as the Providers tab's pills)."""
    return {p["id"]: {"state": p["state"], "status": p["status"]} for p in await _dashboard_providers()}


def _overview_targets(ai: str) -> dict:
    """Who an AI named in a route is, for the Overview page: {"key", "pc" (another device's nickname, or None), "name"} -- the same key the page gives that model's or provider's row."""
    if is_local_model(ai):
        return {"key": "local:" + strip_local_prefix(ai), "pc": None, "name": strip_local_prefix(ai)}
    if is_remote_model(ai):
        nick, model = ai.split("/", 1)
        return {"key": f"pc:{ai}", "pc": nick, "name": model}
    label = _provider_label_for(ai)
    out = {"key": "provider:" + label, "pc": None, "name": label}
    clause = _trouble.clause(_engine_key(ai))
    if clause:
        out["trouble"] = f"{label} {_resolve_model_name(ai)}: {clause}"       # what its own engine says is wrong with this model ("Ollama gemma4:26b: couldn't load (...)")
    return out


def _overview_routes() -> tuple:
    """(routes as [{"job", "ais": [target, ...]}], {row key: ["Dialogue", "Memories backup", ...]}, jobs as [{"key", "job", "ais"}]) from the saved routes (what the Routing tab sets).
    Built the way the Routing tab reads them (routing_view.build): a comma list in a route is several steps (route_ais), and a job that cannot be told apart from another
    (a hard group) without a route of its own follows the group's first route. Before 2026-10-23 neither was done here, so a model could be a job's first choice on the Routing tab
    and a "backup" on the Overview. `jobs` is every job with a route or a model SkyrimNet names, in the Routing tab's order, for the Overview's "Where each job runs"."""
    _refresh_job_names()
    settings = _routing_settings()
    saved = settings.get("routes") or {}
    table = _skyrimnet_job_table() or {}
    in_group = {k: g for g in routing.hard_groups(table, named=_named_jobs(table)) for k in g}
    keys = list(routing.JOB_ORDER) + [k for k in list(table) + list(saved) + job_names.NAMES.known_keys() if k not in routing.JOB_ORDER]
    routes, uses, jobs = [], {}, []
    # what SkyrimNet itself says about a model name, read the way the Routing tab reads it (routing_view.build): a name given to a provider MultiProxy does not carry (OpenRouter),
    # and only to that one, is "set in SkyrimNet"; the name or provider id of MultiProxy's own decision model is the Quick decisions model on this or another device
    other: dict = {}
    mine: set = set()
    mine_decisions: set = set()
    for tk, row in table.items():
        models = list(row.get("models") or [])
        for m, pid in zip(models, list(row.get("providers") or []) + [None] * len(models)):
            if routing.elsewhere_name(pid):
                other[m] = routing.elsewhere_name(pid)
            else:
                mine.add(m)
            if m.lower() == decision_wiring.LOCAL_NAME or "multiproxy-decisions" in str(pid or "").lower():
                mine_decisions.add(m)
    other = {m: n for m, n in other.items() if m not in mine}

    def target_of(ai: str) -> dict:
        if ai in other:
            return {"key": "elsewhere:" + ai, "pc": None, "name": "Set in SkyrimNet (" + other[ai] + ")", "elsewhere": other[ai]}
        if ai in mine_decisions:
            return {"key": "decisions", "pc": None, "name": "Quick decisions"}
        return _overview_targets(ai)
    for job_key in dict.fromkeys(keys):
        own = saved.get(job_key)
        route = own or (routing.route_for(settings, in_group[job_key])[0] if job_key in in_group else None)
        if route:
            names = routing_view.route_ais(route)
        else:                                         # no route of its own: answered by the model(s) SkyrimNet names for it
            names = routing_view.route_ais({"ais": [str(m) for m in (table.get(job_key) or {}).get("models") or []]})
        if not names:
            continue
        label = routing.label(job_key)
        ais = []
        for a in names:
            tg = target_of(a)
            if not (ais and ais[-1]["key"] == tg["key"] and tg["key"] == "decisions"):       # (the decision model named twice in a row is one step)
                ais.append(tg)
        jobs.append({"key": job_key, "job": label, "ais": ais})
        if own:
            routes.append({"job": label, "ais": ais})
        for i, t in enumerate(ais):
            if t.get("elsewhere"):
                continue                               # (no row of its own on the page: nothing here is used by it)
            tag = label + (" backup" if i and route else "")
            if tag not in uses.setdefault(t["key"], []):
                uses[t["key"]].append(tag)
    return routes, uses, jobs


def _overview_gpu() -> dict:
    """{device name: {"cards": real graphics cards, "choices": {AI key: chosen card id or None}}} for this device and each Courier that reported its cards, from the GPU choice data
    the Routing tab already uses. Nothing is probed: this device's cards are the cached read."""
    def one(block) -> Optional[dict]:
        if not isinstance(block, dict):
            return None
        return {"cards": len(block.get("cards") or []), "choices": {a.get("key"): ((a.get("choice") or {}).get("id") or None) for a in (block.get("ais") or []) if a.get("key")}}

    out = {}
    try:
        cards = gpu_choice_wiring.gpu_choice.cached_cards()
        me = one(gpu_choice_wiring.build_block(local_svc, decision_handles.engine if hasattr(decision_handles, "engine") else None, voice_handles.engine if hasattr(voice_handles, "engine") else None,
                                              gpu_choice_wiring.stored_choices(_load_config), cards))
        if me:
            out["This device"] = me
    except Exception as e:
        logger.debug(f"Overview: couldn't read this device's graphics cards ({e.__class__.__name__})")
    for r in remote_svc.all():
        if r.kind == "courier" and isinstance(r.report, dict):
            info = one(r.report.get("gpu_choice"))
            if info is None and r.report.get("gpus"):
                info = {"cards": len(r.report["gpus"]), "choices": {}}
            if info:
                out[r.label] = info
    return out


def _overview_day_start() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _overview_service(handle) -> Optional[dict]:
    try:
        st = handle.status()
    except Exception:
        return None
    return {"state": st.get("state") or "", "label": st.get("label") or "", "pc": st.get("pc") or "", "installed": bool(st.get("installed"))}


async def _overview_pick_ollama_test(entry: dict, p: dict) -> None:
    """Ollama's Test must use a model that can chat (the first one it lists may be an embedding model): one a route already uses, else one that answered in the last day, else the
    first that can chat. `testNext` is the model to try once more if Ollama still says "does not support chat" (an old Ollama that does not report what a model can do)."""
    chat = p.get("chatModels")
    if chat is None:
        chat = [m[0] for m in p["models"]]
    routed = []
    for r in (_routing_settings().get("routes") or {}).values():
        for ai in r.get("ais") or []:
            routed += [x for x in _expand_route_entry(ai) if is_ollama_model(x)]
    recent = []
    if ENABLE_SPEED_TRACKING:
        recent = await asyncio.to_thread(speed_queries.recent_models, SPEED_HISTORY_DB, _PROVIDER_SHORT.get("ollama") or "Ollama", time.time() - 86400)
    order = overview.pick_test_models(chat, routed, recent)
    if order:
        entry["testModel"], entry["testNext"] = order[0], order[1:2]
    else:
        entry["testModel"] = None


async def _overview_payload() -> dict:
    """Everything the Overview page shows (overview.build), gathered from what MultiProxy already knows. No new work is started: the other devices' last reports, the providers'
    last states, the saved replies' last hour, this device's graphics card. A part that cannot be read is left out, never guessed."""
    now = time.time()
    figures = health = None
    if ENABLE_SPEED_TRACKING:
        try:
            figures = await _speed_cache.get(("overview", SPEED_HISTORY_DB), speed_queries.overview_figures, SPEED_HISTORY_DB, now, _overview_day_start(), ttl_s=3.0)
            recent, baseline = await _speed_cache.get(("health", SPEED_HISTORY_DB), speed_queries.health_rows, SPEED_HISTORY_DB, now, HEALTH_WINDOW_SECONDS, _V2_ROW_COLUMNS)
            health = {c["provider"]: c for c in _health_cards(recent, baseline, None)}
        except Exception as e:
            logger.debug(f"Overview: couldn't read the Speeds history ({e.__class__.__name__})")
            figures = health = None
    gpu = await local_svc.gpu()
    mem = {}
    try:
        mem = await gpu_monitor_svc.payload(include_skyrim=False)
    except Exception:
        mem = {}
    this_pc = {"name": other_pcs.short_hostname(), "host": other_pcs.short_hostname(), "gpu": gpu.name if gpu else None,
               "usedMb": mem.get("usedMb") if mem.get("available") else None, "totalMb": mem.get("totalMb") if mem.get("available") else None}
    local_models_rows = [{"id": m.id, "name": m.name, "modelName": m.model_name, "state": local_svc.state_of(m.id)[0], "guardNote": local_svc.guard_note(m.id)} for m in await asyncio.to_thread(local_svc.installed)]
    try:
        speeds = {m["id"]: m for m in (await local_models_speeds())["models"]}
    except Exception:
        speeds = {}
    backup = _backup_model()
    pcs = []
    for r in remote_svc.all():
        g = next((g for g in ((r.report or {}).get("gpus") or []) if g.get("memory_total_mb") and g.get("memory_used_mb") is not None), None) if r.kind == "courier" else None
        pcs.append({"id": r.id, "name": r.label, "online": r.online, "gpu": (r.gpu or {}).get("name") if isinstance(r.gpu, dict) else None, "program": r.program or None,
                    "kind": r.kind, "models": [{"id": m["id"], "name": m.get("name") or m["id"], "state": m.get("state", "running"), "program": m.get("program")} for m in r.models],
                    "problem": (other_pcs.problem_of(r) or {}).get("message") if r.online is not False else None, "coveredBy": backup if r.online is False else None,
                    "usedMb": g["memory_used_mb"] if g else None, "totalMb": g["memory_total_mb"] if g else None,
                    "codeChanged": r.code_changed, "courierOlder": r.kind == "courier" and other_pcs.courier_is_older(r.report),
                    "engines": other_pcs.engines_of(r.report) if r.kind == "courier" else []})
        if r.kind == "courier" and r.report is None and r.online:
            logger.debug(f"Overview: '{r.label}' is online but has not reported its programs and models yet")
    prov_rows = await poll_cache.shared("overview-providers", lambda v: 5.0, _dashboard_providers, stale_ok=True)
    providers = []
    for p in prov_rows:
        if p["id"] in ("local", "remote"):
            continue
        short = _PROVIDER_SHORT.get(p["id"]) or ("TypeSafe" if p["id"] == "typesafe" else p["name"])
        providers.append({"id": p["id"], "name": p["name"], "short": short, "state": p["state"], "status": p["status"],
                          "cloud": p["id"] not in ("ollama", "lmstudio"), "where": "This device" if p["id"] in ("ollama", "lmstudio") else "Cloud",
                          "testModel": (p.get("models") or [[None]])[0][0] if p.get("testable") and p.get("models") else None})
        if p["id"] == "ollama" and p.get("testable") and p.get("models"):
            await _overview_pick_ollama_test(providers[-1], p)
    routes, uses, route_jobs = _overview_routes()
    decisions = _overview_service(decision_handles.active())
    if decision_handles.cloud_only():                 # "Cloud only": no local decision model anywhere, so its row reads Not used (grey) and nothing about it is ever a problem
        decisions = {**(decisions or {}), "state": "stopped", "label": "Not used", "pc": (decisions or {}).get("pc") or "", "installed": False, "cloudOnly": True}
    if decisions:
        order = decision_handles.order(None, bool(typesafe_api_key), bool(nano_api_key)) or []
        after = [x for x in order if x != "local"]
        decisions["fallback"] = ({"typesafe": "TypeSafe", "nano": "Nano-GPT", "standin": "another AI"}.get(after[0]) or (decision_providers.REGISTRY[after[0]].name if after[0] in decision_providers.REGISTRY else None)) if after else None
        decisions["localFirst"] = bool(order and order[0] == "local")             # the player chose the local model to answer first (so a stopped one is a problem, not a choice)
        if decisions["fallback"] in ("TypeSafe", "Nano-GPT", "Perplexity") and decisions["installed"]:
            uses.setdefault(f"provider:{decisions['fallback']}", []).append("Quick decisions backup")
    voice = _overview_service(voice_handles.routed or voice_handles.engine)
    rec = _load_config().get("get_started") if isinstance(_load_config().get("get_started"), dict) else {}
    left = []
    if not any(p["state"] == "ok" for p in prov_rows):
        left.append("add a provider")
    if not skyrim_install_path:
        left.append("link your Skyrim folder")
    return overview.build(tracking=ENABLE_SPEED_TRACKING, figures=figures, this_pc=this_pc, local_models=local_models_rows, local_speeds=speeds, pcs=pcs, providers=providers,
                          health=health or {}, uses=uses, routes=routes, decisions=decisions, voice=voice, setup={"done": bool(rec.get("done")), "left": left},
                          skyrimnet=overview.skyrimnet_status(_outside_client["at"], bool(_skyrim_state.running), time.time()), models_off=models_off.is_off(_load_config()),
                          skyrim_folder_lost=not (skyrim_install_path and _resolve_skyrimnet_dir_cached(skyrim_install_path)), jobs=route_jobs, gpu=await asyncio.to_thread(_overview_gpu),
                          set_me_up={"show": await _set_me_up_show(), "running": bool(set_me_up_svc.job and set_me_up_svc.job.state == "running")})


@app.get("/config/voice/speaking-rate")
async def voice_speaking_rate():
    """The Engine output window's header: seconds the voice engine spent per second of sound, from the last hour's spoken lines (None under 5 lines with both figures)."""
    if not ENABLE_SPEED_TRACKING:
        return {"rtf": None, "lines": 0}
    return await asyncio.to_thread(speed_queries.voice_speaking_rate, SPEED_HISTORY_DB, time.time())


@app.get("/stats/overview")
async def stats_overview():
    """The Overview page (the first tab): every machine, model and service with its status and current numbers. One shared answer for a few seconds, so two open tabs cost one read."""
    return await poll_cache.shared("overview", lambda v: 3.0, _overview_payload, stale_ok=True, max_stale=_DEVICE_VIEW_MAX_STALE)


def _models_tab_downloads(eng, ve) -> list:
    """The kept downloads store (shared by the quick-decisions and voice engines) as Models tab rows: kept-only models, the voice runner builds, and leftovers."""
    files = {f["name"]: f["size"] for f in voice_handles.store.listing()["files"]}
    if not files:
        return []
    keys = set(ve.installed_models())
    st = ve._read_state() if ve.installed() else {}
    decisions = [{"key": k, "name": sp.label, "names": [f.name for f in sp.files()], "installed": eng.installed(k)} for k, sp in eng.models.items()]
    voices = [{"key": k, "name": vm.name, "names": [vm.file.name] + ([vm.companion.name] if vm.companion else []), "installed": k in keys} for k, vm in ve.catalog.items()]
    runners = [{"key": k, "name": f"Voice engine ({b.label})", "names": [f.name for f in b.files], "inUse": bool(st) and st.get("build") == k} for k, b in ve.builds.items()]
    return models_tab.download_rows(files, decisions, voices, runners)


def _models_tab_this_pc(uses: dict) -> dict:
    """This PC's table for the Models tab: the Local device tab's own list, the kept models, and the installed quick-decisions and voice models."""
    installed = []
    for m in local_svc.installed():
        installed.append({"id": m.id, "name": m.name, "state": local_svc.state_of(m.id)[0], "sizeBytes": m.size, "custom": m.custom})
    decision = voice = None
    eng = decision_handles.engine
    if eng.installed():
        key = eng.model_key()
        decision = {"id": key, "name": eng.spec(key).label, "sizeBytes": eng.spec(key).total_bytes(),
                    "state": "running" if eng.state == "ready" else ("loading" if eng.state in ("starting", "getting_ready") else "stopped")}
    ve = voice_handles.engine
    keys = ve.installed_models()
    if keys:
        key = ve.model_key if ve.model_key in keys else keys[0]
        vm = ve.catalog[key]
        live = key == ve.model_key
        voice = {"id": key, "name": vm.name, "sizeBytes": vm.file.size + (vm.companion.size if vm.companion else 0),
                 "state": ("running" if ve.state in ("ready", "too_slow") else "loading" if ve.state in ("starting", "getting_ready") else "stopped") if live else "stopped"}
    rows = models_tab.this_pc_rows(installed, local_svc.kept(), uses, decision, voice)
    rows += _models_tab_downloads(eng, ve)
    rows += models_tab.runner_rows(local_svc.engine_builds(), [m.name for m in local_svc.installed()] + (['Quick decisions'] if eng.installed() else []))
    folder = local_svc.folder
    free = drive = None
    if folder:
        try:
            near = folder
            while not near.exists() and near != near.parent:
                near = near.parent
            free = shutil.disk_usage(near).free
        except OSError:
            free = None
        drive = os.path.splitdrive(str(folder))[0] or str(folder)
    return models_tab.this_table(other_pcs.short_hostname(), rows, str(folder) if folder else None, free, drive)


@app.post("/config/local-models/delete-engine-build")
async def local_models_delete_engine_build(request: Request):
    data = await request.json()
    try:
        freed = await asyncio.to_thread(local_svc.delete_engine_build, str(data.get("dir", "")))
    except local_models.LocalModelError as e:
        return _local_error(e)
    return {"status": "deleted", "freedBytes": freed}


@app.post("/config/models-tab/delete-downloads")
async def models_tab_delete_downloads(request: Request):
    """Delete named files from the kept downloads (the player said yes on the Models tab). Only names the store really keeps; anything installed from them keeps working
    (an install is a hard link or a copy, never a pointer into the store)."""
    data = await request.json()
    kept = {f["name"] for f in voice_handles.store.listing()["files"]}
    names = [n for n in (data.get("names") or []) if isinstance(n, str) and n in kept]
    freed = await asyncio.to_thread(voice_handles.store.clear, names) if names else 0
    return {"status": "deleted", "freedBytes": freed}


@app.get("/config/models-tab")
async def models_tab_payload():
    """The Models tab: one table per PC (this one, then each paired Courier PC), read from what MultiProxy already knows."""
    _routes, uses, _jobs = _overview_routes()
    pcs = [await asyncio.to_thread(_models_tab_this_pc, uses)]
    for r in remote_svc.all():
        if r.kind == "courier":
            pcs.append(models_tab.remote_table(r.id, r.label, r.online, r.report, uses))
    return {"pcs": pcs, "modelsOff": models_off.is_off(_load_config())}


def _fix_it_listener_pids(port: int) -> list:
    if os.name == "nt":
        return fix_it.windows_listener_pids(port)
    pid = _port_listener_pid_linux(port)
    return [pid] if pid else []


async def _fix_it_engine() -> Optional[dict]:
    """The engine check's facts: the decision model is installed on this PC and the engine is one the decision model cannot use (older than the build it needs)."""
    if not decision_handles.engine.installed():
        return None
    n = local_svc.engine_build_number()
    if not (local_svc.engine_exe() and 0 < n < local_models.NATIVE_DECISION_MIN_BUILD):
        return None
    info = await local_svc.engine_update_info()
    return {"old": True, "canUpdate": bool(info.get("available"))}


@app.post("/config/check-everything")
async def check_everything():
    """Overview's Check everything button: only what is wrong, one entry each (fix_it.py). Reads facts MultiProxy already has; changes nothing."""
    ctx = fix_it.Context(
        main_port=MAIN_PORT, claude_port=INTERCEPTOR_PORT, own_pid=os.getpid(), listener_pids=_fix_it_listener_pids, process_name=fix_it.process_name_of,
        pcs=lambda: [{"id": r.id, "name": r.label, "kind": r.kind, "online": r.online, "error": r.error, "codeChanged": r.code_changed} for r in remote_svc.all()],
        engine=_fix_it_engine, claude_too_old=lambda: bool(CLAUDE_PATH) and claude_trouble.is_too_old(_claude_version_now()))
    return await fix_it.run_all(ctx)


@app.get("/config/addresses")
async def addresses_route():
    """The addresses a player pastes into SkyrimNet, for Settings > SkyrimNet connection: chat, and (once set up) voice and quick decisions, each from the helper its own card uses."""
    def state_of(engine) -> str:
        try:
            return str(engine.status().get("state") or "")
        except Exception:
            return ""
    voice_state = state_of(voice_handles.routed or voice_handles.engine)
    decisions_state = state_of(decision_handles.active())
    off = ("", "not_installed", "installing")
    return {
        "chat": f"http://127.0.0.1:{MAIN_PORT}/v1/chat/completions",
        "voice": {"setUp": voice_state not in off, "address": voice_wiring.endpoint_address(MAIN_PORT)},
        "decisions": {"setUp": decisions_state not in off, "address": decision_wiring.endpoint_address(MAIN_PORT)},
        "stt": {"on": bool(_load_config().get(voice_wiring.STT_CONFIG_KEY)), "address": voice_wiring.stt_address(MAIN_PORT)},
    }


@app.post("/config/help-seen")
async def help_seen_route():
    cfg = _load_config()
    cfg["help_seen"] = True
    _save_config(cfg)
    return {"status": "ok"}


@app.get("/favicon.ico")
async def favicon():
    """The dashboard tab's icon, served from this device (the same picture as the installed app's icon)."""
    path = app_icon.find("multiproxy.ico")
    if path is None:
        return Response(status_code=404)
    return Response(content=path.read_bytes(), media_type="image/x-icon", headers={"Cache-Control": "public, max-age=86400"})


@app.head("/", include_in_schema=False)
async def dashboard_is_there():
    """A readiness probe ("is anything there?") gets the page's answer with no body. Same local-only guards as GET /."""
    return Response(status_code=200, media_type="text/html")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    theme_css = _dashboard_theme_css(skyrim_install_path)
    # (The Setup header used to show Claude's template size, default model and CLI name; those are Claude
    # internals. The template size is in the log at capture time, and the default model is on Claude's panel.)
    # "Plugin not detected" means this proxy wasn't started by the SkyrimNet MultiProxy plugin: say what that
    # means, and where to get the plugin.
    plugin_help_html = "" if PLUGIN_VERSION else (
        f'<div class="version-row"><span class="value">Mod not detected</span>'
        f'<a class="help-q" href="#settings" onclick="openSettings(\'mod\'); return false" title="{html_escape(PLUGIN_HELP_TEXT)}">?</a></div>')
    # Only flag a mismatch when BOTH sides are actually known -- a missing plugin version (any
    # launch that doesn't go through the SKSE plugin at all) is the normal, expected case, not
    # something to warn about.
    # (the mod's version is a plain number, so only the release number is compared: the beta tail is ignored)
    version_mismatch = bool(PLUGIN_VERSION) and _version_core(PLUGIN_VERSION) != _version_core(PROXY_VERSION)
    version_mismatch_html = (
        f'<div class="version-mismatch"><span class="dot warn"></span>'
        f'MultiProxy and the Skyrim mod versions do not match. Please update both.</div>'
    ) if version_mismatch else ""

    providers = await _dashboard_providers()
    default_provider = _default_provider_id()
    ready_count = sum(1 for p in providers if p["state"] == "ok")
    # A brand-new install (nothing ready, and the Help page not shown before) opens on Help once.
    get_started_first_run = _get_started_first_run(_load_config())          # a first install: Get started opens by itself (and Help doesn't)
    help_first_run = ready_count == 0 and not _load_config().get("help_seen") and not get_started_first_run
    # Every model row in `providers` is a real, directly-testable id -- an "any model" provider
    # (Kilo) marks its placeholder shape in its own `any` field instead. This replaces
    # the old _PLACEHOLDER_MODEL_IDS set, which existed because the dropdown filter used to decide
    # testability by "contains a slash" -- a heuristic that once hid real ids like
    # nano/gpt-4o-mini. Never bring that heuristic back.
    providers_json = _script_json(providers)
    ks_su = "".join(f", '{s.id}'" for s in key_sources.SOURCES)          # the key sources get the same "Start here" steps as every other key provider

    # Speeds tab, gated on EnableSpeedTracking (2026-09-20) -- when off, this must render EXACTLY
    # like the dashboard did before the Speeds tab ever existed: no tab bar, no empty/disabled
    # Speeds pane, just the Setup content. Built as plain (non-f-string) strings and spliced in
    # below rather than a second HTML template, so there's one source of truth for the markup
    # either way -- these two blocks are pure static HTML with no Python values inside them, so
    # they need no `{{`/`}}` escaping. The Speeds-tab JS functions stay defined either way (dead,
    # never called with no tab button to invoke them from) -- see this feature's own handoff for
    # why stripping ~250 lines of otherwise-inert JS wasn't worth the added risk.
    speed_tabs_html = ("""
  <div class="tabs">
    """ + overview_ui.TAB_HTML + """
    <div class="tab" id="tabSetup" onclick="showMainTab('setup')">Providers</div>
    <div class="tab" id="tabLocal" onclick="showMainTab('local')">Local device</div>
    """ + other_pcs_ui.OTHER_PCS_TAB_HTML + voice_ui.VOICE_TAB_HTML + decision_ui.DECISION_TAB_HTML + models_ui.MODELS_TAB_HTML + routing_ui.ROUTING_TAB_HTML + """
    <div class="tab" id="tabQuick" onclick="showMainTab('quick')">Quick Test</div>
    """ + (
        """<div class="tab" id="tabSpeeds" onclick="showMainTab('speeds')">Speeds</div>""" if ENABLE_SPEED_TRACKING else "") + help_ui.HELP_TAB_HTML + settings_ui.COG_HTML + """
  </div>
""")
    if ENABLE_SPEED_TRACKING:
        speed_pane_html = """
  <div id="paneSpeeds" style="display:none">
""" + tab_desc_ui.html("speeds") + """    <div class="card su-start" data-sh-static="speeds">
      <h3>Start here</h3>
      <ol class="su-steps">
        <li>Let it find your Skyrim folder, or type it and press Link.</li>
        <li>Play for a minute and talk to an NPC.</li>
        <li>Check the health cards at the top (green means fine).</li>
        <li>Open a model card to see the numbers. Use "By job" to compare providers.</li>
      </ol>
    </div>
    <div class="card" id="linkCard">
      <h3>See what each provider is actually used for.</h3>
      <p style="color:var(--body); font-size:0.85rem; line-height:1.6; margin:8px 0 4px">
        Link your Skyrim install to see speeds for each job, like dialogue or diary writing.
      </p>
      <div id="detectSearching" class="detect-row" style="display:none"><span>&#128269; Looking for your Skyrim install via Steam&hellip;</span></div>
      <div id="manualRow" class="path-row" style="display:none">
        <input id="pathInput" placeholder="e.g. D:\\Games\\Steam\\steamapps\\common\\Skyrim Special Edition">
        <button onclick="linkSkyrimPath()">Link</button>
      </div>
    </div>

    <div class="card" id="linkedCard" style="display:none">
      <div class="skyrimnet-not-found-warn" id="skyrimnetNotFoundWarn" style="margin-top:0">
        <span class="dot warn"></span>Linked, but your SkyrimNet folder was not found. Tell MultiProxy where it is in Settings.
        <button class="ghost" style="margin-left:auto; padding:4px 10px; font-size:0.78rem" onclick="openSettings('folders')">Change in Settings</button>
      </div>
    </div>

    <div class="card" id="healthCard">
      <div class="health-head">
        <h3>How your AIs are doing right now</h3>
        <div style="display:flex; gap:14px; flex-wrap:wrap; align-items:center">
          <label class="label" id="speedPcBox" style="display:none">Where
            <select id="speedPc" onchange="setSpeedPc(this.value)"></select>
          </label>
          <label class="label">Show
            <select id="healthShow" onchange="setHealthShow(this.value)">
              <option value="hour">Used in the last hour</option>
              <option value="ready">Every ready service</option>
            </select>
          </label>
        </div>
      </div>
      <p class="section-hint">A quick verdict for each service you've used lately, so you can spot a struggling one before your NPCs do.</p>
      <div class="health-grid" id="healthGrid"></div>
      <div class="empty" id="healthEmpty" style="display:none"></div>
    </div>

    <div class="card">
      <div class="sv-toolbar">
        <div class="view-toggle" id="speedViewToggle" style="margin-bottom:0">
          <span class="view-opt active" id="optSpeedsByModel" onclick="showSpeedsView('model')">By model</span>
          <span class="view-opt" id="optSpeedsByRole" onclick="showSpeedsView('role')">By job</span>
        </div>
        <div class="view-toggle" id="unitToggle" title="How speed is shown" style="margin-bottom:0">
          <span class="view-opt active" id="unitWords" onclick="setSpeedUnit('words')">words/s</span>
          <span class="view-opt" id="unitTokens" onclick="setSpeedUnit('tokens')">tokens/s</span>
        </div>
        <div class="range-picker" id="rangePicker">
          <span class="range-opt active" data-r="today" onclick="selectSpeedRange('today')">Today</span>
          <span class="range-opt" data-r="7d" onclick="selectSpeedRange('7d')">7 days</span>
          <span class="range-opt" data-r="30d" onclick="selectSpeedRange('30d')">30 days</span>
          <span class="range-opt" data-r="all" onclick="selectSpeedRange('all')">All time</span>
        </div>
      </div>
      <div id="ownTimeLine" style="display:none; margin:2px 0 10px; font-size:12px; color:var(--muted)"></div>

      <div id="speedsByModelView">
        <div style="text-align:right; margin-bottom:6px"><span class="thresh-toggle" onclick="toggleThresh()">Edit speed thresholds</span></div>
        <div class="thresh-panel" id="threshPanel">
          <div class="label" style="margin-bottom:8px">Change what counts as quick or slow for each job. "Starts talking" is the wait before the first word appears.</div>
          <div class="thresh-row"><span class="rl">Dialogue &middot; starts talking</span><span class="tx">quick under</span><input id="st-dialogue-good"><span class="tx">sec &middot; slow over</span><input id="st-dialogue-slow"></div>
          <div class="thresh-row"><span class="rl">Dialogue &middot; full reply</span><span class="tx">good under</span><input id="th-dialogue-good"><span class="tx">sec &middot; slow over</span><input id="th-dialogue-slow"></div>
          <div class="thresh-row"><span class="rl">Game Master &middot; starts talking</span><span class="tx">quick under</span><input id="st-gamemaster-good"><span class="tx">sec &middot; slow over</span><input id="st-gamemaster-slow"></div>
          <div class="thresh-row"><span class="rl">Game Master &middot; full reply</span><span class="tx">good under</span><input id="th-gamemaster-good"><span class="tx">sec &middot; slow over</span><input id="th-gamemaster-slow"></div>
          <div class="thresh-row"><span class="rl">Vision &middot; starts talking</span><span class="tx">quick under</span><input id="st-vision-good"><span class="tx">sec &middot; slow over</span><input id="st-vision-slow"></div>
          <div class="thresh-row"><span class="rl">Vision &middot; full reply</span><span class="tx">good under</span><input id="th-vision-good"><span class="tx">sec &middot; slow over</span><input id="th-vision-slow"></div>
          <div class="thresh-row"><span class="rl">Background (diary, memory, profile) &middot; starts talking</span><span class="tx">quick under</span><input id="st-background-good"><span class="tx">sec &middot; slow over</span><input id="st-background-slow"></div>
          <div class="thresh-row"><span class="rl">Background (diary, memory, profile) &middot; full reply</span><span class="tx">good under</span><input id="th-background-good"><span class="tx">sec &middot; slow over</span><input id="th-background-slow"></div>
          <div class="thresh-save">
            <span class="saved-msg" id="threshSaved">&#10003; Saved</span>
            <span class="saved-msg" id="threshError" style="color:var(--err)"></span>
            <button class="ghost" onclick="toggleThresh()">Cancel</button>
            <button onclick="saveThresh()">Save</button>
          </div>
        </div>
        <div id="speedRows"></div>
      </div>

      <div id="speedsByRoleView" style="display:none">
        <div class="rtabs" id="roleTabs"></div>
        <div class="role-explain" id="roleExplain"></div>
        <table class="role-table" id="roleTable">
          <thead><tr>
            <th>Model</th><th>Share of this job</th><th>Reliability</th>
            <th id="thFirst">Starts talking</th><th id="thFull">Full reply</th>
          </tr></thead>
          <tbody id="roleRows"></tbody>
        </table>
        <div class="empty" id="byRoleEmpty" style="display:none"></div>
      </div>
    </div>

    <div class="card" id="trueEmptyCard" style="display:none">
      <div class="speeds-empty">No AI used yet this session. Talk to an NPC, then check back here.</div>
    </div>

  </div>
"""
    else:
        speed_pane_html = ""

    return f"""<!DOCTYPE html>
<html data-width="{_page_width()}"><head><title>SkyrimNet MultiProxy</title>
<link rel="icon" href="/favicon.ico">
<style>
{theme_css}
  /* Page width (Settings > Appearance): --page-w is how wide the column may get; --prose-w is kept as a hook but is "none": text uses the width its card has (director, 2026-10-02: no narrow text columns on a wide page). */
  :root {{ --page-w:1100px; --prose-w:none; --page-pad:20px }}
  html[data-width="comfortable"] {{ --page-w:720px }}
  html[data-width="full"] {{ --page-w:none; --page-pad:32px }}
  html {{ scrollbar-gutter:stable }}  /* the scroll bar's space is always reserved, so the page never jumps sideways when it gets taller or shorter */
  body {{ background:var(--bg); color:var(--text); font-family:system-ui,sans-serif; max-width:var(--page-w); margin:32px auto; padding:0 var(--page-pad) }}
  p, .hint, .muted, .lm-muted, .subtitle {{ max-width:var(--prose-w) }}
  input[type=text], input[type=password], input[type=number], select {{ max-width:100% }}
  /* text boxes that sit beside a button never stretch into absurdly long fields on a wide page */
  .lm-line input[type=text], .op-add input[type=text], #lmFolderTyped {{ max-width:640px }}
  h1 {{ color:var(--text); font-size:1.5rem; margin:0 0 4px }}
  h3 {{ margin:0; font-size:1rem; color:var(--text) }}
  .subtitle {{ color:var(--muted); font-size:0.9rem; margin-bottom:14px }}
  .card {{ background:var(--panel); border-radius:8px; padding:20px; margin:16px 0; border:1px solid var(--line) }}
  .label {{ color:var(--soft); font-size:0.8rem }}
  .value {{ color:var(--text); font-family:monospace; font-size:0.85rem }}
  .endpoint {{ background:var(--bg); padding:8px 12px; border-radius:6px; font-family:monospace; font-size:0.85rem; color:var(--accent-text); margin-top:4px; border:1px solid var(--line) }}
  .grid3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-top:12px }}
  .version-row {{ margin-top:12px }}
  .help-q {{ display:inline-block; width:16px; height:16px; line-height:15px; text-align:center; margin-left:8px; border:1px solid var(--line2); border-radius:50%; font-size:0.7rem; color:var(--soft); text-decoration:none; cursor:help; vertical-align:1px }}
  .help-q:hover {{ color:var(--text); border-color:var(--accent) }}
  .su-start h3 {{ margin:0 0 8px; font-size:1rem }}
  .su-steps {{ margin:6px 0 0 4px; padding:0; list-style:none; display:flex; flex-direction:column; gap:8px; counter-reset:su }}
  .su-steps > li {{ font-size:0.9rem; line-height:1.5 }}
  .su-steps > li::before {{ counter-increment:su; content:counter(su) ". "; font-weight:600 }}
  .su-box {{ margin:4px 0 14px; padding:12px 14px; border:1px solid var(--line2); border-radius:8px }}
  .su-box h4 {{ margin:0 0 6px; font-size:0.9rem }}
  .su-banner {{ margin:0 0 12px; padding:10px 12px; border-radius:8px; border:1px solid var(--warn); font-size:0.9rem }}
  .su-howto {{ font-size:0.95rem; padding:10px 18px; margin:0 0 12px }}
  a.su-howto {{ display:inline-block; background:var(--accent); color:var(--on-accent); border-radius:6px; text-decoration:none; cursor:pointer }}
  a.su-howto:hover {{ background:var(--accent-hover) }}
  .su-copyhint {{ margin:2px 0 8px }}
  .version-mismatch {{ display:flex; align-items:center; gap:6px; color:var(--warn); font-size:0.78rem; margin-top:10px }}
  .skyrimnet-not-found-warn {{ display:flex; align-items:center; gap:6px; color:var(--warn); font-size:0.78rem; margin-top:10px }}
  select, input, textarea {{ background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:6px; padding:8px 10px; font-family:monospace; font-size:0.85rem; box-sizing:border-box }}
  select {{ cursor:pointer }}
  select:focus, input:focus, textarea:focus {{ outline:none; border-color:var(--accent) }}
  ::placeholder {{ color:var(--muted); opacity:1 }}
  textarea {{ width:100%; resize:vertical }}
  button {{ background:var(--accent); color:var(--on-accent); border:none; padding:8px 18px; border-radius:6px; cursor:pointer; font-size:0.85rem }}
  button:hover {{ background:var(--accent-hover) }}
  /* A disabled button can't be pressed: say so with the cursor (the spinning 'wait' cursor read as "busy" and confused the director). */
  button:disabled {{ background:var(--line); color:var(--muted); cursor:not-allowed }}
  button.ghost {{ background:transparent; color:var(--soft); border:1px solid var(--line); padding:7px 12px }}
  button.ghost:hover {{ color:var(--text); border-color:var(--line2) }}
  code {{ color:var(--accent-text) }}
  a {{ color:var(--accent-text) }}

  .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px }}
  .card-head .version-row {{ margin:0 }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px }}
  .grp {{ margin-top:14px }} .grp:first-child {{ margin-top:0 }}
  .grp-h {{ font-size:0.74rem; letter-spacing:0.06em; text-transform:uppercase; color:var(--muted); margin:0 0 8px; display:flex; gap:10px; align-items:baseline }}
  .grp-h span {{ text-transform:none; letter-spacing:0 }}
  /* Request line (2026-09-27): a quiet live status under the provider chips, one line per provider
     that has a limit set in multiproxy.ini's [Concurrency]. Hidden when no limit is set. */
  .request-line {{ color:var(--muted); font-size:0.78rem; margin-top:10px; line-height:1.6 }}
  .chip {{ font:inherit; display:inline-flex; align-items:center; gap:8px; font-size:0.85rem; padding:4px 13px; border-radius:20px; background:color-mix(in srgb, var(--text) 6%, var(--bg)); border:1px solid var(--line); color:var(--text); cursor:pointer; user-select:none }}
  .chip:hover {{ border-color:color-mix(in srgb, var(--accent) 60%, var(--line)) }}
  /* The page's general button:hover fills with the accent; a chip never fills. */
  .chip:hover, .chip:active {{ background:color-mix(in srgb, var(--text) 6%, var(--bg)); color:var(--text) }}
  .chip.off:hover, .chip.off:active {{ background:transparent; color:var(--muted) }}
  .chip.active {{ border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) }}
  .chip .tag {{ font-size:0.72rem; color:var(--muted) }}
  .chip .dot {{ width:8px; height:8px }}
  .chip.off {{ color:var(--muted); background:transparent }}
  .chip .dot.off {{ background:transparent; box-shadow:inset 0 0 0 1.5px var(--muted) }}
  /* Issue dots pulse (DESIGN.md): yellow and red fade slowly in and out; green and "not set up" never move. */
  .chip .dot.warn, .chip .dot.bad {{ animation:issue 2.4s ease-in-out infinite }}
  @keyframes issue {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:0.45 }} }}
  @media (prefers-reduced-motion:reduce) {{ .chip .dot.warn, .chip .dot.bad {{ animation:none }} }}
  .dot {{ width:7px; height:7px; border-radius:50%; flex-shrink:0 }}
  .ok {{ background:var(--ok) }} .warn {{ background:var(--warn) }} .off {{ background:var(--line2) }} .bad {{ background:var(--err) }}
  .picker-row {{ display:flex; gap:10px; align-items:center; margin-top:16px }}
  .pill {{ font-size:0.8rem; font-weight:600; white-space:nowrap }}
  .panel {{ margin-top:14px; padding:16px; background:color-mix(in srgb, var(--bg) 50%, transparent); border:1px solid var(--line); border-radius:8px }}
  .key-row {{ display:flex; gap:8px; align-items:center }}
  .key-row input {{ flex:1 }}
  .help {{ color:var(--muted); font-size:0.8rem; margin-top:8px; line-height:1.5 }}
  .models-head {{ display:flex; justify-content:space-between; align-items:baseline; margin:18px 0 6px }}
  .models-head .label {{ text-transform:uppercase; letter-spacing:0.05em; font-size:0.72rem }}
  .model {{ display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr) auto; gap:12px; align-items:center; padding:7px 0; border-top:1px solid var(--panel) }}
  .model:first-of-type {{ border-top:none }}
  .model .mid {{ font-family:monospace; font-size:0.82rem; color:var(--accent-text); overflow-wrap:anywhere }}
  .model .name {{ font-size:0.85rem; overflow-wrap:anywhere }}
  .model .note {{ color:var(--muted); font-size:0.78rem; overflow-wrap:anywhere }}
  .copy {{ background:none; border:1px solid var(--line); color:var(--soft); padding:3px 9px; font-size:0.75rem; border-radius:5px }}
  .copy:hover {{ background:var(--panel); color:var(--text) }}
  .any-model {{ color:var(--soft); font-size:0.8rem; padding:6px 0 2px }}
  /* Pinned Codex models (2026-09-16) -- purple text is the only "this is pinned" signal, no
     separate badge (redundant with the color and the Unpin button already only appearing here). */
  .model--pinned .mid, .model--pinned .name {{ color:var(--role-diary) }}
  .pin-legend-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--role-diary); vertical-align:middle; margin-right:2px }}
  .pin-row {{ display:flex; gap:8px; align-items:center; margin-top:10px; padding-top:10px; border-top:1px dashed var(--line) }}
  .pin-row input {{ flex:1; font-family:system-ui,sans-serif; font-size:0.85rem }}
  .empty {{ color:var(--soft); font-size:0.82rem; padding:8px 0 2px; line-height:1.5 }}
  .models-head .copy {{ font-size:0.72rem }}

  .codex-tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 4px }}
  .codex-tabs button {{ background:transparent; border:1px solid var(--line); color:var(--soft); padding:7px 14px; font-size:0.82rem; border-radius:6px }}
  .codex-tabs button:hover {{ color:var(--text); border-color:var(--line2) }}
  .codex-tabs button.active {{ border-color:var(--accent); color:var(--text); background:color-mix(in srgb, var(--accent) 12%, transparent) }}

  /* rev 3 (2026-09-16): grouped-by-vendor picker for Pin a model, replacing the flat native
     datalist -- matches design/dashboard-codex-multi-provider-mockup.html's own rev 3 styles. */
  .pin-search {{ width:100%; margin-bottom:10px }}
  .vendor-group {{ border-top:1px solid var(--panel) }}
  .vendor-group:first-of-type {{ border-top:none }}
  .vendor-head {{ display:flex; justify-content:space-between; align-items:center; padding:9px 2px; cursor:pointer }}
  .vendor-head:hover .vendor-name {{ color:var(--text) }}
  .vendor-name {{ font-size:0.87rem; color:var(--body) }}
  .vendor-count {{ color:var(--muted); font-size:0.78rem }}
  .vendor-chevron {{ color:var(--muted); font-size:0.75rem; margin-right:8px; display:inline-block; width:0.8em }}
  .vendor-models {{ display:none; padding:2px 0 8px 18px }}
  .vendor-group.open > .vendor-models {{ display:block }}
  .vendor-group.open > .vendor-head .vendor-chevron {{ transform:rotate(90deg) }}
  .vendor-models .model {{ cursor:pointer; padding:6px 0 }}
  .vendor-models .model:hover .mid, .vendor-models .model:hover .name {{ color:var(--text) }}

  .codex-list-head {{ display:flex; justify-content:space-between; align-items:baseline; margin:20px 0 8px }}
  .codex-list-head .label {{ text-transform:uppercase; letter-spacing:0.05em; font-size:0.72rem }}
  .codex-row {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:center; padding:8px 0; border-top:1px solid var(--panel) }}
  .codex-row:first-of-type {{ border-top:none }}
  .codex-row .name {{ font-size:0.85rem }}
  .codex-row .note {{ color:var(--muted); font-size:0.78rem }}
  .codex-row .actions {{ display:flex; gap:6px }}
  .add-form {{ margin-top:14px; padding:14px; background:var(--bg); border:1px solid var(--line); border-radius:8px }}
  .add-form .field {{ margin-top:10px }}
  .add-form label {{ display:block; color:var(--soft); font-size:0.8rem; margin-bottom:4px }}
  .add-form input, .add-form select {{ width:100%; font-family:system-ui,sans-serif }}
  .add-form .radio-row {{ display:flex; gap:16px; font-size:0.85rem; flex-wrap:wrap }}
  .add-form .radio-row label {{ display:flex; align-items:center; gap:6px; color:var(--text); margin:0; white-space:nowrap }}
  .add-form .field-hint {{ color:var(--muted); font-size:0.78rem; margin-top:5px; line-height:1.4 }}
  .add-form .form-actions {{ display:flex; gap:8px; justify-content:flex-end; margin-top:14px }}
  .add-form .readonly-value {{ color:var(--accent-text); font-family:monospace; font-size:0.85rem; padding:8px 0 }}

  .test-row {{ display:grid; grid-template-columns:1fr 1.4fr; gap:8px; margin-top:12px }}
  .test-row select {{ width:100%; font-family:system-ui,sans-serif; font-size:0.85rem }}
  #customModel {{ width:100%; margin-top:8px }}
  .qt-field {{ margin-top:10px }}
  .qt-label {{ color:var(--soft); font-size:0.8rem; display:block; margin-bottom:5px }}
  .qt-field textarea, .qt-field select {{ width:100%; font-family:system-ui,sans-serif }}
  .purpose-hint {{ color:var(--muted); font-size:0.78rem; margin-top:4px; line-height:1.4; font-style:italic }}

  /* Tabs (Speeds tab, 2026-09-19) */
  .tabs {{ display:flex; gap:4px; margin-bottom:18px; border-bottom:1px solid var(--line) }}
  .tab {{ padding:9px 16px; font-size:0.9rem; color:var(--soft); cursor:pointer; user-select:none; border-bottom:2px solid transparent }}
  .tab:hover {{ color:var(--text) }}
  .tab.active {{ color:var(--text); border-bottom-color:var(--accent) }}

  /* Link-folder card */
  .path-row {{ display:flex; gap:8px; margin-top:10px }}
  .path-row input {{ flex:1 }}
  .linked-row {{ display:flex; align-items:center; gap:8px; color:var(--ok-text); font-size:0.85rem; flex-wrap:wrap }}
  .linked-row .path {{ color:var(--muted); font-family:monospace; font-size:0.78rem }}
  .detect-row {{ display:flex; align-items:center; gap:8px; color:var(--soft); font-size:0.85rem; margin-top:10px }}

  /* Speed rows -- each model gets its own bordered card so several in a row read as separate
     sections at a glance, not one long undifferentiated list. */
  .speed-row {{ padding:16px; margin:12px 0; border:1px solid var(--line); border-radius:8px; background:color-mix(in srgb, var(--bg) 30%, transparent) }}
  .speed-row:first-of-type {{ margin-top:0 }}
  .speed-row:last-of-type {{ margin-bottom:0 }}
  .speed-head {{ display:flex; flex-direction:column; align-items:flex-start; gap:6px }}
  .model-name {{ font-family:monospace; font-size:0.9rem; color:var(--text) }}
  .roles {{ display:flex; gap:6px; flex-wrap:wrap }}
  .role {{ font-size:0.72rem; padding:2px 9px; border-radius:9px; border:1px solid; white-space:nowrap }}
  .role.dialogue {{ color:var(--role-dialogue); border-color:color-mix(in srgb, var(--role-dialogue) 38%, transparent); background:color-mix(in srgb, var(--role-dialogue) 8%, transparent) }}
  .role.diary {{ color:var(--role-diary); border-color:color-mix(in srgb, var(--role-diary) 38%, transparent); background:color-mix(in srgb, var(--role-diary) 8%, transparent) }}
  .role.vision {{ color:var(--role-vision); border-color:color-mix(in srgb, var(--role-vision) 38%, transparent); background:color-mix(in srgb, var(--role-vision) 8%, transparent) }}
  .role.gamemaster {{ color:var(--role-gm); border-color:color-mix(in srgb, var(--role-gm) 38%, transparent); background:color-mix(in srgb, var(--role-gm) 8%, transparent) }}
  .role.other {{ color:var(--soft); border-color:color-mix(in srgb, var(--line2) 38%, transparent); background:color-mix(in srgb, var(--line) 19%, transparent) }}
  .speed-stats {{ display:flex; gap:18px; margin-top:10px; align-items:baseline; flex-wrap:wrap }}
  .stat .n {{ font-size:1.05rem; font-weight:600; color:var(--text) }}
  .stat .l {{ display:block; color:var(--muted); font-size:0.72rem; text-transform:uppercase; letter-spacing:0.03em; margin-top:2px }}
  .verdict {{ display:flex; align-items:center; gap:6px; font-size:0.8rem; margin-top:8px }}
  .verdict.ok-text {{ color:var(--ok) }} .verdict.warn-text {{ color:var(--warn) }} .verdict.bad-text {{ color:var(--err) }}
  .sparkline {{ display:flex; gap:2px; align-items:flex-end; height:28px; margin-top:10px }}
  .bar {{ width:5px; background:var(--accent); border-radius:1px; opacity:0.8 }}
  .bar.slow {{ background:var(--err) }}
  .bar.bucket {{ width:9px; border-radius:2px 2px 0 0 }}
  .bucket-hint {{ color:var(--line2); font-size:0.68rem; margin-top:4px }}
  .calls {{ color:var(--muted); font-size:0.75rem; margin-top:6px }}

  /* Speeds history range picker (2026-09-20) */
  .range-row {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; flex-wrap:wrap; gap:10px }}
  .range-picker {{ display:flex; gap:4px; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:3px }}
  .range-opt {{ padding:6px 12px; font-size:0.8rem; color:var(--soft); border-radius:6px; cursor:pointer; user-select:none }}
  .range-opt:hover {{ color:var(--text) }}
  .range-opt.active {{ background:var(--accent); color:var(--on-accent) }}

  /* Tracker callout */
  .callout {{ border:1px dashed var(--line2) }}
  .callout h3 {{ display:flex; align-items:center; gap:8px }}
  .callout p {{ color:var(--body); font-size:0.85rem; line-height:1.6; margin:8px 0 12px }}
  .callout-actions {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap }}
  .toggled {{ color:var(--ok); font-size:0.85rem; display:none; align-items:center; gap:6px }}

  /* Thresholds panel */
  .thresh-toggle {{ color:var(--accent-text); font-size:0.78rem; cursor:pointer; user-select:none; display:inline-flex; align-items:center; gap:4px }}
  .thresh-toggle:hover {{ color:var(--text) }}
  .thresh-panel {{ margin-top:14px; padding:14px; background:color-mix(in srgb, var(--bg) 50%, transparent); border:1px solid var(--line); border-radius:8px; display:none }}
  .thresh-row {{ display:grid; grid-template-columns:1fr auto 90px auto 90px; gap:8px; align-items:center; padding:6px 0 }}
  .thresh-row .rl {{ font-size:0.82rem; color:var(--text) }}
  .thresh-row .tx {{ color:var(--muted); font-size:0.78rem }}
  .thresh-row input {{ width:100%; font-size:0.8rem; padding:6px 8px }}
  .thresh-save {{ display:flex; justify-content:flex-end; gap:8px; margin-top:10px; align-items:center }}
  .saved-msg {{ color:var(--ok); font-size:0.8rem; display:none; align-items:center; gap:6px }}
  .speeds-empty {{ color:var(--soft); font-size:0.85rem; text-align:center; padding:30px 10px; line-height:1.6 }}

  /* Speeds "By model / By role" compare view (2026-09-20) */
  .view-toggle {{ display:flex; gap:4px; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:3px; width:fit-content; margin-bottom:16px }}
  .view-opt {{ padding:6px 14px; font-size:0.8rem; color:var(--soft); border-radius:6px; cursor:pointer; user-select:none }}
  .view-opt:hover {{ color:var(--text) }}
  .view-opt.active {{ background:var(--accent); color:var(--on-accent) }}
  .role-row {{ display:flex; align-items:center; gap:10px; margin:4px 0 16px; flex-wrap:wrap }}
  .role-row select {{ min-width:220px; font-family:system-ui,sans-serif; font-size:0.85rem }}
  .cmp-row {{ display:flex; align-items:center; gap:16px; padding:14px 16px; margin:10px 0; border:1px solid var(--line); border-radius:8px; background:color-mix(in srgb, var(--bg) 30%, transparent) }}
  .cmp-rank {{ font-family:monospace; color:var(--muted); font-size:0.85rem; width:20px; flex-shrink:0 }}
  .cmp-main {{ flex:1; min-width:0 }}
  .cmp-model {{ font-family:monospace; font-size:0.88rem; color:var(--text) }}
  .cmp-period {{ color:var(--muted); font-size:0.75rem; margin-top:2px }}
  .cmp-stats {{ display:flex; gap:16px; flex-shrink:0 }}
  .cmp-stat .n {{ font-size:0.95rem; font-weight:600; color:var(--text) }}
  .cmp-stat .l {{ display:block; color:var(--muted); font-size:0.68rem; text-transform:uppercase; letter-spacing:0.03em }}
  .cmp-row.current {{ border-color:color-mix(in srgb, var(--accent) 50%, transparent) }}
  .now-badge {{ font-size:0.68rem; color:var(--accent-text); border:1px solid color-mix(in srgb, var(--accent) 38%, transparent); background:color-mix(in srgb, var(--accent) 8%, transparent); border-radius:8px; padding:1px 8px; margin-left:8px }}

  /* Speeds v2 (2026-09-27) -- from design/dashboard-speeds-v2-mockup.html, Revision 2. Class names are
     prefixed/namespaced (lv-*, hverdict, sv-*, rtab, mcard) because .ok/.warn/.bad/.dot/.stat/.tab
     already mean something else elsewhere on this page. */
  .sv-toolbar {{ display:flex; justify-content:flex-start; align-items:center; flex-wrap:wrap; gap:10px 28px; margin-bottom:18px }}  /* grouped on the left: spreading them edge to edge on a wide page left gaps nobody could follow */
  .sv-gap {{ margin-top:14px }}
  .hdot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; background:var(--muted) }}
  .lv-ok {{ color:var(--ok) }} .lv-ok .hdot {{ background:var(--ok) }}
  .lv-warn {{ color:var(--warn) }} .lv-warn .hdot {{ background:var(--warn) }}
  .lv-bad {{ color:var(--err) }} .lv-bad .hdot {{ background:var(--err) }}
  .lv-none {{ color:var(--soft) }}
  .health-head {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:4px }}
  .health-head select {{ margin-left:6px; font-family:system-ui,sans-serif; font-size:0.8rem; padding:5px 8px }}
  .section-hint {{ color:var(--soft); font-size:0.85rem; margin:4px 0 16px }}
  .health-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:12px }}
  .hcard {{ border:1px solid var(--line); border-radius:8px; padding:12px 14px; background:color-mix(in srgb, var(--bg) 30%, transparent) }}
  .hcard .name {{ font-weight:600; font-size:0.9rem; color:var(--text) }}
  .hcard .why {{ color:var(--soft); font-size:0.75rem }}
  .hverdict {{ display:inline-flex; align-items:center; gap:6px; font-size:0.75rem; margin:6px 0 4px }}
  .mcard {{ border:1px solid var(--line); border-radius:8px; padding:16px 18px; margin:14px 0; background:color-mix(in srgb, var(--bg) 30%, transparent) }}
  .mcard:first-of-type {{ margin-top:0 }}
  .mhead {{ display:flex; justify-content:space-between; align-items:flex-start; gap:10px; flex-wrap:wrap }}
  .mcard .via {{ color:var(--muted); font-size:0.75rem; margin-top:2px }}
  .where-tag {{ font-family:system-ui,sans-serif; font-size:0.68rem; border:1px solid var(--line); border-radius:8px; padding:0 7px; margin-left:8px; color:var(--muted); white-space:nowrap }}
  .rel {{ display:inline-flex; align-items:center; gap:6px; font-size:0.75rem; border-radius:10px; padding:2px 10px; white-space:nowrap; border:1px solid var(--line) }}
  .rel.lv-ok {{ background:color-mix(in srgb, var(--ok) 16%, transparent); border-color:color-mix(in srgb, var(--ok) 45%, transparent) }}
  .rel.lv-warn {{ background:color-mix(in srgb, var(--warn) 16%, transparent); border-color:color-mix(in srgb, var(--warn) 45%, transparent) }}
  .rel.lv-bad {{ background:color-mix(in srgb, var(--err) 16%, transparent); border-color:color-mix(in srgb, var(--err) 45%, transparent) }}
  .sv-stats {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:14px; margin:16px 0 10px }}
  .sv-stat .n {{ display:block; font-size:1.15rem; font-weight:600; color:var(--text) }}
  .sv-stat .n small {{ font-size:0.75rem; color:var(--soft); font-weight:400 }}
  .sv-stat .l {{ display:block; color:var(--soft); font-size:0.72rem; margin-bottom:2px }}
  .sv-stat .v {{ font-size:0.72rem; margin-top:2px }}
  .sv-stat .v.muted {{ color:var(--muted) }}
  .range {{ position:relative; height:6px; background:var(--line); border-radius:3px; margin:8px 0 4px }}
  .range .span {{ position:absolute; top:0; height:6px; border-radius:3px; background:linear-gradient(90deg,color-mix(in srgb, var(--ok) 60%, transparent),color-mix(in srgb, var(--warn) 60%, transparent)) }}
  .range .mid {{ position:absolute; top:-3px; width:12px; height:12px; border-radius:50%; background:var(--text); border:2px solid var(--bg); transform:translateX(-50%) }}
  .range-l {{ display:flex; justify-content:space-between; color:var(--muted); font-size:0.7rem }}
  .strip {{ display:flex; gap:2px; align-items:flex-end; height:26px; margin:6px 0 4px }}
  .strip i {{ flex:1; background:color-mix(in srgb, var(--accent) 67%, transparent); border-radius:1px; min-width:3px }}
  .strip i.err {{ background:var(--err) }}
  .errline {{ color:var(--err); font-size:0.75rem; margin-top:6px }}
  .repairline {{ color:var(--soft); font-size:0.75rem; margin-top:6px }}
  .rtabs {{ display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 14px }}
  .rtab {{ font-size:0.8rem; padding:5px 12px; border:1px solid var(--line); border-radius:6px; color:var(--body); cursor:pointer; user-select:none }}
  .rtab:hover {{ border-color:var(--line2) }}
  .rtab.active {{ border-color:var(--accent); background:color-mix(in srgb, var(--accent) 19%, transparent); color:var(--text) }}
  .role-explain {{ background:var(--bg); border-left:3px solid var(--accent); padding:10px 14px; border-radius:0 6px 6px 0; font-size:0.85rem; color:var(--body); margin-bottom:14px }}
  .role-table {{ width:100%; border-collapse:collapse; font-size:0.82rem }}
  .role-table th {{ text-align:left; color:var(--muted); font-weight:500; font-size:0.72rem; padding:8px 8px; border-bottom:1px solid var(--line) }}
  .role-table th.hotcell {{ color:var(--accent-text) }}
  .role-table td {{ padding:12px 8px; border-bottom:1px solid var(--panel); vertical-align:top }}
  .role-table td.hotcell {{ color:var(--text); font-weight:600 }}
  .role-table td.mono {{ font-family:monospace }}
  .role-table .hverdict {{ margin:0 }}
  #threshPanel {{ margin-bottom:14px }}
  .role-table tr.current td {{ background:color-mix(in srgb, var(--accent) 7%, transparent) }}
  .share {{ display:flex; align-items:center; gap:8px }}
  .share .sbar {{ height:5px; background:var(--accent); border-radius:3px }}
  .ext {{ color:var(--accent-text); font-size:0.8rem; text-decoration:none }}
  .ext:hover {{ text-decoration:underline }}
  @media (max-width:600px) {{ .sv-stats {{ grid-template-columns:1fr 1fr }} }}
{local_models_ui.LOCAL_MODELS_CSS}
{jump_ui.JUMP_CSS}
{local_settings_ui.LOCAL_SETTINGS_CSS}
{decision_ui.DECISION_CSS}
{models_ui.MODELS_CSS}
{overview_ui.CSS}
{other_pcs_ui.OTHER_PCS_CSS}
{voice_ui.VOICE_CSS}
{routing_ui.ROUTING_CSS}
{speeds_time_ui.SPEEDS_TIME_CSS}
{speeds_local_ui.SPEEDS_LOCAL_CSS}
{prompt_size_ui.PROMPT_SIZE_CSS}
{selection_ui.SELECTION_CSS}
{help_ui.HELP_CSS}
{quicktest_ui.CSS}
{decision_test_ui.CSS}
{skyrimnet_check_ui.CSS}
{quick_sections_ui.CSS}
{tab_desc_ui.CSS}
{address_rows_ui.CSS}
{settings_ui.CSS}
{gpu_choice_ui.CSS}
{get_started_ui.CSS}
{set_me_up_ui.CSS}
{start_here_ui.CSS}
{model_groups_ui.MODEL_GROUPS_CSS}
</style></head>
<body>
  <h1>SkyrimNet MultiProxy</h1>
  <div class="subtitle">Connect any AI to SkyrimNet—cloud or local, paid or free.</div>
{speed_tabs_html}
{overview_ui.PANE_HTML}
  <div id="paneSetup" style="display:none">
{tab_desc_ui.html('setup')}  <div class="card su-start" data-sh-static="setup">
    <h3>Start here</h3>
    <ol class="su-steps">
      <li>Pick a provider below that you already pay for or have a key for.</li>
      <li>Paste your key and click Save (or Sign in).</li>
      <li>Wait for the dot next to its name to turn green.</li>
      <li>Copy a model name from the list.</li>
      <li>Paste the model name and MultiProxy&rsquo;s address into SkyrimNet.</li>
    </ol>
  </div>

  <div class="card">
    <div class="card-head">
      <h3>Choose a provider</h3>
      <span class="label" id="readyCount">{ready_count} of {len(providers)} ready</span>
    </div>
    <div id="noneReady" style="display:none">
      <div class="su-banner">No providers are ready yet. Pick one below to get started.</div>
      <div class="su-box"><h4>First time here?</h4><ol class="su-steps">
        <li>If the Get started page opens, follow its steps.</li>
        <li>If it did not open, go to the Providers tab and pick one provider.</li>
        <li>Add its key or sign in, then wait for the green dot.</li>
        <li>Open Settings once and check the Skyrim mod and folder.</li>
      </ol></div>
    </div>
    <div id="chips"></div>

    <div class="picker-row">
      <b id="providerName"></b>
      <span class="pill" id="providerPill"></span>
    </div>

    <div class="panel" id="panel"></div>
  </div>
  </div>
  <div id="paneLocal" style="display:none">
{tab_desc_ui.html('local')}{jump_ui.row('lmJump')}{local_models_ui.LOCAL_MODELS_CARD_HTML}{local_settings_ui.LOCAL_SETTINGS_HTML}
{decision_ui.LOCAL_MODELS_LINE_HTML}
  </div>
  <div id="paneQuick" style="display:none">
{tab_desc_ui.html('quick')}  <div id="qsList"></div>
  <div id="qsBodies" hidden>
  <div id="qsBody-provider"><div class="card">
    <h3>Try a provider before you play.</h3>
    <div class="su-box" data-sh-static="quick-test"><h4>Start here</h4><ol class="su-steps">
      <li>Pick a provider and model.</li>
      <li>Keep the sample scenario or pick another.</li>
      <li>Press Send.</li>
      <li>Read the reply in the chat window. Type more to keep talking.</li>
    </ol></div>
    <div class="test-row">
      <select id="testProvider"></select>
      <select id="testModel"></select>
    </div>
    <input id="customModel" placeholder="Type a model, e.g. " hidden>
    <div class="qt-field" style="margin-top:10px">
      <label class="qt-label">Scenario</label>
      <select id="testScenario" onchange="applyScenario()"></select>
      <div class="purpose-hint" id="scenarioPurpose"></div>
    </div>
    <div class="qt-field">
      <label class="qt-label">Background</label>
      <textarea id="sysPrompt" rows="2"></textarea>
    </div>
    <div class="qt-field">
      <label class="qt-label">Prompt</label>
      <textarea id="userMsg" rows="2"></textarea>
    </div>
    <div class="qt-field">
      <label class="qt-label">Additional context <span style="color:var(--muted); font-weight:normal">(optional)</span></label>
      <textarea id="extraContext" rows="3" placeholder="Add memories or recent events to see how the model reacts."></textarea>
    </div>
    <button style="margin-top:12px" onclick="openQuickTestPanel()" id="testBtn">Send</button>
  </div></div>
  <div id="qsBody-decision">
{decision_test_ui.PANE_HTML}  </div>
  <div id="qsBody-check">
{skyrimnet_check_ui.PANE_HTML}  </div>
  </div>
  </div>
{speed_pane_html}
{other_pcs_ui.OTHER_PCS_PANE_HTML}
{voice_ui.VOICE_PANE_HTML}
{decision_ui.DECISION_PANE_HTML}
{models_ui.MODELS_PANE_HTML}
{routing_ui.ROUTING_PANE_HTML}
{help_ui.HELP_PANE_HTML}
{settings_ui.PANE_HTML}
<template id="stConnTemplate">
  <div class="st-conn">
{version_mismatch_html}
    <div class="addr-row" id="addrName" style="margin-top:8px"><span class="label addr-name">Name</span><div class="endpoint" style="display:flex; justify-content:space-between; align-items:center; gap:10px"><span>{SKYRIMNET_PROVIDER_NAME}</span><button class="copy" onclick="copyModel(this, '{SKYRIMNET_PROVIDER_NAME}')">Copy</button></div></div>
      <div class="addr-row" id="addrChat" style="margin-top:8px"><span class="label addr-name">Chat</span><div class="endpoint" style="display:flex; justify-content:space-between; align-items:center; gap:10px"><span>http://127.0.0.1:{MAIN_PORT}/v1/chat/completions</span><button class="copy" onclick="copyModel(this, 'http://127.0.0.1:{MAIN_PORT}/v1/chat/completions')">Copy</button></div><div class="addr-hint">Paste into SkyrimNet under AI &gt; Providers, API Endpoint (API Style: Chat Completions).</div></div>
    <div id="addrRows"></div>
  </div>
</template>
{quicktest_ui.MODAL_HTML}
{get_started_ui.PANE_HTML}
<script>
// Every request this page makes to its own server carries X-MultiProxy: 1 (local_guard.py refuses a change without it: another website cannot add it).
(function () {{
  const realFetch = window.fetch;
  window.fetch = function (input, init) {{
    try {{
      const url = new URL(typeof input === 'string' ? input : (input.url || String(input)), location.href);
      if (url.origin === location.origin) {{
        init = Object.assign({{}}, init || {{}});
        const h = new Headers(init.headers || (typeof input !== 'string' && input.headers) || {{}});
        h.set('X-MultiProxy', '1');
        init.headers = h;
      }}
    }} catch (e) {{}}
    return realFetch.call(this, input, init);
  }};
}})();
{selection_ui.SELECTION_JS}
const PROVIDERS = {providers_json};
const PICKER_GROUPS = {_script_json([list(g) for g in PICKER_GROUPS])};
const SH_HIDDEN = {_script_json(_hidden_start_here())};
{start_here_ui.JS}
const HELP_FIRST_RUN = {_script_json(help_first_run)};
const GET_STARTED_FIRST_RUN = {_script_json(get_started_first_run)};
const HELP_PROVIDERS = {_script_json([m[1] for m in help_content.PAGES.values() if m[1]])};
// The button is only for a provider that is not set up yet, and it opens the Help page in a new window (the page's own #help/<id> address).
const helpBtn = id => (HELP_PROVIDERS.includes(id) && !((PROVIDERS.find(q => q.id === id) || {{}}).state === 'ok')) ? `<a class="su-howto" href="#help/${{id}}" target="_blank" rel="noopener">How to set this up</a>` : '';
// "(More in Help)" in a provider's help line becomes a link to that provider's Help page.
const moreHelp = (text, id) => HELP_PROVIDERS.includes(id)
  ? text.replace('(More in Help)', `(<a onclick="openHelp('${{id}}')">More in Help</a>)`)
  : text.replace(' (More in Help)', '');
const SU_KEYED = ['glm', 'nano', 'openai', 'deepseek', 'kilo', 'gemini', 'xai', 'continuum', 'typesafe', 'perplexity'{ks_su}];
const SU_SIGNIN = ['claude', 'codex', 'geminicli', 'grok'];
const SU_STEPS = {{
  key: ["Open the provider's link below and make a key.", "Paste the key here and press Save.", "Check that the status says the key is saved.", "Copy a model name and paste it into SkyrimNet."],
  signin: ["Install the program if it says Not installed.", "Open the program once on your device and sign in.", "Back here, press Check for models until the dot is green.", "Copy a model name and paste it into SkyrimNet."],
  own: ["Install or start the program on this device (or your other device).", "Make sure it has at least one model ready.", "Press Check for models until the dot is green.", "Copy the model name and paste it into SkyrimNet."],
}};
function suSteps(id) {{
  if (id === 'lmstudio' && typeof lmsSteps === 'function') return lmsSteps();
  const k = SU_KEYED.includes(id) ? 'key' : SU_SIGNIN.includes(id) ? 'signin' : 'own';
  return shBox('setup-' + id, '<div class="su-box"><h4>Start here</h4><ol class="su-steps">' + SU_STEPS[k].map(t => '<li>' + t + '</li>').join('') + '</ol></div>');
}}
const SU_ANY = 'You can use any model from this provider. Type its name directly into SkyrimNet.';
const SU_COPY_HINT = '<div class="help su-copyhint">Copy a model name and paste it into the Model box in SkyrimNet.</div>';
const DEFAULT_PROVIDER = {_script_json(default_provider)};
const DEFAULT_MODEL_ID = {_script_json(_default_claude_model())};
const CODEX_PREFIX = {_script_json(CODEX_PREFIX)};
const MARK = {{ ok:'✓', warn:'!', off:'○' }};
const COLOR = {{ ok:'var(--ok-text)', warn:'var(--warn-text)', off:'var(--muted)' }};
const $ = id => document.getElementById(id);
{model_groups_ui.MODEL_GROUPS_LOGIC_JS}
{model_groups_ui.MODEL_GROUPS_RENDER_JS}
const byId = id => PROVIDERS.find(p => p.id === id);
let codexSelectedId = 'default';  // which Codex sub-provider this panel + Quick Test currently follow
let codexActiveTab = 'default';  // which of the 4 Codex tabs (default/add-builtin/add-custom/pin-model) is shown, preserved across renderCodexPanel() re-renders (e.g. after a pin/unpin or provider save)

// Quick Test scenarios (2026-09-16) -- verbatim from design/quick-test-scenarios.md, the director's
// own source doc. 'custom' (today's existing default behavior) leaves System/Prompt exactly as
// last typed; picking a named one is a one-time fill, never a locked template.
const SCENARIOS = {{
  custom: {{ label: 'Custom (write your own)', sys: '', user: '', purpose: '' }},
  1: {{
    label: '1. The Faithful Follower (Lydia)',
    sys: 'You are Lydia, a stalwart Nord housecarl sworn to protect the Dragonborn. Stay in character. Keep responses under 2 sentences.',
    user: 'What do you think of dragons?',
    purpose: 'Tests direct follower dialogue and character personality.',
  }},
  2: {{
    label: '2. The Cynical Merchant (Belethor)',
    sys: 'You are Belethor, the slick, opportunistic general merchant of Whiterun. Stay in character and keep it concise.',
    user: "What's the best deal you have in the shop today?",
    purpose: 'Tests humor, sleazy charm, and handling vendor-style interactions.',
  }},
  3: {{
    label: '3. Environmental Atmosphere (Whiterun Plains)',
    sys: 'You are a sensory scene narrator for Skyrim. Describe environments vividly in 2 to 3 evocative sentences, focusing on sights, sounds, and weather.',
    user: 'Describe standing on the tundra plains just outside Whiterun as evening approaches.',
    purpose: 'Tests descriptive depth, mood, lighting, and ambient immersion without NPC dialogue.',
  }},
  4: {{
    label: '4. Two-Character Banter (Nazeem & Carlotta Valentia)',
    sys: 'You are a scriptwriter for Skyrim NPC interactions. Output a natural, brief 3-line dialogue exchange between two Whiterun citizens.',
    user: 'Write a brief market interaction where Nazeem tries to impress Carlotta Valentia, and she immediately shuts him down.',
    purpose: 'Tests multi-character dialogue generation, pacing, and formatting.',
  }},
  5: {{
    label: '5. Scholarly Lore & Philosophy (Paarthurnax)',
    sys: 'You are Paarthurnax, an ancient dragon meditating atop the Throat of the World. Speak slowly, incorporating Dovahzul words where appropriate. Stay in character.',
    user: 'Is it better to be born good, or to overcome your evil nature through great effort?',
    purpose: 'Tests philosophical reasoning, pacing, and dialect/custom vocabulary handling.',
  }},
  6: {{
    label: "6. Tavern Rumors & Storytelling (Jarl's Court)",
    sys: 'You are an innkeeper at The Bannered Mare in Whiterun. You share rumors and local news with weary travelers in a rustic tone.',
    user: 'Heard any good rumors around the hold lately?',
    purpose: 'Tests narrative world-building, quest hooks, and conversational lore recall.',
  }},
}};

// Explicit order, not Object.keys(SCENARIOS) -- JS always iterates integer-like object keys
// ('1'..'6') ascending BEFORE plain string keys ('custom'), regardless of insertion order, which
// would silently put Custom last instead of first.
const SCENARIO_ORDER = ['custom', '1', '2', '3', '4', '5', '6'];

function applyScenario() {{
  const s = SCENARIOS[$('testScenario').value];
  $('sysPrompt').value = s.sys;
  $('userMsg').value = s.user;
  $('scenarioPurpose').textContent = s.purpose;
}}

// The pills (2026-10-03): three groups by how you pay. Each provider arrives with its own `group`, short `pill` name and optional quiet `tag`
// (see _PICKER_INFO), so nothing here knows a provider by name. Inside a group, set-up ones come first, then not-set-up, each alphabetical: a pill
// only moves when it goes from set up to not set up (or back), never on a refresh.
let selectedProvider = null;
function pillLabel(p) {{
  return p.pill || p.name;
}}
function isSetUp(p) {{ return p.state !== 'off'; }}

function renderChips(active) {{
  if (active === undefined) active = selectedProvider;
  const byOrder = (a, b) => (isSetUp(b) - isSetUp(a)) || pillLabel(a).localeCompare(pillLabel(b));
  $('chips').innerHTML = PICKER_GROUPS.map(g => {{
    const list = PROVIDERS.filter(p => p.group === g[0]).sort(byOrder);
    if (!list.length) return '';
    return `<div class="grp"><div class="grp-h">${{g[1]}} <span>${{g[2]}}</span></div><div class="chips">` + list.map(p =>
      `<button type="button" class="chip ${{p.state==='off'?'off':''}} ${{p.id===active?'active':''}}" data-id="${{p.id}}" aria-pressed="${{p.id===active}}"><span class="dot ${{p.state}}"></span>${{pillLabel(p)}}${{p.tag ? ` <span class="tag">${{p.tag}}</span>` : ''}}</button>`).join('') + `</div></div>`;
  }}).join('');
  $('chips').querySelectorAll('.chip').forEach(c => c.onclick = () => {{ try {{ history.replaceState(null, '', '#setup/' + c.dataset.id); }} catch (e) {{}} selectProvider(c.dataset.id); }});
  const ready = PROVIDERS.filter(p => p.state==='ok').length;
  $('readyCount').textContent = `${{ready}} of ${{PROVIDERS.length}} ready`;
  $('noneReady').style.display = ready ? 'none' : '';
}}

function renderPanel(p) {{
  if (p.id === 'codex') {{ renderCodexPanel(p); return; }}
  const clearBtn = p.hasKey ? `<button class="ghost" onclick="clearKeyReal('${{p.id}}')">Clear</button>` : '';
  const keyRow = p.keyless ? '' : `
    <div class="key-row">
      <input type="password" id="keyInput" placeholder="${{p.placeholder}}">
      <button onclick="saveKey('${{p.id}}')">Save</button>${{clearBtn}}
    </div><div id="keyCheck"></div>`;
  // More than 5 models -> one row per family (model_groups_ui.py); 5 or fewer stay a flat list.
  const rows = renderModelListHTML(p.id, p.id, p.models, ([mid,name,note], inGroup) => `
    <div class="model">
      <span class="mid">${{_escapeHtml(mid)}}</span>
      <span><div class="name">${{_escapeHtml(name)}}</div>${{inGroup ? '' : `<div class="note">${{_escapeHtml(note)}}</div>`}}</span>
      <button class="copy" onclick="copyModel(this, '${{jsq(mid)}}')">Copy model name</button>
    </div>`);
  // Ollama's own `empty` guidance line (2026-09-13: "Not running" / "no models installed" /
  // "couldn't load the Cloud list") already says everything the generic any-model line would --
  // skip that redundant second line for Ollama specifically, while `p.any` still does its other
  // job of adding "Type your own..." to Quick Test's model dropdown.
  const any = (p.any && p.id !== 'ollama') ? `<div class="any-model">${{SU_ANY}}</div>` : '';
  const empty = p.empty ? `<div class="empty">${{p.empty}}</div>` : '';
  const refresh = p.live ? `<button class="copy" onclick="refreshLive(this, '${{p.id}}')">Check for models</button>` : '';
  const label = _escapeHtml(p.label || 'Available models');
  // A provider that is already set up has no set-up help: no button, no steps, no explanation line.
  const setUp = p.state === 'ok';
  $('panel').innerHTML = `${{setUp ? '' : helpBtn(p.id) + suSteps(p.id)}}${{keyRow}}
    ${{setUp ? '' : `<div class="help" style="${{p.keyless?'margin-top:0':''}}">${{moreHelp(p.help, p.id)}}</div>`}}
    ${{typeof claudeTroubleLine === 'function' ? claudeTroubleLine(p) : ''}}
    ${{typeof lmsPanelExtras === 'function' ? lmsPanelExtras(p) : ''}}
    <div class="models-head"><span class="label">${{label}}</span>${{refresh}}</div>
    ${{p.models.length ? SU_COPY_HINT : ''}}${{rows}}${{empty}}${{any}}`;
}}

// --- Codex multi-provider panel -- a plain provider gets one flat panel (renderPanel above);
// Codex gets an inner provider picker (default ChatGPT + every saved provider) plus a "Codex
// providers" management list with its own inline (non-modal) add/edit form, matching
// design/dashboard-codex-multi-provider-mockup.html. Quick Test follows whichever sub-provider is
// selected here (design note #4) by mutating the shared PROVIDERS 'codex' entry's own
// models/any in place -- fillTestModels()/testChat() stay completely unaware this exists.

function codexEntry(p, id) {{
  return p.codexProviders.find(c => c.id === id) || p.codexProviders[0];
}}

// Tab bar restored (2026-09-16) to match design/dashboard-codex-multi-provider-mockup.html's own
// approved 4-tab layout -- Default view / Add provider (built-in) / Add provider (custom) / Pin a
// model. Was flattened into one continuously-scrolling panel during the codex-multi-provider and
// codex-pin-a-model builds; that was fine while the account's own catalog was small, but it grew to
// 382 real, live entries (confirmed live -- OpenAI's own "list" visibility now covers hundreds of
// models, not a filtering bug here), so reaching the Pin field or the provider list meant scrolling
// past all 382 raw model rows first. Splitting the pin form and the add-provider form onto their
// own tabs means opening either is instant regardless of how many real models the account has.
const CODEX_TAB_IDS = ['default', 'add-builtin', 'add-custom', 'pin-model'];

function showCodexTab(id) {{
  codexActiveTab = id;
  CODEX_TAB_IDS.forEach(t => {{
    const el = $('codex-tab-' + t);
    if (el) el.style.display = (t === id) ? '' : 'none';
  }});
  const buttons = document.querySelectorAll('#codexTabs button');
  buttons.forEach((b, i) => b.classList.toggle('active', CODEX_TAB_IDS[i] === id));
}}

// Default view's own curated summary (2026-09-16, narrowed from rev 3's broad "any OpenAI-vendor
// model" filter, ~95 rows, down to exactly the four models OpenAI's own docs call out as
// "recommended": https://learn.chatgpt.com/docs/models?surface=cli -- Astra, Sol, Terra, Luna).
// Matched by FAMILY NAME (a token in the slug's own model-name segment), not a hardcoded full
// version string like "gpt-5.6-sol" -- OpenAI rolls each family to a new underlying model over
// time (confirmed live: the account's real catalog already carries `gpt-5.6-sol` AND a real
// `~openai/gpt-sol-latest` alias pointing at whatever's current), and the family name is the part
// that's actually stable branding.
//
// Confirmed live 2026-09-16 exactly which real slugs exist for each family, rather than assuming:
// every one of the four has BOTH a concrete versioned model (`openai/gpt-6-astra`,
// `openai/gpt-5.6-sol`, etc., plus their own `-pro`/`:batch` variants) AND a
// `~openai/gpt-<family>-latest` alias ("this model always redirects to the latest model in the
// <X> family"). The alias is preferred when both exist -- it's the one OpenAI itself keeps
// pointed at whatever's current, so this filter never needs touching again as each family rolls
// forward. A plain substring match on the family name is NOT enough on its own: the real catalog
// also has `upstage/solar-pro4`/`upstage/solar-pro-3` (Upstage's own unrelated "Solar" line, which
// contains "sol" as a substring) -- the vendor check below is what actually excludes those, not
// the family-name match by itself.
const CODEX_RECOMMENDED_FAMILIES = ['astra', 'sol', 'terra', 'luna'];

// rawId is already stripped of the codex/<provider>/ prefix (see codexRawId()). Returns
// {{family, isAlias}} for an OpenAI-vendor model whose own name contains one of the four
// recommended family names as a whole token, or null for anything else (including a bare,
// no-vendor slug -- e.g. the disclosed static CODEX_FALLBACK_MODELS entry, gpt-5.5 -- which never
// qualifies as "recommended" under this narrower rule even though rev 3's own broader first-party
// rule would have allowed it).
function codexRecommendedFamilyInfo(rawId) {{
  const isAlias = rawId.startsWith('~');
  const withoutAlias = isAlias ? rawId.slice(1) : rawId;
  if (!withoutAlias.includes('/')) return null;
  const [vendor, ...rest] = withoutAlias.split('/');
  if (vendor !== 'openai') return null;
  const tokens = rest.join('/').split(/[-:]/);
  const family = CODEX_RECOMMENDED_FAMILIES.find(fam => tokens.includes(fam));
  return family ? {{family, isAlias}} : null;
}}

// rawId is `mid` with this provider's own prefix (CODEX_PREFIX, or CODEX_PREFIX + providerId + '/'
// for a saved provider) already stripped off -- see codexRawId() below.
function codexRawId(mid, providerId) {{
  const stripPrefix = providerId === 'default' ? CODEX_PREFIX : `${{CODEX_PREFIX}}${{providerId}}/`;
  return mid.startsWith(stripPrefix) ? mid.slice(stripPrefix.length) : mid;
}}

// A pinned row's 4th tuple element (true, or the raw pinned id string) is the only "this is
// pinned" signal -- purple text via .model--pinned, and its note cell holds an Unpin button
// instead of plain note text. No separate badge/pill: redundant with the color and the Unpin
// button already only appearing here.
//
// name/note are escaped -- confirmed live (2026-09-16) that this is NOT theoretical: the real
// 382-entry catalog contains at least one description with a raw, unclosed '<' (morph-v3-large's
// own doc text truncates mid-tag as "...format: <instructi…"). Left unescaped, that single '<'
// corrupted the browser's own HTML parsing of the rest of the page -- every tab panel after the
// Models list silently got nested INSIDE the Default view tab instead of staying siblings, so
// switching to any other tab showed nothing (it was still there, just inside a display:none
// ancestor). `mid` stays unescaped: it's built entirely from proxy-controlled prefixes plus the
// catalog's own slug field, which is identifier-shaped (never seen containing HTML metacharacters
// across the real catalog), and is also reused as a JS string literal in the Copy/Unpin buttons.
function codexModelRowHTML([mid, name, note, pinned], providerId, noNote) {{
  return `
    <div class="model${{pinned ? ' model--pinned' : ''}}">
      <span class="mid">${{_escapeHtml(mid)}}</span>
      <span><div class="name">${{_escapeHtml(name)}}</div>${{noNote ? '' : `<div class="note">${{_escapeHtml(note)}}</div>`}}</span>
      ${{pinned
        ? `<button class="copy" title="Unpin" onclick="unpinCodexModel('${{jsq(providerId)}}','${{jsq(pinned)}}')">Unpin</button>`
        : `<button class="copy" onclick="copyModel(this, '${{jsq(mid)}}')">Copy model name</button>`}}
    </div>`;
}}

function renderCodexPanel(p) {{
  if (!p.codexProviders.some(c => c.id === codexSelectedId)) codexSelectedId = 'default';
  const active = codexEntry(p, codexSelectedId);
  p.models = active.models;
  p.any = active.any;

  const pickerOpts = p.codexProviders.map(c =>
    `<option value="${{c.id}}" ${{c.id===codexSelectedId?'selected':''}}>${{c.name}}</option>`).join('');

  // Default view's own Models list is a curated summary of exactly OpenAI's own four
  // "recommended" families (Astra/Sol/Terra/Luna), not the account's whole catalog nor even the
  // broader ~95-model OpenAI-vendor set rev 3 first landed on -- narrowed 2026-09-16 once the
  // director looked at 95 real rows and confirmed the actual bar is OpenAI's own docs, which name
  // exactly these four. One row per family (the `~openai/<family>-latest` alias when it exists,
  // confirmed live that it currently does for all four; a concrete non-alias match otherwise, so
  // this degrades gracefully if a future catalog refresh ever drops an alias), in that fixed
  // Astra/Sol/Terra/Luna order rather than wherever the account's own priority ordering happens to
  // place them. Pinned models ALWAYS survive alongside these four regardless of vendor -- pinning
  // is a deliberate choice, never hidden -- appended after, deduped against the four by id so a
  // pinned model that happens to BE one of the four recommended entries never shows twice. Only
  // applies to the implicit default provider; a saved provider's own panel (which never has a real
  // live catalog anyway, only its own pinned entries) is unfiltered.
  let defaultViewModels = active.models;
  if (codexSelectedId === 'default') {{
    const byFamily = new Map();
    active.models.forEach(m => {{
      const [mid, , , pinned] = m;
      if (pinned) return;  // a pinned model is handled entirely separately, below
      const info = codexRecommendedFamilyInfo(codexRawId(mid, active.id));
      if (!info) return;
      const existing = byFamily.get(info.family);
      if (!existing || (info.isAlias && !existing.isAlias)) {{
        byFamily.set(info.family, {{row: m, isAlias: info.isAlias}});
      }}
    }});
    const recommendedRows = CODEX_RECOMMENDED_FAMILIES.map(fam => byFamily.get(fam)).filter(Boolean).map(e => e.row);
    const recommendedMids = new Set(recommendedRows.map(m => m[0]));
    const pinnedForDefault = active.models.filter(m => m[3] && !recommendedMids.has(m[0]));
    defaultViewModels = [...recommendedRows, ...pinnedForDefault];
  }}
  const rows = renderModelListHTML('codex', 'codex|' + active.id, defaultViewModels, (m, inGroup) => codexModelRowHTML(m, active.id, inGroup));
  const any = active.any ? `<div class="any-model">${{SU_ANY}}</div>` : '';
  const refresh = (codexSelectedId === 'default' && p.live) ? `<button class="copy" onclick="refreshLive(this, 'codex')">Check for models</button>` : '';
  const label = codexSelectedId === 'default' ? (p.label || 'Models') : 'Models';
  const hasPinned = defaultViewModels.some(m => m[3]);
  const legend = hasPinned ? `<div class="help" style="margin-top:4px"><span class="pin-legend-dot"></span> Purple = a model you pinned yourself.</div>` : '';

  // Pin a model's own tab shows whatever's already pinned for this provider up top (same row shape
  // + Unpin button, unfiltered by the GPT-only rule above -- a pinned third-party model belongs
  // here just as much as a pinned first-party one).
  const pinnedOnly = active.models.filter(m => m[3]);
  const pinnedRows = renderModelListHTML('codex', 'codex-pins|' + active.id, pinnedOnly, (m, inGroup) => codexModelRowHTML(m, active.id, inGroup));
  const pinnedLegend = pinnedOnly.length
    ? `<div class="help" style="margin-bottom:14px"><span class="pin-legend-dot"></span> Purple = a model you've already pinned.</div>`
    : '';

  const listRows = p.codexProviders.map(c => `
    <div class="codex-row">
      <span><div class="name">${{c.name}}</div><div class="note">${{c.typeLabel}}${{c.note ? ' · ' + c.note : ''}}</div></span>
      <span class="actions">${{c.editable ? `<button class="ghost" onclick="openCodexEditForm('${{c.id}}')">Edit</button>` : ''}}${{c.removable ? `<button class="ghost" onclick="removeCodexProviderReal('${{c.id}}')">Remove</button>` : ''}}</span>
    </div>`).join('');

  const codexSetUp = p.state === 'ok';
  $('panel').innerHTML = `
    ${{codexInstallHtml(p)}}
    ${{codexSetUp ? '' : helpBtn('codex') + suSteps('codex')}}
    <div class="picker-row" style="margin-top:0">
      <select id="codexProviderPick" onchange="selectCodexProvider(this.value)">${{pickerOpts}}</select>
    </div>
    <div class="codex-tabs" id="codexTabs">
      <button onclick="showCodexTab('default')">Models list</button>
      <button onclick="openCodexAddForm('builtin')">Add built-in provider</button>
      <button onclick="openCodexAddForm('custom')">Add custom provider</button>
      <button onclick="showCodexTab('pin-model')">Add an unlisted model</button>
    </div>
    <div id="codex-tab-default">
      ${{codexSetUp ? '' : `<div class="help" style="margin-top:10px">${{moreHelp(active.help, 'codex')}}</div>`}}
      <div class="models-head"><span class="label">${{label}}</span>${{refresh}}</div>
      ${{rows.includes('class="model') ? SU_COPY_HINT : ''}}${{rows}}${{any}}${{legend}}
      <div class="pin-row">
        <input id="codexPinIdDefault" autocomplete="off" placeholder="Model name (like gpt-6-astra)">
        <input id="codexPinNameDefault" placeholder="Display name (optional)">
        <button class="copy" onclick="pinCodexModel('${{active.id}}', 'codexPinIdDefault', 'codexPinNameDefault')">Add model</button>
      </div>
      <div class="help" style="margin:6px 0 16px">Add a model that isn't listed here. ${{moreHelp('(More in Help)', 'codex')}}</div>
      <div class="codex-list-head"><span class="label">Extra providers</span><button class="copy" onclick="openCodexAddForm('builtin')">Add a provider</button></div>
      ${{listRows}}
    </div>
    <div id="codex-tab-add-builtin" style="display:none"></div>
    <div id="codex-tab-add-custom" style="display:none"></div>
    <div id="codex-tab-pin-model" style="display:none">
      ${{pinnedRows}}${{pinnedLegend}}
      <div class="pin-row" style="margin-top:0; padding-top:0; border-top:none">
        <input id="codexPinId" autocomplete="off" placeholder="Model name (like gpt-6-astra)">
        <input id="codexPinName" placeholder="Display name (optional)">
        <button class="copy" onclick="pinCodexModel('${{active.id}}')">Add model</button>
      </div>
      <div class="help" style="margin:6px 0 16px">Add a model that isn't listed, or pick one from the catalog below.</div>
      <input class="pin-search" id="pinSearch" placeholder="Search all models (${{active.models.length}})..." oninput="filterCodexVendorGroups(this.value)">
      <div class="vendor-group" id="vendorGroupsTop">
        <div class="vendor-head" onclick="toggleCodexVendorGroup(this)">
          <span><span class="vendor-chevron">▶</span><span class="vendor-name">All providers</span></span>
          <span class="vendor-count" id="vendorGroupsCount"></span>
        </div>
        <div class="vendor-models" id="vendorGroups"></div>
      </div>
    </div>`;
  showCodexTab(codexActiveTab);
  setupCodexPinSearch(active.models, active.id);
}}

// Vendor label overrides (2026-09-16) for the handful of real vendor keys that read badly under
// plain title-casing -- everything else falls through to a generic humanize (split on '-'/'_',
// capitalize each word). This list is a living, low-stakes polish pass, not an exhaustive registry
// -- a brand-new vendor showing up in a future catalog refresh still gets a perfectly readable,
// if less branded-looking, label with zero code changes required.
const CODEX_VENDOR_LABELS = {{
  openai: 'OpenAI / GPT', 'x-ai': 'xAI', 'z-ai': 'Z.ai', 'meta-llama': 'Meta (Llama)',
  'ibm-granite': 'IBM Granite', 'bytedance-seed': 'ByteDance Seed', 'nex-agi': 'NexAGI',
  'arcee-ai': 'Arcee AI', 'aion-labs': 'Aion Labs', 'nousresearch': 'NousResearch',
  'anthracite-org': 'Anthracite', 'thinkingmachines': 'Thinking Machines',
  'inclusionai': 'InclusionAI', moonshotai: 'MoonshotAI',
}};

function codexVendorLabel(vendor) {{
  if (CODEX_VENDOR_LABELS[vendor]) return CODEX_VENDOR_LABELS[vendor];
  return vendor.split(/[-_]/).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}}

// A bare (no-slash) raw id groups under the same "OpenAI / GPT" bucket as codexIsFirstParty()'s own
// no-slash-is-first-party rule -- one consistent "vendor" concept used both to filter Default
// view's own list and to bucket Pin a model's own browser, rather than two separately-invented rules.
function codexVendorFromRawId(rawId) {{
  if (!rawId.includes('/')) return 'openai';
  return rawId.split('/')[0].replace(/^~/, '');
}}

function toggleCodexVendorGroup(headEl) {{
  headEl.parentElement.classList.toggle('open');
}}

function resetCodexVendorGroups() {{
  const top = $('vendorGroupsTop');
  if (top) top.classList.remove('open');
  document.querySelectorAll('#vendorGroups .vendor-group').forEach(g => {{
    g.classList.remove('open');
    g.style.display = '';
    g.querySelectorAll('.vendor-models .model').forEach(m => {{ m.style.display = ''; }});
  }});
}}

function pickCodexModel(rawId) {{
  $('codexPinId').value = rawId;
  $('pinSearch').value = '';
  resetCodexVendorGroups();
}}

function filterCodexVendorGroups(query) {{
  const q = query.trim().toLowerCase();
  if (!q) {{ resetCodexVendorGroups(); return; }}
  let anyGroupMatched = false;
  document.querySelectorAll('#vendorGroups .vendor-group').forEach(group => {{
    const models = group.querySelectorAll('.vendor-models .model');
    let anyMatch = false;
    models.forEach(m => {{
      const match = m.textContent.toLowerCase().includes(q);
      m.style.display = match ? '' : 'none';
      if (match) anyMatch = true;
    }});
    group.style.display = anyMatch ? '' : 'none';
    group.classList.toggle('open', anyMatch);
    if (anyMatch) anyGroupMatched = true;
  }});
  const top = $('vendorGroupsTop');
  if (top) top.classList.toggle('open', anyGroupMatched);
}}

// Pin field browser (rev 3, 2026-09-16) -- replaces the flat native <datalist> (which dumped the
// whole ~382-entry catalog ungrouped the instant the field was focused) with a collapsed-by-
// default, grouped-by-vendor accordion built from the SAME active.models array the Default view's
// own list already renders from -- no new backend endpoint. Rows use data-model-id + a real click
// handler rather than an inline onclick string, so a vendor/model id never has to be escaped for
// safe embedding inside a JS string literal in an HTML attribute -- _escapeHtml() alone (already
// proven correct for a double-quoted HTML attribute) is all that's needed here.
function setupCodexPinSearch(models, providerId) {{
  const container = $('vendorGroups');
  if (!container) return;
  const groups = new Map();
  models.forEach(([mid, name, note]) => {{
    const rawId = codexRawId(mid, providerId);
    const vendor = codexVendorFromRawId(rawId);
    if (!groups.has(vendor)) groups.set(vendor, []);
    groups.get(vendor).push([rawId, name, note]);
  }});
  const sortedVendors = Array.from(groups.keys()).sort((a, b) => codexVendorLabel(a).localeCompare(codexVendorLabel(b)));
  const countEl = $('vendorGroupsCount');
  if (countEl) countEl.textContent = models.length;
  container.innerHTML = sortedVendors.map(vendor => {{
    const items = groups.get(vendor);
    // name+note share ONE grid column (the same nested span-wrapping-two-divs shape the real
    // .model rows elsewhere in this file already use) rather than each being its own top-level
    // grid item -- confirmed live 2026-09-16 that giving .note its own column blows out the whole
    // row: that 3rd column is an `auto` track sized for a short button/note, and a real
    // description (some run 150-200+ characters) forces it to claim nearly all the row's width,
    // squeezing the mid/name columns down to their minmax(0,...) floor -- one character per line.
    const modelRows = items.map(([rawId, name, note]) => `
      <div class="model" data-model-id="${{_escapeHtml(rawId)}}">
        <span class="mid">${{_escapeHtml(rawId)}}</span>
        <span><div class="name">${{_escapeHtml(name)}}</div><div class="note">${{_escapeHtml(note)}}</div></span>
      </div>`).join('');
    return `
      <div class="vendor-group" data-vendor="${{_escapeHtml(vendor)}}">
        <div class="vendor-head" onclick="toggleCodexVendorGroup(this)">
          <span><span class="vendor-chevron">▶</span><span class="vendor-name">${{_escapeHtml(codexVendorLabel(vendor))}}</span></span>
          <span class="vendor-count">${{items.length}}</span>
        </div>
        <div class="vendor-models">${{modelRows}}</div>
      </div>`;
  }}).join('');
  container.querySelectorAll('.vendor-models .model').forEach(el => {{
    el.onclick = () => pickCodexModel(el.dataset.modelId);
  }});
}}

function selectCodexProvider(id) {{
  codexSelectedId = id;
  renderCodexPanel(byId('codex'));
  fillTestModels();
}}

function codexPresetBaseUrl(preset) {{
  return {{ openai: 'https://api.openai.com/v1', ollama: 'http://localhost:11434/v1', lmstudio: 'http://localhost:1234/v1' }}[preset] || '';
}}

function openCodexAddForm(forcedType) {{ renderCodexForm(null, forcedType); }}

function openCodexEditForm(id) {{
  const entry = byId('codex').codexProviders.find(c => c.id === id);
  if (entry) renderCodexForm(entry);
}}

function closeCodexAddForm() {{
  const b = $('codex-tab-add-builtin'); if (b) b.innerHTML = '';
  const c = $('codex-tab-add-custom'); if (c) c.innerHTML = '';
  showCodexTab('default');
}}

function renderCodexForm(existing, forcedType) {{
  const isEdit = !!existing;
  const type = existing ? existing.type : (forcedType || 'builtin');
  const tabId = type === 'builtin' ? 'add-builtin' : 'add-custom';
  const disabledIfEdit = isEdit ? 'disabled' : '';

  // Both add-provider tabs share one form's worth of element ids (codexApiKey, codexPreset, etc.)
  // since only one is ever the live one at a time -- clear the other so a stale duplicate id can
  // never linger in the page and get picked up by $() by accident.
  $('codex-tab-add-builtin').innerHTML = '';
  $('codex-tab-add-custom').innerHTML = '';
  showCodexTab(tabId);

  $('codex-tab-' + tabId).innerHTML = `
    <div class="add-form">
      <div class="field">
        <label>Provider kind</label>
        <div class="radio-row">
          <label><input type="radio" name="codexType" value="builtin" ${{type==='builtin'?'checked':''}} ${{disabledIfEdit}} onchange="codexFormTypeChanged()"> Built-in</label>
          <label><input type="radio" name="codexType" value="custom" ${{type==='custom'?'checked':''}} ${{disabledIfEdit}} onchange="codexFormTypeChanged()"> Custom service</label>
        </div>
      </div>
      <div id="codexFormFields"></div>
      <div class="field-hint" id="codexFormError" style="color:var(--err)"></div>
      <div class="form-actions">
        <button class="ghost" onclick="closeCodexAddForm()">Cancel</button>
        <button onclick="saveCodexProvider(${{isEdit ? `'${{existing.id}}'` : 'null'}})">Save provider</button>
      </div>
    </div>`;
  renderCodexFormFields(type, {{
    preset: existing ? (existing.preset || 'openai') : 'ollama',
    name: existing ? existing.name : '',
    baseUrl: existing ? existing.baseUrl : '',
    isEdit,
  }});
}}

function codexFormTypeChanged() {{
  const type = document.querySelector('input[name="codexType"]:checked').value;
  renderCodexForm(null, type);
}}

function codexFormPresetChanged() {{
  renderCodexFormFields('builtin', {{preset: $('codexPreset').value, name: '', baseUrl: '', isEdit: false}});
}}

function clearCodexFormKey(e) {{
  e.preventDefault();
  const el = $('codexApiKey');
  el.value = '';
  el.dataset.forceClear = '1';
}}

function renderCodexFormFields(type, opts) {{
  const isEdit = opts.isEdit;
  const keyField = (placeholder) => `
    <div class="field">
      <label>API key${{type==='custom'?' (Leave blank if not needed)':''}}</label>
      <input type="password" id="codexApiKey" placeholder="${{placeholder}}">
      ${{isEdit ? `<div class="field-hint"><a href="#" onclick="clearCodexFormKey(event)">Clear saved key</a></div>` : ''}}
    </div>`;
  let html = '';
  if (type === 'builtin') {{
    const preset = opts.preset;
    html += `
      <div class="field">
        <label>Choose one</label>
        <select id="codexPreset" ${{isEdit?'disabled':''}} onchange="codexFormPresetChanged()">
          <option value="openai" ${{preset==='openai'?'selected':''}}>OpenAI</option>
          <option value="ollama" ${{preset==='ollama'?'selected':''}}>Ollama (This device)</option>
          <option value="lmstudio" ${{preset==='lmstudio'?'selected':''}}>LM Studio (This device)</option>
        </select>
      </div>
      <div class="field">
        <label>Address</label>
        <div class="readonly-value">${{codexPresetBaseUrl(preset)}}</div>
        <div class="field-hint">Filled in for you.${{preset!=='openai' ? ' Make sure the app is running before you test.' : ''}}</div>
      </div>`;
    if (preset === 'openai') html += keyField(isEdit ? 'Leave blank to keep current key' : 'sk-...');
  }} else {{
    html += `
      <div class="field"><label>Name</label><input type="text" id="codexName" placeholder="e.g. My provider" value="${{_escapeHtml(opts.name||'')}}" ${{isEdit?'disabled':''}}></div>
      <div class="field"><label>Address</label><input type="text" id="codexBaseUrl" placeholder="e.g. https://api.example.com/v1" value="${{_escapeHtml(opts.baseUrl||'')}}"></div>
      ${{keyField(isEdit ? 'Leave blank to keep current key' : 'sk-...')}}`;
  }}
  $('codexFormFields').innerHTML = html;
}}

async function saveCodexProvider(editId) {{
  const type = document.querySelector('input[name="codexType"]:checked').value;
  const body = {{type}};
  if (type === 'builtin') {{
    if (!editId) body.preset = $('codexPreset').value;
  }} else {{
    if (!editId) body.name = $('codexName').value.trim();
    const baseUrlEl = $('codexBaseUrl');
    if (baseUrlEl) body.base_url = baseUrlEl.value.trim();
  }}
  const keyEl = $('codexApiKey');
  if (keyEl) {{
    if (keyEl.value.trim()) body.api_key = keyEl.value.trim();
    else if (keyEl.dataset.forceClear) body.api_key = '';
  }}
  const errEl = $('codexFormError');
  try {{
    const url = editId ? `/config/codex-providers/${{editId}}` : '/config/codex-providers';
    const r = await fetch(url, {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(body)}});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Something went wrong.');
    // Land back on Default view so the saved/edited provider is immediately visible in the
    // "Codex providers" list, confirming the save actually happened -- with the tab bar restored,
    // staying on the (now-blank, form-torn-down) add-provider tab after a real success would leave
    // the user staring at nothing instead of seeing their own provider listed.
    codexActiveTab = 'default';
    await refreshCodexProviders();
  }} catch(e) {{
    if (errEl) errEl.textContent = e.message;
  }}
}}

async function removeCodexProviderReal(id) {{
  try {{
    const r = await fetch(`/config/codex-providers/${{id}}/remove`, {{method: 'POST'}});
    if (!r.ok) throw new Error('request failed');
    if (codexSelectedId === id) codexSelectedId = 'default';
    await refreshCodexProviders();
  }} catch(e) {{
    showPillError();
  }}
}}

async function refreshCodexProviders() {{
  const p = byId('codex');
  p.codexProviders = await (await fetch('/config/codex-provider-panel')).json();
  renderCodexPanel(p);
  updatePickerOption(p);
  fillTestModels();
}}

// idInputId/nameInputId default to the Pin a model tab's own pair -- Default view's own inline
// pin-row (2026-09-16) passes its own, differently-id'd inputs explicitly, since both tabs' pin
// rows exist in the DOM at once (one just display:none) and duplicate ids would make
// getElementById() silently always grab whichever pair comes first in document order.
async function pinCodexModel(provider, idInputId, nameInputId) {{
  idInputId = idInputId || 'codexPinId';
  nameInputId = nameInputId || 'codexPinName';
  const modelId = $(idInputId).value.trim();
  if (!modelId) return;
  const name = $(nameInputId).value.trim();
  try {{
    const r = await fetch('/config/codex-pin-model', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{provider, model_id: modelId, name}}),
    }});
    if (!r.ok) throw new Error('request failed');
    await refreshCodexProviders();
  }} catch(e) {{
    showPillError();
  }}
}}

async function unpinCodexModel(provider, modelId) {{
  try {{
    const r = await fetch('/config/codex-unpin-model', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{provider, model_id: modelId}}),
    }});
    if (!r.ok) throw new Error('request failed');
    await refreshCodexProviders();
  }} catch(e) {{
    showPillError();
  }}
}}

function copyModel(btn, mid) {{
  navigator.clipboard.writeText(mid).catch(() => {{}});
  const original = btn.textContent;
  btn.textContent = 'Copied';
  setTimeout(() => {{ btn.textContent = original; }}, 1200);
}}

function updatePickerOption(p) {{
  renderChips();                                   // (the pills are the picker now: redraw them with this provider's new state)
}}

function showPillError() {{
  $('providerPill').textContent = 'Error saving. Check the Help tab for details.';
  $('providerPill').style.color = 'var(--err-text)';
}}

async function _refreshLiveEntry(p) {{
  // A "live" provider's key toggles which target/catalog a request actually hits (Ollama: local
  // daemon vs Cloud; Gemini: no-key static list vs a real live /models call) -- re-check that
  // target's real state/models server-side via its own refreshEndpoint, rather than guessing a
  // status string client-side. The same real check each provider's own ↻ Refresh button uses.
  const r = await fetch(p.refreshEndpoint);
  if (r.ok) Object.assign(p, await r.json());
}}

// A key that doesn't start the way this provider's keys do is asked about first (never blocked): the question and its two buttons sit under the box.
function keyLooksWrong(p, key) {{ return !!(p.keyPrefix && !key.startsWith(p.keyPrefix)); }}
function askSaveAnyway(id) {{
  const p = byId(id);
  const box = $('keyCheck');
  if (!box) return;
  box.innerHTML = `<div class="help">This doesn't look like ${{p.keyKindName}} (they start with ${{p.keyPrefix}}). Save anyway?</div>
    <div class="key-row"><button onclick="saveKey('${{id}}', true)">Save anyway</button><button class="ghost" onclick="$('keyCheck').innerHTML = ''">Cancel</button></div>`;
}}
async function saveKey(id, confirmed) {{
  const p = byId(id);
  const key = $('keyInput').value.trim();
  if (!key) return;
  if (!confirmed && keyLooksWrong(p, key)) {{ askSaveAnyway(id); return; }}
  if (p.keyShape && !new RegExp(p.keyShape).test(key)) {{ $('keyCheck').innerHTML = '<div class="help">' + esc(p.keyShapeHelp || '') + '</div>'; return; }}
  try {{
    const r = await fetch(p.keyEndpoint, {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{key: key}})
    }});
    if (!r.ok) throw new Error('request failed');
    p.hasKey = true;
    if (p.live) {{
      await _refreshLiveEntry(p);
    }} else {{
      p.state = 'ok';
      p.testable = true;
      p.status = 'Key saved';
    }}
    updatePickerOption(p);
    selectProvider(id);
  }} catch(e) {{
    showPillError();
  }}
}}

async function clearKeyReal(id) {{
  const p = byId(id);
  try {{
    const r = await fetch(p.keyEndpoint, {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{key: ''}})
    }});
    if (!r.ok) throw new Error('request failed');
    p.hasKey = false;
    if (p.live) {{
      await _refreshLiveEntry(p);
    }} else {{
      p.state = 'off'; p.status = 'No key yet'; p.testable = false;
    }}
    updatePickerOption(p);
    selectProvider(id);
  }} catch(e) {{
    showPillError();
  }}
}}

async function refreshLive(btn, id) {{
  const original = btn.textContent;
  btn.textContent = 'Checking…';
  btn.disabled = true;
  try {{
    const p = byId(id);
    await _refreshLiveEntry(p);
    updatePickerOption(p);
    selectProvider(id);
  }} catch(e) {{
    btn.disabled = false;
    btn.textContent = original;
    showPillError();
  }}
}}

// Quick Test only offers providers that can actually answer right now -- `testable` is decided
// server-side in _dashboard_providers() (one boolean, not re-derived from `state` strings here).
const usable = p => p.testable;

function fillTestProviders() {{
  const keep = $('testProvider').value;
  const list = PROVIDERS.filter(usable);
  $('testProvider').innerHTML = list.length
    ? list.map(p => `<option value="${{p.id}}">${{p.name}}</option>`).join('')
    : `<option value="">No providers set up yet. Go to the Providers tab to add one first.</option>`;
  $('testProvider').disabled = !list.length;
  $('testBtn').disabled = !list.length;
  // If the previously-selected provider is still listed, keep it -- otherwise the browser already
  // defaults a freshly-rebuilt <select> to its first <option>, which is exactly the spec'd
  // fallback ("Quick Test falls back to the first listed provider").
  if (list.some(p => p.id === keep)) $('testProvider').value = keep;
}}

function selectProvider(id, opts) {{
  opts = opts || {{}};
  const p = byId(id);
  selectedProvider = id;
  $('providerName').textContent = p.pill || p.name;
  $('providerPill').textContent = p.status;
  $('providerPill').style.color = COLOR[p.state];
  renderChips(id);
  renderPanel(p);
  fillTestProviders();
  if (usable(p)) $('testProvider').value = id;   // Q1: follows the pick above, when it can answer
  fillTestModels(opts.preferredModel);
}}

function fillTestModels(preferred) {{
  const p = byId($('testProvider').value);
  if (!p) {{
    // Nothing testable at all -- empty, disabled model select, no custom-model box.
    $('testModel').innerHTML = '';
    $('testModel').disabled = true;
    $('customModel').hidden = true;
    return;
  }}
  $('testModel').disabled = false;
  // Quick Test sends a chat message: a model its provider says is not for chat (embeddings, decisions only) is not offered. A model whose kind is not known is offered as always.
  let opts = p.models.filter(([mid]) => !(p.kinds && p.kinds[mid]) || p.kinds[mid].includes('Chat')).map(([mid,name]) => `<option value="${{mid}}">${{name}}</option>`).join('');
  if (p.any) opts += `<option value="__custom">Type your own...</option>`;
  $('testModel').innerHTML = opts;
  if (preferred && $('testModel').querySelector(`option[value="${{preferred}}"]`)) {{
    $('testModel').value = preferred;
  }}
  $('customModel').placeholder = `Type a model, e.g. ${{p.any || ''}}`;
  toggleCustom();
}}
function toggleCustom() {{ $('customModel').hidden = $('testModel').value !== '__custom'; }}

function _escapeHtml(s) {{
  // Escapes quotes too (2026-10-06): the result is also used inside attribute values, and a model id from a provider's list can hold a quote.
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

{quicktest_ui.JS}
{decision_test_ui.JS}
{skyrimnet_check_ui.JS}
{quick_sections_ui.JS}
{address_rows_ui.JS}

$('testProvider').onchange = () => fillTestModels();
$('testModel').onchange = toggleCustom;
$('testScenario').innerHTML = SCENARIO_ORDER.map(key => `<option value="${{key}}">${{SCENARIOS[key].label}}</option>`).join('');
$('testScenario').value = '1';  // seeded on load, matching today's own hardcoded Lydia default
applyScenario();
// selectProvider() calls fillTestProviders() itself, which builds #testProvider's own options.
// The address (#setup/continuum) picks the provider too: a link or a reload lands on the same card.
function setupHashProvider() {{
  const m = /^#setup\\/([\\w-]+)$/.exec(location.hash || '');
  return m && byId(m[1]) ? m[1] : null;
}}
window.addEventListener('hashchange', () => {{ const id = setupHashProvider(); if (id) selectProvider(id); }});
selectProvider(setupHashProvider() || DEFAULT_PROVIDER, {{preferredModel: DEFAULT_MODEL_ID}});

{_SPEEDS_V2_JS}
{jump_ui.JUMP_JS}
{local_models_ui.LOCAL_MODELS_JS}
{local_settings_ui.LOCAL_SETTINGS_JS}
{decision_ui.DECISION_JS}
{models_ui.MODELS_JS}
{other_pcs_ui.OTHER_PCS_JS}
{voice_ui.VOICE_JS}
{claude_ui.CLAUDE_JS}
{copilot_ui.COPILOT_JS}
{codex_install_ui.CODEX_INSTALL_JS}
{routing_ui.ROUTING_JS}
{speeds_time_ui.SPEEDS_TIME_JS}
{speeds_local_ui.SPEEDS_LOCAL_JS}
{prompt_size_ui.PROMPT_SIZE_JS}
{lmstudio_ui.LMSTUDIO_JS}
{claude_trouble.CLAUDE_TROUBLE_JS}
{help_ui.HELP_JS}
{settings_ui.JS}
{gpu_choice_ui.JS}
{overview_ui.JS}
showMainTab('overview');
{get_started_ui.JS}
{set_me_up_ui.JS}
</script>
</body></html>"""


for _jev_path in JEV_PATHS:
    app.post(_jev_path)(_jev_from_body)


@app.post("/{path:path}")
async def _decisions_on_any_other_path(path: str, request: Request):
    """The last route: a decisions-shaped POST (questions and no messages) to a path nothing above knows is still answered as a decision, whatever SkyrimNet
    Beta 26 rc2 turned the typed Endpoint into; anything else is the plain "no such address" it always got."""
    try:
        body = await request.json()
    except Exception:
        body = None
    if isinstance(body, dict) and body.get("questions") is not None and not body.get("messages"):
        logger.info(f"Decisions request on an unlisted path /{path}: answering it as a decision")
        return await _jev_from_body(request)
    raise HTTPException(status_code=404, detail="Not Found")


if __name__ == "__main__":
    _wait_pid = os.environ.pop("MULTIPROXY_WAIT_FOR_PID", "").strip()
    if _wait_pid.isdigit():                          # a restart from the Settings page: the old copy is still closing
        for _ in range(60):
            if not _pid_alive(int(_wait_pid)):
                break
            time.sleep(0.25)
    _preflight_main_port()
    gc_tuning.apply()          # start-up is over: the objects made so far are never re-examined by the collector (see gc_tuning.py)
    uvicorn.run(app, host="127.0.0.1", port=MAIN_PORT, log_config=_UVICORN_LOG_CFG)