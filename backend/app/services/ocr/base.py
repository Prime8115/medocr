"""OCR provider interface."""
from abc import ABC, abstractmethod


class OCRError(Exception):
    """Raised when extraction cannot be performed (misconfig, API error, bad output).

    The API surfaces this as an explicit 'failed' document status. We never fall
    back to fabricated data — a pharmacy must never see an invented prescription.

    `kind` says what went wrong, because the right response differs:
      "busy"     - the AI is overloaded or rate-limited; retrying later will help.
      "rejected" - the AI refused the request itself (bad key, bad model, schema
                   it will not serve); retrying the same request will not help.
      "output"   - the AI answered but the answer was unusable (truncated JSON);
                   splitting the document into smaller pieces may help.
      None       - anything else.
    """

    def __init__(self, message: str = "", kind: str | None = None,
                 retry_after: float | None = None, daily_quota: bool = False):
        super().__init__(message)
        self.kind = kind
        # For "busy": how long Gemini said to wait, and whether the day's quota
        # is what ran out - so the queue waits as long as needed, no longer.
        self.retry_after = retry_after
        self.daily_quota = daily_quota


class OCRProvider(ABC):
    name: str = "base"

    @abstractmethod
    def classify(self, file_bytes: bytes, content_type: str) -> str:
        """Return 'prescription' or 'invoice'."""

    @abstractmethod
    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        """Return the type-specific `fields` dict for the given doc_type."""
