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


def _idempotency_key(document_id: str, connector_id: str) -> str:
    return f"{document_id}:{connector_id}"


def push_document(db: Session, document: Document, connector_overrides: Optional[dict] = None) -> List[PushDelivery]:
    """Deliver the document to every enabled connector for its shop.

    Idempotent: a connector already delivered (success) or queued (pending) for
    this document is skipped. Previously failed deliveries are retried.
    """
    payload = build_push_payload(document)
    connectors = (
        db.query(Connector)
        .filter(Connector.shop_id == document.shop_id, Connector.enabled.is_(True))
        .all()
    )

    deliveries: List[PushDelivery] = []
    for model in connectors:
        key = _idempotency_key(document.id, model.id)
        existing = (
            db.query(PushDelivery)
            .filter(PushDelivery.idempotency_key == key)
            .order_by(PushDelivery.created_at.desc())
            .first()
        )
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
    """True if every delivery succeeded or is queued (pending)."""
    return bool(deliveries) and all(d.status in (SUCCESS, PENDING) for d in deliveries)
