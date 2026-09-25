"""Regression tests against real supplier invoices.

Generated fixtures nearly cost us this feature: they were drawn with ruled table
borders, so Tier-1 passed on them while failing completely on all four real
invoices a customer sent - three of which draw no rules the table finder can
use, and one of which draws them round the wrong block.

So these run against the genuine PDFs. They are gitignored (supplier GSTINs,
trade prices, margins), and the suite skips when they are absent, which keeps CI
green. `manifest.json` holds only structural expectations - item counts, printed
copies, which price column the bill is charged on - and no money figures.

To run them: drop the PDFs into tests/real_invoices/.
"""
import json
import pathlib

import pytest

from app.services.ocr import process_document
from app.services.ocr.invoice_parser import parse_invoice_pdf

HERE = pathlib.Path(__file__).parent / "real_invoices"
MANIFEST = HERE / "manifest.json"


def _cases():
    if not MANIFEST.exists():
        return []
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["invoices"]
    return [e for e in entries if (HERE / e["file"]).exists()]


CASES = _cases()
pytestmark = pytest.mark.skipif(
    not CASES, reason="real invoice PDFs not present (see tests/real_invoices/manifest.json)"
)


def _ids(cases):
    return [c["file"].split(".")[0][:18] for c in cases]


@pytest.fixture(scope="module")
def results():
    """Extract every invoice once; the Zydus file alone is 33 pages."""
    out = {}
    for case in CASES:
        data = (HERE / case["file"]).read_bytes()
        out[case["file"]] = process_document("real", data, "application/pdf", doc_type="invoice")
    return out


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_deterministic_parser_handles_it(case, results):
    """Tier-1 must read it. Falling back to the AI means paying for a page we
    can already read exactly - and losing exactness on the numbers."""
    meta = results[case["file"]]["meta"]
    assert meta["pipeline"] == "pdf_parser", f"{case['file']} fell back to the AI path"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_item_count(case, results):
    """The count on screen must be the count on the paper."""
    meta = results[case["file"]]["meta"]
    assert meta["item_count"] == case["item_count"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_printed_copies_are_read_once(case, results):
    """Zydus prints the same invoice three times in one file: 143 items, not 429."""
    meta = results[case["file"]]["meta"]
    assert meta.get("copies_detected") == case["copies_detected"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_billed_rate_column(case, results):
    """Four suppliers, four different billed columns - Rate, PTS, PTS and NIR.

    No ordering of column names gets this right; it is settled by arithmetic,
    from amount / quantity. Getting it wrong puts the wrong purchase price into
    the pharmacy's stock.
    """
    payload = results[case["file"]]
    assert payload["meta"].get("billed_rate_column") == case["billed_rate_column"]

    items = payload["fields"]["line_items"]
    assert items, "no line items"
    first = items[0]
    assert first["rate_source"]["value"] == case["rate_source_label"]
    # The label is not a measurement and must not dilute overall confidence.
    assert first["rate_source"]["confidence"] is None


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_rate_equals_amount_over_quantity(case, results):
    """Whatever column it came from, `rate` must explain the line's amount."""
    for item in results[case["file"]]["fields"]["line_items"]:
        qty = item["quantity"]["value"]
        rate = item["rate"]["value"]
        amount = item["amount"]["value"]
        if not (qty and rate and amount):
            continue
        unit = float(amount) / float(qty)
        # Equal, or above it by no more than a line discount.
        assert 0.70 <= unit / float(rate) <= 1.005, item["description"]["value"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_totals_reconcile(case, results):
    """The lines, plus their GST, must add up to the total printed on the bill.

    `null` in the manifest means the invoice prints no machine-readable total
    (Bharat prints its grand total only in words), in which case we must say so
    rather than quietly claim the invoice is fine.
    """
    meta = results[case["file"]]["meta"]
    assert meta.get("total_reconciles") == case["total_reconciles"]
    if case["total_reconciles"] is None:
        assert any("total could not be read" in w for w in meta["warnings"])


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_clean_invoice_raises_no_alarms(case, results):
    """A bill that reconciles must not also cry wolf.

    The warning banner is only worth anything if it stays quiet on good
    invoices. Anything an invoice *should* warn about is declared in the
    manifest - Bharat carries one free replacement line with no taxable value,
    and saying so is correct - so any warning beyond those is a false alarm.
    """
    meta = results[case["file"]]["meta"]
    expected = case.get("expected_warnings", [])
    unexpected = [
        w for w in meta["warnings"]
        if " " in w and not any(e in w for e in expected)
    ]
    assert unexpected == [], unexpected


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_unruled_invoices_need_the_coordinate_rebuild(case, results):
    """Pins how each invoice is laid out, so the coordinate rebuild cannot be
    dropped as 'unused'.

    Two of these four draw no rules pdfplumber can find at all. Kanchan is the
    subtle one: it DOES draw a box that scans as a table, but it is round the
    address block, not the line items - which is why the fallback is decided on
    whether usable rows came out, never on whether some table was found.
    """
    import io

    import pdfplumber

    from app.services.ocr.invoice_parser import _find_header_row

    data = (HERE / case["file"]).read_bytes()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        ruled = any(
            _find_header_row(t) is not None
            for page in pdf.pages[:3]
            for t in (page.extract_tables() or [])
        )
    assert ruled == case["has_ruled_table"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_every_line_has_a_medicine_name(case, results):
    """Footer prose and declaration text must not survive as line items."""
    for item in results[case["file"]]["fields"]["line_items"]:
        name = (item["description"]["value"] or "").strip()
        assert len(name) >= 2
        assert not name.lower().startswith(("by us", "been ", "responsible", "we hereby"))


def test_parser_returns_hints_for_the_pipeline():
    """The parser hands the pipeline the supplier's own price-column wording."""
    case = next((c for c in CASES if c["file"].startswith("KANCHAN")), None)
    if case is None:
        pytest.skip("Kanchan invoice not present")
    parsed = parse_invoice_pdf((HERE / case["file"]).read_bytes())
    assert parsed is not None
    labels = parsed["_hints"]["price_labels"]
    assert labels.get("pts") == "P.T.S."
    assert labels.get("ptr") == "P.T.R."


# --------------------------------- performance ---------------------------------
# A pharmacist is standing at the counter waiting for this. Generous enough not
# to flake on a slow CI box, tight enough to catch a real regression: before the
# page-reading was trimmed, the 33-page invoice took 11s.
_BUDGET_SECONDS = {1: 3.0, 2: 3.0, 3: 4.0, 33: 8.0}


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_extraction_is_fast_enough_to_wait_for(case):
    """Extraction must feel immediate, including for a triplicate invoice.

    The slow part was never the table parsing (well under a second); it was
    reading the text of every page in the file, including the two printed copies
    we then discard.
    """
    import time

    data = (HERE / case["file"]).read_bytes()
    budget = _BUDGET_SECONDS.get(case["pages"], 8.0)

    started = time.perf_counter()
    process_document("perf", data, "application/pdf", doc_type="invoice")
    elapsed = time.perf_counter() - started

    assert elapsed < budget, f"{case['file']} took {elapsed:.1f}s (budget {budget}s)"


def test_duplicate_pages_are_never_parsed():
    """The copies must be skipped, not read and then thrown away.

    Zydus is 33 pages holding one 11-page invoice three times. If the page
    budget ever regresses to reading all of them, this catches it.
    """
    case = next((c for c in CASES if c["copies_detected"] > 1), None)
    if case is None:
        pytest.skip("no multi-copy invoice present")

    import io

    import pdfplumber

    read: list = []
    original = pdfplumber.page.Page.extract_text

    def counting(self, *a, **kw):
        read.append(self.page_number)
        return original(self, *a, **kw)

    pdfplumber.page.Page.extract_text = counting
    try:
        parse_invoice_pdf((HERE / case["file"]).read_bytes())
    finally:
        pdfplumber.page.Page.extract_text = original

    per_copy = case["pages"] // case["copies_detected"]
    # One copy's pages, plus the couple sampled to detect the repeat.
    assert len(set(read)) <= per_copy + 2, sorted(set(read))


# ------------------------------ header fields ------------------------------
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_supplier_is_the_vendor_not_the_customer(case, results):
    """An invoice prints the vendor and the customer in the same header.

    Flattened to text those columns interleave, and "the first line that looks
    like a company" picked the BUYER - JB Chemicals was being filed under its
    own customer's name, which would send every purchase to the wrong vendor.
    """
    supplier = results[case["file"]]["fields"]["supplier"]
    assert supplier["name"]["value"] == case["supplier_name"]
    # The customer on all four of these invoices is Eastern Agencies.
    assert "EASTERN" not in (supplier["name"]["value"] or "").upper()


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_supplier_gstin_is_the_suppliers_own(case, results):
    """Spelled "GSTIN No :", "GSTin:" and "GS Tin :" across these four, so the
    statutory shape is matched rather than the label - and it must come from the
    supplier's block, never the buyer's."""
    supplier = results[case["file"]]["fields"]["supplier"]
    assert supplier["gstin"]["value"] == case["supplier_gstin"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_invoice_number_and_date(case, results):
    invoice = results[case["file"]]["fields"]["invoice"]
    assert invoice["invoice_no"]["value"] == case["invoice_no"]
    assert invoice["invoice_date"]["value"] == case["invoice_date"]
    # Post-processing turns it into an ISO date for the connectors.
    assert invoice["invoice_date"]["normalized"], "date did not normalise"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_every_line_has_the_fields_a_pharmacist_needs(case, results):
    """Batch, expiry, quantity, rate, amount and GST on every single line.

    Bharat heads one column "Exp.date / Mfg.date"; excluding anything mentioning
    "mfg" silently dropped the expiry from every line of that invoice.
    """
    items = results[case["file"]]["fields"]["line_items"]
    assert items
    for item in items:
        for field in ("description", "batch_no", "expiry", "quantity", "rate", "gst_percent"):
            assert item[field]["value"], f"{field} missing on {item['description']['value']!r}"
        # Amount is absent only on a free replacement line, which the invoice
        # itself leaves blank - the manifest declares that case.
        if not item["amount"]["value"]:
            assert case.get("expected_warnings"), item["description"]["value"]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_no_stray_punctuation_in_identifiers(case, results):
    """A product name spilling into the next column left batch numbers like
    "() TMET6"."""
    for item in results[case["file"]]["fields"]["line_items"]:
        for field in ("batch_no", "hsn"):
            value = item[field]["value"]
            if value:
                assert value == value.strip()
                assert "(" not in value and ")" not in value, (field, value)
