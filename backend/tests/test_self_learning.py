"""Correct a supplier once, never again: column lessons from reviewers' edits."""
from io import BytesIO

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

from app.config import settings
from app.models.supplier_column_lesson import SupplierColumnLesson
from app.services import supplier_profile
from app.services.ocr import process_document
from tests.conftest import register_and_login

SUPPLIER = "27AAGCL1995C1ZF"
BUYER = "27AAECD7847H1ZC"
HEADER = ["PRODUCT NAME", "BATCH", "QTY.", "MRP", "RETAILER RATE", "NET RATE", "VALUE"]


def _row(i, invoice):
    qty, mrp = 10 + i, 100.0 + 10 * i + invoice
    retailer = round(mrp * 0.75, 2)
    net = round(retailer * 0.9, 2)
    return [f"MEDICINE {invoice}{i:02d} TAB", f"BN{invoice}{i:03d}", str(qty), f"{mrp:.2f}",
            f"{retailer:.2f}", f"{net:.2f}", f"{qty * net:.2f}"]


def _bill(invoice: int) -> bytes:
    """A supplier that heads its PTR "RETAILER RATE" - wording the reader does not know."""
    rows = [_row(i, invoice) for i in range(4)]
    total = round(sum(float(r[-1]) for r in rows), 2)
    style = ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=10)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
    doc.build([
        Paragraph("NEW ERA PHARMA DISTRIBUTORS PVT LTD &nbsp; TAX INVOICE", style),
        Paragraph(f"GSTIN: {SUPPLIER}", style),
        Paragraph(f"Bill To: EASTERN AGENCIES HEALTHCARE PVT LTD GSTIN: {BUYER}", style),
        Paragraph(f"Invoice No: NE-{invoice:04d} Date: 0{invoice}/09/2025", style),
        Spacer(1, 8), Table([HEADER] + rows), Spacer(1, 8),
        Paragraph(f"Grand Total : {total:.2f}", style),
    ])
    return buf.getvalue()


def _read(data: bytes, lessons=None) -> dict:
    saved = (settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr)
    settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr = None, None, True
    try:
        return process_document("t", data, "application/pdf", doc_type="invoice", lessons=lessons)
    finally:
        settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr = saved


def _ptrs(result):
    return [(i.get("ptr") or {}).get("value") for i in result["fields"]["line_items"]]


def test_the_bill_alone_leaves_the_ptr_blank():
    first = _read(_bill(1))
    assert first["meta"]["pipeline"] == "pdf_parser"
    assert _ptrs(first) == [None] * 4
    # ...but every column is traced to its heading.
    assert first["meta"]["columns"]["rate"] == "NET RATE"


def test_a_correction_teaches_the_column_and_the_next_bill_arrives_right(client, db_session):
    from app.models.document import Document
    from app.services.corrections import keep_original

    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    first = keep_original(_read(_bill(1)))
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=first)
        s.add(doc)
        s.commit()
        doc_id = doc.id
    finally:
        s.close()

    # The reviewer types each line's PTR - the figure under "RETAILER RATE".
    fixed = first["fields"]
    for line, row in zip(fixed["line_items"], [_row(i, 1) for i in range(4)]):
        line["ptr"] = {"value": row[4], "confidence": 1.0}
    r = client.patch(f"/v1/documents/{doc_id}", json={"fields": fixed}, headers=headers)
    assert r.status_code == 200, r.text

    s = db_session()
    try:
        [lesson] = s.query(SupplierColumnLesson).filter_by(shop_id=shop_id).all()
        assert (lesson.supplier_gstin, lesson.field, lesson.kind, lesson.heading_printed) == \
            (SUPPLIER, "ptr", "is", "RETAILER RATE")
        lessons = supplier_profile.lessons_for_shop(s, shop_id)
    finally:
        s.close()

    # The supplier's next bill: read with the shop's lessons, PTR arrives filled.
    second = _read(_bill(2), lessons)
    assert _ptrs(second) == [row[4] for row in (_row(i, 2) for i in range(4))]
    assert second["meta"]["lessons_applied"] == [
        {"supplier_gstin": SUPPLIER, "field": "ptr", "heading": "RETAILER RATE", "kind": "is"}]
    assert second["meta"]["total_reconciles"] is True


def test_another_suppliers_bill_is_untouched():
    lessons = {"27AAACG1895Q1ZY": {"is": {"retailerrate": "ptr"}, "not": {}}}
    result = _read(_bill(3), lessons)
    assert _ptrs(result) == [None] * 4 and not result["meta"].get("lessons_applied")


def test_a_lesson_that_breaks_the_bill_is_dropped_for_it():
    # A wrong lesson: the quantity is the MRP column. The bill stops adding up,
    # so it is read again without lessons - and the lesson reported as failed.
    lessons = {SUPPLIER: {"is": {"mrp": "quantity"}, "not": {}}}
    result = _read(_bill(4), lessons)
    assert result["meta"]["total_reconciles"] is True
    assert result["meta"]["lessons_failed"][0]["field"] == "quantity"
    assert not result["meta"].get("lessons_applied")


def test_a_lesson_undone_twice_is_retired(db_session):
    from app.models.shop import Shop

    s = db_session()
    try:
        shop = Shop(name="T")
        s.add(shop)
        s.flush()
        row = SupplierColumnLesson(shop_id=shop.id, supplier_gstin=SUPPLIER, heading="retailerrate",
                                   heading_printed="RETAILER RATE", field="ptr", kind="is", times_used=0,
                                   times_undone=0, times_failed=0, active=True)
        s.add(row)
        s.flush()
        line = {"ptr": {"value": "75.00"}, "batch_no": {"value": "B1"}}
        before = {"supplier": {"gstin": {"value": SUPPLIER}}, "line_items": [line]}
        after = {"supplier": {"gstin": {"value": SUPPLIER}},
                 "line_items": [{**line, "ptr": {"value": "80.00"}}]}
        payload = {"meta": {"lessons_applied": [{"supplier_gstin": SUPPLIER, "field": "ptr",
                                                 "heading": "RETAILER RATE", "kind": "is"}]}}
        for _ in range(2):
            supplier_profile.learn_from_edit(s, shop.id, payload, before, after, None)
        s.flush()
        assert row.times_undone == 2 and row.active is False
        assert supplier_profile.lessons_for_shop(s, shop.id) == {}
    finally:
        s.rollback()
        s.close()


def test_lessons_can_be_seen_and_forgotten(client, db_session):
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    s = db_session()
    try:
        row = SupplierColumnLesson(shop_id=shop_id, supplier_gstin=SUPPLIER, heading="retailerrate",
                                   heading_printed="RETAILER RATE", field="ptr", kind="is", times_used=3,
                                   times_undone=0, times_failed=0, active=True)
        s.add(row)
        s.commit()
        lesson_id = row.id
    finally:
        s.close()
    listed = client.get("/v1/documents/suppliers/lessons", headers=headers).json()["lessons"]
    assert [(l["heading"], l["field"], l["times_used"]) for l in listed] == [("RETAILER RATE", "ptr", 3)]
    assert client.delete(f"/v1/documents/suppliers/lessons/{lesson_id}", headers=headers).status_code == 200
    assert client.get("/v1/documents/suppliers/lessons", headers=headers).json()["lessons"] == []
    # Another shop cannot touch it.
    assert client.delete(f"/v1/documents/suppliers/lessons/{lesson_id}", headers=headers).status_code == 404


def test_the_ai_is_told_a_known_suppliers_columns():
    from app.services.ocr import lessons
    tokens = lessons.start({SUPPLIER: {"is": {"retailerrate": "ptr"}, "not": {"tpptrvalue": ["ptr"]},
                                       "printed": {"retailerrate": "RETAILER RATE", "tpptrvalue": "TP/ PTR Value"}}})
    try:
        assert lessons.prompt_note() == ""            # supplier not known yet
        lessons.choose(f"GSTIN: {SUPPLIER}")
        note = lessons.prompt_note()
        assert '"RETAILER RATE" is the ptr' in note and '"TP/ PTR Value" is NOT the ptr' in note
    finally:
        lessons.finish(tokens)
