"""Application configuration — all values are environment-driven (12-factor).

Never hardcode secrets, hosts, or ports. Everything comes from the environment
(or `.env` in development). See `.env.example` for the full list.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- App ---
    app_name: str = "MediScan OCR Connect API"
    environment: str = "development"

    # --- Security ---
    jwt_secret: str = "dev-insecure-secret-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # --- Database ---
    database_url: str = "sqlite:///./mediscan_dev.db"

    # --- CORS ---
    cors_origins: str = "http://localhost:5173,http://localhost:8081,http://localhost:19006"

    # --- OCR ---
    gemini_api_key: Optional[str] = None
    ocr_model: str = "gemini-2.5-flash"
    # Fallback model tried when the primary is overloaded/unavailable after retries.
    ocr_fallback_model: Optional[str] = "gemini-2.0-flash"
    ocr_max_retries: int = 4          # attempts per model on transient errors
    ocr_base_backoff: float = 1.5     # seconds; doubles each retry
    # Mock OCR must be OFF by default: we never fabricate medical data in real use.
    # Enabled only for tests/local demos via env ALLOW_MOCK_OCR=true.
    allow_mock_ocr: bool = False
    low_confidence_threshold: float = 0.6

    # --- Storage ---
    storage_backend: str = "local"  # "local" | "s3"
    storage_local_dir: str = "./storage"
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_endpoint_url: Optional[str] = None

    # --- Limits ---
    max_upload_mb: int = 15
    rate_limit_per_minute: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
