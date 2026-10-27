"""The one list of the Courier's files (courier/courier-files.json), for every build script and the release signer."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST_PATH = ROOT / "courier" / "courier-files.json"
STAMP_NAME = "courier-build.txt"          # the date and commit a package was built from; allowed in an update, never required


def version_of(courier_py_text: str) -> str:
    """The Courier's version, 1.<build>, worked out from the text of courier.py exactly as courier.py works it out (COURIER_MAJOR defaults to 1)."""
    import re
    b = re.search(r"^COURIER_BUILD\s*=\s*(\d+)", courier_py_text, re.M)
    if not b:
        raise ValueError("courier.py does not state its COURIER_BUILD.")
    m = re.search(r"^COURIER_MAJOR\s*=\s*(\d+)", courier_py_text, re.M)
    return f"{m.group(1) if m else 1}.{b.group(1)}"


def _load() -> dict:
    return json.loads(LIST_PATH.read_text(encoding="utf-8"))


def program() -> list:
    """The program's .py files, as named inside a package."""
    return list(_load()["program"])


def data() -> dict:
    """{name inside a package: source path from the project root}."""
    return dict(_load()["data"])


def installer_only() -> list:
    """Program files only the Windows installer's stage carries (installer_helper.py)."""
    return list(_load()["installer_only"])


def installer_only_files() -> list:
    """Other files (not .py program modules) only the Windows installer carries: the start program. A signed update never contains them, and the signer leaves them out of the package instead of refusing the stage."""
    return list(_load().get("installer_only_files", []))


def setup() -> list:
    """Files in the zip only (set-up scripts, readme, this list); never part of an update."""
    return list(_load()["setup"])


def update_set() -> list:
    """Exactly the names a signed update may contain (the stamp aside): the program files and the data files."""
    return program() + list(data())
