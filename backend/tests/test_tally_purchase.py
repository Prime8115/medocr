"""Strict Tally purchase export must not guess pharmaceutical quantities or masters."""
from copy import deepcopy
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.models.connector import Connector
from app.models.document import Document
from app.models.push_delivery import PushDelivery
from app.models.tally_export import TallyExport
from app.models.user import User
from app.core.security import create_access_token
from app.services.connectors import tally_purchase
from tests.conftest import register_and_login


PAYLOAD = {
    "document_id": "doc_demo", "doc_type": "invoice", "meta": {"document_kind": "invoice"},
    "data": {
        "supplier": {"name": {"value": "A & B Pharma"}},
        "invoice": {"invoice_no": {"value": "M-41"}, "invoice_date": {"value": "12/09/2026"},
                    "total_amount": {"value": "112.00"}, "total_cgst_amount": {"value": "6.00"},
                    "total_sgst_amount": {"value": "6.00"}},
        "line_items": [{"description": {"value": "Capsule 15's"}, "quantity": {"value": "10"},
                        "free_quantity": {"value": "2"}, "rate": {"value": "10.00"},
                        "amount": {"value": "100.00"}, "batch_no": {"value": "B-10"},
                        "expiry": {"value": "12/2027"}}],
    },
}

CONFIG = {"format": "tally_prime_xml", "tally": {
    "company": "Pilot Pharmacy", "party_ledger": "A & B Pharma", "purchase_ledger": "Purchase",
    "unit": "Nos", "cgst_ledger": "Input CGST", "sgst_ledger": "Input SGST",
    "stock_items": {"Capsule 15's": "Capsule 15 Pack"},
    "batch_expiries": {"Capsule 15 Pack|B-10": "12/2027"},
}}


def test_tally_preflight_rejects_pack_text_in_numeric_quantity():
    payload = deepcopy(PAYLOAD)
    payload["data"]["line_items"][0]["quantity"]["value"] = "15's"
    result = tally_purchase.assess(payload, CONFIG)
    assert not result["ready"]
    assert any(issue["path"] == "line_items.0.quantity" for issue in result["issues"])
    with pytest.raises(ValueError):
        tally_purchase.render(payload, CONFIG)


def test_tally_preflight_requires_exact_batch_expiry_and_balance():
    missing = deepcopy(CONFIG)
    missing["tally"].pop("batch_expiries")
    assert "batch_expiry_mapping" in {i["code"] for i in tally_purchase.assess(PAYLOAD, missing)["issues"]}
    changed = deepcopy(PAYLOAD)
    changed["data"]["invoice"]["total_amount"]["value"] = "112.01"
    assert "reconciliation" in {i["code"] for i in tally_purchase.assess(changed, CONFIG)["issues"]}
    changed["meta"]["document_kind"] = "credit_note"
    assert "document_kind" in {i["code"] for i in tally_purchase.assess(changed, CONFIG)["issues"]}


def test_tally_xml_is_balanced_and_preserves_paid_free_and_batch():
    assert tally_purchase.assess(PAYLOAD, CONFIG)["ready"]
    root = ET.fromstring(tally_purchase.render(PAYLOAD, CONFIG))
    voucher = root.find(".//VOUCHER")
    assert voucher.findtext("DATE") == "20260912"
    assert voucher.findtext("PARTYLEDGERNAME") == "A & B Pharma"
    item = voucher.find("ALLINVENTORYENTRIES.LIST")
    assert item.findtext("ACTUALQTY") == "12 Nos"
    assert item.findtext("BILLEDQTY") == "10 Nos"
    assert item.findtext("BATCHALLOCATIONS.LIST/BATCHNAME") == "B-10"
    assert Decimal(voucher.findtext("ALLLEDGERENTRIES.LIST/AMOUNT")) == Decimal("112.00")
    assert sum(Decimal(e.findtext("AMOUNT")) for e in voucher.findall("ALLLEDGERENTRIES.LIST")) == Decimal("100.00")
    assert Decimal(item.findtext("ACCOUNTINGALLOCATIONS.LIST/AMOUNT")) == Decimal("-100.00")


def test_tally_endpoint_is_shop_scoped_and_approval_gated(client, db_session):
    owner = register_and_login(client)
    with db_session() as db:
        user = db.query(User).filter(User.email == "owner@shop.com").one()
        doc = Document(shop_id=user.shop_id, doc_type="invoice", status="needs_review",
                       payload={"fields": PAYLOAD["data"], "meta": PAYLOAD["meta"]})
        connector = Connector(shop_id=user.shop_id, type="file_export", name="Tally pilot", config=CONFIG)
        db.add_all([doc, connector])
        db.commit()
        doc_id, connector_id = doc.id, connector.id
    url = f"/v1/documents/{doc_id}/tally"
    params = {"connector_id": connector_id}
    assert client.get(url + "/preflight", params=params, headers=owner).json()["ready"]
    assert client.get(url + "/download", params=params, headers=owner).status_code == 409
    with db_session() as db:
        db.get(Document, doc_id).status = "approved"
        db.commit()
    download = client.get(url + "/download", params=params, headers=owner)
    assert download.status_code == 200, download.text
    assert download.headers["cache-control"] == "private, no-store"
    ET.fromstring(download.text)
    other = register_and_login(client, email="other@shop.com", shop="Other Shop")
    assert client.get(url + "/preflight", params=params, headers=other).status_code == 404
    assert client.get(url + "/export-status", params=params, headers=other).status_code == 404
    assert client.post(url + "/report", params=params,
                       json={"outcome": "not_imported", "reason": "Unused"}, headers=other).status_code == 404


def _approved_tally_invoice(client, db_session):
    owner = register_and_login(client)
    with db_session() as db:
        user = db.query(User).filter(User.email == "owner@shop.com").one()
        doc = Document(shop_id=user.shop_id, doc_type="invoice", status="approved",
                       payload={"fields": PAYLOAD["data"], "meta": PAYLOAD["meta"]})
        connector = Connector(shop_id=user.shop_id, type="file_export", name="Tally pilot", config=CONFIG)
        db.add_all([doc, connector])
        db.commit()
        return owner, doc.id, connector.id


def test_tally_download_is_pending_until_owner_reports_a_matching_receipt(client, db_session):
    owner, doc_id, connector_id = _approved_tally_invoice(client, db_session)
    url = f"/v1/documents/{doc_id}/tally"
    params = {"connector_id": connector_id}
    assert client.get(url + "/export-status", params=params, headers=owner).json() is None
    first = client.get(url + "/download", params=params, headers=owner)
    assert first.status_code == 200, first.text
    assert "_r1.xml" in first.headers["content-disposition"]
    status = client.get(url + "/export-status", params=params, headers=owner).json()
    assert status["status"] == "downloaded" and status["line_count"] == 1
    assert client.get(url + "/download", params=params, headers=owner).status_code == 409
    assert client.post(f"/v1/documents/{doc_id}/push", headers=owner).status_code == 400
    with db_session() as db:
        assert db.query(PushDelivery).count() == 0
        assert db.query(TallyExport).one().download_count == 1
        assert db.get(Document, doc_id).status == "approved"

    report = {"outcome": "imported", "external_reference": "PUR-18", "imported_line_count": 1,
              "imported_total": "112.00", "rows_and_batches_checked": True}
    assert client.post(url + "/report", params=params, json={**report, "imported_line_count": 2},
                       headers=owner).status_code == 409
    assert client.post(url + "/report", params=params, json={**report, "imported_total": "113.00"},
                       headers=owner).status_code == 409
    accepted = client.post(url + "/report", params=params, json=report, headers=owner)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "reported_imported"
    assert client.post(url + "/report", params=params, json=report, headers=owner).status_code == 200
    assert client.get(url + "/download", params=params, headers=owner).status_code == 409
    assert client.delete(f"/v1/connectors/{connector_id}", headers=owner).status_code == 409
    assert client.patch(f"/v1/connectors/{connector_id}", json={"config": {"format": "json"}},
                        headers=owner).status_code == 409

    with db_session() as db:
        shop_id = db.query(User).filter(User.email == "owner@shop.com").one().shop_id
        staff = User(shop_id=shop_id, email="staff@shop.com", hashed_password="unused", role="staff")
        db.add(staff)
        db.commit()
        staff_headers = {"Authorization": f"Bearer {create_access_token(staff.id, shop_id, staff.role)}"}
    assert client.get(url + "/export-status", params=params, headers=staff_headers).status_code == 200
    assert client.post(url + "/report", params=params, json=report, headers=staff_headers).status_code == 403


def test_tally_not_imported_requires_reason_and_allows_one_reviewed_retry(client, db_session):
    owner, doc_id, connector_id = _approved_tally_invoice(client, db_session)
    url = f"/v1/documents/{doc_id}/tally"
    params = {"connector_id": connector_id}
    assert client.get(url + "/download", params=params, headers=owner).status_code == 200
    assert client.post(url + "/report", params=params, json={"outcome": "not_imported"},
                       headers=owner).status_code == 422
    recorded = client.post(url + "/report", params=params,
                           json={"outcome": "not_imported", "reason": "File was not used"}, headers=owner)
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["status"] == "not_imported"
    second = client.get(url + "/download", params=params, headers=owner)
    assert second.status_code == 200, second.text
    assert "_r2.xml" in second.headers["content-disposition"]
    assert client.get(url + "/export-status", params=params, headers=owner).json()["download_count"] == 2
    with db_session() as db:
        db.get(Connector, connector_id).enabled = False
        db.commit()
    assert client.get(url + "/export-status", params=params, headers=owner).status_code == 200
    assert client.get(url + "/download", params=params, headers=owner).status_code == 404
    assert client.post(url + "/report", params=params,
                       json={"outcome": "not_imported", "reason": "Import exceptions remain"},
                       headers=owner).status_code == 200


def test_general_push_still_uses_other_connectors_without_claiming_tally_delivery(client, db_session, tmp_path):
    owner, doc_id, tally_id = _approved_tally_invoice(client, db_session)
    with db_session() as db:
        user = db.query(User).filter(User.email == "owner@shop.com").one()
        db.add(Connector(shop_id=user.shop_id, type="file_export", name="JSON archive",
                         config={"formats": ["json"], "output_dir": str(tmp_path)}))
        db.commit()
    pushed = client.post(f"/v1/documents/{doc_id}/push", headers=owner)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["status"] == "pushed"
    assert len(pushed.json()["deliveries"]) == 1
    with db_session() as db:
        deliveries = db.query(PushDelivery).all()
        assert len(deliveries) == 1
        assert deliveries[0].connector_id != tally_id
        assert db.query(TallyExport).count() == 0
