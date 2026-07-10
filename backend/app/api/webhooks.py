from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter()

class WebhookConfig(BaseModel):
    url: str
    events: List[str]

@router.post("/")
async def register_webhook(config: WebhookConfig):
    """
    Register webhook endpoints & event subscriptions.
    """
    # For MVP, just return success
    return {"status": "success", "message": "Webhook registered", "config": config.dict()}

@router.get("/")
async def list_webhooks():
    """
    List registered webhooks.
    """
    return []
