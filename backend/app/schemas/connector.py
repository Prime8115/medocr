from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ConnectorCreate(BaseModel):
    type: str  # webhook | file_export | desktop_agent
    name: str = Field(min_length=1)
    config: dict = {}
    secret: Optional[str] = None  # webhook HMAC secret; stored server-side
    enabled: bool = True


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    secret: Optional[str] = None
    enabled: Optional[bool] = None


class ConnectorOut(BaseModel):
    id: str
    type: str
    name: str
    config: dict = {}
    enabled: bool
    has_secret: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class DeliveryOut(BaseModel):
    id: str
    document_id: str
    connector_id: str
    status: str
    response_code: Optional[int] = None
    response_body: Optional[str] = None
    attempts: int
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Desktop agent ---
class AgentPairRequest(BaseModel):
    code: str


class AgentToken(BaseModel):
    agent_token: str
    connector_id: str


class AgentDelivery(BaseModel):
    id: str
    document_id: str
    payload: Any
