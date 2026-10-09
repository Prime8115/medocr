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

# How far the total built from the lines may sit from the printed total and
# still be the same bill: the grand total's rounding to the rupee, plus each
# line's GST rounded to the paisa, plus a hair for very large bills. Kept tight
# on purpose - it used to be max(5 rupees, 2%), under which a 2,000-rupee
# misread on a 1-lakh bill "reconciled". Freight, cash discounts and other foot
# adjustments are NOT absorbed here: a bill that has them is flagged for the
# reviewer, never passed by widening the margin.
_TOTAL_TOLERANCE_ABS = 1.5
_TOTAL_TOLERANCE_PER_LINE = 0.05
_TOTAL_TOLERANCE_PCT = 0.001


def total_tolerance(total: float, line_count: int) -> float:
    """The largest gap between lines and a printed total that is still round-off."""
    return max(_TOTAL_TOLERANCE_ABS + _TOTAL_TOLERANCE_PER_LINE * max(line_count, 0),
               abs(total) * _TOTAL_TOLERANCE_PCT)

# An amount in words carries no paise, so "one lakh ... only" against
# 1,190,128.90 is agreement, not a contradiction. Anything beyond a rupee is the
# bill genuinely disagreeing with itself.
_WORDS_TOLERANCE = 1.0


def _v(field) -> str:
    """The trimmed string value of a {value, confidence} leaf."""
    if not isinstance(field, dict):
        return ""
    return str(field.get("value") or "").strip()


# The confidence the parser gives a bill total it SUMMED from the lines rather
# than read off the page. An exact marker, not a threshold: the AI reports its
# own readings at 0.9 and the like, and those are printed figures.
SUMMED_CONFIDENCE = 0.849


def _derived(field) -> bool:
    """A bill total the parser added up from the lines, not one the bill prints."""
    conf = (field or {}).get("confidence") if isinstance(field, dict) else None
    return conf is not None and abs(float(conf) - SUMMED_CONFIDENCE) < 1e-9


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


# How far quantity x rate may stray from the line amount and still be the same
# line. A discount or scheme shrinks the amount; a GST-inclusive amount inflates
# it. Outside this band the columns were misread. Named because the table-source
# choice in invoice_parser scores candidate readings by the same test - one
# definition of "this row's arithmetic works", used everywhere.
LINE_RATIO_LOW = 0.5
LINE_RATIO_HIGH = 1.5


def line_arithmetic_holds(item: dict) -> Optional[bool]:
    """Whether quantity x unit price matches this line's amount.

    None when the row does not print enough to tell. The unit price is whichever
    of the price columns the row carries: `rate` is resolved later, from the
    whole invoice, so during parsing PTS or PTR is all there is to go on.
    """
    qty, amount = _num(item.get("quantity")), _num(item.get("amount"))
    price = next(
        (p for p in (_num(item.get("rate")), _num(item.get("pts")), _num(item.get("ptr")))
         if p),
        None,
    )
    if not qty or not price or not amount or amount <= 0:
        return None
    expected = qty * price
    if expected <= 0:
        return None
    return LINE_RATIO_LOW <= amount / expected <= LINE_RATIO_HIGH


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
        if LINE_RATIO_LOW <= ratio <= LINE_RATIO_HIGH:
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


def _discounted_gross(items: List[dict]) -> Optional[float]:
    """Each line less ITS OWN discount %, with its GST added back.

    Aurowin prints the amount before discount (quantity x rate) and a "DISC
    5.00" on every line, and charges GST on what is left: 27,485.60 less 5% =
    26,111.32, plus 5% GST = 27,417 - its total. Only lines whose discount
    leaves money changed count; None when no line carries a discount %.
    """
    total, any_discount, seen = 0.0, False, False
    for item in items:
        amount = _num(item.get("amount"))
        if amount is None:
            continue
        seen = True
        pct = _num(item.get("discount_percent"))
        taxable = _num(item.get("taxable_amount"))
        if pct and 0 < pct < 100 and (taxable is None or abs(taxable - amount) < 0.01):
            amount = amount * (1 - pct / 100.0)
            any_discount = True
        gst = _num(item.get("gst_percent")) or 0.0
        total += amount * (1 + gst / 100.0)
    return round(total, 2) if seen and any_discount else None


def _bill_tax(invoice: dict) -> Optional[float]:
    """The tax the bill states at its foot: the combined GST figure, or else the
    sum of whichever heads it prints (CGST, SGST, IGST, UTGST)."""
    total = _num(invoice.get("total_gst_amount"))
    if total is not None and not _derived(invoice.get("total_gst_amount")):
        return total
    keys = ("total_cgst_amount", "total_sgst_amount", "total_igst_amount", "total_utgst_amount")
    # Heads the bill prints beat a GST total summed from the lines (Cosmin:
    # 18% of its undiscounted lines, where the bill charged it after 5% off).
    printed = [_num(invoice.get(k)) for k in keys if not _derived(invoice.get(k))]
    printed = [h for h in printed if h is not None]
    if printed:
        return round(sum(printed), 2)
    if total is not None:
        return total
    heads = [_num(invoice.get(k)) for k in keys]
    heads = [h for h in heads if h is not None]
    return round(sum(heads), 2) if heads else None


def _expected_totals(line_total: float, gross_total: Optional[float], invoice: dict) -> List[Tuple[str, float]]:
    """Every way the printed grand total can be built FROM THE LINES.

    An Indian invoice reaches its grand total from the line amounts through
    adjustments printed at its foot: GST on the taxable value, and often a
    trade or cash discount on the whole bill, applied BEFORE the tax. MSV
    Lifesciences: 15,909.00 in lines, less 10% trade discount (1,590.90),
    plus CGST 357.96 and SGST 357.96, rounded = 15,034.00. Comparing the lines
    with the total alone reported every such bill as wrong.

    Each candidate starts from the line sum, never from the bill's own summary
    figures alone - matching the total from those would hide exactly the
    missing or misread line this check exists to catch.
    """
    candidates = [("lines", line_total)]
    if gross_total is not None:
        candidates.append(("lines + per-line GST", gross_total))

    discount = _num(invoice.get("total_discount_amount"))
    discount = abs(discount) if discount else None   # printed as "(-)1,590.90" or "1,590.90"
    tax = _bill_tax(invoice)

    if discount:
        net = line_total - discount
        candidates.append(("lines - bill discount", net))
        if gross_total is not None and line_total:
            # Per-line GST, on lines reduced by the bill discount pro rata.
            candidates.append(("lines - bill discount + per-line GST", gross_total * net / line_total))
    if tax is not None:
        candidates.append(("lines + bill tax", line_total + tax))
        if discount:
            candidates.append(("lines - bill discount + bill tax", line_total - discount + tax))
        else:
            implied = _implied_discount(line_total, _num(invoice.get("total_taxable_amount")))
            if implied is not None:
                pct, amount = implied
                candidates.append((f"lines - {pct:g}% bill discount + bill tax", line_total - amount + tax))
    return [(name, round(value, 2)) for name, value in candidates]


# A bill-wide discount is a round rate: 10%, 2.5%. Anything else between the
# lines and the taxable value is more likely a missing or misread line.
_IMPLIED_DISCOUNT_RANGE = (0.5, 30.0)
_ROUND_RATE_TOLERANCE = 0.02   # percentage points


def _implied_discount(line_total: float, taxable: Optional[float]) -> Optional[Tuple[float, float]]:
    """(rate %, amount) of a bill discount the invoice applied but whose figure
    was not read - inferred from the gap between the lines and the printed
    taxable value, and accepted only if that gap is a round rate.

    The MSV photo: the AI read the taxable value 14,318.10 and both tax heads,
    but not the "Less Trade Discount (-)1,590.90" line. 15,909.00 - 14,318.10
    = 1,590.90 is exactly 10.00% of the lines, which no missing line would
    produce by chance - while the gap a dropped or misread line leaves almost
    never lands on a round rate, so that is still flagged.
    """
    if not taxable or not line_total or taxable >= line_total:
        return None
    gap = line_total - taxable
    pct = gap / line_total * 100
    nearest = round(pct * 2) / 2   # whole or half percent
    low, high = _IMPLIED_DISCOUNT_RANGE
    if low <= nearest <= high and abs(pct - nearest) <= _ROUND_RATE_TOLERANCE:
        return nearest, gap
    return None


# Invoice-level totals a bill prints besides its amount payable: "Total Amt :
# 99230.21" before a credit note brings AIOCD's payable to 95,055. Never a page
# sub-total or a carried-forward figure - those match a PARTIAL reading.
_PAGE_TOTAL = re.compile(
    r"(?<![a-z])(?:total\s*(?:amt|amount|value)|invoice\s*(?:value|total|amount)|gross\s*total|"
    r"bill\s*(?:amount|value)|net\s*value)\s*(?:\(\s*rs\.?\s*\))?\s*[:\-]?\s*(?:rs\.?|inr|₹)?\s*"
    r"(-?[\d,]+\.\d{2})",
    re.I,
)
_NOT_INVOICE_LEVEL = re.compile(r"(sub|page|carried|c\s*/\s*f|b\s*/\s*f|brought)\s*$", re.I)


def printed_invoice_totals(text: str) -> List[float]:
    """Every invoice-level total figure printed on the page."""
    out = []
    for m in _PAGE_TOTAL.finditer(text or ""):
        before = (text or "")[max(0, m.start() - 12):m.start()]
        if _NOT_INVOICE_LEVEL.search(before):
            continue
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return out


# A credit note the bill sets off against itself: "Less:CreditNotes 3,509.00-"
# (Raptakos), "Less C/N Amount: 6667.00" (Aristo). The payable plus it is the
# invoice's own value - what its lines and tax must build up to.
_CREDIT_NOTE_SET_OFF = re.compile(
    # ...and "CR Notes(-) 38,642.00" (Medley).
    r"(?:\bless\s*:?\s*(?:c\s*/\s*n|credit\s*notes?)|\bcn\s*/\s*dn|\bcr\s*notes?\s*\(\s*-\s*\))"
    r"\s*(?:amount|amt|value)?\s*[:\-]*\s*"
    r"(?:rs\.?|inr|₹)?\s*([\d,]+\.\d{2})",
    re.I,
)


_MONEY_FIGURE = re.compile(r"(?<![\d.,])([\d,]+\.\d{2})(?![\d%])")


def tds_deducted(text: str) -> List[float]:
    """TDS amounts the bill prints (income tax the buyer withholds): every
    figure on a line after "TDS" - Mankind's "TDS to be Deducted 0.1- % on
    7674.52 8.00" names its base first and the TDS last."""
    out = []
    for line in (text or "").splitlines():
        at = re.search(r"\btds\b", line, re.I)
        if not at:
            continue
        for m in _MONEY_FIGURE.finditer(line[at.end():]):
            value = float(m.group(1).replace(",", ""))
            if 0 < value and value not in out:
                out.append(value)
    return out


def credit_notes_set_off(text: str) -> List[float]:
    """Each credit-note amount the bill deducts from its own value."""
    out = []
    for m in _CREDIT_NOTE_SET_OFF.finditer(text or ""):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return out


def reconcile_invoice(fields: dict, stated_item_count: Optional[int] = None,
                      total_in_words: Optional[str] = None,
                      page_text: Optional[str] = None,
                      slab_summary: Optional[dict] = None) -> dict:
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
    reconciled_by: Optional[str] = None
    built_from_lines: Optional[float] = None

    if line_total is not None and printed_total:
        candidates = _expected_totals(line_total, gross_total, invoice)
        discounted = _discounted_gross(items)
        if discounted is not None:
            candidates.append(("lines less their own discount % + per-line GST", discounted))
        # The lines' own printed net amounts, tax included (Sun prints only
        # these, with no tax columns). Every line must carry one.
        nets = [_num(i.get("net_amount")) for i in items]
        if nets and all(n is not None for n in nets):
            candidates.append(("the lines' net amounts (tax included)", round(sum(nets), 2)))
        tolerance = total_tolerance(printed_total, len(items))
        # The closest candidate decides, so the one reported is the real build-up.
        best_name, best_value = min(candidates, key=lambda c: abs(c[1] - printed_total))
        built_from_lines = best_value
        reconciles = abs(best_value - printed_total) <= tolerance
        if reconciles:
            reconciled_by = best_name
        else:
            # The invoice's own printed TAXABLE total is a second thing the lines
            # can be checked against, and it still starts from the line sum - so
            # it cannot hide a missing line, which is what this check is for.
            #
            # A scan needs it. OCR reads the money column reliably but often
            # misses the narrow CGST/SGST columns, so the tax cannot be added
            # back and the grand total is unreachable. Kanchan scanned: all 12
            # lines read correctly, summing to exactly the 102,864.00 the bill
            # prints as its basic amount, yet rejected for want of a tax column.
            printed_taxable = _num(invoice.get("total_taxable_amount"))
            # Only a figure the bill PRINTS. A taxable total the parser summed
            # from these very lines always "matches" them - AANAV reconciled
            # on 1 line of its 14 that way.
            if printed_taxable and not _derived(invoice.get("total_taxable_amount")):
                taxable_tolerance = total_tolerance(printed_taxable, len(items))
                if abs(line_total - printed_taxable) <= taxable_tolerance:
                    reconciles = True
                    reconciled_by = "the invoice's printed taxable total"

        if not reconciles and page_text:
            # The bill may print its invoice value AND a smaller amount payable
            # after a credit note or advance it settles on the same bill. Lines
            # that build up exactly to a printed invoice-level total are proven;
            # the step down to the payable is the bill's own, and is said.
            invoice_values = printed_invoice_totals(page_text) + [
                round(printed_total + cn, 2)
                # ...and the TDS the buyer withholds (Mankind: 8,058 less 8.00 TDS).
                for cn in credit_notes_set_off(page_text) + tds_deducted(page_text)]
            for figure in invoice_values:
                # The payable itself again - not a within-tolerance figure:
                # Mankind's TDS is 0.1%, as small as the round-off allowance.
                if abs(figure - printed_total) < 0.01:
                    continue
                # Only a build-up WITH tax is an invoice value: Sun prints its
                # taxable "Total Value" too, and the lines alone matched it -
                # the GST then read as a 549,297-rupee "deduction".
                hit = next(((n, v) for n, v in candidates
                            if ("gst" in n.lower() or "tax" in n.lower())
                            and abs(v - figure) <= total_tolerance(figure, len(items))), None)
                if hit:
                    reconciles = True
                    reconciled_by = f"{hit[0]} = the invoice value the bill prints ({_fmt(figure)})"
                    # After the bill's own adjustment, the lines build up to
                    # what it asks for - which is what its words spell out.
                    built_from_lines = printed_total + (hit[1] - figure)
                    warnings.append(
                        f"The bill's lines and tax come to {_fmt(figure)}, which it prints as its "
                        f"invoice value; the amount payable is {_fmt(printed_total)} - "
                        f"{_fmt(abs(figure - printed_total))} the bill itself "
                        f"{'deducts' if figure > printed_total else 'adds'} (a credit note, advance "
                        "or other adjustment). Please confirm it."
                    )
                    break

        if not reconciles and slab_summary and slab_summary.get("lines_amount"):
            # The bill's own GST summary: its Amount column is exactly these
            # lines, and its taxable value and tax make its total. Hindustan
            # Capsule's credit note takes its scheme amount off one slab and
            # not the other, so no single rule leads from the lines to its
            # taxable value - but every figure is the bill's own, and the lines
            # are exactly the ones it summarises.
            summary_total = slab_summary.get("total_taxable_amount", 0) + slab_summary.get("tax", 0)
            if abs(slab_summary["lines_amount"] - line_total) <= total_tolerance(line_total, len(items)) \
                    and abs(summary_total - printed_total) <= tolerance:
                reconciles = True
                reconciled_by = "the bill's GST summary (its Amount column is the lines; its taxable value and tax give the total)"
                built_from_lines = round(summary_total, 2)

        if not reconciles:
            # Name the gap. "Short by 12,344.00" is what sends a reviewer to the
            # line that is wrong; two totals alone leave them doing the
            # subtraction themselves on every mismatched bill.
            gap = printed_total - best_value
            direction = "short by" if gap > 0 else "over by"
            warnings.append(
                f"Line items add up to {_fmt(line_total)} but the invoice total is "
                f"{_fmt(printed_total)} - {direction} {_fmt(abs(gap))}, after the discount "
                f"and tax the bill states ({best_name}). "
                "Please check the items before approving."
            )
    elif printed_total is None:
        # No grand total to hold the lines to - but a printed TAXABLE total
        # is still the bill's own figure, and the lines must reach it.
        printed_taxable = _num(invoice.get("total_taxable_amount"))
        if line_total is not None and printed_taxable and not _derived(invoice.get("total_taxable_amount")):
            if abs(line_total - printed_taxable) <= total_tolerance(printed_taxable, len(items)):
                reconciles = True
                reconciled_by = "the invoice's printed taxable total"
        warnings.append("Invoice total could not be read - please enter it before approving.")

    # The bill against itself. An Indian tax invoice states its total twice: in
    # words, and implicitly in the figures that build it up. Abbott's lines plus
    # its own CGST and SGST come to 144,144.00, while it spells out one lakh
    # forty four thousand SIXTY EIGHT - exactly 76 less. Rather than quietly
    # pick a side on a number the pharmacy is about to pay, say so.
    #
    # Compared against the build-up from the LINES, not against a label-matched
    # figure: the words are what the bill asserts, and the lines plus the tax it
    # states are what it adds up to. A difference beyond a rupee - the paise the
    # words never carry - is the bill disagreeing with itself.
    # Reported, not warned about. The gap that matters here is tiny - Abbott's
    # is 76 rupees in 144,144 - and at that size it cannot be told apart from
    # our own shortfall when a per-line tax cell does not read (Menarini's lines
    # reach 99.85% of its stated tax). A warning on either would cry wolf on
    # both, so the figure is surfaced beside the total instead and the reviewer
    # sees what the bill says in its own words.
    spelled = _num({"value": total_in_words}) if total_in_words else None
    words_disagrees = bool(
        spelled and built_from_lines
        and abs(spelled - built_from_lines) > _WORDS_TOLERANCE
    )
    if words_disagrees and page_text:
        # The bill may spell out what is left after the TDS the BUYER deducts:
        # Eris's lines and tax come to 21,607.58, it prints "TDS 20.58-", and
        # its words say Twenty One Thousand Five Hundred Eighty Seven. That is
        # the bill agreeing with itself, and is said rather than flagged.
        for tds in tds_deducted(page_text):
            if abs(spelled - (built_from_lines - tds)) <= _WORDS_TOLERANCE:
                words_disagrees = False
                warnings.append(
                    f"The bill spells out {_fmt(spelled)}: its value {_fmt(built_from_lines)} less "
                    f"{_fmt(tds)} TDS, which the buyer deducts and pays to the government.")
                break

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
        "total_in_words": _fmt(spelled) if spelled is not None else None,
        "total_in_words_disagrees": words_disagrees,
        # What the bill's own lines and stated tax add up to - the other side of
        # a words-versus-figures disagreement, offered to the reviewer as a choice.
        "total_built_from_lines": _fmt(built_from_lines) if built_from_lines is not None else None,
        "total_reconciles": reconciles,
        "total_reconciled_by": reconciled_by,
        "stated_item_count": stated_item_count,
        # Kept with the reading, so a re-check after an edit still has it.
        "slab_summary": slab_summary,
        "warnings": warnings,
    }


_PARTY_LABELS = {"supplier": "supplier", "bill_to": "Bill-to", "ship_to": "Ship-to"}


def flag_invalid_gstins(fields: dict) -> List[str]:
    """Warn about every GSTIN that fails its check character, and lower its
    confidence so the review screen highlights it.

    Shape alone is not enough: the AI copied the MSV invoice's garbled scanner
    text ("33ABEFM0315R128") straight into the supplier GSTIN, where it would
    have been filed against the wrong party. Never changes the value - only the
    pharmacist, looking at the paper, can say what it should be.
    """
    from app.services.ocr.invoice_header import gstin_is_valid

    warnings: List[str] = []
    for party, label in _PARTY_LABELS.items():
        leaf = (fields.get(party) or {}).get("gstin")
        value = _v(leaf)
        if value and not gstin_is_valid(value):
            leaf["confidence"] = min(leaf.get("confidence") or 0.3, 0.3)
            warnings.append(f"The {label} GSTIN {value} is not a valid GSTIN - please check it against the invoice.")
    return warnings


# ------------------------- fields the bill already states -------------------------
# GSTIN = 2-digit state code + the holder's 10-character PAN + entity + Z + check.
_GSTIN = re.compile(r"^\d{2}([A-Z]{5}\d{4}[A-Z])[A-Z0-9]Z[A-Z0-9]$")
# Union Territories without a legislature, where UTGST replaces SGST.
_UT_STATE_CODES = frozenset({"04", "25", "26", "31", "35", "38"})
_HEADS = ("cgst", "sgst", "igst", "utgst")


def _leaf(value: str, confidence: float = 1.0) -> dict:
    return {"value": value, "confidence": confidence}


# "SGST/UTGST", "SGST / UTGST", "UTGST/SGST": one column or line for the two.
# No word boundary at the ends: a scan's table rules run into the heading
# (MSV's OCR reads "—SSGST/UTGST__").
_COMBINED_SGST_UTGST = re.compile(r"S\s*GST\s*/\s*UT\s*GST|UT\s*GST\s*/\s*S\s*GST", re.I)


def show_combined_utgst(fields: dict, page_text: str) -> bool:
    """Show a printed "SGST/UTGST" figure under UTGST as well as SGST.

    The client's rule: a value the bill prints is shown. MSV and Abbott print
    one combined "SGST/UTGST" figure (7,722.00 on Abbott); it was filed under
    SGST and UTGST read 0.00. Now UTGST carries the same printed figure, on
    the bill's totals and on each line.

    It is ONE tax, printed once, so it is counted once: the caller records
    meta.sgst_utgst_combined, and every sum of the tax heads (verify.py) then
    leaves UTGST out. The bill's total GST is fixed before this runs.
    Returns True when the bill prints the combined heading and a figure was shown.
    """
    if not _COMBINED_SGST_UTGST.search(page_text or ""):
        return False
    shown = False
    invoice = fields.get("invoice")
    if isinstance(invoice, dict) and _v(invoice.get("total_sgst_amount")):
        leaf = invoice["total_sgst_amount"]
        invoice["total_utgst_amount"] = {"value": leaf.get("value"), "confidence": leaf.get("confidence")}
        shown = True
    for item in fields.get("line_items") or []:
        # The line's SGST pair is mirrored whole - a blank included. Copying the
        # rate alone left MSV's lines, which print no tax amounts, showing
        # "UTGST 2.5%, 0.00" - a pair no line can have.
        sgst = {kind: item.get(f"sgst_{kind}") for kind in ("percent", "amount")}
        if not any(isinstance(leaf, dict) and _v(leaf) for leaf in sgst.values()):
            continue
        for kind, leaf in sgst.items():
            item[f"utgst_{kind}"] = ({"value": leaf.get("value"), "confidence": leaf.get("confidence")}
                                     if isinstance(leaf, dict) else {"value": None, "confidence": None})
        shown = True
    return shown


def drop_copied_pans(fields: dict, page_text: str) -> List[str]:
    """Blank a party's PAN that is only the middle of its GSTIN.

    The client's rule: a field the bill does not print stays blank. The model
    is told not to copy a PAN out of a GSTIN; this holds it to that where the
    page's own text can show what is printed. A PAN that appears on the page
    on its own (not inside a GSTIN) is kept. Returns the paths blanked.
    """
    from app.services.ocr.invoice_header import _GSTIN_SHAPE

    text = page_text or ""
    if not text.strip():
        return []
    blanked: List[str] = []
    for party in ("supplier", "bill_to", "ship_to"):
        block = fields.get(party)
        if not isinstance(block, dict):
            continue
        pan = _v(block.get("pan")).upper()
        gstin = _v(block.get("gstin")).upper().replace(" ", "")
        if not pan or gstin[2:12] != pan:
            continue
        printed = False
        for m in re.finditer(re.escape(pan), text, re.I):
            around = text[max(0, m.start() - 2):m.end() + 3].upper()
            if not _GSTIN_SHAPE.search(around):
                printed = True
                break
        if not printed:
            block["pan"] = {"value": None, "confidence": None}
            blanked.append(f"{party}.pan")
    return blanked


# A line's tax worked out from its printed rate rather than read: held below
# full confidence, the same as the table reader holds it (invoice_parser.py).
_DERIVED_CONFIDENCE = 0.85


def _lines_are_the_taxable_value(fields: dict) -> bool:
    """Whether each line's amount is its taxable value - no discount taken off
    the whole bill at its foot. MSV takes 10% off below the lines, so a line's
    tax there is NOT its amount at the rate."""
    invoice = fields.get("invoice") or {}
    items = fields.get("line_items") or []
    amounts = [_num(it.get("amount")) for it in items]
    if not items or any(a is None for a in amounts):
        return False
    taxable = _num(invoice.get("total_taxable_amount"))
    if taxable is not None:
        return abs(sum(amounts) - taxable) <= total_tolerance(taxable, len(items))
    discount = _num(invoice.get("total_discount_amount"))
    return not discount


def _complete_line_tax(item: dict, i: int, present: List[str], amounts_too: bool = True) -> List[str]:
    """A line's tax heads and net amount, from its printed GST rate.

    V. N. Pharma prints one "IGST %" column (18.00) and the tax only as a bill
    total; the AI read the rate as the line's GST % and left IGST % and amount
    and the net amount blank. What the bill fixes:
      * the rate splits by law - all IGST on an inter-state sale, half CGST and
        half SGST (or UTGST) within a state;
      * each head's amount is the line's taxable value at that rate;
      * the net amount is the taxable value plus its tax.
    Only blanks are filled. The head_totals check then holds the lines' tax
    against the bill's printed totals, so a misread rate is still caught.
    """
    filled: List[str] = []
    rate_leaf = item.get("gst_percent") or {}
    rate = _num(rate_leaf)
    if rate is not None and rate >= 0:
        share = rate if present == ["igst"] else rate / 2
        for head in present:
            key = f"{head}_percent"
            if not _v(item.get(key)):
                item[key] = _leaf(f"{share:.2f}", rate_leaf.get("confidence") or 1.0)
                filled.append(f"line_items[{i}].{key}")
    taxable = _num(item.get("amount"))
    if taxable is None or not amounts_too:
        return filled
    for head in present:
        pct, key = _num(item.get(f"{head}_percent")), f"{head}_amount"
        if pct is not None and not _v(item.get(key)):
            item[key] = _leaf(_fmt(round(taxable * pct / 100.0 + 1e-9, 2)), _DERIVED_CONFIDENCE)
            filled.append(f"line_items[{i}].{key}")
    if not _v(item.get("net_amount")):
        taxes = [item.get(f"{head}_amount") for head in present]
        if all(_num(t) is not None for t in taxes):
            sure = min([(t or {}).get("confidence") or 1.0 for t in taxes] or [1.0])
            item["net_amount"] = _leaf(_fmt(taxable + sum(_num(t) for t in taxes)), sure)
            filled.append(f"line_items[{i}].net_amount")
    return filled


def amounts_net_of_own_discount(fields: dict, page_text: Optional[str] = None) -> Optional[str]:
    """An AI-read line given its value BEFORE its own discount, corrected.

    The AI is asked for each line's taxable value. On Meridian's bill it gave
    Flatuna's "Total" column, 1,654.62 - exactly 22 x 75.21 - not its taxable
    value after the 9.09% discount, 1,504.22; every other line was right, and
    the bill came out 150 over. A line whose amount is exactly quantity x rate
    while it carries a discount of its own is taken net of that discount -
    and kept only when that makes the bill add up when it did not before.
    """
    import copy

    if reconcile_invoice(fields, page_text=page_text).get("total_reconciles") is not False:
        return None
    changes = {}
    for n, item in enumerate(fields.get("line_items") or []):
        qty, rate, amount = _num(item.get("quantity")), _num(item.get("rate")), _num(item.get("amount"))
        pct, off = _num(item.get("discount_percent")), _num(item.get("discount_amount"))
        if not (qty and rate and amount) or abs(qty * rate - amount) > max(0.05, 0.001 * amount):
            continue
        if off and 0 < off < amount:
            changes[n] = amount - off
        elif pct and 0 < pct < 100:
            changes[n] = amount * (1 - pct / 100.0)
    if not changes:
        return None
    # The fewest lines whose correction makes the bill add up - and only when
    # exactly one such set exists. Meridian's AI reading also put discounts on
    # lines the bill shows undiscounted; correcting all of them breaks it.
    from itertools import combinations

    def adds_up(subset) -> bool:
        trial = copy.deepcopy(fields)
        for n in subset:
            trial["line_items"][n]["amount"] = _leaf(f"{changes[n]:.2f}", _DERIVED_CONFIDENCE)
        return reconcile_invoice(trial, page_text=page_text).get("total_reconciles") is True

    found = None
    for size in range(1, min(3, len(changes)) + 1):
        hits = [s for s in combinations(sorted(changes), size) if adds_up(s)]
        if len(hits) > 1:
            return None   # more than one way to make it add up: not ours to choose
        if hits:
            found = hits[0]
            break
    if not found:
        return None
    for n in found:
        fields["line_items"][n]["amount"] = _leaf(f"{changes[n]:.2f}", _DERIVED_CONFIDENCE)
    lines = ", ".join(str(n + 1) for n in found)
    return (f"Line {lines}: the amount read was the value before the line's own discount; taken "
            "after it, the lines add up to the bill's total. Please check.")


def taxable_from_lines(fields: dict) -> Optional[str]:
    """An AI-read taxable value that is really the bill's GROSS value.

    Mednosis prints "Gross Amount 17228.00", a discount of 516.84, then the
    GST; the AI took 17,228 as the taxable value, and taxable + tax missed the
    total by the discount. The lines' own sum, 16,711.16, plus that same tax
    IS the total - so the lines' sum is the taxable value.
    """
    invoice = fields.get("invoice") or {}
    items = fields.get("line_items") or []
    taxable, total = _num(invoice.get("total_taxable_amount")), _num(invoice.get("total_amount"))
    tax = _bill_tax(invoice)
    amounts = [_num(i.get("amount")) for i in items]
    if not (taxable and total and tax) or not items or any(a is None for a in amounts):
        return None
    line_sum = round(sum(amounts), 2)
    if abs(taxable + tax - total) <= 1.0 or abs(line_sum + tax - total) > 1.0 or abs(line_sum - taxable) <= 1.0:
        return None
    invoice["total_taxable_amount"] = _leaf(f"{line_sum:.2f}", _DERIVED_CONFIDENCE)
    return (f"The taxable value read, {_fmt(taxable)}, with the tax does not make the bill's total; "
            f"the lines' own {_fmt(line_sum)} does - taken as the taxable value. Please check.")


def tax_heads_from_rates(fields: dict) -> Optional[str]:
    """Tax heads that break the bill's own arithmetic, replaced by the lines'
    rates when those restore it.

    Ferring prints two slabs per head (CGST 2.5% 423.89 and 6% 12,210.54); the
    AI reported only the 6% one, so the taxable value and the tax fell 848 short
    of the bill's total. The lines' own GST rates give 25,268.86, and the
    taxable value plus that IS the printed total - so that figure is taken,
    marked worked out, and said. Nothing changes unless the heads read fail
    taxable + tax = total and the rates make it hold to the rupee.
    """
    invoice = fields.get("invoice") or {}
    items = fields.get("line_items") or []
    taxable, total = _num(invoice.get("total_taxable_amount")), _num(invoice.get("total_amount"))
    heads = {h: _num(invoice.get(f"total_{h}_amount")) for h in _HEADS}
    read = sum(v for v in heads.values() if v)
    if not taxable or not total or not read or abs(taxable + read - total) <= 1.0 or not items:
        return None
    amounts = [_num(i.get("amount")) for i in items]
    rates = [_num(i.get("gst_percent")) for i in items]
    if any(a is None for a in amounts) or any(r is None for r in rates):
        return None
    line_sum = sum(amounts)
    if line_sum <= 0 or abs(line_sum - taxable) > 1.0:
        return None
    from_rates = round(sum(a * r / 100.0 for a, r in zip(amounts, rates)), 2)
    if abs(taxable + from_rates - total) > 1.0:
        return None
    inter = bool(heads.get("igst")) and not heads.get("cgst")
    if inter:
        invoice["total_igst_amount"] = _leaf(f"{from_rates:.2f}", _DERIVED_CONFIDENCE)
    else:
        half = f"{from_rates / 2:.2f}"
        local = "utgst" if heads.get("utgst") and not heads.get("sgst") else "sgst"
        invoice["total_cgst_amount"] = _leaf(half, _DERIVED_CONFIDENCE)
        invoice[f"total_{local}_amount"] = _leaf(half, _DERIVED_CONFIDENCE)
    invoice["total_gst_amount"] = _leaf(f"{from_rates:.2f}", _DERIVED_CONFIDENCE)
    return (f"The tax heads read come to {_fmt(read)}, which with the taxable value does not reach the "
            f"bill's total; the lines' own GST rates give {_fmt(from_rates)}, which does - taken from the "
            "rates. Please check the tax heads against the bill.")


def complete_from_the_bill(fields: dict) -> List[str]:
    """Fill fields that the bill states implicitly, for EITHER reader.

    None of this is guessed - each is a fact the printed invoice already fixes:

    * A party's PAN is NOT taken from its GSTIN. The client's rule is that a
      field the bill does not print stays blank: MSV prints both GSTINs and no
      PAN, and its PAN columns must be empty, not characters 3-12 of the GSTIN.
    * The total GST is the sum of the heads the bill prints.
    * A tax head that cannot apply to the sale is zero. An intra-state sale in a
      state carries no IGST and no UTGST; an inter-state one no CGST, SGST or
      UTGST. The tester read a blank UTGST on Tamil Nadu and Maharashtra bills
      as "missing" - it is 0.00, and saying so is accurate.

    Returns the dotted paths filled, for the log.
    """
    filled: List[str] = []

    invoice = fields.get("invoice")
    if not isinstance(invoice, dict):
        return filled

    supplier = _v((fields.get("supplier") or {}).get("gstin"))
    buyer = _v((fields.get("bill_to") or {}).get("gstin")) or _v((fields.get("ship_to") or {}).get("gstin"))
    if _GSTIN.match(supplier.upper()) and _GSTIN.match(buyer.upper()):
        inter = supplier[:2] != buyer[:2]
        ut = supplier[:2] in _UT_STATE_CODES
        if inter:
            absent = ("cgst", "sgst", "utgst")
        else:
            absent = ("igst", "sgst") if ut else ("igst", "utgst")
        # Only once the heads that DO apply are known - a zero beside a blank
        # would read as "this bill carries no tax".
        present = [h for h in _HEADS if h not in absent]
        if any(_num(invoice.get(f"total_{h}_amount")) is not None for h in present):
            for head in absent:
                key = f"total_{head}_amount"
                if not _v(invoice.get(key)):
                    invoice[key] = _leaf("0.00")
                    filled.append(f"invoice.{key}")
        # Each line's own tax, where the bill fixes it without printing it.
        amounts_too = _lines_are_the_taxable_value(fields)
        for i, item in enumerate(fields.get("line_items") or []):
            filled += _complete_line_tax(item, i, present, amounts_too)
        # ...and on each line whose applicable heads were read, so its UTGST %
        # and amount read 0 like the bill's total does, rather than blank.
        for i, item in enumerate(fields.get("line_items") or []):
            if not any(_num(item.get(f"{h}_{k}")) is not None
                       for h in present for k in ("percent", "amount")):
                continue
            for head in absent:
                for kind, zero in (("percent", "0"), ("amount", "0.00")):
                    key = f"{head}_{kind}"
                    if not _v(item.get(key)):
                        item[key] = _leaf(zero)
                        filled.append(f"line_items[{i}].{key}")

    # A bill-level discount the lines settle: zero when every line's discount
    # is printed as zero (Menarini's "0.00%") or its gross equals its taxable
    # value (V L's TOTAL BASIC = TAXABLE AMOUNT). Left blank, the export read it
    # as not captured.
    if not _v(invoice.get("total_discount_amount")):
        items = fields.get("line_items") or []

        def settled_zero(it: dict) -> bool:
            pct, amt = _num(it.get("discount_percent")), _num(it.get("discount_amount"))
            if amt is not None:
                return amt == 0
            if pct is not None:
                return pct == 0
            gross, taxable = _num(it.get("gross_amount")), _num(it.get("amount"))
            return gross is not None and taxable is not None and abs(gross - taxable) < 0.01

        if items and all(settled_zero(it) for it in items):
            invoice["total_discount_amount"] = _leaf("0.00")
            filled.append("invoice.total_discount_amount")

    if not _v(invoice.get("total_gst_amount")):
        heads = [_num(invoice.get(f"total_{h}_amount")) for h in _HEADS]
        known = [h for h in heads if h is not None]
        if known and any(known):
            invoice["total_gst_amount"] = _leaf(_fmt(sum(known)))
            filled.append("invoice.total_gst_amount")
    return filled
