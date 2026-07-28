"""Deterministic mock provider — TEST/DEMO ONLY.

Every value is suffixed '(MOCK)' and the pipeline is labelled 'mock' so mock
output can never be mistaken for a real extraction. Only reachable when
settings.allow_mock_ocr is true.
"""
from app.services.ocr.base import OCRProvider


def _f(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


class MockProvider(OCRProvider):
    name = "mock"

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        # Heuristic for tests: callers can bias via a magic marker in the bytes.
        if b"INVOICE" in file_bytes[:64].upper():
            return "invoice"
        return "prescription"

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        if doc_type == "invoice":
            return {
                "supplier": {
                    "name": _f("MediSupply Distributors (MOCK)"),
                    "gstin": _f("29ABCDE1234F1Z5 (MOCK)"),
                    "address": _f("Bengaluru (MOCK)", 0.5),
                },
                "invoice": {
                    "invoice_no": _f("INV-2026-001 (MOCK)"),
                    "invoice_date": _f("27/07/2026"),
                    "total_amount": _f("1250.00"),
                },
                "line_items": [
                    {
                        "description": _f("Paracetamol 500mg (MOCK)"),
                        "batch_no": _f("B12345"),
                        "expiry": _f("12/2027"),
                        "quantity": _f("100"),
                        "mrp": _f("2.50"),
                        "rate": _f("1.80"),
                        "amount": _f("180.00"),
                        "hsn": _f("3004"),
                        "gst_percent": _f("12"),
                    }
                ],
            }
        return {
            "patient": {
                "name": _f("Ramesh Kumar (MOCK)"),
                "age": _f("45"),
                "gender": _f("M"),
            },
            "prescriber": {
                "name": _f("Dr. Sharma (MOCK)"),
                "registration_no": _f("MCI-12345", 0.4),
            },
            "medications": [
                {
                    "name": _f("Paracetamol (MOCK)"),
                    "strength": _f("500 mg"),
                    "form": _f("tablet"),
                    "frequency": _f("1-0-1"),
                    "duration": _f("3 days"),
                    "instructions": _f("after food", 0.5),
                }
            ],
        }
