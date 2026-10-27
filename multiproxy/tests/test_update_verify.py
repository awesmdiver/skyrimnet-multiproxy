"""The signed-package check (update_verify.py): the Ed25519 code against the RFC 8032 vectors, and every way a package can be wrong.

Only throwaway keys made here are ever used. The TEST key's private half (below) is public on purpose: it protects nothing.
"""
import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import courier_keys  # noqa: E402
import update_verify as uv  # noqa: E402

TEST_SEED = hashlib.sha256(b"multiproxy-courier TEST KEY, public on purpose, not a secret").digest()

# RFC 8032 section 7.1 (TEST 1, TEST 2).
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


@pytest.mark.parametrize("secret,public,message,signature", RFC_VECTORS)
def test_the_rfc_8032_vectors(secret, public, message, signature):
    seed, msg = bytes.fromhex(secret), bytes.fromhex(message)
    assert uv.ed25519_public_key(seed).hex() == public
    assert uv.ed25519_sign(seed, msg).hex() == signature
    assert uv.ed25519_verify(bytes.fromhex(public), msg, bytes.fromhex(signature))


def test_a_changed_message_signature_or_key_does_not_verify():
    seed = bytes.fromhex(RFC_VECTORS[1][0])
    public, sig = uv.ed25519_public_key(seed), uv.ed25519_sign(seed, b"hello")
    assert uv.ed25519_verify(public, b"hello", sig)
    assert not uv.ed25519_verify(public, b"hellp", sig)
    assert not uv.ed25519_verify(public, b"hello", sig[:-1] + bytes([sig[-1] ^ 1]))
    assert not uv.ed25519_verify(public, b"hello", bytes([sig[0] ^ 1]) + sig[1:])
    assert not uv.ed25519_verify(uv.ed25519_public_key(bytes(32)), b"hello", sig)
    assert not uv.ed25519_verify(public, b"hello", sig[:63]) and not uv.ed25519_verify(public[:31], b"hello", sig)
    # a signature whose s is not below the group order is refused (no malleable twin)
    twin = sig[:32] + int.to_bytes(int.from_bytes(sig[32:], "little") + uv._Q, 32, "little")
    assert not uv.ed25519_verify(public, b"hello", twin)


def test_it_agrees_with_the_cryptography_package_when_that_is_installed():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization
    for i in range(3):
        seed = hashlib.sha256(b"seed%d" % i).digest()
        key = ed.Ed25519PrivateKey.from_private_bytes(seed)
        raw_public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        assert uv.ed25519_public_key(seed) == raw_public
        assert uv.ed25519_sign(seed, b"manifest %d" % i) == key.sign(b"manifest %d" % i)


def test_the_compiled_in_test_key_is_the_one_whose_private_half_is_public():
    assert courier_keys.TEST_PUBLIC_KEY == base64.b64encode(uv.ed25519_public_key(TEST_SEED)).decode()
    assert courier_keys.public_keys()[0] == courier_keys.decode_key(courier_keys.CURRENT)
    assert courier_keys.NEXT == "" and len(courier_keys.public_keys()) == 1


# ---- packages -----------------------------------------------------------------------------------------------------------------------------------
SEED = hashlib.sha256(b"a throwaway key for these tests").digest()
PUBLIC = uv.ed25519_public_key(SEED)


def files(build=6, extra=None):
    out = {"courier.py": ('COURIER_VERSION = "1.0.0"\nCOURIER_BUILD = %d          # a comment\n' % build).encode(),
           "requirements-courier.txt": b"aiohttp>=3.9.0\r\n# a comment\r\n\r\npystray>=0.19.5\r\n", "other_pcs.py": b"x = 1\n"}
    out.update(extra or {})
    return out


def manifest_for(fs, build=6, **over):
    m = {"format": uv.FORMAT, "product": uv.PRODUCT, "build": build, "version": "1.0.0", "platform": "any", "runtime_id": uv.runtime_id(fs["requirements-courier.txt"]),
         "files": [{"name": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in sorted(fs.items())]}
    m.update(over)
    return json.dumps(m, sort_keys=True).encode()


def package(fs=None, manifest=None, seed=SEED, sig=None, members=None):
    fs = files() if fs is None else fs
    manifest = manifest_for(fs) if manifest is None else manifest
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", manifest)
        z.writestr("manifest.sig", base64.b64encode(uv.ed25519_sign(seed, manifest) if sig is None else sig))
        for n, d in (fs if members is None else members).items():
            z.writestr(n, d)
    return buf.getvalue()


def code_of(blob, **kw):
    with pytest.raises(uv.UpdateRejected) as e:
        uv.verify_package(blob, keys=kw.pop("keys", [PUBLIC]), **kw)
    return e.value.code


def test_a_good_package_is_accepted_and_hands_back_exactly_its_files():
    manifest, members = uv.verify_package(package(), keys=[PUBLIC])
    assert manifest["build"] == 6 and members == files()
    assert uv.verify_package(package(), keys=[PUBLIC], running_build=5)[0]["build"] == 6


def test_the_runtime_id_ignores_line_endings_comments_and_blank_lines_only():
    assert uv.runtime_id(b"a>=1\r\nb\r\n") == uv.runtime_id(b"# x\n\na>=1\nb\n")
    assert uv.runtime_id(b"a>=1\nb\n") != uv.runtime_id(b"a>=1\nb\nc\n")


def test_every_key_we_carry_may_have_signed_it_and_no_other():
    other = uv.ed25519_public_key(hashlib.sha256(b"not ours").digest())
    assert uv.verify_package(package(), keys=[other, PUBLIC])[0]
    assert code_of(package(), keys=[other]) == "bad_signature"
    assert code_of(package(seed=hashlib.sha256(b"not ours").digest())) == "bad_signature"
    assert code_of(package(), keys=[]) == "bad_signature"


def test_an_unsigned_package_and_a_changed_manifest_are_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", manifest_for(files()))
        for n, d in files().items():
            z.writestr(n, d)
    assert code_of(buf.getvalue()) == "bad_signature"
    fs = files()
    signed = manifest_for(fs)
    assert code_of(package(fs, manifest=signed.replace(b'"build": 6', b'"build": 7'), sig=uv.ed25519_sign(SEED, signed))) == "bad_signature"
    assert code_of(package(sig=b"\0" * 64)) == "bad_signature"


def test_a_changed_byte_in_a_file_is_refused_even_though_the_manifest_is_signed():
    fs = files()
    manifest = manifest_for(fs)
    evil = dict(fs, **{"other_pcs.py": b"x = 2\n"})
    assert code_of(package(fs, manifest, members=evil)) == "damaged"


def test_a_file_swapped_for_one_of_the_same_size_and_a_matching_fingerprint_still_needs_the_signature():
    """The attacker controls the zip and every hash in it, but not the signed manifest: the manifest's hash decides."""
    fs = files()
    evil = dict(fs, **{"other_pcs.py": b"x = 2\n"})
    forged = manifest_for(evil)                                         # a manifest that matches the evil file, signed by someone else
    assert code_of(package(evil, forged, seed=hashlib.sha256(b"attacker").digest())) == "bad_signature"


def test_an_extra_file_and_a_missing_file_are_refused():
    fs = files()
    assert code_of(package(fs, members=dict(fs, **{"evil.py": b"import os"}))) == "damaged"
    assert code_of(package(fs, members={k: v for k, v in fs.items() if k != "other_pcs.py"})) == "damaged"


def test_a_name_that_climbs_out_or_a_folder_or_a_link_is_refused():
    fs = files()
    for bad in ("../evil.py", "sub/evil.py", "/evil.py", "C:evil.py", ".hidden.py", "evil.exe"):
        assert code_of(package(fs, members=dict(fs, **{bad: b"x"}))) == "damaged", bad
    manifest = manifest_for(dict(fs, **{"../evil.py": b"x"}))
    assert code_of(package(dict(fs, **{"../evil.py": b"x"}), manifest)) == "damaged"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", manifest_for(fs))
        z.writestr("manifest.sig", base64.b64encode(uv.ed25519_sign(SEED, manifest_for(fs))))
        for n, d in fs.items():
            z.writestr(n, d)
        link = zipfile.ZipInfo("evil.py")
        link.external_attr = 0o120777 << 16
        z.writestr(link, "courier.py")
    assert code_of(buf.getvalue()) == "damaged"


def test_a_build_older_or_equal_is_refused_and_the_build_must_be_the_signed_one():
    assert code_of(package(), running_build=6) == "not_newer"
    assert code_of(package(), running_build=9) == "not_newer"
    fs = files(build=5)                                                 # courier.py says 5 ...
    assert code_of(package(fs, manifest_for(fs, build=6))) == "wrong_build"      # ... the signed manifest says 6
    fs = files()
    two = dict(fs, **{"courier.py": fs["courier.py"] + b"COURIER_BUILD = 7\n"})
    assert code_of(package(two)) == "wrong_build"                       # not exactly one build line


def test_a_manifest_naming_another_runtime_than_its_requirements_is_refused():
    fs = files()
    assert code_of(package(fs, manifest_for(fs, runtime_id="req-0000000000000000"))) == "damaged"


def test_other_products_formats_and_odd_manifests_are_refused():
    fs = files()
    for over in ({"product": "something-else"}, {"format": 2}, {"build": "6"}, {"build": True}, {"build": 0}):
        assert code_of(package(fs, manifest_for(fs, **over))) in ("damaged", "wrong_build"), over
    assert code_of(package(fs, b"[1, 2]")) == "damaged"
    assert code_of(package(fs, b"\xff\xfe not json")) == "damaged"


def test_size_limits(monkeypatch):
    monkeypatch.setattr(uv, "MAX_MEMBER_BYTES", 10)
    assert code_of(package()) == "too_big"
    monkeypatch.undo()
    monkeypatch.setattr(uv, "MAX_UNPACKED_BYTES", 20)
    assert code_of(package()) == "too_big"
    monkeypatch.undo()
    monkeypatch.setattr(uv, "MAX_MEMBERS", 3)
    assert code_of(package()) == "too_big"
    monkeypatch.undo()
    monkeypatch.setattr(uv, "MAX_PACKAGE_BYTES", 100)
    assert code_of(package()) == "too_big"


def test_something_that_is_not_a_zip_or_not_there_is_refused(tmp_path):
    assert code_of(b"not a zip at all") == "damaged"
    with pytest.raises(uv.UpdateRejected) as e:
        uv.verify_package(tmp_path / "nothing.zip", keys=[PUBLIC])
    assert e.value.code == "damaged"
    p = tmp_path / "ok.zip"
    p.write_bytes(package())
    assert uv.verify_package(p, keys=[PUBLIC])[0]["build"] == 6


def test_the_reasons_that_can_cross_the_wire_are_a_fixed_list_and_carry_no_paths():
    assert set(uv.CODES) >= {"bad_signature", "not_newer", "wrong_build", "damaged", "too_big"}
    for blob in (b"junk", package(manifest=b"{}")):
        with pytest.raises(uv.UpdateRejected) as e:
            uv.verify_package(blob, keys=[PUBLIC])
        assert e.value.code in uv.CODES


def test_the_module_needs_nothing_outside_the_standard_library_and_the_key_list():
    text = (ROOT / "update_verify.py").read_text(encoding="utf-8")
    import re
    imports = set(re.findall(r"^\s*(?:import|from) ([A-Za-z_0-9]+)", text, flags=re.M))
    assert imports <= set(sys.stdlib_module_names) | {"courier_keys"}, imports
