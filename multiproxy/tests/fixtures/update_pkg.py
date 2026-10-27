"""Throwaway packages, installs and staged updates for the Update Courier tests. The keys here are made up for the tests and protect nothing."""
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import courier_updater as upd
import update_verify as uv

SEED = hashlib.sha256(b"a throwaway key for the Courier update tests").digest()
PUBLIC = uv.ed25519_public_key(SEED)
PUBLIC_B64 = base64.b64encode(PUBLIC).decode()
OTHER_SEED = hashlib.sha256(b"someone else's key").digest()

REQUIREMENTS = b"aiohttp>=3.9.0\n"


def courier_py(build: int, extra: str = "") -> bytes:
    return ('COURIER_VERSION = "1.0.0"\nCOURIER_BUILD = %d\n%s' % (build, extra)).encode()


def files_for(build: int, **override) -> dict:
    out = {"courier.py": courier_py(build), "requirements-courier.txt": REQUIREMENTS, "other_pcs.py": ("# build %d\n" % build).encode(),
           "courier_updater.py": b"# the helper (a stand-in here)\n"}
    out.update(override)
    return out


def manifest_bytes(files: dict, build: int, **over) -> bytes:
    m = {"format": uv.FORMAT, "product": uv.PRODUCT, "build": build, "version": "1.0.0", "platform": "any", "runtime_id": uv.runtime_id(files["requirements-courier.txt"]),
         "files": [{"name": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in sorted(files.items())]}
    m.update(over)
    return json.dumps(m, sort_keys=True).encode()


def package_bytes(build: int = 7, files=None, seed=SEED, manifest=None, members=None) -> bytes:
    files = files_for(build) if files is None else files
    manifest = manifest_bytes(files, build) if manifest is None else manifest
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", manifest)
        z.writestr("manifest.sig", base64.b64encode(uv.ed25519_sign(seed, manifest)))
        for n, d in (files if members is None else members).items():
            z.writestr(n, d)
    return buf.getvalue()


def make_install(root: Path, build: int = 6) -> Path:
    """An 'installed Courier' folder with a few program files, all saying `build`."""
    root.mkdir(parents=True, exist_ok=True)
    for n, d in files_for(build).items():
        (root / n).write_bytes(d)
    return root


def snapshot(folder: Path) -> dict:
    return {p.name: p.read_bytes() for p in sorted(Path(folder).iterdir()) if p.is_file()}


def stage(install: Path, build: int = 7, files=None, seed=SEED, settings_file=None, run_args=None, manager="direct", from_build: int = 6, keys=None) -> Path:
    """What the Courier's `stage` step leaves behind: update/staging/<build>/ with the manifest, signature and files, and a `staged` journal."""
    files = files_for(build) if files is None else files
    manifest = manifest_bytes(files, build)
    update = Path(install) / "update"
    here = update / "staging" / str(build)
    here.mkdir(parents=True)
    for n, d in files.items():
        (here / n).write_bytes(d)
    (here / "manifest.json").write_bytes(manifest)
    (here / "manifest.sig").write_bytes(base64.b64encode(uv.ed25519_sign(seed, manifest)))
    upd.write_journal(update, {"state": "staged", "from_build": from_build, "to_build": build, "version": "1.0.0", "keys": keys or [PUBLIC_B64],
                               "settings_file": str(settings_file) if settings_file else "", "run_args": run_args or ["run"], "manager": manager, "staged_at": 0})
    return here
