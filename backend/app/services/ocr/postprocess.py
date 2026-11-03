"""Deterministic post-processing of extracted fields.

Adds normalized companions WITHOUT destroying the original OCR values:
  - dates      -> ISO 'YYYY-MM-DD' in <field>.normalized
  - quantities -> integer/float in <field>.normalized
  - strengths  -> {amount, unit} in <field>.normalized
  - medication names -> normalized_id via a pluggable hook (stub for now)
"""
import re
from typing import Optional, Tuple

from dateutil import parser as date_parser


def normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        # dayfirst=True: Indian pharmacy docs use DD/MM/YYYY.
        return date_parser.parse(value, dayfirst=True, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def is_pack_expression(value) -> bool:
    """Explicit pack notation; ordinary '15 tablets' can still be quantity."""
    return bool(re.search(r"\d\s*['’`]\s*s\b|\d\s*[x×*]\s*\d", str(value or ""), re.I))


def normalize_quantity(value: Optional[str]):
    if value is None or is_pack_expression(value):
        return None
    m = re.fullmatch(r"\s*(?:qty\s*:\s*)?([-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+))\s*(?:[A-Za-z.]+)?\s*",
                     str(value), re.I)
    if not m:
        return None
    num = m.group(1).replace(",", "")
    return float(num) if "." in num else int(num)


_STRENGTH_RE = re.compile(r"(?P<amount>\d*\.?\d+)\s*(?P<unit>mg|mcg|g|ml|iu|%)", re.IGNORECASE)


def normalize_strength(value: Optional[str]):
    if not value:
        return None
    m = _STRENGTH_RE.search(value)
    if not m:
        return None
    amount = m.group("amount")
    return {
        "amount": float(amount) if "." in amount else int(amount),
        "unit": m.group("unit").lower(),
    }


def normalize_medication_name(value: Optional[str]) -> Optional[str]:
    """Hook for mapping a raw medicine name to a catalog id.

    Real drug-database lookup lands later; for now returns None (no match).
    """
    return None


def _set_norm(field: dict, normalized) -> None:
    if isinstance(field, dict):
        field.pop("normalized", None)
        if normalized is not None:
            field["normalized"] = normalized


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DASHES = re.compile("[\u2010-\u2015\u2212]")


def _month_year(token: str) -> Optional[Tuple[int, int]]:
    """(year, month) of an expiry-shaped token - "08/26", "08-2026", "Aug-26"."""
    m = (re.fullmatch(r"(\d{1,2})[/\-.](\d{2}|\d{4})", token)
         or re.fullmatch(r"([A-Za-z]{3})[-/' ]?(\d{2}|\d{4})", token))
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower()) if m.group(1).isalpha() else int(m.group(1))
    return (int(m.group(2)) % 100, month) if month and 1 <= month <= 12 else None


def _two_dates(value) -> Optional[Tuple[str, str]]:
    """(earlier, later) when a cell holds exactly two different month-year
    dates side by side, else None."""
    tokens = str(value or "").split()
    if len(tokens) != 2:
        return None
    when = [_month_year(t) for t in tokens]
    if None in when or when[0] == when[1]:
        return None
    return (tokens[0], tokens[1]) if when[0] < when[1] else (tokens[1], tokens[0])


_HSN_IN_NAME = re.compile(r"\s*\(\s*HSN\s*[-:]?\s*(\d{4}(?:\d{2}){0,2})\s*\)\s*$", re.I)
_DOSAGE = r"TABS?|TABLETS?|CAPS?|CAPSULES?|SYP|SYRUP|SUSP(?:ENSION)?|INJ(?:ECTION)?|CREAM|GEL|OINT(?:MENT)?|" \
          r"DROPS?|SACHETS?|LOTION|SPRAY|SOAP|POWDER|STRIPS?|BOTTLES?|VIALS?|AMP(?:OULES?)?"
_DOSAGE_WORD = re.compile(_DOSAGE, re.I)
# A company after the pack: the name ends in a figure ("10'S", "40MG", "1X10"),
# then one to four words ending Ltd / Limited / Pvt Ltd / LLP / Inc.
_MAKER_AFTER_PACK = re.compile(
    r"^(?P<name>.*\d\S*)\s+(?P<maker>(?P<first>[A-Za-z][\w&.'-]*)(?:\s+[A-Za-z][\w&.'-]*){0,3}?\s+"
    r"(?:Pvt\.?\s+Ltd|Private\s+Limited|Ltd|Limited|LLP|Inc)\.?)$", re.I)
# The last words of a maker line run into the name column, after a dosage word.
_MAKER_TAIL = re.compile(r"(?<=\S)\s+(?:PRIVATE\s+)?(?:LIMITED|LTD)\.?(?:\s*\([A-Z]{2,6}\))?$")


def tidy_line(item: dict) -> None:
    """Values one cell printed in a shape the stock software cannot take."""
    # A batch is keyed in as printed, with a plain hyphen: MAX RELIEF draws
    # "TPG — 928" with a long dash.
    batch = item.get("batch_no")
    if isinstance(batch, dict) and isinstance(batch.get("value"), str) and _DASHES.search(batch["value"]):
        batch["value"] = _DASHES.sub("-", batch["value"])
    # An HSN code wrapped in its cell: Sanofi prints "300490" over "69", read as
    # "300490 69". The code is the digits run together - 4, 6 or 8 of them.
    hsn = item.get("hsn")
    if isinstance(hsn, dict) and isinstance(hsn.get("value"), str) \
            and re.fullmatch(r"\d{4,6}(?: \d{2}){1,2}", hsn["value"].strip()) \
            and len(hsn["value"].replace(" ", "")) in (6, 8):
        hsn["value"] = hsn["value"].replace(" ", "")
    # A pack printed under "Uom": Health N U heads it so and prints "10's".
    # A unit that is a count or a size is the pack too; the unit stays as read.
    uom = item.get("uom")
    if isinstance(uom, dict) and isinstance(uom.get("value"), str) and not (item.get("pack") or {}).get("value") \
            and re.fullmatch(r"\d+\s*(?:'?s|[x*]\s*\d+\S*|\s*(?:ml|gm?|kg|mg|tabs?|caps?|nos?)\.?)",
                             uom["value"].strip(), re.I):
        item["pack"] = {"value": uom["value"].strip(), "confidence": uom.get("confidence")}
    # A product name carrying what is not its name.
    desc = item.get("description")
    if isinstance(desc, dict) and isinstance(desc.get("value"), str):
        name = desc["value"].strip()
        # ...its HSN, "OLMETIME-H 20 TABLETS(HSN- 30049079)" (Mankind).
        hsn_in = _HSN_IN_NAME.search(name)
        if hsn_in and hsn_in.start() > 0:
            if not (item.get("hsn") or {}).get("value"):
                item["hsn"] = {"value": hsn_in.group(1), "confidence": desc.get("confidence")}
            name = name[:hsn_in.start()].strip()
        # ...its maker, printed after the pack: "JUPIROS EZ 40 TABLET 10'S
        # Windlass Biotech Ltd" (Alkem).
        maker = _MAKER_AFTER_PACK.match(name)
        if maker and not _DOSAGE_WORD.fullmatch(maker.group("first")):
            if not (item.get("manufacturer") or {}).get("value"):
                item["manufacturer"] = {"value": maker.group("maker").strip(), "confidence": desc.get("confidence")}
            name = maker.group("name").strip()
        # ...or the end of the maker line under it: "LEXATAL-5C TAB LIMITED.
        # (CNS)" (Parshva prints "SEPTALYST LIFESCIENCES PRIVATE LIMITED. (CNS)").
        tail = _MAKER_TAIL.search(name)
        before = name[:tail.start()].split()[-1:] if tail else []
        if tail and before and _DOSAGE_WORD.fullmatch(before[0].strip(".,")):
            name = name[:tail.start()].strip()
        # ...or a lone digit left after its dosage form by an overprinted pack:
        # Cipla prints the name's "170 ML" over the pack's, read "EMULSION 1".
        stray = re.match(r"^(.*\b(?:" + _DOSAGE + r"|EMULSION|SOLUTION|LIQUID))\s+\d$", name, re.I)
        if stray:
            name = stray.group(1).strip()
        if name and name != desc["value"].strip():
            desc["value"] = name
    # Two dates in the expiry cell, side by side: IPCA heads the column "MFG.
    # Expiry" and prints "03/25 08/26". A medicine expires after it is made, so
    # the later date is the expiry and the earlier the manufacturing date.
    expiry = item.get("expiry")
    pair = _two_dates(expiry.get("value")) if isinstance(expiry, dict) else None
    if pair:
        expiry["value"] = pair[1]
        if not (item.get("mfg_date") or {}).get("value"):
            item["mfg_date"] = {"value": pair[0], "confidence": expiry.get("confidence")}


_SLABS = (5.0, 12.0, 18.0, 28.0)


def _money(field) -> Optional[float]:
    try:
        return float(str((field or {}).get("value")).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fill_single_slab(fields: dict) -> None:
    """GST % on lines that print none, when the bill's own totals show every
    line is at one slab.

    Win Medicare prints no rate on its lines, only the bill's CGST and SGST
    totals: 34,219.22 on 1,90,106.70 taxable is 18% exactly. When the tax over
    the taxable total is one slab, the lines add up to that taxable total and
    no line shows another rate, every line is at that slab. Filled below full
    confidence: it is worked out, not read.
    """
    inv = fields.get("invoice") or {}
    items = [i for i in fields.get("line_items") or [] if isinstance(i, dict)]
    taxable = _money(inv.get("total_taxable_amount"))
    heads = [_money(inv.get(f"total_{h}_amount")) for h in ("cgst", "sgst", "utgst", "igst")]
    tax = sum(h for h in heads if h) or _money(inv.get("total_gst_amount"))
    if not items or not taxable or not tax:
        return
    lines = [_money(i.get("amount")) for i in items]
    if None in lines or abs(sum(lines) - taxable) > max(1.0, 0.001 * taxable):
        return
    rate = 100.0 * tax / taxable
    slab = next((s for s in _SLABS if abs(rate - s) <= 0.02), None)
    if slab is None:
        return
    read = {_money(i.get("gst_percent")) for i in items} - {None}
    if read - {slab}:
        return
    for item in items:
        if _money(item.get("gst_percent")) is None:
            item["gst_percent"] = {"value": f"{slab:.2f}", "confidence": 0.7}


def postprocess_fields(doc_type: str, fields: dict) -> dict:
    """Enrich fields in place and return them."""
    if doc_type == "prescription":
        for med in fields.get("medications", []) or []:
            _set_norm(med.get("strength"), normalize_strength((med.get("strength") or {}).get("value")))
            _set_norm(med.get("duration"), normalize_quantity((med.get("duration") or {}).get("value")))
            nid = normalize_medication_name((med.get("name") or {}).get("value"))
            if isinstance(med.get("name"), dict) and nid:
                med["name"]["normalized_id"] = nid
    elif doc_type == "invoice":
        inv = fields.get("invoice") or {}
        for d_key in ("invoice_date", "due_date", "lr_date", "po_date"):
            _set_norm(inv.get(d_key), normalize_date((inv.get(d_key) or {}).get("value")))
        sup = fields.get("supplier") or {}
        for dl_d_key in ("dl_date_1", "dl_date_2", "dl_date_3", "dl_date_4"):
            _set_norm(sup.get(dl_d_key), normalize_date((sup.get(dl_d_key) or {}).get("value")))
        for item in fields.get("line_items", []) or []:
            if isinstance(item, dict):
                tidy_line(item)
            _set_norm(item.get("expiry"), normalize_date((item.get("expiry") or {}).get("value")))
            for key in ("quantity", "free_quantity", "total_quantity"):
                _set_norm(item.get(key), normalize_quantity((item.get(key) or {}).get("value")))
        drop_flag_column(fields.get("line_items") or [])
        rejoin_hyphen_wraps(fields.get("line_items") or [])
        fill_single_slab(fields)
    return fields


def rejoin_hyphen_wraps(items: list) -> None:
    """The end of a name carried onto the next page: J.B.'s last product on
    page 1 is "METROGYL TABS 200MG 20's" and page 2 opens with its "-BLISTER"
    (as "METROGYL IP 400MG 20T -BLISTER" prints it whole) over the next
    product, read as "-BLISTER METROGYL-O TABLETS". The fragment goes back."""
    for prev, item in zip(items, items[1:]):
        if not (isinstance(prev, dict) and isinstance(item, dict)):
            continue
        d, p = item.get("description"), prev.get("description")
        if not (isinstance(d, dict) and isinstance(p, dict) and isinstance(d.get("value"), str)
                and isinstance(p.get("value"), str) and p["value"].strip()):
            continue
        m = re.match(r"(-[A-Za-z][\w'.]*)\s+(\S.*)$", d["value"].strip())
        if m:
            p["value"] = f'{p["value"].rstrip()} {m.group(1)}'
            d["value"] = m.group(2)


def drop_flag_column(items: list) -> None:
    """A one-letter flag column read into every name: Sun prints "N" (its
    scheme marker) just left of each product, giving "N GLUCORED FORTE". Only
    when nearly every line of the bill starts with the same lone letter."""
    names = [(it.get("description") or {}) for it in items if isinstance(it, dict)]
    names = [d for d in names if isinstance(d, dict) and isinstance(d.get("value"), str) and d["value"].strip()]
    if len(names) < 3:
        return
    firsts = [re.match(r"([A-Z])\s+\S", d["value"].strip()) for d in names]
    letters = {m.group(1) for m in firsts if m}
    if 1 <= len(letters) <= 2 and sum(1 for m in firsts if m) >= 0.9 * len(names):
        for d, m in zip(names, firsts):
            if m:
                d["value"] = d["value"].strip()[2:].strip()
