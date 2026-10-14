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
from app.services.ocr.layout import signature

# {supplier GSTIN: {"layouts": {signature: {"is": {...}, "not": {...}}}}}
LESSONS: ContextVar[dict] = ContextVar("supplier_lessons", default={})
_CURRENT: ContextVar[Optional[str]] = ContextVar("lesson_supplier", default=None)
_APPLIED: ContextVar[Optional[list]] = ContextVar("lessons_applied", default=None)
_LAYOUT: ContextVar[str] = ContextVar("lesson_layout", default="")
# Set when a bill is re-read without lessons (the safety net).
DISABLED: ContextVar[bool] = ContextVar("lessons_disabled", default=False)


def norm(heading) -> str:
    """A heading as compared: lower case, no spaces, dots, dashes or newlines
    (the same as invoice_parser._norm)."""
    return re.sub(r"[\s.\n_\-]+", "", str(heading or "")).lower()


def start(lessons: Optional[dict]) -> Tuple:
    """Begin a reading with these lessons. Returns tokens for finish()."""
    return (LESSONS.set(lessons or {}), _CURRENT.set(None), _APPLIED.set([]), _LAYOUT.set(""))


def finish(tokens: Tuple) -> None:
    LESSONS.reset(tokens[0])
    _CURRENT.reset(tokens[1])
    _APPLIED.reset(tokens[2])
    _LAYOUT.reset(tokens[3])


def choose(page_text: Optional[str]) -> Optional[str]:
    """The supplier this bill is from, among those with lessons: the one whose
    GSTIN the page prints. Chosen once per reading."""
    if _CURRENT.get() or not LESSONS.get():
        return _CURRENT.get()
    text = (page_text or "").upper()
    matches = [gstin for gstin in LESSONS.get() if gstin and gstin.upper() in text]
    if len(matches) == 1:
        _CURRENT.set(matches[0])
        return matches[0]
    return None


def column_lessons(norms: List[str]) -> Tuple[Dict[int, str], Dict[int, Set[str]]]:
    """For a table's headings: {column index: the field it is} and {column
    index: fields it is not}, from the current supplier's lessons."""
    gstin = _CURRENT.get()
    _LAYOUT.set(signature(norms))
    if not gstin or DISABLED.get():
        return {}, {}
    lessons = ((LESSONS.get().get(gstin) or {}).get("layouts") or {}).get(_LAYOUT.get()) or {}
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
    entry = {"supplier_gstin": _CURRENT.get(), "field": field, "heading": heading, "kind": kind,
             "layout_signature": _LAYOUT.get()}
    if entry not in applied:
        applied.append(entry)


def applied() -> list:
    return list(_APPLIED.get() or [])


def prompt_note() -> str:
    """What this shop has taught us about the current supplier's columns, as
    a note for the AI's instructions - for a bill the free reader handed on
    (its supplier is known by then). Empty when there is nothing to say."""
    gstin = _CURRENT.get()
    if not gstin or DISABLED.get():
        return ""
    lessons = ((LESSONS.get().get(gstin) or {}).get("layouts") or {}).get(_LAYOUT.get()) or {}
    printed = lessons.get("printed") or {}
    said = [f'the column headed "{printed.get(h, h)}" is the {field.replace("_", " ")}'
            for h, field in (lessons.get("is") or {}).items()]
    said += [f'the column headed "{printed.get(h, h)}" is NOT the {field.replace("_", " ")}'
             for h, fields in (lessons.get("not") or {}).items() for field in fields]
    if not said:
        return ""
    return ("\n\nThis supplier's bills, as the pharmacy's reviewers have corrected them before: "
            + "; ".join(said) + ". Read each line's fields accordingly.")
