"""The two settings files, edited in one place (2026-10-02): used by MultiProxy's server and by the tray app, which can be running while the
server isn't (so it can't ask the server to change a setting).

`multiproxy.ini` is edited line by line so every comment the player's file has stays (a normal ConfigParser write would drop them);
`config.json` is plain JSON. "Start with Windows" is a per-user Run entry (no admin), written through `courier_system.StartWithWindows`
(the same code the Courier uses, with a stand-in registry in tests so a test never touches the real one).
"""
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

SETTINGS_LOCATION_FILE = "settings-location.txt"   # written by the installer next to the program: where this install keeps its settings and logs
STARTUP_NAME = "SkyrimNet MultiProxy"            # the name of the Run entry, as Windows' own Startup Apps list shows it


def settings_dir(program_dir) -> str:
    """The folder that holds this program's settings and logs (config.json, multiproxy.ini, the logs, the speed history).

    In order: the MULTIPROXY_SETTINGS_DIR environment variable; the folder named in `settings-location.txt` beside the program (the installer writes it,
    so a player-chosen settings folder is followed by the server, the tray app and the Skyrim mod alike); else the program's own folder, which is
    what a download-and-unzip install and a developer's checkout have always used."""
    env = os.environ.get("MULTIPROXY_SETTINGS_DIR", "").strip()
    if env:
        return env
    try:
        text = (Path(program_dir) / SETTINGS_LOCATION_FILE).read_text(encoding="utf-8").strip().strip('"')
        if text:
            return text
    except OSError:
        pass
    return str(program_dir)


def ini_set(path: str, section: str, key: str, value: str) -> None:
    """Change one `Key = value` line under [section], keeping every comment and every other line. Adds the line, the section or the file when missing."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    want = f"[{section}]".lower()
    in_section, done, insert_at = False, False, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s.lower() == want
            if in_section:
                insert_at = i + 1
            continue
        if in_section and re.match(rf"^\s*{re.escape(key)}\s*[=:]", line, flags=re.I):
            lines[i] = f"{key} = {value}"
            done = True
            break
    if not done:
        if insert_at is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            insert_at = len(lines)
        lines.insert(insert_at, f"{key} = {value}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def ini_get_bool(path: str, section: str, key: str, default: bool = False) -> bool:
    import configparser
    cp = configparser.ConfigParser()
    try:
        cp.read(path, encoding="utf-8")
        return cp.getboolean(section, key, fallback=default)
    except (configparser.Error, ValueError, OSError):
        return default


def read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def tray_start_command(program_dir: str, python: Optional[str] = None) -> list:
    """How the tray app is started: no console (pythonw on Windows), the program's own folder. The Run entry, the Skyrim mod's pointer file and
    the dev script all use this one command."""
    exe = python or sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        exe = exe[: -len("python.exe")] + "pythonw.exe"
    return [exe, str(Path(program_dir) / "multiproxy_tray.py")]


LAUNCHER_NAME = "SkyrimNet MultiProxy.exe"                  # the small start program the installer puts next to multiproxy_tray.py (installer/launcher/): it has its own name, publisher and icon


def startup_command(program_dir: str) -> str:
    """What Windows runs at sign-in. Windows names a Startup item from the program file's own description, so an installed copy starts through its own small start program
    (shown as "SkyrimNet MultiProxy", not as "Python"); a copy with no start program next to it (a download-and-unzip install, a developer's checkout) starts the tray app the
    way it always did. The Skyrim mod's pointer file keeps using `tray_start_command` (pythonw): that one is not a Startup item."""
    launcher = Path(program_dir) / LAUNCHER_NAME
    if os.name == "nt" and launcher.is_file() and (Path(program_dir) / "python" / "pythonw.exe").is_file():
        return f'"{launcher}"'
    return " ".join(f'"{p}"' for p in tray_start_command(program_dir))


def run_entry(program_dir: str, registry: Any = None):
    """The per-user Run entry for the tray app (`.supported` is False off Windows)."""
    from courier_system import StartWithWindows
    return StartWithWindows(startup_command(program_dir), registry=registry, name=STARTUP_NAME)
