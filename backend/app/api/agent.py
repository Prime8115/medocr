"""Desktop-agent facing API: pair, poll pending deliveries, acknowledge."""
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import create_agent_token, decode_access_token
from app.database import get_db
from app.models.connector import Connector
from app.models.push_delivery import PushDelivery
from app.schemas.connector import AgentDelivery, AgentPairRequest, AgentToken
from app.services.connectors.base import SUCCESS

router = APIRouter()

_agent_scheme = OAuth2PasswordBearer(tokenUrl="/v1/agent/pair", auto_error=True)
_CRED_EXC = HTTPException(status_code=401, detail="Invalid agent token")


def get_agent_connector(token: str = Depends(_agent_scheme), db: Session = Depends(get_db)) -> Connector:
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise _CRED_EXC
    if payload.get("type") != "agent":
        raise _CRED_EXC
    connector = db.get(Connector, payload.get("sub"))
    if not connector or connector.type != "desktop_agent":
        raise _CRED_EXC
    return connector


@router.post("/pair", response_model=AgentToken)
def pair_agent(body: AgentPairRequest, db: Session = Depends(get_db)):
    """Exchange a one-time pairing code for a long-lived agent token."""
    code = body.code.strip().upper()
    # Find an unpaired desktop_agent connector with this code.
    candidates = db.query(Connector).filter(Connector.type == "desktop_agent").all()
    connector = next(
        (c for c in candidates if (c.config or {}).get("pairing_code", "").upper() == code and not (c.config or {}).get("paired")),
        None,
    )
    if not connector:
        raise HTTPException(status_code=400, detail="Invalid or already-used pairing code.")

    # Mark paired and invalidate the code.
    config = dict(connector.config or {})
    config["paired"] = True
    config.pop("pairing_code", None)
    connector.config = config
    db.commit()

    token = create_agent_token(connector.id, connector.shop_id)
    return AgentToken(agent_token=token, connector_id=connector.id)


@router.get("/deliveries", response_model=list[AgentDelivery])
def poll_deliveries(connector: Connector = Depends(get_agent_connector), db: Session = Depends(get_db)):
    """Pending deliveries queued for this agent."""
    rows = (
        db.query(PushDelivery)
        .filter(PushDelivery.connector_id == connector.id, PushDelivery.status == "pending")
        .order_by(PushDelivery.created_at.asc())
        .limit(50)
        .all()
    )
    return [AgentDelivery(id=r.id, document_id=r.document_id, payload=r.request_payload) for r in rows]


@router.post("/deliveries/{delivery_id}/ack")
def ack_delivery(
    delivery_id: str, connector: Connector = Depends(get_agent_connector), db: Session = Depends(get_db)
):
    """Agent confirms it wrote the data into the local software."""
    delivery = db.get(PushDelivery, delivery_id)
    if not delivery or delivery.connector_id != connector.id:
        raise HTTPException(status_code=404, detail="Delivery not found")
    delivery.status = SUCCESS
    db.commit()
    return {"status": "ok", "delivery_id": delivery_id}
