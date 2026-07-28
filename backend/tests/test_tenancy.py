"""Tenancy isolation: one shop must never see another shop's documents."""
from app.models.document import Document
from tests.conftest import register_and_login


def _seed_document(TestingSession, shop_id, doc_type="prescription"):
    db = TestingSession()
    try:
        doc = Document(shop_id=shop_id, doc_type=doc_type, status="needs_review")
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id
    finally:
        db.close()


def _shop_id_for(client, headers):
    return client.get("/v1/auth/me", headers=headers).json()["shop_id"]


def test_shop_cannot_see_other_shops_documents(client, db_session):
    headers_a = register_and_login(client, email="a@shop.com", shop="Shop A")
    headers_b = register_and_login(client, email="b@shop.com", shop="Shop B")

    shop_a = _shop_id_for(client, headers_a)
    shop_b = _shop_id_for(client, headers_b)

    doc_a = _seed_document(db_session, shop_a)
    doc_b = _seed_document(db_session, shop_b)

    # Shop A lists only its own document.
    list_a = client.get("/v1/documents/", headers=headers_a).json()
    ids_a = {d["id"] for d in list_a}
    assert doc_a in ids_a
    assert doc_b not in ids_a

    # Shop A cannot fetch Shop B's document by id.
    assert client.get(f"/v1/documents/{doc_b}", headers=headers_a).status_code == 404
    # But Shop B can.
    assert client.get(f"/v1/documents/{doc_b}", headers=headers_b).status_code == 200


def test_cannot_approve_other_shops_document(client, db_session):
    headers_a = register_and_login(client, email="a2@shop.com", shop="Shop A")
    headers_b = register_and_login(client, email="b2@shop.com", shop="Shop B")
    shop_b = _shop_id_for(client, headers_b)
    doc_b = _seed_document(db_session, shop_b)

    assert client.post(f"/v1/documents/{doc_b}/approve", headers=headers_a).status_code == 404
