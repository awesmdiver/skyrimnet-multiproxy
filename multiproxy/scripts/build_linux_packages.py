"""Builds the two Linux packages (2026-10-05):  dist/MultiProxy-<version>-linux.tar.gz  and  dist/MultiProxy-Courier-<version>-linux.tar.gz

    python scripts/build_linux_packages.py [--only multiproxy|courier] [--out DIR]

Each is a plain tarball with one folder in it (`multiproxy/`, `multiproxy-courier/`): the program files, `install.sh`, a README and the license.
There is no bundled Python on Linux: `install.sh` makes a private environment from the computer's own Python and installs the requirements into it.

The program files are followed from what the entry points import (the same walk the Windows installers use, `installer/build.py`), so a new module
can't be forgotten. Anything that holds a secret (settings, pairing codes, logs, the speed history) can never be put in: the build refuses.
Shell scripts are written with plain LF line endings and the executable bit set (a CR in a script breaks it on Linux).
"""
import argparse
import gzip
import importlib.util
import io
import re
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))          # (so a test that loads this file by path finds its sibling too)
import courier_files  # noqa: E402
LINUX = ROOT / "linux"

_spec = importlib.util.spec_from_file_location("installer_build", ROOT / "installer" / "build.py")
_ib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ib)

NEVER = set(_ib.NEVER) | {"courier.out", "multiproxy.pid", "courier.pid", "endpoint.json", "voice-license.json", "voice-license-this-pc.json"}

PRODUCTS = {
    "multiproxy": {
        "folder": "multiproxy",
        "output": "MultiProxy-{version}-linux.tar.gz",
        "roots": ["multiproxy", "multiproxy_tray", "multiproxy_cli"],
        "version_file": "multiproxy.py", "version_re": r'^PROXY_VERSION\s*=\s*"([^"]+)"',
        # source (from the project root) -> name in the package
        "data": {"docs/help/help-content.md": "docs/help/help-content.md", "multiproxy.ini.example": "multiproxy.ini.example", "config.example.json": "config.example.json", "settings_presets.json": "settings_presets.json",
                 "LICENSE": "LICENSE-proxy.txt", "THIRD-PARTY-NOTICES.md": "THIRD-PARTY-NOTICES.md", "requirements.txt": "requirements.txt",
                 "requirements-tray.txt": "requirements-tray.txt", "installer/icons/app-icon.png": "app-icon.png",
                 "linux/lib.sh": "lib.sh", "linux/install.sh": "install.sh", "linux/multiproxy": "multiproxy", "linux/README-multiproxy.txt": "README.txt"},
    },
    "courier": {
        "folder": "multiproxy-courier",
        "output": "MultiProxy-Courier-{version}-linux.tar.gz",
        "roots": ["courier"],
        "version_file": "courier.py", "version_from": courier_files.version_of,
        "data": {"LICENSE": "LICENSE-proxy.txt", "requirements-courier.txt": "requirements-courier.txt", "installer/icons/courier-icon.png": "courier-icon.png",
                 "linux/lib.sh": "lib.sh", "linux/courier-install.sh": "install.sh", "linux/multiproxy-courier": "multiproxy-courier", "linux/README-courier.txt": "README.txt"},
    },
}

EXECUTABLE = {"install.sh", "multiproxy", "multiproxy-courier"}
TEXT_LF = (".sh", ".txt", ".md")


class BuildError(Exception):
    pass


REQUIRE_COURIER_UPDATE = False


def _courier_update_entry() -> list:
    """The signed Courier update MultiProxy carries (same rule as the Windows build: checked with the program's own code, and only a release insists on it)."""
    source = _ib.COURIER_UPDATE_SOURCE
    if not source.is_file():
        if REQUIRE_COURIER_UPDATE:
            raise BuildError(f"The signed Courier update isn't there ({source}). Build the Courier, sign it (docs/RELEASING-THE-COURIER.md), then build again.")
        return []
    sys.path.insert(0, str(ROOT))
    import update_verify
    try:
        manifest, _ = update_verify.verify_package(source)
    except update_verify.UpdateRejected as e:
        raise BuildError(f"The signed Courier update does not pass its own check ({e.code}); it was not added.")
    try:
        _ib.check_courier_build(manifest.get("build"))
    except _ib.BuildError as e:
        raise BuildError(str(e))
    return [(source, _ib.COURIER_UPDATE_NAME)]


def collect(name: str) -> list:
    """[(source path, name inside the package)], sorted, after the refusals."""
    product = PRODUCTS[name]
    # (the Courier's files come from the one shared list, courier/courier-files.json, not from following imports; the installer-only helper is not in a Linux package)
    mods = [f[:-3] for f in _ib.courier_files.program()] if name == "courier" else _ib.program_modules(product["roots"])
    entries = [(ROOT / f"{m}.py", f"{m}.py") for m in mods]
    entries += [(ROOT / src, dest) for src, dest in product["data"].items()]
    if name == "multiproxy":
        entries += _courier_update_entry()
    for path, dest in entries:
        if Path(dest).name in NEVER:
            raise BuildError(f"Refusing to package {dest}: it holds a player's own settings or secrets and must never be in a package.")
        if not path.is_file():
            raise BuildError(f"Can't build the package: {path} is missing.")
    return sorted(entries, key=lambda e: e[1])


def _content(path: Path, dest: str) -> bytes:
    data = path.read_bytes()
    if dest.endswith(TEXT_LF) or Path(dest).name in EXECUTABLE:
        data = data.replace(b"\r\n", b"\n")
    return data


def version_of(name: str) -> str:
    product = PRODUCTS[name]
    text = (ROOT / product["version_file"]).read_text(encoding="utf-8")
    if "version_from" in product:          # the Courier's version is 1.<build>, worked out from the build number
        try:
            return product["version_from"](text)
        except ValueError as e:
            raise BuildError(str(e))
    m = re.search(product["version_re"], text, re.M)
    if not m:
        raise BuildError(f"Couldn't read the version from {product['version_file']}.")
    return m.group(1)


def build(name: str, out_dir: Path) -> Path:
    product = PRODUCTS[name]
    version = version_of(name)
    entries = collect(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / product["output"].format(version=version)
    folder = product["folder"]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as t:
        top = tarfile.TarInfo(folder)
        top.type, top.mode = tarfile.DIRTYPE, 0o755
        t.addfile(top)
        made = {folder}
        for path, dest in entries:
            parent = Path(dest).parent
            while str(parent) not in (".", ""):
                d = f"{folder}/{parent.as_posix()}"
                if d not in made:
                    di = tarfile.TarInfo(d)
                    di.type, di.mode = tarfile.DIRTYPE, 0o755
                    t.addfile(di)
                    made.add(d)
                parent = parent.parent
            data = _content(path, dest)
            ti = tarfile.TarInfo(f"{folder}/{dest}")
            ti.size = len(data)
            ti.mode = 0o755 if Path(dest).name in EXECUTABLE else 0o644
            ti.mtime = int(path.stat().st_mtime)
            t.addfile(ti, io.BytesIO(data))
    with open(out, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as gz:
            gz.write(raw.getvalue())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=sorted(PRODUCTS))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--require-courier-update", action="store_true", help="refuse to build MultiProxy without the signed Courier update (a release does)")
    args = ap.parse_args(argv)
    global REQUIRE_COURIER_UPDATE
    REQUIRE_COURIER_UPDATE = args.require_courier_update
    try:
        for name in ([args.only] if args.only else sorted(PRODUCTS)):
            out = build(name, Path(args.out))
            with tarfile.open(out) as t:
                files = [m for m in t.getmembers() if m.isfile()]
            print(f"Built {out} ({len(files)} files)")
    except (BuildError, _ib.BuildError) as e:
        print(f"The build stopped: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
