"""When a bill prints two answers to one field, the reviewer decides.

Some ambiguities no reading can settle, because the bill itself gives two
answers and which one is "right" is a business decision, not a reading one:

* Zydus prints "PO Number: GN-15912-1068-SHREE" - the customer's purchase
  order - and "Order No: 100178296 Dt: 30.06.2025" - its own sales order. Which
  one a shop's software wants as the PO is up to the shop.
* Abbott spells out a total of 144,068.00 while its own lines and tax add up
  to 144,144.00.

Guessing would be wrong some of the time with nothing to show for it. Instead
each such field becomes a CHOICE in `meta.choices`: every option, where on the
bill it came from, and a default - so the document is complete either way.
Approval waits until the reviewer has picked; a pick for a field that is a
supplier's habit (which reference is the PO) is remembered for that supplier,
so its next bill arrives already decided - and can still be changed.

A choice looks like:

    {"id": "po", "label": "Purchase order", "fields": ["invoice.po_no", ...],
     "options": [{"label": "PO Number", "values": {"invoice.po_no": "GN-..."}}],
     "default": 1, "chosen": None, "remember": True, "resolves": []}
"""
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services.ocr.invoice_header import (
    _DATE,
    _NEXT_LABEL,
    _TOKEN,
    _is_label_not_value,
    _labelled_all,
)

# Where a purchase-order reference is printed, by what it is.
_ORDER_SOURCES = (
    ("PO Number", ["PO Number", "Purchase Order No", "Purchase Order Number", "Customer PO No",
                   "Buyer's Order No", "Buyers Order No"]),
    ("Order No", ["Order No", "Sales Order No", "SO No", "Ord Ref No"]),
)
_DATE_AFTER = re.compile(r"\s*(?:date|dt)\.?\s*[:\-]?\s*" + _DATE, re.I)


def _first_reference(text: str, labels: List[str]) -> Optional[tuple]:
    """(value, date or None) for the first real value after any of these labels."""
    for label in labels:
        loose = r"[\s.:\-]*".join(re.escape(w) for w in label.split())
        for m in re.finditer(loose + r"[\s.:#\-]*" + _TOKEN, text or "", re.I):
            value = _NEXT_LABEL.split(m.group(1))[0].strip(" .,-:/")
            if len(value) < 2 or _is_label_not_value(value, text):
                continue
            date = _DATE_AFTER.match(text, m.end(1))
            return value, (date.group(1) if date else None)
    return None


def reference_choices(text: str, fields: dict) -> List[dict]:
    """A choice for the PO when the bill prints two different references."""
    options = []
    for label, labels in _ORDER_SOURCES:
        found = _first_reference(text, labels)
        if found and all(found[0] != o["values"]["invoice.po_no"] for o in options):
            options.append({"label": label, "values": {"invoice.po_no": found[0],
                                                       "invoice.po_date": found[1]}})
    if len(options) < 2:
        return []
    current = ((fields.get("invoice") or {}).get("po_no") or {}).get("value")
    default = next((i for i, o in enumerate(options)
                    if o["values"]["invoice.po_no"] == current), 0)
    return [{
        "id": "po", "label": "Purchase order",
        "question": "The bill prints two order references. Which one is the purchase order?",
        "fields": ["invoice.po_no", "invoice.po_date"],
        "options": options, "default": default, "chosen": None,
        "remember": True, "resolves": [],
    }]


def total_choice(report: dict) -> List[dict]:
    """A choice for the bill total when its words and its figures disagree."""
    words, built = report.get("total_in_words"), report.get("total_built_from_lines")
    if not report.get("total_in_words_disagrees") or not words or not built:
        return []
    return [{
        "id": "total", "label": "Bill total",
        "question": "The bill's total in words does not match what its figures add up to. "
                    "Which is the amount payable?",
        "fields": ["invoice.total_amount"],
        "options": [
            {"label": "Total in words", "values": {"invoice.total_amount": words}},
            {"label": "Lines plus the tax the bill states",
             "values": {"invoice.total_amount": built}},
        ],
        "default": 0, "chosen": None,
        # Every bill's discrepancy is its own; nothing to remember.
        "remember": False, "resolves": ["total_in_words"],
    }]


def _set(fields: dict, path: str, value: Optional[str], confidence: float = 1.0) -> None:
    section, _, key = path.partition(".")
    fields.setdefault(section, {})[key] = {"value": value, "confidence": confidence}


def apply_default(fields: dict, choices: List[dict]) -> None:
    """Fill each choice's fields from its default option, so the document is
    complete before anyone has decided."""
    for choice in choices:
        option = choice["options"][choice["default"]]
        for path, value in option["values"].items():
            _set(fields, path, value, confidence=0.5)


def pending(meta: Optional[dict]) -> List[dict]:
    return [c for c in (meta or {}).get("choices") or [] if c.get("chosen") is None]


def choose(payload: dict, choice_id: str, option: int, user_id: Optional[str],
           remembered: bool = False) -> dict:
    """Apply a decision: the chosen option's values go into the fields, the
    choice is recorded with who made it and when, and any check it settles is
    marked acknowledged. Returns the choice. Raises KeyError / IndexError."""
    meta = payload.setdefault("meta", {})
    choice = next(c for c in meta.get("choices") or [] if c["id"] == choice_id)
    values = choice["options"][option]["values"]
    fields = payload.setdefault("fields", {})
    for path, value in values.items():
        _set(fields, path, value)
    choice.update({
        "chosen": option, "by": user_id, "remembered": remembered,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return choice


def settle_checks(meta: dict) -> None:
    """Checks a decided choice settles count as acknowledged; pending choices
    appear as failed checks of their own, so the verdict shows them."""
    verification = meta.get("verification")
    if not verification:
        return
    checks = [c for c in verification["checks"] if not c["id"].startswith("choice_")]
    acked = verification.setdefault("acknowledged", [])
    acked_ids = {a.get("id") for a in acked}
    for choice in meta.get("choices") or []:
        if choice.get("chosen") is None:
            checks.append({
                "id": f"choice_{choice['id']}", "label": f"{choice['label']}: choose one",
                "status": "fail", "message": choice["question"], "fields": choice["fields"],
            })
            continue
        for check_id in choice.get("resolves") or []:
            if check_id not in acked_ids:
                acked.append({"id": check_id, "by": choice.get("by"), "at": choice.get("at"),
                              "via": f"choice_{choice['id']}"})
                acked_ids.add(check_id)
    verification["checks"] = checks
    failed = [c for c in checks if c["status"] == "fail" and c["id"] not in acked_ids]
    verification["failed"] = len([c for c in checks if c["status"] == "fail"])
    verification["passed"] = len([c for c in checks if c["status"] == "pass"])
    verification["verdict"] = "verified" if not failed else "needs_check"


def options_by_label(choice: dict) -> Dict[str, int]:
    return {o["label"]: i for i, o in enumerate(choice["options"])}
