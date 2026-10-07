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
    # Fallback tried when the primary is overloaded or errors.
    #
    # gemini-2.5-flash sat here until it was RETIRED - Google answers 404 "no
    # longer available to new users" - so for some time the fallback did nothing
    # but add latency before failing. Pinned model versions go away; the live
    # check now calls this model nightly so the next retirement surfaces within
    # a day instead of being discovered by a pharmacist.
    ocr_fallback_model: Optional[str] = "gemini-3.5-flash"
    # 0 = the AI gives the same answer for the same page every time. Above it,
    # each reading varies - the same scan once came back without its Bill-to
    # GSTIN and once with it.
    ocr_temperature: float = 0.0
    # Check the parties' GSTINs against a second, local reading of the page
    # (services/ocr/party_check.py). Costs a few seconds of CPU on a scan.
    ocr_party_check_enabled: bool = True
    # Longest a single AI request may run. Without a limit, an overloaded model
    # held each request for minutes before failing: one MSV scan took 12m45s on
    # production with nothing logged. A timed-out request goes straight to the
    # fallback model rather than being retried on the one that hung.
    ocr_request_timeout_seconds: float = 90.0
    # After a timeout or an overload, a model is tried last for this long, so
    # the scans that follow start on the model that is working.
    ocr_model_rest_seconds: float = 600.0
    ocr_max_retries: int = 2          # attempts per model on transient errors (fast failover)
    ocr_base_backoff: float = 1.0     # seconds; doubles each retry
    # Requests per minute the app allows itself per key and model, so it paces
    # itself below Gemini's quota instead of being refused. The free tier allows
    # about 15 for Flash-Lite; 0 turns the limit off (a paid key, say).
    ocr_rpm_per_key: int = 10
    # How long a scan may wait in place for a free slot before going back to
    # the queue (which waits as long as Gemini said, without holding a worker).
    ocr_rate_wait_max_seconds: float = 20.0
    # When the day's Gemini quota is gone: "manual_entry" sends the scan for
    # completion by hand at once; "wait" keeps it queued until the quota resets
    # at midnight Pacific time.
    ocr_daily_quota_mode: str = "manual_entry"
    # When the AI is still busy after those retries, the scan is put back in the
    # queue and read again later instead of failing: free-tier per-minute limits
    # clear within a minute, so waiting is usually all it needs. Waits double
    # from the base (20s, 40s, 80s, 160s, 300s ~ 10 minutes in all), capped.
    ocr_busy_requeue_attempts: int = 5
    ocr_busy_requeue_base_delay: float = 20.0
    ocr_busy_requeue_max_delay: float = 300.0
    # Extraction work is kept in the ocr_jobs table and run by a background
    # worker, so a restart or deploy cannot lose a scan in flight. Tests turn
    # the worker off and run due jobs explicitly.
    ocr_worker_enabled: bool = True
    ocr_worker_poll_seconds: float = 5.0
    # The lease on a running job. Its holder renews it every poll; a lease left
    # unrenewed this long means the holder is dead, and the job is re-queued.
    ocr_job_lease_seconds: float = 90.0
    # A scan whose process dies this many times running is given up on, so a
    # file that crashes the server cannot be retried forever.
    ocr_job_max_attempts: int = 3

    # When the AI cannot read a document, read its text locally and send it for
    # manual entry instead of failing it (services/ocr/fallback.py).
    ocr_fallback_enabled: bool = True
    ocr_fallback_max_pages: int = 10
    # Read a SCANNED invoice's line-item table with Tesseract before spending an
    # AI call. Default OFF: on real scans it currently recovers the totals and
    # part of the table, but loses lines where OCR drops a header word and the
    # columns shift. The reconciliation gate catches that and falls back to the
    # AI, so nothing wrong is ever kept - but the OCR pass costs ~30s first, and
    # a pharmacist waiting at a counter should not pay that for a fallback.
    # Turn on to evaluate: OCR_TESSERACT_TABLES=true
    ocr_tesseract_tables: bool = False
    # Read every scan a second time with Tesseract and compare it with the
    # AI's reading, field by field (services/ocr/cross_read.py). A checker,
    # not a reader - on wherever Tesseract is installed.
    ocr_cross_read: bool = True
    # Ask the AI, from the page text, for fields a reading left blank - only
    # when one is printed but unread or essential, and only keeping answers
    # printed on the page word for word (services/ocr/gap_fill.py).
    ocr_gap_fill: bool = True

    tesseract_cmd: str = "tesseract"
    tesseract_lang: str = "eng"
    tesseract_timeout_seconds: float = 60.0

    # Database connection pool (Postgres). Requests and the scan workers share it.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: float = 30.0
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
    # Photos larger than this (longest side, pixels) are scaled down at upload:
    # sharper than any bill needs, and the AI and local OCR read them faster.
    upload_max_image_side: int = 3000
    # A PDF holding several separate invoices becomes one document per invoice.
    upload_split_invoices: bool = True
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
