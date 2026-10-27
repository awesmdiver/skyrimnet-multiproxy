"""The public keys a Courier trusts to sign its updates (compiled in; never fetched, never read from a setting, a file or the environment).

CURRENT signs today's releases. NEXT is empty until the next key is generated; it ships one release ahead so a rotation never needs a visit to
every device: release N carries the new key as NEXT, release N+1 is signed with it and moves it to CURRENT (docs/RELEASING-THE-COURIER.md).

A public key is not a secret. The matching PRIVATE key is the director's own and is never in this repository.
"""
import base64

# A TEST key, public on purpose: its private half is written in tests/test_update_verify.py, so it protects nothing. It is here so the code can be
# exercised before the director adds the real key. The release signer refuses to sign while CURRENT is still this key (and
# tests/test_courier_release_gate.py fails a release build the same way).
TEST_PUBLIC_KEY = "dZR05AH+KELxnhvmMRaVUTBhx4hTqkjs6DoL2KHQvE8="

CURRENT = "2Zrk+k9DTL3lryOdF9oFQB/9KL/GzMUQTVIGROnF5qg="       # the director's release key (public half; the private half is never in this repository)
NEXT = ""


def is_test_key() -> bool:
    return CURRENT == TEST_PUBLIC_KEY


def decode_key(text: str) -> bytes:
    """A base64 public key as its 32 bytes (ValueError when it is not one)."""
    raw = base64.b64decode(text.strip(), validate=True)
    if len(raw) != 32:
        raise ValueError("A public key is 32 bytes.")
    return raw


def public_keys() -> list:
    """Every key a package may be signed by, as 32-byte values: CURRENT, and NEXT when it is set."""
    return [decode_key(k) for k in (CURRENT, NEXT) if k and k.strip()]
