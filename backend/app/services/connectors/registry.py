"""Build a live connector instance from a stored Connector model row."""
from app.models.connector import Connector
from app.services.connectors.agent import DesktopAgentConnector
from app.services.connectors.base import BaseConnector
from app.services.connectors.file_export import FileExportConnector
from app.services.connectors.webhook import WebhookConnector

CONNECTOR_TYPES = ("webhook", "file_export", "desktop_agent")


def build_connector(model: Connector, **overrides) -> BaseConnector:
    """Instantiate the connector implementation for a model row.

    `overrides` lets tests inject fakes (e.g. http_post, sleep) into WebhookConnector.
    """
    config = model.config or {}
    if model.type == "webhook":
        return WebhookConnector(config=config, secret=model.secret_ref, **overrides)
    if model.type == "file_export":
        return FileExportConnector(config=config)
    if model.type == "desktop_agent":
        return DesktopAgentConnector(config=config)
    raise ValueError(f"Unknown connector type: {model.type!r}")
