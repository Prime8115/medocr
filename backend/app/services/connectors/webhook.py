"""Webhook connector: signed HTTP POST with retries and exponential backoff."""
import json
import time
from typing import Callable, Optional

import requests

from app.services.connectors.base import FAILED, SUCCESS, BaseConnector, DeliveryResult
from app.services.connectors.signing import (
    PAYLOAD_VERSION_HEADER,
    SIGNATURE_HEADER,
    sign,
)


class WebhookConnector(BaseConnector):
    type = "webhook"

    def __init__(
        self,
        config: dict,
        secret: Optional[str] = None,
        http_post: Optional[Callable] = None,
        sleep: Optional[Callable] = None,
        max_attempts: int = 3,
        base_backoff: float = 0.5,
        timeout: float = 10.0,
    ):
        self.url = (config or {}).get("url")
        self.secret = secret
        self.http_post = http_post or requests.post
        self.sleep = sleep or time.sleep
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.timeout = timeout

    def deliver(self, payload: dict) -> DeliveryResult:
        if not self.url:
            return DeliveryResult(status=FAILED, response_body="Webhook URL not configured.")

        body = json.dumps(payload, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            PAYLOAD_VERSION_HEADER: str(payload.get("payload_version", "1.0")),
        }
        if self.secret:
            headers[SIGNATURE_HEADER] = sign(self.secret, body)

        last_code: Optional[int] = None
        last_body: Optional[str] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.http_post(self.url, data=body, headers=headers, timeout=self.timeout)
                last_code = getattr(resp, "status_code", None)
                last_body = (getattr(resp, "text", "") or "")[:2000]
                if last_code is not None and 200 <= last_code < 300:
                    return DeliveryResult(
                        status=SUCCESS, response_code=last_code, response_body=last_body, attempts=attempt
                    )
            except requests.RequestException as exc:
                last_body = str(exc)

            if attempt < self.max_attempts:
                self.sleep(self.base_backoff * (2 ** (attempt - 1)))

        return DeliveryResult(
            status=FAILED, response_code=last_code, response_body=last_body, attempts=self.max_attempts
        )
