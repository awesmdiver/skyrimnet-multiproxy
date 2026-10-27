"""Which version and which build a program is, in one plain line (2026-10-06): shown in the tray menu, on the dashboard's About line and in the first line of the log, so a
player can paste it into a bug report and a maintainer can tell two packages with the same version apart.

The build writes `multiproxy-build.txt` (`<date>-<short commit>`, the same stamp the Courier package carries as `courier-build.txt`) next to the program; a copy run from the
source has none, and says so. Plain functions, no side effects: the tray and the server both read the same file.
"""
from pathlib import Path

STAMP_FILE = "multiproxy-build.txt"


def read_stamp(folder) -> str:
    try:
        return (Path(folder) / STAMP_FILE).read_text(encoding="utf-8").strip()[:80]
    except OSError:
        return ""


def version_line(version: str, stamp: str) -> str:
    return f"MultiProxy {version}, built {stamp}" if stamp else f"MultiProxy {version} (not built from a package)"
