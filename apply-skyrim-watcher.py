#!/usr/bin/env python3
"""
apply-skyrim-watcher.py
Patches proxy.py (galanx/Claude-SkyrimNet-Proxy) to add the Skyrim
auto-close feature from ProxyLauncher.

When enabled, the proxy monitors for the Skyrim process and shuts itself
down automatically when the game exits — no manual cleanup needed.

Usage:
    python apply-skyrim-watcher.py path/to/proxy.py

    # Enable the feature right away:
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
    """Insert configparser/subprocess/threading imports."""
    anchor = "from contextlib import asynccontextmanager, suppress"
    if anchor not in text:
        raise ValueError(
            "Cannot find 'from contextlib import asynccontextmanager' — "
            "is this the right file?"
        )
    insert = (
        "import configparser\n"
        "import subprocess\n"
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


# ── Orchestrator ─────────────────────────────────────────────────────────────

def apply_patch(text: str) -> str:
    text = _hunk_imports(text)
    text = _hunk_config_block(text)
    text = _hunk_watcher_function(text)
    text = _hunk_lifespan_thread(text)
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
