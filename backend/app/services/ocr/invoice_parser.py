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
    "free_quantity": (["freeqty", "schemeqty", "free", "fqty", "scheme", "bonus"], ["%", "value", "amount"]),
    "quantity": (["quantity", "qty", "nos", "units"], ["free", "fqty", "scheme", "bonus", "%"]),
    "mrp": (["mrp"], ["%"]),
    "ptr": (["pricetoretailer", "retailerprice", "ptr"], ["%"]),
    "pts": (["pricetostockist", "stockistprice", "pts"], ["%"]),
    # An explicit billed-rate column. Never matches MRP/PTR/PTS columns.
    "rate": (
        ["billrate", "netrate", "purchaserate", "salerate", "unitprice", "prate", "rate"],
        ["%", "mrp", "ptr", "pts"],
    ),
    "discount_percent": (["disc%", "discount%", "discount", "disc"], ["amt", "amount", "value", "rs"]),
    "amount": (["netamount", "netamt", "taxableamt", "taxablevalue", "amount", "value", "total"], ["%"]),
}

# Which price column feeds `rate` when the invoice has no explicit rate column,
# in order of preference. PTR is what this pharmacy actually pays.
_RATE_FALLBACKS = ("ptr", "pts")

_COPY_MARKERS = (
    ("original", re.compile(r"\boriginal\b", re.I)),
    ("duplicate", re.compile(r"\bduplicate\b", re.I)),
    ("triplicate", re.compile(r"\btriplicate\b", re.I)),
    ("quadruplicate", re.compile(r"\bquadruplicate\b", re.I)),
    ("office", re.compile(r"\boffice\s+copy\b", re.I)),
)

_SUMMARY_ROW = re.compile(r"\b(total|grand|net\s*amount|sub\s*total|subtotal|carried|c/f|b/f)\b", re.I)


def _norm(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"[\s\.\n_\-]+", "", str(cell)).lower()


def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _find_header_row(table) -> Optional[int]:
    """Row index of the column-header row (has batch + a qty/product marker)."""
    for i, row in enumerate(table):
        norms = [_norm(c) for c in row]
        joined = " ".join(norms)
        if "batch" in joined and ("qty" in joined or "productname" in joined or "product" in joined or "item" in joined):
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
    """Indices of GST-percentage columns (CGST%/SGST%/IGST%/GST%) to sum."""
    out = []
    for idx, c in enumerate(header_row):
        h = _norm(c)
        if ("gst" in h or "igst" in h) and "%" in str(c) and "amt" not in h:
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
    r"grand\s*total",
    r"bill\s*amount",
    r"invoice\s*(?:total|amount|value)",
    r"net\s*amount",
    r"total\s*amount",
    r"amount\s*payable",
]
_MONEY = r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2}|[\d,]{2,})"


def _extract_total(text: str) -> Optional[str]:
    """The invoice's printed total, preferring the most specific wording."""
    for pattern in _TOTAL_PATTERNS:
        matches = re.findall(pattern + r"\s*[:\-]?\s*" + _MONEY, text or "", re.I)
        if matches:
            # The last occurrence is the foot of the bill.
            return matches[-1].replace(",", "")
    return None


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
def _build_item(row, cols: dict, header_row, gst_cols: List[int]) -> Optional[dict]:
    def cell(field):
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    desc = _clean(cell("description"))
    if not desc or len(desc) < 2:
        return None
    if _SUMMARY_ROW.search(desc):
        return None

    item = {"description": _f(desc)}
    for field in ("batch_no", "expiry", "hsn", "pack"):
        if cols.get(field) is not None:
            item[field] = _f(_clean(cell(field)))
    for field in ("quantity", "free_quantity", "mrp", "ptr", "pts", "rate", "discount_percent", "amount"):
        if cols.get(field) is not None:
            item[field] = _f(_num(cell(field)))

    # `rate` is the connector contract and must always be populated. When the
    # invoice has no explicit rate column, fall back to PTR then PTS - and say so.
    rate_source = _header_text(header_row, cols.get("rate")) if cols.get("rate") is not None else ""
    if not (item.get("rate") or {}).get("value"):
        for fallback in _RATE_FALLBACKS:
            candidate = (item.get(fallback) or {}).get("value")
            if candidate:
                item["rate"] = _f(candidate)
                rate_source = _header_text(header_row, cols.get(fallback)) or fallback.upper()
                break
    if rate_source:
        # confidence None: this is a label, not an extracted measurement, and it
        # must not dilute the document's overall confidence score.
        item["rate_source"] = _f(rate_source, None)

    # GST% = sum of CGST%+SGST% (or IGST%) columns.
    gst_vals = []
    for gi in gst_cols:
        if gi < len(row):
            n = _num(row[gi])
            if n:
                gst_vals.append(float(n))
    if gst_vals:
        item["gst_percent"] = _f(str(round(sum(gst_vals), 2)))

    # Only keep rows that have at least a quantity or a batch.
    if item.get("quantity", {}).get("value") or item.get("batch_no", {}).get("value"):
        return item
    return None


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
    meta = None
    copies = 1
    stated_count = None
    full_text = ""

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_texts = [(p.extract_text() or "") for p in pdf.pages]

            # Read only the first printed copy of the invoice.
            groups = _copy_groups([_copy_label(t) for t in page_texts])
            copies = len(groups)
            wanted = set(groups[0]) if copies > 1 else set(range(len(pdf.pages)))
            if copies > 1:
                log.info("invoice_parser: %d printed copies detected; reading pages %s", copies, sorted(wanted))

            full_text = "\n".join(page_texts[i] for i in sorted(wanted))
            stated_count = _extract_item_count(full_text)

            for page_no, page in enumerate(pdf.pages):
                if page_no not in wanted:
                    continue
                if meta is None:
                    meta = _extract_header_meta(page_texts[page_no])
                for table in page.extract_tables() or []:
                    hi = _find_header_row(table)
                    if hi is None:
                        continue
                    header_row = table[hi]
                    cols = _map_columns(header_row)
                    if "description" not in cols or ("quantity" not in cols and "batch_no" not in cols):
                        continue  # not a line-item table we understand
                    gst_cols = _gst_columns(header_row)
                    for row in table[hi + 1:]:
                        item = _build_item(row, cols, header_row, gst_cols)
                        if item:
                            line_items.append(item)
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
    fields["_hints"] = {"copies_detected": copies, "stated_item_count": stated_count}
    return fields
