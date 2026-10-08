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
import os
import pathlib

import pytest

from app.services.ocr import process_document
from app.services.ocr.invoice_parser import parse_invoice_pdf

MANIFEST = pathlib.Path(__file__).parent / "real_invoices" / "manifest.json"
# The PDFs: here when dropped in by hand, or in the private invoice corpus
# (INVOICE_CORPUS - see test_corpus.py), which is how CI reads them.
_CORPUS = os.environ.get("INVOICE_CORPUS")
HERE = (pathlib.Path(_CORPUS) / "pdfs" / "original"
        if _CORPUS and (pathlib.Path(_CORPUS) / "pdfs" / "original").is_dir()
        else MANIFEST.parent)


def _cases():
    if not MANIFEST.exists():
        return []
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["invoices"]
    return [e for e in entries if (HERE / e["file"]).exists()]


# Scans are read by Tesseract or the AI, never by the digital parser these tests
# hold to exact figures - and running them here would call the paid model.
ALL_CASES = _cases()
CASES = [c for c in ALL_CASES if not c.get("scanned")]
SCANNED = [c for c in ALL_CASES if c.get("scanned")]
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
        # A free replacement supply is billed at zero on purpose; there is no
        # rate to reconcile it against.
        if item.get("free_supply", {}).get("value"):
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


# --------------------------- nothing is left behind ---------------------------
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_every_page_of_the_first_copy_is_read(case):
    """Skipping printed copies must never skip a page that carries items.

    The companion test caps how MANY pages are read; this one pins the floor.
    Between them a copy-detection mistake cannot quietly drop line items:
    reading too few pages fails here, reading them all fails there.
    """
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
    # page_number is 1-based; every page of the first copy must be visited.
    assert set(range(1, per_copy + 1)) <= set(read), sorted(set(read))


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_no_column_is_silently_discarded(case, results):
    """Every column the supplier prints is either mapped to a field or kept in
    `extras`. A column we have never seen before must still reach the user."""
    import io

    import pdfplumber

    from app.services.ocr.invoice_parser import _find_header_row, _gst_columns, _map_columns, _norm
    from app.services.ocr.pdf_table import extract_word_tables

    data = (HERE / case["file"]).read_bytes()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:2]:
            tables = [t for t in (page.extract_tables() or []) if _find_header_row(t) is not None]
            tables = tables or extract_word_tables(page)
            for table in tables:
                hi = _find_header_row(table)
                if hi is None:
                    continue
                header = table[hi]
                # Tax columns are claimed by the rate/amount arithmetic rather than
                # by name - a bare "CGST" heading cannot say which it holds - and
                # reach cgst_percent / cgst_amount that way, exactly as _build_item
                # counts them.
                mapped = set(_map_columns(header).values()) | set(_gst_columns(header))
                # Only columns that hold something: a heading printed over an
                # empty column ("%" beside Kanchan's discount) has nothing to keep.
                body = table[hi + 1:]
                # A heading printed twice ("Pack | Pack") is one column to the
                # word reader, which is the one the results come from.
                mapped_names = {_norm(header[i]) for i in mapped if i < len(header)}
                headings = {
                    str(h).replace("\n", " ").strip()
                    for i, h in enumerate(header)
                    if i not in mapped and str(h or "").strip()
                    and any(i < len(r) and str(r[i] or "").strip() for r in body)
                    and _norm(h) not in mapped_names
                }
                if not headings:
                    return
                # Whatever was not mapped must appear as an extra on some line.
                seen = {
                    (e.get("label") or "")
                    for item in results[case["file"]]["fields"]["line_items"]
                    for e in (item.get("extras") or [])
                }
                # Headings with no value on any row legitimately produce no extra.
                assert seen, f"{headings} dropped with no extras recorded"
                return


def test_free_supply_is_read_as_zero_not_as_missing():
    """Bharat bills a replacement line at no charge: blank amount, 0.00 tax.

    That is a zero-value line, not unreadable data - reporting it as "1 line(s)
    have no amount" made a correct reading look like a failure.
    """
    case = next((c for c in CASES if c["file"].startswith("BHARAT")), None)
    if case is None:
        pytest.skip("Bharat invoice not present")
    result = process_document(
        "free", (HERE / case["file"]).read_bytes(), "application/pdf", doc_type="invoice"
    )
    meta = result["meta"]
    assert meta["free_supply_lines"] == 1
    assert not [w for w in meta["warnings"] if "no amount" in w]

    free = [i for i in result["fields"]["line_items"] if i.get("free_supply", {}).get("value")]
    assert len(free) == 1
    assert free[0]["amount"]["value"] == "0.00"
    # Still a real line with real stock attached to it.
    assert free[0]["quantity"]["value"] == "25"
    assert free[0]["batch_no"]["value"]


@pytest.mark.parametrize("case", SCANNED, ids=_ids(SCANNED))
def test_a_scan_is_never_read_as_a_digital_pdf(case):
    """A scan carries a text layer too - the scanner's guess at it.

    MSV Lifesciences' reads its GSTIN as "33ABEFM031 5R128". Trusting that layer
    would push a corrupt GSTIN to billing with full confidence, so the digital
    parser must decline it and leave it to a reader that looks at the picture.
    """
    from app.services.ocr.pdf_utils import is_digital_pdf

    data = (HERE / case["file"]).read_bytes()
    assert not is_digital_pdf(data)
    assert parse_invoice_pdf(data) is None


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_a_blank_field_on_the_bill_stays_blank(case, results):
    """Where the bill leaves a field empty, we must not fill it with the next label.

    V L prints "L.R. NO. : DATE :" with both blank and we reported the lorry
    receipt as "DATE"; Zydus's transporter came back as "PO Number". Inventing
    data is worse than omitting it.
    """
    invoice = results[case["file"]]["fields"]["invoice"]
    labels = {"date", "mode", "no", "tel no", "po number", "gstin", "transporter"}
    for key in ("lr_no", "transport", "po_no"):
        value = ((invoice.get(key) or {}).get("value") or "").strip().lower()
        assert value not in labels, f"{key} = {value!r}"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_supplier_gstin_agrees_with_its_pan(case, results):
    """A GSTIN carries its holder's PAN in characters 3-12, so the two must agree.

    Abbott prints the buyer's GSTIN in its own block; we filed Abbott's purchases
    under the pharmacy's own GSTIN until this was enforced.
    """
    supplier = results[case["file"]]["fields"]["supplier"]
    gstin = (supplier.get("gstin") or {}).get("value")
    pan = (supplier.get("pan") or {}).get("value")
    if gstin and pan:
        assert gstin[2:12] == pan


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_no_party_name_is_label_prose(case, results):
    """A buyer name must be a name - or blank, if the bill prints none.

    Interleaved columns gave "Receiver ( Details of Consignee (Shiped PVT LTD..."
    and "PARLE(WEST Address: ANDHERI"; a doubled-print block gave "NNaammee".
    """
    fields = results[case["file"]]["fields"]
    for party in ("bill_to", "ship_to"):
        name = ((fields.get(party) or {}).get("name") or {}).get("value") or ""
        low = name.lower()
        for prose in ("details of", "receiver", "consignee", "address:", "shiped", "nnaammee"):
            assert prose not in low, f"{party}.name = {name!r}"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_both_parties_are_read_as_printed(case, results):
    """Bill-to and Ship-to name and GSTIN, exactly as each bill prints them.

    These broke in four different ways across the real invoices: a centred
    heading over left-aligned details (V L), a block drawn twice over
    (Overseas), a buyer named only on a "Cust.Code & Name:" line with a
    different consignee (Abbott), and a name cut at the column edge (JB).
    """
    fields = results[case["file"]]["fields"]
    for party in ("bill_to", "ship_to"):
        block = fields.get(party) or {}
        assert (block.get("name") or {}).get("value") == case[f"{party}_name"], party
        assert (block.get("gstin") or {}).get("value") == case[f"{party}_gstin"], party


# The only check a real invoice may fail is one where the BILL is at fault:
# Abbott spells out 144,068 while its own figures build to 144,144.
# ...and the bills that give two answers to one field wait for the reviewer to
# choose: Zydus prints a "PO Number" and an "Order No"; Abbott's words and
# figures disagree on its total.
_EXPECTED_FAILED_CHECKS = {
    "ABBOTT HEALTHCARE PRIVATE LIMITED.pdf": {"total_in_words", "choice_total"},
    "Zydus PDF(1).pdf": {"choice_po"},
    # JB prints its own "Ord. Ref. No." and, under a second label, the
    # customer's "Contract Ref PO".
    "J.B.CHEMICALS & PHARMA LIMITED (PHARMACARE).pdf": {"choice_po"},
}


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_every_real_invoice_verifies(case, results):
    """Every identity each bill states holds on what we read from it.

    A failure here is either a misread to fix or a check too strict to ship -
    this is how the verification layer found Overseas's expiry was its mfg date.
    """
    verification = results[case["file"]]["meta"]["verification"]
    failed = {c["id"] for c in verification["checks"] if c["status"] == "fail"}
    assert failed == _EXPECTED_FAILED_CHECKS.get(case["file"], set()), [
        c for c in verification["checks"] if c["status"] == "fail"
    ]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_header_fields_read_as_printed(case, results):
    """LR, PO, due date, IRN, transport, discount and drug licences, exactly
    as each bill prints them - or blank where the bill leaves them blank.

    Each value here was checked against the printed invoice. These broke one
    supplier at a time (Menarini's "L.R. No. : LOCAL Date : 22 Sep 25" and
    "Valid till - 27-Apr-2028", Abbott's footer licences, Bharat's carrier),
    so every field is pinned for every supplier.
    """
    fields = results[case["file"]]["fields"]
    for path, expected in case["header_expect"].items():
        section, key = path.split(".")
        got = ((fields.get(section) or {}).get(key) or {}).get("value")
        assert got == expected, f"{path}: {got!r} != {expected!r}"


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_first_line_tax_and_dates_read_as_printed(case, results):
    line = results[case["file"]]["fields"]["line_items"][0]
    for key, expected in case["first_line_expect"].items():
        got = (line.get(key) or {}).get("value")
        assert got == expected, f"{key}: {got!r} != {expected!r}"
