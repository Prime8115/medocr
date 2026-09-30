"""The normalized, versioned push payload — the stable contract for integrators.

This shape is documented in docs/INTEGRATION.md and must not break within a
major version. Additive fields are fine; removals/renames require a version bump.
"""
from typing import Optional

from app.models.document import Document

PUSH_PAYLOAD_VERSION = "1.0"


def build_push_payload(document: Document, config: Optional[dict] = None) -> dict:
    """The payload delivered to a connector.

    `data` is the full nested extraction and never changes shape - that is the
    contract integrators already build against.

    A connector may additionally set a `profile` (or its own `columns`), and the
    same information then also arrives as `rows`: one flat object per line item,
    keyed by that profile's column names, with the invoice-level fields repeated
    on each. An API consumer then reads exactly the field names it asked for
    rather than walking the nested structure - the same data the CSV export
    produces, over HTTP.
    """
    payload = document.payload or {}
    out = {
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

    if config and (config.get("profile") or config.get("columns")):
        # Imported here to keep the payload contract free of a mapping import
        # cycle; mapping already depends on nothing in this module.
        from app.services.connectors.mapping import flatten_rows, resolve_columns

        columns = resolve_columns(config, out["doc_type"])
        if columns:
            flat = flatten_rows(out)
            out["rows"] = [
                {c["header"]: row.get(c["field"], "") for c in columns} for row in flat
            ]
            out["row_columns"] = [c["header"] for c in columns]
    return out
