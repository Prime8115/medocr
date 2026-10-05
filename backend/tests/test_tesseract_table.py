"""Reading a scanned invoice's table with Tesseract instead of the paid model.

The TSV adapter is tested here with synthetic Tesseract output, so it runs
anywhere. The end-to-end reading needs Tesseract installed and is covered by
test_manual_entry / the live checks.
"""
import pytest

from app.services.ocr.tesseract_table import (
    MIN_WORD_CONFIDENCE,
    RENDER_DPI,
    _clean_word,
    _tsv_to_words,
    describe,
    words_to_text,
)

_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv(*rows):
    return "\n".join([_HEAD] + ["\t".join(str(c) for c in r) for r in rows])


def test_pixels_are_converted_to_pdf_points():
    """Tesseract measures in pixels of the image we rendered; the column
    rebuilder is tuned in PDF points. At 300 dpi a gap looks four times wider
    than the tuning expects, and every column splits."""
    tsv = _tsv([5, 1, 1, 1, 1, 1, 300, 600, 150, 30, 96, "ESPRA"])
    scale = 72.0 / RENDER_DPI
    word = _tsv_to_words(tsv, scale)[0]

    assert word["x0"] == pytest.approx(300 * scale)      # 72pt = 1 inch in
    assert word["x1"] == pytest.approx(450 * scale)
    assert word["top"] == pytest.approx(600 * scale)
    assert word["text"] == "ESPRA"


def test_low_confidence_words_are_dropped():
    """Speckle from a scan arrives as low-confidence "words" and invents columns."""
    tsv = _tsv(
        [5, 1, 1, 1, 1, 1, 10, 10, 40, 12, 95, "GOOD"],
        [5, 1, 1, 1, 1, 2, 60, 10, 10, 12, MIN_WORD_CONFIDENCE - 10, "~"],
    )
    assert [w["text"] for w in _tsv_to_words(tsv, 1.0)] == ["GOOD"]


def test_blank_and_unparseable_rows_are_skipped():
    tsv = _tsv(
        [5, 1, 1, 1, 1, 1, 10, 10, 40, 12, 95, "   "],
        [5, 1, 1, 1, 1, 2, "x", 10, 40, 12, 95, "BAD"],
        [5, 1, 1, 1, 1, 3, 10, 10, 40, 12, 95, "KEEP"],
    )
    assert [w["text"] for w in _tsv_to_words(tsv, 1.0)] == ["KEEP"]


# --------------------------- the table's own rules ---------------------------
# Tesseract reads a ruled table's vertical lines as characters. Left in, they
# dirty every value ("100}") and weld a rule onto its neighbour
# ("MEGAS|EDC12402"), which shifts the whole column mapping.
@pytest.mark.parametrize("raw,expected", [
    ("100}", "100"),
    ("|1,750.25", "1,750.25"),
    ("[ESPRA 40]", "ESPRA 40"),
    ("MEGAS|EDC12402", "MEGAS EDC12402"),   # an interior rule splits the pair
    ("|", ""),
    ("}{", ""),
    ("___", ""),
    ("ESPRA 40 TAB", "ESPRA 40 TAB"),       # untouched when there is no rule
])
def test_ruled_line_characters_are_removed(raw, expected):
    assert _clean_word(raw) == expected


def test_a_row_of_only_rules_contributes_nothing():
    tsv = _tsv(
        [5, 1, 1, 1, 1, 1, 10, 10, 4, 12, 95, "|"],
        [5, 1, 1, 1, 1, 2, 40, 10, 60, 12, 95, "ESPRA"],
    )
    assert [w["text"] for w in _tsv_to_words(tsv, 1.0)] == ["ESPRA"]


def test_words_become_text_in_printed_line_order():
    """The header patterns expect the page's reading order; Tesseract's own text
    output loses the column structure that keeps a party's details together."""
    words = [
        {"text": "INVOICE", "x0": 10, "x1": 60, "top": 10},
        {"text": "NO", "x0": 65, "x1": 90, "top": 10},
        {"text": "ACME", "x0": 10, "x1": 50, "top": 40},
    ]
    assert words_to_text(words) == "INVOICE NO\nACME"


def test_describe_reports_the_configuration():
    out = describe()
    assert out["dpi"] == RENDER_DPI
    assert "available" in out and "language" in out


def test_the_tier_is_off_by_default():
    """On real scans it currently recovers the totals and part of the table but
    loses lines where OCR drops a header word. The reconciliation gate catches
    that, so nothing wrong is kept - but the OCR pass costs ~30s before falling
    back, and a pharmacist at a counter should not wait for a fallback."""
    from app.config import settings

    assert settings.ocr_tesseract_tables is False
