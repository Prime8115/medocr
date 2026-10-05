"""A second, independent reading of the parties' GSTINs.

The AI does not always return everything it can see: the same MSV Lifesciences
scan, sent twice, came back once with the Bill-to GSTIN and once without it.
A blank field looks like "not on the invoice", which is a silent mistake.

So the GSTINs printed on the page are read a second way - from a digital
PDF's own text, or by local OCR (Tesseract) on a scan or photo - and kept only
when their check character is right. A GSTIN that passes it is, beyond
reasonable doubt, exactly what is printed: a misread character fails the
check. Each is assigned to a party by where it sits - before or after the
buyer's heading - never by guesswork, and only when that position names one
GSTIN alone.

The AI's reading is never overwritten by this unless it is blank or fails
its own check; when both readings are valid and disagree, the field is
flagged for the pharmacist rather than either being trusted.
"""
import io
import logging
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

_LABELS = {"supplier": "supplier", "bill_to": "Bill-to"}
# Below the review threshold: filled from the page, still shown for confirming.
_FILLED_CONFIDENCE = 0.55


def first_page_text(data: bytes, content_type: str) -> str:
    """The first page's text, read without the AI. Empty when it cannot be
    read (no Tesseract, unreadable file) - the check is then skipped."""
    from app.services.ocr.fallback import _ocr_image, _pdf_page_images, _prepare, _tesseract_available
    from app.services.ocr.pdf_utils import extract_text_pages, is_digital_pdf

    try:
        if content_type == "application/pdf":
            if is_digital_pdf(data):
                return (extract_text_pages(data)[:1] or [""])[0]
            if not _tesseract_available():
                return ""
            return "\n".join(_ocr_image(_prepare(img)) for img in _pdf_page_images(data, 1))
        if content_type.startswith("image/"):
            if not _tesseract_available():
                return ""
            from PIL import Image

            return _ocr_image(_prepare(Image.open(io.BytesIO(data))))
    except Exception as exc:  # noqa: BLE001 - a failed second reading changes nothing
        log.info("party check: could not read the page: %s", exc)
    return ""


def page_party_gstins(text: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    """(supplier's GSTIN, buyer's GSTIN, every valid GSTIN on the page).

    Only GSTINs whose check character is right are considered. A party's is
    given only when its side of the buyer heading holds exactly one; with no
    heading, neither is given.
    """
    from app.services.ocr.invoice_header import _GSTIN_SHAPE, gstin_is_valid
    from app.services.ocr.invoice_parser import _BUYER_HEADING

    found = [(m.start(), m.group(1)) for m in _GSTIN_SHAPE.finditer(text or "") if gstin_is_valid(m.group(1))]
    every = list(dict.fromkeys(g for _, g in found))
    heading = _BUYER_HEADING.search(text or "")
    if not heading:
        return None, None, every
    before = set(g for pos, g in found if pos < heading.start())
    after = set(g for pos, g in found if pos >= heading.start()) - before
    supplier = next(iter(before)) if len(before) == 1 else None
    buyer = next(iter(after)) if len(after) == 1 else None
    return supplier, buyer, every


def cross_check(fields: dict, text: str) -> List[str]:
    """Fill or flag the parties' GSTINs against the page's own. Returns the
    warnings for the reviewer."""
    from app.services.ocr.invoice_header import gstin_is_valid

    if not text:
        return []
    supplier, buyer, every = page_party_gstins(text)
    warnings: List[str] = []
    for party, on_page in (("supplier", supplier), ("bill_to", buyer)):
        node = fields.get(party)
        if not isinstance(node, dict) or not on_page:
            continue
        leaf = node.setdefault("gstin", {"value": None, "confidence": None})
        read = str(leaf.get("value") or "").strip().upper()
        label = _LABELS[party]
        if read == on_page:
            continue
        if not read:
            leaf.update(value=on_page, confidence=_FILLED_CONFIDENCE)
            warnings.append(f"The {label} GSTIN was missed by the AI; {on_page} is printed on the invoice "
                            "and has been filled in - please confirm it.")
        elif not gstin_is_valid(read):
            leaf.update(value=on_page, confidence=_FILLED_CONFIDENCE)
            warnings.append(f"The {label} GSTIN was read as {read}, which is not a valid GSTIN; the invoice "
                            f"shows {on_page}, which has been filled in - please confirm it.")
        else:
            leaf["confidence"] = min(leaf.get("confidence") or 0.3, 0.3)
            warnings.append(f"Two readings of the {label} GSTIN disagree ({read} and {on_page}) - "
                            "please check it against the invoice.")

    claimed = {str(((fields.get(p) or {}).get("gstin") or {}).get("value") or "").strip().upper()
               for p in ("supplier", "bill_to", "ship_to")}
    bill_to = (fields.get("bill_to") or {}).get("gstin") or {}
    unclaimed = [g for g in every if g not in claimed]
    if not bill_to.get("value") and unclaimed:
        warnings.append("The invoice shows GSTIN " + ", ".join(unclaimed) + ", which was not read into "
                        "Bill-to or Ship-to - please check which party it belongs to.")
    return warnings
