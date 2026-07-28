"""The normalized, versioned push payload — the stable contract for integrators.

This shape is documented in docs/INTEGRATION.md and must not break within a
major version. Additive fields are fine; removals/renames require a version bump.
"""
from app.models.document import Document

PUSH_PAYLOAD_VERSION = "1.0"


def build_push_payload(document: Document) -> dict:
    payload = document.payload or {}
    return {
        "payload_version": PUSH_PAYLOAD_VERSION,
        "event": "document.approved",
        "document_id": document.id,
        "shop_id": document.shop_id,
        "doc_type": document.doc_type,
        "overall_confidence": document.overall_confidence,
        "schema_version": payload.get("schema_version"),
        "data": payload.get("fields", {}),
        "meta": payload.get("meta", {}),
    }
