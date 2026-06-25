#!/usr/bin/env python3
"""
apply-skyrim-watcher.py
Patches proxy.py (galanx/Claude-SkyrimNet-Proxy) with features
from ProxyLauncher, controlled via proxy.ini:

  AutoCloseWithSkyrim — monitors the Skyrim process and shuts the proxy down
                         automatically when the game exits.
  EnableLogging        — writes proxy.log alongside proxy.py; fresh file
                         each session (previous run's log is overwritten).

Additional patches applied unconditionally:

  Token counting       — every API response log line now includes
                         "| in=N out=N tok" for both streaming and
                         non-streaming calls.
  Console title        — restores the console window title to
                         "Claude SkyrimNet Proxy" after the auth-capture
                         subprocess (claude --print) changes it.

All features except EnableLogging default to false in the generated proxy.ini.

Usage:
    python apply-skyrim-watcher.py path/to/proxy.py

    # Enable auto-close right away:
    python apply-skyrim-watcher.py path/to/proxy.py --enable

Requirements:
    Python 3.10+, no extra packages.
    Works on Windows (uses tasklist, no psutil needed).

Source: https://github.com/awesmdiver/ProxyLauncher
Target: https://github.com/galanx/Claude-SkyrimNet-Proxy
"""

import sys
import os
import shutil

# ── Marker ─────────────────────────────────────────────────────────────────
# If this string is already in the file the patch has already been applied.
ALREADY_APPLIED_MARKER = "AUTO_CLOSE_WITH_SKYRIM"


# ── Patch hunks ─────────────────────────────────────────────────────────────

def _hunk_imports(text: str) -> str:
    """Insert configparser/subprocess/threading imports (and logging.handlers if absent)."""
    anchor = "from contextlib import asynccontextmanager, suppress"
    if anchor not in text:
        raise ValueError(
            "Cannot find 'from contextlib import asynccontextmanager' — "
            "is this the right file?"
        )
    # Only add these if the original doesn't already import them
    handlers_line = "" if "import logging.handlers" in text else "import logging.handlers\n"
    re_line = "" if "import re" in text else "import re\n"
    insert = (
        "import configparser\n"
        + handlers_line
        + re_line
        + "import subprocess\n"
        "import threading\n"
    )
    return text.replace(anchor, insert + anchor, 1)


def _hunk_config_block(text: str) -> str:
    """Insert proxy.ini config loading after the logger definition."""
    anchor_old = 'logger = logging.getLogger("proxy")\n\nDEFAULT_MODEL'
    if anchor_old not in text:
        raise ValueError(
            "Cannot find 'logger = logging.getLogger(\"proxy\")' followed by "
            "'DEFAULT_MODEL' — file may have changed upstream."
        )
    anchor_new = (
        'logger = logging.getLogger("proxy")\n'
        "\n"
        "# --- Skyrim watcher config (proxy.ini) ---\n"
        "_PROXY_INI = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"proxy.ini\")\n"
        "_proxy_ini_cfg = configparser.ConfigParser()\n"
        "_proxy_ini_cfg.read(_PROXY_INI)\n"
        "AUTO_CLOSE_WITH_SKYRIM: bool = _proxy_ini_cfg.getboolean(\"General\", \"AutoCloseWithSkyrim\", fallback=False)\n"
        "_SKYRIM_PROCESSES: list[str] = [\n"
        "    p.strip()\n"
        "    for p in _proxy_ini_cfg.get(\"General\", \"SkyrimProcess\", fallback=\"SkyrimSE.exe,SkyrimVR.exe\").split(\",\")\n"
        "    if p.strip()\n"
        "]\n"
        "\n"
        "DEFAULT_MODEL"
    )
    return text.replace(anchor_old, anchor_new, 1)


def _hunk_file_logging(text: str) -> str:
    """Insert EnableLogging + VT processing + uvicorn log_config builder after the config block."""
    # Anchor on the end of the _SKYRIM_PROCESSES list followed by DEFAULT_MODEL = "..."
    # We avoid hardcoding the model name so this works across upstream releases.
    anchor_start = "    if p.strip()\n]\n"
    dm_prefix = 'DEFAULT_MODEL = "'
    if anchor_start not in text or dm_prefix not in text:
        raise ValueError(
            "Cannot find end of _SKYRIM_PROCESSES or DEFAULT_MODEL = — "
            "run this after _hunk_config_block, or file may have changed upstream."
        )

    # Find the DEFAULT_MODEL line that immediately follows anchor_start
    idx = text.index(anchor_start)
    dm_idx = text.index(dm_prefix, idx)
    dm_end = text.index("\n", dm_idx)
    dm_line = text[dm_idx:dm_end + 1]   # e.g. 'DEFAULT_MODEL = "claude-sonnet-4-5-20250929"\n'

    anchor_old = anchor_start + "\n" + dm_line
    if anchor_old not in text:
        raise ValueError(
            "Cannot find _SKYRIM_PROCESSES + DEFAULT_MODEL block — "
            "file layout may have changed upstream."
        )

    anchor_new = (
        "    if p.strip()\n"
        "]\n"
        "ENABLE_LOGGING: bool = _proxy_ini_cfg.getboolean(\"General\", \"EnableLogging\", fallback=False)\n"
        "\n"
        "\n"
        "class _RedactingFormatter(logging.Formatter):\n"
        "    \"\"\"File-log formatter that masks API key tokens so they never reach disk.\"\"\"\n"
        "    _TOKEN_RE = re.compile(r'\\bsk-[A-Za-z0-9_-]{10,}')\n"
        "\n"
        "    def format(self, record: logging.LogRecord) -> str:\n"
        "        return self._TOKEN_RE.sub('sk-***REDACTED***', super().format(record))\n"
        "\n"
        "\n"
        "# Enable ANSI VT sequences on the Windows console so uvicorn's colour codes render correctly.\n"
        "# Consoles created by CreateProcess (e.g. via an SKSE plugin) have VT processing off by default.\n"
        "try:\n"
        "    import ctypes as _ctypes\n"
        "    _k32 = _ctypes.windll.kernel32\n"
        "    for _std in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE\n"
        "        _h = _k32.GetStdHandle(_std)\n"
        "        _mode = _ctypes.c_ulong()\n"
        "        if _h and _k32.GetConsoleMode(_h, _ctypes.byref(_mode)):\n"
        "            _k32.SetConsoleMode(_h, _mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING\n"
        "except Exception:\n"
        "    pass\n"
        "\n"
        "# Build a custom uvicorn log_config that keeps uvicorn's pretty console output while\n"
        "# also tee-ing every message to the log file (plain text, tokens redacted).\n"
        "# None = let uvicorn use its built-in default (pretty console, no file).\n"
        "_UVICORN_LOG_CFG = None\n"
        "if ENABLE_LOGGING:\n"
        "    _LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"proxy.log\")\n"
        "    import copy\n"
        "    from uvicorn.config import LOGGING_CONFIG as _ULC\n"
        "    _UVICORN_LOG_CFG = copy.deepcopy(_ULC)\n"
        "    _UVICORN_LOG_CFG[\"formatters\"][\"file_fmt\"] = {\n"
        "        \"()\": _RedactingFormatter,\n"
        "        \"fmt\": \"%(asctime)s %(levelname)-8s %(message)s\",\n"
        "    }\n"
        "    _UVICORN_LOG_CFG[\"handlers\"][\"file\"] = {\n"
        "        \"class\": \"logging.FileHandler\",\n"
        "        \"filename\": _LOG_FILE,\n"
        "        \"mode\": \"w\",\n"
        "        \"encoding\": \"utf-8\",\n"
        "        \"formatter\": \"file_fmt\",\n"
        "    }\n"
        "    _UVICORN_LOG_CFG[\"formatters\"][\"proxy_fmt\"] = {\n"
        "        \"format\": \"%(asctime)s %(levelname)-8s %(message)s\",\n"
        "    }\n"
        "    _UVICORN_LOG_CFG[\"handlers\"][\"proxy_console\"] = {\n"
        "        \"class\": \"logging.StreamHandler\",\n"
        "        \"stream\": \"ext://sys.stderr\",\n"
        "        \"formatter\": \"proxy_fmt\",\n"
        "    }\n"
        "    for _lname in (\"uvicorn\", \"uvicorn.access\"):\n"
        "        _UVICORN_LOG_CFG[\"loggers\"][_lname][\"handlers\"] = (\n"
        "            list(_UVICORN_LOG_CFG[\"loggers\"][_lname].get(\"handlers\", [])) + [\"file\"]\n"
        "        )\n"
        "    _UVICORN_LOG_CFG[\"root\"] = {\"handlers\": [\"proxy_console\", \"file\"], \"level\": \"INFO\"}\n"
        "\n"
        + dm_line
    )
    return text.replace(anchor_old, anchor_new, 1)


def _hunk_console_title(text: str) -> str:
    """Restore the console window title after claude --print changes it."""
    anchor_old = (
        "        try:\n"
        "            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)\n"
        "        except asyncio.TimeoutError:\n"
        "            proc.kill()\n"
        "            with suppress(Exception):\n"
        "                await proc.wait()\n"
        "            raise\n"
    )
    if anchor_old not in text:
        raise ValueError(
            "Cannot find proc.communicate() block in capture_auth — "
            "file may have changed upstream."
        )
    anchor_new = (
        "        try:\n"
        "            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)\n"
        "        except asyncio.TimeoutError:\n"
        "            proc.kill()\n"
        "            with suppress(Exception):\n"
        "                await proc.wait()\n"
        "            raise\n"
        "        finally:\n"
        "            try:\n"
        "                import ctypes\n"
        "                ctypes.windll.kernel32.SetConsoleTitleW(\"Claude SkyrimNet Proxy\")\n"
        "            except Exception:\n"
        "                pass\n"
    )
    return text.replace(anchor_old, anchor_new, 1)


def _hunk_direct_token_count(text: str) -> str:
    """Add _usage / token-count logging to call_api_direct."""
    # Skip if already applied (PR merged upstream or previously patched)
    if "_usage = None" in text and '"message_start"' in text and "_tok" in text:
        return text
    anchor_old = (
        "            resp_body = await resp.read()\n"
        "            text_parts = []\n"
        "            content_type = resp.headers.get(\"Content-Type\", \"\")\n"
        "            if \"text/event-stream\" in content_type:\n"
        "                for line in resp_body.decode(\"utf-8\", errors=\"replace\").split(\"\\n\"):\n"
        "                    if line.startswith(\"data: \"):\n"
        "                        data_str = line[6:].strip()\n"
        "                        if data_str == \"[DONE]\":\n"
        "                            continue\n"
        "                        try:\n"
        "                            event = json.loads(data_str)\n"
        "                            if event.get(\"type\") == \"content_block_delta\":\n"
        "                                delta = event.get(\"delta\", {})\n"
        "                                if delta.get(\"type\") == \"text_delta\":\n"
        "                                    text_parts.append(delta.get(\"text\", \"\"))\n"
        "                        except json.JSONDecodeError:\n"
        "                            pass\n"
        "            else:\n"
        "                try:\n"
        "                    data = json.loads(resp_body)\n"
        "                    for block in data.get(\"content\", []):\n"
        "                        if block.get(\"type\") == \"text\":\n"
        "                            text_parts.append(block.get(\"text\", \"\"))\n"
        "                except json.JSONDecodeError:\n"
        "                    pass\n"
        "\n"
        "            response_text = \"\".join(text_parts)\n"
        "            logger.info(f\"[{request_id}] <- {len(response_text)} chars ({elapsed:.1f}s)\")\n"
        "            return response_text\n"
    )
    if anchor_old not in text:
        raise ValueError(
            "Cannot find response-parsing block in call_api_direct — "
            "file may have changed upstream."
        )
    anchor_new = (
        "            resp_body = await resp.read()\n"
        "            text_parts = []\n"
        "            _usage = None\n"
        "            content_type = resp.headers.get(\"Content-Type\", \"\")\n"
        "            if \"text/event-stream\" in content_type:\n"
        "                _in_tok = 0\n"
        "                _out_tok = 0\n"
        "                for line in resp_body.decode(\"utf-8\", errors=\"replace\").split(\"\\n\"):\n"
        "                    if line.startswith(\"data: \"):\n"
        "                        data_str = line[6:].strip()\n"
        "                        if data_str == \"[DONE]\":\n"
        "                            continue\n"
        "                        try:\n"
        "                            event = json.loads(data_str)\n"
        "                            if event.get(\"type\") == \"message_start\":\n"
        "                                _in_tok = event.get(\"message\", {}).get(\"usage\", {}).get(\"input_tokens\", 0)\n"
        "                            elif event.get(\"type\") == \"message_delta\":\n"
        "                                _out_tok = event.get(\"usage\", {}).get(\"output_tokens\", 0)\n"
        "                            elif event.get(\"type\") == \"content_block_delta\":\n"
        "                                delta = event.get(\"delta\", {})\n"
        "                                if delta.get(\"type\") == \"text_delta\":\n"
        "                                    text_parts.append(delta.get(\"text\", \"\"))\n"
        "                        except json.JSONDecodeError:\n"
        "                            pass\n"
        "                if _in_tok or _out_tok:\n"
        "                    _usage = {\"input_tokens\": _in_tok, \"output_tokens\": _out_tok}\n"
        "            else:\n"
        "                try:\n"
        "                    data = json.loads(resp_body)\n"
        "                    for block in data.get(\"content\", []):\n"
        "                        if block.get(\"type\") == \"text\":\n"
        "                            text_parts.append(block.get(\"text\", \"\"))\n"
        "                    _usage = data.get(\"usage\", {})\n"
        "                except json.JSONDecodeError:\n"
        "                    _usage = {}\n"
        "\n"
        "            response_text = \"\".join(text_parts)\n"
        "            _tok = f\" | in={_usage.get('input_tokens', 0)} out={_usage.get('output_tokens', 0)} tok\" if _usage else \"\"\n"
        "            logger.info(f\"[{request_id}] <- {len(response_text)} chars ({elapsed:.1f}s){_tok}\")\n"
        "            return response_text\n"
    )
    return text.replace(anchor_old, anchor_new, 1)


def _hunk_streaming_token_count(text: str) -> str:
    """Add input/output token tracking to call_api_streaming_with_retry."""
    # Skip if already applied (PR merged upstream or previously patched)
    if "input_tokens = 0" in text and "output_tokens = 0" in text:
        return text
    # 1. Initialise counters alongside total_chars
    old1 = "        total_chars = 0\n\n        session = auth.session"
    if old1 not in text:
        raise ValueError(
            "Cannot find 'total_chars = 0' init in call_api_streaming_with_retry — "
            "file may have changed upstream."
        )
    new1 = (
        "        total_chars = 0\n"
        "        input_tokens = 0\n"
        "        output_tokens = 0\n"
        "\n"
        "        session = auth.session"
    )
    text = text.replace(old1, new1, 1)

    # 2. Add message_start/message_delta handlers before content_block_delta.
    #    The "continue" on JSONDecodeError distinguishes this from call_api_direct
    #    which uses "pass" in the same pattern.
    old2 = (
        "                        except json.JSONDecodeError:\n"
        "                            continue\n"
        "\n"
        "                        if event.get(\"type\") == \"content_block_delta\":"
    )
    if old2 not in text:
        raise ValueError(
            "Cannot find SSE event handler in call_api_streaming_with_retry — "
            "file may have changed upstream."
        )
    new2 = (
        "                        except json.JSONDecodeError:\n"
        "                            continue\n"
        "\n"
        "                        if event.get(\"type\") == \"message_start\":\n"
        "                            input_tokens = event.get(\"message\", {}).get(\"usage\", {}).get(\"input_tokens\", 0)\n"
        "                        elif event.get(\"type\") == \"message_delta\":\n"
        "                            output_tokens = event.get(\"usage\", {}).get(\"output_tokens\", 0)\n"
        "                        elif event.get(\"type\") == \"content_block_delta\":"
    )
    text = text.replace(old2, new2, 1)

    # 3. Update the streamed-response log line to include token counts
    old3 = (
        "logger.info(f\"[{request_id}] <- {total_chars} chars ({elapsed:.1f}s, streamed)\")"
    )
    if old3 not in text:
        raise ValueError(
            "Cannot find streamed log line in call_api_streaming_with_retry — "
            "file may have changed upstream."
        )
    new3 = (
        "logger.info(f\"[{request_id}] <- {total_chars} chars ({elapsed:.1f}s, streamed) | in={input_tokens} out={output_tokens} tok\")"
    )
    text = text.replace(old3, new3, 1)

    return text


def _hunk_watcher_function(text: str) -> str:
    """Insert _skyrim_watcher() before the lifespan context manager."""
    anchor = "@asynccontextmanager\nasync def lifespan(app):"
    if anchor not in text:
        raise ValueError(
            "Cannot find '@asynccontextmanager\\nasync def lifespan' — "
            "file may have changed upstream."
        )
    fn = (
        "def _skyrim_watcher(process_names: list[str], poll_interval: int = 10) -> None:\n"
        '    """Background thread: shuts down proxy when Skyrim exits."""\n'
        "    names_lower = [n.lower() for n in process_names]\n"
        "\n"
        "    def any_running() -> bool:\n"
        "        try:\n"
        "            for name in names_lower:\n"
        "                result = subprocess.run(\n"
        '                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],\n'
        "                    capture_output=True, text=True, timeout=5,\n"
        "                )\n"
        "                if name in result.stdout.lower():\n"
        "                    return True\n"
        "        except Exception:\n"
        "            return True  # fail safe: don't shut down on a check error\n"
        "        return False\n"
        "\n"
        "    # Skyrim is usually already running when the proxy starts, but allow a 2-minute\n"
        "    # grace period in case the proxy was pre-launched before the game.\n"
        '    logger.info("SkyrimWatcher: waiting for Skyrim process...")\n'
        "    detected = False\n"
        "    for _ in range(12):  # 12 x 10 s = 2 minutes\n"
        "        if any_running():\n"
        '            logger.info("SkyrimWatcher: Skyrim detected, monitoring for exit")\n'
        "            detected = True\n"
        "            break\n"
        "        time.sleep(10)\n"
        "\n"
        "    if not detected:\n"
        '        logger.warning("SkyrimWatcher: Skyrim not detected within 2 minutes — watcher exiting")\n'
        "        return\n"
        "\n"
        "    while True:\n"
        "        time.sleep(poll_interval)\n"
        "        if not any_running():\n"
        '            logger.info("SkyrimWatcher: Skyrim process ended — shutting down proxy")\n'
        "            os._exit(0)\n"
        "\n"
        "\n"
    )
    return text.replace(anchor, fn + anchor, 1)


def _hunk_lifespan_thread(text: str) -> str:
    """Insert thread-start block inside lifespan(), after auth warm-up."""
    anchor_old = (
        "    await auth.ensure_ready(force=True, timeout=60)\n"
        "\n"
        "    try:\n"
        "        yield"
    )
    if anchor_old not in text:
        raise ValueError(
            "Cannot find 'ensure_ready + try/yield' inside lifespan — "
            "file may have changed upstream."
        )
    anchor_new = (
        "    await auth.ensure_ready(force=True, timeout=60)\n"
        "\n"
        "    if AUTO_CLOSE_WITH_SKYRIM:\n"
        "        t = threading.Thread(\n"
        "            target=_skyrim_watcher,\n"
        "            args=(_SKYRIM_PROCESSES,),\n"
        "            daemon=True,\n"
        '            name="SkyrimWatcher",\n'
        "        )\n"
        "        t.start()\n"
        '        logger.info(f"SkyrimWatcher started (processes: {\', \'.join(_SKYRIM_PROCESSES)})")\n'
        "\n"
        "    try:\n"
        "        yield"
    )
    return text.replace(anchor_old, anchor_new, 1)


def _hunk_uvicorn_run(text: str) -> str:
    """Update uvicorn.run() to pass our custom log_config when file logging is enabled."""
    anchor_old = '    uvicorn.run(app, host="127.0.0.1", port=8000)'
    if anchor_old not in text:
        raise ValueError(
            "Cannot find 'uvicorn.run(app, host=\"127.0.0.1\", port=8000)' — "
            "file may have changed upstream."
        )
    anchor_new = (
        "    _run_kw = {\"log_config\": _UVICORN_LOG_CFG} if _UVICORN_LOG_CFG is not None else {}\n"
        "    uvicorn.run(app, host=\"127.0.0.1\", port=8000, **_run_kw)"
    )
    return text.replace(anchor_old, anchor_new, 1)


# ── Orchestrator ─────────────────────────────────────────────────────────────

def apply_patch(text: str) -> str:
    text = _hunk_imports(text)
    text = _hunk_config_block(text)
    text = _hunk_file_logging(text)
    text = _hunk_console_title(text)
    text = _hunk_direct_token_count(text)
    text = _hunk_streaming_token_count(text)
    text = _hunk_watcher_function(text)
    text = _hunk_lifespan_thread(text)
    text = _hunk_uvicorn_run(text)
    return text


def create_ini(proxy_dir: str, enable: bool) -> str:
    ini_path = os.path.join(proxy_dir, "proxy.ini")
    if os.path.exists(ini_path):
        return ini_path  # don't overwrite user's existing config

    value = "true" if enable else "false"
    content = (
        "[General]\n"
        "; Set to true to automatically shut down the proxy when Skyrim exits.\n"
        "; The proxy polls for the Skyrim process every 10 seconds.\n"
        f"AutoCloseWithSkyrim = {value}\n"
        "\n"
        "; Set to true to write a log file alongside proxy.py (proxy.log).\n"
        "; A fresh file is started each time the proxy launches.\n"
        "EnableLogging = false\n"
        "\n"
        "; Comma-separated list of Skyrim process names to watch.\n"
        "SkyrimProcess = SkyrimSE.exe, SkyrimVR.exe\n"
    )
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(content)
    return ini_path


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    enable_flag = "--enable" in args
    paths = [a for a in args if not a.startswith("--")]

    if not paths:
        print(__doc__)
        sys.exit(1)

    proxy_path = paths[0]
    if not os.path.isfile(proxy_path):
        print(f"Error: {proxy_path!r} not found.")
        sys.exit(1)

    with open(proxy_path, "r", encoding="utf-8") as f:
        original = f.read()

    if ALREADY_APPLIED_MARKER in original:
        print("Patch already applied — nothing to do.")
        sys.exit(0)

    # Dry run to catch errors before touching the file
    try:
        patched = apply_patch(original)
    except ValueError as exc:
        print(f"Patch failed: {exc}")
        sys.exit(1)

    # Back up then write
    backup = proxy_path + ".bak"
    shutil.copy2(proxy_path, backup)
    print(f"Backup:  {backup}")

    with open(proxy_path, "w", encoding="utf-8") as f:
        f.write(patched)
    print(f"Patched: {proxy_path}")

    # Create proxy.ini if absent
    ini_path = create_ini(os.path.dirname(os.path.abspath(proxy_path)), enable_flag)
    print(f"Config:  {ini_path}")

    print()
    if enable_flag:
        print("Auto-close is ENABLED. The proxy will shut down when Skyrim exits.")
    else:
        print("Auto-close is disabled by default.")
        print(f"To enable it, open {ini_path} and set AutoCloseWithSkyrim = true")


if __name__ == "__main__":
    main()
