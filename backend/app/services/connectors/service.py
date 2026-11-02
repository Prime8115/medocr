"""Push orchestration: deliver an approved document to all enabled connectors,
recording each attempt and enforcing idempotency (never double-post).
"""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.connector import Connector
from app.models.document import Document
from app.models.push_delivery import PushDelivery
from app.services.connectors.base import PENDING, SUCCESS
from app.services.connectors.payload import build_push_payload
from app.services.connectors.registry import build_connector


class DeliveredVersionChanged(Exception):
    """An earlier export cannot stand in for newly approved document data."""

    def __init__(self, connector_id: str):
        self.connector_id = connector_id
        super().__init__("Document changed after an earlier delivery was sent or queued.")


def _idempotency_key(document_id: str, connector_id: str) -> str:
    return f"{document_id}:{connector_id}"


def _same_exported_version(previous: dict | None, current: dict) -> bool:
    if not isinstance(previous, dict):
        return False
    # Review notes and acknowledgement timestamps may change without changing
    # the purchase entry. Compare exactly the content a receiver would import.
    keys = ("doc_type", "schema_version", "data", "rows", "row_columns")
    if any(previous.get(key) != current.get(key) for key in keys):
        return False
    return ((previous.get("meta") or {}).get("document_kind") ==
            (current.get("meta") or {}).get("document_kind"))


def is_manual_tally(model: Connector) -> bool:
    """A generated XML file is not a delivery accepted by Tally."""
    return model.type == "file_export" and (model.config or {}).get("format") == "tally_prime_xml"


def push_document(db: Session, document: Document, connector_overrides: Optional[dict] = None) -> List[PushDelivery]:
    """Deliver the document to every enabled connector for its shop.

    Idempotent for unchanged exported data: a connector already delivered
    (success) or queued (pending) is skipped. If the approved data changes,
    block all connectors before sending anything. Previously failed deliveries
    are retried.
    """
    connectors = (
        db.query(Connector)
        .filter(Connector.shop_id == document.shop_id, Connector.enabled.is_(True))
        .all()
    )

    # Preflight every connector before sending to any one of them. Otherwise a
    # changed invoice could be posted to one destination before another reports
    # that its old delivery is stale.
    prepared = []
    for model in connectors:
        if is_manual_tally(model):
            continue
        # Built per connector: each may ask for its own column profile, and the
        # payload then also carries the flat rows under those names.
        payload = build_push_payload(document, model.config or {})
        key = _idempotency_key(document.id, model.id)
        existing = (
            db.query(PushDelivery)
            .filter(PushDelivery.idempotency_key == key)
            .order_by(PushDelivery.created_at.desc())
            .first()
        )
        if existing and existing.status in (SUCCESS, PENDING):
            if not _same_exported_version(existing.request_payload, payload):
                raise DeliveredVersionChanged(model.id)
        prepared.append((model, payload, key, existing))

    deliveries: List[PushDelivery] = []
    for model, payload, key, existing in prepared:
        # Skip if already delivered or queued (idempotency).
        if existing and existing.status in (SUCCESS, PENDING):
            deliveries.append(existing)
            continue

        overrides = (connector_overrides or {}).get(model.type, {})
        impl = build_connector(model, **overrides)
        result = impl.deliver(payload)

        if existing and existing.status not in (SUCCESS, PENDING):
            # Retry a previously failed delivery in place.
            existing.status = result.status
            existing.delivery_state = result.delivery_state or ("failed" if result.status == "failed" else None)
            existing.external_reference = result.external_reference
            existing.response_code = result.response_code
            existing.response_body = result.response_body
            existing.attempts = (existing.attempts or 0) + result.attempts
            existing.request_payload = payload
            deliveries.append(existing)
        else:
            delivery = PushDelivery(
                document_id=document.id,
                connector_id=model.id,
                status=result.status,
                delivery_state=result.delivery_state or ("failed" if result.status == "failed" else None),
                external_reference=result.external_reference,
                request_payload=payload,
                response_body=result.response_body,
                response_code=result.response_code,
                attempts=result.attempts,
                idempotency_key=key,
            )
            db.add(delivery)
            deliveries.append(delivery)

    db.commit()
    for d in deliveries:
        db.refresh(d)
    return deliveries


def all_ok(deliveries: List[PushDelivery]) -> bool:
    """True only when every connector has confirmed delivery, not merely queued it."""
    return bool(deliveries) and all(d.status == SUCCESS for d in deliveries)
