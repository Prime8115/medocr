"""Learning where a supplier prints a field, from the reviewer who filled it in.

A new supplier's layout leaves fields blank the first time: it labels its LR
number "Docket No. & Date", or prints its PO under the heading instead of
beside it. The reviewer types the value in. That value is on the page - so the
words printed just before it are where THIS supplier keeps that field.

This module holds the two halves, both pure:

* `learn(page_text, path, value)` - the label in front of a value the reviewer
  entered, and how the value sits against it, as a small dict to store.
* `find(page_text, learned, fields)` - on the supplier's next bill, the value
  at that label, or None.

It is deliberately cautious:

* The label kept is the SHORTEST run of words in front of the value that leads
  back to that value alone on the page - "LR No", not "Mumbai Jurisdiction LR
  No"; never "Date" on its own, which every bill prints three times.
* A value printed under several such labels (a date that is the invoice date,
  the order date and the LR date at once) is learned only if exactly one of
  them names the field - "LR Date" for an LR date. Otherwise nothing is learned:
  there is no telling which one the reviewer meant.
* What `find` returns must have the same shape as what the reviewer typed -
  letters where there were letters, digits where there were digits - and pass
  the same tests the AI gap-fill's answers do (gap_fill._acceptable). A label
  that leads to two different values returns nothing.

The database half - storing per shop and supplier GSTIN, applying on arrival -
is services/supplier_labels.py.
"""
import re
from typing import Dict, List, Optional

from app.services.ocr.gap_fill import _ASKABLE, _acceptable
from app.services.ocr.invoice_header import _DATE

# Header fields a label can be learned for. Money is the reconciliation's
# business, and party names are read from blocks, not after labels.
LEARNABLE: Dict[str, str] = {
    path: kind for path, kind in _ASKABLE.items()
    if kind != "money" and path not in ("supplier.name", "bill_to.name", "ship_to.name")
}
# Confidence of a value found at a learned label: above the AI's guess (it is
# where a person said the value is), below a direct reading.
LEARNED_CONFIDENCE = 0.7

# Words that name each field - the tie-break when one value sits under several
# labels. Compared with a label's words, dots removed ("L.R." -> "lr").
_NAMES = {
    "invoice.invoice_no": ("invoice", "inv", "bill"),
    "invoice.invoice_date": ("invoice", "inv", "bill"),
    "invoice.due_date": ("due",),
    "invoice.lr_no": ("lr", "gr", "rr", "docket", "consignment", "awb", "cn", "bilty", "builty"),
    "invoice.lr_date": ("lr", "gr", "rr", "docket", "consignment", "awb", "cn", "bilty", "builty"),
    "invoice.po_no": ("po", "order", "purchase", "indent"),
    "invoice.po_date": ("po", "order", "purchase", "indent"),
    "invoice.irn": ("irn",),
    "invoice.eway_bill_no": ("way", "eway", "ewb"),
    "invoice.transport": ("transport", "transporter", "courier", "carrier", "despatch",
                          "dispatch", "mode", "through", "vehicle"),
    "supplier.email": ("mail", "email"),
    "supplier.dl_no_1": ("dl", "licence", "license", "drug"),
    "supplier.dl_no_2": ("dl", "licence", "license", "drug"),
    "supplier.gstin": ("gstin", "gst", "gstn", "tin"),
    "bill_to.gstin": ("gstin", "gst", "gstn", "tin", "uin"),
    "ship_to.gstin": ("gstin", "gst", "gstn", "tin", "uin"),
}
_DATE_WORDS = {"date", "dt", "dated"}
# A figure stepped over inside a label: "Order No: 100178296 Dt: 30.06.2025".
_FIGURE = "<num>"
_MAX_LABEL_WORDS = 4
_SEPARATORS = " \t:.#-/|"
_CAPTURE = {
    "date": _DATE,
    "irn": r"([A-Fa-f0-9]{64})\b",
    "eway": r"(\d{10,16})\b",
    "email": r"([\w.+\-]+@[\w\-]+\.[\w.\-]+)",
    "gstin": r"([0-9]{2}[A-Za-z0-9]{13})\b",
    "reference": r"([A-Za-z0-9][A-Za-z0-9\-/.]*[A-Za-z0-9]|[A-Za-z0-9]{2,})",
}
# Text (a transporter's name) is taken as the same number of words as last time.
_TEXT = r"([^\s:]+(?:[ \t]+[^\s:]+){%d})"


def shape(value: str) -> str:
    """Runs of letters as A, of digits as 9, the rest kept: "DK778812" -> "A9",
    "MUM25NODM01080" -> "A9A9", "09.09.2025" -> "9.9.9"."""
    out = re.sub(r"[A-Za-z]+", "A", value or "")
    return re.sub(r"[0-9]+", "9", out)


def _has_digit(word: str) -> bool:
    return any(ch.isdigit() for ch in word)


def _is_separator(word: str) -> bool:
    return not word.strip(_SEPARATORS + ",;")


def _label_runs(before: str, values: frozenset = frozenset(), is_date: bool = False) -> List[str]:
    """Candidate labels from the text in front of a value, shortest first: the
    last word, the last two, ... up to four words. A figure may be stepped over
    only while what is collected is just a date word - "Order No: 100178296 Dt:"
    gives "Order No <num> Dt" - so the date beside a number keeps that number's
    label, as does a date right after one ("Docket No. & Date : DK778812 /
    23-09-2025"); anywhere else a figure ends the label. A word that is another
    field's value ("L.R. No. : LOCAL Date :") counts as a figure."""
    words = before.split()
    collected: List[str] = []
    runs: List[str] = []
    stepped = False
    while words:
        word = words.pop()
        if _is_separator(word):
            continue
        if _has_digit(word) or word.strip(_SEPARATORS + ",").lower() in values:
            label_words = [w for w in collected if w != _FIGURE]
            if stepped or not is_date or any(w.strip(_SEPARATORS).lower() not in _DATE_WORDS for w in label_words):
                break
            collected.insert(0, _FIGURE)
            stepped = True
            continue
        collected.insert(0, word)
        if sum(1 for w in collected if w != _FIGURE) > _MAX_LABEL_WORDS:
            break
        label = " ".join(collected).strip(_SEPARATORS + ",")
        if label.startswith(_FIGURE):
            continue
        if sum(ch.isalpha() for ch in label) >= 3:
            runs.append(label)
    return runs


def _occurrence_labels(page: str, at: int, path: str, values: frozenset) -> List[dict]:
    """Candidate labels for a value found at `at`: beside it on its line, or
    failing that, ending one of the two lines above it. A label above is a
    looser fit - other columns' text lies between - so it must name the field."""
    start = page.rfind("\n", 0, at) + 1
    runs = _label_runs(page[start:at], values, LEARNABLE.get(path) == "date")
    if runs:
        return [{"label": r, "where": "after"} for r in runs]
    # Below: other columns' text shares the value's line, so where it sits is
    # kept too - counted from the line's end, as JB prints "... 400056 5248
    # 09.09.2025": the PO, then its date, last.
    end = page.find("\n", at)
    tail = len(page[at:end if end >= 0 else len(page)].split()) - 1
    for line in reversed(page[:start].split("\n")[:-1][-2:]):
        runs = [r for r in _label_runs(line, values) if _names_field(path, r)]
        if runs:
            return [{"label": r, "where": "below", "tail": tail} for r in runs]
    return []


def _field_values(fields: Optional[dict]) -> frozenset:
    out = set()
    for section in (fields or {}).values():
        if isinstance(section, dict):
            for leaf in section.values():
                if isinstance(leaf, dict):
                    v = str(leaf.get("value") or "").strip().lower()
                    if len(v) >= 2:
                        out.add(v)
    return frozenset(out)


def _names_field(path: str, label: str) -> bool:
    words = set(re.split(r"[^a-z0-9]+", label.lower().replace(".", "")))
    return any(name in words for name in _NAMES.get(path, ()))


def learn(page_text: str, path: str, value: str, fields: Optional[dict] = None) -> Optional[dict]:
    """What to remember about where `value` for `path` is printed, or None.

    None when the field is not learnable, the value is not on the page, or no
    single label leads back to it (see the module docstring).
    """
    value = (value or "").strip()
    if path not in LEARNABLE or len(value) < 2 or not page_text:
        return None
    values = _field_values(fields) - {value.lower()}
    base = {"path": path, "shape": shape(value), "words": len(value.split())}
    shortest: Dict[str, dict] = {}   # per occurrence, the shortest label that leads back
    named: Dict[str, dict] = {}      # per occurrence, the shortest that also names the field
    for m in re.finditer(re.escape(value), page_text):
        # A whole value, not part of a longer one.
        before = page_text[m.start() - 1] if m.start() else " "
        after = page_text[m.end()] if m.end() < len(page_text) else " "
        if before.isalnum() or after.isalnum():
            continue
        first = True
        for candidate in _occurrence_labels(page_text, m.start(), path, values):
            learned = {**candidate, **base}
            matches = _matches(page_text, learned, fields or {})
            if set(matches) != {value}:
                continue
            # A label that does not name the field must be printed once: "Date"
            # alone leads back to this value only by the page's coincidence.
            if not _names_field(path, learned["label"]) and len(matches) > 1:
                continue
            if first:
                shortest[learned["label"].lower()] = learned
                first = False
            if _names_field(path, learned["label"]):
                named[learned["label"].lower()] = learned
                break
    for found in (named, shortest):
        if len(found) == 1:
            return next(iter(found.values()))
    return None


def _loose(label: str) -> str:
    parts = []
    for word in label.split():
        parts.append(r"\S+" if word == _FIGURE else re.escape(word))
    return r"[ \t.:\-/]*".join(parts)


def find(page_text: str, learned: dict, fields: dict) -> Optional[str]:
    """The value at a learned label on this page, or None - also None when the
    label leads to two different values, since then it does not say which."""
    found = set(_matches(page_text, learned, fields))
    return found.pop() if len(found) == 1 else None


def _matches(page_text: str, learned: dict, fields: dict) -> List[str]:
    """Every acceptable value at the label, one per place it is printed."""
    path = learned.get("path")
    kind = LEARNABLE.get(path)
    if not kind or not page_text or not learned.get("label"):
        return []
    if kind == "text":
        capture = _TEXT % max(0, int(learned.get("words") or 1) - 1)
    else:
        capture = _CAPTURE[kind]
    candidates: List[str] = []
    # The label starts a word: "Date" is not found inside "Ack Date"'s "kDate".
    label = r"(?<![A-Za-z0-9])" + _loose(learned["label"])
    if learned.get("where") == "below":
        tail = int(learned.get("tail") or 0)
        for m in re.finditer(label, page_text, re.I):
            for line in page_text[m.end():].split("\n")[1:3]:
                words = line.split()
                if kind != "text" and len(words) > tail:
                    word = words[-1 - tail]
                    if re.fullmatch(capture, word):
                        candidates.append(word)
    else:
        pattern = label + r"[ \t:.#\-/]*" + capture
        candidates = [m.group(1) for m in re.finditer(pattern, page_text, re.I)]
    found = []
    for value in candidates:
        value = value.strip().rstrip(".,/-")
        if kind in ("reference", "eway") and shape(value) != learned.get("shape"):
            continue
        if _acceptable(path, value, page_text, fields):
            found.append(value)
    return found
