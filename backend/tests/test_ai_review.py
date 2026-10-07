"""The AI reviewer: a second model checks each value read against the scan."""
from app.services.ocr import ai_review
from app.services.ocr.verify import reverify


def _f(value):
    return {"value": value, "confidence": 0.9}


def _fields():
    return {
        "supplier": {"name": _f("MSV LIFESCIENCES"), "gstin": _f("27ABCDE1234F3ZY")},
        "bill_to": {"name": _f("Prime Pharma")},
        "invoice": {"invoice_no": _f("M-544"), "invoice_date": _f("6-Oct-25"), "total_amount": _f("15,034.00")},
        "line_items": [
            {"description": _f("Glimedose MP2"), "batch_no": _f("OVT-25327"), "quantity": _f("100"),
             "amount": _f("6,070.00")},
            {"description": _f("Ferodose XT"), "batch_no": _f("OVT-25263"), "quantity": _f("20"),
             "amount": _f("1,785.00")},
        ],
    }


def _ids(values, path):
    return next(vid for vid, v in values.items() if v[0] == path)


def test_only_values_read_are_offered_with_short_ids():
    values, left_out = ai_review.values_to_review(_fields(), max_lines=60)
    paths = {v[0] for v in values.values()}
    assert "invoice.invoice_no" in paths and "line_items[1].batch_no" in paths
    assert "invoice.total_igst_amount" not in paths      # blank: nothing to check
    assert left_out == 0
    prompt = ai_review.build_prompt(values)
    assert '"OVT-25263"' in prompt and "line 2 batch" in prompt


def test_lines_beyond_the_cap_are_counted_not_reviewed():
    values, left_out = ai_review.values_to_review(_fields(), max_lines=1)
    assert left_out == 1
    assert not any(v[0].startswith("line_items[1]") for v in values.values())


def test_all_matched_passes():
    values, _ = ai_review.values_to_review(_fields(), 60)
    check = ai_review.judge(values, {"match": list(values), "differs": [], "unclear": []})
    assert check["status"] == "pass"
    assert f"{len(values)} value(s) confirmed" in check["message"]


def test_a_difference_fails_and_flags_that_field():
    values, _ = ai_review.values_to_review(_fields(), 60)
    batch = _ids(values, "line_items[0].batch_no")
    answer = {"match": [v for v in values if v != batch],
              "differs": [{"id": batch, "seen": "OVT-25321"}]}
    check = ai_review.judge(values, answer)
    assert check["status"] == "fail"
    assert check["fields"] == ["line_items[0].batch_no"]
    assert "OVT-25321" in check["message"]


def test_formatting_differences_are_agreement():
    values, _ = ai_review.values_to_review(_fields(), 60)
    total = _ids(values, "invoice.total_amount")
    date = _ids(values, "invoice.invoice_date")
    name = _ids(values, "supplier.name")
    answer = {"match": [v for v in values if v not in (total, date, name)],
              "differs": [{"id": total, "seen": "15034"}, {"id": date, "seen": "06/10/2025"},
                          {"id": name, "seen": "M.S.V. Lifesciences"}]}
    assert ai_review.judge(values, answer)["status"] == "pass"


def test_an_empty_answer_confirms_nothing():
    """A lazy reviewer that answers nothing must never pass the bill."""
    values, _ = ai_review.values_to_review(_fields(), 60)
    assert ai_review.judge(values, {"match": [], "differs": [], "unclear": []})["status"] == "skipped"
    assert ai_review.judge(values, None)["status"] == "skipped"
    assert ai_review.judge(values, {"match": ["Z9", 7]})["status"] == "skipped"


def test_unanswered_values_are_reported_not_confirmed():
    values, _ = ai_review.values_to_review(_fields(), 60)
    some = list(values)[:3]
    check = ai_review.judge(values, {"match": some})
    assert check["status"] == "pass"
    assert "3 value(s) confirmed" in check["message"]
    assert f"{len(values) - 3} value(s) it could not confirm" in check["message"]


def test_a_value_both_matched_and_disputed_counts_as_disputed():
    values, _ = ai_review.values_to_review(_fields(), 60)
    inv = _ids(values, "invoice.invoice_no")
    check = ai_review.judge(values, {"match": list(values), "differs": [{"id": inv, "seen": "M-545"}]})
    assert check["status"] == "fail"
    assert check["message"].startswith("1 value(s) the reviewer sees differently")
    assert check["fields"] == ["invoice.invoice_no"]


class _Provider:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.seen = answer, exc, None

    def review_json(self, prompt, data, ct):
        self.seen = (prompt, ct)
        if self.exc:
            raise self.exc
        return self.answer


def test_review_never_raises():
    check = ai_review.review(_Provider(exc=RuntimeError("quota")), _fields(), b"img", "image/jpeg")
    assert check["status"] == "skipped"


def test_review_of_a_photo_sends_the_photo():
    values, _ = ai_review.values_to_review(_fields(), 60)
    provider = _Provider(answer={"match": list(values)})
    check = ai_review.review(provider, _fields(), b"img", "image/jpeg")
    assert check["status"] == "pass"
    assert provider.seen[1] == "image/jpeg"


def test_unsupported_provider_is_skipped():
    from app.services.ocr.mock import MockProvider

    check = ai_review.review(MockProvider(), _fields(), b"img", "image/jpeg")
    assert check["status"] == "skipped"


def test_review_survives_an_edit_and_an_edit_resolves_it():
    fields = _fields()
    values, _ = ai_review.values_to_review(fields, 60)
    batch = _ids(values, "line_items[0].batch_no")
    check = ai_review.judge(values, {"match": [v for v in values if v != batch],
                                     "differs": [{"id": batch, "seen": "OVT-25321"}]})
    old = {"fields": _fields(), "meta": {"verification": {"checks": [check]}}}

    kept = reverify("invoice", old, _fields())
    assert any(c["id"] == "ai_review" and c["status"] == "fail" for c in kept["verification"]["checks"])

    edited = _fields()
    edited["line_items"][0]["batch_no"] = _f("OVT-25321")
    after = reverify("invoice", old, edited)
    assert any(c["id"] == "ai_review" and c["status"] == "pass" for c in after["verification"]["checks"])


# --- in the pipeline ------------------------------------------------------------------

class _ScanReader:
    """Reads a scan as one MSV line; reviews with a scripted answer."""
    name = "scripted"

    def __init__(self, answer=None):
        self.answer, self.reviewed = answer, []

    def classify(self, *_a):
        return "invoice"

    def extract(self, *_a):
        return {"invoice": {"invoice_no": _f("M-544"), "total_amount": _f("6,070.00")},
                "line_items": [{"description": _f("Glimedose MP2"), "batch_no": _f("OVT-25327"),
                                "quantity": _f("100"), "rate": _f("60.70"), "amount": _f("6,070.00")}]}

    def review_json(self, prompt, data, ct):
        self.reviewed.append((ct, data[:5]))
        return self.answer


def _scan(monkeypatch, provider, **settings_over):
    import app.services.ocr as ocr
    from app.config import settings
    from app.services.ocr import cross_read
    from tests.test_scanned_pdf import scanned_pdf

    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    monkeypatch.setattr(cross_read, "second_reading", lambda *_a: None)
    for k, v in settings_over.items():
        monkeypatch.setattr(settings, k, v)
    meta = ocr.process_document("d", scanned_pdf(), "application/pdf", "invoice")["meta"]
    return {c["id"]: c for c in meta["verification"]["checks"]}, meta


def test_a_scan_is_reviewed_from_its_picture(monkeypatch):
    provider = _ScanReader({"differs": [{"id": "L1.c", "seen": "OVT-25321"}], "match": []})
    checks, meta = _scan(monkeypatch, provider)
    assert provider.reviewed and provider.reviewed[0][0] == "application/pdf"
    assert checks["ai_review"]["status"] == "fail"
    assert checks["ai_review"]["fields"] == ["line_items[0].batch_no"]
    assert any("AI reviewer" in w for w in meta["warnings"])


def test_the_reviewer_can_be_switched_off(monkeypatch):
    provider = _ScanReader({"match": []})
    checks, _ = _scan(monkeypatch, provider, ocr_ai_review=False)
    assert "ai_review" not in checks and not provider.reviewed


def test_a_digital_pdf_is_not_reviewed(monkeypatch):
    import pytest as _pytest
    _pytest.importorskip("reportlab")
    import app.services.ocr as ocr
    from tests.fixtures.invoice_pdf import build_invoice_pdf

    provider = _ScanReader({"match": []})
    monkeypatch.setattr(ocr, "get_provider", lambda: provider)
    ocr.process_document("d", build_invoice_pdf(n_items=5), "application/pdf", "invoice")
    assert not provider.reviewed
