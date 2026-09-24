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


def reconcile_invoice(fields: dict, stated_item_count: Optional[int] = None) -> dict:
    """Cross-check the extracted lines against the invoice's own totals.

    Returns a dict of meta keys plus a `warnings` list. Never mutates values.
    """
    items = fields.get("line_items") or []
    invoice = fields.get("invoice") or {}

    amounts = [_num(i.get("amount")) for i in items]
    have = [a for a in amounts if a is not None]
    line_total = round(sum(have), 2) if have else None
    printed_total = _num(invoice.get("total_amount"))

    warnings: List[str] = []
    reconciles: Optional[bool] = None

    if line_total is not None and printed_total:
        tolerance = max(_TOTAL_TOLERANCE_ABS, printed_total * _TOTAL_TOLERANCE_PCT)
        reconciles = abs(line_total - printed_total) <= tolerance
        if not reconciles:
            warnings.append(
                f"Line items add up to {_fmt(line_total)} but the invoice total reads "
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
        "total_reconciles": reconciles,
        "stated_item_count": stated_item_count,
        "warnings": warnings,
    }
