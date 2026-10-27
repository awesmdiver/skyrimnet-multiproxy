"""Checking a signed Courier update package (standard library only).

One module, three users: the Courier (before it stages anything), the update helper (right before it swaps files) and the main device
(before it sends a package), so all three make the same decision. The release signer (scripts/sign_courier_package.py) uses the same
Ed25519 code to sign. See docs/security-review-update-courier-design.md (M1 to M3, M11) for why each check is here.

What a package is: a zip with `manifest.json` (the raw bytes that are signed), `manifest.sig` (the Ed25519 signature, base64) and every file the
manifest lists, flat, nothing else. The manifest names the product, the build number, a `runtime_id` and each file's size and SHA-256. The
signature is checked over the RAW manifest bytes before the manifest is parsed. A fingerprint the sender gives is never the trust.

Ed25519 here is the reference algorithm of RFC 8032 section 6 in plain Python integers (checked against the RFC's own test vectors in
tests/test_update_verify.py, and against the `cryptography` package when that is installed). It is slow (tens of milliseconds) and not
constant-time: fine for verifying one small manifest, and for an offline signing tool; do not reuse it for anything else.
"""
import base64
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from typing import Optional

# ---- limits (M3) ---------------------------------------------------------------------------------------------------------------------------------
MAX_PACKAGE_BYTES = 64 * 1024 ** 2
MAX_MEMBERS = 100
MAX_MEMBER_BYTES = 16 * 1024 ** 2
MAX_UNPACKED_BYTES = 128 * 1024 ** 2
MAX_MANIFEST_BYTES = 1024 ** 2
MAX_SIGNATURE_BYTES = 4096

FORMAT = 1
PRODUCT = "multiproxy-courier"
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.sig"
BUILD_FILE = "courier.py"
REQUIREMENTS_FILE = "requirements-courier.txt"

# Flat names only: no folder, no leading dot, no "..".
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,60}\.(py|txt|png|ico)$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_BUILD_LINE = re.compile(r"^COURIER_BUILD\s*=\s*(\d+)\s*(?:#.*)?$", re.M)

# The fixed reasons that may cross the wire or reach the row (never exception text, a path or a file name).
CODES = ("bad_signature", "not_newer", "wrong_build", "damaged", "too_big", "busy", "no_space", "no_permission", "runtime_changed",
         "didnt_start", "timeout", "too_many_tries", "unreachable")


class UpdateRejected(Exception):
    """A package that must not be installed. `code` is one of CODES; `detail` is for the device's own log (never shown, never sent)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ==============================================================================================================================================
# Ed25519 (RFC 8032, section 6)
# ==============================================================================================================================================
_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _compress(p) -> bytes:
    zinv = _inv(p[2])
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _h(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError("An Ed25519 private key is 32 bytes.")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte private seed."""
    return _compress(_mul(_expand(seed)[0], _G))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """The 64-byte signature (used by the release signer only; the Courier never signs)."""
    a, prefix = _expand(seed)
    public = _compress(_mul(a, _G))
    r = _h(prefix + message)
    big_r = _compress(_mul(r, _G))
    s = (r + _h(big_r + public + message) * a) % _Q
    return big_r + int.to_bytes(s, 32, "little")


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a valid signature by exactly this public key. Never raises on bad input."""
    try:
        if len(public) != 32 or len(signature) != 64:
            return False
        a = _decompress(public)
        r = _decompress(signature[:32])
        if a is None or r is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= _Q:
            return False
        h = _h(signature[:32] + public + message)
        return _equal(_mul(s, _G), _add(r, _mul(h, a)))
    except (ValueError, TypeError):
        return False


# ==============================================================================================================================================
# The package
# ==============================================================================================================================================
def runtime_id(requirements: bytes) -> str:
    """Which runtime a package needs: a hash of the requirements file with line endings and blank/comment lines normalised. A device whose own
    requirements file gives a different id cannot take the package as a code-only update (M9)."""
    text = requirements.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    return "req-" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def read_courier_build(courier_py: bytes) -> int:
    """The `COURIER_BUILD = N` number in courier.py's source. Exactly one such line, or the package is refused."""
    found = _BUILD_LINE.findall(courier_py.decode("utf-8", "replace"))
    if len(found) != 1:
        raise UpdateRejected("wrong_build", "courier.py does not state exactly one COURIER_BUILD")
    return int(found[0])


# Windows device names (a file called con.py or aux.txt is the device, not a file) and the two modules Python runs by itself at start: never allowed in a package, however it was signed.
_RESERVED_WINDOWS = frozenset({"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})
_RUNS_BY_ITSELF = frozenset({"sitecustomize", "usercustomize"})


def valid_name(name) -> bool:
    """A flat file name a package may hold: the plain shape above, not a Windows device name, and no program that would stand in for a standard-library module or run at start."""
    if not (isinstance(name, str) and bool(_NAME.match(name)) and ".." not in name):
        return False
    stem = name.split(".")[0].lower()
    if stem in _RESERVED_WINDOWS:
        return False
    if name.endswith(".py"):
        module = name[:-3]
        if module.lower() in _RUNS_BY_ITSELF or module in getattr(sys, "stdlib_module_names", ()):
            return False
    return True


def _default_keys() -> list:
    import courier_keys
    return courier_keys.public_keys()


def _parse_manifest(raw: bytes) -> dict:
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateRejected("damaged", "the manifest is not JSON")
    if not isinstance(m, dict):
        raise UpdateRejected("damaged", "the manifest is not an object")
    if m.get("format") != FORMAT or m.get("product") != PRODUCT:
        raise UpdateRejected("damaged", "not a Courier package of this format")
    build = m.get("build")
    if isinstance(build, bool) or not isinstance(build, int) or build < 1:
        raise UpdateRejected("damaged", "the build number is missing")
    if not isinstance(m.get("version"), str) or not isinstance(m.get("runtime_id"), str):
        raise UpdateRejected("damaged", "the manifest is incomplete")
    files = m.get("files")
    if not isinstance(files, list) or not files:
        raise UpdateRejected("damaged", "the manifest lists no files")
    if len(files) > MAX_MEMBERS - 2:
        raise UpdateRejected("too_big", "too many files")
    seen, total = set(), 0
    for f in files:
        if not isinstance(f, dict) or not valid_name(f.get("name")) or f["name"] in seen:
            raise UpdateRejected("damaged", "a file name in the manifest is not allowed")
        size, sha = f.get("size"), f.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(sha, str) or not _SHA.match(sha):
            raise UpdateRejected("damaged", "a file entry in the manifest is incomplete")
        if size > MAX_MEMBER_BYTES:
            raise UpdateRejected("too_big", "a file is too big")
        seen.add(f["name"])
        total += size
    if total > MAX_UNPACKED_BYTES:
        raise UpdateRejected("too_big", "the package unpacks to too much")
    if BUILD_FILE not in seen or REQUIREMENTS_FILE not in seen:
        raise UpdateRejected("damaged", "courier.py or the requirements file is missing")
    return m


def _read_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.flag_bits & 0x1:
        raise UpdateRejected("damaged", "an encrypted file")
    if info.file_size > limit:
        raise UpdateRejected("too_big", "a file is too big")
    try:
        with z.open(info) as f:
            data = f.read(limit + 1)
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, ValueError, RuntimeError):
        raise UpdateRejected("damaged", "a file in the zip cannot be read")
    if len(data) > limit:
        raise UpdateRejected("too_big", "a file is bigger than it says")
    return data


def verify_package(package, keys: Optional[list] = None, running_build: Optional[int] = None) -> tuple:
    """Check a package completely and return (manifest, {name: bytes}). Raises UpdateRejected(code) for anything wrong."""
    manifest, members, _raw, _sig = verify_package_raw(package, keys, running_build)
    return manifest, members


def verify_package_raw(package, keys: Optional[list] = None, running_build: Optional[int] = None) -> tuple:
    """`verify_package`, and the very bytes that were checked as well: (manifest, {name: bytes}, raw manifest, raw signature file). A caller that writes the manifest and signature down
    again (the staging) writes these, never a second read of the package.

    `package` is a path or the bytes. `keys` is a list of 32-byte public keys; the default is the Courier's compiled-in list (courier_keys.py).
    `running_build` is the build of the Courier that would install it; when given, the package must be strictly newer (equal is refused)."""
    keys = _default_keys() if keys is None else list(keys)
    if not keys:
        raise UpdateRejected("bad_signature", "no public key is set")
    if isinstance(package, (bytes, bytearray)):
        blob = bytes(package)
        if len(blob) > MAX_PACKAGE_BYTES:
            raise UpdateRejected("too_big", "the package is too big")
    else:
        try:
            if os.path.getsize(package) > MAX_PACKAGE_BYTES:
                raise UpdateRejected("too_big", "the package is too big")
            with open(package, "rb") as fh:
                blob = fh.read(MAX_PACKAGE_BYTES + 1)
        except OSError:
            raise UpdateRejected("damaged", "the package cannot be read")
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise UpdateRejected("damaged", "not a zip file")
    with z:
        infos = z.infolist()
        if len(infos) > MAX_MEMBERS:
            raise UpdateRejected("too_big", "too many files")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise UpdateRejected("damaged", "a file name appears twice")
        for i in infos:
            mode = (i.external_attr >> 16) & 0xFFFF
            kind = stat.S_IFMT(mode)          # 0 when the zip records permissions only (what Python's own writer does)
            if i.is_dir() or (kind and kind != stat.S_IFREG):
                raise UpdateRejected("damaged", "a folder or link in the package")
            if i.filename not in (MANIFEST_NAME, SIGNATURE_NAME) and not valid_name(i.filename):
                raise UpdateRejected("damaged", "a file name in the package is not allowed")
        by_name = {i.filename: i for i in infos}
        if MANIFEST_NAME not in by_name or SIGNATURE_NAME not in by_name:
            raise UpdateRejected("bad_signature", "the package is not signed")
        raw = _read_member(z, by_name[MANIFEST_NAME], MAX_MANIFEST_BYTES)
        raw_sig = _read_member(z, by_name[SIGNATURE_NAME], MAX_SIGNATURE_BYTES)
        try:
            sig = base64.b64decode(raw_sig.strip(), validate=True)
        except ValueError:
            raise UpdateRejected("bad_signature", "the signature is not readable")
        # The signature comes first: nothing in the manifest is believed (not even parsed) until a key we carry has signed these exact bytes.
        if not any(ed25519_verify(k, raw, sig) for k in keys):
            raise UpdateRejected("bad_signature", "no key we carry signed this")
        manifest = _parse_manifest(raw)
        listed = {f["name"]: f for f in manifest["files"]}
        if set(by_name) != set(listed) | {MANIFEST_NAME, SIGNATURE_NAME}:
            raise UpdateRejected("damaged", "the files in the package are not the files the manifest lists")
        members = {}
        for name, entry in listed.items():
            data = _read_member(z, by_name[name], MAX_MEMBER_BYTES)
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise UpdateRejected("damaged", "a file does not match the manifest")
            members[name] = data
    _check_members(manifest, members, running_build)
    return manifest, members, raw, raw_sig


def _check_members(manifest: dict, members: dict, running_build: Optional[int]) -> None:
    if read_courier_build(members[BUILD_FILE]) != manifest["build"]:
        raise UpdateRejected("wrong_build", "courier.py says a different build than the manifest")
    if runtime_id(members[REQUIREMENTS_FILE]) != manifest["runtime_id"]:
        raise UpdateRejected("damaged", "the runtime id does not match the requirements file")
    if running_build is not None and manifest["build"] <= running_build:
        raise UpdateRejected("not_newer", "build %d is not newer than %d" % (manifest["build"], running_build))


def verify_staged(folder, keys: Optional[list] = None, running_build: Optional[int] = None) -> dict:
    """The same decision as verify_package, for a package already unpacked into a folder: `manifest.json`, `manifest.sig` and exactly the files the manifest
    lists (the update helper runs this right before it swaps anything, so a file changed on disk since it was staged is caught). Returns the manifest."""
    keys = _default_keys() if keys is None else list(keys)
    if not keys:
        raise UpdateRejected("bad_signature", "no public key is set")
    folder = os.fspath(folder)
    try:
        names = sorted(os.listdir(folder))
        for n in names:
            if os.path.islink(os.path.join(folder, n)) or not os.path.isfile(os.path.join(folder, n)):
                raise UpdateRejected("damaged", "a folder or link in the staged files")
        if MANIFEST_NAME not in names or SIGNATURE_NAME not in names:
            raise UpdateRejected("bad_signature", "the staged files are not signed")
        raw = _read_file(os.path.join(folder, MANIFEST_NAME), MAX_MANIFEST_BYTES)
        sig = base64.b64decode(_read_file(os.path.join(folder, SIGNATURE_NAME), MAX_SIGNATURE_BYTES).strip(), validate=True)
    except ValueError:
        raise UpdateRejected("bad_signature", "the signature is not readable")
    except OSError:
        raise UpdateRejected("damaged", "the staged files cannot be read")
    if not any(ed25519_verify(k, raw, sig) for k in keys):
        raise UpdateRejected("bad_signature", "no key we carry signed this")
    manifest = _parse_manifest(raw)
    listed = {f["name"]: f for f in manifest["files"]}
    if set(names) != set(listed) | {MANIFEST_NAME, SIGNATURE_NAME}:
        raise UpdateRejected("damaged", "the staged files are not the files the manifest lists")
    members = {}
    for name, entry in listed.items():
        try:
            data = _read_file(os.path.join(folder, name), MAX_MEMBER_BYTES)
        except OSError:
            raise UpdateRejected("damaged", "a staged file cannot be read")
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise UpdateRejected("damaged", "a staged file does not match the manifest")
        members[name] = data
    _check_members(manifest, members, running_build)
    return manifest


def _read_file(path: str, limit: int) -> bytes:
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise UpdateRejected("too_big", "a file is too big")
    return data


# What crosses the wire to the main device (and into the row): a short, fixed set. The finer codes above are for the device's own log.
WIRE_CODES = ("bad_signature", "not_newer", "bad_package", "busy", "no_space", "runtime_changed", "update_failed", "rolled_back")


def to_wire(code: str) -> str:
    """The wire code for an internal reason: file/format problems all say `bad_package`; anything unknown says `update_failed`."""
    if code in ("damaged", "wrong_build", "too_big"):
        return "bad_package"
    return code if code in WIRE_CODES else "update_failed"
