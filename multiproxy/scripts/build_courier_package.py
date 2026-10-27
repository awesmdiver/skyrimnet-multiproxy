"""Builds MultiProxy-Courier.zip: the small download for your OTHER devices (the MultiProxy Courier).

    python scripts/build_courier_package.py [--out PATH]

The Courier is its own small program (no web page, nothing Skyrim-related, no multiproxy.py); it shares
other_pcs.py, local_models.py and gpu_monitor.py with the full MultiProxy so a fix lands in both. Flat zip
(everything at the top level) because the Windows setup script copies its siblings by name, and the Linux
installer unpacks it with plain Python.

The release process (the public repo's RELEASING.md) publishes this zip, and courier/install-courier.sh,
as release assets. The file lists below are the single place that says what goes in, and the build refuses
to run if a file that must never be published (settings hold the pairing code) is anywhere near the list.
"""
import argparse
import datetime
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ZIP_NAME = "MultiProxy-Courier.zip"
# THE list of the Courier's files is courier/courier-files.json (scripts/courier_files.py): this build, the Windows installer, the Linux package, the set-up
# script and the release signer all read it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import courier_files

STAMP_NAME = courier_files.STAMP_NAME
PROGRAM_FILES = courier_files.program()
SETUP_FILES = courier_files.setup()
DATA_FILES = courier_files.data()          # {name in the package: source from the project root}

NEVER = {"courier-signing-key.txt", "signin.dat", "config.json", "courier.json", "multiproxy.ini", "multiproxy.log", "proxy.ini", "proxy.log", "courier.log", "speed_history.db", "local_models_pids.json"}


def collect() -> list[tuple[Path, str]]:
    entries = [(ROOT / f, f) for f in PROGRAM_FILES]
    entries += [(ROOT / "courier" / f, f) for f in SETUP_FILES]
    entries += [(ROOT / src, dest) for dest, src in DATA_FILES.items()]
    for path, name in entries:
        if name in NEVER or name.endswith(".tmp") or "claude" in Path(name).parts[:-1]:
            raise SystemExit(f"Refusing to package {name}: it must never be published.")
        if not path.is_file():
            raise SystemExit(f"Can't build the package: {path} is missing.")
    return entries


def _normalize(name: str, data: bytes) -> bytes:
    """Line endings each kind of script needs: Windows batch/PowerShell files want CRLF (a batch file
    with bare LF can misbehave around labels), Linux shell scripts want plain LF (a CR breaks them)."""
    if name.endswith((".bat", ".ps1")):
        return re.sub(rb"\r?\n", b"\r\n", data)
    if name.endswith(".sh"):
        return data.replace(b"\r\n", b"\n")
    return data


def build_stamp() -> str:
    """`<date>-<commit>` of this build (the commit is the short git id; `unknown` when git is not there). Read back by courier.read_built."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    return f"{datetime.date.today().isoformat()}-{commit}"


def build(out: Path) -> list[str]:
    entries = collect()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, name in entries:
            info = zipfile.ZipInfo.from_file(path, name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name.endswith(".sh"):
                info.external_attr = (0o755 & 0xFFFF) << 16          # keep the scripts runnable on Linux
            z.writestr(info, _normalize(name, path.read_bytes()))
        z.writestr(STAMP_NAME, build_stamp() + "\n")
    return [name for _, name in entries] + [STAMP_NAME]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "dist" / f"MultiProxy-Courier-{courier_files.version_of((ROOT / 'courier.py').read_text(encoding='utf-8'))}.zip"))
    args = ap.parse_args()
    names = build(Path(args.out))
    print(f"Built {args.out} with {len(names)} files:")
    for n in names:
        print("  " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
