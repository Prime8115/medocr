"""Desktop-agent connector: enqueue the payload for a paired Windows agent.

Delivery is asynchronous. `deliver` returns PENDING; the push service persists a
PushDelivery row that the agent later collects via the agent API and acks.
"""
from app.services.connectors.base import PENDING, BaseConnector, DeliveryResult


class DesktopAgentConnector(BaseConnector):
    type = "desktop_agent"

    def __init__(self, config: dict):
        self.config = config or {}

    def deliver(self, payload: dict) -> DeliveryResult:
        # No synchronous transport: the paired agent polls for pending deliveries.
        return DeliveryResult(status=PENDING, response_body="Queued for desktop agent.")

    def test(self) -> DeliveryResult:
        paired = bool((self.config or {}).get("paired"))
        if not paired:
            return DeliveryResult(status="failed", response_body="Agent not paired yet.")
        return DeliveryResult(status=PENDING, response_body="Agent paired; test queued.")
