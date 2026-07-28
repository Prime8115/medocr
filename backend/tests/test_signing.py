"""HMAC webhook signing."""
from app.services.connectors.signing import sign, verify


def test_sign_is_deterministic_and_prefixed():
    body = b'{"a":1}'
    sig = sign("secret", body)
    assert sig.startswith("sha256=")
    assert sign("secret", body) == sig


def test_verify_accepts_valid_and_rejects_tampered():
    body = b'{"a":1}'
    sig = sign("secret", body)
    assert verify("secret", body, sig)
    assert not verify("secret", b'{"a":2}', sig)      # body changed
    assert not verify("wrong", body, sig)              # secret changed
    assert not verify("secret", body, "")              # missing signature
