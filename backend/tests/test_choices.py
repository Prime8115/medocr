"""When a bill gives two answers to one field, the reviewer decides - once per supplier."""
import copy

from app.services.ocr import choices as ch
from app.services.ocr.verify import verify_invoice
from tests.conftest import register_and_login

ZYDUS_GSTIN = "27AAACG1895Q1ZY"
ZYDUS_TEXT = ("GSTIN: 27AAACG1895Q1ZY Order No: 100178296 Dt: 30.06.2025\n"
              "PO Number: GN-15912-1068-SHREE")


def _leaf(v):
    return {"value": v, "confidence": 1.0}


def _fields(po="100178296", date="30.06.2025"):
    return {
        "supplier": {"name": _leaf("Zydus Healthcare Limited"), "gstin": _leaf(ZYDUS_GSTIN)},
        "bill_to": {}, "ship_to": {},
        "invoice": {"invoice_no": _leaf("2299707688"), "po_no": _leaf(po), "po_date": _leaf(date),
                    "total_amount": _leaf("1000.00")},
        "line_items": [{"description": _leaf("MED"), "quantity": _leaf("10"),
                        "rate": _leaf("100.00"), "amount": _leaf("1000.00")}],
    }


def _payload():
    fields = _fields()
    meta = {"total_reconciles": True, "total_reconciled_by": "lines"}
    offered = ch.reference_choices(ZYDUS_TEXT, fields)
    ch.apply_default(fields, offered)
    meta["choices"] = offered
    meta["verification"] = verify_invoice(fields, meta)
    ch.settle_checks(meta)
    return {"schema_version": "1", "doc_type": "invoice", "fields": fields, "meta": meta}


# --- detection --------------------------------------------------------------------

def test_two_order_references_become_a_choice():
    [choice] = ch.reference_choices(ZYDUS_TEXT, _fields())
    assert [o["label"] for o in choice["options"]] == ["PO Number", "Order No"]
    assert choice["options"][0]["values"] == {"invoice.po_no": "GN-15912-1068-SHREE",
                                              "invoice.po_date": None}
    assert choice["options"][1]["values"] == {"invoice.po_no": "100178296",
                                              "invoice.po_date": "30.06.2025"}
    # The default is what was read anyway, so nothing changes until someone decides.
    assert choice["default"] == 1


def test_one_reference_is_no_choice():
    assert ch.reference_choices("Order No. : 4608 Date :20/08/2025", _fields("4608")) == []


def test_a_blank_reference_is_no_choice():
    # Menarini prints "Cust.Ord.No. : N" - a blank field, not a second reference.
    text = "Order No. : MUM25NODM01080 Date : 22-Sep-2025\nCust.Ord.No. : N"
    assert ch.reference_choices(text, _fields("MUM25NODM01080")) == []


def test_words_against_figures_is_a_choice_not_remembered():
    [choice] = ch.total_choice({"total_in_words": "144068.00", "total_in_words_disagrees": True,
                                "total_built_from_lines": "144144.00"})
    assert [o["values"]["invoice.total_amount"] for o in choice["options"]] == ["144068.00", "144144.00"]
    assert choice["remember"] is False
    assert choice["resolves"] == ["total_in_words"]


# --- deciding -----------------------------------------------------------------------

def test_an_undecided_choice_holds_the_verdict():
    payload = _payload()
    v = payload["meta"]["verification"]
    assert v["verdict"] == "needs_check"
    assert any(c["id"] == "choice_po" for c in v["checks"])


def test_choosing_fills_the_fields_and_settles_the_verdict():
    payload = _payload()
    ch.choose(payload, "po", 0, "u1")
    ch.settle_checks(payload["meta"])
    inv = payload["fields"]["invoice"]
    assert inv["po_no"]["value"] == "GN-15912-1068-SHREE"
    assert inv["po_date"]["value"] is None
    assert payload["meta"]["verification"]["verdict"] == "verified"
    assert payload["meta"]["choices"][0]["chosen"] == 0


def test_choosing_the_total_settles_the_words_check():
    meta = {"verification": {"checks": [
        {"id": "total_in_words", "label": "Total in words", "status": "fail"}]},
        "choices": ch.total_choice({"total_in_words": "144068.00", "total_in_words_disagrees": True,
                                    "total_built_from_lines": "144144.00"})}
    payload = {"fields": {"invoice": {}}, "meta": meta}
    ch.choose(payload, "total", 1, "u1")
    ch.settle_checks(meta)
    assert payload["fields"]["invoice"]["total_amount"]["value"] == "144144.00"
    assert meta["verification"]["verdict"] == "verified"


# --- through the API, and remembered per supplier ------------------------------------

def _seed(db_session, shop_id):
    from app.models.document import Document
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review",
                       payload=_payload())
        s.add(doc)
        s.commit()
        return doc.id
    finally:
        s.close()


def test_approval_waits_for_a_choice_and_the_choice_is_remembered(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _seed(db_session, shop_id)

    # Not even "acknowledging" it gets past: it is a decision.
    refused = client.post(f"/v1/documents/{doc_id}/approve", json={"acknowledged": ["choice_po"]},
                          headers=headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["open_choices"][0]["id"] == "po"

    chosen = client.post(f"/v1/documents/{doc_id}/choose",
                         json={"choice": "po", "option": 0, "remember": True}, headers=headers)
    assert chosen.status_code == 200, chosen.text
    body = chosen.json()
    assert body["payload"]["fields"]["invoice"]["po_no"]["value"] == "GN-15912-1068-SHREE"
    assert body["payload"]["meta"]["verification"]["verdict"] == "verified"
    assert client.post(f"/v1/documents/{doc_id}/approve", json={"acknowledged": []},
                       headers=headers).status_code == 200

    # Zydus's next bill arrives already decided - and still shows the choice.
    from app.services.supplier_choices import apply_remembered
    s = db_session()
    try:
        nxt = apply_remembered(s, shop_id, "invoice", _payload())
    finally:
        s.close()
    assert nxt["fields"]["invoice"]["po_no"]["value"] == "GN-15912-1068-SHREE"
    choice = nxt["meta"]["choices"][0]
    assert choice["chosen"] == 0 and choice["remembered"] is True
    assert nxt["meta"]["verification"]["verdict"] == "verified"


def test_another_shop_does_not_inherit_the_decision(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _seed(db_session, shop_id)
    client.post(f"/v1/documents/{doc_id}/choose", json={"choice": "po", "option": 0}, headers=headers)

    from app.services.supplier_choices import apply_remembered
    s = db_session()
    try:
        other = apply_remembered(s, "some-other-shop", "invoice", _payload())
    finally:
        s.close()
    assert other["meta"]["choices"][0]["chosen"] is None


def test_a_choice_that_does_not_exist_is_refused(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    doc_id = _seed(db_session, shop_id)
    r = client.post(f"/v1/documents/{doc_id}/choose", json={"choice": "po", "option": 7},
                    headers=headers)
    assert r.status_code == 422


def test_payload_is_not_mutated_by_deciding(client, db_session):
    from app.services.supplier_choices import decide
    payload = _payload()
    original = copy.deepcopy(payload)
    s = db_session()
    try:
        decide(s, "shop", "invoice", payload, "po", 0, "u1", remember=False)
    finally:
        s.close()
    assert payload == original
