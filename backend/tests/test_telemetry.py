"""Extraction health: the aggregate we should have been watching.

The four invoice defects were discovered by a user on WhatsApp, not by us. These
tests pin the signals that would have shown them: invoices that do not add up,
Tier-1 falling back to the AI path, and duplicate rows being collapsed.
"""
from app.services.telemetry import extraction_health, health_warnings


class Doc:
    """Just enough of a Document row for the pure aggregator."""

    def __init__(self, doc_id="d", doc_type="invoice", status="needs_review", meta=None):
        self.id = doc_id
        self.doc_type = doc_type
        self.status = status
        self.payload = {"meta": meta} if meta is not None else None


def _invoice(doc_id, **meta):
    base = {"pipeline": "pdf_parser", "total_reconciles": True, "duplicates_removed": 0}
    base.update(meta)
    return Doc(doc_id, "invoice", "needs_review", base)


def test_counts_documents_by_status_type_and_pipeline():
    docs = [
        _invoice("a"),
        _invoice("b", pipeline="gemini"),
        Doc("c", "prescription", "approved", {"pipeline": "gemini"}),
    ]
    health = extraction_health(docs)
    assert health["documents"] == 3
    assert health["by_doc_type"] == {"invoice": 2, "prescription": 1}
    assert health["by_status"] == {"needs_review": 2, "approved": 1}
    assert health["by_pipeline"] == {"pdf_parser": 1, "gemini": 2}


def test_tracks_tier1_share_of_invoices():
    docs = [_invoice("a"), _invoice("b"), _invoice("c", pipeline="gemini"), _invoice("d", pipeline="gemini")]
    invoices = extraction_health(docs)["invoices"]
    assert invoices["total"] == 4
    assert invoices["tier1_parser"] == 2
    assert invoices["tier1_parser_pct"] == 50.0


def test_tracks_reconciliation_outcomes():
    docs = [
        _invoice("a", total_reconciles=True),
        _invoice("b", total_reconciles=False),
        _invoice("c", total_reconciles=None),
    ]
    invoices = extraction_health(docs)["invoices"]
    assert invoices["reconciled"] == 1
    assert invoices["total_mismatch"] == 1
    assert invoices["total_unreadable"] == 1
    assert invoices["reconciled_pct"] == 33.3


def test_tracks_duplicates_and_printed_copies():
    docs = [
        _invoice("a", duplicates_removed=286, copies_detected=1),
        _invoice("b", duplicates_removed=0, copies_detected=3),
        _invoice("c"),
    ]
    invoices = extraction_health(docs)["invoices"]
    assert invoices["with_duplicates_removed"] == 1
    assert invoices["duplicate_rows_removed"] == 286
    assert invoices["multi_copy_pdfs"] == 1


def test_counts_documents_users_reported():
    docs = [_invoice("a"), _invoice("b"), _invoice("c")]
    assert extraction_health(docs, reported_ids={"b", "zzz"})["reported_by_users"] == 1


def test_counts_documents_with_unreadable_pages():
    docs = [_invoice("a", pages_failed=2), _invoice("b", pages_failed=0)]
    assert extraction_health(docs)["pages_failed_documents"] == 1


def test_handles_an_empty_window():
    health = extraction_health([])
    assert health["documents"] == 0
    assert health["invoices"]["total"] == 0
    assert health["invoices"]["tier1_parser_pct"] is None
    assert health_warnings(health) == []


def test_tolerates_documents_with_no_payload():
    health = extraction_health([Doc("a", "invoice", "queued", None)])
    assert health["documents"] == 1
    assert health["invoices"]["total"] == 0


# --------------------------------- warnings ---------------------------------
def test_warns_when_invoices_stop_adding_up():
    docs = [_invoice(str(i), total_reconciles=False) for i in range(5)]
    warnings = health_warnings(extraction_health(docs))
    assert any("do not add up" in w for w in warnings)


def test_warns_when_tier1_stops_handling_invoices():
    """A silent Tier-1 regression is invisible: the AI fallback still returns
    plausible-looking data. This is the alarm for it."""
    docs = [_invoice(str(i), pipeline="gemini") for i in range(10)]
    warnings = health_warnings(extraction_health(docs))
    assert any("exact PDF parser" in w for w in warnings)


def test_warns_when_totals_cannot_be_read():
    docs = [_invoice(str(i), total_reconciles=None) for i in range(6)]
    assert any("no readable total" in w for w in health_warnings(extraction_health(docs)))


def test_warns_about_user_reports():
    docs = [_invoice(str(i)) for i in range(6)]
    health = extraction_health(docs, reported_ids={"1", "2"})
    assert any("reported as wrong" in w for w in health_warnings(health))


def test_stays_quiet_on_a_small_sample():
    """Two odd invoices in a slow week is noise, not a signal."""
    docs = [_invoice("a", total_reconciles=False), _invoice("b", total_reconciles=False)]
    assert health_warnings(extraction_health(docs)) == []


def test_healthy_window_produces_no_warnings():
    docs = [_invoice(str(i)) for i in range(10)]
    assert health_warnings(extraction_health(docs)) == []
