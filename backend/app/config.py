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
    # Multi-key pool: comma-separated list of Gemini API keys for load balancing & 429 rotation
    gemini_api_keys: Optional[str] = None
    # Maximum concurrent background OCR jobs across the server (smooth pacing for simultaneous users)
    ocr_max_concurrent_jobs: int = 6
    # Use high-throughput, low-latency Flash-Lite as primary (sub-2s latency, high quota)
    ocr_model: str = "gemini-flash-lite-latest"
    # Fallback tried when the primary is overloaded or errors
    ocr_fallback_model: Optional[str] = "gemini-2.5-flash"
    ocr_max_retries: int = 2          # attempts per model on transient errors (fast failover)
    ocr_base_backoff: float = 1.0     # seconds; doubles each retry
    # Large multi-page PDFs (e.g. long distributor invoices) are processed in
    # page-chunks and merged, to stay under the model's output-token limit.
    ocr_pdf_chunk_pages: int = 2
    ocr_max_output_tokens: int = 65536
    # Parallel chunk extraction: how many page-chunks to send to the model at
    # once. Kept low to respect the model's rate limits (429). Raise only if your
    # API tier has generous per-minute quota.
    ocr_chunk_concurrency: int = 2
    # Digital (text) PDFs are extracted from embedded text — compact & reliable —
    # allowing more pages per call than image chunks.
    ocr_text_chunk_pages: int = 6
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
    max_upload_mb: int = 25
    rate_limit_per_minute: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def gemini_keys_list(self) -> list[str]:
        keys: list[str] = []
        if self.gemini_api_keys:
            keys.extend([k.strip() for k in self.gemini_api_keys.split(",") if k.strip()])
        if self.gemini_api_key and self.gemini_api_key.strip() and self.gemini_api_key.strip() not in keys:
            keys.append(self.gemini_api_key.strip())
        return keys


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
