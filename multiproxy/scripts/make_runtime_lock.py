"""Writes the two lock files the Windows installers build their bundled Python from (installer/locks/multiproxy.lock.txt and courier.lock.txt).

    python scripts/make_runtime_lock.py [--only multiproxy|courier]

Each lock names every package the runtime holds with its exact version and the SHA-256 of every Windows wheel PyPI lists for it, so the build installs with
`pip install --require-hashes` and gets the very files that were looked at (a changed file on the index is refused). The requirements*.txt files stay the human list
(and are what Linux, the dev setup and the Courier update's runtime id use); to move a pin, run this on the newest versions, read the diff, then run the build (its
OSV check refuses a known advisory).

It asks pip to resolve for the bundled Python's own platform (win_amd64, cp314) without installing anything, then asks PyPI for the file hashes. Needs the internet.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ROOT / "installer" / "locks"
PRODUCTS = {"multiproxy": ["requirements.txt", "requirements-tray.txt"], "courier": ["requirements-courier.txt"]}
PY_MINOR, PY_TAG = "3.14", "cp314"
HEADER = ("# Written by scripts/make_runtime_lock.py: do not edit by hand. Every package the {name} installer's bundled Python holds, pinned, with the SHA-256 of each\n"
          "# Windows wheel PyPI lists for it. installer/build.py installs from this with --require-hashes and keys its runtime cache on this file's text.\n")


def _wheel_ok(filename: str) -> bool:
    """A wheel the bundled Python (Windows, 64-bit, CPython 3.14) can use."""
    if not filename.endswith(".whl"):
        return False
    tags = filename[:-4].split("-")[-3:]          # python tag, abi tag, platform tag
    py, abi, plat = tags
    plat_ok = plat == "any" or "win_amd64" in plat.split(".")
    py_ok = any(p in (PY_TAG, "py3", "py314") for p in py.split("."))
    abi_ok = any(a in (PY_TAG, "abi3", "none") for a in abi.split("."))
    return plat_ok and py_ok and abi_ok


def resolve(requirements: list) -> list:
    """[(name, version)] pip would install for the bundled Python, nothing installed."""
    with tempfile.TemporaryDirectory(prefix="lock-") as tmp:
        report = Path(tmp) / "report.json"
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--disable-pip-version-check", "--quiet", "--only-binary=:all:", "--target", str(Path(tmp) / "t"),
               "--platform", "win_amd64", "--python-version", PY_MINOR, "--implementation", "cp", "--abi", PY_TAG, "--report", str(report)]
        for r in requirements:
            cmd += ["-r", str(ROOT / r)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit("pip could not resolve the requirements:\n" + res.stderr[-1500:])
        data = json.loads(report.read_text(encoding="utf-8"))
    return sorted({(i["metadata"]["name"], i["metadata"]["version"]) for i in data["install"]}, key=lambda t: t[0].lower())


def hashes_for(name: str, version: str) -> list:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    found = sorted({f["digests"]["sha256"] for f in files if _wheel_ok(f["filename"]) and not f.get("yanked")})
    if not found:
        raise SystemExit(f"PyPI lists no Windows wheel for {name} {version}.")
    return found


def write_lock(product: str) -> Path:
    lines = [HEADER.format(name=product)]
    for name, version in resolve(PRODUCTS[product]):
        h = hashes_for(name, version)
        lines.append(f"{name.lower().replace('_', '-')}=={version} " + " ".join(f"\\\n    --hash=sha256:{x}" for x in h) + "\n")
    LOCKS.mkdir(parents=True, exist_ok=True)
    out = LOCKS / f"{product}.lock.txt"
    out.write_text("".join(lines), encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=list(PRODUCTS))
    args = ap.parse_args()
    for p in ([args.only] if args.only else list(PRODUCTS)):
        print("wrote", write_lock(p))
