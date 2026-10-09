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
from app.services.ocr.invoice_header import (
    extract_references,
    extract_totals,
    is_interstate,
    local_tax_head,
    normalised_party_key,
    party_details,
    party_regions,
    sum_line_totals,
    supplier_extras,
    supplier_gstin_for_pan,
    _has_doubled_glyphs,
)
from app.services.ocr.invoice_checks import SUMMED_CONFIDENCE, line_arithmetic_holds
from app.services.ocr.pdf_table import WORD_TOLERANCE, extract_word_tables, page_layout, without_watermark

log = logging.getLogger(__name__)

# field -> (keywords in PREFERENCE order, exclude keywords). Header cells are
# normalized to lowercase with spaces/newlines/dots removed before matching.
# Order of this dict matters: earlier fields claim their column first.
_COLS: Dict[str, Tuple[List[str], List[str]]] = {
    "description": (
        ["productname", "itemname", "description", "proddesc", "particulars", "product",
         "desc", "item", "goods", "medicine"],
        ["hsn", "code", "qty", "rate", "amount"],
    ),
    "hsn": (["hsncode", "hsn"], []),
    "product_code": (["productcode", "prdcode", "itemcode", "prodcode", "code"], ["hsn"]),
    "manufacturer": (["mfgname", "manufacturername", "manufacturer", "mfgco", "company"], ["code", "date", "cd"]),
    # Excludes "exp": Bharat heads one column "Exp.date / Mfg.date", and the
    # expiry is the field a pharmacist actually needs, so it claims that column.
    # Excludes "batch" too: Menarini stacks "Batch No" over "Mfg.Date" in ONE
    # column, where the record line carries the BATCH and the wrapped line below
    # carries the date. Letting mfg_date claim it left every batch number blank.
    "mfg_date": (["mfgdate", "mfgdt", "manufacturingdate", "mfd"], ["exp", "batch"]),
    # Not "Billed Qty / UoM" (Piramal): the quantity's column, its unit beside it.
    "uom": (["uom", "unitofmeasure"], ["qty"]),
    "batch_no": (["batchno", "batch", "lotno", "lot", "bno", "btno"], ["bill", "sr", "inv"]),
    # No "mfg" exclusion: a Mfg-only column never contains "exp", while Bharat
    # heads one column "Exp.date / Mfg.date" - excluding it lost every expiry.
    "expiry": (["expdate", "expiry", "exp"], []),
    "pack": (["packing", "pack"], []),
    # Free/scheme quantity is claimed BEFORE quantity so a "F.QTY" column can
    # never be mistaken for the billed quantity.
    # "Fr. Qty." (Kanchan) normalises to "frqty", which matched nothing before.
    # "D.Qty" (IPCA): the deal quantity - goods given free on the scheme.
    # Not "Bonus Disc." (Medley): a discount rate, not free goods.
    "free_quantity": (["freeqty", "frqty", "schemeqty", "dealqty", "free", "fqty", "scheme", "bonus"],
                      ["%", "value", "amount", "disc"]),
    # "Billed Qty." before a bare "qty": Cipla prints "Loose Qty" (mostly empty)
    # first, and its billed quantity is the one the stock and the amount follow.
    "quantity": (["quantity", "billedqty", "billqty", "qty", "nos", "units"], ["free", "fqty", "scheme", "bonus", "%"]),
    "mrp": (["mrp"], ["%"]),
    # Lupin heads "Trade Price/Unit | TP/ PTR Value": the price, then the
    # line's value at it - never the PTR.
    "ptr": (["pricetoretailer", "retailerprice", "ptr", "tradeprice/unit"], ["%", "value", "amount"]),
    "pts": (["pricetostockist", "stockistprice", "distributorprice", "distprice", "pts"], ["%"]),
    # An explicit billed-rate column. "NIR" (Net Invoice Rate) is tried first
    # because Bharat prints it beside a bare CGST "Rate" column that would
    # otherwise win. GST rate columns are excluded outright.
    "rate": (
        ["nir", "netinvoicerate", "billrate", "netrate", "purchaserate", "salerate",
         "unitprice", "prate", "rate"],
        # ...and never a "Discount Rate" (Cipla Health): the discount's percentage.
        ["%", "mrp", "ptr", "pts", "cgst", "sgst", "igst", "gst", "tax", "disc"],
    ),
    # Bare "DIS" (Marg): the line's discount rate.
    "discount_percent": (["disc%", "discount%", "discount", "disc", "dis"], ["amt", "amount", "value", "rs"]),
    # "Spl.Dis. Amount" (Ajanta) - "Dis." as well as "Disc.".
    "discount_amount": (["discamt", "discountamt", "discountamount", "discamount", "disamount", "disamt"], ["%"]),
    "cd_percent": (["cd%", "cashdisc%", "cashdiscount%"], ["amt", "amount", "rs"]),
    "cd_amount": (["cdamt", "cdamount", "cashdiscamt", "cashdiscountamt"], ["%"]),
    "wp_percent": (["wp%", "wpdisc%"], ["amt", "amount"]),
    "wp_amount": (["wpamt", "wpamount", "wpvalue"], ["%"]),
    "scheme": (["schemedesc", "schemedescription", "schemename", "scheme"], ["%", "qty", "amt", "value"]),
    "scheme_value": (["schemevalue", "schemeamt", "schemeamount"], ["%"]),
    # "Sale Value" (Overseas) is the line BEFORE its discount - a gross figure.
    # Left to `amount`, it overstated every line by the discount.
    # ...and Dr. Reddy's "Total Amount (base price)", before its discount and tax.
    "gross_amount": (["grossamount", "grossamt", "grossvalue", "grosstotal", "totalbasic", "gross",
                      "salevalue", "baseprice", "basevalue", "ptrvalue"], ["%"]),
    # "Total Amount" (Menarini) is the line INCLUDING tax - a net amount, not
    # the taxable one. Excluded from anything taxable so the column the bill
    # actually sums still goes to `amount`.
    "net_amount": (["netamount", "netamt", "netvalue", "totalamount"], ["%", "taxable"]),
    "utgst_percent": (["utgst%", "utgstrate"], ["amt", "amount"]),
    "utgst_amount": (["utgstamt", "utgstamount"], ["%", "rate"]),
    "scheme_percent": (["sch%", "scheme%", "schemediscount"], ["amt", "qty"]),
    "total_quantity": (["totalqty", "totqty", "netqty"], ["%", "free"]),
    # Tax columns are captured per head so the amounts reach the accounts, and so
    # a zero-rated line can be told apart from an unreadable one.
    "cgst_percent": (["cgst%", "cgstrate"], ["amt", "amount", "rs/"]),
    "cgst_amount": (["cgstamt", "cgstamount"], ["%", "rate"]),
    # Not "SGST % Rs. / %" (Piramal): the tax and its rate in one cell, split
    # like every merged tax cell (_gst_columns).
    "sgst_percent": (["sgst%", "sgstrate", "utgst%"], ["amt", "amount", "rs/"]),
    "sgst_amount": (["sgstamt", "sgstamount", "utgstamt"], ["%", "rate"]),
    "igst_percent": (["igst%", "igstrate"], ["amt", "amount", "rs/"]),
    "igst_amount": (["igstamt", "igstamount"], ["%", "rate"]),
    # The net/taxable column is what the bill actually sums; a plain "Amount"
    # column (Kanchan) is the figure BEFORE the line discount.
    # The taxable value - what GST is charged on, and what the lines are summed
    # against. `net_amount` and `gross_amount` are separate columns above, so
    # they are no longer allowed to stand in for this one.
    "amount": (
        # Bare "TAXABLE" (the Marg-style ERP layout: "AMOUNT | DISC | TAXABLE")
        # names the taxable value; its "AMOUNT" is the figure before discount.
        ["taxableamount", "taxablevalue", "taxableamt", "taxable", "assessablevalue", "assessable",
         "amount", "value", "total"],
        # A tax column is not the line's own amount, and neither is a discount
        # column. Overseas prints "Disc Value" and "CGST Amount" beside its
        # taxable "Trans. Value"; without these, the sum of the invoice was the
        # sum of its discounts.
        ["%", "gross", "net", "disc", "disamount", "disamt", "cgst", "sgst", "igst", "utgst"],
    ),
}

_COPY_MARKERS = (
    # East India prints "First Copy" / "Second Copy : Transporter" / ...: all
    # three copies were read as one bill, every line tripled.
    ("first", re.compile(r"\bfirst\s+copy\b", re.I)),
    ("second", re.compile(r"\bsecond\s+copy\b", re.I)),
    ("third", re.compile(r"\bthird\s+copy\b", re.I)),
    ("fourth", re.compile(r"\bfourth\s+copy\b", re.I)),
    ("original", re.compile(r"\boriginal\b", re.I)),
    ("duplicate", re.compile(r"\bduplicate\b", re.I)),
    ("triplicate", re.compile(r"\btriplicate\b", re.I)),
    ("quadruplicate", re.compile(r"\bquadruplicate\b", re.I)),
    ("office", re.compile(r"\boffice\s+copy\b", re.I)),
)

# India's highest GST slab. Anything above it is a misread cell, not a rate.
_MAX_GST_PERCENT = 28.0

# The rates GST is actually charged at - whole, and split into CGST + SGST
# halves - plus the small special rates. A figure that is not one of these is
# tax, not a rate. "Below 28" was not enough: Menarini's single-figure CGST
# cells carry 27.77 and 11.89 rupees, which read as 27.77% and 11.89% and left
# both lines with no tax, so the bill's CGST total came out 39.66 short.
_GST_RATES = (0.0, 0.1, 0.125, 0.25, 0.5, 1.0, 1.5, 2.5, 3.0, 5.0, 6.0, 7.5, 9.0,
              12.0, 14.0, 18.0, 28.0)


# Confidence for a figure computed from others on the bill rather than read off
# it. Below the review threshold's "certain", so it is marked for a glance.
_DERIVED_CONFIDENCE = 0.85
# A bill total summed from its own lines. Shares the derived figure's standing,
# and is what invoice_checks._derived recognises.
_SUMMED_CONFIDENCE = SUMMED_CONFIDENCE


def _is_gst_rate(n: float) -> bool:
    return any(abs(n - r) < 0.001 for r in _GST_RATES)

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
# "B.No.", "Bt.No", "Batch#" and "Lot No" are all common headings for the same
# column; demanding the literal word "batch" rejected whole invoices.
_BATCH_MARKERS = ("batch", "lotno", "lot", "bno", "btno", "bat")
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
                # "Taxable Amt after Disc." (Zuventus) IS the taxable amount;
                # the discount it mentions is already taken off.
                excluded = any(e in h for e in excludes) and not (
                    field == "amount" and keyword.startswith("taxable")) and not (
                    # "PRODUCT NAME (HSN CODE)" (Mankind) is the product's
                    # name, whatever else the heading mentions.
                    field == "description" and h.startswith(keyword) and len(keyword) >= 7)
                if keyword in h and not excluded:
                    hit = idx
                    break
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break
    # "D.Qty" (IPCA) is the deal quantity - free goods - and its bare "Sale"
    # column the quantity sold. Matched whole: "Billed Qty" also ends in "dqty".
    qty_at = mapping.get("quantity")
    if qty_at is not None and norms[qty_at] in ("dqty", "dealqty"):
        sale = next((i for i, h in enumerate(norms) if h == "sale" and i not in taken), None)
        if sale is not None:
            if "free_quantity" not in mapping:
                mapping["free_quantity"] = qty_at
            else:
                taken.discard(qty_at)
            mapping["quantity"] = sale
            taken.add(sale)
    # One column for both, quantity first: "Qty Sale+Free" over "500+100" (East
    # India), "Qty/ FreeQty" (Linux). Claimed by "free", the billed quantity was
    # read as free goods and the quantity left blank. It is the quantity
    # column; "+" or "/" in the cell splits off the free part (_build_item).
    free_at = mapping.get("free_quantity")
    if "quantity" not in mapping and free_at is not None:
        head = norms[free_at]
        at_qty = min((head.find(w) for w in ("qty", "quantity") if w in head), default=-1)
        if 0 <= at_qty < head.find("free"):
            mapping["quantity"] = mapping.pop("free_quantity")
    # A "Net Value" printed BEFORE the tax columns is what the tax is charged
    # on: Prabodhan heads "Value | Trade Discount | ... | Net Value | CGST |
    # SGST | IGST | Total" - its Value is before the discount, its Total after
    # the tax.
    # Raptakos prints "Net Value | Additional Discount | Taxable Value | SGST
    # ... | Total": there the taxable value is its own column, and the net
    # with the tax is the Total.
    net_at, amount_at = mapping.get("net_amount"), mapping.get("amount")
    if net_at is not None and amount_at is not None and norms[net_at].startswith("net") \
            and any("gst" in h for h in norms[net_at + 1:]) \
            and (amount_at > net_at or norms[amount_at] in ("value", "amount", "amt")):
        total_at = next((i for i in range(len(norms) - 1, net_at, -1)
                         if norms[i] in ("total", "totalvalue", "totalamount") and i not in taken), None)
        if amount_at < net_at:
            if "gross_amount" not in mapping:
                mapping["gross_amount"] = amount_at
            else:
                taken.discard(amount_at)
            mapping["amount"] = net_at
        else:
            taken.discard(net_at)
        mapping.pop("net_amount")
        if total_at is not None:
            mapping["net_amount"] = total_at
            taken.add(total_at)
    # A plain "Amount" printed AFTER the tax columns, with a bare "Value" before
    # them: the tax is charged on the Value. Ajanta heads "PTS | Spl.Dis.
    # Amount | Value | CGST % Tax Amt | SGST % Tax Amt | Amount" - its last
    # column is the line before the special discount, and taken as the taxable
    # value it put tax on a line the bill had discounted to nothing.
    amount_at = mapping.get("amount")
    if amount_at is not None and norms[amount_at] in ("amount", "amt"):
        value_at = next((i for i, h in enumerate(norms[:amount_at])
                         if h == "value" and i not in taken), None)
        # ...the Value before EVERY tax column: NSV's "SGST | VALUE | CGST |
        # VALUE | D.AMT | AMOUNT" prints each head's tax as its "VALUE".
        if value_at is not None and any("gst" in h for h in norms[value_at + 1:amount_at]) \
                and not any("gst" in h for h in norms[:value_at]):
            taken.discard(amount_at)
            mapping["amount"] = value_at
            taken.add(value_at)
    # When the taxable column is headed as such, a plain "AMOUNT" beside it is
    # the line before its discount - the gross.
    amount_at = mapping.get("amount")
    if amount_at is not None and ("taxable" in norms[amount_at] or "assessable" in norms[amount_at]) \
            and "gross_amount" not in mapping:
        # ...one printed BEFORE it: Wanbury's "Amt" after its taxable column is
        # a tax amount, not the line before discount.
        plain = next((i for i, h in enumerate(norms)
                      if h in ("amount", "amt", "value") and i not in taken and i < amount_at), None)
        if plain is not None:
            mapping["gross_amount"] = plain
            taken.add(plain)
    # A bare "Value" just right of a bare tax head is that head's tax: NSV
    # heads "SGST | VALUE | CGST | VALUE" over "6% | 72.90 | 6% | 72.90".
    for i in range(1, len(norms)):
        if i in taken or norms[i] not in ("value", "amt", "amount"):
            continue
        before = norms[i - 1]
        head = next((h for h in ("cgst", "sgst", "igst", "utgst") if before.startswith(h)), None)
        # Never after a RATE heading: Hetero's "CGST SGST % | AMOUNT" ends on
        # the line's net.
        if head and f"{head}_amount" not in mapping \
                and not any(w in before for w in ("amt", "amount", "value", "%", "rate")):
            mapping[f"{head}_amount"] = i
            taken.add(i)
    # A lone "%" column just left of a tax head's amount is that head's rate:
    # "% | CGST AMOUNT" printed as a two-line heading that split apart.
    for i, h in enumerate(norms[:-1]):
        if h not in ("%", "rate", "per", "per%") or i in taken:
            continue
        nxt = norms[i + 1]
        # ...and just left of a discount amount, the discount's rate: Kanchan
        # prints "% | Disc. Amt." and 2.00 under the "%".
        if nxt.startswith(("discamt", "discountamt", "discamount")) and "discount_percent" not in mapping:
            mapping["discount_percent"] = i
            taken.add(i)
            continue
        for head in ("cgst", "sgst", "igst", "utgst"):
            if nxt.startswith(head) and f"{head}_percent" not in mapping:
                mapping[f"{head}_percent"] = i
                taken.add(i)
                break
    return mapping


def _header_text(header_row, idx: Optional[int]) -> str:
    if idx is None or idx >= len(header_row):
        return ""
    return _clean(header_row[idx])


# A rate column headed by "tax" alone, naming no head.
# A bare "GST" (IPCA prints "GST | IGST | CGST | SGST" over "12 | 0.00 | 146.32 |
# 146.32") is the whole rate too - taken as CGST, it was paired up to 18%.
_BARE_TAX_RATE = ("tax%", "taxrate", "tax%rate", "gst", "gstrate")
# "6.00/6.00" under it: the CGST and SGST rates of an intra-state line.
_RATE_PAIR = re.compile(r"^\s*(\d{1,2}(?:\.\d+)?)\s*/\s*(\d{1,2}(?:\.\d+)?)\s*$")


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
        if h in _BARE_TAX_RATE:
            out.append(idx)   # "Tax%" (Troikaa): the line's whole GST rate
            continue
        if "gst" not in h:
            continue
        # A heading that only MENTIONS GST names no tax column: Torrent heads
        # its description "(... MRP ... as per Pre GST reform 2.0 rates)", its
        # prices "Per Pack (Prices post GST reforms 2.0)", and sums its heads
        # under "Total GST" - each read as a tax head's rate or amount.
        if not any(n in h for n in ("cgst", "sgst", "igst", "utgst")) and (
                len(h) > 12 or h.startswith("total")):
            continue
        if "%" in str(c) or "rate" in h:
            out.append(idx)
        elif not any(w in h for w in ("amt", "amount", "value")):
            # A BARE tax heading - V L Enterprises heads its pair just "CGST"
            # and prints "9.00 655.67" beneath it, the rate and the tax in one
            # cell. The heading cannot say which, so it goes through the same
            # arithmetic as every other merged tax cell: a figure at or below
            # the maximum GST rate is the rate, the next one is the amount.
            # Mapping a bare heading straight to the amount would have posted
            # 9% as nine rupees of tax on every line of that invoice.
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
    # "Round Off (+/-): 0.09 ... NET RECEIVABLE Rs. 724829.25" (Torrent), after
    # a "Grand Total: 724829.16" that is before the round-off.
    r"net\s*receivable",
    r"net\s*to\s*pay",
    # "TOTALPAY 12,799.00" (Hindustan Capsule, set without spaces).
    r"total\s*pay(?:able)?\b",
    # "Invoice Amt 7410.43 ... TOPAY 7410.00" (Meher): the rounded payable.
    r"(?<![a-z])topay\b",
    r"grand\s*total",
    r"bill\s*amount",
    r"invoice\s*(?:total|amount|amt|value)",
    r"total\s*invoice",
    r"net\s*amount",
    r"total\s*amount",
    r"amount\s*payable",
]
# Deliberately no bare "total" pattern. Bharat labels its taxable column sum
# "Total 371458.70" and prints its grand total ONLY in words; a last-resort bare
# label read the column sum as the amount due - 41,907 short of the bill. Where
# an invoice labels its total no better than that, the amount in words is the
# figure we take, and reconcile_invoice compares the two for contradictions.
_MONEY = r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2}|[\d,]{2,})"

# What a supplier may print between the label and the figure. Menarini heads its
# grand total "Net Payable Amt : 54,058.00" - with "Amt" in the way, the label
# did not match and the bill's TAXABLE total ("Net Amount 46870.98", 7,187 less)
# was read as the amount due. Only these few words are allowed through, so the
# pattern cannot skip across a label to a neighbouring column's figure.
_TOTAL_TAIL = r"(?:\s*(?:amt|amount|value|payable|due|rs|inr)\.?){0,2}\s*[:\-]?\s*"


# Aristo's only figure for the bill is "Gross Amount 6,577.10" (lines,
# discount and tax) before it sets off a credit note to "Amount Payable:
# -90.00". Only when the WHOLE bill names no other total: the first page of
# a two-page bill (Stedman) prints its "Gross Amount" before any tax.
_LAST_RESORT_TOTAL = re.compile(r"gross\s*amount" + _TOTAL_TAIL + _MONEY, re.I)


def _whole_bill_total(text: str) -> Optional[str]:
    total = _extract_total(text)
    if total is None:
        found = [m for m in _LAST_RESORT_TOTAL.findall(text or "") if re.search(r"[1-9]", m)]
        total = found[-1].replace(",", "") if found else None
    return total


def _extract_total(text: str) -> Optional[str]:
    """The invoice's printed total, preferring the most specific wording.

    Falls back to the amount-in-words line, which for some suppliers (Bharat
    Serums) is the only place the grand total appears at all.
    """
    # A labelled figure the bill also spells out is its total, whichever label
    # it sits under: Ajanta prints "Grand Total 59,443.21", then "Less Special
    # Disc. 1,806.12", then "Invoice Amount 57,637.00" - and the words say
    # Fifty Seven Thousand Six Hundred Thirty Seven.
    words = total_from_words(text)
    if words is not None:
        # Exactly first: the words name whole rupees, as the rounded total does.
        for tolerance in (0.005, 1.0):
            for pattern in _TOTAL_PATTERNS:
                for m in re.findall(pattern + _TOTAL_TAIL + _MONEY, text or "", re.I):
                    if re.search(r"[1-9]", m) and abs(float(m.replace(",", "")) - float(words)) < tolerance:
                        return m.replace(",", "")
            # ...or later in a totals ROW under that label: Micropark prints
            # "Grand Total 10,059.60 905.37 905.37 0.00 11,870.00" - taxable,
            # the tax heads, then the total its words spell out.
            for pattern in _TOTAL_PATTERNS:
                for row in re.findall(pattern + r"[^\n]*", text or "", re.I):
                    for m in re.findall(r"(?<![\d,.])(\d[\d,]*\.\d{2})(?![\d.])", row):
                        if abs(float(m.replace(",", "")) - float(words)) < tolerance:
                            return m.replace(",", "")
    for pattern in _TOTAL_PATTERNS:
        matches = re.findall(pattern + _TOTAL_TAIL + _MONEY, text or "", re.I)
        # A zero is no bill's total: Bayer heads a summary row "... INVOICE
        # AMOUNT" and the row beneath starts with its 0.00 cash discount. Its
        # total is the "NET AMOUNT PAYABLE" further down.
        matches = [m for m in matches if re.search(r"[1-9]", m)]
        if len({m.replace(",", "") for m in matches}) > 1:
            # Several figures under one label: the one the bill also spells
            # out is its total. IPCA prints "Net Amount : 236867.00", the words
            # for it, then "Net Amount Payable 236655.00" after deducting TDS -
            # tax the BUYER withholds, not a lower price.
            words = total_from_words(text)
            if words is not None:
                spelled = [m for m in matches if abs(float(m.replace(",", "")) - float(words)) < 1.0]
                if spelled:
                    return spelled[-1].replace(",", "")
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

# Several of these are STEMS - AGENC(IES), LABORATOR(IES), ENTERPRISE(S) - so
# the pattern must not demand a word boundary after them. With one, "V L
# ENTERPRISES" was not recognised as a company at all and the supplier was read
# as "Due Date : 12/09/2025 Division : PHARMA".
_COMPANY = re.compile(
    r"\b(LIMITED|LTD|PVT|PRIVATE|DISTRIBUTOR|PHARMA|HEALTHCARE|ENTERPRISE|AGENC|LABORATOR|"
    r"INDUSTRIES|REMEDIES|BIOTECH|LIFESCIENCE|LOGISTICS|MARKETING|TRADERS)\w*",
    re.I,
)
# "C.A. of X" / "C&F of X" names the principal a carrying agent acts for. The
# invoicing party - the one whose GSTIN is on the bill, and the one the pharmacy
# actually buys from - is the agent itself, printed separately.
# ...and "Super Stockiest of Tablets (India) Limited" (V L Enterprises, spelling
# theirs) names the principal a stockist buys from, not the stockist itself.
_AGENT_OF = re.compile(
    r"^\s*(?:super\s*)?(c\.?\s*a\.?|c\s*&\s*f|c\.?f\.?a\.?|agent|stocki?e?st|distributor)"
    r"\s*(of|for)\b",
    re.I,
)

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
_GSTIN_SHAPE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][A-Z0-9])\b")
_GSTIN = re.compile(r"\bG\s*S\s*T\s*(?:IN|No)?\.?\s*(?:no\.?)?\s*[:\-]?\s*([0-9A-Z]{15})\b", re.I)
_INVOICE_NO = re.compile(r"\binvoice\s*(?:no|num(?:ber)?|#)\.?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)", re.I)
# How suppliers label the number, most specific first. Not \b-anchored on the
# left: Aaraf's licence runs straight into "...21B-585861Invoice No. : A000298".
_NO_VALUE = r"[ \t]*[:\-#]?[ \t]*([A-Za-z0-9][A-Za-z0-9\-/]*[A-Za-z0-9])"
_INVOICE_NO_LABELS = [
    re.compile(r"(?<![A-Za-z])(?:tax\s*|gst\s*|sales\s*)?inv(?:oice)?\.?[ \t]*(?:no|num(?:ber)?|#)\.?"
               + _NO_VALUE, re.I),
    # Never the e-way bill's number: IPCA prints "Eway Bill NO: 271987615581".
    re.compile(r"(?<![A-Za-z])(?<!way )(?<!way-)(?<!way)bill[ \t]*(?:no|num(?:ber)?)\.?" + _NO_VALUE, re.I),
    re.compile(r"(?<![A-Za-z])invoice[ \t]*:" + _NO_VALUE, re.I),
    # "Doc. No/Date : 5403626760/25.08.2025" (Zuventus): number and date as one.
    re.compile(r"(?<![A-Za-z])(?:invoice|inv|bill|doc(?:ument)?)\.?[ \t]*no\.?[ \t]*/[ \t]*(?:date|dt)\.?"
               r"[ \t]*[:\-]?[ \t]*([A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])(?=[ \t]*/)", re.I),
    re.compile(r"(?<![A-Za-z])doc(?:ument)?\.?[ \t]*no\.?" + _NO_VALUE, re.I),
    # A credit note's own number: "CREDITNOTENO.C000084" (Hindustan Capsule).
    re.compile(r"(?<![A-Za-z])credit[ \t]*note[ \t]*no\.?" + _NO_VALUE, re.I),
    # ...a return's: "S.Return No. : CN00001" (AANAV), "CN No : MUMG1CN2600117"
    # (Wanbury) - only where no invoice number is printed at all.
    re.compile(r"(?<![A-Za-z])(?:s(?:ales)?\.?[ \t]*return|cn)[ \t]*no\.?" + _NO_VALUE, re.I),
]
# A real calendar date, one separator throughout - never Mediv's invoice number
# "25-26/0630" (a financial year and a serial), which has the same digits.
_LOOKS_LIKE_DATE = re.compile(
    r"^(?:(?:0?[1-9]|[12]\d|3[01])([/\-.])(?:0?[1-9]|1[0-2])\1(?:\d{2}|\d{4})"
    r"|\d{4}([/\-.])(?:0?[1-9]|1[0-2])\2(?:0?[1-9]|[12]\d|3[01]))$")


def find_invoice_no(text: str) -> Optional[str]:
    """The invoice's own number, under whichever label the supplier uses.

    "Invoice No.", "GST Inv. No.", "Bill No.", a bare "Invoice:", and - for a
    credit note - "Doc No". A candidate must carry a digit and must not be a
    date, so a label printed with its value elsewhere ("Invoice No. Date")
    does not hand over the wrong field.
    """
    for pattern in _INVOICE_NO_LABELS:
        for m in pattern.finditer(text or ""):
            value = m.group(1)
            if not any(ch.isdigit() for ch in value) or _LOOKS_LIKE_DATE.match(value) or len(value) > 30:
                continue
            return value
    return None
# A date as Indian invoices print it. The third form is the one that matters
# here: Menarini dates every field "22-Sep-2025", and with only numeric months
# accepted its invoice date, due date, LR date and PO date were ALL blank while
# every one of them was printed on the bill.
_MONTH_NAME = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_VALUE = (
    r"([0-3]?\d[./\-][0-1]?\d[./\-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|[0-3]?\d[\s./\-](?:" + _MONTH_NAME + r")[a-z]*[\s./\-]\d{2,4})"
)
_INVOICE_DATE = [
    # "INVOICEDATE:-05/09/2025" (Hindustan Capsule): a colon and a dash.
    re.compile(r"\binvoice\s*date\s*[:\-]{0,2}\s*" + _DATE_VALUE, re.I),
    re.compile(r"\binvoice\s*no.*?\bdt\.?\s*[:\-]?\s*" + _DATE_VALUE, re.I),
    # "Doc. No/Date : 5403626760/25.08.2025" - the date after the number.
    re.compile(r"(?<![A-Za-z])(?:invoice|inv|bill|doc(?:ument)?)\.?[ \t]*no\.?[ \t]*/[ \t]*(?:date|dt)\.?"
               r"[ \t]*[:\-]?[ \t]*[A-Za-z0-9\-]+[ \t]*/[ \t]*" + _DATE_VALUE, re.I),
    re.compile(r"\bdated?\s*[:\-]\s*" + _DATE_VALUE, re.I),
    re.compile(r"\bdate\s*[:\-]\s*" + _DATE_VALUE, re.I),
]


# A bare "Date :" that belongs to some other reference on the line: "Cust
# Reference Date :08/08/2025" (Ajanta) above its own "DATE : 19/09/2025".
_OTHER_DATE = re.compile(
    r"(?:ref(?:erence)?|order|po|lr|l\.r|due|cust(?:omer)?|challan|cheque|chq|ack|e-?way\s*bill|"
    r"delivery|dispatch|mfg|exp(?:iry)?|dl|licen[cs]e|claim)\.?\s*(?:no\.?\s*)?$",
    re.I,
)


def _invoice_date_match(text: Optional[str]):
    """The invoice's own date: a labelled invoice date first, else the first
    bare "Date" that is not another reference's date - and when every bare
    date is another's (an e-way bill or IRN acknowledgement dated the same
    day is all some bills print), the first of them, as before."""
    first_other = None
    for n, pattern in enumerate(_INVOICE_DATE):
        for m in pattern.finditer(text or ""):
            if n >= 3:
                before = (text or "")[max(0, m.start() - 30):m.start()].split("\n")[-1]
                if _OTHER_DATE.search(before.strip()):
                    first_other = first_other or m
                    continue
            return m
    return first_other


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
        words = page.extract_words(
            keep_blank_chars=False, extra_attrs=["size"], x_tolerance=WORD_TOLERANCE
        )
    except Exception:  # noqa: BLE001
        return []
    if not words:
        return []

    boundary = _buyer_boundary(words, float(page.height))
    if boundary is not None:
        words = [w for w in words if float(w["x1"]) <= boundary - 2]

    # The header takes the same room on a landscape page as on a portrait one,
    # which is more of its height: East India's PAN and e-mail sit at 33-38%.
    limit = max(float(page.height), float(page.width)) * 0.30
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


# "Cust.Code & Name: 10023867-EASTERN AGENCIES HEALTHCARE PVT. LTD." - the
# buyer's name with the supplier's account code in front, ending where the next
# label on the line begins.
_CUSTOMER_NAME = re.compile(
    r"\bcust(?:omer)?\.?\s*(?:code\s*&\s*)?name\s*:\s*(?:\d+\s*-\s*)?"
    r"([A-Za-z][^\n]*?)(?=\s+(?:ship(?:p?ed)?\s*to|billed\s*to|address|gstin|pan)\b|\s*$)",
    re.I | re.M,
)


_EMAIL_ANYWHERE = re.compile(r"([\w.+\-]+@[\w\-]+\.[A-Za-z][\w.\-]*[A-Za-z])")


def _complete_legal_suffix(name: Optional[str], page_text: str) -> Optional[str]:
    """Finish a supplier name cut off after "PRIVATE" or "PVT".

    The supplier's block is cut at the x where the buyer's column begins, and a
    large title can run past that line: Overseas's "OVERSEAS HEALTH CARE
    PRIVATE LIMITED" came back without its "LIMITED". The full line is still in
    the page text, so the missing word is read from there - only ever the legal
    suffix, nothing else from the buyer's side.
    """
    if not name or not re.search(r"\b(private|pvt\.?)\s*$", name, re.I):
        return name
    m = re.search(re.escape(name) + r"\s+(limited|ltd\.?)\b", page_text or "", re.I)
    return f"{name} {m.group(1)}" if m else name


def _extract_header_meta(
    text: str,
    supplier_lines: Optional[List[Tuple[float, str]]] = None,
    parties: Optional[dict] = None,
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
    name = _complete_legal_suffix(name, text)

    gstin = (
        _GSTIN_SHAPE.search(own or "")
        or _GSTIN.search(own or "")
        or _GSTIN_SHAPE.search(text or "")
        or _GSTIN.search(text or "")
    )
    inv_no = _INVOICE_NO.search(text or "")
    number = inv_no.group(1) if inv_no else None
    if not number or not any(ch.isdigit() for ch in number) or _LOOKS_LIKE_DATE.match(number):
        number = find_invoice_no(text)
    if number:
        # The next label run into the number: "KLPL000419Date" (Klingen).
        number = re.sub(r"(?<=\d)(?:date|dated|dt)$", "", number, flags=re.I)
    if number and number[-1] in "-/":
        # A number that ends in "-" or "/" wrapped onto the next line: East
        # India prints "MUM25-" and, under it, "26TM/00523".
        rest = re.search(re.escape(number) + r"[^\n]*\n[ \t]*([A-Za-z0-9][A-Za-z0-9\-/]*)", text or "")
        if rest and any(ch.isdigit() for ch in rest.group(1)):
            number = number + rest.group(1)
    inv_dt = _invoice_date_match(text)

    supplier = {
        "name": _f(name),
        "gstin": _f(gstin.group(1).upper() if gstin else None),
        "address": _f(address),
    }
    # PAN, e-mail and drug licences come from the supplier's own block, so the
    # buyer's equivalents cannot be mistaken for the vendor's.
    party_text = "\n".join((parties or {}).values())
    for key, value in supplier_extras(own or text, party_text, text).items():
        supplier[key] = _f(value)

    # A GSTIN carries its owner's PAN in characters 3-12, so the supplier's two
    # identifiers have to agree. Abbott prints the BUYER's GSTIN inside the
    # supplier's own block and its own only in the page footer, so we filed
    # Abbott's purchases under the pharmacy's own GSTIN - which corrupts both
    # the purchase record and the input-tax credit claimed against it.
    better = supplier_gstin_for_pan(
        (supplier.get("pan") or {}).get("value"),
        (supplier.get("gstin") or {}).get("value"),
        text,
    )
    if better:
        supplier["gstin"] = _f(better)

    # Bill-to and Ship-to, each read from its own column.
    regions = parties or {}
    # A party area drawn twice over (Overseas) garbles BOTH columns, though
    # only one may show the tell-tale doubled letters - so the verdict is
    # taken once, for the pair.
    garbled = any(_has_doubled_glyphs(regions.get(k, "")) for k in ("bill_to", "ship_to"))
    party_fields = {}
    for key in ("bill_to", "ship_to"):
        detail = party_details(regions.get(key, ""))
        if garbled:
            detail["name"] = None
            detail["address"] = None
        party_fields[key] = {k: _f(v) for k, v in detail.items()}

    # Some bills name the buyer on a labelled line of its own rather than in
    # the Bill-to block: Abbott prints "Cust.Code & Name: 10023867-EASTERN
    # AGENCIES HEALTHCARE PVT. LTD." and then goes from "Billed To:" straight
    # into the street address.
    if not (party_fields.get("bill_to", {}).get("name") or {}).get("value"):
        labelled = _CUSTOMER_NAME.search(text or "")
        if labelled:
            party_fields.setdefault("bill_to", {})["name"] = _f(labelled.group(1).strip(" .,-"))

    # A buyer's name cut at its column's edge after "PVT" (JB) is finished the
    # same way as the supplier's - from the full line, legal suffix only.
    for key in ("bill_to", "ship_to"):
        leaf = party_fields.get(key, {}).get("name") or {}
        if leaf.get("value"):
            done = _complete_legal_suffix(leaf["value"], "\n".join([text or ""] + list(regions.values())))
            if done != leaf["value"]:
                party_fields[key]["name"] = _f(done)

    # Bill-to and Ship-to are usually the same company, and several suppliers
    # print its GSTIN or PAN only once. Share a value between them when the names
    # agree, rather than leaving a required field blank.
    bill, ship = party_fields.get("bill_to", {}), party_fields.get("ship_to", {})
    # Compared on a normalised key: the two blocks are the same company even
    # when one wrapped as "PVT LTD" and the other as "PVT. LTD.".
    bill_key = normalised_party_key(bill.get("name", {}).get("value"))
    ship_key = normalised_party_key(ship.get("name", {}).get("value"))
    same = bool(bill_key) and bool(ship_key) and (
        bill_key == ship_key or bill_key.startswith(ship_key) or ship_key.startswith(bill_key)
    )
    if same:
        for key in ("gstin", "pan"):
            here, there = bill.get(key, {}).get("value"), ship.get(key, {}).get("value")
            if here and not there:
                ship[key] = _f(here)
            elif there and not here:
                bill[key] = _f(there)

    references = extract_references(text, "\n".join(regions.values()))
    totals = extract_totals(text)
    return {
        **party_fields,
        "supplier": supplier,
        "invoice": {
            "invoice_no": _f(number),
            "invoice_date": _f(inv_dt.group(1) if inv_dt else None),
            "total_amount": _f(_extract_total(text)),
            **{k: _f(v) for k, v in references.items()},
            **{k: _f(v) for k, v in totals.items()},
        },
    }


# --------------------------------- row build ---------------------------------
# Columns read as text, and as numbers. Kept beside _COLS so adding a column
# there is a one-line change here rather than a silently dropped field.
_TEXT_FIELDS = ("batch_no", "expiry", "mfg_date", "hsn", "pack", "uom",
                "product_code", "manufacturer", "scheme")
_NUMERIC_FIELDS = ("quantity", "free_quantity", "total_quantity", "mrp", "ptr", "pts",
                   "rate", "discount_percent", "discount_amount", "scheme_percent",
                   "scheme_value", "cd_percent", "cd_amount", "wp_percent", "wp_amount",
                   "cgst_percent", "cgst_amount", "sgst_percent", "sgst_amount",
                   "igst_percent", "igst_amount", "utgst_percent", "utgst_amount",
                   "gross_amount", "net_amount", "amount")


# Piramal stacks "Product Code/ HSN": "400016002" over "30045090". Never a bare
# "HSN Code" - Sanofi wraps its HSN "300490" over "69" in that one field.
_STACKABLE = (("batch_no", r"batch|lot"), ("mfg_date", r"mfg|mfd"), ("expiry", r"exp"),
              ("product_code", r"(?:prod(?:uct)?|item)\.?\s*code"), ("hsn", r"hsn"))


def _stacked_fields(heading) -> List[str]:
    """The fields a stacked heading names, in the order it names them."""
    text = str(heading or "").lower()
    found = []
    for field, pattern in _STACKABLE:
        m = re.search(pattern, text)
        if m:
            found.append((m.start(), field))
    return [field for _, field in sorted(found)]


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


# Only three heads exist on an Indian invoice. A sale is either INTRA-state,
# taxed as CGST + SGST, or INTER-state, taxed as IGST - never both. UTGST simply
# replaces SGST in a Union Territory, so it belongs in the SGST slot.
def _tax_head(heading: str, interstate: Optional[bool], local: Optional[str] = None) -> str:
    """Which tax head a column belongs to.

    Suppliers routinely head one column for both possibilities - Zydus prints
    "CGST/ IGST %" and "SGST/ UTGST %" and fills whichever applies. Matching the
    heading alone picked the name that happened to appear last, so an
    intra-state Maharashtra sale was being posted as IGST and UTGST. Getting
    this wrong is not cosmetic: input credit claimed under the wrong head is a
    GST filing error.

    When a heading names both, the parties' GSTIN state codes decide. With no
    GSTINs to compare, the heading's own wording is the fallback.
    """
    names = {n for n in ("cgst", "sgst", "igst", "utgst") if n in heading}
    # `local` is the head that pairs with CGST on this invoice - SGST normally,
    # UTGST inside a Union Territory that has no legislature.
    pairs = local or ("utgst" if "utgst" in names and "sgst" not in names else "sgst")

    merged = bool(names & {"cgst", "sgst", "utgst"}) and "igst" in names
    if merged and interstate is not None:
        if interstate:
            return "igst"
        return "cgst" if "cgst" in names else pairs
    if names == {"igst"}:
        return "igst"
    if "cgst" in names:
        return "cgst"
    if names & {"sgst", "utgst"}:
        # An explicitly single-head column is honoured as printed: the supplier
        # knows whether it charges SGST or UTGST.
        return "utgst" if "utgst" in names and "sgst" not in names else pairs
    if "igst" in names:
        return "igst"
    return "cgst"


_TAX_FIELDS = ("cgst_percent", "cgst_amount", "sgst_percent", "sgst_amount",
               "igst_percent", "igst_amount", "utgst_percent", "utgst_amount")


def reassign_merged_tax_columns(cols: dict, header_row, interstate: Optional[bool],
                                local: Optional[str] = None) -> dict:
    """Correct tax columns whose heading names more than one head.

    Keyword mapping works on substrings, so "CGST/ IGST AMT." matches "igstamt"
    and the whole column is filed as IGST - on an intra-state invoice where the
    figure is actually CGST. Zydus heads all four of its tax columns that way.
    The heading cannot settle it; the parties' state codes can.
    """
    if interstate is None:
        return cols
    out = dict(cols)
    for field in _TAX_FIELDS:
        idx = out.get(field)
        if idx is None or idx >= len(header_row):
            continue
        heading = _norm(header_row[idx])
        heads = [h for h in ("cgst", "sgst", "igst", "utgst") if h in heading]
        if len(heads) < 2:
            continue                      # a single-head column is unambiguous
        suffix = "percent" if field.endswith("percent") else "amount"
        correct = f"{_tax_head(heading, interstate, local)}_{suffix}"
        if correct != field:
            out.pop(field, None)
            # Only claim the corrected slot if nothing truer already holds it.
            if out.get(correct) is None:
                out[correct] = idx
    return out


_EXPIRY_TOKEN = re.compile(r"^(\d{1,2}[/\-.]\d{2,4}|[A-Za-z]{3}[-/' ]?\d{2,4})$")


def _split_side_by_side(heading, value: str) -> Dict[str, str]:
    """Maker, expiry and batch from one cell whose heading names them in a row.

    Only for a heading naming the expiry AND the batch, and only when the cell
    holds exactly one expiry-shaped token - otherwise nothing is split. The
    words before the expiry are the maker (when the heading names one first),
    the words after it the batch, joined: a batch never contains a space, and
    "E JV01ABA" is how the PDF spaced "EJV01ABA".
    """
    # "FSSAI Expiry: 06.03.2027" printed into the heading (Raptakos) is the
    # licence's expiry, not this column's.
    head = re.sub(r"fssaiexpiry:?[\d:/]*", "", _norm(heading))
    at_exp = head.find("exp")
    at_batch = max(head.find("batch"), head.find("bno"), head.find("lot"))
    if at_exp < 0 or at_batch < 0 or not value:
        return {}
    tokens = value.split()
    found = [i for i, t in enumerate(tokens) if _EXPIRY_TOKEN.match(t)]
    if len(found) != 1:
        return {}
    k = found[0]
    before, after = tokens[:k], tokens[k + 1:]
    out = {"expiry": tokens[k]}
    # "Batch Expiry Sales Units" (Raptakos): a whole number after the expiry,
    # under a heading that names units or quantity after it, is the quantity.
    at_qty = max(head.find("units"), head.find("qty"), head.find("quantity"))
    if at_qty > at_exp and after and re.fullmatch(r"\d+", after[-1]):
        out["quantity"] = after[-1]
        after = after[:-1]
    if at_exp < at_batch:
        if not after:
            return {}
        out["batch_no"] = "".join(after)
        maker = " ".join(before).strip(" -")
        if maker and re.search(r"mfg|mfr|mkt|company", head[:at_exp]):
            out["manufacturer"] = maker
    else:
        if not before:
            return {}
        out["batch_no"] = "".join(before)
    return out


def _split_code_cell(heading, value: str) -> Dict[str, str]:
    """Serial, HSN and batch printed side by side under one heading.

    Blue Cross heads one column "SR. BATCH NO. HSN CODE NO." and prints
    "1 30049079 AGB2513 ANGICAM" - serial, HSN, batch, and the first word of
    the product's name, which runs in from the next column. Split only when
    the cell holds an HSN-shaped number AND a batch-shaped code; the words
    after them go back to the front of the description.
    """
    head = _norm(heading)
    if "hsn" not in head or not any(k in head for k in ("batch", "bno")) or not value:
        return {}
    tokens = value.split()
    if tokens and re.fullmatch(r"\d{1,3}", tokens[0]):
        tokens = tokens[1:]   # the serial number
    hsn = next((i for i, t in enumerate(tokens) if re.fullmatch(r"\d{4}(?:\d{2}){0,2}", t)), None)
    batch = next((i for i, t in enumerate(tokens) if i != hsn and re.search(r"[A-Za-z]", t)
                  and re.search(r"\d", t) and 3 <= len(t) <= 20), None)
    if hsn is None or batch is None:
        return {}
    out = {"hsn": tokens[hsn], "batch_no": tokens[batch]}
    rest = [t for i, t in enumerate(tokens) if i not in (hsn, batch) and i > max(hsn, batch)]
    if rest:
        out["description_prefix"] = " ".join(rest)
    return out


def _split_price_and_quantity(heading, value: str) -> Dict[str, str]:
    """"PTR * QUANTITY" over "28.57 800" (Blue Cross): the price, then the count."""
    head = _norm(heading)
    if "ptr" not in head or not any(k in head for k in ("quantity", "qty")):
        return {}
    numbers = (value or "").split()
    if len(numbers) != 2 or not re.fullmatch(r"\d[\d,]*\.\d{2}", numbers[0]) \
            or not re.fullmatch(r"\d[\d,]*", numbers[1]):
        return {}
    return {"ptr": numbers[0].replace(",", ""), "quantity": numbers[1].replace(",", "")}


# The whole GST rates (CGST + SGST together, or IGST).
_FULL_SLABS = {0.25, 1.0, 3.0, 5.0, 12.0, 18.0, 28.0, 40.0}
_CARRIED_FORWARD = re.compile(r"\b(balance\s*[bc]\s*/?\s*f|brought\s*forward|carried\s*forward)\b", re.I)
_MONEY_TOKEN = re.compile(r"(?:^|\s)(?:\d[\d,]*\.\d{2}|\.\d{2})(?:\s|$)")
_NOT_A_PRODUCT = re.compile(
    r"(\brupees\b|\bpaisa\b|\bonly\s*$|terms\s*(and|&)\s*conditions|\ba\s*/\s*c\b|\bifsc\b|"
    r"\bgstin\s*:|\bpan\s*no\b|\bstate\s*code\b|\bcredit\s*note\b|\bdebit\s*note\b|\bvalue\s*:|"
    r"^(cn|dn)\s*no|=====)",
    re.I,
)
_PAGE_FURNITURE = re.compile(
    r"\s(?:printed\s+(?:date|on|by)\s*:|page\s+\d+\s+of\s+\d+|\d[\d.]*\s*\*\s*\d+(?:\.\d+)?\s*\+\s*\d+(?:\.\d+)?\s*%\s*=)",
    re.I)
_MAKER_IN_DESC = re.compile(r"\s(?:mfg|mfr|mkt|marketed\s+by|manufactured\s+by)\s*[.:]\s*(?!(?:date|dt)\b)(\S.*)$", re.I)
# Where footer text starts when it runs onto the last product's description.
_FOOTER_IN_DESC = re.compile(r"\s(?:scheme\s+discount|discount\s+sgst|rupees|=====|value\s*:|"
                             r"credit\s+note|debit\s+note|terms\s*(?:and|&)\s*conditions|"
                             r"printed\s+(?:date|on)\s*:|"
                             r"\d[\d,]*\.\d{2}\s+\d[\d,]*\.\d{2})", re.I)
_HEADING_WORDS = {"amount", "value", "total", "qty", "quantity", "rate", "description", "particulars",
                  "product", "item", "schamt", "taxable"}


def _is_batch(value: str) -> bool:
    """Whether a batch column's text is a batch number: never a money figure
    ("331.17 331.17", ".00 631.05", "156.59 SGST"), a rupee amount, or a run
    of words - a totals row read off the grid puts those there."""
    text = (value or "").strip()
    if not text or "₹" in text or re.search(r"\brs\.?\s*\d", text, re.I):
        return False
    if _MONEY_TOKEN.search(text) or re.fullmatch(r"[\d,]+\.\d{2}", text):
        return False
    # Only digits with dots or spaces ("0 0.0") is a figure, not a batch; a
    # batch of digits alone ("25442312") is fine.
    if re.fullmatch(r"[\d.\s]+", text) and ("." in text or " " in text):
        return False
    return len(text.split()) <= 3


_WRAPPED_DECIMALS = re.compile(r"^([\d,]+\.\d?)\s*\n\s*(\d{1,2})(?:\n|$)")


def _unwrapped_number(cell):
    """A figure whose last decimals wrapped onto the next line, put back:
    Alkem prints "6,576.6" with its final "0" beneath it, and "15,108." with
    "00" - read as 6,576.6 and 15,108 the bill came out short."""
    text = str(cell or "").strip()
    m = _WRAPPED_DECIMALS.match(text)
    if not m:
        return cell
    whole, tail = m.group(1), m.group(2)
    decimals = whole.split(".")[1]
    if len(decimals) + len(tail) != 2:
        return cell
    return whole + tail


def _before_tax(item: dict, value) -> bool:
    """Whether a line figure is quantity x one of its prices while the line
    also carries tax - so the figure is before that tax."""
    def num(key):
        v = _num((item.get(key) or {}).get("value"))
        return float(v) if v is not None else None

    figure = float(_num(value) or 0)
    tax = sum(num(f"{h}_amount") or 0 for h in ("cgst", "sgst", "utgst", "igst"))
    qty = num("quantity")
    if not (figure > 0 and tax > 0 and qty):
        return False
    return any(p and abs(qty * p - figure) <= max(0.05, 0.001 * figure)
               for p in (num("rate"), num("pts"), num("ptr")))


def _tax_base(item: dict, rate_cell=None) -> Optional[str]:
    """The value a line's GST is charged on: its amount, less the line's own
    discount % when the amount is plainly before it - quantity times a price
    the line prints. Sumbiotic's 1,272.90 less 10% is taxed on 1,145.61."""
    amount = _num((item.get("amount") or {}).get("value"))
    disc = _num((item.get("discount_percent") or {}).get("value"))
    qty = _num((item.get("quantity") or {}).get("value"))
    if amount is None or not disc or not 0 < float(disc) < 100 or not qty:
        return amount
    prices = [_num(rate_cell)] + [_num((item.get(k) or {}).get("value")) for k in ("rate", "pts", "ptr")]
    if any(p and abs(float(qty) * float(p) - float(amount)) <= max(0.05, 0.001 * float(amount)) for p in prices):
        return f"{float(amount) * (1 - float(disc) / 100):.2f}"
    return amount


def _build_item(row, cols: dict, header_row, gst_cols: List[int],
                interstate: Optional[bool] = None,
                local: Optional[str] = None) -> Optional[dict]:
    cols = reassign_merged_tax_columns(cols, header_row, interstate, local)

    def cell(field):
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    # A page's running total brought forward shares the first item's cells in
    # a ruled grid: "Balance B/F\nOLMIN 20-CH", "220,830.00\n5,198.70" - the
    # first line of each two-line cell is the running total, the second the
    # item. Eris's bill came to twice its total until the first lines went.
    first_desc = str(cell("description") or "").split("\n")[0]
    if _CARRIED_FORWARD.search(first_desc):
        row = [("\n".join(str(c).split("\n")[1:]) if c is not None and "\n" in str(c) else c) for c in row]
        # A figure the brought-forward line has and the item does not stays
        # on one line: Eris's "Disc. Amt. 2,414.70" is the running discount,
        # while the item's amount before and after discount are the same.
        disc_at, gross_at, amount_at = cols.get("discount_amount"), cols.get("gross_amount"), cols.get("amount")
        if None not in (disc_at, gross_at, amount_at) and max(disc_at, gross_at, amount_at) < len(row) \
                and _num(row[gross_at]) and _num(row[gross_at]) == _num(row[amount_at]):
            row = list(row)
            row[disc_at] = ""

    desc = _clean(cell("description"))
    # The end of a long heading wrapped onto the first item: Torrent's
    # "Description (... as per | Pre GST reform 2.0 rates)" left "Pre GST
    # reform 2.0 rates) AMIFRU 40 TAB" as its first product.
    desc_at = cols.get("description")
    heading = _clean(header_row[desc_at]) if desc_at is not None and desc_at < len(header_row) else ""
    words = desc.split()
    for cut in range(len(words) - 1, 2, -1):
        if " ".join(words[:cut]) in heading:
            desc = " ".join(words[cut:])
            break
    if not desc or len(desc) < 2:
        return None

    item = {"description": _f(desc)}
    for field in _TEXT_FIELDS:
        if cols.get(field) is not None:
            item[field] = _f(_strip_stray(_clean(cell(field))))
    # The last word of the name run into the HSN column: Raptakos's
    # "THREPTIN DISKETTES JUNIOR 250 G" left "G 21069099" as its HSN.
    spill = re.fullmatch(r"([A-Za-z.]{1,4})\s+(\d{4}(?:\d{2}){0,2})", (item.get("hsn") or {}).get("value") or "")
    if spill:
        item["hsn"] = _f(spill.group(2))
        item["description"] = _f(f"{desc} {spill.group(1)}")
    # Name over HSN in one heading set narrower than the name: Dr. Reddy's
    # "Desc.of Goods/ HSN of Goods/" left "CELEVIDA" over "21069099" in the
    # column before it, and "EN-Vanilla Powder 400 gm" under the heading.
    hsn_text = (item.get("hsn") or {}).get("value") or ""
    if hsn_text and not re.fullmatch(r"[\d ]{4,10}", hsn_text) and cols.get("hsn") is not None \
            and "desc" in _norm(header_row[cols["hsn"]] if cols["hsn"] < len(header_row) else ""):
        lines = [ln.strip() for ln in str(cell("description") or "").split("\n") if ln.strip()]
        codes = [ln for ln in lines if re.fullmatch(r"\d{4}(?:\d{2}){0,2}", ln)]
        if codes:
            item["hsn"] = _f(codes[0])
            item["description"] = _f(_clean(" ".join([ln for ln in lines if ln not in codes] + [hsn_text])))
    # One HSN among other things in its cell: the product name under a shared
    # "HSN CODE PRODUCT DESCRIPTION" heading (Aristo, Graciera), the maker and
    # category codes before it ("CH N 30049099", Cipla), a "(NUTRA)" tag, or a
    # footer run in below it. The HSN is the one 4-, 6- or 8-digit code.
    hsn_text = (item.get("hsn") or {}).get("value") or ""
    if hsn_text and not re.fullmatch(r"[\d ]{4,10}", hsn_text):
        codes = re.findall(r"(?<![\dA-Za-z.,/])(\d{4}(?:\d{2}){0,2})(?![\d.,/])", hsn_text)
        if len(codes) == 1:
            item["hsn"] = _f(codes[0])
            name = _clean(hsn_text.split(codes[0], 1)[1])
            heading = _norm(header_row[cols["hsn"]]) if cols.get("hsn") is not None \
                and cols["hsn"] < len(header_row) else ""
            if hsn_text.startswith(codes[0]) and ("description" in heading or "product" in heading) \
                    and re.search(r"[A-Za-z]{3}", name):
                old = (item.get("description") or {}).get("value") or ""
                if old and old not in name and len(old) <= 5 and not (item.get("manufacturer") or {}).get("value"):
                    item["manufacturer"] = _f(old)   # the maker's code column, taken for the name
                item["description"] = _f(name)
    for field in _NUMERIC_FIELDS:
        if cols.get(field) is not None:
            item[field] = _f(_num(_unwrapped_number(cell(field))))
    # An HSN's last digit set against the MRP beside it: NSV prints "2106909
    # 9199.00" for HSN 21069099 and MRP 199.00. No HSN has seven digits, and
    # the MRP without it still sits above the trade prices.
    hsn_text = (item.get("hsn") or {}).get("value") or ""
    mrp_text = (item.get("mrp") or {}).get("value") or ""
    if re.fullmatch(r"\d{7}", hsn_text) and re.fullmatch(r"\d{2,}(?:\.\d+)?", mrp_text):
        rest = float(mrp_text[1:])
        trade = [float(v) for v in (_num((item.get(k) or {}).get("value")) for k in ("ptr", "pts", "rate")) if v]
        if trade and rest >= max(trade):
            item["hsn"] = _f(hsn_text + mrp_text[0])
            item["mrp"] = _f(mrp_text[1:])

    # A column stacking two fields carries one value per line, in the order its
    # heading names them: Overseas's "Mfg.Dt / Exp.Dt" holds "Aug-25" then
    # "Jul-27", Menarini's "Batch No / Mfg.Date" holds the batch then the mfg
    # date. Read as one value, Overseas's expiry was its manufacturing date.
    for idx, heading in enumerate(header_row):
        if idx >= len(row):
            continue
        parts = [p.strip() for p in str(row[idx] or "").split("\n") if p.strip()]
        order = _stacked_fields(heading)
        # ...or three, one per line: Prabodhan's "Batch No/ Mfg Dt./ Expiry Dt."
        # holds "PLMT2505", "02/25", "01/28".
        if len(parts) < 2 or not (len(order) == 2 or len(order) == len(parts) == 3):
            continue
        for field, value in zip(order, parts):
            item[field] = _f(_strip_stray(_clean(value)))

    # One column heading several fields side by side, on ONE line: the
    # Marg-style ERP heads "MFGR EXP. BATCH NO. DATE" and prints "ABLT 12/26
    # CH-2501" - maker, expiry, batch. Read as one value the batch was "ABLT
    # 12/26 CH-2501" and the expiry blank.
    for idx, heading in enumerate(header_row):
        if idx >= len(row) or "\n" in str(row[idx] or "").strip():
            continue
        split = _split_side_by_side(heading, _clean(row[idx]))
        for field, value in split.items():
            if field == "manufacturer" and cols.get("manufacturer") not in (None, idx):
                continue
            if field == "expiry" and cols.get("expiry") not in (None, idx):
                continue
            item[field] = _f(value)
        if "\n" in str(row[idx] or "").strip():
            continue
        for field, value in {**_split_code_cell(heading, _clean(row[idx])),
                             **_split_price_and_quantity(heading, _clean(row[idx]))}.items():
            if field == "description_prefix":
                desc = f"{value} {desc}".strip()
                item["description"] = _f(desc)
            elif not (item.get(field) or {}).get("value") or cols.get(field) == idx:
                item[field] = _f(value)

    # A tax head's heading run into the line's own "AMOUNT" heading (Aurowin:
    # "SGST | CGST AMOUNT" over "2.50 | 2.50 1403.40"): the cell holds the
    # head's RATE and the line AMOUNT. Taken as the head's tax it posted 2.50
    # rupees and left the line with no amount. Only where the bill has no
    # amount column of its own, and the two figures are a rate and money.
    if cols.get("amount") is None:
        for head in ("cgst", "sgst", "igst", "utgst"):
            idx = cols.get(f"{head}_amount")
            if idx is None or idx >= len(row):
                continue
            # The line's own row only: Aurowin's last line has the bill's TOTAL
            # row stacked under it in the same cell ("2.50 2828.60\n27485.60").
            first = str(row[idx] or "").split("\n")[0]
            figures = [float(n.replace(",", "")) for n in _NUM.findall(first)]
            if len(figures) == 2 and _is_gst_rate(figures[0]) and figures[1] > 10 * max(figures[0], 1):
                if not (item.get(f"{head}_percent") or {}).get("value"):
                    item[f"{head}_percent"] = _f(f"{figures[0]:g}")
                item["amount"] = _f(f"{figures[1]:.2f}")
                item.pop(f"{head}_amount", None)
                break
            # Both heads' rates, then the amount: Agresco heads its last column
            # "SGST CGST Amount" over "6.00 6.00 1240.20".
            if len(figures) == 3 and figures[0] == figures[1] and figures[0] * 2 in _FULL_SLABS \
                    and figures[2] > 10 * figures[0]:
                for which in ("cgst", local or "sgst"):
                    if not (item.get(f"{which}_percent") or {}).get("value"):
                        item[f"{which}_percent"] = _f(f"{figures[0]:g}")
                item["amount"] = _f(f"{figures[2]:.2f}")
                item.pop(f"{head}_amount", None)
                break

    # The line amount in an unheaded last column, run into the cell beside it:
    # Kreit heads "... CGST | Value" and prints "270.11 3001.25" under the
    # last heading - the CGST and then the amount (25 x 120.05). The mapped
    # "Value" read as the amount summed the bill's tax. Taken only when the
    # mapped figure is NOT quantity x rate and the row's last figure is.
    qty, rate = _num(cell("quantity")), _num(cell("rate"))
    if qty and rate and row:
        dis = _num((item.get("discount_percent") or {}).get("value"))
        built = float(qty) * float(rate) * (1 - (float(dis) if dis and float(dis) < 100 else 0) / 100)
        tol = max(1.0, 0.005 * built)
        have = _num((item.get("amount") or {}).get("value"))
        last = [float(n.replace(",", "")) for n in _NUM.findall(str(row[-1] or "").split("\n")[0])]
        if built > 0 and (have is None or abs(float(have) - built) > tol) and len(last) >= 2 \
                and abs(last[-1] - built) <= tol:
            item["amount"] = _f(f"{last[-1]:.2f}")

    # "20+2" under Qty (Agresco, Marg): twenty billed and two free - and
    # "500+100" or "10/2" under a "Qty Sale+Free" / "Qty/ FreeQty" heading.
    plus = re.fullmatch(r"\s*(\d+)\s*[+/]\s*(\d+)\s*", str(cell("quantity") or "").split("\n")[0])
    if plus and not (item.get("free_quantity") or {}).get("value"):
        item["quantity"] = _f(plus.group(1))
        item["free_quantity"] = _f(plus.group(2))

    # A "discount %" over 100 is money, not a rate: the ERP above heads its
    # discount column just "DISC" and prints 870.46 rupees in it.
    disc = _num((item.get("discount_percent") or {}).get("value"))
    if disc is not None and float(disc) > 100 and not (item.get("discount_amount") or {}).get("value"):
        item["discount_amount"] = item.pop("discount_percent")

    # Anything the mapping did not claim is kept verbatim under the supplier's
    # own heading. A column we have never seen before - a scheme percentage, a
    # case/loose marker, a manufacturer code - is real information off the bill,
    # and it used to be discarded without trace.
    claimed = set(cols.values()) | set(gst_cols)
    extras = []
    for idx, heading in enumerate(header_row):
        if idx in claimed or idx >= len(row):
            continue
        label = _strip_stray(_clean(heading))
        value = _strip_stray(_clean(row[idx]))
        if label and value:
            extras.append({"label": label, "value": value, "confidence": 1.0})
    if extras:
        item["extras"] = extras

    # `rate` is deliberately NOT guessed here. Which printed price column a bill
    # is charged on varies by supplier and by who the buyer is, and no ordering
    # of column names gets it right - invoice_checks.resolve_billed_rate decides
    # it from amount / quantity once the whole invoice has been read.

    # GST% = sum of CGST%+SGST% (or IGST%) columns, and each head's own rate and
    # amount where the invoice separates them.
    #
    # Several suppliers merge the pair into one cell - JB heads a column
    # "CGST Rate | Amt." and prints "6.00 1396.02" under it. Read as a single
    # number that is either a 665% tax rate or a lost amount, so a merged cell is
    # split: the first number is the rate, the second is the tax.
    gst_vals = []
    whole_rate = None   # from a column printing the line's whole GST rate
    for gi in gst_cols:
        if gi >= len(row):
            continue
        numbers = [float(n.replace(",", "")) for n in _NUM.findall(str(_unwrapped_number(row[gi]) or ""))]
        if not numbers:
            continue
        head = _norm(header_row[gi]) if gi < len(header_row) else ""
        if head in _BARE_TAX_RATE:
            # Troikaa's "Tax%" prints "6.00/6.00": CGST over SGST, both halves
            # of one slab. A single figure is the line's whole rate: split
            # between CGST and SGST within a state, all IGST across states.
            pair = _RATE_PAIR.match(str(row[gi] or "").splitlines()[0] if row[gi] else "")
            if pair and float(pair.group(1)) == float(pair.group(2)) and \
                    float(pair.group(1)) * 2 in _FULL_SLABS:
                whole_rate = float(pair.group(1)) * 2
            elif numbers[0] in _FULL_SLABS | {0.0}:
                whole_rate = numbers[0]
            else:
                continue
            heads = {"igst": whole_rate} if interstate else \
                {"cgst": whole_rate / 2, local or "sgst": whole_rate / 2} if interstate is False or pair else {}
            for which, pct in heads.items():
                if not (item.get(f"{which}_percent") or {}).get("value"):
                    item[f"{which}_percent"] = _f(f"{pct:g}")
            continue
        # The figure marked "%" is the rate, whatever else is one: Piramal
        # prints "0.00" over "2.5%" for a free line - no tax, at 2.5%.
        text = str(_unwrapped_number(row[gi]) or "")
        marked = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        at = len(_NUM.findall(text[:marked.start()])) if marked and _is_gst_rate(float(marked.group(1))) else None
        if at is None or at >= len(numbers):
            at = next((i for i, n in enumerate(numbers) if _is_gst_rate(n)), None)
        rate = numbers[at] if at is not None else None
        which = _tax_head(head, interstate, local)
        if rate is None:
            # No figure here can be a rate, so the cell holds the TAX ITSELF.
            # Menarini heads its column just "CGST" and prints 28.08 under it,
            # with the 2.50% on the wrapped line below; 28.08 exceeds India's
            # top slab, so it is money, not a percentage. Dropping the cell for
            # want of a rate lost the tax on every line of that invoice.
            if not (item.get(f"{which}_amount") or {}).get("value"):
                item[f"{which}_amount"] = _f(f"{numbers[-1]:.2f}")
            continue
        gst_vals.append(rate)

        if not (item.get(f"{which}_percent") or {}).get("value"):
            item[f"{which}_percent"] = _f(f"{rate:g}")
        amounts = [n for i, n in enumerate(numbers) if i != at]
        if not amounts and gi + 1 < len(row) and gi + 1 < len(header_row) \
                and not _clean(header_row[gi + 1]) and gi + 1 not in cols.values():
            # The head's name over its rate, its amount in the unheaded column
            # beside it: Torrent's "SGST/UTGST" spans "In % | Total Value".
            amounts = [float(n.replace(",", "")) for n in _NUM.findall(str(_unwrapped_number(row[gi + 1]) or ""))[:1]]
        if amounts and not (item.get(f"{which}_amount") or {}).get("value"):
            item[f"{which}_amount"] = _f(f"{amounts[-1]:.2f}")
        elif rate == 0 and not (item.get(f"{which}_amount") or {}).get("value"):
            # A head charged at 0% carries no tax. Saying so lets the invoice
            # total for that head read 0.00 - Menarini prints "IGST 0.00" - not
            # blank, which the export reads as "not captured".
            item[f"{which}_amount"] = _f("0.00")
    # No amount column, and a tax head's "amount" is exactly quantity x rate:
    # that is the LINE's amount printed in the tax column (Klingen heads its
    # last column just "C G S T" over "6.00 7521.50" - 50 x 150.43). As tax it
    # was 75 times too much; as the amount it reconciles.
    # (Already found as the row's last figure, it still is not the tax.)
    if cols.get("amount") is None:
        qty = _num(cell("quantity"))
        rate = _num(cell("rate"))
        for head in ("cgst", "sgst", "igst", "utgst"):
            value = _num((item.get(f"{head}_amount") or {}).get("value"))
            if not (qty and rate and value):
                continue
            built = float(qty) * float(rate)
            if built > 0 and abs(float(value) - built) <= max(1.0, 0.005 * built):
                if not (item.get("amount") or {}).get("value"):
                    item["amount"] = _f(f"{float(value):.2f}")
                item.pop(f"{head}_amount", None)
                break

    # No tax recognised by heading, but two side-by-side columns we did not
    # claim hold a real GST rate and exactly that rate's tax on the line:
    # Wanbury heads them "MISC Rate | Amt" over "5.00 | 6.67" (5% of 133.47).
    # The arithmetic, not the heading, says it is the line's GST.
    if not gst_vals and not any((item.get(f"{h}_percent") or {}).get("value")
                                for h in ("cgst", "sgst", "igst", "utgst")):
        base = _num((item.get("amount") or {}).get("value"))
        claimed_now = set(cols.values()) | set(gst_cols)
        for idx in range(len(row) - 1):
            if not base or idx in claimed_now or idx + 1 in claimed_now:
                continue
            rate, tax = _num(row[idx]), _num(row[idx + 1])
            if rate is None or tax is None or float(rate) not in _FULL_SLABS:
                continue
            if abs(float(base) * float(rate) / 100 - float(tax)) <= max(0.02, 0.003 * float(tax)):
                rate_f, tax_f = float(rate), float(tax)
                if interstate is True:
                    item["igst_percent"], item["igst_amount"] = _f(f"{rate_f:g}"), _f(f"{tax_f:.2f}")
                elif interstate is False:
                    for head in ("cgst", "sgst"):
                        item[f"{head}_percent"] = _f(f"{rate_f / 2:g}", confidence=_DERIVED_CONFIDENCE)
                        item[f"{head}_amount"] = _f(f"{tax_f / 2:.2f}", confidence=_DERIVED_CONFIDENCE)
                gst_vals = [rate_f]
                if not (item.get("net_amount") or {}).get("value"):
                    item["net_amount"] = _f(f"{float(base) + tax_f:.2f}", confidence=_DERIVED_CONFIDENCE)
                break

    # Within a state CGST and SGST are always the same rate and amount. When
    # only one head was recognised and a column we did not claim holds that
    # very "rate amount" pair, it is the other head - Corona's page 2 heads
    # its CGST column just "Details", the word CGST lost.
    if interstate is not True:
        for have, want in (("sgst", "cgst"), ("cgst", "sgst")):
            pct = _num((item.get(f"{have}_percent") or {}).get("value"))
            amt = _num((item.get(f"{have}_amount") or {}).get("value"))
            if not (pct and amt) or (item.get(f"{want}_amount") or {}).get("value"):
                continue
            claimed_now = set(cols.values()) | set(gst_cols)
            for idx in range(len(row)):
                if idx in claimed_now:
                    continue
                figures = _NUM.findall(str(row[idx] or "").split("\n")[0])
                if len(figures) == 2 and abs(float(figures[0]) - float(pct)) < 0.001 \
                        and abs(float(figures[1].replace(",", "")) - float(amt)) < 0.01:
                    item[f"{want}_percent"] = _f(f"{float(pct):g}")
                    item[f"{want}_amount"] = _f(f"{float(amt):.2f}")
                    gst_vals.append(float(pct))
                    break

    # Within a state, CGST and SGST are by law the same rate. Both parties in
    # one state and only one head read (Dr Reddy's, Wockhardt): the other is
    # the same - derived, so held below full confidence. And a head "amount"
    # equal to its own rate is the rate read twice (Wockhardt's "6.00").
    if interstate is False and not (item.get("igst_percent") or {}).get("value"):
        taxable_now = _tax_base(item, cell("rate"))
        for have, want in (("cgst", "sgst"), ("sgst", "cgst")):
            pct = _num((item.get(f"{have}_percent") or {}).get("value"))
            if not pct or float(pct) <= 0:
                continue
            amt = _num((item.get(f"{have}_amount") or {}).get("value"))
            if taxable_now and (amt is None or abs(float(amt) - float(pct)) < 0.001):
                amt = f"{float(taxable_now) * float(pct) / 100:.2f}"
                item[f"{have}_amount"] = _f(amt, confidence=_DERIVED_CONFIDENCE)
            if not (item.get(f"{want}_percent") or {}).get("value") and not (item.get("utgst_percent") or {}).get("value"):
                rate = float(pct)
                if rate * 2 in _FULL_SLABS:
                    # A half-rate (6 of 12): its pair is the same.
                    item[f"{want}_percent"] = _f(f"{rate:g}", confidence=_DERIVED_CONFIDENCE)
                    if amt is not None:
                        item[f"{want}_amount"] = _f(f"{float(amt):.2f}", confidence=_DERIVED_CONFIDENCE)
                    gst_vals = [rate, rate]
                elif rate in _FULL_SLABS and amt is not None and taxable_now and \
                        abs(float(taxable_now) * rate / 100 - float(amt)) <= max(0.05, 0.005 * float(amt)):
                    # A FULL rate (12) with the full tax: the combined GST in
                    # one column (Coxswain), split evenly between the heads.
                    half = f"{float(amt) / 2:.2f}"
                    for head in ("cgst", "sgst"):
                        item[f"{head}_percent"] = _f(f"{rate / 2:g}", confidence=_DERIVED_CONFIDENCE)
                        item[f"{head}_amount"] = _f(half, confidence=_DERIVED_CONFIDENCE)
                    gst_vals = [rate / 2, rate / 2]
            break

    # A head with a rate but no tax, on a line the other heads tax, is not
    # charged: Yogi prints "IGST Rate% 12.00 | IGST Amt 0.00" beside CGST and
    # SGST at 6% each on a sale within the state. Summed, the line was at 24%.
    paid = {h: _num((item.get(f"{h}_amount") or {}).get("value")) for h in ("cgst", "sgst", "utgst", "igst")}
    taxed = {h for h, v in paid.items() if v is not None and float(v) > 0}
    uncharged = [h for h, v in paid.items() if v is not None and float(v) == 0 and taxed and (
        (h == "igst" and taxed & {"cgst", "sgst", "utgst"}) or (h != "igst" and "igst" in taxed))]
    for h in uncharged:
        pct = _num((item.get(f"{h}_percent") or {}).get("value"))
        if pct and float(pct) > 0:
            item[f"{h}_percent"] = _f("0")
            gst_vals = [_num((item.get(f"{x}_percent") or {}).get("value")) for x in ("cgst", "sgst", "utgst", "igst")]
            gst_vals = [float(v) for v in gst_vals if v and float(v) > 0]
    if whole_rate is not None:
        # The bill's own whole rate; the heads' columns beside it only split it.
        gst_vals = [whole_rate]
    if gst_vals:
        item["gst_percent"] = _f(str(round(sum(gst_vals), 2)))
    elif (item.get("gst_percent") or {}).get("value") in (None, "0.0", "0"):
        heads_pct = [_num((item.get(f"{h}_percent") or {}).get("value"))
                     for h in ("cgst", "sgst", "igst", "utgst")]
        known_pcts = [float(p) for p in heads_pct if p is not None and float(p) > 0]
        if known_pcts:
            item["gst_percent"] = _f(str(round(sum(known_pcts), 2)), confidence=_DERIVED_CONFIDENCE)

    # A bill that prints each head's RATE on the line but its tax only in the
    # footer (Abbott: "6.00 | 6.00" per line, "7,722.00" twice at the foot) has
    # still stated the line's tax - it is the taxable value at that rate, which
    # is what GST is. Computed, so held below full confidence: the reviewer can
    # see it was worked out rather than read.
    taxable = _tax_base(item, cell("rate"))
    if taxable is not None:
        for head in ("cgst", "sgst", "igst", "utgst"):
            pct = _num((item.get(f"{head}_percent") or {}).get("value"))
            if pct and not (item.get(f"{head}_amount") or {}).get("value"):
                item[f"{head}_amount"] = _f(f"{float(taxable) * float(pct) / 100:.2f}",
                                            confidence=_DERIVED_CONFIDENCE)
            elif not pct and (item.get(f"{head}_amount") or {}).get("value"):
                amt_str = (item.get(f"{head}_amount") or {}).get("value")
                amt_num = _num(amt_str)
                if amt_num is not None:
                    try:
                        t_f = float(taxable)
                        a_f = float(amt_num)
                        if t_f > 0:
                            derived_pct = round((a_f / t_f) * 100.0, 2)
                            if _is_gst_rate(derived_pct):
                                item[f"{head}_percent"] = _f(f"{derived_pct:g}", confidence=_DERIVED_CONFIDENCE)
                    except (ValueError, TypeError):
                        pass

    # `amount` is the line value everything reconciles against. Many invoices
    # print only one value column and head it "Net Amount" or "Gross Amount";
    # now that those have fields of their own they no longer feed `amount`, so
    # fall back to them when no taxable column was printed. Without this such an
    # invoice has nothing to add up and cannot be reconciled at all.
    if not (item.get("amount") or {}).get("value"):
        for alternative in ("net_amount", "gross_amount"):
            value = (item.get(alternative) or {}).get("value")
            if value:
                item["amount"] = _f(value)
                if alternative == "net_amount" and _before_tax(item, value):
                    # Yogi's "Total Amount" is quantity x PTS, its CGST and
                    # SGST printed beside it on top: the taxable value, not
                    # a net that includes them.
                    item.pop("net_amount")
                break

    # A merged "Discount Qty | Amt." column (JB) holds two numbers under one
    # heading, so neither reached a field and the invoice reported no discount
    # at all. Split it the same way the merged tax columns are split: the last
    # number is the money.
    if cols.get("discount_amount") is None and cols.get("discount_percent") is None:
        for idx, heading in enumerate(header_row):
            head = _norm(heading)
            if "disc" not in head or idx >= len(row):
                continue
            if idx in cols.values() or idx in gst_cols:
                # Raptakos runs "Additional Discount" into its taxable value and
                # SGST % headings: that column is claimed, and its 9.00 is a rate.
                continue
            # The line's own figures only: a footer run in beneath ("250
            # TABLETS 80 ," from Cipla's pending items) is not its discount.
            numbers = _NUM.findall(str(row[idx] or "").split("\n")[0])
            if numbers:
                item["discount_amount"] = _f(numbers[-1].replace(",", ""))
            break

    # NetAmount is what the client's import posts against the purchase. Most
    # invoices do not print it as a column - they print the taxable value and
    # the tax heads and leave the reader to add them up - so compute it when it
    # is absent rather than exporting a blank for every single line.
    if not (item.get("net_amount") or {}).get("value"):
        taxable = _num((item.get("amount") or {}).get("value"))
        if taxable is not None:
            taxes = [
                _num((item.get(k) or {}).get("value"))
                for k in ("cgst_amount", "sgst_amount", "igst_amount", "utgst_amount")
            ]
            known = [float(t) for t in taxes if t is not None]
            if known:
                # As sure as the least sure figure it was added up from.
                sure = min(
                    (item.get(k) or {}).get("confidence") or 1.0
                    for k in ("cgst_amount", "sgst_amount", "igst_amount", "utgst_amount")
                    if (item.get(k) or {}).get("value")
                )
                item["net_amount"] = _f(f"{float(taxable) + sum(known):.2f}", confidence=sure)

    # A quantity the reading lost - printed hard against a scheme column, as
    # "10 (23.08%)" under "QTY SCH" - is still stated by the bill: it is the
    # line's amount before discount over its rate. Taken only when that comes
    # out a whole number, and held below full confidence.
    if not (item.get("quantity") or {}).get("value"):
        rate = _num((item.get("rate") or {}).get("value"))
        gross = _num((item.get("gross_amount") or {}).get("value")) or _num((item.get("amount") or {}).get("value"))
        try:
            if rate and gross and float(rate) > 0:
                q = float(gross) / float(rate)
                if q >= 1 and abs(q - round(q)) < 0.005:
                    item["quantity"] = _f(str(int(round(q))), confidence=_DERIVED_CONFIDENCE)
        except (TypeError, ValueError):
            pass

    # No rate printed, but the line shows its amount and its net (tax
    # included) amount: the GST between them is the rate, when it is a real
    # slab (Sun: 69,428.58 -> 77,760.00 is 12%).
    if not (item.get("gst_percent") or {}).get("value"):
        amt = _num((item.get("amount") or {}).get("value"))
        net = _num((item.get("net_amount") or {}).get("value"))
        try:
            if amt and net and float(amt) > 0 and float(net) > float(amt):
                pct = round((float(net) / float(amt) - 1) * 100, 2)
                if _is_gst_rate(pct):
                    item["gst_percent"] = _f(f"{pct:g}", confidence=_DERIVED_CONFIDENCE)
        except (TypeError, ValueError):
            pass

    # GST% is the sum of the heads' rates. Where only one head's rate column
    # was recognised (the other's "%" printed apart from its heading), the
    # heads read separately still say it: 2.5 + 2.5 is 5, not 2.5.
    pcts = {h: _num((item.get(f"{h}_percent") or {}).get("value")) for h in ("cgst", "sgst", "utgst", "igst")}
    local_sum = (float(pcts["cgst"] or 0) + max(float(pcts["sgst"] or 0), float(pcts["utgst"] or 0)))
    heads_sum = local_sum if local_sum else float(pcts["igst"] or 0)
    current = _num((item.get("gst_percent") or {}).get("value"))
    if heads_sum and _is_gst_rate(heads_sum) and (current is None or float(current) < heads_sum):
        item["gst_percent"] = _f(f"{heads_sum:g}")

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

    # Only keep rows that have at least a quantity or a batch. A quantity of
    # nothing is not one (an IRN line read "0.00" there), and a money figure in
    # the batch column is not a batch (a totals row read "91891.60" there).
    batch = (item.get("batch_no") or {}).get("value") or ""
    real_batch = _is_batch(batch)
    if re.fullmatch(r"[0-9a-fA-F]{32,}", desc.replace(" ", "")):
        return None  # an IRN or hash printed in the grid
    code_words = str((item.get("product_code") or {}).get("value") or "").split()
    if len(code_words) > 1 and any(ch.isdigit() for ch in code_words[0]) \
            and not any(ch.isdigit() for w in code_words[1:] for ch in w):
        # "DFT05 Ace | Revelol 50/5 10s" (IPCA): the name's first word sits in
        # the code column. A product code is one token.
        desc = f"{' '.join(code_words[1:])} {desc}".strip()
        item["description"] = _f(desc)
        item["product_code"] = _f(code_words[0])
    furniture = _PAGE_FURNITURE.search(desc)
    if furniture and furniture.start() > 0:
        # Page furniture printed under the grid, caught by the last line:
        # "TELMIKAA MT 50MG Printed Date: 28-08-2025" (Troikaa), Klingen's
        # tax working "14580*6+6%=874.8SGST+...".
        desc = desc[:furniture.start()].strip(" ,:-")
        item["description"] = _f(desc)
    batch_words = str((item.get("batch_no") or {}).get("value") or "").split()
    if len(batch_words) > 1 and all(w.isalpha() and len(w) >= 3 for w in batch_words[:-1]) \
            and any(ch.isdigit() for ch in batch_words[-1]):
        # "KLINPRO PROTEIN | POWDER NHPR25109" (Klingen): the name's last
        # word spilled into the batch column. A batch is one code.
        desc = f"{desc} {' '.join(batch_words[:-1])}".strip()
        item["description"] = _f(desc)
        item["batch_no"] = _f(batch_words[-1])
        batch = batch_words[-1]
        real_batch = _is_batch(batch)
    maker = _MAKER_IN_DESC.search(desc)
    if maker and maker.start() > 0:
        # "NIGRILOW CREAM 50GM Mfg : MAXNOVA HEALTHCARE" (Cosmin): the maker,
        # printed on the line under the name, is not part of the name.
        if not (item.get("manufacturer") or {}).get("value"):
            item["manufacturer"] = _f(maker.group(1).strip())
        desc = desc[:maker.start()].strip()
        item["description"] = _f(desc)
    junk_text = _NOT_A_PRODUCT.search(desc)
    if junk_text or re.fullmatch(r"[\s₹$.,:\d/-]*", desc) or desc.strip(" :.").lower() in _HEADING_WORDS:
        has_qty_or_amount = any((_num((item.get(k) or {}).get("value")) or 0) and
                                float(_num((item.get(k) or {}).get("value"))) > 0
                                for k in ("quantity", "amount"))
        if not (real_batch and has_qty_or_amount):
            # A tax summary, an amount in words, bank details, the buyer's
            # address or a repeated heading, read off the grid as a product.
            return None
        # A real product (its batch and quantity say so) whose description
        # caught the footer printed under it: "RIFABLOG-400 TAB SCHEME
        # DISCOUNT 0.00 ... Seventeen only" (Aurowin). Keep the product, cut
        # the footer. A code-only description (Bayer's "88574559 /6486125")
        # stays as printed.
        if junk_text:
            cut = _FOOTER_IN_DESC.split(desc)[0].strip(" :-,") or desc
            item["description"] = _f(cut)
    if (item.get("quantity") or {}).get("value") and (real_batch or not batch):
        return item
    if real_batch:
        return item
    return None


def _infer_description_column(rows, cols: dict, width: int) -> Optional[int]:
    """The column holding the medicine name, when no header named it.

    Judged on the data, not the heading: the description is the leftmost
    unclaimed column whose cells are mostly words. Requires real evidence - at
    least two rows and a clear majority - so a stray text cell in a numeric
    column cannot be mistaken for the product name.
    """
    claimed = set(cols.values())
    for idx in range(min(width, 6)):          # a product name is never far right
        if idx in claimed:
            continue
        wordy = total = 0
        for row in rows:
            if idx >= len(row):
                continue
            cell = _clean(row[idx])
            if not cell:
                continue
            total += 1
            # "words" means letters that are not just a code or a figure.
            if sum(c.isalpha() for c in cell) >= 3 and not _NUM.fullmatch(cell.replace(" ", "")):
                wordy += 1
        if total >= 2 and wordy * 2 > total:
            return idx
    return None


def _better_reading(word_items: List[dict], ruled_items: List[dict]) -> bool:
    """Whether the coordinate rebuild beat the ruled-table extraction.

    Row count used to decide this, with ruled winning ties because it is exact
    when the invoice really draws its grid. But a "table" found by its rules is
    not necessarily the LINE-ITEM table. Overseas draws one box around the whole
    page: `extract_tables()` hands back 25 rows of mostly empty cells that yield
    exactly ONE item - tying with the rebuilder's one CORRECT item and winning
    on the tie. That item had no quantity, the expiry in the mfg-date field, and
    a 6.00 tax RATE where the amount belongs.

    So the rows are judged on whether their arithmetic works - quantity x price
    against the line amount, the same test `validate_line_arithmetic` applies -
    and only then on how many there are. A page border cannot fake that.
    """
    def score(items: List[dict]) -> int:
        return sum(1 for item in items if line_arithmetic_holds(item))

    def solid(items: List[dict]) -> int:
        return sum(1 for item in items
                   if (item.get("batch_no") or {}).get("value") and (item.get("amount") or {}).get("value"))

    # Far more real lines - each with a batch and an amount - outweighs a few
    # whose arithmetic happens to check: East India's ruled grid gave 3 such
    # lines of its 17, while the rebuild read all 17 but could not test their
    # arithmetic (its rate sits in a merged heading).
    word_solid, ruled_solid = solid(word_items), solid(ruled_items)
    if word_solid >= 2 * ruled_solid and word_solid >= ruled_solid + 3:
        return True
    if ruled_solid >= 2 * word_solid and ruled_solid >= word_solid + 3:
        return False

    word_score, ruled_score = score(word_items), score(ruled_items)
    if word_score != ruled_score:
        return word_score > ruled_score

    # Equal so far: the reading that has each line's whole tax wins. Corona's
    # ruled grid lost its CGST column on page 2; the rebuild kept both heads.
    def taxed(items: List[dict]) -> int:
        def has(item, key):
            return bool((item.get(key) or {}).get("value"))
        return sum(1 for item in items
                   if (has(item, "cgst_amount") and (has(item, "sgst_amount") or has(item, "utgst_amount")))
                   or has(item, "igst_amount"))

    word_taxed, ruled_taxed = taxed(word_items), taxed(ruled_items)
    if word_taxed != ruled_taxed:
        return word_taxed > ruled_taxed
    # Neither is more self-consistent: fall back to the old rule, which keeps
    # the ruled path on Kanchan and Zydus where it has always been right.
    return len(word_items) > len(ruled_items)


# Words that only ever appear as a SUB-heading under another one, never as a
# column heading in their own right. A row made of these is the second half of
# a stacked header, not a line item.
# "Sold | Free" under "Quantity" (Medley).
_SUBHEADINGS = ("%", "amount", "amt", "rate", "value", "qty", "no", "date", "free", "sold", "billed")


def _merge_stacked_header(table, hi: int):
    """A stacked ruled header as one header row, plus where the data starts.

    V L Enterprises heads its tax pair across two ruled rows - "CGST" spanning
    two cells on one row, then "%" and "AMOUNT" beneath them. Read as a single
    row, both of its tax columns are headed just "CGST" and the amount is
    indistinguishable from the rate. The word-coordinate path already stitches
    stacked headers together; the ruled path did not, so the sub-heading row was
    treated as a line item and the tax amounts reached no field at all.

    The parent is carried across its children, so "AMOUNT" under "CGST" becomes
    "CGST AMOUNT" - but only where a child actually exists, leaving the blank
    cells a ruled grid is full of alone.
    """
    if hi + 1 >= len(table):
        return table[hi], hi + 1
    below = table[hi + 1]
    cells = [str(c or "").strip() for c in below]
    filled = [c for c in cells if c]
    if not filled or len(filled) > len(cells):
        return table[hi], hi + 1
    # Every filled cell must be a sub-heading word, and none of them a figure.
    if not all(_norm(c) in _SUBHEADINGS or _norm(c).strip("%") in _SUBHEADINGS
               for c in filled):
        return table[hi], hi + 1

    header = [str(c or "").strip() for c in table[hi]]
    merged, parent = [], ""
    for i, own in enumerate(header):
        child = cells[i] if i < len(cells) else ""
        if own:
            parent = own
        label = f"{parent} {child}".strip() if child else own
        merged.append(label)
    return merged, hi + 2


def _rows_from_tables(tables, labels: dict, interstate: Optional[bool] = None,
                      local: Optional[str] = None) -> List[dict]:
    """Line items from whichever of these tables is the line-item table."""
    out: List[dict] = []
    for table in tables or []:
        hi = _find_header_row(table)
        if hi is None:
            continue
        header_row, data_from = _merge_stacked_header(table, hi)
        cols = _map_columns(header_row)
        if "description" not in cols:
            # OCR of a scan regularly loses one header word, and "Description"
            # is a common casualty - Kanchan's whole table was rejected for it
            # even though every row carried the product name. A line-item table
            # always HAS a product name, and it is the leftmost column that
            # holds words rather than figures, so infer it rather than discard
            # the invoice.
            inferred = _infer_description_column(table[data_from:], cols, len(header_row))
            if inferred is not None:
                cols = {**cols, "description": inferred}
        if "description" not in cols or ("quantity" not in cols and "batch_no" not in cols):
            continue  # not a line-item table we understand
        gst_cols = _gst_columns(header_row)
        labels.update(price_labels(cols, header_row))
        rows = []
        for row in _with_detail_lines_folded(table[data_from:], cols):
            item = _build_item(row, cols, header_row, gst_cols, interstate, local)
            if item:
                rows.append(item)
        # The rest of the previous page's last line, at the top of this one.
        lead = _continued_from_last_page(table[data_from] if data_from < len(table) else None,
                                         cols, header_row)
        out.extend(([{"_continues": lead}] if lead else []) + _without_totals_row(rows))
    return out


def _with_detail_lines_folded(rows, cols: dict) -> list:
    """Each line's second printed line folded into it, cell by cell.

    Dr. Reddy's prints every item on two lines: the name, batch, quantity and
    amounts, then the HSN, expiry, PTR and the SGST beneath. The second line
    carries figures of its own, so it was taken for an item - one with no
    quantity and no amount - and dropped with the HSN and expiry on it. A row
    with neither, right after one with both, is the line above's.
    """
    def has(row, field):
        idx = cols.get(field)
        return idx is not None and idx < len(row) and bool(str(row[idx] or "").strip())

    numeric = [i for f, i in cols.items() if f not in ("description", "batch_no", "product_code", "uom", "pack")]
    out: list = []
    for row in rows:
        # ...and carries no words where the name goes - never a footer's
        # "Pending Items: Amlopres L Tablets 1 x 15'S IP 20 , ..." (Cipla).
        name_at = cols.get("description")
        name = str(row[name_at] or "") if name_at is not None and name_at < len(row) else ""
        detail = (out and has(out[-1], "quantity") and has(out[-1], "amount")
                  and not has(row, "quantity") and not has(row, "amount")
                  and not re.search(r"[A-Za-z]{3}", name)
                  and any(i < len(row) and _NUM.search(str(row[i] or "")) for i in numeric))
        if detail:
            above = out[-1]
            out[-1] = [("\n".join(p for p in (str(above[i] or "").strip(), str(row[i] or "").strip()) if p)
                        if i < len(row) else above[i]) for i in range(len(above))]
        else:
            out.append(list(row))
    return out


def _continued_from_last_page(row, cols: dict, header_row) -> Optional[dict]:
    """A page's first row that is the end of the previous page's last line.

    Mankind's line 7 breaks across the page: "OLMETIME-H 20 TABLETS(HSN-" with
    its MFG date on page 1, and "30049079)", its EXP date JUN-27 and its CGST
    and SGST values at the top of page 2. With no quantity, batch or amount it
    is no line of its own; its figures finish the line before it.
    """
    if not row:
        return None

    def cell(idx):
        return str(row[idx] or "").strip() if idx is not None and idx < len(row) else ""

    if any(cell(cols.get(k)) for k in ("quantity", "batch_no", "amount")):
        return None
    out: dict = {}
    if cell(cols.get("description")):
        out["description"] = _clean(cell(cols.get("description")))
    for idx, heading in enumerate(header_row):
        order = _stacked_fields(heading)
        value = cell(idx)
        if len(order) == 2 and value and "\n" not in value:
            out[order[1]] = value   # a stacked column's second value
        head = _norm(heading)
        for tax in ("cgst", "sgst", "igst", "utgst"):
            figures = _NUM.findall(value)
            if tax in head and figures and float(figures[-1].replace(",", "") or 0) > 0:
                out[f"{tax}_amount"] = figures[-1].replace(",", "")
    return out if any(k != "description" for k in out) else None


def _join_page_lines(pages: List[List[dict]]) -> List[dict]:
    """The pages' lines in order, a line broken across a page made whole."""
    lines: List[dict] = []
    for items in pages:
        for item in items:
            rest = item.get("_continues")
            if rest is None:
                lines.append(item)
                continue
            if not lines:
                continue
            last = lines[-1]
            if rest.get("description"):
                desc = f"{(last.get('description') or {}).get('value') or ''} {rest['description']}".strip()
                last["description"] = _f(desc)
            if rest.get("expiry"):
                # The first page carried only the stacked column's first value.
                if (last.get("expiry") or {}).get("value") and not (last.get("mfg_date") or {}).get("value"):
                    last["mfg_date"] = last["expiry"]
                last["expiry"] = _f(rest["expiry"])
            for key, value in rest.items():
                if key.endswith("_amount") and not (last.get(key) or {}).get("value"):
                    last[key] = _f(value)
    return lines


def _without_totals_row(items: List[dict]) -> List[dict]:
    """Drop the table's own totals row, read as if it were a product.

    Cipla Pharma and K Sales close the item grid with a row carrying the
    total quantity and the total amount under a remark ("Remark :"), which
    doubled the bill. Dropped only on proof: no batch, no HSN, no rate and no
    expiry - nothing a real product line has - AND an amount equal to the sum
    of every other line. A real product with the same amount as the rest
    together (two equal lines) still has its batch, so it stays.
    """
    if len(items) < 2:
        return items

    def has(item, key):
        return bool((item.get(key) or {}).get("value"))

    kept = []
    for n, item in enumerate(items):
        if any(has(item, k) for k in ("batch_no", "hsn", "rate", "expiry")):
            kept.append(item)
            continue
        amount = _num((item.get("amount") or {}).get("value"))
        rest = [_num((o.get("amount") or {}).get("value")) for m, o in enumerate(items) if m != n]
        rest_total = sum(float(a) for a in rest if a is not None)
        if amount is not None and rest_total > 0 and abs(float(amount) - rest_total) <= max(1.0, 0.0005 * rest_total):
            continue
        kept.append(item)
    return kept


_SLAB_TAX = re.compile(
    r"\b(CGST|SGST|IGST|UTGST)\s*([\d.]+)\s*%\s*on\s*taxable\s*value\s*([\d,]+\.\d{1,2})\s+([\d,]+\.\d{1,2})",
    re.I,
)


def _slab_tax_totals(invoice_meta: dict, text: str) -> None:
    """The bill's tax and taxable totals from its per-slab tax lines.

    Alkem's SAP bills state tax only as "Add CGST 6.00 % On Taxable Value
    668,913.93 40,134.82", one line per head and slab. Without them the bill
    had no tax to add to its lines, and could never reconcile. Each head's
    tax is the sum over its slabs; the taxable total is the sum over the slabs
    of ONE head (CGST, or IGST) - the same value is printed under each head.
    A figure the bill prints elsewhere is kept; one the parser summed is not.
    """
    heads: Dict[str, float] = {}
    taxable: Dict[str, Dict[tuple, float]] = {}
    for m in _SLAB_TAX.finditer(text or ""):
        head = m.group(1).lower()
        try:
            base = float(m.group(3).replace(",", ""))
            tax = float(m.group(4).replace(",", ""))
        except ValueError:
            continue
        heads[head] = heads.get(head, 0.0) + tax
        taxable.setdefault(head, {})[(m.group(2), m.group(3))] = base
    if not heads:
        return

    def settable(key: str) -> bool:
        leaf = invoice_meta.get(key) or {}
        return not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE

    for head, tax in heads.items():
        if settable(f"total_{head}_amount"):
            invoice_meta[f"total_{head}_amount"] = _f(f"{tax:.2f}")
    basis = taxable.get("cgst") or taxable.get("igst") or next(iter(taxable.values()))
    if settable("total_taxable_amount"):
        invoice_meta["total_taxable_amount"] = _f(f"{sum(basis.values()):.2f}")
    if settable("total_gst_amount"):
        invoice_meta["total_gst_amount"] = _f(f"{sum(heads.values()):.2f}")


# A tax head's total at the END of a summary line: "SGST 6% 169.32" (Alchem,
# beside its terms), "CGST VALUE 1,048.41" (Mahavir). Anchored to the line's
# end so a group heading "SGST% : 6.00 631.05" (rate, then amount) is not one.
_SUMMARY_TAX = re.compile(
    r"(?:^|\s)(CGST|SGST|IGST|UTGST)\s*(?:@?\s*([\d.]+)\s*%|value|amount|amt|payable|payble)?\s*[:\-]?\s*"
    r"(?:rs\.?\s*|₹\s*)?([\d,]+\.\d{1,2})\s*$",
    re.I | re.M,
)
# Any figure printed after a "Total" label - candidates for the bill's total.
_ANY_TOTAL = re.compile(r"\btotal\b[^\n\d₹]{0,25}(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2})", re.I)


def _total_by_cross_foot(invoice_meta: dict, text: str, items: Optional[List[dict]] = None) -> None:
    """The total that the bill's own taxable value and tax add up to.

    H&H prints "Total Amount ₹ 189093.15" - its lines BEFORE a 75,637.26
    discount - and only then "Total ₹ 132786.38", which is subtotal plus
    IGST. A label alone cannot tell them apart; the arithmetic can. When the
    total read is not taxable + tax, and another figure printed after a
    "Total" label is, that figure is the bill's total.
    """
    def printed(key):
        leaf = invoice_meta.get(key) or {}
        if not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE:
            return None
        return _num(leaf.get("value"))

    # The taxable value may be the lines' own sum: lines plus the printed tax
    # meeting a printed total is the proof either way.
    total = printed("total_amount")
    taxable = _num((invoice_meta.get("total_taxable_amount") or {}).get("value"))
    if not taxable and items:
        amounts = [_num((i.get("amount") or {}).get("value")) for i in items]
        taxable = sum(float(a) for a in amounts) if None not in amounts else None
    heads = [printed(f"total_{h}_amount") for h in ("cgst", "sgst", "igst", "utgst")]
    tax = sum(float(h) for h in heads if h)
    if not total or not taxable or not tax:
        return
    built = float(taxable) + tax
    if abs(built - float(total)) <= 1.0:
        return
    # A total the bill also spells out, or one a credit note it sets off
    # explains (Medley: 672,940.80 less 38,642 of notes = 634,299), stands.
    from app.services.ocr.invoice_checks import credit_notes_set_off

    words = total_from_words(text)
    if words is not None and abs(float(words) - float(total)) < 1.0:
        return
    if any(abs(float(total) + cn - built) <= 1.0 for cn in credit_notes_set_off(text)):
        return
    for m in _ANY_TOTAL.finditer(text or ""):
        figure = float(m.group(1).replace(",", ""))
        if abs(figure - built) <= 1.0:
            invoice_meta["total_amount"] = _f(f"{figure:.2f}")
            return


def _summary_tax_totals(invoice_meta: dict, text: str) -> None:
    """Each tax head's total from the bill's summary lines, when no labelled
    "Total CGST" was read. Alchem and Mahavir print GST only there - none on
    the lines - so without it their lines could never build up to the total.
    A head printed on every page is counted once; two slabs of a head add.
    """
    found: Dict[str, Dict[tuple, float]] = {}
    for m in _SUMMARY_TAX.finditer(text or ""):
        try:
            value = float(m.group(3).replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            found.setdefault(m.group(1).lower(), {})[(m.group(2), value)] = value
    bill = _num((invoice_meta.get("total_amount") or {}).get("value"))
    for head, slabs in found.items():
        # One head's tax is at most a fifth of the bill (half the 40% slab).
        # 3100820 prints "CGST 2.5000 % 740.64" - the slab's TAXABLE value,
        # with the tax on the line below - summing to the whole bill's base.
        if bill and sum(slabs.values()) > 0.2 * float(bill):
            continue
        key = f"total_{head}_amount"
        leaf = invoice_meta.get(key) or {}
        if not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE:
            invoice_meta[key] = _f(f"{sum(slabs.values()):.2f}")


_SUB_TOTAL = re.compile(r"\bsub\s*-?\s*total\s*[:\-]?\s*(?:rs\.?\s*)?([\d,]+\.\d{1,2})\b", re.I)


def _taxable_from_sub_total(invoice_meta: dict, text: str) -> None:
    """A "SUB TOTAL" is the taxable value when the bill's own tax heads build
    it up to its grand total. Cosmin prints SUB TOTAL 30,058.29 + SGST and
    CGST 2,705.24 each = 35,469.00 - after a 5% discount it never prints, so
    without this figure its lines (31,640.32) had nothing to step down to.
    """
    leaf = invoice_meta.get("total_taxable_amount") or {}
    if leaf.get("value") and leaf.get("confidence") != _SUMMED_CONFIDENCE:
        return
    total = _num((invoice_meta.get("total_amount") or {}).get("value"))
    heads = [_num((invoice_meta.get(f"total_{h}_amount") or {}).get("value"))
             for h in ("cgst", "sgst", "igst", "utgst")]
    tax = sum(float(h) for h in heads if h)
    if not total or not tax:
        return
    for m in _SUB_TOTAL.finditer(text or ""):
        value = float(m.group(1).replace(",", ""))
        if abs(value + tax - float(total)) <= 1.0:
            invoice_meta["total_taxable_amount"] = _f(f"{value:.2f}")
            return


def _labelled_totals(invoice_meta: dict, text: str) -> None:
    """The bill's labelled totals read from EVERY page. The header is read
    from the first page, but a multi-page bill prints its foot on the last:
    Medley's "Less Scheme Disc 66760.00" and "Total Amount Before Tax
    600,840.00" are on page 2. Fills only what no page-one label gave."""
    for key, value in extract_totals(text).items():
        leaf = invoice_meta.get(key) or {}
        if value and (not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE):
            invoice_meta[key] = _f(value)


_SUMMARY_FIGURE = re.compile(r"^₹?-?[\d,]*\d\.\d{2}%?$")


def _slab_summary_table(invoice_meta: dict, text: str) -> Optional[dict]:
    """The bill's totals from its GST slab summary: a heading line naming the
    taxable value and the tax heads, then one row of figures per slab.

    Hindustan Capsule prints "Amount SchAmt Discamt Taxable CSGT% CGSTRs.
    SGST% SGSTRs. TotalAmt." with a row for each slab - its only statement of
    the 3% discount, the taxable value and the tax. Each column is summed;
    taken only when the bill's own arithmetic holds (taxable + tax = the
    table's total), and never over a figure the bill states elsewhere.
    """
    lines = (text or "").splitlines()
    for at, line in enumerate(lines):
        low = line.lower()
        if "taxable" not in low or not re.search(r"cgst|sgst|igst", low):
            continue
        rows = []
        for row in lines[at + 1:at + 12]:
            tail = []
            for token in reversed(row.split()):
                if not _SUMMARY_FIGURE.match(token):
                    break
                tail.append(token)
            if len(tail) >= 4:
                rows.append(list(reversed(tail)))
        width = max((len(r) for r in rows), default=0)
        rows = [r for r in rows if len(r) == width]
        heads = line.split()[-width:] if width else []
        if not rows or len(heads) != width:
            continue
        sums: Dict[str, float] = {}
        for i, head in enumerate(heads):
            h = _norm(head)
            if "%" in head:
                continue
            key = ("total_discount_amount" if "disc" in h or h.startswith("sch") else
                   "total_taxable_amount" if "taxable" in h else
                   next((f"total_{t}_amount" for t in ("cgst", "sgst", "igst", "utgst") if t in h), None) or
                   ("bill_total" if "total" in h else
                    # The plain "Amount" column: the lines' own value, by slab.
                    "lines_amount" if h in ("amount", "amt", "value") else None))
            if key:
                sums[key] = sums.get(key, 0.0) + sum(
                    float(r[i].lstrip("₹").replace(",", "").rstrip("%")) for r in rows)
        taxable, total = sums.get("total_taxable_amount"), sums.get("bill_total")
        tax = sum(v for k, v in sums.items() if k.endswith("gst_amount"))
        if not taxable or not total or abs(taxable + tax - total) > 1.0:
            continue
        for key, value in sums.items():
            if key in ("bill_total", "lines_amount"):
                continue
            leaf = invoice_meta.get(key) or {}
            if not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE:
                invoice_meta[key] = _f(f"{value:.2f}")
        # What the table itself says, for the reconciliation: its Amount column
        # is the bill's own sum of its lines (invoice_checks.reconcile_invoice).
        return {k: round(v, 2) for k, v in {**sums, "tax": tax}.items()}
    return None


def _scale_worked_out_tax(items: List[dict], invoice_meta: dict) -> None:
    """Line tax we worked out, charged on what the bill-wide discount leaves.

    A line's tax worked out as its amount at its rate is too much when the
    bill takes a discount off every line before the tax: Hindustan Capsule's
    3% (printed only in its slab summary), Cosmin's 5% (never printed). When
    the lines less the discount the bill states - or a round rate - come to its
    printed taxable value, each worked-out tax is scaled by the same share.
    Only worked-out figures change; a tax the line prints stands.
    """
    def printed(key):
        leaf = invoice_meta.get(key) or {}
        if not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE:
            return None
        return _num(leaf.get("value"))

    taxable = printed("total_taxable_amount")
    amounts = [_num((i.get("amount") or {}).get("value")) for i in items]
    if not taxable or not items or any(a is None for a in amounts):
        return
    line_total = sum(float(a) for a in amounts)
    if line_total <= 0 or float(taxable) >= line_total:
        return
    gap = line_total - float(taxable)
    discount = printed("total_discount_amount")
    pct = gap / line_total * 100
    if not ((discount and abs(float(discount) - gap) <= 1.0) or abs(pct - round(pct * 2) / 2) <= 0.02):
        # No one share takes the lines to the taxable value (Hindustan
        # Capsule's credit note deducts its scheme from one slab only): a tax
        # worked out per line would be a figure the bill never states. Left
        # blank - the bill's own head totals stand.
        for item in items:
            for head in ("cgst", "sgst", "igst", "utgst"):
                if (item.get(f"{head}_amount") or {}).get("confidence") == _DERIVED_CONFIDENCE:
                    item.pop(f"{head}_amount", None)
        return
    scale = float(taxable) / line_total
    for item in items:
        for head in ("cgst", "sgst", "igst", "utgst"):
            leaf = item.get(f"{head}_amount") or {}
            if leaf.get("value") and leaf.get("confidence") == _DERIVED_CONFIDENCE:
                item[f"{head}_amount"] = _f(f"{float(_num(leaf['value'])) * scale:.2f}",
                                            confidence=_DERIVED_CONFIDENCE)


def _gst_total_from_heads(invoice_meta: dict) -> None:
    """The bill's GST total is its heads added up. Marg heads a class table
    "SGST CGST TOTAL GST SUB TOTAL" and a stray figure was read under the
    label (AANAV: 90.00 against SGST and CGST PAYBLE 329.82 each); the heads
    it prints as payable are the specific figures, and they win."""
    def printed(key):
        leaf = invoice_meta.get(key) or {}
        if not leaf.get("value") or leaf.get("confidence") == _SUMMED_CONFIDENCE:
            return None
        return _num(leaf.get("value"))

    found = {h: printed(f"total_{h}_amount") for h in ("cgst", "sgst", "igst", "utgst")}
    found = {h: float(v) for h, v in found.items() if v is not None and float(v) > 0}
    # Every head the bill charges must be printed: CGST with its SGST/UTGST
    # pair, or IGST. Abbott prints only "CGST :Rs. 7,722.00" legibly - half
    # the tax - and that half is not its GST total.
    if "cgst" in found and not ({"sgst", "utgst"} & set(found)):
        return
    if ({"sgst", "utgst"} & set(found)) and "cgst" not in found:
        return
    heads = list(found.values())
    if not heads:
        return
    total = printed("total_gst_amount")
    if total is None or abs(float(total) - sum(heads)) > 1.0:
        invoice_meta["total_gst_amount"] = _f(f"{sum(heads):.2f}")


def _drop_impossible_tax_total(invoice_meta: dict) -> None:
    """A "total GST" larger than the top GST rate allows is a misread: Marg's
    class table prints "TOTAL GST TOTAL 62434.99" and the bill's whole taxable
    value was taken as its tax. Blank beats impossible - the lines' own tax is
    summed in its place."""
    gst = _num((invoice_meta.get("total_gst_amount") or {}).get("value"))
    base = _num((invoice_meta.get("total_taxable_amount") or {}).get("value")) or         _num((invoice_meta.get("total_amount") or {}).get("value"))
    if gst and base and float(base) > 0 and float(gst) > 0.29 * float(base):
        invoice_meta.pop("total_gst_amount", None)


def _total_from_words_if_missing(fields: dict, full_text: str) -> None:
    """The total as the bill spells it out, when no figure could be read.

    Marg-style bills print "Grand Total" in one place and its figure in a box
    elsewhere, and some carry a literal "GRAND TOTAL 0.00" in the text layer -
    yet every one of them spells the amount out: "Rs. Twenty two thousand
    three hundred and twenty only". That is the bill's own statement of what
    is payable, so it stands in for the figure - a total of 0.00 on a bill
    with lines is never the real one. Held below a direct reading.
    """
    invoice = fields.setdefault("invoice", {})
    current = _num((invoice.get("total_amount") or {}).get("value"))
    if current is not None and float(current) > 0:
        return
    words = total_from_words(full_text)
    if words:
        invoice["total_amount"] = _f(words, confidence=0.9)


def parse_scanned_invoice(data: bytes, content_type: str,
                          max_pages: Optional[int] = None,
                          header_only_ok: bool = False) -> Optional[dict]:
    """Read a SCANNED invoice's table with Tesseract instead of the paid model.

    Identical in shape to `parse_invoice_pdf`, and deliberately built from the
    same pieces: Tesseract supplies positioned words, `tables_from_words`
    rebuilds the columns, and `_rows_from_tables` turns them into line items -
    the very code the digital invoices proved. Header fields come from the same
    patterns too.

    Returns None whenever it cannot do the job, and the caller then uses the AI
    exactly as before. Declining is not a failure: a wrong number read off a
    scan would become wrong stock, so the bar for keeping this reading is that
    the invoice reconciles - which the caller checks.
    """
    from app.services.ocr import tesseract_table

    if not tesseract_table.available():
        return None

    pages = tesseract_table.words_per_page(data, content_type, max_pages)
    if not pages or not any(pages):
        return None

    labels: dict = {}
    line_items: List[dict] = []
    meta = None
    page_texts: List[str] = []
    interstate: Optional[bool] = None
    local_head: Optional[str] = None

    for words in pages:
        if not words:
            page_texts.append("")
            continue
        text = tesseract_table.words_to_text(words)
        page_texts.append(text)
        if meta is None:
            # The header is read from the OCR text. There is no x-column
            # separation to lean on here, so the party blocks come from the flat
            # text and may interleave - the reconciliation check below is what
            # keeps a bad reading out.
            meta = _extract_header_meta(text, [(0.0, ln) for ln in text.splitlines()])
            local_head = local_tax_head(
                (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                (meta.get("bill_to", {}).get("gstin") or {}).get("value"),
            )
            interstate = is_interstate(
                (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                (meta.get("bill_to", {}).get("gstin") or {}).get("value"),
            )
        line_items = _join_page_lines([line_items, _rows_from_tables(
            tesseract_table.tables_from_words(words), labels, interstate, local_head
        )])

    if not line_items and not (header_only_ok and meta):
        return None

    fields = meta or {"supplier": {}, "invoice": {}}
    full_text = "\n".join(page_texts)
    if not (fields.get("invoice", {}).get("total_amount") or {}).get("value"):
        fields.setdefault("invoice", {})["total_amount"] = _f(_whole_bill_total(full_text))
    _total_from_words_if_missing(fields, full_text)

    invoice_meta = fields.setdefault("invoice", {})
    _labelled_totals(invoice_meta, full_text)
    _slab_tax_totals(invoice_meta, full_text)
    _summary_tax_totals(invoice_meta, full_text)
    _taxable_from_sub_total(invoice_meta, full_text)
    slab_summary = _slab_summary_table(invoice_meta, full_text)
    _gst_total_from_heads(invoice_meta)
    _total_by_cross_foot(invoice_meta, full_text, line_items)
    _drop_impossible_tax_total(invoice_meta)
    _scale_worked_out_tax(line_items, invoice_meta)
    for key, value in sum_line_totals(line_items).items():
        if not (invoice_meta.get(key) or {}).get("value"):
            # Added up from the lines, not read off the bill - held below a
            # printed figure, so nothing mistakes it for the bill's own total.
            invoice_meta[key] = _f(value, confidence=_SUMMED_CONFIDENCE)

    fields["line_items"] = line_items
    fields["_hints"] = {
        "copies_detected": 1,
        "stated_item_count": _extract_item_count(full_text),
        "price_labels": labels,
        "document_text": full_text,
        # The bill's total as it spells it out, for the cross-check against the
        # figure. An Indian tax invoice states it twice and they do not always
        # agree - see reconcile_invoice.
        "total_in_words": total_from_words(full_text),
        "slab_summary": slab_summary,
    }
    return fields


_READABLE = re.compile(r"\b(INVOICE|TOTAL|AMOUNT|GST|QTY|BATCH|RATE|MRP|DATE|PRODUCT)\b", re.I)


def _is_sideways(page) -> bool:
    """Whether a page's text is drawn a quarter turn round (see upright_pdf)."""
    chars = page.chars or []
    if not chars:
        return False
    if sum(1 for c in chars if not c.get("upright", True)) >= 0.6 * len(chars):
        return True
    # Text run down the page can still be flagged upright; it then reads one
    # letter per "word" - "G", "r", "a", "n", "d". Told cheaply from a sample:
    # consecutive letters share an x and step down the page, not across it.
    sample = [c for c in chars[:400] if str(c.get("text", "")).strip()]
    pairs = list(zip(sample, sample[1:]))
    down = sum(1 for a, b in pairs
               if abs(float(a["x0"]) - float(b["x0"])) < 1.0 and abs(float(a["top"]) - float(b["top"])) > 2.0)
    return len(pairs) >= 30 and down >= 0.6 * len(pairs)


def _turned(data: bytes) -> bytes:
    """The PDF turned whichever way puts real words on page 1. Returns the
    input when neither way does."""
    import io

    import pdfplumber
    import pypdf

    best, best_hits = data, 0
    for angle in (90, 270):
        reader = pypdf.PdfReader(io.BytesIO(data))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            page.rotate(angle)
            page.transfer_rotation_to_content()
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        with pdfplumber.open(io.BytesIO(buf.getvalue())) as turned:
            words = " ".join(w["text"] for w in turned.pages[0].extract_words())
        hits = len(_READABLE.findall(words))
        if hits > best_hits:
            best, best_hits = buf.getvalue(), hits
    return best


def upright_pdf(data: bytes) -> bytes:
    """The PDF with its text the right way up, for reading by position.

    AIOCD's ERP lays a landscape bill sideways on a portrait page: every
    character is drawn rotated a quarter turn. A plain text dump copes, but a
    reader that rebuilds columns from x and y sees the whole page sideways -
    "TNUOMA" for AMOUNT - and finds no table at all. Turning the page so the
    text stands up puts every word back where the eye sees it.

    Which way to turn is decided by which reading has words in it. Returns the
    input untouched when the text already stands up, or when nothing helps.
    """
    import io

    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not pdf.pages or not _is_sideways(pdf.pages[0]):
                return data
        return _turned(data)
    except Exception as exc:  # noqa: BLE001 - reading it as it is is no worse than before
        log.info("invoice_parser: could not turn the page upright (%s)", exc)
        return data


def _stream_text(data: bytes) -> str:
    """Page 1 in the PDF's own drawing order (see parties.resolve). Empty when
    it cannot be read - the layout text is then used alone."""
    import io

    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        return reader.pages[0].extract_text() or "" if reader.pages else ""
    except Exception:  # noqa: BLE001
        return ""


def parse_invoice_pdf(data: bytes, read_every_page: bool = False,
                      _turned_already: bool = False) -> Optional[dict]:
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
    party_text = ""
    page_items: Dict[int, List[dict]] = {}
    labels: dict = {}
    meta = None
    copies = 1
    stated_count = None
    full_text = ""
    interstate: Optional[bool] = None
    local_head: Optional[str] = None

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total_pages = len(pdf.pages)
            # Checked on the PDF already open - opening it a second time to look
            # cost a third of a second on every bill. A sideways page is turned
            # upright and read again from the start.
            if not _turned_already and total_pages and _is_sideways(pdf.pages[0]):
                turned = _turned(data)
                if turned is not data:
                    return parse_invoice_pdf(turned, read_every_page, _turned_already=True)

            # Read only the first printed copy. Settled by comparing two pages
            # rather than by reading every page's text, which was the single
            # slowest step of an upload: eight seconds on a 33-page invoice.
            copies = 1 if read_every_page else _detect_copies(pdf)
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
            carried_layout = None
            last_table_page = None

            for page_no in wanted:
                page = without_watermark(pdf.pages[page_no])
                # Same tolerance as the table reader, so header fields do not
                # arrive run together on PDFs that carry no space characters.
                page_texts[page_no] = page.extract_text(x_tolerance=WORD_TOLERANCE) or ""
                if meta is None:
                    regions = party_regions(page, WORD_TOLERANCE)
                    party_text = "\n".join(regions.values())
                    meta = _extract_header_meta(
                        page_texts[page_no],
                        supplier_region_lines(page),
                        regions,
                    )
                    # Decided once, from the two GSTINs: an intra-state sale is
                    # CGST + SGST, an inter-state one is IGST. Suppliers head a
                    # single column for both, so the heading alone cannot say.
                    local_head = local_tax_head(
                        (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                        (meta.get("bill_to", {}).get("gstin") or {}).get("value"),
                    )
                    interstate = is_interstate(
                        (meta.get("supplier", {}).get("gstin") or {}).get("value"),
                        (meta.get("bill_to", {}).get("gstin") or {}).get("value")
                        or (meta.get("ship_to", {}).get("gstin") or {}).get("value"),
                    )
                    if interstate is None:
                        # Who is who may still be unsettled here, but when every
                        # valid GSTIN on the page is of one state the sale is
                        # within it (Wockhardt: its own and the buyer's, both 27).
                        from app.services.ocr.parties import gstins_on_page

                        on_page = {g for _, g in gstins_on_page(page_texts[page_no])}
                        if len(on_page) >= 2 and len({g[:2] for g in on_page}) == 1:
                            interstate = False
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
                ruled_items = _rows_from_tables(page.extract_tables() or [], ruled_labels, interstate, local_head)
                word_items = _rows_from_tables(extract_word_tables(page), word_labels, interstate, local_head)
                if not word_items and not ruled_items and last_table_page is not None:
                    # A continuation page with no header of its own reads
                    # through the columns of the last page that had one -
                    # worked out only now, when a page needs it.
                    if carried_layout is None:
                        carried_layout = page_layout(without_watermark(pdf.pages[last_table_page])) or False
                    if carried_layout:
                        word_items = _rows_from_tables(extract_word_tables(page, carried_layout),
                                                       word_labels, interstate, local_head)
                elif word_items or ruled_items:
                    if last_table_page != page_no:
                        carried_layout = None
                    last_table_page = page_no
                if _better_reading(word_items, ruled_items):
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

            line_items.extend(_join_page_lines([page_items.get(page_no, []) for page_no in wanted]))
            # A totals row printed on its own page (Cipla Pharma) is only
            # recognisable against the whole bill's lines.
            line_items = _without_totals_row(line_items)
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
        fields.setdefault("invoice", {})["total_amount"] = _f(_whole_bill_total(full_text))
    _total_from_words_if_missing(fields, full_text)
    # Nor will references printed only on a later page: Kanchan and Zydus print
    # their IRN in the foot of the last page. Read from every page of the copy,
    # filling only what the first page left blank.
    for key, value in extract_references(full_text).items():
        if value and not (fields.setdefault("invoice", {}).get(key) or {}).get("value"):
            fields["invoice"][key] = _f(value)
    # ...and the supplier's e-mail, which JB prints in its last page's footer.
    # An address that sits in the buyer's blocks is the buyer's.
    supplier = fields.setdefault("supplier", {})
    if not (supplier.get("email") or {}).get("value"):
        for m in _EMAIL_ANYWHERE.finditer(full_text):
            if m.group(1).lower() not in party_text.lower():
                supplier["email"] = _f(m.group(1))
                break
    # Any invoice-level total the bill did not print is summed from the lines,
    # so the tax split always reaches the shop's accounts.
    invoice_meta = fields.setdefault("invoice", {})
    _labelled_totals(invoice_meta, full_text)
    _slab_tax_totals(invoice_meta, full_text)
    _summary_tax_totals(invoice_meta, full_text)
    _taxable_from_sub_total(invoice_meta, full_text)
    slab_summary = _slab_summary_table(invoice_meta, full_text)
    _gst_total_from_heads(invoice_meta)
    _total_by_cross_foot(invoice_meta, full_text, line_items)
    _drop_impossible_tax_total(invoice_meta)
    _scale_worked_out_tax(line_items, invoice_meta)
    for key, value in sum_line_totals(line_items).items():
        if not (invoice_meta.get(key) or {}).get("value"):
            # Added up from the lines, not read off the bill - held below a
            # printed figure, so nothing mistakes it for the bill's own total.
            invoice_meta[key] = _f(value, confidence=_SUMMED_CONFIDENCE)

    fields["line_items"] = line_items
    fields["_hints"] = {
        "stream_text": _stream_text(data),
        "copies_detected": copies,
        "stated_item_count": stated_count,
        "price_labels": labels,
        "total_in_words": total_from_words(full_text),
        "slab_summary": slab_summary,
        "document_text": full_text,
    }
    return fields
