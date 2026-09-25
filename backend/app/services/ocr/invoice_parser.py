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
    # No "mfg" exclusion: a Mfg-only column never contains "exp", while Bharat
    # heads one column "Exp.date / Mfg.date" - excluding it lost every expiry.
    "expiry": (["expdate", "expiry", "exp"], []),
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


# Where the BUYER's details start. Everything to the left of this belongs to the
# supplier - the two are side-by-side columns that flatten into interleaved text.
_BUYER_HEADING = re.compile(r"\b(bill(?:ed)?\s*to|ship\s*to|sold\s*to|buyer|consignee|customer)\b", re.I)

_COMPANY = re.compile(
    r"\b(LIMITED|LTD|PVT|PRIVATE|DISTRIBUTOR|PHARMA|HEALTHCARE|ENTERPRISE|AGENC|LABORATOR|"
    r"INDUSTRIES|REMEDIES|BIOTECH|LIFESCIENCE|LOGISTICS|MARKETING|TRADERS)\b",
    re.I,
)
# "C.A. of X" / "C&F of X" names the principal a carrying agent acts for. The
# invoicing party - the one whose GSTIN is on the bill, and the one the pharmacy
# actually buys from - is the agent itself, printed separately.
_AGENT_OF = re.compile(r"^\s*(c\.?\s*a\.?|c\s*&\s*f|c\.?f\.?a\.?|agent|stockist)\s*(of|for)\b", re.I)

_DOC_WORDS = re.compile(r"\b(TAX\s*INVOICE|INVOICE|ORIGINAL|DUPLICATE|TRIPLICATE|CREDIT\s*NOTE)\b", re.I)
# Lines that are details about a party, not part of its address.
_NOT_ADDRESS = re.compile(
    r"(gs\s*t\s*in|gstin|pan\s*no|pan\s*:|d\.?l\.?\s*no|drug\s*lic|food\s*lic|fssai|cin|"
    r"e-?mail|phone|mob\b|tel\b|invoice|state\s*code|pos\s*:|c\.?\s*person|"
    r"\boriginal\b|\bduplicate\b|\btriplicate\b)",
    re.I,
)
# Where a party's name stops and its registration details begin.
_DETAIL_LABEL = re.compile(
    r"(gs\s*t\s*in|gstin|pan\s*no|pan\s*:|cin\s*[.:]|d\.?l\.?\s*no|drug\s*lic|food\s*lic|"
    r"fssai|e-?mail|phone|mob\b|tel\b|invoice\s*(?:no|date))",
    re.I,
)

# The statutory GSTIN shape: 2-digit state, 5-letter PAN prefix, 4 digits,
# letter, then two more. Matching the shape itself survives the many spellings
# of the label - "GSTIN No :", "GSTin:", "GS Tin :".
_GSTIN_SHAPE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]{2})\b")
_GSTIN = re.compile(r"\bG\s*S\s*T\s*(?:IN|No)?\.?\s*(?:no\.?)?\s*[:\-]?\s*([0-9A-Z]{15})\b", re.I)
_INVOICE_NO = re.compile(r"\binvoice\s*(?:no|num(?:ber)?|#)\.?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)", re.I)
_DATE_VALUE = r"([0-3]?\d[./\-][0-1]?\d[./\-]\d{2,4}|\d{4}-\d{2}-\d{2})"
_INVOICE_DATE = [
    re.compile(r"\binvoice\s*date\s*[:\-]?\s*" + _DATE_VALUE, re.I),
    re.compile(r"\binvoice\s*no.*?\bdt\.?\s*[:\-]?\s*" + _DATE_VALUE, re.I),
    re.compile(r"\bdated?\s*[:\-]\s*" + _DATE_VALUE, re.I),
    re.compile(r"\bdate\s*[:\-]\s*" + _DATE_VALUE, re.I),
]


def _buyer_boundary(words, page_height: float) -> Optional[float]:
    """The x where the buyer's column starts, if the page has one."""
    limit = page_height * 0.45
    boundary = None
    for i, word in enumerate(words):
        top = float(word["top"])
        if top > limit:
            continue
        # The window must START with the heading and stay on one line. A sliding
        # window that merely CONTAINS it matched across a line break - the
        # supplier's PAN followed by the buyer's "Bill to" on the next line -
        # and anchored the column boundary to the supplier's own text, cutting
        # its name in half.
        window = [w for w in words[i:i + 3] if abs(float(w["top"]) - top) <= 3.5]
        phrase = " ".join(str(w["text"]) for w in window)
        if _BUYER_HEADING.match(phrase):
            x0 = float(word["x0"])
            boundary = x0 if boundary is None else min(boundary, x0)
    # A heading hard against the left edge leaves nothing safe to cut.
    return boundary if boundary and boundary >= 40 else None


def supplier_region_lines(page) -> List[Tuple[float, str]]:
    """(font size, text) per line of the supplier's own block.

    Two signals separate the vendor from the customer, because an invoice prints
    both in the same header:

    * **Position.** The buyer's details sit in their own column. Flattened to
      text the columns interleave, and "the first line that looks like a
      company" then picks up the BUYER - JB Chemicals was being filed under its
      own customer's name, which would send every purchase to the wrong vendor.
      So everything right of the "Bill to / Ship to" heading is cut away.
    * **Size.** The supplier prints its own name larger than anything else in
      the header, which settles which of several company names is the vendor -
      Kanchan lists itself, the principal it acts for, and the customer, within
      a few lines of each other.
    """
    try:
        words = page.extract_words(keep_blank_chars=False, extra_attrs=["size"])
    except Exception:  # noqa: BLE001
        return []
    if not words:
        return []

    boundary = _buyer_boundary(words, float(page.height))
    if boundary is not None:
        words = [w for w in words if float(w["x1"]) <= boundary - 2]

    limit = float(page.height) * 0.30
    top = [w for w in words if float(w["top"]) <= limit]
    if not top:
        return []
    from app.services.ocr.pdf_table import _visual_lines

    out: List[Tuple[float, str]] = []
    for _, row in _visual_lines(top):
        size = max((float(w.get("size") or 0) for w in row), default=0.0)
        out.append((size, " ".join(str(w["text"]) for w in row)))
    return out


def _clean_name(line: str) -> str:
    """A party's name, with the document type and any registration details cut off."""
    name = _DOC_WORDS.split(line)[0]
    name = _DETAIL_LABEL.split(name)[0]
    return re.sub(r"\s+", " ", name).strip(" -|:,.")


def _supplier_details(lines: List[Tuple[float, str]]) -> tuple:
    """(name, address) from the supplier's block, biggest company name first."""
    candidates = [
        (size, i, _clean_name(text))
        for i, (size, text) in enumerate(lines)
        if _COMPANY.search(text) and not _BUYER_HEADING.search(text)
    ]
    candidates = [c for c in candidates if len(c[2]) >= 4]
    if not candidates:
        return None, None

    # A party named only as "C.A. of <principal>" is last resort; otherwise the
    # largest print wins, and ties go to whichever is printed first.
    size, index, name = max(
        candidates, key=lambda c: (0 if _AGENT_OF.match(c[2]) else 1, c[0], -c[1])
    )

    address_parts = []
    for _, text in lines[index + 1:index + 6]:
        if _NOT_ADDRESS.search(text) or _COMPANY.search(text):
            continue
        cleaned = re.sub(r"\s+", " ", text).strip()
        if cleaned:
            address_parts.append(cleaned)
    address = ", ".join(address_parts).strip(" ,") or None
    return name, address


def _extract_header_meta(
    text: str,
    supplier_lines: Optional[List[Tuple[float, str]]] = None,
) -> dict:
    """Supplier and invoice details from the page text.

    The supplier's own block is isolated first - by x position where the buyer's
    details sit in a neighbouring column, and by font size among the company
    names at the top. Without that, "the first line that looks like a company"
    picks up the CUSTOMER, which would file every purchase under the wrong
    vendor. Invoice number, date and total are read from the whole page, where
    they sit in their own column.
    """
    lines = supplier_lines or []
    own = "\n".join(t for _, t in lines)
    name, address = _supplier_details(lines)
    if name is None:
        name, address = _supplier_details([(0.0, ln) for ln in (text or "").splitlines()])

    gstin = (
        _GSTIN_SHAPE.search(own or "")
        or _GSTIN.search(own or "")
        or _GSTIN_SHAPE.search(text or "")
        or _GSTIN.search(text or "")
    )
    inv_no = _INVOICE_NO.search(text or "")
    inv_dt = next((m for m in (p.search(text or "") for p in _INVOICE_DATE) if m), None)
    return {
        "supplier": {
            "name": _f(name),
            "gstin": _f(gstin.group(1).upper() if gstin else None),
            "address": _f(address),
        },
        "invoice": {
            "invoice_no": _f(inv_no.group(1) if inv_no else None),
            "invoice_date": _f(inv_dt.group(1) if inv_dt else None),
            "total_amount": _f(_extract_total(text)),
        },
    }


# --------------------------------- row build ---------------------------------
def _strip_stray(text: str) -> str:
    """Drop tokens carrying no characters, e.g. the "()" that trails a product
    name into the batch column and turned "TMET6" into "() TMET6"."""
    tokens = [t for t in (text or "").split() if re.search(r"[A-Za-z0-9]", t)]
    return " ".join(tokens)


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
            item[field] = _f(_strip_stray(_clean(cell(field))))
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
                    meta = _extract_header_meta(
                        page_texts[page_no], supplier_region_lines(page)
                    )
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
