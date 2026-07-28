"""Connector interface and delivery result."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# Delivery statuses
SUCCESS = "success"
FAILED = "failed"
PENDING = "pending"  # accepted into a queue (desktop agent) but not yet confirmed


@dataclass
class DeliveryResult:
    status: str
    response_code: Optional[int] = None
    response_body: Optional[str] = None
    attempts: int = 1
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (SUCCESS, PENDING)


class BaseConnector(ABC):
    type: str = "base"

    @abstractmethod
    def deliver(self, payload: dict) -> DeliveryResult:
        """Deliver an approved-document payload to the external target."""

    def test(self) -> DeliveryResult:
        """Send a synthetic payload to verify configuration. Overridable."""
        return self.deliver({"payload_version": "1.0", "event": "test.ping", "data": {}})
