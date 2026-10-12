"""What a shop has taught the reader about each supplier's columns.

A reviewer who corrects a supplier's PTR on every line, to the figure printed
under that supplier's "Trade Price/Unit", has shown the reader that this
supplier heads its PTR "Trade Price/Unit" - and that the column it read from
is not the PTR. services/supplier_profile.py learns such lessons from the
corrections; this module applies them while a bill is read:

* LESSONS holds the shop's lessons for every supplier, by supplier GSTIN.
* choose(page_text) picks the supplier whose GSTIN is printed on the bill.
* _map_columns (invoice_parser.py) asks column_lessons() which headings are a
  field, and which are not, before its own keyword list - and the lessons it
  used are recorded, so a reading that stops adding up can be redone without
  them, and every learned field is shown as learned.
"""
import re
from contextvars import ContextVar
from typing import Dict, List, Optional, Set, Tuple

# {supplier GSTIN: {"is": {heading: field}, "not": {heading: [field, ...]}}}
LESSONS: ContextVar[dict] = ContextVar("supplier_lessons", default={})
_CURRENT: ContextVar[Optional[str]] = ContextVar("lesson_supplier", default=None)
_APPLIED: ContextVar[Optional[list]] = ContextVar("lessons_applied", default=None)
# Set when a bill is re-read without lessons (the safety net).
DISABLED: ContextVar[bool] = ContextVar("lessons_disabled", default=False)


def norm(heading) -> str:
    """A heading as compared: lower case, no spaces, dots, dashes or newlines
    (the same as invoice_parser._norm)."""
    return re.sub(r"[\s.\n_\-]+", "", str(heading or "")).lower()


def start(lessons: Optional[dict]) -> Tuple:
    """Begin a reading with these lessons. Returns tokens for finish()."""
    return (LESSONS.set(lessons or {}), _CURRENT.set(None), _APPLIED.set([]))


def finish(tokens: Tuple) -> None:
    LESSONS.reset(tokens[0])
    _CURRENT.reset(tokens[1])
    _APPLIED.reset(tokens[2])


def choose(page_text: Optional[str]) -> Optional[str]:
    """The supplier this bill is from, among those with lessons: the one whose
    GSTIN the page prints. Chosen once per reading."""
    if _CURRENT.get() or not LESSONS.get():
        return _CURRENT.get()
    text = (page_text or "").upper()
    for gstin in LESSONS.get():
        if gstin and gstin.upper() in text:
            _CURRENT.set(gstin)
            return gstin
    return None


def column_lessons(norms: List[str]) -> Tuple[Dict[int, str], Dict[int, Set[str]]]:
    """For a table's headings: {column index: the field it is} and {column
    index: fields it is not}, from the current supplier's lessons."""
    gstin = _CURRENT.get()
    if not gstin or DISABLED.get():
        return {}, {}
    lessons = LESSONS.get().get(gstin) or {}
    is_, not_ = lessons.get("is") or {}, lessons.get("not") or {}
    forced: Dict[int, str] = {}
    forbidden: Dict[int, Set[str]] = {}
    for i, h in enumerate(norms):
        if h and h in is_ and is_[h] not in forced.values():
            forced[i] = is_[h]
        if h and h in not_:
            forbidden[i] = set(not_[h])
    return forced, forbidden


def used(field: str, heading: str, kind: str) -> None:
    """Record that a lesson shaped this reading."""
    applied = _APPLIED.get()
    if applied is None:
        return
    entry = {"supplier_gstin": _CURRENT.get(), "field": field, "heading": heading, "kind": kind}
    if entry not in applied:
        applied.append(entry)


def applied() -> list:
    return list(_APPLIED.get() or [])
