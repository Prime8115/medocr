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


def is_digital_pdf(data: bytes, min_chars_per_page: int = 200) -> bool:
    """True if the PDF has substantial embedded text (computer-generated), so we
    can extract text directly instead of sending page images to the vision model.
    Scanned/photographed PDFs return False and use the image pipeline."""
    pages = extract_text_pages(data)
    if not pages:
        return False
    total = sum(len(t.strip()) for t in pages)
    # Digital if the average page carries real text.
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
