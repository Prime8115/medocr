"""Uploads checked and tidied before they are queued - so a file that cannot
be read is refused at once with a plain reason, instead of reaching the queue
and ending in "Failed" or an empty review form.

* The file's real type is read from its first bytes, not from what the phone
  or browser claimed: phones send photos as "application/octet-stream", and
  a PDF named scan.jpg is still a PDF.
* Photos: iPhone HEIC/HEIF becomes JPEG; the camera's rotation (EXIF) is
  applied; very large photos are scaled down; a photo that cannot be opened,
  is blank, or is too small to read is refused.
* PDFs: a password-protected PDF is refused (unless the password is empty,
  as with many "owner-locked" supplier PDFs - those are unlocked); a damaged
  one is repaired when the PDF engine can, and refused when it cannot; blank
  pages (scanner separator sheets, empty backs) are removed.
* Several invoices in one PDF are found, so the upload can make one
  document per invoice (see `invoice_groups`).

Everything here is local and free: no AI call is spent on any of it.
"""
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.config import settings

log = logging.getLogger(__name__)

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
HEIC = "image/heic"

EXTENSION = {PDF: ".pdf", JPEG: ".jpg", PNG: ".png", WEBP: ".webp"}

# What the user is told. Plain words about their file - never a stack trace.
MSG_UNSUPPORTED = "This file type is not supported. Please upload a photo (JPEG, PNG, WebP or HEIC) or a PDF."
MSG_BAD_IMAGE = "This photo could not be opened - it may be damaged. Please take or choose it again."
MSG_BLANK_IMAGE = "This photo looks blank. Please take the photo again with the document in view."
MSG_SMALL_IMAGE = "This photo is too small to read. Please take it again, closer to the document."
MSG_PDF_PASSWORD = ("This PDF is password-protected. Please remove the password (or print it to a new PDF) "
                    "and upload it again.")
MSG_PDF_DAMAGED = "This PDF is damaged and could not be opened. Please download or export it again."
MSG_PDF_EMPTY = "This PDF has no pages."
MSG_PDF_BLANK = "Every page of this PDF looks blank. Please check that you chose the right file."

_MIN_IMAGE_SIDE = 200          # pixels; below this nothing on a bill is legible
_BLANK_STDDEV = 4.0            # grey-level spread of a photo with nothing in it
# A page is blank when almost none of it is ink - but a scanned blank page has
# specks of dust, so "none" cannot mean zero.
_BLANK_INK_FRACTION = 0.002
_BLANK_RENDER_SCALE = 0.25     # ~18 dpi: enough to see ink, cheap per page
_MAX_PAGES_CHECKED_FOR_BLANKS = 200


class UploadRejected(Exception):
    """The file cannot be read. `message` is for the user, as it stands."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason  # short code, for logs and tests


@dataclass
class Prepared:
    data: bytes
    content_type: str
    filename: str
    sha256: str                       # of the file exactly as uploaded
    notes: List[str] = field(default_factory=list)   # what was done to it, for the log
    pages_removed: int = 0


# ------------------------------------------------------------------ type --
def sniff(data: bytes) -> Optional[str]:
    """The file's type from its first bytes, or None if it is none we take."""
    head = data[:64]
    if head.lstrip()[:5] == b"%PDF-" or b"%PDF-" in data[:1024]:
        return PDF
    if head[:3] == b"\xff\xd8\xff":
        return JPEG
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"heim", b"heis", b"hevc",
                                               b"hevx", b"mif1", b"msf1", b"avif"):
        return HEIC
    return None


def _stem(filename: Optional[str]) -> str:
    name = (filename or "upload").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:80] or "upload"


# ---------------------------------------------------------------- photos --
def _open_image(data: bytes, kind: str):
    from PIL import Image

    if kind == HEIC:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:  # pragma: no cover - listed in requirements
            raise UploadRejected(MSG_UNSUPPORTED, "heic_unsupported")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception as exc:  # noqa: BLE001 - any decoder error means the same to the user
        log.info("upload: image did not open: %s", exc)
        raise UploadRejected(MSG_BAD_IMAGE, "image_unreadable")


def _prepare_image(data: bytes, kind: str, notes: List[str]):
    from PIL import ImageOps, ImageStat

    img = _open_image(data, kind)
    changed = kind == HEIC
    if kind == HEIC:
        notes.append("converted HEIC to JPEG")

    if _orientation(img) not in (None, 1):
        # Phones store a photo sideways and say how to turn it; not every
        # reader honours that, so the stored copy is turned once, here.
        img = ImageOps.exif_transpose(img)
        notes.append("rotated upright")
        changed = True

    if min(img.size) < _MIN_IMAGE_SIDE:
        raise UploadRejected(MSG_SMALL_IMAGE, "image_too_small")
    grey = img.convert("L")
    if ImageStat.Stat(grey).stddev[0] < _BLANK_STDDEV:
        raise UploadRejected(MSG_BLANK_IMAGE, "image_blank")

    longest = settings.upload_max_image_side
    if longest and max(img.size) > longest:
        scale = longest / max(img.size)
        from PIL import Image

        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        notes.append(f"scaled down to {img.width}x{img.height}")
        changed = True

    if not changed:
        return data, kind
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), JPEG


def _orientation(img) -> Optional[int]:
    try:
        return img.getexif().get(0x0112)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ PDFs --
def _open_pdf(data: bytes, notes: List[str]):
    """(reader, bytes) for a PDF that can be read - unlocked or repaired if
    need be - else UploadRejected."""
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many supplier PDFs are locked only against editing: they open
            # with an empty password, and the copy we keep should not be locked.
            try:
                ok = reader.decrypt("")
            except Exception:  # noqa: BLE001 - unsupported cipher and the like
                ok = 0
            if not ok:
                raise UploadRejected(MSG_PDF_PASSWORD, "pdf_password")
            writer = PdfWriter(clone_from=reader)
            buf = io.BytesIO()
            writer.write(buf)
            data = buf.getvalue()
            reader = PdfReader(io.BytesIO(data))
            notes.append("removed an empty-password lock")
        len(reader.pages)  # a broken page tree fails here
        return reader, data
    except UploadRejected:
        raise
    except Exception as exc:  # noqa: BLE001 - try the more forgiving engine
        log.info("upload: pypdf could not read the PDF (%s); trying to repair it", exc)

    repaired = _repair_pdf(data)
    if repaired is None:
        raise UploadRejected(MSG_PDF_DAMAGED, "pdf_damaged")
    notes.append("repaired a damaged PDF")
    try:
        return PdfReader(io.BytesIO(repaired)), repaired
    except Exception:  # noqa: BLE001
        raise UploadRejected(MSG_PDF_DAMAGED, "pdf_damaged")


def _repair_pdf(data: bytes) -> Optional[bytes]:
    """PDFium opens many files other readers refuse (a broken cross-reference
    table, a truncated tail); saving from it writes a sound file."""
    from app.services.ocr.pdfium_safe import resave

    try:
        return resave(data)
    except Exception as exc:  # noqa: BLE001
        if "password" in str(exc).lower():
            raise UploadRejected(MSG_PDF_PASSWORD, "pdf_password")
        return None


def _blank_pages(data: bytes, texts: List[str]) -> List[int]:
    """Indexes of the pages with no text and next to no ink."""
    from app.services.ocr.pdfium_safe import page_total, render_pages

    try:
        total = min(page_total(data), _MAX_PAGES_CHECKED_FOR_BLANKS)
        candidates = [i for i in range(total) if not (i < len(texts) and texts[i].strip())]
        if not candidates:
            return []
        images = render_pages(data, _BLANK_RENDER_SCALE, grayscale=True, only=candidates)
    except Exception as exc:  # noqa: BLE001 - never refuse a file over this check
        log.info("upload: blank-page check skipped: %s", exc)
        return []
    blank = []
    for i, image in zip(candidates, images):
        grey = image.convert("L")
        ink = sum(grey.histogram()[:160])  # clearly darker than paper
        if ink <= _BLANK_INK_FRACTION * grey.width * grey.height:
            blank.append(i)
    return blank


def _page_texts(reader) -> List[str]:
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            out.append("")
    return out


def _without_pages(reader, drop: List[int]) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i not in drop:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _prepare_pdf(data: bytes, notes: List[str]):
    reader, data = _open_pdf(data, notes)
    count = len(reader.pages)
    if count == 0:
        raise UploadRejected(MSG_PDF_EMPTY, "pdf_empty")
    texts = _page_texts(reader)
    blank = _blank_pages(data, texts)
    if len(blank) == count:
        raise UploadRejected(MSG_PDF_BLANK, "pdf_blank")
    if blank:
        data = _without_pages(reader, blank)
        notes.append(f"removed {len(blank)} blank page(s)")
    return data, len(blank)


# ------------------------------------------------------------------ entry --
def prepare(data: bytes, claimed_type: Optional[str], filename: Optional[str]) -> Prepared:
    """Check an upload and return what should be stored and read. Raises
    UploadRejected, with a message for the user, when it cannot be read."""
    digest = hashlib.sha256(data).hexdigest()
    kind = sniff(data)
    if kind is None:
        raise UploadRejected(MSG_UNSUPPORTED, "unsupported_type")
    notes: List[str] = []
    if claimed_type and claimed_type != kind and claimed_type not in ("application/octet-stream", "image/heif"):
        notes.append(f"sent as {claimed_type}, is {kind}")
    removed = 0
    if kind == PDF:
        out, removed = _prepare_pdf(data, notes)
        out_type = PDF
    else:
        out, out_type = _prepare_image(data, kind, notes)
    return Prepared(
        data=out,
        content_type=out_type,
        filename=_stem(filename) + EXTENSION[out_type],
        sha256=digest,
        notes=notes,
        pages_removed=removed,
    )


# ------------------------------------------------- several invoices, one PDF --
# Scanner OCR and Tally layouts both appear; the number must hold a digit, so a
# heading that runs on into the next label ("Invoice No. Dated") is not taken
# for a number.
_INVOICE_NO = re.compile(
    r"[il1|]nv[o0][il1]ce\s*(?:no|number|num|#)\b\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,24})", re.I)


def _page_invoice_numbers(text: str) -> set:
    # A word run into the number by the text layer is not part of it: Alkem's
    # continuation pages read "7613021411ALKEM", which split its one invoice
    # into two documents.
    return {re.sub(r"(?<=\d)[A-Z]{3,}$", "", m.group(1).upper())
            for m in _INVOICE_NO.finditer(text or "") if re.search(r"\d", m.group(1))}


_DOCUMENT_NO = re.compile(
    r"(?<![A-Za-z])(?:credit\s*note|debit\s*note|cn|dn|sap\s*doc|doc(?:ument)?)\.?\s*(?:no|number|#)\b\.?"
    r"\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,24})", re.I)


_NOTE_NO = re.compile(
    r"(?<![A-Za-z])(?:credit|debit)\s*note\s*(?:no|number|#)\b\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,24})", re.I)


def _page_note_numbers(text: str) -> set:
    """The page's own credit note number - or its debit note's, when it names
    no credit note. A credit note also cites the debit note it answers."""
    found = {"credit": set(), "debit": set()}
    for m in _NOTE_NO.finditer(text or ""):
        if re.search(r"\d", m.group(1)):
            kind = "credit" if m.group(0).lower().startswith("credit") else "debit"
            found[kind].add(re.sub(r"(?<=\d)[A-Z]{3,}$", "", m.group(1).upper()))
    return found["credit"] or found["debit"]


def _positioned_texts(data: bytes, pages: int) -> List[str]:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return [(page.extract_text() or "") for page in pdf.pages[:pages]]
    except Exception:  # noqa: BLE001
        return [""] * pages


def _page_document_numbers(text: str) -> set:
    return {m.group(1).upper() for m in _DOCUMENT_NO.finditer(text or "") if re.search(r"\d", m.group(1))}


def invoice_groups(data: bytes) -> List[List[int]]:
    """The pages of each separate invoice in a PDF, when it plainly holds more
    than one; otherwise a single group of every page.

    Only a PDF made by software is judged (never a scan's text layer), and only when the evidence is
    unambiguous: each page names at most one invoice number, a page naming
    none continues the invoice before it, and each number's pages are
    contiguous. Copies of one invoice (Original/Duplicate/Triplicate) share
    its number, so they stay together. Anything else - a statement listing
    many invoices, numbers that come back later - is left as one document:
    splitting wrongly is worse than not splitting.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        texts = _page_texts(reader)
    except Exception:  # noqa: BLE001
        return []
    every = [list(range(len(texts)))] if texts else []
    from app.services.ocr.pdf_utils import is_scanned_pdf

    # A scan's text is the scanner's guess: one misread digit would make two
    # copies of an invoice look like two invoices. Never split on it.
    if len(texts) < 2 or is_scanned_pdf(data) or sum(1 for t in texts if len(t.strip()) > 50) < len(texts) / 2:
        return every

    per_page = [_page_invoice_numbers(t) for t in texts]
    if not any(per_page):
        # No invoice number anywhere: a credit note names itself by its own
        # number. Ajanta sends two in one PDF, "SAP Doc. No.: 8510945928" on
        # the first page and "8510946051" on the rest.
        # Read with positions: pypdf prints Ajanta's labels and their values
        # in separate runs, and the number lost its label.
        per_page = [_page_document_numbers(t) for t in _positioned_texts(data, len(texts))]

    groups: List[List[int]] = []
    numbers: List[str] = []
    for i, text in enumerate(texts):
        found = per_page[i]
        if not found:
            # A page naming no invoice but a credit or debit note of its own is
            # that note, not the invoice's next page: Hindustan Capsule prints
            # its credit note C000084 after the invoice it is set off against.
            found = {f"NOTE:{n}" for n in _page_note_numbers(text)}
        if len(found) > 1:
            return every
        number = next(iter(found), None)
        if number is None or (numbers and number == numbers[-1]):
            if not groups:
                return every  # the first page must say which invoice it is
            groups[-1].append(i)
            continue
        if number in numbers:
            return every  # an invoice returning later: not a simple batch
        numbers.append(number)
        groups.append([i])
    return groups if len(groups) > 1 else every


def pdf_pages(data: bytes, pages: List[int]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for i in pages:
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
