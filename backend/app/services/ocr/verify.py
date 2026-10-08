"""The verification layer: every extraction checked against itself.

An Indian tax invoice states most of its figures more than once - each line's
tax is its taxable value at a printed rate, the tax totals are the sum of the
lines, the grand total is the taxable value plus the tax, and the total is
spelled out again in words. A value that was misread breaks at least one of
those identities. This module runs all of them and reports, per check:

    pass     - the figures agree;
    fail     - they do not, and these fields are implicated;
    skipped  - the bill does not print what the check needs.

`skipped` is never `fail`: a bill that simply does not print a gross or a net
column must not be flagged for it, or the verdict would cry wolf and stop
being read.

The result is stored as `meta.verification`. Its `verdict` is "verified" only
with zero failures; anything else is "needs_check", and approval then requires
each failed check to be acknowledged against the paper.

This wraps the integrity checks in invoice_checks - it does not re-implement
them - and adds the identities those did not cover.
"""
import datetime as _dt
import re
from typing import Dict, Iterable, List, Optional

from app.services.ocr.invoice_header import gstin_is_valid

# The rates GST is actually charged at, whole and split into CGST + SGST halves.
_GST_RATES = (0.0, 0.1, 0.125, 0.25, 0.5, 1.0, 1.5, 2.5, 3.0, 5.0, 6.0, 7.5, 9.0,
              12.0, 14.0, 18.0, 28.0)
_HEADS = ("cgst", "sgst", "igst", "utgst")
# Confidence given to a field implicated by a failed check, so the review
# screen's existing "needs check" highlighting picks it up.
_FLAGGED_CONFIDENCE = 0.4

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


# ----------------------------------------------------------------- helpers ---

def _v(leaf) -> str:
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _n(leaf) -> Optional[float]:
    raw = _v(leaf).replace(",", "").replace("₹", "")
    m = re.search(r"-?\d*\.?\d+", raw)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, abs(b) * rel_tol)


def _year(y: int) -> int:
    return y + 2000 if y < 100 else y


def parse_month(text: str) -> Optional[tuple]:
    """(year, month) from any date form these invoices print, or None.

    Month precision is deliberate: expiries are printed as "Oct-27", "11/2026"
    or "31-May-27", and a month is all a pharmacy compares them by.
    """
    s = (text or "").strip().lower()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})", s)
    if m:
        return _year(int(m.group(3))), int(m.group(2))
    m = re.fullmatch(r"(?:\d{1,2}[\s./\-])?([a-z]{3})[a-z]*[\s./\-](\d{2,4})", s)
    if m and m.group(1) in _MONTHS:
        return _year(int(m.group(2))), _MONTHS[m.group(1)]
    m = re.fullmatch(r"(\d{1,2})[./\-](\d{2,4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return _year(int(m.group(2))), int(m.group(1))
    return None


class _Checks:
    """Collects check results and the fields each failure implicates."""

    def __init__(self) -> None:
        self.items: List[dict] = []

    def add(self, check_id: str, label: str, status: str, message: str = "",
            fields: Iterable[str] = ()) -> None:
        self.items.append({
            "id": check_id, "label": label, "status": status,
            "message": message, "fields": sorted(set(fields)),
        })


def _line(i: int, key: str) -> str:
    return f"line_items[{i}].{key}"


# ------------------------------------------------------------------ checks ---

def _check_reconciliation(c: _Checks, report: dict) -> None:
    ok = report.get("total_reconciles")
    if ok is True:
        c.add("total_reconciles", "Lines add up to the bill total", "pass",
              f"Matched as {report.get('total_reconciled_by') or 'the printed total'}.")
    elif ok is False:
        c.add("total_reconciles", "Lines add up to the bill total", "fail",
              f"The lines add up to {report.get('line_items_total')}, which does not reach "
              "the total printed on the bill.", ["invoice.total_amount"])
    else:
        c.add("total_reconciles", "Lines add up to the bill total", "fail",
              "The bill's total could not be read, so the lines cannot be checked against it.",
              ["invoice.total_amount"])


def _check_item_count(c: _Checks, report: dict, items: List[dict]) -> None:
    stated = report.get("stated_item_count")
    if not stated:
        c.add("item_count", "Item count matches the bill", "skipped")
    elif stated == len(items):
        c.add("item_count", "Item count matches the bill", "pass", f"{stated} items, as stated.")
    else:
        c.add("item_count", "Item count matches the bill", "fail",
              f"The bill states {stated} items; {len(items)} were read.")


def _check_gstins(c: _Checks, fields: dict) -> None:
    names = {"supplier": "Supplier", "bill_to": "Bill-to", "ship_to": "Ship-to"}
    for party, name in names.items():
        value = _v((fields.get(party) or {}).get("gstin"))
        check_id = f"gstin_{party}"
        if not value:
            c.add(check_id, f"{name} GSTIN is valid", "skipped")
        elif gstin_is_valid(value):
            c.add(check_id, f"{name} GSTIN is valid", "pass")
        else:
            c.add(check_id, f"{name} GSTIN is valid", "fail",
                  f"{value} fails the GSTIN check digit - a character was misread.",
                  [f"{party}.gstin"])


def _check_supplier_pan(c: _Checks, fields: dict) -> None:
    sup = fields.get("supplier") or {}
    gstin, pan = _v(sup.get("gstin")).upper(), _v(sup.get("pan")).upper()
    if not gstin or not pan or len(gstin) != 15:
        c.add("supplier_pan", "Supplier PAN matches its GSTIN", "skipped")
    elif gstin[2:12] == pan:
        c.add("supplier_pan", "Supplier PAN matches its GSTIN", "pass")
    else:
        c.add("supplier_pan", "Supplier PAN matches its GSTIN", "fail",
              f"PAN {pan} is not the one inside GSTIN {gstin}.",
              ["supplier.gstin", "supplier.pan"])


def _check_line_arithmetic(c: _Checks, items: List[dict]) -> None:
    bad, tested = [], 0
    for i, it in enumerate(items):
        qty, rate, amount = _n(it.get("quantity")), _n(it.get("rate")), _n(it.get("amount"))
        if not qty or not rate or not amount or amount <= 0:
            continue
        tested += 1
        # Discounts shrink a line, tax-inclusive amounts inflate it; only a gross
        # mismatch means a misread column (same band as invoice_checks).
        if not 0.5 <= amount / (qty * rate) <= 1.5:
            bad.append(i)
    _verdict(c, "line_arithmetic", "Quantity x rate matches each line", tested, bad,
             "quantity x rate is nowhere near the amount",
             lambda i: [_line(i, "quantity"), _line(i, "rate"), _line(i, "amount")])


def _check_line_tax(c: _Checks, items: List[dict]) -> None:
    bad, tested = [], 0
    for i, it in enumerate(items):
        taxable = _n(it.get("amount"))
        if not taxable:
            continue
        for head in _HEADS:
            rate, tax = _n(it.get(f"{head}_percent")), _n(it.get(f"{head}_amount"))
            if rate is None or tax is None or rate == 0:
                continue
            tested += 1
            if not _close(taxable * rate / 100.0, tax, 0.10, 0.01):
                bad.append((i, head))
    _verdict(c, "line_tax", "Each line's tax is its taxable value at the rate", tested,
             bad, "the tax is not the taxable value at the printed rate",
             lambda b: [_line(b[0], f"{b[1]}_amount"), _line(b[0], f"{b[1]}_percent"),
                        _line(b[0], "amount")],
             describe=lambda b: f"line {b[0] + 1} {b[1].upper()}")


def _check_line_net(c: _Checks, items: List[dict], combined: bool = False) -> None:
    # A bill printing one "SGST/UTGST" figure shows it under both heads; it is
    # one tax, so it is added once.
    heads = tuple(h for h in _HEADS if not (combined and h == "utgst"))
    bad, tested = [], 0
    for i, it in enumerate(items):
        net, taxable = _n(it.get("net_amount")), _n(it.get("amount"))
        taxes = [_n(it.get(f"{h}_amount")) for h in heads]
        known = [t for t in taxes if t is not None]
        # Only a net the bill PRINTED tests anything - one we added up passes
        # by construction.
        if net is None or taxable is None or not known or (it.get("net_amount") or {}).get(
                "confidence", 1.0) not in (None, 1.0):
            continue
        tested += 1
        if not _close(taxable + sum(known), net, 0.5, 0.005):
            bad.append(i)
    _verdict(c, "line_net", "Each line's net is its taxable value plus tax", tested, bad,
             "taxable value plus tax does not give the net amount",
             lambda i: [_line(i, "net_amount"), _line(i, "amount")])


def _check_line_discount(c: _Checks, items: List[dict]) -> None:
    bad, tested = [], 0
    for i, it in enumerate(items):
        gross, disc, taxable = (_n(it.get("gross_amount")), _n(it.get("discount_amount")),
                                _n(it.get("amount")))
        if gross is None or disc is None or taxable is None or disc == 0:
            continue
        tested += 1
        if not _close(gross - disc, taxable, 0.5, 0.005):
            bad.append(i)
    _verdict(c, "line_discount", "Gross less discount gives the taxable value", tested, bad,
             "gross less discount does not give the taxable value",
             lambda i: [_line(i, "gross_amount"), _line(i, "discount_amount"), _line(i, "amount")])


def _check_head_totals(c: _Checks, fields: dict, items: List[dict]) -> None:
    invoice = fields.get("invoice") or {}
    bad, tested = [], 0
    for head in _HEADS:
        printed = _n(invoice.get(f"total_{head}_amount"))
        line_values = [_n(it.get(f"{head}_amount")) for it in items]
        known = [v for v in line_values if v is not None]
        if printed is None or not known or len(known) < len(items) or printed == 0:
            continue
        tested += 1
        if not _close(sum(known), printed, 1.0, 0.002):
            bad.append(head)
    _verdict(c, "head_totals", "Tax totals equal the sum of the lines", tested, bad,
             "the printed total does not equal the lines' tax",
             lambda h: [f"invoice.total_{h}_amount"],
             describe=lambda h: h.upper())


def _check_rates_give_the_tax(c: _Checks, fields: dict, items: List[dict], combined: bool = False) -> None:
    """The lines' GST rates, applied to their taxable values, give the tax the
    bill states.

    Catches a misread RATE on a bill whose lines print no tax amounts - the
    case line_tax and head_totals cannot test. MSV on production: the AI read
    two 5% lines as 12% and 18%; every other check passed. A bill-wide discount
    reduces each line's taxable value alike, so the lines are scaled to the
    bill's taxable total before the rates are applied.
    """
    label = "The lines' GST rates give the bill's tax"
    invoice = fields.get("invoice") or {}
    amounts = [_n(it.get("amount")) for it in items]
    rates = [_n(it.get("gst_percent")) for it in items]
    taxable = _n(invoice.get("total_taxable_amount"))
    tax = _n(invoice.get("total_gst_amount"))
    if tax is None:
        heads = ("cgst", "sgst", "igst") + (() if combined else ("utgst",))
        printed = [v for v in (_n(invoice.get(f"total_{h}_amount")) for h in heads) if v is not None]
        tax = sum(printed) if printed else None
    line_sum = sum(a for a in amounts if a is not None)
    if (not items or not tax or not taxable or line_sum <= 0
            or any(a is None for a in amounts) or any(r is None for r in rates)):
        c.add("rates_give_tax", label, "skipped")
        return
    scale = taxable / line_sum
    if not 0.5 <= scale <= 1.05:
        # The taxable total is not the lines' value less a discount: freight,
        # or a figure on another basis. Nothing to scale by with confidence.
        c.add("rates_give_tax", label, "skipped")
        return
    expected = sum(a * r / 100.0 for a, r in zip(amounts, rates)) * scale
    if _close(expected, tax, 1.0 + 0.02 * len(items), 0.015):
        c.add("rates_give_tax", label, "pass", f"The rates give {expected:.2f}; the bill states {tax:.2f}.")
    else:
        c.add("rates_give_tax", label, "fail",
              f"The lines' rates give tax of {expected:.2f}, but the bill states {tax:.2f} - "
              "a line's GST rate may be misread.",
              [f"line_items[{i}].gst_percent" for i in range(len(items))] + ["invoice.total_gst_amount"])


def _check_cross_foot(c: _Checks, fields: dict, report: dict) -> None:
    invoice = fields.get("invoice") or {}
    taxable, gst, total = (_n(invoice.get("total_taxable_amount")),
                           _n(invoice.get("total_gst_amount")), _n(invoice.get("total_amount")))
    derived_tax = any(
        isinstance(it.get(f"{h}_amount"), dict)
        and (it[f"{h}_amount"].get("confidence") or 1.0) < 1.0
        and it[f"{h}_amount"].get("value") not in (None, "")
        for it in fields.get("line_items") or [] for h in _HEADS
    )
    if (taxable is None or gst is None or total is None or gst == 0
            # Tax we worked out from printed rates tests nothing against the
            # bill; and a bill whose total the lines match WITHOUT tax states
            # no tax-inclusive total to foot to.
            or derived_tax or report.get("total_reconciled_by") == "lines"):
        c.add("cross_foot", "Taxable value plus GST gives the total", "skipped")
        return
    # A rupee of round-off, and anything the lines themselves already accounted
    # for (a bill-level discount the reconciliation matched) is allowed for.
    reconciled_by = report.get("total_reconciled_by") or ""
    if _close(taxable + gst, total, 1.5, 0.002) or reconciled_by.startswith("lines - "):
        c.add("cross_foot", "Taxable value plus GST gives the total", "pass")
    elif "invoice value the bill prints" in reconciled_by:
        # The bill foots to its printed invoice value, then deducts a credit
        # note or advance to reach what is payable - said in the warnings.
        c.add("cross_foot", "Taxable value plus GST gives the total", "pass",
              "Taxable value plus GST gives the invoice value the bill prints; the amount payable "
              "differs by an adjustment the bill applies itself.")
    else:
        c.add("cross_foot", "Taxable value plus GST gives the total", "fail",
              f"{taxable:.2f} + {gst:.2f} = {taxable + gst:.2f}, but the bill's total is "
              f"{total:.2f}.",
              ["invoice.total_amount", "invoice.total_taxable_amount", "invoice.total_gst_amount"])


def _check_words(c: _Checks, report: dict) -> None:
    words = report.get("total_in_words")
    if not words:
        c.add("total_in_words", "Total in words agrees with the figures", "skipped")
    elif report.get("total_in_words_disagrees"):
        c.add("total_in_words", "Total in words agrees with the figures", "fail",
              f"The bill spells out {words}, which its own figures do not add up to.",
              ["invoice.total_amount"])
    else:
        c.add("total_in_words", "Total in words agrees with the figures", "pass")


def _check_price_ladder(c: _Checks, items: List[dict]) -> None:
    bad, tested = [], 0
    for i, it in enumerate(items):
        mrp, ptr, pts, rate = (_n(it.get(k)) for k in ("mrp", "ptr", "pts", "rate"))
        if not mrp:
            continue
        prices = [p for p in (ptr, pts, rate) if p]
        if not prices:
            continue
        tested += 1
        # Trade prices sit at or below MRP, and PTS at or below PTR. A small
        # margin allows for rounding in the printed figures.
        if any(p > mrp * 1.005 for p in prices) or (ptr and pts and pts > ptr * 1.005):
            bad.append(i)
    _verdict(c, "price_ladder", "Prices sit below MRP, PTS below PTR", tested, bad,
             "a trade price is above MRP, or PTS above PTR",
             lambda i: [_line(i, k) for k in ("mrp", "ptr", "pts", "rate")])


def _check_gst_rates(c: _Checks, items: List[dict]) -> None:
    bad, tested = [], 0
    for i, it in enumerate(items):
        for key in ("gst_percent",) + tuple(f"{h}_percent" for h in _HEADS):
            rate = _n(it.get(key))
            if rate is None:
                continue
            tested += 1
            if not any(abs(rate - r) < 0.001 for r in _GST_RATES):
                bad.append((i, key))
    _verdict(c, "gst_rates", "Every tax rate is a real GST rate", tested, bad,
             "is not a GST rate", lambda b: [_line(b[0], b[1])],
             describe=lambda b: f"line {b[0] + 1} {b[1].replace('_percent', '').upper()}%")


def _check_dates(c: _Checks, fields: dict, items: List[dict], today: _dt.date) -> None:
    invoice = fields.get("invoice") or {}
    inv = parse_month(_v(invoice.get("invoice_date")))
    problems: List[str] = []
    implicated: List[str] = []
    tested = False
    if inv:
        tested = True
        if inv > (today.year, today.month):
            problems.append("the invoice date is in the future")
            implicated.append("invoice.invoice_date")
    for i, it in enumerate(items):
        exp = parse_month(_v(it.get("expiry")))
        mfg = parse_month(_v(it.get("mfg_date")))
        if exp and inv:
            tested = True
            if exp < inv:
                problems.append(f"line {i + 1} expired before the invoice date")
                implicated.append(_line(i, "expiry"))
        if exp and mfg:
            tested = True
            if mfg > exp:
                problems.append(f"line {i + 1} is manufactured after it expires")
                implicated += [_line(i, "expiry"), _line(i, "mfg_date")]
    if not tested:
        c.add("dates", "Dates are possible", "skipped")
    elif problems:
        c.add("dates", "Dates are possible", "fail", "; ".join(problems[:4]).capitalize() + ".",
              implicated)
    else:
        c.add("dates", "Dates are possible", "pass")


def _check_irn(c: _Checks, fields: dict) -> None:
    """A GST IRN is exactly 64 hex characters; anything else was cut short -
    usually wrapped across lines in a narrow column, as Menarini's and JB's
    were - and would match nothing on the GST portal."""
    irn = _v((fields.get("invoice") or {}).get("irn"))
    if not irn:
        c.add("irn", "IRN is complete", "skipped")
    elif re.fullmatch(r"[0-9A-Fa-f]{64}", irn):
        c.add("irn", "IRN is complete", "pass")
    else:
        c.add("irn", "IRN is complete", "fail",
              f"The IRN read has {len(irn)} characters; a GST IRN has 64.", ["invoice.irn"])


def _check_hsn(c: _Checks, items: List[dict]) -> None:
    bad, tested = [], 0
    for i, it in enumerate(items):
        raw = _v(it.get("hsn"))
        if not raw:
            continue
        tested += 1
        if not re.fullmatch(r"\d{4}|\d{6}|\d{8}", re.sub(r"[\s.]", "", raw)):
            bad.append(i)
    _verdict(c, "hsn", "HSN codes have 4, 6 or 8 digits", tested, bad,
             "the HSN code is not 4, 6 or 8 digits", lambda i: [_line(i, "hsn")])


def _verdict(c: _Checks, check_id: str, label: str, tested: int, bad: list, problem: str,
             fields_for, describe=lambda i: f"line {i + 1}") -> None:
    """Turn a per-line test into one check: skipped, pass, or fail naming lines."""
    if not tested:
        c.add(check_id, label, "skipped")
    elif not bad:
        c.add(check_id, label, "pass", f"{tested} checked.")
    else:
        where = ", ".join(describe(b) for b in bad[:6]) + (" ..." if len(bad) > 6 else "")
        fields: List[str] = []
        for b in bad:
            fields += fields_for(b)
        c.add(check_id, label, "fail", f"{where}: {problem}.", fields)


# -------------------------------------------------------------------- entry ---

def verify_invoice(fields: dict, report: dict, today: Optional[_dt.date] = None,
                   extra: Optional[List[dict]] = None, page_text: Optional[str] = None) -> dict:
    """Run every check. `report` is reconcile_invoice's result (as stored in
    meta); `extra` adds checks computed elsewhere - the scan cross-read."""
    items = fields.get("line_items") or []
    today = today or _dt.date.today()
    c = _Checks()
    _check_reconciliation(c, report)
    _check_cross_foot(c, fields, report)
    _check_words(c, report)
    _check_item_count(c, report, items)
    _check_head_totals(c, fields, items)
    _check_line_arithmetic(c, items)
    _check_line_tax(c, items)
    _check_line_net(c, items, combined=bool(report.get("sgst_utgst_combined")))
    _check_line_discount(c, items)
    _check_price_ladder(c, items)
    _check_gst_rates(c, items)
    _check_rates_give_the_tax(c, fields, items, combined=bool(report.get("sgst_utgst_combined")))
    _check_dates(c, fields, items, today)
    _check_hsn(c, items)
    _check_irn(c, fields)
    _check_gstins(c, fields)
    _check_supplier_pan(c, fields)
    from app.services.ocr.document_kind import check as kind_check

    kind = kind_check(report.get("document_kind"))
    if kind:
        c.items.append(kind)
    # Whatever the bill prints that we did not read (missed_fields.py).
    from app.services.ocr.missed_fields import missed_check

    c.items.append(missed_check(fields, page_text or report.get("page_text") or ""))
    for check in extra or []:
        c.items.append(check)

    passed = sum(1 for x in c.items if x["status"] == "pass")
    failed = [x for x in c.items if x["status"] == "fail"]
    return {
        "checks": c.items,
        "passed": passed,
        "failed": len(failed),
        "skipped": sum(1 for x in c.items if x["status"] == "skipped"),
        "verdict": "verified" if not failed else "needs_check",
        "acknowledged": [],
    }


def flag_failed_fields(fields: dict, verification: dict) -> None:
    """Lower the confidence of every field a failed check implicates, so the
    existing review highlighting and "needs check" filter show it."""
    for check in verification.get("checks") or []:
        if check["status"] != "fail":
            continue
        for path in check.get("fields") or []:
            leaf = _resolve(fields, path)
            if isinstance(leaf, dict) and leaf.get("value") not in (None, ""):
                current = leaf.get("confidence")
                leaf["confidence"] = min(current if current is not None else 1.0,
                                         _FLAGGED_CONFIDENCE)


def _resolve(fields: dict, path: str):
    m = re.fullmatch(r"line_items\[(\d+)\]\.(\w+)", path)
    if m:
        items = fields.get("line_items") or []
        i = int(m.group(1))
        return items[i].get(m.group(2)) if i < len(items) else None
    section, _, key = path.partition(".")
    return (fields.get(section) or {}).get(key)


def open_checks(verification: Optional[dict]) -> List[Dict[str, str]]:
    """Failed checks not yet acknowledged - what stands between review and approval."""
    if not verification:
        return []
    done = {a.get("id") for a in verification.get("acknowledged") or []}
    return [
        {"id": x["id"], "label": x["label"], "message": x.get("message", "")}
        for x in verification.get("checks") or []
        if x["status"] == "fail" and x["id"] not in done
    ]


# Checks made from the page itself, which an edit cannot re-run: carried over.
_CARRIED_CHECKS = ("cross_read", "ai_review")


def reverify(doc_type: str, old_payload: dict, fields: dict) -> dict:
    """The document's meta, re-checked after a reviewer's edit.

    Before this, an edit replaced the fields and left the checks as they were:
    a reviewer who corrected a misread total still saw its warning, and one who
    mistyped a figure saw no warning at all. The checks now follow the data.

    The scan's second reading is not repeated - the reviewer is now the second
    reader - but its verdict is not dropped either: a disputed value the
    reviewer changed counts as resolved; one left untouched stays disputed.
    Acknowledgements are cleared, because the data they vouched for changed.
    """
    from app.services.ocr.invoice_checks import reconcile_invoice

    meta = dict((old_payload or {}).get("meta") or {})
    if doc_type != "invoice":
        return meta
    report = reconcile_invoice(fields, meta.get("stated_item_count"), meta.get("total_in_words"),
                               page_text=meta.get("page_text"))
    report.pop("warnings", None)
    meta.update({k: v for k, v in report.items() if k != "total_in_words" or v is not None})

    old_fields = (old_payload or {}).get("fields") or {}
    carried = []
    for check in ((meta.get("verification") or {}).get("checks") or []):
        if check.get("id") not in _CARRIED_CHECKS:
            continue
        if check["status"] == "fail":
            changed = [p for p in check.get("fields") or []
                       if _v(_resolve(old_fields, p)) != _v(_resolve(fields, p))]
            if check.get("fields") and len(changed) == len(check["fields"]):
                check = {**check, "status": "pass",
                         "message": "Every value the second reading disputed was corrected in review."}
        carried.append(check)

    verification = verify_invoice(fields, meta, extra=carried)
    flag_failed_fields(fields, verification)
    meta["verification"] = verification
    from app.services.ocr.choices import settle_checks

    settle_checks(meta)
    meta["warnings"] = [f"{c['label']}: {c['message']}" for c in verification["checks"]
                        if c["status"] == "fail"]
    return meta
