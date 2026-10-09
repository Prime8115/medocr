"""Recover a line-item table from a PDF that doesn't draw one.

pdfplumber's `extract_tables()` finds tables by their ruled lines. Plenty of real
distributor invoices don't draw any - the columns are held together by
x-alignment alone, and `extract_tables()` returns nothing at all. Of the four
supplier invoices we have, three are like that.

So this module rebuilds the table from word coordinates instead:

  1. group words into visual lines by their vertical position;
  2. find the header band (a header is often stacked over 2-3 lines, e.g.
     "Total / Qty.EA" or "Price to / Retailer");
  3. cluster the header words into columns by horizontal overlap;
  4. assign every data word to a column by where it sits.

It returns the same shape as `page.extract_tables()` - a list of tables, each a
list of rows, each row a list of cell strings - so the column mapping and row
building downstream are shared with the ruled-table path and cannot drift apart.
"""
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# Words that mark a line as the column header of a line-item table.
_HEADER_KEYWORDS = (
    "batch", "lot", "bno", "qty", "quantity", "hsn", "mrp", "rate", "ptr", "pts",
    "amount", "value", "exp", "expiry", "product", "description", "item",
    "pack", "free", "disc", "gst", "uom", "nir",
)
# A header needs several of them: "Rate" alone appears in plenty of prose.
_MIN_HEADER_HITS = 4

# A line that ends the line-item table.
_CARRIED_TOTAL = re.compile(
    r"\b(balance\s*b\s*/?\s*f|balance\s*c\s*/?\s*f|brought\s*forward|carried\s*forward|"
    r"carried\s*over|b\s*/\s*f\b|c\s*/\s*f\b)", re.I)
_FOOTER = re.compile(
    # "GST Summary (15621.50 @ 6.00% SGST=937.29,CGST=937.29)" - Overseas sets
    # its summary block in the table's own columns, so every figure on that line
    # read as a second line item and doubled the invoice.
    r"\b(sub\s*total|grand\s*total|total\s*taxable|basic\s*amount|summary|"
    r"in\s*words|"
    r"whether\s*tax|any\s*rcm|terms\s*(and|&)\s*cond|jurisdiction|rupees\s*:|"
    r"bank\s*(account|name)|for\s+stockist|e\s*&\s*o\.?e|subject\s+to|declaration|"
    # The warranty/declaration block JB prints under its last page. Left
    # unrecognised, its prose was parsed as line items - and one of them
    # harvested the invoice's own total, nearly doubling the line sum.
    r"hereby|warranty|contravene|properly\s*packed|responsible\s*for|certified|"
    r"once\s*sold|signator|authorised)\b"
    r"|\bIRN\s*[:#]",
    re.I,
)
_NUMERIC = re.compile(r"^-?[\d,]+\.?\d*$")

# Vertical tolerance when deciding two words share a line, in points.
_LINE_TOLERANCE = 3.5
# How close two glyphs must be before they count as one word. pdfplumber's
# default of 3 is too loose for invoices that carry no space characters at all -
# Bharat's product names arrived as "AntiD150mcg/1mlPFS**". At 1.5 the same line
# reads "AntiD 150mcg/1ml PFS **", which is what the paper says and what
# inventory matching needs.
WORD_TOLERANCE = 1.5

# Horizontal gap below which two header words belong to the same column.
# Tuned on real invoices: JB prints "Mfg Cd." and "Total Qty.EA" 6pt apart, so
# anything looser welds the manufacturer code onto the quantity.
_COLUMN_GAP = 5.0


Word = Dict[str, object]
Line = Tuple[float, List[Word]]


def _visual_lines(words: Sequence[Word], tolerance: float = _LINE_TOLERANCE) -> List[Line]:
    """Group words into lines by vertical position, left to right.

    Grouped by walking the words in vertical order rather than by rounding each
    `top` into a bucket. Rounding splits a row whenever it straddles a bucket
    boundary - JB sets its product codes a fraction higher than the rest of the
    row, and every line was being torn in two, which doubled both the item count
    and the invoice value.
    """
    ordered = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    lines: List[Line] = []
    current: List[Word] = []
    anchor = 0.0
    for word in ordered:
        top = float(word["top"])
        if current and top - anchor <= tolerance:
            current.append(word)
        else:
            if current:
                lines.append((anchor, sorted(current, key=lambda w: float(w["x0"]))))
            current, anchor = [word], top
    if current:
        lines.append((anchor, sorted(current, key=lambda w: float(w["x0"]))))
    return lines


def _line_text(line: Line) -> str:
    return " ".join(str(w["text"]) for w in line[1])


def _header_hits(line: Line) -> int:
    text = _line_text(line).lower()
    return sum(1 for kw in _HEADER_KEYWORDS if kw in text)


def _looks_like_header_continuation(line: Line) -> bool:
    """A stacked second/third header line: short words, no real data in it."""
    words = [str(w["text"]) for w in line[1]]
    if not words or len(words) > 24:
        return False
    numeric = sum(1 for w in words if _NUMERIC.match(w) and len(w.replace(",", "")) > 2)
    return numeric == 0 and all(len(w) <= 14 for w in words)


def _contributes_to_header(line: Line) -> bool:
    """Whether a stacked line adds anything to the header.

    Usually that means a keyword of its own. But the last line of a stacked
    header is often a lone fragment with no keyword at all - Abbott prints the
    "%" of "CGST% / SGST/UTGST %" on its own line, and Menarini the "Taxes)" of
    "MRP (Incl. Taxes)". Dropping it is not cosmetic: without the "%", a column
    headed "SGST/UTGST" reads as an AMOUNT column, and a tax RATE of 6.00 would
    be filed as six rupees of tax.
    """
    if _header_hits(line) > 0:
        return True
    words = [str(w["text"]) for w in line[1]]
    return bool(words) and len(words) <= 4 and all(len(w) <= 7 for w in words)


def _find_header_band(lines: List[Line]) -> Optional[Tuple[int, int]]:
    """(first, last) line indices of the column header, or None."""
    best: Optional[Tuple[int, int]] = None
    for i, line in enumerate(lines):
        hits = _header_hits(line)
        if hits < _MIN_HEADER_HITS:
            continue
        if best is None or hits > best[1]:
            best = (i, hits)
    if best is None:
        return None

    start = best[0]
    end = start
    for j in range(start + 1, min(start + 3, len(lines))):
        if _looks_like_header_continuation(lines[j]) and _contributes_to_header(lines[j]):
            end = j
        else:
            break
    # ...and upward. The line with the most keywords is not always the top of
    # the header: Menarini stacks "MRP" and "HSN / Batch No / Gross / Taxable"
    # ABOVE its widest heading line. Growing only downward left those columns
    # untitled, so MRP's values fell into the Quantity column ("13.00 126.00")
    # and, with no "Batch No" heading anywhere, the whole correctly-rebuilt
    # table was rejected for want of a batch marker - sending the invoice to
    # the AI. A data row cannot be swallowed here: it carries numbers, which
    # `_looks_like_header_continuation` refuses.
    for j in range(start - 1, max(-1, start - 3), -1):
        if _looks_like_header_continuation(lines[j]) and _header_hits(lines[j]) > 0:
            start = j
        else:
            break
    return start, end


def _cluster_columns(band: List[Line]) -> List[dict]:
    """Merge the header band's words into columns by horizontal overlap."""
    words = [w for _, row in band for w in row]
    if not words:
        return []
    words.sort(key=lambda w: float(w["x0"]))

    columns: List[dict] = []
    for word in words:
        x0, x1 = float(word["x0"]), float(word["x1"])
        if columns and x0 - columns[-1]["x1"] <= _COLUMN_GAP:
            col = columns[-1]
            col["x1"] = max(col["x1"], x1)
            col["words"].append(word)
        else:
            columns.append({"x0": x0, "x1": x1, "words": [word]})

    for col in columns:
        ordered = sorted(col["words"], key=lambda w: (float(w["top"]), float(w["x0"])))
        col["label"] = " ".join(str(w["text"]) for w in ordered)
    return columns


def _add_unheaded_leading_column(columns: List[dict], data: List[Line]) -> List[dict]:
    """Recover a column whose heading was never read.

    OCR of a scan drops the odd header word, and the leftmost one is a common
    casualty: Kanchan's "Description" vanished, so "HSN Code" became the first
    column and every product name - printed well to its left - was swept into
    it, giving cells like "ENDOCAL D FORTE 30045090".

    If the data rows consistently carry words to the LEFT of the first heading,
    those words are their own column and it simply has no title. Adding it back
    restores the alignment of every column after it, which no amount of
    per-field guessing downstream can do.
    """
    if not columns or not data:
        return columns
    first_x = columns[0]["x0"]
    # A gap big enough that the words cannot belong to the first heading.
    margin = max(6.0, _COLUMN_GAP)

    rows_with, left_x0, left_x1 = 0, [], []
    for _, row in data:
        outside = [w for w in row if float(w["x1"]) <= first_x - margin]
        if outside:
            rows_with += 1
            left_x0.append(min(float(w["x0"]) for w in outside))
            left_x1.append(max(float(w["x1"]) for w in outside))

    # Needs to be the rule, not one stray word: most rows must show it.
    if rows_with < 2 or rows_with * 2 <= len(data):
        return columns
    return [{"x0": min(left_x0), "x1": max(left_x1), "words": [], "label": ""}] + columns


def _boundaries(columns: List[dict]) -> List[float]:
    """Split points between columns: the midpoint of the gap between them.

    Headers are usually left-aligned while their numbers are right-aligned, so a
    value can sit slightly outside its own header's span. Splitting on the gap
    keeps such a value with the right column.
    """
    edges = [float("-inf")]
    for left, right in zip(columns, columns[1:]):
        edges.append((left["x1"] + right["x0"]) / 2.0)
    edges.append(float("inf"))
    return edges


def _assign(line: Line, edges: List[float], n: int) -> List[str]:
    cells: List[List[str]] = [[] for _ in range(n)]
    for word in line[1]:
        centre = (float(word["x0"]) + float(word["x1"])) / 2.0
        for i in range(n):
            if edges[i] <= centre < edges[i + 1]:
                cells[i].append(str(word["text"]))
                break
    return [" ".join(c).strip() for c in cells]


def _is_record_start(cells: List[str], numeric_columns: Sequence[int]) -> bool:
    """A row that carries the line's numbers, versus a wrapped continuation.

    Product names and manufacturer names spill onto their own lines; those carry
    no numbers and belong to the record above them.
    """
    filled = sum(1 for i in numeric_columns if i < len(cells) and _NUMERIC.match(cells[i].replace(" ", "")))
    return filled >= 2


def _page_words(page) -> List[Word]:
    try:
        return page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=WORD_TOLERANCE)
    except Exception as exc:  # noqa: BLE001 - a page we cannot read is not fatal
        log.debug("pdf_table: extract_words failed (%s)", exc)
        return []


def extract_word_tables(page, layout: Optional[List[dict]] = None) -> List[List[List[str]]]:
    """Rebuild line-item tables from a PDF page's word positions. `layout` is
    an earlier page's columns, for a continuation page that prints no header."""
    return tables_from_words(_page_words(page), layout)


def page_layout(page) -> Optional[List[dict]]:
    """The item table's columns on this page - its headings and where they
    sit - or None when the page prints no header band."""
    lines = _visual_lines(_page_words(page))
    band = _find_header_band(lines)
    if band is None:
        return None
    columns = _cluster_columns(lines[band[0]:band[1] + 1])
    body = lines[band[1] + 1:]
    for i, line in enumerate(body):
        if _FOOTER.search(_line_text(line)):
            body = body[:i]
            break
    columns = _add_unheaded_leading_column(columns, body)
    return columns if len(columns) >= 5 else None


def tables_from_words(words: Sequence[Word], layout: Optional[List[dict]] = None) -> List[List[List[str]]]:
    """Rebuild line-item tables from positioned words. Same shape as extract_tables().

    The words may come from anywhere that can say where each one sits - a
    digital PDF's own text, or Tesseract reading a scan. Everything below works
    on coordinates alone, so a scanned invoice goes through exactly the column
    logic that was proven on the digital ones, rather than a second
    implementation that would drift from it.

    Each word needs `text`, `x0`, `x1` and `top`.
    """
    if not words:
        return []

    lines = _visual_lines(words)
    band = _find_header_band(lines)
    if band is None:
        if not layout:
            return []
        # A continuation page that prints no header of its own (Alkem's pages
        # 2-4): the first page's columns, and the rows from the first real
        # record on - the page's own letterhead above it is not a row.
        columns, end, carried = list(layout), -1, True
    else:
        start, end = band
        carried = False
        columns = _cluster_columns(lines[start:end + 1])
        # Only the real rows may vote on the column layout: the declaration and
        # tax-summary prose beneath them aligns with nothing.
        body = lines[end + 1:]
        for i, line in enumerate(body):
            if _FOOTER.search(_line_text(line)):
                body = body[:i]
                break
        columns = _add_unheaded_leading_column(columns, body)
    if len(columns) < 5:
        return []
    edges = _boundaries(columns)
    n = len(columns)
    header = [col["label"] for col in columns]

    # Columns whose header suggests they hold numbers — used to tell a real row
    # from a wrapped continuation line.
    numeric_columns = [
        i for i, col in enumerate(columns)
        if re.search(r"qty|quantity|rate|amount|value|mrp|ptr|pts|nir|price", col["label"], re.I)
    ]
    if not numeric_columns:
        numeric_columns = list(range(n))

    rows: List[List[str]] = []
    row_tops: List[float] = []
    description_col = next(
        (i for i, col in enumerate(columns)
         if re.search(r"product|description|item|particular|goods", col["label"], re.I)),
        0,
    )

    # Columns whose heading stacks two fields - "Mfg.Dt / Exp.Dt" (Overseas),
    # "Batch No / Mfg.Date" (Menarini). The record line carries the first and
    # the wrapped line beneath it the second, so the wrapped line's value is kept
    # as a second line in the cell - the convention a ruled table's cells use -
    # instead of being thrown away with the rest of the wrapped line. Overseas's
    # expiry, Jul-27, lived only there; we were reporting its mfg date, Aug-25,
    # as the expiry.
    stacked = {
        i for i, col in enumerate(columns)
        if sum(bool(re.search(p, col["label"], re.I))
               for p in (r"batch|lot", r"mfg|mfd", r"exp")) >= 2
    }
    continuation_cols = {
        i for i, col in enumerate(columns)
        if i in stacked or re.search(r"cgst|sgst|igst|utgst|gst|tax|disc|rate|%", col["label"], re.I)
    }

    # Lines with no numbers of their own, waiting to be given to a record:
    # (top, description text, {stacked column: value}).
    orphans: List[Tuple[float, str, Dict[int, str]]] = []

    def give(record: List[str], otext: str, ovals: Dict[int, str], before: bool) -> None:
        if otext:
            record[description_col] = (f"{otext} {record[description_col]}" if before
                                       else f"{record[description_col]} {otext}").strip()
        for col, val in ovals.items():
            if record[col] and "\n" not in record[col]:
                record[col] = f"{record[col]}\n{val}"

    for line in lines[end + 1:]:
        text = _line_text(line)
        if _FOOTER.search(text):
            break
        if _CARRIED_TOTAL.search(text):
            # A page's running total brought forward or carried over (Eris
            # prints "Balance B/F 2,359,633.25" above a page's first item,
            # which then took that as its amount). Never a product, never part
            # of one.
            continue
        cells = _assign(line, edges, n)
        if not any(cells):
            continue
        if carried and not rows and not _is_record_start(cells, numeric_columns):
            continue  # the continuation page's letterhead, above its first row
        if (_is_record_start(cells, numeric_columns) and rows and not cells[description_col].strip()
                and all(not rows[-1][i].strip() for i, c in enumerate(cells) if c.strip())):
            # The rest of the row above, printed on the line beneath it: Alkem
            # prints a long item's prices and amount under its name and
            # quantities. No product name of its own, and every figure lands
            # in a column the row above left empty.
            for i, c in enumerate(cells):
                if c.strip():
                    rows[-1][i] = c
            continue
        if _is_record_start(cells, numeric_columns):
            top = line[0]
            # Decide where the orphans between the last record and this one go.
            for otop, otext, ovals in orphans:
                if rows and abs(otop - row_tops[-1]) <= abs(otop - top):
                    give(rows[-1], otext, ovals, before=False)
                else:
                    give(cells, otext, {}, before=True)
            orphans = []
            rows.append(cells)
            row_tops.append(top)
        else:
            extra = cells[description_col].strip()
            values = {i: cells[i].strip() for i in continuation_cols if cells[i].strip()}
            if extra or values:
                orphans.append((line[0], extra, values))

    # Anything left over belongs to the last record.
    for _, otext, ovals in orphans:
        if rows:
            give(rows[-1], otext, ovals, before=False)

    if not rows:
        return []
    return [[header] + rows]
