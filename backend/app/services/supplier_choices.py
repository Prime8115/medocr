"""Remembering a reviewer's decisions per supplier, and applying them.

See services/ocr/choices.py for what a choice is. This is the part that needs
the database: once a shop has said which of Zydus's two order references is its
PO, the next Zydus bill for that shop is decided the same way on arrival - and
the review screen still shows the choice, marked as remembered, so it can be
changed.
"""
import copy
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.supplier_choice import SupplierChoice
from app.services.ocr import choices as ch
from app.services.ocr.verify import reverify

log = logging.getLogger(__name__)


def _supplier_gstin(payload: dict) -> Optional[str]:
    leaf = ((payload.get("fields") or {}).get("supplier") or {}).get("gstin") or {}
    value = str(leaf.get("value") or "").strip().upper()
    return value if len(value) == 15 else None


def decide(db: Session, shop_id: str, doc_type: str, payload: dict, choice_id: str,
           option: int, user_id: Optional[str], remember: bool = True) -> dict:
    """Apply a reviewer's decision to a payload and return the new payload.

    The checks are re-run on the result, and - where the choice is one that
    belongs to the supplier rather than to this bill - the decision is saved
    so the supplier's next bill arrives decided.
    """
    before = copy.deepcopy(payload)
    after = copy.deepcopy(payload)
    choice = ch.choose(after, choice_id, option, user_id)
    choices_now = after["meta"]["choices"]
    after["meta"] = reverify(doc_type, before, after["fields"])
    after["meta"]["choices"] = choices_now
    ch.settle_checks(after["meta"])

    gstin = _supplier_gstin(after)
    if remember and choice.get("remember") and gstin:
        label = choice["options"][option]["label"]
        row = (db.query(SupplierChoice)
               .filter_by(shop_id=shop_id, supplier_gstin=gstin, choice_id=choice_id).first())
        if row:
            row.option_label, row.decided_by = label, user_id
        else:
            db.add(SupplierChoice(shop_id=shop_id, supplier_gstin=gstin, choice_id=choice_id,
                                  option_label=label, decided_by=user_id))
    return after


def apply_remembered(db: Session, shop_id: str, doc_type: str, payload: dict) -> dict:
    """Decide, on arrival, every choice this shop has already decided for this
    supplier. Returns the payload, changed or not. Never raises."""
    try:
        gstin = _supplier_gstin(payload)
        pending = ch.pending(payload.get("meta"))
        if not gstin or not pending:
            return payload
        for choice in pending:
            if not choice.get("remember"):
                continue
            row = (db.query(SupplierChoice)
                   .filter_by(shop_id=shop_id, supplier_gstin=gstin, choice_id=choice["id"]).first())
            if not row:
                continue
            option = ch.options_by_label(choice).get(row.option_label)
            if option is None:
                continue
            before = copy.deepcopy(payload)
            ch.choose(payload, choice["id"], option, user_id=row.decided_by, remembered=True)
            choices_now = payload["meta"]["choices"]
            payload["meta"] = reverify(doc_type, before, payload["fields"])
            payload["meta"]["choices"] = choices_now
            ch.settle_checks(payload["meta"])
        return payload
    except Exception:  # noqa: BLE001 - a remembered decision is a convenience, never a failure
        log.exception("could not apply remembered supplier choices")
        return payload
