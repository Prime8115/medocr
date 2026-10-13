"""Every reviewer correction is kept with its value - the material for lessons."""
from app.models.document_correction import DocumentCorrection
from app.services import corrections
from tests.conftest import register_and_login

GSTIN = "27AAGCL1995C1ZF"


def _leaf(v):
    return {"value": v, "confidence": 1.0}


def _fields(ptr="9785.71", po=None):
    return {
        "supplier": {"name": _leaf("LUPIN LTD"), "gstin": _leaf(GSTIN)},
        "bill_to": {}, "ship_to": {},
        "invoice": {"invoice_no": _leaf("5374170236"), "invoice_date": _leaf("29.07.2025"),
                    "total_amount": _leaf("8807.14"), **({"po_no": _leaf(po)} if po else {})},
        "line_items": [{"description": _leaf("SOFTOVAC 450GM"), "batch_no": _leaf("A25309TM"),
                        "quantity": _leaf("20"), "ptr": _leaf(ptr), "rate": _leaf("440.36"),
                        "amount": _leaf("8807.14")}],
    }


def test_diffs_name_each_changed_field_and_line():
    old, new = _fields(), _fields(ptr="489.29", po="4519")
    found = {(d["field"], d["line"]): (d["old"], d["new"]) for d in corrections.diffs(old, new)}
    assert found == {("line.ptr", 0): ("9785.71", "489.29"), ("invoice.po_no", None): (None, "4519")}


def test_the_same_figure_written_differently_is_no_correction():
    old, new = _fields(ptr="9785.71"), _fields(ptr="9,785.710")
    assert corrections.diffs(old, new) == []


def test_the_first_reading_is_kept_once():
    result = {"fields": _fields(), "meta": {}}
    corrections.keep_original(result)
    result["fields"]["line_items"][0]["ptr"] = _leaf("1.00")
    corrections.keep_original(result)
    assert result["meta"]["original_fields"]["line_items"][0]["ptr"]["value"] == "9785.71"


def test_an_edit_records_what_was_read_what_was_put_and_where_it_came_from(client, db_session):
    from app.models.document import Document

    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review",
                       payload={"schema_version": "1", "doc_type": "invoice", "fields": _fields(),
                                "meta": {"pipeline": "pdf_parser", "columns": {"ptr": "TP/ PTR Value"}}})
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()

    r = client.patch(f"/v1/documents/{doc_id}", json={"fields": _fields(ptr="489.29")}, headers=headers)
    assert r.status_code == 200, r.text

    s = db_session()
    try:
        [row] = s.query(DocumentCorrection).filter_by(document_id=doc_id).all()
    finally:
        s.close()
    assert (row.field, row.line, row.value_read, row.value_corrected) == ("line.ptr", 0, "9785.71", "489.29")
    assert row.source == "TP/ PTR Value" and row.supplier_gstin == GSTIN and row.pipeline == "pdf_parser"


def test_the_learning_report_counts_only_shops_that_share(client, db_session):
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "learning_report", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "learning_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sharing = register_and_login(client)
    assert client.put("/v1/auth/shop/share-corrections", json={"share": True}, headers=sharing).status_code == 200
    shop_a = client.get("/v1/auth/me", headers=sharing).json()["shop_id"]
    private = register_and_login(client, email="other@shop.com", shop="Shop B")
    shop_b = client.get("/v1/auth/me", headers=private).json()["shop_id"]
    from app.models.document import Document
    s = db_session()
    try:
        for shop, gstin in ((shop_a, "27AAACI9822K1Z9"), (shop_a, GSTIN), (shop_b, GSTIN)):
            doc = Document(shop_id=shop, doc_type="invoice", status="needs_review", payload={})
            s.add(doc)
            s.flush()
            s.add(corrections.DocumentCorrection(shop_id=shop, document_id=doc.id, supplier_gstin=gstin,
                                                 field="line.quantity", line=0, value_read="10",
                                                 value_corrected="12", source="Qty.Sale"))
        s.commit()
        [row] = mod.report(s, days=7)
    finally:
        s.close()
    assert (row["field"], row["heading"], row["corrections"], row["suppliers"], row["shops"]) == \
        ("line.quantity", "Qty.Sale", 2, 2, 1)
