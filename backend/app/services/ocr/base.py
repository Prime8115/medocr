"""OCR provider interface."""
from abc import ABC, abstractmethod


class OCRError(Exception):
    """Raised when extraction cannot be performed (misconfig, API error, bad output).

    The API surfaces this as an explicit 'failed' document status. We never fall
    back to fabricated data — a pharmacy must never see an invented prescription.
    """


class OCRProvider(ABC):
    name: str = "base"

    @abstractmethod
    def classify(self, file_bytes: bytes, content_type: str) -> str:
        """Return 'prescription' or 'invoice'."""

    @abstractmethod
    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        """Return the type-specific `fields` dict for the given doc_type."""
