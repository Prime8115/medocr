"""Deployment checks must reject unsafe combinations before containers start."""

import tempfile
import unittest
from pathlib import Path

from check_env import read_env, validate


def example() -> dict[str, str]:
    password = "a" * 48
    return {
        "COMPOSE_PROJECT_NAME": "mediscan",
        "API_DOMAIN": "api.pilot-pharmacy.in",
        "WEB_DOMAIN": "app.pilot-pharmacy.in",
        "MEDISCAN_API_PORT": "8090",
        "MEDISCAN_WEB_PORT": "8091",
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg2://mediscan:{password}@db:5432/mediscan",
        "JWT_SECRET": "b" * 48,
        "GEMINI_API_KEY": "test-key-not-a-live-credential",
        "ENVIRONMENT": "production",
        "ALLOW_MOCK_OCR": "false",
        "CORS_ORIGINS": "https://app.pilot-pharmacy.in",
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_DIR": "/app/storage",
        "SOURCE_ENCRYPTION_KEYS": "",
    }


class EnvironmentChecks(unittest.TestCase):
    def test_valid_stack_passes_with_retention_warning(self):
        errors, warnings = validate(example())
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)

    def test_example_and_mismatched_database_password_fail(self):
        values = example()
        values["API_DOMAIN"] = "api.example.com"
        values["POSTGRES_PASSWORD"] = "c" * 48
        errors, _ = validate(values)
        self.assertTrue(any("API_DOMAIN" in error for error in errors))
        self.assertTrue(any("DATABASE_URL" in error for error in errors))

    def test_mock_ocr_and_wrong_cors_fail(self):
        values = example()
        values["ALLOW_MOCK_OCR"] = "true"
        values["CORS_ORIGINS"] = "*"
        errors, _ = validate(values)
        self.assertTrue(any("ALLOW_MOCK_OCR" in error for error in errors))
        self.assertTrue(any("CORS_ORIGINS" in error for error in errors))

    def test_proxy_ports_must_be_distinct_and_unprivileged(self):
        values = example()
        values["MEDISCAN_API_PORT"] = "443"
        values["MEDISCAN_WEB_PORT"] = "443"
        errors, _ = validate(values)
        self.assertTrue(any("MEDISCAN_API_PORT" in error for error in errors))
        values["MEDISCAN_API_PORT"] = "8091"
        values["MEDISCAN_WEB_PORT"] = "8091"
        errors, _ = validate(values)
        self.assertTrue(any("must differ" in error for error in errors))

    def test_duplicate_key_is_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="test_env_",
                                         dir=Path(__file__).parent, delete=False) as handle:
            handle.write("JWT_SECRET=first\nJWT_SECRET=second\n")
            path = Path(handle.name)
        try:
            with self.assertRaisesRegex(ValueError, "duplicate JWT_SECRET"):
                read_env(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
