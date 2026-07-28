"""Password hashing (bcrypt) and JWT access tokens (PyJWT)."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, shop_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "shop_id": shop_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def create_agent_token(connector_id: str, shop_id: str) -> str:
    """Long-lived token for a paired desktop agent (scoped to one connector)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": connector_id,
        "shop_id": shop_id,
        "type": "agent",
        "iat": now,
        "exp": now + timedelta(days=365),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
