"""The Courier's release signer: makes the signed update package the main device sends to a Courier, and checks one.

    python scripts/sign_courier_package.py --generate-key <folder outside every repository>
    python scripts/sign_courier_package.py --sign <staged Courier folder> --key <key file> --out <package.zip>
    python scripts/sign_courier_package.py --verify <package.zip> [--pubkey <base64 public key>]
    python scripts/sign_courier_package.py --mark-released <package.zip>

The DIRECTOR runs --generate-key and --sign on his own machine; the private key never goes into the repository, a prompt, the queue, a log or a working tree
(docs/security-review-update-courier-design.md M11, docs/RELEASING-THE-COURIER.md). This script never prints, logs or echoes the key.

--sign builds the manifest from the real staged files (name, size, SHA-256, the build number read from courier.py's COURIER_BUILD, the runtime id), signs the raw
manifest bytes with Ed25519, writes the zip, then checks the zip it just wrote with the same code the Courier uses (update_verify.py). It refuses: a key inside any
git working tree or readable by other accounts; a key that is not CURRENT or NEXT in courier_keys.py; a CURRENT key that is still the public test key; a build that is
not above the last released one (release/courier-last-released-build.txt); a staged folder with a missing, unknown or extra file.
"""
import argparse
import ast
import base64
import datetime
import getpass
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import courier_files          # noqa: E402  (the one list of the Courier's files)
import courier_keys           # noqa: E402
import update_verify as uv    # noqa: E402

KEY_FILE_NAME = "courier-signing-key.txt"
KEY_HEADER = "multiproxy-courier-signing-key v1"
RELEASE_FILE = ROOT / "release" / "courier-last-released-build.txt"


class Refused(Exception):
    """A plain reason the tool will not go on. Never holds key material."""


# ---- where a key may live ---------------------------------------------------------------------------------------------------------------------------
def inside_a_repository(path: Path) -> bool:
    """True when `path` (which need not exist yet) is inside this project or inside any folder that is a git working tree."""
    p = Path(os.path.abspath(path))
    try:
        p.relative_to(ROOT)
        return True
    except ValueError:
        pass
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return True
    return False


def _icacls(path: Path) -> str:
    try:
        return subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def readable_by_others(path: Path) -> bool:
    """Where the platform tells us: a mode with group/other bits (POSIX), or an Everyone / Users / Authenticated Users entry (Windows)."""
    if os.name != "nt":
        return bool(path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))
    text = _icacls(path).lower()
    return any(who in text for who in ("everyone:", "builtin\\users:", "authenticated users:"))


def lock_down(path: Path) -> bool:
    """Make the key file the owner's alone. Returns False when this platform would not let us (the caller says so)."""
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
            return True
        res = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:F"], capture_output=True, text=True, timeout=20)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---- keys ---------------------------------------------------------------------------------------------------------------------------------------------
def generate_key(folder) -> str:
    """Make the private key file in `folder` and return the PUBLIC key (base64). The folder must be outside every repository."""
    folder = Path(folder)
    if inside_a_repository(folder):
        raise Refused("That folder is inside a repository (or this project). Choose a folder outside every repository, on a disk you back up.")
    key_path = folder / KEY_FILE_NAME
    if key_path.exists():
        raise Refused("A signing key is already in that folder. It was not touched.")
    folder.mkdir(parents=True, exist_ok=True)
    seed = secrets.token_bytes(32)
    with open(key_path, "x", encoding="utf-8", newline="\n") as f:
        f.write(KEY_HEADER + "\n" + base64.b64encode(seed).decode("ascii") + "\n")
    if not lock_down(key_path):
        print("Warning: this system would not let me restrict the key file to your account. Check who can read it.", file=sys.stderr)
    return base64.b64encode(uv.ed25519_public_key(seed)).decode("ascii")


def read_key(key_file) -> bytes:
    """The 32-byte private seed from a key file, after the location and permission checks. Nothing about the key is ever put in a message."""
    path = Path(key_file)
    if inside_a_repository(path):
        raise Refused("That key file is inside a repository (or this project). A signing key must never be in a working tree. Move it out and try again.")
    if not path.is_file():
        raise Refused("There is no key file at that path.")
    if readable_by_others(path):
        raise Refused("Other accounts on this computer can read that key file. Restrict it to your own account and try again.")
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        if len(lines) < 2 or lines[0].strip() != KEY_HEADER:
            raise ValueError
        seed = base64.b64decode(lines[1].strip(), validate=True)
        if len(seed) != 32:
            raise ValueError
    except ValueError:
        raise Refused("That file is not a Courier signing key.")
    return seed


# ---- the last released build ------------------------------------------------------------------------------------------------------------------------
def last_released_build() -> int:
    try:
        for ln in RELEASE_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                return int(ln)
    except (OSError, ValueError):
        pass
    raise Refused(f"Cannot read the last released build from {RELEASE_FILE.name}. It must hold one whole number.")


# ---- the manifest and the package -------------------------------------------------------------------------------------------------------------------
def left_out(staged) -> list:
    """The names in the staged folder that only the installers carry, so a signed update leaves them out (the script says so on screen)."""
    only = set(courier_files.installer_only()) | set(courier_files.installer_only_files())
    return sorted(p.name for p in Path(staged).iterdir() if p.is_file() and p.name in only)


def gather(staged) -> dict:
    """{name: bytes} of the staged Courier folder, after refusing a missing, unknown or extra file."""
    folder = Path(staged)
    if not folder.is_dir():
        raise Refused("The staged Courier folder is not there.")
    allowed = set(courier_files.update_set()) | {courier_files.STAMP_NAME}
    present = {}
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            raise Refused(f"The staged folder holds a folder ({entry.name}). Only the Courier's own files may be signed.")
        if entry.name in courier_files.installer_only() or entry.name in courier_files.installer_only_files():
            continue          # what only the Windows installer carries (its helper, the start program) sits in its stage; it is never part of an update
        if entry.name not in allowed:
            raise Refused(f"The staged folder holds a file that is not part of an update ({entry.name}). Nothing was signed.")
        data = entry.read_bytes()
        if KEY_HEADER.encode("ascii") in data:
            raise Refused(f"A staged file ({entry.name}) looks like a signing key. Nothing was signed.")
        present[entry.name] = data
    missing = [n for n in courier_files.update_set() if n not in present]
    if missing:
        raise Refused(f"The staged folder is missing {', '.join(missing)}. Nothing was signed.")
    return present


def check_staged_keys(files: dict, public: bytes, allow_test_key: bool = False) -> None:
    """The Courier these files make must trust the key that signs them (or the next update could never be installed) and must not trust the public TEST key (its private half is
    in the tests, so anyone could sign an update for it). Read from the staged courier_keys.py itself, not from this project's copy: a stage built before the real key was pasted
    in would otherwise be signed with the real key and still carry the test one (security review of the 4.0 release, M2)."""
    try:
        consts = {}
        for node in ast.parse(files["courier_keys.py"].decode("utf-8")).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v = node.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    consts[node.targets[0].id] = v.value
                elif isinstance(v, ast.Name) and v.id in consts:
                    consts[node.targets[0].id] = consts[v.id]
        carried = [consts.get("CURRENT", ""), consts.get("NEXT", "")]
        decoded = [courier_keys.decode_key(k) for k in carried if k.strip()]
    except (KeyError, SyntaxError, ValueError, UnicodeDecodeError):
        raise Refused("The staged courier_keys.py could not be read, so the keys it carries could not be checked. Build the Courier again. Nothing was signed.")
    if public not in decoded:
        raise Refused("The staged files do not carry the public key this package is signed with, so that Courier could not take the next update. Build the Courier again "
                      "after the public key is in courier_keys.py. Nothing was signed.")
    if not allow_test_key and base64.b64decode(courier_keys.TEST_PUBLIC_KEY) in decoded:
        raise Refused("The staged files still trust the public test key. Build the Courier again after the real public key is in courier_keys.py. Nothing was signed.")


def build_manifest(files: dict, created=None) -> bytes:
    build = uv.read_courier_build(files[uv.BUILD_FILE])
    try:
        version = courier_files.version_of(files[uv.BUILD_FILE].decode("utf-8", "replace"))        # 1.<build>, the way courier.py works it out
    except ValueError:
        raise Refused("courier.py does not state its COURIER_BUILD.")
    manifest = {
        "format": uv.FORMAT,
        "product": uv.PRODUCT,
        "build": build,
        "version": version,
        "platform": "any",
        "runtime_id": uv.runtime_id(files[uv.REQUIREMENTS_FILE]),
        "created": created or datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        "files": [{"name": n, "size": len(files[n]), "sha256": hashlib.sha256(files[n]).hexdigest()} for n in sorted(files)],
    }
    return (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")


def write_package(out: Path, manifest: bytes, signature: bytes, files: dict) -> None:
    def info(name):
        zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))       # the same bytes in, the same zip out
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        return zi
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(info(uv.MANIFEST_NAME), manifest)
        z.writestr(info(uv.SIGNATURE_NAME), base64.b64encode(signature) + b"\n")
        for name in sorted(files):
            z.writestr(info(name), files[name])


def sign(staged, key_file, out, allow_test_key: bool = False) -> dict:
    seed = read_key(key_file)
    public = uv.ed25519_public_key(seed)
    trusted = courier_keys.public_keys()
    if public not in trusted:
        raise Refused("That key is not the CURRENT or NEXT key in courier_keys.py, so a Courier would not trust the package. Nothing was signed.")
    if courier_keys.is_test_key() and not allow_test_key:
        raise Refused("courier_keys.py still has the public TEST key as CURRENT. Put the real public key there first (docs/RELEASING-THE-COURIER.md).")
    files = gather(staged)
    check_staged_keys(files, public, allow_test_key)
    manifest = build_manifest(files)
    build = json.loads(manifest)["build"]
    last = last_released_build()
    if build <= last:
        raise Refused(f"This is build {build}, and build {last} is already released. Raise COURIER_BUILD in courier.py first. Nothing was signed.")
    out = Path(out)
    write_package(out, manifest, uv.ed25519_sign(seed, manifest), files)
    try:
        uv.verify_package(out, keys=[public], running_build=last)      # the package just written, checked by the very code a Courier runs
    except uv.UpdateRejected as e:
        out.unlink(missing_ok=True)
        raise Refused(f"The package it wrote did not pass its own check ({e.code}); it was deleted.")
    return {"build": build, "files": len(files), "out": str(out), "left_out": left_out(staged)}


def mark_released(package, pubkey=None) -> int:
    keys = [courier_keys.decode_key(pubkey)] if pubkey else None
    try:
        manifest, _ = uv.verify_package(package, keys=keys)
    except uv.UpdateRejected as e:
        raise Refused(f"That package does not pass its check ({e.code}), so it was not recorded as released.")
    build = manifest["build"]
    if build <= last_released_build():
        raise Refused(f"Build {build} is not above the recorded released build; nothing changed.")
    RELEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RELEASE_FILE.write_text(f"{build}\n", encoding="utf-8")
    return build


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--generate-key", metavar="FOLDER")
    g.add_argument("--sign", metavar="STAGED_FOLDER")
    g.add_argument("--verify", metavar="PACKAGE")
    g.add_argument("--mark-released", metavar="PACKAGE")
    ap.add_argument("--key", metavar="KEY_FILE")
    ap.add_argument("--out", metavar="PACKAGE")
    ap.add_argument("--pubkey", metavar="BASE64", help="check against this public key instead of the ones in courier_keys.py")
    ap.add_argument("--allow-test-key", action="store_true", help="for tests only: sign while courier_keys.py still has the public test key")
    args = ap.parse_args(argv)
    try:
        if args.generate_key:
            pub = generate_key(args.generate_key)
            print(f"Made {KEY_FILE_NAME} in that folder. Back it up in several places; there is no way to get it back.")
            print("Public key (paste it into courier_keys.py):")
            print(pub)
        elif args.sign:
            if not args.key or not args.out:
                raise Refused("--sign needs --key and --out.")
            info = sign(args.sign, args.key, args.out, args.allow_test_key)
            if info["left_out"]:
                print("Left out of the update (the installers carry it): " + ", ".join(info["left_out"]) + ".")
            print(f"Signed build {info['build']} ({info['files']} files): {info['out']}")
        elif args.verify:
            keys = [courier_keys.decode_key(args.pubkey)] if args.pubkey else None
            try:
                manifest, _ = uv.verify_package(args.verify, keys=keys)
            except uv.UpdateRejected as e:
                print(f"Not accepted: {e.code}" + (f" ({e.detail})" if e.detail else ""))
                return 1
            print(f"OK: build {manifest['build']}, version {manifest['version']}, {len(manifest['files'])} files, signed by a key in {'--pubkey' if args.pubkey else 'courier_keys.py'}.")
        else:
            print(f"Recorded build {mark_released(args.mark_released, args.pubkey)} as released. Commit {RELEASE_FILE.name}.")
    except Refused as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 2
    except ValueError:
        print("Refused: that public key is not a valid key.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
