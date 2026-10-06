"""Learning a new supplier's layout from the reviewer who fills in what we missed."""
from app.models.audit_log import AuditLog
from app.models.supplier_label import SupplierLabel
from app.services.ocr import learned_labels as ll
from app.services.ocr.verify import verify_invoice
from app.services.supplier_coverage import arrival
from app.services.supplier_labels import apply_learned
from tests.conftest import register_and_login

GSTIN = "27AAACI9822K1Z9"


def _page(docket="DK778812", lr_date="23-09-2025", order="4519"):
    return ("NEW SUPPLIER PVT LTD  GSTIN : 27AAACI9822K1Z9\n"
            "Invoice No. : INV-1  Invoice Date : 22-09-2025\n"
            f"Docket No. & Date : {docket} / {lr_date}\n"
            "Cust Ref : PO -\n"
            f"MUMBAI 400056 {order} 21.08.2025\n"
            "Grand Total 1000.00")


def _leaf(v, c=1.0):
    return {"value": v, "confidence": c}


def _fields(**invoice):
    return {
        "supplier": {"name": _leaf("NEW SUPPLIER PVT LTD"), "gstin": _leaf(GSTIN)},
        "bill_to": {}, "ship_to": {},
        "invoice": {"invoice_no": _leaf("INV-1"), "invoice_date": _leaf("22-09-2025"),
                    "total_amount": _leaf("1000.00"),
                    **{k: _leaf(v) for k, v in invoice.items()}},
        "line_items": [{"description": _leaf("MED"), "quantity": _leaf("10"),
                        "rate": _leaf("100.00"), "amount": _leaf("1000.00")}],
    }


# --- what is learned -------------------------------------------------------------

def test_a_label_the_reader_does_not_know_is_learned_and_found_again():
    learned = ll.learn(_page(), "invoice.lr_no", "DK778812", _fields())
    assert learned["label"] == "Docket No. & Date" and learned["where"] == "after"
    assert ll.find(_page(docket="DK901122"), learned, _fields()) == "DK901122"


def test_a_date_after_a_number_keeps_the_numbers_label():
    learned = ll.learn(_page(), "invoice.lr_date", "23-09-2025", _fields(lr_no="DK778812"))
    assert learned["label"] == "Docket No. & Date <num>"
    assert ll.find(_page(docket="DK901122", lr_date="01-10-2025"), learned, _fields()) == "01-10-2025"


def test_a_value_printed_below_its_label_is_learned():
    learned = ll.learn(_page(), "invoice.po_no", "4519", _fields())
    assert learned["where"] == "below" and "PO" in learned["label"]
    assert ll.find(_page(order="4608"), learned, _fields()) == "4608"


def test_a_value_under_several_labels_none_naming_the_field_is_not_learned():
    page = "Invoice Date : 22-09-2025  Supply Date : 22-09-2025\n"
    assert ll.learn(page, "invoice.due_date", "22-09-2025", {}) is None


def test_the_label_naming_the_field_wins_when_one_value_has_several():
    page = "Invoice Date : 21.08.2025\nOrder Date : 21.08.2025\nLR Date :21.08.2025\n"
    assert ll.learn(page, "invoice.lr_date", "21.08.2025", {})["label"] == "LR Date"


def test_a_value_of_another_shape_is_not_taken():
    learned = ll.learn(_page(), "invoice.lr_no", "DK778812", _fields())
    assert ll.find(_page(docket="778812"), learned, _fields()) is None


def test_a_label_leading_to_two_values_says_nothing():
    learned = {"path": "invoice.lr_no", "label": "Docket No", "where": "after", "shape": "A9",
               "words": 1}
    page = "Docket No : DK1 \nDocket No : DK2\n"
    assert ll.find(page, learned, {}) is None


def test_a_value_not_on_the_page_teaches_nothing():
    assert ll.learn(_page(), "invoice.lr_no", "ZZ000001", _fields()) is None
    # Money is never learned: the reconciliation owns it.
    assert ll.learn(_page(), "invoice.total_amount", "1000.00", _fields()) is None


# --- through the API, per shop and supplier ------------------------------------------

def _payload(**invoice):
    fields = _fields(**invoice)
    meta = {"total_reconciles": True, "page_text": _page()}
    meta["verification"] = verify_invoice(fields, meta)
    return {"schema_version": "1", "doc_type": "invoice", "fields": fields, "meta": meta}


def _seed(db_session, shop_id, payload):
    from app.models.document import Document
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=payload)
        s.add(doc)
        s.commit()
        return doc.id
    finally:
        s.close()


def _next_bill(db_session, shop_id, **invoice):
    payload = _payload(**invoice)
    payload["meta"]["page_text"] = _page(docket="DK901122", lr_date="01-10-2025")
    s = db_session()
    try:
        out = apply_learned(s, shop_id, "invoice", payload)
        s.commit()
        return out
    finally:
        s.close()


def test_a_reviewers_fill_in_is_learned_and_the_next_bill_arrives_filled(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _seed(db_session, shop_id, _payload())

    fixed = _fields(lr_no="DK778812", lr_date="23-09-2025")
    r = client.patch(f"/v1/documents/{doc_id}", json={"fields": fixed}, headers=headers)
    assert r.status_code == 200, r.text

    s = db_session()
    try:
        rows = {row.field: row for row in s.query(SupplierLabel).filter_by(shop_id=shop_id)}
        audit = s.query(AuditLog).filter_by(action="document.edited", target=doc_id).one()
    finally:
        s.close()
    assert rows["invoice.lr_no"].label == "Docket No. & Date"
    assert rows["invoice.lr_no"].learned_from == "blank"
    assert {"invoice.lr_no", "invoice.lr_date"} <= set(audit.detail["changed"])
    assert set(audit.detail["learned"]) == {"invoice.lr_no", "invoice.lr_date"}
    assert audit.detail["supplier_gstin"] == GSTIN

    nxt = _next_bill(db_session, shop_id)
    inv = nxt["fields"]["invoice"]
    assert inv["lr_no"] == {"value": "DK901122", "confidence": ll.LEARNED_CONFIDENCE}
    assert inv["lr_date"]["value"] == "01-10-2025"
    assert {f["path"] for f in nxt["meta"]["learned_filled"]} == {"invoice.lr_no", "invoice.lr_date"}

    # Another shop has taught us nothing.
    other = _next_bill(db_session, "some-other-shop")
    assert not other["fields"]["invoice"].get("lr_no")


def test_a_value_the_reader_did_read_is_replaced_only_if_it_was_misread(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    # Learned from a blank: a value the reader does read is left alone.
    doc_id = _seed(db_session, shop_id, _payload())
    client.patch(f"/v1/documents/{doc_id}", json={"fields": _fields(lr_no="DK778812")},
                 headers=headers)
    kept = _next_bill(db_session, shop_id, lr_no="SOMETHING")
    assert kept["fields"]["invoice"]["lr_no"]["value"] == "SOMETHING"

    # Learned from a correction: the reader is known to misread it here.
    doc_id = _seed(db_session, shop_id, _payload(lr_no="WRONG1"))
    client.patch(f"/v1/documents/{doc_id}", json={"fields": _fields(lr_no="DK778812")},
                 headers=headers)
    fixed = _next_bill(db_session, shop_id, lr_no="WRONG2")
    assert fixed["fields"]["invoice"]["lr_no"]["value"] == "DK901122"
    assert fixed["meta"]["learned_filled"][0]["replaced"] == "WRONG2"


def test_applying_never_mutates_the_payload_it_was_given(client, db_session):
    import copy
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _seed(db_session, shop_id, _payload())
    client.patch(f"/v1/documents/{doc_id}", json={"fields": _fields(lr_no="DK778812")},
                 headers=headers)
    payload = _payload()
    payload["meta"]["page_text"] = _page(docket="DK901122")
    original = copy.deepcopy(payload)
    s = db_session()
    try:
        apply_learned(s, shop_id, "invoice", payload)
    finally:
        s.close()
    assert payload == original


# --- the supplier coverage report ---------------------------------------------------

def test_the_supplier_report_shows_who_needs_attention(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    # Three bills from the new supplier, each missing its LR number on arrival.
    missed = _payload()
    missed["meta"]["verification"] = {"verdict": "needs_check", "checks": [
        {"id": "printed_not_read", "status": "fail", "fields": ["invoice.lr_no"]}]}
    missed["meta"]["arrival"] = arrival(missed["meta"])  # as the job runner records it
    ids = [_seed(db_session, shop_id, missed) for _ in range(3)]
    client.patch(f"/v1/documents/{ids[0]}", json={"fields": _fields(lr_no="DK778812")},
                 headers=headers)

    # One clean bill from a supplier we read well.
    good = _payload()
    good["fields"]["supplier"] = {"name": _leaf("ZYDUS HEALTHCARE LIMITED"),
                                  "gstin": _leaf("27AAACG1895Q1ZY")}
    good["meta"]["verification"] = {"verdict": "verified", "checks": []}
    good["meta"]["arrival"] = arrival(good["meta"])
    _seed(db_session, shop_id, good)

    r = client.get("/v1/documents/suppliers", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()["suppliers"]
    assert [row["supplier_gstin"] for row in rows] == [GSTIN, "27AAACG1895Q1ZY"]
    new = rows[0]
    assert new["status"] == "needs_attention"
    assert new["documents"] == 3 and new["verified_on_arrival"] == 0
    assert new["missed_fields"] == [{"field": "invoice.lr_no", "count": 3}]
    assert new["corrected_fields"] == [{"field": "invoice.lr_no", "count": 1}]
    assert new["edited_documents"] == 1
    assert new["learned_labels"] == 1
    assert rows[1]["status"] == "new" and rows[1]["verified_on_arrival_pct"] == 100.0


def test_the_supplier_report_is_per_shop(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    _seed(db_session, shop_id, _payload())
    other = register_and_login(client, email="other@shop.com", shop="Shop B")
    assert client.get("/v1/documents/suppliers", headers=other).json()["suppliers"] == []
