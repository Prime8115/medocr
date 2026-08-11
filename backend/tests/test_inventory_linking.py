"""Approving a document links its line items to inventory SKUs (for stock update)."""
from app.services.inventory.matching import enrich_payload_with_matches
from tests.conftest import register_and_login


class FakeItem:
    def __init__(self, id, name, sku, composition=None):
        self.id = id
        self.name = name
        self.sku = sku
        self.composition = composition
        from app.services.inventory.matching import normalize
        self.normalized_name = normalize(name)
        self.strength = None
        self.mrp = None
        self.stock_qty = 0


def test_enrich_attaches_sku_to_matched_items():
    payload = {
        "doc_type": "invoice",
        "fields": {"line_items": [
            {"description": {"value": "Paracetamol 500"}},
            {"description": {"value": "ZZZ Unknown Drug"}},
        ]},
    }
    items = [FakeItem("i1", "Paracetamol 500mg Tablet", "PCM500")]
    linked = enrich_payload_with_matches(payload, items)
    assert linked == 1
    li = payload["fields"]["line_items"]
    assert li[0]["inventory_match"]["sku"] == "PCM500"
    assert li[0]["inventory_match"]["score"] >= 70
    assert "inventory_match" not in li[1]  # unmatched item not linked


def test_approve_links_inventory_and_push_carries_sku(client, mock_ocr, db_session):
    from app.models.connector import Connector
    from app.models.inventory import InventoryItem
    from app.services.inventory.matching import normalize

    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    # Seed inventory that matches the mock invoice item ("Paracetamol 500mg (MOCK)").
    s = db_session()
    try:
        s.add(InventoryItem(shop_id=shop_id, name="Paracetamol 500mg (MOCK)",
                            normalized_name=normalize("Paracetamol 500mg (MOCK)"), sku="PCM-500", stock_qty=100))
        s.add(Connector(shop_id=shop_id, type="file_export", name="exp", config={"format": "json"}, enabled=True))
        s.commit()
    finally:
        s.close()

    # Upload an invoice (mock), approve, and confirm the matched SKU is attached.
    files = {"file": ("inv.jpg", b"INVOICE data", "image/jpeg")}
    doc_id = client.post("/v1/documents/", files=files, data={"doc_type": "invoice"}, headers=headers).json()["document_id"]
    assert client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"] == "needs_review"

    approved = client.post(f"/v1/documents/{doc_id}/approve", headers=headers).json()
    li = approved["payload"]["fields"]["line_items"]
    assert li[0].get("inventory_match", {}).get("sku") == "PCM-500"
    assert approved["payload"]["meta"]["inventory_linked"] == 1

    # Push carries the linked SKU in the payload data.
    push = client.post(f"/v1/documents/{doc_id}/push", headers=headers).json()
    assert push["status"] == "pushed"
