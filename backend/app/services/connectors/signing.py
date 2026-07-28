"""HMAC-SHA256 signing for webhook payloads.

Receivers verify authenticity by recomputing the signature over the exact raw
request body using the shared secret, then constant-time comparing.
"""
import hashlib
import hmac

SIGNATURE_HEADER = "X-MediScan-Signature"
PAYLOAD_VERSION_HEADER = "X-MediScan-Payload-Version"


def sign(secret: str, body: bytes) -> str:
    """Return 'sha256=<hexdigest>' for the given raw body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret: str, body: bytes, signature: str) -> bool:
    if not signature:
        return False
    expected = sign(secret, body)
    return hmac.compare_digest(expected, signature)
