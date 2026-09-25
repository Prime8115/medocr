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
    "batch", "lot", "qty", "quantity", "hsn", "mrp", "rate", "ptr", "pts",
    "amount", "value", "exp", "expiry", "product", "description", "item",
    "pack", "free", "disc", "gst", "uom", "nir",
)
# A header needs several of them: "Rate" alone appears in plenty of prose.
_MIN_HEADER_HITS = 4

# A line that ends the line-item table.
_FOOTER = re.compile(
    r"\b(sub\s*total|grand\s*total|total\s*taxable|basic\s*amount|tax\s*summary|"
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
        if _looks_like_header_continuation(lines[j]) and _header_hits(lines[j]) > 0:
            end = j
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


def extract_word_tables(page) -> List[List[List[str]]]:
    """Rebuild line-item tables from word positions. Same shape as extract_tables()."""
    try:
        words = page.extract_words(
            keep_blank_chars=False, use_text_flow=False, x_tolerance=WORD_TOLERANCE
        )
    except Exception as exc:  # noqa: BLE001 - a page we cannot read is not fatal
        log.debug("pdf_table: extract_words failed (%s)", exc)
        return []
    if not words:
        return []

    lines = _visual_lines(words)
    band = _find_header_band(lines)
    if band is None:
        return []
    start, end = band

    columns = _cluster_columns(lines[start:end + 1])
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

    # Lines with no numbers of their own, waiting to be given to a record.
    orphans: List[Tuple[float, str]] = []

    for line in lines[end + 1:]:
        text = _line_text(line)
        if _FOOTER.search(text):
            break
        cells = _assign(line, edges, n)
        if not any(cells):
            continue
        if _is_record_start(cells, numeric_columns):
            top = line[0]
            # Decide where the orphans between the last record and this one go.
            for otop, otext in orphans:
                if rows and abs(otop - row_tops[-1]) <= abs(otop - top):
                    rows[-1][description_col] = f"{rows[-1][description_col]} {otext}".strip()
                else:
                    cells[description_col] = f"{otext} {cells[description_col]}".strip()
            orphans = []
            rows.append(cells)
            row_tops.append(top)
        else:
            extra = cells[description_col].strip()
            if extra:
                orphans.append((line[0], extra))

    # Anything left over belongs to the last record.
    for _, otext in orphans:
        if rows:
            rows[-1][description_col] = f"{rows[-1][description_col]} {otext}".strip()

    if not rows:
        return []
    return [[header] + rows]
