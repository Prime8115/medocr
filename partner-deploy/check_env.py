"""Check the self-hosted deployment's values before Docker sees them.

This deliberately prints only field names and errors, never secret values.
Use plain KEY=value lines in partner-deploy/.env (no shell expansion).
"""

from __future__ import annotations

import base64
import binascii
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


DOMAIN = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]*")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {number}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"line {number}: invalid variable name")
        if key in values:
            raise ValueError(f"line {number}: duplicate {key}")
        values[key] = value.strip().strip('"').strip("'")
    return values


def validate(values: dict[str, str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = ("COMPOSE_PROJECT_NAME", "API_DOMAIN", "WEB_DOMAIN", "POSTGRES_PASSWORD",
                "DATABASE_URL", "JWT_SECRET", "ENVIRONMENT", "ALLOW_MOCK_OCR",
                "CORS_ORIGINS", "STORAGE_BACKEND", "STORAGE_LOCAL_DIR")
    for key in required:
        if not values.get(key):
            errors.append(f"{key} is required")

    project = values.get("COMPOSE_PROJECT_NAME", "")
    if project and not PROJECT.fullmatch(project):
        errors.append("COMPOSE_PROJECT_NAME must use lowercase letters, digits, _ or -")

    for key in ("API_DOMAIN", "WEB_DOMAIN"):
        domain = values.get(key, "").lower()
        reserved = domain.endswith((".example", ".test", ".invalid", ".localhost",
                                    ".example.com", ".example.net", ".example.org"))
        if domain and (not DOMAIN.fullmatch(domain) or reserved):
            errors.append(f"{key} must be a real DNS hostname without https://")
    if values.get("API_DOMAIN", "").lower() == values.get("WEB_DOMAIN", "").lower():
        errors.append("API_DOMAIN and WEB_DOMAIN must differ")

    ports: dict[str, int] = {}
    for key in ("MEDISCAN_API_PORT", "MEDISCAN_WEB_PORT"):
        raw = values.get(key, "")
        if raw:
            if not raw.isdecimal() or not 1024 <= int(raw) <= 65535:
                errors.append(f"{key} must be an unused port from 1024 to 65535")
            else:
                ports[key] = int(raw)
    if len(ports) == 2 and len(set(ports.values())) != 2:
        errors.append("MEDISCAN_API_PORT and MEDISCAN_WEB_PORT must differ")

    password = values.get("POSTGRES_PASSWORD", "")
    if password and (len(password) < 24 or not re.fullmatch(r"[A-Za-z0-9]+", password)):
        errors.append("POSTGRES_PASSWORD must be at least 24 alphanumeric characters; use random hex")

    url = values.get("DATABASE_URL", "")
    if url:
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "postgresql+psycopg2" or parsed.hostname != "db"
                    or parsed.username != "mediscan" or parsed.path != "/mediscan"
                    or parsed.port != 5432 or unquote(parsed.password or "") != password):
                errors.append("DATABASE_URL must match the db service and POSTGRES_PASSWORD")
        except ValueError:
            errors.append("DATABASE_URL is not a valid PostgreSQL URL")

    jwt = values.get("JWT_SECRET", "")
    if jwt and (len(jwt) < 32 or "replace" in jwt.lower() or "change-me" in jwt.lower()):
        errors.append("JWT_SECRET must be a separate random secret of at least 32 characters")
    if not (values.get("GEMINI_API_KEY", "").strip() or values.get("GEMINI_API_KEYS", "").strip()):
        errors.append("GEMINI_API_KEY or GEMINI_API_KEYS is required for OCR")
    elif "replace" in values.get("GEMINI_API_KEY", "").lower():
        errors.append("GEMINI_API_KEY still has the example value")

    if values.get("ENVIRONMENT", "").lower() != "production":
        errors.append("ENVIRONMENT must be production")
    if values.get("ALLOW_MOCK_OCR", "").lower() != "false":
        errors.append("ALLOW_MOCK_OCR must be false")
    if values.get("STORAGE_BACKEND", "").lower() != "local":
        errors.append("This Compose stack expects STORAGE_BACKEND=local")
    if values.get("STORAGE_LOCAL_DIR", "") != "/app/storage":
        errors.append("STORAGE_LOCAL_DIR must be /app/storage for the persistent volume")
    expected_origin = "https://" + values.get("WEB_DOMAIN", "")
    origins = [part.strip() for part in values.get("CORS_ORIGINS", "").split(",")]
    if "*" in origins or expected_origin not in origins:
        errors.append("CORS_ORIGINS must include https://WEB_DOMAIN and must not include *")

    keys = values.get("SOURCE_ENCRYPTION_KEYS", "").strip()
    if not keys:
        warnings.append("SOURCE_ENCRYPTION_KEYS is empty; exact original uploads will not be retained")
    else:
        for part in keys.split(","):
            try:
                if len(base64.urlsafe_b64decode(part.strip())) != 32:
                    raise ValueError("wrong key length")
            except (ValueError, binascii.Error):
                errors.append("SOURCE_ENCRYPTION_KEYS contains an invalid Fernet key")
                break
    return errors, warnings


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 partner-deploy/check_env.py partner-deploy/.env", file=sys.stderr)
        return 2
    try:
        values = read_env(Path(sys.argv[1]))
    except (OSError, ValueError) as exc:
        print(f"Environment file error: {exc}", file=sys.stderr)
        return 2
    errors, warnings = validate(values)
    for warning in warnings:
        print(f"Warning: {warning}")
    for error in errors:
        print(f"Error: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Environment preflight passed. No secret values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
