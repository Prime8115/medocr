"""Recheck one field: the AI is asked about one value, and its answer is
checked against the bill before it is offered (services/recheck.py)."""
import copy

import pytest

from app.services import recheck
from app.services.storage import storage
from tests.conftest import register_and_login
from tests.test_self_learning import _bill, _read, _row


class _AI:
    """Answers every question with `answer`, keeping what it was asked."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def review_json(self, prompt, file_bytes, content_type):
        self.asked.append((prompt, content_type))
        return self.answer


def _fields():
    return _read(_bill(1))["fields"]


def test_the_question_names_the_field_and_points_out_the_line():
    fields = _fields()
    prompt, current, kind = recheck.question(fields, "line_items[2].ptr")
    assert "PTR (price to retailer)" in prompt and "item 3 of 4" in prompt
    assert "MEDICINE 102 TAB" in prompt and "BN1002" in prompt
    assert (current, kind) == ("", "money")
    prompt, current, _ = recheck.question(fields, "invoice.invoice_no")
    assert "the invoice number" in prompt and current == "NE-0001"
    prompt, _, _ = recheck.question(fields, "invoice.po_no")
    assert "Customer PO" in prompt     # how suppliers print it


@pytest.mark.parametrize("index", (1, 2, 3))
def test_supplier_drug_licence_dates_can_be_rechecked(index):
    fields = _fields()
    fields["supplier"][f"dl_no_{index}"] = {"value": f"DL-{index}"}
    prompt, _, kind = recheck.question(fields, f"supplier.dl_date_{index}")
    assert f"{('first', 'second', 'third')[index - 1]} drug licence" in prompt
    assert f"drug licence 'DL-{index}'" in prompt
    assert "not a bill-to, ship-to or invoice date" in prompt
    assert kind == "date"


@pytest.mark.parametrize("path", ["invoice.nonsense", "line_items[9].ptr", "line_items[0].bogus",
                                  "meta.page_text", "", "line_items[0]"])
def test_only_real_fields_can_be_rechecked(path):
    with pytest.raises(recheck.RecheckError):
        recheck.question(_fields(), path)


def test_a_value_on_the_page_is_offered():
    fields = _fields()
    page = "PRODUCT NAME BATCH QTY. MRP RETAILER RATE\nMEDICINE 102 TAB BN1002 12 121.00 90.75 81.68 980.16"
    ai = _AI({"value": "90.75", "printed_label": "RETAILER RATE", "printed_text": "121.00 90.75 81.68"})
    out = recheck.recheck(ai, fields, page, "line_items[2].ptr", b"%PDF", "application/pdf")
    assert (out["found"], out["printed_label"], out["on_page"], out["agrees"]) == \
        ("90.75", "RETAILER RATE", True, False)
    assert "90.75" in out["message"]


def test_a_value_not_on_the_page_is_flagged_not_offered_as_read():
    fields = _fields()
    out = recheck.recheck(_AI({"value": "99.99"}), fields, "nothing like it here 12.00",
                          "line_items[2].ptr", b"%PDF", "application/pdf")
    assert out["on_page"] is False
    assert "not printed on the bill" in out["message"]


def test_the_same_value_with_other_formatting_agrees():
    fields = _fields()
    fields["invoice"]["total_amount"] = {"value": "1234.5", "confidence": 1.0}
    out = recheck.recheck(_AI({"value": "1,234.50"}), fields, "Grand Total : 1,234.50",
                          "invoice.total_amount", b"%PDF", "application/pdf")
    assert out["agrees"] is True and out["on_page"] is True


def test_a_field_not_printed_and_an_ai_that_cannot_answer():
    fields = _fields()
    out = recheck.recheck(_AI({"value": None}), fields, "text", "invoice.po_no", b"%PDF", "application/pdf")
    assert out["found"] is None and "could not find" in out["message"]
    out = recheck.recheck(_AI(None), fields, "text", "invoice.po_no", b"%PDF", "application/pdf")
    assert out["found"] is None and "try again" in out["message"]


def test_a_scan_has_no_text_to_check_against():
    out = recheck.recheck(_AI({"value": "NE-0001"}), _fields(), None, "invoice.invoice_no",
                          b"\xff\xd8", "image/jpeg")
    assert out["on_page"] is None and out["agrees"] is True


def test_the_api_answers_without_changing_the_bill(client, db_session, monkeypatch):
    from app.models.audit_log import AuditLog
    from app.models.document import Document

    headers = register_and_login(client, email="recheck@example.com", shop="Recheck Shop")
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    data = _bill(1)
    first = _read(data)
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=first,
                       image_ref=storage.save(data, "bill.pdf", "application/pdf"))
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()

    ptr = _row(2, 1)[4]
    ai = _AI({"value": ptr, "printed_label": "RETAILER RATE", "printed_text": ptr})
    monkeypatch.setattr("app.services.ocr.get_provider", lambda: ai)
    r = client.post(f"/v1/documents/{doc_id}/recheck", json={"path": "line_items[2].ptr"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["found"], body["on_page"], body["agrees"]) == (ptr, True, False)
    assert ai.asked and ai.asked[0][1] == "application/pdf"

    # Nothing changed on the bill; the recheck is in the audit log.
    after = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    assert (after["payload"]["fields"]["line_items"][2].get("ptr") or {}).get("value") in (None, "")
    s = db_session()
    try:
        assert s.query(AuditLog).filter_by(action="document.rechecked", target=doc_id).count() == 1
    finally:
        s.close()

    r = client.post(f"/v1/documents/{doc_id}/recheck", json={"path": "invoice.bogus"}, headers=headers)
    assert r.status_code == 422


def test_header_recheck_values_survive_patch_and_reload(client, db_session):
    from app.models.document import Document
    from app.models.document_correction import DocumentCorrection
    from app.services.corrections import keep_original

    headers = register_and_login(client, email="header-save@example.com", shop="Header Save Shop")
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    original = keep_original(_read(_bill(1)))
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=original)
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()

    corrected = copy.deepcopy(original["fields"])
    expected = {
        "supplier.gstin": "27AAECD7847H1ZC",
        "supplier.dl_no_1": "20B-124567-111",
        "supplier.dl_date_1": "21/02/2025",
        "supplier.dl_no_2": "21B-124567-112",
        "supplier.dl_date_2": "21/02/2025",
        "supplier.dl_no_3": "20B-124567-113",
        "supplier.dl_date_3": "21/02/2025",
        "supplier.address": "2nd floor, Soham Square, Ahmedabad",
        "bill_to.gstin": "29ATMPA5693E1ZF",
        "bill_to.address": "Unit 505, Bengaluru",
        "ship_to.name": "NATIONAL COLL MANAGEMENT AGENCY",
        "ship_to.gstin": "29ATMPA5693E1ZF",
        "ship_to.address": "Unit 509, Bengaluru",
        "invoice.lr_no": "LR-2035873",
        "invoice.transport": "BHAVESH TRANSPORT SERVICES",
    }
    for path, value in expected.items():
        section, key = path.split(".")
        corrected[section][key] = {"value": value, "confidence": 1.0}

    response = client.patch(f"/v1/documents/{doc_id}", json={"fields": corrected}, headers=headers)
    assert response.status_code == 200, response.text
    reloaded = client.get(f"/v1/documents/{doc_id}", headers=headers)
    assert reloaded.status_code == 200
    for path, value in expected.items():
        section, key = path.split(".")
        assert response.json()["payload"]["fields"][section][key]["value"] == value
        assert reloaded.json()["payload"]["fields"][section][key]["value"] == value

    s = db_session()
    try:
        changes = {row.field for row in s.query(DocumentCorrection).filter_by(document_id=doc_id).all()}
        assert set(expected) <= changes
    finally:
        s.close()


def test_another_shops_bill_cannot_be_rechecked(client, db_session):
    from app.models.document import Document

    owner = register_and_login(client, email="owner-rc@example.com", shop="Owner RC")
    other = register_and_login(client, email="other-rc@example.com", shop="Other RC")
    shop_id = client.get("/v1/auth/me", headers=owner).json()["shop_id"]
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=_read(_bill(1)),
                       image_ref=storage.save(_bill(1), "bill.pdf", "application/pdf"))
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()
    r = client.post(f"/v1/documents/{doc_id}/recheck", json={"path": "invoice.invoice_no"}, headers=other)
    assert r.status_code == 404
