"""Regressions that can look plausible while corrupting inventory data."""
import copy

import pytest

from app.services.ocr.coverage import requirements, summarize
from app.services.ocr import lessons
from app.services.ocr.layout import signature
from app.services.ocr.verify import open_checks, reverify, verify_invoice
from app.services import supplier_profile, recheck
from tests.test_verify import _clean_bill, REPORT, TODAY
from tests.test_self_learning import HEADER, SUPPLIER, BUYER, _bill, _read, _row
from tests.conftest import register_and_login


def test_no_checks_or_only_skipped_checks_never_means_verified():
    for checks in ([], [{"id": "tax", "status": "skipped"}]):
        assert summarize({"checks": checks})["verdict"] == "incomplete"


def test_missing_batch_is_not_hidden_by_correct_arithmetic():
    fields = _clean_bill()
    fields["line_items"][0]["batch_no"] = {"value": None}
    verification = verify_invoice(fields, REPORT, today=TODAY)
    assert verification["failed"] == 0  # arithmetic is still sound
    assert verification["verdict"] == "needs_check"
    assert open_checks(verification)[0]["id"] == "extraction_coverage"
    assert "line_items[0].batch_no" in verification["review_requirements"][0]["fields"]


def test_zero_amount_and_quantity_are_present_not_missing():
    fields = _clean_bill()
    fields["line_items"][0]["quantity"]["value"] = "0"
    fields["line_items"][0]["amount"]["value"] = "0.00"
    assert requirements(fields, {}) == []


def test_incomplete_pages_remain_an_approval_requirement_after_edit():
    fields = _clean_bill()
    before = {"fields": fields, "meta": {**REPORT, "pages_failed": 1}}
    changed = reverify("invoice", before, fields)
    assert "page_coverage" in {c["id"] for c in open_checks(changed["verification"])}


@pytest.mark.parametrize("check_id", ["cross_read", "ai_review"])
def test_an_edit_invalidates_an_earlier_independent_pass(check_id):
    fields = _clean_bill()
    before = {"fields": fields, "meta": {**REPORT, "verification": {"checks": [
        {"id": check_id, "label": "Second reading", "status": "pass", "fields": []}]}}}
    changed = copy.deepcopy(fields)
    changed["line_items"][0]["batch_no"]["value"] = "DIFFERENT-BATCH"
    checks = reverify("invoice", before, changed)["verification"]["checks"]
    assert next(c for c in checks if c["id"] == check_id)["status"] == "skipped"


def test_coverage_acknowledgement_is_audited_and_an_edit_reopens_it(client, db_session):
    from app.models.document import Document
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]
    fields = _clean_bill()
    fields["line_items"][0]["expiry"] = {"value": None}
    payload = {"fields": fields, "meta": {**REPORT, "verification": verify_invoice(fields, REPORT, today=TODAY)}}
    with db_session() as db:
        document = Document(shop_id=shop_id, doc_type="invoice", status="needs_review", payload=payload)
        db.add(document)
        db.commit()
        doc_id = document.id
    refused = client.post(f"/v1/documents/{doc_id}/approve", headers=headers)
    assert refused.status_code == 409
    approved = client.post(f"/v1/documents/{doc_id}/approve", headers=headers,
                           json={"acknowledged": ["extraction_coverage"]})
    assert approved.status_code == 200
    verification = approved.json()["payload"]["meta"]["verification"]
    assert verification["verdict"] == "reviewed"
    assert verification["acknowledged"][0]["by"]
    fields["invoice"]["invoice_no"]["value"] = "TEST-124"
    edited = client.patch(f"/v1/documents/{doc_id}", headers=headers, json={"fields": fields})
    assert edited.status_code == 200
    assert "extraction_coverage" in {c["id"] for c in open_checks(edited.json()["payload"]["meta"]["verification"])}


def test_layout_identity_ignores_whitespace_but_preserves_order():
    assert signature(HEADER) == signature([h.lower().replace(" ", "\n") for h in HEADER])
    assert signature(HEADER) != signature(list(reversed(HEADER)))


def test_column_lesson_applies_only_to_the_taught_layout():
    saved = {SUPPLIER: {"layouts": {signature(HEADER): {"is": {"retailerrate": "ptr"}, "not": {}}}}}
    tokens = lessons.start(saved)
    try:
        lessons.choose(SUPPLIER)
        forced, _ = lessons.column_lessons([lessons.norm(h) for h in HEADER])
        assert forced == {4: "ptr"}
        assert lessons.column_lessons([lessons.norm(h) for h in reversed(HEADER)]) == ({}, {})
        assert lessons.prompt_note() == ""
    finally:
        lessons.finish(tokens)


def test_legacy_and_ambiguous_supplier_lessons_are_not_guessed():
    tokens = lessons.start({SUPPLIER: {"is": {"retailerrate": "ptr"}}, BUYER: {"layouts": {}}})
    try:
        assert lessons.choose(f"{SUPPLIER} {BUYER}") is None
        lessons.choose(SUPPLIER)
        assert lessons.column_lessons([lessons.norm(h) for h in HEADER]) == ({}, {})
    finally:
        lessons.finish(tokens)


def test_one_corrected_row_cannot_teach_a_multirow_price_mapping(db_session):
    from app.models.shop import Shop
    from app.models.supplier_column_lesson import SupplierColumnLesson
    before = _read(_bill(1))
    after = copy.deepcopy(before["fields"])
    after["line_items"][0]["ptr"] = {"value": _row(0, 1)[4]}
    with db_session() as db:
        shop = Shop(name="Learning safeguards")
        db.add(shop)
        db.flush()
        assert supplier_profile.learn_from_edit(db, shop.id, before, before["fields"], after, None) == []
        assert db.query(SupplierColumnLesson).count() == 0


def test_different_page_layouts_are_preserved_and_cannot_teach_one_rule(db_session, monkeypatch):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from app.models.shop import Shop
    from app.services.ocr.invoice_parser import parse_invoice_pdf
    import tests.test_self_learning as fixtures

    first = _bill(1)
    alternate = [h.replace("RETAILER RATE", "TRADE PRICE") for h in HEADER]
    monkeypatch.setattr(fixtures, "HEADER", alternate)
    writer = PdfWriter()
    for data in (first, _bill(2)):
        writer.add_page(PdfReader(BytesIO(data)).pages[0])
    joined = BytesIO()
    writer.write(joined)
    fields = parse_invoice_pdf(joined.getvalue())
    layouts = fields["_hints"]["price_labels"]["table_layouts"]
    assert {layout["signature"] for layout in layouts} == {signature(HEADER), signature(alternate)}
    after = copy.deepcopy(fields)
    for line in after["line_items"]:
        line["ptr"] = line["mrp"]
    with db_session() as db:
        shop = Shop(name="Mixed layout")
        db.add(shop)
        db.flush()
        assert supplier_profile.learn_from_edit(
            db, shop.id, {"meta": {"table_layouts": layouts, "columns": {"mrp": "MRP"}}},
            fields, after, None) == []


def test_same_supplier_can_learn_different_meanings_in_different_layouts(db_session):
    from app.models.shop import Shop

    with db_session() as db:
        shop = Shop(name="Two layouts")
        db.add(shop)
        db.flush()
        first = supplier_profile._upsert(db, shop.id, SUPPLIER, "Price", "ptr", "is", None, "layout-a")
        second = supplier_profile._upsert(db, shop.id, SUPPLIER, "Price", "pts", "is", None, "layout-b")
        assert first.active and second.active
        supplier_profile.record_outcome(db, shop.id, {"lessons_failed": [
            {"supplier_gstin": SUPPLIER, "heading": "Price", "field": "ptr", "kind": "is",
             "layout_signature": "layout-a"}]})
        assert first.times_failed == 1 and second.times_failed == 0


@pytest.mark.parametrize("value,kind,text", [
    ("12", "number", "Quantity 120"), ("12.3", "money", "Rate 123.00"),
    ("12.30", "money", "Rate 12.304"), ("B12", "text", "Batch AB123"),
    ("123", "text", "Invoice 1234"), ("12", "number", "Credit -12.00"),
    ("AB-12", "text", "Batch AB12"),
])
def test_recheck_does_not_confirm_substrings_or_different_decimals(value, kind, text):
    assert not recheck.printed_on_page(value, kind, text)


def test_decimal_shift_and_batch_punctuation_are_not_agreement():
    assert not recheck._same("money", "12.3", "123")
    assert not recheck._same("number", "1", "-1")
    assert not recheck._same("text", "AB-12", "AB12")
    assert recheck._same("money", "1,234.50", "1234.5")


def test_long_invoice_with_ambiguous_or_missing_line_page_abstains(monkeypatch):
    from app.services.ocr import pdf_utils
    from app.config import settings
    monkeypatch.setattr(settings, "ocr_ai_review_max_pages", 2)
    monkeypatch.setattr(pdf_utils, "page_count", lambda _: 4)
    monkeypatch.setattr(pdf_utils, "split_pdf", lambda *_: [b"1", b"2", b"3", b"4"])
    fields = {"line_items": [{"description": {"value": "MEDICINE"}, "batch_no": {"value": "B123"}}]}
    for texts in (["", "", "", ""], ["", "MEDICINE B123", "MEDICINE B123", ""]):
        monkeypatch.setattr(pdf_utils, "extract_text_pages", lambda _, result=texts: result)
        with pytest.raises(recheck.RecheckError, match="safely locate"):
            recheck._pages(b"pdf", "application/pdf", fields, "line_items[0].ptr")
    monkeypatch.setattr(pdf_utils, "extract_text_pages", lambda _: ["", "", "MEDICINE B123", ""])
    assert recheck._pages(b"pdf", "application/pdf", fields, "line_items[0].ptr")[0] == b"3"
