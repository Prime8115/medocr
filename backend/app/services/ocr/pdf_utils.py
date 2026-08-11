"""PDF helpers for chunked processing of long documents."""
import io
from typing import List


def page_count(data: bytes) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - if we can't read it, treat as single unit
        return 1


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
