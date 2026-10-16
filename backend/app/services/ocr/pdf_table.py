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
# A wrapped line that labels the product above it: its maker or marketer.
_DETAIL_LINE = re.compile(r"\s*(?:mfg|mfr|mkt|mfd|marketed\s+by|manufactured\s+by)\s*[.:]", re.I)
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
    r"|\bIRN\s*[:#]"
    # "Total of CARDIMAXX 0.00 0.00 211488.25 ..." (IPCA): a division's total
    # row, with the tax summary under it.
    r"|^\s*total\s+of\s"
    # Sumbiotic lists the sale returns it adjusts under its items, in the same
    # columns: "ADJUSTMENT DETAIL =====>", "SALE RETURN NO. : CN00007". Goods
    # coming back are not goods bought.
    r"|\badjustment\s*detail|\bsale\s*return\s*no"
    # "Gross Inv. Val. :87,535.62" (Blue Cross), then its credit notes listed.
    r"|\bgross\s*inv"
    # "CLASS TOTAL SCHEME DISCOUNT SGST CGST ..." (NSV): its GST-class table.
    r"|\bclass\s*total\b",
    re.I,
)
_NUMERIC = re.compile(r"^-?[\d,]+\.?\d*$")
# A currency mark printed on every figure ("₹138.00", Hindustan Capsule) does not
# stop it being a number.
_CURRENCY = re.compile(r"^(?:₹|rs\.?|inr)", re.I)

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
    # Up to four lines below: Bayer stacks "BATCH / Mfg. Date / EXPIRY" and
    # "P.T.R. / M.R.P. / P.T.S" three deep, under a line of tax heads.
    for j in range(start + 1, min(start + 5, len(lines))):
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


_NAME_HEADING = re.compile(r"description|particulars|details", re.I)


def _label(words: list) -> str:
    """A heading's words in reading order. Pieces of one word that touch are
    joined: Glowderma letter-spaces "E xp.Dt" and "R ate"."""
    out, prev = "", None
    for w in sorted(words, key=lambda w: (round(float(w["top"])), float(w["x0"]))):
        touching = (prev is not None and round(float(w["top"])) == round(float(prev["top"]))
                    and -0.5 <= float(w["x0"]) - float(prev["x1"]) < 0.8)
        out += ("" if touching or not out else " ") + str(w["text"])
        prev = w
    return out


def _cluster_columns(band: List[Line]) -> List[dict]:
    """Merge the header band's words into columns by horizontal overlap."""
    words = [w for _, row in band for w in row]
    if not words:
        return []
    # A name heading set ABOVE other columns is a title over them, not one of
    # them: Hindustan Capsule prints "DESCRIPTION" over "BATCH | EXPDT", beside
    # its own "PRODUCTS" column, and the two columns merged into one. Dropped
    # only when it spans two headings of the main line and that line already
    # names the product.
    main = max((row for _, row in band), key=len)
    if any(_NAME_HEADING.fullmatch(str(w["text"])) or str(w["text"]).lower().startswith("product")
           for w in main):
        def spans(w):
            return sum(1 for m in main if float(m["x0"]) < float(w["x1"]) and float(w["x0"]) < float(m["x1"]))
        words = [w for w in words
                 if any(w is m for m in main) or not (_NAME_HEADING.fullmatch(str(w["text"])) and spans(w) >= 2)]
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
        col["label"] = _label(col["words"])
    return columns


# Kinds of field a heading names, for telling two columns run together from
# one heading with parts (see _split_by_channels).
_FIELD_KINDS = {
    "date": r"\bmfg\b|mfg\.|\bmfd|\bexp|date|\bdt\b",
    "quantity": r"qty|quantity|\bfree\b",
    "price": r"\bmrp\b|\bptr\b|\bpts\b|\brate\b|price",
    "amount": r"amount|\bamt\b|value",
    "tax": r"g5?st|\btax\b",
    "name": r"descr|product|\bitem|particular|brand|\bname\b",
    "code": r"\bhsn|\bsac\b",
    "batch": r"batch|\blot\b",
    "pack": r"pack|\buom\b",
}


def _kinds(label: str) -> set:
    return {k for k, p in _FIELD_KINDS.items() if re.search(p, label, re.I)}


def _split_by_channels(columns: List[dict], body: List[Line]) -> List[dict]:
    """Split a heading cluster that is really two columns side by side.

    Glowderma prints "Mfg.Dt / Exp.Dt" and "Qty / Free Qty" so close together
    that their headings read as one column, "Mfg.Dt Qty . Free Qty Exp.Dt",
    and the quantity was lost in the dates' cell. The rows show the two
    columns: dates end at 271, quantities start at 289. A cluster is split
    where the rows leave an empty channel (a stray line may cross it) and its
    heading words do too; only when its top heading line has a heading on each
    side, and half its rows a value on each side.
    """
    out: List[dict] = []
    for col in columns:
        heads = col["words"]
        cells = [[w for w in row if col["x0"] - 2 <= (float(w["x0"]) + float(w["x1"])) / 2 <= col["x1"] + 2]
                 for _, row in body]
        cells = [c for c in cells if c]
        head_spans = [(float(w["x0"]), float(w["x1"])) for w in heads]
        # A stray line may cross the channel - "Page 1 of 1" at the foot.
        stray = max(1, len(cells) // 20)
        cut = None
        x = col["x0"] + _CHANNEL_STEP
        while x < col["x1"] and len(heads) > 1 and len(cells) >= 2:
            crossing = sum(1 for c in cells if any(float(w["x0"]) < x < float(w["x1"]) for w in c))
            if crossing <= stray and not any(a < x < b for a, b in head_spans):
                left = sum(1 for c in cells if any(float(w["x1"]) <= x for w in c))
                right = sum(1 for c in cells if any(float(w["x0"]) >= x for w in c))
                # The top heading line itself is two headings: one title over
                # both ("CGST" over "Rate | Amount") is a heading with
                # sub-columns, which the cell readers handle as it is.
                top = min(round(float(w["top"])) for w in heads)
                first = [w for w in heads if round(float(w["top"])) - top <= 2]
                if min(left, right) >= max(2, len(cells) // 2) \
                        and any(float(w["x1"]) <= x for w in first) and any(float(w["x0"]) >= x for w in first):
                    cut = x
                    break
            x += _CHANNEL_STEP
        if cut is not None:
            # Two different kinds of field side by side - dates beside
            # quantities - never two taxes, two prices or the halves of a word.
            left = _kinds(_label([w for w in heads if float(w["x1"]) <= cut]))
            right = _kinds(_label([w for w in heads if float(w["x0"]) >= cut]))
            # A tax head's own rate and amount are parts of it, not columns.
            if not left or not right or left & right or ("tax" in left | right
                                                        and {"amount", "price"} & (left | right)):
                cut = None
        if cut is None:
            out.append(col)
            continue
        for part in ([w for w in heads if float(w["x1"]) <= cut], [w for w in heads if float(w["x0"]) >= cut]):
            out.append({"x0": min(float(w["x0"]) for w in part), "x1": max(float(w["x1"]) for w in part),
                        "words": part, "label": _label(part)})
    return out


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

    rows_with, left_x0, left_x1, run_on = 0, [], [], 0
    for _, row in data:
        outside = [w for w in row if float(w["x1"]) <= first_x - margin]
        if outside:
            rows_with += 1
            left_x0.append(min(float(w["x0"]) for w in outside))
            left_x1.append(max(float(w["x1"]) for w in outside))
            # ...unless they run straight on into the first column's text, a
            # word space apart: Dr. Reddy's starts "CELEVIDA EN-Vanilla Powder"
            # left of its "Desc.of Goods" heading. That is one name.
            after = [float(w["x0"]) for w in row if float(w["x0"]) >= left_x1[-1]]
            if after and min(after) - left_x1[-1] <= 4.5:
                run_on += 1

    # Needs to be the rule, not one stray word: most rows must show it.
    if rows_with < 2 or rows_with * 2 <= len(data) or (run_on >= 2 and run_on * 3 >= rows_with):
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


# Resolution, in points, at which the empty channels between columns are found.
_CHANNEL_STEP = 0.5


_TEXT_HEADING = re.compile(r"batch|lot|exp|pack|pkg|hsn|desc|product|item|mfg|mfd|mfr|code|name|"
                           r"particular|goods|uom|unit|make|company", re.I)


def _figures_only(column: dict) -> bool:
    """A column whose heading names a figure (rate, amount, tax) and no text field."""
    return not _TEXT_HEADING.search(column.get("label") or "")


def _channel_edges(columns: List[dict], body: List[Line], edges: List[float]) -> List[float]:
    """Move a boundary that cuts through printed words into the empty channel
    the rows themselves leave between two columns.

    A boundary at the midpoint between two headings is right only when each
    column's values sit under its heading. They often do not: JB prints
    "Product Description" over x 36-89 and "Batch Number" from x 175, so the
    midpoint fell at 132 - while its product names run on to x 147. Every
    name's last words ("TABLET", "()", "1x10'S") were filed as the batch:
    "1x10'S XTAG25003". Down the whole table, though, no word is printed
    anywhere between x 148 and 172: that empty channel is where one column ends
    and the next begins, drawn line or not.

    Which channel: a word printed under a heading belongs to that heading. So
    the boundary must lie right of every word that overlaps the left heading
    and left of every word that overlaps the right one (Akumentis prints its
    batch "ANAT2503" from under "Batch" to x 241 and "May-25" under the next
    heading from x 245: the boundary goes in 241-245, not in the wider gap
    after May-25). Among the clear channels in that range, the one nearest the
    old boundary.

    Conservative by design: a boundary already clear of every word is kept, and
    so is one with no clear channel that respects both headings.
    """
    words = [w for _, row in body for w in row]
    if len(body) < 2 or not words:
        return edges
    spans = [(float(w["x0"]), float(w["x1"])) for w in words]

    def clear(x: float) -> bool:
        return not any(x0 < x < x1 for x0, x1 in spans)

    def under(col: dict, x0: float, x1: float) -> bool:
        return x1 > col["x0"] and x0 < col["x1"]

    out = list(edges)
    for i in range(1, len(edges) - 1):
        if clear(edges[i]):
            continue
        left, right = columns[i - 1], columns[i]
        if _figures_only(left) and _figures_only(right):
            # Between two columns of figures the cells are split by their own
            # rules downstream (merged "CGST AMOUNT %" headings, rate and
            # amount 1 pt apart); words running into the next column is a
            # text column's problem.
            continue
        # The range the boundary may take: past the left heading's own words,
        # short of the right heading's.
        lo = max([x1 for x0, x1 in spans if under(left, x0, x1) and not under(right, x0, x1)]
                 + [(left["x0"] + left["x1"]) / 2.0])
        hi = min([x0 for x0, x1 in spans if under(right, x0, x1) and not under(left, x0, x1)]
                 + [(right["x0"] + right["x1"]) / 2.0])
        if hi <= lo:
            continue
        runs: List[Tuple[float, float]] = []
        start: Optional[float] = None
        steps = int((hi - lo) / _CHANNEL_STEP)
        for k in range(steps + 1):
            x = lo + k * _CHANNEL_STEP
            if clear(x):
                start = x if start is None else start
            elif start is not None:
                runs.append((start, x - _CHANNEL_STEP))
                start = None
        if start is not None:
            runs.append((start, hi))
        if not runs:
            continue
        a, b = min(runs, key=lambda r: abs((r[0] + r[1]) / 2.0 - edges[i]))
        moved = (a + b) / 2.0
        # Boundaries stay in order: never past a neighbour already placed.
        if out[i - 1] < moved < out[i + 1]:
            out[i] = moved
    return out


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
    filled = sum(1 for i in numeric_columns
                 if i < len(cells) and _NUMERIC.match(_CURRENCY.sub("", cells[i].replace(" ", ""))))
    return filled >= 2


def _oblique(obj) -> bool:
    """A character drawn at a slant - neither level nor a right angle."""
    if obj.get("object_type") != "char":
        return False
    a, b = (obj.get("matrix") or (1, 0))[:2]
    return abs(a) > 0.05 and abs(b) > 0.05


def without_watermark(page):
    """The page without its diagonal watermark. Klingen stamps "KLINGEN LIFE
    SCIENCES PVT. LTD." across its grid at 35 degrees, and its letters landed
    in the cells - "0.0D0", "274.5 L 7", "21069099S" - losing a line and a
    batch. Nothing an invoice states is printed at a slant."""
    try:
        if not any(_oblique(c) for c in page.chars):
            return page
        return page.filter(lambda o: not _oblique(o))
    except Exception:  # noqa: BLE001 - never fatal; read the page as it is
        return page


# Overlapping pairs of letters, from two different strings, that make a page
# one drawn over itself (see without_overprint).
_OVERPRINT_PAIRS = 3


def _run_starts(chars: list) -> List[Tuple[float, int]]:
    """Each character's run, as (where it starts, its first letter's place in
    the drawing order): letters drawn one after another along one line, each
    starting where the last ended (spaces are drawn too)."""
    out: List[Tuple[float, int]] = []
    prev = None
    for i, c in enumerate(chars):
        size = float(c.get("size") or 0) or float(c["bottom"]) - float(c["top"])
        if (prev is None or abs(float(c["top"]) - float(prev["top"])) > 1.0
                or not -0.2 * size <= float(c["x0"]) - float(prev["x1"]) <= 0.6 * size):
            start = (float(c["x0"]), i)
        out.append(start)
        prev = c
    return out


def without_overprint(page):
    """The page without the text one cell runs on over the next cells.

    Some billing software prints a cell's whole text even where it is wider
    than the cell, straight over its neighbours': IPCA's maker "PURE & CURE
    HEALTHCARE PVT. LTD." runs from its Mfr column over the batch "AGV01AHA"
    and the dates; Sol Derma's "SOL DERMA PHARM" over the batch "AP5056". Read
    by position the letters interleave - "GCVU0R1EA". Of two letters drawn
    over each other by different runs, the run that started further left is
    the one running on; from where the other run starts its letters are
    dropped. Each cell keeps
    its own text, and the run-on keeps what it printed in its own cell. Bold
    faked by drawing a letter twice, or a block drawn twice a little apart,
    is not this: the two carry the same text.
    """
    try:
        chars = list(page.chars)
        starts = _run_starts(chars)
        order = {id(c): i for i, c in enumerate(chars)}
        texts: Dict[Tuple[float, int], str] = {}
        for c, start in zip(chars, starts):
            texts[start] = texts.get(start, "") + (c.get("text") or "")
        texts = {k: re.sub(r"\s+", "", v).upper() for k, v in texts.items()}
        lines: List[list] = []
        for c in sorted((c for c in chars if (c.get("text") or "").strip()), key=lambda c: float(c["top"])):
            if lines and float(c["top"]) - float(lines[-1][0]["top"]) <= 1.0:
                lines[-1].append(c)
            else:
                lines.append([c])
        edge: Dict[Tuple[float, int], float] = {}     # run -> where it first runs over other text
        pairs = 0
        for line in lines:
            line.sort(key=lambda c: float(c["x0"]))
            for a, b in zip(line, line[1:]):
                if a["text"] == b["text"]:
                    continue
                width = min(float(a["x1"]) - float(a["x0"]), float(b["x1"]) - float(b["x0"]))
                sa, sb = starts[order[id(a)]], starts[order[id(b)]]
                if width <= 0 or float(a["x1"]) - float(b["x0"]) <= 0.5 * width or abs(sa[0] - sb[0]) <= 1.0:
                    continue
                # The same text drawn twice a little apart (Overseas draws its
                # party block so) is one value, not one cell over another.
                if texts[sa] in texts[sb] or texts[sb] in texts[sa]:
                    continue
                pairs += 1
                run, other = (sa, sb) if sa[0] < sb[0] else (sb, sa)
                edge[run] = min(edge.get(run, float("inf")), other[0])
        if pairs < _OVERPRINT_PAIRS:
            return page
        # Cut where the other text starts, leaving a word's gap before it.
        drop = {id(c) for c, start in zip(chars, starts)
                if start in edge and float(c["x1"]) > edge[start] - 0.25 * float(c.get("size") or 8)}
        return page.filter(lambda o: id(o) not in drop)
    except Exception:  # noqa: BLE001 - never fatal; read the page as it is
        return page


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
        columns = _split_by_channels(columns, body)
    if len(columns) < 5:
        return []
    edges = _boundaries(columns)
    # The rows themselves say where each column ends (_channel_edges): only the
    # item rows vote, not the footer prose under them.
    rows_below = lines[end + 1:]
    for i, line in enumerate(rows_below):
        if _FOOTER.search(_line_text(line)):
            rows_below = rows_below[:i]
            break
    edges = _channel_edges(columns, rows_below, edges)
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
    # The name column. A heading that says so wins over one merely starting
    # "Product": Bayer heads its code column "PRODUCT / MFR" and its names
    # "DESCRIPTION / HSN", and the names' wrapped lines went to the codes.
    description_col = next(
        (i for i, col in enumerate(columns)
         if re.search(r"descr|particular|goods|(?:product|item)\s*name", col["label"], re.I)),
        next((i for i, col in enumerate(columns)
              if re.search(r"product|description|item|particular|goods", col["label"], re.I)), 0),
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
        # ...or prices, one per line: Bayer heads "P.T.R. / M.R.P. / P.T.S".
        or sum(bool(re.search(p, col["label"], re.I))
               for p in (r"p\.?\s*t\.?\s*r", r"m\.?\s*r\.?\s*p", r"p\.?\s*t\.?\s*s")) >= 2
    }
    # A pack printed over two lines, centred on its row: Troikaa sets "10X10"
    # just above the item's line and "CAP" just below it. Each piece goes to
    # the record it sits nearer, and the pieces are joined, not stacked.
    pack_cols = {i for i, col in enumerate(columns)
                 if re.search(r"pack|pkg", col["label"], re.I) and i != description_col}
    continuation_cols = {
        i for i, col in enumerate(columns)
        if i in stacked or i in pack_cols
        or re.search(r"cgst|sgst|igst|utgst|gst|tax|disc|rate|%", col["label"], re.I)
    }

    # Lines with no numbers of their own, waiting to be given to a record:
    # (top, description text, {stacked column: value}).
    orphans: List[Tuple[float, str, Dict[int, str]]] = []

    def give(record: List[str], otext: str, ovals: Dict[int, str], before: bool) -> None:
        if otext:
            record[description_col] = (f"{otext} {record[description_col]}" if before
                                       else f"{record[description_col]} {otext}").strip()
        for col, val in ovals.items():
            if col in pack_cols:
                # Only a pack the row's own line leaves empty, and only a short
                # unit after it: a neighbouring line's "TABLETS" or "INDIA" is
                # some other cell's spill, never part of "10'S".
                if before:
                    if not record[col]:
                        record[col] = val
                elif record[col] and len(val) <= 4 and val.isalpha():
                    # ...a word the cell broke, joined back: "10X10TA" over "B".
                    glue = "" if len(val) <= 2 and re.search(r"\d[A-Za-z]+$", record[col]) else " "
                    record[col] = f"{record[col]}{glue}{val}"
            elif not before and record[col] and record[col].count("\n") < (2 if col in stacked else 1):
                # A stacked heading names up to three values, one per line.
                record[col] = f"{record[col]}\n{val}"

    # The line's own amount: every product line prints one. Akumentis stacks
    # "Qty / Free Qty" and "MRP New / Old", so the line under a product holds
    # a free quantity and an old MRP - two figures, but no amount: the rest of
    # the line above, not a product of its own.
    amount_cols = [i for i, col in enumerate(columns)
                   if re.search(r"amount|value|\btotal\b|\bamt\b", col["label"], re.I)
                   and not re.search(r"gst|tax\b|disc|rate", col["label"], re.I)]

    def starts_record(cells: List[str]) -> bool:
        if not _is_record_start(cells, numeric_columns):
            return False
        products = [r for r in rows if r[description_col].strip() or any(r[i].strip() for i in amount_cols)]
        with_amount = [r for r in products if any(r[i].strip() for i in amount_cols)]
        if amount_cols and products and len(with_amount) >= 0.8 * len(products):
            return any(cells[i].strip() for i in amount_cols)
        return True

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
        if carried and not rows and not starts_record(cells):
            continue  # the continuation page's letterhead, above its first row
        if (starts_record(cells) and rows and not cells[description_col].strip()
                and all(not rows[-1][i].strip() for i, c in enumerate(cells) if c.strip())):
            # The rest of the row above, printed on the line beneath it: Alkem
            # prints a long item's prices and amount under its name and
            # quantities. No product name of its own, and every figure lands
            # in a column the row above left empty.
            for i, c in enumerate(cells):
                if c.strip():
                    rows[-1][i] = c
            continue
        if starts_record(cells):
            top = line[0]
            # Decide where the orphans between the last record and this one go.
            for otop, otext, ovals in orphans:
                # "Mfg : JAIN LIFECARE" (Cosmin) is a detail printed UNDER its
                # product, however close it sits to the next one - as a prefix
                # it became part of the next product's name.
                if rows and (abs(otop - row_tops[-1]) <= abs(otop - top) or _DETAIL_LINE.match(otext)):
                    give(rows[-1], otext, ovals, before=False)
                elif not rows and ovals and not carried:
                    # Figures above a page's first line are the end of the
                    # previous page's last line (Mankind's line 7): a row of
                    # their own, for the parser to join back - never a prefix.
                    lead = [""] * n
                    lead[description_col] = otext
                    for col, val in ovals.items():
                        lead[col] = val
                    rows.append(lead)
                    row_tops.append(otop)
                else:
                    give(cells, otext, {c: v for c, v in ovals.items() if c in pack_cols}, before=True)
            orphans = []
            rows.append(cells)
            row_tops.append(top)
        else:
            extra = cells[description_col].strip()
            values = {i: cells[i].strip() for i in continuation_cols if cells[i].strip()}
            if extra or values:
                orphans.append((line[0], extra, values))

    # Anything left over belongs to the last record - when it is printed with
    # it: MAX RELIEF's "Adj No : CN-00007 -3713.00", far below the last line,
    # is the bill's credit adjustment, not the end of a product's name.
    for otop, otext, ovals in orphans:
        if rows and otop - row_tops[-1] <= 40:
            give(rows[-1], otext, ovals, before=False)

    if not rows:
        return []
    return [[header] + rows]
