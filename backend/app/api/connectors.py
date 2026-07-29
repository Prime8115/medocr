"""Connector configuration API (owner-managed) + test round-trip + delivery logs."""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_owner
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.connector import Connector
from app.models.push_delivery import PushDelivery
from app.models.user import User
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorOut,
    ConnectorUpdate,
    DeliveryOut,
)
from app.services.connectors.registry import CONNECTOR_TYPES, build_connector
from app.services.connectors import mapping

router = APIRouter()


@router.get("/options")
def connector_options(user: User = Depends(get_current_user)):
    """Available connector types, export formats, and mapping profiles for the UI."""
    return {
        "types": list(CONNECTOR_TYPES),
        "formats": mapping.AVAILABLE_FORMATS,
        "profiles": mapping.AVAILABLE_PROFILES,
    }


def _to_out(model: Connector) -> ConnectorOut:
    return ConnectorOut(
        id=model.id,
        type=model.type,
        name=model.name,
        config=model.config or {},
        enabled=model.enabled,
        has_secret=bool(model.secret_ref),
        created_at=model.created_at,
    )


def _get_owned(connector_id: str, db: Session, user: User) -> Connector:
    c = (
        db.query(Connector)
        .filter(Connector.id == connector_id, Connector.shop_id == user.shop_id)
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Connector not found")
    return c


@router.post("/", response_model=ConnectorOut, status_code=201)
def create_connector(body: ConnectorCreate, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    if body.type not in CONNECTOR_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {CONNECTOR_TYPES}")

    config = dict(body.config or {})
    # Desktop agents get a one-time pairing code.
    if body.type == "desktop_agent":
        config.setdefault("pairing_code", secrets.token_hex(4).upper())
        config.setdefault("paired", False)

    connector = Connector(
        shop_id=user.shop_id,
        type=body.type,
        name=body.name,
        config=config,
        secret_ref=body.secret,
        enabled=body.enabled,
    )
    db.add(connector)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="connector.created", target=connector.id))
    db.commit()
    db.refresh(connector)
    return _to_out(connector)


@router.get("/", response_model=list[ConnectorOut])
def list_connectors(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Connector).filter(Connector.shop_id == user.shop_id).all()
    return [_to_out(r) for r in rows]


@router.get("/{connector_id}", response_model=ConnectorOut)
def get_connector(connector_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _to_out(_get_owned(connector_id, db, user))


@router.patch("/{connector_id}", response_model=ConnectorOut)
def update_connector(
    connector_id: str, body: ConnectorUpdate, db: Session = Depends(get_db), user: User = Depends(require_owner)
):
    c = _get_owned(connector_id, db, user)
    if body.name is not None:
        c.name = body.name
    if body.config is not None:
        c.config = body.config
    if body.secret is not None:
        c.secret_ref = body.secret
    if body.enabled is not None:
        c.enabled = body.enabled
    db.commit()
    db.refresh(c)
    return _to_out(c)


@router.delete("/{connector_id}", status_code=204)
def delete_connector(connector_id: str, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    c = _get_owned(connector_id, db, user)
    db.delete(c)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="connector.deleted", target=connector_id))
    db.commit()
    return None


@router.post("/{connector_id}/test")
def test_connector(connector_id: str, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    """Run a real test round-trip and return the result."""
    c = _get_owned(connector_id, db, user)
    impl = build_connector(c)
    result = impl.test()
    return {
        "status": result.status,
        "ok": result.ok,
        "response_code": result.response_code,
        "response_body": result.response_body,
        "attempts": result.attempts,
    }


@router.get("/{connector_id}/deliveries", response_model=list[DeliveryOut])
def connector_deliveries(
    connector_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _get_owned(connector_id, db, user)  # tenancy check
    return (
        db.query(PushDelivery)
        .filter(PushDelivery.connector_id == connector_id)
        .order_by(PushDelivery.created_at.desc())
        .limit(100)
        .all()
    )
