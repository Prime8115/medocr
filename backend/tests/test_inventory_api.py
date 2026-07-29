"""Inventory API: CSV import, list, count, document reconciliation, tenancy."""
import io

from tests.conftest import register_and_login

CSV = (
    "Item Name,Code,MRP,Stock\n"
    "Paracetamol 500mg Tablet,PCM500,2.50,100\n"
    "Amoxicillin 250mg Capsule,AMOX250,5.00,50\n"
    "Cetirizine 10mg Tablet,CET10,1.20,30\n"
)


def _import(client, headers, text=CSV):
    files = {"file": ("stock.csv", text.encode(), "text/csv")}
    return client.post("/v1/inventory/import", files=files, headers=headers)


def test_import_and_count(client):
    headers = register_and_login(client)
    r = _import(client, headers)
    assert r.status_code == 200
    assert r.json()["imported"] == 3

    count = client.get("/v1/inventory/count", headers=headers).json()
    assert count["count"] == 3
    assert count["connected"] is True


def test_count_zero_when_no_inventory(client):
    headers = register_and_login(client)
    count = client.get("/v1/inventory/count", headers=headers).json()
    assert count["connected"] is False


def test_import_rejects_missing_name_column(client):
    headers = register_and_login(client)
    r = _import(client, headers, text="Foo,Bar\n1,2\n")
    assert r.status_code == 400


def test_match_single_name(client):
    headers = register_and_login(client)
    _import(client, headers)
    r = client.post("/v1/inventory/match", json={"name": "Paracetamol 500"}, headers=headers)
    cands = r.json()["candidates"]
    assert cands and cands[0]["name"].startswith("Paracetamol")
    assert cands[0]["score"] >= 80


def test_document_reconciliation(client, db_session):
    from app.models.document import Document

    headers = register_and_login(client)
    _import(client, headers)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    s = db_session()
    try:
        doc = Document(
            shop_id=shop_id, doc_type="prescription", status="needs_review",
            payload={"doc_type": "prescription", "fields": {"medications": [
                {"name": {"value": "Paracetamol 500 mg"}},
                {"name": {"value": "Cetirizine 10mg"}},
            ]}},
        )
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()

    r = client.get(f"/v1/inventory/documents/{doc_id}/match", headers=headers)
    body = r.json()
    assert body["connected"] is True
    assert body["total"] == 2
    assert body["matched"] == 2
    assert body["items"][0]["candidates"][0]["stock_qty"] == 100


def test_reconciliation_without_inventory_reports_not_connected(client, db_session):
    from app.models.document import Document

    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="prescription", status="needs_review", payload={"fields": {}})
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()
    body = client.get(f"/v1/inventory/documents/{doc_id}/match", headers=headers).json()
    assert body["connected"] is False


def test_inventory_is_shop_scoped(client):
    h_a = register_and_login(client, email="a@shop.com", shop="A")
    h_b = register_and_login(client, email="b@shop.com", shop="B")
    _import(client, h_a)
    # Shop B sees none of shop A's items.
    assert client.get("/v1/inventory/count", headers=h_b).json()["count"] == 0
    assert client.get("/v1/inventory/", headers=h_b).json() == []
