"""PDF helpers for chunked processing of long documents."""
import hashlib
import io
import threading
from collections import OrderedDict
from typing import List

# One scan asks "is this a digital PDF? a scanned one?" several times over -
# routing, the party check, the fallback, the parser - and each answer opened
# and parsed the whole file again. Under a burst of uploads that was the same
# work done five times per document. The answers are kept, keyed by the file's
# own hash, for the few files in flight.
_ANSWERS: "OrderedDict[tuple, bool]" = OrderedDict()
_ANSWERS_LOCK = threading.Lock()
_ANSWERS_KEPT = 64


def _remembered(kind: str, data: bytes, args: tuple, compute) -> bool:
    key = (kind, hashlib.blake2b(data, digest_size=16).digest(), args)
    with _ANSWERS_LOCK:
        if key in _ANSWERS:
            _ANSWERS.move_to_end(key)
            return _ANSWERS[key]
    answer = compute()
    with _ANSWERS_LOCK:
        _ANSWERS[key] = answer
        while len(_ANSWERS) > _ANSWERS_KEPT:
            _ANSWERS.popitem(last=False)
    return answer


def page_count(data: bytes) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - if we can't read it, treat as single unit
        return 1


def extract_text_pages(data: bytes) -> List[str]:
    """Return the embedded text of each page (empty string if none)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [(p.extract_text() or "") for p in reader.pages]
    except Exception:  # noqa: BLE001
        return []


def extract_text_sample(data: bytes, pages: int = 3) -> List[str]:
    """Embedded text of the first few pages only.

    Whether a PDF is computer-generated or scanned is a property of the whole
    document, so a sample settles it. Reading all 33 pages of a triplicate
    invoice just to answer that question cost two seconds of every upload.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        sample = list(reader.pages[:pages])
        # Include the last page too: a file whose opening pages are scanned
        # covers or images would otherwise be judged non-digital on its worst
        # pages and pushed to the AI unnecessarily.
        if len(reader.pages) > pages:
            sample.append(reader.pages[-1])
        return [(p.extract_text() or "") for p in sample]
    except Exception:  # noqa: BLE001
        return []


# A page is a scan when one picture covers most of it.
_SCAN_COVERAGE = 0.7


def _sample_indexes(total: int, pages: int) -> List[int]:
    idx = list(range(min(total, pages)))
    if total > pages:
        idx.append(total - 1)
    return idx


def is_scanned_pdf(data: bytes, sample_pages: int = 3) -> bool:
    return _remembered("scanned", data, (sample_pages,), lambda: _is_scanned_pdf(data, sample_pages))


def _is_scanned_pdf(data: bytes, sample_pages: int) -> bool:
    """True if the PDF's pages are pictures - a scanner's or a phone's - even
    when it also carries text.

    Scanners add a hidden text layer of their OWN reading of the picture (PDF
    text render mode 3, invisible). That reading is often wrong: on a real
    supplier's bill the layer had the "Z" of a GSTIN as "2", another GSTIN's
    "2" as "Z", and the date a day out ("5-Oct-25" for 6-Oct-25). Text taken
    from such a file is the scanner's guess, not the document - so a scan is
    always read from its picture, never from that layer.

    A page counts as scanned when a single image covers most of it; the
    sampled pages decide for the file (most of them, as with is_digital_pdf).
    """
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            idx = _sample_indexes(len(pdf.pages), sample_pages)
            scanned = 0
            for i in idx:
                page = pdf.pages[i]
                area = float(page.width * page.height) or 1.0
                for im in page.images:
                    w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0))
                    h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0))
                    if w * h >= _SCAN_COVERAGE * area:
                        scanned += 1
                        break
            return bool(idx) and scanned * 2 > len(idx)
    except Exception:  # noqa: BLE001 - unreadable here: the other checks decide
        return False


def image_only_pdf(data: bytes, dpi: int = 200, quality: int = 85) -> bytes:
    """The same pages as pictures only - the scanner's text layer left behind,
    so the AI reads what is printed, not what the scanner guessed."""
    from app.services.ocr.pdfium_safe import render_pages

    images = [img.convert("RGB") for img in render_pages(data, dpi / 72)]
    if not images:
        return data
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=dpi, quality=quality)
    return buf.getvalue()


def is_digital_pdf(data: bytes, min_chars_per_page: int = 200, sample_pages: int = 3) -> bool:
    return _remembered("digital", data, (min_chars_per_page, sample_pages),
                       lambda: _is_digital_pdf(data, min_chars_per_page, sample_pages))


def _is_digital_pdf(data: bytes, min_chars_per_page: int, sample_pages: int) -> bool:
    """True if the PDF was made by software (billing software, "print to PDF"),
    so its text IS the document and can be read directly instead of sending
    page images to the vision model. Scanned/photographed PDFs return False -
    including scans carrying a scanner's text layer (see is_scanned_pdf)."""
    if is_scanned_pdf(data, sample_pages):
        return False
    pages = extract_text_sample(data, sample_pages)
    if not pages:
        return False
    total = sum(len(t.strip()) for t in pages)
    # Digital if the average sampled page carries real text.
    if total < min_chars_per_page * max(1, len(pages)) // 2:
        return False
    return not _words_drawn_as_shapes(data, sample_pages)


def _words_drawn_as_shapes(data: bytes, sample_pages: int) -> bool:
    """True when most of the page's words are drawings, not text.

    Ferring (INMH21221) prints only its letterhead as text; the buyer, every
    line and the totals are drawn as outlines - 2,727 curves beside 306
    characters. Taken as digital, the AI was sent the text alone, the
    letterhead, and returned a bill of nothing. Such a page is read as a
    picture. A logo is a few hundred curves; this is thousands, outnumbering
    the characters several times over.
    """
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            idx = _sample_indexes(len(pdf.pages), sample_pages)
            drawn = sum(1 for i in idx
                        if len(pdf.pages[i].curves) > 1000
                        and len(pdf.pages[i].curves) > 3 * len(pdf.pages[i].chars))
            return bool(idx) and drawn * 2 > len(idx)
    except Exception:  # noqa: BLE001 - unreadable here: the text decides
        return False


def split_pdf(data: bytes, pages_per_chunk: int) -> List[bytes]:
    """Split a PDF into a list of smaller PDFs of `pages_per_chunk` pages each.

    Returns [data] unchanged if it isn't a splittable PDF or has <= chunk pages.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:  # pragma: no cover
        return [data]

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        return [data]

    n = len(reader.pages)
    if n <= pages_per_chunk:
        return [data]

    chunks: List[bytes] = []
    for start in range(0, n, pages_per_chunk):
        writer = PdfWriter()
        for i in range(start, min(start + pages_per_chunk, n)):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks
