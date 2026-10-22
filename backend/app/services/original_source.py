"""Authenticated encryption for original uploads retained beside OCR copies.

The normal processed image_ref is deliberately unchanged. Keeping an exact
original is opt-in until a durable key has been provisioned. Multiple keys
allow rotation: the first encrypts, and all configured keys can read.
"""
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.services.storage import storage


_MAGIC = b"MSRC1\n"


def _keys() -> list[Fernet]:
    return [Fernet(value.strip().encode("ascii"))
            for value in settings.source_encryption_keys.split(",") if value.strip()]


def save(data: bytes, filename: str) -> str | None:
    keys = _keys()
    if not keys:
        return None
    encrypted = _MAGIC + keys[0].encrypt(data)
    return storage.save(encrypted, filename + ".msrc", "application/octet-stream")


def load(ref: str, expected_sha256: str | None = None) -> bytes:
    encrypted = storage.load(ref)
    if not encrypted.startswith(_MAGIC):
        raise ValueError("The stored original has an unknown format.")
    for key in _keys():
        try:
            data = key.decrypt(encrypted[len(_MAGIC):])
        except InvalidToken:
            continue
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("The stored original did not pass its integrity check.")
        return data
    raise ValueError("The stored original cannot be decrypted with the configured keys.")
