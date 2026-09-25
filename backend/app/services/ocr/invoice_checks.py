"""Invoice integrity checks applied to EVERY extraction, from either tier.

The pharmacist's trust in this app rests on one promise: the line list we show
matches the paper. These checks enforce that promise:

  * `dedupe_line_items` collapses rows that repeat verbatim. The usual cause is
    a GST invoice printed three times in one PDF (Original / Duplicate /
    Triplicate) - a 143-item invoice arriving as 429 rows.
  * `reconcile_invoice` adds up the line amounts and compares them with the
    total printed on the invoice, and with the item count the invoice states.
    A mismatch becomes a loud warning instead of silently wrong stock data.

Nothing here deletes data quietly: every collapse and every mismatch is
reported back through `meta` so the UI can show it.
"""
import re
from typing import List, Optional, Tuple

# A line total may legitimately differ from the invoice total by round-off,
# freight, or a cash discount applied at the foot of the bill.
_TOTAL_TOLERANCE_ABS = 5.0
_TOTAL_TOLERANCE_PCT = 0.02


def _v(field) -> str:
    """The trimmed string value of a {value, confidence} leaf."""
    if not isinstance(field, dict):
        return ""
    return str(field.get("value") or "").strip()


def _num(field) -> Optional[float]:
    raw = _v(field).replace(",", "")
    m = re.search(r"-?\d*\.?\d+", raw)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _signature(item: dict) -> tuple:
    """Identity of a line row. Deliberately wide: a row only counts as a repeat
    when the medicine, batch, quantity AND money all match exactly, which is
    what a reprinted copy of the same invoice looks like."""
    desc = re.sub(r"\s+", " ", _v(item.get("description"))).lower()
    return (
        desc,
        _v(item.get("batch_no")).lower(),
        _v(item.get("expiry")),
        _v(item.get("quantity")),
        _v(item.get("free_quantity")),
        _v(item.get("mrp")),
        _v(item.get("rate")),
        _v(item.get("amount")),
    )


def dedupe_line_items(items: List[dict]) -> Tuple[List[dict], int]:
    """Return (unique_items_in_original_order, removed_count).

    Identical rows are collapsed to the first occurrence. A row with no
    distinguishing detail at all (description only) is left untouched, because
    a bare repeated name is not enough evidence that it is a reprint.
    """
    if not items:
        return items or [], 0

    seen: dict = {}
    unique: List[dict] = []
    removed = 0
    for item in items:
        sig = _signature(item)
        # Require at least one corroborating field beyond the description.
        if not any(sig[1:]):
            unique.append(item)
            continue
        if sig in seen:
            removed += 1
            continue
        seen[sig] = True
        unique.append(item)
    return unique, removed


def validate_line_arithmetic(items: List[dict], low_confidence: float = 0.4) -> int:
    """Flag rows whose quantity x rate is nowhere near the line amount.

    That is the signature of a column read off by one - the cause of "the 2nd
    product's qty is perfect but the others are wrong". We do not correct the
    value (we never invent data); we drop its confidence so the row surfaces
    under 'Needs check' in review.

    Returns the number of rows flagged.
    """
    flagged = 0
    for item in items:
        qty, rate, amount = _num(item.get("quantity")), _num(item.get("rate")), _num(item.get("amount"))
        if not qty or not rate or not amount or amount <= 0:
            continue
        expected = qty * rate
        if expected <= 0:
            continue
        ratio = amount / expected
        # Discounts/schemes shrink the amount, GST-inclusive amounts inflate it;
        # only a gross mismatch indicates a misread column.
        if 0.5 <= ratio <= 1.5:
            continue
        flagged += 1
        for key in ("quantity", "rate", "amount"):
            leaf = item.get(key)
            if isinstance(leaf, dict) and leaf.get("value") not in (None, ""):
                leaf["confidence"] = min(leaf.get("confidence") or 1.0, low_confidence)
    return flagged


def _fmt(amount: float) -> str:
    return f"{amount:.2f}"


# --------------------------- which price was billed ---------------------------
# Candidate price columns, and the label to show when one of them turns out to
# be the rate the line was actually billed at.
_PRICE_FIELDS = ("rate", "ptr", "pts", "mrp")
_DEFAULT_LABELS = {"rate": "RATE", "ptr": "PTR", "pts": "PTS", "mrp": "MRP"}


def _billed_unit(item: dict) -> Optional[float]:
    """What this line was actually charged per unit: amount / quantity."""
    qty, amount = _num(item.get("quantity")), _num(item.get("amount"))
    if not qty or amount is None:
        return None
    return amount / qty


# A line discount pulls the amount below the printed price. Anything cheaper
# than this is a different column, not a discount.
_MAX_LINE_DISCOUNT = 0.30
# Allow a hair above 1.0 for rounding in the printed amount.
_MAX_PRICE_RATIO = 1.005


def _matching_price_field(item: dict, unit: float) -> Optional[str]:
    """Which printed price column best explains amount / quantity.

    Not an exact match: a line discount means the amount lands *below* the price
    it was struck from (Kanchan bills PTS less 2%, and heads its discount column
    just "%", so it cannot be found by name). The billed column is therefore the
    printed price the unit comes closest to without exceeding it - an exact
    match scores 1.0 and wins outright.
    """
    best, best_ratio = None, 0.0
    for field in _PRICE_FIELDS:
        price = _num(item.get(field))
        if not price:
            continue
        ratio = unit / price
        if 1 - _MAX_LINE_DISCOUNT <= ratio <= _MAX_PRICE_RATIO and ratio > best_ratio:
            best, best_ratio = field, ratio
    return best


def resolve_billed_rate(items: List[dict], labels: Optional[dict] = None) -> Optional[str]:
    """Set each line's `rate` to the price it was actually billed at.

    A pharmacy invoice prints MRP, PTR, PTS and sometimes a named rate column
    (JB prints "Rate", Bharat prints "NIR"), and WHICH of them the bill is
    charged on varies by supplier and by who the buyer is - a stockist is billed
    on PTS, a retailer on PTR. Column-name precedence cannot know that, and got
    it wrong on the very invoice a user complained about: every Zydus line is
    charged on PTS, and we were labelling it PTR.

    Arithmetic does know it: amount / quantity IS the billed rate. We match that
    against the printed columns, take a majority vote across the invoice, and
    apply the winning column to every line - so one line with an odd discount
    cannot make a single invoice report two different kinds of price.

    Returns the field that won, or None when nothing could be resolved.
    """
    labels = {**_DEFAULT_LABELS, **(labels or {})}
    votes: dict = {}
    for item in items:
        unit = _billed_unit(item)
        if unit is None:
            continue
        field = _matching_price_field(item, unit)
        if field:
            votes[field] = votes.get(field, 0) + 1

    if not votes:
        # Nothing to reconcile against (no amounts, or none matched). `rate` is
        # still the connector contract, so fall back to the first printed price
        # column - and label it honestly rather than calling it "the rate".
        for item in items:
            for field in _PRICE_FIELDS:
                value = item.get(field)
                if isinstance(value, dict) and value.get("value") not in (None, ""):
                    item["rate"] = {"value": value["value"], "confidence": value.get("confidence", 1.0)}
                    item["rate_source"] = {"value": labels.get(field, field.upper()), "confidence": None}
                    break
        return None
    winner = max(votes, key=lambda f: (votes[f], -_PRICE_FIELDS.index(f)))
    label = labels.get(winner, winner.upper())

    for item in items:
        value = item.get(winner)
        if isinstance(value, dict) and value.get("value") not in (None, ""):
            item["rate"] = {"value": value.get("value"), "confidence": value.get("confidence", 1.0)}
            # A label, not a measurement: confidence None keeps it out of the
            # document's overall confidence score.
            item["rate_source"] = {"value": label, "confidence": None}
    return winner


def mark_free_supplies(items: List[dict]) -> int:
    """Read a zero-rated line as zero, not as missing data.

    Bharat bills a replacement stock line at no charge: the taxable value is
    printed blank, with 0.00 CGST and 0.00 SGST beside it. We read that exactly
    right and then reported it as "1 line(s) have no amount" - an error message
    for a line the invoice deliberately leaves empty. The tax columns are the
    evidence: blank amount plus zero tax is a free supply, so the amount is
    0.00. A blank amount with NO tax figures to corroborate it stays unknown and
    still warns, because then we genuinely could not read it.

    Returns how many lines were recognised as free supplies.
    """
    marked = 0
    for item in items:
        if _v(item.get("amount")):
            continue
        taxes = [_num(item.get(k)) for k in ("cgst_amount", "sgst_amount", "igst_amount")]
        known = [t for t in taxes if t is not None]
        if not known or any(t != 0 for t in known):
            continue
        item["amount"] = {"value": "0.00", "confidence": 1.0}
        item["free_supply"] = {"value": "true", "confidence": None}
        marked += 1
    return marked


def _gross_total(items: List[dict]) -> Optional[float]:
    """Sum of line amounts with each line's own GST added back.

    Indian invoices print a tax-inclusive grand total; the line `amount` is the
    taxable value. This is the number that should match it.
    """
    total = 0.0
    seen = False
    for item in items:
        amount = _num(item.get("amount"))
        if amount is None:
            continue
        seen = True
        gst = _num(item.get("gst_percent")) or 0.0
        total += amount * (1 + gst / 100.0)
    return round(total, 2) if seen else None


def reconcile_invoice(fields: dict, stated_item_count: Optional[int] = None) -> dict:
    """Cross-check the extracted lines against the invoice's own totals.

    Returns a dict of meta keys plus a `warnings` list. Never mutates values.
    """
    items = fields.get("line_items") or []
    invoice = fields.get("invoice") or {}

    amounts = [_num(i.get("amount")) for i in items]
    have = [a for a in amounts if a is not None]
    line_total = round(sum(have), 2) if have else None
    gross_total = _gross_total(items)
    printed_total = _num(invoice.get("total_amount"))

    warnings: List[str] = []
    reconciles: Optional[bool] = None

    if line_total is not None and printed_total:
        # An Indian invoice prints a GST-INCLUSIVE grand total, while each line's
        # amount is its taxable value. Comparing the two directly reports a ~12%
        # shortfall on a perfectly good invoice - a false alarm on almost every
        # bill, which would make the one signal a pharmacist relies on worthless.
        # So a bill reconciles if the printed total matches either the taxable
        # sum or that sum plus the per-line GST.
        bases = [("taxable", line_total)]
        if gross_total is not None:
            bases.append(("with GST", gross_total))
        tolerance = max(_TOTAL_TOLERANCE_ABS, printed_total * _TOTAL_TOLERANCE_PCT)
        matched = next((name for name, value in bases if abs(value - printed_total) <= tolerance), None)
        reconciles = matched is not None
        if not reconciles:
            best = min(bases, key=lambda b: abs(b[1] - printed_total))
            warnings.append(
                f"Line items add up to {_fmt(best[1])} but the invoice total reads "
                f"{_fmt(printed_total)}. Please check the items before approving."
            )
    elif printed_total is None:
        warnings.append("Invoice total could not be read - please enter it before approving.")

    if stated_item_count and items and stated_item_count != len(items):
        warnings.append(
            f"The invoice states {stated_item_count} items but {len(items)} were read. "
            "Please check for missing or repeated lines."
        )

    if have and len(have) < len(items):
        warnings.append(f"{len(items) - len(have)} line(s) have no amount.")

    return {
        "line_items_total": _fmt(line_total) if line_total is not None else None,
        "line_items_total_with_gst": _fmt(gross_total) if gross_total is not None else None,
        "total_reconciles": reconciles,
        "stated_item_count": stated_item_count,
        "warnings": warnings,
    }
