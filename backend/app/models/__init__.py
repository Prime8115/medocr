"""Import all models so SQLAlchemy's metadata is fully populated
(needed for create_all and Alembic autogenerate)."""
from app.models.shop import Shop
from app.models.user import User
from app.models.document import Document
from app.models.connector import Connector
from app.models.push_delivery import PushDelivery
from app.models.audit_log import AuditLog
from app.models.inventory import InventoryItem

__all__ = [
    "Shop", "User", "Document", "Connector", "PushDelivery", "AuditLog", "InventoryItem",
]
