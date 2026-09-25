"""Deterministic invoice parser for digital (computer-generated) PDFs.

Reads the line-item table directly from the PDF using pdfplumber - no AI, no
per-call cost, no page limit, no rate limits, exact values. Works for any digital
distributor invoice whose columns can be recognised. Falls back to the AI
pipeline when the table can't be recognised (scanned/photographed invoices, or
unusual layouts).

Two things this parser is careful about, because both burned real users:

1. **Printed copies.** A GST tax invoice is routinely printed three times in one
   PDF - "Original for Recipient", "Duplicate for Transporter", "Triplicate for
   Supplier". Parsing every page then yields 3x the real line count. We split the
   pages into copy groups and read only the first one.

2. **Which price is "rate".** MRP, PTR, PTS and the billed rate are four
   different numbers. We map each to its own field and record which column the
   billed `rate` came from, instead of picking whichever price column happens to
   sit furthest left.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.services.ocr.amount_words import total_from_words
from app.services.ocr.pdf_table import extract_word_tables

log = logging.getLogger(__name__)

# field -> (keywords in PREFERENCE order, exclude keywords). Header cells are
# normalized to lowercase with spaces/newlines/dots removed before matching.
# Order of this dict matters: earlier fields claim their column first.
_COLS: Dict[str, Tuple[List[str], List[str]]] = {
    "description": (
        ["productname", "itemname", "description", "particulars", "product", "item", "goods", "medicine"],
        ["hsn", "code", "qty", "rate", "amount"],
    ),
    "hsn": (["hsncode", "hsn"], []),
    "batch_no": (["batchno", "batch", "lotno", "lot"], []),
    "expiry": (["expdate", "expiry", "exp"], ["mfg", "mfd"]),
    "pack": (["packing", "pack"], []),
    # Free/scheme quantity is claimed BEFORE quantity so a "F.QTY" column can
    # never be mistaken for the billed quantity.
    # "Fr. Qty." (Kanchan) normalises to "frqty", which matched nothing before.
    "free_quantity": (["freeqty", "frqty", "schemeqty", "free", "fqty", "scheme", "bonus"], ["%", "value", "amount"]),
    "quantity": (["quantity", "qty", "nos", "units"], ["free", "fqty", "scheme", "bonus", "%"]),
    "mrp": (["mrp"], ["%"]),
    "ptr": (["pricetoretailer", "retailerprice", "ptr"], ["%"]),
    "pts": (["pricetostockist", "stockistprice", "pts"], ["%"]),
    # An explicit billed-rate column. "NIR" (Net Invoice Rate) is tried first
    # because Bharat prints it beside a bare CGST "Rate" column that would
    # otherwise win. GST rate columns are excluded outright.
    "rate": (
        ["nir", "netinvoicerate", "billrate", "netrate", "purchaserate", "salerate",
         "unitprice", "prate", "rate"],
        ["%", "mrp", "ptr", "pts", "cgst", "sgst", "igst", "gst", "tax"],
    ),
    "discount_percent": (["disc%", "discount%", "discount", "disc"], ["amt", "amount", "value", "rs"]),
    # The net/taxable column is what the bill actually sums; a plain "Amount"
    # column (Kanchan) is the figure BEFORE the line discount.
    "amount": (
        ["taxableamount", "taxablevalue", "taxableamt", "netamount", "netamt", "netvalue",
         "amount", "value", "total"],
        ["%"],
    ),
}

_COPY_MARKERS = (
    ("original", re.compile(r"\boriginal\b", re.I)),
    ("duplicate", re.compile(r"\bduplicate\b", re.I)),
    ("triplicate", re.compile(r"\btriplicate\b", re.I)),
    ("quadruplicate", re.compile(r"\bquadruplicate\b", re.I)),
    ("office", re.compile(r"\boffice\s+copy\b", re.I)),
)

# India's highest GST slab. Anything above it is a misread cell, not a rate.
_MAX_GST_PERCENT = 28.0

# No real pharmacy line carries more units than this. A bigger number in the
# quantity column means we picked up an invoice or IRN number from a footer.
_MAX_LINE_QUANTITY = 1_000_000

_SUMMARY_ROW = re.compile(r"\b(total|grand|net\s*amount|sub\s*total|subtotal|carried|c/f|b/f)\b", re.I)


def _norm(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"[\s\.\n_\-]+", "", str(cell)).lower()


def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


# Suppliers name the batch column differently - Zydus "BATCH", JB "Batch
# Number", Bharat "Lot No.". Demanding the literal word "batch" rejected Bharat.
_BATCH_MARKERS = ("batch", "lotno", "lot")
_ITEM_MARKERS = ("qty", "quantity", "productname", "product", "item", "description")


def _find_header_row(table) -> Optional[int]:
    """Row index of the column-header row (a batch/lot marker + a qty/product one)."""
    for i, row in enumerate(table):
        joined = " ".join(_norm(c) for c in row)
        if any(b in joined for b in _BATCH_MARKERS) and any(m in joined for m in _ITEM_MARKERS):
            return i
    return None


def _map_columns(header_row) -> dict:
    """Map our field -> column index.

    Keywords are tried in PREFERENCE order, and the whole header is scanned for
    each keyword before moving to the next one. That is what makes the mapping
    stable across suppliers: a column literally headed "RATE" always wins over a
    "PTR" column that happens to be printed to its left.
    """
    norms = [_norm(c) for c in header_row]
    mapping: dict = {}
    taken: set = set()
    for field, (includes, excludes) in _COLS.items():
        for keyword in includes:
            hit = None
            for idx, h in enumerate(norms):
                if not h or idx in taken:
                    continue
                if keyword in h and not any(e in h for e in excludes):
                    hit = idx
                    break
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
    return mapping


def _header_text(header_row, idx: Optional[int]) -> str:
    if idx is None or idx >= len(header_row):
        return ""
    return _clean(header_row[idx])


def _gst_columns(header_row) -> List[int]:
    """Indices of GST-rate columns (CGST/SGST/IGST) whose percentage we sum.

    Most invoices mark the rate with a "%". JB heads its pair "CGST Rate | Amt."
    with no percent sign at all, so a rate column is also recognised by the word
    "rate" - the cell's first number is the percentage either way. Without this
    the invoice has no GST, and its tax-inclusive printed total can never be
    reconciled against the taxable lines.
    """
    out = []
    for idx, c in enumerate(header_row):
        h = _norm(c)
        if "gst" not in h:
            continue
        if "%" in str(c) or "rate" in h:
            out.append(idx)
    return out


_NUM = re.compile(r"-?[\d,]*\.?\d+")


def _num(cell) -> Optional[str]:
    m = _NUM.search(str(cell or ""))
    return m.group().replace(",", "") if m else None


def _f(value, confidence: Optional[float] = 1.0):
    return {"value": value if value not in (None, "") else None, "confidence": confidence}


# --------------------------- printed-copy handling ---------------------------
def _copy_label(text: str) -> Optional[str]:
    """The copy this page belongs to, from markers like 'DUPLICATE FOR TRANSPORTER'."""
    head = (text or "")[:1500]
    for name, pattern in _COPY_MARKERS:
        if pattern.search(head):
            return name
    return None


_COPY_WORDS = re.compile(r"\b(original|duplicate|triplicate|quadruplicate)\b", re.I)


def _page_signature(text: str) -> str:
    """A page's content with the copy wording removed, for comparing copies."""
    return re.sub(r"\s+", " ", _COPY_WORDS.sub("", text or "")).strip()


def _detect_copies(pdf, max_copies: int = 4) -> int:
    """How many times this invoice is printed in the file, read cheaply.

    Reading every page's text just to find the copy markers was costing eight
    seconds on a 33-page triplicate invoice - by far the slowest thing in the
    upload. Copies are exact repeats at a fixed period, so comparing page 1 with
    the page one period later settles it after reading two pages instead of all
    of them.

    Returns 1 when the file holds a single invoice.
    """
    total = len(pdf.pages)
    if total < 2:
        return 1
    try:
        first = _page_signature(pdf.pages[0].extract_text() or "")
    except Exception:  # noqa: BLE001
        return 1
    if not first:
        return 1

    for copies in range(max_copies, 1, -1):
        if total % copies:
            continue
        try:
            candidate = _page_signature(pdf.pages[total // copies].extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
        if candidate and candidate == first:
            return copies
    return 1


def _copy_groups(labels: List[Optional[str]]) -> List[List[int]]:
    """Split page indices into groups, one per printed copy.

    A new group starts whenever the copy marker changes to a different one.
    Unlabelled pages (continuation pages) stay with the copy above them.
    """
    groups: List[List[int]] = []
    current: List[int] = []
    seen: Optional[str] = None
    for i, label in enumerate(labels):
        if label and seen is not None and label != seen:
            groups.append(current)
            current = []
        if label:
            seen = label
        current.append(i)
    if current:
        groups.append(current)
    return groups


# ------------------------------ header metadata ------------------------------
# Most specific first - "Net Payable" beats a bare "Total" printed higher up.
_TOTAL_PATTERNS = [
    r"net\s*payable",
    r"net\s*to\s*pay",
    r"grand\s*total",
    r"bill\s*amount",
    r"invoice\s*(?:total|amount|value)",
    r"net\s*amount",
    r"total\s*amount",
    r"amount\s*payable",
]
_MONEY = r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2}|[\d,]{2,})"


def _extract_total(text: str) -> Optional[str]:
    """The invoice's printed total, preferring the most specific wording.

    Falls back to the amount-in-words line, which for some suppliers (Bharat
    Serums) is the only place the grand total appears at all.
    """
    for pattern in _TOTAL_PATTERNS:
        matches = re.findall(pattern + r"\s*[:\-]?\s*" + _MONEY, text or "", re.I)
        if matches:
            # The last occurrence is the foot of the bill.
            return matches[-1].replace(",", "")
    return total_from_words(text)


_ITEM_COUNT = re.compile(
    r"(?:total\s*(?:no\.?\s*of\s*)?(?:items|products|lines)|no\.?\s*of\s*items|item\s*count)"
    r"\s*[:\-]?\s*(\d{1,4})",
    re.I,
)


def _extract_item_count(text: str) -> Optional[int]:
    """The item count the invoice prints about itself, when it prints one."""
    m = _ITEM_COUNT.search(text or "")
    if not m:
        return None
    try:
        count = int(m.group(1))
    except ValueError:
        return None
    return count if 0 < count < 5000 else None


def _extract_header_meta(text: str) -> dict:
    """Best-effort supplier + invoice number/date/total from the page text."""
    supplier = None
    for line in (text or "").splitlines():
        s = line.strip()
        if re.search(r"\b(LIMITED|LTD|PVT|PRIVATE|DISTRIBUTOR|PHARMA|HEALTHCARE|ENTERPRISE|AGENC)", s, re.I):
            # Trim trailing document-type words that share the line.
            supplier = re.split(r"\b(TAX\s*INVOICE|INVOICE|ORIGINAL|DUPLICATE|CREDIT\s*NOTE)\b", s, flags=re.I)[0].strip(" -|")
            break
    inv_no = re.search(r"invoice\s*no\.?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)", text or "", re.I)
    inv_dt = re.search(r"invoice\s*no.*?dt\.?\s*[:\-]?\s*([0-9][0-9./\-]{6,})", text or "", re.I)
    gstin = re.search(r"GSTIN\s*[:\-]?\s*([0-9A-Z]{15})", text or "", re.I)
    return {
        "supplier": {"name": _f(supplier), "gstin": _f(gstin.group(1) if gstin else None), "address": _f(None)},
        "invoice": {
            "invoice_no": _f(inv_no.group(1) if inv_no else None),
            "invoice_date": _f(inv_dt.group(1) if inv_dt else None),
            "total_amount": _f(_extract_total(text)),
        },
    }


# --------------------------------- row build ---------------------------------
def _looks_like_prose(text: str) -> bool:
    """True for a sentence fragment rather than a medicine name.

    Terms-and-conditions text at the foot of a page can line up with the item
    columns well enough to be read as a row ("by us do not contravene he..."),
    and such a row can pick up a summary figure as its amount. A medicine name
    is short, carries a strength or pack size, or is set in capitals; running
    prose is several lowercase words with no digits in sight.
    """
    words = text.split()
    if len(words) < 3 or any(ch.isdigit() for ch in text):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    lowercase = sum(1 for c in letters if c.islower())
    return lowercase / len(letters) > 0.6



def price_labels(cols: dict, header_row) -> dict:
    """The supplier's own wording for each price column, e.g. {'pts': 'P.T.S.'}.

    Shown to the pharmacist as `Rate (P.T.S.)`, so the number on screen is
    traceable to a column on the paper.
    """
    out = {}
    for field in ("rate", "ptr", "pts", "mrp"):
        label = _header_text(header_row, cols.get(field))
        if label:
            out[field] = re.sub(r"\s+", " ", label).strip()
    return out


def _build_item(row, cols: dict, header_row, gst_cols: List[int]) -> Optional[dict]:
    def cell(field):
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    desc = _clean(cell("description"))
    if not desc or len(desc) < 2:
        return None

    item = {"description": _f(desc)}
    for field in ("batch_no", "expiry", "hsn", "pack"):
        if cols.get(field) is not None:
            item[field] = _f(_clean(cell(field)))
    for field in ("quantity", "free_quantity", "mrp", "ptr", "pts", "rate", "discount_percent", "amount"):
        if cols.get(field) is not None:
            item[field] = _f(_num(cell(field)))

    # `rate` is deliberately NOT guessed here. Which printed price column a bill
    # is charged on varies by supplier and by who the buyer is, and no ordering
    # of column names gets it right - invoice_checks.resolve_billed_rate decides
    # it from amount / quantity once the whole invoice has been read.

    # GST% = sum of CGST%+SGST% (or IGST%) columns.
    gst_vals = []
    for gi in gst_cols:
        if gi < len(row):
            n = _num(row[gi])
            if n:
                value = float(n)
                # A merged "SGST % Amount" cell can hand us the tax AMOUNT
                # instead of the rate; 665% GST then inflates the gross total
                # sixty-fold. India's top GST slab is 28%.
                if 0 <= value <= _MAX_GST_PERCENT:
                    gst_vals.append(value)
    if gst_vals:
        item["gst_percent"] = _f(str(round(sum(gst_vals), 2)))

    # A footer line that slips past the text filters gives itself away here: its
    # "quantity" is an IRN or invoice number a dozen digits long. Kanchan's IRN
    # line was being kept as an item, and its amount - the invoice's own basic
    # total - doubled the line sum.
    qty_text = (item.get("quantity") or {}).get("value")
    if qty_text:
        try:
            if abs(float(qty_text)) > _MAX_LINE_QUANTITY:
                return None
        except ValueError:
            pass

    # Text tests on the description are a last resort, applied ONLY to a row
    # with nothing to corroborate it. A batch number, an HSN code and an expiry
    # date are hard evidence of a real purchase line, and they outweigh any
    # guess made from wording: "Nano Leo Total Sachets Sale" is a real product
    # that reads exactly like a totals row, and was being thrown away.
    if not any((item.get(f) or {}).get("value") for f in ("batch_no", "hsn", "expiry")):
        if _SUMMARY_ROW.search(desc) or _looks_like_prose(desc):
            return None

    # Only keep rows that have at least a quantity or a batch.
    if item.get("quantity", {}).get("value") or item.get("batch_no", {}).get("value"):
        return item
    return None


def _rows_from_tables(tables, labels: dict) -> List[dict]:
    """Line items from whichever of these tables is the line-item table."""
    out: List[dict] = []
    for table in tables or []:
        hi = _find_header_row(table)
        if hi is None:
            continue
        header_row = table[hi]
        cols = _map_columns(header_row)
        if "description" not in cols or ("quantity" not in cols and "batch_no" not in cols):
            continue  # not a line-item table we understand
        gst_cols = _gst_columns(header_row)
        labels.update(price_labels(cols, header_row))
        for row in table[hi + 1:]:
            item = _build_item(row, cols, header_row, gst_cols)
            if item:
                out.append(item)
    return out


def parse_invoice_pdf(data: bytes) -> Optional[dict]:
    """Return an invoice `fields` dict parsed deterministically, or None if the
    table can't be recognised (caller then falls back to the AI pipeline).

    The returned dict carries a non-schema `_hints` key with what we learned
    about the document (copies found, item count the invoice states). The caller
    strips it before validation.
    """
    try:
        import io

        import pdfplumber
    except ImportError:  # pragma: no cover
        return None

    line_items: List[dict] = []
    page_items: Dict[int, List[dict]] = {}
    labels: dict = {}
    meta = None
    copies = 1
    stated_count = None
    full_text = ""

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total_pages = len(pdf.pages)

            # Read only the first printed copy. Settled by comparing two pages
            # rather than by reading every page's text, which was the single
            # slowest step of an upload: eight seconds on a 33-page invoice.
            copies = _detect_copies(pdf)
            per_copy = total_pages // copies if copies > 1 else total_pages
            wanted = list(range(per_copy))
            if copies > 1:
                log.info(
                    "invoice_parser: %d printed copies detected; reading pages 1-%d of %d",
                    copies, per_copy, total_pages,
                )

            # Text is collected only for the pages we actually read. Their
            # character layer has to be parsed for the tables anyway, so this
            # costs almost nothing - whereas doing it for every page of a
            # triplicate invoice meant paying for two copies we then discard.
            page_texts: Dict[int, str] = {}

            for page_no in wanted:
                page = pdf.pages[page_no]
                page_texts[page_no] = page.extract_text() or ""
                if meta is None:
                    meta = _extract_header_meta(page_texts[page_no])
                # Two ways to find the table, and we keep whichever actually
                # yields more line items.
                #
                # Ruled extraction is exact when the invoice draws its grid, so
                # it wins ties. But a detected table is not necessarily the
                # LINE-ITEM table: Kanchan draws a box round its address block
                # that scans as one and hands back a row or two of nonsense.
                # Judging on the outcome rather than on "did we find a table"
                # is what keeps both layouts working.
                ruled_labels: dict = {}
                word_labels: dict = {}
                ruled_items = _rows_from_tables(page.extract_tables() or [], ruled_labels)
                word_items = _rows_from_tables(extract_word_tables(page), word_labels)
                if len(word_items) > len(ruled_items):
                    labels.update(word_labels)
                    page_items[page_no] = word_items
                else:
                    labels.update(ruled_labels)
                    page_items[page_no] = ruled_items

            # Safety net for copies the period check cannot see - copies of
            # unequal length, or a page count that is not an exact multiple.
            # The page texts are already in hand, so this costs nothing.
            if copies == 1:
                groups = _copy_groups([_copy_label(page_texts[i]) for i in wanted])
                if len(groups) > 1:
                    copies = len(groups)
                    wanted = groups[0]
                    log.info("invoice_parser: %d printed copies found by marker", copies)

            for page_no in wanted:
                line_items.extend(page_items.get(page_no, []))
            full_text = "\n".join(page_texts[i] for i in wanted)
            stated_count = _extract_item_count(full_text)
    except Exception as exc:  # noqa: BLE001 - any parsing failure -> fall back to AI
        log.warning("invoice_parser: deterministic parse failed (%s); falling back to AI", exc, exc_info=True)
        return None

    if not line_items:
        return None

    fields = meta or {"supplier": {}, "invoice": {}}
    # A total printed only on the last page won't be in the first page's text.
    if not (fields.get("invoice", {}).get("total_amount") or {}).get("value"):
        fields.setdefault("invoice", {})["total_amount"] = _f(_extract_total(full_text))
    fields["line_items"] = line_items
    fields["_hints"] = {
        "copies_detected": copies,
        "stated_item_count": stated_count,
        "price_labels": labels,
    }
    return fields
