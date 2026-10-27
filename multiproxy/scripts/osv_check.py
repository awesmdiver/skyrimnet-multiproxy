"""Asks OSV (osv.dev, the public vulnerability list) about every package in a built runtime, and names any with a known vulnerability that a newer version fixes.

    python scripts/osv_check.py <site-packages folder>      (exit 1 when something has a fix available, 2 when OSV could not be asked)

Only package names and versions are sent. Used by installer/build.py after the bundled Python is built, on every build (a cached runtime gets an old advisory too).
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.osv.dev/v1"


class OsvUnavailable(Exception):
    """OSV could not be asked (no internet, or it answered with something unreadable)."""


def installed(site_packages: Path) -> list:
    """[(name, version)] from the *.dist-info folders in a site-packages folder."""
    out = []
    for d in sorted(Path(site_packages).glob("*.dist-info")):
        m = re.match(r"^(.+?)-(\d[^-]*)\.dist-info$", d.name)
        if m:
            out.append((m.group(1).replace("_", "-").lower(), m.group(2)))
    return sorted(out)      # by the lower-cased name (a folder called Pillow sorts before aiohttp as written)


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OsvUnavailable(str(e))


def _get(path: str) -> dict:
    try:
        with urllib.request.urlopen(API + path, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OsvUnavailable(str(e))


def fixed_versions(vuln: dict, package: str) -> list:
    """The versions of `package` that OSV says fix this vulnerability (empty: no fix is listed)."""
    out = []
    for a in vuln.get("affected", []):
        if a.get("package", {}).get("name", "").replace("_", "-").lower() != package:
            continue
        for rng in a.get("ranges", []):
            out += [e["fixed"] for e in rng.get("events", []) if "fixed" in e]
    return out


def check(packages: list, post=None, get=None) -> list:
    """The findings with a fix available: [{"package", "version", "id", "summary", "fixed_in"}]. Withdrawn entries and ones with no fix yet are left out."""
    post, get = post or _post, get or _get
    if not packages:
        return []
    res = post("/querybatch", {"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in packages]})
    findings = []
    for (name, version), out in zip(packages, res.get("results", [])):
        for hit in out.get("vulns") or []:
            vuln = get("/vulns/" + hit["id"])
            if vuln.get("withdrawn"):
                continue
            fixes = fixed_versions(vuln, name)
            if fixes:
                findings.append({"package": name, "version": version, "id": vuln["id"], "summary": vuln.get("summary", ""), "fixed_in": fixes})
    return findings


def describe(findings: list) -> str:
    return "\n".join(f"  {f['package']} {f['version']}: {f['id']} {f['summary']} (fixed in {', '.join(f['fixed_in'])})" for f in findings)


if __name__ == "__main__":
    try:
        found = check(installed(Path(sys.argv[1])))
    except OsvUnavailable as e:
        print(f"Could not ask OSV: {e}", file=sys.stderr)
        sys.exit(2)
    print(describe(found) if found else "no known vulnerability with a fix available")
    sys.exit(1 if found else 0)
