"""PDF helpers for chunked processing of long documents."""
import io
from typing import List


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
    """True if the PDF's pages are pictures - a scanner's or a phone's - even
    when it also carries text.

    Scanners add a hidden text layer of their OWN reading of the picture (PDF
    text render mode 3, invisible). That reading is often wrong: the MSV
    Lifesciences bill's layer says GSTIN "33ABEFM031 5R128" and date
    "5-Oct-25" where the paper says 33ABEFM0315R1Z8 and 6-Oct-25. Text taken
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
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        images = [pdf[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(pdf))]
    finally:
        pdf.close()
    if not images:
        return data
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=dpi, quality=quality)
    return buf.getvalue()


def is_digital_pdf(data: bytes, min_chars_per_page: int = 200, sample_pages: int = 3) -> bool:
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
    return total >= min_chars_per_page * max(1, len(pages)) // 2


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
