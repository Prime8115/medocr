"""Read a scanned invoice's line-item table with Tesseract - free, local, exact.

A scan used to go straight to the paid vision model, which is the expensive path
and not exact on the numbers that become stock values. But Tesseract reports
WHERE each word sits, and we already have a column rebuilder that works on word
coordinates alone - the one proven on the digital invoices. Feeding one into the
other reads the table off a scan with no AI call at all.

What this is and is not:

* It reuses `pdf_table.tables_from_words` verbatim. There is no second column
  implementation to drift from the first - a fix to the header clustering helps
  both paths.
* It is attempted BEFORE the AI and simply declines when it cannot do the job:
  too few columns found, or the lines refusing to add up to the printed total.
  A scan this cannot read still goes to the vision model exactly as before.
* Every figure is then checked by the same reconciliation as any other invoice,
  so a misread digit shows up as a total that does not match rather than as
  silent stock corruption. That check is what makes trusting OCR numbers safe.

Coordinates matter more than they look. Tesseract measures in pixels of the
image we rendered; the rebuilder's tolerances are in PDF points (72 per inch).
Everything is scaled back to points on the way in, or a 300 dpi render makes
every gap look four times wider than the tuning expects and every column splits.
"""
import csv
import io
import logging
import subprocess
from typing import Dict, List, Optional, Sequence

from app.config import settings
from app.services.ocr.pdf_table import Word, tables_from_words

__all__ = [
    "available", "describe", "page_words", "tables_from_words",
    "tables_per_page", "words_per_page", "words_to_text",
]

log = logging.getLogger(__name__)

# Rendering resolution. 300 dpi is what Tesseract reads printed invoices best
# at; below ~200 the small print in a line-item table starts to break up.
RENDER_DPI = 300

# PDF points per inch - the unit the column rebuilder is tuned in.
_POINTS_PER_INCH = 72.0

# Tesseract's own per-word confidence, 0-100. Below this a "word" is usually
# speckle from the scan, and letting it through invents columns.
MIN_WORD_CONFIDENCE = 40.0

# Page segmentation mode 6: "assume a single uniform block of text". On a
# ruled invoice table this keeps a row's cells on one text line, where the
# fully-automatic mode 3 tends to split a wide table into separate blocks and
# scramble the reading order.
_PSM = "6"


# Tesseract reads a ruled table's vertical lines as characters - mostly "|", and
# "{" "}" "[" "]" where a rule meets a horizontal one. Left in, they both dirty
# every cell value ("100}") and weld a rule onto its neighbouring word
# ("ENDOCAL D FORTE |30045090"), which shifts the whole column mapping.
_RULE_CHARS = "|{}[]_"


def _clean_word(text: str) -> str:
    """A word with the table's own ruled lines stripped off it.

    Interior rules matter as much as the edges: a rule read between two cells
    arrives as one token, "MEGAS|EDC12402", which buries a batch number inside
    the manufacturer's name. Splitting on the rule puts each value back in its
    own column, since each half keeps its own position.
    """
    cleaned = text.strip()
    for char in _RULE_CHARS:
        cleaned = cleaned.replace(char, " ")
    cleaned = " ".join(cleaned.split())
    return cleaned


def _tsv_to_words(tsv: str, scale: float) -> List[Word]:
    """Tesseract's TSV rows as positioned words, in PDF points.

    Columns are: level, page_num, block_num, par_num, line_num, word_num,
    left, top, width, height, conf, text.
    """
    words: List[Word] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        text = _clean_word(row.get("text") or "")
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            left, top = float(row["left"]), float(row["top"])
            width = float(row["width"])
        except (TypeError, ValueError, KeyError):
            continue
        if confidence < MIN_WORD_CONFIDENCE:
            continue
        words.append({
            "text": text,
            "x0": left * scale,
            "x1": (left + width) * scale,
            "top": top * scale,
            "conf": confidence,
        })
    return words


def page_words(image, dpi: int = RENDER_DPI) -> List[Word]:
    """Positioned words for one rendered page image, or [] if Tesseract cannot."""
    try:
        from PIL import Image  # noqa: F401  (image is already a PIL image)

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        result = subprocess.run(
            [settings.tesseract_cmd, "stdin", "stdout", "-l", settings.tesseract_lang,
             "--psm", _PSM, "tsv"],
            input=buf.getvalue(), capture_output=True,
            timeout=settings.tesseract_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tesseract_table: tesseract could not run (%s)", exc)
        return []
    if result.returncode != 0:
        log.warning("tesseract_table: tesseract exited %s: %s",
                    result.returncode, result.stderr[:200])
        return []
    return _tsv_to_words(result.stdout.decode("utf-8", "replace"), _POINTS_PER_INCH / dpi)


def _page_images(data: bytes, content_type: str, max_pages: int):
    """The document's pages as images, whatever it arrived as."""
    if content_type == "application/pdf":
        # Rendered together under the PDFium lock (see pdfium_safe); the
        # Tesseract reading that follows runs outside it.
        from app.services.ocr.pdfium_safe import render_pages

        yield from render_pages(data, RENDER_DPI / _POINTS_PER_INCH, max_pages=max_pages)
    else:
        from PIL import Image

        yield Image.open(io.BytesIO(data))


def tables_per_page(data: bytes, content_type: str,
                    max_pages: Optional[int] = None) -> List[List[List[List[str]]]]:
    """Line-item tables found on each page, read by Tesseract.

    One entry per page, each holding whatever tables that page yielded, in the
    same shape `page.extract_tables()` returns - so the caller can treat a scan
    exactly like a digital PDF.
    """
    limit = max_pages or settings.ocr_fallback_max_pages
    out: List[List[List[List[str]]]] = []
    try:
        for image in _page_images(data, content_type, limit):
            words = page_words(image)
            out.append(tables_from_words(words) if words else [])
    except Exception as exc:  # noqa: BLE001 - a scan we cannot read is not fatal
        log.warning("tesseract_table: could not read the document (%s)", exc)
    return out


def words_per_page(data: bytes, content_type: str,
                   max_pages: Optional[int] = None) -> List[Sequence[Word]]:
    """The positioned words of each page - for the header fields, which are read
    from text rather than from the table."""
    limit = max_pages or settings.ocr_fallback_max_pages
    out: List[Sequence[Word]] = []
    try:
        for image in _page_images(data, content_type, limit):
            out.append(page_words(image))
    except Exception as exc:  # noqa: BLE001
        log.warning("tesseract_table: could not read the document (%s)", exc)
    return out


def words_to_text(words: Sequence[Word], line_tolerance: float = 3.5) -> str:
    """Positioned words as plain text, one printed line per text line.

    The header patterns expect the page's reading order; Tesseract's own text
    output loses the column structure that keeps a party's details together.
    """
    from app.services.ocr.pdf_table import _visual_lines

    return "\n".join(
        " ".join(str(w["text"]) for w in line[1])
        for line in _visual_lines(words, line_tolerance)
    )


def available() -> bool:
    """Whether Tesseract is installed, so the caller can skip this tier."""
    import shutil

    return shutil.which(settings.tesseract_cmd) is not None


def describe() -> Dict[str, object]:
    """What this tier is configured to do - for diagnostics."""
    return {
        "available": available(),
        "dpi": RENDER_DPI,
        "psm": _PSM,
        "min_word_confidence": MIN_WORD_CONFIDENCE,
        "language": settings.tesseract_lang,
    }
