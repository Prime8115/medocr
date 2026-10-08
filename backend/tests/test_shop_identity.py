"""The shop's own GSTINs: stated by the owner, or learned from approved bills."""
from app.services.shop_identity import own_gstins
from tests.conftest import register_and_login

EASTERN = "27AAECD7847H1ZC"
ASCENT = "27AASCA3306L1ZE"


def _shop_id(client, headers):
    return client.get("/v1/auth/me", headers=headers).json()["shop_id"]


def test_the_owner_states_the_shops_gstins(client, db_session):
    headers = register_and_login(client)
    r = client.put("/v1/auth/shop/gstins", json={"gstins": [EASTERN.lower()]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["gstins"] == [EASTERN]
    assert client.get("/v1/auth/shop", headers=headers).json()["gstins"] == [EASTERN]


def test_an_invalid_gstin_is_refused(client):
    headers = register_and_login(client)
    r = client.put("/v1/auth/shop/gstins", json={"gstins": ["27AADCM6990J1ZM"]}, headers=headers)
    assert r.status_code == 422


def test_a_gstin_is_learned_from_bills_the_shop_approved(client, db_session):
    from app.models.document import Document

    headers = register_and_login(client)
    shop_id = _shop_id(client, headers)
    s = db_session()
    try:
        bill = {"fields": {"bill_to": {"gstin": {"value": ASCENT, "confidence": 1.0}}}}
        s.add(Document(shop_id=shop_id, doc_type="invoice", status="approved", payload=bill))
        # An unapproved bill teaches nothing: no person confirmed its buyer.
        other = {"fields": {"bill_to": {"gstin": {"value": EASTERN, "confidence": 1.0}}}}
        s.add(Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=other))
        s.commit()
        assert own_gstins(s, shop_id) == [ASCENT]
        # Another shop learns nothing from this one's bills.
        assert own_gstins(s, "another-shop") == []
    finally:
        s.close()
