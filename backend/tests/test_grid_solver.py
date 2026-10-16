"""Reading line tables whose columns the headings alone do not settle."""
from app.services.ocr.pdf_table import _split_by_channels, tables_from_words


def _w(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 7, "upright": True}


def _col(*words):
    return {"x0": min(w["x0"] for w in words), "x1": max(w["x1"] for w in words), "words": list(words),
            "label": " ".join(w["text"] for w in words)}


def test_two_columns_under_one_run_of_headings_are_split():
    """Glowderma: "Mfg.Dt / Exp.Dt" and "Qty / Free Qty" so close their
    headings read as one column; the rows show the empty channel between."""
    col = _col(_w("Mfg.Dt", 250, 270, 100), _w("Qty", 286, 298, 100),
               _w("Exp.Dt", 250, 270, 108), _w("Free", 272, 285, 108))
    body = [(120 + 9 * n, [_w(d, 250, 270, 120 + 9 * n), _w(q, 289, 296, 120 + 9 * n)])
            for n, (d, q) in enumerate((("Jun-25", "20"), ("May-27", "0"), ("Jul-25", "10"), ("Jun-27", "0")))]
    labels = [c["label"] for c in _split_by_channels([col], body)]
    assert labels == ["Mfg.Dt Exp.Dt", "Qty Free"]


def test_a_tax_heading_over_its_rate_and_amount_is_not_split():
    col = _col(_w("CGST", 480, 500, 100), _w("Rate", 466, 480, 108), _w("Amount", 488, 512, 108))
    body = [(120 + 9 * n, [_w("6.00", 466, 478, 120 + 9 * n), _w("190.55", 493, 513, 120 + 9 * n)])
            for n in range(4)]
    assert len(_split_by_channels([col], body)) == 1


def test_halves_of_one_heading_are_not_split():
    col = _col(_w("HSN", 145, 160, 100), _w("Code", 170, 188, 100))
    body = [(120 + 9 * n, [_w("3004", 145, 160, 120 + 9 * n), _w("9099", 172, 188, 120 + 9 * n)])
            for n in range(4)]
    assert len(_split_by_channels([col], body)) == 1


def _bayer_like():
    """A heading three lines deep: "BATCH / Mfg. Date / EXPIRY" and
    "P.T.R. / M.R.P. / P.T.S", each value on its own line, and a code column
    headed "PRODUCT / MFR" beside the names' "DESCRIPTION / HSN"."""
    words = [_w("PRODUCT", 26, 60, 100), _w("DESCRIPTION", 72, 120, 100), _w("BATCH", 190, 215, 100),
             _w("QTY", 235, 250, 100), _w("RATE", 295, 315, 100), _w("AMOUNT", 346, 375, 100),
             _w("TAXBASE", 479, 512, 100), _w("P.T.R.", 777, 800, 100),
             _w("/M.R.P.", 777, 805, 105),
             _w("/", 26, 28, 109), _w("MFR", 29, 45, 109), _w("/", 72, 74, 109), _w("HSN", 76, 92, 109),
             _w("/", 190, 192, 109), _w("Mfg.", 194, 208, 109), _w("Date", 210, 226, 109),
             _w("P.T.S", 777, 794, 113), _w("/", 190, 192, 118), _w("EXPIRY", 194, 219, 118)]
    for n, (code, name, batch, mfg, exp, ptr, mrp, pts) in enumerate((
            ("885745", "KERENDIA 10MG TABL BLI", "BXK6GP2", "03.11.2024", "10-2027", "1,067.86", "1,495.00", "961.07"),
            ("268523", "GLUCO-BAY 25 MG TABL", "PB25201", "01.01.2025", "12-2027", "72.86", "102.00", "65.57"))):
        top = 140 + 40 * n
        x = 72
        line = [_w(code, 26, 56, top), _w(batch, 190, 222, top), _w("546", 235, 250, top),
                _w("961.07", 295, 320, top), _w("5,247.22", 346, 380, top), _w("5,247.22", 479, 512, top),
                _w(ptr, 774, 800, top)]
        for part in name.split():
            line.append(_w(part, x, x + 5 * len(part), top))
            x += 5 * len(part) + 3
        words += line
        words += [_w("/6486125", 26, 56, top + 9), _w("30049099", 72, 105, top + 9), _w("/" + mfg, 190, 230, top + 9),
                  _w("/" + mrp, 774, 805, top + 9)]
        words += [_w("/" + exp, 190, 224, top + 17), _w("/" + pts, 774, 800, top + 17)]
    return words


def test_a_three_deep_heading_and_its_stacked_values():
    table = tables_from_words(_bayer_like())[0]
    header, first = table[0], table[1]
    assert any("EXPIRY" in h for h in header) and any("P.T.S" in h for h in header)
    batch = header.index(next(h for h in header if h.startswith("BATCH")))
    prices = header.index(next(h for h in header if h.startswith("P.T.R.")))
    assert first[batch].split("\n") == ["BXK6GP2", "/03.11.2024", "/10-2027"]
    assert first[prices].split("\n") == ["1,067.86", "/1,495.00", "/961.07"]
    # The names' wrapped line went to the names, not to the code column.
    assert "30049099" in first[header.index(next(h for h in header if h.startswith("DESCRIPTION")))]
    assert "30049099" not in first[0]


def test_stacked_prices_and_dates_reach_their_fields():
    from app.services.ocr.invoice_parser import _rows_from_tables

    items = _rows_from_tables(tables_from_words(_bayer_like()), {}, None, None)
    first = {k: v.get("value") for k, v in items[0].items() if isinstance(v, dict)}
    assert (first["batch_no"], first["mfg_date"], first["expiry"]) == ("BXK6GP2", "03.11.2024", "10-2027")
    assert (first["ptr"], first["mrp"], first["pts"]) == ("1,067.86", "1,495.00", "961.07")
    assert first["hsn"] == "30049099" and first["description"] == "KERENDIA 10MG TABL BLI"


# --- the two readings of one page -------------------------------------------------------

def _f(value):
    return {"value": value, "confidence": 0.9}


def test_a_blank_is_filled_from_the_other_reading_of_the_same_lines():
    from app.services.ocr.invoice_parser import _fill_from_other_reading

    kept = [{"amount": _f("3175.80"), "batch_no": _f("2500101A"), "mfg_date": _f("Jun-25")}]
    other = [{"amount": _f("3175.80"), "batch_no": _f("2500101A"), "mfg_date": _f("Jun-25"),
              "expiry": _f("May-27")}]
    _fill_from_other_reading(kept, other)
    assert kept[0]["expiry"]["value"] == "May-27"


def test_readings_of_different_lines_are_never_mixed():
    from app.services.ocr.invoice_parser import _fill_from_other_reading

    kept = [{"amount": _f("3175.80"), "batch_no": _f("2500101A")}]
    for other in ([{"amount": _f("573.40"), "expiry": _f("May-27")}],
                  [{"amount": _f("3175.80"), "batch_no": _f("2500142A"), "expiry": _f("May-27")}],
                  []):
        _fill_from_other_reading(kept, other)
        assert "expiry" not in kept[0]


# --- a batch cell holding the end of a name ---------------------------------------------

def _item(batch, desc="GLUCOBAY M 50 TABL"):
    from app.services.ocr.invoice_parser import _build_item

    header = ["Description", "Batch", "Qty", "Rate", "Amount"]
    cols = {"description": 0, "batch_no": 1, "quantity": 2, "rate": 3, "amount": 4}
    return _build_item([desc, batch, "10", "12.50", "125.00"], cols, header, [], None, None)


def test_the_end_of_a_long_name_goes_back_to_the_name():
    item = _item("1X10'S IN PB25413")
    assert item["batch_no"]["value"] == "PB25413"
    assert item["description"]["value"] == "GLUCOBAY M 50 TABL 1X10'S IN"


def test_a_batch_with_a_space_of_its_own_is_kept():
    for batch in ("TPG - 928", "T2507 23", "AB 12"):
        assert _item(batch)["batch_no"]["value"] == batch


# --- which lines are products -------------------------------------------------------------

def _simple(rows, extra=()):
    words = [_w("Description", 40, 90, 100), _w("Batch", 175, 200, 100), _w("Qty", 240, 255, 100),
             _w("MRP", 280, 300, 100), _w("Amount", 320, 350, 100)]
    for top, cells in rows:
        for text, x0, x1 in cells:
            words.append(_w(text, x0, x1, top))
    return tables_from_words(words + list(extra))[0]


def test_a_stacked_line_with_no_amount_stays_with_its_product():
    """Akumentis: under each product, its free quantity and old MRP - two
    figures, but no amount of its own."""
    table = _simple([
        (120, [("CREAM", 40, 70), ("AB123", 176, 205), ("10", 245, 252), ("120.00", 280, 300), ("1000.00", 322, 350)]),
        (129, [("MFG", 40, 55), ("Name", 57, 75), ("2", 247, 252), ("130.00", 280, 300)]),
        (145, [("LOTION", 40, 72), ("CD456", 176, 205), ("5", 247, 252), ("90.00", 282, 300), ("400.00", 324, 350)]),
    ])
    assert len(table) == 3   # header + two products


def test_a_remark_far_below_the_last_product_is_not_part_of_it():
    table = _simple([
        (120, [("CREAM", 40, 70), ("AB123", 176, 205), ("10", 245, 252), ("120.00", 280, 300), ("1000.00", 322, 350)]),
    ], extra=[_w("Adj", 40, 55, 330), _w("No", 57, 66, 330), _w("CN-00007", 70, 110, 330)])
    assert table[1][0] == "CREAM"
