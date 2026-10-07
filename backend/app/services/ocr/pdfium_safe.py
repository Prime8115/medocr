"""Every use of PDFium in the app, behind one lock.

PDFium - the renderer under pypdfium2 - is not thread-safe: two threads using
it at once, even on two different documents, can crash the whole process with a
segmentation fault. The app does run it from several threads: OCR jobs run
concurrently (`ocr_max_concurrent_jobs`), and a scan is read a second time by
Tesseract in parallel with the AI call (cross_read). Deploys were failing at
random with exit code 139 exactly so - the cross-read thread rendering a page
while the main thread rendered the same scan for the AI - and in production the
same race could take down the worker processing an upload.

So nothing outside this module touches pypdfium2. Each function opens the
document, does its work and closes it inside the lock, and hands back plain
data - bytes, or PIL images copied out of PDFium's memory - so nothing that
outlives the lock still points into PDFium. Rendering is quick; the slow work
done with the pages afterwards (Tesseract, the AI call) runs outside the lock.
"""
import io
import threading
from typing import List, Optional

# Re-entrant, so a helper that calls another helper cannot deadlock itself.
_LOCK = threading.RLock()


def render_pages(data: bytes, scale: float, max_pages: Optional[int] = None,
                 grayscale: bool = False, only: Optional[List[int]] = None) -> list:
    """The document's pages as PIL images, rendered at `scale` (dpi / 72).

    `max_pages` caps how many are rendered from the start; `only` renders just
    those page indexes. Raises what pypdfium2 raises for an unreadable file.
    """
    import pypdfium2 as pdfium

    with _LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            count = len(pdf)
            indexes = [i for i in (only if only is not None else range(count)) if i < count]
            if max_pages is not None:
                indexes = indexes[:max_pages]
            images = []
            for i in indexes:
                page = pdf[i]
                try:
                    image = page.render(scale=scale, grayscale=grayscale).to_pil()
                    # A real copy, so the image does not share PDFium's buffer.
                    images.append(image.copy())
                finally:
                    page.close()
            return images
        finally:
            pdf.close()


def page_total(data: bytes) -> int:
    """How many pages PDFium sees in the document."""
    import pypdfium2 as pdfium

    with _LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()


def resave(data: bytes) -> Optional[bytes]:
    """The document written back out by PDFium - which repairs many files
    other readers refuse - or None when it has no pages. Raises what
    pypdfium2 raises for a file it cannot open (a password, say)."""
    import pypdfium2 as pdfium

    with _LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            if len(pdf) == 0:
                return None
            buf = io.BytesIO()
            pdf.save(buf)
            return buf.getvalue()
        finally:
            pdf.close()
